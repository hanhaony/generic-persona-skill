"""实体别名容错映射（灰度修复）：模型把显示名填进 subject 时不再误杀。

# 缺陷现场（灰度实测，Ben Affleck / Matt Damon）

用户输入 `benaffleck, mattdamon` 建档后，模型抽取的候选证据把 `subject`
填成了**全名**（如 `Ben Affleck`）而不是系统分配的 `pe_xxx`。严格白名单
按"不在材料声明对象、也不在本次目标里"整条拒收（`UNKNOWN_SUBJECT`），
英文人物实测 **0 证据**，整个 run 的钱白花。

# 修复语义

- 显示名能**唯一**对上一个目标（忽略大小写、忽略全部空白）⇒ 自动换算成
  规范 ID 放行，并留痕 `SUBJECT_ALIAS_REMAPPED`（不静默）；
- 对不上的照旧拒收：**防编造引用的闸门没有放松**，这里救的只是
  "显示名 vs ID"这一种已实测的写法偏差；
- 归一化后两个目标折叠成同一个键 ⇒ 不猜，别名直接作废。

# 本文件没有调用任何真实模型

端到端用的是 `tests.test_p4_4a_model_extraction` 的 `StubChatProvider`
（本地固定字符串）；素材沿用该文件声明的虚构人物纪律——本文件因场景所需
使用**英文虚构人名**（Ben Affleck 式），不含对真实人物的事实断言。
"""

from __future__ import annotations

import unittest

from persona.core.context import new_call_context
from persona.core.result import unwrap
from persona.core.types import SourceMaterial
from persona.modules.claim_validation import (
    ISSUE_SUBJECT_ALIAS_REMAPPED,
    REJECT_UNKNOWN_SUBJECT,
    VERDICT_OK,
    subject_alias_map,
    validate_model_payload,
)
from persona.modules.extract_quality import QUALITY_VALID
from persona.modules.extraction import ProviderClaimExtractor
from persona.providers.mock import network_blocked
from tests.test_p4_4a_model_extraction import StubChatProvider

PROJECT = "prj_alias"

EN_SNIPPET = (
    "Two friends from Cambridge wrote a screenplay about a janitor at MIT, "
    "and their longtime collaborator directed the film that made them famous."
)


def _en_material(*, subjects: list[str]) -> SourceMaterial:
    return SourceMaterial(
        source_id="src_en", title="English film trivia page", url="https://example.invalid/en",
        canonical_key="key_en", snippet=EN_SNIPPET,
        subject_entity_ids=list(subjects), quality_status=QUALITY_VALID)


def _targets() -> list[dict]:
    return [
        {"entity_id": "pe_affleck", "display_name": "Ben Affleck"},
        {"entity_id": "pe_damon", "display_name": "Matt Damon"},
    ]


def _claim(**kw) -> dict:
    base = {
        "key": "c1",
        "material_key": "key_en",
        "claim": "The two friends co-wrote the screenplay.",
        "tier": "PROFILE_BACKGROUND",
        "dimension": "BACKGROUND",
        "subject_entity_ids": ["Ben Affleck"],
        "confidence": 0.7,
    }
    base.update(kw)
    return base


def _payload(claims: list[dict]) -> str:
    import json
    return json.dumps({"claims": claims}, ensure_ascii=False)


class TestAliasMap(unittest.TestCase):
    def test_display_names_map_to_entity_ids(self):
        # 键是**归一化后**的形态（去空白、小写）——查找时对模型输出做同一变换
        self.assertEqual(
            subject_alias_map([], _targets()),
            {"benaffleck": "pe_affleck", "mattdamon": "pe_damon"})

    def test_dict_form_name_key_is_accepted(self):
        m = subject_alias_map([], [{"entity_id": "pe_x", "name": "阿雾"}])
        self.assertEqual(m, {"阿雾": "pe_x"})

    def test_collision_is_dropped_not_guessed(self):
        """两个目标折叠成同一个键 ⇒ 别名作废（宁可拒收也不瞎改写）。"""
        targets = [
            {"entity_id": "pe_1", "display_name": "Ben Affleck"},
            {"entity_id": "pe_2", "display_name": "ben  affleck"},
        ]
        self.assertEqual(subject_alias_map([], targets), {})


class TestSubjectAliasTolerance(unittest.TestCase):
    def _validate(self, claims: list[dict], **kw):
        return validate_model_payload(
            _payload(claims), materials=kw.pop("materials", [_en_material(
                subjects=["pe_affleck", "pe_damon"])]),
            targets=kw.pop("targets", _targets()), **kw)

    def test_display_name_subject_is_remapped_not_rejected(self):
        result = self._validate([_claim()])
        self.assertEqual(result.verdict, VERDICT_OK)
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.accepted[0].subject_entity_ids, ["pe_affleck"])
        self.assertIn(ISSUE_SUBJECT_ALIAS_REMAPPED, result.accepted[0].issues)
        self.assertTrue(any("显示名" in s for s in result.issues),
                        f"留痕缺失：{result.issues}")

    def test_case_and_whitespace_are_ignored(self):
        for written in ("ben affleck", "BEN AFFLECK", "benaffleck", "Ben  Affleck"):
            result = self._validate([_claim(subject_entity_ids=[written])])
            self.assertEqual(len(result.accepted), 1, written)
            self.assertEqual(result.accepted[0].subject_entity_ids,
                             ["pe_affleck"], written)

    def test_mixed_id_and_display_name_both_survive(self):
        result = self._validate([_claim(
            key="c2", subject_entity_ids=["pe_affleck", "Matt Damon"])])
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.accepted[0].subject_entity_ids,
                         ["pe_affleck", "pe_damon"])

    def test_chinese_display_name_remapping(self):
        """中文同享容错（不许只有英文受益，也不许中文逻辑回退）。"""
        material = SourceMaterial(
            source_id="src_cn", title="雾港设定资料集",
            url="https://example.invalid/cn", canonical_key="key_cn",
            snippet="阿雾负责看守雾港灯塔。", subject_entity_ids=["pe_awu"],
            quality_status=QUALITY_VALID)
        result = validate_model_payload(
            _payload([{
                "key": "c3", "material_key": "key_cn",
                "claim": "阿雾负责看守雾港灯塔", "tier": "PROFILE_BACKGROUND",
                "dimension": "BACKGROUND", "subject_entity_ids": ["阿雾"],
                "confidence": 0.7}]),
            materials=[material],
            targets=[{"entity_id": "pe_awu", "display_name": "阿雾"}])
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.accepted[0].subject_entity_ids, ["pe_awu"])

    def test_truly_unknown_subject_is_still_rejected(self):
        """对不上的照旧拒收：防编造的闸门没有放松。"""
        result = self._validate([_claim(subject_entity_ids=["Someone Else"])])
        self.assertEqual(result.accepted, [])
        self.assertEqual([r.reason for r in result.rejected],
                         [REJECT_UNKNOWN_SUBJECT])

    def test_collision_falls_back_to_rejection(self):
        targets = [
            {"entity_id": "pe_1", "display_name": "Ben Affleck"},
            {"entity_id": "pe_2", "display_name": "ben  affleck"},
        ]
        result = validate_model_payload(
            _payload([_claim()]), materials=[_en_material(subjects=["pe_1"])],
            targets=targets)
        self.assertEqual(result.accepted, [])
        self.assertEqual([r.reason for r in result.rejected],
                         [REJECT_UNKNOWN_SUBJECT])


class TestEnglishExtractionEndToEnd(unittest.TestCase):
    """英文材料端到端（Mock）：显示名主体被换算后顺利产出 >0 条候选。"""

    def test_english_material_yields_candidates(self):
        provider = StubChatProvider(_payload([
            _claim(key="c1", subject_entity_ids=["Ben Affleck"]),
            _claim(key="c2", subject_entity_ids=["mattdamon"],
                   claim="The collaborator directed the film."),
        ]))
        extractor = ProviderClaimExtractor(provider)
        ctx = new_call_context(project_id=PROJECT, actor="tests")
        with network_blocked():
            outcome = unwrap(extractor.extract(
                ctx, [_en_material(subjects=["pe_affleck", "pe_damon"])],
                targets=_targets()))
        self.assertEqual(len(outcome.claims), 2)          # >0：不再整批误杀
        self.assertEqual(outcome.verdict, VERDICT_OK)
        subjects = {s for c in outcome.claims for s in c.subject_entity_ids}
        self.assertEqual(subjects, {"pe_affleck", "pe_damon"})   # 已换算成规范 ID


if __name__ == "__main__":
    unittest.main()
