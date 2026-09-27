"""Mock Bank 数据模型 —— 模拟真实银行的核心实体。

约定：
- 所有金额以「分」(cents) 为单位存储，禁止浮点（金融系统不用浮点，答辩点）。
- 实体用 pydantic 定义，后续直接对接 FastAPI 请求/响应模型。
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class User(BaseModel):
    id: int
    name: str
    phone: str
    # 联系人昵称 -> 收款账号（场景1：按人名/昵称转账）
    contacts: dict[str, str] = {}


class Account(BaseModel):
    id: str
    user_id: int
    name: str  # 如：活期账户
    balance_cents: int = 0
    locked_cents: int = 0  # 已锁定资金（场景6：生日预算锁定）
    currency: str = "CNY"

    @property
    def available_cents(self) -> int:
        return self.balance_cents - self.locked_cents


class Contact(BaseModel):
    """联系人簿（场景1：按人名/别名/手机号转账的解析依据）。
    生产环境来源：用户手动添加 / 历史转账沉淀 / 通讯录授权合并。"""
    id: str
    user_id: int
    name: str  # 显示名：如「妈妈」
    aliases: list[str] = []  # 别名：如「母亲」
    phone: str = ""  # 手机号（可空）
    account_id: str  # 绑定的收款账户
    relation: str = ""  # 关系：家人/朋友/同事/其他


class Transaction(BaseModel):
    id: str
    account_id: str
    ts: datetime
    kind: str  # salary / transfer / consume / subscription / fee / refund
    amount_cents: int  # 正=收入 负=支出
    counterparty: str  # 对方/商户
    category: str = ""  # 餐饮/交通/购物/住房/娱乐/转账…
    note: str = ""
    status: str = "ok"


class Card(BaseModel):
    id: str
    user_id: int
    card_type: str  # debit / virtual
    status: str  # active / frozen / lost / unlocked
    daily_limit_cents: int = 0
    locked: bool = False


class WealthProduct(BaseModel):
    id: str
    name: str
    risk_level: str  # low / mid / high
    expected_return: float = 0.0  # 年化收益率（展示用，允许浮点）
    min_amount_cents: int = 0


class Holding(BaseModel):
    id: str
    user_id: int
    product_id: str
    amount_cents: int = 0


class Subscription(BaseModel):
    id: str
    user_id: int
    merchant: str
    item: str
    amount_cents: int
    cycle_days: int = 30
    next_bill_date: date
    status: str = "active"  # active / cancelled


class Event(BaseModel):
    id: str
    user_id: int
    name: str  # 如：我爱人生日
    event_date: date
    amount_cents: int = 0  # 关联预算（场景6：锁定 1000 元）
    note: str = ""
    fired: bool = False  # 事件是否已触发执行


class ScheduledTransfer(BaseModel):
    """定时转账（场景1）。实际触发执行在编排层（场景6 DAG / 定时器）。"""
    id: str
    from_account_id: str
    to_account_id: str
    amount_cents: int
    note: str = ""
    next_run: date
    cycle_days: int = 0  # 0=一次性
    status: str = "active"  # active / cancelled / done


class SplitBill(BaseModel):
    """AA 拆分收款（场景1）。"""
    id: str
    account_id: str
    title: str
    total_cents: int
    people_count: int
    per_person_cents: int
    remainder_cents: int = 0
    status: str = "open"  # open / settled


class Order(BaseModel):
    """礼品/商品订购（场景6）。"""
    id: str
    account_id: str
    merchant: str
    amount_cents: int
    note: str = ""
    status: str = "placed"
