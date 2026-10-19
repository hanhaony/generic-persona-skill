"""D-D：`locator.field` 必须如实反映材料性质（正文 vs 检索摘要）。

**缺陷**：`claim_validation.py` 曾把 `locator` 写死成 `{"field": "snippet", ...}`。
第二次受控真跑抓回了 3 份正文、2 份过门禁进材料流，但库里 24 条证据的定位
**全是 `snippet`**——`persona evidence` 因此把"正文第 1816 字符"显示成
`定位 snippet[1816:1848]`（摘要不可能那么长，标签是错的）。

后果不是难看，是**证据不可回溯**：过不了正文门禁的摘要证据（tier 更低）与
实打实的正文证据在输出里长得一模一样，用户无从判断可信度。

**修法**：材料究竟是摘要还是正文，`MaterialText.quality_status` 早就判过了
（门禁结论），写 locator 时照抄即可——不重新猜。

注意一个反直觉的地方：`materials_from_extracts()` 只交 excerpt 不交全文，
**正文摘录同样装在 `snippet` 字段里**，所以不能靠"字段名是不是 snippet"判断，
唯一可靠的判据是 `quality_status`。本文件第一条测试就是钉死这个认知。

全部离线：不联网、不写库、不调模型。
"""

from __future__ import annotations

import json
import unittest

from persona.core.enums import EvidenceDimension, EvidenceTier
from persona.modules.claim_validation import (
    QUALITY_SUMMARY_ONLY,
    QUALITY_VALID,
    MaterialText,
    build_material_views,
    validate_model_payload,
)
from persona.modules.research import SourceMaterial
from persona.modules.claim_validation import find_quote_span

KEY_SUMMARY = "key_summary_only"
KEY_CONTENT = "key_full_content"
KEY_SUMMARY_BARE = "key_summary_bare"

#: 引文在两份材料里的**偏移刻意不同**，这样偏移本身也能佐证归属。
TEXT_SUMMARY = "检索摘要：雾港灯塔由阿雾看守，制度见于设定集。"
TEXT_CONTENT = "抓取正文前面还有一段很长的铺垫文字：雾港灯塔由阿雾看守，写于第三版。"
#: 不含引文的摘要——用于造"模型报错材料"的场景（D-291 重定位）。
TEXT_SUMMARY_BARE = "检索摘要：雾港的制度见于设定集，看守安排另有记载。"


def _material(*, key: str, source_id: str, snippet: str, quality: str) -> SourceMaterial:
    return SourceMaterial(
        source_id=source_id,
        title=f"{source_id} 的标题",
        url=f"https://example.invalid/{source_id}",
        canonical_key=key,
        snippet=snippet,
        subject_entity_ids=["pe_a"],
        quality_status=quality,
    )


MATERIAL_SUMMARY = _material(key=KEY_SUMMARY, source_id="src_summary",
                             snippet=TEXT_SUMMARY, quality=QUALITY_SUMMARY_ONLY)
MATERIAL_CONTENT = _material(key=KEY_CONTENT, source_id="src_content",
                             snippet=TEXT_CONTENT, quality=QUALITY_VALID)
MATERIAL_UNDECLARED = _material(key="key_undeclared", source_id="src_undeclared",
                                snippet=TEXT_CONTENT, quality="")
MATERIAL_SUMMARY_BARE = _material(key=KEY_SUMMARY_BARE, source_id="src_bare",
                                  snippet=TEXT_SUMMARY_BARE,
                                  quality=QUALITY_SUMMARY_ONLY)

QUOTE = "雾港灯塔由阿雾看守"


def _payload(material_key: str, quote: str = QUOTE) -> str:
    return json.dumps({"claims": [{
        "key": "m1",
        "material_key": material_key,
        "claim": "阿雾看守雾港灯塔",
        "tier": str(EvidenceTier.VERBATIM),
        "dimension": str(EvidenceDimension.BACKGROUND),
        "subject_entity_ids": ["pe_a"],
        "confidence": 0.7,
        "verbatim_quote": quote,
    }]}, ensure_ascii=False)


def _locator_of(materials: list[SourceMaterial], material_key: str):
    result = validate_model_payload(_payload(material_key), materials=materials)
    assert result.accepted, f"应当被接受，实际 issues={result.issues}"
    return result.accepted[0]


class TestLocatorFieldReflectsMaterialKind(unittest.TestCase):
    """核心两条：正文 → `content`，摘要 → `snippet`。"""

    def test_valid_material_is_located_in_content(self):
        claim = _locator_of([MATERIAL_CONTENT], KEY_CONTENT)
        self.assertEqual(claim.locator["field"], "content")
        self.assertEqual(claim.source_id, "src_content")

    def test_summary_only_material_is_located_in_snippet(self):
        claim = _locator_of([MATERIAL_SUMMARY], KEY_SUMMARY)
        self.assertEqual(claim.locator["field"], "snippet")
        self.assertEqual(claim.source_id, "src_summary")

    def test_both_kinds_are_distinguishable_in_the_same_run(self):
        """同一次抽取里两种材料并存时，产出的定位必须能区分开。"""
        claims = validate_model_payload(
            json.dumps({"claims": [
                {"key": "a", "material_key": KEY_SUMMARY,
                 "claim": "阿雾看守灯塔（摘要）", "tier": "VERBATIM",
                 "dimension": "BACKGROUND", "subject_entity_ids": ["pe_a"],
                 "confidence": 0.7, "verbatim_quote": QUOTE},
                {"key": "b", "material_key": KEY_CONTENT,
                 "claim": "阿雾看守灯塔（正文）", "tier": "VERBATIM",
                 "dimension": "BACKGROUND", "subject_entity_ids": ["pe_a"],
                 "confidence": 0.7, "verbatim_quote": QUOTE},
            ]}, ensure_ascii=False),
            materials=[MATERIAL_SUMMARY, MATERIAL_CONTENT])
        by_key = {c.key: c for c in claims.accepted}
        self.assertEqual(by_key["a"].locator["field"], "snippet")
        self.assertEqual(by_key["b"].locator["field"], "content")


class TestUndeclaredQualityStaysConservative(unittest.TestCase):
    """门禁结论缺失时按**低可信**那一类记：宁可低估，不虚增。"""

    def test_undeclared_is_not_promoted_to_content(self):
        claim = _locator_of([MATERIAL_UNDECLARED], "key_undeclared")
        self.assertEqual(claim.locator["field"], "snippet")

    def test_undeclared_and_summary_only_are_not_the_same_thing(self):
        """两者都写 `snippet`，但来源方的信息量不同——注释里写明了，这里钉住取值。"""
        views = build_material_views([MATERIAL_SUMMARY, MATERIAL_UNDECLARED])
        self.assertEqual(views[0].quality_status, QUALITY_SUMMARY_ONLY)
        self.assertEqual(views[1].quality_status, "")


class TestSpanStillPointsAtTheQuote(unittest.TestCase):
    """改的是 `field`，`start/end` 的语义不能跟着漂——切片仍须逐字等于摘录。"""

    def test_slice_equals_quote_for_content(self):
        claim = _locator_of([MATERIAL_CONTENT], KEY_CONTENT)
        start, end = claim.locator["start"], claim.locator["end"]
        self.assertEqual(TEXT_CONTENT[start:end], QUOTE)

    def test_slice_equals_quote_for_summary(self):
        claim = _locator_of([MATERIAL_SUMMARY], KEY_SUMMARY)
        start, end = claim.locator["start"], claim.locator["end"]
        self.assertEqual(TEXT_SUMMARY[start:end], QUOTE)

    def test_two_kinds_yield_different_offsets(self):
        """同一句引文在两份材料里的偏移不同 → 偏移本身也能佐证归属。"""
        a = _locator_of([MATERIAL_SUMMARY], KEY_SUMMARY).locator
        b = _locator_of([MATERIAL_CONTENT], KEY_CONTENT).locator
        self.assertNotEqual((a["start"], a["end"]), (b["start"], b["end"]))


class TestRelocationUsesTheFinalMaterial(unittest.TestCase):
    """D-291：模型报错材料时按**引文**改归属——`field` 必须跟着新归属走。

    最容易漏的一条：如果只按"模型自报的 key"取 quality_status，引文被改判到
    正文材料后，定位仍会按摘要材料写成 `snippet`。
    """

    def test_field_follows_the_relocated_material(self):
        # 模型自称引文来自"不含这句话"的摘要材料，而它只在正文材料里逐字出现。
        claim = _locator_of([MATERIAL_SUMMARY_BARE, MATERIAL_CONTENT],
                            KEY_SUMMARY_BARE)
        self.assertEqual(claim.source_id, "src_content")     # 归属已改正
        self.assertEqual(claim.locator["field"], "content")  # field 同步跟着改
        self.assertEqual(TEXT_CONTENT[claim.locator["start"]:claim.locator["end"]],
                         QUOTE)

    def test_relocation_reverse_direction_also_holds(self):
        """反向：模型报正文材料，引文只在摘要材料里 → 改判后 field 变 `snippet`。"""
        quote_only_in_summary = "检索摘要：" + QUOTE
        result = validate_model_payload(
            _payload(KEY_CONTENT, quote=quote_only_in_summary),
            materials=[MATERIAL_SUMMARY, MATERIAL_CONTENT])
        self.assertTrue(result.accepted, f"issues={result.issues}")
        claim = result.accepted[0]
        self.assertEqual(claim.source_id, "src_summary")
        self.assertEqual(claim.locator["field"], "snippet")


class TestQualityStatusIsTheOnlyReliableCriterion(unittest.TestCase):
    """钉死那个反直觉事实：**不能靠字段名判断**材料是摘要还是正文。"""

    def test_both_materials_carry_their_text_in_the_snippet_field(self):
        """正文材料也把文本装在 `snippet` 里（`materials_from_extracts` 只交 excerpt）。"""
        views = {v.key: v for v in build_material_views(
            [MATERIAL_SUMMARY, MATERIAL_CONTENT])}
        self.assertIn(QUOTE, views[KEY_SUMMARY].text)
        self.assertIn(QUOTE, views[KEY_CONTENT].text)

    def test_distinguishing_needs_quality_status(self):
        """**同一段文本**，只换 quality_status → 只有后者能决定 field。"""
        shared = TEXT_CONTENT
        summary_view = MaterialText(
            key=KEY_SUMMARY, source_id="src_summary", title="t", url="u",
            text=shared, quality_status=QUALITY_SUMMARY_ONLY)
        content_view = MaterialText(
            key=KEY_CONTENT, source_id="src_content", title="t", url="u",
            text=shared, quality_status=QUALITY_VALID)
        self.assertEqual(summary_view.text, content_view.text)      # 文本一模一样
        self.assertNotEqual(summary_view.quality_status,
                            content_view.quality_status)            # 只有这个能分

    def test_quote_span_is_found_in_either_material(self):
        self.assertIsNotNone(find_quote_span(QUOTE, TEXT_SUMMARY))
        self.assertIsNotNone(find_quote_span(QUOTE, TEXT_CONTENT))


if __name__ == "__main__":
    unittest.main()
