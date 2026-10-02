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

from .llm import BaseLLM, build_llm, LLMReply
from .prompts import SYSTEM_PROMPT, build_tool_schemas
from .timeexpr import resolve as resolve_time_expr
from .decision import ACTION_TO_GRADE, DecisionEngine, Verdict, _merge_grade

_WEEK_CN = "一二三四五六日"


def _system_prompt(user_name: str = "小明", account: str = "6222-0001") -> str:
    """系统提示词：实时注入当前日期与当前用户上下文（姓名/账户）。
    多用户视角（AA 对端/IM 渠道切换用户）依赖注入：模型知道"我是谁、我的账户是哪个"，
    查询与付款默认落到当前账户，而不是写死的单一用户。"""
    now = datetime.now()
    today = f"{now:%Y-%m-%d}（周{_WEEK_CN[now.weekday()]}）"
    return (SYSTEM_PROMPT.replace("{TODAY}", today)
                          .replace("{USER_NAME}", user_name)
                          .replace("{ACCOUNT}", account))

# 工具 → 银行服务执行器（能力层映射；权限判定不在这里，在上游编排）
# 覆盖赛题 6 大场景：转账家族 / 账单分析 / 理财 / 卡片 / 订阅代扣 / 跨场景联动


def _next_run(p: dict) -> str | None:
    """定时转账首次执行日：明确日期(模型从话中提取) > 相对表达式(系统换算) > 默认锚点。
    模型永远不自己算日期——日期换算全部走确定性时间解析器（幻觉防护）。"""
    if p.get("next_run"):
        return p["next_run"]
    if p.get("next_run_expr"):
        d = resolve_time_expr(p["next_run_expr"])
        return d.isoformat() if d else None
    return None
EXECUTORS = {
    # 场景1：智能转账
    "list_contacts": lambda svc, p: svc.list_contacts(p.get("user_id", 1)),
    "add_contact": lambda svc, p: svc.add_contact(
        p["name"], p["account_id"], p.get("phone", ""),
        p.get("aliases", []), p.get("relation", ""), p.get("user_id", 1),
    ),
    "transfer": lambda svc, p: svc.transfer(
        p["from_account_id"], p["to_account_id"], p["amount_cents"], p.get("note", "")
    ),
    "schedule_transfer": lambda svc, p: svc.schedule_transfer(
        p["from_account_id"], p["to_account_id"], p["amount_cents"],
        p.get("note", ""), _next_run(p), p.get("cycle_days", 0),
    ),
    "split_bill": lambda svc, p: svc.split_bill(
        p["account_id"], p["total_cents"], p["people_count"], p.get("title", "AA收款"),
        p.get("payer_accounts"),
    ),
    "split_bill_status": lambda svc, p: svc.split_bill_status(p["account_id"], p.get("bill_id", "")),
    "pay_split_bill": lambda svc, p: svc.pay_split_bill(
        p["account_id"], p["payer_account_id"], p.get("bill_id", ""), p.get("request_id"),
    ),
    "list_pending_splits": lambda svc, p: svc.list_pending_splits(p["account_id"]),
    # 场景2：账单分析
    "query_balance": lambda svc, p: svc.get_balance(p["account_id"]),
    "list_transactions": lambda svc, p: svc.list_transactions(p["account_id"], p.get("limit", 50)),
    "analyze_bills": lambda svc, p: svc.analyze_bills(p["account_id"], p.get("month")),
    "annual_report": lambda svc, p: svc.annual_report(p["account_id"], p.get("year", 2026)),
    # 场景3：理财
    "wealth_products": lambda svc, p: svc.wealth_products(p.get("user_id", 1)),
    "wealth_compare": lambda svc, p: svc.wealth_compare(p.get("product_ids", ["WP-001", "WP-002", "WP-003"])),
    "risk_assessment": lambda svc, p: svc.risk_assessment(p.get("user_id", 1)),
    "risk_questionnaire": lambda svc, p: svc.risk_questionnaire(),
    "risk_submit": lambda svc, p: svc.risk_submit(p.get("user_id", 1), p.get("answers", {})),
    "wealth_recommend": lambda svc, p: svc.wealth_recommend(p.get("user_id", 1)),
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
    "split_bill_status": "AA收款进度", "pay_split_bill": "AA收款入账",
    "list_contacts": "联系人查询", "add_contact": "添加联系人",
    "wealth_products": "理财查询", "wealth_compare": "理财对比", "wealth_recommend": "理财推荐", "risk_assessment": "风险评估",
    "risk_questionnaire": "风险评估问卷", "risk_submit": "风险评估提交",
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
    data: dict = field(default_factory=dict)  # 工具返回的结构化数据（前端可视化卡片，如 AA 进度）
    decision: dict = field(default_factory=dict)  # 双引擎判定证据（规则 ⊕ JEV 置信度，前端展示）


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
    decision: dict = field(default_factory=dict)  # 该次判定的规则⊕JEV 证据（答辩可视化）


# 异常熔断阈值（赛题：连续失败或可疑行为触发安全锁定）
MFA_FAIL_LIMIT = 3  # 连续输错验证码
SUSPICIOUS_LIMIT = 3  # 连续注入/越权试探

# 对话式风险评估问卷触发词（命中后系统逐题询问，不走 LLM 解析，保证选项准确）
RISK_QUIZ_TRIGGERS = ("做风险评估", "做一下风险评估", "风险测评", "风险问卷", "测一测", "测评", "评估一下", "风险评估")
# 含查询意图时不算"开始问卷"（如"查风险评估结果/我的风险等级"走 LLM 查询）
RISK_QUIZ_EXCLUDE = ("查看", "看看", "查询", "结果", "等级", "我的风险")


def _match_quiz_option(user_msg: str, options: list) -> str | None:
    """问卷选项容错匹配：忽略空格（"5年以上"→"5 年以上"）、支持序号（"2"→第 2 项）。"""
    norm = "".join(user_msg.split()).lower()
    for o in options:
        if "".join(o["label"].split()).lower() == norm:
            return o["label"]
    if user_msg.strip().isdigit():
        idx = int(user_msg.strip())
        if 1 <= idx <= len(options):
            return options[idx - 1]["label"]
    return None


class AgentOrchestrator:
    def __init__(self, llm: BaseLLM | None = None, service: BankService | None = None,
                 decision: DecisionEngine | None = None,
                 user_id: int = 1, account_id: str = "6222-0001", user_name: str = "小明"):
        self.llm = llm or build_llm()
        self.service = service or BankService()
        self.decision = decision or DecisionEngine()  # 双引擎决策层（off/mock/live）
        self.user_id = user_id
        self.account_id = account_id
        self.user_name = user_name
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
        sim_date 传 YYYY-MM-DD 可模拟任意日期（评测/演示），不传用今天。
        返回扣款明细，让用户确认"钱确实动了"（前端资产卡同步刷新）。"""
        r1 = self.service.run_due_scheduled(sim_date)
        r2 = self.service.run_due_events(sim_date)
        parts = [r1.message]
        for e in r1.data.get("executed", []):
            mark = "✓" if e.get("ok") else "✗ " + e.get("message", "")
            parts.append(f"{e['amount_cents'] / 100:.2f} 元 → {e.get('to_account_id', '')} {mark}")
        parts.append(r2.message)
        text = "；".join(parts)
        self._log("(系统定时触发)", "", {}, "", "system", "",
                  f"定时器拨动{('@' + sim_date) if sim_date else ''}：{text}")
        return AgentReply("auto", text)

    def submit_risk(self, answers: dict, user_id: int = 1) -> AgentReply:
        """风险评估问卷提交（前端答题 → 系统计分判定，绿级自动执行 + 全量审计）。
        不走 LLM（问卷交互在前端），但复用权限门语义与审计链路，保证"所有关键操作可追溯"。"""
        if self.user_state.get("locked"):
            out = AgentReply("deny", "⚠ 账户已安全锁定，请重置会话或联系人工接管")
            self._log("提交风险评估问卷", "risk_submit", answers, "green", "lockout", "", out.message)
            return out
        r = self.service.risk_submit(user_id, answers)
        if not r.ok:
            return AgentReply("chat", r.message)
        d = r.data
        matched = "、".join(p["name"] for p in d["matched_products"])
        self._log("提交风险评估问卷", "risk_submit", answers, "green", "auto", "",
                  f"{d['level_cn']}（{d['score']} 分），适配：{matched}")
        return AgentReply(
            "auto",
            f"风险评估完成：{d['level_cn']}（{d['score']} 分）。适配产品：{matched}。{d['advice']} {d['risk_warning']}",
            tool="risk_submit", data=d,
        )

    def _advance_risk_quiz(self, user_msg: str, st: dict) -> AgentReply:
        """问卷进行中的一轮：校验选项 → 存答案 → 下一题或计分提交；支持取消与错误重问。"""
        quiz = st.get("risk_quiz")
        if any(k in user_msg for k in ("取消", "退出", "算了", "不做", "跳过")):
            st.pop("risk_quiz", None)
            self._log(user_msg, "risk_quiz", {}, "green", "cancel", "", "风险评估问卷中断（用户取消）")
            return AgentReply("chat", "已取消风险评估，随时可以说「做风险评估」重新开始。")
        q = self.service.RISK_QUESTIONS[quiz["q_index"]]
        labels = [o["label"] for o in q["options"]]
        matched = _match_quiz_option(user_msg, q["options"])
        if matched is None:
            return AgentReply("chat",
                f"第 {quiz['q_index'] + 1}/6 题：{q['text']}\n请从以下选项回复一项：{'、'.join(labels)}（或回复「取消」退出）",
                data={"quiz": {"q_index": quiz["q_index"] + 1, "total": len(self.service.RISK_QUESTIONS),
                              "text": q["text"], "options": labels}})
        quiz["answers"][q["id"]] = matched
        quiz["q_index"] += 1
        self._log(user_msg, "risk_quiz", {q["id"]: user_msg}, "green", "quiz", "",
                  f"问卷进度 {quiz['q_index']}/{len(self.service.RISK_QUESTIONS)}")
        if quiz["q_index"] >= len(self.service.RISK_QUESTIONS):
            st.pop("risk_quiz", None)
            r = self.service.risk_submit(self.user_id, quiz["answers"])
            if not r.ok:
                return AgentReply("chat", r.message)
            d = r.data
            matched = "、".join(p["name"] for p in d["matched_products"])
            self._log("风险评估问卷完成", "risk_submit", quiz["answers"], "green", "auto", "",
                      f"{d['level_cn']}（{d['score']} 分），适配：{matched}")
            return AgentReply(
                "auto",
                f"✅ 风险评估完成：{d['level_cn']}（{d['score']} 分）。适配产品：{matched}。{d['advice']} {d['risk_warning']}",
                tool="risk_submit", data=d,
            )
        nq = self.service.RISK_QUESTIONS[quiz["q_index"]]
        return AgentReply("chat",
            f"已记录（{quiz['q_index']}/6）。下一题：{nq['text']}\n请回复选项文字或序号，或直接点下方选项：{'、'.join(o['label'] for o in nq['options'])}",
            data={"quiz": {"q_index": quiz["q_index"] + 1, "total": len(self.service.RISK_QUESTIONS),
                          "text": nq["text"], "options": [o["label"] for o in nq["options"]]}})

    def request_operation(self, tool: str, params: dict) -> AgentReply:
        """确定性操作入口（按钮一键，不依赖 LLM）：工具 → 权限门 → 确认/MFA → 执行，与对话同一审计链路。"""
        reply = LLMReply(tool_calls=[{"name": tool, "arguments": params}])
        return self._route_tool(reply, f"[按钮] {tool}", self.user_state)

    # ---------- 主入口 ----------
    def handle(self, user_msg: str, user_state: dict | None = None) -> AgentReply:
        st = user_state or self.user_state
        # 异常熔断：锁定后全部操作拒绝（含查询），审计留痕
        if st.get("locked"):
            out = AgentReply("deny", "⚠ 账户已安全锁定（连续验证失败或可疑行为），请重置会话或联系人工接管")
            self._log(user_msg, "", {}, "", "lockout", "", out.message)
            self.history.append({"role": "assistant", "content": out.message})
            return out

        # 对话式风险评估问卷：进行中 → 本轮消息视为答案（支持取消/错误重问）
        if st.get("risk_quiz"):
            out = self._advance_risk_quiz(user_msg, st)
            self.history.append({"role": "user", "content": user_msg})
            self.history.append({"role": "assistant", "content": out.message})
            return out
        if any(k in user_msg for k in RISK_QUIZ_TRIGGERS) and not any(e in user_msg for e in RISK_QUIZ_EXCLUDE):
            st["risk_quiz"] = {"answers": {}, "q_index": 0}
            q0 = self.service.RISK_QUESTIONS[0]
            self._log(user_msg, "risk_quiz", {}, "green", "quiz", "", "开始风险评估问卷（1/6）")
            opts = "、".join(o["label"] for o in q0["options"])
            out = AgentReply("chat", f"开始风险评估（1/6）：{q0['text']}\n可回复选项文字或序号（如「1」），也可以直接点下方选项。\n{opts}",
                             data={"quiz": {"q_index": 1, "total": len(self.service.RISK_QUESTIONS),
                                           "text": q0["text"],
                                           "options": [o["label"] for o in q0["options"]]}})
            self.history.append({"role": "user", "content": user_msg})
            self.history.append({"role": "assistant", "content": out.message})
            return out

        self.history.append({"role": "user", "content": user_msg})
        messages = [{"role": "system", "content": _system_prompt(self.user_name, self.account_id)}, *self.history]
        reply = self.llm.complete(messages, tools=self.tools)

        if not reply.tool_calls and not reply.plan:  # 纯对话（追问/澄清/闲聊）
            text = reply.text or "（无可用操作）"
            is_operation = any(k in user_msg for k in OPERATION_HINTS)
            is_query = any(k in user_msg for k in QUERY_HINTS)
            if is_operation or is_query:
                # 查询/操作类请求模型未调工具（含疑似编造文字）→ 一律先内部自动重试一次。
                # 编造内容永不透传给用户——铁证编造也先重试（模型偶发抽风），
                # 重试成功即呈现真实数据（用户无感）；重试后仍编造才拦截并计可疑。
                self.history.append({
                    "role": "assistant",
                    "content": "（系统提示）你请求的业务必须调用工具完成（查询类如 query_balance/list_subscriptions，"
                               "操作类如 transfer/split_bill/cancel_subscription 等），请立即调用对应工具，"
                               "不要以文字复述金额、不要仅回复确认、不要编造查询结果。",
                })
                self._log(user_msg, "", {}, "", "retry", "", "查询/操作类请求未调工具，系统自动重试一次")
                retry = self.llm.complete(
                    [{"role": "system", "content": _system_prompt(self.user_name, self.account_id)}, *self.history], tools=self.tools
                )
                if retry.tool_calls or retry.plan:
                    return self._route_tool(retry, user_msg, st)
                # 重试后仍未调工具：双引擎校验（规则铁证 / JEV 高置信）→ 拦截并计入可疑行为（与熔断联动）
                fab, jev = self._fab_check(retry.text or "")
                if fab:
                    msg = "系统拦截：检测到未通过工具执行的账户信息或交易结果（疑似编造）。请重新描述需求，我将通过工具核实办理。"
                    return self._record_suspicious(user_msg, "", {}, msg, st, jev)
                fo = _query_failover(user_msg, self.account_id, self.user_id)
                if fo:
                    tool, params = fo
                    # 规则兜底路由：模型连续未调工具时，命中明确查询意图关键词直接执行真实查询
                    # （绿级权限门 + 全量审计；参数锁定当前账户，结果全部来自工具，不存在编造）
                    self._log(user_msg, tool, params, "", "failover", "",
                              f"模型未调用工具，规则兜底路由命中「{tool}」，直接执行真实查询")
                    return self._route_tool(_FO_REPLY(tool, params), user_msg, st)
                if is_operation:
                    out = AgentReply(
                        "chat",
                        "我还没有执行任何操作。请允许我通过工具为你办理——你可以再对我说一次，我会先展示操作详情待你确认。",
                    )
                else:
                    out = AgentReply(
                        "chat",
                        "我还没有执行任何查询。请允许我通过工具为你核实——你可以再对我说一次，我会调用查询工具获取真实数据。",
                    )
                self._log(user_msg, "", {}, "", "chat", "", out.message)
                self.history.append({"role": "assistant", "content": out.message})
                return out
            # 纯闲聊（非查询/操作）：铁证编造（执行编号/账户号——系统唯一生成物）立即拦截并计入可疑行为
            fab, jev = self._fab_check(text)
            if fab:
                msg = "系统拦截：检测到未通过工具执行的账户信息或交易结果（疑似编造）。请重新描述需求，我将通过工具核实办理。"
                return self._record_suspicious(user_msg, "", {}, msg, st, jev)
            out = AgentReply("chat", text)
            self._log(user_msg, "", {}, "", "chat", "", out.message)
            self.history.append({"role": "assistant", "content": out.message})
            return out

        return self._route_tool(reply, user_msg, st)

    def _route_tool(self, reply, user_msg: str, user_state: dict) -> AgentReply:
        """工具调用/计划路由：DAG 计划或单工具 → 权限门 → 挂起/执行/拒绝。"""
        st = user_state
        if reply.plan:  # 跨场景联动：DAG 计划（有序子任务，逐节点过权限门）
            self._log(user_msg, "", {}, "yellow", "plan", "", f"解析为 {len(reply.plan)} 节点 DAG")
            return self._run_plan(reply.plan, user_msg, st)

        tc = reply.tool_calls[0]
        tool, params = tc["name"], tc.get("arguments", {})
        decision, jev = self._decide(tool, params, st)
        risk = decision.spec.get("risk", "?") if decision.spec else "?"
        route = self.decision.route_check(user_msg, tool, risk)  # 路由校验（记录型）
        self._log(user_msg, tool, params, risk, decision.action, "", decision.reason,
                  {"grade": jev.to_dict() if jev else {}, "route": route.to_dict() if route.engine != "rule" else {}})

        if decision.action == ACTION_AUTO:
            return self._execute(tool, params, user_msg, decision.reason)

        if decision.action in (ACTION_CONFIRM, ACTION_MFA):
            pid = f"p{len(self._pending) + 1}"
            self._pending[pid] = {"kind": "single", "tool": tool, "params": params, "action": decision.action}
            need = "用户确认" if decision.action == ACTION_CONFIRM else "多因子强验证"
            self.history.append({"role": "assistant", "content": f"需要{need}：{decision.reason}"})
            return AgentReply(decision.action, decision.reason, tool=tool, params=params,
                              pending_id=pid, decision={"grade": jev.to_dict() if jev else {}})

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
            decision, jev = self._decide(tool, params, user_state)
            risk = decision.spec.get("risk", "?") if decision.spec else "?"
            self._log(f"(DAG#{i + 1}){tool}", tool, params, risk, decision.action, "", decision.reason,
                      {"grade": jev.to_dict() if jev else {}})
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
        decision, jev = self._decide(p["tool"], p["params"], user_state or self.user_state)
        if decision.action != ACTION_CONFIRM:
            return AgentReply(decision.action, decision.reason, tool=p["tool"], params=p["params"],
                              decision={"grade": jev.to_dict() if jev else {}})
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

    def _record_suspicious(self, user_msg, tool, params, reason, st, decision=None) -> AgentReply:
        """注入/编造试探累计：每次落审计，达到阈值触发安全锁定。"""
        st["suspicious_count"] += 1
        self._log(user_msg, tool, params, "red", "suspicious", "", reason, decision)
        out = AgentReply("deny", reason, tool=tool, params=params)
        if st["suspicious_count"] >= SUSPICIOUS_LIMIT:
            st["locked"] = True
            out.message = f"{reason}；已累计 {SUSPICIOUS_LIMIT} 次可疑行为，账户已安全锁定"
            self._log(user_msg, tool, params, "red", "lockout", "", out.message, decision)
        self.history.append({"role": "assistant", "content": out.message})
        return out

    def _plan_resume(self, p: dict, user_state: dict, mfa_ok: bool = False) -> AgentReply:
        """DAG 节点确认/强验证通过后：执行该节点并继续推进计划。"""
        plan, idx = p["plan"], p["idx"]
        tool, params = p["tool"], p["params"]
        decision, jev = self._decide(tool, params, user_state)
        allowed = decision.action in (ACTION_CONFIRM, ACTION_MFA)
        if mfa_ok and decision.action != ACTION_MFA:
            return AgentReply("deny", "该节点无需强验证，操作未执行", tool=tool, params=params)
        if not allowed:
            return AgentReply(decision.action, decision.reason, tool=tool, params=params,
                              decision={"grade": jev.to_dict() if jev else {}})
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
            out = AgentReply("deny", f"执行失败：{r.message}", execution_id=r.execution_id, tool=tool, params=params, data=r.data)
            self.history.append({"role": "assistant", "content": out.message})
            return out
        # 转账成功 → 计入今日累计（日限额升级的依据）
        if tool == "transfer":
            self.user_state["today_transfer_cents"] += params.get("amount_cents", 0)
        out = AgentReply("auto", _summarize(tool, r), execution_id=r.execution_id, tool=tool, params=params, data=r.data)
        self.history.append({"role": "assistant", "content": out.message})
        return out

    def _decide(self, tool: str, params: dict, st: dict):
        """权限门：确定性规则判定 → JEV 双引擎保守合并。
        规则是底线：JEV 只能更严格（live 且置信度达标才升级），绝不降级/放行。"""
        decision = decide(self.registry, tool, params, st)
        if decision.action not in (ACTION_AUTO, ACTION_CONFIRM, ACTION_MFA):
            return decision, Verdict("red", 1.0, "rule")
        grade = ACTION_TO_GRADE[decision.action]
        jev = self.decision.grade_operation(tool, params, grade)
        merged = _merge_grade(grade, jev)
        if merged.value != grade:  # 仅 live 高置信时发生（mock 镜像不升级）
            if merged.value == "yellow":
                decision.action = ACTION_CONFIRM
            elif merged.value == "red":
                decision.action = ACTION_MFA
            decision.reason = f"{decision.reason}（JEV 建议更严格：{merged.value}，置信 {merged.confidence:.0%}，已保守合并）"
            jev = merged
        return decision, jev

    def _fab_check(self, text: str) -> tuple[bool, Verdict]:
        """幻觉校验双引擎：规则铁证为硬底线，JEV 概率层在置信达标时追加拦截。"""
        rule = _looks_fabricated(text)
        jev = self.decision.fabrication_check(text, rule)
        flag = rule or (jev.value == "fabricated" and jev.confidence >= self.decision.threshold)
        return flag, jev

    def _log(self, user_msg, tool, params, risk, action, eid, message, decision=None):
        self.audit.append(
            AuditRecord(datetime.now(), user_msg, tool, dict(params), risk, action, eid, message,
                        decision.to_dict() if isinstance(decision, Verdict) else (decision or {}))
        )


# 查询类 / 操作类提示词（用于区分"编造查询结果"与"操作类文字复述"）
QUERY_HINTS = ("余额", "流水", "账单", "年度", "收益", "评估", "对比", "推荐", "明细", "查询", "查", "看看",
               "还剩", "多少钱", "多少", "订阅", "代扣", "理财", "持仓")
# 注意：整词匹配优先用长词（"退订"而非"订"，避免"订阅"被误判为操作类）
OPERATION_HINTS = ("转", "AA", "平摊", "挂失", "解挂", "解冻", "冻结", "申购", "赎回", "改密码", "密码", "取消", "退订", "买", "锁定", "申请", "还款", "已付款", "付AA")


def _looks_fabricated(text: str) -> bool:
    """铁证编造判定：纯文字回复中出现"执行编号/账户号"（系统唯一生成物）→ 模型必然造假，立即拦截。
    疑似金额文字不再单独拦截——查询/操作类未调工具一律自动重试，编造内容永不透传。"""
    if not text:
        return False
    return bool(
        re.search(r"执行编号\s*[0-9a-fA-F]{6,}", text)
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
    if tool == "list_contacts":
        cs = d["contacts"]
        if not cs:
            return "联系人簿为空，说「添加联系人 XX，账户 6222-XXXX」即可添加。"
        lines = "、".join(f"{c['name']}（{c['account_id']}" + (f"，{c['relation']}" if c["relation"] else "") + "）" for c in cs)
        return f"共 {d['count']} 位联系人：{lines}"
    if tool == "add_contact":
        return f"{r.message}（绑定账户 {d['account_id']}，执行编号 {r.execution_id[:8]}）"
    if tool == "schedule_transfer":
        return (
            f"已登记定时转账：{d['amount_cents'] / 100:.2f} 元 → 账户 {d['to_account_id']}"
            f"，下次执行 {d['next_run']}（执行编号 {r.execution_id[:8]}）"
        )
    if tool == "split_bill":
        payers = "、".join(f"账户{p['account_id']}" for p in d["payers"]) or "（无）"
        return (
            f"AA 收款单已生成：「{d['title']}」共 {d['people_count']} 人，"
            f"每人 {d['per_person_cents'] / 100:.2f} 元，总计 {d['total_cents'] / 100:.2f} 元；"
            f"待收款：{payers}（说「XX已付款」逐一入账）"
        )
    if tool == "list_pending_splits":
        if not d["items"]:
            return "你当前没有待付的 AA 分摊。"
        lines = "；".join(
            f"「{x['title']}」待付 {x['amount_cents'] / 100:.2f} 元（已收 {x['paid_count']}/{x['payer_count']}）"
            for x in d["items"]
        )
        return f"你有 {d['count']} 项待付 AA 分摊：{lines}（说「支付聚餐AA」即可付款）"
    if tool == "split_bill_status":
        paid = "、".join(p["account_id"] for p in d["paid"]) or "无"
        due = "、".join(p["account_id"] for p in d["due"]) or "无"
        return (
            f"「{d['title']}」收款进度：已收 {d['paid_count']}/{d['payer_count']}"
            f"（每人 {d['per_person_cents'] / 100:.2f} 元）；已付：{paid}；待付：{due}"
        )
    if tool == "pay_split_bill":
        return (
            f"AA 收款入账：{d['payer_account_id']} {d['amount_cents'] / 100:.2f} 元 → 发起人"
            f"（已收 {d['paid_count']}/{d['payer_count']}"
            + ("，已收齐结清！" if d["settled"] else "）")
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
        return f"{r.message} {d['amount_cents'] / 100:.2f} 元"
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
    if tool == "risk_submit":
        matched = "、".join(p["name"] for p in d["matched_products"])
        return f"风险评估完成：{d['level_cn']}（{d['score']} 分）。适配产品：{matched}。{d['advice']} {d['risk_warning']}"
    if tool == "wealth_recommend":
        recs = "；".join(
            f"{r['name']}（年化{r['expected_return'] * 100:.1f}%，参考投入 {r['suggest_amount_cents'] / 100:.2f} 元，"
            f"示例月收益约 {r['est_monthly_income']:.2f} 元）：{r['reason']}"
            for r in d["recommendations"]
        )
        return f"理财推荐（{d['level_cn']}）：{recs}。{d['summary']}"
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


# ---------- 绿级查询规则兜底路由（系统防御层） ----------
class _FO_REPLY:
    """规则兜底路由的伪 LLM 回复：复用 _route_tool 完整链路（权限门 + 审计 + 执行）。

    只承载绿级只读查询工具的参数（账户锁定为当前用户），不存在绕过权限的可能。
    """

    def __init__(self, tool: str, params: dict):
        self.plan = None
        self.tool_calls = [{"name": tool, "arguments": params}]
        self.text = ""


_QUERY_FAILOVER: list[tuple[tuple[str, ...], str, object]] = [
    # (关键词元组, 工具名, 参数构造器(user_msg, account_id, user_id))
    (("异常交易", "异常消费", "可疑交易", "风控"), "analyze_bills",
     lambda u, acc, uid: {"account_id": acc}),
    (("账单", "消费分类", "对账单", "支出分析"), "analyze_bills",
     lambda u, acc, uid: {"account_id": acc}),
    (("年度", "年账单", "年度报告"), "annual_report",
     lambda u, acc, uid: {"account_id": acc}),
    (("余额",), "query_balance",
     lambda u, acc, uid: {"account_id": acc}),
    (("流水", "交易记录", "交易明细", "明细"), "list_transactions",
     lambda u, acc, uid: {"account_id": acc, "limit": 20}),
    (("订阅", "代扣", "续费"), "list_subscriptions",
     lambda u, acc, uid: {"user_id": uid}),
    (("理财", "在售", "持仓", "收益"), "wealth_products",
     lambda u, acc, uid: {"user_id": uid}),
    (("待付", "AA分摊", "收款进度"), "list_pending_splits",
     lambda u, acc, uid: {"account_id": acc}),
]


def _query_failover(user_msg: str, account_id: str, user_id: int):
    """绿级查询兜底：模型连续未调工具时，命中明确查询意图关键词即直接执行真实查询。

    安全边界：只覆盖只读查询工具；操作类（转账/申购/挂失等）绝不进入此路由——
    它们即使命中关键词也要走黄级确认/红级强验证，此函数不返回任何操作类工具。
    """
    for keys, tool, build in _QUERY_FAILOVER:
        if any(k in user_msg for k in keys):
            return tool, build(user_msg, account_id, user_id)
    return None
