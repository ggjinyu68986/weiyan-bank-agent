"""权限引擎 v0 —— 判级大脑。

设计（答辩点）：
- 数据驱动：规则全部来自 operations.json，引擎不硬编码任何工具。
- 纯函数：输入 (工具, 参数, 用户状态) → 输出决策，不碰任何 IO，可单测、可画状态机。
- 安全底线：未注册操作一律拒绝（deny）。

决策动作：auto=自动执行 / confirm=需用户确认 / mfa=需多因子强验证 / deny=拒绝
"""
from __future__ import annotations

from dataclasses import dataclass, field

GREEN = "green"
YELLOW = "yellow"
RED = "red"

ACTION_AUTO = "auto"
ACTION_CONFIRM = "confirm"
ACTION_MFA = "mfa"
ACTION_DENY = "deny"


@dataclass
class PermissionDecision:
    action: str  # auto / confirm / mfa / deny
    reason: str
    spec: dict | None = field(default=None)


def _amount_cents(params: dict) -> int:
    return params.get("amount_cents") or 0


def decide(
    registry: dict[str, dict],
    tool: str,
    params: dict | None = None,
    user_state: dict | None = None,
) -> PermissionDecision:
    """判级入口。user_state 示例：{"today_transfer_cents": 50000}"""
    params = params or {}
    user_state = user_state or {"today_transfer_cents": 0}

    spec = registry.get(tool)
    if not spec:
        # 安全底线：未注册的操作一律拒绝，不给 LLM 任何自由发挥空间
        return PermissionDecision(ACTION_DENY, f"未注册操作「{tool}」，安全原则：一律拒绝")

    risk = spec.get("risk")
    name_cn = spec.get("name_cn", tool)

    if risk == GREEN:
        return PermissionDecision(ACTION_AUTO, f"绿色操作，确认意图后自动执行：{name_cn}", spec)

    if risk == RED:
        return PermissionDecision(
            ACTION_MFA, f"红色操作，必须多因子强验证：{name_cn}", spec
        )

    # yellow：带日限额的检查今日累计，超限自动升级为强验证（黄→红）
    limit = spec.get("daily_limit_cents")
    if limit:
        amt = _amount_cents(params)
        cum = user_state.get("today_transfer_cents", 0)
        if cum + amt > limit:
            return PermissionDecision(
                ACTION_MFA,
                f"黄色操作但将超日限额（今日已 {cum / 100:.2f} 元 + 本次 {amt / 100:.2f} 元"
                f" > {limit / 100:.2f} 元），升级为强验证：{name_cn}",
                spec,
            )
        return PermissionDecision(
            ACTION_CONFIRM,
            f"黄色操作，执行前需展示详情并获用户确认：{name_cn}（{amt / 100:.2f} 元）",
            spec,
        )

    return PermissionDecision(ACTION_CONFIRM, f"黄色操作，执行前需用户确认：{name_cn}", spec)


def would_exceed_daily(spec: dict, params: dict, user_state: dict) -> bool:
    """供调用方在确认后、执行前再次校验（防止并发/累计变化导致超限）。"""
    limit = spec.get("daily_limit_cents")
    if not limit:
        return False
    amt = _amount_cents(params)
    cum = user_state.get("today_transfer_cents", 0)
    return cum + amt > limit


if __name__ == "__main__":  # 无 pytest 环境的快速冒烟演示
    from backend.registry.loader import load_registry

    reg = load_registry()
    cases = [
        ("query_balance", {}, {"today_transfer_cents": 0}),
        ("transfer", {"amount_cents": 50_000}, {"today_transfer_cents": 0}),
        ("transfer", {"amount_cents": 80_000}, {"today_transfer_cents": 50_000}),
        ("report_card_loss", {}, {}),
        ("hack_steal_money", {}, {}),
    ]
    for tool, params, state in cases:
        d = decide(reg, tool, params, state)
        print(f"{tool:20s} -> {d.action:8s} {d.reason}")
