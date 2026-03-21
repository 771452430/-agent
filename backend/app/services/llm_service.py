"""LLM 访问层。

这个模块的设计重点是“让学习点显式可见”：
- ChatPromptTemplate + MessagesPlaceholder：学习 Prompt / Messages；
- with_structured_output：学习 Structured Output；
- provider 检测与 fallback：学习如何在本地模式下也能把链路跑通。
"""

from __future__ import annotations

import importlib

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langsmith import traceable

from ..schemas import ChatMessage, Citation, FinalResponse, ModelConfig


class LLMService:
    """统一处理模型配置、Prompt 组织与结构化输出。"""

    PROVIDER_MODULES = {
        "openai": "langchain_openai",
        "ollama": "langchain_ollama",
        "anthropic": "langchain_anthropic",
    }

    def __init__(self, allow_mock_model: bool = True) -> None:
        self.allow_mock_model = allow_mock_model

    def resolve_model_config(self, model_config: ModelConfig | None) -> ModelConfig:
        return model_config or ModelConfig()

    def _provider_available(self, provider: str) -> bool:
        module_name = self.PROVIDER_MODULES.get(provider)
        if module_name is None:
            return provider == "mock"
        try:
            importlib.import_module(module_name)
            return True
        except Exception:
            return False

    def _build_prompt(self) -> ChatPromptTemplate:
        return ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是一个 LangChain 学习助手。"
                    "你需要结合工具结果和检索上下文，输出结构化且可验证的答案。"
                    "如果当前处于本地学习模式，也要清楚说明答案依据来自哪一部分。",
                ),
                MessagesPlaceholder("history"),
                (
                    "human",
                    "用户最新问题：{query}\n\n"
                    "可用工具结果：{tool_result_summary}\n\n"
                    "检索上下文：\n{retrieval_context}\n\n"
                    "请给出清晰回答，并在有引用时保留 citations。",
                ),
            ]
        )

    def _build_history(self, messages: list[ChatMessage]) -> list[BaseMessage]:
        history: list[BaseMessage] = []
        for message in messages[-6:]:
            if message.role == "assistant":
                history.append(AIMessage(content=message.content))
            elif message.role == "human":
                history.append(HumanMessage(content=message.content))
        return history

    def _tool_summary(self, tool_outputs: dict[str, object]) -> str:
        if not tool_outputs:
            return "无"
        return "\n".join(f"- {name}: {value}" for name, value in tool_outputs.items())

    def _fallback_response(
        self,
        query: str,
        tool_outputs: dict[str, object],
        citations: list[Citation],
        retrieval_context: str,
    ) -> FinalResponse:
        answer_parts = [
            "当前处于本地学习模式，以下答案由 LangGraph 节点、工具结果和检索上下文拼装生成。",
        ]
        if tool_outputs:
            answer_parts.append("工具结果如下：")
            answer_parts.extend(f"- {name}: {value}" for name, value in tool_outputs.items())
        if retrieval_context:
            answer_parts.append("知识库命中片段已加入回答依据。")
        if not tool_outputs and not retrieval_context:
            answer_parts.append(f"收到你的问题：{query}")
            answer_parts.append("当前没有触发额外工具或知识检索，因此返回的是一个基础说明型回答。")
        next_actions = [
            "尝试上传一份文档，再问一个带“根据文档”或“知识库”关键词的问题。",
            "尝试输入“报销 3 天 每天 100 含税”，观察工具链如何运行。",
        ]
        return FinalResponse(
            answer="\n".join(answer_parts),
            citations=citations,
            used_tools=list(tool_outputs.keys()),
            next_actions=next_actions,
        )

    @traceable(name="final_response_generation")
    def generate_response(
        self,
        query: str,
        messages: list[ChatMessage],
        tool_outputs: dict[str, object],
        citations: list[Citation],
        retrieval_context: str,
        model_config: ModelConfig,
    ) -> FinalResponse:
        prompt = self._build_prompt()
        prompt_value = prompt.invoke(
            {
                "history": self._build_history(messages),
                "query": query,
                "tool_result_summary": self._tool_summary(tool_outputs),
                "retrieval_context": retrieval_context or "无",
            }
        )

        if model_config.provider != "mock" and self._provider_available(model_config.provider):
            try:
                model = init_chat_model(
                    model=model_config.model,
                    model_provider=model_config.provider,
                    temperature=model_config.temperature,
                )
                structured_model = model.with_structured_output(FinalResponse)
                result = structured_model.invoke(prompt_value.to_messages())
                return FinalResponse.model_validate(result)
            except Exception:
                pass

        return self._fallback_response(
            query=query,
            tool_outputs=tool_outputs,
            citations=citations,
            retrieval_context=retrieval_context,
        )
