"""Persona 模块测试（P3 第一步：人物人格档案基础；用户称之为 P3.2）。

全部跑在**临时目录**的 SQLite 文件上，不碰任何正式项目库、不调外部服务。

覆盖（对应用户验收清单第 9 条 + 模块诚实边界）：
  1. 创建与查询（档案 v1、版本读取、过滤/排序/分页、历史版本可见）
  2. 证据追溯（条目 → evidence_refs → EvidenceItem 往返；推断链可追溯）
  3. 不确定性表达（uncertainty_note / open_questions / INSUFFICIENT_EVIDENCE）
  4. 冲突保留（关联不裁决、双方证据都在、CF-V5 双向一致、不阻断写入）
  5. 版本变化（revise 生成新版本、旧版归档不覆盖、diff、空转拒绝）
  6. 虚构角色版本边界（PF-V5：版本声明、不跨组、证据不串用、双版本不合并）
  7. 模拟内容隔离（模块层 + 存储层双层闸门；mem_/ctn_ 引用拒绝）
  8. 稳定性闸门（PF-V2 / D-012：SINGLE 不进 traits；PF-V3 物理隔离）
  9. 非法输入（各校验规则的失败码）
 10. 重启持久化（新存储实例读回全部版本）
 11. 模块边界（F-2/F-5：不启动研究、不依赖 Provider）

对应文档：INTERFACES §6、DATA_MODEL §4.7、ERROR_HANDLING §2.2、
DECISIONS §2.17（D-111~D-120）。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    DivergenceKind,
    EvidenceDimension,
    EvidenceStatus,
    EvidenceTier,
    ProfileStatus,
    ProvenanceNamespace,
    ReviewStatus,
    Stability,
)
from persona.core.errors import ErrorCode
from persona.core.ids import new_token
from persona.core.result import is_err, is_ok, unwrap
from persona.core.types import Query
from persona.modules.evidence import (
    ConflictDraft,
    EvidenceDraft,
    EvidenceService,
    InferenceDraft,
    SourceDraft,
)
from persona.modules.persona import (
    SECTION_BACKGROUND,
    SECTION_EXPRESSION_STYLE,
    SECTION_HABITS,
    SECTION_SINGLE_EVENTS,
    SECTION_TRAITS,
    CanonBoundary,
    PersonaProfileDraft,
    PersonaProfileView,
    PersonaService,
    ProfileItem,
    ProfileSection,
    TimelineItem,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_persona"


class PersonaFixture:
    """一个临时数据库 + 证据服务 + 人格档案服务 + 调用上下文。用完即关。"""

    def __init__(self, tmp: str) -> None:
        self.storage = SqliteStorage(Path(tmp) / "persona.db")
        self.service = PersonaService(self.storage)
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

    def source(self, title: str = "《雾港与星灯》访谈录", **kwargs) -> str:
        draft = SourceDraft(title=title,
                            canonical_key=kwargs.pop("canonical_key",
                                                    f"key_{new_token()}"),
                            **kwargs)
        return unwrap(self.evidence_service.register_source(self.ctx, draft)).id

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
        return unwrap(self.evidence_service.add_evidence(self.ctx, draft)).id

    # ---------------------------------------------------------- 常用组合

    def hero(self) -> str:
        if not hasattr(self, "_hero"):
            self._hero = self.entity("林小满")
        return self._hero

    def one_evidence(self, claim: str = "他习惯在雨夜出门", *,
                     subject: str | None = None, **kwargs) -> str:
        return self.evidence(self.source(), claim, subject=subject, **kwargs)

    def trait(self, item_id: str, text: str, refs: list[str], *,
              stability: Stability = Stability.REPEATED,
              confidence: float = 0.8, **kwargs) -> ProfileItem:
        return ProfileItem(item_id=item_id, text=text, confidence=confidence,
                           evidence_refs=refs, stability=stability, **kwargs)

    def minimal_draft(self, entity_id: str | None = None, **kwargs) -> PersonaProfileDraft:
        """一份只有一条背景（带证据）的最小合法档案。"""
        entity_id = entity_id or self.hero()
        ref = self.one_evidence("他出生于雾港，自幼在灯厂长大的。",
                                dimension=EvidenceDimension.BACKGROUND)
        return PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text="雾港出身，灯厂长大的青年。",
                                      confidence=0.8,
                                      evidence_refs=[ref]),
            applicable_variant_ids=[entity_id],
            **kwargs,
        )


def _fixture() -> PersonaFixture:
    return PersonaFixture(tempfile.mkdtemp(prefix="gps-persona-"))


class FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()

    def tearDown(self) -> None:
        self.fx.storage.close()
        shutil.rmtree(Path(self.fx.storage.path).parent, ignore_errors=True)


# ==========================================================================
# 1. 创建与查询
# ==========================================================================


class TestProfileCreation(FixtureTestCase):
    def test_create_v1_and_read_back(self):
        fx = self.fx
        entity = fx.hero()
        ref = fx.one_evidence()
        draft = fx.minimal_draft(entity)
        draft.traits = [fx.trait("trait_1", "寡言但观察力敏锐", [ref])]
        draft.habits = [fx.trait("habit_1", "习惯在雨夜出门", [ref],
                                 stability=Stability.REPEATED)]
        ref_out = unwrap(fx.service.create_profile(fx.ctx, draft))
        self.assertTrue(ref_out.id.startswith("pnf_"))
        self.assertEqual(ref_out.version, 1)
        self.assertEqual(ref_out.status, str(ProfileStatus.INSUFFICIENT_EVIDENCE))

        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(view.version, 1)
        self.assertEqual(view.namespace, str(ProvenanceNamespace.SYSTEM_DERIVED))
        self.assertEqual(view.entity_id, entity)
        self.assertEqual(view.background.text, "雾港出身，灯厂长大的青年。")
        self.assertEqual(len(view.traits), 1)
        self.assertEqual(view.traits[0].item_id, "trait_1")
        self.assertEqual(view.traits[0].evidence_refs, [ref])
        self.assertEqual(view.traits[0].stability, Stability.REPEATED)
        self.assertIsNone(view.superseded_by)
        # 覆盖度由服务计算：背景 1 条证据；traits/habits 各 1 条目
        self.assertEqual(view.coverage[SECTION_BACKGROUND],
                         {"items": 1, "evidence_refs": 1})
        self.assertEqual(view.coverage[SECTION_TRAITS]["items"], 1)
        self.assertEqual(view.coverage[SECTION_HABITS]["items"], 1)
        self.assertEqual(view.coverage[SECTION_EXPRESSION_STYLE],
                         {"items": 0, "evidence_refs": 0})

    def test_create_requires_existing_entity(self):
        draft = PersonaProfileDraft(
            entity_id="pe_" + "0" * 32,
            open_questions=["对象尚未登记"])
        result = self.fx.service.create_profile(self.fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_ENTITY_MISSING)

    def test_duplicate_create_rejected(self):
        fx = self.fx
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft()))
        again = fx.service.create_profile(fx.ctx, fx.minimal_draft())
        self.assertTrue(is_err(again))
        self.assertEqual(again.error.code, ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE)

    def test_empty_profile_without_open_questions_rejected(self):
        draft = PersonaProfileDraft(entity_id=self.fx.hero(),
                                    applicable_variant_ids=[self.fx.hero()])
        result = self.fx.service.create_profile(self.fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT)

    def test_empty_profile_with_open_questions_is_ok(self):
        """全空档案合法，但必须说明缺什么（D-020 / D-117）。"""
        fx = self.fx
        entity = fx.hero()
        draft = PersonaProfileDraft(
            entity_id=entity, applicable_variant_ids=[entity],
            open_questions=["尚无任何证据：性格/习惯/表达风格全部缺失"],
            status=ProfileStatus.INSUFFICIENT_EVIDENCE)
        ref = unwrap(fx.service.create_profile(fx.ctx, draft))
        self.assertEqual(ref.status, str(ProfileStatus.INSUFFICIENT_EVIDENCE))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(view.open_questions,
                         ["尚无任何证据：性格/习惯/表达风格全部缺失"])
        self.assertTrue(all(v == {"items": 0, "evidence_refs": 0}
                            for v in view.coverage.values()))

    def test_unknown_status_rejected(self):
        draft = self.fx.minimal_draft()
        draft.status = "WEIRD"
        result = self.fx.service.create_profile(self.fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_item_without_evidence_refs_rejected(self):
        """PF-V1：有条目就必须有证据；没证据的不许编（D-020）。"""
        fx = self.fx
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [])]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT)

    def test_section_text_without_evidence_rejected(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.expression_style = ProfileSection(text="说话简短，爱用比喻。")
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code,
                         ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT)

    def test_item_namespace_must_be_system_derived(self):
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [ref],
                                 namespace=ProvenanceNamespace.PUBLIC_SOURCE)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_real_person_profile_ok(self):
        fx = self.fx
        person = fx.entity("示例真人 R", entity_type="REAL_PERSON")
        src = fx.source(title="公开访谈实录")
        ref = fx.evidence(src, "他在访谈中谈到早年求学经历。",
                          subject=person,
                          dimension=EvidenceDimension.BACKGROUND)
        draft = PersonaProfileDraft(
            entity_id=person,
            background=ProfileSection(text="早年求学经历见公开访谈。",
                                      evidence_refs=[ref]),
            open_questions=["性格维度暂无证据"])
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))


# ==========================================================================
# 2. 查询
# ==========================================================================


class TestProfileQuery(FixtureTestCase):
    def test_get_latest_and_specific_version(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.background.text = "雾港出身；后搬至星灯塔附近。"
        revised.change_reason = "补充新证据"
        second = unwrap(fx.service.revise(fx.ctx, entity, revised))
        self.assertEqual(second.version, 2)

        latest = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(latest.version, 2)
        v1 = unwrap(fx.service.get_persona(fx.ctx, entity, version=1))
        self.assertEqual(v1.version, 1)
        self.assertNotEqual(v1.background.text, latest.background.text)

    def test_get_unknown_version_rejected(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        result = fx.service.get_persona(fx.ctx, entity, version=99)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_VERSION)

    def test_get_unknown_entity_rejected(self):
        result = self.fx.service.get_persona(self.fx.ctx, "pe_" + "0" * 32)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_query_filters_and_history_visible(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.status = ProfileStatus.OK
        revised.change_reason = "状态改判"
        unwrap(fx.service.revise(fx.ctx, entity, revised))

        page = unwrap(fx.service.query_profiles(
            fx.ctx, Query(filters={"entity_id": entity})))
        self.assertEqual(page.total, 2)  # 历史版本不被隐藏
        ok_page = unwrap(fx.service.query_profiles(
            fx.ctx, Query(filters={"entity_id": entity, "status": "OK"})))
        self.assertEqual(ok_page.total, 1)
        self.assertEqual(ok_page.items[0].version, 2)

    def test_query_unknown_filter_rejected(self):
        result = self.fx.service.query_profiles(
            self.fx.ctx, Query(filters={"claim": "x"}))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_UNKNOWN_FILTER)

    def test_query_limit_too_large_rejected(self):
        result = self.fx.service.query_profiles(self.fx.ctx, Query(limit=10_000))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_LIMIT_TOO_LARGE)

    def test_query_sort_by_version_desc(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        for reason in ("第二次", "第三次"):
            revised = fx.minimal_draft(entity)
            revised.open_questions = [f"缺口记录（{reason}）"]
            revised.change_reason = reason
            unwrap(fx.service.revise(fx.ctx, entity, revised))
        page = unwrap(fx.service.query_profiles(
            fx.ctx, Query(filters={"entity_id": entity},
                          sort=("version", "desc"))))
        self.assertEqual([i.version for i in page.items], [3, 2, 1])

    def test_query_pagination(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        for n in range(2, 5):
            revised = fx.minimal_draft(entity)
            revised.open_questions = [f"缺口 {n}"]
            revised.change_reason = f"第 {n} 版"
            unwrap(fx.service.revise(fx.ctx, entity, revised))
        page = unwrap(fx.service.query_profiles(
            fx.ctx, Query(filters={"entity_id": entity}, limit=2, offset=2)))
        self.assertEqual(len(page.items), 2)
        self.assertEqual(page.total, 4)
        self.assertFalse(page.has_more)


# ==========================================================================
# 3. 证据追溯
# ==========================================================================


class TestEvidenceTracing(FixtureTestCase):
    def test_item_to_evidence_roundtrip(self):
        fx = self.fx
        entity = fx.hero()
        ref = fx.one_evidence("他习惯在雨夜出门。")
        draft = fx.minimal_draft(entity)
        draft.traits = [fx.trait("trait_1", "夜行性习惯", [ref])]
        unwrap(fx.service.create_profile(fx.ctx, draft))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(view.traits[0].evidence_refs, [ref])
        evidence = unwrap(fx.evidence_service.get_evidence(fx.ctx, ref))
        self.assertEqual(evidence.claim, "他习惯在雨夜出门。")
        self.assertIn(entity, evidence.subject_entity_ids)

    def test_dangling_evidence_ref_rejected(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", ["evd_" + "0" * 32])]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_inference_evidence_ref_traceable_to_root(self):
        """引用推断证据合法，且推断链可追溯到根证据（不得伪装成直接证据）。"""
        fx = self.fx
        entity = fx.hero()
        root = fx.one_evidence("他在三个不同场合都先观察再行动。")
        inference = unwrap(fx.evidence_service.record_inference(fx.ctx, InferenceDraft(
            claim="他可能是一个谨慎型的人。",
            derived_from_evidence_ids=[root],
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[entity],
            confidence=0.6,
            reasoning_note="三处独立场合的一致表现")))
        draft = fx.minimal_draft(entity)
        draft.traits = [fx.trait("trait_1", "谨慎", [inference.id],
                                 stability=Stability.PATTERN)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        inferred = unwrap(fx.evidence_service.get_evidence(fx.ctx, view.traits[0].evidence_refs[0]))
        self.assertEqual(inferred.tier, str(EvidenceTier.INFERENCE))
        self.assertEqual(inferred.namespace, str(ProvenanceNamespace.SYSTEM_DERIVED))
        self.assertIsNone(inferred.source_id)  # 推断永不得伪装成来源陈述
        self.assertEqual(inferred.conflict_ids, [])

    def test_evidence_for_other_variant_rejected(self):
        """D-117：版本专属证据不得串用到其他版本的档案。"""
        fx = self.fx
        group = new_token("vg_")
        variant_a = fx.entity("示例角色 F（原作版）", variant_group_id=group)
        variant_b = fx.entity("示例角色 F（2020 动画改编版）", variant_group_id=group)
        src = fx.source(title="《雾港与星灯》第 24 集")
        ref = fx.evidence(src, "该角色在最终决战中选择了离开。",
                          subject=variant_b, tier=EvidenceTier.VERBATIM,
                          verbatim_quote="“我要离开这里。”",
                          applicable_variant_ids=[variant_b])
        draft = PersonaProfileDraft(
            entity_id=variant_a,
            background=ProfileSection(text="最终决战中选择离开。",
                                      evidence_refs=[ref]),
            applicable_variant_ids=[variant_a],
            open_questions=["原作版没有这段剧情"])
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_coverage_counts_distinct_evidence(self):
        fx = self.fx
        entity = fx.hero()
        ref = fx.one_evidence()
        draft = fx.minimal_draft(entity)
        draft.background.evidence_refs = [ref]
        draft.traits = [fx.trait("trait_1", "观察力敏锐", [ref]),
                        fx.trait("trait_2", "寡言", [ref])]
        unwrap(fx.service.create_profile(fx.ctx, draft))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(view.coverage[SECTION_TRAITS],
                         {"items": 2, "evidence_refs": 1})


# ==========================================================================
# 4. 不确定性表达
# ==========================================================================


class TestUncertaintyExpression(FixtureTestCase):
    def test_uncertainty_note_roundtrip(self):
        fx = self.fx
        entity = fx.hero()
        ref = fx.one_evidence()
        draft = fx.minimal_draft(entity)
        draft.traits = [fx.trait("trait_1", "外表冷淡", [ref],
                                 uncertainty_note="两处来源描述程度不同，未裁决")]
        unwrap(fx.service.create_profile(fx.ctx, draft))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(view.traits[0].uncertainty_note,
                         "两处来源描述程度不同，未裁决")

    def test_gap_recorded_in_open_questions(self):
        """没有证据的维度不编造，缺口进 open_questions（D-020）。"""
        fx = self.fx
        entity = fx.hero()
        draft = fx.minimal_draft(entity)
        draft.open_questions = ["表达风格：没有任何来源描述其用词习惯"]
        unwrap(fx.service.create_profile(fx.ctx, draft))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(view.open_questions,
                         ["表达风格：没有任何来源描述其用词习惯"])
        self.assertEqual(view.expression_style.text, "")
        self.assertEqual(view.coverage[SECTION_EXPRESSION_STYLE]["items"], 0)

    def test_insufficient_evidence_is_ok_not_error(self):
        fx = self.fx
        entity = fx.hero()
        draft = fx.minimal_draft(entity)
        draft.status = ProfileStatus.INSUFFICIENT_EVIDENCE
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))  # D-008：资料不足不是失败
        self.assertEqual(unwrap(result).status,
                         str(ProfileStatus.INSUFFICIENT_EVIDENCE))

    def test_confidence_out_of_range_rejected(self):
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [ref], confidence=1.5)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_view_has_no_truth_field(self):
        """PF-V4：结构上不存在"真实性"字段——confidence 只是排序权重。"""
        fields = {f for f in vars(PersonaProfileView) if not f.startswith("_")}
        for forbidden in ("truth", "veracity", "is_true", "factuality", "real"):
            self.assertNotIn(forbidden, fields)

    def test_conflicting_evidence_both_kept_in_profile(self):
        """矛盾双方都进档案，并挂上冲突主题；不单选一边。"""
        fx = self.fx
        entity = fx.hero()
        src = fx.source()
        e1 = fx.evidence(src, "他于 2015 年开始在灯厂工作。",
                         dimension=EvidenceDimension.EXPERIENCE)
        e2 = fx.evidence(src, "他于 2018 年才开始在灯厂工作。",
                         dimension=EvidenceDimension.EXPERIENCE)
        conflict = unwrap(fx.evidence_service.record_conflict(fx.ctx, ConflictDraft(
            topic="开始工作时间", subject_entity_ids=[entity],
            member_evidence_ids=[e1, e2],
            divergence_kind=DivergenceKind.TIMELINE)))
        draft = fx.minimal_draft(entity)
        draft.experiences = [
            TimelineItem(item_id="exp_2015", text="2015 年入职灯厂",
                         evidence_refs=[e1], occurred_at=None),
            TimelineItem(item_id="exp_2018", text="2018 年入职灯厂",
                         evidence_refs=[e2], occurred_at=None),
        ]
        draft.controversial_topic_ids = [conflict.id]
        draft.status = ProfileStatus.CONFLICTED
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        texts = {i.text for i in view.experiences}
        self.assertEqual(texts, {"2015 年入职灯厂", "2018 年入职灯厂"})
        self.assertEqual(view.controversial_topic_ids, [conflict.id])
        self.assertEqual(view.status, str(ProfileStatus.CONFLICTED))


# ==========================================================================
# 5. 冲突保留
# ==========================================================================


class TestConflictPreservation(FixtureTestCase):
    def _profile_with_conflict(self, fx: PersonaFixture):
        entity = fx.hero()
        src = fx.source()
        e1 = fx.evidence(src, "说法 A：他离开了雾港。")
        e2 = fx.evidence(src, "说法 B：他一直留在雾港。")
        conflict = unwrap(fx.evidence_service.record_conflict(fx.ctx, ConflictDraft(
            topic="是否离开雾港", subject_entity_ids=[entity],
            member_evidence_ids=[e1, e2],
            divergence_kind=DivergenceKind.FACTUAL)))
        draft = fx.minimal_draft(entity)
        draft.traits = [fx.trait("trait_1", "行踪成谜", [e1, e2])]
        draft.controversial_topic_ids = [conflict.id]
        return entity, conflict, draft

    def test_conflict_link_writes_downstream_refs(self):
        """CF-V5 双向一致：档案引用冲突 ⇒ 冲突记录回写 downstream_refs。"""
        fx = self.fx
        entity, conflict, draft = self._profile_with_conflict(fx)
        ref = unwrap(fx.service.create_profile(fx.ctx, draft))
        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", conflict.id))
        refs = row["downstream_refs"]
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0]["profile_id"], ref.id)
        self.assertEqual(refs[0]["version"], 1)
        self.assertEqual(refs[0]["field"], "controversial_topic_ids")

    def test_conflict_link_idempotent_on_revise(self):
        fx = self.fx
        entity, conflict, draft = self._profile_with_conflict(fx)
        unwrap(fx.service.create_profile(fx.ctx, draft))
        draft.open_questions = ["冲突主题待人工审阅"]
        draft.change_reason = "同一冲突再次关联 + 记录缺口"
        unwrap(fx.service.revise(fx.ctx, entity, draft))
        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", conflict.id))
        # v2 是新 profile_id，应新增一条而不是重复 v1 的条目
        self.assertEqual(len(row["downstream_refs"]), 2)
        self.assertEqual({r["version"] for r in row["downstream_refs"]}, {1, 2})

    def test_dangling_conflict_ref_rejected(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.controversial_topic_ids = ["cfl_" + "0" * 32]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_conflict_ref_wrong_kind_rejected(self):
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.controversial_topic_ids = [ref]  # evd_ 不是冲突
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_disputed_evidence_still_referenceable(self):
        """冲突不阻断：证据被标 DISPUTED 后，档案仍可引用（双方说法都在）。"""
        fx = self.fx
        entity, conflict, draft = self._profile_with_conflict(fx)
        member_ids = [m["evidence_id"] for m in
                      unwrap(fx.storage.get(fx.ctx, "ConflictRecord",
                                            conflict.id))["members"]]
        for member_id in member_ids:
            disputed = unwrap(fx.evidence_service.get_evidence(fx.ctx, member_id))
            self.assertEqual(disputed.status, str(EvidenceStatus.DISPUTED))
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        view = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertEqual(len(view.traits[0].evidence_refs), 2)

    def test_conflict_record_not_adjudicated(self):
        """档案关联冲突不会改变冲突的"不裁决"性质。"""
        fx = self.fx
        entity, conflict, draft = self._profile_with_conflict(fx)
        unwrap(fx.service.create_profile(fx.ctx, draft))
        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", conflict.id))
        self.assertEqual(row["resolution_policy"], "KEEP_ALL_UNASSESSED")
        self.assertIn(row["review_status"], ("UNREVIEWED", "ACKNOWLEDGED"))
        self.assertNotIn("RESOLVED", [s.value for s in ReviewStatus])


# ==========================================================================
# 6. 版本变化
# ==========================================================================


class TestVersioningAndDiff(FixtureTestCase):
    def test_revise_creates_v2_and_archives_v1(self):
        fx = self.fx
        entity = fx.hero()
        first = unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.background.text = "雾港出身；后搬至星灯塔附近。"
        revised.change_reason = "补充搬家证据"
        second = unwrap(fx.service.revise(fx.ctx, entity, revised))
        self.assertEqual(second.version, 2)

        v1 = unwrap(fx.service.get_persona(fx.ctx, entity, version=1))
        self.assertEqual(v1.background.text, "雾港出身，灯厂长大的青年。")  # 旧结论未被覆盖
        self.assertEqual(v1.superseded_by, second.id)
        latest = unwrap(fx.service.get_persona(fx.ctx, entity))
        self.assertIsNone(latest.superseded_by)
        self.assertEqual(latest.change_reason, "补充搬家证据")

    def test_revise_requires_change_reason(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.background.text = "新背景"
        revised.change_reason = ""
        result = fx.service.revise(fx.ctx, entity, revised)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_revise_identical_content_rejected(self):
        fx = self.fx
        entity = fx.hero()
        draft = fx.minimal_draft(entity)
        unwrap(fx.service.create_profile(fx.ctx, draft))
        same = fx.minimal_draft(entity)
        same.entity_id = entity
        # 同一份内容（逐字段相同），只换更新理由 ⇒ 拒绝空转版本
        same.background = draft.background
        same.change_reason = "只是想再存一次"
        result = fx.service.revise(fx.ctx, entity, same)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE)

    def test_revise_only_open_questions_change_is_allowed(self):
        """只改 open_questions 也算内容变化（缺口记录是档案的一部分）。"""
        fx = self.fx
        entity = fx.hero()
        draft = fx.minimal_draft(entity)
        unwrap(fx.service.create_profile(fx.ctx, draft))
        revised = fx.minimal_draft(entity)
        revised.entity_id = entity
        revised.background = draft.background
        revised.open_questions = ["习惯维度：仅一条证据，待补"]
        revised.change_reason = "记录新的缺口"
        result = fx.service.revise(fx.ctx, entity, revised)
        self.assertTrue(is_ok(result))

    def test_revise_without_profile_rejected(self):
        fx = self.fx
        entity = fx.hero()
        revised = fx.minimal_draft(entity)
        revised.change_reason = "x"
        result = fx.service.revise(fx.ctx, entity, revised)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_revise_entity_mismatch_rejected(self):
        fx = self.fx
        entity = fx.hero()
        other = fx.entity("林小满（另一版）")
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.change_reason = "x"
        result = fx.service.revise(fx.ctx, other, revised)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_diff_shows_field_level_changes(self):
        fx = self.fx
        entity = fx.hero()
        ref_a = fx.one_evidence("他习惯在雨夜出门。")
        v1 = fx.minimal_draft(entity)
        v1.traits = [fx.trait("trait_1", "寡言", [ref_a])]
        v1.habits = [fx.trait("habit_1", "雨夜出门", [ref_a])]
        v1.change_reason = "v1"
        unwrap(fx.service.create_profile(fx.ctx, v1))

        ref_b = fx.one_evidence("他后来明确表达过想离开雾港。")
        v2 = fx.minimal_draft(entity)
        v2.background.text = "雾港出身；明确表达过想离开。"
        v2.traits = [fx.trait("trait_1", "寡言但决断", [ref_a, ref_b])]
        v2.change_reason = "v2：背景改写 + 性格修订 + 移除旧习惯"
        unwrap(fx.service.revise(fx.ctx, entity, v2))

        diff = unwrap(fx.service.diff_versions(fx.ctx, entity, 1, 2))
        self.assertEqual((diff.v_from, diff.v_to), (1, 2))
        by_key = {(c.field, c.change_type, c.item_id): c for c in diff.changes}
        self.assertIn((SECTION_BACKGROUND, "changed", ""), by_key)
        self.assertIn((SECTION_TRAITS, "changed", "trait_1"), by_key)
        self.assertIn((SECTION_HABITS, "removed", "habit_1"), by_key)
        self.assertEqual(diff.to_version_change_reason, "v2：背景改写 + 性格修订 + 移除旧习惯")

    def test_diff_unknown_version_rejected(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        result = fx.service.diff_versions(fx.ctx, entity, 1, 7)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_VERSION)

    def test_all_versions_retained(self):
        """Q-19 的 5 版裁剪未实现：当前全版本保留，不删用户数据。"""
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        for n in range(2, 8):
            revised = fx.minimal_draft(entity)
            revised.open_questions = [f"缺口 {n}"]
            revised.change_reason = f"第 {n} 版"
            unwrap(fx.service.revise(fx.ctx, entity, revised))
        page = unwrap(fx.service.query_profiles(
            fx.ctx, Query(filters={"entity_id": entity}, limit=200)))
        self.assertEqual(page.total, 7)


# ==========================================================================
# 7. 虚构角色版本边界
# ==========================================================================


class TestFictionalBoundary(FixtureTestCase):
    def _variant(self, fx: PersonaFixture, name: str, group: str) -> str:
        return fx.entity(name, variant_group_id=group)

    def test_fictional_requires_applicable_variants(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.applicable_variant_ids = []
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_fictional_must_include_itself(self):
        fx = self.fx
        group = new_token("vg_")
        hero = self._variant(fx, "林小满", group)
        other = self._variant(fx, "林小满（另一版）", group)
        draft = fx.minimal_draft(hero)
        draft.applicable_variant_ids = [other]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_cross_variant_group_rejected(self):
        fx = self.fx
        group_a = new_token("vg_")
        group_b = new_token("vg_")
        hero = self._variant(fx, "林小满", group_a)
        stranger = self._variant(fx, "另角色", group_b)
        draft = fx.minimal_draft(hero)
        draft.applicable_variant_ids = [hero, stranger]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_unknown_variant_rejected(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.applicable_variant_ids = [fx.hero(), "pe_" + "0" * 32]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_ENTITY_MISSING)

    def test_do_not_apply_to_self_rejected(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.do_not_apply_to_variants = [fx.hero()]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_two_variants_get_separate_profiles(self):
        """同一角色的两个改编版本：两份独立档案，系统不自动合并（PE-V3）。"""
        fx = self.fx
        group = new_token("vg_")
        original = self._variant(fx, "示例角色 F（原作版）", group)
        anime = self._variant(fx, "示例角色 F（2020 动画改编版）", group)
        ref_o = fx.one_evidence("原作中他留在了雾港。", subject=original)
        ref_a = fx.one_evidence("动画中他离开了雾港。", subject=anime)
        d1 = PersonaProfileDraft(
            entity_id=original,
            background=ProfileSection(text="原作结局：留在雾港。",
                                      evidence_refs=[ref_o]),
            applicable_variant_ids=[original],
            open_questions=["动画版剧情不适用"])
        d2 = PersonaProfileDraft(
            entity_id=anime,
            background=ProfileSection(text="动画结局：离开雾港。",
                                      evidence_refs=[ref_a]),
            applicable_variant_ids=[anime],
            open_questions=["原作版剧情不适用"])
        self.assertTrue(is_ok(fx.service.create_profile(fx.ctx, d1)))
        self.assertTrue(is_ok(fx.service.create_profile(fx.ctx, d2)))
        page = unwrap(fx.service.query_profiles(fx.ctx, Query(limit=200)))
        self.assertEqual(page.total, 2)
        v1 = unwrap(fx.service.get_persona(fx.ctx, original))
        v2 = unwrap(fx.service.get_persona(fx.ctx, anime))
        self.assertEqual(v1.background.text, "原作结局：留在雾港。")
        self.assertEqual(v2.background.text, "动画结局：离开雾港。")
        self.assertEqual(v1.applicable_variant_ids, [original])
        self.assertEqual(v2.applicable_variant_ids, [anime])

    def test_canon_boundary_roundtrip(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.canon_boundary = CanonBoundary(
            canonical_material_refs=["原作轻小说版", "2020 动画改编版"],
            non_canonical_material_refs=["同人衍生剧场版"],
            note="仅登记材料归属，不裁决内容真伪")
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))
        view = unwrap(fx.service.get_persona(fx.ctx, fx.hero()))
        self.assertEqual(view.canon_boundary.canonical_material_refs,
                         ["原作轻小说版", "2020 动画改编版"])
        self.assertEqual(view.canon_boundary.non_canonical_material_refs,
                         ["同人衍生剧场版"])

    def test_canon_boundary_empty_ref_rejected(self):
        fx = self.fx
        draft = fx.minimal_draft()
        draft.canon_boundary = CanonBoundary(canonical_material_refs=["  "])
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)


# ==========================================================================
# 8. 模拟内容隔离
# ==========================================================================


class TestSimulatedIsolation(FixtureTestCase):
    def test_module_layer_rejects_simulated_item(self):
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [ref],
                                 namespace=ProvenanceNamespace.SIMULATED)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_storage_layer_rejects_simulated_profile(self):
        """绕过模块层直接写库，存储结构闸门再拒一次（D-064 双层）。"""
        fx = self.fx
        result = fx.storage.put(
            fx.ctx, "PersonaProfile", {"entity_id": fx.hero(), "version": 1},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_memory_item_ref_rejected(self):
        """模拟记忆永远不能成为档案依据（INTERFACES §6.3）。"""
        fx = self.fx
        memory_id = unwrap(fx.storage.put(
            fx.ctx, "MemoryItem", {"kind": "EPISODIC", "content": "模拟出来的记忆"},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED))
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [memory_id])]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)
        self.assertIn("EvidenceItem", result.error.message)

    def test_conversation_turn_ref_rejected(self):
        """模拟对话轮次永远不能成为档案依据。"""
        fx = self.fx
        turn_id = unwrap(fx.storage.put(
            fx.ctx, "ConversationTurn", {"content": "模拟对话中的一句"},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED))
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [turn_id])]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_full_table_scan_no_simulated_profile(self):
        fx = self.fx
        entity = fx.hero()
        unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.change_reason = "v2"
        unwrap(fx.service.revise(fx.ctx, entity, revised))
        page = unwrap(fx.service.query_profiles(fx.ctx, Query(limit=200)))
        self.assertTrue(page.items)
        for view in page.items:
            self.assertEqual(view.namespace, str(ProvenanceNamespace.SYSTEM_DERIVED))


# ==========================================================================
# 9. 稳定性闸门
# ==========================================================================


class TestStabilityGate(FixtureTestCase):
    def test_gate_single_to_traits_rejected(self):
        fx = self.fx
        item = fx.trait("trait_1", "勇敢", [], stability=Stability.SINGLE)
        result = fx.service.apply_stability_gate(fx.ctx, item, SECTION_TRAITS)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_gate_repeated_and_pattern_pass(self):
        fx = self.fx
        for stability in (Stability.REPEATED, Stability.PATTERN):
            item = fx.trait("trait_1", "勇敢", [], stability=stability)
            self.assertTrue(is_ok(
                fx.service.apply_stability_gate(fx.ctx, item, SECTION_TRAITS)))

    def test_gate_unknown_to_traits_rejected(self):
        fx = self.fx
        item = fx.trait("trait_1", "勇敢", [], stability=Stability.UNKNOWN)
        result = fx.service.apply_stability_gate(fx.ctx, item, SECTION_TRAITS)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_gate_not_applied_to_single_events(self):
        """single_events 不受闸门约束——单次行为的合法归宿（D-012）。"""
        fx = self.fx
        item = fx.trait("evt_1", "某次冲动行事", [], stability=Stability.SINGLE)
        result = fx.service.apply_stability_gate(fx.ctx, item, SECTION_SINGLE_EVENTS)
        self.assertTrue(is_ok(result))

    def test_gate_not_applied_to_habits(self):
        fx = self.fx
        item = fx.trait("habit_1", "某天偶然一次的行为", [], stability=Stability.SINGLE)
        result = fx.service.apply_stability_gate(fx.ctx, item, SECTION_HABITS)
        self.assertTrue(is_ok(result))

    def test_create_traits_with_single_rejected(self):
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("trait_1", "勇敢", [ref],
                                 stability=Stability.SINGLE)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_single_event_with_single_stability_ok(self):
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.single_events = [fx.trait("evt_1", "某次当众发火", [ref],
                                        stability=Stability.SINGLE)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_ok(result))

    def test_traits_and_single_events_item_id_overlap_rejected(self):
        """PF-V3：稳定性格与单次行为不得交叉引用同一 item_id。"""
        fx = self.fx
        ref = fx.one_evidence()
        draft = fx.minimal_draft()
        draft.traits = [fx.trait("shared_1", "勇敢", [ref])]
        draft.single_events = [fx.trait("shared_1", "某次勇敢行事", [ref],
                                        stability=Stability.SINGLE)]
        result = fx.service.create_profile(fx.ctx, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_unknown_stability_enum_rejected(self):
        fx = self.fx
        item = fx.trait("trait_1", "勇敢", [], stability="SOMETIMES")
        result = fx.service.apply_stability_gate(fx.ctx, item, SECTION_TRAITS)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)


# ==========================================================================
# 10. 重启持久化
# ==========================================================================


class TestPersistenceAcrossRestart(FixtureTestCase):
    def test_survives_new_storage_instance(self):
        fx = self.fx
        entity = fx.hero()
        first = unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
        revised = fx.minimal_draft(entity)
        revised.background.text = "雾港出身；后搬至星灯塔附近。"
        revised.change_reason = "补充搬家证据"
        second = unwrap(fx.service.revise(fx.ctx, entity, revised))

        db_path = fx.storage.path
        fx.storage.close()
        reboot = SqliteStorage(db_path)
        ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(reboot.init_schema(ctx))
        service = PersonaService(reboot)

        latest = unwrap(service.get_persona(ctx, entity))
        self.assertEqual(latest.version, 2)
        self.assertEqual(latest.background.text, "雾港出身；后搬至星灯塔附近。")
        v1 = unwrap(service.get_persona(ctx, entity, version=1))
        self.assertEqual(v1.superseded_by, second.id)
        self.assertEqual(v1.background.text, "雾港出身，灯厂长大的青年。")
        self.assertTrue(v1.id.startswith("pnf_"))
        self.assertEqual(latest.coverage[SECTION_BACKGROUND]["evidence_refs"], 1)


# ==========================================================================
# 11. 模块边界
# ==========================================================================


class TestModuleBoundary(unittest.TestCase):
    def test_no_research_or_provider_methods(self):
        """F-2/F-5：模块不自行启动研究流程，也不持有 Provider。"""
        forbidden = {"search", "collect", "query_plan", "fetch", "distill",
                     "research", "crawl", "call_provider", "chat"}
        public = {m for m in dir(PersonaService) if not m.startswith("_")}
        self.assertFalse(public & forbidden)

    def test_module_does_not_import_providers(self):
        import persona.modules.persona as module
        source = Path(module.__file__).read_text(encoding="utf-8")
        import_lines = [line for line in source.splitlines()
                        if line.startswith(("from ", "import "))]
        self.assertFalse(any("provider" in line.lower() for line in import_lines))

    def test_service_only_depends_on_storage(self):
        import inspect
        signature = inspect.signature(PersonaService.__init__)
        self.assertEqual(list(signature.parameters), ["self", "storage"])

    def test_reading_does_not_mutate_evidence(self):
        """读档案不碰证据表（ Dialogue 式只读纪律的前置）。"""
        fx = _fixture()
        try:
            entity = fx.hero()
            unwrap(fx.service.create_profile(fx.ctx, fx.minimal_draft(entity)))
            before = unwrap(fx.storage.query(
                fx.ctx, "EvidenceItem", Query(limit=200))).total
            unwrap(fx.service.get_persona(fx.ctx, entity))
            unwrap(fx.service.query_profiles(fx.ctx, Query(limit=200)))
            after = unwrap(fx.storage.query(
                fx.ctx, "EvidenceItem", Query(limit=200))).total
            self.assertEqual(before, after)
        finally:
            fx.storage.close()
            shutil.rmtree(Path(fx.storage.path).parent, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
