"""H-10 端到端复验（自动抽取路径，隔离 Mock，全程离线）。

目的：
- 用**最简输入**（只给目标人物名，不传 claim / EvidenceItem / PersonaProfile /
  RelationshipProfile）从业务入口 `BusinessOrchestrator.run` 跑全流程；
- 验证 G-2 自动抽取 → 证据登记 → 人格 → 关系 的集成没有断链：
  早期用例（TestAutoClaimExtraction）只断言"证据已登记 + 五阶段 EXECUTED ⇒ SUCCEEDED"，
  **没有**断言自动抽取路径下人格/关系真的**看见**了证据；本类补上这一下游可见性守护。
- 关键约束：`BusinessFixture.entity()` 不去重，fixtures 与 request 必须用**同一组**
  实体 ID，否则下游 `query_evidence` 按 targets 过滤会 0 命中（纯接线问题，非产品 bug）。

不修改任何已验收模块；仅新增集成测试补覆盖。
"""
import re
import unittest

from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err
from persona.orchestrator import (
    RUN_FAILED, RUN_PARTIAL, RUN_SUCCEEDED,
    STAGE_EVIDENCE, STAGE_PERSONA, STAGE_RELATIONSHIP,
    STEP_EXECUTED, STEP_FAILED, STEP_NOT_REACHED, STEP_SKIPPED,
    BusinessRequest,
)
from tests.test_business_orchestrator import (
    BusinessFixture, _claim_fixtures,
    MockClaimExtractor, RuleRelationshipAnalyzer, unwrap, BusinessOrchestrator,
    Cardinality,
)


def _wire(fx, *names, extractor=None):
    """创建一组实体 ID 并注入 G-2 抽取器 + G-4 分析器（fixtures 与 request 共用同一组 ID）。"""
    ids = [fx.entity(n) for n in names]
    fx._eids = ids
    if extractor is None:
        extractor = MockClaimExtractor(_claim_fixtures(*ids))
    fx.orch = BusinessOrchestrator(fx._deps(
        claim_extractor=extractor, relationship_analyzer=RuleRelationshipAnalyzer()))
    return ids


def _request(fx, *names, max_rounds=2):
    ids = fx._eids
    assert len(ids) == len(names)
    targets = [fx.target(i, n) for i, n in zip(ids, names)]
    return BusinessRequest(targets=targets, max_rounds=max_rounds)


def _trace_relationship(fx, result):
    """关系档案落盘条目引用都必须落在本次 evidence_ids 内。"""
    ev_ids = set(result.evidence_ids)
    bad = []
    for rid in result.relationship_profile_ids:
        view = unwrap(fx.relationship.get_relationship(fx.ctx, rid))
        refs = []
        for it in view.shared_experiences:
            refs += it.evidence_refs
        for it in view.interaction_patterns:
            refs += it.evidence_refs
        for it in view.emotional_expression_habits:
            refs += it.evidence_refs
        for blk in view.perspectives.values():
            for lst in blk.view_of_targets.values():
                for it in lst:
                    refs += it.evidence_refs
        gd = view.group_dynamics
        if gd is not None:
            for it in gd.alliances + gd.exclusion + gd.relay:
                refs += it.evidence_refs
        for r in refs:
            if r not in ev_ids:
                bad.append((rid, r))
    return bad


class TestH10E2EAutoExtraction(unittest.TestCase):
    # ---- 子需求 (1)(2)(3)：最简输入 + 阶段顺序 + 聚合 + 可追溯 ----------------
    def test_minimal_two_names_runs_full_chain_ac1(self):
        fx = BusinessFixture(record=True)
        try:
            ids = _wire(fx, "阿雾", "阿灯")
            req = _request(fx, "阿雾", "阿灯", max_rounds=2)
            # (1) 最简输入：不传任何 claim / 证据 / 档案
            self.assertEqual(len(req.evidence_seeds or []), 0)
            self.assertEqual([t.entity_id for t in req.targets], ids)

            result = unwrap(fx.orch.run(fx.ctx, req))
            # (2) 全流程跑通且由 Orchestrator 聚合
            self.assertEqual(result.status, RUN_SUCCEEDED)
            calls = fx.log.calls
            self.assertIn("research.run", calls)
            self.assertIn("persona.create_profile", calls)
            self.assertIn("relationship.create_relationship", calls)
            self.assertIn("dialogue.start_session", calls)
            # 阶段顺序：研究→证据→人格→关系→对话
            idx = [calls.index(c) for c in ("research.run", "evidence.add_evidence",
                                            "persona.create_profile",
                                            "relationship.create_relationship",
                                            "dialogue.start_session")]
            self.assertEqual(idx, sorted(idx),
                             "阶段必须按 研究→证据→人格→关系→对话 顺序")
            # 产出齐全
            self.assertGreater(len(result.evidence_ids), 0)
            self.assertEqual(len(result.persona_profile_ids), 2)
            self.assertEqual(len(result.relationship_profile_ids), 1)
            # 下游人格**真的基于证据**（自动抽取路径下游可见性）
            for t in req.targets:
                pv = unwrap(fx.persona.get_persona(fx.ctx, t.entity_id))
                self.assertNotEqual(pv.status, "INSUFFICIENT_EVIDENCE")
            # AC-1：2 人 → PAIR，group_dynamics 为 None
            rview = unwrap(fx.relationship.get_relationship(
                fx.ctx, result.relationship_profile_ids[0]))
            self.assertEqual(rview.cardinality, str(Cardinality.PAIR))
            self.assertIsNone(rview.group_dynamics)
            # (3) 可追溯性：关系条目全部落在 evidence_ids 内
            self.assertEqual(_trace_relationship(fx, result), [])
        finally:
            fx.teardown()

    # ---- 子需求 (2)(3)(4)：3 人 + 群体维度 + 共现非联盟 + 可追溯 ----
    def test_minimal_three_names_group_and_cooccurrence_not_alliance_ac2(self):
        fx = BusinessFixture(record=True)
        try:
            ids = _wire(fx, "阿雾", "阿灯", "阿海")
            req = _request(fx, "阿雾", "阿灯", "阿海", max_rounds=2)
            self.assertEqual([t.entity_id for t in req.targets], ids)

            result = unwrap(fx.orch.run(fx.ctx, req))
            self.assertEqual(result.status, RUN_SUCCEEDED)
            self.assertGreater(len(result.evidence_ids), 0)
            self.assertEqual(len(result.persona_profile_ids), 3)

            rview = unwrap(fx.relationship.get_relationship(
                fx.ctx, result.relationship_profile_ids[0]))
            # AC-2：≥3 人 → GROUP，且群体维度出现
            self.assertEqual(rview.cardinality, str(Cardinality.GROUP))
            gd = rview.group_dynamics
            self.assertIsNotNone(gd)
            # (4) 共现材料不得确定性判定为联盟/排斥/传话
            self.assertEqual(len(gd.alliances), 0)
            self.assertEqual(len(gd.exclusion), 0)
            self.assertEqual(len(gd.relay), 0)
            # 关系确实**看见**了 RELATION 证据（下游可见性）：至少 1 条 RELATION 证据
            rel_msg = result.step_of(STAGE_RELATIONSHIP).message
            m = re.search(r"(\d+) 条 RELATION 证据支撑", rel_msg)
            self.assertIsNotNone(m, "关系阶段应报告 RELATION 证据条数")
            self.assertGreaterEqual(int(m.group(1)), 1, "关系应基于 ≥1 条 RELATION 证据")
            # (3) 全部关系条目可追溯
            self.assertEqual(_trace_relationship(fx, result), [])
        finally:
            fx.teardown()

    # ---- 子需求 (5)(7)：无材料 → 安全 PARTIAL，不伪造，不误标完成 ----------------
    def test_no_material_safe_partial_not_completed(self):
        fx = BusinessFixture(record=True)
        try:
            _wire(fx, "阿雾", "阿灯")
            fx.search_provider.materials = {}  # 清空材料 → 检索零结果
            req = _request(fx, "阿雾", "阿灯", max_rounds=1)

            result = unwrap(fx.orch.run(fx.ctx, req))
            ev = result.step_of(STAGE_EVIDENCE)
            # (5) 无材料时证据阶段 SKIPPED，不凭空生成
            self.assertEqual(ev.status, STEP_SKIPPED)
            self.assertEqual(len(result.evidence_ids), 0)
            # 业务诚实报 PARTIAL（不把部分成功说成全流程成功）
            self.assertEqual(result.status, RUN_PARTIAL)
            # (7) 被跳过的阶段不得被算作"已执行完成"
            done = {s.name for s in result.steps if s.status == STEP_EXECUTED}
            self.assertNotIn(STAGE_EVIDENCE, done)
            # 无材料是干净的收敛（非 PAUSED/FAILED/CANCELLED 故障）
            self.assertEqual(result.task_status, "COMPLETED")
        finally:
            fx.teardown()

    # ---- 子需求 (5)：来源矛盾分别保留，不裁决/不合并 ----
    def test_contradiction_preserved(self):
        fx = BusinessFixture(record=True)
        try:
            _wire(fx, "阿雾", "阿灯")
            req = _request(fx, "阿雾", "阿灯", max_rounds=1)
            result = unwrap(fx.orch.run(fx.ctx, req))
            # 冲突被记录（fog_canon vs lamp_conflict）
            self.assertTrue(len(result.conflict_ids) >= 1)
            claims = []
            for eid in result.evidence_ids:
                ev = unwrap(fx.evidence.get_evidence(fx.ctx, eid))
                claims.append((ev.claim, ev.canon_level))
            night = [c for c in claims if c[0] == "阿雾每晚点灯"]
            # 两种正典级别各自保留，不裁决不合并
            self.assertEqual(len(night), 2)
        finally:
            fx.teardown()

    # ---- 子需求 (6)(7)：Mock Provider 异常 → 整链 FAILED，后续阶段不误标完成 ----
    def test_provider_failure_is_failed_not_wrongly_completed(self):
        class _BrokenExtractor:
            def extract(self, ctx, materials, *, targets=None):
                return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                                     "mock 抽取服务不可用"))

        fx = BusinessFixture(record=True)
        try:
            _wire(fx, "阿雾", "阿灯", extractor=_BrokenExtractor())
            req = _request(fx, "阿雾", "阿灯", max_rounds=1)
            result = unwrap(fx.orch.run(fx.ctx, req))
            # 整链 FAILED，且任务机被置 FAILED（区别于 COMPLETED/PAUSED/CANCELLED）
            self.assertEqual(result.status, RUN_FAILED)
            self.assertEqual(result.task_status, "FAILED")
            self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_FAILED)
            # 后续阶段 NOT_REACHED，绝不误标为已完成
            self.assertEqual(result.step_of(STAGE_PERSONA).status, STEP_NOT_REACHED)
            self.assertEqual(result.step_of(STAGE_RELATIONSHIP).status, STEP_NOT_REACHED)
            # 不伪造产出
            self.assertEqual(result.evidence_ids, [])
            self.assertEqual(result.persona_profile_ids, [])
        finally:
            fx.teardown()

    # ---- 子需求 (6) 局部：重复调用幂等，不产生重复证据 ----
    def test_idempotent_rerun_no_duplicate_evidence(self):
        fx = BusinessFixture(record=True)
        try:
            _wire(fx, "阿雾", "阿灯")
            req = _request(fx, "阿雾", "阿灯", max_rounds=2)
            r1 = unwrap(fx.orch.run(fx.ctx, req))
            r2 = unwrap(fx.orch.run(fx.ctx, req))
            # 重跑后证据集合不变（去重生效，不新增重复证据）
            self.assertEqual(set(r1.evidence_ids), set(r2.evidence_ids))
        finally:
            fx.teardown()


if __name__ == "__main__":
    unittest.main()
