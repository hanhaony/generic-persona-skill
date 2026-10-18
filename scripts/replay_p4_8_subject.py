r"""P4.8 D-288 离线回放：**映射注入后主体还会不会错**。

## 为什么要有这个脚本

R4.5 的真实产物里 26 条证据有 25 条主体错绑（讲宝玉的挂黛玉 ID、讲黛玉的挂
宝玉 ID），两份档案的 `traits` 互相张冠李戴。修法是把提示词里的裸 ID 列表换成
「名字 = ID」映射（`extraction.py` 的 `_system_prompt`）。

但"改完提示词"本身不能证明"主体不再错"——**要拿真实数据重算一遍**。这个脚本
读 R4.5 的隔离库（`%TEMP%\gps-p48-r45\real\persona.db`），做三件事：

1. **重判主体**：按"claim 句首出现的那个目标名字"重新归属（模拟一个严格照
   提示词映射执行的模型）；
2. **对账**：逐条对比"落库主体"与"按内容重判的主体"，数出纠正了多少条；
3. **重算档案**：用纠正后的主体跑产品的 `_stability_groups` + `_build_persona_draft`，
   给出"如果主体没错，黛玉/宝玉的 traits 应该是什么"。

## 这个脚本**不证明**什么（边界必须写清楚）

- 它用的是**规则匹配**（名字出现在句首），不是模型输出。它证明的是
  "**信息给足了，主体就能被确定**"，不证明"真实模型一定照做"。
- 这一步等价于 R4.4 之后那次"变体 B 按 L2 人工重贴"的回放：证明下游会怎样，
  不证明上游一定这么贴。真实效果要等 R4.6 真跑。

零调用：只读本地 sqlite，不触网、不花一分钱。

用法：
    python scripts/replay_p4_8_subject.py
    python scripts/replay_p4_8_subject.py --db <path> --entity 林黛玉
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
from typing import Any

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from persona.modules.trait_vocabulary import zh_classical_vocabulary   # noqa: E402
from persona.orchestrator.business import BusinessOrchestrator         # noqa: E402

DEFAULT_DB = pathlib.Path.home() / "AppData/Local/Temp/gps-p48-r45/real/persona.db"
_stability_groups = BusinessOrchestrator._stability_groups
_build_persona_draft = BusinessOrchestrator._build_persona_draft


class _Item:
    """`_stability_groups` / `_build_persona_draft` 只读这些属性。"""

    def __init__(self, evidence_id: str, claim: str, dimension: str,
                 subjects: list[str], source_id: str, trait_tag: str) -> None:
        self.id = evidence_id
        self.claim = claim
        self.dimension = dimension
        self.subject_entity_ids = list(subjects)
        self.source_id = source_id
        self.stability = "REPEATED"      # 回放关注归属，不关注稳定性判定
        self.effective_stability = "REPEATED"
        self.canon_level = ""
        self.applicable_variant_ids: list[str] = []
        self.trait_tag = trait_tag


def _load(db: pathlib.Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    names = {r["id"]: json.loads(r["payload"]).get("display_name", "")
             for r in con.execute(
                 "select id, payload from records where kind='PersonEntity'")}
    rows = [json.loads(r["payload"]) for r in con.execute(
        "select payload from records where kind='EvidenceItem' order by created_at")]
    con.close()
    return names, rows


def _name_variants(name: str) -> list[str]:
    """名字的匹配形态：全名 + 去掉姓氏首字的名（"贾宝玉" → "宝玉"）。

    只取全名会漏掉"脂批说，宝玉常常因看到…"这种简称写法（R4.5 的 #16 就是
    这么漏的：落库挂了黛玉，回放判成"无人物"）。这是**启发式**，所以匹配到
    变体时脚本会标注出来，让人能复核。
    """
    out = [name]
    if len(name) >= 3:
        out.append(name[1:])
    return out


def _primary_owner(claim: str, names: dict[str, str],
                   order: list[str]) -> tuple[str | None, bool, bool]:
    """按**句首最早出现**的目标名判主体。

    返回 `(主体 ID, 是否多个目标都被提到, 是否用了简称匹配)`。
    """
    hits: list[tuple[int, str, bool]] = []
    for entity_id, name in names.items():
        if not name:
            continue
        for index, variant in enumerate(_name_variants(name)):
            at = claim.find(variant)
            if at >= 0:
                hits.append((at, entity_id, index > 0))
    if not hits:
        return None, False, False
    hits.sort()
    #: 同一位置命中多个目标（变体冲突）时按全名优先。
    at, entity_id, via_alias = hits[0]
    mentioned = len({h[1] for h in hits}) > 1
    return entity_id, mentioned, via_alias


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R4.5 主体归属离线回放（零调用）")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--entity", default="", help="只看某个人（默认全部）")
    args = parser.parse_args(argv)

    db = pathlib.Path(args.db)
    if not db.exists():
        print(f"找不到隔离库：{db}")
        print("先跑 R4.5，或用 --db 指定路径")
        return 2

    names, rows = _load(db)
    names = {k: v for k, v in names.items() if v}
    order = list(names)
    if args.entity:
        names = {k: v for k, v in names.items() if v == args.entity}
        if not names:
            print(f"库中没有实体「{args.entity}」；可选：{list(order)}")
            return 2

    print(f"库：{db}")
    print(f"实体：{ {v: k for k, v in names.items()} }")
    print(f"证据：{len(rows)} 条\n")

    corrected: list[_Item] = []
    changed = unresolved = 0
    print("=" * 78)
    print("逐条：落库主体 → 按内容重判")
    print("=" * 78)

    for i, row in enumerate(rows, 1):
        claim = str(row.get("claim") or "")
        stored = list(row.get("subject_entity_ids") or [])
        owner, multi, via_alias = _primary_owner(claim, names, order)
        dimension = str(row.get("dimension") or "")
        #: RELATION 且两个名字都在 ⇒ 双主体（"宝玉与黛玉青梅竹马"）。
        if dimension == "RELATION" and multi:
            owner_ids = [eid for eid, nm in names.items() if nm in claim]
        elif owner:
            owner_ids = [owner]
        else:
            owner_ids = stored          # 无人物：保持原样，单独计数
            unresolved += 1

        mark = "  " if sorted(owner_ids) == sorted(stored) else "→ "
        if mark == "→ ":
            changed += 1
        label = {v: k for k, v in names.items()}
        fmt = lambda ids: [label.get(x, x[-6:]) for x in ids]
        alias_note = "  （简称匹配）" if via_alias else ""
        print(f"{i:2d}. {dimension:12s} 落库={str(fmt(stored)):16s} "
              f"{mark}重判={fmt(owner_ids)}{alias_note}")
        print(f"    {claim[:64]}")

        corrected.append(_Item(str(row.get("id") or f"evd_{i}"), claim, dimension,
                              owner_ids, str(row.get("source_id") or ""),
                              str(row.get("trait_tag") or "")))

    print("\n" + "=" * 78)
    print(f"纠正：{changed}/{len(rows)} 条主体变了；无人物无法判定 {unresolved} 条")
    print("=" * 78)

    # ---------------- 重算档案 ----------------
    vocabulary = zh_classical_vocabulary()
    print("\n纠正后的档案（稳定性按 D-109 规则实算：1=SINGLE / ≥2 源=REPEATED / ≥3 源=PATTERN）：")
    for entity_id, name in names.items():
        items = [i for i in corrected if entity_id in i.subject_entity_ids]
        #: **不能一律给 REPEATED**：那会让所有标签都进 traits，回放结果虚高。
        #: 用产品的分组 + D-109 规则实算，才是"纠正主体后 R4.6 会看到什么"。
        stabilities: dict[str, str] = {}
        for key, members in _stability_groups(items).items():
            sources = {m.source_id for m in members if m.source_id}
            verdict = ("PATTERN" if len(sources) >= 3
                       else "REPEATED" if len(sources) >= 2 else "SINGLE")
            for member in members:
                stabilities[member.id] = verdict
        draft = _build_persona_draft(entity_id, items, "FICTIONAL_CHARACTER",
                                     stabilities=stabilities,
                                     vocabulary=vocabulary)
        print(f"\n-- {name}（{len(items)} 条证据）")
        for block in ("traits", "habits", "single_events"):
            entries = list(getattr(draft, block) or [])
            if not entries:
                continue
            print(f"   [{block}] {len(entries)} 条")
            for entry in entries:
                print(f"     · {str(getattr(entry, 'text', entry))[:60]}")
        #: `background` / `expression_style` 是 `ProfileSection`（单块文本），
        #: 不是 list——与上面三个条目型板块形状不同。
        for block in ("background", "expression_style"):
            section = getattr(draft, block, None)
            text = str(getattr(section, "text", "") or "")
            if text:
                print(f"   [{block}] {text[:60]}")

    # ---------------- 落库档案对照 ----------------
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    print("\n" + "=" * 78)
    print("落库档案（R4.5 实际写进去的）")
    print("=" * 78)
    label = {v: k for k, v in names.items()}
    for r in con.execute("select payload from records where kind='PersonaProfile'"):
        p = json.loads(r["payload"])
        who = label.get(p.get("entity_id"), p.get("entity_id"))
        if args.entity and who != args.entity:
            continue
        print(f"\n-- {who}")
        for block in ("traits", "habits", "single_events"):
            for entry in p.get(block) or []:
                text = entry.get("text") if isinstance(entry, dict) else entry
                print(f"   [{block}] {str(text)[:64]}")
        #: `background` / `expression_style` 在落库 payload 里是**嵌套对象**
        #: （`ProfileSection` 序列化后的 dict），不是条目数组。
        for block in ("background", "expression_style"):
            section = p.get(block)
            text = ""
            if isinstance(section, dict):
                text = str(section.get("text") or "")
            elif isinstance(section, str):
                text = section
            if text:
                print(f"   [{block}] {text[:64]}")
    con.close()

    print("\n注意：本回放用**规则匹配**（句首人名）判主体，不是模型输出。")
    print("它证明「信息给足了主体就能确定」，不证明「真实模型一定照做」。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
