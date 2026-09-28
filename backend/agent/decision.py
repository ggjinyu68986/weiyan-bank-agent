"""JEV 双引擎决策层（可插拔增强，不替换确定性规则引擎）。

定位（答辩叙事）：JEV 是 TypeSafe AI 的 System One 决策模型——不生成文本，只做
"给状态 + 类型化问题 → 结构化决策 + 概率 + 置信度"（70-500ms，输入 $0.042/M token，输出免费）。
本层把系统的 3 个安全判定点接上 JEV，与确定性规则并行：

  1. 权限分级（grade）：绿/黄/红 概率化判定，与规则结果比对，不一致时取保守方；
  2. 幻觉校验（fabrication）：模型回复是否"疑似编造账户信息/交易结果"；
  3. 工具路由（route）：LLM 选择的工具与用户意图是否匹配（记录型，不阻断执行）。

运行模式（环境变量 JEV_MODE）：
  off   —— 纯规则（评测/离线基线，行为与旧版完全一致）；
  mock  —— 确定性伪 JEV：镜像规则并给出合理置信度，离线可演示"双引擎 + 置信度"（默认）；
  live  —— 真实 JEV API（OpenRouter /api/alpha/decisions）；无 Key / 网络失败自动回退 mock。

安全原则（与赛题"注入防御/幻觉防护"直接呼应）：
  - 判定与执行分离：本模块只产出判定，执行仍走沙箱链路；
  - 保守合并：确定性规则是底线，JEV 只能更严格（live 且置信度达标才升级），绝不能降级；
  - JEV 自身仍可能被 prompt injection 影响 → 置信度阈值 + 确定性兜底（官方最佳实践）。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field

# 权限等级偏序：red > yellow > green（保守合并用）
GRADE_RANK = {"green": 0, "yellow": 1, "red": 2}
ACTION_TO_GRADE = {"auto": "green", "confirm": "yellow", "mfa": "red", "deny": "red"}

# 升级/拦截置信度阈值：JEV 判定低于该值不改变任何动作（宁可相信规则）
CONFIDENCE_THRESHOLD = 0.85

# 纯查询类工具（mock 置信度派生用）
_QUERY_TOOLS = {
    "query_balance", "list_transactions", "analyze_bills", "annual_report",
    "wealth_products", "wealth_compare", "risk_assessment", "list_contacts",
    "list_subscriptions", "subscription_reminders", "detect_subscriptions",
    "split_bill_status",
}
# 极高风险工具（红级）
_SENSITIVE_TOOLS = {
    "report_card_loss", "unlock_card", "change_password",
    "buy_wealth", "redeem_wealth", "adjust_card_limit",
}
# 意图关键词（路由校验 mock 镜像用；与 orchestrator 的 QUERY_HINTS 保持同源语义）
_QUERY_WORDS = ("余额", "流水", "账单", "年度", "收益", "评估", "对比", "推荐",
                "明细", "查询", "看看", "还剩", "多少钱", "多少", "订阅", "代扣",
                "理财", "持仓")
_OPERATION_WORDS = ("转", "AA", "平摊", "挂失", "解挂", "解冻", "冻结", "申购",
                    "赎回", "密码", "取消", "退订", "买", "锁定", "申请", "已付款", "付AA")

# 疑似账户/金额特征（决定是否值得调 JEV 幻觉校验；铁证判定仍走确定性规则）
_FABRICATED_HINT = re.compile(r"执行编号|6222-\d{4}|[\d,]+\.\d{2}\s*元|余额[:：]?\s*[\d,]+")


@dataclass
class Verdict:
    """一次判定的完整结果（规则 ⊕ JEV 的原始证据，供审计/前端可视化）。"""
    value: str            # 判定值：green/yellow/red / fabricated/ok / ok/mismatch
    confidence: float     # 0-1，JEV 置信度（mock 为规则镜像置信度，live 为模型返回）
    engine: str           # rule / mock / live —— 哪个引擎产出该判定
    detail: str = ""      # live 模式原始 detail / 失败原因

    def to_dict(self) -> dict:
        return {
            "value": self.value,
            "confidence": round(self.confidence, 4),
            "engine": self.engine,
            "detail": self.detail,
        }


def _merge_grade(rule_grade: str, jev: Verdict, threshold: float = CONFIDENCE_THRESHOLD) -> Verdict:
    """保守合并：JEV 比规则更严格且置信度达标 → 升级；否则维持规则（规则是底线）。
    注意：JEV 是增强层，绝不负责"放行"——规则判 red/yellow，JEV 判 green，仍按规则执行。"""
    if not jev or jev.engine in ("rule", "mock") or jev.confidence < threshold:
        return Verdict(rule_grade, jev.confidence if jev else 1.0,
                       "rule" if not jev else jev.engine, jev.detail if jev else "")
    if GRADE_RANK.get(jev.value, 0) > GRADE_RANK.get(rule_grade, 0):
        return Verdict(jev.value, jev.confidence, "live", jev.detail + "（保守合并升级）")
    return Verdict(rule_grade, jev.confidence, "live", jev.detail)


class DecisionEngine:
    """可插拔决策引擎：off=纯规则 / mock=确定性伪JEV / live=真实 JEV API。"""

    def __init__(self, mode: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, model: str | None = None,
                 timeout: float = 1.5, threshold: float = CONFIDENCE_THRESHOLD):
        self.mode = (mode or os.getenv("JEV_MODE", "mock")).lower()
        self.api_key = api_key or os.getenv("JEV_API_KEY", "")
        self.base_url = base_url or os.getenv("JEV_BASE_URL",
                                              "https://openrouter.ai/api/alpha/decisions")
        self.model = model or os.getenv("JEV_MODEL", "typesafe/jev-1.13")
        self.timeout = timeout
        self.threshold = threshold
        # live 必须要有 Key，否则自动回退 mock（演示/比赛现场无网也稳定）
        if self.mode == "live" and not self.api_key:
            self.mode = "mock"

    @property
    def active(self) -> bool:
        return self.mode != "off"

    # ---------- 判定点 1：权限分级 ----------
    def grade_operation(self, tool: str, params: dict, rule_grade: str) -> Verdict:
        """给一次"工具+参数"判绿/黄/红，与规则结果并行。返回带置信度的 Verdict。"""
        if self.mode == "off":
            return Verdict(rule_grade, 1.0, "rule")
        if self.mode == "live":
            v = self._live_grade(tool, params, rule_grade)
            if v is not None:
                return v
            # 网络/解析失败 → 回退 mock（镜像规则，不改变动作）
        return Verdict(rule_grade, self._mock_confidence(tool, params, rule_grade), "mock")

    def _mock_confidence(self, tool: str, params: dict, rule_grade: str) -> float:
        """确定性伪置信度：查询类高置信；操作类中高；转账接近日限额（1000 元）时
        显式降低置信度，模拟"边界情况模型拿不准"，答辩演示有戏剧性且可复现。"""
        if rule_grade == "green":
            return 0.96
        if rule_grade == "red":
            return 0.98
        if tool == "transfer" or tool == "schedule_transfer":
            amt = params.get("amount_cents", 0)
            if amt and 90_000 <= amt < 100_000:  # 接近 1000 元日限额边界
                return 0.84
            return 0.93
        if tool in _SENSITIVE_TOOLS:
            return 0.97
        return 0.92

    def _live_grade(self, tool: str, params: dict, rule_grade: str) -> Verdict | None:
        state = (f"工具={tool}，参数={json.dumps(params, ensure_ascii=False)[:200]}，"
                 f"确定性规则判定={rule_grade}")
        questions = {
            "grade": {
                "type": "choice",
                "instructions": "这是一次银行智能体操作，请按风险判定权限等级。"
                                "green=纯查询自动执行；yellow=需用户确认（小额操作）；"
                                "red=需多因子强验证（大额/敏感操作）。",
                "criteria": {
                    "green": "纯查询类，无资金变动，自动执行",
                    "yellow": "有资金变动或需确认的小额操作（日累计≤1000元）",
                    "red": "大额（日累计>1000元）或高敏感操作，必须强验证",
                },
            }
        }
        ans = self._post(state, questions)
        if not ans:
            return None
        try:
            a = ans["answers"]["grade"]
            choice = str(a.get("choice", "")).lower()
            if choice not in GRADE_RANK:
                return None
            probs = a.get("probabilities") or {}
            conf = float(a.get("confidence") or probs.get(choice, 0) or 0.5)
            return Verdict(choice, conf, "live", json.dumps(probs, ensure_ascii=False)[:200])
        except (KeyError, TypeError, ValueError):
            return None

    # ---------- 判定点 2：幻觉校验 ----------
    def fabrication_check(self, text: str, rule_flag: bool) -> Verdict:
        """模型回复是否疑似编造账户信息/交易结果。规则铁证为硬底线，JEV 为概率层。"""
        if self.mode == "off" or not _FABRICATED_HINT.search(text or ""):
            # 无账户/金额特征时不值得调模型；铁证规则已覆盖
            return Verdict("fabricated" if rule_flag else "ok", 0.99 if rule_flag else 0.5, "rule")
        if self.mode == "live":
            v = self._live_fabrication(text)
            if v is not None:
                return v
        # mock / live 失败：镜像规则铁证（确定性兜底）
        return Verdict("fabricated" if rule_flag else "ok", 0.99 if rule_flag else 0.6, "mock")

    def _live_fabrication(self, text: str) -> Verdict | None:
        questions = {
            "fabricated": {
                "type": "noul",
                "instructions": "这段 AI 银行助手回复是否在未调用任何查询/执行工具的情况下，"
                                "编造了账户信息或交易结果（如余额、执行编号、账户号、转账结果）？",
            }
        }
        ans = self._post(text[:500], questions)
        if not ans:
            return None
        try:
            a = ans["answers"]["fabricated"]
            choice = bool(a.get("choice"))
            conf = float(a.get("confidence") or 0.5)
            return Verdict("fabricated" if choice else "ok", conf, "live")
        except (KeyError, TypeError, ValueError):
            return None

    # ---------- 判定点 3：工具路由校验（记录型，不阻断） ----------
    def route_check(self, user_msg: str, tool: str, rule_grade: str) -> Verdict:
        """LLM 选中的工具与用户意图是否匹配。默认只记录到审计，不阻断执行。"""
        if self.mode == "off":
            return Verdict("ok", 1.0, "rule")
        is_query_intent = any(w in (user_msg or "") for w in _QUERY_WORDS)
        is_operation_intent = any(w in (user_msg or "") for w in _OPERATION_WORDS)
        tool_is_query = tool in _QUERY_TOOLS or rule_grade == "green"
        tool_is_operation = not tool_is_query
        if (is_query_intent and tool_is_operation) or (is_operation_intent and tool_is_query and not is_query_intent):
            return Verdict("mismatch", 0.74, "mock" if self.mode == "mock" else "rule",
                           "意图与所选工具类别不一致（记录型提醒）")
        return Verdict("ok", 0.9, "mock" if self.mode == "mock" else "rule")

    # ---------- JEV HTTP 调用 ----------
    def _post(self, state: str, questions: dict) -> dict | None:
        """POST /api/alpha/decisions。失败（网络/解析）返回 None，调用方回退 mock。"""
        body = json.dumps({"model": self.model, "state": state, "questions": questions}).encode()
        req = urllib.request.Request(
            self.base_url, data=body, method="POST",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
            return None
