"""时间表示 —— 对应 docs/DATA_MODEL.md §2.3、INTERFACES.md §1.2。

两条规则：
    1. 精确时间统一用 **UTC**，序列化成 ISO-8601（`2026-10-13T12:00:00Z`）；
    2. 模糊时间（"九十年代初"、"第 4 集之后"）必须用 `FuzzyDate` 表达，
       **禁止**塞进单个 datetime 字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .enums import _StrEnum  # 仅内部复用字符串枚举基类


class TimePrecision(_StrEnum):
    EXACT = "EXACT"
    DAY = "DAY"
    MONTH = "MONTH"
    YEAR = "YEAR"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"
    WORK_INTERNAL = "WORK_INTERNAL"  # 作品内部时间，如"第 4 集之后"


def utc_now() -> datetime:
    """当前 UTC 时间（带时区信息）。"""
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    """转成 ISO-8601 字符串，末尾带 `Z`。"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(text: str) -> datetime:
    """从 ISO-8601 字符串解析回 datetime（统一成 UTC）。"""
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class FuzzyDate:
    """模糊时间：原话 + 可比较的区间 + 精度。"""

    raw: str
    start: str | None = None      # ISO 日期或日期时间
    end: str | None = None
    precision: TimePrecision = TimePrecision.UNKNOWN
