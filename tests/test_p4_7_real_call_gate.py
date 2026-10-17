"""P4.7 整改：装配层**默认禁止真实外呼**（`allow_real=False`）。

# 为什么要有这道闸

2026-10-17 的 P4.7 自检里，我用 `persona business run --material …` 跑了一次单人物
链路。仓库 `.env` 里配着 `PERSONA_OFFLINE_ONLY=false` + `PERSONA_MODEL_PROVIDER=deepseek`，
装配层按配置把真实 Provider 构造出来，于是**真的发出并完成了 1 次 POST**
（0.0019161 USD）。本轮本应零花费。

根因不是"忘了改配置"，而是**默认值选错了**：`offline_only=false` 是"允许联网"，
被当成了"这次运行被授权花钱"。这两件事必须分开：

    - 配置层：`offline_only` 决定**这套配置能不能联网**（已有，加载期即拒）；
    - 装配层：`allow_real` 决定**这一次装配能不能构造真实 Provider**（本文件）。

本文件锁住五件事：

1. 默认（`allow_real` 缺省）→ 配置指着 deepseek 也**不构造**，记
   `REAL_PROVIDER_BLOCKED`，且**一次网络出口都没碰**；
2. `allow_real=True` → 才构造（构造 ≠ 调用，仍然不发请求）；
3. `allow_real=True` **不伪造能力**：配置没给服务商时照样降级
   （`CHAT_PROVIDER_UNAVAILABLE`），不会因为"授权了"就凭空有抽取器；
4. **显式注入不受闸门影响**：桩 Provider 是调用方塞进来的，不存在"默默按配置联网"；
5. CLI `business run` 默认不传授权，`--allow-real` 才传；不给授权时的拒绝信息
   必须**指名**这条规则与开关（否则用户只看到"没有抽取器"，查不出原因）。

全程离线：本文件不联网。真实 Provider 只被**构造**，`urlopen` 被替换成断言失败。
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from persona.cli.main import EXIT_NOT_IMPLEMENTED, main as cli_main
from persona.config import test_settings
from persona.core.context import new_call_context
from persona.core.result import Err, is_ok
from persona.orchestrator import (
    WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE,
    WARNING_STACK_EXTRACTOR_ABSENT,
    WARNING_STACK_REAL_PROVIDER_BLOCKED,
    StackOptions,
    build_business_stack,
)
from persona.providers.mock import FakeProvider
from persona.storage.sqlite import SqliteStorage


def _network_settings():
    """**合法**的联网配置：离线开关关闭 + 真实服务商 + 假密钥（不会真的用到）。"""
    return test_settings(
        offline_only=False,
        **{
            "model.provider": "deepseek",
            # 假密钥：只要非空就能过配置校验；任何真实请求都会被下面的
            # `urlopen` 断言拦住，所以它不可能被用出去。
            "secrets.provider_api_key": "sk-test-not-a-real-key",
        },
    )


def _offline_settings():
    return test_settings()


class _TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p47-gate-")
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "gate.db"

    def _build(self, settings, *, allow_real=False, **kwargs):
        storage = SqliteStorage(self.path)
        self.addCleanup(storage.close)
        ctx = new_call_context(project_id="prj_p47_gate", actor="p47-gate")
        built = build_business_stack(
            settings, ctx, storage=storage,
            options=StackOptions(build_chat_provider=True),
            allow_real=allow_real, **kwargs)
        self.assertTrue(is_ok(built), f"装配失败：{getattr(built, 'error', '')}")
        return built.value


# ==========================================================================
# 一、装配层：默认拒绝 / 显式授权
# ==========================================================================


class DefaultDenyTest(_TempDb):
    def test_default_does_not_build_a_real_provider(self):
        """配置允许联网 + 指着 deepseek，但没授权 → 不留抽取器。"""
        stack = self._build(_network_settings())
        self.assertIsNone(stack.claim_extractor,
                          "默认必须拒绝构造真实 Provider（否则就是这一次的复现）")
        codes = {w.code for w in stack.warnings}
        self.assertIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)
        self.assertIn(WARNING_STACK_EXTRACTOR_ABSENT, codes)

    def test_default_never_touches_the_network(self):
        """闸门是在**构造之前**生效的：连 socket 都不该有机会被创建。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("默认禁止真实外呼：不得联网")):
            stack = self._build(_network_settings())
        self.assertIsNone(stack.claim_extractor)

    def test_blocked_message_names_the_gate_and_the_switch(self):
        """告警必须说清"为什么"和"怎么授权"，不能只说"没有抽取器"。"""
        stack = self._build(_network_settings())
        note = next(w for w in stack.warnings
                    if w.code == WARNING_STACK_REAL_PROVIDER_BLOCKED)
        self.assertIn("默认禁止真实外呼", note.message)
        self.assertIn("--allow-real", note.message)

    def test_allow_real_builds_but_still_does_not_call(self):
        """授权只解除闸门：Provider 会被构造，但构造不等于发请求。"""
        with mock.patch("urllib.request.urlopen",
                        side_effect=AssertionError("授权构造 ≠ 允许立即联网")):
            stack = self._build(_network_settings(), allow_real=True)
        self.assertIsNotNone(stack.claim_extractor)
        self.assertEqual(type(stack.claim_extractor.provider).__name__,
                         "DeepSeekProvider")
        codes = {w.code for w in stack.warnings}
        self.assertNotIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)

    def test_allow_real_does_not_invent_a_provider(self):
        """授权不等于有能力：配置没给服务商，照样降级，不伪造抽取器。"""
        stack = self._build(_offline_settings(), allow_real=True)
        self.assertIsNone(stack.claim_extractor)
        codes = {w.code for w in stack.warnings}
        self.assertIn(WARNING_STACK_CHAT_PROVIDER_UNAVAILABLE, codes)
        self.assertNotIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)

    def test_injected_provider_is_not_gated(self):
        """显式注入的桩 Provider 不受闸门影响（注入本身就是显式动作）。"""
        stack = self._build(_offline_settings(), allow_real=False,
                            chat_provider=FakeProvider())
        self.assertIsNotNone(stack.claim_extractor)
        self.assertIsInstance(stack.claim_extractor.provider, FakeProvider)
        codes = {w.code for w in stack.warnings}
        self.assertNotIn(WARNING_STACK_REAL_PROVIDER_BLOCKED, codes)


# ==========================================================================
# 二、CLI：默认不授权，`--allow-real` 才授权
# ==========================================================================


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = cli_main(argv)
    return code, buf.getvalue()


class CliGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="p47-gate-cli-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._restore_env)
        self._saved: dict[str, str | None] = {}
        self._set_env("PERSONA_DATA_DIR", self._tmp.name)
        # 让 CLI 的配置**看起来**是允许联网的（`.env` 里真实存在的那套）
        self._set_env("PERSONA_OFFLINE_ONLY", "false")
        self._set_env("PERSONA_MODEL_PROVIDER", "deepseek")
        self._set_env("PERSONA_PROVIDER_API_KEY", "sk-test-not-a-real-key")

    def _set_env(self, key: str, value: str) -> None:
        import os
        self._saved[key] = os.environ.get(key)
        os.environ[key] = value

    def _restore_env(self) -> None:
        import os
        for key, old in self._saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

    def test_run_without_flag_passes_allow_real_false(self):
        captured: dict = {}

        def fake_build(settings, ctx, **kwargs):
            captured.update(kwargs)
            return Err(mock.MagicMock())

        with mock.patch("persona.orchestrator.build_business_stack", fake_build):
            code, out = _run(["business", "run", "--names", "阿灯",
                              "--work-title", "雾港灯会", "--adaptation-version",
                              "2020 动画改编版", "--no-dotenv"])
        self.assertEqual(captured.get("allow_real"), False)
        self.assertEqual(code, 1)          # 装配失败 → 直接退出，不进业务链
        self.assertIn("装配失败", out)

    def test_run_with_flag_passes_allow_real_true(self):
        captured: dict = {}

        def fake_build(settings, ctx, **kwargs):
            captured.update(kwargs)
            return Err(mock.MagicMock())

        with mock.patch("persona.orchestrator.build_business_stack", fake_build):
            _run(["business", "run", "--names", "阿灯",
                  "--work-title", "雾港灯会", "--adaptation-version",
                  "2020 动画改编版", "--allow-real", "--no-dotenv"])
        self.assertEqual(captured.get("allow_real"), True)

    def test_refusal_explains_the_gate_and_how_to_authorize(self):
        """不给授权时，拒绝信息必须指名规则与开关（不是只说"没有抽取器"）。"""
        code, out = _run(["business", "run", "--names", "阿灯",
                          "--work-title", "雾港灯会", "--adaptation-version",
                          "2020 动画改编版", "--no-dotenv"])
        self.assertEqual(code, EXIT_NOT_IMPLEMENTED)
        self.assertIn("默认禁止真实外呼", out)
        self.assertIn("--allow-real", out)
        self.assertIn("没有发起任何请求", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
