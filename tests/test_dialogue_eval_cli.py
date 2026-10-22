"""P6.1-0：`run_dialogue_eval.py` 的**温度参数**接线测试（离线，零外呼）。

为什么需要这个文件：P6.1-0 的核心风险是"评估全程 `temperature=0.0`，而产品默认
0.7"——如果 `--temperature` 只是加进了 argparse 却**没真传给 `DialogueService`**，
那么用 0.7 跑出来的"全绿"仍然是 0.0 的结论。这正是本项目已经栽过四次的
"装配层默认值"缺陷家族（D-A/D-B/D-D/D-E）：**参数定义在、但产品路径不装配**。

所以断言四件事：

1. 评估默认温度是 **0.0**（历史口径），不是产品的 0.7；
2. 非法温度在**任何一次真实调用之前**就被拒（超范围 / NaN）；
3. `--mock` 是确定性自检，显式给温度一律报错（不许"参数存在但不生效"）；
4. `stage_b` 的 `temperature` 是**必填关键字参数、没有默认值**——防止有人
   直接调 `stage_b(args, dims)` 时悄悄落回某个默认温度。
"""

from __future__ import annotations

import contextlib
import inspect
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

import run_dialogue_eval as runner                # noqa: E402
from persona.core.result import Ok                # noqa: E402
from persona.modules.dialogue import DIALOGUE_TEMPERATURE  # noqa: E402


def _run(argv: list[str]) -> tuple[int | None, str]:
    """跑 `main(argv)`，返回 `(退出码或 None, stderr 文本)`。"""
    err = io.StringIO()
    code: int | None = None
    with contextlib.redirect_stderr(err):
        try:
            code = runner.main(argv)
        except SystemExit as e:                   # argparse 的正常报错路径
            code = int(e.code) if e.code is not None else 0
    return code, err.getvalue()


class TestTemperatureResolution(unittest.TestCase):
    """`resolve_eval_temperature`：默认值与范围校验。"""

    def test_default_is_eval_zero_not_product_seven(self):
        """不给 `--temperature` ⇒ 0.0（评估口径），**不是**产品默认 0.7。"""
        self.assertEqual(runner.resolve_eval_temperature(None), 0.0)
        self.assertNotEqual(runner.EVAL_DEFAULT_TEMPERATURE, DIALOGUE_TEMPERATURE)

    def test_accepts_product_temperature(self):
        """P6.1-0 要用的 0.7 必须被接受。"""
        self.assertEqual(runner.resolve_eval_temperature(0.7), 0.7)

    def test_rejects_out_of_range(self):
        for bad in (-0.1, 2.1, 100):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    runner.resolve_eval_temperature(bad)

    def test_rejects_nan(self):
        """NaN 的比较恒为 False，必须被范围校验拒掉（不能漏成"等于任何值"）。"""
        with self.assertRaises(ValueError):
            runner.resolve_eval_temperature(float("nan"))


class TestCliWiring(unittest.TestCase):
    """CLI 层：非法值/错误组合必须在真实调用之前报错。"""

    def test_out_of_range_rejected_before_any_call(self):
        code, err = _run(["--real", "--small", "--dims", "B,C",
                          "--temperature", "3"])
        self.assertEqual(code, 2)
        self.assertIn("temperature", err)

    def test_nan_rejected_before_any_call(self):
        code, err = _run(["--real", "--small", "--temperature", "nan"])
        self.assertEqual(code, 2)
        self.assertIn("temperature", err)

    def test_mock_rejects_explicit_temperature(self):
        """`--mock` 温度恒 0；显式给温度不许静默忽略。"""
        code, err = _run(["--mock", "--temperature", "0.7"])
        self.assertEqual(code, 2)
        self.assertIn("只用于 --real", err)

    def test_stage_b_temperature_is_required(self):
        """**D 家族防线**：`stage_b` 的温度必须必填，没有"忘了传就落默认"的口子。

        如果它变成有默认值的参数，那么"参数没传"和"参数传了 0.0"就无法区分，
        `--temperature 0.7` 失效也不会有人发现。
        """
        params = inspect.signature(runner.stage_b).parameters
        self.assertIn("temperature", params)
        self.assertIs(params["temperature"].default, inspect.Parameter.empty,
                      "stage_b 的 temperature 不许有默认值（会掩盖未装配）")
        self.assertEqual(params["temperature"].kind,
                         inspect.Parameter.KEYWORD_ONLY)


# ==========================================================================
# P6.2-b：评估链路的记忆写入策略必须是 none（否则记忆污染评估）
# ==========================================================================


class TestEvalMemoryPolicyGuard(unittest.TestCase):
    """产品侧 P6.2 起有 `rule` 策略（每轮自动写模拟记忆）。

    评估绝不能跟着开：一旦写进去，下一题的上下文里就多了上一题的内容，
    **而没有任何断言会发现**——分数会安静地变好看或变难看。所以这里三层钉死：
    ① 会话读回来的策略不是 none → 直接抛错（fail-closed）；
    ② 脚本源码里不许出现任何"选择写入策略"的写法（静态扫描）；
    ③ 守卫本身被真的调用（`start_eval_session` 里挂牌）。
    """

    def test_none_policy_passes(self):
        view = SimpleNamespace(id="sid_eval", memory_write_policy="none")
        runner.assert_eval_memory_policy(view)          # 不抛即可

    def test_rule_policy_fails_loudly(self):
        view = SimpleNamespace(id="sid_eval", memory_write_policy="rule")
        with self.assertRaises(RuntimeError) as ctx:
            runner.assert_eval_memory_policy(view)
        self.assertIn("污染", str(ctx.exception))

    def test_explicit_policy_fails_loudly(self):
        view = SimpleNamespace(id="sid_eval", memory_write_policy="explicit")
        with self.assertRaises(RuntimeError):
            runner.assert_eval_memory_policy(view)

    def test_missing_field_fails_closed(self):
        """拿不到这个字段 = 不知道策略 = 不许跑（缺省放行等于没守卫）。"""
        with self.assertRaises(RuntimeError):
            runner.assert_eval_memory_policy(SimpleNamespace(id="sid_eval"))

    def test_start_eval_session_applies_the_guard(self):
        """守卫必须挂在开会话的路径上，不能只当摆设函数。"""
        class _Svc:
            def __init__(self, policy):
                self.policy = policy

            def start_session(self, ctx, draft):
                return Ok(SimpleNamespace(id="sid_eval",
                                          memory_write_policy=self.policy))

        self.assertEqual(
            runner.start_eval_session(_Svc("none"), None, "pe_a", "pe_b"),
            "sid_eval")
        with self.assertRaises(RuntimeError):
            runner.start_eval_session(_Svc("rule"), None, "pe_a", "pe_b")

    def test_script_never_opts_into_memory_write(self):
        """静态扫描：评估脚本里不许出现"选择写入策略"的写法。

        动态守卫能挡住"策略被改了"，但挡不住"有人把默认值改成 rule"——
        这条盯的是源码本身。
        """
        src = (_ROOT / "scripts" / "run_dialogue_eval.py").read_text(
            encoding="utf-8")
        for forbidden in ("POLICY_MEMORY_WRITE_RULE",
                          "POLICY_MEMORY_WRITE_EXPLICIT",
                          'memory_write_policy="rule"',
                          "memory_write_policy='rule'"):
            self.assertNotIn(forbidden, src,
                             f"评估脚本出现了 {forbidden}——记忆会污染评估")


if __name__ == "__main__":
    unittest.main()
