"""P6.2-a 规则引擎单测（stdlib unittest，零第三方依赖，零 I/O，零外呼）。

覆盖 `persona/modules/memory_writeback.py` 的：

1. **四条肯定规则**（R-EPI / R-SEM / R-REL / R-EXPL）→ kind 正确、内容正确；
2. **七条否决**（N1–N7）各至少 1–2 句 → **0 条候选**；
3. **verbatim 不变量（fuzz 50 条）**：`content in user_message` 恒成立——
   这条比任何单点用例都值钱（改写就等于引入第二个生成器，幻觉从那条路回来）；
4. **确定性**：同输入跑两遍逐字段相等（派生层幂等的前提）；
5. **三条硬上限**：单轮 ≤2 条、内容 ≤120 字、整条 ≤200 字；
6. **签名护栏**：`derive_candidates` / `derive_report` **没有**接收模型回答的参数
   ——这是回声室的唯一入口，用 `inspect.signature` 钉死；
7. **字段契约**：只给 `commit_turn` 需要的 5 个字段，不自己填 `scope`
   （填错 scope 就是 INV-1 违规）；`confidence` / `salience` 落在 [0,1]。

对应文档：`docs/P6_2_MEMORY_WRITE_PLAN.md` §2 / §5.1。
"""

from __future__ import annotations

import inspect
import random
import unittest

from persona.core.enums import MemoryKind
from persona.modules.memory_writeback import (
    MAX_CANDIDATES_PER_TURN,
    MAX_CONTENT_CHARS,
    MAX_MESSAGE_CHARS,
    RULE_CONFIDENCE,
    RULE_VERSION,
    derive_candidates,
    derive_report,
)

PARTICIPANTS = (("pe_bao", "宝玉"), ("pe_dai", "黛玉"))


# ==========================================================================
# 1. 四条肯定规则
# ==========================================================================


class TestEpisodicRule(unittest.TestCase):
    """R-EPI：第一人称 + 时间/动作 cue → EPISODIC。"""

    def test_time_cue_yields_episodic(self):
        cands = derive_candidates("昨天我去城南看了桃花")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.EPISODIC.value)
        self.assertEqual(cands[0]["content"], "昨天我去城南看了桃花")

    def test_action_cue_yields_episodic(self):
        cands = derive_candidates("我读了《西厢记》，心里喜欢")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.EPISODIC.value)

    def test_time_cue_adds_salience(self):
        with_time = derive_candidates("昨天我去看了桃花")[0]["salience"]
        without_time = derive_candidates("我去了城南看桃花")[0]["salience"]
        self.assertGreater(with_time, without_time)

    def test_third_person_only_is_not_recorded(self):
        """"他昨天去了城南"——不是用户自述，不能记成用户的事。"""
        self.assertEqual(derive_candidates("他昨天去了城南看桃花"), [])

    def test_recent_period_cue_yields_episodic(self):
        """"近来"这类模糊时段比"昨天"更常见（离线探针暴露的召回缺口）。"""
        cands = derive_candidates("我近来在读《西厢记》，越读越觉得有意思")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.EPISODIC.value)

    def test_ongoing_action_cue_yields_episodic(self):
        cands = derive_candidates("我近来在读《西厢记》")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.EPISODIC.value)

    def test_sentence_without_first_person_is_dropped(self):
        """**已知召回缺口**（precision-first 的代价）：整句没有第一人称 → 不记。

        "今早起来，窗外的海棠开了" 对用户来说明显是自述，但字面没有"我"。
        要放宽这条，必须先想清楚第三人称叙事（"他昨天去了城南"）的误记风险——
        所以这里把现状钉住：**改它必须是显式决定，不能悄悄松**。
        """
        self.assertEqual(derive_candidates("今早起来，窗外的海棠开了"), [])

    def test_future_plan_is_not_recorded(self):
        """**已知召回缺口**：未来计划/意愿不记。

        "我要去扬州了，三月动身" 含第一人称 + 时间，但不属于四类规则中的任何一类
        （事件/偏好/关系变化/显式指令）。加"计划"类属于扩规则，得单独拍板。
        """
        self.assertEqual(derive_candidates("我要去扬州了，三月动身"), [])


class TestSemanticRule(unittest.TestCase):
    """R-SEM：第一人称 + 稳定语气 cue → SEMANTIC。"""

    def test_preference_cue_yields_semantic(self):
        cands = derive_candidates("我一向不爱那些应酬话")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.SEMANTIC.value)

    def test_superlative_cue_yields_semantic(self):
        cands = derive_candidates("我最喜欢雨天里读旧书")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.SEMANTIC.value)

    def test_never_cue_yields_semantic(self):
        cands = derive_candidates("我从不与人争这些虚礼")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.SEMANTIC.value)


class TestRelationRule(unittest.TestCase):
    """R-REL：态度变化 cue + 参与者指称 → RELATION_DELTA。"""

    def test_attitude_change_with_second_person(self):
        cands = derive_candidates("我现在觉得你比从前可亲", participants=PARTICIPANTS)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.RELATION_DELTA.value)

    def test_attitude_change_with_participant_name(self):
        cands = derive_candidates("我现在觉得宝玉比从前可亲", participants=PARTICIPANTS)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.RELATION_DELTA.value)

    def test_attitude_change_without_any_participant_is_dropped(self):
        """"我不再喜欢这枝花了"——不涉及会话参与者，不是关系变化。"""
        self.assertEqual(derive_candidates("我不再喜欢这枝花了"), [])


class TestExplicitRule(unittest.TestCase):
    """R-EXPL：显式指令 → SEMANTIC，且显著性最高。"""

    def test_remember_cue_yields_high_salience(self):
        cands = derive_candidates("记住我三月要去扬州")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.SEMANTIC.value)
        self.assertGreaterEqual(cands[0]["salience"], 0.7)
        self.assertIn("R-EXPL", cands[0]["confidence_means"])

    def test_do_not_forget_cue(self):
        cands = derive_candidates("别忘了替我收着那方旧砚")
        self.assertEqual(len(cands), 1)
        self.assertIn("R-EXPL", cands[0]["confidence_means"])

    def test_explicit_beats_episodic(self):
        """同一句同时命中 R-EXPL 和 R-EPI 时，按优先级取 R-EXPL。"""
        cands = derive_candidates("记住昨天我去了城南看桃花")
        self.assertEqual(len(cands), 1)
        self.assertIn("R-EXPL", cands[0]["confidence_means"])


class TestSelfNameRule(unittest.TestCase):
    """R-EXPL 的「叫我X」族（P0 第二阶段「渐进式熟人社会」）。

    自述称呼要能进长期记忆，跨会话的熟识称呼才有着落；但"他们叫我别去"
    这类第三者转述绝不能记——所以 cue 命中后还要过 `_self_name_ok` 形状校验。
    """

    # ---- 正向：第一人称自述称呼 → 写入一条 SEMANTIC ----

    def test_you_can_call_me_yields_semantic(self):
        cands = derive_candidates("你可以叫我小明，这样亲近些")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["kind"], MemoryKind.SEMANTIC.value)
        self.assertIn("R-EXPL", cands[0]["confidence_means"])
        # verbatim：内容是原话切片
        self.assertIn(cands[0]["content"], "你可以叫我小明，这样亲近些")

    def test_call_me_variant_cheng_wo(self):
        cands = derive_candidates("以后称我小昭便可，不必客气")
        self.assertEqual(len(cands), 1)
        self.assertIn("R-EXPL", cands[0]["confidence_means"])

    def test_call_me_variant_han_wo(self):
        cands = derive_candidates("以后就喊我灯哥好了")
        self.assertEqual(len(cands), 1)

    def test_ascii_name_is_accepted(self):
        cands = derive_candidates("叫我Tony就行")
        self.assertEqual(len(cands), 1)

    def test_two_char_name_is_accepted(self):
        cands = derive_candidates("你可以叫我阿雾，别这么见外")
        self.assertEqual(len(cands), 1)

    # ---- 反向：第三者转述 / 祈使短语 → 不写 ----

    def test_third_person_relaying_is_rejected(self):
        """"他们叫我别去"是转述他人指令，不是自我介绍。"""
        self.assertEqual(derive_candidates("他们叫我别去"), [])

    def test_other_people_relaying_is_rejected(self):
        self.assertEqual(derive_candidates("别人叫我做事，我从来不听"), [])

    def test_imperative_tail_is_rejected(self):
        """「叫我」后跟祈使短语（别去/快走）不是称呼。"""
        self.assertEqual(derive_candidates("你以后叫我别再提这事"), [])
        self.assertEqual(derive_candidates("船家叫我快走，莫要回头"), [])

    def test_question_form_is_vetoed_by_n1(self):
        """问句优先否决（N1 在 R-EXPL 之前）。"""
        self.assertEqual(derive_candidates("你可以叫我小明？"), [])

    def test_bare_call_me_too_short_is_vetoed_by_n2(self):
        """"叫我小明"整句 4 字 < 6 → N2 短句否决（既有防线，不为其开口子）。"""
        self.assertEqual(derive_candidates("叫我小明"), [])

    # ---- 不回退：传统显式指令照旧放行 ----

    def test_strong_explicit_cue_still_wins_even_with_call_me(self):
        """句中命中"记住"族时按原样放行，形状校验不回退既有行为。"""
        cands = derive_candidates("记住，他们叫我大哥")
        self.assertEqual(len(cands), 1)
        self.assertIn("R-EXPL", cands[0]["confidence_means"])


# ==========================================================================
# 2. 七条否决
# ==========================================================================


class TestVetoQuestion(unittest.TestCase):
    """N1：问句不是事实。"""

    def test_question_mark_vetoes(self):
        r = derive_report("你还记得昨天我说的话吗？")
        self.assertEqual(r.candidates, ())
        self.assertIn("N1_QUESTION", r.rejected_codes)

    def test_interrogative_head_without_mark_vetoes(self):
        r = derive_report("为什么我昨天去了城南")
        self.assertEqual(r.candidates, ())
        self.assertIn("N1_QUESTION", r.rejected_codes)

    def test_statement_with_question_inside_keeps_statement(self):
        """整条里只有一句是问句时，陈述句仍可被采纳。"""
        cands = derive_candidates("昨天我去城南看了桃花。你猜我遇见谁了？")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["content"], "昨天我去城南看了桃花")


class TestVetoGreeting(unittest.TestCase):
    """N2：寒暄/应答会灌满记忆表。"""

    def test_hello_vetoes(self):
        r = derive_report("你好")
        self.assertEqual(r.candidates, ())
        self.assertIn("N2_GREETING_OR_TOO_SHORT", r.rejected_codes)

    def test_thanks_vetoes(self):
        self.assertEqual(derive_candidates("谢谢你"), [])

    def test_too_short_vetoes(self):
        self.assertEqual(derive_candidates("嗯嗯"), [])
        self.assertEqual(derive_candidates("好"), [])

    def test_greeting_within_long_sentence_is_not_vetoed_by_n2(self):
        """N2 作用于整条消息；长句里含"好的"不该被 N2 否决（该由是否命中 cue 决定）。"""
        cands = derive_candidates("好的，我一向不爱这些客套话")
        self.assertEqual(len(cands), 1)


class TestVetoBackref(unittest.TestCase):
    """N3：回指模型上轮的话 → 防"它编 → 它记 → 它更确信"。"""

    def test_you_just_said_vetoes(self):
        r = derive_report("你刚才说西厢记很好，我记下了")
        self.assertEqual(r.candidates, ())
        self.assertIn("N3_BACKREF_TO_MODEL", r.rejected_codes)

    def test_you_said_before_vetoes(self):
        r = derive_report("你说过昨天那件事")
        self.assertEqual(r.candidates, ())
        self.assertIn("N3_BACKREF_TO_MODEL", r.rejected_codes)


class TestVetoProfileOverlap(unittest.TestCase):
    """N4：档案已有的不再记一遍（否则同一事实在上下文里出现两次）。"""

    def test_high_overlap_vetoes(self):
        # 档案里已经有这句话的完整表述 → 再记一遍就是同一事实在上下文里出现两次。
        # （阈值是"被档案覆盖的比例 ≥0.6"，所以这里用真正高重叠的文本，
        #   不能拿只重合几个词的句子来测——那测的是阈值松紧，不是机制。）
        profile = ["阿雾：我一向沉默寡言，每天黄昏点亮灯塔。"]
        r = derive_report(
            "我一向沉默寡言，每天黄昏点亮灯塔", profile_texts=profile)
        self.assertEqual(r.candidates, ())
        self.assertIn("N4_PROFILE_OVERLAP", r.rejected_codes)

    def test_low_overlap_keeps_candidate(self):
        profile = ["阿雾：沉默寡言的灯塔看守，每天黄昏点亮灯塔。"]
        cands = derive_candidates(
            "我一向爱吃城南那家的桂花糕", profile_texts=profile)
        self.assertEqual(len(cands), 1)

    def test_empty_profile_texts_never_vetoes(self):
        cands = derive_candidates("我一向爱吃桂花糕", profile_texts=())
        self.assertEqual(len(cands), 1)


class TestVetoSensitive(unittest.TestCase):
    """N5：敏感标识丢弃，且**报告里不落内容**。"""

    def test_phone_vetoes_and_leaks_nothing(self):
        r = derive_report("记住我的电话是13812345678")
        self.assertEqual(r.candidates, ())
        self.assertTrue(any(c.startswith("N5_SENSITIVE") for c in r.rejected_codes))
        # 审计轨迹里绝不能出现号码本身
        dumped = repr([(x.code, x.sentence_index, x.rule) for x in r.rejections])
        self.assertNotIn("13812345678", dumped)

    def test_id_card_vetoes(self):
        r = derive_report("我的身份证是110101199003071234")
        self.assertEqual(r.candidates, ())
        self.assertTrue(any(c.startswith("N5_SENSITIVE") for c in r.rejected_codes))

    def test_email_vetoes(self):
        r = derive_report("我的邮箱是 a.b@example.com 记住它")
        self.assertEqual(r.candidates, ())
        self.assertTrue(any(c.startswith("N5_SENSITIVE") for c in r.rejected_codes))

    def test_bank_card_vetoes(self):
        r = derive_report("记住卡号 6222021234567890123")
        self.assertEqual(r.candidates, ())
        self.assertTrue(any(c.startswith("N5_SENSITIVE") for c in r.rejected_codes))

    def test_sensitive_veto_is_whole_message(self):
        """一条消息里只要有一处敏感标识，整条不记（不做"掐掉半句"的改写）。"""
        r = derive_report("昨天我去城南看了桃花。我的电话是13812345678")
        self.assertEqual(r.candidates, ())


class TestVetoTooLong(unittest.TestCase):
    """N6：整条 > 200 字直接丢；不截断（截断会改语义）。"""

    def test_long_message_vetoes(self):
        msg = "我一向爱读旧书" * 40  # 280 字
        self.assertGreater(len(msg), MAX_MESSAGE_CHARS)
        r = derive_report(msg)
        self.assertEqual(r.candidates, ())
        self.assertIn("N6_TOO_LONG", r.rejected_codes)

    def test_sentence_over_content_limit_is_dropped_not_truncated(self):
        long_sentence = "我一向" + "爱读旧书" * 40  # 单句 > 120 字但整条 < 200？需保证 < 200
        self.assertLessEqual(len(long_sentence), MAX_MESSAGE_CHARS)
        self.assertGreater(len(long_sentence), MAX_CONTENT_CHARS)
        r = derive_report(long_sentence)
        self.assertEqual(r.candidates, ())
        self.assertIn("SENTENCE_TOO_LONG", r.rejected_codes)


class TestVetoMetaTalk(unittest.TestCase):
    """N7：纯元话语没有信息量。"""

    def test_continue_vetoes(self):
        r = derive_report("好的我们开始吧")
        self.assertEqual(r.candidates, ())
        self.assertIn("N7_META_TALK", r.rejected_codes)

    def test_continue_alone_vetoes(self):
        r = derive_report("你继续说下去")
        self.assertEqual(r.candidates, ())
        # 长度够、也不是寒暄，只能由 N7 拦下
        self.assertIn("N7_META_TALK", r.rejected_codes)


# ==========================================================================
# 3. verbatim 不变量（fuzz）
# ==========================================================================


class TestVerbatimInvariant(unittest.TestCase):
    """`content` 必须是 `user_message` 的子串——记忆不许被改写。"""

    CORPUS = (
        "昨天我去城南看了桃花", "我一向不爱那些应酬话", "记住我三月要去扬州",
        "我现在觉得你比从前可亲", "我读了《西厢记》后来梦见莺莺",
        "今天下雨我哪里也没去", "我最讨厌虚情假意的客套",
        "你刚才说扬州很好", "嗯嗯", "我们开始吧",
        "后来我在灯下写了半阙词，心里难过得很", "我从不与人争这些虚礼",
    )

    def test_fuzz_50_messages_content_is_always_substring(self):
        rng = random.Random(20261020)  # 固定种子：确定性 fuzz
        checked = 0
        for _ in range(50):
            parts = [rng.choice(self.CORPUS) for _ in range(rng.randint(1, 3))]
            message = "。".join(parts) + "。"
            for cand in derive_candidates(message):
                self.assertIn(
                    cand["content"], message,
                    msg=f"改写发生了：{cand['content']!r} 不在 {message!r} 里")
                checked += 1
        self.assertGreater(checked, 0, "fuzz 没派生出任何候选，等于没测到不变量")

    def test_content_excludes_sentence_delimiters(self):
        cands = derive_candidates("昨天我去看了桃花。我一向爱吃桂花糕")
        self.assertEqual(len(cands), 2)
        for c in cands:
            self.assertNotIn("。", c["content"])

    def test_leading_and_trailing_space_is_stripped_but_still_substring(self):
        message = "  昨天我去看了桃花  "
        cands = derive_candidates(message)
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0]["content"], "昨天我去看了桃花")
        self.assertIn(cands[0]["content"], message)


# ==========================================================================
# 4. 确定性 + 上限 + 字段契约 + 签名护栏
# ==========================================================================


class TestDeterminism(unittest.TestCase):
    """派生层幂等的前提：同输入必同输出（不吃随机数、不吃当前时间）。"""

    def test_same_input_twice_same_output(self):
        msg = "昨天我去城南看了桃花。我一向不爱应酬"
        first = derive_candidates(msg, participants=PARTICIPANTS)
        second = derive_candidates(msg, participants=PARTICIPANTS)
        self.assertEqual(first, second)

    def test_report_also_deterministic(self):
        msg = "你刚才说扬州。记住我三月去扬州"
        a = derive_report(msg)
        b = derive_report(msg)
        self.assertEqual(a, b)


class TestCaps(unittest.TestCase):
    """三条硬上限。"""

    def test_at_most_two_candidates_per_turn(self):
        msg = ("昨天我去城南看桃花。我一向爱吃桂花糕。"
               "记住我三月要去扬州。我现在觉得你比从前可亲")
        cands = derive_candidates(msg, participants=PARTICIPANTS)
        self.assertLessEqual(len(cands), MAX_CANDIDATES_PER_TURN)
        self.assertEqual(len(cands), MAX_CANDIDATES_PER_TURN)

    def test_third_match_records_cap_rejection(self):
        msg = ("昨天我去城南看桃花。我一向爱吃桂花糕。"
               "记住我三月要去扬州")
        r = derive_report(msg)
        self.assertIn("MAX_PER_TURN_CAP", r.rejected_codes)

    def test_duplicate_sentence_in_same_turn_recorded_once(self):
        msg = "昨天我去看了桃花。昨天我去看了桃花"
        cands = derive_candidates(msg)
        self.assertEqual(len(cands), 1)
        r = derive_report(msg)
        self.assertIn("DUPLICATE_IN_TURN", r.rejected_codes)


class TestFieldContract(unittest.TestCase):
    """候选字段必须正好是 `commit_turn` 需要的那几个。"""

    def test_fields_are_exactly_the_five(self):
        cand = derive_candidates("昨天我去看了桃花")[0]
        self.assertEqual(
            set(cand), {"kind", "content", "confidence", "confidence_means", "salience"})

    def test_does_not_set_scope(self):
        """scope 由 `commit_turn` 补 `SESSION`；引擎自己填就等于替调用方做了 INV-1 决定。"""
        cand = derive_candidates("昨天我去看了桃花")[0]
        self.assertNotIn("scope", cand)
        self.assertNotIn("scope_id", cand)
        self.assertNotIn("source_turn_id", cand)

    def test_confidence_and_salience_in_range(self):
        for msg in ("昨天我去看了桃花", "记住我三月去扬州", "我一向爱吃桂花糕",
                    "我现在觉得你比从前可亲"):
            cand = derive_candidates(msg, participants=PARTICIPANTS)[0]
            self.assertEqual(cand["confidence"], RULE_CONFIDENCE)
            self.assertGreaterEqual(cand["confidence"], 0.0)
            self.assertLessEqual(cand["confidence"], 1.0)
            self.assertGreaterEqual(cand["salience"], 0.0)
            self.assertLessEqual(cand["salience"], 1.0)

    def test_confidence_means_is_never_empty(self):
        cand = derive_candidates("昨天我去看了桃花")[0]
        self.assertTrue(str(cand["confidence_means"]).strip())

    def test_kind_is_always_a_known_memory_kind(self):
        messages = ("昨天我去看了桃花", "记住我三月去扬州", "我一向爱吃桂花糕",
                    "我现在觉得你比从前可亲")
        allowed = {k.value for k in MemoryKind}
        for msg in messages:
            for cand in derive_candidates(msg, participants=PARTICIPANTS):
                self.assertIn(cand["kind"], allowed)


class TestSignatureGuard(unittest.TestCase):
    """**回声室防线**：签名里不许出现任何"模型回答"类参数。"""

    FORBIDDEN = ("answer", "response", "reply", "completion", "model_output", "draft")

    def _assert_no_model_text_param(self, fn):
        params = list(inspect.signature(fn).parameters)
        for name in params:
            for bad in self.FORBIDDEN:
                self.assertNotIn(
                    bad, name.lower(),
                    msg=f"{fn.__name__} 出现了疑似模型输出的参数 {name!r}："
                        f"把模型的话写进记忆会形成回声室")

    def test_derive_candidates_signature(self):
        self._assert_no_model_text_param(derive_candidates)

    def test_derive_report_signature(self):
        self._assert_no_model_text_param(derive_report)

    def test_module_is_pure_no_relative_imports(self):
        """纯函数模块：不 import 存储 / Provider / 其它 persona 子模块。"""
        import persona.modules.memory_writeback as mod
        source = inspect.getsource(mod)
        self.assertNotIn("from ..", source)
        self.assertNotIn("import ..", source)
        self.assertNotIn("storage", source.replace("存储", ""))

    def test_rule_version_exposed(self):
        self.assertTrue(RULE_VERSION)
        self.assertIn(RULE_VERSION.split("-")[0], RULE_VERSION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
