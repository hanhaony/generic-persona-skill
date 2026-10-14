"""Evidence 模块测试（P2 证据管线第一步；用户称之为 P3.1）。

全部跑在**临时目录**的 SQLite 文件上，不碰任何正式项目库、不调外部服务。

覆盖（对应用户验收清单第 8 条 + 模块诚实边界）：
  1. 创建与查询（来源 / 事件 / 证据的登记、读取、过滤、排序、分页）
  2. 来源追溯（证据 → source_id → SourceEvent → locator 往返；查询必返归属字段）
  3. 证据分类（三类原始证据 + INFERENCE 不参与排序；三条评分互不等价）
  4. 冲突保留（不裁决、不阻断、双方说法都在、无 RESOLVED 状态）
  5. 重复记录处理（来源去重 / 转载保留 / 证据五元组并组 / 语义相似不并组）
  6. 模拟内容隔离（模块层 + 存储层双层闸门；全表扫描无 SIMULATED）
  7. 非法输入（各校验规则的失败码）
  8. 虚构版本隔离（variant_group 不得跨指；版本分歧进冲突而非合并）
  9. 稳定性闸门（SINGLE / REPEATED / PATTERN；只读）
 10. 重启持久化（新存储实例读回）
 11. 模块边界（F-2：不自行启动研究流程；只依赖 Storage）

对应文档：INTERFACES §4、DATA_MODEL §4.3~§4.6、ERROR_HANDLING §2.2、
DECISIONS §2.16（D-102~D-110）。
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    CanonLevel,
    DivergenceKind,
    EvidenceDimension,
    EvidenceStatus,
    EvidenceTier,
    ProvenanceNamespace,
    ReviewStatus,
    SourceKind,
    SourceRole,
    Stability,
)
from persona.core.errors import ErrorCode
from persona.core.ids import new_token
from persona.core.result import is_err, is_ok, unwrap
from persona.core.temporal import FuzzyDate, TimePrecision
from persona.core.types import Query
from persona.modules import evidence as ev
from persona.modules.evidence import (
    GRANULARITIES,
    TIER_PRIORITY,
    ConflictDraft,
    EvidenceDraft,
    EvidenceService,
    InferenceDraft,
    SourceDraft,
    SourceEventDraft,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_evidence"


class EvidenceFixture:
    """一个临时数据库 + 证据服务 + 调用上下文。用完即关。"""

    def __init__(self, tmp: str) -> None:
        self.storage = SqliteStorage(Path(tmp) / "evidence.db")
        self.service = EvidenceService(self.storage)
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self._hero: str | None = None

    # ---------------------------------------------------------- 基础构件

    def entity(self, name: str = "林小满", *,
               variant_group_id: str | None = None) -> str:
        payload: dict = {"display_name": name, "entity_type": "FICTIONAL_CHARACTER"}
        if variant_group_id:
            payload["variant_group_id"] = variant_group_id
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity", payload,
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def hero(self) -> str:
        if self._hero is None:
            self._hero = self.entity("林小满")
        return self._hero

    def source(self, title: str = "《雾港与星灯》访谈录", *,
               canonical_key: str | None = None,
               kind=SourceKind.INTERVIEW, **kwargs) -> str:
        draft = SourceDraft(
            title=title,
            canonical_key=canonical_key or f"key_{new_token()}",
            kind=kind,
            **kwargs,
        )
        return unwrap(self.service.register_source(self.ctx, draft)).id

    def event(self, source_id: str, title: str = "第 3 集剧情", *,
              locator: dict | None = None, **kwargs) -> str:
        draft = SourceEventDraft(
            source_id=source_id, title=title,
            locator=locator if locator is not None else {"timestamp": "00:12:30"},
            **kwargs,
        )
        return unwrap(self.service.add_source_event(self.ctx, draft)).id

    def evidence(self, source_id: str, claim: str = "他习惯在雨夜出门", *,
                 subject: str | None = None, **kwargs) -> str:
        draft = EvidenceDraft(
            claim=claim,
            tier=kwargs.pop("tier", EvidenceTier.PROFILE_BACKGROUND),
            dimension=kwargs.pop("dimension", EvidenceDimension.TRAIT),
            subject_entity_ids=[subject or self.hero()],
            extraction_confidence=kwargs.pop("extraction_confidence", 0.9),
            source_id=source_id,
            **kwargs,
        )
        return unwrap(self.service.add_evidence(self.ctx, draft)).id


def _fixture() -> EvidenceFixture:
    return EvidenceFixture(tempfile.mkdtemp(prefix="gps-evidence-"))


class FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()

    def tearDown(self) -> None:
        self.fx.storage.close()
        shutil.rmtree(Path(self.fx.storage.path).parent, ignore_errors=True)


# ==========================================================================
# 1. 创建与查询
# ==========================================================================


class TestSourceRegistration(FixtureTestCase):
    def test_register_and_get_roundtrip(self):
        sid = self.fx.source(title="《雾港与星灯》访谈录",
                             publisher="星灯出版社", reliability="HIGH")
        view = unwrap(self.fx.service.get_source(self.fx.ctx, sid))
        self.assertEqual(view.title, "《雾港与星灯》访谈录")
        self.assertEqual(view.kind, "INTERVIEW")
        self.assertEqual(view.reliability, "HIGH")
        self.assertEqual(view.namespace, "PUBLIC_SOURCE")
        self.assertTrue(view.id.startswith("src_"))

    def test_empty_title_rejected(self):
        result = self.fx.service.register_source(
            self.fx.ctx, SourceDraft(title="  ", canonical_key="k1"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_sc_v1_requires_key_or_hash(self):
        """SC-V1：canonical_key 与 content_hash 至少一个非空。"""
        result = self.fx.service.register_source(
            self.fx.ctx, SourceDraft(title="无键来源"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_sc_v2_original_cannot_point_to_canonical(self):
        """SC-V2：ORIGINAL 来源不得再指向别的原始来源。"""
        first = self.fx.source(title="原始访谈")
        result = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="又一份原始", canonical_key="k-orig-2",
            role_in_family=SourceRole.ORIGINAL,
            canonical_family_source_id=first))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_sc_v3_non_original_without_note_rejected(self):
        """SC-V3：非 ORIGINAL 未指明原始来源时必须写 source_note。"""
        result = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="匿名转载", canonical_key="k-reprint",
            role_in_family=SourceRole.REPRINT))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_reprint_inherits_family_and_keeps_own_record(self):
        """转载各自保留记录，只继承族 ID（DATA_MODEL §4.3）。"""
        original = self.fx.source(title="原始访谈", canonical_key="k-orig")
        reprint = unwrap(self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="原始访谈（转载）", canonical_key="k-repr",
            role_in_family=SourceRole.REPRINT,
            canonical_family_source_id=original))).id
        self.assertNotEqual(original, reprint)
        original_view = unwrap(self.fx.service.get_source(self.fx.ctx, original))
        reprint_view = unwrap(self.fx.service.get_source(self.fx.ctx, reprint))
        self.assertEqual(reprint_view.source_family_id, original_view.source_family_id)
        self.assertEqual(reprint_view.role_in_family, "REPRINT")
        self.assertEqual(reprint_view.canonical_family_source_id, original)

    def test_dedupe_by_canonical_key_returns_existing(self):
        first = self.fx.source(title="同一篇", canonical_key="k-same")
        again = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="同一篇（重复登记）", canonical_key="k-same"))
        self.assertTrue(is_ok(again))
        self.assertEqual(again.value.id, first)
        self.assertEqual([w.code for w in again.warnings],
                         [ev.WARNING_SOURCE_DEDUP_HIT])
        page = unwrap(self.fx.service.query_sources(self.fx.ctx, Query()))
        self.assertEqual(page.total, 1)  # 不新建、不删除

    def test_dedupe_by_content_hash(self):
        first = self.fx.source(title="甲", canonical_key="", content_hash="h1")
        again = unwrap(self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="甲（重复）", canonical_key="", content_hash="h1")))
        self.assertEqual(again.id, first)

    def test_same_title_different_key_not_merged(self):
        """不得因文字相似就合并来源（用户目标 7）。"""
        a = self.fx.source(title="同名不同源", canonical_key="k-a")
        b = self.fx.source(title="同名不同源", canonical_key="k-b")
        self.assertNotEqual(a, b)
        page = unwrap(self.fx.service.query_sources(self.fx.ctx, Query()))
        self.assertEqual(page.total, 2)

    def test_cited_from_missing_rejected(self):
        result = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="引用了不存在", canonical_key="k-cite",
            cited_from_source_ids=["src_missing"]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_SOURCE)

    def test_canonical_family_source_missing_rejected(self):
        result = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="找不到原始", canonical_key="k-cf",
            role_in_family=SourceRole.TRANSLATION,
            canonical_family_source_id="src_missing"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_SOURCE)

    def test_subject_entity_missing_rejected(self):
        result = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="主体不存在", canonical_key="k-subj",
            subject_entity_ids=["pe_missing"]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_ENTITY_MISSING)

    def test_unknown_kind_rejected(self):
        result = self.fx.service.register_source(self.fx.ctx, SourceDraft(
            title="怪种类", canonical_key="k-kind", kind="TELEGRAM"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_get_source_not_found(self):
        result = self.fx.service.get_source(self.fx.ctx, "src_" + "0" * 32)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_SOURCE)

    def test_query_sources_filter_and_pagination(self):
        for i in range(3):
            self.fx.source(title=f"新闻{i}", kind=SourceKind.NEWS_REPORT,
                           canonical_key=f"k-news-{i}")
        self.fx.source(title="访谈", canonical_key="k-int")
        page = unwrap(self.fx.service.query_sources(
            self.fx.ctx, Query(filters={"kind": "NEWS_REPORT"})))
        self.assertEqual(page.total, 3)
        page2 = unwrap(self.fx.service.query_sources(
            self.fx.ctx, Query(limit=2, sort=("created_at", "desc"))))
        self.assertEqual(len(page2.items), 2)
        self.assertTrue(page2.has_more)

    def test_query_sources_unknown_filter_rejected(self):
        result = self.fx.service.query_sources(
            self.fx.ctx, Query(filters={"nickname": "x"}))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)

    def test_query_sources_bad_sort_rejected(self):
        result = self.fx.service.query_sources(
            self.fx.ctx, Query(sort=("title", "asc")))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)


# ==========================================================================
# 2. 来源事件与追溯
# ==========================================================================


class TestSourceEvents(FixtureTestCase):
    def test_add_event_and_list(self):
        sid = self.fx.source()
        eid = self.fx.event(sid, title="第 3 集", locator={"timestamp": "00:12:30"})
        events = unwrap(self.fx.service.get_source_events(self.fx.ctx, sid))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].id, eid)
        self.assertEqual(events[0].locator, {"timestamp": "00:12:30"})

    def test_se_v2_locator_required(self):
        sid = self.fx.source()
        result = self.fx.service.add_source_event(self.fx.ctx, SourceEventDraft(
            source_id=sid, title="无定位", locator={}))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_se_v3_work_content_requires_canon_level(self):
        sid = self.fx.source(title="《雾港与星灯》第 3 集",
                             kind=SourceKind.WORK_CONTENT, canonical_key="k-work")
        result = self.fx.service.add_source_event(self.fx.ctx, SourceEventDraft(
            source_id=sid, title="第 3 集", locator={"chapter": "3"}))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)
        ok = self.fx.event(sid, title="第 3 集", locator={"chapter": "3"},
                           canon_level=CanonLevel.UNKNOWN)
        self.assertTrue(ok.startswith("sev_"))

    def test_se_v1_duplicate_of_missing(self):
        sid = self.fx.source()
        result = self.fx.service.add_source_event(self.fx.ctx, SourceEventDraft(
            source_id=sid, title="重复事件", locator={"timestamp": "00:01:00"},
            duplicate_of_event_id="sev_missing"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_extract_events_single_block_degradation(self):
        """确定性降级：单块事件 + EVENT_SINGLE_BLOCK 警告（诚实边界，不假装切分）。"""
        sid = self.fx.source(title="长篇报道", kind=SourceKind.NEWS_REPORT,
                             canonical_key="k-news-long")
        result = self.fx.service.extract_events(self.fx.ctx, sid)
        self.assertTrue(is_ok(result))
        self.assertEqual([w.code for w in result.warnings],
                         [ev.WARNING_EVENT_SINGLE_BLOCK])
        self.assertEqual(len(result.value), 1)
        self.assertEqual(result.value[0].locator, {"chapter": "全文"})

    def test_extract_events_is_idempotent(self):
        sid = self.fx.source(kind=SourceKind.NEWS_REPORT)
        first = unwrap(self.fx.service.extract_events(self.fx.ctx, sid))
        second = self.fx.service.extract_events(self.fx.ctx, sid)
        self.assertTrue(is_ok(second))
        self.assertEqual([e.id for e in first], [e.id for e in second.value])
        self.assertEqual(second.warnings, [])  # 第二次不再建、不再警告

    def test_extract_events_bad_granularity(self):
        sid = self.fx.source()
        result = self.fx.service.extract_events(self.fx.ctx, sid,
                                                min_granularity="sentence")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_GRANULARITY_UNSUPPORTED)
        self.assertEqual(set(GRANULARITIES), {"document", "section", "timestamp"})

    def test_extract_events_source_missing(self):
        result = self.fx.service.extract_events(self.fx.ctx, "src_missing")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_SOURCE)

    def test_evidence_to_event_roundtrip(self):
        """来源追溯往返：证据 → source_event_ids → SourceEvent → locator。"""
        sid = self.fx.source()
        eid = self.fx.event(sid, locator={"timestamp": "00:12:30"})
        evd = self.fx.evidence(sid, source_event_ids=[eid])
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, evd))
        self.assertEqual(view.source_event_ids, [eid])
        events = unwrap(self.fx.service.get_source_events(self.fx.ctx, view.source_id))
        self.assertEqual(events[0].locator, {"timestamp": "00:12:30"})


# ==========================================================================
# 3. 证据创建与查询
# ==========================================================================


class TestEvidenceCreation(FixtureTestCase):
    def test_add_and_get_roundtrip(self):
        sid = self.fx.source()
        evd = self.fx.evidence(sid, claim="他习惯在雨夜出门",
                               verbatim_quote=None,
                               stated_at=FuzzyDate(raw="2021 年春", start="2021-04-01",
                                                   precision=TimePrecision.MONTH))
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, evd))
        self.assertEqual(view.claim, "他习惯在雨夜出门")
        self.assertEqual(view.tier, "PROFILE_BACKGROUND")
        self.assertEqual(view.namespace, "PUBLIC_SOURCE")
        self.assertEqual(view.source_id, sid)
        self.assertEqual(view.subject_entity_ids, [self.fx.hero()])
        self.assertEqual(view.extraction_confidence, 0.9)
        self.assertEqual(view.reliability_snapshot, "UNKNOWN")
        self.assertTrue(view.id.startswith("evd_"))

    def test_view_always_carries_provenance_fields(self):
        """INTERFACES §4.2：namespace / source_id / source_event_ids / conflict_ids
        必然出现——下游无法忽略归属信息。"""
        sid = self.fx.source()
        evd = self.fx.evidence(sid)
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, evd))
        self.assertTrue(view.namespace)
        self.assertIsNotNone(view.source_id)
        self.assertEqual(view.source_event_ids, [])
        self.assertEqual(view.conflict_ids, [])

    def test_reliability_snapshot_copied_from_source(self):
        sid = self.fx.source(reliability="HIGH")
        evd = self.fx.evidence(sid)
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, evd))
        self.assertEqual(view.reliability_snapshot, "HIGH")

    def test_ev_v2_verbatim_requires_quote(self):
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="我说过：雨夜让人清醒", tier=EvidenceTier.VERBATIM,
            dimension=EvidenceDimension.SPEECH_STYLE,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.95,
            source_id=sid))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_TIER_QUOTE_MISSING)

    def test_ev_v4_source_required(self):
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="无来源证据", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=None))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_SOURCE_MISSING)

    def test_source_not_found(self):
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="指向不存在", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id="src_missing"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_SOURCE)

    def test_dangling_event_rejected(self):
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="挂着不存在的事件", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=sid, source_event_ids=["sev_missing"]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_event_from_other_source_rejected(self):
        """事件归属一致性：事件属于别的来源 → 拒收（不静默改挂）。"""
        sid_a = self.fx.source(title="来源甲", canonical_key="k-a")
        sid_b = self.fx.source(title="来源乙", canonical_key="k-b")
        eid = self.fx.event(sid_a)
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="张冠李戴", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=sid_b, source_event_ids=[eid]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_confidence_out_of_range(self):
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="置信度越界", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=1.5,
            source_id=sid))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_subject_missing_rejected(self):
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="主体不存在", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=["pe_missing"], extraction_confidence=0.9,
            source_id=sid))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_ENTITY_MISSING)

    def test_empty_claim_rejected(self):
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="   ", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=sid))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_unknown_tier_and_dimension_rejected(self):
        sid = self.fx.source()

        def _draft(**overrides) -> ev.EvidenceDraft:
            draft = EvidenceDraft(
                claim="枚举表外值", tier=EvidenceTier.PROFILE_BACKGROUND,
                dimension=EvidenceDimension.TRAIT,
                subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
                source_id=sid)
            for key, value in overrides.items():
                setattr(draft, key, value)
            return draft

        for bad in ({"tier": "RUMOR"}, {"dimension": "MOOD"},
                    {"stability": "ETERNAL"}, {"status": "DELETED"},
                    {"canon_level": "OFFICIAL_FANON"}):
            result = self.fx.service.add_evidence(self.fx.ctx, _draft(**bad))
            self.assertTrue(is_err(result), bad)
            self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_get_evidence_not_found(self):
        result = self.fx.service.get_evidence(self.fx.ctx, "evd_" + "0" * 32)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_get_evidence_tier(self):
        sid = self.fx.source()
        evd = self.fx.evidence(sid, tier=EvidenceTier.VERBATIM,
                               verbatim_quote="雨夜让人清醒。")
        tier = unwrap(self.fx.service.get_evidence_tier(self.fx.ctx, evd))
        self.assertIs(tier, EvidenceTier.VERBATIM)

    def test_query_evidence_filters(self):
        sid = self.fx.source()
        self.fx.evidence(sid, claim="说法甲", dimension=EvidenceDimension.TRAIT)
        self.fx.evidence(sid, claim="说法乙",
                         dimension=EvidenceDimension.SPEECH_STYLE)
        page = unwrap(self.fx.service.query_evidence(
            self.fx.ctx, Query(filters={"dimension": "SPEECH_STYLE"})))
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].claim, "说法乙")
        page2 = unwrap(self.fx.service.query_evidence(
            self.fx.ctx, Query(filters={"entity_ids": [self.fx.hero()]})))
        self.assertEqual(page2.total, 2)
        page3 = unwrap(self.fx.service.query_evidence(
            self.fx.ctx, Query(filters={"source_id": sid})))
        self.assertEqual(page3.total, 2)

    def test_query_evidence_unknown_filter_and_bad_sort(self):
        result = self.fx.service.query_evidence(
            self.fx.ctx, Query(filters={"nickname": "x"}))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)
        result2 = self.fx.service.query_evidence(
            self.fx.ctx, Query(sort=("claim", "asc")))
        self.assertTrue(is_err(result2))
        self.assertEqual(result2.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)
        result3 = self.fx.service.query_evidence(self.fx.ctx, Query(limit=201))
        self.assertTrue(is_err(result3))
        self.assertEqual(result3.error.code, ErrorCode.VALIDATION_LIMIT_TOO_LARGE)


# ==========================================================================
# 4. 证据分类（目标 3）
# ==========================================================================


class TestEvidenceTiering(FixtureTestCase):
    def test_tier_priority_excludes_inference(self):
        """优先级只排原始来源证据三类；INFERENCE 不是"第四等的真话"。"""
        self.assertEqual(set(TIER_PRIORITY), {EvidenceTier.VERBATIM,
                                              EvidenceTier.PROFILE_BACKGROUND,
                                              EvidenceTier.THIRD_PARTY})
        self.assertNotIn(EvidenceTier.INFERENCE, TIER_PRIORITY)
        self.assertGreater(TIER_PRIORITY[EvidenceTier.VERBATIM],
                           TIER_PRIORITY[EvidenceTier.PROFILE_BACKGROUND])
        self.assertGreater(TIER_PRIORITY[EvidenceTier.PROFILE_BACKGROUND],
                           TIER_PRIORITY[EvidenceTier.THIRD_PARTY])

    def test_three_scores_are_independent(self):
        """tier / reliability_snapshot / extraction_confidence 互不等价（D-010）：
        一条 THIRD_PARTY 证据可以来自 HIGH 可靠来源、抽取置信度也可以很低。"""
        sid = self.fx.source(reliability="HIGH")
        evd = self.fx.evidence(sid, claim="传闻他左手写字",
                               tier=EvidenceTier.THIRD_PARTY,
                               extraction_confidence=0.3)
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, evd))
        self.assertEqual(view.tier, "THIRD_PARTY")
        self.assertEqual(view.reliability_snapshot, "HIGH")
        self.assertEqual(view.extraction_confidence, 0.3)

    def test_add_evidence_rejects_inference_tier(self):
        """INFERENCE 只能经 record_inference（强制 derived_from）。"""
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="我推测他怕水", tier=EvidenceTier.INFERENCE,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.6,
            source_id=sid))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NO_DERIVATION)

    def test_system_derived_non_inference_rejected(self):
        """EV-V5：非推断证据不得标记 SYSTEM_DERIVED。"""
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="伪装成系统结论", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=sid, namespace=ProvenanceNamespace.SYSTEM_DERIVED))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_TIER_NAMESPACE_MISMATCH)

    def test_record_inference_forces_system_derived(self):
        sid = self.fx.source()
        base = self.fx.evidence(sid, claim="他在三处访谈都提到怕水")
        inference = unwrap(self.fx.service.record_inference(self.fx.ctx, InferenceDraft(
            claim="他很可能怕水", derived_from_evidence_ids=[base],
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], confidence=0.7,
            reasoning_note="三处独立提及")))
        self.assertEqual(inference.tier, "INFERENCE")
        self.assertIsNone(inference.source_id)
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, inference.id))
        self.assertEqual(view.namespace, "SYSTEM_DERIVED")
        page = unwrap(self.fx.service.query_evidence(
            self.fx.ctx, Query(filters={"namespace": "SYSTEM_DERIVED"})))
        self.assertEqual([v.id for v in page.items], [inference.id])

    def test_record_inference_requires_derivation(self):
        """EV-V3：无根推断被拒。"""
        result = self.fx.service.record_inference(self.fx.ctx, InferenceDraft(
            claim="无依据的猜测", derived_from_evidence_ids=[],
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], confidence=0.5))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NO_DERIVATION)

    def test_record_inference_dangling_root(self):
        result = self.fx.service.record_inference(self.fx.ctx, InferenceDraft(
            claim="根不存在", derived_from_evidence_ids=["evd_missing"],
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], confidence=0.5))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_inference_for_fictional_character_declares_variants(self):
        """InferenceDraft 扩展 applicable_variant_ids：虚构角色的推断同样声明版本。"""
        v1 = self.fx.entity("林小满（1994 剧集版）", variant_group_id="vg_star")
        sid = self.fx.source()
        base = self.fx.evidence(sid, subject=v1, applicable_variant_ids=[v1])
        inference = unwrap(self.fx.service.record_inference(self.fx.ctx, InferenceDraft(
            claim="剧集版里她更谨慎", derived_from_evidence_ids=[base],
            dimension=EvidenceDimension.TRAIT, subject_entity_ids=[v1],
            confidence=0.6, applicable_variant_ids=[v1])))
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, inference.id))
        self.assertEqual(view.applicable_variant_ids, [v1])


# ==========================================================================
# 5. 模拟内容隔离（目标 5）
# ==========================================================================


class TestSimulatedIsolation(FixtureTestCase):
    def test_add_evidence_simulated_rejected(self):
        """EV-V1：模块层在任何业务校验之前拒绝（先给一个合法草稿也会被拒）。"""
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="模拟对话里她说她怕水", tier=EvidenceTier.VERBATIM,
            dimension=EvidenceDimension.SPEECH_STYLE,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.99,
            source_id=sid, verbatim_quote="我怕水",
            namespace=ProvenanceNamespace.SIMULATED))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_no_simulated_evidence_anywhere(self):
        """全表扫描：任何路径写进来的证据都不许是 SIMULATED。"""
        sid = self.fx.source()
        self.fx.evidence(sid, claim="真实证据")
        self.fx.service.record_inference(self.fx.ctx, InferenceDraft(
            claim="系统推断", derived_from_evidence_ids=[self.fx.evidence(sid)],
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], confidence=0.5))
        loaded = unwrap(self.fx.storage.query(
            self.fx.ctx, "EvidenceItem", Query(limit=200)))
        namespaces = {row["namespace"] for row in loaded.items}
        self.assertNotIn("SIMULATED", namespaces)
        self.assertTrue(namespaces <= {"PUBLIC_SOURCE", "SYSTEM_DERIVED"})

    def test_storage_gate_is_second_layer(self):
        """D-064 双层闸门：即使绕过模块层，存储结构层也拒绝。"""
        result = self.fx.storage.put(
            self.fx.ctx, "EvidenceItem", {"claim": "硬闯", "tier": "VERBATIM"},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_source_event_simulated_rejected(self):
        sid = self.fx.source()
        result = self.fx.storage.put(
            self.fx.ctx, "SourceEvent",
            {"source_id": sid, "title": "模拟事件", "locator": {"chapter": "全文"}},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_user_provided_evidence_needs_input_record(self):
        """E-2 缺口如实记录：USER_PROVIDED 须经 UserInputRecord（尚未实现），
        当前 add_evidence 明确拒绝而不是静默放行。"""
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="用户口述", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.8,
            source_id=sid, namespace=ProvenanceNamespace.USER_PROVIDED))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)


# ==========================================================================
# 6. 冲突保留（目标 4）
# ==========================================================================


class TestConflictPreservation(FixtureTestCase):
    def _two_conflicting(self) -> tuple[str, str]:
        sid_a = self.fx.source(title="来源甲", canonical_key="k-ca")
        sid_b = self.fx.source(title="来源乙", canonical_key="k-cb")
        e1 = self.fx.evidence(sid_a, claim="他生于 1980 年")
        e2 = self.fx.evidence(sid_b, claim="他生于 1983 年")
        return e1, e2

    def test_record_conflict_keeps_both_claims(self):
        e1, e2 = self._two_conflicting()
        result = self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="出生年份", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1, e2],
            divergence_kind=DivergenceKind.FACTUAL,
            position_summaries={e1: "甲说 1980", e2: "乙说 1983"}))
        self.assertTrue(is_ok(result))
        self.assertEqual(result.value.member_count, 2)
        self.assertEqual([w.code for w in result.warnings],
                         [ev.WARNING_CONFLICT_RECORDED])
        # 双方说法都还在，一条都没丢
        v1 = unwrap(self.fx.service.get_evidence(self.fx.ctx, e1))
        v2 = unwrap(self.fx.service.get_evidence(self.fx.ctx, e2))
        self.assertEqual(v1.claim, "他生于 1980 年")
        self.assertEqual(v2.claim, "他生于 1983 年")

    def test_conflict_policy_and_review_status(self):
        e1, e2 = self._two_conflicting()
        ref = unwrap(self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="出生年份", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1, e2],
            divergence_kind=DivergenceKind.FACTUAL,
            position_summaries={e1: "甲说 1980", e2: "乙说 1983"})))
        row = unwrap(self.fx.storage.get(self.fx.ctx, "ConflictRecord", ref.id))
        self.assertEqual(row["resolution_policy"], "KEEP_ALL_UNASSESSED")
        self.assertEqual(row["review_status"], "UNREVIEWED")
        self.assertEqual(row["namespace"], "SYSTEM_DERIVED")
        self.assertEqual(len(row["members"]), 2)
        self.assertEqual(row["members"][0]["position_summary"], "甲说 1980")
        # 枚举里故意没有 RESOLVED（D-013）
        self.assertNotIn("RESOLVED", [r.value for r in ReviewStatus])

    def test_conflict_marks_disputed_and_backwrites_ids(self):
        e1, e2 = self._two_conflicting()
        ref = unwrap(self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="出生年份", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1, e2],
            divergence_kind=DivergenceKind.FACTUAL)))
        for eid in (e1, e2):
            view = unwrap(self.fx.service.get_evidence(self.fx.ctx, eid))
            self.assertEqual(view.status, "DISPUTED")
            self.assertEqual(view.conflict_ids, [ref.id])
        page = unwrap(self.fx.service.query_evidence(
            self.fx.ctx, Query(filters={"status": "DISPUTED"})))
        self.assertEqual(page.total, 2)

    def test_conflict_does_not_block_subsequent_writes(self):
        """冲突不是错误：记录之后照常写新证据（D-055）。"""
        e1, e2 = self._two_conflicting()
        self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="出生年份", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1, e2],
            divergence_kind=DivergenceKind.FACTUAL))
        sid = self.fx.source(title="来源丙", canonical_key="k-cc")
        e3 = self.fx.evidence(sid, claim="他怕水")
        view = unwrap(self.fx.service.get_evidence(self.fx.ctx, e3))
        self.assertEqual(view.status, "ACTIVE")
        self.assertEqual(view.conflict_ids, [])

    def test_conflict_single_member_rejected(self):
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="只有一条")
        result = self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="凑不齐冲突", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1], divergence_kind=DivergenceKind.FACTUAL))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS)

    def test_conflict_member_dedup_warning(self):
        e1, e2 = self._two_conflicting()
        result = self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="成员重复", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1, e2, e1],
            divergence_kind=DivergenceKind.FACTUAL))
        self.assertTrue(is_ok(result))
        self.assertEqual(result.value.member_count, 2)
        self.assertIn(ev.WARNING_CONFLICT_MEMBER_DEDUPED,
                      [w.code for w in result.warnings])

    def test_conflict_missing_member_rejected(self):
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="真成员")
        result = self.fx.service.record_conflict(self.fx.ctx, ConflictDraft(
            topic="假成员", subject_entity_ids=[self.fx.hero()],
            member_evidence_ids=[e1, "evd_missing"],
            divergence_kind=DivergenceKind.FACTUAL))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_detect_conflicts_canon_vs_non_canon(self):
        """结构检测：同一说法跨正典状态 → VERSION_DIFFERENCE，只记录不裁决。"""
        sid_a = self.fx.source(title="官方设定集", kind=SourceKind.OFFICIAL_MATERIAL,
                               canonical_key="k-official")
        sid_b = self.fx.source(title="同人解读站", kind=SourceKind.FAN_WIKI,
                               canonical_key="k-fan")
        e1 = self.fx.evidence(sid_a, claim="她是灯塔守护者的后裔",
                              canon_level=CanonLevel.CANON,
                              dimension=EvidenceDimension.CANON_FACT)
        e2 = self.fx.evidence(sid_b, claim="她是灯塔守护者的后裔",
                              canon_level=CanonLevel.NON_CANON,
                              dimension=EvidenceDimension.CANON_FACT)
        refs = unwrap(self.fx.service.detect_conflicts(self.fx.ctx))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].divergence_kind, "VERSION_DIFFERENCE")
        self.assertEqual(refs[0].member_count, 2)
        # 幂等：再跑一次不会建第二条冲突
        refs2 = unwrap(self.fx.service.detect_conflicts(self.fx.ctx))
        self.assertEqual(len(refs2), 1)
        conflicts = unwrap(self.fx.storage.query(
            self.fx.ctx, "ConflictRecord", Query(limit=200)))
        self.assertEqual(conflicts.total, 1)

    def test_detect_conflicts_mutually_exclusive_variants(self):
        """结构检测：同一说法适用于互斥改编版本 → VERSION_DIFFERENCE。"""
        hero = self.fx.hero()
        v1 = self.fx.entity("林小满（1994 剧集版）", variant_group_id="vg_star")
        v2 = self.fx.entity("林小满（2011 电影版）", variant_group_id="vg_star")
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="她在终章牺牲", subject=hero,
                              applicable_variant_ids=[v1])
        e2 = self.fx.evidence(sid, claim="她在终章牺牲", subject=hero,
                              applicable_variant_ids=[v2])
        refs = unwrap(self.fx.service.detect_conflicts(self.fx.ctx))
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].divergence_kind, "VERSION_DIFFERENCE")
        self.assertEqual(refs[0].member_count, 2)

    def test_detect_conflicts_empty_is_ok_not_error(self):
        """没有冲突返回空列表——不是错误（D-055）。"""
        sid = self.fx.source()
        self.fx.evidence(sid, claim="平淡无奇的一条")
        refs = unwrap(self.fx.service.detect_conflicts(self.fx.ctx))
        self.assertEqual(refs, [])


# ==========================================================================
# 7. 重复记录处理（目标 7）
# ==========================================================================


class TestDuplicateHandling(FixtureTestCase):
    def test_evidence_dupes_merge_with_strict_conditions(self):
        """五元组全同（含空白变体）才并组；保留全部来源引用。"""
        sid_a = self.fx.source(title="来源甲", canonical_key="k-da")
        sid_b = self.fx.source(title="来源乙", canonical_key="k-db")
        e1 = self.fx.evidence(sid_a, claim="他  喜欢  下雨天")
        e2 = self.fx.evidence(sid_b, claim="他 喜欢 下雨天")
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(len(refs), 1)
        group = refs[0]
        self.assertEqual(group.merge_method, "exact_claim_whitespace_normalized")
        self.assertEqual(sorted(group.member_evidence_ids), sorted([e1, e2]))
        self.assertEqual(sorted(group.member_source_ids), sorted([sid_a, sid_b]))
        self.assertIn(group.id, [e1, e2])  # 组 ID = 规范成员指针，零新前缀
        for eid in (e1, e2):
            view = unwrap(self.fx.service.get_evidence(self.fx.ctx, eid))
            self.assertEqual(view.dedupe_group_id, group.id)

    def test_dedupe_group_id_is_earliest_member(self):
        """组 ID = 最早成员（跨秒可判定；同秒内退化按 id，见 RUNNING §5.4 E-4）。"""
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="同一说法")
        time.sleep(1.1)  # created_at 只有秒级精度，错开保证可判定
        e2 = self.fx.evidence(sid, claim="同一说法")
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs[0].id, e1)

    def test_dedupe_is_idempotent(self):
        sid_a = self.fx.source(title="甲", canonical_key="k-ia")
        sid_b = self.fx.source(title="乙", canonical_key="k-ib")
        self.fx.evidence(sid_a, claim="重复说法")
        self.fx.evidence(sid_b, claim="重复说法")
        first = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        before = unwrap(self.fx.service.get_evidence(
            self.fx.ctx, first[0].member_evidence_ids[0]))
        second = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual([r.id for r in first], [r.id for r in second])
        after = unwrap(self.fx.service.get_evidence(
            self.fx.ctx, first[0].member_evidence_ids[0]))
        self.assertEqual(before.created_at, after.created_at)  # 无重复写入

    def test_different_subject_not_merged(self):
        other = self.fx.entity("沈默")
        sid = self.fx.source()
        self.fx.evidence(sid, claim="同一说法")
        self.fx.evidence(sid, claim="同一说法", subject=other)
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])

    def test_different_dimension_not_merged(self):
        sid = self.fx.source()
        self.fx.evidence(sid, claim="同一说法", dimension=EvidenceDimension.TRAIT)
        self.fx.evidence(sid, claim="同一说法",
                         dimension=EvidenceDimension.HABIT)
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])

    def test_different_canon_level_not_merged(self):
        sid = self.fx.source()
        self.fx.evidence(sid, claim="同一说法", canon_level=CanonLevel.CANON,
                         dimension=EvidenceDimension.CANON_FACT)
        self.fx.evidence(sid, claim="同一说法", canon_level=CanonLevel.NON_CANON,
                         dimension=EvidenceDimension.CANON_FACT)
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])

    def test_different_variant_not_merged(self):
        v1 = self.fx.entity("林小满（1994 剧集版）", variant_group_id="vg_star")
        v2 = self.fx.entity("林小满（2011 电影版）", variant_group_id="vg_star")
        sid = self.fx.source()
        self.fx.evidence(sid, claim="同一说法", subject=v1,
                         applicable_variant_ids=[v1])
        self.fx.evidence(sid, claim="同一说法", subject=v2,
                         applicable_variant_ids=[v2])
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])

    def test_semantically_similar_text_not_merged(self):
        """不因文字相似就合并：语义压缩需要模型，本阶段不做、不假装。"""
        sid = self.fx.source()
        self.fx.evidence(sid, claim="他喜欢下雨天")
        self.fx.evidence(sid, claim="他喜爱下雨天")
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])


# ==========================================================================
# 8. 虚构版本隔离（目标 6）
# ==========================================================================


class TestFictionalBoundary(FixtureTestCase):
    def test_cross_variant_group_rejected(self):
        """EV-V7：applicable_variant_ids 跨 variant_group 混指 → 拒收。"""
        v1 = self.fx.entity("林小满（剧集版）", variant_group_id="vg_star")
        w1 = self.fx.entity("沈默（剧集版）", variant_group_id="vg_other")
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="把两个角色的版本混在一起", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=sid, applicable_variant_ids=[v1, w1]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_variant_missing_rejected(self):
        sid = self.fx.source()
        result = self.fx.service.add_evidence(self.fx.ctx, EvidenceDraft(
            claim="指向不存在的版本", tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], extraction_confidence=0.9,
            source_id=sid, applicable_variant_ids=["pe_missing"]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_ENTITY_MISSING)

    def test_same_group_variants_coexist_without_merging(self):
        """同组不同版本的证据各自保留、互不并组（版本边界的最低保证）。"""
        v1 = self.fx.entity("林小满（1994 剧集版）", variant_group_id="vg_star")
        v2 = self.fx.entity("林小满（2011 电影版）", variant_group_id="vg_star")
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="她在终章牺牲", subject=v1,
                              applicable_variant_ids=[v1])
        e2 = self.fx.evidence(sid, claim="她在终章活着", subject=v2,
                              applicable_variant_ids=[v2])
        self.assertNotEqual(e1, e2)
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])


# ==========================================================================
# 9. 稳定性闸门
# ==========================================================================


class TestStability(FixtureTestCase):
    def test_single(self):
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="只出现一次的说法")
        verdict = unwrap(self.fx.service.judge_stability(
            self.fx.ctx, self.fx.hero(), "只出现一次的说法", [e1]))
        self.assertEqual(verdict.stability, Stability.SINGLE)
        self.assertEqual(verdict.occurrences, 1)
        self.assertEqual(verdict.distinct_sources, 1)
        self.assertIsNone(verdict.time_span)

    def test_repeated_two_same_source(self):
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="重复说法")
        e2 = self.fx.evidence(sid, claim="重复说法")
        verdict = unwrap(self.fx.service.judge_stability(
            self.fx.ctx, self.fx.hero(), "重复说法", [e1, e2]))
        self.assertEqual(verdict.stability, Stability.REPEATED)
        self.assertEqual(verdict.distinct_sources, 1)

    def test_pattern_three_distinct_sources(self):
        eids = []
        for i in range(3):
            sid = self.fx.source(title=f"来源{i}", canonical_key=f"k-st-{i}")
            eids.append(self.fx.evidence(sid, claim="三个来源都提到"))
        verdict = unwrap(self.fx.service.judge_stability(
            self.fx.ctx, self.fx.hero(), "三个来源都提到", eids))
        self.assertEqual(verdict.stability, Stability.PATTERN)
        self.assertEqual(verdict.distinct_sources, 3)

    def test_time_span_from_stated_at(self):
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="跨时间的说法",
                              stated_at=FuzzyDate(raw="2020", start="2020-01-01",
                                                  precision=TimePrecision.YEAR))
        e2 = self.fx.evidence(sid, claim="跨时间的说法",
                              stated_at=FuzzyDate(raw="2023", start="2023-06-01",
                                                  precision=TimePrecision.DAY))
        verdict = unwrap(self.fx.service.judge_stability(
            self.fx.ctx, self.fx.hero(), "跨时间的说法", [e1, e2]))
        self.assertEqual(verdict.time_span, ("2020-01-01", "2023-06-01"))

    def test_empty_evidence_rejected(self):
        result = self.fx.service.judge_stability(
            self.fx.ctx, self.fx.hero(), "空列表", [])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_EMPTY_EVIDENCE)

    def test_missing_evidence_rejected(self):
        result = self.fx.service.judge_stability(
            self.fx.ctx, self.fx.hero(), "假证据", ["evd_missing"])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_judge_stability_is_read_only(self):
        """只读闸门：判定过程不改证据（rev / payload 原样）。"""
        sid = self.fx.source()
        e1 = self.fx.evidence(sid, claim="只读判定")
        before = unwrap(self.fx.storage.get(self.fx.ctx, "EvidenceItem", e1))
        self.fx.service.judge_stability(self.fx.ctx, self.fx.hero(), "只读判定", [e1])
        after = unwrap(self.fx.storage.get(self.fx.ctx, "EvidenceItem", e1))
        self.assertEqual(before["payload"], after["payload"])
        self.assertEqual(before["rev"], after["rev"])


# ==========================================================================
# 10. 重启持久化
# ==========================================================================


class TestPersistenceAcrossRestart(FixtureTestCase):
    def test_survives_new_storage_instance(self):
        sid = self.fx.source(title="持久来源", canonical_key="k-persist")
        eid = self.fx.event(sid)
        evd = self.fx.evidence(sid, claim="持久证据", source_event_ids=[eid])
        self.fx.service.record_inference(self.fx.ctx, InferenceDraft(
            claim="持久推断", derived_from_evidence_ids=[evd],
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[self.fx.hero()], confidence=0.6))
        refs = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))
        self.assertEqual(refs, [])  # 只有一条，无组
        self.fx.storage.close()

        # 模拟进程重启：全新存储实例 + 全新服务实例
        db_path = Path(self.fx.storage.path)
        storage2 = SqliteStorage(db_path)
        service2 = EvidenceService(storage2)
        ctx2 = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(storage2.init_schema(ctx2))
        try:
            source = unwrap(service2.get_source(ctx2, sid))
            self.assertEqual(source.title, "持久来源")
            view = unwrap(service2.get_evidence(ctx2, evd))
            self.assertEqual(view.claim, "持久证据")
            self.assertEqual(view.source_event_ids, [eid])
            self.assertEqual(view.dimension, "TRAIT")
            events = unwrap(service2.get_source_events(ctx2, sid))
            self.assertEqual(len(events), 1)
            inference = unwrap(service2.query_evidence(
                ctx2, Query(filters={"namespace": "SYSTEM_DERIVED"})))
            self.assertEqual(inference.total, 1)
        finally:
            storage2.close()

    def test_dedupe_group_survives_restart(self):
        sid_a = self.fx.source(title="甲", canonical_key="k-ra")
        sid_b = self.fx.source(title="乙", canonical_key="k-rb")
        e1 = self.fx.evidence(sid_a, claim="重启后还在组里")
        e2 = self.fx.evidence(sid_b, claim="重启后还在组里")
        group_id = unwrap(self.fx.service.detect_duplicates(self.fx.ctx))[0].id
        self.fx.storage.close()

        storage2 = SqliteStorage(Path(self.fx.storage.path))
        service2 = EvidenceService(storage2)
        ctx2 = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(storage2.init_schema(ctx2))
        try:
            view = unwrap(service2.get_evidence(ctx2, e2))
            self.assertEqual(view.dedupe_group_id, group_id)
            self.assertIn(group_id, (e1, e2))
        finally:
            storage2.close()


# ==========================================================================
# 11. 模块边界（目标 9 / F-2）
# ==========================================================================


class TestModuleBoundaries(FixtureTestCase):
    def test_service_has_no_research_methods(self):
        """F-2：Evidence 不自行启动研究流程——连方法都不存在。"""
        for name in ("search", "collect", "query_plan", "fetch", "crawl"):
            self.assertFalse(hasattr(self.fx.service, name), name)

    def test_service_depends_only_on_storage(self):
        """不绕过现有模块接口：构造只收 Storage，不持 Provider。"""
        self.assertEqual(EvidenceService.__init__.__annotations__.get("storage"),
                         "SqliteStorage")
        self.assertFalse(hasattr(self.fx.service, "provider"))

    def test_reader_protocol_has_no_write_methods(self):
        """EvidenceReader 只有读方法——协议里根本没有写入口。"""
        write_methods = {"register_source", "add_source_event", "extract_events",
                         "add_evidence", "record_inference", "detect_duplicates",
                         "detect_conflicts", "record_conflict"}
        self.assertTrue(write_methods.isdisjoint(set(dir(ev.EvidenceReader))))
        self.assertIn("query_evidence", dir(ev.EvidenceReader))
        self.assertIn("get_evidence_tier", dir(ev.EvidenceReader))

    def test_service_satisfies_reader_protocol(self):
        """服务实例可当只读端口用（结构检查）。"""
        self.assertTrue(hasattr(self.fx.service, "query_evidence"))
        self.assertTrue(hasattr(self.fx.service, "get_evidence_tier"))


if __name__ == "__main__":
    unittest.main()
