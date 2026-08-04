# 简历与面试材料

本文中的数字只使用项目当前真实实现和已运行评测结果。外部服务延迟和 Token 用量会波动，
面试时应说明测量环境和数据集规模，不把单次结果描述成普遍性能保证。

## 30 秒项目介绍

我实现了一个基于 LangGraph 和 MCP 的全栈智能出行 Agent。用户可以用自然语言查询天气、
搜索地点并规划驾车、步行或公共交通路线，模型会根据上下文自主选择 4 个 MCP Server 提供的
7 个工具。项目的重点不只是工具调用，我还使用 Human-in-the-loop 保护行程写入，使用
SQLite checkpoint 支持多轮会话和服务重启后的审批恢复，并通过 FastAPI、React 和 NDJSON
实现流式交互。工程上建立了固定行为评测、HTTP 可观测性、有限重试和 GitHub Actions CI；
最近一次 7 条固定任务评测中，工具选择、参数和任务完成三项指标均达到 100%。

## 简历项目描述（可直接粘贴）

**智能出行 Agent｜独立开发｜20XX.XX – 至今**

项目地址：<https://github.com/zyt08280527/smart-travel-agent>

技术栈：Python、LangGraph、LangChain、MCP、FastAPI、React、TypeScript、SQLite、Pydantic、Qwen

- 基于 LangGraph 与 MCP 构建智能出行 Agent，将天气、地点解析、驾车、步行、公共交通及行程
  保存封装为 4 个 MCP Server、7 个标准化工具，由模型结合对话上下文自主选择并顺序编排。
- 设计 Domain → Service → MCP → Agent 分层，接入 Open-Meteo、OpenStreetMap、OSRM、
  openrouteservice 和高德 Web服务 API，通过 Pydantic 统一多来源数据，并处理 WGS84/GCJ-02
  坐标差异、超时、业务错误与数据署名边界。
- 使用 Human-in-the-loop 拦截具有副作用的行程保存操作，并通过异步 SQLite checkpoint 持久化
  LangGraph 执行状态，实现用户批准/拒绝、只写一次以及服务重启后恢复待审批工作流。
- 构建 FastAPI + React 全栈交互，支持 NDJSON 流式输出、工具进度、Markdown、结构化结果卡片
  和历史会话；建立 7 条固定行为评测及 134 个后端、8 个前端测试，最近一次评测的工具选择、
  参数和任务完成率均为 100%，并通过 GitHub Actions 自动执行代码检查、测试和生产构建。

## 简历数字的正确讲法

可以说：

> 在当前 7 条固定行为评测集上，工具选择正确率为 7/7、参数正确率为 6/6、任务完成率为 7/7。

不应说：

> Agent 在所有真实出行问题上的准确率都是 100%。

原因是 7 条任务属于项目回归集，能够证明关键路径稳定，但规模还不足以代表所有城市、表达方式、
网络状态和第三方 API 情况。

## 面试讲述顺序

1. 先讲用户问题：自然语言出行请求需要天气、地点和路线等多个外部能力协作。
2. 再讲 Agent：模型负责决定调用哪个工具以及参数，LangGraph 负责维护消息循环和执行状态。
3. 再讲 MCP：将外部能力暴露成带 JSON Schema 的标准工具，降低 Agent 与服务实现的耦合。
4. 再讲可靠性：Pydantic 校验、错误边界、确定性展示、HITL、checkpoint、重试和可观测性。
5. 最后讲验证：单元测试、协议冒烟测试、行为评测、报告对比和 GitHub Actions CI。

## 高频面试问题目录

1. 请简单介绍一下这个项目。
2. 为什么这个项目需要 Agent，而不是固定 `if/else` 工作流？
3. 模型如何决定调用哪个工具以及传什么参数？
4. 为什么使用 MCP，而不是直接使用普通 Python 函数或 LangChain Tool？
5. LangChain 和 LangGraph 有什么区别？
6. LangGraph 在这个项目中具体做了什么？
7. 为什么采用 Domain → Service → MCP → Agent 分层？
8. Service 适配层如何统一不同天气和地图接口的数据？
9. HITL 如何保证模型不能直接保存行程？
10. 为什么同时使用 `checkpoints.sqlite` 和 `conversations.sqlite`？
11. 如何减少大模型幻觉并保证 Agent 结果可靠？
12. 为什么 Agent 项目还需要行为评估，不能只做单元测试？
13. 如何处理第三方天气和地图 API 的超时或失败？
14. 为什么流式接口选择 NDJSON，而不是普通 JSON、SSE 或 WebSocket？
15. MCP Server 如何通过 stdio 通信，为什么不能随意 `print()`？
16. JSONL 是什么，为什么当前行程使用 JSONL 存储？
17. 项目最难的部分、当前不足和后续优化方向是什么？
18. 多轮对话的上下文是如何保存和恢复的？
19. 如何避免同一条审批请求被重复执行或重复写入？
20. 为什么项目同时涉及 WGS84 和 GCJ-02 坐标系？
21. 工具描述和 JSON Schema 为什么会影响模型的工具选择？
22. 为什么选择 Qwen，并使用 OpenAI-compatible 接口？
23. 如何防范 Prompt Injection 和越权工具调用？
24. 如何降低 Agent 的延迟、Token 和第三方 API 成本？
25. 如何在不调用真实模型和外部服务的情况下测试项目？
26. 为什么 GitHub CI 不直接运行真实 Agent 行为评测？
27. 如果将项目部署为多用户生产服务，架构需要怎样演进？

## 高频问题回答要点

### 1. 请简单介绍一下这个项目

- 用户使用自然语言完成天气、地点和多方式路线查询，并可在审批后保存行程。
- Qwen 负责理解意图与提出工具调用，LangChain 组装模型和工具，LangGraph 管理有状态执行。
- 4 个 MCP Server 暴露 7 个工具，FastAPI + React 提供流式 Web 交互。
- 可靠性设计包括 Pydantic、确定性展示、HITL、SQLite checkpoint、有限重试和行为评测。

### 2. 为什么需要 Agent

- 用户表达和任务组合不固定，可能只查天气，也可能执行地点解析 → 路线规划 → 行程保存。
- 模型适合处理自然语言意图和动态工具编排，固定 `if/else` 更适合入口和流程完全确定的业务。
- 模型不负责关键数据校验和权限；确定性代码负责业务边界与副作用控制。

### 3. 模型如何决定工具和参数

- MCP 工具向模型提供工具名称、描述和 JSON Schema。
- Qwen 根据用户消息、System Prompt、历史消息和工具 Schema 生成结构化 tool call。
- LangGraph 执行工具，把结果作为 `ToolMessage` 加回上下文，再让模型继续推理。
- LangGraph 负责执行循环，但具体选哪个工具和参数主要由模型推理产生。

### 4. 为什么使用 MCP

- MCP 标准化工具发现、参数 Schema、调用和结果返回，降低 Agent 与工具实现的耦合。
- Agent 不需要知道天气和地图服务内部使用哪个第三方 API。
- 工具可以由不同进程、语言或应用复用；代价是增加进程管理和协议调试复杂度。
- 如果只是一个不会复用的小型单体程序，直接使用普通 Tool 会更简单。

### 5. LangChain 和 LangGraph 的区别

- LangChain 是高层 Agent 框架，提供模型、消息、工具、中间件和 `create_agent()`。
- LangGraph 是有状态编排运行时，提供图执行、streaming、interrupt、checkpoint 和恢复。
- 当前 LangChain Agent 构建在 LangGraph 上；二者是不同抽象层，不是互斥选择。

### 6. LangGraph 在项目中做了什么

- 维护模型 → 工具 → 模型的消息循环。
- 按 `thread_id` 保存多轮状态。
- 在 `save_itinerary` 前触发 interrupt，通过 SQLite checkpoint 保存暂停点。
- 用户审批后使用 `Command(resume=...)` 从暂停位置继续执行。

### 7. 为什么采用四层架构

- Domain：定义统一业务模型、字段类型和校验规则。
- Service：调用第三方 API，处理超时、业务错误和字段转换。
- MCP：将 Service 包装成带 Schema 的标准工具。
- Agent：理解自然语言并动态编排工具。
- 分层隔离第三方接口、工具协议和模型行为这三类不同变化。

### 8. Service 如何统一不同接口

- 第三方原始字段只在 Service 内解析，例如 `temperature_2m` 映射为 `temperature_c`。
- Service 将原始 JSON 转换成 `CurrentWeather`、`RoutePlan` 等 Pydantic 模型。
- Pydantic 再检查类型、必填字段、非负数和经纬度范围。
- MCP 只把统一模型序列化后的 JSON 交给模型；更换供应商时主要修改 Service。

### 9. HITL 如何保护行程保存

- 模型生成 `save_itinerary` tool call 时只是提出操作及参数，工具尚未执行。
- `HumanInTheLoopMiddleware` 在工具执行前拦截并暂停 LangGraph。
- checkpoint 保存待执行操作，前端显示批准/拒绝卡片。
- 批准后恢复并真正执行写入；拒绝则跳过工具，JSONL 文件保持不变。

### 10. 为什么使用两个 SQLite 数据库

- `checkpoints.sqlite` 保存 LangGraph 内部消息、工具轨迹、节点状态和 HITL 暂停点，用于恢复执行。
- `conversations.sqlite` 保存产品可见消息、会话标题和结果卡片，用于前端历史展示。
- 两者通过 `thread_id` 关联，但把运行时内部状态与产品展示模型解耦。

### 11. 如何减少幻觉

- System Prompt 约束行为方向，但不把 Prompt 当成唯一安全边界。
- MCP JSON Schema 约束工具参数，Pydantic 校验业务数据。
- Service 统一外部数据语义，确定性展示层补充来源并限制无依据声明。
- HITL 控制副作用，固定行为评测检查工具轨迹、参数和最终回答。

### 12. 为什么需要行为评估

- 单元测试适合验证确定性的字段转换、异常处理和存储逻辑。
- Agent 行为还受模型推理影响，需要检查工具选择、参数、`ToolMessage` 和最终回答。
- 当前固定评测集统计工具选择 7/7、参数 6/6、任务完成 7/7。
- 外部服务故障标记为 `BLOCKED`，避免把基础设施问题误判为 Agent 行为回退。

### 13. 如何处理第三方 API 失败

- 网络异常、HTTP 429 和 5xx 最多尝试 2 次；普通 4xx 和明确业务错误不重试。
- 每次尝试记录服务方、方法、脱敏路径、状态码、耗时、成功状态和 attempt。
- 观测数据通过 MCP artifact 供应用和评测使用，不混入模型业务文本。
- 后续生产优化可增加缓存、熔断、降级和备用服务商。

### 14. 为什么使用 NDJSON

- 普通 JSON 必须等待整次 Agent 执行结束，无法展示工具进度和文本增量。
- NDJSON 每行一个独立事件，适合 `tool_requested`、`assistant_delta`、`final` 和审批等结构化事件。
- 当前通信是一次 POST、单向流式响应，不需要 WebSocket 的长期双向连接。
- 前端用缓冲区处理任意字节分块，再按换行解析完整 JSON。

### 15. stdio MCP 与 stdout 边界

- MCP Client 通过子进程 stdin 发送协议请求，通过 stdout 读取协议响应。
- 普通 `print()` 默认写 stdout，可能与协议消息混合并导致解析失败。
- 诊断日志应写 stderr 或日志系统，业务结果通过 MCP 返回值传输。
- stdio 适合本地单机；独立远程部署时需要采用适合网络访问的 transport。

### 16. JSONL 是什么

- JSONL 每行保存一个独立 JSON 对象，追加简单、可读，适合原型和演示。
- 当前批准保存后向 `data/itineraries.jsonl` 追加一条记录。
- JSONL 缺少数据库索引、关系、事务和成熟的多进程并发控制。
- 生产环境可迁移至 PostgreSQL，增加 `users`、`itineraries` 及所有权和权限关系。

### 17. 最难部分、不足与优化

- 主要难点：把模型驱动的保存请求改造成可暂停、可审批、可恢复且不重复写入的工作流。
- 当前不足：评测集规模较小、缺少公网鉴权和限流、JSONL 不适合多用户生产业务、尚未云端部署。
- 建议顺序：扩大评测集 → 鉴权与限流 → 缓存/熔断/降级 → 正式数据库 → Docker 和云部署。
- 回答不足时要说明边界和演进方案，不能声称当前已经具备不存在的生产能力。

### 18. 多轮上下文如何保存和恢复

- 前端在后续请求中继续携带同一个 `thread_id`，后端据此构造 LangGraph 配置。
- LangGraph checkpointer 按线程保存消息和图状态，下一次调用时恢复该线程的上下文。
- 产品可见历史另存于 `conversations.sqlite`，避免前端直接依赖 LangGraph 内部消息结构。
- 长对话仍需要进一步增加消息裁剪、摘要或长期记忆策略，避免上下文和 Token 持续增长。

### 19. 如何避免重复审批和重复写入

- 暂停状态与待审批操作绑定到 `thread_id`，审批接口先确认该线程确实存在待处理 interrupt。
- 第一次批准后图从 checkpoint 继续并完成，后续再次提交时没有待审批操作，因而拒绝重复恢复。
- 当前保证主要来自工作流状态和测试验证；生产环境还应增加幂等键、数据库唯一约束和事务。
- 不能把“当前测试中只写一次”夸大成分布式环境下已经具备严格 exactly-once 语义。

### 20. 为什么存在两种坐标系

- OpenStreetMap、OSRM 和 openrouteservice 使用 WGS84；高德国内地图接口使用 GCJ-02。
- 将 WGS84 坐标直接传给高德可能产生位置偏移，影响逆地理编码和公共交通规划。
- 公交 Service 在调用高德前完成坐标转换，再解析城市编码并请求路线。
- 坐标转换属于确定性基础设施逻辑，不应交给模型计算。

### 21. 工具描述和 JSON Schema 的作用

- 模型主要根据工具名称、docstring 描述和参数 Schema 判断工具用途及如何构造参数。
- 描述需要明确能力范围，例如天气工具只接受城市名，避免传入景点或完整地址。
- `Literal`、默认值和必填字段会进入 Schema，减少无效参数组合。
- Schema 是结构约束，不保证业务语义绝对正确，因此仍需 Pydantic 和 Service 校验。

### 22. 为什么选择 Qwen 和兼容接口

- Qwen 支持结构化 tool calling，能够根据工具 Schema 生成名称、参数和 call ID。
- OpenAI-compatible 接口可复用成熟的 LangChain 客户端，减少针对厂商协议的定制代码。
- 模型创建集中在独立模块，业务 Service 与 MCP 工具不依赖具体模型供应商。
- 如果替换模型，需要重新运行行为评测，不能只验证接口能够连接。

### 23. 如何防范 Prompt Injection 和越权调用

- 不把用户文本直接拼接成系统命令、文件路径或 SQL，工具只接受受限的结构化参数。
- 用 System Prompt 声明能力边界，但关键权限不能只依赖 Prompt。
- 写操作经过 HITL；Pydantic、`Literal` 和 Service 校验限制参数范围。
- 公网生产环境还需要用户鉴权、资源所有权检查、速率限制、审计和更细粒度的工具权限策略。

### 24. 如何优化延迟和成本

- 减少不必要的工具描述和历史消息，长会话可采用裁剪或摘要。
- 对稳定的地点解析结果设置合规缓存，避免重复调用第三方服务。
- 可以并行执行相互独立的只读工具，但存在依赖关系的步骤仍需顺序执行。
- 通过评测报告同时观察任务正确率、平均耗时、Token 和 HTTP 次数，避免只优化单一指标。

### 25. 如何隔离真实模型和外部服务进行测试

- Service 接受外部 `httpx.AsyncClient`，测试时注入 `MockTransport` 返回固定响应。
- 单元测试分别覆盖 Domain 校验、Service 转换、MCP 包装、展示策略和存储。
- 前端使用 Vitest 和 React Testing Library 模拟 NDJSON 流、审批和历史会话接口。
- 真实模型与真实网络留给行为评测和冒烟测试，避免普通单元测试不稳定且产生费用。

### 26. 为什么 CI 不运行真实 Agent 评测

- GitHub 公共 CI 不应持有个人开发环境的真实 API Key，也不应在每次 push 时产生模型费用。
- 第三方天气和地图服务波动会使 CI 出现与代码无关的偶发失败。
- CI 运行可重复的 Ruff、Pytest、前端测试、lint 和 production build。
- 真实行为评测作为受控任务运行，生成报告后再与基线比较；生产团队可使用受保护 Secrets 和定时任务扩展。

### 27. 多用户生产架构如何演进

- 使用 PostgreSQL 保存用户、会话和行程，以唯一约束和事务支持权限、幂等及并发。
- 引入登录鉴权、租户隔离、速率限制、API 配额和审计日志。
- 将 Web API、Agent worker 和 MCP/外部服务适配拆分，并使用任务队列处理长时间运行任务。
- 使用容器、反向代理、HTTPS、集中式日志、指标、trace、缓存、熔断和降级策略。
- 部署前扩充离线与在线评测集，并针对模型、Prompt、工具或供应商变更设置回归门槛。
