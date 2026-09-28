"""LLM Provider 抽象：OpenAI 兼容（DeepSeek 默认）+ Mock 兜底。

环境变量（.env）：
  LLM_API_KEY=sk-xxx
  LLM_BASE_URL=https://api.deepseek.com/v1
  LLM_MODEL=deepseek-chat

无 Key 时 build_llm() 自动回退 MockLLM——开发/测试/演示零阻塞。
MockLLM 为确定性规则路由：覆盖 6 大场景常用话术，可复现、可单测。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field


def _load_dotenv() -> None:
    """极简 .env 加载（零依赖）：仓库根目录的 .env 自动生效。"""
    try:
        p = pathlib.Path(__file__).resolve().parents[2] / ".env"
        if not p.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


@dataclass
class LLMReply:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)  # [{name, arguments(dict)}]
    plan: list[dict] = field(default_factory=list)  # DAG 计划：[{id,tool,params,depends}]


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


USER_ACCOUNT = "6222-0001"


class MockLLM(BaseLLM):
    """确定性假 LLM：规则路由 6 大场景话术（可复现、可单测、离线可用）。"""

    def complete(self, messages, tools=None) -> LLMReply:
        text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                text = m.get("content", "")
                break

        # 注入/绕过试探 → 让 LLM"试图"调用未注册工具，验证权限门拦截
        if any(k in text for k in ("无视规则", "忽略规则", "绕过", "hack", "直接转走")):
            return self._tool("hack_steal_money", {})

        # ---- 场景2 查询 ----
        if "余额" in text:
            return self._tool("query_balance", {"account_id": USER_ACCOUNT})
        if any(k in text for k in ("流水", "明细")):
            return self._tool("list_transactions", {"account_id": USER_ACCOUNT, "limit": 20})
        if any(k in text for k in ("年度", "全年", "今年账单")):
            return self._tool("annual_report", {"account_id": USER_ACCOUNT, "year": 2026})
        if any(k in text for k in ("账单", "花了", "消费", "异常", "可疑")):
            month = 9 if ("这个月" in text or "9月" in text or "本月" in text) else None
            return self._tool("analyze_bills", {"account_id": USER_ACCOUNT, "month": month})

        # ---- 场景1 转账 ----
        if "转" in text and any(k in text for k in
                                ("定时", "每周", "每月", "下个月", "明天", "后天", "下周",
                                 "预约", "到时", "定期", "分钟后", "提前")):
            expr = ("每周" if "每周" in text else
                    ("每月" if ("每月" in text or "下个月" in text) else
                     ("明天" if "明天" in text else
                      ("后天" if "后天" in text else
                       ("下周" if "下周" in text else
                        ("一分钟后" if "分钟后" in text else None))))))
            cycle = 7 if "每周" in text else (30 if ("每月" in text or "下个月" in text) else 0)
            params = {
                "from_account_id": USER_ACCOUNT, "to_account_id": self._pick_to(text),
                "amount_cents": _extract_yuan(text) * 100,
                "note": "定时" + ("给妈妈" if "妈妈" in text else ""),
                "cycle_days": cycle,
            }
            if expr:
                params["next_run_expr"] = expr  # 只出意图，日期由系统时间解析器换算
            return self._tool("schedule_transfer", params)
        if "转" in text:
            m = re.search(r"1\d{10}", text)  # 按手机号转账（赛题示例）
            return self._tool("transfer", {
                "from_account_id": USER_ACCOUNT,
                "to_account_id": m.group(0) if m else self._pick_to(text),
                "amount_cents": _extract_yuan(text) * 100,
                "note": "给" + ("妈妈" if "妈妈" in text else ("老婆" if ("老婆" in text or "爱人" in text) else "家人")),
            })
        if any(k in text for k in ("AA", "aa", "平分", "凑份子", "已付款", "付AA", "收款进度", "收款入账")):
            if any(k in text for k in ("进度", "收到", "收齐", "付了多少")):
                return self._tool("split_bill_status", {"account_id": USER_ACCOUNT})
            if any(k in text for k in ("已付款", "付AA", "支付", "AA收款", "转给发起人")):
                return self._tool("pay_split_bill", {
                    "account_id": USER_ACCOUNT, "payer_account_id": self._pick_to(text),
                })
            explicit = re.search(r"(\d+)\s*人", text)
            people = int(explicit.group(1)) if explicit else 0
            # 点名的收款人（按消息出现顺序；未点名则空，由系统默认联系人簿前 N-1 位）
            payers = self._pick_payers(text)
            if not explicit:
                people = len(payers) + 1 if payers else 3
            params = {
                "account_id": USER_ACCOUNT, "total_cents": _extract_yuan(text) * 100,
                "people_count": people, "title": "聚餐AA",
            }
            if payers:
                params["payer_accounts"] = payers
            return self._tool("split_bill", params)

        # ---- 场景3 理财 ----
        if "理财" in text and ("买" in text or "申购" in text):
            return self._tool("buy_wealth", {
                "user_id": 1, "product_id": "WP-001", "amount_cents": (_extract_yuan(text) or 1000) * 100,
            })
        if "赎回" in text:
            return self._tool("redeem_wealth", {
                "user_id": 1, "product_id": "WP-001", "amount_cents": (_extract_yuan(text) or 1000) * 100,
            })
        if any(k in text for k in ("对比", "哪个", "比较")) and "理财" in text:
            return self._tool("wealth_compare", {"product_ids": ["WP-001", "WP-002", "WP-003"]})
        if any(k in text for k in ("风险测评", "风险评估", "测风险", "风险等级")):
            return self._tool("risk_assessment", {"user_id": 1})
        if "理财" in text or "产品" in text:
            return self._tool("wealth_products", {"user_id": 1})

        # ---- 场景4 卡片 ----
        if "虚拟卡" in text:
            return self._tool("apply_virtual_card", {"user_id": 1})
        if "挂失" in text:
            return self._tool("report_card_loss", {"card_id": "C-0001"})
        if "解挂" in text:
            return self._tool("unlock_card", {"card_id": "C-0001"})
        if "冻结" in text:
            return self._tool("freeze_card", {"card_id": "C-0001"})
        if "解冻" in text:
            return self._tool("unfreeze_card", {"card_id": "C-0001"})
        if "额度" in text:
            return self._tool("adjust_card_limit", {
                "card_id": "C-0001", "new_limit_cents": (_extract_yuan(text) or 20000) * 100,
            })
        if any(k in text for k in ("改密码", "修改密码", "换密码")):
            return self._tool("change_password", {"user_id": 1, "new_password": "NewPass2026"})

        # ---- 场景5 订阅 ----
        if any(k in text for k in ("退订", "取消订阅")):
            return self._tool("cancel_subscription", {"subscription_id": "S-001"})
        if any(k in text for k in ("扣费", "自动扣", "订阅识别")):
            return self._tool("detect_subscriptions", {"account_id": USER_ACCOUNT})
        if "续费" in text:
            return self._tool("subscription_reminders", {"user_id": 1})
        if "订阅" in text:
            return self._tool("list_subscriptions", {"user_id": 1})

        # ---- 场景6 跨场景联动 ----
        if "生日" in text and any(k in text for k in ("爱人", "老婆")):
            # 事件 E-001：爱人生日 2026-12-20 → DAG：锁定 1000 元 → 生日前 2 天订购鲜花+蛋糕
            return LLMReply(
                plan=[
                    {"id": "n1", "tool": "lock_funds",
                     "params": {"account_id": USER_ACCOUNT, "amount_cents": 100_000, "note": "爱人生日预算"}},
                    {"id": "n2", "tool": "order_gift",
                     "params": {"account_id": USER_ACCOUNT, "merchant": "某某鲜花店",
                                "amount_cents": 20_000, "note": "爱人生日礼物"}, "depends": ["n1"]},
                    {"id": "n3", "tool": "order_gift",
                     "params": {"account_id": USER_ACCOUNT, "merchant": "某某蛋糕店",
                                "amount_cents": 15_000, "note": "爱人生日蛋糕"}, "depends": ["n1"]},
                ]
            )
        if any(k in text for k in ("锁定", "预留", "生日预算")):
            return self._tool("lock_funds", {
                "account_id": USER_ACCOUNT, "amount_cents": (_extract_yuan(text) or 1000) * 100,
                "note": "爱人生日预算",
            })
        if any(k in text for k in ("鲜花", "蛋糕", "订花", "订蛋糕")):
            return self._tool("order_gift", {
                "account_id": USER_ACCOUNT, "merchant": "某某鲜花店",
                "amount_cents": (_extract_yuan(text) or 200) * 100, "note": "爱人生日礼物",
            })

        return LLMReply(text="（Mock）我还没听懂你的意思，请换一种说法。")

    def _tool(self, name, arguments) -> LLMReply:
        return LLMReply(tool_calls=[{"name": name, "arguments": arguments}])

    def _pick_to(self, text) -> str:
        """收款人解析（与联系人簿保持一致：姓名/别名 → 账户号）。"""
        for name, acc in (
            ("老婆", "6222-1002"), ("爱人", "6222-1002"),
            ("妈妈", "6222-1001"), ("张伟", "6222-1003"),
            ("爸爸", "6222-1004"), ("小王", "6222-1005"),
        ):
            if name in text:
                return acc
        return USER_ACCOUNT

    def _pick_payers(self, text) -> list[str]:
        """AA 点名收款人解析：按消息中姓名/别名出现的先后顺序，返回去重账户号列表。"""
        hits = []
        for name, acc in (
            ("妈妈", "6222-1001"), ("老婆", "6222-1002"), ("爱人", "6222-1002"),
            ("张伟", "6222-1003"), ("爸爸", "6222-1004"), ("小王", "6222-1005"),
        ):
            if name in text and acc not in [a for _, a in hits]:
                hits.append((text.index(name), acc))
        hits.sort(key=lambda x: x[0])
        return [acc for _, acc in hits]


def _extract_yuan(text: str) -> int:
    """从"转5万"/"转800元"中提取人民币金额（元）。"""
    m = re.search(r"(\d+)\s*万", text)
    if m:
        return int(m.group(1)) * 10_000
    m = re.search(r"(\d+)\s*[元块]", text)
    return int(m.group(1)) if m else 0


def build_llm() -> BaseLLM:
    """按环境变量构建 LLM；无 Key 回退 Mock（可离线开发）。"""
    _load_dotenv()
    key = os.getenv("LLM_API_KEY", "").strip()
    base = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1").strip()
    model = os.getenv("LLM_MODEL", "deepseek-chat").strip()
    if key:
        return ChatLLM(key, base, model)
    return MockLLM()
