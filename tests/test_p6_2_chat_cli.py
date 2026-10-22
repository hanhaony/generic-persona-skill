"""P6.2-c：`persona chat` 的 CLI 与端到端测试（离线，零真实外呼）。

为什么单独一个文件：P6.2 的前两阶段（规则引擎 / `run_round` 接线）都只在
**模块层**验证过。产品路径没接通的话，那就是本项目已经栽过四次的 D 家族
形状——**能力只存在于测试显式注入的那条路上**（`docs/P5_1_REAL_RUN.md` §11）。
所以这里全部对着**真实的 CLI 入口 `main([...])`** 跑，断言的是**库里有什么**，
不是"函数返回了什么"。

打桩的边界只有一处：`persona.orchestrator.assembly.chat_provider_from_settings`
换成脚本化 Provider。装配（存储 / 记忆服务 / 对话服务 / 账本）、参数校验、
授权闸门、落库、收尾**全是真的**——只把"发出去的网络请求"换掉。

覆盖（对应 P6.2-c 清单第 5 条）：

    CLI 6 条
      1. `chat` 不再是占位；帮助文本写明默认 off
      2. 少于 2 个人物 → 拒绝
      3. 名字不在库里 / 没有档案 → 拒绝并告诉用户先 `persona run`
      4. `--rounds 0` → 拒绝
      5. 非交互环境不给 `--yes` → 拒绝，且**不写**确认文件
      6. TTY 首次运行问一次并记住（与 `persona run` 同一份 `.persona_consent.json`）

    端到端 3 条
      7. `--message` 单发 + `--memory-write rule` → **库里真有记忆行**
      8. 默认 off → 能对话，但零记忆行
      9. REPL 逐行读输入 → 跑满轮次自己结束，会话 ENDED

    Ctrl-C 1 条
     10. 中断后：已写记忆仍在、会话状态 ENDED、不抛未捕获异常、退出码 130
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from persona.cli import consent
from persona.cli.main import EXIT_INTERRUPTED, EXIT_OK, main
from persona.core.enums import ProvenanceNamespace
from persona.core.result import Ok, UsageDelta
from persona.core.types import MAX_QUERY_LIMIT, Query, UsageReport
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from tests.test_p5_1_readmodels import PROJECT, P51Fixture

#: 该记的（规则引擎 R-EPI 命中）
MEMORABLE = "昨天我去城南看了桃花，心里高兴得很"
#: 不该记的（N2 寒暄）
CHITCHAT = "你好呀"


class ScriptedProvider:
    """确定性 Provider：零网络、零费用，只为了让 `draft_turn` 有回答可落盘。

    **`usage` 必须给 token 数**：账本把"有请求但算不出金额"记成**未知笔**，
    累计到上限就暂停后续调用（`BUDGET_EXCEEDED`）。真实 Provider 一定带 token，
    桩不带的话，第二轮就会被这条闸门挡住——那测的就不是对话而是桩的失真。
    """

    def __init__(self) -> None:
        self.chat_calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.chat_calls += 1
        return Ok(ChatResponse(
            text="（脚本化回答）我记下了。", finish_reason="stop",
            model_id="scripted",
            usage=UsageDelta(requests=1, tokens_in=100, tokens_out=50)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


_ENV_KEYS = (
    "PERSONA_DATA_DIR", "PERSONA_OFFLINE_ONLY", "PERSONA_MODEL_PROVIDER",
    "PERSONA_PROVIDER_API_KEY", "PERSONA_SEARCH_API_KEY", "PERSONA_MODEL_MAX_CALLS",
    "PERSONA_MODEL_MAX_COST", "PERSONA_MODEL_MAX_COST_PER_TASK", "PERSONA_PROVIDER",
    "PERSONA_MODEL_ID", "PERSONA_MODEL_CURRENCY", "PERSONA_MODEL_PRICE_IN_PER_1M",
    "PERSONA_MODEL_PRICE_OUT_PER_1M", "PERSONA_MODEL_PRICE_SOURCE",
    "PERSONA_MODEL_PRICE_CHECKED_AT", "PERSONA_MODEL_EST_TOKENS_IN",
    "PERSONA_MODEL_EST_TOKENS_OUT", "PERSONA_MODEL_REQUIRE_GATE_VERDICT",
    "PERSONA_RESEARCH_MAX_EXTRACTS",
    # 别名也要清掉：开发机上可能已经有真实密钥，不清的话"缺密钥"这类断言会失真。
    "TAVILY_API_KEY", "DEEPSEEK_API_KEY",
)


class _ChatEnv:
    """把 `chat` 需要的环境变量摆成"配好了模型 + 有单价"，退出时原样还回去。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.saved: dict[str, str | None] = {}

    def __enter__(self) -> "_ChatEnv":
        values = {
            "PERSONA_DATA_DIR": str(self.root),
            "PERSONA_OFFLINE_ONLY": "false",
            "PERSONA_MODEL_PROVIDER": "deepseek",
            "PERSONA_PROVIDER_API_KEY": "sk-test-not-a-real-key",
            "PERSONA_MODEL_MAX_CALLS": "10",
            "PERSONA_MODEL_ID": "deepseek-chat",
            "PERSONA_MODEL_CURRENCY": "USD",
            "PERSONA_MODEL_PRICE_IN_PER_1M": "0.14",
            "PERSONA_MODEL_PRICE_OUT_PER_1M": "0.28",
            "PERSONA_MODEL_PRICE_SOURCE": "https://example.test/pricing",
        }
        for key in _ENV_KEYS:
            self.saved[key] = os.environ.get(key)
            os.environ.pop(key, None)
        os.environ.update(values)
        return self

    def __exit__(self, *exc: object) -> None:
        for key, old in self.saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


class _ChatCase(unittest.TestCase):
    """临时库 + 两个有档案的人物 + 脚本化 Provider（**唯一**被打桩的地方）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gps-p62-chat-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "persona.db"

        fx = P51Fixture(str(self.root))
        self.hero = fx.entity("阿雾", works=("雾港与星灯",), adaptation="2020 动画改编版")
        self.other = fx.entity("阿灯", works=("雾港与星灯",), adaptation="2020 动画改编版")
        src = fx.source()
        ref = fx.evidence_of(src, "阿雾沉默寡言", subjects=[self.hero])
        fx.profile(self.hero, background=fx.section("阿雾：沉默寡言的灯塔看守。", [ref]))
        ref2 = fx.evidence_of(src, "阿灯活泼", subjects=[self.other])
        fx.profile(self.other, background=fx.section("阿灯：活泼的渔家少女。", [ref2]))
        self.fx = fx

        env = _ChatEnv(self.root)
        env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)

        self.provider = ScriptedProvider()
        patch = mock.patch(
            "persona.orchestrator.assembly.chat_provider_from_settings",
            lambda settings, **kw: Ok(self.provider))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.fx.storage.close()

    # ------------------------------------------------------------ 断言辅助

    def _argv(self, *extra: str) -> list[str]:
        return ["chat", "--names", "阿雾,阿灯", "--db", str(self.db),
                "--project", PROJECT, "--no-dotenv", *extra]

    def _tty(self) -> list:
        patches = [
            mock.patch("persona.cli.main._stdin_is_tty", lambda: True),
            mock.patch("persona.cli.main._stdout_is_tty", lambda: True),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return patches

    def memory_rows(self) -> list[dict]:
        q = Query(filters={"project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(self.fx.storage.query(
            self.fx.ctx, "MemoryItem", q, include_deleted=True).value.items)

    def session_rows(self) -> list[dict]:
        q = Query(filters={"project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(self.fx.storage.query(
            self.fx.ctx, "ConversationSession", q).value.items)

    def turn_rows(self) -> list[dict]:
        q = Query(filters={"project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(self.fx.storage.query(
            self.fx.ctx, "ConversationTurn", q).value.items)


# ==========================================================================
# CLI 6 条
# ==========================================================================


class TestChatCommandSurface(_ChatCase):

    def test_chat_is_registered_and_defaults_to_off(self):
        """`chat` 不再是占位；默认 `off` 必须写在帮助里（默认写记忆是不可接受的）。"""
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main(["chat", "--help"])
            except SystemExit:
                pass
        text = buf.getvalue()
        self.assertIn("--memory-write", text)
        self.assertIn("off（默认）", text)
        self.assertNotIn("尚未实现", text)

    def test_fewer_than_two_names_is_refused(self):
        code, out = _run(["chat", "--names", "阿雾", "--db", str(self.db),
                          "--project", PROJECT, "--no-dotenv", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("至少要 2 个人物", out)

    def test_unknown_name_tells_the_user_to_run_first(self):
        code, out = _run(["chat", "--names", "查无此人,阿灯", "--db", str(self.db),
                          "--project", PROJECT, "--no-dotenv", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("没有找到「查无此人」", out)
        self.assertIn("persona run", out)

    def test_zero_rounds_is_refused(self):
        code, out = _run(self._argv("--rounds", "0", "--yes"))
        self.assertEqual(code, 1)
        self.assertIn("--rounds 必须是正整数", out)

    def test_non_interactive_without_yes_is_refused(self):
        """与 `persona run` 同口径：没人反对 ≠ 同意，且不留下确认文件。"""
        code, out = _run(self._argv("--rounds", "1"))
        self.assertEqual(code, 1)
        self.assertIn("非交互环境", out)
        self.assertFalse(consent.consent_path(self.root).exists())

    def test_tty_asks_once_and_remembers(self):
        """确认机制与 `run` **同一个函数**：问法、记住方式、文件内容都一样。"""
        self._tty()
        with mock.patch("builtins.input", return_value="y"):
            code, out = _run(self._argv("--rounds", "1", "--message", CHITCHAT))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("继续？[y/N]", out)
        self.assertTrue(consent.is_confirmed(self.root))
        payload = consent.load_consent(self.root) or {}
        self.assertEqual(set(payload), set(consent.ALLOWED_KEYS))
        # 顺手钉死"不许有第二套确认"：`build_question` / `remember` 在全文件里
        # 各只出现一次（都在 `_authorize_real_calls` 里）。将来有人在 `chat`
        # 里另写一句问句，这条会红。
        # `persona.cli.main` 这个名字被 `cli/__init__.py` 里的 `main` 函数占了，
        # 所以只能从 `sys.modules` 拿模块本身。
        import sys
        source = Path(sys.modules["persona.cli.main"].__file__).read_text(
            encoding="utf-8")
        for needle in ("consent.build_question", "consent.remember("):
            with self.subTest(needle=needle):
                self.assertEqual(source.count(needle), 1,
                                 f"{needle} 出现了 {source.count(needle)} 次："
                                 "确认逻辑被复制了")


# ==========================================================================
# 端到端 3 条（断言的是**库里有什么**）
# ==========================================================================


class TestChatEndToEnd(_ChatCase):

    def test_message_round_with_rule_writes_a_real_memory_row(self):
        """D 家族的正面防线：不只看输出里有没有 id，要看**库里有没有这一行**。"""
        code, out = _run(self._argv("--rounds", "1", "--message", MEMORABLE,
                                    "--memory-write", "rule", "--yes"))
        self.assertEqual(code, EXIT_OK)
        rows = self.memory_rows()
        self.assertEqual(len(rows), 1, "规则策略下库里没有记忆行——产品路径没接通")
        row = rows[0]
        self.assertEqual(row["namespace"], ProvenanceNamespace.SIMULATED.value)
        self.assertEqual(row["source_type"], "SIMULATED_DIALOGUE")
        self.assertEqual(row["scope"], "SESSION")
        self.assertIn(row["content"], MEMORABLE)
        # 轮次行必须回填"我生成了哪些记忆"，否则幂等重放会丢
        turns = [t for t in self.turn_rows() if t.get("generated_memory_ids")]
        self.assertEqual(len(turns), 1)
        self.assertEqual(list(turns[0]["generated_memory_ids"]), [row["id"]])
        self.assertIn("写入记忆 1 条", out)

    def test_default_off_writes_nothing_but_still_talks(self):
        code, out = _run(self._argv("--rounds", "1", "--message", MEMORABLE, "--yes"))
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.memory_rows(), [], "默认策略下写了记忆——D-179 被绕过")
        self.assertIn("写入记忆 0 条", out)
        # 会话照常留下：不写记忆 ≠ 不记录对话
        self.assertEqual(len(self.session_rows()), 1)
        self.assertEqual(self.session_rows()[0]["status"], "ENDED")

    def test_repl_reads_lines_and_ends_the_session(self):
        with mock.patch("builtins.input", side_effect=[CHITCHAT, MEMORABLE]):
            code, out = _run(self._argv("--rounds", "2", "--memory-write",
                                        "rule", "--yes"))
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.provider.chat_calls, 2)
        # 寒暄不记、自述要记 → 恰好 1 条
        self.assertEqual(len(self.memory_rows()), 1)
        sessions = self.session_rows()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "ENDED")
        self.assertIn("写入记忆 1 条", out)


# ==========================================================================
# Ctrl-C：干净中止
# ==========================================================================


class TestChatCtrlC(_ChatCase):

    def test_ctrl_c_keeps_memories_closes_session_and_does_not_raise(self):
        """中断三件事：记忆不回滚、会话不留 ACTIVE、异常不逃逸到调用方。"""
        interrupted: list[int] = []

        def _input(_prompt: str = "") -> str:
            if not interrupted:
                interrupted.append(1)
                return MEMORABLE
            raise KeyboardInterrupt()

        code, out = 0, ""
        try:
            with mock.patch("builtins.input", side_effect=_input):
                code, out = _run(self._argv("--rounds", "5", "--memory-write",
                                            "rule", "--yes"))
        except KeyboardInterrupt:  # pragma: no cover - 到这里就是测试失败
            self.fail("KeyboardInterrupt 逃逸出了 persona chat")

        self.assertEqual(code, EXIT_INTERRUPTED)
        self.assertIn("Ctrl-C", out)
        # ① 已写的记忆还在（每轮一个事务，中断不会回滚上一轮）
        self.assertEqual(len(self.memory_rows()), 1)
        # ② 会话状态关闭，不留 ACTIVE/OPEN
        sessions = self.session_rows()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["status"], "ENDED")
        # ③ 只跑了 1 轮就被中断（第 2 轮读输入时中断，没有第 2 次模型调用）
        self.assertEqual(self.provider.chat_calls, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
