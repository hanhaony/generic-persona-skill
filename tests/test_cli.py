"""CLI 启动与帮助输出测试（INTERFACES §11）。

验证：
  - `persona --help` 能出来
  - `persona info` 打印阶段与 Python 版本
  - `persona selftest` 在临时库上跑通（不写项目数据）
  - 业务命令是**诚实的占位**：返回"尚未实现"，不假装能用
  - CLI **不绕过 Orchestrator**：源码里不 import 业务模块（见 test_dependency_rules.py）
"""

import contextlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from persona.cli.main import EXIT_NOT_IMPLEMENTED, EXIT_OK, build_parser, main


@contextlib.contextmanager
def _temp_data_dir():
    """让 CLI 把数据库写到临时目录（D-070：测试不得在项目里留 .db）。"""
    with tempfile.TemporaryDirectory(prefix="gps-cli-") as tmp:
        previous = os.environ.get("PERSONA_DATA_DIR")
        os.environ["PERSONA_DATA_DIR"] = tmp
        try:
            yield tmp
        finally:
            if previous is None:
                os.environ.pop("PERSONA_DATA_DIR", None)
            else:
                os.environ["PERSONA_DATA_DIR"] = previous


class TestCliStartup(unittest.TestCase):
    def test_help_exits_zero(self):
        with redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as ctx:
                build_parser().parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("generic-persona-skill", out.getvalue())

    def test_no_args_prints_help(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main([])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("usage: persona", out.getvalue())

    def test_version_flag(self):
        with redirect_stdout(io.StringIO()) as out:
            with self.assertRaises(SystemExit) as ctx:
                build_parser().parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn("persona", out.getvalue())

    def test_info_output(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["info"])
        text = out.getvalue()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("persona.core", text)
        self.assertIn("PENDING", text)
        self.assertIn("PUBLIC_SOURCE", text)
        # 正向：`info` 是第一眼对外文案，阶段号必须是当下的，不能停在旧阶段。
        self.assertIn("P6", text)

    def test_info_does_not_claim_the_old_stage(self):
        """`persona info` 是灰度用户跑的第一条命令，**不许说过期的话**。

        下面每一条都曾是 P5 时代的**事实**，现在全是假的（P6 二期已验收，检索 / 抓取 /
        模型抽取 / 对话均已接入并通过受控真跑验证）。把它们钉成红灯，
        是为了不让「文档改了、命令没改」这类对外说谎再发生（本次就是这么发现的）。
        改动本命令文案后若命中这里，先问一句：是真的退步了，还是说法换了？
        说法换了就把这句改成新说法的准确表述，别删守卫。
        """
        with redirect_stdout(io.StringIO()) as out:
            main(["info"])
        text = out.getvalue()
        for stale in ("与 AI 蒸馏尚未实现",
                      "不接真实搜索 API",
                      "尚未授权真实模型调用",
                      "源→证据自动抽取未实现",
                      "真实检索与正文抓取未接"):
            with self.subTest(stale=stale):
                self.assertNotIn(
                    stale, text,
                    f"`persona info` 里出现了 P5 时代的过期说法：{stale}",
                )

    def test_selftest_runs_offline(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["selftest"])
        text = out.getvalue()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("SQLite 读写", text)
        self.assertIn("隔离闸门", text)
        self.assertIn("Mock Provider", text)
        self.assertIn("通过", text)


class TestCliPlaceholderCommands(unittest.TestCase):
    def test_business_commands_are_honest_placeholders(self):
        """研究 / 记忆 / 评测**仍**是占位，不假装能用。

        `chat` 已于 P6.2-c 接通真实对话（见 tests/test_p6_2_chat_cli.py），
        所以它**不在这份占位清单里**——留在这里就会在 argparse 层报错，
        把"已实现"误报成"未实现"。
        """
        for argv in (["new", "--names", "甲,乙"],
                     ["sources", "prj_1"], ["memory", "prj_1"], ["eval"]):
            with self.subTest(argv=argv):
                with redirect_stdout(io.StringIO()) as out:
                    code = main(argv)
                self.assertEqual(code, EXIT_NOT_IMPLEMENTED)
                self.assertIn("尚未实现", out.getvalue())

    def test_unknown_task_id_is_a_friendly_message(self):
        """P2.3：status / resume / pause / cancel 已接通任务服务。
        给了不存在的 ID 应当**友好报错**，而不是抛堆栈。"""
        with _temp_data_dir():
            for argv in (["status", "tsk_" + "0" * 32, "--project", "prj_cli"],
                         ["resume", "tsk_" + "0" * 32, "--project", "prj_cli"]):
                with self.subTest(argv=argv):
                    with redirect_stdout(io.StringIO()) as out:
                        code = main(argv)
                    self.assertEqual(code, 1)
                    self.assertIn("失败", out.getvalue())
                    self.assertNotIn("Traceback", out.getvalue())


class TestCliTaskLifecycle(unittest.TestCase):
    """`persona task ...` 端到端冒烟（用临时数据目录，不碰项目里的 var/）。"""

    def test_create_start_pause_via_cli(self):
        with _temp_data_dir():
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["task", "create", "--project", "prj_cli",
                             "--stages", "A,B", "--key", "cli-1"])
            self.assertEqual(code, EXIT_OK)
            created = out.getvalue()
            self.assertIn("PENDING", created)
            task_id = [line.split(":", 1)[1].strip()
                       for line in created.splitlines()
                       if line.strip().startswith("任务 ")][0]
            self.assertTrue(task_id.startswith("tsk_"), created)

            with redirect_stdout(io.StringIO()) as out2:
                self.assertEqual(main(["task", "start", task_id, "--project", "prj_cli"]),
                                 EXIT_OK)
            self.assertIn("RUNNING", out2.getvalue())

            with redirect_stdout(io.StringIO()) as out3:
                self.assertEqual(
                    main(["task", "pause", task_id, "--project", "prj_cli",
                          "--reason", "USER"]),
                    EXIT_OK)
            self.assertIn("PAUSED", out3.getvalue())

            with redirect_stdout(io.StringIO()) as out4:
                self.assertEqual(main(["task", "list", "--project", "prj_cli"]), EXIT_OK)
            self.assertIn(task_id, out4.getvalue())

    def test_task_without_action_prints_help(self):
        with redirect_stdout(io.StringIO()) as out:
            code = main(["task"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("create", out.getvalue())


if __name__ == "__main__":
    unittest.main()
