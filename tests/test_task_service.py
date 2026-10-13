"""任务生命周期测试（P2.3）。

覆盖：
  - 创建 / 查询 / 持久化 / 重启后读回
  - 合法与非法状态转换（六态）
  - 暂停 ≠ 失败 ≠ 取消
  - 检查点在"进程重启"后仍可读（含**真实子进程**验证）
  - 幂等：重复提交不建第二个任务

对应文档：INTERFACES §12.2/§12.3、ERROR_HANDLING §6、DATA_MODEL §4.12、DECISIONS §2.14。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.enums import PausedReason, TaskStatus, TaskType
from persona.core.errors import ErrorCode
from persona.core.result import UsageDelta, error_of, is_err, is_ok, unwrap
from persona.core.types import Query
from persona.orchestrator import (
    PROCESS_INTERRUPTED,
    BudgetSpec,
    TaskDraft,
    TaskService,
    new_checkpoint,
)
from persona.orchestrator.checkpoint import mark_unit_done
from persona.storage.sqlite import SqliteStorage

ROOT = Path(__file__).resolve().parent.parent


class TaskFixture:
    """一个临时数据库 + 任务服务 + 调用上下文。用完即关。"""

    def __init__(self, tmp: str, project_id: str = "prj_test") -> None:
        self.storage = SqliteStorage(Path(tmp) / "tasks.db")
        self.settings = test_settings(data_dir=str(Path(tmp) / "var"))
        self.service = TaskService(self.storage, self.settings)
        self.ctx = new_call_context(project_id=project_id, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))

    def new_task(self, **kwargs) -> str:
        draft = TaskDraft(
            task_type=kwargs.pop("task_type", TaskType.RESEARCH),
            stage_sequence=kwargs.pop("stage_sequence", ["RESEARCH", "EVIDENCE"]),
            **kwargs,
        )
        return unwrap(self.service.create_task(self.ctx, draft)).id


def _fixture() -> TaskFixture:
    tmp = tempfile.mkdtemp(prefix="gps-task-")
    return TaskFixture(tmp)


class TestCreateAndQuery(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()

    def tearDown(self) -> None:
        self.fx.storage.close()

    def test_create_is_pending_with_first_stage(self):
        task_id = self.fx.new_task()
        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(task.current_stage, "RESEARCH")
        self.assertEqual(task.completed_stages, [])
        self.assertIsNone(task.paused_reason)
        self.assertEqual(task.progress.items_total, 2)
        self.assertTrue(task.id.startswith("tsk_"))

    def test_unknown_task_type_rejected(self):
        result = self.fx.service.create_task(
            self.fx.ctx, TaskDraft(task_type="MAKE_COFFEE", stage_sequence=["A"])
        )
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_UNKNOWN_TASK_TYPE)

    def test_empty_stage_sequence_rejected(self):
        result = self.fx.service.create_task(
            self.fx.ctx, TaskDraft(task_type=TaskType.RESEARCH, stage_sequence=[])
        )
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_STAGE_SEQUENCE_EMPTY)

    def test_idempotency_key_prevents_duplicate(self):
        """TS-V4：重复提交返回**原任务**，不新建第二条。"""
        first = self.fx.new_task(idempotency_key="idem-1")
        again = self.fx.new_task(idempotency_key="idem-1")
        self.assertEqual(first, again)
        tasks = unwrap(self.fx.service.list_tasks(self.fx.ctx))
        self.assertEqual(len(tasks.items), 1)

    def test_unknown_task_not_found(self):
        result = self.fx.service.get(self.fx.ctx, "tsk_" + "0" * 32)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.NOT_FOUND_TASK)


class TestStateTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()
        self.task_id = self.fx.new_task()

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _status(self) -> TaskStatus:
        return unwrap(self.fx.service.get(self.fx.ctx, self.task_id)).status

    def test_start_then_illegal_start(self):
        self.assertTrue(is_ok(self.fx.service.start(self.fx.ctx, self.task_id)))
        self.assertEqual(self._status(), TaskStatus.RUNNING)
        again = self.fx.service.start(self.fx.ctx, self.task_id)
        self.assertTrue(is_err(again))
        self.assertEqual(self._status(), TaskStatus.RUNNING)  # 原状态不变

    def test_pause_from_pending_rejected(self):
        result = self.fx.service.pause(self.fx.ctx, self.task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STATE_CONFLICT_NOT_RUNNING)
        self.assertEqual(self._status(), TaskStatus.PENDING)

    def test_pause_requires_reason(self):
        self.fx.service.start(self.fx.ctx, self.task_id)
        result = self.fx.service._move(self.fx.ctx, self.task_id, TaskStatus.PAUSED)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_REQUIRED_FIELD)
        self.assertEqual(self._status(), TaskStatus.RUNNING)

    def test_resume_without_checkpoint_rejected(self):
        """暂停了但没写检查点 → 不许"假装记得做到哪"。"""
        self.fx.service.start(self.fx.ctx, self.task_id)
        self.fx.service.pause(self.fx.ctx, self.task_id, PausedReason.USER)
        result = self.fx.service.resume(self.fx.ctx, self.task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_CHECKPOINT_MISSING)
        self.assertEqual(self._status(), TaskStatus.PAUSED)

    def test_resume_with_checkpoint(self):
        self.fx.service.start(self.fx.ctx, self.task_id)
        cp = new_checkpoint(self.task_id, stage="RESEARCH")
        mark_unit_done(cp, "RESEARCH:0")
        self.fx.service.save_checkpoint(self.fx.ctx, self.task_id, cp)
        self.fx.service.pause(self.fx.ctx, self.task_id, PausedReason.USER)

        self.assertTrue(is_ok(self.fx.service.resume(self.fx.ctx, self.task_id)))
        task = unwrap(self.fx.service.get(self.fx.ctx, self.task_id))
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertIsNone(task.paused_reason)

    def test_resume_with_missing_artifacts_rejected(self):
        """ERROR_HANDLING §6.5 第 3 步：产出丢了就报错，不许"假装没做过"。"""
        self.fx.service.start(self.fx.ctx, self.task_id)
        cp = new_checkpoint(self.task_id, stage="RESEARCH")
        mark_unit_done(cp, "RESEARCH:0")
        cp.artifact_ids = {"source_ids": ["src_" + "0" * 32]}
        self.fx.service.save_checkpoint(self.fx.ctx, self.task_id, cp)
        self.fx.service.pause(self.fx.ctx, self.task_id, PausedReason.DEPENDENCY)
        result = self.fx.service.resume(self.fx.ctx, self.task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_ARTIFACT_MISSING)
        self.assertEqual(self._status(), TaskStatus.PAUSED)

    def test_resume_from_completed_rejected(self):
        self.fx.service.start(self.fx.ctx, self.task_id)
        self.fx.service.complete(self.fx.ctx, self.task_id)
        result = self.fx.service.resume(self.fx.ctx, self.task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STATE_CONFLICT_TERMINAL_STATE)
        self.assertEqual(self._status(), TaskStatus.COMPLETED)

    def test_paused_is_not_cancelled(self):
        """暂停 ≠ 取消（D-088）：暂停可续跑，取消不可。"""
        self.fx.service.start(self.fx.ctx, self.task_id)
        cp = new_checkpoint(self.task_id, stage="RESEARCH")
        self.fx.service.save_checkpoint(self.fx.ctx, self.task_id, cp)
        self.fx.service.pause(self.fx.ctx, self.task_id, PausedReason.USER)
        self.assertTrue(is_ok(self.fx.service.resume(self.fx.ctx, self.task_id)))

        other = self.fx.new_task(idempotency_key="cancel-me")
        self.fx.service.start(self.fx.ctx, other)
        self.fx.service.save_checkpoint(self.fx.ctx, other, new_checkpoint(other))
        self.assertTrue(is_ok(self.fx.service.cancel(self.fx.ctx, other, "用户不要了")))
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, other)).status,
                         TaskStatus.CANCELLED)
        self.assertTrue(is_err(self.fx.service.resume(self.fx.ctx, other)))

    def test_failed_keeps_error_trace(self):
        """TS-V5：失败必须留痕，不得被静默清空。"""
        self.fx.service.start(self.fx.ctx, self.task_id)
        self.assertTrue(is_ok(self.fx.service.fail(self.fx.ctx, self.task_id, "boom")))
        task = unwrap(self.fx.service.get(self.fx.ctx, self.task_id))
        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertIsNotNone(task.last_error)
        self.assertIn("boom", task.last_error.message)
        # 失败后不得再启动
        self.assertTrue(is_err(self.fx.service.start(self.fx.ctx, self.task_id)))

    def test_failed_differs_from_paused(self):
        self.fx.service.start(self.fx.ctx, self.task_id)
        self.fx.service.save_checkpoint(self.fx.ctx, self.task_id,
                                        new_checkpoint(self.task_id))
        self.fx.service.fail(self.fx.ctx, self.task_id, "boom")
        result = self.fx.service.resume(self.fx.ctx, self.task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STATE_CONFLICT_TERMINAL_STATE)

    def test_terminal_task_cannot_change_budget(self):
        self.fx.service.start(self.fx.ctx, self.task_id)
        self.fx.service.complete(self.fx.ctx, self.task_id)
        result = self.fx.service.set_budget(self.fx.ctx, self.task_id, BudgetSpec(max_requests=9))
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STATE_CONFLICT_TERMINAL_STATE)


class TestPersistenceAcrossRestart(unittest.TestCase):
    """模拟"进程重启"：关掉连接，用**新的**存储实例（甚至新进程）读回。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-restart-")

    def test_state_survives_new_storage_instance(self):
        fx = TaskFixture(self.tmp)
        task_id = fx.new_task()
        fx.service.start(fx.ctx, task_id)
        cp = new_checkpoint(task_id, stage="RESEARCH", cursor="RESEARCH:0")
        mark_unit_done(cp, "RESEARCH:0")
        fx.service.save_checkpoint(fx.ctx, task_id, cp)
        fx.service.pause(fx.ctx, task_id, PausedReason.BUDGET)
        fx.storage.close()

        # ——"重启"：全新服务实例，指向同一个数据库文件 ——
        reboot = TaskFixture(self.tmp)
        task = unwrap(reboot.service.get(reboot.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.paused_reason, PausedReason.BUDGET)
        self.assertEqual(task.checkpoint.cursor, "RESEARCH:0")
        self.assertTrue(task.checkpoint.resume_token)

        checkpoint = unwrap(reboot.service.load_checkpoint(reboot.ctx, task_id))
        self.assertIsNotNone(checkpoint)
        self.assertIn("RESEARCH:0", checkpoint.completed_units)
        reboot.storage.close()

    def test_checkpoint_readable_from_another_process(self):
        """更强的验证：真的用**另一个 Python 进程**读同一个数据库。"""
        fx = TaskFixture(self.tmp)
        task_id = fx.new_task()
        fx.service.start(fx.ctx, task_id)
        cp = new_checkpoint(task_id, stage="EVIDENCE", cursor="EVIDENCE:2")
        mark_unit_done(cp, "EVIDENCE:0")
        mark_unit_done(cp, "EVIDENCE:1")
        fx.service.save_checkpoint(fx.ctx, task_id, cp)
        fx.service.record_usage(fx.ctx, task_id, UsageDelta(requests=3, tokens_in=120))
        fx.storage.close()

        script = (
            "import json, sys\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from persona.core.context import new_call_context\n"
            "from persona.orchestrator import TaskService\n"
            "from persona.storage.sqlite import SqliteStorage\n"
            "from persona.config import test_settings\n"
            f"st = SqliteStorage(r'{Path(self.tmp) / 'tasks.db'}')\n"
            f"sv = TaskService(st, test_settings(data_dir=r'{self.tmp}'))\n"
            "ctx = new_call_context(project_id='prj_test', actor='subprocess')\n"
            f"cp = st.load_checkpoint(ctx, '{task_id}').value\n"
            f"t = sv.get(ctx, '{task_id}').value\n"
            "st.close()\n"
            "print(json.dumps({'status': t.status.value, 'units': cp.completed_units,\n"
            "                  'cursor': cp.cursor, 'token': bool(cp.resume_token)}))\n"
        )

        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(proc.returncode, 0, msg=f"子进程失败：{proc.stderr}")
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["status"], "RUNNING")
        self.assertEqual(payload["units"], ["EVIDENCE:0", "EVIDENCE:1"])
        self.assertEqual(payload["cursor"], "EVIDENCE:2")

    def test_sweep_interrupted_marks_running_as_paused(self):
        """ERROR_HANDLING §6.6：崩溃后 RUNNING → PAUSED(DEPENDENCY)。"""
        fx = TaskFixture(self.tmp)
        task_id = fx.new_task()
        fx.service.start(fx.ctx, task_id)
        fx.storage.close()

        reboot = TaskFixture(self.tmp)
        fixed = unwrap(reboot.service.sweep_interrupted(reboot.ctx))
        self.assertEqual(fixed, [task_id])
        task = unwrap(reboot.service.get(reboot.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.paused_reason, PausedReason.DEPENDENCY)
        self.assertEqual(task.last_error.code, PROCESS_INTERRUPTED)
        reboot.storage.close()


if __name__ == "__main__":
    unittest.main()
