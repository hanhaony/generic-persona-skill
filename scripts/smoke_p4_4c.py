"""P4.4-C 真实模型**受控冒烟**运行器（一次性、人工执行、不进 unittest 收集）。

    python scripts/smoke_p4_4c.py            # 完整跑：预检 → 1 次真实调用 → 报告
    python scripts/smoke_p4_4c.py --dry      # 只做预检，**绝不发出真实请求**

# 它会花真钱

这个脚本会向 DeepSeek 发出**一次**真实 `chat/completions` 请求，产生真实费用。
所有省钱与安全纪律都写死在下面，运行前逐条预检，任何一条不满足就**在发请求之前**
退出：

1. `max_retries=0`（重试 = 再花一次钱）；`max_calls=1`；`max_extracts≥1`；
2. 单任务/总费用上限 0.25 USD，单价与币种必须齐备（缺单价 → 闸门拒绝发请求）；
3. 币种 USD、档位 peak、模型 ID deepseek-flash（**不擅自换模型**）；
4. `require_gate_verdict=True`：材料必须亮出正文质量门禁结论，否则进不了抽取器；
5. `response_format` 映射为 `json_object` 且提示词含 "json"（D4：否则拒绝发送）；
6. 密钥**只**在请求头；任何输出行里出现密钥子串 → 立即终止并标红；
7. 失败**不重试**；费用未知记 `UNKNOWN`，**绝不记 0**。

# 为什么配置写在脚本里

模型段的非敏感配置（provider/模型/单价/上限）在本机**尚未持久化**（不在环境变量、
也没有 `.env`）。本脚本用 `load_settings(overrides=...)` 把拍板值**显式**注入，
好处是：跑什么配置一目了然、可复现、不改用户机器上的任何东西。
密钥仍然只从环境变量来（`PERSONA_PROVIDER_API_KEY` 或别名 `DEEPSEEK_API_KEY`）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.config import load_settings                                    # noqa: E402
from persona.core.context import CallContext                                # noqa: E402
from persona.core.result import is_err                                      # noqa: E402
from persona.core.types import SourceMaterial                               # noqa: E402
from persona.core.usage_ledger import ExternalUsageLedger                    # noqa: E402
from persona.modules.extract_quality import QUALITY_VALID                    # noqa: E402
from persona.modules.research import PersonQueryTarget                       # noqa: E402
from persona.orchestrator.model_setup import (                               # noqa: E402
    chat_provider_config,
    chat_provider_from_settings,
    claim_extractor_from_settings,
    effective_max_cost,
    model_limits_from_settings,
    model_plan,
)
from persona.providers.base import ChatRequest                              # noqa: E402

# --------------------------------------------------------------------------
# 拍板配置（P4.4-B / P4.4-C）。**显式注入**，不依赖本机是否持久化过。
# --------------------------------------------------------------------------
SMOKE_CONFIG: dict[str, str] = {
    "PERSONA_OFFLINE_ONLY": "false",          # 真实联网（否则加载期就拒）
    "PERSONA_MODEL_PROVIDER": "deepseek",
    "PERSONA_MODEL_ID": "deepseek-flash",     # 不擅自换模型
    "PERSONA_MODEL_CURRENCY": "USD",          # 币种固定 USD，不做汇率换算
    "PERSONA_MODEL_PRICE_TIER": "peak",       # 保留 peak
    "PERSONA_MODEL_PRICE_IN_PER_1M": "0.3",
    "PERSONA_MODEL_PRICE_OUT_PER_1M": "1.2",
    "PERSONA_MODEL_PRICE_SOURCE": "https://api-docs.deepseek.com/quick_start/pricing",
    "PERSONA_MODEL_PRICE_CHECKED_AT": "2026-10-16",
    "PERSONA_MODEL_MAX_COST": "0.25",         # 总上限（暂与单任务同值）
    "PERSONA_MODEL_MAX_COST_PER_TASK": "0.25",
    "PERSONA_MODEL_MAX_CALLS": "1",           # 冒烟范围：只允许 1 次
    "PERSONA_MODEL_EST_TOKENS_IN": "3000",    # 保守估算，用于发送前预留
    "PERSONA_MODEL_EST_TOKENS_OUT": "2000",
    "PERSONA_MODEL_REQUIRE_GATE_VERDICT": "true",
    "PERSONA_RESEARCH_MAX_EXTRACTS": "1",     # ≥1（本脚本不跑研究，仅令配置自洽）
}

#: 合成虚构材料（AC-16：**不得**出现真实人物名）
SMOKE_TITLE = "[合成样张·虚构] 雾港地方志摘抄（非真实人物）"
SMOKE_SNIPPET = (
    "阿雾在雾港灯塔值守，每日记录潮位与风向。"
    "据地方志记载，阿雾偏爱在傍晚独自巡视堤坝。"
    "星灯会由阿灯发起，成员多为雾港的老住户。"
)
SMOKE_SOURCE_ID = "src_smoke_synthetic_1"
SMOKE_CANONICAL_KEY = "smoke:synthetic:1"
SMOKE_URL = "https://example.invalid/smoke-synthetic-1"

SMOKE_TARGETS = (
    PersonQueryTarget(entity_id="pe_smoke_awu", display_name="阿雾"),
    PersonQueryTarget(entity_id="pe_smoke_adeng", display_name="阿灯"),
)


# --------------------------------------------------------------------------
# 输出缓冲 + 密钥泄漏自检
# --------------------------------------------------------------------------
class _Out:
    """所有输出先过这里，最后统一做**密钥子串扫描**。"""

    def __init__(self, secret: str) -> None:
        self._secret = secret or ""
        self.lines: list[str] = []
        self.leak = False

    def __call__(self, text: str = "") -> None:
        if self._secret and self._secret in text:
            self.leak = True
            text = text.replace(self._secret, "***[REDACTED]***")
        self.lines.append(text)
        print(text)

    def scan(self, *values: str) -> bool:
        """对任意字符串做一次泄漏检查（返回 True = 发现泄漏）。"""
        if not self._secret:
            return False
        return any(self._secret in (v or "") for v in values)


class _RecordingProvider:
    """薄记录层：委托给真 Provider，只额外留下最后一次响应与调用次数。

    不是"第二套实现"——它不解析、不判定，只把 `ChatResponse` 原样存下来供报告引用。
    `provider_name` 必须透传，否则抽取器的幂等键会变成包装类的类名。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.provider_name = getattr(inner, "provider_name", type(inner).__name__)
        self.calls = 0
        self.last_result = None

    def chat(self, ctx, req):
        self.calls += 1
        self.last_result = self._inner.chat(ctx, req)
        return self.last_result

    def search(self, ctx, req):
        return self._inner.search(ctx, req)

    def usage(self, ctx, scope):
        return self._inner.usage(ctx, scope)

    def health(self, ctx):
        return self._inner.health(ctx)

    def capabilities(self):
        return self._inner.capabilities()


def _hr(out: _Out, title: str) -> None:
    out("")
    out("=" * 72)
    out(title)
    out("=" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4.4-C 受控冒烟（会真实计费）")
    parser.add_argument("--dry", action="store_true",
                        help="只做预检，绝不发出真实请求")
    args = parser.parse_args(argv)

    # ---------- 0. 配置加载（密钥只从环境变量来） ----------
    loaded = load_settings(overrides=SMOKE_CONFIG)
    if is_err(loaded):
        print("配置加载失败：", loaded.error.message)
        return 2
    settings = loaded.value
    secret = str(settings.secrets.provider_api_key or "")
    out = _Out(secret)

    out("P4.4-C 真实模型受控冒烟测试")
    out(f"模式：{'DRY-RUN（不发请求）' if args.dry else '真实调用（会产生费用）'}")
    out(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    out(f"密钥：{'已设置（内容不显示）' if secret else '未设置'}  长度={len(secret)}")

    # ---------- 1. 配置核验（不发请求） ----------
    _hr(out, "[1] 配置核验")
    plan = model_plan(settings)
    for key in ("enabled", "provider", "model_id", "currency", "price_tier",
                "price_per_1m_in", "price_per_1m_out", "price_per_1k_in",
                "price_per_1k_out", "price_source", "price_checked_at",
                "max_cost_total", "max_cost_per_task", "max_cost_effective",
                "max_calls", "est_tokens_in", "est_tokens_out",
                "est_cost_per_call", "require_gate_verdict", "secret_status"):
        out(f"  {key:22s}: {plan[key]}")

    limits = model_limits_from_settings(settings)
    provider_cfg = chat_provider_config(settings)

    problems: list[str] = []

    def expect(cond: bool, desc: str) -> None:
        out(f"  [{'OK ' if cond else 'FAIL'}] {desc}")
        if not cond:
            problems.append(desc)

    out("")
    expect(plan["enabled"] is True, "模型抽取已启用（model.provider 非空）")
    expect(plan["provider"] == "deepseek", "服务商 = deepseek")
    expect(plan["model_id"] == "deepseek-flash", "模型 ID = deepseek-flash")
    expect(plan["currency"] == "USD", "币种 = USD（不做汇率换算）")
    expect(plan["price_tier"] == "peak", "价格档位 = peak")
    expect(plan["price_per_1m_in"] == 0.3, "输入单价 = 0.3 /1M")
    expect(plan["price_per_1m_out"] == 1.2, "输出单价 = 1.2 /1M")
    expect(plan["max_cost_effective"] == 0.25, "生效费用上限 = 0.25 USD")
    expect(plan["max_calls"] == 1, "调用次数上限 = 1")
    expect(plan["require_gate_verdict"] is True, "require_gate_verdict = True")
    expect(provider_cfg.max_retries == 0, "Provider 重试次数强制 = 0")
    expect(provider_cfg.per_call_timeout_ms == 30_000, "单次调用超时 = 30000 ms")
    expect(secret != "", "模型密钥已设置（只检查存在性）")
    if args.dry:
        expect(plan["est_cost_per_call"] is not None, "单次调用费用可估算（闸门可预留）")

    # ---------- 2. 预算与账本前置状态 ----------
    _hr(out, "[2] 预算与账本前置状态")
    ledger = ExternalUsageLedger()
    before = ledger.totals()
    out(f"  账本类型        : 新建内存账本（**未创建/未删除任何账本文件**）")
    out(f"  调用前已占用     : {ledger.occupied_cost():.6f} {limits.currency}")
    out(f"  调用前记录数     : {before.requests}")
    out(f"  调用前未知费用笔数: {before.unknown_requests}")
    pause, why = ledger.should_pause(limits.max_cost)
    expect(not pause, f"预算闸门未处于暂停态（{why or '正常'}）")

    # ---------- 3. 材料与目标（合成虚构） ----------
    _hr(out, "[3] 材料与目标（合成虚构，AC-16）")
    materials = [SourceMaterial(
        source_id=SMOKE_SOURCE_ID,
        title=SMOKE_TITLE,
        url=SMOKE_URL,
        canonical_key=SMOKE_CANONICAL_KEY,
        snippet=SMOKE_SNIPPET,
        source_kind="WEB_PAGE",
        subject_entity_ids=[t.entity_id for t in SMOKE_TARGETS],
        quality_status=QUALITY_VALID,
    )]
    out(f"  材料数          : {len(materials)}（单份，最少必要）")
    out(f"  material_key    : {SMOKE_CANONICAL_KEY}")
    out(f"  quality_status  : {QUALITY_VALID}（门禁结论齐备，可进抽取器）")
    out(f"  目标白名单      : {[t.entity_id for t in SMOKE_TARGETS]}")
    out(f"  正文            : {SMOKE_SNIPPET}")
    expect(len(materials) == 1, "只发送 1 份材料")

    # ---------- 4. 请求预检（离线构造，绝不发送） ----------
    _hr(out, "[4] 请求预检（离线构造，不发送）")
    provider_result = chat_provider_from_settings(settings)
    if is_err(provider_result):
        out(f"  [FAIL] 无法装配 Provider：{provider_result.error.message}")
        problems.append("Provider 装配失败")
        return _finish(out, problems, abort=True)
    real_provider = provider_result.value
    recorder = _RecordingProvider(real_provider)

    extractor = claim_extractor_from_settings(
        settings, recorder, ledger=ledger, task_id="task_smoke_p4_4c_001")
    ctx = CallContext(project_id="smoke_p4_4c", actor="smoke",
                      task_id="task_smoke_p4_4c_001")

    request: ChatRequest = extractor.build_request(
        materials, list(SMOKE_TARGETS), model_hint=settings.model.model_id)
    out(f"  response_format : {request.response_format}（统一契约的『要结构化输出』）")
    out(f"  max_output_tokens: {request.max_output_tokens}")
    out(f"  temperature     : {getattr(request, 'temperature', None)}")
    out(f"  消息数          : {len(request.messages)}")

    body_result = real_provider._build_body(ctx, request)
    if is_err(body_result):
        out(f"  [FAIL] 请求体构造被拒：{body_result.error.message}")
        problems.append("请求体构造被拒（D4 或参数非法）")
        return _finish(out, problems, abort=True)
    body = body_result.value

    wire = json.dumps(body, ensure_ascii=False)
    expect(body.get("model") == "deepseek-flash", "线上 model = deepseek-flash")
    expect(body.get("response_format") == {"type": "json_object"},
           "response_format 已映射为 json_object")
    expect(body.get("thinking") == {"type": "disabled"}, "thinking = disabled（D1）")
    expect(body.get("stream") is False, "stream = false")
    expect(body.get("max_tokens") == request.max_output_tokens, "max_tokens 已透传")
    expect("json" in json.dumps(body.get("messages"), ensure_ascii=False).lower(),
           "提示词含 \"json\" 字样（D4 硬要求）")
    expect("metadata" not in body, "metadata（含 prompt_version）不外发")
    expect(not out.scan(wire), "请求体里**不含**密钥")
    expect(not out.scan(repr(real_provider)), "Provider repr 里**不含**密钥")
    expect(getattr(recorder, "provider_name", "") == "deepseek",
           "幂等键 provider 段 = deepseek")

    if problems:
        out("")
        out("预检未通过：**不发出任何真实请求**。")
        return _finish(out, problems, abort=True)

    if args.dry:
        out("")
        out("DRY-RUN 结束：预检全部通过，未发出任何真实请求。")
        return 0

    # ---------- 5. 真实调用（只此一次，失败不重试） ----------
    _hr(out, "[5] 真实调用（1 次，max_retries=0，失败不重试）")
    out("  → 正在发送 POST https://api.deepseek.com/chat/completions …")
    started = time.monotonic()
    outcome = None
    raised: BaseException | None = None
    try:
        result = extractor.extract(ctx, materials, targets=list(SMOKE_TARGETS))
    except BaseException as exc:                      # noqa: BLE001 —— 如实上报
        raised = exc
        result = None
    elapsed = time.monotonic() - started
    out(f"  ← 返回，耗时 {elapsed:.2f} s；Provider 实际调用次数 = {recorder.calls}")

    if result is not None and is_err(result):
        out(f"  调用结果        : Err（{result.error.code}）")
        out(f"  错误信息        : {result.error.message}")
    elif result is not None:
        outcome = result.value
        out("  调用结果        : Ok")
    if raised is not None:
        out(f"  抛出异常        : {type(raised).__name__}: {raised}")

    # ---------- 6. 结果与输出验证 ----------
    _hr(out, "[6] 输出与证据门禁")
    if outcome is not None:
        out(f"  method          : {outcome.method}")
        out(f"  整批结论 verdict : {outcome.verdict}")
        out(f"  候选 claims     : {len(outcome.claims)}")
        out(f"  待审核 pending  : {len(outcome.pending)}")
        out(f"  拒收 rejected   : {len(outcome.rejected)}")
        out(f"  warnings        : {len(outcome.warnings)}")
        for w in outcome.warnings:
            out(f"    - [{w.code}] {w.message}")
        for r in outcome.rejected:
            out(f"    ! 拒收 [{r.reason}] {r.message}")
        for p in outcome.pending:
            out(f"    ? 待审核 [{p.reason}] {p.message}")
        for c in outcome.claims:
            out("    * 候选：")
            out(f"        key            : {c.key}")
            out(f"        tier/dimension : {c.tier} / {c.dimension}")
            out(f"        subjects       : {c.subject_entity_ids}")
            out(f"        confidence     : {c.confidence}")
            out(f"        claim          : {c.claim}")
            out(f"        quote          : {c.verbatim_quote!r}")
            out(f"        locator        : {c.locator}")
            out(f"        derivation_keys: {c.derivation_keys}")
            out(f"        tags           : {c.tags}")
        detail = outcome.validation_detail or {}
        out("  validation_detail:")
        out("    " + json.dumps(detail, ensure_ascii=False)[:1500])

    # ---------- 7. 响应原文与用量 ----------
    _hr(out, "[7] 响应与 token 用量")
    last = recorder.last_result
    chat_resp = last.value if (last is not None and not is_err(last)) else None
    raw_text = getattr(chat_resp, "text", "") if chat_resp is not None else ""
    if chat_resp is not None:
        out(f"  finish_reason   : {getattr(chat_resp, 'finish_reason', '')}")
        out(f"  model_id        : {getattr(chat_resp, 'model_id', '')}")
        out(f"  raw_ref(请求 id): {getattr(chat_resp, 'raw_ref', '')}")
        out(f"  原始输出字符数   : {len(raw_text)}")
        out("  原始输出（截断 2000 字符）：")
        out("    " + raw_text[:2000].replace("\n", "\n    "))
        u = getattr(chat_resp, "usage", None)
        if u is not None:
            out(f"  tokens_in       : {u.tokens_in}")
            out(f"  tokens_out      : {u.tokens_out}")
            out(f"  cost_confirmed  : {u.cost_confirmed}（Provider 不报金额，应为 None）")
            out(f"  cost_status     : {u.cost_status}")
    else:
        out("  （没有可展示的响应对象：调用未成功）")

    # ---------- 8. 账本 ----------
    _hr(out, "[8] 账本记录与预算")
    records = ledger.records()
    for rec in records:
        out(f"  记录 ID(幂等键)  : {rec.idempotency_key}")
        out(f"    provider/op   : {rec.provider} / {rec.operation}")
        out(f"    outcome       : {rec.outcome}")
        out(f"    request_id    : {rec.request_id}")
        out(f"    cost_estimate : {rec.cost_estimate}")
        out(f"    cost_confirmed: {rec.cost_confirmed}")
        out(f"    cost_status   : {rec.cost_status}")
        out(f"    cost_source   : {rec.cost_source}")
        out(f"    currency      : {rec.currency}")
        out(f"    requested_at  : {rec.requested_at}")
    totals = ledger.totals()
    out(f"  记录数          : {totals.requests}")
    out(f"  已确认费用       : {totals.confirmed_cost}")
    out(f"  估算费用         : {totals.estimated_cost:.6f}")
    out(f"  未知占位         : {totals.unknown_hold_cost:.6f}")
    out(f"  未知费用笔数     : {totals.unknown_requests}")
    out(f"  失败笔数         : {totals.failed_requests}")
    out(f"  已占用（保守）   : {ledger.occupied_cost():.6f} {limits.currency}")
    cap = limits.max_cost
    if cap is not None:
        out(f"  剩余预算         : {max(0.0, cap - ledger.occupied_cost()):.6f} / {cap} {limits.currency}")

    # ---------- 9. 安全自检 ----------
    _hr(out, "[9] 安全自检")
    expect(not out.leak, "全部输出行均不含密钥")
    expect(recorder.calls <= 1, "真实调用次数 ≤ 1（未重试、未循环）")

    return _finish(out, problems, abort=False, outcome=outcome, ledger=ledger)


def _finish(out: _Out, problems: list[str], *, abort: bool, outcome=None,
            ledger=None) -> int:
    _hr(out, "结论")
    if problems:
        out(f"  预检问题 {len(problems)} 条：")
        for p in problems:
            out(f"    - {p}")
    if abort:
        out("  未发出真实请求（按停止条件退出）。")
        return 3
    out(f"  预检：{'全部通过' if not problems else '存在失败项'}")
    out("  真实调用：已发出 1 次（除非上方显示 Err/异常）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
