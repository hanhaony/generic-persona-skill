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


# --------------------------------------------------------------------------
# 账本
# --------------------------------------------------------------------------


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
        self._reservations: dict[str, float] = {}
        self._unknown_holds: dict[str, float] = {}

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

    def totals(self) -> UsageTotals:
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

    def occupied_cost(self) -> float:
        """当前已被占用的成本（实际 + 预留 + 未知占位），用于请求前的保守判断。"""
        return self.totals().accountable_cost()

    # ---------------------------------------------------------------- 闸门

    def would_exceed(self, max_cost: float | None, need: float) -> bool:
        """加上本次需求后会不会超上限。`max_cost=None` 表示不限制。"""
        if max_cost is None:
            return False
        return (self.occupied_cost() + need) > max_cost

    def should_pause(self, max_cost: float | None = None, need: float = 0.0) -> tuple[bool, str]:
        """保守策略：**宁可停手，不可透支**。

        两种停手：
        1. 会超成本上限；
        2. 未知费用的请求累计达到上限 → 费用状态不明，停下来等人工核对。
        """
        totals = self.totals()
        if max_cost is not None and (totals.accountable_cost() + need) > max_cost:
            return True, f"费用上限：已占用 {totals.accountable_cost():.2f} + 本次 {need:.2f}"
        if totals.unknown_requests >= self.max_unknown_requests:
            return True, (f"存在 {totals.unknown_requests} 笔费用状态未知的请求"
                          f"（上限 {self.max_unknown_requests}），需人工核对后再继续")
        return False, ""
