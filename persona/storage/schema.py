"""SQLite 最小表结构 —— 对应 docs/INTERFACES.md §3.3。

⚠️ **这是 P1.2-B 的"骨架版"表结构，不是最终形态**：
本阶段只建 4 张表，用于验证"能不能连上、能不能读写"。
按 `DATA_MODEL.md` 拆成 13 张带完整列的表，属于 P2 之后的迁移工作
（`schema_version` 递增 + 迁移脚本，**禁止自动删表**）。

现在的做法：
    - `records`       通用记录表：13 个业务实体**暂以 JSON 载荷**存放，
                      通用字段（id / namespace / 创建者 / 软删 / 版本号）独立成列，便于统一查询与审计。
    - `usage_records` 每次外部调用的账本（D-060）
    - `audit_records` 审计日志（只增不改不删）
    - `checkpoints`   任务检查点（D-061 恢复用）

尚未实现（诚实列出）：FTS5 关键词索引（P4 记忆检索时才需要，见 Q-30）。
"""

from __future__ import annotations

from ..core.enums import ENTITY_KINDS

SCHEMA_VERSION = 1

DDL_STATEMENTS: tuple[str, ...] = (
    # 元数据：只存一行 schema_version
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    # 通用记录表
    """
    CREATE TABLE IF NOT EXISTS records (
        id             TEXT PRIMARY KEY,
        kind           TEXT NOT NULL,
        project_id     TEXT NOT NULL,
        namespace      TEXT NOT NULL,
        payload        TEXT NOT NULL,          -- 实体具体字段的 JSON
        created_by     TEXT NOT NULL,
        created_at     TEXT NOT NULL,          -- ISO-8601 UTC
        updated_at     TEXT NOT NULL,
        deleted_at     TEXT,                   -- 软删除（D-025：永不物理删除）
        rev            INTEGER NOT NULL DEFAULT 1,   -- 乐观锁版本号
        trace_ref      TEXT,
        schema_version INTEGER NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_kind_project
        ON records (kind, project_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_records_kind_deleted
        ON records (kind, deleted_at)
    """,
    # 用量账本
    """
    CREATE TABLE IF NOT EXISTS usage_records (
        id            TEXT PRIMARY KEY,
        scope         TEXT NOT NULL,           -- task_id 或 project_id
        actor         TEXT NOT NULL,
        requests      INTEGER NOT NULL DEFAULT 0,
        tokens_in     INTEGER,                 -- 未知为 NULL（D-060）
        tokens_out    INTEGER,
        latency_ms    INTEGER NOT NULL DEFAULT 0,
        cost_estimate REAL,
        currency      TEXT NOT NULL DEFAULT 'UNKNOWN',
        trace_ref     TEXT,
        created_at    TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_usage_scope ON usage_records (scope)
    """,
    # 审计日志：只增不改不删
    """
    CREATE TABLE IF NOT EXISTS audit_records (
        id         TEXT PRIMARY KEY,
        level      TEXT NOT NULL,              -- INFO / WARN / ERROR / CRITICAL
        actor      TEXT NOT NULL,
        action     TEXT NOT NULL,
        trace_ref  TEXT,
        detail     TEXT,
        created_at TEXT NOT NULL
    )
    """,
    # 检查点：一个任务一条，覆盖式写入（幂等）
    """
    CREATE TABLE IF NOT EXISTS checkpoints (
        task_id      TEXT PRIMARY KEY,
        payload      TEXT NOT NULL,            -- Checkpoint 的 JSON
        resume_token TEXT NOT NULL,
        saved_at     TEXT NOT NULL
    )
    """,
)

# 允许写入 `records` 表的实体种类
RECORD_KINDS: frozenset[str] = ENTITY_KINDS

# 查询时允许作为过滤键的公共列（其它键 → Err(VALIDATION.unknown_filter)）
FILTER_KEYS: frozenset[str] = frozenset(
    {"project_id", "namespace", "created_by", "kind", "trace_ref"}
)

# 允许排序的公共列
SORT_KEYS: frozenset[str] = frozenset({"created_at", "updated_at", "id"})
