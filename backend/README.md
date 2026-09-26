# 微言 · Mock Bank + Agent（能力层）

仿真银行后端（`bank_sim/`）+ Agent 编排（`agent/`）+ HTTP 接口（`api/main.py`）。

## 启动（含前端演示）

```bash
pip install -r ../requirements.txt   # 或已激活 .venv
uvicorn backend.api.main:app --reload
```

- Swagger 文档：http://127.0.0.1:8000/docs
- 前端聊天页：直接双击打开 `frontend/index.html`（file:// 可用，已配 CORS）

## Agent 对话接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/agent/chat` | 人话 → LLM 决策 → 权限门 → 执行/确认/强验证 |
| POST | `/api/v1/agent/confirm` | 黄级：用户确认后执行（再次过权限门） |
| POST | `/api/v1/agent/authorize` | 红级：短信/人脸/U盾 强验证通过后执行（演示码 123456） |
| POST | `/api/v1/agent/reset` | 重置会话与银行数据 |
| GET | `/api/v1/agent/audit` | 审计日志（决策链路全记录） |

## 常用 Mock Bank 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/accounts/{id}/balance` | 查余额 |
| GET | `/api/v1/accounts/{id}/transactions` | 查交易流水 |
| POST | `/api/v1/transfers` | 转账（幂等，带 execution_id） |

## 设计说明（答辩点）

- 金额一律用「分」(cents)，不用浮点；转账幂等（同 request_id 只执行一次）。
- 每个操作返回唯一 `execution_id`——Agent 只能回显真实返回值（幻觉防护第一道闸）。
- **权限门在编排层（Agent）调用前强制经过**：接口是"哑的"，即使 LLM 被诱导，越权也会被拦截（deny/mfa）。
- 会话内"今日累计转账"跟踪：超 1000 元自动升级红级强验证（黄→红）。
- LLM 可插拔（DeepSeek 默认，`backend/agent/llm.py`）；无 Key 自动回退 MockLLM。
