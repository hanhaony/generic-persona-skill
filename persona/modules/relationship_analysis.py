"""关系语义分析（G-4）——把已登记的 RELATION 维度证据转成结构化关系研判。

对应 `INTERFACES.md` §7、`RUNNING.md` §5.12 的 G-4 缺口、以及
`DECISIONS.md` D-215 的已登记约束。

# 职责

把 `Evidence` **已经登记**的、维度为 `RELATION` 的证据（`EvidenceView`）
转成一份**结构化研判**（`RelationshipAnalysisOutcome`）：

  - 双向视角（A→B 与 B→A 分开放，绝不融合成"综合定论"，RL-V5）；
  - 共同经历 / 互动模式 / 情绪与表达习惯 / 时间变化 / 群体互动（≥3 人）。

仅此而已。本模块**不写任何业务表**，也不碰 `RelationshipService` /
`EvidenceService` / `Research`——它只是把证据"读"进来、"研判"出来，
落盘由 `BusinessOrchestrator` 负责（与 `ClaimExtractorPort` 同一套路：
抽取器只产候选、不登记）。

# 非职责（红线）

⛔ **不启动研究流程**：没有 `search` / `collect` / `query_plan`（F-2 同精神）。
   输入是**已经登记的证据**，不是原始材料；找不到证据就如实说"证据不足"。
⛔ **不访问网络、不调真实模型**：`RuleRelationshipAnalyzer` 是确定性关键字规则；
   `MockRelationshipAnalyzer` 是确定性查表（测试材料）；
   `ProviderRelationshipAnalyzer` 只定义契约与错误处理，本阶段不接真实模型。
⛔ **不伪造证据引用**：每个产出条目都必须带 `evidence_refs`，且引用的 ID 来自
   输入证据——结构上就不可能凭空生成关系（RL-V6）。
⛔ **不把"共同出现 / 共同活动 / 同来源提及"当成联盟 / 冷落 / 传话**
   （D-215 硬约束）：群体互动三个板块（alliances / exclusion / relay）只接受
   **明确的结盟 / 排斥 / 中转标记**，单纯共现一律不足以下结论，留空并记说明。
⛔ **不产出"亲密度 / 强度"数值**（RL-V2 / D-048）：本模块任何形状都不许出现
   score / closeness / intimacy 字段。
⛔ **不把系统推断伪装成来源原话 / 已验证事实**：所有条目都追溯至已登记证据，
   系统级说明（note / uncertainty_note）明确标注"规则分析，非语义模型"。

# 能力边界（务必正视）

本阶段的 `RuleRelationshipAnalyzer` 是**离线确定性规则**，不是语义模型。
它能做的：按证据维度归类、按关键字识别重复/情绪/结盟等明显信号、保留不确定
与矛盾、逐条追溯证据。它**做不到**的：真正"读懂"一段对话里的潜台词、判断
某种关系是否稳定、区分"礼貌性疏远"与"真实敌对"。这些都留给未来的真实语义
模型——届时只需替换 `RelationshipAnalyzerPort` 的实现，`BusinessDeps` 注入即可，
接口与数据形状不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..core.context import CallContext
from ..core.enums import Cardinality, Stability
from ..core.errors import ErrorCode, new_error
from ..core.result import Err, Ok, Result, UsageDelta, WarningNote, is_err

if TYPE_CHECKING:  # 避免 L4→L4 运行期耦合：仅作类型注解
    from ..modules.evidence import EvidenceView

# --------------------------------------------------------------------------
# 方法 / 标签常量
# --------------------------------------------------------------------------

ANALYZE_METHOD_RULE = "RULE_BASED_OFFLINE"   # 离线确定性规则（非语义模型）
ANALYZE_METHOD_MOCK = "MOCK_FIXTURE"         # 确定性查表（测试材料）
ANALYZE_METHOD_MODEL = "MODEL"               # 模型驱动（本阶段不接真实模型）

TAG_MOCK_FIXTURE = "mock-fixture"            # 明确标注：这是测试材料，不是真实证据

# 提示码
WARNING_NO_RELATION_INPUT = "NO_RELATION_INPUT"          # 没有可用 RELATION 证据
WARNING_CO_OCCURRENCE_NOT_ALLIANCE = "CO_OCCURRENCE_NOT_ALLIANCE"
WARNING_SINGLE_NOT_PATTERN = "SINGLE_BEHAVIOR_NOT_PATTERN"
WARNING_POSSIBLE_CONTRADICTION = "POSSIBLE_CONTRADICTION"
# G9：覆盖 <2 名参与者的证据不再静默跳过，按数量报出来。
# 与 `orchestrator.business` 里同名常量**同值**——模块层不得 import 编排层（F-1），
# 所以各自定义，值保持一致即可让运维按码统一筛。
WARNING_SUBJECT_INSUFFICIENT = "RELATION_EVIDENCE_SUBJECT_INSUFFICIENT"


# --------------------------------------------------------------------------
# 规则标记（确定性关键字；中文虚构语料友好，不绑定任何真人）
# --------------------------------------------------------------------------

# 群体互动——结盟 / 站队
_ALLIANCE_MARKERS = (
    "结盟", "盟友", "同盟", "联手", "并肩", "一伙", "联盟", "共同对抗",
    "一同", "合伙", "结伙", "抱团",
)
# 群体互动——排斥 / 冷落 / 站队对立
_EXCLUSION_MARKERS = (
    "排斥", "冷落", "排挤", "对立", "疏远", "敌对", "孤立", "针对",
    "不理", "避开", "嫌隙", "反目",
)
# 群体互动——传话 / 信息中转
_RELAY_MARKERS = (
    "传话", "转达", "中间人", "传信", "中转", "代为传达", "捎话", "带话",
    "传口信", "递话",
)
# 互动模式——重复 / 规律（配合 stability 判定"模式"，D-012）
_RECURRENCE_MARKERS = (
    "每天", "总是", "经常", "反复", "每次", "常年", "定期", "一贯",
    "通常", "屡次", "时常", "照例", "日复一日",
)
# 情绪 / 表达习惯——可被规则从正文标记提取的明显信号
_EMOTION_MARKERS = (
    "生气", "愤怒", "微笑", "大笑", "拥抱", "哭泣", "流泪", "冷淡",
    "热情", "敌意", "温柔", "嫌弃", "疼爱", "关心", "指责", "夸奖",
    "赞美", "嘲讽", "讽刺", "害怕", "恐惧", "依赖", "不信任", "信任",
    "嫉妒", "敬佩", "尊敬", "喜欢", "讨厌", "爱", "恨", "护着", "偏袒",
)
# 群体互动的"共现 / 共活动"——这些**不足以**判定结盟 / 冷落 / 传话
_CO_OCCURRENCE_MARKERS = (
    "共同出席", "一同", "一起", "同场", "共处", "同去", "结伴",
    "共同参与", "同台", "同时在场", "都来了",
)


# --------------------------------------------------------------------------
# 产出形状（本模块的私有结构；由编排器翻译为 Relationship 模块的 dataclass）
# --------------------------------------------------------------------------


@dataclass
class RelationshipAnalysisItem:
    """一条研判条目。**必须带 `evidence_refs`**（RL-V4 / RL-V6）。

    不携带 `item_id`：由编排器按"角色 + 证据 ID"确定性生成，保证重跑幂等。
    """

    text: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    stability: Stability | str = Stability.UNKNOWN
    uncertainty_note: str = ""


@dataclass
class RelationshipAnalysisTimelineItem(RelationshipAnalysisItem):
    """带时间的研判条目（时间变化 / 共同经历时间轴）。"""

    occurred_at: Any = None                    # FuzzyDate | None
    participant_entity_ids: list[str] = field(default_factory=list)


@dataclass
class RelationshipAnalysisPerspective:
    """单人视角块（A 怎么看 B，与 B 怎么看 A 分两个块放，RL-V5）。"""

    entity_id: str = ""
    view_of_targets: dict[str, list[RelationshipAnalysisItem]] = field(
        default_factory=dict)
    feeling_toward: list[RelationshipAnalysisItem] = field(default_factory=list)
    expression_toward: list[RelationshipAnalysisItem] = field(default_factory=list)
    uncertainty_note: str = ""


@dataclass
class RelationshipAnalysisGroupDynamics:
    """群体互动研判（≥3 人；PAIR 时此结构为 None）。

    三个板块各自是列表：**仅接受明确标记**，共现不足以下结论（D-215）。
    """

    alliances: list[RelationshipAnalysisItem] = field(default_factory=list)
    exclusion: list[RelationshipAnalysisItem] = field(default_factory=list)
    relay: list[RelationshipAnalysisItem] = field(default_factory=list)
    note: str = ""


@dataclass
class RelationshipAnalysisOutcome:
    """一次研判的完整结果。**候选 ≠ 落盘**：能否写入由 `Relationship` 校验。

    字段与 `RelationshipProfile` 一一对应（除 group_dynamics 仅在 GROUP 时出现）。
    """

    perspectives: dict[str, RelationshipAnalysisPerspective] = field(
        default_factory=dict)
    shared_experiences: list[RelationshipAnalysisTimelineItem] = field(
        default_factory=list)
    interaction_patterns: list[RelationshipAnalysisItem] = field(
        default_factory=list)
    emotional_expression_habits: list[RelationshipAnalysisItem] = field(
        default_factory=list)
    temporal_changes: list[RelationshipAnalysisTimelineItem] = field(
        default_factory=list)
    group_dynamics: RelationshipAnalysisGroupDynamics | None = None
    asymmetry_notes: list[RelationshipAnalysisItem] = field(default_factory=list)
    conflict_ids: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[WarningNote] = field(default_factory=list)
    method: str = ANALYZE_METHOD_RULE
    usage: UsageDelta = field(default_factory=lambda: UsageDelta(requests=1))
    # 能力边界标记：本阶段规则/ Mock 一律为 False；真实语义模型实现应为 True。
    is_semantic_model: bool = False


# --------------------------------------------------------------------------
# 请求形状
# --------------------------------------------------------------------------


@dataclass
class RelationshipAnalysisRequest:
    """一次研判的输入。

    `relation_evidence` 由编排器**预先过滤**为维度 = RELATION 的已登记证据；
    本模块不再碰 `Evidence` 服务（不反向依赖）。
    """

    participant_entity_ids: list[str] = field(default_factory=list)
    relation_evidence: list["EvidenceView"] = field(default_factory=list)
    cardinality: Cardinality | str = Cardinality.PAIR

    def scenario_key(self) -> str:
        """确定性场景键，供 `MockRelationshipAnalyzer` 查表。"""
        return "|".join(sorted(self.participant_entity_ids))


# --------------------------------------------------------------------------
# 端口
# --------------------------------------------------------------------------


@runtime_checkable
class RelationshipAnalyzerPort(Protocol):
    """关系语义分析端口（与 `ClaimExtractorPort` 同一抽象层级）。

    编排器依赖此端口；具体实现可替换（Mock / Rule / 未来真实模型），
    **实现不持有 Evidence / Relationship 的引用**。
    """

    def analyze(
        self, ctx: CallContext, request: RelationshipAnalysisRequest
    ) -> Result[RelationshipAnalysisOutcome]:
        """把 RELATION 证据研判成结构化结果。失败返回 `Err`。"""
        ...


# --------------------------------------------------------------------------
# 内部辅助（规则与 Mock 共用）
# --------------------------------------------------------------------------


def _norm_stability(value: Any) -> Stability:
    try:
        return Stability(str(value))
    except ValueError:
        return Stability.UNKNOWN


def _covered(participants: list[str], subject_ids: list[str]) -> list[str]:
    """取"证据主体"与"本次参与者"的交集（保持 participants 顺序）。"""
    wanted = set(subject_ids)
    return [p for p in participants if p in wanted]


def _has_marker(text: str, markers: tuple[str, ...]) -> bool:
    return any(m in text for m in markers)


def _item(text: str, evidence_id: str, stability: Any,
          uncertainty_note: str = "") -> RelationshipAnalysisItem:
    return RelationshipAnalysisItem(
        text=text, evidence_refs=[str(evidence_id)],
        stability=_norm_stability(stability), uncertainty_note=uncertainty_note)


def _is_co_occurrence_only(text: str) -> bool:
    """判断是否只是"共同出现 / 共同活动"——不足以判定群体互动。"""
    return _has_marker(text, _CO_OCCURRENCE_MARKERS) and not (
        _has_marker(text, _ALLIANCE_MARKERS)
        or _has_marker(text, _EXCLUSION_MARKERS)
        or _has_marker(text, _RELAY_MARKERS))


def _is_group(cardinality: Any) -> bool:
    return cardinality == Cardinality.GROUP or \
        str(cardinality) == str(Cardinality.GROUP)


def _empty_outcome(method: str, *, open_questions: list[str] | None = None,
                   notes: list[str] | None = None,
                   warnings: list[WarningNote] | None = None,
                   ) -> RelationshipAnalysisOutcome:
    return RelationshipAnalysisOutcome(
        method=method,
        open_questions=list(open_questions or []),
        notes=list(notes or []),
        warnings=list(warnings or []),
    )


# --------------------------------------------------------------------------
# Mock：确定性查表（测试材料）
# --------------------------------------------------------------------------


class MockRelationshipAnalyzer:
    """确定性查表（G-2 同套路）。`fixtures` 键 = `scenario_key()`。

    测试用：显式给出某组参与者的研判结果，**不走规则**，便于精确断言
    A→B / B→A 不对称、矛盾、时间变化等场景。标注 `mock-fixture`：
    这是测试材料，**不是真实证据**，不能当成"真实关系研判能力已验证"。
    """

    def __init__(self, fixtures: dict[str, RelationshipAnalysisOutcome]) -> None:
        self.fixtures = fixtures
        self.calls: int = 0
        self.last_request: RelationshipAnalysisRequest | None = None

    def analyze(
        self, ctx: CallContext, request: RelationshipAnalysisRequest
    ) -> Result[RelationshipAnalysisOutcome]:
        self.calls += 1
        self.last_request = request
        key = request.scenario_key()
        if key in self.fixtures:
            outcome = self.fixtures[key]
            outcome.method = ANALYZE_METHOD_MOCK
            outcome.is_semantic_model = False
            return Ok(outcome)
        # 没有对应夹具：返回"证据不足"的安全空结果，不崩溃、不伪造。
        return Ok(_empty_outcome(
            ANALYZE_METHOD_MOCK,
            open_questions=["Mock 未提供该参与者组合的研判夹具：关系证据待补"],
            notes=[f"mock-fixture：未命中场景键 {key!r}，返回空结果"]))


# --------------------------------------------------------------------------
# Rule：离线确定性规则（非语义模型）
# --------------------------------------------------------------------------


class RuleRelationshipAnalyzer:
    """离线确定性规则分析器（**不是语义模型**，见模块文档"能力边界"）。

    把 RELATION 证据按维度归类、按关键字识别明显信号，**逐条保留证据引用**，
    对不确定的部分一律记 `uncertainty_note` / `open_questions`，绝不下结论。
    """

    def analyze(
        self, ctx: CallContext, request: RelationshipAnalysisRequest
    ) -> Result[RelationshipAnalysisOutcome]:
        self.calls = getattr(self, "calls", 0) + 1
        self.last_request = request
        participants = [str(p) for p in request.participant_entity_ids
                        if str(p).strip()]
        if len(participants) < 2:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"关系研判至少需要 2 名参与者，收到 {len(participants)} 名",
                trace_ref=ctx.trace_ref))
        if not request.relation_evidence:
            return Ok(_empty_outcome(
                ANALYZE_METHOD_RULE,
                open_questions=["本次没有 RELATION 维度的已登记证据，关系各维度待补"],
                warnings=[WarningNote(
                    code=WARNING_NO_RELATION_INPUT,
                    message="没有可用的 RELATION 证据，关系研判返回空（不伪造）")]))

        notes: list[str] = [
            "规则分析器为离线确定性实现，不具备真实语义模型的语义理解能力；"
            "所有条目均追溯至已登记证据，未凭空生成（RL-V6）。",
            "单向态度（A 如何看待 B vs B 如何看待 A）若证据为双方共同记录，"
            "则分别保留同一证据引用并标注'未单独确认单向态度'，不强行对称。",
        ]
        warnings: list[WarningNote] = []

        # 群体互动的本地收集器（避免使用实例状态，保证调用间相互隔离/幂等）
        group_alliances: list[RelationshipAnalysisItem] = []
        group_exclusion: list[RelationshipAnalysisItem] = []
        group_relay: list[RelationshipAnalysisItem] = []
        co_occurrence_warned = False

        # —— 收集覆盖 ≥2 名参与者的 RELATION 证据 ——
        covered_items: list[tuple[list[str], Any]] = []
        insufficient_ids: list[str] = []
        for ev in request.relation_evidence:
            covered = _covered(participants,
                               getattr(ev, "subject_entity_ids", []) or [])
            if len(covered) >= 2:
                covered_items.append((covered, ev))
            else:
                # G9：修复前这里是无声跳过，调用方只知道"没产出条目"，
                # 无从判断是材料里没有双人句、还是分析规则没命中。
                insufficient_ids.append(str(getattr(ev, "id", "") or ""))

        if insufficient_ids:
            warnings.append(WarningNote(
                code=WARNING_SUBJECT_INSUFFICIENT,
                message=f"{len(insufficient_ids)} 条 RELATION 证据未覆盖 ≥2 名本次"
                        f"参与者（{', '.join(i for i in insufficient_ids[:3] if i)}"
                        + (f" 等 {len(insufficient_ids)} 条"
                           if len(insufficient_ids) > 3 else "")
                        + "），未参与研判——这些证据支撑不了'共同经历'，"
                          "但数量本身说明材料里确实有 RELATION 维度内容"))

        perspectives: dict[str, RelationshipAnalysisPerspective] = {
            p: RelationshipAnalysisPerspective(entity_id=p) for p in participants}
        shared: list[RelationshipAnalysisTimelineItem] = []
        interaction: list[RelationshipAnalysisItem] = []
        emotional: list[RelationshipAnalysisItem] = []
        temporal: list[RelationshipAnalysisTimelineItem] = []
        conflict_ids: list[str] = []
        asymmetry: list[RelationshipAnalysisItem] = []

        # 按覆盖对（无序）归集，便于检测矛盾 / 时间变化
        pair_items: dict[tuple[str, str], list[tuple[list[str], Any]]] = {}

        for covered, ev in covered_items:
            ev_id = str(getattr(ev, "id", "") or "")
            claim = str(getattr(ev, "claim", "") or "")
            # G6：优先读稳定性**判定**结果（`effective_stability` = 判定优先，
            # 没有判定时退回登记值）。不这么做的话，判定落库了却没人读，
            # `interaction_patterns` 的模式闸门（只放 REPEATED/PATTERN）
            # 会因为 `stability` 恒为 UNKNOWN 而永远打不开。
            stability = _norm_stability(
                getattr(ev, "effective_stability", None)
                or getattr(ev, "stability", Stability.UNKNOWN))
            stated_at = getattr(ev, "stated_at", None)
            for cid in (getattr(ev, "conflict_ids", []) or []):
                if cid not in conflict_ids:
                    conflict_ids.append(str(cid))

            # 共同经历：凡覆盖 ≥2 人即记录（带时间与逐条目参与者）
            shared.append(RelationshipAnalysisTimelineItem(
                text=claim, evidence_refs=[ev_id], stability=stability,
                occurred_at=stated_at, participant_entity_ids=list(covered)))

            # 双向视角：把同一证据引用放进每个相关参与者的 view_of_targets
            # （诚实：注明是双方共同记录，未单独确认单向态度）
            for obs in covered:
                others = [t for t in covered if t != obs]
                block = perspectives[obs]
                for tgt in others:
                    block.view_of_targets.setdefault(tgt, []).append(_item(
                        claim, ev_id, stability,
                        uncertainty_note="证据为双方共同记录，未单独确认单向态度"))

            # 互动模式（模式闸门 D-012）：仅 REPEATED/PATTERN 且含重复标记
            if stability in (Stability.REPEATED, Stability.PATTERN) \
                    and _has_marker(claim, _RECURRENCE_MARKERS):
                interaction.append(_item(
                    claim, ev_id, stability,
                    uncertainty_note="重复/规律性互动由规则从证据文本+稳定性提取，"
                                    "非语义确认"))
            elif stability == Stability.SINGLE \
                    and _has_marker(claim, _RECURRENCE_MARKERS):
                # 单次行为不得固化成长期模式（AC-7 / D-012 同精神）
                warnings.append(WarningNote(
                    code=WARNING_SINGLE_NOT_PATTERN,
                    message=f"单次行为不固化为互动模式（已归共同经历）："
                            f"{claim[:30]}…"))

            # 情绪 / 表达习惯：含情绪标记时记录（双向，注明未确认归属/方向）
            if _has_marker(claim, _EMOTION_MARKERS):
                emotional.append(_item(
                    claim, ev_id, stability,
                    uncertainty_note="情绪/表达标记由规则从证据文本提取，"
                                    "未单独确认归属与方向"))

            # 时间变化：按覆盖对归集 stated_at
            pair = tuple(sorted(covered))
            pair_items.setdefault(pair, []).append((covered, ev))

            # 群体互动（仅 GROUP）：明确标记才落板块，共现不足以下结论
            if _is_group(request.cardinality):
                if _is_co_occurrence_only(claim):
                    co_occurrence_warned = True
                else:
                    if _has_marker(claim, _ALLIANCE_MARKERS):
                        group_alliances.append(_item(
                            claim, ev_id, stability,
                            uncertainty_note="结盟/站队由规则从证据文本标记提取，"
                                            "需语义复核"))
                    if _has_marker(claim, _EXCLUSION_MARKERS):
                        group_exclusion.append(_item(
                            claim, ev_id, stability,
                            uncertainty_note="排斥/冷落由规则从证据文本标记提取，"
                                            "需语义复核"))
                    if _has_marker(claim, _RELAY_MARKERS):
                        group_relay.append(_item(
                            claim, ev_id, stability,
                            uncertainty_note="传话/中转由规则从证据文本标记提取，"
                                            "需语义复核"))

        # —— 时间变化：同一覆盖对出现多个不同时点 → 时间轴 ——
        for pair, items in pair_items.items():
            starts = [getattr(ev, "stated_at", None) for _, ev in items]
            distinct = {str(s.start) for s in starts
                        if s and getattr(s, "start", None)}
            if len(distinct) >= 2:
                for covered, ev in items:
                    temporal.append(RelationshipAnalysisTimelineItem(
                        text=str(getattr(ev, "claim", "") or ""),
                        evidence_refs=[str(getattr(ev, "id", "") or "")],
                        stability=_norm_stability(
                            getattr(ev, "stability", Stability.UNKNOWN)),
                        occurred_at=getattr(ev, "stated_at", None),
                        participant_entity_ids=list(covered)))

        # —— 可能的矛盾：同一覆盖对存在不同说法 ——
        for pair, items in pair_items.items():
            claims = {str(getattr(ev, "claim", "") or "").strip()
                      for _, ev in items}
            if len(claims) >= 2:
                asymmetry.append(RelationshipAnalysisItem(
                    text=f"覆盖对 {list(pair)} 存在 {len(claims)} 种不同说法；"
                         f"规则未做语义矛盾判定，已分别保留并关联 conflict_ids",
                    evidence_refs=[str(getattr(ev, "id", "") or "")
                                   for _, ev in items],
                    uncertainty_note="可能与矛盾需真实语义模型复核"))
                warnings.append(WarningNote(
                    code=WARNING_POSSIBLE_CONTRADICTION,
                    message=f"覆盖对 {list(pair)} 存在多种说法，已分别保留"))

        # —— 群体互动结构（PAIR 时必须为 None，RL-V3）——
        group: RelationshipAnalysisGroupDynamics | None
        if _is_group(request.cardinality):
            filled = bool(group_alliances or group_exclusion or group_relay)
            if filled:
                note = ("以下群体互动由规则从证据文本标记提取，需真实语义模型复核；"
                        "仅共同出现/共同活动未计入（D-215）。")
            elif co_occurrence_warned:
                note = ("群体互动（联盟/冷落/传话）未自动填充：现有证据仅为共同"
                        "出现/共同活动，不足以判定（D-215 硬约束）；群体级证据已"
                        "记入 shared_experiences，待真实语义模型评估。")
            else:
                note = ("群体互动（联盟/冷落/传话）暂无明确标记证据，留空并记说明"
                        "（不伪造）。")
            group = RelationshipAnalysisGroupDynamics(
                alliances=group_alliances, exclusion=group_exclusion,
                relay=group_relay, note=note)
        else:
            group = None

        # —— 不对称说明：某方向有视角条目而另一方向没有 ——
        # 注意：每条 asymmetry note 都必须带 evidence_refs（RL-V4/RL-V6），
        # 因此只针对"有记录"的方向，引用支撑它的证据，绝不空引用。
        for obs in participants:
            block = perspectives[obs]
            for tgt in participants:
                if tgt == obs:
                    continue
                obs_items = block.view_of_targets.get(tgt, [])
                tgt_items = perspectives[tgt].view_of_targets.get(obs, [])
                obs_has = bool(obs_items)
                tgt_has = bool(tgt_items)
                if obs_has and not tgt_has:
                    refs: list[str] = []
                    for it in obs_items:
                        for r in it.evidence_refs:
                            if r not in refs:
                                refs.append(r)
                    asymmetry.append(RelationshipAnalysisItem(
                        text=f"{obs}→{tgt} 方向有记录而 {tgt}→{obs} 方向无记录，"
                             f"关系呈现不对称（未强行对称）",
                        evidence_refs=refs,
                        uncertainty_note="单向证据缺失，可能为材料覆盖不均"))

        # 为每个空视角块写不确定说明（诚实）
        for obs in participants:
            block = perspectives[obs]
            if not block.view_of_targets and not block.feeling_toward \
                    and not block.expression_toward:
                block.uncertainty_note = "暂无该参与者对其他人的单向视角证据"

        # 证据不足说明
        open_questions: list[str] = []
        has_content = bool(shared or interaction or emotional or temporal
                          or (group and (group.alliances or group.exclusion
                                         or group.relay)))
        if not has_content:
            open_questions.append(
                "RELATION 证据未能支撑任何关系维度，各维度待补（不伪造）")

        if co_occurrence_warned and group is not None and not (
                group.alliances or group.exclusion or group.relay):
            warnings.append(WarningNote(
                code=WARNING_CO_OCCURRENCE_NOT_ALLIANCE,
                message="存在仅共同出现/共同活动的证据：不足以判定联盟/冷落/传话"
                        "（已按 D-215 留空）"))

        return Ok(RelationshipAnalysisOutcome(
            perspectives=perspectives,
            shared_experiences=shared,
            interaction_patterns=interaction,
            emotional_expression_habits=emotional,
            temporal_changes=temporal,
            group_dynamics=group,
            asymmetry_notes=asymmetry,
            conflict_ids=conflict_ids,
            open_questions=open_questions,
            notes=notes,
            warnings=warnings,
            method=ANALYZE_METHOD_RULE,
            is_semantic_model=False,
        ))


# --------------------------------------------------------------------------
# Provider：仅定义契约（本阶段不接真实模型）
# --------------------------------------------------------------------------


class ProviderRelationshipAnalyzer:
    """契约占位（与 `ProviderClaimExtractor` 同口径）。

    本阶段**不接入真实语义模型**：一旦被调用即返回 `EXTERNAL_UNAVAILABLE`，
    绝不假装会"读证据写研判"。未来接入真实模型时，在此实现内调用 Provider，
    接口与产出形状保持不变。
    """

    def analyze(
        self, ctx: CallContext, request: RelationshipAnalysisRequest
    ) -> Result[RelationshipAnalysisOutcome]:
        return Err(new_error(
            ErrorCode.EXTERNAL_UNAVAILABLE,
            "ProviderRelationshipAnalyzer 仅定义契约；本阶段未接入真实语义模型，"
            "请注入 RuleRelationshipAnalyzer / MockRelationshipAnalyzer",
            trace_ref=ctx.trace_ref))
