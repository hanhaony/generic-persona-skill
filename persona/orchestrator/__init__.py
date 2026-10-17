"""协调层（L5）。

P1.2-B：只有状态机与检查点的数据结构。
**P2.3 追加**：`records.py`（TaskRecord）、`tasks.py`（TaskService）、
`budget.py`（预算闸门）、`executor.py`（最小执行器 + Mock）。

⚠️ 仍然**不是**完整的任务引擎：没有阶段编排的业务语义、没有墙钟预算、
没有真实的研究/蒸馏/对话单元。它管的是"任务这门账"，不是"业务这份活"。
"""

from .assembly import (
    WARNING_STACK_ANALYZER_DISABLED,
    WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE,
    WARNING_STACK_EXTRACTOR_ABSENT,
    WARNING_STACK_LEDGER_HYDRATION_FAILED,
    WARNING_STACK_LEDGER_NOT_HYDRATED,
    WARNING_STACK_REAL_PROVIDER_BLOCKED,
    WARNING_STACK_SEARCH_OFFLINE,
    BusinessStack,
    StackOptions,
    TargetSpec,
    build_business_stack,
    materials_from_files,
    register_targets,
    work_id_for,
)
from .budget import (
    NEAR_LIMIT_RATIO,
    WARNING_BUDGET_NEAR_LIMIT,
    BudgetGate,
    gate_for,
)
from .business import (
    RUN_FAILED,
    RUN_PAUSED,
    RUN_PARTIAL,
    RUN_SUCCEEDED,
    STAGE_DIALOGUE,
    STAGE_EVIDENCE,
    STAGE_PERSONA,
    STAGE_RELATIONSHIP,
    STAGE_RESEARCH,
    STAGE_SEQUENCE,
    STEP_ALREADY_CURRENT,
    STEP_EXECUTED,
    STEP_FAILED,
    STEP_NOT_REACHED,
    STEP_SKIPPED,
    BusinessDeps,
    BusinessOrchestrator,
    BusinessRequest,
    BusinessRunResult,
    BusinessStepResult,
    EvidenceSeed,
)
from .checkpoint import idempotency_key, is_unit_done, mark_unit_done, new_checkpoint
from .executor import MockUnitHandler, TaskRunner, UnitOutcome, WorkUnit
from .model_setup import (
    DRY_RUN_SAMPLE_TARGETS,
    build_dry_run_request,
    chat_provider_config,
    chat_provider_from_settings,
    claim_extractor_from_settings,
    effective_max_cost,
    estimate_call_cost_usd,
    model_limits_from_settings,
    model_plan,
    sample_materials,
    unit_price_per_1k,
)
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
    # 业务编排器（H-10A）
    "BusinessOrchestrator",
    "BusinessDeps",
    # 业务链装配层（P4.6 / P0-1）
    "build_business_stack",
    "BusinessStack",
    "StackOptions",
    "TargetSpec",
    "register_targets",
    "work_id_for",
    "materials_from_files",
    "WARNING_STACK_LEDGER_NOT_HYDRATED",
    "WARNING_STACK_LEDGER_HYDRATION_FAILED",
    "WARNING_STACK_SEARCH_OFFLINE",
    "WARNING_STACK_EXTRACTOR_ABSENT",
    "WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE",
    "WARNING_STACK_REAL_PROVIDER_BLOCKED",
    "WARNING_STACK_ANALYZER_DISABLED",
    "BusinessRequest",
    "BusinessRunResult",
    "BusinessStepResult",
    "EvidenceSeed",
    "STAGE_SEQUENCE",
    "STAGE_RESEARCH",
    "STAGE_EVIDENCE",
    "STAGE_PERSONA",
    "STAGE_RELATIONSHIP",
    "STAGE_DIALOGUE",
    "RUN_SUCCEEDED",
    "RUN_PARTIAL",
    "RUN_PAUSED",
    "RUN_FAILED",
    "STEP_EXECUTED",
    "STEP_ALREADY_CURRENT",
    "STEP_SKIPPED",
    "STEP_FAILED",
    "STEP_NOT_REACHED",
    # 模型抽取装配（P4.4-B）
    "model_limits_from_settings",
    "claim_extractor_from_settings",
    "estimate_call_cost_usd",
    "unit_price_per_1k",
    "effective_max_cost",
    "model_plan",
    "build_dry_run_request",
    "sample_materials",
    "DRY_RUN_SAMPLE_TARGETS",
    # 真实聊天 Provider 装配（P4.4-C）
    "chat_provider_config",
    "chat_provider_from_settings",
]
