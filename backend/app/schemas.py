"""对外接口与内部状态的结构化模型。

学习型项目里，schema 很重要，因为它把“Agent 到底产出什么”说清楚了。
这里刻意把模型单独放出来，让你之后读 README 或 docs 时能快速对照：
- API 请求/响应长什么样；
- Thread State 里保留哪些关键数据；
- FinalResponse 为什么不是一段随意文本。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelConfig(BaseModel):
    """统一的模型配置。"""

    provider: str = Field(default="mock", description="模型提供商，如 openai / ollama / mock")
    model: str = Field(default="learning-mode", description="模型名称")
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_tokens: int = Field(default=1024, ge=128, le=8192)


class Citation(BaseModel):
    """RAG 命中的引用片段。"""

    document_id: str
    document_name: str
    chunk_id: str
    snippet: str


class FinalResponse(BaseModel):
    """最终返回给前端和用户的结构化输出。"""

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    used_tools: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


class ChatMessage(BaseModel):
    """会话中的单条消息。"""

    id: str
    role: Literal["human", "assistant", "system"]
    content: str
    created_at: datetime


class ToolEvent(BaseModel):
    """工具调用轨迹。"""

    id: str
    tool_name: str
    status: Literal["started", "completed", "failed"]
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime
    ended_at: datetime | None = None
    note: str | None = None


class SkillDescriptor(BaseModel):
    """Skill 的教学型描述信息。"""

    id: str
    name: str
    description: str
    category: Literal["core", "tool", "knowledge"]
    tools: list[str]
    enabled_by_default: bool = True
    requires_rag: bool = False
    learning_focus: list[str] = Field(default_factory=list)


class KnowledgeDocument(BaseModel):
    """知识库文档元信息。"""

    id: str
    name: str
    type: str
    status: Literal["processing", "ready", "error"]
    chunk_count: int = 0
    created_at: datetime
    error_message: str | None = None


class ThreadSummary(BaseModel):
    """左侧会话列表用的摘要。"""

    thread_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_preview: str = ""


class ThreadState(BaseModel):
    """线程详情：会话消息 + 最后一次运行结果。"""

    model_config = ConfigDict(populate_by_name=True)

    thread_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    model_settings: ModelConfig = Field(alias="model_config")
    enabled_skills: list[str]
    messages: list[ChatMessage] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    final_output: FinalResponse | None = None


class CreateThreadRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: str | None = None
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled_skills: list[str] | None = None


class CreateThreadResponse(BaseModel):
    thread_id: str
    title: str


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    content: str = Field(min_length=1)
    model_settings: ModelConfig | None = Field(default=None, alias="model_config")
    enabled_skills: list[str] | None = None


class CatalogResponse(BaseModel):
    models: list[ModelConfig]
    skills: list[SkillDescriptor]
    tools: list[dict[str, Any]]
    learning_focus: list[dict[str, str]]


class UploadDocumentRequest(BaseModel):
    file_name: str
    content_base64: str


class KnowledgeSearchResponse(BaseModel):
    query: str
    citations: list[Citation] = Field(default_factory=list)
    retrieval_context: str = ""
