"""Relationship —— 人物关系档案基础模块（docs/INTERFACES.md §7、DATA_MODEL §4.8）。

**P3 第二步**（用户称之为 P3.3；仓库 ROADMAP 的 P3 = 人物档案 + 关系档案，
编号差异见 DECISIONS §3 Q-37）：关系档案的**本地管理基础**——创建、查询、
更新（版本化）、持久化、**双向视角**、共同经历与时间变化、证据关联与追溯、
冲突与不确定性保留、多人物群体场景、虚构版本边界、模拟内容隔离。

**职责边界（INTERFACES §7.1）**：
    ✅ 关系档案存储与版本管理、证据关联校验（RL-V1~V6）、冲突关联（不裁决）、
       覆盖度统计、群体互动（≥3 人）
    ⛔ **不直接调用任何具体模型供应商**（F-5）：本文件不 import Provider，
       没有任何 search / collect / query_plan——检索是 `Research` 的事。
    ⛔ **不实现 `distill` 的 AI 蒸馏主体**（INTERFACES §7.2 的
       `distill(ctx, participant_entity_ids, options)` 要调 `Provider.chat`）：
       那属于下一阶段。本模块提供它落盘前**必须经过**的存储/校验/版本层。
    ⛔ **不从两份人格档案推导现实关系**（RL-V6）：每条关系条目必须至少引用
       一条 `EvidenceItem`；只引用 `ProfileItem.item_id`（档案字段）的依赖
       一律拒绝——关系必须有证据，不能由两份档案"拼"出来。
    ⛔ 不合并双方视角（D-018）：A→B 与 B→A 各占一个格子，系统不生成
       "综合定论"字段（RL-V5）。
    ⛔ 不产出任何"亲密度 / 强度"数值（D-048 / RL-V2）：字段名与 payload 键名
       都不许出现 score / closeness / intimacy（入口 + 落盘双重扫描）。

**三条硬规则**（与证据、人格档案模块一致的精神）：
    - 每个非空关系条目必须带 `evidence_refs`；没证据就留空并记
      `open_questions`（D-020），**不允许靠想象补全**。
    - `confidence` 只用于排序展示，**不代表真伪**（D-010）；
      结构上没有任何"真实性"字段。
    - `namespace` 恒为 `SYSTEM_DERIVED`（DATA_MODEL §4.8 数据来源）：
      档案是加工产物，模拟内容永远不能变成关系依据（模块层 + 存储层双层闸门）。

**诚实边界（本阶段不做的）**：
    - `distill`（AI 蒸馏）未实现——不调模型，不假装会"读证据写档案"；
      本模块的写入入口是 `create_relationship` / `revise`（内容由调用方给出）。
    - 语义级冲突检测（"这两条说法意思矛盾"）需要模型，未实现；
      已实现的是确定性结构校验（RL-V1~V6 + 版本边界 + 引用完整性）。
    - Q-19 的"保留最近 5 版"裁剪未实现：当前**全版本保留**（不删用户数据）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.context import CallContext
from ..core.enums import (
    Cardinality,
    EntityType,
    ProfileStatus,
    ProvenanceNamespace,
    Stability,
)
from ..core.errors import ErrorCode, new_error
from ..core.ids import new_id, prefix_of
from ..core.result import Err, Ok, Result, is_err, unwrap, with_warning
from ..core.types import MAX_QUERY_LIMIT, Page, Query
from ..storage.sqlite import SqliteStorage

# 复用 Persona 模块的公共数据契约与序列化辅助（DATA_MODEL §4.15 的
# `ProfileItem` / `TimelineItem` 是跨实体复用的复合类型；INTERFACES §7.1
# 允许 Relationship 只读依赖 Persona）。不重复造轮子。
from .persona import (
    ProfileItem,
    TimelineItem,
    _coerce,
    _confidence,
    _item_from_dict,
    _item_to_dict,
    _str_list,
)

# --------------------------------------------------------------------------
# 警告码（业务提示，不是错误；ERROR_HANDLING §2.3）
# --------------------------------------------------------------------------

WARNING_RELATIONSHIP_SUPERSEDED = "RELATIONSHIP_SUPERSEDED"  # 修订产生新版本，旧版已归档
WARNING_CONFLICT_LINKED = "CONFLICT_LINKED"                # 档案关联了冲突（未裁决）
# G7（A 级写风险）：本次修订**产出为空**，已拦下、旧版保留（不降级覆盖）
WARNING_RELATIONSHIP_DEGRADED_SKIPPED = "RELATIONSHIP_DEGRADED_SKIPPED"

# 关系档案的内容板块（覆盖度统计的键）
SECTION_SHARED_EXPERIENCES = "shared_experiences"
SECTION_INTERACTION_PATTERNS = "interaction_patterns"
SECTION_EMOTIONAL_EXPRESSION = "emotional_expression_habits"
SECTION_TEMPORAL_CHANGES = "temporal_changes"
SECTION_ASYMMETRY_NOTES = "asymmetry_notes"

ITEM_SECTIONS: tuple[str, ...] = (
    SECTION_SHARED_EXPERIENCES,
    SECTION_INTERACTION_PATTERNS,
    SECTION_EMOTIONAL_EXPRESSION,
    SECTION_TEMPORAL_CHANGES,
    SECTION_ASYMMETRY_NOTES,
)

# 互动模式是**模式性声明**（"总是 A 主导、B 回应"）：单次互动不得固化
# 为长期模式（用户目标 5 / D-012 同精神）。SINGLE 请放 shared_experiences。
GATED_SECTIONS: tuple[str, ...] = (SECTION_INTERACTION_PATTERNS,)
PATTERN_STABILITY: frozenset[str] = frozenset(
    {str(Stability.REPEATED), str(Stability.PATTERN)})

# 视角块内的条目字段（RL-V4：每个视角条目都必须带 evidence_refs）
PERSPECTIVE_ITEM_FIELDS: tuple[str, ...] = (
    "view_of_targets", "feeling_toward", "expression_toward")

# 群体互动的三个方面（DATA_MODEL §4.8：联盟、站队、冷落、传话）
GROUP_DYNAMICS_SECTIONS: tuple[str, ...] = ("alliances", "exclusion", "relay")

# RL-V2 / D-048：禁止出现的字段名标记（入口扫描 + payload 落盘前扫描）
FORBIDDEN_FIELD_MARKERS: tuple[str, ...] = ("score", "closeness", "intimacy")

# query_relationships 允许的过滤键（payload 字段 + 结构列）
RELATIONSHIP_FILTER_KEYS: frozenset[str] = frozenset(
    {"participant_entity_ids", "cardinality", "status", "version",
     "applicable_variant_ids", "namespace"})
# 允许排序的列
SORTABLE_COLUMNS: frozenset[str] = frozenset(
    {"created_at", "updated_at", "id", "version"})

# 内容指纹的键（不含版本元数据；用于空转检测，D-112 同口径）
CONTENT_KEYS: tuple[str, ...] = (
    "participant_entity_ids", "cardinality", "perspectives",
    SECTION_SHARED_EXPERIENCES, SECTION_INTERACTION_PATTERNS,
    SECTION_EMOTIONAL_EXPRESSION, SECTION_TEMPORAL_CHANGES,
    "group_dynamics", SECTION_ASYMMETRY_NOTES, "conflict_ids",
    "applicable_variant_ids", "open_questions", "status",
)


def _normalize_items(items: Any) -> list:
    """把 None / 单项 / 列表统一成列表。"""
    if items is None:
        return []
    if isinstance(items, (list, tuple)):
        return list(items)
    return [items]


# --------------------------------------------------------------------------
# 端口协议（P1.2-B 定义；本阶段按 INTERFACES §7.2 与已实现面对齐，同 D-119 先例）
# --------------------------------------------------------------------------


class RelationshipPort(Protocol):
    """关系档案的**写入 + 查询**接口。只有本模块能写关系档案表。

    签名与 `docs/INTERFACES.md §7.2` 对齐。P1.2-B 骨架里的旧签名
    （`distill(participant_entity_ids, evidence_ids)` 等）与文档不一致，
    本阶段按**文档为准**更正（同 D-110 / D-119 先例）；
    `distill` 的 AI 蒸馏主体尚未实现（要调 `Provider.chat`），
    待下一阶段补入协议，本次不虚占签名。
    """

    def create_relationship(
        self, ctx: CallContext, draft: "RelationshipDraft"
    ) -> Result["RelationshipRef"]:
        """创建关系档案 v1。同一参与者集合已存在 → 用 revise。"""
        ...

    def revise(
        self, ctx: CallContext, participant_entity_ids: list[str],
        draft: "RelationshipDraft",
    ) -> Result["RelationshipRef"]:
        """基于新内容生成**新版本**；旧版归档保留（superseded_by 链）。

        内容与最新版完全一致 → `Err(STATE_CONFLICT.no_new_evidence)`
        （不生成空转版本）；尚无档案 → `Err(NOT_FOUND.entity)`。
        参与者集合按**集合**匹配（存储顺序不影响身份）。
        """

    def get_relationship(
        self, ctx: CallContext, key: str | list[str], version: int | None = None
    ) -> Result["RelationshipView"]:
        """读取档案；`key` 可以是 `rlp_` ID 或参与者 ID 列表；
        `version=None` 返回最新版。"""
        ...

    def query_relationships(
        self, ctx: CallContext, q: Query
    ) -> Result[Page["RelationshipView"]]:
        """按条件查询档案（**含历史版本**；过滤键白名单外一律拒绝）。"""
        ...

    def diff_versions(
        self, ctx: CallContext, key: str | list[str], v_from: int, v_to: int
    ) -> Result["RelationshipDiff"]:
        """字段级差异（只读），供审查与评测使用。"""
        ...

    def apply_pattern_gate(
        self, ctx: CallContext, item: ProfileItem,
        target_section: str = SECTION_INTERACTION_PATTERNS,
    ) -> Result[ProfileItem]:
        """模式闸门（D-012 同精神 / 用户目标 5）：`SINGLE` 不许进
        `interaction_patterns`——单次互动不得固化为长期模式。"""
        ...

    def validate_evidence_refs(
        self, ctx: CallContext, entries: list,
        participant_entity_ids: list[str] | None = None,
    ) -> Result[bool]:
        """RL-V6 校验：每条条目都必须直接引用 `EvidenceItem`；
        只引用档案字段（`ProfileItem.item_id`）视为非法。"""
        ...


class RelationshipReader(Protocol):
    """**只读**端口（供 Dialogue 使用）。"""

    def get_relationship(
        self, ctx: CallContext, key: str | list[str], version: int | None = None
    ) -> Result["RelationshipView"]:
        ...

    def query_relationships(
        self, ctx: CallContext, q: Query
    ) -> Result[Page["RelationshipView"]]:
        ...


# --------------------------------------------------------------------------
# 数据契约（Draft 进 / View 出；跨模块只传 ID 与这些形状，不传数据库行）
# --------------------------------------------------------------------------


@dataclass
class PerspectiveBlock:
    """单人视角块（DATA_MODEL §4.8 `PerspectiveBlock`）。

    **A 怎么看 B，和 B 怎么看 A，存在两个不同的格子里**（D-018）。
    系统不会把它们融合成"他们的关系是这样的"——`view_of_targets`
    按目标人物分键，`feeling_toward` / `expression_toward` 是本人对
    其他参与者的情绪倾向与表达习惯。
    """

    entity_id: str
    view_of_targets: dict[str, list[ProfileItem]] = field(default_factory=dict)
    feeling_toward: list[ProfileItem] = field(default_factory=list)
    expression_toward: list[ProfileItem] = field(default_factory=list)
    uncertainty_note: str = ""


@dataclass
class GroupDynamics:
    """群体互动（DATA_MODEL §4.8 `GroupDynamics`；≥3 人时必填，RL-V3）。

    内嵌结构为 D-121 定形（DATA_MODEL 未定义其字段）：联盟、冷落、传话
    三个可定位方面 + 自由文本备注。每个条目与其他板块一样必须带
    `evidence_refs`（RL-V4）。
    """

    alliances: list[ProfileItem] = field(default_factory=list)   # 联盟
    exclusion: list[ProfileItem] = field(default_factory=list)   # 站队 / 冷落
    relay: list[ProfileItem] = field(default_factory=list)       # 传话 / 信息中转
    note: str = ""


@dataclass
class SharedExperienceItem(TimelineItem):
    """共同经历条目（DATA_MODEL §4.8：共同经历"含时间与参与者"）。

    `TimelineItem` 已带时间（`occurred_at` / `work_internal_time`），
    但不带**逐条目参与者**——群体场景下一次经历可能只涉及部分成员
    （D-122：本地扩展，不修改已验收的 `TimelineItem`）。
    `participant_entity_ids` 为空 = 全体档案参与者；非空时必须是
    档案参与者的子集。
    """

    participant_entity_ids: list[str] = field(default_factory=list)


@dataclass
class RelationshipDraft:
    """创建/修订关系档案的请求（DATA_MODEL §4.8 必填项 + INTERFACES §7.2）。

    `change_reason` 是**更新依据**：revise 时必填（D-112 同口径），
    create 时可省略（默认"初始版本"）——版本变化永不静默发生。
    """

    participant_entity_ids: list[str]
    cardinality: Cardinality | str
    perspectives: dict[str, PerspectiveBlock]
    shared_experiences: list[SharedExperienceItem] = field(default_factory=list)
    interaction_patterns: list[ProfileItem] = field(default_factory=list)
    emotional_expression_habits: list[ProfileItem] = field(default_factory=list)
    temporal_changes: list[TimelineItem] = field(default_factory=list)
    group_dynamics: GroupDynamics | None = None
    asymmetry_notes: list[ProfileItem] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    applicable_variant_ids: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    status: ProfileStatus | str = ProfileStatus.INSUFFICIENT_EVIDENCE
    change_reason: str = ""


@dataclass
class RelationshipRef:
    """创建/修订后返回的最小形状。"""

    id: str
    participant_entity_ids: list[str]
    version: int
    status: str


@dataclass
class RelationshipView:
    """关系档案的完整读视图（含版本与归档信息）。"""

    id: str
    participant_entity_ids: list[str]
    cardinality: str
    namespace: str
    version: int
    perspectives: dict[str, PerspectiveBlock]
    shared_experiences: list[SharedExperienceItem]
    interaction_patterns: list[ProfileItem]
    emotional_expression_habits: list[ProfileItem]
    temporal_changes: list[TimelineItem]
    group_dynamics: GroupDynamics | None
    asymmetry_notes: list[ProfileItem]
    conflict_ids: list[str]
    applicable_variant_ids: list[str]
    open_questions: list[str]
    coverage: dict
    status: str
    superseded_by: str | None
    change_reason: str
    created_at: str = ""
    updated_at: str = ""


@dataclass
class RelationshipChange:
    """diff 中的一处字段级变化。"""

    field: str
    change_type: str            # added / removed / changed
    item_id: str = ""           # 条目级变化时带
    entity_id: str = ""         # 视角块变化时带（视角归属人）
    before: str = ""
    after: str = ""


@dataclass
class RelationshipDiff:
    """两个版本之间的字段级差异（INTERFACES §7.2）。"""

    participant_key: str        # 参与者集合的稳定字符串表示
    v_from: int
    v_to: int
    changes: list[RelationshipChange]
    to_version_change_reason: str = ""


# --------------------------------------------------------------------------
# 序列化辅助（存储 payload 只放可 JSON 化的 plain 数据）
# --------------------------------------------------------------------------


def _shared_to_dict(item: SharedExperienceItem) -> dict:
    data = _item_to_dict(item)          # TimelineItem 的形状（含时间）
    data["participant_entity_ids"] = _str_list(item.participant_entity_ids)
    return data


def _shared_from_dict(data: Any) -> SharedExperienceItem:
    base = _item_from_dict(data, timeline=True)
    return SharedExperienceItem(
        item_id=base.item_id, text=base.text, confidence=base.confidence,
        evidence_refs=base.evidence_refs, stability=base.stability,
        namespace=base.namespace, uncertainty_note=base.uncertainty_note,
        occurred_at=base.occurred_at, work_internal_time=base.work_internal_time,
        participant_entity_ids=_str_list(
            data.get("participant_entity_ids") if isinstance(data, dict) else None),
    )


def _perspective_to_dict(block: PerspectiveBlock) -> dict:
    return {
        "entity_id": str(block.entity_id),
        "view_of_targets": {
            str(target): [_item_to_dict(i) for i in _normalize_items(items)]
            for target, items in (block.view_of_targets or {}).items()
        },
        "feeling_toward": [_item_to_dict(i)
                           for i in _normalize_items(block.feeling_toward)],
        "expression_toward": [_item_to_dict(i)
                              for i in _normalize_items(block.expression_toward)],
        "uncertainty_note": str(block.uncertainty_note or ""),
    }


def _perspective_from_dict(data: Any) -> PerspectiveBlock:
    if not isinstance(data, dict):
        data = {}
    raw_targets = data.get("view_of_targets")
    targets: dict[str, list[ProfileItem]] = {}
    if isinstance(raw_targets, dict):
        for target, items in raw_targets.items():
            targets[str(target)] = [_item_from_dict(i)
                                    for i in _normalize_items(items)]
    return PerspectiveBlock(
        entity_id=str(data.get("entity_id", "")),
        view_of_targets=targets,
        feeling_toward=[_item_from_dict(i)
                        for i in _normalize_items(data.get("feeling_toward"))],
        expression_toward=[_item_from_dict(i)
                           for i in _normalize_items(data.get("expression_toward"))],
        uncertainty_note=str(data.get("uncertainty_note") or ""),
    )


def _group_to_dict(group: GroupDynamics | None) -> dict | None:
    if group is None:
        return None
    return {
        "alliances": [_item_to_dict(i) for i in _normalize_items(group.alliances)],
        "exclusion": [_item_to_dict(i) for i in _normalize_items(group.exclusion)],
        "relay": [_item_to_dict(i) for i in _normalize_items(group.relay)],
        "note": str(group.note or ""),
    }


def _group_from_dict(data: Any) -> GroupDynamics | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        return None
    return GroupDynamics(
        alliances=[_item_from_dict(i) for i in _normalize_items(data.get("alliances"))],
        exclusion=[_item_from_dict(i) for i in _normalize_items(data.get("exclusion"))],
        relay=[_item_from_dict(i) for i in _normalize_items(data.get("relay"))],
        note=str(data.get("note") or ""),
    )


def _scan_forbidden_keys(obj: Any, path: str = "") -> str | None:
    """RL-V2 / D-048：递归扫描 payload 键名，命中 score/closeness/intimacy 即违规。

    返回第一个违规键的路径（便于报错）；干净返回 None。
    """
    if isinstance(obj, dict):
        for key, value in obj.items():
            low = str(key).lower()
            if any(marker in low for marker in FORBIDDEN_FIELD_MARKERS):
                return f"{path}{key}"
            found = _scan_forbidden_keys(value, f"{path}{key}.")
            if found is not None:
                return found
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            found = _scan_forbidden_keys(value, f"{path}[{index}].")
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------
# 服务实现
# --------------------------------------------------------------------------


class RelationshipService:
    """关系档案的本地管理基础。**只依赖 `Storage`**，不碰 Provider（F-2/F-5）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self.storage = storage

    # ------------------------------------------------------------ 创建

    def create_relationship(
        self, ctx: CallContext, draft: RelationshipDraft
    ) -> Result[RelationshipRef]:
        """创建关系档案 v1（`version=1`，`superseded_by=None`）。

        同一参与者集合已存在档案 → `Err(STATE_CONFLICT.no_new_evidence)`：
        档案是版本化实体，更新必须走 `revise`（保留旧版）。
        """
        participants = _str_list(draft.participant_entity_ids)
        existing = self._relationships_of(ctx, set(participants))
        if is_err(existing):
            return existing
        if existing.value:
            latest = max(existing.value, key=lambda r: int(r.get("version", 0)))
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE,
                f"这些参与者之间的关系档案已存在（最新版本 "
                f"v{latest.get('version')}）；更新请用 revise 生成新版本，"
                "旧版本会归档保留",
                trace_ref=ctx.trace_ref))
        problem = self._validate_draft(ctx, draft, require_reason=False)
        if problem is not None:
            return problem
        return self._write_relationship(ctx, draft, version=1,
                                        supersede_id=None)

    # ------------------------------------------------------------ 修订

    def revise(
        self, ctx: CallContext, participant_entity_ids: list[str],
        draft: RelationshipDraft,
    ) -> Result[RelationshipRef]:
        """基于新内容生成**新版本**；旧版打 `superseded_by` 归档（D-111 同口径）。

        内容与最新版**完全一致** → `Err(STATE_CONFLICT.no_new_evidence)`
        （D-112 同口径：不生成空转版本）。`change_reason` 必填（更新依据）。
        参与者集合按**集合**匹配：传入顺序不影响身份。
        """
        given = set(_str_list(participant_entity_ids))
        draft_set = set(_str_list(draft.participant_entity_ids))
        if given != draft_set:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"draft.participant_entity_ids（{sorted(draft_set)}）与 revise "
                f"目标（{sorted(given)}）不一致", trace_ref=ctx.trace_ref))
        existing = self._relationships_of(ctx, given)
        if is_err(existing):
            return existing
        if not existing.value:
            return Err(new_error(
                ErrorCode.NOT_FOUND_ENTITY,
                f"参与者 {sorted(given)} 尚无关系档案，请先 create_relationship",
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
        if self._content_of_draft(draft) == self._content_of_row(latest):
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE,
                "关系内容与最新版完全一致，不生成新版本"
                "（如确需记录说明，请先修改内容）", trace_ref=ctx.trace_ref))
        # —— G7（A 级写风险）：**空产出绝不覆盖有内容的旧版** ——
        # 同 persona.revise：旧版 `shared=1`、新版 `shared=0` 时内容指纹不相等，
        # 会写出空的新版本并把旧版 `superseded_by` 指过去，默认读取即降级为空档案
        # （P4.5-B 审查实测：关系档案 v1 → v2 `INSUFFICIENT_EVIDENCE shared=0`）。
        if not self._has_any_content(draft) and self._has_any_content_row(latest):
            result = Ok(RelationshipRef(
                id=str(latest["id"]),
                participant_entity_ids=_str_list(latest.get("participant_entity_ids")),
                version=int(latest.get("version", 0)),
                status=str(latest.get("status", ""))))
            return with_warning(
                result, WARNING_RELATIONSHIP_DEGRADED_SKIPPED,
                f"本次修订未产出任何内容（各维度为空），不生成新版本；"
                f"保留 v{latest.get('version')}——空产出绝不覆盖有内容的档案")
        return self._write_relationship(
            ctx, draft, version=int(latest.get("version", 0)) + 1,
            supersede_id=str(latest["id"]))

    # ------------------------------------------------------------ 读取

    def get_relationship(
        self, ctx: CallContext, key: str | list[str],
        version: int | None = None,
    ) -> Result[RelationshipView]:
        """读取档案；`key` 为 `rlp_` ID 或参与者 ID 列表；`version=None` 取最新版。"""
        if isinstance(key, str):
            if prefix_of(key) != "RelationshipProfile":
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"key 必须是 RelationshipProfile ID（rlp_ 前缀）或参与者 "
                    f"ID 列表，收到 {key!r}", trace_ref=ctx.trace_ref))
            row = self._get_row(ctx, "RelationshipProfile", key)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(
                    ErrorCode.NOT_FOUND_ENTITY,
                    f"找不到关系档案：{key!r}", trace_ref=ctx.trace_ref))
            if version is not None and \
                    int(row.value.get("version", 0)) != int(version):
                return Err(new_error(
                    ErrorCode.NOT_FOUND_VERSION,
                    f"关系档案 {key} 没有 v{version}"
                    f"（现有版本：v{row.value.get('version')}）",
                    trace_ref=ctx.trace_ref))
            return Ok(self._relationship_view(row.value))
        loaded = self._relationships_of(ctx, set(_str_list(key)))
        if is_err(loaded):
            return loaded
        rows = loaded.value
        if not rows:
            return Err(new_error(
                ErrorCode.NOT_FOUND_ENTITY,
                f"找不到关系档案：参与者 {sorted(set(_str_list(key)))}"
                + (f"（版本 v{version}）" if version is not None else ""),
                trace_ref=ctx.trace_ref))
        if version is None:
            row = max(rows, key=lambda r: int(r.get("version", 0)))
        else:
            matches = [r for r in rows if int(r.get("version", 0)) == int(version)]
            if not matches:
                return Err(new_error(
                    ErrorCode.NOT_FOUND_VERSION,
                    f"关系档案没有 v{version}（现有版本："
                    f"{sorted(int(r.get('version', 0)) for r in rows)}）",
                    trace_ref=ctx.trace_ref))
            row = matches[0]
        return Ok(self._relationship_view(row))

    def query_relationships(
        self, ctx: CallContext, q: Query
    ) -> Result[Page[RelationshipView]]:
        """按条件查询档案。**含历史版本**（调用方可按 version 过滤）。"""
        problem = self._check_query(ctx, q)
        if problem is not None:
            return problem
        loaded = self._load_all(ctx, "RelationshipProfile", ctx.project_id)
        if is_err(loaded):
            return loaded
        rows = [r for r in loaded.value if self._match_filters(r, q.filters)]
        rows = self._sort_rows(rows, q.sort)
        page_items, total, has_more = self._paginate(rows, q)
        return Ok(Page(items=[self._relationship_view(r) for r in page_items],
                       total=total, has_more=has_more))

    # ------------------------------------------------------------ 差异

    def diff_versions(
        self, ctx: CallContext, key: str | list[str], v_from: int, v_to: int
    ) -> Result[RelationshipDiff]:
        """两个版本之间的字段级差异（只读；DATA_MODEL：旧版永不删除）。"""
        if isinstance(key, str):
            loaded = self._load_all(ctx, "RelationshipProfile", ctx.project_id)
            if is_err(loaded):
                return loaded
            rows = [r for r in loaded.value if str(r.get("id")) == str(key)]
        else:
            loaded = self._relationships_of(ctx, set(_str_list(key)))
            if is_err(loaded):
                return loaded
            rows = loaded.value
        if not rows:
            return Err(new_error(
                ErrorCode.NOT_FOUND_ENTITY,
                f"找不到关系档案：{key!r}", trace_ref=ctx.trace_ref))
        by_version = {int(r.get("version", 0)): r for r in rows}
        for v in (v_from, v_to):
            if v not in by_version:
                return Err(new_error(
                    ErrorCode.NOT_FOUND_VERSION,
                    f"关系档案没有 v{v}（现有版本：{sorted(by_version)}）",
                    trace_ref=ctx.trace_ref))
        return Ok(RelationshipDiff(
            participant_key=self._participant_key(
                _str_list(by_version[v_to].get("participant_entity_ids"))),
            v_from=v_from, v_to=v_to,
            changes=self._diff_rows(by_version[v_from], by_version[v_to]),
            to_version_change_reason=str(
                by_version[v_to].get("change_reason", ""))))

    # ------------------------------------------------------------ 模式闸门

    def apply_pattern_gate(
        self, ctx: CallContext, item: ProfileItem,
        target_section: str = SECTION_INTERACTION_PATTERNS,
    ) -> Result[ProfileItem]:
        """模式闸门（用户目标 5 / D-012 同精神）：`SINGLE` 不许进
        `interaction_patterns`。

        互动模式是"总是这样"的声明：只出现过一次的互动不能固化长期模式，
        它的归宿是 `shared_experiences`（带时间的具体经历）。
        其余板块不受闸门约束——情绪表达习惯等可用 `uncertainty_note`
        与 `stability` 字段表达不确定，交由下游判断。
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
                f"用户目标 5 / D-012 同精神：{target_section} 只接受 "
                f"REPEATED / PATTERN，收到 {stability.value}——单次互动"
                "请放 shared_experiences，不得固化为长期模式",
                trace_ref=ctx.trace_ref))
        return Ok(item)

    # ------------------------------------------------------------ RL-V6 校验

    def validate_evidence_refs(
        self, ctx: CallContext, entries: list,
        participant_entity_ids: list[str] | None = None,
    ) -> Result[bool]:
        """RL-V6：每条条目都必须**直接引用** `EvidenceItem`。

        只引用 `ProfileItem.item_id`（人格档案字段）的依赖被视为非法——
        关系必须有证据，不能由两份档案"拼"出来（INTERFACES §7.1）。
        `participant_entity_ids` 给出时额外校验版本适用性（D-117 同口径）。
        """
        for index, entry in enumerate(entries):
            if isinstance(entry, dict):
                refs = _str_list(entry.get("evidence_refs"))
                where = f"entries[{index}]"
            else:
                refs = _str_list(getattr(entry, "evidence_refs", None))
                where = f"entries[{index}]"
            if not refs:
                return Err(new_error(
                    ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT,
                    f"RL-V6：{where} 没有 evidence_refs——关系条目必须至少"
                    "引用一条 EvidenceItem", trace_ref=ctx.trace_ref))
            problem = self._require_evidence_refs(
                ctx, refs, _str_list(participant_entity_ids), where=where)
            if problem is not None:
                return problem
        return Ok(True)

    # ------------------------------------------------------------ 内部：写入

    def _write_relationship(
        self, ctx: CallContext, draft: RelationshipDraft, version: int,
        supersede_id: str | None = None,
    ) -> Result[RelationshipRef]:
        """落盘一个新版本；`supersede_id` 非空时把旧版打上归档指针。"""
        payload = self._relationship_payload(draft, version=version,
                                             superseded_by=None)
        # RL-V2 落盘前兜底扫描：payload 里不许出现亲密度类键名
        forbidden = _scan_forbidden_keys(payload)
        if forbidden is not None:
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED,
                f"RL-V2 / D-048：payload 含禁止字段 {forbidden!r}"
                "（不设任何亲密度 / 强度数值指标）", trace_ref=ctx.trace_ref))
        relationship_id = new_id("RelationshipProfile")
        outcome = self.storage.put(
            ctx, "RelationshipProfile", payload, project_id=ctx.project_id,
            namespace=ProvenanceNamespace.SYSTEM_DERIVED, id=relationship_id)
        if is_err(outcome):
            return outcome
        if supersede_id:
            archived = self.storage.update(
                ctx, "RelationshipProfile", supersede_id,
                {"superseded_by": relationship_id})
            if is_err(archived):
                return archived
        self._link_conflicts(ctx, relationship_id,
                             _str_list(draft.participant_entity_ids),
                             int(version), _str_list(draft.conflict_ids))
        self._audit(ctx, "relationship.write_profile",
                    f"{relationship_id} v{version} participants="
                    f"{sorted(_str_list(draft.participant_entity_ids))}")
        result = Ok(RelationshipRef(
            id=relationship_id,
            participant_entity_ids=_str_list(draft.participant_entity_ids),
            version=int(version), status=payload["status"]))
        if supersede_id:
            with_warning(result, WARNING_RELATIONSHIP_SUPERSEDED,
                         f"已生成 v{version}；旧版本已归档"
                         "（superseded_by 指向新版本）")
        if _str_list(draft.conflict_ids):
            with_warning(result, WARNING_CONFLICT_LINKED,
                         f"已关联 {len(_str_list(draft.conflict_ids))} 条冲突记录"
                         "（只记录不裁决，D-013）")
        return result

    def _link_conflicts(self, ctx: CallContext, relationship_id: str,
                        participants: list[str], version: int,
                        conflict_ids: list[str]) -> None:
        """CF-V5 双向一致：档案引用冲突时回写 `downstream_refs`。

        幂等：同一 (relationship_id, version) 不重复追加。只记录关联，
        **不裁决**冲突（D-013）。
        """
        for conflict_id in conflict_ids:
            row = self._get_row(ctx, "ConflictRecord", conflict_id)
            if is_err(row) or row.value is None:
                continue  # 存在性已在 _validate_draft 校验过
            refs = row.value.get("downstream_refs")
            refs = list(refs) if isinstance(refs, list) else []
            entry = {"relationship_id": relationship_id,
                     "participant_entity_ids": sorted(participants),
                     "version": version, "field": "conflict_ids"}
            if any(isinstance(r, dict) and r.get("relationship_id") == relationship_id
                   and r.get("version") == version for r in refs):
                continue
            refs.append(entry)
            updated = self.storage.update(ctx, "ConflictRecord", conflict_id,
                                          {"downstream_refs": refs})
            if is_err(updated):
                continue  # 回写失败不阻断档案写入（冲突记录本身已存在）

    # ------------------------------------------------------------ 内部：校验

    def _validate_draft(self, ctx: CallContext, draft: RelationshipDraft,
                        require_reason: bool) -> Err | None:
        """创建/修订共用的全部校验（RL-V1~V6 + 版本边界 + 引用完整性）。"""
        # —— RL-V2 入口扫描：draft 上不许挂亲密度类字段 ——
        for key in vars(draft):
            low = str(key).lower()
            if any(marker in low for marker in FORBIDDEN_FIELD_MARKERS):
                return Err(new_error(
                    ErrorCode.VALIDATION_GATE_REJECTED,
                    f"RL-V2 / D-048：draft 含禁止字段 {key!r}"
                    "（不设任何亲密度 / 强度数值指标）", trace_ref=ctx.trace_ref))

        # —— 参与者（≥2、存在、不重复）——
        participants = _str_list(draft.participant_entity_ids)
        if len(participants) < 2:
            return Err(new_error(
                ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS,
                f"关系至少需要两个参与者，收到 {len(participants)} 个",
                trace_ref=ctx.trace_ref))
        if len(set(participants)) != len(participants):
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "participant_entity_ids 含重复对象", trace_ref=ctx.trace_ref))
        entity_types: dict[str, EntityType] = {}
        entity_rows: dict[str, dict] = {}
        for participant in participants:
            entity = self._require_entity_row(ctx, participant)
            if is_err(entity):
                return entity
            row = entity.value
            entity_type = _coerce(row.get("entity_type"), EntityType)
            if entity_type is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"参与者 {participant} 的 entity_type 无法识别："
                    f"{row.get('entity_type')!r}", trace_ref=ctx.trace_ref))
            entity_types[participant] = entity_type
            entity_rows[participant] = row

        # —— 反合并（用户目标 7）：同一角色的两个改编版本不得同时作为
        # 参与者出现在一份关系档案里（不同作品的角色不受此限）——
        seen_groups: dict[str, str] = {}
        for participant in participants:
            group = str(entity_rows[participant].get("variant_group_id") or "")
            if group:
                if group in seen_groups:
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        f"PE-V3 / 用户目标 7：{seen_groups[group]} 与 "
                        f"{participant} 是同一角色的不同改编版本，不得作为"
                        "两个参与者出现在一份关系档案里（不自动合并）",
                        trace_ref=ctx.trace_ref))
                seen_groups[group] = participant

        # —— cardinality 与人数一致（RL-V3 的判定基础）——
        cardinality = _coerce(draft.cardinality, Cardinality)
        if cardinality is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"未知关系规模：{draft.cardinality!r}；可选："
                f"{[c.value for c in Cardinality]}", trace_ref=ctx.trace_ref))
        if cardinality == Cardinality.PAIR and len(participants) != 2:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"cardinality=PAIR 要求恰好 2 个参与者，收到 {len(participants)} 个",
                trace_ref=ctx.trace_ref))
        if cardinality == Cardinality.GROUP and len(participants) < 3:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"cardinality=GROUP 要求至少 3 个参与者，收到 {len(participants)} 个",
                trace_ref=ctx.trace_ref))

        # —— RL-V1：perspectives 键集合 == 参与者集合 ——
        perspectives = draft.perspectives or {}
        if set(perspectives) != set(participants):
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"RL-V1：perspectives 的键集合必须等于 participant_entity_ids "
                f"的集合（缺 {sorted(set(participants) - set(perspectives))}，"
                f"多 {sorted(set(perspectives) - set(participants))}）",
                trace_ref=ctx.trace_ref))

        # —— 状态枚举 ——
        status = _coerce(draft.status, ProfileStatus)
        if status is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"未知档案状态：{draft.status!r}；可选："
                f"{[s.value for s in ProfileStatus]}", trace_ref=ctx.trace_ref))

        # —— 模拟隔离：条目命名空间恒 SYSTEM_DERIVED（PF-V6 同口径）——
        for where, item in self._all_items(draft):
            namespace = _coerce(item.namespace, ProvenanceNamespace)
            if namespace is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    f"未知命名空间：{item.namespace!r}", trace_ref=ctx.trace_ref))
            if namespace == ProvenanceNamespace.SIMULATED:
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    f"{where}：模拟内容永远不能进入关系档案"
                    "（模拟对话 / 事件 / 记忆不得成为关系证据）",
                    trace_ref=ctx.trace_ref))
            if namespace != ProvenanceNamespace.SYSTEM_DERIVED:
                return Err(new_error(
                    ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                    f"{where}：条目 namespace 必须为 SYSTEM_DERIVED，"
                    f"收到 {namespace.value}", trace_ref=ctx.trace_ref))

        # —— 视角块（RL-V1 键已查；块内形状 + RL-V4 证据）——
        for owner, block in perspectives.items():
            if str(block.entity_id or "") != str(owner):
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"RL-V1：视角块 {owner} 的 entity_id 与键不一致"
                    f"（{block.entity_id!r}）", trace_ref=ctx.trace_ref))
            others = [p for p in participants if p != owner]
            for target in (block.view_of_targets or {}):
                if target not in others:
                    return Err(new_error(
                        ErrorCode.VALIDATION_DANGLING_REFERENCE,
                        f"视角块 {owner} 的 view_of_targets 指向了非参与者"
                        f"或自身：{target}", trace_ref=ctx.trace_ref))
            for field_name in PERSPECTIVE_ITEM_FIELDS:
                items = (block.view_of_targets or {}) if field_name == "view_of_targets" \
                    else getattr(block, field_name)
                iterable = ([(t, i) for t, lst in (items or {}).items()
                             for i in _normalize_items(lst)]
                            if field_name == "view_of_targets"
                            else [("", i) for i in _normalize_items(items)])
                for target, item in iterable:
                    problem = self._validate_item(
                        ctx, item, participants,
                        where=f"perspectives[{owner}].{field_name}"
                              + (f"[{target}]" if target else ""))
                    if problem is not None:
                        return problem

        # —— 内容板块（RL-V4 证据 + 模式闸门 + 形状）——
        for name in ITEM_SECTIONS:
            gated = name in GATED_SECTIONS
            for item in _normalize_items(getattr(draft, name)):
                problem = self._validate_item(ctx, item, participants,
                                              where=name, gated=gated)
                if problem is not None:
                    return problem
        # 共同经历的逐条目参与者必须是档案参与者的子集（D-122）
        for item in _normalize_items(draft.shared_experiences):
            extra = set(_str_list(item.participant_entity_ids)) - set(participants)
            if extra:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"共同经历「{str(item.text)[:30]}…」的参与者含非档案"
                    f"参与者：{sorted(extra)}", trace_ref=ctx.trace_ref))

        # —— RL-V3：group_dynamics 条件必填 ——
        if cardinality == Cardinality.GROUP:
            group = draft.group_dynamics
            if group is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_GROUP_DYNAMICS_MISSING,
                    "RL-V3：≥3 人的关系档案必须提供 group_dynamics"
                    "（联盟 / 冷落 / 传话；群体关系不拆成两人关系）",
                    trace_ref=ctx.trace_ref))
            filled = any(_normalize_items(getattr(group, section))
                         for section in GROUP_DYNAMICS_SECTIONS)
            if not filled and not str(group.note or "").strip():
                return Err(new_error(
                    ErrorCode.VALIDATION_GROUP_DYNAMICS_MISSING,
                    "RL-V3：group_dynamics 三个板块全空时必须写 note "
                    "说明缺什么（D-020 同口径：不交白卷也不说明）",
                    trace_ref=ctx.trace_ref))
            for section in GROUP_DYNAMICS_SECTIONS:
                for item in _normalize_items(getattr(group, section)):
                    problem = self._validate_item(
                        ctx, item, participants, where=f"group_dynamics.{section}")
                    if problem is not None:
                        return problem
        elif draft.group_dynamics is not None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "RL-V3：两人关系的 group_dynamics 必须为 None"
                "（群体互动只对 ≥3 人生效）", trace_ref=ctx.trace_ref))

        # —— D-020 同口径：不能全空——没有证据支撑就必须说明缺什么 ——
        if not self._has_any_content(draft) and not _str_list(draft.open_questions):
            return Err(new_error(
                ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT,
                "D-020：关系档案没有任何证据支撑的内容时，必须写 "
                "open_questions 说明缺什么——不允许交白卷也不说明",
                trace_ref=ctx.trace_ref))

        # —— 版本适用性（PF-V5 / D-115 同口径）——
        # applicable_variant_ids 声明"这份关系档案适用于哪些版本实例"：
        # 必须覆盖全部虚构参与者、必须是参与者的子集、不得含真人
        # （D-115：真人没有改编版本）。**不同作品的角色天然在不同
        # variant_group，不在此拒绝**——拒绝的只是"同一角色的两个版本
        # 同时当参与者"（上方反合并检查）。
        variants = _str_list(draft.applicable_variant_ids)
        fictionals = [p for p in participants
                      if entity_types[p] == EntityType.FICTIONAL_CHARACTER]
        if variants:
            extra = set(variants) - set(participants)
            if extra:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"applicable_variant_ids 必须是参与者的子集，"
                    f"多出：{sorted(extra)}", trace_ref=ctx.trace_ref))
            for variant_id in variants:
                if prefix_of(variant_id) != "PersonEntity":
                    return Err(new_error(
                        ErrorCode.VALIDATION_DANGLING_REFERENCE,
                        f"applicable_variant_ids 只能引用 PersonEntity，"
                        f"收到 {variant_id!r}", trace_ref=ctx.trace_ref))
                row = self._get_row(ctx, "PersonEntity", variant_id)
                if is_err(row):
                    return row
                if row.value is None:
                    return Err(new_error(
                        ErrorCode.VALIDATION_ENTITY_MISSING,
                        f"适用的改编版本不存在：{variant_id}",
                        trace_ref=ctx.trace_ref))
            for participant in fictionals:
                if participant not in variants:
                    return Err(new_error(
                        ErrorCode.VALIDATION_REQUIRED_FIELD,
                        f"PF-V5：虚构参与者 {participant} 必须出现在 "
                        "applicable_variant_ids（关系档案适用于哪些版本）",
                        trace_ref=ctx.trace_ref))
            reals = [p for p in participants
                     if entity_types[p] == EntityType.REAL_PERSON]
            leaked = set(variants) & set(reals)
            if leaked:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"D-115：真人对象没有改编版本，不得出现在 "
                    f"applicable_variant_ids：{sorted(leaked)}",
                    trace_ref=ctx.trace_ref))
        else:
            if fictionals:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "PF-V5：含虚构参与者的关系档案必须声明 "
                    "applicable_variant_ids（适用于哪些改编版本）",
                    trace_ref=ctx.trace_ref))

        # —— 冲突引用：存在性 + 种类（CF-V5 双向一致由 _link_conflicts 回写）——
        for conflict_id in _str_list(draft.conflict_ids):
            if prefix_of(conflict_id) != "ConflictRecord":
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"conflict_ids 只能引用 ConflictRecord，收到 {conflict_id!r}",
                    trace_ref=ctx.trace_ref))
            row = self._get_row(ctx, "ConflictRecord", conflict_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"conflict_ids 指向的冲突不存在：{conflict_id}",
                    trace_ref=ctx.trace_ref))

        if require_reason and not str(draft.change_reason or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "D-112：revise 必须提供 change_reason（更新依据）",
                trace_ref=ctx.trace_ref))
        return None

    def _validate_item(self, ctx: CallContext, item: ProfileItem,
                       participants: list[str], where: str,
                       gated: bool = False) -> Err | None:
        """条目校验：形状 → 证据引用完整性（RL-V4/RL-V6）→ 模式闸门。"""
        if not str(item.text or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{where} 条目文本不能为空（item_id={item.item_id!r}）",
                trace_ref=ctx.trace_ref))
        if _confidence(item.confidence) is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{where} 条目 confidence 必须是 0~1 的数"
                "（排序权重，不代表真伪）", trace_ref=ctx.trace_ref))
        if not str(item.item_id or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"{where} 条目必须带 item_id（跨版本稳定才能产出可读 diff，"
                "D-114 同口径）", trace_ref=ctx.trace_ref))
        if not _str_list(item.evidence_refs):
            return Err(new_error(
                ErrorCode.VALIDATION_EVIDENCE_REFS_INSUFFICIENT,
                f"RL-V4：{where} 条目「{str(item.text)[:30]}…」必须带 "
                "evidence_refs；没有证据支撑请留空并记入 open_questions",
                trace_ref=ctx.trace_ref))
        problem = self._require_evidence_refs(
            ctx, _str_list(item.evidence_refs), participants, where=where)
        if problem is not None:
            return problem
        if gated:
            gate = self.apply_pattern_gate(ctx, item, SECTION_INTERACTION_PATTERNS)
            if is_err(gate):
                return gate
        return None

    def _require_evidence_refs(self, ctx: CallContext, refs: list[str],
                               participants: list[str], where: str) -> Err | None:
        """RL-V4 + RL-V6 + 隔离红线：引用必须是**存在**的 `EvidenceItem`。

        ⛔ 永不接受 `MemoryItem` / `ConversationTurn` / `PersonaProfile`
        （模拟对话、模拟事件、模拟记忆、人格档案字段都不能当关系证据，
        INTERFACES §7.1 / 用户目标 5、8）。
        ⛔ 证据声明了 `applicable_variant_ids` 时，**每个参与者**都必须
        被覆盖（D-117 同口径：版本专属证据不得串用到其他版本的关系上）。
        """
        for ref in refs:
            if prefix_of(ref) != "EvidenceItem":
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"RL-V6：{where} 的 evidence_refs 只能引用 EvidenceItem，"
                    f"收到 {ref!r}（人格档案 / 模拟记忆 / 模拟对话永远不能"
                    "作为关系证据）", trace_ref=ctx.trace_ref))
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
                    "永远不能变成关系依据", trace_ref=ctx.trace_ref))
            variants = _str_list(row.value.get("applicable_variant_ids"))
            if variants:
                # 版本边界（D-117 同口径）：证据声明的适用版本必须与档案
                # 参与者有交集——空交集说明这条证据讲的是**别的版本**的
                # 事（如 a 的 2020 动画版），拿来给轻小说版的关系当依据
                # 就是版本串用。
                if not set(variants) & set(participants):
                    return Err(new_error(
                        ErrorCode.VALIDATION_DANGLING_REFERENCE,
                        f"D-117：证据 {ref} 只适用于版本 {variants}，与档案"
                        f"参与者 {participants} 无交集——版本专属证据"
                        "不得串用", trace_ref=ctx.trace_ref))
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

    def _relationships_of(self, ctx: CallContext,
                          participant_set: set[str]) -> Result[list[dict]]:
        """某参与者集合的全部档案版本（按集合匹配，顺序无关；不含软删除）。"""
        loaded = self._load_all(ctx, "RelationshipProfile", ctx.project_id)
        if is_err(loaded):
            return loaded
        return Ok([r for r in loaded.value
                   if set(_str_list(r.get("participant_entity_ids")))
                   == set(participant_set)])

    def _require_entity_row(self, ctx: CallContext,
                            entity_id: str) -> Result[dict]:
        row = self._get_row(ctx, "PersonEntity", entity_id)
        if is_err(row):
            return row
        if row.value is None or prefix_of(entity_id) != "PersonEntity":
            return Err(new_error(
                ErrorCode.VALIDATION_ENTITY_MISSING,
                f"参与者不存在：{entity_id}", trace_ref=ctx.trace_ref))
        return Ok(row.value)

    def _audit(self, ctx: CallContext, action: str, detail: str) -> None:
        self.storage.append_audit(ctx, "INFO", action, detail=detail)

    def _check_query(self, ctx: CallContext, q: Query) -> Err | None:
        unknown = set(q.filters) - RELATIONSHIP_FILTER_KEYS
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

    @staticmethod
    def _participant_key(participants: list[str]) -> str:
        """参与者集合的稳定字符串表示（diff 展示用；顺序无关）。"""
        return "|".join(sorted(_str_list(participants)))

    def _relationship_view(self, row: dict) -> RelationshipView:
        return RelationshipView(
            id=str(row.get("id", "")),
            participant_entity_ids=_str_list(row.get("participant_entity_ids")),
            cardinality=str(row.get("cardinality", "")),
            namespace=str(row.get("namespace", "")),
            version=int(row.get("version", 0) or 0),
            perspectives={str(owner): _perspective_from_dict(block)
                          for owner, block in (row.get("perspectives") or {}).items()},
            shared_experiences=[_shared_from_dict(i) for i in
                                _normalize_items(row.get(SECTION_SHARED_EXPERIENCES))],
            interaction_patterns=[_item_from_dict(i) for i in
                                  _normalize_items(row.get(SECTION_INTERACTION_PATTERNS))],
            emotional_expression_habits=[
                _item_from_dict(i) for i in
                _normalize_items(row.get(SECTION_EMOTIONAL_EXPRESSION))],
            temporal_changes=[_item_from_dict(i, timeline=True) for i in
                              _normalize_items(row.get(SECTION_TEMPORAL_CHANGES))],
            group_dynamics=_group_from_dict(row.get("group_dynamics")),
            asymmetry_notes=[_item_from_dict(i) for i in
                             _normalize_items(row.get(SECTION_ASYMMETRY_NOTES))],
            conflict_ids=_str_list(row.get("conflict_ids")),
            applicable_variant_ids=_str_list(row.get("applicable_variant_ids")),
            open_questions=_str_list(row.get("open_questions")),
            coverage=dict(row.get("coverage") or {}),
            status=str(row.get("status", "")),
            superseded_by=row.get("superseded_by"),
            change_reason=str(row.get("change_reason", "")),
            created_at=str(row.get("created_at", "")),
            updated_at=str(row.get("updated_at", "")),
        )

    @staticmethod
    def _all_items(draft: RelationshipDraft) -> list[tuple[str, ProfileItem]]:
        """全部条目（带位置路径），供命名空间与证据校验遍历。"""
        items: list[tuple[str, ProfileItem]] = []
        for name in ITEM_SECTIONS:
            for item in _normalize_items(getattr(draft, name)):
                items.append((name, item))
        for owner, block in (draft.perspectives or {}).items():
            for target, lst in (block.view_of_targets or {}).items():
                for item in _normalize_items(lst):
                    items.append((f"perspectives[{owner}].view_of_targets[{target}]",
                                  item))
            for field_name in ("feeling_toward", "expression_toward"):
                for item in _normalize_items(getattr(block, field_name)):
                    items.append((f"perspectives[{owner}].{field_name}", item))
        if draft.group_dynamics is not None:
            for section in GROUP_DYNAMICS_SECTIONS:
                for item in _normalize_items(
                        getattr(draft.group_dynamics, section)):
                    items.append((f"group_dynamics.{section}", item))
        return items

    @staticmethod
    def _has_any_content(draft: RelationshipDraft) -> bool:
        """是否有任何证据支撑的内容（任一条目非空即算）。"""
        return bool(RelationshipService._all_items(draft))

    @staticmethod
    def _has_any_content_row(row: dict) -> bool:
        """**已存储版本**是否有任何内容（G7：用于拦住"空产出覆盖有内容旧版"）。

        与 `_has_any_content` 同口径（条目板块 + 视角块 + 群体互动），
        只是作用在存储行的原始 dict 上。
        """
        for name in ITEM_SECTIONS:
            if _normalize_items(row.get(name)):
                return True
        perspectives = row.get("perspectives") or {}
        if isinstance(perspectives, dict):
            for block in perspectives.values():
                if not isinstance(block, dict):
                    continue
                targets = block.get("view_of_targets") or {}
                if isinstance(targets, dict):
                    for target_items in targets.values():
                        if _normalize_items(target_items):
                            return True
                for field_name in ("feeling_toward", "expression_toward"):
                    if _normalize_items(block.get(field_name)):
                        return True
        group = row.get("group_dynamics")
        if isinstance(group, dict):
            for section in GROUP_DYNAMICS_SECTIONS:
                if _normalize_items(group.get(section)):
                    return True
        return False

    @staticmethod
    def _relationship_payload(draft: RelationshipDraft, version: int,
                              superseded_by: str | None) -> dict:
        """draft → 存储 payload（内容 + 服务端计算的覆盖度）。"""
        return {
            "participant_entity_ids": _str_list(draft.participant_entity_ids),
            "cardinality": str(getattr(draft.cardinality, "value",
                                       draft.cardinality)),
            "perspectives": {str(owner): _perspective_to_dict(block)
                             for owner, block in (draft.perspectives or {}).items()},
            SECTION_SHARED_EXPERIENCES: [
                _shared_to_dict(i) for i in _normalize_items(draft.shared_experiences)],
            SECTION_INTERACTION_PATTERNS: [
                _item_to_dict(i) for i in _normalize_items(draft.interaction_patterns)],
            SECTION_EMOTIONAL_EXPRESSION: [
                _item_to_dict(i) for i in
                _normalize_items(draft.emotional_expression_habits)],
            SECTION_TEMPORAL_CHANGES: [
                _item_to_dict(i) for i in _normalize_items(draft.temporal_changes)],
            "group_dynamics": _group_to_dict(draft.group_dynamics),
            SECTION_ASYMMETRY_NOTES: [
                _item_to_dict(i) for i in _normalize_items(draft.asymmetry_notes)],
            "conflict_ids": _str_list(draft.conflict_ids),
            "applicable_variant_ids": _str_list(draft.applicable_variant_ids),
            "open_questions": _str_list(draft.open_questions),
            "coverage": RelationshipService._compute_coverage(draft),
            "status": str(getattr(draft.status, "value", draft.status)),
            "version": int(version),
            "superseded_by": superseded_by,
            "change_reason": str(draft.change_reason or "") or
            ("初始版本（create_relationship）" if version == 1 else ""),
        }

    @staticmethod
    def _compute_coverage(draft: RelationshipDraft) -> dict:
        """覆盖度统计（D-113 同口径）：每个板块有多少条目、多少条不同证据支撑。

        **由服务计算，不接受调用方填报**——覆盖度必须反映真实存储内容。
        """
        coverage: dict[str, dict[str, int]] = {}
        for owner, block in (draft.perspectives or {}).items():
            refs: set[str] = set()
            count = 0
            for lst in (block.view_of_targets or {}).values():
                for item in _normalize_items(lst):
                    count += 1
                    refs.update(_str_list(item.evidence_refs))
            for field_name in ("feeling_toward", "expression_toward"):
                for item in _normalize_items(getattr(block, field_name)):
                    count += 1
                    refs.update(_str_list(item.evidence_refs))
            coverage[f"perspectives.{owner}"] = {"items": count,
                                                 "evidence_refs": len(refs)}
        for name in ITEM_SECTIONS:
            refs = set()
            items = _normalize_items(getattr(draft, name))
            for item in items:
                refs.update(_str_list(item.evidence_refs))
            coverage[name] = {"items": len(items), "evidence_refs": len(refs)}
        if draft.group_dynamics is not None:
            refs = set()
            count = 0
            for section in GROUP_DYNAMICS_SECTIONS:
                for item in _normalize_items(
                        getattr(draft.group_dynamics, section)):
                    count += 1
                    refs.update(_str_list(item.evidence_refs))
            coverage["group_dynamics"] = {"items": count,
                                          "evidence_refs": len(refs)}
        return coverage

    @staticmethod
    def _content_of_draft(draft: RelationshipDraft) -> dict:
        """draft 的**内容指纹**（不含版本元数据），用于空转检测（D-112）。"""
        payload = RelationshipService._relationship_payload(
            draft, version=1, superseded_by=None)
        return {k: payload[k] for k in CONTENT_KEYS}

    @staticmethod
    def _content_of_row(row: dict) -> dict:
        """已存储版本的内容指纹（与 `_content_of_draft` 同形状，可直接 ==）。"""
        return {k: row.get(k) for k in CONTENT_KEYS}

    # ------------------------------------------------------------ 内部：diff

    @staticmethod
    def _diff_rows(old: dict, new: dict) -> list[RelationshipChange]:
        """字段级差异：集合字段、状态、视角块（按 owner+target+item_id）、
        条目板块（按 item_id）、群体互动。"""
        changes: list[RelationshipChange] = []

        def _add(field_name: str, change_type: str, before: str = "",
                 after: str = "", item_id: str = "", entity_id: str = "") -> None:
            changes.append(RelationshipChange(
                field=field_name, change_type=change_type, item_id=item_id,
                entity_id=entity_id, before=before, after=after))

        # 集合字段
        for name in ("participant_entity_ids", "conflict_ids",
                     "applicable_variant_ids", "open_questions"):
            before_set = set(_str_list(old.get(name)))
            after_set = set(_str_list(new.get(name)))
            for value in sorted(before_set - after_set):
                _add(name, "removed", value)
            for value in sorted(after_set - before_set):
                _add(name, "added", after=value)

        if str(old.get("cardinality", "")) != str(new.get("cardinality", "")):
            _add("cardinality", "changed", str(old.get("cardinality", "")),
                 str(new.get("cardinality", "")))
        if str(old.get("status", "")) != str(new.get("status", "")):
            _add("status", "changed", str(old.get("status", "")),
                 str(new.get("status", "")))

        # 视角块：按 owner → target/item 对齐
        old_persp = {str(k): _perspective_from_dict(v)
                     for k, v in (old.get("perspectives") or {}).items()}
        new_persp = {str(k): _perspective_from_dict(v)
                     for k, v in (new.get("perspectives") or {}).items()}
        for owner in sorted(set(old_persp) | set(new_persp)):
            before_block = old_persp.get(owner)
            after_block = new_persp.get(owner)
            field_name = f"perspectives.{owner}"
            if before_block is None:
                _add(field_name, "added", after=str(after_block.entity_id))
                continue
            if after_block is None:
                _add(field_name, "removed", before=str(before_block.entity_id))
                continue
            # view_of_targets：按 target → item_id
            before_targets = before_block.view_of_targets
            after_targets = after_block.view_of_targets
            for target in sorted(set(before_targets) | set(after_targets)):
                b_items = {i.item_id: i for i in before_targets.get(target, [])}
                a_items = {i.item_id: i for i in after_targets.get(target, [])}
                for item_id in sorted(set(b_items) - set(a_items)):
                    _add(field_name, "removed", b_items[item_id].text,
                         item_id=item_id, entity_id=owner)
                for item_id in sorted(set(a_items) - set(b_items)):
                    _add(field_name, "added", after=a_items[item_id].text,
                         item_id=item_id, entity_id=owner)
                for item_id in sorted(set(b_items) & set(a_items)):
                    if (b_items[item_id].text != a_items[item_id].text
                            or sorted(b_items[item_id].evidence_refs)
                            != sorted(a_items[item_id].evidence_refs)
                            or b_items[item_id].uncertainty_note
                            != a_items[item_id].uncertainty_note):
                        _add(field_name, "changed", b_items[item_id].text,
                             a_items[item_id].text, item_id=item_id,
                             entity_id=owner)
            for sub in ("feeling_toward", "expression_toward"):
                b_items = {i.item_id: i for i in getattr(before_block, sub)}
                a_items = {i.item_id: i for i in getattr(after_block, sub)}
                for item_id in sorted(set(b_items) - set(a_items)):
                    _add(f"{field_name}.{sub}", "removed", b_items[item_id].text,
                         item_id=item_id, entity_id=owner)
                for item_id in sorted(set(a_items) - set(b_items)):
                    _add(f"{field_name}.{sub}", "added",
                         after=a_items[item_id].text, item_id=item_id,
                         entity_id=owner)
                for item_id in sorted(set(b_items) & set(a_items)):
                    if (b_items[item_id].text != a_items[item_id].text
                            or sorted(b_items[item_id].evidence_refs)
                            != sorted(a_items[item_id].evidence_refs)):
                        _add(f"{field_name}.{sub}", "changed",
                             b_items[item_id].text, a_items[item_id].text,
                             item_id=item_id, entity_id=owner)
            if before_block.uncertainty_note != after_block.uncertainty_note:
                _add(field_name, "changed", before_block.uncertainty_note,
                     after_block.uncertainty_note, entity_id=owner)

        # 条目板块：按 item_id 对齐
        for name in ITEM_SECTIONS:
            timeline = name in (SECTION_SHARED_EXPERIENCES, SECTION_TEMPORAL_CHANGES)
            shared = name == SECTION_SHARED_EXPERIENCES
            b_items = {i.item_id: i for i in
                       (_shared_from_dict(d) if shared else _item_from_dict(d, timeline)
                        for d in _normalize_items(old.get(name)))}
            a_items = {i.item_id: i for i in
                       (_shared_from_dict(d) if shared else _item_from_dict(d, timeline)
                        for d in _normalize_items(new.get(name)))}
            for item_id in sorted(set(b_items) - set(a_items)):
                _add(name, "removed", b_items[item_id].text, item_id=item_id)
            for item_id in sorted(set(a_items) - set(b_items)):
                _add(name, "added", after=a_items[item_id].text, item_id=item_id)
            for item_id in sorted(set(b_items) & set(a_items)):
                b, a = b_items[item_id], a_items[item_id]
                same = (b.text == a.text
                        and sorted(b.evidence_refs) == sorted(a.evidence_refs)
                        and b.uncertainty_note == a.uncertainty_note
                        and str(b.stability) == str(a.stability)
                        and b.confidence == a.confidence)
                if shared and same and isinstance(b, SharedExperienceItem) \
                        and isinstance(a, SharedExperienceItem):
                    same = (sorted(b.participant_entity_ids)
                            == sorted(a.participant_entity_ids))
                if timeline and same and not shared:
                    same = b.occurred_at == a.occurred_at
                if not same:
                    _add(name, "changed", b.text, a.text, item_id=item_id)

        # 群体互动
        before_group = _group_from_dict(old.get("group_dynamics"))
        after_group = _group_from_dict(new.get("group_dynamics"))
        if before_group != after_group:
            _add("group_dynamics", "changed", str(before_group),
                 str(after_group))

        changes.sort(key=lambda c: (c.field, c.change_type, c.item_id,
                                    c.entity_id, c.before, c.after))
        return changes
