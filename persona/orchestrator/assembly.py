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

from ..config import DEFAULT_MODEL_ID, AppSettings
from ..config.spec import (
    NETWORK_MODEL_PROVIDER_NAMES,
    NETWORK_PROVIDER_NAMES,
    ResearchLimits,
)
from ..core.context import CallContext
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, WarningNote, error_of, is_err, is_ok
from ..core.types import SourceMaterial
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
from ..modules.research import (
    PersonQueryTarget,
    ResearchDeps,
    ResearchService,
    load_local_materials,
)
from ..modules.extract_quality import DEFAULT_MIN_EFFECTIVE_CHARS
from ..providers.base import Provider
from ..providers.mock import FakeProvider
from ..storage.sqlite import SqliteStorage
from .business import BusinessDeps, BusinessOrchestrator
from .model_setup import (
    chat_provider_from_settings,
    claim_extractor_from_settings,
    model_limits_from_settings,
    search_provider_from_settings,
)
from .tasks import TaskService

# --------------------------------------------------------------------------
# 告警码（与既有命名口径一致：大写下划线，不含变量部分）
# --------------------------------------------------------------------------

WARNING_STACK_LEDGER_NOT_HYDRATED = "LEDGER_NOT_HYDRATED"        # 主动关闭了 hydrate
WARNING_STACK_LEDGER_HYDRATION_FAILED = "LEDGER_HYDRATION_FAILED"  # 读历史失败（无累计上限时降级）
WARNING_STACK_SEARCH_OFFLINE = "SEARCH_PROVIDER_OFFLINE"          # 未注入检索 Provider，用离线 Fake
WARNING_STACK_SEARCH_PROVIDER_UNAVAILABLE = "SEARCH_PROVIDER_UNAVAILABLE"  # 按配置构造检索 Provider 失败
WARNING_STACK_EXTRACTOR_ABSENT = "EXTRACTOR_ABSENT"               # 没有可用的 claim 抽取器
WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE = "CHAT_PROVIDER_UNAVAILABLE"  # 按配置构造聊天 Provider 失败
WARNING_STACK_REAL_PROVIDER_BLOCKED = "REAL_PROVIDER_BLOCKED"     # 默认禁止真实外呼，未显式授权
WARNING_STACK_ANALYZER_DISABLED = "RELATIONSHIP_ANALYZER_DISABLED"  # 按配置关闭了规则分析器


def _config_wants_network_provider(settings: AppSettings) -> bool:
    """配置是不是**指着**一个真实模型服务商（与"有没有密钥"无关）。

    只用来决定"要不要报告被闸门拦下"：配置根本没提真实服务商时，走原来的
    构造路径并按 `CHAT_PROVIDER_UNAVAILABLE` 降级，不制造一条无意义的拦截告警。
    """
    name = str(getattr(settings.model, "provider", "") or "").strip().lower()
    return name in NETWORK_MODEL_PROVIDER_NAMES


def _config_wants_network_search(settings: AppSettings) -> bool:
    """配置是不是**指着**一个真实检索服务商（与"有没有密钥"无关）。

    与 `_config_wants_network_provider` 同一用途：只决定"要不要报告被闸门拦下"，
    配置根本没提真实服务商时走原来的构造路径并降级为 `SEARCH_PROVIDER_OFFLINE`。
    """
    name = str(getattr(settings, "provider_name", "") or "").strip().lower()
    return name in NETWORK_PROVIDER_NAMES


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
    #: `True` = 按配置构造**真实检索** Provider（只构造，不发请求）。
    #: 默认 `False`；且同样受 `allow_real` 约束（一条规则管住两类外呼）。
    build_search_provider: bool = False
    enable_relationship_analyzer: bool | None = None
    research_limits: ResearchLimits | None = None
    extraction_strategy: Any = None
    search_metadata: dict[str, Any] = field(default_factory=dict)
    quality_min_effective_chars: int | None = None
    #: D-280 trait_tag 词表。`None` = 用默认 L1（**抽取侧与编排侧同一份**：
    #: 装配时同时喂给 `claim_extractor_from_settings` 和 `BusinessOrchestrator`，
    #: 两侧口径必须一致，否则抽取器放行的 L2 标签在归并侧解析不出族）。
    #: 传**空词表** = 显式关掉标签解析（与 trait_tag 上线前一致）。
    trait_vocabulary: Any = None
    #: D-293 抽取温度。`None` = 生产默认 0.7（`DEFAULT_EXTRACT_TEMPERATURE`）。
    #: **只给对照实验用**：R4.8 传 0 验"同材料同输入是否同输出"。
    #: 不要把它接进产品配置去"调优"——0 会压掉选材与措辞的自然变化。
    #: 对话温度不在这里：那是 `DialogueService` 自己的旋钮（`DIALOGUE_TEMPERATURE`），
    #: 两者刻意不共用。
    extract_temperature: float | None = None


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
# 执行前的"调用清单"（P4.8 / R4 的前置条件）
# --------------------------------------------------------------------------

#: Tavily **basic** 口径（官方 api-credits，2026-10-15 核对）：
#: Search 1 credit/次；Extract 每 5 条成功 URL 1 credit → 0.2 credit/条。
#: 只用于**事前估算**，实际以响应里的 `usage.credits` 为准（账本口径不变）。
SEARCH_CREDITS_PER_CALL = 1.0
EXTRACT_CREDITS_PER_CALL = 0.2


@dataclass(frozen=True)
class CallPlan:
    """一次业务链运行**将要发出**的真实调用（执行前给用户看的账）。

    为什么要有它：授权必须"看过账再签"。一次真实运行会同时触发检索、正文抓取
    与模型调用，三者的计量单位还不一样（credit / USD）。只喊一句
    "要花点钱"不算授权——必须说清"几次、哪一类、大概多少"。
    """

    search_online: bool
    model_online: bool
    searches: int
    extracts_upper_bound: int
    extractions: int
    dialogues: int
    est_search_credits: float | None
    est_model_cost: float | None
    currency: str
    reasons: list[str]
    #: 低质量正文的**补救**抓取上限（D-288 另记）：它与 `max_extracts` 是
    #: **两份额度**（补救不占初始名额）。不计入 `est_search_credits`——
    #: 补救通常因同一 URL 仍不合格而 0 计费，按最坏情况翻倍会把预估报高。
    remediations: int = 0

    def as_lines(self) -> list[str]:
        """给 CLI 直接打印的行（**不含**任何密钥与单价明细）。"""
        lines: list[str] = []
        if self.searches == 0:
            lines.append("  检索            : 0 次")
        else:
            lines.append(f"  检索            : {self.searches} 次"
                         f"（模板查询，basic ≈{self.est_search_credits:g} credits）")
        if self.extracts_upper_bound == 0:
            lines.append("  抓取正文        : 0 次")
        else:
            total = self.extracts_upper_bound + self.remediations
            detail = (f"{self.extracts_upper_bound} 初始 + {self.remediations} 补救"
                      if self.remediations else "`max_extracts` 上限")
            lines.append(f"  抓取正文        : ≤{total} 次（{detail}；"
                         f"补救不占初始名额，单条失败官方不计费）")
        calls = self.extractions + self.dialogues
        if calls == 0:
            lines.append("  模型调用        : 0 次")
        else:
            parts = []
            if self.extractions:
                parts.append(f"{self.extractions} 次抽取（批量）")
            if self.dialogues:
                parts.append(f"{self.dialogues} 次对话")
            lines.append(f"  模型调用        : {' + '.join(parts)}")
        if self.est_model_cost is None:
            lines.append("  预估费用        : 模型侧估不出来（缺单价或 token 估算）")
        elif self.extractions + self.dialogues == 0:
            lines.append("  预估费用        : 0.000000（本次没有模型调用）")
        else:
            lines.append(f"  预估费用        : 模型 ≈{self.est_model_cost:.6f}"
                         f" {self.currency}"
                         + (f"；检索 ≈{self.est_search_credits:g} credits"
                            if self.est_search_credits else ""))
        lines.append("  计量单位        : 检索按 **credit**、模型按 **USD** ——"
                     "两者不可直接相加（账本不做汇率换算）")
        for reason in self.reasons:
            lines.append(f"  说明            : {reason}")
        lines.append("  边界            : 清单之外不会有任何调用；出现清单外的调用即为缺陷")
        return lines


def plan_calls(
    stack: BusinessStack,
    targets: list[Any],
    *,
    materials: list[SourceMaterial] | None = None,
    max_rounds: int = 1,
) -> CallPlan:
    """估算本次运行**会发出**的检索 / 抓取 / 模型调用次数。

    - 给了本地材料 → 研究阶段**不检索**，也不抓取（文件已在盘上）；
    - 检索 Provider 是离线的 `FakeProvider` → 0 次（不联网）；
    - 抓取次数只能给**上限**：要等检索结果才知道有几条可用正文；
    - 模型抽取是**一次批量**（全部材料与目标一个请求）；
    - 对话只在**多目标**时生成（单目标按语义跳过）；**每一轮只生成 1 个 turn**
      （`DialogueService.run(max_rounds)` 每轮调一次模型），所以上限是 `轮数`，
      不是 `目标数 × 轮数`（P4.8 / D-4 修正：此前后者把清单报大了一倍）。
    """
    materials = list(materials or [])
    search_provider = getattr(getattr(stack.research, "deps", None), "provider", None)
    search_online = search_provider is not None and not isinstance(
        search_provider, FakeProvider)
    extractor = getattr(stack, "claim_extractor", None)
    model_online = extractor is not None and not isinstance(
        getattr(extractor, "provider", None), FakeProvider)

    reasons: list[str] = []
    limits = stack.research.deps.effective_limits
    searches = 0
    extracts = 0
    if materials:
        reasons.append(f"本次用本地材料 {len(materials)} 份：材料通道**不检索、不抓取**")
    elif not search_online:
        reasons.append("检索 Provider 离线（FakeProvider）：不联网、不产生检索费用")
    else:
        planned = stack.research.plan_queries(
            stack.ctx, [_query_target(t) for t in targets])
        searches = len(planned.value) if is_ok(planned) else len(targets)
        extracts = int(limits.max_extracts)
        reasons.append(f"检索上限 {limits.max_queries} 次 / 抓取上限 "
                       f"{limits.max_extracts} 次 + 补救 "
                       f"{int(getattr(limits, 'max_remediations', 0) or 0)} 次"
                       "（配置 research.*）")

    extractions = 1 if model_online and (materials or searches) else 0
    # 对话：每轮 1 个 turn（`DialogueService.run` 的循环体只调一次模型），
    # 与 `BusinessOrchestrator.plan()` 的 `STAGE_DIALOGUE: max(1, rounds)` 同口径。
    dialogues = (max(0, int(max_rounds))
                 if model_online and len(targets) >= 2 else 0)
    if model_online and len(targets) < 2:
        reasons.append("单目标：关系与对话按语义跳过，只做 1 次批量抽取")

    per_call = estimate_model_cost(model_limits_from_settings(stack.settings))
    total_calls = extractions + dialogues
    if total_calls == 0:
        est_model: float | None = 0.0
    elif per_call is None:
        est_model = None
    else:
        est_model = per_call * total_calls
    est_credits = (searches * SEARCH_CREDITS_PER_CALL
                   + extracts * EXTRACT_CREDITS_PER_CALL) if searches else 0.0
    return CallPlan(
        search_online=search_online,
        model_online=model_online,
        searches=searches,
        extracts_upper_bound=extracts,
        extractions=extractions,
        dialogues=dialogues,
        est_search_credits=est_credits,
        est_model_cost=est_model,
        currency=model_limits_from_settings(stack.settings).currency,
        reasons=reasons,
        remediations=int(getattr(limits, "max_remediations", 0) or 0),
    )


def _query_target(target: Any) -> PersonQueryTarget:
    """把已登记的目标转成检索目标（只填检索真正用到的字段）。

    两种形状都要认：`PersonQueryTarget`（`register_targets` 的返回值，字段是
    `work_titles` 复数）和别的带 `work_title`（单数）的轻对象。此前只认后者，
    于是"已经是检索目标"的对象反而把作品名清空成 `[]` —— 清单与实际请求
    各按各的口径算，看起来一致，其实都在查光秃秃的人名。
    """
    titles = [str(t) for t in (getattr(target, "work_titles", ()) or []) if str(t)]
    if not titles:
        single = str(getattr(target, "work_title", "") or "")
        titles = [single] if single else []
    return PersonQueryTarget(
        entity_id=str(getattr(target, "entity_id", "")),
        display_name=str(getattr(target, "display_name", "") or ""),
        entity_type=str(getattr(target, "entity_type", "") or ""),
        work_titles=titles,
        adaptation_version=getattr(target, "adaptation_version", None),
    )


def _works_of(payload: dict) -> list[str]:
    """从已登记的实体载荷里取作品名（只取有名字的，不臆造）。"""
    meta = payload.get("fictional_character_meta") or {}
    return [str(work.get("title") or "")
            for work in (meta.get("works") or [])
            if isinstance(work, dict) and str(work.get("title") or "")]


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
        # 作品名/改编版本必须**跟着目标一起走下去**：`plan_queries` 用
        # `work_titles` 拼"基础辨识词"（名字 + 作品名）。此前这里只塞了
        # `display_name`，于是真实检索退化成查两字人名（P4.8 R4 实测抓到：
        # 查询词是「阿灯」「阿雾」），而 CLI 明明强制要求 `--work-title`——
        # 声明了却不用，等于让用户白填。
        targets.append(PersonQueryTarget(
            entity_id=str(created.value),
            display_name=name,
            entity_type=entity_type,
            work_titles=list(_works_of(payload)),
            adaptation_version=(str(payload.get("fictional_character_meta", {})
                                    .get("adaptation_version") or "") or None),
        ))
    return Ok(targets)


# --------------------------------------------------------------------------
# 本地材料（P4.7 离线通道）
# --------------------------------------------------------------------------


def materials_from_files(
    paths: "list[Any]",
    *,
    target_entity_ids: "list[str] | tuple[str, ...]" = (),
    target_names: "list[str] | tuple[str, ...]" = (),
    min_effective_chars: int | None = None,
) -> tuple[list[SourceMaterial], list[WarningNote]]:
    """把**用户给的本地文件**变成可交给编排器的材料（`BusinessRequest.materials`）。

    为什么放在装配层：CLI 只能 import `orchestrator`（F-7 / D-059），
    而真正读文件 + 跑门禁的逻辑属于 `modules.research`。本函数是两者之间
    唯一的那个薄转接点。

    两条边界：

    - **材料只能来自调用方给的路径**：产品代码里没有内置演示数据，
      所以"CLI 能跑出档案"这件事只对**用户自己提供的材料**成立；
    - **门禁结论随材料传递**：判不合格的文件不会出现在返回值里，
      只能在 `warnings` 里看到原因（不静默丢弃）。
    """
    kwargs: dict[str, Any] = {
        "target_entity_ids": list(target_entity_ids or ()),
        "target_names": list(target_names or ()),
    }
    if min_effective_chars is not None:
        kwargs["min_effective_chars"] = int(min_effective_chars)
    else:
        kwargs["min_effective_chars"] = DEFAULT_MIN_EFFECTIVE_CHARS
    return load_local_materials(list(paths or ()), **kwargs)


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
    allow_real: bool = False,
) -> Result[BusinessStack]:
    """按配置装配整条业务链。**只装配，不发请求、不花钱。**

    参数优先级（重要的只有两条）：

    - `claim_extractor` > `chat_provider`：显式注入的抽取器优先（测试/脚本用桩）；
      否则用 `chat_provider` 按配置装配一个真实抽取器；两者都没有 → 不留抽取器
      （此时业务链只能靠 `evidence_seeds` 显式种子，**不会凭空生成 claim**）。
    - `relationship_analyzer` > 配置：显式注入优先；否则按
      `settings.relationship.analyzer` 决定是否装 `RuleRelationshipAnalyzer`。

    `allow_real`（P4.7 整改：**默认禁止真实外呼**）：

    - `False`（默认）时，即使配置写着 `PERSONA_OFFLINE_ONLY=false` 且配了真实服务商，
      本层也**不构造**真实 Provider，只记 `REAL_PROVIDER_BLOCKED` 告警——因为
      "配置允许联网"不等于"这一次运行被授权花钱"（2026-10-17 那次未授权调用就是
      靠"我以为没配密钥"发生的）。
    - 显式注入的 `chat_provider` / `claim_extractor` **不受此闸门影响**：注入是
      调用方的显式动作，闸门拦的是"按配置默默构造"。测试里的桩 Provider 照常可用。

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
        memory_reader=memory,
        # P4.8 / D-3：对话的模型调用也要进**同一本账**。此前这四项一个都没传，
        # 于是对话花的钱只在 `ConversationSession.budget_used` 里有 token，
        # `external_usage_records` 里一行都没有（累计闸门与逐笔对账同时漏账）。
        # 次数上限与抽取器**共用**：`max_calls` 约束的是"本次运行几次模型调用"，
        # 不是"抽取几次"。
        ledger=ledger,
        limits=model_limits_from_settings(settings),
        usage_sink=storage,
        task_id=options.task_id,
        model_hint=settings.model.model_id or DEFAULT_MODEL_ID))
    task_service = TaskService(storage)

    # ---------------- 4. 研究（检索 + 正文抓取） ----------------
    if search_provider is None and options.build_search_provider:
        if not allow_real and _config_wants_network_search(settings):
            # 同一条闸门管住两类外呼：只授权模型、不授权检索，等于悄悄开了检索。
            warnings.append(WarningNote(
                code=WARNING_STACK_REAL_PROVIDER_BLOCKED,
                message=f"默认禁止真实外呼：配置指定了真实检索服务商"
                        f"（provider_name={settings.provider_name!r}），但调用方未显式授权"
                        f"（`allow_real=True` / CLI `--allow-real`），已拒绝构造——"
                        f"本次装配不会产生任何检索请求与费用"))
        else:
            built_search = search_provider_from_settings(settings)
            if is_err(built_search):
                warnings.append(WarningNote(
                    code=WARNING_STACK_SEARCH_PROVIDER_UNAVAILABLE,
                    message=f"按配置构造检索 Provider 失败（{built_search.error.code}）："
                            f"{built_search.error.message}"))
            else:
                search_provider = built_search.value
    if search_provider is None:
        warnings.append(WarningNote(
            code=WARNING_STACK_SEARCH_OFFLINE,
            message="未注入检索 Provider，使用离线 FakeProvider（需要 fixture_key，"
                    "不会联网）。真实检索需显式 `--allow-real`，且先过 Q-02 的渠道约束"))
    research_deps = ResearchDeps(
        provider=search_provider or FakeProvider(),
        evidence=evidence,
        search_metadata=dict(options.search_metadata),
        extraction_strategy=options.extraction_strategy,
        # P4.8 / R3 修的一个真缺口：此前 `options.research_limits` 默认 None，
        # 研究阶段就回落到模块默认值（5/5/3）——**配置里的
        # `PERSONA_RESEARCH_MAX_QUERIES/MAX_EXTRACTS` 在产品路径上被静默忽略**
        # （只有 e2e 脚本显式传了 limits，所以一直没被发现）。
        # 现在默认取 `settings.research`，显式传参仍然优先。
        limits=options.research_limits or settings.research,
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
        if not allow_real and _config_wants_network_provider(settings):
            # 默认拒绝：配置指向真实服务商 ≠ 本次运行被授权真实外呼。
            warnings.append(WarningNote(
                code=WARNING_STACK_REAL_PROVIDER_BLOCKED,
                message=f"默认禁止真实外呼：配置指定了真实模型服务商"
                        f"（model.provider={settings.model.provider!r}），但调用方未显式授权"
                        f"（`allow_real=True` / CLI `--allow-real`），已拒绝构造——"
                        f"本次装配不会产生任何外部请求与费用"))
        else:
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
            ledger=ledger, usage_sink=storage, task_id=options.task_id,
            vocabulary=options.trait_vocabulary,
            temperature=options.extract_temperature)
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
    #: D-280/D-283：词表**两侧同一份**。`StackOptions.trait_vocabulary=None`
    #: 时 `BusinessOrchestrator` 用它自己的默认（L1），与
    #: `claim_extractor_from_settings` 的默认一致；调用方显式注入时，
    #: 两侧都收到同一个对象，抽取侧放行的标签在归并侧才解析得出族。
    orchestrator = BusinessOrchestrator(
        BusinessDeps(
            research=research, evidence=evidence, persona=persona,
            relationship=relationship, dialogue=dialogue,
            task_service=task_service, storage=storage,
            claim_extractor=extractor, relationship_analyzer=analyzer),
        trait_vocabulary=options.trait_vocabulary)

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
