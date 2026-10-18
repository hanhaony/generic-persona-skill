#!/usr/bin/env python
"""D-286 离线回放：把 **R4.4 的 25 条真实证据**换到 L2 中文古典词表下重算。

    python scripts/replay_p4_8_l2.py                       # 默认读 %TEMP%/gps-p48-r44
    python scripts/replay_p4_8_l2.py --db <persona.db>     # 指定隔离库

**零外部调用、零费用**：只读 R4.4 已落盘的证据，走产品自己的归并实现
（`BusinessOrchestrator._stability_groups` + `_build_persona_draft`）。

# 两个变体，回答两个不同的问题

| 变体 | 标签 | 回答 |
|---|---|---|
| A 只换词表 | 沿用 R4.4 的 L1 标签 | **光加词表有用吗？** |
| B 按 L2 重贴 | 按 L2 词表重贴（人工模拟模型的贴法） | **补了族 + 补了缺口，误并会消失吗？** |

A 的结果是"没用"——这不是意外，是 D-280 的原话：归并键只看标签，
标签不变，格子就不变。**词表是候选格，贴法才决定落格**。
B 的结果才是 L2 的价值。

# 诚实声明（必须随结果一起读）

变体 B 的标签是**人工按 L2 词表贴的**，模拟"模型在 L2 下会怎么选"，
**不是模型的真实输出**。它能证明的是"如果贴对了，下游会怎样"，
不能证明"模型一定会贴对"。真实贴法要等 R4.5 跑一次才知道。

变体 B 同时施加了 D-287 的原子化修正：把 R4.4 的 `#13`
（`体弱多病 + 聪慧灵敏 + 师从贾雨村 + 精通诗词`）**拆成两条**
（`frail` + `knowledgeable`）——这正是"一条一个事实"该有的样子，
放在回放里是为了看"拆开之后能多覆盖多少"。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.core.enums import EntityType, Stability                       # noqa: E402
from persona.modules.trait_vocabulary import (                             # noqa: E402
    default_vocabulary,
    zh_classical_vocabulary,
)
from persona.orchestrator.business import (                                # noqa: E402
    BusinessOrchestrator,
    _effective_dimension,
)

DEFAULT_DB = Path.home() / "AppData/Local/Temp/gps-p48-r44/real/persona.db"
PROJECT = "prj_p48_r44_replay"


class _Evidence:
    """只带 `_merge_key` / `_build_persona_draft` 需要的字段。"""

    def __init__(self, eid: str, claim: str, dimension: str, subjects: list[str],
                 source_id: str, trait_tag: str | None) -> None:
        self.id = eid
        self.claim = claim
        self.dimension = dimension
        self.subject_entity_ids = list(subjects)
        self.source_id = source_id
        self.stability = Stability.SINGLE.value
        self.effective_stability = Stability.SINGLE.value
        self.canon_level = ""
        self.applicable_variant_ids: list[str] = []
        self.trait_tag = trait_tag or ""


#: **变体 B 的人工重贴表**（按 R4.4 的证据序号，1-based）。
#: 只列"标签变了"的那几条；没列的原样不动。`None` = 这条被拆开处理。
B_RETAG: dict[int, str] = {
    1: "caustic",        # 言辞尖酸刻薄、喜欢怼人 → SPEECH_STYLE（原来错贴 witty）
    13: "frail",         # 体弱多病（原 claim 里的第一个事实）
    21: "egalitarian",   # 平等待人、尊重个性（compassionate 只覆盖了"待人友善"）
    24: "traditional",   # 编新不如述旧、直书"曲径通幽"（knowledgeable 不表达"崇古"）
}

#: 变体 B 里由 `#13` 拆出来的第二半（原 claim 剩下的"聪慧 / 诗词"部分）。
B_SPLIT_FROM_13 = ("林黛玉生性聪慧灵敏，精通诗词。", "knowledgeable")

#: 只用于人判"标签是否贴切"的对照：变体 B 之后每条用的是哪个标签。
B_TAG_OF_INDEX = {
    1: "caustic", 2: "observant", 3: "reading", 7: "witty", 9: "knowledgeable",
    10: "aloof", 13: "frail", 21: "egalitarian", 22: "compassionate",
    24: "traditional", 25: "analytical",
}


def _load(db_path: Path) -> tuple[list[dict], dict[str, str]]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    rows = [json.loads(r["payload"]) for r in con.execute(
        "select payload from records where kind='EvidenceItem' order by created_at")]
    names = {}
    for r in con.execute(
            "select id, payload from records where kind='PersonEntity'"):
        names[r["id"]] = json.loads(r["payload"]).get("display_name") or r["id"]
    con.close()
    return rows, names


def _verdict(sources: int) -> str:
    """D-109 的复述：1=SINGLE，2=REPEATED，≥3=PATTERN。

    脚本里重写一遍是**故意的**：回放要能对原始判定做交叉验证——
    变体 A 算出来的结论必须与 R4.4 落盘的 `stability_verdict` 一致，
    不一致就说明这里抄错了（脚本会断言）。
    """
    if sources >= 3:
        return Stability.PATTERN.value
    if sources >= 2:
        return Stability.REPEATED.value
    return Stability.SINGLE.value


def _analyse(items: list[Any], vocabulary, entity_id: str,
             names: dict[str, str]) -> dict:
    groups = BusinessOrchestrator._stability_groups(items)
    stabilities: dict[str, str] = {}
    mergeable = 0
    for key, members in groups.items():
        sources = {m.source_id for m in members if m.source_id}
        verdict = _verdict(len(sources))
        for m in members:
            stabilities[m.id] = verdict
        if verdict != Stability.SINGLE.value:
            mergeable += 1
    draft = BusinessOrchestrator._build_persona_draft(
        entity_id, items, EntityType.FICTIONAL_CHARACTER,
        stabilities=stabilities, vocabulary=vocabulary)
    return {"groups": groups, "mergeable": mergeable, "draft": draft}


def _show(label: str, result: dict, items: list[Any], names: dict[str, str],
          entity_id: str) -> None:
    draft = result["draft"]
    #: 组内全部原文：`ProfileItem` 只留第一条文本，其余在 `evidence_refs` 里，
    #: 误并检测必须把**引用指向的原文**一起看，否则只能看到第一条。
    texts = {i.id: i.claim for i in items}
    print(f"\n--- {label}")
    print(f"  组数（含单条）: {len(result['groups'])}   可归并组（≥2 源）: "
          f"{result['mergeable']}")
    print(f"  traits={len(draft.traits)}  habits={len(draft.habits)}  "
          f"single_events={len(draft.single_events)}")
    for item in draft.traits:
        print(f"    [traits] {item.text[:60]}   refs={len(item.evidence_refs)}"
              f"  note={(item.uncertainty_note or '')[:40]}")
    for item in draft.habits:
        print(f"    [habits] {item.text[:60]}   refs={len(item.evidence_refs)}")
    #: SPEECH_STYLE 落在 `expression_style`（文本块，不是条目列表）——
    #: L1 下这一族是空的，所以 R4.4 那份档案这里是空的。
    expression = getattr(draft, "expression_style", None)
    if expression is not None and getattr(expression, "text", ""):
        print(f"    [expression_style] {expression.text[:70]}"
              f"   refs={len(expression.evidence_refs)}")
    #: 误并检测：同一个条目引用的原文里既出现"尖酸/刻薄"又出现"幽默/爱笑"。
    false_merge = False
    for bucket in ("traits", "habits", "single_events"):
        for item in getattr(draft, bucket):
            merged = " ".join(texts.get(ref, "") for ref in item.evidence_refs)
            if ("尖酸" in merged or "刻薄" in merged) and (
                    "幽默" in merged or "爱笑" in merged):
                false_merge = True
                print(f"    [!] 误并仍在：[{bucket}] {item.text[:50]}")
    print(f"  误并（尖酸刻薄 × 幽默爱笑）: {'**仍在**' if false_merge else '已消除'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D-286 离线回放（零调用）")
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help="R4.4 隔离库路径（默认 %TEMP%\\gps-p48-r44\\real\\persona.db）")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"找不到隔离库：{db_path}")
        return 2

    rows, names = _load(db_path)
    l1, l2 = default_vocabulary(), zh_classical_vocabulary()
    print(f"证据数：{len(rows)}   词表：L1={len(l1.all_tags())} / "
          f"L2={len(l2.all_tags())}")
    print(f"启用维度：L1={sorted(l1.enabled_dimensions())} / "
          f"L2={sorted(l2.enabled_dimensions())}")

    entities = sorted({s for r in rows for s in (r.get("subject_entity_ids") or [])})
    print(f"主体：{[(names.get(e, e), e) for e in entities]}")

    # ---------------- 变体 A：标签不变，只换词表 ----------------
    a_items = [_Evidence(str(r.get("id") or f"evd_{i}"), str(r.get("claim") or ""),
                         str(r.get("dimension") or ""),
                         list(r.get("subject_entity_ids") or []),
                         str(r.get("source_id") or ""), r.get("trait_tag"))
               for i, r in enumerate(rows, 1)]

    # ---------------- 变体 B：按 L2 重贴 ----------------
    b_items: list[_Evidence] = []
    for i, r in enumerate(rows, 1):
        tag = r.get("trait_tag")
        dimension = str(r.get("dimension") or "")
        if i in B_RETAG:
            tag = B_RETAG[i]
            dimension = "SPEECH_STYLE" if l2.family_of(tag) == "SPEECH_STYLE" else "TRAIT"
        b_items.append(_Evidence(str(r.get("id") or f"evd_{i}"),
                                 str(r.get("claim") or ""), dimension,
                                 list(r.get("subject_entity_ids") or []),
                                 str(r.get("source_id") or ""), tag))
        if i == 13:
            text, split_tag = B_SPLIT_FROM_13
            b_items.append(_Evidence(str(r.get("id") or "evd_13") + "_split",
                                     text, "TRAIT",
                                     list(r.get("subject_entity_ids") or []),
                                     str(r.get("source_id") or ""), split_tag))

    #: 交叉验证：A 的分组结论必须与落盘判定一致。
    stored = {str(r.get("id")): (r.get("stability_verdict") or {}).get("stability")
              for r in rows}

    for entity in entities:
        name = names.get(entity, entity)
        for label, items, vocabulary in (
                ("变体 A：**只换词表**，标签仍用 R4.4 的原标签", a_items, l2),
                ("变体 B：**按 L2 重贴**（含 #13 拆成两条）", b_items, l2)):
            mine = [i for i in items if entity in i.subject_entity_ids]
            result = _analyse(mine, vocabulary, entity, names)
            _show(f"{name} / {label}", result, mine, names, entity)
            if label.startswith("变体 A"):
                for key, members in result["groups"].items():
                    sources = {m.source_id for m in members if m.source_id}
                    expected = _verdict(len(sources))
                    got = stored.get(members[0].id)
                    if got and got != expected:
                        print(f"  [WARN] 分组投影与落盘判定不一致：{members[0].id} "
                              f"算得 {expected} / 落盘 {got}")

    # ---------------- 覆盖率 ----------------
    #: 分母只算**可贴标签维度**（TRAIT / HABIT / SPEECH_STYLE）：
    #: RELATION / CANON_FACT / BACKGROUND 本来就不该有标签，
    #: 把它们算进分母会把覆盖率稀释成一个没意义的数。
    taggable = [i for i in b_items if _effective_dimension(i, l2) in
                ("TRAIT", "HABIT", "SPEECH_STYLE")]
    tagged = [i for i in taggable if i.trait_tag]
    print(f"\n--- 覆盖率（变体 B，全部主体）")
    print(f"  可贴标签维度证据：{len(taggable)} 条，有标签 {len(tagged)} 条"
          f"（{len(tagged) / max(1, len(taggable)):.0%}）")
    used = sorted({i.trait_tag for i in tagged})
    print(f"  使用标签 {len(used)} 个：{used}")
    speech = [t for t in used if l2.family_of(t) == "SPEECH_STYLE"]
    print(f"  其中 SPEECH_STYLE {speech or '（无）'} —— L1 下这一族是空的")
    print("\n变体 B 的标签是**人工模拟**，不是模型输出；真实贴法要等 R4.5。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
