"""P5.1 只读层（`persona/orchestrator/readmodels.py`）离线测试。

为什么这一层值得单独测：它是**唯一**把"库里存的东西"翻译成"用户看到的话"的
地方，而最容易错、错了最难被发现的规则恰好都在这里：

1. **来源数 ≠ 证据数**。黛玉的"说话方式"是 2 条证据、1 个来源（都出自中青报）。
   把证据条数写成来源数，等于替数据撒谎。
2. **同名实体绝不合并**（D-059 / `merge_policy = NEVER_AUTO_MERGE`）。可以有
   "选哪一个显示"的规则，不可以有"把两个合成一个"的规则。
3. **档案引用了不存在的证据必须如实列出**（`MISSING_REF`）。吞掉它的后果是
   用户以为每条结论都有出处。
4. **未并入档案的证据要数清楚**（`CANON_FACT 4 条 + RELATION 3 条`），而不是
   给一句"还有一些证据没用上"。

全部离线：临时 SQLite + 模块服务直接写夹具，不触网、不调模型、零费用。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    Cardinality,
    EvidenceDimension,
    EvidenceTier,
    ProfileStatus,
    ProvenanceNamespace,
    Stability,
)
from persona.core.errors import ErrorCode
from persona.core.result import is_err, is_ok, unwrap
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    ProfileItem,
    ProfileSection,
)
from persona.modules.relationship import (
    PerspectiveBlock,
    RelationshipDraft,
    RelationshipService,
)
from persona.orchestrator.readmodels import (
    SECTION_TITLES,
    evidence_report,
    open_read_services,
    persona_report,
    relationship_report,
    resolve_entity,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_p51"


# ==========================================================================
# 夹具：一个临时库 + 三个模块服务（写）+ 一个只读栈（读）
# ==========================================================================


class P51Fixture:
    """临时库。写入用模块服务（真实校验），读取用 `open_read_services`。

    刻意**不复用同一个 `SqliteStorage`**：只读栈必须能对着一个"别人写好的库"
    读，测试里就应该是两条连接（WAL + autocommit，读写互不阻塞）。
    """

    def __init__(self, tmp: str) -> None:
        self.root = Path(tmp)
        self.path = self.root / "persona.db"
        self.storage = SqliteStorage(self.path)
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        self.evidence_service = EvidenceService(self.storage)
        self.persona_service = PersonaService(self.storage)
        self.relationship_service = RelationshipService(self.storage)
        unwrap(self.storage.init_schema(self.ctx))
        self._read = None

    # ------------------------------------------------------------- 写：基础构件

    def entity(self, name: str = "阿灯", *, works: tuple[str, ...] = ("雾港灯会",),
               adaptation: str = "2020 动画改编版") -> str:
        payload: dict = {
            "display_name": name,
            "entity_type": "FICTIONAL_CHARACTER",
            "merge_policy": "NEVER_AUTO_MERGE",
            "status": "OK",
            "fictional_character_meta": {
                "works": [{"work_id": f"wrk_{index}", "title": title,
                           "medium": "ANIMATION"}
                          for index, title in enumerate(works)],
                "adaptation_version": adaptation,
                "adaptation_lineage": [],
                "official_canon_level": "CANON",
            },
        }
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity", payload,
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def source(self, title: str = "《雾港灯会》访谈录", *,
               url: str = "https://example.test/interview",
               retrieved_at: str = "2026-01-02T03:04:05Z") -> str:
        draft = SourceDraft(title=title, canonical_key=f"key-{title}-{url}",
                            url=url, retrieved_at=retrieved_at)
        return unwrap(self.evidence_service.register_source(self.ctx, draft)).id

    def evidence_of(self, source_id: str, claim: str, *,
                    subjects: list[str], dimension: EvidenceDimension = EvidenceDimension.TRAIT,
                    tier: EvidenceTier = EvidenceTier.THIRD_PARTY,
                    trait_tag: str = "", quote: str | None = None,
                    locator: dict | None = None,
                    tags: list[str] | None = None) -> str:
        draft = EvidenceDraft(
            claim=claim,
            tier=tier,
            dimension=dimension,
            subject_entity_ids=list(subjects),
            extraction_confidence=0.8,
            source_id=source_id,
            trait_tag=trait_tag,
            verbatim_quote=quote,
            locator=locator,
            tags=list(tags or []),
        )
        return unwrap(self.evidence_service.add_evidence(self.ctx, draft)).id

    def item(self, item_id: str, text: str, refs: list[str], *,
             stability: Stability = Stability.REPEATED) -> ProfileItem:
        return ProfileItem(item_id=item_id, text=text, confidence=0.8,
                           evidence_refs=list(refs), stability=stability)

    def section(self, text: str, refs: list[str]) -> ProfileSection:
        return ProfileSection(text=text, confidence=0.8, evidence_refs=list(refs))

    def profile(self, entity_id: str, **kwargs) -> int:
        """建一份档案，返回版本号。`kwargs` 直接进 `PersonaProfileDraft`。"""
        kwargs.setdefault("applicable_variant_ids", [entity_id])
        kwargs.setdefault("status", ProfileStatus.OK)
        draft = PersonaProfileDraft(entity_id=entity_id, **kwargs)
        return unwrap(self.persona_service.create_profile(self.ctx, draft)).version

    def revise(self, entity_id: str, **kwargs) -> int:
        """出第二版（`create_profile` 对同一对象只接受一次，D-111 / D-112）。"""
        kwargs.setdefault("applicable_variant_ids", [entity_id])
        kwargs.setdefault("status", ProfileStatus.OK)
        kwargs.setdefault("change_reason", "测试里补一版")
        draft = PersonaProfileDraft(entity_id=entity_id, **kwargs)
        return unwrap(self.persona_service.revise(self.ctx, entity_id, draft)).version

    def set_created_at(self, record_id: str, iso: str) -> None:
        """改**记账列**的 `created_at`，用来稳定"谁更新"这类断言。

        `to_iso` 只到秒，同一瞬间创建的两条记录时间戳完全一样，"取最新"这条
        规则就没法被确定性地验证。改的是记录的时间列，不动 payload、不动业务
        语义——只是把"两天前建的"和"今天建的"造成真的差一天。
        """
        self.storage.connect().execute(
            "UPDATE records SET created_at = ? WHERE id = ?", (iso, record_id))

    # ------------------------------------------------------------- 读

    def read(self):
        """只读栈（缓存；`close()` 时统一关）。"""
        if self._read is None:
            self._read = unwrap(open_read_services(self.path, project_id=PROJECT))
        return self._read

    def close(self) -> None:
        if self._read is not None:
            self._read.storage.close()
            self._read = None
        self.storage.close()
        shutil.rmtree(self.root, ignore_errors=True)


def _fixture() -> P51Fixture:
    return P51Fixture(tempfile.mkdtemp(prefix="gps-p51-"))


class FixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()

    def tearDown(self) -> None:
        self.fx.close()


# ==========================================================================
# 一、名字 → 实体（0 / 1 / 多命中、pe_ 直传）
# ==========================================================================


class TestResolveEntity(FixtureCase):
    def test_empty_name_is_rejected(self):
        result = resolve_entity(self.fx.read(), "   ")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_unknown_name_reports_not_found(self):
        result = resolve_entity(self.fx.read(), "查无此人")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)
        self.assertIn("没有找到", result.error.message)

    def test_single_match_has_no_other_candidates(self):
        entity_id = self.fx.entity("阿灯")
        chosen, others = unwrap(resolve_entity(self.fx.read(), "阿灯"))
        self.assertEqual(chosen.entity_id, entity_id)
        self.assertEqual(chosen.display_name, "阿灯")
        self.assertEqual(others, [])
        # 作品与改编版本要一起带出来（show 头部第二行就靠它）
        self.assertEqual(chosen.works, ["雾港灯会"])
        self.assertEqual(chosen.adaptation_version, "2020 动画改编版")

    def test_pe_id_is_an_escape_hatch(self):
        entity_id = self.fx.entity("阿灯")
        self.fx.entity("阿灯")  # 故意造同名，直传 ID 时不该被它干扰
        chosen, others = unwrap(resolve_entity(self.fx.read(), entity_id))
        self.assertEqual(chosen.entity_id, entity_id)
        self.assertEqual(others, [])

    def test_unknown_pe_id_is_not_found(self):
        result = resolve_entity(self.fx.read(), "pe_" + "0" * 32)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_same_name_returns_latest_and_lists_the_rest(self):
        first = self.fx.entity("阿灯")
        second = self.fx.entity("阿灯")
        third = self.fx.entity("阿灯")
        # 造出确定的时间差（真实场景里它们是不同时间建的）
        self.fx.set_created_at(first, "2026-01-01T00:00:00Z")
        self.fx.set_created_at(second, "2026-02-01T00:00:00Z")
        self.fx.set_created_at(third, "2026-03-01T00:00:00Z")

        chosen, others = unwrap(resolve_entity(
            self.fx.read(), "阿灯", need_profile=False))
        self.assertEqual(chosen.entity_id, third)
        self.assertEqual([e.entity_id for e in others], [second, first])

    def test_same_second_entities_keep_insertion_order(self):
        """同一秒建的三份（`created_at` 完全相同）→ 不能悄悄选中更早那份。"""
        first = self.fx.entity("阿灯")
        second = self.fx.entity("阿灯")
        third = self.fx.entity("阿灯")
        for entity_id in (first, second, third):
            self.fx.set_created_at(entity_id, "2026-05-05T05:05:05Z")

        chosen, others = unwrap(resolve_entity(
            self.fx.read(), "阿灯", need_profile=False))
        self.assertEqual(chosen.entity_id, third, "后插入的应当算更晚")
        self.assertEqual([e.entity_id for e in others], [second, first])

    def test_prefers_the_one_with_a_profile(self):
        """最新那个还没有档案时，显示"最近一份有档案的"——但其余一个都不能藏。"""
        older = self.fx.entity("阿灯")
        self.fx.set_created_at(older, "2026-01-01T00:00:00Z")
        source = self.fx.source()
        ref = self.fx.evidence_of(source, "他总在夜里修灯", subjects=[older])
        self.fx.profile(older, traits=[self.fx.item("t1", "耐得住夜", [ref])])
        newer = self.fx.entity("阿灯")  # 后建、无档案
        self.fx.set_created_at(newer, "2026-04-01T00:00:00Z")

        chosen, others = unwrap(resolve_entity(self.fx.read(), "阿灯"))
        self.assertEqual(chosen.entity_id, older)
        self.assertEqual([e.entity_id for e in others], [newer])

        # 不要求档案时，规则退化为"取最新"
        chosen2, _ = unwrap(resolve_entity(
            self.fx.read(), "阿灯", need_profile=False))
        self.assertEqual(chosen2.entity_id, newer)


# ==========================================================================
# 二、人格档案报告
# ==========================================================================


class TestPersonaReport(FixtureCase):
    def _rich(self) -> dict:
        """一份覆盖到重点场景的档案：2 证据同源、单次行为、空分区、未并入 7 条。"""
        fx = self.fx
        hero = fx.entity("阿灯")
        other = fx.entity("老周")

        s1 = fx.source("中青报访谈", url="https://example.test/a")
        s2 = fx.source("图书馆档案", url="https://example.test/b")
        s3 = fx.source("考据贴", url="https://example.test/c")

        e1 = fx.evidence_of(s1, "他话少但看得准", subjects=[hero],
                            trait_tag="reserved",
                            quote="我没什么可说的。", locator={"field": "body",
                                                             "start": 3, "end": 18})
        e2 = fx.evidence_of(s1, "他说话留三分", subjects=[hero],
                            trait_tag="reserved")
        e3 = fx.evidence_of(s2, "他的话总带刺", subjects=[hero],
                            trait_tag="caustic")
        e4 = fx.evidence_of(s2, "他某次当众发火", subjects=[hero],
                            dimension=EvidenceDimension.EXPERIENCE)
        cross = fx.evidence_of(s3, "老周说他靠得住", subjects=[other])

        # 未并入：CANON_FACT 4 条 + RELATION 3 条
        for index in range(4):
            fx.evidence_of(s3, f"设定事实 {index}", subjects=[hero],
                           dimension=EvidenceDimension.CANON_FACT)
        for index in range(3):
            fx.evidence_of(s3, f"关系陈述 {index}", subjects=[hero],
                           dimension=EvidenceDimension.RELATION)

        fx.profile(
            hero,
            traits=[fx.item("t1", "寡言但观察力敏锐", [e1, e2])],
            habits=[fx.item("h1", "夜里一个人修灯", [e2])],
            expression_style=fx.section("说话简省，偶尔带刺。", [e3]),
            single_events=[fx.item("s1", "某次当众发火", [e4],
                                   stability=Stability.SINGLE)],
        )
        return {"hero": hero, "other": other, "cross": cross,
                "e1": e1, "e2": e2, "e3": e3}

    def test_sections_are_fixed_order_and_complete(self):
        ids = self._rich()
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        self.assertEqual([(s.key, s.title) for s in report.sections],
                         list(SECTION_TITLES))
        self.assertEqual([s.title for s in report.sections],
                         ["稳定性格", "习惯", "说话方式", "单次行为", "背景", "经历"])

    def test_empty_sections_have_no_items(self):
        ids = self._rich()
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        by_key = {s.key: s for s in report.sections}
        for key in ("background", "experiences"):
            self.assertEqual(by_key[key].items, [], key)
        self.assertEqual(len(by_key["traits"].items), 1)
        self.assertEqual(len(by_key["single_events"].items), 1)

    def test_source_count_dedupes_while_evidence_count_does_not(self):
        """2 条证据同源 → `2 条证据 / 1 个来源`。这是全层最容易写错的一处。"""
        ids = self._rich()
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        traits = {s.key: s for s in report.sections}["traits"].items[0]
        self.assertEqual(traits.evidence_count, 2)
        self.assertEqual(traits.source_count, 1)
        self.assertEqual(traits.evidence_ids, [ids["e1"], ids["e2"]])

    def test_referenced_counts_are_distinct_from_totals(self):
        ids = self._rich()
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        self.assertEqual(report.evidence_total, 11)   # 4 条被引用 + 7 条未并入
        self.assertEqual(report.referenced_evidence, 4)
        self.assertEqual(report.referenced_sources, 2)  # 中青报 + 图书馆
        self.assertEqual(report.source_total, 3)

    def test_unmerged_is_counted_by_dimension(self):
        ids = self._rich()
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        self.assertEqual(report.unmerged.total, 7)
        self.assertEqual(report.unmerged.by_dimension,
                         [("CANON_FACT", 4), ("RELATION", 3)])
        self.assertEqual(len(report.unmerged.rows), 7)
        self.assertTrue(all(row.claim.startswith(("设定事实", "关系陈述"))
                            for row in report.unmerged.rows))

    def test_missing_ref_is_reported_not_swallowed(self):
        """档案引用了"另一个人的证据"（本库有、但不属于她）→ 必须进 `missing_refs`。"""
        ids = self._rich()
        # 追加一个版本，把 cross 证据挂到阿灯身上（模块层只校验"存在"）
        self.fx.revise(
            ids["hero"],
            traits=[self.fx.item("t1", "寡言但观察力敏锐", [ids["e1"]]),
                    self.fx.item("t2", "别人眼里的靠得住", [ids["cross"]])],
        )
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        self.assertEqual(report.profile_version, 2)
        self.assertEqual(report.missing_refs, [ids["cross"]])

    def test_entity_without_profile_is_not_an_error(self):
        entity_id = self.fx.entity("只有实体没有档案")
        report = unwrap(persona_report(self.fx.read(), entity_id))
        self.assertTrue(report.profile_missing)
        self.assertEqual(report.status, "MISSING")
        self.assertIsNone(report.profile_version)
        self.assertEqual(report.sections, [])

    def test_entity_without_profile_keeps_all_evidence_unmerged(self):
        entity_id = self.fx.entity("半成品")
        source = self.fx.source()
        self.fx.evidence_of(source, "背景一", subjects=[entity_id],
                            dimension=EvidenceDimension.BACKGROUND)
        self.fx.evidence_of(source, "设定一", subjects=[entity_id],
                            dimension=EvidenceDimension.CANON_FACT)
        report = unwrap(persona_report(self.fx.read(), entity_id))
        self.assertEqual(report.evidence_total, 2)
        self.assertEqual(report.unmerged.total, 2)
        self.assertEqual(report.referenced_evidence, 0)

    def test_open_questions_survive(self):
        entity_id = self.fx.entity("留白角色")
        self.fx.profile(entity_id, open_questions=["没有任何性格类证据"],
                        status=ProfileStatus.INSUFFICIENT_EVIDENCE)
        report = unwrap(persona_report(self.fx.read(), entity_id))
        self.assertEqual(report.open_questions, ["没有任何性格类证据"])
        self.assertEqual(report.status, str(ProfileStatus.INSUFFICIENT_EVIDENCE))

    def test_text_sections_are_flagged(self):
        ids = self._rich()
        report = unwrap(persona_report(self.fx.read(), ids["hero"]))
        by_key = {s.key: s for s in report.sections}
        self.assertTrue(by_key["expression_style"].is_text)
        self.assertTrue(by_key["background"].is_text)
        self.assertFalse(by_key["traits"].is_text)

    def test_same_name_candidates_travel_with_the_report(self):
        older = self.fx.entity("阿灯")
        self.fx.set_created_at(older, "2026-01-01T00:00:00Z")
        source = self.fx.source()
        ref = self.fx.evidence_of(source, "他总在夜里修灯", subjects=[older])
        self.fx.profile(older, traits=[self.fx.item("t1", "耐得住夜", [ref])])
        newer = self.fx.entity("阿灯")
        self.fx.set_created_at(newer, "2026-04-01T00:00:00Z")

        report = unwrap(persona_report(self.fx.read(), "阿灯"))
        self.assertEqual(report.entity.entity_id, older)
        self.assertEqual([e.entity_id for e in report.other_entities], [newer])
        self.assertFalse(report.chosen_is_latest)  # older 不是最新的那一份

    def test_newest_profile_marks_chosen_latest(self):
        older = self.fx.entity("阿灯")
        self.fx.set_created_at(older, "2026-01-01T00:00:00Z")
        source = self.fx.source()
        ref = self.fx.evidence_of(source, "他总在夜里修灯", subjects=[older])
        self.fx.profile(older, traits=[self.fx.item("t1", "耐得住夜", [ref])])
        newer = self.fx.entity("阿灯")
        self.fx.set_created_at(newer, "2026-04-01T00:00:00Z")
        ref2 = self.fx.evidence_of(source, "他也修钟", subjects=[newer])
        self.fx.profile(newer, traits=[self.fx.item("t1", "手上有活", [ref2])])

        report = unwrap(persona_report(self.fx.read(), "阿灯"))
        self.assertEqual(report.entity.entity_id, newer)
        self.assertTrue(report.chosen_is_latest)
        self.assertEqual([e.entity_id for e in report.other_entities], [older])

    def test_version_can_be_pinned(self):
        entity_id = self.fx.entity("阿灯")
        source = self.fx.source()
        ref = self.fx.evidence_of(source, "他总在夜里修灯", subjects=[entity_id])
        self.fx.profile(entity_id, traits=[self.fx.item("t1", "耐得住夜", [ref])])
        self.fx.revise(entity_id,
                       traits=[self.fx.item("t1", "耐得住夜（改）", [ref])])
        latest = unwrap(persona_report(self.fx.read(), entity_id))
        pinned = unwrap(persona_report(self.fx.read(), entity_id, version=1))
        self.assertEqual(latest.profile_version, 2)
        self.assertEqual(pinned.profile_version, 1)
        self.assertEqual(pinned.sections[0].items[0].text, "耐得住夜")


# ==========================================================================
# 三、关系档案报告
# ==========================================================================


class TestRelationshipReport(FixtureCase):
    def _pair(self) -> dict:
        fx = self.fx
        a = fx.entity("阿灯")
        b = fx.entity("老周")
        source = fx.source("双人访谈", url="https://example.test/pair")
        ref = fx.evidence_of(source, "访谈里阿灯多次提到老周靠得住", subjects=[a, b],
                             dimension=EvidenceDimension.RELATION)
        draft = RelationshipDraft(
            participant_entity_ids=[a, b],
            cardinality=Cardinality.PAIR,
            perspectives={
                a: PerspectiveBlock(
                    entity_id=a,
                    view_of_targets={b: [fx.item("v_ab", "阿灯觉得老周靠得住", [ref])]},
                    feeling_toward=[fx.item("f_ab", "对老周有信任感", [ref])]),
                b: PerspectiveBlock(
                    entity_id=b,
                    view_of_targets={a: [fx.item("v_ba", "老周觉得阿灯肯吃苦", [ref])]},
                    expression_toward=[fx.item("e_ba", "对阿灯说话直接", [ref])]),
            },
            shared_experiences=[],
            asymmetry_notes=[fx.item("n_1", "阿灯把老周当长辈，老周把他当同伴", [ref])],
            applicable_variant_ids=[a, b],
            status=ProfileStatus.OK,
        )
        unwrap(fx.relationship_service.create_relationship(fx.ctx, draft))
        return {"a": a, "b": b, "ref": ref}

    def test_both_directions_are_listed_separately(self):
        ids = self._pair()
        report = unwrap(relationship_report(self.fx.read(), ids["a"], ids["b"]))
        pairs = sorted((p.from_name, p.to_name) for p in report.perspectives)
        self.assertEqual(pairs, [("老周", "阿灯"), ("阿灯", "老周")])
        self.assertEqual(report.cardinality, str(Cardinality.PAIR))
        self.assertEqual(report.status, str(ProfileStatus.OK))

    def test_perspectives_carry_names_not_ids(self):
        ids = self._pair()
        report = unwrap(relationship_report(self.fx.read(), ids["a"], ids["b"]))
        for perspective in report.perspectives:
            self.assertTrue(perspective.from_name)
            self.assertTrue(perspective.to_name)
            self.assertFalse(perspective.from_name.startswith("pe_"))
        self.assertEqual([p.entity_id for p in report.participants], [ids["a"], ids["b"]])

    def test_counts_and_asymmetry(self):
        ids = self._pair()
        report = unwrap(relationship_report(self.fx.read(), ids["a"], ids["b"]))
        self.assertEqual(len(report.asymmetry_notes), 1)
        self.assertEqual(report.asymmetry_notes[0].source_count, 1)
        self.assertEqual(report.shared_experiences, [])
        self.assertEqual(report.interaction_patterns, [])
        self.assertEqual(report.missing_refs, [])

    def test_unknown_participant_is_not_found(self):
        ids = self._pair()
        result = relationship_report(self.fx.read(), ids["a"], "查无此人")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_missing_relationship_is_an_error_not_an_empty_report(self):
        a = self.fx.entity("阿灯")
        b = self.fx.entity("老周")
        result = relationship_report(self.fx.read(), a, b)
        self.assertTrue(is_err(result))


# ==========================================================================
# 四、证据报告
# ==========================================================================


class TestEvidenceReport(FixtureCase):
    def _hero(self) -> dict:
        fx = self.fx
        hero = fx.entity("阿灯")
        s1 = fx.source("中青报访谈", url="https://example.test/a",
                       retrieved_at="2026-01-02T03:04:05Z")
        s2 = fx.source("考据贴", url="https://example.test/c")
        e1 = fx.evidence_of(s1, "他话少但看得准", subjects=[hero],
                            trait_tag="reserved", quote="我没什么可说的。",
                            locator={"field": "body", "start": 3, "end": 18},
                            tags=["TRAIT_TAG_DIMENSION_MISMATCH"])
        e2 = fx.evidence_of(s2, "某次当众发火", subjects=[hero],
                            dimension=EvidenceDimension.EXPERIENCE,
                            trait_tag="caustic")
        e3 = fx.evidence_of(s2, "设定事实一", subjects=[hero],
                            dimension=EvidenceDimension.CANON_FACT)
        fx.profile(hero, traits=[fx.item("t1", "寡言但观察力敏锐", [e1])])
        return {"hero": hero, "e1": e1, "e2": e2, "e3": e3}

    def test_groups_are_split_into_section_and_item(self):
        ids = self._hero()
        report = unwrap(evidence_report(self.fx.read(), ids["hero"]))
        self.assertEqual(len(report.groups), 1)
        group = report.groups[0]
        self.assertEqual(group.section, "稳定性格")
        self.assertEqual(group.title, "寡言但观察力敏锐")
        self.assertEqual(group.evidence_count, 1)
        self.assertEqual(group.source_count, 1)
        self.assertEqual([row.evidence_id for row in group.items], [ids["e1"]])

    def test_unmerged_holds_only_unreferenced_evidence(self):
        ids = self._hero()
        report = unwrap(evidence_report(self.fx.read(), ids["hero"]))
        # 顺序不是这一层的承诺（存储默认按 created_at 排序，同秒就按行序），
        # 承诺的是**内容**：被引用的那条不在这里，没被引用的两条都在。
        self.assertEqual({row.evidence_id for row in report.unmerged},
                         {ids["e2"], ids["e3"]})
        self.assertNotIn(ids["e1"], {row.evidence_id for row in report.unmerged})

    def test_rows_carry_source_url_and_locator(self):
        ids = self._hero()
        report = unwrap(evidence_report(self.fx.read(), ids["hero"]))
        row = report.groups[0].items[0]
        self.assertEqual(row.source_title, "中青报访谈")
        self.assertEqual(row.source_url, "https://example.test/a")
        self.assertEqual(row.quote, "我没什么可说的。")
        self.assertEqual(row.locator, "body[3:18]")
        self.assertEqual(row.retrieved_at, "2026-01-02T03:04:05Z")
        self.assertEqual(row.flags, ["TRAIT_TAG_DIMENSION_MISMATCH"])

    def test_trait_filter_keeps_only_matching_rows(self):
        ids = self._hero()
        report = unwrap(evidence_report(self.fx.read(), ids["hero"], trait="caustic"))
        self.assertEqual([row.evidence_id for row in report.unmerged], [ids["e2"]])
        self.assertEqual(report.groups, [])
        self.assertEqual(report.trait_filter, "caustic")

    def test_unknown_trait_lists_available_tags(self):
        """查不到标签不是错误：把可用标签列出来，让用户能改一个字重跑。"""
        ids = self._hero()
        report = unwrap(evidence_report(self.fx.read(), ids["hero"], trait="nope"))
        self.assertEqual(report.groups, [])
        self.assertEqual(report.unmerged, [])
        self.assertEqual(dict(report.available_tags), {"reserved": 1, "caustic": 1})

    def test_entity_without_profile_puts_everything_in_unmerged(self):
        entity_id = self.fx.entity("只有证据")
        source = self.fx.source()
        self.fx.evidence_of(source, "事实一", subjects=[entity_id],
                            dimension=EvidenceDimension.CANON_FACT)
        report = unwrap(evidence_report(self.fx.read(), entity_id))
        self.assertTrue(report.profile_missing)
        self.assertEqual(report.groups, [])
        self.assertEqual(len(report.unmerged), 1)

    def test_totals_count_distinct_sources(self):
        ids = self._hero()
        report = unwrap(evidence_report(self.fx.read(), ids["hero"]))
        self.assertEqual(report.evidence_total, 3)
        self.assertEqual(report.source_total, 2)


# ==========================================================================
# 五、只读栈本身：不建库、不写库
# ==========================================================================


class TestReadStackBoundary(unittest.TestCase):
    def test_missing_database_is_an_error_and_stays_missing(self):
        with tempfile.TemporaryDirectory(prefix="gps-p51-") as tmp:
            path = Path(tmp) / "nope.db"
            result = open_read_services(path, project_id=PROJECT)
            self.assertTrue(is_err(result))
            self.assertEqual(result.error.code, ErrorCode.STORAGE_READ_FAILED)
            self.assertFalse(path.exists(), "只读栈不许顺手建库")

    def test_open_existing_database_is_ok(self):
        fx = _fixture()
        try:
            services = fx.read()
            self.assertTrue(is_ok(open_read_services(fx.path, project_id=PROJECT)))
            self.assertEqual(services.database_path, fx.path)
        finally:
            fx.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
