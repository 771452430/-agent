# 工作台链路说明

这个文档专门讲“系统是怎么跑起来的”，适合你一边打开代码一边对照看。

## 1. Chat 链路

入口文件：`backend/app/main.py`

1. 前端在 `frontend/components/chat-workspace.tsx` 调 `streamMessage()`。
2. 后端 `POST /api/threads/{thread_id}/messages` 进入 `ChatService.stream_message()`。
3. `ChatService` 先把用户消息写入 `ThreadStore`。
4. 历史消息、模型配置、启用的 skills 被整理成 graph 初始状态。
5. `LearningChatGraph` 开始执行：
   - `inspect_request`
   - `execute_tools` 或 `retrieve_context`
   - `finalize_response`
6. graph 的增量事件被 `ChatService` 转成 SSE。
7. 前端 `applyEvent()` 收到事件后立刻更新 UI。
8. 执行结束后，最终答案与工具轨迹被写回 SQLite。

## 2. 检索模式链路

入口文件：`frontend/components/retrieval-workspace.tsx`

1. 左侧知识树来自 `GET /api/knowledge/tree`。
2. 目录上传时，前端会把每个文件的 `relative_path` 一起发给后端。
3. 如果粘贴 GitLab `/-/tree/<ref>/<path>` 地址，前端会调用 `POST /api/knowledge/tree/import-gitlab`。
4. `GitLabImportService` 会校验 URL、使用 `PRIVATE-TOKEN` 调 GitLab API 拉取目录树和 raw 文件。
5. 无论来自本地目录还是 GitLab，后端最终都会进入 `KnowledgeStore`：
   - 本地目录走 `KnowledgeStore.ingest_directory()`
   - GitLab 文档树走 `KnowledgeStore.upsert_document()`
6. 文档进入 `KnowledgeStore.ingest_document()`：
   - 解析文本
   - 切分 chunk
   - 写入 SQLite
   - 记录 tree metadata
7. 检索时前端调用 `POST /api/retrieval/query`。
8. `ChatService.query_retrieval()` 调用 `RAGPipeline.run()`。
9. `RAGPipeline` 依次完成：
   - query rewrite
   - scoped retrieve
   - context format
10. `LLMService.summarize_retrieval()` 再把命中片段总结成 summary。

## 3. 我的 Agent 链路

入口文件：`frontend/components/agents-workspace.tsx`

1. Agent 配置先保存到 SQLite。
2. 运行 Agent 时，前端调用 `POST /api/agents/{agent_id}/run`。
3. `ChatService.run_agent()` 读取 Agent 配置。
4. 配置被注入 graph 初始状态：
   - `system_prompt`
   - `model_config`
   - `enabled_skills`
   - `knowledge_scope_type`
   - `knowledge_scope_id`
5. 如果 Agent 绑定了知识范围，graph 会优先走 RAG 分支。
6. 最终仍然由同一个 `finalize_response` 节点收口。

## 4. 巡检 Agent 链路

入口文件：`frontend/components/watchers-workspace.tsx`

1. 你在前端创建一个巡检 Agent，保存到 `watcher_agents`。
2. 后端 `WatcherScheduler` 会周期性扫描 `next_run_at` 到期的配置。
3. 手动点击“立即运行”时，会直接进入 `WatcherService.run_watcher()`。
4. `WatcherAgentGraph` 按节点顺序执行：
   - `fetch_dashboard_json`
   - `extract_bug_list`
   - `detect_new_bugs`
   - `match_owner_rules`
   - `llm_assign_fallback`
   - `call_assignment_api`
   - `compose_email`
   - `send_email`
   - `persist_run`
5. `watcher_seen_bugs` 用来判断“这个 bug_id 是不是第一次出现”。
6. `watcher_runs` 记录每次执行的抓取数、解析数、新增数、分配结果和邮件状态。

## 5. 为什么这几条链路可以共用一套核心能力

关键原因是项目把能力拆成了几层：

- `KnowledgeStore`
  负责知识树、文档、chunk、scope 检索。
- `RAGPipeline`
  负责把检索组织成可组合链。
- `LearningChatGraph`
  负责路由、工具、检索、最终输出。
- `WatcherAgentGraph`
  负责定时巡检、增量比较、负责人分配和通知。
- `ChatService`
  负责把不同入口组装起来。
- `WatcherService`
  负责把巡检图与外部副作用（HTTP / 分配接口 / SMTP）接起来。

这样你会看到：

- Chat 不是一个单独系统
- 检索模式也不是一个单独系统
- 我的 Agent 也不是一个单独系统
- 巡检 Agent 也不是一个独立黑盒

它们本质上是在复用同一套底层能力，只是入口和状态不同。
