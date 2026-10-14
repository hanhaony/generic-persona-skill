"""P3.5 长期记忆基础模块测试（stdlib unittest，零第三方依赖，沿用 D-068）。

覆盖：持久化与重启读回、关键词检索、去重、冲突、过期、归档、删除（含派生产物失效）、
对象/命名空间边界、模拟隔离、supersede、consolidate/rebuild。全部用临时 SQLite，不触网。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from persona.core.context import new_call_context
from persona.core.enums import (
    DerivedStatus,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    ProvenanceNamespace,
)
from persona.core.ids import new_id
from persona.core.result import is_err, is_ok, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query
from persona.modules.memory import (
    DeleteTarget,
    MemoryQuery,
    MemoryService,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_memory_test"


class MemoryFixture:
    """临时 SQLite + 已建表的记忆测试夹具。"""

    def __init__(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="mem_test_")
        self.path = os.path.join(self.tmp, "mem.db")
        self.storage = SqliteStorage(self.path)
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))

    def new_service(self) -> MemoryService:
        # 重新打开一个连接（模拟"重启后读取"），验证持久化
        return MemoryService(SqliteStorage(self.path))

    def service_same_conn(self) -> MemoryService:
        return MemoryService(self.storage)

    def count(self, kind: str, namespace: str | None = None) -> int:
        filters = {"kind": kind, "project_id": PROJECT}
        if namespace:
            filters["namespace"] = namespace
        q = Query(filters=filters, limit=MAX_QUERY_LIMIT)
        res = self.storage.query(self.ctx, kind, q, include_deleted=True)
        return len(unwrap(res).items)

    def put_evidence(self, eid: str | None = None) -> str:
        eid = eid or new_id("EvidenceItem")
        unwrap(
            self.storage.put(
                self.ctx,
                "EvidenceItem",
                {"claim": "占位证据", "tier": "THIRD_PARTY"},
                project_id=PROJECT,
                namespace=ProvenanceNamespace.PUBLIC_SOURCE.value,
                created_by="tests",
                id=eid,
            )
        )
        return eid

    def put_derived(
        self, source_memory_ids: list[str], status: str = DerivedStatus.FRESH.value
    ) -> str:
        aid = new_id("DerivedArtifact")
        unwrap(
            self.storage.put(
                self.ctx,
                "DerivedArtifact",
                {
                    "kind": "SUMMARY",
                    "scope": "ENTITY:pe_alice",
                    "source_memory_ids": source_memory_ids,
                    "content_ref": f"derived:{aid}",
                    "builder": "test",
                    "content_signature": "sig",
                    "status": status,
                    "invalidated_reason": None,
                    "invalidated_at": None,
                    "rebuilt_from_id": None,
                },
                project_id=PROJECT,
                namespace=ProvenanceNamespace.SYSTEM_DERIVED.value,
                created_by="tests",
                id=aid,
            )
        )
        return aid

    def draft(self, **kw) -> dict:
        base = dict(
            scope=MemoryScope.ENTITY.value,
            scope_id="pe_alice",
            kind=MemoryKind.SEMANTIC.value,
            content="Alice 喜欢在清晨写作。",
            source_type=MemorySourceType.USER_INPUT.value,
            namespace=ProvenanceNamespace.USER_PROVIDED.value,
            confidence=0.7,
            confidence_means="用户自述，未经第三方佐证",
            salience=0.6,
            related_entity_ids=["pe_alice"],
        )
        base.update(kw)
        return base

    def simulated_draft(self, **kw) -> dict:
        base = dict(
            scope=MemoryScope.SESSION.value,
            scope_id="css_s1",
            kind=MemoryKind.EPISODIC.value,
            content="模拟对话里 Alice 承认了错误。",
            source_turn_id="ctn_t1",
            confidence=0.5,
            confidence_means="模拟对话中的一次性表述，非确定事实",
            salience=0.4,
            related_entity_ids=["pe_alice"],
        )
        base.update(kw)
        return base

    def close(self) -> None:
        self.storage.close()


class FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = MemoryFixture()
        self.service = self.fx.service_same_conn()

    def tearDown(self) -> None:
        self.fx.close()
        import shutil

        shutil.rmtree(self.fx.tmp, ignore_errors=True)


class TestPersistence(FixtureTestCase):
    def test_write_then_read_back(self):
        res = unwrap(self.service.write(self.fx.ctx, [self.fx.draft()]))
        mid = res[0].id
        row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", mid, include_deleted=True))
        self.assertEqual(row["content"], "Alice 喜欢在清晨写作。")
        self.assertEqual(row["lifecycle_status"], MemoryStatus.ACTIVE.value)
        self.assertEqual(row["namespace"], ProvenanceNamespace.USER_PROVIDED.value)

    def test_persist_survives_restart(self):
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft()]))
        # 用新连接重新打开同一个库（模拟进程重启）
        reboot = self.fx.new_service()
        page = unwrap(reboot.search(self.fx.ctx, MemoryQuery(text="清晨")))
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].content, "Alice 喜欢在清晨写作。")


class TestKeywordSearch(FixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fx.put_evidence()  # 仅让库里多一张无关表，验证隔离
        unwrap(
            self.service.write(
                self.fx.ctx,
                [
                    self.fx.draft(content="Alice 喜欢在清晨写作。", salience=0.6),
                    self.fx.draft(content="Bob 下午会去健身房。", salience=0.3,
                                 scope_id="pe_bob", related_entity_ids=["pe_bob"]),
                    self.fx.draft(content="Alice 讨厌吵闹的咖啡馆。", salience=0.9),
                ],
            )
        )

    def test_text_match_and_exclude(self):
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="清晨")))
        self.assertEqual(page.total, 1)
        self.assertIn("清晨", page.items[0].content)

    def test_multi_term_and(self):
        # "Alice 写作" 两个词都需命中 → 命中第 1 条；"健身房" 不命中
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="Alice 写作")))
        ids = {m.content for m in page.items}
        self.assertIn("Alice 喜欢在清晨写作。", ids)
        self.assertNotIn("Bob 下午会去健身房。", ids)

    def test_scope_and_entity_filters(self):
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(entity_ids=["pe_bob"])))
        self.assertEqual(page.total, 1)
        self.assertIn("健身房", page.items[0].content)

    def test_namespace_exclude(self):
        # 当前都是 USER_PROVIDED；排除它后应无结果
        page = unwrap(
            self.service.search(
                self.fx.ctx, MemoryQuery(namespaces_exclude=[ProvenanceNamespace.USER_PROVIDED.value])
            )
        )
        self.assertEqual(page.total, 0)

    def test_excludes_terminal_states_by_default(self):
        # 新建一条并归档/删除后再检索，默认不应出现
        res = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="临时记忆")]))
        mid = res[0].id
        unwrap(self.service.archive(self.fx.ctx, [mid]))
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="临时记忆")))
        self.assertEqual(page.total, 0)

    def test_salience_affects_ordering(self):
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="Alice")))
        # 两条都含 Alice：salience 0.9 应排在 0.6 之前
        self.assertEqual(page.items[0].salience, 0.9)

    def test_access_count_incremented(self):
        res = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="命中计数")]))
        mid = res[0].id
        unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="命中计数")))
        row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", mid, include_deleted=True))
        self.assertGreaterEqual(int(row["access_count"]), 1)


class TestDedup(FixtureTestCase):
    def test_duplicate_content_returns_existing(self):
        r1 = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="重复内容")]))
        r2_res = self.service.write(self.fx.ctx, [self.fx.draft(content="重复内容")])
        r2 = unwrap(r2_res)
        self.assertEqual(r2[0].id, r1[0].id)
        self.assertIn("MEMORY_DEDUP_HIT", [w.code for w in r2_res.warnings])
        # 库中只应有 1 条 ACTIVE
        self.assertEqual(self.fx.count("MemoryItem"), 1)


class TestConflict(FixtureTestCase):
    def test_link_conflict_both_conflicted_and_linked(self):
        a = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="A 说 X")]))[0].id
        b = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="B 说非 X")]))[0].id
        link = unwrap(self.service.link_conflict(self.fx.ctx, a, b, note="事实冲突"))
        self.assertEqual(link.memory_id_a, a)
        ra = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", a, include_deleted=True))
        rb = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", b, include_deleted=True))
        self.assertEqual(ra["lifecycle_status"], MemoryStatus.CONFLICTED.value)
        self.assertEqual(rb["lifecycle_status"], MemoryStatus.CONFLICTED.value)
        self.assertIn(b, ra["conflict_with_memory_ids"])
        self.assertIn(a, rb["conflict_with_memory_ids"])

    def test_conflict_returned_when_explicitly_requested(self):
        a = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="A 说 X")]))[0].id
        b = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="B 说非 X")]))[0].id
        unwrap(self.service.link_conflict(self.fx.ctx, a, b))
        # 默认检索不含 CONFLICTED（DATA_MODEL §7.6 #34）
        default = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="X")))
        self.assertEqual(default.total, 0)
        # 显式包含 CONFLICTED 时两条都返回（INTERFACES §9.3）
        incl = unwrap(
            self.service.search(
                self.fx.ctx,
                MemoryQuery(text="X", lifecycle_states=[MemoryStatus.ACTIVE.value, MemoryStatus.CONFLICTED.value]),
            )
        )
        self.assertEqual(incl.total, 2)

    def test_self_conflict_rejected(self):
        a = unwrap(self.service.write(self.fx.ctx, [self.fx.draft()]))[0].id
        res = self.service.link_conflict(self.fx.ctx, a, a)
        self.assertTrue(is_err(res))


class TestExpire(FixtureTestCase):
    def test_expire_sweep_and_invalidate_derived(self):
        from persona.core.temporal import to_iso, utc_now
        from datetime import timedelta

        past = to_iso(utc_now() - timedelta(days=10))
        mid = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="会过期的记忆", expires_at=past)]))[0].id
        aid = self.fx.put_derived([mid])
        sweep = unwrap(self.service.expire_sweep(self.fx.ctx))
        self.assertIn(mid, sweep.expired_ids)
        self.assertIn(aid, sweep.invalidated_artifact_ids)
        row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", mid, include_deleted=True))
        self.assertEqual(row["lifecycle_status"], MemoryStatus.EXPIRED.value)
        art = unwrap(self.fx.storage.get(self.fx.ctx, "DerivedArtifact", aid, include_deleted=True))
        self.assertEqual(art["status"], DerivedStatus.INVALIDATED.value)

    def test_expired_excluded_from_default_search(self):
        from persona.core.temporal import to_iso, utc_now
        from datetime import timedelta

        past = to_iso(utc_now() - timedelta(days=10))
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="过期记忆", expires_at=past)]))
        # 必须先触发过期扫描才会置 EXPIRED（expires_at 只是字段，不会自动生效）
        unwrap(self.service.expire_sweep(self.fx.ctx))
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="过期记忆")))
        self.assertEqual(page.total, 0)


class TestArchive(FixtureTestCase):
    def test_archive_excluded_by_default(self):
        mid = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="归档记忆")]))[0].id
        unwrap(self.service.archive(self.fx.ctx, [mid]))
        self.assertEqual(unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="归档记忆"))).total, 0)
        archived = unwrap(
            self.service.search(self.fx.ctx, MemoryQuery(text="归档记忆", lifecycle_states=[MemoryStatus.ARCHIVED.value]))
        )
        self.assertEqual(archived.total, 1)


class TestDelete(FixtureTestCase):
    def test_delete_invalidates_derived(self):
        mid = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="待删记忆")]))[0].id
        aid = self.fx.put_derived([mid])
        out = unwrap(self.service.delete(self.fx.ctx, DeleteTarget(id=mid)))
        self.assertIn(mid, out.deleted_ids)
        self.assertIn(aid, out.invalidated_artifact_ids)
        self.assertIsNotNone(out.scheduled_purge_at)
        row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", mid, include_deleted=True))
        self.assertEqual(row["lifecycle_status"], MemoryStatus.DELETED.value)
        self.assertEqual(row["purge_state"], "PENDING_PURGE")
        art = unwrap(self.fx.storage.get(self.fx.ctx, "DerivedArtifact", aid, include_deleted=True))
        self.assertEqual(art["status"], DerivedStatus.INVALIDATED.value)
        # 默认检索排除已删
        self.assertEqual(unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="待删记忆"))).total, 0)

    def test_delete_by_scope(self):
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="s1a", scope_id="pe_s1")]))
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="s1b", scope_id="pe_s1")]))
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="s2", scope_id="pe_s2")]))
        out = unwrap(self.service.delete(self.fx.ctx, DeleteTarget(scope="ENTITY")))
        self.assertEqual(len(out.deleted_ids), 3)

    def test_delete_missing_id_reports_not_found(self):
        # storage.update 对缺失 id 返回 NOT_FOUND（§9.3 delete 失败含 NOT_FOUND）
        res = self.service.delete(self.fx.ctx, DeleteTarget(id=new_id("MemoryItem")))
        self.assertTrue(is_err(res))


class TestObjectBoundary(FixtureTestCase):
    def test_simulated_forces_namespace_and_source_type(self):
        ref = unwrap(self.service.write_simulated(self.fx.ctx, self.fx.simulated_draft()))
        row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", ref.id, include_deleted=True))
        self.assertEqual(row["namespace"], ProvenanceNamespace.SIMULATED.value)
        self.assertEqual(row["source_type"], MemorySourceType.SIMULATED_DIALOGUE.value)
        self.assertEqual(row["source_turn_id"], "ctn_t1")

    def test_namespace_isolation_in_search(self):
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="真实记忆")]))
        unwrap(self.service.write_simulated(self.fx.ctx, self.fx.simulated_draft(content="模拟记忆")))
        real_only = unwrap(
            self.service.search(
                self.fx.ctx, MemoryQuery(text="记忆", namespaces_exclude=[ProvenanceNamespace.SIMULATED.value])
            )
        )
        self.assertEqual(real_only.total, 1)
        self.assertIn("真实记忆", real_only.items[0].content)
        sim_only = unwrap(
            self.service.search(
                self.fx.ctx, MemoryQuery(text="记忆", namespaces_include=[ProvenanceNamespace.SIMULATED.value])
            )
        )
        self.assertEqual(sim_only.total, 1)
        self.assertIn("模拟记忆", sim_only.items[0].content)

    def test_simulated_cannot_become_evidence(self):
        before = self.fx.count("EvidenceItem")
        unwrap(self.service.write_simulated(self.fx.ctx, self.fx.simulated_draft()))
        after = self.fx.count("EvidenceItem")
        self.assertEqual(before, after)  # 模拟记忆从不写入证据表

    def test_namespace_mismatch_rejected(self):
        bad = self.fx.draft(
            source_type=MemorySourceType.SIMULATED_DIALOGUE.value,
            namespace=ProvenanceNamespace.USER_PROVIDED.value,
            source_turn_id="ctn_x",
        )
        res = self.service.write(self.fx.ctx, [bad])
        self.assertTrue(is_err(res))


class TestSimulatedIsolation(FixtureTestCase):
    def test_real_tables_unchanged_after_simulated_writes(self):
        snap = (
            self.fx.count("EvidenceItem"),
            self.fx.count("PersonaProfile"),
            self.fx.count("RelationshipProfile"),
        )
        for i in range(3):
            unwrap(self.service.write_simulated(self.fx.ctx, self.fx.simulated_draft(content=f"模拟 {i}")))
        after = (
            self.fx.count("EvidenceItem"),
            self.fx.count("PersonaProfile"),
            self.fx.count("RelationshipProfile"),
        )
        self.assertEqual(snap, after)

    def test_clear_simulated_only_removes_simulated(self):
        unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="真实记忆")]))
        unwrap(self.service.write_simulated(self.fx.ctx, self.fx.simulated_draft(content="模拟记忆")))
        n = unwrap(self.service.clear_simulated(self.fx.ctx))
        self.assertEqual(n, 1)
        # 软删除保留记录行（D-025 永不物理删除），但检索不再返回模拟记忆
        self.assertEqual(
            unwrap(
                self.service.search(
                    self.fx.ctx, MemoryQuery(text="记忆", namespaces_include=[ProvenanceNamespace.SIMULATED.value])
                )
            ).total,
            0,
        )
        # 真实记忆不受影响
        self.assertEqual(self.fx.count("MemoryItem", namespace=ProvenanceNamespace.USER_PROVIDED.value), 1)


class TestSupersede(FixtureTestCase):
    def test_supersede_keeps_old_immutable_and_marks_new_active(self):
        old = unwrap(self.service.write(self.fx.ctx, [self.fx.draft(content="旧内容")]))[0].id
        new_ref = unwrap(
            self.service.supersede(self.fx.ctx, old, {"content": "新内容"})
        )
        new_idv = new_ref.id
        self.assertNotEqual(old, new_idv)
        old_row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", old, include_deleted=True))
        new_row = unwrap(self.fx.storage.get(self.fx.ctx, "MemoryItem", new_idv, include_deleted=True))
        self.assertEqual(old_row["lifecycle_status"], MemoryStatus.SUPERSEDED.value)
        self.assertEqual(old_row["superseded_by_memory_id"], new_idv)
        self.assertEqual(old_row["content"], "旧内容")  # 旧内容不可变
        self.assertEqual(new_row["lifecycle_status"], MemoryStatus.ACTIVE.value)
        # 默认检索只返回新版本
        page = unwrap(self.service.search(self.fx.ctx, MemoryQuery(text="内容")))
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].content, "新内容")


class TestConsolidate(FixtureTestCase):
    def test_consolidate_and_idempotent(self):
        unwrap(
            self.service.write(
                self.fx.ctx,
                [
                    self.fx.draft(content="要点一", scope_id="pe_s"),
                    self.fx.draft(content="要点二", scope_id="pe_s"),
                ],
            )
        )
        first = unwrap(self.service.consolidate(self.fx.ctx, scope="ENTITY"))
        self.assertIsNotNone(first.artifact_id)
        self.assertTrue(first.fresh)
        # 再次 consolidate 应复用同一产物（签名一致）
        second = unwrap(self.service.consolidate(self.fx.ctx, scope="ENTITY"))
        self.assertEqual(first.artifact_id, second.artifact_id)

    def test_rebuild_invalidates_when_source_deleted(self):
        mids = [
            r.id
            for r in unwrap(
                self.service.write(
                    self.fx.ctx,
                    [
                        self.fx.draft(content="a", scope_id="pe_s"),
                        self.fx.draft(content="b", scope_id="pe_s"),
                    ],
                )
            )
        ]
        art = unwrap(self.service.consolidate(self.fx.ctx, scope="ENTITY")).artifact_id
        # 删除一条来源 → 重建应失效
        unwrap(self.service.delete(self.fx.ctx, DeleteTarget(id=mids[0])))
        rebuilt = unwrap(self.service.rebuild_derived(self.fx.ctx, art))
        self.assertFalse(rebuilt.fresh)
        artifact = unwrap(self.fx.storage.get(self.fx.ctx, "DerivedArtifact", art, include_deleted=True))
        self.assertEqual(artifact["status"], DerivedStatus.INVALIDATED.value)


class TestValidationErrors(FixtureTestCase):
    def test_confidence_means_missing(self):
        d = self.fx.draft()
        d["confidence_means"] = ""
        res = self.service.write(self.fx.ctx, [d])
        self.assertTrue(is_err(res))

    def test_reference_missing(self):
        d = self.fx.draft(referenced_evidence_ids=[new_id("EvidenceItem")])
        res = self.service.write(self.fx.ctx, [d])
        self.assertTrue(is_err(res))

    def test_reference_present_ok(self):
        eid = self.fx.put_evidence()
        d = self.fx.draft(referenced_evidence_ids=[eid])
        res = self.service.write(self.fx.ctx, [d])
        self.assertTrue(is_ok(res))

    def test_link_duplicate_self_rejected(self):
        a = unwrap(self.service.write(self.fx.ctx, [self.fx.draft()]))[0].id
        res = self.service.link_duplicate(self.fx.ctx, a, a)
        self.assertTrue(is_err(res))

    def test_unknown_scope_rejected(self):
        d = self.fx.draft(scope="NOT_A_SCOPE")
        res = self.service.write(self.fx.ctx, [d])
        self.assertTrue(is_err(res))


class TestWriterPortContract(FixtureTestCase):
    def test_write_simulated_satisfies_dialogue_writer_port(self):
        from persona.modules.dialogue import MemoryWriterPort

        # 结构上的端口契约：仅含 write_simulated
        import inspect

        self.assertEqual(
            {n for n, _ in inspect.getmembers(MemoryWriterPort, inspect.isfunction) if not n.startswith("_")},
            {"write_simulated"},
        )
        # MemoryService 实例可被当作 MemoryWriterPort 使用
        ref = unwrap(self.service.write_simulated(self.fx.ctx, self.fx.simulated_draft()))
        self.assertTrue(ref.id.startswith("mem_"))


if __name__ == "__main__":
    unittest.main()
