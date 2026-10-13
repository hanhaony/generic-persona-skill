"""Research 端口 —— 公开资料搜集（docs/INTERFACES.md §5）。

**职责**：生成检索计划、调用 `Provider.search`、过滤排序、把结果交给 `Evidence` 登记。
**非职责**：⛔ **不直接写 Evidence 表**（D-031 / F-10）——必须先经 `Evidence.register_source`。

⛔ 检索 0 条结果**不是错误**：记 `NO_RESULT` 并继续（ERROR_HANDLING §2.3）。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.result import Result
from ..core.types import Ref


class ResearchPort(Protocol):
    def plan_queries(self, ctx: CallContext, entity_ids: list[str]) -> Result[list[str]]:
        """生成检索计划。第一版用**确定性模板**，不调模型（Q-29 推荐）。"""
        ...

    def collect(self, ctx: CallContext, query: str) -> Result[dict]:
        """执行一次检索（经 `Provider.search`）。0 结果 → `Ok(fetch_status=NO_RESULT)`。"""
        ...

    def filter_and_rank(self, ctx: CallContext, raw_results: list[dict]) -> Result[list[dict]]:
        """去广告 / 去低质 / 排序。合规钩子（来源白黑名单、robots/ToS）留在这里（Q-02）。"""
        ...

    def register(self, ctx: CallContext, raw_results: list[dict]) -> Result[list[Ref]]:
        """把筛选后的结果交给 `Evidence.register_source`，**自己不写证据表**。"""
        ...

    def run(self, ctx: CallContext, entity_ids: list[str]) -> Result[dict]:
        """对外主入口：计划 → 检索 → 过滤 → 登记。"""
        ...
