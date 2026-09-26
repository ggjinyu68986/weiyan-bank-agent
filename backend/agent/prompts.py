"""系统提示词 v1 + 工具 schema（Agent 的能力清单）。

提示词三原则（答辩点）：
1. 只允许调用已注册工具；2. 禁止编造结果（一切来自工具返回值）；3. 不得引导用户绕过验证。
"""
from __future__ import annotations

SYSTEM_PROMPT = """你是「微言」，一位银行智能助理，服务用户「小明」（默认账户 6222-0001）。

规则（必须遵守）：
1. 只能调用工具列表中的操作；没有合适工具时，直接文字回答或追问澄清。
2. 金额一律使用「分」(cents) 表示，例如 800 元 = 80000 分。
3. 禁止编造余额、交易或执行结果——所有结果必须来自工具的返回值。
4. 权限判定由系统完成，你不得建议或引导用户绕过任何验证（含限额、确认、人脸/短信）。
5. 用户省略上下文时，根据对话历史和联系人信息合理补全；不确定就追问，不猜测执行。"""


def build_tool_schemas() -> list[dict]:
    """OpenAI 兼容 tools 格式（DeepSeek/豆包/GPT 通用）。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "query_balance",
                "description": "查询账户余额",
                "parameters": {
                    "type": "object",
                    "properties": {"account_id": {"type": "string"}},
                    "required": ["account_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_transactions",
                "description": "查询交易流水",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "account_id": {"type": "string"},
                        "limit": {"type": "integer", "description": "返回条数，默认50"},
                    },
                    "required": ["account_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "transfer",
                "description": "转账，金额单位：分（10000 分 = 100 元）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from_account_id": {"type": "string"},
                        "to_account_id": {"type": "string"},
                        "amount_cents": {"type": "integer", "description": "金额，分"},
                        "note": {"type": "string", "description": "备注，如：给妈妈"},
                    },
                    "required": ["from_account_id", "to_account_id", "amount_cents"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_subscriptions",
                "description": "查询订阅代扣列表",
                "parameters": {
                    "type": "object",
                    "properties": {"user_id": {"type": "integer"}},
                    "required": ["user_id"],
                },
            },
        },
    ]
