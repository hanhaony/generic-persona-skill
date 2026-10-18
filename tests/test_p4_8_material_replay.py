#!/usr/bin/env python
"""固定材料集（D-290）：缓存回放机制的单元测试。

R4.6 的方法论教训：同目标、同检索词、同参数的两次运行，10 个来源只有 5 个重合
（Tavily 排序/索引会变）。**材料不固定，"同一实验做两次"就不成立**，任何条数对比
都不可归因。本文件锁三件事：

1. 缓存只装 tavily 的**成功**往返，模型调用与失败往返不进缓存；
2. 回放**物理上不联网**，缺项必须抛 `MaterialReplayMiss`（绝不回落到网络）；
3. 回放**不记账**：provider 换一本空账本，原账本 0 笔——否则费用口径就假了。

另含探测句注入（"黛玉侧材料里放一句宝玉的说法"），用于强判别样本。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _extra in (_REPO_ROOT, _REPO_ROOT / "scripts"):
    if str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))

import e2e_p4_8_real_search as runner                                    # noqa: E402

from persona.core.context import new_call_context
from persona.core.result import is_ok
from persona.core.usage_ledger import ExternalUsageLedger
from persona.modules.material_replay import (
    MaterialReplayError,
    MaterialReplayMiss,
    ReplayCache,
    inject_probe_via,
    install_replay,
    load_replay_cache,
    resolve_cache_path,
)
from persona.providers.base import SearchRequest
from persona.providers.tavily import TavilyProvider

SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"


def _search_body(query: str) -> str:
    """与 `TavilyProvider.search()` 发出的请求体**逐字段一致**（顺序也一致）。

    缓存键就是请求体原文：参数一变（limit / depth / include_*）就查不到，
    这是刻意的——回放只认"同一次请求"。本夹具必须跟着 Provider 的 payload 走。
    """
    return json.dumps({
        "query": query,
        "max_results": 5,
        "search_depth": "basic",
        "include_raw_content": False,
        "include_images": False,
        "include_usage": True,
    })


def _search_response(results: list[dict]) -> str:
    return json.dumps({
        "results": results,
        "usage": {"credits": 1},
        "request_id": "req_d290",
    }, ensure_ascii=False)


def _write_cache(path: Path, entries: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8")
    return path


def _entries() -> list[dict]:
    return [
        {"label": "search", "url": SEARCH_URL, "request": _search_body("林黛玉 红楼梦"),
         "response": _search_response([
             {"title": "为什么是林黛玉？", "url": "https://example.invalid/dai",
              "content": "林黛玉言辞尖刻。"},
             {"title": "红楼梦总页", "url": "https://example.invalid/all",
              "content": "红楼梦全书概述。"},
         ])},
        {"label": "search", "url": SEARCH_URL, "request": _search_body("贾宝玉 红楼梦"),
         "response": _search_response([
             {"title": "贾宝玉 百度百科", "url": "https://example.invalid/bao",
              "content": "贾宝玉厌恶四书。"},
         ])},
        {"label": "search", "url": SEARCH_URL, "request": _search_body("红楼梦 人物群像"),
         "response": _search_response([
             {"title": "群像一", "url": "https://example.invalid/g1", "content": "群像一正文。"},
             {"title": "群像二", "url": "https://example.invalid/g2", "content": "群像二正文。"},
             {"title": "群像三", "url": "https://example.invalid/g3", "content": "群像三正文。"},
         ])},
        {"label": "extract", "url": EXTRACT_URL,
         "request": json.dumps({"urls": ["https://example.invalid/dai"],
                                "include_images": False,
                                "include_usage": True,
                                "extract_depth": "basic"}),
         "response": json.dumps({"results": [{"url": "https://example.invalid/dai",
                                              "raw_content": "林黛玉正文"}]})},
        # 失败往返：没有 response，不进缓存
        {"label": "search", "url": SEARCH_URL, "request": _search_body("失败的查询"),
         "error": "URLError: timed out"},
        # 模型调用：绝不缓存（模型必须真实发生）
        {"label": "chat", "url": "https://api.deepseek.com/chat/completions",
         "request": "{}", "response": json.dumps({"choices": []})},
    ]


class CacheFileTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache_file = _write_cache(self.root / "raw_responses.jsonl", _entries())

    def tearDown(self) -> None:
        self.tmp.cleanup()


class TestLoad(CacheFileTestCase):
    """缓存装载：只收 tavily 的成功往返。"""

    def test_resolves_directory_and_direct_file(self):
        self.assertEqual(resolve_cache_path(self.root), self.cache_file)
        self.assertEqual(resolve_cache_path(self.cache_file), self.cache_file)

    def test_resolves_real_subdirectory(self):
        real = self.root / "real"
        real.mkdir()
        moved = real / "raw_responses.jsonl"
        moved.write_text(self.cache_file.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(resolve_cache_path(self.root), moved)

    def test_missing_file_raises(self):
        with self.assertRaises(MaterialReplayError):
            resolve_cache_path(self.root / "nope")

    def test_skips_failures_and_model_calls(self):
        cache = load_replay_cache(self.root)
        self.assertEqual(len(cache), 4)                     # 3 search + 1 extract
        self.assertEqual(cache.counts(), {"search": 3, "extract": 1})
        # 失败的那次检索与模型调用都不在缓存里
        keys = [body for (_url, body) in cache.entries]
        self.assertNotIn(_search_body("失败的查询"), keys)
        self.assertFalse(any("chat" in url for (url, _b) in cache.entries))

    def test_empty_cache_raises(self):
        empty = _write_cache(self.root / "empty.jsonl", [{"label": "chat",
                                                          "url": "https://x/chat/completions",
                                                          "request": "{}", "response": "{}"}])
        with self.assertRaises(MaterialReplayError):
            load_replay_cache(empty)

    def test_lookup_is_exact_on_body(self):
        cache = load_replay_cache(self.root)
        self.assertIsNotNone(cache.lookup(SEARCH_URL, _search_body("林黛玉 红楼梦")))
        self.assertIsNone(cache.lookup(SEARCH_URL, _search_body("换了一个词")))

    def test_queries_keep_file_order(self):
        cache = load_replay_cache(self.root)
        self.assertEqual([q for q, _k in cache.queries()],
                         ["林黛玉 红楼梦", "贾宝玉 红楼梦", "红楼梦 人物群像"])


class TestProbeInjection(CacheFileTestCase):
    """强判别样本：把"宝玉的说法"放进黛玉侧材料。

    注入位置**优先抓取全文**：往摘要里注入时，if 那份摘要正好是要抓全文的源，
    注入句会被 `MATERIAL_SUPERSEDED`（摘要→全文）静默吃掉；全文不会被取代。
    """

    PROBE = "贾宝玉自幼厌恶科举功名，把读书上进的人称作禄蠹。"

    def test_probe_prefers_extract_fulltext(self):
        cache = load_replay_cache(self.root)
        updated, url, placement = cache.inject_probe(
            query_contains="林黛玉", text=self.PROBE)
        self.assertEqual(placement, "extract_fulltext")
        self.assertEqual(url, "https://example.invalid/dai")
        extract_body = updated.lookup(
            EXTRACT_URL,
            json.dumps({"urls": ["https://example.invalid/dai"], "include_images": False,
                        "include_usage": True, "extract_depth": "basic"}))
        self.assertIn(self.PROBE, json.loads(extract_body)["results"][0]["raw_content"])
        # 检索摘要保持原样（不被顺手改掉）
        search_body = updated.lookup(SEARCH_URL, _search_body("林黛玉 红楼梦"))
        self.assertNotIn(self.PROBE, search_body)

    def test_probe_falls_back_to_snippet_when_no_extract(self):
        cache = load_replay_cache(self.root)
        updated, url, placement = cache.inject_probe(
            query_contains="贾宝玉", text="林黛玉自幼体弱多病。")
        self.assertEqual(placement, "search_snippet")
        self.assertEqual(url, "https://example.invalid/bao")
        self.assertIn("林黛玉自幼体弱多病。",
                      updated.lookup(SEARCH_URL, _search_body("贾宝玉 红楼梦")))

    def test_probe_index_selects_which_snippet(self):
        cache = load_replay_cache(self.root)
        updated, url, placement = cache.inject_probe(
            query_contains="人物群像", text="某句探测文本。", index=-1)
        self.assertEqual(placement, "search_snippet")
        self.assertEqual(url, "https://example.invalid/g3")
        results = json.loads(updated.lookup(SEARCH_URL, _search_body("红楼梦 人物群像")))["results"]
        self.assertIn("某句探测文本。", results[2]["content"])
        self.assertNotIn("某句探测文本。", results[0]["content"])

    def test_probe_index_out_of_range_raises(self):
        cache = load_replay_cache(self.root)
        with self.assertRaises(MaterialReplayError):
            cache.inject_probe(query_contains="人物群像", text="某句", index=99)

    def test_probe_does_not_mutate_the_original_cache(self):
        cache = load_replay_cache(self.root)
        cache.inject_probe(query_contains="林黛玉", text=self.PROBE)
        body = cache.lookup(
            EXTRACT_URL,
            json.dumps({"urls": ["https://example.invalid/dai"], "include_images": False,
                        "include_usage": True, "extract_depth": "basic"}))
        self.assertNotIn(self.PROBE, body)

    def test_unknown_query_raises(self):
        cache = load_replay_cache(self.root)
        with self.assertRaises(MaterialReplayError):
            cache.inject_probe(query_contains="福尔摩斯", text="某句话")

    def test_empty_text_raises(self):
        cache = load_replay_cache(self.root)
        with self.assertRaises(MaterialReplayError):
            cache.inject_probe(query_contains="林黛玉", text="   ")

    def test_inject_probe_via_path_helper(self):
        updated, url, placement = inject_probe_via(
            self.root, query_contains="林黛玉", text=self.PROBE)
        self.assertEqual((url, placement), ("https://example.invalid/dai", "extract_fulltext"))


class TestInstallReplay(CacheFileTestCase):
    """安装：不联网、缺项必炸、回放不记账。"""

    def _provider(self) -> TavilyProvider:
        return TavilyProvider(api_key="test-key-not-real")

    def test_replayed_search_needs_no_network(self):
        cache = load_replay_cache(self.root)
        provider = self._provider()
        session = install_replay(provider, cache)
        ctx = new_call_context(project_id="prj_d290", actor="t")
        result = provider.search(ctx, SearchRequest(query="林黛玉 红楼梦", limit=5))
        self.assertTrue(is_ok(result))
        self.assertEqual([r.title for r in result.value.results],
                         ["为什么是林黛玉？", "红楼梦总页"])
        self.assertEqual(session.hits("search"), 1)
        self.assertEqual(session.miss_count(), 0)

    def test_replay_does_not_touch_the_real_ledger(self):
        cache = load_replay_cache(self.root)
        provider = self._provider()
        real_ledger = ExternalUsageLedger()
        provider.usage_ledger = real_ledger
        session = install_replay(provider, cache)
        ctx = new_call_context(project_id="prj_d290", actor="t")
        provider.search(ctx, SearchRequest(query="林黛玉 红楼梦", limit=5))
        # 真账本一笔不落：回放没有发生任何真实计费
        self.assertEqual(real_ledger.records(), [])
        self.assertIsNotNone(session.ledger)
        self.assertNotEqual(session.ledger, real_ledger)

    def test_uncached_request_raises_instead_of_going_online(self):
        cache = load_replay_cache(self.root)
        provider = self._provider()
        session = install_replay(provider, cache)
        ctx = new_call_context(project_id="prj_d290", actor="t")
        with self.assertRaises(MaterialReplayMiss):
            provider.search(ctx, SearchRequest(query="福尔摩斯 血字的研究", limit=5))
        self.assertEqual(session.miss_count(), 1)

    def test_extract_also_replays(self):
        cache = load_replay_cache(self.root)
        provider = self._provider()
        install_replay(provider, cache)
        ctx = new_call_context(project_id="prj_d290", actor="t")
        from persona.providers.base import ExtractRequest
        result = provider.extract(ctx, ExtractRequest(urls=["https://example.invalid/dai"]))
        self.assertTrue(is_ok(result))
        self.assertEqual(result.value.results[0].raw_content, "林黛玉正文")

    def test_session_counts_hits_by_kind(self):
        cache = load_replay_cache(self.root)
        provider = self._provider()
        session = install_replay(provider, cache)
        ctx = new_call_context(project_id="prj_d290", actor="t")
        provider.search(ctx, SearchRequest(query="林黛玉 红楼梦", limit=5))
        provider.search(ctx, SearchRequest(query="林黛玉 红楼梦", limit=5))
        self.assertEqual(session.hits("search"), 2)
        self.assertEqual(session.unique_hits, 1)


class TestProbeVerdict(unittest.TestCase):
    """探针判定必须是三态可解释的结论，不能"大概正确"。

    OK   = 黛玉侧材料里的宝玉说法挂给了宝玉；
    FAIL = 挂错（D-288 复现）；
    WARN = 探针句没被抽成 claim（本轮没测到，不是通过）。
    """

    TARGETS = [SimpleNamespace(display_name="林黛玉", entity_id="pe_dai"),
               SimpleNamespace(display_name="贾宝玉", entity_id="pe_bao")]
    SOURCES = [{"id": "src_dai_side", "url": "https://example.invalid/dai"}]
    PROBE = {"target": "林黛玉", "about": "贾宝玉",
             "url": "https://example.invalid/dai", "text": "探针", "index": -1,
             "placement": "extract_fulltext"}

    def _judge(self, evidence, sources=None):
        return runner._judge_probe(evidence=evidence,
                                   sources=sources or self.SOURCES,
                                   probe_info=self.PROBE, targets=self.TARGETS)

    def test_ok_when_statement_about_bao_is_bound_to_bao(self):
        evidence = [{"source_id": "src_dai_side", "claim": "贾宝玉厌恶科举与禄蠹。",
                     "subject_entity_ids": ["pe_bao"]}]
        verdict, lines = self._judge(evidence)
        self.assertEqual(verdict, "OK")
        self.assertTrue(any("挂给了 贾宝玉" in line for line in lines))

    def test_fail_when_statement_is_bound_to_the_retrieval_target(self):
        """这正是 R4.5 的错法：材料是黛玉侧 → 整份算给黛玉。"""
        evidence = [{"source_id": "src_dai_side", "claim": "贾宝玉厌恶科举与禄蠹。",
                     "subject_entity_ids": ["pe_dai"]}]
        verdict, lines = self._judge(evidence)
        self.assertEqual(verdict, "FAIL")
        self.assertTrue(any("没有挂给 贾宝玉" in line for line in lines))

    def test_double_subject_still_counts_as_bound(self):
        """双主体（黛玉+宝玉）也算挂对：只要 about 在主体里。"""
        evidence = [{"source_id": "src_dai_side", "claim": "贾宝玉厌恶科举。",
                     "subject_entity_ids": ["pe_dai", "pe_bao"]}]
        self.assertEqual(self._judge(evidence)[0], "OK")

    def test_warn_when_probe_source_missing(self):
        verdict, lines = self._judge([], sources=[{"id": "src_x", "url": "https://other"}])
        self.assertEqual(verdict, "WARN")
        self.assertTrue(any("没进来源表" in line for line in lines))

    def test_warn_when_about_is_not_a_target(self):
        probe = dict(self.PROBE, about="福尔摩斯")
        verdict, _lines = runner._judge_probe(
            evidence=[], sources=self.SOURCES, probe_info=probe, targets=self.TARGETS)
        self.assertEqual(verdict, "WARN")

    def test_warn_when_probe_sentence_was_not_extracted(self):
        evidence = [{"source_id": "src_dai_side", "claim": "林黛玉言辞尖刻。",
                     "subject_entity_ids": ["pe_dai"]}]
        verdict, lines = self._judge(evidence)
        self.assertEqual(verdict, "WARN")
        self.assertTrue(any("没有被抽成 claim" in line for line in lines))

    def test_evidence_from_other_sources_is_ignored(self):
        evidence = [{"source_id": "src_bao_side", "claim": "贾宝玉厌恶科举。",
                     "subject_entity_ids": ["pe_bao"]}]
        self.assertEqual(self._judge(evidence)[0], "WARN")


class TestReplayDoesNotPersistUsage(CacheFileTestCase):
    """D-292：回放期间 `external_usage_records` 必须**零新增**。

    R4.7 暴露的漏洞：`install_replay` 把 `provider.usage_ledger` 换成一本普通空账本，
    内存层看起来干净了，但 `ResearchModule._persist_usage()` 遍历的**正是**这本账本，
    回放的 `settle()` 写进去的 3 笔会在收尾时被原样落库 ⇒
    `SEARCH 1.0 + SEARCH 1.0 + EXTRACT 0.0 = 2.0 credits` 假支出，
    而且 `request_id` 是**上一轮的真 id**（对账时看起来像真花过钱）。

    这里走的是**真链路**：回放 → provider 记账 → `_persist_usage()` → SqliteStorage。
    """

    def tearDown(self) -> None:
        # 先把 sqlite 连接关掉，再删临时目录（Windows 下文件被占用会删不掉）
        for storage in getattr(self, "_storages", []):
            storage.close()
        self.tmp.cleanup()

    def _storage(self, ctx):
        from persona.storage.sqlite import SqliteStorage
        storage = SqliteStorage(Path(self.root) / "usage.db")
        self._storages = getattr(self, "_storages", []) + [storage]
        self.assertTrue(is_ok(storage.init_schema(ctx)))
        return storage

    def _research(self, provider, storage):
        from persona.modules.research import ResearchDeps, ResearchService

        class _NoopRegistrar:
            def register_source(self, ctx, source):
                from persona.core.result import Ok
                return Ok("src_noop")

        return ResearchService(ResearchDeps(
            provider=provider, evidence=_NoopRegistrar(),
            usage_sink=storage, task_id="tsk_d292"))

    def _replay_once(self, provider, ctx):
        from persona.providers.base import ExtractRequest
        provider.search(ctx, SearchRequest(query="林黛玉 红楼梦", limit=5))
        provider.extract(ctx, ExtractRequest(urls=["https://example.invalid/dai"]))

    def test_persist_usage_writes_nothing_during_replay(self):
        ctx = new_call_context(project_id="prj_d292", actor="t")
        cache = load_replay_cache(self.root)
        provider = TavilyProvider(api_key="test-key-not-real")
        install_replay(provider, cache)
        self._replay_once(provider, ctx)

        # 回放阶段确实"用过"缓存，但账本里一笔都不该有
        self.assertEqual(provider.usage_ledger.records(), [])

        storage = self._storage(ctx)
        self._research(provider, storage)._persist_usage(ctx)
        persisted = storage.external_usage_records(ctx)
        self.assertTrue(is_ok(persisted))
        self.assertEqual(persisted.value, [],
                         "回放不得往 external_usage_records 写任何行")

    def test_discarding_ledger_refuses_settle_but_still_answers_queries(self):
        """丢弃账本的行为边界：`settle()` 恒 False、`records()` 恒空，查询不炸。"""
        ctx = new_call_context(project_id="prj_d292", actor="t")
        provider = TavilyProvider(api_key="test-key-not-real")
        install_replay(provider, load_replay_cache(self.root))
        ledger = provider.usage_ledger
        record = _fake_credit_record()
        self.assertFalse(ledger.settle(record))
        self.assertEqual(ledger.records(), [])
        self.assertEqual(ledger.totals().requests, 0)
        self.assertFalse(ledger.has_attempted(record.idempotency_key))

    def test_control_real_ledger_does_persist(self):
        """对照：**非回放**的账本必须照常落库——否则上一条测的是"落库坏了"。"""
        ctx = new_call_context(project_id="prj_d292", actor="t")
        provider = TavilyProvider(api_key="test-key-not-real")
        provider.usage_ledger.settle(_fake_credit_record())
        storage = self._storage(ctx)
        self._research(provider, storage)._persist_usage(ctx)
        persisted = storage.external_usage_records(ctx)
        self.assertEqual(len(persisted.value), 1)
        self.assertEqual(persisted.value[0].provider, "tavily")


def _fake_credit_record():
    """一笔"看起来真的花了 1 credit"的检索记录（不带网络）。"""
    from persona.core.types import ExternalUsageRecord

    return ExternalUsageRecord(
        idempotency_key="idem_d292_0001",
        provider="tavily",
        operation="SEARCH",
        outcome="SUCCEEDED",
        requested_at="2026-10-18T00:00:00Z",
        request_id="req_d292_0001",
        cost_estimate=1.0,
        cost_confirmed=1.0,
        currency="TAVILY_CREDIT",
        cost_status="CONFIRMED",
        cost_source="PROVIDER_USAGE",
    )


if __name__ == "__main__":
    unittest.main()
