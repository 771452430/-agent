# 支持问题 Agent RAG Embedding 设置升级记录

## 时间
- 2026-04-03 17:09（Asia/Shanghai）

## 背景与目标
- 当前项目的 RAG embedding 仍然主要依赖环境变量控制，默认实际运行是本地 hashing fallback。
- 目标是把 embedding 升级成“可在页面里配置、保存后立即生效、并且能看见当前到底在用什么 backend”的状态。

## 本次完成事项
- 新增全局 RAG embedding 设置的 SQLite 存储与服务层。
- 新增后端接口：
  - `GET /api/settings/rag-embedding`
  - `PATCH /api/settings/rag-embedding`
- `EmbeddingService` 升级为“双来源配置”：
  - 环境变量优先
  - 未配置时回退到数据库里的全局 RAG embedding 设置
- `KnowledgeStore` 新增索引 backend 元数据记录。
- 保存 embedding 设置后会自动全量重建 Chroma 向量索引。
- 查询阶段新增 backend 一致性保护：
  - 如果查询时因为 provider 不可用回退成 hashing，而索引是另一套 embedding 建出来的，则跳过向量召回，避免错配。
- 前端工作台新增 `RAG Embedding 设置` 面板，可配置 provider、model、timeout，并展示：
  - 配置来源
  - 当前运行模式
  - 期望 backend
  - 当前索引 backend
  - 是否需要重建

## 本次新增能力
- 现在可以不用手工改环境变量，直接在页面配置 RAG embedding。
- 保存设置后，知识库向量索引会自动按当前配置重建。
- 可以直接看到系统现在是走 provider embedding，还是仍然回退到了 hashing。
- 可以识别“配置和索引不一致”的情况，减少检索异常。

## 本次修复与调整
- 补了 RAG embedding 专项回归测试，覆盖：
  - 数据库设置生效
  - 环境变量优先级覆盖数据库设置
- 继续保留本地 hashing fallback，保证 provider 不可用时链路仍能运行。

## 验证结果
- 后端 `py_compile` 通过
- 后端 `unittest` 共 15 项通过
- 前端 `tsc --noEmit` 通过
- 前端 `next build` 通过

## 当前结论
- RAG embedding 已从“只能靠环境变量”升级为“页面可配 + 自动重建索引 + 可观察运行状态”。
- 当前是否真正用上语义 embedding，仍取决于你配置的 provider/model 是否支持 embeddings 接口。

## 后续优化项
- 增加 embedding 连通性测试按钮，直接验证 `/embeddings` 是否可用。
- 增加“推荐 embedding 模型”提示，降低手工填写模型名的成本。
- 评估是否继续升级本地 fallback embedding，让离线模式下的语义召回再提一档。
