"""LangGraph 会话图。

这个图只有几个节点，但每个节点都对应一个明确的学习概念：
- inspect_request: 为什么 graph 需要先路由；
- retrieve_context: RAG 链路如何接进 graph；
- execute_tools: 工具/Skill 的调用如何回写状态；
- finalize_response: Prompt + Structured Output 如何整合成最终答案。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ..rag.pipeline import RAGPipeline
from ..registry import SkillRegistry
from ..schemas import ChatMessage, Citation, FinalResponse, ModelConfig, ToolEvent
from ..services.llm_service import LLMService


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChatGraphState(TypedDict, total=False):
    messages: Annotated[list[Any], add_messages]
    history_messages: list[ChatMessage]
    thread_id: str
    user_input: str
    model_config: ModelConfig
    enabled_skills: list[str]
    route: Literal["chat", "tool", "rag"]
    tool_outputs: dict[str, Any]
    tool_events: list[ToolEvent]
    citations: list[Citation]
    retrieval_context: str
    final_output: FinalResponse
    stream_events: list[dict[str, Any]]


class LearningChatGraph:
    """封装 graph 构建与节点逻辑。"""

    def __init__(self, skill_registry: SkillRegistry, rag_pipeline: RAGPipeline, llm_service: LLMService) -> None:
        self.skill_registry = skill_registry
        self.rag_pipeline = rag_pipeline
        self.llm_service = llm_service
        self.graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(ChatGraphState)
        builder.add_node("inspect_request", self.inspect_request)
        builder.add_node("retrieve_context", self.retrieve_context)
        builder.add_node("execute_tools", self.execute_tools)
        builder.add_node("finalize_response", self.finalize_response)
        builder.add_edge(START, "inspect_request")
        builder.add_conditional_edges(
            "inspect_request",
            self._route_after_inspect,
            {
                "rag": "retrieve_context",
                "tool": "execute_tools",
                "chat": "finalize_response",
            },
        )
        builder.add_edge("retrieve_context", "finalize_response")
        builder.add_edge("execute_tools", "finalize_response")
        builder.add_edge("finalize_response", END)
        return builder.compile()

    def _route_after_inspect(self, state: ChatGraphState) -> str:
        return state.get("route", "chat")

    def inspect_request(self, state: ChatGraphState) -> dict[str, Any]:
        text = state["user_input"].lower()
        route = "chat"
        if self.rag_pipeline.knowledge_store.has_documents() and any(
            token in text for token in ("知识库", "文档", "附件", "资料", "根据", "参考")
        ):
            route = "rag"
        elif any(token in text for token in ("报销", "费用", "税", "金额", "总结", "提炼")):
            route = "tool"
        return {
            "route": route,
            "stream_events": [
                {
                    "event": "route",
                    "data": {
                        "route": route,
                        "reason": "根据关键词和知识库可用性做轻量路由，便于学习 graph 中的条件分支。",
                    },
                }
            ],
        }

    def retrieve_context(self, state: ChatGraphState) -> dict[str, Any]:
        result = self.rag_pipeline.run(state["user_input"])
        return {
            "citations": result.citations,
            "retrieval_context": result.retrieval_context,
            "stream_events": [
                {
                    "event": "retrieval",
                    "data": {
                        "query": result.query,
                        "citations": [item.model_dump() for item in result.citations],
                        "context_preview": result.retrieval_context[:240],
                    },
                }
            ],
        }

    def _extract_finance_inputs(self, user_input: str) -> tuple[int, float]:
        day_match = re.search(r"(\d+)\s*天", user_input)
        number_matches = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", user_input)]
        days = int(day_match.group(1)) if day_match else 1
        daily = 100.0
        if len(number_matches) >= 2:
            daily = float(number_matches[1])
        elif len(number_matches) == 1 and day_match is None:
            daily = float(number_matches[0])
        return days, daily

    def execute_tools(self, state: ChatGraphState) -> dict[str, Any]:
        enabled_skills = state.get("enabled_skills", [])
        text = state["user_input"]
        lowered = text.lower()
        tool_outputs: dict[str, Any] = {}
        tool_events: list[ToolEvent] = []
        stream_events: list[dict[str, Any]] = []

        def run_tool(tool_name: str, payload: dict[str, Any]) -> Any:
            tool_def = self.skill_registry.get_tool(tool_name)
            if tool_def is None or not self.skill_registry.is_tool_enabled(tool_name, enabled_skills):
                return None
            started_at = _utc_now()
            started = ToolEvent(
                id=str(uuid4()),
                tool_name=tool_name,
                status="started",
                input=payload,
                output={},
                started_at=started_at,
            )
            stream_events.append({"event": "tool_start", "data": started.model_dump(mode="json")})
            try:
                raw_output = tool_def.tool.invoke(payload)
                completed = ToolEvent(
                    id=started.id,
                    tool_name=tool_name,
                    status="completed",
                    input=payload,
                    output={"result": raw_output},
                    started_at=started.started_at,
                    ended_at=_utc_now(),
                )
                tool_events.append(completed)
                stream_events.append({"event": "tool_end", "data": completed.model_dump(mode="json")})
                return raw_output
            except Exception as exc:
                failed = ToolEvent(
                    id=started.id,
                    tool_name=tool_name,
                    status="failed",
                    input=payload,
                    output={},
                    started_at=started.started_at,
                    ended_at=_utc_now(),
                    note=str(exc),
                )
                tool_events.append(failed)
                stream_events.append({"event": "tool_end", "data": failed.model_dump(mode="json")})
                return None

        if any(token in lowered for token in ("报销", "费用", "税", "金额")):
            days, daily = self._extract_finance_inputs(text)
            money = run_tool("calc_money", {"days": days, "daily": daily})
            if money is not None:
                tool_outputs["calc_money"] = money
            if "税" in lowered and money is not None:
                tax = run_tool("calc_tax", {"calc_money": money})
                if tax is not None:
                    tool_outputs["calc_tax"] = tax
                    breakdown = run_tool("format_breakdown", {"calc_money": money, "calc_tax": tax})
                    if breakdown is not None:
                        tool_outputs["format_breakdown"] = breakdown

        if any(token in lowered for token in ("总结", "提炼", "概括")):
            summary = run_tool("summarize_notes", {"text": text})
            if summary is not None:
                tool_outputs["summarize_notes"] = summary

        return {
            "tool_outputs": tool_outputs,
            "tool_events": tool_events,
            "stream_events": stream_events,
        }

    def finalize_response(self, state: ChatGraphState) -> dict[str, Any]:
        final_output = self.llm_service.generate_response(
            query=state["user_input"],
            messages=state.get("history_messages", []),
            tool_outputs=state.get("tool_outputs", {}),
            citations=state.get("citations", []),
            retrieval_context=state.get("retrieval_context", ""),
            model_config=state["model_config"],
        )
        return {
            "final_output": final_output,
            "stream_events": [
                {
                    "event": "final",
                    "data": final_output.model_dump(mode="json"),
                }
            ],
        }
