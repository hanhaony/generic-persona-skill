"""Evaluation 端口 —— 自动化检查与评测（docs/INTERFACES.md §10）。

**职责**：单元测试 / 集成测试、静态依赖检查、不变式断言、结构性质量度量、报告生成。
**非职责**（D-056）：⛔ **不得篡改正式业务数据来制造通过的结果**；
              ⛔ 不得绕过模块接口直写数据库；⛔ 不参与生产流程。

三条测试守则：隔离库 / 只经接口播种 / 只读断言。
第一版 `measure_quality` **只输出结构性指标，不做主观打分**（Q-10 待定）。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.result import Result


class EvaluationPort(Protocol):
    def run_unit_tests(self, ctx: CallContext, selector: str = "") -> Result[dict]:
        ...

    def run_integration_tests(self, ctx: CallContext, suite: str = "") -> Result[dict]:
        ...

    def run_static_checks(self, ctx: CallContext) -> Result[dict]:
        """依赖方向、禁止字段、签名违规的静态扫描。"""
        ...

    def check_invariants(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """INV-1~INV-4 断言。必含：`simulated_not_in_evidence`、`conflict_not_resolved`、
        `no_intimacy_field`、`variants_not_merged`、`no_person_specific_literals`。"""
        ...

    def measure_quality(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """结构性指标：可追溯率、冲突保留率、去重命中率、字段填充率。**不做主观打分**。"""
        ...
