"""BusinessOrchestrator —— **业务编排器**（H-10A）。

把「研究 → 证据 → 人物档案 → 关系档案 → 对话」五步串成一条可一键跑完的链，
并**复用**既有 `TaskService` / `TaskRunner` 的六态机、预算闸门与检查点。

**职责**：
    ✅ 决定「先做什么、后做什么」，把上一步的产出喂给下一步
    ✅ 在每一步之间强制**数据边界**（研究材料 ≠ 已验证证据；模拟内容 ≠ 档案）
    ✅ 汇报每一步的执行结果与整条链的成功 / 部分成功 / 失败

**非职责（红线）**：
    ⛔ **不复制第二套六态机 / 预算账本 / 检查点**。本编排器实现 `executor.UnitHandler`
       协议，由既有 `TaskRunner` 驱动：状态转换、预算预占与记账、检查点落盘、
       失败重试与 `FAILED` 置位**全部由既有代码完成**，本文件不碰。
    ⛔ **不做内容理解**。不自己写 claim、不做语义相关性判断、不蒸馏人格——
       claim 由**可注入的 `ClaimExtractorPort`** 从研究材料抽取（G-2），
       编排器只负责把候选交给 `Evidence` 校验登记；档案条目是证据的
       **搬运 + 按维度归类**，不是系统"理解"出来的结论。
    ⛔ 不直接写任何业务表。写库一律经 `Evidence` / `Persona` / `Relationship` /
       `Dialogue`（`Dialogue` 内部只写 `Conversation*` 与模拟记忆）。
    ⛔ 不持有 API Key、不选择具体 Provider——`Provider` 由各模块自己的依赖注入。

**依赖方向**：本文件位于 `persona/orchestrator/`（L5 协调层），可以调用
`persona/modules/*`（L4/L3）；**业务模块不得反向 import 本层**
（`tests/test_dependency_rules.py` F-1 + `Evaluation.check_dependencies` 硬性拦截）。

**已报告的契约缺口**（详见 `RUNNING.md` §5.12）：
    - **G-1**：`TaskType` 没有"全流程"成员。最小兼容：用 `TaskType.RESEARCH`
      作为这枚跨阶段任务的类型（`task_type` 可由请求覆盖），真实语义由
      `stage_sequence`（五个阶段名）承载。
    - **G-2**（已于 2026-10-14 关闭）：「来源 → 证据」的自动抽取。
      `Research` 现在把**原始材料**（含检索摘要）随产出返回，编排器调用注入的
      `ClaimExtractorPort` 把它转成候选 claim，再经 `Evidence` 校验 / 去重 / 登记。
      **抽取器只是候选生产者**：它拿不到 `Evidence`，结构上就不可能把 claim
      直接标成"已验证证据"。推断（`INFERENCE`）专用 `record_inference` 入口，
      且必须能解析出根证据，否则丢弃（EV-V3 禁止无根推断）。
      调用方仍可用 `evidence_seeds` 显式给出 claim——**显式优先**，给定时
      跳过自动抽取。
    - **H-17**（仍开）：材料的**正文全文**抓取未实现，抽取只能基于检索摘要
      `snippet`，覆盖面有限。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..core.context import CallContext
from ..core.enums import (
    CanonLevel,
    Cardinality,
    EntityType,
    EvidenceDimension,
    EvidenceTier,
    ProfileStatus,
    Stability,
    TaskStatus,
    TaskType,
)
from ..core.errors import AppError, ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, WarningNote, error_of, is_err, is_ok
from ..core.types import MAX_QUERY_LIMIT, BudgetUsage, Query, SourceMaterial
from ..modules.dialogue import DialogueService, SessionDraft
from ..modules.evidence import EvidenceDraft, EvidenceService, InferenceDraft
from ..modules.extraction import (
    CandidateClaim,
    ClaimExtractorPort,
    ExtractionOutcome,
)
from ..modules.persona import (
    CanonBoundary,
    PersonaProfileDraft,
    PersonaService,
    ProfileItem,
    ProfileSection,
    TimelineItem,
)
from ..modules.relationship import (
    GroupDynamics,
    PerspectiveBlock,
    RelationshipDraft,
    RelationshipService,
    SharedExperienceItem,
)
from ..modules.relationship_analysis import (
    RelationshipAnalysisOutcome,
    RelationshipAnalyzerPort,
    RelationshipAnalysisRequest,
)
from ..modules.research import (
    PersonQueryTarget,
    ResearchOutput,
    ResearchService,
    materials_from_extracts,
)
from ..storage.sqlite import SqliteStorage
from .executor import TaskRunner, UnitOutcome, WorkUnit
from .records import BudgetSpec, RetryPolicy, TaskRecord
from .state import is_terminal
from .tasks import TaskDraft, TaskService

# --------------------------------------------------------------------------
# 阶段名（与 `TaskType` 成员同名，便于 `stage_sequence` 直接承载语义）
# --------------------------------------------------------------------------

STAGE_RESEARCH = "RESEARCH"
STAGE_EVIDENCE = "EVIDENCE_EXTRACT"
STAGE_PERSONA = "PERSONA_DISTILL"
STAGE_RELATIONSHIP = "RELATIONSHIP_DISTILL"
STAGE_DIALOGUE = "DIALOGUE_RUN"

STAGE_SEQUENCE: tuple[str, ...] = (
    STAGE_RESEARCH,
    STAGE_EVIDENCE,
    STAGE_PERSONA,
    STAGE_RELATIONSHIP,
    STAGE_DIALOGUE,
)

# --------------------------------------------------------------------------
# 状态字（结果自述；「全流程成功」只有 RUN_SUCCEEDED 一个）
# --------------------------------------------------------------------------

RUN_SUCCEEDED = "SUCCEEDED"   # 五个阶段全部执行且任务 COMPLETED
RUN_PARTIAL = "PARTIAL"       # 跑完了，但有阶段未执行 / 产出不完整
RUN_PAUSED = "PAUSED"         # 被预算或用户暂停（不是失败，可 resume）
RUN_FAILED = "FAILED"         # 有阶段失败 / 任务 FAILED

STEP_EXECUTED = "EXECUTED"            # 本轮真正跑了
STEP_ALREADY_CURRENT = "ALREADY_CURRENT"  # 目标已是最新（未产生新版本）
STEP_SKIPPED = "SKIPPED"              # 未执行，且有明确原因
STEP_FAILED = "FAILED"
STEP_NOT_REACHED = "NOT_REACHED"      # 本轮未到达该阶段

# --------------------------------------------------------------------------
# 提示码（业务提示，不是错误；ERROR_HANDLING §2.3）
# --------------------------------------------------------------------------

WARNING_EVIDENCE_DRAFTS_ABSENT = "EVIDENCE_DRAFTS_ABSENT"
WARNING_EVIDENCE_DEDUP_HIT = "EVIDENCE_DEDUP_HIT"
WARNING_NO_EVIDENCE_FOR_ENTITY = "NO_EVIDENCE_FOR_ENTITY"
WARNING_PROFILE_ALREADY_CURRENT = "PROFILE_ALREADY_CURRENT"
WARNING_NO_RELATION_EVIDENCE = "NO_RELATION_EVIDENCE"
WARNING_NO_DIALOGUE_PROFILE = "NO_DIALOGUE_PROFILE"

# G-2：自动抽取链路的提示码
WARNING_EXTRACTOR_ABSENT = "EXTRACTOR_ABSENT"          # 没注入抽取器，也没给种子
WARNING_NO_MATERIALS = "NO_MATERIALS"                  # 研究没带回材料
WARNING_CLAIM_REJECTED = "CLAIM_REJECTED"              # 候选 claim 被抽取器拒收
WARNING_CLAIM_PENDING_REVIEW = "CLAIM_PENDING_REVIEW"  # 候选 claim 结构合法但核验不了，转待审核（P4.4-A）
WARNING_NO_CANDIDATES = "NO_CANDIDATES"                # 一个可登记的候选都没有
WARNING_INFERENCE_NO_ROOT = "INFERENCE_NO_ROOT"        # 无根推断，丢弃

# G4：抓取正文 → 材料流的接线（Research 阶段）
WARNING_MATERIAL_GATE_REJECTED = "MATERIAL_GATE_REJECTED"  # 抓取正文未过质量门禁，未进材料流
WARNING_MATERIAL_UNBOUND = "MATERIAL_SOURCE_UNBOUND"       # 抓取正文找不到对应已登记来源
WARNING_MATERIAL_SUPERSEDED = "MATERIAL_SUPERSEDED"        # 同来源的检索摘要被抓取正文取代
WARNING_MATERIAL_NOT_USABLE = "NO_MATERIAL_FROM_EXTRACTS"  # 有抓取正文但没有一份可用，退回摘要

# G9：RELATION 证据未被档案消费时的**分类**告警
# 修复前这些证据被 `continue` 静默丢弃，运维只能看到一句笼统的
# NO_RELATION_EVIDENCE（"关系档案是空的"），无法区分"材料里没有双人句"、
# "分析器没产出"、还是"其他"。三类分开记，且各带**数量 + 样例 ID**。
WARNING_RELATION_SUBJECT_INSUFFICIENT = "RELATION_EVIDENCE_SUBJECT_INSUFFICIENT"  # 主体不足：覆盖 <2 名参与者
WARNING_RELATION_ANALYSIS_FAILED = "RELATION_ANALYSIS_FAILED"                    # 分析失败：分析器没产出条目
WARNING_RELATION_EVIDENCE_UNUSED_OTHER = "RELATION_EVIDENCE_UNUSED_OTHER"        # 其他未消费情形
# 单人物：阶段按语义跳过，但本次**确实登记了** RELATION 证据 ⇒ 它们不会被任何
# 档案消费（人物档案没有 RELATION 维度，关系档案没建立）。必须显式说出来，
# 否则这条证据就等于凭空消失。
WARNING_RELATION_STAGE_SKIPPED_WITH_EVIDENCE = "RELATION_STAGE_SKIPPED_WITH_EVIDENCE"

# G6：稳定性判定（`judge_stability`）的接线结果
WARNING_STABILITY_UNASSESSED = "STABILITY_UNASSESSED"          # 有 TRAIT 证据，但没拿到判定
WARNING_STABILITY_JUDGED = "STABILITY_JUDGED"                  # 判定完成（含降级/单次结论）

# 冲突记录在检查点里的键（阶段间传递 ID 仍走已落盘的检查点，D-210）
ARTIFACT_CONFLICT_IDS = "conflict_ids"

# 单次行为不得进「稳定性格特征」（AC-7 / PF-V2 / D-012）
_NON_TRAIT_STABILITIES = (Stability.SINGLE, Stability.UNKNOWN)


# --------------------------------------------------------------------------
# 请求与结果
# --------------------------------------------------------------------------


@dataclass
class EvidenceSeed:
    """一条待登记证据 + 它引用的**来源定位符**（研究材料的 `canonical_key` / `url`）。

    为什么需要这层包装：研究阶段跑在证据阶段**之前**，调用方发请求时还拿不到
    `src_` 开头的来源 ID。于是调用方只声明"这条说法来自哪份材料"（`source_ref`），
    由编排器在研究阶段之后把它绑定到本次研究**实际登记**的 `Source` 上，
    再填进 `draft.source_id`。

    **这仍然不是自动抽取**：claim 文本、维度、级别、稳定性全部由调用方显式给出
    （G-2）；编排器只做"绑定 + 校验"，不生成任何说法。
    """

    source_ref: str
    draft: EvidenceDraft


@dataclass
class BusinessRequest:
    """一次业务全流程的请求。

    **G-2 之后的最小输入就是目标人物 + 研究目标**：`evidence_seeds` **可选**。
    不传时，编排器会走「Research → Claim Extraction → Evidence Registration」。

    `evidence_seeds` 保留为**显式覆盖通道**：给出时跳过自动抽取，原样登记
    （用于回归测试与人工校准）。两种路径都强制"证据必须绑定到本次研究登记的
    来源"。
    """

    targets: list[PersonQueryTarget]
    topic: str | None = None
    max_rounds: int = 2
    evidence_seeds: list[EvidenceSeed] = field(default_factory=list)
    task_type: TaskType | str = TaskType.RESEARCH
    budget_limit: BudgetSpec | None = None
    retry_policy: RetryPolicy | None = None
    idempotency_key: str | None = None


@dataclass
class BusinessStepResult:
    """一个阶段的执行结果。"""

    name: str
    status: str
    artifact_ids: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[WarningNote] = field(default_factory=list)
    message: str = ""
    usage: UsageDelta | None = None


@dataclass
class BusinessRunResult:
    """整条链的结构化结果。**能说明实际跑了哪些步骤**。"""

    task_id: str
    status: str                       # RUN_SUCCEEDED / RUN_PARTIAL / RUN_PAUSED / RUN_FAILED
    task_status: str                  # 底层 `TaskStatus`（PENDING…CANCELLED）
    steps: list[BusinessStepResult]
    source_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    persona_profile_ids: list[str] = field(default_factory=list)
    relationship_profile_ids: list[str] = field(default_factory=list)
    session_id: str | None = None
    turn_ids: list[str] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    warnings: list[WarningNote] = field(default_factory=list)
    budget_used: BudgetUsage = field(default_factory=BudgetUsage)
    error: AppError | None = None

    def is_complete(self) -> bool:
        """**只有 True 才算「全流程成功」**；`PARTIAL` / `PAUSED` 一律不算。"""
        return self.status == RUN_SUCCEEDED

    def step_of(self, name: str) -> BusinessStepResult | None:
        for step in self.steps:
            if step.name == name:
                return step
        return None


@dataclass(frozen=True)
class BusinessDeps:
    """业务编排器的全部外部依赖（**全部注入，内部不 new**）。

    `storage` **只读**：仅用于取 `PersonEntity` 的 `entity_type`——
    `PF-V5` / `D-115` 要求虚构角色声明适用版本、真人不得声明，
    编排器不能靠猜。除此之外编排器不碰存储。

    `claim_extractor`（G-2）：把研究材料转成**候选** claim。为空时只能走
    `evidence_seeds` 显式通道——编排器**不自己编 claim**。
    """

    research: ResearchService
    evidence: EvidenceService
    persona: PersonaService
    relationship: RelationshipService
    dialogue: DialogueService
    task_service: TaskService
    storage: SqliteStorage
    claim_extractor: ClaimExtractorPort | None = None
    relationship_analyzer: RelationshipAnalyzerPort | None = None


# --------------------------------------------------------------------------
# 内部辅助
# --------------------------------------------------------------------------


def _str_list(values: Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, str):
        return [values]
    return [str(v) for v in values if str(v or "").strip()]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _usage_from(budget: BudgetUsage) -> UsageDelta:
    """`BudgetUsage` → `UsageDelta`，交给既有预算闸门记账。

    D-060：token 为 0 表示 Provider **未回报**，按"未知"记 `None`，不填估计值。
    """
    return UsageDelta(
        requests=int(budget.requests or 0),
        tokens_in=budget.tokens_in or None,
        tokens_out=budget.tokens_out or None,
        latency_ms=int(budget.latency_ms or 0),
    )


def _stability_of(value: Any) -> Stability:
    try:
        return Stability(str(value))
    except ValueError:
        return Stability.UNKNOWN


def _first_ref(item: Any) -> str:
    """取研判条目的首个证据引用，作为确定性 `item_id` 种子（保证重跑幂等）。"""
    refs = _str_list(getattr(item, "evidence_refs", None))
    return refs[0] if refs else "x"


def _id_sample(ids: list[str], limit: int = 3) -> str:
    """告警里展示少量 ID 样例（不把几百个 ID 塞进一条消息）。"""
    shown = ", ".join(ids[:limit])
    return shown + (f" 等 {len(ids)} 条" if len(ids) > limit else "")


def _fold_whitespace(text: Any) -> str:
    """只折空白（D-104 的归一化口径）。**不做任何语义处理**。

    刻意与 `evidence.detect_duplicates` 的归一化保持一致：稳定性判定按"同一说法"
    分组，如果这里做了别的事（小写化、去标点），就会和去重器的分组口径分叉。
    """
    return " ".join(str(text or "").split())


def _dimension_of(value: Any) -> EvidenceDimension:
    try:
        return EvidenceDimension(str(value))
    except ValueError:
        return EvidenceDimension.BACKGROUND


# --------------------------------------------------------------------------
# 实现
# --------------------------------------------------------------------------


class BusinessOrchestrator:
    """业务编排器：串起五个业务模块，并把任务生命周期交给既有 `TaskRunner`。

    两套入口：
        - `run(ctx, request)`        新建任务 → 跑到底
        - `resume(ctx, task_id, request)`  从检查点续跑（已完成阶段不重复）

    `plan` / `execute` 是 `executor.UnitHandler` 的实现，供 `TaskRunner` 调用；
    **业务代码不要直接调它们**。
    """

    def __init__(self, deps: BusinessDeps) -> None:
        self.deps = deps
        self._request: BusinessRequest | None = None
        self._task_id: str = ""
        self._steps: dict[str, BusinessStepResult] = {}
        self._materials: dict[str, list[SourceMaterial]] = {}

    # ================================================================ 入口

    def run(self, ctx: CallContext, request: BusinessRequest) -> Result[BusinessRunResult]:
        """一条命令跑完五步。返回**永不把部分成功说成全流程成功**。"""
        problem = self._validate_request(ctx, request)
        if problem is not None:
            return problem

        draft = TaskDraft(
            task_type=request.task_type,
            stage_sequence=list(STAGE_SEQUENCE),
            target_entity_ids=[t.entity_id for t in request.targets],
            budget_limit=request.budget_limit,
            retry_policy=request.retry_policy,
            idempotency_key=request.idempotency_key,
        )
        created = self.deps.task_service.create_task(ctx, draft)
        if is_err(created):
            return created
        record = created.value
        task_id = record.id

        # 同一 `idempotency_key` 会返回**既有任务**（TS-V4）。已终态就只读进度，
        # 不再试图把它推回 RUNNING（六态机不允许，也不该重复干活）。
        if is_terminal(record.status):
            self._request = request
            self._task_id = task_id
            self._steps = {}
            return Ok(self._summarize(ctx, task_id, None))
        if record.status == TaskStatus.PAUSED:
            resumed = self.deps.task_service.resume(ctx, task_id)
            if is_err(resumed):
                return resumed
        elif record.status == TaskStatus.PENDING:
            started = self.deps.task_service.start(ctx, task_id)
            if is_err(started):
                return started
        return self._drive(ctx, task_id, request)

    def resume(
        self, ctx: CallContext, task_id: str, request: BusinessRequest
    ) -> Result[BusinessRunResult]:
        """从检查点续跑。**已完成阶段一律不重复执行**（AC-11）。

        `request` 需重新提供：检查点只存产出 ID，不存业务请求（缺口见 §G-3）。
        """
        problem = self._validate_request(ctx, request)
        if problem is not None:
            return problem
        current = self.deps.task_service.get(ctx, task_id)
        if is_err(current):
            return current
        if current.value.status == TaskStatus.PAUSED:
            resumed = self.deps.task_service.resume(ctx, task_id)
            if is_err(resumed):
                return resumed
        return self._drive(ctx, task_id, request)

    # ================================================================ UnitHandler

    def plan(self, task: TaskRecord) -> list[WorkUnit]:
        """列出五个阶段的工作单元（顺序即执行顺序）。

        顺手记下 `task.id`：`execute` 拿不到任务对象，需要它去读检查点里
        前序阶段的产出 ID。
        """
        self._task_id = task.id
        request = self._request
        rounds = max(0, int(request.max_rounds)) if request else 0
        targets = len(request.targets) if request else 1
        drafts = len(request.evidence_seeds) if request else 1
        estimates = {
            STAGE_RESEARCH: max(1, targets * (2 if _text(request.topic if request else "") else 1)),
            STAGE_EVIDENCE: max(1, drafts),
            STAGE_PERSONA: max(1, targets),
            STAGE_RELATIONSHIP: 1,
            STAGE_DIALOGUE: max(1, rounds),
        }
        return [
            WorkUnit(name=stage, stage=stage,
                     estimate=UsageDelta(requests=estimates[stage]))
            for stage in STAGE_SEQUENCE
        ]

    def execute(
        self, ctx: CallContext, unit: WorkUnit, *, idempotency_key: str = ""
    ) -> Result[UnitOutcome]:
        """执行一个阶段。失败即 `Err`——由 `TaskRunner` 决定重试 / 置 FAILED。"""
        request = self._request
        if request is None:
            return Err(new_error(
                ErrorCode.INTERNAL,
                "execute 被调用但没有设置请求：请先走 run() / resume()",
                trace_ref=ctx.trace_ref))

        task_id = self._task_id or _task_id_from_key(idempotency_key)
        prior = self._load_artifacts(ctx, task_id)

        step = self._dispatch(ctx, request, unit.name, prior)
        if isinstance(step, Err):
            failed = BusinessStepResult(name=unit.name, status=STEP_FAILED,
                                        message=str(step.error))
            self._steps[unit.name] = failed
            return step
        self._steps[unit.name] = step
        return Ok(UnitOutcome(artifact_ids=dict(step.artifact_ids), usage=step.usage))

    # ================================================================ 分派

    def _dispatch(
        self, ctx: CallContext, request: BusinessRequest, name: str,
        prior: dict[str, list[str]],
    ) -> BusinessStepResult | Err:
        if name == STAGE_RESEARCH:
            return self._step_research(ctx, request)
        if name == STAGE_EVIDENCE:
            return self._step_evidence(ctx, request, prior)
        if name == STAGE_PERSONA:
            return self._step_persona(ctx, request, prior)
        if name == STAGE_RELATIONSHIP:
            return self._step_relationship(ctx, request, prior)
        if name == STAGE_DIALOGUE:
            return self._step_dialogue(ctx, request, prior)
        return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                             f"未知阶段：{name}", trace_ref=ctx.trace_ref))

    # ================================================================ ① 研究

    def _step_research(
        self, ctx: CallContext, request: BusinessRequest
    ) -> BusinessStepResult | Err:
        """调 `ResearchService.run` → 登记来源。**来源不是证据**。

        G4 修复：这里同时把**抓取正文**（过了正文质量门禁的那些）转成材料，
        与检索摘要一起进材料流——见 `_material_flow`。修复前只记住摘要，
        导致上线配置下抽取器把每份材料都判"未声明门禁结论"而全部拒收，
        模型永不被调用。
        """
        outcome = self.deps.research.run(ctx, list(request.targets))
        if is_err(outcome):
            return outcome
        research = outcome.value
        warnings = list(research.warnings)

        flow = self._material_flow(
            ctx, research, [t.entity_id for t in request.targets],
            _text(request.topic))
        if isinstance(flow, Err):
            return flow
        materials, flow_warnings = flow
        warnings.extend(flow_warnings)

        gate_valid = sum(1 for m in materials if _text(m.quality_status))
        message = (f"登记 {len(research.source_ids)} 条来源"
                   f"（计划 {research.planned_count} 条检索，"
                   f"执行 {research.executed_count} 条，"
                   f"无结果 {research.no_result_count} 条；"
                   f"带回 {len(research.materials)} 份检索摘要、"
                   f"{len(research.extracted)} 份抓取正文，"
                   f"进入材料流 {len(materials)} 份"
                   f"（其中带门禁结论 {gate_valid} 份））")
        self._remember(materials)
        return BusinessStepResult(
            name=STAGE_RESEARCH, status=STEP_EXECUTED,
            artifact_ids={"source_ids": list(research.source_ids)},
            warnings=warnings, message=message,
            usage=_usage_from(research.budget_used))

    # ================================================================ ② 证据

    def _step_evidence(
        self, ctx: CallContext, request: BusinessRequest,
        prior: dict[str, list[str]],
    ) -> BusinessStepResult | Err:
        """`Research → Claim Extraction → Evidence Registration`（G-2）。

        两条入口：
          - 调用方给了 `evidence_seeds` → **显式优先**，登记它们（覆盖自动抽取）；
          - 没给 → 用注入的 `ClaimExtractorPort` 从研究材料抽取候选 claim。

        两道闸门（两路径共用）：
          1. 每条证据的 `source_id` **必须**在本次研究登记的来源里（可追溯）；
          2. 内容与主体都相同的证据**不重复登记**（`add_evidence` 本身不去重）。
        """
        source_ids = list(prior.get("source_ids", []))
        seeds = list(request.evidence_seeds or [])
        if seeds:
            return self._register_seeds(ctx, seeds, source_ids)

        # —— 自动抽取路径 ——
        if self.deps.claim_extractor is None:
            return BusinessStepResult(
                name=STAGE_EVIDENCE, status=STEP_SKIPPED,
                warnings=[WarningNote(
                    code=WARNING_EXTRACTOR_ABSENT,
                    message="既未注入 Claim Extractor，调用方也未给出 claim："
                            "编排器不伪造 claim")],
                message="证据阶段未执行：无抽取器、无显式 claim。")

        materials = self._materials_for(ctx, request, prior)
        if isinstance(materials, Err):
            return materials
        if not materials:
            return BusinessStepResult(
                name=STAGE_EVIDENCE, status=STEP_SKIPPED,
                warnings=[WarningNote(
                    code=WARNING_NO_MATERIALS,
                    message="研究阶段没有带回任何材料：无从抽取 claim（不凭空生成）")],
                message="证据阶段未执行：无材料。")

        extracted = self.deps.claim_extractor.extract(
            ctx, materials, targets=list(request.targets))
        if is_err(extracted):
            return extracted
        outcome = extracted.value
        return self._register_candidates(
            ctx, outcome, source_ids, usage=getattr(outcome, "usage", None))

    # ------------------------------------------------------- 路径 A：显式种子

    def _register_seeds(
        self, ctx: CallContext, seeds: list[EvidenceSeed], source_ids: list[str]
    ) -> BusinessStepResult | Err:
        """登记调用方显式给出的证据草稿（G-2 之前的唯一路径，保留作覆盖通道）。"""
        locator = self._source_locator(ctx, source_ids)
        if isinstance(locator, Err):
            return locator
        evidence_ids: list[str] = []
        warnings: list[WarningNote] = []
        for index, seed in enumerate(seeds):
            resolved = self._resolve_source(seed, source_ids, locator)
            if resolved is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"第 {index} 条证据的来源定位符"
                    f"（{_text(seed.source_ref) or '空'}）无法解析到本次研究登记的"
                    "来源：证据必须能追溯到已登记来源，不得凭空声称",
                    trace_ref=ctx.trace_ref))
            draft = replace(seed.draft, source_id=resolved)
            existing = self._find_evidence(ctx, resolved, draft)
            if isinstance(existing, Err):
                return existing
            if existing is not None:
                evidence_ids.append(existing)
                warnings.append(WarningNote(
                    code=WARNING_EVIDENCE_DEDUP_HIT,
                    message=f"第 {index} 条证据已存在（同来源 + 同 claim），复用原记录"))
                continue
            added = self.deps.evidence.add_evidence(ctx, draft)
            if is_err(added):
                return added
            evidence_ids.append(added.value.id)

        subjects = sorted({e for seed in seeds
                           for e in _str_list(seed.draft.subject_entity_ids)})
        conflicts = self._detect_conflicts(ctx, subjects)
        if isinstance(conflicts, Err):
            return conflicts
        return BusinessStepResult(
            name=STAGE_EVIDENCE, status=STEP_EXECUTED,
            artifact_ids={"evidence_ids": evidence_ids,
                          ARTIFACT_CONFLICT_IDS: conflicts},
            warnings=warnings,
            message=f"登记 {len(evidence_ids)} 条证据（全部可追溯到本次来源）"
                    + self._conflict_note(conflicts))

    # ------------------------------------------------- 路径 B：自动抽取（G-2）

    def _register_candidates(
        self, ctx: CallContext, outcome: ExtractionOutcome, source_ids: list[str],
        *, usage: "UsageDelta | None" = None,
    ) -> BusinessStepResult | Err:
        """把**候选** claim 交给 `Evidence` 校验与登记。

        三种 claim 分两条入口（对应模块 docstring 的机械区分）：
          - 原文直接支持 / 对材料的归纳 → `Evidence.add_evidence`；
          - 系统推断 `INFERENCE` → `Evidence.record_inference`，且**必须**能解析
            出根证据，否则丢弃（EV-V3：禁止无根推断）。

        冲突**不裁决也不合并**：相互矛盾的说法各自登记成独立证据，再请
        `Evidence.detect_conflicts` 做确定性结构检测并记 `ConflictRecord`。

        `outcome.pending`（P4.4-A）是**待审核**清单：结构合法但核验不了
        （声称原话却没摘录、材料可疑、推断的根找不到…）。它们**不登记**，
        但必须在这里显式转成提示——否则就成了"静默丢弃"。
        """
        known = set(source_ids)
        warnings: list[WarningNote] = []
        # G5 修复：抽取器自己的告警**原样透传**。
        # 修复前这里被整个丢掉，于是"材料为什么没进抽取器"
        # （MATERIAL_QUALITY_UNDECLARED / MODEL_CALL_DUPLICATE_SKIPPED /
        #  MODEL_RETURNED_EMPTY / MODEL_REFUSED / NO_MATERIALS …）在任务告警里
        # 完全看不到，只剩一句笼统的 CLAIM_REJECTED —— 运维会把"材料没过门禁"
        # 误判成"模型抽不出东西"，且无法按码筛选。
        warnings.extend(getattr(outcome, "warnings", []) or [])
        for rejected in outcome.rejected:
            # 材料级拒收（只有 reason，没有 claim）**用 reason 本身当告警码**：
            # 这样 MATERIAL_UNDECLARED / MATERIAL_NOT_GATE_PASSED 是可筛选的码，
            # 而不是只以文字形式藏在 message 里。claim 级拒收保持 CLAIM_REJECTED。
            reason = _text(rejected.reason)
            detail = rejected.message or _text(getattr(rejected, "claim", ""))
            if reason and not _text(getattr(rejected, "claim", "")):
                warnings.append(WarningNote(
                    code=reason, message=f"材料被拒收（{reason}）：{detail}"))
                continue
            warnings.append(WarningNote(
                code=WARNING_CLAIM_REJECTED,
                message=f"候选 claim 被拒收（{reason}）：{detail}"))
        pending_claims = list(getattr(outcome, "pending", []) or [])
        for pending in pending_claims:
            warnings.append(WarningNote(
                code=WARNING_CLAIM_PENDING_REVIEW,
                message=f"候选 claim 待审核（{getattr(pending, 'reason', '')}）："
                        f"{getattr(pending, 'message', '') or getattr(pending, 'claim', '')}"
                        "——未登记，交人工"))

        evidence_ids: list[str] = []
        key_to_evidence: dict[str, str] = {}
        subjects: set[str] = set()

        # ① 非推断：原文直接支持 / 归纳
        for candidate in outcome.claims:
            if _text(candidate.tier) == str(EvidenceTier.INFERENCE):
                continue
            if _text(candidate.source_id) not in known:
                return Err(new_error(
                    ErrorCode.VALIDATION_DANGLING_REFERENCE,
                    f"候选 claim「{_text(candidate.claim)}」的 source_id"
                    f"（{_text(candidate.source_id) or '空'}）不在本次研究登记的"
                    "来源内：证据必须能追溯到已登记来源，不得凭空声称",
                    trace_ref=ctx.trace_ref))
            draft = self._to_evidence_draft(candidate, outcome.method)
            existing = self._find_evidence(ctx, candidate.source_id, draft)
            if isinstance(existing, Err):
                return existing
            if existing is not None:
                key_to_evidence[_text(candidate.key)] = existing
                evidence_ids.append(existing)
                warnings.append(WarningNote(
                    code=WARNING_EVIDENCE_DEDUP_HIT,
                    message=f"候选「{_text(candidate.claim)}」已存在"
                            "（同来源 + 同 claim + 同主体），复用原记录"))
                continue
            added = self.deps.evidence.add_evidence(ctx, draft)
            if is_err(added):
                return added
            key_to_evidence[_text(candidate.key)] = str(added.value.id)
            evidence_ids.append(str(added.value.id))
            subjects.update(_str_list(candidate.subject_entity_ids))

        # ② 推断：必须能解析出根证据，否则丢弃
        dropped = 0
        for candidate in outcome.claims:
            if _text(candidate.tier) != str(EvidenceTier.INFERENCE):
                continue
            derived = [key_to_evidence[k] for k in _str_list(candidate.derivation_keys)
                       if k in key_to_evidence]
            if not derived:
                dropped += 1
                warnings.append(WarningNote(
                    code=WARNING_INFERENCE_NO_ROOT,
                    message=f"推断「{_text(candidate.claim)}」找不到根证据"
                            "（derivation_keys 无法解析）：按 EV-V3 丢弃，"
                            "不登记为无根推断"))
                continue
            existing = self._find_derived(ctx, candidate)
            if isinstance(existing, Err):
                return existing
            if existing is not None:
                evidence_ids.append(existing)
                warnings.append(WarningNote(
                    code=WARNING_EVIDENCE_DEDUP_HIT,
                    message=f"推断「{_text(candidate.claim)}」已存在"
                            "（同 claim + 同主体），复用原记录"))
                continue
            recorded = self.deps.evidence.record_inference(ctx, InferenceDraft(
                claim=_text(candidate.claim),
                derived_from_evidence_ids=derived,
                dimension=candidate.dimension,
                subject_entity_ids=_str_list(candidate.subject_entity_ids),
                confidence=float(candidate.confidence or 0.0),
                reasoning_note=_text(candidate.note)
                or "由抽取器产出的系统推断，未经人工复核",
                tags=[f"extract:{_text(outcome.method) or 'UNKNOWN'}"],
            ))
            if is_err(recorded):
                return recorded
            evidence_ids.append(str(recorded.value.id))
            subjects.update(_str_list(candidate.subject_entity_ids))

        conflicts = self._detect_conflicts(ctx, sorted(subjects))
        if isinstance(conflicts, Err):
            return conflicts
        message = (f"从 {len(outcome.claims) + len(outcome.rejected)} 个候选中登记 "
                   f"{len(evidence_ids)} 条证据"
                   + (f"，丢弃 {dropped} 条无根推断" if dropped else "")
                   + f"，拒收 {len(outcome.rejected)} 条"
                   + (f"，待审核 {len(pending_claims)} 条" if pending_claims else "")
                   + self._conflict_note(conflicts))
        if not evidence_ids:
            # 一条都没登记成功 —— 如实说"没做"，绝不报 EXECUTED 冒充成功
            warnings.append(WarningNote(
                code=WARNING_NO_CANDIDATES,
                message="没有任何候选通过校验：证据阶段实际未产出证据"))
            return BusinessStepResult(
                name=STAGE_EVIDENCE, status=STEP_SKIPPED,
                artifact_ids={"evidence_ids": [], ARTIFACT_CONFLICT_IDS: []},
                warnings=warnings, message=message + "：证据阶段未产出任何证据",
                usage=usage)
        return BusinessStepResult(
            name=STAGE_EVIDENCE, status=STEP_EXECUTED,
            artifact_ids={"evidence_ids": evidence_ids,
                          ARTIFACT_CONFLICT_IDS: conflicts},
            warnings=warnings, message=message, usage=usage)

    @staticmethod
    def _to_evidence_draft(
        candidate: CandidateClaim, method: str = ""
    ) -> EvidenceDraft:
        """候选 claim → `EvidenceDraft`。**只搬运字段，不提升其可信度**。"""
        return EvidenceDraft(
            claim=_text(candidate.claim),
            tier=candidate.tier,
            dimension=candidate.dimension,
            subject_entity_ids=_str_list(candidate.subject_entity_ids),
            extraction_confidence=float(candidate.confidence or 0.0),
            source_id=_text(candidate.source_id) or None,
            locator=dict(candidate.locator) if candidate.locator else None,
            verbatim_quote=_text(candidate.verbatim_quote) or None,
            stated_at=candidate.stated_at,
            stability=candidate.stability,
            canon_level=candidate.canon_level,
            tags=_str_list(candidate.tags) + [f"extract:{_text(method) or 'UNKNOWN'}"],
        )

    def _find_derived(
        self, ctx: CallContext, candidate: CandidateClaim
    ) -> str | None | Err:
        """推断的幂等守卫：同 claim + 同主体的推断不重复登记。

        `record_inference` 本身不去重；重跑会产生第二条内容一样的推断。
        """
        page = self.deps.evidence.query_evidence(
            ctx, Query(filters={"tier": str(EvidenceTier.INFERENCE),
                                "entity_ids": _str_list(candidate.subject_entity_ids)},
                       limit=MAX_QUERY_LIMIT))
        if is_err(page):
            return page
        wanted = _text(candidate.claim)
        wanted_subjects = set(_str_list(candidate.subject_entity_ids))
        for item in page.value.items:
            if _text(item.claim) != wanted:
                continue
            if set(_str_list(item.subject_entity_ids)) != wanted_subjects:
                continue
            return str(item.id)
        return None

    def _detect_conflicts(
        self, ctx: CallContext, subject_ids: list[str]
    ) -> list[str] | Err:
        """请 `Evidence` 做确定性结构冲突检测。**只记录，不裁决**（D-055 / AC-15）。"""
        if not subject_ids:
            return []
        found = self.deps.evidence.detect_conflicts(
            ctx, {"entity_ids": list(subject_ids)})
        if is_err(found):
            return found
        return [str(c.id) for c in found.value]

    @staticmethod
    def _conflict_note(conflict_ids: list[str]) -> str:
        return f"；检出 {len(conflict_ids)} 组冲突（只记录，不裁决）" if conflict_ids else ""

    @staticmethod
    def _resolve_source(
        seed: EvidenceSeed, source_ids: list[str], locator: dict[str, str]
    ) -> str | None:
        """把 `source_ref`（canonical_key / url）解析成本次登记的 `Source` ID。

        若草稿已直接给出 `source_id` 且在本次产出内，则直接用它。
        """
        given = _text(seed.draft.source_id)
        if given and given in set(source_ids):
            return given
        return locator.get(_text(seed.source_ref)) or None

    def _source_locator(
        self, ctx: CallContext, source_ids: list[str]
    ) -> dict[str, str] | Err:
        """建 `canonical_key / url → source_id` 的反查表（只读，不写库）。"""
        table: dict[str, str] = {}
        for source_id in source_ids:
            view = self.deps.evidence.get_source(ctx, source_id)
            if is_err(view):
                return view
            for key in (_text(view.value.canonical_key), _text(view.value.url)):
                if key:
                    table.setdefault(key, source_id)
        return table

    def _material_flow(
        self, ctx: CallContext, research: ResearchOutput, target_ids: list[str],
        topic: str,
    ) -> tuple[list[SourceMaterial], list[WarningNote]] | Err:
        """研究产出 → **送进抽取器的材料**。G4 修复的唯一落点。

        缺陷本体（修复前）：`_step_research` 只 `_remember(research.materials)`，
        而那是**检索摘要**（`quality_status=""`）。抓取到的正文虽然过了正文质量
        门禁、也带着 `quality_status=VALID` 躺在 `research.extracted` 里，却
        **从来没有人取**——`materials_from_extracts()` 这个唯一会把它转成材料的
        函数在产品代码里零调用点。于是上线配置（`require_gate_verdict=True`）
        下每份材料都在抽取器入口被判 `MATERIAL_UNDECLARED` 全部拒收，
        **模型一次都不会被调用**（"抓了正文却交不出去"）。

        这里补上三件必须一起做的事：
          1. 调 `materials_from_extracts()` 把**过门禁的**正文转成材料；
          2. 把 `source_id` 绑回去——该函数按设计留 `""`，而证据登记的可追溯
             闸门要求候选的 `source_id` 必须在本次登记的来源里；
          3. 同一来源既有摘要又有正文时**正文取代摘要**（同一 URL 的同一份
             内容，重复交出去只会让模型看到重复材料，且摘要必然被严格门禁拒收）。

        没有可用正文时**退回摘要**（保持既有行为，不新增阻断）：此时严格门禁
        会如实把它们判为"未声明"，而不是静默假装有材料。
        """
        extracts = list(getattr(research, "extracted", []) or [])
        materials, rejected = materials_from_extracts(
            extracts, target_entity_ids=list(target_ids), query_text=_text(topic))

        warnings: list[WarningNote] = []
        for item in rejected:
            reasons = "、".join(str(r) for r in (item.reasons or [])) or "UNKNOWN"
            warnings.append(WarningNote(
                code=WARNING_MATERIAL_GATE_REJECTED,
                message=f"抓取正文未通过质量门禁（{item.status}：{reasons}），"
                        f"未进入材料流：{_text(item.url) or '（无 url）'}"))

        locator = self._source_locator(ctx, list(research.source_ids))
        if isinstance(locator, Err):
            return locator
        unbound: list[str] = []
        for material in materials:
            source_id = (locator.get(_text(material.url))
                         or locator.get(_text(material.canonical_key)))
            if not source_id:
                unbound.append(_text(material.url) or "（无 url）")
                continue
            material.source_id = source_id
        if unbound:
            warnings.append(WarningNote(
                code=WARNING_MATERIAL_UNBOUND,
                message=f"{len(unbound)} 份抓取正文找不到对应的已登记来源，"
                        f"未进入材料流（证据必须能追溯到已登记来源，不得凭空声称）："
                        f"{unbound}"))

        bound = [m for m in materials if _text(m.source_id)]
        if not bound:
            if extracts:
                warnings.append(WarningNote(
                    code=WARNING_MATERIAL_NOT_USABLE,
                    message=f"抓取到 {len(extracts)} 份正文但无一可用"
                            "（未过门禁或来源无法绑定）：退回检索摘要"))
            return list(research.materials), warnings

        covered = {k for m in bound
                   for k in (_text(m.url), _text(m.canonical_key)) if k}
        kept = [m for m in research.materials
                if not ({_text(m.url), _text(m.canonical_key)} & covered)]
        superseded = len(research.materials) - len(kept)
        if superseded:
            warnings.append(WarningNote(
                code=WARNING_MATERIAL_SUPERSEDED,
                message=f"{superseded} 份检索摘要被同来源的抓取正文取代"
                        "（同一来源不重复交给抽取器）"))
        return bound + kept, warnings

    def _remember(self, materials: list[SourceMaterial]) -> None:
        """把研究材料存进**进程内**缓存（按 `task_id`）。

        ⚠️ 为什么不落检查点：`TaskService.resume` 会把 `artifact_ids` 里的
        **每一项**都当成实体引用去校验（`VALIDATION.artifact_missing`），
        材料正文不是 ID，塞进去会让续跑直接失败。而既有任务模块不在本步改动
        范围内，所以材料改为进程内传递（**已知限制**：跨进程续跑见
        `_materials_for` 的幂等回补）。
        """
        if self._task_id:
            self._materials[self._task_id] = list(materials or [])

    def _materials_for(
        self, ctx: CallContext, request: BusinessRequest, prior: dict[str, list[str]]
    ) -> list[SourceMaterial] | Err:
        """取本轮的材料。**缓存失效时以幂等重跑研究的方式回补**，不凭空造材料。"""
        cached = self._materials.get(self._task_id or "")
        if cached:
            return list(cached)
        if not prior.get("source_ids"):
            return []
        # 研究是幂等的（register_source 会去重命中），重跑只为取回材料正文，
        # 不会产生重复来源。G4：走与 `_step_research` **同一条**材料流，
        # 否则回补路径又会只取回摘要（修复不完整 = 埋一个只在续跑时触发的坑）。
        rerun = self.deps.research.run(ctx, list(request.targets))
        if is_err(rerun):
            return rerun
        flow = self._material_flow(
            ctx, rerun.value, [t.entity_id for t in request.targets],
            _text(request.topic))
        if isinstance(flow, Err):
            return flow
        materials, _warnings = flow
        self._remember(materials)
        return materials

    def _find_evidence(
        self, ctx: CallContext, source_id: str, draft: EvidenceDraft
    ) -> str | None | Err:
        """幂等守卫：同来源 + 同 claim + 同主体 的证据不重复登记。"""
        page = self.deps.evidence.query_evidence(
            ctx, Query(filters={"source_id": source_id}, limit=MAX_QUERY_LIMIT))
        if is_err(page):
            return page
        wanted_claim = _text(draft.claim)
        wanted_subjects = set(_str_list(draft.subject_entity_ids))
        for item in page.value.items:
            if _text(item.claim) != wanted_claim:
                continue
            if set(_str_list(item.subject_entity_ids)) != wanted_subjects:
                continue
            return str(item.id)
        return None

    # ================================================================ ③ 人物档案

    def _step_persona(
        self, ctx: CallContext, request: BusinessRequest,
        prior: dict[str, list[str]],
    ) -> BusinessStepResult | Err:
        """**只**用已登记的证据形成 / 更新人物档案。没有证据就留 `open_questions`。"""
        evidence_ids = list(prior.get("evidence_ids", []))
        loaded = self._load_evidence(ctx, request, evidence_ids)
        if isinstance(loaded, Err):
            return loaded
        by_entity = loaded
        types = self._entity_types(ctx, [t.entity_id for t in request.targets])
        if isinstance(types, Err):
            return types

        profile_ids: list[str] = []
        warnings: list[WarningNote] = []
        # G6：判定必须在**全部证据上一次性完成**，不能放进 per-target 循环——
        # 一条双主体证据会同时出现在两个目标的 `items` 里，逐目标判定会对它
        # 判两次（后一次可能只有它自己 ⇒ 判成 SINGLE 并覆盖前一次的 REPEATED），
        # 既多写一遍库，结果也不稳定。
        _unique: dict[str, Any] = {}
        for _items in by_entity.values():
            for _item in _items:
                _unique[str(_item.id)] = _item
        stabilities = self._assess_stabilities(
            ctx, list(_unique.values()), warnings)
        for target in request.targets:
            entity_id = _text(target.entity_id)
            items = by_entity.get(entity_id, [])
            draft = self._build_persona_draft(
                entity_id, items, types.get(entity_id, ""), stabilities)
            if not items:
                warnings.append(WarningNote(
                    code=WARNING_NO_EVIDENCE_FOR_ENTITY,
                    message=f"{entity_id} 没有可用证据，档案记为 INSUFFICIENT_EVIDENCE"
                            "（不补全、不臆测）"))
            before = len(warnings)
            written = self._write_persona(ctx, entity_id, draft, warnings)
            if isinstance(written, Err):
                return written
            profile_ids.append(written[0])
            if written[1] and len(warnings) == before:
                # 模块层已经给出更具体的说明（如 G7 的空产出拦截）时不再叠加通用提示
                warnings.append(WarningNote(
                    code=WARNING_PROFILE_ALREADY_CURRENT,
                    message=f"{entity_id} 的档案已是最新，未生成新版本（不重复更新）"))

        return BusinessStepResult(
            name=STAGE_PERSONA, status=STEP_EXECUTED,
            artifact_ids={"persona_profile_ids": profile_ids},
            warnings=warnings,
            message=f"形成 / 更新 {len(profile_ids)} 份人物档案（证据驱动）")

    def _load_evidence(
        self, ctx: CallContext, request: BusinessRequest, evidence_ids: list[str]
    ) -> dict[str, list[Any]] | Err:
        """按证据 ID 取出详情，并按主体对象归拢。"""
        wanted = set(evidence_ids)
        if not wanted:
            return {}
        page = self.deps.evidence.query_evidence(
            ctx, Query(filters={"entity_ids": [t.entity_id for t in request.targets]},
                       limit=MAX_QUERY_LIMIT))
        if is_err(page):
            return page
        by_entity: dict[str, list[Any]] = {}
        for item in page.value.items:
            if str(item.id) not in wanted:
                continue
            for subject in _str_list(item.subject_entity_ids):
                by_entity.setdefault(subject, []).append(item)
        return by_entity

    def _relation_evidence_of(
        self, ctx: CallContext, request: BusinessRequest, evidence_ids: list[str]
    ) -> list[Any] | Err:
        """本次登记的、`dimension=RELATION` 的证据（按 ID 去重，保持首次出现顺序）。

        G9：抽出来是因为**两条路径都要它** —— 主路径要喂给分析器/兜底，
        单人物路径要在阶段跳过之前数一遍"有多少证据会被悬空"。
        """
        loaded = self._load_evidence(ctx, request, evidence_ids)
        if isinstance(loaded, Err):
            return loaded
        seen: dict[str, Any] = {}
        for target in request.targets:
            for item in loaded.get(_text(target.entity_id), []):
                key = str(item.id)
                if key in seen:
                    continue
                if _dimension_of(item.dimension) != EvidenceDimension.RELATION:
                    continue
                seen[key] = item
        return list(seen.values())

    # ============================================================ G6 稳定性判定

    @staticmethod
    def _stability_groups(items: list[Any]) -> dict[tuple, list[Any]]:
        """按「归一化说法 + 主体集合」把证据归组（G6-D3）。

        **不含 `dimension`**：同一句话被抽成两个维度（如同时是 HABIT 与 TRAIT）
        仍是**同一个说法的同一次出现**，按维度切开会人为压低 `occurrences`。

        归一化只折空白（与 `evidence._normalize_claim`、D-104 同口径）：
        这是"确定性可判定"的上限，语义级聚类需要模型，本实现不含。
        """
        groups: dict[tuple, list[Any]] = {}
        for item in items:
            key = (_fold_whitespace(_text(item.claim)),
                   frozenset(_str_list(item.subject_entity_ids)))
            groups.setdefault(key, []).append(item)
        return groups

    def _assess_stabilities(
        self, ctx: CallContext, items: list[Any], warnings: list[WarningNote]
    ) -> dict[str, Stability]:
        """判定每条证据的稳定性并**落库为附加字段**（G6-D1）。

        G6-D2：组内**按 `source_id` 去重**，每个来源只留一条代表。
        原因是幂等键用**精确** claim、分组键用**归一化** claim，两者的差集
        就是"仅空白差异的近似重复"——同一来源里这类变体会各占一个证据 ID。
        若原样喂给 `judge_stability`，`occurrences=2 / distinct_sources=1`
        会被判成 `REPEATED` 并进 `traits`，等于**一条材料自己给自己作证两次**，
        绕过 AC-7。按来源去重后 `occurrences` 恒等于 `distinct_sources`。

        失败不致命：判定拿不到就退回 `UNKNOWN`（继续按 AC-7 不进 `traits`），
        但必须留告警（G6-D4：记录为"未判定"）。

        **只判定没有明确声明的证据**（`stability == UNKNOWN`）。真实链路里抽取器
        永远给 `UNKNOWN`（PF-V2 不允许模型自评），所以生产路径都会判；而测试夹具、
        显式种子、将来的人工标注若已经写了 `REPEATED` / `PATTERN`，那是**更强的
        信息**，机械规则不该把它盖掉（与 G7「不降级覆盖」同一精神）。
        计数时**仍把已声明的成员算作一次出现**——它确实是一个独立来源的佐证，
        只是不需要再判一次。
        """
        judged: dict[str, Stability] = {}
        if not items:
            return judged
        unassessed: list[str] = []
        for (claim_key, _subjects), members in self._stability_groups(items).items():
            pending = [i for i in members
                       if _stability_of(getattr(i, "stability", None)) == Stability.UNKNOWN]
            if not pending:
                continue          # 全都已明确声明，无需判定也不需要写库
            by_source: dict[str, Any] = {}
            for item in members:      # ← 用**全部**成员计数，不只 pending
                sid = _text(getattr(item, "source_id", "")) or f"__no_source__:{item.id}"
                by_source.setdefault(sid, item)
            representatives = list(by_source.values())
            subject_id = next(iter(_str_list(members[0].subject_entity_ids)), "")
            verdict = self.deps.evidence.judge_stability(
                ctx, subject_id, claim_key, [str(i.id) for i in representatives])
            if isinstance(verdict, Err):
                unassessed.extend(str(i.id) for i in pending)
                continue
            # 只把结论写给**未声明**的那些；已声明的行保持原样（不覆盖）
            recorded = self.deps.evidence.record_stability_verdict(
                ctx, [str(i.id) for i in pending], verdict.value)
            if isinstance(recorded, Err):
                unassessed.extend(str(i.id) for i in pending)
                continue
            for item in pending:
                judged[str(item.id)] = verdict.value.stability
        if unassessed:
            warnings.append(WarningNote(
                code=WARNING_STABILITY_UNASSESSED,
                message=f"{len(unassessed)} 条证据未取得稳定性判定"
                        f"（{_id_sample(unassessed)}），按 `UNKNOWN` 处理——"
                        "这些证据**不会**进入 `traits`（AC-7 保守口径）"))
        return judged

    @staticmethod
    def _build_persona_draft(
        entity_id: str, items: list[Any], entity_type: str,
        stabilities: dict[str, Stability] | None = None,
    ) -> PersonaProfileDraft:
        """证据 → 档案条目（**按维度搬运，不做语义理解**）。

        AC-7：`stability` 为 `SINGLE` / `UNKNOWN` 的 TRAIT 证据进 `single_events`，
        **绝不**进 `traits`（PF-V2 / D-012：单次行为不得固化成稳定性格）。

        `stabilities`（G6）是 `{证据 ID: 判定结果}`，来自 `judge_stability`；
        为 `None` 时**逐字**退回旧行为（读证据自身登记的 `stability`）——
        脚本与既有测试直接调本方法，不能因为加参数就改变它们的结果。

        取值优先级：**判定结果 > 证据自带的 `effective_stability`**。
        后者见 `EvidenceView.effective_stability`（附加字段优先，其次是登记值）。

        PF-V5 / D-115：虚构角色必须声明适用版本且**必须包含自身**；
        真人没有改编版本，`applicable_variant_ids` 与 `canon_boundary` 必须为空。
        """
        background: list[str] = []
        background_refs: list[str] = []
        traits: list[ProfileItem] = []
        single_events: list[ProfileItem] = []
        habits: list[ProfileItem] = []
        expression: list[str] = []
        expression_refs: list[str] = []
        experiences: list[TimelineItem] = []
        canon_refs: list[str] = []
        non_canon_refs: list[str] = []
        variants: list[str] = []

        for item in items:
            evidence_id = str(item.id)
            claim = _text(item.claim)
            if stabilities is not None and evidence_id in stabilities:
                stability = stabilities[evidence_id]
            else:
                stability = _stability_of(
                    getattr(item, "effective_stability", None) or item.stability)
            refs = [evidence_id]
            variants.extend(_str_list(item.applicable_variant_ids))
            dimension = _dimension_of(item.dimension)

            # item_id 由证据 ID 推导——**可复现**：重跑时条目身份不变，
            # `PersonaService` 才能判定"内容与最新版一致"而不生成空转版本。
            item_id = f"pi_{evidence_id}"
            if dimension == EvidenceDimension.BACKGROUND:
                background.append(claim)
                background_refs.append(evidence_id)
            elif dimension == EvidenceDimension.TRAIT:
                entry = ProfileItem(item_id=item_id, text=claim,
                                    confidence=0.0, evidence_refs=refs,
                                    stability=stability)
                if stability in _NON_TRAIT_STABILITIES:
                    single_events.append(entry)
                else:
                    traits.append(entry)
            elif dimension == EvidenceDimension.HABIT:
                habits.append(ProfileItem(item_id=item_id, text=claim,
                                          confidence=0.0, evidence_refs=refs,
                                          stability=stability))
            elif dimension == EvidenceDimension.SPEECH_STYLE:
                expression.append(claim)
                expression_refs.append(evidence_id)
            elif dimension == EvidenceDimension.EXPERIENCE:
                experiences.append(TimelineItem(item_id=item_id, text=claim,
                                                confidence=0.0, evidence_refs=refs,
                                                stability=stability))
            elif dimension == EvidenceDimension.CANON_FACT:
                if item.canon_level == CanonLevel.CANON and item.source_id:
                    canon_refs.append(str(item.source_id))
                elif item.source_id:
                    non_canon_refs.append(str(item.source_id))

        fictional = entity_type != EntityType.REAL_PERSON
        canon = None
        if fictional and (canon_refs or non_canon_refs):
            canon = CanonBoundary(
                canonical_material_refs=sorted(set(canon_refs)),
                non_canonical_material_refs=sorted(set(non_canon_refs)),
                note="正典边界由证据的 canon_level 机械归集；**只登记，不裁决**材料真伪。")

        # PF-V5：虚构必须声明且含自身；D-115：真人一律为空
        if fictional:
            variants = sorted(set(variants) | {entity_id})
        else:
            variants = []

        # D-020 / D-117：没有任何证据支撑的内容时必须说明缺什么（不许交白卷）
        has_content = bool(background or traits or habits or expression
                           or experiences or single_events)
        open_questions: list[str] = []
        if not items:
            open_questions.append("尚无可用证据，人物档案各维度待补")
        elif not has_content:
            open_questions.append(
                "本次证据中没有可用于人物档案的维度"
                "（例如 RELATION 维度归关系档案），人物档案各维度待补")
        return PersonaProfileDraft(
            entity_id=entity_id,
            background=ProfileSection(text=" ".join(background), confidence=0.0,
                                      evidence_refs=background_refs),
            traits=traits,
            habits=habits,
            expression_style=ProfileSection(text=" ".join(expression), confidence=0.0,
                                            evidence_refs=expression_refs),
            experiences=experiences,
            single_events=single_events,
            canon_boundary=canon,
            applicable_variant_ids=variants,
            open_questions=open_questions,
            status=ProfileStatus.OK if items else ProfileStatus.INSUFFICIENT_EVIDENCE,
            change_reason="",
        )

    def _write_persona(
        self, ctx: CallContext, entity_id: str, draft: PersonaProfileDraft,
        notes: list[WarningNote] | None = None,
    ) -> tuple[str, bool] | Err:
        """写档案。已存在则 `revise`；内容未变 / 本次产出为空 → **复用旧版，不产生新版本**。

        返回 `(profile_id, already_current)`。`notes` 非空时，把模块层返回的告警
        （如 G7 的 `PROFILE_DEGRADED_SKIPPED`）原样带出去，供步骤级告警使用——
        否则"空产出被拦下"这件事在任务视图里看不到。
        """
        draft.change_reason = "业务编排：按本次登记的证据重建档案"
        existing = self.deps.persona.get_persona(ctx, entity_id)
        if is_ok(existing):
            revised = self.deps.persona.revise(ctx, entity_id, draft)
            if is_err(revised):
                if revised.error.code == ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE:
                    return (str(existing.value.id), True)
                return revised
            if notes is not None:
                notes.extend(getattr(revised, "warnings", []) or [])
            # G7：模块层拦下"空产出覆盖"时返回的是**旧版**，版本号没有前进；
            # 只有版本号前进才算"真的产生了新版本"。
            return (str(revised.value.id),
                    int(revised.value.version) <= int(existing.value.version))
        created = self.deps.persona.create_profile(ctx, draft)
        if is_err(created):
            if created.error.code == ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE:
                again = self.deps.persona.get_persona(ctx, entity_id)
                if is_ok(again):
                    return (str(again.value.id), True)
            return created
        return (str(created.value.id), False)

    # ================================================================ ④ 关系档案

    def _step_relationship(
        self, ctx: CallContext, request: BusinessRequest,
        prior: dict[str, list[str]],
    ) -> BusinessStepResult | Err:
        """关系**必须有证据**：只用 `dimension=RELATION` 的证据，绝不从两份人物档案拼。

        G-4（RUNNING §5.12）：若注入了 `relationship_analyzer`，则把已登记的
        RELATION 证据交给它做**语义研判**，把产出翻译为 `RelationshipDraft`；
        否则退回「编排器不做语义判断」的兜底实现（群体互动只记 note，维度留空）。
        两条路径共用 RL-V3 / RL-V6 等校验，都不伪造证据。
        """
        participants = [_text(t.entity_id) for t in request.targets if _text(t.entity_id)]
        evidence_ids = list(prior.get("evidence_ids", []))
        warnings: list[WarningNote] = []

        if len(participants) < 2:
            # G9：单人物时阶段按语义跳过 —— 但**本次可能确实登记了 RELATION 证据**。
            # 这些证据既不进人物档案（`_build_persona_draft` 没有 RELATION 分支），
            # 也进不了关系档案（没建立），因此**不会被任何档案消费**。
            # 修复前这条路悄无声息，运维只能看到"阶段 SKIPPED"，
            # 完全不知道那几条证据去哪了。这里必须显式说出来。
            suspended = self._relation_evidence_of(ctx, request, evidence_ids)
            if isinstance(suspended, Err):
                warnings.append(WarningNote(
                    code=WARNING_RELATION_EVIDENCE_UNUSED_OTHER,
                    message="参与者不足 2 人，关系阶段跳过；但统计本次 RELATION 证据"
                            f"失败（{error_of(suspended).code}），无法确认是否存在"
                            "被悬空的证据——请检查证据读取"))
            elif suspended:
                warnings.append(WarningNote(
                    code=WARNING_RELATION_STAGE_SKIPPED_WITH_EVIDENCE,
                    message=f"参与者只有 {len(participants)} 人，关系阶段按语义跳过；"
                            f"但本次登记了 {len(suspended)} 条 RELATION 证据"
                            f"（{_id_sample([str(i.id) for i in suspended])}），"
                            "它们不会被任何档案消费（人物档案没有 RELATION 维度，"
                            "关系档案未建立）——要消费请提供 ≥2 名参与者"))
            return BusinessStepResult(
                name=STAGE_RELATIONSHIP, status=STEP_SKIPPED,
                warnings=warnings,
                message=f"参与者不足 2 人（{len(participants)}），无法建立关系档案。")

        loaded_relation = self._relation_evidence_of(ctx, request, evidence_ids)
        if isinstance(loaded_relation, Err):
            return loaded_relation
        relation_items: list[Any] = loaded_relation

        cardinality = Cardinality.GROUP if len(participants) >= 3 else Cardinality.PAIR
        types = self._entity_types(ctx, participants)
        if isinstance(types, Err):
            return types
        # PF-V5 / D-115：覆盖全部虚构参与者，且不得含真人
        variants = sorted({p for p in participants
                           if types.get(p) != EntityType.REAL_PERSON})

        if self.deps.relationship_analyzer is not None and relation_items:
            # —— G-4 路径：注入的语义分析器做研判 ——
            outcome = self.deps.relationship_analyzer.analyze(
                ctx, RelationshipAnalysisRequest(
                    participant_entity_ids=participants,
                    relation_evidence=relation_items,
                    cardinality=cardinality))
            if isinstance(outcome, Err):
                return outcome
            # G5 同型：分析器自己的告警（含它内部"主体不足"的分类计数）必须透传，
            # 否则"为什么关系档案是空的"在任务视图里又变成一句笼统话。
            warnings.extend(getattr(outcome.value, "warnings", []) or [])
            draft = self._draft_from_analysis(
                outcome.value, participants, cardinality, variants)
            # 分析失败：证据交进去了，分析器却一个条目都没产出
            if relation_items and not RelationshipService._has_any_content(draft):
                warnings.append(WarningNote(
                    code=WARNING_RELATION_ANALYSIS_FAILED,
                    message=f"{len(relation_items)} 条 RELATION 证据已交给分析器，"
                            "但未产出任何条目（分析失败）——关系档案仅记 "
                            "open_questions，不补全、不臆造"))
        else:
            # —— 兜底路径（未注入分析器）：不做语义判断 ——
            draft = self._relationship_draft_fallback(
                participants, cardinality, relation_items, variants, warnings)

        before = len(warnings)
        written = self._write_relationship(ctx, participants, draft, warnings)
        if isinstance(written, Err):
            return written
        profile_id, already = written
        if already and len(warnings) == before:
            warnings.append(WarningNote(
                code=WARNING_PROFILE_ALREADY_CURRENT,
                message="关系档案已是最新，未生成新版本（不重复更新）"))
        if already and relation_items:
            # G9 的"其他"落点：本次证据一条都没写进新版本（复用既有版本）。
            # 可能是内容本来就一致（无害重跑），也可能是空产出被降级拦下（G7）。
            # 两种情况都该让运维看见"这 N 条证据本次没有产生任何写入"。
            warnings.append(WarningNote(
                code=WARNING_RELATION_EVIDENCE_UNUSED_OTHER,
                message=f"本次 {len(relation_items)} 条 RELATION 证据未写入新版本"
                        "（关系档案复用了既有版本）——若这非预期，检查是否"
                        "重复运行或写入被拦下"))
        return BusinessStepResult(
            name=STAGE_RELATIONSHIP, status=STEP_EXECUTED,
            artifact_ids={"relationship_profile_ids": [profile_id]},
            warnings=warnings,
            message=f"关系档案 {cardinality.value}（{len(participants)} 人，"
                    f"{len(relation_items)} 条 RELATION 证据支撑；"
                    f"语义分析={self.deps.relationship_analyzer is not None})")

    # ------------------------------------------------------- 兜底：不做语义判断

    def _relationship_draft_fallback(
        self, participants: list[str], cardinality: Cardinality,
        relation_items: list[Any], variants: list[str],
        warnings: list[WarningNote],
    ) -> RelationshipDraft:
        """未注入分析器时的兜底：只把 RELATION 证据搬进 shared_experiences，
        群体互动仅记 note，不做任何语义断言（与 G-4 之前的行为一致）。"""
        shared: list[SharedExperienceItem] = []
        group: GroupDynamics | None = None
        # G9：主体不足的证据不再"凭空消失"，按 ID 收集后统一报数
        insufficient: list[str] = []

        for item in relation_items:
            covered = [p for p in participants
                       if p in set(_str_list(item.subject_entity_ids))]
            if len(covered) < 2:
                insufficient.append(str(item.id))
                continue
            shared.append(SharedExperienceItem(
                item_id=f"pi_{item.id}", text=_text(item.claim),
                confidence=0.0, evidence_refs=[str(item.id)],
                stability=_stability_of(
                    getattr(item, "effective_stability", None) or item.stability),
                participant_entity_ids=covered))

        if insufficient:
            warnings.append(WarningNote(
                code=WARNING_RELATION_SUBJECT_INSUFFICIENT,
                message=f"{len(insufficient)} 条 RELATION 证据未覆盖 ≥2 名本次参与者"
                        f"（{_id_sample(insufficient)}），未进关系档案——"
                        "常见原因：材料只描述了单人的事，或只提到本次之外的第三人。"
                        "这不是丢数据，是这些证据不能支撑'共同经历'"))

        if cardinality == Cardinality.GROUP:
            # RL-V3 强制：≥3 人必须有 group_dynamics。
            # 编排器**不做语义判断**，不把"共同出现"断言成联盟 / 冷落 / 传话；
            # 群体级证据落在 shared_experiences，维度本身如实说明当前填充程度。
            group = GroupDynamics(
                note="群体互动（联盟 / 冷落 / 传话）尚未从材料中自动抽取："
                     "编排器未注入关系分析器，不做语义判断，群体级证据已记入 "
                     "shared_experiences（G-4 缺口待注入分析器后闭合）。")

        if not relation_items:
            # 只有"本次一条 RELATION 证据都没有"才配这句话。
            # 修复前它也在"有证据但全被筛掉"时出现，把
            # "材料里没有双人句" 误报成 "没有 RELATION 证据"，运维无从判断。
            warnings.append(WarningNote(
                code=WARNING_NO_RELATION_EVIDENCE,
                message="没有可用的 RELATION 维度证据，关系档案各维度留空并记 open_questions"))

        return RelationshipDraft(
            participant_entity_ids=participants,
            cardinality=cardinality,
            perspectives={p: PerspectiveBlock(entity_id=p) for p in participants},
            shared_experiences=shared,
            group_dynamics=group,
            applicable_variant_ids=variants,
            open_questions=["互动模式待补"] if not shared else [],
            status=ProfileStatus.OK if shared else ProfileStatus.INSUFFICIENT_EVIDENCE,
            change_reason="",
        )

    # ------------------------------------------------------- G-4：分析产出 → 落盘草稿

    def _draft_from_analysis(
        self, outcome: RelationshipAnalysisOutcome, participants: list[str],
        cardinality: Cardinality, variants: list[str],
    ) -> RelationshipDraft:
        """把 `RelationshipAnalysisOutcome` 翻译成 `RelationshipDraft`。

        `item_id` 按「角色 + 证据 ID」确定性生成，保证重跑幂等（revise 不生成
        空转版本）。所有条目都带 `evidence_refs`，且引用已登记证据（RL-V4/RL-V6）。
        """
        perspectives: dict[str, PerspectiveBlock] = {}
        for owner, block in (outcome.perspectives or {}).items():
            view_of_targets: dict[str, list[ProfileItem]] = {}
            for tgt, items in (block.view_of_targets or {}).items():
                view_of_targets[str(tgt)] = [
                    ProfileItem(
                        item_id=f"ra_view_{owner}_{tgt}_{_first_ref(it)}",
                        text=_text(it.text), confidence=0.0,
                        evidence_refs=list(it.evidence_refs),
                        stability=_stability_of(it.stability),
                        uncertainty_note=_text(it.uncertainty_note))
                    for it in items]
            perspectives[str(owner)] = PerspectiveBlock(
                entity_id=str(owner),
                view_of_targets=view_of_targets,
                feeling_toward=[
                    ProfileItem(
                        item_id=f"ra_feel_{owner}_{_first_ref(it)}",
                        text=_text(it.text), confidence=0.0,
                        evidence_refs=list(it.evidence_refs),
                        stability=_stability_of(it.stability),
                        uncertainty_note=_text(it.uncertainty_note))
                    for it in (block.feeling_toward or [])],
                expression_toward=[
                    ProfileItem(
                        item_id=f"ra_expr_{owner}_{_first_ref(it)}",
                        text=_text(it.text), confidence=0.0,
                        evidence_refs=list(it.evidence_refs),
                        stability=_stability_of(it.stability),
                        uncertainty_note=_text(it.uncertainty_note))
                    for it in (block.expression_toward or [])],
                uncertainty_note=_text(block.uncertainty_note))

        shared_experiences = [
            SharedExperienceItem(
                item_id=f"ra_shared_{_first_ref(it)}",
                text=_text(it.text), confidence=0.0,
                evidence_refs=list(it.evidence_refs),
                stability=_stability_of(it.stability),
                occurred_at=it.occurred_at,
                participant_entity_ids=list(it.participant_entity_ids),
                uncertainty_note=_text(it.uncertainty_note))
            for it in (outcome.shared_experiences or [])]

        interaction_patterns = [
            ProfileItem(
                item_id=f"ra_pat_{_first_ref(it)}",
                text=_text(it.text), confidence=0.0,
                evidence_refs=list(it.evidence_refs),
                stability=_stability_of(it.stability),
                uncertainty_note=_text(it.uncertainty_note))
            for it in (outcome.interaction_patterns or [])]

        emotional_expression_habits = [
            ProfileItem(
                item_id=f"ra_emo_{_first_ref(it)}",
                text=_text(it.text), confidence=0.0,
                evidence_refs=list(it.evidence_refs),
                stability=_stability_of(it.stability),
                uncertainty_note=_text(it.uncertainty_note))
            for it in (outcome.emotional_expression_habits or [])]

        temporal_changes = [
            TimelineItem(
                item_id=f"ra_temp_{_first_ref(it)}",
                text=_text(it.text), confidence=0.0,
                evidence_refs=list(it.evidence_refs),
                stability=_stability_of(it.stability),
                occurred_at=it.occurred_at,
                uncertainty_note=_text(it.uncertainty_note))
            for it in (outcome.temporal_changes or [])]

        group: GroupDynamics | None = None
        gd = outcome.group_dynamics
        if cardinality == Cardinality.GROUP and gd is not None:
            group = GroupDynamics(
                alliances=[
                    ProfileItem(
                        item_id=f"ra_al_{_first_ref(it)}",
                        text=_text(it.text), confidence=0.0,
                        evidence_refs=list(it.evidence_refs),
                        stability=_stability_of(it.stability),
                        uncertainty_note=_text(it.uncertainty_note))
                    for it in (gd.alliances or [])],
                exclusion=[
                    ProfileItem(
                        item_id=f"ra_ex_{_first_ref(it)}",
                        text=_text(it.text), confidence=0.0,
                        evidence_refs=list(it.evidence_refs),
                        stability=_stability_of(it.stability),
                        uncertainty_note=_text(it.uncertainty_note))
                    for it in (gd.exclusion or [])],
                relay=[
                    ProfileItem(
                        item_id=f"ra_re_{_first_ref(it)}",
                        text=_text(it.text), confidence=0.0,
                        evidence_refs=list(it.evidence_refs),
                        stability=_stability_of(it.stability),
                        uncertainty_note=_text(it.uncertainty_note))
                    for it in (gd.relay or [])],
                note=_text(gd.note))

        asymmetry_notes = [
            ProfileItem(
                item_id=f"ra_asym_{idx}_{_first_ref(it)}",
                text=_text(it.text), confidence=0.0,
                evidence_refs=list(it.evidence_refs),
                stability=_stability_of(it.stability),
                uncertainty_note=_text(it.uncertainty_note))
            for idx, it in enumerate(outcome.asymmetry_notes or [])]

        has_content = bool(
            shared_experiences or interaction_patterns
            or emotional_expression_habits or temporal_changes
            or any(block.view_of_targets for block in perspectives.values())
            or (group and (group.alliances or group.exclusion or group.relay)))

        return RelationshipDraft(
            participant_entity_ids=participants,
            cardinality=cardinality,
            perspectives=perspectives,
            shared_experiences=shared_experiences,
            interaction_patterns=interaction_patterns,
            emotional_expression_habits=emotional_expression_habits,
            temporal_changes=temporal_changes,
            group_dynamics=group,
            asymmetry_notes=asymmetry_notes,
            conflict_ids=list(outcome.conflict_ids or []),
            applicable_variant_ids=variants,
            open_questions=list(outcome.open_questions or []),
            status=ProfileStatus.OK if has_content
            else ProfileStatus.INSUFFICIENT_EVIDENCE,
            change_reason="",
        )

    def _write_relationship(
        self, ctx: CallContext, participants: list[str], draft: RelationshipDraft,
        notes: list[WarningNote] | None = None,
    ) -> tuple[str, bool] | Err:
        draft.change_reason = "业务编排：按本次登记的证据重建关系档案"
        existing = self.deps.relationship.get_relationship(ctx, list(participants))
        if is_ok(existing):
            revised = self.deps.relationship.revise(ctx, list(participants), draft)
            if is_err(revised):
                if revised.error.code == ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE:
                    return (str(existing.value.id), True)
                return revised
            if notes is not None:
                notes.extend(getattr(revised, "warnings", []) or [])
            # G7：同 `_write_persona`——版本号没前进就是"没产生新版本"（空产出被拦下）
            return (str(revised.value.id),
                    int(revised.value.version) <= int(existing.value.version))
        created = self.deps.relationship.create_relationship(ctx, draft)
        if is_err(created):
            if created.error.code == ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE:
                again = self.deps.relationship.get_relationship(ctx, list(participants))
                if is_ok(again):
                    return (str(again.value.id), True)
            return created
        return (str(created.value.id), False)

    # ================================================================ ⑤ 对话

    def _step_dialogue(
        self, ctx: CallContext, request: BusinessRequest,
        prior: dict[str, list[str]],
    ) -> BusinessStepResult | Err:
        """用**已有的**人物 / 关系档案 + 模拟记忆生成模拟对话。

        模拟产物只进 `Conversation*` 与 `MemoryItem(SIMULATED)`；
        本步骤结束后不会有任何内容回流进证据 / 档案。
        """
        participants = [_text(t.entity_id) for t in request.targets if _text(t.entity_id)]
        persona_ids = list(prior.get("persona_profile_ids", []))
        relationship_ids = list(prior.get("relationship_profile_ids", []))
        if len(participants) < 2 or not persona_ids:
            return BusinessStepResult(
                name=STAGE_DIALOGUE, status=STEP_SKIPPED,
                warnings=[WarningNote(code=WARNING_NO_DIALOGUE_PROFILE,
                                      message="缺少参与者或人物档案，对话未启动")],
                message="对话阶段未执行：没有可用的人物档案。")

        rounds = max(1, int(request.max_rounds))
        draft = SessionDraft(
            participant_entity_ids=participants,
            topic=request.topic,
            persona_profile_ids=participants,
            relationship_profile_ids=relationship_ids,
            max_rounds=rounds,
        )
        started = self.deps.dialogue.start_session(ctx, draft)
        if is_err(started):
            return started
        session_id = started.value.id
        run = self.deps.dialogue.run(ctx, session_id, rounds)
        if is_err(run):
            return run
        turns = list(run.value.turns)
        ended = self.deps.dialogue.end_session(ctx, session_id, "pipeline_completed")
        if is_err(ended):
            return ended
        usage = UsageDelta(requests=len(turns))
        return BusinessStepResult(
            name=STAGE_DIALOGUE, status=STEP_EXECUTED,
            artifact_ids={"session_ids": [session_id],
                          "turn_ids": [str(t.id) for t in turns]},
            message=f"生成 {len(turns)} 轮模拟对话（命名空间恒 SIMULATED）",
            usage=usage)

    # ================================================================ 驱动与汇总

    def _drive(
        self, ctx: CallContext, task_id: str, request: BusinessRequest
    ) -> Result[BusinessRunResult]:
        """交给既有 `TaskRunner` 跑到底，再把结果汇总成 `BusinessRunResult`。"""
        self._request = request
        self._task_id = task_id
        self._steps = {}

        runner = TaskRunner(self.deps.task_service, self)
        outcome = runner.run_to_completion(ctx, task_id)
        error = error_of(outcome) if is_err(outcome) else None

        return Ok(self._summarize(ctx, task_id, error))

    def _summarize(
        self, ctx: CallContext, task_id: str, error: AppError | None
    ) -> BusinessRunResult:
        task = self.deps.task_service.get(ctx, task_id)
        task_status = TaskStatus.FAILED
        budget_used = BudgetUsage()
        if is_ok(task):
            task_status = task.value.status
            budget_used = task.value.budget_used

        checkpoint = self.deps.task_service.load_checkpoint(ctx, task_id)
        artifacts: dict[str, list[str]] = {}
        completed_units: list[str] = []
        if is_ok(checkpoint) and checkpoint.value is not None:
            artifacts = dict(checkpoint.value.artifact_ids)
            completed_units = list(checkpoint.value.completed_units)

        steps: list[BusinessStepResult] = []
        for stage in STAGE_SEQUENCE:
            if stage in self._steps:
                steps.append(self._steps[stage])
            elif stage in completed_units:
                steps.append(BusinessStepResult(
                    name=stage, status=STEP_EXECUTED,
                    message="此前轮次已完成，本轮未重复执行（读自检查点）"))
            else:
                steps.append(BusinessStepResult(name=stage, status=STEP_NOT_REACHED,
                                                message="本轮未到达该阶段"))

        status = self._overall_status(steps, task_status, error)
        warnings: list[WarningNote] = []
        for step in steps:
            warnings.extend(step.warnings)

        return BusinessRunResult(
            task_id=task_id,
            status=status,
            task_status=str(task_status),
            steps=steps,
            source_ids=list(artifacts.get("source_ids", [])),
            evidence_ids=list(artifacts.get("evidence_ids", [])),
            persona_profile_ids=list(artifacts.get("persona_profile_ids", [])),
            relationship_profile_ids=list(artifacts.get("relationship_profile_ids", [])),
            session_id=(artifacts.get("session_ids") or [None])[0],
            turn_ids=list(artifacts.get("turn_ids", [])),
            conflict_ids=list(artifacts.get(ARTIFACT_CONFLICT_IDS, [])),
            warnings=warnings,
            budget_used=budget_used,
            error=error,
        )

    @staticmethod
    def _overall_status(
        steps: list[BusinessStepResult], task_status: TaskStatus, error: AppError | None
    ) -> str:
        """**只有五步全部真正执行、任务 COMPLETED 且无错误才算全流程成功**。"""
        if error is not None:
            return RUN_FAILED
        if task_status == TaskStatus.FAILED:
            return RUN_FAILED
        if task_status == TaskStatus.PAUSED:
            return RUN_PAUSED
        done = {
            step.name for step in steps
            if step.status in (STEP_EXECUTED, STEP_ALREADY_CURRENT)
        }
        if task_status == TaskStatus.COMPLETED and all(s in done for s in STAGE_SEQUENCE):
            return RUN_SUCCEEDED
        return RUN_PARTIAL

    # ================================================================ 内部

    def _entity_types(
        self, ctx: CallContext, entity_ids: list[str]
    ) -> dict[str, str] | Err:
        """只读取 `PersonEntity.entity_type`（`PF-V5` / `D-115` 需要它）。"""
        found: dict[str, str] = {}
        for entity_id in entity_ids:
            row = self.deps.storage.get(ctx, "PersonEntity", entity_id)
            if is_err(row):
                return row
            if row.value is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_ENTITY_MISSING,
                    f"检索目标不是已登记的对象：{entity_id}", trace_ref=ctx.trace_ref))
            found[entity_id] = str(row.value.get("entity_type") or "")
        return found

    def _load_artifacts(self, ctx: CallContext, task_id: str) -> dict[str, list[str]]:
        """从**已落盘的检查点**读前序阶段的产出——这是阶段间传递数据的唯一通道。"""
        if not task_id:
            return {}
        checkpoint = self.deps.task_service.load_checkpoint(ctx, task_id)
        if is_err(checkpoint) or checkpoint.value is None:
            return {}
        return dict(checkpoint.value.artifact_ids)

    @staticmethod
    def _validate_request(ctx: CallContext, request: BusinessRequest) -> Err | None:
        targets = list(request.targets or [])
        if not targets:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "业务请求至少需要一个检索目标", trace_ref=ctx.trace_ref))
        for index, target in enumerate(targets):
            if not _text(target.entity_id):
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"第 {index} 个检索目标缺少 entity_id", trace_ref=ctx.trace_ref))
            if not (_text(target.display_name) or _str_list(target.aliases)):
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"第 {index} 个检索目标既无 display_name 也无 aliases",
                    trace_ref=ctx.trace_ref))
        if len({_text(t.entity_id) for t in targets}) != len(targets):
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "检索目标含重复 entity_id", trace_ref=ctx.trace_ref))
        return None


def _task_id_from_key(idempotency_key: str) -> str:
    """兜底：从 `TaskRunner` 给的下游幂等键 `f"{task_id}:{unit}"` 反解任务 ID。"""
    raw = _text(idempotency_key)
    return raw.rsplit(":", 1)[0] if ":" in raw else ""
