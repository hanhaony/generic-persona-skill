"""`TaskRecord` —— 长任务的状态载体（DATA_MODEL §4.12）。

**责任边界**：只管「做到哪了、怎么接着做」。
产出内容一律不在本对象里——这里只存**产出的 ID**（`checkpoint.artifact_ids`）。

给初学者的解释：
    `TaskRecord` 就像一张「施工进度单」：写了这个工程分几步（stage_sequence）、
    现在做到第几步（current_stage）、哪些步已完成（completed_stages）、
    花了多少钱（budget_used）、上限多少（budget_limit）、
    上次停在哪（checkpoint 摘要）、以及万一出事的原因（last_error）。
    它**不保存施工成果本身**，成果在各自的实体表里，这里只留门牌号（ID）。

序列化说明（D-090）：
    **完整的检查点存在 `checkpoints` 表里**，本对象只镜像 4 个摘要字段
    （`resume_token` / `saved_at` / `stage` / `cursor`），避免两处数据打架。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from ..core.enums import PausedReason, TaskStatus, TaskType
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result
from ..core.types import BudgetUsage, Progress

# 崩溃/重启时写入 `last_error` 的标记（ERROR_HANDLING §6.6）
PROCESS_INTERRUPTED = "PROCESS_INTERRUPTED"

# 命名空间：任务记录是**系统账本**，不是业务资料（闸门表里没有 TaskRecord，故自选）
TASK_RECORD_NAMESPACE = "SYSTEM_DERIVED"


@dataclass
class RetryPolicy:
    """最小重试策略（DATA_MODEL §4.12 的 `retry_policy`）。

    由 `persona.config` 的 `RetryLimits` 转换而来，**执行器不自己读配置**。
    """

    max_attempts: int = 3
    backoff_base_ms: int = 500
    backoff_max_ms: int = 8_000
    jitter: bool = True

    @classmethod
    def from_retry_limits(cls, limits: Any) -> "RetryPolicy":
        """从 `persona.config.spec.RetryLimits` 构造（只取执行器用得到的字段）。"""
        return cls(
            max_attempts=int(getattr(limits, "max_retries", 3)) + 1,  # 首次 + 重试次数
            backoff_base_ms=int(getattr(limits, "backoff_base_ms", 500)),
            backoff_max_ms=int(getattr(limits, "backoff_max_ms", 8_000)),
            jitter=bool(getattr(limits, "jitter", True)),
        )


@dataclass
class BudgetSpec:
    """一次任务的预算上限（DATA_MODEL §4.12 的 `budget_limit`）。

    `None` = 这一项**不限制**。由 `persona.config.spec.BudgetLimits` 转换而来。
    """

    max_requests: int | None = None
    max_tokens_in: int | None = None
    max_tokens_out: int | None = None
    max_cost: float | None = None
    max_duration_sec: int | None = None

    @classmethod
    def from_budget_limits(cls, limits: Any) -> "BudgetSpec":
        return cls(
            max_requests=getattr(limits, "max_requests", None),
            max_tokens_in=getattr(limits, "max_tokens_in", None),
            max_tokens_out=getattr(limits, "max_tokens_out", None),
            max_cost=getattr(limits, "max_cost", None),
            max_duration_sec=getattr(limits, "max_duration_sec", None),
        )


@dataclass
class CheckpointSummary:
    """`TaskRecord` 里的检查点**摘要**（完整数据在 `checkpoints` 表，见 D-090）。"""

    resume_token: str = ""
    saved_at: str = ""
    stage: str = ""
    cursor: str = ""


@dataclass
class ErrorInfo:
    """最后一次失败的记录（DATA_MODEL §4.12 的 `last_error`）。

    `TS-V5`：非空时不得被静默清空——失败必须留痕。
    """

    code: str = ""
    message: str = ""
    trace_ref: str = ""


@dataclass
class TaskRecord:
    """一次长任务的完整状态。"""

    id: str = ""
    project_id: str = ""
    task_type: TaskType = TaskType.RESEARCH
    target_entity_ids: list[str] = field(default_factory=list)
    stage_sequence: list[str] = field(default_factory=list)
    current_stage: str = ""
    completed_stages: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    paused_reason: PausedReason | None = None
    progress: Progress = field(default_factory=Progress)
    checkpoint: CheckpointSummary = field(default_factory=CheckpointSummary)
    budget_limit: BudgetSpec = field(default_factory=BudgetSpec)
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    attempt_count: int = 0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    depends_on_task_ids: list[str] = field(default_factory=list)
    idempotency_key: str = ""
    last_error: ErrorInfo | None = None
    created_at: str = ""
    updated_at: str = ""

    # ---------------------------------------------------------------- 序列化

    def to_payload(self) -> dict[str, Any]:
        """转成可写进 `records.payload` 的字典（JSON 安全）。"""
        return {
            "task_type": self.task_type.value,
            "target_entity_ids": list(self.target_entity_ids),
            "stage_sequence": list(self.stage_sequence),
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "status": self.status.value,
            "paused_reason": self.paused_reason.value if self.paused_reason else None,
            "progress": {
                "items_total": self.progress.items_total,
                "items_done": self.progress.items_done,
            },
            "checkpoint": {
                "resume_token": self.checkpoint.resume_token,
                "saved_at": self.checkpoint.saved_at,
                "stage": self.checkpoint.stage,
                "cursor": self.checkpoint.cursor,
            },
            "budget_limit": {
                "max_requests": self.budget_limit.max_requests,
                "max_tokens_in": self.budget_limit.max_tokens_in,
                "max_tokens_out": self.budget_limit.max_tokens_out,
                "max_cost": self.budget_limit.max_cost,
                "max_duration_sec": self.budget_limit.max_duration_sec,
            },
            "budget_used": {
                "requests": self.budget_used.requests,
                "tokens_in": self.budget_used.tokens_in,
                "tokens_out": self.budget_used.tokens_out,
                "latency_ms": self.budget_used.latency_ms,
                "cost_estimate": self.budget_used.cost_estimate,
                "currency": self.budget_used.currency,
            },
            "attempt_count": self.attempt_count,
            "retry_policy": {
                "max_attempts": self.retry_policy.max_attempts,
                "backoff_base_ms": self.retry_policy.backoff_base_ms,
                "backoff_max_ms": self.retry_policy.backoff_max_ms,
                "jitter": self.retry_policy.jitter,
            },
            "depends_on_task_ids": list(self.depends_on_task_ids),
            "idempotency_key": self.idempotency_key,
            "last_error": (
                {
                    "code": self.last_error.code,
                    "message": self.last_error.message,
                    "trace_ref": self.last_error.trace_ref,
                }
                if self.last_error
                else None
            ),
        }

    @classmethod
    def from_payload(
        cls, task_id: str, project_id: str, payload: Mapping[str, Any],
        *, created_at: str = "", updated_at: str = "",
    ) -> "TaskRecord":
        """从 `records.payload` 还原。**未知/缺失字段一律用默认值**，不猜。"""
        budget_limit = dict(payload.get("budget_limit") or {})
        budget_used = dict(payload.get("budget_used") or {})
        progress = dict(payload.get("progress") or {})
        checkpoint = dict(payload.get("checkpoint") or {})
        retry = dict(payload.get("retry_policy") or {})
        error = payload.get("last_error")
        return cls(
            id=task_id,
            project_id=project_id,
            task_type=TaskType(payload.get("task_type", TaskType.RESEARCH.value)),
            target_entity_ids=list(payload.get("target_entity_ids") or []),
            stage_sequence=list(payload.get("stage_sequence") or []),
            current_stage=str(payload.get("current_stage", "")),
            completed_stages=list(payload.get("completed_stages") or []),
            status=TaskStatus(payload.get("status", TaskStatus.PENDING.value)),
            paused_reason=(
                PausedReason(payload["paused_reason"]) if payload.get("paused_reason") else None
            ),
            progress=Progress(
                items_total=int(progress.get("items_total", 0)),
                items_done=int(progress.get("items_done", 0)),
            ),
            checkpoint=CheckpointSummary(
                resume_token=str(checkpoint.get("resume_token", "")),
                saved_at=str(checkpoint.get("saved_at", "")),
                stage=str(checkpoint.get("stage", "")),
                cursor=str(checkpoint.get("cursor", "")),
            ),
            budget_limit=BudgetSpec(
                max_requests=budget_limit.get("max_requests"),
                max_tokens_in=budget_limit.get("max_tokens_in"),
                max_tokens_out=budget_limit.get("max_tokens_out"),
                max_cost=budget_limit.get("max_cost"),
                max_duration_sec=budget_limit.get("max_duration_sec"),
            ),
            budget_used=BudgetUsage(
                requests=int(budget_used.get("requests", 0)),
                tokens_in=int(budget_used.get("tokens_in", 0)),
                tokens_out=int(budget_used.get("tokens_out", 0)),
                latency_ms=int(budget_used.get("latency_ms", 0)),
                cost_estimate=budget_used.get("cost_estimate"),
                currency=str(budget_used.get("currency", "UNKNOWN")),
            ),
            attempt_count=int(payload.get("attempt_count", 0)),
            retry_policy=RetryPolicy(
                max_attempts=int(retry.get("max_attempts", 3)),
                backoff_base_ms=int(retry.get("backoff_base_ms", 500)),
                backoff_max_ms=int(retry.get("backoff_max_ms", 8_000)),
                jitter=bool(retry.get("jitter", True)),
            ),
            depends_on_task_ids=list(payload.get("depends_on_task_ids") or []),
            idempotency_key=str(payload.get("idempotency_key", "")),
            last_error=(
                ErrorInfo(
                    code=str(error.get("code", "")),
                    message=str(error.get("message", "")),
                    trace_ref=str(error.get("trace_ref", "")),
                )
                if isinstance(error, Mapping)
                else None
            ),
            created_at=created_at,
            updated_at=updated_at,
        )

    # ---------------------------------------------------------------- 校验

    def validate(self) -> Result[None]:
        """`TS-V1 / TS-V6` 的最小校验：阶段序列有序、暂停原因与状态自洽。"""
        if self.stage_sequence and self.completed_stages:
            expected = self.stage_sequence[: len(self.completed_stages)]
            if list(self.completed_stages) != list(expected):
                return Err(
                    new_error(
                        ErrorCode.VALIDATION_REQUIRED_FIELD,
                        "TS-V1 违反：completed_stages 必须与 stage_sequence 的前缀一致"
                        f"（stage_sequence={self.stage_sequence}, "
                        f"completed_stages={self.completed_stages}）",
                    )
                )
        if self.status == TaskStatus.PAUSED and self.paused_reason is None:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                          "TS-V6 违反：status=PAUSED 时 paused_reason 必填")
            )
        if self.status != TaskStatus.PAUSED and self.paused_reason is not None:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                          "TS-V6 违反：非 PAUSED 状态下 paused_reason 必须为 None")
            )
        return Ok(None)
