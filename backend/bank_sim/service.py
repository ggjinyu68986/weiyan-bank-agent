"""银行业务服务（能力层）—— 覆盖赛题 6 大场景。

每个操作返回 OpResult（带 execution_id）。
注意：本层不做权限判定——权限门由编排层（Agent）在调用前强制经过，
这是"权限判定与执行分离"的设计（答辩点：接口是哑的，规则在上游）。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from .models import (
    Card,
    Contact,
    Holding,
    Order,
    ScheduledTransfer,
    SplitBill,
    SplitPayer,
    Subscription,
    Transaction,
)
from .result import OpResult
from .store import BankStore

# 异常识别规则阈值（分）
DEEP_NIGHT_MIN = 100_000  # 深夜大额：>= 1000 元
LARGE_MIN = 100_000  # 大额消费：>= 1000 元
HIGH_FREQ = 3  # 高频：同商户月内 >= 3 笔


class BankService:
    def __init__(self, store: BankStore | None = None):
        self.store = store or BankStore()

    # ========== 场景2：查询类 ==========
    def get_balance(self, account_id: str) -> OpResult:
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        return OpResult.success(
            {
                "account_id": account_id,
                "balance_cents": acc.balance_cents,
                "locked_cents": acc.locked_cents,
                "available_cents": acc.available_cents,
                "currency": acc.currency,
            }
        )

    def list_transactions(self, account_id: str, limit: int = 50) -> OpResult:
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        txs = sorted(
            (t for t in self.store.transactions if t.account_id == account_id),
            key=lambda t: t.ts,
            reverse=True,
        )[:limit]
        return OpResult.success(
            {"count": len(txs), "transactions": [t.model_dump(mode="json") for t in txs]}
        )

    def analyze_bills(self, account_id: str, month: int | None = None, year: int = 2026) -> OpResult:
        """账单分析：分类统计 + 异常交易识别（深夜大额/异地/高频）。"""
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        m = month or datetime.now().month
        txs = [t for t in self.store.transactions
               if t.account_id == account_id and t.ts.year == year and t.ts.month == m]

        income = sum(t.amount_cents for t in txs if t.amount_cents > 0)
        expense = sum(t.amount_cents for t in txs if t.amount_cents < 0)

        # 分类统计（支出类）
        cat_map: dict[str, dict] = {}
        for t in txs:
            if t.amount_cents >= 0 or t.kind == "salary":
                continue
            key = t.category or "其他"
            item = cat_map.setdefault(key, {"category": key, "amount_cents": 0, "count": 0})
            item["amount_cents"] += t.amount_cents
            item["count"] += 1
        by_category = sorted(cat_map.values(), key=lambda x: x["amount_cents"])

        # 异常识别：深夜大额 / 异地 / 高频（房租、转账等固定支出不参与）
        anomalies: list[dict] = []
        for t in txs:
            if t.amount_cents >= 0 or t.category in ("住房", "转账"):
                continue
            amt = abs(t.amount_cents)
            reasons = []
            if t.ts.hour >= 23 or t.ts.hour < 5:
                if amt >= DEEP_NIGHT_MIN:
                    reasons.append("深夜大额消费")
                elif amt >= 5000:
                    reasons.append("深夜消费")
            if amt >= LARGE_MIN and not reasons:
                reasons.append("大额消费")
            if "异地" in t.note:
                reasons.append("异地消费")
            if reasons:
                anomalies.append(
                    {
                        "ts": t.ts.isoformat(),
                        "counterparty": t.counterparty,
                        "amount_cents": t.amount_cents,
                        "reason": "、".join(reasons),
                        "note": t.note,
                    }
                )
        # 高频：同商户月内 >= N 笔
        merchant_count: dict[str, list] = {}
        for t in txs:
            if t.amount_cents < 0:
                merchant_count.setdefault(t.counterparty, []).append(t)
        for merchant, items in merchant_count.items():
            if len(items) >= HIGH_FREQ:
                anomalies.append(
                    {
                        "ts": items[-1].ts.isoformat(),
                        "counterparty": merchant,
                        "amount_cents": sum(t.amount_cents for t in items),
                        "reason": f"高频消费（本月 {len(items)} 笔）",
                        "note": items[-1].note,
                    }
                )

        return OpResult.success(
            {
                "period": f"{year}-{m:02d}",
                "total_income_cents": income,
                "total_expense_cents": expense,
                "by_category": by_category,
                "anomaly_count": len(anomalies),
                "anomalies": anomalies,
            }
        )

    # ========== 场景1：智能转账 ==========
    def list_contacts(self, user_id: int = 1) -> OpResult:
        """我的联系人（转账按人名解析的依据：姓名/别名/手机号 → 账户）。"""
        cs = [c for c in self.store.contacts.values() if c.user_id == user_id]
        return OpResult.success(
            {"count": len(cs), "contacts": [c.model_dump(mode="json") for c in cs]}
        )

    def add_contact(self, name: str, account_id: str, phone: str = "",
                    aliases: list[str] | None = None, relation: str = "", user_id: int = 1) -> OpResult:
        """添加联系人：绑定一个收款账户（低风险配置操作，全量审计）。
        生产环境可扩展：校验手机号格式、去重、防"诱导把资金转给新账户"。"""
        if not name or not account_id:
            return OpResult.error("INVALID_PARAMS", "联系人姓名与收款账户必填")
        if not self.store.accounts.get(account_id):
            return OpResult.error("ACCOUNT_NOT_FOUND", f"收款账户不存在：{account_id}")
        if any(c.name == name for c in self.store.contacts.values() if c.user_id == user_id):
            return OpResult.error("DUPLICATE_CONTACT", f"联系人「{name}」已存在")
        cid = f"CT-{len(self.store.contacts) + 1:04d}"
        contact = Contact(
            id=cid, user_id=user_id, name=name, aliases=list(aliases or []),
            phone=phone, account_id=account_id, relation=relation,
        )
        self.store.contacts[cid] = contact
        return OpResult.success(
            {"contact_id": cid, "name": name, "account_id": account_id,
             "phone": phone, "relation": relation},
            message=f"已添加联系人：{name}",
        )

    def transfer(self, from_account_id, to_account_id, amount_cents, note="", request_id=None) -> OpResult:
        if amount_cents <= 0:
            return OpResult.error("INVALID_AMOUNT", f"转账金额必须为正数：{amount_cents} 分")
        key = request_id or f"req-{self.store.next_tx_id()}"
        if key in self.store.idempotency:
            prev = self.store.idempotency[key]
            return OpResult.success(
                prev["data"], message="重放（幂等命中），返回原执行结果", execution_id=prev["execution_id"]
            )
        src = self._account(from_account_id)
        dst = self._resolve_account(to_account_id)
        if not src:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"转出账户不存在：{from_account_id}")
        if not dst:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"收款账户不存在：{to_account_id}")
        if src.available_cents < amount_cents:
            return OpResult.error(
                "INSUFFICIENT_BALANCE",
                f"可用余额不足：可用 {src.available_cents / 100:.2f} 元，需转 {amount_cents / 100:.2f} 元",
            )
        src.balance_cents -= amount_cents
        dst.balance_cents += amount_cents
        self._append_tx(src.id, "transfer", -amount_cents, dst.id, "转账", note)
        self._append_tx(dst.id, "transfer", amount_cents, src.id, "转账", note)
        data = {
            "from_account_id": from_account_id,
            "to_account_id": to_account_id,
            "amount_cents": amount_cents,
            "note": note,
            "request_id": key,
        }
        r = OpResult.success(data, message="转账成功")
        self.store.idempotency[key] = {"execution_id": r.execution_id, "data": data}
        return r

    def schedule_transfer(self, from_account_id, to_account_id, amount_cents, note="",
                          next_run: str | None = None, cycle_days: int = 0) -> OpResult:
        """定时转账：登记计划，实际触发由编排层定时器/事件驱动执行。"""
        if amount_cents <= 0:
            return OpResult.error("INVALID_AMOUNT", f"金额必须为正数：{amount_cents} 分")
        src = self._account(from_account_id)
        if not src:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{from_account_id}")
        try:
            run_date = date.fromisoformat(next_run or "2026-10-05")
        except ValueError:
            return OpResult.error("INVALID_DATE", f"日期格式错误：{next_run}")
        st = ScheduledTransfer(
            id=f"ST-{len(self.store.scheduled_transfers) + 1:04d}",
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount_cents=amount_cents,
            note=note,
            next_run=run_date,
            cycle_days=cycle_days,
        )
        self.store.scheduled_transfers[st.id] = st
        return OpResult.success(
            {
                "schedule_id": st.id,
                "to_account_id": to_account_id,
                "amount_cents": amount_cents,
                "note": note,
                "next_run": st.next_run.isoformat(),
                "cycle_days": cycle_days,
            },
            message="定时转账已登记",
        )

    def split_bill(self, account_id, total_cents, people_count, title="AA收款",
                   payer_accounts: list[str] | None = None) -> OpResult:
        """AA 拆分收款（完整闭环第一步）：发起人垫付 total，向其余 N-1 人各收人均金额。
        人均 = 整除，余数归发起人（自己那份 = per + rem），保证总数分毫不差。
        payer_accounts 缺省 = 联系人簿前 N-1 位（演示语义：聚餐默认叫上家人朋友）。"""
        if total_cents <= 0 or people_count < 2:
            return OpResult.error("INVALID_PARAMS", "金额必须为正数、参与人数至少 2 人")
        per = total_cents // people_count
        rem = total_cents - per * people_count
        payers = []
        if payer_accounts:
            # 用户点名的收款人：支持账户号/联系人姓名/别名/手机号，逐人解析；找不到 -> 拒绝并告知
            for acc in payer_accounts:
                acct = self._resolve_account(acc)
                if acct is None:
                    return OpResult.error("PAYER_NOT_FOUND", "收款人不存在或无法识别：" + acc)
                if acct.id == account_id:  # 防把发起人自己列为收款人
                    continue
                if any(p.account_id == acct.id for p in payers):  # 防重复
                    continue
                payers.append(SplitPayer(account_id=acct.id, amount_cents=per))
            if not payers:
                return OpResult.error("INVALID_PAYERS", "未识别到有效的收款人名单，请重新描述参与人")
        else:
            # 缺省收款对象：联系人簿前 N-1 位（发起人自己也分摊一份，只向别人收）
            cs = [c for c in self.store.contacts.values() if c.user_id == 1][: people_count - 1]
            for c in cs:
                payers.append(SplitPayer(account_id=c.account_id, amount_cents=per))
        bill = SplitBill(
            id=f"SB-{len(self.store.split_bills) + 1:04d}",
            account_id=account_id,
            title=title,
            total_cents=total_cents,
            people_count=people_count,
            per_person_cents=per,
            remainder_cents=rem,
            payers=payers,
        )
        self.store.split_bills[bill.id] = bill
        breakdown = [per] * people_count
        breakdown[0] += rem
        return OpResult.success(
            {
                "bill_id": bill.id,
                "title": title,
                "total_cents": total_cents,
                "people_count": people_count,
                "per_person_cents": per,
                "first_person_extra_cents": rem,
                "breakdown": breakdown,
                "paid_count": 0,
                "payer_count": len(payers),
                "payers": [{"account_id": p.account_id, "amount_cents": p.amount_cents, "paid": p.paid}
                           for p in payers],
            },
            message="AA 收款单已生成",
        )

    def _latest_open_bill(self, account_id: str) -> SplitBill | None:
        """最近创建的未结清收款单（模型/用户无需记 bill_id 的缺省定位）。"""
        for b in reversed(list(self.store.split_bills.values())):
            if b.account_id == account_id and b.status == "open":
                return b
        return None

    def _latest_open_bill_with_payer(self, payer_account_id: str) -> SplitBill | None:
        """按收款人账户兜底定位最近未结清收款单（对端视角：收款人支付自己的分摊，
        不知道发起人账户也能定位到单）。"""
        for b in reversed(list(self.store.split_bills.values())):
            if b.status == "open" and any(p.account_id == payer_account_id for p in b.payers):
                return b
        return None

    def list_pending_splits(self, account_id: str) -> OpResult:
        """我的待付 AA 分摊（对端视角）：当前账户作为收款对象、且未付款的分摊单列表。
        演示价值：多人 AA 不是"自说自话"——每个参与者用自己的账户/渠道查询并确认付款，
        发起人端实时看到进度变化。"""
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", "账户不存在：" + account_id)
        items = []
        for b in self.store.split_bills.values():
            if b.status != "open":
                continue
            for p in b.payers:
                if p.account_id == acc.id and not p.paid:
                    items.append({
                        "bill_id": b.id,
                        "title": b.title,
                        "initiator_account_id": b.account_id,
                        "amount_cents": p.amount_cents,
                        "per_person_cents": b.per_person_cents,
                        "paid_count": sum(1 for q in b.payers if q.paid),
                        "payer_count": len(b.payers),
                    })
        return OpResult.success(
            {"count": len(items), "items": items},
            message="待付 AA 分摊查询完成",
        )

    def split_bill_status(self, account_id: str, bill_id: str = "") -> OpResult:
        """AA 收款进度（绿级查询）：已收/待收、每人金额、状态。"""
        bill = self.store.split_bills.get(bill_id) or self._latest_open_bill(account_id)
        if not bill:
            return OpResult.error("BILL_NOT_FOUND", "没有进行中的 AA 收款单，说「聚餐600元3个人AA」即可发起")
        paid = [p for p in bill.payers if p.paid]
        due = [p for p in bill.payers if not p.paid]
        return OpResult.success(
            {
                "bill_id": bill.id,
                "title": bill.title,
                "status": bill.status,
                "total_cents": bill.total_cents,
                "per_person_cents": bill.per_person_cents,
                "paid_count": len(paid),
                "payer_count": len(bill.payers),
                "paid": [{"account_id": p.account_id, "amount_cents": p.amount_cents} for p in paid],
                "due": [{"account_id": p.account_id, "amount_cents": p.amount_cents} for p in due],
                "payers": [{"account_id": p.account_id, "amount_cents": p.amount_cents, "paid": p.paid}
                           for p in bill.payers],
            },
            message="AA 收款进度查询完成",
        )

    def pay_split_bill(self, account_id: str, payer_account_id: str,
                       bill_id: str = "", request_id: str | None = None) -> OpResult:
        """AA 收款（闭环第二步）：收款人确认付款——从 payer 账户扣人均金额，入账发起人账户。
        幂等：同一收款人重复付款拒绝；全部付清自动结清（status=settled）。"""
        bill = (self.store.split_bills.get(bill_id)
                or self._latest_open_bill(account_id)
                or self._latest_open_bill_with_payer(payer_account_id))
        if not bill:
            return OpResult.error("BILL_NOT_FOUND", "没有进行中的 AA 收款单，先发起 AA 再收款")
        if bill.status == "settled":
            return OpResult.error("BILL_SETTLED", f"「{bill.title}」已收齐结清")
        # 收款人解析：兼容账户号/联系人姓名/手机号（模型传哪个都能定位）
        resolved = self._resolve_account(payer_account_id)
        payer_id = resolved.id if resolved else payer_account_id
        payer = next((p for p in bill.payers if p.account_id == payer_id), None)
        if not payer:
            return OpResult.error("PAYER_NOT_IN_BILL", f"账户 {payer_account_id} 不在本收款单分摊名单中")
        if payer.paid:
            return OpResult.error("ALREADY_PAID", f"该分摊人已付款（{payer.paid_execution_id}），请勿重复收款")
        src = self._account(payer.account_id)
        dst = self._account(bill.account_id)
        if not src or src.available_cents < payer.amount_cents:
            return OpResult.error("INSUFFICIENT_BALANCE", f"{payer.account_id} 可用余额不足，无法支付分摊款")
        src.balance_cents -= payer.amount_cents
        dst.balance_cents += payer.amount_cents
        self._append_tx(payer.account_id, "transfer", -payer.amount_cents, bill.account_id, "转账",
                        f"AA分摊-{bill.title}")
        self._append_tx(bill.account_id, "transfer", payer.amount_cents, payer.account_id, "转账",
                        f"AA收款-{bill.title}")
        payer.paid = True
        payer.paid_execution_id = request_id or f"aa-{self.store.next_tx_id()}"
        if all(p.paid for p in bill.payers):
            bill.status = "settled"
        paid_count = sum(1 for p in bill.payers if p.paid)
        done = bill.status == "settled"
        return OpResult.success(
            {
                "bill_id": bill.id,
                "title": bill.title,
                "payer_account_id": payer.account_id,
                "amount_cents": payer.amount_cents,
                "paid_count": paid_count,
                "payer_count": len(bill.payers),
                "settled": done,
                "total_cents": bill.total_cents,
                "per_person_cents": bill.per_person_cents,
                "payers": [{"account_id": p.account_id, "amount_cents": p.amount_cents, "paid": p.paid}
                           for p in bill.payers],
            },
            message=f"已收款：{payer.account_id} {payer.amount_cents / 100:.2f} 元"
                    + (f"，{paid_count}/{len(bill.payers)} 人已付" if not done else f"，已收齐结清（{paid_count}/{len(bill.payers)}）"),
        )

    # ========== 场景3：理财 ==========
    def wealth_products(self, user_id: int = 1) -> OpResult:
        products = list(self.store.products.values())
        holdings = [h for h in self.store.holdings.values() if h.user_id == user_id]
        return OpResult.success(
            {
                "products": [p.model_dump(mode="json") for p in products],
                "holdings": [h.model_dump(mode="json") for h in holdings],
            }
        )

    def buy_wealth(self, user_id: int, product_id: str, amount_cents: int, account_id: str = "6222-0001") -> OpResult:
        prod = self.store.products.get(product_id)
        if not prod:
            return OpResult.error("PRODUCT_NOT_FOUND", f"产品不存在：{product_id}")
        if amount_cents < prod.min_amount_cents:
            return OpResult.error(
                "BELOW_MIN_AMOUNT",
                f"低于起购金额：{prod.min_amount_cents / 100:.2f} 元，申购 {amount_cents / 100:.2f} 元",
            )
        acc = self._account(account_id)
        if not acc or acc.available_cents < amount_cents:
            return OpResult.error("INSUFFICIENT_BALANCE", "可用余额不足")
        acc.balance_cents -= amount_cents
        h = next((x for x in self.store.holdings.values()
                  if x.user_id == user_id and x.product_id == product_id), None)
        if h:
            h.amount_cents += amount_cents
        else:
            self.store.holdings[f"H-{len(self.store.holdings) + 1:03d}"] = Holding(
                id=f"H-{len(self.store.holdings) + 1:03d}", user_id=user_id,
                product_id=product_id, amount_cents=amount_cents,
            )
        self._append_tx(account_id, "fee", -amount_cents, product_id, "理财", f"申购{prod.name}")
        return OpResult.success(
            {"product_id": product_id, "product": prod.name, "amount_cents": amount_cents},
            message=f"申购成功：{prod.name}",
        )

    def redeem_wealth(self, user_id: int, product_id: str, amount_cents: int, account_id: str = "6222-0001") -> OpResult:
        prod = self.store.products.get(product_id)
        h = next((x for x in self.store.holdings.values()
                  if x.user_id == user_id and x.product_id == product_id), None)
        if not prod or not h:
            return OpResult.error("HOLDING_NOT_FOUND", "未持有该产品")
        if amount_cents > h.amount_cents:
            return OpResult.error("EXCEED_HOLDING", f"赎回超持仓：持有 {h.amount_cents / 100:.2f} 元")
        h.amount_cents -= amount_cents
        self._account(account_id).balance_cents += amount_cents
        self._append_tx(account_id, "refund", amount_cents, product_id, "理财", f"赎回{prod.name}")
        return OpResult.success(
            {"product_id": product_id, "product": prod.name, "amount_cents": amount_cents},
            message=f"赎回成功：{prod.name}",
        )

    # ========== 场景4：卡片管理 ==========
    def list_cards(self, user_id: int) -> OpResult:
        """我的卡列表（卡片页数据源：类型/状态/额度/锁定）。"""
        cards = [c for c in self.store.cards.values() if c.user_id == user_id]
        return OpResult.success(
            {"count": len(cards), "cards": [c.model_dump(mode="json") for c in cards]}
        )

    def apply_virtual_card(self, user_id: int) -> OpResult:
        cid = f"CV-{len(self.store.cards) + 1:04d}"
        card = Card(id=cid, user_id=user_id, card_type="virtual", status="active",
                    daily_limit_cents=1_000_000)
        self.store.cards[cid] = card
        return OpResult.success({"card_id": cid, "status": "active", "daily_limit_cents": 1_000_000},
                           message="虚拟卡申请成功")

    def adjust_card_limit(self, card_id: str, new_limit_cents: int) -> OpResult:
        card = self.store.cards.get(card_id)
        if not card:
            return OpResult.error("CARD_NOT_FOUND", f"卡片不存在：{card_id}")
        if new_limit_cents <= 0:
            return OpResult.error("INVALID_AMOUNT", "额度必须为正数")
        card.daily_limit_cents = new_limit_cents
        return OpResult.success({"card_id": card_id, "daily_limit_cents": new_limit_cents},
                           message="卡片额度已调整")

    def report_card_loss(self, card_id: str) -> OpResult:
        card = self.store.cards.get(card_id)
        if not card:
            return OpResult.error("CARD_NOT_FOUND", f"卡片不存在：{card_id}")
        card.status, card.locked = "lost", True
        return OpResult.success({"card_id": card_id, "status": "lost"}, message="卡片已挂失并锁定")

    def unlock_card(self, card_id: str) -> OpResult:
        card = self.store.cards.get(card_id)
        if not card:
            return OpResult.error("CARD_NOT_FOUND", f"卡片不存在：{card_id}")
        card.status, card.locked = "active", False
        return OpResult.success({"card_id": card_id, "status": "active"}, message="卡片已解挂恢复使用")

    # ========== 场景5：订阅代扣 ==========
    def list_subscriptions(self, user_id: int) -> OpResult:
        # 只返回有效订阅（已取消的排除，保证"取消→复查"状态一致）
        subs = [s for s in self.store.subscriptions.values() if s.user_id == user_id and s.status != "cancelled"]
        return OpResult.success(
            {"count": len(subs), "subscriptions": [s.model_dump(mode="json") for s in subs]}
        )

    def cancel_subscription(self, subscription_id: str) -> OpResult:
        sub = self.store.subscriptions.get(subscription_id)
        if not sub:
            return OpResult.error("SUBSCRIPTION_NOT_FOUND", f"订阅不存在：{subscription_id}")
        if sub.status == "cancelled":
            return OpResult.error("ALREADY_CANCELLED", f"「{sub.merchant}」已取消")
        sub.status = "cancelled"
        return OpResult.success(
            {"subscription_id": subscription_id, "merchant": sub.merchant, "status": "cancelled"},
            message=f"已取消订阅：{sub.merchant}",
        )

    def detect_subscriptions(self, account_id: str = "6222-0001") -> OpResult:
        """从账单自动识别订阅扣费（场景5核心能力）。"""
        detected: dict[str, dict] = {}
        for t in self.store.transactions:
            if t.account_id != account_id or t.amount_cents >= 0:
                continue
            if t.kind == "subscription":
                d = detected.setdefault(t.counterparty, {"merchant": t.counterparty, "count": 0, "total_cents": 0, "last": ""})
                d["count"] += 1
                d["total_cents"] += abs(t.amount_cents)
                d["last"] = t.ts.isoformat()[:10]
        return OpResult.success(
            {"count": len(detected), "detected": sorted(detected.values(), key=lambda x: -x["total_cents"])},
            message="订阅扣费识别完成",
        )

    def subscription_reminders(self, user_id: int) -> OpResult:
        """续费提醒：active 订阅按下次扣费日排序（7 天内重点提醒）。"""
        today = date.today()
        subs = [s for s in self.store.subscriptions.values()
                if s.user_id == user_id and s.status == "active"]
        subs.sort(key=lambda s: s.next_bill_date)
        rows = []
        for s in subs:
            days = (s.next_bill_date - today).days
            rows.append(
                {
                    "subscription_id": s.id,
                    "merchant": s.merchant,
                    "amount_cents": s.amount_cents,
                    "next_bill_date": s.next_bill_date.isoformat(),
                    "days_left": days,
                    "urgent": 0 <= days <= 7,
                }
            )
        return OpResult.success({"count": len(rows), "reminders": rows})

    # ========== 场景6：跨场景联动 ==========
    def lock_funds(self, account_id: str, amount_cents: int, note: str = "") -> OpResult:
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        if amount_cents <= 0 or acc.available_cents < amount_cents:
            return OpResult.error("INSUFFICIENT_BALANCE", "可用余额不足，无法锁定")
        acc.locked_cents += amount_cents
        return OpResult.success(
            {"account_id": account_id, "locked_cents": acc.locked_cents,
             "available_cents": acc.available_cents},
            message=f"已锁定 {amount_cents / 100:.2f} 元",
        )

    def order_gift(self, account_id: str, merchant: str, amount_cents: int, note: str = "") -> OpResult:
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        if amount_cents <= 0 or acc.available_cents < amount_cents:
            return OpResult.error("INSUFFICIENT_BALANCE", "可用余额不足，无法下单")
        acc.balance_cents -= amount_cents
        oid = f"O-{len(self.store.orders) + 1:04d}"
        self.store.orders[oid] = Order(
            id=oid, account_id=account_id, merchant=merchant, amount_cents=amount_cents, note=note
        )
        self._append_tx(account_id, "consume", -amount_cents, merchant, "购物", note)
        return OpResult.success(
            {"order_id": oid, "merchant": merchant, "amount_cents": amount_cents, "note": note},
            message=f"订购成功：{merchant}",
        )

    # ========== 定时调度（系统触发，审计标记 system） ==========
    def run_due_scheduled(self, target: str | None = None) -> OpResult:
        """执行到期定时转账（时间沙箱：target 可传任意日期，供评测/演示）。
        系统级执行也走 transfer（幂等 key=sys-{id}-{date}），重复 tick 不重复扣款。"""
        today = date.fromisoformat(target) if target else date.today()
        executed = []
        for st in list(self.store.scheduled_transfers.values()):
            if st.status != "active" or st.next_run > today:
                continue
            r = self.transfer(st.from_account_id, st.to_account_id, st.amount_cents,
                              st.note, request_id=f"sys-{st.id}-{st.next_run.isoformat()}")
            executed.append({
                "schedule_id": st.id, "to_account_id": st.to_account_id,
                "amount_cents": st.amount_cents,
                "ok": r.ok, "message": r.message, "execution_id": r.execution_id,
            })
            if r.ok:
                if st.cycle_days > 0:
                    st.next_run += timedelta(days=st.cycle_days)
                else:
                    st.status = "done"
        return OpResult.success(
            {"executed_count": len(executed), "executed": executed},
            message=f"定时任务执行完成（{len(executed)} 项）",
        )

    def run_due_events(self, target: str | None = None) -> OpResult:
        """事件引擎：到期（事件日前 2 天）自动触发联动动作（场景6）。
        如 E-001 爱人生日 12/20 → 12/18 自动订购鲜花+蛋糕。"""
        today = date.fromisoformat(target) if target else date.today()
        fired = []
        for ev in self.store.events.values():
            due_date = ev.event_date - timedelta(days=2)
            if ev.fired or today < due_date:
                continue
            flower = int(ev.amount_cents * 0.4)
            cake = int(ev.amount_cents * 0.3)
            r1 = self.order_gift("6222-0001", "某某鲜花店", flower, note=ev.note)
            r2 = self.order_gift("6222-0001", "某某蛋糕店", cake, note=ev.note)
            ev.fired = True
            fired.append({"event": ev.name, "date": ev.event_date.isoformat(),
                          "orders": [r1.data.get("order_id", ""), r2.data.get("order_id", "")]})
        return OpResult.success(
            {"fired_count": len(fired), "fired": fired},
            message=f"事件触发完成（{len(fired)} 个事件）",
        )

    # ========== 场景2 扩展：年度账单报告 ==========
    def annual_report(self, account_id: str, year: int = 2026) -> OpResult:
        """年度账单：按月汇总收支 + 支出分类 Top（赛题：月度/年度账单报告）。"""
        acc = self._account(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        txs = [t for t in self.store.transactions
               if t.account_id == account_id and t.ts.year == year]
        months: dict[int, dict] = {}
        for t in txs:
            m = t.ts.month
            d = months.setdefault(m, {"month": m, "income_cents": 0, "expense_cents": 0, "count": 0})
            d["count"] += 1
            if t.amount_cents > 0:
                d["income_cents"] += t.amount_cents
            else:
                d["expense_cents"] += t.amount_cents
        total_income = sum(d["income_cents"] for d in months.values())
        total_expense = sum(d["expense_cents"] for d in months.values())
        cat: dict[str, int] = {}
        for t in txs:
            if t.amount_cents < 0:
                key = t.category or "其他"
                cat[key] = cat.get(key, 0) + t.amount_cents
        top = sorted(cat.items(), key=lambda x: x[1])[:3]
        return OpResult.success(
            {
                "year": year,
                "month_count": len(months),
                "months": sorted(months.values(), key=lambda x: x["month"]),
                "total_income_cents": total_income,
                "total_expense_cents": total_expense,
                "top_categories": [{"category": k, "amount_cents": v} for k, v in top],
            },
            message=f"{year} 年度账单报告",
        )

    # ========== 场景3 扩展：风险评估 / 产品对比 ==========
    def risk_assessment(self, user_id: int = 1) -> OpResult:
        """风险评估：返回用户风险等级与适配产品（种子画像默认稳健型，可扩展问卷）。"""
        level = "low"  # 演示：小明画像 = 低风险（保守稳健）
        level_cn = "保守稳健型"
        matched = [p for p in self.store.products.values() if p.risk_level == level]
        return OpResult.success(
            {
                "user_id": user_id,
                "level": level,
                "level_cn": level_cn,
                "matched_products": [p.model_dump(mode="json") for p in matched],
                "advice": "建议以低风险固收类为主，可搭配稳健理财（年化 2.5%）。",
            },
            message="风险评估完成",
        )

    def wealth_compare(self, product_ids: list[str]) -> OpResult:
        """理财对比：收益/风险/起购横向比较 + 结论建议。"""
        prods = [self.store.products[p] for p in product_ids if p in self.store.products]
        if not prods:
            return OpResult.error("PRODUCT_NOT_FOUND", "未找到可对比的产品")
        rows = sorted(prods, key=lambda p: p.expected_return)
        return OpResult.success(
            {
                "compare": [
                    {
                        "id": p.id, "name": p.name, "risk_level": p.risk_level,
                        "expected_return": p.expected_return,
                        "min_amount_cents": p.min_amount_cents,
                    }
                    for p in rows
                ],
                "suggestion": f"追求稳健选「{rows[0].name}」，能接受波动选「{rows[-1].name}」",
            },
            message="产品对比完成",
        )

    # ========== 账户安全：密码修改（红级） ==========
    def change_password(self, user_id: int, new_password: str) -> OpResult:
        """密码修改（红级 MFA 后执行）。演示环境不落库，仅校验强度。"""
        if not new_password or len(new_password) < 8:
            return OpResult.error("WEAK_PASSWORD", "密码至少 8 位（建议包含字母和数字）")
        if new_password == "12345678":
            return OpResult.error("WEAK_PASSWORD", "密码过于简单，请更换")
        return OpResult.success(
            {"user_id": user_id, "changed": True},
            message="密码修改成功",
        )

    # ========== 场景4 扩展：交易冻结/解冻 ==========
    def freeze_card(self, card_id: str) -> OpResult:
        card = self.store.cards.get(card_id)
        if not card:
            return OpResult.error("CARD_NOT_FOUND", f"卡片不存在：{card_id}")
        card.locked = True
        return OpResult.success({"card_id": card_id, "status": "frozen"}, message="卡片已冻结（暂停交易）")

    def unfreeze_card(self, card_id: str) -> OpResult:
        card = self.store.cards.get(card_id)
        if not card:
            return OpResult.error("CARD_NOT_FOUND", f"卡片不存在：{card_id}")
        card.locked = False
        return OpResult.success({"card_id": card_id, "status": "active"}, message="卡片已解冻（恢复交易）")

    # ========== 内部 ==========
    def _account(self, account_id: str):
        return self.store.accounts.get(account_id)

    # 联系人姓名 → 账户（赛题"按人名转账"，模型传姓名也能兜底解析；联系人簿为主，此表兼容兜底）
    CONTACT_ALIASES = {
        "妈妈": "6222-1001", "母亲": "6222-1001",
        "老婆": "6222-1002", "爱人": "6222-1002", "妻子": "6222-1002",
        "张伟": "6222-1003",
    }

    def _resolve_account(self, expr: str):
        """按账户号/联系人簿(姓名/别名/手机号)/用户手机号解析收款账户。
        顺序：账户号直查 → 联系人簿 → 11 位手机号 → 兜底别名表（防模型传别名的兼容层）。"""
        acc = self.store.accounts.get(expr)
        if acc:
            return acc
        if isinstance(expr, str):
            # 联系人簿：姓名 / 别名 / 手机号
            for c in self.store.contacts.values():
                if expr == c.name or (c.phone and expr == c.phone) or expr in c.aliases:
                    return self.store.accounts.get(c.account_id)
            if re.fullmatch(r"1\d{10}", expr):
                for u in self.store.users.values():
                    if u.phone == expr:
                        for a in self.store.accounts.values():
                            if a.user_id == u.id:
                                return a
            return self.store.accounts.get(self.CONTACT_ALIASES.get(expr, ""))
        return None

    def _append_tx(self, account_id, kind, amount_cents, counterparty, category, note):
        self.store.transactions.append(
            Transaction(
                id=self.store.next_tx_id(),
                account_id=account_id,
                ts=datetime.now(),
                kind=kind,
                amount_cents=amount_cents,
                counterparty=counterparty,
                category=category,
                note=note,
            )
        )
