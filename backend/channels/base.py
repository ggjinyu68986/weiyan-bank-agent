"""渠道抽象：Agent 内核与接入渠道解耦。

协议三步：receive（接收消息）→ handle（调用编排器内核）→ send（发送回复）。
安全机制（权限门/熔断/审计/幻觉兜底）全部在内核，渠道本身无权限判定——渠道是"哑的"。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from backend.agent.orchestrator import AgentOrchestrator, AgentReply


class BaseChannel(ABC):
    """任意渠道（APP/Web/IM/微信/飞书/Telegram）的统一接口。"""

    def __init__(self) -> None:
        self.agent = AgentOrchestrator()  # 每个渠道持有独立会话（隔离，符合银行业务习惯）

    @abstractmethod
    def receive(self) -> str:
        """从渠道接收一条用户消息。"""

    @abstractmethod
    def send(self, reply: AgentReply) -> None:
        """把内核回复投递到渠道。"""

    def handle(self, message: str) -> AgentReply:
        """（核心步骤）调用 Agent 内核：意图理解→权限门→执行/挂起→审计。
        渠道层不做任何权限判定。"""
        return self.agent.handle(message)

    def run(self) -> None:
        """渠道主循环：receive → handle → send。"""
        while True:
            message = self.receive()
            if message is None:  # 渠道主动断开
                break
            self.send(self.handle(message))
