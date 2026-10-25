# Smart Travel Agent（智能出行助手）

[![CI](https://github.com/zyt08280527/smart-travel-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zyt08280527/smart-travel-agent/actions/workflows/ci.yml)

一个用于解决自然语言出行路线规划与交通方式选择问题的全栈智能助手。用户只需描述
起终点、出行偏好或限制条件，Agent 就能组合天气、地点解析、驾车、步行和公共交通能力，
比较候选方案并给出可解释建议；单段通过 Human-in-the-loop（HITL）批准保存，
多站点通过页面明确确认保存，规划和推荐本身不自动保存正式行程。

项目当前已具备可运行的 React 前端、FastAPI 后端、MCP 工具服务、
LangGraph SQLite checkpoint、独立产品会话历史、多轮对话、行为评估和自动化测试。

当前版本（2026-10-25）已基本满足核心需求，用户简单试用及设备定位到推荐、路线规划链路验收通过；
这不代表所有场景、设备和上线条件已验收。最近一次代码验证为后端607项、前端84项测试及静态检查/生产构建通过，
主要使用模拟供应商；本轮文档收尾未重新运行测试。项目尚未部署为公开服务。

- [本版功能、验证状态与已知限制](docs/VERSION_STATUS.md)
- [项目演示流程](docs/DEMO_GUIDE.md)
- [新对话交接与阶段记录](docs/NEW_CHAT_HANDOFF.md)

先收尾当前核心功能，之后再讨论上线准备；景点扩库安排在上线之后。

## 核心能力

- 城市级实时天气查询：温度、体感温度、降水量、风速和天气现象；
- 地点搜索与起终点解析：通过高德 POI 返回候选地点、经纬度和数据署名；
- 驾车路线规划：基于高德的交通感知时长、费用信息、路况和分步导航；
- 步行路线规划：基于高德路线规划 2.0 的预计时长和中文步行指引；
- 公共交通规划：步行接驳、公交/地铁分段、换乘次数和候选方案；
- 综合出行推荐：并发获取当前天气与三种路线，按时间、费用、步行和换乘偏好评分并支持失败降级；
- 单层出行偏好：综合、时间、省钱、少步行、少换乘、地铁优先；独立设置硬限制，先过滤再推荐，系统推荐与手动选择分离，地图和保存跟随当前选择；
- 统一行程候选：将驾车、步行和每条公共交通路线整理为同一级可执行方案，支持交通方式筛选、单击切换、地图联动和保存当前方案；
- 路线快照保鲜：偏好追问优先复用有效快照进行本地重排，快照过期后携带已确认坐标自动刷新路线与天气；刷新失败时明确降级为过期快照并提示风险；
- 多轮 Agent 对话：使用同一个 `thread_id` 保留上下文；
- HITL 行程保存：用户批准后才写入 JSONL，拒绝则不产生业务记录；
- 已保存行程查询：同一份 JSONL 数据可由 Agent 只读查询，也可通过 REST API 在前端列表展示；
- 行程详情与删除：前端可展开查看时长依据和备注，删除前二次确认并原子更新本地记录；
- 一键重新规划：从已保存行程发起新的 Agent 会话，重新查询当前天气与三种路线，并对比保存时方案和当前推荐；
- 真实路线地图：在高德地图上展示驾车、步行和公共交通的当前候选轨迹；兼容现有 WGS84 路线数据，通过高德坐标转换接口转为 GCJ-02 后绘制，候选切换同步更新地图；
- SQLite checkpoint：FastAPI 重启后仍可恢复对话和待审批工作流；
- 确定性展示层：补齐数据署名，并拦截部分无依据的能力或实时信息声明；
- NDJSON 流式聊天：逐步输出回答并展示工具请求与完成状态；
- 结构化结果卡片：天气、地点、统一行程候选、路线详情和当前方案摘要；
- 当前行程工作区：电脑端左侧对话/右侧路线，手机端“对话 / 当前行程”切换；路线候选、地图和保存集中展示，聊天仅保留简短说明与查看入口；
- React Web UI：Markdown、审批卡片、结果卡片和服务端历史会话管理；
- 行程列表 UI：启动时加载最近保存记录，并在保存审批成功后自动刷新；
- 前端自动化测试：覆盖流解析、最终答案、卡片、HITL、历史加载和删除。
- 多站点完整行程：同城固定顺序、1–3个中间点，支持停留、时间窗、全程限制、全部分段地图、手选与保存重开；出发时间可选，校验具体时间约束时才需要补充；
- 目的地推荐：在现有三个深圳馆区内按明确条件核算往返，支持有限的连续条件修改、澄清、排除候选与服务端历史恢复；推荐采用确定性过滤/评分，不是训练出的推荐模型；
- 设备定位起点：在推荐弹窗主动授权定位，查看地址/精度/时间并确认后重新推荐，后续规划沿用同一起点；也可手动选择，失败不自动回退旧默认起点；
- 到达时刻天气：按可信行程的站点和预计到达时刻查询小时预报，可在推荐中显式选择天气策略，不是全程气象或安全保证；
- 出发前守护与站内提醒：保存后主动开启，持久化检查与有限重试，支持取消、重启恢复、已读和只读查看原行程；重新规划须用户发起，不自动覆盖原保存。

## 系统架构

```text
React + TypeScript (Vite)
        │ HTTP / JSON
        ▼
FastAPI
        │
        ├── REST 业务服务 ─► 地点/设备定位、完整草案、目的地推荐与历史
        │                   多站点 SQLite（草案、保存快照、守护任务与已读）
        ├── 后台守护循环 ─► 路线/到达采样点天气复查、持久化站内结果
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
              ├── place MCP ──── 高德 POI 搜索
              ├── route MCP ─► 高德 Web服务 API（驾车/步行/公共交通）
              ├── planning MCP ─► 天气与三种路线并发比较、规则评分和失败降级
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
| `recommend_travel_plan` | 综合天气与三种路线，比较并推荐交通方式 |
| `plan_multi_stop_trip` | 按固定顺序规划同城完整多站点行程，生成草案，不自动保存 |
| `save_itinerary` | 保存行程；执行前必须通过 HITL 审批 |
| `list_itineraries` | 按保存时间倒序查询行程；只读操作，无需审批 |

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
AMAP_API_KEY=
ROUTE_SNAPSHOT_TTL_SECONDS=300
```

`ROUTE_SNAPSHOT_TTL_SECONDS` 控制路线快照可复用时长，默认 300 秒。有效期内的
偏好变化不会重复请求第三方服务；过期后系统会确定性调用综合规划工具，并在前端展示
路线数据更新时间与刷新状态。若第三方服务刷新失败，系统会对旧快照应用本轮新约束，
同时以降级警告明确标注数据已经过期，而不会将其描述为实时结果。

不要把真实 `.env` 或任何 API Key 提交到版本控制。

### 2. 启动 FastAPI

```powershell
python -m uvicorn travel_agent.api.app:app --host 127.0.0.1 --port 8000
```

健康检查：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

若 8000 已被 RAG 等其他服务占用，智能出行后端改用 `--port 8001`，同时将 `frontend/.env.local` 的 `VITE_API_BASE_URL` 设置为 `http://127.0.0.1:8001`。智能出行的 `/health` 应返回 `tools` 工具列表；前端会拒绝其他服务的健康响应并显示端口检查提示。

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

地图需在 `frontend/.env.local` 中配置单独申请的高德 **Web 端（JS API）** Key：

```dotenv
VITE_AMAP_JS_KEY=你的JS_API_Key
# 本地开发用安全密钥；VITE_ 变量会出现在浏览器构建产物中。
VITE_AMAP_SECURITY_JS_CODE=你的JS_API安全密钥
```

后端 `.env` 中的 `AMAP_API_KEY` 仍是 Web 服务 Key，不能直接替代 JS API Key。生产部署改配 `VITE_AMAP_SERVICE_HOST=https://你的域名/_AMapService`，由受控安全代理携带安全密钥转发，不将安全密钥打包到前端；具体配置参考[高德 JS API 安全配置](https://lbs.amap.com/api/javascript-api-v2/prerequisites)。缺少配置或加载失败时，地图区域显示提示和重试入口，路线详情仍可查看。

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
| `GET` | `/api/itineraries?limit=20` | 获取最近保存的行程列表 |
| `DELETE` | `/api/itineraries/{itinerary_id}` | 删除指定的已保存行程 |

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

行为评估使用固定的 8 条任务数据集，分别统计工具选择、工具参数和端到端任务完成情况，
同时记录运行耗时、模型与 MCP 工具调用次数、Token 用量及外部 HTTP 尝试。评测报告写入
`artifacts/evals/`，该目录中的运行结果不提交到版本控制。

2026-09-11 的一次真实运行结果：

| 指标 | 结果 |
| --- | --- |
| 工具选择正确率 | 8/8（100%） |
| 参数正确率 | 7/7（100%） |
| 任务完成率 | 8/8（100%） |
| 外部服务阻塞案例 | 0/8 |
| 平均案例耗时 | 13.57 秒 |
| 总 Token | 87,497 |
| 外部 HTTP 尝试 | 30 次，重试 0 次，失败尝试 0 次 |

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

- 当前天气MCP工具使用城市级查询；行程天气另有逐站坐标/小时预报入口，只覆盖采样点，不是现场实测或全程天气；
- 当前天气结果不包含湿度、紫外线指数和降水概率；
- 驾车时长和路况来自查询时的交通感知快照；步行与公共交通时长为静态预计；
- 路线快照默认缓存 5 分钟；该 TTL 只控制多轮偏好重排时是否重新请求，不代表路线在 5 分钟内绝对不变；
- 自动刷新失败时可能使用上一轮过期快照给出临时建议；前端会明确提示，用户不应将降级结果视为实时导航依据；
- 公共交通费用为 `null` 时只表示未提供，不能推断免费；
- 公共交通工具不提供实时班次、车辆位置、到站或运营状态；
- 路线地图使用抽样后的查询时轨迹，不是逐秒更新的导航；底图及 WGS84 转 GCJ-02 需要访问高德服务并配置 JS API Key；
- 行程支持本地保存、Agent 查询和前端列表展示；服务端可恢复会话与推荐状态，未提供账号隔离、公开上线的跨设备服务、订票或导出；
- 多站点首版不支持跨城、自动优化站点顺序、停车换乘；目的地推荐仅现有三个深圳馆区，严格要求已核实当日开放时可能无候选；
- 守护是出发前的一次性检查窗口，依赖服务运行，异常最多尝试三次；站内查看，不是实时跟踪、官方预警或保证准点，无邮件/推送；
- 设备定位需要用户授权与安全环境，精度不足、超时或不在深圳时需手动选择；定位不会自动更新或覆盖明确填写的其他起点；
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

当前实现、核心流程与后续计划见 [docs/project-progress.md](docs/project-progress.md)，分阶段学习记录见 [docs/learning-roadmap.md](docs/learning-roadmap.md)。

## 工程实现要点

- 基于 LangChain/LangGraph 构建能选择 10 个 MCP 工具的智能出行 Agent；新增多站点工具的真实模型行为仍待联调；
- 设计 Domain → Service → MCP → Agent 分层，统一多来源 API 数据并用 Pydantic 校验；
- 使用 HITL 中间件保护行程写入，通过批准/拒绝流程避免模型直接执行有副作用操作；
- 使用 Async SQLite checkpoint 持久化多轮会话与 interrupt，支持服务重启后恢复审批；
- 将 LangGraph 执行状态与产品会话历史分库，支持历史加载、继续对话和联动删除；
- 构建 React + FastAPI 全栈交互，支持流式 Markdown、结果卡片、历史会话和审批卡片；
- 建立单元测试、协议冒烟测试、行为评估和确定性展示策略，约束模型幻觉与数据边界。
