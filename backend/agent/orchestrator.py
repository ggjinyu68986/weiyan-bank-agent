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
# 覆盖赛题 6 大场景：转账家族 / 账单分析 / 理财 / 卡片 / 订阅代扣 / 跨场景联动
EXECUTORS = {
    # 场景1：智能转账
    "transfer": lambda svc, p: svc.transfer(
        p["from_account_id"], p["to_account_id"], p["amount_cents"], p.get("note", "")
    ),
    "schedule_transfer": lambda svc, p: svc.schedule_transfer(
        p["from_account_id"], p["to_account_id"], p["amount_cents"],
        p.get("note", ""), p.get("next_run"), p.get("cycle_days", 0),
    ),
    "split_bill": lambda svc, p: svc.split_bill(
        p["account_id"], p["total_cents"], p["people_count"], p.get("title", "AA收款")
    ),
    # 场景2：账单分析
    "query_balance": lambda svc, p: svc.get_balance(p["account_id"]),
    "list_transactions": lambda svc, p: svc.list_transactions(p["account_id"], p.get("limit", 50)),
    "analyze_bills": lambda svc, p: svc.analyze_bills(p["account_id"], p.get("month")),
    # 场景3：理财
    "wealth_products": lambda svc, p: svc.wealth_products(p.get("user_id", 1)),
    "buy_wealth": lambda svc, p: svc.buy_wealth(p["user_id"], p["product_id"], p["amount_cents"]),
    "redeem_wealth": lambda svc, p: svc.redeem_wealth(p["user_id"], p["product_id"], p["amount_cents"]),
    # 场景4：卡片管理
    "apply_virtual_card": lambda svc, p: svc.apply_virtual_card(p.get("user_id", 1)),
    "adjust_card_limit": lambda svc, p: svc.adjust_card_limit(p["card_id"], p["new_limit_cents"]),
    "report_card_loss": lambda svc, p: svc.report_card_loss(p["card_id"]),
    "unlock_card": lambda svc, p: svc.unlock_card(p["card_id"]),
    # 场景5：订阅代扣
    "list_subscriptions": lambda svc, p: svc.list_subscriptions(p.get("user_id", 1)),
    "cancel_subscription": lambda svc, p: svc.cancel_subscription(p["subscription_id"]),
    "detect_subscriptions": lambda svc, p: svc.detect_subscriptions(p.get("account_id", "6222-0001")),
    "subscription_reminders": lambda svc, p: svc.subscription_reminders(p.get("user_id", 1)),
    # 场景6：跨场景联动
    "lock_funds": lambda svc, p: svc.lock_funds(p["account_id"], p["amount_cents"], p.get("note", "")),
    "order_gift": lambda svc, p: svc.order_gift(
        p["account_id"], p["merchant"], p["amount_cents"], p.get("note", "")
    ),
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
        # 会话内"今日累计转账"跟踪：超 1000 元自动升级红级（黄→红）
        self.user_state: dict = {"today_transfer_cents": 0}

    def reset(self) -> None:
        """重置会话（演示/测试用）：清历史、清待确认、重置银行数据；审计保留。"""
        self.history = []
        self.user_state = {"today_transfer_cents": 0}
        self._pending = {}
        self.service.store.reset()

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
        decision = decide(self.registry, tool, params, user_state or self.user_state)
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
        decision = decide(self.registry, p["tool"], p["params"], user_state or self.user_state)
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
        # 转账成功 → 计入今日累计（日限额升级的依据）
        if tool == "transfer":
            self.user_state["today_transfer_cents"] += params.get("amount_cents", 0)
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
    if tool == "schedule_transfer":
        return (
            f"已登记定时转账：{d['amount_cents'] / 100:.2f} 元 → 账户 {d['to_account_id']}"
            f"，下次执行 {d['next_run']}（执行编号 {r.execution_id[:8]}）"
        )
    if tool == "split_bill":
        return (
            f"AA 收款单已生成：「{d['title']}」共 {d['people_count']} 人，"
            f"每人 {d['per_person_cents'] / 100:.2f} 元，总计 {d['total_cents'] / 100:.2f} 元"
        )
    if tool == "analyze_bills":
        cats = "、".join(f"{c['category']} {abs(c['amount_cents']) / 100:.2f}元" for c in d["by_category"])
        return (
            f"{d['period']}账单：支出 {abs(d['total_expense_cents']) / 100:.2f} 元，"
            f"收入 {d['total_income_cents'] / 100:.2f} 元；分类：{cats or '无支出'}；"
            f"发现 {d['anomaly_count']} 笔异常："
            + "；".join(f"{a['counterparty']}（{a['reason']}）" for a in d["anomalies"])
        )
    if tool == "wealth_products":
        prods = "、".join(f"{p['name']}（年化{p['rate']}）" for p in d["products"])
        holdings = "、".join(f"{p['name']} {p['amount_cents'] / 100:.2f}元" for p in d["holdings"]) or "暂无持仓"
        return f"在售 {len(d['products'])} 款产品：{prods}；当前持仓：{holdings}"
    if tool in ("buy_wealth", "redeem_wealth"):
        return f"{r.message}：{d['product']} {d['amount_cents'] / 100:.2f} 元"
    if tool == "apply_virtual_card":
        return f"虚拟卡申请成功：{d['card_id']}（日限额 {d['daily_limit_cents'] / 100:.2f} 元）"
    if tool == "adjust_card_limit":
        return f"卡片 {d['card_id']} 日限额已调整为 {d['daily_limit_cents'] / 100:.2f} 元"
    if tool in ("report_card_loss", "unlock_card"):
        return f"{r.message}：{d['card_id']}（状态 {d['status']}）"
    if tool == "cancel_subscription":
        return f"{r.message}（执行编号 {r.execution_id[:8]}）"
    if tool == "detect_subscriptions":
        if not d["detected"]:
            return "未识别到周期性订阅扣费。"
        lines = "、".join(f"{x['merchant']}（{x['count']}笔/{x['total_cents'] / 100:.2f}元）" for x in d["detected"])
        return f"识别到 {d['count']} 项订阅扣费：{lines}"
    if tool == "subscription_reminders":
        if not d["reminders"]:
            return "暂无待续费订阅。"
        lines = "、".join(
            f"{x['merchant']}（{x['next_bill_date']}，{x['amount_cents'] / 100:.2f} 元"
            + ("，即将到期" if x["urgent"] else "") + "）"
            for x in d["reminders"]
        )
        return f"续费提醒：{lines}"
    if tool == "lock_funds":
        return f"{r.message}，当前已锁定 {d['locked_cents'] / 100:.2f} 元，可用余额 {d['available_cents'] / 100:.2f} 元"
    if tool == "order_gift":
        return f"订购成功：{d['merchant']} {d['amount_cents'] / 100:.2f} 元（订单 {d['order_id']}）"
    return r.message
