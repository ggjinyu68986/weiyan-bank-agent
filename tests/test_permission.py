"""权限引擎单元测试 —— 自动评测的第一道防线自测。

覆盖：绿自动 / 黄确认 / 超限升级红 / 红强验证 / 未注册拒绝 / 6 场景操作齐备。
运行：pytest tests/test_permission.py -q
"""
from __future__ import annotations

import pytest

from backend.registry.loader import load_registry
from backend.security.permission import decide


@pytest.fixture(scope="module")
def reg() -> dict[str, dict]:
    return load_registry()


def test_query_balance_auto(reg):
    assert decide(reg, "query_balance").action == "auto"


def test_analyze_bills_auto(reg):
    assert decide(reg, "analyze_bills").action == "auto"


def test_small_transfer_confirm(reg):
    # 500 元，今日未转 → 黄：确认
    d = decide(reg, "transfer", {"amount_cents": 50_000}, {"today_transfer_cents": 0})
    assert d.action == "confirm"


def test_transfer_at_daily_limit_still_confirm(reg):
    # 今日已转 500，再转 500 = 恰好 1000 → 仍为确认（≤1000）
    d = decide(reg, "transfer", {"amount_cents": 50_000}, {"today_transfer_cents": 50_000})
    assert d.action == "confirm"


def test_transfer_over_daily_limit_upgrades_to_mfa(reg):
    # 今日已转 500，再转 800 = 1300 > 1000 → 升级为强验证
    d = decide(reg, "transfer", {"amount_cents": 80_000}, {"today_transfer_cents": 50_000})
    assert d.action == "mfa"


def test_large_transfer_mfa(reg):
    # 单笔 5000 元 > 1000 → 红：强验证
    d = decide(reg, "transfer", {"amount_cents": 500_000}, {"today_transfer_cents": 0})
    assert d.action == "mfa"


def test_card_loss_mfa(reg):
    assert decide(reg, "report_card_loss").action == "mfa"


def test_buy_wealth_mfa(reg):
    assert decide(reg, "buy_wealth").action == "mfa"


def test_unknown_tool_denied(reg):
    d = decide(reg, "hack_steal_money")
    assert d.action == "deny"


def test_cancel_subscription_confirm(reg):
    assert decide(reg, "cancel_subscription").action == "confirm"


def test_split_bill_amount_from_total_cents(reg):
    """AA 拆分的金额字段是 total_cents：确认文案必须显示真实金额而非 0.00。"""
    d = decide(reg, "split_bill", {"total_cents": 60_000}, {"today_transfer_cents": 0})
    assert d.action == "confirm"
    assert "600.00 元" in d.reason


def test_split_bill_over_limit_upgrades(reg):
    """AA 拆分 1200 元 > 1000 日限额 → 同样升级强验证（黄→红）。"""
    d = decide(reg, "split_bill", {"total_cents": 120_000}, {"today_transfer_cents": 0})
    assert d.action == "mfa"


def test_registry_covers_six_scenarios(reg):
    """6 大场景的关键操作必须全部注册（缺一个=场景没法跑）。"""
    required = [
        # 场景1 智能转账
        "transfer", "schedule_transfer", "split_bill",
        # 场景2 账单分析
        "analyze_bills",
        # 场景3 理财
        "buy_wealth", "redeem_wealth",
        # 场景4 卡片
        "apply_virtual_card", "adjust_card_limit", "report_card_loss", "unlock_card",
        # 场景5 订阅
        "list_subscriptions", "cancel_subscription",
        # 场景6 跨场景联动
        "lock_funds", "order_gift",
    ]
    missing = [t for t in required if t not in reg]
    assert not missing, f"注册表缺少场景操作: {missing}"
