"""P4.8 D-278 保守版：`evidence_anchors` 只读证据锚点。

R4.3 实测把 `traits` 为空的账算清楚了：16 条证据 `stability` 全 `SINGLE`，
被 D-012 / PF-V2 闸门挡在 `traits` 外。根治要等受控特征标签（D-280 方案），
短期先做**保守版**——把"每个特征有几条来源提到"显示出来，**但不进 `traits`**。

这个字段因此有两条硬边界，测试逐条钉死：

1. **只读**：不参与 `coverage`、不改变任何板块的条目数、不绕过稳定性闸门；
2. **不可编造**：计数由服务从 ID 列表重算，声称 5 个来源只列 2 个 ⇒ 落盘记 2。

以及一条**反向**断言：跨源同义但措辞不同的说法在这里**仍然各占一行**——
本字段**不解决** D-278。把它写进测试是为了防止将来有人误以为它解决了。
"""

from __future__ import annotations

import unittest

from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    _anchor_view,
)

#: 覆盖度由服务计算（`PersonaService._compute_coverage`），不接受调用方填报；
#: 这里直接调它，是为了证明**锚点不会渗进覆盖度**。
_compute_coverage = PersonaService._compute_coverage
from persona.orchestrator.business import BusinessOrchestrator


# ==========================================================================
# 夹具
# ==========================================================================

class _EvidenceStub:
    """`_build_persona_draft` 只需要这些属性（duck typing，不依赖存储）。"""

    def __init__(self, evidence_id: str, claim: str, dimension: str,
                 subjects: list[str], source_id: str,
                 stability: str = "UNKNOWN", canon_level: str = "") -> None:
        self.id = evidence_id
        self.claim = claim
        self.dimension = dimension
        self.subject_entity_ids = list(subjects)
        self.source_id = source_id
        self.stability = stability
        self.effective_stability = stability
        self.canon_level = canon_level
        self.applicable_variant_ids: list[str] = []


PE_A = "pe_000000000000000000000000000001"
PE_B = "pe_000000000000000000000000000002"


def _draft(items: list[_EvidenceStub], entity_id: str = PE_A) -> PersonaProfileDraft:
    return BusinessOrchestrator._build_persona_draft(
        entity_id, items, "FICTIONAL_CHARACTER")


# ==========================================================================
# 只读性：不进 traits、不影响 coverage
# ==========================================================================

class TestAnchorsAreReadOnly(unittest.TestCase):
    def test_anchors_never_enter_traits(self):
        """锚点恒 `in_traits=False`，且 `traits` 桶不受它影响。"""
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯会拉小提琴", "TRAIT", [PE_A], "src_1"),
        ])
        self.assertEqual(len(draft.traits), 0,
                         "SINGLE 稳定性不得进 traits（AC-7 / D-012）")
        self.assertEqual(len(draft.single_events), 1)
        self.assertEqual(len(draft.evidence_anchors), 1)
        self.assertFalse(draft.evidence_anchors[0]["in_traits"])

    def test_anchors_do_not_change_coverage(self):
        """覆盖度只看六个板块；锚点是并列字段，不掺进去。"""
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯会拉小提琴", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "福尔摩斯吸烟斗", "HABIT", [PE_A], "src_1"),
        ])
        coverage = _compute_coverage(draft)
        self.assertNotIn("evidence_anchors", coverage)
        self.assertEqual(coverage["traits"]["items"], 0)
        self.assertEqual(coverage["habits"]["items"], 1)
        self.assertEqual(len(draft.evidence_anchors), 2)

    def test_profile_status_is_unaffected_by_anchors(self):
        """有证据但无可用板块时，状态仍是 OK（锚点不冒充"有内容"）。"""
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯会拉小提琴", "TRAIT", [PE_A], "src_1"),
        ])
        self.assertEqual(str(draft.status), "OK")
        self.assertFalse(draft.open_questions)


# ==========================================================================
# 计数：由服务从 ID 列表重算，不可编造
# ==========================================================================

class TestAnchorCountsAreRecomputed(unittest.TestCase):
    def test_source_count_is_recomputed_from_ids(self):
        """声称 5 个来源却只列 2 个 ⇒ 落盘记 2（防自相矛盾）。"""
        view = _anchor_view([{
            "feature": "吸烟斗", "dimension": "HABIT", "subjects": [PE_A],
            "evidence_ids": ["evd_1", "evd_2"], "source_ids": ["src_1", "src_2"],
            "source_count": 5, "evidence_count": 99,
        }])
        self.assertEqual(view[0]["source_count"], 2)
        self.assertEqual(view[0]["evidence_count"], 2)

    def test_counts_count_distinct_ids_only(self):
        """同一条列两遍不算两条——问的是"几条**不同**来源提到"。"""
        view = _anchor_view([{
            "feature": "吸烟斗", "evidence_ids": ["evd_1", "evd_1", "evd_2"],
            "source_ids": ["src_1", "src_1"],
        }])
        self.assertEqual(view[0]["evidence_count"], 2)
        self.assertEqual(view[0]["source_count"], 1)

    def test_unknown_keys_are_dropped(self):
        """白名单之外的键一律丢弃。"""
        view = _anchor_view([{"feature": "x", "伪造键": "y", "traits": ["不该有"]}])
        self.assertEqual(set(view[0]), {
            "feature", "dimension", "subjects", "evidence_ids", "source_ids",
            "evidence_count", "source_count", "in_traits"})

    def test_non_dict_entries_are_ignored(self):
        self.assertEqual(_anchor_view(["不是字典", 42, None]), [])

    def test_empty_input_yields_empty_list(self):
        self.assertEqual(_anchor_view(None), [])
        self.assertEqual(_anchor_view([]), [])


# ==========================================================================
# 归并口径：与条目一致（G12 / D-250）
# ==========================================================================

class TestAnchorGrouping(unittest.TestCase):
    def test_same_claim_from_two_sources_merges(self):
        """**措辞完全相同**且来自两个来源 ⇒ 一个锚点、`source_count=2`。"""
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯吸烟斗", "HABIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "福尔摩斯吸烟斗", "HABIT", [PE_A], "src_2"),
        ])
        self.assertEqual(len(draft.evidence_anchors), 1)
        anchor = draft.evidence_anchors[0]
        self.assertEqual(anchor["source_count"], 2)
        self.assertEqual(anchor["evidence_count"], 2)

    def test_synonymous_wording_stays_separate(self):
        """**反向断言**：跨源同义但措辞不同 ⇒ 仍然各占一行。

        这一条在证明本字段**没有**解决 D-278。写进测试是为了防止将来有人
        把"锚点数变多了"误当成"跨源归并通过了"。
        """
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯会拉小提琴", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "他是极为成功的小提琴演奏者", "TRAIT",
                          [PE_A], "src_2"),
        ])
        self.assertEqual(len(draft.evidence_anchors), 2)
        self.assertTrue(all(a["source_count"] == 1
                            for a in draft.evidence_anchors))

    def test_different_dimensions_no_longer_stay_separate(self):
        """**D-283 起语义反转**：同文本同主体、维度不同 ⇒ **一个**锚点。

        归并键 `(主体集合, tag|text)` 不含 `dimension`——R4.3 实测同一个
        事实在不同来源会被抽成不同维度（小提琴一处 HABIT 一处 TRAIT），
        维度留在键里这些永远合不到一起。锚点与条目、稳定性判定用同一把键。

        展示的 `dimension` 取组内**第一条**的维度（先到先得），
        两条证据都在 `evidence_ids` 里，可回溯。
        """
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯吸烟斗", "HABIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "福尔摩斯吸烟斗", "TRAIT", [PE_A], "src_1"),
        ])
        self.assertEqual(len(draft.evidence_anchors), 1)
        anchor = draft.evidence_anchors[0]
        self.assertEqual(anchor["evidence_count"], 2)
        self.assertEqual(anchor["dimension"], "HABIT")
        self.assertEqual(sorted(anchor["evidence_ids"]),
                         ["evd_1", "evd_2"])

    def test_different_subjects_stay_separate(self):
        draft = _draft([
            _EvidenceStub("evd_1", "上过阿富汗战场", "BACKGROUND", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "上过阿富汗战场", "BACKGROUND", [PE_B], "src_1"),
        ])
        self.assertEqual(len(draft.evidence_anchors), 2)

    def test_multi_source_anchors_sort_first(self):
        """多来源的排前面：让"哪些特征有跨源支撑"一眼可见。"""
        draft = _draft([
            _EvidenceStub("evd_1", "只有一条来源", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "两条来源都提到", "TRAIT", [PE_A], "src_1"),
            _EvidenceStub("evd_3", "两条来源都提到", "TRAIT", [PE_A], "src_2"),
        ])
        self.assertEqual(draft.evidence_anchors[0]["feature"], "两条来源都提到")
        self.assertEqual(draft.evidence_anchors[0]["source_count"], 2)
        self.assertEqual(draft.evidence_anchors[-1]["source_count"], 1)

    def test_no_evidence_yields_no_anchors(self):
        draft = _draft([])
        self.assertEqual(draft.evidence_anchors, [])


# ==========================================================================
# 端到端：草稿产出 → 落盘归一化
# ==========================================================================

class TestDraftToStorageRoundTrip(unittest.TestCase):
    def test_draft_anchors_survive_normalization(self):
        """`_build_persona_draft` 产出的锚点经 `_anchor_view` 后计数不变。"""
        draft = _draft([
            _EvidenceStub("evd_1", "福尔摩斯吸烟斗", "HABIT", [PE_A], "src_1"),
            _EvidenceStub("evd_2", "福尔摩斯吸烟斗", "HABIT", [PE_A], "src_2"),
            _EvidenceStub("evd_3", "福尔摩斯会拉小提琴", "TRAIT", [PE_A], "src_3"),
        ])
        view = _anchor_view(draft.evidence_anchors)
        self.assertEqual(len(view), 2)
        by_feature = {v["feature"]: v for v in view}
        self.assertEqual(by_feature["福尔摩斯吸烟斗"]["source_count"], 2)
        self.assertEqual(by_feature["福尔摩斯会拉小提琴"]["source_count"], 1)
        self.assertTrue(all(not v["in_traits"] for v in view))


if __name__ == "__main__":
    unittest.main()
