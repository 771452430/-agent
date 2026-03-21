"""内置 Skill 与 Tool。

这里保留三个教学目标：
1. 让你看到 `@tool` / schema 是如何定义结构化工具的；
2. 让现有报销 demo 的能力升级为 Skill；
3. 增加一个知识型 Skill，用于对照 RAG 分支。
"""

from __future__ import annotations

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from ..registry import SkillRegistry
from ..schemas import SkillDescriptor
from ..services.knowledge_store import KnowledgeStore


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


def build_skill_registry(knowledge_store: KnowledgeStore) -> SkillRegistry:
    registry = SkillRegistry()

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
    return registry
