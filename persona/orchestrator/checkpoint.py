"""检查点辅助函数 —— 对应 docs/INTERFACES.md §12.5、ERROR_HANDLING §6.4/§6.5。

恢复时避免重复执行的两道保险（D-061）：
    1. `completed_units` 集合 —— 防"重复跑"；
    2. 下游幂等键 `f"{task_id}:{unit}"` —— 防"重复写入扣两次钱"。

⚠️ 本阶段只有数据结构与辅助函数，**没有任务引擎**（不实现 run_step / resume 的执行流程）。
"""

from __future__ import annotations

from ..core.ids import new_token
from ..core.temporal import to_iso, utc_now
from ..core.types import BudgetUsage, Checkpoint


def new_checkpoint(
    task_id: str,
    *,
    stage: str = "",
    cursor: str = "",
    completed_units: list[str] | None = None,
    artifact_ids: dict | None = None,
    budget_used: BudgetUsage | None = None,
) -> Checkpoint:
    """新建一个检查点（自动生成 `resume_token` 与 `saved_at`）。"""
    return Checkpoint(
        task_id=task_id,
        stage=stage,
        cursor=cursor,
        completed_units=list(completed_units or []),
        artifact_ids=dict(artifact_ids or {}),
        budget_used=budget_used or BudgetUsage(),
        resume_token=new_token("rst_"),
        saved_at=to_iso(utc_now()),
    )


def mark_unit_done(cp: Checkpoint, unit: str, *, artifact_ids: dict | None = None) -> Checkpoint:
    """标记一个工作单元已完成（幂等：重复标记不会产生重复项）。"""
    if unit not in cp.completed_units:
        cp.completed_units.append(unit)
    if artifact_ids:
        for key, value in artifact_ids.items():
            cp.artifact_ids.setdefault(key, []).extend(value)
    return cp


def is_unit_done(cp: Checkpoint, unit: str) -> bool:
    """resume 时据此跳过已完成单元（不调用 Provider，不计费）。"""
    return unit in cp.completed_units


def idempotency_key(task_id: str, unit: str) -> str:
    """下游幂等键（第二道保险）。"""
    return f"{task_id}:{unit}"
