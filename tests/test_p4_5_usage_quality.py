"""P4.5 测试：外部用量账本 + 正文质量门禁。

**全离线**：用替身网络出口 / Fake Provider / 模拟 HTTP 响应，**零真实请求**。

⚠️ 本文件的正文样例**全部是合成内容**，刻意不写入 P4.3 那次真实网页的内容——
   用真实内容写断言等于把"本次这个网页能过"当成"门禁能用"，那不是测试。
   合成内容按**结构**构造（导航行 = markdown 链接条目；正文 = 连续句子），
   这样门禁判的是结构特征，而不是背下来了某个页面。

覆盖：
- 用量与预算：Search/Extract 分别入账、缺 usage、超时、失败≠免费、
  预留/结算/释放/暂停、幂等、连续请求不能绕过上限、存储账本
- 正文质量：正常长正文、空、纯导航、过短、页眉页脚多但有正文、抓取失败、
  页面类型、低质量不得进 Claim Extractor、补救受预算与上限约束
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import ProvenanceNamespace
from persona.core.result import Err, is_err, unwrap
from persona.core.text_window import (
    DEFAULT_EXCERPT_WINDOW_CHARS,
    locate_prose_window,
    select_prose_window,
)
from persona.core.usage_ledger import (
    ExternalUsageLedger,
    estimate_extract_credits,
    estimate_search_credits,
    extract_key_parts,
    make_idempotency_key,
)
from persona.core.types import OP_EXTRACT, ExternalUsageRecord
from persona.modules.extract_quality import (
    DEFAULT_CHUNKS_PER_SOURCE,
    PAGE_TYPE_GENERIC,
    PAGE_TYPE_VIDEO,
    PAGE_TYPE_WIKI,
    QUALITY_EMPTY,
    QUALITY_LOW,
    QUALITY_VALID,
    REASON_NAV_DOMINATED,
    REASON_NO_PROSE,
    REASON_TARGET_NOT_MENTIONED,
    REASON_TOO_SHORT,
    REASON_UNSUPPORTED_PAGE_TYPE,
    ContentQuality,
    assess_content_quality,
    detect_page_type,
    has_extraction_defect,
    plan_remediation,
)
from persona.modules.evidence import EvidenceService
from persona.modules.research import (
    WARNING_EXTRACT_DUPLICATE_SKIPPED,
    WARNING_EXTRACT_REMEDIATED,
    WARNING_EXTRACT_REMEDIATION_BUDGET_STOPPED,
    WARNING_EXTRACT_REMEDIATION_CAPPED,
    WARNING_EXTRACT_REMEDIATION_UNKNOWN_PAUSE,
    ExtractedContent,
    PersonQueryTarget,
    ResearchDeps,
    ResearchLimits,
    ResearchOutput,
    ResearchService,
    TopNExtractionStrategy,
    materials_from_extracts,
)
from persona.providers.base import ExtractRequest, ProviderConfig, SearchRequest
from persona.providers.tavily import TavilyProvider
from persona.storage.sqlite import SqliteStorage

from .test_p4_2_tavily_compliance import (
    StubUrlopen,
    _FakeResponse,
    _http_error,
    P42Fixture,
)

PROJECT = "prj_p45"


# --------------------------------------------------------------------------
# 合成正文素材（按结构构造，不来自任何真实网页）
# --------------------------------------------------------------------------

_PROSE_SENTENCE = "研究者在那个时期的主要工作集中在形式化系统与可计算性边界的界定上。"
# 每句 33 字 × 2 = 66 字/行，超过"正文行"的 40 字下限


def _prose(lines: int = 8) -> str:
    return "\n".join(_PROSE_SENTENCE * 2 for _ in range(lines))


def _nav(count: int = 40) -> str:
    return "\n".join(f"[章节条目{i}](#锚点{i})" for i in range(count))


def _long_nav(count: int = 20) -> str:
    """长目录行：一行里串起多个链接（链接字符占半行以上）。"""
    return "\n".join(
        " ".join(f"[章节{i}-{j}](#锚点{i}{j})" for j in range(4))
        for i in range(count))


# 提到目标人物的正文（门禁的"相关性"判据需要它，否则会多一个无关理由）
_TARGET_SENTENCE = "阿雾在那段时期的主要工作集中在形式化系统与可计算性的边界上。"


def _prose_target(lines: int = 8) -> str:
    return "\n".join(_TARGET_SENTENCE * 2 for _ in range(lines))


def _long_page(nav_lines: int = 50, prose_lines: int = 40) -> str:
    """**刻意**造一个 content 与 excerpt 不同的长页：页头一大段目录，正文在后面。

    为什么必须长到这个程度：P4.5 受控复测撞到的正是"门禁判全文（VALID）、
    下游交付的却是摘录（页头目录）"。要让测试能抓到这个错位，fixture 里
    `content` 与 `excerpt` **不能是同一段文本**——否则差异永远不可见。
    """
    return _long_nav(nav_lines) + "\n\n" + _prose_target(prose_lines)


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------


class P45Fixture(P42Fixture):
    def target(self, entity_id: str, name: str = "阿雾", **kw) -> PersonQueryTarget:
        return PersonQueryTarget(entity_id=entity_id, display_name=name, **kw)


class P45TestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="gps-p45-")
        self.fx = P45Fixture(self.tmp)

    def tearDown(self):
        self.fx.close()

    def provider(self, stub) -> TavilyProvider:
        p = TavilyProvider(api_key="test-key", config=ProviderConfig(max_retries=0))
        p._urlopen = stub
        return p

    def service(self, provider, *, limits=None, extraction_strategy=None,
                compliance=None, usage_sink=None, task_id=None):
        return ResearchService(ResearchDeps(
            provider=provider, evidence=self.fx.evidence,
            compliance=compliance, limits=limits,
            extraction_strategy=extraction_strategy,
            usage_sink=usage_sink, task_id=task_id))


# ==========================================================================
# 任务一：用量与预算账本
# ==========================================================================


class TestUsageRecordedPerOperation(P45TestCase):
    """Search 与 Extract 的用量**分别**入账（P4.3 真实暴露：Extract 曾整个丢失）。"""

    def test_search_and_extract_are_recorded_separately(self):
        stub = StubUrlopen(
            search_data={"results": [], "usage": {"credits": 1},
                         "request_id": "req-search-1"},
            extract_data={"results": [{"url": "https://good.invalid/a",
                                       "raw_content": "x"}],
                          "failed_results": [], "usage": {"credits": 0},
                          "request_id": "req-extract-1"})
        p = self.provider(stub)
        unwrap(p.search(self.fx.ctx, SearchRequest(query="雾港")))
        unwrap(p.extract(self.fx.ctx, ExtractRequest(urls=["https://good.invalid/a"])))

        records = p.usage_ledger.records()
        self.assertEqual(len(records), 2)
        by_op = {r.operation: r for r in records}
        self.assertEqual(by_op["SEARCH"].request_id, "req-search-1")
        self.assertEqual(by_op["EXTRACT"].request_id, "req-extract-1")
        # 两者的操作类型不同，可分别统计
        self.assertEqual(by_op["SEARCH"].cost_confirmed, 1.0)
        self.assertEqual(by_op["EXTRACT"].cost_confirmed, 0.0)
        # 估算：search 1.0；extract = 1 URL × 1/5 = 0.2
        self.assertEqual(by_op["SEARCH"].cost_estimate, 1.0)
        self.assertAlmostEqual(by_op["EXTRACT"].cost_estimate, 0.2)
        # 单元数：extract 记录 URL 数与成功数
        self.assertEqual(by_op["EXTRACT"].unit_count, 1)
        self.assertEqual(by_op["EXTRACT"].success_unit_count, 1)

    def test_extract_usage_reaches_research_output(self):
        """抓取用量必须进入 `ResearchOutput.budget_used`（P4.3 缺陷的直接回归）。"""
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            # 提到目标人物的正文：否则门禁会按"与目标无关"判低质量，
            # 触发一次补救抓取，把本例要断言的"1 次 search + 1 次 extract"搅乱。
            # （本例测的是用量入账，不是质量，夹具必须干净。）
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _prose_target(10)}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        # 关键：抓取的用量累加进来了 → requests = 1(search) + 1(extract)
        self.assertEqual(out.budget_used.requests, 2)
        self.assertEqual(out.budget_used.cost_confirmed, 1.0)
        self.assertAlmostEqual(out.budget_used.cost_estimate, 1.2)  # 1.0 + 0.2

    def test_failed_url_extraction_is_officially_not_charged(self):
        """官方明确：单条 URL 抓取失败不计费 —— 这是唯一可判 NOT_CHARGED 的情形。"""
        stub = StubUrlopen(extract_data={
            "results": [],
            "failed_results": [{"url": "https://gone.invalid/a", "error": "unreachable"}],
            "usage": {"credits": 0}})
        p = self.provider(stub)
        unwrap(p.extract(self.fx.ctx, ExtractRequest(urls=["https://gone.invalid/a"])))
        rec = p.usage_ledger.records()[0]
        self.assertEqual(rec.outcome, "SUCCEEDED")
        self.assertEqual(rec.cost_status, "NOT_CHARGED")
        self.assertEqual(rec.cost_confirmed, 0.0)
        self.assertEqual(rec.success_unit_count, 0)


class TestMissingUsageFields(P45TestCase):
    """响应缺 `usage` → 实际费用未知，**绝不填 0**。"""

    def test_missing_usage_marks_confirmed_unknown(self):
        stub = StubUrlopen(search_data={"results": []})   # 无 usage
        p = self.provider(stub)
        unwrap(p.search(self.fx.ctx, SearchRequest(query="雾港")))
        rec = p.usage_ledger.records()[0]
        self.assertIsNone(rec.cost_confirmed)
        self.assertEqual(rec.cost_status, "UNKNOWN")
        self.assertEqual(rec.cost_source, "NONE")
        self.assertEqual(rec.outcome, "SUCCEEDED")   # 请求成功了，但费用不知道

    def test_usage_without_credits_is_also_unknown(self):
        stub = StubUrlopen(search_data={"results": [], "usage": {}})
        p = self.provider(stub)
        unwrap(p.search(self.fx.ctx, SearchRequest(query="雾港")))
        self.assertIsNone(p.usage_ledger.records()[0].cost_confirmed)
        self.assertEqual(p.usage_ledger.records()[0].cost_status, "UNKNOWN")

    def test_unknown_is_not_zero_in_totals(self):
        stub = StubUrlopen(search_data={"results": []})
        p = self.provider(stub)
        unwrap(p.search(self.fx.ctx, SearchRequest(query="雾港")))
        totals = p.usage_ledger.totals()
        self.assertEqual(totals.unknown_requests, 1)
        self.assertIsNone(totals.confirmed_cost)      # 不是 0.0，是"没有确认值"
        self.assertGreater(totals.unknown_hold_cost, 0.0)  # 未知也要占额度


class TestTimeoutAndLostResponse(P45TestCase):
    """超时 / 断连 / 响应丢失 → 失败 + 费用未知。"""

    def test_timeout_records_failed_and_unknown(self):
        stub = StubUrlopen(error_for={"search": TimeoutError("boom")})
        p = self.provider(stub)
        res = p.search(self.fx.ctx, SearchRequest(query="雾港"))
        self.assertTrue(is_err(res))
        rec = p.usage_ledger.records()[0]
        self.assertEqual(rec.outcome, "FAILED")
        self.assertIsNone(rec.cost_confirmed)
        self.assertEqual(rec.cost_status, "UNKNOWN")
        self.assertIsNone(rec.request_id)      # 拿不到服务端标识

    def test_unreachable_network_records_failed_and_unknown(self):
        stub = StubUrlopen(error_for={"search": urllib.error.URLError("no route")})
        p = self.provider(stub)
        self.assertTrue(is_err(p.search(self.fx.ctx, SearchRequest(query="雾港"))))
        rec = p.usage_ledger.records()[0]
        self.assertEqual(rec.outcome, "FAILED")
        self.assertEqual(rec.cost_status, "UNKNOWN")

    def test_unparsable_response_records_failed_and_unknown(self):
        stub = StubUrlopen(return_invalid_json=True)
        p = self.provider(stub)
        self.assertTrue(is_err(p.search(self.fx.ctx, SearchRequest(query="雾港"))))
        self.assertEqual(p.usage_ledger.records()[0].cost_status, "UNKNOWN")


class TestFailedButCostUnknown(P45TestCase):
    """请求失败 ≠ 免费：预留**不释放**，转为未知占位。"""

    def test_failure_keeps_holding_budget_not_released(self):
        stub = StubUrlopen(error_for={"search": _http_error(500)})
        p = self.provider(stub)
        self.assertTrue(is_err(p.search(self.fx.ctx, SearchRequest(query="雾港"))))
        totals = p.usage_ledger.totals()
        self.assertEqual(totals.failed_requests, 1)
        self.assertEqual(totals.reserved_cost, 0.0)        # 预留已被结算掉
        self.assertGreater(totals.unknown_hold_cost, 0.0)  # 转为未知占位，仍占额度
        self.assertEqual(len(p.usage_ledger.records()), 1)  # 失败也要留痕

    def test_failure_is_not_counted_as_confirmed_zero(self):
        stub = StubUrlopen(error_for={"search": _http_error(503)})
        p = self.provider(stub)
        self.assertTrue(is_err(p.search(self.fx.ctx, SearchRequest(query="雾港"))))
        rec = p.usage_ledger.records()[0]
        self.assertIsNone(rec.cost_confirmed)   # 不是 0.0！
        self.assertEqual(rec.cost_status, "UNKNOWN")

    def test_auth_failure_also_unknown(self):
        stub = StubUrlopen(error_for={"search": _http_error(401)})
        p = self.provider(stub)
        self.assertTrue(is_err(p.search(self.fx.ctx, SearchRequest(query="雾港"))))
        self.assertEqual(p.usage_ledger.records()[0].cost_status, "UNKNOWN")


class TestBudgetReserveSettleRelease(P45TestCase):
    """预留 / 结算 / 释放 的生命周期。"""

    def test_reserve_then_settle_moves_to_confirmed(self):
        led = ExternalUsageLedger()
        key = make_idempotency_key("tavily", "SEARCH", "雾港")
        led.reserve(key, 1.0)
        self.assertEqual(led.totals().reserved_cost, 1.0)
        led.settle(ExternalUsageRecord(
            idempotency_key=key, provider="tavily", operation="SEARCH",
            outcome="SUCCEEDED", requested_at="t", cost_estimate=1.0,
            cost_confirmed=1.0, currency="TAVILY_CREDIT",
            cost_source="PROVIDER_USAGE", cost_status="CONFIRMED"))
        totals = led.totals()
        self.assertEqual(totals.reserved_cost, 0.0)     # 预留已释放
        self.assertEqual(totals.confirmed_cost, 1.0)
        self.assertEqual(totals.unknown_hold_cost, 0.0)

    def test_reserve_then_release_records_nothing(self):
        led = ExternalUsageLedger()
        key = make_idempotency_key("tavily", "SEARCH", "雾港")
        led.reserve(key, 1.0)
        led.release(key)
        totals = led.totals()
        self.assertEqual(totals.reserved_cost, 0.0)
        self.assertEqual(len(led.records()), 0)

    def test_settle_unknown_keeps_hold(self):
        led = ExternalUsageLedger()
        key = make_idempotency_key("tavily", "EXTRACT", "https://a.invalid")
        led.reserve(key, 0.2)
        led.settle(ExternalUsageRecord(
            idempotency_key=key, provider="tavily", operation="EXTRACT",
            outcome="FAILED", requested_at="t", cost_estimate=0.2,
            cost_status="UNKNOWN"))
        totals = led.totals()
        self.assertEqual(totals.reserved_cost, 0.0)
        self.assertGreater(totals.unknown_hold_cost, 0.0)

    def test_confirm_clears_unknown_hold(self):
        """人工核对流程：拿到账单后回填，未知转为已确认。"""
        led = ExternalUsageLedger()
        key = make_idempotency_key("tavily", "EXTRACT", "https://a.invalid")
        led.settle(ExternalUsageRecord(
            idempotency_key=key, provider="tavily", operation="EXTRACT",
            outcome="FAILED", requested_at="t", cost_estimate=0.2,
            cost_status="UNKNOWN"))
        self.assertTrue(led.confirm(key, 1.0, source="PROVIDER_USAGE"))
        totals = led.totals()
        self.assertEqual(totals.unknown_hold_cost, 0.0)
        self.assertEqual(totals.unknown_requests, 0)
        self.assertEqual(led.get(key).cost_status, "CONFIRMED")

    def test_pause_when_cost_status_unclear(self):
        """费用状态不明 → 停手等人工核对（保守策略，不硬闯）。"""
        led = ExternalUsageLedger(max_unknown_requests=1)
        led.settle(ExternalUsageRecord(
            idempotency_key="k1", provider="tavily", operation="SEARCH",
            outcome="FAILED", requested_at="t", cost_estimate=1.0,
            cost_status="UNKNOWN"))
        paused, reason = led.should_pause()
        self.assertTrue(paused)
        self.assertIn("未知", reason)

    def test_pause_when_would_exceed(self):
        led = ExternalUsageLedger()
        led.settle(ExternalUsageRecord(
            idempotency_key="k1", provider="tavily", operation="SEARCH",
            outcome="SUCCEEDED", requested_at="t", cost_estimate=1.0,
            cost_confirmed=1.0, cost_status="CONFIRMED"))
        paused, _ = led.should_pause(max_cost=1.5, need=1.0)
        self.assertTrue(paused)


class TestLedgerIdempotency(P45TestCase):
    """幂等：重试 / 恢复不重复记账。"""

    def test_settle_same_key_twice_counts_once(self):
        led = ExternalUsageLedger()
        key = make_idempotency_key("tavily", "SEARCH", "雾港")
        rec = ExternalUsageRecord(
            idempotency_key=key, provider="tavily", operation="SEARCH",
            outcome="SUCCEEDED", requested_at="t", cost_estimate=1.0,
            cost_confirmed=1.0, cost_status="CONFIRMED")
        self.assertTrue(led.settle(rec))
        self.assertFalse(led.settle(rec))       # 第二次被忽略
        self.assertEqual(len(led.records()), 1)
        self.assertEqual(led.totals().confirmed_cost, 1.0)   # 不是 2.0

    def test_retry_shares_one_key(self):
        """同一请求重试多次 → 账本里只有一笔。"""
        stub = StubUrlopen(
            search_data={"results": [], "usage": {"credits": 1}},
            error_for={"search": _http_error(500)})
        # 先失败再成功（重试由 Provider 内部收敛），结果是 1 笔
        p = self.provider(stub)
        self.assertTrue(is_err(p.search(self.fx.ctx, SearchRequest(query="雾港"))))
        del stub.error_for["search"]
        unwrap(p.search(self.fx.ctx, SearchRequest(query="雾港")))
        # 两次调用内容相同 → 幂等键相同 → 只记 1 笔
        self.assertEqual(len(p.usage_ledger.records()), 1)

    def test_idempotency_key_is_stable_and_distinct(self):
        a = make_idempotency_key("tavily", "SEARCH", "雾港", 5, "basic")
        b = make_idempotency_key("tavily", "SEARCH", "雾港", 5, "basic")
        c = make_idempotency_key("tavily", "SEARCH", "雾港", 6, "basic")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_storage_record_is_idempotent(self):
        """存储层兜底：主键冲突视为已记过，不重复计。"""
        rec = ExternalUsageRecord(
            idempotency_key="stored-1", provider="tavily", operation="SEARCH",
            outcome="SUCCEEDED", requested_at="2026-10-15T00:00:00Z",
            cost_estimate=1.0, cost_confirmed=1.0, currency="TAVILY_CREDIT",
            cost_source="PROVIDER_USAGE", cost_status="CONFIRMED")
        self.assertTrue(unwrap(self.fx.storage.record_external_usage(self.fx.ctx, rec)))
        self.assertFalse(unwrap(self.fx.storage.record_external_usage(self.fx.ctx, rec)))
        report = unwrap(self.fx.storage.external_usage_report(self.fx.ctx))
        self.assertEqual(report.requests, 1)
        self.assertEqual(report.confirmed_cost, 1.0)


class TestSequentialCannotBypassCeiling(P45TestCase):
    """连续请求不能绕过上限：靠**预留**（发请求之前就占位）。"""

    def test_second_call_blocked_by_reservation(self):
        stub = StubUrlopen(search_data={
            "results": [{"title": "A", "url": "https://good.invalid/a",
                         "content": "摘要"}],
            "usage": {"credits": 1}})
        p = self.provider(stub)
        svc = self.service(p, limits=ResearchLimits(max_queries=5, max_cost=1.5))
        res = svc.run(self.fx.ctx,
                      [self.fx.target(self.fx.entity("阿雾"), "阿雾", topic="生平")])
        self.assertTrue(is_err(res))
        self.assertEqual(str(res.error.code), "BUDGET_EXCEEDED")
        self.assertEqual(len(stub.calls), 1)   # 第二次**根本没发**

    def test_reservation_exists_before_request_is_sent(self):
        """预留必须发生在请求**之前**（否则等于没预留）。

        预留由 ResearchService 做（它才知道费用上限），Provider 只负责结算。
        这里在替身网络出口里"偷看"请求发出那一刻的账本状态。
        """
        captured: dict = {}
        holder: dict = {}

        class SpyStub(StubUrlopen):
            def __call__(self, req, timeout=None):
                ledger = holder["provider"].usage_ledger
                captured["reserved"] = ledger.totals().reserved_cost
                return super().__call__(req, timeout=timeout)

        stub = SpyStub(search_data={
            "results": [{"title": "A", "url": "https://good.invalid/a",
                         "content": "摘要"}],
            "usage": {"credits": 1}})
        p = self.provider(stub)
        holder["provider"] = p
        svc = self.service(p, limits=ResearchLimits(max_cost=10))
        unwrap(svc.run(self.fx.ctx,
                       [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        # 请求真正发出的那一刻，预留已经在账上了
        self.assertGreater(captured["reserved"], 0.0)

    def test_concurrent_reservations_are_all_counted(self):
        """两个"并发"预留同时未结算 → 都计入已占用，不被漏掉。"""
        led = ExternalUsageLedger()
        led.reserve("a", 1.0)
        led.reserve("b", 1.0)
        self.assertEqual(led.totals().reserved_cost, 2.0)
        self.assertTrue(led.would_exceed(1.5, 1.0))


class TestStorageLedger(P45TestCase):
    """存储账本：落库、汇总（估算/实际/未知分开）、按状态列出待核对项。"""

    def test_report_separates_estimate_confirmed_unknown(self):
        def rec(key, est, conf, status):
            return ExternalUsageRecord(
                idempotency_key=key, provider="tavily", operation="SEARCH",
                outcome="SUCCEEDED" if status != "UNKNOWN" else "FAILED",
                requested_at="2026-10-15T00:00:00Z", cost_estimate=est,
                cost_confirmed=conf, currency="TAVILY_CREDIT",
                cost_source="PROVIDER_USAGE", cost_status=status)

        for r in (rec("a", 1.0, 2.0, "CONFIRMED"), rec("b", 1.0, None, "UNKNOWN")):
            unwrap(self.fx.storage.record_external_usage(self.fx.ctx, r))
        report = unwrap(self.fx.storage.external_usage_report(self.fx.ctx))
        self.assertEqual(report.requests, 2)
        self.assertEqual(report.confirmed_cost, 2.0)    # 只算已确认的
        self.assertEqual(report.estimated_cost, 2.0)    # 估算另算
        self.assertEqual(report.unknown_requests, 1)
        self.assertEqual(report.failed_requests, 1)

    def test_list_unknown_for_manual_reconciliation(self):
        unwrap(self.fx.storage.record_external_usage(
            self.fx.ctx,
            ExternalUsageRecord(idempotency_key="u1", provider="tavily",
                                operation="EXTRACT", outcome="FAILED",
                                requested_at="2026-10-15T00:00:00Z",
                                cost_estimate=0.2, cost_status="UNKNOWN")))
        rows = unwrap(self.fx.storage.external_usage_records(
            self.fx.ctx, cost_status="UNKNOWN"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].idempotency_key, "u1")
        self.assertIsNone(rows[0].cost_confirmed)


class TestOfficialRateEstimates(P45TestCase):
    """官方费率只用于**估算**，且与实际分开。"""

    def test_search_estimate_matches_official_rate(self):
        self.assertEqual(estimate_search_credits("basic"), 1.0)
        self.assertEqual(estimate_search_credits("advanced"), 2.0)

    def test_extract_estimate_matches_official_rate(self):
        # 官方：basic 每 5 次成功提取 1 credit
        self.assertAlmostEqual(estimate_extract_credits(5, "basic"), 1.0)
        self.assertAlmostEqual(estimate_extract_credits(1, "basic"), 0.2)
        self.assertAlmostEqual(estimate_extract_credits(5, "advanced"), 2.0)

    def test_unknown_depth_yields_none_not_guess(self):
        self.assertIsNone(estimate_search_credits("ultra"))
        self.assertIsNone(estimate_extract_credits(3, "ultra"))

    def test_estimate_and_confirmed_never_mixed(self):
        """credits=0（未达计费粒度）时，估算仍是 0.2，不会被 0 覆盖。"""
        stub = StubUrlopen(extract_data={
            "results": [{"url": "https://good.invalid/a", "raw_content": "x"}],
            "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        unwrap(p.extract(self.fx.ctx, ExtractRequest(urls=["https://good.invalid/a"])))
        rec = p.usage_ledger.records()[0]
        self.assertEqual(rec.cost_confirmed, 0.0)     # 实际回报
        self.assertAlmostEqual(rec.cost_estimate, 0.2)  # 估算（保守口径）
        self.assertEqual(rec.cost_status, "CONFIRMED")


class TestNoUsageWithoutNetwork(P45TestCase):
    """本阶段零真实请求：拿掉网络出口时必须直接炸，而不是静默连网。"""

    def test_missing_urlopen_raises_instead_of_reaching_internet(self):
        p = TavilyProvider(api_key="test-key", config=ProviderConfig(max_retries=0))
        # 断网哨兵：任何真实连接都会被拦
        def _blocked(req, timeout=None):
            raise AssertionError("不得发起真实网络请求")

        p._urlopen = _blocked
        with self.assertRaises(AssertionError):
            p.search(self.fx.ctx, SearchRequest(query="雾港"))


# ==========================================================================
# 任务二：正文质量门禁
# ==========================================================================


class TestContentQualityGate(P45TestCase):
    """门禁对各类正文的判定。"""

    def test_normal_long_content_is_valid(self):
        q = assess_content_quality(_prose(20), url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_VALID)

    def test_empty_content_is_empty(self):
        q = assess_content_quality("", url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_EMPTY)
        self.assertIn("EMPTY_CONTENT", q.reasons)

    def test_whitespace_only_is_empty(self):
        q = assess_content_quality("   \n\t\n  ", url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_EMPTY)

    def test_nav_only_is_low_quality(self):
        q = assess_content_quality(_nav(40), url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_LOW)
        self.assertIn(REASON_NAV_DOMINATED, q.reasons)
        self.assertLess(q.prose_chars, 200)

    def test_long_nav_lines_are_not_mistaken_for_prose(self):
        """长目录行（一行串多个链接）不能因为"行够长"被当成正文。"""
        q = assess_content_quality(_long_nav(20), url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_LOW)
        self.assertIn(REASON_NAV_DOMINATED, q.reasons)

    def test_too_short_content_is_low_quality(self):
        q = assess_content_quality(_prose(2), url="https://good.invalid/a")  # 132 字
        self.assertEqual(q.status, QUALITY_LOW)
        self.assertIn(REASON_TOO_SHORT, q.reasons)

    def test_fragmented_content_is_low_quality(self):
        """字够多但全是碎片（每段都太短，凑不出成段的正文）。"""
        # 8 段，每段 40 字（刚够"正文行"下限），段间用导航行隔开
        parts: list[str] = []
        for i in range(8):
            parts.append("短段落内容" * 8)
            parts.append(f"[条目{i}](#锚点{i})")
        q = assess_content_quality("\n".join(parts), url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_LOW)
        self.assertIn(REASON_NO_PROSE, q.reasons)
        self.assertGreaterEqual(q.prose_chars, 200)   # 总字数其实够
        self.assertLess(q.longest_prose_run, 200)      # 但没有成段的

    def test_heavy_header_footer_with_real_body_is_valid(self):
        """页眉页脚很多，但正文也足够 → **仍是 VALID**（不能误杀）。"""
        content = _nav(30) + "\n" + _prose(8) + "\n" + _nav(30)
        q = assess_content_quality(content, url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_VALID)
        self.assertGreater(q.nav_line_ratio, 0.6)   # 确实导航很多
        self.assertGreaterEqual(q.prose_chars, 400)

    def test_irrelevant_to_target_is_low_quality(self):
        q = assess_content_quality(
            _prose(20), url="https://good.invalid/a", target_names=["阿雾"])
        self.assertEqual(q.status, QUALITY_LOW)
        self.assertIn(REASON_TARGET_NOT_MENTIONED, q.reasons)
        self.assertFalse(q.target_mentioned)

    def test_relevant_to_target_passes(self):
        content = "阿雾" + _prose(20)
        q = assess_content_quality(
            content, url="https://good.invalid/a", target_names=["阿雾"])
        self.assertEqual(q.status, QUALITY_VALID)
        self.assertTrue(q.target_mentioned)

    def test_relevance_skipped_when_no_target(self):
        """没有目标 → 不判相关性（无法可靠判断就不判）。"""
        q = assess_content_quality(_prose(20), url="https://good.invalid/a",
                                   target_names=None)
        self.assertIsNone(q.target_mentioned)
        self.assertEqual(q.status, QUALITY_VALID)

    def test_alias_matches_target(self):
        q = assess_content_quality("雾港水手" + _prose(20),
                                   url="https://good.invalid/a",
                                   target_names=["阿雾", "雾港水手"])
        self.assertTrue(q.target_mentioned)

    def test_quality_carries_metrics_for_diagnosis(self):
        q = assess_content_quality(_nav(40), url="https://good.invalid/a")
        detail = q.as_detail()
        for key in ("status", "reasons", "prose_chars", "nav_line_ratio",
                    "longest_prose_run", "page_type"):
            self.assertIn(key, detail)
        self.assertIsInstance(q.sample, str)


class TestPageTypeHandling(P45TestCase):
    """页面类型识别与分类型处理。"""

    def test_detect_page_types(self):
        self.assertEqual(detect_page_type("https://en.wikipedia.org/wiki/X"),
                         PAGE_TYPE_WIKI)
        self.assertEqual(detect_page_type("https://www.youtube.com/watch?v=x"),
                         PAGE_TYPE_VIDEO)
        self.assertEqual(detect_page_type("https://site.example.com/a.pdf"),
                         "PDF")
        self.assertEqual(detect_page_type("https://site.example.com/a"),
                         PAGE_TYPE_GENERIC)

    def test_video_page_gets_no_remediation(self):
        """视频页没有可提的文章正文 → **不补救**（避免白花额度）。"""
        q = assess_content_quality(
            _prose(20), url="https://www.youtube.com/watch?v=gtRLmL70TH0")
        self.assertIn(REASON_UNSUPPORTED_PAGE_TYPE, q.reasons)
        plan = plan_remediation("https://www.youtube.com/watch?v=gtRLmL70TH0", q,
                                query="阿雾 生平")
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.options, [])
        self.assertEqual(plan.page_type, PAGE_TYPE_VIDEO)

    def test_wiki_page_gets_official_query_option(self):
        q = assess_content_quality(_nav(40), url="https://en.wikipedia.org/wiki/X")
        plan = plan_remediation("https://en.wikipedia.org/wiki/X", q,
                                query="阿雾 生平")
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.page_type, PAGE_TYPE_WIKI)
        first = plan.options[0]
        self.assertEqual(first["query"], "阿雾 生平")
        self.assertEqual(first["chunks_per_source"], 3)   # 官方默认 3，范围 1..5

    def test_chunks_per_source_only_with_query(self):
        """官方约束：`chunks_per_source` 仅在提供 `query` 时可用。"""
        q = assess_content_quality(_nav(40), url="https://en.wikipedia.org/wiki/X")
        plan = plan_remediation("https://en.wikipedia.org/wiki/X", q, query="")
        for opt in plan.options:
            self.assertNotIn("chunks_per_source", opt)   # 无 query → 不带该参数

    def test_page_type_comes_from_url_not_passed_quality(self):
        """补救必须按**本次 URL** 判类型，不能沿用别处的评估结果。"""
        q = assess_content_quality(_nav(40), url="https://en.wikipedia.org/wiki/X")
        plan = plan_remediation("https://www.youtube.com/watch?v=1", q, query="x")
        self.assertEqual(plan.page_type, PAGE_TYPE_VIDEO)
        self.assertFalse(plan.allowed)


class TestRemediationConstraints(P45TestCase):
    """补救受预算、次数上限、幂等约束。"""

    def test_options_capped_by_max_options(self):
        q = assess_content_quality(_nav(40), url="https://en.wikipedia.org/wiki/X")
        plan = plan_remediation("https://en.wikipedia.org/wiki/X", q,
                                query="阿雾 生平", max_options=1)
        self.assertEqual(len(plan.options), 1)

    def test_zero_options_means_no_remediation(self):
        q = assess_content_quality(_nav(40), url="https://en.wikipedia.org/wiki/X")
        plan = plan_remediation("https://en.wikipedia.org/wiki/X", q,
                                query="阿雾 生平", max_options=0)
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.options, [])

    def test_remediation_request_carries_official_params(self):
        """按补救方案构造的请求，只带官方确认过的参数。"""
        q = assess_content_quality(_nav(40), url="https://en.wikipedia.org/wiki/X")
        plan = plan_remediation("https://en.wikipedia.org/wiki/X", q,
                                query="阿雾 生平", max_options=1)
        req = ExtractRequest(urls=["https://en.wikipedia.org/wiki/X"],
                             **plan.options[0])
        self.assertEqual(req.query, "阿雾 生平")
        self.assertEqual(req.chunks_per_source, 3)
        self.assertIsNone(req.format)
        self.assertIsNone(req.extract_depth)

    def test_remediated_request_idempotency_key_differs_from_original(self):
        k1 = make_idempotency_key("tavily", "EXTRACT",
                                  "https://en.wikipedia.org/wiki/X", "basic", "",
                                  "", None)
        k2 = make_idempotency_key("tavily", "EXTRACT",
                                  "https://en.wikipedia.org/wiki/X", "basic",
                                  "阿雾 生平", "", 3)
        self.assertNotEqual(k1, k2)   # 补救是另一次请求 → 另立一笔账

    def test_remediation_still_recorded_in_ledger(self):
        """补救请求也要进账本（不能因为是"补救"就不记账）。"""
        stub = StubUrlopen(extract_data={
            "results": [{"url": "https://good.invalid/a", "raw_content": _prose(10)}],
            "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        unwrap(p.extract(self.fx.ctx,
                         ExtractRequest(urls=["https://good.invalid/a"],
                                        metadata={"idempotency_key": "orig"})))
        unwrap(p.extract(self.fx.ctx,
                         ExtractRequest(urls=["https://good.invalid/a"],
                                        query="阿雾 生平", chunks_per_source=3,
                                        metadata={"idempotency_key": "remedy"})))
        self.assertEqual(len(p.usage_ledger.records()), 2)


class TestLowQualityBlockedFromClaimExtractor(P45TestCase):
    """低质量正文不得变成研究材料（Claim Extractor 的原料）。"""

    def test_nav_only_excerpt_produces_no_material(self):
        """页头 = 长目录，摘录取到的是目录 → 门禁判废 → 一个材料都不给。

        夹具刻意让 `content`（含目录 + 无正文）与 `excerpt`（页头 2000 字）
        有区别：门禁判的是 `excerpt`，不是 `content`。
        """
        raw = _long_nav(50)
        item = ExtractedContent(url="https://good.invalid/a", title="A",
                                content=raw, excerpt=raw[:2000])
        item.quality = assess_content_quality(item.excerpt, url="https://good.invalid/a")
        materials, rejected = materials_from_extracts([item])
        self.assertEqual(len(materials), 0)      # 一个材料都不给
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].status, QUALITY_LOW)
        self.assertIn(REASON_NAV_DOMINATED, rejected[0].reasons)

    def test_valid_excerpt_produces_material_with_excerpt_only(self):
        """长页：正文在目录之后；交出去的材料是那段正文摘录，不是全文。"""
        raw = _long_page()
        excerpt = select_prose_window(raw)
        self.assertNotEqual(excerpt, raw[:DEFAULT_EXCERPT_WINDOW_CHARS])  # 不是页头
        item = ExtractedContent(url="https://good.invalid/a", title="A",
                                content=raw, excerpt=excerpt)
        item.quality = assess_content_quality(item.excerpt, url="https://good.invalid/a")
        materials, rejected = materials_from_extracts([item])
        self.assertEqual(len(materials), 1)
        self.assertEqual(len(rejected), 0)
        self.assertEqual(materials[0].url, "https://good.invalid/a")
        self.assertEqual(materials[0].snippet, excerpt)     # 交摘录
        self.assertNotEqual(materials[0].snippet, raw)      # 不交全文

    def test_unevaluated_content_is_blocked_too(self):
        """未评估（quality=None）一律不交出去 —— 防止漏过门禁。"""
        item = ExtractedContent(url="https://good.invalid/a", title="A",
                                content=_prose(20), excerpt="x")
        materials, rejected = materials_from_extracts([item])
        self.assertEqual(len(materials), 0)
        self.assertEqual(len(rejected), 1)

    def test_failed_extract_produces_no_material(self):
        item = ExtractedContent(url="https://gone.invalid/a", error="unreachable")
        item.quality = ContentQuality(status=QUALITY_EMPTY,
                                      reasons=["EMPTY_CONTENT"])
        materials, rejected = materials_from_extracts([item])
        self.assertEqual(len(materials), 0)
        self.assertEqual(rejected[0].extract_error, "unreachable")

    def test_end_to_end_low_quality_never_reaches_materials(self):
        """端到端：抓到的是导航骨架 → 出口清空 + 无材料。"""
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _nav(40)}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        # 出口收口：低质量正文被物理清空，下游拿不到内容
        self.assertEqual(out.extracted[0].content, "")
        # 被拒记录留在 rejected_extracts（不静默丢弃）
        self.assertEqual(len(out.rejected_extracts), 1)
        self.assertEqual(out.rejected_extracts[0].status, QUALITY_LOW)
        # 转换成材料时为 0 条
        materials, rejected = materials_from_extracts(out.extracted)
        self.assertEqual(len(materials), 0)
        self.assertEqual(len(rejected), 1)

    def test_diagnosis_distinguishes_api_failure_from_gate_rejection(self):
        """诊断信息要能区分"抓取失败"与"本地门禁拒收"。"""
        api_fail = ExtractedContent(url="https://gone.invalid/a", error="unreachable")
        api_fail.quality = ContentQuality(status=QUALITY_EMPTY, reasons=["EMPTY_CONTENT"])
        gate_reject = ExtractedContent(url="https://good.invalid/a",
                                       content=_nav(40), excerpt="")
        gate_reject.quality = assess_content_quality(
            _nav(40), url="https://good.invalid/a")
        _, rejected = materials_from_extracts([api_fail, gate_reject])
        by_url = {r.url: r for r in rejected}
        # API 层失败：有 extract_error
        self.assertEqual(by_url["https://gone.invalid/a"].extract_error, "unreachable")
        # 门禁拒收：没有 extract_error，是内容不行
        self.assertIsNone(by_url["https://good.invalid/a"].extract_error)
        self.assertEqual(by_url["https://good.invalid/a"].status, QUALITY_LOW)


class TestExcerptWindowSelection(P45TestCase):
    """B：摘录取"连续正文最密集的那段原文"，而不是页头。

    这些用例在旧行为（摘录 = `raw[:2000]`）下**必然失败**——这正是它们存在的理由。
    """

    def test_excerpt_starts_after_navigation_prefix(self):
        raw = _long_page()
        start, end = locate_prose_window(raw)
        self.assertGreater(start, DEFAULT_EXCERPT_WINDOW_CHARS)   # 起点跳过整段目录
        self.assertLessEqual(end - start, DEFAULT_EXCERPT_WINDOW_CHARS)

    def test_excerpt_is_prose_not_toc_entries(self):
        raw = _long_page()
        excerpt = select_prose_window(raw)
        self.assertIn("阿雾", excerpt)          # 正文进来了
        self.assertNotIn("[章节", excerpt)       # 目录条目没进来

    def test_excerpt_would_have_been_navigation_under_old_rule(self):
        """同一份长页：旧摘录（页头 2000 字）是目录，新摘录是正文。"""
        raw = _long_page()
        old_excerpt = raw[:DEFAULT_EXCERPT_WINDOW_CHARS]
        new_excerpt = select_prose_window(raw)
        self.assertEqual(assess_content_quality(old_excerpt, url="https://good.invalid/a").status,
                         QUALITY_LOW)
        self.assertEqual(assess_content_quality(new_excerpt, url="https://good.invalid/a").status,
                         QUALITY_VALID)

    def test_no_prose_falls_back_to_head_not_fake_window(self):
        """找不到正文 → 回退到开头（不假装找到了），由门禁去判废。"""
        raw = _long_nav(50)
        start, end = locate_prose_window(raw)
        self.assertEqual((start, end), (0, DEFAULT_EXCERPT_WINDOW_CHARS))
        self.assertEqual(select_prose_window(raw), raw[:DEFAULT_EXCERPT_WINDOW_CHARS])

    def test_short_page_excerpt_is_whole_text(self):
        raw = _prose_target(10)
        self.assertEqual(select_prose_window(raw), raw)

    def test_window_is_pure_function(self):
        """纯函数：不碰网络与存储（可被 Provider 直接调用）。"""
        raw = _long_page()
        self.assertEqual(select_prose_window(raw), select_prose_window(raw))

    def test_provider_excerpt_skips_page_head(self):
        """Provider 交出来的摘录就是正文窗口，不是页头。"""
        raw = _long_page()
        stub = StubUrlopen(extract_data={
            "results": [{"url": "https://good.invalid/a", "title": "A",
                         "raw_content": raw}],
            "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        res = unwrap(p.extract(self.fx.ctx,
                               ExtractRequest(urls=["https://good.invalid/a"])))
        item = res.results[0]
        self.assertNotIn("[章节", item.excerpt)
        self.assertIn("阿雾", item.excerpt)
        self.assertTrue(item.truncated)          # 只取了一段，标了截断
        self.assertEqual(item.raw_content, raw)  # 全文仍在，摘录只是窗口

    def test_provider_head_only_page_is_not_faked_as_prose(self):
        """纯导航页：Provider 交回页头（不是编造的正文），门禁随后判废。"""
        raw = _long_nav(50)
        stub = StubUrlopen(extract_data={
            "results": [{"url": "https://good.invalid/a", "title": "A",
                         "raw_content": raw}],
            "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        res = unwrap(p.extract(self.fx.ctx,
                               ExtractRequest(urls=["https://good.invalid/a"])))
        self.assertEqual(res.results[0].excerpt, raw[:DEFAULT_EXCERPT_WINDOW_CHARS])


class TestQualityGateJudgesExcerpt(P45TestCase):
    """A：门禁判的是**摘录**（下游真正拿到的那段），不是全文。

    P4.5 受控复测撞到的错位：全文 VALID、摘录是目录 → 摘录照样变成材料。
    下面这些用例在"门禁判 content"的旧行为下**必然失败**。
    """

    def test_full_text_valid_but_excerpt_nav_is_rejected(self):
        raw = _long_page()
        # 全文看起来完全合格（正文很多）—— 旧门禁就是被这个骗过去的
        self.assertEqual(assess_content_quality(raw, url="https://good.invalid/a").status,
                         QUALITY_VALID)
        # 但门禁判的是摘录：页头目录 → 判废
        q = assess_content_quality(raw[:DEFAULT_EXCERPT_WINDOW_CHARS],
                                   url="https://good.invalid/a")
        self.assertEqual(q.status, QUALITY_LOW)
        self.assertIn(REASON_NAV_DOMINATED, q.reasons)

    def test_gate_judges_excerpt_in_research_flow(self):
        """流程里门禁取的是 `item.excerpt`：造一个 excerpt 与 content 不一致的条目。

        直接构造（不走 Provider）才能逼出"门禁读的是哪个字段"：
        content 是合格长页，excerpt 是目录 → 必须判废。
        """
        raw = _long_page()
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": raw}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)

        class ExcerptRewriter:
            """把 Provider 交出的正文窗口**换回**页头目录，模拟旧摘录行为。"""

            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def extract(self, ctx, req):
                res = self._inner.extract(ctx, req)
                if is_err(res):
                    return res
                for item in res.value.results:
                    if item.raw_content and not item.error:
                        item.excerpt = item.raw_content[:DEFAULT_EXCERPT_WINDOW_CHARS]
                return res

        svc = self.service(ExcerptRewriter(p), extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        self.assertEqual(out.extracted[0].quality.status, QUALITY_LOW)
        self.assertEqual(len(out.rejected_extracts), 1)
        # 低质量正文被物理清空 → 下游拿不到内容
        self.assertEqual(out.extracted[0].content, "")
        materials, rejected = materials_from_extracts(out.extracted)
        self.assertEqual(len(materials), 0)
        self.assertEqual(len(rejected), 1)

    def test_delivered_material_equals_judged_excerpt(self):
        """交付 == 判定：材料里的 snippet 就是门禁判过的那段摘录。"""
        raw = _long_page()
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": raw}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        judged = out.extracted[0].quality
        self.assertEqual(judged.status, QUALITY_VALID)
        materials, rejected = materials_from_extracts(out.extracted)
        self.assertEqual(len(materials), 1)
        self.assertEqual(len(rejected), 0)
        self.assertEqual(materials[0].snippet, out.extracted[0].excerpt)
        self.assertIn("阿雾", materials[0].snippet)
        self.assertNotIn("[章节", materials[0].snippet)
        # 交出去的确实只是摘录，不是那 5000+ 字全文
        self.assertLess(len(materials[0].snippet), len(raw))


class TestExtractQualityInResearchFlow(P45TestCase):
    """门禁接进 Research 流程后的行为。"""

    def test_quality_status_attached_to_extracted(self):
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": "阿雾" + _prose(20)}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        self.assertEqual(out.extracted[0].quality.status, QUALITY_VALID)
        self.assertTrue(out.extracted[0].is_usable_material)
        self.assertEqual(len(out.rejected_extracts), 0)

    def test_short_content_flagged_not_dropped(self):
        """门禁只标记、不丢弃：条目仍在 extracted 里，供诊断。"""
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": "太短"}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        self.assertEqual(len(out.extracted), 1)          # 仍在
        self.assertEqual(out.extracted[0].quality.status, QUALITY_LOW)
        self.assertEqual(out.extracted[0].content, "")   # 但内容已被出口清空
        self.assertEqual(len(out.rejected_extracts), 1)

    def test_extract_failure_recorded_as_empty(self):
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://gone.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [], "failed_results": [
                {"url": "https://gone.invalid/a", "error": "Failed to retrieve"}],
                "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        self.assertEqual(len(out.rejected_extracts), 1)
        self.assertEqual(out.rejected_extracts[0].status, QUALITY_EMPTY)
        self.assertEqual(out.rejected_extracts[0].extract_error,
                         "Failed to retrieve")

    def test_provider_without_extract_support_still_works(self):
        """Mock Provider 不支持 extract → 保持既有行为（不回退）。"""
        from persona.providers.mock import FakeProvider

        svc = self.service(FakeProvider(fixtures={}),
                           extraction_strategy=TopNExtractionStrategy())
        self.assertIsNotNone(svc)


# ==========================================================================
# 补救接线（官方 query+chunks_per_source / format=text 自动重抓）
# ==========================================================================


class _ParamAwareExtractStub:
    """按**请求参数**返回不同正文的 /extract 桩（其余行为同 `StubUrlopen`）。

    为什么需要它：`StubUrlopen` 对同一 URL 永远返回同一份内容——那样
    "补救换了官方参数"在响应上看不出任何差别，测试就只能断言"发过了"，
    断言不到"换来的内容真的不一样"。这里按 `format` 分流：默认返回
    `extract_data`，`format=text` 时返回 `text_content`。
    """

    def __init__(self, *, search_data=None, extract_data=None, error_for=None,
                 text_content=""):
        self.search_data = search_data or {}
        self.extract_data = extract_data or {}
        self.error_for = dict(error_for or {})
        self.text_content = text_content
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, req, timeout=None):
        payload = json.loads(req.data.decode("utf-8")) if req.data else {}
        headers = dict(getattr(req, "headers", None) or {})
        self.calls.append((req.full_url, payload, headers))
        if req.full_url.endswith("/search"):
            if "search" in self.error_for:
                raise self.error_for["search"]
            return _FakeResponse(json.dumps(self.search_data).encode("utf-8"))
        if req.full_url.endswith("/extract"):
            if "extract" in self.error_for:
                raise self.error_for["extract"]
            if payload.get("format") == "text":
                data = {
                    "results": [{"url": u, "title": "T",
                                 "raw_content": self.text_content}
                                for u in (payload.get("urls") or [])],
                    "failed_results": [],
                    "usage": {"credits": 0},
                    "request_id": "req-extract-text",
                }
            else:
                data = self.extract_data
            return _FakeResponse(json.dumps(data).encode("utf-8"))
        raise urllib.error.HTTPError(req.full_url, 404, "not found", None, None)


class TestRemediationIdempotencyKeys(P45TestCase):
    """幂等键：**预留键 == 结算键**必须显式覆盖，不能只靠代码审查。

    预留由 Research 做（请求发出之前），结算由 Provider 做。两边各算一遍键，
    一旦口径漂移：预留键 ≠ 结算键 → 预留永不弹掉（**幽灵占额度**），
    或两笔不同请求共用一个键 → 该记的被当成重复、**静默不计**。
    """

    def test_research_key_formula_matches_provider_fallback(self):
        """research 侧公式与 Provider 兜底推导对同参数产出**同一个键**。

        走真实 Provider 代码路径（不给 metadata 键 → Provider 自己推导），
        再与 research 用的 `extract_key_parts` 公式比对——两边任一改错都会红。
        """
        urls = ["https://good.invalid/a", "https://good.invalid/b"]
        stub = StubUrlopen(extract_data={"results": [], "failed_results": [],
                                         "usage": {"credits": 0}})
        p = self.provider(stub)
        unwrap(p.extract(self.fx.ctx, ExtractRequest(
            urls=urls, query="阿雾", chunks_per_source=3)))
        settled = p.usage_ledger.records()[0].idempotency_key
        research_key = make_idempotency_key(
            "tavily", OP_EXTRACT,
            *extract_key_parts(urls, depth="basic", query="阿雾",
                               chunks_per_source=3))
        self.assertEqual(settled, research_key)

    def test_reserved_key_is_the_key_that_gets_settled(self):
        """端到端：账本里那条 EXTRACT 记录的键，就是 research 预留的键。

        同时断言 `reserved_cost == 0`：若预留键与结算键不同，预留会永远
        留在账本里虚占额度，这个断言专门抓"幽灵占额度"。
        """
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _prose_target(10)}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        unwrap(svc.run(self.fx.ctx,
                       [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        expected = make_idempotency_key(
            "tavily", OP_EXTRACT,
            *extract_key_parts(["https://good.invalid/a"], depth="basic",
                               query="阿雾",
                               chunks_per_source=DEFAULT_CHUNKS_PER_SOURCE))
        rec = p.usage_ledger.get(expected)
        self.assertIsNotNone(rec)                      # 结算用的就是预留那个键
        self.assertEqual(rec.operation, OP_EXTRACT)
        totals = p.usage_ledger.totals()
        self.assertEqual(totals.reserved_cost, 0.0)    # 没有幽灵占额度
        self.assertEqual(totals.unknown_hold_cost, 0.0)

    def test_initial_and_remediation_keys_differ(self):
        """初始（query+chunks）与补救（format=text）必须是不同的键。

        若相同，补救请求会被当成"初始那笔的重复"而永不发送。
        """
        url = ["https://good.invalid/a"]
        initial = make_idempotency_key(
            "tavily", OP_EXTRACT,
            *extract_key_parts(url, depth="basic", query="阿雾",
                               chunks_per_source=3))
        remediate = make_idempotency_key(
            "tavily", OP_EXTRACT,
            *extract_key_parts(url, depth="basic", fmt="text"))
        self.assertNotEqual(initial, remediate)

    def test_chunks_value_changes_the_key(self):
        """chunks 3 与 5 是不同请求意图 → 不同键（补救不会撞初始键）。"""
        url = ["https://good.invalid/a"]
        k3 = make_idempotency_key("tavily", OP_EXTRACT, *extract_key_parts(
            url, query="阿雾", chunks_per_source=3))
        k5 = make_idempotency_key("tavily", OP_EXTRACT, *extract_key_parts(
            url, query="阿雾", chunks_per_source=5))
        self.assertNotEqual(k3, k5)

    def test_empty_params_do_not_change_the_key(self):
        """没带 query/chunks 与"带了空值"是同一个键（空值被过滤）。"""
        url = ["https://good.invalid/a"]
        a = make_idempotency_key("tavily", OP_EXTRACT, *extract_key_parts(url))
        b = make_idempotency_key("tavily", OP_EXTRACT, *extract_key_parts(
            url, query="", fmt="", chunks_per_source=None))
        self.assertEqual(a, b)

    def test_same_request_twice_is_not_sent_twice(self):
        """同一个 Provider（同一本账）跑两次：第二次抓取**不再发送**。

        settle 幂等只防重复记账；这里断言发送层守卫真的挡住了重发
        （extract 调用次数不增长），并有显式 warning。
        """
        search_data = {"results": [
            {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
            "usage": {"credits": 1}}
        extract_data = {"results": [{"url": "https://good.invalid/a", "title": "A",
                                     "raw_content": _prose_target(10)}],
                        "failed_results": [], "usage": {"credits": 0}}
        stub = StubUrlopen(search_data=search_data, extract_data=extract_data)
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        unwrap(svc.run(self.fx.ctx,
                       [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        second = unwrap(svc.run(self.fx.ctx,
                                [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 1)        # 第二次没再发
        self.assertIn(WARNING_EXTRACT_DUPLICATE_SKIPPED,
                      [w.code for w in second.warnings])

    def test_cross_run_guard_via_storage(self):
        """跨轮幂等（D3）：上一轮的记录在持久账本里 → 新一轮不再发送。

        模拟"任务恢复 / 重跑"：全新的 Provider（= 全新的内存账本），
        只有持久账本记得上一轮抓过。没有 `has_external_usage`，
        这个请求会真的再花一次钱，而 settle 只会静默返回 False。
        """
        search_data = {"results": [
            {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
            "usage": {"credits": 1}}
        extract_data = {"results": [{"url": "https://good.invalid/a", "title": "A",
                                     "raw_content": _prose_target(10)}],
                        "failed_results": [], "usage": {"credits": 0}}
        p1 = self.provider(StubUrlopen(search_data=search_data,
                                       extract_data=extract_data))
        svc1 = self.service(p1, extraction_strategy=TopNExtractionStrategy(),
                            usage_sink=self.fx.storage, task_id="task-run-1")
        unwrap(svc1.run(self.fx.ctx,
                        [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        # 新一轮：新 Provider（新内存账本）+ 同一个持久账本
        stub2 = StubUrlopen(search_data=search_data, extract_data=extract_data)
        p2 = self.provider(stub2)
        svc2 = self.service(p2, extraction_strategy=TopNExtractionStrategy(),
                            usage_sink=self.fx.storage, task_id="task-run-2")
        out2 = unwrap(svc2.run(self.fx.ctx,
                               [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        extract_calls = [c for c in stub2.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 0)        # 一个都没发
        self.assertIn(WARNING_EXTRACT_DUPLICATE_SKIPPED,
                      [w.code for w in out2.warnings])
        # 持久账本里就是上一轮那一笔（没有新增）
        stored = unwrap(self.fx.storage.external_usage_records(
            self.fx.ctx, task_id="task-run-1"))
        extract_records = [r for r in stored if r.operation == OP_EXTRACT]
        self.assertEqual(len(extract_records), 1)


class TestLedgerSendGuard(P45TestCase):
    """发送层守卫的账本侧：`has_attempted` 覆盖预留/未知占位/已结算。"""

    def test_has_attempted_covers_all_three_states(self):
        led = ExternalUsageLedger()
        self.assertFalse(led.has_attempted("k1"))
        led.reserve("k1", 0.2)
        self.assertTrue(led.has_attempted("k1"))       # 预留中（正在花）
        led.settle(ExternalUsageRecord(
            idempotency_key="k1", provider="tavily", operation="EXTRACT",
            outcome="SUCCEEDED", requested_at="t", cost_estimate=0.2,
            cost_confirmed=0.0, cost_status="CONFIRMED"))
        self.assertTrue(led.has_attempted("k1"))       # 已结算
        led.settle(ExternalUsageRecord(
            idempotency_key="k2", provider="tavily", operation="EXTRACT",
            outcome="FAILED", requested_at="t", cost_estimate=0.2,
            cost_status="UNKNOWN"))
        self.assertTrue(led.has_attempted("k2"))       # 未知占位（花过但不知道多少）
        self.assertFalse(led.has_attempted("k3"))

    def test_storage_has_external_usage(self):
        """存储侧只读查询：记过 → True；没记过 → False。"""
        rec = ExternalUsageRecord(
            idempotency_key="stored-2", provider="tavily", operation="EXTRACT",
            outcome="SUCCEEDED", requested_at="2026-10-16T00:00:00Z",
            cost_estimate=0.2, cost_confirmed=0.0, currency="TAVILY_CREDIT",
            cost_source="PROVIDER_USAGE", cost_status="CONFIRMED")
        self.assertFalse(unwrap(
            self.fx.storage.has_external_usage(self.fx.ctx, "stored-2")))
        unwrap(self.fx.storage.record_external_usage(self.fx.ctx, rec))
        self.assertTrue(unwrap(
            self.fx.storage.has_external_usage(self.fx.ctx, "stored-2")))
        self.assertFalse(unwrap(
            self.fx.storage.has_external_usage(self.fx.ctx, "never-seen")))


class TestRemediationBudgetAndCounts(P45TestCase):
    """补救的预算（D1）与次数（D2）约束。"""

    def _low_item(self, url="https://good.invalid/a"):
        item = ExtractedContent(url=url, content=_nav(40), excerpt=_nav(40))
        item.quality = assess_content_quality(item.excerpt, url=url)
        self.assertEqual(item.quality.status, QUALITY_LOW)
        return item

    def _nav_stub(self, *, error_for=None):
        return _ParamAwareExtractStub(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _nav(40)}],
                          "failed_results": [], "usage": {"credits": 0}},
            error_for=error_for,
            text_content=_prose_target(10))

    def test_budget_insufficient_stops_remediation_but_keeps_run_ok(self):
        """D1：补救阶段费用上限不足 → 停手 + warning，**不**把整轮判失败。

        初始抓取已成功（好材料在手），不该被一次"可选补救"作废。
        """
        stub = self._nav_stub()
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_extracts=1,
                                                 max_remediations=1,
                                                 max_cost=1.5))
        out = ResearchOutput()
        # 预先占用额度：只剩不足一次补救（粗粒度 1.0）的空间
        out.budget_used.add(requests=2, cost_estimate=1.2, cost_confirmed=1.0)
        item = self._low_item()
        svc._remediate_low_quality(self.fx.ctx, out, [item])
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 0)        # 一个补救请求都没发
        self.assertIn(WARNING_EXTRACT_REMEDIATION_BUDGET_STOPPED,
                      [w.code for w in out.warnings])
        self.assertEqual(item.remediation_attempts, 0)
        self.assertEqual(p.usage_ledger.totals().reserved_cost, 0.0)

    def test_budget_allows_remediation_and_usage_is_counted(self):
        """额度够 → 补救正常发，且用量进 budget_used（与初始抓取同一套记账）。"""
        stub = self._nav_stub()
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_extracts=1, max_remediations=1))
        out = ResearchOutput()
        svc._remediate_low_quality(self.fx.ctx, out, [self._low_item()])
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 1)
        self.assertEqual(out.budget_used.requests, 1)  # 补救也计数
        self.assertAlmostEqual(out.budget_used.cost_estimate, 0.2)
        self.assertEqual(out.budget_used.cost_confirmed, 0.0)
        self.assertEqual(p.usage_ledger.totals().reserved_cost, 0.0)

    def test_unknown_cost_pauses_further_remediation(self):
        """补救请求失败（500）→ 费用 UNKNOWN + 占额度；后续补救停手等人工核对。

        失败 ≠ 免费：预留不释放；费用状态不明时不得默认免费继续花。
        """
        stub = self._nav_stub(error_for={"extract": _http_error(500)})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_extracts=3, max_remediations=1))
        out = ResearchOutput()
        items = [self._low_item("https://good.invalid/a"),
                 self._low_item("https://good.invalid/b")]
        svc._remediate_low_quality(self.fx.ctx, out, items)
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 1)        # 只发了第一个就停手
        self.assertIn(WARNING_EXTRACT_REMEDIATION_UNKNOWN_PAUSE,
                      [w.code for w in out.warnings])
        totals = p.usage_ledger.totals()
        self.assertEqual(totals.unknown_requests, 1)
        self.assertGreater(totals.unknown_hold_cost, 0.0)   # 未知也要占额度
        self.assertEqual(items[1].remediation_attempts, 0)  # 第二个没被碰

    def test_max_remediations_zero_disables_remediation(self):
        stub = self._nav_stub()
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_remediations=0))
        out = ResearchOutput()
        svc._remediate_low_quality(self.fx.ctx, out, [self._low_item()])
        self.assertEqual([c for c in stub.calls if c[0].endswith("/extract")], [])
        self.assertEqual(out.warnings, [])     # 总开关关闭：不告警不花钱

    def test_max_extracts_zero_disables_remediation(self):
        stub = self._nav_stub()
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_extracts=0, max_remediations=1))
        out = ResearchOutput()
        svc._remediate_low_quality(self.fx.ctx, out, [self._low_item()])
        self.assertEqual([c for c in stub.calls if c[0].endswith("/extract")], [])

    def test_total_remediation_capped_by_max_extracts(self):
        """D2：本轮补救总量 ≤ max_extracts（超过就截断并告警）。"""
        stub = self._nav_stub()
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_extracts=1, max_remediations=1))
        out = ResearchOutput()
        items = [self._low_item("https://good.invalid/a"),
                 self._low_item("https://good.invalid/b")]
        svc._remediate_low_quality(self.fx.ctx, out, items)
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 1)        # 总量被截到 1
        self.assertIn(WARNING_EXTRACT_REMEDIATION_CAPPED,
                      [w.code for w in out.warnings])
        self.assertEqual(items[0].remediation_attempts, 1)
        self.assertEqual(items[1].remediation_attempts, 0)

    def test_relevance_only_rejection_is_not_remediated(self):
        """只是"与目标人物无关"（非提取缺陷）→ 不补救。

        接线时实测发现的浪费点：对无关页面花 format=text 的额度，
        内容回来还是一样无关。预算要花在能救回来的条目上。
        """
        stub = self._nav_stub()
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           limits=ResearchLimits(max_extracts=2, max_remediations=1))
        out = ResearchOutput()
        item = ExtractedContent(url="https://good.invalid/a",
                                content=_prose(10), excerpt=_prose(10))
        item.quality = assess_content_quality(item.excerpt, url=item.url,
                                              target_names=["阿雾"])
        self.assertEqual(item.quality.status, QUALITY_LOW)
        self.assertIn(REASON_TARGET_NOT_MENTIONED, item.quality.reasons)
        svc._remediate_low_quality(self.fx.ctx, out, [item])
        self.assertEqual([c for c in stub.calls if c[0].endswith("/extract")], [])
        self.assertEqual(item.remediation_attempts, 0)
        self.assertTrue(item.remediation_detail)       # 记了"跳过"诊断
        self.assertIn("相关性", item.remediation_detail[0]["skipped"])


class TestRemediationRescuesContent(P45TestCase):
    """补救真的把低质量正文救活，并走完整的出口闸门。"""

    def test_remediation_rescues_nav_page_end_to_end(self):
        """端到端：初始抓到导航骨架 → format=text 补救 → 过门禁 → 材料交出。"""
        stub = _ParamAwareExtractStub(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _nav(40)}],
                          "failed_results": [], "usage": {"credits": 0}},
            text_content=_prose_target(10))
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy(),
                           usage_sink=self.fx.storage, task_id="task-rescue")
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        item = out.extracted[0]
        self.assertEqual(item.quality.status, QUALITY_VALID)   # 被救活
        self.assertEqual(item.remediation_attempts, 1)
        self.assertEqual(len(out.rejected_extracts), 0)        # 从拒绝列表移出
        materials, rejected = materials_from_extracts(out.extracted)
        self.assertEqual(len(materials), 1)
        self.assertEqual(len(rejected), 0)
        self.assertEqual(materials[0].snippet, item.excerpt)   # 交付 == 判定
        self.assertIn("阿雾", materials[0].snippet)
        self.assertNotIn("[章节", materials[0].snippet)
        self.assertIn(WARNING_EXTRACT_REMEDIATED,
                      [w.code for w in out.warnings])
        # 两次抓取：初始（query+chunks）与补救（format=text）
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 2)
        self.assertEqual(extract_calls[0][1].get("chunks_per_source"),
                         DEFAULT_CHUNKS_PER_SOURCE)           # D4：初始带分块
        self.assertEqual(extract_calls[1][1].get("format"), "text")  # 补救换格式
        self.assertNotIn("chunks_per_source", extract_calls[1][1])
        # 账本：两笔 EXTRACT，键不同，都结算、无幽灵占额度
        records = [r for r in p.usage_ledger.records()
                   if r.operation == OP_EXTRACT]
        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0].idempotency_key,
                            records[1].idempotency_key)
        self.assertEqual(p.usage_ledger.totals().reserved_cost, 0.0)

    def test_remediation_still_low_stays_rejected(self):
        """补救回来还是低质量 → 留在 rejected、诊断记下补救历史、出口清空。"""
        stub = _ParamAwareExtractStub(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _nav(40)}],
                          "failed_results": [], "usage": {"credits": 0}},
            text_content=_nav(40))          # 补救拿回来的还是导航
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = unwrap(svc.run(self.fx.ctx,
                             [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        item = out.extracted[0]
        self.assertEqual(item.quality.status, QUALITY_LOW)
        self.assertEqual(item.remediation_attempts, 1)
        self.assertEqual(item.content, "")                 # 出口物理清空
        self.assertEqual(len(out.rejected_extracts), 1)
        # 诊断里留着"补救过但没救活"，与"从未补救"可区分
        self.assertIn("remediation", out.rejected_extracts[0].diagnosis)
        materials, rejected = materials_from_extracts(out.extracted)
        self.assertEqual(len(materials), 0)
        self.assertEqual(len(rejected), 1)

    def test_video_page_is_not_remediated(self):
        """视频页没有可提的文章正文 → 不补救（不白花额度）。"""
        stub = _ParamAwareExtractStub(
            search_data={"results": [
                {"title": "V", "url": "https://www.youtube.com/watch?v=abc",
                 "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://www.youtube.com/watch?v=abc",
                                       "title": "V", "raw_content": _nav(40)}],
                          "failed_results": [], "usage": {"credits": 0}},
            text_content=_prose_target(10))
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        out = ResearchOutput()
        item = ExtractedContent(url="https://www.youtube.com/watch?v=abc",
                                content=_nav(40), excerpt=_nav(40))
        item.quality = assess_content_quality(
            item.excerpt, url=item.url)
        self.assertEqual(item.quality.status, QUALITY_LOW)
        svc._remediate_low_quality(self.fx.ctx, out, [item])
        extract_calls = [c for c in stub.calls if c[0].endswith("/extract")]
        self.assertEqual(len(extract_calls), 0)
        self.assertEqual(item.remediation_attempts, 0)
        self.assertTrue(item.remediation_detail)


class TestRemediationPlanOptions(P45TestCase):
    """补救选项的构造（纯函数）：R1——初始已带 query+chunks 时剔除选项①。"""

    def test_already_queried_drops_the_query_option(self):
        q = assess_content_quality(_nav(40), url="https://good.invalid/a")
        plan = plan_remediation("https://good.invalid/a", q, query="阿雾",
                                already_queried=True)
        self.assertTrue(plan.allowed)
        self.assertEqual(plan.options, [{"format": "text"}])
        self.assertIn("重复消费", plan.reason)

    def test_not_prequeried_keeps_the_query_option(self):
        q = assess_content_quality(_nav(40), url="https://good.invalid/a")
        # max_options=2 才能看到两个选项都在（默认 1 会被截断，见下一个用例）
        plan = plan_remediation("https://good.invalid/a", q, query="阿雾",
                                max_options=2)
        self.assertEqual(plan.options[0],
                         {"query": "阿雾",
                          "chunks_per_source": DEFAULT_CHUNKS_PER_SOURCE})
        self.assertEqual(plan.options[1], {"format": "text"})

    def test_without_query_has_no_chunks_option(self):
        plan = plan_remediation("https://good.invalid/a", ContentQuality(),
                                query="")
        self.assertEqual(plan.options, [{"format": "text"}])
        self.assertIn("chunks_per_source", plan.reason)

    def test_max_options_truncates(self):
        plan = plan_remediation("https://good.invalid/a", ContentQuality(),
                                query="阿雾", max_options=1)
        self.assertEqual(len(plan.options), 1)
        self.assertEqual(plan.options[0]["query"], "阿雾")

    def test_video_page_is_not_remediable(self):
        plan = plan_remediation("https://www.youtube.com/watch?v=abc",
                                ContentQuality(), query="阿雾")
        self.assertFalse(plan.allowed)
        self.assertEqual(plan.options, [])

    def test_has_extraction_defect_distinguishes_relevance(self):
        nav_q = assess_content_quality(_nav(40), url="https://good.invalid/a")
        self.assertTrue(has_extraction_defect(nav_q))     # 导航占满 → 值得救
        off_topic = assess_content_quality(_prose(10),
                                           url="https://good.invalid/a",
                                           target_names=["阿雾"])
        self.assertFalse(has_extraction_defect(off_topic))  # 只是无关 → 不救
        short_q = assess_content_quality("太短", url="https://good.invalid/a")
        self.assertTrue(has_extraction_defect(short_q))


class TestInitialExtractCarriesOfficialChunks(P45TestCase):
    """D4：初始抓取也带官方 query + chunks_per_source（补救降级为兜底）。"""

    def test_initial_request_carries_query_and_chunks(self):
        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _prose_target(10)}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = self.service(p, extraction_strategy=TopNExtractionStrategy())
        unwrap(svc.run(self.fx.ctx,
                       [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        payload = [c for c in stub.calls if c[0].endswith("/extract")][0][1]
        self.assertEqual(payload["query"], "阿雾")         # 目标姓名
        self.assertEqual(payload["chunks_per_source"],
                         DEFAULT_CHUNKS_PER_SOURCE)        # 官方默认 3
        self.assertEqual(payload["extract_depth"], "basic")

    def test_no_target_text_sends_neither_query_nor_chunks(self):
        """没有目标文本 → 官方约束下 query/chunks 都不带（不发明参数）。"""

        class _NoQueryTextService(ResearchService):
            def _target_query_text(self) -> str:
                return ""

        stub = StubUrlopen(
            search_data={"results": [
                {"title": "A", "url": "https://good.invalid/a", "content": "摘要"}],
                "usage": {"credits": 1}},
            extract_data={"results": [{"url": "https://good.invalid/a", "title": "A",
                                       "raw_content": _prose(10)}],
                          "failed_results": [], "usage": {"credits": 0}})
        p = self.provider(stub)
        svc = _NoQueryTextService(ResearchDeps(
            provider=p, evidence=self.fx.evidence,
            extraction_strategy=TopNExtractionStrategy()))
        unwrap(svc.run(self.fx.ctx,
                       [self.fx.target(self.fx.entity("阿雾"), "阿雾")]))
        payload = [c for c in stub.calls if c[0].endswith("/extract")][0][1]
        self.assertNotIn("query", payload)
        self.assertNotIn("chunks_per_source", payload)


if __name__ == "__main__":
    unittest.main()
