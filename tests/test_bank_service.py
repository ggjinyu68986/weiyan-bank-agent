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
