"""SQLite 存储实现 —— 对应 docs/INTERFACES.md §3。

职责：事务、CRUD、软删除、引用完整性检查、审计日志、用量表、检查点读写。
**非职责**（§3.1）：不做任何业务判断（不判断证据级别、不判断冲突、不排序打分）。

给初学者的解释：
    这一层就像"仓库管理员"。它只管东西存进去、取出来、记得谁什么时候动过。
    它**不判断**"这句话是不是真的"——那是 Evidence 模块的活。

关于命名空间闸门：本骨架把「namespace 是否允许写入该实体」当作**结构性约束**
（类似数据库的 CHECK 约束）在 `put` 里拦截，属于"引用完整性检查"范畴，
**不是**业务判断。真正的模块级闸门仍会由各模块接口调用（P2 起实现）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..core.context import CallContext
from ..core.enums import ProvenanceNamespace, namespace_allowed
from ..core.errors import ErrorCode, new_error
from ..core.ids import new_id
from ..core.result import Err, Ok, Result, UsageDelta
from ..core.temporal import utc_now, to_iso
from ..core.types import (
    COST_UNKNOWN,
    MAX_QUERY_LIMIT,
    OUTCOME_FAILED,
    BudgetUsage,
    Checkpoint,
    ExternalUsageRecord,
    ExternalUsageTotals,
    Page,
    Query,
    ReferenceReport,
    UsageReport,
)
from .schema import DDL_STATEMENTS, FILTER_KEYS, RECORD_KINDS, SCHEMA_VERSION, SORT_KEYS


def budget_usage_from_dict(data: Mapping[str, Any]) -> BudgetUsage:
    """从检查点 JSON 还原 `BudgetUsage`（未知量按 0 处理，金额不猜）。"""
    return BudgetUsage(
        requests=int(data.get("requests", 0) or 0),
        tokens_in=int(data.get("tokens_in", 0) or 0),
        tokens_out=int(data.get("tokens_out", 0) or 0),
        latency_ms=int(data.get("latency_ms", 0) or 0),
        cost_estimate=data.get("cost_estimate"),
        currency=data.get("currency", "UNKNOWN"),
    )


def _json_default(value: Any) -> Any:
    """JSON 序列化兜底：枚举取 `.value`，datetime 转 ISO，其余交给调用方保证。"""
    if isinstance(value, ProvenanceNamespace) or hasattr(value, "value"):
        return value.value
    if hasattr(value, "isoformat"):
        return to_iso(value) if value.tzinfo else value.isoformat()
    raise TypeError(f"无法序列化的类型：{type(value)!r}")


def _to_payload(data: Mapping[str, Any] | Any) -> dict[str, Any]:
    """把 dataclass 或字典统一转成普通字典。"""
    if is_dataclass(data) and not isinstance(data, type):
        return asdict(data)
    return dict(data)


class SqliteStorage:
    """最小 SQLite 仓储。所有方法返回 `Result[...]`，**不抛业务异常**（EH-1）。"""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn: sqlite3.Connection | None = None
        self._tx_depth = 0

    # ---------------------------------------------------------------- 连接

    def connect(self) -> sqlite3.Connection:
        """建立（或复用）连接。WAL 模式：读可并发，写串行。"""
        if self._conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def init_schema(self, ctx: CallContext) -> Result[None]:
        """建表并写入 `schema_version`。可重复调用（幂等）。"""
        if ctx.dry_run:
            return Ok(None)
        try:
            conn = self.connect()
            for ddl in DDL_STATEMENTS:
                conn.execute(ddl)
            conn.execute(
                "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(SCHEMA_VERSION),),
            )
        except sqlite3.Error as exc:
            return Err(
                new_error(ErrorCode.STORAGE_WRITE_FAILED, f"建表失败：{exc}",
                          trace_ref=ctx.trace_ref)
            )
        return Ok(None)

    def schema_version(self, ctx: CallContext) -> Result[int]:
        try:
            row = self.connect().execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, str(exc), trace_ref=ctx.trace_ref))
        return Ok(int(row["value"]) if row else 0)

    # ---------------------------------------------------------------- 事务

    def transaction(self, ctx: CallContext, fn: Callable[[], Result[Any]]) -> Result[Any]:
        """在一个事务里执行 `fn`。`fn` 返回 `Err` 或抛 `sqlite3.Error` → 整体回滚。

        嵌套调用时只在最外层开事务（避免 SQLite 报错）。
        """
        conn = self.connect()
        outermost = self._tx_depth == 0
        if outermost:
            conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
        try:
            outcome = fn()
            if isinstance(outcome, Err):
                if outermost:
                    conn.execute("ROLLBACK")
                return outcome
            if outermost:
                conn.execute("COMMIT")
            return outcome
        except sqlite3.Error as exc:
            if outermost:
                conn.execute("ROLLBACK")
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"事务失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        finally:
            if outermost:
                self._tx_depth = 0

    # ---------------------------------------------------------------- 写入

    def put(
        self,
        ctx: CallContext,
        kind: str,
        payload: Mapping[str, Any] | Any,
        *,
        project_id: str | None = None,
        namespace: ProvenanceNamespace | str | None = None,
        created_by: str | None = None,
        id: str | None = None,
    ) -> Result[str]:
        """写入一条记录，返回新 ID。

        强制校验（结构性约束）：
            1. `kind` 必须是已知的实体种类；
            2. `namespace` 必须通过该实体的写入闸门（例：EvidenceItem 不许 SIMULATED，D-023）。
        """
        if kind not in RECORD_KINDS:
            return Err(
                new_error(ErrorCode.VALIDATION_UNKNOWN_KIND, f"未知实体种类：{kind!r}",
                          trace_ref=ctx.trace_ref)
            )

        body = _to_payload(payload)
        ns = namespace if namespace is not None else body.pop("namespace", None)
        if ns is None:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                          "缺少必填字段 namespace（D-021：每条记录都要标明来自哪个世界）",
                          trace_ref=ctx.trace_ref)
            )
        if not namespace_allowed(kind, ns):
            return Err(
                new_error(ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN,
                          f"{kind} 不允许写入 namespace={ns}",
                          trace_ref=ctx.trace_ref)
            )

        pid = project_id if project_id is not None else body.pop("project_id", None)
        if not pid:
            return Err(
                new_error(ErrorCode.VALIDATION_REQUIRED_FIELD, "缺少必填字段 project_id",
                          trace_ref=ctx.trace_ref)
            )

        record_id = id or new_id(kind)
        now = to_iso(utc_now())
        row = (
            record_id,
            kind,
            pid,
            str(getattr(ns, "value", ns)),
            json.dumps(body, ensure_ascii=False, default=_json_default),
            created_by or ctx.actor,
            now,
            now,
            None,
            1,
            ctx.trace_ref,
            SCHEMA_VERSION,
        )
        if ctx.dry_run:
            return Ok(record_id)
        try:
            self.connect().execute(
                "INSERT INTO records (id, kind, project_id, namespace, payload, created_by,"
                " created_at, updated_at, deleted_at, rev, trace_ref, schema_version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"写入失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok(record_id)

    def update(
        self,
        ctx: CallContext,
        kind: str,
        id: str,
        patch: Mapping[str, Any],
        *,
        expected_rev: int | None = None,
    ) -> Result[dict[str, Any]]:
        """局部更新记录的 payload。带乐观锁：`expected_rev` 不匹配 → `STORAGE.conflict`。"""
        current = self.get(ctx, kind, id)
        if isinstance(current, Err):
            return current
        if current.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY, f"{kind} {id} 不存在",
                                 trace_ref=ctx.trace_ref))

        row_rev = int(current.value["rev"])
        if expected_rev is not None and expected_rev != row_rev:
            return Err(new_error(ErrorCode.STORAGE_CONFLICT,
                                 f"乐观锁冲突：期望 rev={expected_rev}，实际 rev={row_rev}",
                                 trace_ref=ctx.trace_ref))

        merged = {**current.value["payload"], **dict(patch)}
        now = to_iso(utc_now())
        if ctx.dry_run:
            return Ok({**current.value, "payload": merged, "rev": row_rev + 1, "updated_at": now})
        try:
            conn = self.connect()
            cur = conn.execute(
                "UPDATE records SET payload = ?, updated_at = ?, rev = rev + 1"
                " WHERE id = ? AND kind = ? AND deleted_at IS NULL",
                (json.dumps(merged, ensure_ascii=False, default=_json_default), now, id, kind),
            )
            if cur.rowcount == 0:
                return Err(new_error(ErrorCode.NOT_FOUND_ENTITY, f"{kind} {id} 不存在或已删除",
                                     trace_ref=ctx.trace_ref))
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"更新失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok({**current.value, "payload": merged, "rev": row_rev + 1, "updated_at": now})

    def soft_delete(self, ctx: CallContext, kind: str, id: str) -> Result[None]:
        """软删除：只打 `deleted_at`，**永不物理删除**（D-025）。"""
        if ctx.dry_run:
            return Ok(None)
        try:
            cur = self.connect().execute(
                "UPDATE records SET deleted_at = ?, updated_at = ?"
                " WHERE id = ? AND kind = ? AND deleted_at IS NULL",
                (to_iso(utc_now()), to_iso(utc_now()), id, kind),
            )
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"软删除失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        if cur.rowcount == 0:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY, f"{kind} {id} 不存在或已删除",
                                 trace_ref=ctx.trace_ref))
        return self.append_audit(ctx, "INFO", f"soft_delete:{kind}", detail=id)

    # ---------------------------------------------------------------- 读取

    def get(
        self,
        ctx: CallContext,
        kind: str,
        id: str,
        *,
        include_deleted: bool = False,
    ) -> Result[dict[str, Any] | None]:
        """按 ID 读取。`Ok(None)` 表示**没找到**，这不是错误。"""
        sql = "SELECT * FROM records WHERE id = ? AND kind = ?"
        params: list[Any] = [id, kind]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        try:
            row = self.connect().execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, f"读取失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok(self._row_to_dict(row) if row else None)

    def query(
        self,
        ctx: CallContext,
        kind: str,
        q: Query,
        *,
        include_deleted: bool = False,
    ) -> Result[Page[dict[str, Any]]]:
        """按条件查询。过滤键/排序键必须在白名单内，否则直接拒绝。"""
        if q.limit > MAX_QUERY_LIMIT:
            return Err(new_error(ErrorCode.VALIDATION_LIMIT_TOO_LARGE,
                                 f"limit 不得超过 {MAX_QUERY_LIMIT}", trace_ref=ctx.trace_ref))
        unknown = set(q.filters) - FILTER_KEYS
        if unknown:
            return Err(new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER,
                                 f"未登记的过滤键：{sorted(unknown)}", trace_ref=ctx.trace_ref))

        where = ["kind = ?"]
        params: list[Any] = [kind]
        if not include_deleted:
            where.append("deleted_at IS NULL")
        for key in sorted(q.filters):
            where.append(f"{key} = ?")
            params.append(q.filters[key])
        where_sql = " AND ".join(where)

        total_sql = f"SELECT COUNT(*) AS c FROM records WHERE {where_sql}"
        order_sql = "created_at ASC"
        if q.sort:
            column, direction = q.sort
            if column not in SORT_KEYS:
                return Err(new_error(ErrorCode.VALIDATION_UNKNOWN_FILTER,
                                     f"不允许按 {column!r} 排序", trace_ref=ctx.trace_ref))
            order_sql = f"{column} {'DESC' if str(direction).lower() == 'desc' else 'ASC'}"

        try:
            conn = self.connect()
            total = int(conn.execute(total_sql, params).fetchone()["c"])
            rows = conn.execute(
                f"SELECT * FROM records WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
                [*params, q.limit, q.offset],
            ).fetchall()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, f"查询失败：{exc}",
                                 trace_ref=ctx.trace_ref))

        items = [self._row_to_dict(r) for r in rows]
        return Ok(Page(items=items, total=total, has_more=(q.offset + len(items)) < total))

    def check_references(
        self,
        ctx: CallContext,
        kind: str | None,
        ids: list[str],
    ) -> Result[ReferenceReport]:
        """检查一批 ID 是否都还在（未被删除）。`kind=None` 表示不限种类。"""
        missing: list[str] = []
        try:
            conn = self.connect()
            for rid in ids:
                sql = "SELECT 1 FROM records WHERE id = ? AND deleted_at IS NULL"
                params: list[Any] = [rid]
                if kind:
                    sql += " AND kind = ?"
                    params.append(kind)
                if conn.execute(sql, params).fetchone() is None:
                    missing.append(rid)
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, f"引用检查失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok(ReferenceReport(missing=missing))

    # ---------------------------------------------------------------- 检查点

    def save_checkpoint(self, ctx: CallContext, task_id: str, cp: Checkpoint) -> Result[str]:
        """写入（覆盖）检查点，返回 `resume_token`。同一 token 覆盖即幂等。"""
        if not cp.resume_token:
            cp.resume_token = f"tok_{task_id}_{utc_now().strftime('%Y%m%d%H%M%S')}"
        if not cp.saved_at:
            cp.saved_at = to_iso(utc_now())
        payload = {
            "task_id": cp.task_id,
            "stage": cp.stage,
            "cursor": cp.cursor,
            "completed_units": cp.completed_units,
            "artifact_ids": cp.artifact_ids,
            "budget_used": {
                "requests": cp.budget_used.requests,
                "tokens_in": cp.budget_used.tokens_in,
                "tokens_out": cp.budget_used.tokens_out,
                "latency_ms": cp.budget_used.latency_ms,
            },
            "resume_token": cp.resume_token,
            "saved_at": cp.saved_at,
        }
        if ctx.dry_run:
            return Ok(cp.resume_token)
        try:
            self.connect().execute(
                "INSERT INTO checkpoints (task_id, payload, resume_token, saved_at)"
                " VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET"
                " payload = excluded.payload, resume_token = excluded.resume_token,"
                " saved_at = excluded.saved_at",
                (task_id, json.dumps(payload, ensure_ascii=False), cp.resume_token, cp.saved_at),
            )
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"保存检查点失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok(cp.resume_token)

    def load_checkpoint(self, ctx: CallContext, task_id: str) -> Result[Checkpoint | None]:
        """读取检查点。`Ok(None)` 表示还没有检查点（不是错误，但 resume 会据此报错）。"""
        try:
            row = self.connect().execute(
                "SELECT payload FROM checkpoints WHERE task_id = ?", (task_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, f"读取检查点失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        if row is None:
            return Ok(None)
        data = json.loads(row["payload"])
        budget = data.get("budget_used", {})
        return Ok(
            Checkpoint(
                task_id=data.get("task_id", task_id),
                stage=data.get("stage", ""),
                cursor=data.get("cursor", ""),
                completed_units=list(data.get("completed_units", [])),
                artifact_ids=dict(data.get("artifact_ids", {})),
                budget_used=budget_usage_from_dict(budget),
                resume_token=data.get("resume_token", ""),
                saved_at=data.get("saved_at", ""),
            )
        )

    # ---------------------------------------------------------------- 用量 / 审计

    def record_usage(
        self,
        ctx: CallContext,
        scope: str,
        usage: UsageDelta,
        *,
        actor: str | None = None,
    ) -> Result[None]:
        """记一笔用量。token 未知时写 `NULL`，**绝不填估计值**（D-060）。"""
        if ctx.dry_run:
            return Ok(None)
        row = (
            new_id("UsageRecord"),
            scope,
            actor or ctx.actor,
            usage.requests,
            usage.tokens_in if usage.tokens_in is not None else None,
            usage.tokens_out if usage.tokens_out is not None else None,
            usage.latency_ms,
            usage.cost_estimate,
            usage.currency,
            ctx.trace_ref,
            to_iso(utc_now()),
        )
        try:
            self.connect().execute(
                "INSERT INTO usage_records (id, scope, actor, requests, tokens_in, tokens_out,"
                " latency_ms, cost_estimate, currency, trace_ref, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"记录用量失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok(None)

    def record_external_usage(
        self,
        ctx: CallContext,
        record: ExternalUsageRecord,
    ) -> Result[bool]:
        """记一笔**外部调用**的逐笔用量。**幂等**：主键冲突视为已记过，不重复计。

        返回 `Ok(True)` = 真的写入了；`Ok(False)` = 同一 `idempotency_key` 已存在，
        忽略（重试/任务恢复靠这个保证不重复计费——这是账本幂等性的**存储层兜底**，
        内存账本之外再上数据库主键，双保险）。
        """
        if ctx.dry_run:
            return Ok(False)
        try:
            self.connect().execute(
                "INSERT INTO external_usage_records (idempotency_key, provider, operation,"
                " outcome, requested_at, request_id, unit_count, success_unit_count,"
                " cost_estimate, cost_confirmed, currency, cost_source, cost_status,"
                " task_id, run_id, trace_ref)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                record.to_row(),
            )
        except sqlite3.IntegrityError:
            return Ok(False)          # 幂等键已存在 → 已记过，不再重复
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED,
                                 f"记录外部用量失败：{exc}", trace_ref=ctx.trace_ref))
        return Ok(True)

    def has_external_usage(
        self,
        ctx: CallContext,
        idempotency_key: str,
    ) -> Result[bool]:
        """跨轮幂等的**只读查询**：这个幂等键记过没有（主键查）。

        发送层守卫用它防止"任务恢复 / 重跑时重复抓取"——内存账本在重跑时
        是空的，只有持久账本记得上一轮抓过。查询出错由调用方按"保守不发送"
        处理（宁可不抓，不可重复花钱）。
        """
        if ctx.dry_run:
            return Ok(False)
        try:
            row = self.connect().execute(
                "SELECT 1 FROM external_usage_records"
                " WHERE idempotency_key = ? LIMIT 1",
                (idempotency_key,),
            ).fetchone()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED,
                                 f"查询外部用量失败：{exc}", trace_ref=ctx.trace_ref))
        return Ok(row is not None)

    def external_usage_report(
        self,
        ctx: CallContext,
        *,
        task_id: str | None = None,
    ) -> Result[ExternalUsageTotals]:
        """汇总外部用量：估算、实际、未知三者分开报。"""
        if task_id is not None:
            sql = ("SELECT * FROM external_usage_records WHERE task_id = ?")
            params: tuple = (task_id,)
        else:
            sql = "SELECT * FROM external_usage_records"
            params = ()
        try:
            rows = self.connect().execute(sql, params).fetchall()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED,
                                 f"汇总外部用量失败：{exc}", trace_ref=ctx.trace_ref))

        totals = ExternalUsageTotals()
        for r in rows:
            totals.requests += 1
            if r["outcome"] == OUTCOME_FAILED:
                totals.failed_requests += 1
            if r["cost_status"] == COST_UNKNOWN:
                totals.unknown_requests += 1
            if r["cost_confirmed"] is not None:
                totals.confirmed_cost = (totals.confirmed_cost or 0.0) + r["cost_confirmed"]
            if r["cost_estimate"] is not None:
                totals.estimated_cost += r["cost_estimate"]
            totals.by_operation[r["operation"]] = (
                totals.by_operation.get(r["operation"], 0) + 1)
        return Ok(totals)

    def external_usage_records(
        self,
        ctx: CallContext,
        *,
        task_id: str | None = None,
        cost_status: str | None = None,
    ) -> Result[list[ExternalUsageRecord]]:
        """列出外部用量记录（供人工核对）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if cost_status is not None:
            clauses.append("cost_status = ?")
            params.append(cost_status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        try:
            rows = self.connect().execute(
                f"SELECT * FROM external_usage_records{where} ORDER BY requested_at",
                tuple(params),
            ).fetchall()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED,
                                 f"读取外部用量失败：{exc}", trace_ref=ctx.trace_ref))
        return Ok([ExternalUsageRecord.from_row(r) for r in rows])

    def usage_report(self, ctx: CallContext, scope: str) -> Result[UsageReport]:
        """汇总某个 scope（task_id / project_id）的用量。"""
        try:
            rows = self.connect().execute(
                "SELECT actor, requests, tokens_in, tokens_out, latency_ms, cost_estimate, currency"
                " FROM usage_records WHERE scope = ?",
                (scope,),
            ).fetchall()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, f"汇总用量失败：{exc}",
                                 trace_ref=ctx.trace_ref))

        report = UsageReport()
        for r in rows:
            report.requests += int(r["requests"] or 0)
            report.tokens_in += int(r["tokens_in"] or 0)
            report.tokens_out += int(r["tokens_out"] or 0)
            report.latency_ms_total += int(r["latency_ms"] or 0)
            report.breakdown_by_actor[r["actor"]] = (
                report.breakdown_by_actor.get(r["actor"], 0) + int(r["requests"] or 0)
            )
        return Ok(report)

    def append_audit(
        self,
        ctx: CallContext,
        level: str,
        action: str,
        *,
        detail: Any = None,
    ) -> Result[str]:
        """追加一条审计日志（只增不改不删）。"""
        audit_id = new_id("AuditRecord")
        if ctx.dry_run:
            return Ok(audit_id)
        try:
            self.connect().execute(
                "INSERT INTO audit_records (id, level, actor, action, trace_ref, detail, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (
                    audit_id,
                    level,
                    ctx.actor,
                    action,
                    ctx.trace_ref,
                    None if detail is None else json.dumps(detail, ensure_ascii=False,
                                                           default=_json_default),
                    to_iso(utc_now()),
                ),
            )
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_WRITE_FAILED, f"写审计失败：{exc}",
                                 trace_ref=ctx.trace_ref))
        return Ok(audit_id)

    def audit_count(self, ctx: CallContext) -> Result[int]:
        """审计日志条数（仅供测试与自检使用）。"""
        try:
            row = self.connect().execute("SELECT COUNT(*) AS c FROM audit_records").fetchone()
        except sqlite3.Error as exc:
            return Err(new_error(ErrorCode.STORAGE_READ_FAILED, str(exc), trace_ref=ctx.trace_ref))
        return Ok(int(row["c"]) if row else 0)

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        payload = json.loads(row["payload"])
        return {
            **payload,
            "id": row["id"],
            "kind": row["kind"],
            "project_id": row["project_id"],
            "namespace": row["namespace"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"],
            "rev": row["rev"],
            "trace_ref": row["trace_ref"],
            "schema_version": row["schema_version"],
            "payload": payload,
        }
