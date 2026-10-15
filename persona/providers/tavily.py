"""Tavily 搜索 / 正文抓取 Provider（docs/INTERFACES.md §2、CONFIG.md）。

**定位（P4.2）**：把"真实搜索服务"接进唯一的外部出口 `Provider` 协议，遵守
Tavily 官方 REST API（search + extract 两个端点）。本文件**只负责外部调用与结果
标准化**，绝不绕过 `Orchestrator` / `Evidence` / 任务预算（D-030/D-054）：

- 搜索 → `Provider.search`（返回 `SearchResponse`）；
- 正文抓取 → 额外方法 `extract`（返回 `ExtractResponse`），`Provider` 协议本身不强制，
  由 `capabilities().supports_extract` 声明；
- 错误统一映射为 `EXTERNAL_*` 错误码（超时/限流/鉴权/不可用/响应格式错误）；
- 超时与退避重试严格走注入的 `ProviderConfig`，不自己拍脑袋定默认值。

**安全红线（与 Mock 家族同一套）**：
- `api_key` 由装配层经 `Secrets` 注入（D-077：除 `persona.config` 外任何模块不得
  `os.getenv`）；本类**从不**读取环境变量、从不持有全局默认密钥。
- **任何错误信息都不含密钥值**：异常只描述状态码/原因，绝不回显 `api_key`。
- 默认 `offline_only=true` 时系统根本不会构造本 Provider（见 `config.validate_settings`）；
  即便被构造，本类也**不主动发起请求**，所有网络调用都收敛在 `_http_post` 一处。

**本阶段限制**：实现已就绪，但**不在此阶段发起任何真实 API 请求、不产生费用**
（P4.2 第六步）。真实冒烟测试留待下一阶段，由人工显式开启 `offline_only=false`
并提供密钥后进行。

**依赖**：仅标准库 `urllib` / `json` / `time` / `ssl` —— 不引入第三方依赖（P4.2 第六步）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from ..core.context import CallContext
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, is_err
from ..core.temporal import to_iso, utc_now
from ..core.types import (
    COST_CONFIRMED,
    COST_UNKNOWN,
    CURRENCY_CREDIT,
    OP_EXTRACT,
    OP_SEARCH,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    SOURCE_NONE,
    SOURCE_OFFICIAL_RATE,
    SOURCE_PROVIDER_USAGE,
    ExternalUsageRecord,
    UsageReport,
)
from ..core.text_window import (
    DEFAULT_EXCERPT_WINDOW_CHARS,
    select_prose_window,
)
from ..core.usage_ledger import (
    ExternalUsageLedger,
    estimate_extract_credits,
    estimate_search_credits,
    make_idempotency_key,
)
from .base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    ExtractRequest,
    ExtractResponse,
    ExtractResult,
    HealthStatus,
    ProviderConfig,
    SearchRequest,
    SearchResponse,
    SearchResult,
)

# Tavily 官方 REST 端点（可经构造参数覆盖，便于测试指向桩服务器）
DEFAULT_BASE_URL = "https://api.tavily.com"
DEFAULT_USER_AGENT = "generic-persona-skill/0.1"

# 不可重试 / 可重试的快速判断（与 ERROR_HANDLING §2.1 类别默认保持一致）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class _TavilyUsage:
    """本 Provider 的本地用量记账（与 `FakeProvider` 同口径）。"""

    requests: int = 0


@dataclass
class _CallOutcome:
    """一次调用的结果三件套：响应体 + 逐笔用量记录 + 增量。"""

    data: dict
    record: ExternalUsageRecord
    usage: UsageDelta


class TavilyProvider:
    """Tavily 真实 Provider（可注入、可测试、密钥不外泄）。

    网络调用全部集中在 `_http_post`；测试可把 `provider._urlopen` 替换成桩函数，
    **完全不触网**地验证标准化与错误映射逻辑。
    """

    def __init__(
        self,
        api_key: str,
        *,
        config: ProviderConfig | None = None,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        usage_ledger: ExternalUsageLedger | None = None,
        excerpt_window_chars: int = DEFAULT_EXCERPT_WINDOW_CHARS,
    ) -> None:
        # api_key 来自装配层注入；本类不读环境变量。
        self.api_key = api_key or ""
        self.config = config or ProviderConfig()
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        # 证据摘录的窗口长度：摘录取"连续正文最密集的那段"，长度上限由它决定。
        self.excerpt_window_chars = max(1, int(excerpt_window_chars))
        self._usage = _TavilyUsage()
        # 逐笔用量账本：外部**共享**同一本账，预留由调用方做、结算由本类做。
        # 不注入时自建一本，保证 Provider 单独使用也不会丢记录。
        self.usage_ledger = usage_ledger or ExternalUsageLedger()
        # 网络出口，单点可替换（测试桩）。
        self._urlopen = urllib.request.urlopen

    # ------------------------------------------------------------ 能力

    def capabilities(self) -> Result[CapabilitySet]:
        return Ok(CapabilitySet(
            provider_name="tavily",
            supports_chat=False,         # Tavily 不是聊天模型
            supports_search=True,
            supports_structured_output=False,
            supports_extract=True,
            rate_limit_per_minute=None,
            cost_model="per-call",
        ))

    # ------------------------------------------------------------ 搜索

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        if not req.query.strip():
            return Err(new_error(ErrorCode.VALIDATION_QUERY_EMPTY, "空查询不会发起请求",
                                 trace_ref=ctx.trace_ref))
        limit = _coerce_int(req.limit, 5) or 5
        filters = req.filters or {}
        depth = str(filters.get("search_depth") or "basic")
        payload: dict[str, Any] = {
            # 注意：密钥**不进请求体**（P4.3 修正），改用 `Authorization: Bearer` 头，
            # 与 Tavily 官方当前规范一致（docs.tavily.com：header / required）。
            "query": req.query,
            "max_results": limit,
            "search_depth": depth,
            "include_raw_content": False,   # 正文抓取走独立 extract 端点
            "include_images": False,
            "include_usage": True,          # 让官方回报 usage.credits，供对账
        }
        include = filters.get("include_domains")
        exclude = filters.get("exclude_domains")
        if include:
            payload["include_domains"] = include if isinstance(include, list) else [include]
        if exclude:
            payload["exclude_domains"] = exclude if isinstance(exclude, list) else [exclude]

        # 幂等键：优先用调用方给的（便于与调用方的预留对齐），否则按请求内容生成。
        # 同一"请求意图"→ 同一 key → 重试/恢复时账本不会重复记账。
        key = (req.metadata or {}).get("idempotency_key") or make_idempotency_key(
            "tavily", OP_SEARCH, req.query, limit, depth)

        outcome = self._post_json(
            ctx, "/search", payload, operation=OP_SEARCH,
            idempotency_key=key, unit_count=1, depth=depth)
        if is_err(outcome):
            return outcome                      # Err 已带 usage（费用状态未知）

        call = outcome.value
        data = call.data
        raw_results = data.get("results") or []
        results: list[SearchResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            results.append(SearchResult(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                canonical_key=str(item.get("url") or ""),  # 用 URL 作稳定定位键
                snippet=str(item.get("content") or ""),
                published_at=self._coerce_published(item.get("published_date")),
                source_kind_guess="WEB_PAGE",
            ))
        # 结算（幂等：同一 key 重复结算会被忽略 → 重试/恢复不会重复计费）
        self.usage_ledger.settle(call.record)
        self._accumulate(call.usage)
        return Ok(SearchResponse(results=results, usage=call.usage), usage=call.usage)

    # ------------------------------------------------------------ 正文抓取

    def extract(self, ctx: CallContext, req: ExtractRequest) -> Result[ExtractResponse]:
        urls = [str(u) for u in (req.urls or []) if str(u).strip()]
        if not urls:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "extract 需要至少一个 URL", trace_ref=ctx.trace_ref))
        payload: dict[str, Any] = {
            "urls": urls,
            "include_images": bool(req.include_images),
            "include_usage": True,          # 同 search：回报 usage.credits 供对账
        }
        # ---- 官方确认过的可选参数：为 None 就不发送，让服务端用官方默认 ----
        depth = str(req.extract_depth or "basic")
        payload["extract_depth"] = depth
        if req.format:
            payload["format"] = req.format
        if req.query:
            payload["query"] = req.query
            # 官方约束：chunks_per_source 仅在提供 query 时可用（1..5）
            if req.chunks_per_source is not None:
                payload["chunks_per_source"] = max(1, min(5, int(req.chunks_per_source)))
        if req.include_favicon:
            payload["include_favicon"] = True

        key = (req.metadata or {}).get("idempotency_key") or make_idempotency_key(
            "tavily", OP_EXTRACT, *sorted(urls), depth, req.query or "",
            req.format or "", req.chunks_per_source)

        outcome = self._post_json(
            ctx, "/extract", payload, operation=OP_EXTRACT,
            idempotency_key=key, unit_count=len(urls), depth=depth)
        if is_err(outcome):
            return outcome

        call = outcome.value
        data = call.data
        out: list[ExtractResult] = []
        for item in (data.get("results") or []):
            if not isinstance(item, dict):
                continue
            raw = str(item.get("raw_content") or "")
            # 摘录取"连续正文最密集的那段原文"，而不是页头 —— 页头常常是导航/目录。
            # （P4.5 复测：314,743 字长页的页头 23,206 字全是目录，摘录因此正文 0 字）
            excerpt = select_prose_window(
                raw, window_chars=self.excerpt_window_chars)
            out.append(ExtractResult(
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                raw_content=raw,
                excerpt=excerpt,
                content_type="text/html",
                truncated=len(excerpt) < len(raw),
                error=None,
            ))
        # Tavily 会把抓不到的 URL 单独列在 failed_results
        failed_count = 0
        for failed in (data.get("failed_results") or []):
            if isinstance(failed, dict):
                failed_count += 1
                out.append(ExtractResult(
                    url=str(failed.get("url") or ""),
                    error=str(failed.get("error") or "extract_failed")))

        # 成功单元数已知 → 用真实成功数精炼记录（官方按成功提取数计费）
        call.record.success_unit_count = len(
            [r for r in out if not r.error]) if out else 0
        call.record.unit_count = len(urls)
        estimate = estimate_extract_credits(call.record.success_unit_count, depth)
        if estimate is not None:
            call.record.cost_estimate = estimate
            call.usage.cost_estimate = estimate
        # 全部 URL 都抓取失败 → 官方明确"不计费"，这是唯一能判 NOT_CHARGED 的情形
        if failed_count and call.record.success_unit_count == 0:
            self._mark_not_charged(call.record, call.usage)
        self.usage_ledger.settle(call.record)   # 结算（幂等，重试不会重复计）

        self._accumulate(call.usage)
        return Ok(ExtractResponse(results=out, usage=call.usage), usage=call.usage)

    # ------------------------------------------------------------ 其它协议方法

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        # Tavily 不提供聊天/生成能力：明确拒绝，不伪装成可用。
        return Err(new_error(
            ErrorCode.EXTERNAL_UNAVAILABLE,
            "Tavily Provider 仅提供搜索与正文抓取，不支持聊天生成",
            trace_ref=ctx.trace_ref))

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        report = UsageReport(requests=self._usage.requests)
        return Ok(report)

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        # 不发起网络请求；仅依据配置判断"是否具备调用条件"。
        ready = bool(self.api_key)
        return Ok(HealthStatus(
            reachable=ready,
            degraded=not ready,
            last_error_code=None if ready else str(ErrorCode.VALIDATION_REQUIRED_FIELD),
            checked_at=to_iso(utc_now()),
        ))

    # ------------------------------------------------------------ 网络出口

    def _post_json(
        self,
        ctx: CallContext,
        path: str,
        payload: dict[str, Any],
        *,
        operation: str,
        idempotency_key: str,
        unit_count: int,
        depth: str,
    ) -> Result["_CallOutcome"]:
        """发一次 POST 并解析 JSON；统一处理超时/限流/鉴权/不可用/格式错误，
        并在**成功与失败两条路径上**都留下逐笔用量记录。

        这是**唯一**发生真实网络调用的地方。所有重试与错误分类都在此收敛。

        **记账口径（一次逻辑请求 = 一笔记录）**：
        重试属于"同一个请求意图"，共用同一个 `idempotency_key`，
        所以无论重试几次，账本里**只有一笔**——这是不重复记账的关键。

        **失败 ≠ 免费**：请求失败时费用状态记为 `UNKNOWN`（不是 0），
        因为我们无法从失败响应里得知服务端是否已扣费。
        """
        url = self.base_url + path
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        # 官方鉴权：`Authorization: Bearer <api_key>` 请求头（P4.3 修正，原为 body 里的
        # api_key，已废弃且不见于当前文档）。密钥**只**走 HTTPS 请求头，
        # **绝不**进入日志、异常、`repr` 或错误信息。
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout_s = max(0.1, (self.config.per_call_timeout_ms or 30_000) / 1000.0)

        # —— 事前估算（按官方费率；请求前就算得出，供预留与保守判断）——
        if operation == OP_SEARCH:
            pre_estimate = estimate_search_credits(depth)
        else:
            pre_estimate = estimate_extract_credits(unit_count, depth)
        record = ExternalUsageRecord(
            idempotency_key=idempotency_key,
            provider="tavily",
            operation=operation,
            requested_at=to_iso(utc_now()),
            unit_count=unit_count,
            cost_estimate=pre_estimate,
            currency=CURRENCY_CREDIT if pre_estimate is not None else "UNKNOWN",
            cost_source=SOURCE_OFFICIAL_RATE if pre_estimate is not None else SOURCE_NONE,
            trace_ref=ctx.trace_ref,
        )

        result = self._request_json(ctx, url, data, headers, timeout_s)
        if is_err(result):
            # 失败：绝不把费用当成 0 —— 记录 UNKNOWN 并**保留**预占（失败≠免费）
            record.outcome = OUTCOME_FAILED
            record.cost_confirmed = None
            record.cost_status = COST_UNKNOWN
            record.cost_source = SOURCE_NONE
            record.request_id = None
            usage = UsageDelta(
                requests=0,
                cost_estimate=record.cost_estimate,
                cost_confirmed=None,
                currency=record.currency,
                cost_status=COST_UNKNOWN,
                cost_source=record.cost_source,
                request_id=None,
            )
            self.usage_ledger.settle(record)
            return result                      # 原样传播 Err，不吞
        body_data = result.value

        # —— 成功：能拿到多少就记多少，拿不到就留 UNKNOWN ——
        record.outcome = OUTCOME_SUCCEEDED
        record.request_id = self._request_id_of(body_data)
        credits = self._credits_of(body_data)
        if credits is not None:
            record.cost_confirmed = credits
            record.cost_status = COST_CONFIRMED
            record.cost_source = SOURCE_PROVIDER_USAGE
            record.currency = CURRENCY_CREDIT
        else:
            # 响应里有数据但没有 usage → **不知道花了多少**，绝不当 0
            record.cost_confirmed = None
            record.cost_status = COST_UNKNOWN
            record.cost_source = SOURCE_NONE
        usage = UsageDelta(
            requests=1,
            cost_estimate=record.cost_estimate,
            cost_confirmed=record.cost_confirmed,
            currency=record.currency,
            cost_status=record.cost_status,
            cost_source=record.cost_source,
            request_id=record.request_id,
        )
        return Ok(_CallOutcome(data=body_data, record=record, usage=usage), usage=usage)

    def _request_json(
        self,
        ctx: CallContext,
        url: str,
        data: bytes,
        headers: dict[str, str],
        timeout_s: float,
    ) -> Result[dict]:
        """真正的网络收发与错误分类（重试在此收敛），**不做记账**。"""
        max_retries = max(0, self.config.max_retries)
        for attempt in range(max_retries + 1):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method="POST")
                with self._urlopen(req, timeout=timeout_s) as resp:  # type: ignore[call-arg]
                    body = resp.read().decode("utf-8", errors="replace")
                return self._parse_body(ctx, body)
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code in (401, 403):
                    return Err(new_error(ErrorCode.EXTERNAL_AUTH,
                                         "Tavily 凭据无效（HTTP 401/403）", trace_ref=ctx.trace_ref))
                if code == 429:
                    retry_after = self._retry_after(exc)
                    if attempt < max_retries:
                        self._sleep(attempt, retry_after)
                        continue
                    return Err(new_error(ErrorCode.EXTERNAL_RATE_LIMIT,
                                         "Tavily 触发限流（HTTP 429）", trace_ref=ctx.trace_ref))
                if code in _RETRYABLE_STATUS:
                    if attempt < max_retries:
                        self._sleep(attempt, None)
                        continue
                    return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                                         f"Tavily 服务暂时不可用（HTTP {code}）",
                                         trace_ref=ctx.trace_ref))
                # 其它 4xx：请求本身有问题，不可重试
                return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                                     f"Tavily 拒绝请求（HTTP {code}）", trace_ref=ctx.trace_ref))
            except (urllib.error.URLError, TimeoutError, OSError):
                # 网络不可达 / 超时：可重试
                if attempt < max_retries:
                    self._sleep(attempt, None)
                    continue
                return Err(new_error(ErrorCode.EXTERNAL_TIMEOUT,
                                     "Tavily 请求超时或网络不可达", trace_ref=ctx.trace_ref))
            except (json.JSONDecodeError, ValueError):
                # 响应格式错误：不可重试（重试也无济于事）
                return Err(new_error(ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                     "Tavily 返回了无法解析的响应", trace_ref=ctx.trace_ref))
        # 走到这里说明重试耗尽
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                             "Tavily 请求在重试后仍然失败", trace_ref=ctx.trace_ref))

    # ------------------------------------------------------------ 用量记账

    @staticmethod
    def _request_id_of(data: dict) -> str | None:
        """取官方的 `request_id`（对账/报障的唯一标识）。响应没有 → `None`。"""
        value = data.get("request_id")
        return str(value) if value else None

    @staticmethod
    def _mark_not_charged(record: ExternalUsageRecord, usage: UsageDelta) -> None:
        """标记"官方明确不计费"——**唯一**可以这么判的情形。

        依据：Tavily 官方计费文档原文
        "You never get charged if a URL extraction fails"（docs.tavily.com/
        documentation/api-credits）。注意这里判的是**单条 URL 抓取失败**，
        不适用于"整个请求失败"（那时是 UNKNOWN，不是免费）。
        """
        from ..core.types import COST_NOT_CHARGED

        record.cost_confirmed = 0.0
        record.cost_estimate = 0.0
        record.cost_status = COST_NOT_CHARGED
        record.cost_source = SOURCE_PROVIDER_USAGE
        usage.cost_confirmed = 0.0
        usage.cost_estimate = 0.0
        usage.cost_status = COST_NOT_CHARGED
        usage.cost_source = SOURCE_PROVIDER_USAGE

    @staticmethod
    def _credits_of(data: dict) -> float | None:
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return None
        raw = usage.get("credits")
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_body(ctx: CallContext, body: str) -> Result[dict]:
        try:  # noqa
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return Err(new_error(ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                 "Tavily 返回了无法解析的响应", trace_ref=ctx.trace_ref))
        if not isinstance(data, dict):
            return Err(new_error(ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                 "Tavily 响应顶层不是对象", trace_ref=ctx.trace_ref))
        return Ok(data)

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError) -> int | None:
        value = (exc.headers or {}).get("Retry-After") if exc.headers else None
        if value is None:
            return None
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    def _sleep(self, attempt: int, retry_after: int | None) -> None:
        if retry_after is not None and retry_after > 0:
            secs = float(retry_after)
        else:
            base = (self.config.backoff_base_ms or 500) / 1000.0
            cap = (self.config.backoff_max_ms or 8000) / 1000.0
            secs = min(cap, base * (2 ** attempt))
            if self.config.jitter:
                import random
                secs *= (0.5 + random.random())
        # 真实退避只在生产路径发生；测试可 monkeypatch `_sleep` 成空操作。
        time.sleep(secs)

    def _accumulate(self, usage: UsageDelta) -> None:
        self._usage.requests += usage.requests or 0

    @staticmethod
    def _coerce_published(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
