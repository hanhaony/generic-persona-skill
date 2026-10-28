"""P8.0 一轮多响应 + P8.1 自主群聊（stdlib unittest，临时库，零外呼）。

**这一层钉住的三件事**：

1. **P8.0 后答者看得见先答者**：同会话先 `run_round(msg)` 再 `run_round(None)`，
   第二位发言人的上下文里必须有先答者的**显示名**与原话（transcript 显示名标注）；
2. **P8.1 在场众人**：多人同场时 system prompt 注入【在场众人】块——
   在场者名单 + 最近一条他人发言 + 互动许可；单人会话绝不注入；
3. **红线：角色互聊绝不进长期记忆**——纯 `None` 轮（无用户消息）在 `rule`
   策略下也**零记忆写入**（`run_round` 只从用户消息派生候选，物理上拿不到
   角色互聊的内容）。这是回声室防线：角色互聊只活在本会话 transcript 里。

对应方案：P8.0/P8.1（2026-10-28 拍板），prompt 版本 `p8-group-2026-10-28a`。
"""

from __future__ import annotations

import io
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    ProvenanceNamespace,
)
from persona.core.errors import ErrorCode
from persona.core.ids import new_token
from persona.core.result import Ok, UsageDelta, is_err, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query
from persona.modules.dialogue import (
    POLICY_MEMORY_WRITE_RULE,
    DialogueDeps,
    DialogueService,
    SessionDraft,
)
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.memory import MemoryQuery, MemoryService
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

PROJECT = "prj_p8_group_chat"


class RecordingProvider:
    """确定性 Provider：零网络、零费用；**记录每轮 system/user 消息**供断言。"""

    def __init__(self) -> None:
        self.chat_calls = 0
        self.last_system = ""
        self.last_user = ""

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.chat_calls += 1
        self.last_system = "\n".join(
            m.content for m in req.messages if m.role == "system")
        self.last_user = "\n".join(
            m.content for m in req.messages if m.role == "user")
        return Ok(ChatResponse(
            text="（脚本化回答）妹妹这话极是。",
            finish_reason="stop", model_id="scripted",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        from persona.core.types import UsageReport
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class GroupFixture:
    """临时 SQLite + 两个实体/档案 + 对话服务。用完即删。"""

    def __init__(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-p8-group-")
        self.storage = SqliteStorage(Path(self.tmp) / "group.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.evidence = EvidenceService(self.storage)
        self.memory = MemoryService(self.storage)
        self.provider = RecordingProvider()
        self.service = DialogueService(DialogueDeps(
            profiles=self.persona, relations=self.relationship,
            evidence=self.evidence, memory=self.memory, storage=self.storage,
            provider=self.provider, memory_reader=self.memory))

    def entity(self, name: str) -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {
                "display_name": name, "entity_type": "FICTIONAL_CHARACTER",
                "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
                "fictional_character_meta": {
                    "works": [{"work_id": new_token("wrk_"),
                               "title": "《大观园四季》", "medium": "ANIMATION"}],
                    "adaptation_version": "测试改编版",
                    "adaptation_lineage": ["原作", "测试改编版"],
                    "official_canon_level": "CANON",
                },
            },
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def profile(self, entity_id: str, background: str) -> str:
        src = unwrap(self.evidence.register_source(
            self.ctx, SourceDraft(title="《大观园四季》访谈录",
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
        a = self.entity("林黛玉")
        b = self.entity("贾宝玉")
        self.profile(a, "林黛玉：多愁善感的潇湘妃子。")
        self.profile(b, "贾宝玉：痴情的怡红公子。")
        return a, b

    def start(self, participants: list[str], *,
              policy: str = POLICY_MEMORY_WRITE_RULE) -> str:
        return unwrap(self.service.start_session(
            self.ctx, SessionDraft(
                participant_entity_ids=list(participants),
                persona_profile_ids=list(participants),
                memory_write_policy=policy))).id

    def memory_rows(self) -> list[dict]:
        q = Query(filters={"kind": "MemoryItem", "project_id": PROJECT},
                  limit=MAX_QUERY_LIMIT)
        return list(unwrap(self.storage.query(
            self.ctx, "MemoryItem", q, include_deleted=True)).items)

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class GroupChatTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = GroupFixture()
        self.a, self.b = self.fx.actors()

    def tearDown(self) -> None:
        self.fx.close()

    # ---- P8.0：后答者看得见先答者 ------------------------------------

    def test_second_speaker_sees_first_speaker_by_display_name(self):
        """P8.0 核心：先答者发言后，后答者的 prompt 里有先答者显示名与原话。"""
        sid = self.fx.start([self.a, self.b])
        first = unwrap(self.fx.service.run_round(
            self.fx.ctx, sid, "今日园子里桃花开了。"))
        self.assertEqual(first.speaker_entity_id, self.a)
        second = unwrap(self.fx.service.run_round(self.fx.ctx, sid, None))
        self.assertEqual(second.speaker_entity_id, self.b)
        user = self.fx.provider.last_user
        # transcript 用显示名标注（不再是 pe_xxx / __user__）
        self.assertIn("[林黛玉]", user)
        self.assertIn("[访客]", user)
        self.assertNotIn("[pe_", user)
        self.assertNotIn("__user__", user)
        # 先答者的原话在后答者上下文里
        self.assertIn("今日园子里桃花开了", user)

    def test_multi_party_prompt_has_in_room_block(self):
        """P8.1：多人同场注入【在场众人】——在场者 + 最近一条他人发言。"""
        sid = self.fx.start([self.a, self.b])
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, "你近来可好？"))
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, None))
        system = self.fx.provider.last_system
        self.assertIn("【在场众人】", system)
        self.assertIn("贾宝玉", system)  # 在场者名单
        self.assertIn("方才林黛玉说：", system)  # 最近一条他人发言
        self.assertIn("不得把它当成既定设定转述", system)  # 红线

    def test_single_party_context_never_gets_in_room_block(self):
        """multi_party=False（单人上下文）绝不注入在场众人块（防御性分支）。"""
        context = {
            "persona_briefs": [], "rel_briefs": [], "memory_briefs": [],
            "transcript": "", "multi_party": False, "others_in_room": [],
            "last_other_line": "", "referenced_memory_ids": [],
            "referenced_persona_item_ids": [],
            "referenced_relationship_item_ids": [],
            "safety_flags": [], "evidence_in_context": [],
        }
        msgs = self.fx.service._build_messages(
            {"id": "css_x", "participant_entity_ids": [self.a]}, self.a, context)
        self.assertNotIn("【在场众人】", msgs[0].content)

    # ---- P8.1 红线：角色互聊绝不进长期记忆 ---------------------------

    def test_pure_none_rounds_write_no_memory_even_under_rule_policy(self):
        """P8.1 红线：纯角色互聊轮（无用户消息）在 rule 策略下零记忆写入。"""
        sid = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_RULE)
        for _ in range(3):
            turn = self.fx.service.run_round(self.fx.ctx, sid, None)
            self.assertFalse(is_err(turn))
        self.assertEqual(self.fx.memory_rows(), [],
                         "角色互聊的内容绝不能进长期记忆（回声室防线）")

    def test_user_message_rounds_may_write_but_none_rounds_never(self):
        """对照：用户消息轮可以写（rule 授权），随后的互聊轮依然零新增。"""
        sid = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_RULE)
        unwrap(self.fx.service.run_round(
            self.fx.ctx, sid, "你可以叫我小明，这样亲近些"))
        self.assertEqual(len(self.fx.memory_rows()), 1)
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, None))
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, None))
        self.assertEqual(len(self.fx.memory_rows()), 1,
                         "互聊轮不得新增任何记忆")

    def test_inter_chat_memory_not_retrievable_cross_session(self):
        """跨会话视角：互聊轮写下的记忆不存在，新会话检索自然为空。"""
        sid = self.fx.start([self.a, self.b], policy=POLICY_MEMORY_WRITE_RULE)
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, None))
        page = unwrap(self.fx.memory.search(
            self.fx.ctx,
            MemoryQuery(text="脚本化", namespaces_include=None)))
        self.assertEqual(len(page.items), 0)

    # ---- 渲染：一眼分清谁在说话 --------------------------------------

    def test_chat_turn_printing_labels_speaker_and_indents(self):
        """P8.0 渲染：【角色名】+ 回答缩进；重定向（非 TTY）下无 ANSI 转义。"""
        from persona.cli.main import _print_chat_turn

        class FakeView:
            speaker_entity_id = "pe_x"
            content = "第一行回答\n第二行回答"
            referenced_evidence_ids: list[str] = []
            generated_memory_ids = ["mem_1"]

        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_chat_turn(1, "你好", FakeView(), {"pe_x": "林黛玉"})
        out = buf.getvalue()
        self.assertIn("你：你好", out)
        self.assertIn("【林黛玉】 第一行回答", out)
        self.assertIn("    第二行回答", out)
        self.assertNotIn("\x1b[", out, "非 TTY（重定向）下不得有 ANSI 转义")
        self.assertIn("记忆：mem_1", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
