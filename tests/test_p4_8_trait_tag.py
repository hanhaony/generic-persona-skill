"""P4.8 D-280 / D-282 / D-283 / D-284：受控特征标签 `trait_tag`。

## 这一轮解决的是什么

D-278 的实测结论：**证据层的"说法身份"用逐字归一化，跨来源同义永远归并不了**。
`evidence._normalize_claim()` 只折空白，`_stability_groups` 按归一化全等分组
⇒ `occurrences` 恒为 1 ⇒ `stability` 恒 `SINGLE` ⇒ D-012 / PF-V2 闸门把所有
TRAIT 证据挡在 `traits` 外。**与给多少材料、检索多少次无关，是结构性必然。**

D-280 的修法：把归一化**前移到抽取时**——模型从**受控词表**里选一个标签，
归并交给确定性规则（`主体集合 + 标签`）。模型只做归纳/分类，不做"这两条是不是
一个意思"的判断（那会违反 AC-7：模型给自己的产出背书）。

## 为什么必须同时做"claim 原子化"（D-282）

用 R4.3 的 16 条证据 + 草拟 L1 词表做**离线回放**（`docs/P4_8_D280_VOCAB_REPLAY.md`）：

| 情形 | 可归并组数 |
|---|---|
| 6 条复合 claim 贴标（方案原样） | **0** |
| 25 条原子事实贴标 | **3** |

原因：R4.3 抽出来的 claim 一条装 3~6 个事实（"吸烟斗、拉小提琴、听音乐会、
洗土耳其浴；1888 染可卡因、1894 戒除"），而 `trait_tag` 只能填一个
⇒ 两个来源碰巧选到同一个标签的概率只有 ~6%~8%。
**复合说法的源头是 `max_claims_per_material=4`**（D-276 为防截断逼模型压缩）——
防截断的措施制造了让归并失效的东西。原子化与 trait_tag 必须一起做。

## 本文件钉住的东西

1. **词表**：L1 规模、空词表 ⇒ 完全退化为上线前行为、L2/L3 覆盖优先级；
2. **schema / 提示词**：空词表时一个 `trait_tag` 字样都不出现；有词表时
   `null` 是合法出口、自造标签会被拒、贴错比填 null 更糟；
3. **校验**：词表外 / 启用维度缺失 / 未启用维度误填 ⇒ 拒收；族冲突 ⇒ 采纳标签族；
4. **归并键唯一实现**：`_stability_groups` 与 `_build_persona_draft`
   **判 REPEATED 的组数 == 生成条目数**（D-3「两条通道两套账」的教训）；
5. **traits > 0**：三来源同标签 ⇒ PATTERN ⇒ 进 `traits`（这是主路径的验收项）；
6. **反向断言**：没有 trait_tag 时同义说法仍然各占一行——证明"解决 D-278 的是
   trait_tag + 原子化"，而不是别的什么巧合。

全部离线：本地桩 Provider，不触网、不调真实模型、不产生费用。
"""

from __future__ import annotations

import json
import unittest

from persona.core.context import new_call_context
from persona.core.enums import EvidenceDimension
from persona.core.result import Ok, unwrap
from persona.modules.claim_validation import (
    ISSUE_TRAIT_TAG_DIMENSION_MISMATCH,
    REJECT_TRAIT_TAG_MISSING,
    REJECT_TRAIT_TAG_NOT_IN_VOCABULARY,
    REJECT_TRAIT_TAG_UNEXPECTED_DIMENSION,
    ValidationLimits,
    validate_model_payload,
)
from persona.modules.extraction import (
    DEFAULT_MAX_CLAIMS_PER_MATERIAL,
    OUTPUT_TOKEN_CEILING,
    PROMPT_VERSION,
    WARNING_TRAIT_TAG_DIMENSION_MISMATCH,
    ProviderClaimExtractor,
    build_extraction_schema,
)
from persona.modules.trait_vocabulary import (
    FAMILY_HABIT,
    FAMILY_TRAIT,
    LAYER_CUSTOM,
    LAYER_DOMAIN,
    TraitTag,
    TraitVocabulary,
    default_vocabulary,
)
from persona.orchestrator.business import (
    BusinessOrchestrator,
    _effective_dimension,
    _merge_key,
    _merge_note,
    _profile_item_id,
)

# `_stability_groups` 是 `BusinessOrchestrator` 的静态方法（G6-D3 归并键的唯一
# 实现侧）。这里取短别名，让调用点读起来跟普通函数一样。
_stability_groups = BusinessOrchestrator._stability_groups

PROJECT = "prj_p48_trait_tag"


# ==========================================================================
# 夹具
# ==========================================================================

class _EvidenceStub:
    """`_build_persona_draft` / `_stability_groups` 只需要这些属性。"""

    def __init__(self, evidence_id: str, claim: str, dimension: str,
                 subjects: list[str], source_id: str,
                 stability: str = "UNKNOWN", canon_level: str = "",
                 trait_tag: str = "") -> None:
        self.id = evidence_id
        self.claim = claim
        self.dimension = dimension
        self.subject_entity_ids = list(subjects)
        self.source_id = source_id
        self.stability = stability
        self.effective_stability = stability
        self.canon_level = canon_level
        self.applicable_variant_ids: list[str] = []
        self.trait_tag = trait_tag


PE_A = "pe_000000000000000000000000000001"
PE_B = "pe_000000000000000000000000000002"


class _MaterialStub:
    """`validate_model_payload` 的材料视图来源（它只读这几个字段）。"""

    def __init__(self, key: str = "m1", text: str = "") -> None:
        self.canonical_key = key
        self.source_id = "src_1"
        self.title = "材料"
        self.url = "https://example.invalid/m1"
        self.snippet = text
        self.quality_status = ""
        self.subject_entity_ids = [PE_A]


def _payload(*claims: dict) -> str:
    return json.dumps({"claims": list(claims)}, ensure_ascii=False)


def _claim(claim: str, dimension: str, *, trait_tag=..., quote: str | None = None,
           material_key: str = "m1", subjects=(PE_A,), tier: str | None = None):
    """一条候选。`trait_tag=...`（Ellipsis）表示**不放这个字段**。

    默认主体用 `PE_A`：`validate_model_payload` 会核对主体是否在材料的
    `subject_entity_ids` 里，对不上就是 `UNKNOWN_SUBJECT`（编造引用），
    跟被测的 trait_tag 规则毫无关系。
    """
    item = {
        "material_key": material_key,
        "claim": claim,
        "tier": tier or ("VERBATIM" if quote else "PROFILE_BACKGROUND"),
        "dimension": dimension,
        "subject_entity_ids": list(subjects),
    }
    if quote:
        item["verbatim_quote"] = quote
    if trait_tag is not ...:
        item["trait_tag"] = trait_tag
    return item


def _validate(*claims: dict, vocabulary=default_vocabulary(),
              materials=None):
    return validate_model_payload(
        _payload(*claims),
        materials=materials or [_MaterialStub(
            text=next(iter([c.get("verbatim_quote") or c.get("claim")
                            for c in claims]), ""))],
        targets=[],
        vocabulary=vocabulary)


class _StubProvider:
    """离线桩：返回给定文本，带 `offline=True`（不记账）。"""

    offline = True

    def __init__(self, text: str) -> None:
        self.calls = 0
        self._text = text

    def chat(self, request, ctx=None):
        from persona.providers.base import ChatResponse, UsageReport
        self.calls += 1
        return Ok(ChatResponse(
            text=self._text,
            usage=UsageReport(requests=1, tokens_in=10, tokens_out=10)))


# ==========================================================================
# 1. 词表
# ==========================================================================

class TestVocabulary(unittest.TestCase):

    def test_l1_has_thirty_one_tags_and_only_trait_habit_families(self):
        """草拟清单写"约 32 个"，逐条点下来是 31；且只有 TRAIT / HABIT 两族。

        `SPEECH_STYLE` **没有** L1 标签 ⇒ 该维度不是启用维度 ⇒ 该维度的
        claim 一律 `null`（回退逐字归并，与上线前一致）。将来通过 L2/L3
        注入语言风格标签即自动启用，不需要改代码（见下面那条测试）。
        """
        v = default_vocabulary()
        self.assertEqual(len(v.all_tags()), 31)
        self.assertEqual(sorted(v.enabled_dimensions()),
                         sorted([FAMILY_TRAIT, FAMILY_HABIT]))
        self.assertNotIn("SPEECH_STYLE", v.enabled_dimensions())

    def test_empty_vocabulary_behaves_as_off(self):
        """空词表 ⇒ 没有任何标签、没有启用维度、提示词块为空。"""
        v = TraitVocabulary()
        self.assertFalse(v)
        self.assertEqual(v.all_tags(), ())
        self.assertEqual(v.enabled_dimensions(), frozenset())
        self.assertEqual(v.prompt_block(), "")

    def test_layers_resolve_l3_over_l2_over_l1(self):
        v = TraitVocabulary.build(
            domain=[TraitTag("deduction", FAMILY_TRAIT, "演绎", layer=LAYER_DOMAIN)],
            custom=[TraitTag("calm", FAMILY_TRAIT, "项目定制的冷静", layer=LAYER_CUSTOM)])
        # L2 新标签进得来，L1 的同名标签被 L3 覆盖
        self.assertEqual(v.resolve("deduction").layer, LAYER_DOMAIN)
        self.assertEqual(v.resolve("calm").description, "项目定制的冷静")
        self.assertEqual(v.resolve("calm").layer, LAYER_CUSTOM)
        # L1 里没被覆盖的照旧
        self.assertEqual(v.resolve("observant").layer, "L1")

    def test_injecting_speech_style_tags_auto_enables_the_dimension(self):
        """启用维度**由词表里实际存在的族决定**，不写死。

        这样"哪些维度要贴标签"只有一处事实来源；写死一份清单，
        注入方根本不知道自己在改行为。
        """
        v = TraitVocabulary.build(domain=[
            TraitTag("mantra_muttering", "SPEECH_STYLE", "口头禅式念叨",
                     layer=LAYER_DOMAIN)])
        self.assertIn("SPEECH_STYLE", v.enabled_dimensions())

    def test_family_of_unknown_tag_returns_empty_not_raise(self):
        """未知标签 ⇒ `""`（调用方按"没有标签"处理），**不抛错**。

        `_build_persona_draft` 可能收到手工构造的证据对象；让归并因为一个
        不认识的标签崩掉，比保守回退更糟。
        """
        self.assertEqual(default_vocabulary().family_of("teleportation"), "")
        self.assertEqual(default_vocabulary().family_of(""), "")

    def test_all_tags_are_sorted_and_unique(self):
        tags = default_vocabulary().all_tags()
        self.assertEqual(tags, tuple(sorted(set(tags))))


# ==========================================================================
# 2. schema 与提示词
# ==========================================================================

class TestSchemaAndPrompt(unittest.TestCase):

    def test_vocabularyless_schema_has_no_trait_tag(self):
        """未配置词表 ⇒ schema 里**没有**这个字段（方案 §8 第 3 项）。"""
        item = build_extraction_schema(None)["properties"]["claims"]["items"]
        self.assertNotIn("trait_tag", item["properties"])

    def test_schema_exposes_tag_enum_with_null_escape(self):
        schema = build_extraction_schema(default_vocabulary())
        field = schema["properties"]["claims"]["items"]["properties"]["trait_tag"]
        self.assertEqual(field["type"], ["string", "null"])
        self.assertIn(None, field["enum"], "null 是刻意保留的出口")
        for tag in default_vocabulary().all_tags():
            self.assertIn(tag, field["enum"])

    def test_empty_vocabulary_prompt_never_mentions_trait_tag(self):
        """空词表 ⇒ 提示词一个字都不提它是那个字段。

        规则里提、schema 里没有 ⇒ 模型左右为难，只会乱猜。
        """
        request = ProviderClaimExtractor.build_request([], [])
        self.assertNotIn("trait_tag", request.messages[0].content)

    def test_vocabulary_prompt_states_the_three_consequences(self):
        """必须写清后果，不只写规则（D-276 的经验）。

        三条缺一不可：必须填、禁止自造、**贴错比填 null 更糟**。
        """
        system = ProviderClaimExtractor.build_request(
            [], [], vocabulary=default_vocabulary()).messages[0].content
        self.assertIn("禁止自造标签", system)
        self.assertIn("贴错标签比填 null 更糟", system)
        self.assertIn("一条 claim 只陈述一个事实", system)
        self.assertIn("analytical", system)      # 词表内容被注入
        self.assertIn("substance_use", system)

    def test_prompt_version_bumped_and_not_the_old_value(self):
        """提示词一改版本必须 +1，否则幂等键会让新旧产出互相串。

        旧值是 `g2-extract-2026-10-17`（只加了"每份材料最多 N 条"）。
        """
        self.assertEqual(PROMPT_VERSION, "g2-extract-2026-11-03a")
        self.assertNotEqual(PROMPT_VERSION, "g2-extract-2026-10-18c")

    def test_request_metadata_advertises_tag_state(self):
        """dry-run 报告要能一眼看出"这次开没开 trait_tag"。

        metadata 不是装饰：它是"估算是真发的那一个"这条纪律的延伸
        （D-272：估算与真发必须同源）。
        """
        with_tags = ProviderClaimExtractor.build_request(
            [], [], vocabulary=default_vocabulary()).metadata
        self.assertEqual(with_tags["prompt_version"], PROMPT_VERSION)
        self.assertEqual(with_tags["trait_tag_enabled"], "true")
        self.assertEqual(with_tags["trait_tags"], "31")

        without = ProviderClaimExtractor.build_request([], []).metadata
        self.assertEqual(without["trait_tag_enabled"], "false")
        self.assertEqual(without["trait_tags"], "0")

    def test_max_claims_covers_nine_materials_times_cap(self):
        """`ValidationLimits.max_claims` 必须装得下提示词允许的最大条数。

        9 份材料 × 4 条 = 36。设小了 = 用一个常量把另一条链路的成果作废
        （R4.3 实测 9 份 × 4 条 = 7456 token 正好是上限，那条链路能跑通）。
        """
        self.assertGreaterEqual(ValidationLimits().max_claims,
                                9 * DEFAULT_MAX_CLAIMS_PER_MATERIAL)
        self.assertLessEqual(9 * DEFAULT_MAX_CLAIMS_PER_MATERIAL * 200,
                             OUTPUT_TOKEN_CEILING)


# ==========================================================================
# 3. 校验（claim_validation）
# ==========================================================================

class TestValidation(unittest.TestCase):

    def test_tag_outside_vocabulary_is_rejected_not_rewritten(self):
        result = _validate(_claim("会传送", "TRAIT", trait_tag="teleportation"))
        reasons = [r.reason for r in result.rejected]
        self.assertIn(REJECT_TRAIT_TAG_NOT_IN_VOCABULARY, reasons)
        message = [r.message for r in result.rejected
                   if r.reason == REJECT_TRAIT_TAG_NOT_IN_VOCABULARY][0]
        self.assertIn("不把它改写成词表里的某一个", message)

    def test_missing_tag_on_enabled_dimension_is_rejected(self):
        result = _validate(_claim("逻辑缜密", "TRAIT"))     # 不放 trait_tag
        self.assertEqual([r.reason for r in result.rejected],
                         [REJECT_TRAIT_TAG_MISSING])

    def test_explicit_null_is_a_legal_exit(self):
        """**显式 null 与"字段缺失"是两件事**（D-280 §5）。

        null = 模型考虑过并说"这条贴不上"，该条回退按原文归并；
        字段缺失 = 模型压根没想这件事，正是"可填可不填"的漏洞。
        """
        result = _validate(_claim("思维简单直接", "TRAIT", trait_tag=None))
        self.assertEqual(len(result.rejected), 0)
        self.assertEqual(result.accepted[0].trait_tag, "")

    def test_empty_string_counts_as_missing(self):
        """空串不是"我不知道"的表示，按缺失拒收。"""
        result = _validate(_claim("思维简单直接", "TRAIT", trait_tag=""))
        self.assertEqual([r.reason for r in result.rejected],
                         [REJECT_TRAIT_TAG_MISSING])

    def test_tag_on_unexpected_dimension_is_rejected(self):
        """BACKGROUND 上贴标签 ⇒ 拒收（不是"以标签为准"）。

        否则一句背景描述贴个 `analytical` 就能绕过稳定性闸门
        （D-012 / PF-V2 只允许 REPEATED / PATTERN 进 `traits`）。
        """
        result = _validate(_claim("1852 年生", "BACKGROUND",
                                  trait_tag="analytical"))
        self.assertEqual([r.reason for r in result.rejected],
                         [REJECT_TRAIT_TAG_UNEXPECTED_DIMENSION])

    def test_untagged_dimensions_accept_no_tag(self):
        """未启用维度不给标签 ⇒ 正常放行（与 trait_tag 上线前行为一致）。"""
        result = _validate(_claim("1852 年生", "BACKGROUND"),
                           _claim("初见在化学实验室", "EXPERIENCE"))
        self.assertEqual(len(result.accepted), 2)
        self.assertTrue(all(a.trait_tag == "" for a in result.accepted))

    def test_family_conflict_adopts_the_tag_family_and_records_it(self):
        """dimension=TRAIT 但贴了 `music`（HABIT 族）⇒ 放行，按族落板块。

        R4.3 实测同一个事实在不同来源被抽成不同维度（小提琴在 baike 是
        HABIT、在大英图书馆是 TRAIT）。维度不可靠 ⇒ 以标签为准，
        但**必须留痕**，否则"板块其实是标签族决定的"这件事看不见。
        """
        result = _validate(_claim("会拉小提琴", "TRAIT", trait_tag="music"))
        self.assertEqual(len(result.rejected), 0)
        accepted = result.accepted[0]
        self.assertEqual(accepted.trait_tag, "music")
        self.assertIn(ISSUE_TRAIT_TAG_DIMENSION_MISMATCH, accepted.issues)
        self.assertTrue(any("已按标签族归入 HABIT" in i for i in result.issues))

    def test_tag_must_be_a_string(self):
        result = _validate(_claim("冷静", "TRAIT", trait_tag=42))
        self.assertIn("TYPE_ERROR", [r.reason for r in result.rejected])

    def test_consistent_tag_and_dimension_passes_cleanly(self):
        for tag, dimension in (("analytical", "TRAIT"), ("music", "HABIT"),
                               ("just", "TRAIT"), ("substance_use", "HABIT")):
            result = _validate(_claim("测试", dimension, trait_tag=tag))
            self.assertEqual(len(result.rejected), 0, f"{tag}/{dimension}")
            self.assertNotIn(ISSUE_TRAIT_TAG_DIMENSION_MISMATCH,
                             result.accepted[0].issues)

    def test_without_vocabulary_behavior_is_identical_to_before(self):
        """**空词表 ⇒ 与 trait_tag 上线之前完全一致**（方案 §8 第 3 项）。

        同一个 payload：有词表时 3 条被拒（词表外 / 缺标签 / 背景塞标签）、
        5 条放行（其中 3 条带标签，含 1 条族冲突留痕）；没词表时 8 条全放行、
        标签恒空。这个断言是"不装词表不退化"的回归防线。
        """
        claims = (
            _claim("逻辑缜密", "TRAIT", trait_tag="analytical"),
            _claim("吸食可卡因", "HABIT", trait_tag="substance_use"),
            _claim("词表外", "TRAIT", trait_tag="teleportation"),
            _claim("缺标签", "TRAIT"),
            _claim("背景塞标签", "BACKGROUND", trait_tag="analytical"),
            _claim("族冲突", "TRAIT", trait_tag="music"),
            _claim("未启用无标签", "BACKGROUND"),
            _claim("显式 null", "TRAIT", trait_tag=None),
        )
        text = _payload(*claims)
        with_v = validate_model_payload(
            text, materials=[_MaterialStub()], targets=[],
            vocabulary=default_vocabulary())
        without_v = validate_model_payload(
            text, materials=[_MaterialStub()], targets=[], vocabulary=None)

        self.assertEqual(len(without_v.accepted), 8, "空词表不该拒任何一条")
        self.assertTrue(all(a.trait_tag == "" for a in without_v.accepted))
        self.assertEqual(len(with_v.accepted), 5)
        self.assertEqual(len(with_v.rejected), 3)


# ==========================================================================
# 4. 归并键（唯一实现）
# ==========================================================================

class TestMergeKey(unittest.TestCase):

    def test_same_tag_merges_across_different_wording(self):
        """**D-278 的核心修复**：措辞不同、标签相同 ⇒ 同一组。"""
        items = [
            _EvidenceStub("evd_1", "逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "聪明如超人", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
            _EvidenceStub("evd_3", "智慧超群", "TRAIT", [PE_A], "src_3",
                          trait_tag="analytical"),
        ]
        groups = _stability_groups(items)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(next(iter(groups.values()))), 3)

    def test_different_subjects_never_merge_even_with_same_tag(self):
        """主体集合**必须**留在键里，而且是集合相等（不是有交集）。

        回放里跑出过实物：把华生"带小聪明的分析"贴成 `analytical` 后，
        键不含主体 ⇒ 它被并进"福尔摩斯分析能力强"这一条。那就是误并。
        """
        items = [
            _EvidenceStub("evd_1", "逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "思维简单直接", "TRAIT", [PE_B], "src_2",
                          trait_tag="analytical"),
        ]
        self.assertEqual(len(_stability_groups(items)), 2)

    def test_intersection_is_not_enough_for_subjects(self):
        """`{福}` 与 `{福, 华}` 有交集，但**不是同一主体集合** ⇒ 不归并。

        若改成"有交集即同组"，双主体证据会把两个人物的特征连成一片。
        """
        items = [
            _EvidenceStub("evd_1", "聪明", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "也很聪明", "TRAIT", [PE_A, PE_B], "src_2",
                          trait_tag="analytical"),
        ]
        self.assertEqual(len(_stability_groups(items)), 2)

    def test_dimension_is_not_part_of_the_key(self):
        """**D-283**：同一个事实在不同来源被抽成不同维度 ⇒ 仍然归并。

        R4.3 实测：小提琴在 baike 是 `HABIT`，在大英图书馆和别处是 `TRAIT`。
        维度留在键里，这些永远合不到一起（G6-D3 当年刻意排除它的理由）。
        """
        items = [
            _EvidenceStub("evd_1", "会拉小提琴", "HABIT", [PE_A], "src_1",
                          trait_tag="music"),
            _EvidenceStub("evd_2", "会拉小提琴", "TRAIT", [PE_A], "src_2",
                          trait_tag="music"),
        ]
        self.assertEqual(len(_stability_groups(items)), 1)

    def test_untagged_claims_still_merge_only_on_identical_wording(self):
        """没有标签 ⇒ **与 trait_tag 上线前完全一致**：逐字归一化分组。"""
        same = [
            _EvidenceStub("evd_1", "福尔摩斯  会拉小提琴", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "福尔摩斯 会拉小提琴", "TRAIT", [PE_A], "src_2"),
        ]
        self.assertEqual(len(_stability_groups(same)), 1, "折空白差异算同一个说法")
        different = [
            _EvidenceStub("evd_1", "福尔摩斯会拉小提琴", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "福尔摩斯极为擅长小提琴", "TRAIT", [PE_A], "src_2"),
        ]
        self.assertEqual(len(_stability_groups(different)), 2,
                         "措辞不同仍然各占一行——D-278 未被解决的地方")

    def test_tag_keys_and_text_keys_cannot_collide(self):
        """`tag:` / `text:` 前缀让两种键不可能撞上（一个说法不会长成 `tag:x`）。"""
        items = [
            _EvidenceStub("evd_1", "tag:analytical", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "tag:analytical", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
        ]
        self.assertEqual(len(_stability_groups(items)), 2)


# ==========================================================================
# 5. 条目生成：板块由标签族决定
# ==========================================================================

class TestRouting(unittest.TestCase):

    def _draft(self, items, entity_id=PE_A, vocabulary=default_vocabulary(),
               stabilities=None):
        return BusinessOrchestrator._build_persona_draft(
            entity_id, items, "FICTIONAL_CHARACTER", stabilities, vocabulary)

    def test_tag_family_decides_the_section_not_dimension(self):
        """dimension=TRAIT 但标签是 `music`（HABIT 族）⇒ 进 habits。"""
        draft = self._draft([
            _EvidenceStub("evd_1", "会拉小提琴", "TRAIT", [PE_A], "src_1",
                          trait_tag="music")])
        self.assertEqual(len(draft.habits), 1)
        self.assertEqual(len(draft.traits) + len(draft.single_events), 0)

    def test_trait_tag_with_habit_dimension_lands_in_traits(self):
        """反过来：dimension=HABIT 但标签是 `analytical` ⇒ 进 traits。

        这一条与上一条合起来，证明"板块由谁决定"只有一个答案：标签族。
        """
        draft = self._draft([
            _EvidenceStub("evd_1", "逻辑缜密", "HABIT", [PE_A], "src_1",
                          stability="REPEATED", trait_tag="analytical")])
        self.assertEqual(len(draft.traits), 1)
        self.assertEqual(len(draft.habits), 0)

    def test_untagged_claim_routes_by_its_own_dimension(self):
        """没有标签 ⇒ 完全按 dimension 走（与上线前一致）。"""
        draft = self._draft([
            _EvidenceStub("evd_1", "会拉小提琴", "HABIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "逻辑缜密", "TRAIT", [PE_A], "src_2",
                          stability="REPEATED"),
        ])
        self.assertEqual(len(draft.habits), 1)
        self.assertEqual(len(draft.traits), 1)

    def test_merged_entry_records_how_many_sources_and_wordings(self):
        """D-284：`TRAIT_TAG_DIVERSITY` 字符重合度告警的替代品。

        回放实测那个指标无区分度：真归并组 `analytical` 组内重合度 0.000，
        "吸烟斗 vs 注射可卡因"这种真该警惕的对照对也是 0.000——
        **同义必然异形**。改成不做判断、只报事实。
        """
        draft = self._draft([
            _EvidenceStub("evd_1", "逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "聪明如超人", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
            _EvidenceStub("evd_3", "智慧超群", "TRAIT", [PE_A], "src_3",
                          trait_tag="analytical"),
        ], stabilities={f"evd_{i}": "PATTERN" for i in (1, 2, 3)})
        entry = draft.traits[0]
        self.assertIn("3 个来源", entry.uncertainty_note)
        self.assertIn("3 条措辞各异的说法", entry.uncertainty_note)
        self.assertIn("原文见证据引用", entry.uncertainty_note)
        self.assertEqual(len(entry.evidence_refs), 3)
        # 原文仍可追溯：第一条作为展示文本，其余在证据引用里
        self.assertEqual(entry.text, "逻辑缜密")

    def test_single_wording_gets_no_merge_note(self):
        """只有一种措辞 ⇒ 没什么可说明的，不添噪声。"""
        draft = self._draft([
            _EvidenceStub("evd_1", "会拉小提琴", "HABIT", [PE_A], "src_1",
                          trait_tag="music")])
        self.assertEqual(draft.habits[0].uncertainty_note, "")
        self.assertEqual(_merge_note({"distinct_texts": ["x"], "source_ids": ["s"]}), "")

    def test_identical_wording_from_two_sources_gets_no_note(self):
        """跨源**同句**合并（G12/D-250）不需要说明——那本来就不是归并出来的。"""
        draft = self._draft([
            _EvidenceStub("evd_1", "会拉小提琴。", "TRAIT", [PE_A], "src_1",
                          trait_tag="music"),
            _EvidenceStub("evd_2", "会拉小提琴。", "HABIT", [PE_A], "src_2",
                          trait_tag="music"),
        ])
        self.assertEqual(len(draft.habits), 1)
        self.assertEqual(draft.habits[0].uncertainty_note, "")
        self.assertEqual(len(draft.habits[0].evidence_refs), 2)

    def test_anchors_use_the_same_key_as_entries(self):
        """锚点分组键必须与条目键一致（D-281 的"口径一致"声明在新的键上仍成立）。"""
        draft = self._draft([
            _EvidenceStub("evd_1", "逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "聪明如超人", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
        ])
        self.assertEqual(len(draft.evidence_anchors), 1)
        anchor = draft.evidence_anchors[0]
        self.assertEqual(anchor["evidence_count"], 2)
        self.assertEqual(anchor["source_count"], 2)
        self.assertFalse(anchor["in_traits"], "锚点仍然不进 traits（D-281 边界）")


# ==========================================================================
# 6. 归并键唯一实现：判定侧与条目侧必须是同一本账
# ==========================================================================

class TestSingleMergeKeyImplementation(unittest.TestCase):
    """D-3 的教训是"两条通道两套账"；D-283 要求归并键收敛到唯一实现。

    这里用**同一条证据集合**同时喂两侧，断言"判 REPEATED/PATTERN 的组数
    == 生成条目数"。两侧各写一遍键时，这条断言会红——
    这正是它存在的意义（不是重复，是防漂移）。
    """

    def _items(self):
        return [
            # 三来源同标签 ⇒ 应判 PATTERN、应生成 1 条
            _EvidenceStub("evd_1", "逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "聪明如超人", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
            _EvidenceStub("evd_3", "智慧超群", "TRAIT", [PE_A], "src_3",
                          trait_tag="analytical"),
            # 两来源同标签 ⇒ 应判 REPEATED、应生成 1 条
            _EvidenceStub("evd_4", "会拉小提琴", "HABIT", [PE_A], "src_1",
                          trait_tag="music"),
            _EvidenceStub("evd_5", "极为擅长小提琴", "TRAIT", [PE_A], "src_2",
                          trait_tag="music"),
            # 单来源 ⇒ SINGLE、应生成 1 条（进 single_events）
            _EvidenceStub("evd_6", "冷静", "TRAIT", [PE_A], "src_3",
                          trait_tag="calm"),
            # 无标签同句 ⇒ 逐字归并一条
            _EvidenceStub("evd_7", "1852 年生", "BACKGROUND", [PE_A], "src_3"),
            # 无标签异句 ⇒ 两条
            _EvidenceStub("evd_8", "当过军医", "BACKGROUND", [PE_A], "src_1"),
        ]

    def test_group_count_matches_entry_count(self):
        items = self._items()
        groups = _stability_groups(items)
        draft = BusinessOrchestrator._build_persona_draft(
            PE_A, items, "FICTIONAL_CHARACTER", None, default_vocabulary())

        entries = (draft.traits + draft.single_events + draft.habits
                   + draft.experiences)
        # 只有三个可贴标签的维度产出 `ProfileItem`；BACKGROUND /
        # SPEECH_STYLE 走文本板块（没有 item_id，重复文本只留一次）。
        # 所以"组数 == 条目数"这门账**只在可贴标签的维度上对**。
        tag_capable = {
            key: members for key, members in groups.items()
            if _effective_dimension(members[0], default_vocabulary())
            in (EvidenceDimension.TRAIT, EvidenceDimension.HABIT,
                EvidenceDimension.EXPERIENCE)}
        # 3 个标签组（analytical / music / calm）+ 0 个文本组
        self.assertEqual(len(tag_capable), 3)
        self.assertEqual(len(entries), len(tag_capable))
        # 条目 id 由 (维度, 说法键, 主体) 机械决定 ⇒ 可以逐个对上。
        # 两侧各写一遍键时这里会红：条目 id 对不上任何一个组
        expected_ids = {
            _profile_item_id(
                _effective_dimension(members[0], default_vocabulary()),
                key[1], tuple(sorted(key[0])))
            for key, members in tag_capable.items()}
        self.assertEqual({e.item_id for e in entries}, expected_ids)

    def test_three_sources_same_tag_reaches_pattern_and_enters_traits(self):
        """**主路径验收项**：traits 不再是结构性为 0（D-278 的修复证据）。"""
        items = [
            _EvidenceStub("evd_1", "逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "聪明如超人", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
            _EvidenceStub("evd_3", "智慧超群", "TRAIT", [PE_A], "src_3",
                          trait_tag="analytical"),
        ]
        # 判定侧：一个组、三个来源
        groups = _stability_groups(items)
        self.assertEqual(len(groups), 1)
        # 条目侧：stability 取组内最强 ⇒ PATTERN ⇒ 过闸门
        draft = BusinessOrchestrator._build_persona_draft(
            PE_A, items, "FICTIONAL_CHARACTER",
            {i.id: "PATTERN" for i in items}, default_vocabulary())
        self.assertEqual(len(draft.traits), 1)
        self.assertEqual(draft.traits[0].stability, "PATTERN")

    def test_single_stability_still_blocked_from_traits(self):
        """D-012 / PF-V2 没有被绕过：单来源特征仍然进不了 traits。"""
        draft = BusinessOrchestrator._build_persona_draft(
            PE_A, [_EvidenceStub("evd_1", "冷静", "TRAIT", [PE_A], "src_1",
                                 trait_tag="calm")],
            "FICTIONAL_CHARACTER", None, default_vocabulary())
        self.assertEqual(len(draft.traits), 0)
        self.assertEqual(len(draft.single_events), 1)


# ==========================================================================
# 7. R4.3 回放数据的复刻（离线）
# ==========================================================================

class TestR43ReplayReplication(unittest.TestCase):
    """把 `docs/P4_8_D280_VOCAB_REPLAY.md` 的 25 条原子事实搬进测试。

    回放的三个可归并组必须在这里原样复现——它不但是回归防线，
    也是"离线回放的结论确实被实现兑现"的证据。
    """

    # (证据 ID, 说法, dimension, 标签, 来源)
    ATOMIC = (
        ("e01", "吸烟斗", "HABIT", "substance_use", "baike"),
        ("e02", "拉小提琴", "HABIT", "music", "baike"),
        ("e03", "听音乐会", "HABIT", "music", "baike"),
        ("e04", "洗土耳其浴", "HABIT", "grooming", "baike"),
        ("e05", "一八八八年染上可卡因，一八九四年戒除", "HABIT",
         "substance_use", "baike"),
        ("e06", "精通化学、心理学、解剖学、数学、外语", "TRAIT",
         "knowledgeable", "baike"),
        ("e07", "是极为成功的小提琴演奏者", "TRAIT", "music", "baike"),
        ("e08", "善于刀剑", "TRAIT", "exercise", "baike"),
        ("e09", "观察入微", "TRAIT", "observant", "baike"),
        ("e10", "逻辑缜密", "TRAIT", "analytical", "baike"),
        ("e11", "办案全心投入、废寝忘食", "TRAIT", "persistent", "baike"),
        ("e12", "特立独行", "TRAIT", "aloof", "bl"),
        ("e13", "聪明如超人", "TRAIT", "analytical", "bl"),
        ("e14", "会拉小提琴", "TRAIT", "music", "bl"),
        ("e15", "给自己注射可卡因溶液", "TRAIT", "substance_use", "bl"),
        ("e16", "嘴衔烟斗", "TRAIT", "substance_use", "qidian"),
        ("e17", "富有正义感", "TRAIT", "just", "qidian"),
        ("e18", "冷静", "TRAIT", "calm", "qidian"),
        ("e19", "勇敢", "TRAIT", "courageous", "qidian"),
        ("e20", "智慧超群", "TRAIT", "analytical", "qidian"),
        ("e21", "叙述中常掺杂自己带点小聪明的分析", "TRAIT", "witty", "zrzyzjw"),
    )

    def _items(self):
        return [
            _EvidenceStub(*[eid], claim, dim, [PE_A], f"src_{src}",
                          trait_tag=tag)
            for eid, claim, dim, tag, src in self.ATOMIC
        ]

    def test_exactly_three_merges_are_possible(self):
        """回放结论：25 条原子事实 ⇒ **3 组可归并**。

        analytical（3 源，PATTERN）/ music（2 源，REPEATED）/
        substance_use（3 源，PATTERN）。其余 11 组单源。
        """
        groups = _stability_groups(self._items())
        merged = {k: v for k, v in groups.items()
                  if len({i.source_id for i in v}) >= 2}
        self.assertEqual(len(merged), 3)
        keys = {k[1] for k in merged}
        self.assertEqual(keys, {"tag:analytical", "tag:music", "tag:substance_use"})

    def test_analytical_reaches_pattern_with_three_sources(self):
        groups = _stability_groups(self._items())
        group = [v for k, v in groups.items() if k[1] == "tag:analytical"][0]
        self.assertEqual(len({i.source_id for i in group}), 3)
        self.assertEqual({i.claim for i in group},
                         {"逻辑缜密", "聪明如超人", "智慧超群"})

    def test_composite_claim_would_not_have_merged(self):
        """**反向断言**：R4.3 的 6 条复合 claim 贴标 ⇒ 0 组可归并。

        这是"必须同时做原子化"的证据。把它钉在这里，防止将来有人
        以为只上 trait_tag 就够了。
        """
        composite = [
            _EvidenceStub("c1", "嗜好包括吸烟斗、拉小提琴、听音乐会、洗土耳其浴；"
                                "一八八八年染上服用可卡因的恶习，一八九四年戒除",
                          "HABIT", [PE_A], "src_baike", trait_tag="substance_use"),
            _EvidenceStub("c2", "精通化学、心理学、解剖学、数学、外语等多种侦探所需专长，"
                                "是极为成功的小提琴演奏者，也善于刀剑，观察入微、"
                                "逻辑缜密，办案时全心投入、废寝忘食",
                          "TRAIT", [PE_A], "src_baike", trait_tag="knowledgeable"),
            _EvidenceStub("c3", "特立独行又聪明如超人的顾问侦探，会拉小提琴、"
                                "给自己注射可卡因溶液",
                          "TRAIT", [PE_A, PE_B], "src_bl", trait_tag="analytical"),
            _EvidenceStub("c4", "清瘦的高个子，身披大氅，嘴衔烟斗，鹰钩鼻，目光锐利，"
                                "富有正义感",
                          "TRAIT", [PE_A, PE_B], "src_qidian", trait_tag="just"),
            _EvidenceStub("c5", "被塑造得冷静、勇敢、智慧超群",
                          "TRAIT", [PE_A], "src_qidian", trait_tag="calm"),
            _EvidenceStub("c6", "作为叙述者常掺杂自己带点小聪明的分析",
                          "TRAIT", [PE_B], "src_zrzyzjw", trait_tag="witty"),
        ]
        groups = _stability_groups(composite)
        merged = {k: v for k, v in groups.items()
                  if len({i.source_id for i in v}) >= 2}
        self.assertEqual(merged, {}, "复合说法下一条只能贴一个标签，选不中同一个")


# ==========================================================================
# 8. 抽取器贯通：模型输出 → 证据 → trait_tag
# ==========================================================================

class TestExtractorWiring(unittest.TestCase):

    def test_trait_tag_survives_to_the_outcome(self):
        """模型给的标签要一路带到 `CandidateClaim`（否则下游什么都归并不了）。"""
        text = _payload(_claim("逻辑缜密", "TRAIT", trait_tag="analytical"))
        extractor = ProviderClaimExtractor(
            _StubProvider(text), vocabulary=default_vocabulary())
        outcome = unwrap(extractor.extract(
            new_call_context(project_id=PROJECT, actor="test"),
            [_MaterialStub(text="逻辑缜密")]))
        self.assertEqual(len(outcome.claims), 1)
        self.assertEqual(outcome.claims[0].trait_tag, "analytical")

    def test_mismatch_warning_names_the_family(self):
        """D-283：族冲突要报出来，且说清"按标签族归入"。"""
        text = _payload(_claim("会拉小提琴", "TRAIT", trait_tag="music"))
        extractor = ProviderClaimExtractor(
            _StubProvider(text), vocabulary=default_vocabulary())
        outcome = unwrap(extractor.extract(
            new_call_context(project_id=PROJECT, actor="test"),
            [_MaterialStub(text="会拉小提琴")]))
        codes = [w.code for w in outcome.warnings]
        self.assertIn(WARNING_TRAIT_TAG_DIMENSION_MISMATCH, codes)
        message = [w.message for w in outcome.warnings
                   if w.code == WARNING_TRAIT_TAG_DIMENSION_MISMATCH][0]
        self.assertIn("按标签族", message)

    def test_no_vocabulary_leaves_the_tag_alone(self):
        """没配词表的抽取器：标签字段被忽略，行为与上线前一致。"""
        text = _payload(_claim("逻辑缜密", "TRAIT", trait_tag="analytical"))
        extractor = ProviderClaimExtractor(_StubProvider(text))
        outcome = unwrap(extractor.extract(
            new_call_context(project_id=PROJECT, actor="test"),
            [_MaterialStub(text="逻辑缜密")]))
        self.assertEqual(outcome.claims[0].trait_tag, "")


# ==========================================================================
# 9. 主路径：traits 不再是结构性为 0
# ==========================================================================

class TestTraitsBecomeNonEmpty(unittest.TestCase):
    """D-278 的验收：**同一个事实被三个来源各自表述 ⇒ 仍然合得起来**。

    这里用白盒方式直接喂 `_build_persona_draft`（stability 由 `_stability_groups`
    单独测），避免为了凑一条端到端夹具而重复造存储。
    模型 → 抽取器那一半由 `TestExtractorWiring` 兜住。
    """

    def test_stub_draft_produces_traits(self):
        items = [
            _EvidenceStub("evd_1", "福尔摩斯逻辑缜密", "TRAIT", [PE_A], "src_1",
                          trait_tag="analytical"),
            _EvidenceStub("evd_2", "福尔摩斯聪明如超人", "TRAIT", [PE_A], "src_2",
                          trait_tag="analytical"),
            _EvidenceStub("evd_3", "福尔摩斯智慧超群", "TRAIT", [PE_A], "src_3",
                          trait_tag="analytical"),
        ]
        draft = BusinessOrchestrator._build_persona_draft(
            PE_A, items, "FICTIONAL_CHARACTER",
            {i.id: "PATTERN" for i in items}, default_vocabulary())
        self.assertEqual(len(draft.traits), 1)
        self.assertEqual(draft.status, "OK")
        self.assertEqual(draft.traits[0].evidence_refs,
                         ["evd_1", "evd_2", "evd_3"])
        # 组内三条措辞都在 note 里可查（D-284：不静默）
        self.assertIn("3 条措辞各异的说法", draft.traits[0].uncertainty_note)

    def test_single_source_still_cannot_become_a_trait(self):
        """**反向断言**：这条修复没有顺手把闸门拆掉。

        单来源特征仍然只进 `single_events`（D-012 / PF-V2 不变）。
        """
        items = [_EvidenceStub("evd_1", "冷静", "TRAIT", [PE_A], "src_1",
                               trait_tag="calm")]
        draft = BusinessOrchestrator._build_persona_draft(
            PE_A, items, "FICTIONAL_CHARACTER", None, default_vocabulary())
        self.assertEqual(len(draft.traits), 0)
        self.assertEqual(len(draft.single_events), 1)


# ==========================================================================
# 10. trait_tag 贯穿 EvidenceDraft → 行 → 视图
# ==========================================================================

class TestEvidencePlumbing(unittest.TestCase):

    def test_draft_row_and_view_carry_the_tag(self):
        from persona.modules.evidence import (
            EvidenceService, EvidenceDraft, EvidenceView,
        )
        import tempfile
        from persona.storage.sqlite import SqliteStorage
        from persona.core.context import new_call_context

        with tempfile.TemporaryDirectory(prefix="p48-tag-") as tmp:
            storage = SqliteStorage(f"{tmp}/persona.db")
            service = EvidenceService(storage)
            ctx = new_call_context(project_id=PROJECT, actor="test")
            # 没有来源时 `add_evidence` 会按源缺失校验；这里只验字段贯通，
            # 因此直接看 payload 与 _evidence_view。
            draft = EvidenceDraft(
                claim="逻辑缜密", tier="PROFILE_BACKGROUND", dimension="TRAIT",
                subject_entity_ids=[PE_A], extraction_confidence=0.5,
                source_id=None, trait_tag="analytical")
            self.assertEqual(draft.trait_tag, "analytical")
            view = service._evidence_view({
                "id": "evd_x", "claim": draft.claim, "tier": "PROFILE_BACKGROUND",
                "dimension": "TRAIT", "namespace": "PUBLIC_SOURCE",
                "source_id": None, "source_event_ids": [],
                "subject_entity_ids": [PE_A], "stability": "UNKNOWN",
                "status": "ACTIVE", "conflict_ids": [], "trait_tag": "analytical",
            })
            self.assertIsInstance(view, EvidenceView)
            self.assertEqual(view.trait_tag, "analytical")
            # 旧行没有这个键 ⇒ 读出来是空串（不是 None、不抛错）
            legacy = service._evidence_view({
                "id": "evd_old", "claim": "x", "tier": "PROFILE_BACKGROUND",
                "dimension": "TRAIT", "namespace": "PUBLIC_SOURCE",
                "source_id": None, "source_event_ids": [],
                "subject_entity_ids": [PE_A], "stability": "UNKNOWN",
                "status": "ACTIVE", "conflict_ids": [],
            })
            self.assertEqual(legacy.trait_tag, "")

    def test_merge_key_ignores_objects_without_the_field(self):
        """旧证据对象没有 `trait_tag` 属性 ⇒ 键仍能算（getattr 兜底）。"""
        class _Legacy:
            claim = "会拉小提琴"
            dimension = "TRAIT"
            subject_entity_ids = [PE_A]

        self.assertEqual(_merge_key(_Legacy()),
                         (frozenset({PE_A}), "text:会拉小提琴"))

    def test_effective_dimension_falls_back_safely(self):
        from persona.modules.trait_vocabulary import TraitVocabulary

        # 标签族解析不出（换掉了词表）+ dimension 落在不该有标签的维度
        # ⇒ 退回 BACKGROUND，不让背景描述顺着标签挤进 traits
        class _Odd:
            claim = "x"
            dimension = "BACKGROUND"
            subject_entity_ids = [PE_A]
            trait_tag = "music"

        self.assertEqual(
            _effective_dimension(_Odd(), TraitVocabulary()),
            "BACKGROUND")


# ==========================================================================
# 9. 装配层接线（D-280 在真实链路上真的接上了）
# ==========================================================================

class TestAssemblyWiring(unittest.TestCase):
    """抽取器是装配层用 `claim_extractor_from_settings` 造的，词表必须走到那里。

    只在测试里 `ProviderClaimExtractor(vocabulary=...)` 不算接通——真实链路
    的 schema 由装配层决定。那里不传词表 ⇒ schema 没有 `trait_tag` ⇒ 模型
    压根收不到标签清单 ⇒ D-280 在真实链路上等于没上线。
    """

    def _stack(self, *, trait_vocabulary=None):
        import tempfile
        from pathlib import Path as _Path

        from persona.config import test_settings
        from persona.core.result import is_ok
        from persona.orchestrator import StackOptions, build_business_stack
        from persona.providers.mock import FakeProvider
        from persona.storage.sqlite import SqliteStorage

        tmp = tempfile.TemporaryDirectory(prefix="p48-wire-")
        self.addCleanup(tmp.cleanup)
        storage = SqliteStorage(_Path(tmp.name) / "wire.db")
        self.addCleanup(storage.close)
        settings = test_settings(
            offline_only=False, provider_name="fake",
            **{"model.provider": "deepseek",
               "secrets.provider_api_key": "sk-test-not-a-real-key"})
        built = build_business_stack(
            settings, new_call_context(project_id=PROJECT, actor="test"),
            storage=storage, chat_provider=FakeProvider({}),
            options=StackOptions(build_search_provider=False,
                                 trait_vocabulary=trait_vocabulary))
        self.assertTrue(is_ok(built), "装配失败")
        return built.value

    def test_default_stack_has_the_vocabulary_on_both_sides(self):
        """默认（不传）= L1 词表启用，且**抽取侧与编排侧是同一本账**。"""
        stack = self._stack()
        schema = build_extraction_schema(stack.claim_extractor.vocabulary)
        self.assertIn("trait_tag",
                      schema["properties"]["claims"]["items"]["properties"])
        self.assertEqual(len(stack.claim_extractor.vocabulary.all_tags()), 31)
        # 编排器默认词表与抽取器默认词表层数一致（同一个来源）
        self.assertEqual(len(stack.orchestrator.trait_vocabulary.all_tags()), 31)

    def test_explicit_empty_vocabulary_turns_the_schema_back_off(self):
        """显式空词表 = 关掉：`None` 与空词表是两件事（同 business.py 纪律）。"""
        stack = self._stack(trait_vocabulary=TraitVocabulary())
        self.assertFalse(stack.claim_extractor.vocabulary)
        self.assertNotIn(
            "trait_tag",
            build_extraction_schema(stack.claim_extractor.vocabulary)
            ["properties"]["claims"]["items"]["properties"])

    def test_custom_vocabulary_reaches_both_sides(self):
        """调用方注入的自定义词表**同时**到抽取器与编排器。

        只到一边会静默分叉：抽取器放行的 L2 标签，归并侧解析不出族。
        """
        custom = TraitVocabulary.build(domain=[
            TraitTag("mantra_muttering", "SPEECH_STYLE", "口头禅式念叨",
                     layer=LAYER_DOMAIN)])
        stack = self._stack(trait_vocabulary=custom)
        self.assertIs(stack.claim_extractor.vocabulary, custom)
        self.assertIs(stack.orchestrator.trait_vocabulary, custom)
        self.assertIn("SPEECH_STYLE",
                      stack.claim_extractor.vocabulary.enabled_dimensions())

    def test_request_metadata_reports_the_tag_state(self):
        """metadata 是 dry-run 与真实运行**同源**的证据（D-272 纪律）。"""
        stack = self._stack()
        # `build_request` 是静态入口，词表要显式传——真实 `extract()` 传的
        # 就是抽取器自己那本（`self.vocabulary`），这里照抄同一口径。
        request = stack.claim_extractor.build_request(
            [], [], vocabulary=stack.claim_extractor.vocabulary)
        self.assertEqual(request.metadata["trait_tag_enabled"], "true")
        self.assertEqual(request.metadata["trait_tags"], "31")
        self.assertEqual(request.metadata["prompt_version"], PROMPT_VERSION)


if __name__ == "__main__":
    unittest.main()
