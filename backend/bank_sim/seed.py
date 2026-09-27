"""测试用户「小明」的完整画像（种子数据）。

用途：
- 场景2 账单分析/异常识别：靠这份月度流水演示分类、报表、异常（深夜大额、异地消费）。
- 场景5 订阅代扣：靠订阅列表演示识别、续费提醒、一键取消。
- 场景6 跨场景联动：靠 Event「我爱人生日」演示锁定资金 + 定时订购。
"""
from __future__ import annotations

from datetime import date, datetime, time

from .models import (
    Account,
    Card,
    Contact,
    Event,
    Holding,
    Subscription,
    Transaction,
    User,
    WealthProduct,
)


def build_seed() -> dict:
    users = [
        User(
            id=1,
            name="小明",
            phone="13800138000",
            contacts={
                "妈妈": "6222-1001",
                "老婆": "6222-1002",
                "张伟": "6222-1003",
            },
        ),
        User(id=2, name="王妈妈", phone="13900139000"),
        User(id=3, name="李太太", phone="13700137000"),
        User(id=4, name="张伟", phone="13600136000"),
        User(id=5, name="爸爸", phone=""),
        User(id=6, name="小王", phone=""),
    ]

    accounts = [
        Account(id="6222-0001", user_id=1, name="活期账户", balance_cents=5_820_000),  # 小明 58200.00 元
        Account(id="6222-1001", user_id=2, name="活期账户", balance_cents=2_000_000),  # 妈妈
        Account(id="6222-1002", user_id=3, name="活期账户", balance_cents=1_500_000),  # 老婆
        Account(id="6222-1003", user_id=4, name="活期账户", balance_cents=800_000),  # 张伟
        Account(id="6222-1004", user_id=5, name="活期账户", balance_cents=3_000_000),  # 爸爸
        Account(id="6222-1005", user_id=6, name="活期账户", balance_cents=500_000),  # 小王
    ]

    # 联系人簿（场景1：按人名/别名/手机号转账的解析依据）
    contacts = [
        Contact(id="CT-0001", user_id=1, name="妈妈", aliases=["母亲"], phone="13900139000",
                account_id="6222-1001", relation="家人"),
        Contact(id="CT-0002", user_id=1, name="老婆", aliases=["爱人", "妻子"], phone="13700137000",
                account_id="6222-1002", relation="家人"),
        Contact(id="CT-0003", user_id=1, name="张伟", aliases=[], phone="13600136000",
                account_id="6222-1003", relation="朋友"),
        Contact(id="CT-0004", user_id=1, name="爸爸", aliases=["父亲"], phone="",
                account_id="6222-1004", relation="家人"),
        Contact(id="CT-0005", user_id=1, name="小王", aliases=[], phone="",
                account_id="6222-1005", relation="朋友"),
    ]

    cards = [
        Card(
            id="C-0001",
            user_id=1,
            card_type="debit",
            status="active",
            daily_limit_cents=2_000_000,  # 单日消费限额 20000 元
        )
    ]

    products = [
        WealthProduct(id="WP-001", name="稳健天天利", risk_level="low", expected_return=0.025, min_amount_cents=10_000),
        WealthProduct(id="WP-002", name="平衡精选", risk_level="mid", expected_return=0.045, min_amount_cents=100_000),
        WealthProduct(id="WP-003", name="进取先锋", risk_level="high", expected_return=0.075, min_amount_cents=100_000),
    ]

    holdings = [
        Holding(id="H-001", user_id=1, product_id="WP-001", amount_cents=1_000_000)  # 已持有稳健理财 10000 元
    ]

    subscriptions = [
        Subscription(id="S-001", user_id=1, merchant="某某视频", item="年度会员", amount_cents=3_000, cycle_days=30, next_bill_date=date(2026, 10, 5)),
        Subscription(id="S-002", user_id=1, merchant="某某云盘", item="月费", amount_cents=2_500, cycle_days=30, next_bill_date=date(2026, 10, 8)),
        Subscription(id="S-003", user_id=1, merchant="某某外卖", item="会员月费", amount_cents=1_500, cycle_days=30, next_bill_date=date(2026, 10, 12)),
    ]

    events = [
        Event(
            id="E-001",
            user_id=1,
            name="我爱人生日",
            event_date=date(2026, 12, 20),
            amount_cents=100_000,  # 预算 1000 元：锁定 + 订购鲜花蛋糕
            note="生日前 2 天（12/18）自动订购鲜花和蛋糕",
        )
    ]

    transactions = _build_transactions()
    return {
        "users": users,
        "accounts": accounts,
        "cards": cards,
        "products": products,
        "holdings": holdings,
        "subscriptions": subscriptions,
        "events": events,
        "transactions": transactions,
        "contacts": contacts,
    }


def _build_transactions() -> list[Transaction]:
    """9 月流水：工资/房租/订阅/日常消费 + 埋 2 个异常（深夜大额、异地消费）。"""
    # (日, 时分, kind, 金额分, 对方, 分类, 备注)
    rows = [
        (1, "09:00", "salary", 2_000_000, "公司发薪", "收入", "9月工资"),
        (1, "09:30", "consume", -500_000, "房东", "住房", "房租"),
        (2, "12:20", "consume", -3_500, "某某快餐", "餐饮", "午餐"),
        (3, "08:10", "consume", -800, "地铁", "交通", "通勤"),
        (4, "19:30", "consume", -12_800, "某某超市", "购物", "日用品"),
        (5, "12:10", "consume", -2_800, "某咖啡馆", "餐饮", "咖啡"),
        (6, "20:00", "consume", -6_600, "某某火锅", "餐饮", "聚餐"),
        (8, "10:00", "subscription", -3_000, "某某视频", "娱乐", "年度会员"),
        (9, "21:15", "consume", -9_900, "某某商城", "购物", "衣服"),
        (10, "12:05", "consume", -3_200, "某某快餐", "餐饮", "午餐"),
        (12, "02:33", "consume", -120_000, "某某奢侈品店", "购物", "异常：深夜大额消费"),  # ← 异常1
        (13, "08:20", "consume", -800, "地铁", "交通", "通勤"),
        (15, "18:40", "consume", -5_800, "某生鲜店", "购物", "买菜"),
        (16, "12:15", "consume", -3_000, "某某快餐", "餐饮", "午餐"),
        (18, "11:00", "transfer", -20_000, "妈妈", "转账", "给妈妈"),
        (20, "14:30", "consume", -25_000, "上海某餐厅", "餐饮", "异常：异地消费(上海)"),  # ← 异常2
        (22, "12:10", "consume", -3_100, "某某快餐", "餐饮", "午餐"),
        (24, "09:00", "salary", 500_000, "公司", "收入", "项目奖金"),
        (26, "19:00", "consume", -4_500, "电影院", "娱乐", "电影"),
        (28, "12:05", "consume", -3_000, "某某快餐", "餐饮", "午餐"),
        (30, "21:30", "consume", -16_000, "某某药店", "购物", "药品"),
    ]
    txs: list[Transaction] = []
    for day, hhmm, kind, amount, cp, cat, note in rows:
        hh, mm = map(int, hhmm.split(":"))
        txs.append(
            Transaction(
                id=f"T-202609{day:02d}-{len(txs)+1:03d}",
                account_id="6222-0001",
                ts=datetime(2026, 9, day, hh, mm),
                kind=kind,
                amount_cents=amount,
                counterparty=cp,
                category=cat,
                note=note,
            )
        )
    return txs
