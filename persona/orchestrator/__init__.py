"""协调层（L5）。本阶段只有状态机与检查点的数据结构，**不含任务引擎**。"""

from .checkpoint import idempotency_key, is_unit_done, mark_unit_done, new_checkpoint
from .state import (
    LEGAL_TRANSITIONS,
    RESUMABLE_STATES,
    TERMINAL_STATES,
    TaskState,
    can_transition,
    is_resumable,
    is_terminal,
    transition_error,
)

__all__ = [
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "RESUMABLE_STATES",
    "TaskState",
    "can_transition",
    "is_terminal",
    "is_resumable",
    "transition_error",
    "new_checkpoint",
    "mark_unit_done",
    "is_unit_done",
    "idempotency_key",
]
