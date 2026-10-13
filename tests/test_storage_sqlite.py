"""SQLite 临时数据库读写测试。

对应文档：INTERFACES §3。要点：
  - 全部跑在**临时目录**的 SQLite 文件上，不碰任何正式项目库
  - 验证：建表、写入、读取、查询分页、乐观锁、软删除、引用检查、检查点、用量、审计
  - 验证**隔离红线**：`EvidenceItem` 不允许 `namespace=SIMULATED`（D-023）
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import ProvenanceNamespace
from persona.core.errors import ErrorCode
from persona.core.result import UsageDelta, is_err, is_ok, unwrap
from persona.core.types import Checkpoint, Query
from persona.orchestrator.checkpoint import new_checkpoint
from persona.storage.schema import SCHEMA_VERSION
from persona.storage.sqlite import SqliteStorage


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gps-test-")
        self.db_path = Path(self.tmp) / "test.db"
        self.store = SqliteStorage(self.db_path)
        self.ctx = new_call_context(project_id="prj_test", actor="test")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put_person(self, name="测试对象", namespace=ProvenanceNamespace.PUBLIC_SOURCE):
        return self.store.put(
            self.ctx,
            "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER"},
            project_id="prj_test",
            namespace=namespace,
        )


class TestSchemaAndBasicCrud(StorageTestCase):
    def test_init_schema_is_idempotent(self):
        self.assertTrue(is_ok(self.store.init_schema(self.ctx)))
        self.assertTrue(is_ok(self.store.init_schema(self.ctx)))
        self.assertEqual(unwrap(self.store.schema_version(self.ctx)), SCHEMA_VERSION)

    def test_put_get_roundtrip(self):
        self.store.init_schema(self.ctx)
        rid = unwrap(self._put_person())
        got = unwrap(self.store.get(self.ctx, "PersonEntity", rid))
        self.assertEqual(got["display_name"], "测试对象")
        self.assertEqual(got["namespace"], "PUBLIC_SOURCE")
        self.assertEqual(got["kind"], "PersonEntity")
        self.assertEqual(got["rev"], 1)
        self.assertIsNone(got["deleted_at"])

    def test_get_missing_is_ok_none_not_error(self):
        """没找到**不是错误**（ERROR_HANDLING §2.3 精神）。"""
        self.store.init_schema(self.ctx)
        result = self.store.get(self.ctx, "PersonEntity", "pe_not_exist")
        self.assertTrue(is_ok(result))
        self.assertIsNone(result.value)

    def test_query_pagination(self):
        self.store.init_schema(self.ctx)
        for i in range(5):
            self._put_person(name=f"对象{i}")
        page = unwrap(self.store.query(self.ctx, "PersonEntity", Query(limit=2)))
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.total, 5)
        self.assertTrue(page.has_more)

    def test_query_filter_and_sort(self):
        self.store.init_schema(self.ctx)
        self._put_person(name="甲")
        self._put_person(name="乙")
        page = unwrap(
            self.store.query(
                self.ctx,
                "PersonEntity",
                Query(filters={"project_id": "prj_test"}, sort=("created_at", "desc")),
            )
        )
        self.assertEqual(page.total, 2)

    def test_limit_too_large_rejected(self):
        self.store.init_schema(self.ctx)
        result = self.store.query(self.ctx, "PersonEntity", Query(limit=201))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_LIMIT_TOO_LARGE)

    def test_unknown_filter_rejected(self):
        self.store.init_schema(self.ctx)
        result = self.store.query(self.ctx, "PersonEntity", Query(filters={"nickname": "x"}))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)

    def test_unknown_kind_rejected(self):
        self.store.init_schema(self.ctx)
        result = self.store.put(self.ctx, "NotAnEntity", {}, project_id="prj_test",
                                namespace=ProvenanceNamespace.PUBLIC_SOURCE)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_KIND)

    def test_missing_namespace_rejected(self):
        self.store.init_schema(self.ctx)
        result = self.store.put(self.ctx, "PersonEntity", {"display_name": "x"},
                                project_id="prj_test")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)


class TestOptimisticLockAndSoftDelete(StorageTestCase):
    def test_update_and_optimistic_lock(self):
        self.store.init_schema(self.ctx)
        rid = unwrap(self._put_person())
        updated = unwrap(self.store.update(self.ctx, "PersonEntity", rid,
                                           {"display_name": "改名"}, expected_rev=1))
        self.assertEqual(updated["payload"]["display_name"], "改名")
        self.assertEqual(updated["rev"], 2)

        conflict = self.store.update(self.ctx, "PersonEntity", rid, {"display_name": "再改"},
                                     expected_rev=1)
        self.assertTrue(is_err(conflict))
        self.assertEqual(conflict.error.code, ErrorCode.STORAGE_CONFLICT)

    def test_soft_delete_keeps_row(self):
        """D-025：所有删除一律软删除 + 审计，永不物理删除。"""
        self.store.init_schema(self.ctx)
        rid = unwrap(self._put_person())
        self.assertTrue(is_ok(self.store.soft_delete(self.ctx, "PersonEntity", rid)))
        self.assertIsNone(unwrap(self.store.get(self.ctx, "PersonEntity", rid)))
        still = unwrap(self.store.get(self.ctx, "PersonEntity", rid, include_deleted=True))
        self.assertIsNotNone(still["deleted_at"])
        self.assertGreaterEqual(unwrap(self.store.audit_count(self.ctx)), 1)

    def test_check_references(self):
        self.store.init_schema(self.ctx)
        rid = unwrap(self._put_person())
        report = unwrap(self.store.check_references(self.ctx, "PersonEntity", [rid, "pe_missing"]))
        self.assertEqual(report.missing, ["pe_missing"])
        self.assertFalse(report.ok)


class TestIsolationGate(StorageTestCase):
    def test_evidence_item_cannot_be_simulated(self):
        """D-023 / INV-2：模拟内容永远进不了证据表。"""
        self.store.init_schema(self.ctx)
        result = self.store.put(
            self.ctx,
            "EvidenceItem",
            {"claim": "这是模拟出来的话"},
            project_id="prj_test",
            namespace=ProvenanceNamespace.SIMULATED,
        )
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_conversation_turn_must_be_simulated(self):
        self.store.init_schema(self.ctx)
        result = self.store.put(
            self.ctx,
            "ConversationTurn",
            {"content": "模拟发言"},
            project_id="prj_test",
            namespace=ProvenanceNamespace.PUBLIC_SOURCE,
        )
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_simulated_memory_is_allowed(self):
        self.store.init_schema(self.ctx)
        result = self.store.put(
            self.ctx,
            "MemoryItem",
            {"content": "模拟记忆"},
            project_id="prj_test",
            namespace=ProvenanceNamespace.SIMULATED,
        )
        self.assertTrue(is_ok(result))


class TestCheckpointAndUsage(StorageTestCase):
    def test_checkpoint_roundtrip(self):
        self.store.init_schema(self.ctx)
        cp = new_checkpoint("tsk_1", stage="RESEARCH", cursor="query:2",
                            completed_units=["query:0", "query:1"])
        token = unwrap(self.store.save_checkpoint(self.ctx, "tsk_1", cp))
        self.assertTrue(token)

        loaded = unwrap(self.store.load_checkpoint(self.ctx, "tsk_1"))
        self.assertIsInstance(loaded, Checkpoint)
        self.assertEqual(loaded.stage, "RESEARCH")
        self.assertEqual(loaded.cursor, "query:2")
        self.assertEqual(loaded.completed_units, ["query:0", "query:1"])
        self.assertEqual(loaded.resume_token, token)

    def test_load_missing_checkpoint_is_ok_none(self):
        self.store.init_schema(self.ctx)
        self.assertIsNone(unwrap(self.store.load_checkpoint(self.ctx, "tsk_missing")))

    def test_usage_records_unknown_tokens_as_null(self):
        """D-060：未知的量写 NULL，不填估计值。"""
        self.store.init_schema(self.ctx)
        self.assertTrue(is_ok(self.store.record_usage(
            self.ctx, "tsk_1", UsageDelta(requests=1, tokens_in=None, tokens_out=None,
                                          latency_ms=12))))
        report = unwrap(self.store.usage_report(self.ctx, "tsk_1"))
        self.assertEqual(report.requests, 1)
        self.assertEqual(report.tokens_in, 0)
        self.assertEqual(report.tokens_out, 0)
        self.assertEqual(report.latency_ms_total, 12)
        self.assertEqual(report.breakdown_by_actor.get("test"), 1)

    def test_transaction_rolls_back_on_err(self):
        self.store.init_schema(self.ctx)
        from persona.core.errors import new_error
        from persona.core.result import Err

        def failing() -> "Err":
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD, "故意失败"))

        result = self.store.transaction(self.ctx, failing)
        self.assertTrue(is_err(result))
        page = unwrap(self.store.query(self.ctx, "PersonEntity", Query()))
        self.assertEqual(page.total, 0)

    def test_dry_run_writes_nothing(self):
        dry = new_call_context(project_id="prj_test", actor="test", dry_run=True)
        self.store.init_schema(self.ctx)
        self.store.put(dry, "PersonEntity", {"display_name": "不会落盘"},
                       project_id="prj_test", namespace=ProvenanceNamespace.PUBLIC_SOURCE)
        page = unwrap(self.store.query(self.ctx, "PersonEntity", Query()))
        self.assertEqual(page.total, 0)


if __name__ == "__main__":
    unittest.main()
