"""Provider 层（L2）：唯一的外部出口。本阶段只有 Mock 实现，全程离线。"""

from .base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    Message,
    Provider,
    ProviderConfig,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from .mock import (
    FailingProvider,
    FakeProvider,
    OfflineGuardProvider,
    OfflineViolation,
    RecordingProvider,
    network_blocked,
)
from .registry import ProviderRegistry, default_registry

__all__ = [
    "CapabilitySet",
    "ChatRequest",
    "ChatResponse",
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
]
