"""AA 拆分收款完整闭环单测：发起 → 收款人付款入账 → 进度 → 结清 → 防重。"""
import pytest

from backend.bank_sim.service import BankService


@pytest.fixture()
def svc():
    return BankService()


def test_split_bill_creates_bill_with_payers(svc):
    r = svc.split_bill("6222-0001", 60_000, 3, "聚餐AA")
    assert r.ok
    d = r.data
    assert d["per_person_cents"] == 20_000  # 每人 200 元
    assert len(d["payers"]) == 2  # 3 人 AA，发起人之外 2 位收款对象（默认联系人前 2 位）


def test_remainder_goes_to_initiator(svc):
    r = svc.split_bill("6222-0001", 60_100, 3, "AA")
    assert r.data["per_person_cents"] == 20_033
    assert r.data["first_person_extra_cents"] == 1  # 601 元 3 人：20033+20033+20034


def test_pay_split_bill_transfers_and_tracks(svc):
    svc.split_bill("6222-0001", 60_000, 3, "聚餐AA")
    # 默认 payer 前 2 位 = 妈妈(6222-1001)、老婆(6222-1002)
    before_init = svc.get_balance("6222-0001").data["balance_cents"]
    before_payer = svc.get_balance("6222-1001").data["balance_cents"]

    r1 = svc.pay_split_bill("6222-0001", "6222-1001")
    assert r1.ok
    assert r1.data["paid_count"] == 1 and r1.data["settled"] is False

    after_payer = svc.get_balance("6222-1001").data["balance_cents"]
    assert before_payer - after_payer == 20_000  # 妈妈 -200 元

    r2 = svc.pay_split_bill("6222-0001", "6222-1002")
    assert r2.ok and r2.data["settled"] is True  # 全部付清 → 结清

    after_init = svc.get_balance("6222-0001").data["balance_cents"]
    assert after_init - before_init == 40_000  # 发起人 +400 元


def test_pay_split_bill_rejects_duplicate(svc):
    svc.split_bill("6222-0001", 60_000, 3, "聚餐AA")
    svc.pay_split_bill("6222-0001", "6222-1001")
    r = svc.pay_split_bill("6222-0001", "6222-1001")
    assert not r.ok and "重复" in r.message


def test_pay_split_bill_rejects_payer_not_in_bill(svc):
    svc.split_bill("6222-0001", 60_000, 3, "聚餐AA")
    r = svc.pay_split_bill("6222-0001", "6222-1005")  # 小王不在默认 payer（前2位）
    assert not r.ok and "不在" in r.message


def test_pay_split_bill_no_open_bill(svc):
    r = svc.pay_split_bill("6222-0001", "6222-1001")
    assert not r.ok and "进行中" in r.message


def test_split_bill_status_progress(svc):
    svc.split_bill("6222-0001", 60_000, 3, "聚餐AA")
    svc.pay_split_bill("6222-0001", "6222-1001")
    st = svc.split_bill_status("6222-0001")
    assert st.ok
    assert st.data["paid_count"] == 1 and st.data["payer_count"] == 2


def test_split_bill_requires_at_least_two(svc):
    r = svc.split_bill("6222-0001", 60_000, 1, "AA")
    assert not r.ok and "至少 2 人" in r.message
