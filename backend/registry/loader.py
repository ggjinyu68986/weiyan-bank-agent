"""操作注册表加载器：读 operations.json，返回 {tool: spec}。"""
from __future__ import annotations

import json
import pathlib


def load_registry() -> dict[str, dict]:
    p = pathlib.Path(__file__).resolve().parent / "operations.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return {op["tool"]: op for op in data["operations"]}
