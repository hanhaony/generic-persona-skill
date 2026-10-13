"""CLI 启动与帮助输出测试（INTERFACES §11）。

验证：
  - `persona --help` 能出来
  - `persona info` 打印阶段与 Python 版本
  - `persona selftest` 在临时库上跑通（不写项目数据）
  - 业务命令是**诚实的占位**：返回"尚未实现"，不假装能用
  - CLI **不绕过 Orchestrator**：源码里不 import 业务模块（见 test_dependency_rules.py）
"""

import io
import unittest
from contextlib import redirect_stdout

from persona.cli.main import EXIT_NOT_IMPLEMENTED, EXIT_OK, build_parser, main


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
        self.assertIn("P1.2-B", text)
        self.assertIn("persona.core", text)
        self.assertIn("PENDING", text)
        self.assertIn("PUBLIC_SOURCE", text)

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
        for argv in (["new", "--names", "甲,乙"], ["status", "tsk_1"], ["resume", "tsk_1"],
                     ["pause", "tsk_1"], ["cancel", "tsk_1"], ["chat", "prj_1"],
                     ["sources", "prj_1"], ["memory", "prj_1"], ["eval"]):
            with self.subTest(argv=argv):
                with redirect_stdout(io.StringIO()) as out:
                    code = main(argv)
                self.assertEqual(code, EXIT_NOT_IMPLEMENTED)
                self.assertIn("尚未实现", out.getvalue())


if __name__ == "__main__":
    unittest.main()
