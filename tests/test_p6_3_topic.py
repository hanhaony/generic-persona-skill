"""P6.3：`--topic` 跨会话检索入口的门禁与接线测试（离线，零真实外呼）。

为什么单独立一个文件：P6.2-d 真跑证明 R2（跨会话检索）**代码在、产品路径
永不触发**——`persona chat` 没有 topic 入口，`SessionDraft.topic` 恒 None。
缺的是入口不是能力。入口一旦加上，"宽 topic 污染上下文"就从理论变成现实：
实测（`docs/P6_3_TOPIC_PLAN.md` §4.4.1）真实库里 2 条记忆，`topic="我"`
一个字把**两条全捞进来**。所以这个文件一半测触发、一半测**不污染**。

门禁的形状（DECISIONS §2.33）：**一个纯函数 `topic_is_retrievable()`、
两处用法**——`start_session` 用它 Err（用户显式给的坏值不静默丢弃），
`_retrieve_memories` 用它跳过 R2（历史脏行不让整轮失败）。
防线不能只放 CLI：只放 CLI 就是本项目栽过四次的 D 家族形状。

覆盖：
    门禁（纯函数 + start_session）  7 条
    检索触发 / 不触发              3 条（含"无 topic 只查一次"的强断言）
    取错不污染                     2 条
    CLI                            3 条
    端到端（脚本化 Provider）      1 条
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from persona.core.context import new_call_context
from persona.core.errors import ErrorCode
from persona.core.result import Ok, is_err, unwrap
from persona.core.types import MAX_QUERY_LIMIT, Query
from persona.modules.dialogue import (
    TOPIC_MAX_LEN,
    TOPIC_STOPWORDS,
    _STOPWORDS_BY_LEN,
    SessionDraft,
    topic_is_retrievable,
)
from tests.test_dialogue_memory_integration import (
    RELATED_CONTENT,
    TOPIC,
    UNRELATED_CONTENT,
    CapturingProvider,
    IntegrationFixture,
)
#: CLI 侧的项目 ID 必须和 P51Fixture 写库用的是同一个（`prj_p51`），
#: 否则"人物不在库里"——`tests.test_dialogue_memory_integration.PROJECT` 是另一个库。
from tests.test_p5_1_readmodels import PROJECT, P51Fixture
from tests.test_p6_2_chat_cli import (
    MEMORABLE,
    ScriptedProvider,
    _ChatEnv,
    _run,
)

#: 一条**合规**但本库没有的记忆（C′ 的 topic 形态）
OTHER_TOPIC = "青埂峰"


class CountingReader:
    """包一层真实 `MemoryService`：只记录被问了几次、问了什么。

    "无 topic 时不触发 R2" 的最强断言不是"查了没查到"，而是
    **第二次带 `text` 的查询根本没发生**。
    """

    def __init__(self, real) -> None:
        self._real = real
        self.queries: list = []

    def search(self, ctx, q):
        self.queries.append(q)
        return self._real.search(ctx, q)

    def texts(self) -> list:
        return [getattr(q, "text", None) for q in self.queries]


class TestTopicGate(unittest.TestCase):
    """门禁本身：`Ok(None)` 无 topic / `Ok(str)` 放行 / `Err` 拦。"""

    def test_none_and_blank_topic_are_not_errors(self):
        """没给 topic = 不想跨会话取，**不是错误**（与 P6.2 口径一致）。"""
        for value in (None, "", "   ", "　"):
            with self.subTest(value=value):
                result = topic_is_retrievable(value)
                self.assertFalse(is_err(result))
                self.assertIsNone(result.value)

    def test_blank_topic_does_not_carry_stopword_error(self):
        """空白走的是"无 topic"分支，不会撞上停用词 Err。"""
        self.assertIsNone(topic_is_retrievable(" ").value)

    def test_pronoun_and_particle_topics_are_rejected(self):
        """代词 / 助词 / 疑问词：剥完什么都不剩 → `TOO_BROAD`。"""
        for word in ("我", "你", "的", "了", "这个", "那个", "什么", "怎么", "我们"):
            with self.subTest(word=word):
                result = topic_is_retrievable(word)
                self.assertTrue(is_err(result), f"{word} 竟然放行了")
                self.assertEqual(result.error.code,
                                 ErrorCode.VALIDATION_TOPIC_TOO_BROAD)

    def test_one_char_topic_is_rejected_as_too_short(self):
        """1 字 topic 在子串匹配下几乎必污染（实测 `我` 命中全部记忆）。"""
        result = topic_is_retrievable("书")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_TOPIC_TOO_SHORT)

    def test_overlong_topic_is_rejected(self):
        result = topic_is_retrievable("字" * (TOPIC_MAX_LEN + 1))
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_TOPIC_TOO_LONG)

    def test_stopwords_stripped_longest_first(self):
        """**匹配语义比词表更重要**：按长度降序剥，"我们"必须先于"我"。

        `我们的` 剥对了是空（拒）；若顺序写反（先剥"我"），会剩下一个"们"
        而被放行——门禁就漏了。这条同时钉死顺序与结果。
        """
        self.assertEqual(
            _STOPWORDS_BY_LEN, tuple(sorted(TOPIC_STOPWORDS, key=len, reverse=True)))
        self.assertLess(_STOPWORDS_BY_LEN.index("我们"), _STOPWORDS_BY_LEN.index("我"))
        result = topic_is_retrievable("我们的")
        self.assertTrue(is_err(result),
                        "按长度降序剥的话「我们的」应该什么都不剩")
        # 但"我们的西厢记"要放行：它很具体，误杀它就是门禁太蠢
        self.assertEqual(topic_is_retrievable("我们的西厢记").value, "我们的西厢记")

    def test_short_but_specific_topic_passes(self):
        """`西厢`（2 字、非完整词）：**放行**。拦了才是错——子串匹配下它命中
        的就是西厢记相关记忆，不污染。门禁不管词完不完整。"""
        for word in ("西厢", "西厢记", "青埂峰", "崔莺莺", "西厢记 崔莺莺"):
            with self.subTest(word=word):
                self.assertEqual(topic_is_retrievable(word).value, word)

    def test_misspelled_topic_passes_gate(self):
        """拼错**放行**：拼错属于"取不到"（检索 0 条），不是"取错"。
        系统不猜用户意图（不纠正、不模糊匹配），空结果不注入任何东西。"""
        self.assertEqual(topic_is_retrievable("西厢记记").value, "西厢记记")

    def test_gate_message_names_the_topic_and_the_reason(self):
        """错误信息要能照着改：说清是哪个 topic、为什么不行（不写"报错"两个字）。"""
        message = topic_is_retrievable("我").error.message
        self.assertIn('topic "我"', message)
        self.assertIn("太宽泛", message)
        self.assertIn("西厢记", message)  # 给一个能用的例子


class TestTopicGateInStartSession(unittest.TestCase):
    """`start_session` 是**写路径**：用户显式给的坏 topic 必须 Err，不静默丢弃。"""

    def setUp(self) -> None:
        self.fx = IntegrationFixture(provider=CapturingProvider())

    def tearDown(self) -> None:
        self.fx.close()

    def _start(self, topic):
        return self.fx.service().start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=list(self.fx.two_actors()), topic=topic,
            memory_write_policy="none"))

    def test_bad_topic_is_rejected_and_no_session_row_is_written(self):
        before = self.fx.count("ConversationSession")
        result = self._start("我")
        self.assertTrue(is_err(result))
        self.assertEqual(result.error.code, ErrorCode.VALIDATION_TOPIC_TOO_BROAD)
        # 静默丢弃 = "我按你说的做了"，其实没有。宁可报错，也不留一个 topic
        # 被悄悄改掉的会话。
        self.assertEqual(self.fx.count("ConversationSession"), before)

    def test_blank_topic_starts_a_session_without_topic(self):
        view = unwrap(self._start("   "))
        row = unwrap(self.fx.storage.get(self.fx.ctx, "ConversationSession", view.id))
        self.assertIsNone(row["topic"])

    def test_good_topic_is_stored_verbatim(self):
        view = unwrap(self._start(TOPIC))
        row = unwrap(self.fx.storage.get(self.fx.ctx, "ConversationSession", view.id))
        self.assertEqual(row["topic"], TOPIC)


class TestRetrievalTrigger(unittest.TestCase):
    """读路径：有 topic 才查 R2；无 topic / 脏 topic 都只查 R1。"""

    def setUp(self) -> None:
        self.provider = CapturingProvider()
        self.fx = IntegrationFixture(provider=self.provider)

    def tearDown(self) -> None:
        self.fx.close()

    def _service_with_counting_reader(self):
        from persona.modules.memory import MemoryService
        real = MemoryService(self.fx.storage)
        reader = CountingReader(real)
        return self.fx.service(memory_reader=reader), reader

    def test_no_topic_queries_only_r1(self):
        """**最强的一条**：无 topic 时带 `text` 的查询**根本没发生**（不是"查了没查到"）。"""
        a, b = self.fx.two_actors()
        service, reader = self._service_with_counting_reader()
        sid = unwrap(service.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], topic=None))).id
        unwrap(service.draft_turn(self.fx.ctx, sid, a))
        self.assertEqual(len(reader.queries), 1, f"预期只查 R1，实际 {len(reader.queries)} 次")
        self.assertEqual([t for t in reader.texts() if t], [])

    def test_valid_topic_queries_r2_with_the_topic(self):
        a, b = self.fx.two_actors()
        service, reader = self._service_with_counting_reader()
        sid = unwrap(service.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], topic=TOPIC))).id
        unwrap(service.draft_turn(self.fx.ctx, sid, a))
        self.assertEqual(len(reader.queries), 2)
        self.assertEqual(reader.texts()[1], TOPIC)

    def test_dirty_historical_topic_skips_r2_instead_of_failing(self):
        """历史脏 topic（绕过 `start_session` 直接写进库的行）→ **跳过 R2**，
        不让整轮失败。读路径不为一条脏行挂掉。"""
        a, b = self.fx.two_actors()
        service, reader = self._service_with_counting_reader()
        sid = unwrap(service.start_session(self.fx.ctx, SessionDraft(
            participant_entity_ids=[a, b], topic=None))).id
        dirty_row = {"id": sid, "participant_entity_ids": [a, b], "topic": "我"}
        result = unwrap(service._retrieve_memories(self.fx.ctx, dirty_row))
        self.assertEqual(result, [])
        self.assertEqual(len(reader.queries), 1, "脏 topic 竟然触发了 R2")
        self.assertEqual([t for t in reader.texts() if t], [])


class TestNoPollution(unittest.TestCase):
    """取到该取的，**取不到无关的**——污染比取不到更糟。"""

    def setUp(self) -> None:
        self.provider = CapturingProvider()
        self.fx = IntegrationFixture(provider=self.provider)

    def tearDown(self) -> None:
        self.fx.close()

    def _write(self, sid: str, content: str) -> str:
        turn = unwrap(self.fx.service().run_round(
            self.fx.ctx, sid, memory_candidates=[self.fx.candidate(content)]))
        return turn.generated_memory_ids[0]

    def test_topic_retrieves_the_matching_memory(self):
        a, b = self.fx.two_actors()
        s1 = self.fx.start([a, b])
        mid = self._write(s1, RELATED_CONTENT)

        s2 = self.fx.start([a, b], topic=TOPIC)
        draft = unwrap(self.fx.service().draft_turn(self.fx.ctx, s2, a))
        self.assertIn(mid, draft.referenced_memory_ids)
        self.assertIn(RELATED_CONTENT, self.fx.prompts())

    def test_topic_does_not_pull_unrelated_memory(self):
        """topic 只捞命中它的：无关记忆**不进** prompt（不是"捞回来再筛掉"）。"""
        a, b = self.fx.two_actors()
        s1 = self.fx.start([a, b])
        self._write(s1, RELATED_CONTENT)
        self._write(s1, UNRELATED_CONTENT)

        s2 = self.fx.start([a, b], topic=TOPIC)
        unwrap(self.fx.service().draft_turn(self.fx.ctx, s2, a))
        prompt = self.fx.prompts()
        self.assertIn(RELATED_CONTENT, prompt)
        self.assertNotIn(UNRELATED_CONTENT, prompt,
                         "无关记忆被 topic 检索带进了上下文")

    def test_unmatched_topic_injects_nothing(self):
        """合规 topic 但库里没有匹配 → 空结果，**不报错**（取不到 ≠ 出错）。"""
        a, b = self.fx.two_actors()
        s1 = self.fx.start([a, b])
        self._write(s1, RELATED_CONTENT)

        s2 = self.fx.start([a, b], topic=OTHER_TOPIC)
        draft = unwrap(self.fx.service().draft_turn(self.fx.ctx, s2, a))
        self.assertEqual(draft.referenced_memory_ids, [])


# ==========================================================================
# CLI：坏 topic 必须在花钱之前被拦住；好 topic 必须真的进会话
# ==========================================================================


class _TopicCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gps-p63-topic-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.db = self.root / "persona.db"

        fx = P51Fixture(str(self.root))
        self.hero = fx.entity("阿雾", works=("雾港与星灯",), adaptation="2020 动画改编版")
        self.other = fx.entity("阿灯", works=("雾港与星灯",), adaptation="2020 动画改编版")
        src = fx.source()
        ref = fx.evidence_of(src, "阿雾沉默寡言", subjects=[self.hero])
        fx.profile(self.hero, background=fx.section("阿雾：沉默寡言的灯塔看守。", [ref]))
        ref2 = fx.evidence_of(src, "阿灯活泼", subjects=[self.other])
        fx.profile(self.other, background=fx.section("阿灯：活泼的渔家少女。", [ref2]))
        self.fx = fx
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")

        env = _ChatEnv(self.root)
        env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)

        self.provider = ScriptedProvider()
        patch = mock.patch(
            "persona.orchestrator.assembly.chat_provider_from_settings",
            lambda settings, **kw: Ok(self.provider))
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self) -> None:
        self.fx.storage.close()

    def _argv(self, *extra: str) -> list[str]:
        return ["chat", "--names", "阿雾,阿灯", "--db", str(self.db),
                "--project", PROJECT, "--no-dotenv", *extra]

    def session_rows(self) -> list[dict]:
        q = Query(filters={"project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(self.fx.storage.query(
            self.ctx, "ConversationSession", q).value.items)

    def turn_rows(self) -> list[dict]:
        q = Query(filters={"project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(self.fx.storage.query(
            self.ctx, "ConversationTurn", q).value.items)

    def referenced(self) -> list[str]:
        """本次会话**真的注入了**哪些记忆（看轮次行，不看屏幕输出）。"""
        out: list[str] = []
        for row in self.turn_rows():
            out.extend(row.get("referenced_memory_ids") or [])
        return out


class TestTopicCli(_TopicCliCase):

    def test_bad_topic_is_refused_before_any_call(self):
        """**最重要的一条**：坏 topic 一步钱都不动（0 次模型调用）。"""
        code, out = _run(self._argv("--rounds", "1", "--message", MEMORABLE,
                                    "--topic", "我", "--yes"))
        self.assertEqual(code, 1)
        self.assertIn("太宽泛", out)
        self.assertIn("没有发起任何请求", out)
        self.assertEqual(self.provider.chat_calls, 0, "坏 topic 竟然发起了调用")
        self.assertEqual(self.session_rows(), [])

    def test_too_short_topic_is_refused_before_any_call(self):
        code, out = _run(self._argv("--rounds", "1", "--message", MEMORABLE,
                                    "--topic", "书", "--yes"))
        self.assertEqual(code, 1)
        self.assertIn("太短", out)
        self.assertEqual(self.provider.chat_calls, 0)

    def test_topic_is_really_passed_into_the_session(self):
        """D 家族正面防线：不只看参数被解析了，要看**会话行里真的有**。"""
        code, out = _run(self._argv("--rounds", "1", "--message", MEMORABLE,
                                    "--topic", TOPIC, "--yes"))
        self.assertEqual(code, 0)
        rows = self.session_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["topic"], TOPIC, "topic 没被装配进 SessionDraft")
        self.assertIn(TOPIC, out)  # 授权清单里也要看得见


# ==========================================================================
# 端到端：A′ 写 → B′ 带 topic 读到 → C′ 带别的 topic 读不到
# （真跑的 A′→B′→C′ 就是这三步；这里用脚本化 Provider 离线预演一遍）
# ==========================================================================


class TestTopicEndToEnd(_TopicCliCase):
    """输入与真跑清单写死的那三句完全一致，便于真跑时逐条对照。"""

    #: 与 P6.2-d / P6.3 真跑同一句（规则引擎 R-EPI 命中，会写入）
    EPI = "我在读《西厢记》，觉得崔莺莺这个人挺有意思"
    #: B′/C′ 的提问（照抄 P6.2-d 会话 B 那句失败基线）
    ASK = "你还记得我说过在读什么书吗？"

    def _write_session(self) -> list[str]:
        code, out = _run(self._argv("--rounds", "1", "--message", self.EPI,
                                    "--memory-write", "rule", "--topic", "西厢记",
                                    "--yes"))
        self.assertEqual(code, 0, out)
        self.assertIn("写入记忆 1 条", out)
        return [r["id"] for r in self._rows("MemoryItem")]

    def _rows(self, kind: str) -> list[dict]:
        q = Query(filters={"project_id": PROJECT}, limit=MAX_QUERY_LIMIT)
        return list(self.fx.storage.query(
            self.ctx, kind, q, include_deleted=True).value.items)

    def test_session_b_with_matching_topic_reads_what_a_wrote(self):
        written = self._write_session()
        self.assertEqual(len(written), 1)

        code, out = _run(self._argv("--rounds", "1", "--message", self.ASK,
                                    "--topic", "西厢记", "--yes"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.referenced(), written,
                         "A′ 写下的记忆没被 B′ 跨会话读到——R2 没在产品路径生效")

    def test_session_c_with_other_topic_reads_nothing(self):
        self._write_session()

        code, out = _run(self._argv("--rounds", "1", "--message", self.ASK,
                                    "--topic", "青埂峰", "--yes"))
        self.assertEqual(code, 0, out)
        self.assertEqual(self.referenced(), [],
                         "topic 不匹配却注入了记忆——污染")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
