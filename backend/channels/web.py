"""Web / APP 渠道适配：现有 FastAPI + 纯 HTML/JS 前端即为 Web/APP 渠道（frontend/index.html）。

本模块展示同一协议（receive → handle → send）在 Web 渠道的实现形态，
并说明 IM 渠道（微信/飞书/Telegram）如何接入：仅需把消息投递到 Channel.receive 语义的入口，
复用编排器全部安全机制（权限门/熔断/审计/幻觉兜底），渠道本身零权限逻辑。

线上 API（等价于本渠道的 receive/handle 出口）：
    POST /api/v1/agent/chat      用户消息 → 内核
    POST /api/v1/agent/confirm   确认挂起操作
    POST /api/v1/agent/authorize 强验证（演示码 123456）
    GET  /api/v1/agent/status    会话安全状态
    GET  /api/v1/agent/audit     审计日志
"""
from __future__ import annotations

from backend.channels.base import BaseChannel


class WebChannel(BaseChannel):
    """Web 渠道适配器（实际线上由 FastAPI 路由承载，此处为协议示例）。"""

    def receive(self) -> str:
        raise NotImplementedError("线上由 HTTP POST /api/v1/agent/chat 承载")

    def send(self, reply) -> None:
        raise NotImplementedError("线上由 HTTP 响应体承载，前端渲染确认卡/MFA 弹层/审计面板")
