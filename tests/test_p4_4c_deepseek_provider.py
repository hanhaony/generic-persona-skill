"""P4.4-C：DeepSeek Chat Provider 的离线测试（**全程零联网、零真实请求、零费用**）。

对应用户第八步的测试计划（九组）。所有"模型响应"都是本地构造的 JSON 字符串，
网络出口 `DeepSeekProvider._urlopen` 整类替换成 `StubUrlopen`；本文件**不会**：
发起任何真实 HTTP 请求、读取任何真实密钥、打印任何密钥值。

素材沿用既有的**合成虚构**命名（阿雾 / 雾港 / 星灯，AC-16）；密钥一律是**假占位值**。

九组：
  1. 请求构造（URL / 鉴权头 / model / max_tokens / stream / thinking / metadata 不外发）
  2. `response_format` 映射（json_schema→json_object；text 不发；缺 "json" → Err）
  3. 响应归一化（text / finish_reason / model_id / raw_ref / structured）
  4. 用量映射（只报 token，**金额恒为 None**；cache 字段不污染 tokens_in）
  5. 错误映射（401/403/429/5xx/超时/非法 JSON/choices 空）
  6. 密钥安全（不进 body / 不进异常 / 不进 repr / health 不联网）
  7. 账本边界（**Provider 不记账**；端到端由抽取器记 CONFIRMED + OFFICIAL_RATE）
  8. 能力与协议（chat=True / search=False / cost_model=per-token / Provider 协议）
  9. 装配接线（model_id 来自配置、max_retries 强制 0、无密钥拒绝构造、notes 已改陈述句）
"""

from __future__ import annotations

import io
import json
import unittest
import urllib.error

from persona.config import DEFAULT_MODEL_ID, load_settings
from persona.core.context import new_call_context
from persona.core.errors import ErrorCode
from persona.core.result import error_of, is_err, is_ok, unwrap
from persona.core.types import OP_CHAT, UsageReport
from persona.core.usage_ledger import (
    COST_CONFIRMED,
    COST_UNKNOWN,
    OUTCOME_FAILED,
    SOURCE_OFFICIAL_RATE,
    ExternalUsageLedger,
    ModelCallLimits,
    make_idempotency_key,
    model_call_key_parts,
)
from persona.modules.extraction import (
    PROMPT_VERSION,
    ProviderClaimExtractor,
)
from persona.orchestrator import (
    DRY_RUN_SAMPLE_TARGETS,
    chat_provider_config,
    chat_provider_from_settings,
    claim_extractor_from_settings,
    model_plan,
    sample_materials,
)
from persona.providers.base import (
    ChatRequest,
    Message,
    Provider,
    ProviderConfig,
    SearchRequest,
)
from persona.providers.deepseek import (
    CHAT_PATH,
    DEFAULT_BASE_URL,
    PROVIDER_NAME,
    DeepSeekProvider,
)
from persona.providers.registry import default_registry

PROJECT = "prj_p44c"

# **假**密钥占位值：仅用于验证"不会被打印、不会进 body"。不是任何真实凭据。
FAKE_KEY = "sk-not-a-real-deepseek-key-0001"

# 与 .env.example 一致的 P4.4-C 拍板配置（合成材料、合成目标）
MODEL_ENV = {
    "PERSONA_MODEL_PROVIDER": "deepseek",
    "PERSONA_MODEL_ID": "deepseek-flash",
    "PERSONA_MODEL_CURRENCY": "USD",
    "PERSONA_MODEL_PRICE_TIER": "peak",
    "PERSONA_MODEL_PRICE_IN_PER_1M": "0.3",
    "PERSONA_MODEL_PRICE_OUT_PER_1M": "1.2",
    "PERSONA_MODEL_PRICE_SOURCE": "https://api-docs.deepseek.com/quick_start/pricing",
    "PERSONA_MODEL_PRICE_CHECKED_AT": "2026-10-16",
    "PERSONA_MODEL_MAX_COST": "0.25",
    "PERSONA_MODEL_MAX_COST_PER_TASK": "0.25",
    "PERSONA_MODEL_MAX_CALLS": "1",
    "PERSONA_MODEL_EST_TOKENS_IN": "3000",
    "PERSONA_MODEL_EST_TOKENS_OUT": "2000",
    "PERSONA_MODEL_REQUIRE_GATE_VERDICT": "true",
    "PERSONA_OFFLINE_ONLY": "false",
    "DEEPSEEK_API_KEY": FAKE_KEY,
}


def settings_for(**overrides):
    env = dict(MODEL_ENV)
    env.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
    result = load_settings(use_dotenv=False, env=env)
    if is_err(result):
        raise AssertionError(f"测试配置本身不合法：{error_of(result).message}")
    return unwrap(result)


# --------------------------------------------------------------------------
# 网络出口替身：可脚本化，绝不真连网
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class StubUrlopen:
    """替换 `DeepSeekProvider._urlopen`：记录请求并返回预设 JSON 或抛错。

    - `calls` 记录 `(url, payload, headers, timeout)`，用于验证鉴权头、请求体、
      模型名、`max_tokens`、`stream`、`thinking`、以及"调用了几次"；
    - `body`：直接给定的响应 JSON 文本；`data`：会 `json.dumps` 的字典；
    - `error`：调用时抛出（HTTPError / URLError）；
    - `invalid_json=True`：返回无法解析的响应体。
    """

    def __init__(self, data=None, *, body=None, error: BaseException | None = None,
                 invalid_json: bool = False):
        self.data = data
        self.body = body
        self.error = error
        self.invalid_json = invalid_json
        self.calls: list[tuple[str, dict, dict, float]] = []

    def __call__(self, req, timeout=None):
        payload = json.loads(req.data.decode("utf-8")) if req.data else {}
        headers = dict(getattr(req, "headers", None) or {})
        self.calls.append((req.full_url, payload, headers, timeout))
        if self.error is not None:
            raise self.error
        if self.invalid_json:
            return _FakeResponse(b"this-is-not-json")
        if self.body is not None:
            return _FakeResponse(self.body.encode("utf-8"))
        return _FakeResponse(json.dumps(self.data or {}).encode("utf-8"))

    # ---- 断言辅助 ----

    def only_call(self):
        if len(self.calls) != 1:
            raise AssertionError(f"期望恰好 1 次调用，实际 {len(self.calls)}")
        return self.calls[0]


def header_of(headers: dict, name: str) -> str | None:
    """urllib 会把头名规范化（`Authorization` / `Content-type`），故大小写不敏感取值。"""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def http_error(code: int, reason: str = "err") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.deepseek.test" + CHAT_PATH, code, reason, None, io.BytesIO(b"{}"))


def ds_response(content: str, *, finish_reason: str = "stop",
                model: str = "deepseek-flash", response_id: str = "chatcmpl-test-1",
                usage: dict | None = None) -> dict:
    """构造一份 DeepSeek 形状的响应体（合成，非真实响应）。"""
    body: dict = {
        "id": response_id,
        "object": "chat.completion",
        "model": model,
        "choices": [{
            "index": 0,
            "finish_reason": finish_reason,
            "message": {"role": "assistant", "content": content},
        }],
    }
    if usage is not None:
        body["usage"] = usage
    return body


def provider_with(stub: StubUrlopen, **kwargs) -> DeepSeekProvider:
    provider = DeepSeekProvider(
        FAKE_KEY,
        config=kwargs.pop("config", ProviderConfig(max_retries=0)),
        model_id=kwargs.pop("model_id", DEFAULT_MODEL_ID),
        **kwargs,
    )
    provider._urlopen = stub
    return provider


def chat_request(*, fmt: str = "json_schema", text: str = "请只输出 JSON 结构。",
                 model_hint: str | None = "deepseek-flash",
                 max_output_tokens: int = 4096,
                 temperature: float = 0.7,
                 stop: list[str] | None = None) -> ChatRequest:
    return ChatRequest(
        messages=[Message(role="system", content=text),
                  Message(role="user", content="材料：阿雾在雾港灯塔值守。")],
        model_hint=model_hint,
        response_format=fmt,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        stop=list(stop or []),
        metadata={"prompt_version": PROMPT_VERSION},
    )


def ctx():
    return new_call_context(project_id=PROJECT, actor="tests")


# ==========================================================================
# 一、请求构造
# ==========================================================================


class TestRequestBuilding(unittest.TestCase):
    def setUp(self):
        self.stub = StubUrlopen(ds_response("{}", usage={"prompt_tokens": 10,
                                                         "completion_tokens": 2}))
        self.provider = provider_with(self.stub)
        self.result = self.provider.chat(ctx(), chat_request())
        self.assertTrue(is_ok(self.result), error_of(self.result))

    def test_posts_to_chat_completions(self):
        url, _, _, _ = self.stub.only_call()
        self.assertEqual(url, DEFAULT_BASE_URL + CHAT_PATH)
        self.assertTrue(url.endswith("/chat/completions"))

    def test_authorization_bearer_header(self):
        _, _, headers, _ = self.stub.only_call()
        self.assertEqual(header_of(headers, "Authorization"), f"Bearer {FAKE_KEY}")

    def test_key_is_not_in_request_body(self):
        _, payload, _, _ = self.stub.only_call()
        self.assertNotIn(FAKE_KEY, json.dumps(payload, ensure_ascii=False))

    def test_model_comes_from_model_hint(self):
        _, payload, _, _ = self.stub.only_call()
        self.assertEqual(payload["model"], "deepseek-flash")

    def test_max_output_tokens_mapped_to_max_tokens(self):
        _, payload, _, _ = self.stub.only_call()
        self.assertEqual(payload["max_tokens"], 4096)
        # DeepSeek **没有** max_completion_tokens，不得凭空发送
        self.assertNotIn("max_completion_tokens", payload)

    def test_stream_is_explicitly_false(self):
        _, payload, _, _ = self.stub.only_call()
        self.assertIs(payload["stream"], False)

    def test_thinking_is_explicitly_disabled(self):
        """D1：显式关闭思考模式（官方默认可能是 enabled）。"""
        _, payload, _, _ = self.stub.only_call()
        self.assertEqual(payload["thinking"], {"type": "disabled"})

    def test_metadata_is_not_sent(self):
        """`metadata` 含 prompt_version 等内部审计信息，不外发。"""
        _, payload, _, _ = self.stub.only_call()
        self.assertNotIn("metadata", payload)
        self.assertNotIn(PROMPT_VERSION, json.dumps(payload, ensure_ascii=False))

    def test_temperature_is_passed_through(self):
        """D2：显式发 0.7，覆盖 DeepSeek 的默认 1。"""
        _, payload, _, _ = self.stub.only_call()
        self.assertEqual(payload["temperature"], 0.7)

    def test_stop_is_omitted_when_empty(self):
        _, payload, _, _ = self.stub.only_call()
        self.assertNotIn("stop", payload)

    def test_stop_is_sent_when_given(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        provider.chat(ctx(), chat_request(stop=["###", "\n\n"]))
        _, payload, _, _ = stub.only_call()
        self.assertEqual(payload["stop"], ["###", "\n\n"])

    def test_timeout_comes_from_provider_config(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub, config=ProviderConfig(max_retries=0,
                                                             per_call_timeout_ms=15_000))
        provider.chat(ctx(), chat_request())
        _, _, _, timeout = stub.only_call()
        self.assertAlmostEqual(timeout, 15.0, places=3)

    def test_empty_messages_is_rejected_before_sending(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        result = provider.chat(ctx(), ChatRequest(messages=[]))
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.VALIDATION_REQUIRED_FIELD)
        self.assertEqual(stub.calls, [])          # 请求**没有**发出去

    def test_unknown_role_is_rejected(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        req = ChatRequest(messages=[Message(role="tool", content="x")])
        result = provider.chat(ctx(), req)
        self.assertTrue(is_err(result))
        self.assertEqual(stub.calls, [])

    def test_missing_model_is_rejected(self):
        """既无 model_hint 也无装配层注入的模型 ID → 不知道调哪个模型。"""
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub, model_id="")
        result = provider.chat(ctx(), chat_request(model_hint=""))
        self.assertTrue(is_err(result))
        self.assertEqual(stub.calls, [])


# ==========================================================================
# 二、`response_format` 映射
# ==========================================================================


class TestResponseFormatMapping(unittest.TestCase):
    def test_json_schema_maps_to_json_object(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        provider.chat(ctx(), chat_request(fmt="json_schema"))
        _, payload, _, _ = stub.only_call()
        self.assertEqual(payload["response_format"], {"type": "json_object"})

    def test_text_format_sends_no_response_format_field(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        provider.chat(ctx(), chat_request(fmt="text", text="随便聊聊，不需要结构。"))
        _, payload, _, _ = stub.only_call()
        self.assertNotIn("response_format", payload)

    def test_uppercase_json_keyword_passes(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request(text="请输出 JSON Schema 定义的结构。"))
        self.assertTrue(is_ok(result), error_of(result))
        self.assertEqual(len(stub.calls), 1)

    def test_lowercase_json_keyword_passes(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request(text="返回一段 json。"))
        self.assertTrue(is_ok(result), error_of(result))
        self.assertEqual(len(stub.calls), 1)

    def test_missing_json_keyword_is_a_hard_rejection(self):
        """D4：缺 "json" → **拒绝发送**，绝不静默往调用方提示词里补字。"""
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request(text="请输出结构化结果，用键值对。"))
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.EXTERNAL_UNAVAILABLE)
        self.assertIn("json", error_of(result).message.lower())
        self.assertEqual(stub.calls, [])

    def test_unknown_response_format_is_rejected(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request(fmt="xml"))
        self.assertTrue(is_err(result))
        self.assertEqual(stub.calls, [])


# ==========================================================================
# 三、响应归一化
# ==========================================================================


class TestResponseNormalization(unittest.TestCase):
    def _chat(self, body: dict):
        stub = StubUrlopen(body)
        provider = provider_with(stub)
        return provider.chat(ctx(), chat_request())

    def test_text_is_taken_from_choices_message(self):
        result = self._chat(ds_response("你好"))
        self.assertTrue(is_ok(result))
        self.assertEqual(unwrap(result).text, "你好")

    def test_finish_reason_is_passed_through(self):
        """DeepSeek **没有** refusal；拒答走 content_filter（claim_validation 认这条）。"""
        result = self._chat(ds_response("", finish_reason="content_filter"))
        self.assertTrue(is_ok(result))
        self.assertEqual(unwrap(result).finish_reason, "content_filter")

    def test_model_id_is_passed_through(self):
        result = self._chat(ds_response("x", model="deepseek-flash-2026"))
        self.assertEqual(unwrap(result).model_id, "deepseek-flash-2026")

    def test_raw_ref_carries_response_id(self):
        result = self._chat(ds_response("x", response_id="chatcmpl-abc123"))
        self.assertEqual(unwrap(result).raw_ref, "chatcmpl-abc123")

    def test_structured_is_filled_when_text_is_a_json_object(self):
        result = self._chat(ds_response('{"claims": []}'))
        self.assertEqual(unwrap(result).structured, {"claims": []})

    def test_structured_is_none_and_no_error_when_text_is_not_json(self):
        """解析口径归 `claim_validation`：这里解析失败**不报错**。"""
        result = self._chat(ds_response("这不是 JSON，但 Provider 不越权判断。"))
        self.assertTrue(is_ok(result))
        self.assertIsNone(unwrap(result).structured)
        self.assertEqual(unwrap(result).text, "这不是 JSON，但 Provider 不越权判断。")

    def test_structured_is_none_for_json_array(self):
        """顶层是数组不算 dict → 不填 `structured`（也不算错）。"""
        result = self._chat(ds_response("[1, 2, 3]"))
        self.assertTrue(is_ok(result))
        self.assertIsNone(unwrap(result).structured)

    def test_null_content_becomes_empty_text(self):
        body = ds_response("")
        body["choices"][0]["message"]["content"] = None
        result = self._chat(body)
        self.assertTrue(is_ok(result))
        self.assertEqual(unwrap(result).text, "")


# ==========================================================================
# 四、用量映射（**只报 token，绝不报金额**）
# ==========================================================================


class TestUsageMapping(unittest.TestCase):
    def _usage_of(self, usage: dict | None):
        stub = StubUrlopen(ds_response("{}", usage=usage))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request())
        self.assertTrue(is_ok(result), error_of(result))
        return unwrap(result)

    def test_tokens_taken_directly(self):
        response = self._usage_of({"prompt_tokens": 1234, "completion_tokens": 567,
                                   "total_tokens": 1801})
        self.assertEqual(response.usage.tokens_in, 1234)
        self.assertEqual(response.usage.tokens_out, 567)

    def test_missing_usage_leaves_tokens_none(self):
        """缺 usage → 未知，**不填 0**（D-060）。"""
        response = self._usage_of(None)
        self.assertIsNone(response.usage.tokens_in)
        self.assertIsNone(response.usage.tokens_out)

    def test_requests_is_one(self):
        self.assertEqual(self._usage_of({"prompt_tokens": 1,
                                         "completion_tokens": 1}).usage.requests, 1)

    def test_cost_confirmed_is_always_none(self):
        """D3：Provider 只报 token，金额由装配层按配置算——绝不冒充"官方回报金额"。"""
        for usage in ({"prompt_tokens": 10, "completion_tokens": 5}, None):
            with self.subTest(usage=usage):
                response = self._usage_of(usage)
                self.assertIsNone(response.usage.cost_confirmed)
                self.assertIsNone(response.usage.cost_estimate)

    def test_cache_fields_do_not_pollute_tokens_in(self):
        """D5：cache hit/miss 无承载字段 → 统一按 miss 保守口径；tokens_in 取 prompt_tokens。"""
        response = self._usage_of({
            "prompt_tokens": 2000,
            "prompt_cache_hit_tokens": 1500,
            "prompt_cache_miss_tokens": 500,
            "completion_tokens": 100,
        })
        self.assertEqual(response.usage.tokens_in, 2000)

    def test_envelope_usage_matches_response_usage(self):
        """`Ok.usage` 与 `ChatResponse.usage` 同源（抽取器两条路径都能读到）。"""
        stub = StubUrlopen(ds_response("{}", usage={"prompt_tokens": 7,
                                                    "completion_tokens": 3}))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request())
        self.assertEqual(result.usage.tokens_in, unwrap(result).usage.tokens_in)

    def test_response_id_fills_usage_request_id(self):
        """服务端 `id` 要进 `UsageDelta.request_id`（账本逐笔对账/报障的唯一标识）。

        回归护栏：此前 `_usage_delta` 把它恒填 `None`，于是账本里每笔模型费用的
        服务端凭据都是空的——而 `raw_ref` 只是通用字段，抽取器不该反过来假设它等
        同 request_id。标识必须由最清楚它的 Provider 自己填。
        """
        stub = StubUrlopen(ds_response("{}", response_id="chatcmpl-req-42",
                                       usage={"prompt_tokens": 10,
                                              "completion_tokens": 2}))
        provider = provider_with(stub)
        response = unwrap(provider.chat(ctx(), chat_request()))
        self.assertEqual(response.usage.request_id, "chatcmpl-req-42")
        self.assertEqual(response.raw_ref, "chatcmpl-req-42")

    def test_missing_response_id_leaves_request_id_none(self):
        """响应没有 `id` → `None`（**不填 ""**：D-060，未知不是空串）。"""
        body = ds_response("{}", usage={"prompt_tokens": 10, "completion_tokens": 2})
        body.pop("id")
        stub = StubUrlopen(body=json.dumps(body))
        provider = provider_with(stub)
        response = unwrap(provider.chat(ctx(), chat_request()))
        self.assertIsNone(response.usage.request_id)

    def test_request_id_survives_missing_usage(self):
        """`usage` 缺失时 token 未知，但服务端标识仍要留住（两者互不牵连）。"""
        stub = StubUrlopen(ds_response("{}", response_id="chatcmpl-only-id",
                                       usage=None))
        provider = provider_with(stub)
        response = unwrap(provider.chat(ctx(), chat_request()))
        self.assertIsNone(response.usage.tokens_in)
        self.assertEqual(response.usage.request_id, "chatcmpl-only-id")

    def test_body_counters_are_not_ledger_records(self):
        """`usage()` 报的是本对象调用次数，**不是**外部分账本。"""
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        provider.chat(ctx(), chat_request())
        report = unwrap(provider.usage(ctx(), "session"))
        self.assertIsInstance(report, UsageReport)
        self.assertEqual(report.requests, 1)


# ==========================================================================
# 五、错误映射
# ==========================================================================


class TestErrorMapping(unittest.TestCase):
    def _err_code(self, stub: StubUrlopen):
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request())
        self.assertTrue(is_err(result))
        return error_of(result).code

    def test_401_maps_to_auth(self):
        self.assertEqual(self._err_code(StubUrlopen(error=http_error(401))),
                         ErrorCode.EXTERNAL_AUTH)

    def test_403_maps_to_auth(self):
        self.assertEqual(self._err_code(StubUrlopen(error=http_error(403))),
                         ErrorCode.EXTERNAL_AUTH)

    def test_429_maps_to_rate_limit(self):
        self.assertEqual(self._err_code(StubUrlopen(error=http_error(429))),
                         ErrorCode.EXTERNAL_RATE_LIMIT)

    def test_500_maps_to_unavailable(self):
        self.assertEqual(self._err_code(StubUrlopen(error=http_error(500))),
                         ErrorCode.EXTERNAL_UNAVAILABLE)

    def test_503_maps_to_unavailable(self):
        self.assertEqual(self._err_code(StubUrlopen(error=http_error(503))),
                         ErrorCode.EXTERNAL_UNAVAILABLE)

    def test_400_maps_to_unavailable(self):
        self.assertEqual(self._err_code(StubUrlopen(error=http_error(400))),
                         ErrorCode.EXTERNAL_UNAVAILABLE)

    def test_network_error_maps_to_timeout(self):
        self.assertEqual(
            self._err_code(StubUrlopen(error=urllib.error.URLError("boom"))),
            ErrorCode.EXTERNAL_TIMEOUT)

    def test_socket_timeout_maps_to_timeout(self):
        self.assertEqual(self._err_code(StubUrlopen(error=TimeoutError("slow"))),
                         ErrorCode.EXTERNAL_TIMEOUT)

    def test_invalid_json_maps_to_response_invalid(self):
        self.assertEqual(self._err_code(StubUrlopen(invalid_json=True)),
                         ErrorCode.EXTERNAL_RESPONSE_INVALID)

    def test_top_level_not_object_maps_to_response_invalid(self):
        self.assertEqual(self._err_code(StubUrlopen(body="[1, 2]")),
                         ErrorCode.EXTERNAL_RESPONSE_INVALID)

    def test_empty_choices_maps_to_response_invalid(self):
        self.assertEqual(self._err_code(StubUrlopen({"choices": []})),
                         ErrorCode.EXTERNAL_RESPONSE_INVALID)

    def test_missing_choices_maps_to_response_invalid(self):
        self.assertEqual(self._err_code(StubUrlopen({"id": "x"})),
                         ErrorCode.EXTERNAL_RESPONSE_INVALID)

    def test_default_provider_config_does_not_retry(self):
        """默认（未注入 config）也必须 max_retries=0：重试 = 再花一次钱。"""
        stub = StubUrlopen(error=http_error(503))
        provider = DeepSeekProvider(FAKE_KEY, model_id=DEFAULT_MODEL_ID)
        provider._urlopen = stub
        result = provider.chat(ctx(), chat_request())
        self.assertTrue(is_err(result))
        self.assertEqual(len(stub.calls), 1, "默认不得重试")


# ==========================================================================
# 六、密钥安全
# ==========================================================================


class TestSecretSafety(unittest.TestCase):
    def test_key_not_in_body(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        provider.chat(ctx(), chat_request())
        _, payload, headers, _ = stub.only_call()
        self.assertNotIn(FAKE_KEY, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(FAKE_KEY, json.dumps(list(headers.keys())))

    def test_key_not_in_error_message(self):
        stub = StubUrlopen(error=http_error(401))
        provider = provider_with(stub)
        result = provider.chat(ctx(), chat_request())
        self.assertTrue(is_err(result))
        self.assertNotIn(FAKE_KEY, error_of(result).message)
        self.assertNotIn(FAKE_KEY, str(error_of(result)))

    def test_key_not_in_repr(self):
        provider = provider_with(StubUrlopen(ds_response("{}")))
        self.assertNotIn(FAKE_KEY, repr(provider))
        self.assertNotIn(FAKE_KEY, str(provider))

    def test_health_does_not_touch_the_network(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = provider_with(stub)
        result = provider.health(ctx())
        self.assertTrue(is_ok(result))
        self.assertTrue(unwrap(result).reachable)
        self.assertEqual(stub.calls, [])

    def test_health_degrades_without_key(self):
        stub = StubUrlopen(ds_response("{}"))
        provider = DeepSeekProvider("", config=ProviderConfig(max_retries=0))
        provider._urlopen = stub
        status = unwrap(provider.health(ctx()))
        self.assertFalse(status.reachable)
        self.assertTrue(status.degraded)
        self.assertEqual(status.last_error_code,
                         str(ErrorCode.VALIDATION_REQUIRED_FIELD))

    def test_assembly_rejects_missing_key(self):
        """无密钥 → 拒绝构造（不构造一个必然 401 的对象）。

        密钥缺失时**配置校验**就会拒绝加载，所以这里用 `replace` 直接造一份
        "通过了配置、但密钥为空"的配置，专门验证**装配层自己的守卫**。
        """
        from dataclasses import replace

        from persona.config.spec import Secrets

        settings = settings_for()
        settings = replace(settings, secrets=Secrets())
        result = chat_provider_from_settings(settings)
        self.assertTrue(is_err(result))
        self.assertIn("未设置", error_of(result).message)


# ==========================================================================
# 七、账本边界（**Provider 不记账**）
# ==========================================================================


class TestLedgerBoundary(unittest.TestCase):
    def test_provider_alone_never_writes_to_a_ledger(self):
        """D3：Provider 调用后，账本必须是空的（它根本拿不到账本）。"""
        ledger = ExternalUsageLedger()
        stub = StubUrlopen(ds_response("{}", usage={"prompt_tokens": 500,
                                                    "completion_tokens": 100}))
        provider = provider_with(stub)
        provider.chat(ctx(), chat_request())          # 直调 Provider
        self.assertEqual(ledger.records(), [])
        self.assertEqual(ledger.occupied_cost(), 0.0)

    def _limits(self) -> ModelCallLimits:
        return ModelCallLimits(
            max_calls=2,
            max_cost=0.25,
            price_per_1k_in=0.0003,      # $0.3 / 1M
            price_per_1k_out=0.0012,     # $1.2 / 1M
            currency="USD",
            est_tokens_in=1000,
            est_tokens_out=500,
        )

    @staticmethod
    def _model_payload() -> str:
        return json.dumps({"claims": [{
            "key": "c1",
            "material_key": "dryrun:sample",
            "claim": "阿雾在雾港灯塔值守",
            "tier": "VERBATIM",
            "dimension": "BACKGROUND",
            "subject_entity_ids": ["pe_dryrun_awu"],
            "confidence": 0.9,
            "verbatim_quote": "阿雾在雾港灯塔值守",
        }]}, ensure_ascii=False)

    def _extractor_with(self, stub: StubUrlopen):
        provider = provider_with(stub)
        ledger = ExternalUsageLedger()
        extractor = ProviderClaimExtractor(
            provider,
            model_hint=DEFAULT_MODEL_ID,
            limits=self._limits(),
            ledger=ledger,
            require_gate_verdict=True,
        )
        return extractor, ledger

    def test_end_to_end_confirms_cost_from_official_rate(self):
        """端到端：真 Provider（桩网络）+ 真抽取器 + 真账本。"""
        stub = StubUrlopen(ds_response(
            self._model_payload(),
            usage={"prompt_tokens": 1200, "completion_tokens": 300,
                   "total_tokens": 1500}))
        extractor, ledger = self._extractor_with(stub)
        outcome = unwrap(extractor.extract(
            ctx(), sample_materials(), targets=list(DRY_RUN_SAMPLE_TARGETS)))

        self.assertEqual(len(outcome.claims), 1)
        self.assertEqual(outcome.claims[0].claim, "阿雾在雾港灯塔值守")
        records = ledger.records()
        self.assertEqual(len(records), 1, "抽取器只记一笔（Provider 不记）")
        record = records[0]
        self.assertEqual(record.provider, "deepseek")
        self.assertEqual(record.operation, OP_CHAT)
        # 1200/1000×0.0003 + 300/1000×0.0012 = 0.00036 + 0.00036
        self.assertAlmostEqual(record.cost_confirmed, 0.00072, places=9)
        self.assertEqual(record.cost_status, COST_CONFIRMED)
        # DeepSeek 只回报 token、不回报金额 → 语义准确的来源是"按官方费率推算"
        self.assertEqual(record.cost_source, SOURCE_OFFICIAL_RATE)

    def test_ledger_record_carries_server_request_id(self):
        """端到端：账本记录要有服务端 `id`（此前恒为 `None` → 逐笔对账缺凭据）。"""
        stub = StubUrlopen(ds_response(
            self._model_payload(), response_id="chatcmpl-ledger-7",
            usage={"prompt_tokens": 1200, "completion_tokens": 300}))
        extractor, ledger = self._extractor_with(stub)
        unwrap(extractor.extract(ctx(), sample_materials(),
                                 targets=list(DRY_RUN_SAMPLE_TARGETS)))
        record = ledger.records()[0]
        self.assertEqual(record.request_id, "chatcmpl-ledger-7")

    def test_idempotency_key_uses_deepseek_provider_name(self):
        stub = StubUrlopen(ds_response(
            self._model_payload(),
            usage={"prompt_tokens": 1200, "completion_tokens": 300}))
        extractor, ledger = self._extractor_with(stub)
        unwrap(extractor.extract(ctx(), sample_materials(),
                                 targets=list(DRY_RUN_SAMPLE_TARGETS)))
        expected = make_idempotency_key(
            PROVIDER_NAME, OP_CHAT,
            *model_call_key_parts(model=DEFAULT_MODEL_ID,
                                  material_keys=["dryrun:sample"],
                                  target_ids=["pe_dryrun_awu"],
                                  prompt_version=PROMPT_VERSION))
        self.assertEqual(ledger.records()[0].idempotency_key, expected)

    def test_failure_path_records_unknown_not_zero(self):
        """失败 ≠ 免费：超时后抽取器记 `UNKNOWN`，且**绝不**记 0。"""
        stub = StubUrlopen(error=urllib.error.URLError("boom"))
        extractor, ledger = self._extractor_with(stub)
        result = extractor.extract(ctx(), sample_materials(),
                                   targets=list(DRY_RUN_SAMPLE_TARGETS))
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.EXTERNAL_TIMEOUT)
        records = ledger.records()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].outcome, OUTCOME_FAILED)
        self.assertEqual(records[0].cost_status, COST_UNKNOWN)
        self.assertIsNone(records[0].cost_confirmed)
        # 未知占位仍在占用额度（预占不释放）
        self.assertGreater(ledger.occupied_cost(), 0.0)

    def test_duplicate_call_is_not_sent_twice(self):
        """同一意图第二次 → 直接跳过，**不再花钱**。"""
        stub = StubUrlopen(ds_response(self._model_payload(),
                                       usage={"prompt_tokens": 10,
                                              "completion_tokens": 2}))
        extractor, ledger = self._extractor_with(stub)
        first = unwrap(extractor.extract(ctx(), sample_materials(),
                                         targets=list(DRY_RUN_SAMPLE_TARGETS)))
        self.assertTrue(first.claims)
        second = unwrap(extractor.extract(ctx(), sample_materials(),
                                          targets=list(DRY_RUN_SAMPLE_TARGETS)))
        self.assertEqual(second.claims, [])
        self.assertEqual(len(stub.calls), 1, "重复意图不得再发一次")

    def test_assembly_shares_one_ledger_shape(self):
        """装配层：抽取器持账本，Provider 不持账本（同一本账只挂在抽取器上）。"""
        settings = settings_for()
        provider = unwrap(chat_provider_from_settings(settings))
        ledger = ExternalUsageLedger()
        extractor = claim_extractor_from_settings(settings, provider, ledger=ledger)
        self.assertIs(extractor.ledger, ledger)
        self.assertFalse(hasattr(provider, "usage_ledger"))


# ==========================================================================
# 八、能力与协议
# ==========================================================================


class TestCapabilitiesAndProtocol(unittest.TestCase):
    def setUp(self):
        self.provider = provider_with(StubUrlopen(ds_response("{}")))

    def test_capabilities_declare_chat_only(self):
        caps = unwrap(self.provider.capabilities())
        self.assertEqual(caps.provider_name, "deepseek")
        self.assertTrue(caps.supports_chat)
        self.assertFalse(caps.supports_search)
        self.assertFalse(caps.supports_extract)
        self.assertTrue(caps.supports_structured_output)
        self.assertEqual(caps.cost_model, "per-token")

    def test_search_is_refused(self):
        result = self.provider.search(ctx(), SearchRequest(query="阿雾"))
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.EXTERNAL_UNAVAILABLE)

    def test_usage_reports_zero_before_any_call(self):
        report = unwrap(self.provider.usage(ctx(), "session"))
        self.assertEqual(report.requests, 0)

    def test_satisfies_provider_protocol(self):
        self.assertIsInstance(self.provider, Provider)

    def test_registry_builds_deepseek_with_injected_secret(self):
        from persona.config.spec import Secrets

        registry = default_registry()
        built = unwrap(registry.get(
            "deepseek",
            ProviderConfig(max_retries=0),
            Secrets(provider_api_key=FAKE_KEY),
        ))
        self.assertIsInstance(built, DeepSeekProvider)
        self.assertEqual(built.api_key, FAKE_KEY)

    def test_registry_does_not_fall_back_to_search_key(self):
        """搜索密钥不能当模型密钥用（那只会得到 401，且掩盖配置错误）。"""
        from persona.config.spec import Secrets

        registry = default_registry()
        built = unwrap(registry.get(
            "deepseek",
            ProviderConfig(max_retries=0),
            Secrets(search_api_key=FAKE_KEY),
        ))
        self.assertEqual(built.api_key, "")


# ==========================================================================
# 九、装配接线
# ==========================================================================


class TestAssemblyWiring(unittest.TestCase):
    def test_assembly_uses_configured_model_id(self):
        settings = settings_for()
        provider = unwrap(chat_provider_from_settings(settings))
        self.assertEqual(provider.model_id, DEFAULT_MODEL_ID)
        # 请求没给 model_hint 时，回退到装配层注入的模型 ID
        stub = StubUrlopen(ds_response("{}"))
        provider._urlopen = stub
        provider.chat(ctx(), chat_request(model_hint=""))
        _, payload, _, _ = stub.only_call()
        self.assertEqual(payload["model"], DEFAULT_MODEL_ID)

    def test_assembly_forces_max_retries_zero(self):
        settings = settings_for()
        provider = unwrap(chat_provider_from_settings(settings))
        self.assertEqual(provider.config.max_retries, 0)
        self.assertEqual(chat_provider_config(settings).max_retries, 0)

    def test_assembly_keeps_configured_timeout(self):
        settings = settings_for()
        config = chat_provider_config(settings)
        self.assertEqual(config.per_call_timeout_ms, settings.retry.per_call_timeout_ms)

    def test_assembly_rejects_disabled_model(self):
        settings = settings_for(PERSONA_MODEL_PROVIDER=None)
        result = chat_provider_from_settings(settings)
        self.assertTrue(is_err(result))
        self.assertIn("未启用", error_of(result).message)

    def test_assembly_rejects_unknown_provider_name(self):
        """用 `replace` 直接构造未知服务商（绕开配置校验，验证装配层自己的守卫）。"""
        from dataclasses import replace

        settings = settings_for()
        settings = replace(settings, model=replace(settings.model, provider="openai"))
        result = chat_provider_from_settings(settings)
        self.assertTrue(is_err(result))
        self.assertIn("不认识", error_of(result).message)

    def test_plan_notes_now_describe_the_implemented_mapping(self):
        """过时 notes 已改成陈述句：不再说"须由适配层映射"。"""
        notes = " ".join(model_plan(settings_for())["notes"])
        self.assertIn("已由 DeepSeekProvider 映射", notes)
        self.assertNotIn("须由 DeepSeek 适配层映射", notes)

    def test_plan_notes_mention_retry_and_thinking_decisions(self):
        notes = " ".join(model_plan(settings_for())["notes"])
        self.assertIn("max_retries=0", notes)
        self.assertIn("thinking", notes)
        self.assertIn("disabled", notes)

    def test_plan_notes_mention_cache_conservative_gap(self):
        notes = " ".join(model_plan(settings_for())["notes"])
        self.assertIn("miss", notes)

    def test_disabled_model_plan_has_no_provider_notes(self):
        """未启用模型时不该出现"已配好 Provider"的陈述（避免读起来像已跑通）。"""
        settings = settings_for(PERSONA_MODEL_PROVIDER=None)
        notes = " ".join(model_plan(settings)["notes"])
        self.assertNotIn("max_retries=0", notes)

    def test_assembly_never_leaks_key_into_plan(self):
        plan = model_plan(settings_for())
        self.assertNotIn(FAKE_KEY, json.dumps(plan, default=str, ensure_ascii=False))
        self.assertTrue(plan["secret_status"]["PERSONA_PROVIDER_API_KEY"])


if __name__ == "__main__":
    unittest.main()
