"""`app.py` 动作/台词分离与句读切分的守卫测试（2026-11-05 P1 阅读体验）。

# 缺陷现场

模型输出混合动作描写（（端起茶杯）、*叹了口气*）与台词，一次性全部
刷出来且无视觉区分——动作和说话混在一起，缺乏"活人感"。

# 修复语义（split_action_segments + _speech_chunks）

- 动作 = （…）/ (…) / **…** / *…* 包裹的内容 → ("action", 内容)，
  括号形态保留括号展示，星号形态剥掉星号（** 渲染成 markdown 粗体会
  破坏灰斜体样式）；
- 其余为 ("speech", 内容)，段序与原文一致；
- 无法解析时整体按台词兜底，绝不让渲染层炸掉。

# 为什么用 AST 抽取

`app.py` 模块级 `import streamlit`，本仓库测试纪律是零第三方依赖——
同 `test_app_parse_names.py` 的做法，用 `ast` 把纯函数摘出来执行。
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _load_function(name: str, needs: dict):
    """从 app.py 里摘出一个纯函数（不触发 streamlit import）。

    若函数依赖模块级正则常量（如 `_ACTION_RE`），一并摘取赋值节点注入——
    测试钉住的是 app.py 里的真实现，不是测试自己复刻的副本。
    """
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    namespace: dict = dict(needs)
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            is_compile = (getattr(func, "attr", "") == "compile"
                          or getattr(func, "id", "") == "compile")
            targets = [t.id for t in node.targets
                       if isinstance(t, ast.Name)]
            if is_compile and "_ACTION_RE" in targets:
                exec(compile(ast.Module(body=[node], type_ignores=[]),
                             str(APP_PY), "exec"), namespace)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(APP_PY), "exec"), namespace)
            return namespace[name]
    fail = unittest.TestCase()
    fail.fail(f"app.py 里找不到 {name}：函数被改名或删除了？")


class TestSplitActionSegments(unittest.TestCase):
    def setUp(self):
        self.split = _load_function("split_action_segments", {"re": re})

    def test_bracket_action_then_speech(self):
        """（端起茶杯）+ 台词：动作在前，台词在后。"""
        got = self.split("（端起茶杯）这茶不错。")
        self.assertEqual(got, [("action", "（端起茶杯）"),
                               ("speech", "这茶不错。")])

    def test_asterisk_action_keeps_inner_without_stars(self):
        """*叹了口气* → 动作段不带星号（否则渲染成 markdown 粗体）。"""
        got = self.split("*叹了口气*罢了。")
        self.assertEqual(got, [("action", "叹了口气"),
                               ("speech", "罢了。")])

    def test_double_asterisk_action(self):
        got = self.split("**拂袖而去**你们随意。")
        self.assertEqual(got, [("action", "拂袖而去"),
                               ("speech", "你们随意。")])

    def test_halfwidth_parens_keep_brackets(self):
        got = self.split("(smiles) Well said.")
        self.assertEqual(got, [("action", "(smiles)"),
                               ("speech", "Well said.")])

    def test_multiple_segments_order_preserved(self):
        """动作与台词交错时，段序必须与原文一致。"""
        got = self.split("（起身）你来了。*压低声音*别声张。")
        self.assertEqual(
            [k for k, _ in got],
            ["action", "speech", "action", "speech"])
        self.assertEqual(got[1][1], "你来了。")
        self.assertEqual(got[2][1], "压低声音")
        self.assertEqual(got[3][1], "别声张。")

    def test_plain_text_is_single_speech(self):
        got = self.split("今天天气不错，出去走走？")
        self.assertEqual(got, [("speech", "今天天气不错，出去走走？")])

    def test_empty_and_blank_input(self):
        self.assertEqual(self.split(""), [])
        self.assertEqual(self.split("   \n  "), [])

    def test_action_only_message(self):
        """纯动作发言（角色沉默只做动作）也要能渲染。"""
        got = self.split("（沉默地看了对方一眼）")
        self.assertEqual(got, [("action", "（沉默地看了对方一眼）")])

    def test_multiline_speech_preserved(self):
        got = self.split("第一句。\n第二句。")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "speech")
        self.assertIn("\n", got[0][1])


class TestSpeechChunks(unittest.TestCase):
    def setUp(self):
        self.chunks = _load_function("_speech_chunks", {"re": re})

    def test_sentence_split_on_chinese_punctuation(self):
        got = self.chunks("你来了。坐吧，喝茶！")
        self.assertEqual(got, ["你来了。", "坐吧，", "喝茶！"])

    def test_long_clause_without_punctuation(self):
        """无句读的长句也要能切（整句返回，不丢字）。"""
        got = self.chunks("这句话没有任何停顿符号")
        self.assertEqual(got, ["这句话没有任何停顿符号"])

    def test_empty_falls_back_to_original(self):
        self.assertEqual(self.chunks("…"), ["…"])


class TestCssAndAnimationWiring(unittest.TestCase):
    """静态断言：动画与样式关键件必须接线（拔掉即红）。"""

    def setUp(self):
        self.src = APP_PY.read_text(encoding="utf-8")

    def test_time_imported_and_sleep_used(self):
        self.assertIn("import time", self.src)
        self.assertIn("time.sleep(", self.src)

    def test_action_css_class_exists(self):
        self.assertIn(".action-text", self.src)
        self.assertIn("font-style: italic", self.src)

    def test_serif_typography_for_chat_messages(self):
        self.assertIn("Noto Serif SC", self.src)
        self.assertIn("line-height: 1.8", self.src)

    def test_history_renders_static_not_animated(self):
        """历史渲染必须 animate=False（否则每次交互重播延时）。"""
        self.assertIn("render_assistant_message(msg, animate=False)", self.src)

    def test_animation_only_for_new_turns(self):
        self.assertIn("render_assistant_message(msg, animate=True", self.src)


if __name__ == "__main__":
    unittest.main()
