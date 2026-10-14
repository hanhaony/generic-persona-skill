"""G-2：来源材料 → 候选 claim 的抽取（`persona/modules/extraction.py`）。

全部为**合成虚构素材**（AC-16 / AC-17），不触网、不调真实模型、不用密钥。

覆盖：
  1. 候选保留 source_id / 原文摘录 / 文本定位 / 对象关联 / 时间 / 分类 / 抽取状态
  2. 三种 claim 的机械区分（原文直接支持 / 归纳 / 系统推断）
  3. 无效摘录（声称的"原文"不在材料里）被拒收且如实记录
  4. 空材料、无可抽取主张、未知等级、越界置信度的处理
  5. 冲突保留：相似但不同的说法**不合并**
  6. Provider 抽象：模型返回非法 JSON / Provider 报错 / 缺 material_key 的处理
  7. 抽取器**拿不到** `Evidence`（结构上无法绕过校验直接标记"已验证"）
  8. 静态检查（依赖方向 / 禁字段 / 无厂商 SDK）
"""

from __future__ import annotations

import ast
import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    CanonLevel,
    EvidenceDimension,
    EvidenceTier,
    Stability,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, UsageDelta, is_err, is_ok, unwrap
from persona.core.types import SourceMaterial, UsageReport
from persona.modules.evaluation import EvaluationService
from persona.modules.extraction import (
    EXTRACT_METHOD_MOCK,
    EXTRACT_METHOD_MODEL,
    REJECT_CONFIDENCE_OUT_OF_RANGE,
    REJECT_EMPTY_CLAIM,
    REJECT_EMPTY_MATERIAL,
    REJECT_INFERENCE_WITH_QUOTE,
    REJECT_NO_SUBJECT,
    REJECT_QUOTE_NOT_IN_MATERIAL,
    REJECT_UNKNOWN_DIMENSION,
    REJECT_UNKNOWN_TIER,
    TAG_MOCK_FIXTURE,
    CandidateClaim,
    ClaimExtractorPort,
    MockClaimExtractor,
    ProviderClaimExtractor,
    fuzzy_from_text,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.providers.mock import OfflineGuardProvider, network_blocked
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_extract"

# --------------------------------------------------------------------------
# 固定测试材料：虚构地名 / 虚构作品，不含任何真实人物专属设定
# --------------------------------------------------------------------------

SNIPPET_FOG = "雾港灯塔与看守制度的官方设定。"
SNIPPET_LAMP = "主创访谈，谈及阿雾与阿灯的相遇。"

MATERIAL_FOG = SourceMaterial(
    source_id="src_fog", title="雾港设定资料集",
    url="https://example.invalid/mist-harbor", canonical_key="key_mist_harbor",
    snippet=SNIPPET_FOG, published_at="2021-05-04",
    source_kind="OFFICIAL_MATERIAL", subject_entity_ids=["pe_a"])
MATERIAL_LAMP = SourceMaterial(
    source_id="src_lamp", title="星灯访谈录",
    url="https://example.invalid/starlamp", canonical_key="key_starlamp_interview",
    snippet=SNIPPET_LAMP, published_at="2022-01-10",
    source_kind="INTERVIEW", subject_entity_ids=["pe_b"])
MATERIAL_EMPTY = SourceMaterial(
    source_id="src_empty", title="无正文的材料", canonical_key="key_empty",
    snippet="", subject_entity_ids=["pe_a"])


def _claim(**kw) -> CandidateClaim:
    base = {
        "claim": "阿雾负责看守雾港灯塔",
        "tier": EvidenceTier.PROFILE_BACKGROUND,
        "dimension": EvidenceDimension.BACKGROUND,
        "subject_entity_ids": ["pe_a"],
        "confidence": 0.7,
    }
    base.update(kw)
    return CandidateClaim(**base)


# ==========================================================================
# Mock 抽取器
# ==========================================================================


class TestMockExtractor(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")

    # ---------------------------------------------------------- 基本产出

    def test_extracts_candidates_from_material(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim()]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(len(outcome.claims), 1)
        self.assertEqual(outcome.method, EXTRACT_METHOD_MOCK)

    def test_candidate_keeps_traceability_fields(self):
        """目标 3：source_id / 摘录 / 定位 / 对象 / 时间 / 分类 都保留。"""
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim(
            key="c1", verbatim_quote="雾港灯塔", locator={"field": "snippet"},
            stated_at=fuzzy_from_text("2021-05-04"), stability=Stability.REPEATED,
            canon_level=CanonLevel.CANON, tier=EvidenceTier.VERBATIM)]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        got = outcome.claims[0]
        self.assertEqual(got.source_id, "src_fog")          # 来源追溯
        self.assertEqual(got.verbatim_quote, "雾港灯塔")      # 原文摘录
        self.assertEqual(got.locator, {"field": "snippet"})  # 文本定位
        self.assertEqual(got.subject_entity_ids, ["pe_a"])   # 对象关联
        self.assertEqual(got.stated_at.raw, "2021-05-04")    # 时间信息
        self.assertEqual(str(got.tier), str(EvidenceTier.VERBATIM))   # 证据分类
        self.assertEqual(str(got.dimension), str(EvidenceDimension.BACKGROUND))
        self.assertEqual(str(got.stability), str(Stability.REPEATED))
        self.assertEqual(str(got.canon_level), str(CanonLevel.CANON))
        self.assertEqual(got.key, "c1")

    def test_mock_output_is_tagged_as_test_material(self):
        """目标 7：Mock 数据必须清楚标识为测试材料。"""
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim()]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertIn(TAG_MOCK_FIXTURE, outcome.claims[0].tags)

    def test_unknown_material_yields_no_claim_and_warns(self):
        """材料没有对应 fixture → 不编造 claim，只给提示。"""
        extractor = MockClaimExtractor({})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(outcome.claims, [])
        self.assertTrue(any(w.code == "NO_CANDIDATES" for w in outcome.warnings))

    def test_no_materials_is_reported_not_faked(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim()]})
        outcome = unwrap(extractor.extract(self.ctx, []))
        self.assertEqual(outcome.claims, [])
        self.assertTrue(any(w.code == "NO_MATERIALS" for w in outcome.warnings))

    # ---------------------------------------------------------- 拒收（不静默丢弃）

    def test_empty_material_is_rejected(self):
        extractor = MockClaimExtractor({"key_empty": [_claim()]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_EMPTY]))
        self.assertEqual(outcome.claims, [])
        self.assertEqual([r.reason for r in outcome.rejected], [REJECT_EMPTY_MATERIAL])

    def test_quote_not_in_material_is_rejected(self):
        """目标 3/4：声称的"原文摘录"在材料里找不到 → 拒收，不得伪装成原话。"""
        extractor = MockClaimExtractor({"key_mist_harbor": [
            _claim(verbatim_quote="这句话根本不在材料里", tier=EvidenceTier.VERBATIM)]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(outcome.claims, [])
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_QUOTE_NOT_IN_MATERIAL])

    def test_valid_verbatim_quote_is_accepted(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [
            _claim(verbatim_quote="雾港灯塔", tier=EvidenceTier.VERBATIM)]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(len(outcome.claims), 1)
        self.assertEqual(outcome.rejected, [])

    def test_empty_claim_is_rejected(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim(claim="  ")]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([r.reason for r in outcome.rejected], [REJECT_EMPTY_CLAIM])

    def test_missing_subject_is_rejected(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [
            _claim(subject_entity_ids=[])]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([r.reason for r in outcome.rejected], [REJECT_NO_SUBJECT])

    def test_unknown_tier_is_rejected(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim(tier="SUPER_TRUE")]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([r.reason for r in outcome.rejected], [REJECT_UNKNOWN_TIER])

    def test_unknown_dimension_is_rejected(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim(dimension="VIBE")]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_UNKNOWN_DIMENSION])

    def test_confidence_out_of_range_is_rejected(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [_claim(confidence=1.5)]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_CONFIDENCE_OUT_OF_RANGE])

    def test_inference_may_not_carry_a_verbatim_quote(self):
        """目标 4：推断不得伪装成原文引用。"""
        extractor = MockClaimExtractor({"key_mist_harbor": [
            _claim(tier=EvidenceTier.INFERENCE, verbatim_quote="雾港灯塔",
                   derivation_keys=["c0"])]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(outcome.claims, [])
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_INFERENCE_WITH_QUOTE])

    def test_inference_without_quote_is_accepted_as_candidate(self):
        extractor = MockClaimExtractor({"key_mist_harbor": [
            _claim(tier=EvidenceTier.INFERENCE, derivation_keys=["c0"])]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(len(outcome.claims), 1)
        self.assertEqual(outcome.claims[0].derivation_keys, ["c0"])

    # ---------------------------------------------------------- 冲突保留（不合并）

    def test_similar_but_different_claims_are_not_merged(self):
        """目标 6：说法不同就各自保留，绝不因相似合并掉差异。"""
        extractor = MockClaimExtractor({"key_mist_harbor": [
            _claim(claim="阿雾每晚点灯", canon_level=CanonLevel.CANON),
            _claim(claim="阿雾隔晚点灯", canon_level=CanonLevel.NON_CANON),
        ]})
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([c.claim for c in outcome.claims],
                         ["阿雾每晚点灯", "阿雾隔晚点灯"])
        self.assertEqual([str(c.canon_level) for c in outcome.claims],
                         [str(CanonLevel.CANON), str(CanonLevel.NON_CANON)])

    def test_same_text_from_two_sources_stays_two_candidates(self):
        """同一说法来自两个来源 → 两条独立候选（来源独立性保留）。"""
        extractor = MockClaimExtractor({
            "key_mist_harbor": [_claim(claim="阿雾每晚点灯",
                                       canon_level=CanonLevel.CANON)],
            "key_starlamp_interview": [_claim(claim="阿雾每晚点灯",
                                              canon_level=CanonLevel.NON_CANON)],
        })
        outcome = unwrap(extractor.extract(
            self.ctx, [MATERIAL_FOG, MATERIAL_LAMP]))
        self.assertEqual(len(outcome.claims), 2)
        self.assertEqual({c.source_id for c in outcome.claims},
                         {"src_fog", "src_lamp"})

    # ---------------------------------------------------------- 协议一致性

    def test_mock_satisfies_the_port(self):
        self.assertIsInstance(MockClaimExtractor({}), ClaimExtractorPort)

    def test_extract_is_deterministic(self):
        fixtures = {"key_mist_harbor": [_claim()]}
        first = unwrap(MockClaimExtractor(fixtures).extract(self.ctx, [MATERIAL_FOG]))
        second = unwrap(MockClaimExtractor(fixtures).extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual([c.claim for c in first.claims],
                         [c.claim for c in second.claims])


# ==========================================================================
# Provider 抽象（模型抽取的契约与错误处理；本阶段不接真实模型）
# ==========================================================================


class StubChatProvider:
    """记录 prompt、返回固定文本，可注入失败。"""

    def __init__(self, text: str = "", *, fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.chat_calls = 0
        self.last_request: ChatRequest | None = None

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.chat_calls += 1
        self.last_request = req
        if self.fail:
            return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE, "注入失败",
                                 trace_ref=ctx.trace_ref))
        return Ok(ChatResponse(text=self.text, finish_reason="stop",
                               model_id="stub", usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class TestProviderExtractorContract(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")

    def test_provider_error_is_propagated_verbatim(self):
        extractor = ProviderClaimExtractor(StubChatProvider(fail=True))
        with network_blocked():
            result = extractor.extract(self.ctx, [MATERIAL_FOG])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)

    def test_malformed_json_is_an_error_not_empty_success(self):
        """返回非法 JSON → 报错，绝不谎报"抽取成功但零结果"。"""
        extractor = ProviderClaimExtractor(StubChatProvider("这不是 JSON"))
        with network_blocked():
            result = extractor.extract(self.ctx, [MATERIAL_FOG])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)

    def test_valid_model_output_is_mapped_to_candidates(self):
        payload = ('{"claims":[{"key":"m1","material_key":"key_mist_harbor",'
                   '"claim":"阿雾负责看守雾港灯塔","tier":"PROFILE_BACKGROUND",'
                   '"dimension":"BACKGROUND","subject_entity_ids":["pe_a"],'
                   '"confidence":0.6}]}')
        provider = StubChatProvider(payload)
        extractor = ProviderClaimExtractor(provider)
        with network_blocked():
            outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(len(outcome.claims), 1)
        self.assertEqual(outcome.claims[0].source_id, "src_fog")
        self.assertEqual(outcome.method, EXTRACT_METHOD_MODEL)
        self.assertIsNotNone(provider.last_request)
        self.assertEqual(provider.last_request.response_format, "json_schema")

    def test_quote_not_in_material_is_rejected_for_model_output(self):
        payload = ('{"claims":[{"material_key":"key_mist_harbor","claim":"胡说",'
                   '"tier":"VERBATIM","dimension":"BACKGROUND",'
                   '"subject_entity_ids":["pe_a"],"verbatim_quote":"不在材料里"}]}')
        extractor = ProviderClaimExtractor(StubChatProvider(payload))
        with network_blocked():
            outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(outcome.claims, [])
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_QUOTE_NOT_IN_MATERIAL])

    def test_missing_material_key_is_rejected(self):
        """模型没说清来自哪份材料 → 无法溯源，拒收。"""
        payload = ('{"claims":[{"claim":"无源之说","tier":"PROFILE_BACKGROUND",'
                   '"dimension":"BACKGROUND","subject_entity_ids":["pe_a"]}]}')
        extractor = ProviderClaimExtractor(StubChatProvider(payload))
        with network_blocked():
            outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))
        self.assertEqual(outcome.claims, [])
        self.assertEqual(len(outcome.rejected), 1)

    def test_no_materials_does_not_call_the_model(self):
        provider = StubChatProvider("{}")
        extractor = ProviderClaimExtractor(provider)
        with network_blocked():
            outcome = unwrap(extractor.extract(self.ctx, []))
        self.assertEqual(provider.chat_calls, 0)
        self.assertEqual(outcome.claims, [])

    def test_model_extractor_uses_only_the_provider_abstraction(self):
        """D-054：只经 `Provider`，且不允许绕过离线护栏。"""
        provider = StubChatProvider('{"claims":[]}')
        guarded = OfflineGuardProvider(provider)
        extractor = ProviderClaimExtractor(guarded)
        with network_blocked():
            self.assertTrue(is_ok(extractor.extract(self.ctx, [MATERIAL_FOG])))


# ==========================================================================
# 边界：抽取器拿不到 Evidence
# ==========================================================================


class TestExtractorCannotBypassEvidence(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        self.tmp = tempfile.mkdtemp(prefix="gps-extract-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_extractor_has_no_evidence_entry_point(self):
        """目标 5：抽取器结构上就拿不到 `Evidence`，无法绕过校验直接标记证据。

        用 AST 判"代码里"有没有证据写入入口——docstring 里提到
        `Evidence.add_evidence`（说明该交给谁）不算违规。
        """
        source = Path("persona/modules/extraction.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("evidence", str(node.module or ""),
                                 "抽取器不得 import evidence 模块")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("evidence", alias.name)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, ("add_evidence", "record_inference"),
                                 "抽取器不得调用证据登记入口")

    def test_module_passes_static_checks(self):
        storage = SqliteStorage(Path(self.tmp) / "extract.db")
        self.addCleanup(storage.close)
        checks = unwrap(EvaluationService(storage).run_static_checks(self.ctx))
        self.assertTrue(checks["passed"], checks)


if __name__ == "__main__":
    unittest.main()
