#!/usr/bin/env python
"""D-289：引文核验的"折空白"必须能容忍 PDF/硬换行，但不能放过改写。

R4.6 实测暴露的问题：`_norm_ws` 早先把连续空白**压成一个空格**，于是

    材料（PDF 抽取）：…… 想生气[空格]就生气的 ……
    模型引文        ：…… 想生气就生气的 ……

两边"折空白"后仍然差一个空格 ⇒ `find` 返回 -1 ⇒ 判成 `QUOTE_NOT_IN_MATERIAL`。
三轮累计 4 次拒收**全是这种误拒**（R4.4 2/2、R4.5 1/1、R4.6 1/1）。

本文件钉两件事：

1. **正向**：换行、句内空格、全角空格、制表符、`\r\n`、PDF 断行，
   只要字符按顺序完整出现，就必须命中，且 `locator` 指向原文真实区间；
2. **反向**：改写一个词、删一个字、调换顺序、跨句拼接（中间删掉一句），
   **仍然必须拒收**——丢空白只放过空白，不放过任何字符层面的差异。
"""

from __future__ import annotations

import json
import unittest

from persona.modules.claim_validation import (
    REJECT_QUOTE_NOT_IN_MATERIAL,
    find_quote_span,
    validate_model_payload,
)
from persona.modules.extraction import SourceMaterial


def _material(snippet: str, *, key: str = "key_d289") -> SourceMaterial:
    return SourceMaterial(
        source_id="src_d289", title="D-289 夹具", url="https://example.invalid/d289",
        canonical_key=key, snippet=snippet, subject_entity_ids=["pe_a"],
        quality_status="VALID")


def _payload(quote: str, claim: str = "林黛玉言辞尖刻") -> str:
    return json.dumps({"claims": [{
        "key": "m1",
        "material_key": "key_d289",
        "claim": claim,
        "tier": "VERBATIM",
        "dimension": "TRAIT",
        "subject_entity_ids": ["pe_a"],
        "confidence": 0.9,
        "verbatim_quote": quote,
    }]}, ensure_ascii=False)


def _strip(text: str) -> str:
    return "".join(text.split())


#: R4.6 的真实材料形态：PDF 抽取在"想生气"与"就生气"之间留了一个空格
PDF_TEXT = "林黛玉也是如此，她任情任性、真诚单纯，她的一生是想说就说、想生气 就生气的，她不担心也不怕得罪人。"
#: R4.4/R4.5 的真实材料形态：对联前是一个空行
COUPLET_TEXT = "宝玉道：“如此说，匾上则莫若‘蘅芷清芬’四字。对联则是：\n\n吟成豆蔻才犹艳，睡足酴醾梦也香。"
#: R4.5 的另一条真实材料形态：跨行引用
LINEBREAK_TEXT = "雾港灯塔\n  与看守制度的官方设定。"


class TestWhiteSpaceIsFoldedNotCollapsed(unittest.TestCase):
    """正向：空白差异必须命中，且定位落在原文真实区间。"""

    def assert_matches(self, quote: str, text: str) -> None:
        span = find_quote_span(quote, text)
        self.assertIsNotNone(span, f"应命中但没命中：{quote!r}")
        start, end = span
        # 定位必须真的包住那句原文：去掉空白后完全一致
        self.assertEqual(_strip(text[start:end]), _strip(quote))

    def test_pdf_inline_space(self):
        """R4.6 实测：PDF 把"想生气 就生气"断开一个空格。"""
        self.assert_matches(
            "她任情任性、真诚单纯，她的一生是想说就说、想生气就生气的，她不担心也不怕得罪人。",
            PDF_TEXT)

    def test_blank_line_before_couplet(self):
        """R4.4/R4.5 实测：引文里没有那个空行。"""
        self.assert_matches(
            "宝玉道：“如此说，匾上则莫若‘蘅芷清芬’四字。对联则是：吟成豆蔻才犹艳，睡足酴醾梦也香。",
            COUPLET_TEXT)

    def test_hard_wrap_with_indent(self):
        self.assert_matches("雾港灯塔 与看守制度", LINEBREAK_TEXT)

    def test_quote_may_drop_whitespace_entirely(self):
        self.assert_matches("雾港灯塔与看守制度", LINEBREAK_TEXT)

    def test_fullwidth_and_tab_and_crlf(self):
        text = "第一段\r\n　第二段\t第三段 第四段"
        self.assert_matches("第一段第二段第三段第四段", text)
        self.assert_matches("第一段 第二段 第三段 第四段", text)

    def test_multiple_spaces_inside_sentence(self):
        text = "她    说了    一句    话"
        self.assert_matches("她说了 一句话", text)

    def test_span_points_at_original_text_not_normalized_copy(self):
        """折空白只用于匹配，`locator` 必须指向**原文**下标。"""
        span = find_quote_span("她任情任性、真诚单纯", PDF_TEXT)
        self.assertIsNotNone(span)
        start, end = span
        self.assertEqual(PDF_TEXT[start:end], "她任情任性、真诚单纯")


class TestStrictnessIsPreserved(unittest.TestCase):
    """反向：字符层面的任何差异都必须仍然拒收。"""

    def test_substituted_word_is_rejected(self):
        quote = "她任情任性、真诚单纯，她的一生是想说就说、想发火就发火的，她不担心也不怕得罪人。"
        self.assertIsNone(find_quote_span(quote, PDF_TEXT))

    def test_deleted_character_is_rejected(self):
        quote = "她任情任性、真诚单纯，她的一生是想说就说、想生气就生气，不担心也不怕得罪人。"
        self.assertIsNone(find_quote_span(quote, PDF_TEXT))

    def test_reordered_clause_is_rejected(self):
        quote = "她不担心也不怕得罪人，她任情任性、真诚单纯"
        self.assertIsNone(find_quote_span(quote, PDF_TEXT))

    def test_eliding_an_interior_sentence_is_rejected(self):
        """跨句拼接（把中间一句删掉再接起来）不是"原文摘录"。"""
        quote = "她任情任性、真诚单纯，她不担心也不怕得罪人"
        self.assertIsNone(find_quote_span(quote, PDF_TEXT))

    def test_quote_spanning_two_materials_is_rejected(self):
        self.assertIsNone(find_quote_span("雾港灯塔 与看守制度 的官方设定第一段",
                                          COUPLET_TEXT))

    def test_validation_layer_accepts_folded_quote_and_rejects_paraphrase(self):
        material = _material(PDF_TEXT)
        ok = validate_model_payload(
            _payload("她任情任性、真诚单纯，她的一生是想说就说、想生气就生气的，"
                     "她不担心也不怕得罪人。"),
            materials=[material])
        self.assertEqual(len(ok.accepted), 1)
        self.assertEqual(ok.rejected, [])

        bad = validate_model_payload(
            _payload("她任性放纵、心思单纯，一辈子想说什么就说什么"), materials=[material])
        self.assertEqual(bad.accepted, [])
        self.assertEqual([r.reason for r in bad.rejected], [REJECT_QUOTE_NOT_IN_MATERIAL])

    def test_empty_and_blank_quotes_are_rejected(self):
        self.assertIsNone(find_quote_span("", PDF_TEXT))
        self.assertIsNone(find_quote_span("   \n\t ", PDF_TEXT))


class TestRealRejectionSamplesAreNowAdmitted(unittest.TestCase):
    """把 R4.4 / R4.5 / R4.6 的 4 条误拒原样钉成夹具。

    夹具用的是**当时的真实引文与真实材料片段**：换一段文本就锁不住这个缺陷了。
    """

    SAMPLES = [
        # (轮次, 引文, 材料, 当时在哪份材料)
        ("R4.4 黛玉任情任性（sinoss PDF）",
         "她任情任性、真诚单纯，她的一生是想说就说、想生气就生气的，她不担心也不怕得罪人",
         PDF_TEXT),
        ("R4.4 宝玉题匾（维基文库第 17 回）",
         "宝玉道：“如此说，匾上则莫若‘蘅芷清芬’四字。对联则是：吟成豆蔻才犹艳，睡足酴醾梦也香。",
         COUPLET_TEXT),
        ("R4.5 宝玉题匾（同一份材料，同一条引文）",
         "宝玉道：“如此说，匾上则莫若‘蘅芷清芬’四字。对联则是：吟成豆蔻才犹艳，睡足酴醾梦也香。",
         COUPLET_TEXT),
        ("R4.6 黛玉任情任性（同 R4.4，句末多一个句号）",
         "她任情任性、真诚单纯，她的一生是想说就说、想生气就生气的，她不担心也不怕得罪人。",
         PDF_TEXT),
    ]

    def test_all_four_historical_rejections_now_match(self):
        for label, quote, text in self.SAMPLES:
            with self.subTest(sample=label):
                span = find_quote_span(quote, text)
                self.assertIsNotNone(span, f"{label}：修复后仍应命中")
                self.assertEqual(_strip(text[span[0]:span[1]]), _strip(quote))


if __name__ == "__main__":
    unittest.main()
