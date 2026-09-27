"""银行存储（开发期内存实现）。

说明：接口层不碰存储细节（Repository 抽象），后续可无缝换 SQLite/Postgres。
数据初始化自 seed.py 的"小明完整画像"。
"""
from __future__ import annotations

from .models import (
    Account,
    Card,
    Contact,
    Event,
    Holding,
    Order,
    ScheduledTransfer,
    SplitBill,
    Subscription,
    Transaction,
    User,
    WealthProduct,
)
from .seed import build_seed


class BankStore:
    def __init__(self, seed: dict | None = None):
        data = seed or build_seed()
        self.users: dict[int, User] = {u.id: u for u in data["users"]}
        self.accounts: dict[str, Account] = {a.id: a for a in data["accounts"]}
        self.transactions: list[Transaction] = data["transactions"]
        self.cards: dict[str, Card] = {c.id: c for c in data["cards"]}
        self.products: dict[str, WealthProduct] = {p.id: p for p in data["products"]}
        self.holdings: dict[str, Holding] = {h.id: h for h in data["holdings"]}
        self.subscriptions: dict[str, Subscription] = {s.id: s for s in data["subscriptions"]}
        self.events: dict[str, Event] = {e.id: e for e in data["events"]}
        self.contacts: dict[str, Contact] = {c.id: c for c in data["contacts"]}
        # 幂等登记：request_id -> {execution_id, data}
        self.idempotency: dict[str, dict] = {}
        self.scheduled_transfers: dict[str, ScheduledTransfer] = {}
        self.split_bills: dict[str, SplitBill] = {}
        self.orders: dict[str, Order] = {}
        self._tx_seq = 0

    def next_tx_id(self) -> str:
        self._tx_seq += 1
        return f"T{self._tx_seq:06d}"

    def reset(self) -> None:
        """重置到种子状态（测试/演示用）。"""
        data = build_seed()
        self.__init__(data)
