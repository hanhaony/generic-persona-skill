"""错误码与 `AppError` —— 对应 docs/ERROR_HANDLING.md §2。

三条纪律（ERROR_HANDLING §3）：
    EH-1 跨模块边界**不抛业务异常**，可预期的失败一律进 `Err(AppError)`；
    EH-2 只有 `INTERNAL` 与不变式被破坏时允许抛 Python 异常，且必须带 trace_ref；
    EH-3 禁止吞错。

⚠️ 与文档的差异（**已记录，待人工确认后回填 ERROR_HANDLING.md §2.2**）：
    实现需要，新增了两个文档里没有的错误码：
      - `VALIDATION.unknown_kind`   实体种类不在 `ENTITY_KINDS` 里
      - `VALIDATION.unknown_filter` 查询条件用了未登记的过滤键
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from .enums import _StrEnum


class ErrorCategory(_StrEnum):
    """错误类别（ERROR_HANDLING §2.1）。类别决定"能不能重试"。"""

    VALIDATION = "VALIDATION"                  # 输入/数据不合规，重试没用
    NOT_FOUND = "NOT_FOUND"                    # 引用的对象不存在
    STATE_CONFLICT = "STATE_CONFLICT"          # 当前状态不允许这个操作
    EXTERNAL_TIMEOUT = "EXTERNAL_TIMEOUT"      # 外部调用超时
    EXTERNAL_RATE_LIMIT = "EXTERNAL_RATE_LIMIT"
    EXTERNAL_UNAVAILABLE = "EXTERNAL_UNAVAILABLE"
    EXTERNAL_AUTH = "EXTERNAL_AUTH"            # 鉴权失败
    EXTERNAL_QUOTA = "EXTERNAL_QUOTA"          # 供应商配额耗尽
    STORAGE = "STORAGE"                        # 本地数据库读写失败 / 锁冲突
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"        # 预算耗尽 → 暂停，不是失败
    CANCELLED = "CANCELLED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"  # 违反 INV-1~INV-4，最严重
    INTERNAL = "INTERNAL"


class ErrorCode(_StrEnum):
    """具体错误码。取值与 ERROR_HANDLING §2.2 一致（另有 2 个新增，见模块顶部说明）。"""

    # —— 验证类 ——
    VALIDATION_REQUIRED_FIELD = "VALIDATION.required_field"
    VALIDATION_NAMESPACE_FORBIDDEN = "VALIDATION.namespace_forbidden"
    VALIDATION_TIER_QUOTE_MISSING = "VALIDATION.tier_quote_missing"
    VALIDATION_NO_DERIVATION = "VALIDATION.no_derivation"
    VALIDATION_GATE_REJECTED = "VALIDATION.gate_rejected"
    VALIDATION_EVIDENCE_REFS_INSUFFICIENT = "VALIDATION.evidence_refs_insufficient"
    VALIDATION_TOO_FEW_PARTICIPANTS = "VALIDATION.too_few_participants"
    VALIDATION_LIMIT_TOO_LARGE = "VALIDATION.limit_too_large"
    VALIDATION_QUERY_EMPTY = "VALIDATION.query_empty"
    VALIDATION_UNKNOWN_KIND = "VALIDATION.unknown_kind"        # 新增，待回填文档
    VALIDATION_UNKNOWN_FILTER = "VALIDATION.unknown_filter"    # 新增，待回填文档
    VALIDATION_CONFIG_INVALID = "VALIDATION.config_invalid"    # P2.2 新增：配置值非法/超范围

    # —— 找不到 ——
    NOT_FOUND_TASK = "NOT_FOUND.task"
    NOT_FOUND_PROJECT = "NOT_FOUND.project"
    NOT_FOUND_SOURCE = "NOT_FOUND.source"
    NOT_FOUND_ENTITY = "NOT_FOUND.entity"
    NOT_FOUND_VERSION = "NOT_FOUND.version"

    # —— 状态冲突 ——
    STATE_CONFLICT_NOT_PENDING = "STATE_CONFLICT.not_pending"
    STATE_CONFLICT_NOT_RUNNING = "STATE_CONFLICT.not_running"
    STATE_CONFLICT_NOT_PAUSED = "STATE_CONFLICT.not_paused"
    STATE_CONFLICT_TERMINAL_STATE = "STATE_CONFLICT.terminal_state"
    STATE_CONFLICT_SESSION_ENDED = "STATE_CONFLICT.session_ended"
    STATE_CONFLICT_NO_NEW_EVIDENCE = "STATE_CONFLICT.no_new_evidence"

    # —— 外部服务 ——
    EXTERNAL_TIMEOUT = "EXTERNAL_TIMEOUT"
    EXTERNAL_RATE_LIMIT = "EXTERNAL_RATE_LIMIT"
    EXTERNAL_UNAVAILABLE = "EXTERNAL_UNAVAILABLE"
    EXTERNAL_AUTH = "EXTERNAL_AUTH"
    EXTERNAL_QUOTA = "EXTERNAL_QUOTA"

    # —— 存储 ——
    STORAGE_READ_FAILED = "STORAGE.read_failed"
    STORAGE_WRITE_FAILED = "STORAGE.write_failed"
    STORAGE_CONFLICT = "STORAGE.conflict"

    # —— 预算 / 取消 / 不变式 ——
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    CANCELLED = "CANCELLED"
    INVARIANT_VIOLATION_ISOLATION = "INVARIANT_VIOLATION.isolation"
    INTERNAL = "INTERNAL"


@dataclass(frozen=True)
class ErrorSpec:
    """某个错误码的"性格"：属于哪类、能不能重试、能不能靠 resume 继续。"""

    category: ErrorCategory
    retryable: bool
    recoverable: bool
    user_hint: str = ""


# 类别级默认值（ERROR_HANDLING §2.1）
_CATEGORY_DEFAULT: dict[ErrorCategory, tuple[bool, bool]] = {
    ErrorCategory.VALIDATION: (False, False),
    ErrorCategory.NOT_FOUND: (False, False),
    ErrorCategory.STATE_CONFLICT: (False, False),
    ErrorCategory.EXTERNAL_TIMEOUT: (True, True),
    ErrorCategory.EXTERNAL_RATE_LIMIT: (True, True),
    ErrorCategory.EXTERNAL_UNAVAILABLE: (True, True),
    ErrorCategory.EXTERNAL_AUTH: (False, False),
    ErrorCategory.EXTERNAL_QUOTA: (False, False),
    ErrorCategory.STORAGE: (True, True),
    ErrorCategory.BUDGET_EXCEEDED: (False, True),   # 不可重试，但**可以续跑**
    ErrorCategory.CANCELLED: (False, False),
    ErrorCategory.INVARIANT_VIOLATION: (False, False),
    ErrorCategory.INTERNAL: (False, False),
}

ERROR_SPEC: dict[ErrorCode, ErrorSpec] = {}


def _register(code: ErrorCode, category: ErrorCategory, hint: str = "") -> None:
    retryable, recoverable = _CATEGORY_DEFAULT[category]
    ERROR_SPEC[code] = ErrorSpec(category, retryable, recoverable, hint)


for _code, _category, _hint in [
    (ErrorCode.VALIDATION_REQUIRED_FIELD, ErrorCategory.VALIDATION, "内部参数不完整"),
    (ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN, ErrorCategory.VALIDATION, "试图写入不允许的数据类型"),
    (ErrorCode.VALIDATION_TIER_QUOTE_MISSING, ErrorCategory.VALIDATION, "原话证据缺少原文"),
    (ErrorCode.VALIDATION_NO_DERIVATION, ErrorCategory.VALIDATION, "不允许无依据的推断"),
    (ErrorCode.VALIDATION_GATE_REJECTED, ErrorCategory.VALIDATION, "单次行为不得写入稳定性格"),
    (ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT, ErrorCategory.VALIDATION, "关系缺少证据支撑"),
    (ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS, ErrorCategory.VALIDATION, "至少需要两个对象"),
    (ErrorCode.VALIDATION_LIMIT_TOO_LARGE, ErrorCategory.VALIDATION, "查询条数超限"),
    (ErrorCode.VALIDATION_QUERY_EMPTY, ErrorCategory.VALIDATION, "空查询不会发起请求"),
    (ErrorCode.VALIDATION_UNKNOWN_KIND, ErrorCategory.VALIDATION, "未知的实体种类"),
    (ErrorCode.VALIDATION_UNKNOWN_FILTER, ErrorCategory.VALIDATION, "查询条件里有未登记的过滤键"),
    (ErrorCode.VALIDATION_CONFIG_INVALID, ErrorCategory.VALIDATION, "配置项取值非法或超出允许范围"),
    (ErrorCode.NOT_FOUND_TASK, ErrorCategory.NOT_FOUND, "找不到指定任务"),
    (ErrorCode.NOT_FOUND_PROJECT, ErrorCategory.NOT_FOUND, "找不到指定项目"),
    (ErrorCode.NOT_FOUND_SOURCE, ErrorCategory.NOT_FOUND, "找不到指定来源"),
    (ErrorCode.NOT_FOUND_ENTITY, ErrorCategory.NOT_FOUND, "找不到指定对象"),
    (ErrorCode.NOT_FOUND_VERSION, ErrorCategory.NOT_FOUND, "找不到指定版本"),
    (ErrorCode.STATE_CONFLICT_NOT_PENDING, ErrorCategory.STATE_CONFLICT, "任务已启动，不能重复启动"),
    (ErrorCode.STATE_CONFLICT_NOT_RUNNING, ErrorCategory.STATE_CONFLICT, "任务未在运行中，无法暂停"),
    (ErrorCode.STATE_CONFLICT_NOT_PAUSED, ErrorCategory.STATE_CONFLICT, "只有暂停中的任务能恢复"),
    (ErrorCode.STATE_CONFLICT_TERMINAL_STATE, ErrorCategory.STATE_CONFLICT, "任务已结束"),
    (ErrorCode.STATE_CONFLICT_SESSION_ENDED, ErrorCategory.STATE_CONFLICT, "会话已结束"),
    (ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE, ErrorCategory.STATE_CONFLICT, "没有新证据，不重复蒸馏"),
    (ErrorCode.EXTERNAL_TIMEOUT, ErrorCategory.EXTERNAL_TIMEOUT, "外部服务响应超时"),
    (ErrorCode.EXTERNAL_RATE_LIMIT, ErrorCategory.EXTERNAL_RATE_LIMIT, "外部服务限流，稍后自动继续"),
    (ErrorCode.EXTERNAL_UNAVAILABLE, ErrorCategory.EXTERNAL_UNAVAILABLE, "外部服务暂时不可用"),
    (ErrorCode.EXTERNAL_AUTH, ErrorCategory.EXTERNAL_AUTH, "凭据无效，请检查配置"),
    (ErrorCode.EXTERNAL_QUOTA, ErrorCategory.EXTERNAL_QUOTA, "供应商配额已用尽"),
    (ErrorCode.STORAGE_READ_FAILED, ErrorCategory.STORAGE, "读取本地数据失败"),
    (ErrorCode.STORAGE_WRITE_FAILED, ErrorCategory.STORAGE, "写入本地数据失败"),
    (ErrorCode.STORAGE_CONFLICT, ErrorCategory.STORAGE, "并发写冲突"),
    (ErrorCode.BUDGET_EXCEEDED, ErrorCategory.BUDGET_EXCEEDED, "已达预算上限，进度已保存，可之后继续"),
    (ErrorCode.CANCELLED, ErrorCategory.CANCELLED, "任务已被取消"),
    (ErrorCode.INVARIANT_VIOLATION_ISOLATION, ErrorCategory.INVARIANT_VIOLATION, "违反数据隔离红线，任务已停止"),
    (ErrorCode.INTERNAL, ErrorCategory.INTERNAL, "程序内部错误"),
]:
    _register(_code, _category, _hint)


@dataclass(frozen=True)
class AppError:
    """跨模块传递的错误对象（INTERFACES §1.4）。

    `retryable` 与 `recoverable` 默认取自 `ERROR_SPEC`，也允许调用方显式覆盖。
    """

    code: ErrorCode
    category: ErrorCategory
    message: str = ""
    retryable: bool = False
    recoverable: bool = False
    trace_ref: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def user_hint(self) -> str:
        return ERROR_SPEC[self.code].user_hint

    def with_context(self, **extra: Any) -> "AppError":
        """向上传递时**补充**上下文，但**不改写** code 与 category（ERROR_HANDLING §3.2）。"""
        merged = {**self.details, **extra}
        return replace(self, details=merged)

    def __str__(self) -> str:  # 让日志与 CLI 输出可读
        return f"[{self.code}] {self.message or self.user_hint}"


def new_error(
    code: ErrorCode,
    message: str = "",
    *,
    trace_ref: str = "",
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
    recoverable: bool | None = None,
) -> AppError:
    """创建 `AppError`，自动从 `ERROR_SPEC` 补齐类别与重试属性。"""
    spec = ERROR_SPEC[code]
    return AppError(
        code=code,
        category=spec.category,
        message=message or spec.user_hint,
        retryable=spec.retryable if retryable is None else retryable,
        recoverable=spec.recoverable if recoverable is None else recoverable,
        trace_ref=trace_ref,
        details=dict(details or {}),
    )


class InvariantViolationError(RuntimeError):
    """不变式（INV-1~INV-4）被破坏。

    这是**唯一**允许抛出的业务相关异常（ERROR_HANDLING EH-2），
    因为它代表实现缺陷，必须让任务立即停下并保留现场。
    """

    def __init__(self, message: str, *, trace_ref: str = "") -> None:
        super().__init__(f"{message} (trace_ref={trace_ref})")
        self.trace_ref = trace_ref
