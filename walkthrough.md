# agentDemo 项目深度解析：从用户输入到 Agent 执行

你好！作为一个刚开始学习前端的小白，理解一个全栈项目（包含前端和后端）的**全链路数据流**是非常关键的。这个 `agentDemo` 是一个典型的 **"前后端分离"** 架构的 AI Agent 平台原型。

下面我们将从用户在界面上“点一下”开始，一步步拆解代码是如何跑起来的。

---

## 1. 核心架构总览

项目分为两大部分：
- **`frontend/` (前端)**: 使用 Vite + React + TypeScript 构建。它是“脸面”，负责收集用户的需求，并发起请求。
- **`backend/` (后端)**: 使用 FastAPI (Python) 构建。它是“大脑”，负责决定怎么处理请求，并执行具体的“技能”（Skills）或“代理”（Agents）。

---

## 2. 全链路执行流程（"点一下"之后发生了什么？）

假设你在界面输入了 **"报销出差费用，含税"** 并点击按钮。

### 第一步：前端捕获输入并准备数据
**位置：** [frontend/src/pages/InputPage.tsx](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/pages/InputPage.tsx)
- 在 [handleRun](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/pages/InputPage.tsx#53-72) 函数中，React 会把你的输入 `userInput` 和一些参数（如 `days`, `daily`）打包成一个 `context` 对象。
- 如果开启了“自动路由”，它会调用 [api/agent.ts](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/api/agent.ts) 里的 [runAuto](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/api/agent.ts#38-46) 函数。

### 第二步：发起 HTTP 请求
**位置：** [frontend/src/api/agent.ts](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/api/agent.ts)
- 通过 `axios` 向后端的 `http://localhost:8000/run_auto` 发送一个 `POST` 请求，把你的文本带过去。

### 第三步：后端接收请求并路由
**位置：** [backend/app/main.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py) -> [run_auto](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py#94-101) 接口
1.  后端接收到文本后，首先交给 [router.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/router.py)。
2.  **路由逻辑 ([router.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/router.py))**: 它会检查你的文本里有没有关键词。比如看到“税”或“出差”，它就会决定使用 `reimbursement_with_tax` 这个工作流。

### 第四步：工作流引擎启动
**位置：** [backend/app/workflow_engine.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/workflow_engine.py)
- 后端发现要运行 `reimbursement_with_tax`，这个工作流定义在 [main.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py) 中：
  `["calc_money", "calc_tax", "format_breakdown"]`
- 引擎会按照这个顺序，一个一个去**注册中心 (Registry)** 找对应的函数来执行。

### 第五步：任务节点执行
**位置：** [backend/app/main.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py) & `backend/app/skills/`
- **节点 1: `calc_money`**: 计算基础金额。
- **节点 2: [calc_tax](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py#46-50)**: 根据基础金额计算 10% 的税。
- **节点 3: [format_breakdown](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py#52-58)**: 把金额和税拼接成一段话。
- **注意**：每个节点都会把结果存入 `context` 字典，下一个节点可以从这个字典里取上一个节点算出的数。

### 第六步：返回结果并由前端展示
- 后端把最终处理完的 `context` 返回给前端。
- 前端 [InputPage.tsx](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/pages/InputPage.tsx) 收到结果后，通过 `setResult(res.result)` 更新状态。
- React 重新渲染界面，你在屏幕上看到了 JSON 格式的计算结果。

---

## 3. 关键组件解析 (给前端同学的重点笔记)

### 1. 前端 API 封装 ([api/agent.ts](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/api/agent.ts))
作为前端开发者，你应该养成把 API 请求单独放在一个文件夹的习惯。这里使用了 `axios`。
> [!TIP]
> 这里的 `API_BASE` 指向的是后端的地址。

### 2. 自动路由与手动选择
在 [InputPage.tsx](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/pages/InputPage.tsx) 中，有个 `autoMode`。
- **AutoMode = true**: AI 猜你想干嘛（后端 [router.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/router.py) 做判断）。
- **AutoMode = false**: 你自己在下拉框选一个流程。

### 3. 后端插件系统 ([plugin_loader.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/plugin_loader.py))
这是这个 Demo 比较高级的地方。它会自动扫描 [skills/](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py#116-120) 文件夹下的 Python 文件。这意味着如果你想给 Agent 增加新技能，你只需要在那个文件夹下写个新文件，不需要修改 [main.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py) 的核心代码。

---

## 4. 后续学习建议

1.  **尝试添加一个新技能**：
    在 `backend/app/skills/` 下模仿写一个简单的函数，比如“打折计算”，然后在 [main.py](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py) 的 [workflows](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/backend/app/main.py#110-114) 里引用它。
2.  **优化前端 UI**：
    目前结果是简单的 JSON 文本。你可以尝试在 [InputPage.tsx](file:///Users/wangyahui/yonyou/AI%E5%B7%A5%E5%85%B7/agentDemo/frontend/src/pages/InputPage.tsx) 里的 `result` 渲染部分，用更漂亮的卡片形式展示报销明细。
3.  **理解 Context**：
    在练习时，仔细观察 `context` 对象是如何在各个步骤之间传递数据的，这是 Agent 工作流设计的核心。

希望这份文档能帮你快速上手！如果有具体哪行代码看不懂，随时问我。
