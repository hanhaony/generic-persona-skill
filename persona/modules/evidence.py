"""Evidence —— 来源与证据管理基础实现（docs/INTERFACES.md §4、DATA_MODEL §4.3~§4.6）。

**P2 证据管线第一步**（用户称之为 P3.1；仓库 ROADMAP 的编号是 P2，
差异已记录待拍板，见 DECISIONS §3 Q-37）：**本地管理基础**——
登记、查询、持久化、校验、追溯、去重、冲突保留。

**职责边界（INTERFACES §4.1）**：
    ✅ 来源登记与去重、来源事件、证据原子化与定级、去重归并、冲突记录、稳定性判定
    ⛔ **不自行启动研究循环**（F-2）：本文件没有 search / collect / query_plan，
       不调用 `Provider.search`；检索是 `Research` 的事（尚未实现）。
    ⛔ 不裁决冲突；不写记忆或档案。

**三条评分互不等价**（DATA_MODEL §4.5，D-010）：
    `tier`（取舍排序）/ `reliability_snapshot`（可信度参考）/ `extraction_confidence`（技术质量）
    —— **没有任何一个等于"这句话是真的"**。

**隔离红线**（EV-V1 / D-023）：`EvidenceItem` 永远不接受 `namespace=SIMULATED`，
模块层在任何业务校验之前拒绝，存储层还有一道结构闸门（D-064 双层）。

**诚实边界（本阶段不做的，详见 docs/RUNNING.md §5.4）**：
    - `extract_events` 只走确定性降级：创建"覆盖全文的单块事件" + `EVENT_SINGLE_BLOCK` 警告；
      AI 辅助切分要调 `Provider.chat`，属于后续阶段。
    - `detect_conflicts` 只做**确定性结构检测**（同说法跨正典状态 / 跨互斥改编版本）；
      "两句话意思矛盾"这种语义检测需要模型，未实现、不假装。
    - 去重条件刻意严格：归一化只折空白、不压缩语义；**正典状态与改编版本不同的一律不并组**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.context import CallContext
from ..core.enums import (
    CONFLICT_RESOLUTION_POLICY,
    CanonLevel,
    DivergenceKind,
    EvidenceDimension,
    EvidenceStatus,
    EvidenceTier,
    FetchStatus,
    ProvenanceNamespace,
    Reliability,
    ReviewStatus,
    SourceEventType,
    SourceKind,
    SourceRole,
    Stability,
)
from ..core.errors import ErrorCode, new_error
from ..core.ids import new_id
from ..core.result import Err, Ok, Result, is_err, unwrap, with_warning
from ..core.temporal import FuzzyDate, TimePrecision, to_iso, utc_now
from ..core.types import MAX_QUERY_LIMIT, Page, Query
from ..storage.sqlite import SqliteStorage

# --------------------------------------------------------------------------
# 警告码（业务提示，不是错误；ERROR_HANDLING §2.3）
# --------------------------------------------------------------------------

WARNING_SOURCE_DEDUP_HIT = "SOURCE_DEDUP_HIT"               # 来源重复命中去重
WARNING_EVENT_SINGLE_BLOCK = "EVENT_SINGLE_BLOCK"           # 来源不可切分，退回单块事件
WARNING_CONFLICT_RECORDED = "CONFLICT_RECORDED"             # 冲突已记录（未裁决）
WARNING_CONFLICT_MEMBER_DEDUPED = "CONFLICT_MEMBER_DEDUPED"  # 冲突成员去重

# 证据级别优先级（DATA_MODEL §4.5）：**只排原始来源证据三类**。
# INFERENCE 是系统推断标记，**不参与排序**——它不是"第四等的真话"（D-009/D-010）。
TIER_PRIORITY: dict[EvidenceTier, int] = {
    EvidenceTier.VERBATIM: 3,
    EvidenceTier.PROFILE_BACKGROUND: 2,
    EvidenceTier.THIRD_PARTY: 1,
}

# extract_events 支持的切分粒度（INTERFACES §4.2）
GRANULARITIES: frozenset[str] = frozenset({"document", "section", "timestamp"})

# 允许排序的列（与 storage SORT_KEYS 一致；payload 字段排序未实现）
SORTABLE_COLUMNS: frozenset[str] = frozenset({"created_at", "updated_at", "id"})


def _normalize_claim(text: str) -> str:
    """去重/冲突判定时的说法归一化：**只折空白，不压缩语义、不改大小写**。

    （中文没有大小写问题；把"的/地"或标点差异算作同一说法属于语义判断，不做。）
    """
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------
# 端口协议（P1.2-B 定义，未改动）
# --------------------------------------------------------------------------


class EvidencePort(Protocol):
    """证据模块的**写入 + 查询**接口。只有本模块能写证据表。

    签名与 `docs/INTERFACES.md §4.2` 对齐。P1.2-B 骨架里的旧签名
    （`judge_stability(entity_id, dimension) -> Result[Stability]` 等）
    与文档不一致，本阶段按**文档为准**更正（D-110）；Protocol 不做运行时
    强制，此次更正不影响任何既有调用方。
    """

    def register_source(self, ctx: CallContext, draft: SourceDraft) -> Result[SourceRef]:
        """登记一份来源（含去重：同 canonical_key / content_hash 只登记一次）。"""
        ...

    def extract_events(self, ctx: CallContext, source_id: str,
                       min_granularity: str = "document") -> Result[list[SourceEventRef]]:
        """把一个来源切成可定位的原始事件（一次采访 / 一集剧情 / 一次设定发布）。"""
        ...

    def add_evidence(self, ctx: CallContext, draft: EvidenceDraft) -> Result[EvidenceRef]:
        """新增一条证据（强制分级 + 出处定位 + 命名空间校验）。"""
        ...

    def record_inference(self, ctx: CallContext,
                         draft: InferenceDraft) -> Result[EvidenceRef]:
        """记录系统推断。`derived_from` **不可为空**（D-014：禁止无根推断）。"""
        ...

    def detect_duplicates(self, ctx: CallContext,
                          scope: dict | None = None) -> Result[list[DedupeGroupRef]]:
        """归并同一说法的多个来源：保留全部来源引用，只合并"说法本身"（D-015）。"""
        ...

    def detect_conflicts(self, ctx: CallContext,
                         scope: dict | None = None) -> Result[list[ConflictRef]]:
        """检测互斥说法并记 `ConflictRecord`。

        冲突**不是错误**（D-055）：返回 `Ok([...])`，流程继续。
        """
        ...

    def query_evidence(self, ctx: CallContext, q: Query) -> Result[Page[EvidenceView]]:
        """按条件查询证据。"""
        ...


class EvidenceReader(Protocol):
    """**只读**端口。Persona / Relationship / Dialogue 只能拿到这个——协议里根本没有写方法。"""

    def query_evidence(self, ctx: CallContext, q: Query) -> Result[Page[dict]]:
        ...

    def get_evidence_tier(self, ctx: CallContext, evidence_id: str) -> Result[EvidenceTier]:
        ...


# --------------------------------------------------------------------------
# 数据契约（Draft 进 / View 出；跨模块只传 ID 与这些形状，不传数据库行）
# --------------------------------------------------------------------------


@dataclass
class SourceDraft:
    """登记一份来源的请求（INTERFACES §4.2 `SourceDraft` + DATA_MODEL §4.3 必填项）。"""

    title: str
    canonical_key: str = ""
    content_hash: str = ""
    kind: SourceKind | str = SourceKind.OTHER
    url: str | None = None
    publisher: str | None = None
    author: str | None = None
    published_at: FuzzyDate | None = None
    language: str = "und"                      # BCP-47；"und" = 未声明
    reliability: Reliability | str = Reliability.UNKNOWN
    source_family_id: str = ""                 # 空 = 自成一族（族 ID = 自身 ID）
    role_in_family: SourceRole | str = SourceRole.ORIGINAL
    canonical_family_source_id: str | None = None
    cited_from_source_ids: list[str] = field(default_factory=list)
    subject_entity_ids: list[str] = field(default_factory=list)
    content_ref: str | None = None             # 正文定位（快照路径 / 正文 ID）
    access_note: str | None = None
    license_access: str | None = None
    snapshot_ref: str | None = None
    fetch_status: FetchStatus | str = FetchStatus.OK
    retrieved_at: str = ""                     # ISO-8601；空 = 现在
    source_note: str = ""                      # SC-V3：非 ORIGINAL 且找不到原始来源时的说明


@dataclass
class SourceRef:
    """登记/读取来源后返回给调用方的最小形状（INTERFACES §4.2）。"""

    id: str
    title: str
    kind: str
    reliability: str


@dataclass
class SourceView:
    """来源的完整读视图（含追溯字段）。"""

    id: str
    title: str
    kind: str
    namespace: str
    canonical_key: str = ""
    content_hash: str = ""
    url: str | None = None
    publisher: str | None = None
    author: str | None = None
    published_at: FuzzyDate | None = None
    language: str = "und"
    reliability: str = "UNKNOWN"
    source_family_id: str = ""
    role_in_family: str = "ORIGINAL"
    canonical_family_source_id: str | None = None
    cited_from_source_ids: list[str] = field(default_factory=list)
    subject_entity_ids: list[str] = field(default_factory=list)
    fetch_status: str = "OK"
    retrieved_at: str = ""
    created_at: str = ""


@dataclass
class SourceEventDraft:
    """登记一个来源事件的请求（DATA_MODEL §4.4）。精确切分由调用方给出，
    AI 辅助切分尚未实现（见模块 docstring 的诚实边界）。"""

    source_id: str
    title: str
    event_type: SourceEventType | str = SourceEventType.OTHER
    locator: dict = field(default_factory=dict)  # {timestamp, page, paragraph, chapter, scene_id}
    sequence_no: int | None = None
    real_world_date: FuzzyDate | None = None
    work_internal_time: str | None = None
    participant_entity_ids: list[str] = field(default_factory=list)
    canon_level: CanonLevel | None = None
    applicable_variant_ids: list[str] = field(default_factory=list)
    summary: str | None = None
    duplicate_of_event_id: str | None = None
    parent_event_id: str | None = None


@dataclass
class SourceEventRef:
    """来源事件的最小形状（INTERFACES §4.2 `SourceEventRef`）。"""

    id: str
    title: str
    event_type: str
    locator: dict = field(default_factory=dict)
    real_world_date: FuzzyDate | None = None
    canon_level: str | None = None


@dataclass
class EvidenceDraft:
    """新增一条证据的请求（INTERFACES §4.2 `EvidenceDraft`）。

    `namespace` 默认 `PUBLIC_SOURCE`；`SYSTEM_DERIVED` 只能经 `record_inference`，
    `USER_PROVIDED` 须经 `UserInputRecord`（尚未实现），`SIMULATED` 永久拒绝（EV-V1）。
    """

    claim: str
    tier: EvidenceTier | str
    dimension: EvidenceDimension | str
    subject_entity_ids: list[str]
    extraction_confidence: float
    source_id: str | None = None
    source_event_ids: list[str] = field(default_factory=list)
    locator: dict | None = None
    verbatim_quote: str | None = None
    stated_at: FuzzyDate | None = None
    stability: Stability | str = Stability.UNKNOWN
    canon_level: CanonLevel | None = None
    applicable_variant_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    namespace: ProvenanceNamespace | str = ProvenanceNamespace.PUBLIC_SOURCE
    status: EvidenceStatus | str = EvidenceStatus.ACTIVE
    reliability_snapshot: Reliability | str | None = None  # 空 = 从来源复制


@dataclass
class EvidenceRef:
    """新增证据后返回的最小形状（INTERFACES §4.2 `EvidenceRef`）。"""

    id: str
    claim: str
    tier: str
    source_id: str | None
    stability: str
    status: str


@dataclass
class EvidenceView:
    """证据的完整读视图。`namespace` / `source_id` / `source_event_ids` /
    `conflict_ids` **必然出现**（INTERFACES §4.2：下游无法忽略归属信息）。"""

    id: str
    claim: str
    tier: str
    dimension: str
    namespace: str
    source_id: str | None
    source_event_ids: list[str]
    subject_entity_ids: list[str]
    stability: str
    status: str
    conflict_ids: list[str]
    dedupe_group_id: str | None = None
    verbatim_quote: str | None = None
    stated_at: FuzzyDate | None = None
    extraction_confidence: float = 0.0
    reliability_snapshot: str = "UNKNOWN"
    canon_level: str | None = None
    applicable_variant_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass
class InferenceDraft:
    """记录一条系统推断的请求（INTERFACES §4.2 `InferenceDraft`）。

    `derived_from_evidence_ids` **至少 1 个**（EV-V3：禁止无根推断）。
    `applicable_variant_ids` 是对 INTERFACES 草案的扩展：虚构角色的推断
    同样要声明适用的改编版本（对齐 EV-V7 的精神）。
    """

    claim: str
    derived_from_evidence_ids: list[str]
    dimension: EvidenceDimension | str
    subject_entity_ids: list[str]
    confidence: float
    reasoning_note: str = ""
    applicable_variant_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class ConflictDraft:
    """登记一个冲突的请求。**不提供任何"谁对"的字段**——结构上就无法裁决。"""

    topic: str
    subject_entity_ids: list[str]
    member_evidence_ids: list[str]
    divergence_kind: DivergenceKind | str
    uncertainty_note: str = ""
    position_summaries: dict[str, str] = field(default_factory=dict)  # evidence_id -> 摘要
    review_status: ReviewStatus | str = ReviewStatus.UNREVIEWED


@dataclass
class ConflictRef:
    """冲突的最小形状（INTERFACES §4.2 `ConflictRef`）。"""

    id: str
    topic: str
    divergence_kind: str
    member_count: int
    review_status: str


@dataclass
class DedupeGroupRef:
    """去重组（INTERFACES §4.2 `DedupeGroupRef`）。

    组 ID = **最早成员**的 evidence_id（"规范成员指针"，见 D-105）：
    不新建前缀、不建新表、完全确定性，重复检测结果幂等。
    """

    id: str
    canonical_claim: str
    member_evidence_ids: list[str]
    member_source_ids: list[str]
    merge_method: str


@dataclass
class StabilityVerdict:
    """稳定性判定（INTERFACES §4.2 `StabilityVerdict`）。只读，不改证据。"""

    stability: Stability
    occurrences: int
    distinct_sources: int
    time_span: tuple[str, str] | None
    reason: str


# --------------------------------------------------------------------------
# 序列化辅助
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


# 来源种类 → 单块事件类型的确定性映射（extract_events 降级路径用）
_EVENT_TYPE_BY_SOURCE_KIND: dict[str, SourceEventType] = {
    "INTERVIEW": SourceEventType.INTERVIEW,
    "OFFICIAL_MATERIAL": SourceEventType.OFFICIAL_RELEASE,
    "SELF_PUBLISHED": SourceEventType.PUBLIC_STATEMENT,
    "SOCIAL_POST": SourceEventType.PUBLIC_STATEMENT,
    "NEWS_REPORT": SourceEventType.DOCUMENT_SECTION,
    "BIOGRAPHY": SourceEventType.DOCUMENT_SECTION,
    "FAN_WIKI": SourceEventType.DOCUMENT_SECTION,
    "COMMENTARY": SourceEventType.DOCUMENT_SECTION,
    "WORK_CONTENT": SourceEventType.DOCUMENT_SECTION,
    "OTHER": SourceEventType.OTHER,
}


# --------------------------------------------------------------------------
# 服务实现
# --------------------------------------------------------------------------


class EvidenceService:
    """来源与证据的本地管理基础。**只依赖 `Storage`**，不碰 Provider（F-2/F-8 方向）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self.storage = storage

    # ------------------------------------------------------------ 来源：登记

    def register_source(self, ctx: CallContext, draft: SourceDraft) -> Result[SourceRef]:
        """登记一份来源。命中去重（同 `canonical_key` 或同 `content_hash`）时
        **返回已存在的 id** + `warnings=[SOURCE_DEDUP_HIT]`，不新建、不删除、
        不合并——转载各自保留记录（DATA_MODEL §4.3）。
        """
        # —— 形状校验 ——
        if not str(draft.title or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "来源标题不能为空", trace_ref=ctx.trace_ref))
        kind = _coerce(draft.kind, SourceKind)
        if kind is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知的来源种类：{draft.kind!r}", trace_ref=ctx.trace_ref))
        reliability = _coerce(draft.reliability, Reliability) or Reliability.UNKNOWN
        role = _coerce(draft.role_in_family, SourceRole)
        if role is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知的来源族内角色：{draft.role_in_family!r}",
                                 trace_ref=ctx.trace_ref))
        fetch_status = _coerce(draft.fetch_status, FetchStatus) or FetchStatus.OK

        canonical_key = str(draft.canonical_key or "").strip()
        content_hash = str(draft.content_hash or "").strip()
        if not canonical_key and not content_hash:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "SC-V1：canonical_key 与 content_hash 至少一个非空",
                                 trace_ref=ctx.trace_ref))

        # —— 转载族谱自洽（SC-V2 / SC-V3）——
        canonical_id = (draft.canonical_family_source_id or "").strip() or None
        if role == SourceRole.ORIGINAL and canonical_id:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "SC-V2：ORIGINAL 来源不得再指向别的原始来源",
                                 trace_ref=ctx.trace_ref))
        if role != SourceRole.ORIGINAL and not canonical_id \
                and not str(draft.source_note or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "SC-V3：非 ORIGINAL 来源未指明原始来源时，必须写 source_note 说明",
                                 trace_ref=ctx.trace_ref))

        # —— 引用存在性 ——
        for entity_id in _str_list(draft.subject_entity_ids):
            problem = self._require_entity(ctx, entity_id)
            if problem is not None:
                return problem
        original_row = None
        if canonical_id:
            original_row = self._get_row(ctx, "Source", canonical_id)
            if is_err(original_row):
                return original_row
            if original_row.value is None:
                return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                     f"canonical_family_source_id 指向的来源不存在：{canonical_id}",
                                     trace_ref=ctx.trace_ref))
        for cited in _str_list(draft.cited_from_source_ids):
            row = self._get_row(ctx, "Source", cited)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                     f"cited_from_source_ids 指向的来源不存在：{cited}",
                                     trace_ref=ctx.trace_ref))

        # —— 去重：同键命中直接返回原记录（AC-4）——
        dedupe = self._find_source_by_key(ctx, canonical_key, content_hash)
        if is_err(dedupe):
            return dedupe
        if dedupe.value is not None:
            existing = dedupe.value
            return with_warning(
                Ok(SourceRef(id=str(existing["id"]), title=str(existing.get("title", "")),
                             kind=str(existing.get("kind", "")),
                             reliability=str(existing.get("reliability", "UNKNOWN")))),
                WARNING_SOURCE_DEDUP_HIT,
                f"来源已登记过（{existing['_dedupe_by']}），返回原记录",
            )

        # —— 族 ID：未给则自成一族；非 ORIGINAL 可继承原始来源的族 ——
        source_id = new_id("Source")
        family_id = str(draft.source_family_id or "").strip()
        if not family_id and original_row is not None and original_row.value is not None:
            family_id = str(original_row.value.get("source_family_id", ""))
        if not family_id:
            family_id = source_id

        # —— SC-V4：引用环防护（新 ID 尚不存在，理论上不可能成环，仍做显式检查）——
        cycle = self._citation_cycle(ctx, _str_list(draft.cited_from_source_ids), source_id)
        if is_err(cycle):
            return cycle
        if cycle.value:
            return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                 f"SC-V4：cited_from_source_ids 形成引用环：{cycle.value}",
                                 trace_ref=ctx.trace_ref))

        payload = {
            "title": str(draft.title),
            "canonical_key": canonical_key,
            "content_hash": content_hash,
            "kind": str(kind),
            "url": draft.url,
            "publisher": draft.publisher,
            "author": draft.author,
            "published_at": _fuzzy_to_dict(draft.published_at),
            "language": str(draft.language or "und"),
            "reliability": str(reliability),
            "license_access": draft.license_access,
            "subject_entity_ids": _str_list(draft.subject_entity_ids),
            "source_family_id": family_id,
            "role_in_family": str(role),
            "canonical_family_source_id": canonical_id,
            "cited_from_source_ids": _str_list(draft.cited_from_source_ids),
            "content_ref": draft.content_ref,
            "access_note": draft.access_note,
            "source_note": str(draft.source_note or ""),
            "snapshot_ref": draft.snapshot_ref,
            "fetch_status": str(fetch_status),
            "retrieved_at": draft.retrieved_at or to_iso(utc_now()),
        }
        outcome = self.storage.put(ctx, "Source", payload, project_id=ctx.project_id,
                                   namespace=ProvenanceNamespace.PUBLIC_SOURCE, id=source_id)
        if is_err(outcome):
            return outcome
        self._audit(ctx, "evidence.register_source", source_id)
        return Ok(SourceRef(id=source_id, title=str(draft.title), kind=str(kind),
                            reliability=str(reliability)))

    def get_source(self, ctx: CallContext, source_id: str) -> Result[SourceView]:
        row = self._get_row(ctx, "Source", source_id)
        if is_err(row):
            return row
        if row.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                 f"找不到来源 {source_id}", trace_ref=ctx.trace_ref))
        return Ok(self._source_view(row.value))

    def query_sources(self, ctx: CallContext, q: Query) -> Result[Page[SourceView]]:
        problem = self._check_query(ctx, q, {"kind", "reliability", "role_in_family",
                                             "source_family_id", "canonical_key",
                                             "namespace"})
        if problem is not None:
            return problem
        loaded = self._load_all(ctx, "Source", ctx.project_id)
        if is_err(loaded):
            return loaded
        # 结构列 kind（值恒为 "Source"）会遮蔽 payload 里的业务 kind，先还原再过滤
        rows = [{**r, "kind": self._source_kind(r)} for r in loaded.value]
        rows = [r for r in rows if self._match_filters(r, q.filters)]
        rows = self._sort_rows(rows, q.sort)
        page_items, total, has_more = self._paginate(rows, q)
        return Ok(Page(items=[self._source_view(r) for r in page_items],
                       total=total, has_more=has_more))

    # ------------------------------------------------------------ 来源：事件

    def add_source_event(self, ctx: CallContext,
                         draft: SourceEventDraft) -> Result[SourceEventRef]:
        """显式登记一个来源事件（精确切分的入口）。"""
        source = self._get_row(ctx, "Source", draft.source_id)
        if is_err(source):
            return source
        if source.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                 f"找不到来源 {draft.source_id}", trace_ref=ctx.trace_ref))
        if not str(draft.title or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "事件标题不能为空", trace_ref=ctx.trace_ref))
        event_type = _coerce(draft.event_type, SourceEventType)
        if event_type is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知的事件类型：{draft.event_type!r}",
                                 trace_ref=ctx.trace_ref))
        # SE-V2：定位信息至少一项非空
        if not any(str(v or "").strip() for v in (draft.locator or {}).values()):
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "SE-V2：locator 至少一个子字段非空"
                                 "（timestamp/page/paragraph/chapter/scene_id）",
                                 trace_ref=ctx.trace_ref))
        # SE-V3：作品类来源的事件必须声明正典状态
        canon_level = _coerce(draft.canon_level, CanonLevel) if draft.canon_level else None
        if draft.canon_level and canon_level is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知的正典状态：{draft.canon_level!r}",
                                 trace_ref=ctx.trace_ref))
        if self._source_kind(source.value) in ("WORK_CONTENT", "OFFICIAL_MATERIAL") \
                and canon_level is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "SE-V3：作品类来源的事件必须声明 canon_level"
                                 "（无法判断时用 UNKNOWN）", trace_ref=ctx.trace_ref))
        # SE-V4 / EV-V7：适用版本必须存在，且不得跨 variant_group 混指
        problem = self._check_variants(ctx, _str_list(draft.applicable_variant_ids))
        if problem is not None:
            return problem
        for entity_id in _str_list(draft.participant_entity_ids):
            problem = self._require_entity(ctx, entity_id, role="事件参与者")
            if problem is not None:
                return problem
        # SE-V1：duplicate_of 必须存在且不成环
        if draft.duplicate_of_event_id:
            target = self._get_row(ctx, "SourceEvent", draft.duplicate_of_event_id)
            if is_err(target):
                return target
            if target.value is None:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"duplicate_of_event_id 指向的事件不存在："
                                     f"{draft.duplicate_of_event_id}", trace_ref=ctx.trace_ref))
            if self._event_chain_too_deep(ctx, draft.duplicate_of_event_id):
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     "SE-V1：duplicate_of_event_id 链过长或成环",
                                     trace_ref=ctx.trace_ref))
        if draft.parent_event_id:
            parent = self._get_row(ctx, "SourceEvent", draft.parent_event_id)
            if is_err(parent):
                return parent
            if parent.value is None:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"parent_event_id 指向的事件不存在：{draft.parent_event_id}",
                                     trace_ref=ctx.trace_ref))

        event_id = new_id("SourceEvent")
        payload = {
            "source_id": draft.source_id,
            "title": str(draft.title),
            "event_type": str(event_type),
            "locator": dict(draft.locator or {}),
            "sequence_no": draft.sequence_no,
            "real_world_date": _fuzzy_to_dict(draft.real_world_date),
            "work_internal_time": draft.work_internal_time,
            "participant_entity_ids": _str_list(draft.participant_entity_ids),
            "canon_level": str(canon_level) if canon_level else None,
            "applicable_variant_ids": _str_list(draft.applicable_variant_ids),
            "summary": draft.summary,
            "duplicate_of_event_id": draft.duplicate_of_event_id,
            "parent_event_id": draft.parent_event_id,
        }
        outcome = self.storage.put(ctx, "SourceEvent", payload,
                                   project_id=ctx.project_id,
                                   namespace=ProvenanceNamespace.PUBLIC_SOURCE, id=event_id)
        if is_err(outcome):
            return outcome
        self._audit(ctx, "evidence.add_source_event", event_id)
        return Ok(SourceEventRef(id=event_id, title=str(draft.title),
                                 event_type=str(event_type), locator=dict(draft.locator or {}),
                                 real_world_date=draft.real_world_date,
                                 canon_level=str(canon_level) if canon_level else None))

    def get_source_events(self, ctx: CallContext,
                          source_id: str) -> Result[list[SourceEventRef]]:
        source = self._get_row(ctx, "Source", source_id)
        if is_err(source):
            return source
        if source.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                 f"找不到来源 {source_id}", trace_ref=ctx.trace_ref))
        loaded = self._load_all(ctx, "SourceEvent", ctx.project_id)
        if is_err(loaded):
            return loaded
        rows = [r for r in loaded.value if str(r.get("source_id")) == source_id]
        return Ok([self._event_ref(r) for r in rows])

    def extract_events(self, ctx: CallContext, source_id: str,
                       min_granularity: str = "document") -> Result[list[SourceEventRef]]:
        """把一个来源切成可定位事件。

        **本阶段只走确定性降级路径**：不调用 `Provider.chat`（INTERFACES §4.2 中
        chat 是"仅当使用"的可选项）。来源不可切分时创建**单个覆盖全文的事件**并附
        `warnings=[EVENT_SINGLE_BLOCK]`；已有事件则原样返回（幂等，不重复建）。
        """
        if min_granularity not in GRANULARITIES:
            return Err(new_error(ErrorCode.VALIDATION_GRANULARITY_UNSUPPORTED,
                                 f"不支持的切分粒度：{min_granularity!r}；"
                                 f"可选：{sorted(GRANULARITIES)}", trace_ref=ctx.trace_ref))
        source = self._get_row(ctx, "Source", source_id)
        if is_err(source):
            return source
        if source.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                 f"找不到来源 {source_id}", trace_ref=ctx.trace_ref))

        existing = self.get_source_events(ctx, source_id)
        if is_err(existing):
            return existing
        if existing.value:
            return Ok(existing.value)  # 幂等：重复调用不重复建事件

        kind = self._source_kind(source.value)
        event_type = _EVENT_TYPE_BY_SOURCE_KIND.get(kind, SourceEventType.DOCUMENT_SECTION)
        canon_level = CanonLevel.UNKNOWN if kind in ("WORK_CONTENT", "OFFICIAL_MATERIAL") else None
        draft = SourceEventDraft(
            source_id=source_id,
            title=f"{source.value.get('title', '')}（全文块）",
            event_type=event_type,
            locator={"chapter": "全文"},  # 单块事件的定位语义：整篇文档
            participant_entity_ids=_str_list(source.value.get("subject_entity_ids")),
            canon_level=canon_level,
        )
        created = self.add_source_event(ctx, draft)
        if is_err(created):
            return created
        return with_warning(Ok([unwrap(created)]), WARNING_EVENT_SINGLE_BLOCK,
                            "来源未做细粒度切分（AI 辅助切分尚未实现），"
                            "已创建覆盖全文的单块事件")

    # ------------------------------------------------------------ 证据：登记

    def add_evidence(self, ctx: CallContext, draft: EvidenceDraft) -> Result[EvidenceRef]:
        """新增一条证据。校验顺序：**命名空间闸门 → 业务校验**（INTERFACES §4.2）。"""
        # ① 命名空间闸门（在任何业务校验之前；EV-V1 硬闸门）
        namespace = _coerce(draft.namespace, ProvenanceNamespace)
        if namespace is None:
            return Err(new_error(ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                                 f"未知命名空间：{draft.namespace!r}", trace_ref=ctx.trace_ref))
        if namespace == ProvenanceNamespace.SIMULATED:
            return Err(new_error(ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                                 "EV-V1：证据禁止 SIMULATED——模拟内容永远不能变成真实证据",
                                 trace_ref=ctx.trace_ref))
        tier = _coerce(draft.tier, EvidenceTier)
        if tier is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知证据级别：{draft.tier!r}；可选："
                                 f"{[t.value for t in EvidenceTier]}", trace_ref=ctx.trace_ref))
        if tier == EvidenceTier.INFERENCE:
            return Err(new_error(ErrorCode.VALIDATION_NO_DERIVATION,
                                 "tier=INFERENCE 必须经 record_inference 并提供 derived_from；"
                                 "add_evidence 不接受推断", trace_ref=ctx.trace_ref))
        if namespace == ProvenanceNamespace.SYSTEM_DERIVED:
            return Err(new_error(ErrorCode.VALIDATION_TIER_NAMESPACE_MISMATCH,
                                 "非推断证据不得标记为 SYSTEM_DERIVED（EV-V5）",
                                 trace_ref=ctx.trace_ref))
        if namespace == ProvenanceNamespace.USER_PROVIDED:
            return Err(new_error(ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                                 "USER_PROVIDED 证据须经 UserInputRecord 登记"
                                 "（该记录尚未实现，见 RUNNING §5.4 E-2）",
                                 trace_ref=ctx.trace_ref))
        if namespace != ProvenanceNamespace.PUBLIC_SOURCE:
            return Err(new_error(ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                                 f"add_evidence 只接受 PUBLIC_SOURCE，收到 {namespace}",
                                 trace_ref=ctx.trace_ref))

        # ② 业务校验
        if not str(draft.claim or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "claim 不能为空（一条证据只说一件事）",
                                 trace_ref=ctx.trace_ref))
        dimension = _coerce(draft.dimension, EvidenceDimension)
        if dimension is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知证据维度：{draft.dimension!r}", trace_ref=ctx.trace_ref))
        subjects = _str_list(draft.subject_entity_ids)
        if not subjects:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "subject_entity_ids 不能为空（证据必须说明说的是谁）",
                                 trace_ref=ctx.trace_ref))
        for entity_id in subjects:
            problem = self._require_entity(ctx, entity_id, role="证据主体")
            if problem is not None:
                return problem
        if tier == EvidenceTier.VERBATIM and not str(draft.verbatim_quote or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_TIER_QUOTE_MISSING,
                                 "EV-V2：VERBATIM 证据必须带 verbatim_quote 原文",
                                 trace_ref=ctx.trace_ref))
        source_id = (draft.source_id or "").strip() or None
        if source_id is None:
            return Err(new_error(ErrorCode.VALIDATION_SOURCE_MISSING,
                                 "EV-V4：非 INFERENCE 证据必须带 source_id",
                                 trace_ref=ctx.trace_ref))
        source = self._get_row(ctx, "Source", source_id)
        if is_err(source):
            return source
        if source.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_SOURCE,
                                 f"证据引用的来源不存在：{source_id}", trace_ref=ctx.trace_ref))
        for event_id in _str_list(draft.source_event_ids):
            event = self._get_row(ctx, "SourceEvent", event_id)
            if is_err(event):
                return event
            if event.value is None:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"source_event_ids 指向的事件不存在：{event_id}",
                                     trace_ref=ctx.trace_ref))
            if str(event.value.get("source_id")) != source_id:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"事件 {event_id} 属于来源 {event.value.get('source_id')}，"
                                     f"与证据的 source_id={source_id} 不一致",
                                     trace_ref=ctx.trace_ref))
        confidence = _confidence(draft.extraction_confidence)
        if confidence is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "extraction_confidence 必须是 0~1 的数"
                                 "（它是抽取准确度，不是真实性）", trace_ref=ctx.trace_ref))
        problem = self._check_variants(ctx, _str_list(draft.applicable_variant_ids))
        if problem is not None:
            return problem
        stability = _coerce(draft.stability, Stability)
        if stability is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知稳定性：{draft.stability!r}", trace_ref=ctx.trace_ref))
        status = _coerce(draft.status, EvidenceStatus)
        if status is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知证据状态：{draft.status!r}", trace_ref=ctx.trace_ref))
        canon_level = _coerce(draft.canon_level, CanonLevel) if draft.canon_level else None
        if draft.canon_level and canon_level is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知正典状态：{draft.canon_level!r}", trace_ref=ctx.trace_ref))
        # reliability_snapshot：冗余自来源，便于查询（DATA_MODEL §4.5）
        reliability = _coerce(draft.reliability_snapshot, Reliability) \
            if draft.reliability_snapshot else None
        if reliability is None:
            reliability = _coerce(source.value.get("reliability"), Reliability) \
                or Reliability.UNKNOWN

        evidence_id = new_id("EvidenceItem")
        payload = {
            "claim": str(draft.claim),
            "tier": str(tier),
            "verbatim_quote": draft.verbatim_quote,
            "dimension": str(dimension),
            "subject_entity_ids": subjects,
            "source_id": source_id,
            "source_event_ids": _str_list(draft.source_event_ids),
            "locator": dict(draft.locator) if draft.locator else None,
            "stated_at": _fuzzy_to_dict(draft.stated_at),
            "stability": str(stability),
            "extraction_confidence": confidence,
            "reliability_snapshot": str(reliability),
            "canon_level": str(canon_level) if canon_level else None,
            "applicable_variant_ids": _str_list(draft.applicable_variant_ids),
            "derived_from_evidence_ids": [],
            "dedupe_group_id": None,
            "conflict_ids": [],
            "status": str(status),
            "tags": _str_list(draft.tags),
        }
        outcome = self.storage.put(ctx, "EvidenceItem", payload,
                                   project_id=ctx.project_id, namespace=namespace,
                                   id=evidence_id)
        if is_err(outcome):
            return outcome
        self._audit(ctx, "evidence.add_evidence", evidence_id)
        return Ok(EvidenceRef(id=evidence_id, claim=str(draft.claim), tier=str(tier),
                              source_id=source_id, stability=str(stability),
                              status=str(status)))

    def record_inference(self, ctx: CallContext,
                         draft: InferenceDraft) -> Result[EvidenceRef]:
        """记录一条系统推断：`tier=INFERENCE`、`namespace=SYSTEM_DERIVED`、
        `source_id=None`。**禁止无根推断**（EV-V3 / D-014）。"""
        derived = _str_list(draft.derived_from_evidence_ids)
        if not derived:
            return Err(new_error(ErrorCode.VALIDATION_NO_DERIVATION,
                                 "EV-V3：推断必须列出 derived_from_evidence_ids（禁止无根推断）",
                                 trace_ref=ctx.trace_ref))
        for evidence_id in derived:
            row = self._get_row(ctx, "EvidenceItem", evidence_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"derived_from 指向的证据不存在：{evidence_id}",
                                     trace_ref=ctx.trace_ref))
        if not str(draft.claim or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "推断的 claim 不能为空", trace_ref=ctx.trace_ref))
        dimension = _coerce(draft.dimension, EvidenceDimension)
        if dimension is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知证据维度：{draft.dimension!r}", trace_ref=ctx.trace_ref))
        subjects = _str_list(draft.subject_entity_ids)
        if not subjects:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "推断的 subject_entity_ids 不能为空",
                                 trace_ref=ctx.trace_ref))
        for entity_id in subjects:
            problem = self._require_entity(ctx, entity_id, role="推断主体")
            if problem is not None:
                return problem
        confidence = _confidence(draft.confidence)
        if confidence is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "confidence 必须是 0~1 的数", trace_ref=ctx.trace_ref))
        problem = self._check_variants(ctx, _str_list(draft.applicable_variant_ids))
        if problem is not None:
            return problem

        evidence_id = new_id("EvidenceItem")
        payload = {
            "claim": str(draft.claim),
            "tier": str(EvidenceTier.INFERENCE),
            "verbatim_quote": None,
            "dimension": str(dimension),
            "subject_entity_ids": subjects,
            "source_id": None,               # 推断永不得伪装成来源中的直接陈述
            "source_event_ids": [],
            "locator": None,
            "stated_at": None,
            "stability": str(Stability.UNKNOWN),
            "extraction_confidence": confidence,
            "reliability_snapshot": str(Reliability.UNKNOWN),
            "canon_level": None,
            "applicable_variant_ids": _str_list(draft.applicable_variant_ids),
            "derived_from_evidence_ids": derived,
            "dedupe_group_id": None,
            "conflict_ids": [],
            "status": str(EvidenceStatus.ACTIVE),
            "tags": _str_list(draft.tags),
            "reasoning_note": str(draft.reasoning_note or ""),
        }
        outcome = self.storage.put(ctx, "EvidenceItem", payload,
                                   project_id=ctx.project_id,
                                   namespace=ProvenanceNamespace.SYSTEM_DERIVED,
                                   id=evidence_id)
        if is_err(outcome):
            return outcome
        self._audit(ctx, "evidence.record_inference", evidence_id)
        return Ok(EvidenceRef(id=evidence_id, claim=str(draft.claim),
                              tier=str(EvidenceTier.INFERENCE), source_id=None,
                              stability=str(Stability.UNKNOWN),
                              status=str(EvidenceStatus.ACTIVE)))

    # ------------------------------------------------------------ 证据：读取

    def get_evidence(self, ctx: CallContext, evidence_id: str) -> Result[EvidenceView]:
        row = self._get_row(ctx, "EvidenceItem", evidence_id)
        if is_err(row):
            return row
        if row.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY,
                                 f"找不到证据 {evidence_id}", trace_ref=ctx.trace_ref))
        return Ok(self._evidence_view(row.value))

    def get_evidence_tier(self, ctx: CallContext,
                          evidence_id: str) -> Result[EvidenceTier]:
        """只读端口用的最小查询：只要证据级别（INTERFACES §8.1 `EvidenceReader`）。"""
        view = self.get_evidence(ctx, evidence_id)
        if is_err(view):
            return view
        tier = _coerce(view.value.tier, EvidenceTier)
        if tier is None:  # 数据损坏：库里存了枚举表外的值
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"证据 {evidence_id} 的 tier 无法识别：{view.value.tier!r}",
                                 trace_ref=ctx.trace_ref))
        return Ok(tier)

    def query_evidence(self, ctx: CallContext, q: Query) -> Result[Page[EvidenceView]]:
        """只读查询（INTERFACES §4.2）。返回必然包含 `namespace` / `source_id` /
        `source_event_ids` / `conflict_ids`——下游无法忽略归属信息。"""
        problem = self._check_query(ctx, q, {"entity_ids", "tier", "dimension", "stability",
                                             "status", "conflict_ids", "canon_level",
                                             "applicable_variant_ids", "namespace",
                                             "source_id"})
        if problem is not None:
            return problem
        loaded = self._load_all(ctx, "EvidenceItem", ctx.project_id)
        if is_err(loaded):
            return loaded
        rows = [r for r in loaded.value if self._match_evidence_filters(r, q.filters)]
        rows = self._sort_rows(rows, q.sort)
        page_items, total, has_more = self._paginate(rows, q)
        return Ok(Page(items=[self._evidence_view(r) for r in page_items],
                       total=total, has_more=has_more))

    def judge_stability(self, ctx: CallContext, subject_entity_id: str, claim_text: str,
                        evidence_ids: list[str]) -> Result[StabilityVerdict]:
        """稳定性闸门（D-012）。**只读**：不改证据、不调外部服务。

        确定性规则（D-109）：1 条 ⇒ SINGLE；≥2 条 ⇒ REPEATED；
        ≥3 个**不同来源** ⇒ PATTERN。语义级稳定性判定需要模型，未实现。
        """
        ids = _str_list(evidence_ids)
        if not ids:
            return Err(new_error(ErrorCode.VALIDATION_EMPTY_EVIDENCE,
                                 "稳定性判定至少需要一条证据", trace_ref=ctx.trace_ref))
        sources: set[str] = set()
        starts: list[str] = []
        for evidence_id in ids:
            row = self._get_row(ctx, "EvidenceItem", evidence_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"证据不存在：{evidence_id}", trace_ref=ctx.trace_ref))
            if row.value.get("source_id"):
                sources.add(str(row.value["source_id"]))
            stated = _fuzzy_from_dict(row.value.get("stated_at"))
            if stated and stated.start:
                starts.append(str(stated.start))
        occurrences = len(ids)
        distinct_sources = len(sources)
        time_span = (min(starts), max(starts)) if len(set(starts)) >= 2 else None
        if occurrences <= 1:
            stability = Stability.SINGLE
        elif distinct_sources >= 3:
            stability = Stability.PATTERN
        else:
            stability = Stability.REPEATED
        reason = (f"对象 {subject_entity_id} 的说法「{str(claim_text or '')[:40]}」："
                  f"{occurrences} 条证据、{distinct_sources} 个不同来源"
                  + (f"、时间跨度 {time_span[0]}~{time_span[1]}" if time_span else "")
                  + "（确定性规则：1=SINGLE，≥2=REPEATED，≥3 来源=PATTERN）")
        return Ok(StabilityVerdict(stability=stability, occurrences=occurrences,
                                   distinct_sources=distinct_sources,
                                   time_span=time_span, reason=reason))

    # ------------------------------------------------------------ 去重

    def detect_duplicates(self, ctx: CallContext,
                          scope: dict | None = None) -> Result[list[DedupeGroupRef]]:
        """检测重复说法并归并。**只归并说法本身，保留全部来源引用**（D-015）。

        去重条件（D-104，刻意严格）：归一化 claim（仅折空白）+ 相同 subject 集合
        + 相同 dimension + 相同 applicable_variant_ids + **相同 canon_level**。
        正典状态不同、适用版本不同 ⇒ **不并组**（后者还会进冲突检测）。
        """
        project_id, entity_ids = self._scope(ctx, scope)
        loaded = self._load_all(ctx, "EvidenceItem", project_id)
        if is_err(loaded):
            return loaded
        rows = loaded.value
        if entity_ids:
            rows = [r for r in rows
                    if set(_str_list(r.get("subject_entity_ids"))) & set(entity_ids)]

        groups: dict[tuple, list[dict]] = {}
        for row in rows:
            key = (
                _normalize_claim(str(row.get("claim", ""))),
                tuple(sorted(_str_list(row.get("subject_entity_ids")))),
                str(row.get("dimension", "")),
                tuple(sorted(_str_list(row.get("applicable_variant_ids")))),
                str(row.get("canon_level") or ""),
            )
            groups.setdefault(key, []).append(row)

        refs: list[DedupeGroupRef] = []
        for members in groups.values():
            if len(members) < 2:
                continue
            canonical = members[0]  # _load_all 已按 (created_at, id) 排序
            group_id = str(canonical["id"])
            for member in members:
                if str(member.get("dedupe_group_id") or "") != group_id:
                    updated = self.storage.update(
                        ctx, "EvidenceItem", str(member["id"]),
                        {"dedupe_group_id": group_id})
                    if is_err(updated):
                        return updated
            member_source_ids: list[str] = []
            for member in members:
                sid = member.get("source_id")
                if sid and str(sid) not in member_source_ids:
                    member_source_ids.append(str(sid))
            refs.append(DedupeGroupRef(
                id=group_id,
                canonical_claim=str(canonical.get("claim", "")),
                member_evidence_ids=[str(m["id"]) for m in members],
                member_source_ids=member_source_ids,
                merge_method="exact_claim_whitespace_normalized",
            ))
        refs.sort(key=lambda r: r.id)
        if refs:
            self._audit(ctx, "evidence.detect_duplicates", f"groups={len(refs)}")
        return Ok(refs)

    # ------------------------------------------------------------ 冲突

    def record_conflict(self, ctx: CallContext, draft: ConflictDraft) -> Result[ConflictRef]:
        """登记一个冲突。**永久保留每一种说法，不做裁决**（D-013 / D-055）。

        `resolution_policy` 恒为 `KEEP_ALL_UNASSESSED`；`review_status` 只有
        `UNREVIEWED` / `ACKNOWLEDGED`——枚举里没有 `RESOLVED`。
        """
        # 成员去重（保留顺序）；重复成员只警告，不失败（CF-V1）
        seen: set[str] = set()
        member_ids: list[str] = []
        deduped = False
        for evidence_id in _str_list(draft.member_evidence_ids):
            if evidence_id in seen:
                deduped = True
                continue
            seen.add(evidence_id)
            member_ids.append(evidence_id)
        if len(member_ids) < 2:
            return Err(new_error(ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS,
                                 "CF-V1：冲突至少需要 2 个不同的证据成员",
                                 trace_ref=ctx.trace_ref))
        rows = []
        for evidence_id in member_ids:
            row = self._get_row(ctx, "EvidenceItem", evidence_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(ErrorCode.VALIDATION_DANGLING_REFERENCE,
                                     f"冲突成员证据不存在：{evidence_id}",
                                     trace_ref=ctx.trace_ref))
            rows.append(row.value)
        if not str(draft.topic or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "冲突主题不能为空", trace_ref=ctx.trace_ref))
        subjects = _str_list(draft.subject_entity_ids)
        if not subjects:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "冲突必须说明涉及哪些对象", trace_ref=ctx.trace_ref))
        for entity_id in subjects:
            problem = self._require_entity(ctx, entity_id, role="冲突涉及对象")
            if problem is not None:
                return problem
        divergence = _coerce(draft.divergence_kind, DivergenceKind)
        if divergence is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知的冲突类型：{draft.divergence_kind!r}",
                                 trace_ref=ctx.trace_ref))
        review = _coerce(draft.review_status, ReviewStatus)
        if review is None:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 f"未知的审阅状态：{draft.review_status!r}"
                                 "（可选 UNREVIEWED / ACKNOWLEDGED）",
                                 trace_ref=ctx.trace_ref))

        members = [
            {
                "evidence_id": str(row["id"]),
                "position_summary": str(draft.position_summaries.get(str(row["id"]),
                                                                     row.get("claim", ""))),
                "source_id": row.get("source_id"),
                "tier": str(row.get("tier", "")),
                "stated_at": row.get("stated_at"),
                "canon_level": row.get("canon_level"),
                "applicable_variant_ids": _str_list(row.get("applicable_variant_ids")),
            }
            for row in rows
        ]
        conflict_id = new_id("ConflictRecord")
        payload = {
            "topic": str(draft.topic),
            "subject_entity_ids": subjects,
            "members": members,
            "divergence_kind": str(divergence),
            "resolution_policy": CONFLICT_RESOLUTION_POLICY,
            "uncertainty_note": str(draft.uncertainty_note or ""),
            "review_status": str(review),
            "downstream_refs": [],
        }
        outcome = self.storage.put(ctx, "ConflictRecord", payload,
                                   project_id=ctx.project_id,
                                   namespace=ProvenanceNamespace.SYSTEM_DERIVED,
                                   id=conflict_id)
        if is_err(outcome):
            return outcome

        # 回写证据：conflict_ids + 状态标 DISPUTED（不覆盖 WITHDRAWN/SUPERSEDED）
        for row in rows:
            conflict_ids = _str_list(row.get("conflict_ids"))
            patch: dict = {}
            if conflict_id not in conflict_ids:
                patch["conflict_ids"] = [*conflict_ids, conflict_id]
            if str(row.get("status")) == str(EvidenceStatus.ACTIVE):
                patch["status"] = str(EvidenceStatus.DISPUTED)
            if patch:
                updated = self.storage.update(ctx, "EvidenceItem", str(row["id"]), patch)
                if is_err(updated):
                    return updated
        self._audit(ctx, "evidence.record_conflict", conflict_id)
        result = Ok(ConflictRef(id=conflict_id, topic=str(draft.topic),
                                divergence_kind=str(divergence),
                                member_count=len(members), review_status=str(review)))
        if deduped:
            with_warning(result, WARNING_CONFLICT_MEMBER_DEDUPED,
                         "冲突成员列表有重复，已去重（CF-V1）")
        return with_warning(result, WARNING_CONFLICT_RECORDED,
                            "冲突已记录（未裁决，双方说法都保留）")

    def detect_conflicts(self, ctx: CallContext,
                         scope: dict | None = None) -> Result[list[ConflictRef]]:
        """返回作用域内的冲突，并做**确定性结构检测**（D-106）：

        1. 同一说法（归一化 claim + 相同 subject）在不同来源的 **canon_level 分歧**
           （CANON vs NON_CANON/AMBIGUOUS）⇒ VERSION_DIFFERENCE；
        2. 同一说法适用于**互斥的改编版本集合** ⇒ VERSION_DIFFERENCE。

        "两句话意思矛盾"这类**语义检测需要模型，未实现**；检测到的冲突只记录、不裁决。
        已记录过的成员集合不会重复建冲突（幂等）。
        """
        project_id, entity_ids = self._scope(ctx, scope)
        loaded = self._load_all(ctx, "EvidenceItem", project_id)
        if is_err(loaded):
            return loaded
        rows = loaded.value
        if entity_ids:
            rows = [r for r in rows
                    if set(_str_list(r.get("subject_entity_ids"))) & set(entity_ids)]

        refs: list[ConflictRef] = []
        recorded_member_sets: list[frozenset[str]] = []

        # ① 已有冲突
        conflicts = self._load_all(ctx, "ConflictRecord", project_id)
        if is_err(conflicts):
            return conflicts
        for row in conflicts.value:
            members = row.get("members") or []
            member_ids = [str(m.get("evidence_id")) for m in members
                          if isinstance(m, dict)]
            recorded_member_sets.append(frozenset(member_ids))
            refs.append(ConflictRef(id=str(row["id"]), topic=str(row.get("topic", "")),
                                    divergence_kind=str(row.get("divergence_kind", "")),
                                    member_count=len(member_ids),
                                    review_status=str(row.get("review_status", ""))))

        # ② 结构检测
        by_claim: dict[tuple, list[dict]] = {}
        for row in rows:
            key = (_normalize_claim(str(row.get("claim", ""))),
                   tuple(sorted(_str_list(row.get("subject_entity_ids")))))
            by_claim.setdefault(key, []).append(row)

        def _already_recorded(member_ids: list[str]) -> bool:
            target = frozenset(member_ids)
            return any(target == existing or target <= existing
                       for existing in recorded_member_sets)

        for members in by_claim.values():
            if len(members) < 2:
                continue
            canon_levels = {str(m.get("canon_level") or "") for m in members}
            has_canon = "CANON" in canon_levels
            has_non_canon = bool(canon_levels & {"NON_CANON", "AMBIGUOUS"})
            if has_canon and has_non_canon:
                member_ids = [str(m["id"]) for m in members]
                if not _already_recorded(member_ids):
                    created = self.record_conflict(ctx, ConflictDraft(
                        topic=f"正典状态分歧：{_normalize_claim(str(members[0].get('claim', '')))[:60]}",
                        subject_entity_ids=_str_list(members[0].get("subject_entity_ids")),
                        member_evidence_ids=member_ids,
                        divergence_kind=DivergenceKind.VERSION_DIFFERENCE,
                        uncertainty_note="结构化检测：同一说法在不同来源的正典状态不一致"
                                         "（CANON vs NON_CANON/AMBIGUOUS）；未裁决",
                    ))
                    if is_err(created):
                        return created
                    refs.append(unwrap(created))
                    recorded_member_sets.append(frozenset(member_ids))
                continue

            # 互斥改编版本：至少两组都非空且两两不交
            variant_sets = [frozenset(_str_list(m.get("applicable_variant_ids")))
                            for m in members]
            non_empty = [s for s in variant_sets if s]
            if len(non_empty) >= 2 and all(a.isdisjoint(b)
                                           for i, a in enumerate(non_empty)
                                           for b in non_empty[i + 1:]):
                member_ids = [str(m["id"]) for m in members]
                if not _already_recorded(member_ids):
                    created = self.record_conflict(ctx, ConflictDraft(
                        topic=f"版本差异：{_normalize_claim(str(members[0].get('claim', '')))[:60]}",
                        subject_entity_ids=_str_list(members[0].get("subject_entity_ids")),
                        member_evidence_ids=member_ids,
                        divergence_kind=DivergenceKind.VERSION_DIFFERENCE,
                        uncertainty_note="结构化检测：同一说法适用于互斥的改编版本；未裁决",
                    ))
                    if is_err(created):
                        return created
                    refs.append(unwrap(created))
                    recorded_member_sets.append(frozenset(member_ids))

        refs.sort(key=lambda r: r.id)
        return Ok(refs)

    # ------------------------------------------------------------ 内部：存储辅助

    def _get_row(self, ctx: CallContext, kind: str,
                 entity_id: str) -> Result[dict | None]:
        """读一行（扁平化：实体字段在顶层）。

        `Ok(None)` = 不存在；`Err` = 读取失败——**绝不把读失败伪装成不存在**。
        """
        return self.storage.get(ctx, kind, entity_id)

    def _load_all(self, ctx: CallContext, kind: str, project_id: str) -> Result[list[dict]]:
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
            items.extend(unwrap(page).items)
            if not unwrap(page).has_more or not unwrap(page).items:
                break
            offset += len(unwrap(page).items)
        items.sort(key=lambda r: (str(r.get("created_at", "")), str(r.get("id", ""))))
        return Ok(items)

    def _require_entity(self, ctx: CallContext, entity_id: str,
                        role: str = "对象") -> Err | None:
        row = self._get_row(ctx, "PersonEntity", entity_id)
        if is_err(row):
            return row
        if row.value is None or not str(row.value.get("id", "")).startswith("pe_"):
            return Err(new_error(ErrorCode.VALIDATION_ENTITY_MISSING,
                                 f"{role}不存在：{entity_id}", trace_ref=ctx.trace_ref))
        return None

    def _find_source_by_key(self, ctx: CallContext, canonical_key: str,
                            content_hash: str) -> Result[dict | None]:
        loaded = self._load_all(ctx, "Source", ctx.project_id)
        if is_err(loaded):
            return loaded
        for row in loaded.value:
            if canonical_key and str(row.get("canonical_key", "")) == canonical_key:
                return Ok({**row, "_dedupe_by": "canonical_key"})
            if content_hash and str(row.get("content_hash", "")) == content_hash:
                return Ok({**row, "_dedupe_by": "content_hash"})
        return Ok(None)

    def _citation_cycle(self, ctx: CallContext, cited: list[str],
                        new_source_id: str) -> Result[list[str] | None]:
        """SC-V4：沿 cited_from 链走一遍，若回到新来源 ID 即成环。"""
        for start in cited:
            chain = [start]
            current = start
            for _ in range(64):
                if current == new_source_id:
                    return Ok(chain)
                row = self._get_row(ctx, "Source", current)
                if is_err(row):
                    return row
                if row.value is None:
                    break
                nexts = _str_list(row.value.get("cited_from_source_ids"))
                if not nexts:
                    break
                current = nexts[0]
                chain.append(current)
        return Ok(None)

    def _event_chain_too_deep(self, ctx: CallContext, event_id: str,
                              limit: int = 64) -> bool:
        current = event_id
        for _ in range(limit):
            row = self._get_row(ctx, "SourceEvent", current)
            if is_err(row) or row.value is None:
                return False
            nxt = row.value.get("duplicate_of_event_id")
            if not nxt:
                return False
            current = str(nxt)
        return True

    def _check_variants(self, ctx: CallContext, variant_ids: list[str]) -> Err | None:
        """EV-V7 / SE-V4：适用版本必须存在，且不得跨 variant_group 混指。"""
        if not variant_ids:
            return None
        groups: set[str] = set()
        for variant_id in variant_ids:
            row = self._get_row(ctx, "PersonEntity", variant_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(ErrorCode.VALIDATION_ENTITY_MISSING,
                                     f"适用的改编版本不存在：{variant_id}",
                                     trace_ref=ctx.trace_ref))
            group = str(row.value.get("variant_group_id") or "")
            if group:
                groups.add(group)
        if len(groups) > 1:
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED,
                                 "EV-V7：applicable_variant_ids 跨 variant_group 混指"
                                 "（改编版本不得无条件合并）", trace_ref=ctx.trace_ref))
        return None

    def _audit(self, ctx: CallContext, action: str, detail: str) -> None:
        self.storage.append_audit(ctx, "INFO", action, detail=detail)

    def _scope(self, ctx: CallContext, scope: dict | None) -> tuple[str, list[str]]:
        scope = scope or {}
        project_id = str(scope.get("project_id") or ctx.project_id)
        entity_ids = _str_list(scope.get("entity_ids"))
        return project_id, entity_ids

    def _check_query(self, ctx: CallContext, q: Query,
                     allowed: set[str]) -> Err | None:
        unknown = set(q.filters) - allowed
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
    def _match_evidence_filters(row: dict, filters: dict) -> bool:
        for key, expected in filters.items():
            # EvidenceQuery 的过滤键 entity_ids 对应载荷字段 subject_entity_ids
            # （INTERFACES §4.2）；conflict_ids / applicable_variant_ids 同名。
            actual = row.get("subject_entity_ids" if key == "entity_ids" else key)
            if key in ("entity_ids", "conflict_ids", "applicable_variant_ids"):
                wanted = {str(e) for e in _str_list(expected)}
                if not wanted & set(_str_list(actual)):
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

    # ------------------------------------------------------------ 内部：视图转换

    @staticmethod
    def _source_kind(row: dict) -> str:
        """来源的**业务**种类。

        `Storage._row_to_dict` 的结构列 `kind`（值恒为记录种类 "Source"）会遮蔽
        payload 里的业务 `kind`（如 "INTERVIEW"）——两个字段在 DATA_MODEL 里撞名。
        读来源种类必须显式回退到 payload，否则 SE-V3 / extract_events 全部失效。
        """
        payload = row.get("payload")
        if isinstance(payload, dict) and "kind" in payload:
            return str(payload["kind"])
        return str(row.get("kind", ""))

    @staticmethod
    def _source_view(row: dict) -> SourceView:
        return SourceView(
            id=str(row.get("id", "")),
            title=str(row.get("title", "")),
            kind=EvidenceService._source_kind(row),
            namespace=str(row.get("namespace", "")),
            canonical_key=str(row.get("canonical_key", "")),
            content_hash=str(row.get("content_hash", "")),
            url=row.get("url"),
            publisher=row.get("publisher"),
            author=row.get("author"),
            published_at=_fuzzy_from_dict(row.get("published_at")),
            language=str(row.get("language", "und")),
            reliability=str(row.get("reliability", "UNKNOWN")),
            source_family_id=str(row.get("source_family_id", "")),
            role_in_family=str(row.get("role_in_family", "ORIGINAL")),
            canonical_family_source_id=row.get("canonical_family_source_id"),
            cited_from_source_ids=_str_list(row.get("cited_from_source_ids")),
            subject_entity_ids=_str_list(row.get("subject_entity_ids")),
            fetch_status=str(row.get("fetch_status", "OK")),
            retrieved_at=str(row.get("retrieved_at", "")),
            created_at=str(row.get("created_at", "")),
        )

    @staticmethod
    def _event_ref(row: dict) -> SourceEventRef:
        return SourceEventRef(
            id=str(row.get("id", "")),
            title=str(row.get("title", "")),
            event_type=str(row.get("event_type", "")),
            locator=dict(row.get("locator") or {}),
            real_world_date=_fuzzy_from_dict(row.get("real_world_date")),
            canon_level=row.get("canon_level"),
        )

    @staticmethod
    def _evidence_view(row: dict) -> EvidenceView:
        return EvidenceView(
            id=str(row.get("id", "")),
            claim=str(row.get("claim", "")),
            tier=str(row.get("tier", "")),
            dimension=str(row.get("dimension", "")),
            namespace=str(row.get("namespace", "")),
            source_id=row.get("source_id"),
            source_event_ids=_str_list(row.get("source_event_ids")),
            subject_entity_ids=_str_list(row.get("subject_entity_ids")),
            stability=str(row.get("stability", "")),
            status=str(row.get("status", "")),
            conflict_ids=_str_list(row.get("conflict_ids")),
            dedupe_group_id=row.get("dedupe_group_id"),
            verbatim_quote=row.get("verbatim_quote"),
            stated_at=_fuzzy_from_dict(row.get("stated_at")),
            extraction_confidence=float(row.get("extraction_confidence") or 0.0),
            reliability_snapshot=str(row.get("reliability_snapshot", "UNKNOWN")),
            canon_level=row.get("canon_level"),
            applicable_variant_ids=_str_list(row.get("applicable_variant_ids")),
            tags=_str_list(row.get("tags")),
            created_at=str(row.get("created_at", "")),
        )
