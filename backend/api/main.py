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
from backend.agent.users import DEFAULT_USER, VIEW_USERS
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
# 关键：Agent 与查询接口必须共用同一个 BankService 实例（同一份 store），
# 否则"转账扣款"发生在 agent 私有 store、查询读的是另一份数据 → 余额不变（曾为真机演示 bug）。
# 多用户视角：每个用户独立 Agent 会话（独立历史/熔断/待确认），共享同一份银行数据——
# AA 多方协作演示：张伟在自己的会话里查待付并付款，小明端进度实时联动。
_agents: dict[str, AgentOrchestrator] = {}


def agent_for(user: str = DEFAULT_USER) -> AgentOrchestrator:
    """取（或创建）指定视角的 Agent 会话。"""
    if user not in VIEW_USERS:
        raise HTTPException(status_code=400, detail=f"未知视角用户：{user}（可用：{'、'.join(VIEW_USERS)}）")
    if user not in _agents:
        uid, acc = VIEW_USERS[user]
        _agents[user] = AgentOrchestrator(service=service, user_id=uid, account_id=acc, user_name=user)
    return _agents[user]


def _rebuild_agents() -> None:
    """重置演示：所有用户会话重建（共享 store 复位一次）。"""
    service.store.reset()
    _agents.clear()


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


@app.get("/api/v1/accounts/{account_id}/cards")
def get_cards(account_id: str):
    """我的卡列表（卡片页数据源）。"""
    acc = service.store.accounts.get(account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="ACCOUNT_NOT_FOUND")
    return _resp(service.list_cards(acc.user_id))


# ---------- 联系人簿（场景1：按人名转账的解析依据） ----------
class ContactReq(BaseModel):
    name: str
    account_id: str
    phone: str = ""
    aliases: list[str] = []
    relation: str = ""


@app.get("/api/v1/contacts")
def get_contacts():
    """我的联系人列表。"""
    return _resp(service.list_contacts(1))


@app.post("/api/v1/contacts")
def add_contact(req: ContactReq):
    """添加联系人（绑定收款账户，此后可按人名转账）。"""
    return _resp(
        service.add_contact(req.name, req.account_id, req.phone, req.aliases, req.relation)
    )


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
    user: str = DEFAULT_USER  # 视角用户（AA 多方协作：谁登录就是谁的账户）


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
        "data": r.data,  # 工具返回的结构化数据（前端可视化：AA 收款进度卡片等）
        "decision": r.decision,  # 双引擎判定证据（规则 ⊕ JEV 置信度，前端展示）
    }


@app.post("/api/v1/agent/chat")
def agent_chat(req: ChatRequest):
    return _reply(agent_for(req.user).handle(req.message))


@app.post("/api/v1/agent/confirm")
def agent_confirm(req: ConfirmRequest, user: str = DEFAULT_USER):
    return _reply(agent_for(user).confirm(req.pending_id))


@app.post("/api/v1/agent/authorize")
def agent_authorize(req: AuthorizeRequest, user: str = DEFAULT_USER):
    return _reply(agent_for(user).authorize(req.pending_id, req.mfa_code))


@app.post("/api/v1/agent/reset")
def agent_reset():
    _rebuild_agents()
    return {"ok": True, "message": "会话已重置（银行数据已复原）"}


@app.post("/api/v1/agent/tick")
def agent_tick(req: TickRequest):
    """系统定时器拨动（时间沙箱）：执行到期定时转账 + 触发到期事件（小明视角）。"""
    return _reply(agent_for(DEFAULT_USER).tick(req.date))


@app.get("/api/v1/agent/status")
def agent_status(user: str = DEFAULT_USER):
    """会话安全状态（异常熔断演示）：锁定 / 失败计数。"""
    return agent_for(user).status()


@app.get("/api/v1/agent/audit")
def agent_audit(limit: int = 50, user: str = DEFAULT_USER):
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
            "decision": r.decision,  # 双引擎判定证据（规则 ⊕ JEV），审计面板可视化
        }
        for r in agent_for(user).audit[-limit:]
    ]
    return {"count": len(records), "records": list(reversed(records))}


@app.get("/api/v1/agent/users")
def agent_users():
    """视角用户列表（前端视角切换器数据源）。"""
    return {"users": [{"name": n, "user_id": u, "account_id": a} for n, (u, a) in VIEW_USERS.items()],
            "default": DEFAULT_USER}


@app.get("/api/v1/agent/pending-splits")
def agent_pending_splits(account_id: str = "6222-0001"):
    """我的待付 AA 分摊（对端视角数据，前端待付卡数据源）。"""
    r = service.list_pending_splits(account_id)
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data


@app.get("/api/v1/agent/risk/questions")
def agent_risk_questions():
    """风险评估问卷（KYC 适当性）：6 题 + 选项，前端答题弹层数据源。"""
    r = service.risk_questionnaire()
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data


class RiskSubmitRequest(BaseModel):
    user_id: int = 1
    answers: dict


@app.post("/api/v1/agent/risk/submit")
def agent_risk_submit(req: RiskSubmitRequest):
    """提交问卷答案：走 Agent 编排（绿级自动 + 审计），返回等级/适配产品/风险提示。"""
    return _reply(agent_for(DEFAULT_USER).submit_risk(req.answers, req.user_id))


@app.get("/api/v1/agent/wealth")
def agent_wealth(user_id: int = 1):
    """理财 Tab 数据源：在售产品 + 我的持仓。"""
    r = service.wealth_products(user_id)
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data


@app.get("/api/v1/agent/wealth/recommend")
def agent_wealth_recommend(user_id: int = 1):
    """理财推荐（绿级）：风险等级 + 持仓 + 可用余额 → Top 3 推荐（理由/参考投入/示例收益）。"""
    r = service.wealth_recommend(user_id)
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data


@app.get("/api/v1/agent/wealth/compare")
def agent_wealth_compare(product_ids: str = "WP-001,WP-002"):
    """理财对比：按 ID 列表横向比较（收益/风险/起购）+ 结论建议。"""
    r = service.wealth_compare([x.strip() for x in product_ids.split(",") if x.strip()])
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data


@app.get("/api/v1/agent/annual")
def agent_annual(account_id: str = "6222-0001", year: int = 2026):
    """年度账单报告数据（前端年度视图数据源：按月收支趋势 + 年度分类 Top）。"""
    r = service.annual_report(account_id, year)
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data


@app.get("/api/v1/agent/bills")
def agent_bills(account_id: str = "6222-0001", month: int | None = None):
    """账单分析原始数据（前端 ECharts 可视化数据源）。"""
    r = service.analyze_bills(account_id, month)
    if not r.ok:
        raise HTTPException(status_code=400, detail=r.to_dict())
    return r.data
