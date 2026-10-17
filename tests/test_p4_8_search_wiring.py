"""P4.8 / R3：Tavily 接线——**检索也受 `allow_real` 闸门管**＋执行前的调用清单。

# 为什么检索要单独接一遍

`TavilyProvider` 早在 P4.2 就写好了（search + extract、`_urlopen` 可替换、
20+ 项合规测试），但**装配层从来没有构造点**：`search_provider` 只能外部注入，
于是产品路径上永远只会告警 `SEARCH_PROVIDER_OFFLINE`。
"能力存在但装不起来"和"没有能力"是两回事，本文件补的是前半句。

# 两条不能破的边界

1. **同一把闸门管两类外呼**：`allow_real=False` 时既不建模型出口，也不建检索出口。
   否则"只授权模型"会顺手把检索打开——检索比模型更容易花超（按次计费）。
2. **`max_retries` 强制 0**：对按次计费的检索，重试 = 再买一次；
   配置里的 `max_retries=3` 不能自动带进来。

# 调用清单（`plan_calls`）

用户要求"授权前必须打印将发生几次检索 / 几次抓取 / 几次模型调用"。
它必须**可执行地**回答这件事，而不是给一句"要花点钱"。
本文件锁住四条：本地材料 ⇒ 0 检索；离线 ⇒ 0 检索；真实检索 ⇒ 次数来自
`plan_queries`；单目标 ⇒ 0 次对话（多目标才生成对话）。

全程离线：Tavily 只被**构造**，`urlopen` 被替换成断言失败的替身。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.result import Err, is_err, is_ok, unwrap
from persona.orchestrator import (
    WARNING_STACK_REAL_PROVIDER_BLOCKED,
    WARNING_STACK_SEARCH_OFFLINE,
    WARNING_STACK_SEARCH_PROVIDER_UNAVAILABLE,
    StackOptions,
    TargetSpec,
    build_business_stack,
    plan_calls,
    register_targets,
    search_provider_from_settings,
)
from persona.providers.mock import FakeProvider
from persona.providers.tavily import TavilyProvider
from persona.storage.sqlite import SqliteStorage


def _online_settings():
    """合法的联网检索配置（假密钥；任何真实请求都会被 `urlopen` 替身拦住）。"""
    return test_settings(
        offline_only=False,
        provider_name="tavily",
        **{"secrets.search_api_key": "tvly-test-not-a-real-key"},
    )


def _model_only_key_settings():
    """有模型密钥、**没有**检索密钥：用来测"配了检索服务商但缺密钥"。"""
    return test_settings(
        offline_only=False,
        provider_name="tavily",
        **{"secrets.provider_api_key": "sk-test-not-a-real-key"},
    )


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


class _StackCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p48-search-")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "s.db"

    def _build(self, settings, *, allow_real=False, build_chat=False, **kwargs):
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        ctx = new_call_context(project_id="prj_p48", actor="p48-test")
        built = build_business_stack(
            settings, ctx, storage=storage,
            options=StackOptions(build_search_provider=True,
                                 build_chat_provider=build_chat),
            allow_real=allow_real, **kwargs)
        self.assertTrue(is_ok(built), f"装配失败：{getattr(built, 'error', '')}")
        stack = built.value
        targets = unwrap(register_targets(storage, stack.ctx, [
            TargetSpec("阿灯", "FICTIONAL_CHARACTER", "雾港灯会", "2020 动画改编版")]))
        return stack, targets


# ==========================================================================
# 一、`search_provider_from_settings`（只构造，不发请求）
# ==========================================================================


class SearchProviderFromSettingsTest(unittest.TestCase):
    def test_empty_provider_name_is_refused(self):
        """默认 `provider_name=fake` ⇒ 不构造（安全默认：什么都不配就离线）。"""
        result = search_provider_from_settings(test_settings())
        self.assertTrue(is_err(result))
        self.assertIn("未启用真实检索", result.error.message)

    def test_unknown_provider_is_refused(self):
        result = search_provider_from_settings(
            test_settings(provider_name="some-search-api"))
        self.assertTrue(is_err(result))
        self.assertIn("已登记的联网检索服务商", result.error.message)

    def test_missing_key_is_refused_without_constructing(self):
        settings = _model_only_key_settings()
        result = search_provider_from_settings(settings)
        self.assertTrue(is_err(result))
        self.assertIn("未设置检索密钥", result.error.message)

    def test_valid_config_builds_tavily_without_network(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("构造 ≠ 调用：不得联网")):
            result = search_provider_from_settings(_online_settings())
        self.assertTrue(is_ok(result))
        provider = unwrap(result)
        self.assertIsInstance(provider, TavilyProvider)
        # 按次计费：重试 = 再买一次，配置里的 max_retries 不得带进来
        self.assertEqual(provider.config.max_retries, 0)

    def test_key_never_leaks_into_repr_or_error(self):
        settings = _online_settings()
        blob = repr(search_provider_from_settings(settings))
        self.assertNotIn("tvly-test-not-a-real-key", blob)
        # 缺密钥时也不能把"别的密钥"带进错误信息
        err = search_provider_from_settings(_model_only_key_settings())
        self.assertNotIn("tvly", err.error.message)
        self.assertNotIn("sk-test-not-a-real-key", err.error.message)


# ==========================================================================
# 二、装配层：同一把闸门管住检索
# ==========================================================================


class SearchGateTest(_StackCase):
    def test_default_does_not_build_a_search_provider(self):
        stack, _targets = self._build(_online_settings())
        self.assertIsInstance(stack.research.deps.provider, FakeProvider)
        codes = {w.code for w in stack.warnings}
        self.assertIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)
        self.assertIn(WARNING_STACK_SEARCH_OFFLINE, codes)

    def test_default_never_touches_the_network(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("默认禁止真实外呼：不得联网")):
            stack, _targets = self._build(_online_settings())
        self.assertIsInstance(stack.research.deps.provider, FakeProvider)

    def test_allow_real_builds_the_search_provider(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("授权构造 ≠ 立即联网")):
            stack, _targets = self._build(_online_settings(), allow_real=True)
        self.assertIsInstance(stack.research.deps.provider, TavilyProvider)
        codes = {w.code for w in stack.warnings}
        self.assertNotIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)
        self.assertNotIn(WARNING_STACK_SEARCH_OFFLINE, codes)

    def test_allow_real_without_config_degrades_honestly(self):
        """授权不等于有能力：配置没给检索服务商 ⇒ 降级 + 告警，不伪造 Provider。"""
        stack, _targets = self._build(test_settings(), allow_real=True)
        self.assertIsInstance(stack.research.deps.provider, FakeProvider)
        codes = {w.code for w in stack.warnings}
        self.assertIn(WARNING_STACK_SEARCH_PROVIDER_UNAVAILABLE, codes)
        self.assertNotIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)

    def test_injected_provider_wins_over_config(self):
        """显式注入优先：注入桩时不去按配置建真实出口（也不会报被闸门拦下）。"""
        stack, _targets = self._build(_online_settings(), search_provider=FakeProvider())
        self.assertIsInstance(stack.research.deps.provider, FakeProvider)
        codes = {w.code for w in stack.warnings}
        self.assertNotIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)

    def test_config_research_limits_reach_the_research_stage(self):
        """配置里的 `research.*` 必须真的进到研究阶段（R3 顺手修的老缺口）。

        此前装配层把 `limits=None` 传下去，研究阶段回落到模块默认值（5/5/3），
        `PERSONA_RESEARCH_MAX_QUERIES/MAX_EXTRACTS` 在产品路径上被**静默忽略**。
        """
        settings = test_settings(**{"research.max_extracts": 1,
                                    "research.max_queries": 2})
        stack, _targets = self._build(settings)
        limits = stack.research.deps.effective_limits
        self.assertEqual((limits.max_queries, limits.max_extracts), (2, 1))


# ==========================================================================
# 三、调用清单：授权前必须说清"几次、哪类、多少钱"
# ==========================================================================


class CallPlanTest(_StackCase):
    def test_local_materials_mean_no_search_and_no_extract(self):
        stack, targets = self._build(test_settings())
        plan = plan_calls(stack, targets, materials=[object()])
        self.assertEqual(plan.searches, 0)
        self.assertEqual(plan.extracts_upper_bound, 0)
        self.assertTrue(any("不检索、不抓取" in r for r in plan.reasons))

    def test_offline_stack_reports_zero_calls(self):
        stack, targets = self._build(test_settings())
        plan = plan_calls(stack, targets)
        self.assertEqual((plan.searches, plan.extracts_upper_bound,
                          plan.extractions, plan.dialogues), (0, 0, 0, 0))
        self.assertFalse(plan.search_online)
        self.assertFalse(plan.model_online)

    def test_online_search_plan_counts_queries_and_upper_bound(self):
        stack, targets = self._build(_online_settings(), allow_real=True)
        plan = plan_calls(stack, targets)
        self.assertTrue(plan.search_online)
        self.assertEqual(plan.searches, 1, "单目标无话题 ⇒ 模板只生成 1 条查询")
        self.assertGreater(plan.extracts_upper_bound, 0)
        # basic 口径：检索 1 credit/次 + 抓取 0.2 credit/条
        expected = 1.0 + plan.extracts_upper_bound * 0.2
        self.assertAlmostEqual(plan.est_search_credits, expected, places=6)

    def test_single_target_has_no_dialogue_calls(self):
        stack, targets = self._build(test_settings())
        plan = plan_calls(stack, targets, max_rounds=3)
        self.assertEqual(plan.dialogues, 0, "单目标按语义跳过对话，不得虚报调用")

    def test_multi_target_dialogue_calls_are_bounded(self):
        """D-4：对话次数 = **轮数**（每轮 1 个 turn），不是"目标数 × 轮数"。

        此前按后者报，2 目标 × 2 轮被报成 4 次，实际只发 2 次——
        清单偏大会让人以为"还早"，等真的超预算才发现口径错了。
        """
        stack, _targets = self._build(_online_model_settings(), allow_real=True,
                                      build_chat=True)
        targets = unwrap(register_targets(stack.storage, stack.ctx, [
            TargetSpec("阿灯", "FICTIONAL_CHARACTER", "雾港灯会", "2020 动画改编版"),
            TargetSpec("阿雾", "FICTIONAL_CHARACTER", "雾港灯会", "2020 动画改编版")]))
        plan = plan_calls(stack, targets, max_rounds=2)
        self.assertEqual(plan.dialogues, 2, "对话 = 轮数（每轮 1 个 turn）")
        self.assertEqual(plan.extractions, 1, "抽取仍是一次批量")

    def test_model_cost_estimate_uses_configured_unit_price(self):
        """真实模型下：清单的金额 = 单次估算 × 调用次数（不是拍脑袋的数）。"""
        settings = _online_model_settings()
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("只构造清单，不得联网")):
            stack, targets = self._build(settings, allow_real=True, build_chat=True)
            plan = plan_calls(stack, targets, materials=[object()])
        self.assertEqual(plan.extractions, 1, "抽取是一次批量调用")
        # 配置单价按"每 100 万 token"：3000/1e6*0.3 + 2000/1e6*1.2 = 0.0033 USD
        self.assertAlmostEqual(plan.est_model_cost, 0.0033, places=6)
        self.assertEqual(plan.currency, "USD")

    def test_lines_are_printable_and_state_the_boundary(self):
        stack, targets = self._build(_online_settings(), allow_real=True)
        lines = plan_calls(stack, targets).as_lines()
        blob = "\n".join(lines)
        self.assertIn("检索", blob)
        self.assertIn("抓取正文", blob)
        self.assertIn("模型调用", blob)
        self.assertIn("清单之外不会有任何调用", blob)
        self.assertIn("credit", blob)


# ==========================================================================
# 四、CLI 是否真的把"建不建检索出口"传下去
# ==========================================================================


class RegisteredTargetQueryTextTest(_StackCase):
    """登记的目标必须把作品名带进检索词（P4.8 R4 真实运行前 dry-run 抓到的缺陷）。

    症状：`register_targets` 把作品名写进了实体载荷，却只给
    `PersonQueryTarget` 填了 `display_name`，于是 `plan_queries` 拿不到
    `work_titles`，真实检索退化成查「阿灯」「阿雾」这种两字人名
    （实测查回来全是同名噪声）。CLI 又强制要求 `--work-title` ——
    声明了却不用，等于让用户白填。
    """

    def test_registered_target_carries_work_title_and_version(self):
        stack, targets = self._build(test_settings())
        target = targets[0]
        self.assertEqual(target.work_titles, ["雾港灯会"])
        self.assertEqual(target.adaptation_version, "2020 动画改编版")
        self.assertEqual(target.entity_type, "FICTIONAL_CHARACTER")

    def test_query_text_contains_the_work_title(self):
        stack, targets = self._build(test_settings())
        planned = unwrap(stack.research.plan_queries(stack.ctx, targets))
        self.assertEqual(len(planned), 1, "无话题 ⇒ 每目标 1 条身份查询")
        self.assertEqual(planned[0].query_text, "阿灯 雾港灯会",
                         "基础辨识词 = 名字 + 作品名（INTERFACES §5.2）")

    def test_real_person_without_work_title_stays_bare(self):
        """真人没有作品名：检索词只留名字，**不拼空串、不补占位**。"""
        stack = self._build(test_settings())[0]
        targets = unwrap(register_targets(stack.storage, stack.ctx, [
            TargetSpec("某公众人物", "REAL_PERSON")]))
        self.assertEqual(targets[0].work_titles, [])
        planned = unwrap(stack.research.plan_queries(stack.ctx, targets))
        self.assertEqual(planned[0].query_text, "某公众人物")

    def test_query_target_passes_work_titles_through(self):
        """`_query_target` 不能把已有 `work_titles` 清空（这正是缺陷的成因）。"""
        from persona.orchestrator.assembly import _query_target
        stack, targets = self._build(test_settings())
        converted = _query_target(targets[0])
        self.assertEqual(converted.work_titles, ["雾港灯会"])
        self.assertEqual(converted.adaptation_version, "2020 动画改编版")


class CliBuildSearchFlagTest(unittest.TestCase):
    """`business run` 只有在**不给本地材料**时才构造检索出口。

    理由：`--material` 走的是材料通道，研究阶段**不检索**；
    既然不发检索请求，就没有理由建一个检索出口（少一个可能被误用的对象）。
    """

    def setUp(self) -> None:
        import os
        self._tmp = tempfile.TemporaryDirectory(prefix="p48-cli-")
        self.addCleanup(self._tmp.cleanup)
        self._saved = {k: os.environ.get(k) for k in ("PERSONA_DATA_DIR",)}
        os.environ["PERSONA_DATA_DIR"] = self._tmp.name
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        import os
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def _capture(self, extra: list[str]) -> dict:
        from persona.cli.main import main as cli_main
        captured: dict = {}

        def fake_build(settings, ctx, **kwargs):
            captured.update(kwargs)
            return Err(mock.MagicMock())

        argv = ["business", "run", "--names", "阿灯",
                "--work-title", "雾港灯会", "--adaptation-version",
                "2020 动画改编版", "--no-dotenv"] + extra
        with mock.patch("persona.orchestrator.build_business_stack", fake_build):
            cli_main(argv)
        return captured.get("options").__dict__ if captured.get("options") else {}

    def test_run_without_materials_asks_for_the_search_provider(self):
        options = self._capture([])
        # 装配失败在业务链之前，但 StackOptions 已经传下去了
        self.assertTrue(options.get("build_search_provider"))

    def test_run_with_materials_does_not_build_a_search_provider(self):
        material = Path(self._tmp.name) / "m.txt"
        material.write_text("阿灯在灯会上逐盏点亮灯芯，直到最后一盏。",
                            encoding="utf-8", newline="\n")
        options = self._capture(["--material", str(material)])
        self.assertFalse(options.get("build_search_provider"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()