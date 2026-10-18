"""D-286：L2 中文古典层（`zh_classical_vocabulary`）。

来源：**R4.4 真实检索**（`docs/P4_8_R44_REAL_SEARCH.md`）的两个实测结论：

1. `SPEECH_STYLE` 族在 L1 **一个标签都没有**，而林黛玉最核心的特征恰恰是
   "说话方式"（"言辞尖酸刻薄、喜欢怼人"）⇒ 模型只能硬塞 `TRAIT.witty`
   （定义是"幽默机智"）⇒ 与"幽默爱笑"那条**误并成同一个 traits 条目**。
2. 材料反复强调、L1 却没有格子的：敏感/多心、叛逆、痴情、病弱、俊美、
   崇古、平等待人。

本层**不改变默认路径**：`default_vocabulary()` 仍是纯 L1（31 个）。
把中文古典语汇塞进 L1，"换角还能不能覆盖"就再也测不出来了（D-280 §分层）。

全部离线：不触网、不调用模型、零费用。
"""

import unittest

from persona.core.usage_ledger import model_call_key_parts
from persona.modules.extraction import (
    ProviderClaimExtractor,
    build_extraction_schema,
)
from persona.modules.trait_vocabulary import (
    FAMILY_HABIT,
    FAMILY_SPEECH_STYLE,
    FAMILY_TRAIT,
    L2_ZH_CLASSICAL_TAG_SPEC,
    default_vocabulary,
    vocabulary_by_name,
    zh_classical_vocabulary,
)
from persona.orchestrator.business import (
    BusinessOrchestrator,
    _effective_dimension,
    _merge_key,
)
from tests.test_p4_8_trait_tag import PE_A, PE_B, _EvidenceStub

_stability_groups = BusinessOrchestrator._stability_groups


class TestL2Vocabulary(unittest.TestCase):

    def test_adds_eleven_labels_on_top_of_l1(self):
        l1, l2 = default_vocabulary(), zh_classical_vocabulary()
        self.assertEqual(len(l1.all_tags()), 31)
        self.assertEqual(len(L2_ZH_CLASSICAL_TAG_SPEC), 11)
        self.assertEqual(len(l2.all_tags()), 42)
        #: L1 的标签一个不少——L2 是**追加**，不是替换。
        self.assertTrue(set(l1.all_tags()) <= set(l2.all_tags()))

    def test_speech_style_family_is_no_longer_empty(self):
        """R4.4 结构缺口的正面修复：空族被填上，维度自动启用。"""
        l2 = zh_classical_vocabulary()
        speech = [t for t in l2.all_tags()
                  if l2.family_of(t) == FAMILY_SPEECH_STYLE]
        self.assertEqual(sorted(speech),
                         ["caustic", "incisive", "reserved", "tactful"])
        self.assertIn(FAMILY_SPEECH_STYLE, l2.enabled_dimensions())
        #: L1 那边**没有**动：默认路径仍然是两族。
        self.assertNotIn(FAMILY_SPEECH_STYLE,
                         default_vocabulary().enabled_dimensions())

    def test_gap_labels_from_r44_are_present(self):
        l2 = zh_classical_vocabulary()
        for tag, family in (
                ("sensitive", FAMILY_TRAIT), ("devoted", FAMILY_TRAIT),
                ("rebellious", FAMILY_TRAIT), ("egalitarian", FAMILY_TRAIT),
                ("traditional", FAMILY_TRAIT), ("frail", FAMILY_TRAIT),
                ("attractive", FAMILY_TRAIT)):
            self.assertTrue(l2.is_known(tag), tag)
            self.assertEqual(l2.family_of(tag), family, tag)
        #: HABIT 族本轮**没有**新增：R4.4 的缺口全是特征与说话方式。
        self.assertEqual(len([t for t in l2.all_tags()
                              if l2.family_of(t) == FAMILY_HABIT]), 5)

    def test_default_path_stays_l1(self):
        """默认仍是 31 个、没有 caustic——"L2 要显式选"是设计，不是巧合。"""
        default = default_vocabulary()
        self.assertEqual(len(default.all_tags()), 31)
        self.assertNotIn("caustic", default.all_tags())


class TestVocabularyByName(unittest.TestCase):

    def test_known_names(self):
        self.assertEqual(len(vocabulary_by_name("l1").all_tags()), 31)
        self.assertEqual(len(vocabulary_by_name("l2-zh-classical").all_tags()), 42)
        #: 大小写与空白容错（CLI 输入）。
        self.assertEqual(len(vocabulary_by_name(" L2-ZH-CLASSICAL ").all_tags()), 42)

    def test_unknown_name_returns_none_not_default(self):
        """拼错名字**不能**静默回退到默认——那会让"我想用 L2"悄悄失效。"""
        self.assertIsNone(vocabulary_by_name("l2-zh-classic"))
        self.assertIsNone(vocabulary_by_name(""))


class TestFingerprint(unittest.TestCase):

    def test_fingerprint_is_stable_and_layer_specific(self):
        a, b = zh_classical_vocabulary(), zh_classical_vocabulary()
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertNotEqual(a.fingerprint(), default_vocabulary().fingerprint())
        self.assertEqual(len(a.fingerprint()), 16)

    def test_l1_and_l2_do_not_share_an_idempotency_key(self):
        """切层 = 换请求意图。不把词表写进键 ⇒ 切到 L2 会被当重复跳过。

        同材料、同目标、同提示词版本，只有词表不同——键必须不同。
        """
        args = dict(model="deepseek-flash", material_keys=["key_0"],
                    target_ids=["pe_a"], prompt_version="g2-extract-2026-10-18c")
        l1 = model_call_key_parts(
            vocabulary_fingerprint=default_vocabulary().fingerprint(), **args)
        l2 = model_call_key_parts(
            vocabulary_fingerprint=zh_classical_vocabulary().fingerprint(), **args)
        self.assertNotEqual(l1, l2)
        #: 空串 = 不涉及词表的调用（对话），对既有键完全兼容。
        self.assertEqual(model_call_key_parts(**args),
                         model_call_key_parts(vocabulary_fingerprint="", **args))


class TestL2ReachesSchemaAndPrompt(unittest.TestCase):

    def test_schema_enum_and_prompt_list_speech_style_tags(self):
        schema = build_extraction_schema(zh_classical_vocabulary())
        item = schema["properties"]["claims"]["items"]["properties"]["trait_tag"]
        self.assertIn("caustic", item["enum"])
        self.assertIn("rebellious", item["enum"])
        request = ProviderClaimExtractor.build_request(
            [], None, vocabulary=zh_classical_vocabulary())
        system = request.messages[0].content
        self.assertIn("[SPEECH_STYLE]", system)
        self.assertIn("刻薄/毒舌", system)
        #: 提示词必须说明"该维度必须贴标签"，否则模型不会填 SPEECH_STYLE。
        self.assertIn("SPEECH_STYLE", system)


class TestMisMergeIsGone(unittest.TestCase):

    def test_caustic_and_witty_are_different_groups(self):
        """R4.4 的误并标本来就出在这里：尖酸刻薄（witty）与幽默爱笑（witty）。

        贴成 `caustic`（SPEECH_STYLE）之后，两条的键不同 ⇒ 不再并成一条。
        """
        l2 = zh_classical_vocabulary()
        sharp = _EvidenceStub("evd_1", "林黛玉言辞尖酸刻薄，喜欢怼人。",
                              "SPEECH_STYLE", [PE_A], "src_1",
                              trait_tag="caustic")
        funny = _EvidenceStub("evd_2", "林黛玉是幽默爱笑的少女。",
                              "TRAIT", [PE_A], "src_2", trait_tag="witty")
        #: 族由标签决定——`_effective_dimension` 会按 L2 解析出 SPEECH_STYLE。
        self.assertEqual(_effective_dimension(sharp, l2),
                         "SPEECH_STYLE")
        self.assertEqual(_effective_dimension(funny, l2), "TRAIT")
        self.assertNotEqual(_merge_key(sharp), _merge_key(funny))

    def test_two_sources_with_same_speech_style_still_merge(self):
        """补族不是"把风格说法一刀切散"：同标签跨来源照样归并（AC-7 仍成立）。"""
        l2 = zh_classical_vocabulary()
        a = _EvidenceStub("evd_1", "言辞尖刻，爱讽刺人。", "SPEECH_STYLE",
                          [PE_A], "src_1", trait_tag="caustic")
        b = _EvidenceStub("evd_2", "说话刻薄，好挖苦。", "SPEECH_STYLE",
                          [PE_A], "src_2", trait_tag="caustic")
        groups = _stability_groups([a, b])
        self.assertEqual(len(groups), 1)
        key = next(iter(groups))
        self.assertEqual(key[1], "tag:caustic")
        self.assertEqual(_effective_dimension(a, l2), "SPEECH_STYLE")

    def test_different_subjects_never_merge(self):
        a = _EvidenceStub("evd_1", "言辞尖刻。", "SPEECH_STYLE", [PE_A],
                          "src_1", trait_tag="caustic")
        b = _EvidenceStub("evd_2", "说话刻薄。", "SPEECH_STYLE", [PE_B],
                          "src_2", trait_tag="caustic")
        self.assertEqual(len(_stability_groups([a, b])), 2)


if __name__ == "__main__":
    unittest.main()
