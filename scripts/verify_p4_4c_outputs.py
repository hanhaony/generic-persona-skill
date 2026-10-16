"""P4.4-C 输出与证据门禁的**独立离线验证**（不联网、不再花一分钱）。

    python scripts/verify_p4_4c_outputs.py

它做四件事，全部离线：

1. **复算可追溯性**：把运行 1 捕获的模型原文重新送进 `validate_model_payload`，
   并**独立**核对每条 VERBATIM 的 `verbatim_quote` 是否真能在材料里逐字找到、
   `locator` 的 start/end 是否与原文下标一致（不采信模型的任何自报元数据）。
2. **tier 语义**：VERBATIM 必须有摘录；INFERENCE 必须有 derivation_keys 且不得带摘录。
3. **门禁负样例（用桩 Provider，绝不触网）**：把材料改成 `LOW_QUALITY` 或留空，
   在 `require_gate_verdict=true` 下必须被**拒收**且**一次模型调用都不发生**。
4. **候选 ≠ 证据**：确认产出物是 `CandidateClaim`（带 `model-extract` 标记），
   没有被登记成 `EvidenceItem`。

`RAW_OUTPUT_RUN1` 是 2026-10-16 真实冒烟（run 1）的**逐字原文**，采集自该次运行的
标准输出；本脚本只读它、不改它。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.core.context import CallContext                                # noqa: E402
from persona.core.result import is_err, Ok                                   # noqa: E402
from persona.core.types import SourceMaterial                                # noqa: E402
from persona.core.usage_ledger import ExternalUsageLedger, ModelCallLimits    # noqa: E402
from persona.modules import extraction as ex                                 # noqa: E402
from persona.modules.claim_validation import validate_model_payload           # noqa: E402
from persona.modules.extract_quality import QUALITY_LOW, QUALITY_VALID        # noqa: E402
from persona.modules.extraction import ProviderClaimExtractor                 # noqa: E402
from persona.modules.research import PersonQueryTarget                        # noqa: E402

# --------------------------------------------------------------------------
# 运行 1 的输入与原文（逐字采集，不改）
# --------------------------------------------------------------------------
MATERIAL_SNIPPET = (
    "阿雾在雾港灯塔值守，每日记录潮位与风向。"
    "据地方志记载，阿雾偏爱在傍晚独自巡视堤坝。"
    "星灯会由阿灯发起，成员多为雾港的老住户。"
)
TARGETS = [
    PersonQueryTarget(entity_id="pe_smoke_awu", display_name="阿雾"),
    PersonQueryTarget(entity_id="pe_smoke_adeng", display_name="阿灯"),
]

RAW_OUTPUT_RUN1 = (
    '{"claims": [{"key": "smoke1-001", "material_key": "smoke:synthetic:1", '
    '"claim": "阿雾在雾港灯塔值守。", "tier": "VERBATIM", "dimension": "EXPERIENCE", '
    '"subject_entity_ids": ["pe_smoke_awu"], "confidence": 0.9, '
    '"verbatim_quote": "阿雾在雾港灯塔值守"}, '
    '{"key": "smoke1-002", "material_key": "smoke:synthetic:1", '
    '"claim": "阿雾每日记录潮位与风向。", "tier": "VERBATIM", "dimension": "HABIT", '
    '"subject_entity_ids": ["pe_smoke_awu"], "confidence": 0.9, '
    '"verbatim_quote": "每日记录潮位与风向"}, '
    '{"key": "smoke1-003", "material_key": "smoke:synthetic:1", '
    '"claim": "据地方志记载，阿雾偏爱在傍晚独自巡视堤坝。", "tier": "VERBATIM", '
    '"dimension": "HABIT", "subject_entity_ids": ["pe_smoke_awu"], "confidence": 0.85, '
    '"verbatim_quote": "据地方志记载，阿雾偏爱在傍晚独自巡视堤坝"}, '
    '{"key": "smoke1-004", "material_key": "smoke:synthetic:1", '
    '"claim": "星灯会由阿灯发起。", "tier": "VERBATIM", "dimension": "RELATION", '
    '"subject_entity_ids": ["pe_smoke_adeng"], "confidence": 0.9, '
    '"verbatim_quote": "星灯会由阿灯发起"}, '
    '{"key": "smoke1-005", "material_key": "smoke:synthetic:1", '
    '"claim": "星灯会成员多为雾港的老住户。", "tier": "VERBATIM", '
    '"dimension": "BACKGROUND", "subject_entity_ids": ["pe_smoke_adeng"], '
    '"confidence": 0.8, "verbatim_quote": "成员多为雾港的老住户"}]}'
)


def _material(status: str) -> SourceMaterial:
    return SourceMaterial(
        source_id="src_smoke_synthetic_1",
        title="[合成样张·虚构] 雾港地方志摘抄（非真实人物）",
        url="https://example.invalid/smoke-synthetic-1",
        canonical_key="smoke:synthetic:1",
        snippet=MATERIAL_SNIPPET,
        source_kind="WEB_PAGE",
        subject_entity_ids=[t.entity_id for t in TARGETS],
        quality_status=status,
    )


class _CountingStubProvider:
    """只数调用次数、永不联网的桩 Provider（用于门禁负样例）。"""

    provider_name = "deepseek"        # 与真 Provider 同名，键口径一致
    calls = 0

    def chat(self, ctx, req):
        self.calls += 1
        raise AssertionError("门禁失效：负样例竟然走上了模型调用路径")

    def search(self, ctx, req):
        raise AssertionError("不涉及搜索")

    def usage(self, ctx, scope):
        return Ok(None)

    def health(self, ctx):
        return Ok(None)

    def capabilities(self):
        return Ok(None)


def main() -> int:
    results: list[tuple[bool, str]] = []

    def check(cond: bool, desc: str) -> None:
        results.append((bool(cond), desc))
        print(f"  [{'OK ' if cond else 'FAIL'}] {desc}")

    ctx = CallContext(project_id="verify_p4_4c", actor="verify")

    # ---------- 1. 复算：原文 → 校验器 ----------
    print("\n[1] 原文重新过校验器（复算，不采信模型自报）")
    material = _material(QUALITY_VALID)
    res = validate_model_payload(RAW_OUTPUT_RUN1, materials=[material],
                                 targets=TARGETS, finish_reason="stop")
    check(res.verdict == "OK", f"整批结论 = {res.verdict}")
    check(len(res.accepted) == 5, f"接受 5 条（实际 {len(res.accepted)}）")
    check(not res.rejected, "无拒收")
    check(not res.pending, "无待审核")
    check(not res.malformed, "不是畸形输出")

    # ---------- 2. 可追溯性：逐字 + locator 独立核对 ----------
    print("\n[2] 可追溯性独立核对（quote 逐字 + locator 下标）")
    for vc in res.accepted:
        quote = vc.verbatim_quote or ""
        found = MATERIAL_SNIPPET.find(quote)
        ok_found = found >= 0 and quote != ""
        loc = vc.locator or {}
        ok_loc = (loc.get("field") == "snippet"
                  and loc.get("start") == found
                  and loc.get("end") == found + len(quote))
        check(ok_found, f"{vc.key}: 摘录能在材料里逐字找到（下标 {found}）")
        check(ok_loc, f"{vc.key}: locator 与下标一致 {loc}")
        check(vc.tier == "VERBATIM", f"{vc.key}: tier = {vc.tier}")

    # ---------- 3. tier 语义 ----------
    print("\n[3] tier 语义（VERBATIM 有摘录；INFERENCE 不许有摘录、必须有根）")
    for vc in res.accepted:
        if vc.tier == "VERBATIM":
            check(bool(vc.verbatim_quote), f"{vc.key}: VERBATIM 带摘录")
        if vc.tier == "INFERENCE":
            check(not vc.verbatim_quote, f"{vc.key}: INFERENCE 不带摘录")
            check(bool(vc.derivation_keys), f"{vc.key}: INFERENCE 有 derivation_keys")
    check(all(vc.subject_entity_ids for vc in res.accepted), "每条都有主体 ID")
    allowed_ids = {t.entity_id for t in TARGETS}
    check(all(set(vc.subject_entity_ids) <= allowed_ids for vc in res.accepted),
          "主体 ID 全部落在白名单内（未自造 ID）")

    # ---------- 4. 门禁负样例（桩 Provider，绝不触网） ----------
    print("\n[4] 门禁负样例（require_gate_verdict=true；桩 Provider=离线）")
    for status, label in ((QUALITY_LOW, "LOW_QUALITY"), ("", "未声明 ''")):
        stub = _CountingStubProvider()
        extractor = ProviderClaimExtractor(
            stub, model_hint="deepseek-flash",
            limits=ModelCallLimits(max_calls=1, max_cost=0.25,
                                   price_per_1k_in=0.0003, price_per_1k_out=0.0012,
                                   currency="USD", est_tokens_in=3000,
                                   est_tokens_out=2000),
            ledger=ExternalUsageLedger(),
            require_gate_verdict=True,
        )
        out_result = extractor.extract(ctx, [_material(status)], targets=TARGETS)
        outcome = out_result.value if not is_err(out_result) else None
        check(outcome is not None and not outcome.claims,
              f"quality_status={label}: 未产出任何候选")
        check(outcome is not None and bool(outcome.rejected),
              f"quality_status={label}: 被拒收并留痕")
        reason = outcome.rejected[0].reason if (outcome and outcome.rejected) else ""
        expected = (ex.REJECT_MATERIAL_NOT_GATE_PASSED if status
                    else ex.REJECT_MATERIAL_UNDECLARED)
        check(reason == expected, f"quality_status={label}: 拒收原因 = {reason}")
        check(stub.calls == 0, f"quality_status={label}: **零模型调用**（{stub.calls}）")

    # ---------- 5. 候选 ≠ 证据 ----------
    print("\n[5] 候选 ≠ 证据")
    sample = ProviderClaimExtractor._to_candidate(
        res.accepted[0], {"smoke:synthetic:1": material})
    check(isinstance(sample, ex.CandidateClaim), "产出物是 CandidateClaim")
    check(ex.TAG_MODEL_EXTRACT in sample.tags, "带 model-extract 标记（不等于已核实）")
    check(not hasattr(sample, "verified") and not hasattr(sample, "evidence_id"),
          "CandidateClaim 没有『已验证/证据 ID』字段")
    check(sample.locator is not None, "locator 由框架给出（模型伪造不了位置）")

    failed = [d for ok, d in results if not ok]
    print("\n" + "=" * 60)
    print(f"验证项总数 {len(results)}，通过 {len(results) - len(failed)}，"
          f"失败 {len(failed)}")
    for d in failed:
        print(f"  FAIL: {d}")
    print("=" * 60)
    print(json.dumps({"total": len(results), "failed": len(failed),
                      "passed": len(results) - len(failed)}, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
