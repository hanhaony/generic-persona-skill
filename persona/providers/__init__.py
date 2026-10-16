"""Provider 层（L2）：唯一的外部出口。

- Mock 家族全程离线（默认受离线护栏包裹）；
- `TavilyProvider`（搜索 / 正文抓取，P4.2）；
- `DeepSeekProvider`（聊天生成，P4.4-C）——两者都只在 `offline_only=false`
  且密钥齐备时才会被装配层构造。
"""

from .base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    ExtractRequest,
    ExtractResponse,
    ExtractResult,
    HealthStatus,
    Message,
    Provider,
    ProviderConfig,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from .deepseek import DeepSeekProvider
from .mock import (
    FailingProvider,
    FakeProvider,
    OfflineGuardProvider,
    OfflineViolation,
    RecordingProvider,
    network_blocked,
)
from .registry import ProviderRegistry, default_registry
from .tavily import TavilyProvider

__all__ = [
    "CapabilitySet",
    "ChatRequest",
    "ChatResponse",
    "ExtractRequest",
    "ExtractResponse",
    "ExtractResult",
    "HealthStatus",
    "Message",
    "Provider",
    "ProviderConfig",
    "SearchRequest",
    "SearchResponse",
    "SearchResult",
    "FakeProvider",
    "RecordingProvider",
    "FailingProvider",
    "OfflineGuardProvider",
    "OfflineViolation",
    "network_blocked",
    "ProviderRegistry",
    "default_registry",
    "TavilyProvider",
    "DeepSeekProvider",
]
