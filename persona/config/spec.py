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


class ModelCurrency(str, Enum):
    """模型单价的**计价币种**（P4.4-B）。

    只允许两种：DeepSeek 官方价目表用 `USD`，阿里云百炼用 `CNY`。
    **一本账里只能有一个币种**——把 USD 单价和 CNY 上限混进同一本账，
    闸门算出来的数就没有意义了，所以币种是配置项而不是每次调用随便传。
    """

    USD = "USD"
    CNY = "CNY"


class ModelPriceTier(str, Enum):
    """价格档位（P4.4-B）。

    DeepSeek 的价目表**分峰时与闲时**（同一模型差一倍），所以"这次按哪个档位
    预留"必须显式选一个并在配置里留下痕迹——否则同一份单价既能算出 0.6 也能
    算出 1.2，账本上的数字变成猜的。
    """

    OFF_PEAK = "off_peak"    # 闲时（更便宜：估算更乐观 → 需要更保守的预留时别选它）
    PEAK = "peak"            # 峰时（更贵：**保守预留应该选这个**）


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
    max_remediations: int = 1            # 每个 URL 最多补救几次（P4.5：低质量正文的官方参数重试）
    max_cost: float | None = None        # 金额上限（None = 不限制）


# ---------------------------------------------------------------------------
# 关系研判（G-4 / P4.6）的配置
# ---------------------------------------------------------------------------

#: 装配离线规则分析器（默认）：确定性、零成本、条目全部可追溯到已登记证据。
RELATIONSHIP_ANALYZER_RULE = "rule"
#: 明确关闭：关系阶段退回"只搬共享经历"的兜底路径（D-D 的开关）。
RELATIONSHIP_ANALYZER_OFF = "off"
RELATIONSHIP_ANALYZER_MODES: tuple[str, ...] = (
    RELATIONSHIP_ANALYZER_RULE, RELATIONSHIP_ANALYZER_OFF)


@dataclass(frozen=True)
class RelationshipSettings:
    """关系语义分析器的装配开关（P4.6 / D-D：**默认装配，可关闭**）。

    `rule` = 注入 `RuleRelationshipAnalyzer`（离线确定性实现，不调模型、不花钱）；
    `off`  = 不注入，关系阶段走兜底（与 P4.6 之前的行为一致）。

    ⚠️ 关闭只影响"是否装配分析器"，**不影响** RELATION 证据的登记与消费口径：
    兜底路径仍然产出 `shared_experiences`，只是 `perspectives` / `feeling_toward`
    这些需要方向性研判的字段留空（并记 `open_questions`，不伪造）。
    """

    analyzer: str = RELATIONSHIP_ANALYZER_RULE

    def analyzer_enabled(self) -> bool:
        """是否装配规则分析器。未知取值一律按**关闭**处理（安全默认）。"""
        return str(self.analyzer or "").strip() == RELATIONSHIP_ANALYZER_RULE


# ---------------------------------------------------------------------------
# 模型抽取（G-2 / P4.4-B）的配置
# ---------------------------------------------------------------------------

# 已选定的服务商（P4.4-B 决策）。**这里只登记"允许联网的模型服务商"名字**，
# 具体单价一律由配置给出（见 `ModelSettings`）——单价是会过期的商业条款，
# 不写死在代码里当"官方费率"。
NETWORK_MODEL_PROVIDER_NAMES: tuple[str, ...] = ("deepseek",)

# 服务商决策的登记值（P4.4-B）：DeepSeek API / deepseek-flash / USD。
# 注意：**只有"是否启用"默认关闭**（`provider=None`），其余字段在 .env.example
# 里给出一份可直接用的取值；默认关闭是为了让"什么都不配"仍然离线、不花钱。
DEFAULT_MODEL_ID = "deepseek-flash"


@dataclass(frozen=True)
class ModelSettings:
    """模型抽取（G-2）的配置（P4.4-B）。

    分三组，各自回答一个问题：

    1. **用谁**：`provider` / `model_id` / `currency`。
       `provider=None` 表示**未启用模型抽取**（安全默认：什么都不配就离线、不花钱）。
    2. **多少钱**：`price_in_per_1m` / `price_out_per_1m` / `price_tier` /
       `price_source` / `price_checked_at`。
       单价是**原币**（`currency`）每 100 万 token；`price_source` + `price_checked_at`
       是"这个数字从哪来、什么时候核对的"——没有来源的单价等于没有单价。
    3. **闸门**：`soft_limit`（单次完整任务的软上限）/ `hard_limit`（软限之上的硬熔断）/
       `max_cost`（遗留总额上限）/ `max_cost_per_task`（遗留单任务上限）/
       `max_calls` / `est_tokens_in` / `est_tokens_out` / `require_gate_verdict`。
       **P0 起预算以「软硬双限」为准**：`soft_limit` 达值暂停询问、`hard_limit`
       达值强制中断，二者均以「本次任务口径（排除历史）」计算，历史账单不污染新任务。

    **币种固定 USD，不做汇率换算**（P4.4-C 拍板）：DeepSeek 官方计价即 USD，
    一本账里只会有一种币。原 P4.4-B 的 `fx_usd_cny` / `fx_source` 折算通道已
    按拍板**移除**——汇率是会过期的商业条款，而折算结果又不参与任何预算判定，
    留着只会多一个"看起来算了其实没算"的数。

    三条硬规矩（由 `validate_settings` 兜住，不是靠自觉）：
      - 设了费用上限 → **必须**给出输入与输出两个单价，否则拒绝加载
        （没单价就没法在发送前估算，也就没法保证不超支）；
      - 给出了单价 → **必须**记录来源；
      - 启用真实模型服务商 → 不允许 `offline_only=true`，且必须已设置密钥。
    """

    # ---- 1. 用谁 ----
    provider: str | None = None            # None = 未启用模型抽取
    model_id: str = DEFAULT_MODEL_ID
    currency: str = ModelCurrency.USD.value

    # ---- 2. 多少钱（原币 / 每 1M token）----
    price_tier: str = ModelPriceTier.OFF_PEAK.value
    price_in_per_1m: float | None = None
    price_out_per_1m: float | None = None
    price_source: str | None = None        # 官方价目表地址
    price_checked_at: str | None = None    # 核对日期（YYYY-MM-DD）

    # ---- 3. 闸门 ----
    max_cost: float | None = None            # （遗留）本轮/本账本的总额上限（原币）
    max_cost_per_task: float | None = None   # （遗留）单任务上限（原币）
    # —— P0 体验修复：全局单任务「软硬双限」（优先于上面的遗留上限）——
    soft_limit: float | None = None          # 单次完整任务的软上限（原币）；达此值暂停询问
    hard_limit: float | None = None          # 软上限之上的硬熔断值（原币）；达此值强制中断
    max_calls: int | None = None             # 一条流程最多几次模型调用
    est_tokens_in: int = 0                   # 事前估算输入 token（0 = 不估算）
    est_tokens_out: int = 0
    require_gate_verdict: bool = True         # 材料必须亮出正文质量门禁结论

    def enabled(self) -> bool:
        """是否启用模型抽取。`False` 时其余字段一律不生效（也不校验单价）。"""
        return bool(str(self.provider or "").strip())


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
    relationship: RelationshipSettings = field(default_factory=RelationshipSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
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
                "max_remediations": self.research.max_remediations,
                "max_cost": self.research.max_cost,
            },
            "relationship": {
                "analyzer": self.relationship.analyzer,
                "analyzer_enabled": self.relationship.analyzer_enabled(),
            },
            "model": {
                "enabled": self.model.enabled(),
                "provider": self.model.provider,
                "model_id": self.model.model_id,
                "currency": self.model.currency,
                "price_tier": self.model.price_tier,
                "price_in_per_1m": self.model.price_in_per_1m,
                "price_out_per_1m": self.model.price_out_per_1m,
                "price_source": self.model.price_source,
                "price_checked_at": self.model.price_checked_at,
                "max_cost": self.model.max_cost,
                "max_cost_per_task": self.model.max_cost_per_task,
                "max_calls": self.model.max_calls,
                "est_tokens_in": self.model.est_tokens_in,
                "est_tokens_out": self.model.est_tokens_out,
                "require_gate_verdict": self.model.require_gate_verdict,
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
    FieldSpec("PERSONA_RESEARCH_MAX_REMEDIATIONS", "research.max_remediations", "int", 1,
              "每个 URL 的正文抓取最多补救几次（低质量时用官方参数重试，默认 1；0 = 不补救）",
              minimum=0, maximum=5),
    FieldSpec("PERSONA_RESEARCH_MAX_COST", "research.max_cost", "float", None,
              "一次研究的金额上限；留空表示不限制", minimum=0, nullable=True),
    # ---- 关系研判（G-4 / P4.6，D-D）----
    FieldSpec("PERSONA_RELATIONSHIP_ANALYZER", "relationship.analyzer", "enum",
              RELATIONSHIP_ANALYZER_RULE,
              "关系语义分析器：rule=装配离线规则分析器（默认，零成本）；"
              "off=不装配，关系阶段走兜底",
              choices=RELATIONSHIP_ANALYZER_MODES),
    # ---- 模型抽取（G-2 / P4.4-B）：用谁 ----
    FieldSpec("PERSONA_MODEL_PROVIDER", "model.provider", "str", None,
              "模型服务商（当前只有 deepseek）；**留空 = 不启用模型抽取**（离线、不花钱）",
              nullable=True),
    FieldSpec("PERSONA_MODEL_ID", "model.model_id", "str", DEFAULT_MODEL_ID,
              "模型 ID（P4.4-B 决策：deepseek-flash）"),
    FieldSpec("PERSONA_MODEL_CURRENCY", "model.currency", "enum", ModelCurrency.USD.value,
              "模型单价的币种（一本账只能有一个币种）",
              choices=tuple(m.value for m in ModelCurrency)),
    # ---- 多少钱（原币 / 每 1M token）----
    FieldSpec("PERSONA_MODEL_PRICE_TIER", "model.price_tier", "enum",
              ModelPriceTier.OFF_PEAK.value,
              "按哪个价格档位预留（DeepSeek 分峰时/闲时，同一模型差一倍）",
              choices=tuple(t.value for t in ModelPriceTier)),
    FieldSpec("PERSONA_MODEL_PRICE_IN_PER_1M", "model.price_in_per_1m", "float", None,
              "输入单价（原币 / 每 100 万 token）；设了费用上限就必须给出",
              minimum=0, maximum=1_000_000, nullable=True),
    FieldSpec("PERSONA_MODEL_PRICE_OUT_PER_1M", "model.price_out_per_1m", "float", None,
              "输出单价（原币 / 每 100 万 token）；设了费用上限就必须给出",
              minimum=0, maximum=1_000_000, nullable=True),
    FieldSpec("PERSONA_MODEL_PRICE_SOURCE", "model.price_source", "str", None,
              "单价的来源（官方价目表地址）；给出单价就必须记录来源",
              nullable=True),
    FieldSpec("PERSONA_MODEL_PRICE_CHECKED_AT", "model.price_checked_at", "str", None,
              "单价的核对日期（YYYY-MM-DD）", nullable=True),
    # ---- 闸门 ----
    FieldSpec("PERSONA_MODEL_MAX_COST", "model.max_cost", "float", None,
              "（遗留）本轮模型调用的总额上限（原币，如 USD）；留空表示不限制",
              minimum=0, maximum=1_000_000, nullable=True),
    FieldSpec("PERSONA_MODEL_MAX_COST_PER_TASK", "model.max_cost_per_task", "float", None,
              "（遗留）单个任务的模型费用上限（原币）；留空表示不限制",
              minimum=0, maximum=1_000_000, nullable=True),
    # —— P0 体验修复：全局单任务「软硬双限」——
    FieldSpec("PERSONA_BUDGET_SOFT_LIMIT", "model.soft_limit", "float", None,
              "单次完整任务（无论几个阶段）的模型费用软上限（原币）；"
              "消耗达此值暂停并询问是否继续（y 则软限抬升 2.00 继续，n 则安全退出）；"
              "留空表示不启用软暂停",
              minimum=0, maximum=1_000_000, nullable=True),
    FieldSpec("PERSONA_BUDGET_HARD_LIMIT", "model.hard_limit", "float", None,
              "软上限之上的硬熔断值（原币）；单次任务消耗达此值强制中断以防烧钱；"
              "留空表示不启用硬熔断",
              minimum=0, maximum=1_000_000, nullable=True),
    FieldSpec("PERSONA_MODEL_MAX_CALLS", "model.max_calls", "int", None,
              "一条流程最多几次模型调用；留空表示不限制", minimum=0, maximum=10_000,
              nullable=True),
    FieldSpec("PERSONA_MODEL_EST_TOKENS_IN", "model.est_tokens_in", "int", 0,
              "事前估算的输入 token（0 = 不估算；设了费用上限就必须 > 0）",
              minimum=0, maximum=10_000_000),
    FieldSpec("PERSONA_MODEL_EST_TOKENS_OUT", "model.est_tokens_out", "int", 0,
              "事前估算的输出 token（0 = 不估算；设了费用上限就必须 > 0）",
              minimum=0, maximum=10_000_000),
    FieldSpec("PERSONA_MODEL_REQUIRE_GATE_VERDICT", "model.require_gate_verdict", "bool",
              True,
              "材料是否必须亮出正文质量门禁结论才允许进入抽取器（P4.4-B 决策：true）"),
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
    if spec.path == "relationship.analyzer":
        # 存纯字符串（理由同下面的模型枚举）：装配层按字符串比较决定是否注入分析器，
        # 变成枚举成员后 str() 会带上类名，比较就会永远不相等。
        if text not in RELATIONSHIP_ANALYZER_MODES:
            raise AssertionError(f"未登记的分析器取值：{text}")
        return text
    model_enum = _MODEL_ENUM_PATHS.get(spec.path)
    if model_enum is not None:
        # **存纯字符串**（不是枚举成员）：币种/档位要原样流进
        # `ModelCallLimits.currency`，变成枚举成员后 str() 会带上类名，
        # 账本里的币种就会写成 "ModelCurrency.USD" 这种不能对账的东西。
        return model_enum(text).value
    raise AssertionError(f"未登记的枚举字段：{spec.path}")


# 模型相关枚举字段：登记 → 取值 → 立即降回纯字符串（理由见上）
_MODEL_ENUM_PATHS: dict[str, type] = {
    "model.currency": ModelCurrency,
    "model.price_tier": ModelPriceTier,
}


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

    problems.extend(_validate_model(settings))

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


def _validate_model(settings: AppSettings) -> list[AppError]:
    """模型抽取配置的交叉校验（P4.4-B）。

    这里只做**配置层能判定的自相矛盾**，不判断"这个单价对不对"（那要人工核对
    官方价目表）。每一条都对应一条预算红线，放在加载期而不是运行期：

    - 设了上限却缺单价 → 运行期只能"拒绝发起请求"，与其到那时才停，不如加载就报错；
    - 给了单价却没有来源 → 无法审计"这个数字从哪来的"；
    - 单任务上限 > 总上限 → 自相矛盾；
    - 启用了真实模型服务商却仍要求离线 → 拒构造（与真实搜索 Provider 同规矩）。
    """
    model = settings.model
    problems: list[AppError] = []

    # 任何一类费用上限被设置（软/硬/遗留总额/遗留单任务），都必须给出单价与 token 估算
    cost_caps = [v for v in (model.soft_limit, model.hard_limit,
                             model.max_cost, model.max_cost_per_task)
                 if v is not None]
    if cost_caps:
        if model.price_in_per_1m is None or model.price_out_per_1m is None:
            problems.append(config_error(
                FIELD_BY_ENV_KEY["PERSONA_MODEL_PRICE_IN_PER_1M"],
                "设了模型费用上限，但输入/输出单价没有同时给出。"
                "没有单价就无法在发送前估算本次费用，也就无法保证不超支——"
                "闸门不会用估计值冒充，只会拒绝自动发起请求。请补齐两个单价"
                "或清空上限。"))
        if int(model.est_tokens_in or 0) <= 0 and int(model.est_tokens_out or 0) <= 0:
            problems.append(config_error(
                FIELD_BY_ENV_KEY["PERSONA_MODEL_EST_TOKENS_IN"],
                "设了模型费用上限，但事前 token 估算全是 0（est_tokens_in/out）。"
                "闸门要按『本次可能产生的最大费用』预留，没有 token 估算就算不出"
                "这个数——未知不等于 0。请给出保守估算或清空上限。"))
        # 软上限不得大于硬上限（两者都设时）
        if (model.soft_limit is not None and model.hard_limit is not None
                and model.soft_limit > model.hard_limit):
            problems.append(config_error(
                FIELD_BY_ENV_KEY["PERSONA_BUDGET_SOFT_LIMIT"],
                f"软上限 {model.soft_limit} 不能大于硬上限 {model.hard_limit}："
                "软上限是『暂停询问』的阈值，硬上限是『强制中断』的阈值，"
                "软限必须 ≤ 硬限。"))

    if (model.price_in_per_1m is not None or model.price_out_per_1m is not None) \
            and not str(model.price_source or "").strip():
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_MODEL_PRICE_SOURCE"],
            "给出了模型单价，但没有记录单价的来源（官方价目表地址）。"
            "来源不明的单价一旦过期就会被当成官方费率误信，比没有单价更危险。"))

    if (model.max_cost is not None and model.max_cost_per_task is not None
            and model.max_cost_per_task > model.max_cost):
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_MODEL_MAX_COST_PER_TASK"],
            f"单任务上限 {model.max_cost_per_task} 大于总上限 {model.max_cost}："
            "单任务花费不可能超过总额，这两个数写反了。"))

    if model.provider in NETWORK_MODEL_PROVIDER_NAMES:
        if settings.offline_only:
            problems.append(new_error(
                ErrorCode.VALIDATION_CONFIG_INVALID,
                f"model.provider={model.provider!r} 是真实模型服务商，"
                f"不能在 offline_only=true 下启用（那会真实联网并产生费用）。"
                f"请设 PERSONA_OFFLINE_ONLY=false 并提供模型密钥，"
                f"或把 PERSONA_MODEL_PROVIDER 留空以关闭模型抽取。",
                details={"path": "model.provider", "reason": "offline_conflict"},
            ))
        elif not settings.secrets.provider_api_key:
            problems.append(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"启用了真实模型服务商（model.provider={model.provider!r}），"
                f"但未设置模型密钥。请设置 {ENV_PROVIDER_API_KEY}"
                f"（或它的别名 DEEPSEEK_API_KEY）后再启用。",
                details={"env_key": ENV_PROVIDER_API_KEY, "reason": "missing"},
            ))

    # —— 交叉检查：要求门禁结论，却不抓正文 ——
    # 只在**真的启用模型**时才判：否则"什么都不配"的默认配置会被无关的规则打回。
    # 这条拦的是一种很具体的死局：检索摘要天生没有门禁结论（它不是网页正文），
    # 所以"要求每份材料都亮出结论" + "不抓正文" = 材料永远进不了抽取器
    # = 模型一次都不会被调用。这条死局在运行期看起来像"业务流程正常跑完了"，
    # 极难从日志发现，所以必须挡在加载期。
    if (model.enabled() and model.require_gate_verdict
            and int(settings.research.max_extracts or 0) <= 0):
        problems.append(config_error(
            FIELD_BY_ENV_KEY["PERSONA_RESEARCH_MAX_EXTRACTS"],
            "启用了真实模型抽取并开启 require_gate_verdict（材料必须亮出正文质量"
            "门禁结论），但研究阶段不抓取网页正文（max_extracts=0）：检索摘要天生"
            "没有门禁结论，结果是**没有任何材料能进入抽取器，模型一次都不会被调用**。"
            "请把该值设为 ≥1（并确认抓取正文确实经过质量门禁），"
            "或把 require_gate_verdict 设为 false。"))

    return problems


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
