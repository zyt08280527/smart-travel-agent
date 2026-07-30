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

## 简历项目描述

**Smart Travel Agent｜LangGraph、MCP、FastAPI、React、Qwen**

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

