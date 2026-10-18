"""P4.4-B：服务商决策、预算控制与 gate 处置的测试。

对应用户第六步「离线测试」清单：

| 验收标准 | 落点 |
| --- | --- |
| 配置可集中加载，非法值被拒 | `TestModelConfig` |
| 密钥不出现在代码/测试/日志/报告 | `TestSecretInjection` |
| 币种统一为 USD，原币价格与换算依据有记录 | `TestModelConfig` + `TestLimitsMapping` |
| 预算闸门在发送前拦截，超限不扣费 | `TestBudgetGateBeforeSend` |
| pending 不进入下游，`require_gate_verdict=True` 生效 | `TestGateDisposal` |
| dry-run 打印一次构造好的 ChatRequest | `TestDryRun` |

# 本文件**没有**调用任何真实模型，也没有产生任何费用

所有"模型响应"都是本地固定字符串（复用 P4.4-A 的 `StubChatProvider`）；
本文件里的密钥一律是**假的占位值**（`SECRET_PLACEHOLDER`），且只用于验证
"不会被打印/回显"。测试素材沿用既有的合成虚构名（阿雾 / 阿灯 / 雾港 / 星灯，AC-16）。
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest import mock

from persona.cli.main import main as cli_main
from persona.config import (
    ENV_PROVIDER_API_KEY,
    NETWORK_MODEL_PROVIDER_NAMES,
    default_settings,
    describe_fields,
    load_settings,
    parse_dotenv,
    set_path,
    validate_settings,
)
from persona.config.loader import WARNING_SECRET_IN_DOTENV
from persona.core.context import new_call_context
from persona.core.errors import ErrorCode
from persona.core.result import error_of, is_err, is_ok, unwrap
from persona.core.types import SourceMaterial
from persona.core.usage_ledger import (
    COST_CONFIRMED,
    COST_UNKNOWN,
    CURRENCY_UNKNOWN,
    OP_CHAT,
    OUTCOME_FAILED,
    SOURCE_OFFICIAL_RATE,
    ExternalUsageLedger,
    ModelCallLimits,
)
from persona.modules.extract_quality import QUALITY_LOW, QUALITY_VALID
from persona.modules.extraction import (
    REJECT_MATERIAL_NOT_GATE_PASSED,
    REJECT_MATERIAL_UNDECLARED,
    WARNING_CLAIM_PENDING_REVIEW,
    WARNING_MATERIAL_NOT_GATE_PASSED,
    WARNING_MATERIAL_SUMMARY_ONLY,
    WARNING_NO_MATERIALS,
    ProviderClaimExtractor,
    target_ids_of,
)
from persona.orchestrator import (
    build_dry_run_request,
    claim_extractor_from_settings,
    effective_max_cost,
    estimate_call_cost_usd,
    model_limits_from_settings,
    model_plan,
    sample_materials,
    unit_price_per_1k,
)
from persona.orchestrator.business import BusinessOrchestrator, BusinessRequest
from persona.providers.mock import network_blocked
# 复用 P4.4-A 的本地替身与素材构造，避免第二套实现
from tests.test_p4_4a_model_extraction import (  # noqa: F401
    KEY_FOG,
    MATERIAL_FOG,
    MATERIAL_LOW,
    MATERIAL_VALID,
    StubChatProvider,
    _item,
    _payload,
)
from tests.test_business_orchestrator import BusinessTestCase

PROJECT = "prj_p44b"

# 一个**假**的密钥占位值，仅用于验证"不会被打印"。不是任何真实凭据。
SECRET_PLACEHOLDER = "placeholder-not-a-real-key-0001"

# P4.4-B / P4.4-C 的服务商决策（与 .env.example 一致）
DEEPSEEK_PRICE_SOURCE = "https://api-docs.deepseek.com/quick_start/pricing"
MODEL_DECISION_ENV = {
    "PERSONA_MODEL_PROVIDER": "deepseek",
    "PERSONA_MODEL_ID": "deepseek-flash",
    "PERSONA_MODEL_CURRENCY": "USD",
    "PERSONA_MODEL_PRICE_TIER": "peak",
    "PERSONA_MODEL_PRICE_IN_PER_1M": "0.3",
    "PERSONA_MODEL_PRICE_OUT_PER_1M": "1.2",
    "PERSONA_MODEL_PRICE_SOURCE": DEEPSEEK_PRICE_SOURCE,
    "PERSONA_MODEL_PRICE_CHECKED_AT": "2026-10-16",
    "PERSONA_MODEL_MAX_COST": "0.25",
    "PERSONA_MODEL_MAX_COST_PER_TASK": "0.25",
    "PERSONA_MODEL_MAX_CALLS": "1",
    "PERSONA_MODEL_EST_TOKENS_IN": "3000",
    "PERSONA_MODEL_EST_TOKENS_OUT": "2000",
    "PERSONA_MODEL_REQUIRE_GATE_VERDICT": "true",
    "PERSONA_OFFLINE_ONLY": "false",
    "DEEPSEEK_API_KEY": SECRET_PLACEHOLDER,
}


def env_for(**overrides) -> dict[str, str]:
    """一份**齐全且可用**的模型配置 + 指定覆盖。"""
    env = dict(MODEL_DECISION_ENV)
    env.update({k: v for k, v in overrides.items() if v is not None})
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
    return env


def settings_for(**overrides):
    """加载一份配置；不合法就抛断言（测试代码写错，不是业务错误）。"""
    result = load_settings(use_dotenv=False, env=env_for(**overrides))
    if is_err(result):
        raise AssertionError(f"测试配置本身不合法：{error_of(result).message}")
    return unwrap(result)


def load_error(**overrides):
    """加载一份**应当失败**的配置，返回全部问题信息。"""
    result = load_settings(use_dotenv=False, env=env_for(**overrides))
    if is_ok(result):
        return None
    error = error_of(result)
    return [error.message, *error.details.get("all_problems", [])]


def _has(problems, needle: str) -> bool:
    return problems is not None and any(needle in p for p in problems)


# ==========================================================================
# 一、配置：集中加载 + 非法值被拒（P4.4-B 验收第 1、6 条）
# ==========================================================================


class TestModelConfig(unittest.TestCase):
    def test_defaults_disable_model_and_stay_valid_offline(self):
        """默认必须"不启用、不花钱、且配置合法"——否则 `cp .env.example .env` 就崩。"""
        settings = default_settings()
        self.assertFalse(settings.model.enabled())
        self.assertIsNone(settings.model.provider)
        self.assertTrue(validate_settings(settings) == [])
        self.assertEqual(settings.model.currency, "USD")

    def test_model_fields_are_registered_in_the_single_registry(self):
        paths = {field["path"] for field in describe_fields()}
        expected = {
            "model.provider", "model.model_id", "model.currency", "model.price_tier",
            "model.price_in_per_1m", "model.price_out_per_1m", "model.price_source",
            "model.price_checked_at",
            "model.max_cost", "model.max_cost_per_task", "model.max_calls",
            "model.est_tokens_in", "model.est_tokens_out", "model.require_gate_verdict",
        }
        self.assertTrue(expected.issubset(paths), expected - paths)
        for field in describe_fields():
            if field["path"].startswith("model."):
                self.assertTrue(field["env_key"].startswith("PERSONA_"))
                self.assertTrue(field["help"], f"{field['path']} 缺 help")
        # P4.4-C 拍板移除了 USD→CNY 折算通道：旧字段不许复活
        self.assertNotIn("model.fx_usd_cny", paths)
        self.assertNotIn("model.fx_source", paths)

    def test_env_example_loads_as_dotenv_and_is_offline_safe(self):
        """模板必须"复制了就能加载"——且默认不启用模型（不联网、不花钱）。"""
        path = Path(__file__).resolve().parents[1] / ".env.example"
        text = path.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / ".env"
            env_file.write_text(text, encoding="utf-8")
            result = load_settings(env={}, dotenv_path=env_file)
        self.assertTrue(is_ok(result), error_of(result).message if is_err(result) else "")
        settings = unwrap(result)
        self.assertFalse(settings.model.enabled())
        self.assertTrue(settings.offline_only)

    def test_env_example_records_the_deepseek_decision(self):
        """决策值必须落在配置模板里：服务商/模型/币种/单价/来源/核对日期/上限/gate。"""
        text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
            encoding="utf-8")
        parsed = parse_dotenv(text)
        self.assertEqual(parsed.get("PERSONA_MODEL_ID"), "deepseek-flash")
        self.assertEqual(parsed.get("PERSONA_MODEL_CURRENCY"), "USD")
        self.assertEqual(parsed.get("PERSONA_MODEL_PRICE_SOURCE"), DEEPSEEK_PRICE_SOURCE)
        self.assertEqual(parsed.get("PERSONA_MODEL_PRICE_CHECKED_AT"), "2026-10-16")
        # P4.4-C 拍板：单任务与总上限暂取同一个保守值；冒烟范围 max_calls=1
        self.assertEqual(parsed.get("PERSONA_MODEL_MAX_COST"), "0.25")
        self.assertEqual(parsed.get("PERSONA_MODEL_MAX_COST_PER_TASK"), "0.25")
        self.assertEqual(parsed.get("PERSONA_MODEL_MAX_CALLS"), "1")
        self.assertEqual(parsed.get("PERSONA_MODEL_REQUIRE_GATE_VERDICT"), "true")
        # 币种统一 USD 且不做换算：折算字段已从模板中移除
        self.assertNotIn("PERSONA_MODEL_FX_USD_CNY", parsed)
        self.assertNotIn("PERSONA_MODEL_FX_SOURCE", parsed)

    def test_env_example_never_contains_a_secret_key(self):
        text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
            encoding="utf-8")
        for key in parse_dotenv(text):
            self.assertNotIn("API_KEY", key, f"{key} 不得作为可解析的配置项出现")

    def test_configured_decision_is_accepted(self):
        settings = settings_for()
        self.assertTrue(settings.model.enabled())
        self.assertEqual(settings.model.currency, "USD")
        self.assertEqual(settings.model.price_in_per_1m, 0.3)
        self.assertEqual(settings.model.price_out_per_1m, 1.2)
        self.assertEqual(settings.model.price_tier, "peak")

    # ------------------------------------------------------------ 非法值

    def test_unknown_currency_and_tier_are_rejected(self):
        self.assertIsNotNone(load_error(PERSONA_MODEL_CURRENCY="eur"))
        self.assertIsNotNone(load_error(PERSONA_MODEL_CURRENCY="usd"))  # 不猜大小写
        self.assertIsNotNone(load_error(PERSONA_MODEL_PRICE_TIER="rush"))

    def test_unit_prices_stored_as_plain_strings_not_enum_members(self):
        """币种必须**原样流进账本**，不能变成 `ModelCurrency.USD` 这种没法对账的东西。"""
        limits = model_limits_from_settings(settings_for())
        self.assertEqual(limits.currency, "USD")
        self.assertIsInstance(limits.currency, str)

    def test_cost_ceiling_without_prices_is_rejected(self):
        problems = load_error(PERSONA_MODEL_PRICE_IN_PER_1M=None,
                             PERSONA_MODEL_PRICE_OUT_PER_1M=None)
        self.assertTrue(_has(problems, "输入/输出单价没有同时给出"), problems)

    def test_one_sided_price_is_rejected(self):
        problems = load_error(PERSONA_MODEL_PRICE_OUT_PER_1M=None)
        self.assertTrue(_has(problems, "输入/输出单价没有同时给出"), problems)

    def test_cost_ceiling_without_token_estimate_is_rejected(self):
        problems = load_error(PERSONA_MODEL_EST_TOKENS_IN="0",
                              PERSONA_MODEL_EST_TOKENS_OUT="0")
        self.assertTrue(_has(problems, "事前 token 估算全是 0"), problems)

    def test_price_without_source_is_rejected(self):
        problems = load_error(PERSONA_MODEL_PRICE_SOURCE=None)
        self.assertTrue(_has(problems, "没有记录单价的来源"), problems)

    def test_removed_fx_keys_are_unknown_not_fatal(self):
        """P4.4-C 拍板移除了 USD→CNY 折算通道：旧键降级为"未知项"，
        只告警不失败（D-082）——老配置不会因此加载不了，也不会被悄悄换算。"""
        result = load_settings(use_dotenv=False, env=env_for(
            PERSONA_MODEL_FX_USD_CNY="7.10", PERSONA_MODEL_FX_SOURCE="某来源"))
        self.assertTrue(is_ok(result))
        messages = " ".join(w.message for w in result.warnings)
        self.assertIn("PERSONA_MODEL_FX_USD_CNY", messages)
        self.assertNotIn("model.fx_usd_cny", str(unwrap(result).to_public_dict()))

    def test_per_task_ceiling_above_total_is_rejected(self):
        problems = load_error(PERSONA_MODEL_MAX_COST_PER_TASK="2.00")
        self.assertTrue(_has(problems, "大于总上限"), problems)

    def test_network_model_provider_is_rejected_when_offline(self):
        problems = load_error(PERSONA_OFFLINE_ONLY="true")
        self.assertTrue(_has(problems, "不能在 offline_only=true 下启用"), problems)

    def test_network_model_provider_requires_a_key(self):
        problems = load_error(DEEPSEEK_API_KEY=None,
                              PERSONA_PROVIDER_API_KEY=None)
        self.assertTrue(_has(problems, "未设置模型密钥"), problems)

    def test_gate_on_without_body_extraction_is_rejected(self):
        """交叉检查：要求门禁结论却不抓正文 = 材料永远进不了抽取器（模型永不跑）。"""
        problems = load_error(PERSONA_RESEARCH_MAX_EXTRACTS="0")
        self.assertTrue(_has(problems, "模型一次都不会被调用"), problems)
        # 关掉 gate 就不该报这条（摘要没有结论是正常的）
        self.assertIsNone(load_error(PERSONA_RESEARCH_MAX_EXTRACTS="0",
                                     PERSONA_MODEL_REQUIRE_GATE_VERDICT="false"))

    def test_gate_rule_only_applies_when_model_is_enabled(self):
        """默认（未启用模型）时，max_extracts=0 不应该被这条规则打回。"""
        result = load_settings(use_dotenv=False, env={
            "PERSONA_RESEARCH_MAX_EXTRACTS": "0"})
        self.assertTrue(is_ok(result), error_of(result).message if is_err(result) else "")

    def test_network_model_provider_names_are_declared(self):
        self.assertIn("deepseek", NETWORK_MODEL_PROVIDER_NAMES)


# ==========================================================================
# 二、密钥注入与"永不回显"（P4.4-B 验收第 4 条）
# ==========================================================================


class TestSecretInjection(unittest.TestCase):
    def test_deepseek_alias_feeds_the_model_key(self):
        settings = settings_for(PERSONA_PROVIDER_API_KEY=None)
        self.assertEqual(settings.secrets.provider_api_key, SECRET_PLACEHOLDER)

    def test_persona_key_wins_over_alias(self):
        settings = settings_for(PERSONA_PROVIDER_API_KEY="persona-key-0001")
        self.assertEqual(settings.secrets.provider_api_key, "persona-key-0001")

    def test_alias_written_into_dotenv_is_ignored_with_warning(self):
        """把别名写进 `.env` 也要被识别为"密钥进文件"：忽略 + 告警，不是静默忽略。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(f"DEEPSEEK_API_KEY={SECRET_PLACEHOLDER}\n",
                            encoding="utf-8")
            result = load_settings(env={}, dotenv_path=path)
        self.assertTrue(is_ok(result), "忽略密钥不应导致加载失败")
        self.assertIsNone(unwrap(result).secrets.provider_api_key)
        notes = [note for note in result.warnings
                 if note.code == WARNING_SECRET_IN_DOTENV]
        self.assertTrue(notes)
        self.assertNotIn(SECRET_PLACEHOLDER, " ".join(n.message for n in notes))

    def test_secret_never_in_public_dict_repr_or_plan(self):
        settings = settings_for()
        blobs = [str(settings), repr(settings),
                 json.dumps(settings.to_public_dict(), ensure_ascii=False, default=str),
                 json.dumps(model_plan(settings), ensure_ascii=False, default=str)]
        for blob in blobs:
            self.assertNotIn(SECRET_PLACEHOLDER, blob)

    def test_secret_status_reports_only_set_or_not(self):
        plan = model_plan(settings_for())
        self.assertIs(plan["secret_status"][ENV_PROVIDER_API_KEY], True)
        self.assertIs(plan["secret_status"]["PERSONA_SEARCH_API_KEY"], False)

    def test_short_secret_error_hides_the_value(self):
        result = load_settings(use_dotenv=False, env=env_for(
            PERSONA_PROVIDER_API_KEY="tiny"))
        self.assertTrue(is_err(result))
        self.assertNotIn("tiny", error_of(result).message)

    def test_cli_plan_and_dryrun_never_print_the_secret(self):
        env = {**os.environ, **MODEL_DECISION_ENV}
        env.pop("PERSONA_PROVIDER_API_KEY", None)
        for action in ("plan", "dryrun"):
            buffer = io.StringIO()
            with mock.patch.dict(os.environ, env, clear=True), \
                    redirect_stdout(buffer):
                code = cli_main(["model", action, "--no-dotenv"])
            self.assertEqual(code, 0, f"model {action} 应当成功退出")
            text = buffer.getvalue()
            self.assertNotIn(SECRET_PLACEHOLDER, text)
            self.assertIn("已设置（内容不显示）", text)

    def test_lowercase_persona_key_is_still_found(self):
        """变量名被改成小写也要能读到（本机实测出现过，见 loader `_env_lookup_ci`）。

        密钥读不到时上层只报"未设置"，用户看不出是大小写问题——所以读取路径
        必须大小写不敏感。
        """
        env = env_for()
        env.pop("DEEPSEEK_API_KEY")
        env["persona_provider_api_key"] = SECRET_PLACEHOLDER
        result = load_settings(use_dotenv=False, env=env)
        self.assertTrue(is_ok(result))
        self.assertEqual(unwrap(result).secrets.provider_api_key, SECRET_PLACEHOLDER)

    def test_lowercase_alias_is_still_found(self):
        """别名被改成小写（`deepseek_api_key`）同样要能读到。"""
        env = env_for()
        env.pop("DEEPSEEK_API_KEY")
        env["deepseek_api_key"] = SECRET_PLACEHOLDER
        result = load_settings(use_dotenv=False, env=env)
        self.assertTrue(is_ok(result))
        self.assertEqual(unwrap(result).secrets.provider_api_key, SECRET_PLACEHOLDER)

    def test_exact_case_wins_over_case_variant(self):
        """同一逻辑变量出现两种大小写时，精确名优先（顺序确定、可预期）。"""
        env = env_for(PERSONA_PROVIDER_API_KEY="persona-key-0001")
        env["persona_provider_api_key"] = "lowercase-variant-0002"
        result = load_settings(use_dotenv=False, env=env)
        self.assertTrue(is_ok(result))
        self.assertEqual(unwrap(result).secrets.provider_api_key, "persona-key-0001")


# ==========================================================================
# 三、配置 → 运行限制（币种统一 USD、单价换算只在一处）
# ==========================================================================


class TestLimitsMapping(unittest.TestCase):
    def test_unit_price_converted_from_1m_to_1k(self):
        self.assertEqual(unit_price_per_1k(0.3), 0.0003)
        self.assertEqual(unit_price_per_1k(1.2), 0.0012)
        self.assertIsNone(unit_price_per_1k(None))       # 缺单价 ≠ 0（D-060）

    def test_effective_ceiling_takes_the_stricter_one(self):
        # P4.4-C 拍板：两个上限暂取同一个值 → 生效的就是它
        model = settings_for().model
        self.assertEqual(effective_max_cost(model), 0.25)
        stricter = set_path(settings_for(), "model.max_cost_per_task", 0.10).model
        self.assertEqual(effective_max_cost(stricter), 0.10)
        only_task = set_path(settings_for(), "model.max_cost", None).model
        self.assertEqual(effective_max_cost(only_task), 0.25)
        only_total = set_path(settings_for(), "model.max_cost_per_task", None).model
        self.assertEqual(effective_max_cost(only_total), 0.25)
        none_set = set_path(set_path(settings_for(), "model.max_cost", None),
                            "model.max_cost_per_task", None).model
        self.assertIsNone(effective_max_cost(none_set))

    def test_limits_carry_currency_estimates_and_prices(self):
        settings = settings_for()
        limits = model_limits_from_settings(settings)
        self.assertIsInstance(limits, ModelCallLimits)
        self.assertEqual(limits.currency, "USD")
        self.assertEqual(limits.price_per_1k_in, 0.0003)
        self.assertEqual(limits.price_per_1k_out, 0.0012)
        self.assertEqual(limits.est_tokens_in, 3000)
        self.assertEqual(limits.est_tokens_out, 2000)
        self.assertEqual(limits.max_cost, 0.25)
        self.assertEqual(limits.max_calls, 1)      # P4.4-C 冒烟范围：1 次调用

    def test_currency_unknown_is_not_guessed(self):
        """币种没给 → 不许默认当成 USD（那会把 CNY 的账当 USD 记）。"""
        blanked = set_path(settings_for(), "model.currency", "")
        limits = model_limits_from_settings(blanked)
        self.assertEqual(limits.currency, CURRENCY_UNKNOWN)

    def test_estimate_call_cost_uses_the_configured_tier_price(self):
        """3000 in × 0.0003 + 2000 out × 0.0012 = 0.0009 + 0.0024 = 0.0033 USD。"""
        settings = settings_for()
        self.assertAlmostEqual(estimate_call_cost_usd(settings), 0.0033, places=9)
        self.assertTrue(model_plan(settings)["notes"])

    def test_estimate_is_none_when_prices_are_missing(self):
        blanked = set_path(set_path(settings_for(), "model.price_in_per_1m", None),
                           "model.price_out_per_1m", None)
        self.assertIsNone(estimate_call_cost_usd(blanked))

    def test_plan_shows_both_ceilings_and_the_effective_one(self):
        plan = model_plan(settings_for())
        self.assertEqual(plan["max_cost_total"], 0.25)
        self.assertEqual(plan["max_cost_per_task"], 0.25)
        self.assertEqual(plan["max_cost_effective"], 0.25)
        # P4.6 / D-B：两层上限**真的**各算各的（不再是"只执行一个、取更严的近似"），
        # 所以说明文字必须讲"两层都会被判断"，而不是旧的"暂取同一个值"。
        self.assertEqual(plan["max_cost_total_effective"], 0.25)
        self.assertTrue(any("两层都会被判断" in note for note in plan["notes"]))


# ==========================================================================
# 四、预算闸门：发送**之前**拦截（P4.4-B 验收第 2 条）
# ==========================================================================


class TestBudgetGateBeforeSend(unittest.TestCase):
    """用配置装配出的抽取器跑闸门；Provider 是本地替身，记录被调用次数。"""

    def setUp(self) -> None:
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")

    def _extractor(self, settings, provider, **kw):
        return claim_extractor_from_settings(settings, provider, **kw)

    def test_ceiling_without_price_blocks_before_send(self):
        """单价缺失 + 设了上限 → 拒绝自动发起请求；**一次都没有发出去**。"""
        broken = set_path(set_path(settings_for(), "model.price_in_per_1m", None),
                          "model.price_out_per_1m", None)
        provider = StubChatProvider(_payload([_item()]))
        result = self._extractor(broken, provider).extract(
            self.ctx, [MATERIAL_VALID])
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(provider.chat_calls, 0, "闸门必须拦在发送之前")

    def test_call_limit_blocks_before_send(self):
        settings = set_path(settings_for(), "model.max_calls", 0)
        provider = StubChatProvider(_payload([_item()]))
        result = self._extractor(settings, provider).extract(
            self.ctx, [MATERIAL_VALID])
        self.assertTrue(is_err(result))
        self.assertEqual(error_of(result).code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(provider.chat_calls, 0)

    def test_occupied_spend_blocks_the_next_call(self):
        """账本已占用的额度会把**下一次**调用拦在发送之前。

        上限取 0.004：单次估算 0.0033 放得进，但一次真实花费（0.0018）之后再估
        0.0033 就超了 → 第二次必须被拦，且 Provider 只被调用一次。
        """
        settings = set_path(settings_for(), "model.max_cost", 0.004)
        settings = set_path(settings, "model.max_cost_per_task", 0.004)
        ledger = ExternalUsageLedger()
        from persona.core.result import UsageDelta
        provider = StubChatProvider(
            _payload([_item()]),
            usage=UsageDelta(requests=1, tokens_in=2000, tokens_out=1000))
        extractor = self._extractor(settings, provider, ledger=ledger)
        first = extractor.extract(self.ctx, [MATERIAL_VALID])
        self.assertTrue(is_ok(first), "第一次应当放行（单次估算在上限内）")
        self.assertEqual(provider.chat_calls, 1)

        other = replace(MATERIAL_VALID, canonical_key="key_other")  # 换材料 → 换键
        second = extractor.extract(self.ctx, [other])
        self.assertTrue(is_err(second))
        self.assertEqual(error_of(second).code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(provider.chat_calls, 1, "超限不能再发第二次")

    def test_duplicate_intent_is_skipped_without_paying_again(self):
        provider = StubChatProvider(_payload([_item()]))
        extractor = self._extractor(settings_for(), provider)
        self.assertTrue(is_ok(extractor.extract(self.ctx, [MATERIAL_VALID])))
        again = extractor.extract(self.ctx, [MATERIAL_VALID])
        self.assertTrue(is_ok(again))
        self.assertEqual(provider.chat_calls, 1, "同一意图不得重复花钱")
        self.assertIn("MODEL_CALL_DUPLICATE_SKIPPED",
                      [w.code for w in unwrap(again).warnings])

    def test_success_records_confirmed_cost_in_usd(self):
        ledger = ExternalUsageLedger()
        from persona.core.result import UsageDelta
        provider = StubChatProvider(
            _payload([_item()]),
            usage=UsageDelta(requests=1, tokens_in=2000, tokens_out=1000))
        extractor = self._extractor(settings_for(), provider, ledger=ledger)
        self.assertTrue(is_ok(extractor.extract(self.ctx, [MATERIAL_VALID])))
        record = [r for r in ledger.records() if r.operation == OP_CHAT][0]
        self.assertEqual(record.cost_status, COST_CONFIRMED)
        self.assertEqual(record.cost_source, SOURCE_OFFICIAL_RATE)
        self.assertEqual(record.currency, "USD")
        # 2000×0.0003 + 1000×0.0012 = 0.0006 + 0.0012
        self.assertAlmostEqual(record.cost_confirmed, 0.0018, places=9)

    def test_failure_records_unknown_and_keeps_the_reservation(self):
        """失败 ≠ 免费：费用记 UNKNOWN（**不是 0**），预留转未知占位继续占额度。"""
        ledger = ExternalUsageLedger()
        provider = StubChatProvider(fail=True)
        extractor = self._extractor(settings_for(), provider, ledger=ledger)
        result = extractor.extract(self.ctx, [MATERIAL_VALID])
        self.assertTrue(is_err(result))
        record = [r for r in ledger.records() if r.operation == OP_CHAT][0]
        self.assertEqual(record.outcome, OUTCOME_FAILED)
        self.assertEqual(record.cost_status, COST_UNKNOWN)
        self.assertIsNone(record.cost_confirmed)
        self.assertGreater(ledger.totals().unknown_hold_cost, 0.0)

    def test_missing_usage_is_unknown_not_zero(self):
        provider = StubChatProvider(_payload([_item()]), usage=None)
        provider.usage = None
        ledger = ExternalUsageLedger()
        extractor = self._extractor(settings_for(), provider, ledger=ledger)
        self.assertTrue(is_ok(extractor.extract(self.ctx, [MATERIAL_VALID])))
        record = [r for r in ledger.records() if r.operation == OP_CHAT][0]
        self.assertEqual(record.cost_status, COST_UNKNOWN)
        self.assertIsNone(record.cost_confirmed)
        self.assertNotEqual(record.cost_confirmed, 0)

    def test_exception_settles_failed_then_propagates(self):
        ledger = ExternalUsageLedger()
        provider = StubChatProvider(raises=RuntimeError("boom"))
        extractor = self._extractor(settings_for(), provider, ledger=ledger)
        with self.assertRaises(RuntimeError):
            extractor.extract(self.ctx, [MATERIAL_VALID])
        record = [r for r in ledger.records() if r.operation == OP_CHAT][0]
        self.assertEqual(record.outcome, OUTCOME_FAILED)
        self.assertEqual(record.cost_status, COST_UNKNOWN)

    def test_ledger_is_recorded_through_the_storage_sink(self):
        """逐笔用量能落到既有账本出口（复用 P4.5 的 sink，不新建第二套）。"""
        from tests.test_p4_4a_model_extraction import StubUsageSink
        sink = StubUsageSink()
        provider = StubChatProvider(_payload([_item()]))
        extractor = self._extractor(settings_for(), provider, usage_sink=sink,
                                    task_id="tsk_p44b")
        self.assertTrue(is_ok(extractor.extract(self.ctx, [MATERIAL_VALID])))
        self.assertEqual(len(sink.recorded), 1)
        self.assertEqual(sink.recorded[0].currency, "USD")
        self.assertEqual(sink.recorded[0].task_id, "tsk_p44b")


# ==========================================================================
# 五、gate 处置（P4.4-B 验收第 3 条）
# ==========================================================================


class TestGateDisposal(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")

    def test_gate_on_rejects_undeclared_material_without_calling_the_model(self):
        provider = StubChatProvider(_payload([_item()]))
        extractor = claim_extractor_from_settings(settings_for(), provider)
        outcome = unwrap(extractor.extract(self.ctx, [MATERIAL_FOG]))  # 未声明
        self.assertEqual(provider.chat_calls, 0, "材料没通过准入就不该调用模型")
        self.assertEqual(outcome.claims, [])
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_MATERIAL_UNDECLARED])
        self.assertIn(WARNING_MATERIAL_NOT_GATE_PASSED,
                      [w.code for w in outcome.warnings])
        self.assertIn(WARNING_NO_MATERIALS, [w.code for w in outcome.warnings])

    def test_gate_off_admits_undeclared_material(self):
        """对照组：关掉 gate 开关，同样的材料会被放行（并显式告警）。"""
        off = set_path(settings_for(), "model.require_gate_verdict", False)
        provider = StubChatProvider(_payload([_item()]))
        outcome = unwrap(
            claim_extractor_from_settings(off, provider).extract(
                self.ctx, [MATERIAL_FOG]))
        self.assertEqual(provider.chat_calls, 1)
        self.assertEqual(len(outcome.claims), 1)
        self.assertIn("MATERIAL_QUALITY_UNDECLARED",
                      [w.code for w in outcome.warnings])

    def test_gate_on_rejects_low_quality_body(self):
        provider = StubChatProvider(_payload([_item()]))
        outcome = unwrap(
            claim_extractor_from_settings(settings_for(), provider).extract(
                self.ctx, [MATERIAL_LOW]))
        self.assertEqual(provider.chat_calls, 0)
        self.assertEqual([r.reason for r in outcome.rejected],
                         [REJECT_MATERIAL_NOT_GATE_PASSED])

    def test_valid_material_passes_the_gate(self):
        provider = StubChatProvider(_payload([_item()]))
        outcome = unwrap(
            claim_extractor_from_settings(settings_for(), provider).extract(
                self.ctx, [MATERIAL_VALID]))
        self.assertEqual(provider.chat_calls, 1)
        self.assertEqual(len(outcome.claims), 1)


class TestPendingBlocksDownstream(BusinessTestCase):
    """pending 必须**不进下游**，也不得被自动批准（P4.4-B 三.2/三.5）。"""

    def _targets(self):
        a = self.fx.entity("阿雾")
        b = self.fx.entity("阿灯")
        return a, b, [self.fx.target(a, "阿雾"), self.fx.target(b, "阿灯")]

    def test_pending_candidate_is_never_registered_as_evidence(self):
        a, _b, targets = self._targets()
        # 声称有原话却给不出摘录 → 严格校验判为"待审核"
        payload = _payload([_item(material_key=KEY_FOG, subject_entity_ids=[a],
                                  tier="VERBATIM", claim="阿雾负责看守雾港灯塔",
                                  confidence=0.6)])
        provider = StubChatProvider(payload)
        extractor = ProviderClaimExtractor(provider, require_gate_verdict=False)
        self.fx.orch = BusinessOrchestrator(self.fx._deps(extractor))
        with network_blocked():
            result = unwrap(self.fx.orch.run(
                self.fx.ctx, BusinessRequest(targets=targets, max_rounds=2)))

        self.assertEqual(provider.chat_calls, 1)
        self.assertEqual(result.evidence_ids, [], "pending 不得进入下游")
        self.assertIn(WARNING_CLAIM_PENDING_REVIEW,
                      [w.code for w in result.warnings])
        self.assertEqual(self.fx.count("EvidenceItem"), 0, "pending 不得变成证据")

    def test_snippet_only_chain_produces_evidence_under_strict_gate(self):
        """**P4.8 / D-6 改写**：只抓摘要的链路在严格 gate 下**应当产出证据**。

        这个用例以前断言的是反面（`chat_calls == 0`、0 条证据），并把那解释为
        "`require_gate_verdict=True` 的定义"。R4.1 实测证明那个解释是错的：
        7 条来源**全部真实相关**、正文抓取失败退回摘要 ⇒ 全部按"未声明"拒收
        ⇒ 0 证据。**这不是门禁有效，是门禁把"我们没抓到正文"当成了"内容不可用"**
        ——误杀，而且与目标是否真实存在无关。

        修法（用户拍板方案 ①）：摘要带 `SUMMARY_ONLY` 一等状态进入材料流；
        门禁只负责"亮结论"，不负责"判死刑"；最终可用性由证据层的 `tier` 决定。

        ⚠️ 判废的正文（`LOW_QUALITY` / `EMPTY`）**仍然**被挡在抽取器外——
        放行摘要不等于放松门禁，见 `tests/test_p4_8_summary_gate.py`。
        """
        a, _b, targets = self._targets()
        provider = StubChatProvider(_payload([_item(material_key=KEY_FOG,
                                                   subject_entity_ids=[a])]))
        extractor = ProviderClaimExtractor(provider, require_gate_verdict=True)
        self.fx.orch = BusinessOrchestrator(self.fx._deps(extractor))
        with network_blocked():
            result = unwrap(self.fx.orch.run(
                self.fx.ctx, BusinessRequest(targets=targets, max_rounds=2)))
        self.assertEqual(provider.chat_calls, 1, "摘要不再被判死刑：模型会被调用")
        self.assertEqual(len(result.evidence_ids), 1)
        self.assertEqual(self.fx.count("EvidenceItem"), 1)
        self.assertIn(WARNING_MATERIAL_SUMMARY_ONLY,
                      [w.code for w in result.warnings],
                      "放行摘要必须留痕，不能静默通过")


class TestConfiguredChainRegistersEvidence(BusinessTestCase):
    """装配层 + 编排器 + 真存储：合格候选最终落到证据表（候选仍需 Evidence 校验）。"""

    def test_configured_extractor_registers_a_valid_candidate(self):
        a = self.fx.entity("阿雾")
        targets = [self.fx.target(a, "阿雾")]
        payload = _payload([_item(material_key=KEY_FOG, subject_entity_ids=[a],
                                  claim="阿雾负责看守雾港灯塔")])
        provider = StubChatProvider(payload)
        # 用配置装配（而不是手工 new），且关掉严格 gate 以配合"只抓摘要"的夹具
        settings = set_path(settings_for(), "model.require_gate_verdict", False)
        extractor = claim_extractor_from_settings(settings, provider)
        self.fx.orch = BusinessOrchestrator(self.fx._deps(extractor))
        with network_blocked():
            result = unwrap(self.fx.orch.run(
                self.fx.ctx, BusinessRequest(targets=targets, max_rounds=2)))
        self.assertEqual(provider.chat_calls, 1)
        self.assertEqual(len(result.evidence_ids), 1)
        row = unwrap(self.fx.storage.get(self.fx.ctx, "EvidenceItem",
                                        result.evidence_ids[0]))
        self.assertEqual(row.get("claim"), "阿雾负责看守雾港灯塔")
        self.assertIn(row.get("source_id"), set(result.source_ids))


# ==========================================================================
# 六、dry-run（P4.4-B 实施步骤第 5 点）
# ==========================================================================


class TestDryRun(unittest.TestCase):
    def test_dry_run_builds_a_two_message_request(self):
        request, admitted, rejected = build_dry_run_request(settings_for())
        self.assertEqual([m.role for m in request.messages], ["system", "user"])
        self.assertEqual(request.model_hint, "deepseek-flash")
        self.assertEqual(request.response_format, "json_schema")
        self.assertIn("prompt_version", request.metadata)
        self.assertEqual(len(admitted), 1)
        self.assertEqual(rejected, [])

    def test_system_message_carries_rules_and_the_injection_boundary(self):
        request, _admitted, _rejected = build_dry_run_request(settings_for())
        system = request.messages[0].content
        self.assertIn("忽略", system)                 # 注入边界声明
        self.assertIn("WEB_CONTENT_BEGIN", system)
        self.assertIn("本次允许使用的主体", system)
        self.assertIn("pe_dryrun_awu", system)        # 白名单主体
        self.assertIn("不得自造 ID", system)
        self.assertIn("verbatim_quote", system)       # 输出 schema 嵌在规则里

    def test_user_message_carries_only_data_in_marked_blocks(self):
        request, admitted, _rejected = build_dry_run_request(settings_for())
        user = request.messages[1].content
        self.assertIn("<<WEB_CONTENT_BEGIN>>", user)
        self.assertIn("<<WEB_CONTENT_END>>", user)
        self.assertIn(f"[material_key={admitted[0].canonical_key}]", user)
        self.assertIn(admitted[0].snippet, user)

    def test_dry_run_never_sends_and_works_under_network_block(self):
        """dry-run 是纯构造：在"禁止一切真实网络"的环境里也必须能跑。"""
        with network_blocked():
            request, admitted, _rejected = build_dry_run_request(settings_for())
        self.assertTrue(request.messages)
        self.assertEqual(len(admitted), 1)

    def test_dry_run_applies_the_same_admission_as_the_real_run(self):
        low = SourceMaterial(source_id="src_low", title="低质量正文",
                             url="https://example.invalid/low",
                             canonical_key="key_low", snippet="导航 首页",
                             quality_status=QUALITY_LOW)
        request, admitted, rejected = build_dry_run_request(
            settings_for(), [low], [])
        self.assertEqual(admitted, [])
        self.assertEqual(len(rejected), 1)
        # 没有材料时也不能构造出"要发给模型的请求内容"
        self.assertNotIn("导航 首页", request.messages[1].content)

    def test_sample_material_is_labelled_synthetic(self):
        materials = sample_materials()
        self.assertIn("dry-run", materials[0].title)
        self.assertEqual(materials[0].quality_status, QUALITY_VALID)

    def test_target_ids_helper_is_the_single_shared_reading(self):
        """同一份 target 用对象或 dict 表达，读出来的 ID 必须一致。

        （P4.4-B 实施中被 dry-run 抓到过：幂等键认 dict，提示词白名单不认，
        结果是"白名单静默消失"。这里锁住唯一口径。）
        """
        from persona.modules.research import PersonQueryTarget
        obj = [PersonQueryTarget(entity_id="pe_a"), PersonQueryTarget(entity_id="pe_b")]
        as_dicts = [{"entity_id": "pe_a"}, {"entity_id": "pe_b"}]
        self.assertEqual(target_ids_of(obj), ["pe_a", "pe_b"])
        self.assertEqual(target_ids_of(as_dicts), ["pe_a", "pe_b"])
        self.assertEqual(target_ids_of(None), [])
        self.assertEqual(target_ids_of([]), [])

    def test_whitelist_absent_is_stated_explicitly(self):
        """没有目标时，系统消息必须**说出来**，不能静默降级为"没有约束"。"""
        request, _admitted, _rejected = build_dry_run_request(
            settings_for(), None, [])
        self.assertIn("没有给出允许的主体 ID 白名单", request.messages[0].content)

    def test_cli_dryrun_prints_the_request_and_says_it_did_not_send(self):
        env = {**os.environ, **MODEL_DECISION_ENV}
        env.pop("PERSONA_PROVIDER_API_KEY", None)
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), redirect_stdout(buffer):
            code = cli_main(["model", "dryrun", "--no-dotenv"])
        text = buffer.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("没有发送任何请求", text)
        self.assertIn("<<WEB_CONTENT_BEGIN>>", text)
        self.assertIn("本次允许使用的主体", text)
        self.assertIn("deepseek-flash", text)


if __name__ == "__main__":
    unittest.main()
