"""Memory 模块 —— 长期记忆与关键词检索（docs/INTERFACES.md §9、DATA_MODEL.md §4.10/§4.11）。

**职责**：记忆写入、去重、冲突关联、检索、更新（新版本）、过期、归档、删除；
摘要/索引/缓存（`DerivedArtifact`）的生命周期。

**非职责**（INV-3 / F-3）：⛔ 记忆永远不能升级成证据（本模块里**没有** `add_evidence`）；
⛔ 不改人物/关系档案；⛔ 不把 `SIMULATED` 提升为其它命名空间（无 `promote_to_real`）。

**隔离约束**：
- 检索结果**原样带出 `namespace`**（D-024），调用方据此区分模拟/真实；
- 删除记忆时，相关 `DerivedArtifact` **同事务失效**（D-051）；
- 模拟记忆 `namespace=SIMULATED` 且 `source_type=SIMULATED_DIALOGUE`，检索时不可被"洗白"。

**首版检索方案**（D-042 / D-074）：采用关键词检索，在内存中对已加载的 `MemoryItem`
做子串匹配（D-074 的"自建字串倒排表"退化方案）。**不引入 FTS5 表**，以避免未审查的
schema 迁移；FTS5 是未来优化项（已在报告中标注）。

**与 `MemoryPort` 骨架的差异**（需报告，见 DECISIONS §2.21）：`memory.py` 原有的
`MemoryPort` 只是早于 INTERFACES §9.3 详细契约的占位骨架（方法名 `update`/`expire`
与 §9.3 的 `supersede`/`expire_sweep` 不符，且缺 `link_duplicate`/`link_conflict`/
`consolidate`/`rebuild_derived`/`invalidate_derived`）。本文件将 `MemoryPort` 对齐到
§9.3，并实现 `MemoryService`。`Dialogue` 注入的窄端口 `MemoryWriterPort`（定义于
`dialogue.py`）保持仅 `write_simulated`，未改动。

**`write_simulated` 签名说明**：§9.3 写为 `write_simulated(ctx, turn_id, items)`，
但 `Dialogue` 依赖的 `MemoryWriterPort.write_simulated(ctx, draft: dict)` 是已验收的
注入契约，故本实现采用 `(ctx, draft)` 单字典形式，`draft` 内含 `source_turn_id`；
`namespace` 与 `source_type` 由方法内部强制锁定。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from ..core.context import CallContext
from ..core.enums import (
    DerivedKind,
    DerivedStatus,
    MemoryKind,
    MemoryScope,
    MemorySourceType,
    MemoryStatus,
    ProvenanceNamespace,
    PurgeState,
)
from ..core.errors import ErrorCode, new_error
from ..core.ids import new_id
from ..core.result import Err, Ok, Result, WarningNote, is_err
from ..core.temporal import from_iso, to_iso, utc_now
from ..core.types import MAX_QUERY_LIMIT, Page, Query
from ..storage.sqlite import SqliteStorage

# 软删除后到物理清理（逻辑不可达）的宽限期（天），对应 Q-21。
PURGE_GRACE_DAYS = 30

# 派生产物命名空间：系统内部加速数据，统一归 SYSTEM_DERIVED。
_DERIVED_NAMESPACE = ProvenanceNamespace.SYSTEM_DERIVED.value

# 模拟记忆在对话上下文里必须显式声明的隔离标记（与 Dialogue 模块保持一致语义）。
MEMORY_SIMULATION_NOTICE = (
    "【模拟记忆】以下记忆来自模拟对话，不构成真实事件或作品官方设定。"
)


# --------------------------------------------------------------------------
# 公共 DTO
# --------------------------------------------------------------------------


@dataclass
class MemoryRef:
    """写入/更新后返回的轻量引用（INTERFACES §9.3 `MemoryRef`）。"""

    id: str
    kind: str
    content_excerpt: str
    namespace: str
    lifecycle_status: str
    duplicate_of_memory_id: str | None = None


@dataclass
class MemoryView:
    """检索返回的视图（INTERFACES §9.3 `MemoryView`，必带隔离字段）。"""

    id: str
    scope: str
    scope_id: str
    kind: str
    content: str
    related_entity_ids: list[str]
    namespace: str
    source_type: str
    source_turn_id: str | None
    confidence: float
    confidence_means: str
    salience: float
    lifecycle_status: str
    valid_from: str | None = None
    expires_at: str | None = None


@dataclass
class MemoryQuery:
    """记忆检索条件（INTERFACES §9.3 `MemoryQuery`）。"""

    text: str | None = None
    scope: str | None = None
    scope_id: str | None = None
    entity_ids: list[str] | None = None
    kinds: list[str] | None = None
    namespaces_include: list[str] | None = None
    namespaces_exclude: list[str] | None = None
    lifecycle_states: list[str] | None = None  # 默认 [ACTIVE]
    include_expired: bool = False
    limit: int = 50
    offset: int = 0


@dataclass
class DeleteTarget:
    """删除目标：支持三级（INTERFACES §9.3）—— 单个 id / 按 scope / 按 namespace。"""

    id: str | None = None
    scope: str | None = None
    namespace: str | None = None


@dataclass
class ConflictLinkRef:
    """冲突关联结果。"""

    memory_id_a: str
    memory_id_b: str
    note: str | None = None


@dataclass
class SweepOutcome:
    expired_ids: list[str] = field(default_factory=list)
    invalidated_artifact_ids: list[str] = field(default_factory=list)


@dataclass
class ArchiveOutcome:
    archived_ids: list[str] = field(default_factory=list)


@dataclass
class DeleteOutcome:
    deleted_ids: list[str] = field(default_factory=list)
    invalidated_artifact_ids: list[str] = field(default_factory=list)
    scheduled_purge_at: str | None = None


@dataclass
class DerivedArtifactRef:
    artifact_id: str | None
    source_memory_ids: list[str]
    fresh: bool


# --------------------------------------------------------------------------
# 端口（对齐 INTERFACES §9.3；原骨架的方法名已重命名以匹配已验收契约）
# --------------------------------------------------------------------------


class MemoryPort(Protocol):
    """记忆端口。⛔ 不得出现 `add_evidence` / `record_inference`（F-3 / INV-3）。"""

    def write(self, ctx: CallContext, drafts: list[dict]) -> Result[list[MemoryRef]]:
        ...

    def write_simulated(self, ctx: CallContext, draft: dict) -> Result[MemoryRef]:
        ...

    def link_duplicate(self, ctx: CallContext, memory_id: str, duplicate_of_id: str) -> Result[None]:
        ...

    def link_conflict(
        self, ctx: CallContext, memory_id_a: str, memory_id_b: str, note: str | None = None
    ) -> Result[ConflictLinkRef]:
        ...

    def search(self, ctx: CallContext, q: MemoryQuery) -> Result[Page[MemoryView]]:
        ...

    def supersede(self, ctx: CallContext, memory_id: str, new_draft: dict) -> Result[MemoryRef]:
        ...

    def expire_sweep(self, ctx: CallContext, scope: str | None = None, now: str | None = None) -> Result[SweepOutcome]:
        ...

    def archive(self, ctx: CallContext, target: list[str] | str) -> Result[ArchiveOutcome]:
        ...

    def delete(self, ctx: CallContext, target: DeleteTarget) -> Result[DeleteOutcome]:
        ...

    def consolidate(
        self, ctx: CallContext, scope: str | None = None, session_id: str | None = None
    ) -> Result[DerivedArtifactRef]:
        ...

    def rebuild_derived(self, ctx: CallContext, artifact_id: str) -> Result[DerivedArtifactRef]:
        ...

    def invalidate_derived(self, ctx: CallContext, ids: list[str]) -> Result[None]:
        ...

    def clear_simulated(self, ctx: CallContext, scope: str | None = None) -> Result[int]:
        ...

    def purge_expired(self, ctx: CallContext, now: str | None = None) -> Result[int]:
        ...


# --------------------------------------------------------------------------
# 默认 namespace 推导（source_type → namespace 一致性）
# --------------------------------------------------------------------------

_SOURCE_NS_DEFAULT = {
    MemorySourceType.SIMULATED_DIALOGUE.value: ProvenanceNamespace.SIMULATED.value,
    MemorySourceType.USER_INPUT.value: ProvenanceNamespace.USER_PROVIDED.value,
    MemorySourceType.SYSTEM_CONSOLIDATION.value: ProvenanceNamespace.SYSTEM_DERIVED.value,
    MemorySourceType.PUBLIC_SOURCE_REFERENCE.value: ProvenanceNamespace.PUBLIC_SOURCE.value,
}


def _default_namespace(source_type: str) -> str:
    return _SOURCE_NS_DEFAULT.get(source_type, ProvenanceNamespace.USER_PROVIDED.value)


# --------------------------------------------------------------------------
# 实现
# --------------------------------------------------------------------------


class MemoryService:
    """长期记忆服务（实现 `MemoryPort`）。"""

    def __init__(self, storage: SqliteStorage) -> None:
        self.storage = storage

    # ---------------------------------------------------------------- 写入

    def write(self, ctx: CallContext, drafts: list[dict]) -> Result[list[MemoryRef]]:
        """写入一条或多条记忆。重复内容命中现有记忆时返回已存在 id + `MEMORY_DEDUP_HIT` 提示。"""
        if not drafts:
            return Ok([])
        refs: list[MemoryRef] = []
        warnings: list[WarningNote] = []
        for draft in drafts:
            err = self._validate_draft(draft, ctx)
            if is_err(err):
                return err  # 任一条校验失败整体拒绝（不落半截）
            dup = self._find_duplicate(ctx, draft)
            if dup is not None:
                refs.append(
                    MemoryRef(
                        id=dup["id"],
                        kind=dup["kind"],
                        content_excerpt=str(dup.get("content", ""))[:120],
                        namespace=dup["namespace"],
                        lifecycle_status=dup["lifecycle_status"],
                        duplicate_of_memory_id=dup["id"],
                    )
                )
                warnings.append(
                    WarningNote(code="MEMORY_DEDUP_HIT", message=f"内容已存在：{dup['id']}")
                )
                continue
            mid = new_id("MemoryItem")
            payload = self._build_payload(draft, mid)
            res = self.storage.put(
                ctx,
                "MemoryItem",
                payload,
                project_id=ctx.project_id,
                namespace=payload["namespace"],
                created_by=ctx.actor,
                id=mid,
            )
            if is_err(res):
                return res
            refs.append(
                MemoryRef(
                    id=mid,
                    kind=payload["kind"],
                    content_excerpt=payload["content"][:120],
                    namespace=payload["namespace"],
                    lifecycle_status=payload["lifecycle_status"],
                )
            )
        return Ok(refs, warnings=warnings)

    def write_simulated(self, ctx: CallContext, draft: dict) -> Result[MemoryRef]:
        """`Dialogue` 唯一可用的记忆写入通道。强制 `namespace=SIMULATED` + `source_type=SIMULATED_DIALOGUE`。

        去重与 `write()` 同口径（P6.2-a 修复）：同 `namespace` + 同 `scope` + 同
        `scope_id` + 同 `content` 且仍 `ACTIVE` 的既有记忆，直接返回它（带
        `MEMORY_DEDUP_HIT` 提示），**不再落第二条**。

        为什么这条必须在写入层修、而不是只在调用方预查：`commit_turn` 的事务里
        逐条调用本方法，调用方（规则引擎 / 重放 / 将来任何入口）都不该各自实现一遍
        查重；漏一个入口，重复记忆就会进库，并被下一轮当上下文注入。
        """
        required = (
            "scope",
            "scope_id",
            "kind",
            "content",
            "source_turn_id",
            "confidence",
            "confidence_means",
            "salience",
        )
        for f in required:
            if f not in draft or draft[f] in (None, ""):
                return Err(
                    new_error(
                        ErrorCode.VALIDATION_REQUIRED_FIELD,
                        f"模拟记忆缺少必填字段：{f}",
                        trace_ref=ctx.trace_ref,
                    )
                )
        full = dict(draft)
        full["source_type"] = MemorySourceType.SIMULATED_DIALOGUE.value
        full["namespace"] = ProvenanceNamespace.SIMULATED.value
        if "lifecycle_status" not in full:
            full["lifecycle_status"] = MemoryStatus.ACTIVE.value
        err = self._validate_draft(full, ctx)
        if is_err(err):
            return err
        dup = self._find_duplicate(ctx, full)
        if dup is not None:
            return Ok(
                MemoryRef(
                    id=dup["id"],
                    kind=dup["kind"],
                    content_excerpt=str(dup.get("content", ""))[:120],
                    namespace=dup["namespace"],
                    lifecycle_status=dup["lifecycle_status"],
                    duplicate_of_memory_id=dup["id"],
                ),
                warnings=[WarningNote(
                    code="MEMORY_DEDUP_HIT",
                    message=f"内容已存在：{dup['id']}")],
            )
        mid = new_id("MemoryItem")
        payload = self._build_payload(
            full,
            mid,
            forced_namespace=ProvenanceNamespace.SIMULATED.value,
            forced_source_type=MemorySourceType.SIMULATED_DIALOGUE.value,
        )
        res = self.storage.put(
            ctx,
            "MemoryItem",
            payload,
            project_id=ctx.project_id,
            namespace=ProvenanceNamespace.SIMULATED.value,
            created_by=ctx.actor,
            id=mid,
        )
        if is_err(res):
            return res
        return Ok(
            MemoryRef(
                id=mid,
                kind=payload["kind"],
                content_excerpt=payload["content"][:120],
                namespace=ProvenanceNamespace.SIMULATED.value,
                lifecycle_status=MemoryStatus.ACTIVE.value,
            )
        )

    # ---------------------------------------------------------------- 去重/冲突

    def link_duplicate(
        self, ctx: CallContext, memory_id: str, duplicate_of_id: str
    ) -> Result[None]:
        """只做关联标记，不删除被指为重复的那一条（保留可追溯）。"""
        if memory_id == duplicate_of_id:
            return Err(
                new_error(
                    ErrorCode.VALIDATION_SELF_REFERENCE,
                    "去重不能指向自身",
                    trace_ref=ctx.trace_ref,
                )
            )
        a = self._get_memory(ctx, memory_id)
        if is_err(a):
            return a
        b = self._get_memory(ctx, duplicate_of_id)
        if is_err(b):
            return b
        return self.storage.update(
            ctx,
            "MemoryItem",
            memory_id,
            {"duplicate_of_memory_id": duplicate_of_id},
        )

    def link_conflict(
        self,
        ctx: CallContext,
        memory_id_a: str,
        memory_id_b: str,
        note: str | None = None,
    ) -> Result[ConflictLinkRef]:
        """双向登记冲突，双方置 `CONFLICTED`；不合并、不择优。"""
        if memory_id_a == memory_id_b:
            return Err(
                new_error(
                    ErrorCode.VALIDATION_SELF_REFERENCE,
                    "冲突不能指向自身",
                    trace_ref=ctx.trace_ref,
                )
            )
        a = self._get_memory(ctx, memory_id_a)
        if is_err(a):
            return a
        b = self._get_memory(ctx, memory_id_b)
        if is_err(b):
            return b

        def fn() -> Result[None]:
            a_conf = list(a.value.get("conflict_with_memory_ids") or [])
            if memory_id_b not in a_conf:
                a_conf.append(memory_id_b)
            b_conf = list(b.value.get("conflict_with_memory_ids") or [])
            if memory_id_a not in b_conf:
                b_conf.append(memory_id_a)
            u1 = self.storage.update(
                ctx,
                "MemoryItem",
                memory_id_a,
                {
                    "conflict_with_memory_ids": a_conf,
                    "lifecycle_status": MemoryStatus.CONFLICTED.value,
                },
            )
            if is_err(u1):
                return u1
            u2 = self.storage.update(
                ctx,
                "MemoryItem",
                memory_id_b,
                {
                    "conflict_with_memory_ids": b_conf,
                    "lifecycle_status": MemoryStatus.CONFLICTED.value,
                },
            )
            return u2

        res = self.storage.transaction(ctx, fn)
        if is_err(res):
            return res
        return Ok(ConflictLinkRef(memory_id_a=memory_id_a, memory_id_b=memory_id_b, note=note))

    # ---------------------------------------------------------------- 检索

    def search(self, ctx: CallContext, q: MemoryQuery) -> Result[Page[MemoryView]]:
        """关键词检索。默认只返回 `ACTIVE`；`DELETED` 永远排除；`EXPIRED`/`ARCHIVED` 默认排除。"""
        loaded = self._load_memories(ctx, include_deleted=True)
        if is_err(loaded):
            return loaded
        states = q.lifecycle_states if q.lifecycle_states is not None else [MemoryStatus.ACTIVE.value]

        matched: list[dict] = []
        for m in loaded.value:
            ls = m.get("lifecycle_status")
            if ls == MemoryStatus.DELETED.value and MemoryStatus.DELETED.value not in states:
                continue
            if ls not in states:
                if not (q.include_expired and ls == MemoryStatus.EXPIRED.value):
                    continue
            if q.scope and m.get("scope") != q.scope:
                continue
            if q.scope_id and m.get("scope_id") != q.scope_id:
                continue
            if q.kinds and m.get("kind") not in q.kinds:
                continue
            if q.entity_ids:
                rel = set(m.get("related_entity_ids") or [])
                if not (set(q.entity_ids) & rel):
                    continue
            if q.namespaces_include and m.get("namespace") not in q.namespaces_include:
                continue
            if q.namespaces_exclude and m.get("namespace") in q.namespaces_exclude:
                continue
            if q.text and str(q.text).strip() and not self._matches(q.text, m.get("content", "")):
                continue
            matched.append(m)

        scored = [(self._score(q.text, m), m) for m in matched]
        scored.sort(key=lambda x: (x[0], x[1].get("created_at", "")), reverse=True)

        start = q.offset
        end = q.offset + q.limit
        page_items = [self._to_view(m) for _, m in scored[start:end]]
        self._record_access(ctx, [m["id"] for _, m in scored])  # 读路径副作用，显式记录
        total = len(scored)
        return Ok(
            Page(items=page_items, total=total, has_more=(start + len(page_items)) < total)
        )

    # ---------------------------------------------------------------- 更新

    def supersede(self, ctx: CallContext, memory_id: str, new_draft: dict) -> Result[MemoryRef]:
        """"更新" = 新建版本 + 旧版本置 `SUPERSEDED`（ME-V4：content 不可变，绝不原地改）。"""
        old = self._get_memory(ctx, memory_id)
        if is_err(old):
            return old
        old_row = old.value
        full = {
            "scope": new_draft.get("scope", old_row["scope"]),
            "scope_id": new_draft.get("scope_id", old_row["scope_id"]),
            "kind": new_draft.get("kind", old_row["kind"]),
            "content": new_draft["content"],
            "related_entity_ids": new_draft.get(
                "related_entity_ids", old_row.get("related_entity_ids", [])
            ),
            "source_type": new_draft.get("source_type", old_row["source_type"]),
            "source_turn_id": new_draft.get("source_turn_id", old_row.get("source_turn_id")),
            "referenced_evidence_ids": new_draft.get(
                "referenced_evidence_ids", old_row.get("referenced_evidence_ids", [])
            ),
            "confidence": new_draft.get("confidence", old_row.get("confidence")),
            "confidence_means": new_draft.get("confidence_means", old_row.get("confidence_means")),
            "uncertainty_note": new_draft.get("uncertainty_note", old_row.get("uncertainty_note")),
            "salience": new_draft.get("salience", old_row.get("salience")),
            "valid_from": new_draft.get("valid_from", old_row.get("valid_from")),
            "expires_at": new_draft.get("expires_at", old_row.get("expires_at")),
            "namespace": new_draft.get("namespace", old_row["namespace"]),
            "lifecycle_status": MemoryStatus.ACTIVE.value,
        }
        err = self._validate_draft(full, ctx)
        if is_err(err):
            return err

        new_mid = new_id("MemoryItem")
        new_payload = self._build_payload(full, new_mid)

        def fn() -> Result[None]:
            put = self.storage.put(
                ctx,
                "MemoryItem",
                new_payload,
                project_id=ctx.project_id,
                namespace=new_payload["namespace"],
                created_by=ctx.actor,
                id=new_mid,
            )
            if is_err(put):
                return put
            return self.storage.update(
                ctx,
                "MemoryItem",
                memory_id,
                {
                    "lifecycle_status": MemoryStatus.SUPERSEDED.value,
                    "superseded_by_memory_id": new_mid,
                },
            )

        res = self.storage.transaction(ctx, fn)
        if is_err(res):
            return res
        return Ok(
            MemoryRef(
                id=new_mid,
                kind=new_payload["kind"],
                content_excerpt=new_payload["content"][:120],
                namespace=new_payload["namespace"],
                lifecycle_status=MemoryStatus.ACTIVE.value,
            )
        )

    # ---------------------------------------------------------------- 过期

    def expire_sweep(
        self, ctx: CallContext, scope: str | None = None, now: str | None = None
    ) -> Result[SweepOutcome]:
        """扫描 `expires_at < now` 的 ACTIVE 记忆置 `EXPIRED`，并同事务失效相关派生产物。"""
        now = now or to_iso(utc_now())
        loaded = self._load_memories(ctx, include_deleted=True)
        if is_err(loaded):
            return loaded
        expired_ids: list[str] = []
        for m in loaded.value:
            if m.get("lifecycle_status") != MemoryStatus.ACTIVE.value:
                continue
            if scope and m.get("scope") != scope:
                continue
            exp = m.get("expires_at")
            if exp and exp < now:
                expired_ids.append(m["id"])
        if not expired_ids:
            return Ok(SweepOutcome(expired_ids=[], invalidated_artifact_ids=[]))

        def fn() -> Result[list[str]]:
            for mid in expired_ids:
                u = self.storage.update(
                    ctx, "MemoryItem", mid, {"lifecycle_status": MemoryStatus.EXPIRED.value}
                )
                if is_err(u):
                    return u
            inv = self._invalidate_artifacts_for(ctx, expired_ids)
            if is_err(inv):
                return inv
            return Ok(inv.value)

        res = self.storage.transaction(ctx, fn)
        if is_err(res):
            return res
        return Ok(SweepOutcome(expired_ids=expired_ids, invalidated_artifact_ids=res.value))

    # ---------------------------------------------------------------- 归档

    def archive(self, ctx: CallContext, target: list[str] | str) -> Result[ArchiveOutcome]:
        """置 `ARCHIVED`（默认不出现在检索结果）。target 可为 id 列表或 scope 字符串。"""
        ids = self._resolve_ids(ctx, target)
        if is_err(ids):
            return ids

        def fn() -> Result[list[str]]:
            archived: list[str] = []
            for mid in ids.value:
                u = self.storage.update(
                    ctx, "MemoryItem", mid, {"lifecycle_status": MemoryStatus.ARCHIVED.value}
                )
                if is_err(u):
                    return u
                archived.append(mid)
            return Ok(archived)

        res = self.storage.transaction(ctx, fn)
        if is_err(res):
            return res
        return Ok(ArchiveOutcome(archived_ids=res.value))

    # ---------------------------------------------------------------- 删除

    def delete(self, ctx: CallContext, target: DeleteTarget) -> Result[DeleteOutcome]:
        """软删除 + 同事务失效派生产物（D-051）。部分失败整体回滚（D-025 永不物理删除）。"""
        ids = self._resolve_delete_ids(ctx, target)
        if is_err(ids):
            return ids
        resolved = ids.value
        scheduled = self._purge_date()

        def fn() -> Result[tuple[list[str], list[str]]]:
            deleted: list[str] = []
            for mid in resolved:
                u = self.storage.update(
                    ctx,
                    "MemoryItem",
                    mid,
                    {
                        "lifecycle_status": MemoryStatus.DELETED.value,
                        "purge_state": PurgeState.PENDING_PURGE.value,
                    },
                )
                if is_err(u):
                    return u
                aud = self.storage.append_audit(ctx, "INFO", "memory.soft_delete", detail=mid)
                if is_err(aud):
                    return aud
                deleted.append(mid)
            inv = self._invalidate_artifacts_for(ctx, resolved)
            if is_err(inv):
                return inv
            return Ok((deleted, inv.value))

        res = self.storage.transaction(ctx, fn)
        if is_err(res):
            return res
        deleted, invalidated = res.value
        return Ok(
            DeleteOutcome(
                deleted_ids=deleted,
                invalidated_artifact_ids=invalidated,
                scheduled_purge_at=scheduled,
            )
        )

    # ---------------------------------------------------------------- 派生产物

    def consolidate(
        self,
        ctx: CallContext,
        scope: str | None = None,
        session_id: str | None = None,
    ) -> Result[DerivedArtifactRef]:
        """把 ACTIVE 记忆压缩为 `DerivedArtifact(kind=SUMMARY)`（只基于 ACTIVE，不继承旧摘要文本）。

        首版采用确定性文本压缩（D-042：不接入模型/Provider），未来可替换为 Provider 摘要。
        """
        if session_id:
            scope = f"{MemoryScope.SESSION.value}:{session_id}"
        loaded = self._load_memories(ctx, include_deleted=True)
        if is_err(loaded):
            return loaded
        active = [
            m
            for m in loaded.value
            if m.get("lifecycle_status") == MemoryStatus.ACTIVE.value
            and (scope is None or m.get("scope") == scope)
        ]
        if not active:
            return Ok(DerivedArtifactRef(artifact_id=None, source_memory_ids=[], fresh=False))

        src_ids = sorted(m["id"] for m in active)
        content = self._build_summary(active)
        signature = self._signature(src_ids, content)

        existing = self._find_fresh_artifact(ctx, scope, signature)
        if existing is not None:
            return Ok(
                DerivedArtifactRef(artifact_id=existing["id"], source_memory_ids=src_ids, fresh=True)
            )

        art_id = new_id("DerivedArtifact")
        payload = {
            "kind": DerivedKind.SUMMARY.value,
            "scope": scope or "",
            "source_memory_ids": src_ids,
            "content_ref": f"derived:{art_id}",
            "builder": "memory.consolidate/v1",
            "content_signature": signature,
            "status": DerivedStatus.FRESH.value,
            "invalidated_reason": None,
            "invalidated_at": None,
            "rebuilt_from_id": None,
        }
        res = self.storage.put(
            ctx,
            "DerivedArtifact",
            payload,
            project_id=ctx.project_id,
            namespace=_DERIVED_NAMESPACE,
            created_by=ctx.actor,
            id=art_id,
        )
        if is_err(res):
            return res
        return Ok(DerivedArtifactRef(artifact_id=art_id, source_memory_ids=src_ids, fresh=True))

    def rebuild_derived(self, ctx: CallContext, artifact_id: str) -> Result[DerivedArtifactRef]:
        """基于当前 ACTIVE 记忆重建派生产物；旧摘要文本不作为新摘要输入（防借尸还魂）。"""
        got = self.storage.get(ctx, "DerivedArtifact", artifact_id, include_deleted=True)
        if is_err(got):
            return got
        if got.value is None:
            return Err(
                new_error(
                    ErrorCode.NOT_FOUND_ENTITY,
                    f"DerivedArtifact {artifact_id} 不存在",
                    trace_ref=ctx.trace_ref,
                )
            )
        artifact = got.value
        src_ids = list(artifact.get("source_memory_ids") or [])

        for sid in src_ids:
            m = self._get_memory(ctx, sid)
            if is_err(m):
                return m  # 来源记忆已物理缺失
            if m.value.get("lifecycle_status") != MemoryStatus.ACTIVE.value:
                # DA-V1：来源不再全部 ACTIVE → 失效，不重建
                now = to_iso(utc_now())
                u = self.storage.update(
                    ctx,
                    "DerivedArtifact",
                    artifact_id,
                    {
                        "status": DerivedStatus.INVALIDATED.value,
                        "invalidated_reason": "source memory no longer active",
                        "invalidated_at": now,
                    },
                )
                if is_err(u):
                    return u
                return Ok(DerivedArtifactRef(artifact_id=artifact_id, source_memory_ids=src_ids, fresh=False))

        loaded = self._load_memories(ctx, include_deleted=True)
        if is_err(loaded):
            return loaded
        active = [m for m in loaded.value if m["id"] in set(src_ids)]
        content = self._build_summary(active)
        signature = self._signature(src_ids, content)
        now = to_iso(utc_now())
        u = self.storage.update(
            ctx,
            "DerivedArtifact",
            artifact_id,
            {
                "content_ref": f"derived:{artifact_id}",
                "content_signature": signature,
                "status": DerivedStatus.FRESH.value,
                "invalidated_reason": None,
                "invalidated_at": None,
                "rebuilt_from_id": artifact_id,
            },
        )
        if is_err(u):
            return u
        return Ok(DerivedArtifactRef(artifact_id=artifact_id, source_memory_ids=src_ids, fresh=True))

    def invalidate_derived(self, ctx: CallContext, ids: list[str]) -> Result[None]:
        """显式失效指定派生产物（供 delete/expire 内部与评测断言使用）。"""
        now = to_iso(utc_now())

        def fn() -> Result[None]:
            for aid in ids:
                u = self.storage.update(
                    ctx,
                    "DerivedArtifact",
                    aid,
                    {
                        "status": DerivedStatus.INVALIDATED.value,
                        "invalidated_reason": "explicit",
                        "invalidated_at": now,
                    },
                )
                if is_err(u):
                    return u
            return Ok(None)

        return self.storage.transaction(ctx, fn)

    # ---------------------------------------------------------------- 清理

    def clear_simulated(self, ctx: CallContext, scope: str | None = None) -> Result[int]:
        """清空某范围内的模拟记忆（CLI `--clear-simulated`）。复用 delete 逻辑以保证派生失效一致。"""
        res = self.delete(
            ctx,
            DeleteTarget(namespace=ProvenanceNamespace.SIMULATED.value, scope=scope),
        )
        if is_err(res):
            return res
        return Ok(len(res.value.deleted_ids))

    def purge_expired(self, ctx: CallContext, now: str | None = None) -> Result[int]:
        """宽限期满后将 `PENDING_PURGE` 记忆置 `PURGED`（逻辑不可达）。

        边界说明（用户目标 7）：本阶段**不物理删除记录行**（D-025 永不物理删除，
        且 storage 层 `soft_delete` 也无物理删除），因此 purge 仅标记 `PURGED` 并使其
        在所有读取路径中不可达；`content_ref` 外部 blob 在 v1 不存在（内容存于 payload），
        故"物理删除 content_ref"为 N/A。完整物理清理列为未来项，不在本阶段宣称。
        """
        now = now or to_iso(utc_now())
        cutoff = self._purge_cutoff(now)
        loaded = self._load_memories(ctx, include_deleted=True)
        if is_err(loaded):
            return loaded
        to_purge = [
            m["id"]
            for m in loaded.value
            if m.get("purge_state") == PurgeState.PENDING_PURGE.value
            and (m.get("deleted_at") or "") <= cutoff
        ]

        def fn() -> Result[int]:
            for mid in to_purge:
                u = self.storage.update(
                    ctx, "MemoryItem", mid, {"purge_state": PurgeState.PURGED.value}
                )
                if is_err(u):
                    return u
            return Ok(len(to_purge))

        return self.storage.transaction(ctx, fn)

    # ================================================================ 内部

    def _build_payload(
        self,
        d: dict,
        mid: str,
        *,
        forced_namespace: str | None = None,
        forced_source_type: str | None = None,
        superseded_by: str | None = None,
    ) -> dict:
        namespace = forced_namespace or d.get("namespace") or _default_namespace(d["source_type"])
        source_type = forced_source_type or d["source_type"]
        return {
            "scope": d["scope"],
            "scope_id": d["scope_id"],
            "kind": d["kind"],
            "namespace": namespace,
            "content": d["content"],
            "related_entity_ids": list(d.get("related_entity_ids") or []),
            "relationship_profile_id": d.get("relationship_profile_id"),
            "source_type": source_type,
            "source_turn_id": d.get("source_turn_id"),
            "referenced_evidence_ids": list(d.get("referenced_evidence_ids") or []),
            "confidence": float(d["confidence"]),
            "confidence_means": d["confidence_means"],
            "uncertainty_note": d.get("uncertainty_note"),
            "salience": float(d.get("salience", 0.5)),
            "access_count": 0,
            "last_accessed_at": None,
            "valid_from": d.get("valid_from"),
            "expires_at": d.get("expires_at"),
            "lifecycle_status": d.get("lifecycle_status") or MemoryStatus.ACTIVE.value,
            "duplicate_of_memory_id": d.get("duplicate_of_memory_id"),
            "conflict_with_memory_ids": list(d.get("conflict_with_memory_ids") or []),
            "superseded_by_memory_id": superseded_by,
            "purge_state": PurgeState.NONE.value,
            "purge_audit_id": None,
        }

    def _validate_draft(self, d: dict, ctx: CallContext) -> Result[None]:
        for f in (
            "scope",
            "scope_id",
            "kind",
            "content",
            "source_type",
            "confidence",
            "confidence_means",
            "salience",
        ):
            if f not in d or d[f] in (None, ""):
                return Err(
                    new_error(
                        ErrorCode.VALIDATION_REQUIRED_FIELD,
                        f"记忆缺少必填字段：{f}",
                        trace_ref=ctx.trace_ref,
                    )
                )
        try:
            MemoryScope(d["scope"])
        except ValueError:
            return Err(
                new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER, f"未知 scope：{d['scope']!r}",
                          trace_ref=ctx.trace_ref)
            )
        try:
            MemoryKind(d["kind"])
        except ValueError:
            return Err(
                new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER, f"未知 kind：{d['kind']!r}",
                          trace_ref=ctx.trace_ref)
            )
        try:
            MemorySourceType(d["source_type"])
        except ValueError:
            return Err(
                new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER, f"未知 source_type：{d['source_type']!r}",
                          trace_ref=ctx.trace_ref)
            )
        if not str(d["confidence_means"]).strip():
            return Err(
                new_error(
                    ErrorCode.VALIDATION_CONFIDENCE_MEANS_MISSING,
                    "记忆必须说明 confidence 的含义（防止被当成真实性）",
                    trace_ref=ctx.trace_ref,
                )
            )
        ns = d.get("namespace") or _default_namespace(d["source_type"])
        if (
            d["source_type"] == MemorySourceType.SIMULATED_DIALOGUE.value
            and ns != ProvenanceNamespace.SIMULATED.value
        ):
            return Err(
                new_error(
                    ErrorCode.VALIDATION_NAMESPACE_MISMATCH,
                    "source_type=SIMULATED_DIALOGUE 必须 namespace=SIMULATED",
                    trace_ref=ctx.trace_ref,
                )
            )
        if d["source_type"] == MemorySourceType.SIMULATED_DIALOGUE.value and not d.get("source_turn_id"):
            return Err(
                new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "source_type=SIMULATED_DIALOGUE 必须提供 source_turn_id",
                    trace_ref=ctx.trace_ref,
                )
            )
        for eid in d.get("referenced_evidence_ids") or []:
            r = self.storage.get(ctx, "EvidenceItem", eid)
            if is_err(r):
                return r
            if r.value is None:
                return Err(
                    new_error(
                        ErrorCode.VALIDATION_REFERENCE_MISSING,
                        f"记忆引用的证据不存在：{eid}",
                        trace_ref=ctx.trace_ref,
                    )
                )
        return Ok(None)

    def _find_duplicate(self, ctx: CallContext, d: dict) -> dict | None:
        ns = d.get("namespace") or _default_namespace(d["source_type"])
        loaded = self._load_memories(
            ctx, include_deleted=True, namespace=ns
        )
        if is_err(loaded):
            return None
        for m in loaded.value:
            if (
                m.get("lifecycle_status") == MemoryStatus.ACTIVE.value
                and m.get("scope") == d["scope"]
                and m.get("scope_id") == d["scope_id"]
                and m.get("content") == d["content"]
            ):
                return m
        return None

    def _get_memory(self, ctx: CallContext, memory_id: str) -> Result[dict]:
        r = self.storage.get(ctx, "MemoryItem", memory_id, include_deleted=True)
        if is_err(r):
            return r
        if r.value is None:
            return Err(
                new_error(
                    ErrorCode.NOT_FOUND_ENTITY,
                    f"MemoryItem {memory_id} 不存在",
                    trace_ref=ctx.trace_ref,
                )
            )
        return Ok(self._as_memory(r.value))

    @staticmethod
    def _as_memory(row: dict) -> dict:
        """还原被记录表 `kind` 列遮蔽的 MemoryKind（存储层字段名冲突的实现细节）。"""
        payload = row.get("payload") or {}
        norm = dict(row)
        norm["kind"] = payload.get("kind", row.get("kind"))
        return norm

    def _load_memories(
        self, ctx: CallContext, *, include_deleted: bool = True, namespace: str | None = None
    ) -> Result[list[dict]]:
        filters: dict = {"kind": "MemoryItem", "project_id": ctx.project_id}
        if namespace:
            filters["namespace"] = namespace
        q = Query(filters=filters, limit=MAX_QUERY_LIMIT, sort=("created_at", "asc"))
        res = self.storage.query(ctx, "MemoryItem", q, include_deleted=include_deleted)
        if is_err(res):
            return res
        # 记录表自身的 `kind` 列（="MemoryItem"）会遮蔽载荷里的 MemoryKind；
        # 这里把语义 `kind` 还原回来（INV：字段名冲突的存储层实现细节）。
        return Ok([self._as_memory(r) for r in res.value.items])

    def _load_artifacts(
        self, ctx: CallContext, *, include_deleted: bool = True
    ) -> Result[list[dict]]:
        q = Query(
            filters={"kind": "DerivedArtifact", "project_id": ctx.project_id},
            limit=MAX_QUERY_LIMIT,
            sort=("created_at", "asc"),
        )
        res = self.storage.query(ctx, "DerivedArtifact", q, include_deleted=include_deleted)
        if is_err(res):
            return res
        return Ok(res.value.items)

    def _invalidate_artifacts_for(
        self, ctx: CallContext, memory_ids: list[str]
    ) -> Result[list[str]]:
        """找出 source_memory_ids 含任一 memory_id 的派生产物 → 同事务 INVALIDATED。"""
        arts = self._load_artifacts(ctx, include_deleted=True)
        if is_err(arts):
            return arts
        now = to_iso(utc_now())
        invalidated: list[str] = []
        target = set(memory_ids)

        def fn() -> Result[list[str]]:
            local_invalidated: list[str] = []
            for a in arts.value:
                srcs = set(a.get("source_memory_ids") or [])
                if srcs & target:
                    u = self.storage.update(
                        ctx,
                        "DerivedArtifact",
                        a["id"],
                        {
                            "status": DerivedStatus.INVALIDATED.value,
                            "invalidated_reason": "source memory deleted/expired",
                            "invalidated_at": now,
                        },
                    )
                    if is_err(u):
                        return u
                    local_invalidated.append(a["id"])
            return Ok(local_invalidated)

        res = self.storage.transaction(ctx, fn)
        if is_err(res):
            return res
        invalidated = res.value
        return Ok(invalidated)

    def _resolve_ids(self, ctx: CallContext, target: list[str] | str) -> Result[list[str]]:
        if isinstance(target, (list, tuple)):
            return Ok(list(target))
        if isinstance(target, str):
            loaded = self._load_memories(ctx, include_deleted=True)
            if is_err(loaded):
                return loaded
            ids = [
                m["id"]
                for m in loaded.value
                if m.get("scope") == target
                and m.get("lifecycle_status") != MemoryStatus.DELETED.value
            ]
            return Ok(ids)
        return Err(
            new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "archive 目标必须是 id 列表或 scope 字符串",
                trace_ref=ctx.trace_ref,
            )
        )

    def _resolve_delete_ids(self, ctx: CallContext, target: DeleteTarget) -> Result[list[str]]:
        if target.id:
            return Ok([target.id])
        loaded = self._load_memories(ctx, include_deleted=True)
        if is_err(loaded):
            return loaded
        if target.scope:
            ids = [
                m["id"]
                for m in loaded.value
                if m.get("scope") == target.scope
                and m.get("lifecycle_status") != MemoryStatus.DELETED.value
            ]
            return Ok(ids)
        if target.namespace:
            ids = [
                m["id"]
                for m in loaded.value
                if m.get("namespace") == target.namespace
                and m.get("lifecycle_status") != MemoryStatus.DELETED.value
            ]
            return Ok(ids)
        return Err(
            new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "DeleteTarget 必须指定 id / scope / namespace",
                trace_ref=ctx.trace_ref,
            )
        )

    def _record_access(self, ctx: CallContext, ids: list[str]) -> None:
        """读路径副作用：更新命中记忆的 access_count / last_accessed_at（最佳努力，失败不阻断检索）。"""
        if not ids:
            return
        now = to_iso(utc_now())

        def fn() -> Result[None]:
            for mid in ids:
                cur = self.storage.get(ctx, "MemoryItem", mid, include_deleted=True)
                if is_err(cur) or cur.value is None:
                    continue
                cnt = int(cur.value.get("access_count") or 0) + 1
                self.storage.update(
                    ctx, "MemoryItem", mid, {"access_count": cnt, "last_accessed_at": now}
                )
            return Ok(None)

        self.storage.transaction(ctx, fn)

    def _matches(self, text: str, content: str) -> bool:
        terms = [t for t in str(text).split() if t]
        if not terms:
            return True
        cl = str(content).lower()
        return all(t.lower() in cl for t in terms)

    def _score(self, text: str | None, m: dict) -> float:
        salience = float(m.get("salience") or 0.0)
        base = 1.0
        if text and str(text).strip():
            terms = [t for t in str(text).split() if t]
            if terms:
                cl = str(m.get("content", "")).lower()
                matched = sum(1 for t in terms if t.lower() in cl)
                base = matched / len(terms)
        recency = self._recency_factor(m.get("created_at"))
        return base * (0.5 + 0.5 * salience) * recency

    @staticmethod
    def _recency_factor(created_at: str | None) -> float:
        if not created_at:
            return 1.0
        try:
            age_days = (utc_now() - from_iso(created_at)).total_seconds() / 86400.0
        except (ValueError, TypeError):
            return 1.0
        return 1.0 / (1.0 + max(age_days, 0.0) / 30.0)

    @staticmethod
    def _to_view(m: dict) -> MemoryView:
        return MemoryView(
            id=m["id"],
            scope=m.get("scope", ""),
            scope_id=m.get("scope_id", ""),
            kind=m.get("kind", ""),
            content=m.get("content", ""),
            related_entity_ids=list(m.get("related_entity_ids") or []),
            namespace=m.get("namespace", ""),
            source_type=m.get("source_type", ""),
            source_turn_id=m.get("source_turn_id"),
            confidence=float(m.get("confidence") or 0.0),
            confidence_means=m.get("confidence_means", ""),
            salience=float(m.get("salience") or 0.0),
            lifecycle_status=m.get("lifecycle_status", ""),
            valid_from=m.get("valid_from"),
            expires_at=m.get("expires_at"),
        )

    @staticmethod
    def _build_summary(active: list[dict]) -> str:
        lines = [f"{i + 1}. {m.get('content', '')}" for i, m in enumerate(active)]
        return "\n".join(lines)

    @staticmethod
    def _signature(src_ids: list[str], content: str) -> str:
        raw = "|".join(src_ids) + "::" + content
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def _find_fresh_artifact(
        self, ctx: CallContext, scope: str | None, signature: str
    ) -> dict | None:
        arts = self._load_artifacts(ctx, include_deleted=True)
        if is_err(arts):
            return None
        for a in arts.value:
            if (
                a.get("status") == DerivedStatus.FRESH.value
                and a.get("scope") == (scope or "")
                and a.get("content_signature") == signature
            ):
                return a
        return None

    @staticmethod
    def _purge_date() -> str:
        return to_iso(utc_now() + timedelta(days=PURGE_GRACE_DAYS))

    @staticmethod
    def _purge_cutoff(now: str) -> str:
        try:
            base = from_iso(now)
        except (ValueError, TypeError):
            base = utc_now()
        return to_iso(base - timedelta(days=PURGE_GRACE_DAYS))
