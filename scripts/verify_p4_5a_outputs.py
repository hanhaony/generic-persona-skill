"""P4.5-A 运行产物**离线独立复算器**（不联网、不重跑、不读密钥）。

    python scripts/verify_p4_5a_outputs.py --dump var/p45a_last_run.json

# 为什么独立复算

抽取器与校验器（`persona.modules.claim_validation`）是**被测对象**。
如果复算器 import 它们，就变成"用被测代码验证被测代码"——摘录可追溯性会
永远自证成立。所以本脚本：

- **不 import** `persona.modules.*`：完全不依赖被测实现；
- 自己按 `locator.start/end` 切片、自己用 `str.find` 复核；
- 自己按配置单价复算费用。

覆盖 P4.5-A 设计稿的 A3 / A4 / A5 / A6 / A7 / A8 / A12（+ 一条密钥扫描）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: 这些 stability 是"单次 / 未知"，按 AC-7 / PF-V2 / D-012 **不得**进 `traits`
NON_TRAIT_STABILITIES = {"SINGLE", "UNKNOWN", "Stability.SINGLE", "Stability.UNKNOWN"}

#: 出现了这些"可信度提升"字段名且值为真 → A12 失败（格式正确 ≠ 事实可信）
FORBIDDEN_TRUTHY_KEYS = {
    "verified", "is_verified", "approved", "is_approved", "fact_checked",
    "evidence_id", "evidence_ids",
}

SECRET_SHAPE = re.compile(r"sk-[A-Za-z0-9]{16,}")


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append((name, bool(ok), detail))

    def summary(self) -> tuple[int, int]:
        passed = sum(1 for _, ok, _ in self.rows if ok)
        return passed, len(self.rows)


def _norm_ws(text: str) -> str:
    """折空白（独立实现，只用标准库）。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def verify(artifact: dict) -> Report:
    rep = Report()
    cfg = artifact.get("config", {})
    materials = artifact.get("materials", [])
    extraction = artifact.get("extraction", {})
    claims = extraction.get("claims", [])
    evidence_ids = set(artifact.get("evidence_ids", []))
    sources = set(artifact.get("sources", []))
    persona = artifact.get("persona", {})
    ledger = artifact.get("ledger", {})
    calls = artifact.get("calls", {})

    by_key = {str(m.get("canonical_key") or m.get("url")): m for m in materials}
    by_source = {str(m.get("source_id")): m for m in materials}

    # ---------- V0：材料侧 ----------
    rep.check("V0.1 至少 1 份材料", len(materials) >= 1, f"materials={len(materials)}")
    rep.check("V0.2 材料门禁结论全为 VALID",
              all(m.get("quality_status") == "VALID" for m in materials),
              f"status={[m.get('quality_status') for m in materials]}")
    rep.check("V0.3 材料已绑定已登记来源 ID",
              all(m.get("source_id") and m["source_id"] in sources for m in materials),
              f"sources={sorted(sources)}")

    # ---------- A3：可追溯 ----------
    bad_src = [c.get("key") for c in claims
               if not c.get("source_id") or c["source_id"] not in sources]
    rep.check("A3 每条候选的 source_id 都在本次来源内", not bad_src,
              f"越界候选={bad_src}")

    # ---------- A4：摘录可追溯（独立复算 locator） ----------
    quote_problems: list[str] = []
    for c in claims:
        tier = str(c.get("tier") or "")
        quote = c.get("verbatim_quote")
        loc = c.get("locator") or {}
        key = c.get("key")
        if "VERBATIM" in tier:
            if not quote:
                quote_problems.append(f"{key}: VERBATIM 缺摘录")
                continue
            material = by_source.get(str(c.get("source_id")))
            if material is None:
                quote_problems.append(f"{key}: 找不到对应材料")
                continue
            snippet = str(material.get("snippet") or "")
            # ① 独立用 str.find 复核（不信任 locator）
            pos = snippet.find(str(quote))
            if pos < 0 and _norm_ws(quote) and _norm_ws(quote) in _norm_ws(snippet):
                pos = -2  # 折空白后命中：可接受，但 locator 无法逐字复核
            if pos < 0:
                quote_problems.append(f"{key}: 摘录不在材料中")
                continue
            # ② 按 locator 切片复核
            start, end = loc.get("start"), loc.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                quote_problems.append(f"{key}: locator 不是 {start,end} 形式")
                continue
            sliced = snippet[start:end]
            if sliced != str(quote):
                quote_problems.append(
                    f"{key}: locator 切片 != 摘录（{sliced[:20]!r} vs {str(quote)[:20]!r}）")
                continue
            if loc.get("field") != "snippet":
                quote_problems.append(f"{key}: locator.field != snippet")
        else:
            if quote:
                quote_problems.append(f"{key}: 非 VERBATIM 却带摘录")
    rep.check("A4 逐字摘录可按 locator 独立复算命中", not quote_problems,
              f"问题={quote_problems}")

    # ---------- A4b：tier 语义 ----------
    inf_no_derivation = [c.get("key") for c in claims
                         if "INFERENCE" in str(c.get("tier") or "")
                         and not c.get("derivation_keys")]
    rep.check("A4b INFERENCE 候选必须带 derivation_keys", not inf_no_derivation,
              f"缺根推断={inf_no_derivation}")

    # ---------- A6：门禁语义 ----------
    rep.check("A6.1 整批 verdict 可见", bool(extraction.get("verdict")),
              f"verdict={extraction.get('verdict')}")
    rep.check("A6.2 配置币种 = USD", cfg.get("currency") == "USD",
              f"currency={cfg.get('currency')}")
    rep.check("A6.3 require_gate_verdict = True", cfg.get("require_gate_verdict") is True,
              f"={cfg.get('require_gate_verdict')}")

    # ---------- A5 / A6.4：账本与费用复算 ----------
    records = ledger.get("records", [])
    chat_records = [r for r in records if r.get("operation") == "CHAT"]
    rep.check("A5.1 账本恰好 1 条 CHAT 记录", len(chat_records) == 1,
              f"records={[(r.get('operation'), r.get('outcome')) for r in records]}")
    if chat_records:
        rec = chat_records[0]
        p_in = cfg.get("price_per_1k_in")
        p_out = cfg.get("price_per_1k_out")
        # token 取 **Provider 响应里报的**那份（与账本/抽取器那条链路相互独立），
        # 这样 A5.2 是跨组件交叉核对，而不是同源自证。
        resp_usage = extraction.get("response_usage") or {}
        tin = resp_usage.get("tokens_in")
        tout = resp_usage.get("tokens_out")
        recomputed = None
        if None not in (p_in, p_out, tin, tout):
            recomputed = float(tin) / 1000.0 * float(p_in) + float(tout) / 1000.0 * float(p_out)
        confirmed = rec.get("cost_confirmed")
        if confirmed is None:
            rep.check("A5.2 费用可复算（CONFIRMED）",
                      str(rec.get("cost_status")) == "UNKNOWN",
                      f"cost_confirmed=None → 必须标 UNKNOWN，实际 {rec.get('cost_status')}")
        else:
            ok = recomputed is not None and abs(float(confirmed) - recomputed) < 1e-9
            rep.check("A5.2 费用可复算（响应 token × 配置单价 == cost_confirmed）", ok,
                      f"记录={confirmed} 复算={recomputed} tokens={tin}/{tout}")
        rep.check("A5.3 cost_source = OFFICIAL_RATE",
                  str(rec.get("cost_source")) == "OFFICIAL_RATE",
                  f"={rec.get('cost_source')}")
        rep.check("A5.4 cost_status = CONFIRMED",
                  str(rec.get("cost_status")) == "CONFIRMED",
                  f"={rec.get('cost_status')}")
        rep.check("A5.5 服务端 request_id 非空（可对账）", bool(rec.get("request_id")),
                  f"request_id={rec.get('request_id')!r}")
        rep.check("A5.6 单笔费用 ≤ 上限",
                  confirmed is None or float(confirmed) <= float(cfg.get("max_cost") or 1e9),
                  f"{confirmed} vs {cfg.get('max_cost')}")
    rep.check("A5.7 已占用 ≤ 上限",
              float(ledger.get("occupied_cost") or 0.0) <= float(cfg.get("max_cost") or 1e9),
              f"occupied={ledger.get('occupied_cost')} cap={cfg.get('max_cost')}")
    rep.check("A5.8 无未知费用笔数", int(ledger.get("unknown_requests") or 0) == 0,
              f"unknown_requests={ledger.get('unknown_requests')}")
    rep.check("A5.9 无失败笔数", int(ledger.get("failed_requests") or 0) == 0,
              f"failed_requests={ledger.get('failed_requests')}")

    # ---------- A1：调用次数 ----------
    rep.check("A1 真实调用恰好 1 次", int(calls.get("real_chat_calls") or 0) == 1,
              f"real_chat_calls={calls.get('real_chat_calls')}")

    # ---------- A7：人格可追溯 ----------
    refs_all: list[str] = []
    for bucket in ("background_refs",):
        refs_all += [str(r) for r in (persona.get(bucket) or [])]
    for bucket in ("traits", "habits", "single_events", "experiences"):
        for item in persona.get(bucket) or []:
            refs_all += [str(r) for r in (item.get("evidence_refs") or [])]
    untraceable = sorted({r for r in refs_all if r not in evidence_ids})
    rep.check("A7 档案条目引用全部命中已登记证据", not untraceable,
              f"不可追溯={untraceable} evidence_ids={sorted(evidence_ids)}")
    rep.check("A7b 至少产生了 1 条档案引用", bool(refs_all),
              f"refs={len(refs_all)}")

    # ---------- A8：AC-7 单次事件不得进 traits ----------
    bad_traits = [i.get("text") for i in (persona.get("traits") or [])
                  if str(i.get("stability")) in NON_TRAIT_STABILITIES]
    rep.check("A8.1 traits 内不含 SINGLE/UNKNOWN 稳定性条目", not bad_traits,
              f"违规={bad_traits}")
    single_texts = {str(i.get("text")) for i in (persona.get("single_events") or [])}
    trait_texts = {str(i.get("text")) for i in (persona.get("traits") or [])}
    rep.check("A8.2 单次事件未出现在 traits", not (single_texts & trait_texts),
              f"交集={single_texts & trait_texts}")

    # ---------- A12：格式正确 ≠ 事实可信 ----------
    def walk(node, path="$"):
        found = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k in FORBIDDEN_TRUTHY_KEYS and v is True:
                    found.append(f"{path}.{k}=True")
                found += walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                found += walk(v, f"{path}[{idx}]")
        return found

    truthy = walk(artifact)
    rep.check("A12 产物中不存在'已验证/已批准'被置真的字段", not truthy,
              f"命中={truthy}")

    # ---------- C 轮：多目标 / 关系条目 / 稳定性（仅在新版产物里校验） ----------
    targets = artifact.get("targets") or []
    relationship = artifact.get("relationship") or []
    evidence_rows = artifact.get("evidence") or []
    stage_warnings = artifact.get("stage_warnings") or {}
    diagnostics = artifact.get("diagnostics") or {}

    if diagnostics:
        target_ids = {str(t.get("entity_id")) for t in targets}
        pair = len(targets) >= 2

        if pair:
            rep.check("C1 双目标各有一份人物档案", len(targets) == 2,
                      f"targets={[t.get('display_name') for t in targets]}")
        else:
            rep.check("C1 单目标产物只有 1 份人物档案", len(targets) == 1,
                      f"targets={[t.get('display_name') for t in targets]}")

        # 每份档案都不能把 SINGLE/UNKNOWN 写进 traits（AC-7 / PF-V2 / D-012）
        bad_traits_multi: list[str] = []
        refs_multi: list[str] = []
        for t in targets:
            for item in t.get("traits") or []:
                if str(item.get("stability")) in NON_TRAIT_STABILITIES:
                    bad_traits_multi.append(f"{t.get('display_name')}:{item.get('text')}")
                refs_multi += [str(r) for r in (item.get("evidence_refs") or [])]
            refs_multi += [str(r) for r in (t.get("background_refs") or [])]
        rep.check("C2 每份人物档案的 traits 都不含 SINGLE/UNKNOWN 条目",
                  not bad_traits_multi, f"违规={bad_traits_multi}")
        rep.check("C3 多目标档案引用全部命中已登记证据",
                  not [r for r in refs_multi if r not in evidence_ids],
                  f"不可追溯={sorted({r for r in refs_multi if r not in evidence_ids})}")

        # ---- REL.*：关系条目产出路径（仅双目标产物有意义） ----
        # 单人物请求下关系阶段按语义 SKIPPED，四层探针的判定矩阵不适用
        # （矩阵的前置行是"3 份材料"，单人物只有 1 份）。此时只检查"确实没有
        # 关系档案"，不把 SKIPPED 误判成链路故障。
        if pair:
            rep.check("REL.1 关系档案恰好 1 份", len(relationship) == 1,
                      f"relationship={[r.get('id') for r in relationship]}")
            rel = relationship[0] if relationship else {}
            participants = [str(p) for p in (rel.get("participant_entity_ids") or [])]
            items = rel.get("shared_experiences") or []
            rep.check("REL.2 cardinality = PAIR 且参与者恰好 2 人",
                      str(rel.get("cardinality")) == "PAIR" and len(participants) == 2,
                      f"cardinality={rel.get('cardinality')} participants={participants}")
            rep.check("REL.3 关系参与者都在本次目标内",
                      bool(participants) and set(participants) <= target_ids,
                      f"participants={participants} targets={sorted(target_ids)}")
            rep.check("REL.4 status=OK ⟺ shared_experiences 非空",
                      (str(rel.get("status")) == "OK") == bool(items),
                      f"status={rel.get('status')} items={len(items)}")
            rel_refs = [str(r) for it in items for r in (it.get("evidence_refs") or [])]
            rep.check("REL.5 关系条目引用全部命中已登记证据",
                      not [r for r in rel_refs if r not in evidence_ids],
                      f"悬空={sorted({r for r in rel_refs if r not in evidence_ids})}")

            # 判定矩阵自洽：产物自报的 verdict 必须能从原始计数复算出来
            l1 = int(diagnostics.get("L1_materials_with_gate") or 0)
            l2 = int(diagnostics.get("L2_relation_candidates") or 0)
            l3 = int(diagnostics.get("L3_candidates_covering_both") or 0)
            l4 = int(diagnostics.get("L4_shared_experiences") or 0)
            statuses = [str(s) for s in (diagnostics.get("L4_status") or [])]
            if l1 < 3:
                expect = "PRECONDITION_NOT_MET"
            elif l4 >= 1 and statuses and all(s == "OK" for s in statuses):
                expect = "LINK_OK"
            elif l2 == 0:
                expect = "INSUFFICIENT_NO_RELATION_DIMENSION"
            elif l3 == 0:
                expect = "INSUFFICIENT_SINGLE_SUBJECT"
            elif l4 == 0:
                expect = "LINK_FAULT"
            else:
                expect = "PARTIAL"
            rep.check("REL.6 归因结论可由原始计数独立复算",
                      str(diagnostics.get("verdict")) == expect,
                      f"自报={diagnostics.get('verdict')} 复算={expect}")

            # 证据不足时**必须**有对应告警码（不允许静默返回空档案）
            rel_codes = set(stage_warnings.get("RELATIONSHIP_DISTILL") or [])
            if l1 < 3:
                rep.check("REL.7 未过前置时不要求关系告警（材料不足，归因不成立）",
                          True, f"codes={sorted(rel_codes)}")
            elif l2 == 0:
                rep.check("REL.7 L2=0 时带 NO_RELATION_EVIDENCE 告警",
                          "NO_RELATION_EVIDENCE" in rel_codes,
                          f"codes={sorted(rel_codes)}")
            elif l3 == 0:
                rep.check("REL.7 L3=0 时带主体不足告警",
                          "RELATION_EVIDENCE_SUBJECT_INSUFFICIENT" in rel_codes,
                          f"codes={sorted(rel_codes)}")
            else:
                rep.check("REL.7 关系阶段告警码可解释", True, f"codes={sorted(rel_codes)}")
        else:
            rep.check("REL.1s 单人物不产出关系档案（参与者 <2 按语义跳过）",
                      not relationship,
                      f"relationship={[r.get('id') for r in relationship]}")

        # ---- STAB.*：G6 稳定性判定的运行时证据 ----
        verdict_of = {str(e.get("id")): e.get("stability_verdict") for e in evidence_rows}
        rep.check("STAB.1 每条证据都有 stability_verdict",
                  bool(evidence_rows) and all(verdict_of.values()),
                  f"缺判定={[k for k, v in verdict_of.items() if not v]}")
        rewritten = [e.get("id") for e in evidence_rows
                     if str(e.get("stability")) != "UNKNOWN"]
        rep.check("STAB.2 stability 列未被判定覆盖（仍是登记值 UNKNOWN）",
                  not rewritten, f"被改写={rewritten}")
        # 只对**有来源**的证据成立：INFERENCE 证据登记时不带单一来源
        # （source_id 为空），occurrences=1 / distinct_sources=0 是正确结果。
        sourced = [v for e, v in verdict_of.items()
                   if v and next((r.get("source_id") for r in evidence_rows
                                  if str(r.get("id")) == e), None)]
        rep.check("STAB.3 有来源证据的 occurrences == distinct_sources（G6-D2 生效）",
                  all(int(v.get("occurrences", -1)) == int(v.get("distinct_sources", -2))
                      for v in sourced),
                  "存在有来源证据 occurrences != distinct_sources")
        bad_support = []
        for t in targets:
            for item in t.get("traits") or []:
                for r in (item.get("evidence_refs") or []):
                    v = verdict_of.get(str(r)) or {}
                    if str(v.get("stability")) in NON_TRAIT_STABILITIES:
                        bad_support.append(f"{t.get('display_name')}:{r}")
        rep.check("STAB.4 traits 的支撑证据判定均非 SINGLE/UNKNOWN",
                  not bad_support, f"违规={bad_support}")

    # ---------- 密钥 ----------
    blob = json.dumps(artifact, ensure_ascii=False)
    rep.check("SEC 产物中不含密钥形态字符串", not SECRET_SHAPE.search(blob),
              "命中 sk- 形态" if SECRET_SHAPE.search(blob) else "")

    return rep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4.5-A 运行产物离线复算")
    parser.add_argument("--dump", required=True, metavar="PATH",
                        help="e2e_p4_5a.py --dump 产出的 JSON")
    args = parser.parse_args(argv)

    path = Path(args.dump)
    if not path.exists():
        print(f"找不到产物文件：{path}")
        return 2
    artifact = json.loads(path.read_text(encoding="utf-8"))

    rep = verify(artifact)
    print("P4.5-A 运行产物离线复算（**不联网、不重跑、不 import 被测模块**）")
    print(f"产物：{path}")
    print(f"schema={artifact.get('schema')}  目标="
          f"{(artifact.get('target') or {}).get('display_name')}")
    print(f"整批 verdict={((artifact.get('extraction') or {}).get('verdict'))}")
    names = [str(t.get("display_name")) for t in (artifact.get("targets") or [])]
    if names:
        print(f"目标数={len(names)}（{'、'.join(names)}）", end="")
        rel = (artifact.get("relationship") or [{}])[0]
        if rel:
            print(f"  关系档案 status={rel.get('status')} "
                  f"shared_experiences={len(rel.get('shared_experiences') or [])}", end="")
        print("")
    diag = artifact.get("diagnostics") or {}
    if diag:
        print(f"四层探针：L1={diag.get('L1_materials_with_gate')} "
              f"L2={diag.get('L2_relation_candidates')} "
              f"L3={diag.get('L3_candidates_covering_both')} "
              f"L4={diag.get('L4_shared_experiences')} "
              f"→ {diag.get('verdict')}")
    print("")
    for name, ok, detail in rep.rows:
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}")
        if detail and not ok:
            print(f"          {detail}")
    passed, total = rep.summary()
    print("")
    print(f"结果：{passed}/{total} 通过")
    return 0 if passed == total else 3


if __name__ == "__main__":
    raise SystemExit(main())
