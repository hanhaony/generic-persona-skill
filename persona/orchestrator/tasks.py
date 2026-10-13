"""`TaskService` —— 任务的创建、查询、状态转换与持久化（INTERFACES §12.3）。

**职责**：把「任务」这门账管好——状态合法、落盘、可续跑。
**非职责**（D-093）：⛔ **不做任何业务**（不研究、不蒸馏、不对话）。
    真正干活的是 `executor.py` 的执行器，本服务只按它的要求改状态与存检查点。

⚠️ **诚实边界（重要）**：
    - 这里实现的是「基础闭环」：**创建 → 查询 → 状态转换 → 落盘 → 读回 → 续跑**。
    - **没有**实现完整的业务恢复：进程重启后能读到状态与检查点，
      如何重跑某个具体业务单元由 P2 之后的研究/证据管线决定。
    - 崩溃检测只有 `sweep_interrupted()`：把还是 `RUNNING` 的任务回退为
      `PAUSED(DEPENDENCY)`（ERROR_HANDLING §6.6）。它**不会自动帮你把活干完**。

给初学者的解释：
    本文件是"任务前台"。它不做饭，只记账：谁点了什么菜、做到第几道、
    停在哪、还能不能接着做。做饭的是 `executor.py` 里的"厨师"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..core.context import CallContext
from ..core.enums import PausedReason, TaskStatus, TaskType
from ..core.errors import AppError, ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, is_err, is_ok, unwrap
from ..core.temporal import to_iso, utc_now
from ..core.types import BudgetUsage, Checkpoint, Page, Progress, Query, TaskStatusView
from ..storage.sqlite import SqliteStorage
from .records import (
    PROCESS_INTERRUPTED,
    TASK_RECORD_NAMESPACE,
    BudgetSpec,
    ErrorInfo,
    RetryPolicy,
    TaskRecord,
)
from .state import can_transition, is_terminal, transition_error

TASK_KIND = "TaskRecord"


@dataclass
class TaskDraft:
    """创建任务的请求。字段与 `DATA_MODEL §4.12` 一一对应。"""

    task_type: TaskType | str
    stage_sequence: Sequence[str]
    target_entity_ids: Sequence[str] = ()
    budget_limit: BudgetSpec | None = None
    retry_policy: RetryPolicy | None = None
    idempotency_key: str | None = None
    depends_on_task_ids: Sequence[str] = ()


def _transition(
    record: TaskRecord,
    target: TaskStatus,
    *,
    reason: PausedReason | None = None,
    trace_ref: str = "",
) -> Result[None]:
    """按 `state.py` 的规则改状态。**非法转换不改任何字段**。

    （与 `TaskState.transition` 同一套规则，只是作用对象是 `TaskRecord`。）
    """
    if not can_transition(record.status, target):
        return Err(transition_error(record.status, target, trace_ref=trace_ref))
    if target == TaskStatus.PAUSED and reason is None:
        return Err(
            new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                      "进入 PAUSED 必须给出 paused_reason", trace_ref=trace_ref)
        )
    record.status = target
    record.paused_reason = reason if target == TaskStatus.PAUSED else None
    return Ok(None)


class TaskService:
    """任务的前台服务。所有写库都经过 `Storage`，不自己开连接。"""

    def __init__(self, storage: SqliteStorage, settings: Any = None) -> None:
        self.storage = storage
        self.settings = settings  # persona.config.AppSettings（可为空）

    # ---------------------------------------------------------------- 创建

    def create_task(self, ctx: CallContext, draft: TaskDraft) -> Result[TaskRecord]:
        """建一个 `PENDING` 任务。

        失败原因：
            `VALIDATION.unknown_task_type`   任务类型不在枚举里
            `VALIDATION.stage_sequence_empty` 阶段序列为空
        `TS-V4`：同一 `project_id` 下 `idempotency_key` 重复 → **返回原任务**，不新建。
        """
        try:
            task_type = TaskType(draft.task_type)
        except ValueError:
            return Err(
                new_error(ErrorCode.VALIDATION_UNKNOWN_TASK_TYPE,
                          f"未知的任务类型：{draft.task_type!r}", trace_ref=ctx.trace_ref)
            )

        stages = [str(s) for s in draft.stage_sequence if str(s)]
        if not stages:
            return Err(
                new_error(ErrorCode.VALIDATION_STAGE_SEQUENCE_EMPTY,
                          "stage_sequence 不能为空：没有阶段就无从推进",
                          trace_ref=ctx.trace_ref)
            )

        # TS-V4：重复提交直接返回原任务
        key = draft.idempotency_key or ""
        if key:
            existing = self._find_by_idempotency_key(ctx, key)
            if isinstance(existing, Err):
                return existing
            if existing.value is not None:
                return Ok(existing.value)

        budget_limit = draft.budget_limit or self._default_budget()
        retry_policy = draft.retry_policy or self._default_retry()
        now = to_iso(utc_now())
        record = TaskRecord(
            project_id=ctx.project_id,
            task_type=task_type,
            target_entity_ids=list(draft.target_entity_ids),
            stage_sequence=stages,
            current_stage=stages[0],
            completed_stages=[],
            status=TaskStatus.PENDING,
            progress=Progress(items_total=len(stages)),
            budget_limit=budget_limit,
            budget_used=BudgetUsage(),
            retry_policy=retry_policy,
            depends_on_task_ids=list(draft.depends_on_task_ids),
            idempotency_key=key,
            created_at=now,
            updated_at=now,
        )
        check = record.validate()
        if is_err(check):
            return check

        outcome = self.storage.put(
            ctx,
            TASK_KIND,
            record.to_payload(),
            project_id=ctx.project_id,
            namespace=TASK_RECORD_NAMESPACE,
        )
        if is_err(outcome):
            return outcome
        record.id = unwrap(outcome)
        return Ok(record)

    # ---------------------------------------------------------------- 读取

    def get(self, ctx: CallContext, task_id: str) -> Result[TaskRecord]:
        """读一个任务。不存在 → `Err(NOT_FOUND.task)`。"""
        row = self.storage.get(ctx, TASK_KIND, task_id)
        if is_err(row):
            return row
        if row.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_TASK, f"找不到任务 {task_id}",
                                 trace_ref=ctx.trace_ref))
        data = row.value
        return Ok(
            TaskRecord.from_payload(
                task_id,
                str(data.get("project_id", ctx.project_id)),
                data.get("payload", {}),
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
            )
        )

    def list_tasks(self, ctx: CallContext) -> Result[Page[TaskRecord]]:
        """列出当前项目下的全部任务（按创建时间升序）。"""
        result = self.storage.query(
            ctx, TASK_KIND, Query(filters={"project_id": ctx.project_id}, limit=200)
        )
        if is_err(result):
            return result
        page = unwrap(result)
        records = [
            TaskRecord.from_payload(
                row["id"],
                str(row.get("project_id", ctx.project_id)),
                row.get("payload", {}),
                created_at=str(row.get("created_at", "")),
                updated_at=str(row.get("updated_at", "")),
            )
            for row in page.items
        ]
        return Ok(Page(items=records, total=page.total, has_more=page.has_more))

    def status_view(self, ctx: CallContext, task_id: str) -> Result[TaskStatusView]:
        """`get_task_status`：给 CLI 与用户看的状态快照。"""
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        return Ok(
            TaskStatusView(
                id=record.id,
                status=record.status,
                current_stage=record.current_stage,
                completed_stages=list(record.completed_stages),
                budget_used=record.budget_used,
                progress=record.progress,
                last_error=record.last_error.message if record.last_error else None,
                paused_reason=record.paused_reason,
            )
        )

    # ---------------------------------------------------------------- 状态转换

    def start(self, ctx: CallContext, task_id: str) -> Result[TaskRecord]:
        return self._move(ctx, task_id, TaskStatus.RUNNING)

    def pause(
        self, ctx: CallContext, task_id: str, reason: PausedReason = PausedReason.USER
    ) -> Result[TaskRecord]:
        return self._move(ctx, task_id, TaskStatus.PAUSED, reason=reason)

    def resume(self, ctx: CallContext, task_id: str) -> Result[TaskRecord]:
        """`PAUSED → RUNNING`。**只有暂停中的任务能恢复**（ERROR_HANDLING §6.5 前三步）。

        失败原因：
            `STATE_CONFLICT.*`              状态不是 PAUSED（含终态）
            `VALIDATION.checkpoint_missing` 没有检查点，无从续跑
            `VALIDATION.artifact_missing`   检查点引用的产出已丢失（不许"假装没做过"）
        """
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)

        if record.status != TaskStatus.PAUSED:
            return Err(
                transition_error(record.status, TaskStatus.RUNNING, trace_ref=ctx.trace_ref)
            )

        checkpoint = self.storage.load_checkpoint(ctx, task_id)
        if is_err(checkpoint):
            return checkpoint
        if checkpoint.value is None:
            return Err(
                new_error(ErrorCode.VALIDATION_CHECKPOINT_MISSING,
                          f"任务 {task_id} 没有检查点，无法恢复", trace_ref=ctx.trace_ref)
            )

        artifact_ids: list[str] = []
        for values in unwrap(checkpoint).artifact_ids.values():
            if isinstance(values, list):
                artifact_ids.extend(str(v) for v in values)
        if artifact_ids:
            refs = self.storage.check_references(ctx, None, artifact_ids)
            if is_err(refs):
                return refs
            report = unwrap(refs)
            if not report.ok:
                return Err(
                    new_error(ErrorCode.VALIDATION_ARTIFACT_MISSING,
                              f"检查点引用的产出已丢失：{report.missing[:5]}"
                              f"（共 {len(report.missing)} 个）",
                              trace_ref=ctx.trace_ref)
                )
        return self._move(ctx, task_id, TaskStatus.RUNNING)

    def complete(self, ctx: CallContext, task_id: str) -> Result[TaskRecord]:
        return self._move(ctx, task_id, TaskStatus.COMPLETED)

    def fail(
        self, ctx: CallContext, task_id: str, error: AppError | str | None = None
    ) -> Result[TaskRecord]:
        """置为 `FAILED`。`last_error` **必须留下痕迹**（TS-V5），不允许静默清空。"""
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        if isinstance(error, AppError):
            record.last_error = ErrorInfo(code=str(error.code), message=error.message,
                                          trace_ref=error.trace_ref or ctx.trace_ref)
        elif isinstance(error, str):
            record.last_error = ErrorInfo(code="FAILED", message=error, trace_ref=ctx.trace_ref)
        moved = self._move(ctx, task_id, TaskStatus.FAILED, record=record)
        return moved

    def cancel(self, ctx: CallContext, task_id: str, reason: str = "") -> Result[TaskRecord]:
        """取消。**与暂停是两回事**（D-088）：取消后不可续跑。"""
        return self._move(ctx, task_id, TaskStatus.CANCELLED, note=reason)

    def set_budget(self, ctx: CallContext, task_id: str, limit: BudgetSpec) -> Result[TaskRecord]:
        """调整预算上限（ERROR_HANDLING §5.4：续跑的两个前提之一）。

        终态任务不允许再改额度（否则"已完成/已失败"的账就对不上了）。
        """
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        if is_terminal(record.status):
            return Err(
                new_error(ErrorCode.STATE_CONFLICT_TERMINAL_STATE,
                          f"任务已处于终态 {record.status}，不能再调整预算",
                          trace_ref=ctx.trace_ref)
            )
        record.budget_limit = limit
        outcome = self._persist(ctx, record)
        if is_err(outcome):
            return outcome
        return Ok(record)

    def mark_attempt(self, ctx: CallContext, task_id: str, error: AppError) -> Result[TaskRecord]:
        """记一次失败尝试：**状态不变**，只把 `attempt_count + 1` 并留 `last_error`（TS-V5）。

        这与 `fail()` 的区别：`fail()` 是**终态**决定，`mark_attempt()` 只是记账，
        重试次数是否耗尽由执行器判断（ERROR_HANDLING §4.4）。
        """
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        record.attempt_count += 1
        record.last_error = ErrorInfo(code=str(error.code), message=error.message,
                                      trace_ref=error.trace_ref or ctx.trace_ref)
        outcome = self._persist(ctx, record)
        if is_err(outcome):
            return outcome
        return Ok(record)

    def record_progress(
        self,
        ctx: CallContext,
        task_id: str,
        *,
        items_done: int,
        items_total: int | None = None,
        current_stage: str | None = None,
        completed_stage: str | None = None,
    ) -> Result[TaskRecord]:
        """推进进度与阶段（TS-V1：完成顺序必须与 `stage_sequence` 一致）。"""
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        record.progress.items_done = items_done
        if items_total is not None:
            record.progress.items_total = items_total
        if completed_stage and completed_stage not in record.completed_stages:
            record.completed_stages.append(completed_stage)
        if current_stage:
            record.current_stage = current_stage
        check = record.validate()
        if is_err(check):
            return check
        outcome = self._persist(ctx, record)
        if is_err(outcome):
            return outcome
        return Ok(record)

    # ---------------------------------------------------------------- 检查点

    def save_checkpoint(self, ctx: CallContext, task_id: str, cp: Checkpoint) -> Result[str]:
        """写检查点（覆盖式，幂等），并把摘要镜像回 `TaskRecord`（D-090）。

        顺序：**先落检查点**，再改任务状态 —— 反过来会丢进度（ERROR_HANDLING §5.1）。
        """
        outcome = self.storage.save_checkpoint(ctx, task_id, cp)
        if is_err(outcome):
            return outcome
        token = unwrap(outcome)
        task = self.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        record.checkpoint.resume_token = token
        record.checkpoint.saved_at = cp.saved_at
        record.checkpoint.stage = cp.stage
        record.checkpoint.cursor = cp.cursor
        record.budget_used = cp.budget_used
        return self._persist_token(ctx, record, token)

    def _persist_token(self, ctx: CallContext, record: TaskRecord, token: str) -> Result[str]:
        outcome = self._persist(ctx, record)
        if is_err(outcome):
            return outcome
        return Ok(token)

    def load_checkpoint(self, ctx: CallContext, task_id: str) -> Result[Checkpoint | None]:
        return self.storage.load_checkpoint(ctx, task_id)

    # ---------------------------------------------------------------- 用量

    def record_usage(
        self, ctx: CallContext, task_id: str, usage: UsageDelta, *, actor: str = "orchestrator"
    ) -> Result[None]:
        """记一笔用量（scope = task_id）。token 未知写 `NULL`，**绝不估算**（D-060）。"""
        return self.storage.record_usage(ctx, task_id, usage, actor=actor)

    # ---------------------------------------------------------------- 崩溃恢复

    def sweep_interrupted(self, ctx: CallContext) -> Result[list[str]]:
        """把"上次没来得及收尾"的 `RUNNING` 任务回退为 `PAUSED(DEPENDENCY)`。

        ERROR_HANDLING §6.6：进程被杀后我们**不知道它到底跑到哪**，
        所以不敢标 `COMPLETED`，也不敢标 `FAILED`，只能安全地退回 `PAUSED`
        并留下 `PROCESS_INTERRUPTED` 痕迹。

        返回被回退的 `task_id` 列表。
        """
        result = self.list_tasks(ctx)
        if is_err(result):
            return result
        fixed: list[str] = []
        for record in unwrap(result).items:
            if record.status != TaskStatus.RUNNING:
                continue
            record.last_error = ErrorInfo(
                code=PROCESS_INTERRUPTED,
                message="进程中断：状态已安全回退为 PAUSED，检查点保留",
                trace_ref=ctx.trace_ref,
            )
            outcome = self._move(ctx, record.id, TaskStatus.PAUSED,
                                 reason=PausedReason.DEPENDENCY, record=record)
            if is_ok(outcome):
                fixed.append(record.id)
        return Ok(fixed)

    # ---------------------------------------------------------------- 内部

    def _move(
        self,
        ctx: CallContext,
        task_id: str,
        target: TaskStatus,
        *,
        reason: PausedReason | None = None,
        record: TaskRecord | None = None,
        note: str = "",
    ) -> Result[TaskRecord]:
        """读取 → 转换 → 校验 → 落盘。**任何一步失败都不改库**。"""
        if record is None:
            found = self.get(ctx, task_id)
            if is_err(found):
                return found
            record = unwrap(found)
        if note and target == TaskStatus.CANCELLED:
            record.last_error = ErrorInfo(code="CANCELLED", message=note or "用户取消",
                                          trace_ref=ctx.trace_ref)
        moved = _transition(record, target, reason=reason, trace_ref=ctx.trace_ref)
        if is_err(moved):
            return moved
        check = record.validate()
        if is_err(check):
            return check
        outcome = self._persist(ctx, record)
        if is_err(outcome):
            return outcome
        return Ok(record)

    def _persist(self, ctx: CallContext, record: TaskRecord) -> Result[dict[str, Any]]:
        record.updated_at = to_iso(utc_now())
        return self.storage.update(ctx, TASK_KIND, record.id, record.to_payload())

    def _find_by_idempotency_key(self, ctx: CallContext, key: str) -> Result[TaskRecord | None]:
        result = self.list_tasks(ctx)
        if is_err(result):
            return result
        for record in unwrap(result).items:
            if record.idempotency_key == key:
                return Ok(record)
        return Ok(None)

    def _default_budget(self) -> BudgetSpec:
        """默认预算来自**配置**（D-077：只有配置模块能读环境变量）。"""
        if self.settings is None:
            return BudgetSpec()
        return BudgetSpec.from_budget_limits(getattr(self.settings, "budget", None))

    def _default_retry(self) -> RetryPolicy:
        if self.settings is None:
            return RetryPolicy()
        return RetryPolicy.from_retry_limits(getattr(self.settings, "retry", None))
