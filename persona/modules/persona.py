"""Persona —— 人物人格档案基础模块（docs/INTERFACES.md §6、DATA_MODEL §4.7）。

**P3 第一步**（用户称之为 P3.2；仓库 ROADMAP 的 P3 = 人物档案 + 关系档案，
编号差异见 DECISIONS §3 Q-37）：**本地管理基础**——档案的创建、查询、
更新（版本化）、持久化、证据关联与追溯、不确定性表达、冲突保留、
虚构角色版本边界、模拟内容隔离。

**职责边界（INTERFACES §6.1）**：
    ✅ 档案存储与版本管理、证据关联校验（PF-V1~V6）、稳定性闸门（D-012）、
       冲突关联（不裁决）、覆盖度统计
    ⛔ **不直接调用任何具体模型供应商**（F-5）：本文件不 import Provider，
       没有任何 search / collect / query_plan——检索是 `Research` 的事。
    ⛔ **不实现 `distill` 的 AI 蒸馏主体**（INTERFACES §6.2 的
       `distill(ctx, entity_id, options)` 要调 `Provider.chat`）：那属于
       下一阶段。本模块提供它落盘前**必须经过**的存储/校验/版本层。
    ⛔ 不改证据、不写记忆、不做关系建模。

**三条硬规则**（与证据模块一致的精神）：
    - 每个非空档案条目必须带 `evidence_refs`；没证据就留空并记
      `open_questions`（D-020），**不允许靠想象补全**。
    - `confidence` 只用于排序展示，**不代表真伪**（D-010 / PF-V4）；
      结构上没有任何"真实性"字段。
    - `namespace` 恒为 `SYSTEM_DERIVED`（PF-V6）：档案是加工产物，
      模拟内容永远不能变成档案依据（模块层 + 存储层双层闸门）。

**诚实边界（本阶段不做的）**：
    - `distill`（AI 蒸馏）未实现——不调模型，不假装会"读证据写档案"；
      本模块的写入入口是 `create_profile` / `revise`（内容由调用方给出）。
    - 语义级质量判断（"这条性格总结得准不准"）需要模型，未实现；
      已实现的是**确定性结构校验**（PF-V1~V6 + 版本边界 + 引用完整性）。
    - Q-19 的"保留最近 5 版、更早归档为摘要"未实现：当前**全版本保留**
      （不删用户数据），裁剪策略待拍板。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.context import CallContext
from ..core.enums import (
    EntityType,
    ProfileStatus,
    ProvenanceNamespace,
    Stability,
)
from ..core.errors import ErrorCode, new_error
from ..core.ids import new_id, prefix_of
from ..core.result import Err, Ok, Result, is_err, unwrap, with_warning
from ..core.temporal import FuzzyDate, TimePrecision, to_iso, utc_now
from ..core.types import MAX_QUERY_LIMIT, Page, Query
from ..storage.sqlite import SqliteStorage

# --------------------------------------------------------------------------
# 警告码（业务提示，不是错误；ERROR_HANDLING §2.3）
# --------------------------------------------------------------------------

WARNING_PROFILE_SUPERSEDED = "PROFILE_SUPERSEDED"    # 修订产生新版本，旧版已归档
WARNING_CONFLICT_LINKED = "CONFLICT_LINKED"          # 档案关联了冲突（未裁决）
# G7（A 级写风险）：本次修订**产出为空**，已拦下、旧版保留（不降级覆盖）
WARNING_PROFILE_DEGRADED_SKIPPED = "PROFILE_DEGRADED_SKIPPED"

# 档案的六个板块（DATA_MODEL §4.7；覆盖度统计的键）
SECTION_BACKGROUND = "background"
SECTION_TRAITS = "traits"
SECTION_HABITS = "habits"
SECTION_EXPRESSION_STYLE = "expression_style"
SECTION_EXPERIENCES = "experiences"
SECTION_SINGLE_EVENTS = "single_events"

# 单块文本型板块（ProfileSection）
TEXT_SECTIONS: tuple[str, ...] = (SECTION_BACKGROUND, SECTION_EXPRESSION_STYLE)
# 条目列表型板块（list[ProfileItem] / list[TimelineItem]）
ITEM_SECTIONS: tuple[str, ...] = (SECTION_TRAITS, SECTION_HABITS,
                                  SECTION_SINGLE_EVENTS, SECTION_EXPERIENCES)
ALL_SECTIONS: tuple[str, ...] = TEXT_SECTIONS + ITEM_SECTIONS

# 稳定性闸门适用的板块（PF-V2 / D-012）：只有 traits 受闸门约束
GATED_SECTIONS: tuple[str, ...] = (SECTION_TRAITS,)
# traits 允许的稳定性（PF-V2：SINGLE 一律拒绝）
TRAIT_STABILITY: frozenset[str] = frozenset(
    {str(Stability.REPEATED), str(Stability.PATTERN)})

# query_profiles 允许的过滤键（payload 字段 + 结构列）
PROFILE_FILTER_KEYS: frozenset[str] = frozenset(
    {"entity_id", "status", "version", "applicable_variant_ids", "namespace"})
# 允许排序的列（storage SORT_KEYS + version：按版本号排序历史）
SORTABLE_COLUMNS: frozenset[str] = frozenset(
    {"created_at", "updated_at", "id", "version"})


def _normalize_items(items: Any) -> list:
    """把 None / 单项 / 列表统一成列表。"""
    if items is None:
        return []
    if isinstance(items, (list, tuple)):
        return list(items)
    return [items]


#: `evidence_anchors` 允许的键（**白名单**：多余的键一律丢弃）
ANCHOR_KEYS: tuple[str, ...] = (
    "feature", "dimension", "subjects", "evidence_ids", "source_ids",
    "evidence_count", "source_count", "in_traits",
)


def _anchor_view(anchors: Any) -> list[dict]:
    """只读锚点视图的**落盘前归一化**：按白名单取键，计数**重算**。

    为什么计数要重算而不是照抄：`evidence_anchors` 的立身之本是"只读、由服务算"。
    若照抄调用方给的 `source_count`，就退化成另一个可填报的字段——那它和
    `traits` 的区别只剩"放在哪个键里"。重算后，调用方**可以留空、不可以编造**：
    声称 5 个来源却只列 2 个，落盘一律记 2。

    键的**内容**仍来自构建方（本模块无法凭 ID 反查来源），所以这不是防篡改，
    是**防自相矛盾**——与 `coverage` 同级的可信度。
    """
    out: list[dict] = []
    for raw in _normalize_items(anchors):
        if not isinstance(raw, dict):
            continue
        #: 去重后计数：问的是"几条**不同**来源/证据提到"，同一条列两遍不算两条
        evidence_ids = list(dict.fromkeys(
            str(x) for x in _normalize_items(raw.get("evidence_ids"))
            if str(x or "").strip()))
        source_ids = list(dict.fromkeys(
            str(x) for x in _normalize_items(raw.get("source_ids"))
            if str(x or "").strip()))
        subjects = [str(x) for x in _normalize_items(raw.get("subjects"))
                    if str(x or "").strip()]
        out.append({
            "feature": str(raw.get("feature") or ""),
            "dimension": str(raw.get("dimension") or ""),
            "subjects": subjects,
            "evidence_ids": evidence_ids,
            "source_ids": source_ids,
            "evidence_count": len(evidence_ids),
            "source_count": len(source_ids),
            #: 锚点**恒不进** `traits`；这里只是把事实写明，交给读的人判断
            "in_traits": bool(raw.get("in_traits")),
        })
    return out


# --------------------------------------------------------------------------
# 端口协议（P1.2-B 定义；本阶段按 INTERFACES §6.2 与已实现面对齐，D-119）
# --------------------------------------------------------------------------


class PersonaPort(Protocol):
    """人格档案的**写入 + 查询**接口。只有本模块能写档案表。

    签名与 `docs/INTERFACES.md §6.2` 对齐。P1.2-B 骨架里的旧签名
    （`distill(entity_id, evidence_ids)` / `revise(profile_id, ...)` 等）
    与文档不一致，本阶段按**文档为准**更正（同 D-110 先例）；
    `distill` 的 AI 蒸馏主体尚未实现（要调 `Provider.chat`），
    待下一阶段补入协议，本次不虚占签名。
    """

    def create_profile(self, ctx: CallContext,
                       draft: "PersonaProfileDraft") -> Result["PersonaProfileRef"]:
        """创建档案 v1。已存在 → `Err(STATE_CONFLICT.no_new_evidence)`（用 revise）。"""
        ...

    def revise(self, ctx: CallContext, entity_id: str,
               draft: "PersonaProfileDraft") -> Result["PersonaProfileRef"]:
        """基于新内容生成**新版本**；旧版归档保留（superseded_by 链）。

        内容与最新版完全一致 → `Err(STATE_CONFLICT.no_new_evidence)`
        （不生成空转版本）；尚无档案 → `Err(NOT_FOUND.entity)`。
        """
        ...

    def get_persona(self, ctx: CallContext, entity_id: str,
                    version: int | None = None) -> Result["PersonaProfileView"]:
        """读取档案；`version=None` 返回最新版。"""
        ...

    def query_profiles(self, ctx: CallContext,
                       q: Query) -> Result[Page["PersonaProfileView"]]:
        """按条件查询档案（**含历史版本**；过滤键白名单外一律拒绝）。"""
        ...

    def diff_versions(self, ctx: CallContext, entity_id: str, v_from: int,
                      v_to: int) -> Result["PersonaDiff"]:
        """字段级差异（只读），供审查与评测使用。"""
        ...

    def apply_stability_gate(self, ctx: CallContext, item: "ProfileItem",
                             target_section: str = SECTION_TRAITS
                             ) -> Result["ProfileItem"]:
        """稳定性闸门（D-012 / PF-V2）：`SINGLE` 不许进 `traits`。"""
        ...


class PersonaReader(Protocol):
    """**只读**端口（供 Relationship 与 Dialogue 使用）。"""

    def get_persona(self, ctx: CallContext, entity_id: str,
                    version: int | None = None) -> Result["PersonaProfileView"]:
        ...

    def query_profiles(self, ctx: CallContext,
                       q: Query) -> Result[Page["PersonaProfileView"]]:
        ...


# --------------------------------------------------------------------------
# 数据契约（Draft 进 / View 出；跨模块只传 ID 与这些形状，不传数据库行）
# --------------------------------------------------------------------------


@dataclass
class ProfileItem:
    """档案条目（DATA_MODEL §4.15 `ProfileItem`）。

    `confidence` 只是排序权重（PF-V4 / D-010）；`namespace` 恒
    `SYSTEM_DERIVED`（PF-V6：档案是加工产物）。"这是系统推断"的标记在
    **证据层**表达（`tier=INFERENCE` + `derived_from_evidence_ids`），
    条目经 `evidence_refs` 追溯到证据、再追溯到推断的根。
    """

    item_id: str
    text: str
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    stability: Stability | str = Stability.UNKNOWN
    namespace: ProvenanceNamespace | str = ProvenanceNamespace.SYSTEM_DERIVED
    uncertainty_note: str = ""


@dataclass
class TimelineItem(ProfileItem):
    """时间线条目（DATA_MODEL §4.15 `TimelineItem` = ProfileItem + 时间）。"""

    occurred_at: FuzzyDate | None = None
    work_internal_time: str | None = None


@dataclass
class ProfileSection:
    """单块文本型板块（background / expression_style）。

    DATA_MODEL 未定义其内嵌结构（缺口已记为 D-113）：文本非空时
    `evidence_refs` 必须非空（PF-V1 / D-020），否则内容进 `open_questions`。
    """

    text: str = ""
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    uncertainty_note: str = ""


@dataclass
class CanonBoundary:
    """虚构角色的正典边界（DATA_MODEL §4.7 `canon_boundary`，内嵌结构为 D-113 定形）。

    记录哪些材料是官方设定、哪些不是；**只登记，不裁决**材料真伪。
    """

    canonical_material_refs: list[str] = field(default_factory=list)
    non_canonical_material_refs: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class PersonaProfileDraft:
    """创建/修订档案的请求（DATA_MODEL §4.7 必填项 + INTERFACES §6.2）。

    `change_reason` 是**更新依据**：revise 时必填（D-112），create 时
    可省略（默认"初始版本"）——版本变化永不静默发生。
    """

    entity_id: str
    background: ProfileSection = field(default_factory=ProfileSection)
    traits: list[ProfileItem] = field(default_factory=list)
    habits: list[ProfileItem] = field(default_factory=list)
    expression_style: ProfileSection = field(default_factory=ProfileSection)
    experiences: list[TimelineItem] = field(default_factory=list)
    single_events: list[ProfileItem] = field(default_factory=list)
    canon_boundary: CanonBoundary | None = None
    applicable_variant_ids: list[str] = field(default_factory=list)
    do_not_apply_to_variants: list[str] = field(default_factory=list)
    controversial_topic_ids: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    status: ProfileStatus | str = ProfileStatus.INSUFFICIENT_EVIDENCE
    change_reason: str = ""
    #: 只读的"证据锚点"视图（D-278 的保守版，**不是** `traits`）。
    #:
    #: 每项形如 `{"feature", "dimension", "subjects", "evidence_ids",
    #: "source_ids", "evidence_count", "source_count", "in_traits"}`，
    #: 回答"这个特征有几条来源提到"。
    #:
    #: 为什么要单独一个字段而不是直接放宽闸门：R4.3 实测 16 条证据
    #: `stability` 全为 `SINGLE`，`traits` 桶结构性为空。把跨源线索显示出来
    #: 能救急（档案不至于全空），但**把它塞进 `traits` 就直接违反 D-012 / PF-V2**
    #: （单次行为不得固化为稳定性格）。所以：看得见，但不进桶。
    #:
    #: 计数由服务从**证据本身**算出（`_build_persona_draft`），落盘时再做一次
    #: 自洽校验（`_anchor_view`）——调用方可以留空，不可以编造。
    evidence_anchors: list[dict] = field(default_factory=list)


@dataclass
class PersonaProfileRef:
    """创建/修订后返回的最小形状。"""

    id: str
    entity_id: str
    version: int
    status: str


@dataclass
class PersonaProfileView:
    """档案的完整读视图（含版本与归档信息）。"""

    id: str
    entity_id: str
    version: int
    namespace: str
    background: ProfileSection
    traits: list[ProfileItem]
    habits: list[ProfileItem]
    expression_style: ProfileSection
    experiences: list[TimelineItem]
    single_events: list[ProfileItem]
    canon_boundary: CanonBoundary | None
    applicable_variant_ids: list[str]
    do_not_apply_to_variants: list[str]
    controversial_topic_ids: list[str]
    open_questions: list[str]
    coverage: dict
    status: str
    superseded_by: str | None
    change_reason: str
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ProfileChange:
    """diff 中的一处字段级变化。"""

    field: str
    change_type: str            # added / removed / changed
    item_id: str = ""           # 条目级变化时带
    before: str = ""
    after: str = ""


@dataclass
class PersonaDiff:
    """两个版本之间的字段级差异（INTERFACES §6.2 `PersonaDiff`）。"""

    entity_id: str
    v_from: int
    v_to: int
    changes: list[ProfileChange]
    to_version_change_reason: str = ""


# --------------------------------------------------------------------------
# 序列化辅助（存储 payload 只放可 JSON 化的 plain 数据）
# --------------------------------------------------------------------------


def _fuzzy_to_dict(value: FuzzyDate | None) -> dict | None:
    if value is None:
        return None
    return {
        "raw": value.raw,
        "start": value.start,
        "end": value.end,
        "precision": str(value.precision),
    }


def _fuzzy_from_dict(data: Any) -> FuzzyDate | None:
    if data is None:
        return None
    if isinstance(data, FuzzyDate):
        return data
    if not isinstance(data, dict):
        return None
    try:
        precision = TimePrecision(str(data.get("precision", "UNKNOWN")))
    except ValueError:
        precision = TimePrecision.UNKNOWN
    return FuzzyDate(
        raw=str(data.get("raw", "")),
        start=data.get("start"),
        end=data.get("end"),
        precision=precision,
    )


def _coerce(value: Any, enum_cls) -> Any:
    """把字符串/枚举统一成枚举；不认识返回 None（由调用方决定报什么错）。"""
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value))
    except ValueError:
        return None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _confidence(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _item_to_dict(item: ProfileItem) -> dict:
    data: dict[str, Any] = {
        "item_id": str(item.item_id),
        "text": str(item.text),
        "confidence": item.confidence,
        "evidence_refs": _str_list(item.evidence_refs),
        "stability": str(getattr(item.stability, "value", item.stability)),
        "namespace": str(getattr(item.namespace, "value", item.namespace)),
        "uncertainty_note": str(item.uncertainty_note or ""),
    }
    if isinstance(item, TimelineItem):
        data["occurred_at"] = _fuzzy_to_dict(item.occurred_at)
        data["work_internal_time"] = item.work_internal_time
    return data


def _item_from_dict(data: Any, timeline: bool = False) -> ProfileItem:
    if not isinstance(data, dict):
        data = {}
    stability = _coerce(data.get("stability"), Stability) or Stability.UNKNOWN
    namespace = _coerce(data.get("namespace"), ProvenanceNamespace) \
        or ProvenanceNamespace.SYSTEM_DERIVED
    common = {
        "item_id": str(data.get("item_id", "")),
        "text": str(data.get("text", "")),
        "confidence": _confidence(data.get("confidence")) or 0.0,
        "evidence_refs": _str_list(data.get("evidence_refs")),
        "stability": stability,
        "namespace": namespace,
        "uncertainty_note": str(data.get("uncertainty_note") or ""),
    }
    if timeline:
        return TimelineItem(
            **common,
            occurred_at=_fuzzy_from_dict(data.get("occurred_at")),
            work_internal_time=data.get("work_internal_time"),
        )
    return ProfileItem(**common)


def _section_to_dict(section: ProfileSection) -> dict:
    return {
        "text": str(section.text or ""),
        "confidence": section.confidence,
        "evidence_refs": _str_list(section.evidence_refs),
        "uncertainty_note": str(section.uncertainty_note or ""),
    }


def _section_from_dict(data: Any) -> ProfileSection:
    if not isinstance(data, dict):
        data = {}
    return ProfileSection(
        text=str(data.get("text", "")),
        confidence=_confidence(data.get("confidence")) or 0.0,
        evidence_refs=_str_list(data.get("evidence_refs")),
        uncertainty_note=str(data.get("uncertainty_note") or ""),
    )


def _canon_to_dict(canon: CanonBoundary | None) -> dict | None:
    if canon is None:
        return None
    return {
        "canonical_material_refs": _str_list(canon.canonical_material_refs),
        "non_canonical_material_refs": _str_list(canon.non_canonical_material_refs),
        "note": str(canon.note or ""),
    }


def _canon_from_dict(data: Any) -> CanonBoundary | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    return CanonBoundary(
        canonical_material_refs=_str_list(data.get("canonical_material_refs")),
        non_canonical_material_refs=_str_list(data.get("non_canonical_material_refs")),
        note=str(data.get("note") or ""),
    )


# --------------------------------------------------------------------------
# 服务实现
# --------------------------------------------------------------------------


class PersonaService:
    """人格档案的本地管理基础。**只依赖 `Storage`**，不碰 Provider（F-2/F-5）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self.storage = storage

    # ------------------------------------------------------------ 创建

    def create_profile(self, ctx: CallContext,
                       draft: PersonaProfileDraft) -> Result[PersonaProfileRef]:
        """创建档案 v1（`version=1`，`superseded_by=None`）。

        已存在档案 → `Err(STATE_CONFLICT.no_new_evidence)`：档案是版本化
        实体、不是可去重内容，更新必须走 `revise`（保留旧版）。
        """
        existing = self._profiles_of(ctx, draft.entity_id)
        if is_err(existing):
            return existing
        if existing.value:
            latest = max(existing.value, key=lambda r: int(r.get("version", 0)))
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE,
                f"该对象的档案已存在（最新版本 v{latest.get('version')}）；"
                "更新请用 revise 生成新版本，旧版本会归档保留",
                trace_ref=ctx.trace_ref))
        problem = self._validate_draft(ctx, draft, require_reason=False)
        if problem is not None:
            return problem
        return self._write_profile(ctx, draft, version=1, superseded_by=None)

    # ------------------------------------------------------------ 修订

    def revise(self, ctx: CallContext, entity_id: str,
               draft: PersonaProfileDraft) -> Result[PersonaProfileRef]:
        """基于新内容生成**新版本**；旧版打 `superseded_by` 归档（D-111）。

        内容与最新版**完全一致** → `Err(STATE_CONFLICT.no_new_evidence)`
        （D-112：不生成空转版本）。`change_reason` 必填（更新依据）。
        """
        if str(draft.entity_id or "") != str(entity_id or ""):
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"draft.entity_id（{draft.entity_id!r}）与 revise 目标 "
                f"（{entity_id!r}）不一致", trace_ref=ctx.trace_ref))
        existing = self._profiles_of(ctx, entity_id)
        if is_err(existing):
            return existing
        if not existing.value:
            return Err(new_error(
                ErrorCode.NOT_FOUND_ENTITY,
                f"对象 {entity_id} 尚无档案，请先 create_profile",
                trace_ref=ctx.trace_ref))
        latest = max(existing.value, key=lambda r: int(r.get("version", 0)))
        if not str(draft.change_reason or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "D-112：revise 必须提供 change_reason（更新依据）——"
                "版本变化永不静默发生", trace_ref=ctx.trace_ref))
        problem = self._validate_draft(ctx, draft, require_reason=True)
        if problem is not None:
            return problem
        # 内容一致性：与最新版逐字段相同 ⇒ 拒绝空转（D-112）
        if self._content_of_draft(draft) == self._content_of_row(latest):
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE,
                "档案内容与最新版完全一致，不生成新版本"
                "（如确需记录说明，请先修改内容）", trace_ref=ctx.trace_ref))
        # —— G7（A 级写风险）：**空产出绝不覆盖有内容的旧版** ——
        # 上面那条"内容完全一致"挡不住"旧版有 1 条、新版 0 条"：两者不相等，
        # 于是会写出一个空的新版本，并把旧版的 `superseded_by` 指过去；而默认
        # 读取取的是 version 最大者 ⇒ 读到空档案。旧版行虽仍在库里，实际已被
        # 降级覆盖（P4.5-A 冒烟重跑实测：v1 `OK items=1` → v2 `INSUFFICIENT_EVIDENCE items=0`）。
        # 处理：不写新版本、不动旧版的归档指针、返回旧版引用 + 专属告警。
        if not self._has_any_content(draft) and self._has_any_content_row(latest):
            result = Ok(PersonaProfileRef(
                id=str(latest["id"]), entity_id=str(entity_id),
                version=int(latest.get("version", 0)),
                status=str(latest.get("status", ""))))
            return with_warning(
                result, WARNING_PROFILE_DEGRADED_SKIPPED,
                f"本次修订未产出任何内容（各维度为空），不生成新版本；"
                f"保留 v{latest.get('version')}——空产出绝不覆盖有内容的档案")
        return self._write_profile(ctx, draft,
                                   version=int(latest.get("version", 0)) + 1,
                                   superseded_by=None,
                                   supersede_id=str(latest["id"]))

    # ------------------------------------------------------------ 读取

    def get_persona(self, ctx: CallContext, entity_id: str,
                    version: int | None = None) -> Result[PersonaProfileView]:
        """读取档案；`version=None` 返回最新版（版本号最大者）。"""
        rows = self._profiles_of(ctx, entity_id)
        if is_err(rows):
            return rows
        if not rows.value:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY,
                                 f"对象 {entity_id} 没有人格档案",
                                 trace_ref=ctx.trace_ref))
        if version is None:
            row = max(rows.value, key=lambda r: int(r.get("version", 0)))
        else:
            matches = [r for r in rows.value if int(r.get("version", 0)) == int(version)]
            if not matches:
                return Err(new_error(
                    ErrorCode.NOT_FOUND_VERSION,
                    f"对象 {entity_id} 没有 v{version} 的档案"
                    f"（现有版本：{sorted(int(r.get('version', 0)) for r in rows.value)}）",
                    trace_ref=ctx.trace_ref))
            row = matches[0]
        return Ok(self._profile_view(row))

    def query_profiles(self, ctx: CallContext,
                       q: Query) -> Result[Page[PersonaProfileView]]:
        """按条件查询档案。**含历史版本**（调用方可按 version 过滤）。"""
        problem = self._check_query(ctx, q)
        if problem is not None:
            return problem
        loaded = self._load_all(ctx, "PersonaProfile", ctx.project_id)
        if is_err(loaded):
            return loaded
        rows = [r for r in loaded.value if self._match_filters(r, q.filters)]
        rows = self._sort_rows(rows, q.sort)
        page_items, total, has_more = self._paginate(rows, q)
        return Ok(Page(items=[self._profile_view(r) for r in page_items],
                       total=total, has_more=has_more))

    # ------------------------------------------------------------ 差异

    def diff_versions(self, ctx: CallContext, entity_id: str, v_from: int,
                      v_to: int) -> Result[PersonaDiff]:
        """两个版本之间的字段级差异（只读；DATA_MODEL：旧版永不删除）。"""
        rows = self._profiles_of(ctx, entity_id)
        if is_err(rows):
            return rows
        if not rows.value:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY,
                                 f"对象 {entity_id} 没有人格档案",
                                 trace_ref=ctx.trace_ref))
        by_version = {int(r.get("version", 0)): r for r in rows.value}
        for v in (v_from, v_to):
            if v not in by_version:
                return Err(new_error(
                    ErrorCode.NOT_FOUND_VERSION,
                    f"对象 {entity_id} 没有 v{v} 的档案"
                    f"（现有版本：{sorted(by_version)}）", trace_ref=ctx.trace_ref))
        changes = self._diff_rows(by_version[v_from], by_version[v_to])
        return Ok(PersonaDiff(
            entity_id=entity_id, v_from=v_from, v_to=v_to, changes=changes,
            to_version_change_reason=str(by_version[v_to].get("change_reason", ""))))

    # ------------------------------------------------------------ 稳定性闸门

    def apply_stability_gate(self, ctx: CallContext, item: ProfileItem,
                             target_section: str = SECTION_TRAITS
                             ) -> Result[ProfileItem]:
        """稳定性闸门（D-012 / PF-V2）：`SINGLE` 一律不许进 `traits`。

        其余板块不受闸门约束（DATA_MODEL PF-V2 只约束 traits）；
        单次行为的归宿是 `single_events`——与 `traits` 物理隔离。
        """
        stability = _coerce(item.stability, Stability)
        if stability is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"未知稳定性：{item.stability!r}；可选："
                f"{[s.value for s in Stability]}", trace_ref=ctx.trace_ref))
        if target_section in GATED_SECTIONS and stability not in (
                Stability.REPEATED, Stability.PATTERN):
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED,
                f"PF-V2 / D-012：{target_section} 只接受 REPEATED / PATTERN，"
                f"收到 {stability.value}——单次行为请放 single_events，"
                "不得固化为稳定性格", trace_ref=ctx.trace_ref))
        return Ok(item)

    # ------------------------------------------------------------ 内部：写入

    def _write_profile(self, ctx: CallContext, draft: PersonaProfileDraft,
                       version: int, superseded_by: str | None,
                       supersede_id: str | None = None) -> Result[PersonaProfileRef]:
        """落盘一个新版本；`supersede_id` 非空时把旧版打上归档指针。"""
        coverage = self._compute_coverage(draft)
        payload = {
            "entity_id": str(draft.entity_id),
            "version": int(version),
            "background": _section_to_dict(draft.background),
            "traits": [_item_to_dict(i) for i in _normalize_items(draft.traits)],
            "habits": [_item_to_dict(i) for i in _normalize_items(draft.habits)],
            "expression_style": _section_to_dict(draft.expression_style),
            "experiences": [_item_to_dict(i) for i in _normalize_items(draft.experiences)],
            "single_events": [_item_to_dict(i) for i in _normalize_items(draft.single_events)],
            "canon_boundary": _canon_to_dict(draft.canon_boundary),
            "applicable_variant_ids": _str_list(draft.applicable_variant_ids),
            "do_not_apply_to_variants": _str_list(draft.do_not_apply_to_variants),
            "controversial_topic_ids": _str_list(draft.controversial_topic_ids),
            "open_questions": _str_list(draft.open_questions),
            "coverage": coverage,
            #: 只读视图：不参与 `_compute_coverage`，不影响任何板块的条目数
            "evidence_anchors": _anchor_view(draft.evidence_anchors),
            "status": str(getattr(draft.status, "value", draft.status)),
            "superseded_by": superseded_by,
            "change_reason": str(draft.change_reason or "") or
            ("初始版本（create_profile）" if version == 1 else ""),
        }
        profile_id = new_id("PersonaProfile")
        outcome = self.storage.put(ctx, "PersonaProfile", payload,
                                   project_id=ctx.project_id,
                                   namespace=ProvenanceNamespace.SYSTEM_DERIVED,
                                   id=profile_id)
        if is_err(outcome):
            return outcome
        if supersede_id:
            archived = self.storage.update(ctx, "PersonaProfile", supersede_id,
                                           {"superseded_by": profile_id})
            if is_err(archived):
                return archived
        self._link_conflicts(ctx, profile_id, str(draft.entity_id), int(version),
                             _str_list(draft.controversial_topic_ids))
        self._audit(ctx, "persona.write_profile",
                    f"{profile_id} v{version} entity={draft.entity_id}")
        result = Ok(PersonaProfileRef(id=profile_id, entity_id=str(draft.entity_id),
                                      version=int(version),
                                      status=payload["status"]))
        if supersede_id:
            with_warning(result, WARNING_PROFILE_SUPERSEDED,
                         f"已生成 v{version}；旧版本已归档（superseded_by 指向新版本）")
        return result

    def _link_conflicts(self, ctx: CallContext, profile_id: str, entity_id: str,
                        version: int, conflict_ids: list[str]) -> None:
        """CF-V5 双向一致：档案引用冲突时，把 `downstream_refs` 回写到冲突记录。

        幂等：同一 (profile_id, version, field) 不重复追加。只记录关联，
        **不裁决**冲突（D-013）。
        """
        for conflict_id in conflict_ids:
            row = self._get_row(ctx, "ConflictRecord", conflict_id)
            if is_err(row) or row.value is None:
                continue  # 存在性已在 _validate_draft 校验过
            refs = row.value.get("downstream_refs")
            refs = list(refs) if isinstance(refs, list) else []
            entry = {"profile_id": profile_id, "entity_id": entity_id,
                     "version": version, "field": "controversial_topic_ids"}
            if any(isinstance(r, dict) and r.get("profile_id") == profile_id
                   and r.get("version") == version for r in refs):
                continue
            refs.append(entry)
            updated = self.storage.update(ctx, "ConflictRecord", conflict_id,
                                          {"downstream_refs": refs})
            if is_err(updated):
                continue  # 回写失败不阻断档案写入（冲突记录本身已存在）

    # ------------------------------------------------------------ 内部：校验

    def _validate_draft(self, ctx: CallContext, draft: PersonaProfileDraft,
                        require_reason: bool) -> Err | None:
        """创建/修订共用的全部校验（PF-V1~V6 + 版本边界 + 引用完整性）。"""
        if not str(draft.entity_id or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "entity_id 不能为空", trace_ref=ctx.trace_ref))
        entity = self._require_entity_row(ctx, draft.entity_id)
        if is_err(entity):
            return entity
        entity_row = entity.value
        entity_type = _coerce(entity_row.get("entity_type"), EntityType)
        if entity_type is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"对象 {draft.entity_id} 的 entity_type 无法识别："
                f"{entity_row.get('entity_type')!r}", trace_ref=ctx.trace_ref))
        if require_reason and not str(draft.change_reason or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "D-112：revise 必须提供 change_reason（更新依据）",
                trace_ref=ctx.trace_ref))

        status = _coerce(draft.status, ProfileStatus)
        if status is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"未知档案状态：{draft.status!r}；可选："
                f"{[s.value for s in ProfileStatus]}", trace_ref=ctx.trace_ref))

        # —— PF-V6 / 模拟隔离：档案与条目命名空间恒 SYSTEM_DERIVED ——
        for item in self._all_items(draft):
            namespace = _coerce(item.namespace, ProvenanceNamespace)
            if namespace is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    f"未知命名空间：{item.namespace!r}", trace_ref=ctx.trace_ref))
            if namespace == ProvenanceNamespace.SIMULATED:
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    "PF-V6 / 隔离红线：模拟内容永远不能进入人格档案",
                    trace_ref=ctx.trace_ref))
            if namespace != ProvenanceNamespace.SYSTEM_DERIVED:
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    f"PF-V6：档案条目 namespace 必须为 SYSTEM_DERIVED，"
                    f"收到 {namespace.value}", trace_ref=ctx.trace_ref))

        # —— PF-V5 / PE-V3：版本边界（真人无版本；虚构必须显式声明）——
        variants = _str_list(draft.applicable_variant_ids)
        forbidden = _str_list(draft.do_not_apply_to_variants)
        if entity_type == EntityType.REAL_PERSON:
            if variants or forbidden:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "D-115：真人对象没有改编版本，applicable_variant_ids / "
                    "do_not_apply_to_variants 必须为空", trace_ref=ctx.trace_ref))
            if draft.canon_boundary is not None:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "D-120：canon_boundary 是虚构角色专用字段，真人档案不得填写",
                    trace_ref=ctx.trace_ref))
        else:
            if not variants:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "PF-V5：虚构角色档案必须声明 applicable_variant_ids"
                    "（适用于哪些改编版本）", trace_ref=ctx.trace_ref))
            if str(draft.entity_id) not in variants:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "PF-V5：applicable_variant_ids 必须包含档案对象自身"
                    f"（{draft.entity_id}）", trace_ref=ctx.trace_ref))
            if str(draft.entity_id) in forbidden:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "PF-V5：do_not_apply_to_variants 不得包含档案对象自身",
                    trace_ref=ctx.trace_ref))
            problem = self._check_variants(ctx, variants)
            if problem is not None:
                return problem
            # 正典边界形状（D-113 定形；只登记引用，不裁决真伪）
            if draft.canon_boundary is not None:
                problem = self._check_canon_boundary(ctx, draft.canon_boundary)
                if problem is not None:
                    return problem

        # —— 板块与条目（PF-V1~V4 + 引用完整性）——
        for name in TEXT_SECTIONS:
            problem = self._validate_section(ctx, name, getattr(draft, name),
                                             str(draft.entity_id))
            if problem is not None:
                return problem
        for name in ITEM_SECTIONS:
            gated = name in GATED_SECTIONS
            for item in _normalize_items(getattr(draft, name)):
                problem = self._validate_item(ctx, name, item,
                                              str(draft.entity_id), gated)
                if problem is not None:
                    return problem
        # PF-V3：traits 与 single_events 不得交叉引用同一 item_id
        trait_ids = {str(i.item_id) for i in _normalize_items(draft.traits)}
        event_ids = {str(i.item_id) for i in _normalize_items(draft.single_events)}
        overlap = trait_ids & event_ids
        if overlap:
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED,
                f"PF-V3：traits 与 single_events 不得共用 item_id：{sorted(overlap)}"
                "（稳定性格与单次行为物理隔离，D-012）",
                trace_ref=ctx.trace_ref))

        # —— D-117：不能全空——没有证据支撑就必须说明缺什么（D-020）——
        if not self._has_any_content(draft) and not _str_list(draft.open_questions):
            return Err(new_error(
                ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT,
                "D-020 / D-117：档案没有任何证据支撑的内容时，必须写 "
                "open_questions 说明缺什么——不允许交白卷也不说明",
                trace_ref=ctx.trace_ref))

        # —— 冲突引用：存在性 + 种类（CF-V5 双向一致由 _link_conflicts 回写）——
        for conflict_id in _str_list(draft.controversial_topic_ids):
            if prefix_of(conflict_id) != "ConflictRecord":
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"controversial_topic_ids 只能引用 ConflictRecord，"
                    f"收到 {conflict_id!r}", trace_ref=ctx.trace_ref))
            row = self._get_row(ctx, "ConflictRecord", conflict_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"controversial_topic_ids 指向的冲突不存在：{conflict_id}",
                    trace_ref=ctx.trace_ref))
        return None

    def _validate_section(self, ctx: CallContext, name: str,
                          section: ProfileSection, entity_id: str) -> Err | None:
        """单块文本型板块：文本非空 ⇒ evidence_refs 非空且有效（PF-V1 / D-020）。"""
        if not str(section.text or "").strip():
            return None  # 空板块合法：缺口由 coverage + open_questions 表达
        if section.confidence and _confidence(section.confidence) is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{name}.confidence 必须是 0~1 的数（排序权重，非真实性，PF-V4）",
                trace_ref=ctx.trace_ref))
        if not _str_list(section.evidence_refs):
            return Err(new_error(
                ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT,
                f"PF-V1 / D-020：{name} 有内容就必须带 evidence_refs；"
                "没有证据支撑的内容请留空并记入 open_questions",
                trace_ref=ctx.trace_ref))
        return self._require_evidence_refs(ctx, _str_list(section.evidence_refs),
                                           entity_id, where=name)

    def _validate_item(self, ctx: CallContext, name: str, item: ProfileItem,
                       entity_id: str, gated: bool) -> Err | None:
        """条目校验：形状 → 稳定性闸门 → 证据引用完整性。"""
        if not str(item.text or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{name} 条目文本不能为空（item_id={item.item_id!r}）",
                trace_ref=ctx.trace_ref))
        if _confidence(item.confidence) is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{name} 条目 confidence 必须是 0~1 的数"
                "（排序权重，不代表真伪，PF-V4）", trace_ref=ctx.trace_ref))
        if not str(item.item_id or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{name} 条目必须带 item_id（跨版本稳定才能产出可读 diff，D-114）",
                trace_ref=ctx.trace_ref))
        if not _str_list(item.evidence_refs):
            return Err(new_error(
                ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT,
                f"PF-V1 / D-020：{name} 条目「{str(item.text)[:30]}…」"
                "必须带 evidence_refs；没有证据支撑请留空并记入 open_questions",
                trace_ref=ctx.trace_ref))
        problem = self._require_evidence_refs(ctx, _str_list(item.evidence_refs),
                                              entity_id,
                                              where=f"{name}[{item.item_id}]")
        if problem is not None:
            return problem
        if gated:
            gate = self.apply_stability_gate(ctx, item, name)
            if is_err(gate):
                return gate
        return None

    def _require_evidence_refs(self, ctx: CallContext, refs: list[str],
                               entity_id: str, where: str) -> Err | None:
        """PF-V1 + 隔离红线：引用必须是**存在**的 `EvidenceItem`。

        ⛔ 永不接受 `MemoryItem` / `ConversationTurn`（模拟对话、模拟事件、
        模拟记忆不得成为档案依据，INTERFACES §6.3 / 用户目标 8）。
        ⛔ 证据声明了 `applicable_variant_ids` 时必须包含本档案对象
        （D-117：版本专属证据不得串用到其他版本）。
        """
        for ref in refs:
            if prefix_of(ref) != "EvidenceItem":
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"{where} 的 evidence_refs 只能引用 EvidenceItem，"
                    f"收到 {ref!r}（模拟记忆/对话永远不能作为档案依据）",
                    trace_ref=ctx.trace_ref))
            row = self._get_row(ctx, "EvidenceItem", ref)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"{where} 引用的证据不存在：{ref}", trace_ref=ctx.trace_ref))
            if str(row.value.get("namespace")) == str(ProvenanceNamespace.SIMULATED):
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    f"{where} 引用了 SIMULATED 证据 {ref}——模拟内容"
                    "永远不能变成档案依据", trace_ref=ctx.trace_ref))
            variants = _str_list(row.value.get("applicable_variant_ids"))
            if variants and str(entity_id) not in variants:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"D-117：证据 {ref} 只适用于改编版本 {variants}，"
                    f"不能作为 {entity_id} 的档案依据（版本边界，PE-V3）",
                    trace_ref=ctx.trace_ref))
        return None

    def _check_variants(self, ctx: CallContext, variant_ids: list[str]) -> Err | None:
        """PF-V5 / PE-V3：适用版本必须存在，且不得跨 variant_group 混指。"""
        if not variant_ids:
            return None
        groups: set[str] = set()
        for variant_id in variant_ids:
            row = self._get_row(ctx, "PersonEntity", variant_id)
            if is_err(row):
                return row
            if row.value is None or prefix_of(variant_id) != "PersonEntity":
                return Err(new_error(
                    ErrorCode.VALIDATION_ENTITY_MISSING,
                    f"适用的改编版本不存在：{variant_id}", trace_ref=ctx.trace_ref))
            group = str(row.value.get("variant_group_id") or "")
            if group:
                groups.add(group)
        if len(groups) > 1:
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED,
                "PE-V3：applicable_variant_ids 跨 variant_group 混指"
                "（不同改编版本不得无条件合并）", trace_ref=ctx.trace_ref))
        return None

    def _check_canon_boundary(self, ctx: CallContext,
                              canon: CanonBoundary) -> Err | None:
        """正典边界形状校验（D-113 定形）：引用为非空字符串；只登记不裁决。"""
        for field_name in ("canonical_material_refs", "non_canonical_material_refs"):
            for ref in _str_list(getattr(canon, field_name)):
                if not ref.strip():
                    return Err(new_error(
                        ErrorCode.VALIDATION_REQUIRED_FIELD,
                        f"canon_boundary.{field_name} 含空引用",
                        trace_ref=ctx.trace_ref))
        return None

    # ------------------------------------------------------------ 内部：存储辅助

    def _get_row(self, ctx: CallContext, kind: str,
                 entity_id: str) -> Result[dict | None]:
        """读一行（扁平化）。`Ok(None)` = 不存在；`Err` = 读取失败——
        **绝不把读失败伪装成不存在**（P2.4 纪律）。"""
        return self.storage.get(ctx, kind, entity_id)

    def _load_all(self, ctx: CallContext, kind: str,
                  project_id: str) -> Result[list[dict]]:
        """把项目下某类记录全部读出（分页遍历，绕开单批 200 上限）。"""
        items: list[dict] = []
        offset = 0
        while True:
            page = self.storage.query(
                ctx, kind,
                Query(filters={"project_id": project_id}, limit=MAX_QUERY_LIMIT,
                      offset=offset))
            if is_err(page):
                return page
            batch = unwrap(page)
            items.extend(batch.items)
            if not batch.has_more or not batch.items:
                break
            offset += len(batch.items)
        items.sort(key=lambda r: (str(r.get("created_at", "")), str(r.get("id", ""))))
        return Ok(items)

    def _profiles_of(self, ctx: CallContext,
                     entity_id: str) -> Result[list[dict]]:
        """某对象的全部档案版本（不含已软删除）。"""
        loaded = self._load_all(ctx, "PersonaProfile", ctx.project_id)
        if is_err(loaded):
            return loaded
        return Ok([r for r in loaded.value
                   if str(r.get("entity_id", "")) == str(entity_id)])

    def _require_entity_row(self, ctx: CallContext,
                            entity_id: str) -> Result[dict]:
        row = self._get_row(ctx, "PersonEntity", entity_id)
        if is_err(row):
            return row
        if row.value is None or prefix_of(entity_id) != "PersonEntity":
            return Err(new_error(
                ErrorCode.VALIDATION_ENTITY_MISSING,
                f"对象不存在：{entity_id}", trace_ref=ctx.trace_ref))
        return Ok(row.value)

    def _audit(self, ctx: CallContext, action: str, detail: str) -> None:
        self.storage.append_audit(ctx, "INFO", action, detail=detail)

    def _check_query(self, ctx: CallContext, q: Query) -> Err | None:
        unknown = set(q.filters) - PROFILE_FILTER_KEYS
        if unknown:
            return Err(new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER,
                                 f"未登记的过滤键：{sorted(unknown)}",
                                 trace_ref=ctx.trace_ref))
        if q.limit > MAX_QUERY_LIMIT:
            return Err(new_error(ErrorCode.VALIDATION_LIMIT_TOO_LARGE,
                                 f"limit 不得超过 {MAX_QUERY_LIMIT}",
                                 trace_ref=ctx.trace_ref))
        if q.sort and str(q.sort[0]) not in SORTABLE_COLUMNS:
            return Err(new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER,
                                 f"不允许按 {q.sort[0]!r} 排序；"
                                 f"可选：{sorted(SORTABLE_COLUMNS)}",
                                 trace_ref=ctx.trace_ref))
        return None

    # ------------------------------------------------------------ 内部：过滤/分页/排序

    @staticmethod
    def _match_filters(row: dict, filters: dict) -> bool:
        for key, expected in filters.items():
            actual = row.get(key)
            if isinstance(expected, (list, tuple, set)):
                if not set(str(e) for e in expected) & set(_str_list(actual)):
                    return False
            elif str(actual) != str(expected):
                return False
        return True

    @staticmethod
    def _sort_rows(rows: list[dict], sort: tuple[str, str] | None) -> list[dict]:
        if not sort:
            return rows
        column, direction = sort
        reverse = str(direction).lower() == "desc"
        return sorted(rows, key=lambda r: str(r.get(column, "")), reverse=reverse)

    @staticmethod
    def _paginate(rows: list[dict], q: Query) -> tuple[list[dict], int, bool]:
        total = len(rows)
        offset = max(0, int(q.offset))
        items = rows[offset:offset + q.limit]
        return items, total, offset + len(items) < total

    # ------------------------------------------------------------ 内部：视图/内容

    def _profile_view(self, row: dict) -> PersonaProfileView:
        return PersonaProfileView(
            id=str(row.get("id", "")),
            entity_id=str(row.get("entity_id", "")),
            version=int(row.get("version", 0) or 0),
            namespace=str(row.get("namespace", "")),
            background=_section_from_dict(row.get("background")),
            traits=[_item_from_dict(i) for i in _normalize_items(row.get("traits"))],
            habits=[_item_from_dict(i) for i in _normalize_items(row.get("habits"))],
            expression_style=_section_from_dict(row.get("expression_style")),
            experiences=[_item_from_dict(i, timeline=True)
                         for i in _normalize_items(row.get("experiences"))],
            single_events=[_item_from_dict(i)
                           for i in _normalize_items(row.get("single_events"))],
            canon_boundary=_canon_from_dict(row.get("canon_boundary")),
            applicable_variant_ids=_str_list(row.get("applicable_variant_ids")),
            do_not_apply_to_variants=_str_list(row.get("do_not_apply_to_variants")),
            controversial_topic_ids=_str_list(row.get("controversial_topic_ids")),
            open_questions=_str_list(row.get("open_questions")),
            coverage=dict(row.get("coverage") or {}),
            status=str(row.get("status", "")),
            superseded_by=row.get("superseded_by"),
            change_reason=str(row.get("change_reason", "")),
            created_at=str(row.get("created_at", "")),
            updated_at=str(row.get("updated_at", "")),
        )

    @staticmethod
    def _all_items(draft: PersonaProfileDraft) -> list[ProfileItem]:
        items: list[ProfileItem] = []
        for name in ITEM_SECTIONS:
            items.extend(_normalize_items(getattr(draft, name)))
        return items

    @staticmethod
    def _has_any_content(draft: PersonaProfileDraft) -> bool:
        """是否有任何证据支撑的内容（文本板块非空或任一条目非空）。"""
        for name in TEXT_SECTIONS:
            if str(getattr(draft, name).text or "").strip():
                return True
        return bool(PersonaService._all_items(draft))

    @staticmethod
    def _has_any_content_row(row: dict) -> bool:
        """**已存储版本**是否有任何内容（G7：用于拦住"空产出覆盖有内容旧版"）。

        与 `_has_any_content` 同口径，只是作用在存储行的原始 dict 上。
        """
        for name in TEXT_SECTIONS:
            section = row.get(name)
            if isinstance(section, dict) and str(section.get("text") or "").strip():
                return True
        for name in ITEM_SECTIONS:
            if _normalize_items(row.get(name)):
                return True
        return False

    @staticmethod
    def _compute_coverage(draft: PersonaProfileDraft) -> dict:
        """覆盖度统计（D-113）：每个板块有多少条目、多少条不同证据支撑。

        **由服务计算，不接受调用方填报**——覆盖度必须反映真实存储内容。
        """
        coverage: dict[str, dict[str, int]] = {}
        for name in TEXT_SECTIONS:
            section = getattr(draft, name)
            refs = _str_list(section.evidence_refs) if str(section.text or "").strip() else []
            coverage[name] = {"items": 1 if refs else 0,
                              "evidence_refs": len(set(refs))}
        for name in ITEM_SECTIONS:
            items = _normalize_items(getattr(draft, name))
            refs: set[str] = set()
            for item in items:
                refs.update(_str_list(item.evidence_refs))
            coverage[name] = {"items": len(items), "evidence_refs": len(refs)}
        return coverage

    @staticmethod
    def _content_of_draft(draft: PersonaProfileDraft) -> dict:
        """draft 的**内容指纹**（不含版本元数据），用于空转检测（D-112）。"""
        return {
            "background": _section_to_dict(draft.background),
            "traits": [_item_to_dict(i) for i in _normalize_items(draft.traits)],
            "habits": [_item_to_dict(i) for i in _normalize_items(draft.habits)],
            "expression_style": _section_to_dict(draft.expression_style),
            "experiences": [_item_to_dict(i) for i in _normalize_items(draft.experiences)],
            "single_events": [_item_to_dict(i) for i in _normalize_items(draft.single_events)],
            "canon_boundary": _canon_to_dict(draft.canon_boundary),
            "applicable_variant_ids": sorted(_str_list(draft.applicable_variant_ids)),
            "do_not_apply_to_variants": sorted(_str_list(draft.do_not_apply_to_variants)),
            "controversial_topic_ids": sorted(_str_list(draft.controversial_topic_ids)),
            "open_questions": sorted(_str_list(draft.open_questions)),
            "status": str(getattr(draft.status, "value", draft.status)),
        }

    @staticmethod
    def _content_of_row(row: dict) -> dict:
        """已存储版本的内容指纹（与 `_content_of_draft` 同形状，可直接 ==）。"""
        return {
            "background": row.get("background") or _section_to_dict(ProfileSection()),
            "traits": _normalize_items(row.get("traits")),
            "habits": _normalize_items(row.get("habits")),
            "expression_style": row.get("expression_style") or _section_to_dict(ProfileSection()),
            "experiences": _normalize_items(row.get("experiences")),
            "single_events": _normalize_items(row.get("single_events")),
            "canon_boundary": row.get("canon_boundary"),
            "applicable_variant_ids": sorted(_str_list(row.get("applicable_variant_ids"))),
            "do_not_apply_to_variants": sorted(_str_list(row.get("do_not_apply_to_variants"))),
            "controversial_topic_ids": sorted(_str_list(row.get("controversial_topic_ids"))),
            "open_questions": sorted(_str_list(row.get("open_questions"))),
            "status": str(row.get("status", "")),
        }

    # ------------------------------------------------------------ 内部：diff

    @staticmethod
    def _diff_rows(old: dict, new: dict) -> list[ProfileChange]:
        """字段级差异：板块文本、条目（按 item_id）、集合字段、状态。"""
        changes: list[ProfileChange] = []

        def _add(field_name: str, change_type: str, before: str = "",
                 after: str = "", item_id: str = "") -> None:
            changes.append(ProfileChange(field=field_name, change_type=change_type,
                                         item_id=item_id, before=before, after=after))

        # 文本板块
        for name in TEXT_SECTIONS:
            before_sec = _section_from_dict(old.get(name))
            after_sec = _section_from_dict(new.get(name))
            if before_sec.text != after_sec.text:
                _add(name, "changed", before_sec.text, after_sec.text)
            elif sorted(before_sec.evidence_refs) != sorted(after_sec.evidence_refs):
                _add(name, "changed",
                     f"evidence_refs={sorted(before_sec.evidence_refs)}",
                     f"evidence_refs={sorted(after_sec.evidence_refs)}")

        # 条目板块：按 item_id 对齐
        for name in ITEM_SECTIONS:
            timeline = name == SECTION_EXPERIENCES
            old_items = {i.item_id: i for i in
                         (_item_from_dict(d, timeline)
                          for d in _normalize_items(old.get(name)))}
            new_items = {i.item_id: i for i in
                         (_item_from_dict(d, timeline)
                          for d in _normalize_items(new.get(name)))}
            for item_id in sorted(set(old_items) - set(new_items)):
                _add(name, "removed", old_items[item_id].text, item_id=item_id)
            for item_id in sorted(set(new_items) - set(old_items)):
                _add(name, "added", after=new_items[item_id].text, item_id=item_id)
            for item_id in sorted(set(old_items) & set(new_items)):
                before_item, after_item = old_items[item_id], new_items[item_id]
                if (before_item.text != after_item.text
                        or sorted(before_item.evidence_refs) != sorted(after_item.evidence_refs)
                        or str(before_item.stability) != str(after_item.stability)
                        or before_item.confidence != after_item.confidence
                        or before_item.uncertainty_note != after_item.uncertainty_note):
                    _add(name, "changed", before_item.text, after_item.text,
                         item_id=item_id)

        # 集合字段
        for name in ("applicable_variant_ids", "do_not_apply_to_variants",
                     "controversial_topic_ids", "open_questions"):
            before_set = set(_str_list(old.get(name)))
            after_set = set(_str_list(new.get(name)))
            for value in sorted(before_set - after_set):
                _add(name, "removed", value)
            for value in sorted(after_set - before_set):
                _add(name, "added", after=value)

        # 正典边界与状态
        before_canon = _canon_from_dict(old.get("canon_boundary"))
        after_canon = _canon_from_dict(new.get("canon_boundary"))
        if before_canon != after_canon:
            _add("canon_boundary", "changed", str(before_canon), str(after_canon))
        if str(old.get("status", "")) != str(new.get("status", "")):
            _add("status", "changed", str(old.get("status", "")),
                 str(new.get("status", "")))

        changes.sort(key=lambda c: (c.field, c.change_type, c.item_id, c.before, c.after))
        return changes
