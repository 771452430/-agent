"""内置 Skill 与 Tool。

这里保留三个教学目标：
1. 让你看到 `@tool` / schema 是如何定义结构化工具的；
2. 让现有报销 demo 的能力升级为 Skill；
3. 增加一个知识型 Skill，用于对照 RAG 分支。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..registry import SkillRegistry
from ..schemas import SkillDescriptor
from ..services.knowledge_store import KnowledgeStore
from ..services.yonyou_work_notify_service import YonyouWorkNotifyService


class CalcMoneyInput(BaseModel):
    days: int = Field(description="报销天数", examples=[3])
    daily: float = Field(description="每天金额", examples=[100])


class CalcTaxInput(BaseModel):
    calc_money: float = Field(description="基础金额", examples=[300])


class FormatBreakdownInput(BaseModel):
    calc_money: float = Field(description="基础金额", examples=[300])
    calc_tax: float = Field(description="税额", examples=[30])


class SummarizeNotesInput(BaseModel):
    text: str = Field(description="要总结的文本")


class SearchKnowledgeInput(BaseModel):
    query: str = Field(description="要在知识库里检索的问题")


class SendYonyouWorkNotifyInput(BaseModel):
    openapi_base_url: str = Field(description="租户 OpenAPI 基础域名，例如 https://xxx.diwork.com")
    src_msg_id: str = Field(description="消息唯一标识，用于幂等")
    yht_user_ids: list[str] = Field(description="友互通 userId 列表")
    title: str = Field(description="通知标题")
    content: str = Field(description="通知内容")
    label_code: str | None = Field(default=None, description="领域编码")
    service_code: str | None = Field(default=None, description="服务编码")
    url: str | None = Field(default=None, description="移动端打开地址")
    web_url: str | None = Field(default=None, description="Web 端打开地址")
    mini_program_url: str | None = Field(default=None, description="友空间小程序地址")
    app_id: str | None = Field(default=None, description="应用 appId")
    tab_id: str | None = Field(default=None, description="移动端自定义分类")
    catcode1st: str | None = Field(default=None, description="分类 id")
    attributes: dict[str, Any] | None = Field(default=None, description="自定义扩展属性")
    esn_data: dict[str, Any] | list[dict[str, Any]] | None = Field(
        default=None,
        description="业务属性 JSON，可传对象或对象数组",
    )
    app_key: str | None = Field(default=None, description="应用 appKey；不传时回退到 YONYOU_APP_KEY")
    app_secret: str | None = Field(default=None, description="应用 appSecret；不传时回退到 YONYOU_APP_SECRET")
    auth_base_url: str | None = Field(
        default=None,
        description="鉴权基础域名；不传时回退到 YONYOU_AUTH_BASE_URL 或 openapi_base_url",
    )
    timeout: int = Field(default=30, ge=1, le=120, description="请求超时秒数")


def build_skill_registry(knowledge_store: KnowledgeStore) -> SkillRegistry:
    registry = SkillRegistry()
    yonyou_notify_service = YonyouWorkNotifyService()

    @tool("calc_money", args_schema=CalcMoneyInput)
    def calc_money(days: int, daily: float) -> float:
        """计算报销金额（天数 × 每日金额）。"""

        return round(float(days) * float(daily), 2)

    @tool("calc_tax", args_schema=CalcTaxInput)
    def calc_tax(calc_money: float) -> float:
        """计算 10% 税额。"""

        return round(float(calc_money) * 0.1, 2)

    @tool("format_breakdown", args_schema=FormatBreakdownInput)
    def format_breakdown(calc_money: float, calc_tax: float) -> str:
        """把基础金额、税额和总金额格式化为可读明细。"""

        total = round(float(calc_money) + float(calc_tax), 2)
        return f"基础金额：{calc_money}，税额：{calc_tax}，总计：{total}"

    @tool("summarize_notes", args_schema=SummarizeNotesInput)
    def summarize_notes(text: str) -> str:
        """把文本提炼成简洁总结。"""

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        preview = "；".join(lines[:3]) if lines else text[:80]
        return f"总结：{preview[:180]}"

    @tool("search_knowledge_base", args_schema=SearchKnowledgeInput)
    def search_knowledge_base(query: str) -> str:
        """在知识库中搜索相关片段。"""

        citations = knowledge_store.search(query, limit=3)
        if not citations:
            return "未在知识库中找到命中内容。"
        return "\n".join(f"- {citation.document_name}: {citation.snippet}" for citation in citations)

    @tool("send_yonyou_work_notify", args_schema=SendYonyouWorkNotifyInput)
    def send_yonyou_work_notify(
        openapi_base_url: str,
        src_msg_id: str,
        yht_user_ids: list[str],
        title: str,
        content: str,
        label_code: str | None = None,
        service_code: str | None = None,
        url: str | None = None,
        web_url: str | None = None,
        mini_program_url: str | None = None,
        app_id: str | None = None,
        tab_id: str | None = None,
        catcode1st: str | None = None,
        attributes: dict[str, Any] | None = None,
        esn_data: dict[str, Any] | list[dict[str, Any]] | None = None,
        app_key: str | None = None,
        app_secret: str | None = None,
        auth_base_url: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        """获取 access_token 并发送用友幂等工作通知。"""

        return yonyou_notify_service.send_work_notify(
            openapi_base_url=openapi_base_url,
            src_msg_id=src_msg_id,
            yht_user_ids=yht_user_ids,
            title=title,
            content=content,
            label_code=label_code,
            service_code=service_code,
            url=url,
            web_url=web_url,
            mini_program_url=mini_program_url,
            app_id=app_id,
            tab_id=tab_id,
            catcode1st=catcode1st,
            attributes=attributes,
            esn_data=esn_data,
            app_key=app_key,
            app_secret=app_secret,
            auth_base_url=auth_base_url,
            timeout=timeout,
        )

    registry.register_skill(
        SkillDescriptor(
            id="finance_helper",
            name="Finance Helper",
            description="演示结构化工具调用：金额计算、税额计算、明细格式化。",
            category="tool",
            tools=["calc_money", "calc_tax", "format_breakdown"],
            enabled_by_default=True,
            requires_rag=False,
            learning_focus=["Tool schema", "Tool chaining", "Skill catalog"],
        ),
        tools=[calc_money, calc_tax, format_breakdown],
    )
    registry.register_skill(
        SkillDescriptor(
            id="writing_helper",
            name="Writing Helper",
            description="演示通用处理型 Skill：摘要、提炼与结构化表达。",
            category="core",
            tools=["summarize_notes"],
            enabled_by_default=True,
            requires_rag=False,
            learning_focus=["Tool schema", "Structured inputs", "Prompt-friendly outputs"],
        ),
        tools=[summarize_notes],
    )
    registry.register_skill(
        SkillDescriptor(
            id="knowledge_helper",
            name="Knowledge Helper",
            description="演示知识型 Skill：围绕知识库进行检索与回答。",
            category="knowledge",
            tools=["search_knowledge_base"],
            enabled_by_default=True,
            requires_rag=True,
            learning_focus=["RAG", "Retriever", "Citation"],
        ),
        tools=[search_knowledge_base],
    )
    registry.register_skill(
        SkillDescriptor(
            id="yonyou_work_notify",
            name="Yonyou Work Notify",
            description="获取 access_token、计算签名并发送用友幂等工作通知。",
            category="integration",
            tools=["send_yonyou_work_notify"],
            enabled_by_default=False,
            requires_rag=False,
            learning_focus=["API integration", "HmacSHA256 signing", "Idempotent messaging"],
        ),
        tools=[send_yonyou_work_notify],
    )
    return registry
