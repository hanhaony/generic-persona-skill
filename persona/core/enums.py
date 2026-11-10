"""核心枚举 —— 全项目唯一的"名词表"。

规则（来自 docs/DECISIONS.md）：
- 取值**必须**与 `docs/DATA_MODEL.md §5.1` 完全一致。任何地方都不许出现表外的字符串。
- 想改这里的取值，走 `DECISIONS.md §4` 的变更流程，不许在代码里悄悄加。

给初学者的解释：
    枚举就是"只允许填这几个词"的清单。比如"证据级别"只能填
    VERBATIM / PROFILE_BACKGROUND / THIRD_PARTY / INFERENCE 四个之一，
    填别的就会立刻报错，而不是等到后面才发现数据乱了。
"""

from __future__ import annotations

from enum import Enum


class _StrEnum(str, Enum):
    """字符串枚举：既能当枚举用，也能直接写进 JSON / SQLite。"""

    def __str__(self) -> str:  # 打印日志时显示取值本身，方便人读
        return str(self.value)


# --------------------------------------------------------------------------
# 1. 对象与命名空间
# --------------------------------------------------------------------------


class EntityType(_StrEnum):
    """被模拟对象的类型。真人与虚构角色**共用一套结构**，靠这个字段区分（D-047）。"""

    REAL_PERSON = "REAL_PERSON"              # 现实中的人（公开资料较丰富的公众人物）
    FICTIONAL_CHARACTER = "FICTIONAL_CHARACTER"  # 作品中的角色（游戏/动漫/电影/电视剧）


class ProvenanceNamespace(_StrEnum):
    """这条数据来自"哪个世界"（D-021）。**与"证据级别"是两个独立维度**。

    - `PUBLIC_SOURCE`  公开来源里的原始资料
    - `SYSTEM_DERIVED` 系统生成的档案与分析结论（含推断）
    - `USER_PROVIDED`  用户输入
    - `SIMULATED`      AI 模拟对话与模拟事件
    """

    PUBLIC_SOURCE = "PUBLIC_SOURCE"
    SYSTEM_DERIVED = "SYSTEM_DERIVED"
    USER_PROVIDED = "USER_PROVIDED"
    SIMULATED = "SIMULATED"


class EvidenceTier(_StrEnum):
    """证据级别（D-009）。**优先级不等于真伪**（D-010）。

    `INFERENCE`（系统推断）不参与前三者的排序，它必须单独标记。
    """

    VERBATIM = "VERBATIM"                        # 人物原话
    PROFILE_BACKGROUND = "PROFILE_BACKGROUND"    # 背景、档案与经历
    THIRD_PARTY = "THIRD_PARTY"                  # 他人提及与评价
    INFERENCE = "INFERENCE"                      # 系统推断（必须带 derived_from）


# --------------------------------------------------------------------------
# 2. 来源与事件
# --------------------------------------------------------------------------


class SourceKind(_StrEnum):
    """来源的种类。"""

    INTERVIEW = "INTERVIEW"
    NEWS_REPORT = "NEWS_REPORT"
    OFFICIAL_MATERIAL = "OFFICIAL_MATERIAL"
    WORK_CONTENT = "WORK_CONTENT"
    BIOGRAPHY = "BIOGRAPHY"
    SELF_PUBLISHED = "SELF_PUBLISHED"
    FAN_WIKI = "FAN_WIKI"
    COMMENTARY = "COMMENTARY"
    SOCIAL_POST = "SOCIAL_POST"
    OTHER = "OTHER"


class SourceRole(_StrEnum):
    """同一篇资料的不同版本（转载族谱，用于去重但不丢追溯）。"""

    ORIGINAL = "ORIGINAL"                # 原始发布
    REPRINT = "REPRINT"                  # 转载
    TRANSLATION = "TRANSLATION"          # 译本
    EXCERPT = "EXCERPT"                  # 节选
    SECONDARY_CITATION = "SECONDARY_CITATION"  # 二次引用


class SourceEventType(_StrEnum):
    """来源中可识别的原始事件（一次采访、一集剧情、一次官方设定发布……）。"""

    INTERVIEW = "INTERVIEW"
    EPISODE = "EPISODE"
    CHAPTER = "CHAPTER"
    SCENE = "SCENE"
    OFFICIAL_RELEASE = "OFFICIAL_RELEASE"
    PUBLIC_STATEMENT = "PUBLIC_STATEMENT"
    DOCUMENT_SECTION = "DOCUMENT_SECTION"
    OTHER = "OTHER"


class Reliability(_StrEnum):
    """来源可靠度（DATA_MODEL §4.3）。**是"可信度参考"，不等于真伪**（D-010）。"""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class FetchStatus(_StrEnum):
    """来源抓取状态（DATA_MODEL §4.3）。"""

    OK = "OK"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class EvidenceStatus(_StrEnum):
    """证据状态（DATA_MODEL §4.5）。**永不物理删除**，只改状态（D-025）。"""

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"   # 被更完整的证据取代
    DISPUTED = "DISPUTED"       # 卷入冲突（冲突本身不裁决，D-013）
    WITHDRAWN = "WITHDRAWN"     # 撤回（保留记录并说明原因）


class CanonLevel(_StrEnum):
    """虚构内容的"正式程度"：官方设定 / 非官方解读，不可混为一谈。"""

    CANON = "CANON"            # 官方设定或作品正式剧情
    NON_CANON = "NON_CANON"    # 同人、解读、推测
    AMBIGUOUS = "AMBIGUOUS"    # 有争议
    UNKNOWN = "UNKNOWN"        # 无法判断


# --------------------------------------------------------------------------
# 3. 证据、人物、记忆
# --------------------------------------------------------------------------


class EvidenceDimension(_StrEnum):
    """证据描述的是人物的哪个维度。"""

    BACKGROUND = "BACKGROUND"
    TRAIT = "TRAIT"
    HABIT = "HABIT"
    SPEECH_STYLE = "SPEECH_STYLE"
    EXPERIENCE = "EXPERIENCE"
    RELATION = "RELATION"
    CANON_FACT = "CANON_FACT"


class Stability(_StrEnum):
    """稳定性闸门（D-012）：**单次行为不得被认定为稳定性格**。"""

    SINGLE = "SINGLE"      # 只出现过一次
    REPEATED = "REPEATED"  # 重复出现
    PATTERN = "PATTERN"    # 形成模式
    UNKNOWN = "UNKNOWN"    # 无法判断


class DivergenceKind(_StrEnum):
    """冲突的类型。冲突**保留不裁决**（D-013、D-055）。"""

    FACTUAL = "FACTUAL"                  # 事实矛盾
    TIMELINE = "TIMELINE"                # 时间线不一致
    INTERPRETATION = "INTERPRETATION"    # 解读不同
    PERSPECTIVE = "PERSPECTIVE"          # 视角不同（双方说法都成立）
    VERSION_DIFFERENCE = "VERSION_DIFFERENCE"  # 版本差异（不同改编/时间线）


class ReviewStatus(_StrEnum):
    """冲突记录的人工审阅状态（DATA_MODEL §4.6）。

    ⚠️ 枚举里**故意没有 RESOLVED**（D-013）：一旦结构上允许"已解决"，
    未解决的冲突就会被下游当成确定事实。最彻底的防止办法是这个选项压根不存在。
    """

    UNREVIEWED = "UNREVIEWED"
    ACKNOWLEDGED = "ACKNOWLEDGED"


class Cardinality(_StrEnum):
    """关系档案的参与者规模（DATA_MODEL §4.8，P3.3 关系档案基础模块补入代码）。

    `PAIR` = 两人关系；`GROUP` = 三人及以上的群体关系。
    群体关系**存储为一份完整档案**（`participant_entity_ids` ≥ 3），
    不拆成互不相关的两人关系（用户目标 6 / D-017）。
    """

    PAIR = "PAIR"
    GROUP = "GROUP"


class ProfileStatus(_StrEnum):
    """人物档案状态（DATA_MODEL §4.7，P3.2 人格档案基础模块补入代码）。

    `INSUFFICIENT_EVIDENCE` **不是失败**（D-008）：资料不足时如实标记、
    继续流程，剩下的缺口进 `open_questions`（D-020）。
    """

    OK = "OK"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    AMBIGUOUS = "AMBIGUOUS"
    CONFLICTED = "CONFLICTED"


class MemorySourceType(_StrEnum):
    """记忆是从哪儿来的。"""

    SIMULATED_DIALOGUE = "SIMULATED_DIALOGUE"    # AI 模拟对话产生 → namespace 必须是 SIMULATED
    USER_INPUT = "USER_INPUT"
    SYSTEM_CONSOLIDATION = "SYSTEM_CONSOLIDATION"  # 系统整理的摘要
    PUBLIC_SOURCE_REFERENCE = "PUBLIC_SOURCE_REFERENCE"  # 对公开资料的引用


class MemoryKind(_StrEnum):
    EPISODIC = "EPISODIC"          # 事件记忆
    SEMANTIC = "SEMANTIC"           # 语义记忆
    RELATION_DELTA = "RELATION_DELTA"  # 关系变化
    SUMMARY = "SUMMARY"             # 摘要


class MemoryStatus(_StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"   # 被更新的记忆取代
    CONFLICTED = "CONFLICTED"   # 与其他记忆冲突
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"
    DELETED = "DELETED"         # 软删除（D-025：永不物理删除）


# --------------------------------------------------------------------------
# 3b. 对话会话（DATA_MODEL §4.9）
# --------------------------------------------------------------------------


class SessionStatus(_StrEnum):
    """对话会话状态（DATA_MODEL §4.9）。"""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ENDED = "ENDED"


class MemoryScope(_StrEnum):
    """记忆作用域（DATA_MODEL §4.9 / §4.10）。"""

    SESSION = "SESSION"
    ENTITY = "ENTITY"
    RELATIONSHIP = "RELATIONSHIP"
    PROJECT = "PROJECT"


class DerivedKind(_StrEnum):
    """派生产物种类（DATA_MODEL §4.11 / §5.1）。"""

    SUMMARY = "SUMMARY"    # 摘要压缩
    INDEX = "INDEX"        # 检索索引
    CACHE = "CACHE"        # 缓存


class DerivedStatus(_StrEnum):
    """派生产物生命周期（DATA_MODEL §4.11 / §5.1）。"""

    FRESH = "FRESH"                # 基于仍 ACTIVE 的记忆，可用
    STALE = "STALE"                # 来源部分过期，待重建
    INVALIDATED = "INVALIDATED"    # 来源已删/过期，必须失效


class PurgeState(_StrEnum):
    """记忆物理清理状态（DATA_MODEL §4.10 / §5.1，Q-21）。"""

    NONE = "NONE"
    PENDING_PURGE = "PENDING_PURGE"    # 软删除后进入 30 天宽限期
    PURGED = "PURGED"                  # 宽限期满，逻辑不可达（记录行保留，D-025）


# --------------------------------------------------------------------------
# 4. 任务
# --------------------------------------------------------------------------


class TaskType(_StrEnum):
    RESEARCH = "RESEARCH"
    EVIDENCE_EXTRACT = "EVIDENCE_EXTRACT"
    PERSONA_DISTILL = "PERSONA_DISTILL"
    RELATIONSHIP_DISTILL = "RELATIONSHIP_DISTILL"
    DIALOGUE_RUN = "DIALOGUE_RUN"
    MEMORY_CONSOLIDATION = "MEMORY_CONSOLIDATION"
    EVALUATION = "EVALUATION"


class TaskStatus(_StrEnum):
    """任务六态（D-052）。**暂停原因单独存 `PausedReason`，不新增状态名**。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class PausedReason(_StrEnum):
    """暂停的原因。`PAUSED` + 本字段，与 `CANCELLED` 是两回事（见 ERROR_HANDLING §6.3）。"""

    BUDGET = "BUDGET"          # 预算耗尽（D-043：不是失败）
    USER = "USER"              # 用户主动暂停
    DEPENDENCY = "DEPENDENCY"  # 依赖未就绪 / 进程被中断后回退


# --------------------------------------------------------------------------
# 5. 实体种类与命名空间写入闸门
# --------------------------------------------------------------------------

# 13 个业务实体（DATA_MODEL §3）+ 支撑实体，与 ID 前缀一一对应（见 core/ids.py）
#
# P8.1-B（2026-11-10）追加 `CharacterInstance`（第 14 个实体，DATA_MODEL §10 变更
# 历史同步登记）：**故事人物实例**——同一 PersonaProfile 在不同故事里的独立化身。
# 实例是故事层产物，namespace 恒为 SIMULATED（闸门见下）：档案（pnf_）是现实锚，
# 模拟永不写回；实例的状态（目标/情绪/信念/知识/关系看法）全部在 payload 内，
# 依托 records 表 kind+payload 文档式设计，零 Schema 改动。
ENTITY_KINDS: frozenset[str] = frozenset(
    {
        "Project",
        "PersonEntity",
        "Source",
        "SourceEvent",
        "EvidenceItem",
        "ConflictRecord",
        "PersonaProfile",
        "RelationshipProfile",
        "ConversationSession",
        "ConversationTurn",
        "MemoryItem",
        "TaskRecord",
        "DerivedArtifact",
        "CharacterInstance",
    }
)

# 命名空间写入闸门（对应 INTERFACES.md §1.8 / DATA_MODEL §5.4）
# 读法：往某个实体里写数据时，namespace 只能取集合里的值，其它一律拒绝。
NAMESPACE_WRITE_GATE: dict[str, frozenset[ProvenanceNamespace]] = {
    "Source": frozenset({ProvenanceNamespace.PUBLIC_SOURCE}),
    "SourceEvent": frozenset({ProvenanceNamespace.PUBLIC_SOURCE}),
    "EvidenceItem": frozenset(
        {
            ProvenanceNamespace.PUBLIC_SOURCE,
            ProvenanceNamespace.SYSTEM_DERIVED,   # 仅 INFERENCE 类证据
            ProvenanceNamespace.USER_PROVIDED,    # 仅经 UserInputRecord 登记
        }
    ),
    "PersonaProfile": frozenset({ProvenanceNamespace.SYSTEM_DERIVED}),
    "RelationshipProfile": frozenset({ProvenanceNamespace.SYSTEM_DERIVED}),
    "ConversationSession": frozenset({ProvenanceNamespace.SIMULATED}),
    "ConversationTurn": frozenset({ProvenanceNamespace.SIMULATED}),
    # 记忆四个命名空间都可能，但必须与 source_type 自洽（该自洽检查在 Memory 模块，P4 实现）
    "MemoryItem": frozenset(
        {
            ProvenanceNamespace.PUBLIC_SOURCE,
            ProvenanceNamespace.SYSTEM_DERIVED,
            ProvenanceNamespace.USER_PROVIDED,
            ProvenanceNamespace.SIMULATED,
        }
    ),
    # P8.1-B：故事人物实例是纯故事层产物——**只许 SIMULATED**。
    # 这与 EV-V1（SIMULATED 永不进证据）共同构成防污染双闸：档案/证据现实层
    # 与实例故事层在命名空间上彻底隔离。
    "CharacterInstance": frozenset({ProvenanceNamespace.SIMULATED}),
}


def namespaces_allowed_for(kind: str) -> frozenset[ProvenanceNamespace] | None:
    """返回该实体允许写入的命名空间集合；返回 `None` 表示这张表目前没有闸门。"""
    return NAMESPACE_WRITE_GATE.get(kind)


def namespace_allowed(kind: str, namespace: ProvenanceNamespace | str) -> bool:
    """判断 `namespace` 是否允许写入 `kind`。未知字符串一律不放行。"""
    allowed = namespaces_allowed_for(kind)
    if allowed is None:
        return True
    try:
        ns = ProvenanceNamespace(namespace)
    except ValueError:
        return False
    return ns in allowed


# 冲突记录的处理策略：枚举里**故意没有 RESOLVED**（D-013）
CONFLICT_RESOLUTION_POLICY = "KEEP_ALL_UNASSESSED"
