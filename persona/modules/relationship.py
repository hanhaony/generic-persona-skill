"""Relationship 端口 —— 关系与互动模式蒸馏（docs/INTERFACES.md §7）。

**职责**：共同经历、互动模式、情绪与表达习惯、**双方不同视角**、时间变化、群体互动（≥3 人，D-017/D-019）。
**非职责**：⛔ **不得只由两份人物档案推导现实关系**（F-6 / RL-V6）——
          每一条关系条目都必须**至少引用一条 `EvidenceItem`**。

另外：⛔ **禁止任何"亲密度 / 强度"数值指标**（D-048，字段名不得含 score / intimacy / closeness）。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.result import Result
from ..core.types import Ref


class RelationshipPort(Protocol):
    def distill(
        self, ctx: CallContext, participant_entity_ids: list[str], evidence_ids: list[str]
    ) -> Result[Ref]:
        """蒸馏关系档案。**视角按人物分键独立存储**，系统不生成"综合定论"（D-018）。"""
        ...

    def revise(self, ctx: CallContext, profile_id: str, new_evidence_ids: list[str]) -> Result[Ref]:
        ...

    def get_relationship(self, ctx: CallContext, profile_id: str) -> Result[dict]:
        ...

    def validate_evidence_refs(self, ctx: CallContext, entries: list[dict]) -> Result[bool]:
        """RL-V6 校验：每条条目都必须直接引用证据，只引用档案字段视为非法。"""
        ...


class RelationshipReader(Protocol):
    """只读端口（供 Dialogue 使用）。"""

    def get_relationship(self, ctx: CallContext, profile_id: str) -> Result[dict]:
        ...
