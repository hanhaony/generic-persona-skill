"""模型调用的**预算闸门**与**逐笔记账**（抽取器与对话共用一套）。

# 为什么单独一个模块

P4.8 R4 的真实运行暴露了 D-3：对话阶段的模型调用**没有任何记账出口**——
token 只落在 `ConversationSession.budget_used` 里，`external_usage_records`
一行都没有。后果是双重的：

- **累计闸门失效**：`max_cost_total` 看不到对话花掉的钱；
- **逐笔对账失效**：账单里有一笔扣费，库里找不到对应记录。

修法不是"给对话再写一套"，而是把抽取器里已经验收过的闸门与结算口径提到这里，
两条通道调用**同一个** `check_model_budget()` / `settle_model_call()`。
否则迟早变成"两条通道两套账"，而账本最不能容忍的就是两套口径。

# 闸门四条规则（与 P4.4-A / P4.6-G11 完全一致，只有一份实现）

1. 次数上限用满 → 停手；
2. 设了费用上限但**单价未知** → 无法估算 → 不发起请求（费用未知 ≠ 零）；
3. **单任务**上限（`task_id` 累计，含同任务历史）超限 → 停；
4. **项目累计**上限（整本账，含 hydrate 读回的历史）超限 → 停；
   历史里存在"未知且无任何估算"的笔数时金额无法界定 → 同样停手。

# 三条记账纪律

1. **未知不填 0**：响应缺 usage / 超时 / 断连 → `cost_status=UNKNOWN`；
2. **失败不免费**：失败时同样记一笔（`outcome=FAILED` + `UNKNOWN`），
   预留转为未知占位继续占用额度；
3. **实际优先于估算**：`cost_confirmed` 取响应回报；没有才按官方单价用真实
   token 数折算；两者都没有 → 未知。

# 谁**不**记账

离线替身（`FakeProvider` 或显式声明 `offline = True` 的桩）**不产生外部用量**，
因为请求根本没有离开进程。这类调用既不预留、也不落账——把它们记成
"UNKNOWN" 会让账本里凭空多出 1 credit 的未知占位（`unknown_hold_credits`），
下一笔真实调用就会被闸门误判为"存在未知费用"而停手。

⚠️ 反过来说：**没声明 `offline` 的 Provider 一律按真实出口记账**。
宁可多记一笔本地调用的账（金额按配置单价算，通常是几厘），
也不要漏掉一笔真实扣费。
"""

from __future__ import annotations

from typing import Any

from ..core.context import CallContext
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, UsageDelta
from ..core.temporal import to_iso, utc_now
from ..core.types import (
    COST_CONFIRMED,
    COST_UNKNOWN,
    CURRENCY_UNKNOWN,
    OP_CHAT,
    OUTCOME_SUCCEEDED,
    SOURCE_NONE,
    SOURCE_OFFICIAL_RATE,
    SOURCE_PROVIDER_USAGE,
    ExternalUsageRecord,
)
from ..core.usage_ledger import (
    ExternalUsageLedger,
    ModelCallLimits,
    cost_from_model_usage,
    estimate_model_cost,
)

#: 声明为离线替身时用的属性名。Provider 只要带 `offline = True` 就不会被记账。
OFFLINE_FLAG = "offline"


def is_offline_provider(provider: Any) -> bool:
    """这个 Provider 是不是**进程内的替身**（不产生外部用量）。

    判据是**显式声明**（`offline = True`），不是类型名猜测：

    - `FakeProvider` 自带该标记（它就是"确定性假实现"）；
    - 脚本/测试里的桩需要自己加上这一行——加标记是一个显式动作，
      漏加只会导致**多记一笔本地账**，不会漏记真实扣费。
    """
    if provider is None:
        return True
    return bool(getattr(provider, OFFLINE_FLAG, False))


def count_chat_calls(ledger: ExternalUsageLedger | None, fallback: int) -> int:
    """本次进程已经发生的 CHAT 次数（没有账本时退回调用方自己的计数器）。

    整个流程共用一本账时，抽取与对话的次数**合并计入** `max_calls`——
    这才是"这次运行最多花几次模型调用"的正确含义。
    """
    if ledger is None:
        return int(fallback or 0)
    return sum(1 for record in ledger.records() if record.operation == OP_CHAT)


def check_model_budget(
    ctx: CallContext,
    *,
    ledger: ExternalUsageLedger | None,
    limits: ModelCallLimits | None,
    task_id: str | None,
    calls_made: int,
    key: str,
) -> Err | None:
    """**发送之前**的闸门。返回 `Err` 表示不许发（`BUDGET_EXCEEDED` 是暂停信号）。

    `limits is None` → 不设闸门（也不预留），保持"未配置限额就不拦"的既有语义。
    """
    if limits is None:
        return None
    if limits.max_calls is not None and calls_made >= limits.max_calls:
        return Err(new_error(
            ErrorCode.BUDGET_EXCEEDED,
            f"模型调用次数已达上限 {limits.max_calls}：不再自动发起请求",
            trace_ref=ctx.trace_ref))
    estimate = estimate_model_cost(limits)
    has_cost_cap = (limits.max_cost is not None
                    or limits.max_cost_total is not None)
    if has_cost_cap and estimate is None:
        return Err(new_error(
            ErrorCode.BUDGET_EXCEEDED,
            "未配置模型单价（price_per_1k_*），无法估算费用；"
            "在设了费用上限的情况下不能保证不超支 → 不自动发起请求"
            "（费用未知不等于零）",
            trace_ref=ctx.trace_ref))
    if ledger is not None and estimate is not None:
        # ① 单任务上限：这个任务累计（含同任务历史）不能超
        if limits.max_cost is not None:
            task_cost = ledger.occupied_cost_for_task(task_id)
            if task_cost + estimate > limits.max_cost:
                return Err(new_error(
                    ErrorCode.BUDGET_EXCEEDED,
                    f"单任务费用上限：该任务已占用 {task_cost:.4f}"
                    f"{limits.currency} + 本次估算 {estimate:.4f}"
                    f" 超过 {limits.max_cost}",
                    trace_ref=ctx.trace_ref))
        # ② 项目累计上限：整本账（含历史）不能超
        if limits.max_cost_total is not None:
            if ledger.unpriced_unknown_requests() > 0:
                return Err(new_error(
                    ErrorCode.BUDGET_EXCEEDED,
                    f"项目累计费用上限已设，但账本里有 "
                    f"{ledger.unpriced_unknown_requests()} 笔历史费用状态未知、"
                    "且没有任何估算可用——金额无法界定，不能保证不超累计上限 "
                    f"{limits.max_cost_total}（费用未知不等于零）",
                    trace_ref=ctx.trace_ref))
            total = ledger.occupied_cost()
            if total + estimate > limits.max_cost_total:
                return Err(new_error(
                    ErrorCode.BUDGET_EXCEEDED,
                    f"项目累计费用上限：账本已占用 {total:.4f}"
                    f"{limits.currency}（含历史）+ 本次估算 {estimate:.4f}"
                    f" 超过 {limits.max_cost_total}",
                    trace_ref=ctx.trace_ref))
    if ledger is not None:
        # 只判"本次进程的未知笔数"；费用上限已在上面分层判过
        pause, why = ledger.should_pause()
        if pause:
            return Err(new_error(
                ErrorCode.BUDGET_EXCEEDED, f"模型调用暂停：{why}",
                trace_ref=ctx.trace_ref))
        ledger.reserve(key, estimate)
    return None


def settle_model_call(
    ctx: CallContext,
    *,
    ledger: ExternalUsageLedger | None,
    usage_sink: Any,
    provider_name: str,
    key: str,
    outcome_kind: str,
    usage: UsageDelta | None,
    limits: ModelCallLimits | None,
    task_id: str | None,
    operation: str = OP_CHAT,
) -> ExternalUsageRecord:
    """结算一笔**已经发出**的模型调用，并写进账本与持久层。

    **必须在调用返回（或抛异常）之后立刻调用**，早于任何"结果是否可用"的判断：
    一次返回了空内容的请求同样花过钱，不能因为"业务上没用"就不记账。

    幂等由账本的 `settle()` 保证（同一个 key 只记一次）；`usage_sink`
    负责跨进程留痕（`SqliteStorage.record_external_usage`）。
    """
    estimate = estimate_model_cost(limits) if limits is not None else None
    tokens_in = getattr(usage, "tokens_in", None) if usage is not None else None
    tokens_out = getattr(usage, "tokens_out", None) if usage is not None else None
    confirmed: float | None = None
    source = SOURCE_NONE
    if usage is not None and getattr(usage, "cost_confirmed", None) is not None:
        confirmed = float(getattr(usage, "cost_confirmed"))
        source = SOURCE_PROVIDER_USAGE
    elif limits is not None:
        confirmed = cost_from_model_usage(limits, tokens_in=tokens_in,
                                          tokens_out=tokens_out)
        if confirmed is not None:
            source = SOURCE_OFFICIAL_RATE
    status = COST_CONFIRMED if confirmed is not None else COST_UNKNOWN

    record = ExternalUsageRecord(
        idempotency_key=key,
        provider=provider_name,
        operation=operation,
        outcome=outcome_kind,
        requested_at=to_iso(utc_now()),
        request_id=(str(getattr(usage, "request_id", "") or "") or None
                    if usage is not None else None),
        unit_count=1,
        success_unit_count=(1 if outcome_kind == OUTCOME_SUCCEEDED else None),
        cost_estimate=estimate,
        cost_confirmed=confirmed,
        currency=(limits.currency if limits is not None else CURRENCY_UNKNOWN),
        cost_source=source,
        cost_status=status,
        task_id=task_id,
        trace_ref=ctx.trace_ref,
    )
    if ledger is not None:
        ledger.settle(record)
    if usage_sink is not None:
        record_fn = getattr(usage_sink, "record_external_usage", None)
        if callable(record_fn):
            record_fn(ctx, record)
    return record
