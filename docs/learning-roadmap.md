# 学习与实战路线

原则：每一阶段都必须有“可运行结果 + 自动化验证 + 能讲给面试官听的设计取舍”。

## 阶段 0：读懂需求与架构（已完成）

已掌握：

- Agent 与普通聊天机器人的区别；
- MCP 解决的工具协议标准化问题；
- Domain、Service、MCP、Agent 分层的职责；
- 为什么外部 API 适配和业务转换不应全部写在 `@mcp.tool()` 中。

阶段产物：项目架构、目录边界、依赖分组和分阶段路线。

## 阶段 1：实现第一个 MCP 工具（已完成）

完成“中文城市 → 坐标 → 当前天气”的完整工具链。

已掌握：

- `FastMCP` 根据函数签名生成工具 JSON Schema；
- stdio transport 与 stdout 协议边界；
- HTTP 状态、业务错误、超时和异常响应处理；
- Pydantic 数据转换与范围校验；
- 依赖注入和无真实网络的 Service 单元测试。

验收结果：天气 Service、MCP 协议冒烟测试和异常路径均可运行。

## 阶段 2：接入 LLM，形成最小 Agent（已完成）

Qwen 能通过 tool calling 自主决定是否调用天气工具，而不是用 `if/else`
硬编码用户意图。

已掌握：

- LangChain `create_agent` 与 LangGraph 消息循环；
- HumanMessage、AIMessage、ToolMessage 和 tool call ID；
- MCP 工具加载与模型工具 Schema；
- `thread_id` 和多轮会话上下文；
- System Prompt 与行为评估的不同作用。

验收结果：闲聊不调用天气工具；实时天气问题调用正确工具；缺少城市时先追问；
虚构城市由工具返回明确错误。

## 阶段 3：扩展为智能出行 Agent（已完成）

已实现 5 个 MCP Server、8 个工具：

- 天气查询；
- 地点搜索和路线起终点解析；
- 驾车、步行和公共交通规划；
- 综合天气与三种路线的交通方式比较；
- 行程保存。

已完成：

- OpenStreetMap 地点搜索、Open-Meteo 天气与高德路线规划数据适配；
- WGS84 与 GCJ-02 坐标转换；
- 多工具顺序编排；
- `save_itinerary` 的 HITL 批准与拒绝；
- 驾车、步行、公共交通保存评估；
- 确定性署名和关键数据边界展示。

验收结果：完成“天气 + 地点 + 路线 + 建议 + 用户批准后保存”的多步任务。

## 阶段 4：产品化（已完成）

### 已完成

- FastAPI 生命周期管理的长连接 Agent Runtime；
- `/health`、`/api/chat`、`/approval` HTTP 接口；
- CORS 和可配置前后端地址；
- React + TypeScript 聊天界面；
- 加载、错误、多轮消息和 Markdown 展示；
- HITL 审批卡片与批准/拒绝按钮；
- 浏览器 `sessionStorage` 会话恢复；
- Async SQLite checkpoint；
- FastAPI 重启后的普通对话恢复；
- FastAPI 重启后的 HITL 暂停与审批恢复；
- 可测试的 LangGraph v2 流式事件转换协议；
- FastAPI NDJSON 流式聊天接口 `/api/chat/stream`；
- 前端逐步回答、工具调用进度和流式 HITL 审批；
- 天气、地点、驾车、步行和公共交通结构化结果卡片；
- Vitest + React Testing Library 前端自动化测试；
- 前端 Oxlint、自动化测试和 production build。
- 独立的产品会话元数据与可见消息 SQLite 仓储；
- 会话列表、详情读取和结构化结果卡片恢复；
- React 历史会话侧栏、会话切换和继续对话；
- 会话删除确认，以及产品历史与 LangGraph checkpoint 联动清理。

验收结果：页面刷新或服务重启后可以列出、加载、继续和删除产品会话，
同时保持产品展示数据与 LangGraph 执行状态职责分离。

## 阶段 5：工程化与评测（核心部分已完成）

### 已完成

- Domain、Service、MCP、展示层和 FastAPI 单元测试；
- MCP 协议冒烟测试；
- 天气、地点、驾车、步行和公交的行为评估；
- HITL 批准、拒绝、只写一次和服务重启恢复测试；
- Ruff、Pytest、Vitest、Oxlint、TypeScript 和前端生产构建。
- 固定的 8 条 Agent 行为评测数据集；
- 工具选择正确率、参数正确率和任务完成率统计；
- 端到端延迟、模型调用、MCP 工具调用和 Token 用量统计；
- 外部 HTTP 请求的服务方、路径、状态码、耗时和成功状态观测；
- 只对网络异常、HTTP 429 和 5xx 生效的有限重试机制；
- 基线报告与候选报告对比，以及性能回退阈值检查；
- GitHub Actions 后端与前端 CI。

### 待完成

- 面向生产环境的集中式结构化日志与分布式 trace；
- 限流和公开访问鉴权；
- Docker 与部署说明；
- 公网或云端演示环境。

## 阶段 6：简历与面试材料（待开始）

最终简历描述必须来自真实实现和真实测量结果，例如：

> 基于 LangGraph 与 MCP 构建智能出行 Agent，编排天气、POI 与多方式路线工具；
> 通过 HITL 与 SQLite checkpoint 实现写操作审批及服务重启恢复。

量化描述必须等阶段 5 的固定评测集完成后再填写：

> 设计 N 条离线任务集，工具选择准确率达到 X%，端到端任务成功率达到 Y%。

在尚未测量前，不填写虚构的任务数量、准确率、延迟或成本。
