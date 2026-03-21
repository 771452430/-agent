# LangChain 学习地图

这份文档回答一个问题：**你现在这个项目里，每个 LangChain 概念到底落在什么代码上。**

## 1. Prompt / Messages

- 文件：`backend/app/services/llm_service.py`
- 看点：
  - `ChatPromptTemplate`
  - `MessagesPlaceholder`
  - `HumanMessage` / `AIMessage`

你要学到的是：

- prompt 不一定是大字符串
- 多轮会话更适合 message 模型

## 2. Tool / Structured Tool

- 文件：`backend/app/skills/learning.py`
- 看点：
  - `@tool`
  - `args_schema`

你要学到的是：

- Tool 的描述和参数 schema 会直接影响 Agent 如何理解工具

## 3. Structured Output

- 文件：`backend/app/schemas.py`
- 看点：
  - `FinalResponse`

你要学到的是：

- 最终输出不应该只是自然语言
- 明确 schema 更适合前端展示和后续扩展

## 4. Runnable / LCEL

- 文件：`backend/app/rag/pipeline.py`
- 看点：
  - `RunnableLambda`
  - query rewrite → retrieve → format context

你要学到的是：

- LangChain 不止是 agent
- 很多链路用 runnable 更清晰

## 5. LangGraph

- 文件：`backend/app/graphs/chat_graph.py`
- 看点：
  - `StateGraph`
  - 条件边
  - 节点状态更新

你要学到的是：

- 什么场景需要 graph
- graph 如何把状态流转显式化

## 6. RAG

- 文件：
  - `backend/app/services/knowledge_store.py`
  - `backend/app/rag/pipeline.py`

你要学到的是：

- 文档不是“直接喂给模型”
- 中间有 loader / splitter / retrieve / citation 这条链路

## 7. Streaming

- 文件：`backend/app/services/chat_service.py`
- 看点：
  - SSE event 序列化
  - graph update 到前端事件流的映射

你要学到的是：

- token streaming 和 event streaming 是两种不同层次

## 8. LangSmith

- 文件：
  - `backend/app/rag/pipeline.py`
  - `backend/app/services/llm_service.py`

你要学到的是：

- 如何 trace 一次请求
- 如何观察 retrieval 和 final generation 两段逻辑
