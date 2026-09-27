"""确定性时间表达式解析器（时间沙箱的一部分）。

设计原则（答辩点："模型只出意图，系统出事实"）：
- 大模型从不直接算日期（日期是幻觉高发区，银行对时间精确性有硬要求）；
  模型只输出结构化意图表达式（如 "明天" / "下周X" / "N天后" / "一分钟后"），
- 本模块负责把表达式换算成具体 YYYY-MM-DD，纯确定性、可单测、可审计。
- 周期词（每周/每月/定期）不产生首次执行日期 → 返回 None，
  由调用方落到固定锚点（演示沙箱基准日 2026-10-05），保证演示/评测可复现。
- 分钟级表达（"一分钟后"）在按天粒度的调度系统里 ≈ "尽快（当天）" → 解析为今天。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
# 周期词：不产生首次日期（调用方用固定锚点）
_CYCLE_WORDS = ("每周", "每月", "每周一", "每月1号", "定期", "周期")


def resolve(expr: str | None, base: date | None = None) -> date | None:
    """相对时间表达式 → 具体日期。无法确定返回 None（调用方走默认锚点）。

    base 用于测试注入固定基准；不传用真实今天。
    """
    if not expr:
        return None
    e = expr.strip()
    if not e:
        return None
    b = base or date.today()

    # 明确日期 YYYY-MM-DD（模型从用户话中提取的绝对日期，直接使用）
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", e):
        try:
            return date.fromisoformat(e)
        except ValueError:
            return None

    # 今天 / 明天 / 后天
    if e in ("今天", "今日", "today", "当天"):
        return b
    if e in ("明天", "明日", "tomorrow"):
        return b + timedelta(days=1)
    if e in ("后天",):
        return b + timedelta(days=2)

    # N 天后 / N 天前
    m = re.fullmatch(r"(\d+)\s*天(?:后|以后|之后)", e)
    if m:
        return b + timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"(\d+)\s*天(?:前|以前|之前)", e)
    if m:
        return b - timedelta(days=int(m.group(1)))

    # 下周X / 下星期X：以"下一个周一"为新一周起点
    m = re.fullmatch(r"下?周([一二三四五六日天])", e)
    if m:
        next_monday = b + timedelta(days=(7 - b.weekday()) % 7)
        if next_monday == b:  # 今天恰好周一 → 下周 = 再下一周
            next_monday += timedelta(days=7)
        return next_monday + timedelta(days=_WEEKDAYS[m.group(1)])
    if e in ("下周", "下星期"):
        nxt = b + timedelta(days=(7 - b.weekday()) % 7)
        return nxt + timedelta(days=7) if nxt == b else nxt

    # 下个月X日 / 下个月
    m = re.fullmatch(r"下个月(\d{1,2})?日?", e)
    if m:
        y, mo = b.year, b.month + 1
        if mo == 13:
            y, mo = y + 1, 1
        day = int(m.group(1)) if m.group(1) else 1
        try:
            return date(y, mo, day)
        except ValueError:
            return None

    # 尽快类（分钟级在按天粒度系统里 ≈ 当天）：一分钟后 / X分钟后 / 马上 / 尽快 / 立刻
    if any(k in e for k in ("分钟后", "马上", "尽快", "立刻", "立即", "现在就转")):
        return b

    # 周期词：不产生首次日期
    if any(e.startswith(w) or e == w for w in _CYCLE_WORDS):
        return None

    return None
