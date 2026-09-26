"""FastAPI 能力层（Mock Bank HTTP 接口 + Agent 对话接口）。

启动：uvicorn backend.api.main:app --reload
- Mock Bank 接口：余额 / 流水 / 转账
- Agent 对话接口：chat（人话→LLM→权限门→执行）+ confirm / authorize + audit + reset
权限判定始终在编排层（上游），接口层不做权限判定。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.agent.orchestrator import AgentOrchestrator, AgentReply
from backend.bank_sim.service import BankService

app = FastAPI(title="Weiyan Mock Bank + Agent API", version="0.2.0")

# 本地演示（file:// 打开前端）需要跨域；生产环境应收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

service = BankService()
agent = AgentOrchestrator()


# ---------- Mock Bank 接口 ----------
class TransferRequest(BaseModel):
    from_account_id: str
    to_account_id: str
    amount_cents: int = Field(gt=0, description="金额，单位：分")
    note: str = ""
    request_id: str | None = None


def _resp(r):
    if not r.ok:
        status = 404 if r.code == "ACCOUNT_NOT_FOUND" else 400
        raise HTTPException(status_code=status, detail=r.to_dict())
    return r.to_dict()


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "weiyan-bank-sim"}


@app.get("/api/v1/accounts/{account_id}/balance")
def get_balance(account_id: str):
    return _resp(service.get_balance(account_id))


@app.get("/api/v1/accounts/{account_id}/transactions")
def get_transactions(account_id: str, limit: int = 50):
    return _resp(service.list_transactions(account_id, limit))


@app.post("/api/v1/transfers")
def transfer(req: TransferRequest):
    return _resp(
        service.transfer(
            req.from_account_id,
            req.to_account_id,
            req.amount_cents,
            req.note,
            req.request_id,
        )
    )


# ---------- Agent 对话接口 ----------
class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    pending_id: str


class AuthorizeRequest(BaseModel):
    pending_id: str
    mfa_code: str = "123456"


class TickRequest(BaseModel):
    date: str | None = None  # 时间沙箱：模拟日期 YYYY-MM-DD


def _reply(r: AgentReply) -> dict:
    return {
        "requires": r.requires,
        "message": r.message,
        "execution_id": r.execution_id,
        "tool": r.tool,
        "params": r.params,
        "pending_id": r.pending_id,
    }


@app.post("/api/v1/agent/chat")
def agent_chat(req: ChatRequest):
    return _reply(agent.handle(req.message))


@app.post("/api/v1/agent/confirm")
def agent_confirm(req: ConfirmRequest):
    return _reply(agent.confirm(req.pending_id))


@app.post("/api/v1/agent/authorize")
def agent_authorize(req: AuthorizeRequest):
    return _reply(agent.authorize(req.pending_id, req.mfa_code))


@app.post("/api/v1/agent/reset")
def agent_reset():
    agent.reset()
    return {"ok": True, "message": "会话已重置（银行数据已复原）"}


@app.post("/api/v1/agent/tick")
def agent_tick(req: TickRequest):
    """系统定时器拨动（时间沙箱）：执行到期定时转账 + 触发到期事件。"""
    return _reply(agent.tick(req.date))


@app.get("/api/v1/agent/status")
def agent_status():
    """会话安全状态（异常熔断演示）：锁定 / 失败计数。"""
    return agent.status()


@app.get("/api/v1/agent/audit")
def agent_audit(limit: int = 50):
    """审计日志（决策链路全记录）——演示/答辩面板数据源。"""
    records = [
        {
            "ts": r.ts.isoformat(),
            "user_msg": r.user_msg,
            "tool": r.tool,
            "risk": r.risk,
            "action": r.action,
            "execution_id": r.execution_id,
            "message": r.message,
        }
        for r in agent.audit[-limit:]
    ]
    return {"count": len(records), "records": list(reversed(records))}


@app.get("/api/v1/agent/bills")
def agent_bills(account_id: str = "6222-0001", month: int | None = None):
    """账单分析原始数据（前端 ECharts 可视化数据源）。"""
    r = service.analyze_bills(account_id, month)
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data
