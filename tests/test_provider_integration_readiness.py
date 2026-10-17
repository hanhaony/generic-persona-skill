"""P4.1 真实研究 Provider 接入准备 —— 集成就绪度测试（全程离线，无密钥，不触网）。

定位（对应 P4.1 验收清单）：
    本轮**不改任何产品代码**——审查结论是 `Provider` 协议 / `ProviderRegistry` /
    配置校验 / 密钥脱敏 / 离线开关 / `BudgetGate` / 错误分类**已满足**真实 Provider
    接入的全部前置要求。本文件用一个"真实形状"的 Provider 替身，把下列就绪度保证
    **跑成可断言的集成证据**：

    1. 插拔 seam：真实 Provider 注入 `ResearchService` 即可跑通研究，来源元数据
       （url / published_at / source_kind / canonical_key）如实落库，不伪造；
    2. 配置注入：经 `ProviderRegistry` 取 Provider 时，`ProviderConfig`（超时/重试边界）
       被注入到 Provider；
    3. 未注册的真实 Provider 名 → **明确报错**，不静默回退到 Mock；
    4. 离线硬保证：真实 Provider 一旦尝试联网，`OfflineGuardProvider` 立即抛错；
       整条研究链路可在 `network_blocked()` 下零 socket 跑完；
    5. 预算预检先行：预算耗尽时** Provider 根本不会被调用**，任务按既有契约
       暂停并存检查点（不继续消费资源）；
    6. 错误分类：超时（可重试）/ 限流 / 鉴权（不可重试）分类与重试语义端到端保持；
    7. 空结果 → 业务结果而非错误，不制造来源。

不引入第三方依赖；只用标准库 + 既有 `persona.*` 模块。
"""

import shutil
import socket
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import PausedReason, ProvenanceNamespace, TaskStatus
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query, UsageReport
from persona.modules.evidence import EvidenceService
from persona.modules.research import (
    PersonQueryTarget,
    ResearchDeps,
    ResearchService,
)
from persona.orchestrator import UnitOutcome, WorkUnit
from persona.providers.base import (
    CapabilitySet,
    ChatResponse,
    HealthStatus,
    ProviderConfig,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from persona.providers.mock import (
    OfflineGuardProvider,
    OfflineViolation,
    network_blocked,
)
from persona.providers.registry import ProviderRegistry, default_registry
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_p41"

# 虚构材料（AC-16）：`.invalid` 是保留的、 guaranteed 不可路由的 TLD，
# 即便脱离护栏也**不可能**连到真实服务器——双重保证"测试不触网"。
RESULT_AWU = SearchResult(
    title="雾港设定资料集",
    url="https://example.invalid/mist-harbor",
    canonical_key="key_mist_harbor",
    # P4.8 / D-2：来源必须提到目标才算相关 → 摘要里出现"阿雾"
    snippet="雾港的地理与机构说明；阿雾是灯塔的常年值守者。",
    published_at="2021-05-04",
    source_kind_guess="OFFICIAL_MATERIAL",
)


# --------------------------------------------------------------------------
# 测试替身：按"真实 Provider 包装层"的方式实现 Provider 协议（但不触网）
# --------------------------------------------------------------------------


class StubRealLikeProvider:
    """模拟真实搜索 Provider 的**测试替身**（确定性、默认离线、无密钥）。

    - 持有构造注入的 `ProviderConfig`（真实 Provider 用它做超时/重试边界）；
    - `search` 返回带 url / published_at / source_kind 的**真实形状** `SearchResult`；
    - `error_for` 可按检索词注入 `EXTERNAL_TIMEOUT` / `RATE_LIMIT` / `AUTH` 等；
    - 记录 `calls`：用于证明"预算耗尽时 Provider 根本未被调用"；
    - `attempt_network=True` 时尝试一次真实 `socket.create_connection`
      （仅在离线护栏测试里打开，用于证明护栏会拦截）。
    """

    def __init__(self, results_by_query=None, *, error_for=None, config=None,
                 attempt_network=False, name="stub-real-search"):
        self.results_by_query = dict(results_by_query or {})
        self.error_for = dict(error_for or {})
        self.config = config or ProviderConfig()
        self.attempt_network = attempt_network
        self.name = name
        self.calls: list[str] = []

    def capabilities(self):
        return Ok(CapabilitySet(provider_name=self.name, supports_search=True,
                                rate_limit_per_minute=60, cost_model="per-call"))

    def chat(self, ctx, req):
        return Ok(ChatResponse(text="", model_id=self.name))

    def search(self, ctx, req):
        self.calls.append(req.query)
        if self.attempt_network:
            # 真实 Provider 会在这里建立连接；替身用一次真实 socket 调用来证明护栏有效。
            # .invalid 域名保证即使脱离护栏也连不到真实服务器。
            socket.create_connection(("example.invalid", 443), timeout=1)
        code = self.error_for.get(req.query)
        if code is not None:
            return Err(new_error(code, f"{self.name} 注入失败：{req.query}",
                                 trace_ref=ctx.trace_ref))
        results = list(self.results_by_query.get(req.query, []))
        usage = UsageDelta(requests=1, tokens_in=None, tokens_out=None, latency_ms=5)
        return Ok(SearchResponse(results=results, usage=usage), usage=usage)

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=len(self.calls)))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------


class ReadinessFixture:
    def __init__(self, tmp: str):
        self.tmp = tmp
        self.storage = SqliteStorage(Path(tmp) / "p41.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.evidence = EvidenceService(self.storage)

    def entity(self, name: str = "阿雾") -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER"},
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def target(self, entity_id: str, name: str = "阿雾", **kw) -> PersonQueryTarget:
        return PersonQueryTarget(entity_id=entity_id, display_name=name, **kw)

    def service(self, provider, *, search_metadata=None) -> ResearchService:
        return ResearchService(ResearchDeps(
            provider=provider, evidence=self.evidence,
            search_metadata=dict(search_metadata or {})))

    def source_rows(self) -> list[dict]:
        return unwrap(self.storage.query(
            self.ctx, "Source", Query(limit=MAX_QUERY_LIMIT))).items

    def count(self, kind: str) -> int:
        return unwrap(self.storage.query(
            self.ctx, kind, Query(limit=MAX_QUERY_LIMIT))).total

    def close(self):
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class ReadinessTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="persona_p41_")
        self.fx = ReadinessFixture(self._tmp)

    def tearDown(self):
        self.fx.close()


# --------------------------------------------------------------------------
# 1. 插拔 seam：真实 Provider 注入即用，来源元数据如实落库
# --------------------------------------------------------------------------


class TestProviderPlugInSeam(ReadinessTestCase):
    def test_real_like_provider_runs_full_research_and_preserves_source_metadata(self):
        stub = StubRealLikeProvider(results_by_query={"阿雾": [RESULT_AWU]})
        svc = self.fx.service(stub)
        target = self.fx.target(self.fx.entity("阿雾"), "阿雾")

        result = unwrap(svc.run(self.fx.ctx, [target]))

        # 研究跑通、Provider 确被调用一次
        self.assertTrue(result.source_ids)
        self.assertEqual(stub.calls, ["阿雾"])
        # 来源元数据如实落库（url / 日期 / 类型 / 定位键），不伪造
        rows = self.fx.source_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.get("url"), "https://example.invalid/mist-harbor")
        self.assertEqual(row.get("canonical_key"), "key_mist_harbor")
        # published_at 以 FuzzyDate 形状保守保存（不推断）
        self.assertIn("2021-05-04", str(row.get("published_at")))
        # Research 只登记来源，绝不凭空造证据
        self.assertEqual(self.fx.count("EvidenceItem"), 0)
        # 材料可追溯：material 带上 source_id + url
        mat = result.materials[0]
        self.assertEqual(mat.url, "https://example.invalid/mist-harbor")
        self.assertTrue(mat.source_id)

    def test_provider_config_is_injected_via_registry(self):
        registry = ProviderRegistry()
        seen: dict = {}

        def factory(cfg, secrets=None):
            seen["cfg"] = cfg
            return StubRealLikeProvider(config=cfg)

        registry.register("stub_real", factory)
        cfg = ProviderConfig(per_call_timeout_ms=1234, max_retries=5,
                             backoff_base_ms=250)
        provider = unwrap(registry.get("stub_real", cfg))

        # 超时/重试边界经 registry 注入到 Provider（真实 Provider 据此设超时与退避）
        self.assertIs(seen["cfg"], cfg)
        self.assertEqual(provider.config.per_call_timeout_ms, 1234)
        self.assertEqual(provider.config.max_retries, 5)
        self.assertEqual(provider.config.backoff_base_ms, 250)

    def test_unknown_real_provider_name_is_clear_error_not_silent_fallback(self):
        registry = default_registry()
        result = registry.get("some-real-search-api")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)
        self.assertIn("未注册", result.error.message)   # 明确报错，不静默回退 Mock
        self.assertIn("fake", result.error.message)     # 并告知当前可用项


# --------------------------------------------------------------------------
# 2. 离线硬保证：真实 Provider 一联网即被拦；整链可零 socket 跑完
# --------------------------------------------------------------------------


class TestOfflineEnforcement(ReadinessTestCase):
    def test_offline_guard_blocks_real_network_attempt_at_seam(self):
        stub = StubRealLikeProvider(results_by_query={"阿雾": [RESULT_AWU]},
                                    attempt_network=True)
        guarded = OfflineGuardProvider(stub)
        # 真实 Provider 尝试建连接 → 离线护栏立即抛错（不发出真实请求）
        with self.assertRaises(OfflineViolation):
            guarded.search(self.fx.ctx, SearchRequest(query="阿雾"))

    def test_full_research_runs_under_network_block_with_zero_sockets(self):
        stub = StubRealLikeProvider(results_by_query={"阿雾": [RESULT_AWU]})
        svc = self.fx.service(OfflineGuardProvider(stub))
        target = self.fx.target(self.fx.entity("阿雾"), "阿雾")
        # 整条研究链路在 socket 被屏蔽的情况下跑通 = 确认无真实网络请求
        with network_blocked():
            result = unwrap(svc.run(self.fx.ctx, [target]))
        self.assertTrue(result.source_ids)
        self.assertEqual(stub.calls, ["阿雾"])


# --------------------------------------------------------------------------
# 3. 预算预检先行：预算耗尽 → Provider 不被调用 + 暂停 + 存检查点（需求 #6）
# --------------------------------------------------------------------------


class _ProviderCallingHandler:
    """最小 UnitHandler：每个单元的 execute 调一次 Provider.search。

    用来证明 TaskRunner 的预算闸门在**调用 Provider 之前**就拦下。
    """

    def __init__(self, provider, unit_count=3):
        self.provider = provider
        self.unit_count = unit_count

    def plan(self, task):
        return [WorkUnit(name=f"q:{i}", stage="RESEARCH",
                         estimate=UsageDelta(requests=1)) for i in range(self.unit_count)]

    def execute(self, ctx, unit, *, idempotency_key=""):
        resp = self.provider.search(ctx, SearchRequest(query="阿雾"))
        if is_err(resp):
            return resp
        return Ok(UnitOutcome(usage=UsageDelta(requests=1)))


class TestBudgetGuardsProviderCall(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="persona_p41_budget_")
        self.storage = SqliteStorage(Path(self._tmp) / "budget.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        from persona.orchestrator import TaskService
        self.service = TaskService(self.storage)

    def tearDown(self):
        self.storage.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_budget_exhaustion_pauses_and_never_calls_provider(self):
        from persona.orchestrator import BudgetSpec, TaskDraft, TaskRunner

        stub = StubRealLikeProvider(results_by_query={"阿雾": [RESULT_AWU]})
        handler = _ProviderCallingHandler(stub, unit_count=3)
        runner = TaskRunner(self.service, handler)

        # 预算只够 2 次调用，但有 3 个单元
        task = unwrap(self.service.create_task(
            self.ctx,
            TaskDraft(task_type="RESEARCH", stage_sequence=["RESEARCH"],
                      budget_limit=BudgetSpec(max_requests=2))))
        unwrap(self.service.start(self.ctx, task.id))

        result = unwrap(runner.run_to_completion(self.ctx, task.id))

        # 第 3 个单元在预算闸门处被拦：Provider 只被调用 2 次，没有超支消费
        self.assertEqual(len(stub.calls), 2)
        # 任务按既有契约暂停（BUDGET），不是失败
        record = unwrap(self.service.get(self.ctx, task.id))
        self.assertEqual(record.status, TaskStatus.PAUSED)
        self.assertEqual(record.paused_reason, PausedReason.BUDGET)
        # 暂停前先落检查点（进度不丢）
        cp = unwrap(self.service.load_checkpoint(self.ctx, task.id))
        self.assertEqual(len(cp.completed_units), 2)
        # 汇总状态如实反映暂停
        self.assertEqual(result.status, TaskStatus.PAUSED)


# --------------------------------------------------------------------------
# 4. 错误分类端到端：超时(可重试) / 限流 / 鉴权(不可重试) 语义保持
# --------------------------------------------------------------------------


class TestErrorClassificationEndToEnd(ReadinessTestCase):
    def _two_query_target(self):
        # 带 topic → plan_queries 生成两条检索词："阿雾"(identity) + "阿雾 结局"(topic)
        return self.fx.target(self.fx.entity("阿雾"), "阿雾", topic="结局")

    def test_retryable_timeout_on_one_query_continues_and_warns(self):
        stub = StubRealLikeProvider(
            results_by_query={"阿雾": [RESULT_AWU]},
            error_for={"阿雾 结局": ErrorCode.EXTERNAL_TIMEOUT})
        svc = self.fx.service(stub)

        result = unwrap(svc.run(self.fx.ctx, [self._two_query_target()]))

        # 单条可重试失败 → 记录 warning 并继续，成功查询的来源照样登记
        self.assertTrue(result.source_ids)
        codes = [w.code for w in result.warnings]
        self.assertIn("QUERY_FAILED", codes)
        # 超时错误的语义：可重试、可恢复（真实 Provider 的退避重试靠这个标志）
        self.assertTrue(new_error(ErrorCode.EXTERNAL_TIMEOUT).retryable)

    def test_all_queries_timeout_reports_error_with_code(self):
        stub = StubRealLikeProvider(
            error_for={"阿雾": ErrorCode.EXTERNAL_TIMEOUT,
                       "阿雾 结局": ErrorCode.EXTERNAL_TIMEOUT})
        svc = self.fx.service(stub)

        result = svc.run(self.fx.ctx, [self._two_query_target()])

        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_TIMEOUT)
        self.assertEqual(self.fx.count("Source"), 0)   # 全失败不留下半点伪装成果

    def test_auth_failure_is_classified_not_retryable(self):
        stub = StubRealLikeProvider(
            error_for={"阿雾": ErrorCode.EXTERNAL_AUTH,
                       "阿雾 结局": ErrorCode.EXTERNAL_AUTH})
        svc = self.fx.service(stub)

        result = svc.run(self.fx.ctx, [self._two_query_target()])

        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_AUTH)
        # 鉴权失败不可重试（重试无益），且不可自动恢复——需人改配置
        spec_err = new_error(ErrorCode.EXTERNAL_AUTH)
        self.assertFalse(spec_err.retryable)
        self.assertFalse(spec_err.recoverable)

    def test_rate_limit_is_retryable(self):
        # 限流可重试、可恢复（稍后自动继续）
        spec_err = new_error(ErrorCode.EXTERNAL_RATE_LIMIT)
        self.assertTrue(spec_err.retryable)
        self.assertTrue(spec_err.recoverable)

    def test_empty_results_is_business_outcome_not_error(self):
        stub = StubRealLikeProvider(results_by_query={"阿雾": []})
        svc = self.fx.service(stub)

        result = unwrap(svc.run(self.fx.ctx, [self._two_query_target()]))

        # 0 结果是业务结果，不是错误；不制造任何来源
        self.assertEqual(result.source_ids, [])
        self.assertEqual(self.fx.count("Source"), 0)
        self.assertGreaterEqual(result.no_result_count, 1)


if __name__ == "__main__":
    unittest.main()
