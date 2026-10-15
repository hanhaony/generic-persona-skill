"""Mock Provider 测试：确定性、可回放、可注入失败、**全程离线**。

对应文档：INTERFACES §2.5/§2.6、DECISIONS D-035/D-054/D-060。
"""

import unittest

from persona.core.context import new_call_context
from persona.core.errors import ErrorCode
from persona.core.result import is_err, is_ok, unwrap
from persona.providers.base import (
    ChatRequest,
    ChatResponse,
    Message,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from persona.providers.mock import (
    FailingProvider,
    FakeProvider,
    OfflineGuardProvider,
    OfflineViolation,
    RecordingProvider,
    network_blocked,
)
from persona.providers.registry import default_registry


def _chat_request(key: str = "greeting", text: str = "你好") -> ChatRequest:
    return ChatRequest(
        messages=[Message(role="user", content=text)],
        metadata={"fixture_key": key},
    )


class TestFakeProvider(unittest.TestCase):
    def setUp(self):
        self.ctx = new_call_context(project_id="prj_test", actor="test", task_id="tsk_1")
        self.provider = FakeProvider(
            fixtures={
                "chat:greeting": ChatResponse(text="你好，我是固定返回。"),
                "search:empty": SearchResponse(results=[]),
                "search:two": SearchResponse(
                    results=[SearchResult(title="T1", url="u1"), SearchResult(title="T2", url="u2")]
                ),
            }
        )

    def test_chat_is_deterministic(self):
        first = unwrap(self.provider.chat(self.ctx, _chat_request()))
        second = unwrap(self.provider.chat(self.ctx, _chat_request()))
        self.assertEqual(first.text, second.text)
        self.assertEqual(first.text, "你好，我是固定返回。")

    def test_chat_requires_explicit_fixture_key(self):
        """禁止用请求内容做模糊匹配，否则测试不稳定（INTERFACES §2.6）。"""
        result = self.provider.chat(self.ctx, ChatRequest(messages=[Message(role="user", content="x")]))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)

    def test_unknown_fixture_rejected(self):
        result = self.provider.chat(self.ctx, _chat_request(key="not-registered"))
        self.assertTrue(is_err(result))

    def test_empty_messages_rejected(self):
        req = _chat_request()
        req.messages = []
        self.assertTrue(is_err(self.provider.chat(self.ctx, req)))

    def test_search_empty_query_rejected(self):
        req = SearchRequest(query="   ", metadata={"fixture_key": "empty"})
        result = self.provider.search(self.ctx, req)
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_QUERY_EMPTY)

    def test_search_zero_results_is_ok(self):
        """搜索 0 条结果**不是错误**（ERROR_HANDLING §2.3）。"""
        result = self.provider.search(
            self.ctx, SearchRequest(query="某人", metadata={"fixture_key": "empty"})
        )
        self.assertTrue(is_ok(result))
        self.assertEqual(result.value.results, [])

    def test_usage_accumulates_and_tokens_unknown(self):
        self.provider.chat(self.ctx, _chat_request())
        self.provider.search(
            self.ctx, SearchRequest(query="某人", metadata={"fixture_key": "two"})
        )
        report = unwrap(self.provider.usage(self.ctx, "tsk_1"))
        self.assertEqual(report.requests, 2)
        self.assertEqual(report.tokens_in, 0)   # 未知 = 0，绝不编造（D-060）
        self.assertEqual(report.tokens_out, 0)

    def test_capabilities_and_health_never_fail(self):
        caps = unwrap(self.provider.capabilities())
        self.assertTrue(caps.supports_chat)
        self.assertTrue(caps.supports_search)
        health = unwrap(self.provider.health(self.ctx))
        self.assertTrue(health.reachable)


class TestRecordingProvider(unittest.TestCase):
    def test_records_every_call(self):
        ctx = new_call_context(project_id="prj_test", actor="test", task_id="tsk_1")
        inner = FakeProvider(fixtures={"chat:a": ChatResponse(text="A")})
        provider = RecordingProvider(inner)
        provider.chat(ctx, _chat_request(key="a"))
        provider.chat(ctx, _chat_request(key="a"))
        self.assertEqual(len(provider.call_log), 2)
        self.assertTrue(provider.call_log[0].ok)

    def test_records_failures_too(self):
        ctx = new_call_context(project_id="prj_test", actor="test")
        provider = RecordingProvider(FailingProvider({"chat": ErrorCode.EXTERNAL_RATE_LIMIT}))
        result = provider.chat(ctx, _chat_request())
        self.assertTrue(is_err(result))
        self.assertFalse(provider.call_log[0].ok)
        self.assertEqual(provider.call_log[0].error_code, str(ErrorCode.EXTERNAL_RATE_LIMIT))


class TestFailingProvider(unittest.TestCase):
    def test_injects_given_error_code(self):
        ctx = new_call_context(project_id="prj_test", actor="test")
        provider = FailingProvider({"chat": ErrorCode.EXTERNAL_AUTH})
        result = provider.chat(ctx, _chat_request())
        self.assertTrue(is_err(result))
        self.assertFalse(result.error.retryable)   # 鉴权失败不该重试

    def test_budget_exceeded_is_recoverable(self):
        ctx = new_call_context(project_id="prj_test", actor="test")
        provider = FailingProvider({"chat": ErrorCode.BUDGET_EXCEEDED})
        result = provider.chat(ctx, _chat_request())
        self.assertTrue(is_err(result))
        self.assertTrue(result.error.recoverable)  # 可续跑（D-043）


class TestOfflineGuarantee(unittest.TestCase):
    def test_network_is_blocked_during_provider_calls(self):
        ctx = new_call_context(project_id="prj_test", actor="test", task_id="tsk_1")
        provider = OfflineGuardProvider(FakeProvider(fixtures={"chat:off": ChatResponse(text="ok")}))
        result = provider.chat(ctx, _chat_request(key="off"))
        self.assertTrue(is_ok(result))

    def test_blocked_socket_raises(self):
        import socket

        with network_blocked():
            with self.assertRaises(OfflineViolation):
                socket.create_connection(("example.invalid", 80))

    def test_socket_restored_after_block(self):
        import socket

        before = socket.create_connection
        with network_blocked():
            self.assertIsNot(socket.create_connection, before)
        self.assertIs(socket.create_connection, before)


class TestRegistry(unittest.TestCase):
    def test_default_registry_names(self):
        registry = default_registry()
        # P4.2 起注册表新增真实 Provider "tavily"（仅注册、不被离线护栏包裹）
        self.assertEqual(registry.names(), ["failing", "fake", "recording", "tavily"])

    def test_get_unknown_name_fails(self):
        registry = default_registry()
        self.assertTrue(is_err(registry.get("openai")))

    def test_fake_from_registry_is_offline_guarded(self):
        registry = default_registry(fixtures={"chat:reg": ChatResponse(text="from registry")})
        provider = unwrap(registry.get("fake"))
        ctx = new_call_context(project_id="prj_test", actor="test", task_id="tsk_1")
        self.assertTrue(is_ok(provider.chat(ctx, _chat_request(key="reg"))))


if __name__ == "__main__":
    unittest.main()
