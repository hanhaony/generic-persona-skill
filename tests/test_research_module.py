"""Research 模块测试（H-11A；仓库 ROADMAP 编号为 P2 的 Research 部分）。

全部跑在**临时目录**的 SQLite 文件上，**不触网**（`OfflineGuardProvider` + `network_blocked`
双重硬保证），不调真实模型 / 搜索 API，不使用密钥。

覆盖（对应用户验收清单 1~9 + 模块诚实边界）：
  1. 计划（确定性模板；空目标 / 缺 entity_id / 无名字的校验失败）
  2. 检索（成功、0 结果为业务结果而非错误、Provider 异常传播、BUDGET_EXCEEDED 立即传播、
     `search_metadata` 原样透传）
  3. 过滤排序（无效材料带原因拒绝、批内去重、确定性顺序、对象边界附带）
  4. 登记（经 Evidence；登记失败不伪装成功；重复登记幂等）
  5. 端到端 run（计数、来源可被 Evidence 消费与追溯、部分失败继续、全失败上报）
  6. 模拟隔离（Research 不产生 EvidenceItem / 不写 SIMULATED；存储层闸门仍在）
  7. 重复调用幂等
  8. 模块边界（只拿 register_source；模块里没有 add_evidence / 存储层 / sqlite3）
  9. 复用既有 Mock Provider 家族（FakeProvider / RecordingProvider）

对应文档：INTERFACES §5、DATA_MODEL §4.3/§4.5、ERROR_HANDLING §2.3、DECISIONS §2.23。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    FetchStatus,
    ProvenanceNamespace,
    SourceKind,
)
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query, UsageReport
from persona.modules.evidence import EvidenceService
from persona.modules.research import (
    FETCH_NO_RESULT,
    WARNING_BATCH_DEDUP,
    WARNING_CANDIDATE_REJECTED,
    WARNING_EMPTY_INPUT,
    WARNING_NO_RESULT,
    WARNING_QUERY_FAILED,
    CollectOutcome,
    PlannedQuery,
    PersonQueryTarget,
    RankedCandidate,
    RankedCandidates,
    ResearchDeps,
    ResearchService,
    SourceRegistrarPort,
)
from persona.providers.base import (
    CapabilitySet,
    ChatResponse,
    HealthStatus,
    ProviderConfig,
    SearchResponse,
    SearchResult,
)
from persona.providers.mock import FakeProvider, OfflineGuardProvider, RecordingProvider, network_blocked
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_research"

# 固定测试材料：地名 / 角色名均为虚构，不含任何真实人物专属设定（AC-16）。
MATERIAL_A = SearchResult(
    title="雾港设定资料集",
    url="https://example.invalid/mist-harbor",
    canonical_key="key_mist_harbor",
    snippet="雾港的地理与机构说明。",
    published_at="2021-05-04",
    source_kind_guess="OFFICIAL_MATERIAL",
)
MATERIAL_B = SearchResult(
    title="星灯访谈录",
    url="https://example.invalid/starlamp-interview",
    canonical_key="key_starlamp_interview",
    snippet="主创访谈，谈及角色设定过程。",
    published_at="2022-01",
    source_kind_guess="INTERVIEW",
)


# --------------------------------------------------------------------------
# 辅助：可注入的 Mock Research Provider（确定性、离线、无密钥）
# --------------------------------------------------------------------------


class MockResearchProvider:
    """**Mock Research Provider**：按检索词返回**固定研究材料**。

    - 完全确定性：同一检索词永远返回同一批材料；
    - 完全离线：不建任何连接（配合 `OfflineGuardProvider` / `network_blocked` 硬保证）；
    - 可注入失败：`error_for={query: ErrorCode}` 模拟单条 / 全部失败与预算耗尽；
    - 记录 `last_metadata`，用于断言 `search_metadata` 是否原样透传。
    """

    def __init__(
        self,
        materials: dict[str, list[SearchResult]] | None = None,
        *,
        error_for: dict[str, ErrorCode] | None = None,
    ) -> None:
        self.materials = dict(materials or {})
        self.error_for = dict(error_for or {})
        self.calls: list[str] = []
        self.last_metadata: dict = {}

    # --- Provider 协议 ---

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="mock-research", supports_search=True))

    def chat(self, ctx, req):
        return Ok(ChatResponse(text=""))

    def search(self, ctx, req):
        self.calls.append(req.query)
        self.last_metadata = dict(req.metadata or {})
        code = self.error_for.get(req.query)
        if code is not None:
            return Err(new_error(code, f"注入失败：{req.query}", trace_ref=ctx.trace_ref))
        usage = UsageDelta(requests=1, tokens_in=None, tokens_out=None, latency_ms=5)
        return Ok(SearchResponse(results=list(self.materials.get(req.query, [])), usage=usage),
                  usage=usage)

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=len(self.calls)))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class RecordingRegistrar:
    """只实现 `register_source` 的登记端口替身：记录被调用的方法名。"""

    def __init__(self, delegate) -> None:
        self.delegate = delegate
        self.calls: list[str] = []

    def register_source(self, ctx, draft):
        self.calls.append("register_source")
        return self.delegate.register_source(ctx, draft)


# --------------------------------------------------------------------------
# Fixture
# --------------------------------------------------------------------------


class ResearchFixture:
    """临时数据库 + Evidence 服务 + Research 服务（按需构造）+ 调用上下文。"""

    def __init__(self, tmp: str) -> None:
        self.tmp = tmp
        self.storage = SqliteStorage(Path(tmp) / "research.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.evidence = EvidenceService(self.storage)

    # --- 基础构件 ---

    def entity(self, name: str = "阿雾") -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER"},
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def target(self, entity_id: str, name: str = "阿雾", **kwargs) -> PersonQueryTarget:
        return PersonQueryTarget(entity_id=entity_id, display_name=name, **kwargs)

    def service(self, provider, *, search_metadata: dict | None = None,
                registrar=None) -> ResearchService:
        evidence = registrar if registrar is not None else RecordingRegistrar(self.evidence)
        return ResearchService(ResearchDeps(
            provider=provider, evidence=evidence,
            search_metadata=dict(search_metadata or {})))

    def count(self, kind: str) -> int:
        page = unwrap(self.storage.query(self.ctx, kind, Query(limit=MAX_QUERY_LIMIT)))
        return page.total

    def rows(self, kind: str) -> list[dict]:
        return unwrap(self.storage.query(self.ctx, kind, Query(limit=MAX_QUERY_LIMIT))).items

    def close(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


class FixtureTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="persona_research_")
        self.fx = ResearchFixture(self._tmp)

    def tearDown(self) -> None:
        self.fx.close()


# --------------------------------------------------------------------------
# 1. 计划
# --------------------------------------------------------------------------


class TestPlanQueries(FixtureTestCase):
    def test_empty_targets_is_validation_error(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.plan_queries(self.fx.ctx, [])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_missing_entity_id_is_rejected(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.plan_queries(self.fx.ctx, [PersonQueryTarget(entity_id="", display_name="阿雾")])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_invalid_target_shape_is_rejected(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.plan_queries(self.fx.ctx, [{"entity_id": "x"}])  # type: ignore[list-item]
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_nameless_target_is_rejected(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.plan_queries(self.fx.ctx, [PersonQueryTarget(entity_id="pe_x")])
        self.assertTrue(is_err(result))

    def test_plan_is_deterministic_and_contains_no_preset_conclusion(self):
        svc = self.fx.service(MockResearchProvider())
        hero = self.fx.entity("阿雾")
        target = self.fx.target(hero, "阿雾", work_titles=["雾港"], topic="结局")
        first = unwrap(svc.plan_queries(self.fx.ctx, [target]))
        second = unwrap(svc.plan_queries(self.fx.ctx, [target]))
        self.assertEqual([q.query_text for q in first], [q.query_text for q in second])
        self.assertEqual(len(first), 2)                      # identity + topic
        self.assertEqual([q.intent for q in first], ["identity", "topic"])
        for planned in first:
            self.assertIn("阿雾", planned.query_text)
            self.assertEqual(planned.target_entity_ids, [hero])
            self.assertNotIn("是", planned.query_text)        # 不内嵌结论式断言
            self.assertNotIn("因为", planned.query_text)

    def test_alias_used_when_display_name_missing(self):
        svc = self.fx.service(MockResearchProvider())
        hero = self.fx.entity("阿雾")
        planned = unwrap(svc.plan_queries(
            self.fx.ctx, [PersonQueryTarget(entity_id=hero, aliases=["小雾"])]))
        self.assertEqual(planned[0].query_text, "小雾")

    def test_no_topic_yields_single_query(self):
        svc = self.fx.service(MockResearchProvider())
        hero = self.fx.entity("阿雾")
        planned = unwrap(svc.plan_queries(self.fx.ctx, [self.fx.target(hero)]))
        self.assertEqual(len(planned), 1)


# --------------------------------------------------------------------------
# 2. 检索（collect）
# --------------------------------------------------------------------------


class TestCollect(FixtureTestCase):
    def test_empty_query_is_validation_error(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.collect(self.fx.ctx, PlannedQuery(query_text="   "))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_QUERY_EMPTY)

    def test_non_planned_query_is_rejected(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.collect(self.fx.ctx, "阿雾")  # type: ignore[arg-type]
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_successful_collect_returns_results_and_usage(self):
        provider = MockResearchProvider({"阿雾": [MATERIAL_A, MATERIAL_B]})
        svc = self.fx.service(provider)
        outcome = unwrap(svc.collect(self.fx.ctx, PlannedQuery(query_text="阿雾")))
        self.assertIsInstance(outcome, CollectOutcome)
        self.assertEqual(len(outcome.results), 2)
        self.assertEqual(outcome.fetch_status, str(FetchStatus.OK))
        self.assertIsNotNone(outcome.usage)

    def test_zero_results_is_business_outcome_not_error(self):
        svc = self.fx.service(MockResearchProvider({}))
        result = svc.collect(self.fx.ctx, PlannedQuery(query_text="不存在的名字"))
        self.assertIsInstance(result, Ok)
        outcome = unwrap(result)
        self.assertEqual(outcome.results, [])
        self.assertEqual(outcome.fetch_status, FETCH_NO_RESULT)
        self.assertIn(WARNING_NO_RESULT, [w.code for w in result.warnings])

    def test_provider_error_is_propagated(self):
        provider = MockResearchProvider(error_for={"阿雾": ErrorCode.EXTERNAL_UNAVAILABLE})
        svc = self.fx.service(provider)
        result = svc.collect(self.fx.ctx, PlannedQuery(query_text="阿雾"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_UNAVAILABLE)
        self.assertTrue(result.error.retryable)

    def test_budget_exceeded_is_propagated(self):
        provider = MockResearchProvider(error_for={"阿雾": ErrorCode.BUDGET_EXCEEDED})
        svc = self.fx.service(provider)
        result = svc.collect(self.fx.ctx, PlannedQuery(query_text="阿雾"))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.BUDGET_EXCEEDED)

    def test_search_metadata_is_passed_through_verbatim(self):
        provider = MockResearchProvider({"阿雾": [MATERIAL_A]})
        svc = self.fx.service(provider, search_metadata={"fixture_key": "identity"})
        unwrap(svc.collect(self.fx.ctx, PlannedQuery(query_text="阿雾")))
        self.assertEqual(provider.last_metadata, {"fixture_key": "identity"})


# --------------------------------------------------------------------------
# 3. 过滤与排序
# --------------------------------------------------------------------------


class TestFilterAndRank(FixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.svc = self.fx.service(MockResearchProvider())

    def test_empty_input_is_not_a_failure(self):
        result = self.svc.filter_and_rank(self.fx.ctx, [])
        self.assertIsInstance(result, Ok)
        ranked = unwrap(result)
        self.assertEqual(ranked.accepted, [])
        self.assertIn(WARNING_EMPTY_INPUT, [w.code for w in ranked.warnings])

    def test_invalid_materials_rejected_with_reasons(self):
        no_title = SearchResult(title="", url="https://example.invalid/x", canonical_key="k_x")
        no_locator = SearchResult(title="有标题但无定位", url="", canonical_key="")
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, [no_title, no_locator, {"title": "不是 SearchResult"}]))
        self.assertEqual(ranked.accepted, [])
        reasons = {r.reason.split(":")[0] for r in ranked.rejected}
        self.assertEqual(reasons, {"EMPTY_TITLE", "MISSING_LOCATOR", "INVALID_MATERIAL"})
        self.assertIn(WARNING_CANDIDATE_REJECTED, [w.code for w in ranked.warnings])

    def test_batch_dedupe_folds_repeats(self):
        dup = SearchResult(title="雾港设定资料集（转载）", url="https://example.invalid/mist-harbor",
                           canonical_key="key_mist_harbor", snippet="转载")
        ranked = unwrap(self.svc.filter_and_rank(self.fx.ctx, [MATERIAL_A, dup]))
        self.assertEqual(len(ranked.accepted), 1)
        self.assertTrue(any(r.reason.startswith("DUPLICATE_IN_BATCH") for r in ranked.rejected))
        self.assertIn(WARNING_BATCH_DEDUP, [w.code for w in ranked.warnings])

    def test_ordering_is_deterministic(self):
        forward = unwrap(self.svc.filter_and_rank(self.fx.ctx, [MATERIAL_A, MATERIAL_B]))
        backward = unwrap(self.svc.filter_and_rank(self.fx.ctx, [MATERIAL_B, MATERIAL_A]))
        self.assertEqual(
            [c.result.canonical_key for c in forward.accepted],
            [c.result.canonical_key for c in backward.accepted])

    def test_object_boundary_is_attached_to_candidates(self):
        hero = self.fx.entity("阿雾")
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, [MATERIAL_A, MATERIAL_B], target_entity_ids=[hero]))
        self.assertTrue(ranked.accepted)
        for candidate in ranked.accepted:
            self.assertEqual(candidate.target_entity_ids, [hero])
            self.assertIsInstance(candidate, RankedCandidate)


# --------------------------------------------------------------------------
# 4. 登记（register）
# --------------------------------------------------------------------------


class TestRegister(FixtureTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.hero = self.fx.entity("阿雾")
        self.svc = self.fx.service(MockResearchProvider())

    def _ranked(self, results, entity_ids=None) -> RankedCandidates:
        return unwrap(self.svc.filter_and_rank(
            self.fx.ctx, results, target_entity_ids=entity_ids or [self.hero]))

    def test_register_creates_source_via_evidence_only(self):
        outcome = unwrap(self.svc.register(self.fx.ctx, self._ranked([MATERIAL_A, MATERIAL_B])))
        self.assertEqual(outcome.registered_count, 2)
        self.assertEqual(len(outcome.source_ids), 2)
        self.assertEqual(outcome.event_ids, [])          # Research 不产出 SourceEvent
        self.assertEqual(self.fx.count("Source"), 2)
        # 关键隔离：Research 只登记来源，**不产生任何已验证证据**
        self.assertEqual(self.fx.count("EvidenceItem"), 0)
        self.assertEqual(self.fx.count("SourceEvent"), 0)

    def test_source_metadata_is_preserved(self):
        outcome = unwrap(self.svc.register(self.fx.ctx, self._ranked([MATERIAL_A])))
        # 注意：raw 行的 `kind` 是结构列（恒为 "Source"），业务 kind 要用 Evidence 的读视图
        source = unwrap(self.fx.evidence.get_source(self.fx.ctx, outcome.source_ids[0]))
        self.assertEqual(source.title, MATERIAL_A.title)
        self.assertEqual(source.url, MATERIAL_A.url)
        self.assertEqual(source.canonical_key, MATERIAL_A.canonical_key)
        self.assertEqual(source.kind, str(SourceKind.OFFICIAL_MATERIAL))
        self.assertEqual(source.subject_entity_ids, [self.hero])
        self.assertEqual(source.namespace, str(ProvenanceNamespace.PUBLIC_SOURCE))
        self.assertEqual(source.published_at.raw, "2021-05-04")

    def test_unknown_source_kind_falls_back_to_other(self):
        odd = SearchResult(title="怪来源", url="https://example.invalid/o",
                           canonical_key="key_odd", source_kind_guess="SOMETHING_NEW")
        outcome = unwrap(self.svc.register(self.fx.ctx, self._ranked([odd])))
        source = unwrap(self.fx.evidence.get_source(self.fx.ctx, outcome.source_ids[0]))
        self.assertEqual(source.kind, str(SourceKind.OTHER))

    def test_register_failure_is_propagated_not_faked(self):
        # 对象边界指向不存在的实体 → Evidence 拒绝（VALIDATION.entity_missing）
        ghost = "pe_" + "0" * 32
        ranked = self._ranked([MATERIAL_A], entity_ids=[ghost])
        result = self.svc.register(self.fx.ctx, ranked)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_ENTITY_MISSING)
        self.assertEqual(self.fx.count("Source"), 0)     # 没有"部分成功"的假象

    def test_register_is_idempotent(self):
        first = unwrap(self.svc.register(self.fx.ctx, self._ranked([MATERIAL_A])))
        second = unwrap(self.svc.register(self.fx.ctx, self._ranked([MATERIAL_A])))
        self.assertEqual(first.source_ids, second.source_ids)
        self.assertEqual(second.dedup_hits, 1)
        self.assertEqual(self.fx.count("Source"), 1)

    def test_register_empty_ranked_is_noop(self):
        outcome = unwrap(self.svc.register(self.fx.ctx, RankedCandidates()))
        self.assertEqual(outcome.source_ids, [])
        self.assertEqual(outcome.registered_count, 0)
        self.assertEqual(self.fx.count("Source"), 0)


# --------------------------------------------------------------------------
# 5. 端到端 run
# --------------------------------------------------------------------------


class TestRunEndToEnd(FixtureTestCase):
    def test_successful_run_and_evidence_consumption(self):
        hero = self.fx.entity("阿雾")
        provider = MockResearchProvider({"阿雾": [MATERIAL_A, MATERIAL_B]})
        registrar = RecordingRegistrar(self.fx.evidence)
        svc = self.fx.service(provider, registrar=registrar)
        output = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))

        self.assertEqual(output.planned_count, 1)
        self.assertEqual(output.executed_count, 1)
        self.assertEqual(output.no_result_count, 0)
        self.assertEqual(len(output.source_ids), 2)
        self.assertEqual(output.budget_used.requests, 1)
        self.assertIn("register_source", registrar.calls)

        # Research 的输出**能被既有 Evidence 接口消费**：可查询、可追溯
        page = unwrap(self.fx.evidence.query_sources(self.fx.ctx, Query()))
        self.assertEqual(page.total, 2)
        source = unwrap(self.fx.evidence.get_source(self.fx.ctx, output.source_ids[0]))
        self.assertEqual(source.subject_entity_ids, [hero])
        self.assertTrue(source.url)

    def test_no_result_run_is_ok_with_counters(self):
        hero = self.fx.entity("阿雾")
        svc = self.fx.service(MockResearchProvider({}))
        output = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual(output.no_result_count, 1)
        self.assertEqual(output.source_ids, [])
        self.assertIn(WARNING_NO_RESULT, [w.code for w in output.warnings])
        self.assertEqual(self.fx.count("Source"), 0)

    def test_partial_failure_continues_and_warns(self):
        hero = self.fx.entity("阿雾")
        provider = MockResearchProvider(
            {"阿雾 结局": [MATERIAL_B]}, error_for={"阿雾": ErrorCode.EXTERNAL_UNAVAILABLE})
        svc = self.fx.service(provider)
        output = unwrap(svc.run(
            self.fx.ctx, [self.fx.target(hero, "阿雾", topic="结局")]))

        self.assertEqual(output.planned_count, 2)
        self.assertEqual(output.executed_count, 1)          # 失败的没算执行成功
        self.assertEqual(output.no_result_count, 0)
        self.assertIn(WARNING_QUERY_FAILED, [w.code for w in output.warnings])
        self.assertEqual(len(output.source_ids), 1)          # 成功那条仍登记

    def test_all_queries_failed_is_reported_as_error(self):
        hero = self.fx.entity("阿雾")
        provider = MockResearchProvider(error_for={"阿雾": ErrorCode.EXTERNAL_TIMEOUT})
        svc = self.fx.service(provider)
        result = svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_TIMEOUT)
        self.assertEqual(self.fx.count("Source"), 0)

    def test_budget_exceeded_aborts_immediately(self):
        hero = self.fx.entity("阿雾")
        provider = MockResearchProvider(
            {"阿雾 结局": [MATERIAL_B]}, error_for={"阿雾": ErrorCode.BUDGET_EXCEEDED})
        svc = self.fx.service(provider)
        result = svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾", topic="结局")])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.BUDGET_EXCEEDED)
        # 预算信号不是"跳过继续"：第二条话题查询根本没被发起
        self.assertNotIn("阿雾 结局", provider.calls)

    def test_invalid_materials_alone_never_create_sources(self):
        hero = self.fx.entity("阿雾")
        provider = MockResearchProvider({"阿雾": [SearchResult(title="", url="", canonical_key="")]})
        svc = self.fx.service(provider)
        output = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual(output.source_ids, [])
        self.assertIn(WARNING_CANDIDATE_REJECTED, [w.code for w in output.warnings])
        self.assertEqual(self.fx.count("Source"), 0)

    def test_empty_targets_short_circuits(self):
        svc = self.fx.service(MockResearchProvider())
        result = svc.run(self.fx.ctx, [])
        self.assertTrue(is_err(result))

    def test_budget_argument_is_not_consulted(self):
        """预算账本归 Orchestrator：传入任意对象都不影响 Research 运行（用户目标 7）。"""
        hero = self.fx.entity("阿雾")
        svc = self.fx.service(MockResearchProvider({"阿雾": [MATERIAL_A]}))
        output = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")], budget=object()))
        self.assertEqual(len(output.source_ids), 1)

    def test_repeated_run_is_idempotent(self):
        hero = self.fx.entity("阿雾")
        svc = self.fx.service(MockResearchProvider({"阿雾": [MATERIAL_A, MATERIAL_B]}))
        first = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        second = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual(first.source_ids, second.source_ids)
        self.assertEqual(self.fx.count("Source"), 2)     # 不重复建记录

    def test_multi_target_run(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        provider = MockResearchProvider({"阿雾": [MATERIAL_A], "阿灯": [MATERIAL_B]})
        svc = self.fx.service(provider)
        output = unwrap(svc.run(self.fx.ctx, [
            self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")]))
        self.assertEqual(output.planned_count, 2)
        self.assertEqual(output.executed_count, 2)
        self.assertEqual(len(output.source_ids), 2)
        # 每个来源都带自己的对象边界，不串味
        for source_id in output.source_ids:
            row = unwrap(self.fx.storage.get(self.fx.ctx, "Source", source_id))
            self.assertEqual(len(row["subject_entity_ids"]), 1)


# --------------------------------------------------------------------------
# 6. 隔离与模拟内容
# --------------------------------------------------------------------------


class TestIsolation(FixtureTestCase):
    def test_run_never_touches_protected_tables(self):
        hero = self.fx.entity("阿雾")
        svc = self.fx.service(MockResearchProvider({"阿雾": [MATERIAL_A, MATERIAL_B]}))
        before = {k: self.fx.count(k) for k in
                  ("EvidenceItem", "SourceEvent", "PersonaProfile", "RelationshipProfile",
                   "ConversationSession", "ConversationTurn", "MemoryItem")}
        unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        after = {k: self.fx.count(k) for k in before}
        self.assertEqual(before, after)
        self.assertEqual(self.fx.count("Source"), 2)

    def test_created_sources_are_public_namespace_only(self):
        hero = self.fx.entity("阿雾")
        svc = self.fx.service(MockResearchProvider({"阿雾": [MATERIAL_A]}))
        unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        for row in self.fx.rows("Source"):
            self.assertEqual(row["namespace"], str(ProvenanceNamespace.PUBLIC_SOURCE))

    def test_storage_gate_still_blocks_simulated_writes(self):
        blocked = self.fx.storage.put(
            self.fx.ctx, "Source", {"title": "模拟内容不应进来源表"},
            project_id=PROJECT, namespace=ProvenanceNamespace.SIMULATED)
        self.assertTrue(is_err(blocked))
        self.assertEqual(blocked.error.code, ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN)

    def test_offline_guard_and_network_block(self):
        hero = self.fx.entity("阿雾")
        inner = MockResearchProvider({"阿雾": [MATERIAL_A]})
        svc = self.fx.service(OfflineGuardProvider(inner))
        with network_blocked():
            output = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual(len(output.source_ids), 1)


# --------------------------------------------------------------------------
# 7. 模块边界
# --------------------------------------------------------------------------


class TestModuleBoundary(FixtureTestCase):
    def test_research_gets_only_register_port(self):
        import inspect
        public = {n for n, _ in inspect.getmembers(SourceRegistrarPort, inspect.isfunction)
                  if not n.startswith("_")}
        self.assertEqual(public, {"register_source"})

    def test_service_has_no_evidence_write_methods(self):
        for name in ("add_evidence", "record_inference", "detect_conflicts", "detect_duplicates",
                     "add_source_event", "extract_events"):
            self.assertFalse(hasattr(ResearchService, name), name)

    def test_module_does_not_import_storage_or_sqlite(self):
        text = (Path(__file__).resolve().parents[1] / "persona" / "modules" / "research.py") \
            .read_text(encoding="utf-8")
        self.assertNotIn("import sqlite3", text)
        self.assertNotIn("SqliteStorage", text)
        self.assertNotIn("def add_evidence", text)
        self.assertNotIn(".add_evidence(", text)
        self.assertNotIn("from ..orchestrator", text)

    def test_only_register_source_is_called_on_evidence_port(self):
        hero = self.fx.entity("阿雾")
        registrar = RecordingRegistrar(self.fx.evidence)
        svc = self.fx.service(MockResearchProvider({"阿雾": [MATERIAL_A]}), registrar=registrar)
        unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual(set(registrar.calls), {"register_source"})

    def test_module_passes_static_checks(self):
        """本模块不得引入 `score`/`closeness`/`intimacy` 字段，也不得造成依赖违规（AC-20）。"""
        from persona.modules.evaluation import EvaluationService

        report = unwrap(EvaluationService(self.fx.storage).run_static_checks(self.fx.ctx))
        offenders = [f for f in report["forbidden_fields"] if f["module"] == "research.py"]
        self.assertEqual(offenders, [])
        self.assertEqual(report["dependency_violations"], [])
        self.assertEqual([v for v in report["signature_violations"]
                          if v["module"] == "research.py"], [])
        self.assertTrue(report["passed"])


# --------------------------------------------------------------------------
# 8. 复用既有 Mock Provider 家族
# --------------------------------------------------------------------------


class TestReuseExistingMocks(FixtureTestCase):
    def test_fake_provider_via_search_metadata(self):
        """既有 `FakeProvider` 要求显式 `fixture_key`；Research 用 `search_metadata` 满足它。"""
        hero = self.fx.entity("阿雾")
        provider = FakeProvider({"search:identity": [MATERIAL_A]}, config=ProviderConfig())
        svc = self.fx.service(provider, search_metadata={"fixture_key": "identity"})
        output = unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual(len(output.source_ids), 1)
        self.assertEqual(self.fx.count("Source"), 1)

    def test_fake_provider_missing_fixture_key_is_honest_error(self):
        hero = self.fx.entity("阿雾")
        provider = FakeProvider({}, config=ProviderConfig())
        svc = self.fx.service(provider)                      # 不给 fixture_key
        result = svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_recording_provider_logs_search_calls(self):
        hero = self.fx.entity("阿雾")
        recorder = RecordingProvider(FakeProvider({"search:identity": [MATERIAL_B]}))
        svc = self.fx.service(recorder, search_metadata={"fixture_key": "identity"})
        unwrap(svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")]))
        self.assertEqual([e.op for e in recorder.call_log], ["search"])
        self.assertTrue(recorder.call_log[0].ok)

    def test_failing_provider_is_reported(self):
        from persona.providers.mock import FailingProvider

        hero = self.fx.entity("阿雾")
        svc = self.fx.service(FailingProvider(default=ErrorCode.EXTERNAL_RATE_LIMIT))
        result = svc.run(self.fx.ctx, [self.fx.target(hero, "阿雾")])
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.EXTERNAL_RATE_LIMIT)


if __name__ == "__main__":
    unittest.main()
