"""任务六态状态机与检查点测试。

对应文档：INTERFACES §12.2、ERROR_HANDLING §6.2/§6.3/§6.4/§6.5、DECISIONS D-052/D-061。
"""

import unittest

from persona.core.enums import PausedReason, TaskStatus
from persona.core.errors import ErrorCode
from persona.core.result import is_err, is_ok
from persona.core.types import BudgetUsage
from persona.orchestrator.checkpoint import (
    idempotency_key,
    is_unit_done,
    mark_unit_done,
    new_checkpoint,
)
from persona.orchestrator.state import (
    LEGAL_TRANSITIONS,
    TaskState,
    can_transition,
    is_resumable,
    is_terminal,
)


class TestLegalTransitions(unittest.TestCase):
    def test_six_states_only(self):
        self.assertEqual(len(TaskStatus), 6)
        self.assertEqual(set(LEGAL_TRANSITIONS), set(TaskStatus))

    def test_legal_paths(self):
        self.assertTrue(can_transition(TaskStatus.PENDING, TaskStatus.RUNNING))
        self.assertTrue(can_transition(TaskStatus.RUNNING, TaskStatus.PAUSED))
        self.assertTrue(can_transition(TaskStatus.PAUSED, TaskStatus.RUNNING))   # resume
        self.assertTrue(can_transition(TaskStatus.RUNNING, TaskStatus.COMPLETED))
        self.assertTrue(can_transition(TaskStatus.RUNNING, TaskStatus.FAILED))
        self.assertTrue(can_transition(TaskStatus.PENDING, TaskStatus.CANCELLED))
        self.assertTrue(can_transition(TaskStatus.RUNNING, TaskStatus.CANCELLED))
        self.assertTrue(can_transition(TaskStatus.PAUSED, TaskStatus.CANCELLED))

    def test_illegal_paths(self):
        self.assertFalse(can_transition(TaskStatus.COMPLETED, TaskStatus.RUNNING))
        self.assertFalse(can_transition(TaskStatus.FAILED, TaskStatus.RUNNING))
        self.assertFalse(can_transition(TaskStatus.CANCELLED, TaskStatus.RUNNING))
        self.assertFalse(can_transition(TaskStatus.RUNNING, TaskStatus.RUNNING))
        self.assertFalse(can_transition(TaskStatus.PENDING, TaskStatus.PAUSED))

    def test_terminal_and_resumable(self):
        for status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
            with self.subTest(status=status):
                self.assertTrue(is_terminal(status))
                self.assertFalse(is_resumable(status))
        self.assertTrue(is_resumable(TaskStatus.PAUSED))
        self.assertFalse(is_terminal(TaskStatus.PAUSED))


class TestTaskStateMachine(unittest.TestCase):
    def test_full_happy_path(self):
        task = TaskState(task_id="tsk_1")
        self.assertTrue(is_ok(task.start()))
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertTrue(is_ok(task.complete()))
        self.assertEqual(task.status, TaskStatus.COMPLETED)

    def test_pause_and_resume(self):
        task = TaskState(task_id="tsk_1")
        task.start()
        self.assertTrue(is_ok(task.pause(PausedReason.BUDGET)))
        self.assertEqual(task.paused_reason, PausedReason.BUDGET)
        self.assertTrue(is_ok(task.resume()))
        self.assertIsNone(task.paused_reason)  # 恢复后清空暂停原因

    def test_pause_requires_reason(self):
        task = TaskState(task_id="tsk_1")
        task.start()
        result = task.transition(TaskStatus.PAUSED)
        self.assertTrue(is_err(result))
        self.assertEqual(task.status, TaskStatus.RUNNING)  # 状态不得被改坏

    def test_illegal_transition_does_not_change_status(self):
        task = TaskState(task_id="tsk_1")
        task.start()
        task.complete()
        result = task.start()
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STATE_CONFLICT_TERMINAL_STATE)
        self.assertEqual(task.status, TaskStatus.COMPLETED)  # 原状态保持

    def test_resume_from_completed_is_rejected(self):
        task = TaskState(task_id="tsk_1")
        task.start()
        task.complete()
        result = task.resume()
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STATE_CONFLICT_TERMINAL_STATE)

    def test_pause_from_pending_rejected(self):
        task = TaskState(task_id="tsk_1")
        result = task.pause()
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STATE_CONFLICT_NOT_RUNNING)
        self.assertEqual(task.status, TaskStatus.PENDING)

    def test_paused_differs_from_cancelled(self):
        """暂停 ≠ 取消（ERROR_HANDLING §6.3）。"""
        paused = TaskState(task_id="tsk_1")
        paused.start()
        paused.pause(PausedReason.USER)
        self.assertTrue(is_resumable(paused.status))

        cancelled = TaskState(task_id="tsk_2")
        cancelled.start()
        cancelled.cancel()
        self.assertFalse(is_resumable(cancelled.status))
        self.assertTrue(is_terminal(cancelled.status))


class TestCheckpoint(unittest.TestCase):
    def test_new_checkpoint_has_resume_token(self):
        cp = new_checkpoint("tsk_1", stage="RESEARCH")
        self.assertTrue(cp.resume_token)
        self.assertTrue(cp.saved_at)

    def test_completed_units_are_idempotent(self):
        cp = new_checkpoint("tsk_1", stage="RESEARCH")
        mark_unit_done(cp, "query:0")
        mark_unit_done(cp, "query:0")  # 重复标记不应产生重复项
        self.assertEqual(cp.completed_units.count("query:0"), 1)
        self.assertTrue(is_unit_done(cp, "query:0"))
        self.assertFalse(is_unit_done(cp, "query:1"))

    def test_idempotency_key_format(self):
        self.assertEqual(idempotency_key("tsk_1", "query:0"), "tsk_1:query:0")

    def test_budget_usage_accumulates_unknown_as_zero(self):
        usage = BudgetUsage()
        usage.add(requests=2, tokens_in=None, tokens_out=5, latency_ms=10)
        self.assertEqual(usage.requests, 2)
        self.assertEqual(usage.tokens_in, 0)  # 未知不加，绝不编造
        self.assertEqual(usage.tokens_out, 5)


if __name__ == "__main__":
    unittest.main()
