"""Relationship 模块测试（P3 第二步：人物关系档案基础；用户称之为 P3.3）。

全部跑在**临时目录**的 SQLite 文件上，不碰任何正式项目库、不调外部服务。

覆盖（对应用户验收清单第 9 条 + 模块诚实边界）：
  1. 创建与查询（v1、参与者集合匹配、ID 查找、过滤/排序/分页、历史版本可见）
  2. 双向视角差异（A→B / B→A 独立存储、不合并、RL-V1 键集合、目标校验）
  3. 共同经历（带时间、逐条目参与者子集、RL-V4 证据）
  4. 时间变化（temporal_changes + occurred_at 往返、diff 可见）
  5. 冲突保留（关联不裁决、双方证据都在、CF-V5 双向一致、不阻断写入）
  6. 版本变化（revise 新版本、旧版归档不覆盖、diff、空转拒绝）
  7. 多人物群体边界（≥3 必填 group_dynamics、群体不拆两人、逐条目参与者）
  8. 虚构角色版本边界（版本声明、同角色双版本反合并、证据版本不串用）
  9. 模拟内容隔离（模块层 + 存储层双层闸门；mem_/ctn_/pnf_ 引用拒绝）
 10. 证据追溯（条目 → evidence_refs → EvidenceItem → source 往返；RL-V6）
 11. 模式闸门（单次互动不进 interaction_patterns）
 12. 非法输入（各校验规则的失败码）
 13. 重启持久化（新存储实例读回全部版本）
 14. 模块边界（F-2/F-5：不启动研究、不依赖 Provider）

对应文档：INTERFACES §7、DATA_MODEL §4.8、ERROR_HANDLING §2.2、
DECISIONS §2.18（D-121~D-128）。
"""

import pathlib
import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    Cardinality,
    DivergenceKind,
    EvidenceDimension,
    EvidenceStatus,
    EvidenceTier,
    ProfileStatus,
    ProvenanceNamespace,
    Stability,
)
from persona.core.errors import ErrorCode
from persona.core.ids import new_token
from persona.core.result import is_err, is_ok, unwrap
from persona.core.temporal import FuzzyDate, TimePrecision
from persona.core.types import Query
from persona.modules.evidence import (
    ConflictDraft,
    EvidenceDraft,
    EvidenceService,
    SourceDraft,
)
from persona.modules.persona import ProfileItem
from persona.modules.relationship import (
    SECTION_INTERACTION_PATTERNS,
    SECTION_SHARED_EXPERIENCES,
    SECTION_TEMPORAL_CHANGES,
    GroupDynamics,
    PerspectiveBlock,
    RelationshipDraft,
    RelationshipService,
    SharedExperienceItem,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_relationship"


class RelationshipFixture:
    """一个临时数据库 + 证据服务 + 关系档案服务 + 调用上下文。用完即关。"""

    def __init__(self, tmp: str) -> None:
        self.storage = SqliteStorage(Path(tmp) / "relationship.db")
        self.service = RelationshipService(self.storage)
        self.evidence_service = EvidenceService(self.storage)
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))

    # ---------------------------------------------------------- 基础构件

    def entity(self, name: str = "林小满", *,
               entity_type: str = "FICTIONAL_CHARACTER",
               variant_group_id: str | None = None) -> str:
        payload: dict = {
            "display_name": name,
            "entity_type": entity_type,
            "merge_policy": "NEVER_AUTO_MERGE",
            "status": "OK",
        }
        if entity_type == "FICTIONAL_CHARACTER":
            payload["fictional_character_meta"] = {
                "works": [{"work_id": new_token("wrk_"),
                           "title": "《雾港与星灯》",
                           "medium": "ANIMATION"}],
                "adaptation_version": "2020 动画改编版",
                "adaptation_lineage": ["原作轻小说版", "2020 动画改编版"],
                "official_canon_level": "CANON",
            }
        else:
            payload["real_person_meta"] = {
                "public_identity_tags": ["某领域的公开评论者"],
                "privacy_handling": "PUBLIC_FIGURE_STANDARD",
            }
        if variant_group_id:
            payload["variant_group_id"] = variant_group_id
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity", payload,
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def pair(self) -> tuple[str, str]:
        """两个**不同作品角色**的虚构对象（各自一个 variant_group）。"""
        return (self.entity("林小满", variant_group_id=new_token("vg_")),
                self.entity("陈雾", variant_group_id=new_token("vg_")))

    def real_pair(self) -> tuple[str, str]:
        return (self.entity("某公开评论者甲", entity_type="REAL_PERSON"),
                self.entity("某公开评论者乙", entity_type="REAL_PERSON"))

    def trio(self) -> tuple[str, str, str]:
        return (self.entity("林小满", variant_group_id=new_token("vg_")),
                self.entity("陈雾", variant_group_id=new_token("vg_")),
                self.entity("赵灯", variant_group_id=new_token("vg_")))

    def two_versions(self) -> tuple[str, str]:
        """同一角色的两个改编版本（同一 variant_group）。"""
        group = new_token("vg_")
        return (self.entity("林小满（轻小说版）", variant_group_id=group),
                self.entity("林小满（2020 动画版）", variant_group_id=group))

    def source(self, title: str = "《雾港与星灯》访谈录") -> str:
        draft = SourceDraft(title=title, canonical_key=f"key_{new_token()}")
        return unwrap(self.evidence_service.register_source(self.ctx, draft)).id

    def evidence(self, claim: str, subjects: list[str], **kwargs) -> str:
        draft = EvidenceDraft(
            claim=claim,
            tier=kwargs.pop("tier", EvidenceTier.PROFILE_BACKGROUND),
            dimension=kwargs.pop("dimension", EvidenceDimension.RELATION),
            subject_entity_ids=subjects,
            extraction_confidence=kwargs.pop("extraction_confidence", 0.9),
            source_id=kwargs.pop("source_id", None) or self.source(),
            **kwargs,
        )
        return unwrap(self.evidence_service.add_evidence(self.ctx, draft)).id

    # ---------------------------------------------------------- 常用组合

    def item(self, item_id: str, text: str, refs: list[str], **kwargs) -> ProfileItem:
        return ProfileItem(item_id=item_id, text=text, confidence=0.8,
                           evidence_refs=refs, **kwargs)

    def shared(self, item_id: str, text: str, refs: list[str], *,
               occurred_at: FuzzyDate | None = None, **kwargs) -> SharedExperienceItem:
        return SharedExperienceItem(
            item_id=item_id, text=text, confidence=0.8, evidence_refs=refs,
            occurred_at=occurred_at, **kwargs)

    def pair_draft(self, a: str, b: str, **kwargs) -> RelationshipDraft:
        """一份最小的合法两人关系档案（双方视角 + 一条共同经历）。"""
        ref_ab = self.evidence("访谈录里 A 多次提到 B 靠得住。", [a, b])
        ref_ba = self.evidence("访谈录里 B 提到 A 帮过他。", [a, b])
        ref_shared = self.evidence("两人曾在灯节一起修灯塔。", [a, b])
        perspectives = {
            a: PerspectiveBlock(
                entity_id=a,
                view_of_targets={b: [self.item("v_ab", "A 觉得 B 靠得住",
                                               [ref_ab],
                                               stability=Stability.REPEATED)]},
                feeling_toward=[self.item("f_ab", "对 B 有信任感", [ref_ab])],
                uncertainty_note="A 的内心想法只有访谈转述，待补"),
            b: PerspectiveBlock(
                entity_id=b,
                view_of_targets={a: [self.item("v_ba", "B 觉得 A 肯帮忙",
                                               [ref_ba],
                                               stability=Stability.REPEATED)]},
                expression_toward=[self.item("e_ba", "对 A 说话直接", [ref_ba])]),
        }
        return RelationshipDraft(
            participant_entity_ids=[a, b],
            cardinality=Cardinality.PAIR,
            perspectives=perspectives,
            shared_experiences=[self.shared(
                "s_1", "两人在灯节一起修灯塔", [ref_shared],
                occurred_at=FuzzyDate(raw="2020 年灯节", start="2020-02-01",
                                      end="2020-03-01",
                                      precision=TimePrecision.RANGE))],
            applicable_variant_ids=[a, b],
            **kwargs,
        )

    def group_draft(self, a: str, b: str, c: str,
                    **kwargs) -> RelationshipDraft:
        """一份最小的合法三人关系档案（含 group_dynamics）。"""
        ref = self.evidence("三人曾一起守夜班。", [a, b, c])
        perspectives: dict[str, PerspectiveBlock] = {}
        for p in (a, b, c):
            others = [q for q in (a, b, c) if q != p]
            perspectives[p] = PerspectiveBlock(
                entity_id=p,
                view_of_targets={
                    q: [self.item(f"v_{p}_{q}", f"{p} 与 {q} 熟络", [ref],
                                  stability=Stability.REPEATED)]
                    for q in others})
        return RelationshipDraft(
            participant_entity_ids=[a, b, c],
            cardinality=Cardinality.GROUP,
            perspectives=perspectives,
            shared_experiences=[self.shared("s_1", "三人一起守夜班", [ref],
                                            occurred_at=None)],
            group_dynamics=GroupDynamics(
                alliances=[self.item("g_1", "A 与 B 常一起行动", [ref])],
                note="C 与两人关系待补"),
            applicable_variant_ids=[a, b, c],
            **kwargs,
        )


def _fixture() -> RelationshipFixture:
    tmp = tempfile.mkdtemp(prefix="rlp_test_")
    return RelationshipFixture(tmp)


class FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()

    def tearDown(self) -> None:
        self.fx.storage.close()
        shutil.rmtree(Path(self.fx.storage.path).parent, ignore_errors=True)


# --------------------------------------------------------------------------
# 1. 创建与查询
# --------------------------------------------------------------------------


class TestRelationshipCreation(FixtureTestCase):
    def test_create_and_read_back(self):
        fx = self.fx
        a, b = fx.pair()
        ref = unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        self.assertTrue(ref.id.startswith("rlp_"))
        self.assertEqual(ref.version, 1)
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        self.assertEqual(view.cardinality, "PAIR")
        self.assertEqual(view.namespace, "SYSTEM_DERIVED")
        self.assertEqual(set(view.perspectives), {a, b})
        self.assertEqual(len(view.shared_experiences), 1)

    def test_participant_lookup_is_order_insensitive(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        view = unwrap(fx.service.get_relationship(fx.ctx, [b, a]))
        self.assertEqual(set(view.participant_entity_ids), {a, b})

    def test_lookup_by_relationship_id(self):
        fx = self.fx
        a, b = fx.pair()
        ref = unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        view = unwrap(fx.service.get_relationship(fx.ctx, ref.id))
        self.assertEqual(view.id, ref.id)

    def test_duplicate_create_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        result = fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE)

    def test_different_pairs_are_independent(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        ref2 = unwrap(fx.service.create_relationship(
            fx.ctx, fx.pair_draft(a, c)))
        self.assertNotEqual(
            unwrap(fx.service.get_relationship(fx.ctx, [a, b])).id,
            ref2.id)

    def test_query_with_filters(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        page = unwrap(fx.service.query_relationships(
            fx.ctx, Query(filters={"cardinality": "PAIR"}, limit=50)))
        self.assertEqual(page.total, 1)
        page = unwrap(fx.service.query_relationships(
            fx.ctx, Query(filters={"cardinality": "GROUP"}, limit=50)))
        self.assertEqual(page.total, 0)

    def test_query_participant_filter_is_intersection(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, c)))
        page = unwrap(fx.service.query_relationships(
            fx.ctx, Query(filters={"participant_entity_ids": [a]}, limit=50)))
        self.assertEqual(page.total, 2)

    def test_query_sort_and_pagination(self):
        fx = self.fx
        a, b = fx.pair()
        ref = unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        unwrap(fx.service.revise(fx.ctx, [a, b], fx.pair_draft(
            a, b, change_reason="补充一条共同经历")))
        page = unwrap(fx.service.query_relationships(
            fx.ctx, Query(sort=("version", "desc"), limit=1)))
        self.assertEqual(page.total, 2)
        self.assertEqual(page.items[0].version, 2)
        self.assertTrue(page.has_more)
        first = unwrap(fx.service.query_relationships(
            fx.ctx, Query(sort=("version", "asc"), limit=1)))
        self.assertEqual(first.items[0].id, ref.id)

    def test_query_unknown_filter_rejected(self):
        fx = self.fx
        result = fx.service.query_relationships(
            fx.ctx, Query(filters={"intimacy": 3}, limit=10))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)

    def test_query_limit_too_large_rejected(self):
        fx = self.fx
        result = fx.service.query_relationships(fx.ctx, Query(limit=10_000))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_LIMIT_TOO_LARGE)

    def test_query_unknown_sort_rejected(self):
        fx = self.fx
        result = fx.service.query_relationships(
            fx.ctx, Query(sort=("intimacy", "asc")))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)

    def test_get_missing_relationship(self):
        fx = self.fx
        result = fx.service.get_relationship(fx.ctx, ["pe_missing", "pe_gone"])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_get_wrong_version(self):
        fx = self.fx
        a, b = fx.pair()
        ref = unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        result = fx.service.get_relationship(fx.ctx, ref.id, version=7)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_VERSION)

    def test_get_by_non_relationship_id_rejected(self):
        fx = self.fx
        result = fx.service.get_relationship(fx.ctx, "evd_notrelationship")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)


# --------------------------------------------------------------------------
# 2. 双向视角差异
# --------------------------------------------------------------------------


class TestBidirectionalPerspectives(FixtureTestCase):
    def test_both_directions_stored_independently(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        # A 看 B 与 B 看 A 各占一个格子，内容不同、不合并
        self.assertEqual(view.perspectives[a].view_of_targets[b][0].text,
                         "A 觉得 B 靠得住")
        self.assertEqual(view.perspectives[b].view_of_targets[a][0].text,
                         "B 觉得 A 肯帮忙")
        # 没有任何"综合定论"字段（RL-V5）：视图形状里不存在合成结论
        self.assertNotIn("synthesis", vars(view))

    def test_asymmetry_is_visible(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("A 自述不敢麻烦 B。", [a, b])
        draft.asymmetry_notes = [
            fx.item("asym_1", "A 自述不敢麻烦 B，与 B 的感受不一致", [ref])]
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        self.assertEqual(len(view.asymmetry_notes), 1)
        self.assertEqual(view.asymmetry_notes[0].item_id, "asym_1")

    def test_perspective_uncertainty_note_preserved(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        self.assertEqual(view.perspectives[a].uncertainty_note,
                         "A 的内心想法只有访谈转述，待补")

    def test_rl_v1_missing_perspective_key_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        del draft.perspectives[b]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)
        self.assertIn("RL-V1", result.error.message)

    def test_rl_v1_extra_perspective_key_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        draft = fx.pair_draft(a, b)
        draft.perspectives[c] = PerspectiveBlock(entity_id=c)
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V1", result.error.message)

    def test_perspective_block_entity_mismatch_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.perspectives[a].entity_id = b
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V1", result.error.message)

    def test_view_of_non_participant_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("A 评价过 C。", [a, c])
        draft.perspectives[a].view_of_targets[c] = [
            fx.item("v_ac", "A 评价 C 客气", [ref],
                    stability=Stability.REPEATED)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_view_of_self_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("A 自述。", [a, b])
        draft.perspectives[a].view_of_targets[a] = [
            fx.item("v_aa", "A 看自己", [ref], stability=Stability.REPEATED)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)


# --------------------------------------------------------------------------
# 3. 共同经历
# --------------------------------------------------------------------------


class TestSharedExperiences(FixtureTestCase):
    def test_shared_experience_with_time_roundtrip(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        item = view.shared_experiences[0]
        self.assertEqual(item.text, "两人在灯节一起修灯塔")
        self.assertEqual(item.occurred_at.raw, "2020 年灯节")
        self.assertEqual(item.occurred_at.start, "2020-02-01")
        self.assertEqual(str(item.occurred_at.precision), "RANGE")

    def test_work_internal_time_roundtrip(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("动画第 4 集两人重逢。", [a, b])
        draft.shared_experiences.append(fx.shared(
            "s_2", "第 4 集重逢", [ref],
            occurred_at=FuzzyDate(raw="第 4 集", precision=TimePrecision.WORK_INTERNAL),
            work_internal_time="第 4 集之后"))
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        self.assertEqual(view.shared_experiences[1].work_internal_time,
                         "第 4 集之后")

    def test_per_item_participants_subset_ok(self):
        fx = self.fx
        a, b, c = fx.trio()
        draft = fx.group_draft(a, b, c)
        ref = fx.evidence("A 与 B 单独出过一趟海。", [a, b])
        draft.shared_experiences.append(fx.shared(
            "s_2", "A 与 B 单独出海", [ref], occurred_at=None,
            participant_entity_ids=[a, b]))
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b, c]))
        self.assertEqual(view.shared_experiences[1].participant_entity_ids, [a, b])

    def test_per_item_participants_non_participant_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        draft = fx.pair_draft(a, b)
        draft.shared_experiences[0].participant_entity_ids = [a, c]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_shared_experience_without_evidence_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.shared_experiences.append(fx.shared("s_2", "无证据的共同经历", []))
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT)
        self.assertIn("RL-V4", result.error.message)


# --------------------------------------------------------------------------
# 4. 时间变化
# --------------------------------------------------------------------------


class TestTemporalChanges(FixtureTestCase):
    def test_temporal_change_roundtrip_and_diff(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        ref = fx.evidence("后期访谈里两人已成莫逆。", [a, b])
        draft2 = fx.pair_draft(a, b)
        draft2.temporal_changes = [
            fx.item("t_1", "后期从合作者变成莫逆", [ref],
                    stability=Stability.PATTERN)]
        draft2.temporal_changes[0] = SharedExperienceItem(
            item_id="t_1", text="后期从合作者变成莫逆", confidence=0.8,
            evidence_refs=[ref], stability=Stability.PATTERN,
            occurred_at=FuzzyDate(raw="2021 年", start="2021-01-01",
                                  end="2021-12-31",
                                  precision=TimePrecision.RANGE))
        draft2.change_reason = "补充关系时间线"
        ref2 = unwrap(fx.service.revise(fx.ctx, [a, b], draft2))
        self.assertEqual(ref2.version, 2)
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b], version=2))
        self.assertEqual(len(view.temporal_changes), 1)
        self.assertEqual(view.temporal_changes[0].occurred_at.start, "2021-01-01")
        diff = unwrap(fx.service.diff_versions(fx.ctx, [a, b], 1, 2))
        added = [c for c in diff.changes
                 if c.field == SECTION_TEMPORAL_CHANGES and c.change_type == "added"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].item_id, "t_1")


# --------------------------------------------------------------------------
# 5. 冲突保留
# --------------------------------------------------------------------------


class TestConflictPreservation(FixtureTestCase):
    def _two_conflicting(self, fx):
        a, b = fx.pair()
        e1 = fx.evidence("访谈录称两人自幼相识。", [a, b])
        e2 = fx.evidence("另一篇报道称两人成年后才认识。", [a, b])
        conflict = unwrap(fx.evidence_service.record_conflict(
            fx.ctx, ConflictDraft(
                topic="相识时间", subject_entity_ids=[a, b],
                member_evidence_ids=[e1, e2],
                divergence_kind=DivergenceKind.FACTUAL)))
        return a, b, e1, e2, conflict

    def test_conflict_linked_but_not_adjudicated(self):
        fx = self.fx
        a, b, e1, e2, conflict = self._two_conflicting(fx)
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = [conflict.id]
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", conflict.id))
        self.assertEqual(row["resolution_policy"], "KEEP_ALL_UNASSESSED")
        self.assertIn(row["review_status"], ("UNREVIEWED", "ACKNOWLEDGED"))
        from persona.core.enums import ReviewStatus
        self.assertNotIn("RESOLVED", [s.value for s in ReviewStatus])

    def test_conflict_does_not_block_creation(self):
        fx = self.fx
        a, b, e1, e2, conflict = self._two_conflicting(fx)
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = [conflict.id]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        self.assertTrue(any(w.code == "CONFLICT_LINKED"
                            for w in result.warnings))

    def test_conflict_members_both_retained(self):
        fx = self.fx
        a, b, e1, e2, conflict = self._two_conflicting(fx)
        members = unwrap(fx.evidence_service.query_evidence(
            fx.ctx, Query(filters={"conflict_ids": [conflict.id]}, limit=10)))
        self.assertEqual({m.id for m in members.items}, {e1, e2})
        for evidence_id in (e1, e2):
            row = unwrap(fx.storage.get(fx.ctx, "EvidenceItem", evidence_id))
            self.assertIsNotNone(row)

    def test_cf_v5_bidirectional_writeback(self):
        fx = self.fx
        a, b, e1, e2, conflict = self._two_conflicting(fx)
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = [conflict.id]
        ref = unwrap(fx.service.create_relationship(fx.ctx, draft))
        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", conflict.id))
        self.assertEqual(len(row["downstream_refs"]), 1)
        self.assertEqual(row["downstream_refs"][0]["relationship_id"], ref.id)
        self.assertEqual(row["downstream_refs"][0]["version"], 1)

    def test_conflict_link_idempotent_on_revise(self):
        fx = self.fx
        a, b, e1, e2, conflict = self._two_conflicting(fx)
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = [conflict.id]
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        draft.open_questions = ["相识时间冲突待人工审阅"]
        draft.change_reason = "记录冲突带来的缺口"
        unwrap(fx.service.revise(fx.ctx, [a, b], draft))
        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", conflict.id))
        # v2 是新的 relationship_id，应新增一条而不是重复 v1 的条目
        self.assertEqual(len(row["downstream_refs"]), 2)
        self.assertEqual({r["version"] for r in row["downstream_refs"]}, {1, 2})

    def test_disputed_evidence_still_referenceable(self):
        fx = self.fx
        a, b, e1, e2, conflict = self._two_conflicting(fx)
        row = unwrap(fx.storage.get(fx.ctx, "EvidenceItem", e1))
        self.assertEqual(row["status"], str(EvidenceStatus.DISPUTED))
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = [conflict.id]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_dangling_conflict_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = ["cfc_notexist"]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_wrong_kind_conflict_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.conflict_ids = ["evd_notconflict"]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)


# --------------------------------------------------------------------------
# 6. 版本变化
# --------------------------------------------------------------------------


class TestVersioningAndDiff(FixtureTestCase):
    def test_revise_creates_new_version_and_archives_old(self):
        fx = self.fx
        a, b = fx.pair()
        first = unwrap(fx.service.create_relationship(
            fx.ctx, fx.pair_draft(a, b)))
        ref = fx.evidence("后期访谈里两人已成莫逆。", [a, b])
        draft2 = fx.pair_draft(a, b)
        draft2.temporal_changes = [fx.item("t_1", "后期成为莫逆", [ref],
                                           stability=Stability.PATTERN)]
        draft2.change_reason = "补充关系时间线"
        second = unwrap(fx.service.revise(fx.ctx, [a, b], draft2))
        self.assertEqual(second.version, 2)
        self.assertNotEqual(second.id, first.id)
        v1 = unwrap(fx.service.get_relationship(fx.ctx, [a, b], version=1))
        self.assertEqual(v1.superseded_by, second.id)
        self.assertEqual(v1.temporal_changes, [])  # 旧版内容未被修改

    def test_revise_requires_change_reason(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        ref = fx.evidence("后期访谈里两人已成莫逆。", [a, b])
        draft2 = fx.pair_draft(a, b)
        draft2.temporal_changes = [fx.item("t_1", "后期成为莫逆", [ref],
                                           stability=Stability.PATTERN)]
        result = fx.service.revise(fx.ctx, [a, b], draft2)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_revise_identical_content_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        draft.change_reason = "只是想再存一次"
        result = fx.service.revise(fx.ctx, [a, b], draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE)

    def test_revise_only_open_questions_change_is_allowed(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        draft2 = fx.pair_draft(a, b)
        draft2.open_questions = ["情绪表达习惯：仅一条证据，待补"]
        draft2.change_reason = "记录新的缺口"
        result = fx.service.revise(fx.ctx, [a, b], draft2)
        self.assertTrue(is_ok(result))

    def test_revise_participant_mismatch_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        result = fx.service.revise(fx.ctx, [a, c], fx.pair_draft(a, b))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_diff_field_level(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        ref_new = fx.evidence("后期 A 改口称 B 也并不牢靠。", [a, b])
        draft2 = fx.pair_draft(a, b)
        draft2.perspectives[a].view_of_targets[b] = [
            fx.item("v_ab", "A 改口称 B 并不牢靠", [ref_new],
                    stability=Stability.REPEATED)]
        draft2.status = ProfileStatus.AMBIGUOUS
        draft2.open_questions = ["B 的真实可靠性待补"]
        draft2.change_reason = "v2：视角修订 + 状态变更 + 记录缺口"
        unwrap(fx.service.revise(fx.ctx, [a, b], draft2))
        diff = unwrap(fx.service.diff_versions(fx.ctx, [a, b], 1, 2))
        by_key = {(c.field, c.change_type, c.item_id): c for c in diff.changes}
        self.assertIn(("perspectives." + a, "changed", "v_ab"), by_key)
        self.assertIn(("status", "changed", ""), by_key)
        self.assertIn(("open_questions", "added", ""), by_key)
        self.assertEqual(diff.to_version_change_reason,
                         "v2：视角修订 + 状态变更 + 记录缺口")

    def test_diff_missing_version_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        result = fx.service.diff_versions(fx.ctx, [a, b], 1, 5)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_VERSION)

    def test_history_visible_in_query(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        ref = fx.evidence("后期访谈里两人已成莫逆。", [a, b])
        draft2 = fx.pair_draft(a, b)
        draft2.temporal_changes = [fx.item("t_1", "后期成为莫逆", [ref],
                                           stability=Stability.PATTERN)]
        draft2.change_reason = "补充关系时间线"
        unwrap(fx.service.revise(fx.ctx, [a, b], draft2))
        page = unwrap(fx.service.query_relationships(
            fx.ctx, Query(filters={"participant_entity_ids": [a]}, limit=50)))
        self.assertEqual(page.total, 2)
        self.assertEqual({i.version for i in page.items}, {1, 2})


# --------------------------------------------------------------------------
# 7. 多人物群体边界
# --------------------------------------------------------------------------


class TestGroupBoundary(FixtureTestCase):
    def test_group_requires_group_dynamics(self):
        fx = self.fx
        a, b, c = fx.trio()
        draft = fx.group_draft(a, b, c)
        draft.group_dynamics = None
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_GROUP_DYNAMICS_MISSING)

    def test_group_dynamics_all_empty_without_note_rejected(self):
        fx = self.fx
        a, b, c = fx.trio()
        draft = fx.group_draft(a, b, c)
        draft.group_dynamics = GroupDynamics(note="")
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_GROUP_DYNAMICS_MISSING)

    def test_group_dynamics_all_empty_with_note_ok(self):
        fx = self.fx
        a, b, c = fx.trio()
        draft = fx.group_draft(a, b, c)
        draft.group_dynamics = GroupDynamics(note="联盟与冷落暂无证据，待补")
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_pair_cardinality_with_three_participants_rejected(self):
        fx = self.fx
        a, b, c = fx.trio()
        draft = fx.group_draft(a, b, c)
        draft.cardinality = Cardinality.PAIR
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_group_cardinality_with_two_participants_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.cardinality = Cardinality.GROUP
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_pair_with_group_dynamics_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("两人曾一起行动。", [a, b])
        draft.group_dynamics = GroupDynamics(
            alliances=[fx.item("g_1", "两人结盟", [ref])])
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V3", result.error.message)

    def test_group_stored_as_single_profile_not_decomposed(self):
        fx = self.fx
        a, b, c = fx.trio()
        unwrap(fx.service.create_relationship(fx.ctx, fx.group_draft(a, b, c)))
        page = unwrap(fx.service.query_relationships(
            fx.ctx, Query(limit=50)))
        # 群体关系是一份完整档案；系统不自动拆出两人子档
        self.assertEqual(page.total, 1)
        self.assertEqual(page.items[0].cardinality, "GROUP")
        self.assertEqual(len(page.items[0].participant_entity_ids), 3)
        self.assertEqual(set(page.items[0].perspectives), {a, b, c})

    def test_group_dynamics_items_need_evidence(self):
        fx = self.fx
        a, b, c = fx.trio()
        draft = fx.group_draft(a, b, c)
        draft.group_dynamics = GroupDynamics(
            alliances=[fx.item("g_1", "A 与 B 结盟", [])],
            note="C 的关系待补")
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V4", result.error.message)

    def test_group_dynamics_all_sections_roundtrip(self):
        fx = self.fx
        a, b, c = fx.trio()
        ref = fx.evidence("三人曾一起守夜班。", [a, b, c])
        draft = fx.group_draft(a, b, c)
        draft.group_dynamics = GroupDynamics(
            alliances=[fx.item("g_1", "A 与 B 常一起行动", [ref])],
            exclusion=[fx.item("g_2", "C 被排除在两次行动外", [ref])],
            relay=[fx.item("g_3", "B 常在 A 与 C 之间传话", [ref])],
            note="三方互动细节待补")
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b, c]))
        group = view.group_dynamics
        self.assertEqual(len(group.alliances), 1)
        self.assertEqual(len(group.exclusion), 1)
        self.assertEqual(len(group.relay), 1)
        self.assertEqual(group.note, "三方互动细节待补")


# --------------------------------------------------------------------------
# 8. 虚构角色版本边界
# --------------------------------------------------------------------------


class TestFictionalBoundary(FixtureTestCase):
    def test_fictional_pair_requires_variants(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.applicable_variant_ids = []
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_variants_missing_participant_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.applicable_variant_ids = [a]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("PF-V5", result.error.message)

    def test_variants_must_be_subset_of_participants(self):
        fx = self.fx
        a, b = fx.pair()
        c = fx.entity("赵灯", variant_group_id=new_token("vg_"))
        draft = fx.pair_draft(a, b)
        draft.applicable_variant_ids = [a, b, c]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_real_person_in_variants_rejected(self):
        fx = self.fx
        ra, rb = fx.real_pair()
        draft = fx.pair_draft(ra, rb)
        draft.applicable_variant_ids = [ra, rb]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("D-115", result.error.message)

    def test_real_pair_without_variants_ok(self):
        fx = self.fx
        ra, rb = fx.real_pair()
        draft = fx.pair_draft(ra, rb)
        draft.applicable_variant_ids = []
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_two_versions_of_same_character_rejected(self):
        fx = self.fx
        v1, v2 = fx.two_versions()
        result = fx.service.create_relationship(
            fx.ctx, fx.pair_draft(v1, v2))
        self.assertTrue(is_err(result))
        self.assertIn("PE-V3", result.error.message)

    def test_two_version_pairs_are_separate_profiles(self):
        fx = self.fx
        v1, v2 = fx.two_versions()
        b = fx.entity("陈雾", variant_group_id=new_token("vg_"))
        ref1 = unwrap(fx.service.create_relationship(
            fx.ctx, fx.pair_draft(v1, b)))
        ref2 = unwrap(fx.service.create_relationship(
            fx.ctx, fx.pair_draft(v2, b)))
        self.assertNotEqual(ref1.id, ref2.id)
        view1 = unwrap(fx.service.get_relationship(fx.ctx, [v1, b]))
        view2 = unwrap(fx.service.get_relationship(fx.ctx, [v2, b]))
        self.assertNotEqual(view1.id, view2.id)

    def test_evidence_scoped_to_other_version_rejected(self):
        fx = self.fx
        v1, v2 = fx.two_versions()
        b = fx.entity("陈雾", variant_group_id=new_token("vg_"))
        # 证据只适用于 v2（另一个改编版本）
        wrong = fx.evidence("v2 版剧情里两人并肩。", [v2, b],
                              applicable_variant_ids=[v2])
        draft = fx.pair_draft(v1, b)
        draft.perspectives[v1].view_of_targets[b] = [
            fx.item("v_wrong", "v2 剧情里的印象", [wrong],
                    stability=Stability.REPEATED)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("D-117", result.error.message)

    def test_evidence_scoped_to_participant_version_ok(self):
        fx = self.fx
        v1, v2 = fx.two_versions()
        b = fx.entity("陈雾", variant_group_id=new_token("vg_"))
        right = fx.evidence("v1 版剧情里两人并肩。", [v1, b],
                              applicable_variant_ids=[v1])
        draft = fx.pair_draft(v1, b)
        draft.perspectives[v1].view_of_targets[b] = [
            fx.item("v_right", "v1 剧情里的印象", [right],
                    stability=Stability.REPEATED)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_different_work_characters_allowed(self):
        """不同作品的角色（不同 variant_group）可以有一份关系档案。"""
        fx = self.fx
        a, b = fx.pair()
        result = fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b))
        self.assertTrue(is_ok(result))


# --------------------------------------------------------------------------
# 9. 模拟内容隔离
# --------------------------------------------------------------------------


class TestSimulatedIsolation(FixtureTestCase):
    def test_simulated_item_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("模拟对话里的台词。", [a, b])
        draft.perspectives[a].feeling_toward = [
            fx.item("f_sim", "模拟对话里的情绪", [ref],
                    namespace=ProvenanceNamespace.SIMULATED)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_non_system_derived_item_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("用户自己提供的关系描述。", [a, b])
        draft.asymmetry_notes = [
            fx.item("asym_user", "用户提供的说法", [ref],
                    namespace=ProvenanceNamespace.USER_PROVIDED)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_storage_layer_rejects_simulated_profile(self):
        """绕过模块直接 storage.put 仍被存储层结构闸门拒绝（D-064 双层）。"""
        fx = self.fx
        result = fx.storage.put(
            fx.ctx, "RelationshipProfile",
            {"participant_entity_ids": ["pe_x", "pe_y"], "cardinality": "PAIR"},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED)
        self.assertTrue(is_err(result))

    def test_memory_item_ref_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.perspectives[a].feeling_toward = [
            fx.item("f_mem", "来自模拟记忆的情绪", ["mem_simulated"])]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V6", result.error.message)

    def test_conversation_turn_ref_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.perspectives[a].feeling_toward = [
            fx.item("f_ctn", "来自模拟对话的情绪", ["ctn_simulated"])]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V6", result.error.message)

    def test_persona_profile_ref_rejected(self):
        """RL-V6：不能拿人格档案字段当关系证据（不凭两份档案拼关系）。"""
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.perspectives[a].feeling_toward = [
            fx.item("f_pnf", "从 A 的档案推出来的", ["pnf_someprofile"])]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V6", result.error.message)

    def test_simulated_evidence_cannot_exist_at_all(self):
        """证据层本身就拒绝 SIMULATED（EV-V1）：关系引用不到模拟证据。"""
        fx = self.fx
        a, b = fx.pair()
        result = fx.evidence_service.add_evidence(
            fx.ctx, EvidenceDraft(
                claim="模拟对话里的台词。", tier=EvidenceTier.VERBATIM,
                dimension=EvidenceDimension.RELATION, subject_entity_ids=[a, b],
                extraction_confidence=0.9, source_id=fx.source(),
                namespace=ProvenanceNamespace.SIMULATED))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_full_scan_has_no_simulated_profiles(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        page = unwrap(fx.storage.query(
            fx.ctx, "RelationshipProfile", Query(limit=200)))
        self.assertTrue(page.items)
        for row in page.items:
            self.assertNotEqual(row["namespace"],
                                str(ProvenanceNamespace.SIMULATED))


# --------------------------------------------------------------------------
# 10. 证据追溯
# --------------------------------------------------------------------------


class TestEvidenceTracing(FixtureTestCase):
    def test_item_to_evidence_to_source_roundtrip(self):
        fx = self.fx
        a, b = fx.pair()
        source_id = fx.source()
        ref = fx.evidence("两人曾在灯节一起修灯塔。", [a, b],
                            source_id=source_id)
        draft = fx.pair_draft(a, b)
        draft.shared_experiences = [fx.shared("s_1", "两人在灯节一起修灯塔",
                                              [ref], occurred_at=None)]
        unwrap(fx.service.create_relationship(fx.ctx, draft))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        evidence_id = view.shared_experiences[0].evidence_refs[0]
        evidence_row = unwrap(fx.storage.get(fx.ctx, "EvidenceItem", evidence_id))
        self.assertEqual(evidence_row["source_id"], source_id)
        source_row = unwrap(fx.storage.get(fx.ctx, "Source", source_id))
        self.assertEqual(source_row["title"], "《雾港与星灯》访谈录")

    def test_validate_evidence_refs_accepts_valid(self):
        fx = self.fx
        a, b = fx.pair()
        ref = fx.evidence("两人曾在灯节一起修灯塔。", [a, b])
        result = fx.service.validate_evidence_refs(
            fx.ctx, [{"item_id": "x", "text": "t", "evidence_refs": [ref]}],
            participant_entity_ids=[a, b])
        self.assertTrue(is_ok(result))
        self.assertTrue(result.value)

    def test_validate_evidence_refs_rejects_profile_only(self):
        fx = self.fx
        a, b = fx.pair()
        result = fx.service.validate_evidence_refs(
            fx.ctx, [{"item_id": "x", "text": "t",
                      "evidence_refs": ["pnf_onlyprofile"]}])
        self.assertTrue(is_err(result))
        self.assertIn("RL-V6", result.error.message)

    def test_validate_evidence_refs_rejects_empty(self):
        fx = self.fx
        result = fx.service.validate_evidence_refs(
            fx.ctx, [{"item_id": "x", "text": "t", "evidence_refs": []}])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT)

    def test_nonexistent_evidence_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.shared_experiences = [
            fx.shared("s_1", "引用了不存在的证据", ["evd_missing"], occurred_at=None)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_coverage_is_server_computed(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        self.assertEqual(view.coverage[SECTION_SHARED_EXPERIENCES]["items"], 1)
        self.assertEqual(
            view.coverage[SECTION_SHARED_EXPERIENCES]["evidence_refs"], 1)
        self.assertEqual(view.coverage[SECTION_INTERACTION_PATTERNS]["items"], 0)


# --------------------------------------------------------------------------
# 11. 模式闸门
# --------------------------------------------------------------------------


class TestPatternGate(FixtureTestCase):
    def test_single_interaction_rejected_in_patterns(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("两人只碰撞过一次。", [a, b])
        draft.interaction_patterns = [
            fx.item("p_1", "总是 A 先开口", [ref], stability=Stability.SINGLE)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_GATE_REJECTED)

    def test_repeated_interaction_ok_in_patterns(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("多次访谈里都是 A 先开口。", [a, b])
        draft.interaction_patterns = [
            fx.item("p_1", "总是 A 先开口", [ref], stability=Stability.REPEATED)]
        draft.emotional_expression_habits = [
            fx.item("p_2", "B 对 A 从不用敬语", [ref],
                    stability=Stability.PATTERN)]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_single_interaction_belongs_to_shared_experiences(self):
        """单次互动不被判错——它的归宿是 shared_experiences（具体经历）。"""
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("两人只碰撞过一次。", [a, b])
        draft.shared_experiences.append(fx.shared(
            "s_2", "一次偶然碰撞", [ref], occurred_at=None,
            stability=Stability.SINGLE))
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_gate_error_message_mentions_target(self):
        fx = self.fx
        result = fx.service.apply_pattern_gate(
            fx.ctx, fx.item("p_1", "t", ["evd_x"], stability=Stability.SINGLE))
        self.assertTrue(is_err(result))
        self.assertIn(SECTION_INTERACTION_PATTERNS, result.error.message)


# --------------------------------------------------------------------------
# 12. 非法输入
# --------------------------------------------------------------------------


class TestIllegalInput(FixtureTestCase):
    def test_too_few_participants(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.participant_entity_ids = [a]
        draft.cardinality = Cardinality.PAIR
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS)

    def test_duplicate_participants(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.participant_entity_ids = [a, a]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_missing_participant(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.participant_entity_ids = [a, "pe_notexist"]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_ENTITY_MISSING)

    def test_unknown_cardinality(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.cardinality = "TRIO"
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_rl_v2_intimacy_field_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.intimacy_score = 0.9
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V2", result.error.message)

    def test_rl_v2_closeness_field_rejected(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.relationship_closeness = 5
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertIn("RL-V2", result.error.message)

    def test_empty_item_text(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.asymmetry_notes = [fx.item("asym_1", "  ", ["evd_x"])]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_missing_item_id(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("一条没有 item_id 的说明。", [a, b])
        draft.asymmetry_notes = [ProfileItem(item_id="", text="说明",
                                             confidence=0.8,
                                             evidence_refs=[ref])]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_confidence_out_of_range(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        ref = fx.evidence("一条置信度越界的说明。", [a, b])
        draft.asymmetry_notes = [ProfileItem(item_id="asym_1", text="说明",
                                             confidence=1.5,
                                             evidence_refs=[ref])]
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_unknown_status(self):
        fx = self.fx
        a, b = fx.pair()
        draft = fx.pair_draft(a, b)
        draft.status = "PERFECT"
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_empty_profile_without_open_questions(self):
        fx = self.fx
        a, b = fx.pair()
        draft = RelationshipDraft(
            participant_entity_ids=[a, b],
            cardinality=Cardinality.PAIR,
            perspectives={a: PerspectiveBlock(entity_id=a),
                          b: PerspectiveBlock(entity_id=b)},
            applicable_variant_ids=[a, b])
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT)

    def test_empty_profile_with_open_questions_ok(self):
        fx = self.fx
        a, b = fx.pair()
        draft = RelationshipDraft(
            participant_entity_ids=[a, b],
            cardinality=Cardinality.PAIR,
            perspectives={a: PerspectiveBlock(entity_id=a),
                          b: PerspectiveBlock(entity_id=b)},
            applicable_variant_ids=[a, b],
            open_questions=["两人关系尚无任何证据，待研究"],
            status=ProfileStatus.INSUFFICIENT_EVIDENCE)
        result = fx.service.create_relationship(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        view = unwrap(fx.service.get_relationship(fx.ctx, [a, b]))
        self.assertEqual(view.status, "INSUFFICIENT_EVIDENCE")
        self.assertEqual(view.open_questions, ["两人关系尚无任何证据，待研究"])

    def test_revise_missing_profile(self):
        fx = self.fx
        a, b = fx.pair()
        result = fx.service.revise(
            fx.ctx, [a, b], fx.pair_draft(a, b, change_reason="更新"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)


# --------------------------------------------------------------------------
# 13. 重启持久化
# --------------------------------------------------------------------------


class TestPersistenceAcrossRestart(FixtureTestCase):
    def test_survives_new_storage_instance(self):
        fx = self.fx
        a, b, c = fx.trio()
        ref = unwrap(fx.service.create_relationship(
            fx.ctx, fx.group_draft(a, b, c)))
        ref2 = unwrap(fx.service.revise(
            fx.ctx, [a, b, c], fx.group_draft(
                a, b, c, change_reason="补充群体互动")))
        # 关掉旧连接，用新存储实例从同一文件读回
        fx.storage.close()
        fresh = SqliteStorage(Path(fx.storage.path))
        service = RelationshipService(fresh)
        ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(fresh.init_schema(ctx))
        view = unwrap(service.get_relationship(ctx, [a, b, c]))
        self.assertEqual(view.version, 2)
        self.assertEqual(view.id, ref2.id)
        v1 = unwrap(service.get_relationship(ctx, [a, b, c], version=1))
        self.assertEqual(v1.id, ref.id)
        self.assertEqual(v1.superseded_by, ref2.id)
        self.assertEqual(len(v1.perspectives), 3)
        self.assertIsNotNone(v1.group_dynamics)
        fresh.close()
        shutil.rmtree(Path(fx.storage.path).parent, ignore_errors=True)

    def test_perspectives_and_shared_roundtrip(self):
        fx = self.fx
        a, b = fx.pair()
        unwrap(fx.service.create_relationship(fx.ctx, fx.pair_draft(a, b)))
        fx.storage.close()
        fresh = SqliteStorage(Path(fx.storage.path))
        service = RelationshipService(fresh)
        ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(fresh.init_schema(ctx))
        view = unwrap(service.get_relationship(ctx, [a, b]))
        self.assertEqual(view.perspectives[a].view_of_targets[b][0].text,
                         "A 觉得 B 靠得住")
        self.assertEqual(view.perspectives[a].uncertainty_note,
                         "A 的内心想法只有访谈转述，待补")
        self.assertEqual(view.shared_experiences[0].occurred_at.raw,
                         "2020 年灯节")
        fresh.close()
        shutil.rmtree(Path(fx.storage.path).parent, ignore_errors=True)


# --------------------------------------------------------------------------
# 14. 模块边界
# --------------------------------------------------------------------------


class TestModuleBoundary(unittest.TestCase):
    def test_no_research_methods(self):
        """F-2：关系模块不自行启动研究循环。"""
        import persona.modules.relationship as rm
        names = {m for m in dir(rm.RelationshipService)
                 if not m.startswith("_")}
        for forbidden in ("search", "collect", "query_plan", "distill"):
            self.assertNotIn(forbidden, names)

    def test_no_provider_import(self):
        """F-5 / D-054：不依赖任何具体模型供应商。"""
        import persona.modules.relationship as rm
        source = pathlib.Path(rm.__file__).read_text(encoding="utf-8")
        self.assertNotIn("from ..providers", source)
        self.assertNotIn("import openai", source)
        self.assertNotIn("from ..orchestrator", source)

    def test_no_intimacy_field_in_dataclasses(self):
        """RL-V2 / D-048：数据契约里不存在亲密度类字段。"""
        import persona.modules.relationship as rm
        for name, obj in vars(rm).items():
            if not hasattr(obj, "__dataclass_fields__"):
                continue
            for field_name in obj.__dataclass_fields__:
                low = field_name.lower()
                for marker in ("score", "closeness", "intimacy"):
                    self.assertNotIn(marker, low,
                                     f"{name}.{field_name} 含禁止字段名")

    def test_port_protocol_aligned_with_service(self):
        """Protocol 声明的公开方法必须都在服务上实现（不虚占签名）。"""
        import persona.modules.relationship as rm
        port_methods = {m for m in dir(rm.RelationshipPort)
                        if not m.startswith("_") and callable(
                            getattr(rm.RelationshipPort, m))}
        service_methods = {m for m in dir(rm.RelationshipService)
                           if not m.startswith("_")}
        self.assertTrue(port_methods <= service_methods,
                        f"Protocol 声明了服务未实现的方法："
                        f"{sorted(port_methods - service_methods)}")


if __name__ == "__main__":
    unittest.main()
