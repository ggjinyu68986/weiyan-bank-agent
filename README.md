# 微言 · Weiyan — AI Banking Agent

2026 深圳国际金融科技大赛（FinTechathon）· AI 赛道 · 银行 AI 智能体赛题

> 一句话简介：用户用自然语言描述需求，Agent 自主理解意图、规划任务链路，并在严格的安全框架内完成银行业务操作。

## 目录结构

```
weiyan-bank-agent/
├── backend/    # FastAPI + Agent 编排 + 权限引擎 + Mock Bank
├── frontend/   # 聊天 UI（React + Tailwind + ECharts）
├── docs/       # 技术文档、架构图、安全设计
├── tests/      # pytest + 自动评测 harness
└── README.md
```

## 技术栈（占位，9/27 启动会定稿）

- 后端：Python 3.12 + FastAPI + SQLite + APScheduler
- Agent：自研轻量编排（意图 → DAG 规划 → 权限门 → 执行 → 审计）
- LLM：OpenAI 兼容 API（豆包 / GPT / Claude 可切换）
- 前端：React (Vite) + Tailwind + ECharts
- 沙箱：Docker

## 快速开始（部署说明，10/25 完善）

> TODO：Docker Compose 一键启动、环境变量说明（LLM API Key）、测试运行方式

## 测试

> TODO：pytest + 6 场景自动评测 harness

## 安全设计

- 权限分级：绿（自动执行）/ 黄（用户确认）/ 红（多因子强验证）
- 幻觉防护 / 注入防御 / 操作审计 / 异常熔断 / 沙箱运行
- 详见 `docs/安全设计.md`（TODO）

## License

> TODO：10/25 补齐（建议 MIT / Apache-2.0）

## 参赛信息

- 赛题：银行 AI 智能体（Bank AI Agent）
- 对标：Revolut AIR
- 覆盖场景：智能转账 / 账单分析 / 理财操作 / 卡片管理 / 订阅代扣 / 跨场景联动
