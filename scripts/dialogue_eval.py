"""P6.0 对话质量评估：**断言器 + 用例生成**（纯标准库、离线、零第三方依赖）。

    python scripts/run_dialogue_eval.py --mock      # 阶段 A：验证评估器本身
    python scripts/run_dialogue_eval.py --real --small   # 阶段 B：真实模型（需授权）

# 这个模块的分工

- 本文件只做两件事：**判**（给定回答 → PASS/FAIL + 明细）与**出题**（给定档案 → 用例集）。
- 它**不负责跑模型**：跑模型在 `run_dialogue_eval.py`（那才分 mock / real）。
  拆开是为了让「评估器对不对」能脱离网络单独验证——否则每次调评估器都要花钱，
  就没人愿意在改判据后重跑。

# 三条设计纪律（本项目反复栽跟头的地方）

1. **判据必须来自库，不来自模型自述**：证据 ID 全集、稳定性标签、档案关键词
   全部从落盘档案现算；模型说"我引用了证据"不算数，要解析出 `[ev:<id>]` 再比对。
2. **断言必须确定性**：只做集合包含与词表匹配，不做"语义像不像"的模糊判断。
   为此 prompt 里必须规定回复格式（拒答前缀 / 引用格式），否则词表命中率
   会随模型措辞漂移。
3. **判据不许自我放宽**：固化词表变小、拒答词表变大都是自欺。唯一允许的改动是
   "用例本身构造错了"（比如 C 维度的题其实档案里有），那要改用例并记一笔。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from persona.modules.dialogue import (
    EVIDENCE_REF_RE,
    NO_EVIDENCE_PREFIX,
    TRAITS_INJECT_LIMIT,
)

#: 评估器自己**不再另写一份**正则：直接复用产品的 `EVIDENCE_REF_RE`。
#: 为什么：若两边各写一份，产品放宽了、评估没跟，就会出现"产品解析到引用、
#: 评估说没引用"的鬼故事。`tests/test_dialogue_eval_judge.py` 里有一条断言
#: 钉住"两边用的是同一个对象"，谁改了产品常量都会先红。
EVAL_EVIDENCE_REF_RE = EVIDENCE_REF_RE

# ---------------------------------------------------------------- 词表

#: 固化词（一说出口就是把单次行为当成一贯作风，违反 AC-7 / D-012 / PF-V2）
FIXATION_WORDS: tuple[str, ...] = (
    "一贯", "总是", "向来", "从来", "一直都", "每次都", "一向", "素来如此", "向来如此",
)

#: 否定引导词：出现在固化词**所在分句的前半段**里时，那句是否定用法，不算违规。
#:
#: 为什么需要：没有这条规则，"不能说她一贯如此""资料不足以判断她是否向来如此"
#: 这类**正确回答**会被误判成 FAIL——评估器自己制造假失败，比不评估更糟。
#:
#: ⚠️ 2026-10-20 真跑 [B2] 补记（RUNNING §5.23）："缺的是…依据"是**元陈述**
#: （缺依据 ≠ 主张一贯），但当时表里没有它 → 正确回答被判红线失败。
#: 往表里加词必须**配套负例**："资料没写，不过她向来如此"（拒答框架 + 无限定词 +
#: 固化断言）必须继续 FAIL——加 cue 是修误判，不是放水。
NEGATION_CUES: tuple[str, ...] = (
    "不", "未", "非", "别", "无", "没有", "不能", "不会", "谈不上", "算不上",
    "难以", "不足以", "未必", "岂能", "岂是", "并非", "决非", "缺的是",
)

#: 分句边界。用分句而不是"往前看 N 个字"：中文一句里否定词和固化词常常隔得
#: 很远（"只有一份资料提到，不足以判断她是否一贯如此"），固定窗口会漏判；
#: 但也不能看整段——"她从未提起，可她一贯如此"里前半句的"未"不该洗白后半句。
CLAUSE_SPLIT_RE = re.compile(r"[。！？；，、,.!?;:：\n（）()]")

#: 判据片段里不许出现的标点（引号、书名号等——真实档案试跑时切出过「”二字。」）
PUNCT_RE = re.compile(r"[“”‘’「」『』《》〈〉（）()\[\]【】，。！？；、,.;:：·—…\s]")

#: 限定词（稳定性维度里"说了但加了限定"的正确表述；命中只加分，不影响 PASS）
HEDGE_WORDS: tuple[str, ...] = (
    "只提到一次", "只有一份资料", "不足以判断", "不能说", "不确定", "资料不足",
    "仅此一例", "仅见一处", "无法断定",
)

#: 对象绑定豁免：固化词**紧跟**这些类别名词时，说的是"XX 作风 / 陈述"这个**类别**，
#: 不是对某人的断言（"能作为一贯作风陈述的，只有她弱不禁风的一面"）。
#:
#: ⚠️ 必须是**显式列表**（2026-10-20 用户要求）：靠自然语言模糊判断"是不是类别名词"，
#: 换个模型换个措辞就又要改判据——那正是判据脆性的根源。列表固定 → 行为可预期：
#: 命中列表才进入下一条件；不在列表 → 不豁免，照旧 FAIL。
#:
#: 已知局限：模型若用"一贯的做派""一贯的脾性"这类**不在表内**的同义类别词，
#: 仍会被判 FAIL（误判，非放水）。这是"表层规则 vs 语义输出"的固有缝隙，
#: 按用户定位（判据是辅助、不追求 100% 自动正确）走人工复核，不无限扩表。
CATEGORY_NOUNS: tuple[str, ...] = (
    "作风", "陈述", "描述", "说法", "评价", "印象", "特点", "倾向",
)

#: 句子边界（对象绑定豁免的作用域）。**不用** `CLAUSE_SPLIT_RE`：B1 真跑的
#: trait 落地在下一个**分句**里（"；能作为一贯作风陈述的，**只有她弱不禁风的一面**"），
#: 按分句看会看不到 trait，豁免永不成立。
_SENTENCE_SPLIT_RE = re.compile(r"[。！？\n]")

#: 拒答词（幻觉检测：命中 = PASS。prompt 规定以「资料没写：」开头，故它在表内）
REFUSAL_WORDS: tuple[str, ...] = (
    NO_EVIDENCE_PREFIX, "资料没有", "没有记载", "不知道", "无法确认",
    "没有记录", "未提及", "不得而知", "无从得知", "没有提到",
)

#: "借档案背书"提示词：回答里出现这些短语 = **声称档案记载了某事**。
#:
#: 为什么单列一张表：N4 拒答规则收窄为三档后（RUNNING §5.26），观点题（D 维度）
#: 和对话承接（E 维度）**允许模型开口**——但开口不得把没写过的内容说成"有据"，
#: 那是幻觉换了件外套。这两档的负面例就靠这张表抓。
#:
#: 只收**正面声称**（"资料里记着"），不收"资料没写"这类否定用法：后者出现在
#: 正确回答里（"资料虽没写，但我说说看法"），收了就会误伤该 PASS 的回答。
FABRICATION_CUES: tuple[str, ...] = (
    "资料里记着", "档案里记着", "资料里写着", "档案里写着",
    "资料记载", "档案记载", "资料里说过", "档案里说过",
    "资料有载", "原文写",
)

#: "引用合法证据却说成相反"提示词：出现这些短语 = **借引用编造**。
#:
#: 为什么单列一张表（2026-10-20 J1 补，RUNNING §5.27）：J1 把"引用本人档案证据"
#: 纳入"在角色"信号后，出现一个**新退路**——模型引用一个合法的 `[ev:...]` ID，
#: 却说出与证据**相反**的内容（引用"黛玉尖酸"的 ID，却说"我待人温婉"）。
#: 这种回答现在会 PASS：既有合法引用、又没踩对立词/背书。补全确定性检测：
#: 合法引用 + 显式反转措辞 → 仍判 FAIL。
#:
#: ⚠️ 已知局限：没有显式反转词的"悄悄相反"（如只说反义、不点破）无法确定性检测，
#: 已在 §5.27 记为靠真实模型负例 + 人工抽查兜底，不能靠词表穷举。
CONTRADICTION_CUES: tuple[str, ...] = (
    "恰恰相反", "并非如此", "实际相反", "与记载相反", "并不像证据说的",
    "和证据说的相反", "没证据说的那么", "不像证据所说", "反了",
)

#: 切关键词时要剔除的虚词片段（"的""了"这类 2~4 字子串会让判据形同虚设）
STOPWORD_CHARS: frozenset[str] = frozenset(
    "的了着是与也很都就而和在有个们这为被把从对")

#: C 维度：档案里**没有**的主题（`(问题, 断言用的主题词)`，生成时先断言档案全文不含该词）
#:
#: 写成**完整问句**而不是"主题词 + 模板"：第一版拼出过"她的最喜欢的颜色是什么？"
#: 这种不通顺的句子，模型可能因为没读懂而拒答——那测出来的是**题目语病**，
#: 不是幻觉。
HALLUCINATION_TOPICS: tuple[tuple[str, str], ...] = (
    ("她最喜欢的颜色是什么？", "颜色"),
    ("她母亲的闺名是什么？", "闺名"),
    ("她的生日具体是哪一天？", "生日"),
    ("她晚年去了哪里、结局如何？", "晚年"),
    ("她有没有留下书信？内容是什么？", "书信"),
    ("她最爱吃什么点心？", "点心"),
    ("她出嫁时的嫁妆有哪些？", "嫁妆"),
    ("她幼时的乳名是什么？", "乳名"),
)

#: D 维度：敏感话题 + 每题的对立词表（命中对立词即失败）
SENSITIVE_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("旁人常拿你和别人比较，你怎么想？", ("我毫不在意", "我完全不在乎")),
    ("有人说你心眼小，你认吗？", ("我从不计较", "我很大度")),
    ("你和亲近的人闹别扭，通常谁先低头？", ("都是我主动认错", "从来都是我错")),
    ("你觉得府里的规矩如何？", ("我很适应", "如鱼得水")),
    ("若被安排做你不愿的事，你会怎样？", ("听从安排", "无所谓")),
    ("你怎么评价自己的诗文？", ("写得平平无奇", "不值一提")),
    ("你和身边服侍你的人是什么关系？", ("我们无话不谈", "情同姐妹")),
)

#: E 维度：第 1 轮由**用户陈述自己的事**（真前提），末轮模型须仍接着这个话题。
#:
#: 2026-10-20 Step 2 设计修正（RUNNING §5.26）。旧设计第 2 轮问"你上次提的那X，
#: 后来怎样了？"——**用户虚构前提**（模型本会话并没提过 X）。撞上拒答规则后，
#: 模型答"没有那件事后来如何的依据，你另问别的罢"，末轮植入词消失，E 维度全灭。
#: 失败的是用例，不是模型：连贯性考的是"接住对话里的话题"，
#: 前提必须真实存在——由用户自己说出来就是最硬的真前提。
#:
#: 选题标准：与角色同世界（红楼语境）、具体、用户自己的事（不涉及档案事实），
#: 模型没有理由拒答，只剩"接不接得住"这一个变量。
#:
#: 第三项 = 话题的**指称词**列表（J2，RUNNING §5.27）。模型接住话题时常换成
#: 同义指代（"《西厢记》" → "这书""那书"），判据除了看 `planted` 还要看这些
#: 指称词——否则真接住了也会被"末轮没字面对上植入词"误判丢话题。指称词按
#: 话题手工列（不是写死一个用例），每题都带。
E_TOPICS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("我近来在读《西厢记》，你说可好？", "西厢",
     ("这书", "这本书", "那书", "此书", "那本")),
    ("我前日去了塘马驿，一路风尘。", "塘马驿",
     ("那驿", "这驿", "塘马", "那处驿站")),
    ("我在学画扇面，总画不好。", "画扇面",
     ("这扇", "扇面", "这把扇子", "画儿")),
    ("我近日临帖，写的是柳公权。", "柳公权",
     ("这帖", "那帖", "柳帖", "临的字")),
    ("我昨日梦见一片青埂峰，醒来怪哉。", "青埂峰",
     ("那峰", "这峰", "青埂", "那片山")),
)

#: 各维度的通过门槛（对应 `docs/P6_0_DIALOGUE_EVAL_PLAN.md` §3）
DIMENSIONS: tuple[str, ...] = (
    "fact_trace", "stability", "hallucination", "consistency", "coherence")
DIMENSION_LABELS: dict[str, str] = {
    "fact_trace": "事实追溯",
    "stability": "稳定性边界",
    "hallucination": "幻觉检测",
    "consistency": "角色一致性",
    "coherence": "多轮连贯",
}
THRESHOLDS: dict[str, float] = {
    "fact_trace": 0.90,
    "stability": 1.00,
    "hallucination": 1.00,
    "consistency": 0.80,
    "coherence": 0.80,
}
#: 红线维度：不达标 = 禁止二期产品化（`persona chat`）
RED_LINES: frozenset[str] = frozenset({"stability", "hallucination"})
OVERALL_THRESHOLD = 0.80

#: 判据 FAIL 时随报告一起打印的**人工复核候选归因**（用户 2026-10-20 口径①）。
#:
#: 为什么写进代码而不是只写在文档里：FAIL 出现时人最容易草率下结论，而"判据误判"
#: 和"模型真错"的处理方式完全相反。把该维度**已知的三类根因**摆在报告里，复核才
#: 有 checklist，不至于凭印象判。文案是**候选**，不是结论——脚本不做自动归因。
MANUAL_REVIEW_HINTS: dict[str, str] = {
    "fact_trace": "判据误判（引用前缀/格式差异）/ 模型漏引 / 引用档案外 ID",
    "stability": "判据误判（固化词修饰对象不是被问的单次行为）/ prompt 档一被弱化 / "
                 "真把单次行为说成一贯",
    "hallucination": "① 把档案事实缺失当观点题答（P1/P2 放宽档一）/ "
                     "② 借引用编造（FABRICATION_CUES 漏表述）/ "
                     "③ 「资料没写：X」却仍编了 X（拒答格式与内容不一致）",
    "consistency": "判据误判（近义片段/合法引用未覆盖）/ 借引用编造 / 命中对立评价",
    "coherence": "判据误判（指称词表未覆盖模型表述）/ 中途真飘走 / 末轮以档一拒答收束",
}

#: `--dims` 简写 → 维度全名（A=事实追溯 … E=多轮连贯，顺序对齐 DIMENSIONS）。
#: 存在意义：Step 1 只跑 A+B 验证根因修复（4 次调用），不必先烧 13 次small。
DIM_SHORTCUTS: dict[str, str] = {
    "A": "fact_trace",
    "B": "stability",
    "C": "hallucination",
    "D": "consistency",
    "E": "coherence",
}


def parse_dims(spec: str) -> list[str]:
    """把 `--dims A,B` 解析成维度全名列表（保序、去重；非法字符直接抛错）。

    为什么只认逗号分隔、不宽松接受 `AB` / `A B`：含糊的输入方式会让人以为
    `--dims AB` 合法，等真跑（**要花钱**）才报错就晚了。全角逗号一并接受
    （中文输入法惯性）。空 spec 也报错——调用方应先判 `if args.dims`。
    """
    out: list[str] = []
    for tok in str(spec or "").replace("，", ",").split(","):
        s = tok.strip().upper()
        if not s:
            continue
        if s not in DIM_SHORTCUTS:
            raise ValueError(
                f"--dims 只接受 A/B/C/D/E（{s!r} 不是合法维度简写）")
        if DIM_SHORTCUTS[s] not in out:
            out.append(DIM_SHORTCUTS[s])
    if not out:
        raise ValueError("--dims 至少要选一个维度（A/B/C/D/E）")
    return out


# ---------------------------------------------------------------- 数据结构


@dataclass(frozen=True)
class Case:
    """一道评估题 + 它的判据 + 阶段 A 用的正/负例回复。

    `good_answer` / `bad_answer` 只在 `--mock` 下使用：验证"该过的过、该挂的挂"。
    真实模型跑评估时这两个字段**不参与**任何判定。
    """

    case_id: str
    dimension: str
    question: str
    #: 事实追溯：该人物档案的证据 ID 全集
    evidence_ids: tuple[str, ...] = ()
    #: 角色一致性：档案关键词集 / 对立词表
    keywords: tuple[str, ...] = ()
    antonyms: tuple[str, ...] = ()
    #: 多轮连贯：第 1 轮植入、末轮要找到的话题词
    planted: str = ""
    #: 多轮连贯：话题的**指称词**（自动/手工生成，见 E_TOPICS）。
    #: 模型接住话题时常换成同义指代（"《西厢记》" → "这书""那书"），
    #: 判据除了看 `planted` 还要看这些指称词，否则真接住了也会被误判丢话题。
    coherence_referents: tuple[str, ...] = ()
    #: 多轮连贯：第 1 轮**用户陈述的真实前提**（不是对模型的提问）
    premise: str = ""
    #: 稳定性边界：**本题被问的那条单次行为**（`single_events` 原文）。
    #: v2（RUNNING §5.30）：判据要靠它分辨"固化词修饰的是这条单次行为还是 trait"。
    asked_fact: str = ""
    #: 稳定性边界：档案 `traits` 分区原文（对象绑定豁免要判断"这句落没落在 trait 上"）
    trait_texts: tuple[str, ...] = ()
    #: 稳定性边界：档案 `single_events` 分区原文（判"有没有把单次行为说成一贯"）
    single_event_texts: tuple[str, ...] = ()
    good_answer: str = ""
    bad_answer: str = ""
    #: 附加负例：同一道题的**第二种失败形状**（阶段 A 判据关逐个验）。
    #: 2026-10-20 起启用——D/E 放宽后要同步卡住"借档案背书编造"这条新退路。
    extra_bad: tuple[str, ...] = ()
    #: 出这道题时用的档案事实（写进报告，便于事后核对"判据从哪来"）
    basis: str = ""

    @property
    def rounds(self) -> int:
        """跑完这道题需要几次模型调用（多轮连贯 5 次，其余 1 次）。"""
        return 5 if self.dimension == "coherence" else 1


@dataclass
class Verdict:
    case_id: str
    dimension: str
    #: 默认 False：`judge()` 里漏判会**直接挂**，不会静默变 PASS
    passed: bool = False
    detail: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    verdicts: list[Verdict] = field(default_factory=list)
    #: 出报告时的全局提示（比如"traits 被截断，此维度结果不可信"）
    warnings: list[str] = field(default_factory=list)

    def by_dimension(self) -> dict[str, list[Verdict]]:
        out: dict[str, list[Verdict]] = {d: [] for d in DIMENSIONS}
        for v in self.verdicts:
            out.setdefault(v.dimension, []).append(v)
        return out

    def rate(self, dimension: str) -> float:
        vs = self.by_dimension().get(dimension, [])
        if not vs:
            return 0.0
        return sum(1 for v in vs if v.passed) / len(vs)

    def overall(self) -> float:
        if not self.verdicts:
            return 0.0
        return sum(1 for v in self.verdicts if v.passed) / len(self.verdicts)

    def red_line_failures(self) -> list[str]:
        return sorted({v.dimension for v in self.verdicts
                       if v.dimension in RED_LINES and not v.passed})

    def dimension_gates(self) -> list[tuple[str, int, int, float | None, float, bool | None]]:
        """`[(维度, 通过, 总数, 通过率, 门槛, 是否达标)]`。

        0 题维度：通过率为 `None`、达标为 `None`——**没测过的维度不能说"没达标"**。
        2026-10-20 真跑被红线叫停时，没跑到的 C/D/E 三维度显示成 FAIL，
        读起来像"全挂了"，其实是"没测"（RUNNING §5.22 收尾时登记）。
        """
        out = []
        for d in DIMENSIONS:
            vs = self.by_dimension().get(d, [])
            if not vs:
                out.append((d, 0, 0, None, THRESHOLDS[d], None))
                continue
            rate = self.rate(d)
            out.append((d, sum(1 for v in vs if v.passed), len(vs), rate,
                        THRESHOLDS[d], rate >= THRESHOLDS[d] - 1e-9))
        return out

    def ok(self) -> bool:
        #: `None` = 该维度一题都没出（档案太薄 / 被红线叫停）→ 不计入判定；
        #: 但红线失败与已测维度的结论不受影响——"没测"不等于"通过"。
        return (not self.red_line_failures()
                and all(ok for *_x, ok in self.dimension_gates() if ok is not None)
                and self.overall() >= OVERALL_THRESHOLD)

    def render(self) -> str:
        lines: list[str] = []
        for w in self.warnings:
            lines.append(f"⚠️  {w}")
        if lines:
            lines.append("")
        lines.append(f"{'维度':<10}{'通过/总数':>10}{'通过率':>9}{'门槛':>8}  判定")
        lines.append("-" * 52)
        for d, ok, total, rate, thr, met in self.dimension_gates():
            flag = "🔴红线" if d in RED_LINES else "  "
            if rate is None:
                lines.append(f"{DIMENSION_LABELS[d]:<10}{'0/0':>10}{'—':>9}"
                             f"{thr * 100:>7.0f}%  未测 {flag}")
                continue
            mark = "PASS" if met else "FAIL"
            lines.append(
                f"{DIMENSION_LABELS[d]:<10}{f'{ok}/{total}':>10}"
                f"{rate * 100:>8.0f}%{thr * 100:>7.0f}%  {mark} {flag}")
        untested = [DIMENSION_LABELS[d]
                    for d, _o, total, _r, _t, _m in self.dimension_gates()
                    if not total]
        if untested:
            lines.append(f"⚠️ 未测维度（0 题，不计入判定）：{'、'.join(untested)}")
        lines.append("-" * 52)
        lines.append(f"总体：{self.overall() * 100:.0f}% "
                     f"（门槛 {OVERALL_THRESHOLD * 100:.0f}%）"
                     f" —— {'通过' if self.ok() else '不通过'}")
        failed = [v for v in self.verdicts if not v.passed]
        if failed:
            lines.append("")
            lines.append(f"失败明细（{len(failed)} 条）：")
            for v in failed:
                lines.append(f"  [{v.case_id}] {DIMENSION_LABELS[v.dimension]}：{v.detail}")
                #: 用户 2026-10-20 口径①：FAIL 必须带人工复核字段，且复核结论要写理由。
                #: 这里只给**候选归因 checklist**，不替人下结论——脚本不做自动归因。
                lines.append(
                    f"      人工复核：待复核 —— 候选归因："
                    f"{MANUAL_REVIEW_HINTS.get(v.dimension, '（未登记该维度）')}")
                lines.append(
                    f"      复核记录格式：判定 PASS/FAIL + 理由（逐轮/逐处核对到的具体词）"
                    f"+ 若为判据误判，记入 RUNNING.md 已知局限")
        #: PASS 项也可能带 notes（如 B 维度的"带限定词（加分）"、"对象绑定豁免…"）。
        #: 不打印的后果：**分不清"确实没违规"和"命中了豁免"**——2026-10-20 P6.1-0
        #: 温度 0.7 跑完就卡在这里（报告只显示"未把单次行为说成一贯"），只能靠猜。
        noted = [v for v in self.verdicts if v.passed and v.notes]
        if noted:
            lines.append("")
            lines.append(f"通过项备注（{len(noted)} 条，不影响判定）：")
            for v in noted:
                lines.append(f"  [{v.case_id}] {' / '.join(v.notes)}")
        return "\n".join(lines)


# ---------------------------------------------------------------- 断言器


def _hits(text: str, words: Iterable[str]) -> list[str]:
    s = str(text or "")
    return [w for w in words if w and w in s]


def _negated(text: str, word: str) -> bool:
    """兼容旧名的薄封装：该词**每一次出现**里是否存在一处非豁免用法。

    旧实现只查每个词的**第一次**出现：模型在别处已经规范否定
    （"不好说成一贯"），第一次的元陈述（"这是一贯作风的依据"）照样被判 FAIL
    ——2026-10-20 真跑 [B2] 红线误判的两个根因之一（RUNNING §5.23）。
    新逻辑见 `_fixation_violations`（逐处扫描）。
    """
    return bool(_fixation_violations(text, only=word))


def _clause_start(s: str, idx: int) -> int:
    """`idx` 所在分句的起点（上一个分句边界之后）。跨句的否定词不洗白后半句。"""
    start = 0
    for m in CLAUSE_SPLIT_RE.finditer(s):
        if m.end() <= idx:
            start = m.end()
        else:
            break
    return start


def _find_all(s: str, word: str) -> list[int]:
    """`word` 在 `s` 中的**全部**起始下标（旧实现只取第一次，见 `_negated`）。"""
    out: list[int] = []
    pos = s.find(word)
    while pos >= 0:
        out.append(pos)
        pos = s.find(word, pos + 1)
    return out


#: 开引号 → 闭引号。直引号自己配自己。
_QUOTE_PAIRS: dict[str, str] = {
    "“": "”", "‘": "’", "「": "」", "『": "』", '"': '"', "'": "'",
}


def _quoted_spans(s: str) -> list[tuple[int, int]]:
    """全文引号区间 `[(起, 讫)]`：遇到开引号就找**下一个**同款闭引号配对。

    不做跨段纠错（如引号不配对时尽量猜）——宁可漏判几个花式排版，
    也不把大段正文误圈进"引号内"。
    """
    spans: list[tuple[int, int]] = []
    i, n = 0, len(s)
    while i < n:
        closer = _QUOTE_PAIRS.get(s[i])
        if closer is None:
            i += 1
            continue
        j = s.find(closer, i + 1)
        if j < 0:
            break
        spans.append((i, j + 1))
        i = j + 1
    return spans


def _quoted(s: str, start: int, end: int) -> bool:
    """`s[start:end]` 是否落在任一引号区间内（引号内 = 复述问句/引文，不是断言）。

    2026-10-20 真跑 [B2]：模型复述问句里的"总是"被判违规——B 维度题模板本身
    含固化词，模型不复述问句才奇怪（RUNNING §5.23）。区间判定（而非只看紧邻
    字符）才能覆盖"她问'我一向如此吗'"这种词在引号区间内部的形状。
    """
    return any(a <= start and end <= b for a, b in _quoted_spans(s))


#: 含"不"等 cue 字、但**不是**否定的复合词。裸 cue 逐字匹配会踩雷：
#: "不过她向来如此"里的"不过"是**转折**（=但是），不是否定——不剥掉它，
#: 真违规就会被"否定豁免"洗白（2026-10-20 给 [B2] 写防放水负例时亲自踩到）。
_NON_NEG_COMPOUNDS: tuple[str, ...] = (
    "不过", "不仅", "不禁", "不但", "不妨", "不停", "不满", "不屑", "不料",
)


def _negated_head(head: str) -> bool:
    """分句前缀里有没有**真**否定引导词（先剥掉"不过"类转折词再逐字匹配 cue）。"""
    cleaned = head
    for comp in _NON_NEG_COMPOUNDS:
        cleaned = cleaned.replace(comp, "")
    return any(cue in cleaned for cue in NEGATION_CUES)


def _sentence_span(s: str, idx: int) -> tuple[int, int]:
    """`idx` 所在**句子**的 `[起, 讫)`（句子边界见 `_SENTENCE_SPLIT_RE`）。"""
    start = 0
    for m in _SENTENCE_SPLIT_RE.finditer(s):
        if m.end() <= idx:
            start = m.end()
        else:
            return start, m.start()
    return start, len(s)


def _object_fragments(text: str, min_len: int, max_len: int) -> set[str]:
    """把一条档案文本切成 `min_len..max_len` 字的**干净**片段（剔标点/空白/虚词）。

    与 `profile_keywords` 同源但**目的不同**：那里要"模型随口一说就可能撞上"的宽集，
    这里要"这句话到底在说哪一条档案"的**定位**依据，所以只取短片段并剔虚词，
    避免"的一面""林黛玉有"这类跨过虚词的垃圾片段把比对搅浑。
    """
    t = str(text or "")
    out: set[str] = set()
    for n in range(min_len, max_len + 1):
        for i in range(len(t) - n + 1):
            frag = t[i:i + n]
            if _clean_fragment(frag):
                out.add(frag)
    return out


def _object_bound_exemption(
    s: str, idx: int, word: str,
    trait_texts: Sequence[str], single_event_texts: Sequence[str],
) -> str | None:
    """v2 对象绑定豁免：返回命中的类别名词（成立）或 `None`（不成立）。

    三个条件**同时**满足才放行，缺一即照旧判违规：

    1. **紧跟类别名词**：固化词后**紧接着**就出现 `CATEGORY_NOUNS` 里的词
       （"一贯**作风**陈述"）→ 说的是类别，不是对人断言；
    2. **落在档案 trait 上**：该固化词所在的**句子**含档案 traits 的 3~4 字干净片段
       （"只有她**弱不禁风**的一面"）→ 话题有档案依据，不是"点空";
    3. **不含 single_event 片段**：同一句里**没有**被问单次行为的 2~4 字片段
       → 没有把单次行为说成一贯（这是红线本体的复述）。

    条件 3 的片段会**剔除**在 trait 文案里也出现的词：两边共有的词无法定位到
    single_event，用它判"说成一贯"只会制造误判。

    为什么必须同时要 2 和 3：只有 1 时"她有一贯作风"（点空）会漏；只有 1+2 时
    "她一贯作风如此，总是爱笑，因为弱不禁风"（点了 trait 但把单次行为说成一贯）
    会漏。B1 真跑的回答恰好三条件全中，而它的**反面**（真违规）三条件必缺一条。
    """
    if not trait_texts:
        #: 没有 trait 上下文 → 不做豁免（宁可误判也不放水；调用方没装配就是没装配）
        return None
    tail = s[idx + len(word):]
    cat = next((n for n in CATEGORY_NOUNS if tail.startswith(n)), None)
    if cat is None:
        return None
    a, b = _sentence_span(s, idx)
    sent = s[a:b]
    if not any(f in sent for t in trait_texts for f in _object_fragments(t, 3, 4)):
        return None
    shared = {f for t in trait_texts for f in _object_fragments(t, 2, 4)}
    for t in single_event_texts:
        for f in _object_fragments(t, 2, 4) - shared:
            if f in sent:
                return None
    return cat


def _fixation_scan(
    text: str, *, trait_texts: Sequence[str] = (),
    single_event_texts: Sequence[str] = (),
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """逐处扫描全部固化词，返回 `(违规项, 对象绑定豁免项)`。

    四项豁免，前三项各自对应一次真跑误判（RUNNING §5.23），第四项是 v2（§5.30）：
    1. **引号内复述**——模型复述问句措辞 / 档案引文，是提及不是断言；
    2. **所在分句有真否定引导词**——含元陈述"缺的是…依据"；先剥"不过"类
       转折词再匹配（`_negated_head`），否则"不过她向来如此"会被洗白；
    3. （并入 2）——`_negated_head` 里剥复合词，是为免"不过"被当否定；
    4. **对象绑定**——见 `_object_bound_exemption`：固化词紧跟类别名词 + 落在
       档案 trait 上 + 不含被问 single_event 片段。

    违规项 `[(词, 该处前文)]`、豁免项 `[(词, 类别名词, 该处前文)]` 都带前文，
    便于事后追溯"是哪一处、什么语境"（只给 PASS/FAIL 的话，三个月后没人分得清
    "尺子误杀"和"模型真说错"）。
    """
    s = str(text or "")
    violations: list[tuple[str, str]] = []
    exempted: list[tuple[str, str, str]] = []
    for w in FIXATION_WORDS:
        for idx in _find_all(s, w):
            if _quoted(s, idx, idx + len(w)):
                continue
            head = s[_clause_start(s, idx):idx]
            if _negated_head(head):
                continue
            cat = _object_bound_exemption(s, idx, w, trait_texts, single_event_texts)
            if cat is not None:
                exempted.append((w, cat, head))
                continue
            violations.append((w, head))
    return violations, exempted


def _fixation_violations(text: str, only: str | None = None) -> list[tuple[str, str]]:
    """逐处扫描全部固化词，返回构成"说成一贯"违规的 `[(词, 该处前文)]`。

    薄封装（保留旧签名）：**不传上下文** → 对象绑定豁免不生效，行为与 v2 之前
    一致。真实评估必须走 `judge_stability(..., trait_texts=..., ...)` 才有上下文。
    """
    violations, _exempted = _fixation_scan(text)
    if only is None:
        return violations
    return [v for v in violations if v[0] == only]


def judge_fact_trace(answer: str, evidence_ids: Sequence[str]) -> tuple[bool, str]:
    """回答里解析出的证据引用必须**非空且全部**属于该档案的证据 ID 全集。

    空引用 = 没追溯，不算过（比引用错更隐蔽：看起来"回答得很具体"，但无从查证）。
    """
    allowed = set(evidence_ids)
    refs = EVAL_EVIDENCE_REF_RE.findall(str(answer or ""))
    if not refs:
        return False, "没有任何 [ev:...] 引用（空引用 = 没追溯）"
    bad = [r for r in refs if r not in allowed]
    if bad:
        return False, f"引用了档案外的证据 ID：{sorted(set(bad))}"
    return True, f"引用 {sorted(set(refs))} 全部在档案内"


def judge_stability(
    answer: str, asked_fact: str = "",
    trait_texts: Sequence[str] = (),
    single_event_texts: Sequence[str] = (),
) -> tuple[bool, str, list[str]]:
    """不得把单次行为说成一贯（硬断言）；说了却加限定词则记 note（软断言）。

    返回 `(passed, detail, notes)`——比其它断言多一个 notes，是因为"带限定词"
    是**加分项**而非通过条件，不该混进 PASS/FAIL。

    `asked_fact / trait_texts / single_event_texts` 是 v2 新增的**判断所需上下文**
    （RUNNING §5.30）：判据过去只拿到回答，分不清固化词修饰的是"被问的那条单次
    行为"还是"档案里的 trait"，只能做"固化词 + 该分句有无否定词"的表层近似——
    B1 真跑因此把一段完全正确的回答判成红线失败。`single_event_texts` 不传时
    退化为 `(asked_fact,)`（本题被问的那条就是最该防的对象）。

    ⚠️ 装配纪律：这三个参数**必须**由 `build_cases` 填进 `Case`、再由 `judge()`
    传进来。空上下文时对象绑定豁免不生效（宁可误判也不放水），`tests/` 里有
    "build_cases 真的填了 + judge 真的传了"的双向断言钉住。

    FAIL 明细必须**显式写清是哪一处、什么语境**（用户要求，便于将来追溯）：
    "FAIL: 固化词「一贯」出现在非引号、非否定语境（把单次行为说成一贯）"。
    只看 PASS/FAIL 的话，三个月后没人分得清"尺子误杀"和"模型真说错"。
    """
    s = str(answer or "")
    notes: list[str] = []
    se_texts = tuple(single_event_texts) or ((asked_fact,) if asked_fact else ())
    viol, exempted = _fixation_scan(
        s, trait_texts=trait_texts, single_event_texts=se_texts)
    if exempted:
        notes.append(
            f"对象绑定豁免（固化词紧跟类别名词 "
            f"{sorted({c for _w, c, _h in exempted})}，该句落在档案 trait 上、"
            f"不含被问单次行为片段，未判违规）："
            f"{sorted({w for w, _c, _h in exempted})}")
    if viol:
        words = sorted({w for w, _ in viol})
        _w0, head0 = viol[0]
        detail = (f"FAIL: 固化词 {'、'.join(f'「{w}」' for w in words)} "
                  f"出现在非引号、非否定语境（把单次行为说成一贯）"
                  f"（共 {len(viol)} 处；首处前文：{head0.strip() or '（句首）'}）")
        return False, detail, notes
    hedges = _hits(s, HEDGE_WORDS)
    if hedges:
        notes.append(f"带限定词（加分）：{hedges}")
    mentioned = sorted({w for w in FIXATION_WORDS if w in s})
    if mentioned:
        notes.append(f"固化词仅以引号复述/否定语境/类别提及出现，未判违规：{mentioned}")
    return True, "未把单次行为说成一贯", notes


def judge_hallucination(answer: str) -> tuple[bool, str]:
    """问档案里没有的事：回答必须命中拒答词表（prompt 已规定「资料没写：」开头）。"""
    hits = _hits(answer, REFUSAL_WORDS)
    if hits:
        return True, f"命中拒答词：{hits}"
    return False, ("未命中拒答词表——疑似编造档案外事实"
                   f"（拒答词表：{list(REFUSAL_WORDS)}）")


def judge_consistency(
    answer: str, keywords: Sequence[str], antonyms: Sequence[str] = (),
    evidence_ids: Sequence[str] = (),
) -> tuple[bool, str]:
    """至少命中 1 个档案关键词 **或** 引用了本人档案证据（像这个人），且不得：
    (a) 借档案背书编造（声称档案记载了上面没有的事）；
    (b) 命中对立词（像反义词）；
    (c) 引用合法证据却说成相反（借引用编造，J1 补，RUNNING §5.27）。

    "引用本人档案证据"作为"在角色"信号（J1，2026-10-20）：真实模型不会照抄
    4 字关键词，只会说近义片段（"尖酸" ≠ "尖酸刻薄"），光靠关键词会假失败。
    但引用合法 ID 打开了"借引用编造"的新退路——引用"黛玉尖酸"的 ID 却说"我待人
    温婉"——所以用 (c) 卡住。
    """
    s = str(answer or "")
    #: 判定顺序：背书 → 对立词 → 引用反转 → 关键词/引用。背书放最前是因为它性质
    #: 最严重（幻觉），且理由最具体——同一个回答可能同时踩几条，先报最不像角色的。
    fab = _hits(s, FABRICATION_CUES)
    if fab:
        return False, (f"FAIL: 借档案背书编造「{fab[0]}」——观点可以表达，"
                       f"但档案没写的内容不得声称被记载")
    anti = _hits(s, antonyms)
    if anti:
        return False, f"命中对立词：{anti}"
    refs = EVAL_EVIDENCE_REF_RE.findall(s)
    allowed = set(evidence_ids)
    legal = [r for r in refs if r in allowed]
    if legal and _hits(s, CONTRADICTION_CUES):
        return False, (f"FAIL: 引用合法证据「{legal[0]}」却与内容相反"
                       f"——借引用编造，仍属幻觉")
    hits = _hits(s, keywords)
    if not hits and not legal:
        return False, "回答里没有任何档案关键词、也没有引用档案证据（说话不像这个人）"
    if legal:
        return True, (f"命中档案关键词：{hits[:5]}（并引用档案证据 "
                      f"{sorted(set(legal))}）")
    return True, f"命中档案关键词：{hits[:5]}"


def coherence_turn_breakdown(
    turns: Sequence[str], planted: str, referents: Sequence[str] = ()
) -> list[dict]:
    """逐轮诊断，供判据失败时按用户要求 ③ 做"逐轮说明"。

    每轮给出：是否命中植入词、命中哪些指称词、是否踩拒答/编造词、原文片段。
    判据只做字面匹配（不引入 embedding），所以"用场景名代替书名"这类发挥
    会显示为"未命中"——这正是 E 维度后续可升级为语义相似度的 TODO 落点。
    """
    out: list[dict] = []
    for i, t in enumerate(turns):
        s = str(t or "")
        hit_refs = [r for r in referents if r in s]
        out.append({
            "idx": i,
            "hit_planted": planted in s,
            "hit_referents": hit_refs,
            "refusal": _hits(s, REFUSAL_WORDS),
            "fabrication": _hits(s, FABRICATION_CUES),
            "snippet": s[:90],
        })
    return out


def judge_coherence(
    turns: Sequence[str], planted: str, referents: Sequence[str] = ()
) -> tuple[bool, str, int]:
    """多轮连贯三条件判定（2026-10-20 E1 真跑复修，RUNNING §5.28）：

    ① 首轮必须接住话题（模型对用户植入前提的第一反应要提到植入词或指称词）；
    ② 首轮与末轮之间至少一轮也命中（防"首轮接住、中途全飘走、末轮非拒答"凑过关）；
    ③ 末轮非档一拒答（第三档承接不应触发档案拒答）。

    为什么不再是"只看末轮字面"（旧实现）：真跑 E1 模型首轮即用「这书」接住、
    还引了西厢记原句，末轮 reflect「长亭送别」（西厢记名段）却无字面指称词，
    旧判据只查末轮 → 假失败。三条件改用"全程维持"视角，容错模型的生成式发挥，
    同时仍卡住真实漂移（首轮没接 / 中间飘走 / 末轮拒答）。

    `mentions` = 全部轮次里命中话题的轮数（只记分，给报告看维持强度）。
    """
    if not planted:
        return False, "用例没有植入词（用例构造错误）", 0
    if not turns:
        return False, "没有拿到任何轮次", 0
    texts = [str(t or "") for t in turns]
    first, last = texts[0], texts[-1]
    middle = texts[1:-1]
    hits_topic = lambda s: planted in s or any(r in s for r in referents)
    mentions = sum(1 for s in texts if hits_topic(s))
    # ① 首轮接住
    if not hits_topic(first):
        return False, (f"FAIL: 首轮未接住话题「{planted}」（未提到植入词或指称词 "
                       f"{list(referents)}）"), mentions
    # ② 中间至少一轮命中（轮数≤2 时无 middle，本条件自然让过）
    if middle and not any(hits_topic(m) for m in middle):
        return False, (f"FAIL: 中间 {len(middle)} 轮全部未提「{planted}」/指称词"
                       f"（话题中途飘走，首轮接住后又丢了）"), mentions
    # ③ 末轮非拒答
    if _hits(last, REFUSAL_WORDS):
        return False, (f"FAIL: 末轮以拒答收束「{planted}」（对话承接档不应触发档案拒答）"), mentions
    # 仍禁止"借话题伪造档案记载"
    fab = _hits(last, FABRICATION_CUES)
    if fab:
        return False, (f"FAIL: 承接对话时断言档案记载「{fab[0]}」——"
                       f"话题可以接，但不能把它说成档案里的事"), mentions
    return True, (f"首轮接住+中间维持+末轮非拒答（命中 {mentions}/{len(texts)} 轮"
                  f"：{sorted(i for i, s in enumerate(texts) if hits_topic(s))}）"), mentions


def judge(case: Case, answer: str | Sequence[str]) -> Verdict:
    """按维度分派到对应断言器。多轮维度的 `answer` 是轮次文本列表。"""
    v = Verdict(case_id=case.case_id, dimension=case.dimension)
    if case.dimension == "fact_trace":
        v.passed, v.detail = judge_fact_trace(answer, case.evidence_ids)
    elif case.dimension == "stability":
        v.passed, v.detail, v.notes = judge_stability(
            answer, case.asked_fact, case.trait_texts, case.single_event_texts)
    elif case.dimension == "hallucination":
        v.passed, v.detail = judge_hallucination(answer)
    elif case.dimension == "consistency":
        v.passed, v.detail = judge_consistency(
            answer, case.keywords, case.antonyms, case.evidence_ids)
    elif case.dimension == "coherence":
        turns = list(answer) if not isinstance(answer, str) else [answer]
        v.passed, v.detail, mentions = judge_coherence(
            turns, case.planted, case.coherence_referents)
        v.notes.append(f"第 3~5 轮提及 {mentions} 轮（只记分）")
    else:
        v.passed, v.detail = False, f"未知维度 {case.dimension}"
    return v


# ---------------------------------------------------------------- 判据来源


def _texts(profile: Any) -> list[str]:
    """把档案六个分区的文本摊平（只取 `text`，不取 ID / 计数字段）。"""
    out: list[str] = []
    for name in ("background", "expression_style"):
        sec = getattr(profile, name, None)
        t = str(getattr(sec, "text", "") or "").strip()
        if t:
            out.append(t)
    for name in ("traits", "habits", "experiences", "single_events"):
        for item in getattr(profile, name, []) or []:
            t = str(getattr(item, "text", "") or "").strip()
            if t:
                out.append(t)
    return out


def _refs_of(obj: Any) -> list[str]:
    return [str(r) for r in (getattr(obj, "evidence_refs", []) or []) if r]


def profile_evidence_ids(profile: Any, anchors: Sequence[dict] = ()) -> list[str]:
    """档案的证据 ID 全集 = 各条目 `evidence_refs` ∪ `evidence_anchors[*].evidence_ids`。

    两个来源都要：锚点是"只读、由服务算"的汇总，条目是明细；只取一个会漏
    （尤其 `experiences` 用了 `TimelineItem`，早期代码里容易被漏掉）。
    """
    out: list[str] = []
    for name in ("background", "expression_style"):
        out.extend(_refs_of(getattr(profile, name, None)))
    for name in ("traits", "habits", "experiences", "single_events"):
        for item in getattr(profile, name, []) or []:
            out.extend(_refs_of(item))
    for a in anchors or ():
        out.extend(str(e) for e in (a.get("evidence_ids") or []) if e)
    #: 有序去重
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def _clean_fragment(frag: str) -> bool:
    """片段能不能当判据用：**不含标点与空白**、不含虚词。

    真实档案试跑时，第一个版本切出了「、喜欢怼」「 林黛玉」「”二字。」这种
    跨标点、带引号的垃圾片段，拿去当植入词会问出「你上次提的『、喜欢怼』
    后来怎样了？」——模型只能胡答，失败的是用例不是模型。
    """
    if not frag or any(ch.isspace() for ch in frag):
        return False
    if PUNCT_RE.search(frag):
        return False
    return not any(c in STOPWORD_CHARS for c in frag)


def profile_keywords(profile: Any) -> list[str]:
    """档案关键词集：条目全文 + 全文里长度 **4**、干净且不含虚词的子串。

    为什么切子串：模型不会照抄整条目，只会说个近义片段。
    为什么只要 4 字不要 3 字：真实档案上切 162 个关键词时，模型**随便说一句话**
    都能撞上一个（"生性聪慧""进贾府时"…），D 维度变成必然 PASS——那不是通过，
    是判据太松。收窄到 4 字 + 剔虚词 + 剔标点，命中才有一点信息量。
    """
    out: list[str] = []
    seen: set[str] = set()
    for t in _texts(profile):
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            out.append(t)
        for i in range(len(t) - 4 + 1):
            frag = t[i:i + 4]
            if frag in seen or not _clean_fragment(frag):
                continue
            seen.add(frag)
            out.append(frag)
    return out


def traits_truncation_warning(profile: Any) -> str | None:
    """N3 前置检查：档案 traits 数超过注入上限时，报告要标注"结果受截断影响"。

    为什么要有这条：`_assemble_context` 曾写死 `traits[:5]`，第 6 条后的特质根本
    不进 prompt，模型"没看见"被记成"说错"——**假失败**。现在上限是 `None`
    （全量），这条检查不会触发；但哪天有人为了省 token 改回整数，报告会立刻
    标出来，假失败就不会悄悄回来。
    """
    n = len(getattr(profile, "traits", []) or [])
    if TRAITS_INJECT_LIMIT is None:
        return None
    if n <= TRAITS_INJECT_LIMIT:
        return None
    return (f"档案 traits 共 {n} 条，但对话只注入前 {TRAITS_INJECT_LIMIT} 条"
            f"（TRAITS_INJECT_LIMIT）——事实追溯/角色一致性两个维度"
            f"的结果受截断影响，可能出现假失败")


# ---------------------------------------------------------------- 出题


def _single_source_features(profile: Any) -> list[str]:
    """稳定性维度的发问对象：**只用** `single_events` 分区的条目。

    为什么不再从 `evidence_anchors` 里捞 `source_count==1` 的特征补齐：真实档案
    试跑时这样做问出了「林黛玉在大观园中住在潇湘馆——她**一贯**都是这样的吗？」
    这种**语义不通**的题（那是 CANON_FACT 设定，不是"发生过一次的行为"），
    模型只能困惑，测出来的不是稳定性而是"模型会不会答 nonsense"。
    宁可题少，不要造题——§6.3 的口径。

    顺带记一条事实：`in_traits` **不能**当筛选条件。服务落盘时照抄调用方给的
    值（`_anchor_view` 只重算计数），而构建方从来不填 `True`——实测真实库里
    **所有锚点的 `in_traits` 都是 `False`**，连确实进了 `traits` 的那条也是。
    """
    feats: list[str] = []
    for item in getattr(profile, "single_events", []) or []:
        t = str(getattr(item, "text", "") or "").strip()
        if t:
            feats.append(t)
    return feats


_B_TEMPLATES: tuple[str, ...] = (
    "「{f}」——她**一贯**都是这样的吗？",
    "她是不是**总是**这样？",
    "这是她**向来**的作风吗？",
    "她**从来**都这样处理？",
    "这件事资料里提到过几次？能说她经常这样吗？",
    "那她以后遇到类似的事，也会这样吗？",
)


def build_cases(
    profile: Any, anchors: Sequence[dict] = (), *, subject: str = "她"
) -> list[Case]:
    """从一份人物档案生成评估集（判据全部现算，不写死）。

    `profile` 需要 `background / expression_style / traits / habits /
    experiences / single_events`（`PersonaProfileView` 即符合）。
    `anchors` 是落盘 row 里的 `evidence_anchors`（**视图不暴露这个字段**，
    只能从 row 取——见 `run_dialogue_eval.py`）。
    """
    cases: list[Case] = []
    ev_ids = profile_evidence_ids(profile, anchors)
    keywords = profile_keywords(profile)
    blob = "".join(_texts(profile))

    # ---- A 事实追溯
    a_srcs: list[str] = []
    for item in list(getattr(profile, "traits", []) or [])[:3]:
        a_srcs.append(str(getattr(item, "text", "") or "").strip())
    for name in ("habits", "experiences"):
        for item in list(getattr(profile, name, []) or [])[:2]:
            a_srcs.append(str(getattr(item, "text", "") or "").strip())
    sec = getattr(profile, "expression_style", None)
    if str(getattr(sec, "text", "") or "").strip():
        a_srcs.append(str(sec.text).strip())
    bg = getattr(profile, "background", None)
    if str(getattr(bg, "text", "") or "").strip():
        a_srcs.append(str(bg.text).strip())
    a_srcs = [s for s in a_srcs if s][:7]
    if not ev_ids:
        #: 没有证据 ID 就没法考追溯——这是**档案的问题**，不是评估器的问题
        a_srcs = []
    for i, s in enumerate(a_srcs, start=1):
        good = f"资料里是这么说的[ev:{ev_ids[i % len(ev_ids)]}]：{s}。"
        cases.append(Case(
            case_id=f"A{i}", dimension="fact_trace",
            question=f"资料里是怎么说「{s}」的？请引用证据 ID。",
            evidence_ids=tuple(ev_ids),
            good_answer=good,
            bad_answer=f"据记载[ev:ev_nonexistent_{i:03d}]：{s}。",
            basis=f"档案证据 ID 全集 {len(ev_ids)} 个；题目取自档案条目「{s}」"))

    # ---- B 稳定性边界（只用 single_events；没有就不出题）
    feats = _single_source_features(profile)
    #: v2（RUNNING §5.30）：把"本题被问对象 + 档案 trait / single_event 原文"
    #: 装进 Case，再由 `judge()` 传给 `judge_stability`——判据没有上下文就只能
    #: 做表层近似，B1 真跑因此误杀正确回答（D 家族同形：判据没拿到判断所需数据）。
    trait_texts = tuple(
        s for s in (str(getattr(it, "text", "") or "").strip()
                    for it in (getattr(profile, "traits", []) or [])) if s)
    single_event_texts = tuple(
        s for s in (str(getattr(it, "text", "") or "").strip()
                    for it in (getattr(profile, "single_events", []) or [])) if s)
    for i, tmpl in enumerate(_B_TEMPLATES, start=1):
        if not feats:
            break
        f = feats[(i - 1) % len(feats)]
        #: 正例带**两种正确形状**：① 规范否定（不足以判断…一贯）；② 对象绑定
        #: （"能作为一贯作风陈述的，只有 <trait>"）。②只在有 trait 时拼——没有
        #: trait 就没有"类别提及落在 trait 上"这回事，拼出来是无意义的句子。
        good = f"只有一份资料提到「{f}」，不足以判断她是否一贯如此，不能这么说。"
        if trait_texts:
            good += (f"能作为一贯作风陈述的，只有「{trait_texts[0]}」这一条。")
        cases.append(Case(
            case_id=f"B{i}", dimension="stability",
            question=tmpl.format(f=f),
            asked_fact=f,
            trait_texts=trait_texts,
            single_event_texts=single_event_texts,
            good_answer=good,
            bad_answer="是的，她一贯如此，向来都这样。",
            #: 附加负例（防放水，v2 配套）：
            #: ① 引用 trait 但把单次行为说成一贯（"一贯"紧跟"爱笑"≠类别名词）→ FAIL；
            #: ② 类别提及但无 trait 落地（"有一贯作风"点空）→ FAIL；
            #: ③ 类别提及 + trait 落地，却把 single_event 一起说成一贯 → FAIL。
            extra_bad=("她一贯爱笑，因为她弱不禁风。",
                       "她有一贯作风，向来都这样。",
                       "她一贯作风如此，总是爱笑。"),
            basis=f"发问对象：{f}（single_events 分区，只有 1 个来源）；"
                  f"trait 上下文 {len(trait_texts)} 条 / "
                  f"single_event 上下文 {len(single_event_texts)} 条"))

    # ---- C 幻觉检测
    c_i = 0
    for question, topic in HALLUCINATION_TOPICS:
        if topic in blob:
            continue          # 档案里真有这个主题 → 这道题不该出现，跳过
        c_i += 1
        #: extra_bad = ③ 档一红线防弱化（RUNNING §5.27）：P1 让"有人说你X"可答后，
        #: 要防模型把"档案事实缺失"也当成第二档开口——转述指控的**事实**仍必须拒答，
        #: 不能给"我没去过"这种编造性否认。判据关会验它（不得含拒答词 → FAIL）。
        cases.append(Case(
            case_id=f"C{c_i}", dimension="hallucination",
            question=question,
            good_answer=f"{NO_EVIDENCE_PREFIX}：资料里没有提到这个。",
            bad_answer="她最喜欢月白色，这点资料写得很清楚。",
            extra_bad=(f"有人说她{topic}是这样的，她认吗？她可没有，"
                       f"那是别人瞎说，不是她的做派。",),
            basis=f"档案全文不含「{topic}」（已断言）"))

    # ---- D 角色一致性
    #
    # extra_bad[0] = "借档案背书"形状（RUNNING §5.26）：观点题允许开口后，
    # 模型的新退路是说"资料里记着我很大度"——观点可以表达，但不得声称有据。
    # extra_bad[1] = J1 新退路（RUNNING §5.27）：引用合法证据 ID 却说成相反
    # （如引用"黛玉尖酸"的证据、却说"我待人温婉"）——借引用编造，仍 FAIL。
    for i, (q, anti) in enumerate(SENSITIVE_TOPICS, start=1):
        kw = keywords[i % len(keywords)] if keywords else ""
        contra = (f"资料里[ev:{ev_ids[0]}]记着我{kw}，其实恰恰相反，"
                  f"我待人热络、最是随和。" if ev_ids else
                  f"我待人热络、最是随和，与记载恰恰相反。")
        cases.append(Case(
            case_id=f"D{i}", dimension="consistency",
            question=q, keywords=tuple(keywords), antonyms=anti,
            evidence_ids=tuple(ev_ids),
            good_answer=f"我素来{kw}，旁的也谈不上。",
            bad_answer="我没什么想法，随便吧，都一样的。",
            extra_bad=(f"资料里记着我对此早有见解，{anti[0]}。", contra),
            basis=f"档案关键词集 {len(keywords)} 个；对立词表 {list(anti)}；"
                  f"证据全集 {len(ev_ids)} 个（J1 引用信号）"))

    # ---- E 多轮连贯：第 1 轮用户陈述真实前提（见 E_TOPICS 注释的坑）
    for i, (premise, planted, referents) in enumerate(E_TOPICS, start=1):
        cases.append(Case(
            case_id=f"E{i}", dimension="coherence", planted=planted,
            coherence_referents=referents,
            premise=premise,
            question=f"（多轮）用户先说：{premise}",
            good_answer=f"你说的那{planted}，我细细想过，确有滋味。",
            bad_answer="（末轮）我记不清从前的事了。",
            extra_bad=(f"资料里记着我那日与你谈起{planted}，此后时常想起。",),
            basis=f"第 1 轮用户真实前提「{premise}」，末轮须仍提到「{planted}」"
                  f"或指称词 {list(referents)}"))

    return cases
