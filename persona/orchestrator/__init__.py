"""协调层（L5）。

P1.2-B：只有状态机与检查点的数据结构。
**P2.3 追加**：`records.py`（TaskRecord）、`tasks.py`（TaskService）、
`budget.py`（预算闸门）、`executor.py`（最小执行器 + Mock）。

⚠️ 仍然**不是**完整的任务引擎：没有阶段编排的业务语义、没有墙钟预算、
没有真实的研究/蒸馏/对话单元。它管的是"任务这门账"，不是"业务这份活"。
"""

from .budget import (
    NEAR_LIMIT_RATIO,
    WARNING_BUDGET_NEAR_LIMIT,
    BudgetGate,
    gate_for,
)
from .checkpoint import idempotency_key, is_unit_done, mark_unit_done, new_checkpoint
from .executor import MockUnitHandler, TaskRunner, UnitOutcome, WorkUnit
from .records import (
    PROCESS_INTERRUPTED,
    BudgetSpec,
    CheckpointSummary,
    ErrorInfo,
    RetryPolicy,
    TaskRecord,
)
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
from .tasks import TaskDraft, TaskService

__all__ = [
    # 状态机
    "LEGAL_TRANSITIONS",
    "TERMINAL_STATES",
    "RESUMABLE_STATES",
    "TaskState",
    "can_transition",
    "is_terminal",
    "is_resumable",
    "transition_error",
    # 检查点
    "new_checkpoint",
    "mark_unit_done",
    "is_unit_done",
    "idempotency_key",
    # 任务记录
    "TaskRecord",
    "TaskDraft",
    "BudgetSpec",
    "RetryPolicy",
    "ErrorInfo",
    "CheckpointSummary",
    "PROCESS_INTERRUPTED",
    # 服务
    "TaskService",
    # 预算
    "BudgetGate",
    "gate_for",
    "NEAR_LIMIT_RATIO",
    "WARNING_BUDGET_NEAR_LIMIT",
    # 执行器
    "TaskRunner",
    "MockUnitHandler",
    "WorkUnit",
    "UnitOutcome",
]
