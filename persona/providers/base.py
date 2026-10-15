"""Provider —— 外部服务统一接口（docs/INTERFACES.md §2）。

**这是全项目唯一的外部出口**（D-030、D-054）：
    任何其他模块都不许直接依赖具体厂商 SDK（如 httpx / openai / requests），也不许自己持有 API Key。

本阶段（P1.2-B）只定义接口 + Mock 实现，**不接入任何真实服务**，也不需要 API Key。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.context import CallContext
from ..core.result import Result, UsageDelta
from ..core.types import UsageReport


@dataclass
class CapabilitySet:
    """能力描述（§2.2）。调用方**必须先查能力再决定策略**。"""

    provider_name: str = "unknown"
    supports_chat: bool = False
    supports_search: bool = False
    supports_structured_output: bool = False
    supports_extract: bool = False  # 是否支持"按 URL 抓取正文"（如 Tavily Extract）
    max_context_tokens: int | None = None
    max_output_tokens: int | None = None
    languages: list[str] = field(default_factory=lambda: ["zh", "en"])
    rate_limit_per_minute: int | None = None
    cost_model: str | None = None  # "per-token" / "per-call" / "unknown"


@dataclass
class ProviderConfig:
    """重试与超时参数（§2.5）。配置由 Orchestrator 注入，Provider 自己不持有全局默认值。"""

    max_retries: int = 3
    backoff_base_ms: int = 500
    backoff_max_ms: int = 8000
    jitter: bool = True
    per_call_timeout_ms: int = 30_000
    respect_retry_after: bool = True


@dataclass
class Message:
    role: str  # system / user / assistant
    content: str


@dataclass
class ChatRequest:
    messages: list[Message] = field(default_factory=list)
    model_hint: str | None = None
    response_format: str = "text"          # "text" | "json_schema"
    json_schema: dict | None = None
    temperature: float = 0.7
    max_output_tokens: int = 1024
    stop: list[str] = field(default_factory=list)
    timeout_ms: int = 30_000
    metadata: dict[str, Any] = field(default_factory=dict)  # 只透传，不得携带业务判断


@dataclass
class ChatResponse:
    text: str = ""
    structured: dict | None = None
    finish_reason: str = "stop"
    model_id: str = ""
    usage: UsageDelta | None = None
    raw_ref: str = ""


@dataclass
class SearchRequest:
    query: str = ""
    language: str | None = None
    limit: int = 10
    filters: dict = field(default_factory=dict)
    timeout_ms: int = 30_000
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    title: str = ""
    url: str = ""
    canonical_key: str = ""
    snippet: str = ""
    published_at: str | None = None
    source_kind_guess: str | None = None


@dataclass
class SearchResponse:
    results: list[SearchResult] = field(default_factory=list)
    usage: UsageDelta | None = None


@dataclass
class ExtractResult:
    """一次正文抓取的标准化结果（Tavily Extract 等接口的归一化形状）。

    `raw_content` 是网页正文原文；为遵守"默认不永久保存全部网页全文"，
    上层（如 `ResearchService`）只把它留在内存供下游抽取使用，**不会**写进
    `Source.content_ref`（除非显式开启 retention 策略）。
    """

    url: str = ""
    title: str = ""
    raw_content: str = ""          # 网页正文（可能很长；默认不落库）
    excerpt: str = ""              # 截断后的摘要（默认可用于落库）
    content_type: str = ""         # 内容类型（如 text/html）
    published_at: str | None = None
    truncated: bool = False        # 是否因长度策略被截断
    error: str | None = None       # 单条抓取失败原因（不影响其它 URL）


@dataclass
class ExtractResponse:
    """正文抓取批处理响应。"""

    results: list[ExtractResult] = field(default_factory=list)
    usage: UsageDelta | None = None


@dataclass
class ExtractRequest:
    """正文抓取请求：给一组 URL，取回网页正文（Tavily Extract 等）。

    P4.5 新增的 4 个字段**全部来自 Tavily 官方文档确认过**的参数
    （docs.tavily.com/documentation/api-reference/endpoint/extract，2026-10-15 核对）：

    - `extract_depth`：`basic` / `advanced`（官方默认 basic）
    - `format`：`markdown` / `text`（官方默认 markdown）
    - `query`：内容块相关性重排依据
    - `chunks_per_source`：每源返回的内容块数（1..5，**官方明确：仅在提供 `query` 时可用**）

    默认值一律 `None` = **不发送该参数**，让服务端用官方默认；
    这样既有调用方的行为完全不变（不重写已验收行为的底线）。
    """

    urls: list[str] = field(default_factory=list)
    include_images: bool = False
    timeout_ms: int = 30_000
    metadata: dict[str, Any] = field(default_factory=dict)
    # ---- 官方确认过的可选参数（None = 不发送，用服务端默认）----
    extract_depth: str | None = None
    format: str | None = None
    query: str | None = None
    chunks_per_source: int | None = None
    include_favicon: bool = False


@dataclass
class HealthStatus:
    """`health()` **永不失败**：不可达用 `reachable=False` 表达（§2.3）。"""

    reachable: bool = False
    degraded: bool = False
    last_error_code: str | None = None
    checked_at: str = ""


@runtime_checkable
class Provider(Protocol):
    """外部服务统一接口。所有实现（含 Mock）都必须满足这五个方法。"""

    def capabilities(self) -> Result[CapabilitySet]:
        ...

    def chat(self, ctx: CallContext, req: ChatRequest) -> Result[ChatResponse]:
        ...

    def search(self, ctx: CallContext, req: SearchRequest) -> Result[SearchResponse]:
        ...

    def usage(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        ...

    def health(self, ctx: CallContext) -> Result[HealthStatus]:
        ...
