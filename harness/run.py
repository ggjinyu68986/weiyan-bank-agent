"""自动评测 Harness：按 YAML 场景集驱动 Agent，产出 MD/JSON 双格式报告。

用法：
  python -m harness.run                 # MockLLM（确定性，CI/离线可用）
  python -m harness.run --real          # 真实 LLM（.env 配置的 DeepSeek）

报告输出：docs/reports/eval-report.md / eval-report.json
退出码：全部通过=0，任一失败=1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from backend.agent.llm import MockLLM, build_llm
from backend.agent.orchestrator import AgentOrchestrator

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "harness" / "scenarios.yaml"
REPORT_MD = ROOT / "docs" / "reports" / "eval-report.md"
REPORT_JSON = ROOT / "docs" / "reports" / "eval-report.json"


def run_case(case: dict, real: bool = False) -> dict:
    """执行单个用例：消息 → 确认/强验证流转 → 断言。返回结构化结果。"""
    o = AgentOrchestrator(llm=(build_llm() if real else MockLLM()))
    exp = case["expect"]
    steps = []

    # 第一条消息完整跑完（确认/强验证流转到终态）
    r = o.handle(case["input"])
    steps.append(r.requires)
    dag_confirms, guard = 0, 0
    while r.requires in ("confirm", "mfa") and guard < 10:
        if r.requires == "confirm":
            dag_confirms += 1
            r = o.confirm(r.pending_id)
        else:
            r = o.authorize(r.pending_id, exp.get("mfa_code", "123456"))
        steps.append(r.requires)
        guard += 1

    # 异常熔断用例：三个不同红级操作各输错一次 → 触发锁定 → 再发消息应被拒绝
    steps_lockout: list[str] = []
    lockout_msg = ""
    if case.get("lockout_inputs"):
        o.reset()  # 独立会话保证计数器干净
        for inp in case["lockout_inputs"]:
            rr = o.handle(inp)
            steps_lockout.append(rr.requires)
            bad = o.authorize(rr.pending_id, mfa_code="000000")
            steps_lockout.append(bad.requires)
        after = o.handle("帮我看看余额")  # 锁定后连查询也被拒
        steps_lockout.append(after.requires)
        lockout_msg = after.message
        r = after  # 终态断言基于锁定后的拒绝
        final = r.requires

    # 第二条输入（日累计升级用例：先完成 800 再转 500 → 应升级 mfa）
    steps_next: list[str] = []
    if exp.get("next_input"):
        r2 = o.handle(exp["next_input"])
        steps_next.append(r2.requires)
        guard = 0
        while r2.requires in ("confirm", "mfa") and guard < 10:
            if r2.requires == "confirm":
                r2 = o.confirm(r2.pending_id)
            else:
                r2 = o.authorize(r2.pending_id, exp.get("mfa_code", "123456"))
            steps_next.append(r2.requires)
            guard += 1

    # ---- 断言 ----
    ok = True
    failures: list[str] = []
    def chk(cond, msg):
        nonlocal ok
        if not cond:
            ok = False
            failures.append(msg)

    final = r.requires
    # 真实模型在提示词层直接拒绝注入 → 视作通过（双层防御之一）
    if final == "chat" and case.get("allow_chat_refusal"):
        final = exp["final"]

    first_ok = steps[0] == exp["first"]
    if case.get("allow_chat_refusal") and steps[0] == "chat":
        first_ok = True  # 真实模型在提示词层直接拒绝，属于有效防御
    chk(first_ok, f"首动作期望 {exp['first']}，实际 {steps[0]}")
    chk(final == exp["final"], f"终态期望 {exp['final']}，实际 {r.requires}")
    if exp.get("after_lockout_first"):
        chk(steps_lockout and steps_lockout[-1] == exp["after_lockout_first"],
            f"熔断后查询首动作期望 {exp['after_lockout_first']}，实际 {steps_lockout[-1] if steps_lockout else '无'}")
    if exp.get("after_lockout_contains"):
        chk(exp["after_lockout_contains"] in lockout_msg,
            f"熔断后回复应包含「{exp['after_lockout_contains']}」")
    if exp.get("contains"):
        chk(exp["contains"] in r.message, f"回复应包含「{exp['contains']}」")
    if exp.get("contains2"):
        chk(exp["contains2"] in r2.message, f"第二输入回复应包含「{exp['contains2']}」")
    if exp.get("next_first"):
        chk(steps_next and steps_next[0] == exp["next_first"],
            f"第二输入首动作期望 {exp['next_first']}，实际 {steps_next[0] if steps_next else '无'}")
    if exp.get("dag_confirm_count"):
        chk(dag_confirms == exp["dag_confirm_count"], f"DAG 确认次数期望 {exp['dag_confirm_count']}，实际 {dag_confirms}")
    if exp.get("after_balance_6222-0001"):
        bal = o.service.get_balance("6222-0001")
        got = f"{bal.data['balance_cents'] / 100:.2f}"
        chk(got == exp["after_balance_6222-0001"], f"余额期望 {exp['after_balance_6222-0001']}，实际 {got}")

    return {
        "id": case["id"], "name": case["name"], "input": case["input"],
        "steps": steps + (["|"] + steps_next if steps_next else []) + (["‖"] + steps_lockout if steps_lockout else []),
        "final": r.requires, "message": r.message,
        "ok": ok, "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="使用真实 LLM（默认 Mock，确定性）")
    args = ap.parse_args()

    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    cases = [c for c in data["cases"] if not (args.real and c.get("mock_only"))]
    results = [run_case(c, args.real) for c in cases]
    passed = sum(1 for r in results if r["ok"])

    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    skipped = len(data["cases"]) - len(cases)
    lines = [
        "# 微言 · 自动评测报告",
        "",
        f"- 执行模式：{'真实 LLM（DeepSeek）' if args.real else '确定性 MockLLM'}",
        f"- 用例总数：{len(cases)}（真实模式跳过 mock_only 用例 {skipped} 个）　通过：{passed}　失败：{len(results) - passed}",
        f"- 时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "| 用例 | 场景 | 输入 | 动作链 | 终态 | 结果 |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['name']} | {r['input']} | {'→'.join(r['steps'])} | {r['final']} | "
            f"{'✅ 通过' if r['ok'] else '❌ 失败'} |"
        )
    lines += ["", "## 失败明细", ""] if len(results) - passed else ["", "## 失败明细", "", "（无）", ""]
    for r in results:
        if not r["ok"]:
            lines += [f"### {r['id']} {r['name']}", "".join(f"- {f}" for f in r["failures"]), ""]
    REPORT_MD.write_text("\n".join(lines), encoding="utf-8")

    REPORT_JSON.write_text(
        json.dumps({"mode": "real" if args.real else "mock", "passed": passed,
                    "total": len(results), "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"\n评测完成：{passed}/{len(results)} 通过")
    print(f"报告：{REPORT_MD}")
    if not results or passed != len(results):
        for r in results:
            if not r["ok"]:
                print(f"  ❌ {r['id']} {r['name']}: {'; '.join(r['failures'])}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
