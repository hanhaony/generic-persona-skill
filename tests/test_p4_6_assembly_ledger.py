"""P4.6：装配层（P0-1）、账本落库与两层闸门（P0-2 / G11）、分析器装配（P2）。

本文件只跑**离线**：临时 SQLite + 桩 Provider，不联网、不调真实模型、不花钱。

覆盖三块：

1. **G11 / 账本**
   - `hydrate()` 的保守计入口径（实际 > 估算(取 max) > 未知占位 > 0）
   - hydrate 幂等、跳过重复键、历史笔不混进本次计数（`max_calls` 不被历史污染）
   - 跨进程幂等：换个进程，同一 `idempotency_key` 仍判"已调用过"
   - 两层闸门：单任务上限（按 `task_id`）与项目累计上限（按整本账）各自生效
2. **装配层**
   - 装配出的服务就是编排器用的那一份（不出现两份 composition root）
   - `usage_sink` 真的接到存储上（C 轮"花了钱但没落库"的缺口）
   - hydrate 失败时：配了累计上限 → 拒绝装配；没配 → 降级 + 告警
   - 分析器默认装配（D-D），可关闭；抽取器缺失有明确告警
3. **CLI**
   - `persona business plan` 离线可跑、退出码 0、打印两层上限与账本
   - `persona business run` 在无抽取器时**拒绝**且零调用
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from persona.cli.main import main as cli_main
from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.types import (
    COST_CONFIRMED,
    COST_NOT_CHARGED,
    COST_UNKNOWN,
    CURRENCY_UNKNOWN,
    OP_CHAT,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    SOURCE_OFFICIAL_RATE,
    ExternalUsageRecord,
    SourceMaterial,
)
from persona.core.usage_ledger import ExternalUsageLedger
from persona.modules.extract_quality import QUALITY_VALID
from persona.modules.extraction import WARNING_MODEL_CALL_DUPLICATE_SKIPPED
from persona.modules.relationship_analysis import RuleRelationshipAnalyzer
from persona.modules.research import PersonQueryTarget
from persona.orchestrator import StackOptions, build_business_stack
from persona.orchestrator.assembly import (
    WARNING_STACK_ANALYZER_DISABLED,
    WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE,
    WARNING_STACK_EXTRACTOR_ABSENT,
    WARNING_STACK_LEDGER_HYDRATION_FAILED,
    WARNING_STACK_SEARCH_OFFLINE,
)
from persona.providers.base import ChatResponse
from persona.providers.mock import FakeProvider
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_p46"

#: 两份**不同**材料：用来让两次抽取产生**不同**的幂等键
#: （同一个键会先被"重复调用"拦下，就测不到预算闸门了）。
MATERIAL_A = SourceMaterial(
    source_id="src_p46_a", title="[合成] 雾港值守记录 A",
    url="https://example.invalid/p46/a", canonical_key="p46:a",
    snippet="阿雾每天黄昏点亮灯塔，然后沿堤坝巡视一圈。",
    source_kind="WEB_PAGE", subject_entity_ids=["pe_p46"],
    quality_status=QUALITY_VALID)
MATERIAL_B = SourceMaterial(
    source_id="src_p46_b", title="[合成] 雾港值守记录 B",
    url="https://example.invalid/p46/b", canonical_key="p46:b",
    snippet="阿灯在散场后把最后一盏灯点亮，再上塔记录当夜情形。",
    source_kind="WEB_PAGE", subject_entity_ids=["pe_p46"],
    quality_status=QUALITY_VALID)

TARGETS = [PersonQueryTarget(entity_id="pe_p46", display_name="阿雾")]


# --------------------------------------------------------------------------
# 桩 Provider：只实现 chat，返回合法但空的 claim 列表
# --------------------------------------------------------------------------


class StubChatProvider(FakeProvider):
    """返回 `{"claims": []}` 的桩聊天 Provider，并**数调用次数**。

    用空 claims 是刻意的：本文件要验证的是"钱与闸门"，不是抽取质量。
    空 claims 依然是**成功的一次调用**，会按真实 token 结算费用。
    """

    def __init__(self, *, tokens_in: int = 1358, tokens_out: int = 2397) -> None:
        super().__init__({})
        self.text = '{"claims": []}'
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.calls = 0

    def chat(self, ctx, req):  # noqa: D102 - 桩
        self.calls += 1
        usage = UsageDelta(requests=1, tokens_in=self.tokens_in,
                           tokens_out=self.tokens_out, latency_ms=1)
        return Ok(ChatResponse(text=self.text, structured=None,
                               finish_reason="stop", model_id="stub-model",
                               usage=usage, raw_ref="stub://chat"), usage=usage)


class _BrokenHistoryStorage(SqliteStorage):
    """读历史必失败的真存储（用来验"读不到历史时"的两种策略）。"""

    def external_usage_records(self, ctx, **kwargs):  # noqa: D102 - 测试替身
        return Err(new_error(ErrorCode.STORAGE_READ_FAILED,
                             "模拟：读取 external_usage_records 失败"))


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------


def _paid_settings(**overrides):
    """带单价与上限的测试配置（两层上限：单任务 0.25 / 累计 1.00）。"""
    base = {
        "model.price_in_per_1m": 0.27,
        "model.price_out_per_1m": 1.10,
        "model.price_source": "官方价目表（测试用）",
        "model.price_checked_at": "2026-10-15",
        "model.max_cost": 1.00,
        "model.max_cost_per_task": 0.25,
        "model.max_calls": 5,
        "model.est_tokens_in": 3000,
        "model.est_tokens_out": 2000,
    }
    base.update(overrides)
    return test_settings(**base)


def _record(key: str, *, confirmed=None, estimate=None, status=COST_CONFIRMED,
            task_id="task_a", currency="USD", outcome=OUTCOME_SUCCEEDED):
    return ExternalUsageRecord(
        idempotency_key=key, provider="deepseek", operation=OP_CHAT,
        outcome=outcome, requested_at="2026-10-17T00:00:00+00:00",
        unit_count=1, success_unit_count=1,
        cost_estimate=estimate, cost_confirmed=confirmed,
        currency=currency, cost_source=SOURCE_OFFICIAL_RATE,
        cost_status=status, task_id=task_id)


class _TempDb(unittest.TestCase):
    """每个用例一个临时目录（**不碰 var/**，不留本地库）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p46-")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "p46.db"

    def _ctx(self, task_id: str = "task_a"):
        return new_call_context(project_id=PROJECT, actor="p46-test",
                                task_id=task_id)

    def _spend(self, settings, ledger, provider, *, task_id="task_a",
               material=MATERIAL_A, hydrate=False):
        """装配 → 抽一次（真的调桩 Provider）→ 返回 (stack, outcome)。"""
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        ctx = self._ctx(task_id)
        built = build_business_stack(
            settings, ctx, storage=storage, ledger=ledger,
            chat_provider=provider,
            options=StackOptions(task_id=task_id, hydrate_ledger=hydrate))
        self.assertTrue(not is_err(built), f"装配失败：{getattr(built, 'error', '')}")
        stack = unwrap(built)
        outcome = stack.claim_extractor.extract(ctx, [material], targets=TARGETS)
        return stack, outcome


# ==========================================================================
# 1. G11：hydrate 的计入口径
# ==========================================================================


class TestLedgerHydration(unittest.TestCase):
    def test_confirmed_records_are_loaded_as_actual_cost(self):
        ledger = ExternalUsageLedger()
        summary = ledger.hydrate([_record("k1", confirmed=0.0033),
                                  _record("k2", confirmed=0.0010)])
        self.assertEqual(summary.loaded, 2)
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0043, places=6)
        self.assertEqual(summary.unpriced_unknown, 0)

    def test_unknown_with_estimate_is_counted_by_the_larger_value(self):
        """D-C：未知笔数按**最大估算**计入——估算填低了会让累计闸门少算。"""
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k1", estimate=0.0005, status=COST_UNKNOWN)],
                       unknown_fallback_cost=0.004)
        self.assertAlmostEqual(ledger.occupied_cost(), 0.004, places=6)

    def test_unknown_without_estimate_uses_fallback(self):
        ledger = ExternalUsageLedger()
        summary = ledger.hydrate([_record("k1", status=COST_UNKNOWN)],
                                 unknown_fallback_cost=0.0031)
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0031, places=6)
        self.assertEqual(summary.unpriced_unknown, 0)
        self.assertEqual(summary.unknown_requests, 1)

    def test_unknown_without_any_estimate_is_flagged_not_zeroed(self):
        """D-060：既无估算又无兜底 → 金额只能记 0，但必须**单独计数**。"""
        ledger = ExternalUsageLedger()
        summary = ledger.hydrate([_record("k1", status=COST_UNKNOWN)])
        self.assertEqual(summary.unpriced_unknown, 1)
        self.assertEqual(ledger.unpriced_unknown_requests(), 1)
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0, places=6)

    def test_not_charged_records_cost_nothing(self):
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k1", status=COST_NOT_CHARGED)])
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0, places=6)

    def test_hydration_is_idempotent(self):
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k1", confirmed=0.0033)])
        again = ledger.hydrate([_record("k1", confirmed=0.0033)])
        self.assertTrue(again.already_hydrated)
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0033, places=6)

    def test_hydration_skips_keys_already_settled_this_session(self):
        ledger = ExternalUsageLedger()
        ledger.settle(_record("k1", confirmed=0.0033))
        summary = ledger.hydrate([_record("k1", confirmed=0.0033)])
        self.assertEqual(summary.loaded, 0)
        self.assertEqual(summary.skipped, 1)
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0033, places=6)

    def test_history_does_not_pollute_session_counts(self):
        """历史笔不进 `records()`：否则昨天花过的 1 次会让今天一启动就"次数用尽"。"""
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k1", confirmed=0.0033)])
        self.assertEqual(ledger.totals().requests, 0)
        self.assertEqual(len(ledger.records()), 0)
        self.assertEqual(len(ledger.historical_records()), 1)

    def test_cross_process_idempotency_sees_history(self):
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k1", confirmed=0.0033)])
        self.assertTrue(ledger.has_attempted("k1"))
        self.assertFalse(ledger.has_attempted("k2"))

    def test_occupied_cost_for_task_filters_by_task(self):
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k1", confirmed=0.0030, task_id="task_a"),
                        _record("k2", confirmed=0.0020, task_id="task_b")])
        self.assertAlmostEqual(ledger.occupied_cost(), 0.0050, places=6)
        self.assertAlmostEqual(
            ledger.occupied_cost_for_task("task_a"), 0.0030, places=6)
        self.assertAlmostEqual(
            ledger.occupied_cost_for_task("task_c"), 0.0, places=6)

    def test_historical_unknown_does_not_lock_the_gate_forever(self):
        """历史未知已按保守金额计入累计，但不该让"未知笔数"规则永久关闭闸门。

        否则一条历史未知记录 = 这本账再也用不了（`confirm()` 只作用于内存）。
        """
        ledger = ExternalUsageLedger(max_unknown_requests=1)
        ledger.hydrate([_record("k1", status=COST_UNKNOWN)],
                       unknown_fallback_cost=0.0031)
        paused, _why = ledger.should_pause()
        self.assertFalse(paused, "历史未知不应触发'本次未知笔数'停机规则")

    def test_session_unknown_still_pauses(self):
        ledger = ExternalUsageLedger(max_unknown_requests=1)
        ledger.settle(_record("k1", status=COST_UNKNOWN))
        paused, why = ledger.should_pause()
        self.assertTrue(paused)
        self.assertIn("未知", why)


# ==========================================================================
# 2. G11：两层闸门（端到端，跨进程）
# ==========================================================================


class TestTwoLayerBudgetGate(_TempDb):
    def test_first_process_spends_and_persists(self):
        settings = _paid_settings()
        ledger = ExternalUsageLedger()
        provider = StubChatProvider()
        stack, outcome = self._spend(settings, ledger, provider)
        self.assertFalse(is_err(outcome))
        self.assertEqual(provider.calls, 1)
        report = unwrap(stack.storage.external_usage_report(self._ctx()))
        self.assertEqual(report.requests, 1)
        self.assertIsNotNone(report.confirmed_cost)
        self.assertGreater(report.confirmed_cost, 0.0)

    def test_second_process_sees_history_and_blocks_on_project_cap(self):
        """跨进程累计：进程 A 花了钱 → 进程 B 启动后累计可见 → 发送前被拦。"""
        settings = _paid_settings()
        _stack_a, out_a = self._spend(settings, ExternalUsageLedger(),
                                      StubChatProvider(), material=MATERIAL_A)
        self.assertFalse(is_err(out_a))

        # 进程 B：换一本新账（模拟重启），上限收紧到刚够不了一次调用。
        # 注意配置校验要求 `单任务 ≤ 总上限`，所以两层都设成 0.004：
        # 任务 B 自己一分没花（单任务层过），但整本账已被任务 A 花了 0.003，
        # 再加一次 0.00301 的估算 ⇒ 累计层必须拦（这就是"两层"的意义）。
        tight = _paid_settings(**{"model.max_cost": 0.004,
                                  "model.max_cost_per_task": 0.004})
        provider_b = StubChatProvider()
        stack_b, outcome_b = self._spend(
            tight, ExternalUsageLedger(), provider_b,
            task_id="task_b", material=MATERIAL_B, hydrate=True)

        self.assertEqual(stack_b.hydration.loaded, 1, "进程 B 应读到 1 笔历史")
        self.assertGreater(stack_b.ledger.occupied_cost(), 0.0)
        self.assertEqual(provider_b.calls, 0, "闸门必须在发送前拦住")
        self.assertTrue(is_err(outcome_b))
        self.assertEqual(outcome_b.error.code, ErrorCode.BUDGET_EXCEEDED)
        self.assertIn("项目累计费用上限", outcome_b.error.message)

    def test_task_cap_is_scoped_to_task_id(self):
        """单任务上限按 `task_id` 计：别的任务花掉的钱不该算进这个任务。"""
        settings = _paid_settings(**{"model.max_cost": 10.0,
                                     "model.max_cost_per_task": 0.004})
        _stack_a, out_a = self._spend(settings, ExternalUsageLedger(),
                                      StubChatProvider(), material=MATERIAL_A)
        self.assertFalse(is_err(out_a))
        provider_b = StubChatProvider()
        stack_b, out_b = self._spend(settings, ExternalUsageLedger(), provider_b,
                                     task_id="task_b", material=MATERIAL_B)
        # 任务 B 自己没花过钱 → 单任务上限不拦它（累计上限 10.0 也没到）
        self.assertEqual(provider_b.calls, 1, "新任务的单任务上限不应被别的任务占用")
        self.assertFalse(is_err(out_b))
        self.assertGreater(stack_b.ledger.occupied_cost_for_task("task_b"), 0.0)

    def test_unpriced_history_blocks_before_sending(self):
        """历史里有"未知且无任何估算"的笔数 + 配了累计上限 → 金额无法界定，停。"""
        ledger = ExternalUsageLedger()
        ledger.hydrate([_record("k_unknown", status=COST_UNKNOWN)])
        self.assertEqual(ledger.unpriced_unknown_requests(), 1)

        settings = _paid_settings()
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        ctx = self._ctx("task_u")
        built = build_business_stack(
            settings, ctx, storage=storage, ledger=ledger,
            chat_provider=StubChatProvider(),
            options=StackOptions(task_id="task_u", hydrate_ledger=False))
        stack = unwrap(built)
        provider = stack.claim_extractor.provider
        outcome = stack.claim_extractor.extract(ctx, [MATERIAL_A], targets=TARGETS)
        self.assertEqual(provider.calls, 0)
        self.assertTrue(is_err(outcome))
        self.assertEqual(outcome.error.code, ErrorCode.BUDGET_EXCEEDED)
        self.assertIn("无法界定", outcome.error.message)

    def test_duplicate_key_is_skipped_before_any_budget_check(self):
        """同一份材料再抽一次 → 幂等跳过，**不花钱**（跨进程也一样）。"""
        settings = _paid_settings()
        _stack_a, out_a = self._spend(settings, ExternalUsageLedger(),
                                      StubChatProvider())
        self.assertFalse(is_err(out_a))
        provider_b = StubChatProvider()
        _stack_b, out_b = self._spend(settings, ExternalUsageLedger(), provider_b,
                                      task_id="task_b", material=MATERIAL_A)
        self.assertEqual(provider_b.calls, 0)
        self.assertFalse(is_err(out_b))
        codes = {w.code for w in out_b.value.warnings}
        self.assertIn(WARNING_MODEL_CALL_DUPLICATE_SKIPPED, codes)


# ==========================================================================
# 3. 装配层
# ==========================================================================


class TestBusinessStackAssembly(_TempDb):
    def _build(self, settings=None, *, storage=None, **kwargs):
        settings = settings or test_settings()
        owned = storage is None
        if owned:
            storage = SqliteStorage(self.path)
            self.addCleanup(storage.close)
        built = build_business_stack(settings, self._ctx(), storage=storage, **kwargs)
        self.assertFalse(is_err(built), f"装配失败：{getattr(built, 'error', '')}")
        return unwrap(built)

    def test_orchestrator_uses_the_same_service_instances(self):
        """不允许出现"两份 composition root"：编排器依赖就是装配层那份。"""
        stack = self._build()
        deps = stack.orchestrator.deps
        self.assertIs(deps.evidence, stack.evidence)
        self.assertIs(deps.persona, stack.persona)
        self.assertIs(deps.relationship, stack.relationship)
        self.assertIs(deps.dialogue, stack.dialogue)
        self.assertIs(deps.research, stack.research)
        self.assertIs(deps.storage, stack.storage)

    def test_usage_sink_is_wired_to_storage(self):
        """C 轮缺口：开销必须**落库**，否则换个进程就不知道花过什么。"""
        stack = self._build(chat_provider=StubChatProvider())
        self.assertIs(stack.research.deps.usage_sink, stack.storage)
        self.assertIsNotNone(stack.claim_extractor)
        self.assertIs(stack.claim_extractor.usage_sink, stack.storage)
        self.assertIs(stack.claim_extractor.ledger, stack.ledger)

    def test_default_assembles_rule_analyzer(self):
        stack = self._build()
        self.assertIsInstance(stack.relationship_analyzer, RuleRelationshipAnalyzer)
        self.assertNotIn(WARNING_STACK_ANALYZER_DISABLED,
                         {w.code for w in stack.warnings})

    def test_config_can_disable_analyzer(self):
        settings = test_settings(**{"relationship.analyzer": "off"})
        stack = self._build(settings)
        self.assertIsNone(stack.relationship_analyzer)
        self.assertIn(WARNING_STACK_ANALYZER_DISABLED,
                      {w.code for w in stack.warnings})

    def test_explicit_option_overrides_config(self):
        stack = self._build(options=StackOptions(enable_relationship_analyzer=False))
        self.assertIsNone(stack.relationship_analyzer)

    def test_missing_extractor_and_search_provider_are_reported(self):
        stack = self._build()
        codes = {w.code for w in stack.warnings}
        self.assertIn(WARNING_STACK_EXTRACTOR_ABSENT, codes)
        self.assertIn(WARNING_STACK_SEARCH_OFFLINE, codes)

    def test_asking_for_a_real_chat_provider_without_config_degrades(self):
        """显式要求构造真实 Provider 但配置不齐 → 记告警、不留抽取器（不伪造）。"""
        stack = self._build(options=StackOptions(build_chat_provider=True))
        codes = {w.code for w in stack.warnings}
        self.assertIn(WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE, codes)
        self.assertIsNone(stack.claim_extractor)

    def test_describe_exposes_both_caps(self):
        stack = self._build(_paid_settings())
        facts = stack.describe()
        self.assertEqual(facts["model"]["max_cost_per_task"], 0.25)
        self.assertEqual(facts["model"]["max_cost_total"], 1.00)
        self.assertEqual(facts["components"]["relationship_analyzer"],
                         "RuleRelationshipAnalyzer")
        self.assertIn("ledger", facts)

    def test_hydration_failure_is_fatal_when_a_project_cap_is_configured(self):
        settings = _paid_settings()
        storage = _BrokenHistoryStorage(self.path)
        self.addCleanup(storage.close)
        built = build_business_stack(settings, self._ctx(), storage=storage)
        self.assertTrue(is_err(built))
        self.assertEqual(built.error.code, ErrorCode.BUDGET_EXCEEDED)
        self.assertIn("无法从存储读取历史外部用量", built.error.message)

    def test_hydration_failure_degrades_when_no_cap_is_configured(self):
        storage = _BrokenHistoryStorage(self.path)
        self.addCleanup(storage.close)
        stack = self._build(test_settings(), storage=storage)
        self.assertIn(WARNING_STACK_LEDGER_HYDRATION_FAILED,
                      {w.code for w in stack.warnings})
        self.assertIsNone(stack.hydration)

    def test_hydrated_stack_counts_history_in_total(self):
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        ctx = self._ctx()
        self.assertFalse(is_err(storage.init_schema(ctx)))
        for key, confirmed in (("k1", 0.0030), ("k2", 0.0010)):
            self.assertFalse(is_err(
                storage.record_external_usage(ctx, _record(key, confirmed=confirmed))))
        stack = self._build(storage=storage)
        self.assertEqual(stack.hydration.loaded, 2)
        self.assertAlmostEqual(stack.ledger.occupied_cost(), 0.0040, places=6)

    def test_stack_close_only_closes_own_storage(self):
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        stack = self._build(storage=storage)
        self.assertFalse(stack.owns_storage)
        stack.close()          # 不应关闭外部传入的库
        self.assertFalse(is_err(storage.external_usage_records(self._ctx())))


# ==========================================================================
# 4. CLI
# ==========================================================================


class TestBusinessCli(_TempDb):
    def _env(self):
        return {"PERSONA_DATA_DIR": self._tmp.name}

    def test_plan_runs_offline_and_prints_snapshot(self):
        buffer = io.StringIO()
        with patch.dict("os.environ", self._env(), clear=False):
            with redirect_stdout(buffer):
                code = cli_main(["business", "plan", "--no-dotenv"])
        text = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("业务链装配快照", text)
        self.assertIn("项目累计上限", text)
        self.assertIn("账本（历史）", text)
        self.assertIn("不产生费用", text)

    def test_plan_with_analyzer_off_reflects_in_snapshot(self):
        buffer = io.StringIO()
        with patch.dict("os.environ", self._env(), clear=False):
            with redirect_stdout(buffer):
                code = cli_main(["business", "plan", "--no-dotenv",
                                 "--analyzer", "off"])
        self.assertEqual(code, 0)
        self.assertIn("关系分析器      : 未装配", buffer.getvalue())

    def test_run_refuses_without_extractor_and_spends_nothing(self):
        buffer = io.StringIO()
        with patch.dict("os.environ", self._env(), clear=False):
            with redirect_stdout(buffer):
                code = cli_main(["business", "run", "--no-dotenv",
                                 "--names", "阿雾,阿灯",
                                 "--work-title", "《雾港与星灯》",
                                 "--adaptation-version", "2020 动画改编版"])
        text = buffer.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("拒绝运行", text)
        self.assertIn("没有发起任何请求", text)

    def test_run_requires_work_metadata_for_fictional_characters(self):
        buffer = io.StringIO()
        with patch.dict("os.environ", self._env(), clear=False):
            with redirect_stdout(buffer):
                code = cli_main(["business", "run", "--no-dotenv",
                                 "--names", "阿雾,阿灯"])
        self.assertEqual(code, 1)
        self.assertIn("必须声明所属作品与适用版本", buffer.getvalue())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
