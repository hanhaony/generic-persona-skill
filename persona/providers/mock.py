"""Mock Provider 家族（docs/INTERFACES.md §2.6）。

**本阶段唯一可用的实现**，全程离线、结果完全确定，不需要任何 API Key：

| 实现 | 行为 |
| --- | --- |
| `FakeProvider`          | 按 `(能力, fixture_key)` 返回**固定**结果 |
| `RecordingProvider`     | 包装任意 provider，把每次请求/响应记进 `call_log`（回放测试用） |
| `FailingProvider`       | 按脚本注入指定错误码（限流、超时、鉴权失败、超预算……） |
| `OfflineGuardProvider`  | 包装任意 provider，**一旦发生真实网络调用立刻抛错**（保证测试真离线） |

重要约定：`fixture_key` 必须由调用方**显式**给出（`req.metadata["fixture_key"]`），
**禁止**用请求内容做模糊匹配返回不同结果——否则测试就不稳定了。
"""

from __future__ import annotations

import contextlib
import importlib
from dataclasses import dataclass
from typing import Any

from ..core.context import CallContext
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta
from ..core.temporal import to_iso, utc_now
from ..core.types import UsageReport
from .base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    ProviderConfig,
    SearchRequest,
    SearchResponse,
)


class OfflineViolation(RuntimeError):
    """测试期间发生了真实网络调用。"""


@contextlib.contextmanager
def network_blocked():
    """临时屏蔽 socket：任何真实网络连接都会抛 `OfflineViolation`。

    只用于测试与 `OfflineGuardProvider`，用的是标准库，不引入第三方依赖。
    """
    socket = importlib.import_module("socket")
    original_socket = socket.socket
    original_create = socket.create_connection

    class _BlockedSocket(original_socket):  # type: ignore[misc, valid-type]
        def connect(self, *args: Any, **kwargs: Any) -> None:
            raise OfflineViolation("检测到真实网络连接（测试环境必须离线）")

        def connect_ex(self, *args: Any, **kwargs: Any) -> int:
            raise OfflineViolation("检测到真实网络连接（测试环境必须离线）")

    def _blocked_create_connection(*args: Any, **kwargs: Any) -> Any:
        raise OfflineViolation("检测到真实网络连接（测试环境必须离线）")

    socket.socket = _BlockedSocket  # type: ignore[assignment]
    socket.create_connection = _blocked_create_connection  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[assignment]
        socket.create_connection = original_create  # type: ignore[assignment]


def _scope_of(ctx: CallContext) -> str:
    return ctx.budget_scope or ctx.task_id or ctx.project_id


class FakeProvider:
    """确定性假实现：同样的输入永远返回同样的输出。

    token 用量一律返回 `None`——**未知就不填，绝不猜**（D-060）。
    """

    def __init__(
        self,
        fixtures: dict[str, Any] | None = None,
        config: ProviderConfig | None = None,
    ) -> None:
        self.fixtures: dict[str, Any] = dict(fixtures or {})
        self.config = config or ProviderConfig()
        self._counts: dict[str, UsageReport] = {}

    # ------------------------------------------------------------ 能力

    def capabilities(self) -> Result[CapabilitySet]:
        return Ok(
            CapabilitySet(
                provider_name="fake",
                supports_chat=True,
                supports_search=True,
                supports_structured_output=True,
                max_context_tokens=None,
                max_output_tokens=None,
                rate_limit_per_minute=None,
                cost_model="per-call",
            )
        )

    # ------------------------------------------------------------ 调用

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        key = (req.metadata or {}).get("fixture_key")
        if not key:
            return Err(
                new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "FakeProvider 要求显式给出 metadata['fixture_key']",
                    trace_ref=ctx.trace_ref,
                )
            )
        if not req.messages:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD, "messages 不能为空",
                          trace_ref=ctx.trace_ref)
            )
        fixture = self.fixtures.get(f"chat:{key}")
        if fixture is None:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                          f"未登记的 fixture：chat:{key}", trace_ref=ctx.trace_ref)
            )
        usage = UsageDelta(requests=1, tokens_in=None, tokens_out=None, latency_ms=0)
        self._accumulate(ctx, usage)
        response = ChatResponse(
            text=fixture if isinstance(fixture, str) else getattr(fixture, "text", ""),
            structured=None,
            finish_reason="stop",
            model_id="fake-model",
            usage=usage,
            raw_ref=f"fake://chat/{key}",
        )
        if isinstance(fixture, ChatResponse):
            response = ChatResponse(
                text=fixture.text,
                structured=fixture.structured,
                finish_reason=fixture.finish_reason or "stop",
                model_id=fixture.model_id or "fake-model",
                usage=usage,
                raw_ref=fixture.raw_ref or f"fake://chat/{key}",
            )
        return Ok(response, usage=usage)

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        if not req.query.strip():
            return Err(new_error(ErrorCode.VALIDATION_QUERY_EMPTY, "空查询不会发起请求",
                                 trace_ref=ctx.trace_ref))
        key = (req.metadata or {}).get("fixture_key")
        if not key:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                          "FakeProvider 要求显式给出 metadata['fixture_key']",
                          trace_ref=ctx.trace_ref)
            )
        fixture = self.fixtures.get(f"search:{key}")
        if fixture is None:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                          f"未登记的 fixture：search:{key}", trace_ref=ctx.trace_ref)
            )
        usage = UsageDelta(requests=1, tokens_in=None, tokens_out=None, latency_ms=0)
        self._accumulate(ctx, usage)
        results = fixture if not isinstance(fixture, SearchResponse) else fixture.results
        return Ok(SearchResponse(results=list(results), usage=usage), usage=usage)

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        return Ok(self._counts.get(scope, UsageReport()))

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        return Ok(HealthStatus(reachable=True, degraded=False, checked_at=to_iso(utc_now())))

    # ------------------------------------------------------------ 内部

    def _accumulate(self, ctx: CallContext, delta: UsageDelta) -> None:
        scope = _scope_of(ctx)
        report = self._counts.setdefault(scope, UsageReport())
        report.requests += delta.requests
        report.latency_ms_total += delta.latency_ms
        if delta.tokens_in:
            report.tokens_in += delta.tokens_in
        if delta.tokens_out:
            report.tokens_out += delta.tokens_out
        report.breakdown_by_actor[ctx.actor] = report.breakdown_by_actor.get(ctx.actor, 0) + 1


@dataclass
class CallLogEntry:
    op: str
    request: dict[str, Any]
    ok: bool
    response: Any = None
    error_code: str | None = None
    at: str = ""


class RecordingProvider:
    """包装任意 provider，把每次调用记进 `call_log`，便于回放与回归对比。"""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.call_log: list[CallLogEntry] = []

    def _record(self, op: str, request: dict[str, Any], result: Result[Any]) -> None:
        self.call_log.append(
            CallLogEntry(
                op=op,
                request=request,
                ok=isinstance(result, Ok),
                response=result.value if isinstance(result, Ok) else None,
                error_code=str(result.error.code) if isinstance(result, Err) else None,
                at=to_iso(utc_now()),
            )
        )

    def capabilities(self) -> Result[CapabilitySet]:
        return self.delegate.capabilities()

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        result = self.delegate.chat(ctx, req)
        self._record("chat", {"fixture_key": (req.metadata or {}).get("fixture_key")}, result)
        return result

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        result = self.delegate.search(ctx, req)
        self._record("search", {"query": req.query}, result)
        return result

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        return self.delegate.usage(ctx, scope)

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        return self.delegate.health(ctx)


class FailingProvider:
    """按脚本注入错误。用于测试错误路径（限流、超时、鉴权失败、超预算……）。"""

    def __init__(
        self,
        failures: dict[str, ErrorCode] | None = None,
        *,
        default: ErrorCode = ErrorCode.EXTERNAL_UNAVAILABLE,
        config: ProviderConfig | None = None,
    ) -> None:
        self.failures = dict(failures or {})
        self.default = default
        self.config = config or ProviderConfig()

    def capabilities(self) -> Result[CapabilitySet]:
        return Ok(CapabilitySet(provider_name="failing", supports_chat=True, supports_search=True))

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        code = self.failures.get("chat", self.default)
        return Err(new_error(code, "注入的失败（测试用）", trace_ref=ctx.trace_ref))

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        code = self.failures.get("search", self.default)
        return Err(new_error(code, "注入的失败（测试用）", trace_ref=ctx.trace_ref))

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        return Ok(UsageReport())

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        return Ok(HealthStatus(reachable=False, degraded=True,
                               last_error_code=str(self.default), checked_at=to_iso(utc_now())))


class OfflineGuardProvider:
    """包装任意 provider：**一旦发生真实网络调用立即抛 `OfflineViolation`**。

    这是"测试真离线"的硬保证，不靠自觉。
    """

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate

    def capabilities(self) -> Result[CapabilitySet]:
        with network_blocked():
            return self.delegate.capabilities()

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        with network_blocked():
            return self.delegate.chat(ctx, req)

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        with network_blocked():
            return self.delegate.search(ctx, req)

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        with network_blocked():
            return self.delegate.usage(ctx, scope)

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        with network_blocked():
            return self.delegate.health(ctx)
