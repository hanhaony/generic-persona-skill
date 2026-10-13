"""Memory 端口 —— 长期记忆与关键词检索（docs/INTERFACES.md §9）。

**职责**：记忆写入、去重、冲突关联、检索、更新、过期、归档、删除，以及派生产物治理。
**非职责**：⛔ **记忆永远不能升级成证据**（INV-3 / F-3）——本接口里**没有** `add_evidence`。

第一版检索用**关键词**（D-042，推荐 SQLite FTS5 + trigram），**不引入向量检索**。
检索结果**必须原样带出 `namespace`**，防止调用方"洗白"（D-024）。
删除记忆时，相关摘要/索引/缓存（`DerivedArtifact`）必须**同事务失效**（D-051）。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.result import Result
from ..core.types import Page, Query, Ref


class MemoryPort(Protocol):
    def write(self, ctx: CallContext, draft: dict) -> Result[Ref]:
        """写入一条记忆。必须带 `namespace`，且与 `source_type` 自洽。"""
        ...

    def write_simulated(self, ctx: CallContext, draft: dict) -> Result[Ref]:
        """写入模拟对话产生的记忆（`namespace` 强制为 `SIMULATED`）。"""
        ...

    def search(self, ctx: CallContext, q: Query) -> Result[Page[dict]]:
        """关键词检索。返回项**必须**包含 `namespace` 字段。"""
        ...

    def update(self, ctx: CallContext, memory_id: str, patch: dict) -> Result[Ref]:
        """更新并保留历史版本；被取代的旧记忆置 `SUPERSEDED`。"""
        ...

    def expire(self, ctx: CallContext, scope: str) -> Result[list[str]]:
        """过期扫描。清理了多少条是**业务结果**，不是错误。"""
        ...

    def archive(self, ctx: CallContext, memory_id: str) -> Result[Ref]:
        ...

    def delete(self, ctx: CallContext, memory_id: str) -> Result[None]:
        """软删除，并**同事务**把相关 `DerivedArtifact` 置为失效（D-051）。"""
        ...

    def clear_simulated(self, ctx: CallContext, scope: str) -> Result[int]:
        """清空某个范围内的模拟记忆（CLI `--clear-simulated` 用）。"""
        ...
