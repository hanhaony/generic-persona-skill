"""`CallContext` —— 每个跨模块操作的**第一个参数**（INTERFACES §1.3）。

作用：追踪（谁改的）、计量（算在哪个任务的预算里）、幂等（重复调用不重复扣费）。
规则：**没有 `CallContext` 的操作不得写库，也不得调用 Provider**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ids import new_token


@dataclass(frozen=True)
class CallContext:
    project_id: str
    actor: str                  # 调用方模块名：cli / orchestrator / research / evidence / ...
    task_id: str | None = None  # 所属任务；为空表示这次操作不计入任务预算
    budget_scope: str = ""      # 预算归属，通常 == task_id
    trace_ref: str = ""         # 贯穿日志与错误的追踪 ID
    idempotency_key: str | None = None
    dry_run: bool = False       # 只校验不落盘，必须无副作用

    def __post_init__(self) -> None:
        if not self.project_id:
            raise ValueError("CallContext.project_id 不能为空")
        if not self.actor:
            raise ValueError("CallContext.actor 不能为空")
        # frozen 数据类里用 object.__setattr__ 补齐派生字段
        if not self.budget_scope:
            object.__setattr__(self, "budget_scope", self.task_id or self.project_id)
        if not self.trace_ref:
            object.__setattr__(self, "trace_ref", new_token("trc_"))

    def for_actor(self, actor: str) -> "CallContext":
        """派生一个换了 actor 的上下文（trace_ref 保持不变，便于串联）。"""
        return CallContext(
            project_id=self.project_id,
            actor=actor,
            task_id=self.task_id,
            budget_scope=self.budget_scope,
            trace_ref=self.trace_ref,
            idempotency_key=self.idempotency_key,
            dry_run=self.dry_run,
        )

    def with_idempotency(self, key: str) -> "CallContext":
        """带上幂等键（恢复任务时用 `f"{task_id}:{unit}"`）。"""
        return CallContext(
            project_id=self.project_id,
            actor=self.actor,
            task_id=self.task_id,
            budget_scope=self.budget_scope,
            trace_ref=self.trace_ref,
            idempotency_key=key,
            dry_run=self.dry_run,
        )


def new_call_context(
    project_id: str,
    actor: str,
    *,
    task_id: str | None = None,
    trace_ref: str = "",
    idempotency_key: str | None = None,
    dry_run: bool = False,
    **_: Any,
) -> CallContext:
    """创建一个调用上下文（自动补 trace_ref）。"""
    return CallContext(
        project_id=project_id,
        actor=actor,
        task_id=task_id,
        trace_ref=trace_ref,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
    )
