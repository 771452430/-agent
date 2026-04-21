# 支持问题 Agent RAG 提准改造记录

## 时间

- 记录时间：2026-04-03 00:43 CST
- 时区：Asia/Shanghai

## 背景与目标

本轮工作的目标是完成“支持问题 Agent RAG 提准改造”的收尾，把检索链路从基础 Hybrid 检索升级为更完整、可验证、可持续优化的 RAG 方案，并补齐验证与评估闭环。

## 本次完成事项

- 修正 RAG pipeline 中 query bundle 的构造逻辑，确保保留原始 query，不会被预处理后的 query 覆盖
- 修正 embedding runtime 的兜底逻辑，支持 `ollama_native` 在无 API Key 的情况下正常回退运行
- 让支持问题图测试适配新的 `retrieval_profile`、`query_bundle_context` 和 retrieval debug 结构
- 新增 RAG 专项测试，覆盖 query bundle、结构化 chunk、support_issue profile 加权和 debug 输出
- 新增离线评估脚本，用于从已审核案例和反馈事实中生成样本并输出核心检索指标

## 本次新增能力

- query bundle / retrieval debug 已进入可验证状态
- support_issue retrieval profile 拥有专项回归覆盖
- 离线评估脚本可输出 `Recall@3`、`Recall@5`、`MRR`、`no-hit accuracy`
- 评估预览结果支持按文档去重，避免同一文档多个 chunk 干扰排名观察

## 本次修复与调整

- 调整 RAG pipeline 的 query 传递方式，保证 `original_query` 语义完整
- 修复 embedding provider 运行时判断，避免把 `ollama_native` 错误拦截为无效配置
- 更新测试桩返回结构，兼容 retrieval debug 新字段
- 优化评估脚本中的文档排名展示，减少重复 chunk 对预览结果的干扰

## 验证结果

- 后端 `python3 -m py_compile` 通过
- 后端 `PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests` 共 13 项测试通过
- 前端 `node node_modules/next/dist/bin/next build` 通过
- 前端 `node node_modules/typescript/bin/tsc --noEmit` 通过
- 离线评估脚本 `backend/.venv/bin/python backend/scripts/evaluate_support_issue_rag.py --preview-limit 2` 可实际运行并输出结果

## 当前结论

- 正样本召回链路已经跑通，query bundle、多路召回、rerank 和 debug 输出已形成闭环
- 当前负样本侧的 `no-hit` 判定仍偏宽，评估结果显示这部分还需要继续收紧

## 后续优化项

- 调整 rerank 阈值，降低弱相关证据误入上下文的概率
- 优化 `no-hit` 判定规则，避免伪命中
- 强化 support_issue 场景的负样本过滤策略
- 持续积累已审核案例与反馈事实，提升离线评估样本质量
