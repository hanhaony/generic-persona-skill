#!/usr/bin/env python
"""D-289 复核：把 R4.4 / R4.5 / R4.6 的 4 条拒收候选，用**当时的原始响应**重跑一遍核验。

    python scripts/replay_p4_8_quote_rejections.py
    python scripts/replay_p4_8_quote_rejections.py --runs C:/.../gps-p48-r44 C:/.../gps-p48-r45

**零调用**：只读 `raw_responses.jsonl` 与隔离库，不联网、不调模型、不花钱。

它回答一个问题：`QUOTE_NOT_IN_MATERIAL` 拒收的那些引文，是真的不在材料里，
还是被"折空白"实现误杀？

做法：
1. 从原始响应重建当时的材料表（搜索 `content` + 抓取 `raw_content`，取更长的那份）；
2. 从模型输出里取全部候选，减去已登记的证据 ⇒ 当轮被拒收的候选；
3. 用**当前代码**的 `find_quote_span` 复核，并打印命中区间与原文上下文。

输出 `PASS` 表示"修复后应放行"，`FAIL` 表示"仍然找不到（可能是真伪造）"。
脚本以非 0 退出码报告 FAIL，便于当回归用。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.modules.claim_validation import find_quote_span          # noqa: E402

DEFAULT_RUNS = ["gps-p48-r44", "gps-p48-r45", "gps-p48-r46"]


def _load_run(run_dir: Path) -> tuple[dict[str, str], list[dict], set[str]]:
    """返回（材料 url→文本、模型候选、已登记证据的 claim 文本）。"""
    real = run_dir / "real" if (run_dir / "real").exists() else run_dir
    raw = real / "raw_responses.jsonl"
    db = real / "persona.db"
    if not raw.exists() or not db.exists():
        raise FileNotFoundError(f"{run_dir} 里缺 raw_responses.jsonl 或 persona.db")

    materials: dict[str, str] = {}
    claims: list[dict] = []
    for line in raw.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        try:
            payload = json.loads(entry.get("response") or "")
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            text = str(item.get("raw_content") or item.get("content") or "")
            if url and len(text) > len(materials.get(url, "")):
                materials[url] = text
        if entry.get("label") == "chat":
            try:
                content = payload["choices"][0]["message"]["content"]
                data = json.loads(content)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            claims.extend(data.get("claims") or [])

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    registered = {
        json.loads(row["payload"]).get("claim")
        for row in con.execute("select * from records where kind='EvidenceItem'")
    }
    con.close()
    return materials, claims, registered


def _report(run_dir: Path) -> int:
    materials, claims, registered = _load_run(run_dir)
    rejected = [c for c in claims if c.get("claim") not in registered]
    print(f"\n== {run_dir.name}：候选 {len(claims)} / 已登记 {len(registered)}"
          f" / 被拒收 {len(rejected)}")
    failures = 0
    for claim in rejected:
        quote = str(claim.get("verbatim_quote") or "")
        url = str(claim.get("material_key") or "")
        text = materials.get(url, "")
        span = find_quote_span(quote, text) if quote and text else None
        print(f"  - claim: {str(claim.get('claim'))[:60]}")
        print(f"    tag={claim.get('trait_tag')!r}  材料={len(text)} 字  {url[:70]}")
        if span is None:
            failures += 1
            print("    [FAIL] 仍找不到 ⇒ 这条可能真的是伪造/改写引文")
            continue
        start, end = span
        context = text[max(0, start - 12):min(len(text), end + 12)]
        print(f"    [PASS] 命中 [{start}, {end})  原文上下文：{context!r}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="D-289 拒收样本复核（零调用）")
    parser.add_argument("runs", nargs="*", default=None,
                        help="运行目录（缺省用 %%TEMP%% 下的 r44/r45/r46）")
    args = parser.parse_args(argv)

    base = Path(tempfile.gettempdir())
    runs = [Path(p) for p in args.runs] if args.runs else [base / n for n in DEFAULT_RUNS]
    print("D-289 复核：用当前代码的 find_quote_span 重判历史拒收（零调用）")
    total = 0
    for run in runs:
        if not run.exists():
            print(f"\n== {run.name}：目录不存在，跳过")
            continue
        total += _report(run)
    print("\n" + "=" * 72)
    if total:
        print(f"结论：仍有 {total} 条找不到 —— 需要逐条确认是真伪造还是新的匹配缺口")
        return 1
    print("结论：全部命中 —— 历史上的 QUOTE_NOT_IN_MATERIAL 拒收都是折空白的误杀")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
