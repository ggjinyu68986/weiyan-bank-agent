# -*- coding: utf-8 -*-
"""多用户视角 API 测试：AA 多方协作（发起→对端查待付→对端支付→进度联动）。

覆盖 /agent/users、/agent/pending-splits、chat/confirm 的 user 路由。
"""
import pytest
from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """每测前：强制 MockLLM + 复位演示数据。

    - 强制 MockLLM：API 层测试只测路由/协作流，不依赖真实 DeepSeek 的随机输出
      （真实模型可能把 payer_accounts 填名字或账户号，二者 service 层都支持）。
    - reset：store 复位 + 重建用户会话，避免全量跑时共享 store 被其他用例污染。
    """
    monkeypatch.setenv("LLM_API_KEY", "")
    client.post("/api/v1/agent/reset")
    yield


def _chat(message, user="小明"):
    return client.post("/api/v1/agent/chat", json={"message": message, "user": user}).json()


def _confirm(pending_id, user="小明"):
    return client.post(f"/api/v1/agent/confirm?user={user}", json={"pending_id": pending_id}).json()


def test_users_endpoint_lists_all_views():
    d = client.get("/api/v1/agent/users").json()
    names = [u["name"] for u in d["users"]]
    assert "小明" in names and "张伟" in names and "小王" in names
    acc = {u["name"]: u["account_id"] for u in d["users"]}
    assert acc["张伟"] == "6222-1003"


def test_full_aa_collab_across_users():
    # 1) 小明发起点名 AA → 黄级确认 → 建单
    r = _chat("和小王，张伟聚餐 一共600元 帮我AA", "小明")
    assert r["requires"] == "confirm"
    assert r["params"]["payer_accounts"] == ["6222-1005", "6222-1003"]  # MockLLM 按出现顺序返回账户号
    c = _confirm(r["pending_id"], "小明")
    assert "每人 200.00 元" in c["message"]

    # 2) 张伟视角：待付 AA（对端）
    r2 = _chat("我有哪些待付的AA", "张伟")
    assert r2["tool"] == "list_pending_splits"
    assert "聚餐AA" in r2["message"] and "200.00 元" in r2["message"]

    # 3) REST 直查待付（前端待付卡数据源）
    d = client.get("/api/v1/agent/pending-splits?account_id=6222-1003").json()
    assert len(d["items"]) == 1
    assert d["items"][0]["amount_cents"] == 20000
    assert d["items"][0]["paid_count"] == 0 and d["items"][0]["payer_count"] == 2

    # 4) 张伟支付（黄级确认）→ 入账
    r3 = _chat("支付聚餐AA", "张伟")
    assert r3["requires"] == "confirm"
    assert r3["params"]["payer_account_id"] == "6222-1003"
    c3 = _confirm(r3["pending_id"], "张伟")
    assert "已收 1/2" in c3["message"]

    # 5) 钱真的动了：张伟余额 8000 → 7800
    bal = client.get("/api/v1/accounts/6222-1003/balance").json()["data"]
    assert bal["available_cents"] == 780000

    # 6) 小明视角进度联动 1/2，待付剩小王
    r5 = _chat("AA进度", "小明")
    assert "已收 1/2" in r5["message"]
    assert "6222-1005" in r5["message"]


def test_unknown_user_rejected():
    r = client.post("/api/v1/agent/chat", json={"message": "帮我看看余额", "user": "不存在的人"})
    assert r.status_code == 400

def test_annual_report_endpoint():
    """年度账单报告端点（前端年度视图数据源）：按月趋势 + 年度分类 Top。"""
    d = client.get("/api/v1/agent/annual?account_id=6222-0001&year=2026").json()
    assert d["year"] == 2026
    assert d["month_count"] >= 1
    assert d["total_expense_cents"] < 0
    assert d["total_income_cents"] > 0
    assert len(d["months"]) == d["month_count"]
    assert len(d["top_categories"]) >= 1


def test_wealth_endpoints():
    """理财端点：列表/推荐/对比（前端理财 Tab 数据源）。"""
    d = client.get("/api/v1/agent/wealth?user_id=1").json()
    assert len(d["products"]) == 3
    assert len(d["holdings"]) >= 1

    rec = client.get("/api/v1/agent/wealth/recommend?user_id=1").json()
    assert len(rec["recommendations"]) == 3
    assert rec["recommendations"][0]["risk_level"] == "low"

    cmp = client.get("/api/v1/agent/wealth/compare?product_ids=WP-001,WP-003").json()
    assert len(cmp["compare"]) == 2
    assert cmp["compare"][0]["expected_return"] <= cmp["compare"][-1]["expected_return"]
