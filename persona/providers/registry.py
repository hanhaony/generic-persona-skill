"""Provider 注册表（docs/INTERFACES.md §2.6）。

替换机制：`ProviderRegistry.get(name, config) -> Provider`。
**测试环境与生产代码走同一份接口**，不存在"测试专用分支"。
"""

from __future__ import annotations

from typing import Callable

from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result
from .base import ProviderConfig
from .mock import FakeProvider, FailingProvider, OfflineGuardProvider, RecordingProvider


class ProviderRegistry:
    """按名字取 provider 实现的注册表。"""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[ProviderConfig | None], object]] = {}

    def register(self, name: str, factory: Callable[[ProviderConfig | None], object]) -> None:
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def get(self, name: str, config: ProviderConfig | None = None) -> Result[object]:
        factory = self._factories.get(name)
        if factory is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未注册的 provider：{name!r}；可用：{self.names()}"))
        return Ok(factory(config))


def default_registry(
    *,
    fixtures: dict | None = None,
    config: ProviderConfig | None = None,
    guard_offline: bool = True,
) -> ProviderRegistry:
    """内置 Mock 实现的注册表。

    - `fake`      确定性固定返回
    - `recording` 记录调用的 fake（回放测试用）
    - `failing`   注入错误码
    """
    registry = ProviderRegistry()
    registry.register("fake", lambda cfg: FakeProvider(fixtures, cfg or config))
    registry.register(
        "recording", lambda cfg: RecordingProvider(FakeProvider(fixtures, cfg or config))
    )
    registry.register("failing", lambda cfg: FailingProvider(config=cfg or config))

    if guard_offline:
        # 把三个实现都套上离线护栏：一旦真的联网就抛错
        for name in ("fake", "recording", "failing"):
            original = registry._factories[name]

            def _wrapped(cfg: ProviderConfig | None, _orig=original) -> object:
                return OfflineGuardProvider(_orig(cfg))

            registry.register(name, _wrapped)

    return registry
