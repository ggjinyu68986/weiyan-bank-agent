"""FastAPI 能力层（Mock Bank HTTP 接口）。

启动：uvicorn backend.api.main:app --reload
Agent 编排层将调用这些接口执行银行业务；权限判定在编排层（上游）。
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from backend.bank_sim.service import BankService

app = FastAPI(title="Weiyan Mock Bank API", version="0.1.0")
service = BankService()


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
