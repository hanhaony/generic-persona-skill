"""P3.6 对话 × 长期记忆集成测试（stdlib unittest，零第三方依赖，沿用 D-068）。

全部跑在**临时目录**的 SQLite 文件上，不碰正式项目库、不调外部服务、不触网。

覆盖（对应用户验收清单第 9 条）：
  1. 两个**连续会话**：会话 1 写入的模拟记忆，在会话 2 能被检索并注入上下文（AC-9 跨会话）
  2. 无关记忆不会无条件进入上下文（话题不符 / 对象不符 / 无 topic 时不跨会话）
  3. 模拟内容隔离：检索硬限 `namespace=SIMULATED`，真实资料永不进入模拟上下文；
     写入恒 `namespace=SIMULATED` + `source_type=SIMULATED_DIALOGUE`；
     对话前后 `EvidenceItem`/`PersonaProfile`/`RelationshipProfile`/`SourceEvent` 零变化
  4. 写入触发规则：默认 `none` 绝不写；未显式授权 → 拒绝；显式政策 + 候选才写；
     候选 `scope!=SESSION` → 拒绝（不得升级为实体级/人格级稳定记忆）
  5. 失败处理：记忆检索失败、记忆写入失败、Provider 失败、会话持久化失败
     —— 一律传播 Err，不伪造成功；写入失败时轮次**整体回滚**
  6. 记忆生命周期在集成中仍有效：更新（supersede）/ 冲突 / 过期 / 归档 / 删除后
     不再出现在检索结果中
  7. 幂等与重试：同一 `(session_id, index)` 重复提交不重复写记忆、不重复落轮次

对应文档：INTERFACES §8.1/§8.2/§9.3/§9.4、ROADMAP §6（P4）、DECISIONS §2.22。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    ProvenanceNamespace,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.ids import new_id, new_token
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.temporal import to_iso, utc_now
from persona.core.types import MAX_QUERY_LIMIT, Query
from persona.modules.dialogue import (
    POLICY_MEMORY_WRITE_EXPLICIT,
    POLICY_MEMORY_WRITE_NONE,
    DialogueDeps,
    DialogueService,
    SessionDraft,
)
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.memory import DeleteTarget, MemoryQuery, MemoryService
from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    ProfileSection,
)
from persona.modules.relationship import RelationshipService
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_dialogue_memory"

TOPIC = "灯塔的约定"
RELATED_CONTENT = f"{TOPIC}：阿雾答应阿灯，每天黄昏点亮灯塔。"
UNRELATED_TOPIC = "集市上的雨伞"
UNRELATED_CONTENT = f"{UNRELATED_TOPIC}：无关的往事，与本次会话主题不同。"


# ==========================================================================
# Mock / 故障注入
# ==========================================================================


class CapturingProvider:
    """实现 Provider 协议：记录最后一次 chat 的 messages，返回确定性非空文本。"""

    def __init__(self) -> None:
        self.last_messages: list = []
        self.chat_calls = 0

    def _joined(self) -> str:
        return "\n".join(str(getattr(m, "content", m)) for m in self.last_messages)

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="capturing", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Ok(ChatResponse(
            text="（模拟）我记得我们说过的那件事，接着说下去。",
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
    """对话生成失败（不触网、不伪造成功）。"""

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE, "注入的 Provider 失败"))


class FailingSearchReader:
    """记忆**检索**失败（只读端口故障）。"""

    def __init__(self, real) -> None:
        self._real = real

    def search(self, ctx, q):
        return Err(new_error(ErrorCode.STORAGE_READ_FAILED,
                             "注入的记忆检索失败（测试用）", trace_ref=ctx.trace_ref))


class FailingWriter:
    """记忆**写入**失败（写端口故障）。"""

    def write_simulated(self, ctx, draft):
        return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED,
                             "注入的记忆写入失败（测试用）", trace_ref=ctx.trace_ref))


class FailingPutStorage:
    """包一层真实存储：对指定 kind 的 `put` 返回写入失败，其余委派真实存储。"""

    def __init__(self, real: SqliteStorage, fail_kind: str) -> None:
        self._real = real
        self._fail_kind = fail_kind

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def put(self, ctx, kind, payload, **kwargs):
        if kind == self._fail_kind:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED,
                                 f"注入的写入失败（测试用，kind={kind}）",
                                 trace_ref=ctx.trace_ref))
        return self._real.put(ctx, kind, payload, **kwargs)


# ==========================================================================
# 夹具
# ==========================================================================


class IntegrationFixture:
    """临时 SQLite + 五个服务 + 调用上下文。用完即关。"""

    def __init__(self, provider=None) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-dlg-mem-")
        self.path = os.path.join(self.tmp, "integration.db")
        self.storage = SqliteStorage(Path(self.path))
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.evidence = EvidenceService(self.storage)
        self.memory = MemoryService(self.storage)
        self.provider = provider or CapturingProvider()

    # ---------------------------------------------------------- 服务装配

    def service(self, *, storage=None, memory=None, memory_reader=None,
                provider=None) -> DialogueService:
        st = storage or self.storage
        mem = memory if memory is not None else MemoryService(st)
        # 读端口默认是真实 MemoryService（可被显式覆盖以注入检索失败）；
        # 这样注入"只写不读"的故障写入器时，检索路径仍可正常工作。
        reader = memory_reader if memory_reader is not None else MemoryService(st)
        deps = DialogueDeps(
            profiles=self.persona, relations=self.relationship,
            evidence=self.evidence, memory=mem, storage=st,
            provider=provider or self.provider, memory_reader=reader)
        return DialogueService(deps)

    # ---------------------------------------------------------- 基础构件

    def entity(self, name: str) -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {
                "display_name": name, "entity_type": "FICTIONAL_CHARACTER",
                "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
                "fictional_character_meta": {
                    "works": [{"work_id": new_token("wrk_"),
                               "title": "《雾港与星灯》", "medium": "ANIMATION"}],
                    "adaptation_version": "2020 动画改编版",
                    "adaptation_lineage": ["原作轻小说版", "2020 动画改编版"],
                    "official_canon_level": "CANON",
                },
            },
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def persona_profile(self, entity_id: str, background: str) -> str:
        src = unwrap(self.evidence.register_source(
            self.ctx, SourceDraft(title="《雾港与星灯》访谈录",
                                  canonical_key=f"key_{new_token()}"))).id
        ref = unwrap(self.evidence.add_evidence(self.ctx, EvidenceDraft(
            claim=f"{entity_id} 的背景：{background}",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[entity_id],
            extraction_confidence=0.9, source_id=src))).id
        return unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8,
                                      evidence_refs=[ref]),
            applicable_variant_ids=[entity_id],
            open_questions=["习惯维度待补"]))).id

    def two_actors(self) -> tuple[str, str]:
        a = self.entity("阿雾")
        b = self.entity("阿灯")
        self.persona_profile(a, "阿雾：沉默寡言的灯塔看守。")
        self.persona_profile(b, "阿灯：活泼的渔家少女。")
        return a, b

    def candidate(self, content: str = RELATED_CONTENT, **kw) -> dict:
        base = {
            "kind": MemoryKind.EPISODIC.value,
            "content": content,
            "confidence": 0.6,
            "confidence_means": "模拟对话中的口头约定，属不确定内容，非事实断言",
            "salience": 0.6,
        }
        base.update(kw)
        return base

    def start(self, participants: list[str], *, topic: str | None = TOPIC,
              policy: str = POLICY_MEMORY_WRITE_EXPLICIT) -> str:
        view = unwrap(self.service().start_session(self.ctx, SessionDraft(
            participant_entity_ids=list(participants), topic=topic,
            memory_write_policy=policy)))
        return view.id

    # ---------------------------------------------------------- 断言辅助

    def count(self, kind: str, namespace: str | None = None) -> int:
        filters = {"kind": kind, "project_id": PROJECT}
        if namespace:
            filters["namespace"] = namespace
        q = Query(filters=filters, limit=MAX_QUERY_LIMIT)
        return len(unwrap(self.storage.query(self.ctx, kind, q,
                                            include_deleted=True)).items)

    def prompts(self) -> str:
        return self.provider._joined()

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class IntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = CapturingProvider()
        self.fx = IntegrationFixture(provider=self.provider)

    def tearDown(self) -> None:
        self.fx.close()

    # 便捷：跑一轮角色发言（可选带记忆候选）
    def run_turn(self, sid: str, *, candidates=None, service=None):
        svc = service or self.fx.service()
        return svc.run_round(self.fx.ctx, sid, memory_candidates=candidates)


# ==========================================================================
# 1. 跨会话记忆：写入 → 后续会话检索（AC-9）
# ==========================================================================


class TestCrossSessionRetrieval(IntegrationTestCase):
    def test_memory_written_in_session1_retrieved_in_session2(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])

        # 会话 1：显式写入一条符合规则的模拟记忆
        turn = unwrap(self.run_turn(s1, candidates=[fx.candidate()]))
        self.assertEqual(len(turn.generated_memory_ids), 1)
        mid = turn.generated_memory_ids[0]
        row = unwrap(fx.storage.get(fx.ctx, "MemoryItem", mid))
        self.assertEqual(row["namespace"], str(ProvenanceNamespace.SIMULATED))
        self.assertEqual(row["source_type"], MemorySourceType.SIMULATED_DIALOGUE.value)
        self.assertEqual(row["source_turn_id"], turn.id)          # 有出处
        self.assertEqual(row["scope"], MemoryScope.SESSION.value)
        self.assertEqual(row["scope_id"], s1)

        # 会话 2：同一批参与者 + 同一话题 → 跨会话检索到
        s2 = fx.start([a, b])
        self.assertNotEqual(s1, s2)
        draft = unwrap(fx.service().draft_turn(fx.ctx, s2, a))
        self.assertIn(mid, draft.referenced_memory_ids)
        self.assertIn(RELATED_CONTENT, fx.prompts())              # 真的注入了 prompt

    def test_cross_session_survives_process_restart(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        mid = unwrap(self.run_turn(s1, candidates=[fx.candidate()])).generated_memory_ids[0]

        # 重新打开同一数据库文件（模拟"重启进程后再读回"，AC-9）
        fx.storage.close()
        fx.storage = SqliteStorage(Path(fx.path))
        unwrap(fx.storage.init_schema(fx.ctx))
        fx.memory = MemoryService(fx.storage)

        s2 = fx.start([a, b])
        draft = unwrap(fx.service().draft_turn(fx.ctx, s2, a))
        self.assertIn(mid, draft.referenced_memory_ids)

    def test_session_own_memory_retrieved_without_topic(self):
        """无 topic 时，**本会话**自己的记忆仍可用（R1），只是不跨会话取（R2）。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b], topic=None)
        mid = unwrap(self.run_turn(s1, candidates=[fx.candidate()])).generated_memory_ids[0]
        draft = unwrap(fx.service().draft_turn(fx.ctx, s1, b))
        self.assertIn(mid, draft.referenced_memory_ids)

    def test_unrelated_memory_never_enters_context(self):
        """无关记忆（话题不符 / 对象不符 / 真实命名空间）都不进上下文（用户目标 4/5）。"""
        fx = self.fx
        a, b = fx.two_actors()
        outsider = fx.entity("阿潮")   # 非本会话参与者
        s1 = fx.start([a, b])

        # ① 同对象、不同话题 → 话题不符
        other_topic = unwrap(fx.memory.write_simulated(fx.ctx, {
            "scope": MemoryScope.SESSION.value, "scope_id": s1,
            "kind": MemoryKind.EPISODIC.value, "content": f"{UNRELATED_TOPIC}：闲聊片段。",
            "source_turn_id": new_id("ConversationTurn"),
            "confidence": 0.5, "confidence_means": "模拟对话片段",
            "salience": 0.5, "related_entity_ids": [a, b]})).id
        # ② 同话题、但对象不符 → 对象不符
        other_entity = unwrap(fx.memory.write_simulated(fx.ctx, {
            "scope": MemoryScope.SESSION.value, "scope_id": s1,
            "kind": MemoryKind.EPISODIC.value,
            "content": f"{TOPIC}：无关角色阿潮的私事。",
            "source_turn_id": new_id("ConversationTurn"),
            "confidence": 0.5, "confidence_means": "模拟对话片段",
            "salience": 0.5, "related_entity_ids": [outsider]})).id
        # ③ 话题 + 对象都对，但命名空间是 USER_PROVIDED（真实/用户资料）→ 必须排除
        real_ns = unwrap(fx.memory.write(fx.ctx, [{
            "scope": MemoryScope.SESSION.value, "scope_id": s1,
            "kind": MemoryKind.SEMANTIC.value, "content": f"{TOPIC}：用户真实笔记。",
            "source_type": MemorySourceType.USER_INPUT.value,
            "namespace": ProvenanceNamespace.USER_PROVIDED.value,
            "confidence": 0.9, "confidence_means": "用户自述",
            "salience": 0.9, "related_entity_ids": [a, b]}]))[0].id

        s2 = fx.start([a, b])
        draft = unwrap(fx.service().draft_turn(fx.ctx, s2, a))
        for excluded in (other_topic, other_entity, real_ns):
            self.assertNotIn(excluded, draft.referenced_memory_ids)
        self.assertNotIn(UNRELATED_CONTENT, fx.prompts())
        self.assertNotIn("用户真实笔记", fx.prompts())

    def test_no_topic_means_no_cross_session_pull(self):
        """会话无 topic → 不取 R2（跨会话），避免无关记忆无条件进入上下文。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        mid = unwrap(self.run_turn(s1, candidates=[fx.candidate()])).generated_memory_ids[0]
        s2 = fx.start([a, b], topic=None)
        draft = unwrap(fx.service().draft_turn(fx.ctx, s2, a))
        self.assertNotIn(mid, draft.referenced_memory_ids)


# ==========================================================================
# 2. 模拟内容隔离
# ==========================================================================


class TestSimulatedIsolation(IntegrationTestCase):
    def test_real_tables_unchanged_by_full_integration(self):
        fx = self.fx
        a, b = fx.two_actors()
        before = {k: fx.count(k) for k in
                  ("EvidenceItem", "PersonaProfile", "RelationshipProfile",
                   "SourceEvent", "Source")}
        s1 = fx.start([a, b])
        self.run_turn(s1, candidates=[fx.candidate()])
        s2 = fx.start([a, b])
        self.run_turn(s2, candidates=[fx.candidate(RELATED_CONTENT + "（第二次）")])
        after = {k: fx.count(k) for k in before}
        self.assertEqual(before, after)          # 对话 + 记忆写入后真实资料零变化（AC-13）
        self.assertEqual(fx.count("MemoryItem",
                                  namespace=ProvenanceNamespace.SIMULATED.value), 2)

    def test_written_memory_is_simulated_and_has_provenance(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        turn = unwrap(self.run_turn(s1, candidates=[fx.candidate()]))
        row = unwrap(fx.storage.get(fx.ctx, "MemoryItem", turn.generated_memory_ids[0]))
        self.assertEqual(row["namespace"], str(ProvenanceNamespace.SIMULATED))
        self.assertEqual(row["source_type"], MemorySourceType.SIMULATED_DIALOGUE.value)
        self.assertTrue(row["source_turn_id"])
        self.assertTrue(row["confidence_means"])
        # 派生标记不能被"洗白"：检索结果原样带出 namespace
        page = unwrap(fx.memory.search(fx.ctx, MemoryQuery(text=TOPIC)))
        self.assertEqual(page.items[0].namespace, str(ProvenanceNamespace.SIMULATED))

    def test_conversation_tables_are_simulated(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        turn = unwrap(self.run_turn(s1, candidates=[fx.candidate()]))
        srow = unwrap(fx.storage.get(fx.ctx, "ConversationSession", s1))
        trow = unwrap(fx.storage.get(fx.ctx, "ConversationTurn", turn.id))
        self.assertEqual(srow["namespace"], str(ProvenanceNamespace.SIMULATED))
        self.assertEqual(trow["namespace"], str(ProvenanceNamespace.SIMULATED))


# ==========================================================================
# 3. 记忆写入触发规则
# ==========================================================================


class TestWriteTriggerRule(IntegrationTestCase):
    def test_default_policy_never_writes(self):
        """默认策略 none：即便给了候选，也拒绝且不落任何东西（用户目标 3）。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b], policy=POLICY_MEMORY_WRITE_NONE)
        result = self.run_turn(s1, candidates=[fx.candidate()])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(fx.count("ConversationTurn"), 0)
        self.assertEqual(unwrap(fx.storage.get(
            fx.ctx, "ConversationSession", s1))["turn_count"], 0)

    def test_explicit_policy_without_candidates_writes_nothing(self):
        """显式政策但未给候选 → 正常落盘轮次，但一条记忆都不写。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        turn = unwrap(self.run_turn(s1))            # 无 candidates
        self.assertEqual(turn.generated_memory_ids, [])
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(fx.count("ConversationTurn"), 1)

    def test_explicit_policy_with_candidates_writes(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        turn = unwrap(self.run_turn(s1, candidates=[fx.candidate()]))
        self.assertEqual(len(turn.generated_memory_ids), 1)
        self.assertEqual(fx.count("MemoryItem"), 1)

    def test_non_session_scope_rejected(self):
        """候选 scope 不是 SESSION → 拒绝：单轮对话不得升级为实体级/人格级稳定记忆。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        bad = fx.candidate()
        bad["scope"] = MemoryScope.ENTITY.value
        result = self.run_turn(s1, candidates=[bad])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(fx.count("ConversationTurn"), 0)

    def test_candidate_missing_confidence_means_rejected_and_rolled_back(self):
        """不确定内容必须带置信度含义；缺失 → 拒绝，且轮次一并回滚。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        bad = fx.candidate()
        del bad["confidence_means"]
        result = self.run_turn(s1, candidates=[bad])
        self.assertTrue(is_err(result))
        # write_simulated 的必填字段前置校验先命中（同样是拒绝，不是伪造成功）
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(fx.count("ConversationTurn"), 0)

    def test_full_automatic_write_never_happens(self):
        """多轮连续对话（不给候选）→ 一条记忆都不写（绝不"每轮全写"）。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        result = unwrap(fx.service().run(fx.ctx, s1, max_rounds=4))
        self.assertEqual(len(result.turns), 4)
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(fx.count("ConversationTurn"), 4)


# ==========================================================================
# 4. 失败处理（不伪造成功）
# ==========================================================================


class TestFailureHandling(IntegrationTestCase):
    def test_memory_retrieval_failure_propagates(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        svc = fx.service(memory_reader=FailingSearchReader(fx.memory))
        result = svc.draft_turn(fx.ctx, s1, a)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STORAGE_READ_FAILED)

    def test_memory_write_failure_rolls_back_turn(self):
        """记忆写入失败 → 整体回滚：轮次也不留（§8.2 同一事务）。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        svc = fx.service(memory=FailingWriter())
        result = self.run_turn(s1, candidates=[fx.candidate()], service=svc)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STORAGE_WRITE_FAILED)
        self.assertEqual(fx.count("ConversationTurn"), 0)     # 回滚干净
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(unwrap(fx.storage.get(
            fx.ctx, "ConversationSession", s1))["turn_count"], 0)

    def test_memory_persist_failure_rolls_back_turn(self):
        """底层 MemoryItem 落盘失败 → 同样整体回滚（不出现"有对话无记忆"）。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        broken = FailingPutStorage(fx.storage, "MemoryItem")
        svc = fx.service(storage=broken, memory=MemoryService(broken))
        result = self.run_turn(s1, candidates=[fx.candidate()], service=svc)
        self.assertTrue(is_err(result))
        self.assertEqual(fx.count("ConversationTurn"), 0)
        self.assertEqual(fx.count("MemoryItem"), 0)

    def test_provider_failure_writes_nothing(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        svc = fx.service(provider=FailingProvider())
        result = self.run_turn(s1, candidates=[fx.candidate()], service=svc)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)
        self.assertEqual(fx.count("MemoryItem"), 0)
        self.assertEqual(fx.count("ConversationTurn"), 0)

    def test_session_persist_failure_propagates(self):
        fx = self.fx
        a, b = fx.two_actors()
        broken = FailingPutStorage(fx.storage, "ConversationSession")
        svc = fx.service(storage=broken, memory=MemoryService(broken))
        result = svc.start_session(fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], topic=TOPIC,
            memory_write_policy=POLICY_MEMORY_WRITE_EXPLICIT))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.STORAGE_WRITE_FAILED)


# ==========================================================================
# 5. 幂等与重试（暂停 / 恢复不重复写）
# ==========================================================================


class TestIdempotencyAndRetry(IntegrationTestCase):
    def test_same_turn_committed_twice_writes_memory_once(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        svc = fx.service()
        draft = unwrap(svc.draft_turn(fx.ctx, s1, a))
        first = unwrap(svc.commit_turn(fx.ctx, s1, draft,
                                       memory_candidates=[fx.candidate()]))
        second = unwrap(svc.commit_turn(fx.ctx, s1, draft,
                                        memory_candidates=[fx.candidate()]))
        self.assertEqual(first.id, second.id)                       # 同一轮
        self.assertEqual(first.generated_memory_ids, second.generated_memory_ids)
        self.assertEqual(fx.count("MemoryItem"), 1)                 # 没重复写
        self.assertEqual(fx.count("ConversationTurn"), 1)

    def test_resume_does_not_duplicate_completed_turn(self):
        """模拟暂停后恢复：已完成的轮次不会被重做，也不重复写记忆。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        svc = fx.service()
        turn = unwrap(self.run_turn(s1, candidates=[fx.candidate()], service=svc))
        # "恢复"：用同一 draft 再提交一次（同一 index）
        draft = unwrap(svc.draft_turn(fx.ctx, s1, turn.speaker_entity_id))
        # draft_turn 会算出下一轮 index；这里直接构造同 index 的重复提交
        from persona.modules.dialogue import TurnDraftResult
        replay = TurnDraftResult(
            session_id=s1, index=turn.index,
            speaker_entity_id=turn.speaker_entity_id, content=turn.content,
            selection_reason=turn.selection_reason,
            referenced_memory_ids=[], referenced_persona_item_ids=[],
            referenced_relationship_item_ids=[], safety_flags=[], usage=None)
        again = unwrap(svc.commit_turn(fx.ctx, s1, replay,
                                       memory_candidates=[fx.candidate()]))
        self.assertEqual(again.id, turn.id)
        self.assertEqual(fx.count("ConversationTurn"), 1)
        self.assertEqual(fx.count("MemoryItem"), 1)
        self.assertIsNotNone(draft)

    def test_multiple_turns_each_write_their_own_memory(self):
        """不同轮次各自写入一条（幂等只针对同一轮，不吞掉合法的新记忆）。"""
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        svc = fx.service()
        t1 = unwrap(svc.run_round(fx.ctx, s1,
                                  memory_candidates=[fx.candidate()]))
        t2 = unwrap(svc.run_round(fx.ctx, s1, memory_candidates=[
            fx.candidate(content=f"{TOPIC}：第二轮又提了一次。")]))
        self.assertNotEqual(t1.id, t2.id)
        self.assertEqual(len(t1.generated_memory_ids), 1)
        self.assertEqual(len(t2.generated_memory_ids), 1)
        self.assertEqual(fx.count("MemoryItem"), 2)
        self.assertEqual(fx.count("ConversationTurn"), 2)


# ==========================================================================
# 6. 生命周期规则在集成中仍有效
# ==========================================================================


class TestLifecycleInIntegration(IntegrationTestCase):
    def _write_memory(self) -> tuple[str, str, str]:
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        mid = unwrap(self.run_turn(s1, candidates=[fx.candidate()])).generated_memory_ids[0]
        s2 = fx.start([a, b])
        return a, s2, mid

    def _context_ids(self, sid: str, speaker: str) -> list[str]:
        return unwrap(self.fx.service().draft_turn(
            self.fx.ctx, sid, speaker)).referenced_memory_ids

    def test_baseline_retrievable_then_deleted_gone(self):
        fx = self.fx
        a, s2, mid = self._write_memory()
        self.assertIn(mid, self._context_ids(s2, a))          # 基线：可检索
        unwrap(fx.memory.delete(fx.ctx, DeleteTarget(id=mid)))
        self.assertNotIn(mid, self._context_ids(s2, a))       # 删除后不再出现（AC-10）
        self.assertNotIn(RELATED_CONTENT, fx.prompts())

    def test_superseded_old_not_retrieved_new_is(self):
        fx = self.fx
        a, s2, mid = self._write_memory()
        new = unwrap(fx.memory.supersede(fx.ctx, mid, {
            "content": f"{TOPIC}：修订后的说法（新版本）。"}))
        ids = self._context_ids(s2, a)
        self.assertNotIn(mid, ids)
        self.assertIn(new.id, ids)
        self.assertIn("修订后的说法", fx.prompts())

    def test_expired_not_retrieved(self):
        fx = self.fx
        a, b = fx.two_actors()
        s1 = fx.start([a, b])
        past = to_iso(utc_now() - timedelta(days=1))
        mid = unwrap(self.run_turn(s1, candidates=[
            fx.candidate(expires_at=past)])).generated_memory_ids[0]
        s2 = fx.start([a, b])
        self.assertIn(mid, self._context_ids(s2, a))          # 未扫描前仍 ACTIVE
        unwrap(fx.memory.expire_sweep(fx.ctx))
        self.assertNotIn(mid, self._context_ids(s2, a))

    def test_archived_not_retrieved(self):
        fx = self.fx
        a, s2, mid = self._write_memory()
        unwrap(fx.memory.archive(fx.ctx, [mid]))
        self.assertNotIn(mid, self._context_ids(s2, a))

    def test_conflicted_excluded_from_default_context(self):
        """冲突记忆双向登记后双方置 CONFLICTED；默认检索只取 ACTIVE，故不进上下文。

        这是既有契约（DATA_MODEL §7.6 #34 + P3.5 D-153）在集成路径上的直接后果，
        记录为遗留项 H-7（见 RUNNING §5.9）。
        """
        fx = self.fx
        a, s2, mid = self._write_memory()
        other = unwrap(fx.memory.write(fx.ctx, [{
            "scope": MemoryScope.SESSION.value, "scope_id": s2,
            "kind": MemoryKind.EPISODIC.value,
            "content": f"{TOPIC}：与上一条相互矛盾的说法。",
            "source_type": MemorySourceType.USER_INPUT.value,
            "namespace": ProvenanceNamespace.USER_PROVIDED.value,
            "confidence": 0.5, "confidence_means": "用户自述（用于构造冲突）",
            "salience": 0.5, "related_entity_ids": [a]}]))[0].id
        unwrap(fx.memory.link_conflict(fx.ctx, mid, other))
        states = {(unwrap(fx.storage.get(fx.ctx, "MemoryItem", i)))["lifecycle_status"]
                  for i in (mid, other)}
        self.assertEqual(states, {MemoryStatus.CONFLICTED.value})
        self.assertNotIn(mid, self._context_ids(s2, a))
        # 但显式查询 CONFLICTED 时两条都返回（不合并、不择优）
        page = unwrap(fx.memory.search(fx.ctx, MemoryQuery(
            text=TOPIC, lifecycle_states=[MemoryStatus.CONFLICTED.value])))
        self.assertEqual({v.id for v in page.items}, {mid, other})

    def test_deleted_memory_absent_after_restart(self):
        fx = self.fx
        a, s2, mid = self._write_memory()
        unwrap(fx.memory.delete(fx.ctx, DeleteTarget(id=mid)))
        fx.storage.close()
        fx.storage = SqliteStorage(Path(fx.path))
        unwrap(fx.storage.init_schema(fx.ctx))
        fx.memory = MemoryService(fx.storage)
        self.assertNotIn(mid, self._context_ids(s2, a))


if __name__ == "__main__":
    unittest.main()
