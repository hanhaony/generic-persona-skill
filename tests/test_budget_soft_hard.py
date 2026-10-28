"""P0 体验修复：全局单任务「软硬双限」的单元测试。

覆盖：
  - 新开任务账本从 0 起算（历史账单不污染）；
  - 软上限触发 → 暂停（handler 拒绝）→ BUDGET_PAUSED；
  - 软上限触发 → 用户确认继续 → 软限抬升 STEP；
  - 硬上限触发 → 强制熔断 BUDGET_EXCEEDED（且硬优先于软）；
  - 次数上限 / 未知单价 仍按既有语义拦截；
  - CLI `--budget` 经 override 映射到软上限、真实 .env 软硬限生效、
    遗留上限键回退。
"""

from __future__ import annotations

import unittest

from persona.config import load_settings
from persona.core.context import new_call_context
from persona.core.errors import ErrorCategory, ErrorCode
from persona.core.result import is_err
from persona.core.types import (
    COST_CONFIRMED,
    OP_CHAT,
    OUTCOME_SUCCEEDED,
    ExternalUsageRecord,
)
from persona.core.usage_ledger import (
    ExternalUsageLedger,
    ModelCallLimits,
    SOFT_LIMIT_INCREMENT_USD,
)
from persona.modules.model_budget import check_model_budget
from persona.orchestrator import model_limits_from_settings


def _ctx() -> object:
    return new_call_context(project_id="prj_test", actor="test")


def _limits(**kw: object) -> ModelCallLimits:
    base = dict(
        max_calls=None, max_cost=None, max_cost_total=None,
        price_per_1k_in=0.0003, price_per_1k_out=0.0012,
        currency="USD", est_tokens_in=1000, est_tokens_out=500,
        soft_limit=None, hard_limit=None, soft_limit_handler=None,
    )
    base.update(kw)
    return ModelCallLimits(**base)  # type: ignore[arg-type]


def _session_record(cost: float, key: str = "s1") -> ExternalUsageRecord:
    return ExternalUsageRecord(
        idempotency_key=key, provider="p", operation=OP_CHAT,
        outcome=OUTCOME_SUCCEEDED, cost_confirmed=cost, currency="USD",
        cost_status=COST_CONFIRMED, task_id="t_new",
    )


def _history_record(cost: float, key: str = "h1") -> ExternalUsageRecord:
    return ExternalUsageRecord(
        idempotency_key=key, provider="p", operation=OP_CHAT,
        outcome=OUTCOME_SUCCEEDED, cost_confirmed=cost, currency="USD",
        cost_status=COST_CONFIRMED, task_id="t_old",
    )


# 复用一份能让校验通过的定价/估算覆盖，避免「设了上限却缺单价」被加载期拦下
_PRICE_OVERRIDES = {
    "PERSONA_MODEL_PRICE_IN_PER_1M": "0.3",
    "PERSONA_MODEL_PRICE_OUT_PER_1M": "1.2",
    "PERSONA_MODEL_PRICE_SOURCE": "https://example.invalid/pricing",
    "PERSONA_MODEL_PRICE_CHECKED_AT": "2026-10-16",
    "PERSONA_MODEL_EST_TOKENS_IN": "3000",
    "PERSONA_MODEL_EST_TOKENS_OUT": "2000",
}


class SoftHardBudgetTest(unittest.TestCase):

    def test_session_starts_at_zero(self) -> None:
        ledger = ExternalUsageLedger()
        self.assertEqual(ledger.session_occupied_cost(), 0.0)

    def test_history_not_polluting_new_task(self) -> None:
        # 历史账单 10 USD，本次 0；软限 2.0 不应触发（验收标准①）
        ledger = ExternalUsageLedger()
        ledger.hydrate([_history_record(10.0)], unknown_fallback_cost=0.0)
        self.assertAlmostEqual(ledger.occupied_cost(), 10.0)
        self.assertEqual(ledger.session_occupied_cost(), 0.0)
        err = check_model_budget(
            _ctx(), ledger=ledger, limits=_limits(soft_limit=2.0),
            task_id="t_new", calls_made=0, key="x")
        self.assertIsNone(err)

    def test_soft_limit_pauses_when_handler_declines(self) -> None:
        ledger = ExternalUsageLedger()
        ledger.settle(_session_record(2.5, key="s1"))  # 本次 2.5 > 软限 2.0
        declined = lambda _cost: False
        err = check_model_budget(
            _ctx(), ledger=ledger,
            limits=_limits(soft_limit=2.0, soft_limit_handler=declined),
            task_id="t_new", calls_made=0, key="x")
        self.assertIsNotNone(err)
        self.assertEqual(err.error.code, ErrorCode.BUDGET_PAUSED)
        self.assertEqual(err.error.category, ErrorCategory.BUDGET_EXCEEDED)

    def test_soft_limit_continues_and_raises(self) -> None:
        ledger = ExternalUsageLedger()
        ledger.settle(_session_record(2.5, key="s1"))
        accepted = lambda _cost: True
        lim = _limits(soft_limit=2.0, soft_limit_handler=accepted)
        err = check_model_budget(
            _ctx(), ledger=ledger, limits=lim, task_id="t_new",
            calls_made=0, key="x")
        self.assertIsNone(err)
        self.assertAlmostEqual(lim.soft_limit, 2.0 + SOFT_LIMIT_INCREMENT_USD)

    def test_hard_limit_breaks(self) -> None:
        ledger = ExternalUsageLedger()
        ledger.settle(_session_record(5.5, key="s1"))  # > 硬限 5.0
        err = check_model_budget(
            _ctx(), ledger=ledger, limits=_limits(hard_limit=5.0),
            task_id="t_new", calls_made=0, key="x")
        self.assertIsNotNone(err)
        self.assertEqual(err.error.code, ErrorCode.BUDGET_EXCEEDED)

    def test_hard_takes_precedence_over_soft(self) -> None:
        ledger = ExternalUsageLedger()
        ledger.settle(_session_record(5.5, key="s1"))
        accepted = lambda _cost: True
        err = check_model_budget(
            _ctx(), ledger=ledger,
            limits=_limits(soft_limit=2.0, hard_limit=5.0,
                           soft_limit_handler=accepted),
            task_id="t_new", calls_made=0, key="x")
        self.assertIsNotNone(err)
        # 同时超软硬：硬优先熔断，不是暂停
        self.assertEqual(err.error.code, ErrorCode.BUDGET_EXCEEDED)

    def test_max_calls_still_blocks(self) -> None:
        ledger = ExternalUsageLedger()
        err = check_model_budget(
            _ctx(), ledger=ledger, limits=_limits(max_calls=3),
            task_id="t_new", calls_made=3, key="x")
        self.assertIsNotNone(err)
        self.assertEqual(err.error.code, ErrorCode.BUDGET_EXCEEDED)

    def test_unknown_price_blocks_when_cap_set(self) -> None:
        ledger = ExternalUsageLedger()
        # 有软限但无单价 → 估不出费用 → 拒绝发起
        err = check_model_budget(
            _ctx(), ledger=ledger, limits=ModelCallLimits(soft_limit=2.0),
            task_id="t_new", calls_made=0, key="x")
        self.assertIsNotNone(err)
        self.assertEqual(err.error.code, ErrorCode.BUDGET_EXCEEDED)

    def test_cli_override_sets_soft_limit(self) -> None:
        overrides = dict(_PRICE_OVERRIDES)
        overrides.update({
            "PERSONA_BUDGET_SOFT_LIMIT": "1.0",
            "PERSONA_BUDGET_HARD_LIMIT": "9.0",
            "PERSONA_MODEL_MAX_CALLS": "50",
        })
        loaded = load_settings(use_dotenv=False, overrides=overrides)
        self.assertFalse(is_err(loaded), msg="overrides 应能通过校验")
        lim = model_limits_from_settings(loaded.value)
        self.assertAlmostEqual(lim.soft_limit, 1.0)
        self.assertAlmostEqual(lim.hard_limit, 9.0)
        self.assertEqual(lim.max_calls, 50)

    def test_legacy_fallback_when_only_old_keys(self) -> None:
        overrides = dict(_PRICE_OVERRIDES)
        overrides.update({
            "PERSONA_MODEL_MAX_COST": "0.25",
            "PERSONA_MODEL_MAX_COST_PER_TASK": "0.25",
        })
        loaded = load_settings(use_dotenv=False, overrides=overrides)
        self.assertFalse(is_err(loaded))
        lim = model_limits_from_settings(loaded.value)
        # 仅设遗留上限、未设新软硬键 → 回退到遗留单层
        self.assertAlmostEqual(lim.soft_limit, 0.25)
        self.assertIsNone(lim.hard_limit)
        self.assertAlmostEqual(lim.max_cost, 0.25)

    def test_real_env_loads_soft_hard(self) -> None:
        # 真实 .env 含 PERSONA_BUDGET_SOFT_LIMIT=2.00 / HARD=5.00 / MAX_CALLS=200
        loaded = load_settings(use_dotenv=True)
        self.assertFalse(is_err(loaded))
        lim = model_limits_from_settings(loaded.value)
        self.assertAlmostEqual(lim.soft_limit, 2.0)
        self.assertAlmostEqual(lim.hard_limit, 5.0)
        self.assertEqual(lim.max_calls, 200)
        # 新机制生效时遗留上限不再设置（避免双重封顶）
        self.assertIsNone(lim.max_cost)
        self.assertIsNone(lim.max_cost_total)


if __name__ == "__main__":
    unittest.main()
