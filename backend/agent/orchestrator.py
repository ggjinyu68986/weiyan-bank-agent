"""Agent 编排器 v0 —— 最小闭环。

链路：用户消息 → LLM 决策(工具+参数) → 权限门判级 → 执行/确认/强验证 → 回显 + 审计。
安全设计（答辩点）：
- 权限门在【执行前】强制经过，即使 LLM 被诱导，越权操作也被拦截（deny/mfa）。
- 确认/强验证后再次过权限门，防止"确认时合法、执行时已越权"的状态变化。
- 每次交互落审计记录（ts/消息/工具/参数/风险/动作/execution_id/结果）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from backend.bank_sim.service import BankService
from backend.registry.loader import load_registry
from backend.security.permission import (
    ACTION_AUTO,
    ACTION_CONFIRM,
    ACTION_DENY,
    ACTION_MFA,
    decide,
)

from .llm import BaseLLM, build_llm
from .prompts import SYSTEM_PROMPT, build_tool_schemas

# 工具 → 银行服务执行器（能力层映射；权限判定不在这里，在上游编排）
EXECUTORS = {
    "query_balance": lambda svc, p: svc.get_balance(p["account_id"]),
    "list_transactions": lambda svc, p: svc.list_transactions(p["account_id"], p.get("limit", 50)),
    "transfer": lambda svc, p: svc.transfer(
        p["from_account_id"], p["to_account_id"], p["amount_cents"], p.get("note", "")
    ),
    "list_subscriptions": lambda svc, p: svc.list_subscriptions(p.get("user_id", 1)),
}


@dataclass
class AgentReply:
    requires: str  # auto / confirm / mfa / deny / chat
    message: str
    execution_id: str = ""
    tool: str = ""
    params: dict = field(default_factory=dict)
    pending_id: str = ""


@dataclass
class AuditRecord:
    ts: datetime
    user_msg: str
    tool: str
    params: dict
    risk: str
    action: str
    execution_id: str
    message: str


class AgentOrchestrator:
    def __init__(self, llm: BaseLLM | None = None, service: BankService | None = None):
        self.llm = llm or build_llm()
        self.service = service or BankService()
        self.registry = load_registry()
        self.tools = build_tool_schemas()
        self.history: list[dict] = []
        self.audit: list[AuditRecord] = []
        self._pending: dict[str, dict] = {}

    # ---------- 主入口 ----------
    def handle(self, user_msg: str, user_state: dict | None = None) -> AgentReply:
        self.history.append({"role": "user", "content": user_msg})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history]
        reply = self.llm.complete(messages, tools=self.tools)

        if not reply.tool_calls:  # 纯对话（追问/澄清/闲聊）
            out = AgentReply("chat", reply.text or "（无可用操作）")
            self._log(user_msg, "", {}, "", "chat", "", out.message)
            self.history.append({"role": "assistant", "content": out.message})
            return out

        tc = reply.tool_calls[0]
        tool, params = tc["name"], tc.get("arguments", {})
        decision = decide(self.registry, tool, params, user_state or {"today_transfer_cents": 0})
        risk = decision.spec.get("risk", "?") if decision.spec else "?"
        self._log(user_msg, tool, params, risk, decision.action, "", decision.reason)

        if decision.action == ACTION_AUTO:
            return self._execute(tool, params, user_msg, decision.reason)

        if decision.action in (ACTION_CONFIRM, ACTION_MFA):
            pid = f"p{len(self._pending) + 1}"
            self._pending[pid] = {"tool": tool, "params": params, "action": decision.action}
            need = "用户确认" if decision.action == ACTION_CONFIRM else "多因子强验证"
            self.history.append({"role": "assistant", "content": f"需要{need}：{decision.reason}"})
            return AgentReply(decision.action, decision.reason, tool=tool, params=params, pending_id=pid)

        # deny（含未注册工具/注入试探）
        self.history.append({"role": "assistant", "content": decision.reason})
        return AgentReply("deny", decision.reason, tool=tool, params=params)

    # ---------- 确认/强验证后的执行（再次过权限门） ----------
    def confirm(self, pending_id: str, user_state: dict | None = None) -> AgentReply:
        p = self._pending.pop(pending_id, None)
        if not p:
            return AgentReply("deny", "无效的确认凭证，请重新发起操作")
        decision = decide(self.registry, p["tool"], p["params"], user_state or {"today_transfer_cents": 0})
        if decision.action != ACTION_CONFIRM:
            return AgentReply(decision.action, decision.reason, tool=p["tool"], params=p["params"])
        return self._execute(p["tool"], p["params"], "(用户确认后执行)", decision.reason)

    def authorize(self, pending_id: str, mfa_code: str = "123456") -> AgentReply:
        """红级强验证：模拟短信/人脸/U盾通过后执行。"""
        p = self._pending.pop(pending_id, None)
        if not p:
            return AgentReply("deny", "无效的验证凭证，请重新发起操作")
        if mfa_code != "123456":
            return AgentReply("deny", "验证码错误，操作未执行")
        return self._execute(p["tool"], p["params"], "(强验证通过后执行)", "多因子验证通过")

    # ---------- 内部 ----------
    def _execute(self, tool: str, params: dict, user_msg: str, reason: str) -> AgentReply:
        ex = EXECUTORS.get(tool)
        if not ex:
            out = AgentReply("deny", f"工具「{tool}」已注册但尚未实现")
            self._log(user_msg, tool, params, "?", "deny", "", out.message)
            return out
        r = ex(self.service, params)
        self._log(user_msg, tool, params, "", "execute", r.execution_id, r.message)
        if not r.ok:
            out = AgentReply("deny", f"执行失败：{r.message}", execution_id=r.execution_id, tool=tool, params=params)
            self.history.append({"role": "assistant", "content": out.message})
            return out
        out = AgentReply("auto", _summarize(tool, r), execution_id=r.execution_id, tool=tool, params=params)
        self.history.append({"role": "assistant", "content": out.message})
        return out

    def _log(self, user_msg, tool, params, risk, action, eid, message):
        self.audit.append(
            AuditRecord(datetime.now(), user_msg, tool, dict(params), risk, action, eid, message)
        )


def _summarize(tool: str, r) -> str:
    """把工具返回值转成用户可读文案（只来自真实返回值，幻觉防护）。"""
    d = r.data
    if tool == "query_balance":
        return f"当前余额：{d['balance_cents'] / 100:.2f} 元（执行编号 {r.execution_id[:8]}）"
    if tool == "list_transactions":
        txs = d["transactions"]
        if not txs:
            return "暂无交易记录。"
        top = txs[0]
        return (
            f"最近 {d['count']} 笔交易。最新一笔：{top['ts'][:10]} "
            f"{top['counterparty']} {top['amount_cents'] / 100:+.2f} 元（{top['note']}）"
        )
    if tool == "transfer":
        return (
            f"转账成功：{d['amount_cents'] / 100:.2f} 元 → 账户 {d['to_account_id']}"
            f"（执行编号 {r.execution_id[:8]}）"
        )
    if tool == "list_subscriptions":
        subs = d["subscriptions"]
        if not subs:
            return "当前没有订阅代扣。"
        lines = "、".join(f"{s['merchant']}（{s['amount_cents'] / 100:.2f} 元/期）" for s in subs)
        return f"共 {d['count']} 项订阅代扣：{lines}"
    return r.message
