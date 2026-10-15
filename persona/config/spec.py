"""配置的数据结构与校验规则（配套文档：`docs/CONFIG.md`）。

三条硬规矩（DECISIONS §2.12）：

- **D-077 统一入口**：除了本包，**任何模块都不许自己 `os.getenv("PERSONA_...")`**。
  要配置就向 `persona.config` 要，这样"这个值到底从哪来的"才查得到。
- **D-078 分两类**：**非敏感配置**（运行模式、数据目录、预算、日志级别……）可以打印、可以进日志；
  **敏感配置**（各类 API Key）**只能来自环境变量**，不进文件、不进日志、不进数据库、不进 Git。
- **D-081 错误信息绝不包含密钥值**：出错时只说"哪个环境变量、是缺失还是格式不对"，
  **一个字符的值都不回显**。

给初学者的比喻：
    把配置想成"一次点菜"。`AppSettings` 是最终那张菜单（菜名 + 分量 + 忌口），
    `Secrets` 是放在收银台里的支付密码——菜单上只写"已设置/未设置"，密码本身谁也不给看。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from ..core.errors import AppError, ErrorCode, new_error

# ---------------------------------------------------------------------------
# 环境变量前缀与敏感变量名
# ---------------------------------------------------------------------------

ENV_PREFIX = "PERSONA_"

# ⚠️ 这两个是**敏感**变量：只从环境变量读取，绝不写进文件/日志/错误信息。
ENV_PROVIDER_API_KEY = "PERSONA_PROVIDER_API_KEY"
ENV_SEARCH_API_KEY = "PERSONA_SEARCH_API_KEY"

SECRET_ENV_KEYS: tuple[str, ...] = (ENV_PROVIDER_API_KEY, ENV_SEARCH_API_KEY)

# 密钥的最小长度（只做"明显填错"的粗筛，不做真伪判断）
MIN_SECRET_LENGTH = 8


class RunMode(str, Enum):
    """运行模式。影响默认值与严格程度，**不影响隔离红线**。"""

    DEV = "dev"        # 本地开发：宽松，默认离线
    TEST = "test"      # 测试：默认离线、不写用户目录
    PROD = "prod"      # 生产：默认要求密钥齐备


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


# ---------------------------------------------------------------------------
# 敏感配置
# ---------------------------------------------------------------------------


def _mask(value: str | None) -> str:
    """把密钥值变成"能不能用的状态"，**绝不露出内容**。"""
    if not value:
        return "未设置"
    return f"已设置（长度 {len(value)}，内容不显示）"


@dataclass(frozen=True)
class Secrets:
    """敏感配置容器。

    铁律：
      - 值只在内存里；
      - `repr` / `str` / `to_masked_dict()` **一律脱敏**；
      - 校验失败时**只报错，不回显值**。
    """

    provider_api_key: str | None = None
    search_api_key: str | None = None

    def __repr__(self) -> str:  # 覆盖 dataclass 自动生成的 repr（防泄密）
        masked = [_mask(value) for _, value in self._items()]
        return f"Secrets(provider={masked[0]}, search={masked[1]})"

    __str__ = __repr__

    def status(self) -> dict[str, bool]:
        """只回答"有没有设置"，不回答"是什么"。"""
        return {
            ENV_PROVIDER_API_KEY: bool(self.provider_api_key),
            ENV_SEARCH_API_KEY: bool(self.search_api_key),
        }

    def to_masked_dict(self) -> dict[str, str]:
        return {key: _mask(value) for key, value in self._items()}

    def _items(self) -> list[tuple[str, str | None]]:
        return [
            (ENV_PROVIDER_API_KEY, self.provider_api_key),
            (ENV_SEARCH_API_KEY, self.search_api_key),
        ]

    def all_set(self) -> bool:
        return all(value for _, value in self._items())


# ---------------------------------------------------------------------------
# 非敏感配置
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetLimits:
    """一次任务的预算上限（`ERROR_HANDLING.md` §5）。

    `None` 表示**该项不限制**。超预算的行为是**暂停**，不是失败（D-043）。
    """

    max_requests: int = 200
    max_tokens_in: int = 500_000
    max_tokens_out: int = 200_000
    max_cost: float | None = None
    max_duration_sec: int | None = None


@dataclass(frozen=True)
class RetryLimits:
    """重试与超时参数，取值与 `INTERFACES.md` §2.5 的 `ProviderConfig` 一一对应。"""

    max_retries: int = 3
    backoff_base_ms: int = 500
    backoff_max_ms: int = 8_000
    jitter: bool = True
    per_call_timeout_ms: int = 30_000
    respect_retry_after: bool = True

    def to_provider_config(self):
        """转成 `ProviderConfig`（Provider 只认这个形状）。

        这是"配置层 → Provider 抽象"的唯一衔接点，避免 Provider 自己去找环境变量。
        """
        from ..providers.base import ProviderConfig

        return ProviderConfig(
            max_retries=self.max_retries,
            backoff_base_ms=self.backoff_base_ms,
            backoff_max_ms=self.backoff_max_ms,
            jitter=self.jitter,
            per_call_timeout_ms=self.per_call_timeout_ms,
            respect_retry_after=self.respect_retry_after,
        )


@dataclass(frozen=True)
class ResearchLimits:
    """一次研究的量级上限（P4.2 第三步第 2 点）。

    **权威定义放在配置层**：字段与 `CONFIG_FIELDS` 的 `research.*` 一一对应，
    装配层把配置值写入这里，再注入 `ResearchDeps.limits`。这样做既避免
    "重复定义配置字段"（P4.2 第二步：复用等效现有字段），也让"配置 → 运行限制"
    的链路单一、可查。
    """

    max_queries: int = 5                 # 最多几次检索
    max_results_per_query: int = 5       # 每次检索最多保留多少条结果
    max_extracts: int = 3                # 最多抓取多少篇网页正文（0 = 不抓取）
    max_cost: float | None = None        # 金额上限（None = 不限制）


@dataclass(frozen=True)
class AppSettings:
    """一次运行的完整配置（集中加载的结果）。

    `sources` 记录每个字段来自哪一层（default / dotenv / env / explicit），
    出问题的时候一眼能看出"是不是环境变量没生效"。
    """

    mode: RunMode = RunMode.DEV
    log_level: LogLevel = LogLevel.INFO
    data_dir: str = "var"
    database_filename: str = "persona.db"
    provider_name: str = "fake"
    offline_only: bool = True
    budget: BudgetLimits = field(default_factory=BudgetLimits)
    retry: RetryLimits = field(default_factory=RetryLimits)
    research: ResearchLimits = field(default_factory=ResearchLimits)
    secrets: Secrets = field(default_factory=Secrets)
    sources: Mapping[str, str] = field(default_factory=dict)

    # ---------------------------------------------------------------- 派生

    def database_path(self, root: str | Path | None = None) -> Path:
        """数据库文件路径。`data_dir` 是绝对路径就直接用，否则相对 `root`（默认当前工作目录）。"""
        base = Path(root) if root is not None else Path.cwd()
        directory = Path(self.data_dir)
        if not directory.is_absolute():
            directory = base / directory
        return directory / self.database_filename

    def ensure_data_dir(self, root: str | Path | None = None) -> Path:
        """确保数据目录存在（SQLite 不会自动建目录），返回数据库文件路径。"""
        path = self.database_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def to_provider_config(self):
        """便捷转发：直接用配置里的重试参数构造 `ProviderConfig`。"""
        return self.retry.to_provider_config()

    # ---------------------------------------------------------------- 展示

    def to_public_dict(self) -> dict[str, Any]:
        """可安全打印/写日志的快照：**不含任何密钥值**。"""
        return {
            "mode": self.mode.value,
            "log_level": self.log_level.value,
            "data_dir": self.data_dir,
            "database_filename": self.database_filename,
            "provider_name": self.provider_name,
            "offline_only": self.offline_only,
            "budget": {
                "max_requests": self.budget.max_requests,
                "max_tokens_in": self.budget.max_tokens_in,
                "max_tokens_out": self.budget.max_tokens_out,
                "max_cost": self.budget.max_cost,
                "max_duration_sec": self.budget.max_duration_sec,
            },
            "retry": {
                "max_retries": self.retry.max_retries,
                "backoff_base_ms": self.retry.backoff_base_ms,
                "backoff_max_ms": self.retry.backoff_max_ms,
                "jitter": self.retry.jitter,
                "per_call_timeout_ms": self.retry.per_call_timeout_ms,
                "respect_retry_after": self.retry.respect_retry_after,
            },
            "research": {
                "max_queries": self.research.max_queries,
                "max_results_per_query": self.research.max_results_per_query,
                "max_extracts": self.research.max_extracts,
                "max_cost": self.research.max_cost,
            },
            "secrets": self.secrets.to_masked_dict(),
        }

    def __repr__(self) -> str:  # 防止有人 print(settings) 时把密钥打出来
        return f"AppSettings({self.to_public_dict()})"

    __str__ = __repr__


# ---------------------------------------------------------------------------
# 字段登记表：一张表同时服务于「加载」「校验」「`persona config show`」「文档」
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """一个非敏感配置项的定义。"""

    env_key: str                 # 环境变量名
    path: str                    # 在 AppSettings 里的位置，如 "budget.max_requests"
    kind: str                    # enum / int / float / bool / str（float|int 支持空串 = None）
    default: Any
    help: str = ""
    choices: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    nullable: bool = False       # True：空串视为 None（不限制）


CONFIG_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("PERSONA_MODE", "mode", "enum", RunMode.DEV.value,
              "运行模式", choices=tuple(m.value for m in RunMode)),
    FieldSpec("PERSONA_LOG_LEVEL", "log_level", "enum", LogLevel.INFO.value,
              "日志级别", choices=tuple(m.value for m in LogLevel)),
    FieldSpec("PERSONA_DATA_DIR", "data_dir", "str", "var",
              "数据目录（相对路径以当前工作目录为基准）"),
    FieldSpec("PERSONA_DATABASE_FILENAME", "database_filename", "str", "persona.db",
              "SQLite 数据库文件名"),
    FieldSpec("PERSONA_PROVIDER", "provider_name", "str", "fake",
              "Provider 名称（当前只有 Mock 家族：fake / recording / failing / offline_guard）"),
    FieldSpec("PERSONA_OFFLINE_ONLY", "offline_only", "bool", True,
              "是否强制离线（True 时禁止任何真实外部调用）"),
    FieldSpec("PERSONA_BUDGET_MAX_REQUESTS", "budget.max_requests", "int", 200,
              "一次任务最多多少次外部调用", minimum=1, maximum=1_000_000),
    FieldSpec("PERSONA_BUDGET_MAX_TOKENS_IN", "budget.max_tokens_in", "int", 500_000,
              "输入 token 上限", minimum=0, maximum=100_000_000),
    FieldSpec("PERSONA_BUDGET_MAX_TOKENS_OUT", "budget.max_tokens_out", "int", 200_000,
              "输出 token 上限", minimum=0, maximum=100_000_000),
    FieldSpec("PERSONA_BUDGET_MAX_COST", "budget.max_cost", "float", None,
              "金额上限；留空表示不限制", minimum=0, nullable=True),
    FieldSpec("PERSONA_BUDGET_MAX_DURATION_SEC", "budget.max_duration_sec", "int", None,
              "最长运行秒数；留空表示不限制", minimum=1, nullable=True),
    FieldSpec("PERSONA_RETRY_MAX_RETRIES", "retry.max_retries", "int", 3,
              "最大重试次数", minimum=0, maximum=10),
    FieldSpec("PERSONA_RETRY_BACKOFF_BASE_MS", "retry.backoff_base_ms", "int", 500,
              "退避基数（毫秒）", minimum=1, maximum=60_000),
    FieldSpec("PERSONA_RETRY_BACKOFF_MAX_MS", "retry.backoff_max_ms", "int", 8_000,
              "退避上限（毫秒）", minimum=1, maximum=600_000),
    FieldSpec("PERSONA_RETRY_JITTER", "retry.jitter", "bool", True,
              "退避是否加随机抖动（避免惊群）"),
    FieldSpec("PERSONA_RETRY_TIMEOUT_MS", "retry.per_call_timeout_ms", "int", 30_000,
              "单次调用超时（毫秒）", minimum=100, maximum=600_000),
    FieldSpec("PERSONA_RETRY_RESPECT_RETRY_AFTER", "retry.respect_retry_after", "bool", True,
              "是否尊重服务端返回的 retry_after"),
    FieldSpec("PERSONA_RESEARCH_MAX_QUERIES", "research.max_queries", "int", 5,
              "一次研究最多发起多少次检索（默认 5）", minimum=1, maximum=100),
    FieldSpec("PERSONA_RESEARCH_MAX_RESULTS", "research.max_results_per_query", "int", 5,
              "每次检索最多保留多少条结果（默认 5）", minimum=1, maximum=50),
    FieldSpec("PERSONA_RESEARCH_MAX_EXTRACTS", "research.max_extracts", "int", 3,
              "一次研究最多抓取多少篇网页正文（0 = 不抓取正文，默认 3）", minimum=0, maximum=20),
    FieldSpec("PERSONA_RESEARCH_MAX_COST", "research.max_cost", "float", None,
              "一次研究的金额上限；留空表示不限制", minimum=0, nullable=True),
)

# 真实（联网）Provider 名称。装配前必须由 `validate_settings` 校验：
# 它们只能在 `offline_only=false` 且密钥齐备时被构造，否则一律拒构造。
NETWORK_PROVIDER_NAMES: tuple[str, ...] = ("tavily",)

FIELD_BY_ENV_KEY: dict[str, FieldSpec] = {spec.env_key: spec for spec in CONFIG_FIELDS}

# bool 的可接受写法（大小写不敏感）
_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})


# ---------------------------------------------------------------------------
# 解析（把字符串变成类型正确的值）
# ---------------------------------------------------------------------------


def parse_value(spec: FieldSpec, raw: str) -> tuple[Any, str | None]:
    """把字符串解析成字段需要的类型。

    返回 `(值, 错误原因)`；错误原因用来生成**不含密钥值**的报错信息。
    """
    text = raw.strip()

    if spec.kind == "enum":
        if text not in (spec.choices or ()):
            allowed = " / ".join(spec.choices or ())
            return None, f"取值 {text!r} 不在允许范围内（可选：{allowed}）"
        return _to_enum(spec, text), None

    if spec.kind == "bool":
        lowered = text.lower()
        if lowered in _TRUE_WORDS:
            return True, None
        if lowered in _FALSE_WORDS:
            return False, None
        return None, f"取值 {text!r} 不是布尔值（可写：true/false/1/0/yes/no/on/off）"

    if spec.nullable and text == "":
        return None, None

    if spec.kind == "int":
        try:
            value = int(text)
        except ValueError:
            return None, f"取值 {text!r} 不是整数"
    elif spec.kind == "float":
        try:
            value = float(text)
        except ValueError:
            return None, f"取值 {text!r} 不是数字"
    else:  # str
        value = text
        if value == "":
            return None, "不能为空"

    if spec.minimum is not None and value < spec.minimum:
        return None, f"取值 {value} 小于下限 {spec.minimum}"
    if spec.maximum is not None and value > spec.maximum:
        return None, f"取值 {value} 超过上限 {spec.maximum}"
    return value, None


def _to_enum(spec: FieldSpec, text: str):
    if spec.path == "mode":
        return RunMode(text)
    if spec.path == "log_level":
        return LogLevel(text)
    raise AssertionError(f"未登记的枚举字段：{spec.path}")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def validate_settings(settings: AppSettings) -> list[AppError]:
    """校验一份配置，返回**全部**问题（不是遇到第一个就停）。

    注意：涉及密钥的错误信息**只说变量名与问题性质，不回显任何字符**。
    """
    problems: list[AppError] = []

    # —— 非敏感字段的范围校验（解析阶段已查过一次，这里防御"直接构造"的情形）——
    for spec in CONFIG_FIELDS:
        value = _get_path(settings, spec.path)
        text = value.value if isinstance(value, Enum) else str(value)
        if spec.kind == "enum":
            if text not in (spec.choices or ()):
                problems.append(config_error(spec, f"取值 {text!r} 不在允许范围内"))
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            if spec.minimum is not None and value < spec.minimum:
                problems.append(config_error(spec, f"取值 {value} 小于下限 {spec.minimum}"))
            if spec.maximum is not None and value > spec.maximum:
                problems.append(config_error(spec, f"取值 {value} 超过上限 {spec.maximum}"))

    # —— 路径与名称不能为空 ——
    if not settings.data_dir.strip():
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_DATA_DIR"], "数据目录不能为空"))
    if not settings.database_filename.strip():
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_DATABASE_FILENAME"], "数据库文件名不能为空"))
    if not settings.provider_name.strip():
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_PROVIDER"], "Provider 名称不能为空"))

    # —— 退避上限不能小于基数 ——
    if settings.retry.backoff_max_ms < settings.retry.backoff_base_ms:
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_RETRY_BACKOFF_MAX_MS"],
            f"退避上限 {settings.retry.backoff_max_ms}ms 小于退避基数 "
            f"{settings.retry.backoff_base_ms}ms"))

    # —— 密钥：格式只粗筛，值绝不出现在信息里 ——
    for env_key, value in settings.secrets._items():
        if value is None:
            continue
        if len(value) < MIN_SECRET_LENGTH or any(ch.isspace() for ch in value):
            problems.append(new_error(
                ErrorCode.VALIDATION_CONFIG_INVALID,
                f"环境变量 {env_key} 的格式不符合要求（长度不足 {MIN_SECRET_LENGTH} 或含空白字符）。"
                f"出于安全考虑，这里不回显其值，请自行检查后重新设置。",
                details={"env_key": env_key, "reason": "format"},
            ))

    # —— 真实（联网）Provider 与离线开关互斥 ——
    if settings.provider_name in NETWORK_PROVIDER_NAMES and settings.offline_only:
        problems.append(new_error(
            ErrorCode.VALIDATION_CONFIG_INVALID,
            f"provider_name={settings.provider_name!r} 是真实联网 Provider，"
            f"不能在 offline_only=true 下使用。"
            f"请设 PERSONA_OFFLINE_ONLY=false 并提供搜索密钥，"
            f"或把 PERSONA_PROVIDER 改回 fake 以使用 Mock Provider。",
            details={"provider_name": settings.provider_name, "reason": "offline_conflict"},
        ))

    # —— 需要联网却没给任何密钥 ——
    has_any_key = bool(settings.secrets.provider_api_key or settings.secrets.search_api_key)
    if not settings.offline_only and not has_any_key:
        problems.append(new_error(
            ErrorCode.VALIDATION_REQUIRED_FIELD,
            f"当前配置允许真实外部调用（offline_only=false），但未提供任何密钥。"
            f"需设置 {ENV_PROVIDER_API_KEY} 或 {ENV_SEARCH_API_KEY}，"
            f"或把 PERSONA_OFFLINE_ONLY 设为 true 以使用 Mock Provider。",
            details={"env_key": ENV_SEARCH_API_KEY, "reason": "missing"},
        ))

    return problems


def config_error(spec: FieldSpec, reason: str) -> AppError:
    return new_error(
        ErrorCode.VALIDATION_CONFIG_INVALID,
        f"配置项 {spec.env_key}（{spec.path}）无效：{reason}",
        details={"env_key": spec.env_key, "path": spec.path},
    )


# ---------------------------------------------------------------------------
# 路径读写小工具（支持 "budget.max_requests" 这种点号路径）
# ---------------------------------------------------------------------------


def get_path(obj: Any, path: str) -> Any:
    """按点号路径取值，如 `get_path(settings, "budget.max_requests")`。"""
    current = obj
    for part in path.split("."):
        current = getattr(current, part)
    return current


_get_path = get_path  # 内部沿用旧名字，避免改动已写好的调用点


def set_path(settings: AppSettings, path: str, value: Any) -> AppSettings:
    """返回一个改了某项的新配置（原配置**不被修改**，因为都是 frozen 数据类）。"""
    parts = path.split(".")
    if len(parts) == 1:
        return replace(settings, **{parts[0]: value})
    group = getattr(settings, parts[0])
    new_group = _set_path(group, ".".join(parts[1:]), value)
    return replace(settings, **{parts[0]: new_group})


def _set_path(obj: Any, path: str, value: Any) -> Any:
    parts = path.split(".")
    if len(parts) == 1:
        return replace(obj, **{parts[0]: value})
    group = getattr(obj, parts[0])
    return replace(obj, **{parts[0]: _set_path(group, ".".join(parts[1:]), value)})
