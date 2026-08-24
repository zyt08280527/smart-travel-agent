# Smart Travel Agent（智能出行助手）

[![CI](https://github.com/zyt08280527/smart-travel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zyt08280527/smart-travel-agent/actions/workflows/ci.yml)

一个面向 AI Agent 实习求职的全栈工程项目。用户使用自然语言描述出行需求，
Agent 根据上下文自主选择天气、地点搜索、驾车、步行、公共交通和行程保存工具，
并在写入行程前通过 Human-in-the-loop（HITL）请求用户审批。

项目当前已具备可运行的 React 前端、FastAPI 后端、MCP 工具服务、
LangGraph SQLite checkpoint、独立产品会话历史、多轮对话、行为评估和自动化测试。

## 核心能力

- 城市级实时天气查询：温度、体感温度、降水量、风速和天气现象；
- 地点搜索与起终点解析：返回候选地点、经纬度和完整数据署名；
- 驾车路线规划：距离、静态预计时长和分步导航；
- 步行路线规划：基于 openrouteservice 的中文步行指引；
- 公共交通规划：步行接驳、公交/地铁分段、换乘次数和候选方案；
- 多轮 Agent 对话：使用同一个 `thread_id` 保留上下文；
- HITL 行程保存：用户批准后才写入 JSONL，拒绝则不产生业务记录；
- SQLite checkpoint：FastAPI 重启后仍可恢复对话和待审批工作流；
- 确定性展示层：补齐数据署名，并拦截部分无依据的能力或实时信息声明；
- NDJSON 流式聊天：逐步输出回答并展示工具请求与完成状态；
- 结构化结果卡片：天气、地点、驾车、步行和公共交通摘要；
- React Web UI：Markdown、审批卡片、结果卡片和服务端历史会话管理；
- 前端自动化测试：覆盖流解析、最终答案、卡片、HITL、历史加载和删除。

## 系统架构

```text
React + TypeScript (Vite)
        │ HTTP / JSON
        ▼
FastAPI
        │
        ▼
AgentRuntime ───────────────► SQLite checkpoints
        │                     （执行状态、工具轨迹、HITL 暂停状态）
        ├───────────────────► SQLite conversations
        │                     （会话索引、可见消息、结果卡片）
        ▼
LangChain create_agent + LangGraph
        │
        ├── Qwen OpenAI-compatible endpoint
        │
        └── MultiServerMCPClient
              ├── weather MCP ── Open-Meteo
              ├── place MCP ──── OpenStreetMap Nominatim
              ├── route MCP
              │     ├── OSRM（驾车）
              │     ├── openrouteservice（步行）
              │     └── 高德 Web服务 API（公共交通）
              └── itinerary MCP ─► data/itineraries.jsonl
```

## MCP 工具

| 工具 | 作用 |
| --- | --- |
| `query_current_weather` | 查询指定城市当前天气 |
| `search_places` | 搜索地点候选和经纬度 |
| `resolve_route_endpoints` | 同时解析路线起点与终点 |
| `plan_driving_route` | 规划驾车路线 |
| `plan_walking_route` | 规划步行路线 |
| `plan_transit_route` | 规划公共交通路线 |
| `save_itinerary` | 保存行程；执行前必须通过 HITL 审批 |

## 技术栈

- Python 3.12
- MCP Python SDK
- LangChain 1.x / LangGraph 1.x
- `langgraph-checkpoint-sqlite` / `aiosqlite`
- 通义千问 OpenAI 兼容接口
- FastAPI / Uvicorn / Pydantic
- React 19 / TypeScript / Vite
- `react-markdown`
- Pytest / Ruff / Oxlint

## 本地运行

### 1. Python 环境

Windows PowerShell：

```powershell
cd D:\agent开发\smart-travel-agent
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev,agent,api]"
```

复制环境变量模板：

```powershell
Copy-Item .env.example .env
```

在 `.env` 中填写：

```text
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=
ORS_API_KEY=
AMAP_API_KEY=
```

不要把真实 `.env` 或任何 API Key 提交到版本控制。

### 2. 启动 FastAPI

```powershell
python -m uvicorn travel_agent.api.app:app --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

### 3. 启动 React 前端

安装 Node.js 24 LTS 后，在另一个 PowerShell 窗口运行：

```powershell
cd D:\agent开发\smart-travel-agent\frontend
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
npm install
npm run dev
```

浏览器打开：

```text
http://localhost:5173
```

前端 API 地址默认是 `http://127.0.0.1:8000`。需要覆盖时，复制
`frontend/.env.example` 为 `frontend/.env.local`，然后修改
`VITE_API_BASE_URL` 并重启 Vite。

## HTTP API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 检查 Agent Runtime 和已加载工具 |
| `POST` | `/api/chat` | 发送新消息或继续指定 `thread_id` |
| `POST` | `/api/chat/stream` | 以 NDJSON 逐行返回 Agent 执行事件 |
| `POST` | `/api/threads/{thread_id}/approval` | 批准或拒绝待执行操作 |
| `GET` | `/api/conversations` | 获取最近更新的产品会话列表 |
| `GET` | `/api/conversations/{thread_id}` | 获取可见消息和结构化结果卡片 |
| `DELETE` | `/api/conversations/{thread_id}` | 删除产品历史和对应 checkpoint |

聊天响应分为两类：

```text
completed          → 返回最终 answer
approval_required  → 返回 pending_actions，等待用户决定
```

流式接口可能依次返回以下事件：

```text
run_started
tool_requested / tool_completed
result_card / assistant_delta
final / approval_required / error
done
```

## 状态与数据存储

```text
sessionStorage
  └── 当前浏览器标签页的消息、thread_id 和审批卡片

data/checkpoints.sqlite*
  └── LangGraph 消息、工具轨迹和 HITL checkpoint

data/conversations.sqlite*
  └── 前端会话索引、用户可见消息和结构化结果卡片

data/itineraries.jsonl
  └── 用户批准后写入的行程业务记录
```

Checkpoint 数据库可能包含用户输入和工具结果，已通过 `.gitignore` 排除，
不应当作公开数据提交。

## 质量检查

后端：

```powershell
ruff check .
pytest
```

前端：

```powershell
cd frontend
npm test
npm run lint
npm run build
```

行为评估：

```powershell
python scripts\agent_behavior_eval.py
python scripts\itinerary_hitl_eval.py
python scripts\walking_itinerary_hitl_eval.py
python scripts\transit_itinerary_hitl_eval.py
```

行为评估使用固定的 7 条任务数据集，分别统计工具选择、工具参数和端到端任务完成情况，
同时记录运行耗时、模型与 MCP 工具调用次数、Token 用量及外部 HTTP 尝试。评测报告写入
`artifacts/evals/`，该目录中的运行结果不提交到版本控制。

2026-07-30 的一次真实运行结果：

| 指标 | 结果 |
| --- | --- |
| 工具选择正确率 | 7/7（100%） |
| 参数正确率 | 6/6（100%） |
| 任务完成率 | 7/7（100%） |
| 外部服务阻塞案例 | 0/7 |
| 平均案例耗时 | 8.73 秒 |
| 总 Token | 55,296 |
| 外部 HTTP 尝试 | 16 次，重试 0 次，失败尝试 0 次 |

耗时、Token 和外部请求会随模型、网络和第三方服务状态变化，因此它们是测量样本，
不是固定性能承诺。可使用下面的命令比较最近两次报告并检查准确率、延迟和 Token 回退：

```powershell
python scripts\compare_eval_reports.py
```

简单并发测试（需先启动后端）：

```powershell
python scripts\agent_load_test.py --requests 20 --concurrency 5
```

默认场景让 20 个独立会话并发执行不调用工具的短回答，用于测量 FastAPI、Agent 运行时、
SQLite 会话写入和模型调用这条基础链路。2026-08-24 的一次本地运行中，20/20 请求成功，
平均延迟 544.46 ms、P95 延迟 823.93 ms、失败率 0%。该结果不覆盖天气、地图等第三方
工具延迟，也不是生产容量结论；完整逐请求结果写入 `artifacts/load-tests/`。

GitHub Actions 会在每次 push 和 pull request 时自动运行后端 Ruff、Pytest，
以及前端测试、Oxlint 和生产构建。需要真实 API 和模型调用的行为评估不在公共 CI 中运行，
避免泄露密钥、产生 Token 费用或把第三方网络波动误判为代码回归。

## 数据边界

- 天气工具只支持城市级当前天气，不支持校区或精确坐标天气；
- 当前天气结果不包含湿度、紫外线指数和降水概率；
- 路线时长为静态预估，不包含实时拥堵；
- 公共交通费用为 `null` 时只表示未提供，不能推断免费；
- 公共交通工具不提供实时班次、车辆位置、到站或运营状态；
- 行程当前只支持本地保存，不支持订票或导出文件；
- 地点和路线回答必须保留对应数据来源署名。

## 项目结构

```text
smart-travel-agent/
├── frontend/                     # React + TypeScript Web UI
├── src/travel_agent/
│   ├── api/                      # FastAPI、请求模型和 Agent Runtime
│   ├── domain/                   # Pydantic 领域模型
│   ├── services/                 # 外部 API 与业务转换层
│   ├── mcp_servers/              # MCP 工具协议适配层
│   ├── agent.py                  # Agent、MCP 客户端、HITL、checkpoint
│   ├── config.py                 # 后端配置
│   ├── model.py                  # Qwen 模型客户端
│   └── presentation.py           # 确定性用户展示策略
├── scripts/                      # 冒烟测试、数据追踪和行为评估
├── tests/                        # 领域、服务、MCP、Agent 和 API 测试
├── data/                         # 本地运行时数据
├── .env.example
└── pyproject.toml
```

分阶段学习记录见 [docs/learning-roadmap.md](docs/learning-roadmap.md)。

## 可用于简历的项目要点

- 基于 LangChain/LangGraph 构建能自主选择 7 个 MCP 工具的智能出行 Agent；
- 设计 Domain → Service → MCP → Agent 分层，统一多来源 API 数据并用 Pydantic 校验；
- 使用 HITL 中间件保护行程写入，通过批准/拒绝流程避免模型直接执行有副作用操作；
- 使用 Async SQLite checkpoint 持久化多轮会话与 interrupt，支持服务重启后恢复审批；
- 将 LangGraph 执行状态与产品会话历史分库，支持历史加载、继续对话和联动删除；
- 构建 React + FastAPI 全栈交互，支持流式 Markdown、结果卡片、历史会话和审批卡片；
- 建立单元测试、协议冒烟测试、行为评估和确定性展示策略，约束模型幻觉与数据边界。
