"""IM 渠道（终端模拟）——直接运行即可用 IM 方式与「微言」对话：

    python -m backend.channels.console                # 小明视角
    python -m backend.channels.console --user 张伟     # 切换视角（AA 多方协作演示）

演示价值：同一 Agent 内核，Web 端（frontend/index.html）与 IM 端（本渠道）行为一致，
权限分级/确认卡/强验证/熔断/审计 全部可用；生产环境将本适配器换成微信/飞书/Telegram 机器人即可。
**多人协作**：每个用户有独立账户与独立 Agent 会话（共享同一银行数据）。
AA 演示：终端1（小明）发起 AA → 终端2（张伟）查"待付AA"并确认付款 → 终端1 看进度变化。

交互：
- 直接输入自然语言（如「给妈妈转800元」「帮我挂失卡片」）
- confirm 挂起时输入 y/n；mfa 强验证时输入验证码（演示码 123456）
- 命令：/audit 查看审计 /reset 重置会话 /user 张伟 切换视角 /users 查看可切换用户 /quit 退出
"""
from __future__ import annotations

import sys
from typing import Callable

from backend.agent.orchestrator import AgentOrchestrator, AgentReply
from backend.bank_sim.service import BankService

RISK_ICON = {"green": "🟢", "yellow": "🟡", "red": "🔴", "?": "🔘"}

# 视角映射：名称 → (user_id, account_id)（与 seed 数据一致）
VIEW_USERS = {
    "小明": (1, "6222-0001"),
    "王妈妈": (2, "6222-1001"),
    "李太太": (3, "6222-1002"),
    "张伟": (4, "6222-1003"),
    "爸爸": (5, "6222-1004"),
    "小王": (6, "6222-1005"),
}


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
    # 多用户视角：所有用户共享同一银行数据（service），各自独立的 Agent 会话
    service = BankService()
    agents: dict[str, AgentOrchestrator] = {}
    current = "小明"
    for i, arg in enumerate(sys.argv):
        if arg == "--user" and i + 1 < len(sys.argv):
            current = sys.argv[i + 1].strip()
    if current not in VIEW_USERS:
        print(f"未知用户：{current}，可用：{'、'.join(VIEW_USERS)}")
        return

    def agent_for(name: str) -> AgentOrchestrator:
        if name not in agents:
            uid, acc = VIEW_USERS[name]
            agents[name] = AgentOrchestrator(service=service, user_id=uid,
                                             account_id=acc, user_name=name)
        return agents[name]

    input_fn = input
    print_fn = print
    print("【微言 · IM 渠道】我是你的银行智能助理（演示：DeepSeek / 无 Key 自动 Mock）。")
    print("直接说需求即可，如：帮我看看余额 · 给妈妈转800元 · 帮我挂失卡片。输入 /quit 退出。")
    while True:
        try:
            message = input_fn(f"你({current}) > ")
        except (EOFError, KeyboardInterrupt):
            break
        if not message.strip():
            continue
        cmd = message.strip()
        if cmd.startswith("/user"):
            name = cmd.split(maxsplit=1)[1] if len(cmd.split()) > 1 else ""
            if name not in VIEW_USERS:
                print_fn(f"可用用户：{'、'.join(VIEW_USERS)}")
                continue
            current = name
            print_fn(f"已切换视角：{current}（账户 {VIEW_USERS[current][1]}）")
            continue
        if cmd == "/users":
            print_fn("、".join(VIEW_USERS))
            continue
        if not process_message(agent_for(current), message, input, print_fn):
            break
    print("会话结束。")


if __name__ == "__main__":
    main()
