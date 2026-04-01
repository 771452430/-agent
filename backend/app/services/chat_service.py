"""聊天、检索模式、配置型 Agent 的统一业务入口。

这层是最适合“顺着请求读代码”的地方：
1. API 把请求交进来；
2. ChatService 组织线程状态、知识树、LangGraph、LLM；
3. 根据入口类型走 chat / retrieval / agent 三条路径；
4. 把结构化结果再返回给 API 或 SSE。
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
    AgentConfig,
    AgentRunResponse,
    CatalogResponse,
    CreateAgentRequest,
    CreateThreadRequest,
    CreateThreadResponse,
    DirectoryUploadRequest,
    FinalResponse,
    GitLabTreeImportRequest,
    GitLabTreeImportResponse,
    KnowledgeDocument,
    KnowledgeDeleteResponse,
    KnowledgeNodeCreateRequest,
    KnowledgeTreeNode,
    KnowledgeTreeNodeDetail,
    KnowledgeTreeResponse,
    ModelConfig,
    ProviderConfig,
    ProviderTestResponse,
    RetrievalQueryRequest,
    RetrievalResult,
    RunAgentRequest,
    ScopeType,
    SendMessageRequest,
    ThreadState,
    ThreadSummary,
    UpdateKnowledgeDocumentRequest,
    UpdateProviderRequest,
    UpdateAgentRequest,
)
from ..services.agent_store import AgentStore
from ..services.gitlab_import_service import GitLabImportError, GitLabImportService
from ..services.knowledge_store import KnowledgeStore, ROOT_NODE_ID
from ..services.llm_service import LLMService
from ..services.provider_store import ProviderStore
from ..services.retrieval_service import RetrievalService
from ..services.thread_store import ThreadStore


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ChatService:
    """把多个底层能力组装成对外可用的业务服务。"""

    def __init__(
        self,
        thread_store: ThreadStore,
        knowledge_store: KnowledgeStore,
        skill_registry: SkillRegistry,
        llm_service: LLMService,
        agent_store: AgentStore,
        provider_store: ProviderStore,
        gitlab_import_service: GitLabImportService,
    ) -> None:
        self.thread_store = thread_store
        self.knowledge_store = knowledge_store
        self.skill_registry = skill_registry
        self.llm_service = llm_service
        self.agent_store = agent_store
        self.provider_store = provider_store
        self.gitlab_import_service = gitlab_import_service
        self.rag_pipeline = RAGPipeline(knowledge_store)
        self.retrieval_service = RetrievalService(knowledge_store, llm_service)
        self.chat_graph = LearningChatGraph(skill_registry, self.rag_pipeline, llm_service)

    def _default_enabled_skills(self, enabled_skills: list[str] | None) -> list[str]:
        return enabled_skills or self.skill_registry.list_default_skill_ids()

    def _serialize_event(self, event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    def _to_graph_messages(self, messages: list[Any]) -> list[Any]:
        graph_messages: list[Any] = []
        for item in messages:
            if item.role == "assistant":
                graph_messages.append(AIMessage(content=item.content))
            elif item.role == "human":
                graph_messages.append(HumanMessage(content=item.content))
        return graph_messages

    def _require_runnable_model_config(self, model_config: ModelConfig | None) -> ModelConfig:
        """把 LLM 配置校验错误转换成面向前端的 400。"""

        try:
            resolved, _provider = self.llm_service.ensure_model_config_runnable(model_config)
            return resolved
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _normalize_visible_model_config(self, model_config: ModelConfig | None) -> ModelConfig:
        return self.llm_service.normalize_model_config_reference(model_config)

    def _normalize_thread_state(self, state: ThreadState) -> ThreadState:
        normalized_config = self._normalize_visible_model_config(state.model_settings)
        if normalized_config == state.model_settings:
            return state
        return state.model_copy(update={"model_settings": normalized_config})

    def _normalize_agent_config(self, agent: AgentConfig) -> AgentConfig:
        normalized_config = self._normalize_visible_model_config(agent.model_settings)
        if normalized_config == agent.model_settings:
            return agent
        return agent.model_copy(update={"model_settings": normalized_config})

    def _run_graph(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """执行 LangGraph，并把多个节点 update 合并成一次完整结果。

        LangGraph 的 updates 是增量的：有的节点只补 tool_outputs，
        有的节点只补 citations 或 stream_events。这里统一折叠，
        这样 chat / retrieval / agent 三个入口都能复用同一套执行结果。
        """
        aggregate: dict[str, Any] = {
            "tool_outputs": {},
            "tool_events": [],
            "citations": [],
            "retrieval_context": "",
            "final_output": None,
            "stream_events": [],
        }
        for update in self.chat_graph.graph.stream(initial_state, stream_mode="updates"):
            for payload in update.values():
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
                if payload.get("stream_events"):
                    aggregate["stream_events"].extend(payload["stream_events"])
        return aggregate

    def create_thread(self, request: CreateThreadRequest) -> CreateThreadResponse:
        model_config = self._require_runnable_model_config(request.model_settings)
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
        return self._normalize_thread_state(state)

    def list_documents(self, node_id: str | None = None) -> list[KnowledgeDocument]:
        return self.knowledge_store.list_documents(node_id=node_id)

    def ingest_document(
        self,
        filename: str,
        file_bytes: bytes,
        *,
        node_id: str | None = None,
        relative_path: str | None = None,
    ) -> KnowledgeDocument:
        return self.knowledge_store.ingest_document(
            filename=filename,
            file_bytes=file_bytes,
            node_id=node_id,
            relative_path=relative_path,
        )

    def get_knowledge_tree(self) -> KnowledgeTreeResponse:
        return self.knowledge_store.get_tree()

    def create_knowledge_node(self, request: KnowledgeNodeCreateRequest) -> KnowledgeTreeNode:
        try:
            return self.knowledge_store.create_node(request.name, request.parent_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def upload_directory(self, request: DirectoryUploadRequest) -> list[KnowledgeDocument]:
        try:
            return self.knowledge_store.ingest_directory(request.parent_node_id, request.files)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def import_gitlab_tree(self, request: GitLabTreeImportRequest) -> GitLabTreeImportResponse:
        try:
            return self.gitlab_import_service.import_tree(
                tree_url=request.tree_url,
                parent_node_id=request.parent_node_id,
            )
        except GitLabImportError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    def get_knowledge_node_detail(self, node_id: str) -> KnowledgeTreeNodeDetail:
        try:
            return self.knowledge_store.get_node_detail(node_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def delete_knowledge_document(self, document_id: str) -> KnowledgeDeleteResponse:
        try:
            return self.knowledge_store.delete_document(document_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def update_knowledge_document(
        self,
        document_id: str,
        request: UpdateKnowledgeDocumentRequest,
    ) -> KnowledgeDocument:
        try:
            return self.knowledge_store.update_document_metadata(
                document_id,
                external_url=request.external_url,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def delete_knowledge_node(self, node_id: str) -> KnowledgeDeleteResponse:
        try:
            return self.knowledge_store.delete_node(node_id)
        except ValueError as exc:
            status_code = 400 if str(exc) == "根节点不支持删除" else 404
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    def search_knowledge(self, query: str) -> RetrievalResult:
        return self.query_retrieval(RetrievalQueryRequest(query=query, scope_type="global"))

    def query_retrieval(self, request: RetrievalQueryRequest) -> RetrievalResult:
        """检索模式的单次 scoped RAG 请求。

        这条链路和 Chat 最大的不同是：
        - 不走多轮线程；
        - 不做工具分支；
        - 明确由 scope_type / scope_id 控制检索范围；
        - 底层检索已经是 Hybrid RAG，而不是单一 lexical；
        - 最后额外生成 summary，方便右侧结果面板直接展示。
        """
        model_config = self._require_runnable_model_config(request.model_settings)
        scope_type = request.scope_type
        scope_id = request.scope_id if scope_type == "tree_recursive" else None
        if scope_type == "tree_recursive" and not scope_id:
            scope_id = ROOT_NODE_ID
        return self.retrieval_service.run(
            query=request.query,
            scope_type=scope_type,
            scope_id=scope_id,
            model_config=model_config,
        )

    def _normalize_agent_scope(self, scope_type: ScopeType, scope_id: str | None) -> tuple[ScopeType, str | None]:
        """把 Agent 的知识范围配置规范化。"""
        if scope_type == "none":
            return "none", None
        if scope_type == "global":
            return "global", None
        return "tree_recursive", scope_id or ROOT_NODE_ID

    def list_agents(self) -> list[AgentConfig]:
        return [self._normalize_agent_config(agent) for agent in self.agent_store.list_agents()]

    def get_agent(self, agent_id: str) -> AgentConfig:
        agent = self.agent_store.get_agent(agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent not found")
        return self._normalize_agent_config(agent)

    def create_agent(self, request: CreateAgentRequest) -> AgentConfig:
        model_config = self._require_runnable_model_config(request.model_settings)
        knowledge_scope_type, knowledge_scope_id = self._normalize_agent_scope(
            request.knowledge_scope_type,
            request.knowledge_scope_id,
        )
        return self.agent_store.create_agent(
            name=request.name,
            description=request.description,
            system_prompt=request.system_prompt,
            model_config=model_config,
            enabled_skills=self._default_enabled_skills(request.enabled_skills),
            knowledge_scope_type=knowledge_scope_type,
            knowledge_scope_id=knowledge_scope_id,
        )

    def update_agent(self, agent_id: str, request: UpdateAgentRequest) -> AgentConfig:
        current = self.agent_store.get_agent(agent_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Agent not found")

        next_scope_type = request.knowledge_scope_type or current.knowledge_scope_type
        next_scope_id = request.knowledge_scope_id if request.knowledge_scope_id is not None else current.knowledge_scope_id
        next_scope_type, next_scope_id = self._normalize_agent_scope(next_scope_type, next_scope_id)
        updated = self.agent_store.update_agent(
            agent_id,
            name=request.name,
            description=request.description,
            system_prompt=request.system_prompt,
            model_config=self._require_runnable_model_config(request.model_settings) if request.model_settings else None,
            enabled_skills=request.enabled_skills,
            knowledge_scope_type=next_scope_type,
            knowledge_scope_id=next_scope_id,
        )
        assert updated is not None
        return updated

    def run_agent(self, agent_id: str, request: RunAgentRequest) -> AgentRunResponse:
        """运行配置型 Agent。

        配置型 Agent 没有单独的执行引擎，它只是预先固化：
        - system prompt
        - model config
        - enabled skills
        - knowledge scope

        然后交给同一个 LangGraph 去执行。
        """
        agent = self.get_agent(agent_id)
        model_config = self._require_runnable_model_config(agent.model_settings)
        # Chat 入口默认使用全局知识范围；如果以后要做 scoped chat，
        # 只需要把这里的 scope_type / scope_id 改成前端传入值即可。
        aggregate = self._run_graph(
            {
                "thread_id": agent.id,
                "user_input": request.content,
                "model_config": model_config,
                "enabled_skills": agent.enabled_skills,
                "history_messages": [],
                "messages": [],
                "forced_route": "rag" if agent.knowledge_scope_type != "none" else None,
                "scope_type": "global" if agent.knowledge_scope_type == "none" else agent.knowledge_scope_type,
                "scope_id": agent.knowledge_scope_id,
                "system_prompt": agent.system_prompt or None,
            }
        )
        final_output = aggregate["final_output"]
        if not isinstance(final_output, FinalResponse):
            final_output = FinalResponse(
                answer="当前运行未生成最终输出。",
                citations=[],
                used_tools=[],
                next_actions=["检查 Agent 配置或查看后端日志。"],
            )
        return AgentRunResponse(
            agent=agent,
            result=final_output,
            citations=aggregate["citations"],
            retrieval_context=aggregate["retrieval_context"],
        )

    def get_catalog(self) -> CatalogResponse:
        catalog_models: list[ModelConfig] = []
        for provider in self.provider_store.list_providers():
            if not provider.enabled or len(provider.models) == 0:
                continue
            catalog_models.append(
                ModelConfig(provider=provider.id, model=provider.models[0].id, temperature=0.2, max_tokens=1024)
            )

        return CatalogResponse(
            models=catalog_models,
            skills=self.skill_registry.list_skills(),
            tools=self.skill_registry.tool_catalog(),
            learning_focus=[
                {"name": "Prompt / Messages", "description": "显式使用 system / human / ai message。"},
                {"name": "Structured Output", "description": "最终输出统一走 FinalResponse schema。"},
                {"name": "Runnable / LCEL", "description": "RAG 流程拆成可组合 runnable 管道。"},
                {"name": "LangGraph", "description": "用图管理路由、工具、检索和最终输出。"},
                {"name": "Scoped RAG", "description": "按 global / tree_recursive 控制检索范围。"},
            ],
        )

    def stream_message(self, thread_id: str, request: SendMessageRequest) -> Generator[str, None, None]:
        """Chat 模块的多轮流式入口。

        这是最完整的一条学习链路：
        1. 先把用户消息写进 ThreadStore；
        2. 再把历史消息 + 用户输入交给 LangGraph；
        3. LangGraph 边执行边产出 route / tool / retrieval / final 事件；
        4. ChatService 把这些事件流式推给前端；
        5. 最后再把 assistant 回复、tool_events、final_output 写回 SQLite。
        """
        thread = self.thread_store.get_thread_state(thread_id)
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")

        model_config = self._require_runnable_model_config(request.model_settings or thread.model_settings)
        enabled_skills = self._default_enabled_skills(request.enabled_skills or thread.enabled_skills)
        self.thread_store.update_thread_config(thread_id, model_config, enabled_skills)
        user_message = self.thread_store.append_message(thread_id, "human", request.content)
        history = self.thread_store.get_thread_state(thread_id)
        assert history is not None

        yield self._serialize_event(
            "message",
            {"role": "human", "content": user_message.content, "created_at": user_message.created_at.isoformat()},
        )

        aggregate = self._run_graph(
            {
                "thread_id": thread_id,
                "user_input": request.content,
                "model_config": model_config,
                "enabled_skills": enabled_skills,
                "history_messages": history.messages,
                "messages": self._to_graph_messages(history.messages),
                "scope_type": "global",
                "scope_id": None,
            }
        )

        for event in aggregate["stream_events"]:
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

    def list_providers(self) -> list[ProviderConfig]:
        return self.provider_store.list_providers()

    def get_provider(self, provider_id: str) -> ProviderConfig:
        provider = self.provider_store.get_provider(provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        return provider

    def update_provider(self, provider_id: str, request: UpdateProviderRequest) -> ProviderConfig:
        try:
            provider = self.provider_store.update_provider(
                provider_id,
                enabled=request.enabled,
                protocol=request.protocol,
                api_base_url=request.api_base_url,
                api_key=self.provider_store._KEEP_EXISTING if request.api_key is None else request.api_key,
                models=request.models,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if provider is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        return provider

    def test_provider(self, provider_id: str, request: UpdateProviderRequest) -> ProviderTestResponse:
        try:
            result = self.llm_service.test_provider_connection(provider_id, request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return result
