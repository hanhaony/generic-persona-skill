#!/usr/bin/env python
"""D-291：`material_key` 只作线索，**归属以引文逐字出现的位置为准**。

R4.7 的探针暴露了一类新错误：模型的**主体挂对了**（探针句讲宝玉 ⇒ 挂宝玉），
但 `material_key` 填成了**另一份材料**——因为那份材料讲同一件事（百度百科宝玉页
也有"厌恶四书和八股文……叫做禄蠹"）。旧逻辑据此判定"引文不在该材料里"并拒收，
把一条**引文逐字可核验、主体正确**的证据丢掉了。

本文件锁四件事：

1. 引文在**声明的材料**里找不到、但在**别的材料**里逐字存在 ⇒ 改归属放行 +
   `MATERIAL_KEY_CORRECTED`，`source_id` / `locator` 全部指向真正的那份材料；
2. 引文在**多份**材料里都出现 ⇒ 按第一份归属 + `MATERIAL_KEY_AMBIGUOUS`（如实记录）；
3. 引文**哪儿都没有** ⇒ 仍然拒收（"真伪造"不能因为这次放宽而漏进来）；
4. **没有引文**的候选不做改归属（没有可判定的依据，不自作聪明）。

第 5 类是 R4.7 原样复现：探针句逐字在中青报黛玉页，模型把 `material_key` 填成
宝玉页，最终必须**落在注入的那份材料上**。
"""

from __future__ import annotations

import json
import unittest

from persona.modules.claim_validation import (
    ISSUE_MATERIAL_KEY_AMBIGUOUS,
    ISSUE_MATERIAL_KEY_CORRECTED,
    PENDING_SOURCE_METADATA_MISMATCH,
    REJECT_QUOTE_NOT_IN_MATERIAL,
    VERDICT_OK,
    validate_model_payload,
)
from persona.core.types import SourceMaterial

#: R4.7 实际注入的那句话（逐字）
PROBE = "贾宝玉自幼厌恶科举功名，把读书上进的人称作禄蠹，从不肯走仕途经济那条路。"

#: 黛玉侧那份（探针注入处）—— 中青报全文的替身
DAI_TEXT = (
    "林黛玉言辞尖酸刻薄、喜欢怼人。\n\n"
    "这里既有尖酸刻薄的一面，也有弱不禁风的一面。\n"
    + PROBE
)

#: 宝玉百科页 —— 语义高度相似，但**没有**上面那句的逐字文本
BAO_TEXT = (
    "贾宝玉喜欢诗词曲赋之类性情文学，厌恶四书和八股文，批判程朱理学，"
    "把那些追逐科举考试、仕途经济的封建文人叫做“禄蠹”。"
)


def _material(*, key: str, source_id: str, title: str, url: str,
              snippet: str, subjects: list[str]) -> SourceMaterial:
    return SourceMaterial(
        source_id=source_id, title=title, url=url, canonical_key=key,
        snippet=snippet, subject_entity_ids=list(subjects),
    )


DAI = _material(
    key="cyol_dai", source_id="src_cyol", title="为什么是林黛玉？",
    url="http://m.cyol.com/gb/articles/2022-06/14/content_6pqlqcjxW.html",
    snippet=DAI_TEXT, subjects=["pe_dai", "pe_bao"])
BAO = _material(
    key="baike_bao", source_id="src_baike_bao", title="贾宝玉_百度百科",
    url="https://baike.baidu.com/item/%E8%B4%BE%E5%AE%9D%E7%8E%89/59563",
    snippet=BAO_TEXT, subjects=["pe_bao"])


def _claim(**kw) -> dict:
    base = {
        "key": "c18",
        "material_key": "baike_bao",          # ← 模型自报（错的那份）
        "claim": PROBE,
        "tier": "VERBATIM",
        "dimension": "TRAIT",
        "subject_entity_ids": ["pe_bao"],     # ← 主体是对的
        "confidence": 0.9,
        "verbatim_quote": PROBE,
    }
    base.update(kw)
    return base


def _validate(claims: list[dict], materials=None):
    return validate_model_payload(
        json.dumps({"claims": claims}, ensure_ascii=False),
        materials=list(materials or [DAI, BAO]))


class TestRelocationByQuote(unittest.TestCase):
    """引文在哪儿，来源就是哪儿。"""

    def test_quote_found_in_another_material_is_relocated_not_rejected(self):
        """D-291 核心：自报材料里没有引文，但在黛玉页里逐字存在 ⇒ 改归属放行。"""
        result = _validate([_claim()])
        self.assertEqual(result.verdict, VERDICT_OK)
        self.assertEqual(len(result.accepted), 1, "不该再拒收")
        claim = result.accepted[0]
        self.assertEqual(claim.material_key, "cyol_dai")
        self.assertEqual(claim.source_id, "src_cyol")
        self.assertIn(ISSUE_MATERIAL_KEY_CORRECTED, claim.issues)
        # 定位必须指向**真正的那份材料**，且切片就是引文本身
        start, end = claim.locator["start"], claim.locator["end"]
        self.assertEqual(DAI_TEXT[start:end], PROBE)

    def test_relocation_is_reported_at_batch_level(self):
        """改归属必须**可见**：静默改来源比拒收更危险。"""
        result = _validate([_claim()])
        joined = " ".join(result.issues)
        self.assertIn("MATERIAL_KEY", joined.upper())
        self.assertIn("baike_bao", joined)
        self.assertIn("cyol_dai", joined)

    def test_probe_claim_lands_on_the_injected_material(self):
        """R4.7 原样复现：探针句必须落在**注入的那份**（黛玉侧），主体仍是宝玉。

        这一条同时锁住"主体"和"材料"两个维度：
        - 主体不许因为材料在黛玉侧就改成黛玉；
        - 材料不许因为模型自报宝玉页就记成宝玉页。
        """
        result = _validate([_claim(subject_entity_ids=["pe_bao"])])
        claim = result.accepted[0]
        self.assertEqual(claim.subject_entity_ids, ["pe_bao"])
        self.assertEqual(claim.material_key, "cyol_dai")
        self.assertEqual(claim.source_id, "src_cyol")

    def test_quote_anywhere_else_still_rejects(self):
        """引文哪儿都没有 ⇒ 仍然拒收（放宽的只是"材料写错"，不是"引文造假"）。"""
        result = _validate([_claim(verbatim_quote="这句话在两份材料里都不存在。")])
        self.assertEqual(len(result.accepted), 0)
        self.assertEqual(result.rejected[0].reason, REJECT_QUOTE_NOT_IN_MATERIAL)

    def test_quote_in_multiple_materials_is_marked_ambiguous(self):
        """两份材料都含这句 ⇒ 按第一份归属，但标 AMBIGUOUS，不假装确定。"""
        twin = _material(
            key="twin", source_id="src_twin", title="另一份也引了这句",
            url="https://example.invalid/twin", snippet="前言。\n" + PROBE,
            subjects=["pe_bao"])
        result = _validate([_claim(key="c1")], materials=[BAO, DAI, twin])
        claim = result.accepted[0]
        self.assertIn(ISSUE_MATERIAL_KEY_AMBIGUOUS, claim.issues)
        self.assertNotIn(ISSUE_MATERIAL_KEY_CORRECTED, claim.issues)
        self.assertIn(claim.material_key, {"cyol_dai", "twin"})

    def test_correctly_declared_material_is_untouched(self):
        """声明对了就一个字都不改（不能把正常路径也染上告警）。"""
        result = _validate([_claim(material_key="cyol_dai")])
        claim = result.accepted[0]
        self.assertEqual(claim.material_key, "cyol_dai")
        self.assertNotIn(ISSUE_MATERIAL_KEY_CORRECTED, claim.issues)
        self.assertNotIn(ISSUE_MATERIAL_KEY_AMBIGUOUS, claim.issues)

    def test_no_quote_means_no_relocation(self):
        """没有引文就没有判定依据：保持模型自报的材料，不自作聪明。"""
        result = _validate([_claim(
            tier="PROFILE_BACKGROUND", dimension="BACKGROUND",
            verbatim_quote=None, claim="贾宝玉是荣国府的二公子。")])
        claim = result.accepted[0]
        self.assertEqual(claim.material_key, "baike_bao")
        self.assertEqual(claim.source_id, "src_baike_bao")
        self.assertNotIn(ISSUE_MATERIAL_KEY_CORRECTED, claim.issues)

    def test_relocated_claim_with_forged_url_goes_pending(self):
        """改归属后，模型自报的 url 若仍指向旧材料 ⇒ 按"自报元数据不符"待审核。

        顺序很重要：先定真实来源，**再**比对自报元数据，否则会拿旧材料去比。
        """
        result = _validate([_claim(url=BAO.url, source_title=BAO.title)])
        self.assertEqual(len(result.accepted), 0)
        pending = result.pending[0]
        self.assertEqual(pending.reason, PENDING_SOURCE_METADATA_MISMATCH)
        self.assertIn("url", pending.detail["mismatched"])

    def test_relocated_claim_with_matching_url_is_accepted(self):
        """模型连 url 都填的是**真来源**的（自相矛盾但可核验）⇒ 放行。"""
        result = _validate([_claim(url=DAI.url, source_title=DAI.title)])
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.accepted[0].source_id, "src_cyol")

    def test_unknown_material_key_still_rejects(self):
        """key 本身不在本次材料里 ⇒ 仍按 UNKNOWN_MATERIAL 拒收（不猜）。"""
        from persona.modules.claim_validation import REJECT_UNKNOWN_MATERIAL
        result = _validate([_claim(material_key="nowhere")])
        self.assertEqual(result.rejected[0].reason, REJECT_UNKNOWN_MATERIAL)


if __name__ == "__main__":
    unittest.main()
