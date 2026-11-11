"""P8.2-B：人格蒸馏投影层 `distill_profile()` 模块测试。

验收口径（P8.2-B 任务书 §5，逐条对应）：
1. PATTERN 特征只进 A 层；2. REPEATED 只进 B 层（恒 tentative）；
3. SINGLE 不进 A/B（防御性落 C 层 anomalous_traits）；4. open_questions 进 C 层；
5. status != OK 暴露在 C 层；6. 第三方评价占比可识别展示；
7. time_span 不丢失；8. conflicts_visible 保留冲突引用；
9. 不修改原始 PersonaProfile；10. 无数据库写入与 AI 调用路径。
外加：habits→B、证据缺失不伪造、SIMULATED 防御过滤、幂等、入参校验、
静态守卫（FORBIDDEN_FIELD_PATTERN / 依赖方向）。
"""

from __future__ import annotations

import ast
import copy
import unittest
from pathlib import Path

from persona.core.enums import EvidenceTier, ProfileStatus, Stability
from persona.core.result import is_err, is_ok
from persona.modules.distill import (
    LAYER_STABLE,
    LAYER_TENTATIVE,
    DistilledTrait,
    distill_profile,
)
from persona.modules.evidence import EvidenceView
from persona.modules.persona import (
    CanonBoundary,
    ProfileItem,
    ProfileSection,
    PersonaProfileView,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "persona" / "modules" / "distill.py"

# 32 位十六进制，与 ids.new_id 输出一致
ENTITY_ID = "pe_1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c60"


def _item(iid: str, text: str, stability: Stability = Stability.UNKNOWN,
          refs: list[str] | None = None) -> ProfileItem:
    return ProfileItem(item_id=iid, text=text, confidence=0.8,
                       evidence_refs=refs if refs is not None else [f"evd_{iid}"],
                       stability=stability)


def _profile(traits=None, habits=None, single_events=None,
             open_questions=None, status=ProfileStatus.OK) -> PersonaProfileView:
    return PersonaProfileView(
        id="pnf_test_v1", entity_id=ENTITY_ID, version=1,
        namespace="SYSTEM_DERIVED",
        background=ProfileSection(), traits=list(traits or []),
        habits=list(habits or []), expression_style=ProfileSection(),
        experiences=[], single_events=list(single_events or []),
        canon_boundary=CanonBoundary(), applicable_variant_ids=[],
        do_not_apply_to_variants=[], controversial_topic_ids=[],
        open_questions=list(open_questions or []), coverage={},
        status=status, superseded_by=None, change_reason="")


def _ev(eid: str, tier=EvidenceTier.VERBATIM, source_id="src_a",
        stated_at=None, conflict_ids=None, namespace="PUBLIC_SOURCE") -> EvidenceView:
    return EvidenceView(
        id=eid, claim=f"claim of {eid}", tier=str(tier), dimension="TRAIT",
        namespace=namespace, source_id=source_id, source_event_ids=[],
        subject_entity_ids=[ENTITY_ID], stability="UNKNOWN", status="ACTIVE",
        conflict_ids=list(conflict_ids or []),
        stated_at=stated_at)


def _lookup(**views) -> dict:
    """构造 evidence_lookup：evd_xxx -> EvidenceView。未注册的查不到（None）。"""
    return {f"evd_{k}": v for k, v in views.items()}


class DistillLayeringTests(unittest.TestCase):
    """任务书 §5.1~5.5：A/B/C 分层。"""

    def test_1_pattern_only_in_layer_a(self):
        prof = _profile(traits=[
            _item("t_pat", "极度谨慎", Stability.PATTERN),
            _item("t_rep", "偶尔失言", Stability.REPEATED)])
        result = distill_profile(prof, lambda _: None)
        self.assertTrue(is_ok(result))
        view = result.value
        self.assertEqual([t.item_id for t in view.stable_traits], ["t_pat"])
        self.assertEqual(view.stable_traits[0].layer, LAYER_STABLE)
        self.assertFalse(view.stable_traits[0].tentative)
        self.assertEqual([t.item_id for t in view.tentative_patterns], ["t_rep"])

    def test_2_repeated_only_in_layer_b_and_tentative(self):
        prof = _profile(traits=[_item("t_rep", "偶尔失言", Stability.REPEATED)])
        view = distill_profile(prof, lambda _: None).value
        self.assertEqual(len(view.stable_traits), 0)
        self.assertEqual([t.item_id for t in view.tentative_patterns], ["t_rep"])
        self.assertTrue(view.tentative_patterns[0].tentative)
        self.assertEqual(view.tentative_patterns[0].layer, LAYER_TENTATIVE)

    def test_3_single_never_in_a_or_b(self):
        # AC-7 正常把 SINGLE 挡在 single_events；这里防御 traits 里混入 SINGLE
        prof = _profile(traits=[_item("t_sin", "某次失态", Stability.SINGLE)],
                        single_events=[_item("se1", "单次行为", Stability.SINGLE)])
        view = distill_profile(prof, lambda _: None).value
        all_ab = [t.item_id for t in (*view.stable_traits, *view.tentative_patterns)]
        self.assertNotIn("t_sin", all_ab)
        self.assertNotIn("se1", all_ab)
        self.assertEqual([r.item_id for r in view.insufficient.anomalous_traits],
                         ["t_sin"])
        self.assertEqual([r.item_id for r in view.insufficient.single_events], ["se1"])

    def test_4_open_questions_go_to_layer_c(self):
        prof = _profile(open_questions=["values 未定", "动机缺口"])
        view = distill_profile(prof, lambda _: None).value
        self.assertEqual(view.insufficient.open_questions,
                         ["values 未定", "动机缺口"])

    def test_5_non_ok_status_exposed_in_layer_c(self):
        prof = _profile(status=ProfileStatus.CONFLICTED)
        view = distill_profile(prof, lambda _: None).value
        self.assertEqual(view.insufficient.profile_status, "CONFLICTED")
        self.assertIn("CONFLICTED", view.insufficient.status_note)
        ok_view = distill_profile(_profile(status=ProfileStatus.OK),
                                  lambda _: None).value
        self.assertEqual(ok_view.insufficient.profile_status, "OK")
        self.assertEqual(ok_view.insufficient.status_note, "")


class DistillVisibilityTests(unittest.TestCase):
    """任务书 §5.6~5.8：tier 构成 / 时间跨度 / 冲突可见。"""

    def test_6_tier_composition_and_third_party_ratio(self):
        # 混合 VERBATIM + THIRD_PARTY：构成与占比都可见
        lookup = _lookup(
            t_mix_a=_ev("evd_t_mix_a", tier=EvidenceTier.VERBATIM,
                        source_id="src_a"),
            t_mix_b=_ev("evd_t_mix_b", tier=EvidenceTier.THIRD_PARTY,
                        source_id="src_b"))
        prof = _profile(traits=[_item(
            "t_mix", "敢言", Stability.REPEATED,
            refs=["evd_t_mix_a", "evd_t_mix_b"])])
        view = distill_profile(prof, lookup.get).value
        trait = view.tentative_patterns[0]
        self.assertEqual(trait.tier_composition,
                         {"VERBATIM": 1, "THIRD_PARTY": 1})
        self.assertAlmostEqual(trait.third_party_ratio, 0.5)
        self.assertEqual(trait.source_ids, ["src_a", "src_b"])
        self.assertEqual(trait.source_count, 2)
        # 全局依据同口径
        self.assertEqual(view.confidence_basis.tier_totals,
                         {"VERBATIM": 1, "THIRD_PARTY": 1})
        self.assertAlmostEqual(view.confidence_basis.third_party_ratio, 0.5)

    def test_7_time_span_preserved_and_missing_time_is_none(self):
        from persona.core.temporal import FuzzyDate
        lookup = _lookup(t_ts=_ev("evd_t_ts",
                                  stated_at=FuzzyDate(raw="1905 年",
                                                      start="1905-01-01")),
                         t_ts2=_ev("evd_t_ts2",
                                   stated_at=FuzzyDate(raw="1921 年",
                                                       start="1921-06-01")))
        prof = _profile(traits=[_item(
            "t_ts", "谨慎", Stability.PATTERN,
            refs=["evd_t_ts", "evd_t_ts2"])])
        view = distill_profile(prof, lookup.get).value
        self.assertEqual(view.stable_traits[0].time_span,
                         ("1905-01-01", "1921-06-01"))
        # 时间不可考 → 如实 None，不编造
        prof2 = _profile(traits=[_item("t_ts", "谨慎", Stability.PATTERN)])
        view2 = distill_profile(prof2, lambda _: None).value
        self.assertIsNone(view2.stable_traits[0].time_span)

    def test_8_conflicts_visible_keeps_references(self):
        lookup = _lookup(t_c=_ev("evd_t_c", conflict_ids=["cnf_001"]))
        prof = _profile(traits=[_item("t_c", "冲动", Stability.REPEATED)])
        view = distill_profile(prof, lookup.get).value
        self.assertEqual(len(view.conflicts_visible), 1)
        pointer = view.conflicts_visible[0]
        self.assertEqual(pointer.evidence_id, "evd_t_c")
        self.assertEqual(pointer.conflict_ids, ["cnf_001"])
        self.assertEqual(pointer.item_id, "t_c")
        # 冲突不删除、不裁决：特征本身仍在 B 层
        self.assertEqual([t.item_id for t in view.tentative_patterns], ["t_c"])


class DistillSafetyTests(unittest.TestCase):
    """任务书 §5.9~5.10 + 防御性口径。"""

    def test_9_profile_not_mutated(self):
        prof = _profile(
            traits=[_item("t1", "谨慎", Stability.PATTERN),
                    _item("t2", "失言", Stability.REPEATED)],
            habits=[_item("hb1", "先观察")],
            single_events=[_item("se1", "单次")],
            open_questions=["q1"])
        snapshot = copy.deepcopy(prof)
        distill_profile(prof, _lookup(t1=_ev("evd_t1")).get)
        self.assertEqual(repr(prof), repr(snapshot))
        # dataclass 字段逐一比对（repr 相同再兜一层）
        self.assertEqual(vars(prof), vars(snapshot))

    def test_10_no_db_write_no_ai_path(self):
        """静态验证（AST）：distill.py 不 import 存储/Provider/网络模块，
        不出现任何 .execute/.chat/.put 等写或调用路径；签名无 ctx。
        运行时验证：同一输入两次调用结果逐值相等（幂等，零副作用）。"""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        banned_modules = ("sqlite", "storage", "provider", "requests", "httpx")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0].lower()
                    self.assertNotIn(root, banned_modules,
                                     f"distill.py 不应 import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                root = str(node.module or "").split(".")[0].lower()
                self.assertNotIn(root, banned_modules,
                                 f"distill.py 不应 from {node.module} import")
            elif isinstance(node, ast.Call):
                func = node.func
                attr = func.attr.lower() if isinstance(func, ast.Attribute) else ""
                self.assertNotIn(attr, ("execute", "chat", "executemany"),
                                 f"distill.py 不应调用 .{attr}()")
        import inspect
        from persona.modules import distill as mod
        sig = inspect.signature(mod.distill_profile)
        self.assertEqual(list(sig.parameters), ["profile", "evidence_lookup"])
        # 运行时验证：投影不改变外部状态——重复调用结果逐值相等（幂等）
        prof = _profile(traits=[_item("t1", "谨慎", Stability.PATTERN)])
        lookup = _lookup(t1=_ev("evd_t1"))
        v1 = distill_profile(prof, lookup.get).value
        v2 = distill_profile(prof, lookup.get).value
        self.assertEqual(v1, v2)

    def test_11_habits_go_to_layer_b_tentative(self):
        prof = _profile(habits=[_item("hb1", "遇冲突先观察")])
        view = distill_profile(prof, lambda _: None).value
        self.assertEqual([t.item_id for t in view.tentative_patterns], ["hb1"])
        self.assertTrue(view.tentative_patterns[0].tentative)
        self.assertEqual(len(view.stable_traits), 0)

    def test_12_missing_evidence_not_fabricated(self):
        # 引用查不到：记 missing，不计入构成统计
        lookup = _lookup(t1=_ev("evd_t1", tier=EvidenceTier.VERBATIM))
        prof = _profile(traits=[_item(
            "t1", "谨慎", Stability.PATTERN,
            refs=["evd_t1", "evd_ghost"])])
        view = distill_profile(prof, lookup.get).value
        trait = view.stable_traits[0]
        self.assertEqual(trait.evidence_count, 2)   # 引用了 2 条是事实
        self.assertEqual(trait.tier_composition, {"VERBATIM": 1})
        self.assertEqual(view.confidence_basis.missing_evidence_ids, ["evd_ghost"])
        self.assertNotIn("evd_ghost", trait.tier_composition)

    def test_13_simulated_evidence_excluded_defensively(self):
        # EV-V1 在证据层已结构性拒绝；这里钉住投影对"任意 lookup 结果"的防御
        lookup = _lookup(t1=_ev("evd_t1"),
                         t1_sim=_ev("evd_t1_sim", namespace="SIMULATED"))
        prof = _profile(traits=[_item(
            "t1", "谨慎", Stability.PATTERN,
            refs=["evd_t1", "evd_t1_sim"])])
        view = distill_profile(prof, lookup.get).value
        trait = view.stable_traits[0]
        self.assertEqual(trait.tier_composition, {"VERBATIM": 1})
        self.assertEqual(view.confidence_basis.simulated_evidence_ids,
                         ["evd_t1_sim"])

    def test_14_input_validation(self):
        from persona.core.result import is_err
        self.assertTrue(is_err(distill_profile("not-a-profile", lambda _: None)))
        prof = _profile(traits=[_item("t1", "谨慎", Stability.PATTERN)])
        self.assertTrue(is_err(distill_profile(prof, "not-callable")))

    # ------------------------------------------------------------------
    # P8.2-C 修复回归（D1 / D2 / D3 / D5 + 组合）
    # ------------------------------------------------------------------

    def test_d1_layer_c_conflicts_collected(self):
        """D1：C 层（single_events / anomalous_traits）证据冲突进 conflicts_visible。"""
        lookup = _lookup(se=_ev("evd_se", conflict_ids=["cnf_002"]),
                         an=_ev("evd_an", conflict_ids=["cnf_003"]))
        prof = _profile(
            traits=[_item("t_sin", "某次失态", Stability.SINGLE,
                          refs=["evd_an"])],          # 防御性落 anomalous_traits
            single_events=[_item("se1", "单次行为", Stability.SINGLE,
                                 refs=["evd_se"])])
        view = distill_profile(prof, lookup.get).value
        c_ptrs = [p for p in view.conflicts_visible if p.layer == "C"]
        self.assertEqual({p.evidence_id for p in c_ptrs},
                         {"evd_se", "evd_an"})
        self.assertEqual(
            {p.item_id for p in c_ptrs}, {"se1", "t_sin"})
        self.assertEqual({tuple(p.conflict_ids) for p in c_ptrs},
                         {("cnf_002",), ("cnf_003",)})

    def test_d1_conflict_pointer_dedup_across_items(self):
        """D1：多条目引用同一冲突证据只报一次（按 evidence_id 全局去重）。"""
        lookup = _lookup(sh=_ev("evd_sh", conflict_ids=["cnf_x"]))
        prof = _profile(
            traits=[_item("t1", "谨慎", Stability.PATTERN, refs=["evd_sh"]),
                    _item("t2", "冷静", Stability.REPEATED, refs=["evd_sh"])])
        view = distill_profile(prof, lookup.get).value
        self.assertEqual(len(view.conflicts_visible), 1)
        self.assertEqual(view.conflicts_visible[0].evidence_id, "evd_sh")

    def test_d1_layer_ab_conflict_collection_unchanged(self):
        """D1：A/B 层冲突收集行为不退化（原 CASE-05 口径仍成立）。"""
        lookup = _lookup(t_c=_ev("evd_t_c", conflict_ids=["cnf_001"]))
        prof = _profile(traits=[_item("t_c", "冲动", Stability.REPEATED)])
        view = distill_profile(prof, lookup.get).value
        self.assertEqual(len(view.conflicts_visible), 1)
        ptr = view.conflicts_visible[0]
        self.assertEqual((ptr.layer, ptr.evidence_id, ptr.conflict_ids),
                         ("B", "evd_t_c", ["cnf_001"]))

    def test_d2_unresolved_evidence_count(self):
        """D2：特征级 unresolved_evidence_count = missing + failed + SIMULATED。"""
        lookup = _lookup(ok=_ev("evd_ok"), sim=_ev("evd_sim",
                                                   namespace="SIMULATED"))
        prof = _profile(traits=[_item(
            "t1", "谨慎", Stability.PATTERN,
            refs=["evd_ok", "evd_ghost", "evd_sim"])])
        view = distill_profile(prof, lookup.get).value
        trait = view.stable_traits[0]
        self.assertEqual(trait.evidence_count, 3)
        self.assertEqual(trait.unresolved_evidence_count, 2)  # ghost + sim
        self.assertEqual(trait.tier_composition, {"VERBATIM": 1})
        # 全部缺失：unresolved == evidence_count，特征不伪装成有效
        prof2 = _profile(traits=[_item("t2", "冷静", Stability.REPEATED,
                                       refs=["evd_g1", "evd_g2"])])
        v2 = distill_profile(prof2, lookup.get).value
        self.assertEqual(v2.tentative_patterns[0].unresolved_evidence_count, 2)
        # 无缺失：为 0，分层不受影响
        prof3 = _profile(traits=[_item("t3", "果敢", Stability.PATTERN,
                                       refs=["evd_ok"])])
        v3 = distill_profile(prof3, lookup.get).value
        self.assertEqual(v3.stable_traits[0].unresolved_evidence_count, 0)
        self.assertEqual(len(v3.stable_traits), 1)  # D2 不改变 A/B/C 分配

    def test_d3_failed_vs_missing_distinguished(self):
        """D3：lookup 异常进 failed_evidence_ids，与 missing 分列。"""
        call_state = {"raise": True}

        def flaky(ref):
            if call_state["raise"]:
                raise RuntimeError("storage I/O failure")
            return None

        prof = _profile(traits=[_item("t1", "谨慎", Stability.PATTERN,
                                      refs=["evd_x"])])
        v_fail = distill_profile(prof, flaky).value
        self.assertEqual(v_fail.confidence_basis.failed_evidence_ids,
                         ["evd_x"])
        self.assertNotIn("evd_x", v_fail.confidence_basis.missing_evidence_ids)
        # 同一 lookup 正常返回 None → 只进 missing，不进 failed
        call_state["raise"] = False
        v_none = distill_profile(prof, flaky).value
        self.assertEqual(v_none.confidence_basis.missing_evidence_ids,
                         ["evd_x"])
        self.assertEqual(v_none.confidence_basis.failed_evidence_ids, [])
        # 同一 ID 不重复记录：两个特征引用同一异常证据只登记一次
        call_state["raise"] = True
        prof2 = _profile(traits=[
            _item("t1", "谨慎", Stability.PATTERN, refs=["evd_x"]),
            _item("t2", "冷静", Stability.REPEATED, refs=["evd_x"])])
        v2 = distill_profile(prof2, flaky).value
        self.assertEqual(v2.confidence_basis.failed_evidence_ids, ["evd_x"])
        # 条目本身仍在（异常不静默丢条目信息）：A 层特征保留，unresolved 可见
        self.assertEqual(len(v2.stable_traits), 1)
        self.assertEqual(v2.stable_traits[0].unresolved_evidence_count, 1)
        # 不输出异常堆栈或敏感内部信息
        self.assertNotIn("RuntimeError", repr(v2))
        self.assertNotIn("storage", repr(v2))

    def test_d5_dedupe_refs_preserve_order(self):
        """D5：引用去重保序，重复引用只贡献一次统计。"""
        lookup = _lookup(x=_ev("evd_x", source_id="src_x"),
                         a=_ev("evd_a", source_id="src_a"),
                         b=_ev("evd_b", source_id="src_b"))
        # 全重复
        prof = _profile(traits=[_item("t1", "谨慎", Stability.PATTERN,
                                      refs=["evd_x", "evd_x"])])
        view = distill_profile(prof, lookup.get).value
        t = view.stable_traits[0]
        self.assertEqual((t.evidence_refs, t.evidence_count),
                         (["evd_x"], 1))
        self.assertEqual(t.tier_composition, {"VERBATIM": 1})
        self.assertEqual(t.source_count, 1)
        # 混合重复：["evd_a","evd_b","evd_a"] → ["evd_a","evd_b"]
        prof2 = _profile(traits=[_item("t2", "冷静", Stability.REPEATED,
                                       refs=["evd_a", "evd_b", "evd_a"])])
        v2 = distill_profile(prof2, lookup.get).value
        t2 = v2.tentative_patterns[0]
        self.assertEqual(t2.evidence_refs, ["evd_a", "evd_b"])
        self.assertEqual(t2.evidence_count, 2)
        self.assertEqual(t2.tier_composition, {"VERBATIM": 2})
        self.assertEqual(t2.source_ids, ["src_a", "src_b"])

    def test_combined_c_conflict_missing_dup_and_failure(self):
        """组合：C 层冲突 + 缺失证据 + 重复 refs + lookup 异常，同框验证。"""
        def mixed_lookup(ref):
            if ref == "evd_boom":
                raise RuntimeError("storage I/O failure")
            if ref == "evd_dup":
                return _ev("evd_dup", conflict_ids=["cnf_dup"])
            if ref == "evd_ok":
                return _ev("evd_ok", conflict_ids=["cnf_se"])
            return None  # evd_ghost：正常缺失

        prof = _profile(
            traits=[_item("t_a", "谨慎", Stability.PATTERN,
                          refs=["evd_dup", "evd_dup", "evd_boom"]),
                    _item("t_bad", "失态", Stability.SINGLE,
                          refs=["evd_ghost"])],
            single_events=[_item("se1", "单次行为", Stability.SINGLE,
                                 refs=["evd_boom", "evd_boom", "evd_ok"])])
        view = distill_profile(prof, mixed_lookup).value
        # D5：A 层特征 refs 去重保序 + D2 unresolved 含 failed
        ta = view.stable_traits[0]
        self.assertEqual(ta.evidence_refs, ["evd_dup", "evd_boom"])
        self.assertEqual(ta.unresolved_evidence_count, 1)  # boom（failed）
        self.assertEqual(ta.tier_composition, {"VERBATIM": 1})
        # D1：C 层 single_events 冲突收集（evd_ok 带 cnf_se）+ D5：C 层 refs 去重
        se = view.insufficient.single_events[0]
        self.assertEqual(se.evidence_refs, ["evd_boom", "evd_ok"])
        c_ptr = [p for p in view.conflicts_visible if p.layer == "C"]
        self.assertEqual([p.evidence_id for p in c_ptr], ["evd_ok"])
        self.assertEqual(c_ptr[0].conflict_ids, ["cnf_se"])
        # D1：C 层 anomalous_traits（t_bad）条目保留、缺失登记
        self.assertEqual([r.item_id for r in view.insufficient.anomalous_traits],
                         ["t_bad"])
        # D3：failed 与 missing 分列
        self.assertEqual(view.confidence_basis.failed_evidence_ids,
                         ["evd_boom"])
        self.assertEqual(view.confidence_basis.missing_evidence_ids,
                         ["evd_ghost"])
        # 冲突指针：A 层 evd_dup 一个（去重），layer="A"
        a_ptrs = [p for p in view.conflicts_visible if p.layer == "A"]
        self.assertEqual([p.evidence_id for p in a_ptrs], ["evd_dup"])
        self.assertEqual(a_ptrs[0].conflict_ids, ["cnf_dup"])

    def test_15_static_forbidden_field_guard(self):
        """FORBIDDEN_FIELD_PATTERN（score/closeness/intimacy）全模块零命中。"""
        import re
        pattern = re.compile(r".*(score|closeness|intimacy).*")
        for hit in pattern.findall(source := MODULE_PATH.read_text(encoding="utf-8")):
            self.fail(f"distill.py 命中禁字段：{hit}")


if __name__ == "__main__":
    unittest.main()
