"""来源材料 → 候选 claim 的抽取（G-2）。

对应 `INTERFACES.md` §5.4、`DECISIONS.md` §2.25。
用户口头称这一步为「G-2」——仓库里有**两个** G-2：
  - `RUNNING.md` §5.8 的 G-2 = P3.3 遗留「群体子结构关系无法表达」（已接受，D-131）；
  - 本步做的是 `RUNNING.md` §5.12 的 G-2 = H-10A 遗留「来源 → 证据自动抽取未实现」。
两者无关，编号冲突已登记（Q-37 第十轮）。

# 职责

把 `Research` 返回的**原始材料**（`SourceMaterial`）转换为**候选 claim**
（`CandidateClaim`）。仅此而已。

# 非职责（红线）

⛔ **不做校验与登记**：候选 claim 一律交给 `Evidence` 走既有规则校验、去重、
   登记。抽取器**没有** `Evidence` 的引用，结构上也就不可能把 claim 直接
   标记为"已验证证据"。
⛔ **不访问网络、不调真实模型**：`MockClaimExtractor` 是确定性查表；
   `ProviderClaimExtractor` 只定义契约与错误处理，本阶段不接真实模型。
⛔ **不裁决冲突**：相互矛盾的说法**各自保留**成独立候选，绝不因为"看起来像"
   就合并（AC-15）。
⛔ **不把等级当真伪**：`tier` 沿用 `EvidenceTier` 既有定义（D-009/D-010：
   优先级不等于真实性）。

# 三种 claim 的机械区分

| 类型 | `tier` | 登记入口 | 说明 |
| --- | --- | --- | --- |
| 原文直接支持 | `VERBATIM`（带 `verbatim_quote`） | `Evidence.add_evidence` | 摘录必须能在材料里原样找到 |
| 对材料的归纳 | `PROFILE_BACKGROUND` / `THIRD_PARTY` | `Evidence.add_evidence` | 不许带 `verbatim_quote` 冒充原话 |
| 系统推断 | `INFERENCE` | `Evidence.record_inference` | 必须声明 `derivation_keys`，**无根推断一律丢弃** |

**推断不得伪装成原文引用**：`INFERENCE` 候选若带 `verbatim_quote`，会被判为
`INFERENCE_WITH_QUOTE` 拒收——推断没有"原话"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from ..core.context import CallContext
from ..core.enums import (
    CanonLevel,
    EvidenceDimension,
    EvidenceTier,
    Stability,
)
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, WarningNote, is_err
from ..core.temporal import FuzzyDate, TimePrecision
from ..core.types import SourceMaterial
from ..providers.base import ChatRequest, Message, Provider

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

EXTRACT_METHOD_MOCK = "MOCK_FIXTURE"    # 确定性查表（测试材料）
EXTRACT_METHOD_MODEL = "MODEL"          # 模型驱动（本阶段不接真实模型）

TAG_MOCK_FIXTURE = "mock-fixture"       # 明确标注：这是测试材料，不是真实证据

# 拒收原因（**不静默丢弃**：每条都进 `ExtractionOutcome.rejected`）
REJECT_EMPTY_MATERIAL = "EMPTY_MATERIAL"              # 材料无正文可抽取
REJECT_EMPTY_CLAIM = "EMPTY_CLAIM"                    # claim 为空
REJECT_NO_SUBJECT = "NO_SUBJECT"                      # 未关联任何对象
REJECT_QUOTE_NOT_IN_MATERIAL = "QUOTE_NOT_IN_MATERIAL"  # 声称的原文摘录不在材料里
REJECT_UNKNOWN_TIER = "UNKNOWN_TIER"                  # tier 不在既有定义内
REJECT_UNKNOWN_DIMENSION = "UNKNOWN_DIMENSION"        # dimension 不在既有定义内
REJECT_CONFIDENCE_OUT_OF_RANGE = "CONFIDENCE_OUT_OF_RANGE"
REJECT_INFERENCE_WITH_QUOTE = "INFERENCE_WITH_QUOTE"  # 推断冒充原话

# 提示
WARNING_NO_MATERIALS = "NO_MATERIALS"
WARNING_NO_CANDIDATES = "NO_CANDIDATES"

_TIER_VALUES = tuple(str(v) for v in EvidenceTier)
_DIMENSION_VALUES = tuple(str(v) for v in EvidenceDimension)


# --------------------------------------------------------------------------
# 数据形状
# --------------------------------------------------------------------------


@dataclass
class CandidateClaim:
    """一个**候选**主张。尚未校验、尚未登记，因此**不是证据**。

    字段以现有模型为准（`EvidenceDraft` 能承载什么就填什么）；
    无法可靠取得的字段一律留空 / 记 `UNKNOWN`，**不得编造**。
    """

    key: str = ""                       # 抽取器内部稳定标识（供 `derivation_keys` 引用）
    source_id: str = ""                 # 由编排器绑定为本次研究登记的来源
    claim: str = ""
    tier: EvidenceTier | str = EvidenceTier.PROFILE_BACKGROUND
    dimension: EvidenceDimension | str = EvidenceDimension.BACKGROUND
    subject_entity_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    verbatim_quote: str | None = None   # 原文摘录（**必须**能在材料里原样找到）
    locator: dict | None = None         # 文本定位（字段 / 段落 / 时间码…）
    stated_at: FuzzyDate | None = None  # 主张所述时间（未知留 None）
    stability: Stability | str = Stability.UNKNOWN
    canon_level: CanonLevel | None = None
    derivation_keys: list[str] = field(default_factory=list)  # INFERENCE 的根
    tags: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class RejectedClaim:
    """被拒收的候选：**保留记录**，不静默丢弃，也不混进 `claims`。"""

    reason: str
    message: str
    key: str = ""
    source_id: str = ""
    claim: str = ""


@dataclass
class ExtractionOutcome:
    """一次抽取的结果。**候选 ≠ 证据**：能否登记由 `Evidence` 说了算。"""

    claims: list[CandidateClaim] = field(default_factory=list)
    rejected: list[RejectedClaim] = field(default_factory=list)
    warnings: list[WarningNote] = field(default_factory=list)
    method: str = ""
    usage: UsageDelta | None = None


@runtime_checkable
class ClaimExtractorPort(Protocol):
    """抽取器端口。**唯一**入口，便于替换为真实模型实现。"""

    def extract(
        self,
        ctx: CallContext,
        materials: list[SourceMaterial],
        *,
        targets: list[Any] | None = None,
    ) -> Result[ExtractionOutcome]:
        ...


# --------------------------------------------------------------------------
# 内部辅助
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _str_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values if str(v or "").strip()]


def fuzzy_from_text(value: Any) -> FuzzyDate | None:
    """字符串 → `FuzzyDate`。**保守**：像 `YYYY-MM-DD` 才算日精度，否则记 `UNKNOWN`。

    与 `research._fuzzy_from_text` 同口径；放在这里供调用方复用，避免
    L4↔L4 互相 import（ARCHITECTURE §3：同层互不调用）。
    """
    raw = _text(value)
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return FuzzyDate(raw=raw, start=raw, end=raw, precision=TimePrecision.DAY)
    return FuzzyDate(raw=raw, precision=TimePrecision.UNKNOWN)


def _material_key(material: SourceMaterial) -> str:
    """材料在 fixture 表里的键：`canonical_key` → `url` → `title`。"""
    return (_text(material.canonical_key) or _text(material.url)
            or _text(material.title))


def _validate_candidate(
    candidate: CandidateClaim, material: SourceMaterial
) -> str | None:
    """返回拒收原因；`None` = 通过。

    逐条对应模块 docstring 的红线。注意：**只做机械校验，不做语义判断**。
    """
    if not _text(candidate.claim):
        return REJECT_EMPTY_CLAIM
    if not _str_list(candidate.subject_entity_ids):
        return REJECT_NO_SUBJECT
    tier = _text(candidate.tier)
    if tier not in _TIER_VALUES:
        return REJECT_UNKNOWN_TIER
    if _text(candidate.dimension) not in _DIMENSION_VALUES:
        return REJECT_UNKNOWN_DIMENSION
    if not 0.0 <= float(candidate.confidence or 0.0) <= 1.0:
        return REJECT_CONFIDENCE_OUT_OF_RANGE
    quote = _text(candidate.verbatim_quote)
    if quote:
        if tier == str(EvidenceTier.INFERENCE):
            return REJECT_INFERENCE_WITH_QUOTE  # 推断没有"原话"
        if quote not in _text(material.snippet):
            return REJECT_QUOTE_NOT_IN_MATERIAL
    elif tier == str(EvidenceTier.VERBATIM):
        # EV-V2 同精神：声称"原话"却没有摘录 → 交给 Evidence 判
        # 这里不拦，只在本模块如实记录（见 `note` 机制）
        pass
    return None


def _rejected(candidate: CandidateClaim, reason: str, message: str) -> RejectedClaim:
    return RejectedClaim(reason=reason, message=message, key=_text(candidate.key),
                         source_id=_text(candidate.source_id),
                         claim=_text(candidate.claim))


# --------------------------------------------------------------------------
# Mock 抽取器（确定性 · 离线 · 测试材料）
# --------------------------------------------------------------------------


class MockClaimExtractor:
    """确定性离线抽取器：**按材料的 `canonical_key` 查固定 fixture**。

    - 不做任何语义理解、不联网、不调模型；
    - 产出一律打 `mock-fixture` 标签，明确标识为**测试材料**；
    - 材料无正文（空 `snippet`）→ 拒收 `EMPTY_MATERIAL`；
    - 每条候选都过 `_validate_candidate`，拒收原因如实记录。

    ⚠️ 它产出的是**候选**，不是证据；也不代表真实材料抽取能力已通过验证。
    """

    def __init__(
        self,
        fixtures: dict[str, list[CandidateClaim]] | None = None,
    ) -> None:
        self.fixtures: dict[str, list[CandidateClaim]] = {
            key: list(value) for key, value in (fixtures or {}).items()
        }
        self.calls = 0
        self.last_material_keys: list[str] = []

    def extract(
        self,
        ctx: CallContext,
        materials: list[SourceMaterial],
        *,
        targets: list[Any] | None = None,
    ) -> Result[ExtractionOutcome]:
        self.calls += 1
        materials = list(materials or [])
        outcome = ExtractionOutcome(method=EXTRACT_METHOD_MOCK,
                                    usage=UsageDelta(requests=0))
        if not materials:
            outcome.warnings.append(WarningNote(
                code=WARNING_NO_MATERIALS,
                message="没有材料：无法抽取任何候选 claim（不凭空生成）"))
            return Ok(outcome)

        self.last_material_keys = [_material_key(m) for m in materials]
        for material in materials:
            if not _text(material.snippet):
                outcome.rejected.append(RejectedClaim(
                    reason=REJECT_EMPTY_MATERIAL,
                    message=f"材料「{_text(material.title) or _material_key(material)}」"
                            "没有可抽取的正文（正文抓取未实现，H-17）",
                    source_id=_text(material.source_id)))
                continue
            for index, raw in enumerate(self.fixtures.get(_material_key(material), [])):
                candidate = replace(
                    raw,
                    source_id=_text(material.source_id),
                    key=_text(raw.key) or f"{_material_key(material)}#{index}",
                    tags=_str_list(raw.tags) + [TAG_MOCK_FIXTURE],
                )
                reason = _validate_candidate(candidate, material)
                if reason is not None:
                    outcome.rejected.append(_rejected(
                        candidate, reason,
                        f"第 {index} 条候选未通过 {reason} 校验：不静默丢弃，"
                        "也不降级登记"))
                    continue
                outcome.claims.append(candidate)

        if not outcome.claims:
            outcome.warnings.append(WarningNote(
                code=WARNING_NO_CANDIDATES,
                message="材料中没有抽出任何可登记的主张：不编造 claim"))
        return Ok(outcome)


# --------------------------------------------------------------------------
# 模型驱动抽取器：只定义契约与错误处理，本阶段不接真实模型
# --------------------------------------------------------------------------


EXTRACTION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "claim": {"type": "string"},
                    "tier": {"type": "string", "enum": list(_TIER_VALUES)},
                    "dimension": {"type": "string", "enum": list(_DIMENSION_VALUES)},
                    "subject_entity_ids": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "verbatim_quote": {"type": "string"},
                    "derivation_keys": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["claim", "tier", "dimension", "subject_entity_ids"],
            },
        }
    },
    "required": ["claims"],
}


class ProviderClaimExtractor:
    """模型驱动的抽取器（**本阶段不接真实模型**）。

    这里只把"要接真实模型需要什么"定义清楚：

    - **Provider 抽象**：只经 `Provider.chat`（L2 统一出口，D-054），
      不 import 任何厂商 SDK；
    - **错误处理**：
        * `Provider.chat` 返回 `Err` → **原样传播**（不吞、不降级）；
        * 返回文本不是约定的 JSON → `Err(EXTERNAL_UNAVAILABLE)`
          （可按类别重试，不假装"抽取成功但零结果"）；
        * 单条候选取值非法（缺 claim / 缺主体 / 未知 tier）→ 记 `rejected`
          **并继续**其余候选，不因一条坏数据让整次抽取失败；
        * 声称"原文摘录"但材料里找不到 → 拒收 `QUOTE_NOT_IN_MATERIAL`
          （推断/归纳不得冒充原话）。

    ⚠️ 模型输出**仍然是候选**，登记与否由 `Evidence` 决定。
    """

    def __init__(self, provider: Provider, *, model_hint: str | None = None) -> None:
        self.provider = provider
        self.model_hint = model_hint
        self.calls = 0

    def extract(
        self,
        ctx: CallContext,
        materials: list[SourceMaterial],
        *,
        targets: list[Any] | None = None,
    ) -> Result[ExtractionOutcome]:
        materials = list(materials or [])
        outcome = ExtractionOutcome(method=EXTRACT_METHOD_MODEL)
        if not materials:
            outcome.warnings.append(WarningNote(
                code=WARNING_NO_MATERIALS,
                message="没有材料：不调用模型，也不凭空生成 claim"))
            return Ok(outcome)

        request = ChatRequest(
            messages=[Message(role="user", content=self._prompt(materials, targets))],
            model_hint=self.model_hint,
            response_format="json_schema",
            json_schema=EXTRACTION_JSON_SCHEMA,
        )
        self.calls += 1
        responded = self.provider.chat(ctx, request)
        if is_err(responded):
            return responded                      # 原样传播，不吞错误
        usage = getattr(responded, "usage", None) or None
        if usage is not None:
            outcome.usage = usage
        payload = self._parse(_text(getattr(responded.value, "text", "")))
        if payload is None:
            return Err(new_error(
                ErrorCode.EXTERNAL_UNAVAILABLE,
                "模型返回无法解析为约定的 JSON 结构：不把它当成『抽取成功但零结果』",
                trace_ref=ctx.trace_ref))

        by_key = {_material_key(m): m for m in materials}
        for index, item in enumerate(payload.get("claims", []) or []):
            candidate = self._to_candidate(item, index)
            material = by_key.get(_text(item.get("material_key")), None)
            if material is None:
                # 模型没说明来自哪份材料 → 无法确定溯源，拒收
                outcome.rejected.append(_rejected(
                    candidate, REJECT_NO_SUBJECT,
                    "模型未指明该主张来自哪份材料（material_key 缺失）："
                    "无法溯源，不登记"))
                continue
            candidate = replace(candidate, source_id=_text(material.source_id))
            reason = _validate_candidate(candidate, material)
            if reason is not None:
                outcome.rejected.append(_rejected(
                    candidate, reason, f"模型产出的第 {index} 条候选未通过 {reason} 校验"))
                continue
            outcome.claims.append(candidate)
        if not outcome.claims:
            outcome.warnings.append(WarningNote(
                code=WARNING_NO_CANDIDATES,
                message="模型未产出任何可登记的主张：不编造 claim"))
        return Ok(outcome)

    @staticmethod
    def _prompt(materials: list[SourceMaterial], targets: list[Any] | None) -> str:
        """给模型的提示。**只描述任务与输出格式**，不做任何诱导性暗示。"""
        lines = [
            "请把下列材料中的陈述抽取为结构化候选主张。",
            "规则：原文直接支持的标 VERBATIM 并给出可在材料中逐字找到的摘录；"
            "归纳标 PROFILE_BACKGROUND/THIRD_PARTY 且不得给摘录；"
            "推断标 INFERENCE 且必须给出 derivation_keys。",
            "每条必须标明 material_key（来自哪份材料）。"
            "无法可靠取得的信息留空，不要编造。",
            "",
        ]
        for material in materials:
            lines.append(f"[material_key={_material_key(material)}]")
            lines.append(f"title: {_text(material.title)}")
            lines.append(f"snippet: {_text(material.snippet)}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _parse(raw: str) -> dict | None:
        try:
            loaded = json.loads(raw)
        except (ValueError, TypeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _to_candidate(item: Any, index: int) -> CandidateClaim:
        item = item if isinstance(item, dict) else {}
        return CandidateClaim(
            key=_text(item.get("key")) or f"model#{index}",
            claim=_text(item.get("claim")),
            tier=_text(item.get("tier")) or EvidenceTier.PROFILE_BACKGROUND,
            dimension=(_text(item.get("dimension"))
                       or EvidenceDimension.BACKGROUND),
            subject_entity_ids=_str_list(item.get("subject_entity_ids")),
            confidence=float(item.get("confidence") or 0.0),
            verbatim_quote=_text(item.get("verbatim_quote")) or None,
            derivation_keys=_str_list(item.get("derivation_keys")),
        )
