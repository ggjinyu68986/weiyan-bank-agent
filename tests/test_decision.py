"""JEV 双引擎决策层单元测试 —— 可插拔、不改变规则基线。

覆盖：off=纯规则 / mock 镜像置信度 / live 无 Key 回退 mock / 保守合并（只升不降）/
幻觉校验（规则铁证为底线）/ 路由校验（记录型）/ 编排器接入后行为不变。
运行：pytest tests/test_decision.py -q
"""
from __future__ import annotations

import pytest

from backend.agent.decision import DecisionEngine, Verdict, _merge_grade
from backend.agent.orchestrator import AgentOrchestrator, _looks_fabricated
from backend.registry.loader import load_registry
from backend.security.permission import decide
from backend.bank_sim.service import BankService


# ---------- 引擎模式 ----------

def test_default_mode_is_mock():
    e = DecisionEngine()
    assert e.mode == "mock" and e.active


def test_off_mode_returns_rule_only():
    e = DecisionEngine(mode="off")
    v = e.grade_operation("transfer", {"amount_cents": 50_000}, "yellow")
    assert v.engine == "rule" and v.value == "yellow" and v.confidence == 1.0


def test_live_without_key_falls_back_to_mock():
    e = DecisionEngine(mode="live", api_key="")
    assert e.mode == "mock"  # 无 Key 自动回退（演示不依赖网络）


# ---------- 权限分级：mock 镜像 + 置信度 ----------

@pytest.mark.parametrize("tool,params,grade,expect_conf", [
    ("query_balance", {}, "green", 0.96),
    ("transfer", {"amount_cents": 50_000}, "yellow", 0.93),
    ("transfer", {"amount_cents": 95_000}, "yellow", 0.84),  # 接近 1000 元日限额边界 → 置信下调
    ("transfer", {"amount_cents": 500_000}, "red", 0.98),
    ("report_card_loss", {"card_id": "C-0001"}, "red", 0.98),
])
def test_mock_grade_confidence(tool, params, grade, expect_conf):
    e = DecisionEngine(mode="mock")
    v = e.grade_operation(tool, params, grade)
    assert v.value == grade and v.engine == "mock"
    assert abs(v.confidence - expect_conf) < 1e-9  # 确定性可复现


def test_mock_grade_never_changes_action():
    reg = load_registry()
    e = DecisionEngine(mode="mock")
    for tool, params, st in [
        ("transfer", {"amount_cents": 50_000}, {"today_transfer_cents": 0}),
        ("transfer", {"amount_cents": 500_000}, {"today_transfer_cents": 0}),
        ("query_balance", {}, {}),
    ]:
        d = decide(reg, tool, params, st)
        v = e.grade_operation(tool, params, d.action)
        assert v.value == d.action  # mock 是镜像：动作路径完全不变（评测/演示安全）


# ---------- 保守合并（只升不降） ----------

def test_merge_rule_is_floor():
    # JEV 判 green，规则判 yellow → 维持 yellow（JEV 不负责放行）
    merged = _merge_grade("yellow", Verdict("green", 0.99, "live"))
    assert merged.value == "yellow" and merged.engine == "live"


def test_merge_upgrades_when_jev_stricter_and_confident():
    merged = _merge_grade("yellow", Verdict("red", 0.95, "live"))
    assert merged.value == "red" and merged.engine == "live"


def test_merge_ignores_low_confidence_jev():
    merged = _merge_grade("yellow", Verdict("red", 0.60, "live"))
    assert merged.value == "yellow"  # 置信不达标 → 维持规则


def test_merge_mock_never_upgrades():
    merged = _merge_grade("yellow", Verdict("yellow", 0.93, "mock"))
    assert merged.value == "yellow"


# ---------- 幻觉校验 ----------

def test_fabrication_rule_flag_is_hard_floor():
    e = DecisionEngine(mode="mock")
    v = e.fabrication_check("当前余额 58200 元", True)
    assert v.value == "fabricated" and v.confidence == 0.99


def test_fabrication_no_hint_skips_model():
    e = DecisionEngine(mode="live", api_key="x")  # 即便 live，无特征也不调
    v = e.fabrication_check("好的，我明白了", False)
    assert v.value == "ok" and v.engine == "rule"


def test_looks_fabricated_still_catches_badge_evidence():
    assert _looks_fabricated("余额 58200 元（执行编号 abcdef12）")
    assert _looks_fabricated("已转至账户 6222-1001")
    assert not _looks_fabricated("你好，请问需要什么帮助？")


# ---------- 路由校验（记录型） ----------

def test_route_check_mismatch_is_record_only():
    e = DecisionEngine(mode="mock")
    v = e.route_check("帮我看看余额", "transfer", "yellow")
    assert v.value == "mismatch" and v.confidence == 0.74
    v2 = e.route_check("给妈妈转500元", "transfer", "yellow")
    assert v2.value == "ok"


# ---------- 编排器接入：行为基线不变 ----------

def test_orchestrator_mock_decision_attached():
    agent = AgentOrchestrator(service=BankService())
    assert agent.decision.mode == "mock"
    r = agent.handle("看看余额")
    # mock 镜像：仍自动执行（动作路径与旧版一致），且审计链路带双引擎证据
    assert r.requires == "auto"
    assert any(a.decision for a in agent.audit)  # 权限门记录带 规则⊕JEV 证据


def test_orchestrator_mock_confirm_card_has_decision():
    agent = AgentOrchestrator(service=BankService())
    r = agent.handle("给妈妈转500元")
    assert r.requires == "confirm"
    assert r.decision and r.decision["grade"]["engine"] in ("mock", "live")


def test_orchestrator_off_mode_identical_behavior():
    from backend.agent.decision import DecisionEngine as DE
    agent = AgentOrchestrator(service=BankService(), decision=DE(mode="off"))
    r = agent.handle("看看余额")
    assert r.requires == "auto"
    assert agent.audit[-1].decision == {}  # off 不附加双引擎证据
