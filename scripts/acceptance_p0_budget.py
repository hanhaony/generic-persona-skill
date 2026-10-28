"""P0 预算「软硬双限」验收脚本（离线，不花真钱）。

对应 P0 三条验收标准：
  ① 新开任务 / 会话，`used_cost` 必须从 0 起算，历史账单绝不污染新任务；
  ② 连续跑 3 次完整 run 或聊 50 轮天，无任何预算报错；
  ③ 模拟花费达 2.00 USD 时成功弹出 (y/n) 提示；达 5.00 强制中断。

全程只用真实模块与真实 `.env` 配置，但**不调用任何真实 Provider / 不联网**，
账本用笔是合成的（金额等于真实估算），因此不产生费用、可重复运行。
"""

from __future__ import annotations

import sys
from dataclasses import replace

from persona.config import load_settings
from persona.core.context import new_call_context
from persona.core.errors import ErrorCode
from persona.core.result import is_err, is_ok, unwrap
from persona.orchestrator.model_setup import model_limits_from_settings
from persona.core.usage_ledger import (
    COST_CONFIRMED,
    OP_CHAT,
    OUTCOME_SUCCEEDED,
    ExternalUsageLedger,
    ExternalUsageRecord,
    SOFT_LIMIT_INCREMENT_USD,
    estimate_model_cost,
)

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []
CTX = None  # 调用上下文（main 中赋值，check_once 复用）


def record(criterion: str, status: str, detail: str) -> None:
    results.append((criterion, status, detail))
    print(f"  [{status}] {criterion}: {detail}")


def synth_record(key: str, cost: float) -> ExternalUsageRecord:
    return ExternalUsageRecord(
        idempotency_key=key,
        provider="offline-sim",
        operation=OP_CHAT,
        outcome=OUTCOME_SUCCEEDED,
        unit_count=1,
        success_unit_count=1,
        cost_estimate=cost,
        cost_confirmed=cost,
        currency="USD",
        cost_source="SIM",
        cost_status=COST_CONFIRMED,
        task_id=None,
        trace_ref="acceptance",
    )


def main() -> int:
    print("=" * 70)
    print("P0 预算「软硬双限」离线验收")
    print("=" * 70)

    settings_result = load_settings(use_dotenv=True)
    if not is_ok(settings_result):
        print("配置加载失败，无法验收。")
        return 2
    settings = unwrap(settings_result)
    limits = model_limits_from_settings(settings)
    global CTX
    CTX = new_call_context("prj_acceptance", "acceptance-script")
    est = estimate_model_cost(limits)
    print(f"\n真实 .env 配置 → 软限={limits.soft_limit} 硬限={limits.hard_limit} "
          f"次数上限={limits.max_calls} 单次估算={est:.6f} USD")

    # ----------------------------------------------------------------- ①
    print("\n--- 验收①：新开任务从 0 起算，历史不污染 ---")
    ledger = ExternalUsageLedger()
    record("①-新任务基线",
           PASS if ledger.session_occupied_cost() == 0.0 else FAIL,
           f"session_occupied_cost()={ledger.session_occupied_cost():.6f} "
           f"（应为 0.000000）")

    # 模拟「上一次运行」留下的历史账单（跨进程读回）
    fake_history = [synth_record(f"old-{i}", 0.5) for i in range(10)]  # 5.0 USD 历史
    summary = ledger.hydrate(fake_history)
    record("①-历史已读回",
           PASS if summary.loaded == 10 else FAIL,
           f"hydrate 读入 {summary.loaded} 笔历史，占用 {summary.occupied:.4f} USD")

    # 即便有 5.0 USD 历史，新任务的「本次任务口径」仍必须是 0
    record("①-历史不污染本次",
           PASS if ledger.session_occupied_cost() == 0.0 else FAIL,
           f"session_occupied_cost()={ledger.session_occupied_cost():.6f} "
           f"（历史 5.0 不得计入）")

    # 用真实闸门验证：即便历史 5.0，新任务也不会被软限/硬限误拦（以本次口径判定）
    err = check_once(ledger, limits, 0, "fresh-key")
    record("①-历史不触发闸门",
           PASS if err is None else FAIL,
           "check_model_budget 返回 None（不拦截新任务）" if err is None
           else f"意外拦截：{err.error.code}")

    # ----------------------------------------------------------------- ②
    print("\n--- 验收②：3 次完整 run / 50 轮对话，无预算报错 ---")

    def do_one_run(run_idx: int, history: list[ExternalUsageRecord] | None) -> str:
        """模拟一次完整 run（独立进程：新账本 + 跨进程读回历史）。"""
        lg = ExternalUsageLedger()
        if history:
            lg.hydrate(history)
        calls = 0
        while calls < limits.max_calls:
            key = f"run{run_idx}-call{calls}"
            e = check_once(lg, limits, calls, key)
            if is_err(e):
                return f"run{run_idx} 第 {calls} 次被拦：{e.error.code}"
            lg.settle(synth_record(key, est))
            calls += 1
        return ""

    # 三次独立 run，且每次都读回上一次的全部历史（最坏情况），验证历史不污染
    hist: list[ExternalUsageRecord] = []
    for i in range(3):
        msg = do_one_run(i, hist)
        record(f"②-run{i+1}",
               PASS if not msg else FAIL,
               "无预算报错" if not msg else msg)
        # 把这次 run 的笔当作历史交给下一次（跨进程）
        hist = list(hist) + [synth_record(f"run{i}-call{c}", est)
                             for c in range(limits.max_calls)]

    # 50 轮对话（同一进程内累计）
    chat_err = ""
    clg = ExternalUsageLedger()
    for r in range(50):
        key = f"chat-round{r}"
        e = check_once(clg, limits, r, key)
        if is_err(e):
            chat_err = f"第 {r} 轮被拦：{e.error.code}"
            break
        clg.settle(synth_record(key, est))
    record("②-50轮对话",
           PASS if not chat_err else FAIL,
           "无预算报错" if not chat_err else chat_err)

    # ----------------------------------------------------------------- ③
    print("\n--- 验收③：达软限弹 (y/n)；达硬限强制中断 ---")

    def accumulate_to(lg: ExternalUsageLedger, target: float, lim=limits) -> int:
        n = 0
        while lg.session_occupied_cost() + est <= target:
            key = f"acc{n}"
            e = check_once(lg, lim, n, key)
            if is_err(e):
                return n
            lg.settle(synth_record(key, est))
            n += 1
        return n

    # ③ 是「软/硬限机制」专项验证：真实配置里 max_calls=200 会在约 0.66 USD 处
    # 先触发次数上限（远低于 2.0 软限），所以这里用一份「关掉次数上限」的拷贝，
    # 单独把花费推到 2.0 / 5.0 验证软硬限机制本身。
    # （真实产品路径里，单任务 200 次调用 ≈ 0.66 USD，软限 2.0 是异常花费的兜底。）

    # ③a：软限达值 + 用户回答 n → BUDGET_PAUSED
    lg3 = ExternalUsageLedger()
    lim_n = replace(limits, max_calls=None, soft_limit_handler=lambda cost: False)
    accumulate_to(lg3, float(limits.soft_limit), lim_n)
    err = check_once(lg3, lim_n, 999, "reach-soft-n")
    record("③-软限弹(y/n)且选n",
           PASS if (is_err(err) and err.error.code == ErrorCode.BUDGET_PAUSED)
           else FAIL,
           f"返回 {err.error.code if is_err(err) else 'None'} "
           f"（期望 BUDGET_PAUSED）")

    # ③b：软限达值 + 用户回答 y → 软限抬升 SOFT_LIMIT_INCREMENT_USD 继续
    lg3b = ExternalUsageLedger()
    lim_y = replace(limits, max_calls=None, soft_limit_handler=lambda cost: True)
    accumulate_to(lg3b, float(limits.soft_limit), lim_y)
    before = float(lim_y.soft_limit)
    err2 = check_once(lg3b, lim_y, 999, "reach-soft-y")
    after = float(lim_y.soft_limit)
    record("③-软限弹(y/n)且选y",
           PASS if (err2 is None and after == before + SOFT_LIMIT_INCREMENT_USD)
           else FAIL,
           f"软限 {before:.2f} → {after:.2f}（期望 +{SOFT_LIMIT_INCREMENT_USD:.2f}），"
           f"且无拦截" if err2 is None else f"意外拦截 {err2.error.code}")

    # ③c：硬限达值 → 强制中断 BUDGET_EXCEEDED（关掉软限与次数上限，单独验证硬限）
    lg3c = ExternalUsageLedger()
    lim_hard = replace(limits, max_calls=None, soft_limit=None)
    accumulate_to(lg3c, float(limits.hard_limit), lim_hard)
    err3 = check_once(lg3c, lim_hard, 999, "reach-hard")
    record("③-硬限强制中断",
           PASS if (is_err(err3) and err3.error.code == ErrorCode.BUDGET_EXCEEDED)
           else FAIL,
           f"返回 {err3.error.code if is_err(err3) else 'None'} "
           f"（期望 BUDGET_EXCEEDED）")

    # ----------------------------------------------------------------- 汇总
    print("\n" + "=" * 70)
    print("验收汇总")
    print("=" * 70)
    n_pass = sum(1 for _, s, _ in results if s == PASS)
    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    for crit, st, det in results:
        print(f"  {st:4} | {crit} | {det}")
    print(f"\n结果：{n_pass} 通过 / {n_fail} 失败")
    return 0 if n_fail == 0 else 1


def check_once(ledger: ExternalUsageLedger, lim, calls: int, key: str):
    """发前闸门 + 预留；返回 Err 或 None。settle 由调用方在确认不拦后做。"""
    from persona.modules.model_budget import check_model_budget
    return check_model_budget(
        CTX, ledger=ledger, limits=lim, task_id=None,
        calls_made=calls, key=key)


if __name__ == "__main__":
    sys.exit(main())
