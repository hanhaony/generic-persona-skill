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
    cost_estimate: float | None = None
    currency: str = "UNKNOWN"

    def add(self, requests: int = 0, tokens_in: int | None = None,
            tokens_out: int | None = None, latency_ms: int = 0) -> "BudgetUsage":
        """累加一次消耗。**未知的量不加**（None 不等于 0）。"""
        self.requests += requests
        if tokens_in is not None:
            self.tokens_in += tokens_in
        if tokens_out is not None:
            self.tokens_out += tokens_out
        self.latency_ms += latency_ms
        return self


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
class NamespaceGateReport:
    """命名空间闸门检查结果（用于 P1.2-B 的自检展示）。"""

    kind: str
    namespace: ProvenanceNamespace | str
    allowed: bool
