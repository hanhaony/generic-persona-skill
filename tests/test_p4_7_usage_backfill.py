"""P4.7：`persona usage backfill`（人工补记产品命令）测试。

为什么必须做成产品命令（而不是继续手写脚本）：账本是**费用闸门**的唯一依据。
补记若没有产品入口，就会出现两种走样——
  1. 补记被忘掉（累计闸门永远少算一笔，等于闸门是假的）；
  2. 每轮各写一个临时脚本，口径各不相同（本次 C 轮就是这么漏掉的）。

本文件锁住的四条红线：
  - **不许匿名补记**：`--reason` / `--operator` 缺失即拒绝，且**一行都不写**；
  - **幂等键标识事件本身**，不含 operator：同一笔账单被两个人各补一次会更糟；
  - **估算 ≠ 实际**：`--estimated` 只写 `cost_estimate`，`cost_confirmed` 留空、
    状态 `UNKNOWN`（D-060：未知不是 0）；
  - **补记能进闸门**：补完之后 `hydrate` 得到的累计支出必须包含它。

全程离线：本命令不发起任何外部调用。
"""

from __future__ import annotations

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from persona.cli.main import EXIT_OK, main
from persona.core.context import new_call_context
from persona.core.result import is_ok, unwrap
from persona.core.types import CURRENCY_UNKNOWN, COST_CONFIRMED, COST_UNKNOWN
from persona.orchestrator import StackOptions, build_business_stack
from persona.storage.sqlite import SqliteStorage


@contextlib.contextmanager
def _temp_data_dir():
    """把 CLI 的库指到临时目录（D-070：测试不得在仓库里留 .db）。"""
    with tempfile.TemporaryDirectory(prefix="gps-backfill-") as tmp:
        previous = os.environ.get("PERSONA_DATA_DIR")
        os.environ["PERSONA_DATA_DIR"] = tmp
        try:
            yield Path(tmp)
        finally:
            if previous is None:
                os.environ.pop("PERSONA_DATA_DIR", None)
            else:
                os.environ["PERSONA_DATA_DIR"] = previous


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


def _rows(db: Path) -> list[tuple]:
    """直接读账本表（CLI 关库后再读，避免与它的连接互相影响）。"""
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        return list(conn.execute(
            "SELECT idempotency_key, provider, operation, outcome, requested_at,"
            " cost_estimate, cost_confirmed, currency, cost_source, cost_status,"
            " task_id, trace_ref FROM external_usage_records"
            " ORDER BY requested_at, idempotency_key"))
    finally:
        conn.close()


class UsageBackfillCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = _temp_data_dir()
        self.tmp = self._dir.__enter__()
        self.db = self.tmp / "persona.db"
        self.base = ["usage", "backfill", "--no-dotenv"]

    def tearDown(self) -> None:
        self._dir.__exit__(None, None, None)

    # ------------------------------------------------------------ 必填守门

    def test_missing_operator_refuses_and_writes_nothing(self):
        code, text = _run([*self.base, "--key", "k1", "--amount", "0.01",
                           "--reason", "补记", "--operator", "   "])
        self.assertEqual(code, 1)
        self.assertIn("不允许匿名补记", text)
        self.assertEqual(_rows(self.db), [], "校验失败时**一行都不许写**")

    def test_missing_reason_refuses_and_writes_nothing(self):
        code, text = _run([*self.base, "--key", "k1", "--amount", "0.01",
                           "--reason", "", "--operator", "alice"])
        self.assertEqual(code, 1)
        self.assertIn("--reason 不能为空", text)
        self.assertEqual(_rows(self.db), [])

    def test_non_positive_amount_refuses(self):
        for bad in ("0", "-0.5"):
            code, text = _run([*self.base, "--key", "k1", "--amount", bad,
                               "--reason", "补记", "--operator", "alice"])
            self.assertEqual(code, 1, f"amount={bad} 必须被拒绝")
            self.assertIn("必须是正数", text)
        self.assertEqual(_rows(self.db), [])

    def test_key_with_whitespace_refuses(self):
        code, text = _run([*self.base, "--key", "a b", "--amount", "0.01",
                           "--reason", "补记", "--operator", "alice"])
        self.assertEqual(code, 1)
        self.assertIn("不能含空白", text)
        self.assertEqual(_rows(self.db), [])

    # ------------------------------------------------------------ 正常路径

    def test_backfill_writes_confirmed_record_with_traceable_fields(self):
        code, text = _run([*self.base, "--key", "p45a-round",
                           "--amount", "0.0012765", "--reason", "C 轮未落库",
                           "--operator", "alice", "--task-id", "task_x"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("已写入账本", text)

        rows = _rows(self.db)
        self.assertEqual(len(rows), 1)
        (key, provider, operation, outcome, requested_at, estimate, confirmed,
         currency, source, status, task_id, trace_ref) = rows[0]
        self.assertEqual(key, "manual-backfill:p45a-round")
        self.assertEqual(provider, "manual")
        self.assertEqual(operation, "CHAT")
        self.assertEqual(outcome, "SUCCEEDED")
        self.assertEqual(currency, "USD")
        self.assertEqual(status, COST_CONFIRMED)
        self.assertAlmostEqual(confirmed, 0.0012765, places=10)
        self.assertAlmostEqual(estimate, 0.0012765, places=10)
        self.assertEqual(task_id, "task_x")
        # 原因与操作者都进 trace_ref → 可以反查到人
        self.assertIn("alice", trace_ref)
        self.assertIn("C 轮未落库", trace_ref)
        self.assertTrue(requested_at.endswith("Z"), "时间戳统一用 UTC ISO")

    def test_same_key_twice_is_idempotent(self):
        argv = [*self.base, "--key", "dup", "--amount", "0.002",
                "--reason", "补记", "--operator", "alice"]
        first, _ = _run(argv)
        second, text = _run(argv)
        self.assertEqual(first, EXIT_OK)
        self.assertEqual(second, EXIT_OK)
        self.assertIn("未重复记账", text)
        self.assertEqual(len(_rows(self.db)), 1, "同一幂等键不得重复记账")

    def test_key_identifies_the_event_not_the_operator(self):
        """同一笔账单换个人再补一次，必须**命中去重**（否则金额翻倍）。"""
        _run([*self.base, "--key", "bill-42", "--amount", "0.003",
              "--reason", "补记", "--operator", "alice"])
        code, text = _run([*self.base, "--key", "bill-42", "--amount", "0.003",
                           "--reason", "补记", "--operator", "bob"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("未重复记账", text)
        self.assertEqual(len(_rows(self.db)), 1)

    def test_estimated_mode_leaves_confirmed_empty(self):
        code, text = _run([*self.base, "--key", "est", "--amount", "0.004",
                           "--reason", "尚未拿到账单", "--operator", "alice",
                           "--estimated"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("估算", text)

        rows = _rows(self.db)
        self.assertEqual(len(rows), 1)
        _key, _p, _o, _out, _at, estimate, confirmed, _cur, source, status, _t, _tr = rows[0]
        self.assertAlmostEqual(estimate, 0.004, places=10)
        self.assertIsNone(confirmed, "估算模式不得写实际值（估算 ≠ 账单）")
        self.assertEqual(status, COST_UNKNOWN)
        self.assertNotEqual(source, "OFFICIAL_RATE", "估算不该声称来自官方费率")

    # ------------------------------------------------------------ 与闸门的关系

    def test_backfilled_record_is_counted_by_ledger_hydration(self):
        """补记的意义就在这个断言：它必须真的进得了累计闸门。"""
        _run([*self.base, "--key", "hydrate-me", "--amount", "0.0025",
              "--reason", "补记", "--operator", "alice"])

        from persona.config import load_settings

        settings = unwrap(load_settings(use_dotenv=False))
        ctx = new_call_context(project_id="prj_local", actor="tests")
        storage = SqliteStorage(self.db)
        try:
            stack = unwrap(build_business_stack(
                settings, ctx, storage=storage,
                options=StackOptions(task_id="task_hydrate", hydrate_ledger=True)))
        finally:
            pass
        try:
            self.assertIsNotNone(stack.hydration)
            self.assertGreaterEqual(stack.hydration.loaded, 1)
            self.assertAlmostEqual(stack.ledger.occupied_cost(), 0.0025, places=7)
        finally:
            storage.close()

    def test_currency_defaults_to_usd_and_is_recorded(self):
        _run([*self.base, "--key", "cur", "--amount", "0.001",
              "--reason", "补记", "--operator", "alice"])
        rows = _rows(self.db)
        self.assertEqual(rows[0][7], "USD")
        self.assertNotEqual(rows[0][7], CURRENCY_UNKNOWN,
                            "补记必须声明计量单位，不能留 UNKNOWN")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
