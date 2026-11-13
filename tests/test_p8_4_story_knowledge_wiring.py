"""P8.4-B：Story-Session 桥接 + CharacterInstance 档案校验 + 会话级知识缓存。

全部跑在**临时目录**的 SQLite 上，不碰正式库、不触网（Provider 全离线桩）。

验收标准映射（任务书 §三）：
  1.  新会话 story_id 正确保存            → test_story_session_saves_binding
  2.  旧会话无 story_id 回读 None         → test_legacy_session_readback_none
  3.  非法 story_id 明确报错              → test_invalid_story_id_rejected
  4.  绑定不存在 PersonaProfile 失败      → test_create_instance_profile_missing
  5.  profile_version 不存在失败          → test_create_instance_version_missing
  6.  历史版本可绑定，不强制最新          → test_create_instance_historical_version_ok
  7.  生产装配未注入 PersonaReader 测试失败 → test_assembly_wiring_assertion
  8.  跨 Story / 跨 chat 缓存严格隔离     → test_cache_isolation_across_stories_and_scopes
  9.  缓存命中不重复全项目扫描            → test_cache_hit_no_rescan
  10. 写入后失效，下次读取拿新状态        → test_write_then_invalidate_refreshes
  11. 普通会话行为与旧版本一致            → test_plain_session_unchanged
  12. P8.3-B 投影/Dialogue 无回归         → 全量回归单独跑（test_p8_3_* 等）

附加（诚实性红线）：
  - test_story_session_requires_port：无知识端口拒绝开 Story 会话（不静默降级）
  - test_binding_cannot_be_overwritten：story 绑定只写一次，无接口可覆盖
  - test_story_session_prompt_has_no_knowledge：本阶段知识**不进 prompt**
  - test_start_session_prime_failure_leaves_no_session：prime 失败不开会话
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    ProvenanceNamespace,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.ids import new_id, new_token
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.types import Query, UsageReport
from persona.modules.character import (
    K_OBSERVED,
    CharacterInstanceDraft,
    CharacterService,
    KnowledgeEntry,
)
from persona.modules.dialogue import DialogueDeps, DialogueService, SessionDraft
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.memory import MemoryService
from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    ProfileSection,
)
from persona.modules.relationship import RelationshipService
from persona.modules.research import ResearchDeps, ResearchService
from persona.orchestrator.business import (
    BusinessDeps,
    BusinessOrchestrator,
    StoryKnowledgeCache,
)
from persona.orchestrator.tasks import TaskService
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

PROJECT = "prj_p84b"

STORY_A = new_id("Story")
STORY_B = new_id("Story")


# ==========================================================================
# 离线桩
# ==========================================================================


class RecordingChatProvider:
    """离线对话桩：返回确定性非空文本并记录最后一次 messages（供 prompt 断言）。"""

    def __init__(self) -> None:
        self.last_messages: list = []
        self.chat_calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="p84b-chat", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        self.chat_calls += 1
        return Ok(ChatResponse(
            text="（模拟）我知道分寸，接着说。",
            finish_reason="stop", model_id="p84b-chat",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class EmptySearchProvider:
    """离线检索桩（ResearchService 装配用，永不发真请求）。"""

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="p84b-search", supports_chat=True))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class CountingStorage:
    """包装真实存储：按 kind 计数 query()、记录审计事件；其余全部委派。"""

    def __init__(self, real: SqliteStorage) -> None:
        self._real = real
        self.character_instance_queries = 0
        self.audit_events: list[tuple[str, str]] = []

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def query(self, ctx, kind, q, **kwargs):
        if kind == "CharacterInstance":
            self.character_instance_queries += 1
        return self._real.query(ctx, kind, q, **kwargs)

    def append_audit(self, ctx, level, event, **kwargs):
        self.audit_events.append((str(level), str(event)))
        return self._real.append_audit(ctx, level, event, **kwargs)


class FailingCharacterQueryStorage:
    """包装真实存储：CharacterInstance 的 query 一律失败（prime 失败路径用）。"""

    def __init__(self, real: SqliteStorage) -> None:
        self._real = real

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def query(self, ctx, kind, q, **kwargs):
        if kind == "CharacterInstance":
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, "注入的查询失败"))
        return self._real.query(ctx, kind, q, **kwargs)


# ==========================================================================
# 夹具
# ==========================================================================


class P84BFixture:
    """临时 SQLite + 全套服务。`wrap` 可注入存储包装器（计数/故障注入）。"""

    def __init__(self, wrap=None) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-p84b-")
        self.raw_storage = SqliteStorage(Path(self.tmp) / "p84b.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.raw_storage.init_schema(self.ctx))
        self.storage = wrap(self.raw_storage) if wrap is not None else self.raw_storage

        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.evidence = EvidenceService(self.storage)
        self.memory = MemoryService(self.storage)
        self.provider = RecordingChatProvider()
        self.characters = CharacterService(self.storage)

        self.dialogue = DialogueService(DialogueDeps(
            profiles=self.persona, relations=self.relationship,
            evidence=self.evidence, memory=self.memory,
            storage=self.storage, provider=self.provider,
            memory_reader=self.memory))

    # ---------------------------------------------------------- 基础构件

    def entity(self, name: str) -> str:
        payload = {
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
        return unwrap(self.raw_storage.put(
            self.ctx, "PersonEntity", payload, project_id=PROJECT,
            namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def _evidence_ref(self, claim: str, subject: str) -> str:
        src = unwrap(self.evidence.register_source(self.ctx, SourceDraft(
            title="《雾港与星灯》访谈录",
            canonical_key=f"key_{new_token()}"))).id
        return unwrap(self.evidence.add_evidence(self.ctx, EvidenceDraft(
            claim=claim, tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[subject],
            extraction_confidence=0.9, source_id=src))).id

    def persona_profile(self, entity_id: str,
                        background: str = "雾港出身的青年。") -> int:
        """创建档案，返回其版本号（首建恒为 1）。"""
        ref = self._evidence_ref(f"{entity_id} 的背景：{background}", entity_id)
        created = unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8,
                                      evidence_refs=[ref]),
            applicable_variant_ids=[entity_id],
            open_questions=["习惯维度待补"])))
        return int(created.version)

    def revise_profile(self, entity_id: str, background: str) -> None:
        """生成档案新版本（内容必须与最新版不同）。"""
        ref = self._evidence_ref(f"{entity_id} 修订背景：{background}", entity_id)
        unwrap(self.persona.revise(self.ctx, entity_id, PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8,
                                      evidence_refs=[ref]),
            applicable_variant_ids=[entity_id],
            open_questions=["习惯维度待补"],
            change_reason="测试：背景增补修订")))

    def two_actors(self) -> tuple[str, str]:
        a = self.entity("阿雾")
        b = self.entity("阿灯")
        self.persona_profile(a, "阿雾：沉默寡言的灯塔看守。")
        self.persona_profile(b, "阿灯：活泼的渔家少女。")
        return a, b

    def knowledge(self, content: str, *, about: str = "world") -> KnowledgeEntry:
        return KnowledgeEntry(
            id=new_id("KnowledgeEntry"), content=content,
            category=K_OBSERVED, about=about,
            source_ref="亲历轮次 t0", acquired_turn=0)

    def instance(self, story_id: str, character_id: str, *,
                 profile_version: int = 1,
                 knowledge: list[KnowledgeEntry] | None = None):
        return unwrap(self.characters.create_instance(
            self.ctx, CharacterInstanceDraft(
                story_id=story_id, character_id=character_id,
                profile_version=profile_version,
                display_name="测试化身",
                knowledge=knowledge or []), PROJECT))

    def knowledge_cache(self) -> StoryKnowledgeCache:
        return StoryKnowledgeCache(CharacterService(
            self.storage, persona_reader=self.persona))

    def close(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _fixture(**kwargs) -> P84BFixture:
    return P84BFixture(**kwargs)


def _wire(fx: P84BFixture) -> StoryKnowledgeCache:
    """手动把知识端口接到 fixture 的 Dialogue（桥接/轮次类测试用）。"""
    cache = fx.knowledge_cache()
    fx.dialogue.attach_knowledge_port(cache)
    return cache


# ==========================================================================
# A. Story-Session 桥接（验收 1/2/3）
# ==========================================================================


class StorySessionBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()
        self.a, self.b = self.fx.two_actors()

    def tearDown(self) -> None:
        self.fx.close()

    def test_story_session_saves_binding(self):
        """验收 1：story_id 随会话记录持久化，SessionView 正确回读。"""
        cache = _wire(self.fx)
        view = unwrap(self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b], story_id=STORY_A)))
        self.assertEqual(view.story_id, STORY_A)
        payload = unwrap(self.fx.raw_storage.get(
            self.fx.ctx, "ConversationSession", view.id))
        self.assertEqual(payload["story_id"], STORY_A)
        # 端口已被 start_session prime（绑定 STORY_A；无知识角色 → 空列表）。
        got = unwrap(cache.knowledge_for(self.fx.ctx, STORY_A, self.a))
        self.assertEqual(got, [])

    def test_legacy_session_readback_none(self):
        """验收 2：旧记录（payload 无 story_id 键）回读 None；新普通会话同。"""
        _wire(self.fx)
        view = unwrap(self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b])))
        self.assertIsNone(view.story_id)
        # 手工造一条"旧版 payload"（无 story_id 键）验证回读兼容。
        legacy_id = new_id("ConversationSession")
        unwrap(self.fx.raw_storage.put(
            self.fx.ctx, "ConversationSession", {
                "participant_entity_ids": [self.a, self.b],
                "topic": None,
                "persona_profile_ids": [],
                "relationship_profile_ids": [],
                "speaker_selection_policy": "least_recently_spoken",
                "memory_scope": "SESSION",
                "memory_write_policy": "none",
                "turn_count": 0,
                "budget_used": {"requests": 0, "tokens_in": None,
                                "tokens_out": None, "latency_ms": 0},
                "status": "ACTIVE",
            }, project_id=PROJECT,
            namespace=ProvenanceNamespace.SIMULATED, id=legacy_id))
        payload = unwrap(self.fx.dialogue._get_session(self.fx.ctx, legacy_id))
        self.assertIsNone(payload.get("story_id"))

    def test_invalid_story_id_rejected(self):
        """验收 3：非法 story_id 直接 Err（对齐 topic 门禁风格）。"""
        for bad in ("story_1", "sty_short", "sty_" + "z" * 32, "", "css_x"):
            result = self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
                participant_entity_ids=[self.a, self.b], story_id=bad))
            self.assertTrue(is_err(result), f"应拒绝 {bad!r}")
            self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_story_session_requires_port(self):
        """诚实性红线：无知识端口时拒绝开 Story 会话，绝不静默降级。"""
        result = self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b], story_id=STORY_A))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)
        self.assertIn("知识端口", result.error.message)

    def test_binding_cannot_be_overwritten(self):
        """story 绑定只写一次；run_round / end_session 都不会改写 payload。"""
        _wire(self.fx)
        view = unwrap(self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b], story_id=STORY_A)))
        unwrap(self.fx.dialogue.run_round(self.fx.ctx, view.id, "你好"))
        unwrap(self.fx.dialogue.end_session(self.fx.ctx, view.id, "测试结束"))
        payload = unwrap(self.fx.raw_storage.get(
            self.fx.ctx, "ConversationSession", view.id))
        self.assertEqual(payload["story_id"], STORY_A)


# ==========================================================================
# B. CharacterInstance 档案关联校验（验收 4/5/6/7）
# ==========================================================================


class ProfileLinkValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()
        self.a, self.b = self.fx.two_actors()

    def tearDown(self) -> None:
        self.fx.close()

    def test_create_instance_profile_missing(self):
        """验收 4：实体没有 PersonaProfile → NOT_FOUND_ENTITY。"""
        svc = CharacterService(self.fx.storage, persona_reader=self.fx.persona)
        ghost = self.fx.entity("无档案者")          # 有实体、无档案
        result = svc.create_instance(self.fx.ctx, CharacterInstanceDraft(
            story_id=STORY_A, character_id=ghost, profile_version=1), PROJECT)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_ENTITY)

    def test_create_instance_version_missing(self):
        """验收 5：指定 profile_version 不存在 → NOT_FOUND_VERSION（附现有版本）。"""
        svc = CharacterService(self.fx.storage, persona_reader=self.fx.persona)
        result = svc.create_instance(self.fx.ctx, CharacterInstanceDraft(
            story_id=STORY_A, character_id=self.a, profile_version=99), PROJECT)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.NOT_FOUND_VERSION)
        self.assertIn("现有版本", result.error.message)

    def test_create_instance_historical_version_ok(self):
        """验收 6：档案已有 v2，仍允许绑定 v1（只验存在，不强制最新）。"""
        svc = CharacterService(self.fx.storage, persona_reader=self.fx.persona)
        self.fx.revise_profile(self.a, "阿雾：修订后的背景（v2）。")
        view = unwrap(svc.create_instance(self.fx.ctx, CharacterInstanceDraft(
            story_id=STORY_A, character_id=self.a, profile_version=1), PROJECT))
        self.assertEqual(view.profile_version, 1)
        # identity 字段依旧不可修订（本阶段不改变该规则）。
        result = svc.revise_instance(self.fx.ctx, view.id,
                                     {"profile_version": 2}, "尝试改版本")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_GATE_REJECTED)

    def test_assembly_wiring_assertion(self):
        """验收 7：生产装配断言——BusinessOrchestrator 必须注入 PersonaReader
        并把知识端口接到 Dialogue；装配层任何"静默跳过"都会让本测试失败。"""
        research = ResearchService(ResearchDeps(
            provider=OfflineGuardProvider(EmptySearchProvider()),
            evidence=self.fx.evidence,
            search_metadata={"fixture_key": "p84b"}))
        deps = BusinessDeps(
            research=research, evidence=self.fx.evidence,
            persona=self.fx.persona, relationship=self.fx.relationship,
            dialogue=self.fx.dialogue,
            task_service=TaskService(self.fx.storage),
            storage=self.fx.storage)
        orch = BusinessOrchestrator(deps)
        # ① PersonaReader 必须注入且就是装配层那份档案读端口。
        self.assertIsNotNone(orch.character_service.persona_reader)
        self.assertIs(orch.character_service.persona_reader, deps.persona)
        # ② 知识端口必须接到 Dialogue（同一对象，不是各自为政的副本）。
        self.assertIs(self.fx.dialogue.knowledge_port(), orch.story_knowledge)
        # ③ 知识写监听器必须接到缓存的 invalidate（写后显式失效）。
        self.assertIsNotNone(orch.character_service._knowledge_write_listener)
        # 负面对照：直接构造（绕过装配）→ reader 为 None（显式记录的兼容行为）。
        bare = CharacterService(self.fx.storage)
        self.assertIsNone(bare.persona_reader)

    def test_assembly_rewiring_replaces_port(self):
        """同一 Dialogue 上重复装配：端口以最后一次为准（会话级快照，替换无损失）。"""
        research = ResearchService(ResearchDeps(
            provider=OfflineGuardProvider(EmptySearchProvider()),
            evidence=self.fx.evidence,
            search_metadata={"fixture_key": "p84b"}))
        deps = BusinessDeps(
            research=research, evidence=self.fx.evidence,
            persona=self.fx.persona, relationship=self.fx.relationship,
            dialogue=self.fx.dialogue,
            task_service=TaskService(self.fx.storage),
            storage=self.fx.storage)
        first = BusinessOrchestrator(deps)
        second = BusinessOrchestrator(deps)
        self.assertIsNot(first.story_knowledge, second.story_knowledge)
        self.assertIs(self.fx.dialogue.knowledge_port(), second.story_knowledge)


# ==========================================================================
# C. 会话级知识缓存（验收 8/9/10）
# ==========================================================================


class StoryKnowledgeCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture()
        self.a, self.b = self.fx.two_actors()
        secret = self.fx.knowledge("月蚀之夜，钟楼第三层藏着一枚黄铜钥匙。",
                                   about="钟楼")
        self.inst_a = self.fx.instance(STORY_A, self.a, knowledge=[secret])
        other = self.fx.knowledge("南码头每晚十一点换岗。", about="南码头")
        self.fx.instance(STORY_B, self.a, knowledge=[other])
        self.inst_b = self.fx.instance(STORY_A, self.b)   # 有实例、无知识

    def tearDown(self) -> None:
        self.fx.close()

    def test_cache_isolation_across_stories_and_scopes(self):
        """验收 8：跨 Story / 跨缓存实例严格隔离，绝不串知识。"""
        cache_a = self.fx.knowledge_cache()
        unwrap(cache_a.prime_session(self.fx.ctx, STORY_A, [self.a, self.b]))
        got_a = unwrap(cache_a.knowledge_for(self.fx.ctx, STORY_A, self.a))
        self.assertEqual(len(got_a), 1)
        self.assertIn("黄铜钥匙", got_a[0]["content"])
        # 无知识角色 → 空列表（且被缓存，不反复扫描）。
        self.assertEqual(
            unwrap(cache_a.knowledge_for(self.fx.ctx, STORY_A, self.b)), [])
        # ① 同一缓存查别的 story → 空（严格隔离）。
        self.assertEqual(
            unwrap(cache_a.knowledge_for(self.fx.ctx, STORY_B, self.a)), [])
        # ② 另一缓存实例（另一 chat scope）绑 STORY_B → 只有 B 的知识。
        cache_b = self.fx.knowledge_cache()
        unwrap(cache_b.prime_session(self.fx.ctx, STORY_B, [self.a]))
        got_b = unwrap(cache_b.knowledge_for(self.fx.ctx, STORY_B, self.a))
        self.assertEqual(len(got_b), 1)
        self.assertIn("换岗", got_b[0]["content"])
        # ③ 同一缓存重新 prime（新会话）→ 数据整体按新会话重建。
        unwrap(cache_a.prime_session(self.fx.ctx, STORY_B, [self.a]))
        self.assertEqual(
            unwrap(cache_a.knowledge_for(self.fx.ctx, STORY_B, self.a)), got_b)
        self.assertEqual(
            unwrap(cache_a.knowledge_for(self.fx.ctx, STORY_A, self.a)), [])

    def test_cache_hit_no_rescan(self):
        """验收 9：prime 恰好每人一次扫描；此后整场对话零 CharacterInstance 扫描。"""
        fx = _fixture(wrap=CountingStorage)
        try:
            counting = fx.storage
            a, b = fx.two_actors()
            fx.instance(STORY_A, a, knowledge=[
                fx.knowledge("北灯塔的灯每晚十点准时点亮。", about="北灯塔")])
            fx.instance(STORY_A, b)
            cache = fx.knowledge_cache()
            fx.dialogue.attach_knowledge_port(cache)
            baseline = counting.character_instance_queries
            view = unwrap(fx.dialogue.start_session(fx.ctx, SessionDraft(
                participant_entity_ids=[a, b], story_id=STORY_A)))
            after_prime = counting.character_instance_queries
            # prime 恰好为每个参与者扫描一次（不多不少）。
            self.assertEqual(after_prime - baseline, 2)
            # 两轮对话：知识读取全部命中缓存，零新增扫描。
            unwrap(fx.dialogue.run_round(fx.ctx, view.id, "今晚去哪？"))
            unwrap(fx.dialogue.run_round(fx.ctx, view.id, "那就北灯塔吧。"))
            self.assertEqual(counting.character_instance_queries, after_prime)
        finally:
            fx.close()

    def test_write_then_invalidate_refreshes(self):
        """验收 10：知识写入 → 显式失效 → 下一次读取拿到新状态。"""
        cache = self.fx.knowledge_cache()
        unwrap(cache.prime_session(self.fx.ctx, STORY_A, [self.a]))
        got = unwrap(cache.knowledge_for(self.fx.ctx, STORY_A, self.a))
        self.assertEqual(len(got), 1)
        # 接上写监听器（生产装配由 BusinessOrchestrator 完成；这里手动等价）。
        self.fx.characters.set_knowledge_write_listener(cache.invalidate)
        added = self.fx.knowledge("潮退后礁石间会露出旧船骸。", about="礁石")
        unwrap(self.fx.characters.add_knowledge(
            self.fx.ctx, self.inst_a.id, added))
        got2 = unwrap(cache.knowledge_for(self.fx.ctx, STORY_A, self.a))
        self.assertEqual(len(got2), 2)
        self.assertIn(added.id, {e["id"] for e in got2})
        # correct_knowledge 同样走 revise → 失效 → 修正后只剩 ACTIVE 新条。
        unwrap(self.fx.characters.correct_knowledge(
            self.fx.ctx, self.inst_a.id, got[0]["id"],
            "月蚀之夜，钟楼第三层藏着两枚黄铜钥匙。", acquired_turn=1))
        got3 = unwrap(cache.knowledge_for(self.fx.ctx, STORY_A, self.a))
        self.assertTrue(all(e["status"] == "ACTIVE" for e in got3))
        self.assertNotIn(got[0]["id"], {e["id"] for e in got3})

    def test_start_session_prime_failure_leaves_no_session(self):
        """诚实性红线：prime 失败 → 会话不开、库里不留半开记录。"""
        fx = _fixture()
        try:
            a, b = fx.two_actors()
            fx.instance(STORY_A, a)
            fx.instance(STORY_A, b)
            # 先建数据，再把存储包上故障层，故障只影响本轮会话开启。
            failing = FailingCharacterQueryStorage(fx.raw_storage)
            reader = PersonaService(failing)
            dlg = DialogueService(DialogueDeps(
                profiles=reader, relations=RelationshipService(failing),
                evidence=fx.evidence, memory=MemoryService(failing),
                storage=failing, provider=fx.provider,
                memory_reader=MemoryService(failing)))
            dlg.attach_knowledge_port(StoryKnowledgeCache(
                CharacterService(failing, persona_reader=reader)))
            result = dlg.start_session(fx.ctx, SessionDraft(
                participant_entity_ids=[a, b], story_id=STORY_A))
            self.assertTrue(is_err(result))
            sessions = unwrap(fx.raw_storage.query(
                fx.ctx, "ConversationSession",
                Query(filters={"project_id": PROJECT})))
            self.assertEqual(sessions.total, 0)
        finally:
            fx.close()


# ==========================================================================
# D. 普通会话兼容 + prompt 红线（验收 11）
# ==========================================================================


class PlainSessionCompatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = _fixture(wrap=CountingStorage)
        self.counting = self.fx.storage
        self.a, self.b = self.fx.two_actors()
        self.cache = _wire(self.fx)

    def tearDown(self) -> None:
        self.fx.close()

    def _audit_events(self) -> list[tuple[str, str]]:
        return list(self.counting.audit_events)

    def test_plain_session_unchanged(self):
        """验收 11：普通会话（story_id=None）不 prime、不读知识、无知识审计。"""
        view = unwrap(self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b])))
        self.assertIsNone(view.story_id)
        baseline = self.counting.character_instance_queries
        unwrap(self.fx.dialogue.run_round(self.fx.ctx, view.id, "在吗？"))
        unwrap(self.fx.dialogue.run_round(self.fx.ctx, view.id, "聊聊天气。"))
        self.assertEqual(self.counting.character_instance_queries, baseline)
        self.assertNotIn(("INFO", "dialogue.knowledge_read"), self._audit_events())

    def test_story_session_prompt_has_no_knowledge(self):
        """诚实性红线：本阶段知识只读审计、不进 prompt（不改提示词）。"""
        secret = self.fx.knowledge("月蚀之夜，钟楼第三层藏着一枚黄铜钥匙。",
                                   about="钟楼")
        self.fx.instance(STORY_A, self.a, knowledge=[secret])
        view = unwrap(self.fx.dialogue.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b], story_id=STORY_A)))
        unwrap(self.fx.dialogue.run_round(
            self.fx.ctx, view.id, "钟楼里有什么？"))
        joined = "\n".join(str(m) for m in self.fx.provider.last_messages)
        self.assertNotIn("黄铜钥匙", joined)
        # 审计确认读取确实发生了（走缓存、零扫描、不进 prompt）。
        self.assertIn(("INFO", "dialogue.knowledge_read"), self._audit_events())


if __name__ == "__main__":
    unittest.main()
