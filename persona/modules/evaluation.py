"""Evaluation —— 自动化检查与评测（docs/INTERFACES.md §10）。

**职责**：静态依赖检查、不变式断言（INV-1~INV-4）、结构性质量度量、报告生成。
**非职责**（D-056）：⛔ **不得篡改正式业务数据来制造通过的结果**；
              ⛔ 不得绕过模块接口直写数据库；⛔ 不参与生产流程。

三条测试守则：**隔离库 / 只经接口播种 / 只读断言**。

本文件包含两部分：
    1. `EvaluationPort` —— P1.2-B 的端口协议（保留，供类型约束与元测试）。
    2. `EvaluationService` —— **P5「评测与加固」核心实现**（用户称 P3.7 / 端到端集成验收）：
       `check_invariants`（INV-1~INV-4）、`check_dependencies`（分层/禁依赖，AC-20）、
       `run_static_checks`、`snapshot`、`measure_quality`。

**诚实边界**：
    - 所有 DB 断言**只读**（`Storage.query` / `Storage.get`），绝不写业务表（守则 3）。
    - 关键词检索 / 依赖扫描均在**内存**中完成，零第三方依赖（D-068）。
    - `measure_quality` **只输出结构性指标，不做主观打分**（Q-10 待定）。
    - 仓库级文本扫描默认只扫 `persona/` 包；敏感词表由**调用方注入**
      （避免把真实人名写进产品源码 → 违反 AC-16）。
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import Any, Iterable, Protocol, Sequence

from ..core.context import CallContext
from ..core.enums import ProvenanceNamespace
from ..core.result import Ok, Result, is_err
from ..core.types import MAX_QUERY_LIMIT, Query
from ..storage.sqlite import SqliteStorage


# --------------------------------------------------------------------------
# 端口协议（保留 P1.2-B 定义；`EvaluationService` 实现其语义）
# --------------------------------------------------------------------------


class EvaluationPort(Protocol):
    def run_unit_tests(self, ctx: CallContext, selector: str = "") -> Result[dict]:
        ...

    def run_integration_tests(self, ctx: CallContext, suite: str = "") -> Result[dict]:
        ...

    def run_static_checks(self, ctx: CallContext) -> Result[dict]:
        """依赖方向、禁止字段、签名违规的静态扫描。"""
        ...

    def check_invariants(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """INV-1~INV-4 断言。必含：`dialogue_does_not_write_evidence`、`simulated_not_in_evidence`、
        `memory_cannot_become_evidence`、`conflict_not_resolved`、`no_intimacy_field`、
        `variants_not_merged`、`no_person_specific_literals`。"""
        ...

    def measure_quality(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """结构性指标：可追溯率、冲突保留率、去重命中率、字段填充率。**不做主观打分**。"""
        ...


# --------------------------------------------------------------------------
# 常量：受保护表、分层规则、厂商 SDK、禁字段
# --------------------------------------------------------------------------

# INV-1 / AC-13：对话与记忆**不得写入**的四张"现实世界"表
PROTECTED_TABLES: tuple[str, ...] = (
    "EvidenceItem",
    "SourceEvent",
    "PersonaProfile",
    "RelationshipProfile",
)

# 分层规则（INTERFACES §13.2，与 tests/test_dependency_rules.py 同口径）
# (子目录, 源码中不允许出现的字面量, 规则说明)
#
# 注意：needle 用 `_needle()` 拼接，避免规则表**自身**成为被扫描到的"违规字面量"
# （本文件位于 `persona/modules/` 下，会被同目录扫描）。
def _needle(*parts: str) -> str:
    return "".join(parts)


LAYER_RULES: tuple[tuple[str, str, str], ...] = (
    ("cli", _needle("from ..", "modules"), "F-7：CLI 不得直接驱动业务模块，必须经 Orchestrator"),
    ("cli", _needle("import persona.", "modules"), "F-7：CLI 不得直接驱动业务模块"),
    ("cli", _needle("from persona.", "modules"), "F-7：CLI 不得直接驱动业务模块"),
    ("modules", _needle("from ..", "orchestrator"), "F-1：下层模块不得回调 Orchestrator"),
    ("modules", _needle("from ..", "cli"), "F-1：下层模块不得回调 CLI"),
    ("storage", _needle("from ..", "providers"), "F-8：Storage 不得依赖 Provider"),
    ("storage", _needle("from ..", "modules"), "F-8：Storage 不得依赖业务模块"),
    ("providers", _needle("from ..", "modules"), "F-12：Provider 不得依赖业务模块"),
    ("providers", _needle("from ..", "orchestrator"), "F-12：Provider 不得依赖 Orchestrator"),
)

# D-054：Provider 是唯一外部出口，任何模块不得 import 具体厂商 SDK
FORBIDDEN_VENDOR_IMPORTS: tuple[str, ...] = (
    "openai",
    "httpx",
    "requests",
    "anthropic",
    "boto3",
)

# 关系档案禁字段（RL-V2 / D-048）：亲密度不得量化成单一数值
FORBIDDEN_FIELD_PATTERN = re.compile(r".*(score|closeness|intimacy).*")

# 记忆模块不得出现的"写证据"入口（F-3 / INV-3）
MEMORY_WRITE_EVIDENCE_MARKERS = ("add_evidence", "record_inference")
_MEMORY_PUT_EVIDENCE = re.compile(r"""put\(\s*[^)]*["']EvidenceItem["']""")


def _repo_dirs(package_root: pathlib.Path) -> dict[str, pathlib.Path]:
    return {
        name: package_root / name
        for name in ("cli", "modules", "storage", "providers", "orchestrator", "core", "config")
    }


def _iter_sources(directory: pathlib.Path) -> Iterable[pathlib.Path]:
    if not directory.exists():
        return ()
    return sorted(directory.rglob("*.py"))


# --------------------------------------------------------------------------
# 实现
# --------------------------------------------------------------------------


class EvaluationService:
    """只读评测服务。**永不写业务表**（守则 3）。

    `package_root` 默认指向 `persona/` 包（用于静态扫描）；测试可显式传入。
    """

    def __init__(self, storage: SqliteStorage, *, package_root: str | pathlib.Path | None = None) -> None:
        self.storage = storage
        self.package_root = pathlib.Path(
            package_root or pathlib.Path(__file__).resolve().parents[1]
        )

    # ------------------------------------------------------------ 只读查询助手

    def _count_rows(
        self,
        ctx: CallContext,
        kind: str,
        *,
        project_id: str | None = None,
        namespace: str | None = None,
        include_deleted: bool = False,
    ) -> Result[int]:
        """精确计数（走 `COUNT(*)`，不截断）。"""
        filters: dict[str, Any] = {"kind": kind}
        if project_id:
            filters["project_id"] = project_id
        if namespace:
            filters["namespace"] = namespace
        res = self.storage.query(
            ctx, kind, Query(filters=filters, limit=1), include_deleted=include_deleted
        )
        if is_err(res):
            return res
        return Ok(int(res.value.total))

    def _load_rows(
        self,
        ctx: CallContext,
        kind: str,
        *,
        project_id: str | None = None,
        include_deleted: bool = False,
    ) -> Result[list[dict]]:
        filters: dict[str, Any] = {"kind": kind}
        if project_id:
            filters["project_id"] = project_id
        res = self.storage.query(
            ctx, kind, Query(filters=filters, limit=MAX_QUERY_LIMIT), include_deleted=include_deleted
        )
        if is_err(res):
            return res
        return Ok(list(res.value.items))

    # ------------------------------------------------------------ 快照 / 泄漏检测

    def snapshot(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """给四张受保护表拍一张"行数快照"，用于对话前后对比（AC-13）。"""
        counts: dict[str, int] = {}
        for kind in PROTECTED_TABLES:
            res = self._count_rows(ctx, kind, project_id=project_id)
            if is_err(res):
                return res
            counts[kind] = res.value
        return Ok({"project_id": project_id, "counts": counts})

    @staticmethod
    def dialogue_leaks(before: dict, after: dict) -> list[str]:
        """对比两张快照，返回**发生了变化**的表名（应为空，否则 INV-1 被破）。"""
        b = dict(before.get("counts", {}))
        a = dict(after.get("counts", {}))
        return sorted(k for k in set(b) | set(a) if b.get(k) != a.get(k))

    # ------------------------------------------------------------ 不变式断言

    def check_invariants(
        self,
        ctx: CallContext,
        project_id: str,
        *,
        suspicious_literals: Sequence[str] = (),
        scan_roots: Sequence[str | pathlib.Path] | None = None,
    ) -> Result[dict]:
        """INV-1~INV-4 断言。返回 `{"project_id", "checks": [...], "passed": bool}`。

        每个 check 形如 `{"name", "kind", "passed", "detail"}`；`kind` ∈ `db`/`static`。
        """
        checks: list[dict] = []

        # --- INV-1 / AC-13：对话不得写入任何"现实世界"表 ---------------------
        leaked: list[str] = []
        for kind in PROTECTED_TABLES:
            res = self._count_rows(
                ctx, kind, project_id=project_id,
                namespace=str(ProvenanceNamespace.SIMULATED),
            )
            if is_err(res):
                return res
            if res.value:
                leaked.append(f"{kind}={res.value}")
        checks.append({
            "name": "dialogue_does_not_write_evidence",
            "kind": "db",
            "passed": not leaked,
            "detail": "四张受保护表均无 SIMULATED 行" if not leaked
            else f"发现模拟内容写入现实表：{leaked}",
        })

        # --- INV-2：EvidenceItem 全表无 namespace=SIMULATED -------------------
        res = self._count_rows(
            ctx, "EvidenceItem", project_id=project_id,
            namespace=str(ProvenanceNamespace.SIMULATED),
        )
        if is_err(res):
            return res
        n_sim = res.value
        checks.append({
            "name": "simulated_not_in_evidence",
            "kind": "db",
            "passed": n_sim == 0,
            "detail": "EvidenceItem 无 SIMULATED 行" if n_sim == 0
            else f"EvidenceItem 中存在 {n_sim} 条 SIMULATED 行",
        })

        # --- INV-3：Memory 无写证据路径（静态，只看**代码**，不看注释/文档字符串） ---
        mem_text = self._read_source("modules", "memory.py")
        mem_offenders = [m for m in MEMORY_WRITE_EVIDENCE_MARKERS if m in self._function_names(mem_text)]
        if _MEMORY_PUT_EVIDENCE.search(mem_text):
            mem_offenders.append("put:EvidenceItem")
        checks.append({
            "name": "memory_cannot_become_evidence",
            "kind": "static",
            "passed": not mem_offenders,
            "detail": "Memory 无写证据入口（仅只读引用）" if not mem_offenders
            else f"Memory 出现写证据痕迹：{mem_offenders}",
        })

        # --- INV-4：冲突只保留不裁决 -----------------------------------------
        conf_rows = self._load_rows(ctx, "ConflictRecord", project_id=project_id)
        if is_err(conf_rows):
            return conf_rows
        bad_conflicts: list[str] = []
        for row in conf_rows.value:
            review = str(row.get("review_status", ""))
            policy = str(row.get("resolution_policy", ""))
            members = [m for m in (row.get("members") or []) if isinstance(m, dict)]
            if review == "RESOLVED" or (policy and policy != "KEEP_ALL_UNASSESSED") or len(members) < 2:
                bad_conflicts.append(str(row.get("id")))
        checks.append({
            "name": "conflict_not_resolved",
            "kind": "db",
            "passed": not bad_conflicts,
            "detail": f"共 {len(conf_rows.value)} 条冲突记录，全部保留不裁决" if not bad_conflicts
            else f"存在被裁决/退化的冲突记录：{bad_conflicts}",
        })

        # --- 扩展：关系档案不得出现量化亲密度（静态 + 落盘扫描） -------------
        rel_text = self._read_source("modules", "relationship.py")
        rel_field_offenders = self._forbidden_dataclass_fields(rel_text)
        rel_rows = self._load_rows(ctx, "RelationshipProfile", project_id=project_id)
        if is_err(rel_rows):
            return rel_rows
        payload_offenders = [
            str(r.get("id")) for r in rel_rows.value
            if self._find_forbidden_keys(r.get("payload", r))
        ]
        no_intimacy_ok = not rel_field_offenders and not payload_offenders
        checks.append({
            "name": "no_intimacy_field",
            "kind": "static",
            "passed": no_intimacy_ok,
            "detail": "关系档案结构无 score/closeness/intimacy 字段" if no_intimacy_ok
            else f"字段违规：{rel_field_offenders}；落盘违规：{payload_offenders}",
        })

        # --- 扩展：虚构角色的不同改编版本不得被合并 -------------------------
        variants = self._check_variants_not_merged(ctx, project_id)
        if is_err(variants):
            return variants
        checks.append(variants.value)

        # --- AC-16：源码/配置中无特定真实人物字面量（由调用方注入敏感词表） --
        literals = [s for s in suspicious_literals if s]
        if not literals:
            lit_check = {
                "name": "no_person_specific_literals",
                "kind": "static",
                "passed": True,
                "detail": "未提供敏感词表，跳过（调用方可注入 suspicious_literals）",
            }
        else:
            roots = [pathlib.Path(p) for p in (scan_roots or [self.package_root])]
            offenders = self._scan_literals(literals, roots)
            lit_check = {
                "name": "no_person_specific_literals",
                "kind": "static",
                "passed": not offenders,
                "detail": "扫描范围内无敏感字面量" if not offenders
                else f"命中：{[f'{p}:{w}' for p, w in offenders[:10]]}",
            }
        checks.append(lit_check)

        return Ok({
            "project_id": project_id,
            "checks": checks,
            "passed": all(c["passed"] for c in checks),
        })

    # ------------------------------------------------------------ 依赖 / 静态

    def check_dependencies(self, ctx: CallContext) -> Result[dict]:
        """分层与禁依赖静态扫描（AC-20）。返回 `{"passed", "violations": [...]}`。"""
        violations: list[dict] = []
        for sub, needle, rule in LAYER_RULES:
            for path in _iter_sources(self.package_root / sub):
                text = path.read_text(encoding="utf-8")
                if needle in text:
                    violations.append({
                        "module": f"{sub}/{path.name}",
                        "rule": rule,
                        "detail": f"出现禁止写法 {needle!r}",
                    })
        # 厂商 SDK
        for path in _iter_sources(self.package_root):
            text = path.read_text(encoding="utf-8")
            for vendor in FORBIDDEN_VENDOR_IMPORTS:
                if f"import {vendor}" in text:
                    violations.append({
                        "module": str(path.relative_to(self.package_root)),
                        "rule": "D-054：不得 import 具体厂商 SDK",
                        "detail": f"import {vendor}",
                    })
        return Ok({"passed": not violations, "violations": violations})

    def run_static_checks(self, ctx: CallContext) -> Result[dict]:
        """依赖 + 禁字段 + 签名三项静态扫描（INTERFACES §10.2）。"""
        deps = self.check_dependencies(ctx)
        if is_err(deps):
            return deps

        forbidden_fields: list[dict] = []
        signature_violations: list[dict] = []
        for path in _iter_sources(self.package_root / "modules"):
            text = path.read_text(encoding="utf-8")
            for name in self._forbidden_dataclass_fields(text):
                forbidden_fields.append({"module": path.name, "field": name})
            for fn, arg in self._record_params(text):
                signature_violations.append({"module": path.name, "function": fn, "param": arg})

        deps_value = deps.value
        passed = (
            deps_value["passed"] and not forbidden_fields and not signature_violations
        )
        return Ok({
            "passed": passed,
            "dependency_violations": deps_value["violations"],
            "forbidden_fields": forbidden_fields,
            "signature_violations": signature_violations,
        })

    # ------------------------------------------------------------ 质量度量

    def measure_quality(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """结构性指标（**不做主观打分**，Q-10）。"""
        evidence = self._load_rows(ctx, "EvidenceItem", project_id=project_id)
        if is_err(evidence):
            return evidence
        conflicts = self._load_rows(ctx, "ConflictRecord", project_id=project_id)
        if is_err(conflicts):
            return conflicts
        personas = self._load_rows(ctx, "PersonaProfile", project_id=project_id)
        if is_err(personas):
            return personas

        ev_rows = evidence.value
        ev_traceable = sum(
            1 for r in ev_rows if (r.get("source_id") or r.get("source_event_ids"))
        )
        conf_rows = conflicts.value
        conf_retained = sum(
            1 for r in conf_rows
            if len([m for m in (r.get("members") or []) if isinstance(m, dict)]) >= 2
            and str(r.get("review_status", "")) != "RESOLVED"
        )

        # 档案字段填充率 + 可追溯率：递归统计 payload 中带 evidence_refs 的条目
        items_with_refs = 0
        items_total = 0
        for row in personas.value:
            for node in self._walk_dicts(row.get("payload", row)):
                if "evidence_refs" in node:
                    items_total += 1
                    if node["evidence_refs"]:
                        items_with_refs += 1

        ratio = lambda a, b: (round(a / b, 4) if b else None)  # noqa: E731
        return Ok({
            "project_id": project_id,
            "evidence_item_count": len(ev_rows),
            "evidence_traceability_rate": ratio(ev_traceable, len(ev_rows)),
            "conflict_count": len(conf_rows),
            "conflict_retention_rate": ratio(conf_retained, len(conf_rows)),
            "dedup_hit_rate": None,  # 需在写入路径埋点（P5 后续）
            "persona_item_count": items_total,
            "persona_field_fill_rate": ratio(items_with_refs, items_total),
            "llm_judge_disabled": True,
            "notes": ["只输出结构性指标；无标准答案时不做主观打分（Q-10）"],
        })

    # ------------------------------------------------------------ 内部工具

    def _read_source(self, sub: str, filename: str) -> str:
        path = self.package_root / sub / filename
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def _scan_literals(
        self, literals: Sequence[str], roots: Sequence[pathlib.Path]
    ) -> list[tuple[str, str]]:
        """在给定根目录下做大小写不敏感的文本扫描，返回 (相对路径, 命中词)。"""
        lowered = [(w, w.lower()) for w in literals]
        offenders: list[tuple[str, str]] = []
        for root in roots:
            for path in _iter_sources(root) if root.is_dir() else [root]:
                try:
                    text = path.read_text(encoding="utf-8").lower()
                except (OSError, UnicodeDecodeError):
                    continue
                for word, low in lowered:
                    if low in text:
                        offenders.append((str(path), word))
        return offenders

    @staticmethod
    def _forbidden_dataclass_fields(source: str) -> list[str]:
        """AST 扫描：类级字段名命中 score/closeness/intimacy 的（不会误报字符串常量）。"""
        hits: list[str] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return hits
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        if FORBIDDEN_FIELD_PATTERN.match(stmt.target.id):
                            hits.append(f"{node.name}.{stmt.target.id}")
        return hits

    @staticmethod
    def _function_names(source: str) -> set[str]:
        """AST 扫描：文件中**定义的函数名**（不含注释/文档字符串里的提及）。"""
        names: set[str] = set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return names
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
        return names

    @staticmethod
    def _record_params(source: str) -> list[tuple[str, str]]:
        """INTERFACES §1.6：公开函数签名里不得出现 `*Record`。"""
        hits: list[tuple[str, str]] = []
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return hits
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                args = list(node.args.args) + list(node.args.kwonlyargs)
                for arg in args:
                    if arg.arg.endswith("Record"):
                        hits.append((node.name, arg.arg))
        return hits

    def _find_forbidden_keys(self, node: Any) -> list[str]:
        hits: list[str] = []
        for mapping in self._walk_dicts(node):
            for key in mapping:
                if isinstance(key, str) and FORBIDDEN_FIELD_PATTERN.match(key):
                    hits.append(key)
        return hits

    @staticmethod
    def _walk_dicts(node: Any) -> Iterable[dict]:
        """递归遍历嵌套 dict/list，逐一产出其中的 dict。"""
        stack = [node]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                yield cur
                stack.extend(cur.values())
            elif isinstance(cur, (list, tuple)):
                stack.extend(cur)

    def _check_variants_not_merged(self, ctx: CallContext, project_id: str) -> Result[dict]:
        """D-127：同一虚构角色的不同改编版本（同 `variant_group_id`）不得同场出现。"""
        entities = self._load_rows(ctx, "PersonEntity", project_id=project_id)
        if is_err(entities):
            return entities
        group_of: dict[str, str] = {}
        for row in entities.value:
            gid = str(row.get("variant_group_id") or "")
            if gid:
                group_of[str(row.get("id"))] = gid

        profiles = self._load_rows(ctx, "RelationshipProfile", project_id=project_id)
        if is_err(profiles):
            return profiles
        offenders: list[str] = []
        for row in profiles.value:
            participants = [str(p) for p in (row.get("participant_entity_ids") or [])]
            seen: set[str] = set()
            for pid in participants:
                gid = group_of.get(pid, "")
                if gid and gid in seen:
                    offenders.append(str(row.get("id")))
                    break
                if gid:
                    seen.add(gid)
        return Ok({
            "name": "variants_not_merged",
            "kind": "db",
            "passed": not offenders,
            "detail": "无同角色双版本同场" if not offenders
            else f"以下关系档案混用了同一角色的多个版本：{offenders}",
        })


__all__ = [
    "EvaluationPort",
    "EvaluationService",
    "PROTECTED_TABLES",
    "LAYER_RULES",
    "FORBIDDEN_VENDOR_IMPORTS",
]
