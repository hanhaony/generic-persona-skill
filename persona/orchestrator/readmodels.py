"""只读查询层（P5.1）：把"档案 → 条目 → 证据 → 来源"拼成 CLI 能直接打印的形状。

**为什么单独一层**（D-059 / F-7）：CLI 只能 import `orchestrator`，不得直接驱动
`modules`。而 `show` / `evidence` 这类命令恰恰需要把三个模块的读接口串起来；
写在 `cli/` 里会逼 CLI 直接调 `PersonaService`，正是 G4 那类"脚本代劳掩盖缺陷"。

**三条边界（每条都对应一个已踩过的坑）**：

1. **只读**：只调用 `get_*` / `query_*`，**绝不** `put` / `create_*` / `init_schema`。
   数据库文件不存在时直接报错——`sqlite3.connect()` 会顺手建一个空文件，
   只读命令"看一眼"就留下一个 0 字节库，比报错更糟。
2. **不猜来源数**：按条目 `evidence_refs` 逐条查证据、再对 `source_id` 去重后现算。
   **不能用** `PersonaProfile.evidence_anchors`：真实数据里 `in_traits` 恒 `False`
   （统计时点在晋升之前），而且 `PersonaProfileView` 根本不返回这个字段。
3. **不编、不静默**：`evidence_refs` 指向本库没有的证据（`MISSING_REF`）如实列出；
   同名实体不自动合并（`merge_policy = NEVER_AUTO_MERGE`），只把候选交回给渲染层。

本模块**不发任何外部请求、不产生费用、不写一条审计**。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.context import new_call_context
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, is_err
from ..core.types import Query
from ..modules.evidence import EvidenceService, EvidenceView, SourceView
from ..modules.persona import PersonaProfileView, PersonaService, ProfileItem, ProfileSection
from ..modules.relationship import RelationshipService, RelationshipView
from ..storage.sqlite import MAX_QUERY_LIMIT, SqliteStorage

#: 展示用的板块顺序与标题（**固定顺序**，不随档案字段顺序变化）
SECTION_TITLES: tuple[tuple[str, str], ...] = (
    ("traits", "稳定性格"),
    ("habits", "习惯"),
    ("expression_style", "说话方式"),
    ("single_events", "单次行为"),
    ("background", "背景"),
    ("experiences", "经历"),
)

#: 文本型板块（只有一段话，不是条目列表）
TEXT_SECTION_KEYS = frozenset({"expression_style", "background"})

#: 需要在证据行上"亮出来"的校验告警标签（其余标签是流水信息，不展示）
SURFACE_TAGS = frozenset({
    "TRAIT_TAG_DIMENSION_MISMATCH",
    "CLAIM_NOT_ATOMIC",
    "TARGET_NAME_MISSING",
    "MATERIAL_KEY_CORRECTED",
    "MATERIAL_KEY_AMBIGUOUS",
})

#: 未并入原因分组的展示顺序（其余维度按计数降序跟在后面）
DIMENSION_ORDER = ("CANON_FACT", "RELATION", "BACKGROUND", "EXPERIENCE")


# ---------------------------------------------------------------------------
# 形状
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntityRef:
    """一个实体（同名候选也是这个形状）。"""

    entity_id: str
    display_name: str
    created_at: str = ""
    works: list[str] = field(default_factory=list)
    adaptation_version: str = ""


@dataclass(frozen=True)
class ItemView:
    """档案条目 + 现算出来的来源数/证据数。"""

    text: str
    evidence_ids: list[str] = field(default_factory=list)
    evidence_count: int = 0
    source_count: int = 0
    stability: str = "UNKNOWN"


@dataclass(frozen=True)
class SectionView:
    key: str
    title: str
    items: list[ItemView] = field(default_factory=list)
    is_text: bool = False


@dataclass(frozen=True)
class EvidenceRow:
    """一条证据的展示行（已把来源与定位解析好）。"""

    evidence_id: str
    claim: str
    tier: str
    dimension: str
    trait_tag: str
    quote: str = ""
    source_id: str = ""
    source_title: str = ""
    source_url: str = ""
    retrieved_at: str = ""
    locator: str = ""
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UnmergedView:
    """未并入档案的证据：条数 + 维度分布 + 明细。"""

    total: int = 0
    by_dimension: list[tuple[str, int]] = field(default_factory=list)
    rows: list[EvidenceRow] = field(default_factory=list)


@dataclass
class PersonaReport:
    entity: EntityRef
    profile_version: int | None = None
    status: str = ""
    profile_missing: bool = False
    evidence_total: int = 0
    source_total: int = 0
    referenced_evidence: int = 0
    referenced_sources: int = 0
    sections: list[SectionView] = field(default_factory=list)
    unmerged: UnmergedView = field(default_factory=UnmergedView)
    missing_refs: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    other_entities: list[EntityRef] = field(default_factory=list)
    chosen_is_latest: bool = True


@dataclass(frozen=True)
class RelationshipPerspective:
    from_id: str
    from_name: str
    to_id: str
    to_name: str
    items: list[ItemView] = field(default_factory=list)


@dataclass
class RelationshipReport:
    relationship_id: str
    version: int = 0
    status: str = ""
    cardinality: str = ""
    participants: list[EntityRef] = field(default_factory=list)
    perspectives: list[RelationshipPerspective] = field(default_factory=list)
    shared_experiences: list[ItemView] = field(default_factory=list)
    interaction_patterns: list[ItemView] = field(default_factory=list)
    emotional_expression_habits: list[ItemView] = field(default_factory=list)
    asymmetry_notes: list[ItemView] = field(default_factory=list)
    group_dynamics: dict[str, list[ItemView]] = field(default_factory=dict)
    missing_refs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceGroupView:
    """一条档案条目 + 支撑它的证据（`title` 是**条目文本**，`section` 是板块名）。

    为什么要分两层：用户问的是"这条特征凭什么"，所以输出是
    `■ 板块` → `· 条目  2 个来源` → `├ 证据…`。把板块名拼进 `title` 里
    （`稳定性格 · 寡言…`）会丢掉"同一板块下有多条特征"这个层次。
    """

    title: str
    section: str = ""
    source_count: int = 0
    evidence_count: int = 0
    items: list[EvidenceRow] = field(default_factory=list)


@dataclass
class EvidenceReport:
    entity: EntityRef
    evidence_total: int = 0
    source_total: int = 0
    groups: list[EvidenceGroupView] = field(default_factory=list)
    unmerged: list[EvidenceRow] = field(default_factory=list)
    missing_refs: list[str] = field(default_factory=list)
    trait_filter: str = ""
    available_tags: list[tuple[str, int]] = field(default_factory=list)
    profile_missing: bool = False


@dataclass
class ReadServices:
    """只读栈：三个服务 + 存储句柄。**调用方负责 `storage.close()`**。"""

    storage: SqliteStorage
    ctx: Any
    evidence: EvidenceService
    persona: PersonaService
    relationship: RelationshipService
    database_path: Path


# ---------------------------------------------------------------------------
# 打开只读栈
# ---------------------------------------------------------------------------


def open_read_services(
    database_path: str | Path,
    *,
    project_id: str = "prj_local",
    actor: str = "cli",
) -> Result[ReadServices]:
    """打开一个**只读**栈。文件不存在时直接报错，不建库。"""
    path = Path(database_path)
    if not path.exists():
        return Err(_error(
            ErrorCode.STORAGE_READ_FAILED,
            f"找不到数据库：{path}",
        ))
    storage = SqliteStorage(path)
    ctx = new_call_context(project_id=project_id, actor=actor)
    return Ok(ReadServices(
        storage=storage,
        ctx=ctx,
        evidence=EvidenceService(storage),
        persona=PersonaService(storage),
        relationship=RelationshipService(storage),
        database_path=path,
    ))


def _error(code: ErrorCode, message: str, *, trace_ref: str = "") -> Any:
    return new_error(code, message, trace_ref=trace_ref)


# ---------------------------------------------------------------------------
# 读取小工具
# ---------------------------------------------------------------------------


def _all_rows(storage: SqliteStorage, ctx: Any, kind: str,
              project_id: str) -> Result[list[dict]]:
    """按项目取某类的**全部**行（自动翻页；单页上限只有 200）。"""
    collected: list[dict] = []
    offset = 0
    while True:
        page = storage.query(
            ctx, kind,
            Query(filters={"project_id": project_id}, limit=MAX_QUERY_LIMIT, offset=offset),
        )
        if is_err(page):
            return page
        collected.extend(page.value.items)
        if not page.value.has_more:
            return Ok(collected)
        offset += MAX_QUERY_LIMIT


def _entity_ref(row: dict) -> EntityRef:
    meta = row.get("fictional_character_meta") or {}
    works = [str(w.get("title") or "") for w in (meta.get("works") or [])
             if isinstance(w, dict)]
    return EntityRef(
        entity_id=str(row.get("id") or ""),
        display_name=str(row.get("display_name") or ""),
        created_at=str(row.get("created_at") or ""),
        works=[w for w in works if w],
        adaptation_version=str(meta.get("adaptation_version") or ""),
    )


def _locator_map(services: ReadServices) -> dict[str, Any]:
    """一次取出全部证据原始行的 `locator`。

    `EvidenceView` **不带** `locator`（它不是展示字段），但 `evidence` 命令要
    "能追溯到材料定位"。与其逐条 `storage.get`（N 次查询），不如一次把原始行
    读出来——只读命令的唯一成本就在观察者自己的等待上。
    """
    rows = _all_rows(services.storage, services.ctx, "EvidenceItem", services.ctx.project_id)
    if is_err(rows):
        return {}
    return {str(row.get("id") or ""): row.get("locator") for row in rows.value}


def resolve_entity(
    services: ReadServices,
    name_or_id: str,
    *,
    need_profile: bool = True,
    version: int | None = None,
) -> Result[tuple[EntityRef, list[EntityRef]]]:
    """名字 → 实体。返回 `(选中的实体, 其余同名实体)`。

    **不去重、不合并**（`merge_policy = NEVER_AUTO_MERGE`）。选择规则：

    1. 传入 `pe_` 前缀 ID → 直接按 ID 取（逃生口，永远精确）；
    2. 0 个同名 → `Err(NOT_FOUND_ENTITY)`；
    3. 多个同名 → 取 `created_at` 最新的那个；若最新的**没有档案**而更早的有，
       取"最新的**有档案的**那个"——只读命令的第一职责是别让用户看到空转的错误，
       但"其余同名实体"必须原样交回给渲染层，不能悄悄藏起来。
       同一秒建的多份（时间戳相同）按插入顺序兜底，后插入的算更晚。
    """
    text = str(name_or_id or "").strip()
    if not text:
        return Err(_error(ErrorCode.VALIDATION_REQUIRED_FIELD, "名字不能为空"))

    if text.startswith("pe_"):
        row = services.storage.get(services.ctx, "PersonEntity", text)
        if is_err(row):
            return row
        if row.value is None:
            return Err(_error(ErrorCode.NOT_FOUND_ENTITY, f"实体不存在：{text}"))
        return Ok((_entity_ref(row.value), []))

    entities = _all_rows(services.storage, services.ctx, "PersonEntity", services.ctx.project_id)
    if is_err(entities):
        return entities
    rows = [row for row in entities.value
            if str(row.get("display_name") or "").strip() == text]
    if not rows:
        return Err(_error(
            ErrorCode.NOT_FOUND_ENTITY,
            f"没有找到「{text}」：本库里没有这个名字的实体",
        ))

    # `created_at` 只精确到**秒**（`to_iso`），同一次 `run` 里建的同名实体时间戳
    # 会完全相同。此时用行序兜底：`_all_rows` 按 `created_at ASC` 返回，稳定排序
    # 下它就是插入顺序，后插入的更晚。不给这层兜底的话，`sort` 的稳定性会让人
    # 们**悄悄选中更早的那一份**，而输出照样写着"已显示最新"——那是最坏的一种错。
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda pair: (str(pair[1].get("created_at") or ""), pair[0]),
                 reverse=True)
    same = [_entity_ref(row) for _, row in indexed]
    chosen = same[0]
    if need_profile and len(same) > 1:
        with_profile = [e for e in same if _has_profile(services, e.entity_id, version)]
        if with_profile:
            chosen = with_profile[0]
    others = [e for e in same if e.entity_id != chosen.entity_id]
    return Ok((chosen, others))


def _has_profile(services: ReadServices, entity_id: str, version: int | None) -> bool:
    outcome = services.persona.get_persona(services.ctx, entity_id, version)
    return not is_err(outcome)


def _source_of(services: ReadServices, source_id: str,
               cache: dict[str, SourceView | None]) -> SourceView | None:
    if source_id in cache:
        return cache[source_id]
    if not source_id:
        cache[source_id] = None
        return None
    outcome = services.evidence.get_source(services.ctx, source_id)
    view = None if is_err(outcome) else outcome.value
    cache[source_id] = view
    return view


def _locator_text(locator: Any) -> str:
    if isinstance(locator, dict):
        field_name = str(locator.get("field") or "")
        start = locator.get("start")
        end = locator.get("end")
        if field_name and start is not None and end is not None:
            return f"{field_name}[{start}:{end}]"
        if field_name:
            return field_name
    if isinstance(locator, str) and locator.strip():
        return locator.strip()
    return ""


def _evidence_row(view: EvidenceView, source: SourceView | None,
                  locator: Any = None) -> EvidenceRow:
    flags = [tag for tag in (view.tags or []) if tag in SURFACE_TAGS]
    return EvidenceRow(
        evidence_id=view.id,
        claim=str(view.claim or ""),
        tier=str(view.tier or ""),
        dimension=str(view.dimension or ""),
        trait_tag=str(view.trait_tag or ""),
        quote=str(view.verbatim_quote or ""),
        source_id=str(view.source_id or ""),
        source_title=str(getattr(source, "title", "") or ""),
        source_url=str(getattr(source, "url", "") or ""),
        retrieved_at=str(getattr(source, "retrieved_at", "") or ""),
        locator=_locator_text(locator),
        flags=flags,
    )


def _subject_evidence(services: ReadServices, entity_id: str) -> Result[list[EvidenceView]]:
    """某实体的全部证据（自动翻页）。"""
    collected: list[EvidenceView] = []
    offset = 0
    while True:
        page = services.evidence.query_evidence(
            services.ctx,
            Query(filters={"entity_ids": [entity_id]}, limit=MAX_QUERY_LIMIT, offset=offset),
        )
        if is_err(page):
            return page
        collected.extend(page.value.items)
        if not page.value.has_more:
            return Ok(collected)
        offset += MAX_QUERY_LIMIT


def _stability_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "UNKNOWN")


def _item_view(item: ProfileItem, evidence: dict[str, EvidenceView]) -> ItemView:
    refs = [str(r) for r in (item.evidence_refs or [])]
    found = [evidence[r] for r in refs if r in evidence]
    sources = {str(e.source_id) for e in found if str(e.source_id or "")}
    return ItemView(
        text=str(item.text or "").strip(),
        evidence_ids=refs,
        evidence_count=len(found),
        source_count=len(sources),
        stability=_stability_text(item.stability),
    )


def _section_items(section: ProfileSection, evidence: dict[str, EvidenceView]) -> ItemView:
    refs = [str(r) for r in (section.evidence_refs or [])]
    found = [evidence[r] for r in refs if r in evidence]
    sources = {str(e.source_id) for e in found if str(e.source_id or "")}
    return ItemView(
        text=str(section.text or "").strip(),
        evidence_ids=refs,
        evidence_count=len(found),
        source_count=len(sources),
        stability="REPEATED" if len(sources) >= 2 else "SINGLE",
    )


def _unmerged_of(evidences: list[EvidenceView], referenced: set[str],
                 services: ReadServices,
                 locators: dict[str, Any] | None = None) -> UnmergedView:
    cache: dict[str, SourceView | None] = {}
    locators = locators or {}
    rows: list[EvidenceRow] = []
    for view in evidences:
        if view.id in referenced:
            continue
        rows.append(_evidence_row(
            view,
            _source_of(services, str(view.source_id or ""), cache),
            locators.get(view.id),
        ))
    counts: dict[str, int] = {}
    for view in evidences:
        if view.id in referenced:
            continue
        key = str(view.dimension or "UNKNOWN")
        counts[key] = counts.get(key, 0) + 1
    ordered = sorted(
        counts.items(),
        key=lambda kv: (
            DIMENSION_ORDER.index(kv[0]) if kv[0] in DIMENSION_ORDER else len(DIMENSION_ORDER),
            -kv[1],
            kv[0],
        ),
    )
    return UnmergedView(total=len(rows), by_dimension=ordered, rows=rows)


# ---------------------------------------------------------------------------
# 人格档案
# ---------------------------------------------------------------------------


def persona_report(
    services: ReadServices,
    name_or_id: str,
    *,
    version: int | None = None,
) -> Result[PersonaReport]:
    """读一个人物的档案报告（含未并入证据、同名候选）。"""
    resolved = resolve_entity(services, name_or_id, need_profile=True, version=version)
    if is_err(resolved):
        return resolved
    entity, others = resolved.value

    evidences = _subject_evidence(services, entity.entity_id)
    if is_err(evidences):
        return evidences
    index = {view.id: view for view in evidences.value}
    source_ids = {str(v.source_id) for v in evidences.value if str(v.source_id or "")}
    locators = _locator_map(services)

    report = PersonaReport(
        entity=entity,
        evidence_total=len(evidences.value),
        source_total=len(source_ids),
        other_entities=others,
        chosen_is_latest=not others or all(
            e.created_at <= entity.created_at for e in others),
    )

    outcome = services.persona.get_persona(services.ctx, entity.entity_id, version)
    if is_err(outcome):
        # 实体在、档案不在：不是错误，是"还没跑到档案阶段"。照实说。
        report.profile_missing = True
        report.status = "MISSING"
        report.unmerged = _unmerged_of(evidences.value, set(), services, locators)
        return Ok(report)

    profile: PersonaProfileView = outcome.value
    report.profile_version = int(profile.version)
    report.status = str(getattr(profile.status, "value", profile.status) or "")
    report.open_questions = [str(q) for q in (profile.open_questions or [])]

    referenced: set[str] = set()
    for key, title in SECTION_TITLES:
        if key in TEXT_SECTION_KEYS:
            section = getattr(profile, key, None)
            if section is None or not str(section.text or "").strip():
                report.sections.append(SectionView(key=key, title=title, is_text=True))
                continue
            referenced.update(str(r) for r in (section.evidence_refs or []))
            report.sections.append(SectionView(
                key=key, title=title, is_text=True,
                items=[_section_items(section, index)]))
        else:
            items = [it for it in (getattr(profile, key, None) or [])
                     if str(getattr(it, "text", "") or "").strip()]
            for item in items:
                referenced.update(str(r) for r in (item.evidence_refs or []))
            report.sections.append(SectionView(
                key=key, title=title,
                items=[_item_view(it, index) for it in items]))

    found_refs = [r for r in referenced if r in index]
    report.referenced_evidence = len(found_refs)
    report.referenced_sources = len({
        str(index[r].source_id) for r in found_refs if str(index[r].source_id or "")
    })
    report.missing_refs = sorted(r for r in referenced if r not in index)
    report.unmerged = _unmerged_of(evidences.value, referenced, services, locators)
    return Ok(report)


# ---------------------------------------------------------------------------
# 关系档案
# ---------------------------------------------------------------------------


def _items_with_counts(items: list[ProfileItem],
                       evidence: dict[str, EvidenceView]) -> list[ItemView]:
    return [_item_view(it, evidence) for it in items
            if str(getattr(it, "text", "") or "").strip()]


def relationship_report(
    services: ReadServices,
    first: str,
    second: str,
) -> Result[RelationshipReport]:
    """读两人关系档案。**双向视角分开列**（D-018：A 看 B ≠ B 看 A）。"""
    left = resolve_entity(services, first, need_profile=False)
    if is_err(left):
        return left
    right = resolve_entity(services, second, need_profile=False)
    if is_err(right):
        return right
    entity_a, _ = left.value
    entity_b, _ = right.value

    outcome = services.relationship.get_relationship(
        services.ctx, [entity_a.entity_id, entity_b.entity_id])
    if is_err(outcome):
        return outcome
    view: RelationshipView = outcome.value

    # 参与者与证据：一次取全两人的证据（视角条目可能引用对方的证据）
    evidences: dict[str, EvidenceView] = {}
    for entity in (entity_a, entity_b):
        loaded = _subject_evidence(services, entity.entity_id)
        if is_err(loaded):
            return loaded
        for item in loaded.value:
            evidences[item.id] = item

    names: dict[str, str] = {
        entity_a.entity_id: entity_a.display_name,
        entity_b.entity_id: entity_b.display_name,
    }
    for pid in (view.participant_entity_ids or []):
        if pid in names:
            continue
        row = services.storage.get(services.ctx, "PersonEntity", pid)
        if not is_err(row) and row.value is not None:
            names[pid] = _entity_ref(row.value).display_name
    participants = [
        EntityRef(entity_id=str(pid), display_name=names.get(str(pid), str(pid)))
        for pid in (view.participant_entity_ids or [])
    ]

    perspectives: list[RelationshipPerspective] = []
    for owner_id, block in (view.perspectives or {}).items():
        for target_id, items in (getattr(block, "view_of_targets", None) or {}).items():
            perspectives.append(RelationshipPerspective(
                from_id=str(owner_id),
                from_name=names.get(str(owner_id), str(owner_id)),
                to_id=str(target_id),
                to_name=names.get(str(target_id), str(target_id)),
                items=_items_with_counts(list(items), evidences),
            ))
    perspectives.sort(key=lambda p: (p.from_name, p.to_name))

    group: dict[str, list[ItemView]] = {}
    dynamics = view.group_dynamics
    if dynamics is not None:
        for key, label in (("alliances", "联盟"), ("exclusion", "站队 / 冷落"),
                           ("relay", "传话 / 信息中转")):
            group[label] = _items_with_counts(list(getattr(dynamics, key, None) or []), evidences)
        if str(getattr(dynamics, "note", "") or "").strip():
            group["备注"] = [ItemView(text=str(dynamics.note).strip())]

    referenced: set[str] = set()
    for block in (view.perspectives or {}).values():
        for items in (getattr(block, "view_of_targets", None) or {}).values():
            for item in items:
                referenced.update(str(r) for r in (item.evidence_refs or []))
    for collection in (view.shared_experiences, view.interaction_patterns,
                       view.emotional_expression_habits, view.asymmetry_notes):
        for item in collection or []:
            referenced.update(str(r) for r in (item.evidence_refs or []))

    return Ok(RelationshipReport(
        relationship_id=view.id,
        version=int(view.version),
        status=str(getattr(view.status, "value", view.status) or ""),
        cardinality=str(getattr(view.cardinality, "value", view.cardinality) or ""),
        participants=participants,
        perspectives=perspectives,
        shared_experiences=_items_with_counts(list(view.shared_experiences or []), evidences),
        interaction_patterns=_items_with_counts(list(view.interaction_patterns or []), evidences),
        emotional_expression_habits=_items_with_counts(
            list(view.emotional_expression_habits or []), evidences),
        asymmetry_notes=_items_with_counts(list(view.asymmetry_notes or []), evidences),
        group_dynamics=group,
        missing_refs=sorted(r for r in referenced if r not in evidences),
    ))


# ---------------------------------------------------------------------------
# 证据
# ---------------------------------------------------------------------------


def evidence_report(
    services: ReadServices,
    name_or_id: str,
    *,
    trait: str = "",
) -> Result[EvidenceReport]:
    """把证据按"档案条目 → 证据 → 来源"分组；未进档案的单列一节。"""
    resolved = resolve_entity(services, name_or_id, need_profile=False)
    if is_err(resolved):
        return resolved
    entity, _others = resolved.value

    loaded = _subject_evidence(services, entity.entity_id)
    if is_err(loaded):
        return loaded
    all_evidence = loaded.value
    full_index = {view.id: view for view in all_evidence}
    locators = _locator_map(services)

    tags: dict[str, int] = {}
    for view in all_evidence:
        tag = str(view.trait_tag or "")
        if tag:
            tags[tag] = tags.get(tag, 0) + 1

    wanted = str(trait or "").strip().lower()
    visible = [v for v in all_evidence
               if not wanted or str(v.trait_tag or "").lower() == wanted]
    if wanted and not visible:
        return Ok(EvidenceReport(
            entity=entity,
            evidence_total=len(all_evidence),
            source_total=len({str(v.source_id) for v in all_evidence
                              if str(v.source_id or "")}),
            trait_filter=str(trait),
            available_tags=sorted(tags.items(), key=lambda kv: (-kv[1], kv[0])),
        ))

    cache: dict[str, SourceView | None] = {}

    def row_of(view: EvidenceView) -> EvidenceRow:
        return _evidence_row(
            view,
            _source_of(services, str(view.source_id or ""), cache),
            locators.get(view.id),
        )

    report = EvidenceReport(
        entity=entity,
        evidence_total=len(all_evidence),
        source_total=len({str(v.source_id) for v in all_evidence
                          if str(v.source_id or "")}),
        trait_filter=str(trait or ""),
        available_tags=sorted(tags.items(), key=lambda kv: (-kv[1], kv[0])),
    )

    outcome = services.persona.get_persona(services.ctx, entity.entity_id)
    if is_err(outcome):
        report.profile_missing = True
        report.unmerged = [row_of(v) for v in visible]
        return Ok(report)

    profile = outcome.value
    referenced: set[str] = set()
    visible_ids = index_of(visible)

    def emit(section_title: str, text: str, refs: list[str]) -> None:
        """把"板块 · 条目 → 该条目引用的证据"装成一组（顺序由档案分区决定）。"""
        if not text or not refs:
            return
        referenced.update(refs)
        rows = [row_of(full_index[r]) for r in refs
                if r in full_index and r in visible_ids]
        if not rows:
            return
        sources = {row.source_id for row in rows if row.source_id}
        report.groups.append(EvidenceGroupView(
            title=text, section=section_title,
            source_count=len(sources), evidence_count=len(rows), items=rows))

    for key, title in SECTION_TITLES:
        if key in TEXT_SECTION_KEYS:
            section = getattr(profile, key, None)
            emit(title, str(getattr(section, "text", "") or "").strip(),
                 [str(r) for r in (getattr(section, "evidence_refs", None) or [])])
        else:
            for item in (getattr(profile, key, None) or []):
                emit(title, str(getattr(item, "text", "") or "").strip(),
                     [str(r) for r in (item.evidence_refs or [])])

    report.missing_refs = sorted(r for r in referenced if r not in full_index)
    report.unmerged = [row_of(v) for v in visible if v.id not in referenced]
    return Ok(report)


def index_of(evidences: list[EvidenceView]) -> set[str]:
    """证据列表 → ID 集合（`evidence_report` 过滤后判断"这条还可见吗"）。"""
    return {view.id for view in evidences}
