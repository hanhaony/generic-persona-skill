"""Evidence 端口 —— 证据登记、分级、去重、冲突记录（docs/INTERFACES.md §4）。

**职责**：登记来源、切分来源事件、提取并分级证据、去重、记录冲突、判定稳定性。
**非职责**：⛔ **不得自行启动研究循环**（F-2）——本接口里**没有** `search` / `collect` / `query_plan`。

命名空间闸门：
    `Source` / `SourceEvent` 只能 `PUBLIC_SOURCE`；
    `EvidenceItem` 可以是 `PUBLIC_SOURCE` / `SYSTEM_DERIVED`（仅推断）/ `USER_PROVIDED`；
    **`EvidenceItem` 永远不可以是 `SIMULATED`**（D-023）。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.enums import EvidenceTier, Stability
from ..core.result import Result
from ..core.types import Page, Query, Ref


class EvidencePort(Protocol):
    """证据模块的**写入 + 查询**接口。只有本模块能写证据表。"""

    def register_source(self, ctx: CallContext, draft: dict) -> Result[Ref]:
        """登记一份来源（含去重：同 canonical_key / content_hash 只登记一次）。"""
        ...

    def extract_events(self, ctx: CallContext, source_id: str) -> Result[list[Ref]]:
        """把一个来源切成可定位的原始事件（一次采访 / 一集剧情 / 一次设定发布）。"""
        ...

    def add_evidence(self, ctx: CallContext, draft: dict) -> Result[Ref]:
        """新增一条证据（强制分级 + 出处定位 + 命名空间校验）。"""
        ...

    def record_inference(
        self, ctx: CallContext, claim: str, derived_from: list[str]
    ) -> Result[Ref]:
        """记录系统推断。`derived_from` **不可为空**（D-014：禁止无根推断）。"""
        ...

    def detect_duplicates(self, ctx: CallContext, project_id: str) -> Result[list[Ref]]:
        """归并同一说法的多个来源：保留全部来源引用，只合并"说法本身"（D-015）。"""
        ...

    def detect_conflicts(self, ctx: CallContext, project_id: str) -> Result[list[Ref]]:
        """检测互斥说法并记 `ConflictRecord`。

        冲突**不是错误**（D-055）：返回 `Ok([...])`，流程继续。
        """
        ...

    def query_evidence(self, ctx: CallContext, q: Query) -> Result[Page[dict]]:
        """按条件查询证据。"""
        ...

    def judge_stability(
        self, ctx: CallContext, entity_id: str, dimension: str
    ) -> Result[Stability]:
        """稳定性闸门（D-012）：只有 REPEATED / PATTERN 才能进"稳定性格"。"""
        ...


class EvidenceReader(Protocol):
    """**只读**端口。Persona / Relationship / Dialogue 只能拿到这个——协议里根本没有写方法。"""

    def query_evidence(self, ctx: CallContext, q: Query) -> Result[Page[dict]]:
        ...

    def get_evidence_tier(self, ctx: CallContext, evidence_id: str) -> Result[EvidenceTier]:
        ...
