"""模型结构化输出的**严格校验**（P4.4-A 第三步）。

# 这个模块解决什么问题

抽取器（G-2）现在只把"要接真实模型需要什么"写清楚了，但**模型返回的文本**
本身还没有任何把关：

- `json.loads` 成功就当成功——字段缺失、类型写错、枚举瞎编都能一路流下去；
- 模型**编造的原文摘录**只要恰好不是空字符串，就会被当成"有原文支持"；
- 网页正文是**不可信输入**，而抽取器曾把材料原文直接拼进用户消息。

本模块把"模型输出"当成**不可信数据**处理：先解析、再逐字段机械校验、
最后把"能不能追溯回材料"这件事**用材料本身验证**，而不是相信模型的自我声明。

# 三条不可越过的线

1. **不修补、不猜测**：类型错了就是错，不给"字符串转数字"之类的自动修补。
   自动修补等于**用猜测伪造来源证据**（P4.4-A 第三步第 4 点）。
2. **不能核验就不放行**：声称的原文摘录必须在**送给模型的材料文本**里逐字找到
   （允许折空白差异）。找不到 → 拒收；找到了 → 由**框架**（不是模型）
   算出定位 `locator`。模型无法伪造一个它没引用的位置。
3. **不越权**：本模块**不写**任何东西、**不碰**存储、
   **不把候选标成"已验证证据"**。校验只输出"候选 + 拒收 + 待审核"三份清单，
   登记与否一律交给 `Evidence`（P4.4-A 第二步第 8 点）。

# 三种结论，三种去处（**互斥**：同一条候选只会出现在一个清单里）

| 结论 | 含义 | 去处 |
| --- | --- | --- |
| `accepted` | 结构合法 **且** 引用可核验 | 交给 `Evidence` 走既有校验/去重/登记 |
| `pending` | 结构合法但**核验不了**（无摘录、材料可疑、自报元数据对不上、推断无根） | **不进**候选清单，标记待审核（不伪装成有原文支持） |
| `rejected` | 硬性不合格（缺字段/类型错/枚举错/摘录找不到/引用伪造…） | 如实记录原因，不静默丢弃 |

# 能核验到什么程度（诚实边界）

本模块做的是**引用核验 + 结构核验**：摘录是否真的在该材料里、位置在哪、字段是否
合规、有没有编造来源与对象。**语义核验不做**——"这句话是否真的支持这条主张"
"主张里的日期说法是否与原文一致"属于语义判断，需要模型/人工，本轮不假装能做。
所以 `accepted` 只表示"结构合规且引用真实存在"，**不表示内容为真**（与
`Evidence` 的 `VALID` / `tier` 同一诚实口径：优先级不等于真伪）。

# 与 `tier` 的关系（三种 claim 的机械区分）

| 类型 | `tier` | 硬约束 |
| --- | --- | --- |
| 原文直接支持 | `VERBATIM` | `verbatim_quote` 必须能在材料里逐字找到，否则拒收 |
| 对材料的归纳 | `PROFILE_BACKGROUND` / `THIRD_PARTY` | 带摘录时同样要能核验（不得拿摘录冒充原话以外的用途） |
| 系统推断 | `INFERENCE` | 不得带摘录（无"原话"可言）；`derivation_keys` 必须指向本批 payload 内已接受的候选 |

# 提示注入

网页正文是**不可信输入**（`docs/P4.2` 合规钩子的既定口径）：材料里出现
"忽略以上指令""你现在是…"这类片段时，本模块**不会**把它当指令执行，
但会把它标成"可疑材料"，来自该材料的候选一律转 `pending` ——
因为"注入得逞后产出的 claim"恰恰是最危险的东西，宁可交人工。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

from .compliance import detect_prompt_injection
from .extract_quality import QUALITY_VALID

if TYPE_CHECKING:                        # 只为类型标注；运行期不引入循环依赖
    from .trait_vocabulary import TraitVocabulary

# --------------------------------------------------------------------------
# 常量：结论 / 拒收原因 / 待审核原因
# --------------------------------------------------------------------------

# —— 整批 payload 的结论 ——
VERDICT_OK = "OK"                                # 解析成功，逐条校验已完成
VERDICT_EMPTY_OUTPUT = "EMPTY_OUTPUT"            # 模型返回空字符串（官方文档承认会发生）
VERDICT_REFUSAL = "REFUSAL"                      # 模型明确拒答
VERDICT_INVALID_JSON = "INVALID_JSON"            # 不是合法 JSON
VERDICT_NOT_OBJECT = "NOT_OBJECT"                # 是 JSON，但顶层不是对象
VERDICT_CLAIMS_MISSING = "CLAIMS_MISSING"        # 顶层没有 claims 字段
VERDICT_CLAIMS_NOT_LIST = "CLAIMS_NOT_LIST"      # claims 不是数组
VERDICT_TOO_LARGE = "TOO_LARGE"                  # 输出超出长度上限（可能被截断）

# 这些结论属于"**模型输出本身不可用**"（调用方按系统级失败处理）
MALFORMED_VERDICTS: frozenset[str] = frozenset({
    VERDICT_INVALID_JSON,
    VERDICT_NOT_OBJECT,
    VERDICT_CLAIMS_MISSING,
    VERDICT_CLAIMS_NOT_LIST,
    VERDICT_TOO_LARGE,
})

# —— 逐条拒收原因（硬性不合格）——
REJECT_ITEM_NOT_OBJECT = "ITEM_NOT_OBJECT"
REJECT_MISSING_FIELD = "MISSING_FIELD"
REJECT_TYPE_ERROR = "TYPE_ERROR"
REJECT_EMPTY_CLAIM = "EMPTY_CLAIM"
REJECT_UNKNOWN_ENUM = "UNKNOWN_ENUM"
REJECT_UNKNOWN_MATERIAL = "UNKNOWN_MATERIAL"          # material_key 指不到本次材料
REJECT_UNKNOWN_SUBJECT = "UNKNOWN_SUBJECT"            # 主体 ID 不在允许范围内（防编造）
REJECT_EMPTY_SUBJECT = "EMPTY_SUBJECT"
REJECT_CONFIDENCE_OUT_OF_RANGE = "CONFIDENCE_OUT_OF_RANGE"
REJECT_QUOTE_NOT_IN_MATERIAL = "QUOTE_NOT_IN_MATERIAL"
REJECT_INFERENCE_WITH_QUOTE = "INFERENCE_WITH_QUOTE"
REJECT_INFERENCE_NO_DERIVATION = "INFERENCE_NO_DERIVATION"
REJECT_TOO_LONG = "TOO_LONG"
REJECT_TOO_MANY_CLAIMS = "TOO_MANY_CLAIMS"
# —— trait_tag（D-280 / D-283）——
#: 标签不在受控词表里。**不自造标签，也不把造出来的标签改写成词表里的某一个**：
#: 改写等于替模型做决定，而且会静默改变归并结果（D-280 §5）。
REJECT_TRAIT_TAG_NOT_IN_VOCABULARY = "TRAIT_TAG_NOT_IN_VOCABULARY"
#: 该维度已启用 trait_tag（词表里存在这个族的标签），但候选没给。
#: 方案 §3 要求"两边都硬"：可填可不填会让模型随机发挥，归并就废了。
REJECT_TRAIT_TAG_MISSING = "TRAIT_TAG_MISSING"
#: 未启用 trait_tag 的维度（BACKGROUND / EXPERIENCE / RELATION / CANON_FACT）
#: 填了标签。**必须拒收而不是"以标签为准"**：否则一句背景描述可以被贴个
#: `analytical` 直接送进 `traits`，那是绕过稳定性闸门（D-012 / PF-V2）。
REJECT_TRAIT_TAG_UNEXPECTED_DIMENSION = "TRAIT_TAG_UNEXPECTED_DIMENSION"

# —— 待审核原因（结构合法但核验不了）——
PENDING_EMPTY_QUOTE = "EMPTY_QUOTE"                    # 声称原话却没给摘录
PENDING_SOURCE_METADATA_MISMATCH = "SOURCE_METADATA_MISMATCH"  # 自报的来源元数据与材料不符
PENDING_UNTRUSTED_MATERIAL = "UNTRUSTED_MATERIAL"      # 材料被标记疑似提示注入
PENDING_INSTRUCTION_LIKE_CLAIM = "INSTRUCTION_LIKE_CLAIM"      # 主张本身像指令
PENDING_DERIVATION_UNRESOLVED = "DERIVATION_UNRESOLVED"        # 推断的根不在本批候选内

# —— 非致命观察（accepted 项上的一条备注，不影响放行）——
ISSUE_CONFIDENCE_UNSPECIFIED = "CONFIDENCE_UNSPECIFIED"
ISSUE_SUBJECT_ALLOWLIST_EMPTY = "SUBJECT_ALLOWLIST_EMPTY"
ISSUE_DATE_NOT_VERIFIED_DROPPED = "DATE_NOT_VERIFIED_DROPPED"  # 日期核验不过 → 弃用该日期
ISSUE_FORGED_METADATA_IGNORED = "FORGED_METADATA_IGNORED"      # 自报元数据已忽略
#: D-291：模型自报的 `material_key` 与"引文逐字出现在哪份材料"不一致，**已按引文改正**
ISSUE_MATERIAL_KEY_CORRECTED = "MATERIAL_KEY_CORRECTED"
#: D-291：引文在多份材料里都逐字出现，只能按第一份归属（如实记录，不假装确定）
ISSUE_MATERIAL_KEY_AMBIGUOUS = "MATERIAL_KEY_AMBIGUOUS"
#: 两个**已启用** trait_tag 的维度之间族不一致（如 `dimension=TRAIT` 却贴了
#: `music`）。按 D-283 **采纳标签族、保留原 dimension、如实记录**——不拒收：
#: R4.3 实测同一个事实在不同来源被抽成不同维度（小提琴在 baike 是 HABIT、
#: 在大英图书馆是 TRAIT），维度不可靠，以更具体的标签为准。
#: 非启用维度上的标签一律拒收（`REJECT_TRAIT_TAG_UNEXPECTED_DIMENSION`），
#: 那是另一回事：那是在允许绕过闸门。
ISSUE_TRAIT_TAG_DIMENSION_MISMATCH = "TRAIT_TAG_DIMENSION_MISMATCH"

# 模型**不得**自己填写来源元数据：这些键出现即记录，且与材料不符时转待审核
FORGED_METADATA_KEYS: tuple[str, ...] = (
    "source_id", "source_title", "url", "publisher", "author",
    "published_at", "source_kind", "reliability",
)

# 拒答启发式：**只在输出根本不是 JSON 时**使用（避免在合法 JSON 里误报）
_REFUSAL_MARKERS: tuple[str, ...] = (
    "i cannot", "i can't", "i'm sorry", "as an ai", "unable to",
    "无法", "不能提供", "拒绝", "抱歉", "对不起",
)


# --------------------------------------------------------------------------
# 默认上限（可注入，不写死在某一页上）
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationLimits:
    """长度与数量上限（P4.4-A 第三步第 1 点）。**全部可配**。

    `max_claims` 必须**不小于**提示词允许的最大条数
    （`材料数 × max_claims_per_material`，R4.4 是 9 × 4 = 36）：
    设小了会让遵守提示词的输出被判 `TOO_MANY_CLAIMS` 拒收，
    那等于用一个常量把另一条链路的成果作废。
    """

    max_claims: int = 60              # 单次返回最多处理多少条候选
    max_claim_chars: int = 600        # 单条主张的长度上限
    max_quote_chars: int = 1200       # 单条摘录的长度上限
    max_note_chars: int = 300         # 说明字段上限
    max_output_chars: int = 400_000   # 原始输出总量上限（超了按被截断处理）
    min_confidence: float = 0.0
    max_confidence: float = 1.0


# --------------------------------------------------------------------------
# 数据形状
# --------------------------------------------------------------------------


@dataclass
class MaterialText:
    """材料的**只读视图**：校验只需要这几个字段，不碰 `SourceMaterial` 本体。

    用视图而不是直接依赖 `core.types.SourceMaterial`，是为了让本模块的校验
    逻辑可以脱离"材料是怎么来的"独立测试（同一形状传 dict 也能跑）。
    """

    key: str = ""                     # 在 fixture / 提示词里的键（模型据此回指）
    source_id: str = ""               # 已登记来源 ID（由调用方绑定，模型无权修改）
    title: str = ""
    url: str = ""
    text: str = ""                    # **送给模型的正文/摘要**（核验只针对它）
    #: 门禁结论。`""` = 来源方没给；`SUMMARY_ONLY` = 明确声明"我是检索摘要"
    #: （P4.8 / D-6）。两者不是一回事：前者是信息缺失，后者是信息完整。
    quality_status: str = ""
    untrusted: bool = False           # 是否检测到疑似提示注入
    injection: list[str] = field(default_factory=list)


@dataclass
class ValidatedClaim:
    """**结构合法且可核验**的一条候选（仍未登记、仍不是证据）。"""

    key: str = ""
    material_key: str = ""
    source_id: str = ""
    claim: str = ""
    tier: str = ""
    dimension: str = ""
    subject_entity_ids: list[str] = field(default_factory=list)
    confidence: float = 0.0
    verbatim_quote: str | None = None
    locator: dict | None = None       # **框架**算出的位置，不是模型给的
    stated_at_raw: str = ""
    derivation_keys: list[str] = field(default_factory=list)
    note: str = ""
    issues: list[str] = field(default_factory=list)
    #: 受控特征标签（D-280）。**只能从词表里选**，`""` = 没有标签
    #: （该维度未启用 trait_tag，或模型明示 `null`）。
    #: 归并键与条目板块都由它决定，`dimension` 不参与（D-283）。
    trait_tag: str = ""


@dataclass
class PendingClaim:
    """结构合法但**核验不了**：不进候选清单，留待人工审核。"""

    reason: str
    message: str
    key: str = ""
    source_id: str = ""
    claim: str = ""
    detail: dict = field(default_factory=dict)


@dataclass
class RejectedItem:
    """硬性不合格的一条：**如实记录原因，不静默丢弃**。"""

    reason: str
    message: str
    key: str = ""
    source_id: str = ""
    claim: str = ""


@dataclass
class ClaimValidationResult:
    """一次严格校验的完整结果。"""

    verdict: str = VERDICT_OK
    accepted: list[ValidatedClaim] = field(default_factory=list)
    rejected: list[RejectedItem] = field(default_factory=list)
    pending: list[PendingClaim] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)   # 整批级问题（如拒答、超长）
    raw_chars: int = 0

    @property
    def ok(self) -> bool:
        return self.verdict == VERDICT_OK

    @property
    def malformed(self) -> bool:
        """模型输出本身不可用（调用方应报错，而不是当成"成功但零结果"）。"""
        return self.verdict in MALFORMED_VERDICTS

    def as_detail(self) -> dict:
        """诊断摘要（**不含材料全文**，可安全落审计）。"""
        return {
            "verdict": self.verdict,
            "raw_chars": self.raw_chars,
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "pending": len(self.pending),
            "issues": list(self.issues),
            "reject_reasons": sorted({r.reason for r in self.rejected}),
            "pending_reasons": sorted({p.reason for p in self.pending}),
        }


# --------------------------------------------------------------------------
# 文本核验
# --------------------------------------------------------------------------


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_ws(text: str) -> str:
    """折空白：**丢弃**所有空白字符（不是压成一个空格）。

    D-289（R4.6 实测）：早先实现是 `" ".join(text.split())`——把连续空白压成
    **一个空格**。对中文长材料（PDF 抽取、硬换行）来说这是个真 bug：换行落在
    句子中间时，材料折完多出一个空格、而模型的引文没有这个空格 ⇒ `find` 返回
    -1 ⇒ **把真实存在的引文判成"不在材料里"**。三轮实测 4/4 次
    `QUOTE_NOT_IN_MATERIAL` 全是这种误拒（详见 `docs/P4_8_R46_REAL_SEARCH.md` §5）。

    **为什么丢弃而不是"空格可选匹配"**：丢弃对定位更简单——`_norm_index_map`
    只为保留的字符记下标，折后串与原文一一对应，命中区间可直接还原；而"每个字符
    之间允许空白"需要正则回溯，长材料上更慢，还是同一件事。

    **为什么不损失严格性**：丢空白只放过"空白差异"，字符本身必须**按顺序、完整**
    出现——改写、删字、换序、跨句拼接仍然对不上（见
    `tests/test_p4_8_quote_whitespace.py` 的反向用例）。
    """
    return "".join(str(text or "").split())


def _norm_index_map(text: str) -> tuple[str, list[int]]:
    """折空白（丢空白）后的文本 + 每个折后字符对应的**原文本下标**。

    有了这张映射，"在折后文本里找到的位置"才能还原成"原文里的位置"，
    定位才不是假的：`start=index[found]`、`end=index[found+len-1]+1`。
    """
    chars: list[str] = []
    index: list[int] = []
    for position, char in enumerate(text):
        if char.isspace():
            continue
        chars.append(char)
        index.append(position)
    return "".join(chars), index



def find_quote_span(quote: str, text: str) -> tuple[int, int] | None:
    """在材料文本里定位摘录，返回 `[start, end)`；找不到返回 `None`。

    两轮匹配：**先逐字**（最严格），再**丢掉空白后逐字**（容忍换行/缩进/PDF 断行
    在句内插入的空白）。丢空白是 D-289 的修法：早先"压成一个空格"会让
    "材料有空格、引文没有"这种最常见的差异失配。
    两轮都不中 → 找不到，**不接受"意思差不多"**。
    """
    needle = str(quote or "")
    haystack = str(text or "")
    if not needle.strip() or not haystack:
        return None
    position = haystack.find(needle)
    if position >= 0:
        return position, position + len(needle)
    normalized_needle = _norm_ws(needle)
    if not normalized_needle:
        return None
    normalized_text, index = _norm_index_map(haystack)
    found = normalized_text.find(normalized_needle)
    if found < 0:
        return None
    start = index[found]
    end = index[found + len(normalized_needle) - 1] + 1
    return start, end


# --------------------------------------------------------------------------
# 材料视图
# --------------------------------------------------------------------------


def _get(source: Any, name: str, default: Any = "") -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def material_text_of(material: Any) -> str:
    """材料视图里的文本：**送给模型的那段**（`snippet`）。

    抽取器只把 `snippet` 交给模型，核验就必须针对 `snippet`——
    拿全文去核验摘录会得出"模型没引用过的东西也算核验通过"的假结论。
    """
    return _text(_get(material, "snippet"))


def build_material_views(materials: Sequence[Any]) -> list[MaterialText]:
    """把材料列表转成只读视图（含注入标记）。**纯函数**。"""
    views: list[MaterialText] = []
    for material in materials or []:
        text = material_text_of(material)
        injection = detect_prompt_injection(text)
        views.append(MaterialText(
            key=(_text(_get(material, "canonical_key")) or _text(_get(material, "url"))
                 or _text(_get(material, "title"))),
            source_id=_text(_get(material, "source_id")),
            title=_text(_get(material, "title")),
            url=_text(_get(material, "url")),
            text=text,
            quality_status=_text(_get(material, "quality_status")),
            untrusted=bool(injection),
            injection=injection,
        ))
    return views


def allowed_subject_ids(materials: Sequence[Any],
                        targets: Sequence[Any] | None) -> set[str]:
    """允许出现的主体 ID = 材料声明的对象 ∪ 调用方给的目标对象。

    这是**防编造引用**的机械手段：模型只能在给定范围内指认对象，
    凭空造一个 `pe_xxx` 会被拒收（而不是等到 `Evidence` 才发现"对象不存在"）。
    """
    allowed: set[str] = set()
    for material in materials or []:
        for entity_id in _get(material, "subject_entity_ids", []) or []:
            if _text(entity_id):
                allowed.add(_text(entity_id))
    for target in targets or []:
        entity_id = _text(_get(target, "entity_id")) or _text(
            (target or {}).get("entity_id") if isinstance(target, dict) else "")
        if entity_id:
            allowed.add(entity_id)
    return allowed


# --------------------------------------------------------------------------
# 逐条校验
# --------------------------------------------------------------------------


def _enum_values(enum_cls) -> tuple[str, ...]:
    return tuple(str(value) for value in enum_cls)


def _reject(result: ClaimValidationResult, item: Any, reason: str, message: str,
            key: str = "", source_id: str = "") -> None:
    claim = _text(item.get("claim")) if isinstance(item, dict) else ""
    result.rejected.append(RejectedItem(
        reason=reason, message=message, key=key, source_id=source_id, claim=claim))


def _pending(result: ClaimValidationResult, item: Any, reason: str, message: str,
             detail: dict | None = None) -> None:
    key = _text(item.get("key")) if isinstance(item, dict) else ""
    result.pending.append(PendingClaim(
        reason=reason, message=message, key=key,
        source_id=_text(item.get("source_id")) if isinstance(item, dict) else "",
        claim=_text(item.get("claim")) if isinstance(item, dict) else "",
        detail=dict(detail or {})))


def _number(value: Any) -> float | None:
    """严格数字：`bool` 不算数字，字符串**不转换**（不修补）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _str_list_strict(value: Any) -> list[str] | None:
    """严格字符串数组：不是列表、或列表里有非字符串 → `None`（不修补）。"""
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    items: list[str] = []
    for element in value:
        if not isinstance(element, str):
            return None
        if element.strip():
            items.append(element.strip())
    return items


def _validate_trait_tag(
    item: Any,
    index: int,
    *,
    key: str,
    dimension: str,
    vocabulary: "TraitVocabulary | None",
    issues: list[str],
    result: ClaimValidationResult,
) -> str | None:
    """校验 `trait_tag`。

    词表为空（`None` 或没有任何标签）⇒ **彻底的直通**：schema 里不会有
    这个字段、提示词不会提它、这里也就不管它。这是"不装词表就与
    trait_tag 上线之前完全一致"的第三道保证（前两道是 schema 与
    提示词，方案 §8 第 3 项）。

    返回：
        - `str`：通过。值为归一化后的标签（`""` = 这条没有标签）
        - `None`：**已拒收**，调用方必须立刻停止本条的后续校验

    与 `_validate_item` 其它检查同一纪律：**一条候选只记一个拒收原因**
    （先发现最根本的那个），不把同一批字段反复重报。
    """
    if vocabulary is None or not vocabulary:
        return ""
    raw = item.get("trait_tag")
    if raw is not None and not isinstance(raw, str):
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 trait_tag 必须是字符串"
                f"（收到 {type(raw).__name__}）：不做自动转换", key=key)
        return None
    tag = _text(raw)
    enabled = vocabulary.enabled_dimensions()
    if not tag:
        #: **显式 `null` 与"字段缺失"是两回事**（D-280 §5）：null 是模型
        #: 说"这条归不进任何标签"——那是合法的"我不知道"，该条回退按原文
        #: 归并（与 trait_tag 上线前行为一致）；而字段缺失是模型压根没考虑
        #: 这件事，正是"可填可不填会让模型随机发挥"的那个漏洞。
        explicit_null = ("trait_tag" in item and raw is None)
        if dimension in enabled and not explicit_null:
            _reject(result, item, REJECT_TRAIT_TAG_MISSING,
                    f"第 {index} 条候选的 dimension 是 {dimension}，本次已启用 "
                    f"trait_tag（词表有 {sorted(enabled)} 族的标签），但没有给 "
                    f"trait_tag。trait_tag 填 null 表示『这条确实贴不上任何标签』"
                    f"（回退按原文归并）是可以的，不放这个字段不行——"
                    f"可填可不填会让模型随机发挥，归并就废了", key=key)
            return None
        return ""
    spec = vocabulary.resolve(tag)
    if spec is None:
        _reject(result, item, REJECT_TRAIT_TAG_NOT_IN_VOCABULARY,
                f"第 {index} 条候选用了词表外的 trait_tag {tag!r}："
                f"本次只允许 {list(vocabulary.all_tags())}。"
                f"**不接受无法核验的标签，也不把它改写成词表里的某一个**——"
                f"改写等于替模型做决定，还会静默改变归并结果", key=key)
        return None
    if spec.family == dimension:
        return tag
    if dimension in enabled:
        # 两个启用维度之间的族冲突：采纳标签族，原样保留 dimension，如实记录。
        issues.append(ISSUE_TRAIT_TAG_DIMENSION_MISMATCH)
        result.issues.append(
            f"item#{index}: dimension={dimension} 与 trait_tag {tag!r} 的归属族 "
            f"{spec.family} 不一致，已按标签族归入 {spec.family}"
            f"（dimension 原样保留）")
        return tag
    _reject(result, item, REJECT_TRAIT_TAG_UNEXPECTED_DIMENSION,
            f"第 {index} 条候选的 dimension 是 {dimension}（未启用 trait_tag）"
            f"却给了 trait_tag={tag!r}（归属族 {spec.family}）。"
            f"不放行：否则一句背景描述贴个标签就能绕过稳定性闸门"
            f"（D-012 / PF-V2 只允许 REPEATED / PATTERN 进 traits）", key=key)
    return None


def _validate_item(
    item: Any,
    index: int,
    *,
    by_key: dict[str, MaterialText],
    allowed_subjects: set[str],
    subject_checkable: bool,
    limits: ValidationLimits,
    accepted_keys: set[str],
    tier_values: tuple[str, ...],
    dimension_values: tuple[str, ...],
    result: ClaimValidationResult,
    vocabulary: "TraitVocabulary | None" = None,
) -> None:
    """校验**一条**候选（顺序即文档里的检查顺序）。"""
    from ..core.enums import EvidenceTier

    if not isinstance(item, dict):
        _reject(result, {}, REJECT_ITEM_NOT_OBJECT,
                f"第 {index} 条候选不是对象（收到 {type(item).__name__}）："
                "输出格式不符合约定，不改写、不猜测")
        return

    key = _text(item.get("key")) or f"model#{index}"

    # —— 必填字段 ——
    for name in ("claim", "tier", "dimension", "subject_entity_ids", "material_key"):
        if name not in item:
            _reject(result, item, REJECT_MISSING_FIELD,
                    f"第 {index} 条候选缺少必填字段 {name!r}", key=key)
            return

    # —— claim ——
    claim = item.get("claim")
    if not isinstance(claim, str):
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 claim 不是字符串（收到 {type(claim).__name__}）",
                key=key)
        return
    claim = claim.strip()
    if not claim:
        _reject(result, item, REJECT_EMPTY_CLAIM, f"第 {index} 条候选的 claim 为空", key=key)
        return
    if len(claim) > limits.max_claim_chars:
        _reject(result, item, REJECT_TOO_LONG,
                f"第 {index} 条候选的 claim 长度 {len(claim)} 超过上限 "
                f"{limits.max_claim_chars}", key=key)
        return

    # —— tier / dimension：必须是既有的枚举值（不在表内的**不认**）——
    tier = item.get("tier")
    dimension = item.get("dimension")
    if not isinstance(tier, str) or not isinstance(dimension, str):
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 tier/dimension 必须是字符串", key=key)
        return
    if tier not in tier_values:
        _reject(result, item, REJECT_UNKNOWN_ENUM,
                f"第 {index} 条候选的 tier={tier!r} 不在既有定义内：{list(tier_values)}",
                key=key)
        return
    if dimension not in dimension_values:
        _reject(result, item, REJECT_UNKNOWN_ENUM,
                f"第 {index} 条候选的 dimension={dimension!r} 不在既有定义内："
                f"{list(dimension_values)}", key=key)
        return

    # —— trait_tag（D-280 / D-283）：已启用维度必填、未启用维度必须留空 ——
    #: 这一段就定了放行/拒收；`None` = 已拒收，本条的后续检查全部跳过。
    issues: list[str] = []
    trait_tag = _validate_trait_tag(
        item, index, key=key, dimension=dimension, vocabulary=vocabulary,
        issues=issues, result=result)
    if trait_tag is None:
        return

    # —— 材料溯源（**唯一**可信来源：本次输入的材料）——
    #: D-291：`material_key` 只是模型的**自报**，它会按"语义相似"就近归因
    #: （R4.7 实测：探针句逐字在中青报黛玉页，模型却把它填成百度百科宝玉页，
    #: 因为那页也讲"厌恶四书／禄蠹"）。所以这里只校验"这个 key 存在"，
    #: **真实归属由引文逐字出现的位置决定**（见下面"原文摘录"一段）——
    #: 引文是唯一可判定的证据，`material_key` 不是。
    material_key = item.get("material_key")
    if not isinstance(material_key, str) or not material_key.strip():
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 material_key 必须是字符串", key=key)
        return
    material = by_key.get(material_key.strip())
    if material is None:
        _reject(result, item, REJECT_UNKNOWN_MATERIAL,
                f"第 {index} 条候选声称来自材料 {material_key!r}，"
                f"但本次输入里没有这份材料：无法溯源，不登记", key=key)
        return

    # —— 主体 ——
    subjects = _str_list_strict(item.get("subject_entity_ids"))
    if subjects is None:
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 subject_entity_ids 必须是字符串数组", key=key)
        return
    if not subjects:
        _reject(result, item, REJECT_EMPTY_SUBJECT,
                f"第 {index} 条候选没有指明说的是谁", key=key)
        return
    if subject_checkable:
        unknown = [s for s in subjects if s not in allowed_subjects]
        if unknown:
            _reject(result, item, REJECT_UNKNOWN_SUBJECT,
                    f"第 {index} 条候选指出主体 {unknown}，"
                    f"它们既不在材料声明的对象里、也不在本次目标里：疑似编造引用",
                    key=key)
            return

    # —— confidence（缺省允许；给了就必须是 0~1 的数）——
    # `issues` 在这里声明是因为上面几段只 append 不改写；trait_tag 校验
    # 也可能往里加一条（族冲突），所以它必须比 trait_tag 那一段更早出现。
    confidence = 0.0
    if "confidence" in item and item.get("confidence") is not None:
        number = _number(item.get("confidence"))
        if number is None:
            _reject(result, item, REJECT_TYPE_ERROR,
                    f"第 {index} 条候选的 confidence 必须是数字"
                    f"（收到 {type(item.get('confidence')).__name__}）：不做自动转换",
                    key=key)
            return
        if not limits.min_confidence <= number <= limits.max_confidence:
            _reject(result, item, REJECT_CONFIDENCE_OUT_OF_RANGE,
                    f"第 {index} 条候选的 confidence={number} 不在 "
                    f"[{limits.min_confidence}, {limits.max_confidence}] 内", key=key)
            return
        confidence = number
    else:
        issues.append(ISSUE_CONFIDENCE_UNSPECIFIED)

    # —— 原文摘录 ——
    quote_raw = item.get("verbatim_quote")
    if quote_raw is not None and not isinstance(quote_raw, str):
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 verbatim_quote 必须是字符串", key=key)
        return
    quote = _text(quote_raw) or None
    locator: dict | None = None
    if quote is not None:
        if len(quote) > limits.max_quote_chars:
            _reject(result, item, REJECT_TOO_LONG,
                    f"第 {index} 条候选的摘录长度 {len(quote)} 超过上限 "
                    f"{limits.max_quote_chars}", key=key)
            return
        if tier == str(EvidenceTier.INFERENCE):
            _reject(result, item, REJECT_INFERENCE_WITH_QUOTE,
                    f"第 {index} 条候选是系统推断（INFERENCE）却带了原文摘录："
                    "推断没有『原话』，不得冒充原文", key=key)
            return
        span = find_quote_span(quote, material.text)
        if span is None:
            #: D-291 两段式：按**声明的**材料找不到，就在全部材料里按**引文逐字**
            #: 再找一遍。找到即改归属（引文在哪儿，来源就是哪儿）；一份都找不到
            #: 才拒收——"真伪造"仍然拦得住，只有"引文对、材料写错"被救回来。
            #:
            #: 为什么不按 `claim` 文本做模糊匹配来定位：claim 是**概括**
            #: （"她任情任性…"），同一说法在不同材料里的措辞不同，相似度匹配
            #: 会把 A 材料的话挂到 B——那正是 D-291 的成因本身。逐字引文
            #: 是唯一可判定、可复现的口径。
            relocated = [(view, found)
                         for view in by_key.values()
                         if view.key != material.key
                         for found in (find_quote_span(quote, view.text),)
                         if found is not None]
            if not relocated:
                _reject(result, item, REJECT_QUOTE_NOT_IN_MATERIAL,
                        f"第 {index} 条候选声称的原文摘录在该材料里找不到（逐字比对；"
                        "允许空白差异——换行/缩进/PDF 断行——但**不允许改写**）："
                        "不接受无法核验的引用，也不改写它", key=key)
                return
            material, span = relocated[0]
            if len(relocated) > 1:
                issues.append(ISSUE_MATERIAL_KEY_AMBIGUOUS)
                result.issues.append(
                    f"item#{index}: 模型自报 material_key={material.key!r} 里找不到引文，"
                    f"而这句话在 {len(relocated)} 份材料里都逐字出现，"
                    f"已按第一份归属（不确定性如实记录，不假装确定）")
            else:
                issues.append(ISSUE_MATERIAL_KEY_CORRECTED)
                result.issues.append(
                    f"item#{index}: 模型自报 material_key={material_key.strip()!r} 里"
                    f"找不到引文，但它在 {material.key!r} 里逐字出现 ⇒ "
                    f"按引文改正归属（D-291）")
        locator = {"field": "snippet", "start": span[0], "end": span[1]}

    # —— 来源由**最终确定的材料**决定（模型自报的 key 只作线索）——
    source_id = material.source_id

    # —— 自报来源元数据：模型**无权**生成 ——
    forged = [name for name in FORGED_METADATA_KEYS if name in item]
    mismatch: list[str] = []
    if "source_id" in item and _text(item.get("source_id")) != source_id:
        mismatch.append("source_id")
    if "url" in item and _text(item.get("url")) != material.url:
        mismatch.append("url")
    if "source_title" in item and _text(item.get("source_title")) != material.title:
        mismatch.append("source_title")

    # —— 推断的根 ——
    derivation = _str_list_strict(item.get("derivation_keys"))
    if derivation is None:
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 derivation_keys 必须是字符串数组", key=key)
        return
    if tier == str(EvidenceTier.INFERENCE) and not derivation:
        _reject(result, item, REJECT_INFERENCE_NO_DERIVATION,
                f"第 {index} 条候选是系统推断却没给 derivation_keys："
                "禁止无根推断（EV-V3）", key=key)
        return

    # —— 说明字段 ——
    note = item.get("note")
    if note is not None and not isinstance(note, str):
        _reject(result, item, REJECT_TYPE_ERROR,
                f"第 {index} 条候选的 note 必须是字符串", key=key)
        return
    note = _text(note)
    if len(note) > limits.max_note_chars:
        _reject(result, item, REJECT_TOO_LONG,
                f"第 {index} 条候选的 note 长度超过上限 {limits.max_note_chars}", key=key)
        return

    # —— 时间：必须是材料里能看到的那个字符串（不得编造日期）——
    stated_at_raw = ""
    dropped_date = ""
    for field_name in ("stated_at", "stated_at_raw", "date"):
        value = item.get(field_name)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            _reject(result, item, REJECT_TYPE_ERROR,
                    f"第 {index} 条候选的 {field_name} 必须是字符串", key=key)
            return
        value = value.strip()
        if value and value not in material.text:
            dropped_date = value          # 材料里找不到 → 弃用（不猜、不换算）
        else:
            stated_at_raw = value
        break

    # —— 待审核判定：**只要有一条成立，就不进候选清单** ——
    # 顺序即优先级（先记最根本的原因）。
    pending_reasons: list[tuple[str, str, dict]] = []
    if material.untrusted:
        pending_reasons.append((
            PENDING_UNTRUSTED_MATERIAL,
            f"第 {index} 条候选来自被标记疑似提示注入的材料"
            f"（命中 {len(material.injection)} 处模式）：注入得逞的产物最危险，交人工",
            {"injection": list(material.injection)}))
    if detect_prompt_injection(claim):
        pending_reasons.append((
            PENDING_INSTRUCTION_LIKE_CLAIM,
            f"第 {index} 条候选的**主张文本**本身像一条指令：疑似提示注入的产物，交人工",
            {}))
    if mismatch:
        pending_reasons.append((
            PENDING_SOURCE_METADATA_MISMATCH,
            f"第 {index} 条候选自报的来源元数据 {mismatch} 与实际材料不符："
            "以其真实来源为准，并标记待审核",
            {"mismatched": mismatch, "forged_keys": forged}))
    if tier == str(EvidenceTier.VERBATIM) and quote is None:
        pending_reasons.append((
            PENDING_EMPTY_QUOTE,
            f"第 {index} 条候选标为 VERBATIM 却没给原文摘录：无法核验，交人工"
            "（不降级、不补写摘录）",
            {}))

    if pending_reasons:
        for reason, message, detail in pending_reasons:
            _pending(result, item, reason, message, detail)
        return

    # —— 入库 ——
    if dropped_date:
        issues.append(ISSUE_DATE_NOT_VERIFIED_DROPPED)
        result.issues.append(
            f"item#{index}: 日期 {dropped_date!r} 在该材料里找不到，已弃用（未采用、未换算）")
    if forged:
        issues.append(ISSUE_FORGED_METADATA_IGNORED)

    accepted_keys.add(key)
    result.accepted.append(ValidatedClaim(
        key=key,
        material_key=material.key,
        source_id=source_id,
        claim=claim,
        tier=tier,
        dimension=dimension,
        subject_entity_ids=subjects,
        confidence=confidence,
        verbatim_quote=quote,
        locator=locator,
        stated_at_raw=stated_at_raw,
        derivation_keys=derivation,
        note=note,
        issues=issues,
        trait_tag=trait_tag,
    ))


def _resolve_derivations(result: ClaimValidationResult) -> None:
    """推断的根必须落在**本批已接受的候选**里（跨批引用由下游解析，这里不猜）。

    根解析不出来 → 该推断**移出候选清单**转待审核：宁可少登记一条，
    也不登记一条"看着有根、其实指向空气"的推断（EV-V3 禁止无根推断）。
    """
    from ..core.enums import EvidenceTier

    accepted_keys = {c.key for c in result.accepted}
    for claim in list(result.accepted):
        if claim.tier != str(EvidenceTier.INFERENCE):
            continue
        missing = [k for k in claim.derivation_keys if k not in accepted_keys]
        if not missing:
            continue
        result.accepted.remove(claim)
        result.pending.append(PendingClaim(
            reason=PENDING_DERIVATION_UNRESOLVED,
            message=f"推断「{claim.claim[:40]}」的 derivation_keys {missing} "
                    "在本批候选内找不到：不登记（禁止编造根）",
            key=claim.key, source_id=claim.source_id, claim=claim.claim,
            detail={"missing": missing}))


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------


def validate_model_payload(
    raw_text: str,
    *,
    materials: Sequence[Any],
    targets: Sequence[Any] | None = None,
    limits: ValidationLimits | None = None,
    finish_reason: str = "",
    vocabulary: "TraitVocabulary | None" = None,
) -> ClaimValidationResult:
    """**严格校验**模型返回的文本。纯函数：不联网、不写库、不调模型。

    返回的 `accepted` 仍然只是**候选**——登记与否一律由 `Evidence` 决定。

    `vocabulary`（D-280）：受控特征标签词表。为 `None` 或空 ⇒ 不看 `trait_tag`，
    行为与本字段上线之前完全一致。
    """
    from ..core.enums import EvidenceDimension, EvidenceTier

    limits = limits or ValidationLimits()
    raw = str(raw_text or "")
    result = ClaimValidationResult(raw_chars=len(raw))

    if not raw.strip():
        result.verdict = VERDICT_EMPTY_OUTPUT
        result.issues.append("模型返回空输出（官方文档承认 JSON Output 偶发返回空内容）")
        return result

    if len(raw) > limits.max_output_chars:
        result.verdict = VERDICT_TOO_LARGE
        result.issues.append(f"输出长度 {len(raw)} 超过上限 {limits.max_output_chars}，"
                             "疑似被截断：整批不受理")
        return result

    if str(finish_reason or "").strip().lower() in ("refusal", "content_filter"):
        result.verdict = VERDICT_REFUSAL
        result.issues.append(f"模型 finish_reason={finish_reason}（拒答/内容过滤）")
        return result

    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        # 不是 JSON：再判一次"是不是干脆拒答了"（保守启发式，只在此时启用）
        lowered = raw.lower()
        if "{" not in raw and any(m in lowered for m in _REFUSAL_MARKERS):
            result.verdict = VERDICT_REFUSAL
            result.issues.append("输出不是 JSON，且包含典型的拒答措辞：按拒答处理")
            return result
        result.verdict = VERDICT_INVALID_JSON
        result.issues.append("输出不是合法 JSON：不把它当成『抽取成功但零结果』")
        return result

    if not isinstance(payload, dict):
        result.verdict = VERDICT_NOT_OBJECT
        result.issues.append(f"顶层是 {type(payload).__name__}，约定要求对象")
        return result

    if "refusal" in payload and payload.get("refusal"):
        result.verdict = VERDICT_REFUSAL
        result.issues.append(f"模型明确拒答：{_text(payload.get('refusal'))[:120]}")
        return result

    if "claims" not in payload:
        result.verdict = VERDICT_CLAIMS_MISSING
        result.issues.append("顶层缺少 claims 字段：结构不符合约定")
        return result

    items = payload.get("claims")
    if not isinstance(items, list):
        result.verdict = VERDICT_CLAIMS_NOT_LIST
        result.issues.append("claims 不是数组：结构不符合约定")
        return result

    views = build_material_views(materials)
    by_key = {v.key: v for v in views if v.key}
    allowed = allowed_subject_ids(materials, targets)
    tier_values = _enum_values(EvidenceTier)
    dimension_values = _enum_values(EvidenceDimension)

    processed = items[: max(0, limits.max_claims)]
    accepted_keys: set[str] = set()
    for index, item in enumerate(processed):
        _validate_item(
            item, index, by_key=by_key, allowed_subjects=allowed,
            subject_checkable=bool(allowed), limits=limits,
            accepted_keys=accepted_keys, tier_values=tier_values,
            dimension_values=dimension_values, result=result,
            vocabulary=vocabulary)

    if not allowed:
        result.issues.append(ISSUE_SUBJECT_ALLOWLIST_EMPTY)

    for index in range(len(processed), len(items)):
        _reject(result, items[index], REJECT_TOO_MANY_CLAIMS,
                f"第 {index} 条候选超出单次上限 {limits.max_claims}："
                "本批不处理（不静默丢弃，也不在下一批偷偷补）")

    _resolve_derivations(result)
    result.verdict = VERDICT_OK
    return result
