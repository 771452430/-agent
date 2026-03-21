# RAG 学习说明

## 当前实现的 RAG 链路

1. 上传文档
2. 解析文本
3. 切分 chunk
4. 存 SQLite
5. 构造可检索索引
6. 查询时执行：
   - query rewrite
   - retrieve
   - format context
7. final response 返回 citations

## 当前为什么默认用“学习模式”

真实向量检索依赖 embedding / vector store 生态，安装和环境要求更高。

为了让你先把流程学明白，当前实现默认满足两点：

- 没有额外模型依赖时也能跑
- 检索结果仍然是可解释、可查看的

所以默认采用：

- SQLite 保存文档与 chunk
- 本地词法匹配做基础检索
- 若安装 Chroma 相关依赖，则会初始化对应 vector backend

## 你下一步可以怎么练

### 练习 1：把 lexical search 换成真实 embedding 检索

重点看：

- `KnowledgeStore._create_vector_store`
- `KnowledgeStore.search`

### 练习 2：把 query rewrite 做得更聪明

重点看：

- `RAGPipeline._rewrite_query`

### 练习 3：把 citations 做成带页码/段落号

重点看：

- `Citation`
- chunk metadata
