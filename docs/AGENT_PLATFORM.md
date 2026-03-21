# Agent Platform 说明（已升级）

这个项目已经从旧版“串行 workflow demo”升级为新的学习型平台，当前请优先阅读：

- `/Users/wangyahui/yonyou/AI工具/agentDemo/README.md`
- `/Users/wangyahui/yonyou/AI工具/agentDemo/docs/architecture.md`
- `/Users/wangyahui/yonyou/AI工具/agentDemo/docs/langchain-learning-map.md`
- `/Users/wangyahui/yonyou/AI工具/agentDemo/docs/rag.md`
- `/Users/wangyahui/yonyou/AI工具/agentDemo/docs/skills.md`

## 当前定位

- 面向“边做边学”的 LangChain 项目
- 核心能力：会话式 Agent、RAG、Skill、Streaming、结构化输出
- 技术栈：FastAPI + LangChain + LangGraph + Next.js

## 和旧版最大的区别

- 不再以固定 workflow 数组为核心
- 改为 LangGraph 管理状态与路由
- 增加 Thread State、知识库、Skill Catalog 和 SSE 事件流

如果你之前是按旧版文档理解项目，请以新版 README 和 docs 为准。
