"""跨模块通用 DTO —— 对应 docs/INTERFACES.md §1.6、§1.7。

分层规则（§1.6）：
    `*Record` 数据库行      → ⛔ 不许跨模块传递（本阶段尚不存在）
    `*Ref`   {id} + 少量展示字段 → ✅ 默认选择
    `*Query` 查询条件        → ✅
    `Page[T]` 分页结果       → ✅

本文件只放"公共"的形状；各业务实体自己的 `*View` / `*Draft`
（如 `PersonaView`、`EvidenceDraft`）留到对应阶段（P2~P4）再定义。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .enums import PausedReason, ProvenanceNamespace, TaskStatus, TaskType

T = TypeVar("T")

MAX_QUERY_LIMIT = 200  # 超过 → Err(VALIDATION.limit_too_large)


@dataclass
class Ref:
    """跨模块引用一个对象时的最小形状：只有 ID + 少量说明。"""

    id: str
    kind: str
    label: str | None = None


@dataclass
class Query:
    """查询条件。`filters` 的键由各模块定义白名单，未知键一律拒绝。"""

    filters: dict = field(default_factory=dict)
    limit: int = 50
    offset: int = 0
    sort: tuple[str, str] | None = None  # ("created_at", "asc")


@dataclass
class Page(Generic[T]):
    """分页结果。"""

    items: list[T]
    total: int
    has_more: bool


@dataclass
class ReferenceReport:
    """引用完整性检查结果（`Storage.check_references` 的返回）。"""

    missing: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.missing


# --------------------------------------------------------------------------
# 预算与用量
# --------------------------------------------------------------------------


@dataclass
class BudgetUsage:
    """已消耗的预算（ERROR_HANDLING §6.4 的 `budget_used`）。"""

    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_estimate: float | None = None      # 累计**估算**
    cost_confirmed: float | None = None     # 累计**实际**（P4.5）
    currency: str = "UNKNOWN"

    def add(self, requests: int = 0, tokens_in: int | None = None,
            tokens_out: int | None = None, latency_ms: int = 0,
            cost_estimate: float | None = None,
            cost_confirmed: float | None = None,
            currency: str | None = None) -> "BudgetUsage":
        """累加一次消耗。**未知的量不加**（None 不等于 0）。

        P4.5：估算与实际**分开累加**，因为它们含义不同：
        `cost_estimate` 是事前按官方费率推算的，`cost_confirmed` 是 Provider
        回报的实际值。混在一起会让"这次到底花了多少"变成糊涂账。

        `currency` 只在有成本时跟随更新，避免用 `UNKNOWN` 覆盖掉已知单位。
        """
        self.requests += requests
        if tokens_in is not None:
            self.tokens_in += tokens_in
        if tokens_out is not None:
            self.tokens_out += tokens_out
        self.latency_ms += latency_ms
        if cost_estimate is not None:
            self.cost_estimate = (self.cost_estimate or 0.0) + cost_estimate
            if currency:
                self.currency = currency
        if cost_confirmed is not None:
            self.cost_confirmed = (self.cost_confirmed or 0.0) + cost_confirmed
            if currency:
                self.currency = currency
        return self

    def accountable_cost(self) -> float:
        """保守口径的"已消耗成本"：**取估算与实际的较大者**。

        为什么取大者：
        - 只有实际值（如 credits=0，未达计费粒度）时，真实消耗可能是 0.2 而回报 0
          → 用 0 会低估，导致连续请求绕过上限；
        - 只有估算值时，用它兜底；
        - 两者都没有 → 0.0（表示"本次没有可计量成本"，**不是"未知"**；
          未知另有专门的通道，见 `ExternalUsageLedger` 的 UNKNOWN 占位）。
        """
        estimate = self.cost_estimate or 0.0
        confirmed = self.cost_confirmed or 0.0
        return max(estimate, confirmed)


@dataclass
class UsageReport:
    """用量报告（`Provider.usage` 的返回，见 INTERFACES §2.3）。"""

    requests: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms_total: int = 0
    cost_estimate: float | None = None
    currency: str = "UNKNOWN"
    breakdown_by_actor: dict[str, int] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 任务相关的公共形状
# --------------------------------------------------------------------------


@dataclass
class ProjectRef:
    id: str
    name: str = ""
    entity_ids: list[str] = field(default_factory=list)


@dataclass
class TaskRef:
    id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.PENDING


@dataclass
class Progress:
    """任务进度（DATA_MODEL §4.12 的 `progress`）。`percent` 由总数与完成数算出。"""

    items_total: int = 0
    items_done: int = 0

    @property
    def percent(self) -> float:
        if self.items_total <= 0:
            return 0.0
        return round(self.items_done * 100.0 / self.items_total, 2)


@dataclass
class TaskStatusView:
    """`Orchestrator.get_task_status` 的返回（INTERFACES §12.3）。"""

    id: str
    status: TaskStatus
    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    progress: "Progress | None" = None
    last_error: str | None = None
    paused_reason: PausedReason | None = None


@dataclass
class StepOutcome:
    """推进一步的结果。注意 `paused=True` 也是**成功返回**（D-043）。"""

    stage: str
    advanced: bool = False
    paused: bool = False
    warnings: list = field(default_factory=list)


@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    artifact_ids: dict = field(default_factory=dict)


@dataclass
class Checkpoint:
    """检查点（INTERFACES §12.5）。`completed_units` 是恢复时不重复执行的第一道保险。"""

    task_id: str
    stage: str = ""
    cursor: str = ""
    completed_units: list[str] = field(default_factory=list)
    artifact_ids: dict = field(default_factory=dict)
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    resume_token: str = ""
    saved_at: str = ""  # ISO-8601（UTC）


@dataclass
class SourceMaterial:
    """一份**检索到的原始材料**（候选 claim 的原料）。

    放在 `core` 而不是某个业务模块里，是为了让 `L4 Research`（产出方）与
    `L4 抽取器`（消费方）都能引用它——ARCHITECTURE §3 规定**同层之间互不调用**，
    把共享形状下沉到 core 才能在不出循环依赖的前提下传递材料。

    ⚠️ 这是"材料"不是"证据"：未校验、未分级、未登记为 `EvidenceItem`。
    `snippet` 是检索返回的摘要，**不是正文全文**（正文抓取未实现，H-17）。
    """

    source_id: str = ""                 # 本次研究登记 / 去重命中的来源 ID
    title: str = ""
    url: str = ""
    canonical_key: str = ""
    snippet: str = ""                   # 检索摘要（非全文）
    published_at: str | None = None     # 原文字符串，未做日期推断
    source_kind: str = ""
    subject_entity_ids: list[str] = field(default_factory=list)
    target_display_names: list[str] = field(default_factory=list)
    query_text: str = ""


# 外部调用逐笔用量记录的状态常量（本文件只放**形状**，规则归 modules/usage_ledger）
OP_SEARCH = "SEARCH"
OP_EXTRACT = "EXTRACT"
OUTCOME_SUCCEEDED = "SUCCEEDED"
OUTCOME_FAILED = "FAILED"
COST_CONFIRMED = "CONFIRMED"
COST_UNKNOWN = "UNKNOWN"
COST_NOT_CHARGED = "NOT_CHARGED"
SOURCE_PROVIDER_USAGE = "PROVIDER_USAGE"
SOURCE_OFFICIAL_RATE = "OFFICIAL_RATE"
SOURCE_NONE = "NONE"
CURRENCY_CREDIT = "TAVILY_CREDIT"
CURRENCY_UNKNOWN = "UNKNOWN"


@dataclass
class ExternalUsageRecord:
    """一笔**外部调用**的逐笔用量记录（P4.5 任务一）。

    放在 `core` 而不是业务模块里，与 `SourceMaterial` 同一处理口径：
    `storage`（低层）与 `modules`（L4）都要引用它，下沉到 core 才不会循环依赖。

    # 三条硬规矩

    1. **估算与实际分开**：`cost_estimate` 是按官方费率**事前推算**的估算值；
       `cost_confirmed` 才是响应 `usage.credits` 的**实际回报值**。两者永不混用。
    2. **不知道就是不知道**：缺 `usage` / 超时 / 断连 / 服务端错误 →
       `cost_status=UNKNOWN`，**绝不填 0**（0 是"确认没花钱"的断言，未知不是 0）。
    3. **失败 ≠ 免费**：请求失败时费用状态是 `UNKNOWN`；只有官方明确说明不计费的
       情形（如单条 URL 抓取失败）才记为 `NOT_CHARGED`。

    `idempotency_key` 是**幂等主键**：由调用方在发起请求**之前**生成（服务端
    `request_id` 要等响应才知道，失败时根本拿不到）。同一个 key 结算两次会被账本
    忽略——这就是"重试/恢复不重复记账"的保证。
    """

    idempotency_key: str = ""
    provider: str = ""
    operation: str = ""                    # OP_SEARCH / OP_EXTRACT
    outcome: str = ""                      # SUCCEEDED / FAILED
    requested_at: str = ""

    request_id: str | None = None          # 服务端 request_id（失败时为 None）
    unit_count: int = 0                    # 请求规模：search=1；extract=URL 数
    success_unit_count: int | None = None  # 成功单元数（extract 成功 URL 数）

    cost_estimate: float | None = None     # 估算（事前，按官方费率）
    cost_confirmed: float | None = None    # 实际（响应 usage.credits）
    currency: str = CURRENCY_UNKNOWN
    cost_source: str = SOURCE_NONE
    cost_status: str = COST_UNKNOWN

    task_id: str | None = None
    run_id: str | None = None
    trace_ref: str = ""

    def to_row(self) -> tuple:
        """转成存储层可直接 INSERT 的行（顺序与 schema 保持一致）。"""
        return (
            self.idempotency_key,
            self.provider,
            self.operation,
            self.outcome,
            self.requested_at,
            self.request_id,
            self.unit_count,
            self.success_unit_count,
            self.cost_estimate,
            self.cost_confirmed,
            self.currency,
            self.cost_source,
            self.cost_status,
            self.task_id,
            self.run_id,
            self.trace_ref,
        )

    @classmethod
    def from_row(cls, row: Any) -> "ExternalUsageRecord":
        return cls(
            idempotency_key=row["idempotency_key"],
            provider=row["provider"],
            operation=row["operation"],
            outcome=row["outcome"],
            requested_at=row["requested_at"],
            request_id=row["request_id"],
            unit_count=int(row["unit_count"] or 0),
            success_unit_count=(int(row["success_unit_count"])
                                if row["success_unit_count"] is not None else None),
            cost_estimate=row["cost_estimate"],
            cost_confirmed=row["cost_confirmed"],
            currency=row["currency"],
            cost_source=row["cost_source"],
            cost_status=row["cost_status"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            trace_ref=row["trace_ref"] or "",
        )


@dataclass
class ExternalUsageTotals:
    """外部用量的汇总（**估算、实际、未知三者分开报**）。

    混在一起报会让"这次到底花了多少"变成一笔糊涂账，所以三种口径各占一列，
    调用方（与人工核对）可以一眼看出哪些是确认值、哪些只是估算、哪些根本不知道。
    """

    requests: int = 0
    confirmed_cost: float | None = None      # 已确认的实际费用（None = 一笔都没有）
    estimated_cost: float = 0.0              # 按官方费率推算的估算
    unknown_requests: int = 0                # 费用状态未知的请求数
    failed_requests: int = 0                 # 请求失败数（**不等于**未计费）
    by_operation: dict[str, int] = field(default_factory=dict)


@dataclass
class NamespaceGateReport:
    """命名空间闸门检查结果（用于 P1.2-B 的自检展示）。"""

    kind: str
    namespace: ProvenanceNamespace | str
    allowed: bool
