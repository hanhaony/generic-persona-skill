"""`Result` 信封 —— 对应 docs/INTERFACES.md §1.4、§1.5。

为什么不用异常：把"失败"变成返回值的正常分支，调用方**不可能忘记处理**。

四类结果要分清（§1.5）：
    - 业务结果：成功，但"没找到 / 不够 / 有分歧"  → `Ok(...)` + `warnings`，**任务继续**
    - 运行错误：环境/外部出问题，重试可能成功    → `Err(AppError)`，`retryable=True`
    - 验证错误：输入不合规，重试没用             → `Err(AppError)`，`retryable=False`
    - 程序缺陷：不该发生                          → 抛 Python 异常，任务置 FAILED

⚠️ 冲突、资料不足、搜索 0 条结果都**不是错误**（D-055）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar, Union

from .errors import AppError

T = TypeVar("T")


@dataclass
class WarningNote:
    """业务提示（契约里叫 `Warning`，这里为了避免遮蔽内置类型改名为 `WarningNote`）。

    例：`SOURCE_DEDUP_HIT`（来源重复命中）、`CONFLICT_RECORDED`（记录了冲突）。
    **提示不是失败**，任务继续。
    """

    code: str
    message: str = ""


@dataclass
class UsageDelta:
    """一次外部调用消耗的资源（INTERFACES §2.4）。

    硬性要求（D-060）：token / 金额**未知时必须留 `None`，禁止填估计值**。

    **P4.5：估算与实际必须分开**（这是本轮真实运行教训的直接产物）。
    此前 `cost_estimate` 被直接填成响应里 `usage.credits` 的**实际值**，
    名字叫 estimate 却是实际值——估算与实际混在一个字段里，导致：

    - 请求**前**无法用它做预算判断（事前拿不到 credits）；
    - 响应缺 `usage` 时只能填 `None`，而 `None` 又被预算闸门当成"不花钱"跳过。

    现在拆成两个字段，语义各自独立：

    | 字段 | 含义 | 何时可得 | 用途 |
    | --- | --- | --- | --- |
    | `cost_estimate` | 按官方费率**推算**的估算 | 请求**前**即可算出 | 预算预留/事前判断 |
    | `cost_confirmed` | Provider 回报的**实际**值 | 仅在响应带回 `usage` 后 | 事后对账 |

    两者都保留 `None` 表示"未知"。**绝不用 0 代替未知**：0 是在断言"没花钱"，
    而"不知道"不是 0。请求失败 / 超时 / 响应缺 `usage` 时，
    `cost_confirmed=None` 且 `cost_status=UNKNOWN`。
    """

    requests: int = 1
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int = 0
    cost_estimate: float | None = None     # 估算（事前，按官方费率）
    cost_confirmed: float | None = None    # 实际（响应 usage.credits）
    currency: str = "UNKNOWN"
    # 费用状态与来源（让"这个数是估算还是实际、可不可信"随数据一起走）
    cost_status: str = "UNKNOWN"           # CONFIRMED / UNKNOWN / NOT_CHARGED
    cost_source: str = "NONE"              # PROVIDER_USAGE / OFFICIAL_RATE / NONE
    request_id: str | None = None          # 服务端 request_id（失败时为 None）


@dataclass
class Ok(Generic[T]):
    """成功。"""

    value: T
    warnings: list[WarningNote] = field(default_factory=list)
    usage: UsageDelta | None = None


@dataclass
class Err:
    """失败。"""

    error: AppError


# 类型别名，仅供类型标注使用
Result = Union[Ok[T], Err]


def is_ok(result: Result[T]) -> bool:
    """是不是成功。"""
    return isinstance(result, Ok)


def is_err(result: Result[T]) -> bool:
    """是不是失败。"""
    return isinstance(result, Err)


def unwrap(result: Result[T]) -> T:
    """取出成功值；失败则抛错（**只应在测试或确知成功的地方使用**）。"""
    if isinstance(result, Ok):
        return result.value
    raise AssertionError(f"尝试从失败结果取值：{result.error}")


def error_of(result: Result[T]) -> AppError | None:
    """取出错误对象；成功则返回 `None`。"""
    if isinstance(result, Err):
        return result.error
    return None


def with_warning(result: Ok[T], code: str, message: str = "") -> Ok[T]:
    """给成功结果追加一条提示（不改成失败）。"""
    result.warnings.append(WarningNote(code=code, message=message))
    return result
