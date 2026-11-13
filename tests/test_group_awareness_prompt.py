"""P0 群聊体验升级：群像法则 + 防复读 + 跨宇宙认知（2026-11-04，stdlib unittest，零外呼）。

**为什么要有这一层**：用户跨世界观阵营实测（现实演员 + 两个不同作品的角色
同场）暴露"复读机群聊"——所有人用差不多长的句子互相问"你是谁/怎么称呼"，
同作品的战友也互相客套自我介绍。本文件钉死四条红线（回归口径：改坏任何
一条即红）：

1. **群像法则静态进 prompt**：性格决定话多话少（内敛简短 / 活泼话多 /
   严禁同构）+ 不复读别人问过的问题——对**所有**发言人的 system prompt
   都必须出现；
2. **跨宇宙认知判定**（`build_awareness_block` 纯函数 + 端到端）：
   同作品=旧识（免客套）、跨作品=陌生人（不得装熟）、现实人物×虚构角色
   =素不相识但允许「我演过TA」式吐槽、现实×现实=按档案；
3. **静态新块无世界观**：P0_GROUP_DYNAMICS_RULES 对所有人可见，不得出现
   任何时代的示例词（古风词与现代词双向禁止，同跨世界观红线）；
4. **不浪费**：`others` 为空时认知块返回空串（单人不注入）。

对应 prompt 版本 `p8-crossuniverse-2026-11-04a`（幂等键，改 prompt 必须 bump）。
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import ProvenanceNamespace
from persona.core.ids import new_token
from persona.core.result import Ok, UsageDelta, unwrap
from persona.modules.dialogue import (
    DIALOGUE_PROMPT_VERSION,
    DialogueDeps,
    DialogueService,
    P0_AWARENESS_HEADER,
    P0_GROUP_DYNAMICS_RULES,
    SessionDraft,
    build_awareness_block,
    normalize_work_title,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.core.types import UsageReport
from persona.core.result import Ok
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_group_awareness"

ANCIENT_WORDS = ("阁下", "客官", "在下", "一道去")
MODERN_WORDS = ("Hi", "披萨", "兄弟")

#: 判定关键短语（prompt 文案的钉子——改文案时这里是唯一要跟着改的地方）。
PHRASE_SAME_UNIVERSE = "彼此本就相识"
PHRASE_STRANGER = "完全不认识"
PHRASE_ACTOR_TOLERANCE = "我演过TA"
PHRASE_REAL_BY_ARCHIVE = "以档案与关系档案为准"


def mk(name: str, entity_type: str, works: list[str]) -> dict:
    """构造 `build_awareness_block` 的输入形状。"""
    return {"name": name, "entity_type": entity_type, "works": works}


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
            text="（脚本化回答）知道了。",
            finish_reason="stop", model_id="scripted",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class NormalizeWorkTitleTest(unittest.TestCase):
    """作品名归一化：大小写 / 空白 / 书名号不影响同宇宙判定。"""

    def test_case_whitespace_and_brackets_are_normalized(self):
        self.assertEqual(normalize_work_title("Marvel"),
                         normalize_work_title("  marvel "))
        self.assertEqual(normalize_work_title("《漫威》"),
                         normalize_work_title("漫威"))
        self.assertEqual(normalize_work_title("复仇者 联盟"),
                         normalize_work_title("复仇者联盟"))

    def test_empty_title_is_empty(self):
        self.assertEqual(normalize_work_title(""), "")
        self.assertEqual(normalize_work_title(None), "")  # type: ignore[arg-type]


class BuildAwarenessBlockTest(unittest.TestCase):
    """纯函数判定：四种阵营组合 + 边界。"""

    def test_empty_others_returns_empty_string(self):
        self.assertEqual(build_awareness_block(mk("A", "REAL_PERSON", []), []),
                         "")
        self.assertEqual(build_awareness_block({}, []), "")

    def test_same_work_means_old_acquaintance(self):
        block = build_awareness_block(
            mk("托尼", "FICTIONAL_CHARACTER", ["漫威"]),
            [mk("史蒂夫", "FICTIONAL_CHARACTER", ["漫威"])])
        self.assertIn(P0_AWARENESS_HEADER, block)
        self.assertIn("史蒂夫", block)
        self.assertIn(PHRASE_SAME_UNIVERSE, block)
        self.assertNotIn(PHRASE_STRANGER, block)
        # 同作品只给"相识"，具体关系（战友/师徒）不得引擎代编
        self.assertIn("档案没写的不编", block)

    def test_title_normalization_joins_same_universe(self):
        block = build_awareness_block(
            mk("托尼", "FICTIONAL_CHARACTER", ["《漫威》 "]),
            [mk("史蒂夫", "FICTIONAL_CHARACTER", ["漫威"])])
        self.assertIn(PHRASE_SAME_UNIVERSE, block)

    def test_different_works_means_strangers(self):
        block = build_awareness_block(
            mk("托尼", "FICTIONAL_CHARACTER", ["漫威"]),
            [mk("布鲁斯", "FICTIONAL_CHARACTER", ["DC"])])
        self.assertIn("布鲁斯", block)
        self.assertIn(PHRASE_STRANGER, block)
        self.assertNotIn(PHRASE_SAME_UNIVERSE, block)
        self.assertIn("不得假装认识TA", block)

    def test_real_actor_meeting_fictional_role_gets_tolerance(self):
        """现实人物 × 虚构角色：素不相识 + 允许一次「我演过TA」式吐槽。"""
        block = build_awareness_block(
            mk("小罗伯特唐尼", "REAL_PERSON", []),
            [mk("托尼", "FICTIONAL_CHARACTER", ["漫威"])])
        self.assertIn("素不相识", block)
        self.assertIn(PHRASE_ACTOR_TOLERANCE, block)
        # 吐槽是许可，不是事实：不得反过来说引擎断言了"你演过他"
        self.assertIn("若TA恰好是你", block)

    def test_fictional_character_meeting_real_person(self):
        block = build_awareness_block(
            mk("托尼", "FICTIONAL_CHARACTER", ["漫威"]),
            [mk("小罗伯特唐尼", "REAL_PERSON", [])])
        self.assertIn("来自现实世界", block)
        self.assertIn(PHRASE_STRANGER, block)
        self.assertNotIn(PHRASE_ACTOR_TOLERANCE, block)

    def test_real_meeting_real_defers_to_archive(self):
        block = build_awareness_block(
            mk("本", "REAL_PERSON", []),
            [mk("马特", "REAL_PERSON", [])])
        self.assertIn(PHRASE_REAL_BY_ARCHIVE, block)
        self.assertNotIn(PHRASE_ACTOR_TOLERANCE, block)
        self.assertIn("不得虚构共同经历", block)

    def test_mixed_crowd_line_per_person(self):
        """一名发言人面对混合阵营：每人一条判定，互不串味。"""
        block = build_awareness_block(
            mk("托尼", "FICTIONAL_CHARACTER", ["漫威"]),
            [mk("史蒂夫", "FICTIONAL_CHARACTER", ["漫威"]),
             mk("布鲁斯", "FICTIONAL_CHARACTER", ["DC"]),
             mk("小罗伯特唐尼", "REAL_PERSON", [])])
        self.assertEqual(block.count("· "), 3)
        self.assertIn("史蒂夫：你们同属", block)
        self.assertIn("布鲁斯：TA来自另一部作品", block)
        self.assertIn("小罗伯特唐尼：TA来自现实世界", block)


class GroupAwarenessFixture:
    """临时 SQLite + 可指定各自作品名的实体/档案 + 对话服务。用完即删。"""

    def __init__(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-group-aware-")
        self.storage = SqliteStorage(Path(self.tmp) / "aware.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        from persona.modules.evidence import EvidenceService
        from persona.modules.memory import MemoryService
        from persona.modules.persona import PersonaService
        from persona.modules.relationship import RelationshipService
        self.evidence = EvidenceService(self.storage)
        self.persona = PersonaService(self.storage)
        self.memory = MemoryService(self.storage)
        self.provider = RecordingProvider()
        self.service = DialogueService(DialogueDeps(
            profiles=self.persona,
            relations=RelationshipService(self.storage),
            evidence=self.evidence, memory=self.memory, storage=self.storage,
            provider=self.provider, memory_reader=self.memory))

    def entity(self, name: str, entity_type: str,
               work_title: str = "") -> str:
        row: dict = {
            "display_name": name, "entity_type": entity_type,
            "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
        }
        if work_title:
            row["fictional_character_meta"] = {
                "works": [{"work_id": new_token("wrk_"),
                           "title": work_title, "medium": "COMIC"}],
                "adaptation_version": "测试改编版",
                "official_canon_level": "CANON",
            }
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity", row,
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def profile(self, entity_id: str, background: str,
                *, fictional: bool = True) -> None:
        from persona.core.enums import EvidenceDimension, EvidenceTier
        from persona.modules.evidence import EvidenceDraft, SourceDraft
        from persona.modules.persona import (
            PersonaProfileDraft,
            ProfileSection,
        )
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
        unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=background, confidence=0.8,
                                      evidence_refs=[ref]),
            expression_style=ProfileSection(text="", confidence=0.8,
                                            evidence_refs=[ref]),
            applicable_variant_ids=([entity_id] if fictional else []))))

    def start(self, participants: list[str]) -> str:
        return unwrap(self.service.start_session(
            self.ctx, SessionDraft(
                participant_entity_ids=list(participants),
                persona_profile_ids=list(participants)))).id

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class GroupPromptEndToEndTest(unittest.TestCase):
    """端到端：跨阵营会话的 system prompt 逐字断言。"""

    def setUp(self) -> None:
        self.fx = GroupAwarenessFixture()

    def tearDown(self) -> None:
        self.fx.close()

    def _system_for(self, speaker_name: str, speaker_work: str,
                    others: list[tuple[str, str, str]]) -> str:
        """speaker_work 为空串 = 现实人物；others = (名字, 作品, 背景)。"""
        eid = self.fx.entity(speaker_name,
                             "REAL_PERSON" if not speaker_work
                             else "FICTIONAL_CHARACTER", speaker_work)
        self.fx.profile(eid, f"{speaker_name}的背景：测试背景。",
                        fictional=bool(speaker_work))
        ids = [eid]
        for name, work, background in others:
            oid = self.fx.entity(name, "REAL_PERSON" if not work
                                 else "FICTIONAL_CHARACTER", work)
            self.fx.profile(oid, background, fictional=bool(work))
            ids.append(oid)
        sid = self.fx.start(ids)
        unwrap(self.fx.service.run_round(self.fx.ctx, sid, "你们认识彼此吗"))
        return self.fx.provider.last_system

    def test_same_universe_allies_know_each_other(self):
        """托尼的 prompt：对同作品的史蒂夫=旧识，免客套。"""
        system = self._system_for(
            "托尼史塔克", "漫威",
            [("史蒂夫罗杰斯", "漫威", "史蒂夫罗杰斯的背景：测试背景。")])
        self.assertIn(P0_AWARENESS_HEADER, system)
        self.assertIn("史蒂夫罗杰斯：你们同属《漫威》", system)
        self.assertIn(PHRASE_SAME_UNIVERSE, system)

    def test_cross_universe_stranger_not_treated_as_ally(self):
        """托尼的 prompt：对 DC 的布鲁斯=完全不认识。"""
        system = self._system_for(
            "托尼史塔克", "漫威",
            [("布鲁斯韦恩", "DC", "布鲁斯韦恩的背景：测试背景。")])
        self.assertIn("布鲁斯韦恩：TA来自另一部作品", system)
        self.assertIn(PHRASE_STRANGER, system)

    def test_real_actor_gets_role_tolerance_line(self):
        """现实演员的 prompt：对在场虚构角色有「我演过TA」式吐槽许可。"""
        system = self._system_for(
            "小罗伯特唐尼", "",
            [("托尼史塔克", "漫威", "托尼史塔克的背景：测试背景。")])
        self.assertIn("托尼史塔克：TA是虚构角色", system)
        self.assertIn(PHRASE_ACTOR_TOLERANCE, system)

    def test_group_dynamics_rules_always_injected(self):
        """群像法则（性格长度 + 防复读）对任何发言人都静态注入。"""
        system = self._system_for(
            "托尼史塔克", "漫威",
            [("布鲁斯韦恩", "DC", "布鲁斯韦恩的背景：测试背景。")])
        self.assertIn("【群像法则：你的话多话少由你的性格决定】", system)
        self.assertIn("严禁所有人用相近的字数与句式说话", system)
        self.assertIn("【不要复读：别人说过的话，绝不再说一遍】", system)
        self.assertIn("你绝不可以再问一遍", system)

    def test_awareness_block_absent_without_others_data(self):
        """参与者实体缺世界观元数据时认知块为空串——不编造、不炸轮次。"""
        # 直接建"无 works 的虚构角色"两人在场：无共享、无类型差异 → 陌生人档
        system = self._system_for(
            "无名甲", "雾港",
            [("无名乙", "雾港", "无名乙的背景：测试背景。")])
        # 两人都虚构、作品同为"雾港" → 仍是旧识（判定只依赖登记数据）
        self.assertIn(PHRASE_SAME_UNIVERSE, system)

    def test_prompt_version_bumped_for_group_awareness(self):
        self.assertEqual(DIALOGUE_PROMPT_VERSION,
                         "p8-projection-2026-11-13a")


class StaticGroupDynamicsTest(unittest.TestCase):
    """红线：群像法则静态块对所有人可见 → 不得携带任何时代的示例词。"""

    def test_static_block_never_leaks_worldview_words(self):
        for word in ANCIENT_WORDS + MODERN_WORDS:
            self.assertNotIn(
                word, P0_GROUP_DYNAMICS_RULES,
                f"群像法则静态块出现时代字面词 {word!r}——"
                f"静态块对所有人可见，写死任何一方的口吻都是污染")

    def test_static_block_does_not_name_specific_characters(self):
        """法则必须写成中性表述——不得举例任何具体角色名。"""
        for name in ("布鲁斯", "皮特", "托尼", "蝙蝠", "蜘蛛"):
            self.assertNotIn(name, P0_GROUP_DYNAMICS_RULES)

    def test_static_block_avoids_numbered_list_markers(self):
        """下游防弱化测试按「一、」切分三档规则——静态块禁止同款编号。"""
        for marker in ("一、", "二、", "三、"):
            self.assertNotIn(marker, P0_GROUP_DYNAMICS_RULES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
