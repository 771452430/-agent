"""Skill / Tool 注册中心。

这里刻意把 Skill 和 Tool 分开：
- Tool 是 LangChain 的原子能力；
- Skill 是对一组工具的教学型封装，便于解释“Agent 有哪些能力”。

这样前端 catalog、README、docs 都可以围绕 Skill 展示，而运行时依然调用标准 Tool。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool

from .schemas import SkillDescriptor


@dataclass
class ToolDefinition:
    """一个可被 Agent 或 graph 使用的工具定义。"""

    name: str
    description: str
    skill_id: str
    category: str
    tool: BaseTool
    learning_focus: list[str]


class SkillRegistry:
    """统一管理 Skill 与 Tool 的关系。"""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDescriptor] = {}
        self._tools: dict[str, ToolDefinition] = {}

    def register_skill(self, skill: SkillDescriptor, tools: list[BaseTool]) -> None:
        """注册 Skill 及其所包含的 Tool。"""

        self._skills[skill.id] = skill
        for tool in tools:
            self._tools[tool.name] = ToolDefinition(
                name=tool.name,
                description=tool.description or "",
                skill_id=skill.id,
                category=skill.category,
                tool=tool,
                learning_focus=skill.learning_focus,
            )

    def list_skills(self) -> list[SkillDescriptor]:
        return list(self._skills.values())

    def list_default_skill_ids(self) -> list[str]:
        return [skill.id for skill in self._skills.values() if skill.enabled_by_default]

    def get_skill(self, skill_id: str) -> SkillDescriptor | None:
        return self._skills.get(skill_id)

    def get_tool(self, tool_name: str) -> ToolDefinition | None:
        return self._tools.get(tool_name)

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def is_tool_enabled(self, tool_name: str, enabled_skills: list[str]) -> bool:
        tool_def = self._tools.get(tool_name)
        return bool(tool_def and tool_def.skill_id in enabled_skills)

    def tool_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "skill_id": tool.skill_id,
                "category": tool.category,
                "learning_focus": tool.learning_focus,
            }
            for tool in self._tools.values()
        ]
