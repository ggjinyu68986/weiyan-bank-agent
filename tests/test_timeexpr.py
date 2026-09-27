"""确定性时间表达式解析器单测（固定基准日，断言不随真实日期漂移）。"""
from datetime import date

from backend.agent.timeexpr import resolve

BASE = date(2026, 9, 27)  # 周日


def test_absolute_date():
    assert resolve("2026-10-05", BASE) == date(2026, 10, 5)


def test_today_tomorrow_day_after():
    assert resolve("今天", BASE) == date(2026, 9, 27)
    assert resolve("明天", BASE) == date(2026, 9, 28)
    assert resolve("后天", BASE) == date(2026, 9, 29)


def test_n_days_later():
    assert resolve("3天后", BASE) == date(2026, 9, 30)
    assert resolve("5天以后", BASE) == date(2026, 10, 2)
    assert resolve("2天前", BASE) == date(2026, 9, 25)


def test_next_weekday_from_sunday():
    # 基准 2026-09-27（周日）：下一周起点 = 09-28（周一）
    assert resolve("下周一", BASE) == date(2026, 9, 28)
    assert resolve("下周三", BASE) == date(2026, 9, 30)
    assert resolve("下周", BASE) == date(2026, 9, 28)


def test_next_month():
    assert resolve("下个月", BASE) == date(2026, 10, 1)
    assert resolve("下个月5日", BASE) == date(2026, 10, 5)


def test_minute_level_means_today():
    # 分钟级在按天粒度调度系统里 ≈ 尽快（当天）
    assert resolve("一分钟后", BASE) == date(2026, 9, 27)
    assert resolve("3分钟后", BASE) == date(2026, 9, 27)
    assert resolve("马上", BASE) == date(2026, 9, 27)


def test_cycle_words_return_none():
    # 周期词不产生首次日期 → 调用方落到固定锚点
    assert resolve("每周", BASE) is None
    assert resolve("每月", BASE) is None
    assert resolve("定期", BASE) is None


def test_empty_and_garbage():
    assert resolve("", BASE) is None
    assert resolve(None, BASE) is None
    assert resolve("下周三后", BASE) is None
