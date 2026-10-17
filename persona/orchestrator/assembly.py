"""业务链的**装配层 / composition root**（P4.6 / P0-1）。

# 为什么需要这个文件（G4 的同类风险）

在 P4.6 之前，`BusinessOrchestrator` 的依赖**只在 `scripts/` 与 `tests/` 里组装过**：
`persona/cli/main.py` 里一句构造 `BusinessDeps` 的代码都没有。后果不是"少个命令"，
而是每一个"把能力装进产品"的需求都会各自在脚本里再写一遍装配：

    - 关系分析器要装配 → 脚本里写一遍；
    - 账本要落库/hydrate → 脚本里再写一遍；
    - 真实检索要接线 → 第三遍。

三份装配必然漂移（有的传了 `usage_sink`，有的没传；有的 hydrate，有的不 hydrate），
于是"C 轮真实跑过、花了 0.0032838 USD，但这笔钱没进任何库"这种事就会重演。
本模块把装配**收成唯一一份**：`build_business_stack()`。

# 它做什么 / 不做什么

做：
    ✅ 按配置构造全部业务服务（Evidence / Persona / Relationship / Dialogue / Research）
    ✅ 把 `SqliteStorage` 同时接到**用量落库**（`usage_sink`）与**账本 hydrate** 两个口
    ✅ 按 D-D 决定是否注入规则分析器（默认注入，可关）
    ✅ 返回一份可打印的装配事实（`BusinessStack.describe()`），让"为什么没抽到东西"可诊断

不做：
    ⛔ 不发起任何外部调用（构造 Provider 不等于调用 Provider）
    ⛔ 不做业务判断（那是 `BusinessOrchestrator` 与各模块的事）
    ⛔ 不读环境变量（D-077：只有 `persona.config` 能读；本模块只吃 `AppSettings`）
    ⛔ 不打印密钥（`describe()` 里没有任何凭据字段）

# 账本 hydrate 为什么放在这里（G11）

`ExternalUsageLedger` 在 `core`（最底层），**不能** import `storage`（storage 是它的
上层）。所以"从库里读出历史笔、喂给账本"这个动作必须由更上层的装配层完成——
本模块是唯一同时看得到 `SqliteStorage` 与 `ExternalUsageLedger` 的地方。

失败策略（保守优先）：
    - 读历史**失败**且**配了项目累计上限** → 直接 `Err`：读不到累计支出就没法保证
      不超累计上限，装作没事继续花是唯一不可接受的选项；
    - 读历史失败但**没配**累计上限 → 记告警继续（此时没有任何东西依赖这份历史）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from ..config import AppSettings
from ..config.spec import ResearchLimits
from ..core.context import CallContext
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, WarningNote, error_of, is_err
from ..core.usage_ledger import (
    ExternalUsageLedger,
    HydrationSummary,
    estimate_model_cost,
)
from ..modules.dialogue import DialogueDeps, DialogueService
from ..modules.evidence import EvidenceService
from ..modules.memory import MemoryService
from ..modules.persona import PersonaService
from ..modules.relationship import RelationshipService
from ..modules.relationship_analysis import (
    RelationshipAnalyzerPort,
    RuleRelationshipAnalyzer,
)
from ..modules.research import PersonQueryTarget, ResearchDeps, ResearchService
from ..providers.base import Provider
from ..providers.mock import FakeProvider
from ..storage.sqlite import SqliteStorage
from .business import BusinessDeps, BusinessOrchestrator
from .model_setup import (
    chat_provider_from_settings,
    claim_extractor_from_settings,
    model_limits_from_settings,
)
from .tasks import TaskService

# --------------------------------------------------------------------------
# 告警码（与既有命名口径一致：大写下划线，不含变量部分）
# --------------------------------------------------------------------------

WARNING_STACK_LEDGER_NOT_HYDRATED = "LEDGER_NOT_HYDRATED"        # 主动关闭了 hydrate
WARNING_STACK_LEDGER_HYDRATION_FAILED = "LEDGER_HYDRATION_FAILED"  # 读历史失败（无累计上限时降级）
WARNING_STACK_SEARCH_OFFLINE = "SEARCH_PROVIDER_OFFLINE"          # 未注入检索 Provider，用离线 Fake
WARNING_STACK_EXTRACTOR_ABSENT = "EXTRACTOR_ABSENT"               # 没有可用的 claim 抽取器
WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE = "CHAT_PROVIDER_UNAVAILABLE"  # 按配置构造聊天 Provider 失败
WARNING_STACK_ANALYZER_DISABLED = "RELATIONSHIP_ANALYZER_DISABLED"  # 按配置关闭了规则分析器


# --------------------------------------------------------------------------
# 装配选项
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class StackOptions:
    """装配的可选开关（**全部有安全默认值**）。

    `enable_relationship_analyzer=None` 表示"听配置的"
    （`settings.relationship.analyzer`，默认 `rule`）；显式 `True/False` 用于
    测试与一次性覆盖，不写回配置。
    """

    task_id: str | None = None
    hydrate_ledger: bool = True
    #: `True` = 按配置构造**真实**聊天 Provider（只构造，不发请求）。
    #: 默认 `False`：装配层不该"顺手"把一个会花钱的出口建出来，
    #: 必须由调用方显式要求（`persona business run` 才这么做）。
    build_chat_provider: bool = False
    enable_relationship_analyzer: bool | None = None
    research_limits: ResearchLimits | None = None
    extraction_strategy: Any = None
    search_metadata: dict[str, Any] = field(default_factory=dict)
    quality_min_effective_chars: int | None = None


# --------------------------------------------------------------------------
# 装配结果
# --------------------------------------------------------------------------


@dataclass
class BusinessStack:
    """一次装配的**全部产物**（CLI / 脚本 / 测试共用同一份形状）。"""

    settings: AppSettings
    ctx: CallContext
    storage: SqliteStorage
    ledger: ExternalUsageLedger
    evidence: EvidenceService
    persona: PersonaService
    relationship: RelationshipService
    dialogue: DialogueService
    task_service: TaskService
    research: ResearchService
    orchestrator: BusinessOrchestrator
    claim_extractor: Any = None
    relationship_analyzer: RelationshipAnalyzerPort | None = None
    hydration: HydrationSummary | None = None
    warnings: list[WarningNote] = field(default_factory=list)
    owns_storage: bool = False

    # ------------------------------------------------------------------ 展示

    def describe(self) -> dict[str, Any]:
        """装配事实快照（**可直接打印**，不含任何凭据）。"""
        limits = model_limits_from_settings(self.settings)
        return {
            "components": {
                "evidence": type(self.evidence).__name__,
                "persona": type(self.persona).__name__,
                "relationship": type(self.relationship).__name__,
                "dialogue": type(self.dialogue).__name__,
                "research": type(self.research).__name__,
                "task_service": type(self.task_service).__name__,
                "claim_extractor": (type(self.claim_extractor).__name__
                                    if self.claim_extractor is not None else None),
                "relationship_analyzer": (
                    type(self.relationship_analyzer).__name__
                    if self.relationship_analyzer is not None else None),
            },
            "model": {
                "enabled": self.settings.model.enabled(),
                "max_cost_per_task": limits.max_cost,
                "max_cost_total": limits.max_cost_total,
                "max_calls": limits.max_calls,
                "currency": limits.currency,
                "est_cost_per_call": estimate_model_cost(limits),
            },
            "ledger": {
                "hydrated": self.hydration is not None,
                "history": ((self.hydration.as_dict())
                            if self.hydration is not None else None),
                "occupied_total": self.ledger.occupied_cost(),
                "occupied_session": self.ledger.session_occupied_cost(),
                "unpriced_unknown": self.ledger.unpriced_unknown_requests(),
                "currency_invariant": self._currency_invariant(len(
                    self.hydration.currencies) if self.hydration else 0),
            },
            "warnings": [note.code for note in self.warnings],
        }

    def _currency_invariant(self, hydrated_currencies: int) -> str:
        """一本账只允许一种计量单位（装配层的硬规矩）。

        返回 `OK` / `SINGLE_OR_NONE` / `MIXED`。混币时**不阻断**（历史数据可能来自
        早期版本），但必须显式暴露，否则累计比较就是一个错误前提。
        """
        if hydrated_currencies > 1:
            return "MIXED"
        return "SINGLE_OR_NONE"

    # ------------------------------------------------------------------ 收尾

    def close(self) -> None:
        """只关闭自己开的库（外部传进来的由调用方负责）。"""
        if self.owns_storage:
            self.storage.close()


# --------------------------------------------------------------------------
# 目标登记（CLI 与脚本共用，避免 CLI 直接驱动业务模块）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetSpec:
    """要研究的人物（**声明**，不是已经登记好的实体）。"""

    display_name: str
    entity_type: str = "FICTIONAL_CHARACTER"
    work_title: str = ""
    adaptation_version: str = ""


def work_id_for(title: str) -> str:
    """作品 ID：由作品名确定性生成（不臆造作品信息，也不引入随机性）。"""
    import hashlib

    digest = hashlib.sha1(str(title).strip().encode("utf-8")).hexdigest()[:10]
    return f"wrk_{digest}"


def register_targets(
    storage: SqliteStorage,
    ctx: CallContext,
    specs: "list[TargetSpec]",
    *,
    project_id: str | None = None,
) -> Result[list[PersonQueryTarget]]:
    """把人物声明登记成实体，返回可直接交给编排器的目标列表。

    **为什么在装配层而不是 CLI**：CLI 不得直接驱动业务模块（F-7 / D-059）。
    登记 `PersonEntity` 属于"准备输入"，与装配同层更合适；CLI 只做参数解析与展示。

    虚构角色必须声明作品与适用版本（PF-V5 / D-115）：缺了就返回 `Err`，
    **不猜**一个作品名塞进去——那会把"不知道"变成一条假档案。
    """
    from ..core.enums import ProvenanceNamespace

    namespace = ProvenanceNamespace.PUBLIC_SOURCE
    pid = project_id or ctx.project_id
    targets: list[PersonQueryTarget] = []
    for spec in specs:
        name = str(spec.display_name or "").strip()
        if not name:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "人物名不能为空"))
        entity_type = str(spec.entity_type or "FICTIONAL_CHARACTER")
        payload: dict[str, Any] = {
            "display_name": name,
            "entity_type": entity_type,
            "merge_policy": "NEVER_AUTO_MERGE",
            "status": "OK",
        }
        if entity_type == "FICTIONAL_CHARACTER":
            title = str(spec.work_title or "").strip()
            version = str(spec.adaptation_version or "").strip()
            if not (title and version):
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    f"虚构角色 {name} 必须声明所属作品与适用版本"
                    "（PF-V5 / D-115）：请给出 --work-title 与 --adaptation-version，"
                    "或改用 --type REAL_PERSON",
                    trace_ref=ctx.trace_ref))
            payload["fictional_character_meta"] = {
                "works": [{"work_id": work_id_for(title), "title": title,
                           "medium": "ANIMATION"}],
                "adaptation_version": version,
                "official_canon_level": "CANON",
            }
        created = storage.put(ctx, "PersonEntity", payload,
                              project_id=pid, namespace=namespace)
        if is_err(created):
            return Err(created.error)
        targets.append(PersonQueryTarget(entity_id=str(created.value),
                                         display_name=name))
    return Ok(targets)


# --------------------------------------------------------------------------
# 工厂
# --------------------------------------------------------------------------


def build_business_stack(
    settings: AppSettings,
    ctx: CallContext,
    *,
    storage: SqliteStorage | None = None,
    ledger: ExternalUsageLedger | None = None,
    search_provider: Provider | None = None,
    chat_provider: Provider | None = None,
    dialogue_provider: Provider | None = None,
    claim_extractor: Any = None,
    relationship_analyzer: RelationshipAnalyzerPort | None = None,
    options: StackOptions | None = None,
) -> Result[BusinessStack]:
    """按配置装配整条业务链。**只装配，不发请求、不花钱。**

    参数优先级（重要的只有两条）：

    - `claim_extractor` > `chat_provider`：显式注入的抽取器优先（测试/脚本用桩）；
      否则用 `chat_provider` 按配置装配一个真实抽取器；两者都没有 → 不留抽取器
      （此时业务链只能靠 `evidence_seeds` 显式种子，**不会凭空生成 claim**）。
    - `relationship_analyzer` > 配置：显式注入优先；否则按
      `settings.relationship.analyzer` 决定是否装 `RuleRelationshipAnalyzer`。

    `ledger` / `storage` 允许外部注入：测试需要**同一本账**既能被装配层 hydrate、
    又能被断言读数；脚本需要复用临时库。注入的库由调用方负责 `close()`。
    """
    options = options or StackOptions()
    warnings: list[WarningNote] = []
    owns_storage = storage is None

    # ---------------- 1. 存储 ----------------
    if storage is None:
        storage = SqliteStorage(settings.ensure_data_dir())
        owns_storage = True
    # schema 一律由装配层保证（`CREATE TABLE IF NOT EXISTS`，对既有库幂等）。
    # 为什么连"外部传进来的库"也要 init：hydrate 紧接着就要读 external_usage_records，
    # 没建表就会读失败——而"读失败"和"没有历史"必须能区分，不能靠调用方记得先 init。
    initialized = storage.init_schema(ctx)
    if is_err(initialized):
        if owns_storage:
            storage.close()
        return Err(initialized.error)

    # ---------------- 2. 账本：hydrate 历史支出（G11） ----------------
    ledger = ledger if ledger is not None else ExternalUsageLedger()
    hydration: HydrationSummary | None = None
    if options.hydrate_ledger:
        loaded = storage.external_usage_records(ctx)
        if is_err(loaded):
            if settings.model.max_cost is not None:
                # 读不到累计支出 + 配了累计上限 = 无法保证不超支 → 不装配
                if owns_storage:
                    storage.close()
                return Err(new_error(
                    ErrorCode.BUDGET_EXCEEDED,
                    "已配置项目累计费用上限，但无法从存储读取历史外部用量"
                    f"（{error_of(loaded).code}）：读不到累计支出就无法保证不超支，"
                    "拒绝装配（费用未知不等于零）",
                    trace_ref=ctx.trace_ref))
            warnings.append(WarningNote(
                code=WARNING_STACK_LEDGER_HYDRATION_FAILED,
                message=f"读取历史外部用量失败（{error_of(loaded).code}），"
                        "账本按空历史继续；未配置项目累计上限，不影响本次判断"))
        else:
            hydration = ledger.hydrate(
                list(loaded.value),
                unknown_fallback_cost=estimate_model_cost(
                    model_limits_from_settings(settings)))
    else:
        warnings.append(WarningNote(
            code=WARNING_STACK_LEDGER_NOT_HYDRATED,
            message="按要求跳过账本 hydrate：项目累计上限只统计本次进程的支出"
                    "（跨进程累计闸门在本进程**不成立**）"))

    # ---------------- 3. 业务服务（全部复用同一份 storage） ----------------
    evidence = EvidenceService(storage)
    persona = PersonaService(storage)
    relationship = RelationshipService(storage)
    memory = MemoryService(storage)
    dialogue = DialogueService(DialogueDeps(
        profiles=persona, relations=relationship, evidence=evidence,
        memory=memory, storage=storage,
        provider=dialogue_provider or FakeProvider(),
        memory_reader=memory))
    task_service = TaskService(storage)

    # ---------------- 4. 研究（检索 + 正文抓取） ----------------
    if search_provider is None:
        warnings.append(WarningNote(
            code=WARNING_STACK_SEARCH_OFFLINE,
            message="未注入检索 Provider，使用离线 FakeProvider（需要 fixture_key，"
                    "不会联网）。真实检索（H-17 / P3）需单独授权并先定 Q-02/Q-03"))
    research_deps = ResearchDeps(
        provider=search_provider or FakeProvider(),
        evidence=evidence,
        search_metadata=dict(options.search_metadata),
        extraction_strategy=options.extraction_strategy,
        limits=options.research_limits,
        usage_sink=storage,                     # 逐笔用量落库（P4.5 端口，P4.6 才接线）
        task_id=options.task_id,
    )
    if options.quality_min_effective_chars is not None:
        research_deps = replace(
            research_deps,
            quality_min_effective_chars=int(options.quality_min_effective_chars))
    research = ResearchService(research_deps)

    # ---------------- 5. 抽取器（候选生产者，不是验证者） ----------------
    if chat_provider is None and options.build_chat_provider:
        # 只构造、不调用（`chat_provider_from_settings` 不联网）。
        built_chat = chat_provider_from_settings(settings)
        if is_err(built_chat):
            warnings.append(WarningNote(
                code=WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE,
                message=f"按配置构造聊天 Provider 失败（{built_chat.error.code}）："
                        f"{built_chat.error.message}"))
        else:
            chat_provider = built_chat.value

    extractor = claim_extractor
    if extractor is None and chat_provider is not None:
        extractor = claim_extractor_from_settings(
            settings, chat_provider,
            ledger=ledger, usage_sink=storage, task_id=options.task_id)
    if extractor is None:
        warnings.append(WarningNote(
            code=WARNING_STACK_EXTRACTOR_ABSENT,
            message="没有可用的 claim 抽取器（未配置模型 Provider，也未显式注入）："
                    "研究材料不会自动变成候选 claim，业务链只能靠 evidence_seeds"))

    # ---------------- 6. 关系分析器（D-D：默认装配，可关闭） ----------------
    analyzer = relationship_analyzer
    if analyzer is None:
        enabled = options.enable_relationship_analyzer
        if enabled is None:
            enabled = settings.relationship.analyzer_enabled()
        if enabled:
            analyzer = RuleRelationshipAnalyzer()
        else:
            warnings.append(WarningNote(
                code=WARNING_STACK_ANALYZER_DISABLED,
                message="按配置关闭了关系分析器：关系阶段走兜底路径，"
                        "perspectives / feeling_toward 留空（不臆造）"))

    # ---------------- 7. 编排器 ----------------
    orchestrator = BusinessOrchestrator(BusinessDeps(
        research=research, evidence=evidence, persona=persona,
        relationship=relationship, dialogue=dialogue,
        task_service=task_service, storage=storage,
        claim_extractor=extractor, relationship_analyzer=analyzer))

    return Ok(BusinessStack(
        settings=settings,
        ctx=ctx,
        storage=storage,
        ledger=ledger,
        evidence=evidence,
        persona=persona,
        relationship=relationship,
        dialogue=dialogue,
        task_service=task_service,
        research=research,
        orchestrator=orchestrator,
        claim_extractor=extractor,
        relationship_analyzer=analyzer,
        hydration=hydration,
        warnings=warnings,
        owns_storage=owns_storage,
    ))
