"""最小执行器 —— 任务"推进一步"的骨架（INTERFACES §12.3 的 `run_step` / `run_to_completion`）。

**职责**：① 挑下一个没做的工作单元；② 先查预算；③ 调 handler 干活；④ 记账；⑤ 落检查点。
**非职责**：⛔ **不知道具体业务是什么**。干活的是 `UnitHandler`，本阶段只有 `MockUnitHandler`（D-093）。

⚠️ **诚实边界**：
    - 这里跑的是**抽象的"工作单元"**，不是真实的人物研究 / 证据提取 / 对话。
    - 预算闸门是真的（会真的拦住、真的暂停），但**墙钟时间上限**（`max_duration_sec`）
      本阶段未接入——`BudgetGate` 只算次数 / token / 金额。

给初学者的解释：
    把任务想成一条装配线。`run_step` 就是"推一步传送带"：
    先看下一个零件（工作单元）在不在"已做清单"里（幂等），
    再看物料够不够（预算），够了才动手，做完立刻在"进度本"上打勾（检查点）。
    物料不够时**不是报废**，而是关机存档（暂停），等补料再开机接着干（resume）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

from ..core.context import CallContext
from ..core.enums import PausedReason, TaskStatus
from ..core.errors import ErrorCode, new_error
from ..core.result import (
    Err,
    Ok,
    Result,
    UsageDelta,
    WarningNote,
    error_of,
    is_err,
    is_ok,
    unwrap,
)
from ..core.types import StepOutcome, TaskResult
from .budget import WARNING_BUDGET_NEAR_LIMIT, gate_for
from .checkpoint import idempotency_key, is_unit_done, mark_unit_done, new_checkpoint
from .records import TaskRecord
from .tasks import TaskService


@dataclass
class WorkUnit:
    """一个"可划分的工作单元"（ERROR_HANDLING §6.4 的 unit）。

    `name` 必须在一个任务内唯一——它是幂等的钥匙。
    """

    name: str
    stage: str = ""
    estimate: UsageDelta = field(default_factory=UsageDelta)


@dataclass
class UnitOutcome:
    """一个单元干完的结果：产出了哪些 ID、实际花了多少。"""

    artifact_ids: dict[str, list[str]] = field(default_factory=dict)
    usage: UsageDelta | None = None


class UnitHandler(Protocol):
    """"真正干活的人"要遵守的协议。业务模块将来实现它即可接入。"""

    def plan(self, task: TaskRecord) -> list[WorkUnit]:
        """列出这个任务要做的全部单元（顺序即执行顺序）。"""
        ...

    def execute(
        self, ctx: CallContext, unit: WorkUnit, *, idempotency_key: str
    ) -> Result[UnitOutcome]:
        """执行一个单元。**必须可重复调用而不产生副作用**（幂等，ERROR_HANDLING §4.3）。"""
        ...


class MockUnitHandler:
    """**本阶段唯一实现**（D-093）：不联网、不调模型、结果完全确定。

    它做的事只有一件：把"我叫过它"记进 `calls`，方便测试断言**没有重复执行**。
    """

    def __init__(
        self,
        units: Sequence[WorkUnit] | None = None,
        *,
        fail_on: Sequence[str] = (),
        error_code: ErrorCode = ErrorCode.EXTERNAL_UNAVAILABLE,
        requests_per_unit: int = 1,
        tokens: tuple[int | None, int | None] = (None, None),
        emit_artifacts: bool = False,
    ) -> None:
        self.units: list[WorkUnit] | None = list(units) if units is not None else None
        self.fail_on = set(fail_on)
        self.error_code = error_code
        self.requests_per_unit = requests_per_unit
        self.tokens = tokens
        # 默认**不产出**任何实体 ID：Mock 不写真实记录，
        # 否则 resume 的"产出完整性检查"会因为这些假 ID 不存在而正确报错（artifact_missing）。
        self.emit_artifacts = emit_artifacts
        self.calls: list[tuple[str, str]] = []  # (unit.name, idempotency_key)

    # ------------------------------------------------------------ 协议实现

    def plan(self, task: TaskRecord) -> list[WorkUnit]:
        if self.units is not None:
            return list(self.units)
        # 没给单元清单时，按任务的阶段序列各生成一个单元
        return [WorkUnit(name=f"{stage}:0", stage=stage) for stage in task.stage_sequence]

    def execute(
        self, ctx: CallContext, unit: WorkUnit, *, idempotency_key: str = ""
    ) -> Result[UnitOutcome]:
        self.calls.append((unit.name, idempotency_key))
        if unit.name in self.fail_on:
            return Err(
                new_error(self.error_code, f"（Mock）单元 {unit.name} 按脚本注入失败",
                          trace_ref=ctx.trace_ref)
            )
        tokens_in, tokens_out = self.tokens
        return Ok(
            UnitOutcome(
                artifact_ids={"unit_ids": [f"art_{unit.name}"]} if self.emit_artifacts else {},
                usage=UsageDelta(
                    requests=self.requests_per_unit,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                ),
            )
        )

    # ------------------------------------------------------------ 测试辅助

    def call_names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def call_count(self, unit_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == unit_name)


class TaskRunner:
    """把 `TaskService`（记账）与 `UnitHandler`（干活）串起来的最小执行器。"""

    def __init__(self, service: TaskService, handler: UnitHandler) -> None:
        self.service = service
        self.handler = handler

    # ---------------------------------------------------------------- 单步

    def run_step(self, ctx: CallContext, task_id: str) -> Result[StepOutcome]:
        """推进一步。**任何情况下都不会悄悄绕过预算检查**。

        返回：
          - `Ok(StepOutcome{advanced=True})`  完成了一个单元
          - `Ok(StepOutcome{paused=True})`    预算不足 → 已落检查点并暂停（**不是失败**）
          - `Ok(StepOutcome{advanced=False})` 没有剩余单元 → 任务置 `COMPLETED`
          - `Err(...)`                        状态不对 / handler 失败 / 存储失败
        """
        task = self.service.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)

        if record.status != TaskStatus.RUNNING:
            return Err(
                new_error(
                    ErrorCode.STATE_CONFLICT_NOT_RUNNING,
                    f"任务当前是 {record.status}，只有 RUNNING 才能推进一步",
                    trace_ref=ctx.trace_ref,
                )
            )

        checkpoint = self.service.load_checkpoint(ctx, task_id)
        if is_err(checkpoint):
            return checkpoint
        cp = checkpoint.value
        if cp is None:
            cp = new_checkpoint(task_id, stage=record.current_stage)
            cp.budget_used = record.budget_used

        units = self.handler.plan(record)
        remaining = [u for u in units if not is_unit_done(cp, u.name)]

        # ① 没有剩余单元 → 收尾
        if not remaining:
            outcome = self.service.complete(ctx, task_id)
            if is_err(outcome):
                return outcome
            return Ok(StepOutcome(stage=cp.stage or record.current_stage, advanced=False))

        unit = remaining[0]
        stage_name = unit.stage or record.current_stage

        # ② 预算闸门：检查 →（预占）→ 执行 → 记账（D-089）
        gate = gate_for(record.budget_limit, record.budget_used)
        check = gate.check(unit.estimate, trace_ref=ctx.trace_ref)
        if is_err(check):
            # 先落检查点，再暂停（顺序不能反，否则进度会丢）
            cp.stage = stage_name
            cp.cursor = unit.name
            cp.budget_used = record.budget_used
            self.service.save_checkpoint(ctx, task_id, cp)
            paused = self.service.pause(ctx, task_id, PausedReason.BUDGET)
            if is_err(paused):
                return paused
            return Ok(
                StepOutcome(
                    stage=stage_name,
                    paused=True,
                    warnings=[WarningNote(code=str(ErrorCode.BUDGET_EXCEEDED),
                                          message="已达预算上限，进度已保存")],
                )
            )

        warnings: list[WarningNote] = list(check.warnings)

        # ③ 预占 → 执行
        gate.reserve(unit.estimate)
        step_ctx = ctx.with_idempotency(idempotency_key(task_id, unit.name))
        result = self.handler.execute(step_ctx, unit, idempotency_key=step_ctx.idempotency_key or "")
        if is_err(result):
            gate.release(unit.estimate)
            error = error_of(result)
            self.service.mark_attempt(ctx, task_id, error)
            return Err(error)

        outcome = unwrap(result)
        usage = outcome.usage or UsageDelta()

        # ④ 记账
        gate.commit(usage)
        recorded = self.service.record_usage(ctx, task_id, usage, actor="orchestrator")
        if is_err(recorded):
            return recorded

        # ⑤ 落检查点（幂等清单是本阶段"不重复干"的第一道保险，D-091）
        mark_unit_done(cp, unit.name, artifact_ids=outcome.artifact_ids)
        cp.stage = stage_name
        cp.cursor = unit.name
        cp.budget_used = record.budget_used
        saved = self.service.save_checkpoint(ctx, task_id, cp)
        if is_err(saved):
            return saved

        # 阶段推进：该阶段的单元全做完了才记 completed_stages（TS-V1）
        stage_units = [u.name for u in units if (u.stage or record.current_stage) == stage_name]
        completed_stage = stage_name if all(
            is_unit_done(cp, name) for name in stage_units
        ) else None
        next_stage = self._next_stage(record, stage_name, completed_stage is not None)
        progressed = self.service.record_progress(
            ctx,
            task_id,
            items_done=len(cp.completed_units),
            items_total=len(units),
            current_stage=next_stage or stage_name,
            completed_stage=completed_stage,
        )
        if is_err(progressed):
            return progressed

        step = Ok(StepOutcome(stage=stage_name, advanced=True))
        step.warnings.extend(warnings)
        return step

    # ---------------------------------------------------------------- 跑完

    def run_to_completion(
        self, ctx: CallContext, task_id: str, *, max_steps: int = 200
    ) -> Result[TaskResult]:
        """反复 `run_step` 直到：完成 / 暂停（预算）/ 失败。

        重试语义（ERROR_HANDLING §4）：可重试错误且未到 `retry_policy.max_attempts`
        就再试同一个单元；否则置 `FAILED` 并返回 `Err`。
        """
        for _ in range(max_steps):
            step = self.run_step(ctx, task_id)
            if is_err(step):
                error = error_of(step)
                task = self.service.get(ctx, task_id)
                attempts = unwrap(task).attempt_count if is_ok(task) else 0
                max_attempts = unwrap(task).retry_policy.max_attempts if is_ok(task) else 1
                if error is not None and error.retryable and attempts < max_attempts:
                    continue
                failed = self.service.fail(ctx, task_id, error)
                if is_err(failed):
                    return failed
                return Err(error) if error is not None else step

            outcome = unwrap(step)
            if outcome.paused or not outcome.advanced:
                break

        task = self.service.get(ctx, task_id)
        if is_err(task):
            return task
        record = unwrap(task)
        checkpoint = self.service.load_checkpoint(ctx, task_id)
        artifacts: dict[str, Any] = {}
        if is_ok(checkpoint) and checkpoint.value is not None:
            artifacts = dict(checkpoint.value.artifact_ids)
        return Ok(
            TaskResult(
                task_id=task_id,
                status=record.status,
                budget_used=record.budget_used,
                artifact_ids=artifacts,
            )
        )

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _next_stage(record: TaskRecord, stage_name: str, stage_just_done: bool) -> str | None:
        """算下一个当前阶段：只在**刚做完一个完整阶段**时才前进。"""
        if not stage_just_done:
            return None
        sequence = record.stage_sequence
        if stage_name not in sequence:
            return None
        index = sequence.index(stage_name)
        if index + 1 < len(sequence):
            return sequence[index + 1]
        return stage_name  # 已是最后一个阶段
