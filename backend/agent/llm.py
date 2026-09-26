"""LLM Provider 抽象：OpenAI 兼容（DeepSeek 默认）+ Mock 兜底。

环境变量（.env）：
  LLM_API_KEY=sk-xxx
  LLM_BASE_URL=https://api.deepseek.com/v1
  LLM_MODEL=deepseek-chat

无 Key 时 build_llm() 自动回退 MockLLM——开发/测试/演示零阻塞。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{name, arguments(dict)}]


class BaseLLM:
    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> LLMReply:
        raise NotImplementedError


class ChatLLM(BaseLLM):
    """OpenAI 兼容服务适配器（DeepSeek / 豆包 / GPT / Claude-兼容 通用）。"""

    def __init__(self, api_key: str, base_url: str, model: str):
        from openai import OpenAI  # 延迟导入：未配置时不影响 Mock 路径

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(self, messages, tools=None) -> LLMReply:
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        tcs = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}
            tcs.append({"name": tc.function.name, "arguments": args})
        return LLMReply(text=msg.content or "", tool_calls=tcs)


class MockLLM(BaseLLM):
    """确定性假 LLM：供单元测试与无 Key 离线演示（规则匹配，可复现）。"""

    def complete(self, messages, tools=None) -> LLMReply:
        text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                text = m.get("content", "")
                break

        # 注入/绕过试探 → 让 LLM"试图"调用未注册工具，验证权限门拦截
        if any(k in text for k in ("无视规则", "忽略规则", "绕过", "hack", "直接转走")):
            return LLMReply(tool_calls=[{"name": "hack_steal_money", "arguments": {}}])

        if "余额" in text:
            return LLMReply(tool_calls=[{"name": "query_balance", "arguments": {"account_id": "6222-0001"}}])

        if "转" in text:
            yuan = _extract_yuan(text)
            to = "6222-1002" if ("老婆" in text or "爱人" in text) else ("6222-1001" if "妈妈" in text else "6222-0001")
            return LLMReply(
                tool_calls=[
                    {
                        "name": "transfer",
                        "arguments": {
                            "from_account_id": "6222-0001",
                            "to_account_id": to,
                            "amount_cents": yuan * 100,
                            "note": "给" + ("妈妈" if "妈妈" in text else ("老婆" if "老婆" in text else "家人")),
                        },
                    }
                ]
            )

        if any(k in text for k in ("流水", "明细", "账单", "花了")):
            return LLMReply(tool_calls=[{"name": "list_transactions", "arguments": {"account_id": "6222-0001", "limit": 20}}])

        if "订阅" in text:
            return LLMReply(tool_calls=[{"name": "list_subscriptions", "arguments": {"user_id": 1}}])

        return LLMReply(text="（Mock）我还没听懂你的意思，请换一种说法。")


def _extract_yuan(text: str) -> int:
    """从"转5万"/"转800元"中提取人民币金额（元）。"""
    m = re.search(r"(\d+)\s*万", text)
    if m:
        return int(m.group(1)) * 10_000
    m = re.search(r"(\d+)\s*[元块]", text)
    return int(m.group(1)) if m else 0


def build_llm() -> BaseLLM:
    """按环境变量构建 LLM；无 Key 回退 Mock（可离线开发）。"""
    key = os.getenv("LLM_API_KEY", "").strip()
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    if key:
        return ChatLLM(key, base, model)
    return MockLLM()
