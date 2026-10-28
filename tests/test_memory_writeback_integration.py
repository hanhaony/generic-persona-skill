"""P6.2-a 规则引擎 × 对话写入管道的集成测试（stdlib unittest，临时库，零外呼）。

**这一层的存在理由**：规则引擎单测全绿只能证明"派生出来的东西形状看着对"。
工程上真正会翻车的是另一件事——**派生结果能不能被既有写入管道吃下去**（字段是否
齐全、`scope` 谁来补、`namespace` 有没有被强制锁定、重复写会不会重复落库）。
所以这里全部走真实的 `DialogueService.run_round` + 真实 SQLite，
并**直接查库断言**，不只断言函数返回值。

覆盖（对应方案 §5.2）：
  1. `policy=explicit` + 规则派生的候选 → 真的写进库，隔离字段正确，轮次回填了 id；
  2. 落库内容必须是用户原话切片（verbatim）；
  3. `policy=none` + 同样的候选 → 被闸门拒绝，零记忆（D-179 不许松动）；
  4. `policy=none` + 有可记内容 → 零记忆（既有守卫的同形断言）；
  5. `policy=explicit` 但不给候选 → 零记忆（既有行为不许变）；
  6. 连续寒暄 4 轮 → 派生 0 条、写 0 条；
  7. 同一句说两遍 → **只写 1 条**（`write_simulated` dedupe 修复）；
  8. `rule` 模式不产生任何外部用量记录（证明 0 外呼）；
  9. 写入的记忆可被后续会话按关键词检索到（R2 注入的替代断言，见测试注释）。

对应文档：`docs/P6_2_MEMORY_WRITE_PLAN.md` §3 / §5.2。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    MemoryKind,
    ProvenanceNamespace,
)
from persona.core.errors import ErrorCode
from persona.core.ids import new_token
from persona.core.result import Ok, UsageDelta, is_err, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query
from persona.modules.dialogue import (
    POLICY_MEMORY_WRITE_EXPLICIT,
    POLICY_MEMORY_WRITE_NONE,
    POLICY_MEMORY_WRITE_RULE,
    DialogueDeps,
    DialogueService,
    SessionDraft,
)
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.memory import MemoryQuery, MemoryService
from persona.modules.memory_writeback import (
    RULE_VERSION,
    derive_candidates,
    derive_report,
)
from persona.modules.persona import PersonaProfileDraft, PersonaService, ProfileSection
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

PROJECT = "prj_memory_writeback"

#: 该记的（R-EPI）
MEMORABLE = "昨天我去城南看了桃花，心里高兴得很"
#: 不该记的（N2 寒暄）
CHITCHAT = "你好呀"


class ScriptedProvider:
    """确定性 Provider：零网络、零费用，只为了让 `draft_turn` 有回答可落盘。"""

    def __init__(self) -> None:
        self.chat_calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.chat_calls += 1
        # 记下本轮 system 消息，供跨会话 R2 注入断言用（只留最近一轮）。
        self.last_system = "\n".join(
            m.content for m in req.messages if m.role == "system")
        return Ok(ChatResponse(
            text="（脚本化回答）我记下了。",
            finish_reason="stop", model_id="scripted",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        from persona.core.types import UsageReport
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class WritebackFixture:
    """临时 SQLite + 两个实体 + 两个档案 + 对话服务。用完即删。"""

    def __init__(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-mem-wb-")
        self.storage = SqliteStorage(Path(self.tmp) / "writeback.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.evidence = EvidenceService(self.storage)
        self.memory = MemoryService(self.storage)
        self.provider = ScriptedProvider()

    def service(self) -> DialogueService:
        return DialogueService(DialogueDeps(
            profiles=self.persona, relations=self.relationship,
            evidence=self.evidence, memory=self.memory, storage=self.storage,
            provider=self.provider, memory_reader=self.memory))

    # ------------------------------------------------------------ 构件

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

    def profile(self, entity_id: str, background: str) -> str:
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
            applicable_variant_ids=[entity_id]))).id

    def actors(self) -> tuple[str, str]:
        a = self.entity("阿雾")
        b = self.entity("阿灯")
        self.profile(a, "阿雾：沉默寡言的灯塔看守。")
        self.profile(b, "阿灯：活泼的渔家少女。")
        return a, b

    def start(self, participants: list[str], *,
              policy: str = POLICY_MEMORY_WRITE_EXPLICIT,
              topic: str | None = "城南的桃花") -> str:
        return unwrap(self.service().start_session(self.ctx, SessionDraft(
            participant_entity_ids=list(participants), topic=topic,
            memory_write_policy=policy))).id

    # ------------------------------------------------------------ 断言辅助

    def memory_rows(self) -> list[dict]:
        q = Query(filters={"kind": "MemoryItem", "project_id": PROJECT},
                  limit=MAX_QUERY_LIMIT)
        return list(unwrap(self.storage.query(
            self.ctx, "MemoryItem", q, include_deleted=True)).items)

    @staticmethod
    def memory_kind(row: dict) -> str:
        """记录表自身的 `kind` 列（="MemoryItem"）会遮蔽载荷里的语义 kind。

        详见 `MemoryService._as_memory` 的注释——直接读 `row["kind"]` 会拿到
        `"MemoryItem"`，是个很容易误判的坑。
        """
        return str((row.get("payload") or {}).get("kind") or "")

    def turn_row(self, turn_id: str) -> dict:
        return unwrap(self.storage.get(
            self.ctx, "ConversationTurn", turn_id, include_deleted=True))

    def external_usage_count(self) -> int:
        row = self.storage.connect().execute(
            "SELECT COUNT(*) FROM external_usage_records").fetchone()
        return int(row[0])

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class WritebackIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = WritebackFixture()
        self.a, self.b = self.fx.actors()
        self.participants = ((self.a, "阿雾"), (self.b, "阿灯"))

    def tearDown(self) -> None:
        self.fx.close()

    def _round(self, sid: str, message: str, *, pass_candidates: bool = True):
        cands = derive_candidates(
            message, participants=self.participants) if pass_candidates else None
        return self.fx.service().run_round(
            self.fx.ctx, sid, user_message=message, memory_candidates=cands)


# ==========================================================================
# 1 / 2. 派生结果被管道接受 + 落库内容是原话
# ==========================================================================


class TestPipelineAcceptsDerivedCandidates(WritebackIntegrationTest):

    def test_derived_candidate_is_written_with_isolation_fields(self):
        sid = self.fx.start([self.a, self.b])
        turn = unwrap(self._round(sid, MEMORABLE))

        self.assertEqual(len(turn.generated_memory_ids), 1, "轮次没回填记忆 id")
        rows = self.fx.memory_rows()
        self.assertEqual(len(rows), 1, "库里的记忆行数不对")
        row = rows[0]
        self.assertEqual(row["namespace"], ProvenanceNamespace.SIMULATED.value)
        self.assertEqual(row["source_type"], "SIMULATED_DIALOGUE")
        self.assertEqual(row["scope"], "SESSION")
        self.assertEqual(row["scope_id"], sid)
        self.assertEqual(row["source_turn_id"], turn.id)
        self.assertEqual(self.fx.memory_kind(row), MemoryKind.EPISODIC.value)
        self.assertIn(RULE_VERSION, row["confidence_means"])

    def test_turn_row_persists_generated_memory_ids(self):
        """不只信返回值：库里那条轮次也必须记着"我生成了哪些记忆"。"""
        sid = self.fx.start([self.a, self.b])
        turn = unwrap(self._round(sid, MEMORABLE))
        row = self.fx.turn_row(turn.id)
        self.assertEqual(list(row["generated_memory_ids"]),
                         list(turn.generated_memory_ids))

    def test_stored_content_is_verbatim_slice_of_user_message(self):
        sid = self.fx.start([self.a, self.b])
        unwrap(self._round(sid, MEMORABLE + "。我一向爱吃桂花糕"))
        rows = self.fx.memory_rows()
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertIn(row["content"], MEMORABLE + "。我一向爱吃桂花糕")


# ==========================================================================
# 3 / 4 / 5. 闸门：默认零写入不许松动
# ==========================================================================


class TestGatesStayClosed(WritebackIntegrationTest):

    def test_none_policy_rejects_derived_candidates(self):
        sid = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_NONE)
        res = self._round(sid, MEMORABLE)
        self.assertTrue(is_err(res), "默认策略下居然通过了闸门")
        self.assertEqual(res.error.code, ErrorCode.VALIDATION_GATE_REJECTED)
        self.assertEqual(self.fx.memory_rows(), [], "闸门被绕过：默认策略下写了记忆")

    def test_none_policy_without_candidates_writes_nothing(self):
        """D-179 守卫的同形断言：有可记内容、但没人派生 → 一条都不写。"""
        sid = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_NONE)
        unwrap(self._round(sid, MEMORABLE, pass_candidates=False))
        self.assertEqual(self.fx.memory_rows(), [])

    def test_explicit_policy_without_candidates_writes_nothing(self):
        sid = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_EXPLICIT)
        unwrap(self._round(sid, MEMORABLE, pass_candidates=False))
        self.assertEqual(self.fx.memory_rows(), [])

    def test_chitchat_rounds_produce_no_candidates_and_no_memory(self):
        sid = self.fx.start([self.a, self.b])
        for msg in ("你好呀", "谢谢你了", "好的好的", "嗯嗯，知道了"):
            self.assertEqual(
                derive_candidates(msg, participants=self.participants), [],
                msg=f"寒暄被派生成候选：{msg!r}")
            unwrap(self._round(sid, msg))
        self.assertEqual(self.fx.memory_rows(), [])


# ==========================================================================
# 6. dedupe（write_simulated 修复）
# ==========================================================================


class TestDedupeAtWriteLayer(WritebackIntegrationTest):

    def test_same_sentence_twice_writes_one_memory(self):
        sid = self.fx.start([self.a, self.b])
        t1 = unwrap(self._round(sid, MEMORABLE))
        t2 = unwrap(self._round(sid, MEMORABLE))

        rows = self.fx.memory_rows()
        self.assertEqual(len(rows), 1, "同一句话写了两条记忆——dedupe 没生效")
        # 第二轮应指回同一条（与 write() 同口径：返回已存在的 id）
        self.assertEqual(list(t1.generated_memory_ids),
                         list(t2.generated_memory_ids))

    def test_dedupe_does_not_cross_sessions(self):
        """不同会话是不同 scope_id → 不互相去重（记忆是会话级的）。"""
        s1 = self.fx.start([self.a, self.b])
        unwrap(self._round(s1, MEMORABLE))
        s2 = self.fx.start([self.a, self.b])
        unwrap(self._round(s2, MEMORABLE))
        self.assertEqual(len(self.fx.memory_rows()), 2)


# ==========================================================================
# 7 / 8. 零外呼 + 可检索
# ==========================================================================


class TestNoExternalCallsAndRetrievable(WritebackIntegrationTest):

    def test_rule_derivation_adds_no_external_usage(self):
        """规则引擎是纯函数：整条写入流程不该出现任何外部用量记录。"""
        sid = self.fx.start([self.a, self.b])
        before = self.fx.external_usage_count()
        unwrap(self._round(sid, MEMORABLE))
        self.assertEqual(self.fx.external_usage_count(), before)

    def test_written_memory_is_retrievable_by_keyword(self):
        """替代断言：跨会话 R2 注入依赖会话 topic 命中，属 P6.2-b 的端到端范围；
        这里只钉死"写进去的东西检索得到、且带 SIMULATED 隔离标记"。"""
        sid = self.fx.start([self.a, self.b])
        unwrap(self._round(sid, MEMORABLE))
        page = unwrap(self.fx.memory.search(
            self.fx.ctx, MemoryQuery(text="桃花", namespaces_include=None)))
        self.assertGreaterEqual(len(page.items), 1)
        self.assertTrue(all(i.namespace == ProvenanceNamespace.SIMULATED.value
                            for i in page.items))


class TestSelfNameCrossSession(WritebackIntegrationTest):
    """P0 第二阶段「渐进式熟人社会」：自述称呼跨会话持久化。

    会话 A（`rule` 策略、产品路径：run_round 内部自动派生）说「你可以叫我小明」
    → 记忆落库；会话 B（**同库新会话**、带可命中 topic）从记忆库检索到这条
    记忆并在 R2 注入后出现在 system prompt 里——熟识称呼从此跨会话延续。
    """

    def test_self_name_written_by_rule_policy_and_read_back(self):
        # 会话 A：rule 策略 + 不传候选（走 run_round 内部的 derive_report 自动派生）
        sid_a = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_RULE,
                              topic=None)
        unwrap(self._round(sid_a, "你可以叫我小明，这样亲近些",
                           pass_candidates=False))
        rows = self.fx.memory_rows()
        self.assertEqual(len(rows), 1, "rule 策略应自动派生并写入 1 条")
        self.assertIn("叫我小明", str(rows[0]["payload"]))
        self.assertEqual(self.fx.memory_kind(rows[0]),
                         MemoryKind.SEMANTIC.value)

        # 会话 B：同库新会话（同一 SQLite 文件、不同 session 行 = 跨会话），
        # topic 用称呼本体（2 字以上、非停用词 → 门禁放行）
        sid_b = self.fx.start([self.a, self.b], topic="小明")
        self.assertNotEqual(sid_a, sid_b)
        page = unwrap(self.fx.memory.search(
            self.fx.ctx, MemoryQuery(text="小明", namespaces_include=None)))
        self.assertGreaterEqual(len(page.items), 1, "会话 B 检索不到称呼记忆")
        self.assertTrue(all(i.namespace == ProvenanceNamespace.SIMULATED.value
                            for i in page.items))

    def test_self_name_memory_is_injected_into_next_session_prompt(self):
        """端到端：会话 B 跑一轮，R2 注入后称呼记忆真的进了 system prompt。"""
        sid_a = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_RULE,
                              topic=None)
        unwrap(self._round(sid_a, "你可以叫我小明，这样亲近些",
                           pass_candidates=False))
        sid_b = self.fx.start([self.a, self.b], topic="小明")
        unwrap(self._round(sid_b, "今日来访，想与你叙叙旧",
                           pass_candidates=False))
        system = getattr(self.fx.provider, "last_system", "")
        self.assertIn("叫我小明", system,
                      "R2 注入后称呼记忆必须出现在会话 B 的 system prompt 里")


# ==========================================================================
# 9. 报告轨迹可用于审计（不落内容）
# ==========================================================================


class TestDeriveReportForAudit(WritebackIntegrationTest):

    def test_rejection_trail_has_no_content(self):
        """审计里只能有代码与位置；敏感信息尤其不能进审计。"""
        r = derive_report("记住我的电话是13812345678", participants=self.participants)
        self.assertEqual(r.candidates, ())
        dumped = repr([(x.code, x.sentence_index, x.rule) for x in r.rejections])
        self.assertNotIn("13812345678", dumped)
        self.assertIn("N5_SENSITIVE", dumped)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
