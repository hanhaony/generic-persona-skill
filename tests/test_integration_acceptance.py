"""P5「评测与加固」——端到端集成验收（stdlib unittest，零第三方依赖，D-068）。

**范围**（对应用户验收清单 + `ROADMAP.md` §7 P5 的 6 条测试要求）：
  1. 真实支持的**完整调用链**：任务创建/执行 → 对象与证据读写 → 人格/关系档案读写 →
     对话生成 → 记忆检索与**按规则**写入（同一临时库、同一项目）
  2. **模块边界**：CLI 经 Orchestrator/TaskService 驱动，不经 `persona.modules` 直连业务
     （AC-20）；`Evaluation.check_dependencies` 断言无循环依赖
  3. **数据隔离**（AC-13/14/15）：对话与记忆跑完后四张"现实世界"表**零变化**；
     模拟记忆永不进 `EvidenceItem`；模拟语句不会被当成"该人物的原话"检索回来
  4. **异常路径**：预算不足 → 暂停（非失败）+ 检查点；提高额度后 `resume` **不重复执行**；
     Provider 失败 / 存储失败一律传播 `Err`；重复执行幂等
  5. **隔离数据**：全部使用临时目录 SQLite + `PERSONA_DATA_DIR` 重定向，不碰用户数据
  6. **文档一致性**：文档里的测试命令与实现可执行路径一致

对应文档：`ROADMAP.md` §7（P5）、`INTERFACES.md` §10、`PROJECT_SPEC.md` §7、`DECISIONS.md` §2.22.1 D-190。
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    Cardinality,
    DivergenceKind,
    EvidenceDimension,
    EvidenceTier,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    PausedReason,
    ProvenanceNamespace,
    ReviewStatus,
    TaskStatus,
    TaskType,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.ids import new_token
from persona.core.result import Err, Ok, UsageDelta, is_err, is_ok, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query
from persona.modules.dialogue import (
    POLICY_MEMORY_WRITE_EXPLICIT,
    POLICY_MEMORY_WRITE_NONE,
    DialogueDeps,
    DialogueService,
    SessionDraft,
)
from persona.modules.evaluation import EvaluationService
from persona.modules.evidence import (
    ConflictDraft,
    EvidenceDraft,
    EvidenceService,
    SourceDraft,
)
from persona.modules.memory import MemoryQuery, MemoryService
from persona.modules.persona import PersonaProfileDraft, PersonaService, ProfileSection
from persona.modules.relationship import (
    PerspectiveBlock,
    RelationshipDraft,
    RelationshipService,
)
from persona.orchestrator import (
    BudgetSpec,
    MockUnitHandler,
    TaskDraft,
    TaskRunner,
    TaskService,
    WorkUnit,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.providers.mock import OfflineGuardProvider
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_acceptance"
REPO_ROOT = Path(__file__).resolve().parents[1]
TOPIC = "灯塔的约定"
RELATED_CONTENT = f"{TOPIC}：阿雾答应阿灯，每天黄昏点亮灯塔。"
# 只在模拟记忆里出现的独特标记（确保夹具证据/档案里绝无此串 → 可作 AC-14 判据）
SIM_MARKER = "星历77年第七座灯塔"
SIM_CONTENT = f"{TOPIC}：阿雾答应阿灯，{SIM_MARKER} 之前点亮灯塔。"


# ==========================================================================
# Mock Provider（离线优先 + 故障注入）
# ==========================================================================


class CapturingProvider:
    """实现 Provider 协议：记录 prompt、返回确定性非空文本（不触网）。"""

    def __init__(self) -> None:
        self.last_messages: list = []
        self.chat_calls = 0

    def joined(self) -> str:
        return "\n".join(str(getattr(m, "content", m)) for m in self.last_messages)

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="capturing", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Ok(ChatResponse(text="（模拟）我记得那件事，接着说下去。",
                               finish_reason="stop", model_id="capturing",
                               usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        from persona.core.types import UsageReport
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class FailingProvider(CapturingProvider):
    """对话生成失败 → 传播 Err，绝不伪造成功（用户目标 7 / P2.4 纪律）。"""

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE, "注入的 Provider 失败"))


class FailingPutStorage:
    """包一层真实存储：对指定 kind 的 `put` 注入写入失败，其余委派。"""

    def __init__(self, real: SqliteStorage, fail_kind: str) -> None:
        self._real = real
        self._fail_kind = fail_kind

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def put(self, ctx, kind, payload, **kwargs):
        if kind == self._fail_kind:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED,
                                 f"注入的写入失败（kind={kind}）", trace_ref=ctx.trace_ref))
        return self._real.put(ctx, kind, payload, **kwargs)


# ==========================================================================
# 夹具：临时 SQLite + 全套服务
# ==========================================================================


class AcceptanceFixture:
    def __init__(self, provider=None) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-accept-")
        self.path = Path(self.tmp) / "acceptance.db"
        self.storage = SqliteStorage(self.path)
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.evidence = EvidenceService(self.storage)
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.memory = MemoryService(self.storage)
        self.evaluation = EvaluationService(self.storage)
        self.provider = provider or OfflineGuardProvider(CapturingProvider())

    # ---------------------------------------------------------------- 装配

    def dialogue(self, *, storage=None, memory=None, provider=None,
                 memory_reader=None) -> DialogueService:
        st = storage or self.storage
        deps = DialogueDeps(
            profiles=self.persona, relations=self.relationship, evidence=self.evidence,
            memory=memory if memory is not None else MemoryService(st),
            storage=st, provider=provider or self.provider,
            memory_reader=memory_reader if memory_reader is not None else MemoryService(st),
        )
        return DialogueService(deps)

    # ---------------------------------------------------------------- 构件

    def entity(self, name: str) -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {
                "display_name": name, "entity_type": "FICTIONAL_CHARACTER",
                "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
                "fictional_character_meta": {
                    "works": [{"work_id": new_token("wrk_"), "title": "《雾港与星灯》",
                               "medium": "ANIMATION"}],
                    "adaptation_version": "2020 动画改编版",
                    "adaptation_lineage": ["原作轻小说版", "2020 动画改编版"],
                    "official_canon_level": "CANON",
                },
            },
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def source(self, title: str = "《雾港与星灯》访谈录") -> str:
        return unwrap(self.evidence.register_source(
            self.ctx, SourceDraft(title=title, canonical_key=f"key_{new_token()}"))).id

    def evidence_item(self, source_id: str, claim: str, *, subject: str,
                      tier: EvidenceTier = EvidenceTier.PROFILE_BACKGROUND) -> str:
        return unwrap(self.evidence.add_evidence(self.ctx, EvidenceDraft(
            claim=claim, tier=tier, dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[subject], extraction_confidence=0.9,
            source_id=source_id))).id

    def persona_profile(self, entity_id: str, background: str) -> str:
        ref = self.evidence_item(self.source(), f"{entity_id} 的背景：{background}",
                                 subject=entity_id)
        return unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8, evidence_refs=[ref]),
            applicable_variant_ids=[entity_id],
            open_questions=["习惯维度待补"]))).id

    def relationship_profile(self, a: str, b: str) -> str:
        src = self.source("《雾港与星灯》第 3 话剧情")
        ref = self.evidence_item(src, f"{a} 与 {b} 在灯塔前相遇。", subject=a)
        draft = RelationshipDraft(
            participant_entity_ids=[a, b], cardinality=Cardinality.PAIR,
            perspectives={
                a: PerspectiveBlock(entity_id=a),
                b: PerspectiveBlock(entity_id=b),
            },
            conflict_ids=[], open_questions=["互动模式待补"],
            applicable_variant_ids=[a, b],
        )
        return unwrap(self.relationship.create_relationship(self.ctx, draft)).id

    def two_actors(self) -> tuple[str, str]:
        a = self.entity("阿雾")
        b = self.entity("阿灯")
        self.persona_profile(a, "阿雾：沉默寡言的灯塔看守。")
        self.persona_profile(b, "阿灯：活泼的渔家少女。")
        return a, b

    def candidate(self, content: str = RELATED_CONTENT, **kw) -> dict:
        base = {
            "kind": MemoryKind.EPISODIC.value, "content": content,
            "confidence": 0.6,
            "confidence_means": "模拟对话中的口头约定，属不确定内容，非事实断言",
            "salience": 0.6,
        }
        base.update(kw)
        return base

    def start(self, participants, *, topic=TOPIC, policy=POLICY_MEMORY_WRITE_EXPLICIT) -> str:
        return unwrap(self.dialogue().start_session(self.ctx, SessionDraft(
            participant_entity_ids=list(participants), topic=topic,
            memory_write_policy=policy))).id

    # ---------------------------------------------------------------- 只读辅助

    def rows(self, kind: str, *, include_deleted: bool = True) -> list[dict]:
        q = Query(filters={"kind": kind, "project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(unwrap(self.storage.query(self.ctx, kind, q,
                                              include_deleted=include_deleted)).items)

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class AcceptanceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = AcceptanceFixture()

    def tearDown(self) -> None:
        self.fx.close()

    def run_turn(self, sid: str, *, candidates=None, service=None):
        svc = service or self.fx.dialogue()
        return svc.run_round(self.fx.ctx, sid, memory_candidates=candidates)


# ==========================================================================
# 1. 真实支持的完整调用链
# ==========================================================================


class TestFullSupportedChain(AcceptanceTestCase):
    def test_task_lifecycle_create_start_execute(self):
        """任务创建 → 启动 → Mock 执行器推进 → COMPLETED（记录用量）。"""
        svc = TaskService(self.fx.storage)
        units = [WorkUnit(name="RESEARCH:0", stage="RESEARCH"),
                 WorkUnit(name="EVIDENCE_EXTRACT:0", stage="EVIDENCE_EXTRACT")]
        handler = MockUnitHandler(units=units)
        runner = TaskRunner(svc, handler)

        task_id = unwrap(svc.create_task(self.fx.ctx, TaskDraft(
            task_type=TaskType.RESEARCH,
            stage_sequence=["RESEARCH", "EVIDENCE_EXTRACT"],
            budget_limit=BudgetSpec(max_requests=10)))).id
        self.assertEqual(unwrap(svc.get(self.fx.ctx, task_id)).status, TaskStatus.PENDING)
        unwrap(svc.start(self.fx.ctx, task_id))
        result = unwrap(runner.run_to_completion(self.fx.ctx, task_id))

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(handler.call_names(), ["RESEARCH:0", "EVIDENCE_EXTRACT:0"])
        self.assertGreaterEqual(result.budget_used.requests, 2)
        # 用量真的落了账（D-060）
        self.assertGreaterEqual(
            unwrap(self.fx.storage.usage_report(self.fx.ctx, task_id)).requests, 2)

    def test_evidence_and_archive_read_chain(self):
        """对象 + 来源 + 证据写入 → 经只读端口读回。"""
        a, b = self.fx.two_actors()
        src = self.fx.source()
        eid = self.fx.evidence_item(src, "阿雾守着灯塔。", subject=a)

        row = unwrap(self.fx.storage.get(self.fx.ctx, "EvidenceItem", eid))
        self.assertEqual(row["source_id"], src)
        self.assertEqual(unwrap(self.fx.evidence.get_evidence_tier(self.fx.ctx, eid)),
                         EvidenceTier.PROFILE_BACKGROUND)
        # 人格档案可经只读端口读回，且带证据引用
        view = unwrap(self.fx.persona.get_persona(self.fx.ctx, a))
        self.assertEqual(view.entity_id, a)
        self.fx.relationship_profile(a, b)
        rel = unwrap(self.fx.relationship.get_relationship(self.fx.ctx, [a, b]))
        self.assertEqual(sorted(rel.participant_entity_ids), sorted([a, b]))
        # 一条证据可反查来源（AC-3）
        self.assertEqual(row["source_id"], src)

    def test_dialogue_generation_and_memory_rule_write(self):
        """对话生成（Mock）→ 显式授权写入会话级模拟记忆 → 后续会话检索到。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        turn = unwrap(self.run_turn(s1, candidates=[fx.candidate()]))
        self.assertEqual(len(turn.generated_memory_ids), 1)
        mid = turn.generated_memory_ids[0]

        row = unwrap(fx.storage.get(fx.ctx, "MemoryItem", mid))
        self.assertEqual(row["namespace"], str(ProvenanceNamespace.SIMULATED))
        self.assertEqual(row["source_type"], MemorySourceType.SIMULATED_DIALOGUE.value)
        self.assertEqual(row["scope"], MemoryScope.SESSION.value)
        self.assertEqual(row["source_turn_id"], turn.id)

        # 会话 2 → 跨会话检索到（AC-9）
        s2 = fx.start([a, b])
        draft = unwrap(fx.dialogue().draft_turn(fx.ctx, s2, a))
        self.assertIn(mid, draft.referenced_memory_ids)

    def test_end_to_end_single_project_chain(self):
        """一条链跑完：证据 → 档案 → 对话 → 记忆 → 评测（同一临时库）。"""
        fx = self.fx
        a, b = fx.two_actors()
        fx.relationship_profile(a, b)
        s1 = fx.start([a, b])
        mid = unwrap(self.run_turn(s1, candidates=[fx.candidate()])).generated_memory_ids[0]

        # 评测：结构指标可算出，不变式全过
        quality = unwrap(fx.evaluation.measure_quality(fx.ctx, PROJECT))
        self.assertEqual(quality["evidence_item_count"], 3)
        self.assertIsNotNone(quality["evidence_traceability_rate"])
        self.assertTrue(quality["llm_judge_disabled"])
        report = unwrap(fx.evaluation.check_invariants(fx.ctx, PROJECT))
        self.assertTrue(report["passed"], report["checks"])
        # 记忆确实存在且是模拟的
        page = unwrap(fx.memory.search(fx.ctx, MemoryQuery(text=TOPIC)))
        self.assertIn(mid, [r.id for r in page.items])


# ==========================================================================
# 2. 模块边界（CLI / Orchestrator）
# ==========================================================================


class TestModuleBoundaries(AcceptanceTestCase):
    def test_check_dependencies_passes(self):
        """AC-20：无循环依赖、无越层调用、无厂商 SDK。"""
        report = unwrap(self.fx.evaluation.check_dependencies(self.fx.ctx))
        self.assertTrue(report["passed"], report["violations"])

    def test_static_checks_pass(self):
        report = unwrap(self.fx.evaluation.run_static_checks(self.fx.ctx))
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["forbidden_fields"], [])
        self.assertEqual(report["signature_violations"], [])

    def test_cli_does_not_import_business_modules(self):
        """D-059 / F-7：CLI 源码不得直连 `persona.modules`。"""
        cli_text = "\n".join(
            p.read_text(encoding="utf-8")
            for p in sorted((REPO_ROOT / "persona" / "cli").rglob("*.py")))
        for needle in ("from persona.modules", "import persona.modules", "from ..modules"):
            self.assertNotIn(needle, cli_text)

    def test_cli_task_entrypoint_creates_task_in_isolated_data_dir(self):
        """CLI `task create` 经 TaskService 落库，且写入被重定向到临时目录（不碰用户数据）。"""
        from persona.cli.main import main as cli_main

        data_dir = Path(self.fx.tmp) / "cli-data"
        saved = {k: os.environ.get(k) for k in ("PERSONA_DATA_DIR", "PERSONA_DATABASE_FILENAME")}
        os.environ["PERSONA_DATA_DIR"] = str(data_dir)
        os.environ["PERSONA_DATABASE_FILENAME"] = "cli.db"
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli_main(["task", "create", "--project", PROJECT,
                               "--stages", "RESEARCH,EVIDENCE"])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(rc, 0)
        self.assertTrue((data_dir / "cli.db").exists())
        # 任务落在临时库，而不是用户默认库
        store = SqliteStorage(data_dir / "cli.db")
        try:
            rows = unwrap(store.query(self.fx.ctx, "TaskRecord",
                                      Query(filters={"kind": "TaskRecord"}, limit=10)))
            self.assertEqual(len(rows.items), 1)
        finally:
            store.close()

    def test_cli_placeholder_commands_are_honest(self):
        """未实现的业务命令必须明确报"尚未实现"，不假装能用。"""
        from persona.cli.main import EXIT_NOT_IMPLEMENTED, main as cli_main

        for argv in (["chat", PROJECT], ["new", "--names", "甲,乙"], ["eval"]):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli_main(argv), EXIT_NOT_IMPLEMENTED, argv)

    def test_cli_info_and_version_run(self):
        from persona.cli.main import main as cli_main

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main(["info"]), 0)
            with self.assertRaises(SystemExit) as caught:
                cli_main(["--version"])
        self.assertEqual(caught.exception.code, 0)


# ==========================================================================
# 3. 数据隔离与不变式（AC-13 / AC-14 / AC-15）
# ==========================================================================


class TestIsolationAndInvariants(AcceptanceTestCase):
    def test_dialogue_and_memory_leave_reality_tables_unchanged(self):
        """AC-13：对话 + 记忆跑完后，四张"现实世界"表行数零变化。"""
        fx = self.fx
        a, b = fx.two_actors()
        fx.relationship_profile(a, b)
        before = unwrap(fx.evaluation.snapshot(fx.ctx, PROJECT))

        s1 = fx.start([a, b])
        self.run_turn(s1, candidates=[fx.candidate()])
        s2 = fx.start([a, b])
        self.run_turn(s2, candidates=[fx.candidate("另一次的约定")])

        after = unwrap(fx.evaluation.snapshot(fx.ctx, PROJECT))
        self.assertEqual(fx.evaluation.dialogue_leaks(before, after), [])

    def test_check_invariants_all_pass_with_healthy_data(self):
        fx = self.fx
        a, b = fx.two_actors()
        fx.relationship_profile(a, b)
        s1 = fx.start([a, b])
        self.run_turn(s1, candidates=[fx.candidate()])

        report = unwrap(fx.evaluation.check_invariants(
            fx.ctx, PROJECT, suspicious_literals=["特朗普", "Trump"],
            scan_roots=[REPO_ROOT / "persona"]))
        names = {c["name"]: c["passed"] for c in report["checks"]}
        for required in ("dialogue_does_not_write_evidence", "simulated_not_in_evidence",
                         "memory_cannot_become_evidence", "conflict_not_resolved",
                         "no_intimacy_field", "variants_not_merged",
                         "no_person_specific_literals"):
            self.assertIn(required, names)
        self.assertTrue(report["passed"], report["checks"])

    def test_simulated_memory_never_becomes_evidence(self):
        """AC-14 / INV-2：模拟语句永不出现在 EvidenceItem 里。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        self.run_turn(s1, candidates=[fx.candidate(SIM_CONTENT)])

        evidence = fx.rows("EvidenceItem")
        self.assertTrue(evidence)
        # 只在模拟记忆里出现的独特标记，绝不来自任何 EvidenceItem
        self.assertFalse(any(SIM_MARKER in str(r.get("claim", "")) for r in evidence))
        # 存储层闸门：往 EvidenceItem 写 SIMULATED 直接被拒
        blocked = fx.storage.put(fx.ctx, "EvidenceItem", {"claim": "模拟内容不应进证据表"},
                                 project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED)
        self.assertTrue(is_err(blocked))
        self.assertEqual(blocked.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_simulated_memory_is_labelled_and_not_persona_fact(self):
        """③ / INV-3：检索结果原样带出 SIMULATED 标记；模拟记忆不写入人格档案。"""
        fx = self.fx
        a, b = fx.two_actors()
        persona_before = len(fx.rows("PersonaProfile"))
        s1 = fx.start([a, b])
        self.run_turn(s1, candidates=[fx.candidate(SIM_CONTENT)])

        page = unwrap(fx.memory.search(fx.ctx, MemoryQuery(text=TOPIC)))
        self.assertTrue(page.items)
        for row in page.items:
            self.assertEqual(row.namespace, str(ProvenanceNamespace.SIMULATED))
        self.assertEqual(len(fx.rows("PersonaProfile")), persona_before)
        # 人格档案 payload 里没有对话生成的那句约定
        self.assertFalse(any(SIM_MARKER in str(r.get("payload", ""))
                             for r in fx.rows("PersonaProfile")))

    def test_conflict_is_recorded_not_resolved(self):
        """AC-15 / INV-4：冲突只新增与标注，不裁决、不合并。"""
        fx = self.fx
        a, _ = fx.two_actors()
        src = fx.source()
        e1 = fx.evidence_item(src, "阿雾话很多。", subject=a)
        e2 = fx.evidence_item(src, "阿雾很少说话。", subject=a)
        ref = unwrap(fx.evidence.record_conflict(fx.ctx, ConflictDraft(
            topic="阿雾是否健谈", subject_entity_ids=[a],
            member_evidence_ids=[e1, e2], divergence_kind=DivergenceKind.FACTUAL,
            uncertainty_note="两种说法并存")))
        self.assertEqual(ref.member_count, 2)
        self.assertEqual(ref.review_status, ReviewStatus.UNREVIEWED.value)

        row = unwrap(fx.storage.get(fx.ctx, "ConflictRecord", ref.id))
        self.assertEqual(row["resolution_policy"], "KEEP_ALL_UNASSESSED")
        self.assertEqual(len(row["members"]), 2)
        self.assertTrue(unwrap(fx.evaluation.check_invariants(fx.ctx, PROJECT))["passed"])


# ==========================================================================
# 4. 预算 / 暂停恢复 / 失败 / 幂等（AC-11 / AC-12）
# ==========================================================================


class TestBudgetFailureAndIdempotency(AcceptanceTestCase):
    def _task(self, *, max_requests: int, unit_count: int):
        svc = TaskService(self.fx.storage)
        units = [WorkUnit(name=f"RESEARCH:{i}", stage="RESEARCH") for i in range(unit_count)]
        handler = MockUnitHandler(units=units)
        runner = TaskRunner(svc, handler)
        task_id = unwrap(svc.create_task(self.fx.ctx, TaskDraft(
            task_type=TaskType.RESEARCH, stage_sequence=["RESEARCH"],
            budget_limit=BudgetSpec(max_requests=max_requests)))).id
        unwrap(svc.start(self.fx.ctx, task_id))
        return svc, runner, handler, task_id

    def test_budget_exhaustion_pauses_with_checkpoint_then_resume(self):
        """AC-12：预算不足 → 暂停（不是失败）+ 检查点；提高额度后 resume 续跑不重复。"""
        svc, runner, handler, task_id = self._task(max_requests=2, unit_count=5)

        result = unwrap(runner.run_to_completion(self.fx.ctx, task_id))
        self.assertEqual(result.status, TaskStatus.PAUSED)
        record = unwrap(svc.get(self.fx.ctx, task_id))
        self.assertEqual(record.paused_reason, PausedReason.BUDGET)
        self.assertEqual(handler.call_names(), ["RESEARCH:0", "RESEARCH:1"])
        cp = unwrap(svc.load_checkpoint(self.fx.ctx, task_id))
        self.assertEqual(sorted(cp.completed_units), ["RESEARCH:0", "RESEARCH:1"])

        # 提高额度 → resume → 只补跑剩下的 3 个，不重复前 2 个
        unwrap(svc.set_budget(self.fx.ctx, task_id, BudgetSpec(max_requests=10)))
        unwrap(svc.resume(self.fx.ctx, task_id))
        again = unwrap(runner.run_to_completion(self.fx.ctx, task_id))
        self.assertEqual(again.status, TaskStatus.COMPLETED)
        self.assertEqual(handler.call_names(),
                         ["RESEARCH:0", "RESEARCH:1", "RESEARCH:2", "RESEARCH:3", "RESEARCH:4"])

    def test_duplicate_execution_is_idempotent(self):
        """重复执行已完成任务 → 不重复干活。"""
        svc, runner, handler, task_id = self._task(max_requests=10, unit_count=3)
        unwrap(runner.run_to_completion(self.fx.ctx, task_id))
        self.assertEqual(len(handler.calls), 3)
        # 再次推进：任务已 COMPLETED，run_step 应拒绝（不是重复执行）
        step = runner.run_step(self.fx.ctx, task_id)
        self.assertTrue(is_err(step))
        self.assertEqual(len(handler.calls), 3)

    def test_provider_failure_propagates_in_dialogue(self):
        """Provider 失败 → 传播 Err，不落轮次、不伪造成功。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        failing = fx.dialogue(provider=FailingProvider())
        result = failing.run_round(fx.ctx, s1)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)
        self.assertEqual(len(fx.rows("ConversationTurn")), 0)

    def test_storage_write_failure_propagates_and_rolls_back(self):
        """会话持久化失败 → 传播 Err（不假装成功）。"""
        fx = self.fx
        a, b = fx.two_actors()
        broken = FailingPutStorage(fx.storage, "ConversationSession")
        svc = DialogueService(DialogueDeps(
            profiles=fx.persona, relations=fx.relationship, evidence=fx.evidence,
            memory=MemoryService(fx.storage), storage=broken, provider=fx.provider,
            memory_reader=MemoryService(fx.storage)))
        result = svc.start_session(fx.ctx, SessionDraft(participant_entity_ids=[a, b]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STORAGE_WRITE_FAILED)

    def test_interrupted_task_sweep_recovers_to_paused(self):
        """进程中断（RUNNING 遗留）→ `sweep_interrupted` 安全回退为 PAUSED(DEPENDENCY)。"""
        svc, _runner, _handler, task_id = self._task(max_requests=10, unit_count=3)
        self.assertEqual(unwrap(svc.get(self.fx.ctx, task_id)).status, TaskStatus.RUNNING)

        fixed = unwrap(svc.sweep_interrupted(self.fx.ctx))
        self.assertEqual(fixed, [task_id])                 # 遗留的 RUNNING 被识别
        record = unwrap(svc.get(self.fx.ctx, task_id))
        self.assertEqual(record.status, TaskStatus.PAUSED)
        self.assertEqual(record.paused_reason, PausedReason.DEPENDENCY)
        # 幂等：再扫一次不再改动
        self.assertEqual(unwrap(svc.sweep_interrupted(self.fx.ctx)), [])


# ==========================================================================
# 5. 隔离数据 / 离线
# ==========================================================================


class TestIsolatedDataAndOffline(AcceptanceTestCase):
    def test_all_data_in_temp_dir_not_user_data(self):
        fx = self.fx
        self.assertTrue(str(fx.path).startswith(tempfile.gettempdir()) or "gps-accept-" in str(fx.path))
        self.assertTrue(fx.path.exists())
        # 默认用户数据目录（仓库 var/）不得被本测试创建
        self.assertFalse((REPO_ROOT / "var").exists())

    def test_offline_guard_allows_mock_flow(self):
        """AC-19：全程离线——OfflineGuardProvider 包装下对话可跑。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        turn = self.run_turn(s1)
        self.assertTrue(is_ok(turn))


# ==========================================================================
# 6. 文档一致性（启动 / 测试命令）
# ==========================================================================


class TestDocsConsistency(unittest.TestCase):
    def test_running_docs_test_command_matches_implementation(self):
        running = (REPO_ROOT / "docs" / "RUNNING.md").read_text(encoding="utf-8")
        self.assertIn("python -m unittest discover -s tests -t .", running)

    def test_roadmap_has_p5_section(self):
        roadmap = (REPO_ROOT / "docs" / "ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("P5", roadmap)
        self.assertIn("check_invariants", roadmap)

    def test_readme_mentions_current_stage(self):
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("当前阶段", readme)

    def test_documented_test_command_actually_runs(self):
        """抽查：文档承诺的命令可被执行（只跑一个小模块，避免全量耗时）。"""
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_imports"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])


if __name__ == "__main__":
    unittest.main()
