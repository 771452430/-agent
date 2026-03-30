"""LangGraph 会话图。

如果你想理解“为什么这里要用 LangGraph，而不是普通函数串起来”，
这个文件就是答案：
- inspect_request 负责路由；
- retrieve_context 负责 RAG 分支；
- execute_tools 负责 Skill / Tool 调用；
- finalize_response 负责收口成结构化输出。
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
from ..schemas import ChatMessage, Citation, FinalResponse, ModelConfig, ScopeType, ToolEvent
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
    forced_route: Literal["chat", "tool", "rag"] | None
    route: Literal["chat", "tool", "rag"]
    scope_type: ScopeType
    scope_id: str | None
    system_prompt: str | None
    tool_outputs: dict[str, Any]
    tool_events: list[ToolEvent]
    citations: list[Citation]
    retrieval_context: str
    final_output: FinalResponse
    stream_events: list[dict[str, Any]]


class LearningChatGraph:
    """封装 graph 结构与每个节点的教学型实现。"""

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
            lambda state: state.get("route", "chat"),
            {"rag": "retrieve_context", "tool": "execute_tools", "chat": "finalize_response"},
        )
        builder.add_edge("retrieve_context", "finalize_response")
        builder.add_edge("execute_tools", "finalize_response")
        builder.add_edge("finalize_response", END)
        return builder.compile()

    def inspect_request(self, state: ChatGraphState) -> dict[str, Any]:
        """决定当前请求应该走 chat / tool / rag 哪条分支。"""
        forced_route = state.get("forced_route")
        if forced_route:
            return {"route": forced_route, "stream_events": [{"event": "route", "data": {"route": forced_route}}]}

        text = state["user_input"].lower()
        route = "chat"
        if self.rag_pipeline.knowledge_store.has_documents(
            scope_type=state.get("scope_type", "global"),
            scope_id=state.get("scope_id"),
        ) and any(token in text for token in ("知识库", "文档", "附件", "资料", "根据", "参考")):
            route = "rag"
        elif any(token in text for token in ("报销", "费用", "税", "金额", "总结", "提炼")):
            route = "tool"
        return {"route": route, "stream_events": [{"event": "route", "data": {"route": route}}]}

    def retrieve_context(self, state: ChatGraphState) -> dict[str, Any]:
        """RAG 节点：把 scope-aware 检索结果写回 graph state。"""
        result = self.rag_pipeline.run(
            query=state["user_input"],
            scope_type=state.get("scope_type", "global"),
            scope_id=state.get("scope_id"),
        )
        return {
            "citations": result.citations,
            "retrieval_context": result.retrieval_context,
            "stream_events": [
                {
                    "event": "retrieval",
                    "data": {
                        "query": result.query,
                        "scope_type": result.scope_type,
                        "scope_id": result.scope_id,
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
        """工具节点：根据关键词调用已启用 Skill 对应的 Tool。"""
        enabled_skills = state.get("enabled_skills", [])
        text = state["user_input"]
        lowered = text.lower()
        tool_outputs: dict[str, Any] = {}
        tool_events: list[ToolEvent] = []
        stream_events: list[dict[str, Any]] = []

        def run_tool(tool_name: str, payload: dict[str, Any]) -> Any:
            # 这里把 Tool 调用包装成统一的 started/completed/failed 事件，
            # 前端就能把工具轨迹可视化，而不只是看到最后答案。
            tool_def = self.skill_registry.get_tool(tool_name)
            if tool_def is None or not self.skill_registry.is_tool_enabled(tool_name, enabled_skills):
                return None
            started = ToolEvent(
                id=str(uuid4()),
                tool_name=tool_name,
                status="started",
                input=payload,
                output={},
                started_at=_utc_now(),
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

        return {"tool_outputs": tool_outputs, "tool_events": tool_events, "stream_events": stream_events}

    def finalize_response(self, state: ChatGraphState) -> dict[str, Any]:
        """最终收口节点：把工具结果、引用和上下文交给 LLMService。"""
        final_output = self.llm_service.generate_response(
            query=state["user_input"],
            messages=state.get("history_messages", []),
            tool_outputs=state.get("tool_outputs", {}),
            citations=state.get("citations", []),
            retrieval_context=state.get("retrieval_context", ""),
            model_config=state["model_config"],
            system_prompt=state.get("system_prompt"),
        )
        return {"final_output": final_output, "stream_events": [{"event": "final", "data": final_output.model_dump(mode="json")}]} 
