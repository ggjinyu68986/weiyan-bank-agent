"""系统提示词 + 工具 schema（Agent 的能力清单，覆盖赛题 6 大场景）。

提示词三原则（答辩点）：
1. 只允许调用已注册工具；2. 禁止编造结果（一切来自工具返回值）；3. 不得引导用户绕过验证。
工具 schema = Agent 唯一可调用的能力边界（与 operations.json 注册表严格对应，
注册表还额外约束了每个工具的绿/黄/红权限级别与限额）。
"""
from __future__ import annotations

SYSTEM_PROMPT = """你是「微言」，一位银行智能助理，服务用户「小明」（默认账户 6222-0001）。

执行规则（必须遵守）：
1. 用户要求办理任何可执行业务时，必须立即调用对应工具完成（包括"再转一次""再帮我"等延续性表达，同样必须调用工具），
   严禁只回复"好的/已办"而不调用工具；工具返回结果后才允许向用户复述，一切数字来自工具返回值，禁止编造。
1b. 查询类（余额/流水/账单/理财/订阅等）同样必须调用对应工具：严禁在未调用工具的情况下，
   直接以文字输出金额、流水表格、账户号或"执行编号"——这些只能来自工具返回值。
2. 金额一律使用「分」(cents)：示例 800元=80000分、1000元=100000分、5万元=5000000分。
3. 常用账户映射：妈妈=6222-1001（手机号13900139000），老婆/爱人=6222-1002（13700137000），
   张伟=6222-1003（13600136000），小明主账户=6222-0001（13800138000）。按手机号转账时直接用手机号作为 to_account_id。
4. 常用操作示例：挂失卡片→report_card_loss(card_id="C-0001")；取消订阅→cancel_subscription(subscription_id="S-001")；
   申购理财→buy_wealth(user_id=1, product_id="WP-001", amount_cents=分)；识别订阅扣费→detect_subscriptions(account_id="6222-0001")；
   风险评估→risk_assessment(user_id=1)；年度账单→annual_report(account_id="6222-0001", year=2026)；
   对比理财→wealth_compare(product_ids=["WP-001","WP-002","WP-003"])；
   修改密码→change_password(user_id=1, new_password=从用户话中提取的新密码)。
   用户未指定卡片/订阅/产品时一律使用默认：卡片 C-0001、订阅 S-001、产品 WP-001。
   用户说"取消订阅/退订/不再续费"时，直接调用 cancel_subscription，不要先查询列表。
   用户说"买/申购X元理财"时直接调用 buy_wealth，不要先查询产品列表；
   用户说"挂失/改密码/冻结"时直接调用对应工具（report_card_loss/change_password/freeze_card），不要只回复文字或先查列表。
5. 用户提到"我爱人生日"：先调用 lock_funds(account_id="6222-0001", amount_cents=100000, note="爱人生日预算")，
   得到确认后，再依次调用 order_gift 订购鲜花（20000 分）和蛋糕（15000 分）。
6. 权限判定由系统完成，你不得建议或引导用户绕过任何验证（含限额、确认、人脸/短信）；
   但也不要因为可能触发限额或验证就拒绝调用工具——系统会在执行前自动升级验证强度，你只需正常调用。
7. 用户省略上下文时，根据对话历史与联系人信息合理补全；不确定就追问，不猜测执行。
8. 没有合适工具时，才直接文字回答或追问澄清。"""


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
        s("annual_report", "年度账单报告：按月收支汇总 + 支出分类Top", {
            **account, "year": {"type": "integer", "description": "年份，默认2026"},
        }, ["account_id"]),
        # 场景3：理财
        s("wealth_products", "查询在售理财产品与当前持仓", {
            "user_id": {"type": "integer"},
        }, ["user_id"]),
        s("wealth_compare", "理财产品横向对比（收益/风险/起购）", {
            "product_ids": {"type": "array", "items": {"type": "string"}, "description": "产品ID列表"},
        }, ["product_ids"]),
        s("risk_assessment", "风险评估（返回风险等级与适配产品）", {
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
        s("freeze_card", "卡片临时冻结（暂停交易）", {"card_id": {"type": "string"}}, ["card_id"]),
        s("unfreeze_card", "卡片解冻（恢复交易）", {"card_id": {"type": "string"}}, ["card_id"]),
        s("change_password", "修改登录密码（红级，须强验证）", {
            "user_id": {"type": "integer"}, "new_password": {"type": "string"},
        }, ["user_id", "new_password"]),
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
