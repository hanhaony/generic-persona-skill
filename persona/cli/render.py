"""渲染层（P5.1）：把只读报告变**人能读**的文本 / Markdown。

**纯函数**：只吃 dataclass、只吐字符串，不读库、不联网、不看时钟（时间由调用方
传进来）。这样每一条格式规则都能用一句话断言锁住，不需要起数据库。

三条贯穿全文件的规则：

1. **空不等于没有**：空分区必须打印 `（无）` + 一句原因。省略会让用户以为
   "这个人没有习惯"，而事实是"我们没查到"——这是两种完全不同的事。
2. **数得清才敢写**：`N 个来源` 全部来自去重后的 `source_id`；`M 条证据` 是条目
   `evidence_refs` 里真实存在的条数。两者**不同**（黛玉的"说话方式"是 1 个来源、
   2 条证据），混写就是把证据数说成来源数。
3. **不展示 confidence**：真实数据里恒 `0.0`，展示出来是假精度；也不展示 `evd_` /
   `pe_` 内部 ID（除同名歧义这种必须指明"看哪一份"的场合）。
"""

from __future__ import annotations

import re
from typing import Iterable

from ..orchestrator.readmodels import (
    EvidenceReport,
    EvidenceRow,
    ItemView,
    PersonaReport,
    RelationshipReport,
    SectionView,
)

#: 状态码 → 人话（未列出的原样输出，不猜）
STATUS_TEXT = {
    "OK": "OK",
    "INSUFFICIENT_EVIDENCE": "证据不足",
    "PARTIAL": "部分",
}

#: 非 OK 状态要在标题下补一句"为什么"（否则用户只看到一个形容词）
STATUS_NOTE = {
    "INSUFFICIENT_EVIDENCE": "证据不足：没有任何特征达到 2 个独立来源",
    "PARTIAL": "部分：有的板块证据够、有的不够（下面标（无）的就是不够的）",
}

_ENTITY_ID = re.compile(r"pe_[0-9a-f]{6,}", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def shorten_id(entity_id: str) -> str:
    """`pe_67e444bc…` → `pe_67e4…`；`evd_7704ab…` → `evd_7704…`。

    前缀不同、长度不同，按 `_` 切一刀统一处理：保留前缀 + 4 位，够认，又不撑行。
    """
    text = str(entity_id or "")
    prefix, sep, rest = text.partition("_")
    if not sep or len(rest) <= 4:
        return text
    return f"{prefix}_{rest[:4]}…"


def _width(text: str) -> int:
    """显示宽度：CJK / 全角算 2，其余算 1（对齐用，不追求字体级精确）。"""
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in str(text))


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _date(value: str) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text


def _localize(text: str, names: dict[str, str]) -> tuple[str, list[str]]:
    """把正文里的裸实体 ID 换成名字。返回 `(替换后的文本, 没解析出来的 ID)`。

    真实数据里 `asymmetry_notes` 的正文就长这样：
    `覆盖对 ['pe_67e4…', 'pe_a312…'] 存在 2 种不同说法`。直接把内部 ID 打给用户
    等于让他去查库——只换字面，不改任何结论。**不加引号**：原文通常已经在
    列表里带引号了，再加一层会变成 `['「林黛玉」']`。

    解析不到的 ID **原样保留**（不截断、不猜测），由调用方在行尾标
    `⚠️ 未知实体`——"这串乱码是谁"和"我们没解析出来"是两件事。
    """
    missing: list[str] = []

    def replace(match: re.Match) -> str:
        entity_id = match.group(0)
        name = names.get(entity_id)
        if name:
            return name
        if entity_id not in missing:
            missing.append(entity_id)
        return entity_id

    return _ENTITY_ID.sub(replace, str(text or "")), missing


def _sources_phrase(item: ItemView) -> str:
    return f"{item.source_count} 个来源"


def _detail(item: ItemView, *, single_event: bool) -> str:
    if single_event or item.stability.upper() in ("SINGLE", "UNKNOWN"):
        return f"{_sources_phrase(item)} · 非稳定特征"
    if item.evidence_count > 1:
        return f"{_sources_phrase(item)}支持 · 归并自 {item.evidence_count} 条措辞各异的说法"
    return f"{_sources_phrase(item)}支持"


def _dimension_phrase(report: PersonaReport) -> str:
    counts = report.unmerged.by_dimension
    if not counts:
        return ""
    parts = [f"{name} {count} 条" for name, count in counts[:2]]
    if len(counts) > 2:
        parts.append("等")
    return "、".join(parts)


def unmerged_line(report: PersonaReport) -> str:
    """`（另有 N 条证据未并入档案：…，不用于性格推断；明细见 …）`；为空返回空串。"""
    if report.unmerged.total <= 0:
        return ""
    dims = _dimension_phrase(report)
    tail = f"：{dims}，不用于性格推断" if dims else "，不用于性格推断"
    return (f"（另有 {report.unmerged.total} 条证据未并入档案{tail}；"
            f"明细见 persona evidence {report.entity.display_name}）")


def _same_name_lines(report: PersonaReport) -> list[str]:
    others = report.other_entities
    if not others:
        return []
    total = len(others) + 1
    if report.chosen_is_latest:
        first = f"检测到 {total} 个同名实体，已显示最新（{shorten_id(report.entity.entity_id)}）"
    else:
        first = (f"检测到 {total} 个同名实体，已显示最近一份有档案的"
                 f"（{shorten_id(report.entity.entity_id)}）")
    listed = "、".join(shorten_id(e.entity_id) for e in others[:3])
    if len(others) > 3:
        listed += " 等"
    lines = [f"{first}，其余：{listed}。",
             f"用 persona show {shorten_id(others[0].entity_id)} 查看另一份。"]
    if len(others) > 1:
        lines[-1] += "（其余用同一写法换 ID）"
    return lines


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _section_lines(section: SectionView) -> list[str]:
    # 文本型板块有内容时只打标题（它就是一段话）；空的时候要带 `（0）`，
    # 否则同一份输出里"背景"没有数字、"习惯（0）"有数字，读者会以为是两回事。
    if section.is_text and section.items:
        head = section.title
    else:
        head = f"{section.title}（{len(section.items)}）"
    if not section.items:
        return [head, f'  （无）本库没有能支撑"{section.title}"的证据']
    lines = [head]

    if section.is_text:
        for item in section.items:
            lines.append(f"  · {item.text}")
            lines.append(f"    {_sources_phrase(item)}支持（{item.evidence_count} 条证据）")
        return lines

    single_event = section.key == "single_events"
    details = [_detail(item, single_event=single_event) for item in section.items]
    inline = single_event and max(
        (_width(f"  · {item.text}") for item in section.items), default=0) <= 56
    if inline:
        width = max(_width(f"  · {item.text}") for item in section.items)
        for item, detail in zip(section.items, details):
            lines.append(f"{_pad(f'  · {item.text}', width + 4)}{detail}")
        return lines

    for item, detail in zip(section.items, details):
        lines.append(f"  · {item.text}")
        lines.append(f"    {detail}")
    return lines


def render_persona(report: PersonaReport) -> str:
    """`persona show` 的正文（不含结束换行）。"""
    entity = report.entity
    lines = [f"{entity.display_name}  （{entity.entity_id}）"]

    work = "、".join(entity.works) if entity.works else ""
    if entity.adaptation_version:
        lines.append(f"{'《' + work + '》' if work else '（未登记作品）'} · "
                     f"改编版本：{entity.adaptation_version}")
    elif work:
        lines.append(f"《{work}》")

    if report.profile_missing:
        lines.append("没有档案：本库有这个名字的实体，但还没有生成人格档案")
    else:
        lines.append(f"档案 v{report.profile_version} · 状态 "
                     f"{STATUS_TEXT.get(report.status, report.status)} · "
                     f"基于 {report.referenced_evidence} 条证据 / "
                     f"{report.referenced_sources} 个来源")
        note = STATUS_NOTE.get(report.status)
        if note:
            lines.append(note)
    lines.append("")

    if report.profile_missing:
        lines.append("先跑一次研究：")
        lines.append(f'  persona run --names "{entity.display_name}" --work-title <作品>')
        return "\n".join(lines)

    for section in report.sections:
        lines.extend(_section_lines(section))
        lines.append("")

    line = unmerged_line(report)
    if line:
        lines.append(line)
        lines.append("")

    if report.missing_refs:
        lines.append(f"⚠️ 有 {len(report.missing_refs)} 条被档案引用的证据不在本库"
                     f"（{', '.join(shorten_id(r) for r in report.missing_refs[:3])}）")
        lines.append("")

    lines.append('说明：confidence 字段未校准（真实数据恒 0.0），故不展示为"可信度"；')
    lines.append('      "N 个来源"是去重后的独立来源数，不是证据条数；')
    shown = entity.adaptation_version or "原著"
    lines.append(f"      「改编版本：{shown}」可能来自显式指定、也可能是省略后的默认值"
                 "——实体里只存结果值，")
    lines.append("      所以这里无法区分；persona run 的清单会明确标注本次是哪一种。")

    same = _same_name_lines(report)
    if same:
        lines.append("")
        lines.extend(same)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# show-relationship
# ---------------------------------------------------------------------------


def _item_lines(items: list[ItemView], names: dict[str, str], *,
                indent: str = "  ", mark_stability: bool = False) -> list[str]:
    """关系条目行。

    `mark_stability` 只在"某人怎么看某人"里打开：那是**特征**，1 个来源就该
    标"非稳定特征"；而"共同经历 / 情感表达习惯"是**事件与行为描述**，给它们
    挂"非稳定特征"读起来像在说这俩人感情不稳定。
    """
    if not items:
        return [f"{indent}（无）"]
    lines: list[str] = []
    for item in items:
        text, missing = _localize(item.text, names)
        detail = f"{item.source_count} 个来源"
        if mark_stability and item.stability.upper() in ("SINGLE", "UNKNOWN"):
            detail += " · 非稳定特征"
        if missing:
            detail += " · ⚠️ 未知实体"
        lines.append(f"{indent}· {text}    {detail}")
    return lines


def render_relationship(report: RelationshipReport) -> str:
    """`persona show-relationship` 的正文。"""
    names = {p.entity_id: p.display_name for p in report.participants}
    people = " ↔ ".join(p.display_name for p in report.participants) or "（无参与者）"
    lines = [f"{people}  （{report.cardinality} · 档案 v{report.version} · "
             f"状态 {STATUS_TEXT.get(report.status, report.status)}）", ""]

    for perspective in report.perspectives:
        lines.append(f"{perspective.from_name} 眼中的 {perspective.to_name}"
                     f"（{len(perspective.items)}）")
        lines.extend(_item_lines(perspective.items, names, mark_stability=True))
        lines.append("")

    groups = (
        ("共同经历", report.shared_experiences),
        ("互动模式", report.interaction_patterns),
        ("情感表达习惯", report.emotional_expression_habits),
        ("不对称说明", report.asymmetry_notes),
    )
    for title, items in groups:
        lines.append(f"{title}（{len(items)}）")
        lines.extend(_item_lines(items, names))
        lines.append("")

    if report.group_dynamics:
        lines.append("群体互动")
        for label, items in report.group_dynamics.items():
            lines.append(f"  {label}（{len(items)}）")
            lines.extend(_item_lines(items, names, indent="    "))
        lines.append("")

    if report.missing_refs:
        lines.append(f"⚠️ 有 {len(report.missing_refs)} 条被关系档案引用的证据不在本库")
        lines.append("")

    lines.append("说明：关系条目全部为 SYSTEM_DERIVED（由已有证据推导），不是"
                 '"当事人自述"；')
    lines.append('      "非稳定特征"= 只有 1 个来源提到；"N 个来源"是去重后的'
                 "独立来源数，不是证据条数。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------


def _evidence_detail(group: Any) -> str:
    """`2 个来源` / `1 个来源（2 条证据）`——来源数与证据数不同时必须都说出来。"""
    text = f"{group.source_count} 个来源"
    if group.evidence_count != group.source_count:
        text += f"（{group.evidence_count} 条证据）"
    return text


def _evidence_block(rows: list[EvidenceRow], indent: str = "  ") -> list[str]:
    """一组证据的 `├ / └` 树（最后一行为 `└`，其余为 `├`）。

    `indent` 是分支符的起始缩进；明细行再往右两格（引用与来源属于这一条证据）。
    """
    lines: list[str] = []
    pad = " " * len(indent)
    for index, row in enumerate(rows):
        last = index == len(rows) - 1
        branch = "└" if last else "├"
        # 明细行与「分支符之后的文字」对齐：非最后一条用 `│`，最后一条用空格。
        detail = pad + ("    " if last else "│   ")
        tag = f" / {row.trait_tag}" if row.trait_tag else ""
        flags = f"   ⚠️ {', '.join(row.flags)}" if row.flags else ""
        lines.append(f"{indent}{branch} {shorten_id(row.evidence_id)}  {row.tier}"
                     f"  [{row.dimension}{tag}]{flags}")
        if row.quote:
            lines.append(f'{detail}"{row.quote}"')
        title = f"《{row.source_title}》" if row.source_title else "（来源信息缺失）"
        url = f" {row.source_url}" if row.source_url else ""
        lines.append(f"{detail}{title}{url}")
        extras = []
        if row.locator:
            extras.append(f"定位 {row.locator}")
        if row.retrieved_at:
            extras.append(f"抓取于 {_date(row.retrieved_at)}")
        if extras:
            lines.append(detail + " · ".join(extras))
    return lines


def _unmerged_title(report: EvidenceReport) -> str:
    """`未并入` 小节标题 —— **永远有这一行**（0 条也要有）。

    省略它的后果很具体：用户会把 `evidence` 的输出当成"这个人的全部证据"，
    而"全都进了档案"和"我们没查别的"是两件事，必须能分开读。
    """
    if report.unmerged:
        return (f"■ 未并入档案的证据（{len(report.unmerged)} 条，"
                "均为背景事实/关系陈述，未构成性格特征）")
    if report.profile_missing:
        return "■ 未并入档案的证据（0 条，本库还没有针对该人物的人格档案）"
    if report.trait_filter:
        return "■ 未并入档案的证据（0 条，本次筛选下没有未并入的证据）"
    return "■ 未并入档案的证据（0 条，全部证据都已并入上面的条目）"


def render_evidence(report: EvidenceReport) -> str:
    """`persona evidence` 的正文。"""
    name = report.entity.display_name
    lines = [f"{name} 的证据（{report.evidence_total} 条证据 / "
             f"{report.source_total} 个来源）", ""]

    if report.trait_filter and not report.groups and not report.unmerged:
        lines.append(f"没有 trait_tag={report.trait_filter} 的证据。"
                     f"「{name}」当前可用标签：")
        if report.available_tags:
            listed = "  ".join(f"{tag}({count})" for tag, count in report.available_tags[:8])
            if len(report.available_tags) > 8:
                listed += " …"
            lines.append(f"  {listed}")
        else:
            lines.append("  （没有任何证据带 trait_tag）")
        lines.append("说明：词表决定可用标签；本次档案用的是运行时选择的词表。")
        return "\n".join(lines)

    if report.trait_filter:
        hits = sum(group.evidence_count for group in report.groups) + len(report.unmerged)
        lines.append(f"筛选：trait_tag={report.trait_filter}（命中 {hits} 条）")
        lines.append("")

    current = ""
    for group in report.groups:
        if group.section and group.section != current:
            current = group.section
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"■ {current}")
        lines.append(f"  · {group.title}    {_evidence_detail(group)}")
        lines.extend(_evidence_block(group.items, indent="      "))
    if report.groups:
        lines.append("")

    lines.append(_unmerged_title(report))
    for row in report.unmerged:
        tag = f"  [{row.dimension}]" if row.dimension else ""
        title = f"  《{row.source_title}》" if row.source_title else ""
        sources = "1 个来源" if row.source_id else "来源缺失"
        lines.append(f"  · {row.claim}    {sources}{tag}{title}")
    lines.append("")

    if report.profile_missing:
        lines.append("（本库还没有「" + name + "」的人格档案：以上证据全部未并入）")
        lines.append("")

    if report.missing_refs:
        lines.append(f"⚠️ 有 {len(report.missing_refs)} 条被档案引用的证据不在本库")
        lines.append("")

    lines.append('说明：证据只证明"某来源这么说过"，不证明"这是事实"；')
    lines.append("      标 ⚠️ 的条目在校验阶段有过告警（标签与维度不匹配）。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# export --format md
# ---------------------------------------------------------------------------


def render_markdown(report: PersonaReport, *, generated_on: str = "") -> str:
    """`persona export --format md`：**可直接贴进文档**的版本。

    刻意不要的东西：表格（贴进 Word 会散）、`evd_` ID（贴进文档没意义）、
    `confidence`（假精度）。要的东西只有：结论 + 几个来源支持。
    """
    entity = report.entity
    lines = [f"## {entity.display_name}", ""]

    if report.profile_missing:
        lines.append(f"**证据不足：本库没有为「{entity.display_name}」生成任何特征。**")
        lines.append("")
        lines.append(f"原因：库里已有 {report.evidence_total} 条关于她的证据，"
                     "但还没有生成人格档案。")
        return "\n".join(lines)

    meta = [f"档案 v{report.profile_version} · 状态 "
            f"{STATUS_TEXT.get(report.status, report.status)} · "
            f"基于 {report.referenced_evidence} 条证据 / {report.referenced_sources} 个来源"]
    if generated_on:
        meta.append(f"生成于 {generated_on}")
    work = "、".join(entity.works)
    if work or entity.adaptation_version:
        lines.append(f"> 《{work}》 · 改编版本：{entity.adaptation_version}")
    lines.append("> " + " · ".join(meta))
    lines.append('> 来源数为去重后的独立来源数；"非稳定特征"= 只有 1 个来源提到。')
    lines.append("")

    empty = [s for s in report.sections if not s.items]
    if empty and report.referenced_evidence == 0:
        lines.append(f"**证据不足：本库没有为「{entity.display_name}」生成任何特征。**")
        lines.append("")
        lines.append(f"原因：{report.evidence_total} 条证据里没有一条达到"
                     '"稳定特征"门槛（需 ≥2 个独立来源）。')
        if report.unmerged.total:
            lines.append(f"     另有 {report.unmerged.total} 条证据未并入档案"
                         f"（{_dimension_phrase(report)}），不作为性格下结论。")
        return "\n".join(lines)

    for section in report.sections:
        lines.append(f"### {section.title}")
        lines.append("")
        if not section.items:
            lines.append(f'（本库没有能支撑"{section.title}"的证据）')
            lines.append("")
            continue
        for item in section.items:
            if section.key == "single_events":
                lines.append(f"- {item.text}（{item.source_count} 个来源，非稳定特征）")
            else:
                lines.append(f"- **{item.text}**（{item.source_count} 个来源支持）")
        lines.append("")

    if report.unmerged.total:
        lines.append(f"> 另有 {report.unmerged.total} 条证据未并入档案"
                     f"（{_dimension_phrase(report)}），不用于性格推断。")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_lines(text: str) -> Iterable[str]:
    """便捷：按行迭代（CLI 打印 / 写文件共用）。"""
    return text.splitlines()
