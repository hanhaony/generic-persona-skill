"""外部调用的**用量与费用账本**（P4.5 任务一）。

对应 `ERROR_HANDLING` §5/§6、D-060（未知必须留 `None`）、D-089（四段式闸门）。

# 为什么需要单独一本账

既有 `usage_records` 表只记**聚合后的增量**（requests/tokens/cost_estimate），
无法回答下面这些问题，而 P4.3 的真实运行暴露出它们恰恰是关键：

- 这一笔是 **Search** 还是 **Extract**？（P4.3：Extract 的用量**根本没进账本**）
- 请求**失败**了吗？失败时费用是 0 还是"不知道"？
- 费用是**官方回报的真实值**，还是**按官方费率推算的估算**？
- 重试 / 任务恢复时，同一笔请求会不会被**重复记账**？

# 三条硬规矩

1. **估算与实际分开**：`cost_estimate` 是按官方费率**事前推算**的估算值；
   `cost_confirmed` 才是响应里 `usage.credits` 的**实际回报值**。两者永不混用。
2. **不知道就是不知道**：响应缺 `usage`、超时、断连、服务端错误 → `cost_status=UNKNOWN`，
   **绝不填 0**。0 是一个具体的"确认没有产生费用"的断言，未知不是 0（D-060）。
3. **失败 ≠ 免费**：请求失败时费用状态是 `UNKNOWN`，且**预占不释放**——
   转入 `unknown_hold` 继续占用额度，直到人工核对或后续确认。

# 官方计费口径（唯一可信来源：docs.tavily.com/documentation/api-credits，2026-10-15 核对）

- Search：basic **1 credit/次**，advanced **2 credits/次**
- Extract：basic **每 5 次成功 URL 提取 1 credit**，advanced **每 5 次 2 credits**
  → 单次估算 = 1/5 = 0.2（basic）
- **"You never get charged if a URL extraction fails"**（官方原文）→ 单条 URL
  抓取失败是**官方明确不计费**的，这是唯一可以判定 `NOT_CHARGED` 的情形。

⚠️ 上面的费率只用于**估算**。credits→货币的换算（pay-as-you-go $0.008/credit 等）
是**公开价目表，不是本账户实际单价**，本模块**不做货币换算**，一律以 credit 计量。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# 数据形状与状态常量放在 `core`：存储层（低层）与业务模块（L4）都要用它，
# 下沉到 core 才不会形成循环依赖（与 `SourceMaterial` 同一处理口径）。
from .types import (  # noqa: F401  (本模块对外重导出，供调用方单点引用)
    COST_CONFIRMED,
    COST_NOT_CHARGED,
    COST_UNKNOWN,
    CURRENCY_CREDIT,
    CURRENCY_UNKNOWN,
    OP_CHAT,
    OP_EXTRACT,
    OP_SEARCH,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    SOURCE_NONE,
    SOURCE_OFFICIAL_RATE,
    SOURCE_PROVIDER_USAGE,
    ExternalUsageRecord,
)

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

# ---- 官方费率常量（仅用于估算；来源见模块 docstring）----
OFFICIAL_SEARCH_CREDITS = {"basic": 1.0, "advanced": 2.0}
OFFICIAL_EXTRACT_CREDITS_PER_5 = {"basic": 1.0, "advanced": 2.0}
OFFICIAL_EXTRACT_BATCH = 5          # 官方按每 5 次成功提取结算

# 保守策略：未知费用的请求累计到这个数量，就停手等人工核对
DEFAULT_MAX_UNKNOWN_REQUESTS = 1
# 未知费用时，单次按这个保守值继续占用额度（= 1 次 basic search 的费率）
DEFAULT_UNKNOWN_HOLD_CREDITS = 1.0

# 告警
WARNING_USAGE_UNKNOWN = "USAGE_UNKNOWN"
WARNING_USAGE_LEDGER_PAUSED = "USAGE_LEDGER_PAUSED"


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelCallLimits:
    """模型调用的**量级与费用闸门**（P4.4-A 第五步）。

    **单价由装配层注入，本模块不内置任何单价**（理由见 `estimate_model_cost`）。
    单价一旦给出，账本就能同时回答"这次花了多少"和"还能不能再花"。

    三个闸门各管一件事，互不替代：
        - `max_calls`：次数上限（防"无限重试把额度打光"）；
        - `max_cost`：**单任务**金额上限（该任务累计估算值不得超过它）；
        - `price_per_1k_*`：把 token 换成金额的唯一依据；缺它就**无法保证**不超上限。

    P4.6（D-B）新增 `max_cost_total`：**项目累计封顶**。两层同时生效——

    - `max_cost` 看的是"这个任务（`task_id`）累计花了多少"；
    - `max_cost_total` 看的是"这本账（含 hydrate 读回的历史）累计花了多少"。

    修复前只有一个上限参数，装配层只能取两者中更严的那个来近似"两层"，
    于是"项目累计 1.00 USD"从来没有真正独立生效过。
    """

    max_calls: int | None = None          # 一次流程最多几次模型调用（None = 不限制）
    max_cost: float | None = None         # **单任务**费用上限（None = 不限制）
    max_cost_total: float | None = None   # **项目累计**费用上限（含历史；None = 不限制）
    price_per_1k_in: float | None = None  # 输入单价（每 1K token；官方价目表）
    price_per_1k_out: float | None = None
    currency: str = CURRENCY_UNKNOWN      # 单价的计价单位（如 CNY / USD）
    est_tokens_in: int = 0                # 事前估算的输入 token（0 = 不估算 → 费用未知）
    est_tokens_out: int = 0


# --------------------------------------------------------------------------
# 估算（只按官方费率，不做货币换算）
# --------------------------------------------------------------------------


def estimate_search_credits(depth: str = "basic") -> float | None:
    """按官方费率估算一次 Search 的 credits。**只返回估算，不冒充实际。**

    未知深度 → 返回 `None`（不猜，D-060）。
    """
    value = OFFICIAL_SEARCH_CREDITS.get(depth)
    return float(value) if value is not None else None


def estimate_extract_credits(success_urls: int, depth: str = "basic") -> float | None:
    """按官方费率估算 Extract 的 credits：`每 5 次成功提取 = 1 credit(basic)`。

    注意这是**估算**：官方说明的是"每 5 次成功提取计费 1 credit"，
    单次请求的**实际**扣费粒度与 `usage.credits` 的回报可能不同（真实回报过 0），
    所以估算值只能用于预算预留，**绝不当作实际账单**。
    """
    per_batch = OFFICIAL_EXTRACT_CREDITS_PER_5.get(depth)
    if per_batch is None:
        return None
    return (float(success_urls) / OFFICIAL_EXTRACT_BATCH) * float(per_batch)


def make_idempotency_key(provider: str, operation: str, *parts: Any) -> str:
    """生成幂等键：同一"请求意图"必然得到同一个 key（与时机无关）。

    组成 = provider + operation + 归一化后的请求要素（如排序后的 URL 列表、
    search 的 query+max_results）。重试同一请求 → key 相同 → 账本不会重复记账。
    """
    import hashlib

    normalized = "|".join(str(p) for p in parts if p is not None and str(p) != "")
    raw = f"{provider}|{operation}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def model_call_key_parts(
    *,
    model: str,
    material_keys: list[str] | tuple[str, ...],
    target_ids: list[str] | tuple[str, ...] = (),
    prompt_version: str = "",
    vocabulary_fingerprint: str = "",
) -> tuple:
    """模型调用幂等键的**唯一口径**（P4.4-A）。

    组成 = 模型名 | 排序后的材料键 | 排序后的目标对象 | 提示词版本 | 词表指纹

    为什么这些进键而"提示词字数"不进：键要表达的是**请求意图**（拿这批材料、
    对这些人、用这个模型抽一次）。字数是实现的副产品，把它放进键会让
    "同一意图、格式微调"变成两笔新花费——那就不是幂等了。

    `vocabulary_fingerprint`（D-286）：标签清单**不在**提示词版本里。
    同一批材料从 L1 切到 L2 是**不同的请求意图**（候选格不同），
    不带指纹就会撞上 L1 那次调用的键被当重复跳过、复用 L1 产出。
    空串表示"这次调用不涉及词表"（对话等），对既有调用完全兼容。
    """
    return (
        str(model or ""),
        *sorted(str(k) for k in material_keys if str(k or "").strip()),
        *sorted(str(t) for t in target_ids if str(t or "").strip()),
        str(prompt_version or ""),
        str(vocabulary_fingerprint or ""),
    )


def estimate_model_cost(
    limits: "ModelCallLimits",
    *,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
) -> float | None:
    """按**装配层给出的官方单价**估算一次模型调用费用。

    **单价缺失 → 返回 `None`（不知道就是不知道，D-060）**。本模块不内置任何
    服务商单价：那是"某账户在某时间点的商业条款"，写死进代码迟早会过期，
    而一个过期的单价会被当成"官方费率"误信——比没有单价更危险。

    返回 `None` 还有一个硬后果（P4.4-A 第五步第 3 点）：调用方在设了费用上限
    时必须**拒绝自动发起请求**，因为"无法估算"就等于"无法保证不超上限"。
    """
    if limits.price_per_1k_in is None and limits.price_per_1k_out is None:
        return None
    count_in = int(tokens_in if tokens_in is not None else limits.est_tokens_in)
    count_out = int(tokens_out if tokens_out is not None else limits.est_tokens_out)
    if count_in <= 0 and count_out <= 0:
        # 单价有、token 数却是 0（大概率是没给估算）→ **仍然算"不知道"**：
        # 用 0 当估算会让"设了上限"的闸门形同虚设（D-060：未知不是 0）。
        return None
    total = 0.0
    if limits.price_per_1k_in is not None:
        total += (count_in / 1000.0) * float(limits.price_per_1k_in)
    if limits.price_per_1k_out is not None:
        total += (count_out / 1000.0) * float(limits.price_per_1k_out)
    return total


def cost_from_model_usage(
    limits: "ModelCallLimits",
    *,
    tokens_in: int | None,
    tokens_out: int | None,
) -> float | None:
    """响应拿到真实 token 用量后，按官方单价算**实际**费用。

    与 `estimate_model_cost` 的分工：这个是**事后**、用的是响应回报的真实 token 数；
    那个是**事前**、用的是估算 token 数。两者都不做货币换算，单位由
    `limits.currency` 声明。token 数未知或缺单价 → `None`（不填 0）。
    """
    if tokens_in is None and tokens_out is None:
        return None
    if limits.price_per_1k_in is None and limits.price_per_1k_out is None:
        return None
    return estimate_model_cost(limits, tokens_in=tokens_in or 0,
                               tokens_out=tokens_out or 0)


def extract_key_parts(
    urls: list[str] | tuple[str, ...],
    *,
    depth: str = "basic",
    query: str = "",
    fmt: str = "",
    chunks_per_source: int | None = None,
) -> tuple:
    """Extract 幂等键的**唯一口径**（P4.5 补救接线）。

    `Research` 预留（请求发出之前）与 Provider 兜底推导（没给 metadata 键时）
    **必须调同一个函数**。此前两边各写一遍：research 侧 4 段、provider 侧 6 段，
    任何一处漂移都会出事——

    - 预留用 A 键、结算用 B 键 → A 的预留永远弹不掉，**幽灵占额度**；
    - 两笔不同请求共用一个键 → 该记的那笔被当成重复，**静默不计**。

    组成（顺序即契约，不要单独改一边）：
        排序后的 URL 们 | depth | query | format | chunks_per_source
    空值由 `make_idempotency_key` 过滤（None / "" 不参与哈希），
    所以"没带 query"与"带了空 query"天然是同一个键。
    """
    return (
        *sorted(str(u) for u in urls if str(u).strip()),
        str(depth or "basic"),
        query or "",
        fmt or "",
        chunks_per_source,
    )


# --------------------------------------------------------------------------
# 账本
# --------------------------------------------------------------------------


@dataclass
class HydrationSummary:
    """一次**跨进程 hydrate** 的结果（P4.6 / G11）。

    存在的意义：把"这次启动到底读到了多少历史支出"变成可打印的事实，
    而不是让"闸门为什么拦我"变成一句没有依据的话。
    """

    loaded: int = 0                      # 实际读进账本的历史笔数
    skipped: int = 0                     # 与本次已记/已读重复，跳过
    confirmed_cost: float = 0.0          # 历史里已确认的实际费用
    estimated_cost: float = 0.0          # 历史里只有估算的金额
    unknown_requests: int = 0            # 历史里费用状态未知的笔数
    unpriced_unknown: int = 0            # 未知且**无任何估算可用**的笔数（金额无法界定）
    occupied: float = 0.0                # 计入累计闸门的总金额（保守口径）
    currencies: tuple[str, ...] = ()      # 历史记录里出现过的计量单位
    already_hydrated: bool = False       # 之前已经 hydrate 过（本次不重复累加）

    def as_dict(self) -> dict[str, Any]:
        return {
            "loaded": self.loaded,
            "skipped": self.skipped,
            "confirmed_cost": self.confirmed_cost,
            "estimated_cost": self.estimated_cost,
            "unknown_requests": self.unknown_requests,
            "unpriced_unknown": self.unpriced_unknown,
            "occupied": self.occupied,
            "currencies": list(self.currencies),
            "already_hydrated": self.already_hydrated,
        }


@dataclass
class UsageTotals:
    """账本汇总：估算、实际、未知三者分开报。"""

    requests: int = 0
    confirmed_cost: float | None = None
    estimated_cost: float = 0.0
    unknown_hold_cost: float = 0.0
    reserved_cost: float = 0.0
    unknown_requests: int = 0
    failed_requests: int = 0

    def held_cost(self) -> float:
        """已被占用但尚未确认为实际费用的额度（预留 + 未知占位）。"""
        return self.reserved_cost + self.unknown_hold_cost

    def accountable_cost(self) -> float:
        """用于保守预算判断的"已占用成本"= 实际 + 预留 + 未知占位。"""
        return (self.confirmed_cost or 0.0) + self.held_cost()


class ExternalUsageLedger:
    """一本可追溯、可预留、幂等的外部用量账本。

    生命周期与既有四段式闸门（D-089）对齐：
        `reserve(估算)` → 发请求 → `settle(记录)` 或 `release(仅未发出时)`

    # 本次 vs 历史（G11，P4.6）

    账本把记录分成**两本**，这是刻意为之，不是实现细节：

    - `_records`：**本次进程**记下的笔（`records()` 只返回这些）；
    - `_history`：`hydrate()` 从持久层读回的**历史**笔。

    为什么必须分开：`_chat_calls()` 用 `records()` 数"本次调用了几次"来对
    `max_calls` 限流。如果把历史笔混进去，昨天花过的 1 次会让今天一启动就
    "次数已用尽"——那是一个真实的 bug，不是保守。

    但**费用口径必须累计**：`occupied_cost()` 把两本加起来，所以项目级累计
    上限（`ModelCallLimits.max_cost_total`）看到的是"历史上花过的 + 本次已经花的"。
    """

    def __init__(
        self,
        *,
        max_unknown_requests: int = DEFAULT_MAX_UNKNOWN_REQUESTS,
        unknown_hold_credits: float = DEFAULT_UNKNOWN_HOLD_CREDITS,
    ) -> None:
        self.max_unknown_requests = max_unknown_requests
        self.unknown_hold_credits = unknown_hold_credits
        self._records: dict[str, ExternalUsageRecord] = {}
        self._history: dict[str, ExternalUsageRecord] = {}
        self._reservations: dict[str, float] = {}
        self._unknown_holds: dict[str, float] = {}
        #: 历史笔计入累计闸门的金额（保守口径，见 `hydrate()`）
        self._history_cost: float = 0.0
        #: 历史里"未知且无任何估算可用"的笔数——金额无法界定，不能当 0
        self._history_unpriced: int = 0
        self._hydrated: bool = False

    # ---------------------------------------------------------------- 跨进程

    def hydrate(
        self,
        records: "list[ExternalUsageRecord] | tuple[ExternalUsageRecord, ...]",
        *,
        unknown_fallback_cost: float | None = None,
    ) -> HydrationSummary:
        """把**历史笔**读进账本，让累计闸门在重启后仍然成立（G11）。

        计入口径（D-C：宁可保守）：

        1. `cost_confirmed` 有值 → 用实际值（历史确认过的就是事实）；
        2. 只有 `cost_estimate` → 用估算值，并与 `unknown_fallback_cost`（装配层
           按当前单价算出的一次调用估算）取**较大者**——因为估算填低了会让累计
           闸门少算，而少算是唯一不可接受的误差方向；
        3. 状态 `UNKNOWN` 且两个值都没有 → 用 `unknown_fallback_cost`；
           若连它也没有 → 记进 `unpriced_unknown`，金额按 0 累加但**单独计数**，
           由发送前的闸门决定是否停手（见 `ProviderClaimExtractor._budget_block`）。
           绝不把它静默当成"没花钱"（D-060）；
        4. `NOT_CHARGED` → 0（账户被明确告知没有产生费用）。

        幂等：同一个 `idempotency_key` 只读一次；重复调用本方法不会重复累加
        （返回的 `HydrationSummary.already_hydrated=True`）。
        """
        if self._hydrated:
            return HydrationSummary(already_hydrated=True,
                                    occupied=self._history_cost,
                                    unpriced_unknown=self._history_unpriced)
        summary = HydrationSummary()
        fallback = (float(unknown_fallback_cost)
                    if unknown_fallback_cost is not None else None)
        currencies: set[str] = set()
        for record in records or []:
            key = str(record.idempotency_key or "")
            if not key or key in self._history or key in self._records:
                summary.skipped += 1
                continue
            self._history[key] = record
            summary.loaded += 1
            if record.currency:
                currencies.add(str(record.currency))
            if record.cost_status == COST_UNKNOWN:
                summary.unknown_requests += 1

            cost: float
            if record.cost_confirmed is not None:
                cost = float(record.cost_confirmed)
                summary.confirmed_cost += cost
            elif record.cost_estimate is not None:
                candidates = [float(record.cost_estimate)]
                if fallback is not None:
                    candidates.append(fallback)
                cost = max(candidates)
                summary.estimated_cost += float(record.cost_estimate)
            elif record.cost_status == COST_UNKNOWN:
                if fallback is not None:
                    cost = fallback
                else:
                    cost = 0.0
                    summary.unpriced_unknown += 1
                    self._history_unpriced += 1
            elif record.cost_status == COST_NOT_CHARGED:
                cost = 0.0
            else:
                cost = 0.0                       # 既无金额也非未知：按 0（不猜）
            self._history_cost += cost
        summary.occupied = self._history_cost
        summary.unpriced_unknown = self._history_unpriced
        summary.currencies = tuple(sorted(currencies))
        self._hydrated = True
        return summary

    def historical_records(self) -> list[ExternalUsageRecord]:
        """历史笔（不含本次进程记下的）。"""
        return list(self._history.values())

    def unpriced_unknown_requests(self) -> int:
        """历史里"未知且无估算"的笔数：金额无法界定，闸门必须据此停手。"""
        return self._history_unpriced

    def history_cost(self) -> float:
        """历史笔按保守口径计入的金额。"""
        return self._history_cost

    # ---------------------------------------------------------------- 预留

    def reserve(self, key: str, estimate: float | None) -> None:
        """② 预占：请求**发出之前**调用，先把额度冻住。"""
        self._reservations[key] = estimate or 0.0

    def release(self, key: str) -> None:
        """请求**根本没发出去**（如预算已被拦下）时释放预留，不计入任何账。"""
        self._reservations.pop(key, None)

    # ---------------------------------------------------------------- 结算

    def settle(self, record: ExternalUsageRecord) -> bool:
        """④ 记账：**幂等**。同一个 `idempotency_key` 只记一次。

        返回 `True` 表示真的记了这一笔；`False` 表示是重复记账，已被忽略
        （重试 / 任务恢复时靠这个保证不重复计费）。

        关键规则：**费用未知时，预留不释放**，转为 `unknown_hold` 继续占用额度——
        因为"不知道花了多少"不等于"没花钱"。
        """
        key = record.idempotency_key
        if key in self._records:
            return False                      # 已记过 → 忽略，绝不重复

        had_reservation = key in self._reservations
        estimate = self._reservations.pop(key, None)
        if record.cost_estimate is None and estimate is not None:
            record.cost_estimate = estimate  # 沿用事前估算

        if record.cost_status == COST_UNKNOWN:
            hold = record.cost_estimate or self.unknown_hold_credits
            self._unknown_holds[key] = float(hold)
        elif not had_reservation:
            pass                              # 未预留也未结算：按实际值入账
        self._records[key] = record
        return True

    def confirm(self, key: str, confirmed_cost: float, *, source: str) -> bool:
        """后续拿到可信数据（如账单）后，把 UNKNOWN 补成 CONFIRMED。

        这是"人工核对流程"的落点之一：补认后，该笔占用的额度从"未知占位"
        转为"实际消耗"。
        """
        record = self._records.get(key)
        if record is None:
            return False
        record.cost_confirmed = confirmed_cost
        record.cost_source = source
        record.cost_status = COST_CONFIRMED
        self._unknown_holds.pop(key, None)
        return True

    # ---------------------------------------------------------------- 查询

    def records(self) -> list[ExternalUsageRecord]:
        return list(self._records.values())

    def get(self, key: str) -> ExternalUsageRecord | None:
        return self._records.get(key)

    def has_attempted(self, key: str) -> bool:
        """这个幂等键是否已经走过"预留 / 结算"——**重发可能重复计费**。

        `settle()` 的幂等只防"重复**记账**"，防不了"重复**花钱**"：键相同
        再发一次，钱真的会再花一次，而 settle 只会静默返回 `False`。
        发送前守卫 + settle 才是完整的幂等（P4.5 D3）。

        覆盖三种状态：已结算（`_records`）、费用未知占位（`_unknown_holds`）、
        预留中（`_reservations`）——后两者说明这笔已经花过（或正在花），
        同样不能重发。

        G11：还覆盖 `hydrate()` 读回的历史笔——否则换个进程重跑，同一请求
        会再花一次钱，而内存账本是空的。
        """
        return (key in self._records or key in self._unknown_holds
                or key in self._reservations or key in self._history)

    def totals(self) -> UsageTotals:
        """**本次进程**的汇总（不含历史笔；历史见 `history_cost()`）。"""
        t = UsageTotals()
        for r in self._records.values():
            t.requests += 1
            if r.outcome == OUTCOME_FAILED:
                t.failed_requests += 1
            if r.cost_confirmed is not None:
                t.confirmed_cost = (t.confirmed_cost or 0.0) + r.cost_confirmed
            if r.cost_estimate is not None:
                t.estimated_cost += r.cost_estimate
            if r.cost_status == COST_UNKNOWN:
                t.unknown_requests += 1
        t.reserved_cost = sum(self._reservations.values())
        t.unknown_hold_cost = sum(self._unknown_holds.values())
        return t

    def _charged_cost(self, record: ExternalUsageRecord) -> float:
        """单笔记录在当前账本里"占用了多少额度"。

        与实际/估算的优先级一致：实际 > 估算 > 未知占位 > 0。
        """
        if record.cost_confirmed is not None:
            return float(record.cost_confirmed)
        if record.cost_estimate is not None:
            return float(record.cost_estimate)
        if record.cost_status == COST_UNKNOWN:
            return float(self._unknown_holds.get(record.idempotency_key, 0.0))
        return 0.0

    def occupied_cost(self) -> float:
        """**累计**已占用成本（历史 + 本次实际 + 预留 + 未知占位）。

        这是项目级累计闸门（`max_cost_total`）看到的那个数：它回答的是
        "这个库从头到现在已经花掉（或必须预留）多少"，不是"本次花了多少"。
        """
        return self.totals().accountable_cost() + self._history_cost

    def session_occupied_cost(self) -> float:
        """**本次进程**已占用成本（不含历史笔）；单任务上限用它。"""
        return self.totals().accountable_cost()

    def occupied_cost_for_task(self, task_id: str | None) -> float:
        """某个任务累计占用的成本（含该任务的历史笔）。

        `task_id` 为空 → 退化为 `occupied_cost()`（无法归属时按最保守口径）。
        单任务上限（`max_cost`）用这个数：它限定的是"这个任务能花多少"，
        不是"这本账能花多少"。
        """
        wanted = str(task_id or "")
        if not wanted:
            return self.occupied_cost()
        total = 0.0
        for store in (self._history, self._records):
            for record in store.values():
                if str(record.task_id or "") != wanted:
                    continue
                total += self._charged_cost(record)
        # 预留没有 task 归属（本进程内只有一个任务在花），计入本次的单任务口径
        total += sum(self._reservations.values())
        return total

    # ---------------------------------------------------------------- 闸门

    def would_exceed(self, max_cost: float | None, need: float) -> bool:
        """加上本次需求后会不会超上限。`max_cost=None` 表示不限制。"""
        if max_cost is None:
            return False
        return (self.occupied_cost() + need) > max_cost

    def should_pause(self, max_cost: float | None = None, need: float = 0.0) -> tuple[bool, str]:
        """保守策略：**宁可停手，不可透支**。

        两种停手：
        1. 会超成本上限（累计口径）；
        2. 本次进程里未知费用的请求累计达到上限 → 费用状态不明，停下来等人工核对。

        ⚠️ 第 2 条**只数本次进程的未知笔**：历史未知已经按保守金额计入
        `occupied_cost()`（见 `hydrate()`），不需要让闸门**永久**关闭——
        否则一条历史未知记录会让这本账再也无法使用，而 `confirm()` 只作用于
        内存，永远补不上。历史未知的责任落在"金额按上限估"，不是"永久停机"。
        """
        totals = self.totals()
        if max_cost is not None and (self.occupied_cost() + need) > max_cost:
            return True, f"费用上限：已占用 {self.occupied_cost():.2f} + 本次 {need:.2f}"
        if totals.unknown_requests >= self.max_unknown_requests:
            return True, (f"存在 {totals.unknown_requests} 笔费用状态未知的请求"
                          f"（上限 {self.max_unknown_requests}），需人工核对后再继续")
        return False, ""
