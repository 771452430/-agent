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

### 4. `yonyou_work_notify`

Tools:

- `send_yonyou_work_notify`

用途：

- 获取用友自建应用 `access_token`
- 计算 `HmacSHA256` 鉴权签名
- 调用幂等工作通知接口发送消息

入参来源：

- 优先使用工具显式传入的 `app_key` / `app_secret` / `auth_base_url`
- 若未显式传入，则回退到环境变量：
  - `YONYOU_APP_KEY`
  - `YONYOU_APP_SECRET`
  - `YONYOU_AUTH_BASE_URL`

何时启用：

- 需要让 Agent 直接调用用友工作通知接口时启用
- 默认不启用，避免聊天或检索场景误触发外部消息发送

学习点：

- 外部 API 集成
- HMAC 签名
- 幂等消息发送

## 如何新增一个 Skill

1. 写一个或多个 Tool
2. 定义 `SkillDescriptor`
3. 调 `registry.register_skill(...)`
4. 如果需要自动触发，再把逻辑接进 `chat_graph.py`

## 一个重要原则

Skill 不是“为了多包一层而多包一层”，而是为了让项目更容易学、更容易展示、更容易切换能力边界。
