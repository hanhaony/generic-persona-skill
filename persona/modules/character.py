"""Character —— 故事人物实例与认知模型（P8.1-B，2026-11-10 设计报告 §六最小范围）。

**定位（P8.1-A-R 修订版 §二）**：
    PersonaProfile（pnf_）是**现实锚**——证据支持的档案，永不因故事改写；
    CharacterInstance（chi_）是**故事化身**——同一档案在不同故事里的独立实例，
    目标/情绪/信念/知识/关系看法全部在实例 payload 里，互不影响。

**职责边界（本阶段做）**：
    ✅ 实例 CRUD（story_id 格式校验、同故事同人去重、rev 乐观锁修订）
    ✅ BehaviorPolicy 确定性装配（纯函数；single_events 与 SINGLE 条目显式排除；
       个人/关系行为分离；无 trigger/action 键——禁止固定 IF-THEN）
    ✅ KnowledgeState 写入校验与隔离查询（七类；TOLD 强制溯源 + 双可信度；
       INFERRED 强制依据；修正链 superseded_by；UNKNOWN 不存条目——缺席表达）
    ✅ LatentRelation 写入校验（origin_refs ≥1；本角色对 target 的单向看法，
       双向分实例存储；**不从档案 feeling_toward 自动换算**）
    ✅ OOC 基础确定性检查 4 条（K1/S1/I1/T1，全部结构化比对，**不是语义理解**）

**本阶段明确不做（P8.1-A-R §五 NOT IMPLEMENTED，不做半实现）**：
    ⛔ LatentRelation 事件驱动演化（数据结构就位，演化逻辑待 P8.2/P8.3）
    ⛔ 自动知识传播（知识条目全部由调用方显式写入）
    ⛔ 语义级 OOC（自由文本台词判定需要模型）
    ⛔ values/motivations 自动填充（起步为空；需 record_inference 或 UserInputRecord）
    ⛔ Dialogue 注入（P8.3 接线；本模块只冻结 `load_knowledge` 接口）

**依赖方向**：只读 PersonaProfile / RelationshipProfile（Reader 口径，同 Dialogue 先例）；
    只写 records(kind="CharacterInstance", namespace=SIMULATED)。EV-V1（SIMULATED
    永不进证据）+ namespace 闸门构成防污染双保险。

**装配层教训（2026-11-05 云端三连炸同族）**：本模块不接 CLI/app，`--mock` 绿 ≠
    产品路径能跑；接入由 P8.3 的装配层负责，届时必须真跑验证。

字段命名纪律：**任何字段名不得命中 score/closeness/intimacy**（evaluation.py
    FORBIDDEN_FIELD_PATTERN AST 扫描，对 modules/ 全目录生效）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from ..core.context import CallContext
from ..core.enums import ProvenanceNamespace, Stability
from ..core.errors import ErrorCode, new_error
from ..core.ids import is_valid_id, new_id
from ..core.result import Err, Ok, Result, is_err
from ..core.temporal import to_iso, utc_now
from ..core.types import Query
from ..storage.sqlite import SqliteStorage
from .persona import PersonaProfileView, PersonaReader
from .relationship import RelationshipView

# --------------------------------------------------------------------------
# 常量（取值域；全部定性，无数值——FORBIDDEN_FIELD_PATTERN 纪律）
# --------------------------------------------------------------------------

KIND = "CharacterInstance"

#: 知识条目七类。**UNKNOWN 不入清单**：'人物对 X 不知情' 由「其知识条目中
#: 不存在关于 X 的 ACTIVE 条目」表达，绝不落成条目（P8.1-A-R 修订 4）。
K_OBSERVED = "OBSERVED"
K_TOLD = "TOLD"
K_INFERRED = "INFERRED"
K_BELIEVED = "BELIEVED"
K_SUSPECTED = "SUSPECTED"
K_SECRET = "SECRET"
KNOWLEDGE_CATEGORIES: frozenset[str] = frozenset(
    {K_OBSERVED, K_TOLD, K_INFERRED, K_BELIEVED, K_SUSPECTED, K_SECRET})

#: 定性可信度（讲述者与内容各一份，互不换算——P8.1-A-R 修订 4）。
CREDIBILITY_LEVELS: frozenset[str] = frozenset(
    {"HIGH", "MEDIUM", "LOW", "UNVERIFIED"})

#: 知识条目状态。修正不删旧条（D-025 精神）：旧条 CORRECTED/SUPERSEDED，
#: 由 superseded_by 指向新条。
KNOWLEDGE_STATUSES: frozenset[str] = frozenset(
    {"ACTIVE", "CORRECTED", "SUPERSEDED", "DROPPED"})

#: 隐性关系信任轴（定性五档，无数值）。
TRUST_LEVELS: frozenset[str] = frozenset(
    {"DISTRUST", "WARY", "NEUTRAL", "WARM", "DEEP"})
#: 隐性关系立场。UNSET = 尚未形成立场。
STANCE_LEVELS: frozenset[str] = frozenset(
    {"UNSET", "ALLY", "RIVAL", "WARY", "DEPENDENT"})

#: 目标优先级与状态（定性）。
GOAL_PRIORITIES: frozenset[str] = frozenset(
    {"PRIMARY", "SECONDARY", "BACKGROUND"})
GOAL_STATUSES: frozenset[str] = frozenset(
    {"ACTIVE", "ACHIEVED", "ABANDONED", "BLOCKED"})

#: 实例生命周期。
INSTANCE_STATUSES: frozenset[str] = frozenset({"ACTIVE", "ENDED"})

#: OOC 报告分级（P8.1-A-R §4.1 明文边界）：
#:   OK            通过（或无结构化信息可查——如实注明，不冒充已检查）
#:   TOLERATED     偏离已声明且动机可追溯——放行并记录为重要行为
#:   SUSPECTED_LEAK  关键词命中秘密——**只报疑似**，处置权在叙事层
#:   FLAGGED       倾向/引用层面的疑似问题——标记不拦截
#:   BLOCKED       结构层面直接矛盾且无解释通道（仅 I1/T1 两类来源）
OOC_OK = "OK"
OOC_TOLERATED = "TOLERATED"
OOC_SUSPECTED_LEAK = "SUSPECTED_LEAK"
OOC_FLAGGED = "FLAGGED"
OOC_BLOCKED = "BLOCKED"

#: 严重度排序（取报告整体结论时用：最高严重度胜出）。
OOC_SEVERITY: tuple[str, ...] = (
    OOC_BLOCKED, OOC_SUSPECTED_LEAK, OOC_FLAGGED, OOC_TOLERATED, OOC_OK)

#: sty_ 标识符格式：前缀 + 32 位十六进制（与 ids.new_id("Story") 输出一致）。
STORY_ID_RE = re.compile(r"^sty_[0-9a-f]{32}$")

#: UNKNOWN **不是合法条目类别**——只存在于拒绝性校验里。
#: "人物对 X 不知情" 由「其知识条目中不存在关于 X 的 ACTIVE 条目」表达。
K_UNKNOWN_PLACEHOLDER = "UNKNOWN"

#: 分页取全的单页大小（P8.1-C F3）：FILTER_KEYS 不含 payload 键，story_id
#: 过滤只能取回后做；单页截断曾致去重/查询漏检，现循环翻页直到取全。
_PAGE_SIZE = 200


# --------------------------------------------------------------------------
# DTO：行为策略（装配产物，不落库，随时可重算）
# --------------------------------------------------------------------------


@dataclass
class BehaviorRule:
    """一条行为倾向。**倾向，不是必然触发器**——没有 trigger/action 键对。

    文本沿用档案/关系档案**原文**（不二次加工），证据引用透传条目自带的
    `evd_`，追溯链不断。
    """

    text: str
    evidence_refs: list[str] = field(default_factory=list)
    stability: str = str(Stability.UNKNOWN)   # PATTERN / REPEATED（唯一强度表达）
    item_ref: str = ""                        # 源档案条目 item_id（供 OOC-I1 定位）


@dataclass
class RelationalRule(BehaviorRule):
    """带关系对象的行为倾向（P8.1-A-R 修订 3：interaction_patterns 的对象信息不丢）。

    只装配**本人单向视角**（与 Dialogue 现行注入口径一致，dialogue.py:1580-1591）；
    A 对 B 与 B 对 A 分键存储，永不合并。
    """

    target_id: str = ""
    direction: str = "self_to_target"


@dataclass
class BehaviorPolicy:
    """从 PersonaProfile + RelationshipProfile 确定性装配的行为策略。

    ⚠️ 装配是**纯函数**：不落库、无 AI、随档案版本重算。values/motivations
    起步恒空（填充通道 NOT IMPLEMENTED，见模块头）——如实留空，不从 traits 猜。
    """

    entity_id: str
    profile_version: int
    relationship_profile_ids: list[str] = field(default_factory=list)
    # —— 个人行为（与特定对象无关的作风）——
    core_dispositions: list[BehaviorRule] = field(default_factory=list)
    situational_patterns: list[BehaviorRule] = field(default_factory=list)
    # —— 关系行为（按 target_id 分键；A→B 与 B→A 天然分离）——
    relational_patterns: dict[str, list[RelationalRule]] = field(
        default_factory=dict)
    # —— 起步为空（NOT IMPLEMENTED）——
    values: list[BehaviorRule] = field(default_factory=list)
    motivations: list[BehaviorRule] = field(default_factory=list)
    internal_conflicts: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)

    def all_rules(self) -> list[BehaviorRule]:
        """三个板块的全部规则（OOC-I1 定位用）。"""
        rules = [*self.core_dispositions, *self.situational_patterns]
        for items in self.relational_patterns.values():
            rules.extend(items)
        return rules

    def all_evidence_refs(self) -> list[str]:
        """策略引用的全部证据 ID（有序去重；OOC-K1 允许集的档案侧来源）。"""
        seen: list[str] = []
        for rule in self.all_rules():
            for ref in rule.evidence_refs:
                if ref and ref not in seen:
                    seen.append(ref)
        return seen


# --------------------------------------------------------------------------
# DTO：故事实例的动态状态（全部内嵌 payload）
# --------------------------------------------------------------------------


@dataclass
class Goal:
    goal_id: str
    text: str
    priority: str = "SECONDARY"
    origin_ref: str = ""          # 来自哪条经历/知识/轮次
    status: str = "ACTIVE"


@dataclass
class EmotionalState:
    mood: str                     # 自由文本（角色自己的话）
    intensity: str = "MEDIUM"     # LOW / MEDIUM / HIGH（定性）
    since_ref: str = ""           # 起因轮次/事件


@dataclass
class Belief:
    belief_id: str
    text: str
    about: str = "world"          # 目标实体 ID 或 "world"
    truth_status: str = "UNVERIFIED"   # TRUE / FALSE / UNVERIFIED——相信≠事实
    formed_from: str = ""         # 来源 KnowledgeEntry id


@dataclass
class StoryEvent:
    event_id: str
    turn: int                     # 故事时钟（轮次号）
    text: str
    ref: str = ""                 # 对应的 ctn_ 轮次或外部引用


@dataclass
class KnowledgeEntry:
    """一条知识。**七类语义见模块头常量**；每条必带来源与双可信度。

    生命周期：写入（ACTIVE）→ 被修正（CORRECTED，superseded_by 指向新条）
    或被取代（SUPERSEDED）/ 废弃（DROPPED）。修正不删旧条（D-025 精神）。
    """

    id: str
    content: str
    category: str
    about: str = "world"
    source_ref: str = ""          # OBSERVED/INFERRED → 亲历轮次；TOLD → 传播轮次/事件
    teller_id: str = ""           # 讲述者（TOLD 必填；其余恒空）
    acquired_turn: int = 0        # 故事时钟；开局知识 = 0
    acquired_at: str = ""
    teller_credibility: str = "UNVERIFIED"
    content_credibility: str = "UNVERIFIED"
    status: str = "ACTIVE"
    superseded_by: str = ""
    keywords: list[str] = field(default_factory=list)   # S1 匹配用别名（可选）


@dataclass
class LatentRelation:
    """**本实例角色**对 target_id 的当前看法（隐性、内部、不向用户显示）。

    - 定性枚举，无数值，不加权求和；同一事件在不同角色处可产生不同变化
      （各自实例各自记录，天然分离）。
    - **必须有故事事件依据**（origin_refs ≥1）才允许存在——档案里的
      feeling_toward 是现实层事实，绝不在本层自动换算（P8.1-A-R 修订 2）。
    - 演化（跳档校验等）是后续阶段能力；本阶段只做写入校验。
    """

    target_id: str
    trust: str = "NEUTRAL"
    stance: str = "UNSET"
    unresolved_conflicts: list[str] = field(default_factory=list)
    origin_refs: list[str] = field(default_factory=list)   # 支撑轮次/事件（≥1）
    updated_at: str = ""


@dataclass
class CharacterInstanceDraft:
    """创建实例的请求。profile_version 钉住档案版本，故事中途档案改版不影响进行中的故事。"""

    story_id: str                 # sty_ 前缀标识符（独立于 css_，桥接属 P8.3）
    character_id: str             # pe_ 实体 ID
    profile_version: int
    display_name: str = ""
    current_goals: list[Goal] = field(default_factory=list)
    current_emotional_state: EmotionalState | None = None
    current_beliefs: list[Belief] = field(default_factory=list)
    relationship_state: list[LatentRelation] = field(default_factory=list)
    personal_experiences: list[StoryEvent] = field(default_factory=list)
    knowledge: list[KnowledgeEntry] = field(default_factory=list)
    behavior_overrides: list[dict] = field(default_factory=list)


@dataclass
class CharacterInstanceView:
    """实例读视图（payload 平铺 + 记录元数据）。"""

    id: str
    story_id: str
    character_id: str
    profile_version: int
    display_name: str
    status: str
    current_goals: list[dict]
    current_emotional_state: dict | None
    current_beliefs: list[dict]
    relationship_state: list[dict]
    personal_experiences: list[dict]
    knowledge: list[dict]
    behavior_overrides: list[dict]
    change_reason: str
    rev: int
    project_id: str
    created_at: str = ""
    updated_at: str = ""


@dataclass
class OOCReport:
    """OOC 检查报告。**只报告、永不改写人物数据**（同 EvaluationPort 定位）。"""

    verdict: str                  # OOC_OK / TOLERATED / SUSPECTED_LEAK / FLAGGED / BLOCKED
    findings: list[dict] = field(default_factory=list)

    def add(self, rule: str, verdict: str, detail: str,
            refs: list[str] | None = None) -> None:
        self.findings.append({
            "rule": rule, "verdict": verdict, "detail": detail,
            "refs": refs or [],
        })
        if OOC_SEVERITY.index(verdict) < OOC_SEVERITY.index(self.verdict):
            self.verdict = verdict


# --------------------------------------------------------------------------
# 纯函数：BehaviorPolicy 装配（P8.1-A-R §2.2）
# --------------------------------------------------------------------------


def _stability_of(item: Any) -> str:
    """条目的有效稳定性：EvidenceView.effective_stability 同口径——档案条目
    在装配时只有登记值（verdict 在证据层），沿用登记值即可。"""
    return str(getattr(getattr(item, "stability", None), "value",
                       getattr(item, "stability", "")))


def _rule_from_item(item: Any) -> BehaviorRule:
    return BehaviorRule(
        text=str(getattr(item, "text", "")),
        evidence_refs=[str(x) for x in (getattr(item, "evidence_refs", None) or [])],
        stability=_stability_of(item),
        item_ref=str(getattr(item, "item_id", "")),
    )


def build_behavior_policy(profile: PersonaProfileView,
                          relationships: list[RelationshipView],
                          ) -> Result[BehaviorPolicy]:
    """从档案 + 关系档案确定性装配行为策略（无 AI、不落库、可重算）。

    纪律（P8.1-A-R 修订 3）：
    - `single_events` 板块**结构性排除**（函数根本不读它）；
    - traits 只收 REPEATED/PATTERN（SINGLE 被 D-012 闸门挡在档案层，这里双保险）；
    - 关系行为只装**本人单向视角**，按 target_id 分键，interaction_patterns
      的对象信息保留；**不做 feeling_toward → trust/stance 换算**。
    """
    if not isinstance(profile, PersonaProfileView):
        return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED, "profile 必须是 PersonaProfileView"))
    policy = BehaviorPolicy(
        entity_id=profile.entity_id,
        profile_version=int(profile.version),
    )
    # 个人：稳定特质（PATTERN）→ 长期作风
    policy.core_dispositions = [
        _rule_from_item(t) for t in profile.traits
        if _stability_of(t) == str(Stability.PATTERN)]
    # 个人：情境倾向（habits 全量 + REPEATED 特质）
    policy.situational_patterns = [
        _rule_from_item(x) for x in
        ([*profile.habits, *[t for t in profile.traits
                             if _stability_of(t) == str(Stability.REPEATED)]])]
    # 关系：本人单向视角 + interaction_patterns（保留 target 信息）
    rel_ids: list[str] = []
    for rel in relationships or []:
        if not isinstance(rel, RelationshipView):
            continue
        if profile.entity_id not in (rel.participant_entity_ids or []):
            continue
        rel_ids.append(rel.id)
        others = [p for p in (rel.participant_entity_ids or [])
                  if p != profile.entity_id]
        block = (rel.perspectives or {}).get(profile.entity_id)
        for target in others:
            bucket = policy.relational_patterns.setdefault(target, [])
            if block is not None:
                for target_items in (block.view_of_targets or {}).values():
                    for item in target_items:
                        bucket.append(_as_relational(_rule_from_item(item), target))
                for item in (block.feeling_toward or []):
                    bucket.append(_as_relational(_rule_from_item(item), target))
                for item in (block.expression_toward or []):
                    bucket.append(_as_relational(_rule_from_item(item), target))
            for item in (rel.interaction_patterns or []):
                bucket.append(_as_relational(_rule_from_item(item), target))
    policy.relationship_profile_ids = rel_ids
    # 装配缺口如实暴露（D-020 精神）
    policy.open_questions = [*profile.open_questions]
    if not policy.values:
        policy.open_questions.append("values: NOT IMPLEMENTED（填充通道未实现，起步为空）")
    if not policy.motivations:
        policy.open_questions.append("motivations: NOT IMPLEMENTED（填充通道未实现，起步为空）")
    return Ok(policy)


def _as_relational(rule: BehaviorRule, target_id: str) -> RelationalRule:
    return RelationalRule(
        text=rule.text, evidence_refs=rule.evidence_refs,
        stability=rule.stability, item_ref=rule.item_ref,
        target_id=target_id, direction="self_to_target")


# --------------------------------------------------------------------------
# 服务：实例 CRUD + 知识/关系写入 + OOC 基础检查
# --------------------------------------------------------------------------


class CharacterService:
    """故事人物实例服务。只写 records(kind="CharacterInstance")，namespace 恒 SIMULATED。

    ⚠️ 装配层教训：本服务不接 CLI/app（P8.3 接线）；`--mock` 绿 ≠ 产品路径能跑。
    """

    def __init__(self, storage: SqliteStorage,
                 persona_reader: PersonaReader | None = None) -> None:
        self.storage = storage
        #: P8.4-B：档案关联校验依赖。None = 跳过校验（直接构造的既有测试/
        #: 脚本保持兼容）；**生产装配（BusinessOrchestrator）必须注入**，
        #: 装配断言测试盯着这条线，防止生产路径静默跳过校验。
        self.persona_reader = persona_reader
        #: P8.4-B：知识写入成功后的显式失效回调（参数 = character_id）。
        #: 由装配层接到 StoryKnowledgeCache.invalidate——写后失效是**显式**
        #: 的，本模块不做也不声称自动失效。
        self._knowledge_write_listener: Callable[[str], None] | None = None

    def set_knowledge_write_listener(
            self, listener: Callable[[str], None] | None) -> None:
        """P8.4-B：注册知识写入成功后的失效回调（显式注入；传 None 注销）。"""
        self._knowledge_write_listener = listener

    # ------------------------------------------------------------ 创建/读取

    def create_instance(self, ctx: CallContext, draft: CharacterInstanceDraft,
                        project_id: str) -> Result[CharacterInstanceView]:
        """创建实例。校验：story_id/character_id 格式、同故事同人去重、写入闸门。"""
        if not STORY_ID_RE.match(str(draft.story_id or "")):
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED, 
                f"story_id 必须是 sty_ 前缀标识符（sty_ + 32 位十六进制），得到 {draft.story_id!r}",
                trace_ref=ctx.trace_ref))
        if not is_valid_id("PersonEntity", draft.character_id):
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED,
                f"character_id 必须是合法 PersonEntity ID（pe_），得到 {draft.character_id!r}",
                trace_ref=ctx.trace_ref))
        if not isinstance(draft.profile_version, int) or draft.profile_version < 1:
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED, "profile_version 必须是 ≥1 的整数",
                trace_ref=ctx.trace_ref))
        # P8.4-B：档案关联校验（注入 PersonaReader 时）。一次调用同时覆盖两条：
        #   - 实体根本没有档案 → NOT_FOUND_ENTITY（"没有人格档案"）
        #   - 有档案但指定版本不存在 → NOT_FOUND_VERSION（附现有版本清单）
        # 只验证**指定版本存在**，不强制最新——实例绑开档时的版本是历史关联
        # 语义；identity 字段（含 profile_version）依旧不可修订。
        if self.persona_reader is not None:
            prof = self.persona_reader.get_persona(
                ctx, draft.character_id, version=draft.profile_version)
            if is_err(prof):
                return prof
        dup = self._find_by_story_and_character(ctx, draft.story_id,
                                                draft.character_id, project_id)
        if is_err(dup):
            return Err(dup.error)
        if dup.value is not None:
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_NO_NEW_EVIDENCE,
                f"故事 {draft.story_id} 中角色 {draft.character_id} 已有实例 "
                f"{dup.value['id']}（同一故事同一角色只允许一个实例）",
                trace_ref=ctx.trace_ref))
        for entry in draft.knowledge:
            check = _validate_knowledge_entry(entry)
            if check is not None:
                return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED, check,
                                     trace_ref=ctx.trace_ref))
        for relation in draft.relationship_state:
            check = _validate_latent_relation(relation, draft.character_id)
            if check is not None:
                return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED, check,
                                     trace_ref=ctx.trace_ref))
        now = to_iso(utc_now())
        payload: dict[str, Any] = {
            "story_id": draft.story_id,
            "character_id": draft.character_id,
            "profile_version": draft.profile_version,
            "display_name": str(draft.display_name or ""),
            "status": "ACTIVE",
            "current_goals": [_goal_to_dict(g) for g in draft.current_goals],
            "current_emotional_state": (
                _emotion_to_dict(draft.current_emotional_state)
                if draft.current_emotional_state is not None else None),
            "current_beliefs": [_belief_to_dict(b) for b in draft.current_beliefs],
            "relationship_state": [_relation_to_dict(r)
                                   for r in draft.relationship_state],
            "personal_experiences": [_event_to_dict(e)
                                     for e in draft.personal_experiences],
            "knowledge": [_knowledge_to_dict(e) for e in draft.knowledge],
            "behavior_overrides": [dict(x) for x in draft.behavior_overrides],
            "change_reason": "初始实例（故事开档）",
        }
        put = self.storage.put(ctx, KIND, payload, project_id=project_id,
                               namespace=ProvenanceNamespace.SIMULATED)
        if is_err(put):
            return Err(put.error)
        # P8.4-B：实例携带知识开档 = 一次知识写入，显式通知失效监听器。
        if draft.knowledge and self._knowledge_write_listener is not None:
            self._knowledge_write_listener(draft.character_id)
        return self.get_instance(ctx, put.value)

    def get_instance(self, ctx: CallContext, instance_id: str
                     ) -> Result[CharacterInstanceView]:
        """按 ID 读实例。不存在 → Err(NOT_FOUND.entity)。"""
        got = self.storage.get(ctx, KIND, instance_id)
        if is_err(got):
            return Err(got.error)
        if got.value is None:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY,
                                 f"CharacterInstance {instance_id} 不存在",
                                 trace_ref=ctx.trace_ref))
        return Ok(_row_to_view(got.value))

    def query_instances_by_story(self, ctx: CallContext, story_id: str,
                                 project_id: str) -> Result[list[CharacterInstanceView]]:
        """按故事查询实例。payload 过滤不在 FILTER_KEYS 白名单——取回后过滤
        （分页取全，P8.1-C F3；量级上来再立项 payload 索引，不动现有表）。"""
        rows = self._all_project_rows(ctx, project_id)
        if is_err(rows):
            return Err(rows.error)
        return Ok([_row_to_view(r) for r in rows.value
                   if r["payload"].get("story_id") == story_id])

    def load_knowledge(self, ctx: CallContext, story_id: str,
                       character_id: str,
                       project_id: str) -> Result[list[dict]]:
        """读取某故事某角色的全部 ACTIVE 知识条目（P8.3 注入接口，已冻结）。

        SECRET 只存在于持有人实例的 payload 中——结构上天然单持有者可读；
        其他实例根本不携带该条目，无需运行时过滤。
        """
        found = self._find_by_story_and_character(ctx, story_id, character_id,
                                                  project_id)
        if is_err(found):
            return Err(found.error)
        if found.value is None:
            return Ok([])
        return Ok([e for e in (found.value["payload"].get("knowledge") or [])
                   if e.get("status") == "ACTIVE"])

    # ------------------------------------------------------------ 修订

    def revise_instance(self, ctx: CallContext, instance_id: str,
                        patch: dict[str, Any], change_reason: str,
                        expected_rev: int | None = None
                        ) -> Result[CharacterInstanceView]:
        """修订实例（乐观锁）。`change_reason` 必填（D-112 口径：版本变化永不静默）。

        patch 只许改**故事态字段**（白名单）；identity 字段（story_id/
        character_id/profile_version）不许改——改了就不是同一个故事化身。
        """
        if not str(change_reason or "").strip():
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "revise 必须带 change_reason（版本变化永不静默）",
                                 trace_ref=ctx.trace_ref))
        current = self.get_instance(ctx, instance_id)
        if is_err(current):
            return Err(current.error)
        immutable = {"story_id", "character_id", "profile_version", "id"}
        bad = immutable & set(patch)
        if bad:
            return Err(new_error(
                ErrorCode.VALIDATION_GATE_REJECTED,
                f"身份字段不可修订：{sorted(bad)}（换故事/换角色请创建新实例）",
                trace_ref=ctx.trace_ref))
        allowed = {"display_name", "status", "current_goals",
                   "current_emotional_state", "current_beliefs",
                   "relationship_state", "personal_experiences", "knowledge",
                   "behavior_overrides"}
        unknown = set(patch) - allowed
        if unknown:
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED,
                                 f"patch 含未知字段：{sorted(unknown)}",
                                 trace_ref=ctx.trace_ref))
        if "status" in patch and str(patch["status"]) not in INSTANCE_STATUSES:
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED,
                                 f"status 取值非法：{patch['status']!r}",
                                 trace_ref=ctx.trace_ref))
        merged = {**_view_payload(current.value), **patch,
                  "change_reason": str(change_reason)}
        # P8.1-C F2：patch 内容过同一套闸门——校验只在专用方法上时，
        # 修订路径就是旁路（可注入 UNKNOWN 类别/无溯源条目/空依据关系看法）。
        if "knowledge" in patch:
            for e in patch["knowledge"]:
                bad_entry = _validate_knowledge_dict(e)
                if bad_entry is not None:
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        f"patch.knowledge 校验失败：{bad_entry}",
                        trace_ref=ctx.trace_ref))
            chain = _validate_knowledge_chain(patch["knowledge"], instance_id)
            if chain is not None:
                return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED,
                                     f"patch.knowledge 校验失败：{chain}",
                                     trace_ref=ctx.trace_ref))
        if "relationship_state" in patch:
            for r in patch["relationship_state"]:
                bad_rel = _validate_relation_dict(r, current.value.character_id)
                if bad_rel is not None:
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        f"patch.relationship_state 校验失败：{bad_rel}",
                        trace_ref=ctx.trace_ref))
        if "current_beliefs" in patch:
            for b in patch["current_beliefs"]:
                if not isinstance(b, dict):
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        "patch.current_beliefs 条目必须是 dict",
                        trace_ref=ctx.trace_ref))
                if not str(b.get("belief_id") or "").strip():
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        "patch.current_beliefs 条目缺 belief_id",
                        trace_ref=ctx.trace_ref))
                if not str(b.get("text") or "").strip():
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        "patch.current_beliefs 条目缺 text",
                        trace_ref=ctx.trace_ref))
        updated = self.storage.update(ctx, KIND, instance_id, merged,
                                      expected_rev=expected_rev)
        if is_err(updated):
            return Err(updated.error)
        # P8.4-B：知识补丁写入成功 = 一次知识写入，显式通知失效监听器
        # （add_knowledge / correct_knowledge 都走本路径，天然被覆盖）。
        if "knowledge" in patch and self._knowledge_write_listener is not None:
            self._knowledge_write_listener(current.value.character_id)
        return Ok(_row_to_view(updated.value))

    # ------------------------------------------------------------ 知识操作

    def add_knowledge(self, ctx: CallContext, instance_id: str,
                      entry: KnowledgeEntry, expected_rev: int | None = None
                      ) -> Result[CharacterInstanceView]:
        """写入一条知识（调用方显式写入——自动传播是 NOT IMPLEMENTED）。"""
        check = _validate_knowledge_entry(entry)
        if check is not None:
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED, check,
                                 trace_ref=ctx.trace_ref))
        current = self.get_instance(ctx, instance_id)
        if is_err(current):
            return Err(current.error)
        knowledge = [dict(e) for e in _view_payload(current.value)["knowledge"]]
        knowledge.append(_knowledge_to_dict(entry))
        return self.revise_instance(
            ctx, instance_id, {"knowledge": knowledge},
            change_reason=f"add_knowledge:{entry.category}:{entry.id}",
            expected_rev=expected_rev)

    def correct_knowledge(self, ctx: CallContext, instance_id: str,
                          entry_id: str, new_content: str,
                          acquired_turn: int, expected_rev: int | None = None,
                          teller_credibility: str = "UNVERIFIED",
                          content_credibility: str = "UNVERIFIED"
                          ) -> Result[CharacterInstanceView]:
        """修正一条知识：旧条 → CORRECTED + superseded_by，新条 ACTIVE。

        修正链校验：被修正条必须是 ACTIVE；修正后链上 CORRECTED 条目必须
        都有 superseded_by 且指向本实例内的条目。
        修正内容的可信度由调用方断言（P8.1-C F4：默认 UNVERIFIED——
        "修正"并不天然可信，修正本身可能基于低可信的新消息）。
        """
        current = self.get_instance(ctx, instance_id)
        if is_err(current):
            return Err(current.error)
        knowledge = [dict(e) for e in _view_payload(current.value)["knowledge"]]
        target = next((e for e in knowledge if e.get("id") == entry_id), None)
        if target is None:
            return Err(new_error(ErrorCode.NOT_FOUND_ENTITY,
                                 f"知识条目 {entry_id} 不存在于实例 {instance_id}",
                                 trace_ref=ctx.trace_ref))
        if target.get("status") != "ACTIVE":
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED,
                                 f"知识条目 {entry_id} 状态为 {target.get('status')}，"
                                 "只有 ACTIVE 条目可被修正",
                                 trace_ref=ctx.trace_ref))
        new_entry = KnowledgeEntry(
            id=new_id("KnowledgeEntry"), content=str(new_content or "").strip(),
            category=str(target.get("category")), about=str(target.get("about", "world")),
            source_ref=f"correction_of:{entry_id}", teller_id="",
            acquired_turn=int(acquired_turn), acquired_at=to_iso(utc_now()),
            teller_credibility=teller_credibility,
            content_credibility=content_credibility,
            status="ACTIVE", keywords=list(target.get("keywords") or []))
        if not new_entry.content:
            return Err(new_error(ErrorCode.VALIDATION_REQUIRED_FIELD,
                                 "修正内容不得为空", trace_ref=ctx.trace_ref))
        target["status"] = "CORRECTED"
        target["superseded_by"] = new_entry.id
        knowledge.append(_knowledge_to_dict(new_entry))
        check = _validate_knowledge_chain(knowledge, instance_id)
        if check is not None:
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED, check,
                                 trace_ref=ctx.trace_ref))
        return self.revise_instance(
            ctx, instance_id, {"knowledge": knowledge},
            change_reason=f"correct_knowledge:{entry_id}->{new_entry.id}",
            expected_rev=expected_rev)

    # ------------------------------------------------------------ 关系看法

    def set_relation(self, ctx: CallContext, instance_id: str,
                     relation: LatentRelation, expected_rev: int | None = None
                     ) -> Result[CharacterInstanceView]:
        """写入/更新本角色对 target 的看法。origin_refs ≥1 强制（无事件依据不产生）。"""
        current = self.get_instance(ctx, instance_id)
        if is_err(current):
            return Err(current.error)
        check = _validate_latent_relation(relation, current.value.character_id)
        if check is not None:
            return Err(new_error(ErrorCode.VALIDATION_GATE_REJECTED, check,
                                 trace_ref=ctx.trace_ref))
        state = [dict(r) for r in _view_payload(current.value)["relationship_state"]]
        relation_dict = _relation_to_dict(relation)
        relation_dict["updated_at"] = to_iso(utc_now())
        existing = next((r for r in state
                         if r.get("target_id") == relation.target_id), None)
        if existing is None:
            state.append(relation_dict)
        else:
            state[state.index(existing)] = relation_dict
        return self.revise_instance(
            ctx, instance_id, {"relationship_state": state},
            change_reason=f"set_relation:{relation.target_id}",
            expected_rev=expected_rev)

    # ------------------------------------------------------------ 内部

    def _find_by_story_and_character(self, ctx: CallContext, story_id: str,
                                     character_id: str, project_id: str
                                     ) -> Result[dict | None]:
        rows = self._all_project_rows(ctx, project_id)
        if is_err(rows):
            return Err(rows.error)
        for row in rows.value:
            payload = row["payload"]
            if (payload.get("story_id") == story_id
                    and payload.get("character_id") == character_id):
                return Ok(row)
        return Ok(None)

    def _all_project_rows(self, ctx: CallContext,
                          project_id: str) -> Result[list[dict]]:
        """分页取全本项目的 CharacterInstance 行（P8.1-C F3）。

        FILTER_KEYS 不含 payload 键，story_id 过滤只能取回后做；曾用单页
        limit=200 静默截断——实例超 200 时去重检查漏检（可重复创建）、
        按故事查询漏返回。现在循环翻页直到取全。
        """
        rows: list[dict] = []
        offset = 0
        while True:
            page = self.storage.query(ctx, KIND,
                                      _query_project(project_id, offset))
            if is_err(page):
                return Err(page.error)
            rows.extend(page.value.items)
            offset += len(page.value.items)
            if offset >= page.value.total or not page.value.items:
                return Ok(rows)


def _query_project(project_id: str, offset: int = 0) -> Query:
    """按 project_id 过滤的单页 Query（FILTER_KEYS 白名单内）。"""
    return Query(filters={"project_id": project_id},
                 limit=_PAGE_SIZE, offset=offset)


# --------------------------------------------------------------------------
# 纯函数：校验（模块层；namespace 闸门在 storage.put 双保险）
# --------------------------------------------------------------------------


def _validate_knowledge_entry(entry: KnowledgeEntry) -> str | None:
    """校验单条知识。返回 None = 合法；返回字符串 = 错误说明。"""
    if not isinstance(entry, KnowledgeEntry):
        return "entry 必须是 KnowledgeEntry"
    if not str(entry.content or "").strip():
        return "知识内容不得为空"
    if entry.category not in KNOWLEDGE_CATEGORIES:
        return (f"category 必须是 {sorted(KNOWLEDGE_CATEGORIES)} 之一，"
                f"得到 {entry.category!r}")
    if entry.category == K_UNKNOWN_PLACEHOLDER:
        return "UNKNOWN 不落条目：'不知情'由 ACTIVE 条目的缺席表达"
    if not str(entry.source_ref or "").strip():
        return (f"category={entry.category} 的 source_ref 必填"
                + ("（TOLD 必须可溯源到具体传播轮次/事件）"
                   if entry.category == K_TOLD else "（无源知识禁止写入）"))
    if entry.category == K_TOLD and not str(entry.teller_id or "").strip():
        return "TOLD 必须记录讲述者 teller_id（P8.1-A-R 修订 4）"
    if entry.category != K_TOLD and str(entry.teller_id or "").strip():
        return f"category={entry.category} 不允许填 teller_id（恒空）"
    for key in ("teller_credibility", "content_credibility"):
        value = getattr(entry, key)
        if value not in CREDIBILITY_LEVELS:
            return f"{key} 必须是 {sorted(CREDIBILITY_LEVELS)} 之一，得到 {value!r}"
    if entry.status not in KNOWLEDGE_STATUSES:
        return f"status 必须是 {sorted(KNOWLEDGE_STATUSES)} 之一，得到 {entry.status!r}"
    if entry.category == K_SECRET and entry.status == "ACTIVE" and entry.acquired_turn < 0:
        return "SECRET 的 acquired_turn 不得为负"
    return None


def _validate_knowledge_chain(knowledge: list[dict], instance_id: str) -> str | None:
    """修正链校验：①条目 ID 在实例内必须唯一（P8.1-C F1——重复 ID 会污染
    K1 集合比对与 superseded_by 指向）；②CORRECTED/SUPERSEDED 条目必须带
    superseded_by 且指向同实例中存在的条目。"""
    ids: set[str] = set()
    for entry in knowledge:
        entry_id = str(entry.get("id") or "")
        if entry_id in ids:
            return f"知识条目 ID 重复：{entry_id}（同一实例内条目 ID 必须唯一）"
        if entry_id:
            ids.add(entry_id)
    for entry in knowledge:
        status = str(entry.get("status"))
        if status in ("CORRECTED", "SUPERSEDED"):
            target = str(entry.get("superseded_by") or "")
            if not target:
                return (f"条目 {entry.get('id')} 状态 {status} 但缺少 superseded_by"
                        f"（修正不删旧条，必须指向新条）")
            if target not in ids:
                return (f"条目 {entry.get('id')} 的 superseded_by={target} 不在本实例中")
    return None


def _validate_latent_relation(relation: LatentRelation,
                              character_id: str) -> str | None:
    """校验一条关系看法。核心：**必须有故事事件依据**（P8.1-A-R 修订 2）。"""
    if not isinstance(relation, LatentRelation):
        return "relation 必须是 LatentRelation"
    if relation.target_id == character_id:
        return "target_id 不得指向自己（对自我的看法属档案/信念层，不在关系看法）"
    if not relation.origin_refs:
        return ("origin_refs 至少 1 条：没有故事事件依据不得产生关系看法"
                "（禁止无依据地把档案 feeling_toward 换算成 trust/stance）")
    if relation.trust not in TRUST_LEVELS:
        return f"trust 必须是 {sorted(TRUST_LEVELS)} 之一，得到 {relation.trust!r}"
    if relation.stance not in STANCE_LEVELS:
        return f"stance 必须是 {sorted(STANCE_LEVELS)} 之一，得到 {relation.stance!r}"
    return None


def _validate_knowledge_dict(e: dict) -> str | None:
    """dict 形状的知识条目校验（P8.1-C F2，revise_instance patch 路径）。

    规则与 _validate_knowledge_entry **对齐**——写入闸门不允许经修订旁路：
    可注入 UNKNOWN 类别/无溯源条目/非法可信度/破坏修正链，与专用方法同拒。
    """
    if not isinstance(e, dict):
        return "条目必须是 dict"
    if not str(e.get("id") or "").startswith("kn_"):
        return "知识条目 ID 必须是 kn_ 前缀"
    if not str(e.get("content") or "").strip():
        return "知识内容不得为空"
    category = str(e.get("category"))
    if category not in KNOWLEDGE_CATEGORIES:
        return f"category 必须是 {sorted(KNOWLEDGE_CATEGORIES)} 之一，得到 {category!r}"
    if category == K_UNKNOWN_PLACEHOLDER:
        return "UNKNOWN 不落条目：'不知情'由 ACTIVE 条目的缺席表达"
    if not str(e.get("source_ref") or "").strip():
        return (f"category={category} 的 source_ref 必填"
                + ("（TOLD 必须可溯源到具体传播轮次/事件）"
                   if category == K_TOLD else "（无源知识禁止写入）"))
    if category == K_TOLD and not str(e.get("teller_id") or "").strip():
        return "TOLD 必须记录讲述者 teller_id（P8.1-A-R 修订 4）"
    if category != K_TOLD and str(e.get("teller_id") or "").strip():
        return f"category={category} 不允许填 teller_id（恒空）"
    for key in ("teller_credibility", "content_credibility"):
        if e.get(key) not in CREDIBILITY_LEVELS:
            return f"{key} 必须是 {sorted(CREDIBILITY_LEVELS)} 之一，得到 {e.get(key)!r}"
    if e.get("status") not in KNOWLEDGE_STATUSES:
        return f"status 必须是 {sorted(KNOWLEDGE_STATUSES)} 之一，得到 {e.get('status')!r}"
    return None


def _validate_relation_dict(r: dict, character_id: str) -> str | None:
    """dict 形状的关系看法校验（P8.1-C F2，revise_instance patch 路径）。

    规则与 _validate_latent_relation 对齐：origin_refs ≥1、不许指向自己、
    trust/stance 取值域——禁止经修订无依据换算档案 feeling_toward。
    """
    if not isinstance(r, dict):
        return "条目必须是 dict"
    if str(r.get("target_id")) == character_id:
        return "target_id 不得指向自己（对自我的看法属档案/信念层，不在关系看法）"
    if not r.get("origin_refs"):
        return ("origin_refs 至少 1 条：没有故事事件依据不得产生关系看法"
                "（禁止无依据地把档案 feeling_toward 换算成 trust/stance）")
    if r.get("trust") not in TRUST_LEVELS:
        return f"trust 必须是 {sorted(TRUST_LEVELS)} 之一，得到 {r.get('trust')!r}"
    if r.get("stance") not in STANCE_LEVELS:
        return f"stance 必须是 {sorted(STANCE_LEVELS)} 之一，得到 {r.get('stance')!r}"
    return None


# --------------------------------------------------------------------------
# 纯函数：OOC 基础确定性检查（P8.1-A-R §4.2，4 条）
#
# 全部为**结构化字段比对 / 关键词启发式**，不是自然语言语义理解。
# 行动提案（proposal）是调用方给出的 dict，字段全可选：
#   knowledge_refs          提案依赖的知识条目 ID
#   evidence_refs           提案引用的证据 ID（[ev:...]）
#   text                    提案文本（S1 关键词匹配用）
#   against_policy_item_ids 提案自报：与哪些策略条目冲突（辅助信息，不信任自报）
#   motivation / motivation_ref   违背倾向时的内部解释（须可追溯到 goal/事件/知识）
#   experience_refs         提案引用的亲身经历条目 ID
# --------------------------------------------------------------------------


def _proposal_get(proposal: dict, key: str) -> list[str]:
    value = proposal.get(key) or []
    if isinstance(value, str):
        return [value]
    return [str(x) for x in value if str(x or "").strip()]


def ooc_check_knowledge_boundary(proposal: dict, knowledge: list[dict],
                                 policy: BehaviorPolicy | None = None
                                 ) -> OOCReport:
    """OOC-K1 知识边界（结构化集合比对，非语义理解）。

    - knowledge_refs ⊆ 实例 ACTIVE 知识条目；
    - evidence_refs ⊆ 策略引用的证据集（档案侧）∪ 知识条目自带引用；
    - 提案未提供任何结构化引用 → 如实注明"无引用可查"，不冒充已检查。
    """
    report = OOCReport(verdict=OOC_OK)
    known_ids = {str(e.get("id")) for e in knowledge
                 if e.get("status") == "ACTIVE"}
    allowed_evidence = set(policy.all_evidence_refs()) if policy else set()
    bad_knowledge = [r for r in _proposal_get(proposal, "knowledge_refs")
                     if r not in known_ids]
    if bad_knowledge:
        report.add("OOC-K1", OOC_FLAGGED,
                   "提案依赖的知识不在该角色 ACTIVE 知识条目中（结构比对）",
                   bad_knowledge)
    bad_evidence = [r for r in _proposal_get(proposal, "evidence_refs")
                    if r not in allowed_evidence]
    if bad_evidence:
        report.add("OOC-K1", OOC_FLAGGED,
                   "提案引用的证据不在该角色的档案/知识允许集内",
                   bad_evidence)
    if (not _proposal_get(proposal, "knowledge_refs")
            and not _proposal_get(proposal, "evidence_refs")):
        report.add("OOC-K1", OOC_OK,
                   "提案无结构化引用，本检查不覆盖自由文本（语义级检查 NOT IMPLEMENTED）")
    return report


def ooc_check_secret_leak(proposal_text: str, knowledge: list[dict]) -> OOCReport:
    """OOC-S1 秘密疑似泄密（关键词启发式，**只报疑似**——可能被合理化：
    被偷听、对方口误等；处置权在叙事层，绝不因命中而 BLOCKED）。"""
    report = OOCReport(verdict=OOC_OK)
    text = str(proposal_text or "")
    if not text:
        return report
    for entry in knowledge:
        if entry.get("status") != "ACTIVE" or entry.get("category") != K_SECRET:
            continue
        needles = [str(entry.get("content") or "")]
        needles += [str(k) for k in (entry.get("keywords") or []) if str(k).strip()]
        for needle in needles:
            if needle and needle in text:
                report.add("OOC-S1", OOC_SUSPECTED_LEAK,
                           f"提案文本命中秘密条目 {entry.get('id')} 的关键词"
                           "（疑似泄密，需叙事层裁决是否合理化）",
                           [str(entry.get("id"))])
                break
    return report


def ooc_check_identity(proposal: dict, policy: BehaviorPolicy) -> OOCReport:
    """OOC-I1 身份事实（BLOCKED/TOLERATED 的唯一来源之一）。

    - `against_policy_item_ids` 是提案**自报**的冲突条目——辅助信息，
      不信任自报：指向不存在条目的引用按缺失处理并 FLAGGED；
    - 有效冲突 + 无 motivation → BLOCKED；
    - motivation 必须可追溯（motivation_ref ∈ 策略条目/提案知识引用）才 TOLERATED，
      光有文字没有出处仍然 BLOCKED（P8.1-A-R §4.1 边界）。
    """
    report = OOCReport(verdict=OOC_OK)
    declared = _proposal_get(proposal, "against_policy_item_ids")
    if not declared:
        return report
    policy_refs = {r.item_ref for r in policy.all_rules() if r.item_ref}
    valid = [r for r in declared if r in policy_refs]
    invalid = [r for r in declared if r not in policy_refs]
    if invalid:
        report.add("OOC-I1", OOC_FLAGGED,
                   "against_policy_item_ids 指向不存在的策略条目，按缺失处理"
                   "（自报内容不作豁免依据）", invalid)
    if not valid:
        return report
    motivation = str(proposal.get("motivation") or "").strip()
    motivation_ref = str(proposal.get("motivation_ref") or "").strip()
    if not motivation:
        report.add("OOC-I1", OOC_BLOCKED,
                   "提案与核心身份/性格条目冲突且未附任何解释",
                   valid)
        return report
    traceable = set(policy_refs) | set(_proposal_get(proposal, "knowledge_refs"))
    if not motivation_ref or motivation_ref not in traceable:
        report.add("OOC-I1", OOC_BLOCKED,
                   "解释缺少可追溯出处（motivation_ref 须指向存在的策略条目或知识条目）",
                   valid)
        return report
    report.add("OOC-I1", OOC_TOLERATED,
               "偏离已声明且动机可追溯——放行并记录为重要行为", valid)
    return report


def ooc_check_experiences(proposal: dict, experiences: list[dict]) -> OOCReport:
    """OOC-T1 经历时序：提案引用的亲身经历必须真实存在（查无 → BLOCKED——
    不能凭空想起没发生过的事）。"""
    report = OOCReport(verdict=OOC_OK)
    event_ids = {str(e.get("event_id")) for e in experiences}
    bad = [r for r in _proposal_get(proposal, "experience_refs")
           if r not in event_ids]
    if bad:
        report.add("OOC-T1", OOC_BLOCKED,
                   "提案引用的经历条目不存在（不能凭空想起没发生过的事）", bad)
    return report


def ooc_check(proposal: dict, knowledge: list[dict], experiences: list[dict],
              policy: BehaviorPolicy) -> OOCReport:
    """组合四条基础检查，整体结论取最高严重度。"""
    report = OOCReport(verdict=OOC_OK)
    for partial in (ooc_check_knowledge_boundary(proposal, knowledge, policy),
                    ooc_check_secret_leak(str(proposal.get("text") or ""), knowledge),
                    ooc_check_identity(proposal, policy),
                    ooc_check_experiences(proposal, experiences)):
        for finding in partial.findings:
            report.findings.append(finding)
            if OOC_SEVERITY.index(finding["verdict"]) < OOC_SEVERITY.index(report.verdict):
                report.verdict = finding["verdict"]
    return report


# --------------------------------------------------------------------------
# 序列化辅助（payload 只放可 JSON 化的 plain 数据，与 persona.py 同口径）
# --------------------------------------------------------------------------


def _goal_to_dict(g: Goal) -> dict:
    return {"goal_id": str(g.goal_id), "text": str(g.text),
            "priority": str(g.priority), "origin_ref": str(g.origin_ref),
            "status": str(g.status)}


def _emotion_to_dict(e: EmotionalState) -> dict:
    return {"mood": str(e.mood), "intensity": str(e.intensity),
            "since_ref": str(e.since_ref)}


def _belief_to_dict(b: Belief) -> dict:
    return {"belief_id": str(b.belief_id), "text": str(b.text),
            "about": str(b.about), "truth_status": str(b.truth_status),
            "formed_from": str(b.formed_from)}


def _event_to_dict(e: StoryEvent) -> dict:
    return {"event_id": str(e.event_id), "turn": int(e.turn),
            "text": str(e.text), "ref": str(e.ref)}


def _knowledge_to_dict(e: KnowledgeEntry) -> dict:
    return {"id": str(e.id), "content": str(e.content),
            "category": str(e.category), "about": str(e.about),
            "source_ref": str(e.source_ref), "teller_id": str(e.teller_id),
            "acquired_turn": int(e.acquired_turn),
            "acquired_at": str(e.acquired_at),
            "teller_credibility": str(e.teller_credibility),
            "content_credibility": str(e.content_credibility),
            "status": str(e.status), "superseded_by": str(e.superseded_by),
            "keywords": [str(k) for k in (e.keywords or [])]}


def _relation_to_dict(r: LatentRelation) -> dict:
    return {"target_id": str(r.target_id), "trust": str(r.trust),
            "stance": str(r.stance),
            "unresolved_conflicts": [str(x) for x in r.unresolved_conflicts],
            "origin_refs": [str(x) for x in r.origin_refs],
            "updated_at": str(r.updated_at or "")}


def _view_payload(view: CharacterInstanceView) -> dict:
    """从视图还原可修订的 payload 形状（revise 的 merge 基底）。"""
    return {
        "story_id": view.story_id,
        "character_id": view.character_id,
        "profile_version": view.profile_version,
        "display_name": view.display_name,
        "status": view.status,
        "current_goals": list(view.current_goals),
        "current_emotional_state": view.current_emotional_state,
        "current_beliefs": list(view.current_beliefs),
        "relationship_state": list(view.relationship_state),
        "personal_experiences": list(view.personal_experiences),
        "knowledge": list(view.knowledge),
        "behavior_overrides": list(view.behavior_overrides),
        "change_reason": view.change_reason,
    }


def _row_to_view(row: dict) -> CharacterInstanceView:
    payload = row["payload"]
    return CharacterInstanceView(
        id=str(row["id"]),
        story_id=str(payload.get("story_id", "")),
        character_id=str(payload.get("character_id", "")),
        profile_version=int(payload.get("profile_version", 0)),
        display_name=str(payload.get("display_name", "")),
        status=str(payload.get("status", "ACTIVE")),
        current_goals=list(payload.get("current_goals") or []),
        current_emotional_state=payload.get("current_emotional_state"),
        current_beliefs=list(payload.get("current_beliefs") or []),
        relationship_state=list(payload.get("relationship_state") or []),
        personal_experiences=list(payload.get("personal_experiences") or []),
        knowledge=list(payload.get("knowledge") or []),
        behavior_overrides=list(payload.get("behavior_overrides") or []),
        change_reason=str(payload.get("change_reason", "")),
        rev=int(row.get("rev", 1)),
        project_id=str(row.get("project_id", "")),
        created_at=str(row.get("created_at", "")),
        updated_at=str(row.get("updated_at", "")),
    )
