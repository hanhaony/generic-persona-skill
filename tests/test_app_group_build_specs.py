"""`app.py` 混合世界观建档分组的守卫测试（2026-11-04 P0）。

# 缺陷现场

旧壳只有一个全局「作品名称」输入框，一次 `run` 只能带一个
`--type` + `--work-title`——用户实测 5 人跨阵营（现实演员 + 两个作品的
角色同场）在 Web 端**无法一次建档**：填了作品全员虚构同宇宙，
不填全员真人。

# 修复语义（group_build_specs）

- 真实人物：全部合成一批（`--type REAL_PERSON`，无作品）；
- 虚构角色：按**归一化作品名**分批（同键 = 同一次 run = 同一宇宙）。

# 为什么用 AST 抽取

`app.py` 模块级 `import streamlit`，本仓库测试纪律是零第三方依赖——
同 `test_app_parse_names.py` 的做法，用 `ast` 把纯函数摘出来执行。

# 归一化为什么必须对齐引擎

引擎 `dialogue.normalize_work_title` 决定跨宇宙认知的"同作品"判定。
壳里分组若不复刻同款归一化（去空白/书名号、小写），用户给托尼填
「漫威」、给皮特填「《漫威》 」就会生成两个作品标签——引擎把同阵营
战友判成陌生人。分批错 = 认知错，所以两侧规则必须逐字一致。
"""

from __future__ import annotations

import ast
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


class TestNormalizeWorkTitle(unittest.TestCase):
    def setUp(self):
        self.norm = _load_function("normalize_work_title", {"re": re})

    def test_matches_engine_semantics(self):
        """大小写 / 空白 / 书名号归一——与引擎判定键逐字一致。"""
        self.assertEqual(self.norm("Marvel"), self.norm("  marvel "))
        self.assertEqual(self.norm("《漫威》"), self.norm("漫威"))
        self.assertEqual(self.norm("复仇者 联盟"), self.norm("复仇者联盟"))
        self.assertEqual(self.norm(""), "")
        self.assertEqual(self.norm(None), "")  # type: ignore[arg-type]


class TestGroupBuildSpecs(unittest.TestCase):
    def setUp(self):
        norm = _load_function("normalize_work_title", {"re": re})
        self.group = _load_function("group_build_specs",
                                    {"re": re,
                                     "normalize_work_title": norm})

    def test_mixed_camp_splits_into_three_batches(self):
        """用户验收场景：唐尼 REAL + 托尼/皮特 漫威 + 布鲁斯 DC。"""
        types = {"小罗伯特唐尼": "真实人物", "托尼史塔克": "虚构角色",
                 "皮特帕克": "虚构角色", "布鲁斯韦恩": "虚构角色"}
        works = {"小罗伯特唐尼": "", "托尼史塔克": "漫威",
                 "皮特帕克": "漫威", "布鲁斯韦恩": "DC"}
        specs = self.group(types, works)
        self.assertEqual(len(specs), 3)
        self.assertEqual(specs[0], {"entity_type": "REAL_PERSON",
                                    "work_title": "",
                                    "names": ["小罗伯特唐尼"]})
        self.assertEqual(specs[1], {"entity_type": "FICTIONAL_CHARACTER",
                                    "work_title": "漫威",
                                    "names": ["托尼史塔克", "皮特帕克"]})
        self.assertEqual(specs[2], {"entity_type": "FICTIONAL_CHARACTER",
                                    "work_title": "DC",
                                    "names": ["布鲁斯韦恩"]})

    def test_title_normalization_joins_same_universe(self):
        """「漫威」与「《漫威》 」必须同批——否则引擎判成两个宇宙。"""
        types = {"托尼": "虚构角色", "皮特": "虚构角色"}
        works = {"托尼": "漫威", "皮特": "《漫威》 "}
        specs = self.group(types, works)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["names"], ["托尼", "皮特"])

    def test_all_real_persons_share_one_batch(self):
        types = {"A": "真实人物", "B": "真实人物"}
        specs = self.group(types, {"A": "", "B": ""})
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["entity_type"], "REAL_PERSON")
        self.assertEqual(specs[0]["names"], ["A", "B"])

    def test_fictional_without_work_is_skipped(self):
        """虚构角色没填作品 → 跳过不编造（前置校验是调用方的责任）。"""
        types = {"托尼": "虚构角色", "A": "真实人物"}
        specs = self.group(types, {"托尼": "", "A": ""})
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0]["entity_type"], "REAL_PERSON")

    def test_empty_config_gives_empty_list(self):
        self.assertEqual(self.group({}, {}), [])
        self.assertEqual(self.group({"X": "虚构角色"}, {}), [])


if __name__ == "__main__":
    unittest.main()
