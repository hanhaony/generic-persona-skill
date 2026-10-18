# -*- coding: utf-8 -*-
"""受控特征标签词表（D-280 / D-283）。

## 它解决什么问题

`evidence._normalize_claim()` 只折空白（注释明写"压缩语义属于语义判断，不做"），
所以**跨来源的同义说法永远归并不了**——只要措辞不同就是两个"说法"，
`occurrences` 恒为 1、`stability` 恒 `SINGLE`，D-012/PF-V2 的闸门把一切
TRAIT 证据挡在 `traits` 之外（D-278）。

本模块把归一化**前移到抽取时**：模型不再输出一段自然语言让下游猜它是什么意思，
而是从**受控词表**里选一个标签。下游的归并交给确定性规则：
**主体集合 + 标签相同 ⇒ 同一组**。模型只做**归纳/分类**，不做**判定**——
它不需要判断"这两条说法是不是同一个意思"。

## 为什么这不违反 AC-7（禁止模型自证）

模型的自证风险在于：让它判断"我抽的这条和我抽的那条是不是一个意思"，
等于让它给自己的产出背书，从而**凭空制造出"重复出现"这个事实**。
而本方案里"算不算重复出现"由**确定性规则**决定（三元组相等），
模型只回答"这条属于哪个标签"。贴错标签的后果是**归并到错误的格子**
（精确率问题），不会制造事实（可靠性问题）。AC-7 约束的是后者。

## 分层

| 层 | 注入方式 | 默认 | 作用 |
|---|---|---|---|
| L1 | 本模块内置 | **启用** | 跨人物可迁移的通用标签 |
| L2 | 配置注入（领域） | **空** | 按人物类型补充；与 L1 同名时**L2 优先** |
| L3 | 配置注入（项目/用户） | **空** | 优先级最高，可覆盖前两层措辞 |

**L2 默认空是有意的**：默认路径只依赖 L1 ⇒ 词表可迁移性可验证。
一旦把"侦探类标签"硬编码进默认路径，"换角跑一遍"就再也测不出迁移性
（那正是方案 C 被否掉的理由）。

## 标签自带"归属族"

每个标签声明它属于 `TRAIT` / `HABIT` / `SPEECH_STYLE` 哪一族。
**归并键与条目板块都由族决定，不看模型给的 `dimension`**——R4.3 实测：
同一个事实在不同来源被抽成了不同维度（小提琴在 baike 是 HABIT、
在大英图书馆是 TRAIT；烟斗在 baike 是 HABIT、在起点是 TRAIT、
在百科"标志性形象"里是 CANON_FACT）。维度不可靠，不能进键，也不该裁决板块。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Iterable, Mapping

#: 标签可以归属的维度族。取值就是 `EvidenceDimension` 的成员名。
FAMILY_TRAIT = "TRAIT"
FAMILY_HABIT = "HABIT"
FAMILY_SPEECH_STYLE = "SPEECH_STYLE"
TRAIT_TAG_FAMILIES: tuple[str, ...] = (
    FAMILY_TRAIT, FAMILY_HABIT, FAMILY_SPEECH_STYLE)

LAYER_GENERIC = "L1"
LAYER_DOMAIN = "L2"
LAYER_CUSTOM = "L3"

#: **应用**顺序：先 L1、再 L2、后 L3 ⇒ 越靠后的层覆盖越靠前的
#: （查询优先级 L3 > L2 > L1）。两个方向的清单别混用：改错方向会让
#: "L2 优先"静默变成"L1 永远赢"，而注入方完全看不出。
_APPLY_ORDER = (LAYER_GENERIC, LAYER_DOMAIN, LAYER_CUSTOM)
_LAYER_OF = {LAYER_GENERIC: "generic", LAYER_DOMAIN: "domain",
             LAYER_CUSTOM: "custom"}


@dataclass(frozen=True)
class TraitTag:
    """一个受控标签：**标签 + 归属族 + 中文说明**。

    中文说明是给**提示词**用的（模型要能在"能不能从文本直接观察"这个层面
    做判断）；`layer` 记它来自哪一层，便于审计"这个标签是谁注入的"。
    """

    tag: str
    family: str
    description: str = ""
    layer: str = LAYER_GENERIC


#: L1 通用层：按"**能直接从文本观察**"设计，不按抽象心理构念。
#: 证据是文本片段，模型要能判断手头这句话支持哪个标签——所以
#: `compassionate`（富有同情心）收、`narcissistic`（自恋型人格障碍）不收。
#:
#: 族归属按 D-283：落进哪个板块由族决定。**习惯族**只收"可枚举的具体行为"
#: （吸烟斗 / 拉小提琴），不收笼统倾向。
L1_TAG_SPEC: tuple[tuple[str, str, str], ...] = (
    # —— 智力 / 能力 ——
    ("analytical", FAMILY_TRAIT, "分析推理"),
    ("observant", FAMILY_TRAIT, "观察入微"),
    ("knowledgeable", FAMILY_TRAIT, "学识广博"),
    ("resourceful", FAMILY_TRAIT, "机智应变"),
    ("memory", FAMILY_TRAIT, "记忆力超群"),
    # —— 意志 / 性格 ——
    ("courageous", FAMILY_TRAIT, "勇敢"),
    ("persistent", FAMILY_TRAIT, "执着坚韧"),
    ("disciplined", FAMILY_TRAIT, "自律"),
    ("impulsive", FAMILY_TRAIT, "冲动"),
    ("stubborn", FAMILY_TRAIT, "固执"),
    ("patient", FAMILY_TRAIT, "耐心"),
    # —— 待人 ——
    ("compassionate", FAMILY_TRAIT, "富有同情心"),
    ("aloof", FAMILY_TRAIT, "疏离冷淡"),
    ("loyal", FAMILY_TRAIT, "忠诚"),
    ("arrogant", FAMILY_TRAIT, "傲慢"),
    ("humble", FAMILY_TRAIT, "谦逊"),
    ("witty", FAMILY_TRAIT, "幽默机智"),
    # —— 情绪 ——
    ("calm", FAMILY_TRAIT, "冷静"),
    ("volatile", FAMILY_TRAIT, "情绪起伏"),
    ("anxious", FAMILY_TRAIT, "焦虑"),
    ("optimistic", FAMILY_TRAIT, "乐观"),
    ("melancholy", FAMILY_TRAIT, "忧郁"),
    # —— 道德 ——
    ("just", FAMILY_TRAIT, "正义感"),
    ("ruthless", FAMILY_TRAIT, "不择手段"),
    ("honest", FAMILY_TRAIT, "诚实"),
    ("deceptive", FAMILY_TRAIT, "欺瞒"),
    # —— 习惯（配 HABIT）：只收可直接观察的具体行为 ——
    ("substance_use", FAMILY_HABIT, "成瘾/嗜好（药物、烟草等）"),
    ("music", FAMILY_HABIT, "音乐（演奏 / 欣赏）"),
    ("reading", FAMILY_HABIT, "阅读"),
    ("exercise", FAMILY_HABIT, "体能锻炼"),
    ("grooming", FAMILY_HABIT, "仪容讲究"),
)


#: **L2 中文古典层（D-286）**：按 R4.4 实测缺口补，只在**显式注入**时启用。
#:
#: 设计依据（`docs/P4_8_R44_REAL_SEARCH.md`）：
#: - `SPEECH_STYLE` 族在 L1 **一个标签都没有**，而黛玉最核心的特征恰恰是
#:   "说话方式"（尖酸刻薄）⇒ 模型只能硬塞 `TRAIT.witty`（定义是"幽默机智"）
#:   ⇒ 与"幽默爱笑"那条误并成同一个 traits 条目。**空族是结构性缺口**，
#:   补它的优先级高于继续往 TRAIT 里加词。
#: - 其余缺口是材料反复强调、L1 却没有对应格子的特征：敏感/多心、叛逆、
#:   痴情、病弱、俊美、崇古、平等待人。
#:
#: 为什么放 L2 而不是塞进 L1：L1 是"跨人物可迁移"的基线，把中文古典语汇
#: 硬编码进去，"换角跑一遍还能不能覆盖"就再也测不出来了（D-280 §分层）。
#: 代价：注入 L2 后候选格从 31 变 42，误贴的表面变大——所以它必须**显式选择**，
#: 不能成为默认路径。
L2_ZH_CLASSICAL_TAG_SPEC: tuple[tuple[str, str, str], ...] = (
    # —— 说话方式：L1 的空族，R4.4 的误并就出在这里 ——
    ("caustic", FAMILY_SPEECH_STYLE, "刻薄/毒舌（言辞尖刻，爱讽刺挖苦）"),
    ("reserved", FAMILY_SPEECH_STYLE, "含蓄（话不说透，留白）"),
    ("incisive", FAMILY_SPEECH_STYLE, "犀利（一语中的，锋芒外露）"),
    ("tactful", FAMILY_SPEECH_STYLE, "委婉（说话留余地，顾全体面）"),
    # —— 情绪 / 情感 ——
    ("sensitive", FAMILY_TRAIT, "敏感/多心（易受触动，爱往心里去）"),
    ("devoted", FAMILY_TRAIT, "痴情/专一（感情上执着不渝）"),
    # —— 价值观 / 性格 ——
    ("rebellious", FAMILY_TRAIT, "叛逆/离经叛道（不服礼法约束）"),
    ("egalitarian", FAMILY_TRAIT, "平等待人（尊重个性，不论尊卑）"),
    ("traditional", FAMILY_TRAIT, "崇古/守旧（推重旧制旧例）"),
    # —— 生理 / 外貌 ——
    ("frail", FAMILY_TRAIT, "病弱/体弱多病"),
    ("attractive", FAMILY_TRAIT, "俊美/风流（外貌出众）"),
)


def _freeze(spec: Mapping[str, TraitTag]) -> Mapping[str, TraitTag]:
    return MappingProxyType(dict(spec))


_L1: Mapping[str, TraitTag] = _freeze({
    tag: TraitTag(tag=tag, family=family, description=desc, layer=LAYER_GENERIC)
    for tag, family, desc in L1_TAG_SPEC
})

#: 启动时的默认词表：**只用 L1**。L2 / L3 必须由配置显式注入，
#: 这样才能验证"默认路径的迁移性"而不会被领域标签掩盖。
DEFAULT_L1_TAGS: Mapping[str, TraitTag] = _L1


def default_vocabulary() -> "TraitVocabulary":
    """默认词表（仅 L1）。"""
    return TraitVocabulary(generic=_L1)


def _l2_zh_classical_tags() -> tuple[TraitTag, ...]:
    return tuple(
        TraitTag(tag=tag, family=family, description=desc, layer=LAYER_DOMAIN)
        for tag, family, desc in L2_ZH_CLASSICAL_TAG_SPEC
    )


def zh_classical_vocabulary() -> "TraitVocabulary":
    """**L1 + L2 中文古典层**（D-286）。

    用途：中文古典文学人物（《红楼梦》这类）。与 `default_vocabulary()`
    的区别只有一处——多 11 个 L2 标签，其中 4 个属于 `SPEECH_STYLE` 族，
    因此该维度**自动**变成"必须贴标签"的维度（`enabled_dimensions` 不写死）。

    **不是默认值**：默认仍是纯 L1，否则"换角还能不能覆盖"测不出来。
    """
    return TraitVocabulary.build(domain=_l2_zh_classical_tags())


#: 按名字取词表——CLI / e2e 脚本用（**唯一一份名字→词表的映射**）。
#: `l1` = 默认基线；`l2-zh-classical` = 中文古典层。名字拼错返回 `None`，
#: 调用方必须显式处理（不要静默回退到默认，那会让"我想用 L2"悄悄失效）。
VOCABULARY_FACTORIES = {
    "l1": default_vocabulary,
    "l2-zh-classical": zh_classical_vocabulary,
}


def vocabulary_by_name(name: str) -> "TraitVocabulary | None":
    """按名字取词表；未知名字返回 `None`（**不静默回退**）。"""
    factory = VOCABULARY_FACTORIES.get(str(name or "").strip().lower())
    return factory() if factory is not None else None


@dataclass(frozen=True)
class TraitVocabulary:
    """三层受控词表。**同名标签按 L3 > L2 > L1 取一层**（胜者全拿，含族归属）。

    空词表（三层都没有标签）⇒ `__bool__` 为假 ⇒ 抽取链路**完全不走 trait_tag**，
    schema 里不出现这个字段、提示词不注入标签、校验不要求它——
    **行为与 trait_tag 上线之前完全一致**（方案 §8 第 3 项）。
    """

    generic: Mapping[str, TraitTag] = field(default_factory=dict)
    domain: Mapping[str, TraitTag] = field(default_factory=dict)
    custom: Mapping[str, TraitTag] = field(default_factory=dict)

    # ------------------------------------------------------------ 构造

    @classmethod
    def build(
        cls,
        *,
        domain: Iterable[TraitTag] = (),
        custom: Iterable[TraitTag] = (),
        generic: Mapping[str, TraitTag] | None = None,
    ) -> "TraitVocabulary":
        """造一个词表。`generic` 缺省用 L1。**

        各层只存**自己声明的那一份**，层间覆盖由 `_resolved()` 统一裁决
        （先 L1、再 L2、后 L3）——不在这里做合并，否则"谁赢"会有第二份实现。
        """
        base: dict[str, TraitTag] = dict(_L1 if generic is None else generic)
        return cls(
            generic=base,
            domain=MappingProxyType({t.tag: t for t in domain}),
            custom=MappingProxyType({t.tag: t for t in custom}),
        )

    # ------------------------------------------------------------ 查询

    def __bool__(self) -> bool:
        return bool(self._resolved())

    def _resolved(self) -> dict[str, TraitTag]:
        """L3 > L2 > L1 的最终合并视图（按"先 L1 后 L3"的应用顺序覆盖）。"""
        out: dict[str, TraitTag] = {}
        for layer in _APPLY_ORDER:
            mapping = getattr(self, _LAYER_OF[layer]) or {}
            out.update(dict(mapping))
        return out

    def resolve(self, tag: str) -> TraitTag | None:
        """按层优先级取一个标签；不在表里返回 `None`（**不是**默认标签）。"""
        return self._resolved().get(str(tag or "").strip())

    def is_known(self, tag: str) -> bool:
        return self.resolve(tag) is not None

    def family_of(self, tag: str) -> str:
        """标签的归属族；未知标签返回 `""`。

        返回 `""` 而不是抛错：**未知标签不应该让归并崩掉**，调用方应把它
        当成没有标签处理（保守回退）。校验层已经会拒收未知标签，
        这里只是防御：`_build_persona_draft` 之类的下游可能收到手工构造的对象。
        """
        spec = self.resolve(tag)
        return spec.family if spec is not None else ""

    def all_tags(self) -> tuple[str, ...]:
        """全部标签（去重、排序）——schema 的 `enum` 与提示词的候选列表用它。"""
        return tuple(sorted(self._resolved()))

    def by_family(self) -> dict[str, list[str]]:
        """按归属族分组的标签列表（提示词按族展示，模型更好选）。"""
        out: dict[str, list[str]] = {f: [] for f in TRAIT_TAG_FAMILIES}
        for tag, spec in sorted(self._resolved().items()):
            out.setdefault(spec.family, []).append(tag)
        return out

    def enabled_dimensions(self) -> frozenset[str]:
        """**有哪些维度必须贴标签**——由词表里实际存在的族决定，不写死。

        L1 只有 TRAIT / HABIT 两族 ⇒ `SPEECH_STYLE` 不是启用维度，
        该维度的 claim 一律 `null`（回退逐字归并，与今天行为一致）。
        将来通过 L2/L3 注入语言风格标签，该维度**自动**变成启用维度，
        不需要改任何代码。
        """
        return frozenset(spec.family for spec in self._resolved().values())

    def fingerprint(self) -> str:
        """词表的**内容指纹**（16 位），进模型调用幂等键。

        为什么必须进键：提示词版本只覆盖"规则文本"，**不覆盖标签清单**。
        同一批材料、同一个提示词，L1 与 L2 的请求意图是不同的（候选格不同），
        不把词表差异写进键 ⇒ 切到 L2 会撞上 L1 那次调用的键、被当成"重复调用"
        静默跳过，复用 L1 的产出。那是"我想换层"的意图被悄悄吃掉（与
        D-283 空词表被 `or` 换掉同型）。

        用内容而不是 `id()`/内存地址：跨进程、跨会话要能对上。
        """
        payload = "|".join(f"{tag}:{spec.family}"
                           for tag, spec in sorted(self._resolved().items()))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    # ------------------------------------------------------------ 提示词

    def prompt_block(self) -> str:
        """注入系统消息的标签清单（含中文说明）。**按族分段**，便于模型选择。"""
        if not self:
            return ""
        groups = self.by_family()
        lines = []
        for family in TRAIT_TAG_FAMILIES:
            tags = groups.get(family) or []
            if not tags:
                continue
            resolved = self._resolved()
            lines.append(f"[{family}]")
            for tag in tags:
                lines.append(f"  - {tag}：{resolved[tag].description}")
        extra = {f: t for f, t in groups.items() if f not in TRAIT_TAG_FAMILIES}
        for family, tags in sorted(extra.items()):
            if not tags:
                continue
            lines.append(f"[{family}]")
            resolved = self._resolved()
            for tag in tags:
                lines.append(f"  - {tag}：{resolved[tag].description}")
        return "\n".join(lines)


def normalize_tags(values: Any) -> list[str]:
    """把任意入参收敛成干净的标签字符串列表（测试 / 配置注入共用）。"""
    out: list[str] = []
    for value in (values or ()):
        if isinstance(value, TraitTag):
            spec = value
        elif isinstance(value, str):
            spec = TraitTag(tag=value, family=FAMILY_TRAIT)
        elif isinstance(value, (tuple, list)) and len(value) >= 2:
            spec = TraitTag(tag=str(value[0]), family=str(value[1]),
                            description=str(value[2]) if len(value) > 2 else "")
        else:
            continue
        if spec.tag and spec.tag not in out:
            out.append(spec.tag)
    return out
