"""P8.3-B：人格投影最小生产接线——回归测试。

两类验证，都走**真实代码路径**，不靠 mock 断言内部实现细节：

1. `TestProjectionCache`：会话级缓存（分键、版本失效、零重查、降级、
   跨会话隔离、BehaviorPolicy 复用）。profiles/evidence 用轻量只读桩，
   但 `distill_profile()` / `build_behavior_policy()` 都是真函数。
2. `TestDialogueProjectionConsumption`：真实 SQLite + 真实 DialogueService
   + 真实 `run_round()`，检查 system prompt 与 safety_flags 里八个结构化
   字段的消费结果（T-J1~T-J7），以及降级路径逐字兼容。
3. `TestAssemblyWiring`：装配开关默认关闭、开启后端口确实接到 Dialogue。

全程离线：临时 SQLite + 脚本化 Provider，不触网、不花钱。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    ProfileStatus,
    ProvenanceNamespace,
    Stability,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, UsageDelta, unwrap
from persona.core.types import UsageReport
from persona.modules.dialogue import (
    FLAG_PROJECTION_CONFLICT,
    FLAG_PROJECTION_DEGRADED,
    FLAG_PROJECTION_NEEDS_VERIFICATION,
    FLAG_PROJECTION_UNRESOLVED,
    LABEL_LAYER_A,
    LABEL_LAYER_B,
    LABEL_LAYER_C,
    LABEL_TRAITS,
    POLICY_LEAST_RECENTLY_SPOKEN,
    DialogueDeps,
    DialogueService,
    SessionDraft,
)
from persona.modules.distill import (
    ConfidenceBasis,
    ConflictPointer,
    DistilledProfileView,
    DistilledTrait,
    InsufficientBlock,
    RawItemRef,
)
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    ProfileItem,
    ProfileSection,
)
from persona.modules.relationship import RelationshipService
from persona.orchestrator.assembly import (
    PersonaProjectionCache,
    StackOptions,
    build_business_stack,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_p83b"


# ==========================================================================
# 构件
# ==========================================================================


class ScriptedProvider:
    """写死回复的离线 Provider，记录最后一次 messages。"""

    def __init__(self, text: str = "（模拟）我听见了。") -> None:
        self.text = text
        self.last_messages: list = []

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        return Ok(ChatResponse(
            text=self.text, finish_reason="stop", model_id="scripted",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class FakeProfiles:
    """只读桩：可替换当前档案（用于模拟会话中途 revise 出新版本）。"""

    def __init__(self, profile) -> None:
        self.current = profile
        self.calls: list[str] = []

    def get_persona(self, ctx, entity_id, version=None):
        self.calls.append(entity_id)
        if self.current is None:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY, "无档案"))
        return Ok(self.current)


class FakeEvidence:
    """只读桩：dict 形状证据 + 可注入 lookup 异常 / 证据不存在。"""

    def __init__(self, rows=None, boom=()) -> None:
        self.rows = dict(rows or {})
        self.boom = set(boom)
        self.calls: list[str] = []

    def get_evidence(self, ctx, evidence_id):
        self.calls.append(evidence_id)
        if evidence_id in self.boom:
            raise RuntimeError("storage I/O failure (test-only)")
        if evidence_id not in self.rows:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY,
                                 f"找不到证据 {evidence_id}"))
        return Ok(self.rows[evidence_id])


class StubProjectionPort:
    """Dialogue 的只读投影端口桩（记录调用，可注入降级/异常）。"""

    def __init__(self, mapping=None, degraded=None, raiser=None) -> None:
        self.mapping = dict(mapping or {})
        self.degraded = dict(degraded or {})
        self.raiser = raiser
        self.calls: list[tuple[str, int]] = []

    def get(self, entity_id, profile_version=None):
        self.calls.append((entity_id, profile_version))
        if self.raiser is not None:
            raise self.raiser
        return self.mapping.get(entity_id)

    def degraded_for(self, entity_id):
        return self.degraded.get(entity_id)


def _trait(layer, item_id, text, stability, *, refs=(), comp=None,
           unresolved=0, sources=(), ratio=None, span=None, note="",
           tentative=None) -> DistilledTrait:
    t = DistilledTrait(
        layer=layer, item_id=item_id, text=text, stability=stability,
        tentative=(layer == "B" if tentative is None else tentative),
        evidence_refs=list(refs), source_ids=list(sources),
        evidence_count=len(refs), tier_composition=dict(comp or {}),
        third_party_ratio=ratio, time_span=span, uncertainty_note=note,
        unresolved_evidence_count=unresolved)
    t.source_count = len(sources)      # distill.py 的运行时字段
    return t


def _view(entity_id="pe_a", version=1, *, stable=(), tentative=(),
          block=None, conflicts=(), basis=None) -> DistilledProfileView:
    return DistilledProfileView(
        entity_id=entity_id, profile_version=version,
        stable_traits=list(stable), tentative_patterns=list(tentative),
        insufficient=block or InsufficientBlock(profile_status="OK"),
        confidence_basis=basis or ConfidenceBasis(),
        conflicts_visible=list(conflicts))


class _FixtureCase(unittest.TestCase):
    """三个角色 + 六分区档案 + 独立来源证据（真实 SQLite）。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-p83b-")
        self.storage = SqliteStorage(Path(self.tmp) / "p83b.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.evidence = EvidenceService(self.storage)
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.a, self.b, self.c = (self._entity("阿雾"),
                                  self._entity("阿灯"),
                                  self._entity("阿舟"))
        # 每个角色各自独立的证据（不同 source ⇒ 来源独立性可判定）
        ev = {k: self._evidence(k, f"{k} 的资料", who)
              for k, who in (("a1", self.a), ("a2", self.a),
                             ("a3", self.a), ("a4", self.a),
                             ("a5", self.a), ("b1", self.b),
                             ("b2", self.b), ("c1", self.c))}
        self.ev = ev
        unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=self.a, status=ProfileStatus.OK,
            background=ProfileSection(text="雾港出身的青年。",
                                      evidence_refs=[ev["a1"]]),
            traits=[
                ProfileItem(item_id="t1", text="谨慎寡言",
                            stability=Stability.PATTERN,
                            evidence_refs=[ev["a1"], ev["a2"]]),
                ProfileItem(item_id="t2", text="偶尔失言",
                            stability=Stability.REPEATED,
                            evidence_refs=[ev["a3"]]),
            ],
            habits=[ProfileItem(item_id="h1", text="遇冲突先观察",
                                stability=Stability.REPEATED,
                                evidence_refs=[ev["a4"]])],
            single_events=[ProfileItem(item_id="s1", text="某次失态",
                                       stability=Stability.SINGLE,
                                       evidence_refs=[ev["a5"]])],
            expression_style=ProfileSection(text="说话总带着半句留白",
                                            evidence_refs=[ev["a4"]]),
            applicable_variant_ids=[self.a],
            open_questions=["群体互动不适用"])))
        unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=self.b, status=ProfileStatus.OK,
            background=ProfileSection(text="活泼的渔家少女。",
                                      evidence_refs=[ev["b1"]]),
            traits=[ProfileItem(item_id="tb1", text="爽朗直率",
                                stability=Stability.PATTERN,
                                evidence_refs=[ev["b1"], ev["b2"]])],
            applicable_variant_ids=[self.b])))
        unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=self.c, status=ProfileStatus.OK,
            background=ProfileSection(text="沉默的守塔人。",
                                      evidence_refs=[ev["c1"]]),
            applicable_variant_ids=[self.c])))

    def _entity(self, name: str) -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER",
             "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
             "fictional_character_meta": {"works": [],
                                          "adaptation_version": "原著",
                                          "adaptation_lineage": ["原著"],
                                          "official_canon_level": "CANON"}},
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def _evidence(self, key: str, claim: str, subject: str) -> str:
        from persona.core.ids import new_token
        src = unwrap(self.evidence.register_source(
            self.ctx, SourceDraft(title=f"《来源{key}》",
                                  canonical_key=f"k_{new_token()}"))).id
        return unwrap(self.evidence.add_evidence(self.ctx, EvidenceDraft(
            claim=claim, tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT,
            subject_entity_ids=[subject], extraction_confidence=0.9,
            source_id=src))).id

    def _service(self, port=None):
        provider = ScriptedProvider()
        deps = DialogueDeps(
            profiles=self.persona, relations=self.relationship,
            evidence=self.evidence, memory=None, storage=self.storage,
            provider=provider, persona_projection=port)
        return DialogueService(deps), provider

    def _session(self, service, participants=None) -> str:
        return unwrap(service.start_session(self.ctx, SessionDraft(
            participant_entity_ids=participants or [self.a, self.b],
            persona_profile_ids=participants or [self.a, self.b],
            speaker_selection_policy=POLICY_LEAST_RECENTLY_SPOKEN))).id

    def _prompt(self, service, provider, sid, text="你好"):
        turn = unwrap(service.run_round(self.ctx, sid, text))
        return turn, "\n".join(str(m.content) for m in provider.last_messages)

    def tearDown(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ==========================================================================
# 一、会话级缓存（T-J5 / T-J6 + 版本失效 / 隔离 / 降级 / 复用 policy）
# ==========================================================================


class TestProjectionCache(_FixtureCase):

    def _cache(self, profiles=None, evidence=None, *, project_id=PROJECT):
        return PersonaProjectionCache(
            ctx=new_call_context(project_id=project_id, actor="tests"),
            profiles=profiles or FakeProfiles(
                unwrap(self.persona.get_persona(self.ctx, self.a))),
            evidence=evidence or FakeEvidence(),
            relationships=None)

    def test_t_j5_per_entity_cache_isolation(self):
        """T-J5：三个角色各自一份投影，互不串引。"""
        cache = PersonaProjectionCache(
            ctx=self.ctx, profiles=self.persona, evidence=self.evidence,
            relationships=self.relationship)
        cache.prime([self.a, self.b, self.c], scope="story-1")
        va, vb, vc = (cache.get(self.a), cache.get(self.b), cache.get(self.c))
        self.assertEqual((va.entity_id, vb.entity_id, vc.entity_id),
                         (self.a, self.b, self.c))
        self.assertEqual([t.text for t in va.stable_traits], ["谨慎寡言"])
        self.assertEqual([t.text for t in vb.stable_traits], ["爽朗直率"])
        self.assertEqual(vc.stable_traits, [])
        self.assertEqual(len(cache.status()["primed_entity_ids"]), 3)
        self.assertEqual(cache.status()["build_calls"], 3)
        self.assertEqual(cache.status()["mode"], "structured")

    def test_t_j6_no_reprojection_and_no_evidence_query_per_round(self):
        """T-J6：prime 一次后连续多轮，构建次数与证据查询次数都不再增长。"""
        cache = PersonaProjectionCache(
            ctx=self.ctx, profiles=self.persona, evidence=self.evidence,
            relationships=self.relationship)
        cache.prime([self.a, self.b], scope="story-1")
        before = cache.status()
        service, provider = self._service(port=cache)
        sid = self._session(service)
        for i in range(3):
            unwrap(service.run_round(self.ctx, sid, f"第 {i + 1} 轮"))
        after = cache.status()
        self.assertEqual(after["build_calls"], before["build_calls"])
        self.assertEqual(after["evidence_lookup_calls"],
                         before["evidence_lookup_calls"])
        self.assertTrue(provider.last_messages)

    def test_profile_version_change_invalidates_old_projection(self):
        """档案出新版后必须重算；旧版投影不得被继续返回。"""
        profile = unwrap(self.persona.get_persona(self.ctx, self.a))
        profiles = FakeProfiles(profile)
        ev = FakeEvidence()
        cache = PersonaProjectionCache(ctx=self.ctx, profiles=profiles,
                                       evidence=ev)
        cache.prime([self.a], scope="s1")
        v1 = cache.get(self.a, profile.version)
        self.assertEqual(v1.profile_version, profile.version)
        self.assertEqual(cache.build_calls, 1)

        # 会话中途档案 revise 出 v2，且内容变化
        new_item = ProfileItem(item_id="t9", text="新版才有的特质",
                               stability=Stability.PATTERN,
                               evidence_refs=[self.ev["a1"]])
        profiles.current = replace(profile, version=profile.version + 1,
                                   traits=[new_item])
        v2 = cache.get(self.a, profile.version + 1)
        self.assertEqual(v2.profile_version, profile.version + 1)
        self.assertEqual([t.text for t in v2.stable_traits], ["新版才有的特质"])
        self.assertEqual(cache.build_calls, 2)          # 恰好重算一次
        # 消费方仍拿旧版本时：如实降级，绝不把 v2 投影冒充 v1（反之亦然）
        self.assertIsNone(cache.get(self.a, profile.version))
        self.assertIn(self.a, cache.status()["degraded"])

    def test_cross_scope_and_project_no_cache_pollution(self):
        """不同 Story / 会话 / project 不共享缓存；同实例换 scope 会清空。"""
        pa = unwrap(self.persona.get_persona(self.ctx, self.a))
        pb = unwrap(self.persona.get_persona(self.ctx, self.b))
        c1 = PersonaProjectionCache(ctx=self.ctx,
                                    profiles=FakeProfiles(pa),
                                    evidence=FakeEvidence())
        c2 = PersonaProjectionCache(
            ctx=new_call_context(project_id="prj_other", actor="tests"),
            profiles=FakeProfiles(pb), evidence=FakeEvidence())
        c1.prime([self.a], scope="story-A")
        c2.prime([self.a], scope="story-B")      # 同 entity_id、不同档案
        self.assertEqual([t.text for t in c1.get(self.a).stable_traits],
                         ["谨慎寡言"])
        self.assertEqual([t.text for t in c2.get(self.a).stable_traits],
                         ["爽朗直率"])
        self.assertEqual([t.text for t in c1.get(self.a).stable_traits],
                         ["谨慎寡言"])
        # 同一实例换会话：旧条目清空，不残留
        c1.prime([self.b], scope="story-C")
        self.assertEqual(c1.status()["primed_entity_ids"], [self.b])
        self.assertIsNone(c1.get(self.a))

    def test_degradation_is_explicit_and_not_retried(self):
        """构建失败 → 明确降级；每轮不再重试，聊天不被阻塞。"""
        cache = PersonaProjectionCache(ctx=self.ctx,
                                       profiles=FakeProfiles(None),
                                       evidence=FakeEvidence())
        cache.prime([self.a], scope="s1")
        status = cache.status()
        self.assertEqual(status["mode"], "degraded")
        self.assertIn(self.a, status["degraded"])
        self.assertIsNone(cache.get(self.a, 1))
        self.assertEqual(cache.build_calls, 1)   # 降级后不重试
        self.assertIsNone(cache.policy_for(self.a))
        self.assertIn("降级", cache.degraded_for(self.a))

    def test_missing_vs_failed_evidence_is_distinguishable(self):
        """lookup 异常与证据不存在在投影里分列，且不伪造统计。"""
        profile = unwrap(self.persona.get_persona(self.ctx, self.a))
        profiles = FakeProfiles(profile)
        ev = FakeEvidence(rows={self.ev["a1"]: {
            "tier": "VERBATIM", "source_id": "src_1",
            "namespace": "PUBLIC_SOURCE", "conflict_ids": []}},
            boom={self.ev["a2"]})                # a2 = 查询失败
        cache = PersonaProjectionCache(ctx=self.ctx, profiles=profiles,
                                       evidence=ev)
        cache.prime([self.a], scope="s1")
        view = cache.get(self.a)
        basis = view.confidence_basis
        self.assertIn(self.ev["a2"], basis.failed_evidence_ids)
        self.assertNotIn(self.ev["a2"], basis.missing_evidence_ids)
        self.assertIn(self.ev["a3"], basis.missing_evidence_ids)
        self.assertEqual(view.stable_traits[0].unresolved_evidence_count, 1)

    def test_behavior_policy_reused_and_version_bound(self):
        """复用既有 build_behavior_policy，且绑定同一档案版本。"""
        cache = PersonaProjectionCache(
            ctx=self.ctx, profiles=self.persona, evidence=self.evidence,
            relationships=self.relationship)
        cache.prime([self.a], scope="s1")
        proj = cache.get(self.a)
        policy = cache.policy_for(self.a)
        self.assertIsNotNone(policy)
        self.assertEqual(policy.profile_version, proj.profile_version)
        self.assertEqual(policy.entity_id, self.a)


# ==========================================================================
# 二、Dialogue 结构化消费（T-J1~T-J4 / T-J7 + 八字段 / 强度判定）
# ==========================================================================


class TestDialogueProjectionConsumption(_FixtureCase):

    def _structured_view(self):
        """A/B/C 齐全 + 冲突 + 缺失/失败 + 时间跨度 + 第三方占比。"""
        stable = _trait(
            "A", "t1", "谨慎寡言", "PATTERN",
            refs=[self.ev["a1"], self.ev["a2"]],
            comp={"VERBATIM": 1, "THIRD_PARTY": 1},
            sources=["src_1", "src_2"], ratio=0.5,
            span=("1905-01-01", "1921-06-01"))
        tentative = _trait(
            "B", "t2", "偶尔失言", "REPEATED",
            refs=[self.ev["a3"]], comp={"VERBATIM": 1},
            sources=["src_3"], ratio=0.0)
        habit = _trait("B", "h1", "遇冲突先观察", "REPEATED",
                       refs=[self.ev["a4"]], comp={"VERBATIM": 1},
                       sources=["src_4"])
        block = InsufficientBlock(
            profile_status="OK",
            single_events=[RawItemRef("s1", "某次失态",
                                      [self.ev["a5"]], "SINGLE")],
            open_questions=["群体互动不适用"],
            anomalous_traits=[RawItemRef("tx", "异常登记项",
                                         [self.ev["a3"]], "UNKNOWN")])
        conflicts = [ConflictPointer("t1", "A", self.ev["a2"],
                                     ["cnf_001"])]
        basis = ConfidenceBasis(
            resolved_evidence_count=4,
            missing_evidence_ids=[self.ev["a5"]],
            failed_evidence_ids=["evd_boom"],
            tier_totals={"VERBATIM": 3, "THIRD_PARTY": 1},
            third_party_ratio=0.25)
        return _view(self.a, stable=[stable],
                     tentative=[tentative, habit], block=block,
                     conflicts=conflicts, basis=basis)

    def _port(self, view_a, view_b=None) -> StubProjectionPort:
        """两个参与者都给投影，避免"另一角色正常降级"干扰全文断言。"""
        if view_b is None:
            view_b = _view(self.b, block=InsufficientBlock(profile_status="OK"))
        return StubProjectionPort({self.a: view_a, self.b: view_b})

    def test_t_j1_pattern_shows_stable_layer_and_evidence_strength(self):
        """T-J1：PATTERN 进 A 层，构成/来源/时间/占比都参与标注。"""
        port = self._port(self._structured_view())
        service, provider = self._service(port=port)
        sid = self._session(service)
        _, system = self._prompt(service, provider, sid)
        self.assertIn(LABEL_LAYER_A, system)
        self.assertIn("谨慎寡言", system)
        self.assertIn("稳定性 PATTERN", system)
        self.assertIn("证据构成 THIRD_PARTY:1、VERBATIM:1", system)
        self.assertIn("独立来源数 2", system)
        self.assertIn("1905-01-01~1921-06-01", system)
        self.assertIn("证据充分", system)
        self.assertIn(self.ev["a1"], system)        # evidence_refs 可追溯
        # 端口按本轮档案版本取，且每角色一次
        self.assertEqual(len(port.calls), 2)
        self.assertIsInstance(port.calls[0][1], int)

    def test_t_j2_repeated_uses_distinct_tentative_label(self):
        """T-J2：REPEATED / habits 用暂定标签，绝不出现旧「稳定特质」措辞。"""
        port = self._port(self._structured_view())
        service, provider = self._service(port=port)
        sid = self._session(service)
        turn, system = self._prompt(service, provider, sid)
        self.assertIn(LABEL_LAYER_B, system)
        self.assertIn("暂定 tentative", system)
        self.assertIn("偶尔失言", system)
        self.assertIn("遇冲突先观察", system)
        self.assertNotIn(LABEL_TRAITS, system)      # 旧静态标签不再并行生效
        self.assertIn("需核验", system)
        self.assertIn(FLAG_PROJECTION_NEEDS_VERIFICATION, turn.safety_flags)

    def test_t_j3_single_event_stays_in_layer_c(self):
        """T-J3：SINGLE / 异常 trait 只在 C 层，不混入 A/B 稳定描述。"""
        port = self._port(self._structured_view())
        service, provider = self._service(port=port)
        sid = self._session(service)
        _, system = self._prompt(service, provider, sid)
        idx_a, idx_b, idx_c = (system.index(LABEL_LAYER_A),
                               system.index(LABEL_LAYER_B),
                               system.index(LABEL_LAYER_C))
        self.assertLess(idx_a, idx_b)
        self.assertLess(idx_b, idx_c)
        self.assertGreater(system.index("某次失态"), idx_c)
        self.assertGreater(system.index("异常登记项"), idx_c)
        self.assertIn("不得作为稳定人格特征", system)
        self.assertIn("群体互动不适用", system)
        # B 层标签之后、C 层之前不得出现单次行为文本
        self.assertNotIn("某次失态", system[idx_b:idx_c])

    def test_t_j4_conflicts_are_kept_not_dropped(self):
        """T-J4：反例引用保留，特征不因冲突被删，且打 safety flag。"""
        port = self._port(self._structured_view())
        service, provider = self._service(port=port)
        sid = self._session(service)
        turn, system = self._prompt(service, provider, sid)
        self.assertIn("冲突证据", system)
        self.assertIn("cnf_001", system)
        self.assertIn(self.ev["a2"], system)
        self.assertIn("谨慎寡言", system)           # 特征没被抹掉
        self.assertIn(FLAG_PROJECTION_CONFLICT, turn.safety_flags)

    def test_missing_and_failed_are_worded_differently(self):
        """证据不存在 vs 查询失败措辞不同，且 unresolved 参与强度判定。"""
        port = self._port(self._structured_view())
        service, provider = self._service(port=port)
        sid = self._session(service)
        turn, system = self._prompt(service, provider, sid)
        self.assertIn("证据不存在（无法核验）", system)
        self.assertIn("证据查询失败（lookup 异常，**不是**证据缺失）", system)
        self.assertIn("evd_boom", system)
        self.assertIn(FLAG_PROJECTION_UNRESOLVED, turn.safety_flags)

    def test_pattern_with_weak_composition_needs_verification(self):
        """特别要求 3/4：只登记 PATTERN 不能声称「多份资料支持」。"""
        weak = _trait("A", "w1", "单证据的 PATTERN", "PATTERN",
                      refs=["evd_one"], comp={"VERBATIM": 1},
                      sources=["src_only"], unresolved=1,
                      ratio=0.0)
        port = self._port(_view(self.a, stable=[weak]))
        service, provider = self._service(port=port)
        sid = self._session(service)
        turn, system = self._prompt(service, provider, sid)
        self.assertIn("状态：需核验", system)
        self.assertNotIn("多份独立资料支持", system)
        self.assertNotIn("多份资料支持，可作为一贯作风陈述", system)
        self.assertIn("不可核 1", system)
        self.assertIn(FLAG_PROJECTION_NEEDS_VERIFICATION, turn.safety_flags)

    def test_t_j7_degradation_no_port_is_byte_identical(self):
        """T-J7(a)：端口未注入 → 沿用原有静态桶路径，无任何投影字样。"""
        service, provider = self._service(port=None)
        sid = self._session(service)
        _, system = self._prompt(service, provider, sid)
        self.assertIn(LABEL_TRAITS, system)
        self.assertIn("单次行为", system)
        self.assertNotIn("人格投影", system)
        self.assertNotIn(LABEL_LAYER_A, system)

    def test_t_j7_degradation_with_reason_is_explicit(self):
        """T-J7(b)：端口在但投影不可用 → 明确降级、聊天照常成功。"""
        port = StubProjectionPort(
            mapping={}, degraded={self.a: "档案读取失败，人格投影降级"})
        service, provider = self._service(port=port)
        sid = self._session(service)
        turn, system = self._prompt(service, provider, sid)
        self.assertTrue(turn.content)
        self.assertIn(LABEL_TRAITS, system)          # 原路径仍在
        self.assertIn("未使用结构化人格投影", system)
        self.assertIn("档案读取失败，人格投影降级", system)
        self.assertNotIn(LABEL_LAYER_A, system)
        self.assertIn(FLAG_PROJECTION_DEGRADED, turn.safety_flags)

    def test_t_j7_port_exception_does_not_break_or_leak(self):
        """T-J7(c)：端口抛异常 → 降级不炸轮次，且不泄漏堆栈/内部信息。"""
        port = StubProjectionPort(raiser=RuntimeError("secret-internal-path"))
        service, provider = self._service(port=port)
        sid = self._session(service)
        turn, system = self._prompt(service, provider, sid)
        self.assertTrue(turn.content)
        self.assertIn("人格投影端口异常（RuntimeError）", system)
        self.assertIn(FLAG_PROJECTION_DEGRADED, turn.safety_flags)
        self.assertNotIn("secret-internal-path", system)
        self.assertNotIn("Traceback", system)

    def test_eight_fields_all_consumed(self):
        """八个结构化字段逐一在上下文里有对应输出（覆盖表钉子）。"""
        port = self._port(self._structured_view())
        service, provider = self._service(port=port)
        sid = self._session(service)
        _, system = self._prompt(service, provider, sid)
        expectations = {
            "stable_traits": LABEL_LAYER_A,
            "tentative_patterns": LABEL_LAYER_B,
            "insufficient": LABEL_LAYER_C,
            "tier_composition": "证据构成",
            "evidence_refs": f"（证据 {self.ev['a1']}",
            "conflicts_visible": "cnf_001",
            "unresolved_evidence_count": "不可核",
            "failed_evidence_ids": "evd_boom",
        }
        for field, marker in expectations.items():
            with self.subTest(field=field):
                self.assertIn(marker, system)

    def test_t_j5_dialogue_multi_character_isolation(self):
        """T-J5：群聊 3 角色各取自己的投影，A 层文本不串到别人块里。"""
        va = self._structured_view()
        vb = _view(self.b, stable=[_trait(
            "A", "tb1", "爽朗直率", "PATTERN",
            refs=[self.ev["b1"], self.ev["b2"]],
            comp={"VERBATIM": 2}, sources=["sb1", "sb2"])])
        vc = _view(self.c, block=InsufficientBlock(
            profile_status="OK",
            single_events=[RawItemRef("sc1", "守塔时的一次犹豫",
                                      [self.ev["c1"]], "SINGLE")]))
        port = StubProjectionPort({self.a: va, self.b: vb, self.c: vc})
        service, provider = self._service(port=port)
        sid = self._session(service, [self.a, self.b, self.c])
        _, system = self._prompt(service, provider, sid)
        # 三份档案块按参与者顺序出现，各自的独有文本都在
        self.assertIn("谨慎寡言", system)
        self.assertIn("爽朗直率", system)
        self.assertIn("守塔时的一次犹豫", system)
        self.assertEqual([e for e, _ in port.calls],
                         [self.a, self.b, self.c])


# ==========================================================================
# 三、装配开关 + 真实生产链路（不经 stub）
# ==========================================================================


class TestAssemblyWiring(unittest.TestCase):

    def test_switch_defaults_off(self):
        self.assertFalse(StackOptions().enable_persona_projection)

    def test_enabled_stack_injects_the_same_cache_into_dialogue(self):
        tmp = tempfile.mkdtemp(prefix="gps-p83b-asm-")
        try:
            storage = SqliteStorage(Path(tmp) / "asm.db")
            ctx = new_call_context(project_id="prj_asm", actor="tests")
            built = build_business_stack(
                test_settings(), ctx, storage=storage,
                options=StackOptions(enable_persona_projection=True))
            stack = unwrap(built)
            self.assertIsInstance(stack.persona_projections,
                                  PersonaProjectionCache)
            self.assertIs(stack.dialogue.deps.persona_projection,
                          stack.persona_projections)
            self.assertEqual(
                stack.describe()["persona_projection"]["mode"], "degraded")
            storage.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_disabled_stack_keeps_dialogue_unwired(self):
        tmp = tempfile.mkdtemp(prefix="gps-p83b-asm-")
        try:
            storage = SqliteStorage(Path(tmp) / "asm.db")
            ctx = new_call_context(project_id="prj_asm", actor="tests")
            stack = unwrap(build_business_stack(
                test_settings(), ctx, storage=storage))
            self.assertIsNone(stack.persona_projections)
            self.assertIsNone(stack.dialogue.deps.persona_projection)
            self.assertEqual(
                stack.describe()["persona_projection"], {"mode": "disabled"})
            storage.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestProductionChain(_FixtureCase):
    """端到端：`build_business_stack(开关开)` → prime → Dialogue 真的消费投影。

    这是 P8.3-B 唯一一条不经过 stub 投影端口的测试，用来证明产品装配
    路径（CLI `_cmd_chat` 用的就是这条）不是只在测试里接上了线。
    """

    def test_enabled_stack_end_to_end_consumes_real_projection(self):
        provider = ScriptedProvider()
        stack = unwrap(build_business_stack(
            test_settings(), self.ctx, storage=self.storage,
            options=StackOptions(enable_persona_projection=True),
            dialogue_provider=provider))
        cache = stack.persona_projections
        self.assertIsInstance(cache, PersonaProjectionCache)
        self.assertIs(stack.dialogue.deps.persona_projection, cache)
        cache.prime([self.a, self.b], scope="chat-e2e")
        self.assertEqual(cache.status()["mode"], "structured")

        sid = unwrap(stack.dialogue.start_session(self.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b],
            persona_profile_ids=[self.a, self.b],
            speaker_selection_policy=POLICY_LEAST_RECENTLY_SPOKEN))).id
        before = cache.status()
        # 测试配置的"费用状态未知请求"上限为 1，装配层会拦第二次模型调用；
        # 多轮零重查已由 TestProjectionCache.test_t_j6 用真实服务覆盖 3 轮。
        unwrap(stack.dialogue.run_round(self.ctx, sid, "你好"))
        system = "\n".join(str(m.content) for m in provider.last_messages)
        self.assertIn(LABEL_LAYER_A, system)
        self.assertIn("谨慎寡言", system)
        self.assertNotIn(LABEL_TRAITS, system)     # 两个角色都走结构化路径
        after = cache.status()
        self.assertEqual(after["build_calls"], before["build_calls"])
        self.assertEqual(after["evidence_lookup_calls"],
                         before["evidence_lookup_calls"])


if __name__ == "__main__":
    unittest.main()
