"""Dialogue 模块测试（P4 第一步：对话生成基础模块；用户称之为 P3.4）。

全部跑在**临时目录**的 SQLite 文件上，不碰任何正式项目库、不调外部服务、不触网。

覆盖（对应用户验收清单第 9 条 + 模块边界 / 隔离红线）：
  1. 会话创建与校验（参与者≥2、实体存在、档案属于同一批参与者、namespace=SIMULATED）
  2. 单轮 / 多轮端到端（Mock Provider 驱动，落盘轮次）
  3. 多角色动态发言顺序（least_recently_spoken，非固定轮流；每轮记录选择理由 AC-8）
  4. 上下文引用人格/关系档案（只读，不伪造档案外事实；冲突主题打安全标记）
  5. 参与者选择（每轮 speaker + selection_reason 必填可解释）
  6. 异常处理（Provider 失败 / 空回复 / 无效参与者 / 会话持久化失败 → 不伪造成功）
  7. 持久化（轮次落盘、turn_count 自增、事务一致、幂等 commit、重启可读回）
  8. 模拟内容隔离（对话前后 EvidenceItem/PersonaProfile/RelationshipProfile/SourceEvent
     零变化；会话/轮次 namespace 恒 SIMULATED）
  9. 模块边界（Dialogue 只拿只读端口；不写证据/档案；本阶段不写长期记忆）

对应文档：INTERFACES §8、DATA_MODEL §4.9、ERROR_HANDLING §2.2（H-1 三码待确认）、
DECISIONS §2.20。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    MemoryScope,
    ProvenanceNamespace,
    SessionStatus,
    Stability,
)
from persona.core.errors import ErrorCode
from persona.core.ids import new_token
from persona.core.result import Err, Ok, UsageDelta, is_err, is_ok, unwrap
from persona.core.types import Page, Query
from persona.modules.dialogue import (
    DialogueDeps,
    DialogueService,
    POLICY_LEAST_RECENTLY_SPOKEN,
    SessionDraft,
    SelectionReason,
    TurnDraftResult,
)
from persona.modules.evidence import (
    ConflictDraft,
    EvidenceDraft,
    EvidenceService,
    SourceDraft,
)
from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    ProfileItem,
    ProfileSection,
)
from persona.modules.relationship import (
    Cardinality,
    PerspectiveBlock,
    RelationshipDraft,
    RelationshipService,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.providers.mock import FailingProvider, OfflineGuardProvider
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_dialogue"


class DialogueFixture:
    """临时数据库 + Persona/Relationship/Evidence 服务 + 调用上下文。用完即关。"""

    def __init__(self, tmp: str, provider=None) -> None:
        self.storage = SqliteStorage(Path(tmp) / "dialogue.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.evidence = EvidenceService(self.storage)
        self.provider = provider
        self._heroes: dict[str, str] = {}

    # ---------------------------------------------------------- 基础构件

    def entity(self, name: str, *, variant_group_id: str | None = None) -> str:
        payload: dict = {
            "display_name": name,
            "entity_type": "FICTIONAL_CHARACTER",
            "merge_policy": "NEVER_AUTO_MERGE",
            "status": "OK",
            "fictional_character_meta": {
                "works": [{"work_id": new_token("wrk_"),
                           "title": "《雾港与星灯》", "medium": "ANIMATION"}],
                "adaptation_version": "2020 动画改编版",
                "adaptation_lineage": ["原作轻小说版", "2020 动画改编版"],
                "official_canon_level": "CANON",
            },
        }
        if variant_group_id:
            payload["variant_group_id"] = variant_group_id
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity", payload, project_id=PROJECT,
            namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def source(self, title: str = "《雾港与星灯》访谈录") -> str:
        draft = SourceDraft(title=title, canonical_key=f"key_{new_token()}")
        return unwrap(self.evidence.register_source(self.ctx, draft)).id

    def make_evidence(self, source_id: str, claim: str, *, subject: str) -> str:
        draft = EvidenceDraft(
            claim=claim, tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT, subject_entity_ids=[subject],
            extraction_confidence=0.9, source_id=source_id)
        return unwrap(self.evidence.add_evidence(self.ctx, draft)).id

    def persona_profile(self, entity_id: str, background: str = "雾港出身的青年。") -> str:
        ref = self.make_evidence(self.source(), f"{entity_id} 的背景：{background}",
                                 subject=entity_id)
        draft = PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8,
                                      evidence_refs=[ref]),
            applicable_variant_ids=[entity_id],
            open_questions=["习惯维度待补"])
        return unwrap(self.persona.create_profile(self.ctx, draft)).id

    def relationship_profile(self, a: str, b: str, *, with_conflict: bool = False) -> str:
        src = self.source()
        ref_a = self.make_evidence(src, "A 对 B 的态度。", subject=a)
        ref_b = self.make_evidence(src, "B 对 A 的态度。", subject=b)
        perspectives = {
            a: PerspectiveBlock(
                entity_id=a,
                feeling_toward=[ProfileItem(item_id="pa1", text="A 信赖 B",
                                           confidence=0.7, evidence_refs=[ref_a],
                                           stability=Stability.REPEATED)]),
            b: PerspectiveBlock(
                entity_id=b,
                feeling_toward=[ProfileItem(item_id="pb1", text="B 照顾 A",
                                           confidence=0.7, evidence_refs=[ref_b],
                                           stability=Stability.REPEATED)]),
        }
        conflict_ids: list[str] = []
        if with_conflict:
            c = unwrap(self.evidence.record_conflict(self.ctx, ConflictDraft(
                topic="A 与 B 的某分歧", subject_entity_ids=[a, b],
                member_evidence_ids=[ref_a, ref_b],
                divergence_kind="FACTUAL")))
            conflict_ids = [c.id]
        draft = RelationshipDraft(
            participant_entity_ids=[a, b], cardinality=Cardinality.PAIR,
            applicable_variant_ids=[a, b],
            perspectives=perspectives, conflict_ids=conflict_ids,
            open_questions=["群体互动不适用两人关系"])
        return unwrap(self.relationship.create_relationship(self.ctx, draft)).id

    # ---------------------------------------------------------- 角色组合

    def two_actors(self) -> tuple[str, str]:
        a = self.entity("阿雾")
        b = self.entity("阿灯")
        self.persona_profile(a, "阿雾：沉默寡言的灯塔看守。")
        self.persona_profile(b, "阿灯：活泼的渔家少女。")
        return a, b

    def three_actors(self) -> tuple[str, str, str]:
        a, b = self.two_actors()
        c = self.entity("阿星")
        self.persona_profile(c, "阿星：远道而来的旅人。")
        return a, b, c


def _fixture(provider=None) -> DialogueFixture:
    return DialogueFixture(tempfile.mkdtemp(prefix="gps-dialogue-"), provider=provider)


# ==========================================================================
# Mock Provider 家族（仅离线、确定性；不触网）
# ==========================================================================


class CapturingProvider:
    """实现 Provider 协议：记录最后一次 chat 请求的 messages，返回确定性非空文本。

    不依赖 fixture_key（真实 Provider 的惯例），用于验证 Dialogue 把档案上下文
    真的注入到了 prompt 里。
    """

    def __init__(self) -> None:
        self.last_messages: list = []
        self.chat_calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="capturing", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Ok(ChatResponse(
            text="（模拟）我听见了，接着刚才的话说下去。",
            finish_reason="stop", model_id="capturing",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        from persona.core.types import UsageReport
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class EmptyProvider(CapturingProvider):
    """返回空文本 —— 用于验证『空回复不伪造成功』。"""

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Ok(ChatResponse(text="   ", usage=UsageDelta(requests=1)))


class FailingPutStorage:
    """包一层真实存储：对指定 kind 的 `put` 返回写入失败，其余委派真实存储。

    用于验证『会话/轮次持久化失败 → 不伪造成功』（用户目标 7）。
    """

    def __init__(self, real: SqliteStorage, fail_kind: str) -> None:
        self._real = real
        self._fail_kind = fail_kind

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def put(self, ctx, kind, payload, **kwargs):
        if kind == self._fail_kind:
            return Err(__import__("persona.core.errors", fromlist=["new_error"])
                      .new_error(ErrorCode.STORAGE_WRITE_FAILED,
                                 f"注入的写入失败（测试用，kind={kind}）",
                                 trace_ref=ctx.trace_ref))
        return self._real.put(ctx, kind, payload, **kwargs)


# ==========================================================================
# 基类
# ==========================================================================


class FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = CapturingProvider()
        self.fx = _fixture(provider=self.provider)
        self.service = self._build_service(self.fx)

    def _build_service(self, fx: DialogueFixture) -> DialogueService:
        deps = DialogueDeps(
            profiles=fx.persona, relations=fx.relationship, evidence=fx.evidence,
            memory=None, storage=fx.storage, provider=fx.provider)
        return DialogueService(deps)

    def tearDown(self) -> None:
        self.fx.storage.close()
        shutil.rmtree(Path(self.fx.storage.path).parent, ignore_errors=True)


# ==========================================================================
# 1. 会话创建与校验
# ==========================================================================


class TestSessionCreation(FixtureTestCase):
    def test_start_session_basic(self):
        fx = self.fx
        a, b = fx.two_actors()
        view = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], topic="夜谈")))
        self.assertTrue(view.id.startswith("css_"))
        self.assertEqual(set(view.participant_entity_ids), {a, b})
        self.assertEqual(view.status, str(SessionStatus.ACTIVE))
        self.assertEqual(view.turn_count, 0)
        # namespace 恒 SIMULATED（存储层网关强制）
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", view.id))
        self.assertEqual(row["namespace"], str(ProvenanceNamespace.SIMULATED))

    def test_too_few_participants(self):
        fx = self.fx
        a = fx.entity("独行者")
        result = self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS)

    def test_unknown_entity_rejected(self):
        result = self.service.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=["pe_" + "0" * 32, "pe_" + "1" * 32]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_duplicate_participant_rejected(self):
        fx = self.fx
        a, b = fx.two_actors()
        result = self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, a]))
        self.assertTrue(is_err(result))

    def test_profile_mismatch_rejected(self):
        """SS-V3：引用的档案必须属于同一批参与者。"""
        fx = self.fx
        a, b = fx.two_actors()
        stranger = fx.entity("局外人")
        fx.persona_profile(stranger, "局外人的背景。")
        result = self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], persona_profile_ids=[stranger]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_PROFILE_MISMATCH)

    def test_memory_scope_validated(self):
        fx = self.fx
        a, b = fx.two_actors()
        result = self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], memory_scope="GALAXY"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)


# ==========================================================================
# 2. 单轮 / 多轮端到端
# ==========================================================================


class TestEndToEndRounds(FixtureTestCase):
    def test_single_round(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        turn = unwrap(self.service.run_round(fx.ctx, sid))
        self.assertTrue(turn.id.startswith("ctn_"))
        self.assertEqual(turn.index, 1)
        self.assertIn(turn.speaker_entity_id, {a, b})
        self.assertTrue(turn.content)  # 非空、非伪造
        # 会话 turn_count 自增
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(row["turn_count"], 1)

    def test_multi_round_persists_each(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        result = unwrap(self.service.run(fx.ctx, sid, max_rounds=4))
        self.assertEqual(len(result.turns), 4)
        self.assertEqual(result.stopped_reason, "reached_max_rounds")
        for i, t in enumerate(result.turns, start=1):
            self.assertEqual(t.index, i)
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(row["turn_count"], 4)

    def test_restart_reads_back(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        unwrap(self.service.run_round(fx.ctx, sid))
        db_path = fx.storage.path
        fx.storage.close()
        reboot = SqliteStorage(db_path)
        unwrap(reboot.init_schema(new_call_context(project_id=PROJECT, actor="tests")))
        svc = self._build_service(_RebootFixture(reboot))
        view = unwrap(svc._get_session(
            new_call_context(project_id=PROJECT, actor="tests"), sid))
        self.assertEqual(view["status"], str(SessionStatus.ACTIVE))
        # 重启后仍能读到已落盘的轮次
        turns = svc._load_session_turns(  # type: ignore
            new_call_context(project_id=PROJECT, actor="tests"), sid)
        self.assertEqual(len(turns), 1)


class _RebootFixture:
    """把已重启的 storage 包成 DialogueFixture 的形状（仅取 storage/服务）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self.storage = storage
        self.persona = PersonaService(storage)
        self.relationship = RelationshipService(storage)
        self.evidence = EvidenceService(storage)
        self.provider = CapturingProvider()
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")


# ==========================================================================
# 3. 多角色动态发言顺序（least_recently_spoken，非固定轮流）
# ==========================================================================


class TestSpeakerSelection(FixtureTestCase):
    def test_three_actors_rotate_by_least_recently_spoken(self):
        fx = self.fx
        a, b, c = fx.three_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b, c]))).id
        # 初始都未发言 → 按参与者次序 A、B、C
        speakers = []
        for _ in range(3):
            choice = unwrap(self.service.select_speaker(fx.ctx, sid))
            self.assertEqual(choice.selection_reason.rule, "least_recently_spoken")
            speakers.append(choice.speaker_entity_id)
            # 提交该轮以更新发言历史
            draft = unwrap(self.service.draft_turn(
                fx.ctx, sid, choice.speaker_entity_id,
                selection_reason=choice.selection_reason))
            unwrap(self.service.commit_turn(fx.ctx, sid, draft))
        self.assertEqual(speakers, [a, b, c])

    def test_dynamic_not_fixed_order(self):
        """构造不均匀历史：A 连说两轮后，下一轮应是 B/C 中更久未说的。"""
        fx = self.fx
        a, b, c = fx.three_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b, c]))).id

        def _speak(speaker: str) -> None:
            choice = unwrap(self.service.select_speaker(fx.ctx, sid))
            draft = unwrap(self.service.draft_turn(
                fx.ctx, sid, speaker, selection_reason=choice.selection_reason))
            unwrap(self.service.commit_turn(fx.ctx, sid, draft))

        _speak(a)
        _speak(a)  # A 连说两次
        # 此刻 B、C 都未发言（index=-1），A 最近（index=2）→ 选 B（次序在前）
        choice = unwrap(self.service.select_speaker(fx.ctx, sid))
        self.assertEqual(choice.speaker_entity_id, b)

    def test_session_ended_blocks_selection(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        unwrap(self.service.end_session(fx.ctx, sid))
        result = self.service.select_speaker(fx.ctx, sid)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STATE_CONFLICT_SESSION_ENDED)


# ==========================================================================
# 4. 上下文引用人格/关系档案（只读，不伪造事实；冲突安全标记）
# ==========================================================================


class TestContextReference(FixtureTestCase):
    def test_persona_context_injected_and_referenced(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b],
            persona_profile_ids=[a, b]))).id
        choice = unwrap(self.service.select_speaker(fx.ctx, sid))
        draft = unwrap(self.service.draft_turn(
            fx.ctx, sid, choice.speaker_entity_id,
            selection_reason=choice.selection_reason))
        # 引用了人格档案（本阶段按 entity_id 记录）
        self.assertEqual(set(draft.referenced_persona_item_ids), {a, b})
        # prompt 确实注入了人格背景文本（验证"真的引用了"，非空谈）
        system_text = self.provider.last_messages[0].content
        self.assertIn("阿雾：沉默寡言的灯塔看守。", system_text)
        self.assertIn("阿灯：活泼的渔家少女。", system_text)
        self.assertIn("模拟声明", system_text)  # 隔离声明

    def test_relationship_context_and_conflict_flag(self):
        fx = self.fx
        a, b = fx.two_actors()
        rel_id = fx.relationship_profile(a, b, with_conflict=True)
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b],
            persona_profile_ids=[a, b],
            relationship_profile_ids=[rel_id]))).id
        choice = unwrap(self.service.select_speaker(fx.ctx, sid))
        draft = unwrap(self.service.draft_turn(
            fx.ctx, sid, choice.speaker_entity_id,
            selection_reason=choice.selection_reason))
        self.assertIn(rel_id, draft.referenced_relationship_item_ids)
        self.assertIn("CONFLICT_ASSERTION_GUARD", draft.safety_flags)
        # prompt 显式要求不得给"唯一确定事实"式表述
        self.assertIn("CONFLICT_ASSERTION_GUARD", self.provider.last_messages[0].content)


# ==========================================================================
# 5 & 6. 参与者选择记录 + 异常处理（不伪造成功）
# ==========================================================================


class TestSelectionRecorded(FixtureTestCase):
    def test_every_turn_has_explanation(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        result = unwrap(self.service.run(fx.ctx, sid, max_rounds=3))
        for t in result.turns:
            self.assertTrue(t.selection_reason.explanation)
            self.assertEqual(t.selection_reason.rule, "least_recently_spoken")


class TestFailureHandling(FixtureTestCase):
    def test_provider_failure_propagates_not_faked(self):
        fx = self.fx
        failing = FailingProvider(default=ErrorCode.EXTERNAL_UNAVAILABLE)
        svc = self._build_service(_SwapProvider(fx, failing))
        a, b = fx.two_actors()
        sid = unwrap(svc.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        result = svc.run_round(fx.ctx, sid)
        self.assertTrue(is_err(result))  # 不伪造成功
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(row["turn_count"], 0)  # 没有落盘任何轮次

    def test_empty_reply_not_success(self):
        fx = self.fx
        empty = EmptyProvider()
        svc = self._build_service(_SwapProvider(fx, empty))
        a, b = fx.two_actors()
        sid = unwrap(svc.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        result = svc.run_round(fx.ctx, sid)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(row["turn_count"], 0)

    def test_invalid_participant_speaker(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        stranger = fx.entity("局外人")
        result = self.service.draft_turn(fx.ctx, sid, stranger)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_SPEAKER_NOT_PARTICIPANT)

    def test_session_persist_failure_propagates(self):
        fx = self.fx
        broken = FailingPutStorage(fx.storage, "ConversationSession")
        svc = self._build_service(_SwapStorage(fx, broken))
        a, b = fx.two_actors()
        result = svc.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STORAGE_WRITE_FAILED)

    def test_turn_persist_failure_propagates(self):
        fx = self.fx
        broken = FailingPutStorage(fx.storage, "ConversationTurn")
        svc = self._build_service(_SwapStorage(fx, broken))
        a, b = fx.two_actors()
        sid = unwrap(svc.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        choice = unwrap(svc.select_speaker(fx.ctx, sid))
        draft = unwrap(svc.draft_turn(
            fx.ctx, sid, choice.speaker_entity_id,
            selection_reason=choice.selection_reason))
        result = svc.commit_turn(fx.ctx, sid, draft)
        self.assertTrue(is_err(result))  # 不伪造成功
        self.assertEqual(result.error.code, ErrorCode.STORAGE_WRITE_FAILED)


class _SwapProvider:
    """替换 fixture 的 provider（用于失败/空回复路径）。"""

    def __init__(self, fx: DialogueFixture, provider) -> None:
        self._fx = fx
        self._fx.provider = provider

    def __getattr__(self, name: str):
        return getattr(self._fx, name)


class _SwapStorage(_SwapProvider):
    """替换 fixture 的 storage（用于持久化失败路径）。"""

    def __init__(self, fx: DialogueFixture, storage) -> None:
        super().__init__(fx, fx.provider)
        self._fx.storage = storage


# ==========================================================================
# 7. 持久化细节
# ==========================================================================


class TestPersistenceDetails(FixtureTestCase):
    def test_idempotent_commit_same_index(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        choice = unwrap(self.service.select_speaker(fx.ctx, sid))
        d1 = unwrap(self.service.draft_turn(
            fx.ctx, sid, choice.speaker_entity_id,
            selection_reason=choice.selection_reason))
        t1 = unwrap(self.service.commit_turn(fx.ctx, sid, d1))
        # 同一 (session_id, index) 再 commit → 返回同一 turn
        t2 = unwrap(self.service.commit_turn(fx.ctx, sid, d1))
        self.assertEqual(t1.id, t2.id)
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(row["turn_count"], 1)  # 没有产生第二轮

    def test_end_session_blocks_new_turn(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        unwrap(self.service.end_session(fx.ctx, sid, "测试结束"))
        row = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(row["status"], str(SessionStatus.ENDED))
        # 会话已结束：select_speaker 会拒绝（STATE_CONFLICT_SESSION_ENDED），
        # 故直接构造一条待提交轮次，验证 commit_turn 也拦截新轮次。
        draft = TurnDraftResult(
            session_id=sid, index=1, speaker_entity_id=a,
            content="x", selection_reason=SelectionReason(
                rule=POLICY_LEAST_RECENTLY_SPOKEN,
                explanation="测试用：会话已结束后不应接受新轮次"),
            referenced_memory_ids=[], referenced_persona_item_ids=[],
            referenced_relationship_item_ids=[], safety_flags=[], usage=None)
        result = self.service.commit_turn(fx.ctx, sid, draft)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STATE_CONFLICT_SESSION_ENDED)


# ==========================================================================
# 8. 模拟内容隔离
# ==========================================================================


class TestSimulatedIsolation(FixtureTestCase):
    def test_real_tables_unchanged_after_dialogue(self):
        fx = self.fx
        a, b = fx.two_actors()
        rel_id = fx.relationship_profile(a, b)

        def _count(kind: str) -> int:
            return unwrap(fx.storage.query(
                fx.ctx, kind, Query(limit=200))).total

        before = {
            "EvidenceItem": _count("EvidenceItem"),
            "PersonaProfile": _count("PersonaProfile"),
            "RelationshipProfile": _count("RelationshipProfile"),
            "SourceEvent": _count("SourceEvent"),
        }
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b],
            persona_profile_ids=[a, b],
            relationship_profile_ids=[rel_id]))).id
        unwrap(self.service.run(fx.ctx, sid, max_rounds=3))
        after = {
            "EvidenceItem": _count("EvidenceItem"),
            "PersonaProfile": _count("PersonaProfile"),
            "RelationshipProfile": _count("RelationshipProfile"),
            "SourceEvent": _count("SourceEvent"),
        }
        self.assertEqual(before, after)  # 对话零污染现实资料

    def test_session_and_turn_namespace_simulated(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        unwrap(self.service.run_round(fx.ctx, sid))
        srow = unwrap(fx.storage.get(fx.ctx, "ConversationSession", sid))
        self.assertEqual(srow["namespace"], str(ProvenanceNamespace.SIMULATED))
        turns = self.service._load_session_turns(fx.ctx, sid)  # type: ignore
        self.assertTrue(turns)
        for t in turns:
            self.assertEqual(t["namespace"], str(ProvenanceNamespace.SIMULATED))

    def test_offline_guard_provider_runs_offline(self):
        """OfflineGuardProvider 包一层确定性 Provider：对话全程不触网。"""
        fx = self.fx
        guard = OfflineGuardProvider(CapturingProvider())
        svc = self._build_service(_SwapProvider(fx, guard))
        a, b = fx.two_actors()
        sid = unwrap(svc.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        turn = unwrap(svc.run_round(fx.ctx, sid))
        self.assertTrue(turn.content)


# ==========================================================================
# 9. 模块边界 / 用户消息
# ==========================================================================


class TestModuleBoundaryAndUserMessage(FixtureTestCase):
    def test_dialogue_keeps_only_read_ports(self):
        """Dialogue 注入的 Persona/Relationship/Evidence 依赖都是只读协议。

        INV-1：Dialogue 只能拿到 `PersonaReader` / `RelationshipReader` /
        `EvidenceReader`，这些 Protocol 里**根本没有写方法**（技术层面堵死
        模拟污染现实资料）。这里直接检查协议声明，而非被测试的具体服务。
        """
        from persona.modules.evidence import EvidenceReader
        from persona.modules.persona import PersonaReader
        from persona.modules.relationship import RelationshipReader

        write_verbs = {
            "add_evidence", "register_source", "record_conflict",
            "create_profile", "create_relationship", "record_inference",
            "add_source_event",
        }
        for port in (PersonaReader, RelationshipReader, EvidenceReader):
            attrs = getattr(port, "__protocol_attrs__", set())
            overlap = write_verbs & set(attrs)
            self.assertFalse(overlap, f"{port.__name__} 不应含写方法：{overlap}")

    def test_no_long_term_memory_written(self):
        """用户目标 10：本阶段 commit 不写长期记忆（generated_memory_ids=[]）。"""
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        turn = unwrap(self.service.run_round(fx.ctx, sid))
        self.assertEqual(turn.generated_memory_ids, [])
        # MemoryItem 表应为空（未自动写入）
        self.assertEqual(unwrap(fx.storage.query(
            fx.ctx, "MemoryItem", Query(limit=200))).total, 0)

    def test_user_message_then_character_reply(self):
        fx = self.fx
        a, b = fx.two_actors()
        sid = unwrap(self.service.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b]))).id
        user_turn = unwrap(self.service.add_user_message(
            fx.ctx, sid, "你们今晚打算去哪儿？"))
        self.assertEqual(user_turn.speaker_entity_id, "__user__")
        # 用户消息落盘后，下一轮由角色回复（发言人仍是参与者之一）
        reply = unwrap(self.service.run_round(fx.ctx, sid))
        self.assertIn(reply.speaker_entity_id, {a, b})
        self.assertEqual(reply.index, 2)
        # transcript 含用户消息
        system_text = self.provider.last_messages[0].content
        self.assertIn("你们今晚打算去哪儿？", self.provider.last_messages[1].content
                      if len(self.provider.last_messages) > 1 else system_text)


if __name__ == "__main__":
    unittest.main()
