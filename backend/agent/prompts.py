"""系统提示词 + 工具 schema（Agent 的能力清单，覆盖赛题 6 大场景）。

提示词三原则（答辩点）：
1. 只允许调用已注册工具；2. 禁止编造结果（一切来自工具返回值）；3. 不得引导用户绕过验证。
工具 schema = Agent 唯一可调用的能力边界（与 operations.json 注册表严格对应，
注册表还额外约束了每个工具的绿/黄/红权限级别与限额）。
"""
from __future__ import annotations

SYSTEM_PROMPT = """你是「微言」，一位银行智能助理，服务用户「小明」（默认账户 6222-0001）。

规则（必须遵守）：
1. 只能调用工具列表中的操作；没有合适工具时，直接文字回答或追问澄清。
2. 金额一律使用「分」(cents) 表示，例如 800 元 = 80000 分。
3. 禁止编造余额、交易或执行结果——所有结果必须来自工具的返回值。
4. 权限判定由系统完成，你不得建议或引导用户绕过任何验证（含限额、确认、人脸/短信）。
5. 用户省略上下文时，根据对话历史和联系人信息合理补全；不确定就追问，不猜测执行。
6. 常用账户：小明=6222-0001，妈妈=6222-1001，老婆/爱人=6222-1002，张伟=6222-1003。
7. 跨场景联动示例：用户说「我爱人生日」时，理解为其生日在 2026-12-20（事件 E-001），
   默认锁定 1000 元活期并准备生日前 2 天（12-18）订购鲜花与蛋糕——拆分为
   lock_funds(100000分) + order_gift 的 DAG，并在执行前逐项向用户确认。"""


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def build_tool_schemas() -> list[dict]:
    """OpenAI 兼容 tools 格式（DeepSeek/豆包/GPT 通用）。"""
    s = _fn
    account = {"account_id": {"type": "string", "description": "账户号，如 6222-0001"}}
    return [
        # 场景1：智能转账
        s("transfer", "转账，金额单位：分（10000 分 = 100 元）", {
            "from_account_id": {"type": "string"},
            "to_account_id": {"type": "string", "description": "收款账户（可按联系人）"},
            "amount_cents": {"type": "integer", "description": "金额，分"},
            "note": {"type": "string", "description": "备注，如：给妈妈"},
        }, ["from_account_id", "to_account_id", "amount_cents"]),
        s("schedule_transfer", "定时转账（如每周给妈妈转500元）", {
            "from_account_id": {"type": "string"},
            "to_account_id": {"type": "string"},
            "amount_cents": {"type": "integer"},
            "note": {"type": "string"},
            "next_run": {"type": "string", "description": "首次执行日期 YYYY-MM-DD"},
            "cycle_days": {"type": "integer", "description": "周期天数，0=一次性"},
        }, ["from_account_id", "to_account_id", "amount_cents"]),
        s("split_bill", "AA 拆分收款（聚餐/出行费用平摊）", {
            "account_id": {"type": "string"},
            "total_cents": {"type": "integer", "description": "总金额，分"},
            "people_count": {"type": "integer", "description": "参与人数"},
            "title": {"type": "string", "description": "收款事由，如：聚餐AA"},
        }, ["account_id", "total_cents", "people_count"]),
        # 场景2：账单分析
        s("query_balance", "查询账户余额", account, ["account_id"]),
        s("list_transactions", "查询交易流水", {
            **account, "limit": {"type": "integer", "description": "返回条数，默认50"},
        }, ["account_id"]),
        s("analyze_bills", "账单分析：消费分类统计 + 异常交易识别（深夜大额/异地/高频）", {
            **account, "month": {"type": "integer", "description": "月份，默认当月"},
        }, ["account_id"]),
        # 场景3：理财
        s("wealth_products", "查询在售理财产品与当前持仓", {
            "user_id": {"type": "integer"},
        }, ["user_id"]),
        s("buy_wealth", "申购理财产品", {
            "user_id": {"type": "integer"},
            "product_id": {"type": "string"},
            "amount_cents": {"type": "integer"},
        }, ["user_id", "product_id", "amount_cents"]),
        s("redeem_wealth", "赎回理财产品", {
            "user_id": {"type": "integer"},
            "product_id": {"type": "string"},
            "amount_cents": {"type": "integer"},
        }, ["user_id", "product_id", "amount_cents"]),
        # 场景4：卡片管理
        s("apply_virtual_card", "申请虚拟卡", {"user_id": {"type": "integer"}}, ["user_id"]),
        s("adjust_card_limit", "调整卡片日限额", {
            "card_id": {"type": "string"}, "new_limit_cents": {"type": "integer"},
        }, ["card_id", "new_limit_cents"]),
        s("report_card_loss", "卡片挂失（立即冻结）", {"card_id": {"type": "string"}}, ["card_id"]),
        s("unlock_card", "卡片解挂（恢复使用）", {"card_id": {"type": "string"}}, ["card_id"]),
        # 场景5：订阅代扣
        s("list_subscriptions", "查询订阅代扣列表", {"user_id": {"type": "integer"}}, ["user_id"]),
        s("cancel_subscription", "取消订阅代扣", {"subscription_id": {"type": "string"}}, ["subscription_id"]),
        s("detect_subscriptions", "从账单自动识别订阅扣费", account, ["account_id"]),
        s("subscription_reminders", "续费提醒（近期到期的订阅）", {"user_id": {"type": "integer"}}, ["user_id"]),
        # 场景6：跨场景联动
        s("lock_funds", "锁定活期资金（如：生日预算预留）", {
            **account, "amount_cents": {"type": "integer"}, "note": {"type": "string"},
        }, ["account_id", "amount_cents"]),
        s("order_gift", "订购商品/礼品（鲜花、蛋糕等）", {
            **account, "merchant": {"type": "string"}, "amount_cents": {"type": "integer"},
            "note": {"type": "string"},
        }, ["account_id", "merchant", "amount_cents"]),
    ]
