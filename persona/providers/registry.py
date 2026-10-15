"""Provider 注册表（docs/INTERFACES.md §2.6）。

替换机制：`ProviderRegistry.get(name, config, secrets) -> Provider`。
**测试环境与生产代码走同一份接口**，不存在"测试专用分支"。

工厂签名统一为 `Callable[[ProviderConfig | None, Secrets | None], object]`：
- Mock 家族（fake / recording / failing）无视 `secrets`；
- 真实 Provider（如 `tavily`）从 `secrets` 取密钥，**自己不读环境变量**（D-077）。
"""

from __future__ import annotations

from typing import Any, Callable

from ..config.spec import Secrets
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result
from .base import ProviderConfig
from .mock import FakeProvider, FailingProvider, OfflineGuardProvider, RecordingProvider
from .tavily import TavilyProvider


class ProviderRegistry:
    """按名字取 provider 实现的注册表。"""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., object]] = {}

    def register(self, name: str, factory: Callable[..., object]) -> None:
        self._factories[name] = factory

    def names(self) -> list[str]:
        return sorted(self._factories)

    def get(
        self,
        name: str,
        config: ProviderConfig | None = None,
        secrets: "Secrets | None" = None,
    ) -> Result[object]:
        factory = self._factories.get(name)
        if factory is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未注册的 provider：{name!r}；可用：{self.names()}"))
        return Ok(factory(config, secrets))


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

    `tavily` 作为真实 Provider 一并注册，但**不被离线护栏包裹**——它只在
    `offline_only=false` 且密钥齐备时才会被装配层构造（见 `config.validate_settings`），
    护栏只会误伤真实调用，所以只对 Mock 家族加护栏。
    """
    registry = ProviderRegistry()
    registry.register("fake", lambda cfg, secrets=None: FakeProvider(fixtures, cfg or config))
    registry.register(
        "recording", lambda cfg, secrets=None: RecordingProvider(FakeProvider(fixtures, cfg or config))
    )
    registry.register("failing", lambda cfg, secrets=None: FailingProvider(config=cfg or config))

    registry.register(
        "tavily",
        lambda cfg, secrets=None: _build_tavily(cfg, secrets),
    )

    if guard_offline:
        # 把三个 Mock 实现都套上离线护栏：一旦真的联网就抛错
        for name in ("fake", "recording", "failing"):
            original = registry._factories[name]

            def _wrapped(cfg: ProviderConfig | None, secrets=None, _orig=original) -> object:
                return OfflineGuardProvider(_orig(cfg, secrets))

            registry.register(name, _wrapped)

    return registry


def _build_tavily(config: ProviderConfig | None, secrets: "Secrets | None"):
    """用注入的密钥构造 Tavily Provider。

    密钥优先取 `search_api_key`（Tavily 是搜索服务），回退到 `provider_api_key`
    （兼容统一命名）。两者皆空 → 密钥为空字符串；装配阶段已由 `validate_settings`
    保证"真实 Provider + offline_only=false"时必有密钥，这里不再重复校验。
    """
    key = ""
    if secrets is not None:
        key = secrets.search_api_key or secrets.provider_api_key or ""
    return TavilyProvider(api_key=key, config=config)
