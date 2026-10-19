"""P5.1 真跑暴露的两个装配层缺陷（D-A / D-B）的回归测试。

# 这两个缺陷的共同形状

**"能力只存在于测试注入路径上，产品路径从来没接"**——
单元测试全部通过，因为测试都**显式注入**了依赖；而 `persona run` 从不注入，
于是默认值为 `None` / `FakeProvider`，功能静默失效。
2026-10-19 第一次受控真跑才把它们逼出来：

- **D-A**：`dialogue` 在 chat provider 定下来**之前**就构造好了，
  写死 `dialogue_provider or FakeProvider()` → 真跑 DIALOGUE_RUN 必失败
  （FakeProvider 要预登记的 `chat:dialogue_turn:<entity_id>` fixture）。
- **D-B**：`StackOptions.extraction_strategy` 默认 `None`，只有测试注入
  `TopNExtractionStrategy()` → 产品路径**从来不抓正文**，
  `PERSONA_RESEARCH_MAX_EXTRACTS` 是死配置，清单却照着它打"上限 3 次"（假账）。

本文件全程离线：真实 Provider 只被**构造**，`urlopen` 被替换成断言失败的替身。
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from persona.cli.main import _print_run_plan
from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.result import is_ok, unwrap
from persona.modules.research import TopNExtractionStrategy
from persona.orchestrator import (
    WARNING_STACK_DIALOGUE_OFFLINE,
    WARNING_STACK_EXTRACTION_DISABLED,
    StackOptions,
    TargetSpec,
    build_business_stack,
    plan_calls,
    register_targets,
)
from persona.providers.mock import FakeProvider
from persona.providers.tavily import TavilyProvider
from persona.storage.sqlite import SqliteStorage


def _online_model_settings():
    """真实模型 + 真实检索（两者都配齐，假密钥，不联网）。"""
    return test_settings(
        offline_only=False,
        provider_name="tavily",
        **{
            "model.provider": "deepseek",
            "secrets.provider_api_key": "sk-test-not-a-real-key",
            "secrets.search_api_key": "tvly-test-not-a-real-key",
            "model.price_in_per_1m": 0.3,
            "model.price_out_per_1m": 1.2,
            "model.price_source": "https://example.invalid/price",
            "model.est_tokens_in": 3000,
            "model.est_tokens_out": 2000,
            "model.currency": "USD",
        },
    )


def _search_only_settings():
    """只配检索、不配模型密钥：用来验证"模型出口缺失"时对话会**说出来**。"""
    return test_settings(
        offline_only=False,
        provider_name="tavily",
        **{"secrets.search_api_key": "tvly-test-not-a-real-key"},
    )


class _StubExtractor:
    """只用来让 `plan_calls` 认定"模型在线"：它看的只是 `provider` 是不是桩。"""

    def __init__(self) -> None:
        self.provider = object()   # 非 FakeProvider ⇒ model_online = True


class _AssemblyCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p51-asm-")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "s.db"

    def _build(self, settings, *, allow_real=False, build_chat=True,
               options=None, **kwargs):
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        ctx = new_call_context(project_id="prj_p51", actor="p51-test")
        built = build_business_stack(
            settings, ctx, storage=storage,
            options=options or StackOptions(build_search_provider=True,
                                            build_chat_provider=build_chat),
            allow_real=allow_real, **kwargs)
        self.assertTrue(is_ok(built), f"装配失败：{getattr(built, 'error', '')}")
        return built.value


# ==========================================================================
# 一、D-A：对话阶段必须拿到真实聊天 Provider
# ==========================================================================


class DialogueProviderWiringTest(_AssemblyCase):
    def test_real_run_gets_a_real_chat_provider(self):
        """产品路径（`run`：build_chat=True + allow_real）下对话不能再用桩。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(_online_model_settings(), allow_real=True)
        provider = stack.dialogue.deps.provider
        self.assertNotIsInstance(provider, FakeProvider)
        self.assertNotIn(WARNING_STACK_DIALOGUE_OFFLINE,
                         {w.code for w in stack.warnings})

    def test_dialogue_shares_the_extractor_provider(self):
        """对话和抽取调的是**同一个**模型出口：没道理一个真实、一个走桩。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(_online_model_settings(), allow_real=True)
        self.assertIs(stack.dialogue.deps.provider,
                      stack.claim_extractor.provider)

    def test_fallback_is_announced_not_silent(self):
        """没有真实出口时仍然回落 FakeProvider，但**必须报出来**（不许再静默）。"""
        stack = self._build(test_settings(), allow_real=False)
        self.assertIsInstance(stack.dialogue.deps.provider, FakeProvider)
        message = " ".join(w.message for w in stack.warnings
                           if w.code == WARNING_STACK_DIALOGUE_OFFLINE)
        self.assertIn("DIALOGUE_RUN", message, "告警要点明会在哪个阶段失败")
        self.assertIn("fixture", message)

    def test_injected_dialogue_provider_wins(self):
        """显式注入优先（测试要塞固定台词），且不得报"没有 Provider"。"""
        injected = FakeProvider()
        stack = self._build(_online_model_settings(), allow_real=True,
                            dialogue_provider=injected)
        self.assertIs(stack.dialogue.deps.provider, injected)
        self.assertNotIn(WARNING_STACK_DIALOGUE_OFFLINE,
                         {w.code for w in stack.warnings})

    def test_missing_model_key_says_so_at_assembly_time(self):
        """配了检索、没配模型密钥：装配期就告警，而不是等 DIALOGUE_RUN 才炸。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(_search_only_settings(), allow_real=True)
        self.assertIsInstance(stack.dialogue.deps.provider, FakeProvider)
        self.assertIn(WARNING_STACK_DIALOGUE_OFFLINE,
                      {w.code for w in stack.warnings})


# ==========================================================================
# 二、D-B：正文抓取策略在产品路径上必须真的装上
# ==========================================================================


class ExtractionStrategyWiringTest(_AssemblyCase):
    def test_real_search_gets_a_default_strategy(self):
        """授权真实检索 ⇒ 默认装上 `TopNExtractionStrategy`，不靠调用方记得注入。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(_online_model_settings(), allow_real=True)
        self.assertIsInstance(stack.research.deps.extraction_strategy,
                              TopNExtractionStrategy)
        self.assertNotIn(WARNING_STACK_EXTRACTION_DISABLED,
                         {w.code for w in stack.warnings})

    def test_offline_stack_does_not_fake_extraction(self):
        """没授权 ⇒ 不装策略，但必须**明确告警**（否则清单会照配置打假账）。"""
        stack = self._build(_online_model_settings(), allow_real=False)
        self.assertIsNone(stack.research.deps.extraction_strategy)
        message = " ".join(w.message for w in stack.warnings
                           if w.code == WARNING_STACK_EXTRACTION_DISABLED)
        self.assertIn("PERSONA_RESEARCH_MAX_EXTRACTS", message)
        self.assertIn("不生效", message)

    def test_explicit_strategy_is_kept(self):
        injected = TopNExtractionStrategy()
        stack = self._build(
            _online_model_settings(), allow_real=True,
            options=StackOptions(build_search_provider=True,
                                 build_chat_provider=True,
                                 extraction_strategy=injected))
        self.assertIs(stack.research.deps.extraction_strategy, injected)

    def test_limits_still_come_from_config(self):
        """装上策略 ≠ 无视配置：`max_extracts` 仍然按配置走（R3 的老要求）。"""
        settings = test_settings(
            offline_only=False, provider_name="tavily",
            **{"secrets.search_api_key": "tvly-test-not-a-real-key",
               "research.max_extracts": 2, "research.max_queries": 3})
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(settings, allow_real=True, build_chat=False)
        self.assertEqual(stack.research.deps.effective_limits.max_extracts, 2)
        self.assertEqual(stack.research.deps.effective_limits.max_queries, 3)


# ==========================================================================
# 三、装配事实可读：清单据此打印，不许照配置猜
# ==========================================================================


class DescribeHonestyTest(_AssemblyCase):
    def test_describe_exposes_both_facts(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            online = self._build(_online_model_settings(), allow_real=True)
        components = online.describe()["components"]
        self.assertEqual(components["extraction_strategy"],
                         "TopNExtractionStrategy")
        self.assertNotEqual(components["dialogue_provider"], "FakeProvider")

        offline = self._build(_online_model_settings(), allow_real=False)
        components = offline.describe()["components"]
        self.assertIsNone(components["extraction_strategy"])
        self.assertEqual(components["dialogue_provider"], "FakeProvider")


class PlanPrintingHonestyTest(_AssemblyCase):
    """清单必须反映**装配结果**：没装策略就打印 0，不许打"上限 3 次"。"""

    def _plan_text(self, stack, targets) -> str:
        plan = plan_calls(stack, targets)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _print_run_plan(plan, stack, names=["阿灯"], work_title="雾港灯会",
                            adaptation="原著", adaptation_defaulted=False,
                            vocabulary_name="l2", vocabulary_count=42,
                            budget=0.05, settings=stack.settings)
        return buf.getvalue()

    def _targets(self, stack, count: int = 1):
        names = ["阿灯", "阿雾", "阿桥"]
        return unwrap(register_targets(stack.storage, stack.ctx, [
            TargetSpec(names[i], "FICTIONAL_CHARACTER", "雾港灯会", "原著")
            for i in range(count)]))

    def test_disabled_extraction_prints_zero_and_says_why(self):
        stack = self._build(_online_model_settings(), allow_real=False)
        text = self._plan_text(stack, self._targets(stack))
        self.assertIn("0（未启用正文抓取", text)
        self.assertIn("不生效", text)
        self.assertNotIn("上限 3 次", text)

    def test_enabled_extraction_prints_the_upper_bound(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(_online_model_settings(), allow_real=True)
        text = self._plan_text(stack, self._targets(stack))
        plan = plan_calls(stack, self._targets(stack))
        self.assertIn(f"上限 {plan.extracts_upper_bound} 次", text)
        self.assertNotIn("未启用正文抓取", text)

    def test_fake_dialogue_provider_is_flagged_in_the_plan(self):
        """清单答应了"模拟对话 N 次"，就得同时说明这一步其实会失败。

        构造"模型在线、但对话回落 FakeProvider"：显式注入抽取器（其 provider
        不是桩）⇒ `plan_calls` 认为模型在线 ⇒ 报出对话次数；而 chat_provider
        没有构造 ⇒ 对话阶段仍是 FakeProvider。这正是 D-A 修复前的形状。
        """
        stack = self._build(_online_model_settings(), allow_real=False,
                            build_chat=False, claim_extractor=_StubExtractor())
        targets = self._targets(stack, count=2)   # 多目标才有对话
        self.assertGreater(plan_calls(stack, targets).dialogues, 0)
        text = self._plan_text(stack, targets)
        self.assertIn("没有真实聊天 Provider", text)
        self.assertIn("DIALOGUE", text)


# ==========================================================================
# 四、边界：装上策略也不能把离线栈变成联网栈
# ==========================================================================


class NoSilentNetworkTest(_AssemblyCase):
    def test_assembling_with_strategy_never_calls_out(self):
        """装策略属于"构造"，不是"调用"：装配全程不得碰网络。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("装配期不得联网")):
            stack = self._build(_online_model_settings(), allow_real=True)
            self.assertIsInstance(stack.research.deps.provider, TavilyProvider)
            self.assertIsNotNone(stack.research.deps.extraction_strategy)

    def test_no_warning_mentions_the_fake_keys(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造，不得联网")):
            stack = self._build(_online_model_settings(), allow_real=True)
        blob = " ".join(w.message for w in stack.warnings)
        self.assertNotIn("sk-test-not-a-real-key", blob)
        self.assertNotIn("tvly-test-not-a-real-key", blob)
