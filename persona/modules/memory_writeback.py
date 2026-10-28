"""对话 → 模拟记忆候选的**确定性规则引擎**（P6.2-a，`docs/P6_2_MEMORY_WRITE_PLAN.md`）。

**它是什么**：把一条**用户消息**按确定性规则派生成 0–2 条会话级记忆候选，
字段形状对齐 `DialogueService.commit_turn(..., memory_candidates=)` 的现有契约。
**纯函数、零 I/O、零模型调用、零第三方依赖**——不碰存储、不碰账本、不碰 Provider。

**三条不可让步的设计原则**（改这个文件前先读）：

1. **只收用户消息，物理上拿不到模型回答。**
   模型输出是"扮演"，不是事实。一旦把模型的话写进记忆、下一轮又当上下文注入，
   就会形成"它编 → 它记 → 它更确信"的回声室。
   所以本模块所有对外函数的签名里**没有** answer / response / reply / completion
   这类参数；`tests/test_memory_writeback.py` 用 `inspect.signature` 钉死这一点
   （防将来有人"顺手把 answer 传进来"——那是回声室的唯一入口）。

2. **记忆内容必须是用户原文的切片（verbatim），绝不改写。**
   改写等于引入第二个生成器，幻觉会从这条路溜回来。
   不变量：任一候选的 `content` 都是 `user_message` 的子串，由 fuzz 测试保证。

3. **默认不写。** 本模块只负责"派生出候选"，**不负责写**。
   真正落盘必须由会话策略显式授权（`none` / `explicit`，`rule` 为 P6.2-b 新增）。
   既有守卫 `tests/test_dialogue_memory_integration.py::test_full_automatic_write_never_happens`
   必须继续通过——本模块的存在不改变默认路径的零写入行为。

**规则总表**（顺序不可交换：先七条否决，后四条肯定）

| 代码 | 条件 | kind |
|---|---|---|
| R-EXPL | 显式指令：记住/记着/别忘了/你记得；自述称呼：叫我X/称我X/喊我X（过 `_self_name_ok` 形状校验） | SEMANTIC（salience 高） |
| R-REL | 态度变化 + 参与者指称：我现在觉得/我改主意了/我不再/我从前以为 | RELATION_DELTA |
| R-EPI | 第一人称 + 时间/动作：昨天/今天/那天/后来/我去了/我读了/我梦见 | EPISODIC |
| R-SEM | 第一人称 + 稳定语气：我一向/我素来/我最喜欢/我最讨厌/我从不 | SEMANTIC |

| 代码 | 否决条件 |
|---|---|
| N1 | 问句（句末 ？/?，或句首疑问词且无陈述） |
| N2 | 寒暄/应答，或整条消息 < 6 字 |
| N3 | 回指模型上轮的话（你刚说/你上面/你说过）——**防幻觉洗白** |
| N4 | 与档案文本 4-gram 重叠 ≥ 0.6（档案已有的不再记一遍） |
| N5 | 敏感标识（手机号/身份证/邮箱/银行卡）——**丢弃，且报告里不落内容** |
| N6 | 整条消息 > 200 字（不截断：截断会改语义） |
| N7 | 纯元话语（我们开始吧/继续/接着来） |

**两个实现细节**（都容易踩）：

- `memory.py` 对 `confidence` / `salience` **只做 `float()`、不校验区间**
  （`_build_payload`），所以 clamp 必须在这里做；
- 句长超过 120 字的句子**直接丢**（不做前缀截断）——截断后的"记忆"语义会变，
  而记忆是要被下一轮当上下文注入的，宁可少记一条。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

#: 规则版本。写进 `confidence_means`，将来要回溯"这条记忆是哪版规则派生的"靠它。
#: v2（2026-10-28）：R-EXPL 追加自述称呼 cue「叫我/称我/喊我」，命中后过
#: `_self_name_ok` 形状校验（第三者转述 / 祈使短语不放行）。
RULE_VERSION = "memory-rule-v2"

#: 单轮最多派生几条候选（防一句话命中三个 cue 就写三条）。
MAX_CANDIDATES_PER_TURN = 2

#: 候选内容上限（字）。超过的句子直接丢，不做截断。
MAX_CONTENT_CHARS = 120

#: 整条消息上限（字）。超过整条否决（N6）。
MAX_MESSAGE_CHARS = 200

#: 规则派生的固定置信度。**不是**"我很确定"，是"用户只说了一次、没人核实"。
RULE_CONFIDENCE = 0.4

#: 基础显著性；命中时间 cue / 情感词各 +0.2，显式指令 +0.4，最后 clamp 到 [0,1]。
BASE_SALIENCE = 0.3
EXPLICIT_SALIENCE_BONUS = 0.4
TIME_SALIENCE_BONUS = 0.2
EMOTION_SALIENCE_BONUS = 0.2

#: 档案重叠阈值（4-gram 召回率）。≥ 此值即认为"档案里已经有了"。
PROFILE_OVERLAP_THRESHOLD = 0.6
PROFILE_NGRAM = 4

# --------------------------------------------------------------------------
# 词表（**全部显式列举**，不靠自然语言模糊判断）
# --------------------------------------------------------------------------

#: 句级切分符。切分保留原文切片，不做任何改写。
_SENTENCE_BREAKS = "。！？；!?;\n"

#: 句末为这些字符 → 问句（N1）。
_QUESTION_ENDINGS = "？?"

#: 句首疑问词（无问号也算问句，N1）。
_INTERROGATIVE_HEADS: tuple[str, ...] = (
    "吗", "呢", "什么", "怎么", "为何", "为什么", "难道", "是否", "几时", "何处",
)

#: 寒暄/应答（N2）。整条消息去掉标点后**等于**其中之一才否决。
GREETINGS: frozenset[str] = frozenset({
    "你好", "您好", "谢谢", "多谢", "好的", "好", "嗯", "嗯嗯", "哦", "噢",
    "哈哈", "哈哈哈", "在吗", "再见", "拜拜", "辛苦了", "收到", "明白", "知道了",
})

#: 元话语（N7）。整句去标点后等于其中之一，或句中出现即否决该句。
META_TALK: tuple[str, ...] = (
    "我们开始吧", "开始吧", "继续", "接着来", "接着说", "你继续", "说下去", "走吧",
)

#: 回指模型上轮的话（N3）。**出现即否决该句**，宁可少记防洗白。
BACKREF_CUES: tuple[str, ...] = (
    "你刚说", "你刚才", "你上面", "你说过", "你方才", "你先前说", "你前面说",
    "你上一条", "你刚才讲",
)

#: 时间 cue（用于 R-EPI 触发 + salience 加成）。
#: `近来`/`这两天` 这类模糊时段在真实对话里比"昨天"更常见（离线探针 12 句里就有 3 句
#: 是这种说法），漏了会让召回低到不可用——它们与"昨天"同属时间 cue，不是新规则类。
TIME_CUES: tuple[str, ...] = (
    "昨天", "前天", "今日", "今天", "那天", "那次", "上回", "上次", "后来",
    "今早", "昨晚", "方才", "刚才",
    "近来", "这两天", "这几日", "这阵子", "那日", "当日", "前些日子", "前几日",
)

#: 动作 cue（R-EPI 用，本身已含第一人称）。
#: `我在读`/`我在看` 覆盖"持续进行"的叙事（"我近来在读《西厢记》"这类），
#: 与"我读了"是同一语义类别（用户自己做过的事）。
ACTION_CUES: tuple[str, ...] = (
    "我去了", "我读了", "我梦见", "我见到", "我遇见", "我写了", "我看了",
    "我走过", "我听了", "我买了", "我回了", "我到了",
    "我在读", "我在看", "我去过", "我回过",
)

#: 偏好/稳定语气 cue（R-SEM）。
SEMANTIC_CUES: tuple[str, ...] = (
    "我一向", "我素来", "我最喜欢", "我最讨厌", "我最爱", "我从不", "我惯常",
    "我向来", "我平生", "我素不",
)

#: 态度变化 cue（R-REL）。
RELATION_CUES: tuple[str, ...] = (
    "我现在觉得", "我改主意了", "我不再", "我从前以为", "我如今觉得",
    "我现在倒觉得", "我改主意", "我改变了看法",
)

#: 自述称呼 cue（R-EXPL 追加，2026-10-28）：「叫我小明」族。
#: 单列一份是为了 `_matches` 里能区分"传统指令命中"与"self-name 命中"——
#: 后者必须再过 `_self_name_ok` 形状校验，前者照旧放行。
SELF_NAME_CUES: tuple[str, ...] = ("叫我", "称我", "喊我")

#: 显式指令 cue（R-EXPL）。
EXPLICIT_CUES: tuple[str, ...] = (
    "记住", "记着", "别忘了", "你记得", "要记得", "别忘记",
) + SELF_NAME_CUES

#: 自述称呼的正则：cue 后紧跟 2-10 字称呼（中文/字母/数字，不含标点空白）。
#: 不足 2 字（"叫我啊"）或超长都不是称呼。
SELF_NAME_RE: re.Pattern[str] = re.compile(r"(?:叫|称|喊)我([\w一-龥]{2,10})")

#: 第三者转述主语（出现即认定不是用户在自我介绍）。
#: 只收**明确第三者**；"大家叫我小明"仍是用户在下指令，不拦。
SELF_NAME_THIRD_SUBJECTS: tuple[str, ...] = (
    "他们", "她们", "它们", "别人", "人家",
)

#: 称呼段首字若是这些祈使/否定字，视为动词短语而非称呼
#: （"叫我别去""叫我走吧"）。真名首字撞上它们的概率可忽略。
SELF_NAME_IMPERATIVE_HEADS: frozenset[str] = frozenset(
    "别不莫勿少快去来做说想看听走站坐住等跟按照帮替管理放拿打骂催让使逼"
)


def _self_name_ok(sentence: str) -> bool:
    """「叫我X」族命中后的形状校验：只放行**第一人称自述称呼**。

    三道检查（全部确定性、无自然语言判断）：
    1. 句中不含第三者转述主语（"他们叫我别去"）；
    2. cue 后紧跟的 2-10 字段真实存在（正则形状）；
    3. 该字段首字不是祈使/否定字（"别去"的"别"）。
    """
    if any(s in sentence for s in SELF_NAME_THIRD_SUBJECTS):
        return False
    m = SELF_NAME_RE.search(sentence)
    if m is None:
        return False
    return m.group(1)[0] not in SELF_NAME_IMPERATIVE_HEADS

#: 第一人称（R-EPI / R-SEM / R-REL 必须有）。
FIRST_PERSON: tuple[str, ...] = ("我", "俺", "吾", "咱")

#: 第二人称（R-REL 的"参与者指称"可用它——一对一对话里「你」就是对方）。
#: 若不认第二人称，"我现在觉得你比从前可亲"这类最典型的关系变化会被误否决。
SECOND_PERSON: tuple[str, ...] = ("你", "您")

#: 情感词（只用于 salience 加成，不参与触发判断）。
EMOTION_CUES: tuple[str, ...] = (
    "高兴", "开心", "欢喜", "难过", "伤心", "害怕", "担心", "气", "恨",
    "想念", "惦记", "欢喜", "痛快", "失落", "委屈",
)

#: 敏感标识（N5）。命中即整条否决，**报告里只记代码、不落内容**。
SENSITIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PHONE", re.compile(r"1[3-9]\d{9}")),
    ("ID_CARD", re.compile(r"\d{17}[\dXx]")),
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("BANK_CARD", re.compile(r"\d{16,19}")),
)

#: N5 是否有命中（供测试与调用方统一判断，避免重复实现）。
SENSITIVE_CODES: tuple[str, ...] = tuple(code for code, _ in SENSITIVE_PATTERNS)

#: 去标点用的字符集（做 4-gram 重叠时用；不影响 verbatim 切片）。
_PUNCT = set(
    "，。！？；：、（）「」『』《》〈〉“”‘’,.!?;:()[]{}<>\"'`~ \t\r\n　—…·-"
)


# --------------------------------------------------------------------------
# 报告结构
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Rejection:
    """一条否决记录。**只记代码与位置，不记内容**（N5 尤其不能落内容）。"""

    code: str
    #: 句子序号；-1 表示整条消息级别（N2/N5/N6）
    sentence_index: int
    #: 命中的规则代码（仅有规则命中却因其它原因被否决时才有值）
    rule: str | None = None


@dataclass(frozen=True)
class DeriveReport:
    """派生结果 + 否决轨迹。候选是 dict（对齐 `commit_turn` 契约），故用 tuple 承载。"""

    candidates: tuple[dict, ...]
    rejections: tuple[Rejection, ...]
    rule_version: str = RULE_VERSION

    @property
    def rejected_codes(self) -> list[str]:
        return [r.code for r in self.rejections]


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Rule:
    """一条肯定规则。`cues` 全部显式列举，便于审计与调参。"""

    code: str
    kind: str
    cues: tuple[str, ...]
    #: 是否要求句子中出现会话参与者（`R-REL` 用）
    requires_participant: bool = False
    #: 是否要求第一人称（`R-EXPL` 不要求：用户可能在向对方下指令）
    requires_first_person: bool = True
    #: 是否给显著性加成
    explicit: bool = False


_RULES: tuple[_Rule, ...] = (
    _Rule("R-EXPL", "SEMANTIC", EXPLICIT_CUES,
          requires_first_person=False, explicit=True),
    _Rule("R-REL", "RELATION_DELTA", RELATION_CUES, requires_participant=True),
    _Rule("R-EPI", "EPISODIC", TIME_CUES + ACTION_CUES),
    _Rule("R-SEM", "SEMANTIC", SEMANTIC_CUES),
)


def _sentences(text: str) -> list[tuple[int, str, str]]:
    """切成 `(起始下标, 去首尾空白的原文切片, 句末标点)`。

    `起始下标` 与切片都来自原文，因此切片天然是 `text` 的子串——verbatim 不变量的
    基础。**不做任何改写**（不改标点、不合并短句、不截断）。
    """
    out: list[tuple[int, str, str]] = []
    start = 0
    for i, ch in enumerate(text):
        if ch in _SENTENCE_BREAKS:
            out.append((start, text[start:i], ch))
            start = i + 1
    if start < len(text):
        out.append((start, text[start:], ""))
    return [(i, s.strip(), d) for i, s, d in out if s.strip()]


def _strip_punct(text: str) -> str:
    return "".join(ch for ch in str(text or "") if ch not in _PUNCT)


def _grams(text: str, n: int = PROFILE_NGRAM) -> set[str]:
    t = _strip_punct(text)
    if len(t) < n:
        return set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _profile_overlap(content: str, profile_texts: Sequence[str]) -> float:
    """候选内容被档案 4-gram 覆盖的比例（召回率，分母是候选自身的 gram 数）。

    用召回率而不是 Jaccard：档案通常很长，Jaccard 会被分母稀释到永远低于阈值，
    那样这条否决形同虚设。
    """
    cg = _grams(content)
    if not cg:
        return 0.0
    best = 0.0
    for p in profile_texts or ():
        pg = _grams(p)
        if not pg:
            continue
        best = max(best, len(cg & pg) / len(cg))
    return best


def _sensitive_codes(text: str) -> list[str]:
    return [code for code, pat in SENSITIVE_PATTERNS if pat.search(str(text or ""))]


def _is_question(sentence: str, ending: str) -> bool:
    if ending and ending in _QUESTION_ENDINGS:
        return True
    stripped = _strip_punct(sentence)
    return any(stripped.startswith(h) for h in _INTERROGATIVE_HEADS)


def _is_greeting_or_too_short(message: str) -> bool:
    if len(str(message or "").strip()) < 6:
        return True
    return _strip_punct(message) in GREETINGS


def _is_meta(sentence: str) -> bool:
    s = _strip_punct(sentence)
    if s in {_strip_punct(m) for m in META_TALK}:
        return True
    return any(cue in sentence for cue in META_TALK)


def _matches(rule: _Rule, sentence: str, participants: Sequence[tuple[str, str]]) -> bool:
    if rule.requires_first_person and not any(fp in sentence for fp in FIRST_PERSON):
        return False
    if rule.requires_participant:
        names = [str(n) for _, n in (participants or ()) if str(n or "").strip()]
        if not any(n in sentence for n in names) and not any(
            p in sentence for p in SECOND_PERSON
        ):
            return False
    hit = any(cue in sentence for cue in rule.cues)
    if not hit:
        return False
    if rule.code == "R-EXPL":
        # 「叫我X」族必须过形状校验（防"他们叫我别去"混入）；但只要句中
        # 还命中了传统显式指令（"记住"族），就按原样放行——不回退既有行为
        # （"记住，他们叫我大哥"本来就是用户在下指令，照旧可记）。
        strong_hits = [c for c in rule.cues
                       if c in sentence and c not in SELF_NAME_CUES]
        if strong_hits:
            return True
        if any(c in sentence for c in SELF_NAME_CUES):
            return _self_name_ok(sentence)
    return True


def _salience(sentence: str, rule: _Rule) -> float:
    value = BASE_SALIENCE
    if rule.explicit:
        value += EXPLICIT_SALIENCE_BONUS
    if any(c in sentence for c in TIME_CUES):
        value += TIME_SALIENCE_BONUS
    if any(c in sentence for c in EMOTION_CUES):
        value += EMOTION_SALIENCE_BONUS
    return round(min(1.0, max(0.0, value)), 2)


def _candidate(sentence: str, rule: _Rule) -> dict:
    return {
        "kind": rule.kind,
        "content": sentence,
        "confidence": RULE_CONFIDENCE,
        "confidence_means": f"用户单轮自述，未经核实（{RULE_VERSION}：{rule.code}）",
        "salience": _salience(sentence, rule),
    }


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------


def derive_report(
    user_message: str,
    *,
    profile_texts: Sequence[str] = (),
    participants: Sequence[tuple[str, str]] = (),
) -> DeriveReport:
    """派生候选并返回**完整否决轨迹**（写审计用；不含任何内容原文）。

    参数说明：
    - `user_message`：用户这一轮说的话。**这是唯一的输入内容**。
    - `profile_texts`：档案里已有文本（traits/background/expression_style 的
      `.text`），仅用于 N4 重叠否决。
    - `participants`：`(entity_id, display_name)` 序列，仅用于 R-REL 的指称判断。

    **没有、也不会有**任何接收模型回答的参数（见模块 docstring 第 1 条）。
    """
    message = str(user_message or "").strip()
    rejections: list[Rejection] = []

    if not message:
        return DeriveReport((), (Rejection("EMPTY_MESSAGE", -1),))
    if _is_greeting_or_too_short(message):
        return DeriveReport((), (Rejection("N2_GREETING_OR_TOO_SHORT", -1),))
    if len(message) > MAX_MESSAGE_CHARS:
        return DeriveReport((), (Rejection("N6_TOO_LONG", -1),))
    sensitive = _sensitive_codes(message)
    if sensitive:
        # 注意：只记代码，不记内容——审计里绝不能出现手机号/身份证号。
        return DeriveReport(
            (),
            tuple(Rejection(f"N5_SENSITIVE:{code}", -1) for code in sensitive),
        )

    candidates: list[dict] = []
    seen: set[str] = set()
    for idx, sentence, ending in _sentences(message):
        if _is_question(sentence, ending):
            rejections.append(Rejection("N1_QUESTION", idx))
            continue
        if _is_meta(sentence):
            rejections.append(Rejection("N7_META_TALK", idx))
            continue
        if any(cue in sentence for cue in BACKREF_CUES):
            # 用户复述模型的话时，不能反手记成"用户提供的事实"（防洗白）。
            rejections.append(Rejection("N3_BACKREF_TO_MODEL", idx))
            continue
        rule = next((r for r in _RULES if _matches(r, sentence, participants)), None)
        if rule is None:
            continue
        if len(sentence) > MAX_CONTENT_CHARS:
            rejections.append(Rejection("SENTENCE_TOO_LONG", idx, rule.code))
            continue
        overlap = _profile_overlap(sentence, profile_texts)
        if overlap >= PROFILE_OVERLAP_THRESHOLD:
            rejections.append(Rejection("N4_PROFILE_OVERLAP", idx, rule.code))
            continue
        if sentence in seen:
            rejections.append(Rejection("DUPLICATE_IN_TURN", idx, rule.code))
            continue
        if len(candidates) >= MAX_CANDIDATES_PER_TURN:
            rejections.append(Rejection("MAX_PER_TURN_CAP", idx, rule.code))
            continue
        seen.add(sentence)
        candidates.append(_candidate(sentence, rule))

    return DeriveReport(tuple(candidates), tuple(rejections))


def derive_candidates(
    user_message: str,
    *,
    profile_texts: Sequence[str] = (),
    participants: Sequence[tuple[str, str]] = (),
) -> list[dict]:
    """`derive_report(...).candidates` 的便捷形式（P6.2-b 接线时用这个）。"""
    return list(derive_report(
        user_message,
        profile_texts=profile_texts,
        participants=participants,
    ).candidates)
