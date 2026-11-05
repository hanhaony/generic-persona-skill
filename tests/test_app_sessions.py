"""`app.py` 多会话管理的守卫测试（2026-11-05 P1 微信式多开）。

# 缺陷现场

网页是全局单会话：更换人物或刷新会丢历史；多群同开时底层记忆/预算
共用一个库，会串台。

# 修复语义（session_* 纯函数 + 持久化）

- 每会话独立 DB：`session_db_path` 必须落在 `sessions/<sid>/persona.db`
  （引擎预算是全库累计，不独立 DB 挡不住预算串台）；
- `make_project_id`：`prj_web_` 前缀 + 净化 sid（第二道命名空间隔离）；
- `session_label`：群名优先，无群名用人物名（3 个 + 省略号）；
- `unique_label`：radio 选项必须唯一，重名自动「（2）（3）」；
- `save_sessions` / `load_sessions`：JSON 往返无损；损坏/缺失返回空。

AST 抽取 app.py 纯函数（不触发 streamlit import），拔掉实现即红。
"""

from __future__ import annotations

import ast
import json
import os
import re
import tempfile
import time
import unittest
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _load_function(name: str, needs: dict):
    """从 app.py 里摘出一个纯函数（不触发 streamlit import）。"""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    namespace: dict = dict(needs)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(APP_PY), "exec"), namespace)
            return namespace[name]
    fail = unittest.TestCase()
    fail.fail(f"app.py 里找不到 {name}：函数被改名或删除了？")


def _tmp_ns() -> dict:
    """带临时 SESSIONS_DIR/SESSIONS_FILE 的依赖命名空间。"""
    tmp = Path(tempfile.mkdtemp(prefix="gps-sessions-test-"))
    return {"SESSIONS_DIR": tmp / "sessions",
            "SESSIONS_FILE": tmp / "web_sessions.json"}


class TestProjectIsolation(unittest.TestCase):
    def setUp(self):
        ns = _tmp_ns()
        self.db_path = _load_function("session_db_path", ns)
        self.make_project_id = _load_function("make_project_id", {"re": re})

    def test_db_path_isolated_per_session(self):
        """不同会话的 DB 路径必须不同（预算/记忆/档案物理隔离）。"""
        a = self.db_path("s_a")
        b = self.db_path("s_b")
        self.assertNotEqual(a, b)
        self.assertEqual(a.name, "persona.db")
        self.assertIn("sessions", str(a))

    def test_db_path_inside_session_dir(self):
        got = self.db_path("s_x")
        self.assertEqual(got.parent.name, "s_x")

    def test_project_id_prefix(self):
        self.assertTrue(self.make_project_id("s_1").startswith("prj_web_"))

    def test_project_id_sanitized_and_stable(self):
        raw = "s_20261105_120000/a b"
        pid = self.make_project_id(raw)
        self.assertNotIn("/", pid)
        self.assertNotIn(" ", pid)
        self.assertEqual(pid, self.make_project_id(raw))


class TestSessionLabel(unittest.TestCase):
    def setUp(self):
        self.label = _load_function("session_label", {})
        self.unique = _load_function("unique_label", {})

    def test_group_name_wins(self):
        self.assertEqual(self.label("大观园", ["林黛玉", "马斯克"]), "大观园")

    def test_names_fallback_with_ellipsis(self):
        got = self.label("", ["甲", "乙", "丙", "丁"])
        self.assertEqual(got, "甲、乙、丙…")

    def test_names_fallback_exact_three(self):
        self.assertEqual(self.label("", ["甲", "乙", "丙"]), "甲、乙、丙")

    def test_empty_fallback(self):
        self.assertEqual(self.label("", []), "未命名群聊")

    def test_unique_label_first_free(self):
        self.assertEqual(self.unique("A", set()), "A")

    def test_unique_label_second(self):
        self.assertEqual(self.unique("A", {"A"}), "A（2）")

    def test_unique_label_third(self):
        self.assertEqual(self.unique("A", {"A", "A（2）"}), "A（3）")


class TestSessionPersistence(unittest.TestCase):
    def setUp(self):
        self.ns = _tmp_ns()
        self.save = _load_function("save_sessions", dict(self.ns, json=json))
        self.load = _load_function("load_sessions", dict(self.ns, json=json))
        self.new_id = _load_function("new_session_id",
                                     {"time": time, "os": os})

    def test_round_trip_preserves_messages(self):
        sessions = {"s_1": {"group_name": "大观园", "names": ["林黛玉", "马斯克"],
                            "messages": [
                                {"role": "user", "name": "访客", "text": "你吃了吗",
                                 "avatar": None},
                                {"role": "assistant", "name": "林黛玉",
                                 "text": "才起来的。", "avatar": "/tmp/a.png",
                                 "emoji": "🌸"}],
                            "project_id": "prj_web_s_1"}}
        self.save(sessions)
        self.assertEqual(self.load(), sessions)

    def test_missing_file_returns_empty(self):
        self.assertEqual(self.load(), {})

    def test_corrupt_file_returns_empty(self):
        self.ns["SESSIONS_FILE"].write_text("{not json", encoding="utf-8")
        self.assertEqual(self.load(), {})

    def test_non_dict_file_returns_empty(self):
        self.ns["SESSIONS_FILE"].write_text("[1,2]", encoding="utf-8")
        self.assertEqual(self.load(), {})

    def test_session_ids_unique(self):
        ids = {self.new_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)
        self.assertTrue(next(iter(ids)).startswith("s_"))


if __name__ == "__main__":
    unittest.main()
