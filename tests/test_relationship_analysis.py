"""关系语义分析（G-4）单元测试。

覆盖用户要求的 8 类场景 + 可追踪性 / 重跑幂等 / 异常处理：

  1. 明确证据 → 互动模式被识别
  2. 仅有共同出现 → 不足以判定联盟/冷落/传话（不得伪造）
  3. A→B 与 B→A 方向不对称（Mock 精确控制）
  4. 矛盾来源 → 分别保留并关联 conflict_ids
  5. 时间变化 → 时间轴
  6. ≥3 人群体互动 → 明确标记才落板块
  7. 无 / 弱 / 无效证据引用 → 诚实留空 + open_questions，绝不伪造
  8. 重跑幂等 / 证据可追踪 / 异常（参与者 <2、空证据、Provider 占位）

**能力边界**：Rule / Mock 一律 `is_semantic_model=False`；本测试只验证
"确定性离线规则 + 证据可追踪 + 不伪造"，**绝不声称已具备真实语义模型的
语义理解能力**。
"""

import unittest

from persona.core.context import new_call_context
from persona.core.enums import Cardinality, EvidenceDimension, Stability
from persona.core.errors import ErrorCode
from persona.core.result import is_err
from persona.core.temporal import FuzzyDate
from persona.modules.evidence import EvidenceView
from persona.modules.relationship_analysis import (
    ANALYZE_METHOD_MOCK,
    ANALYZE_METHOD_RULE,
    MockRelationshipAnalyzer,
    ProviderRelationshipAnalyzer,
    RelationshipAnalysisItem,
    RelationshipAnalysisOutcome,
    RelationshipAnalysisPerspective,
    RelationshipAnalysisRequest,
    RuleRelationshipAnalyzer,
)


CTX = new_call_context(project_id="test-g4", actor="tests")

# 三个固定参与者
A, B, C = "ent_a", "ent_b", "ent_c"


def mk_ev(ev_id, claim, subjects, *, stability=Stability.REPEATED,
          conflict_ids=None, stated_at=None, dimension=EvidenceDimension.RELATION,
          tier="PROFILE_BACKGROUND"):
    """构造一条 RELATION 维度的 `EvidenceView`（仅填分析器用到的字段）。"""
    return EvidenceView(
        id=ev_id, claim=claim, tier=tier, dimension=dimension,
        namespace="PUBLIC_SOURCE", source_id=None, source_event_ids=[],
        subject_entity_ids=list(subjects), stability=stability, status="OK",
        conflict_ids=list(conflict_ids or []), stated_at=stated_at)


def _outcome_refs(outcome: RelationshipAnalysisOutcome) -> set[str]:
    """收集产出里所有条目的 evidence_refs（用于可追踪性断言）。"""
    refs: set[str] = set()
    for blk in (outcome.perspectives or {}).values():
        for lst in (blk.view_of_targets or {}).values():
            for it in lst:
                refs.update(it.evidence_refs)
        for it in (blk.feeling_toward or []):
            refs.update(it.evidence_refs)
        for it in (blk.expression_toward or []):
            refs.update(it.evidence_refs)
    for it in outcome.shared_experiences:
        refs.update(it.evidence_refs)
    for it in outcome.interaction_patterns:
        refs.update(it.evidence_refs)
    for it in outcome.emotional_expression_habits:
        refs.update(it.evidence_refs)
    for it in outcome.temporal_changes:
        refs.update(it.evidence_refs)
    for it in outcome.asymmetry_notes:
        refs.update(it.evidence_refs)
    gd = outcome.group_dynamics
    if gd is not None:
        for it in (gd.alliances or []):
            refs.update(it.evidence_refs)
        for it in (gd.exclusion or []):
            refs.update(it.evidence_refs)
        for it in (gd.relay or []):
            refs.update(it.evidence_refs)
    return refs


def _assert_no_forbidden_fields(test: unittest.TestCase, obj) -> None:
    """RL-V2 / D-048：任何产出形状都不许出现 score/closeness/intimacy。"""
    names = {f.name for f in __import__("dataclasses").fields(obj)}
    test.assertEqual(names & {"score", "closeness", "intimacy"}, set())


# ==========================================================================
# Rule 分析器：覆盖维度 + 红线
# ==========================================================================


class TestRuleAnalyzer(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = RuleRelationshipAnalyzer()

    def _analyze(self, participants, evidence, cardinality=Cardinality.PAIR):
        req = RelationshipAnalysisRequest(
            participant_entity_ids=participants, relation_evidence=evidence,
            cardinality=cardinality)
        return self.analyzer.analyze(CTX, req)

    # ---------------------------------------------------------- 1. 互动模式

    def test_clear_evidence_interaction_pattern_detected(self):
        ev = mk_ev("e1", "阿雾与阿灯每天在灯塔下交谈", [A, B],
                   stability=Stability.REPEATED)
        res = self._analyze([A, B], [ev])
        self.assertTrue(is_err(res) is False)
        o = res.value
        self.assertTrue(o.interaction_patterns,
                        "含重复标记+REPEATED 的 RELATION 证据应进入互动模式")
        self.assertIn("e1", o.interaction_patterns[0].evidence_refs)

    def test_single_behavior_not_treated_as_pattern(self):
        ev = mk_ev("e1", "阿雾与阿灯某天在灯塔下交谈", [A, B],
                   stability=Stability.SINGLE)
        o = self._analyze([A, B], [ev]).value
        self.assertEqual(o.interaction_patterns, [],
                         "单次行为不得固化为互动模式（D-012）")

    # ---------------------------------------------------------- 2. 共现不足

    def test_co_occurrence_not_alliance(self):
        ev = mk_ev("e1", "三人共同出席雾港灯会", [A, B, C],
                   stability=Stability.REPEATED)
        o = self._analyze([A, B, C], [ev], cardinality=Cardinality.GROUP).value
        self.assertIsNotNone(o.group_dynamics)
        self.assertEqual(o.group_dynamics.alliances, [])
        self.assertEqual(o.group_dynamics.exclusion, [])
        self.assertEqual(o.group_dynamics.relay, [])
        self.assertTrue(o.group_dynamics.note, "群体互动留空必须写 note 说明")
        self.assertTrue(any(w.code == "CO_OCCURRENCE_NOT_ALLIANCE"
                            for w in o.warnings))

    # ---------------------------------------------------------- 6. 群体互动

    def test_group_alliance_from_explicit_marker(self):
        ev = mk_ev("e1", "阿雾、阿灯、阿海三人结盟对抗外敌", [A, B, C],
                   stability=Stability.REPEATED)
        o = self._analyze([A, B, C], [ev], cardinality=Cardinality.GROUP).value
        self.assertTrue(o.group_dynamics.alliances,
                        "明确'结盟'标记应进入 alliances")
        self.assertEqual(o.group_dynamics.exclusion, [])
        self.assertEqual(o.group_dynamics.relay, [])
        self.assertIn("e1", o.group_dynamics.alliances[0].evidence_refs)

    def test_group_exclusion_from_explicit_marker(self):
        ev = mk_ev("e1", "阿雾冷落阿灯", [A, B], stability=Stability.REPEATED)
        o = self._analyze([A, B, C], [ev], cardinality=Cardinality.GROUP).value
        self.assertTrue(o.group_dynamics.exclusion)
        self.assertEqual(o.group_dynamics.alliances, [])
        self.assertEqual(o.group_dynamics.relay, [])

    def test_group_relay_from_explicit_marker(self):
        ev = mk_ev("e1", "阿海为阿雾向阿灯传话", [A, B, C],
                   stability=Stability.REPEATED)
        o = self._analyze([A, B, C], [ev], cardinality=Cardinality.GROUP).value
        self.assertTrue(o.group_dynamics.relay)
        self.assertEqual(o.group_dynamics.alliances, [])
        self.assertEqual(o.group_dynamics.exclusion, [])

    # ---------------------------------------------------------- 5. 时间变化

    def test_temporal_changes_from_distinct_dates(self):
        e1 = mk_ev("e1", "阿雾与阿灯在雾港初遇", [A, B], stability=Stability.SINGLE,
                   stated_at=FuzzyDate(raw="2020", start="2020-01-01"))
        e2 = mk_ev("e2", "阿雾与阿灯在星灯重逢", [A, B], stability=Stability.SINGLE,
                   stated_at=FuzzyDate(raw="2021", start="2021-01-01"))
        o = self._analyze([A, B], [e1, e2]).value
        self.assertEqual(len(o.temporal_changes), 2,
                         "两个不同时点的同对关系应进入时间变化")
        self.assertTrue(all(it.occurred_at for it in o.temporal_changes))

    def test_single_date_no_temporal_change(self):
        e1 = mk_ev("e1", "阿雾与阿灯在雾港初遇", [A, B], stability=Stability.SINGLE,
                   stated_at=FuzzyDate(raw="2020", start="2020-01-01"))
        o = self._analyze([A, B], [e1]).value
        self.assertEqual(o.temporal_changes, [],
                         "单一时点不应制造时间变化")

    # ---------------------------------------------------------- 4. 矛盾来源

    def test_contradictory_sources_preserved_separately(self):
        e1 = mk_ev("e1", "阿雾说与阿灯是挚友", [A, B], stability=Stability.REPEATED,
                   conflict_ids=["cf1"])
        e2 = mk_ev("e2", "阿雾说与阿灯是死敌", [A, B], stability=Stability.REPEATED,
                   conflict_ids=["cf1"])
        o = self._analyze([A, B], [e1, e2]).value
        # 两说分别保留（shared 各一条），且矛盾被标注
        self.assertEqual(len(o.shared_experiences), 2)
        self.assertTrue(any(a.text.startswith("覆盖对") for a in o.asymmetry_notes),
                        "同对关系多说法应记不对称/矛盾说明")
        self.assertIn("cf1", o.conflict_ids, "冲突应被传递")

    # ---------------------------------------------------------- 7. 无/弱/无效

    def test_no_evidence_returns_empty_with_open_questions(self):
        o = self._analyze([A, B], []).value
        self.assertEqual(o.method, ANALYZE_METHOD_RULE)
        self.assertFalse(o.is_semantic_model)
        self.assertTrue(o.open_questions, "无证据必须说明缺什么，不交白卷")
        self.assertEqual(o.shared_experiences, [])

    def test_weak_single_subject_evidence_excluded(self):
        # 只关联单人的 RELATION 证据无法覆盖 ≥2 人 → 不产生共同经历
        ev = mk_ev("e1", "阿雾独自看守灯塔", [A], stability=Stability.REPEATED)
        o = self._analyze([A, B], [ev]).value
        self.assertEqual(o.shared_experiences, [],
                         "单人证据不足以建立二人共同经历")
        self.assertTrue(o.open_questions)

    def test_emotional_dimension_covered_from_markers(self):
        ev = mk_ev("e1", "阿雾温柔地护着阿灯", [A, B], stability=Stability.REPEATED)
        o = self._analyze([A, B], [ev]).value
        self.assertTrue(o.emotional_expression_habits,
                        "情绪标记应进入情绪与表达习惯维度")
        self.assertIn("e1", o.emotional_expression_habits[0].evidence_refs)

    # ---------------------------------------------------------- 8. 幂等/追踪/异常

    def test_deterministic_repeat_runs_equal(self):
        ev = mk_ev("e1", "阿雾与阿灯每天在灯塔下交谈", [A, B],
                   stability=Stability.REPEATED)
        o1 = self._analyze([A, B], [ev]).value
        o2 = self._analyze([A, B], [ev]).value
        self.assertEqual(o1, o2, "相同输入必须产生确定性、可比较的结果")

    def test_every_item_traces_to_input_evidence(self):
        evs = [
            mk_ev("e1", "阿雾与阿灯每天在灯塔下交谈", [A, B],
                  stability=Stability.REPEATED),
            mk_ev("e2", "阿雾温柔地护着阿灯", [A, B], stability=Stability.REPEATED),
        ]
        o = self._analyze([A, B], evs).value
        refs = _outcome_refs(o)
        self.assertTrue(refs, "产出应包含可追踪的证据引用")
        self.assertTrue(refs <= {"e1", "e2"},
                        f"所有引用必须来自输入证据，收到 {refs}")

    def test_no_forbidden_intimacy_fields_anywhere(self):
        ev = mk_ev("e1", "阿雾与阿灯结盟", [A, B, C], stability=Stability.REPEATED)
        o = self._analyze([A, B, C], [ev], cardinality=Cardinality.GROUP).value
        _assert_no_forbidden_fields(self, o)
        if o.group_dynamics is not None:
            _assert_no_forbidden_fields(self, o.group_dynamics)

    def test_too_few_participants_is_error(self):
        res = self._analyze([A], [mk_ev("e1", "x", [A])])
        self.assertTrue(is_err(res))
        self.assertEqual(res.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_capability_boundary_flag_false(self):
        o = self._analyze([A, B], []).value
        self.assertFalse(o.is_semantic_model,
                         "规则分析器必须明示：这不是语义模型")


# ==========================================================================
# Mock 分析器：精确控制（含 A→B / B→A 不对称）
# ==========================================================================


class TestMockAnalyzer(unittest.TestCase):
    def test_asymmetric_directions_controllable(self):
        out = RelationshipAnalysisOutcome(
            perspectives={
                A: RelationshipAnalysisPerspective(
                    entity_id=A,
                    view_of_targets={B: [RelationshipAnalysisItem(
                        text="A 视 B 为盟友", evidence_refs=["e1"])]}),
                B: RelationshipAnalysisPerspective(
                    entity_id=B, view_of_targets={A: []}),
            },
            open_questions=[])
        fix = {RelationshipAnalysisRequest(
            participant_entity_ids=[A, B]).scenario_key(): out}
        analyzer = MockRelationshipAnalyzer(fix)
        res = analyzer.analyze(
            CTX, RelationshipAnalysisRequest(
                participant_entity_ids=[A, B], relation_evidence=[],
                cardinality=Cardinality.PAIR))
        self.assertTrue(is_err(res) is False)
        o = res.value
        self.assertEqual(o.method, ANALYZE_METHOD_MOCK)
        self.assertFalse(o.is_semantic_model)
        self.assertTrue(o.perspectives[A].view_of_targets[B])
        self.assertEqual(o.perspectives[B].view_of_targets[A], [],
                         "B→A 方向可被精确控制为空（不对称）")

    def test_missing_fixture_returns_safe_empty(self):
        analyzer = MockRelationshipAnalyzer({})
        res = analyzer.analyze(
            CTX, RelationshipAnalysisRequest(
                participant_entity_ids=[A, B], relation_evidence=[],
                cardinality=Cardinality.PAIR))
        self.assertTrue(is_err(res) is False)
        self.assertTrue(res.value.open_questions)

    def test_mock_records_call_count(self):
        analyzer = MockRelationshipAnalyzer({})
        analyzer.analyze(CTX, RelationshipAnalysisRequest(
            participant_entity_ids=[A, B], relation_evidence=[]))
        self.assertEqual(analyzer.calls, 1)
        self.assertIsNotNone(analyzer.last_request)


# ==========================================================================
# Provider 分析器：仅契约占位
# ==========================================================================


class TestProviderAnalyzer(unittest.TestCase):
    def test_provider_is_contract_only(self):
        res = ProviderRelationshipAnalyzer().analyze(
            CTX, RelationshipAnalysisRequest(
                participant_entity_ids=[A, B], relation_evidence=[]))
        self.assertTrue(is_err(res))
        self.assertEqual(res.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
