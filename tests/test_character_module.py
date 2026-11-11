"""Character 模块测试（P8.1-B：故事人物实例与认知模型）。

全部跑在**临时目录**的 SQLite 文件上，不碰任何正式项目库、不调外部服务、
零 AI 调用（对应 P8.1-A-R 修订版 §6.3 A 类用例 T1~T20）。

覆盖：
  1. 实例 CRUD（story_id 格式、同故事同人去重、身份字段不可修订）
  2. namespace 闸门（CharacterInstance 只收 SIMULATED——存储层双保险）
  3. BehaviorPolicy 装配（single_events/SINGLE 排除、个人/关系分离、
     无 trigger/action/strength 键、可重算、关系对象信息保留）
  4. KnowledgeState（TOLD 溯源必填、INFERRED 依据必填、双可信度独立、
     修正链、故事隔离、SECRET 结构性单持有者）
  5. LatentRelation（origin_refs ≥1、双向独立分实例存储）
  6. OOC 基础四检查（K1 集合比对 / S1 只报疑似 / I1 BLOCKED-TOLERATED 边界 /
     T1 经历时序）
  7. 防污染端到端（写 100 条知识后证据表计数不变、namespace 全 SIMULATED）
  8. 字段名守卫（FORBIDDEN_FIELD_PATTERN 经 run_static_checks 全模块扫描零命中）
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import ProvenanceNamespace, Stability
from persona.core.ids import new_id
from persona.core.result import is_err, is_ok
from persona.modules.character import (
    K_INFERRED,
    K_OBSERVED,
    K_SECRET,
    K_TOLD,
    OOC_BLOCKED,
    OOC_FLAGGED,
    OOC_OK,
    OOC_SUSPECTED_LEAK,
    OOC_TOLERATED,
    BehaviorRule,
    Belief,
    CharacterInstanceDraft,
    CharacterService,
    EmotionalState,
    Goal,
    KnowledgeEntry,
    LatentRelation,
    StoryEvent,
    build_behavior_policy,
    ooc_check,
    ooc_check_experiences,
    ooc_check_identity,
    ooc_check_knowledge_boundary,
    ooc_check_secret_leak,
)
from persona.modules.evaluation import EvaluationService
from persona.modules.persona import (
    ProfileItem,
    ProfileSection,
    PersonaProfileView,
    CanonBoundary,
)
from persona.modules.relationship import (
    PerspectiveBlock,
    RelationshipView,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_character_test"


def _story_id() -> str:
    return new_id("Story")


def _profile(entity_id: str = "pe_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c60",
             version: int = 1) -> PersonaProfileView:
    """最小 PersonaProfileView（直接构造，走 PersonaService 的校验属另一模块测试）。"""
    trait = lambda iid, text, st: ProfileItem(   # noqa: E731
        item_id=iid, text=text, confidence=0.8,
        evidence_refs=[f"evd_{iid}"], stability=st)
    return PersonaProfileView(
        id=f"pnf_{entity_id[3:]}v{version}", entity_id=entity_id,
        version=version, namespace="SYSTEM_DERIVED",
        background=ProfileSection(text="出身将门", confidence=0.9,
                                  evidence_refs=["evd_bg"]),
        traits=[trait("ti_pat", "极度谨慎，谋定后动", Stability.PATTERN),
                trait("ti_rep", "偶尔冲动失言", Stability.REPEATED),
                trait("ti_single", "某次宴上失态", Stability.SINGLE)],
        habits=[ProfileItem(item_id="hb1", text="遇冲突先观察",
                            evidence_refs=["evd_hb1"])],
        expression_style=ProfileSection(text="言简意赅", confidence=0.7,
                                        evidence_refs=["evd_exp"]),
        experiences=[], single_events=[ProfileItem(item_id="se1",
                                                   text="单次行为，不得入策略")],
        canon_boundary=CanonBoundary(), applicable_variant_ids=[],
        do_not_apply_to_variants=[], controversial_topic_ids=[],
        open_questions=["values 未定"], coverage={}, status="OK",
        superseded_by=None, change_reason="")


def _relationship(entity_id: str = "pe_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c60",
                  other_id: str = "pe_ffff0000111122223333444455556666",
                  ) -> RelationshipView:
    block = PerspectiveBlock(
        entity_id=entity_id,
        view_of_targets={other_id: [ProfileItem(item_id="vt1",
                                                text="认为对方城府极深")]},
        feeling_toward=[ProfileItem(item_id="ft1", text="对其既敬且忌")],
        expression_toward=[ProfileItem(item_id="et1", text="对其措辞客套")])
    return RelationshipView(
        id="rlp_test1", participant_entity_ids=[entity_id, other_id],
        cardinality="PAIR", namespace="SYSTEM_DERIVED", version=1,
        perspectives={entity_id: block}, shared_experiences=[],
        interaction_patterns=[ProfileItem(item_id="ip1", text="与其交锋时先试探",
                                          evidence_refs=["evd_ip1"])],
        emotional_expression_habits=[], temporal_changes=[],
        group_dynamics=None, asymmetry_notes=[], conflict_ids=[],
        applicable_variant_ids=[], open_questions=[], coverage={},
        status="OK", superseded_by=None, change_reason="")


class CharacterFixture:
    """一个临时数据库 + CharacterService + 调用上下文。用完即关。"""

    def __init__(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="persona_character_test_")
        self.storage = SqliteStorage(Path(self.tmp) / "persona.db")
        self.service = CharacterService(self.storage)
        self.evaluation = EvaluationService(
            self.storage, package_root=self._package_root())
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        is_ok(self.storage.init_schema(self.ctx))

    def _package_root(self) -> Path:
        import persona
        return Path(persona.__file__).parent

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def draft(self, story_id: str | None = None,
              character_id: str = "pe_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c60",
              **overrides) -> CharacterInstanceDraft:
        # ⚠️ story_id 用 None 判断（不能用 or）——空串是合法的待校验输入
        d = CharacterInstanceDraft(
            story_id=_story_id() if story_id is None else story_id,
            character_id=character_id,
            profile_version=1, display_name="测试角色")
        for key, value in overrides.items():
            setattr(d, key, value)
        return d

    def create(self, story_id: str | None = None, **overrides):
        result = self.service.create_instance(self.ctx, self.draft(story_id, **overrides),
                                              PROJECT)
        assert is_ok(result), f"fixture 创建实例失败: {result.error.message if is_err(result) else ''}"
        return result.value


class TestInstanceCrud(unittest.TestCase):
    """T1~T3 + 修订约束。"""

    def setUp(self):
        self.f = CharacterFixture()
        self.addCleanup(self.f.close)

    def test_t1_duplicate_instance_rejected(self):
        """T1：同 (story_id, character_id) 第二次创建 → Err(STATE_CONFLICT)。"""
        story = _story_id()
        first = self.f.create(story)
        self.assertEqual(first.story_id, story)
        dup = self.f.service.create_instance(self.f.ctx, self.f.draft(story), PROJECT)
        self.assertTrue(is_err(dup))
        # 同 persona.create_profile 先例：已存在 → STATE_CONFLICT.no_new_evidence
        self.assertEqual(dup.error.code, "STATE_CONFLICT.no_new_evidence")

    def test_t2_namespace_gate_simulated_only(self):
        """T2：存储层闸门——CharacterInstance 只收 SIMULATED。"""
        put = self.f.storage.put(
            self.f.ctx, "CharacterInstance", {"story_id": "x"},
            project_id=PROJECT, namespace=ProvenanceNamespace.SYSTEM_DERIVED)
        self.assertTrue(is_err(put))
        self.assertEqual(put.error.code, "VALIDATION.namespace_forbidden")

    def test_t3_story_id_format_enforced(self):
        """T3：story_id 必须 sty_ 前缀 + 32 位十六进制。"""
        for bad in ("css_b3a9", "sty_short", "sty_" + "z" * 32, "", "story_1"):
            result = self.f.service.create_instance(
                self.f.ctx, self.f.draft(bad), PROJECT)
            self.assertTrue(is_err(result), f"story_id={bad!r} 应被拒绝")

    def test_revise_immutable_identity_fields(self):
        """身份字段不可修订：换 story/角色/档案版本须创建新实例。"""
        inst = self.f.create()
        for field in ("story_id", "character_id", "profile_version"):
            patch = self.f.service.revise_instance(
                self.f.ctx, inst.id, {field: "hacked"}, "测试")
            self.assertTrue(is_err(patch), f"{field} 应不可修订")
        unknown = self.f.service.revise_instance(
            self.f.ctx, inst.id, {"nonexistent": 1}, "测试")
        self.assertTrue(is_err(unknown))
        no_reason = self.f.service.revise_instance(
            self.f.ctx, inst.id, {"display_name": "改名"}, "")
        self.assertTrue(is_err(no_reason))


class TestBehaviorPolicy(unittest.TestCase):
    """T4~T7：装配纪律。"""

    def setUp(self):
        self.f = CharacterFixture()
        self.addCleanup(self.f.close)
        self.profile = _profile()
        self.policy = build_behavior_policy(self.profile, [_relationship()]).value

    def test_t4_single_behavior_excluded(self):
        """T4：single_events 板块与 SINGLE 稳定性条目不进任何策略板块。"""
        all_texts = [r.text for r in self.policy.all_rules()]
        self.assertNotIn("单次行为，不得入策略", all_texts)
        self.assertNotIn("某次宴上失态", all_texts)
        # PATTERN 进 core，REPEATED 进 situational
        self.assertIn("极度谨慎，谋定后动",
                      [r.text for r in self.policy.core_dispositions])
        self.assertIn("偶尔冲动失言",
                      [r.text for r in self.policy.situational_patterns])

    def test_t5_no_if_then_or_strength_keys(self):
        """T5：BehaviorRule 无 trigger/action/strength 键（防 IF-THEN 回潮）。"""
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(BehaviorRule)}
        for banned in ("trigger", "action", "strength", "score", "closeness",
                       "intimacy"):
            self.assertNotIn(banned, field_names)
        # 序列化形状同样无这些键
        rule = self.policy.core_dispositions[0]
        as_dict = vars(rule)
        for banned in ("trigger", "action", "strength"):
            self.assertNotIn(banned, as_dict)

    def test_t6_relational_patterns_keep_target(self):
        """T6：关系行为按 target_id 分键、只含本人单向视角、对象信息不丢。"""
        other = "pe_ffff0000111122223333444455556666"
        self.assertIn(other, self.policy.relational_patterns)
        rules = self.policy.relational_patterns[other]
        texts = [r.text for r in rules]
        # 单向视角三板块 + interaction_patterns 全部到位
        self.assertIn("认为对方城府极深", texts)
        self.assertIn("对其既敬且忌", texts)
        self.assertIn("与其交锋时先试探", texts)
        for r in rules:
            self.assertEqual(r.target_id, other)
            self.assertEqual(r.direction, "self_to_target")
        # 对方视角不出现在本人的策略里
        other_view = "对方视角专属文本"
        self.assertNotIn(other_view, texts)

    def test_t7_policy_pure_and_recomputable(self):
        """T7：装配是纯函数——不落库、随档案版本重算。"""
        self.assertEqual(self.policy.entity_id, self.profile.entity_id)
        self.assertEqual(self.policy.profile_version, 1)
        v2 = build_behavior_policy(_profile(version=2), []).value
        self.assertEqual(v2.profile_version, 2)
        # 装配产物不写库：CharacterInstance 记录数为 0
        page = self.f.storage.query(self.f.ctx, "CharacterInstance",
                                    Query_all())
        self.assertEqual(page.value.total, 0)
        # NOT IMPLEMENTED 缺口如实进 open_questions
        self.assertTrue(any("NOT IMPLEMENTED" in q for q in v2.open_questions))


def Query_all():
    from persona.core.types import Query
    return Query(filters={}, limit=200)


class TestKnowledgeState(unittest.TestCase):
    """T8~T12：知识写入校验、修正链、隔离。"""

    def setUp(self):
        self.f = CharacterFixture()
        self.addCleanup(self.f.close)
        self.inst = self.f.create()

    def _entry(self, **overrides) -> KnowledgeEntry:
        kw = dict(id=new_id("KnowledgeEntry"), content="账本在晚上失踪了",
                  category=K_OBSERVED, about="world", source_ref="ctn_1",
                  acquired_turn=1, teller_credibility="HIGH",
                  content_credibility="HIGH")
        kw.update(overrides)
        return KnowledgeEntry(**kw)

    def test_t8_told_requires_source_and_teller(self):
        """T8：TOLD 必须可溯源到具体传播轮次/事件 + 讲述者（修订 4）。"""
        no_source = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_TOLD, source_ref="", teller_id="pe_x"))
        self.assertTrue(is_err(no_source))
        no_teller = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_TOLD, source_ref="ctn_2", teller_id=""))
        self.assertTrue(is_err(no_teller))
        ok = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_TOLD, source_ref="ctn_2",
                        teller_id="pe_ffff0000111122223333444455556666",
                        teller_credibility="LOW", content_credibility="LOW"))
        self.assertTrue(is_ok(ok))
        # 非 TOLD 不允许填 teller_id
        bad_teller = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_OBSERVED, source_ref="ctn_3",
                        teller_id="pe_x"))
        self.assertTrue(is_err(bad_teller))

    def test_t9_inferred_requires_basis(self):
        """T9：INFERRED 无推理依据 → 拒写（EV-V3 精神）。"""
        result = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_INFERRED, source_ref=""))
        self.assertTrue(is_err(result))
        ok = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_INFERRED, source_ref="ctn_1"))
        self.assertTrue(is_ok(ok))

    def test_t10_correction_chain(self):
        """T10：修正链——旧条 CORRECTED + superseded_by 指向新 ACTIVE 条；
        只有 ACTIVE 条目可被修正。"""
        entry = self._entry(id="kn_fixed0000000000000000000000")
        self.assertTrue(is_ok(self.f.service.add_knowledge(
            self.f.ctx, self.inst.id, entry)))
        revised = self.f.service.correct_knowledge(
            self.f.ctx, self.inst.id, entry.id, "账本是被 B 拿走的",
            acquired_turn=5)
        self.assertTrue(is_ok(revised))
        entries = {e["id"]: e for e in revised.value.knowledge}
        self.assertEqual(entries[entry.id]["status"], "CORRECTED")
        new_id_ref = entries[entry.id]["superseded_by"]
        self.assertIn(new_id_ref, entries)
        self.assertEqual(entries[new_id_ref]["status"], "ACTIVE")
        # 已修正条目不可再修正
        again = self.f.service.correct_knowledge(
            self.f.ctx, self.inst.id, entry.id, "再改一次", acquired_turn=6)
        self.assertTrue(is_err(again))
        # 不存在的条目
        missing = self.f.service.correct_knowledge(
            self.f.ctx, self.inst.id, "kn_nope", "无", acquired_turn=7)
        self.assertTrue(is_err(missing))

    def test_t11_dual_credibility_independent(self):
        """T11：讲述者可信度与内容可信度分开校验、分开存储、不合并。"""
        bad_teller = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(teller_credibility="VERY_HIGH"))
        self.assertTrue(is_err(bad_teller))
        bad_content = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(content_credibility="75%"))
        self.assertTrue(is_err(bad_content))
        ok = self.f.service.add_knowledge(
            self.f.ctx, self.inst.id,
            self._entry(category=K_TOLD, source_ref="ctn_9",
                        teller_id="pe_ffff0000111122223333444455556666",
                        teller_credibility="HIGH",      # 老实人
                        content_credibility="LOW"))     # 讲的却是谣言
        self.assertTrue(is_ok(ok))
        stored = [e for e in ok.value.knowledge if e["id"]][-1]
        self.assertEqual(stored["teller_credibility"], "HIGH")
        self.assertEqual(stored["content_credibility"], "LOW")

    def test_t12_story_isolation_and_secret_single_holder(self):
        """T12：story1 的知识对 story2 不可见；SECRET 只存在于持有人实例。"""
        story_a, story_b = _story_id(), _story_id()
        secret = self._entry(id="kn_secret0000000000000000000000",
                             category=K_SECRET, content="我是内鬼",
                             source_ref="canon:开局", acquired_turn=0,
                             teller_credibility="HIGH",
                             content_credibility="HIGH", keywords=["内鬼"])
        self.assertTrue(is_ok(self.f.service.create_instance(
            self.f.ctx, self.f.draft(story_a, knowledge=[secret]), PROJECT)))
        holder_view = self.f.service.query_instances_by_story(
            self.f.ctx, story_a, PROJECT).value[0]
        self.assertEqual(len(holder_view.knowledge), 1)
        # B（同故事另一角色）的实例不携带 A 的 SECRET——结构性单持有者
        other = self.f.service.create_instance(
            self.f.ctx, self.f.draft(
                story_a, character_id="pe_ffff0000111122223333444455556666"),
            PROJECT)
        self.assertTrue(is_ok(other))
        self.assertEqual(other.value.knowledge, [])
        # load_knowledge 按角色隔离
        a_knowledge = self.f.service.load_knowledge(
            self.f.ctx, story_a, "pe_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c60", PROJECT)
        b_knowledge = self.f.service.load_knowledge(
            self.f.ctx, story_a, "pe_ffff0000111122223333444455556666", PROJECT)
        self.assertEqual(len(a_knowledge.value), 1)
        self.assertEqual(b_knowledge.value, [])
        # 跨故事完全不可见
        b_story = self.f.service.query_instances_by_story(
            self.f.ctx, story_b, PROJECT)
        self.assertTrue(is_ok(b_story))
        self.assertEqual(b_story.value, [])


class TestLatentRelation(unittest.TestCase):
    """T19~T20：关系看法写入校验与双向独立。"""

    def setUp(self):
        self.f = CharacterFixture()
        self.addCleanup(self.f.close)
        self.a_id = "pe_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c60"
        self.b_id = "pe_ffff0000111122223333444455556666"
        self.a = self.f.create(character_id=self.a_id)
        self.b = self.f.create(character_id=self.b_id)

    def test_t19_relation_requires_story_evidence(self):
        """T19：origin_refs 为空 → 拒写（禁止无依据换算 feeling_toward）。"""
        result = self.f.service.set_relation(
            self.f.ctx, self.a.id,
            LatentRelation(target_id=self.b_id, trust="WARM", stance="ALLY",
                           origin_refs=[]))
        self.assertTrue(is_err(result))
        for bad_trust in ("NEUTRAL+", 0.8, "好感度 75"):
            result = self.f.service.set_relation(
                self.f.ctx, self.a.id,
                LatentRelation(target_id=self.b_id, trust=str(bad_trust),
                               origin_refs=["ctn_1"]))
            self.assertTrue(is_err(result), f"trust={bad_trust!r} 应被拒绝")

    def test_t20_bidirectional_views_independent(self):
        """T20：A 对 B 与 B 对 A 分别存在各自实例中，互不读写、不合并。"""
        rev_a = self.f.service.set_relation(
            self.f.ctx, self.a.id,
            LatentRelation(target_id=self.b_id, trust="WARM", stance="ALLY",
                           origin_refs=["ctn_3"]))
        rev_b = self.f.service.set_relation(
            self.f.ctx, self.b.id,
            LatentRelation(target_id=self.a_id, trust="DISTRUST",
                           stance="RIVAL", origin_refs=["ctn_3", "ctn_4"]))
        self.assertTrue(is_ok(rev_a) and is_ok(rev_b))
        a_state = rev_a.value.relationship_state
        b_state = rev_b.value.relationship_state
        self.assertEqual(len(a_state), 1)
        self.assertEqual(a_state[0]["target_id"], self.b_id)
        self.assertEqual(a_state[0]["trust"], "WARM")
        self.assertEqual(len(b_state), 1)
        self.assertEqual(b_state[0]["target_id"], self.a_id)
        self.assertEqual(b_state[0]["trust"], "DISTRUST")
        # 更新走覆盖同 target（不重复追加）
        rev_a2 = self.f.service.set_relation(
            self.f.ctx, self.a.id,
            LatentRelation(target_id=self.b_id, trust="DISTRUST",
                           stance="RIVAL", origin_refs=["ctn_5"]))
        self.assertEqual(len(rev_a2.value.relationship_state), 1)


class TestOocChecks(unittest.TestCase):
    """T13~T16 + 组合：OOC 基础四检查。"""

    def setUp(self):
        self.policy = build_behavior_policy(_profile(), [_relationship()]).value

    def test_t13_k1_knowledge_boundary(self):
        """T13：提案引用未持有的知识 → FLAGGED（集合比对）。"""
        knowledge = [{"id": "kn_have1", "status": "ACTIVE"}]
        report = ooc_check_knowledge_boundary(
            {"knowledge_refs": ["kn_other"]}, knowledge)
        self.assertEqual(report.verdict, OOC_FLAGGED)
        ok = ooc_check_knowledge_boundary({"knowledge_refs": ["kn_have1"]},
                                          knowledge)
        self.assertEqual(ok.verdict, OOC_OK)
        # 无结构化引用 → 如实注明不覆盖自由文本
        none_report = ooc_check_knowledge_boundary({}, knowledge)
        self.assertEqual(none_report.verdict, OOC_OK)
        self.assertTrue("不覆盖自由文本" in none_report.findings[0]["detail"])

    def test_t14_s1_secret_leak_suspected_only(self):
        """T14：非持有者命中秘密关键词 → SUSPECTED_LEAK（只报疑似）；
        持有者同样的文本对外部秘密集为空 → OK；绝不因命中而 BLOCKED。"""
        secret = {"id": "kn_secret1", "status": "ACTIVE",
                  "category": K_SECRET, "content": "我偷走了账本",
                  "keywords": ["账本"]}
        report = ooc_check_secret_leak("听说账本被人拿走了？", [secret])
        self.assertEqual(report.verdict, OOC_SUSPECTED_LEAK)
        holder_report = ooc_check_secret_leak("听说账本被人拿走了？", [])
        self.assertEqual(holder_report.verdict, OOC_OK)
        # 组合检查里也不升格为 BLOCKED
        combined = ooc_check({"text": "听说账本被人拿走了？"}, [secret], [],
                             self.policy)
        self.assertNotEqual(combined.verdict, OOC_BLOCKED)

    def test_t15_i1_blocked_vs_tolerated_boundary(self):
        """T15：违背身份事实无解释 → BLOCKED；附可追溯 motivation → TOLERATED；
        光有文字没有出处仍 BLOCKED；自报错引按缺失处理。"""
        # 无解释 → BLOCKED
        blocked = ooc_check_identity({"against_policy_item_ids": ["ti_pat"]},
                                     self.policy)
        self.assertEqual(blocked.verdict, OOC_BLOCKED)
        # 有文字无出处 → 仍 BLOCKED（motivation_ref 不可追溯）
        no_ref = ooc_check_identity(
            {"against_policy_item_ids": ["ti_pat"],
             "motivation": "因为情况特殊"},
            self.policy)
        self.assertEqual(no_ref.verdict, OOC_BLOCKED)
        # motivation_ref 指向策略条目 → TOLERATED
        tolerated = ooc_check_identity(
            {"against_policy_item_ids": ["ti_pat"],
             "motivation": "挚友命悬一线，风险超过独立行动的承受范围",
             "motivation_ref": "ti_pat"},
            self.policy)
        self.assertEqual(tolerated.verdict, OOC_TOLERATED)
        # motivation_ref 指向提案声明依赖的知识条目同样可追溯
        tolerated_k = ooc_check_identity(
            {"against_policy_item_ids": ["ti_pat"],
             "knowledge_refs": ["kn_seen"],
             "motivation": "我亲眼确认了他不在场",
             "motivation_ref": "kn_seen"},
            self.policy)
        self.assertEqual(tolerated_k.verdict, OOC_TOLERATED)
        # 自报引用不存在的条目 → FLAGGED（不信任自报）
        bad_ref = ooc_check_identity(
            {"against_policy_item_ids": ["ti_ghost"]}, self.policy)
        self.assertEqual(bad_ref.verdict, OOC_FLAGGED)

    def test_t16_t1_experience_exists(self):
        """T16：提案引用不存在的经历 → BLOCKED（不能凭空想起没发生的事）。"""
        experiences = [{"event_id": "ev_story1"}]
        blocked = ooc_check_experiences({"experience_refs": ["ev_ghost"]},
                                        experiences)
        self.assertEqual(blocked.verdict, OOC_BLOCKED)
        ok = ooc_check_experiences({"experience_refs": ["ev_story1"]},
                                   experiences)
        self.assertEqual(ok.verdict, OOC_OK)


class TestAntiPollutionAndGuards(unittest.TestCase):
    """T17~T18：防污染端到端 + 字段名/依赖守卫。"""

    def setUp(self):
        self.f = CharacterFixture()
        self.addCleanup(self.f.close)

    def test_t17_evidence_table_untouched(self):
        """T17：写 100 条知识后，证据表计数不变（SIMULATED 永不进证据）。"""
        before = self._evidence_count()
        self.assertEqual(before, 0)
        entries = [KnowledgeEntry(
            id=new_id("KnowledgeEntry"), content=f"事件 {i}",
            category=K_OBSERVED, source_ref=f"ctn_{i}", acquired_turn=i,
            teller_credibility="HIGH", content_credibility="HIGH")
            for i in range(100)]
        inst = self.f.create(knowledge=entries)
        self.assertEqual(len(inst.knowledge), 100)
        self.assertEqual(self._evidence_count(), 0)
        # 全部实例行 namespace=SIMULATED
        page = self.f.storage.query(self.f.ctx, "CharacterInstance",
                                    Query_all())
        for row in page.value.items:
            self.assertEqual(row["namespace"], "SIMULATED")

    def test_t18_forbidden_fields_and_dependencies_clean(self):
        """T18：run_static_checks 对新模块零命中（禁字段 + 依赖规则 + 签名）。"""
        result = self.f.evaluation.run_static_checks(self.f.ctx)
        self.assertTrue(is_ok(result), result.error.message if is_err(result) else "")
        value = result.value
        self.assertEqual(value["forbidden_fields"], [],
                         f"禁字段违规：{value['forbidden_fields']}")
        self.assertEqual(value["dependency_violations"], [],
                         f"依赖违规：{value['dependency_violations']}")
        self.assertEqual(value["signature_violations"], [],
                         f"签名违规：{value['signature_violations']}")

    def _evidence_count(self) -> int:
        page = self.f.storage.query(self.f.ctx, "EvidenceItem", Query_all())
        return page.value.total


class TestP81CFixes(unittest.TestCase):
    """P8.1-C T21~T26：知识写入旁路堵死 + 分页取全 + 修正可信度参数化。"""

    def setUp(self):
        self.f = CharacterFixture()
        self.addCleanup(self.f.close)

    def _entry(self, **overrides) -> KnowledgeEntry:
        kw = dict(id=new_id("KnowledgeEntry"), content="账本在晚上失踪了",
                  category=K_OBSERVED, about="world", source_ref="ctn_1",
                  acquired_turn=1, teller_credibility="HIGH",
                  content_credibility="HIGH")
        kw.update(overrides)
        return KnowledgeEntry(**kw)

    def test_t21_duplicate_entry_id_rejected(self):
        """T21（F1）：同实例内知识条目 ID 必须唯一——重复追加被拒。"""
        inst = self.f.create()
        entry = self._entry(id="kn_dup000000000000000000000000")
        self.assertTrue(is_ok(self.f.service.add_knowledge(
            self.f.ctx, inst.id, entry)))
        dup = self.f.service.add_knowledge(self.f.ctx, inst.id, entry)
        self.assertTrue(is_err(dup))
        self.assertIn("重复", dup.error.message)
        # 不同 ID 正常追加不受影响
        self.assertTrue(is_ok(self.f.service.add_knowledge(
            self.f.ctx, inst.id, self._entry())))

    def test_t22_revise_knowledge_patch_validated(self):
        """T22（F2）：revise patch 注入 UNKNOWN 类别条目被拒——修订不是旁路。"""
        inst = self.f.create()
        patched = self.f.service.revise_instance(
            self.f.ctx, inst.id,
            {"knowledge": [{"id": "kn_x1", "content": "秘密", "category": "UNKNOWN",
                            "source_ref": "ctn_1", "teller_credibility": "HIGH",
                            "content_credibility": "HIGH", "status": "ACTIVE"}]},
            "测试注入")
        self.assertTrue(is_err(patched))
        self.assertIn("UNKNOWN", patched.error.message)
        # 注入非法可信度同样被拒
        bad_cred = self.f.service.revise_instance(
            self.f.ctx, inst.id,
            {"knowledge": [{"id": "kn_x2", "content": "谣言", "category": K_TOLD,
                            "source_ref": "ctn_2", "teller_id": "pe_ffff0000111122223333444455556666",
                            "teller_credibility": "VERY_HIGH", "content_credibility": "HIGH",
                            "status": "ACTIVE"}]},
            "测试注入")
        self.assertTrue(is_err(bad_cred))

    def test_t23_revise_knowledge_patch_chain_enforced(self):
        """T23（F2+F5）：revise patch 注入缺 superseded_by 的 CORRECTED 条被拒。"""
        inst = self.f.create()
        patched = self.f.service.revise_instance(
            self.f.ctx, inst.id,
            {"knowledge": [{"id": "kn_c1", "content": "旧说法", "category": K_OBSERVED,
                            "source_ref": "ctn_1", "teller_credibility": "HIGH",
                            "content_credibility": "HIGH",
                            "status": "CORRECTED"}]},
            "测试注入断链")
        self.assertTrue(is_err(patched))
        self.assertIn("superseded_by", patched.error.message)

    def test_t24_revise_relation_patch_validated(self):
        """T24（F2）：revise patch 注入 origin_refs=[] 的关系看法被拒。"""
        a = self.f.create()
        b = self.f.create(character_id="pe_ffff0000111122223333444455556666")
        patched = self.f.service.revise_instance(
            self.f.ctx, a.id,
            {"relationship_state": [{"target_id": b.character_id, "trust": "WARM",
                                     "stance": "ALLY", "origin_refs": []}]},
            "测试无依据换算")
        self.assertTrue(is_err(patched))
        self.assertIn("origin_refs", patched.error.message)
        # 注入非法 trust 档位同样被拒
        bad_trust = self.f.service.revise_instance(
            self.f.ctx, a.id,
            {"relationship_state": [{"target_id": b.character_id, "trust": 0.8,
                                     "stance": "ALLY", "origin_refs": ["ctn_1"]}]},
            "测试数值回潮")
        self.assertTrue(is_err(bad_trust))

    def test_t25_pagination_beyond_200(self):
        """T25（F3）：项目内实例超 200 后去重检查仍命中、按故事查询不漏。"""
        story = _story_id()
        ids = [f"pe_{i:032x}" for i in range(210)]
        for i, cid in enumerate(ids):
            self.f.create(story, character_id=cid)
        # 去重检查跨过截断边界仍然命中
        dup = self.f.service.create_instance(
            self.f.ctx, self.f.draft(story, character_id=ids[205]), PROJECT)
        self.assertTrue(is_err(dup))
        self.assertEqual(dup.error.code, "STATE_CONFLICT.no_new_evidence")
        # 按故事查询取全 210 条
        views = self.f.service.query_instances_by_story(
            self.f.ctx, story, PROJECT)
        self.assertTrue(is_ok(views))
        self.assertEqual(len(views.value), 210)

    def test_t26_correction_credibility_parameterized(self):
        """T26（F4）：修正可信度由调用方断言，默认 UNVERIFIED，不再硬编码 HIGH。"""
        inst = self.f.create()
        entry = self._entry(id="kn_fix000000000000000000000000")
        self.assertTrue(is_ok(self.f.service.add_knowledge(
            self.f.ctx, inst.id, entry)))
        revised = self.f.service.correct_knowledge(
            self.f.ctx, inst.id, entry.id, "账本是被 B 拿走的", acquired_turn=5)
        self.assertTrue(is_ok(revised))
        entries = {e["id"]: e for e in revised.value.knowledge}
        new_id_ref = entries[entry.id]["superseded_by"]
        self.assertEqual(entries[new_id_ref]["teller_credibility"], "UNVERIFIED")
        self.assertEqual(entries[new_id_ref]["content_credibility"], "UNVERIFIED")
        # 显式传入的可信度被尊重（且非法值被闸门拒绝）
        revised2 = self.f.service.correct_knowledge(
            self.f.ctx, inst.id, new_id_ref, "账本被 B 放回了原处",
            acquired_turn=9, teller_credibility="HIGH",
            content_credibility="MEDIUM")
        self.assertTrue(is_ok(revised2))
        entries2 = {e["id"]: e for e in revised2.value.knowledge}
        newest = entries2[new_id_ref]["superseded_by"]
        self.assertEqual(entries2[newest]["teller_credibility"], "HIGH")
        self.assertEqual(entries2[newest]["content_credibility"], "MEDIUM")
        bad = self.f.service.correct_knowledge(
            self.f.ctx, inst.id, newest, "再修一次", acquired_turn=11,
            teller_credibility="100%")
        self.assertTrue(is_err(bad))


if __name__ == "__main__":
    unittest.main()
