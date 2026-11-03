"""`app.py` 前端人名解析的守卫测试（灰度修复：英文名带空格被拆碎）。

# 缺陷现场（灰度实测）

旧实现用 `[,，、;；\\s]+` 正则切分——**空格被当成了分隔符**，用户输入
`Ben Affleck, Matt Damon` 被拆成 Ben / Affleck / Matt / Damon 四个"人物"。

# 修复语义（parse_names）

- 中文逗号 `，`、顿号 `、`、分号、换行统一折算成英文逗号；
- **严格只用英文逗号 `split`**：空格在名字内部是合法字符，绝不拆分；
- 逐项 `strip` 后丢弃空串。

# 为什么用 AST 抽取

`app.py` 是 Streamlit 薄壳，模块级 `import streamlit`——本仓库测试纪律是
**零第三方依赖**（只跑标准库 unittest），不能整文件 import。这里用 `ast`
把 `parse_names` 单独摘出来执行：解析函数只依赖 `re`，抽取执行与真实
运行完全等价，同时守住"app.py 里必须存在这个名字解析函数"的契约。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _load_parse_names():
    """从 app.py 里摘出 `parse_names` 纯函数（不触发 streamlit import）。"""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "parse_names":
            namespace: dict = {"re": re}
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(APP_PY), "exec"), namespace)
            return namespace["parse_names"]
    self_fail = unittest.TestCase()
    self_fail.fail("app.py 里找不到 parse_names：解析函数被改名或删除了？")


class TestParseNames(unittest.TestCase):
    def setUp(self):
        self.parse = _load_parse_names()

    def test_english_names_with_spaces_stay_intact(self):
        """核心回归：`Ben Affleck, Matt Damon` 必须是两个人，不是四个。"""
        self.assertEqual(self.parse("Ben Affleck, Matt Damon"),
                         ["Ben Affleck", "Matt Damon"])

    def test_no_comma_means_one_name_even_with_spaces(self):
        self.assertEqual(self.parse("Ben Affleck"), ["Ben Affleck"])
        self.assertEqual(self.parse("林黛玉 贾宝玉"), ["林黛玉 贾宝玉"])

    def test_chinese_separators_all_work(self):
        self.assertEqual(self.parse("林黛玉，贾宝玉、薛宝钗"),
                         ["林黛玉", "贾宝玉", "薛宝钗"])
        self.assertEqual(self.parse("林黛玉；贾宝玉；薛宝钗"),
                         ["林黛玉", "贾宝玉", "薛宝钗"])

    def test_newline_is_a_separator(self):
        self.assertEqual(self.parse("Ben Affleck\nMatt Damon"),
                         ["Ben Affleck", "Matt Damon"])

    def test_strip_and_drop_empty_parts(self):
        self.assertEqual(self.parse("  Ben Affleck ,  , Matt Damon "),
                         ["Ben Affleck", "Matt Damon"])
        self.assertEqual(self.parse(",,,"), [])
        self.assertEqual(self.parse(""), [])
        self.assertEqual(self.parse(None), [])


if __name__ == "__main__":
    unittest.main()
