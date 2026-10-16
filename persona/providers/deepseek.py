"""DeepSeek 聊天 Provider（P4.4-C；docs/CONFIG.md §11.7）。

**定位**：把"真实聊天模型"接进唯一的外部出口 `Provider` 协议，遵守 DeepSeek 官方
REST API（`POST /chat/completions`）。本文件**只负责外部调用与结果标准化**：

- `Provider.chat`（返回 `ChatResponse`）——本 Provider 唯一支持的协议方法；
- `search()` 明确拒绝（DeepSeek 不是搜索服务），不伪装成可用；
- 错误统一映射为 `EXTERNAL_*` 错误码（超时/限流/鉴权/不可用/响应格式错误）；
- 超时与退避严格走注入的 `ProviderConfig`——**默认 `max_retries=0`**（见下）。

# ⚠️ 三条与"钱"有关的纪律（P4.4-C 拍板 D1/D3/D5）

1. **Provider 只报 token，不碰钱**（D3）。DeepSeek 的响应里**只有 token、没有金额**，
   所以本类**绝不填 `cost_confirmed`**：填了就是把"按费率推算的值"冒充成"官方回报值"。
   金额由装配层（`orchestrator/model_setup.py`）按配置单价算出——那是全项目**唯一**的
   单价换算点。**本类也不持有、不接收 `ExternalUsageLedger`**：记账点是抽取器的
   `_settle()`，键口径含 `prompt_version` 与材料键排序，Provider 无从得知；
   若这里再记一笔，同一笔真实请求会在账本里出现两条不同键的记录，
   `occupied_cost()` 被**虚占一倍**，0.25 USD 的上限实际只剩一半可用。
2. **默认不重试**（`max_retries=0`）。对按 token 计费的模型，重试 = **再花一次钱**。
   配置层 `RetryLimits.max_retries` 默认是 3，那是给按次计费的检索写的；
   装配层会显式覆盖成 0，本类的兜底默认值也是 0（"忘了改配置"不该等于"重复花钱"）。
3. **思考模式显式关闭**（D1）。抽取是确定性结构化任务，而思考模式会生成
   `reasoning_content`，其 token **计入 `completion_tokens`**（直接推高费用），
   并与 `max_tokens` 抢额度——极端情况下推理吃光额度，返回空内容白花一次钱。
   官方文档对 `thinking.type` 的默认值表述不一致，所以本类**一律显式传值**，
   让行为确定，不依赖默认。

# JSON 模式的硬要求（D4：不静默改写提示词）

DeepSeek 的 `json_object` 模式有两条官方硬要求：
① 设 `response_format={'type':'json_object'}`；② **提示词里必须出现 "json" 字样**，
否则官方原文警告模型可能 "generate an unending stream of whitespace until the
generation reaches the token limit"（输出到 token 上限为止的空白）。

统一契约里的 `response_format="json_schema"` 表达的是"要结构化输出"这一意图，
DeepSeek 不支持严格 schema（**没有** `json_schema` 值），所以本类把它降级映射为
`json_object`。映射时**检查提示词里是否出现 "json"**（不区分大小写）：

- 有 → 照常发送；
- 没有 → 返回 `Err(EXTERNAL_UNAVAILABLE)`，**拒绝发送**。

**为什么是拒绝而不是自动补一句**：往调用方的提示词里塞文字是**隐藏行为**——
调用方以为自己控制了提示词，实际被改过，而且 dry-run 看到的与实际发出的不是同一份
请求。dry-run 打了桩才有真实价值：打出来的请求通不过这道校验，就说明**真跑也会被拦**。

# 官方契约（核对日期 2026-10-16，来源 api-docs.deepseek.com）

| 项 | 官方值 |
| --- | --- |
| Base URL / 端点 | `https://api.deepseek.com` + `POST /chat/completions` |
| 鉴权 | `Authorization: Bearer ${DEEPSEEK_API_KEY}` |
| `response_format` | **仅** `text` / `json_object`（无 `json_schema`） |
| 输出上限字段 | **只有** `max_tokens`（无 `max_completion_tokens`） |
| `temperature` | 0–2，官方默认 **1**（抽取器显式发 0.7，覆盖该默认，D2） |
| `thinking` | `{"type":"enabled"｜"disabled"}`，文档标默认 `enabled`（故显式传） |
| `finish_reason` | `stop`/`length`/`content_filter`/`tool_calls`/`insufficient_system_resource`/`aborted`（**无 `refusal`**） |
| `usage` | `prompt_tokens`（= hit + miss）、`completion_tokens`、`total_tokens` |
| 响应顶层 `id` | 服务端请求标识（`chatcmpl-…`）→ 同时进 `ChatResponse.raw_ref` 与 `UsageDelta.request_id`（抽取器据此写入账本，供逐笔对账/报障） |

**已知缺口（D5，已登记）**：`prompt_cache_hit_tokens` / `miss_tokens` 没有承载字段，
`ChatResponse.usage` 只有 `tokens_in` / `tokens_out`，所以本轮**统一按 cache miss
的保守口径**计价（= 高估 = 保守安全，配置里取的正是 miss 的 peak 价）。

# 安全红线

- `api_key` 由装配层经 `Secrets` 注入（D-077：除 `persona.config` 外任何模块不得
  `os.getenv`）；本类**从不**读取环境变量、从不持有全局默认密钥。
- 密钥**只**走 HTTPS 请求头，**绝不**进请求体、日志、异常、`repr` 或错误信息。
- **无任何 print / logging / 文件写入**。
- 所有网络调用收敛在 `self._urlopen` 一处（测试整类替换即零联网）。

**依赖**：仅标准库 `urllib` / `json` / `time` / `ssl` —— 不引入任何厂商 SDK（D-054）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Sequence

from ..core.context import CallContext
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, is_err
from ..core.temporal import to_iso, utc_now
from ..core.types import (
    COST_UNKNOWN,
    CURRENCY_UNKNOWN,
    SOURCE_NONE,
    UsageReport,
)
from .base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    Message,
    ProviderConfig,
    SearchRequest,
    SearchResponse,
)

# DeepSeek 官方 REST 端点（可经构造参数覆盖，便于测试指向桩服务器）
DEFAULT_BASE_URL = "https://api.deepseek.com"
CHAT_PATH = "/chat/completions"
DEFAULT_USER_AGENT = "generic-persona-skill/0.1"

#: Provider 名（进幂等键的 `provider_name` 段，见 `extraction._call_key`）
PROVIDER_NAME = "deepseek"

#: 只允许这三种消息角色（OpenAI 兼容格式）
_ALLOWED_ROLES: tuple[str, ...] = ("system", "user", "assistant")

#: JSON 模式的硬要求关键词（不区分大小写；见模块 docstring）
JSON_MODE_KEYWORD = "json"

#: 统一契约 → 厂商线格式：`json_schema` 降级为 `json_object`
#: （DeepSeek 没有 `json_schema` 值；schema 内容已由抽取器嵌进系统消息，不需再注入）
_RESPONSE_FORMAT_JSON_OBJECT = {"type": "json_object"}

#: 思考模式：显式关闭（D1）。取值确定优于依赖官方那个自相矛盾的默认值。
_THINKING_DISABLED = {"type": "disabled"}

# 可重试状态码（与 ERROR_HANDLING §2.1 类别默认一致；仅在 max_retries>0 时生效）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _opt_int(value: Any) -> int | None:
    """宽松转 int：转不了就是 `None`（**不填 0**，D-060）。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class _DeepSeekUsage:
    """本 Provider 的**本地请求计数**（供 `usage()` 协议方法报告）。

    ⚠️ 它**不是**账本：`ExternalUsageLedger` 的记账点在抽取器 `_settle()`。
    这里只是"这个 Provider 对象被调过几次"的自观测数字，不参与预算判定。
    """

    requests: int = 0


class DeepSeekProvider:
    """DeepSeek 真实聊天 Provider（可注入、可测试、密钥不外泄）。

    网络调用全部集中在 `_request_json`；测试可把 `provider._urlopen` 替换成桩函数，
    **完全不触网**地验证请求构造、响应归一化与错误映射。
    """

    #: 供 `extraction._call_key()` / `_settle()` 取用的稳定名字（不是类名）
    provider_name = PROVIDER_NAME

    def __init__(
        self,
        api_key: str,
        *,
        config: ProviderConfig | None = None,
        base_url: str = DEFAULT_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        model_id: str = "",
    ) -> None:
        # api_key 来自装配层注入；本类不读环境变量（D-077）。
        self.api_key = api_key or ""
        # 兜底默认 **max_retries=0**：重试 = 再花一次钱（见模块 docstring 第 2 条）。
        self.config = config if config is not None else ProviderConfig(max_retries=0)
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        # 配置里的模型 ID（请求若自带 `model_hint`，仍以其为准）。
        self.model_id = str(model_id or "")
        self._usage = _DeepSeekUsage()
        # 网络出口，单点可替换（测试桩）。**唯一**发生真实网络调用的地方。
        self._urlopen = urllib.request.urlopen

    # ------------------------------------------------------------ 能力

    def capabilities(self) -> Result[CapabilitySet]:
        return Ok(CapabilitySet(
            provider_name=PROVIDER_NAME,
            supports_chat=True,
            supports_search=False,               # DeepSeek 不是搜索服务
            supports_structured_output=True,     # json_object（非严格 schema）
            supports_extract=False,
            rate_limit_per_minute=None,          # 官方未给固定 RPM，不猜
            cost_model="per-token",              # 按 token 计费
        ))

    # ------------------------------------------------------------ 聊天

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        """统一契约 → DeepSeek 线格式 → 归一化回统一契约。

        **不记账**（D3）：只经 `ChatResponse.usage` 报 token 数，金额由装配层算。
        请求**发出之前**的错误（空消息 / 非法角色 / 未知 `response_format` /
        缺 "json"）一律 fail loud，不静默修补。
        """
        built = self._build_body(ctx, req)
        if is_err(built):
            return built
        body = built.value

        url = self.base_url + CHAT_PATH
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        # 密钥**只**走 HTTPS 请求头，绝不进请求体 / 日志 / 异常（同 Tavily）。
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout_s = max(0.1, (self.config.per_call_timeout_ms or 30_000) / 1000.0)

        result = self._request_json(ctx, url, data, headers, timeout_s)
        if is_err(result):
            return result                        # 原样传播 Err（费用状态由抽取器记 UNKNOWN）
        return self._normalize(ctx, result.value)

    # ------------------------------------------------------------ 其它协议方法

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        # 明确拒绝，不伪装成可用（替换检索请用 TavilyProvider）。
        return Err(new_error(
            ErrorCode.EXTERNAL_UNAVAILABLE,
            "DeepSeek Provider 仅提供聊天生成，不支持搜索",
            trace_ref=ctx.trace_ref))

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        # ⚠️ 这是"本对象调过几次"的自观测计数，**不是**外部分账本。
        return Ok(UsageReport(requests=self._usage.requests))

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        # 不发起网络请求；仅依据配置判断"是否具备调用条件"。
        ready = bool(self.api_key)
        return Ok(HealthStatus(
            reachable=ready,
            degraded=not ready,
            last_error_code=None if ready else str(ErrorCode.VALIDATION_REQUIRED_FIELD),
            checked_at=to_iso(utc_now()),
        ))

    # ------------------------------------------------------------ 请求构造

    def _build_body(self, ctx: CallContext, req: ChatRequest) -> Result[dict]:
        """统一契约 → DeepSeek 请求体。**唯一的映射点**（见模块 docstring 映射表）。"""
        messages: Sequence[Message] = list(getattr(req, "messages", None) or [])
        if not messages:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "chat 需要至少一条消息（system/user/assistant）",
                trace_ref=ctx.trace_ref))

        payload_messages: list[dict[str, str]] = []
        for message in messages:
            role = str(getattr(message, "role", "") or "").strip()
            if role not in _ALLOWED_ROLES:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"不支持的消息角色 {role!r}（只允许 {'/'.join(_ALLOWED_ROLES)}）",
                    trace_ref=ctx.trace_ref))
            payload_messages.append({
                "role": role,
                "content": str(getattr(message, "content", "") or ""),
            })

        model = str(getattr(req, "model_hint", "") or "").strip() or self.model_id
        if not model:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "既没有请求里的 model_hint，也没有装配层注入的模型 ID：不知道要调哪个模型",
                trace_ref=ctx.trace_ref))

        body: dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            # 显式发送，不依赖服务端默认（官方非流式等待期会持续吐空行 keep-alive）
            "stream": False,
            # D1：显式关闭思考模式（理由见模块 docstring）
            "thinking": dict(_THINKING_DISABLED),
        }

        fmt = str(getattr(req, "response_format", "") or "text").strip() or "text"
        if fmt == "json_schema":
            # ① 官方硬要求：提示词里必须出现 "json" 字样（否则可能输出到上限为止的空白）
            if not self._prompt_mentions_json(messages):
                return Err(new_error(
                    ErrorCode.EXTERNAL_UNAVAILABLE,
                    "DeepSeek 的 json_object 模式要求提示词里出现 \"json\" 字样，"
                    "否则官方明确警告模型可能『一直输出空白直到 token 上限』。"
                    "本次提示词里没有该字样 → **拒绝发送**（不静默改写调用方的提示词："
                    "那会让 dry-run 与实际请求不是同一份）",
                    trace_ref=ctx.trace_ref))
            # ② 统一契约 → 厂商线格式（DeepSeek 不支持严格 json_schema）
            body["response_format"] = dict(_RESPONSE_FORMAT_JSON_OBJECT)
        elif fmt != "text":
            # 不认识的取值不猜：猜错的后果是"发出去一份自己都没预期的请求"
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"不认识的 response_format={fmt!r}（统一契约只定义 text / json_schema）",
                trace_ref=ctx.trace_ref))
        # fmt == "text" → **不发送该字段**（与服务端默认一致，不制造差异）

        max_out = _opt_int(getattr(req, "max_output_tokens", None))
        if max_out is not None and max_out > 0:
            # DeepSeek **只有** `max_tokens`，没有 `max_completion_tokens`
            body["max_tokens"] = max_out

        temperature = getattr(req, "temperature", None)
        if temperature is not None:
            try:
                body["temperature"] = float(temperature)
            except (TypeError, ValueError):
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"temperature 不是数字：{type(temperature).__name__}",
                    trace_ref=ctx.trace_ref))

        stop = [str(s) for s in (getattr(req, "stop", None) or []) if str(s)]
        if stop:
            body["stop"] = stop

        # `metadata` **不发送**：含 prompt_version 等内部审计信息，不外发，
        # 也绝不放密钥或调用方的私有上下文。
        return Ok(body)

    @staticmethod
    def _prompt_mentions_json(messages: Sequence[Message]) -> bool:
        """提示词里是否出现 "json" 字样（**不区分大小写**，只看 content）。"""
        for message in messages or []:
            content = str(getattr(message, "content", "") or "")
            if JSON_MODE_KEYWORD in content.lower():
                return True
        return False

    # ------------------------------------------------------------ 响应归一化

    def _normalize(self, ctx: CallContext, data: dict) -> Result[ChatResponse]:
        """DeepSeek 响应 → `ChatResponse`。拿不到 content 就**不假装成功**。"""
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            return Err(new_error(
                ErrorCode.EXTERNAL_RESPONSE_INVALID,
                "DeepSeek 响应里没有 choices：拿不到任何内容，不假装成功",
                trace_ref=ctx.trace_ref))
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message")
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        text = "" if content is None else str(content)

        # 解析口径归 `claim_validation`（不越权判断"模型输出质量"）：
        # 这里只在"恰好能解析成 dict"时顺手填 `structured`，解析失败**不报错**。
        structured: dict | None = None
        if text.strip():
            try:
                parsed = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                structured = parsed

        usage = self._usage_delta(data.get("usage"),
                                  request_id=self._request_id_of(data))
        self._usage.requests += 1
        response = ChatResponse(
            text=text,
            structured=structured,
            finish_reason=str(first.get("finish_reason") or ""),
            model_id=str(data.get("model") or ""),
            usage=usage,
            raw_ref=str(data.get("id") or ""),      # 对账 / 报障标识
        )
        return Ok(response, usage=usage)

    @staticmethod
    def _request_id_of(data: dict) -> str | None:
        """取服务端请求标识（DeepSeek 响应**顶层** `id`，形如 `chatcmpl-…`）。

        与 `TavilyProvider._request_id_of` 同一口径：**响应没有就返回 `None`，
        绝不填 `""`**（D-060：未知不是空串，空串会伪装成"有标识但对不上"）。

        为什么必须从这里补进 `UsageDelta`：抽取器 `_settle()` 写账本时，
        `request_id` **只**从 `UsageDelta.request_id` 取。此前这里恒为 `None`，
        于是账本里每一笔模型费用的"服务端凭据"都是空的——真实对账（拿 token/费用
        去和账单逐笔核对、报障时给服务商定位）就少了唯一标识。响应 `id` 另存一份在
        `ChatResponse.raw_ref`，但那只是"对账/报障"的通用字段，**不能让 L4 抽取器
        反过来假设它等于 request_id**——那是把某家厂商的约定泄漏进业务层。
        所以补在 Provider 这一侧：谁最清楚自己的服务端标识，谁负责填。
        """
        value = data.get("id")
        text = "" if value is None else str(value).strip()
        return text or None

    @staticmethod
    def _usage_delta(raw: Any, *, request_id: str | None = None) -> UsageDelta:
        """DeepSeek `usage` → `UsageDelta`。**只报 token，绝不填金额**（D3）。

        `cost_confirmed` 恒为 `None`、`cost_source` 恒为 `NONE`：
        响应里只有 token、没有金额，所以 Provider **没有**"已确认费用"可报。
        金额由装配层按配置单价推算（抽取器 `_settle()` → `SOURCE_OFFICIAL_RATE`）。

        `request_id` 由调用方传入（见 `_request_id_of`）：它是**服务端标识**，
        不随 `usage` 字段是否返回而消失，所以单独传、不塞进 `raw`。
        """
        payload = raw if isinstance(raw, dict) else {}
        return UsageDelta(
            requests=1,
            tokens_in=_opt_int(payload.get("prompt_tokens")),
            tokens_out=_opt_int(payload.get("completion_tokens")),
            cost_estimate=None,
            cost_confirmed=None,        # ← 关键：Provider 不报金额
            currency=CURRENCY_UNKNOWN,
            cost_status=COST_UNKNOWN,   # 费用状态由抽取器结算时判定
            cost_source=SOURCE_NONE,
            request_id=request_id,      # ← 服务端标识（缺 → None，不填 ""）
        )

    # ------------------------------------------------------------ 网络出口

    def _request_json(
        self,
        ctx: CallContext,
        url: str,
        data: bytes,
        headers: dict[str, str],
        timeout_s: float,
    ) -> Result[dict]:
        """真正的网络收发与错误分类（重试在此收敛），**不做任何记账**。

        失败时费用状态一律由**抽取器**记为 `UNKNOWN`（失败 ≠ 免费）；本层只负责
        把失败**分类**清楚并原样传播。
        """
        max_retries = max(0, int(self.config.max_retries or 0))
        for attempt in range(max_retries + 1):
            try:
                request = urllib.request.Request(
                    url, data=data, headers=headers, method="POST")
                with self._urlopen(request, timeout=timeout_s) as response:  # type: ignore[call-arg]
                    body = response.read().decode("utf-8", errors="replace")
                return self._parse_body(ctx, body)
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code in (401, 403):
                    return Err(new_error(ErrorCode.EXTERNAL_AUTH,
                                         "DeepSeek 凭据无效（HTTP 401/403）",
                                         trace_ref=ctx.trace_ref))
                if code == 429:
                    if attempt < max_retries:
                        self._sleep(attempt, self._retry_after(exc))
                        continue
                    return Err(new_error(ErrorCode.EXTERNAL_RATE_LIMIT,
                                         "DeepSeek 触发限流（HTTP 429）",
                                         trace_ref=ctx.trace_ref))
                if code in _RETRYABLE_STATUS:
                    if attempt < max_retries:
                        self._sleep(attempt, None)
                        continue
                    return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                                         f"DeepSeek 服务暂时不可用（HTTP {code}）",
                                         trace_ref=ctx.trace_ref))
                # 其它 4xx：请求本身有问题（如 400 参数非法），重试无益
                return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                                     f"DeepSeek 拒绝请求（HTTP {code}）",
                                     trace_ref=ctx.trace_ref))
            except (urllib.error.URLError, TimeoutError, OSError):
                # 网络不可达 / 超时：可重试（默认 max_retries=0，即直接失败）
                if attempt < max_retries:
                    self._sleep(attempt, None)
                    continue
                return Err(new_error(ErrorCode.EXTERNAL_TIMEOUT,
                                     "DeepSeek 请求超时或网络不可达",
                                     trace_ref=ctx.trace_ref))
            except (json.JSONDecodeError, ValueError):
                # 响应格式错误：重试也无济于事
                return Err(new_error(ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                     "DeepSeek 返回了无法解析的响应",
                                     trace_ref=ctx.trace_ref))
        # 重试耗尽（默认 0 次重试时不会走到这里）
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                             "DeepSeek 请求在重试后仍然失败",
                             trace_ref=ctx.trace_ref))

    @staticmethod
    def _parse_body(ctx: CallContext, body: str) -> Result[dict]:
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return Err(new_error(ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                 "DeepSeek 返回了无法解析的响应",
                                 trace_ref=ctx.trace_ref))
        if not isinstance(data, dict):
            return Err(new_error(ErrorCode.EXTERNAL_RESPONSE_INVALID,
                                 "DeepSeek 响应顶层不是对象",
                                 trace_ref=ctx.trace_ref))
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
        """退避等待。默认 `max_retries=0` 时**永远不会被调用**，测试可打桩成空操作。"""
        if retry_after is not None and retry_after > 0:
            seconds = float(retry_after)
        else:
            base = (self.config.backoff_base_ms or 500) / 1000.0
            cap = (self.config.backoff_max_ms or 8000) / 1000.0
            seconds = min(cap, base * (2 ** attempt))
            if self.config.jitter:
                import random
                seconds *= (0.5 + random.random())
        time.sleep(seconds)
