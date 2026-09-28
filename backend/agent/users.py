"""演示视角（多用户）映射：名称 → (user_id, account_id)。

与 seed.py 数据一致：6 位用户各有独立账户。
Web/APP（frontend）、IM（console）、REST API 共用同一份映射，
实现"多渠道、多用户"的演示语义（AA 多方协作：谁登录就是谁的账户）。
"""
from __future__ import annotations

VIEW_USERS: dict[str, tuple[int, str]] = {
    "小明": (1, "6222-0001"),
    "王妈妈": (2, "6222-1001"),
    "李太太": (3, "6222-1002"),
    "张伟": (4, "6222-1003"),
    "爸爸": (5, "6222-1004"),
    "小王": (6, "6222-1005"),
}

DEFAULT_USER = "小明"
