"""Agent 编排器 v0 —— 最小闭环。

链路：用户消息 → LLM 决策(工具+参数) → 权限门判级 → 执行/确认/强验证 → 回显 + 审计。
安全设计（答辩点）：
- 权限门在【执行前】强制经过，即使 LLM 被诱导，越权操作也被拦截（deny/mfa）。
- 确认/强验证后再次过权限门，防止"确认时合法、执行时已越权"的状态变化。
- 每次交互落审计记录（ts/消息/工具/参数/风险/动作/execution_id/结果）。
"""
from __future__ import annotations

import re
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
    "annual_report": lambda svc, p: svc.annual_report(p["account_id"], p.get("year", 2026)),
    # 场景3：理财
    "wealth_products": lambda svc, p: svc.wealth_products(p.get("user_id", 1)),
    "wealth_compare": lambda svc, p: svc.wealth_compare(p.get("product_ids", ["WP-001", "WP-002", "WP-003"])),
    "risk_assessment": lambda svc, p: svc.risk_assessment(p.get("user_id", 1)),
    "buy_wealth": lambda svc, p: svc.buy_wealth(p["user_id"], p["product_id"], p["amount_cents"]),
    "redeem_wealth": lambda svc, p: svc.redeem_wealth(p["user_id"], p["product_id"], p["amount_cents"]),
    # 场景4：卡片管理
    "apply_virtual_card": lambda svc, p: svc.apply_virtual_card(p.get("user_id", 1)),
    "adjust_card_limit": lambda svc, p: svc.adjust_card_limit(p["card_id"], p["new_limit_cents"]),
    "report_card_loss": lambda svc, p: svc.report_card_loss(p["card_id"]),
    "unlock_card": lambda svc, p: svc.unlock_card(p["card_id"]),
    "freeze_card": lambda svc, p: svc.freeze_card(p["card_id"]),
    "unfreeze_card": lambda svc, p: svc.unfreeze_card(p["card_id"]),
    "change_password": lambda svc, p: svc.change_password(p.get("user_id", 1), p.get("new_password", "")),
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


_TOOL_CN = {
    "query_balance": "余额查询", "list_transactions": "交易查询", "analyze_bills": "账单分析",
    "annual_report": "年度账单", "transfer": "转账", "schedule_transfer": "定时转账", "split_bill": "AA收款",
    "wealth_products": "理财查询", "wealth_compare": "理财对比", "risk_assessment": "风险评估",
    "buy_wealth": "理财申购", "redeem_wealth": "理财赎回",
    "apply_virtual_card": "虚拟卡申请", "adjust_card_limit": "额度调整",
    "report_card_loss": "卡片挂失", "unlock_card": "卡片解挂",
    "freeze_card": "卡片冻结", "unfreeze_card": "卡片解冻", "change_password": "密码修改",
    "list_subscriptions": "订阅查询", "cancel_subscription": "取消订阅",
    "detect_subscriptions": "订阅识别", "subscription_reminders": "续费提醒",
    "lock_funds": "资金锁定", "order_gift": "礼品订购",
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


# 异常熔断阈值（赛题：连续失败或可疑行为触发安全锁定）
MFA_FAIL_LIMIT = 3  # 连续输错验证码
SUSPICIOUS_LIMIT = 3  # 连续注入/越权试探


class AgentOrchestrator:
    def __init__(self, llm: BaseLLM | None = None, service: BankService | None = None):
        self.llm = llm or build_llm()
        self.service = service or BankService()
        self.registry = load_registry()
        self.tools = build_tool_schemas()
        self.history: list[dict] = []
        self.audit: list[AuditRecord] = []
        self._pending: dict[str, dict] = {}
        # 会话状态：今日累计转账（日限额升级）+ 熔断计数（安全锁定）
        self.user_state: dict = {
            "today_transfer_cents": 0,
            "mfa_failures": 0,
            "suspicious_count": 0,
            "locked": False,
        }

    def reset(self) -> None:
        """重置会话（演示/测试用）：清历史、清待确认、重置银行数据与锁定；审计保留。"""
        self.history = []
        self.user_state = {
            "today_transfer_cents": 0, "mfa_failures": 0,
            "suspicious_count": 0, "locked": False,
        }
        self._pending = {}
        self.service.store.reset()

    def status(self) -> dict:
        """会话安全状态（前端展示 / 熔断演示）。"""
        return {
            "locked": self.user_state["locked"],
            "mfa_failures": self.user_state["mfa_failures"],
            "suspicious_count": self.user_state["suspicious_count"],
            "mfa_fail_limit": MFA_FAIL_LIMIT,
            "suspicious_limit": SUSPICIOUS_LIMIT,
        }

    def _is_locked(self) -> bool:
        return bool(self.user_state.get("locked"))

    def tick(self, sim_date: str | None = None) -> AgentReply:
        """系统定时器一次拨动（时间沙箱）：执行到期定时转账 + 触发到期事件。
        sim_date 传 YYYY-MM-DD 可模拟任意日期（评测/演示），不传用今天。"""
        r1 = self.service.run_due_scheduled(sim_date)
        r2 = self.service.run_due_events(sim_date)
        self._log("(系统定时触发)", "", {}, "", "system", "",
                  f"定时器拨动{('@' + sim_date) if sim_date else ''}：{r1.message}；{r2.message}")
        return AgentReply("auto", f"{r1.message}；{r2.message}")

    # ---------- 主入口 ----------
    def handle(self, user_msg: str, user_state: dict | None = None) -> AgentReply:
        st = user_state or self.user_state
        # 异常熔断：锁定后全部操作拒绝（含查询），审计留痕
        if st.get("locked"):
            out = AgentReply("deny", "⚠ 账户已安全锁定（连续验证失败或可疑行为），请重置会话或联系人工接管")
            self._log(user_msg, "", {}, "", "lockout", "", out.message)
            self.history.append({"role": "assistant", "content": out.message})
            return out

        self.history.append({"role": "user", "content": user_msg})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *self.history]
        reply = self.llm.complete(messages, tools=self.tools)

        if not reply.tool_calls and not reply.plan:  # 纯对话（追问/澄清/闲聊）
            text = reply.text or "（无可用操作）"
            # 幻觉兜底（第二道防线）：未调工具却输出金额/执行编号/账户号 → 疑似编造，拦截并计入可疑行为
            if _looks_fabricated(text):
                msg = "系统拦截：该回复疑似编造账户信息（未调用任何查询工具）。请重新描述需求，我将通过工具核实。"
                return self._record_suspicious(user_msg, "", {}, msg, st)
            out = AgentReply("chat", text)
            self._log(user_msg, "", {}, "", "chat", "", out.message)
            self.history.append({"role": "assistant", "content": out.message})
            return out

        if reply.plan:  # 跨场景联动：DAG 计划（有序子任务，逐节点过权限门）
            self._log(user_msg, "", {}, "yellow", "plan", "", f"解析为 {len(reply.plan)} 节点 DAG")
            return self._run_plan(reply.plan, user_msg, st)

        tc = reply.tool_calls[0]
        tool, params = tc["name"], tc.get("arguments", {})
        decision = decide(self.registry, tool, params, st)
        risk = decision.spec.get("risk", "?") if decision.spec else "?"
        self._log(user_msg, tool, params, risk, decision.action, "", decision.reason)

        if decision.action == ACTION_AUTO:
            return self._execute(tool, params, user_msg, decision.reason)

        if decision.action in (ACTION_CONFIRM, ACTION_MFA):
            pid = f"p{len(self._pending) + 1}"
            self._pending[pid] = {"kind": "single", "tool": tool, "params": params, "action": decision.action}
            need = "用户确认" if decision.action == ACTION_CONFIRM else "多因子强验证"
            self.history.append({"role": "assistant", "content": f"需要{need}：{decision.reason}"})
            return AgentReply(decision.action, decision.reason, tool=tool, params=params, pending_id=pid)

        # deny（未注册工具/注入试探）→ 累计可疑行为，触发熔断
        return self._record_suspicious(user_msg, tool, params, decision.reason, st)

    # ---------- DAG 计划执行（场景6 跨场景联动） ----------
    def _run_plan(self, plan: list[dict], user_msg: str, user_state: dict) -> AgentReply:
        """按拓扑序推进计划：auto 节点直接执行，confirm/mfa 节点逐个挂起，deny 中止整计划。"""
        self._plan_done: list[tuple[str, str]] = []
        return self._plan_step(plan, 0, user_msg, user_state)

    def _plan_step(self, plan, idx, user_msg, user_state) -> AgentReply:
        i = idx
        while i < len(plan):
            node = plan[i]
            tool, params = node["tool"], node.get("params", {})
            decision = decide(self.registry, tool, params, user_state)
            risk = decision.spec.get("risk", "?") if decision.spec else "?"
            self._log(f"(DAG#{i + 1}){tool}", tool, params, risk, decision.action, "", decision.reason)
            if decision.action == ACTION_AUTO:
                r = self._execute(tool, params, f"(DAG#{i + 1}){user_msg}", decision.reason)
                if r.requires != "auto":
                    return r  # 执行失败（deny）
                self._plan_done.append((tool, r.execution_id))
                i += 1
                continue
            if decision.action in (ACTION_CONFIRM, ACTION_MFA):
                pid = f"p{len(self._pending) + 1}"
                self._pending[pid] = {"kind": "plan", "plan": plan, "idx": i, "tool": tool,
                                      "params": params, "action": decision.action}
                need = "确认" if decision.action == ACTION_CONFIRM else "多因子强验证"
                summary = self._plan_summary(plan)
                msg = f"跨场景计划（{summary}）进行到第 {i + 1}/{len(plan)} 步，需要你的{need}：{decision.reason}"
                self.history.append({"role": "assistant", "content": msg})
                return AgentReply(decision.action, msg, tool=tool, params=params, pending_id=pid)
            # deny（如未注册工具）→ 中止整个计划，已执行节点不受影响
            out = AgentReply("deny", f"计划中止：步骤「{tool}」被权限门拒绝（{decision.reason}）", tool=tool, params=params)
            self.history.append({"role": "assistant", "content": out.message})
            return out
        return AgentReply("auto", self._plan_summary(plan, done=self._plan_done))

    def _plan_summary(self, plan, done=None) -> str:
        names = {n["tool"]: _TOOL_CN.get(n["tool"], n["tool"]) for n in plan}
        outline = " → ".join(names[n["tool"]] for n in plan)
        if done is None:
            return outline
        if not done:
            return f"「{outline}」已全部完成"
        lines = "；".join(f"{_TOOL_CN.get(t, t)}（{e[:8]}）" for t, e in done)
        return f"「{outline}」已全部完成：{lines}"

    # ---------- 确认/强验证后的执行（再次过权限门） ----------
    def confirm(self, pending_id: str, user_state: dict | None = None) -> AgentReply:
        p = self._pending.pop(pending_id, None)
        if not p:
            return AgentReply("deny", "无效的确认凭证，请重新发起操作")
        if p["kind"] == "plan":
            return self._plan_resume(p, user_state or self.user_state)
        decision = decide(self.registry, p["tool"], p["params"], user_state or self.user_state)
        if decision.action != ACTION_CONFIRM:
            return AgentReply(decision.action, decision.reason, tool=p["tool"], params=p["params"])
        return self._execute(p["tool"], p["params"], "(用户确认后执行)", decision.reason)

    def authorize(self, pending_id: str, mfa_code: str = "123456") -> AgentReply:
        """红级强验证：模拟短信/人脸/U盾通过后执行。连续失败触发熔断锁定。"""
        p = self._pending.pop(pending_id, None)
        if not p:
            return AgentReply("deny", "无效的验证凭证，请重新发起操作")
        if mfa_code != "123456":
            self.user_state["mfa_failures"] += 1
            msg = "验证码错误，操作未执行"
            if self.user_state["mfa_failures"] >= MFA_FAIL_LIMIT:
                self.user_state["locked"] = True
                msg = f"验证码连续错误 {MFA_FAIL_LIMIT} 次，账户已安全锁定"
                self._log("(MFA失败)", p["tool"], p["params"], "red", "lockout", "", msg)
            return AgentReply("deny", msg)
        if p["kind"] == "plan":
            return self._plan_resume(p, self.user_state, mfa_ok=True)
        return self._execute(p["tool"], p["params"], "(强验证通过后执行)", "多因子验证通过")

    def _record_suspicious(self, user_msg, tool, params, reason, st) -> AgentReply:
        """注入/编造试探累计：每次落审计，达到阈值触发安全锁定。"""
        st["suspicious_count"] += 1
        self._log(user_msg, tool, params, "red", "suspicious", "", reason)
        out = AgentReply("deny", reason, tool=tool, params=params)
        if st["suspicious_count"] >= SUSPICIOUS_LIMIT:
            st["locked"] = True
            out.message = f"{reason}；已累计 {SUSPICIOUS_LIMIT} 次可疑行为，账户已安全锁定"
            self._log(user_msg, tool, params, "red", "lockout", "", out.message)
        self.history.append({"role": "assistant", "content": out.message})
        return out

    def _plan_resume(self, p: dict, user_state: dict, mfa_ok: bool = False) -> AgentReply:
        """DAG 节点确认/强验证通过后：执行该节点并继续推进计划。"""
        plan, idx = p["plan"], p["idx"]
        tool, params = p["tool"], p["params"]
        decision = decide(self.registry, tool, params, user_state)
        allowed = decision.action in (ACTION_CONFIRM, ACTION_MFA)
        if mfa_ok and decision.action != ACTION_MFA:
            return AgentReply("deny", "该节点无需强验证，操作未执行", tool=tool, params=params)
        if not allowed:
            return AgentReply(decision.action, decision.reason, tool=tool, params=params)
        r = self._execute(tool, params, f"(DAG#{idx + 1} 确认后执行)", decision.reason)
        if r.requires != "auto":
            return r
        self._plan_done.append((tool, r.execution_id))
        return self._plan_step(plan, idx + 1, "(继续执行计划)", user_state)

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


def _looks_fabricated(text: str) -> bool:
    """幻觉兜底判定：纯文字回复中出现"金额/执行编号/账户号"且未调用任何工具 → 疑似编造。

    命中任一样式即拦截（真实查询结果的文案必须走工具返回，不允许模型直接文字输出）：
    - 金额：如 58200.00 元、12,345.67 元
    - 执行编号：如 执行编号 f6668297
    - 账户号：如 6222-0001
    """
    if not text:
        return False
    return bool(
        re.search(r"执行编号\s*[0-9a-fA-F]{6,}", text)
        or re.search(r"\d{1,3}(,\d{3})*\.\d{2}\s*元", text)
        or re.search(r"账户\s*6222-\d{4}", text)
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
        prods = "、".join(f"{p['name']}（年化{p['expected_return'] * 100:.1f}%）" for p in d["products"])
        holdings = "、".join(
            f"{next((q['name'] for q in d['products'] if q['id'] == h['product_id']), h['product_id'])} "
            f"{h['amount_cents'] / 100:.2f}元" for h in d["holdings"]
        ) or "暂无持仓"
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
    if tool == "annual_report":
        months = "、".join(f"{m['month']}月支{abs(m['expense_cents']) / 100:.0f}" for m in d["months"])
        return (
            f"{d['year']}年度账单：总支出 {abs(d['total_expense_cents']) / 100:.2f} 元，"
            f"总收入 {d['total_income_cents'] / 100:.2f} 元，共 {d['month_count']} 个月有交易；"
            f"月度：{months or '无'}；支出最多："
            + "、".join(f"{c['category']} {abs(c['amount_cents']) / 100:.2f}元" for c in d["top_categories"])
        )
    if tool == "risk_assessment":
        matched = "、".join(p["name"] for p in d["matched_products"])
        return f"风险评估：{d['level_cn']}（{d['level']}）。适配产品：{matched}。建议：{d['advice']}"
    if tool == "wealth_compare":
        rows = "、".join(
            f"{c['name']}（年化{c['expected_return'] * 100:.1f}%，风险{c['risk_level']}）" for c in d["compare"]
        )
        return f"产品对比：{rows}；{d['suggestion']}"
    if tool == "change_password":
        return f"{r.message}（执行编号 {r.execution_id[:8]}）"
    if tool in ("freeze_card", "unfreeze_card"):
        return f"{r.message}：{d['card_id']}（状态 {d['status']}）"
    return r.message
