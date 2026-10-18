#!/usr/bin/env python
"""D-294：模型调用的**运行参数必须落盘**（`trait_tag_enabled` 是同一根因）。

# 缺陷回顾

`ChatRequest.metadata` 里有 `temperature` / `prompt_version` / `trait_tag_enabled` /
`trait_tags` / `max_output_tokens` / `material_count` 等全部运行参数，但：

- `DeepSeekProvider._build_body()` **刻意不外发**它（对的——不该把内部审计信息
  交给第三方）；
- 此前**也没有任何本地落盘路径**（全仓库只有 CLI dry-run 打印 `prompt_version`）。

于是 R4.8 只能从 `raw_responses.jsonl` 的 HTTP body 反推 `temperature=0.0`，
而 `trait_tag_enabled`（装的是哪层词表）**没有对应的 body 字段，彻底查不到**。
P2 遗留的"`metadata.trait_tag_enabled` 未落库"不是孤立遗漏，是同一条缺口。

# 本文件锁什么

1. 每次抽取在**发送前**写一条 `model.request` 审计，含全部运行参数；
2. 参数落的是**真实生效值**（显式 0 记 0，不记 None/不记默认值）；
3. 老 sink（只有 record/has 两个方法）**不因新增方法而失效**；
4. 审计是**旁路**：写失败不阻断抽取，但必须记 `AUDIT_WRITE_FAILED` 告警——
   "以为留痕了、其实没有"比没有更糟；
5. 审计**不改变外发行为**：请求对象与之前逐字段一致。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, is_ok
from persona.core.types import ExternalUsageRecord, SourceMaterial
from persona.modules.extract_quality import QUALITY_VALID
from persona.modules.extraction import (
    PROMPT_VERSION,
    WARNING_AUDIT_WRITE_FAILED,
    ProviderClaimExtractor,
)
from persona.modules.trait_vocabulary import vocabulary_by_name
from persona.providers.base import CapabilitySet, ChatRequest, ChatResponse
from persona.providers.mock import network_blocked

PROJECT = "prj_d294"
KEY = "key_dai"
URL = "https://example.invalid/why-dai"
SNIPPET = "林黛玉言辞尖酸刻薄、喜欢怼人，这是她性格里最鲜明的一面。"


def _material(*, quality: str = QUALITY_VALID) -> SourceMaterial:
    return SourceMaterial(
        source_id="src_dai", title="为什么是林黛玉？", url=URL, canonical_key=KEY,
        snippet=SNIPPET, subject_entity_ids=["pe_dai"], quality_status=quality)


def _payload() -> str:
    return (
        '{"claims": [{"key": "c1", "material_key": "' + KEY + '", '
        '"claim": "林黛玉言辞尖刻、喜欢怼人", "tier": "VERBATIM", '
        '"dimension": "TRAIT", "subject_entity_ids": ["pe_dai"], '
        '"confidence": 0.9, "verbatim_quote": "林黛玉言辞尖酸刻薄、喜欢怼人", '
        '"trait_tag": "caustic"}]}'
    )


class _StubProvider:
    provider_name = "stub"
    offline = True

    def __init__(self, *, raises: BaseException | None = None) -> None:
        self.raises = raises
        self.calls = 0
        self.last_request: ChatRequest | None = None

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.calls += 1
        self.last_request = req
        if self.raises is not None:
            raise self.raises
        return Ok(ChatResponse(text=_payload(), finish_reason="stop",
                               model_id="stub"))

    def search(self, ctx, req):          # pragma: no cover
        raise AssertionError("本测试不检索")

    def usage(self, ctx, scope):         # pragma: no cover
        raise AssertionError("本测试不查用量")

    def health(self, ctx):               # pragma: no cover
        raise AssertionError("本测试不探活")


class _AuditSink:
    """模拟 `SqliteStorage` 的窄接口：账本两方法 + `append_audit`。"""

    def __init__(self, *, raises: Exception | None = None,
                 error: Exception | None = None) -> None:
        self.audits: list[dict] = []
        self._raises = raises
        self._error = error

    def record_external_usage(self, ctx, record: ExternalUsageRecord):
        return Ok(record.idempotency_key)

    def has_external_usage(self, ctx, idempotency_key: str):
        return Ok(False)

    def append_audit(self, ctx, level, action, *, detail=None):
        if self._raises is not None:
            raise self._raises
        if self._error is not None:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED,
                                 "注入写失败", trace_ref=ctx.trace_ref))
        self.audits.append({"level": level, "action": action, "detail": detail})
        return Ok("adt_stub")


class _BareSink:
    """**老实现**：只有账本两方法，没有 `append_audit`。"""

    def record_external_usage(self, ctx, record: ExternalUsageRecord):
        return Ok(record.idempotency_key)

    def has_external_usage(self, ctx, idempotency_key: str):
        return Ok(False)


def _ctx():
    return new_call_context(project_id=PROJECT, actor="tests")


class TestAuditContent(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = _StubProvider()
        self.sink = _AuditSink()
        self.extractor = ProviderClaimExtractor(
            self.provider, usage_sink=self.sink, temperature=0.0,
            vocabulary=vocabulary_by_name("l2-zh-classical"))

    def test_model_request_is_audited(self):
        with network_blocked():
            ok = self.extractor.extract(_ctx(), [_material()])
        self.assertTrue(is_ok(ok), getattr(ok, "error", None))
        records = [a for a in self.sink.audits if a["action"] == "model.request"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["level"], "INFO")
        # 审计不是"抽不到也算跑过"：这条 claim 真的被接受了
        self.assertEqual(len(ok.value.claims), 1)
        self.assertEqual(ok.value.claims[0].trait_tag, "caustic")

    def test_audit_carries_every_run_parameter(self):
        with network_blocked():
            self.extractor.extract(_ctx(), [_material()])
        detail = next(a["detail"] for a in self.sink.audits
                      if a["action"] == "model.request")
        self.assertEqual(detail["kind"], "model_request")
        self.assertEqual(detail["purpose"], "claim_extraction")
        # 温度：显式 0 必须记 0（不是 None、不是生产默认 0.7）
        self.assertEqual(detail["temperature"], 0.0)
        self.assertEqual(detail["prompt_version"], PROMPT_VERSION)
        # P2 遗留项：这一格以前彻底查不到
        self.assertEqual(detail["trait_tag_enabled"], "true")
        self.assertEqual(detail["trait_tags"], "42")
        self.assertEqual(detail["material_count"], "1")
        self.assertEqual(detail["max_claims_per_material"], "4")
        self.assertTrue(str(detail["max_output_tokens"]).isdigit())

    def test_audit_matches_the_request_actually_sent(self):
        """审计里的值与真正构造出来的请求**同源**，不是另算一遍。"""
        with network_blocked():
            self.extractor.extract(_ctx(), [_material()])
        detail = next(a["detail"] for a in self.sink.audits
                      if a["action"] == "model.request")
        request = self.provider.last_request
        self.assertEqual(detail["temperature"], request.temperature)
        self.assertEqual(detail["prompt_version"],
                         request.metadata.get("prompt_version"))
        self.assertEqual(detail["trait_tag_enabled"],
                         request.metadata.get("trait_tag_enabled"))

    def test_without_vocabulary_flag_is_false(self):
        extractor = ProviderClaimExtractor(
            _StubProvider(), usage_sink=self.sink, temperature=0.7)
        with network_blocked():
            extractor.extract(_ctx(), [_material()])
        detail = next(a["detail"] for a in self.sink.audits
                      if a["action"] == "model.request")
        self.assertEqual(detail["trait_tag_enabled"], "false")
        self.assertEqual(detail["trait_tags"], "0")


class TestAuditIsSideChannel(unittest.TestCase):
    def test_failed_call_is_still_audited(self):
        """失败 ≠ 没发生：抛异常的调用也要留下参数。"""
        sink = _AuditSink()
        extractor = ProviderClaimExtractor(
            _StubProvider(raises=RuntimeError("boom")), usage_sink=sink,
            temperature=0.0)
        with network_blocked():
            with self.assertRaises(RuntimeError):
                extractor.extract(_ctx(), [_material()])
        actions = [a["action"] for a in sink.audits]
        self.assertEqual(actions.count("model.request"), 1)

    def test_old_sink_without_append_audit_keeps_working(self):
        extractor = ProviderClaimExtractor(
            _StubProvider(), usage_sink=_BareSink(), temperature=0.0)
        with network_blocked():
            ok = extractor.extract(_ctx(), [_material()])
        self.assertTrue(is_ok(ok), getattr(ok, "error", None))
        codes = [w.code for w in ok.value.warnings]
        self.assertNotIn(WARNING_AUDIT_WRITE_FAILED, codes)

    def test_audit_raising_does_not_block_extraction(self):
        sink = _AuditSink(raises=RuntimeError("disk full"))
        extractor = ProviderClaimExtractor(
            _StubProvider(), usage_sink=sink, temperature=0.0)
        with network_blocked():
            ok = extractor.extract(_ctx(), [_material()])
        self.assertTrue(is_ok(ok), "审计失败不该让抽取失败")
        codes = [w.code for w in ok.value.warnings]
        self.assertIn(WARNING_AUDIT_WRITE_FAILED, codes)

    def test_audit_error_result_also_warns(self):
        sink = _AuditSink(error=RuntimeError("write failed"))
        extractor = ProviderClaimExtractor(
            _StubProvider(), usage_sink=sink, temperature=0.0)
        with network_blocked():
            ok = extractor.extract(_ctx(), [_material()])
        self.assertTrue(is_ok(ok))
        codes = [w.code for w in ok.value.warnings]
        self.assertIn(WARNING_AUDIT_WRITE_FAILED, codes)

    def test_audit_does_not_change_the_outbound_request(self):
        """**外发行为零变化**：审计只进本地库，请求对象逐字段不变。"""
        sink = _AuditSink()
        provider = _StubProvider()
        extractor = ProviderClaimExtractor(
            provider, usage_sink=sink, temperature=0.0)
        with network_blocked():
            extractor.extract(_ctx(), [_material()])
        request = provider.last_request
        self.assertEqual(request.temperature, 0.0)
        self.assertEqual(set(request.metadata), {
            "prompt_version", "max_output_tokens", "material_count",
            "max_claims_per_material", "temperature", "trait_tag_enabled",
            "trait_tags", "output_ceiling_sufficient"})
        # 审计结果**不写回** metadata（否则下一次幂等键/请求体可能被污染）
        self.assertNotIn("audit_id", request.metadata)


class TestPersistedToRealStorage(unittest.TestCase):
    """用户要求：**跑完后能从库里查到**本轮运行的完整参数。"""

    def setUp(self) -> None:
        from persona.storage.sqlite import SqliteStorage

        self.tmp = tempfile.TemporaryDirectory()
        self.storage = SqliteStorage(Path(self.tmp.name) / "audit.db")
        self.assertTrue(is_ok(self.storage.init_schema(_ctx())))
        self.provider = _StubProvider()
        self.extractor = ProviderClaimExtractor(
            self.provider, usage_sink=self.storage, temperature=0.0,
            vocabulary=vocabulary_by_name("l2-zh-classical"))

    def tearDown(self) -> None:
        self.storage.close()
        self.tmp.cleanup()

    def test_model_request_row_is_queryable_from_sqlite(self):
        import json

        with network_blocked():
            ok = self.extractor.extract(_ctx(), [_material()])
        self.assertTrue(is_ok(ok), getattr(ok, "error", None))
        rows = list(self.storage.connect().execute(
            "SELECT action, level, detail FROM audit_records"
            " WHERE action = 'model.request'"))
        self.assertEqual(len(rows), 1)
        action, level, detail = rows[0]
        self.assertEqual(action, "model.request")
        self.assertEqual(level, "INFO")
        parsed = json.loads(detail)
        self.assertEqual(parsed["temperature"], 0.0)
        self.assertEqual(parsed["trait_tag_enabled"], "true")
        self.assertEqual(parsed["prompt_version"], PROMPT_VERSION)

    def test_row_is_written_once_per_call(self):
        with network_blocked():
            self.extractor.extract(_ctx(), [_material()])
        count = self.storage.connect().execute(
            "SELECT COUNT(*) FROM audit_records WHERE action = 'model.request'"
        ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
