"""跨世界观红线（2026-11-03 P0 全链路泛用性改造，stdlib unittest，零外呼）。

**为什么要有这一层**：此前 system prompt 的口吻示例全部写死古典腔
（阁下/客官/快递猜词），真人档案（21 世纪演员）一进来，角色张口就是
红楼梦腔——核心卖点"完美适配人物的时代与说话方式"被单一世界观击穿。

本文件钉死三条红线（回归口径：改坏任何一条即红）：

1. **静态块无世界观**：P0_IMMERSION_RULES / P0_RELATIONSHIP_STAGES /
   三档回答规则等对所有人可见的文本里，**不得出现任何具体时代的示例词**
   （古风词与现代词双向禁止）；
2. **现代档案 → 古风词零暴露**：Ben Affleck 式背景（含公元年份）
   → era_hint=MODERN，身份块走现代分支（含现代称呼示例），
   整个 system prompt **逐字不含**「阁下/客官/在下/一道去」；
3. **古典档案 → 现代词零暴露**：林黛玉式背景 → era_hint=CLASSICAL，
   整个 system prompt **逐字不含**「Hi/披萨/兄弟」。

身份块（build_identity_block）同时承担表达 DNA 注入：
expression_style 非空时必须进 prompt，且带"不要像 AI 助手一样礼貌刻板"。
对应 prompt 版本 `p8-crossuniverse-2026-11-04a`（幂等键，改 prompt 必须 bump）。
"""

from __future__ import annotations

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
from persona.core.ids import new_token
from persona.core.result import Ok, UsageDelta, unwrap
from persona.core.types import UsageReport
from persona.modules.dialogue import (
    DIALOGUE_PROMPT_VERSION,
    DialogueDeps,
    DialogueService,
    P0_IMMERSION_RULES,
    P0_RELATIONSHIP_STAGES,
    SIMULATION_NOTICE,
    SessionDraft,
    build_identity_block,
    era_hint,
)
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.memory import MemoryService
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

PROJECT = "prj_cross_worldview"

#: 静态块与现 代档案 prompt 里**双向**禁止出现的字面词。
#: 古风词 = 用户灰度实测的出戏来源；现代词 = 古典角色绝不该看到的。
ANCIENT_WORDS = ("阁下", "客官", "在下", "一道去")
MODERN_WORDS = ("Hi", "披萨", "兄弟")

BEN_BACKGROUND = (
    "Ben Affleck is an American actor and filmmaker. "
    "He was born on August 15, 1972, in Berkeley, California. "
    "He won two Academy Awards.")
BEN_EXPRESSION = (
    "Boston accent; quick-witted roast humor; loves playful banter "
    "and cursing with his close friends.")

DAIYU_BACKGROUND = (
    "林黛玉，中国古典名著《红楼梦》中的人物，金陵人氏，寄居贾府潇湘馆，"
    "多愁善感，才情冠绝大观园。")


class RecordingProvider:
    """确定性 Provider：零网络、零费用；记录每轮 system 消息供断言。"""

    def __init__(self) -> None:
        self.last_system = ""

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_system = "\n".join(
            m.content for m in req.messages if m.role == "system")
        return Ok(ChatResponse(
            text="（脚本化回答）你猜怎么着？",
            finish_reason="stop", model_id="scripted",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class WorldviewFixture:
    """临时 SQLite + 实体/档案 + 对话服务。用完即删。"""

    def __init__(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-xworld-")
        self.storage = SqliteStorage(Path(self.tmp) / "xworld.db")
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

    def entity(self, name: str, entity_type: str) -> str:
        row: dict = {
            "display_name": name, "entity_type": entity_type,
            "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
        }
        if entity_type == "FICTIONAL_CHARACTER":
            row["fictional_character_meta"] = {
                "works": [{"work_id": new_token("wrk_"),
                           "title": "《测试作品》", "medium": "NOVEL"}],
                "adaptation_version": "测试改编版",
                "adaptation_lineage": ["原作", "测试改编版"],
                "official_canon_level": "CANON",
            }
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity", row,
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def profile(self, entity_id: str, background: str,
                expression_style: str = "", *, fictional: bool = True) -> str:
        src = unwrap(self.evidence.register_source(
            self.ctx, SourceDraft(title="测试资料",
                                  canonical_key=f"key_{new_token()}"))).id
        ref = unwrap(self.evidence.add_evidence(self.ctx, EvidenceDraft(
            claim=f"{entity_id} 的背景：{background}",
            tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.BACKGROUND,
            subject_entity_ids=[entity_id],
            extraction_confidence=0.9, source_id=src))).id
        # D-115：虚构角色必须声明适用版本且含自身；真人必须为空。
        return unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8,
                                      evidence_refs=[ref]),
            expression_style=ProfileSection(text=expression_style,
                                            confidence=0.8,
                                            evidence_refs=[ref]),
            applicable_variant_ids=([entity_id] if fictional else [])))).id

    def start(self, participants: list[str]) -> str:
        return unwrap(self.service.start_session(
            self.ctx, SessionDraft(
                participant_entity_ids=list(participants),
                persona_profile_ids=list(participants)))).id

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class StaticBlockWorldviewTest(unittest.TestCase):
    """红线 1：静态共享块不得携带任何具体时代的示例词。"""

    def test_static_blocks_never_leak_worldview_words(self):
        for name, block in (("SIMULATION_NOTICE", SIMULATION_NOTICE),
                            ("P0_IMMERSION_RULES", P0_IMMERSION_RULES),
                            ("P0_RELATIONSHIP_STAGES",
                             P0_RELATIONSHIP_STAGES)):
            for word in ANCIENT_WORDS + MODERN_WORDS:
                self.assertNotIn(
                    word, block,
                    f"静态块 {name} 出现时代字面词 {word!r}——"
                    f"静态块对所有人可见，写死任何一方的口吻都是污染")


class EraHintTest(unittest.TestCase):
    """era_hint：确定性关键词启发式（只选示例套，不注入事实）。"""

    def test_modern_signals(self):
        self.assertEqual(
            era_hint("born on August 15, 1972, in California"),
            "MODERN")
        self.assertEqual(era_hint("21 世纪的好莱坞演员"), "MODERN")
        self.assertEqual(era_hint("当代作家"), "MODERN")

    def test_classical_signals(self):
        self.assertEqual(
            era_hint("林黛玉，古典名著《红楼梦》里寄居贾府潇湘馆的姑娘"),
            "CLASSICAL")
        self.assertEqual(era_hint("明朝宫廷画师"), "CLASSICAL")
        self.assertEqual(era_hint("江南书生进京赶考"), "CLASSICAL")

    def test_unknown_falls_back_to_neutral(self):
        self.assertEqual(era_hint("雾港出身的青年。"), "UNKNOWN")
        self.assertEqual(era_hint(""), "UNKNOWN")


class IdentityBlockTest(unittest.TestCase):
    """build_identity_block：分支互斥 + 表达 DNA 注入。"""

    def test_modern_branch_carries_dna_and_no_ancient_words(self):
        block = build_identity_block("Ben Affleck", BEN_BACKGROUND,
                                     BEN_EXPRESSION)
        self.assertIn("你的名字是 Ben Affleck", block)
        self.assertIn("1972", block)                       # 背景逐字注入
        self.assertIn("Boston accent", block)              # 表达 DNA 逐字注入
        self.assertIn("不要像个 AI 助手一样礼貌刻板", block)
        self.assertIn("Hi，兄弟，怎么称呼？", block)         # 现代称呼示例在场
        for word in ANCIENT_WORDS:                          # 古风词零暴露
            self.assertNotIn(word, block)

    def test_classical_branch_has_no_modern_words(self):
        block = build_identity_block("林黛玉", DAIYU_BACKGROUND, "")
        self.assertIn("古代人", block)
        self.assertIn("阁下", block)                        # 古典称呼示例在场
        for word in MODERN_WORDS:                           # 现代词零暴露
            self.assertNotIn(word, block)

    def test_unknown_branch_gives_no_literal_examples(self):
        block = build_identity_block("雾港青年", "雾港出身的青年。", "")
        self.assertIn("若你是现代人", block)
        for word in ANCIENT_WORDS + MODERN_WORDS:
            self.assertNotIn(word, block)

    def test_empty_background_does_not_invent_identity(self):
        block = build_identity_block("某人", "", "")
        self.assertIn("不得自行脑补时代与身份", block)


class CrossWorldviewPromptTest(unittest.TestCase):
    """红线 2/3：整份 system prompt 的时代词逐字断言（端到端）。"""

    def setUp(self) -> None:
        self.fx = WorldviewFixture()

    def tearDown(self) -> None:
        self.fx.close()

    def _system_after_first_round(self, name: str, entity_type: str,
                                  background: str, expression: str,
                                  companion: tuple[str, str] | None = None
                                  ) -> str:
        """建主档案（+可选同伴）→ 开会话 → 跑一轮 → 返回 system prompt。

        会话要求 ≥2 参与者（产品校验），同伴档案同样构造、同样接受断言——
        同一批人的时代必须一致，否则断言对象不明确。
        """
        eid = self.fx.entity(name, entity_type)
        self.fx.profile(eid, background, expression,
                        fictional=(entity_type == "FICTIONAL_CHARACTER"))
        ids = [eid]
        if companion:
            cid = self.fx.entity(companion[0], entity_type)
            self.fx.profile(cid, companion[1],
                            fictional=(entity_type == "FICTIONAL_CHARACTER"))
            ids.append(cid)
        sid = self.fx.start(ids)
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, "你好"))
        return self.fx.provider.last_system

    def test_modern_actor_prompt_is_ancient_word_free(self):
        """Ben Affleck 式档案：整份 prompt 不含任何古风字面词。"""
        system = self._system_after_first_round(
            "Ben Affleck", "REAL_PERSON", BEN_BACKGROUND, BEN_EXPRESSION,
            companion=("Matt Damon",
                       "Matt Damon is an American actor and screenwriter. "
                       "He was born on October 8, 1970, in Cambridge, "
                       "Massachusetts."))
        self.assertIn("【你是谁：身份与时代认知】", system)
        self.assertIn(BEN_EXPRESSION, system)               # DNA 进了 prompt
        self.assertIn("现代人", system)
        for word in ANCIENT_WORDS:
            self.assertNotIn(word, system,
                             f"现代人物的 system prompt 出现古风词 {word!r}")

    def test_classical_character_prompt_is_modern_word_free(self):
        """林黛玉式档案：整份 prompt 不含任何现代字面词。"""
        system = self._system_after_first_round(
            "林黛玉", "FICTIONAL_CHARACTER", DAIYU_BACKGROUND, "",
            companion=("贾宝玉", "贾宝玉，中国古典名著《红楼梦》中的人物，"
                       "怡红院的公子，痴情多才。"))
        self.assertIn("【你是谁：身份与时代认知】", system)
        self.assertIn("古代人", system)
        for word in MODERN_WORDS:
            self.assertNotIn(word, system,
                             f"古典人物的 system prompt 出现现代词 {word!r}")

    def test_prompt_version_bumped_for_era_block(self):
        """身份块改了 prompt 文本 ⇒ 版本必须 +1（幂等键由它参与生成）。"""
        self.assertEqual(DIALOGUE_PROMPT_VERSION, "p8-crossuniverse-2026-11-04a")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
