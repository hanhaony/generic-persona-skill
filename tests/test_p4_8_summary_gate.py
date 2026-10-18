"""P4.8 整改：D-6（摘要通道的一等状态）+ D-7（相关性判定的字形归一）。

两条都来自 **R4.1 换角复跑**的实测（`docs/P4_8_R4_REAL_SEARCH.md`）：

- **D-6 结构性死局**：检索摘要的 `quality_status` 是空 ⇒ 严格模式
  （`require_gate_verdict=True`）把它一律判 `MATERIAL_UNDECLARED` 拒收。
  而 `max_extracts=1` 的单条正文抓取一旦失败，材料流里就只剩摘要 ⇒ 全部拒收
  ⇒ 抽取器一次不调 ⇒ **0 证据是必然的**，与目标是否真实存在无关。
  R4.1 实测：7 条来源**全部真实相关**，仍然 0 证据。
  修法（用户拍板方案 ①）：新增 `SUMMARY_ONLY` 一等状态，摘要**亮结论**并放行，
  **门禁只负责"亮结论"，不负责"判死刑"**；最终可用性由证据层的 `tier` 决定。
- **D-7 误杀**：词表用简体「福尔摩斯」，页面写繁体「福爾摩斯」⇒ 字面不匹配
  ⇒ 本轮最相关的一条（801 字、明确讲福尔摩斯与华生）被判"无关"拒收。
  修法：判定前**两侧都做字形归一**（`fold_variants`）。

全部离线：临时 SQLite + 本地替身，不触网、不调真实模型、不产生费用。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.result import unwrap
from persona.core.types import SourceMaterial
from persona.modules.extraction import (
    REJECT_MATERIAL_NOT_GATE_PASSED,
    REJECT_MATERIAL_UNDECLARED,
    TAG_FROM_SUMMARY,
    TAG_MODEL_EXTRACT,
    WARNING_MATERIAL_SUMMARY_ONLY,
    admit_materials,
)
from persona.modules.extract_quality import (
    QUALITY_EMPTY,
    QUALITY_LOW,
    QUALITY_SUMMARY_ONLY,
    QUALITY_VALID,
    text_mentions_any,
)
from persona.modules.research import (
    REASON_IRRELEVANT,
    WARNING_IRRELEVANT_REJECTED,
)
from persona.modules.text_variants import fold_terms, fold_variants
from persona.providers.base import SearchResult
from persona.providers.mock import FakeProvider

from tests.test_research_module import ResearchFixture

PROJECT = "prj_p48_d6"


def _material(**kw) -> SourceMaterial:
    base = dict(
        source_id="src_1", title="福尔摩斯探案集", url="https://example.invalid/a",
        canonical_key="key_a", snippet="福尔摩斯与华生住在贝克街。",
        source_kind="WEB_PAGE", subject_entity_ids=["pe_a"],
    )
    base.update(kw)
    return SourceMaterial(**base)


SUMMARY = _material(quality_status=QUALITY_SUMMARY_ONLY)
VALID = _material(canonical_key="key_b", quality_status=QUALITY_VALID)
EMPTY = _material(canonical_key="key_c", quality_status=QUALITY_EMPTY)
LOW = _material(canonical_key="key_d", quality_status=QUALITY_LOW)
UNDECLARED = _material(canonical_key="key_e", quality_status="")


# ==========================================================================
# D-6 通道一：检索摘要
# ==========================================================================


class SummaryChannelTest(unittest.TestCase):
    def test_summary_is_admitted_in_strict_mode(self):
        """这是 D-6 的核心：摘要亮了结论，**不该**被严格模式判死刑。"""
        admitted, rejected = admit_materials([SUMMARY], require_gate_verdict=True)
        self.assertEqual([m.canonical_key for m in admitted], [SUMMARY.canonical_key])
        self.assertEqual(rejected, [])

    def test_summary_is_not_treated_as_undeclared(self):
        """`SUMMARY_ONLY` 与 `""`（信息缺失）必须区别对待。"""
        admitted, rejected = admit_materials([SUMMARY], require_gate_verdict=True)
        self.assertEqual([r.reason for r in rejected], [])
        admitted, rejected = admit_materials([UNDECLARED], require_gate_verdict=True)
        self.assertEqual(admitted, [])
        self.assertEqual([r.reason for r in rejected], [REJECT_MATERIAL_UNDECLARED])

    def test_research_declares_summary_only_on_snippet_materials(self):
        """Research 交出的**检索摘要**必须带 `SUMMARY_ONLY`（不是留空）。"""
        tmp = tempfile.mkdtemp(prefix="gps-p48-d6-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fx = ResearchFixture(tmp)
        self.addCleanup(fx.close)
        hero = fx.entity("福尔摩斯")
        svc = fx.service(FakeProvider())
        ctx = fx.ctx

        ranked = unwrap(svc.filter_and_rank(
            ctx,
            [SearchResult(title="福尔摩斯探案集", url="https://example.invalid/a",
                          canonical_key="a", snippet="福尔摩斯与华生住在贝克街。")],
            targets=[fx.target(hero, "福尔摩斯")]))
        self.assertEqual(len(ranked.accepted), 1)

        registered = unwrap(svc.register(ctx, ranked))
        self.assertEqual(len(registered.source_ids), 1)
        materials = registered.materials
        self.assertEqual(len(materials), 1)
        self.assertEqual(materials[0].quality_status, QUALITY_SUMMARY_ONLY,
                         "摘要必须亮出自己的结论，留空会让严格模式误杀整批材料")

    def test_summary_derived_evidence_is_tagged(self):
        """来自摘要的证据要**可查**（`from-summary` 标签），但不改变可用性。"""
        from persona.modules.claim_validation import ValidatedClaim

        validated = ValidatedClaim(key="m1", material_key="key_a",
                                   claim="福尔摩斯住在贝克街",
                                   tier="PROFILE_BACKGROUND",
                                   dimension="background",
                                   subject_entity_ids=["pe_a"],
                                   confidence=0.6, issues=[])
        from persona.modules.extraction import _tags_for
        tags = _tags_for(validated, SUMMARY)
        self.assertIn(TAG_MODEL_EXTRACT, tags)
        self.assertIn(TAG_FROM_SUMMARY, tags)
        # 正文来源**不加**这个标签——标签说的是来源形态，不是好坏。
        self.assertNotIn(TAG_FROM_SUMMARY, _tags_for(validated, VALID))


# ==========================================================================
# D-6 通道二：抓取正文
# ==========================================================================


class BodyChannelTest(unittest.TestCase):
    def test_valid_body_is_admitted(self):
        admitted, rejected = admit_materials([VALID], require_gate_verdict=True)
        self.assertEqual([m.canonical_key for m in admitted], [VALID.canonical_key])
        self.assertEqual(rejected, [])

    def test_failed_body_is_still_rejected(self):
        """放行摘要**不等于**放松门禁：判废的正文仍然进不来。"""
        admitted, rejected = admit_materials(
            [EMPTY, LOW], require_gate_verdict=True)
        self.assertEqual(admitted, [])
        self.assertEqual([r.reason for r in rejected],
                         [REJECT_MATERIAL_NOT_GATE_PASSED] * 2)

    def test_mixed_batch_splits_by_verdict(self):
        admitted, rejected = admit_materials(
            [VALID, SUMMARY, LOW, UNDECLARED], require_gate_verdict=True)
        self.assertEqual(sorted(m.canonical_key for m in admitted),
                         sorted([VALID.canonical_key, SUMMARY.canonical_key]))
        self.assertEqual(sorted(r.reason for r in rejected),
                         sorted([REJECT_MATERIAL_NOT_GATE_PASSED,
                                 REJECT_MATERIAL_UNDECLARED]))


# ==========================================================================
# D-6 通道三：抓取失败退回摘要（R4.1 现场复现）
# ==========================================================================


class FetchFailedFallbackTest(unittest.TestCase):
    """R4.1 现场：Tavily 抓不动百度百科 ⇒ 退回摘要 ⇒ 此前全批拒收。"""

    def test_fallback_to_summary_no_longer_empties_the_material_flow(self):
        materials = [SUMMARY]
        admitted, rejected = admit_materials(materials, require_gate_verdict=True)
        self.assertEqual(len(admitted), 1,
                         "抓取失败后摘要仍要进抽取器——否则必然 0 证据（D-6）")
        self.assertEqual(rejected, [])

    def test_extractor_is_called_for_summary_materials(self):
        """端到端：只有摘要时，模型**会被调用**（此前一次都不调）。"""
        from persona.modules.extraction import ProviderClaimExtractor

        class Stub:
            offline = True

            def __init__(self):
                self.calls = 0

            def chat(self, request, ctx=None):  # noqa: D102
                self.calls += 1
                from persona.core.result import Ok
                from persona.providers.base import ChatResponse, UsageReport
                import json
                payload = {"claims": [{
                    "key": "m1", "material_key": "key_a",
                    "claim": "福尔摩斯住在贝克街",
                    "tier": "PROFILE_BACKGROUND", "dimension": "BACKGROUND",
                    "subject_entity_ids": ["pe_a"], "confidence": 0.6}]}
                return Ok(ChatResponse(
                    text=json.dumps(payload),
                    usage=UsageReport(requests=1, tokens_in=10, tokens_out=10)))

        stub = Stub()
        extractor = ProviderClaimExtractor(stub, require_gate_verdict=True)
        ctx = new_call_context(project_id=PROJECT, actor="tests")
        outcome = unwrap(extractor.extract(ctx, [SUMMARY]))
        self.assertEqual(stub.calls, 1)
        self.assertEqual(len(outcome.claims), 1)
        codes = [w.code for w in outcome.warnings]
        self.assertIn(WARNING_MATERIAL_SUMMARY_ONLY, codes,
                      "放行摘要必须留痕，不能静默通过")
        self.assertIn(TAG_FROM_SUMMARY, outcome.claims[0].tags)


# ==========================================================================
# D-7：字形归一（繁简双向）
# ==========================================================================


class VariantFoldTest(unittest.TestCase):
    def test_traditional_page_matches_simplified_terms(self):
        """R4.1 现场：简体词表 vs 繁体页面（此前误杀）。"""
        self.assertTrue(text_mentions_any(
            "福爾摩斯的懸疑世界 說到福爾摩斯探案系列，除了主人翁神探夏洛克 • 福爾摩斯",
            ["福尔摩斯", "华生"]))

    def test_simplified_page_matches_traditional_terms(self):
        """反向也要成立——只折一侧是不行的。"""
        self.assertTrue(text_mentions_any("福尔摩斯探案集", ["福爾摩斯"]))

    def test_folding_is_idempotent_on_simplified(self):
        self.assertEqual(fold_variants("福尔摩斯华生"), "福尔摩斯华生")

    def test_unrelated_still_rejected(self):
        """归一**不是**放宽：无关内容仍然拒收。"""
        self.assertFalse(text_mentions_any(
            "汽车前雾灯与后雾灯的区别", ["福尔摩斯", "华生"]))

    def test_fold_terms_preserves_order_and_drops_dupes(self):
        self.assertEqual(fold_terms(["福爾摩斯", "华生", "福尔摩斯"]),
                         ["福尔摩斯", "华生"])

    def test_variant_map_has_no_duplicate_keys(self):
        """重复键会让"哪个生效"取决于书写顺序——是隐患，必须在测试里堵住。"""
        from persona.modules.text_variants import VARIANT_MAP
        raw = VARIANT_MAP
        self.assertEqual(len(raw), len(set(raw)))

    def test_filter_accepts_traditional_candidate(self):
        """端到端：繁体候选不再进 `rejected`。"""
        tmp = tempfile.mkdtemp(prefix="gps-p48-d7-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fx = ResearchFixture(tmp)
        self.addCleanup(fx.close)
        hero = fx.entity("福尔摩斯")
        svc = fx.service(FakeProvider())
        trad = SearchResult(
            title="福爾摩斯的懸疑世界",
            url="https://example.invalid/trad",
            canonical_key="trad",
            snippet="說到福爾摩斯探案系列，除了主人翁神探夏洛克 • 福爾摩斯，"
                    "另一位超重要的角色就是華生。")
        ranked = unwrap(svc.filter_and_rank(
            fx.ctx, [trad], targets=[fx.target(hero, "福尔摩斯")]))
        self.assertEqual(len(ranked.accepted), 1)
        self.assertEqual(ranked.rejected, [])
        self.assertNotIn(WARNING_IRRELEVANT_REJECTED,
                         [w.code for w in ranked.warnings])

    def test_noise_is_still_rejected_after_folding(self):
        tmp = tempfile.mkdtemp(prefix="gps-p48-d7b-")
        self.addCleanup(shutil.rmtree, tmp, True)
        fx = ResearchFixture(tmp)
        self.addCleanup(fx.close)
        hero = fx.entity("福尔摩斯")
        svc = fx.service(FakeProvider())
        noise = SearchResult(title="汽车前雾灯", url="https://auto.invalid/1",
                             canonical_key="n1", snippet="雾灯用于雨雾天气。")
        ranked = unwrap(svc.filter_and_rank(
            fx.ctx, [noise], targets=[fx.target(hero, "福尔摩斯")]))
        self.assertEqual(ranked.accepted, [])
        self.assertTrue(all(r.reason.startswith(REASON_IRRELEVANT)
                            for r in ranked.rejected))
