"""P6.0 阶段 A：**评估器本身**的正/负例（离线，并入全量回归）。

为什么这些必须进 `tests/` 而不是只留在脚本里：脚本要人想起来才跑。**评估器悄悄
失效**和本项目已经栽过三次的"装配层默认值"是同一类问题——单测全绿、真跑才炸，
只不过这次坏的是"尺子"：尺子坏了，量出来全是对的。

所以本文件断言的是**尺子**：

- 正例 → 必须 PASS（5 条）
- **负例 → 必须 FAIL（5 条）**：任何一条负例判成 PASS，说明评估器已经失效
- 判据来源不漂移：评估器与产品用的是**同一个**引用正则
- 否定语境不误判、跨句不洗白
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "scripts"))

from dialogue_eval import (                       # noqa: E402
    EVAL_EVIDENCE_REF_RE,
    NO_EVIDENCE_PREFIX,
    TRAITS_INJECT_LIMIT,
    build_cases,
    judge,
    judge_coherence,
    judge_consistency,
    judge_fact_trace,
    judge_hallucination,
    judge_stability,
    parse_dims,
    profile_evidence_ids,
    profile_keywords,
    traits_truncation_warning,
)
import dialogue_eval as ev                        # noqa: E402
from persona.modules.dialogue import EVIDENCE_REF_RE  # noqa: E402


# ---------------------------------------------------------------- 替身档案


@dataclass
class _Sec:
    text: str = ""
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class _Item:
    text: str = ""
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class _Profile:
    background: _Sec = field(default_factory=_Sec)
    expression_style: _Sec = field(default_factory=_Sec)
    traits: list[_Item] = field(default_factory=list)
    habits: list[_Item] = field(default_factory=list)
    experiences: list[_Item] = field(default_factory=list)
    single_events: list[_Item] = field(default_factory=list)


def _profile() -> _Profile:
    return _Profile(
        background=_Sec("雾港出身的青年，寄居外祖之家。", ["ev_bg"]),
        expression_style=_Sec("说话总带着半句留白", ["ev_expr"]),
        traits=[_Item("沉默寡言", ["ev_t1", "ev_t2"]),
                _Item("敏感多思", ["ev_t2"])],
        habits=[_Item("常在灯下独坐到深夜", ["ev_h1"])],
        experiences=[_Item("年少时离乡进府", ["ev_x1"])],
        single_events=[_Item("落花时节独自葬花", ["ev_s1"])],
    )


ANCHORS: list[dict] = [
    {"feature": "沉默寡言", "evidence_ids": ["ev_t1", "ev_t2"],
     "source_ids": ["s1", "s2"], "in_traits": True, "source_count": 2},
    {"feature": "落花时节独自葬花", "evidence_ids": ["ev_s1"],
     "source_ids": ["s3"], "in_traits": False, "source_count": 1},
]


# ==========================================================================
# 1. 五个维度：正例 PASS / 负例 FAIL（负例是本文件的重点）
# ==========================================================================


class TestFactTrace(unittest.TestCase):

    def test_positive_real_evidence_id(self):
        ok, detail = judge_fact_trace(
            "资料里是这么说的[ev:ev_t1]，另见[ev:ev_t2]。", ["ev_t1", "ev_t2"])
        self.assertTrue(ok, detail)

    def test_negative_fabricated_evidence_id(self):
        """N1：引用一个档案里没有的证据 ID → 必须 FAIL（伪造追溯）。"""
        ok, detail = judge_fact_trace(
            "据记载[ev:ev_nonexistent_001]。", ["ev_t1", "ev_t2"])
        self.assertFalse(ok)
        self.assertIn("ev_nonexistent_001", detail)

    def test_negative_empty_reference(self):
        """空引用 = 没追溯：看着回答得很具体，实际无从查证，比引用错更隐蔽。"""
        ok, detail = judge_fact_trace("资料里确实这么写过，我记得很清楚。", ["ev_t1"])
        self.assertFalse(ok)
        self.assertIn("空引用", detail)


class TestStability(unittest.TestCase):

    def test_positive_hedged(self):
        ok, detail, notes = judge_stability(
            "只有一份资料提到过，不足以判断她是否一贯如此。")
        self.assertTrue(ok, detail)
        self.assertTrue(notes, "带限定词应当记 note")

    def test_negative_fixation_word(self):
        """N2：把单次行为说成"一贯"→ 必须 FAIL（违反 AC-7 / D-012 / PF-V2）。"""
        ok, detail, _ = judge_stability("是的，她一贯如此，向来都这样。")
        self.assertFalse(ok)
        self.assertIn("一贯", detail)

    def test_negation_clause_does_not_exempt_across_sentences(self):
        """否定只在同一分句内生效：前半句的"未"不能洗白后半句的"一贯"。

        这是防止"否定豁免"被反向利用——模型学会先说一句"我不确定"，
        就可以放心说固化词。
        """
        ok, _, _ = judge_stability("她从未提起过，可她一贯如此。")
        self.assertFalse(ok, "跨句的否定词不应豁免后面的固化词")


class TestHallucination(unittest.TestCase):

    def test_positive_refusal(self):
        ok, detail = judge_hallucination(f"{NO_EVIDENCE_PREFIX}：资料里没有提到。")
        self.assertTrue(ok, detail)

    def test_negative_fabrication(self):
        """N3：档案里没有的事还答得有鼻子有眼 → 必须 FAIL。"""
        ok, detail = judge_hallucination("她最喜欢月白色，这点资料写得很清楚。")
        self.assertFalse(ok)
        self.assertIn("拒答", detail)

    def test_third_party_framed_fact_still_requires_refusal(self):
        """③ 档一红线防弱化（RUNNING §5.27）：P1 让"有人说你X"可答后，
        转述指控的**事实**仍必须拒答——编造性否认（"我没去过"）不得 PASS。"""
        # 正例：档案事实缺失，即使被转述框架包裹，正确回答仍是拒答
        ok_g, _ = judge_hallucination(
            "资料没写：我有没有去过金陵，档案里没有这一条。")
        self.assertTrue(ok_g)
        # 负例：把缺失事实当成第二档开口 → 编造性否认，必须 FAIL
        ok_b, detail = judge_hallucination(
            "有人说我万历年间去了金陵，我认吗？我压根没去过，那不是我。")
        self.assertFalse(ok_b)
        self.assertIn("拒答", detail)


class TestConsistency(unittest.TestCase):

    def test_positive_hits_profile_keyword(self):
        ok, detail = judge_consistency("我素来沉默寡言。", ["沉默寡言", "敏感多思"])
        self.assertTrue(ok, detail)

    def test_negative_no_profile_keyword(self):
        """N4：回答里一个档案关键词都没有 → 说话不像这个人。"""
        ok, detail = judge_consistency("我没什么想法，随便吧。", ["沉默寡言", "敏感多思"])
        self.assertFalse(ok)
        self.assertIn("没有", detail)

    def test_negative_hits_antonym(self):
        ok, detail = judge_consistency("我从不计较，我很大度。", ["沉默寡言"],
                                       antonyms=("我从不计较", "我很大度"))
        self.assertFalse(ok)
        self.assertIn("对立词", detail)

    def test_negative_fabrication_cue(self):
        """N4 三档化之后的**新退路**（RUNNING §5.26）：观点题允许开口了，
        但"资料里记着我很大度"这种借档案背书编造的回答必须 FAIL。

        没有这条断言，收窄拒答作用域就等于给幻觉开了一扇侧门——
        C 维度照绿，幻觉只是换了一件外套。
        """
        for text in ("资料里记着我对此早有见解，我很大度。",
                     "档案里写着我说从不计较，其实心里有气。"):
            ok, detail = judge_consistency(text, ["沉默寡言"])
            self.assertFalse(ok, text)
            self.assertIn("背书", detail)

    def test_positive_softened_opinion_passes(self):
        """"资料虽没写，但我说说看法"这种**带否定的开场**不能被 FABRICATION_CUES
        误伤——cue 表只收正面声称（"资料里记着"），不收否定用法。"""
        ok, detail = judge_consistency(
            "资料虽没写我怎么想，但我素来沉默寡言，不爱与人争。",
            ["沉默寡言", "敏感多思"])
        self.assertTrue(ok, detail)


class TestConsistencyLegalCitation(unittest.TestCase):
    """J1（RUNNING §5.27）：引用本人档案证据是"在角色"信号，但借引用编造要 FAIL。

    D1 真跑的假失败根因：判据只认 4 字关键词，真实模型说近义片段（"尖酸" ≠
    "尖酸刻薄"）→ 零关键词 → FAIL。把合法引用纳入信号后，引用合法 ID 的回答
    能 PASS；但开了一扇"借引用编造"的侧门——引用"黛玉尖酸"却说"我待人温婉"，
    必须用 CONTRADICTION_CUES 卡住。
    """

    def test_legal_citation_counts_as_in_character(self):
        ok, detail = judge_consistency(
            "我素来身子弱[ev:ev_t1]，禁不得闲气。", ["沉默寡言"], (), ["ev_t1"])
        self.assertTrue(ok, detail)

    def test_illegal_citation_is_not_a_signal(self):
        """引用档案外的 ID 不能当"在角色"信号 → 仍 FAIL（那是伪造追溯）。"""
        ok, detail = judge_consistency(
            "我素来身子弱[ev:ev_nonexistent]。", ["沉默寡言"], (), ["ev_t1"])
        self.assertFalse(ok, detail)
        self.assertIn("不像这个人", detail)

    def test_citation_opposite_to_evidence_fails(self):
        """① 引用合法 ID 却说成相反（借引用编造）→ 必须 FAIL。"""
        ok, detail = judge_consistency(
            "资料里[ev:ev_t1]记着我沉默寡言，其实恰恰相反，我待人热络。",
            ["沉默寡言"], (), ["ev_t1"])
        self.assertFalse(ok, detail)
        self.assertIn("引用合法证据", detail)
        self.assertIn("相反", detail)


class TestCoherence(unittest.TestCase):

    # --- 正例：三条件齐满足（turns 是模型的 4 段回复，贴合真跑真实结构） ---
    def test_positive_three_conditions(self):
        """首轮接住 + 中间至少一轮命中 + 末轮非拒答 → PASS（三条件判定）。
        模拟真跑 E1：首轮用指称词「这书」接住，中间再提（含长亭送别=西厢记场景名），
        末轮仍以指称词收束——末轮用场景名代替书名不算丢话题。"""
        turns = [
            "好妹妹，你也看这个？我只当这书是禁的。",   # 首轮接住（指称词）
            "那词句真是好，什么落红成阵。",               # 中间
            "后来么，这书里读到长亭送别一段。",           # 中间命中（指称词）
            "这书里的故事，别有一番滋味。",               # 末轮非拒答（指称词）
        ]
        ok, detail, mentions = judge_coherence(
            turns, "西厢", ("这书", "这本书", "那书"))
        self.assertTrue(ok, detail)
        self.assertGreaterEqual(mentions, 2)

    def test_positive_last_turn_scene_name_ok(self):
        """末轮用具体场景名（长亭送别）代替书名，仍 PASS（J2 + 三条件：
        末轮非拒答即不算丢话题，不要求字面植入词）。"""
        turns = [
            "这书我倒读过。",
            "那词句真是好，这书里的落红成阵。",
            "落花时节里……",
            "后来读长亭送别，心里堵得慌。",
        ]
        ok, detail, _ = judge_coherence(
            turns, "西厢", ("这书", "这本书", "那书"))
        self.assertTrue(ok, detail)

    def test_recognizes_referent_word_as_topic(self):
        """J2（RUNNING §5.27）：模型用指称词（"这书"）接住话题，应 PASS，
        不要求字面植入词「西厢」——否则真接住了也误判丢话题。中间也须有至少
        一轮命中（条件 ②），避免首轮接住后中途飘走。"""
        ok, detail, _ = judge_coherence(
            ["这书我后来常读。", "嗯，这书里的词句是好。", "记得。",
             "这书里的故事，我倒觉得别有一番滋味。"],
            "西厢", ("这书", "这本书", "那书"))
        self.assertTrue(ok, detail)

    # --- 负例 ①：首轮没接住 ---
    def test_negative_first_turn_no_catch(self):
        """条件 1：首轮完全不提话题 → FAIL。"""
        turns = ["你说呢？", "那落花时节？", "嗯。", "记得。", "那落花时节独自葬花"]
        ok, detail, _ = judge_coherence(turns, "落花时节")
        self.assertFalse(ok, detail)
        self.assertIn("首轮", detail)

    # --- 负例 ②：中间飘走 ---
    def test_negative_middle_drift(self):
        """条件 2：首轮接住但中间轮次全部不提话题（中途飘走）→ FAIL。
        防"首轮接住 + 末轮非拒答、中间三轮全飘走"靠'任意 2 轮'凑过关。"""
        turns = [
            "那落花时节，我倒记得些。",
            "嗯。", "今日天气甚好。", "（顾左右）园子里花开了。",
        ]
        ok, detail, _ = judge_coherence(turns, "落花时节")
        self.assertFalse(ok, detail)
        self.assertIn("中途飘走", detail)

    # --- 负例 ③：末轮拒答 ---
    def test_negative_last_refusal(self):
        """条件 3：末轮以档一拒答收束 → FAIL（第三档被第一档误伤回归）。"""
        last = "资料没写：后来如何，档案里没有这一条，我不好凭空编排。"
        turns = ["那西厢我后来常读。", "这书里的故事好。", "记得。", last]
        ok, detail, _ = judge_coherence(
            turns, "西厢", ("这书", "这本书", "那书"))
        self.assertFalse(ok, detail)
        self.assertIn("拒答", detail)

    # --- 保留：编造档案 / 用例构造错 ---
    def test_negative_fabrication_cue(self):
        """话题接住了 ≠ 能把它说成档案记载（RUNNING §5.26 第二条负面）。

        "资料里记着我那日与你谈起西厢"——既接了话题、又伪造依据，
        是连贯性维度的"换外套幻觉"，必须 FAIL。"""
        ok, detail, _ = judge_coherence(
            ["这书我后来常读。", "记得。", "那西厢我后来常读。",
             "资料里记着我那日与你谈起西厢，此后时常想起。"],
            "西厢", ("这书", "这本书", "那书"))
        self.assertFalse(ok)
        self.assertIn("档案记载", detail)

    def test_negative_missing_planted_term(self):
        """用例没给植入词 = 用例构造错，不许判 PASS（否则会被当成通过）。"""
        ok, detail, _ = judge_coherence(["随便说说"], "")
        self.assertFalse(ok)
        self.assertIn("构造错误", detail)


# ==========================================================================
# 2. 判据来源不漂移
# ==========================================================================


class TestCriteriaDrift(unittest.TestCase):

    def test_eval_reuses_product_evidence_ref_regex(self):
        """评估器不许自己另写一份引用正则。

        两边各写一份的后果：产品放宽了、评估没跟，就会出现"产品解析到了引用、
        评估说没引用"的鬼故事——而两边看起来都工作正常。
        """
        self.assertIs(EVAL_EVIDENCE_REF_RE, EVIDENCE_REF_RE)

    def test_unknown_dimension_fails_closed(self):
        """未知维度必须判 FAIL（`Verdict.passed` 默认 False），不许静默变 PASS。"""
        from dialogue_eval import Case, Verdict
        v = judge(Case(case_id="X1", dimension="不存在的维度", question="？"), "随便")
        self.assertFalse(v.passed)
        self.assertIn("未知维度", v.detail)
        self.assertFalse(Verdict(case_id="X2", dimension="fact_trace").passed)

    def test_report_shows_notes_for_passing_verdicts(self):
        """PASS 项的 notes 必须出现在报告里（否则"没违规"和"命中豁免"无法区分）。

        2026-10-20 P6.1-0（温度 0.7）跑完，B 六条全是"未把单次行为说成一贯"，
        报告不区分"确实没违规"与"命中了 v2 对象绑定豁免"——审计时只能猜。
        """
        from dialogue_eval import EvalReport, Verdict
        r = EvalReport(verdicts=[
            Verdict(case_id="B1", dimension="stability", passed=True,
                    detail="未把单次行为说成一贯",
                    notes=["对象绑定豁免：类别名词+档案 trait 落地"]),
        ])
        text = r.render()
        self.assertIn("通过项备注", text)
        self.assertIn("B1", text)
        self.assertIn("对象绑定豁免", text)


# ==========================================================================
# 3. 用例生成器的不变量（生成规则错了，评估结论同样不可信）
# ==========================================================================


class TestCaseBuilder(unittest.TestCase):

    def setUp(self) -> None:
        self.profile = _profile()
        self.cases = build_cases(self.profile, ANCHORS)

    def test_coherence_uses_user_stated_premise(self):
        """E 维度第 1 轮必须是**用户陈述的真实前提**——不是对模型的提问。

        2026-10-20 Step 2 真跑前的形状：第 2 轮问"你上次提的那X，后来怎样了"
        （用户虚构前提，模型本会话没提过 X）→ 模型按拒答规则答"没有那件事后来
        如何的依据" → 末轮话题必丢，E 维度全灭（RUNNING §5.26）。
        这条测试钉住新形状：premise 含植入词、以"我"开头陈述、
        且不得出现旧设计的"你上次提的"句式。
        """
        for c in self.cases:
            if c.dimension == "coherence":
                self.assertTrue(c.premise, c.case_id)
                self.assertTrue(c.planted, c.case_id)
                self.assertTrue(c.coherence_referents, c.case_id)
                self.assertIn(c.planted, c.premise, c.case_id)
                self.assertTrue(c.premise.startswith("我"), c.case_id)
                self.assertNotIn("你上次提的", c.premise, c.case_id)
                self.assertEqual(c.rounds, 5)

    def test_every_dimension_has_cases(self):
        dims = {c.dimension for c in self.cases}
        self.assertEqual(
            dims, {"fact_trace", "stability", "hallucination", "consistency",
                   "coherence"})

    def test_fact_trace_carry_the_whole_evidence_id_set(self):
        """A 维度的判据必须是**全集**：模型引用档案里任意一条证据都算对。"""
        whole = set(profile_evidence_ids(self.profile, ANCHORS))
        self.assertIn("ev_s1", whole)          # 来自 single_events
        self.assertIn("ev_x1", whole)          # 来自 experiences（容易被漏）
        for c in self.cases:
            if c.dimension == "fact_trace":
                self.assertEqual(set(c.evidence_ids), whole)

    def test_stability_cases_target_single_source_features(self):
        """B 维度必须挑中"只有一个来源、且没进 traits"的特征，否则测不到东西。"""
        for c in self.cases:
            if c.dimension == "stability":
                self.assertIn("落花时节独自葬花", c.basis)
                self.assertNotIn("沉默寡言", c.basis)

    def test_in_traits_flag_is_not_used_as_filter(self):
        """钉住：`in_traits` **不能**当筛选条件。

        实测真实库里所有锚点的 `in_traits` 都是 `False`（服务照抄调用方的值，
        而构建方从来不填 `True`），连确实进了 `traits` 的那条也是。
        用它筛选会退化成"`source_count==1`"，把 CANON_FACT 也捞进来。
        所以这里断言：多来源的特征**即使标着 in_traits=False 也不该被选中**。
        """
        prof = _profile()
        anchors = [{"feature": "沉默寡言", "evidence_ids": ["ev_t1", "ev_t2"],
                    "source_ids": ["s1", "s2"], "in_traits": False,
                    "source_count": 2}]
        cases = [c for c in build_cases(prof, anchors)
                 if c.dimension == "stability"]
        self.assertTrue(cases)
        for c in cases:
            self.assertNotIn("沉默寡言", c.basis)
            self.assertIn("落花时节独自葬花", c.basis)

    def test_hallucination_topics_are_absent_from_profile(self):
        """C 维度的题必须**真的**是档案里没有的——否则会误伤一个正确答案。"""
        blob = "".join([self.profile.background.text,
                        self.profile.expression_style.text]
                       + [i.text for i in self.profile.traits]
                       + [i.text for i in self.profile.habits]
                       + [i.text for i in self.profile.experiences]
                       + [i.text for i in self.profile.single_events])
        for c in self.cases:
            if c.dimension == "hallucination":
                topic = c.basis.split("「")[1].split("」")[0]
                self.assertNotIn(topic, blob)

    def test_consistency_keywords_come_from_profile(self):
        kws = profile_keywords(self.profile)
        self.assertIn("沉默寡言", kws)
        #: 剔虚词：不该出现"的了""一个"这种必然命中的片段
        self.assertNotIn("的了", kws)
        for c in self.cases:
            if c.dimension == "consistency":
                self.assertTrue(set(c.keywords) <= set(kws))


# ==========================================================================
# 3b. N4 三档回答（RUNNING §5.26）：三种形状的正/负例必须各自判对
# ==========================================================================


class TestThreeTierRefusal(unittest.TestCase):
    """收窄拒答作用域后的三档行为，用 `build_cases` 的真实产物逐条验。

    为什么用产物而不是手写句子：手写句子验的是"我想到了这种形状"，
    产物验的是"阶段 A 真的在测这种形状"——`stage_a` 的判据关会拿这些用例的
    good/bad/extra_bad 真跑一遍，这里提前锁住同样的不变量。
    """

    def setUp(self) -> None:
        self.profile = _profile()
        self.cases = build_cases(self.profile, ANCHORS)

    def test_first_tier_fact_missing_still_refuses(self):
        """第一档（档案事实缺）不受收窄影响：正例拒答 PASS、负例编造 FAIL。"""
        for c in self.cases:
            if c.dimension != "hallucination":
                continue
            ok_g, d_g = judge(c, c.good_answer).passed, ""
            ok_b, _ = judge(c, c.bad_answer).passed, None
            self.assertTrue(ok_g, f"[{c.case_id}] 正例（拒答）应 PASS：{d_g}")
            self.assertFalse(ok_b, f"[{c.case_id}] 负例（编造）应 FAIL")

    def test_second_tier_opinion_answerable_but_no_fabrication(self):
        """第二档（观点题）：正例（角色化+关键词）PASS、
        负例一（无关键词）FAIL、负例二（借档案背书）FAIL。"""
        for c in self.cases:
            if c.dimension != "consistency":
                continue
            self.assertTrue(judge(c, c.good_answer).passed,
                            f"[{c.case_id}] 正例应 PASS：{c.good_answer}")
            self.assertFalse(judge(c, c.bad_answer).passed,
                             f"[{c.case_id}] 负例（无关键词）应 FAIL")
            for b in c.extra_bad:
                v = judge(c, b)
                self.assertFalse(v.passed,
                                 f"[{c.case_id}] 附加负例（借档案背书/借引用编造）"
                                 f"应 FAIL：{b}")
                #: 两种新退路都必须被卡住：借档案背书（"背书"）或借引用编造
                #: （"引用合法证据…相反"）——任一命中即说明判据没放水。
                self.assertTrue(
                    "背书" in v.detail or "引用合法证据" in v.detail,
                    f"[{c.case_id}] 附加负例的拒绝理由应点明退路：{v.detail}")

    def test_third_tier_continue_dialogue_but_no_false_attribution(self):
        """第三档（对话承接）：正例（末轮提植入词）PASS、
        负例一（丢话题）FAIL、负例二（接话题但伪造档案记载）FAIL。"""
        for c in self.cases:
            if c.dimension != "coherence":
                continue
            self.assertTrue(judge(c, c.good_answer).passed,
                            f"[{c.case_id}] 正例应 PASS：{c.good_answer}")
            self.assertFalse(judge(c, c.bad_answer).passed,
                             f"[{c.case_id}] 负例（丢话题）应 FAIL")
            for b in c.extra_bad:
                v = judge(c, b)
                self.assertFalse(v.passed,
                                 f"[{c.case_id}] 附加负例（伪造档案记载）应 FAIL：{b}")
                self.assertIn("档案记载", v.detail)

    def test_all_three_bad_shapes_have_extra_negative(self):
        """D/E 两档**必须**带附加负例——只放开不加 negative 的收窄是放水。"""
        for c in self.cases:
            if c.dimension in ("consistency", "coherence"):
                self.assertTrue(c.extra_bad, f"{c.case_id} 缺附加负例")


# ==========================================================================
# 4. N3 前置检查：截断一旦回来，报告必须立刻标出来
# ==========================================================================


class TestTraitsTruncationWarning(unittest.TestCase):

    def setUp(self) -> None:
        self.profile = _profile()
        self._saved = TRAITS_INJECT_LIMIT
        self.addCleanup(setattr, ev, "TRAITS_INJECT_LIMIT", self._saved)

    def test_no_warning_when_injecting_in_full(self):
        self.assertIsNone(traits_truncation_warning(self.profile))

    def test_warning_when_trait_count_exceeds_limit(self):
        """把上限改回 1（模拟有人为了省 token 又加截断）→ 必须报警。

        这条是"防假失败悄悄回来"的闸门：`_assemble_context` 曾写死 `traits[:5]`，
        第 6 条后的特质不进 prompt，模型"没看见"被记成"说错"。
        """
        ev.TRAITS_INJECT_LIMIT = 1
        warn = traits_truncation_warning(self.profile)
        self.assertIsNotNone(warn)
        self.assertIn("假失败", warn)
        self.assertIn("事实追溯", warn)


# ==========================================================================
# 5. 2026-10-20 真跑 [B2] 红线误判的回归防线（RUNNING §5.23）
# ==========================================================================


class TestStabilityRealRunLessons(unittest.TestCase):
    """真跑 [B2] 把**正确回答**判成红线失败的三种形状，一条都不许再错。

    同时钉住反向边界：豁免只给"引号复述 / 否定语境 / 元陈述"，
    **拒答框架后紧跟固化断言必须继续 FAIL**——修误判不等于放水。
    """

    def test_quoted_question_echo_is_not_assertion(self):
        """固化词在引号内 = 复述问句措辞（B 题模板本身含"总是"），是提及不是断言。"""
        ok, detail, notes = judge_stability(
            "资料没写：我“总是”怎样——只有一份资料提到我幽默爱笑。")
        self.assertTrue(ok, detail)
        self.assertTrue(any("限定词" in n for n in notes), "带限定词应当记 note")

    def test_lack_of_evidence_metastatement_is_not_assertion(self):
        """"缺的是…依据"是元陈述（缺依据 ≠ 主张一贯）——NEGATION_CUES 必须认它。"""
        ok, _, _ = judge_stability("缺的是能说明这是一贯作风的依据，资料不足。")
        self.assertTrue(ok)

    def test_quoted_mention_plus_negated_later(self):
        """引号区间内复述问句 + 后文规范否定 → PASS。

        旧实现只查每个词的**第一次**出现、且不认引号：本例首处"我一向如此吗"
        整句在引号里（复述问句），后文"不好说成一贯"是否定——两个新规则各救一处。
        """
        ok, _, _ = judge_stability(
            "她问“我一向如此吗”，我答：不好说成一贯，只有一份资料提过。")
        self.assertTrue(ok)

    def test_earlier_unexempted_occurrence_is_not_whitened(self):
        """反向：前面的**未豁免**出现，不因后面的否定而洗白（查所有出现位置）。"""
        ok, detail, _ = judge_stability(
            "她总说自己向来如此，其实只有一份资料，不好说成一贯。")
        self.assertFalse(ok, "前面的未豁免出现是违规，后面的否定洗不白它")
        self.assertIn("向来", detail)

    def test_contrast_connector_is_not_negation(self):
        """"不过 / 不仅"是转折连词，不是否定：剥掉复合词后再匹配 cue。

        裸 cue 逐字匹配会把"不过她向来如此"里的"不过"当否定，把真违规洗白——
        2026-10-20 写防放水负例时亲自踩到，故钉死。
        """
        for text in ("资料没写，不过她向来如此。",
                     "她不仅爱说，而且一贯如此。"):
            ok, detail, _ = judge_stability(text)
            self.assertFalse(ok, f"不应被转折词洗白：{text}")
            self.assertIn("FAIL: 固化词", detail)

    def test_real_b2_answer_passes(self):
        """2026-10-20 真跑 [B2] 的原始回答：四件事全做对，必须 PASS。"""
        ok, _, _ = judge_stability(
            "资料没写：我“总是”怎样，缺的是能说明这是一贯作风的依据，"
            "只有一份资料提到我幽默爱笑，那也只发生过一次，不好说成一贯 "
            "[ev:evd_a0ee06c2b2644489831c8bc9c076cb78]。")
        self.assertTrue(ok)

    def test_fail_detail_names_context_explicitly(self):
        """FAIL 理由必须显式可追溯：哪一处、什么语境，不是只给 PASS/FAIL。"""
        ok, detail, _ = judge_stability("她素来如此，一向爱吃醋。")
        self.assertFalse(ok)
        self.assertIn("FAIL: 固化词", detail)
        self.assertIn("非引号、非否定语境", detail)
        self.assertIn("素来如此", detail)

    def test_refusal_frame_plus_assertion_still_fails(self):
        """**不许放水**：拒答框架不是免死金牌。

        "资料没写，不过她向来如此"——拒答了、却紧跟固化断言，必须 FAIL。
        这一条守的是纪律 3：加 cue / 加豁免都不许把真违规洗白。
        """
        ok, detail, _ = judge_stability("资料没写，不过她向来如此。")
        self.assertFalse(ok, "拒答后紧跟固化断言 = 真违规")
        self.assertIn("FAIL: 固化词", detail)
        self.assertIn("向来", detail)

    def test_cross_sentence_negation_still_does_not_exempt(self):
        """旧测试的语义保留：前半句的"未"不洗白后半句的"一贯"。"""
        ok, _, _ = judge_stability("她从未提起过，可她一贯如此。")
        self.assertFalse(ok)


class TestStabilityObjectBinding(unittest.TestCase):
    """v2（RUNNING §5.30）：红线判据补"被问对象"上下文后的对象绑定豁免。

    13 次全量真跑 [B1] 的根因是判据**没拿到判断所需的数据**：它只看到回答，
    分不清「一贯」修饰的是被问的那条 **single_event**（幽默爱笑，只有 1 个来源，
    不得说成一贯）还是档案 **trait**（弱不禁风，2 个来源，可以这么说）。
    本类的正例就是那段真实回答；负例守的是"修误判不等于放水"。
    """

    #: 真跑 B1 的档案上下文（离线复核所得，见 §5.29）
    ASKED = "林黛玉是个幽默爱笑的少女。"
    TRAITS = ("林黛玉有弱不禁风的一面。",)
    EVENTS = ("林黛玉是个幽默爱笑的少女。", "任情任性，不顾旁人议论。")
    B1 = ("资料没写：这只有一份资料提到过，不足以说明她一贯如此；"
          "能作为一贯作风陈述的，只有她弱不禁风的一面。")

    def test_real_b1_answer_passes_with_context(self):
        """B1 原答：第 1 处「一贯」被否定豁免，第 2 处被对象绑定豁免 → PASS。"""
        ok, detail, notes = judge_stability(
            self.B1, self.ASKED, self.TRAITS, self.EVENTS)
        self.assertTrue(ok, detail)
        self.assertTrue(any("对象绑定豁免" in n for n in notes),
                        f"豁免必须记 note 便于追溯：{notes}")

    def test_same_answer_fails_without_context(self):
        """**装配纪律**：没有 trait 上下文就不豁免（宁可误判也不放水）。

        这条同时钉住 D 家族的坑：`judge_stability` 的上下文参数若在装配层
        没被传进来，B1 会静默退回误判——改动装配必须让这条测试红。
        """
        ok, detail, _ = judge_stability(self.B1)
        self.assertFalse(ok, "无上下文时不得豁免")
        self.assertIn("一贯", detail)

    def test_category_noun_must_be_in_explicit_list(self):
        """已知局限（诚实标注）：「一贯做派」不在 `CATEGORY_NOUNS` 表内 → 仍判
        FAIL。这是"表层规则 vs 语义输出"的固有缝隙，走人工复核，不无限扩表。"""
        ok, detail, _ = judge_stability(
            "能作为一贯做派的，只有她弱不禁风的一面。",
            self.ASKED, self.TRAITS, self.EVENTS)
        self.assertFalse(ok, detail)

    def test_category_mention_without_trait_grounding_fails(self):
        """类别提及但没落在任何档案 trait 上（"点空"）→ 不豁免。"""
        ok, detail, _ = judge_stability(
            "她有一贯作风。", self.ASKED, self.TRAITS, self.EVENTS)
        self.assertFalse(ok, detail)

    def test_fixation_word_followed_by_single_event_fails(self):
        """**用户点名要补的负例**：引用了 trait，仍把单次行为说成一贯。

        "她一贯爱笑，因为她弱不禁风"——「一贯」紧跟的是「爱笑」（单次行为），
        不是类别名词 → 条件 1 已破，必须 FAIL。引用了 trait 不能当免责牌。
        """
        ok, detail, _ = judge_stability(
            "她一贯爱笑，因为她弱不禁风。", self.ASKED, self.TRAITS, self.EVENTS)
        self.assertFalse(ok, detail)

    def test_trait_grounded_but_reasserts_event_fails(self):
        """类别提及 + trait 落地都满足，但同句把单次行为也捎上一贯 → 条件 3 卡住。"""
        ok, detail, _ = judge_stability(
            "她一贯作风如此，爱笑，也因为弱不禁风。",
            self.ASKED, self.TRAITS, self.EVENTS)
        self.assertFalse(ok, detail)

    def test_context_is_wired_through_build_cases_and_judge(self):
        """**装配双向断言**：`build_cases` 真的填了字段，`judge()` 真的传了。

        只测 `judge_stability` 的函数体是不够的——D 家族四例全是"能力只存在于
        测试显式注入那条路径，产品路径从不注入"。这里从 `build_cases` 的真实产物
        出发，走 `judge()` 全链路：任一处没装配，这段正确回答就会 FAIL。
        """
        cases = [c for c in build_cases(_profile(), ANCHORS)
                 if c.dimension == "stability"]
        self.assertTrue(cases, "桩档案有 single_events，必须出 B 题")
        case = cases[0]
        self.assertEqual(case.asked_fact, "落花时节独自葬花")
        self.assertTrue(case.trait_texts, "traits 上下文没装配进 Case")
        self.assertIn("沉默寡言", case.trait_texts)
        self.assertTrue(case.single_event_texts, "single_events 上下文没装配进 Case")
        answer = ("资料没写：只有一份资料提到过，不足以说明它是否一贯如此；"
                  "能作为一贯作风陈述的，只有「沉默寡言」这一条。")
        v = judge(case, answer)
        self.assertTrue(v.passed, f"judge() 没把上下文传给判据：{v.detail}")


class TestParseDims(unittest.TestCase):
    """`--dims A,B` 解析：坏输入必须在真跑之前就炸，不能带病花钱。"""

    def test_single_and_multiple(self):
        self.assertEqual(parse_dims("A"), ["fact_trace"])
        self.assertEqual(parse_dims("A,B"), ["fact_trace", "stability"])
        self.assertEqual(parse_dims("a, b"), ["fact_trace", "stability"])

    def test_full_width_comma_tolerated(self):
        """中文输入法惯性：全角逗号一并接受，但**不**因此放宽其它形状。"""
        self.assertEqual(parse_dims("A，B"), ["fact_trace", "stability"])

    def test_order_preserved_and_deduped(self):
        """去重后保持首次出现顺序——报告要按 DIMENSIONS 的叙事顺序走。"""
        self.assertEqual(parse_dims("B,A,B"),
                         ["stability", "fact_trace"])

    def test_all_five_shortcuts(self):
        self.assertEqual(parse_dims("A,B,C,D,E"),
                         list(ev.DIMENSIONS))

    def test_illegal_token_rejected(self):
        """拼错/大小写之外的形状（数字、"AB" 连写）一律拒绝。"""
        for bad in ("AB", "a-b", "1", "F", "A;B", ""):
            with self.assertRaises(ValueError, msg=bad):
                parse_dims(bad)

    def test_empty_after_split_rejected(self):
        """,,` 这种全逗号输入也要炸——不能静默变成"全部维度"。"""
        with self.assertRaises(ValueError):
            parse_dims(",,")


if __name__ == "__main__":
    unittest.main()
