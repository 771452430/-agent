"""LLM 访问层。

这一版把“运行时选模型”和“全局 provider 配置”明确拆开：
- Thread / Agent 里只保存 `provider + model` 引用；
- API Key / Base URL / 协议兼容格式统一从 ProviderStore 读取；
- 这样你学习时能更清楚地看到：模型调用其实分成“选引用”和“解析真实连接参数”两步。

除了真实调用模型，这个模块还负责：
- 校验某个 `ModelConfig` 是否可运行；
- 测试 provider 连接并尝试拉取模型列表；
- 继续保留本地 learning mode fallback，避免 demo 因外部依赖不可用而完全失去可玩性。
"""

from __future__ import annotations

import html
import importlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import error as urlerror
from urllib import parse, request
from zoneinfo import ZoneInfo

from langchain.chat_models import init_chat_model
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langsmith import traceable
from pydantic import BaseModel, Field

from ..schemas import (
    ChatMessage,
    Citation,
    FinalResponse,
    ModelConfig,
    OwnerRule,
    ParsedBug,
    ProviderModel,
    ProviderProtocol,
    ProviderRuntimeConfig,
    ProviderTestResponse,
    RAGQueryBundle,
    RAGQueryVariant,
    RetrievalCandidateDebug,
    RetrievalProfile,
    SupportIssueClassificationResult,
    SupportIssueDraftResult,
    SupportIssueReviewResult,
    UpdateProviderRequest,
    WatcherOwnerSuggestion,
)
from .provider_store import OFFICIAL_OPENAI_BASE_URLS, ProviderStore


class ParsedBugBatch(BaseModel):
    """用于结构化输出的 bug 列表包装器。"""

    bugs: list[ParsedBug] = Field(default_factory=list)


class RetrievalRerankItem(BaseModel):
    """单条候选的 rerank 结果。"""

    chunk_id: str
    relevance_score: float = 0.0
    useful_for_answer: bool = False
    reason: str = ""


class RetrievalRerankBatch(BaseModel):
    """候选 rerank 批量结果。"""

    items: list[RetrievalRerankItem] = Field(default_factory=list)


DEFAULT_SYSTEM_PROMPT = (
    "你是一个 LangChain 学习助手。"
    "你需要结合工具结果和检索上下文，输出结构化且可验证的答案。"
    "如果当前处于本地学习模式，也要清楚说明答案依据来自哪一部分。"
)

# 一些第三方 OpenAI-compatible 网关并不是“稳定地返回同一种错误”，
# 而是会出现：
# - 某次请求打到异常节点，直接回空串 / HTML / 502；
# - 下一次同样的请求又恢复正常。
# 因此这里维护一组“值得自动重试”的特征，尽量把这种偶发抖动挡在后端内部。
RETRYABLE_PROVIDER_ERROR_PATTERNS = (
    "unsupported content type",
    "expecting value: line 1 column 1",
    "bad gateway",
    "502",
    "503",
    "504",
    "<!doctype html",
    "<html",
    "temporarily unavailable",
    "connection reset",
    "connection aborted",
    "timed out",
    "timeout",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LLMService:
    """统一处理 provider 解析、Prompt 组织与结构化输出。"""

    PROTOCOL_MODULES: dict[ProviderProtocol, str] = {
        "openai_compatible": "langchain_openai",
        "anthropic_compatible": "langchain_anthropic",
        "ollama_native": "langchain_ollama",
        "mock_local": "",
    }

    PROTOCOL_TO_LANGCHAIN_PROVIDER: dict[ProviderProtocol, str] = {
        "openai_compatible": "openai",
        "anthropic_compatible": "anthropic",
        "ollama_native": "ollama",
        "mock_local": "mock",
    }

    def __init__(self, provider_store: ProviderStore, allow_mock_model: bool = True) -> None:
        # `provider_store` 负责读取“真实连接配置”，
        # 而 LLMService 负责把这些配置变成真正可调用的模型实例或 HTTP 请求。
        self.provider_store = provider_store
        self.allow_mock_model = allow_mock_model

    def _display_timezone(self):
        try:
            return ZoneInfo("Asia/Shanghai")
        except Exception:
            return timezone(timedelta(hours=8))

    def _format_display_datetime(self, value: datetime) -> str:
        return value.astimezone(self._display_timezone()).strftime("%Y-%m-%d %H:%M:%S %Z")

    def _build_learning_provider(self) -> ProviderRuntimeConfig:
        """返回内置 learning mode 对应的运行时 provider。

        Learning Mode 是一个明确的运行模式，不应该受“真实 provider 是否开启”的影响。
        所以这里即使 SQLite 里的 mock provider 被关闭，也仍然构造一个可用的本地运行时配置。
        """

        provider = self.provider_store.get_runtime_provider("mock")
        if provider is not None:
            return provider.model_copy(update={"enabled": True})
        return ProviderRuntimeConfig(
            id="mock",
            name="Learning Mode",
            enabled=True,
            protocol="mock_local",
            allowed_protocols=["mock_local"],
            api_base_url="",
            api_key=None,
            models=[ProviderModel(id="learning-mode", label="Learning Mode", source="manual")],
            locked=True,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )

    def resolve_model_config(self, model_config: ModelConfig | None) -> ModelConfig:
        """把空配置解析成默认的 mock learning mode。"""

        if model_config is not None:
            return model_config

        provider_id, model_id = self.provider_store.default_model_config()
        return ModelConfig(mode="learning", provider=provider_id, model=model_id)

    def normalize_model_config_reference(self, model_config: ModelConfig | None) -> ModelConfig:
        """把历史 provider 引用修正为当前可用的 provider。

        当前主要兼容一个历史场景：
        - 旧数据里使用 `openai`
        - 但第三方网关配置已迁到 `custom_openai`
        """

        resolved = self.resolve_model_config(model_config)
        if resolved.mode != "provider" or resolved.provider != "openai":
            return resolved

        custom_openai = self.provider_store.get_runtime_provider("custom_openai")
        if custom_openai is None or not custom_openai.enabled:
            return resolved

        if any(item.id == resolved.model for item in custom_openai.models):
            return resolved.model_copy(update={"provider": "custom_openai"})

        return resolved

    def ensure_model_config_runnable(self, model_config: ModelConfig | None) -> tuple[ModelConfig, ProviderRuntimeConfig]:
        """确认当前 `provider + model` 是可运行的。

        这里不会静默回退成别的 provider，因为这会让学习时很难看懂：
        明明你选的是 MiniMax，为什么最后却跑成 mock？
        所以如果 provider 被禁用、模型不存在，就直接抛出明确错误。
        """

        resolved = self.normalize_model_config_reference(model_config)
        if resolved.mode == "learning":
            return resolved.model_copy(update={"provider": "mock", "model": "learning-mode"}), self._build_learning_provider()

        provider = self.provider_store.get_runtime_provider(resolved.provider)
        if provider is None:
            raise ValueError(f"未找到 provider: {resolved.provider}")
        if provider.protocol == "mock_local":
            raise ValueError("真实接口模式下不能选择 Learning Mode，请切换到已配置的真实 provider。")
        if not provider.enabled:
            raise ValueError(f"Provider `{provider.name}` 当前已被禁用，请到模型设置里开启后再试。")

        available_model_ids = {model.id for model in provider.models}
        normalized = resolved
        if normalized.model == "" and len(provider.models) > 0:
            normalized = normalized.model_copy(update={"model": provider.models[0].id})

        if len(available_model_ids) == 0 and provider.protocol != "mock_local":
            raise ValueError(f"Provider `{provider.name}` 还没有可用模型，请先在模型设置里添加模型。")
        if normalized.model == "":
            raise ValueError(f"Provider `{provider.name}` 尚未选择模型，请先补充模型配置。")
        if len(available_model_ids) > 0 and normalized.model not in available_model_ids:
            raise ValueError(
                f"模型 `{normalized.model}` 不在 provider `{provider.name}` 的可用列表里，请到模型设置中修正。"
            )

        return normalized, provider

    def _provider_available(self, protocol: ProviderProtocol) -> bool:
        module_name = self.PROTOCOL_MODULES.get(protocol, "")
        if module_name == "":
            return True
        try:
            importlib.import_module(module_name)
            return True
        except Exception:
            return False

    def _build_prompt(self, system_prompt: str | None = None) -> ChatPromptTemplate:
        # 这里把最终要喂给模型的信息拆成 4 块：
        # system prompt、历史消息、最新问题、工具/检索结果。
        # 这样你能清楚看到一个回答到底依赖了哪些上下文来源。
        return ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt or DEFAULT_SYSTEM_PROMPT),
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

    def _is_retryable_provider_error(self, message: str) -> bool:
        """判断错误是否像“第三方网关抖动”而不是明确的业务配置错误。

        这里不会把所有失败都重试，例如：
        - API Key 无效
        - provider 被禁用
        - 模型名不存在
        这些属于确定性错误，重试没有意义。
        """

        normalized = re.sub(r"\s+", " ", message).strip().lower()
        if normalized == "":
            return False
        return any(pattern in normalized for pattern in RETRYABLE_PROVIDER_ERROR_PATTERNS)

    def _build_history(self, messages: list[ChatMessage]) -> list[BaseMessage]:
        # 这里只保留最近几轮历史，是一个有意的教学取舍：
        # 既能体现多轮上下文，又不会让 prompt 膨胀得太难读。
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
        *,
        query: str,
        tool_outputs: dict[str, object],
        citations: list[Citation],
        retrieval_context: str,
        system_prompt: str | None = None,
    ) -> FinalResponse:
        # Learning mode 不调用真实模型，而是把已有中间产物重新组织成一个“可解释答案”。
        # 这样即使没有 API Key，学习者也能走通整条链路。
        answer_parts = [
            "当前处于本地学习模式，以下答案由 LangGraph 节点、工具结果和检索上下文拼装生成。",
        ]
        if system_prompt:
            answer_parts.append(f"当前使用了自定义 system prompt：{system_prompt[:80]}")
        if tool_outputs:
            answer_parts.append("工具结果如下：")
            answer_parts.extend(f"- {name}: {value}" for name, value in tool_outputs.items())
        if retrieval_context:
            answer_parts.append("知识库命中片段已加入回答依据。")
        if not tool_outputs and not retrieval_context:
            answer_parts.append(f"收到你的问题：{query}")
            answer_parts.append("当前没有触发额外工具或知识检索，因此返回的是一个基础说明型回答。")
        return FinalResponse(
            answer="\n".join(answer_parts),
            citations=citations,
            used_tools=list(tool_outputs.keys()),
            next_actions=[
                "尝试上传一组目录文档，再在检索模式里切换 global / tree scope。",
                "尝试创建一个绑定知识范围的 Agent，对比它和普通 Chat 的行为差异。",
            ],
        )

    def _provider_error_response(
        self,
        *,
        query: str,
        tool_outputs: dict[str, object],
        citations: list[Citation],
        retrieval_context: str,
        provider: ProviderRuntimeConfig,
        model_config: ModelConfig,
        message: str,
    ) -> FinalResponse:
        """真实接口模式失败时返回明确错误，而不是假装成 learning mode。"""

        return FinalResponse(
            answer=(
                f"真实接口模式调用失败。\n"
                f"provider: {provider.name}\n"
                f"model: {model_config.model}\n"
                f"原因: {message}"
            ),
            citations=citations,
            used_tools=list(tool_outputs.keys()),
            next_actions=[
                "检查模型设置里的 API Key、Base URL 和协议格式是否正确。",
                "确认后端环境已经安装对应 provider 的 LangChain SDK。",
                f"如果你只是想先体验链路，可以把运行模式切回 learning。",
            ],
        )

    def _stringify_model_content(self, content: Any) -> str:
        """把不同 provider 返回的消息内容统一转成字符串。

        一些 OpenAI-compatible / Anthropic-compatible 厂商在能力上兼容，
        但在“结构化输出”细节上并不完全一致。这里把 LangChain 返回的 content
        做一次宽松归一化，让我们至少能拿到一段可展示的文本答案。
        """

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                parts.append(str(item))
            return "\n".join(part for part in parts if part.strip() != "")
        return str(content)

    def _plain_text_response(
        self,
        *,
        model: Any,
        prompt_messages: list[BaseMessage],
        citations: list[Citation],
        tool_outputs: dict[str, object],
    ) -> FinalResponse:
        """当结构化输出失败时，退回到普通文本模式。

        这一步很适合学习“真实厂商兼容 ≠ 所有高级特性都兼容”这个事实：
        - 基础 chat completion 往往能通；
        - 但 structured output / tool calling 的协议细节可能各家不同。
        """

        # 真实厂商的“结构化输出”支持度并不完全一致，
        # 因此这里准备了一个更保守但更兼容的纯文本兜底路径。
        raw_result = model.invoke(prompt_messages)
        answer = self._stringify_model_content(getattr(raw_result, "content", raw_result)).strip()
        if answer == "":
            answer = "模型已返回结果，但内容为空。"
        return FinalResponse(
            answer=answer,
            citations=citations,
            used_tools=list(tool_outputs.keys()),
            next_actions=[],
        )

    def _serialize_messages(self, messages: list[BaseMessage]) -> list[dict[str, str]]:
        """把 LangChain message 转成兼容协议常见的 role/content 结构。"""

        serialized: list[dict[str, str]] = []
        for message in messages:
            role = "user"
            if getattr(message, "type", "") == "system":
                role = "system"
            elif getattr(message, "type", "") in {"ai", "assistant"}:
                role = "assistant"
            serialized.append({"role": role, "content": self._stringify_model_content(message.content)})
        return serialized

    def _candidate_completion_urls(self, protocol: ProviderProtocol, base_url: str) -> list[str]:
        """为不同协议生成候选 completion 地址。"""

        base = self._normalize_base_url(base_url)
        if protocol == "ollama_native":
            return [base + "/api/chat"]

        if protocol == "anthropic_compatible":
            candidates = [base + "/messages"]
            if not base.endswith("/v1"):
                candidates.append(base + "/v1/messages")
            return list(dict.fromkeys(candidates))

        candidates = [base + "/chat/completions"]
        if not base.endswith("/v1"):
            candidates.append(base + "/v1/chat/completions")
        return list(dict.fromkeys(candidates))

    def _request_json_post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        """发送一个 JSON POST 请求并尽量解析 JSON 响应。"""

        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method="POST")
        with request.urlopen(req, timeout=45) as response:
            raw = response.read().decode("utf-8")
        try:
            parsed = json.loads(raw or "{}")
        except json.JSONDecodeError:
            normalized = raw.lstrip().lower()
            # 有些兼容网关在异常时直接回整页 HTML；
            # 这里保留一个更可读的错误，避免最终只看到“第 1 列无法解析 JSON”。
            if normalized.startswith("<!doctype html") or normalized.startswith("<html"):
                raise ValueError(f"上游返回了 HTML，而不是 JSON：{raw[:240]}")
            # 少数兼容层会直接回纯文本。
            # 这种场景虽然不标准，但先保留下来，让后面的文本提取逻辑还有机会兜底。
            if raw.strip() != "":
                return {"raw": raw}
            raise
        if isinstance(parsed, dict):
            return parsed
        return {"raw": parsed}

    def _build_completion_headers(self, provider: ProviderRuntimeConfig) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if provider.protocol == "anthropic_compatible":
            headers["anthropic-version"] = "2023-06-01"
        if provider.api_key:
            headers["Authorization"] = f"Bearer {provider.api_key}"
            headers["x-api-key"] = provider.api_key
        return headers

    def _build_completion_payload(
        self,
        *,
        provider: ProviderRuntimeConfig,
        model_config: ModelConfig,
        prompt_messages: list[BaseMessage],
    ) -> dict[str, Any]:
        messages = self._serialize_messages(prompt_messages)
        if provider.protocol == "ollama_native":
            return {"model": model_config.model, "messages": messages, "stream": False}
        if provider.protocol == "anthropic_compatible":
            system_text = "\n".join(item["content"] for item in messages if item["role"] == "system").strip()
            non_system_messages = [item for item in messages if item["role"] != "system"]
            payload: dict[str, Any] = {
                "model": model_config.model,
                "messages": non_system_messages,
                "temperature": model_config.temperature,
                "max_tokens": model_config.max_tokens,
            }
            if system_text != "":
                payload["system"] = system_text
            return payload
        return {
            "model": model_config.model,
            "messages": messages,
            "temperature": model_config.temperature,
            "max_tokens": model_config.max_tokens,
        }

    def _serialize_messages_with_content_blocks(
        self,
        messages: list[BaseMessage],
        *,
        block_type: str,
    ) -> list[dict[str, Any]]:
        serialized = self._serialize_messages(messages)
        return [
            {
                "role": item["role"],
                "content": [{"type": block_type, "text": item["content"]}],
            }
            for item in serialized
            if item["content"].strip() != ""
        ]

    def _build_openai_chat_block_payload(
        self,
        *,
        model_config: ModelConfig,
        prompt_messages: list[BaseMessage],
    ) -> dict[str, Any]:
        return {
            "model": model_config.model,
            "messages": self._serialize_messages_with_content_blocks(prompt_messages, block_type="text"),
            "temperature": model_config.temperature,
            "max_tokens": model_config.max_tokens,
        }

    def _build_openai_responses_payload(
        self,
        *,
        model_config: ModelConfig,
        prompt_messages: list[BaseMessage],
    ) -> dict[str, Any]:
        return {
            "model": model_config.model,
            "input": self._serialize_messages_with_content_blocks(prompt_messages, block_type="input_text"),
            "temperature": model_config.temperature,
            "max_output_tokens": model_config.max_tokens,
        }

    def _extract_completion_text(self, provider: ProviderRuntimeConfig, payload: dict[str, Any]) -> str:
        """从不同兼容协议的响应体里提取回答文本。"""

        if provider.protocol == "ollama_native":
            message = payload.get("message")
            if isinstance(message, dict):
                return self._stringify_model_content(message.get("content", ""))
            return self._stringify_model_content(payload.get("response", ""))

        if provider.protocol == "anthropic_compatible":
            content = payload.get("content")
            if isinstance(content, list):
                return self._stringify_model_content(content)
            if isinstance(content, str):
                return content

        choices = payload.get("choices")
        if isinstance(choices, list) and len(choices) > 0:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict):
                    return self._stringify_model_content(message.get("content", ""))
                text = first.get("text")
                if isinstance(text, str):
                    return text

        if isinstance(payload.get("output_text"), str):
            return str(payload["output_text"])
        output = payload.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            parts.append(str(block["text"]))
                if isinstance(item.get("text"), str):
                    parts.append(str(item["text"]))
            if parts:
                return "\n".join(part for part in parts if part.strip() != "")
        if isinstance(payload.get("text"), str):
            return str(payload["text"])
        if isinstance(payload.get("raw"), str):
            return str(payload["raw"])
        return self._stringify_model_content(payload)

    def _direct_completion_response(
        self,
        *,
        provider: ProviderRuntimeConfig,
        model_config: ModelConfig,
        prompt_messages: list[BaseMessage],
        citations: list[Citation],
        tool_outputs: dict[str, object],
    ) -> FinalResponse:
        """协议级 HTTP 兜底。

        有些“兼容 OpenAI/Anthropic”网关可以完成聊天请求，
        但返回体细节不满足 LangChain SDK 的严格预期。
        这里直接按协议发 HTTP 请求，尽量把真实模式打通。
        """

        headers = self._build_completion_headers(provider)
        attempt_errors: list[str] = []
        for attempt_label, url, payload in self._candidate_completion_attempts(
            provider=provider,
            model_config=model_config,
            prompt_messages=prompt_messages,
        ):
            for retry_index in range(3):
                try:
                    response_payload = self._request_json_post(url, headers=headers, payload=payload)
                    answer = self._extract_completion_text(provider, response_payload).strip()
                    if answer == "":
                        answer = "模型已返回响应，但当前未能解析出文本内容。"
                    return FinalResponse(
                        answer=answer,
                        citations=citations,
                        used_tools=list(tool_outputs.keys()),
                        next_actions=[],
                    )
                except urlerror.HTTPError as exc:
                    try:
                        detail = exc.read().decode("utf-8")
                    except Exception:
                        detail = str(exc)
                    summary = re.sub(r"\s+", " ", detail).strip()
                    if len(summary) > 240:
                        summary = summary[:237] + "..."
                    formatted = f"{attempt_label} {parse.urlparse(url).path}: {exc.code} {summary}".strip()
                    if retry_index < 2 and self._is_retryable_provider_error(formatted):
                        time.sleep(0.8 * (retry_index + 1))
                        continue
                    attempt_errors.append(formatted)
                    break
                except Exception as exc:
                    summary = re.sub(r"\s+", " ", str(exc)).strip()
                    if len(summary) > 240:
                        summary = summary[:237] + "..."
                    formatted = f"{attempt_label} {parse.urlparse(url).path}: {summary}".strip()
                    if retry_index < 2 and self._is_retryable_provider_error(formatted):
                        time.sleep(0.8 * (retry_index + 1))
                        continue
                    attempt_errors.append(formatted)
                    break
        raise RuntimeError("；".join(attempt_errors) or "协议级请求失败")

    def _generate_provider_response_once(
        self,
        *,
        provider: ProviderRuntimeConfig,
        normalized_config: ModelConfig,
        prompt_messages: list[BaseMessage],
        query: str,
        tool_outputs: dict[str, object],
        citations: list[Citation],
        retrieval_context: str,
        system_prompt: str | None,
    ) -> FinalResponse:
        """执行一次真实 provider 调用。

        之所以单独拆出来，是为了让外层可以在“第三方网关疑似抖动”时整轮重试。
        """

        if not self._provider_available(provider.protocol):
            return self._provider_error_response(
                query=query,
                tool_outputs=tool_outputs,
                citations=citations,
                retrieval_context=retrieval_context,
                provider=provider,
                model_config=normalized_config,
                message=f"当前环境缺少 `{self.PROTOCOL_MODULES[provider.protocol]}` 依赖。",
            )

        try:
            model = init_chat_model(**self._build_model_kwargs(provider, normalized_config))
            try:
                structured_model = model.with_structured_output(FinalResponse)
                result = structured_model.invoke(prompt_messages)
                return FinalResponse.model_validate(result)
            except Exception:
                try:
                    return self._plain_text_response(
                        model=model,
                        prompt_messages=prompt_messages,
                        citations=citations,
                        tool_outputs=tool_outputs,
                    )
                except Exception:
                    return self._direct_completion_response(
                        provider=provider,
                        model_config=normalized_config,
                        prompt_messages=prompt_messages,
                        citations=citations,
                        tool_outputs=tool_outputs,
                    )
        except Exception as exc:
            try:
                return self._direct_completion_response(
                    provider=provider,
                    model_config=normalized_config,
                    prompt_messages=prompt_messages,
                    citations=citations,
                    tool_outputs=tool_outputs,
                )
            except Exception as fallback_exc:
                return self._provider_error_response(
                    query=query,
                    tool_outputs=tool_outputs,
                    citations=citations,
                    retrieval_context=retrieval_context,
                    provider=provider,
                    model_config=normalized_config,
                    message=f"{exc}；协议级兜底也失败：{fallback_exc}",
                )

    def _build_model_kwargs(self, provider: ProviderRuntimeConfig, model_config: ModelConfig) -> dict[str, Any]:
        """把全局 provider 配置解析成 LangChain 能理解的连接参数。"""

        if provider.protocol == "mock_local":
            return {}

        kwargs: dict[str, Any] = {
            "model": model_config.model,
            "model_provider": self.PROTOCOL_TO_LANGCHAIN_PROVIDER[provider.protocol],
            "temperature": model_config.temperature,
        }
        if provider.api_base_url != "":
            kwargs["base_url"] = provider.api_base_url
        if provider.api_key:
            kwargs["api_key"] = provider.api_key
        if provider.protocol == "ollama_native" and provider.api_base_url != "":
            kwargs["base_url"] = provider.api_base_url
        return kwargs

    @traceable(name="final_response_generation")
    def generate_response(
        self,
        *,
        query: str,
        messages: list[ChatMessage],
        tool_outputs: dict[str, object],
        citations: list[Citation],
        retrieval_context: str,
        model_config: ModelConfig,
        system_prompt: str | None = None,
    ) -> FinalResponse:
        normalized_config, provider = self.ensure_model_config_runnable(model_config)

        prompt = self._build_prompt(system_prompt=system_prompt)
        prompt_value = prompt.invoke(
            {
                "history": self._build_history(messages),
                "query": query,
                "tool_result_summary": self._tool_summary(tool_outputs),
                "retrieval_context": retrieval_context or "无",
            }
        )

        if normalized_config.mode == "learning":
            return self._fallback_response(
                query=query,
                tool_outputs=tool_outputs,
                citations=citations,
                retrieval_context=retrieval_context,
                system_prompt=system_prompt,
            )

        prompt_messages = prompt_value.to_messages()
        for retry_index in range(3):
            result = self._generate_provider_response_once(
                provider=provider,
                normalized_config=normalized_config,
                prompt_messages=prompt_messages,
                query=query,
                tool_outputs=tool_outputs,
                citations=citations,
                retrieval_context=retrieval_context,
                system_prompt=system_prompt,
            )
            # 对第三方兼容网关的间歇性抖动做整轮重试。
            # 只有当错误特征像“上游节点异常”时才重试，避免无意义重复调用。
            if not result.answer.startswith("真实接口模式调用失败。"):
                return result
            if retry_index >= 2 or not self._is_retryable_provider_error(result.answer):
                return result
            time.sleep(1.0 * (retry_index + 1))
        return result

    def _dedupe_strings(self, values: list[str], *, limit: int | None = None) -> list[str]:
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = " ".join(str(value or "").split()).strip()
            if normalized == "":
                continue
            lowered = normalized.lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(normalized)
            if limit is not None and len(deduped) >= limit:
                break
        return deduped

    def _identifier_terms(self, text: str) -> list[str]:
        values = re.findall(r"[A-Za-z][A-Za-z0-9._:/-]{2,}", text or "")
        values.extend(re.findall(r"[A-Z]{2,}[-_ ]?\d{2,}", text or ""))
        return self._dedupe_strings(values, limit=8)

    def _chinese_terms(self, text: str) -> list[str]:
        values = re.findall(r"[\u4e00-\u9fff]{2,8}", text or "")
        # 过滤掉太泛的提示语，避免 query 扩写被噪音词污染。
        ignored = {"问题", "请问", "帮忙", "说明", "处理", "目前", "这个", "那个"}
        return [item for item in self._dedupe_strings(values, limit=8) if item not in ignored]

    def _normalize_query_text(self, text: str) -> str:
        normalized = str(text or "").replace("请根据文档", "").replace("请参考资料", "")
        normalized = normalized.replace("请帮我", "").replace("帮我看下", "")
        return " ".join(normalized.split()).strip()

    def _build_query_variants(
        self,
        *,
        original_query: str,
        normalized_query: str,
        rewritten_query: str,
        keyword_queries: list[str],
        sub_queries: list[str],
    ) -> list[RAGQueryVariant]:
        variants: list[RAGQueryVariant] = []
        variants.append(RAGQueryVariant(label="original", query=original_query, source="original"))
        if normalized_query.lower() != original_query.lower():
            variants.append(RAGQueryVariant(label="canonical", query=normalized_query, source="normalized"))
        if rewritten_query.strip() != "" and rewritten_query.strip().lower() not in {
            original_query.lower(),
            normalized_query.lower(),
        }:
            variants.append(RAGQueryVariant(label="rewritten", query=rewritten_query, source="rewrite"))
        for index, item in enumerate(keyword_queries, start=1):
            variants.append(RAGQueryVariant(label=f"keyword_{index}", query=item, source="keyword"))
        for index, item in enumerate(sub_queries, start=1):
            variants.append(RAGQueryVariant(label=f"sub_{index}", query=item, source="sub_query"))
        deduped: list[RAGQueryVariant] = []
        seen: set[str] = set()
        for item in variants:
            lowered = item.query.strip().lower()
            if lowered == "" or lowered in seen:
                continue
            seen.add(lowered)
            deduped.append(item)
            if len(deduped) >= 4:
                break
        return deduped

    def _fallback_query_bundle(
        self,
        *,
        query: str,
        retrieval_profile: RetrievalProfile,
        context: dict[str, Any] | None,
    ) -> RAGQueryBundle:
        normalized_query = self._normalize_query_text(query) or query.strip()
        context_text = self._normalize_query_text(json.dumps(context or {}, ensure_ascii=False))
        question = self._normalize_query_text(str((context or {}).get("question") or query))
        module_value = self._normalize_query_text(str((context or {}).get("module_value") or ""))
        category = self._normalize_query_text(str((context or {}).get("category") or ""))
        must_terms = self._dedupe_strings(
            self._identifier_terms(query + "\n" + context_text) + self._chinese_terms(question),
            limit=6,
        )
        keyword_queries = self._dedupe_strings(
            [
                " ".join(must_terms[:4]),
                f"{module_value} {question}".strip(),
                f"{category} {question}".strip(),
            ],
            limit=3,
        )
        sub_queries = self._dedupe_strings(
            [
                normalized_query,
                f"{question} 报错 失败 异常".strip() if any(token in question for token in ("报错", "失败", "异常", "无法")) else "",
                f"{module_value} {normalized_query}".strip() if module_value != "" else "",
                f"{category} {normalized_query}".strip() if category != "" else "",
            ],
            limit=4,
        )
        rewritten_query = sub_queries[0] if sub_queries else normalized_query
        filters: dict[str, str] = {}
        if retrieval_profile == "support_issue" and module_value != "":
            filters["module"] = module_value
        if category != "":
            filters["category"] = category
        return RAGQueryBundle(
            original_query=query.strip(),
            normalized_query=normalized_query,
            rewritten_query=rewritten_query,
            keyword_queries=keyword_queries,
            sub_queries=sub_queries,
            must_terms=must_terms,
            filters=filters,
            query_variants=self._build_query_variants(
                original_query=query.strip(),
                normalized_query=normalized_query,
                rewritten_query=rewritten_query,
                keyword_queries=keyword_queries,
                sub_queries=sub_queries,
            ),
        )

    def build_rag_query_bundle(
        self,
        *,
        query: str,
        retrieval_profile: RetrievalProfile,
        context: dict[str, Any] | None,
        model_config: ModelConfig,
    ) -> RAGQueryBundle:
        """构建 query bundle，支持真实模型改写和 learning mode 回退。"""

        fallback = self._fallback_query_bundle(query=query, retrieval_profile=retrieval_profile, context=context)
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是 RAG Query Rewrite 子 agent。"
                    "请基于原始问题和上下文，输出更适合检索的 query bundle。"
                    "要求：保留原问题事实，不得虚构。"
                    "你只能做规整、扩写、拆分、关键词抽取。"
                    "query_variants 留空也可以，系统会自行补齐。",
                ),
                (
                    "human",
                    "检索场景：{retrieval_profile}\n"
                    "原始问题：{query}\n\n"
                    "补充上下文：\n{context}",
                ),
            ]
        )
        structured = RAGQueryBundle.model_validate(
            self._invoke_structured_output_with_fallback(
                prompt=prompt,
                prompt_variables={
                    "retrieval_profile": retrieval_profile,
                    "query": query,
                    "context": self._json_preview(context or {}, max_chars=2400),
                },
                response_model=RAGQueryBundle,
                model_config=model_config,
                fallback_result=fallback,
            )
        )
        normalized_query = structured.normalized_query.strip() or fallback.normalized_query
        rewritten_query = structured.rewritten_query.strip() or structured.normalized_query.strip() or fallback.rewritten_query
        keyword_queries = self._dedupe_strings(structured.keyword_queries or fallback.keyword_queries, limit=3)
        sub_queries = self._dedupe_strings(structured.sub_queries or fallback.sub_queries, limit=4)
        must_terms = self._dedupe_strings(structured.must_terms or fallback.must_terms, limit=6)
        filters = dict(fallback.filters)
        filters.update({key: str(value) for key, value in structured.filters.items() if str(value).strip() != ""})
        return structured.model_copy(
            update={
                "original_query": query.strip(),
                "normalized_query": normalized_query,
                "rewritten_query": rewritten_query,
                "keyword_queries": keyword_queries,
                "sub_queries": sub_queries,
                "must_terms": must_terms,
                "filters": filters,
                "query_variants": self._build_query_variants(
                    original_query=query.strip(),
                    normalized_query=normalized_query,
                    rewritten_query=rewritten_query,
                    keyword_queries=keyword_queries,
                    sub_queries=sub_queries,
                ),
            }
        )

    def _fallback_rerank_candidates(
        self,
        *,
        query: str,
        candidates: list[RetrievalCandidateDebug],
        retrieval_profile: RetrievalProfile,
    ) -> list[RetrievalCandidateDebug]:
        tokens = set(self._dedupe_strings(self._identifier_terms(query) + self._chinese_terms(query), limit=8))
        reranked: list[RetrievalCandidateDebug] = []
        for candidate in candidates:
            haystack = " ".join(
                [
                    candidate.document_name or "",
                    candidate.relative_path or "",
                    candidate.heading_path or "",
                    candidate.snippet or "",
                ]
            ).lower()
            overlap = 0.0
            if tokens:
                overlap = len([token for token in tokens if token.lower() in haystack]) / len(tokens)
            score = 0.18 + min(0.42, overlap * 0.5) + min(0.25, candidate.fused_score * 6.0)
            if retrieval_profile == "support_issue" and candidate.metadata.get("source") == "approved_case":
                score += 0.08
            useful_for_answer = score >= 0.38
            reason = "关键词与标题/片段匹配度较高。" if overlap > 0 else "主要依赖 hybrid 融合分进入候选。"
            reranked.append(
                candidate.model_copy(
                    update={
                        "relevance_score": round(max(0.0, min(1.0, score)), 4),
                        "useful_for_answer": useful_for_answer,
                        "reason": reason,
                    }
                )
            )
        reranked.sort(key=lambda item: (item.relevance_score, item.fused_score), reverse=True)
        return reranked

    def rerank_retrieval_candidates(
        self,
        *,
        query: str,
        candidates: list[RetrievalCandidateDebug],
        retrieval_profile: RetrievalProfile,
        model_config: ModelConfig,
    ) -> list[RetrievalCandidateDebug]:
        """对候选片段做结构化 rerank。"""

        if not candidates:
            return []

        top_candidates = candidates[:12]
        fallback = self._fallback_rerank_candidates(
            query=query,
            candidates=top_candidates,
            retrieval_profile=retrieval_profile,
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是 RAG Rerank 子 agent。"
                    "请判断每个候选片段是否足以支撑回答当前问题。"
                    "只允许基于候选本身做相关性判断，不允许编造内容。",
                ),
                (
                    "human",
                    "检索场景：{retrieval_profile}\n"
                    "问题：{query}\n\n"
                    "候选片段：\n{candidates}",
                ),
            ]
        )
        structured = RetrievalRerankBatch.model_validate(
            self._invoke_structured_output_with_fallback(
                prompt=prompt,
                prompt_variables={
                    "retrieval_profile": retrieval_profile,
                    "query": query,
                    "candidates": self._json_preview(
                        [
                            {
                                "chunk_id": item.chunk_id,
                                "document_name": item.document_name,
                                "relative_path": item.relative_path,
                                "heading_path": item.heading_path,
                                "snippet": item.snippet,
                                "fused_score": item.fused_score,
                            }
                            for item in top_candidates
                        ],
                        max_chars=6000,
                    ),
                },
                response_model=RetrievalRerankBatch,
                model_config=model_config,
                fallback_result=RetrievalRerankBatch(
                    items=[
                        RetrievalRerankItem(
                            chunk_id=item.chunk_id,
                            relevance_score=item.relevance_score,
                            useful_for_answer=item.useful_for_answer,
                            reason=item.reason,
                        )
                        for item in fallback
                    ]
                ),
            )
        )
        rerank_map = {
            item.chunk_id: item
            for item in structured.items
            if item.chunk_id.strip() != ""
        }
        reranked: list[RetrievalCandidateDebug] = []
        for fallback_item in fallback:
            rerank_item = rerank_map.get(fallback_item.chunk_id)
            if rerank_item is None:
                reranked.append(fallback_item)
                continue
            reranked.append(
                fallback_item.model_copy(
                    update={
                        "relevance_score": round(max(0.0, min(1.0, rerank_item.relevance_score)), 4),
                        "useful_for_answer": bool(rerank_item.useful_for_answer),
                        "reason": rerank_item.reason.strip() or fallback_item.reason,
                    }
                )
            )
        reranked.sort(key=lambda item: (item.relevance_score, item.fused_score), reverse=True)
        return reranked

    def _format_evidence_cards(self, evidence_cards: list[RetrievalCandidateDebug]) -> str:
        lines: list[str] = []
        for index, item in enumerate(evidence_cards, start=1):
            lines.extend(
                [
                    f"[{index}] {item.document_name}",
                    f"标题路径：{item.heading_path or item.tree_path or '/'}",
                    f"文件路径：{item.relative_path or item.document_name}",
                    f"命中原因：{item.reason or 'Hybrid 检索命中'}",
                    f"片段：{item.snippet}",
                    "",
                ]
            )
        return "\n".join(lines).strip()

    def summarize_retrieval(
        self,
        *,
        query: str,
        citations: list[Citation],
        retrieval_context: str,
        evidence_cards: list[RetrievalCandidateDebug] | None = None,
        model_config: ModelConfig,
        system_prompt: str | None = None,
    ) -> str:
        normalized_cards = evidence_cards or []
        if len(citations) == 0 and len(normalized_cards) == 0:
            return "未检索到足够相关的证据片段。"
        formatted_context = (
            self._format_evidence_cards(normalized_cards)
            if len(normalized_cards) > 0
            else retrieval_context
        )
        result = self.generate_response(
            query=query,
            messages=[],
            tool_outputs={},
            citations=citations,
            retrieval_context=formatted_context,
            model_config=model_config,
            system_prompt=system_prompt
            or "你是检索工作台助手。请只根据已筛选的证据卡片做总结，明确说明依据来自哪些文档。"
               "如果证据不足，要直接说明限制，不要编造答案。",
        )
        return result.answer

    def _invoke_structured_output_with_fallback(
        self,
        *,
        prompt: ChatPromptTemplate,
        prompt_variables: dict[str, Any],
        response_model: type[BaseModel],
        model_config: ModelConfig,
        fallback_result: BaseModel,
    ) -> BaseModel:
        """统一执行结构化输出，并在 learning mode / 调用失败时回退。

        支持问题 Agent 的多子 agent 都走这条辅助逻辑，避免每个方法都重复：
        - 解析 provider
        - learning mode 兜底
        - provider 不可用兜底
        - 结构化输出失败兜底
        """

        normalized_config, provider = self.ensure_model_config_runnable(model_config)
        if normalized_config.mode == "learning" or not self._provider_available(provider.protocol):
            return fallback_result

        try:
            model = init_chat_model(**self._build_model_kwargs(provider, normalized_config))
            prompt_value = prompt.invoke(prompt_variables)
            result = model.with_structured_output(response_model).invoke(prompt_value.to_messages())
            return response_model.model_validate(result)
        except Exception:
            return fallback_result

    def classify_support_issue(
        self,
        *,
        question: str,
        composed_query: str,
        module_value: str,
        fallback_category: str,
        similar_case_context: str,
        model_config: ModelConfig,
    ) -> SupportIssueClassificationResult:
        """支持问题分类子 agent。

        这里允许模型在固定类别集合内给出更贴近上下文的判断，
        但如果结果异常、为空或超出允许集合，就严格回退到已有规则分类。
        """

        normalized_query = composed_query.strip() or question.strip()
        allowed_categories = {"SQL排查", "配置排查", "环境差异", "需升级人工", "FAQ"}
        fallback = SupportIssueClassificationResult(
            category=fallback_category.strip() or "FAQ",
            composed_query=normalized_query,
            reasoning="已回退到内置规则分类结果。",
            supervisor_notes="优先保持现有分类语义稳定。",
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是支持问题 Row Graph 的分类子 agent。"
                    "你只能从以下类别中选择一个：SQL排查、配置排查、环境差异、需升级人工、FAQ。"
                    "请同时返回适合检索的 composed_query，但不要捏造原问题中不存在的事实。",
                ),
                (
                    "human",
                    "模块：{module_value}\n"
                    "原始问题：{question}\n\n"
                    "当前组合查询：\n{composed_query}\n\n"
                    "历史案例参考：\n{similar_case_context}",
                ),
            ]
        )
        structured = SupportIssueClassificationResult.model_validate(
            self._invoke_structured_output_with_fallback(
                prompt=prompt,
                prompt_variables={
                    "module_value": module_value or "未填写",
                    "question": question or "未填写",
                    "composed_query": normalized_query or "未填写",
                    "similar_case_context": similar_case_context or "无",
                },
                response_model=SupportIssueClassificationResult,
                model_config=model_config,
                fallback_result=fallback,
            )
        )
        category = structured.category.strip()
        if category not in allowed_categories:
            return fallback
        return structured.model_copy(
            update={
                "category": category,
                "composed_query": structured.composed_query.strip() or normalized_query,
                "reasoning": structured.reasoning.strip() or fallback.reasoning,
                "supervisor_notes": structured.supervisor_notes.strip() or fallback.supervisor_notes,
            }
        )

    def draft_support_solution(
        self,
        *,
        question: str,
        category: str,
        retrieval_summary: str,
        retrieval_hit_count: int,
        similar_case_context: str,
        similar_case_count: int,
        model_config: ModelConfig,
    ) -> SupportIssueDraftResult:
        """支持问题草稿子 agent。

        真实模型可把检索总结整理成更像“支持答复”的格式；
        但默认仍保留原有的“直接采用 retrieval summary”语义作为兜底。
        """

        fallback = SupportIssueDraftResult(
            solution=retrieval_summary.strip(),
            reasoning="已直接采用检索总结作为草稿答案。",
            used_similar_case_count=similar_case_count,
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是支持问题 Row Graph 的草稿子 agent。"
                    "请根据检索总结整理成可直接回写到飞书表的中文答复。"
                    "要求：保留步骤化结构，不要编造检索结果中没有的信息。",
                ),
                (
                    "human",
                    "问题：{question}\n"
                    "分类：{category}\n"
                    "命中知识数：{retrieval_hit_count}\n\n"
                    "检索总结：\n{retrieval_summary}\n\n"
                    "历史案例参考：\n{similar_case_context}",
                ),
            ]
        )
        structured = SupportIssueDraftResult.model_validate(
            self._invoke_structured_output_with_fallback(
                prompt=prompt,
                prompt_variables={
                    "question": question or "未填写",
                    "category": category or "FAQ",
                    "retrieval_hit_count": retrieval_hit_count,
                    "retrieval_summary": retrieval_summary or "无",
                    "similar_case_context": similar_case_context or "无",
                },
                response_model=SupportIssueDraftResult,
                model_config=model_config,
                fallback_result=fallback,
            )
        )
        return structured.model_copy(
            update={
                "solution": structured.solution.strip() or fallback.solution,
                "reasoning": structured.reasoning.strip() or fallback.reasoning,
                "used_similar_case_count": (
                    structured.used_similar_case_count
                    if structured.used_similar_case_count >= 0
                    else similar_case_count
                ),
            }
        )

    def review_support_solution(
        self,
        *,
        question: str,
        category: str,
        draft_solution: str,
        retrieval_hit_count: int,
        evidence_summary: str,
        fallback_judge_status: str,
        fallback_confidence_score: float,
        fallback_reason: str,
        model_config: ModelConfig,
    ) -> SupportIssueReviewResult:
        """支持问题复核子 agent。

        这里使用“安全合并”策略：
        - 现有规则判断仍然是保底基线；
        - 模型只能把结果进一步降级为 `manual_review`，不能把原本应转人工的记录强行提升为 `pass`。
        """

        normalized_fallback_status = "pass" if fallback_judge_status == "pass" else "manual_review"
        fallback = SupportIssueReviewResult(
            judge_status=normalized_fallback_status,
            confidence_score=max(0.0, min(1.0, fallback_confidence_score)),
            judge_reason=fallback_reason.strip() or "已回退到内置复核结果。",
            progress_value="AI分析完成" if normalized_fallback_status == "pass" else "待人工确认",
            reviewer_notes="优先保留既有复核语义。",
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是支持问题 Row Graph 的复核子 agent。"
                    "请判断当前草稿是否足以直接交付，或应转人工确认。"
                    "judge_status 只能是 pass 或 manual_review，confidence_score 取 0 到 1。",
                ),
                (
                    "human",
                    "问题：{question}\n"
                    "分类：{category}\n"
                    "命中知识数：{retrieval_hit_count}\n\n"
                    "证据总结：\n{evidence_summary}\n\n"
                    "草稿答案：\n{draft_solution}\n\n"
                    "当前规则基线：status={fallback_judge_status}, confidence={fallback_confidence_score}, reason={fallback_reason}",
                ),
            ]
        )
        structured = SupportIssueReviewResult.model_validate(
            self._invoke_structured_output_with_fallback(
                prompt=prompt,
                prompt_variables={
                    "question": question or "未填写",
                    "category": category or "FAQ",
                    "retrieval_hit_count": retrieval_hit_count,
                    "evidence_summary": evidence_summary or "无",
                    "draft_solution": draft_solution or "无",
                    "fallback_judge_status": normalized_fallback_status,
                    "fallback_confidence_score": round(max(0.0, min(1.0, fallback_confidence_score)), 4),
                    "fallback_reason": fallback.judge_reason,
                },
                response_model=SupportIssueReviewResult,
                model_config=model_config,
                fallback_result=fallback,
            )
        )
        candidate_status = "pass" if structured.judge_status == "pass" else "manual_review"
        candidate_confidence = max(0.0, min(1.0, structured.confidence_score))
        candidate_reason = structured.judge_reason.strip() or fallback.judge_reason

        if normalized_fallback_status != "pass":
            final_status = "manual_review"
            final_confidence = min(fallback.confidence_score, candidate_confidence)
            final_reason = candidate_reason if candidate_status == "manual_review" else fallback.judge_reason
        elif candidate_status != "pass":
            final_status = "manual_review"
            final_confidence = min(fallback.confidence_score, candidate_confidence)
            final_reason = candidate_reason
        else:
            final_status = "pass"
            final_confidence = max(fallback.confidence_score, candidate_confidence)
            final_reason = candidate_reason or fallback.judge_reason

        return structured.model_copy(
            update={
                "judge_status": final_status,
                "confidence_score": round(max(0.0, min(1.0, final_confidence)), 4),
                "judge_reason": final_reason,
                "progress_value": "AI分析完成" if final_status == "pass" else "待人工确认",
                "reviewer_notes": structured.reviewer_notes.strip() or fallback.reviewer_notes,
            }
        )

    def _json_preview(self, payload: Any, *, max_chars: int = 12000) -> str:
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except Exception:
            text = str(payload)
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n...<truncated>"

    def _pick_first_string(self, source: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = source.get(key)
            if value is None:
                continue
            if isinstance(value, (str, int, float)):
                text = str(value).strip()
                if text != "":
                    return text
        return ""

    def _field_cell_text(self, cell: dict[str, Any]) -> str:
        """从 PM 表格单元格里提取最适合展示的文本。

        这类接口不是直接返回：
        - `bug_id`
        - `title`
        - `status`

        而是返回一行由多个 `fieldCode + title/value` 组成的单元格数组。
        所以这里先把每个单元格归一成字符串，后面才能稳定映射成 `ParsedBug`。
        """

        title = cell.get("title")
        if isinstance(title, str) and title.strip() != "":
            return title.strip()

        value = cell.get("value")
        if isinstance(value, (str, int, float)):
            text = str(value).strip()
            if text != "":
                return text

        if isinstance(value, dict):
            for key in ("title", "name", "userName", "label", "code", "aid"):
                nested = value.get(key)
                if isinstance(nested, (str, int, float)) and str(nested).strip() != "":
                    return str(nested).strip()

        return ""

    def _split_service_module(self, text: str) -> tuple[str, str]:
        normalized = text.strip()
        if normalized == "":
            return "", ""
        if "/" not in normalized:
            return normalized, ""
        service, module = normalized.split("/", 1)
        return service.strip(), module.strip()

    def _extract_bug_from_field_row(self, row: list[Any]) -> ParsedBug | None:
        """解析 `records` 里的“单行数组”结构。

        你给的 PM 接口返回：
        `data.page.records = [ [cell, cell, ...], [cell, cell, ...] ]`

        每个 `cell` 都带有 `fieldCode`，例如：
        - `code`
        - `title`
        - `categoryId`
        - `status`
        - `aid`

        这里把它映射成系统内部统一的 `ParsedBug`，这样后续“增量检测、规则分配、发邮件”
        都不需要关心原始 JSON 长什么样。
        """

        field_map: dict[str, dict[str, Any]] = {}
        for cell in row:
            if not isinstance(cell, dict):
                continue
            field_code = cell.get("fieldCode")
            if isinstance(field_code, str) and field_code.strip() != "":
                field_map[field_code] = cell

        if not field_map:
            return None

        bug_code = self._field_cell_text(field_map.get("code", {}))
        bug_aid = self._field_cell_text(field_map.get("aid", {}))
        bug_id = bug_code or bug_aid
        if bug_id == "":
            return None
        jira_issue_id = (
            self._field_cell_text(field_map.get("id", {}))
            or self._field_cell_text(field_map.get("issueId", {}))
            or self._pick_first_string(
                {k: v for k, v in field_map.items()},
                ("id", "issueId", "issue_id"),
            )
        )

        title = self._field_cell_text(field_map.get("title", {}))
        service_module_text = (
            self._field_cell_text(field_map.get("categoryId", {}))
            or self._field_cell_text(field_map.get("service", {}))
            or self._field_cell_text(field_map.get("module", {}))
            or self._field_cell_text(field_map.get("productId", {}))
        )
        service, module = self._split_service_module(service_module_text)
        customer_issue_type = (
            self._field_cell_text(field_map.get("customerIssueType", {}))
            or self._field_cell_text(field_map.get("customer_problem_type", {}))
            or self._field_cell_text(field_map.get("customerProblemType", {}))
            or self._field_cell_text(field_map.get("problemType", {}))
        )
        status = self._field_cell_text(field_map.get("status", {}))
        assignee = self._field_cell_text(field_map.get("assignee", {}))
        reporter = self._field_cell_text(field_map.get("reporter", {}))
        priority = self._field_cell_text(field_map.get("priority", {}))
        created_at = self._field_cell_text(field_map.get("ctime", {}))

        if title == "" and service == "" and module == "" and status == "" and customer_issue_type == "":
            return None

        excerpt_payload = {
            "code": bug_code,
            "aid": bug_aid,
            "jira_issue_id": jira_issue_id,
            "title": title,
            "category": service_module_text,
            "customer_issue_type": customer_issue_type,
            "status": status,
            "assignee": self._field_cell_text(field_map.get("assignee", {})),
            "reporter": self._field_cell_text(field_map.get("reporter", {})),
        }
        return ParsedBug(
            bug_id=bug_id,
            bug_aid=bug_aid,
            jira_issue_id=jira_issue_id,
            title=title,
            service=service,
            module=module,
            category=service_module_text,
            customer_issue_type=customer_issue_type,
            status=status,
            assignee=assignee,
            reporter=reporter,
            priority=priority,
            created_at=created_at,
            raw_excerpt=self._json_preview(excerpt_payload, max_chars=600),
        )

    def _extract_bug_rows_from_payload(self, payload: Any) -> list[ParsedBug]:
        """专门适配 PM 表格型 JSON。

        优先走这个分支，可以让巡检 Agent 在 learning mode 下也稳定跑通，
        不必完全依赖 LLM 去“猜”一行表格数组到底代表什么业务对象。
        """

        results: list[ParsedBug] = []
        seen: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, list):
                if node and all(isinstance(item, list) for item in node):
                    for row in node:
                        bug = self._extract_bug_from_field_row(row)
                        if bug is not None and bug.bug_id not in seen:
                            results.append(bug)
                            seen.add(bug.bug_id)
                for item in node:
                    walk(item)
                return

            if not isinstance(node, dict):
                return

            for value in node.values():
                walk(value)

        walk(payload)
        return results

    def _looks_like_bug_item(self, item: dict[str, Any]) -> bool:
        bug_id = self._pick_first_string(
            item,
            ("bug_id", "key", "id", "issue_id", "ticket_id", "work_item_id", "defect_id"),
        )
        if bug_id == "":
            return False
        supporting_score = sum(
            1
            for text in (
                self._pick_first_string(item, ("title", "summary", "subject", "name", "bug_title")),
                self._pick_first_string(item, ("service", "service_name", "app", "application", "system", "domain")),
                self._pick_first_string(item, ("module", "feature", "component", "area", "node", "function")),
                self._pick_first_string(item, ("status", "state", "bug_status", "workflow_status")),
            )
            if text != ""
        )
        return supporting_score >= 1

    def _extract_jira_issue_table_rows(self, payload: Any) -> list[ParsedBug]:
        issue_table = payload.get("issueTable") if isinstance(payload, dict) and isinstance(payload.get("issueTable"), dict) else payload
        if not isinstance(issue_table, dict):
            return []

        table = issue_table.get("table")
        if not isinstance(table, list):
            return []

        results: list[ParsedBug] = []
        seen: set[str] = set()
        for index, row in enumerate(table):
            if not isinstance(row, dict):
                continue
            bug_id = self._pick_first_string(row, ("key", "issuekey", "bug_id", "id"))
            if bug_id == "":
                issue_keys = issue_table.get("issueKeys")
                if isinstance(issue_keys, list) and index < len(issue_keys):
                    candidate = issue_keys[index]
                    if isinstance(candidate, (str, int)) and str(candidate).strip() != "":
                        bug_id = str(candidate).strip()
            if bug_id == "":
                continue

            jira_issue_id = self._pick_first_string(row, ("id", "issueId", "issue_id", "jira_issue_id"))
            if jira_issue_id == "":
                issue_ids = issue_table.get("issueIds")
                if isinstance(issue_ids, list) and index < len(issue_ids):
                    candidate = issue_ids[index]
                    if isinstance(candidate, (str, int)) and str(candidate).strip() != "":
                        jira_issue_id = str(candidate).strip()

            title = self._pick_first_string(row, ("summary", "title", "subject", "name"))
            status = self._pick_first_string(row, ("status", "state", "bug_status", "workflow_status"))
            if title == "" and status == "":
                continue

            bug = ParsedBug(
                bug_id=bug_id,
                jira_issue_id=jira_issue_id,
                title=title,
                status=status,
                raw_excerpt=self._json_preview(row, max_chars=600),
            )
            if bug.bug_id not in seen:
                seen.add(bug.bug_id)
                results.append(bug)
        return results

    def _heuristic_extract_bugs(self, payload: Any) -> list[ParsedBug]:
        """本地启发式抽取。

        Learning Mode 下仍然希望巡检链路可学习，因此这里提供一个无外部依赖的
        JSON 遍历器：只要原始接口里存在较稳定的 id/title/service/module/status
        字段组合，就能先把新增 Bug 检测链路跑起来。
        """

        results: list[ParsedBug] = []
        seen: set[str] = set()

        table_results = self._extract_jira_issue_table_rows(payload) + self._extract_bug_rows_from_payload(payload)
        for bug in table_results:
            if bug.bug_id not in seen:
                results.append(bug)
                seen.add(bug.bug_id)

        def walk(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    walk(item)
                return
            if not isinstance(node, dict):
                return

            if self._looks_like_bug_item(node):
                bug = ParsedBug(
                    bug_id=self._pick_first_string(
                        node,
                        ("bug_id", "key", "id", "issue_id", "ticket_id", "work_item_id", "defect_id"),
                    ),
                    bug_aid=self._pick_first_string(node, ("aid",)),
                    jira_issue_id=self._pick_first_string(node, ("issueId", "issue_id", "jira_issue_id", "id")),
                    title=self._pick_first_string(node, ("title", "summary", "subject", "name", "bug_title")),
                    service=self._pick_first_string(
                        node,
                        ("service", "service_name", "app", "application", "system", "domain"),
                    ),
                    module=self._pick_first_string(node, ("module", "feature", "component", "area", "node", "function")),
                    customer_issue_type=self._pick_first_string(
                        node,
                        ("customer_issue_type", "customerIssueType", "customer_problem_type", "customerProblemType"),
                    ),
                    status=self._pick_first_string(node, ("status", "state", "bug_status", "workflow_status")),
                    raw_excerpt=self._json_preview(node, max_chars=600),
                )
                if bug.bug_id not in seen:
                    results.append(bug)
                    seen.add(bug.bug_id)

            for value in node.values():
                walk(value)

        walk(payload)
        return results

    def extract_bug_list(self, *, dashboard_payload: Any, model_config: ModelConfig) -> list[ParsedBug]:
        """把面板 JSON 抽取成标准化 Bug 列表。

        这一步故意做成独立方法，是为了把巡检 Agent 的“结构化理解”从普通聊天里拆出来。
        你可以对比：
        - Chat/RAG 侧更关注回答；
        - Watcher 侧更关注把原始 JSON 归一成稳定业务对象。
        """

        table_results = self._extract_jira_issue_table_rows(dashboard_payload) + self._extract_bug_rows_from_payload(dashboard_payload)
        if len(table_results) > 0:
            deduped: list[ParsedBug] = []
            seen: set[str] = set()
            for item in table_results:
                if item.bug_id in seen:
                    continue
                seen.add(item.bug_id)
                deduped.append(item)
            return deduped

        normalized_config, provider = self.ensure_model_config_runnable(model_config)
        fallback = self._heuristic_extract_bugs(dashboard_payload)
        if normalized_config.mode == "learning" or not self._provider_available(provider.protocol):
            return fallback

        try:
            model = init_chat_model(**self._build_model_kwargs(provider, normalized_config))
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "你是 PM Bug 面板解析助手。"
                        "请从原始 JSON 中提取 bug 列表，并统一输出 bugs 字段。"
                        "必须只保留能识别出稳定 bug_id 的项目；"
                        "service/module/status 没有就填空字符串；"
                        "raw_excerpt 只保留能帮助定位该 bug 的短片段。",
                    ),
                    (
                        "human",
                        "请解析下面的 JSON，输出结构化 bug 列表。\n\n{dashboard_payload}",
                    ),
                ]
            )
            prompt_value = prompt.invoke({"dashboard_payload": self._json_preview(dashboard_payload)})
            structured = model.with_structured_output(ParsedBugBatch).invoke(prompt_value.to_messages())
            normalized: list[ParsedBug] = []
            seen: set[str] = set()
            for item in structured.bugs:
                if item.bug_id.strip() == "" or item.bug_id in seen:
                    continue
                normalized.append(item)
                seen.add(item.bug_id)
            return normalized or fallback
        except Exception:
            return fallback

    def preview_bug_list_from_payload(self, dashboard_payload: Any) -> list[ParsedBug]:
        """接口检查用的本地解析预览。"""

        return self._heuristic_extract_bugs(dashboard_payload)

    def _fallback_owner_suggestion(self, bug: ParsedBug, owner_rules: list[OwnerRule]) -> WatcherOwnerSuggestion:
        combined_text = " ".join(
            [bug.title, bug.service, bug.module, bug.customer_issue_type, bug.status, bug.raw_excerpt]
        ).lower()
        best_rule: OwnerRule | None = None
        best_score = 0
        best_terms: list[str] = []

        for rule in owner_rules:
            matched_terms: list[str] = []
            for term in rule.services + rule.modules + rule.keywords + rule.customer_issue_types:
                normalized = term.strip().lower()
                if normalized == "":
                    continue
                if normalized in combined_text:
                    matched_terms.append(term)
            if len(matched_terms) > best_score:
                best_rule = rule
                best_score = len(matched_terms)
                best_terms = matched_terms

        if best_rule is None or best_score == 0:
            return WatcherOwnerSuggestion(matched=False, match_source="unmatched", reason="未找到明显的语义重叠。")

        return WatcherOwnerSuggestion(
            matched=True,
            assignee_code=best_rule.assignee_code,
            match_source="llm",
            reason="本地兜底根据词元重叠匹配到：" + "、".join(best_terms[:5]),
        )

    def suggest_bug_owner(
        self,
        *,
        bug: ParsedBug,
        owner_rules: list[OwnerRule],
        model_config: ModelConfig,
    ) -> WatcherOwnerSuggestion:
        """在规则未命中时，用模型做负责人归属兜底。"""

        valid_rules = [rule for rule in owner_rules if rule.assignee_code.strip() != ""]
        if len(valid_rules) == 0:
            return WatcherOwnerSuggestion(matched=False, match_source="unmatched", reason="没有可用的转派目标规则。")

        normalized_config, provider = self.ensure_model_config_runnable(model_config)
        fallback = self._fallback_owner_suggestion(bug, valid_rules)
        if normalized_config.mode == "learning" or not self._provider_available(provider.protocol):
            return fallback

        try:
            model = init_chat_model(**self._build_model_kwargs(provider, normalized_config))
            prompt = ChatPromptTemplate.from_messages(
                [
                    (
                        "system",
                        "你是 Bug 转派归属助手。"
                        "请根据 bug 的 service/module/title/raw_excerpt，在候选规则中选出最可能的一条。"
                        "若命中，返回 matched=true 且只填写 assignee_code（这里表示最终转派目标）；"
                        "只有当你有足够依据时才 matched=true；否则返回 unmatched。",
                    ),
                    (
                        "human",
                        "Bug 信息：\n{bug}\n\n候选转派规则：\n{owner_rules}",
                    ),
                ]
            )
            prompt_value = prompt.invoke(
                {
                    "bug": self._json_preview(bug.model_dump(mode="json"), max_chars=2000),
                    "owner_rules": self._json_preview(
                        [
                            {
                "assignee_code": rule.assignee_code,
                "customer_issue_types": rule.customer_issue_types,
                "services": rule.services,
                "modules": rule.modules,
                "keywords": rule.keywords,
                            }
                            for rule in valid_rules
                        ],
                        max_chars=4000,
                    ),
                }
            )
            suggestion = model.with_structured_output(WatcherOwnerSuggestion).invoke(prompt_value.to_messages())
            normalized = WatcherOwnerSuggestion.model_validate(suggestion)
            if not normalized.matched or not normalized.assignee_code:
                return fallback
            return normalized.model_copy(update={"match_source": "llm"})
        except Exception:
            return fallback

    def compose_watcher_email_summary(
        self,
        *,
        watcher_name: str,
        dashboard_url: str,
        started_at: datetime,
        assignment_results: list[WatcherAssignmentResult],
        snapshot_bugs: list[ParsedBug] | None = None,
        new_bug_count: int = 0,
        assign_current_list: bool = False,
    ) -> str:
        """生成巡检邮件正文。

        邮件正文保持纯文本，方便你直接查看，也方便未来接入任意 SMTP/邮件网关。
        """

        normalized_snapshot_bugs = snapshot_bugs or []
        lines = [
            f"巡检 Agent：{watcher_name}",
            f"运行时间：{self._format_display_datetime(started_at)}",
            f"面板地址：{dashboard_url}",
            "",
        ]
        if normalized_snapshot_bugs:
            lines.extend(
                [
                    "【当前列表快照】",
                    f"- 当前面板 Bug 数：{len(normalized_snapshot_bugs)}",
                    "",
                ]
            )
            for index, item in enumerate(normalized_snapshot_bugs, start=1):
                lines.extend(
                    [
                        f"{index}. [{item.bug_id}] {item.title}",
                        f"   辅助 ID：{item.jira_issue_id or item.bug_aid or '-'}",
                        f"   服务模块：{item.service or '-'} / {item.module or '-'}",
                        f"   状态优先级：{item.status or '-'} / {item.priority or '-'}",
                        f"   经办人：{item.assignee or '-'}",
                        f"   创建时间：{item.created_at or '-'}",
                        "",
                    ]
                )

        lines.extend(
            [
                "【当前列表分配】" if assign_current_list else "【新增与分配】",
                (
                    f"- 当前列表参与分配：{len(assignment_results)} 条"
                    if assign_current_list
                    else f"- 本轮新增 Bug 数：{new_bug_count or len(assignment_results)}"
                ),
                (
                    f"- 本轮新增 Bug 数：{new_bug_count}"
                    if assign_current_list
                    else ""
                ),
                "",
            ]
        )
        for index, item in enumerate(assignment_results, start=1):
            lines.extend(
                [
                    f"{index}. [{item.bug_id}] {item.title}",
                    f"   辅助 ID：{item.jira_issue_id or item.bug_aid or '-'}",
                    f"   服务模块：{item.service or '-'} / {item.module or '-'}",
                        f"   状态：{item.status or '-'}",
                        f"   转派目标：{item.assignee_code or '未匹配'}",
                        f"   匹配来源：{item.match_source}",
                        f"   匹配原因：{item.match_reason or '-'}",
                        f"   分配结果：{item.assignment_status} {item.assignment_message or ''}".rstrip(),
                    "",
                ]
            )
        if not assignment_results and assign_current_list:
            lines.append("当前列表没有命中可转派规则的 Bug。")
        elif not assignment_results and normalized_snapshot_bugs:
            lines.append("本次邮件用于确认立即执行链路已跑通；当前没有新增 Bug 需要分配。")
        return "\n".join(lines).strip()

    def compose_watcher_email_html(
        self,
        *,
        watcher_name: str,
        dashboard_url: str,
        started_at: datetime,
        assignment_results: list[WatcherAssignmentResult],
        snapshot_bugs: list[ParsedBug] | None = None,
        new_bug_count: int = 0,
        assign_current_list: bool = False,
    ) -> str:
        """生成更适合邮件客户端阅读的 HTML 版本。"""

        normalized_snapshot_bugs = snapshot_bugs or []
        header_html = f"""
        <div style="padding:24px;background:#0f172a;color:#e2e8f0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
          <div style="max-width:960px;margin:0 auto;">
            <div style="background:#111827;border:1px solid #334155;border-radius:16px;padding:20px;">
              <div style="font-size:14px;color:#94a3b8;">巡检 Agent 通知</div>
              <h2 style="margin:8px 0 0;font-size:24px;color:#f8fafc;">{html.escape(watcher_name)}</h2>
              <div style="margin-top:16px;font-size:14px;line-height:1.8;color:#cbd5e1;">
                <div><strong>运行时间：</strong>{html.escape(self._format_display_datetime(started_at))}</div>
                <div><strong>面板地址：</strong><a href="{html.escape(dashboard_url)}" style="color:#7dd3fc;">{html.escape(dashboard_url)}</a></div>
              </div>
              <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:16px;">
                <div style="background:#1e293b;border-radius:999px;padding:6px 12px;font-size:13px;">当前列表 {len(normalized_snapshot_bugs)} 条</div>
                <div style="background:#1d4ed8;border-radius:999px;padding:6px 12px;font-size:13px;">{('当前列表分配 ' + str(len(assignment_results)) + ' 条') if assign_current_list else ('新增 ' + str(new_bug_count or len(assignment_results)) + ' 条')}</div>
              </div>
            </div>
        """

        snapshot_html = ""
        if normalized_snapshot_bugs:
            rows: list[str] = []
            for item in normalized_snapshot_bugs:
                rows.append(
                    "<tr>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.bug_id)}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.jira_issue_id or item.bug_aid or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.title or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.service or '-')} / {html.escape(item.module or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.status or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.assignee or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.created_at or '-')}</td>"
                    "</tr>"
                )
            snapshot_html = (
                "<div style='margin-top:16px;background:#111827;border:1px solid #334155;border-radius:16px;padding:20px;'>"
                "<h3 style='margin:0 0 12px;font-size:18px;color:#f8fafc;'>当前列表快照</h3>"
                "<table style='width:100%;border-collapse:collapse;font-size:13px;color:#e2e8f0;'>"
                "<thead><tr style='background:#0f172a;'>"
                "<th style='padding:10px 12px;text-align:left;'>Bug 编号</th>"
                "<th style='padding:10px 12px;text-align:left;'>辅助 ID</th>"
                "<th style='padding:10px 12px;text-align:left;'>标题</th>"
                "<th style='padding:10px 12px;text-align:left;'>服务 / 模块</th>"
                "<th style='padding:10px 12px;text-align:left;'>状态</th>"
                "<th style='padding:10px 12px;text-align:left;'>经办人</th>"
                "<th style='padding:10px 12px;text-align:left;'>创建时间</th>"
                "</tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table></div>"
            )

        assignment_html = ""
        if assignment_results:
            rows = []
            for item in assignment_results:
                rows.append(
                    "<tr>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.bug_id)}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.jira_issue_id or item.bug_aid or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.title or '-')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.assignee_code or '未匹配')}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.assignment_status)}</td>"
                    f"<td style='padding:10px 12px;border-bottom:1px solid #1e293b;'>{html.escape(item.match_reason or '-')}</td>"
                    "</tr>"
                )
            assignment_html = (
                "<div style='margin-top:16px;background:#111827;border:1px solid #334155;border-radius:16px;padding:20px;'>"
                f"<h3 style='margin:0 0 12px;font-size:18px;color:#f8fafc;'>{'当前列表分配结果' if assign_current_list else '新增与分配结果'}</h3>"
                "<table style='width:100%;border-collapse:collapse;font-size:13px;color:#e2e8f0;'>"
                "<thead><tr style='background:#0f172a;'>"
                "<th style='padding:10px 12px;text-align:left;'>Bug 编号</th>"
                "<th style='padding:10px 12px;text-align:left;'>辅助 ID</th>"
                "<th style='padding:10px 12px;text-align:left;'>标题</th>"
                "<th style='padding:10px 12px;text-align:left;'>转派目标</th>"
                "<th style='padding:10px 12px;text-align:left;'>分配结果</th>"
                "<th style='padding:10px 12px;text-align:left;'>匹配原因</th>"
                "</tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table></div>"
            )
        elif assign_current_list:
            assignment_html = (
                "<div style='margin-top:16px;background:#111827;border:1px solid #334155;border-radius:16px;padding:20px;color:#cbd5e1;'>"
                "当前列表没有命中可转派规则的 Bug。"
                "</div>"
            )
        elif normalized_snapshot_bugs:
            assignment_html = (
                "<div style='margin-top:16px;background:#111827;border:1px solid #334155;border-radius:16px;padding:20px;color:#cbd5e1;'>"
                "当前没有新增 Bug，本次邮件主要用于确认立即执行链路已跑通。"
                "</div>"
            )

        return header_html + snapshot_html + assignment_html + "</div></div>"

    def _normalize_base_url(self, base_url: str) -> str:
        return base_url.rstrip("/")

    def _is_official_openai_base_url(self, base_url: str) -> bool:
        return self._normalize_base_url(base_url) in OFFICIAL_OPENAI_BASE_URLS

    def _uses_extended_openai_fallback(self, provider: ProviderRuntimeConfig) -> bool:
        return provider.protocol == "openai_compatible" and (
            provider.id == "custom_openai" or not self._is_official_openai_base_url(provider.api_base_url)
        )

    def _candidate_model_urls(self, protocol: ProviderProtocol, base_url: str) -> list[str]:
        """针对不同兼容协议，给出几个常见的模型列表地址候选。

        兼容协议在实际厂商里经常出现两种写法：
        - Base URL 已经带 `/v1`
        - Base URL 只是前缀，需要再补 `/v1/models`
        所以这里会尝试几个安全候选，而不是假定所有厂商都完全一致。
        """

        base = self._normalize_base_url(base_url)
        if protocol == "ollama_native":
            return [base + "/api/tags"]

        candidates = [base + "/models"]
        if not base.endswith("/v1"):
            candidates.append(base + "/v1/models")
        return list(dict.fromkeys(candidates))

    def _candidate_responses_urls(self, base_url: str) -> list[str]:
        base = self._normalize_base_url(base_url)
        candidates = [base + "/responses"]
        if not base.endswith("/v1"):
            candidates.append(base + "/v1/responses")
        return list(dict.fromkeys(candidates))

    def _candidate_completion_attempts(
        self,
        *,
        provider: ProviderRuntimeConfig,
        model_config: ModelConfig,
        prompt_messages: list[BaseMessage],
    ) -> list[tuple[str, str, dict[str, Any]]]:
        chat_payload = self._build_completion_payload(
            provider=provider,
            model_config=model_config,
            prompt_messages=prompt_messages,
        )
        chat_urls = self._candidate_completion_urls(provider.protocol, provider.api_base_url)
        attempts = [("chat-string", url, chat_payload) for url in chat_urls]
        if provider.protocol != "openai_compatible" or not self._uses_extended_openai_fallback(provider):
            return attempts

        block_payload = self._build_openai_chat_block_payload(
            model_config=model_config,
            prompt_messages=prompt_messages,
        )
        attempts.extend(("chat-block", url, block_payload) for url in chat_urls)

        responses_payload = self._build_openai_responses_payload(
            model_config=model_config,
            prompt_messages=prompt_messages,
        )
        attempts.extend(
            ("responses-input_text", url, responses_payload)
            for url in self._candidate_responses_urls(provider.api_base_url)
        )
        return attempts

    def _request_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        req = request.Request(url, headers=headers, method="GET")
        with request.urlopen(req, timeout=8) as response:
            payload = response.read().decode("utf-8")
        parsed = json.loads(payload or "{}")
        return parsed if isinstance(parsed, dict) else {}

    def _extract_models(self, payload: dict[str, Any]) -> list[ProviderModel]:
        raw_items: list[Any] = []
        if isinstance(payload.get("data"), list):
            raw_items = payload["data"]
        elif isinstance(payload.get("models"), list):
            raw_items = payload["models"]

        models: list[ProviderModel] = []
        seen: set[str] = set()
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
            if model_id == "" or model_id in seen:
                continue
            label = str(item.get("display_name") or item.get("name") or item.get("model") or model_id).strip() or model_id
            models.append(ProviderModel(id=model_id, label=label, source="discovered"))
            seen.add(model_id)
        return models

    def _build_test_headers(self, protocol: ProviderProtocol, api_key: str | None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if protocol == "anthropic_compatible":
            headers["anthropic-version"] = "2023-06-01"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["x-api-key"] = api_key
        return headers

    def _merge_models(self, current: list[ProviderModel], discovered: list[ProviderModel]) -> list[ProviderModel]:
        merged: list[ProviderModel] = []
        seen: set[str] = set()
        for item in current + discovered:
            if item.id in seen:
                continue
            merged.append(item)
            seen.add(item.id)
        return merged

    def build_provider_test_runtime(
        self,
        provider_id: str,
        override: UpdateProviderRequest | None = None,
    ) -> ProviderRuntimeConfig:
        current = self.provider_store.get_runtime_provider(provider_id)
        if current is None:
            raise ValueError(f"未找到 provider: {provider_id}")

        override = override or UpdateProviderRequest()
        next_protocol = override.protocol or current.protocol
        if next_protocol not in current.allowed_protocols:
            allowed = ", ".join(current.allowed_protocols)
            raise ValueError(f"{provider_id} 只允许这些协议: {allowed}")

        next_models = override.models if override.models is not None else current.models
        next_api_base_url = (override.api_base_url if override.api_base_url is not None else current.api_base_url).strip()
        next_api_key = (override.api_key if override.api_key is not None else current.api_key or "").strip() or None

        return ProviderRuntimeConfig(
            id=current.id,
            name=current.name,
            enabled=override.enabled if override.enabled is not None else current.enabled,
            protocol=next_protocol,
            allowed_protocols=current.allowed_protocols,
            api_base_url=next_api_base_url,
            api_key=next_api_key,
            models=next_models,
            locked=current.locked,
            created_at=current.created_at,
            updated_at=current.updated_at,
        )

    def test_provider_connection(
        self,
        provider_id: str,
        override: UpdateProviderRequest | None = None,
    ) -> ProviderTestResponse:
        """用当前草稿配置测试连接，并尽量拉回可用模型。"""

        provider = self.build_provider_test_runtime(provider_id, override)
        if provider.protocol == "mock_local":
            return ProviderTestResponse(
                ok=True,
                message="Learning Mode 不需要真实连接，当前会直接使用本地学习模式。",
                available_models=provider.models,
            )
        if provider.api_base_url == "":
            raise ValueError(f"Provider `{provider.name}` 还没有配置 API Base URL。")
        if provider.protocol != "ollama_native" and not provider.api_key:
            raise ValueError(f"Provider `{provider.name}` 还没有配置 API Key。")

        headers = self._build_test_headers(provider.protocol, provider.api_key)
        last_error: str | None = None
        for url in self._candidate_model_urls(provider.protocol, provider.api_base_url):
            try:
                payload = self._request_json(url, headers=headers)
                models = self._extract_models(payload)
                merged = self._merge_models(provider.models, models)
                if len(models) > 0:
                    return ProviderTestResponse(
                        ok=True,
                        message=f"连接成功，已从 `{parse.urlparse(url).path}` 拉取到模型列表。",
                        available_models=merged,
                    )
                return ProviderTestResponse(
                    ok=True,
                    message="连接成功，但对方没有返回可解析的模型列表；你仍然可以手动添加模型。",
                    available_models=provider.models,
                )
            except urlerror.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8")
                except Exception:
                    detail = str(exc)
                last_error = f"{exc.code} {detail}".strip()
            except urlerror.URLError as exc:
                last_error = str(exc.reason)
            except Exception as exc:
                last_error = str(exc)

        return ProviderTestResponse(
            ok=False,
            message=last_error or "测试连接失败，请检查 Base URL、API Key 或协议格式。",
            available_models=provider.models,
        )
