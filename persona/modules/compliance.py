"""合规钩子（P4.2 第四步，docs/INTERFACES.md §5 / RUNNING.md 待补）。

**这是什么**：一套**可注入、可测试**的统一合规检查接口。它在研究流程的五个关键
位置（搜索前 / 提取前 / 证据登记前 / 持久化前 / 输出前）对来源与内容做工程检查。

**这不是什么**：它**不是法律合规认证**。网页访问限制、版权许可、隐私风险等不确定
情形，本模块一律判 `NEEDS_HUMAN`（交人工），**绝不**自动假定"可以永久保存全文"
或"允许绕过付费墙/验证码"。

**铁律（对应 P4.2 第四步）**：
- 钩子**只检查、下结论、记原因与时间**，**不**自行启动研究、**不**绕过
  `Orchestrator` 或 `Evidence`（F-2 / D-031）。
- 网页正文与搜索结果**被视为不可信输入**：本模块提供 `detect_prompt_injection`
  与 `sanitize_untrusted_text`，但"不让网页内容覆盖系统/用户指令"是**调用方**的
  职责（调用约定，由抽取器/编排器遵守），钩子本身不执行任何网页内容作为指令。
- 检查失败或不确定 → 明确记录 `DENY` / `NEEDS_HUMAN` + 原因，**不静默放行**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Sequence

from ..core.context import CallContext
from ..core.result import Err, Ok, Result
from ..core.temporal import to_iso, utc_now

# --------------------------------------------------------------------------
# 五个检查位置（对应 P4.2 第四步第 2 点）
# --------------------------------------------------------------------------

COMPLIANCE_STAGE_SEARCH = "before_search"
COMPLIANCE_STAGE_EXTRACT = "before_extract"
COMPLIANCE_STAGE_REGISTER = "before_register"
COMPLIANCE_STAGE_PERSISTENCE = "before_persistence"
COMPLIANCE_STAGE_OUTPUT = "before_output"

ALL_COMPLIANCE_STAGES: tuple[str, ...] = (
    COMPLIANCE_STAGE_SEARCH,
    COMPLIANCE_STAGE_EXTRACT,
    COMPLIANCE_STAGE_REGISTER,
    COMPLIANCE_STAGE_PERSISTENCE,
    COMPLIANCE_STAGE_OUTPUT,
)


class ComplianceStatus(str, Enum):
    """检查结论。"""

    ALLOW = "allow"
    DENY = "deny"                 # 明确拒绝：不得继续
    NEEDS_HUMAN = "needs_human"   # 不确定（版权/付费墙/隐私）：交人工，不自动放行


@dataclass
class ComplianceDecision:
    """一次检查的结论。

    `status` 决定调用方如何处置；`rule` 指明触发了哪条规则；`reason` 与
    `checked_at` 提供可追溯记录（P4.2 第四步第 3 点）。
    """

    status: ComplianceStatus
    rule: str = ""
    reason: str = ""
    checked_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, rule: str = "", reason: str = "") -> "ComplianceDecision":
        return cls(ComplianceStatus.ALLOW, rule, reason, to_iso(utc_now()))

    @classmethod
    def deny(cls, rule: str, reason: str) -> "ComplianceDecision":
        return cls(ComplianceStatus.DENY, rule, reason, to_iso(utc_now()))

    @classmethod
    def needs_human(cls, rule: str, reason: str) -> "ComplianceDecision":
        return cls(ComplianceStatus.NEEDS_HUMAN, rule, reason, to_iso(utc_now()))


@dataclass
class ComplianceEvent:
    """一次检查的输入上下文。

    由调用方（如 `ResearchService`）在五个位置构造并传入钩子，钩子据此下结论。
    """

    stage: str
    url: str | None = None
    domain: str | None = None
    content: str | None = None
    content_length: int = 0
    request_count: int = 0          # 截止当前已发起的检索/提取次数
    traceable: bool = True          # 来源是否可追溯到 URL / 发布方
    access_hint: str = "open"       # open / paywall / copyright / unknown
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplianceRules:
    """规则集合（可注入；全部为"许可/拒绝"的工程闸门）。

    字段皆为**非敏感**配置，可由 `persona.config` 注入；默认"宽松"——
    不列白名单即视为全部允许，只拦明确的黑名单与不确定情形。
    """

    allowed_domains: list[str] | None = None   # None = 不限制；否则只许这些域名
    denied_domains: list[str] = field(default_factory=list)  # 明确拒绝的域名
    max_requests: int | None = None            # 一次研究的检索/提取总次数上限
    max_content_length: int = 200_000          # 单篇正文超过此长度且要保存全文 → 转人工
    require_traceable_source: bool = True      # 不可追溯来源 → 拒绝登记
    retention_policy: str = "metadata_only"    # metadata_only / full_text_allowed
    block_uncertain_access: bool = True        # 付费墙/版权/未知 → NEEDS_HUMAN


class ComplianceHook(Protocol):
    """合规检查接口。所有实现（含 Mock）都必须满足 `check`。"""

    def check(self, ctx: CallContext, event: ComplianceEvent) -> Result[ComplianceDecision]:
        ...


# 常见提示注入模式的保守匹配（仅做"标记"，不作为指令执行）
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions", re.I),
    re.compile(r"disregard\s+(?:the\s+)?(?:above|previous)\s+instructions", re.I),
    re.compile(r"system\s*:", re.I),
    re.compile(r"you\s+are\s+now\s+", re.I),
    re.compile(r"<\s*system\s*>", re.I),
)


def detect_prompt_injection(content: str | None) -> list[str]:
    """扫描文本中疑似提示注入的片段（保守匹配）。

    返回命中的模式说明列表。调用方（抽取器/编排器）据此**把网页内容当作不可信数据**，
    绝不把它嵌入系统/用户指令去执行——本函数是"标记"，不是"放行"。
    """
    if not content:
        return []
    hits: list[str] = []
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(content):
            hits.append(pattern.pattern)
    return hits


def sanitize_untrusted_text(content: str | None, *, label: str = "WEB_CONTENT") -> str:
    """把不可信网页内容包进明确的数据标记，防止被下游当作指令。

    这是**防御性兜底**：即使调用方误把网页文本拼进提示词，明显的边界标记也能降低
    注入得逞的概率。真正的安全仍然依赖"不在系统指令位置插入网页文本"这一调用约定。
    """
    if not content:
        return ""
    return f"<<{label}_BEGIN>>\n{content}\n<<{label}_END>>"


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    # 容错解析：只取 host，去端口、转小写
    text = url.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if ":" in text:
        text = text.rsplit(":", 1)[0]
    return text.lower() or None


class RuleBasedComplianceHook:
    """基于规则的可配置合规钩子（P4.2 第四步第 4 点）。

    覆盖：域名限制、请求预算、内容长度限制、来源可追溯性、内容保留策略、不可信网页
    内容标记。不执行任何网页内容作为指令（见 `detect_prompt_injection`）。
    """

    def __init__(self, rules: ComplianceRules | None = None) -> None:
        self.rules = rules or ComplianceRules()

    # ------------------------------------------------------------ 主入口

    def check(self, ctx: CallContext, event: ComplianceEvent) -> Result[ComplianceDecision]:
        decision = self._evaluate(event)
        return Ok(decision)

    # ------------------------------------------------------------ 规则评估

    def _evaluate(self, event: ComplianceEvent) -> ComplianceDecision:
        stage = event.stage
        rules = self.rules

        # —— 请求预算（搜索/提取前都查）——
        if rules.max_requests is not None and event.request_count > rules.max_requests:
            return ComplianceDecision.deny(
                "request_budget",
                f"已发起 {event.request_count} 次外部请求，超过上限 {rules.max_requests}")

        # —— 域名限制（提取/登记/持久化时才有 domain）——
        domain = event.domain or _domain_of(event.url)
        if domain:
            if domain in {d.lower() for d in rules.denied_domains}:
                return ComplianceDecision.deny(
                    "domain_denied", f"域名 {domain} 在拒绝名单中")
            if rules.allowed_domains is not None:
                allowed = {d.lower() for d in rules.allowed_domains}
                if domain not in allowed:
                    return ComplianceDecision.deny(
                        "domain_not_allowed", f"域名 {domain} 不在允许名单中")

        # —— 来源可追溯性 ——
        if not event.traceable and rules.require_traceable_source and stage in (
                COMPLIANCE_STAGE_REGISTER, COMPLIANCE_STAGE_PERSISTENCE):
            return ComplianceDecision.deny(
                "source_untraceable", "来源缺少可追溯的 URL / 发布方，拒绝登记")

        # —— 内容长度 + 保留策略（提取/持久化时）——
        if stage in (COMPLIANCE_STAGE_EXTRACT, COMPLIANCE_STAGE_PERSISTENCE):
            if (rules.retention_policy != "full_text_allowed"
                    and event.content_length > rules.max_content_length):
                return ComplianceDecision.needs_human(
                    "content_length",
                    f"正文长度 {event.content_length} 超过 {rules.max_content_length}，"
                    f"且保留策略为 metadata_only；需人工决定是否保存全文")

        # —— 访问限制 / 版权 / 隐私不确定 → 交人工，绝不自动永久保存全文 ——
        if rules.block_uncertain_access and event.access_hint in (
                "paywall", "copyright", "unknown") and stage in (
                COMPLIANCE_STAGE_EXTRACT, COMPLIANCE_STAGE_PERSISTENCE):
            return ComplianceDecision.needs_human(
                "uncertain_access",
                f"来源访问状态为 {event.access_hint}（付费墙/版权/未知），"
                f"不可自动假定可永久保存全文，需人工确认")

        # —— 不可信内容标记（仅记录，不影响 ALLOW；真正的防护在调用约定）——
        flagged = detect_prompt_injection(event.content)
        if flagged:
            return ComplianceDecision.allow(
                "untrusted_content_flagged",
                f"检测到疑似提示注入片段（{len(flagged)} 处），已标记；"
                f"调用方须将其作为不可信数据，不得作为指令执行").with_meta(
                flagged_injection=True, injection_patterns=flagged)

        return ComplianceDecision.allow(rule="default")


# 给 ComplianceDecision 增加链式 meta 写入（保持 dataclass 不可变 + 易用）
def _decision_with_meta(self: ComplianceDecision, **extra: Any) -> ComplianceDecision:
    merged = {**self.metadata, **extra}
    return ComplianceDecision(
        status=self.status, rule=self.rule, reason=self.reason,
        checked_at=self.checked_at, metadata=merged)


ComplianceDecision.with_meta = _decision_with_meta  # type: ignore[attr-defined]


class CompositeComplianceHook:
    """聚合多个钩子：任一 DENY → 整体 DENY；否则任一 NEEDS_HUMAN → 整体 NEEDS_HUMAN。"""

    def __init__(self, hooks: Sequence[ComplianceHook]) -> None:
        self.hooks = list(hooks)

    def check(self, ctx: CallContext, event: ComplianceEvent) -> Result[ComplianceDecision]:
        decisions: list[ComplianceDecision] = []
        for hook in self.hooks:
            result = hook.check(ctx, event)
            if isinstance(result, Err):
                return result
            decisions.append(result.value)
        return Ok(self._combine(decisions))

    @staticmethod
    def _combine(decisions: list[ComplianceDecision]) -> ComplianceDecision:
        statuses = [d.status for d in decisions]
        if ComplianceStatus.DENY in statuses:
            first = next(d for d in decisions if d.status == ComplianceStatus.DENY)
            return first
        if ComplianceStatus.NEEDS_HUMAN in statuses:
            first = next(d for d in decisions if d.status == ComplianceStatus.NEEDS_HUMAN)
            return first
        return ComplianceDecision.allow(rule="composite")
