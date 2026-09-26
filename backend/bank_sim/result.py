"""统一操作结果封装：每个业务操作都返回 OpResult，必带 execution_id。

设计（答辩点）：
- 幻觉防护的第一步：Agent 只能回显 OpResult 里的真实数据，不能自行编造。
- execution_id 贯穿"请求→执行→审计"全链路。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


def new_execution_id() -> str:
    return uuid4().hex[:12]


@dataclass
class OpResult:
    ok: bool
    code: str  # OK / ACCOUNT_NOT_FOUND / INSUFFICIENT_BALANCE ...
    message: str
    data: Any = None
    execution_id: str = field(default_factory=new_execution_id)

    @classmethod
    def success(cls, data: Any, message: str = "OK", execution_id: str | None = None) -> "OpResult":
        return cls(ok=True, code="OK", message=message, data=data,
                   execution_id=execution_id or new_execution_id())

    @classmethod
    def error(cls, code: str, message: str) -> "OpResult":
        return cls(ok=False, code=code, message=message, data=None)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "execution_id": self.execution_id,
            "data": self.data,
        }
