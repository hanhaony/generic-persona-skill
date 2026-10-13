"""预算闸门与最小执行器测试（P2.3）。

覆盖：
  - 四段式闸门：检查 → 预占 → 执行 → 记账（未知 token 不估算）
  - 预算不足 ⇒ **暂停**（不是失败），且**先落检查点**
  - 提高额度后 resume 续跑，**不重复执行**已完成的单元
  - 单元级幂等：检查点里已有的单元会被跳过
  - 可重试 / 不可重试错误的处理与重试上限

对应文档：ERROR_HANDLING §4/§5、INTERFACES §12.4/§12.5、DECISIONS D-089/D-091/D-093。
"""

import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import PausedReason, TaskStatus
from persona.core.errors import ErrorCode
from persona.core.result import UsageDelta, error_of, is_err, is_ok, unwrap
from persona.orchestrator import (
    BudgetSpec,
    MockUnitHandler,
    TaskDraft,
    TaskRunner,
    TaskService,
    WorkUnit,
    new_checkpoint,
)
from persona.orchestrator.budget import (
    WARNING_BUDGET_NEAR_LIMIT,
    BudgetGate,
    gate_for,
)
from persona.orchestrator.checkpoint import mark_unit_done
from persona.orchestrator.tasks import TaskRecord
from persona.storage.sqlite import SqliteStorage
from tests.test_task_service import TaskFixture


def _units(count: int, stage: str = "RESEARCH", requests: int = 1) -> list[WorkUnit]:
    return [
        WorkUnit(name=f"{stage}:{i}", stage=stage, estimate=UsageDelta(requests=requests))
        for i in range(count)
    ]


class TestBudgetGate(unittest.TestCase):
    def test_check_happens_before_spending(self):
        gate = gate_for(BudgetSpec(max_requests=2))
        self.assertTrue(is_ok(gate.check(UsageDelta(requests=1))))
        gate.reserve(UsageDelta(requests=1))
        gate.commit(UsageDelta(requests=1))
        self.assertTrue(is_ok(gate.check(UsageDelta(requests=1))))
        gate.reserve(UsageDelta(requests=1))
        gate.commit(UsageDelta(requests=1))
        result = gate.check(UsageDelta(requests=1))
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.BUDGET_EXCEEDED)
        # 关键：超预算**没有**消耗任何东西
        self.assertEqual(gate.used.requests, 2)

    def test_unknown_tokens_are_not_estimated(self):
        """token 未知时不估算，只按调用次数计（ERROR_HANDLING §5.2）。"""
        gate = gate_for(BudgetSpec(max_requests=1, max_tokens_in=100))
        delta = UsageDelta(requests=1, tokens_in=None)
        self.assertTrue(is_ok(gate.check(delta)))
        gate.reserve(delta)
        gate.commit(delta)
        self.assertEqual(gate.used.tokens_in, 0)
        self.assertEqual(gate.reserved.requests, 0)  # 记账后预占必须释放

    def test_near_limit_only_warns(self):
        gate = gate_for(BudgetSpec(max_requests=10))
        gate.reserve(UsageDelta(requests=8))
        gate.commit(UsageDelta(requests=8))
        result = gate.check(UsageDelta(requests=1))
        self.assertTrue(is_ok(result))
        codes = [note.code for note in result.warnings]
        self.assertIn(WARNING_BUDGET_NEAR_LIMIT, codes)

    def test_reserve_reduces_remaining_and_release_restores(self):
        gate = gate_for(BudgetSpec(max_requests=10))
        self.assertEqual(gate.remaining_requests(), 10)
        gate.reserve(UsageDelta(requests=3))
        self.assertEqual(gate.remaining_requests(), 7)
        self.assertTrue(is_err(gate.check(UsageDelta(requests=8))))
        gate.release(UsageDelta(requests=3))
        self.assertEqual(gate.remaining_requests(), 10)
        self.assertTrue(is_ok(gate.check(UsageDelta(requests=8))))

    def test_no_limit_never_exceeds(self):
        gate = gate_for(BudgetSpec())
        for _ in range(5):
            self.assertTrue(is_ok(gate.check(UsageDelta(requests=1000))))
            gate.commit(UsageDelta(requests=1000))

    def test_gate_is_pure_query(self):
        """`would_exceed` 不得改状态。"""
        gate = gate_for(BudgetSpec(max_requests=1))
        self.assertFalse(gate.would_exceed(UsageDelta(requests=1)))
        self.assertTrue(gate.would_exceed(UsageDelta(requests=2)))
        self.assertEqual(gate.used.requests, 0)
        self.assertEqual(gate.reserved.requests, 0)


class RunnerFixture(TaskFixture):
    """在 TaskFixture 基础上加一个执行器。"""

    def runner(self, handler: MockUnitHandler) -> TaskRunner:
        return TaskRunner(self.service, handler)

    def new_task(self, **kwargs) -> str:  # type: ignore[override]
        kwargs.setdefault("stage_sequence", ["RESEARCH"])
        return super().new_task(**kwargs)


class TestTaskRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = RunnerFixture(tempfile.mkdtemp(prefix="gps-run-"))

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _start(self, budget: BudgetSpec, **kwargs) -> tuple[str, MockUnitHandler]:
        handler = MockUnitHandler(**kwargs)
        task_id = self.fx.new_task(budget_limit=budget)
        unwrap(self.fx.service.start(self.fx.ctx, task_id))
        return task_id, handler

    def test_run_step_advances_one_unit_and_accounts_usage(self):
        task_id, handler = self._start(BudgetSpec(max_requests=10), units=_units(3))
        runner = self.fx.runner(handler)
        step = unwrap(runner.run_step(self.fx.ctx, task_id))
        self.assertTrue(step.advanced)
        self.assertEqual(handler.call_names(), ["RESEARCH:0"])

        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.budget_used.requests, 1)
        report = unwrap(self.fx.storage.usage_report(self.fx.ctx, task_id))
        self.assertEqual(report.requests, 1)

        cp = unwrap(self.fx.service.load_checkpoint(self.fx.ctx, task_id))
        self.assertEqual(cp.completed_units, ["RESEARCH:0"])

    def test_run_step_requires_running(self):
        """没 start 就推进 → 拒绝（INTERFACES §12.3：`STATE_CONFLICT.not_running`）。"""
        handler = MockUnitHandler(units=_units(2))
        task_id = self.fx.new_task(budget_limit=BudgetSpec(max_requests=10))
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.PENDING)
        result = self.fx.runner(handler).run_step(self.fx.ctx, task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STATE_CONFLICT_NOT_RUNNING)
        self.assertEqual(handler.calls, [])  # 一步也没干

    def test_budget_exhaustion_pauses_not_fails(self):
        """D-043 / D-089：预算不够 ⇒ 暂停 + 保存进度，返回的是 Ok 不是 Err。"""
        task_id, handler = self._start(BudgetSpec(max_requests=2), units=_units(5))
        runner = self.fx.runner(handler)
        result = runner.run_to_completion(self.fx.ctx, task_id)
        self.assertTrue(is_ok(result), "超预算不是失败，必须成功返回")

        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.paused_reason, PausedReason.BUDGET)
        self.assertEqual(handler.call_names(), ["RESEARCH:0", "RESEARCH:1"])
        self.assertEqual(task.budget_used.requests, 2)

        # 检查点必须已经落盘（进度不丢）
        cp = unwrap(self.fx.service.load_checkpoint(self.fx.ctx, task_id))
        self.assertEqual(cp.completed_units, ["RESEARCH:0", "RESEARCH:1"])
        self.assertEqual(cp.cursor, "RESEARCH:2")

    def test_resume_after_raising_budget_does_not_repeat_work(self):
        """提高额度后继续：只做剩下的，**绝不重做**已完成的单元（D-091）。"""
        task_id, handler = self._start(BudgetSpec(max_requests=2), units=_units(5))
        runner = self.fx.runner(handler)
        runner.run_to_completion(self.fx.ctx, task_id)

        self.assertTrue(is_ok(self.fx.service.set_budget(
            self.fx.ctx, task_id, BudgetSpec(max_requests=10))))
        self.assertTrue(is_ok(self.fx.service.resume(self.fx.ctx, task_id)))

        result = unwrap(runner.run_to_completion(self.fx.ctx, task_id))
        self.assertEqual(result.status, TaskStatus.COMPLETED)
        # 总共只调了 5 次，每个单元恰好一次
        self.assertEqual(len(handler.calls), 5)
        for index in range(5):
            self.assertEqual(handler.call_count(f"RESEARCH:{index}"), 1)
        self.assertEqual(result.budget_used.requests, 5)

    def test_units_already_in_checkpoint_are_skipped(self):
        """幂等第二道保险演示：检查点里有的单元直接跳过，handler 不会被调用。"""
        task_id, handler = self._start(BudgetSpec(max_requests=10), units=_units(3))
        cp = new_checkpoint(task_id, stage="RESEARCH")
        mark_unit_done(cp, "RESEARCH:0")
        unwrap(self.fx.service.save_checkpoint(self.fx.ctx, task_id, cp))

        runner = self.fx.runner(handler)
        unwrap(runner.run_step(self.fx.ctx, task_id))
        self.assertEqual(handler.call_names(), ["RESEARCH:1"])

    def test_idempotency_key_is_passed_to_handler(self):
        task_id, handler = self._start(BudgetSpec(max_requests=10), units=_units(2))
        runner = self.fx.runner(handler)
        unwrap(runner.run_step(self.fx.ctx, task_id))
        unit_name, key = handler.calls[0]
        self.assertEqual(key, f"{task_id}:{unit_name}")

    def test_retryable_error_retries_then_fails(self):
        """ERROR_HANDLING §4：可重试错误按上限重试，耗尽后置 FAILED。"""
        task_id, handler = self._start(
            BudgetSpec(max_requests=10),
            units=_units(3),
            fail_on=["RESEARCH:1"],
            error_code=ErrorCode.EXTERNAL_UNAVAILABLE,
        )
        runner = self.fx.runner(handler)
        result = runner.run_to_completion(self.fx.ctx, task_id)
        self.assertTrue(is_err(result))

        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.retry_policy.max_attempts, 4)   # max_retries(3) + 首次
        self.assertEqual(handler.call_count("RESEARCH:1"), 4)
        self.assertIsNotNone(task.last_error)

    def test_non_retryable_error_fails_immediately(self):
        task_id, handler = self._start(
            BudgetSpec(max_requests=10),
            units=_units(3),
            fail_on=["RESEARCH:1"],
            error_code=ErrorCode.VALIDATION_REQUIRED_FIELD,  # retryable=False
        )
        runner = self.fx.runner(handler)
        self.assertTrue(is_err(runner.run_to_completion(self.fx.ctx, task_id)))
        self.assertEqual(handler.call_count("RESEARCH:1"), 1)
        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.FAILED)

    def test_no_real_network_is_used(self):
        """执行器只调 Mock handler，不涉及 Provider / 网络（D-093）。"""
        task_id, handler = self._start(BudgetSpec(max_requests=10), units=_units(2))
        runner = self.fx.runner(handler)
        unwrap(runner.run_to_completion(self.fx.ctx, task_id))
        self.assertIsInstance(handler, MockUnitHandler)
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
