"""模型抽取的**装配层**（P4.4-B）—— 把配置翻译成可运行的抽取器。

# 为什么单独一个模块

`persona.config` 只管"值从哪来、合不合法"，它**不许**反过来 import 业务模块
（那会把依赖指向上层）；`modules/extraction.py` 只管"怎么抽"，它**不许**读配置
（D-077：只有 config 包能读环境变量）。两边都守规矩，中间就必须有人把
"配置" 翻译成 "构造参数" —— 这个模块就是那个人，也是全项目**唯一**做这件事的地方。

    AppSettings ──model_limits_from_settings()──▶ ModelCallLimits
                ──claim_extractor_from_settings()──▶ ProviderClaimExtractor
                ──chat_provider_from_settings()──▶ DeepSeekProvider（P4.4-C）

# 三条纪律（写在这里是为了让"钱"这件事只有一种说法）

1. **币种只有一个**：`ModelCallLimits.currency` 直接取配置里的币种（P4.4-B 定为 USD）。
   一本账里混两种币，闸门算出来的数就没有意义。
2. **单价只有一个换算点**：配置里的单价是"每 100 万 token"，账本要的是"每 1000
   token"。换算只在本模块的 `unit_price_per_1k()` 里做一次；别处再乘再除，
   迟早会差 1000 倍。
3. **上限取更严的那个**（`effective_max_cost`）：既有闸门（`_budget_block`）只有
   **一个**金额上限参数，所以当"本轮总上限"和"单任务上限"同时配置时，本模块传
   **更小的那个**。这样两个上限都不可能被突破（详见 `effective_max_cost` 的说明）。
   把两个上限做成两层账需要共享账本之外的第二层计数，属新增机制，本轮不做。

# 本模块**不**做的事

⛔ 不发起任何外部调用（不 import 任何厂商 SDK，也不碰网络）；
⛔ 不读环境变量（密钥从 `AppSettings.secrets` 拿，且只拿"有没有设置"）；
⛔ 不打印密钥值（`model_plan()` 的输出里只有"已设置/未设置"）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from ..config import (
    DEFAULT_MODEL_ID,
    ENV_PROVIDER_API_KEY,
    NETWORK_MODEL_PROVIDER_NAMES,
    AppSettings,
    ModelSettings,
)
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result
from ..core.types import CURRENCY_UNKNOWN, SourceMaterial
from ..core.usage_ledger import (
    ExternalUsageLedger,
    ModelCallLimits,
    estimate_model_cost,
)
from ..modules.extract_quality import QUALITY_VALID
from ..modules.extraction import ProviderClaimExtractor, admit_materials
from ..modules.research import PersonQueryTarget
from ..providers.base import ChatRequest, Provider, ProviderConfig
from ..providers.deepseek import DEFAULT_BASE_URL, DeepSeekProvider

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

#: 配置里的单价单位是"每 100 万 token"（服务商价目表的通用写法），
#: 账本要的是"每 1000 token"。换算比例只在这里出现一次。
TOKENS_PER_PRICE_UNIT = 1_000_000
TOKENS_PER_LEDGER_UNIT = 1_000

#: dry-run 用的**合成**材料（不是任何真实人物/事件；只用于把提示词打出来看）。
#: ⚠️ 它带上 `VALID` 的门禁结论，正是为了让 dry-run 演示"能进抽取器的材料长什么样"；
#: 真实材料必须由 `Research` 的正文质量门禁给出结论，不能由调用方自封。
DRY_RUN_SAMPLE_TITLE = "[dry-run 合成样张] 雾港地方志摘抄"
DRY_RUN_SAMPLE_TEXT = (
    "阿雾在雾港灯塔值守，每日记录潮位与风向。"
    "据地方志记载，阿雾偏爱在傍晚独自巡视堤坝。"
)
DRY_RUN_SAMPLE_TARGETS: tuple[PersonQueryTarget, ...] = (
    PersonQueryTarget(entity_id="pe_dryrun_awu", display_name="阿雾"),
)


# --------------------------------------------------------------------------
# 1. 单价换算（唯一换算点）
# --------------------------------------------------------------------------


def unit_price_per_1k(price_per_1m: float | None) -> float | None:
    """把"每 100 万 token 的单价"换算成账本要的"每 1000 token 单价"。

    `None` 进 `None` 出——**缺单价就是缺单价**，绝不拿 0 顶替
    （D-060：未知不是 0；一个 0 单价会让"设了上限"的闸门形同虚设）。
    """
    if price_per_1m is None:
        return None
    return float(price_per_1m) / (TOKENS_PER_PRICE_UNIT / TOKENS_PER_LEDGER_UNIT)


def effective_max_cost(model: ModelSettings) -> float | None:
    """两个金额上限里**真正会被执行**的那一个（更严的那个）。

    既有闸门的金额判断是 `本账已占用 + 本次估算 > 上限`，只有一个上限参数。
    所以：

    - 只配了总上限 → 用总上限；
    - 只配了单任务上限 → 用单任务上限；
    - 两个都配 → 用**较小**的那个。因为账本的"已占用"是累计的，
      取较小值意味着两件事同时成立：单任务不会超它，累计也不会超它
      —— 代价是比"允许多任务累计到总上限"更保守（**宁可少花，不可超支**）。
    - 都没配 → `None`（不限制；此时闸门只剩次数上限与"单价缺失即拒"）。
    """
    candidates = [value for value in (model.max_cost, model.max_cost_per_task)
                  if value is not None]
    return min(candidates) if candidates else None


# --------------------------------------------------------------------------
# 2. 配置 → 运行限制
# --------------------------------------------------------------------------


def model_limits_from_settings(settings: AppSettings) -> ModelCallLimits:
    """`AppSettings.model` → `ModelCallLimits`（账本认的形状）。

    注意 `currency`：配置里没给币种时**不猜**，用 `CURRENCY_UNKNOWN`
    —— 币种未知的账本没法对账，但比"默认当成 USD"安全（那会把 CNY 当成 USD）。
    """
    model = settings.model
    currency = str(model.currency or "").strip() or CURRENCY_UNKNOWN
    return ModelCallLimits(
        max_calls=model.max_calls,
        max_cost=effective_max_cost(model),
        price_per_1k_in=unit_price_per_1k(model.price_in_per_1m),
        price_per_1k_out=unit_price_per_1k(model.price_out_per_1m),
        currency=currency,
        est_tokens_in=int(model.est_tokens_in or 0),
        est_tokens_out=int(model.est_tokens_out or 0),
    )


def estimate_call_cost_usd(settings: AppSettings) -> float | None:
    """按配置的单价与 token 估算**一次**模型调用的费用。

    `None` = 估不出来（缺单价或缺 token 估算）。调用方拿到 `None` 时**不得**
    把它当成 0——见 `modules/extraction.py` 的预算闸门。
    """
    limits = model_limits_from_settings(settings)
    return estimate_model_cost(limits)


def claim_extractor_from_settings(
    settings: AppSettings,
    provider: Provider,
    *,
    ledger: ExternalUsageLedger | None = None,
    usage_sink: Any = None,
    task_id: str | None = None,
) -> ProviderClaimExtractor:
    """按配置装配一个模型驱动抽取器（**只装配，不发请求**）。

    - `provider`：外部出口（D-054）。本模块不构造任何具体 Provider——
      用什么 Provider 由装配层上层决定，这里只要求它满足 `Provider` 协议。
    - `ledger`：不传就新建一本**本次装配独占**的账本。P4.4-C 拍板：总上限与
      单任务上限**暂取同一个值**（0.25 USD，见 `effective_max_cost`）；
      "$1.00 总预算"是项目级封顶，两层独立计数列为遗留项，本轮不实现。
    - `usage_sink`：跨轮幂等与逐笔用量的持久化出口（可选）。
    - `task_id`：写进用量记录，便于对账。
    """
    return ProviderClaimExtractor(
        provider,
        model_hint=settings.model.model_id or DEFAULT_MODEL_ID,
        limits=model_limits_from_settings(settings),
        ledger=ledger if ledger is not None else ExternalUsageLedger(),
        usage_sink=usage_sink,
        task_id=task_id,
        # gate 开关由配置决定（P4.4-B 决策：True → 材料必须亮出门禁结论）
        require_gate_verdict=bool(settings.model.require_gate_verdict),
    )


# --------------------------------------------------------------------------
# 3. 真实聊天 Provider 的装配（P4.4-C）
#
# 三条边界，写在这里是为了让"钱"这件事只有一种说法：
#
# 1. **Provider 不记账**（D3）。DeepSeek 响应里只有 token、没有金额，所以
#    Provider 只经 `ChatResponse.usage` 报 token；金额由本模块按配置单价算。
#    账本（`ExternalUsageLedger`）注入的是**抽取器**，不是 Provider——
#    记账键口径含 `prompt_version` 与材料键排序，Provider 无从得知；
#    若 Provider 也记一笔，同一笔真实请求会出现两条不同键的记录，
#    `occupied_cost()` 被虚占一倍，0.25 USD 的上限实际只剩一半可用。
# 2. **不重试**（`max_retries=0`）。对按 token 计费的模型，重试 = 再花一次钱。
#    配置层的 `RetryLimits.max_retries` 默认 3（那是给按次计费的检索写的），
#    这里**强制覆盖成 0**——"忘了改配置"不该等于"重复花钱"。
# 3. **不在离线开关下构造**。密钥缺失、或 `model.provider` 为空 → 直接 `Err`，
#    不构造一个"必然 401"的 Provider（那只会把配置问题伪装成网络问题）。
# --------------------------------------------------------------------------


def chat_provider_config(settings: AppSettings) -> ProviderConfig:
    """聊天的 `ProviderConfig`：**沿用配置的超时/退避，但 `max_retries` 强制 0**。

    为什么不直接 `settings.retry.to_provider_config()`：那份默认 `max_retries=3`，
    对按 token 计费的聊天模型意味着"一次失败自动再花三次钱"。
    """
    return replace(settings.retry.to_provider_config(), max_retries=0)


def chat_provider_from_settings(
    settings: AppSettings,
    *,
    base_url: str | None = None,
) -> Result[DeepSeekProvider]:
    """按配置装配一个真实聊天 Provider（**只装配，不发请求、不花钱**）。

    返回 `Err` 的三种情形（都**不构造**对象）：
      - `model.provider` 为空 → 模型抽取未启用（安全默认：什么都不配就离线）；
      - `model.provider` 不在 `NETWORK_MODEL_PROVIDER_NAMES` 内 → 不认识的服务商；
      - 密钥未设置（`{ENV_PROVIDER_API_KEY}`）→ 没有密钥就只能得到 401，
        不构造比构造一个必然失败的更清楚。

    ⚠️ 密钥从 `settings.secrets` 取（D-077：只有 `persona.config` 读环境变量），
    本函数**只判断"有没有设置"**，绝不打印、绝不出现在返回值里。
    """
    model = settings.model
    name = str(model.provider or "").strip()
    if not name:
        return Err(new_error(
            ErrorCode.VALIDATION_REQUIRED_FIELD,
            "model.provider 未设置：模型抽取未启用，没有可构造的聊天 Provider"
            "（安全默认：什么都不配就离线、不花钱）"))
    if name not in NETWORK_MODEL_PROVIDER_NAMES:
        return Err(new_error(
            ErrorCode.VALIDATION_REQUIRED_FIELD,
            f"不认识的模型服务商 model.provider={name!r}；"
            f"已登记的联网服务商：{list(NETWORK_MODEL_PROVIDER_NAMES)}"))
    key = str(settings.secrets.provider_api_key or "")
    if not key:
        return Err(new_error(
            ErrorCode.VALIDATION_REQUIRED_FIELD,
            f"未设置模型密钥（{ENV_PROVIDER_API_KEY}）：拒绝构造 {name} Provider"
            "——没有密钥就只能得到 401，不构造比构造一个必然失败的对象更清楚"))
    return Ok(DeepSeekProvider(
        api_key=key,
        config=chat_provider_config(settings),
        base_url=base_url or DEFAULT_BASE_URL,
        model_id=model.model_id or DEFAULT_MODEL_ID,
    ))


# --------------------------------------------------------------------------
# 4. dry-run：把"将要发出去的东西"打印出来（**不发出去**）
# --------------------------------------------------------------------------


def sample_materials() -> list[SourceMaterial]:
    """dry-run 用的合成材料（**明确标注为合成**，不冒充真实来源）。"""
    entity_ids = [t.entity_id for t in DRY_RUN_SAMPLE_TARGETS]
    return [SourceMaterial(
        source_id="src_dryrun_only",
        title=DRY_RUN_SAMPLE_TITLE,
        url="https://example.invalid/dry-run-sample",
        canonical_key="dryrun:sample",
        snippet=DRY_RUN_SAMPLE_TEXT,
        source_kind="WEB_PAGE",
        subject_entity_ids=list(entity_ids),
        quality_status=QUALITY_VALID,
    )]


def build_dry_run_request(
    settings: AppSettings,
    materials: Sequence[SourceMaterial] | None = None,
    targets: Sequence[Any] | None = None,
) -> tuple[ChatRequest, list[SourceMaterial], list[Any]]:
    """构造 dry-run 用的 `ChatRequest`：**不发送、不记账、不花钱**。

    与真实抽取**走同一段代码**（`ProviderClaimExtractor.build_request`），
    并且**同样先过材料准入**——否则打出来的提示词会包含真实运行时根本不会
    送出去的材料，dry-run 就变成了"看一份假的请求"。

    返回 `(请求, 实际会送出去的材料, 被准入挡下的拒收记录)`。
    """
    model = settings.model
    materials = list(materials) if materials is not None else sample_materials()
    targets = list(targets) if targets is not None else list(DRY_RUN_SAMPLE_TARGETS)
    admitted, rejected = admit_materials(
        materials, require_gate_verdict=bool(model.require_gate_verdict))
    request = ProviderClaimExtractor.build_request(
        admitted, targets, model_hint=model.model_id or DEFAULT_MODEL_ID)
    return request, admitted, rejected


# --------------------------------------------------------------------------
# 5. 可打印的决策快照（**不含任何密钥值**）
# --------------------------------------------------------------------------


def model_plan(settings: AppSettings) -> dict[str, Any]:
    """把"这次到底按什么价、什么上限、什么开关跑"整理成可打印的字典。

    与 `AppSettings.to_public_dict()['model']` 的分工：那份是**配置原样**，
    这份是**装配后的实际结果**（含换算后的单价、生效上限、单次调用估算、
    密钥是否已设置）。两者都不含密钥值（D-081）。
    """
    model = settings.model
    limits = model_limits_from_settings(settings)
    per_call = estimate_call_cost_usd(settings)
    return {
        "enabled": model.enabled(),
        "provider": model.provider,
        "model_id": settings.model.model_id or DEFAULT_MODEL_ID,
        "currency": limits.currency,
        "price_tier": model.price_tier,
        "price_per_1m_in": model.price_in_per_1m,
        "price_per_1m_out": model.price_out_per_1m,
        "price_per_1k_in": limits.price_per_1k_in,
        "price_per_1k_out": limits.price_per_1k_out,
        "price_source": model.price_source,
        "price_checked_at": model.price_checked_at,
        "max_cost_total": model.max_cost,
        "max_cost_per_task": model.max_cost_per_task,
        "max_cost_effective": limits.max_cost,
        "max_calls": limits.max_calls,
        "est_tokens_in": limits.est_tokens_in,
        "est_tokens_out": limits.est_tokens_out,
        "est_cost_per_call": per_call,
        "require_gate_verdict": bool(model.require_gate_verdict),
        # 只回答"有没有设置"，不回答"是什么"（D-081）
        "secret_status": settings.secrets.status(),
        "notes": _plan_notes(model),
    }


def _plan_notes(model: ModelSettings) -> list[str]:
    """把"还不成立的事"写在明面上——避免报告读起来像"已经跑通了"。"""
    notes: list[str] = []
    if not model.enabled():
        notes.append(
            "模型抽取**未启用**（model.provider 为空）。本计划只描述配置，"
            "不会有任何模型调用。")
    if model.price_source and model.price_checked_at:
        notes.append(
            f"单价来源已记录（核对日期 {model.price_checked_at}）；"
            "价格是时点数据，启用前请重新核对。")
    elif model.price_in_per_1m is not None or model.price_out_per_1m is not None:
        notes.append("**单价没有来源**：无法审计这个数字从哪来（配置校验本应拦住）。")
    if model.currency and str(model.currency).upper() != "USD":
        notes.append(f"币种为 {model.currency}，不是 P4.4-B 决策的 USD——请确认是否有意为之。")
    if model.max_cost is not None and model.max_cost_per_task is not None:
        effective = effective_max_cost(model)
        if model.max_cost == model.max_cost_per_task:
            notes.append(
                f"总上限与单任务上限暂取同一个值 {model.max_cost}"
                f"（P4.4-C 拍板：先用保守的单任务上限，总预算暂用同一值）。"
                f"既有闸门只执行一个金额上限，本装配层取更严的 {effective}；"
                f"两层独立计数（共享账本 + 任务级计数）是遗留项，本轮未做。")
        else:
            notes.append(
                f"两个金额上限同时存在（总 {model.max_cost} / 单任务 "
                f"{model.max_cost_per_task}）；既有闸门只执行一个，本装配层取"
                f"更严的 {effective}。要让总上限独立生效需第二层计数，"
                f"属新增机制，本轮未做。")
    if str(model.provider or "").strip() in NETWORK_MODEL_PROVIDER_NAMES:
        notes.append(
            "Provider 内部重试已**强制为 0**（max_retries=0，D3）：对按 token 计费的聊天"
            "模型，重试等于**再花一次钱**；请求失败即返回，不做自动重试。")
        notes.append(
            "思考模式**显式传** thinking={'type':'disabled'}（D1）：抽取是确定性结构化"
            "任务，开启会生成计入 completion_tokens 的推理 token（推高费用），"
            "并与 max_tokens 抢额度，可能撑出空内容白花一次钱。")
    notes.append(
        "DeepSeek 差异点（**已由 DeepSeekProvider 映射**）：其 API 只支持 "
        "response_format={'type':'json_object'}，不支持严格 json_schema。"
        "本请求按统一契约声明 response_format='json_schema'（表达的是"
        "『要结构化输出』这一意图），**DeepSeekProvider 已在发出前映射为 json_object**；"
        "json_object 模式要求提示词里出现 \"json\" 字样——抽取器的系统消息已嵌入 "
        "JSON Schema 文本（含 \"JSON Schema\"），映射时若检测不到该字样会**拒绝发送**"
        "（不静默改写提示词）。cache hit/miss 明细无承载字段，统一按 **miss 保守口径**"
        "计价（D5，已知缺口）。")
    return notes
