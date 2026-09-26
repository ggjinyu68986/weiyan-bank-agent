"""Mock Bank 业务服务单元测试。

覆盖：查余额 / 流水 / 转账成功 / 幂等 / 余额不足 / 无效金额 / 未知账户 / 种子画像完整度。
运行：pytest tests/test_bank_service.py -q
"""
from __future__ import annotations

from backend.bank_sim.service import BankService


def test_seed_balance():
    r = BankService().get_balance("6222-0001")
    assert r.ok
    assert r.data["balance_cents"] == 5_820_000  # 58200.00 元


def test_transfer_success():
    svc = BankService()
    r = svc.transfer("6222-0001", "6222-1001", 80_000, note="给妈妈")
    assert r.ok
    assert r.execution_id
    assert svc.get_balance("6222-0001").data["balance_cents"] == 5_820_000 - 80_000
    assert svc.get_balance("6222-1001").data["balance_cents"] == 2_000_000 + 80_000


def test_transfer_idempotent():
    svc = BankService()
    r1 = svc.transfer("6222-0001", "6222-1001", 80_000, request_id="idem-1")
    r2 = svc.transfer("6222-0001", "6222-1001", 80_000, request_id="idem-1")
    assert r1.execution_id == r2.execution_id
    # 只扣一次
    assert svc.get_balance("6222-0001").data["balance_cents"] == 5_820_000 - 80_000


def test_transfer_insufficient():
    svc = BankService()
    r = svc.transfer("6222-0001", "6222-1001", 5_820_001)  # 比余额多 1 分
    assert not r.ok
    assert r.code == "INSUFFICIENT_BALANCE"


def test_transfer_invalid_amount():
    r = BankService().transfer("6222-0001", "6222-1001", 0)
    assert not r.ok
    assert r.code == "INVALID_AMOUNT"


def test_unknown_account():
    r = BankService().get_balance("NOPE")
    assert not r.ok
    assert r.code == "ACCOUNT_NOT_FOUND"


def test_list_transactions_newest_first():
    r = BankService().list_transactions("6222-0001", limit=3)
    assert r.ok
    assert r.data["count"] == 3
    assert r.data["transactions"][0]["note"] == "药品"  # 9/30 药店消费最新


def test_seed_profile_complete():
    """画像完整度自检：订阅/事件/异常必须齐（场景2/5/6 依赖）。"""
    svc = BankService()
    subs = svc.list_subscriptions(1)
    assert subs.ok
    assert subs.data["count"] == 3
    assert any(e.name == "我爱人生日" for e in svc.store.events.values())
    notes = {t.note for t in svc.store.transactions}
    assert any("深夜大额" in n for n in notes)
    assert any("异地" in n for n in notes)


# ========== 场景1：定时转账 / AA 拆分 ==========
def test_schedule_transfer_registered():
    svc = BankService()
    r = svc.schedule_transfer("6222-0001", "6222-1001", 50_000, note="每周给妈妈", next_run="2026-10-05", cycle_days=7)
    assert r.ok
    assert r.data["schedule_id"].startswith("ST-")
    assert r.data["next_run"] == "2026-10-05"
    st = svc.store.scheduled_transfers[r.data["schedule_id"]]
    assert st.status == "active"


def test_schedule_transfer_bad_date():
    r = BankService().schedule_transfer("6222-0001", "6222-1001", 50_000, next_run="bad")
    assert not r.ok
    assert r.code == "INVALID_DATE"


def test_split_bill_math():
    """AA 拆分：900 元 / 4 人 → 每人 225；100 元 / 3 人 → 33/33/34（余数归首）。"""
    svc = BankService()
    r = svc.split_bill("6222-0001", 90_000, 4, title="聚餐AA")
    assert r.ok
    assert r.data["per_person_cents"] == 22_500
    assert r.data["first_person_extra_cents"] == 0
    r2 = svc.split_bill("6222-0001", 10_000, 3)
    assert r2.data["per_person_cents"] == 3_333
    assert r2.data["first_person_extra_cents"] == 1
    assert sum(r2.data["breakdown"]) == 10_000


# ========== 场景2：账单分析 ==========
def test_analyze_bills_categories():
    r = BankService().analyze_bills("6222-0001", month=9)
    assert r.ok
    assert r.data["period"] == "2026-09"
    cats = {c["category"] for c in r.data["by_category"]}
    assert {"餐饮", "交通", "购物", "住房"}.issubset(cats)
    assert r.data["total_expense_cents"] < 0
    assert r.data["total_income_cents"] > 0


def test_analyze_bills_anomalies():
    """异常识别：深夜大额 / 异地 / 高频三条规则都要命中。"""
    r = BankService().analyze_bills("6222-0001", month=9)
    assert r.data["anomaly_count"] >= 3
    reasons = {a["reason"] for a in r.data["anomalies"]}
    assert any("深夜大额" in x for x in reasons)
    assert any("异地" in x for x in reasons)
    assert any("高频" in x for x in reasons)


# ========== 场景3：理财 ==========
def test_buy_wealth_and_redeem():
    svc = BankService()
    bal0 = svc.get_balance("6222-0001").data["balance_cents"]
    r = svc.buy_wealth(1, "WP-001", 100_000)  # 1000 元
    assert r.ok
    assert svc.get_balance("6222-0001").data["balance_cents"] == bal0 - 100_000
    holding = next(h for h in svc.store.holdings.values() if h.user_id == 1 and h.product_id == "WP-001")
    assert holding.amount_cents == 1_000_000 + 100_000  # 种子已持有 10000 元
    r2 = svc.redeem_wealth(1, "WP-001", 50_000)
    assert r2.ok
    assert svc.get_balance("6222-0001").data["balance_cents"] == bal0 - 50_000


def test_buy_wealth_below_min():
    r = BankService().buy_wealth(1, "WP-001", 100)  # 1 元 < 起购 100 元
    assert not r.ok
    assert r.code == "BELOW_MIN_AMOUNT"


def test_redeem_exceed_holding():
    r = BankService().redeem_wealth(1, "WP-001", 999_999_999)
    assert not r.ok
    assert r.code == "EXCEED_HOLDING"


# ========== 场景4：卡片管理 ==========
def test_virtual_card_flow():
    svc = BankService()
    r = svc.apply_virtual_card(1)
    assert r.ok
    cid = r.data["card_id"]
    assert svc.store.cards[cid].status == "active"


def test_card_loss_unlock():
    svc = BankService()
    assert svc.report_card_loss("C-0001").data["status"] == "lost"
    assert svc.store.cards["C-0001"].locked is True
    assert svc.unlock_card("C-0001").data["status"] == "active"
    assert svc.store.cards["C-0001"].locked is False


def test_adjust_card_limit():
    svc = BankService()
    r = svc.adjust_card_limit("C-0001", 500_000)
    assert r.ok
    assert svc.store.cards["C-0001"].daily_limit_cents == 500_000


# ========== 场景5：订阅代扣 ==========
def test_cancel_subscription():
    svc = BankService()
    r = svc.cancel_subscription("S-001")
    assert r.ok
    assert svc.store.subscriptions["S-001"].status == "cancelled"
    r2 = svc.cancel_subscription("S-001")
    assert not r2.ok
    assert r2.code == "ALREADY_CANCELLED"


def test_detect_subscriptions_from_bills():
    r = BankService().detect_subscriptions("6222-0001")
    assert r.ok
    merchants = {x["merchant"] for x in r.data["detected"]}
    assert "某某视频" in merchants


def test_subscription_reminders():
    r = BankService().subscription_reminders(1)
    assert r.ok
    assert all(x["days_left"] > 0 for x in r.data["reminders"])
    assert all(x["subscription_id"].startswith("S-") for x in r.data["reminders"])


# ========== 场景6：跨场景联动 ==========
def test_lock_funds():
    svc = BankService()
    r = svc.lock_funds("6222-0001", 100_000, note="爱人生日预算")
    assert r.ok
    assert svc.store.accounts["6222-0001"].locked_cents == 100_000
    assert r.data["available_cents"] == 5_820_000 - 100_000


def test_lock_funds_insufficient():
    r = BankService().lock_funds("6222-0001", 999_999_999_999)
    assert not r.ok
    assert r.code == "INSUFFICIENT_BALANCE"


def test_order_gift():
    svc = BankService()
    bal0 = svc.get_balance("6222-0001").data["balance_cents"]
    r = svc.order_gift("6222-0001", "某某鲜花店", 20_000, note="爱人生日礼物")
    assert r.ok
    assert svc.get_balance("6222-0001").data["balance_cents"] == bal0 - 20_000
    assert r.data["order_id"].startswith("O-")
