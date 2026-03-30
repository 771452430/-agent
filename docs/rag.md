# RAG 学习说明

## 当前实现的 RAG 链路

1. 上传文档
2. 解析文本
3. 切分 chunk
4. chunk 和 metadata 存 SQLite
5. chunk 同步写入 Chroma 向量索引
6. 查询时执行：
   - query rewrite
   - lexical retrieve
   - vector retrieve
   - RRF fusion
   - format context
7. final response 返回 citations

## 当前为什么采用 Hybrid RAG

纯 lexical 检索足够稳定，但对“语义相近、词不完全一致”的问题不够友好；
纯向量检索又可能漏掉文件名、路径、关键词这些强信号。

所以这一版默认采用 Hybrid RAG，同时继续满足两点：

- 没有额外模型依赖时也能跑
- 检索结果仍然是可解释、可查看的

当前默认实现是：

- SQLite 保存文档与 chunk
- 本地 `HashingVectorizer` 生成稳定 embedding
- `Chroma` 保存向量
- lexical + vector 双召回后做 RRF 融合

## 你下一步可以怎么练

### 练习 1：把本地 embedding 换成真实 embedding 模型

重点看：

- `EmbeddingService`
- `KnowledgeStore._vector_search`
- `KnowledgeStore._fuse_search_results`

### 练习 2：把 query rewrite 做得更聪明

重点看：

- `RAGPipeline._rewrite_query`

### 练习 3：把 citations 做成带页码/段落号

重点看：

- `Citation`
- chunk metadata
