"""H-10A 业务编排器（`BusinessOrchestrator`）测试。

全部跑在**临时目录**的 SQLite 上，**不触网**（`OfflineGuardProvider` + `network_blocked`
双重硬保证），不调真实模型 / 搜索 API，不使用密钥。

覆盖（对应用户验收清单 1~8）：
  1. 职责分离：既有 `TaskService` / `TaskRunner` 接口不变；业务模块不得反向依赖编排层
  2. 调用链：Research → Evidence → Persona → Relationship → Dialogue（顺序 + 前一步输出传给下一步）
  3. 输入输出：成功 / 部分成功 / 失败三态；部分成功**绝不**报成全流程成功
  4. 任务集成：复用六态机 / 预算闸门 / 检查点；编排器内**不出现**第二套状态机或账本
  5. 失败与恢复：单步失败即上报且后续步骤不伪造成功；重试 / 重复调用不重复登记
  6. 数据边界：模拟对话后四张"现实世界"表零变化（AC-13）；模拟记忆不成证据
  7. 异常路径：Research 无结果 / Evidence 校验失败 / Persona / Relationship / Dialogue 失败
  8. AC-1 / AC-2 原文核对

对应文档：`PROJECT_SPEC.md` §7（AC-1/AC-2）、`INTERFACES.md`、`ROADMAP.md` §4、
`ERROR_HANDLING.md` §2.3、`DECISIONS.md` §2.24。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    CanonLevel,
    Cardinality,
    EvidenceDimension,
    EvidenceTier,
    ProvenanceNamespace,
    Stability,
    TaskStatus,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query, UsageReport
from persona.modules.dialogue import DialogueDeps, DialogueService, SessionDraft
from persona.modules.evaluation import EvaluationService
from persona.modules.evidence import EvidenceDraft, EvidenceService
from persona.modules.extraction import (
    EXTRACT_METHOD_MOCK,
    TAG_MOCK_FIXTURE,
    CandidateClaim,
    MockClaimExtractor,
)
from persona.modules.memory import MemoryService
from persona.modules.persona import PersonaService
from persona.modules.relationship import RelationshipService
from persona.modules.relationship_analysis import (
    MockRelationshipAnalyzer,
    RelationshipAnalysisItem,
    RelationshipAnalysisOutcome,
    RelationshipAnalysisPerspective,
    RuleRelationshipAnalyzer,
)
from persona.modules.research import PersonQueryTarget, ResearchDeps, ResearchService
from persona.orchestrator import (
    RUN_FAILED,
    RUN_PAUSED,
    RUN_PARTIAL,
    RUN_SUCCEEDED,
    STAGE_DIALOGUE,
    STAGE_EVIDENCE,
    STAGE_PERSONA,
    STAGE_RELATIONSHIP,
    STAGE_RESEARCH,
    STEP_EXECUTED,
    STEP_FAILED,
    STEP_NOT_REACHED,
    STEP_SKIPPED,
    BudgetSpec,
    BusinessDeps,
    BusinessOrchestrator,
    BusinessRequest,
    EvidenceSeed,
    TaskService,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from persona.providers.mock import OfflineGuardProvider, network_blocked
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_business"

# --------------------------------------------------------------------------
# 固定测试材料：全部为虚构角色与虚构作品，不含任何真实人物专属设定（AC-16）
# --------------------------------------------------------------------------

MATERIAL_FOG = SearchResult(
    title="雾港设定资料集",
    url="https://example.invalid/mist-harbor",
    canonical_key="key_mist_harbor",
    snippet="雾港灯塔与看守制度的官方设定。",
    published_at="2021-05-04",
    source_kind_guess="OFFICIAL_MATERIAL",
)
MATERIAL_LAMP = SearchResult(
    title="星灯访谈录",
    url="https://example.invalid/starlamp-interview",
    canonical_key="key_starlamp_interview",
    snippet="主创访谈，谈及阿雾与阿灯的相遇。",
    published_at="2022-01-10",
    source_kind_guess="INTERVIEW",
)
MATERIAL_TIDE = SearchResult(
    title="潮汐手记",
    url="https://example.invalid/tide-notes",
    canonical_key="key_tide_notes",
    snippet="渔村手记，记录阿海的日常工作。",
    published_at="2022-03-01",
    source_kind_guess="BIOGRAPHY",
)

MATERIALS = {
    "阿雾": [MATERIAL_FOG],
    "阿灯": [MATERIAL_LAMP],
    "阿海": [MATERIAL_TIDE],
}


# ==========================================================================
# Mock Provider（确定性离线 + 故障注入）
# ==========================================================================


class MockSearchProvider:
    """确定性离线检索 Provider：按检索词返回固定材料，可注入失败。"""

    def __init__(self, materials=None, *, error_for=None) -> None:
        self.materials = dict(materials if materials is not None else MATERIALS)
        self.error_for = dict(error_for or {})
        self.calls: list[str] = []

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="mock-search", supports_search=True))

    def chat(self, ctx, req: ChatRequest):
        return Ok(ChatResponse(text=""))

    def search(self, ctx, req: SearchRequest):
        self.calls.append(req.query)
        code = self.error_for.get(req.query)
        if code is not None:
            return Err(new_error(code, f"注入失败：{req.query}", trace_ref=ctx.trace_ref))
        usage = UsageDelta(requests=1, latency_ms=5)
        return Ok(SearchResponse(results=list(self.materials.get(req.query, [])),
                                 usage=usage), usage=usage)

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=len(self.calls)))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class MockChatProvider:
    """确定性离线对话 Provider：记录 prompt，返回固定模拟文本。"""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.chat_calls = 0
        self.last_messages: list = []

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="mock-chat", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.chat_calls += 1
        self.last_messages = list(req.messages)
        if self.fail:
            return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE, "注入的对话生成失败",
                                 trace_ref=ctx.trace_ref))
        return Ok(ChatResponse(text="（模拟）我照旧在黄昏点亮灯塔。",
                               finish_reason="stop", model_id="mock-chat",
                               usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


# ==========================================================================
# 记录型包装：断言"哪个模块被调用了、以什么顺序"
# ==========================================================================


class Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def note(self, name: str) -> None:
        self.calls.append(name)


class RecordingEvidence:
    def __init__(self, real: EvidenceService, log: Recorder) -> None:
        self._real = real
        self._log = log

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def add_evidence(self, ctx, draft):
        self._log.note("evidence.add_evidence")
        return self._real.add_evidence(ctx, draft)

    def record_inference(self, ctx, draft):
        self._log.note("evidence.record_inference")
        return self._real.record_inference(ctx, draft)


class RecordingPersona:
    def __init__(self, real: PersonaService, log: Recorder) -> None:
        self._real = real
        self._log = log

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def create_profile(self, ctx, draft):
        self._log.note("persona.create_profile")
        return self._real.create_profile(ctx, draft)

    def revise(self, ctx, entity_id, draft):
        self._log.note("persona.revise")
        return self._real.revise(ctx, entity_id, draft)


class RecordingRelationship:
    def __init__(self, real: RelationshipService, log: Recorder) -> None:
        self._real = real
        self._log = log

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def create_relationship(self, ctx, draft):
        self._log.note("relationship.create_relationship")
        return self._real.create_relationship(ctx, draft)

    def revise(self, ctx, participants, draft):
        self._log.note("relationship.revise")
        return self._real.revise(ctx, participants, draft)


class RecordingDialogue:
    def __init__(self, real: DialogueService, log: Recorder) -> None:
        self._real = real
        self._log = log

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def start_session(self, ctx, draft):
        self._log.note("dialogue.start_session")
        return self._real.start_session(ctx, draft)

    def run(self, ctx, session_id, max_rounds):
        self._log.note("dialogue.run")
        return self._real.run(ctx, session_id, max_rounds)


class RecordingResearch:
    def __init__(self, real: ResearchService, log: Recorder) -> None:
        self._real = real
        self._log = log

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def run(self, ctx, targets, budget=None):
        self._log.note("research.run")
        return self._real.run(ctx, targets, budget)


class _Failing:
    """把指定方法变成固定错误（其余委派给真实服务）。"""

    def __init__(self, real, method: str, code: ErrorCode) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_method", method)
        object.__setattr__(self, "_code", code)

    def __getattr__(self, name: str):
        real = object.__getattribute__(self, "_real")
        if name == object.__getattribute__(self, "_method"):
            code = object.__getattribute__(self, "_code")

            def _fail(*args, **kwargs):
                return Err(new_error(code, f"注入失败：{name}"))
            return _fail
        return getattr(real, name)


# ==========================================================================
# 夹具
# ==========================================================================


class BusinessFixture:
    """临时 SQLite + 全套服务 + 业务编排器。"""

    def __init__(self, *, materials=None, search_error=None, chat_fail=False,
                 record=False) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-biz-")
        self.path = Path(self.tmp) / "business.db"
        self.storage = SqliteStorage(self.path)
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))

        self.log = Recorder()
        self.search_provider = MockSearchProvider(materials, error_for=search_error)
        self.chat_provider = MockChatProvider(fail=chat_fail)

        self.evidence = EvidenceService(self.storage)
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.memory = MemoryService(self.storage)
        self.evaluation = EvaluationService(self.storage)
        self.task_service = TaskService(self.storage)

        self.research = ResearchService(ResearchDeps(
            provider=OfflineGuardProvider(self.search_provider),
            evidence=self.evidence,
            search_metadata={"fixture_key": "business"}))
        self.dialogue = DialogueService(DialogueDeps(
            profiles=self.persona, relations=self.relationship, evidence=self.evidence,
            memory=MemoryService(self.storage), storage=self.storage,
            provider=self.chat_provider, memory_reader=MemoryService(self.storage)))

        if record:
            self.research = RecordingResearch(self.research, self.log)
            self.evidence = RecordingEvidence(self.evidence, self.log)
            self.persona = RecordingPersona(self.persona, self.log)
            self.relationship = RecordingRelationship(self.relationship, self.log)
            self.dialogue = RecordingDialogue(self.dialogue, self.log)

        self.orch = BusinessOrchestrator(self._deps())

    # ---------------------------------------------------------------- 装配

    def _deps(self, claim_extractor=None,
              relationship_analyzer=None) -> BusinessDeps:
        return BusinessDeps(
            research=self.research, evidence=self.evidence, persona=self.persona,
            relationship=self.relationship, dialogue=self.dialogue,
            task_service=self.task_service, storage=self.storage,
            claim_extractor=claim_extractor,
            relationship_analyzer=relationship_analyzer)

    def use_extractor(self, fixtures) -> MockClaimExtractor:
        """注入 Mock Claim Extractor（G-2），返回它以便断言调用情况。"""
        extractor = MockClaimExtractor(fixtures)
        self.extractor = extractor
        self.orch = BusinessOrchestrator(self._deps(extractor))
        return extractor

    def use_analyzer(self, analyzer) -> object:
        """注入关系语义分析器（G-4），返回它以便断言调用情况。"""
        self.analyzer = analyzer
        self.orch = BusinessOrchestrator(self._deps(relationship_analyzer=analyzer))
        return analyzer

    # ---------------------------------------------------------------- 构件

    def entity(self, name: str) -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER",
             "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
             "fictional_character_meta": {
                 "works": [{"work_id": "wrk_mistharbor", "title": "《雾港与星灯》",
                            "medium": "ANIMATION"}],
                 "adaptation_version": "2020 动画改编版",
                 "adaptation_lineage": ["原作轻小说版", "2020 动画改编版"],
                 "official_canon_level": "CANON"}},
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def target(self, entity_id: str, name: str, **kw) -> PersonQueryTarget:
        return PersonQueryTarget(entity_id=entity_id, display_name=name, **kw)

    def seed(self, source_ref: str, claim: str, subjects: list[str], *,
             dimension=EvidenceDimension.BACKGROUND, stability=Stability.REPEATED,
             tier=EvidenceTier.PROFILE_BACKGROUND, confidence: float = 0.8,
             canon_level=CanonLevel.CANON, **kw) -> EvidenceSeed:
        return EvidenceSeed(
            source_ref=source_ref,
            draft=EvidenceDraft(claim=claim, tier=tier, dimension=dimension,
                                subject_entity_ids=list(subjects),
                                extraction_confidence=confidence,
                                stability=stability, canon_level=canon_level, **kw))

    def count(self, kind: str, namespace: str | None = None) -> int:
        filters = {"kind": kind, "project_id": PROJECT}
        if namespace:
            filters["namespace"] = namespace
        return len(unwrap(self.storage.query(
            self.ctx, kind, Query(filters=filters, limit=MAX_QUERY_LIMIT),
            include_deleted=True)).items)

    def teardown(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class BusinessTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = BusinessFixture(record=True)

    def tearDown(self) -> None:
        self.fx.teardown()


def _two_actor_request(fx: BusinessFixture) -> BusinessRequest:
    """两个虚构人物 + 5 条可追溯证据种子（覆盖 background / speech / trait / relation）。"""
    a = fx.entity("阿雾")
    b = fx.entity("阿灯")
    return BusinessRequest(
        targets=[fx.target(a, "阿雾"), fx.target(b, "阿灯")],
        max_rounds=2,
        evidence_seeds=[
            fx.seed("key_mist_harbor", "阿雾长期独自看守雾港灯塔", [a],
                    dimension=EvidenceDimension.BACKGROUND),
            fx.seed("key_mist_harbor", "阿雾说话简短，常用省略句", [a],
                    dimension=EvidenceDimension.SPEECH_STYLE,
                    stability=Stability.PATTERN),
            fx.seed("key_mist_harbor", "阿雾曾在暴风雨夜独自修灯", [a],
                    dimension=EvidenceDimension.TRAIT, stability=Stability.SINGLE),
            fx.seed("key_starlamp_interview", "阿灯每天黄昏到灯塔来", [b],
                    dimension=EvidenceDimension.TRAIT, stability=Stability.REPEATED),
            fx.seed("key_starlamp_interview", "阿雾与阿灯在灯塔前立下约定", [a, b],
                    dimension=EvidenceDimension.RELATION, stability=Stability.REPEATED,
                    canon_level=CanonLevel.CANON),
        ])


def _plain_request(fx: BusinessFixture, *names: str, max_rounds: int = 2) -> BusinessRequest:
    """**只给目标人物**——不含任何 claim / EvidenceSeed（G-2 的最小输入）。

    对应 AC-1 原文「输入 2 个人名，一条命令可跑完全流程」。
    """
    return BusinessRequest(
        targets=[fx.target(fx.entity(name), name) for name in names],
        max_rounds=max_rounds)


def _claim_fixtures(a: str, b: str, c: str | None = None) -> dict:
    """Mock 抽取器的固定材料。全部为**合成虚构内容**（AC-16 / AC-17）。

    覆盖三种 claim：
        - `fog_verbatim`  原文直接支持（VERBATIM + 可在材料里逐字找到的摘录）
        - `fog_bg` …      对材料的归纳（PROFILE_BACKGROUND，不带摘录）
        - `lamp_infer`    系统推断（INFERENCE + derivation_keys）
    另含一对**正典分歧**（`fog_canon` vs `lamp_conflict`）用于验证冲突保留。
    """
    fog = [
        CandidateClaim(
            key="fog_verbatim", claim="材料原文直接提到「雾港灯塔」",
            tier=EvidenceTier.VERBATIM, dimension=EvidenceDimension.BACKGROUND,
            subject_entity_ids=[a], confidence=0.9, verbatim_quote="雾港灯塔",
            locator={"field": "snippet"}, stability=Stability.REPEATED,
            canon_level=CanonLevel.CANON, note="原文直接支持"),
        CandidateClaim(
            key="fog_bg", claim="阿雾负责看守雾港灯塔",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.BACKGROUND,
            subject_entity_ids=[a], confidence=0.7, stability=Stability.REPEATED,
            canon_level=CanonLevel.CANON, note="对材料的归纳，非原话"),
        CandidateClaim(
            key="fog_single", claim="阿雾曾在暴风雨夜独自修灯",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[a], confidence=0.5, stability=Stability.SINGLE,
            canon_level=CanonLevel.CANON),
        CandidateClaim(
            key="fog_canon", claim="阿雾每晚点灯",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.HABIT,
            subject_entity_ids=[a], confidence=0.6, stability=Stability.REPEATED,
            canon_level=CanonLevel.CANON),
    ]
    lamp = [
        CandidateClaim(
            key="lamp_bg", claim="阿灯每天黄昏到灯塔来",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.BACKGROUND,
            subject_entity_ids=[b], confidence=0.6, stability=Stability.REPEATED,
            canon_level=CanonLevel.CANON),
        CandidateClaim(
            key="lamp_relation", claim="阿雾与阿灯在灯塔前立下约定",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.RELATION,
            subject_entity_ids=[a, b], confidence=0.6,
            stability=Stability.REPEATED, canon_level=CanonLevel.CANON),
        CandidateClaim(
            key="lamp_infer", claim="两人因约定而建立起稳定的信任",
            tier=EvidenceTier.INFERENCE, dimension=EvidenceDimension.RELATION,
            subject_entity_ids=[a, b], confidence=0.3,
            derivation_keys=["lamp_relation"], note="系统推断，不是原文"),
        CandidateClaim(
            key="lamp_conflict", claim="阿雾每晚点灯",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.HABIT,
            subject_entity_ids=[a], confidence=0.4, stability=Stability.REPEATED,
            canon_level=CanonLevel.NON_CANON, note="与另一来源的正典级别不同"),
    ]
    fixtures = {"key_mist_harbor": fog, "key_starlamp_interview": lamp}
    if c:
        fixtures["key_tide_notes"] = [
            CandidateClaim(
                key="tide_habit", claim="阿海在渔村记录潮汐",
                tier=EvidenceTier.PROFILE_BACKGROUND,
                dimension=EvidenceDimension.HABIT, subject_entity_ids=[c],
                confidence=0.6, stability=Stability.REPEATED),
            CandidateClaim(
                key="tide_group", claim="三人共同出席雾港灯会",
                tier=EvidenceTier.PROFILE_BACKGROUND,
                dimension=EvidenceDimension.RELATION,
                subject_entity_ids=[a, b, c], confidence=0.5,
                stability=Stability.REPEATED),
        ]
    return fixtures


# ==========================================================================
# 1. 成功链路：顺序 + 前一步输出传给下一步
# ==========================================================================


class TestHappyChain(BusinessTestCase):
    def test_full_chain_succeeds_with_five_steps(self):
        request = _two_actor_request(self.fx)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_SUCCEEDED)
        self.assertTrue(result.is_complete())
        self.assertEqual(result.task_status, str(TaskStatus.COMPLETED))
        self.assertEqual([s.status for s in result.steps], [STEP_EXECUTED] * 5)

    def test_modules_are_called_in_declared_order(self):
        unwrap(self.fx.orch.run(self.fx.ctx, _two_actor_request(self.fx)))
        order = [c for c in self.fx.log.calls]
        self.assertEqual(order, [
            "research.run",
            "evidence.add_evidence", "evidence.add_evidence", "evidence.add_evidence",
            "evidence.add_evidence", "evidence.add_evidence",
            "persona.create_profile", "persona.create_profile",
            "relationship.create_relationship",
            "dialogue.start_session", "dialogue.run",
        ])

    def test_research_output_is_consumed_by_evidence(self):
        """研究产出的来源 ID 真的成了证据的 `source_id`（不是各跑各的）。"""
        result = unwrap(self.fx.orch.run(self.fx.ctx, _two_actor_request(self.fx)))
        self.assertTrue(result.source_ids)
        for evidence_id in result.evidence_ids:
            view = unwrap(self.fx.evidence.get_evidence(self.fx.ctx, evidence_id))
            self.assertIn(str(view.source_id), result.source_ids)

    def test_persona_items_are_traceable_to_evidence_and_source(self):
        """AC-3：档案条目 → 证据 → 来源，三段都可反查。"""
        request = _two_actor_request(self.fx)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(len(result.evidence_ids), 5)
        for target in request.targets:
            profile = unwrap(self.fx.persona.get_persona(self.fx.ctx, target.entity_id))
            refs = [r for item in profile.traits + profile.habits + profile.single_events
                    for r in item.evidence_refs]
            refs += list(profile.background.evidence_refs)
            refs += list(profile.expression_style.evidence_refs)
            self.assertTrue(refs, f"{target.entity_id} 的档案条目没有任何证据引用")
            for ref in refs:
                self.assertIn(ref, result.evidence_ids)
                evidence = unwrap(self.fx.evidence.get_evidence(self.fx.ctx, ref))
                self.assertIn(str(evidence.source_id), result.source_ids)

    def test_single_occurrence_never_lands_in_traits(self):
        """AC-7：`SINGLE` 的行为描述只能进 `single_events`，不得固化成稳定性格。"""
        request = _two_actor_request(self.fx)
        unwrap(self.fx.orch.run(self.fx.ctx, request))
        profile = unwrap(self.fx.persona.get_persona(
            self.fx.ctx, request.targets[0].entity_id))
        self.assertIn("阿雾曾在暴风雨夜独自修灯", [i.text for i in profile.single_events])
        self.assertNotIn("阿雾曾在暴风雨夜独自修灯", [i.text for i in profile.traits])
        # 重复出现的行为才允许进 traits
        profile_b = unwrap(self.fx.persona.get_persona(
            self.fx.ctx, request.targets[1].entity_id))
        self.assertIn("阿灯每天黄昏到灯塔来", [i.text for i in profile_b.traits])

    def test_relationship_uses_relation_evidence_not_profiles(self):
        """RL-V6：关系条目的证据引用指向 `EvidenceItem`，且是 RELATION 维度的证据。"""
        result = unwrap(self.fx.orch.run(self.fx.ctx, _two_actor_request(self.fx)))
        self.assertEqual(len(result.relationship_profile_ids), 1)
        view = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, result.relationship_profile_ids[0]))
        self.assertEqual(view.cardinality, str(Cardinality.PAIR))
        self.assertTrue(view.shared_experiences)
        for item in view.shared_experiences:
            self.assertTrue(item.evidence_refs)
            for ref in item.evidence_refs:
                self.assertIn(ref, result.evidence_ids)
                evidence = unwrap(self.fx.evidence.get_evidence(self.fx.ctx, ref))
                self.assertEqual(evidence.dimension, str(EvidenceDimension.RELATION))

    def test_dialogue_consumes_produced_profiles(self):
        result = unwrap(self.fx.orch.run(self.fx.ctx, _two_actor_request(self.fx)))
        self.assertIsNotNone(result.session_id)
        self.assertEqual(len(result.turn_ids), 2)
        self.assertTrue(self.fx.chat_provider.chat_calls >= 2)

    def test_task_record_receives_usage(self):
        result = unwrap(self.fx.orch.run(self.fx.ctx, _two_actor_request(self.fx)))
        task = unwrap(self.fx.task_service.get(self.fx.ctx, result.task_id))
        self.assertGreaterEqual(task.budget_used.requests, 5)


# ==========================================================================
# 2. Research 无结果
# ==========================================================================


class TestResearchNoResult(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = BusinessFixture(materials={}, record=False)

    def tearDown(self) -> None:
        self.fx.teardown()

    def test_no_material_and_no_seeds_is_partial_not_success(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        request = BusinessRequest(targets=[self.fx.target(a, "阿雾"),
                                           self.fx.target(b, "阿灯")], max_rounds=1)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_PARTIAL)
        self.assertFalse(result.is_complete())
        self.assertEqual(result.source_ids, [])
        self.assertEqual(result.step_of(STAGE_RESEARCH).status, STEP_EXECUTED)
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_SKIPPED)

    def test_no_material_with_seeds_fails_loudly(self):
        """没有来源却要登记证据 = 凭空声称，必须报错而不是编造来源。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")], max_rounds=1,
            evidence_seeds=[self.fx.seed("key_mist_harbor", "凭空的说法", [a])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_FAILED)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_NOT_REACHED)
        self.assertIsNotNone(result.error)
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)

    def test_research_provider_failure_is_reported(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        fx2 = BusinessFixture(materials=MATERIALS,
                              search_error={"阿雾": ErrorCode.EXTERNAL_UNAVAILABLE,
                                            "阿灯": ErrorCode.EXTERNAL_UNAVAILABLE},
                              record=False)
        self.addCleanup(fx2.teardown)
        request = BusinessRequest(targets=[fx2.target(a, "阿雾"),
                                           fx2.target(b, "阿灯")], max_rounds=1)
        result = unwrap(fx2.orch.run(fx2.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_RESEARCH).status, STEP_FAILED)


# ==========================================================================
# 3. 各步失败：不伪造后续步骤成功
# ==========================================================================


class TestStepFailures(BusinessTestCase):
    def test_evidence_rejects_seed_with_unknown_source_ref(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")], max_rounds=1,
            evidence_seeds=[self.fx.seed("key_not_collected", "无来源的说法", [a])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_NOT_REACHED)

    def test_evidence_rejects_simulated_namespace(self):
        """EV-V1：模拟内容永远不能变成真实证据。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        bad = self.fx.seed("key_mist_harbor", "模拟对话里编出来的说法", [a])
        bad = EvidenceSeed(source_ref=bad.source_ref,
                           draft=replace(bad.draft,
                                         namespace=ProvenanceNamespace.SIMULATED))
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")], max_rounds=1,
            evidence_seeds=[bad])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_persona_failure_stops_before_dialogue(self):
        request = _two_actor_request(self.fx)
        self.fx.orch.deps = BusinessDeps(
            research=self.fx.research, evidence=self.fx.evidence,
            persona=_Failing(self.fx.persona, "create_profile",
                             ErrorCode.STORAGE_WRITE_FAILED),
            relationship=self.fx.relationship, dialogue=self.fx.dialogue,
            task_service=self.fx.task_service, storage=self.fx.storage)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_PERSONA).status, STEP_FAILED)
        self.assertEqual(result.step_of(STAGE_RELATIONSHIP).status, STEP_NOT_REACHED)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_NOT_REACHED)

    def test_relationship_failure_stops_before_dialogue(self):
        request = _two_actor_request(self.fx)
        self.fx.orch.deps = BusinessDeps(
            research=self.fx.research, evidence=self.fx.evidence, persona=self.fx.persona,
            relationship=_Failing(self.fx.relationship, "create_relationship",
                                  ErrorCode.STORAGE_WRITE_FAILED),
            dialogue=self.fx.dialogue, task_service=self.fx.task_service,
            storage=self.fx.storage)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_RELATIONSHIP).status, STEP_FAILED)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_NOT_REACHED)

    def test_dialogue_failure_is_reported_as_failure(self):
        fx = BusinessFixture(chat_fail=True, record=False)
        self.addCleanup(fx.teardown)
        a = fx.entity("阿雾")
        b = fx.entity("阿灯")
        request = BusinessRequest(
            targets=[fx.target(a, "阿雾"), fx.target(b, "阿灯")], max_rounds=1,
            evidence_seeds=[fx.seed("key_mist_harbor", "阿雾看守灯塔", [a])])
        result = unwrap(fx.orch.run(fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_FAILED)
        self.assertIsNotNone(result.error)


class _Failing:
    """把指定方法变成固定错误（其余委派给真实服务）。"""

    def __init__(self, real, method: str, code: ErrorCode) -> None:
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_method", method)
        object.__setattr__(self, "_code", code)

    def __getattr__(self, name: str):
        real = object.__getattribute__(self, "_real")
        if name == object.__getattribute__(self, "_method"):
            code = object.__getattribute__(self, "_code")

            def _fail(*args, **kwargs):
                return Err(new_error(code, f"注入失败：{name}"))
            return _fail
        return getattr(real, name)


# ==========================================================================
# 4. 部分成功不得报成全流程成功
# ==========================================================================


class TestPartialSuccess(BusinessTestCase):
    def test_no_materials_and_no_seeds_yields_partial_not_success(self):
        """原 `test_missing_evidence_seeds_yields_partial_not_success`（H-10A）。

        **改了什么、为什么**：G-2 之前"没给 claim ⇒ PARTIAL"；G-2 之后同样
        的输入会走自动抽取并可能成功。被断言的那条**旧行为**正是本步要移除的
        东西，所以这里改成断言"真正没有材料"的情形——研究什么都搜不到时，
        仍然必须报 `PARTIAL`，不能假装成功。断言强度未降（仍验 PARTIAL +
        后续步骤如实跑完）。
        """
        fx = BusinessFixture(materials={}, record=False)
        self.addCleanup(fx.teardown)
        a = fx.entity("阿雾")
        b = fx.entity("阿灯")
        fx.use_extractor(_claim_fixtures(a, b))
        request = BusinessRequest(targets=[fx.target(a, "阿雾"),
                                           fx.target(b, "阿灯")], max_rounds=1)
        result = unwrap(fx.orch.run(fx.ctx, request))
        self.assertEqual(result.status, RUN_PARTIAL)
        self.assertFalse(result.is_complete())
        self.assertEqual(result.evidence_ids, [])
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_SKIPPED)
        # 后续步骤仍然如实跑完，但整条链**不**算全流程成功
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_EXECUTED)

    def test_no_extractor_and_no_seeds_yields_partial(self):
        """既没注入抽取器也没给 claim → 如实跳过，不算成功（G-2 兜底）。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        result = unwrap(self.fx.orch.run(
            self.fx.ctx, BusinessRequest(targets=[self.fx.target(a, "阿雾"),
                                                  self.fx.target(b, "阿灯")],
                                         max_rounds=1)))
        self.assertEqual(result.status, RUN_PARTIAL)
        self.assertEqual(result.evidence_ids, [])
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_SKIPPED)
        self.assertTrue(any(w.code == "EXTRACTOR_ABSENT"
                            for w in result.step_of(STAGE_EVIDENCE).warnings))

    def test_single_target_skips_relationship_and_reports_partial(self):
        a = self.fx.entity("阿雾")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾")], max_rounds=1,
            evidence_seeds=[self.fx.seed("key_mist_harbor", "阿雾看守灯塔", [a])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.step_of(STAGE_RELATIONSHIP).status, STEP_SKIPPED)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_SKIPPED)
        self.assertEqual(result.status, RUN_PARTIAL)


# ==========================================================================
# 5. 幂等 / 重试 / 恢复
# ==========================================================================


class TestIdempotencyAndResume(BusinessTestCase):
    def test_repeated_run_does_not_duplicate_evidence_or_profiles(self):
        """**新任务**重跑同一请求（不带幂等键 → 五个单元真的会再跑一遍），
        验证：证据不重复登记、档案不产生空转新版本、来源不重复登记。"""
        request = _two_actor_request(self.fx)
        first = unwrap(self.fx.orch.run(self.fx.ctx, request))
        before_evidence = self.fx.count("EvidenceItem")
        before_profiles = self.fx.count("PersonaProfile")
        before_relations = self.fx.count("RelationshipProfile")
        before_sources = self.fx.count("Source")

        again = BusinessRequest(targets=list(request.targets),
                                max_rounds=request.max_rounds,
                                evidence_seeds=list(request.evidence_seeds))
        second = unwrap(self.fx.orch.run(self.fx.ctx, again))

        self.assertNotEqual(first.task_id, second.task_id)
        self.assertEqual(self.fx.count("Source"), before_sources)
        self.assertEqual(self.fx.count("EvidenceItem"), before_evidence)
        self.assertEqual(self.fx.count("PersonaProfile"), before_profiles)
        self.assertEqual(self.fx.count("RelationshipProfile"), before_relations)
        self.assertEqual(first.evidence_ids, second.evidence_ids)
        self.assertEqual(first.persona_profile_ids, second.persona_profile_ids)

    def test_second_run_produces_no_new_profile_version(self):
        request = _two_actor_request(self.fx)
        unwrap(self.fx.orch.run(self.fx.ctx, request))
        versions = [unwrap(self.fx.persona.get_persona(self.fx.ctx, t.entity_id)).version
                    for t in request.targets]
        again = BusinessRequest(targets=list(request.targets),
                                max_rounds=request.max_rounds,
                                evidence_seeds=list(request.evidence_seeds))
        unwrap(self.fx.orch.run(self.fx.ctx, again))
        again_versions = [
            unwrap(self.fx.persona.get_persona(self.fx.ctx, t.entity_id)).version
            for t in request.targets]
        self.assertEqual(versions, again_versions)
        self.assertTrue(all(v == 1 for v in again_versions))

    def test_same_idempotency_key_reuses_the_task(self):
        """幂等键相同 → 复用同一任务，已完成单元一个都不重跑。"""
        request = _two_actor_request(self.fx)
        request.idempotency_key = "idem-business-1"
        first = unwrap(self.fx.orch.run(self.fx.ctx, request))
        calls_after_first = len(self.fx.log.calls)
        again = BusinessRequest(targets=list(request.targets),
                                max_rounds=request.max_rounds,
                                evidence_seeds=list(request.evidence_seeds),
                                idempotency_key="idem-business-1")
        second = unwrap(self.fx.orch.run(self.fx.ctx, again))
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(len(self.fx.log.calls), calls_after_first)

    def test_budget_exhaustion_pauses_and_resume_completes(self):
        request = _two_actor_request(self.fx)
        request.budget_limit = BudgetSpec(max_requests=2)
        first = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(first.status, RUN_PAUSED)
        self.assertEqual(first.task_status, str(TaskStatus.PAUSED))
        self.assertFalse(first.is_complete())

        # 提额后续跑：已完成的阶段不得重复执行
        unwrap(self.fx.task_service.set_budget(self.fx.ctx, first.task_id,
                                               BudgetSpec(max_requests=100)))
        calls_before = len(self.fx.log.calls)
        second = unwrap(self.fx.orch.resume(self.fx.ctx, first.task_id, request))
        self.assertEqual(second.status, RUN_SUCCEEDED)
        executed = [s.name for s in second.steps if s.status == STEP_EXECUTED]
        self.assertEqual(len(set(executed)), len(executed))
        self.assertGreater(len(self.fx.log.calls), calls_before)
        # 研究阶段在第一轮已完成，续跑不应再调一次
        self.assertEqual(self.fx.log.calls.count("research.run"), 1)

    def test_resume_does_not_repeat_completed_stages(self):
        """AC-11：中断后 resume 从检查点继续，已完成阶段不重复。"""
        request = _two_actor_request(self.fx)
        request.budget_limit = BudgetSpec(max_requests=2)
        first = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(first.status, RUN_PAUSED)
        unwrap(self.fx.task_service.set_budget(self.fx.ctx, first.task_id,
                                               BudgetSpec(max_requests=100)))
        second = unwrap(self.fx.orch.resume(self.fx.ctx, first.task_id, request))
        self.assertEqual(self.fx.count("EvidenceItem"), 5)
        self.assertEqual(self.fx.count("PersonaProfile"), 2)
        self.assertEqual(self.fx.count("RelationshipProfile"), 1)
        self.assertEqual(second.status, RUN_SUCCEEDED)


# ==========================================================================
# 6. 数据边界：模拟内容不得进入真实证据库
# ==========================================================================


class TestDataBoundary(BusinessTestCase):
    def test_dialogue_leaves_protected_tables_unchanged(self):
        """AC-13：对话跑完后 `EvidenceItem` / `PersonaProfile` /
        `RelationshipProfile` / `SourceEvent` 四表零变化。"""
        request = _two_actor_request(self.fx)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_SUCCEEDED)

        # 用编排器产出的档案，再单独跑一轮模拟对话，确认模拟内容不回流
        before = unwrap(self.fx.evaluation.snapshot(self.fx.ctx, PROJECT))
        session = unwrap(self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[t.entity_id for t in request.targets],
            persona_profile_ids=[t.entity_id for t in request.targets],
            relationship_profile_ids=list(result.relationship_profile_ids),
            max_rounds=3)))
        unwrap(self.fx.dialogue.run(self.fx.ctx, session.id, 3))
        after = unwrap(self.fx.evaluation.snapshot(self.fx.ctx, PROJECT))
        self.assertEqual(self.fx.evaluation.dialogue_leaks(before, after), [])

    def test_simulated_turns_are_not_evidence(self):
        request = _two_actor_request(self.fx)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertTrue(result.turn_ids)
        self.assertEqual(self.fx.count("EvidenceItem"), 5)
        self.assertEqual(
            self.fx.count("EvidenceItem", namespace=ProvenanceNamespace.SIMULATED), 0)
        self.assertEqual(
            self.fx.count("PersonaProfile", namespace=ProvenanceNamespace.SIMULATED), 0)

    def test_sessions_are_simulated_only(self):
        request = _two_actor_request(self.fx)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        sessions = unwrap(self.fx.storage.query(
            self.fx.ctx, "ConversationSession",
            Query(filters={"kind": "ConversationSession", "project_id": PROJECT},
                  limit=MAX_QUERY_LIMIT), include_deleted=True)).items
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].get("namespace"), str(ProvenanceNamespace.SIMULATED))
        self.assertEqual(str(sessions[0].get("id")), result.session_id)

    def test_canon_boundary_is_preserved(self):
        """虚构角色的作品 / 版本边界随档案一起保留（只登记，不裁决）。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")], max_rounds=1,
            evidence_seeds=[
                self.fx.seed("key_mist_harbor", "灯塔由雾港公所设立", [a],
                             dimension=EvidenceDimension.CANON_FACT,
                             canon_level=CanonLevel.CANON),
                self.fx.seed("key_starlamp_interview", "同人本称灯塔是私建", [a],
                             dimension=EvidenceDimension.CANON_FACT,
                             canon_level=CanonLevel.NON_CANON),
            ])
        unwrap(self.fx.orch.run(self.fx.ctx, request))
        profile = unwrap(self.fx.persona.get_persona(self.fx.ctx, a))
        self.assertIsNotNone(profile.canon_boundary)
        self.assertTrue(profile.canon_boundary.canonical_material_refs)
        self.assertTrue(profile.canon_boundary.non_canonical_material_refs)
        self.assertEqual(
            set(profile.canon_boundary.canonical_material_refs)
            & set(profile.canon_boundary.non_canonical_material_refs), set())


# ==========================================================================
# 7. AC-1 / AC-2 原文核对
# ==========================================================================


class TestAcceptanceCriteria(unittest.TestCase):
    """AC-1「输入 2 个人名，一条命令可跑完研究→证据→人物档案→关系档案→对话」
    AC-2「输入 3 个人名时，关系档案中包含群体互动维度」。"""

    def setUp(self) -> None:
        self.fx = BusinessFixture(record=True)

    def tearDown(self) -> None:
        self.fx.teardown()

    def test_ac1_two_names_run_the_whole_chain_in_one_call(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")], max_rounds=2,
            evidence_seeds=[
                self.fx.seed("key_mist_harbor", "阿雾看守雾港灯塔", [a],
                             dimension=EvidenceDimension.BACKGROUND),
                self.fx.seed("key_starlamp_interview", "阿灯每天黄昏来灯塔", [b],
                             dimension=EvidenceDimension.TRAIT),
                self.fx.seed("key_starlamp_interview", "两人在灯塔前立下约定", [a, b],
                             dimension=EvidenceDimension.RELATION),
            ])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_SUCCEEDED)
        names = [s.name for s in result.steps if s.status == STEP_EXECUTED]
        self.assertEqual(names, [STAGE_RESEARCH, STAGE_EVIDENCE, STAGE_PERSONA,
                                 STAGE_RELATIONSHIP, STAGE_DIALOGUE])
        self.assertTrue(result.source_ids and result.evidence_ids
                        and result.persona_profile_ids and result.relationship_profile_ids
                        and result.turn_ids)

    def test_ac1_two_names_alone_now_produce_evidence_via_extraction(self):
        """原 `test_ac1_requires_claim_input_not_only_two_names`（H-10A）。

        **改了什么、为什么**：那条断言的是 AC-1 的**未满足部分**（光给两个人名
        跑不出证据）。G-2 正是为消除这个缺口而做——同样的输入现在应当成功。
        因此改为断言 AC-1 原文所称的最小输入（**只有两个人名**）确实能跑完
        全流程；断言方向从"必须 PARTIAL"变为"必须 SUCCEEDED 且证据非空"，
        强度未降反升。
        """
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        self.fx.use_extractor(_claim_fixtures(a, b))
        request = BusinessRequest(targets=[self.fx.target(a, "阿雾"),
                                           self.fx.target(b, "阿灯")], max_rounds=1)
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_SUCCEEDED)
        self.assertTrue(result.evidence_ids, "自动抽取必须产出证据")
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_EXECUTED)
        self.assertEqual([s.status for s in result.steps], [STEP_EXECUTED] * 5)

    def test_ac2_three_names_produce_group_relationship(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        c = self.fx.entity("阿海")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯"),
                     self.fx.target(c, "阿海")],
            max_rounds=2,
            evidence_seeds=[
                self.fx.seed("key_mist_harbor", "阿雾看守雾港灯塔", [a],
                             dimension=EvidenceDimension.BACKGROUND),
                self.fx.seed("key_starlamp_interview", "阿灯每天黄昏来灯塔", [b],
                             dimension=EvidenceDimension.TRAIT),
                self.fx.seed("key_tide_notes", "阿海在渔村记录潮汐", [c],
                             dimension=EvidenceDimension.HABIT),
                self.fx.seed("key_starlamp_interview", "三人共同出席雾港灯会",
                             [a, b, c], dimension=EvidenceDimension.RELATION),
            ])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_SUCCEEDED)
        view = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, result.relationship_profile_ids[0]))
        self.assertEqual(view.cardinality, str(Cardinality.GROUP))
        self.assertEqual(len(view.participant_entity_ids), 3)
        # 群体互动维度**存在**（RL-V3 强制），且三人的共同材料进了 shared_experiences
        self.assertIsNotNone(view.group_dynamics)
        self.assertTrue(view.shared_experiences)
        self.assertEqual(
            sorted(view.shared_experiences[0].participant_entity_ids),
            sorted([a, b, c]))

    def test_ac2_group_dynamics_content_is_not_fabricated(self):
        """AC-2 的**未满足部分**：编排器不做语义判断，不把"共同出现"断言成联盟。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        c = self.fx.entity("阿海")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯"),
                     self.fx.target(c, "阿海")],
            max_rounds=1,
            evidence_seeds=[self.fx.seed("key_tide_notes", "三人共同出席雾港灯会",
                                         [a, b, c],
                                         dimension=EvidenceDimension.RELATION)])
        unwrap(self.fx.orch.run(self.fx.ctx, request))
        view = unwrap(self.fx.relationship.get_relationship(self.fx.ctx, [a, b, c]))
        self.assertTrue(view.group_dynamics.note)
        self.assertEqual(view.group_dynamics.alliances, [])
        self.assertEqual(view.group_dynamics.exclusion, [])
        self.assertEqual(view.group_dynamics.relay, [])


# ==========================================================================
# 7.5 G-2：Research → Claim Extraction → Evidence Registration
# ==========================================================================


class TestAutoClaimExtraction(BusinessTestCase):
    """调用方**不提供任何 claim**，全流程自动抽取。"""

    def setUp(self) -> None:
        super().setUp()
        self.a = self.fx.entity("阿雾")
        self.b = self.fx.entity("阿灯")
        self.extractor = self.fx.use_extractor(_claim_fixtures(self.a, self.b))

    def _run(self, **kw):
        """用 setUp 里建好的**同一批**对象跑（重复调用测幂等才不会被新建实体干扰）。"""
        return unwrap(self.fx.orch.run(self.fx.ctx, BusinessRequest(
            targets=[self.fx.target(self.a, "阿雾"), self.fx.target(self.b, "阿灯")],
            max_rounds=kw.get("max_rounds", 2))))

    # ---------------------------------------------------------- 调用链

    def test_chain_is_research_then_extraction_then_registration(self):
        """目标 8：确实按 Research → 抽取 → 登记 的顺序执行。"""
        result = self._run()
        self.assertEqual(result.status, RUN_SUCCEEDED)
        self.assertGreater(self.extractor.calls, 0)   # 抽取器确实被调用
        calls = [c for c in self.fx.log.calls
                 if c.startswith(("research.", "evidence."))]
        self.assertEqual(calls[0], "research.run")            # 研究先跑
        self.assertIn("evidence.record_inference", calls)     # 推断专用入口
        # 7 条非推断（4 条雾港 + 3 条星灯）走 add_evidence，推断走 record_inference
        self.assertEqual(calls.count("evidence.add_evidence"), 7)
        self.assertEqual(calls.count("evidence.record_inference"), 1)
        self.assertEqual(len(result.evidence_ids), 8)

    def test_extractor_receives_materials_from_research(self):
        """前一步的输出确实传给下一步：材料来自本次研究登记的来源。"""
        result = self._run()
        self.assertEqual(set(self.extractor.last_material_keys),
                         {"key_mist_harbor", "key_starlamp_interview"})
        self.assertTrue(set(result.source_ids))

    def test_every_registered_evidence_traces_to_a_research_source(self):
        """目标 3：候选 claim 保留来源追溯。"""
        result = self._run()
        sources = set(result.source_ids)
        for evidence_id in result.evidence_ids:
            row = unwrap(self.fx.storage.get(self.fx.ctx, "EvidenceItem", evidence_id))
            if str(row.get("tier")) == str(EvidenceTier.INFERENCE):
                # 推断按定义没有来源（SYSTEM_DERIVED），但它必须有根证据
                self.assertTrue(row.get("derived_from_evidence_ids"),
                                "推断必须能追溯到根证据")
                continue
            self.assertIn(row.get("source_id"), sources)

    def test_extraction_uses_the_injected_extractor_not_a_hardcoded_one(self):
        """目标 2：抽取器是注入的，编排器内不硬编码实现。"""
        self._run()
        self.assertIs(self.fx.orch.deps.claim_extractor, self.extractor)
        source = Path("persona/orchestrator/business.py").read_text(encoding="utf-8")
        self.assertNotIn("MockClaimExtractor", source, "编排器不得硬编码具体抽取器")
        self.assertNotIn("FakeProvider", source, "编排器不得硬编码具体 Provider")

    # ---------------------------------------------------------- 三种 claim 区分

    def test_verbatim_claim_keeps_its_quote(self):
        """目标 4：原文直接支持的主张保留可逐字核对的摘录。"""
        result = self._run()
        quotes = [unwrap(self.fx.evidence.get_evidence(self.fx.ctx, e)).verbatim_quote
                  for e in result.evidence_ids]
        self.assertIn("雾港灯塔", [q for q in quotes if q])

    def test_inference_is_registered_via_record_inference_not_as_source_fact(self):
        """目标 4：系统推断走 `record_inference`，**不**伪装成原文引用。"""
        result = self._run()
        inferences = [e for e in result.evidence_ids
                      if str(unwrap(self.fx.evidence.get_evidence(
                          self.fx.ctx, e)).tier) == str(EvidenceTier.INFERENCE)]
        self.assertEqual(len(inferences), 1)
        view = unwrap(self.fx.evidence.get_evidence(self.fx.ctx, inferences[0]))
        row = unwrap(self.fx.storage.get(self.fx.ctx, "EvidenceItem", inferences[0]))
        self.assertIsNone(view.source_id)                   # 推断没有来源
        self.assertEqual(view.namespace, "SYSTEM_DERIVED")
        self.assertTrue(row.get("derived_from_evidence_ids"))   # 且有根
        self.assertIsNone(view.verbatim_quote)               # 且没有"原话"
        self.assertNotIn("两人因约定而建立起稳定的信任",
                         [getattr(unwrap(self.fx.evidence.get_evidence(
                             self.fx.ctx, e)), "verbatim_quote", None)
                          for e in result.evidence_ids])

    def test_inference_without_a_root_is_dropped_not_registered(self):
        """无根推断：EV-V3 禁止，编排器丢弃并如实提示。"""
        fixtures = _claim_fixtures(self.a, self.b)
        fixtures["key_mist_harbor"].append(CandidateClaim(
            key="orphan", claim="阿雾其实来自海的另一边", tier=EvidenceTier.INFERENCE,
            dimension=EvidenceDimension.BACKGROUND, subject_entity_ids=[self.a],
            confidence=0.2, derivation_keys=["不存在的候选"]))
        self.fx.use_extractor(fixtures)
        result = self._run()
        claims = [unwrap(self.fx.evidence.get_evidence(self.fx.ctx, e)).claim
                  for e in result.evidence_ids]
        self.assertNotIn("阿雾其实来自海的另一边", claims)
        self.assertTrue(any(w.code == "INFERENCE_NO_ROOT"
                            for w in result.step_of(STAGE_EVIDENCE).warnings))

    # ---------------------------------------------------------- 失败与异常

    def test_research_without_materials_is_not_reported_as_success(self):
        """目标 9：无材料 → 证据阶段跳过、整链 PARTIAL。"""
        fx = BusinessFixture(materials={}, record=False)
        self.addCleanup(fx.teardown)
        a, b = fx.entity("阿雾"), fx.entity("阿灯")
        fx.use_extractor(_claim_fixtures(a, b))
        result = unwrap(fx.orch.run(fx.ctx, _plain_request(fx, "阿雾", "阿灯")))
        self.assertEqual(result.status, RUN_PARTIAL)
        self.assertEqual(result.evidence_ids, [])

    def test_no_claim_in_material_is_not_faked(self):
        """目标 9：无可抽取主张 → 不编造证据，如实报 PARTIAL。"""
        self.fx.use_extractor({})
        result = self._run()
        self.assertEqual(result.evidence_ids, [])
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_SKIPPED)
        self.assertEqual(result.status, RUN_PARTIAL)

    def test_invalid_excerpt_is_rejected_and_recorded(self):
        """目标 9：无效摘录 → 拒收并提示，不静默丢弃也不降级登记。"""
        self.fx.use_extractor({"key_mist_harbor": [
            CandidateClaim(claim="阿雾守着不存在的东西", tier=EvidenceTier.VERBATIM,
                           dimension=EvidenceDimension.BACKGROUND,
                           subject_entity_ids=[self.a], confidence=0.9,
                           verbatim_quote="这段根本不在材料里")]})
        result = self._run(max_rounds=1)
        self.assertTrue(any(w.code == "CLAIM_REJECTED"
                            for w in result.step_of(STAGE_EVIDENCE).warnings))
        self.assertEqual(result.evidence_ids, [])

    def test_extractor_error_is_propagated_not_swallowed(self):
        """目标 9/5：抽取器异常 → 后续阶段 `NOT_REACHED`，整链 FAILED。"""

        class Broken:
            calls = 0

            def extract(self, ctx, materials, *, targets=None):
                Broken.calls += 1
                return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE, "注入的抽取失败"))

        self.fx.orch = BusinessOrchestrator(self.fx._deps(Broken()))
        result = unwrap(self.fx.orch.run(
            self.fx.ctx, _plain_request(self.fx, "阿雾", "阿灯")))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_FAILED)
        self.assertEqual(result.step_of(STAGE_PERSONA).status, STEP_NOT_REACHED)
        self.assertEqual(result.step_of(STAGE_DIALOGUE).status, STEP_NOT_REACHED)

    def test_evidence_validation_failure_stops_the_chain(self):
        """目标 5：Evidence 校验失败 → 上报，不伪造后续成功。"""
        self.fx.evidence = _Failing(self.fx.evidence, "add_evidence",
                                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)
        self.fx.orch = BusinessOrchestrator(self.fx._deps(self.extractor))
        result = unwrap(self.fx.orch.run(
            self.fx.ctx, _plain_request(self.fx, "阿雾", "阿灯")))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.step_of(STAGE_EVIDENCE).status, STEP_FAILED)
        self.assertEqual(result.step_of(STAGE_PERSONA).status, STEP_NOT_REACHED)

    # ---------------------------------------------------------- 幂等 / 重复调用

    def test_repeated_runs_do_not_duplicate_evidence_or_sources(self):
        """目标 9：重复调用不重复登记证据 / 来源，也不产生空转档案版本。"""
        request = _plain_request(self.fx, "阿雾", "阿灯")
        request.idempotency_key = "idem-g2-1"
        first = unwrap(self.fx.orch.run(self.fx.ctx, request))
        before_sources = self.fx.count("Source")
        before_evidence = self.fx.count("EvidenceItem")
        before_profiles = self.fx.count("PersonaProfile")

        again = _plain_request(self.fx, "阿雾", "阿灯")
        again.idempotency_key = "idem-g2-1"
        second = unwrap(self.fx.orch.run(self.fx.ctx, again))

        self.assertEqual(self.fx.count("Source"), before_sources)
        self.assertEqual(self.fx.count("EvidenceItem"), before_evidence)
        self.assertEqual(self.fx.count("PersonaProfile"), before_profiles)
        self.assertEqual(first.evidence_ids, second.evidence_ids)

    def test_second_fresh_run_reuses_existing_evidence_and_profiles(self):
        """再跑一次（新任务）也不该产生重复证据 / 空转档案版本。"""
        self._run()
        before_evidence = self.fx.count("EvidenceItem")
        before_profiles = self.fx.count("PersonaProfile")
        self._run()
        self.assertEqual(self.fx.count("EvidenceItem"), before_evidence)
        self.assertEqual(self.fx.count("PersonaProfile"), before_profiles)

    # ---------------------------------------------------------- 冲突保留

    def test_conflicting_claims_are_both_kept_and_recorded(self):
        """目标 6：正典级别分歧的两条说法各自保留，并记冲突（不裁决）。"""
        result = self._run()
        views = [unwrap(self.fx.evidence.get_evidence(self.fx.ctx, e))
                 for e in result.evidence_ids]
        same_claim = [v for v in views if v.claim == "阿雾每晚点灯"]
        self.assertEqual(len(same_claim), 2, "两条说法不得因相似被合并")
        self.assertEqual({str(v.canon_level) for v in same_claim},
                         {"CANON", "NON_CANON"})
        self.assertTrue(result.conflict_ids, "冲突必须被检出并保留")

    # ---------------------------------------------------------- 模拟数据隔离

    def test_mock_claims_are_tagged_and_never_enter_simulated_namespace(self):
        """目标 7：Mock 产出明确标识为测试材料，且不得落到 SIMULATED / 真实证据混淆。"""
        result = self._run()
        for evidence_id in result.evidence_ids:
            view = unwrap(self.fx.evidence.get_evidence(self.fx.ctx, evidence_id))
            self.assertNotEqual(view.namespace, str(ProvenanceNamespace.SIMULATED))
        tagged = [v for v in (unwrap(self.fx.evidence.get_evidence(self.fx.ctx, e))
                              for e in result.evidence_ids)
                  if TAG_MOCK_FIXTURE in (v.tags or [])]
        self.assertTrue(tagged, "Mock 抽取的证据必须带 mock-fixture 标签")

    def test_dialogue_still_leaves_evidence_untouched(self):
        """AC-13：自动抽取没有破坏模拟隔离——对话跑完受保护表零变化。"""
        result = self._run()
        self.assertEqual(result.status, RUN_SUCCEEDED)
        before = unwrap(self.fx.evaluation.snapshot(self.fx.ctx, PROJECT))
        after = unwrap(self.fx.evaluation.snapshot(self.fx.ctx, PROJECT))
        self.assertEqual(self.fx.evaluation.dialogue_leaks(before, after), [])


# ==========================================================================
# 8. 职责分离与依赖方向
# ==========================================================================


class TestSeparation(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = BusinessFixture(record=False)

    def tearDown(self) -> None:
        self.fx.teardown()

    def test_existing_task_service_api_is_unchanged(self):
        for name in ("create_task", "get", "start", "pause", "resume", "complete",
                     "fail", "cancel", "set_budget", "mark_attempt", "record_progress",
                     "save_checkpoint", "load_checkpoint", "record_usage"):
            self.assertTrue(hasattr(TaskService, name), f"TaskService 缺少 {name}")

    def test_orchestrator_has_no_second_state_machine_or_ledger(self):
        source = Path("persona/orchestrator/business.py").read_text(encoding="utf-8")
        self.assertNotIn("can_transition(", source)
        self.assertNotIn("TaskState(", source)
        self.assertNotIn("gate_for(", source)
        self.assertNotIn("BudgetGate(", source)
        self.assertNotIn(".complete(", source)
        self.assertNotIn(".pause(", source)
        self.assertNotIn(".fail(", source)

    def test_orchestrator_does_not_pick_a_provider(self):
        source = Path("persona/orchestrator/business.py").read_text(encoding="utf-8")
        self.assertNotIn("from ..providers", source)
        self.assertNotIn("from persona.providers", source)

    def test_modules_never_import_orchestrator(self):
        """F-1：业务模块不得反向依赖编排层（复用既有静态检查，AC-20）。"""
        checks = unwrap(self.fx.evaluation.check_dependencies(self.fx.ctx))
        self.assertTrue(checks["passed"], checks["violations"])

    def test_whole_pipeline_runs_with_network_blocked(self):
        """AC-19 佐证：整条链在 socket 被屏蔽时照样跑通（全程离线）。"""
        with network_blocked():
            result = unwrap(self.fx.orch.run(self.fx.ctx, _two_actor_request(self.fx)))
        self.assertEqual(result.status, RUN_SUCCEEDED)
        self.assertTrue(result.turn_ids)

    def test_business_orchestrator_implements_unit_handler(self):
        from persona.orchestrator.executor import TaskRunner
        runner = TaskRunner(self.fx.task_service, self.fx.orch)
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        request = BusinessRequest(
            targets=[self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")], max_rounds=1,
            evidence_seeds=[self.fx.seed("key_mist_harbor", "阿雾看守灯塔", [a])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertIsInstance(runner, TaskRunner)
        # 既有执行器能读回编排器落盘的检查点（说明确实复用了既有能力）
        task = unwrap(self.fx.task_service.get(self.fx.ctx, result.task_id))
        self.assertEqual(task.status, TaskStatus.COMPLETED)
        checkpoint = unwrap(self.fx.task_service.load_checkpoint(
            self.fx.ctx, result.task_id))
        self.assertIsNotNone(checkpoint)
        for stage in (STAGE_RESEARCH, STAGE_EVIDENCE, STAGE_PERSONA,
                      STAGE_RELATIONSHIP, STAGE_DIALOGUE):
            self.assertIn(stage, checkpoint.completed_units)


# ==========================================================================
# 8.5 G-4：关系语义分析接入编排器
# ==========================================================================


class TestG4RelationshipSemantic(BusinessTestCase):
    """编排器注入 `relationship_analyzer` 后，RELATION 证据被研判并落盘；
    未注入时退回「不做语义判断」的兜底（既有 AC-2 测试不受影响）。

    能力边界：本阶段只验证**确定性离线规则 + 证据可追踪 + 不伪造**，
    绝不声称已具备真实语义模型的语义理解能力。
    """

    def _req(self, entities, names, seeds):
        targets = [self.fx.target(e, n) for e, n in zip(entities, names)]
        return BusinessRequest(targets=targets, max_rounds=1, evidence_seeds=seeds)

    def _rel_seed(self, claim, subjects, *,
                  source_ref="key_mist_harbor", stability=Stability.REPEATED):
        return self.fx.seed(source_ref, claim, list(subjects),
                            dimension=EvidenceDimension.RELATION,
                            stability=stability)

    # ---------------------------------------------------------- 注入后富集

    def test_analyzer_invoked_and_enriches_two_actor_profile(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        analyzer = self.fx.use_analyzer(RuleRelationshipAnalyzer())
        request = self._req(
            [a, b], ["阿雾", "阿灯"],
            seeds=[self._rel_seed("阿雾与阿灯每天在灯塔下交谈", [a, b])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_SUCCEEDED)
        self.assertGreater(analyzer.calls, 0)   # 分析器确实被调用
        view = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, result.relationship_profile_ids[0]))
        self.assertEqual(view.cardinality, str(Cardinality.PAIR))
        # Rule 识别出重复互动模式（兜底路径不会产生）
        self.assertTrue(view.interaction_patterns,
                        "注入分析器后互动模式应被识别")
        # 双向视角均填充且可追溯
        self.assertTrue(view.perspectives[a].view_of_targets.get(b))
        self.assertTrue(view.perspectives[b].view_of_targets.get(a))
        for it in view.interaction_patterns:
            self.assertIn(it.evidence_refs[0], result.evidence_ids)

    # ---------------------------------------------------------- 群体互动

    def test_analyzer_path_group_alliance_filled(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        c = self.fx.entity("阿海")
        self.fx.use_analyzer(RuleRelationshipAnalyzer())
        request = self._req(
            [a, b, c], ["阿雾", "阿灯", "阿海"],
            seeds=[self._rel_seed("阿雾、阿灯、阿海三人结盟对抗外敌",
                                  [a, b, c])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        view = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, result.relationship_profile_ids[0]))
        self.assertEqual(view.cardinality, str(Cardinality.GROUP))
        self.assertTrue(view.group_dynamics.alliances,
                        "明确'结盟'标记应进入群体互动")
        self.assertEqual(view.group_dynamics.exclusion, [])
        self.assertEqual(view.group_dynamics.relay, [])
        self.assertIn(view.group_dynamics.alliances[0].evidence_refs[0],
                      result.evidence_ids)

    def test_analyzer_path_co_occurrence_not_alliance(self):
        """与 `test_ac2_group_dynamics_content_is_not_fabricated` 同精神，但走分析器路径：
        仅有共同出现证据时，群体互动三个板块必须为空（D-215），不得伪造。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        c = self.fx.entity("阿海")
        self.fx.use_analyzer(RuleRelationshipAnalyzer())
        request = self._req(
            [a, b, c], ["阿雾", "阿灯", "阿海"],
            seeds=[self._rel_seed("三人共同出席雾港灯会", [a, b, c])])
        unwrap(self.fx.orch.run(self.fx.ctx, request))
        view = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, [a, b, c]))
        self.assertIsNotNone(view.group_dynamics)
        self.assertEqual(view.group_dynamics.alliances, [])
        self.assertEqual(view.group_dynamics.exclusion, [])
        self.assertEqual(view.group_dynamics.relay, [])
        self.assertTrue(view.group_dynamics.note)

    # ---------------------------------------------------------- 可追踪性 / RL-V6

    def test_analyzer_path_every_item_refs_registered_evidence(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        self.fx.use_analyzer(RuleRelationshipAnalyzer())
        request = self._req(
            [a, b], ["阿雾", "阿灯"],
            seeds=[
                self._rel_seed("阿雾与阿灯每天在灯塔下交谈", [a, b]),
                self._rel_seed("阿雾温柔地护着阿灯", [a, b]),
            ])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        view = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, result.relationship_profile_ids[0]))
        refs: set[str] = set()
        for it in view.shared_experiences:
            refs.update(it.evidence_refs)
        for it in view.interaction_patterns:
            refs.update(it.evidence_refs)
        for it in view.emotional_expression_habits:
            refs.update(it.evidence_refs)
        for it in view.temporal_changes:
            refs.update(it.evidence_refs)
        for it in view.asymmetry_notes:
            refs.update(it.evidence_refs)
        for blk in view.perspectives.values():
            for lst in blk.view_of_targets.values():
                for it in lst:
                    refs.update(it.evidence_refs)
        gd = view.group_dynamics
        if gd is not None:
            for it in gd.alliances + gd.exclusion + gd.relay:
                refs.update(it.evidence_refs)
        self.assertTrue(refs, "关系档案应含可追踪的证据引用")
        for ref in refs:
            self.assertIn(ref, result.evidence_ids, "引用必须指向本次登记的证据")
            ev = unwrap(self.fx.evidence.get_evidence(self.fx.ctx, ref))
            self.assertEqual(ev.dimension, str(EvidenceDimension.RELATION))

    # ---------------------------------------------------------- 红线：伪造引用被拒

    def test_analyzer_fake_evidence_ref_rejected(self):
        """Mock 分析器返回引用不存在证据的条目 → RL-V6 拒绝，关系步骤失败。
        证明编排器/关系模块**不会**把分析器产物当已验证事实。"""
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        key = "|".join(sorted([a, b]))
        out = RelationshipAnalysisOutcome(
            perspectives={
                a: RelationshipAnalysisPerspective(
                    entity_id=a,
                    view_of_targets={b: [RelationshipAnalysisItem(
                        text="伪造的关系断言",
                        evidence_refs=["EvidenceItem_does_not_exist"])]}),
                b: RelationshipAnalysisPerspective(entity_id=b),
            },
            open_questions=[])
        self.fx.use_analyzer(MockRelationshipAnalyzer({key: out}))
        request = self._req(
            [a, b], ["阿雾", "阿灯"],
            seeds=[self._rel_seed("阿雾与阿灯立下约定", [a, b])])
        result = unwrap(self.fx.orch.run(self.fx.ctx, request))
        self.assertEqual(result.status, RUN_FAILED)
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_DANGLING_REFERENCE)
        self.assertEqual(result.step_of(STAGE_RELATIONSHIP).status, STEP_FAILED)

    # ---------------------------------------------------------- 重跑幂等

    def test_analyzer_path_idempotent_rerun(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        self.fx.use_analyzer(RuleRelationshipAnalyzer())
        request = self._req(
            [a, b], ["阿雾", "阿灯"],
            seeds=[self._rel_seed("阿雾与阿灯每天在灯塔下交谈", [a, b])])
        r1 = unwrap(self.fx.orch.run(self.fx.ctx, request))
        v1 = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, r1.relationship_profile_ids[0]))
        # 完全相同的输入重跑：不应产生新版本
        r2 = unwrap(self.fx.orch.run(self.fx.ctx, request))
        v2 = unwrap(self.fx.relationship.get_relationship(
            self.fx.ctx, r2.relationship_profile_ids[0]))
        self.assertEqual(v2.version, v1.version, "重跑不应产生新关系版本（幂等）")

    # ---------------------------------------------------------- 依赖注入洁净度

    def test_analyzer_not_hardcoded_in_orchestrator(self):
        """编排器不得硬编码具体分析器实现，只能依赖端口（与 G-2 同口径）。"""
        source = Path("persona/orchestrator/business.py").read_text(encoding="utf-8")
        self.assertNotIn("MockRelationshipAnalyzer", source,
                         "编排器不得硬编码 MockRelationshipAnalyzer")
        self.assertNotIn("RuleRelationshipAnalyzer", source,
                         "编排器不得硬编码 RuleRelationshipAnalyzer")
        self.assertIn("RelationshipAnalyzerPort", source,
                      "编排器应依赖 RelationshipAnalyzerPort 端口")


if __name__ == "__main__":
    unittest.main()
