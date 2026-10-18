#!/usr/bin/env python
"""D-295：强判别探针必须**两行分别判**（主体 + `material_key`），任一错即 FAIL。

# R4.7 的盲区

旧判定先按 `source_id == 探针材料` 过滤证据，再在**过滤后的列表**里找探针句。
于是：

- 探针句被改归属到别的材料 ⇒ 它不在列表里 ⇒ 判"模型没选中这句话"（WARN）；
- 探针句被核验拒收 ⇒ 同理 ⇒ 也是 WARN。

**"该源里没有提到宝玉的句子"被当成了"模型没抽到"**，两者的处置完全不同
（前者要查 D-291，后者只要换探针句），却被压成同一个结论。

# 本文件锁什么

1. 定位探针句时**不按 `source_id` 过滤**（否则永远只能判出"对"）；
2. 主体、`material_key` 各判一行，**任一错即 FAIL**；
3. 故意把 `material_key` 填成别的源 ⇒ **必须 FAIL**（这是用户点名要的用例）；
4. 全库都找不到探针句 ⇒ WARN（本轮没测到），不能冒充通过或失败；
5. 注入的那份材料没进来源表 / `--probe-about` 不是本次目标 ⇒ WARN。
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_p4_8_real_search.py"
_spec = importlib.util.spec_from_file_location("e2e_p48_probe", _SCRIPT)
assert _spec and _spec.loader
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)

DAI = "pe_dai"
BAO = "pe_bao"
CYOL = "http://m.cyol.com/gb/articles/2022-06/14/content_6pqlqcjxW.html"
BAIKE = "https://baike.baidu.com/item/%E8%B4%BE%E5%AE%9D%E7%8E%89/59563"

PROBE_TEXT = "贾宝玉自幼厌恶科举功名，把读书上进的人称作禄蠹，从不肯走仕途经济那条路。"


class _Target:
    def __init__(self, name: str, entity_id: str) -> None:
        self.display_name = name
        self.entity_id = entity_id


TARGETS = [_Target("林黛玉", DAI), _Target("贾宝玉", BAO)]

SOURCES = [
    {"id": "src_cyol", "source_id": "src_cyol", "url": CYOL},
    {"id": "src_baike", "source_id": "src_baike", "url": BAIKE},
]

PROBE_INFO = {
    "target": "林黛玉", "about": "贾宝玉", "text": PROBE_TEXT,
    "url": CYOL, "index": -1, "placement": "extract_fulltext",
}


def _evidence(*, source_id: str = "src_cyol", subjects: list[str] | None = None,
              claim: str | None = None) -> dict:
    return {
        "claim": claim if claim is not None
        else "贾宝玉自幼厌恶科举功名，把读书上进的人称作禄蠹，不肯走仕途经济之路。",
        "source_id": source_id,
        "subject_entity_ids": list(subjects or [BAO]),
        "tier": "VERBATIM",
    }


def _judge(evidence: list[dict], *, probe_info: dict | None = None,
           sources: list[dict] | None = None) -> tuple[str, list[str]]:
    return e2e._judge_probe(
        evidence=evidence, sources=sources or SOURCES,
        probe_info=probe_info or PROBE_INFO, targets=TARGETS)


class TestHappyPath(unittest.TestCase):
    def test_subject_and_material_both_right_is_ok(self):
        verdict, lines = _judge([_evidence()])
        self.assertEqual(verdict, "OK")
        text = "\n".join(lines)
        self.assertIn("[OK ] 主体", text)
        self.assertIn("[OK ] material_key", text)

    def test_each_check_gets_its_own_line(self):
        """两行分开报——合成一行就又会"看到一个 OK 就以为全对"。"""
        _, lines = _judge([_evidence()])
        subject_lines = [ln for ln in lines if "主体：" in ln]
        material_lines = [ln for ln in lines if "material_key：" in ln]
        self.assertEqual(len(subject_lines), 1)
        self.assertEqual(len(material_lines), 1)


class TestMaterialKeyMustBeChecked(unittest.TestCase):
    def test_wrong_material_key_fails_even_when_subject_is_right(self):
        """**用户点名用例**：主体对、`material_key` 填错 ⇒ 必须 FAIL。

        R4.7 的真实形态：探针句被归到百度宝玉页。旧判定按 `source_id` 过滤，
        这条证据根本不在候选里，于是报 WARN（"模型没选中"）——错得离谱。
        """
        verdict, lines = _judge([_evidence(source_id="src_baike")])
        self.assertEqual(verdict, "FAIL")
        text = "\n".join(lines)
        self.assertIn("[OK ] 主体", text)
        self.assertIn("[FAIL] material_key", text)
        self.assertIn("D-291", text)

    def test_wrong_material_key_line_shows_actual_and_expected(self):
        _, lines = _judge([_evidence(source_id="src_baike")])
        material = next(ln for ln in lines if "material_key：" in ln)
        self.assertIn("src_baike", material)      # 实际指向
        self.assertIn("src_cyol", material)       # 应指向
        self.assertIn(BAIKE, material)


class TestSubjectMustBeChecked(unittest.TestCase):
    def test_wrong_subject_fails(self):
        verdict, lines = _judge([_evidence(subjects=[DAI])])
        self.assertEqual(verdict, "FAIL")
        self.assertIn("[FAIL] 主体", "\n".join(lines))
        self.assertIn("D-288", "\n".join(lines))

    def test_both_wrong_reports_both(self):
        verdict, lines = _judge([_evidence(source_id="src_baike", subjects=[DAI])])
        self.assertEqual(verdict, "FAIL")
        text = "\n".join(lines)
        self.assertIn("[FAIL] 主体", text)
        self.assertIn("[FAIL] material_key", text)
        self.assertIn("同时错", text)


class TestNotMeasured(unittest.TestCase):
    def test_missing_probe_sentence_is_warn(self):
        """全库找不到 ⇒ WARN（本轮没测到），不许当通过也不许当失败。"""
        verdict, lines = _judge([_evidence(claim="林黛玉有弱不禁风的一面。")])
        self.assertEqual(verdict, "WARN")
        text = "\n".join(lines)
        self.assertIn("WARN", text)
        self.assertIn("没测到", text)
        # 必须承认"拒收"与"没选中"在证据层同形，不能只报后一种
        self.assertIn("被核验拒收", text)

    def test_similarity_threshold_rejects_unrelated(self):
        verdict, _ = _judge([{"claim": "林黛玉是绛珠仙草转世。",
                              "source_id": "src_cyol",
                              "subject_entity_ids": [BAO]}])
        self.assertEqual(verdict, "WARN")

    def test_probe_material_not_in_sources_is_warn(self):
        verdict, lines = _judge([_evidence()], sources=[SOURCES[1]])
        self.assertEqual(verdict, "WARN")
        self.assertIn("没进来源表", "\n".join(lines))

    def test_unknown_probe_about_is_warn(self):
        info = dict(PROBE_INFO, about="薛宝钗")
        verdict, lines = _judge([_evidence()], probe_info=info)
        self.assertEqual(verdict, "WARN")
        self.assertIn("不是本次目标", "\n".join(lines))


class TestLocatesAcrossSources(unittest.TestCase):
    def test_picks_the_closest_evidence_not_the_first(self):
        """库里同时有别的宝玉句时，要挑**最像探针**的那条，而不是第一条。"""
        unrelated = _evidence(claim="贾宝玉重情不重礼。", source_id="src_baike")
        verdict, lines = _judge([unrelated, _evidence()])
        self.assertEqual(verdict, "OK")
        self.assertIn("相似度", "\n".join(lines))


if __name__ == "__main__":
    unittest.main()
