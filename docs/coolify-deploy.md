# Coolify 部署说明

这份说明对应当前仓库的推荐部署方式：

- `backend` 一个 Service
- `frontend` 一个 Service
- `backend/data` 作为统一持久化目录

## 1. 服务拆分

### backend Service

- Repository：当前 Git 仓库
- Dockerfile Path：`backend/Dockerfile`
- Exposed Port：`8000`

建议环境变量：

```bash
JIRA_APP_KEY=your-jira-app-key
JIRA_APP_SECRET=your-jira-app-secret
JIRA_SUPPORT_DB_PATH=/app/data/jira/jira_support.db
JIRA_LEGACY_SKILL_DIR=
APP_CORS_ORIGIN_REGEX=^https?://([a-zA-Z0-9-]+\\.)*your-domain\\.com$
```

说明：

- `JIRA_LEGACY_SKILL_DIR` 在服务器上建议留空，不再依赖本地旧 skill 目录。
- `JIRA_SUPPORT_DB_PATH` 会作为 Jira 历史库默认路径，同时供两个 Jira Agent 读取。

### frontend Service

- Repository：当前 Git 仓库
- Dockerfile Path：`frontend/Dockerfile`
- Exposed Port：`3000`

建议环境变量：

```bash
INTERNAL_API_BASE=http://backend:8000
NEXT_PUBLIC_API_BASE=
```

说明：

- `INTERNAL_API_BASE` 用于 Next.js 构建期和运行期的 API 重写目标。
- 如果你前端和后端都在同一个 Coolify 内网里，保持 `http://backend:8000` 即可。
- `NEXT_PUBLIC_API_BASE` 一般不用填，除非你有明确的浏览器直连后端需求。

## 2. 持久化目录

在 `backend` Service 上挂一个 Persistent Storage：

- 容器挂载路径：`/app/data`

这会统一保住以下内容：

- `/app/data/learning_demo.sqlite3`
- `/app/data/chroma`
- `/app/data/jira/jira_support.db`
- `/app/data/uploads`

不建议只挂 Jira DB。否则知识库索引、应用设置和上传文件在重建后仍然会丢。

## 3. 部署顺序

1. 先更新 `backend` 的环境变量和 Persistent Storage
2. 部署 `backend`
3. 再配置并部署 `frontend`
4. 打开前端页面验证

## 4. 验证清单

### 后端验证

- `GET /health` 返回 `200`
- `GET /api/settings/jira-data-source` 返回正常配置

### 前端验证

- `Jira 工单 Agent` 页面可打开
- `Jira 方案检索 Agent` 页面可打开
- 左下角 `Jira 数据源设置` 可打开

### 数据验证

在 `Jira 数据源设置` 中：

1. 点击 `测试匹配`
2. 应能匹配到 `工作台`
3. 点击 `立即同步`
4. 同步完成后检查：
   - `/app/data/jira/jira_support.db` 已生成或更新
   - Jira 方案检索可以命中历史解决方案

### 持久化验证

同步完成后重建一次 `backend` 容器，确认以下内容仍在：

- `learning_demo.sqlite3`
- `chroma`
- `jira_support.db`

如果这些文件仍在，说明 Coolify 的持久化配置是正确的。
