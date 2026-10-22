"""P6.2-b：`rule` 策略**接线**测试（真实 SQLite + 真 `run_round`，零外呼）。

**为什么单独一个文件**：P6.2-a 的集成测试是**手动**把候选喂给 `commit_turn`
（`run_round(..., memory_candidates=derive_candidates(...))`）。P6.2-b 新增的是
**产品路径自己派生**——这恰恰是本项目栽过四次的形状：
能力只存在于"测试显式注入"这条路径，产品路径从不注入 → 单测全绿、真跑不生效
（D-A / D-B / D-D / D-E）。所以这里的每条断言都针对"**接线在不在**"：

1. `rule` 会话下，调用方**不给候选**，`run_round` 也要真的写进库；
2. `none` / `explicit` 会话下**绝不**自动派生（尤其 `none`：派生会让每一轮都被
   闸门判 `gate_rejected`，默认路径会从"零写入"变成"每轮报错"）；
3. **反向测试**：把引擎换成一个哨兵实现，看库里有没有那条哨兵记忆——
   如果 `run_round` 哪天不再调用引擎/不再把结果交给 `commit_turn`，它就红；
4. **反向测试**：`profile_texts` / `participants` 真的被装配了——
   用"与档案高度重叠 ⇒ N4 否决"做功能性证明，而不是只看 kwarg 里有没有值；
5. 审计只记代码与计数，**不记内容**。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

import persona.modules.dialogue as dialogue_mod
from persona.core.enums import MemoryKind
from persona.core.result import Ok, is_err, unwrap
from persona.modules.dialogue import (
    POLICY_MEMORY_WRITE_EXPLICIT,
    POLICY_MEMORY_WRITE_NONE,
    POLICY_MEMORY_WRITE_RULE,
    DialogueService,
    SessionDraft,
)
from persona.modules.memory_writeback import DeriveReport, derive_candidates
from tests.test_memory_writeback_integration import (
    MEMORABLE,
    WritebackFixture,
)

PROJECT = "prj_memory_writeback"


class _WiringBase(unittest.TestCase):
    def setUp(self) -> None:
        self.fx = WritebackFixture()
        self.a, self.b = self.fx.actors()
        self.service: DialogueService = self.fx.service()

    def tearDown(self) -> None:
        self.fx.close()

    def _round(self, sid: str, message: str, **kw):
        """**不传候选**——产品路径必须自己派生。"""
        return self.service.run_round(self.fx.ctx, sid, user_message=message, **kw)

    def _start(self, policy: str) -> str:
        return unwrap(self.service.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b],
            memory_write_policy=policy))).id

    def audit_rows(self, action: str) -> list[dict]:
        cur = self.fx.storage.connect().execute(
            "SELECT level, action, detail FROM audit_records WHERE action = ?",
            (action,))
        return [{"level": r[0], "action": r[1], "detail": r[2]}
                for r in cur.fetchall()]


# ==========================================================================
# 1. rule 策略：产品路径自己派生、自己写
# ==========================================================================


class TestRulePolicyEndToEnd(_WiringBase):

    def test_rule_policy_writes_without_caller_candidates(self):
        """核心接线断言：调用方一个候选都不给，库里必须有记忆。"""
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        turn = unwrap(self._round(sid, MEMORABLE))

        rows = self.fx.memory_rows()
        self.assertEqual(len(rows), 1, "rule 会话没写出记忆——产品路径没接线")
        self.assertEqual(list(turn.generated_memory_ids), [rows[0]["id"]])
        self.assertIn(rows[0]["content"], MEMORABLE)          # verbatim 切片
        self.assertEqual(self.fx.memory_kind(rows[0]),
                         MemoryKind.EPISODIC.value)

    def test_rule_policy_chitchat_writes_nothing(self):
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        for msg in ("你好呀", "谢谢你了", "嗯嗯，知道了"):
            unwrap(self._round(sid, msg))
        self.assertEqual(self.fx.memory_rows(), [], "寒暄被写进记忆了")

    def test_rule_policy_question_writes_nothing(self):
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        unwrap(self._round(sid, "你昨天去城南看桃花了吗？"))
        self.assertEqual(self.fx.memory_rows(), [])

    def test_rule_policy_audit_has_counts_but_no_content(self):
        """审计要能回答"这轮派生了几条、为什么没记"，但不能落内容。"""
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        unwrap(self._round(sid, MEMORABLE))
        rows = self.audit_rows("dialogue.memory_derive")
        self.assertEqual(len(rows), 1, "派生没有留审计")
        detail = str(rows[0]["detail"])
        self.assertIn("candidates=1", detail)
        self.assertIn("policy=rule", detail)
        self.assertNotIn("桃花", detail, "审计里落了内容原文")


# ==========================================================================
# 2. 反向护栏：别的策略绝不自动派生
# ==========================================================================


class TestOtherPoliciesDoNotDerive(_WiringBase):

    def test_none_policy_round_succeeds_and_writes_nothing(self):
        """`none` 下**必须**既不写、也不报错。

        这是本实现最容易搞错的一处：如果 `run_round` 不看策略就派生，
        `commit_turn` 的闸门会把每一轮判成 `gate_rejected`——默认路径
        从"零写入"变成"每轮报错"，而且**只有真跑才会发现**。
        """
        sid = self._start(POLICY_MEMORY_WRITE_NONE)
        turn = unwrap(self._round(sid, MEMORABLE))          # 不许 Err
        self.assertEqual(turn.generated_memory_ids, [])
        self.assertEqual(self.fx.memory_rows(), [])
        self.assertEqual(self.audit_rows("dialogue.memory_derive"), [],
                         "none 策略下不该派生（连审计都不该有）")

    def test_explicit_policy_does_not_auto_derive(self):
        """`explicit` 的语义是"调用方自己给候选"，引擎不越俎。"""
        sid = self._start(POLICY_MEMORY_WRITE_EXPLICIT)
        unwrap(self._round(sid, MEMORABLE))
        self.assertEqual(self.fx.memory_rows(), [])

    def test_run_round_without_user_message_derives_nothing(self):
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        unwrap(self.service.run_round(self.fx.ctx, sid))     # 无用户消息
        self.assertEqual(self.fx.memory_rows(), [])


# ==========================================================================
# 3. 反向测试：接线断了就必须红
# ==========================================================================


class TestWiringIsReal(_WiringBase):

    def test_engine_output_is_actually_passed_to_commit_turn(self):
        """把引擎换成哨兵：库里出现哨兵内容 = `run_round` 真调了引擎、
        也真把结果交给了 `commit_turn`。删掉任何一环，这条会红。"""
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        sentinel = {
            "kind": MemoryKind.SEMANTIC.value,
            "content": "哨兵记忆内容",
            "confidence": 0.4,
            "confidence_means": "sentinel",
            "salience": 0.5,
        }
        seen: list[str] = []

        def fake_engine(message, *, profile_texts=(), participants=()):
            seen.append(str(message))
            return DeriveReport((dict(sentinel),), ())

        with mock.patch.object(dialogue_mod, "derive_report", fake_engine):
            unwrap(self._round(sid, "随便说点什么都可以"))

        self.assertEqual(seen, ["随便说点什么都可以"], "run_round 没调用规则引擎")
        contents = [r["content"] for r in self.fx.memory_rows()]
        self.assertEqual(contents, ["哨兵记忆内容"],
                         "引擎产出没被交给 commit_turn")

    def test_engine_receives_profile_texts_and_participant_names(self):
        """两个只读输入必须真的装配：档案文本（N4 用）+ 参与者姓名（R-REL 用）。"""
        sid = self._start(POLICY_MEMORY_WRITE_RULE)
        captured: dict = {}

        def spy(message, *, profile_texts=(), participants=()):
            captured["profile_texts"] = tuple(profile_texts)
            captured["participants"] = tuple(participants)
            return DeriveReport((), ())

        with mock.patch.object(dialogue_mod, "derive_report", spy):
            unwrap(self._round(sid, MEMORABLE))

        self.assertTrue(captured["profile_texts"], "档案文本没装配进规则引擎")
        self.assertTrue(
            any(t for t in captured["profile_texts"] if "灯塔" in t),
            f"档案文本里没有真实档案内容：{captured['profile_texts']}")
        names = dict(captured["participants"])
        self.assertEqual(names.get(self.a), "阿雾", "参与者姓名没装配")
        self.assertEqual(names.get(self.b), "阿灯", "参与者姓名没装配")

    def test_profile_overlap_veto_proves_inputs_reach_the_engine(self):
        """功能性证明（不是看 kwarg 有没有值）：档案里已有的内容不许再记一遍。

        对照很关键——同一句话**不给档案文本**时引擎会产出 1 条；给了就该 0 条。
        若接线漏传 `profile_texts`，库里就会多出 1 条，这条测试红。
        """
        msg = "我一向每日黄昏点亮灯塔"
        self.assertEqual(len(derive_candidates(msg)), 1,
                         "对照前提不成立：这句话本身该被派生")

        fx = _OverlapFixture()
        try:
            a, b = fx.actors()
            svc = fx.service()
            sid = unwrap(svc.start_session(fx.ctx, SessionDraft(
                participant_entity_ids=[a, b],
                memory_write_policy=POLICY_MEMORY_WRITE_RULE))).id
            unwrap(svc.run_round(fx.ctx, sid, user_message=msg))
            self.assertEqual(
                fx.memory_rows(), [],
                "档案里已有的内容又被记了一遍——profile_texts 没传到引擎")
        finally:
            fx.close()


class _OverlapFixture(WritebackFixture):
    """档案里已写明"每日黄昏点亮灯塔"，用来验证 N4 的输入真的被装配。"""

    def actors(self):                      # noqa: D102 - 覆盖父类夹具
        a = self.entity("阿雾")
        b = self.entity("阿灯")
        self.profile(a, "她一向每日黄昏点亮灯塔，从不出错。")
        self.profile(b, "阿灯：活泼的渔家少女。")
        return a, b


# ==========================================================================
# 4. 策略集合本身
# ==========================================================================


class TestPolicySet(unittest.TestCase):

    def test_rule_is_accepted_and_read_back(self):
        fx = WritebackFixture()
        try:
            a, b = fx.actors()
            view = unwrap(fx.service().start_session(fx.ctx, SessionDraft(
                participant_entity_ids=[a, b],
                memory_write_policy=POLICY_MEMORY_WRITE_RULE)))
            self.assertEqual(view.memory_write_policy,
                             POLICY_MEMORY_WRITE_RULE)
        finally:
            fx.close()

    def test_default_policy_still_none(self):
        """D-179 不许松动：不显式选，默认就是 none。"""
        self.assertEqual(SessionDraft(participant_entity_ids=["x", "y"])
                         .memory_write_policy, POLICY_MEMORY_WRITE_NONE)

    def test_unknown_policy_still_rejected(self):
        fx = WritebackFixture()
        try:
            a, b = fx.actors()
            res = fx.service().start_session(fx.ctx, SessionDraft(
                participant_entity_ids=[a, b],
                memory_write_policy="auto"))
            self.assertTrue(is_err(res), "未知策略居然被接受")
            self.assertIn("未知记忆写入策略", res.error.message)
        finally:
            fx.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
