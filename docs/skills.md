# Skill 体系说明

## 什么是 Skill

在这个项目里：

- Tool = 原子能力
- Skill = 一组面向学习/业务理解的能力封装

为什么要多一层 Skill：

- 前端更适合展示 Skill
- README / docs 更适合围绕 Skill 解释
- Agent 是否启用某类能力也更容易表达

## 当前内置 Skill

### 1. `finance_helper`

Tools:

- `calc_money`
- `calc_tax`
- `format_breakdown`

学习点：

- Tool schema
- Tool chaining

### 2. `writing_helper`

Tools:

- `summarize_notes`

学习点：

- 结构化输入
- 面向 Prompt 的输出

### 3. `knowledge_helper`

Tools:

- `search_knowledge_base`

学习点：

- RAG
- Citation
- 知识型能力和普通工具能力的区别

## 如何新增一个 Skill

1. 写一个或多个 Tool
2. 定义 `SkillDescriptor`
3. 调 `registry.register_skill(...)`
4. 如果需要自动触发，再把逻辑接进 `chat_graph.py`

## 一个重要原则

Skill 不是“为了多包一层而多包一层”，而是为了让项目更容易学、更容易展示、更容易切换能力边界。
