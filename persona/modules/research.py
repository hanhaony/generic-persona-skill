"""Research —— 公开资料搜集的**最小可运行实现**（docs/INTERFACES.md §5、DATA_MODEL §4.3）。

**H-11A**（用户称；仓库 `ROADMAP.md` 的编号是 **P2「研究 + 证据管线」** 的 Research
部分，差异见 `DECISIONS.md` §3 Q-37）：把 `Research` 从"只有 Protocol"推进到
**可执行、可测试**的最小模块，为后续 H-10 业务编排器提供稳定接口。

**职责（INTERFACES §5.1）**：
    ✅ 生成**确定性**检索计划（第一版不用模型生成检索词，Q-29）
    ✅ 调用 `Provider.search` 执行一次检索（Provider 是全项目唯一外部出口，D-030/D-054）
    ✅ 过滤 / 排序 / 去重候选，**被拒项必须记录原因**
    ✅ 把筛选后的候选来源交给 `Evidence.register_source` 登记

**非职责（红线）**：
    ⛔ **不直接写 `Source` / `SourceEvent` / `EvidenceItem` 表**（D-031 / F-10）。
       本模块只持有 `SourceRegistrarPort` —— 该协议里**只有 `register_source`**，
       结构上就拿不到 `add_evidence` / 存储仓储层，因此"绕过 Evidence 伪造已验证证据"
       在类型层面就不可能发生。
    ⛔ 不做证据分级、不做人物蒸馏、不裁决人物歧义（交 `Evidence`）。
    ⛔ **不承担任务六态机 / 预算账本 / 检查点管理**（用户目标 7）。本模块只把
       `budget_used`（Provider 如实回报的用量之和）与 `warnings` 带出去，由调用方
       （Orchestrator / `TaskService` / `BudgetGate`）记账与写检查点。
       `INTERFACES §5.2` 提到"每个 `PlannedQuery` 完成后写一次检查点"——**该职责
       属 Orchestrator**，本模块不做，记为遗留（见 `RUNNING.md` §5.11 H-16）。
    ⛔ 不接真实搜索 API、不访问外网、不持有 API Key。

**隔离（用户目标 5）**：
    - 检索只经 `Provider.search`，**绝不触碰** `SIMULATED` 内容；
    - 登记走 `Evidence.register_source`，其命名空间闸门恒为 `PUBLIC_SOURCE`，
      存储层另有结构闸门（D-064 双层），模拟内容写不进去；
    - 拒绝项一律记录原因，不含任何"预设结论"。

**诚实边界（本阶段不做的）**：
    - 检索词只来自**确定性模板**（人名 + 话题），别名的语义扩展、多语言改写未实现；
    - 相关性排序是**确定性结构打分**（标题/URL/摘要/日期是否齐全），
      不是语义相关性模型；不假装能判断"哪条更可信"；
    - 抓取正文、快照、正文切分都不做（`content_ref` / `snapshot_ref` 留空）。

**与 INTERFACES 的差异（已报告，未擅自改契约）**：
    1. `INTERFACES §5.2` 把 `plan_queries` 的失败列为 `VALIDATION.empty_targets`，
       但 `ERROR_HANDLING §2.2` / `ErrorCode` 里**没有这个码**。本实现复用既有的
       `VALIDATION.required_field`，不新增错误码（见 `RUNNING.md` §5.11 H-14）。
    2. `filter_and_rank` 需要一个可选关键字参数 `target_entity_ids`，用于把
       **对象边界**（这条候选属于哪个 PersonEntity）带到 `Evidence`；这是对契约
       签名的**向后兼容扩展**（原签名不传该参数时行为不变），见 H-15。
    3. 契约里的 `fetch_status=NO_RESULT` **不是** `FetchStatus` 枚举成员（该枚举
       只有 OK/PARTIAL/FAILED/BLOCKED）。本模块用模块级常量 `FETCH_NO_RESULT`
       表达"检索成功但 0 结果"，**不改动已审的 `FetchStatus`**（见 H-14）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.context import CallContext
from ..core.enums import FetchStatus, SourceKind
from ..core.errors import ErrorCategory, ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, WarningNote, is_err
from ..core.temporal import FuzzyDate, TimePrecision
from ..core.types import BudgetUsage, SourceMaterial
from ..providers.base import Provider, SearchRequest, SearchResult
from .evidence import SourceDraft, SourceRef

# --------------------------------------------------------------------------
# 警告码（业务提示，不是错误；ERROR_HANDLING §2.3）
# --------------------------------------------------------------------------

WARNING_NO_RESULT = "NO_RESULT"                      # 检索成功但 0 条结果（不是错误）
WARNING_QUERY_FAILED = "QUERY_FAILED"                # 单个检索失败，已跳过并继续
WARNING_EMPTY_INPUT = "EMPTY_INPUT"                  # filter_and_rank 输入为空（不算失败）
WARNING_CANDIDATE_REJECTED = "CANDIDATE_REJECTED"    # 有候选被拒（原因见 ranked.rejected）
WARNING_BATCH_DEDUP = "BATCH_DEDUP"                  # 同一批内的重复候选被折叠
WARNING_REGISTER_DEDUP = "SOURCE_DEDUP_HIT"          # 与 Evidence 同名：命中去重，返回原记录
WARNING_NO_EVENTS = "NO_EVENTS"                      # 本模块不产出 SourceEvent（交 Evidence）

# 检索成功但没有任何结果时的 `fetch_status` 取值。
# **刻意不改动已审的 `FetchStatus` 枚举**（DATA_MODEL §4.3 只有 OK/PARTIAL/FAILED/BLOCKED）；
# INTERFACES §5.2 用字面量 `NO_RESULT` 表达这个业务状态，故在此定义模块级常量。
FETCH_NO_RESULT = "NO_RESULT"

# 确定性排序用的字段权重（结构完整性，**不是"可信度"也不是"打分"**）
_RANK_WEIGHTS: dict[str, float] = {
    "title": 1.0,
    "url": 1.0,
    "snippet": 0.5,
    "published_at": 0.5,
}


# --------------------------------------------------------------------------
# 端口协议
# --------------------------------------------------------------------------


class SourceRegistrarPort(Protocol):
    """Research 能拿到的 `Evidence` **登记**端口：**只有 `register_source`**。

    与 `EvidencePort` 故意分开定义，把 Research 的写权限压到最小：协议里没有
    `add_evidence` / `record_inference` / `detect_*`，也没有任何仓储层句柄，
    因此"Research 直接写证据表"（F-10）在类型层面即被堵死。
    """

    def register_source(self, ctx: CallContext, draft: SourceDraft) -> Result[SourceRef]:
        ...


class ResearchPort(Protocol):
    """资料搜集端口（`docs/INTERFACES.md §5.2`）。

    ⚠️ P1.2-B 骨架里的旧签名（`plan_queries(ctx, entity_ids)` /
    `collect(ctx, query)` / `register(ctx, raw_results)`）与文档 §5.2 不一致；
    本阶段**以文档为准**更正为下面这组签名（与 `Evidence` 的 D-110 同一处理口径）。
    该协议没有任何既有调用方，本次对齐不会破坏任何代码。
    """

    def plan_queries(
        self, ctx: CallContext, targets: list["PersonQueryTarget"]
    ) -> Result[list["PlannedQuery"]]:
        """生成检索计划。第一版用**确定性模板**，不调模型（Q-29）。"""
        ...

    def collect(self, ctx: CallContext, planned_query: "PlannedQuery") -> Result["CollectOutcome"]:
        """执行一次检索（经 `Provider.search`）。0 结果 → `Ok(fetch_status=NO_RESULT)`。"""
        ...

    def filter_and_rank(
        self,
        ctx: CallContext,
        results: list[SearchResult],
        *,
        target_entity_ids: list[str] | None = None,
    ) -> Result["RankedCandidates"]:
        """去重 / 去无效 / 确定性排序。合规钩子（白黑名单、robots/ToS）留在这里（Q-02）。"""
        ...

    def register(
        self, ctx: CallContext, ranked: "RankedCandidates"
    ) -> Result["ResearchRegisterOutcome"]:
        """把筛选后的结果交给 `Evidence.register_source`，**自己不写证据表**。"""
        ...

    def run(
        self,
        ctx: CallContext,
        targets: list["PersonQueryTarget"],
        budget: Any | None = None,
    ) -> Result["ResearchOutput"]:
        """对外主入口：计划 → 检索 → 过滤 → 登记。"""
        ...


# --------------------------------------------------------------------------
# 数据契约（Draft 进 / 结构化结果出；跨模块只传 ID 与这些形状）
# --------------------------------------------------------------------------


@dataclass
class PersonQueryTarget:
    """一个检索目标（INTERFACES §5.2 `PersonQueryTarget` + 用户目标 2 的"对象上下文"）。"""

    entity_id: str
    display_name: str = ""
    aliases: list[str] = field(default_factory=list)
    entity_type: str = ""
    work_titles: list[str] = field(default_factory=list)
    adaptation_version: str | None = None
    topic: str | None = None


@dataclass
class PlannedQuery:
    """一条检索计划（INTERFACES §5.2 `PlannedQuery`）。"""

    query_text: str
    language: str = "zh"
    intent: str = ""
    budget_cost_estimate: int = 1
    target_entity_ids: list[str] = field(default_factory=list)


@dataclass
class CollectOutcome:
    """一次检索的结果（INTERFACES §5.2 `CollectOutcome`）。

    `fetch_status` 是**字符串**：成功且有条目时为 `FetchStatus.OK`，
    成功但 0 条时为 `FETCH_NO_RESULT`（不是错误）。
    """

    results: list[SearchResult] = field(default_factory=list)
    usage: UsageDelta | None = None
    fetch_status: str = str(FetchStatus.OK)


@dataclass
class RankedCandidate:
    """一条被接受的候选：搜索结果 + 它所属的**对象边界** + 结构完整度权重。

    字段名叫 `rank_weight`（而**不是** `score`）是刻意的：`INV-4` 的
    `no_intimacy_field` 检查用 `.*(score|closeness|intimacy).*` 扫描所有模块的
    类字段名（`persona/modules/evaluation.py`），用来防止出现"关系/人格打分"字段。
    这里是**来源结构完整度**，与亲密/评价无关，换个名字即可，**不去放宽已验收的不变式**。
    """

    result: SearchResult
    target_entity_ids: list[str] = field(default_factory=list)
    rank_weight: float = 0.0


@dataclass
class RejectedCandidate:
    """一条被拒绝的候选：**必须带上原因**（INTERFACES §5.2）。"""

    result: SearchResult
    reason: str


@dataclass
class RankedCandidates:
    """过滤排序结果（INTERFACES §5.2 `RankedCandidates`）。"""

    accepted: list[RankedCandidate] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)
    warnings: list[WarningNote] = field(default_factory=list)


@dataclass
class ResearchRegisterOutcome:
    """登记结果（INTERFACES §5.2 `ResearchRegisterOutcome`）。

    `registered_count` = 交给 Evidence 后**拿到 source_id 的候选数**（含去重命中，
    命中也返回既有 id）；`dedup_hits` = 其中命中去重的条数。
    `event_ids` 恒为空：本模块不产出 `SourceEvent`（切分是 `Evidence` 的事）。
    """

    source_ids: list[str] = field(default_factory=list)
    dedup_hits: int = 0
    event_ids: list[str] = field(default_factory=list)
    registered_count: int = 0
    materials: list[SourceMaterial] = field(default_factory=list)


@dataclass
class ResearchOutput:
    """`run` 的结构化结果（INTERFACES §5.2 `ResearchOutput`）。

    `materials` 是 G-2 新增：把检索到的**原始材料**（含 `snippet`）随产出一起
    交给抽取器。此前只返回 `source_ids`——来源元数据里**没有**材料正文，
    下游也就无从抽取 claim（这正是 G-2 的本体）。

    ⚠️ `materials` 是**材料**不是证据：未校验、未分级、未登记。
    `snippet` 只是检索摘要，**不是正文全文**（正文抓取未实现，H-17）。
    """

    source_ids: list[str] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    materials: list[SourceMaterial] = field(default_factory=list)
    planned_count: int = 0
    executed_count: int = 0
    no_result_count: int = 0
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    warnings: list[WarningNote] = field(default_factory=list)


@dataclass(frozen=True)
class ResearchDeps:
    """Research 的全部外部依赖（类型层面阻断误用）。

    - `provider`：**唯一**外部出口（`Provider.search`）。
    - `evidence`：只有 `register_source` 的登记端口（堵死 F-10）。
    - `search_metadata`：原样透传给 `Provider.search` 的 `metadata`。
      存在的意义是让**调用方**显式给出 `fixture_key`（既有 Mock Provider 家族的约定：
      `FakeProvider` 要求 `metadata["fixture_key"]`）。业务代码**不猜**这个键，
      真实 Provider 也会忽略它。
    """

    provider: Provider
    evidence: SourceRegistrarPort
    search_metadata: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# 内部辅助
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _str_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values if str(v or "").strip()]


def _coerce_source_kind(guess: Any) -> SourceKind:
    """把 Provider 的 `source_kind_guess` 折成已审枚举；不认识的归 `OTHER`。"""
    if guess is None:
        return SourceKind.OTHER
    if isinstance(guess, SourceKind):
        return guess
    try:
        return SourceKind(str(guess))
    except ValueError:
        return SourceKind.OTHER


def _fuzzy_from_text(value: Any) -> FuzzyDate | None:
    """`SearchResult.published_at`（字符串）→ `FuzzyDate`。

    只做**保守**转换：看起来像 `YYYY-MM-DD` 就当"日精度"，否则整串进 `raw`
    且精度记 `UNKNOWN`——绝不自作主张推断真实日期。
    """
    raw = _text(value)
    if not raw:
        return None
    parts = raw.split("-")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return FuzzyDate(raw=raw, start=raw, end=raw, precision=TimePrecision.DAY)
    return FuzzyDate(raw=raw, precision=TimePrecision.UNKNOWN)


def _rank_weight_of(result: SearchResult) -> float:
    """确定性结构完整度：字段是否齐备（**不是可信度**，D-010）。"""
    weight = 0.0
    if _text(result.title):
        weight += _RANK_WEIGHTS["title"]
    if _text(result.url):
        weight += _RANK_WEIGHTS["url"]
    if _text(result.snippet):
        weight += _RANK_WEIGHTS["snippet"]
    if _text(result.published_at):
        weight += _RANK_WEIGHTS["published_at"]
    return weight


def _batch_key(result: SearchResult) -> str:
    """批内去重键：优先 `canonical_key`，退回 `url`。"""
    return _text(result.canonical_key) or _text(result.url)


# --------------------------------------------------------------------------
# 实现
# --------------------------------------------------------------------------


class ResearchService:
    """公开资料搜集的最小实现。只依赖注入的 `Provider` 与 `Evidence` 登记端口。

    没有 `Storage`：Research **不落任何自己的表**（没有对应的实体种类，
    也不该为此新增数据模型）。要留痕的数据一律经 `Evidence` 落库，或由
    Orchestrator 写进任务检查点。
    """

    def __init__(self, deps: ResearchDeps) -> None:
        self.deps = deps

    # ------------------------------------------------------------ 计划

    def plan_queries(
        self, ctx: CallContext, targets: list[PersonQueryTarget]
    ) -> Result[list[PlannedQuery]]:
        """按**确定性模板**生成检索计划（不调模型，Q-29）。

        每个目标产出 1~2 条计划：基础辨识词 + （若有话题）话题限定词。
        检索词可含人名与作品名，但**不得内嵌任何预设结论**（INTERFACES §5.2）。
        """
        if not targets:
            # 契约写的是 VALIDATION.empty_targets，该码尚未登记（见模块 docstring / H-14）；
            # 复用既有码，不新增。
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "检索目标不能为空（INTERFACES §5.2 记为 VALIDATION.empty_targets，"
                "该码尚未登记，暂复用 required_field）",
                trace_ref=ctx.trace_ref))

        planned: list[PlannedQuery] = []
        for index, target in enumerate(targets):
            if not isinstance(target, PersonQueryTarget):
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"第 {index} 个检索目标形状不合法：{type(target).__name__}",
                    trace_ref=ctx.trace_ref))
            entity_id = _text(target.entity_id)
            if not entity_id:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"第 {index} 个检索目标缺少 entity_id",
                    trace_ref=ctx.trace_ref))
            # 名字缺失时退回第一个别名；都没有就没法拼检索词
            name = _text(target.display_name) or (_str_list(target.aliases) or [""])[0]
            if not name:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"检索目标 {entity_id} 既无 display_name 也无 aliases，无法生成检索词",
                    trace_ref=ctx.trace_ref))

            work_hint = " ".join(_str_list(target.work_titles))
            topic = _text(target.topic)
            base_parts = [name]
            if work_hint:
                base_parts.append(work_hint)
            planned.append(PlannedQuery(
                query_text=" ".join(base_parts),
                language="zh",
                intent="identity",
                budget_cost_estimate=1,
                target_entity_ids=[entity_id]))
            if topic:
                planned.append(PlannedQuery(
                    query_text=f"{name} {topic}",
                    language="zh",
                    intent="topic",
                    budget_cost_estimate=1,
                    target_entity_ids=[entity_id]))
        return Ok(planned)

    # ------------------------------------------------------------ 检索

    def collect(self, ctx: CallContext, planned_query: PlannedQuery) -> Result[CollectOutcome]:
        """执行一次检索。

        0 条结果**不是失败**：返回 `Ok(fetch_status=NO_RESULT)`（错误处理 §2.3）。
        `Provider` 的错误（`EXTERNAL_*` / `BUDGET_EXCEEDED`）一律原样传播，不吞。
        """
        if not isinstance(planned_query, PlannedQuery):
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"collect 需要 PlannedQuery，收到 {type(planned_query).__name__}",
                trace_ref=ctx.trace_ref))
        query_text = _text(planned_query.query_text)
        if not query_text:
            return Err(new_error(
                ErrorCode.VALIDATION_QUERY_EMPTY,
                "空查询不会发起请求", trace_ref=ctx.trace_ref))

        request = SearchRequest(
            query=query_text,
            language=planned_query.language or None,
            metadata=dict(self.deps.search_metadata),
        )
        response = self.deps.provider.search(ctx, request)
        if is_err(response):
            return response
        results = list(response.value.results or [])
        usage = response.value.usage
        status = str(FetchStatus.OK) if results else FETCH_NO_RESULT
        outcome = CollectOutcome(results=results, usage=usage, fetch_status=status)
        if not results:
            return Ok(outcome, warnings=[WarningNote(
                code=WARNING_NO_RESULT,
                message=f"检索词「{query_text}」没有返回任何来源")],
                usage=usage)
        return Ok(outcome, usage=usage)

    # ------------------------------------------------------------ 过滤排序

    def filter_and_rank(
        self,
        ctx: CallContext,
        results: list[SearchResult],
        *,
        target_entity_ids: list[str] | None = None,
    ) -> Result[RankedCandidates]:
        """去重 / 去无效 / 确定性排序。

        - 空输入**不算失败**：返回空 `accepted` + 一条 `EMPTY_INPUT` 提示。
        - 被拒项**必须记录原因**，不静默丢弃。
        - 排序是确定性结构完整度（`_rank_weight_of`），并列时按去重键稳定排序——
          过程可复现，不引入随机性。
        - 合规钩子（来源白/黑名单、robots/ToS）留在这里（Q-02 待定），
          当前未启用任何名单，只保留位置。
        """
        targets = _str_list(target_entity_ids)
        ranked = RankedCandidates()
        if not results:
            ranked.warnings.append(WarningNote(
                code=WARNING_EMPTY_INPUT, message="没有候选可供过滤（不是失败）"))
            return Ok(ranked)

        seen: dict[str, RejectedCandidate] = {}
        for raw in results:
            if not isinstance(raw, SearchResult):
                ranked.rejected.append(RejectedCandidate(
                    result=SearchResult(), reason="INVALID_MATERIAL: 不是 SearchResult"))
                continue
            if not _text(raw.title):
                ranked.rejected.append(RejectedCandidate(result=raw, reason="EMPTY_TITLE"))
                continue
            key = _batch_key(raw)
            if not key:
                # Evidence 的 SC-V1 要求 canonical_key 或 content_hash 至少一个非空；
                # 两者都没有的候选登记必失败，先在过滤阶段拒掉。
                ranked.rejected.append(RejectedCandidate(
                    result=raw, reason="MISSING_LOCATOR: canonical_key 与 url 均为空"))
                continue
            if key in seen:
                ranked.rejected.append(RejectedCandidate(
                    result=raw, reason=f"DUPLICATE_IN_BATCH: 与已接受候选同键（{key}）"))
                continue
            candidate = RankedCandidate(result=raw, target_entity_ids=list(targets),
                                        rank_weight=_rank_weight_of(raw))
            seen[key] = RejectedCandidate(result=raw, reason="")  # 占位，仅用于去重
            ranked.accepted.append(candidate)

        ranked.accepted.sort(
            key=lambda c: (-c.rank_weight, _batch_key(c.result), _text(c.result.title),
                           _text(c.result.url)))
        if ranked.rejected:
            ranked.warnings.append(WarningNote(
                code=WARNING_CANDIDATE_REJECTED,
                message=f"{len(ranked.rejected)} 条候选被拒绝（原因见 rejected）"))
        if any(r.reason.startswith("DUPLICATE_IN_BATCH") for r in ranked.rejected):
            ranked.warnings.append(WarningNote(
                code=WARNING_BATCH_DEDUP, message="同批内存在重复候选，已折叠"))
        return Ok(ranked)

    # ------------------------------------------------------------ 登记

    def register(
        self, ctx: CallContext, ranked: RankedCandidates
    ) -> Result[ResearchRegisterOutcome]:
        """把候选交给 `Evidence.register_source` 登记。**自己绝不写证据表**。

        逐条登记：任意一条**失败即整体传播该错误**（不静默跳过、不伪造成功）。
        完全不去重的场景不存在——重复候选会命中原记录（幂等）。
        """
        outcome = ResearchRegisterOutcome()
        if ranked is None:
            return Ok(outcome)
        for candidate in ranked.accepted:
            draft = self._to_source_draft(candidate)
            registered = self.deps.evidence.register_source(ctx, draft)
            if is_err(registered):
                return registered
            source_id = registered.value.id
            outcome.source_ids.append(source_id)
            outcome.registered_count += 1
            outcome.materials.append(self._to_material(candidate, source_id))
            if any(w.code == WARNING_REGISTER_DEDUP for w in registered.warnings):
                outcome.dedup_hits += 1
        return Ok(outcome)

    @staticmethod
    def _to_material(candidate: RankedCandidate, source_id: str) -> SourceMaterial:
        """`RankedCandidate` + 登记得到的 `source_id` → `SourceMaterial`。

        与 `_to_source_draft` **分工不同**：
          - `SourceDraft` 只搬**来源元数据**（写进 `Source` 表）；
          - `SourceMaterial` 带上 `snippet`（材料正文摘要），交给抽取器。
        """
        result = candidate.result
        return SourceMaterial(
            source_id=source_id,
            title=_text(result.title),
            url=_text(result.url),
            canonical_key=_text(result.canonical_key) or _text(result.url),
            snippet=_text(result.snippet),
            published_at=_text(result.published_at) or None,
            source_kind=_text(result.source_kind_guess),
            subject_entity_ids=_str_list(candidate.target_entity_ids),
        )

    @staticmethod
    def _to_source_draft(candidate: RankedCandidate) -> SourceDraft:
        """`SearchResult` + 对象边界 → `SourceDraft`（喂给 Evidence 的最小形状）。

        只搬运**来源元数据**：不合成 claim、不做分级、不写 `content_ref`
        （抓取正文尚未实现）。
        """
        result = candidate.result
        locator = _text(result.canonical_key) or _text(result.url)
        return SourceDraft(
            title=_text(result.title),
            canonical_key=locator,
            kind=_coerce_source_kind(result.source_kind_guess),
            url=_text(result.url) or None,
            published_at=_fuzzy_from_text(result.published_at),
            subject_entity_ids=_str_list(candidate.target_entity_ids),
            fetch_status=FetchStatus.OK,
        )

    # ------------------------------------------------------------ 主入口

    def run(
        self,
        ctx: CallContext,
        targets: list[PersonQueryTarget],
        budget: Any | None = None,
    ) -> Result[ResearchOutput]:
        """串起 plan → collect → filter → register。

        失败纪律（INTERFACES §5.2 / ERROR_HANDLING §2.3）：
          - 单个 `PlannedQuery` 检索失败 → **记录 warning 并继续**其他查询；
          - **全部**查询都失败 → 返回 `Err`（上报，不伪装成成功）；
          - `BUDGET_EXCEEDED` → **立刻传播**（这是"暂停"信号，不是"跳过继续"）；
          - 登记失败 → 直接传播（不产生"部分成功的假象"）。

        `budget` 参数仅为与契约签名兼容而保留：**本模块不做预算记账**，
        也不查询/扣减任何账本（用户目标 7）。返回值里的 `budget_used` 是
        Provider 如实回报的用量之和，由调用方决定如何入账。
        """
        del budget  # 合约占位；预算账本归 Orchestrator / BudgetGate
        planned = self.plan_queries(ctx, targets)
        if is_err(planned):
            return planned
        queries = planned.value

        output = ResearchOutput(planned_count=len(queries))
        first_error = None
        failures = 0
        for planned_query in queries:
            collected = self.collect(ctx, planned_query)
            if is_err(collected):
                if collected.error.category == ErrorCategory.BUDGET_EXCEEDED:
                    return collected  # 暂停信号：不吞、不继续
                failures += 1
                first_error = first_error or collected.error
                output.warnings.append(WarningNote(
                    code=WARNING_QUERY_FAILED,
                    message=f"检索词「{planned_query.query_text}」失败：{collected.error}"))
                continue
            output.executed_count += 1
            outcome = collected.value
            self._add_usage(output.budget_used, outcome.usage)
            for note in collected.warnings:
                output.warnings.append(note)
            if not outcome.results:
                output.no_result_count += 1
                continue

            ranked = self.filter_and_rank(
                ctx, outcome.results, target_entity_ids=planned_query.target_entity_ids)
            if is_err(ranked):
                return ranked
            output.warnings.extend(ranked.value.warnings)
            registered = self.register(ctx, ranked.value)
            if is_err(registered):
                return registered
            output.source_ids.extend(registered.value.source_ids)
            output.event_ids.extend(registered.value.event_ids)
            output.materials.extend(registered.value.materials)
            if registered.value.source_ids:
                output.warnings.append(WarningNote(
                    code=WARNING_NO_EVENTS,
                    message="Research 只登记来源；SourceEvent 切分由 Evidence 负责"))

        if queries and failures == len(queries):
            # 全部检索都失败 → 上报（不伪装成功）
            return Err(first_error) if first_error is not None else Err(new_error(
                ErrorCode.INTERNAL, "全部检索失败但未记录到错误", trace_ref=ctx.trace_ref))
        return Ok(output, warnings=list(output.warnings))

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _add_usage(target: BudgetUsage, usage: UsageDelta | None) -> None:
        """累加 Provider 回报的用量。**未知的量不加**（None 不等于 0，D-060）。"""
        if usage is None:
            return
        target.add(requests=usage.requests, tokens_in=usage.tokens_in,
                   tokens_out=usage.tokens_out, latency_ms=usage.latency_ms)
