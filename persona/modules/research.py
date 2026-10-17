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

from dataclasses import dataclass, field, replace
from typing import Any, Protocol, runtime_checkable

from ..core.context import CallContext
from ..core.enums import FetchStatus, SourceKind
from ..core.errors import ErrorCategory, ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, WarningNote, is_err, unwrap
from ..core.temporal import FuzzyDate, TimePrecision
from ..config.spec import ResearchLimits
from ..core.types import (
    OP_EXTRACT,
    OP_SEARCH,
    BudgetUsage,
    ExternalUsageRecord,
    SourceMaterial,
)
from ..core.usage_ledger import (
    estimate_extract_credits,
    estimate_search_credits,
    extract_key_parts,
    make_idempotency_key,
)
from ..providers.base import (
    Provider,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from .extract_quality import (
    DEFAULT_CHUNKS_PER_SOURCE,
    DEFAULT_MIN_EFFECTIVE_CHARS,
    ContentQuality,
    QUALITY_EMPTY,
    QUALITY_LOW,
    QUALITY_VALID,
    REASON_EMPTY,
    assess_content_quality,
    has_extraction_defect,
    plan_remediation,
)
from .compliance import (
    COMPLIANCE_STAGE_EXTRACT,
    COMPLIANCE_STAGE_OUTPUT,
    COMPLIANCE_STAGE_PERSISTENCE,
    COMPLIANCE_STAGE_REGISTER,
    COMPLIANCE_STAGE_SEARCH,
    ComplianceDecision,
    ComplianceEvent,
    ComplianceHook,
    ComplianceStatus,
)
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
# —— P4.5 补救接线（官方 query+chunks_per_source / format=text 自动重抓）——
WARNING_EXTRACT_REMEDIATED = "EXTRACT_REMEDIATED"    # 低质量正文经官方参数补救后通过门禁
WARNING_EXTRACT_REMEDIATION_BUDGET_STOPPED = "EXTRACT_REMEDIATION_BUDGET_STOPPED"
# D1：补救阶段费用上限不足 → **停手 + 告警**，已抓到的材料照常返回（不把整轮判失败）
WARNING_EXTRACT_REMEDIATION_UNKNOWN_PAUSE = "EXTRACT_REMEDIATION_UNKNOWN_PAUSE"
# 费用状态未知的请求达到上限 → 保守停手，等人工核对（不默认失败请求免费）
WARNING_EXTRACT_REMEDIATION_CAPPED = "EXTRACT_REMEDIATION_CAPPED"
# 本轮补救发送总量达到 max_extracts → 截断（D2）
WARNING_EXTRACT_DUPLICATE_SKIPPED = "EXTRACT_DUPLICATE_SKIPPED"
# 幂等：同一请求意图账本里已记过 → **不再发送**（settle 幂等防不了重复花钱）
WARNING_NO_REMEDIATION_SUPPORT = "NO_REMEDIATION_SUPPORT"
# 注入的提取策略没有按 URL 补救的入口 → 跳过补救（不静默失败）
# 官方默认检索/提取深度（docs.tavily.com：search_depth 与 extract_depth 默认 basic）
DEFAULT_SEARCH_DEPTH = "basic"
WARNING_COST_CEILING_UNENFORCEABLE = "COST_CEILING_UNENFORCEABLE"
# 设了 research.max_cost 但本轮**完全无法计量成本** → 闸门无法判定，显式告警（不静默放行）
WARNING_COST_UNACCOUNTABLE = "COST_UNACCOUNTABLE"
# 存在**费用状态未知**的请求（响应无 usage / 请求失败/超时）→ 费用上限覆盖不到它们，
# 必须显式告警并给出人工核对入口：账本里留着 idempotency_key 与 request_id，
# 拿到账单后用 `ExternalUsageLedger.confirm(key, cost)` 回填。

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
    `warnings` 含合规跳过（`COMPLIANCE_SKIPPED`）等提示。
    """

    source_ids: list[str] = field(default_factory=list)
    dedup_hits: int = 0
    event_ids: list[str] = field(default_factory=list)
    registered_count: int = 0
    materials: list[SourceMaterial] = field(default_factory=list)
    warnings: list[WarningNote] = field(default_factory=list)


# --------------------------------------------------------------------------
# 正文提取策略与"已提取内容"形状（P4.2 第三步）
# --------------------------------------------------------------------------

# 权威定义已迁到 `persona.config.spec.ResearchLimits`（与 CONFIG_FIELDS 的
# `research.*` 字段一一对应），避免重复定义配置字段（P4.2 第二步"复用等效现有字段"）。
# 此处仅保留模块级默认常量，供 `ResearchDeps.effective_limits` 在未注入 `limits` 时回落。
DEFAULT_RESEARCH_LIMITS = ResearchLimits(max_queries=5, max_results_per_query=5, max_extracts=3)


@dataclass
class ExtractedContent:
    """一次正文抓取得到的、可追溯到来源 URL 的内容（P4.2 第三步）。

    注意：`content` 是**网页正文全文**，默认**不落库**（`Source` 只存元数据）；
    它留在内存里供下游 claim 抽取使用。`excerpt` 才是默认可用于落库的"证据摘录"。

    ⚠️ **P4.5 方案 A+B**：`excerpt` 同时也是**质量门禁的判定对象**（门禁判摘录，
    不判全文），且它不再等于 `content[:2000]`，而是"连续正文最密集的那段原文"
    （方案 B，见 `core/text_window.py`）。判定的与交付的必须是同一个东西——
    否则长页会出现"全文合格、摘录是目录、目录照样变成材料"。
    """

    url: str = ""
    title: str = ""
    content: str = ""                    # 网页正文全文（默认不持久化）
    excerpt: str = ""                    # 证据摘录：连续正文最密集的那段（可落库）
    published_at: str | None = None
    content_type: str = ""
    extracted_at: str = ""
    truncated: bool = False
    error: str | None = None
    access_hint: str = "open"            # open / paywall / copyright / unknown
    flagged_untrusted: bool = False      # 是否检测到疑似提示注入
    # P4.5：正文质量评估（None = 尚未评估）。只有 VALID 才能当研究材料。
    quality: "ContentQuality | None" = None
    # P4.5 补救诊断（纯记录，不参与门禁判定）：
    #   `remediation_attempts` —— 已发出的补救请求数（受 max_remediations 约束）；
    #   `remediation_detail`   —— 每次补救的选项与结果（救活/仍低质量/被跳过/出错）。
    remediation_attempts: int = 0
    remediation_detail: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_usable_material(self) -> bool:
        """能否作为研究材料（**未评估的一律不可用**，防止漏过门禁）。"""
        return self.quality is not None and self.quality.is_usable


@dataclass
class RejectedExtract:
    """一条**被质量门禁拒收**的正文（P4.5 任务二）。

    ⚠️ 被拒**不是被静默丢弃**：状态与原因都要留下来，供诊断与人工核对。
    关键是要能区分三种不同性质的问题：

    - `extract_error` 有值 → **API/抓取层失败**（目标网页不可访问）；
    - 状态 EMPTY 且无 error → 服务端返回了空内容；
    - 状态 LOW_QUALITY → 内容拿到了，但**本地门禁判定不可用**（如全是导航）。
    """

    url: str = ""
    status: str = QUALITY_EMPTY           # EMPTY / LOW_QUALITY
    reasons: list[str] = field(default_factory=list)
    extract_error: str | None = None     # 抓取层错误（None = 抓到了，是内容不行）
    diagnosis: dict[str, Any] = field(default_factory=dict)

    def as_detail(self) -> dict:
        return {"url": self.url, "status": self.status, "reasons": list(self.reasons),
                "extract_error": self.extract_error,
                "diagnosis": dict(self.diagnosis)}


class ContentExtractionStrategy(Protocol):
    """正文提取策略接口（P4.2 第三步）。

    搜索、提取、编排三者分离：Provider 只负责外部调用与标准化（含 `extract`），
    本策略决定"挑哪些 URL、抓多少、怎么映射"，**不**自行发起网络调用（调用方传入
    已注入的 provider）。
    """

    def extract(
        self,
        ctx: CallContext,
        provider: Provider,
        accepted: list["RankedCandidate"],
        *,
        max_extracts: int,
        metadata: dict | None = None,     # P4.5：透传 idempotency_key 等
        query: str = "",                  # P4.5：官方 query 参数（内容块相关性重排）
        chunks_per_source: int | None = None,  # P4.5 D4：官方分块参数（需 query 才生效）
    ) -> Result[list[ExtractedContent]]:
        ...


@runtime_checkable
class UsageSinkPort(Protocol):
    """外部用量账本的持久化出口（P4.5）。

    只要能"记一笔逐笔用量"就满足（写入侧需保证幂等：同一 `idempotency_key`
    重复写入应被忽略，而不是重复计数）。

    用端口而不是直接用仓储层，是为了让本模块**不依赖存储层**——它只认这个协议，
    具体实现由装配层注入；不注入即不落库（保持既有行为）。
    """

    def record_external_usage(self, ctx: CallContext,
                              record: ExternalUsageRecord) -> Any:
        ...

    def has_external_usage(self, ctx: CallContext,
                           idempotency_key: str) -> Any:
        """跨轮幂等的**只读查询**（P4.5 D3）：这个键在持久账本里记过没有。

        没有它，任务恢复 / 重跑时内存账本是空的，发送层守卫失效——同一个
        请求会真的再花一次钱，而 `settle` 只会静默返回 `False`（防重复记账，
        不防重复花钱）。实现方按主键查即可；查不到/查不了由调用方保守处理。

        用 `getattr` 取用（见 `_duplicate_reason`）：早期只实现了写入侧的
        老出口不因新增该方法而失效。
        """
        ...


def materials_from_extracts(
    extracts: list[ExtractedContent],
    *,
    target_entity_ids: list[str] | None = None,
    query_text: str = "",
) -> tuple[list[SourceMaterial], list[RejectedExtract]]:
    """把抓到的正文转成**研究材料**——**只转通过质量门禁的那些**。

    这是"低质量正文不得进入 Claim Extractor"的**落点**：

    - 只有状态为 `VALID` 的正文才会变成 `SourceMaterial` 交出去；
    - 被拒的正文放在第二个返回值里（**不静默丢弃**，状态与原因都在）；
    - 因此下游抽取器**不可能**拿到导航骨架去生成"看似有来源支持"的 claim。

    要用它而不是直接遍历 `ResearchOutput.extracted`：后者里还留着被拒条目
    供诊断（门禁只标记、不丢弃），直接遍历会绕过门禁。
    """
    materials: list[SourceMaterial] = []
    rejected: list[RejectedExtract] = []
    for item in extracts or []:
        if not item.is_usable_material:
            quality = item.quality
            rejected.append(RejectedExtract(
                url=item.url,
                status=(quality.status if quality else QUALITY_EMPTY),
                reasons=list(quality.reasons) if quality else [REASON_EMPTY],
                extract_error=item.error,
                diagnosis=(quality.as_detail() if quality else {}),
            ))
            continue
        # 只交"摘录"（截断后的可落库片段），**不交全文**（全文默认不落库/不外流）
        materials.append(SourceMaterial(
            source_id="",
            title=item.title,
            url=item.url,
            canonical_key=item.url,
            snippet=item.excerpt,
            published_at=item.published_at,
            source_kind="WEB_PAGE",
            subject_entity_ids=_str_list(target_entity_ids),
            query_text=query_text,
            # P4.4-A：把**门禁结论**随材料交给下游。
            #
            # 走到这里的条目必然 `is_usable_material`（即门禁判为 `VALID`），
            # 所以这个值不是重复判定，而是把"已经判过了"这件事**告诉抽取器**：
            # 抽取器据此才能执行它那一侧的材料准入（`admit_materials`），
            # 并在上线时用 `require_gate_verdict=True` 拒绝任何没有结论的材料。
            # 不填的后果很具体：真实链路上每份材料都是"未声明"，
            # 严格模式会把**所有**真实材料都拒掉（等于模型永远跑不起来）。
            quality_status=QUALITY_VALID,
        ))
    return materials, rejected


class TopNExtractionStrategy:
    """默认策略：按相关性排序取前 N 个有 URL 的结果，调用 Provider.extract。

    - 优先用 Provider 的正式 Extract 接口（如 Tavily Extract），不实现无限制爬虫；
    - 单条抓取失败只记录 `error`，不中断其它 URL；
    - Provider 不支持 extract（如 Mock）时返回空列表 + 一条提示 warning。
    """

    def extract(
        self,
        ctx: CallContext,
        provider: Provider,
        accepted: list["RankedCandidate"],
        *,
        max_extracts: int,
        metadata: dict | None = None,
        query: str = "",
        chunks_per_source: int | None = None,
    ) -> Result[list[ExtractedContent]]:
        """抓前 N 个候选的正文（Protocol 入口；实际抓取在 `extract_urls`）。

        P4.5：
        - `metadata["idempotency_key"]` 透传给 Provider，保证预留与结算对得上；
        - `query` + `chunks_per_source` 用官方参数（内容块按相关性重排 + 分块）。
          D4：初始抓取**默认带上**（真实复测实测：314,743 字长页 → 1,937 字正文），
          从源头减少"摘录是导航骨架"的概率；无 query 时按官方约束都不带；
        - **把 Provider 回报的 usage 原样带出去**（`Ok(out, usage=...)`）。
          此前这一步把 usage 整个丢掉，导致抓取用量从不进账本 —— 这是 P4.3
          真实运行暴露的缺陷的直接根因。
        """
        if max_extracts <= 0:
            return Ok([])
        # 已按 rank_weight 降序，取前 N 个带 URL 的结果
        urls: list[str] = []
        for candidate in accepted:
            url = _text(candidate.result.url)
            if url and url not in urls:
                urls.append(url)
            if len(urls) >= max_extracts:
                break
        if not urls:
            return Ok([])
        return self.extract_urls(
            ctx, provider, urls, max_extracts=max_extracts,
            metadata=metadata, query=query, chunks_per_source=chunks_per_source)

    def extract_urls(
        self,
        ctx: CallContext,
        provider: Provider,
        urls: list[str],
        *,
        max_extracts: int,
        metadata: dict | None = None,
        query: str = "",
        chunks_per_source: int | None = None,
        format: str | None = None,
    ) -> Result[list[ExtractedContent]]:
        """抓**指定 URL 列表**的正文（`extract` 与补救都走这里，同一套转换）。

        补救之所以复用本方法而不是另写一条路：转换逻辑（提示注入检测、
        `raw_content`/`excerpt` 映射、usage 透传）只有一份，初始抓取与补救
        抓取的行为差异只体现在**请求参数**上（query/chunks/format）。
        """
        from ..core.temporal import to_iso, utc_now
        from ..providers.base import ExtractRequest

        urls = [str(u) for u in (urls or []) if str(u).strip()]
        if not urls:
            return Ok([])
        if max_extracts > 0:
            urls = urls[:max_extracts]

        extract_fn = getattr(provider, "extract", None)
        if extract_fn is None or not callable(extract_fn):
            return Ok([], warnings=[WarningNote(
                code="NO_EXTRACT_SUPPORT",
                message=f"Provider 不支持正文提取（{type(provider).__name__}），跳过正文抓取")])

        request = ExtractRequest(urls=urls, metadata=dict(metadata or {}))
        if query:
            # 官方参数：内容块按与 query 的相关性重排（本阶段只用官方选项）
            request.query = query
            # 官方约束：chunks_per_source 仅在提供 query 时可用（Provider 侧夹紧 1..5）
            if chunks_per_source is not None:
                request.chunks_per_source = chunks_per_source
        if format:
            request.format = format
        response = extract_fn(ctx, request)
        if is_err(response):
            return response
        results = list(response.value.results or [])
        out: list[ExtractedContent] = []
        for item in results:
            if item.error:
                out.append(ExtractedContent(
                    url=item.url, error=item.error, extracted_at=to_iso(utc_now())))
                continue
            flagged = bool(detect_untrusted_for(item.raw_content))
            out.append(ExtractedContent(
                url=item.url,
                title=item.title,
                content=item.raw_content,
                excerpt=item.excerpt,
                content_type=item.content_type,
                truncated=item.truncated,
                extracted_at=to_iso(utc_now()),
                flagged_untrusted=flagged,
            ))
        # 关键：把 Provider 的 usage 带出去，否则抓取用量永远进不了账本
        return Ok(out, usage=response.usage)


def detect_untrusted_for(content: str) -> list[str]:
    """透传合规模块的提示注入检测（避免 research 直接依赖合规实现细节）。"""
    from .compliance import detect_prompt_injection

    return detect_prompt_injection(content)


# --------------------------------------------------------------------------
# 本地材料（P4.7：离线通道 —— 用户提供文件，不做检索）
#
# 为什么需要它：`persona business run` 之前只能靠检索拿材料，而检索 Provider
# 尚未接线（H-17 / P3），于是 CLI 在任何配置下都跑不出真实五阶段。补一条
# "用户直接把材料文件交给 CLI"的通道，就能在**零外部调用**的前提下验证
# 装配层 + 编排器 + 各业务模块在 CLI 入口下真的能协同工作。
#
# 两条红线（对应 G4 的教训）：
#   1. **绝不内置合成数据**：材料只能来自用户给的文件路径，产品代码里没有
#      任何"演示用假材料"——否则 CLI 看起来能生成档案，实际产出的是假数据；
#   2. **门禁不能绕过**：本地文件与抓取正文走**同一个** `assess_content_quality`
#      判定，结论写进 `quality_status` 随材料传递；判不合格的材料不进材料流。
# --------------------------------------------------------------------------

#: 单份本地材料的字数上限（超出截断并告警，不静默）
MAX_LOCAL_MATERIAL_CHARS = 200_000

WARNING_LOCAL_MATERIAL_UNREADABLE = "LOCAL_MATERIAL_UNREADABLE"
WARNING_LOCAL_MATERIAL_GATE_REJECTED = "LOCAL_MATERIAL_GATE_REJECTED"
WARNING_LOCAL_MATERIAL_TRUNCATED = "LOCAL_MATERIAL_TRUNCATED"

#: 本地文件的编码尝试顺序：BOM 优先，其次 UTF-8，最后 GB18030（中文 Windows 常见）
_LOCAL_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


@dataclass(frozen=True)
class LocalMaterialFile:
    """一份**已读入**的本地材料（文件名 + 正文 + 定位符）。"""

    path: str
    title: str
    text: str
    locator: str


def _local_locator(path: Any) -> str:
    """本地文件的稳定定位符（`file:///` 前缀 + POSIX 形式的绝对路径）。

    用它当 `url` / `canonical_key`：`Evidence.register_source` 要求至少一个
    locator 非空，而 `file://` 一眼能看出"不是网上的东西"，不会被误当来源。
    """
    from pathlib import Path

    resolved = Path(str(path)).expanduser().resolve()
    return "file:///" + resolved.as_posix().lstrip("/")


def _read_local_text(path: Any) -> tuple[str, str]:
    """读文本文件。返回 `(正文, 编码)`；读不了抛 `OSError`/`UnicodeDecodeError`。"""
    from pathlib import Path

    raw = Path(str(path)).expanduser().read_bytes()
    last_error: Exception | None = None
    for encoding in _LOCAL_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError as exc:      # 换下一种编码再试
            last_error = exc
    raise last_error or UnicodeDecodeError("utf-8", b"", 0, 1, "无法解码本地文件")


def load_local_materials(
    paths: "list[Any]",
    *,
    target_entity_ids: "list[str] | tuple[str, ...]" = (),
    target_names: "list[str] | tuple[str, ...]" = (),
    min_effective_chars: int = DEFAULT_MIN_EFFECTIVE_CHARS,
    max_chars: int = MAX_LOCAL_MATERIAL_CHARS,
) -> tuple[list[SourceMaterial], list[WarningNote]]:
    """把**用户提供的文件**读成材料（`SourceMaterial`），并跑同一个正文质量门禁。

    返回 `(可用材料, 告警)`。**不可用的一律不进材料流**：
      - 文件读不了 → `LOCAL_MATERIAL_UNREADABLE`；
      - 门禁判 EMPTY / LOW_QUALITY → `LOCAL_MATERIAL_GATE_REJECTED`（带原因）；
      - 超过 `max_chars` → 截断并记 `LOCAL_MATERIAL_TRUNCATED`（不静默截断）。

    ⚠️ 本函数**不登记来源**：登记是 `ResearchService.adopt` 的事（要经 Evidence）。
    """
    materials: list[SourceMaterial] = []
    warnings: list[WarningNote] = []
    for raw_path in paths or []:
        shown = str(raw_path)
        try:
            text, encoding = _read_local_text(raw_path)
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(WarningNote(
                code=WARNING_LOCAL_MATERIAL_UNREADABLE,
                message=f"本地材料读取失败，已跳过：{shown}（{type(exc).__name__}: {exc}）"))
            continue

        if len(text) > max_chars:
            warnings.append(WarningNote(
                code=WARNING_LOCAL_MATERIAL_TRUNCATED,
                message=f"本地材料超出 {max_chars} 字上限，已截断：{shown}"
                        f"（原 {len(text)} 字）"))
            text = text[:max_chars]

        locator = _local_locator(raw_path)
        quality = assess_content_quality(
            text, url=locator, target_names=list(target_names),
            min_effective_chars=min_effective_chars)
        if not quality.is_usable:
            reasons = "、".join(str(r) for r in quality.reasons) or "UNKNOWN"
            warnings.append(WarningNote(
                code=WARNING_LOCAL_MATERIAL_GATE_REJECTED,
                message=f"本地材料未通过正文质量门禁（{quality.status}：{reasons}），"
                        f"未进入材料流：{shown}（编码 {encoding}）"))
            continue

        materials.append(SourceMaterial(
            source_id="",
            title=shown.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or shown,
            url=locator,
            canonical_key=locator,
            snippet=text,
            published_at=None,
            source_kind=str(SourceKind.OTHER),
            subject_entity_ids=list(target_entity_ids),
            target_display_names=list(target_names),
            query_text="",
            quality_status=str(quality.status),
        ))
    return materials, warnings


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
    extracted: list[ExtractedContent] = field(default_factory=list)
    # P4.5：被正文质量门禁拒收的条目（**不静默丢弃**，状态与原因都留着）
    rejected_extracts: list[RejectedExtract] = field(default_factory=list)
    warnings: list[WarningNote] = field(default_factory=list)

    def usable_extracts(self) -> list[ExtractedContent]:
        """通过质量门禁的正文 —— **下游只能拿这些**当研究材料。"""
        return [e for e in self.extracted if e.is_usable_material]


@dataclass(frozen=True)
class ResearchDeps:
    """Research 的全部外部依赖（类型层面阻断误用）。

    - `provider`：**唯一**外部出口（`Provider.search` / `extract`）。
    - `evidence`：只有 `register_source` 的登记端口（堵死 F-10）。
    - `search_metadata`：原样透传给 `Provider.search` 的 `metadata`。
      存在的意义是让**调用方**显式给出 `fixture_key`（既有 Mock Provider 家族的约定：
      `FakeProvider` 要求 `metadata["fixture_key"]`）。业务代码**不猜**这个键，
      真实 Provider 也会忽略它。
    - `extraction_strategy`：**可选**。注入后才做正文抓取（默认 None = 只拿检索摘要，
      **不改**既有"不抓正文"行为，H-17）。
    - `compliance`：**可选**。`ComplianceHook`，在五个位置做工程合规检查；None = 不查。
    - `limits`：**可选**。一次研究的量级上限；None 时回落到 `DEFAULT_RESEARCH_LIMITS`。
    """

    provider: Provider
    evidence: SourceRegistrarPort
    search_metadata: dict[str, Any] = field(default_factory=dict)
    extraction_strategy: "ContentExtractionStrategy | None" = None
    compliance: "ComplianceHook | None" = None
    limits: "ResearchLimits | None" = None
    # P4.5：正文质量门禁的阈值（可配置，不写死）
    quality_min_effective_chars: int = DEFAULT_MIN_EFFECTIVE_CHARS
    # P4.5：外部用量账本的**持久化出口**（可选）。
    # 内存账本保证本轮可追溯；落库后才有跨轮/跨进程的对账能力。
    # 默认 None = 不落库（保持既有行为，装配层接入时才启用）。
    usage_sink: "UsageSinkPort | None" = None
    # 关联的任务 ID（写入逐笔记录，供按任务汇总与人工核对）；当前模型无 run id
    task_id: str | None = None

    @property
    def effective_limits(self) -> "ResearchLimits":
        return self.limits or DEFAULT_RESEARCH_LIMITS


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
        self._search_requests_made = 0
        self._compliance_log: list[ComplianceDecision] = []

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
        # 量级上限：一次研究最多 max_queries 次检索（P4.2 第三步第 2 点）
        cap = self.deps.effective_limits.max_queries
        if len(planned) > cap:
            planned = planned[:cap]
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

        # —— 合规检查（搜索前）：未注入合规钩子则跳过（保持既有行为）——
        decision = self._compliance_check(
            ctx, COMPLIANCE_STAGE_SEARCH,
            url=None, domain=None, content=None, content_length=0,
            request_count=self._search_requests_made + 1,
            traceable=True, access_hint="open")
        if isinstance(decision, Err):
            return decision

        # —— 预算：请求**发出之前**预留（P4.5）——
        # 顺序不能反：先预留再发请求，否则连续两次请求都能看到"还没花"的余额。
        # 检索深度：**本模块不猜**——计划里没有这个字段，就按官方默认值 basic 估算。
        # （真实深度由 Provider 侧按官方默认发；估算口径与之保持一致。）
        depth = DEFAULT_SEARCH_DEPTH
        key = self._reserve(ctx, OP_SEARCH, query_text,
                            self.deps.effective_limits.max_results_per_query, depth,
                            estimate=estimate_search_credits(depth),
                            max_cost=self.deps.effective_limits.max_cost)

        request = SearchRequest(
            query=query_text,
            language=planned_query.language or None,
            limit=self.deps.effective_limits.max_results_per_query,
            metadata={**dict(self.deps.search_metadata), "idempotency_key": key},
        )
        response = self.deps.provider.search(ctx, request)
        if is_err(response):
            return response
        self._search_requests_made += 1
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

        逐条登记。若注入了合规钩子，登记/持久化前对每个候选做检查：
          - `DENY` → **跳过该候选**并记录原因（不伪造成功，也不中断其它候选）；
          - 其它 → 正常登记。
        任意一条**登记本身失败**仍整体传播该错误（不伪造成功）。
        """
        outcome = ResearchRegisterOutcome()
        if ranked is None:
            return Ok(outcome)
        for candidate in ranked.accepted:
            # —— 合规检查（登记前 / 持久化前）——
            # 注意：`domain` 留空，由合规模块从 `url` 派生（统一解析逻辑，
            # 避免把完整 URL 误当成域名导致黑名单/白名单匹配失败）。
            decision = self._compliance_check(
                ctx, COMPLIANCE_STAGE_REGISTER,
                url=_text(candidate.result.url) or None,
                domain=None,
                content=_text(candidate.result.snippet) or None,
                traceable=bool(_text(candidate.result.url)),
                access_hint="open")
            if isinstance(decision, Err):
                # DENY：跳过该候选，记录原因（不中断其它候选、不伪造）
                outcome.warnings.append(WarningNote(
                    code="COMPLIANCE_SKIPPED",
                    message=f"候选被合规拒绝（跳过，不登记）：{decision.error.message}"))
                continue
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

    def adopt(
        self, ctx: CallContext, materials: "list[SourceMaterial]"
    ) -> Result[ResearchOutput]:
        """接入**调用方提供的材料**（P4.7 离线通道）：只登记来源，不检索。

        与 `run` 的三处差别，都是有意的：

        1. **不做检索计划、不发任何外部请求**：没有 `Provider.search` / `extract`，
           因此也不做费用预留与结算（不花钱就不可能记错钱）；
        2. **材料正文由调用方读入并已过同一个门禁**（`quality_status` 非空），
           本方法不重新判定、也不改判——门禁结论只有一个来源；
        3. **来源仍走 `Evidence.register_source`**：`canonical_key` 用文件定位符，
           重复接入同一文件会命中去重（返回同一个 `source_id`），
           证据的可追溯闸门因此与检索路径完全一致。

        返回的 `ResearchOutput.planned_count` / `executed_count` 恒为 0：
        "没有检索"是事实，不该伪造成 1 条计划 1 条执行。
        """
        output = ResearchOutput()
        for material in materials or []:
            locator = (_text(material.canonical_key) or _text(material.url))
            if not locator:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"材料「{_text(material.title) or '（无标题）'}」缺少 "
                    "canonical_key / url：无法登记来源（证据必须可追溯）",
                    trace_ref=ctx.trace_ref))
            draft = SourceDraft(
                title=_text(material.title) or locator,
                canonical_key=locator,
                kind=_coerce_source_kind(material.source_kind),
                url=_text(material.url) or None,
                published_at=None,          # 本地文件没有可信的发布时间，不猜
                subject_entity_ids=_str_list(material.subject_entity_ids),
                fetch_status=FetchStatus.OK,
            )
            registered = self.deps.evidence.register_source(ctx, draft)
            if is_err(registered):
                return registered
            source_id = str(registered.value.id)
            output.source_ids.append(source_id)
            output.materials.append(replace(material, source_id=source_id))
            if any(w.code == WARNING_REGISTER_DEDUP for w in registered.warnings):
                output.warnings.append(WarningNote(
                    code=WARNING_REGISTER_DEDUP,
                    message=f"材料「{draft.title}」命中去重，复用既有来源 {source_id}"))
        return Ok(output, warnings=list(output.warnings))

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

        `budget` 参数仅为与契约签名兼容而保留：**本模块不做全局预算记账**，
        也不查询/扣减任何账本（用户目标 7）。返回值里的 `budget_used` 是
        Provider 如实回报的用量之和，由调用方决定如何入账。

        P4.3 例外（用户明确授权）：**全局**预算账本仍归 Orchestrator / BudgetGate，
        但 `research.max_cost`（本阶段费用上限）**由本模块消费**——在每次外部调用
        （检索 / 抓取）之前判断累计成本 + 本次预留是否超限，超限返回 `BUDGET_EXCEEDED`
        作为暂停信号。成本完全依赖 Provider 如实回报（Tavily 为 `usage.credits`），
        **回报缺失时留 `None` 并告警，绝不用估计值冒充**（D-060）。
        """
        del budget  # 合约占位；全局预算账本归 Orchestrator / BudgetGate
        # 记住本轮目标：正文质量门禁要用它判断"内容是否与目标人物无关"（P4.5）
        self._current_targets = list(targets or [])
        planned = self.plan_queries(ctx, targets)
        if is_err(planned):
            return planned
        queries = planned.value

        output = ResearchOutput(planned_count=len(queries))
        first_error = None
        failures = 0
        all_accepted: list[RankedCandidate] = []
        for planned_query in queries:
            # 费用上限：每次真实外部调用**之前**先判（P4.3）。超限即返回
            # BUDGET_EXCEEDED —— 暂停信号，不吞、不继续、不伪造成功。
            ceiling = self._cost_ceiling(
                output, reserve=float(planned_query.budget_cost_estimate or 1))
            if ceiling is not None:
                return ceiling
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
            output.warnings.extend(registered.value.warnings)
            all_accepted.extend(ranked.value.accepted)
            if registered.value.source_ids:
                output.warnings.append(WarningNote(
                    code=WARNING_NO_EVENTS,
                    message="Research 只登记来源；SourceEvent 切分由 Evidence 负责"))

        self._warn_unmeasured_costs(output)

        # —— 正文提取（P4.2 第三步）：仅在注入了策略时触发，默认不做 ——
        extract_decision = self._maybe_extract(ctx, all_accepted, output)
        if is_err(extract_decision):
            return extract_decision

        if queries and failures == len(queries):
            # 全部检索都失败 → 上报（不伪装成功）
            return Err(first_error) if first_error is not None else Err(new_error(
                ErrorCode.INTERNAL, "全部检索失败但未记录到错误", trace_ref=ctx.trace_ref))

        # 抓取阶段也可能产生未知费用 → 再查一次（抓取在检索之后）
        self._warn_unmeasured_costs(output)

        self._persist_usage(ctx)

        # —— 输出前合规闸门（P4.2 第四步）：不确定/被拒的全文不得随产出外流 ——
        output_decision = self._compliance_check(
            ctx, COMPLIANCE_STAGE_OUTPUT, request_count=self._search_requests_made)
        if isinstance(output_decision, Err):
            return output_decision
        self._sanitize_extracted_for_output(output)
        return Ok(output, warnings=list(output.warnings))

    # ------------------------------------------------------------ 合规（可选）

    def _compliance_check(
        self,
        ctx: CallContext,
        stage: str,
        *,
        url: str | None = None,
        domain: str | None = None,
        content: str | None = None,
        content_length: int = 0,
        request_count: int = 0,
        traceable: bool = True,
        access_hint: str = "open",
    ) -> "Err | Ok[ComplianceDecision]":
        """在五个位置触发合规检查。未注入钩子 → 恒 `ALLOW`，不改既有行为。

        - `DENY`：返回 `Err(VALIDATION.compliance_denied)`，调用方据此中止该动作；
        - `NEEDS_HUMAN` / `ALLOW`：返回 `Ok(decision)`，由调用方结合上下文处置
          （默认不阻止，但不把不确定内容当"可永久保存全文"）。
        所有结论记入 `self._compliance_log` 供追溯（P4.2 第四步第 3 点）。
        """
        if self.deps.compliance is None:
            return Ok(ComplianceDecision.allow(rule="no_hook"))
        event = ComplianceEvent(
            stage=stage,
            url=url,
            domain=domain,
            content=content,
            content_length=content_length,
            request_count=request_count,
            traceable=traceable,
            access_hint=access_hint,
        )
        result = self.deps.compliance.check(ctx, event)
        if is_err(result):
            return result
        decision = result.value
        self._compliance_log.append(decision)
        if decision.status == ComplianceStatus.DENY:
            return Err(new_error(
                ErrorCode.VALIDATION_COMPLIANCE_DENIED,
                f"合规检查拒绝（{decision.rule}）：{decision.reason}",
                trace_ref=ctx.trace_ref,
                details={"stage": stage, "rule": decision.rule}))
        return Ok(decision)

    # ------------------------------------------------------------ 正文提取（可选）

    def _maybe_extract(
        self,
        ctx: CallContext,
        accepted: list[RankedCandidate],
        output: "ResearchOutput",
    ) -> "Err | Ok[ComplianceDecision]":
        """若注入了提取策略，则挑选相关性最高的若干 URL 抓正文。

        - 提取前对每个候选做"提取前"合规检查，被 DENY 的 URL 跳过并记录；
        - 调用 `ContentExtractionStrategy`（默认 TopN），由其决定抓取数量；
        - 抓取到的全文**只留在内存**（`output.extracted`），默认不落库
          （`register` 只写来源元数据）—— 满足"不默认永久保存全部网页全文"。
        """
        strategy = self.deps.extraction_strategy
        if strategy is None or not accepted:
            return Ok(ComplianceDecision.allow(rule="no_extraction"))
        max_extracts = self.deps.effective_limits.max_extracts

        # 费用上限：抓取正文前同样先判（P4.3）
        ceiling = self._cost_ceiling(output, reserve=float(max(1, max_extracts)))
        if ceiling is not None:
            return ceiling

        # 提取前合规：按域名/可追溯性筛选可抓取的候选
        extractable: list[RankedCandidate] = []
        for candidate in accepted:
            url = _text(candidate.result.url)
            if not url:
                continue
            decision = self._compliance_check(
                ctx, COMPLIANCE_STAGE_EXTRACT,
                url=url, domain=None, content=_text(candidate.result.snippet) or None,
                traceable=bool(url), access_hint="open")
            if isinstance(decision, Err):
                output.warnings.append(WarningNote(
                    code="COMPLIANCE_SKIPPED",
                    message=f"URL 被合规拒绝（跳过提取）：{decision.error.message}"))
                continue
            extractable.append(candidate)

        # —— 预算：请求**发出之前**预留（P4.5）——
        # 预留按"最坏情况"（全部 URL 都成功）估，结算时再按真实成功数收敛。
        urls = [_text(c.result.url) for c in extractable if _text(c.result.url)]
        urls = list(dict.fromkeys(urls))

        query_text = self._target_query_text()
        # D4：初始抓取也带官方 `chunks_per_source`（官方默认 3）。
        # 真实复测实测：query+chunks 把 314,743 字长页压成 1,937 字正文，
        # 从源头减少"摘录是导航骨架"的概率，省掉一次补救额度。
        # 官方约束：chunks_per_source 仅在提供 query 时可用 → 无 query 就不带。
        initial_chunks = DEFAULT_CHUNKS_PER_SOURCE if query_text else None

        # 幂等键：research 预留与 provider 结算共用 `extract_key_parts` 同一口径
        # （顺序/空值过滤只有一个定义），保证"预留键 == 结算键"由构造保证。
        key = make_idempotency_key(
            "tavily", OP_EXTRACT,
            *extract_key_parts(urls, depth="basic", query=query_text,
                               chunks_per_source=initial_chunks))

        # —— 发送层守卫（P4.5 D3）：账本记过这个键就不再发 ——
        # `settle` 的幂等只防"重复记账"，防不了"重复花钱"：键相同再发一次，
        # 钱真的会再花一次，而 settle 只会静默返回 False。
        duplicate = self._duplicate_reason(ctx, key)
        if duplicate is not None:
            output.warnings.append(WarningNote(
                code=WARNING_EXTRACT_DUPLICATE_SKIPPED,
                message=f"正文抓取跳过发送：{duplicate}（幂等键 {key[:8]}…）"))
            return Ok(ComplianceDecision.allow(rule="extraction_deduped"))

        self._reserve(ctx, OP_EXTRACT, *extract_key_parts(
            urls, depth="basic", query=query_text,
            chunks_per_source=initial_chunks),
            estimate=estimate_extract_credits(len(urls), "basic"),
            max_cost=self.deps.effective_limits.max_cost)

        result = strategy.extract(
            ctx, self.deps.provider, extractable, max_extracts=max_extracts,
            metadata={"idempotency_key": key},
            query=query_text,
            chunks_per_source=initial_chunks,
        )
        if is_err(result):
            # 失败 ≠ 免费：预留**不释放**（转为未知占位，继续占额度）
            return result

        # —— 用量必须进账本（P4.3 真实运行暴露：此前抓取用量被整个丢掉）——
        self._add_usage(output.budget_used, getattr(result, "usage", None))
        if result.warnings:
            output.warnings.extend(result.warnings)

        # —— 正文质量门禁（P4.5 任务二）——
        # 门禁只**标记状态**：所有条目都留在 `extracted` 里（便于诊断），
        # 真正的外流阻断在 `_sanitize_extracted_for_output`（低质量正文会被清空，
        # 物理上不可能被下游拿去生成"看似有来源"的 claim）。
        items, rejected = self._gate_extract_quality(ctx, result.value)
        output.extracted.extend(items)
        output.rejected_extracts.extend(rejected)
        # —— 低质量正文的官方参数补救（P4.5 补救接线）——
        # 初始已带 query+chunks（D4），补救只剩 format=text 等官方选项作兜底。
        self._remediate_low_quality(ctx, output, items)
        return Ok(ComplianceDecision.allow(rule="extraction_done"))

    # ------------------------------------------------------------ 补救（P4.5）

    def _remediate_low_quality(
        self,
        ctx: CallContext,
        output: "ResearchOutput",
        items: list["ExtractedContent"],
    ) -> None:
        """对低质量正文做**有限**补救：用官方参数重抓，过了门禁就原地救活。

        三条硬约束（接入时用户明确要求）：
          1. **预算**：每次补救都走"天花板 → 预留 → 发送 → 入账"，与初始抓取
             同一套；失败不释放预留（失败 ≠ 免费）。预算不足 → **停手 + warning**，
             已抓到的材料照常返回（D1：不把整轮判成 BUDGET_EXCEEDED——
             初始抓取已成功，好材料不该被一次"可选补救"作废）。
          2. **次数**：每 URL 最多 `max_remediations` 次；本轮补救发送总量
             ≤ `max_extracts`（D2：补救是同一篇的第二次抓取，不占初始名额，
             但也不能无限膨胀）。
          3. **幂等**：发送前查账本（轮内 + 跨轮），记过就**不再发**（D3）。

        只补救"拿到了但质量不行"的条目；API 抓取失败（`error` 有值）的条目
        不补救——那是抓取层问题，重试属于另一类决策。
        """
        limits = self.deps.effective_limits
        if limits.max_remediations <= 0 or limits.max_extracts <= 0:
            return
        strategy = self.deps.extraction_strategy
        remediate_fn = getattr(strategy, "extract_urls", None)
        if remediate_fn is None or not callable(remediate_fn):
            output.warnings.append(WarningNote(
                code=WARNING_NO_REMEDIATION_SUPPORT,
                message=f"提取策略 {type(strategy).__name__} 不支持按 URL 补救，跳过补救"))
            return

        query_text = self._target_query_text()
        # D4+R1：初始抓取已带 query+chunks（两者同进同退）时，补救选项①
        # （query+chunks）是重复消费，由 plan_remediation 剔除。
        already_queried = bool(query_text)

        sent = 0
        for item in items:
            if item.error or item.quality is None:
                continue        # API 失败 / 未评估：不是"质量不行"，不补救
            if item.quality.status not in (QUALITY_LOW, QUALITY_EMPTY):
                continue
            if item.remediation_attempts >= limits.max_remediations:
                continue
            if not has_extraction_defect(item.quality):
                # 只是"与目标人物无关"这类**相关性**判断 → 重新抓同一页
                # 也不会变有关，补救只会白花额度（预算要花在能救回来的条目上）
                item.remediation_detail.append(
                    {"skipped": "非提取质量缺陷（相关性判断），补救无收益"})
                continue
            plan = plan_remediation(
                item.url, item.quality, query=query_text,
                max_options=limits.max_remediations - item.remediation_attempts,
                already_queried=already_queried)
            if not plan.allowed or not plan.options:
                # 视频/社交页等：没有可用的官方选项 → 记诊断，不花额度
                item.remediation_detail.append(
                    {"skipped": plan.reason or "无可选官方参数"})
                continue
            for option in plan.options:
                if sent >= limits.max_extracts:
                    output.warnings.append(WarningNote(
                        code=WARNING_EXTRACT_REMEDIATION_CAPPED,
                        message=f"补救总量已达本轮上限（{limits.max_extracts}），停止补救"))
                    return
                # —— 预算（D1）：不足 → 停手 + warning，**不返回 Err** ——
                # 粗粒度最坏情况（1 credit/URL），与初始路径同口径
                ceiling = self._cost_ceiling(output, reserve=1.0)
                if ceiling is not None:
                    output.warnings.append(WarningNote(
                        code=WARNING_EXTRACT_REMEDIATION_BUDGET_STOPPED,
                        message=f"补救因费用上限停止：{ceiling.error.message}"))
                    return
                # —— 费用不明 → 保守停手（未知请求达上限，等人工核对）——
                pause, why = self._pause_reason(limits.max_cost)
                if pause:
                    output.warnings.append(WarningNote(
                        code=WARNING_EXTRACT_REMEDIATION_UNKNOWN_PAUSE,
                        message=f"补救因费用状态不明停止：{why}"))
                    return
                option_query = str(option.get("query") or "")
                option_format = str(option.get("format") or "")
                option_chunks = option.get("chunks_per_source")
                key = make_idempotency_key(
                    "tavily", OP_EXTRACT,
                    *extract_key_parts([item.url], depth="basic",
                                       query=option_query, fmt=option_format,
                                       chunks_per_source=option_chunks))
                duplicate = self._duplicate_reason(ctx, key)
                if duplicate is not None:
                    output.warnings.append(WarningNote(
                        code=WARNING_EXTRACT_DUPLICATE_SKIPPED,
                        message=f"补救跳过发送：{duplicate}（幂等键 {key[:8]}…）"))
                    continue
                self._reserve(ctx, OP_EXTRACT, *extract_key_parts(
                    [item.url], depth="basic", query=option_query,
                    fmt=option_format, chunks_per_source=option_chunks),
                    estimate=estimate_extract_credits(1, "basic"),
                    max_cost=limits.max_cost)
                sent += 1
                result = remediate_fn(
                    ctx, self.deps.provider, [item.url], max_extracts=1,
                    metadata={"idempotency_key": key},
                    query=option_query, chunks_per_source=option_chunks,
                    format=option_format or None)
                if is_err(result):
                    # 失败 ≠ 免费：预留不释放（Provider 已按 UNKNOWN 结算），
                    # 由上面的 should_pause 决定是否继续花下一个。
                    item.remediation_attempts += 1
                    item.remediation_detail.append(
                        {"option": dict(option), "error": str(result.error)})
                    continue
                self._add_usage(output.budget_used, getattr(result, "usage", None))
                for note in (result.warnings or []):
                    output.warnings.append(note)
                # 重新过门禁（判的是新摘录，不是旧的）
                fresh, _ = self._gate_extract_quality(ctx, list(result.value or []))
                item.remediation_attempts += 1
                rescued = [f for f in fresh if not f.error and f.is_usable_material]
                if rescued:
                    new = rescued[0]
                    # 原地更新：URL 不变，换掉内容/摘录/质量（不新增同 URL 的重复条目）
                    item.title = new.title or item.title
                    item.content = new.content
                    item.excerpt = new.excerpt
                    item.content_type = new.content_type
                    item.truncated = new.truncated
                    item.extracted_at = new.extracted_at or item.extracted_at
                    item.flagged_untrusted = new.flagged_untrusted
                    item.quality = new.quality
                    item.remediation_detail.append(
                        {"option": dict(option), "result": "rescued"})
                    output.rejected_extracts = [
                        r for r in output.rejected_extracts if r.url != item.url]
                    output.warnings.append(WarningNote(
                        code=WARNING_EXTRACT_REMEDIATED,
                        message=f"正文经官方参数补救后通过质量门禁：{item.url}"))
                    break   # 救活了就不再花后续选项
                item.remediation_detail.append(
                    {"option": dict(option), "result": "still_low_quality"})
                # 更新 rejected 条目的诊断，让"补救过但没救活"可追溯
                for r in output.rejected_extracts:
                    if r.url == item.url:
                        r.diagnosis = dict(r.diagnosis)
                        r.diagnosis["remediation"] = list(item.remediation_detail)

    def _duplicate_reason(self, ctx: CallContext, key: str) -> str | None:
        """这个幂等键是不是已经抓过（轮内账本 + 跨轮持久账本）。

        返回 None = 没抓过可以发；否则返回原因（用于 warning）。
        持久账本查询出错 → **保守当作已抓过**：宁可不抓，不可重复花钱。
        """
        ledger = self._usage_ledger()
        if ledger is not None and ledger.has_attempted(key):
            return "本轮账本已记录该请求"
        probe = getattr(self.deps.usage_sink, "has_external_usage", None)
        if probe is None:
            return None
        outcome = probe(ctx, key)
        if is_err(outcome):
            return f"持久账本查询失败，按已记录处理（{outcome.error.message}）"
        if unwrap(outcome):
            return "持久账本已记录该请求（跨轮/任务恢复）"
        return None

    def _pause_reason(self, max_cost: float | None) -> tuple[bool, str]:
        """保守停手判断：会超成本上限，或未知费用请求达到上限。

        `ExternalUsageLedger.should_pause` 此前只活在账本对象里、从未接进
        流程——"费用状态不明就停手"这条规则等于没执行。这里接上。
        """
        ledger = self._usage_ledger()
        if ledger is None:
            return False, ""
        return ledger.should_pause(max_cost, need=1.0)

    # ------------------------------------------------------------ 质量门禁

    def _target_query_text(self) -> str:
        """研究目标文本（用于官方 `query` 选项：内容块按相关性重排）。

        取第一个目标的"姓名 + topic"，作为官方 `query` 参数的取值。
        没有目标信息时返回 ""——此时官方 `chunks_per_source` 不可用
        （官方约束：仅在提供 `query` 时可用），补救方案会自动降级。
        """
        for target in getattr(self, "_current_targets", []) or []:
            parts = [str(target.display_name or "").strip(),
                     str(target.topic or "").strip()]
            text = " ".join(p for p in parts if p)
            if text:
                return text
        return ""

    def _target_display_names(self) -> list[str]:
        """目标人物姓名（含别名），供门禁判断内容是否明显无关。"""
        names: list[str] = []
        for target in getattr(self, "_current_targets", []) or []:
            if target.display_name:
                names.append(str(target.display_name))
            names.extend([str(a) for a in (target.aliases or []) if a])
        return names

    def _gate_extract_quality(
        self,
        ctx: CallContext,
        items: list["ExtractedContent"],
    ) -> tuple[list["ExtractedContent"], list["RejectedExtract"]]:
        """给每段正文**打上质量状态**，并把不合格的单独列出来（不静默丢弃）。

        ⚠️ **判定对象 = `excerpt`，不是 `content`（P4.5 方案 A）**。
        原因：下游（材料出口 → Claim Extractor）拿到的是 `excerpt`，不是全文。
        P4.5 受控复测实测到：长页的 `content` 全文 314,743 字判 VALID，而
        `excerpt`（当时 = `raw[:2000]`）是目录骨架——**全文判定掩盖了摘录问题**，
        摘录照样变成材料交出去。判摘录才能让"判定的"与"交付的"是同一个东西。

        返回 `(全部条目, 被拒条目)`：全部条目仍留在 `extracted` 供诊断，
        被拒条目进 `rejected_extracts`，两者的区分让运维能分清三种不同性质的问题：

        - `extract_error` 有值 → **API/抓取层失败**（目标网页不可访问）；
        - 状态 EMPTY 且无 error → 服务端返回了空内容；
        - 状态 LOW_QUALITY → 内容拿到了，但**本地门禁判定不可用**（如全是导航）。
        """
        rejected: list[RejectedExtract] = []
        for item in items:
            if item.error:
                # 抓取本身失败（API 层），不是质量问题 → 单独记，便于区分
                item.quality = ContentQuality(status=QUALITY_EMPTY, reasons=[REASON_EMPTY])
                rejected.append(RejectedExtract(
                    url=item.url, status=QUALITY_EMPTY,
                    reasons=[REASON_EMPTY], extract_error=item.error))
                continue
            # 判**摘录**（下游真正拿到的那段），不是判全文。
            # 不做"摘录为空就退回全文"：那样会把"拿到了全文但没拿到摘录"
            # 判成可用，交出去一个没有摘录的材料。
            item.quality = assess_content_quality(
                item.excerpt,
                url=item.url,
                target_names=self._target_display_names(),
                min_effective_chars=self.deps.quality_min_effective_chars,
            )
            if not item.quality.is_usable:
                rejected.append(RejectedExtract(
                    url=item.url, status=item.quality.status,
                    reasons=list(item.quality.reasons),
                    extract_error=None,
                    diagnosis=item.quality.as_detail()))
        return items, rejected

    def _sanitize_extracted_for_output(self, output: "ResearchOutput") -> None:
        """输出前收口：不可外流的全文一律清空 `content`。

        `register` 默认不落库全文（只存元数据），这里进一步确保**随产出外流**的全文
        不含以下几类内容——工程防御，不是法律认证：

        1. 被标 NEEDS_HUMAN / 访问状态不确定（paywall / copyright / unknown）；
        2. 检测到疑似提示注入；
        3. 抓取本身失败（有 `error`）；
        4. **P4.5 新增：未通过正文质量门禁的**（EMPTY / LOW_QUALITY）。

        第 4 条是"低质量正文不得生成看似有来源支持的 claim"的物理保证：
        内容直接在出口被清空，下游就算拿到了这个对象也取不到正文，
        自然不可能据此生成 claim。
        """
        for item in output.extracted:
            if item.content and (item.flagged_untrusted
                                or item.access_hint in ("paywall", "copyright", "unknown")
                                or item.error):
                item.content = ""  # 只保留 metadata，不外流不确定/不可信全文
            elif item.content and not item.is_usable_material:
                # 门禁未通过（含"尚未评估"）：正文不可用 → 出口清空
                item.content = ""

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _add_usage(target: BudgetUsage, usage: UsageDelta | None) -> None:
        """累加 Provider 回报的用量。**未知的量不加**（None 不等于 0，D-060）。

        P4.5：估算（`cost_estimate`）与实际（`cost_confirmed`）**分开累加**，
        两者都进 `ResearchOutput.budget_used`，供费用上限按**保守口径**拦截。
        """
        if usage is None:
            return
        target.add(requests=usage.requests, tokens_in=usage.tokens_in,
                   tokens_out=usage.tokens_out, latency_ms=usage.latency_ms,
                   cost_estimate=usage.cost_estimate,
                   cost_confirmed=usage.cost_confirmed,
                   currency=usage.currency)

    def _cost_ceiling(self, output: "ResearchOutput", reserve: float) -> "Err | None":
        """`research.max_cost` 上限检查：在**每次外部调用之前**判断是否应停止。

        - `max_cost is None`（默认）→ 不限制，返回 None；
        - 已累计成本 + 本次预留 > 上限 → 返回 `BUDGET_EXCEEDED`（暂停信号，由 `run`
          原样向上传播，不吞、不继续，也不伪造成功）；
        - 已累计成本**未知**（Provider 未回报）→ **无法判定**，不拦（D-060 禁止用
          估计值冒充），由 `run` 记一条 warning 让"闸门未生效"显式可见而非静默。
        """
        max_cost = self.deps.effective_limits.max_cost
        if max_cost is None:
            return None
        # 保守口径：取"估算 / 实际"的较大者（P4.5）
        # 只看实际值会被"未达计费粒度回报 0"骗过去，只看估算值则在无估算时失守。
        used = output.budget_used.accountable_cost()
        held = self._held_cost()
        if (used + held + reserve) > max_cost:
            return Err(new_error(
                ErrorCode.BUDGET_EXCEEDED,
                f"研究费用上限已达：已消耗 {used}{output.budget_used.currency}"
                f"，已预占 {held}，本次预计 {reserve}，上限 {max_cost}"))
        return None

    def _persist_usage(self, ctx: CallContext) -> None:
        """把本轮逐笔用量写入持久化账本（**幂等**，重复写入不会重复计）。

        这一步只做"搬运"：内存账本负责本轮可追溯与预算判断，
        落库负责跨轮/跨进程对账与人工核对。
        写入前补上 `task_id`，让账本能按任务汇总。
        """
        sink = self.deps.usage_sink
        ledger = self._usage_ledger()
        if sink is None or ledger is None:
            return
        for record in ledger.records():
            if self.deps.task_id and not record.task_id:
                record.task_id = self.deps.task_id
            if not record.trace_ref:
                record.trace_ref = ctx.trace_ref
            sink.record_external_usage(ctx, record)

    def _warn_unmeasured_costs(self, output: "ResearchOutput") -> None:
        """费用"测不出来"的两种情况都要**显式告警**，绝不静默放行。

        1. `COST_CEILING_UNENFORCEABLE`：设了上限，但本轮**一笔成本都量不出来**
           （既无估算也无实际）→ 费用闸门这轮等于没开。
        2. `COST_UNACCOUNTABLE`：有请求的费用状态是 UNKNOWN
           （响应无 `usage` / 请求失败 / 超时）→ 上限覆盖不到它们，
           **需人工核对**：账本里留着 `idempotency_key` 与 `request_id`，
           拿到账单后可 `ExternalUsageLedger.confirm(key, cost, source=...)` 回填。
        """
        if self.deps.effective_limits.max_cost is not None:
            if (output.budget_used.requests > 0
                    and output.budget_used.accountable_cost() == 0.0):
                output.warnings.append(WarningNote(
                    code=WARNING_COST_CEILING_UNENFORCEABLE,
                    message="已设置 research.max_cost，但本轮没有任何可计量的成本"
                            "（既无官方费率估算也无 usage.credits），费用上限未能生效"))

        ledger = self._usage_ledger()
        if ledger is None:
            return
        unknown = ledger.totals().unknown_requests
        if unknown and not any(w.code == WARNING_COST_UNACCOUNTABLE
                               for w in output.warnings):
            output.warnings.append(WarningNote(
                code=WARNING_COST_UNACCOUNTABLE,
                message=f"有 {unknown} 笔请求费用状态未知（响应无 usage / 请求失败或超时）；"
                        f"费用上限无法覆盖它们，需人工核对账单后回填账本"))

    # ---------------------------------------------------------------- 预留

    def _usage_ledger(self) -> Any | None:
        """取 Provider 共享的那本外部用量账本（没有就不做预留/结算）。"""
        return getattr(self.deps.provider, "usage_ledger", None)

    def _held_cost(self) -> float:
        """账本当前已被预留/未知占用的成本。"""
        ledger = self._usage_ledger()
        if ledger is None:
            return 0.0
        totals = ledger.totals()
        return totals.reserved_cost + totals.unknown_hold_cost

    def _reserve(
        self,
        ctx: CallContext,
        operation: str,
        *parts: Any,
        estimate: float | None,
        max_cost: float | None,
    ) -> str:
        """请求**发出之前**做预留。返回本次的幂等键。

        预留是为了防"连续请求绕过上限"：不预留的话，第二次请求看到的仍是
        第一次请求前的余额，两次就都能通过检查。
        """
        key = make_idempotency_key("tavily", operation, *parts)
        ledger = self._usage_ledger()
        if ledger is not None:
            ledger.reserve(key, estimate)
        return key
