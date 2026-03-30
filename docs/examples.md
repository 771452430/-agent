# 学习案例

## 案例 1：理解 Tool 轨迹

问题：

```text
报销 3 天 每天 100 含税
```

建议你同时打开：

- `frontend/components/chat-workspace.tsx`
- `backend/app/graphs/chat_graph.py`
- `backend/app/services/llm_service.py`

重点观察：

- 为什么会被路由到 tool
- 每个 Tool 的输入输出是什么
- final response 是怎么把 tool result 收口的

## 案例 2：理解目录上传如何变成知识树

操作：

1. 去检索模式
2. 上传一个带子目录的文件夹
3. 看左侧树结构变化

建议你同时打开：

- `frontend/components/retrieval-workspace.tsx`
- `backend/app/services/knowledge_store.py`

重点观察：

- `relative_path` 是如何传到后端的
- 后端如何逐级创建节点
- 文档最终挂到哪个节点下

## 案例 3：理解 scoped RAG

问题：

```text
请总结这个范围里的重点内容
```

建议你分别测试：

- `global`
- `tree_recursive`

重点观察：

- citation 来自哪些文档
- `tree_path` 有没有变化
- 为什么同一个问题，结果会因为 scope 不同而变化

## 案例 4：理解配置型 Agent

操作：

1. 新建一个 Agent
2. 绑定某个知识树节点
3. 启用 1 到 2 个 Skill
4. 运行问题

建议你同时打开：

- `frontend/components/agents-workspace.tsx`
- `backend/app/services/agent_store.py`
- `backend/app/services/chat_service.py`

重点观察：

- 保存 Agent 和运行 Agent 是两个不同阶段
- Agent 并没有自己的 graph，它复用了同一套 graph
- 配置项是如何影响最终执行结果的
