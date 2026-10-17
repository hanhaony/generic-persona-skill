"""P4.8 整改：D-3（对话调用必须进账本）+ D-2（相关性过滤）+ D-4（清单口径）。

三条缺陷都来自 **R4 第一次真实检索**的实测（`docs/P4_8_R4_REAL_SEARCH.md`）：

- **D-3 记账红线**：对话阶段的模型调用没有记账出口，token 只落在
  `ConversationSession.budget_used`，`external_usage_records` 里一行都没有；
- **D-2 登记 ≠ 可用**：`filter_and_rank` 只查字段齐不齐，5 条"汽车防雾灯"
  页面被全部登记成来源；
- **D-4 清单偏大**：对话次数按"目标数 × 轮数"报，实际每轮只发 1 次。

全部离线：临时 SQLite + 本地替身，不触网、不调真实模型、不产生费用。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.errors import ErrorCode
from persona.core.result import Err, Ok, UsageDelta, is_err, unwrap
from persona.core.types import (
    COST_CONFIRMED,
    COST_UNKNOWN,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
)
from persona.core.usage_ledger import ExternalUsageLedger, ModelCallLimits
from persona.modules.dialogue import DialogueDeps, DialogueService, SessionDraft
from persona.modules.research import (
    REASON_IRRELEVANT,
    WARNING_IRRELEVANT_REJECTED,
    WARNING_RELEVANCE_NOT_CHECKED,
)
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
    SearchResult,
    UsageReport,
)
from persona.providers.mock import FakeProvider
from persona.storage.sqlite import SqliteStorage

from tests.test_dialogue_module import DialogueFixture
from tests.test_research_module import ResearchFixture

PROJECT = "prj_p48_fix"
TASK = "tsk_p48_fix"

#: 与 R4 真实运行同形：配置了单价（每百万 token），所以金额可复算。
LIMITS = ModelCallLimits(
    max_calls=None, max_cost=None, max_cost_total=None,
    price_per_1k_in=0.0003, price_per_1k_out=0.0012,
    currency="USD", est_tokens_in=1000, est_tokens_out=500)


class BillableChat:
    """一个**没声明 offline** 的模型出口：按契约必须被记账。

    真实出口（DeepSeek 等）就是这个形状：有 `provider_name`、回报 token、
    回报服务端 request_id，但**不报金额**（金额由装配层按配置单价算）。
    """

    provider_name = "billable-stub"

    def __init__(self, *, text: str = "（模拟台词）灯还亮着，我先去巡一遍。",
                 usage: UsageDelta | None = None,
                 fail: bool = False,
                 request_id: str = "req-dlg-0001") -> None:
        self.text = text
        self.fail = fail
        self.request_id = request_id
        self.calls = 0
        self._usage = usage if usage is not None else UsageDelta(
            requests=1, tokens_in=1000, tokens_out=200, request_id=request_id)

    def chat(self, ctx, req):
        self.calls += 1
        if self.fail:
            return Err(__import__("persona.core.errors", fromlist=["new_error"]).new_error(
                ErrorCode.EXTERNAL_TIMEOUT, "模拟超时"))
        return Ok(ChatResponse(text=self.text, finish_reason="stop",
                               model_id="billable-stub", usage=self._usage),
                  usage=self._usage)

    def search(self, ctx, req):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=self.calls))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="billable-stub", supports_chat=True))


# ==========================================================================
# D-3：对话的模型调用必须进账本（内存账 + 持久账）
# ==========================================================================


class DialogueAccountingTest(unittest.TestCase):
    """D-3：一次对话调用 = 一行 `external_usage_records`。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-p48-d3-")
        self.fx = DialogueFixture(self.tmp)
        self.ledger = ExternalUsageLedger()

    def tearDown(self) -> None:
        self.fx.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ 辅助

    def _service(self, provider, *, limits=LIMITS, ledger=None) -> DialogueService:
        return DialogueService(DialogueDeps(
            profiles=self.fx.persona, relations=self.fx.relationship,
            evidence=self.fx.evidence, memory=None, storage=self.fx.storage,
            provider=provider, ledger=ledger or self.ledger,
            limits=limits, usage_sink=self.fx.storage, task_id=TASK,
            model_hint="stub-model"))

    def _session(self) -> str:
        a, b = self.fx.two_actors()
        view = unwrap(self._service(BillableChat()).start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b], topic="夜谈")))
        return view.id

    def _stored_records(self):
        return unwrap(self.fx.storage.external_usage_records(self.fx.ctx))

    # ------------------------------------------------------------ 用例

    def test_billable_dialogue_call_is_recorded_in_ledger_and_storage(self):
        """**核心断言**：账本与持久层各有一笔，字段可复算。"""
        chat = BillableChat()
        service = self._service(chat)
        a, b = self.fx.two_actors()
        session = unwrap(service.start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b]))).id
        unwrap(service.run(self.fx.ctx, session, 1))

        self.assertEqual(chat.calls, 1)
        records = self.ledger.records()
        self.assertEqual(len(records), 1, "对话调用必须进内存账本")
        rec = records[0]
        self.assertEqual(rec.operation, "CHAT")
        self.assertEqual(rec.outcome, OUTCOME_SUCCEEDED)
        self.assertEqual(rec.provider, "billable-stub")
        self.assertEqual(rec.task_id, TASK)
        self.assertEqual(rec.request_id, "req-dlg-0001")
        # 1000/1000*0.0003 + 200/1000*0.0012 = 0.0003 + 0.00024
        self.assertAlmostEqual(rec.cost_confirmed, 0.00054, places=9)
        self.assertEqual(rec.cost_status, COST_CONFIRMED)
        self.assertEqual(rec.currency, "USD")

        stored = self._stored_records()
        self.assertEqual(len(stored), 1, "对话调用必须落库（跨进程可对账）")
        self.assertEqual(stored[0].idempotency_key, rec.idempotency_key)

    def test_failed_dialogue_call_is_recorded_as_unknown_not_zero(self):
        """失败 ≠ 免费：记 `FAILED` + `UNKNOWN`，且不留 0（D-060）。"""
        chat = BillableChat(fail=True)
        service = self._service(chat)
        a, b = self.fx.two_actors()
        session = unwrap(service.start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b]))).id
        result = service.run(self.fx.ctx, session, 1)

        self.assertTrue(is_err(result), "Provider 失败必须传播，不伪造成功")
        rec = self.ledger.records()[0]
        self.assertEqual(rec.outcome, OUTCOME_FAILED)
        self.assertEqual(rec.cost_status, COST_UNKNOWN)
        self.assertIsNone(rec.cost_confirmed, "不知道花了多少时不能填 0")
        self.assertEqual(len(self._stored_records()), 1)
        # 失败后保守停手：下一次调用会被闸门拦住（D-089）
        pause, why = self.ledger.should_pause()
        self.assertTrue(pause, why)

    def test_empty_reply_is_still_charged_and_recorded(self):
        """返回空内容也是**真的调用过一次**：照样记账，不能因为"业务上没用"就不记。"""
        chat = BillableChat(text="   ")
        service = self._service(chat)
        a, b = self.fx.two_actors()
        session = unwrap(service.start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b]))).id
        result = service.run(self.fx.ctx, session, 1)

        self.assertTrue(is_err(result), "空回复不得伪造成功")
        rec = self.ledger.records()[0]
        self.assertEqual(rec.outcome, OUTCOME_SUCCEEDED)
        self.assertAlmostEqual(rec.cost_confirmed, 0.00054, places=9)

    def test_each_real_call_gets_its_own_record(self):
        """两次真实调用 = 两笔记录（对话是事件，不做"同一轮只算一次"的折叠）。"""
        chat = BillableChat()
        service = self._service(chat)
        a, b = self.fx.two_actors()
        session = unwrap(service.start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b]))).id
        unwrap(service.run(self.fx.ctx, session, 2))

        self.assertEqual(chat.calls, 2)
        keys = {r.idempotency_key for r in self.ledger.records()}
        self.assertEqual(len(keys), 2, "每次调用必须有独立键，否则会静默丢账")
        self.assertEqual(len(self._stored_records()), 2)

    def test_offline_stub_is_not_recorded(self):
        """离线替身不产生外部用量：不记 `UNKNOWN`、不占额度、不被闸门拦。"""
        self.assertTrue(getattr(FakeProvider(), "offline", False),
                        "FakeProvider 必须显式声明 offline（否则会凭空产生未知费用）")

        class OfflineChat(BillableChat):
            offline = True

        service = self._service(OfflineChat(), limits=ModelCallLimits(max_calls=1))
        a, b = self.fx.two_actors()
        session = unwrap(service.start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b]))).id
        unwrap(service.run(self.fx.ctx, session, 1))

        self.assertEqual(self.ledger.records(), [])
        self.assertEqual(self._stored_records(), [])

    def test_shared_call_cap_stops_the_second_call(self):
        """`max_calls` 约束的是"这次运行几次模型调用"，对话与抽取共用同一本账。"""
        chat = BillableChat()
        service = self._service(chat, limits=ModelCallLimits(
            max_calls=1, price_per_1k_in=0.0003, price_per_1k_out=0.0012,
            currency="USD", est_tokens_in=1000, est_tokens_out=500))
        a, b = self.fx.two_actors()
        session = unwrap(service.start_session(
            self.fx.ctx, SessionDraft(participant_entity_ids=[a, b]))).id
        unwrap(service.run(self.fx.ctx, session, 1))
        second = service.run(self.fx.ctx, session, 1)

        self.assertTrue(is_err(second))
        self.assertEqual(second.error.code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(chat.calls, 1, "超上限后不得再发请求")


class DialogueAccountingWiringTest(unittest.TestCase):
    """装配层必须把账本交给对话：否则"产品路径漏账"，只有单测通过。"""

    def test_assembly_passes_ledger_limits_and_sink_to_dialogue(self):
        from persona.orchestrator import StackOptions, build_business_stack

        tmp = tempfile.mkdtemp(prefix="gps-p48-wire-")
        storage = SqliteStorage(Path(tmp) / "w.db")
        self.addCleanup(storage.close)
        self.addCleanup(shutil.rmtree, tmp, True)
        ctx = new_call_context(project_id=PROJECT, actor="p48-test")
        stack = unwrap(build_business_stack(
            test_settings(), ctx, storage=storage, options=StackOptions()))

        deps = stack.dialogue.deps
        self.assertIs(deps.ledger, stack.ledger, "对话必须用同一本账")
        self.assertIs(deps.usage_sink, storage, "对话用量必须能落库")
        self.assertIsNotNone(deps.limits, "对话也要受次数/费用闸门约束")
        self.assertEqual(deps.task_id, None)


# ==========================================================================
# D-2：无关页面不得登记为来源
# ==========================================================================


def _result(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, canonical_key=url, snippet=snippet)


#: R4 真实返回的页面形状：全部是汽车防雾灯，与「阿灯 《雾港灯会》」无关。
NOISE = [
    _result("汽车前雾灯与后雾灯的区别", "https://auto.invalid/fog-light",
            "前雾灯用于雨雾天气照明，后雾灯为红色警示灯。"),
    _result("雾灯_百度百科", "https://baike.invalid/fog-lamp",
            "雾灯是指安装于汽车前后的灯具。"),
    _result("雾灯灯泡选购指南", "https://shop.invalid/item/123",
            "12V 55W 雾灯灯泡，包邮。"),
]


class RelevanceFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-p48-d2-")
        self.fx = ResearchFixture(self.tmp)
        self.hero = self.fx.entity("阿灯")
        self.svc = self.fx.service(FakeProvider())

    def tearDown(self) -> None:
        self.fx.close()

    def _target(self, name: str = "阿灯", **kw):
        return self.fx.target(self.hero, name, **kw)

    def test_irrelevant_pages_are_rejected_with_reason(self):
        """R4 现场复现：5 条防雾灯页面不得进入 `accepted`。"""
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, NOISE, targets=[self._target(work_titles=["雾港灯会"])]))
        self.assertEqual(ranked.accepted, [])
        self.assertEqual(len(ranked.rejected), 3)
        for rejection in ranked.rejected:
            self.assertTrue(rejection.reason.startswith(REASON_IRRELEVANT),
                            rejection.reason)
        codes = {w.code for w in ranked.warnings}
        self.assertIn(WARNING_IRRELEVANT_REJECTED, codes)

    def test_name_in_snippet_is_enough(self):
        ok = _result("雨夜值守记录", "https://fans.invalid/note-1",
                     "阿灯在雨夜独自巡堤，直到天亮。")
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, [ok] + NOISE, targets=[self._target()]))
        self.assertEqual([c.result.url for c in ranked.accepted], [ok.url])

    def test_work_title_in_title_is_enough(self):
        """只有作品名（没有角色名）也算相关——《X》角色介绍页就是这样。"""
        page = _result("《雾港灯会》角色设定集", "https://fans.invalid/characters",
                       "主要登场人物一览。")
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, [page], targets=[self._target(work_titles=["雾港灯会"])]))
        self.assertEqual(len(ranked.accepted), 1)

    def test_alias_is_enough(self):
        page = _result("灯塔守夜人小记", "https://fans.invalid/alias",
                       "小灯是雾港的守夜人。")
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, [page], targets=[self._target(aliases=["小灯"])]))
        self.assertEqual(len(ranked.accepted), 1)

    def test_url_path_is_enough_for_latin_names(self):
        """英文页面的标题可能不含全名，但 `/wiki/Sherlock_Holmes` 已是强信号。"""
        page = _result("The Adventure of the Blue Carbuncle",
                       "https://en.wikipedia.org/wiki/Sherlock_Holmes",
                       "Short story by Arthur Conan Doyle.")
        target = self.fx.target(self.hero, "Sherlock Holmes")
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, [page], targets=[target]))
        self.assertEqual(len(ranked.accepted), 1)

    def test_no_target_names_means_no_filter_but_says_so(self):
        """没有名字可判时**不假装过滤过**：照收，但显式告警。"""
        ranked = unwrap(self.svc.filter_and_rank(
            self.fx.ctx, NOISE, targets=[self.fx.target(self.hero, "")]))
        self.assertEqual(len(ranked.accepted), 3)
        self.assertIn(WARNING_RELEVANCE_NOT_CHECKED,
                      {w.code for w in ranked.warnings})

    def test_run_does_not_register_irrelevant_source_end_to_end(self):
        """端到端：`run()` 走完，无关页面**不落 Source 表**（登记 ≠ 可用）。"""
        class NoiseProvider:
            provider_name = "noise-stub"
            offline = True

            def search(self, ctx, req):
                return Ok(SearchResponse(results=list(NOISE), usage=UsageDelta(requests=1)))

            def extract(self, ctx, req):
                raise AssertionError("无关页面不该进入抓取阶段")

            def chat(self, ctx, req):
                raise AssertionError("检索流程不得调用 chat")

            def usage(self, ctx, scope):
                return Ok(UsageReport(requests=1))

            def health(self, ctx):
                return Ok(HealthStatus(reachable=True))

            def capabilities(self):
                return Ok(CapabilitySet(provider_name="noise-stub", supports_search=True))

        svc = self.fx.service(NoiseProvider())
        out = unwrap(svc.run(self.fx.ctx, [self._target(work_titles=["雾港灯会"])]))
        self.assertEqual(out.source_ids, [], "无关页面不得登记为来源")
        self.assertEqual(self.fx.count("Source"), 0)
        codes = {w.code for w in out.warnings}
        self.assertIn(WARNING_IRRELEVANT_REJECTED, codes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
