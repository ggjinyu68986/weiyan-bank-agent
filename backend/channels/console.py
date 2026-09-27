"""IM 渠道（终端模拟）——直接运行即可用 IM 方式与「微言」对话：

    python -m backend.channels.console

演示价值：同一 Agent 内核，Web 端（frontend/index.html）与 IM 端（本渠道）行为一致，
权限分级/确认卡/强验证/熔断/审计 全部可用；生产环境将本适配器换成微信/飞书/Telegram 机器人即可。

交互：
- 直接输入自然语言（如「给妈妈转800元」「帮我挂失卡片」）
- confirm 挂起时输入 y/n；mfa 强验证时输入验证码（演示码 123456）
- 命令：/audit 查看审计 /reset 重置会话 /quit 退出
"""
from __future__ import annotations

from typing import Callable

from backend.agent.orchestrator import AgentOrchestrator, AgentReply

RISK_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴", "?": "🔘"}


def _fmt(reply: AgentReply) -> str:
    tag = RISK_ICON.get(reply.tool and "", "🔘")
    if reply.requires == "confirm":
        return f"[需确认] {reply.message}"
    if reply.requires == "mfa":
        return f"[强验证] {reply.message}"
    if reply.requires == "deny":
        return f"[拒绝] {reply.message}"
    if reply.requires == "chat":
        return f"[对话] {reply.message}"
    return f"[执行] {reply.message}"


def process_message(agent: AgentOrchestrator, message: str,
                    input_fn: Callable[[str], str], print_fn: Callable[[str], None]) -> bool:
    """处理单条消息（IM 渠道交互循环的核心，可单测）。
    返回 False 表示会话应结束（/quit）。"""
    cmd = message.strip()
    if cmd == "/quit":
        return False
    if cmd == "/reset":
        agent.reset()
        print_fn("会话已重置（银行数据已复原，锁定已解除）")
        return True
    if cmd == "/audit":
        for rec in agent.audit[-10:]:
            print_fn(f"  {rec.ts.strftime('%H:%M:%S')} | {rec.risk or '-':<6} | {rec.action:<9} | {rec.user_msg} | {rec.message}")
        return True

    reply = agent.handle(message)
    print_fn(f"[微言] {_fmt(reply)}")
    if reply.requires == "confirm":
        ans = input_fn("确认执行吗？(y=确认 / n=取消) > ").strip().lower()
        if ans == "y":
            out = agent.confirm(reply.pending_id)
            print_fn(f"[微言] {_fmt(out)}")
    elif reply.requires == "mfa":
        code = input_fn("请输入验证码（演示码 123456）> ").strip()
        out = agent.authorize(reply.pending_id, code)
        print_fn(f"[微言] {_fmt(out)}")
    return True


def main() -> None:
    agent = AgentOrchestrator()
    input_fn = input
    print_fn = print
    print("【微言 · IM 渠道】我是你的银行智能助理（演示：DeepSeek / 无 Key 自动 Mock）。")
    print("直接说需求即可，如：帮我看看余额 · 给妈妈转800元 · 帮我挂失卡片。输入 /quit 退出。")
    while True:
        try:
            message = input_fn("你 > ")
        except (EOFError, KeyboardInterrupt):
            break
        if not message.strip():
            continue
        if not process_message(agent, message, input, print_fn):
            break
    print("会话结束。")


if __name__ == "__main__":
    main()
