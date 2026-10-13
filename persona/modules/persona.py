"""Persona 端口 —— 人物档案蒸馏（docs/INTERFACES.md §6）。

**职责**：把证据蒸馏成人物档案（背景 / 性格 / 习惯 / 对话风格 / 经历 / 关系，D-016）。
**非职责**：⛔ **不直接调用具体模型供应商**（F-5）——只能依赖 `Provider` 抽象；
          ⛔ 不得引用 `MemoryItem` 作为依据（模拟记忆不能反向喂养档案）。

两条硬规则：
    - 每个非空字段**必须**带 `evidence_refs`；没证据就留空并记 `open_questions`（D-020）。
    - `confidence` 只用于排序展示，**不代表真伪**（D-010）。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.result import Result
from ..core.types import Ref


class PersonaPort(Protocol):
    def distill(self, ctx: CallContext, entity_id: str, evidence_ids: list[str]) -> Result[Ref]:
        """蒸馏单个人物档案。资料不足 → `Ok(status=INSUFFICIENT_EVIDENCE)`，**不算失败**（D-008）。"""
        ...

    def revise(self, ctx: CallContext, profile_id: str, new_evidence_ids: list[str]) -> Result[Ref]:
        """增量修订。没有新证据 → `Err(STATE_CONFLICT.no_new_evidence)`（省预算）。"""
        ...

    def get_persona(self, ctx: CallContext, entity_id: str) -> Result[dict]:
        """读取档案视图。"""
        ...

    def apply_stability_gate(self, ctx: CallContext, trait: str) -> Result[str]:
        """稳定性闸门：`SINGLE` 只能进"单次事件"字段，不许进"稳定性格"（D-012）。"""
        ...


class PersonaReader(Protocol):
    """只读端口（供 Relationship 与 Dialogue 使用）。"""

    def get_persona(self, ctx: CallContext, entity_id: str) -> Result[dict]:
        ...
