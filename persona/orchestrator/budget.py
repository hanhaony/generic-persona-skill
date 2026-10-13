"""预算闸门 —— 对应 docs/ERROR_HANDLING.md §5、INTERFACES.md §12.4。

**四段式闸门（D-089）**：`检查 → 预占 → 执行 → 记账`。
检查必须发生在**任何资源被消耗之前**；不足时**先落检查点再暂停**（D-043），
并且返回的是 `Ok(paused=True)` 而不是 `Err`——超预算**不是失败**。

给初学者的比喻：
    预算就像一张预付卡。买东西之前先问"卡里够不够"（① 检查），
    够就先把钱**冻住**（② 预占，防止两个人同时刷卡超支），
    真正付款（③ 执行），最后把冻结的钱**扣掉**（④ 记账）。
    钱不够时不是"交易失败"，而是"先存着购物车，等充值再接着买"（暂停）。

⚠️ 诚实边界：本模块只做**纯计算**，不读时钟、不读配置、不碰数据库。
    用时是否真的超限（墙钟时间）需要 P5 的任务引擎来接。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, WarningNote, with_warning
from .records import BudgetSpec
from ..core.types import BudgetUsage

# 告警阈值：消耗到 80% 时提示，**不改变行为**（ERROR_HANDLING §5.2）
NEAR_LIMIT_RATIO = 0.8
WARNING_BUDGET_NEAR_LIMIT = "BUDGET_NEAR_LIMIT"

# 记账时"未知"的处理：token 未知按 0 累计，次数一定会计（ERROR_HANDLING §5.2）
UNKNOWN_TOKENS_ARE_ZERO = True


@dataclass
class BudgetGate:
    """一次任务的预算闸门。持有"上限 + 已用 + 预占"三个数。"""

    limit: BudgetSpec
    used: BudgetUsage = field(default_factory=BudgetUsage)
    reserved: BudgetUsage = field(default_factory=BudgetUsage)

    # ---------------------------------------------------------------- 余量

    def _remaining(self, limit: int | None, used: int, reserved: int) -> int | None:
        if limit is None:
            return None  # 不限制
        return limit - used - reserved

    def remaining_requests(self) -> int | None:
        return self._remaining(self.limit.max_requests, self.used.requests, self.reserved.requests)

    def remaining_tokens_in(self) -> int | None:
        return self._remaining(
            self.limit.max_tokens_in, self.used.tokens_in, self.reserved.tokens_in
        )

    def remaining_tokens_out(self) -> int | None:
        return self._remaining(
            self.limit.max_tokens_out, self.used.tokens_out, self.reserved.tokens_out
        )

    def remaining_cost(self) -> float | None:
        if self.limit.max_cost is None:
            return None
        return self.limit.max_cost - self._cost_used()

    def _cost_used(self) -> float:
        return (self.used.cost_estimate or 0.0) + (self.reserved.cost_estimate or 0.0)

    # ---------------------------------------------------------------- 判断

    def _over(self, remaining: int | None, need: int) -> bool:
        return remaining is not None and need > remaining

    def would_exceed(self, delta: UsageDelta) -> bool:
        """加上 `delta` 之后会不会超。**不修改任何状态**（纯查询）。"""
        if self._over(self.remaining_requests(), max(delta.requests, 0)):
            return True
        tokens_in = delta.tokens_in or 0
        tokens_out = delta.tokens_out or 0
        if self._over(self.remaining_tokens_in(), tokens_in):
            return True
        if self._over(self.remaining_tokens_out(), tokens_out):
            return True
        remaining_cost = self.remaining_cost()
        if remaining_cost is not None and delta.cost_estimate is not None:
            if delta.cost_estimate > remaining_cost:
                return True
        return False

    def max_ratio(self, delta: UsageDelta | None = None) -> float:
        """当前（或加上 delta 后）消耗占上限的最大比例，用于 80% 告警。"""
        need_requests = (delta.requests if delta else 0) or 0
        need_in = (delta.tokens_in if delta else None) or 0
        need_out = (delta.tokens_out if delta else None) or 0
        ratios: list[float] = []
        if self.limit.max_requests:
            ratios.append((self.used.requests + self.reserved.requests + need_requests)
                          / self.limit.max_requests)
        if self.limit.max_tokens_in:
            ratios.append((self.used.tokens_in + self.reserved.tokens_in + need_in)
                          / self.limit.max_tokens_in)
        if self.limit.max_tokens_out:
            ratios.append((self.used.tokens_out + self.reserved.tokens_out + need_out)
                          / self.limit.max_tokens_out)
        if self.limit.max_cost:
            cost = self._cost_used() + ((delta.cost_estimate if delta else None) or 0.0)
            ratios.append(cost / self.limit.max_cost)
        return max(ratios) if ratios else 0.0

    # ---------------------------------------------------------------- 闸门

    def check(self, delta: UsageDelta, *, trace_ref: str = "") -> Result[None]:
        """① 检查：**在任何消耗之前调用**。

        - 超限 → `Err(BUDGET_EXCEEDED)`（retryable=False，recoverable=True）
        - 达 80% → `Ok(None)` + `warnings=[BUDGET_NEAR_LIMIT]`（不改变行为）
        """
        if self.would_exceed(delta):
            return Err(
                new_error(
                    ErrorCode.BUDGET_EXCEEDED,
                    "已达预算上限；进度已保存，提高额度后可继续",
                    trace_ref=trace_ref,
                    details={
                        "need": {
                            "requests": delta.requests,
                            "tokens_in": delta.tokens_in,
                            "tokens_out": delta.tokens_out,
                        },
                        "remaining": {
                            "requests": self.remaining_requests(),
                            "tokens_in": self.remaining_tokens_in(),
                            "tokens_out": self.remaining_tokens_out(),
                        },
                    },
                )
            )
        ratio = self.max_ratio(delta)
        if ratio >= NEAR_LIMIT_RATIO:
            return with_warning(
                Ok(None),
                WARNING_BUDGET_NEAR_LIMIT,
                f"预算已使用 {ratio:.0%}（达 {NEAR_LIMIT_RATIO:.0%} 阈值提示，行为不变）",
            )
        return Ok(None)

    def reserve(self, delta: UsageDelta) -> None:
        """② 预占：把额度先冻住，防并发超额。"""
        self.reserved.requests += max(delta.requests, 0)
        if delta.tokens_in is not None:
            self.reserved.tokens_in += delta.tokens_in
        if delta.tokens_out is not None:
            self.reserved.tokens_out += delta.tokens_out
        if delta.cost_estimate is not None:
            self.reserved.cost_estimate = (self.reserved.cost_estimate or 0.0) + delta.cost_estimate

    def _unreserve(self, delta: UsageDelta) -> None:
        """释放预占。**夹在 0 上**：即使调用方忘了先 `reserve`，也不会把余量算成负数。

        （这属于防御性写法：`reserve` / `commit` / `release` 应当成对出现，
        但记错顺序不该让"剩余额度"变成负数——那会让闸门判断失真。）
        """
        self.reserved.requests = max(0, self.reserved.requests - max(delta.requests, 0))
        if delta.tokens_in is not None:
            self.reserved.tokens_in = max(0, self.reserved.tokens_in - delta.tokens_in)
        if delta.tokens_out is not None:
            self.reserved.tokens_out = max(0, self.reserved.tokens_out - delta.tokens_out)
        if delta.cost_estimate is not None:
            self.reserved.cost_estimate = max(
                0.0, (self.reserved.cost_estimate or 0.0) - delta.cost_estimate
            )

    def commit(self, delta: UsageDelta) -> BudgetUsage:
        """④ 记账：释放预占 + 累加到已用。返回累加后的 `used`。"""
        self._unreserve(delta)
        self.used.add(
            requests=max(delta.requests, 0),
            tokens_in=delta.tokens_in,
            tokens_out=delta.tokens_out,
            latency_ms=delta.latency_ms,
        )
        if delta.cost_estimate is not None:
            self.used.cost_estimate = (self.used.cost_estimate or 0.0) + delta.cost_estimate
            self.used.currency = delta.currency or self.used.currency
        return self.used

    def release(self, delta: UsageDelta) -> None:
        """调用失败（或根本没发出去）时释放预占，**不计入已用**。"""
        self._unreserve(delta)


def gate_for(limit: BudgetSpec | None, used: BudgetUsage | None = None) -> BudgetGate:
    """按任务的上限与已用构造闸门。`limit=None` 表示不限制。"""
    return BudgetGate(limit=limit or BudgetSpec(), used=used or BudgetUsage())
