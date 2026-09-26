"""银行业务服务（能力层）。

每个操作返回 OpResult（带 execution_id）。
注意：本层不做权限判定——权限门由编排层（Agent）在调用前强制经过，
这是"权限判定与执行分离"的设计（答辩点：接口是哑的，规则在上游）。
"""
from __future__ import annotations

from datetime import datetime

from .models import Transaction
from .result import OpResult
from .store import BankStore


class BankService:
    def __init__(self, store: BankStore | None = None):
        self.store = store or BankStore()

    # ---------- 场景2：查询类 ----------
    def get_balance(self, account_id: str) -> OpResult:
        acc = self.store.accounts.get(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        return OpResult.success(
            {
                "account_id": account_id,
                "balance_cents": acc.balance_cents,
                "currency": acc.currency,
            }
        )

    def list_transactions(self, account_id: str, limit: int = 50) -> OpResult:
        acc = self.store.accounts.get(account_id)
        if not acc:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"账户不存在：{account_id}")
        txs = sorted(
            (t for t in self.store.transactions if t.account_id == account_id),
            key=lambda t: t.ts,
            reverse=True,
        )[:limit]
        return OpResult.success(
            {
                "count": len(txs),
                "transactions": [t.model_dump(mode="json") for t in txs],
            }
        )

    # ---------- 场景1：转账（幂等 + execution_id） ----------
    def transfer(
        self,
        from_account_id: str,
        to_account_id: str,
        amount_cents: int,
        note: str = "",
        request_id: str | None = None,
    ) -> OpResult:
        if amount_cents <= 0:
            return OpResult.error("INVALID_AMOUNT", f"转账金额必须为正数：{amount_cents} 分")

        key = request_id or f"req-{self.store.next_tx_id()}"
        # 幂等：同一 request_id 只执行一次，重复请求直接返回原结果
        if key in self.store.idempotency:
            prev = self.store.idempotency[key]
            return OpResult.success(
                prev["data"], message="重放（幂等命中），返回原执行结果", execution_id=prev["execution_id"]
            )

        src = self.store.accounts.get(from_account_id)
        dst = self.store.accounts.get(to_account_id)
        if not src:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"转出账户不存在：{from_account_id}")
        if not dst:
            return OpResult.error("ACCOUNT_NOT_FOUND", f"收款账户不存在：{to_account_id}")
        if src.balance_cents < amount_cents:
            return OpResult.error(
                "INSUFFICIENT_BALANCE",
                f"余额不足：可用 {src.balance_cents / 100:.2f} 元，需转 {amount_cents / 100:.2f} 元",
            )

        # 执行：扣款 + 入账 + 双侧流水（原子性由上层编排保证）
        src.balance_cents -= amount_cents
        dst.balance_cents += amount_cents
        now = datetime.now()
        self.store.transactions.append(
            Transaction(
                id=self.store.next_tx_id(),
                account_id=from_account_id,
                ts=now,
                kind="transfer",
                amount_cents=-amount_cents,
                counterparty=to_account_id,
                category="转账",
                note=note,
            )
        )
        self.store.transactions.append(
            Transaction(
                id=self.store.next_tx_id(),
                account_id=to_account_id,
                ts=now,
                kind="transfer",
                amount_cents=amount_cents,
                counterparty=from_account_id,
                category="转账",
                note=note,
            )
        )

        data = {
            "from_account_id": from_account_id,
            "to_account_id": to_account_id,
            "amount_cents": amount_cents,
            "note": note,
            "request_id": key,
        }
        # 先记幂等，再返回（同一 execution_id 贯穿请求与结果）
        r = OpResult.success(data, message="转账成功")
        self.store.idempotency[key] = {"execution_id": r.execution_id, "data": data}
        return r

    # ---------- 场景5：订阅 ----------
    def list_subscriptions(self, user_id: int) -> OpResult:
        subs = [s for s in self.store.subscriptions.values() if s.user_id == user_id]
        return OpResult.success({"count": len(subs), "subscriptions": [s.model_dump(mode="json") for s in subs]})
