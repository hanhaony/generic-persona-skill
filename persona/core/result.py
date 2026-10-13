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
    """

    requests: int = 1
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int = 0
    cost_estimate: float | None = None
    currency: str = "UNKNOWN"


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
