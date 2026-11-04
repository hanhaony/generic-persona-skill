"""`app.py` 实体分类推断的守卫测试（2026-11-04 P0 前端瘦身）。

# 缺陷现场

旧壳要求每位人物手动选「真实/虚构」+ 填作品名——对只想输名字就开聊的
用户极其反人类（用户原话：输入 `林黛玉, 马斯克` 就该直接开聊）。

# 修复语义（parse_classifications + classify_names）

- 模型输出宽松解析：JSON 优先，失败用正则抠 `[...]`，再失败全部兜底
  REAL_PERSON——**宁可多搜一次，不可错标虚构**（错标真人 = 档案变杜撰）。
- fictional 但作品名为空 → 强制降级 REAL_PERSON（虚构没作品 = 引擎无法
  判宇宙，跨宇宙认知会错）。
- classify_names 网络层任何异常（密钥/超时/格式）都走同一兜底，绝不挡
  建档主流程（本文件测纯函数；网络层不 mock 出网，靠兜底语义保证）。

# 为什么用 AST 抽取

`app.py` 模块级 `import streamlit`，本仓库测试纪律是零第三方依赖——
同 `test_app_group_build_specs.py` 的做法，用 `ast` 把纯函数摘出来执行。
"""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

APP_PY = Path(__file__).resolve().parent.parent / "app.py"


def _load_function(name: str, needs: dict):
    """从 app.py 里摘出一个纯函数（不触发 streamlit import）。"""
    tree = ast.parse(APP_PY.read_text(encoding="utf-8"), filename=str(APP_PY))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            namespace: dict = dict(needs)
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         str(APP_PY), "exec"), namespace)
            return namespace[name]
    fail = unittest.TestCase()
    fail.fail(f"app.py 里找不到 {name}：函数被改名或删除了？")


class TestParseClassifications(unittest.TestCase):
    def setUp(self):
        self.parse = _load_function("parse_classifications",
                                    {"json": json, "re": re})

    def test_valid_json_mixed_camp(self):
        """用户验收场景：林黛玉虚构（红楼梦）+ 马斯克真实。"""
        raw = json.dumps([
            {"name": "林黛玉", "kind": "fictional", "work": "红楼梦"},
            {"name": "马斯克", "kind": "real", "work": ""},
        ], ensure_ascii=False)
        got = self.parse(raw, ["林黛玉", "马斯克"])
        self.assertEqual(got["林黛玉"],
                         {"kind": "FICTIONAL_CHARACTER", "work": "红楼梦"})
        self.assertEqual(got["马斯克"], {"kind": "REAL_PERSON", "work": ""})

    def test_kind_aliases_normalized(self):
        """模型口吻不定：REAL_PERSON / real / 真实 都得认出来。"""
        for kind in ("REAL_PERSON", "real", "真实", "Real Person"):
            got = self.parse(
                json.dumps([{"name": "A", "kind": kind, "work": ""}]),
                ["A"])
            self.assertEqual(got["A"]["kind"], "REAL_PERSON", kind)
        for kind in ("fictional", "虚构角色", "FICTIONAL_CHARACTER"):
            got = self.parse(
                json.dumps([{"name": "B", "kind": kind,
                             "work": "漫威"}], ensure_ascii=False),
                ["B"])
            self.assertEqual(got["B"]["kind"], "FICTIONAL_CHARACTER", kind)

    def test_name_matching_ignores_case_and_spaces(self):
        raw = json.dumps([{"name": " steve rogers ", "kind": "fictional",
                           "work": "漫威"}])
        got = self.parse(raw, ["Steve Rogers"])
        self.assertEqual(got["Steve Rogers"]["work"], "漫威")

    def test_unknown_name_falls_back_to_real(self):
        """模型漏报的人名 → 兜底 REAL_PERSON（引擎直接去搜）。"""
        raw = json.dumps([{"name": "林黛玉", "kind": "fictional",
                           "work": "红楼梦"}])
        got = self.parse(raw, ["林黛玉", "马斯克"])
        self.assertEqual(got["马斯克"]["kind"], "REAL_PERSON")

    def test_fictional_without_work_downgrades_to_real(self):
        """虚构但没推断出作品 → 降级真人（没作品 = 没宇宙，搜索更稳）。"""
        raw = json.dumps([{"name": "X", "kind": "fictional", "work": ""},
                          {"name": "Y", "kind": "fictional"}])
        got = self.parse(raw, ["X", "Y"])
        self.assertEqual(got["X"]["kind"], "REAL_PERSON")
        self.assertEqual(got["Y"]["kind"], "REAL_PERSON")

    def test_garbage_output_falls_back_to_all_real(self):
        """输出不可解析（废话/截断 JSON）→ 全部兜底，绝不炸。"""
        for raw in ("", "我觉得林黛玉是虚构的", '[{"name": "林黛玉"',
                    "null"):
            got = self.parse(raw, ["林黛玉", "马斯克"])
            self.assertEqual(
                {n: c["kind"] for n, c in got.items()},
                {"林黛玉": "REAL_PERSON", "马斯克": "REAL_PERSON"}, raw)

    def test_wrapped_dict_payload_accepted(self):
        """允许 {"people": [...]} 之类的包裹结构。"""
        raw = json.dumps({"people": [
            {"name": "皮特", "kind": "fictional", "work": "漫威"}]},
            ensure_ascii=False)
        got = self.parse(raw, ["皮特"])
        self.assertEqual(got["皮特"]["work"], "漫威")

    def test_empty_names_gives_empty_map(self):
        self.assertEqual(self.parse("[]", []), {})


class TestClassifyIntegrationWithGrouping(unittest.TestCase):
    """端到端形状：parse 结果 → role_types/role_works → group_build_specs。"""

    def test_lin_daiyu_and_musk_split_into_two_batches(self):
        parse = _load_function("parse_classifications",
                               {"json": json, "re": re})
        norm = _load_function("normalize_work_title", {"re": re})
        group = _load_function("group_build_specs",
                               {"re": re, "normalize_work_title": norm})
        got = parse(json.dumps([
            {"name": "林黛玉", "kind": "fictional", "work": "红楼梦"},
            {"name": "马斯克", "kind": "real", "work": ""},
        ], ensure_ascii=False), ["林黛玉", "马斯克"])
        types = {n: ("虚构角色" if c["kind"] == "FICTIONAL_CHARACTER"
                     else "真实人物") for n, c in got.items()}
        works = {n: c["work"] for n, c in got.items()}
        specs = group(types, works)
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0]["entity_type"], "REAL_PERSON")
        self.assertEqual(specs[0]["names"], ["马斯克"])
        self.assertEqual(specs[1], {"entity_type": "FICTIONAL_CHARACTER",
                                    "work_title": "红楼梦",
                                    "names": ["林黛玉"]})

    def test_network_failure_fallback_feeds_grouping(self):
        """classify_names 失败兜底（全真）→ 一批 REAL_PERSON。"""
        norm = _load_function("normalize_work_title", {"re": re})
        group = _load_function("group_build_specs",
                               {"re": re, "normalize_work_title": norm})
        types = {"林黛玉": "真实人物", "马斯克": "真实人物"}
        specs = group(types, {"林黛玉": "", "马斯克": ""})
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["names"], ["林黛玉", "马斯克"])


if __name__ == "__main__":
    unittest.main()
