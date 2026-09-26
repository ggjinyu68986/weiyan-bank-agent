# 微言 · Mock Bank（能力层）

仿真银行后端：数据（`bank_sim/`）+ 业务服务（`bank_sim/service.py`）+ HTTP 接口（`api/main.py`）。

## 启动接口服务

```bash
pip install fastapi uvicorn
uvicorn backend.api.main:app --reload
```

Swagger 文档：http://127.0.0.1:8000/docs

## 常用接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/accounts/{id}/balance` | 查余额 |
| GET | `/api/v1/accounts/{id}/transactions` | 查交易流水 |
| POST | `/api/v1/transfers` | 转账（幂等，带 execution_id） |

## 转账示例

```bash
curl -X POST http://127.0.0.1:8000/api/v1/transfers \
  -H "Content-Type: application/json" \
  -d '{"from_account_id":"6222-0001","to_account_id":"6222-1001","amount_cents":80000,"note":"给妈妈","request_id":"demo-001"}'
```

返回：`{"ok":true,"code":"OK","message":"转账成功","execution_id":"...","data":{...}}`

## 设计说明（答辩点）

- 金额一律用「分」(cents)，不用浮点。
- 每个操作返回唯一 `execution_id`——Agent 只能回显真实返回值（幻觉防护第一道闸）。
- 转账幂等：同一 `request_id` 只执行一次，重复请求返回原结果。
- **本层不做权限判定**：接口是"哑的"，绿黄红权限门在编排层（Agent）调用前强制经过。
