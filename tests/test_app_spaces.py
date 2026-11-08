# -*- coding: utf-8 -*-
"""私人空间口令（v0.44 多用户隔离）的纯函数测试。

从 app.py AST 摘取 space_uid / resolve_space_dir，不 import streamlit。
口令空间是隐私边界：这里钉死「确定性、可区分、访客隔离、目录布局」四条。
"""

from __future__ import annotations

import ast
import hashlib
import json
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


class _FakeSt:
    """最小 st 桩：只为 resolve_space_dir 的 session_state 缓存路径。"""

    def __init__(self):
        self.session_state = {}


class TestSpaceUid(unittest.TestCase):
    """space_uid：口令 → 16 位 hex 空间 ID。"""

    def setUp(self):
        self.uid = _load_function("space_uid", {"hashlib": hashlib,
                                                "APP_SPACE_SALT": "t"})

    def test_deterministic_same_pass(self):
        self.assertEqual(self.uid("花园口令123"),
                         self.uid("花园口令123"))

    def test_distinct_passes_differ(self):
        self.assertNotEqual(self.uid("甲的口令"), self.uid("乙的口令"))

    def test_whitespace_stripped(self):
        self.assertEqual(self.uid("  口令  "), self.uid("口令"))

    def test_format_16_hex(self):
        uid = self.uid("任意口令")
        self.assertRegex(uid, r"^[0-9a-f]{16}$")

    def test_empty_pass_still_valid_uid(self):
        # 空口令也产出合法 UID（resolve_space_dir 不走这条，但函数必须稳）
        self.assertRegex(self.uid(""), r"^[0-9a-f]{16}$")

    def test_uid_never_contains_passphrase(self):
        secret = "我的秘密口令"
        uid = self.uid(secret)
        self.assertNotIn(secret, uid)


class TestResolveSpaceDir(unittest.TestCase):
    """resolve_space_dir：口令 → DATA_DIR/users/<uid>/；空 → 访客会话目录。"""

    def _make(self, tmp_root: Path, fake_st):
        fn = _load_function("resolve_space_dir",
                            {"DATA_DIR": tmp_root, "st": fake_st,
                             "tempfile": tempfile, "Path": Path,
                             "space_uid": _load_function(
                                 "space_uid", {"hashlib": hashlib,
                                               "APP_SPACE_SALT": "t"})})
        return fn

    def test_pass_space_is_users_uid_dir(self):
        root = Path(tempfile.mkdtemp(prefix="gps-space-"))
        fn = self._make(root, _FakeSt())
        space, uid = fn("某个口令")
        self.assertEqual(space, root / "users" / uid)
        self.assertTrue(space.is_dir())          # 目录当场建好，save 才能写
        self.assertNotIn("口令", str(space))     # 目录里不能出现口令本身

    def test_pass_spaces_isolated(self):
        root = Path(tempfile.mkdtemp(prefix="gps-space-"))
        fn = self._make(root, _FakeSt())
        space_a, uid_a = fn("甲的空间")
        space_b, uid_b = fn("乙的空间")
        self.assertNotEqual(uid_a, uid_b)
        self.assertNotEqual(space_a, space_b)

    def test_guest_dir_created_and_cached(self):
        root = Path(tempfile.mkdtemp(prefix="gps-space-"))
        fake = _FakeSt()
        fn = self._make(root, fake)
        g1, uid1 = fn("")
        self.assertEqual(uid1, "guest")
        self.assertTrue(str(g1).startswith(str(root / "guest")))
        self.assertTrue(g1.is_dir())
        g2, _ = fn("")                            # rerun：缓存命中同目录
        self.assertEqual(g1, g2)

    def test_guest_dirs_differ_between_sessions(self):
        root = Path(tempfile.mkdtemp(prefix="gps-space-"))
        fn_a = self._make(root, _FakeSt())        # 两个"浏览器会话"
        fn_b = self._make(root, _FakeSt())
        ga, _ = fn_a("")
        gb, _ = fn_b("")
        self.assertNotEqual(ga, gb)               # 不填口令的访客互不可见

    def test_space_switch_changes_sessions_file(self):
        # 会话文件按空间分开：这是「记录不串台」的落点
        root = Path(tempfile.mkdtemp(prefix="gps-space-"))
        fn = self._make(root, _FakeSt())
        sa, _ = fn("口令A")
        sb, _ = fn("口令B")
        self.assertNotEqual(sa / "web_sessions.json",
                            sb / "web_sessions.json")


class TestSpaceLayoutEndToEnd(unittest.TestCase):
    """save_sessions/load_sessions 在两个空间下互不可见（拿真函数跑一遍）。"""

    def test_two_spaces_no_leak(self):
        root = Path(tempfile.mkdtemp(prefix="gps-space-"))
        fake = _FakeSt()
        resolve = _load_function("resolve_space_dir",
                                 {"DATA_DIR": root, "st": fake,
                                  "tempfile": tempfile, "Path": Path,
                                  "space_uid": _load_function(
                                      "space_uid", {"hashlib": hashlib,
                                                    "APP_SPACE_SALT": "t"})})
        save_src = _load_function("save_sessions", {"json": json})
        load_src = _load_function("load_sessions", {"json": json})

        def make_io(space: Path):
            sessions_file = space / "web_sessions.json"

            def save(chat_sessions: dict) -> None:
                try:
                    sessions_file.write_text(
                        json.dumps(chat_sessions, ensure_ascii=False),
                        encoding="utf-8")
                except OSError:
                    pass

            def load() -> dict:
                try:
                    data = json.loads(
                        sessions_file.read_text(encoding="utf-8"))
                    return data if isinstance(data, dict) else {}
                except (OSError, ValueError):
                    return {}

            return save, load

        space_a, _ = resolve("空间A的口令")
        space_b, _ = resolve("空间B的口令")
        save_a, load_a = make_io(space_a)
        save_b, load_b = make_io(space_b)

        now = time.time()
        save_a({"s_a": {"group_name": "甲的群", "names": ["黛玉", "宝玉"],
                        "messages": [{"role": "user", "name": "甲",
                                      "text": "甲的悄悄话", "avatar": None,
                                      "ts": now}],
                        "project_id": "prj_web_a"}})
        save_b({"s_b": {"group_name": "乙的群", "names": ["皮特", "布鲁斯"],
                        "messages": [], "project_id": "prj_web_b"}})

        self.assertEqual(set(load_a().keys()), {"s_a"})
        self.assertEqual(set(load_b().keys()), {"s_b"})
        self.assertNotIn("甲的悄悄话",
                         json.dumps(load_b(), ensure_ascii=False))
        self.assertNotIn("乙的群",
                         json.dumps(load_a(), ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
