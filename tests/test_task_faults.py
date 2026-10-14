"""P2.4 故障注入测试 —— 只补 P2.3 **未覆盖**的故障场景。

**P2.3 已覆盖（本文件不重复测试，仅登记）**：
  - 状态机：六态合法/非法转换、非法不改状态、暂停≠取消≠失败、FAILED 留痕
    （`test_task_state.py` / `test_task_service.py`）
  - 预算：四段式闸门、未知 token 不估算、80% 只告警、超预算⇒暂停+检查点
    （`test_task_budget.py`）
  - 幂等：同 `idempotency_key` 重复提交返回原任务、检查点已有单元被跳过
    （`test_task_service.py` / `test_task_budget.py`）
  - 断点恢复：新存储实例与**真实子进程**读回、`sweep_interrupted` 崩溃回退
    （`test_task_service.py`）

**本文件新增（故障注入，全部离线、全部 Mock、零新增依赖）**：
  1. 检查点保存失败（`TestCheckpointSaveFailure`）
  2. 数据库操作失败（`TestDatabaseFailures`）
  3. 处理器抛异常（`TestHandlerRaises`）
  4. 重复提交（`TestDuplicateSubmission`）
  5. 重复恢复（`TestDuplicateRecovery`）
  6. 跨进程重启验证（`TestRestartAcrossProcess`）

**故障注入手法**：`FlakyConnection` 代理真实 SQLite 连接，对命中特定 SQL
（如 `INSERT INTO checkpoints`）的语句抛 `sqlite3.OperationalError`，
其余语句原样放行 —— 模拟"磁盘 I/O 错误"级别的真实数据库故障，
不改任何产品代码、不引入 mock 之外的依赖。

**诚实边界**（测试断言的就是"现状"，不美化）：
  - B-1 单元执行与检查点保存**非原子**：执行成功且用量已记账后检查点保存失败，
    重启后该单元会被**重复执行**（`test_run_step_checkpoint_failure_*` 与
    `TestRestartAcrossProcess` 两级验证）。
  - B-2 预算暂停路径**忽略** `save_checkpoint` 的返回值：暂停成功但检查点缺失时，
    `resume` 会被明确拒绝（fail-loud，不假装记得进度），但进度已丢。
  - B-3 处理器抛异常（不守 `Result` 契约）时异常**直接穿透** `run_step`：
    任务停留 `RUNNING`、无 `last_error`、无 `attempt_count`，只能靠 `sweep` 兜底。
  对应最小修复建议记录在 `docs/RUNNING.md` §5.3。

对应文档：ERROR_HANDLING §4/§5/§6、INTERFACES §12、DECISIONS §2.14/§2.15、RUNNING §5.3。
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
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
    WorkUnit,
    new_checkpoint,
)
from persona.orchestrator.checkpoint import mark_unit_done
from persona.storage.sqlite import SqliteStorage
from tests.test_task_service import TaskFixture

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 故障注入工具


class FlakyConnection:
    """SQLite 连接代理：命中 `match` 的 SQL 前 `skip` 次放行，
    之后 `fail_times` 次抛 `exc`，再之后恢复放行。

    为什么用代理而不是改产品代码：故障注入必须**只存在于测试中**。
    `sqlite3.Connection.execute` 是只读属性不能直接赋值，故用代理对象。
    """

    def __init__(self, real, match: str, exc: Exception, *, skip: int = 0,
                 fail_times: int = 1) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_match", match)
        object.__setattr__(self, "_exc", exc)
        object.__setattr__(self, "_seen", 0)
        object.__setattr__(self, "_skip", skip)
        object.__setattr__(self, "_fail_times", fail_times)
        object.__setattr__(self, "_failed", 0)

    def execute(self, sql, *args, **kwargs):
        if self._match in sql:
            object.__setattr__(self, "_seen", self._seen + 1)
            if self._seen > self._skip and self._failed < self._fail_times:
                object.__setattr__(self, "_failed", self._failed + 1)
                raise self._exc
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


@contextmanager
def db_fault(storage: SqliteStorage, match: str, exc: Exception, *, skip: int = 0,
             fail_times: int = 1):
    """在 `with` 块内给存储注入数据库故障，退出后自动恢复。"""
    real = storage.connect()
    storage._conn = FlakyConnection(real, match, exc, skip=skip, fail_times=fail_times)
    try:
        yield storage
    finally:
        storage._conn = real


class RaisingHandler:
    """故障注入：**不守契约**的处理器。

    `execute` / `plan` 直接抛 Python 异常，而不是返回 `Result`。
    用来验证"上游不守规矩时，任务引擎会不会被带坏"。
    """

    def __init__(self, units, exc: Exception | None = None, *,
                 raise_in_plan: bool = False) -> None:
        self.units = list(units)
        self.exc = exc or RuntimeError("（注入）处理器内部爆炸，未返回 Result")
        self.raise_in_plan = raise_in_plan
        self.calls: list[str] = []

    def plan(self, task):
        if self.raise_in_plan:
            raise self.exc
        return list(self.units)

    def execute(self, ctx, unit, *, idempotency_key: str = ""):
        self.calls.append(unit.name)
        raise self.exc


def _units(count: int, stage: str = "RESEARCH") -> list[WorkUnit]:
    return [WorkUnit(name=f"{stage}:{i}", stage=stage, estimate=UsageDelta(requests=1))
            for i in range(count)]


DISK_ERROR = sqlite3.OperationalError("disk I/O error (injected by test)")


# ---------------------------------------------------------------- 1. 检查点保存失败


class TestCheckpointSaveFailure(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-fault-cp-")
        self.fx = TaskFixture(self.tmp)

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _running_task(self, budget: BudgetSpec) -> str:
        draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"],
                          budget_limit=budget)
        task_id = unwrap(self.fx.service.create_task(self.fx.ctx, draft)).id
        unwrap(self.fx.service.start(self.fx.ctx, task_id))
        return task_id

    def test_save_checkpoint_db_failure_returns_err_not_raise(self):
        """存储层契约（EH-1）：`sqlite3.Error` 必须转成 `Err`，不许穿透成异常。"""
        task_id = self._running_task(BudgetSpec(max_requests=10))
        with db_fault(self.fx.storage, "INSERT INTO checkpoints", DISK_ERROR):
            result = self.fx.service.save_checkpoint(
                self.fx.ctx, task_id, new_checkpoint(task_id, stage="RESEARCH")
            )
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STORAGE_WRITE_FAILED)
        # 故障解除后检查点确实没写进去
        self.assertIsNone(unwrap(self.fx.service.load_checkpoint(self.fx.ctx, task_id)))

    def test_run_step_checkpoint_failure_leaves_unit_unrecorded(self):
        """**边界 B-1**：执行成功 + 用量已记账，但检查点保存失败 ⇒
        `run_step` 返回 `Err`，任务仍是 `RUNNING`，检查点为空。

        随后"重启"（全新存储实例、同一数据库文件）再推一步：
        该单元被**重复执行**、用量**重复记账** —— 这是当前实现的真实边界，
        不是缺陷隐瞒：第二道保险（幂等键）只是传给 handler 的钥匙，
        去不减责在执行器本身。
        """
        task_id = self._running_task(BudgetSpec(max_requests=10))
        handler = MockUnitHandler(units=_units(3))
        runner = TaskRunner(self.fx.service, handler)

        with db_fault(self.fx.storage, "INSERT INTO checkpoints", DISK_ERROR):
            step = runner.run_step(self.fx.ctx, task_id)
        self.assertTrue(is_err(step))
        self.assertEqual(error_of(step).code, ErrorCode.STORAGE_WRITE_FAILED)

        # 执行发生过、用量记过账，但检查点没落
        self.assertEqual(handler.call_names(), ["RESEARCH:0"])
        report = unwrap(self.fx.storage.usage_report(self.fx.ctx, task_id))
        self.assertEqual(report.requests, 1)
        self.assertIsNone(unwrap(self.fx.service.load_checkpoint(self.fx.ctx, task_id)))
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.RUNNING)

        # —— "进程重启"：全新存储实例指向同一文件 ——
        reboot = TaskFixture(self.tmp)
        try:
            again = unwrap(TaskRunner(reboot.service, handler).run_step(reboot.ctx, task_id))
            self.assertTrue(again.advanced)
        finally:
            reboot.storage.close()

        # 诚实记录：单元重复执行了，用量也重复记了
        self.assertEqual(handler.call_count("RESEARCH:0"), 2)
        report2 = unwrap(self.fx.storage.usage_report(self.fx.ctx, task_id))
        self.assertEqual(report2.requests, 2)

    def test_budget_pause_with_checkpoint_failure_fails_loud_on_resume(self):
        """**边界 B-2**：预算不足路径里检查点也保存失败 ⇒ 任务照样被暂停
        （`save_checkpoint` 返回值被忽略），但检查点是空的；
        `resume` 被明确拒绝（`checkpoint_missing`）——进度丢了，但不假装记得。
        """
        task_id = self._running_task(BudgetSpec(max_requests=0))  # 第一步就触顶
        runner = TaskRunner(self.fx.service, MockUnitHandler(units=_units(3)))

        with db_fault(self.fx.storage, "INSERT INTO checkpoints", DISK_ERROR):
            step = unwrap(runner.run_step(self.fx.ctx, task_id))

        self.assertTrue(step.paused)
        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.paused_reason, PausedReason.BUDGET)
        self.assertIsNone(unwrap(self.fx.service.load_checkpoint(self.fx.ctx, task_id)))

        result = self.fx.service.resume(self.fx.ctx, task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_CHECKPOINT_MISSING)
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.PAUSED)  # 状态不被失败的 resume 改变


# ---------------------------------------------------------------- 2. 数据库操作失败


class TestDatabaseFailures(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = TaskFixture(tempfile.mkdtemp(prefix="gps-fault-db-"))

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _pending_task(self) -> str:
        draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH", "EVIDENCE"])
        return unwrap(self.fx.service.create_task(self.fx.ctx, draft)).id

    def test_transition_persist_failure_keeps_db_status(self):
        """状态转换的落盘失败 ⇒ `Err`，且**数据库里的状态保持原值**
        （读→转换→校验→落盘，落盘这步失败就什么都不改）。"""
        task_id = self._pending_task()
        with db_fault(self.fx.storage, "UPDATE records", DISK_ERROR):
            result = self.fx.service.start(self.fx.ctx, task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STORAGE_WRITE_FAILED)
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.PENDING)

    def test_create_task_put_failure_creates_nothing(self):
        """建任务的写入失败 ⇒ `Err`，库里**不会留下半个任务**。"""
        with db_fault(self.fx.storage, "INSERT INTO records", DISK_ERROR):
            result = self.fx.service.create_task(
                self.fx.ctx, TaskDraft(task_type="RESEARCH", stage_sequence=["A"])
            )
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STORAGE_WRITE_FAILED)
        self.assertEqual(len(unwrap(self.fx.service.list_tasks(self.fx.ctx)).items), 0)

    def test_read_failure_propagates_as_err_not_empty_ok(self):
        """读取失败必须传播为 `Err`，**不许**伪装成 `Ok(None)`
        （那会把"读不出来"误判成"不存在"）。"""
        task_id = self._pending_task()
        with db_fault(self.fx.storage, "SELECT * FROM records", DISK_ERROR):
            result = self.fx.service.get(self.fx.ctx, task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STORAGE_READ_FAILED)

    def test_resume_reference_check_failure_keeps_state(self):
        """`resume` 的产出完整性检查本身遇到数据库故障 ⇒ `Err` 传播，
        任务停在 `PAUSED`，不会被半推着进 `RUNNING`。"""
        task_id = self._pending_task()
        unwrap(self.fx.service.start(self.fx.ctx, task_id))
        cp = new_checkpoint(task_id, stage="RESEARCH")
        mark_unit_done(cp, "RESEARCH:0")
        cp.artifact_ids = {"source_ids": ["src_" + "0" * 32]}
        unwrap(self.fx.service.save_checkpoint(self.fx.ctx, task_id, cp))
        unwrap(self.fx.service.pause(self.fx.ctx, task_id, PausedReason.USER))

        with db_fault(self.fx.storage, "SELECT 1 FROM records", DISK_ERROR):
            result = self.fx.service.resume(self.fx.ctx, task_id)
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.STORAGE_READ_FAILED)
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.PAUSED)


# ---------------------------------------------------------------- 3. 处理器抛异常


class TestHandlerRaises(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = TaskFixture(tempfile.mkdtemp(prefix="gps-fault-exc-"))

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _running_task(self) -> str:
        draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"],
                          budget_limit=BudgetSpec(max_requests=10))
        task_id = unwrap(self.fx.service.create_task(self.fx.ctx, draft)).id
        unwrap(self.fx.service.start(self.fx.ctx, task_id))
        return task_id

    def test_handler_exception_propagates_but_task_is_recoverable(self):
        """**边界 B-3**：处理器抛异常 ⇒ 异常直接穿透 `run_step`（未转 `Err`）。
        任务停留在 `RUNNING`、无 `last_error`、`attempt_count` 不涨；
        但崩溃恢复机制仍能兜底：`sweep` 把它安全退回 `PAUSED(DEPENDENCY)`。
        """
        task_id = self._running_task()
        handler = RaisingHandler(_units(2))
        runner = TaskRunner(self.fx.service, handler)

        with self.assertRaises(RuntimeError):
            runner.run_step(self.fx.ctx, task_id)

        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.RUNNING)   # 停在半路
        self.assertIsNone(task.last_error)                  # 没有错误痕迹
        self.assertEqual(task.attempt_count, 0)             # 也没记尝试
        self.assertEqual(handler.calls, ["RESEARCH:0"])     # 确实炸在执行里

        # 兜底：崩溃扫描把任务救回可恢复状态
        fixed = unwrap(self.fx.service.sweep_interrupted(self.fx.ctx))
        self.assertEqual(fixed, [task_id])
        swept = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(swept.status, TaskStatus.PAUSED)
        self.assertEqual(swept.paused_reason, PausedReason.DEPENDENCY)
        self.assertEqual(swept.last_error.code, "PROCESS_INTERRUPTED")

    def test_handler_plan_exception_propagates_before_any_execution(self):
        """`plan` 就抛异常 ⇒ 异常穿透，且**没有任何单元被执行**。"""
        task_id = self._running_task()
        handler = RaisingHandler(_units(2), raise_in_plan=True)
        with self.assertRaises(RuntimeError):
            TaskRunner(self.fx.service, handler).run_step(self.fx.ctx, task_id)
        self.assertEqual(handler.calls, [])
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.RUNNING)


# ---------------------------------------------------------------- 4. 重复提交


class TestDuplicateSubmission(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = TaskFixture(tempfile.mkdtemp(prefix="gps-fault-dup-"))

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _draft(self, key: str) -> TaskDraft:
        return TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"],
                         idempotency_key=key)

    def test_duplicate_submit_after_write_failure_leaves_no_dirty_state(self):
        """第一次提交因写库失败 ⇒ `Err` 且**无脏数据**；
        故障解除后同一 `idempotency_key` 能正常建成**唯一**任务。"""
        with db_fault(self.fx.storage, "INSERT INTO records", DISK_ERROR):
            first = self.fx.service.create_task(self.fx.ctx, self._draft("idem-x"))
        self.assertTrue(is_err(first))
        self.assertEqual(len(unwrap(self.fx.service.list_tasks(self.fx.ctx)).items), 0)

        second = unwrap(self.fx.service.create_task(self.fx.ctx, self._draft("idem-x")))
        tasks = unwrap(self.fx.service.list_tasks(self.fx.ctx))
        self.assertEqual(len(tasks.items), 1)
        self.assertEqual(tasks.items[0].id, second.id)

    def test_duplicate_submit_returns_original_even_when_completed(self):
        """重复提交返回的是**原任务本身**——哪怕它已经 `COMPLETED`，
        也不会悄悄新建一条来"重做"。"""
        first = unwrap(self.fx.service.create_task(self.fx.ctx, self._draft("idem-y")))
        unwrap(self.fx.service.start(self.fx.ctx, first.id))
        unwrap(self.fx.service.complete(self.fx.ctx, first.id))

        again = unwrap(self.fx.service.create_task(self.fx.ctx, self._draft("idem-y")))
        self.assertEqual(again.id, first.id)
        self.assertEqual(again.status, TaskStatus.COMPLETED)
        self.assertEqual(len(unwrap(self.fx.service.list_tasks(self.fx.ctx)).items), 1)


# ---------------------------------------------------------------- 5. 重复恢复


class TestDuplicateRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = TaskFixture(tempfile.mkdtemp(prefix="gps-fault-rec-"))

    def tearDown(self) -> None:
        self.fx.storage.close()

    def test_double_resume_rejected_and_state_unchanged(self):
        """重复恢复：`resume` 成功后任务已是 `RUNNING`，再次 `resume`
        必须被拒绝，且状态不变。"""
        draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"])
        task_id = unwrap(self.fx.service.create_task(self.fx.ctx, draft)).id
        unwrap(self.fx.service.start(self.fx.ctx, task_id))
        cp = new_checkpoint(task_id, stage="RESEARCH")
        unwrap(self.fx.service.save_checkpoint(self.fx.ctx, task_id, cp))
        unwrap(self.fx.service.pause(self.fx.ctx, task_id, PausedReason.USER))

        self.assertTrue(is_ok(self.fx.service.resume(self.fx.ctx, task_id)))
        second = self.fx.service.resume(self.fx.ctx, task_id)
        self.assertTrue(is_err(second))
        self.assertEqual(error_of(second).code, ErrorCode.STATE_CONFLICT_NOT_PENDING)
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.RUNNING)

    def test_sweep_twice_is_idempotent(self):
        """重复崩溃扫描：第二次 `sweep` 找不到 `RUNNING` 任务 ⇒ 空列表，
        已回退的任务状态与痕迹**不被二次覆盖**。"""
        draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"])
        task_id = unwrap(self.fx.service.create_task(self.fx.ctx, draft)).id
        unwrap(self.fx.service.start(self.fx.ctx, task_id))

        first = unwrap(self.fx.service.sweep_interrupted(self.fx.ctx))
        self.assertEqual(first, [task_id])
        second = unwrap(self.fx.service.sweep_interrupted(self.fx.ctx))
        self.assertEqual(second, [])

        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.PAUSED)
        self.assertEqual(task.paused_reason, PausedReason.DEPENDENCY)
        self.assertEqual(task.last_error.code, "PROCESS_INTERRUPTED")

    def test_multiple_pause_resume_cycles_execute_each_unit_once(self):
        """三轮"预算耗尽 ⇒ 提额 ⇒ 恢复"循环后任务完成，
        **每个单元恰好执行一次**、用量恰好 5 次（幂等不因反复恢复而失效）。"""
        draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"],
                          budget_limit=BudgetSpec(max_requests=2))
        task_id = unwrap(self.fx.service.create_task(self.fx.ctx, draft)).id
        unwrap(self.fx.service.start(self.fx.ctx, task_id))
        handler = MockUnitHandler(units=_units(5))
        runner = TaskRunner(self.fx.service, handler)

        unwrap(runner.run_to_completion(self.fx.ctx, task_id))
        self.assertEqual(unwrap(self.fx.service.get(self.fx.ctx, task_id)).status,
                         TaskStatus.PAUSED)

        for limit in (4, 10):
            unwrap(self.fx.service.set_budget(
                self.fx.ctx, task_id, BudgetSpec(max_requests=limit)))
            unwrap(self.fx.service.resume(self.fx.ctx, task_id))
            unwrap(runner.run_to_completion(self.fx.ctx, task_id))

        task = unwrap(self.fx.service.get(self.fx.ctx, task_id))
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        for index in range(5):
            self.assertEqual(handler.call_count(f"RESEARCH:{index}"), 1)
        self.assertEqual(task.budget_used.requests, 5)
        self.assertEqual(unwrap(self.fx.storage.usage_report(self.fx.ctx, task_id)).requests, 5)


# ---------------------------------------------------------------- 6. 跨进程重启验证


class TestRestartAcrossProcess(unittest.TestCase):
    """边界 B-1 的**跨进程**验证：检查点保存失败后的"重启"不是同进程换实例，
    而是真的换一个 Python 进程 —— 与 P2.3 的子进程验证同一手法。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-fault-proc-")

    def test_unit_redo_after_checkpoint_loss_survives_real_restart(self):
        fx = TaskFixture(self.tmp)
        try:
            draft = TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"],
                              budget_limit=BudgetSpec(max_requests=10))
            task_id = unwrap(fx.service.create_task(fx.ctx, draft)).id
            unwrap(fx.service.start(fx.ctx, task_id))
            handler = MockUnitHandler(units=_units(2))

            with db_fault(fx.storage, "INSERT INTO checkpoints", DISK_ERROR):
                step = TaskRunner(fx.service, handler).run_step(fx.ctx, task_id)
            self.assertTrue(is_err(step))
            self.assertIsNone(unwrap(fx.service.load_checkpoint(fx.ctx, task_id)))
        finally:
            fx.storage.close()

        # —— 真实子进程：全新解释器、全新存储实例、无故障 ——
        script = (
            "import json, sys\n"
            f"sys.path.insert(0, r'{ROOT}')\n"
            "from persona.core.context import new_call_context\n"
            "from persona.orchestrator import (\n"
            "    BudgetSpec, MockUnitHandler, TaskDraft, TaskRunner, TaskService, WorkUnit,\n"
            ")\n"
            "from persona.core.result import UsageDelta\n"
            "from persona.config import test_settings\n"
            "from persona.storage.sqlite import SqliteStorage\n"
            "from tests.test_task_faults import _units\n"
            f"st = SqliteStorage(r'{Path(self.tmp) / 'tasks.db'}')\n"
            f"sv = TaskService(st, test_settings(data_dir=r'{self.tmp}'))\n"
            "ctx = new_call_context(project_id='prj_test', actor='subprocess')\n"
            f"task_id = sv.get(ctx, '{task_id}').value.id\n"
            "handler = MockUnitHandler(units=_units(2))\n"
            "step = TaskRunner(sv, handler).run_step(ctx, task_id)\n"
            "report = st.usage_report(ctx, task_id).value\n"
            "cp = st.load_checkpoint(ctx, task_id).value\n"
            "st.close()\n"
            "print(json.dumps({'advanced': step.value.advanced,\n"
            "                  'calls': handler.call_names(),\n"
            "                  'requests': report.requests,\n"
            "                  'units': cp.completed_units if cp else []}))\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
        )
        self.assertEqual(proc.returncode, 0, msg=f"子进程失败：{proc.stderr}")
        payload = json.loads(proc.stdout.strip().splitlines()[-1])

        # 子进程里第一个单元被重做（父进程已执行过一次），用量累计到 2
        self.assertTrue(payload["advanced"])
        self.assertEqual(payload["calls"], ["RESEARCH:0"])
        self.assertEqual(payload["requests"], 2)
        self.assertEqual(payload["units"], ["RESEARCH:0"])


if __name__ == "__main__":
    unittest.main()
