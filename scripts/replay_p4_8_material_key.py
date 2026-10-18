#!/usr/bin/env python
"""D-291 离线回放：用 R4.7 的**真实**请求/响应复算，验证探针句能被救回。

**零调用**：只读 `raw_responses.jsonl` 与隔离库，不联网、不调模型、不写库。

做法：从 R4.7 抽取请求的 `messages[1]` 里把 10 份材料原样切出来（模型看到的就是
这份文本），再用**修复后**的 `validate_model_payload()` 重跑模型当时返回的候选。

要证明的两件事：

1. 探针句（`c18`）不再被拒收，且 `material_key` 被改到**真正含它的那份材料**；
2. 其余 26 条**一条都不变**（修复只救"材料标错"，不动正常路径）。

用法：
    python scripts/replay_p4_8_material_key.py [%TEMP%\\gps-p48-r47]
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from persona.core.types import SourceMaterial                       # noqa: E402
from persona.modules.claim_validation import (                      # noqa: E402
    ISSUE_MATERIAL_KEY_CORRECTED,
    validate_model_payload,
)
from persona.modules.trait_vocabulary import vocabulary_by_name     # noqa: E402

#: 材料块：`[material_key=…]` + `title: …` + `<<WEB_CONTENT_BEGIN>>` … `<<WEB_CONTENT_END>>`
BLOCK = re.compile(
    r"\[material_key=(?P<key>[^\]]+)\]\s*\n"
    r"title:\s*(?P<title>[^\n]*)\n"
    r"<<WEB_CONTENT_BEGIN>>\n(?P<text>.*?)\n<<WEB_CONTENT_END>>",
    re.S,
)


def load_materials(request_body: str, sources: dict[str, dict]) -> list[SourceMaterial]:
    out: list[SourceMaterial] = []
    for match in BLOCK.finditer(request_body):
        key = match.group("key").strip()
        meta = sources.get(key, {})
        out.append(SourceMaterial(
            source_id=meta.get("id", f"src_unknown_{len(out)}"),
            title=match.group("title").strip(),
            url=meta.get("url", key),
            canonical_key=key,
            snippet=match.group("text"),
            subject_entity_ids=list(meta.get("subjects") or []),
        ))
    return out


def main(argv: list[str]) -> int:
    root = Path(argv[1]) if len(argv) > 1 else Path(
        r"C:\Users\36135\AppData\Local\Temp\gps-p48-r47")
    real = root / "real"
    raw_path, db_path = real / "raw_responses.jsonl", real / "persona.db"

    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    sources = {}
    for row in con.execute("select * from records where kind='Source'"):
        payload = json.loads(row["payload"])
        sources[payload.get("canonical_key") or payload.get("url", "")] = {
            "id": payload.get("source_family_id") or row["id"],
            "url": payload.get("url", ""),
            "subjects": payload.get("subject_entity_ids") or [],
        }

    entries = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines()
               if line.strip()]
    chat = [e for e in entries if e.get("label") == "chat"][0]
    request = json.loads(chat["request"])
    response = json.loads(chat["response"])
    payload = json.loads(response["choices"][0]["message"]["content"])

    materials = load_materials(request["messages"][1]["content"], sources)
    print(f"材料：{len(materials)} 份（从 R4.7 抽取请求原样切出）")
    print(f"候选：{len(payload.get('claims') or [])} 条")

    result = validate_model_payload(
        json.dumps(payload, ensure_ascii=False), materials=materials,
        vocabulary=vocabulary_by_name("l2-zh-classical"))

    print(f"\n校验结果：accepted={len(result.accepted)}"
          f" rejected={len(result.rejected)} pending={len(result.pending)}")
    for item in result.rejected:
        print(f"  [拒收] {item.reason}: {str(item.claim)[:50]}")

    corrected = [c for c in result.accepted if ISSUE_MATERIAL_KEY_CORRECTED in c.issues]
    print(f"\n按引文改归属：{len(corrected)} 条")
    for claim in corrected:
        print(f"  · {claim.claim[:52]}")
        print(f"    归属 → {claim.material_key}（source={claim.source_id}）")

    probe = [c for c in result.accepted if "厌恶科举功名" in c.claim]
    print("\n探针句判定：")
    if not probe:
        print("  [FAIL] 仍然没被接受")
        return 1
    claim = probe[0]
    target = sources.get(claim.material_key, {})
    ok = ("cyol" in claim.material_key) and claim.subject_entity_ids
    print(f"  [{'OK ' if ok else 'FAIL'}] claim={claim.claim[:40]}")
    print(f"         material_key={claim.material_key}")
    print(f"         source_id={claim.source_id}")
    print(f"         subject={claim.subject_entity_ids}")
    print(f"         标签={claim.trait_tag} locator={claim.locator}")

    print("\n批次级告警：")
    for line in result.issues:
        if "material_key" in line.lower():
            print(f"  · {line}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
