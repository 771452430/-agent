"""聊天编排服务。

这个服务是业务层入口：它把 SQLite 中的历史消息、LangGraph 图、RAG 管道、
Skill 注册中心和流式 SSE 输出串起来。这样 API 层就能保持简洁，
而你读代码时也更容易看清“请求进入后发生了什么”。
"""

from __future__ import annotations

import json
from collections.abc import Generator
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage

from ..graphs.chat_graph import LearningChatGraph
from ..rag.pipeline import RAGPipeline
from ..registry import SkillRegistry
from ..schemas import (
    CatalogResponse,
    CreateThreadRequest,
    CreateThreadResponse,
    FinalResponse,
    KnowledgeDocument,
    KnowledgeSearchResponse,
    ModelConfig,
    SendMessageRequest,
    ThreadState,
    ThreadSummary,
)
from ..services.knowledge_store import KnowledgeStore
from ..services.llm_service import LLMService
from ..services.thread_store import ThreadStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChatService:
    """面向 API 的高层服务。"""

    def __init__(
        self,
        thread_store: ThreadStore,
        knowledge_store: KnowledgeStore,
        skill_registry: SkillRegistry,
        llm_service: LLMService,
    ) -> None:
        self.thread_store = thread_store
        self.knowledge_store = knowledge_store
        self.skill_registry = skill_registry
        self.llm_service = llm_service
        self.rag_pipeline = RAGPipeline(knowledge_store)
        self.chat_graph = LearningChatGraph(skill_registry, self.rag_pipeline, llm_service)

    def _default_enabled_skills(self, enabled_skills: list[str] | None) -> list[str]:
        return enabled_skills or self.skill_registry.list_default_skill_ids()

    def create_thread(self, request: CreateThreadRequest) -> CreateThreadResponse:
        model_config = self.llm_service.resolve_model_config(request.model_settings)
        enabled_skills = self._default_enabled_skills(request.enabled_skills)
        title = request.title or "新的学习会话"
        thread_id = self.thread_store.create_thread(title, model_config, enabled_skills)
        return CreateThreadResponse(thread_id=thread_id, title=title)

    def list_threads(self) -> list[ThreadSummary]:
        return self.thread_store.list_threads()

    def get_thread(self, thread_id: str) -> ThreadState:
        state = self.thread_store.get_thread_state(thread_id)
        if state is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return state

    def list_documents(self) -> list[KnowledgeDocument]:
        return self.knowledge_store.list_documents()

    def ingest_document(self, filename: str, file_bytes: bytes) -> KnowledgeDocument:
        return self.knowledge_store.ingest_document(filename=filename, file_bytes=file_bytes)

    def search_knowledge(self, query: str) -> KnowledgeSearchResponse:
        return self.rag_pipeline.run(query=query)

    def get_catalog(self) -> CatalogResponse:
        return CatalogResponse(
            models=[
                ModelConfig(provider="mock", model="learning-mode", temperature=0.2, max_tokens=1024),
                ModelConfig(provider="openai", model="gpt-4o-mini", temperature=0.2, max_tokens=1024),
                ModelConfig(provider="ollama", model="llama3.1", temperature=0.2, max_tokens=1024),
            ],
            skills=self.skill_registry.list_skills(),
            tools=self.skill_registry.tool_catalog(),
            learning_focus=[
                {"name": "Prompt / Messages", "description": "显式使用 system / human / ai message。"},
                {"name": "Structured Output", "description": "最终输出统一走 FinalResponse schema。"},
                {"name": "Runnable / LCEL", "description": "RAG 流程拆成可组合 runnable 管道。"},
                {"name": "LangGraph", "description": "用图管理路由、工具、检索和最终输出。"},
                {"name": "RAG + Citation", "description": "知识问答始终返回引用片段。"},
            ],
        )

    def _serialize_event(self, event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def _to_graph_messages(self, messages):
        graph_messages: list[Any] = []
        for item in messages:
            if item.role == "assistant":
                graph_messages.append(AIMessage(content=item.content))
            elif item.role == "human":
                graph_messages.append(HumanMessage(content=item.content))
        return graph_messages

    def stream_message(self, thread_id: str, request: SendMessageRequest) -> Generator[str, None, None]:
        thread = self.thread_store.get_thread_state(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        model_config = self.llm_service.resolve_model_config(request.model_settings or thread.model_settings)
        enabled_skills = self._default_enabled_skills(request.enabled_skills or thread.enabled_skills)
        self.thread_store.update_thread_config(thread_id, model_config, enabled_skills)
        user_message = self.thread_store.append_message(thread_id, "human", request.content)
        history = self.thread_store.get_thread_state(thread_id)
        assert history is not None

        aggregate: dict[str, Any] = {
            "tool_outputs": {},
            "tool_events": [],
            "citations": [],
            "retrieval_context": "",
            "final_output": None,
        }

        initial_state = {
            "thread_id": thread_id,
            "user_input": request.content,
            "model_config": model_config,
            "enabled_skills": enabled_skills,
            "history_messages": history.messages,
            "messages": self._to_graph_messages(history.messages),
        }

        yield self._serialize_event(
            "message",
            {"role": "human", "content": user_message.content, "created_at": user_message.created_at.isoformat()},
        )

        for update in self.chat_graph.graph.stream(initial_state, stream_mode="updates"):
            for _, payload in update.items():
                if "tool_outputs" in payload:
                    aggregate["tool_outputs"].update(payload["tool_outputs"])
                if "tool_events" in payload:
                    aggregate["tool_events"] = payload["tool_events"]
                if "citations" in payload:
                    aggregate["citations"] = payload["citations"]
                if "retrieval_context" in payload:
                    aggregate["retrieval_context"] = payload["retrieval_context"]
                if "final_output" in payload:
                    aggregate["final_output"] = payload["final_output"]
                for event in payload.get("stream_events", []):
                    yield self._serialize_event(event["event"], event["data"])

        final_output = aggregate["final_output"]
        if not isinstance(final_output, FinalResponse):
            final_output = FinalResponse(
                answer="当前运行未生成最终输出，请检查前面的工具或检索事件。",
                citations=[],
                used_tools=[],
                next_actions=["查看浏览器中的事件流与后端日志，确认哪一步没有返回。"],
            )

        assistant_message = self.thread_store.append_message(thread_id, "assistant", final_output.answer)
        self.thread_store.replace_tool_events(thread_id, aggregate["tool_events"])
        self.thread_store.set_final_output(thread_id, final_output)

        yield self._serialize_event(
            "message",
            {"role": "assistant", "content": assistant_message.content, "created_at": assistant_message.created_at.isoformat()},
        )
        yield self._serialize_event("done", {"thread_id": thread_id, "completed_at": _utc_now().isoformat()})
