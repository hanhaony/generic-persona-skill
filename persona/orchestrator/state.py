"""任务六态状态机 —— 对应 docs/INTERFACES.md §12.2、ERROR_HANDLING.md §6.2。

六态（D-052）：`PENDING` / `RUNNING` / `PAUSED` / `COMPLETED` / `FAILED` / `CANCELLED`。
暂停原因单独存 `paused_reason`，**不新增状态名**。

给初学者的解释：
    状态机就是一张"从哪到哪允许走"的表。走不通的地方**不改变原状态**，
    直接返回一个错误——这样状态永远不会被"改坏"。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core.enums import PausedReason, TaskStatus
from ..core.errors import AppError, ErrorCode, new_error
from ..core.result import Err, Ok, Result

TERMINAL_STATES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
RESUMABLE_STATES: frozenset[TaskStatus] = frozenset({TaskStatus.PAUSED})

# 合法转换表：与 ERROR_HANDLING §6.2 的表格逐格对应
LEGAL_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.RUNNING: frozenset(
        {TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def is_terminal(status: TaskStatus) -> bool:
    return status in TERMINAL_STATES


def is_resumable(status: TaskStatus) -> bool:
    """只有 `PAUSED` 能续跑。`COMPLETED` / `FAILED` / `CANCELLED` 都不行。"""
    return status in RESUMABLE_STATES


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, frozenset())


def transition_error(current: TaskStatus, target: TaskStatus, *, trace_ref: str = "") -> AppError:
    """为一次非法转换生成错误。**不改变原状态**。"""
    if current in TERMINAL_STATES:
        return new_error(ErrorCode.STATE_CONFLICT_TERMINAL_STATE,
                         f"任务已处于终态 {current}，不能再转换到 {target}", trace_ref=trace_ref)
    if target == TaskStatus.RUNNING:
        return new_error(ErrorCode.STATE_CONFLICT_NOT_PENDING,
                         f"只有 PENDING（start）或 PAUSED（resume）能进入 RUNNING，当前是 {current}",
                         trace_ref=trace_ref)
    if target == TaskStatus.PAUSED:
        return new_error(ErrorCode.STATE_CONFLICT_NOT_RUNNING,
                         f"只有 RUNNING 能暂停，当前是 {current}", trace_ref=trace_ref)
    return new_error(ErrorCode.STATE_CONFLICT_NOT_RUNNING,
                     f"从 {current} 不能转换到 {target}", trace_ref=trace_ref)


@dataclass
class TaskState:
    """一个任务当前的状态快照（不含业务逻辑，只管状态合法性）。"""

    task_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    paused_reason: PausedReason | None = None
    last_error: str | None = None

    def transition(
        self,
        target: TaskStatus,
        *,
        reason: PausedReason | None = None,
        trace_ref: str = "",
    ) -> Result[TaskStatus]:
        """尝试转换。非法则返回 `Err`，**当前状态保持不变**。"""
        if not can_transition(self.status, target):
            return Err(transition_error(self.status, target, trace_ref=trace_ref))

        # 先校验再改状态：任何失败都不许把状态改坏
        if target == TaskStatus.PAUSED and reason is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "进入 PAUSED 必须给出 paused_reason", trace_ref=trace_ref))

        self.status = target
        self.paused_reason = reason if target == TaskStatus.PAUSED else None
        return Ok(self.status)

    def start(self, **kw) -> Result[TaskStatus]:
        return self.transition(TaskStatus.RUNNING, **kw)

    def pause(self, reason: PausedReason = PausedReason.USER, **kw) -> Result[TaskStatus]:
        return self.transition(TaskStatus.PAUSED, reason=reason, **kw)

    def resume(self, **kw) -> Result[TaskStatus]:
        return self.transition(TaskStatus.RUNNING, **kw)

    def complete(self, **kw) -> Result[TaskStatus]:
        return self.transition(TaskStatus.COMPLETED, **kw)

    def fail(self, message: str = "", **kw) -> Result[TaskStatus]:
        result = self.transition(TaskStatus.FAILED, **kw)
        if isinstance(result, Ok):
            self.last_error = message or None
        return result

    def cancel(self, **kw) -> Result[TaskStatus]:
        return self.transition(TaskStatus.CANCELLED, **kw)
