# 架构说明

## 总体分层

- `main.py`
  - FastAPI 路由入口
- `chat_service.py`
  - 业务编排层
- `chat_graph.py`
  - LangGraph 会话图
- `skills/learning.py`
  - Tool / Skill 定义
- `rag/pipeline.py`
  - LCEL 风格 RAG 管道
- `thread_store.py`
  - SQLite 线程状态
- `knowledge_store.py`
  - 文档解析、切分、索引、检索

## 为什么这样拆

这样拆不是为了“看起来高级”，而是为了学习路径更清楚：

1. 先看接口
2. 再看 graph
3. 再看 tool/skill
4. 再看 RAG
5. 最后看前端如何消费事件流

## 关键数据流

1. 用户消息进入 `/api/threads/{thread_id}/messages`
2. `ChatService` 取出 SQLite 历史消息
3. 历史消息 + 用户输入进入 `LearningChatGraph`
4. graph 路由到：
   - `chat`
   - `tool`
   - `rag`
5. 节点结果写回状态
6. `finalize_response` 输出 `FinalResponse`
7. `ChatService` 用 SSE 把事件流推给前端
8. 最终结果和工具轨迹写回 SQLite

## 为什么不用“神秘 memory”

这个项目故意不把会话记忆藏起来，而是显式存在 SQLite：

- thread
- message
- tool_event
- final_output

因为这样最适合学习：你能清楚看到 Agent 的“记忆”其实就是状态与历史记录。
