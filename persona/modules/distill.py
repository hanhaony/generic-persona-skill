"""人格蒸馏投影层（P8.2-B）——`distill_profile()` 只读纯函数。

职责边界（P8.2-A 审计拍板，本模块**不假装**会做语义理解）：
- 把已有 PersonaProfile 的桶结构**显式化**为 A/B/C 三层：
    A 层 stable_traits      = traits ∩ PATTERN（已通过稳定性判定）
    B 层 tentative_patterns = traits ∩ REPEATED + habits（一律标 tentative）
    C 层 insufficient       = single_events + open_questions + status != OK
- 补齐三类**可见性**（只展示、不裁决、不删除）：
    tier_composition   证据层级构成（含第三方评价占比）
    time_span          证据 stated_at 的时间跨度（时间变化不隐藏）
    conflicts_visible  冲突证据引用指针（冲突保留不裁决，D-013/D-055）
- **结构性禁止 B/C 升 A**：本模块没有任何写入口、没有升级通道；
  升级只能走既有链路（新证据入库 → judge_stability → revise 新版本）。

不做（NOT IMPLEMENTED，立项需预算审批）：
- AI 语义蒸馏主体 `distill(ctx, entity_id, options)`（Provider.chat）；
- 语义聚类、跨措辞同义归并、动机/价值观推断——投影层只呈现
  确定性规则能证明的东西（计数、构成、跨度、指针）。

纪律：
- **纯函数、零存储、零 AI、零新 ErrorCode**：不碰 Storage，不调 Provider，
  签名里没有 ctx；对同一 (profile, 证据现状) 的两次调用结果逐值相等。
- **不修改原始 profile**：只读访问，所有列表输出为拷贝。
- 防御性口径：traits 里出现非 PATTERN/REPEATED 的条目（正常情况被 AC-7
  挡在装配层）**不伪造归层**，落 C 层 `anomalous_traits` 如实暴露；
  证据引用查不到（missing）/ lookup 调用异常（failed，**与缺失分列**）/ 
  namespace 为 SIMULATED 的证据**不计入任何构成统计**，分别登记，
  并计入特征级 `unresolved_evidence_count`（P8.2-C D2/D3）。
- 冲突指针 **A/B/C 三层统一收集**，按 evidence_id 全局去重（P8.2-C D1）；
  引用先去重保序，重复引用只贡献一次统计（P8.2-C D5）。
- 字段命名不命中 FORBIDDEN_FIELD_PATTERN（evaluation 的关系禁词表）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.enums import ProfileStatus, Stability
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result
from .persona import PersonaProfileView

# --------------------------------------------------------------------------
# 层级常量
# --------------------------------------------------------------------------

#: A 层：稳定人格特征（来源 traits ∩ PATTERN）。
LAYER_STABLE = "A"
#: B 层：暂定人格倾向（来源 traits ∩ REPEATED + habits），恒 tentative。
LAYER_TENTATIVE = "B"
#: C 层：待确认内容（single_events + open_questions + status != OK）。
LAYER_INSUFFICIENT = "C"

#: traits 允许进 A/B 层的稳定性（与 AC-7 闸门口径一致：只分流，不复制桶逻辑）。
_TRAIT_STABILITY_A = str(Stability.PATTERN)
_TRAIT_STABILITY_B = str(Stability.REPEATED)

#: 模拟命名空间标记（与 ProvenanceNamespace.SIMULATED 同值；EV-V1 在证据层
#: 已结构性拒绝，这里是对"外部传入任意 lookup 结果"的防御性二次过滤）。
_SIMULATED = "SIMULATED"


# --------------------------------------------------------------------------
# DTO：蒸馏投影（不落库，随时可重算）
# --------------------------------------------------------------------------


@dataclass
class DistilledTrait:
    """一条蒸馏特征（A/B 层条目）。置信度**不造数值**——依据就是
    evidence_count / source_count / tier_composition / time_span
    （与 evidence_anchors 同源，PF-V4：confidence 只是排序权重恒 0.0）。"""

    layer: str                       # "A" / "B"
    item_id: str
    text: str
    stability: str                   # PATTERN（A）/ REPEATED 或登记值（B）
    tentative: bool                  # B 层恒 True；A 层 False
    evidence_refs: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    evidence_count: int = 0          # 引用的证据总数（D5：去重保序后）
    #: 证据层级构成：{"VERBATIM": n, "THIRD_PARTY": m, ...}——只统计
    #: 能解析且非 SIMULATED 的证据。第三方评价占比一目了然。
    tier_composition: dict[str, int] = field(default_factory=dict)
    #: THIRD_PARTY 占可解析证据的比例（0.0~1.0）；可解析证据为 0 时为 None。
    third_party_ratio: float | None = None
    #: 证据 stated_at 的时间跨度（最早~最晚，ISO 串）；不足两个不同起点为 None
    #: ——时间不可考就如实置 None，不编造（judge_stability 同口径）。
    time_span: tuple[str, str] | None = None
    uncertainty_note: str = ""
    #: 引用中**未能参与统计**的证据数（missing + failed + SIMULATED）——
    #: P8.2-C D2：特征级缺失一目了然，消费方不必自行用 composition 反推。
    unresolved_evidence_count: int = 0


@dataclass
class RawItemRef:
    """C 层条目的最小透传形状（文本沿用档案原文，引用链不断）。"""

    item_id: str
    text: str
    evidence_refs: list[str] = field(default_factory=list)
    stability: str = str(Stability.UNKNOWN)


@dataclass
class InsufficientBlock:
    """C 层：证据不足、不能确定的内容。**不得表述为稳定人格特征**。"""

    profile_status: str              # 档案状态原样（OK / INSUFFICIENT_EVIDENCE / ...）
    #: status != OK 时的说明（OK 时为空串）。
    status_note: str = ""
    single_events: list[RawItemRef] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    #: 防御性出口：traits 里混入的非 PATTERN/REPEATED 条目（AC-7 正常会挡住，
    #: 出现即说明输入异常）——不伪造归层，如实暴露。
    anomalous_traits: list[RawItemRef] = field(default_factory=list)


@dataclass
class ConflictPointer:
    """冲突证据引用指针（只指向，不裁决——D-013/D-055：冲突保留不删除）。"""

    item_id: str                     # 所在档案条目
    layer: str                       # "A" / "B" / "C"
    evidence_id: str
    conflict_ids: list[str] = field(default_factory=list)


@dataclass
class ConfidenceBasis:
    """A+B 层合计的置信度**依据**（不是分数）。"""

    #: 全投影范围内（A/B/C 所有条目）的证据解析情况。
    resolved_evidence_count: int = 0
    missing_evidence_ids: list[str] = field(default_factory=list)
    #: lookup **调用异常**的证据 ID（与"正常返回 None = 证据不存在"分列——
    #: P8.2-C D3：查询故障不得静默降级成证据缺失）。
    failed_evidence_ids: list[str] = field(default_factory=list)
    simulated_evidence_ids: list[str] = field(default_factory=list)
    #: A+B 层各 tier 合计（第三方评价占比的全局口径）。
    tier_totals: dict[str, int] = field(default_factory=dict)
    third_party_ratio: float | None = None


@dataclass
class DistilledProfileView:
    """人格蒸馏投影（只读、可重算、不落库——不建第二个人格库）。

    与 profile.version 绑定：档案 revise 出新版本后重算即可，
    版本管理完全复用现有 revise / diff_versions 链。
    """

    entity_id: str
    profile_version: int
    stable_traits: list[DistilledTrait] = field(default_factory=list)
    tentative_patterns: list[DistilledTrait] = field(default_factory=list)
    insufficient: InsufficientBlock = field(default_factory=InsufficientBlock)
    confidence_basis: ConfidenceBasis = field(default_factory=ConfidenceBasis)
    conflicts_visible: list[ConflictPointer] = field(default_factory=list)


# --------------------------------------------------------------------------
# 纯函数实现
# --------------------------------------------------------------------------

#: 外部注入的证据查询：evidence_id -> EvidenceView | dict | None。
#: 查不到返回 None（如实记 missing，不伪造）。
EvidenceLookup = Callable[[str], Any]


def distill_profile(profile: PersonaProfileView,
                    evidence_lookup: EvidenceLookup,
                    ) -> Result[DistilledProfileView]:
    """把 PersonaProfile 投影为 A/B/C 显式分层（只读纯函数）。

    参数
    ----
    profile : PersonaProfileView
        档案读视图（本函数只读，绝不修改）。
    evidence_lookup : callable
        `evidence_id -> EvidenceView | dict | None`。支持 EvidenceView
        属性访问与 dict 两种形状；返回 None 表示证据查不到。

    返回
    ----
    Result[DistilledProfileView]：profile 不是 PersonaProfileView 或
    lookup 不可调用时 Err（VALIDATION_GATE_REJECTED，同 build_behavior_policy 先例）。
    """
    if not isinstance(profile, PersonaProfileView):
        return Err(new_error(
            ErrorCode.VALIDATION_GATE_REJECTED,
            "profile 必须是 PersonaProfileView"))
    if not callable(evidence_lookup):
        return Err(new_error(
            ErrorCode.VALIDATION_GATE_REJECTED,
            "evidence_lookup 必须是 callable（evidence_id -> EvidenceView | dict | None）"))

    # —— 第一遍：收集全部证据引用并统一解析（会话内去重缓存）——
    all_refs: list[str] = []
    for item in (*profile.traits, *profile.habits, *profile.single_events):
        all_refs.extend(str(x) for x in (item.evidence_refs or []))
    for section in (profile.background, profile.expression_style):
        all_refs.extend(str(x) for x in (section.evidence_refs or []))
    resolved: dict[str, Any] = {}
    failed: set[str] = set()          # P8.2-C D3：lookup 异常单独登记
    for ref in dict.fromkeys(all_refs):          # 去重保序
        try:
            resolved[ref] = evidence_lookup(ref)
        except Exception:                         # noqa: BLE001 —— lookup 异常按"查询失败"登记，不伪造
            resolved[ref] = None
            failed.add(ref)

    view = DistilledProfileView(
        entity_id=str(profile.entity_id),
        profile_version=int(profile.version),
        insufficient=InsufficientBlock(profile_status=str(profile.status)),
    )
    basis = view.confidence_basis
    if str(profile.status) != str(ProfileStatus.OK):
        view.insufficient.status_note = (
            f"档案状态 {profile.status} != OK——本档案全部内容按待确认处理，"
            "不得表述为稳定人格特征")

    # —— A 层：traits ∩ PATTERN ——
    # —— B 层：traits ∩ REPEATED + habits（恒 tentative）——
    # —— 防御：traits 里其余稳定性（SINGLE/UNKNOWN/…）落 C 层 ——
    for item in profile.traits:
        stability = _stability_of(item)
        if stability == _TRAIT_STABILITY_A:
            view.stable_traits.append(_distill_item(item, LAYER_STABLE, resolved, view, failed))
        elif stability == _TRAIT_STABILITY_B:
            view.tentative_patterns.append(
                _distill_item(item, LAYER_TENTATIVE, resolved, view, failed))
        else:
            view.insufficient.anomalous_traits.append(
                _c_item(item, stability, resolved, view, failed))
    for item in profile.habits:
        view.tentative_patterns.append(
            _distill_item(item, LAYER_TENTATIVE, resolved, view, failed))

    # —— C 层：single_events + open_questions + status ——
    for item in profile.single_events:
        view.insufficient.single_events.append(
            _c_item(item, _stability_of(item), resolved, view, failed))
    view.insufficient.open_questions = [str(q) for q in (profile.open_questions or [])]

    # —— 全局依据合计（missing/simulated 已在解析与逐条统计中登记）——
    third = int(basis.tier_totals.get("THIRD_PARTY", 0))
    total = sum(basis.tier_totals.values())
    basis.third_party_ratio = round(third / total, 4) if total else None
    return Ok(view)


# --------------------------------------------------------------------------
# 内部辅助（全部无副作用）
# --------------------------------------------------------------------------


def _stability_of(item: Any) -> str:
    """条目的稳定性（登记值；EvidenceView.effective_stability 同口径）。"""
    return str(getattr(getattr(item, "stability", None), "value",
                       getattr(item, "stability", "")))


def _raw_ref(item: Any, stability: str) -> RawItemRef:
    return RawItemRef(
        item_id=str(getattr(item, "item_id", "")),
        text=str(getattr(item, "text", "")),
        evidence_refs=[str(x) for x in (getattr(item, "evidence_refs", None) or [])],
        stability=stability,
    )


def _dedupe_refs(refs: list[str]) -> list[str]:
    """P8.2-C D5：证据引用去重保序——重复引用只贡献一次统计。"""
    return list(dict.fromkeys(refs))


def _register_evidence(ref: str, ev: Any, view: DistilledProfileView,
                       failed: set[str], item_id: str, layer: str) -> bool:
    """登记单条证据解析结果的问题与冲突指针；返回该证据是否可参与统计。

    - lookup 返回 None：missing_evidence_ids（证据不存在，如实登记）；
    - lookup 曾抛异常（ref ∈ failed）：failed_evidence_ids（查询故障，
      **与"不存在"分列**，不静默降级——P8.2-C D3）；
    - namespace=SIMULATED：simulated_evidence_ids（防御过滤，EV-V1 精神）；
    - 证据带 conflict_ids：登记 ConflictPointer（按 evidence_id 全局去重——
      多条目引用同一冲突只报一次，P8.2-C D1；A/B/C 三层统一收集）。
    各登记列表均查重，同一 ID 不重复记录。
    """
    basis = view.confidence_basis
    if ev is None:
        target = basis.failed_evidence_ids if ref in failed \
            else basis.missing_evidence_ids
        if ref not in target:
            target.append(ref)
        return False
    if str(_ev_field(ev, "namespace") or "").upper() == _SIMULATED:
        if ref not in basis.simulated_evidence_ids:
            basis.simulated_evidence_ids.append(ref)
        return False
    conflict_ids = [str(c) for c in (_ev_field(ev, "conflict_ids") or [])]
    if conflict_ids and not any(p.evidence_id == ref
                                for p in view.conflicts_visible):
        view.conflicts_visible.append(ConflictPointer(
            item_id=item_id, layer=layer, evidence_id=ref,
            conflict_ids=conflict_ids))
    return True


def _c_item(item: Any, stability: str, resolved: dict[str, Any],
            view: DistilledProfileView, failed: set[str]) -> RawItemRef:
    """C 层条目：透传原文 + 问题登记 + 冲突收集（P8.2-C D1——C 层同样可见）。"""
    raw = _raw_ref(item, stability)
    raw.evidence_refs = _dedupe_refs(raw.evidence_refs)
    for ref in raw.evidence_refs:
        _register_evidence(ref, resolved.get(ref), view, failed,
                           raw.item_id, LAYER_INSUFFICIENT)
    return raw


def _ev_field(ev: Any, name: str) -> Any:
    """EvidenceView 属性 / dict 键 两形状统一取值。"""
    if ev is None:
        return None
    if isinstance(ev, dict):
        return ev.get(name)
    return getattr(ev, name, None)


def _distill_item(item: Any, layer: str, resolved: dict[str, Any],
                  view: DistilledProfileView, failed: set[str]) -> DistilledTrait:
    """单条档案条目 → 蒸馏特征（tier 构成 / 时间跨度 / 冲突指针）。

    引用先去重保序（D5）；无法参与统计的证据（missing/failed/SIMULATED）
    统一登记并计入 `unresolved_evidence_count`（D2）——不伪造。
    """
    basis = view.confidence_basis
    refs = _dedupe_refs([str(x) for x in
                         (getattr(item, "evidence_refs", None) or [])])
    trait = DistilledTrait(
        layer=layer,
        item_id=str(getattr(item, "item_id", "")),
        text=str(getattr(item, "text", "")),
        stability=_stability_of(item),
        tentative=(layer == LAYER_TENTATIVE),
        evidence_refs=list(refs),
        evidence_count=len(refs),
        uncertainty_note=str(getattr(item, "uncertainty_note", "") or ""),
    )
    tier_counts: dict[str, int] = {}
    sources: list[str] = []
    starts: set[str] = set()
    resolvable = 0
    for ref in refs:
        if not _register_evidence(ref, resolved.get(ref), view, failed,
                                  trait.item_id, layer):
            continue
        resolvable += 1
        ev = resolved.get(ref)
        tier = str(_ev_field(ev, "tier") or "").upper()
        if tier:
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
            basis.tier_totals[tier] = basis.tier_totals.get(tier, 0) + 1
        source_id = _ev_field(ev, "source_id")
        if source_id and str(source_id) not in sources:
            sources.append(str(source_id))
        stated = _ev_field(ev, "stated_at")
        start = _ev_field(stated, "start") if stated is not None else None
        if start:
            starts.add(str(start))
    trait.source_ids = sources
    trait.source_count = len(sources)
    trait.tier_composition = tier_counts
    basis.resolved_evidence_count += resolvable
    trait.unresolved_evidence_count = trait.evidence_count - resolvable
    if resolvable:
        trait.third_party_ratio = round(
            tier_counts.get("THIRD_PARTY", 0) / resolvable, 4)
    if len(starts) >= 2:
        trait.time_span = (min(starts), max(starts))
    return trait
