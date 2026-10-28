"""P6.0 Step 1：对话上下文注入 + 证据级引用 + 拒答格式（N1 / N2 / N4）。

**这些断言验证的是"改动真的生效"，不只是"没弄坏"。** 覆盖：

1. **N2 注入补全**：`single_events` / `habits` / `experiences` / `expression_style`
   真的进了 prompt，且 `single_events` 带着"不得说成一贯"的标签——
   此前这四个分区一个字都不进 prompt，模型根本看不到"单次行为"。
2. **N1 证据级引用**：证据 ID 随档案注入；模型回答里的 `[ev:<id>]` 被**解析**进
   `referenced_evidence_ids`；引用了上下文没给的 ID 时打 `EVIDENCE_REF_OUT_OF_CONTEXT`，
   并且**照原样记下来**（不静默丢弃，否则追溯仍是装饰品）。
3. **N4 三档回答**：拒答规则收窄为三档（档案事实缺拒答 / 观点态度可答 /
   对话承接可答，后两档不得借档案背书）；「资料没写：」格式继续钉死。
4. **N3 已放开**：`traits` 全量注入（`TRAITS_INJECT_LIMIT=None`），
   并有前置检查防截断悄悄回来。
5. prompt 版本已 bump（改了 prompt 不 bump 会让新旧调用共用幂等键 → 账本漏账）。

全程离线：临时 SQLite + 自定义 Mock Provider，不触网、不花钱。
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from persona.core.context import new_call_context
from persona.core.enums import (
    EvidenceDimension,
    EvidenceTier,
    MemoryScope,
    ProvenanceNamespace,
    Stability,
)
from persona.core.result import Ok, UsageDelta, unwrap
from persona.core.types import UsageReport
from persona.modules.dialogue import (
    DIALOGUE_PROMPT_VERSION,
    FLAG_EVIDENCE_REF_OUT_OF_CONTEXT,
    NO_EVIDENCE_PREFIX,
    TRAITS_INJECT_LIMIT,
    DialogueDeps,
    DialogueService,
    POLICY_LEAST_RECENTLY_SPOKEN,
    SessionDraft,
)
from persona.modules.evidence import EvidenceDraft, EvidenceService, SourceDraft
from persona.modules.persona import (
    PersonaProfileDraft,
    PersonaService,
    ProfileItem,
    ProfileSection,
)
from persona.modules.relationship import RelationshipService
from persona.providers.base import (
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.storage.sqlite import SqliteStorage

PROJECT = "prj_p60_ctx"


class ScriptedProvider:
    """返回调用方写死的文本，并记录最后一次 messages。确定性、离线。"""

    def __init__(self, text: str = "（模拟）我听见了。") -> None:
        self.text = text
        self.last_messages: list = []

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        return Ok(ChatResponse(
            text=self.text, finish_reason="stop", model_id="scripted",
            usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class CtxTestCase(unittest.TestCase):
    """临时库 + 一个「六分区齐全」的人物档案 + 一个可脚本化的 Provider。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="gps-p60-")
        self.storage = SqliteStorage(Path(self.tmp) / "p60.db")
        self.ctx = new_call_context(project_id=PROJECT, actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.evidence = EvidenceService(self.storage)
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)

        self.a = self._entity("阿雾")
        self.b = self._entity("阿灯")

        src = self._source()
        self.ev_bg = self._evidence(src, "阿雾的背景", self.a)
        self.ev_trait = self._evidence(src, "阿雾的稳定特质", self.a)
        self.ev_habit = self._evidence(src, "阿雾的习惯", self.a)
        self.ev_expr = self._evidence(src, "阿雾的说话风格", self.a)
        self.ev_exp = self._evidence(src, "阿雾的经历", self.a)
        self.ev_single = self._evidence(src, "阿雾的单次行为", self.a)

        self.secret_single = "那日落花时节独自葬花"   # 只应出现在 single_events
        self.secret_habit = "常在灯下独坐到深夜"
        self.secret_expr = "说话总带着半句留白"
        self.secret_exp = "年少时离乡进府"

        unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=self.a,
            background=ProfileSection(text="雾港出身的青年。", confidence=0.8,
                                      evidence_refs=[self.ev_bg]),
            traits=[ProfileItem(item_id="t1", text="沉默寡言", confidence=0.8,
                                evidence_refs=[self.ev_trait],
                                stability=Stability.REPEATED)],
            habits=[ProfileItem(item_id="h1", text=self.secret_habit,
                                confidence=0.6, evidence_refs=[self.ev_habit],
                                stability=Stability.REPEATED)],
            expression_style=ProfileSection(text=self.secret_expr, confidence=0.6,
                                            evidence_refs=[self.ev_expr]),
            experiences=[ProfileItem(item_id="x1", text=self.secret_exp,
                                     confidence=0.7, evidence_refs=[self.ev_exp],
                                     stability=Stability.REPEATED)],
            single_events=[ProfileItem(item_id="s1", text=self.secret_single,
                                       confidence=0.4, evidence_refs=[self.ev_single],
                                       stability=Stability.SINGLE)],
            applicable_variant_ids=[self.a],
            open_questions=["群体互动不适用"])))
        # 第二个角色只要背景，够凑够 2 人即可
        src_b = self._source("《阿灯》")
        ev_b = self._evidence(src_b, "阿灯的背景", self.b)
        unwrap(self.persona.create_profile(self.ctx, PersonaProfileDraft(
            entity_id=self.b,
            background=ProfileSection(text="活泼的渔家少女。", confidence=0.8,
                                      evidence_refs=[ev_b]),
            applicable_variant_ids=[self.b])))

    # ------------------------------------------------------------ 构件

    def _entity(self, name: str) -> str:
        return unwrap(self.storage.put(
            self.ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER",
             "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
             "fictional_character_meta": {"works": [], "adaptation_version": "原著",
                                          "adaptation_lineage": ["原著"],
                                          "official_canon_level": "CANON"}},
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    def _source(self, title: str = "《测试来源》") -> str:
        from persona.core.ids import new_token
        return unwrap(self.evidence.register_source(
            self.ctx, SourceDraft(title=title, canonical_key=f"k_{new_token()}"))).id

    def _evidence(self, source_id: str, claim: str, subject: str) -> str:
        return unwrap(self.evidence.add_evidence(self.ctx, EvidenceDraft(
            claim=claim, tier=EvidenceTier.PROFILE_BACKGROUND,
            dimension=EvidenceDimension.TRAIT, subject_entity_ids=[subject],
            extraction_confidence=0.9, source_id=source_id))).id

    # ------------------------------------------------------------ 驱动

    def _session(self, service: DialogueService) -> str:
        return unwrap(service.start_session(self.ctx, SessionDraft(
            participant_entity_ids=[self.a, self.b],
            topic=None,
            persona_profile_ids=[self.a, self.b],
            speaker_selection_policy=POLICY_LEAST_RECENTLY_SPOKEN))).id

    def _service(self, text: str = "（模拟）我听见了。") -> tuple[DialogueService, ScriptedProvider]:
        provider = ScriptedProvider(text)
        deps = DialogueDeps(
            profiles=self.persona, relations=self.relationship, evidence=self.evidence,
            memory=None, storage=self.storage, provider=provider)
        return DialogueService(deps), provider

    def _system_prompt(self, provider: ScriptedProvider) -> str:
        self.assertTrue(provider.last_messages, "Provider 没有被调用")
        return "\n".join(str(m.content) for m in provider.last_messages)

    def tearDown(self) -> None:
        self.storage.close()
        shutil.rmtree(self.tmp, ignore_errors=True)


# ==========================================================================
# N2：注入补全
# ==========================================================================


class TestContextInjection(CtxTestCase):

    def test_single_events_enter_prompt(self):
        """核心：单次行为必须进 prompt，否则模型看不到、稳定性边界测不出真东西。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你一贯如此吗？"))
        system = self._system_prompt(provider)
        self.assertIn(self.secret_single, system)

    def test_single_events_carry_the_no_generalization_label(self):
        """光注入还不够：必须带着"不得说成一贯"的标签（AC-7 / D-012 / PF-V2）。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你一贯如此吗？"))
        system = self._system_prompt(provider)
        self.assertIn("不得", system)
        self.assertIn("一贯", system)          # 标签里点名了"一贯/总是/向来/从来"
        self.assertIn("单次行为", system)

    def test_habits_expression_experiences_enter_prompt(self):
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "说说你自己。"))
        system = self._system_prompt(provider)
        for secret in (self.secret_habit, self.secret_expr, self.secret_exp):
            self.assertIn(secret, system)

    def test_traits_injected_in_full(self):
        """P6.0 N3：traits **全量**注入，7 条一条都不能少。

        此前写死 `traits[:5]`，第 6 条起的特征根本不进 prompt —— 模型"没看见"
        会被评估器记成"说错"，事实追溯 / 角色一致性直接假失败。
        """
        src = self._source("《补充》")
        ev = self._evidence(src, "更多特质", self.a)
        items = [ProfileItem(item_id=f"t{i}", text=f"特质{i}", confidence=0.5,
                             evidence_refs=[ev], stability=Stability.REPEATED)
                 for i in range(1, 8)]
        unwrap(self.persona.revise(self.ctx, self.a, PersonaProfileDraft(
            entity_id=self.a, traits=items, applicable_variant_ids=[self.a],
            change_reason="测试 traits 全量注入")))
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "说说你自己。"))
        system = self._system_prompt(provider)
        for i in range(1, 8):
            self.assertIn(f"特质{i}", system)

    def test_traits_inject_limit_is_none(self):
        """钉住"全量"这个决定本身：常量改成整数时这条会先红。

        改回整数不是不行，但必须同步做两件事：① 改本文件的
        `test_traits_injected_in_full`；② 确认评估脚本的前置检查会标注
        "此维度结果受截断影响"（否则假失败会悄悄回来）。
        """
        self.assertIsNone(TRAITS_INJECT_LIMIT,
                          "TRAITS_INJECT_LIMIT 被改成了整数：见注释里的两个必做项")


# ==========================================================================
# N1：证据级引用
# ==========================================================================


class TestEvidenceRefs(CtxTestCase):

    def test_evidence_ids_injected_into_prompt(self):
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你的性情如何？"))
        system = self._system_prompt(provider)
        for eid in (self.ev_bg, self.ev_trait, self.ev_habit, self.ev_single):
            self.assertIn(eid, system)

    def test_cited_refs_parsed_from_answer(self):
        text = f"我素来如此[ev:{self.ev_trait}]。"
        service, _ = self._service(text)
        sid = self._session(service)
        turn = unwrap(service.run_round(self.ctx, sid, "你的性情如何？"))
        self.assertEqual(turn.referenced_evidence_ids, [self.ev_trait])

    def test_duplicate_citations_deduped_in_order(self):
        text = f"一[ev:{self.ev_trait}] 二[ev:{self.ev_habit}] 三[ev:{self.ev_trait}]"
        service, _ = self._service(text)
        sid = self._session(service)
        turn = unwrap(service.run_round(self.ctx, sid, "说说你自己。"))
        self.assertEqual(turn.referenced_evidence_ids,
                         [self.ev_trait, self.ev_habit])

    def test_out_of_context_ref_is_flagged_and_kept(self):
        """引用了上下文没给的 ID → 打标记，但**照原样记下来**交给评估器抓。

        静默丢掉错误引用 = 伪造追溯，这正是 N1 要堵的洞。
        """
        text = f"我记得[ev:ev_根本不存在_001]这件事。"
        service, _ = self._service(text)
        sid = self._session(service)
        turn = unwrap(service.run_round(self.ctx, sid, "那件事呢？"))
        self.assertIn(FLAG_EVIDENCE_REF_OUT_OF_CONTEXT, turn.safety_flags)
        self.assertEqual(turn.referenced_evidence_ids, ["ev_根本不存在_001"])

    def test_in_context_ref_not_flagged(self):
        text = f"我素来如此[ev:{self.ev_trait}]。"
        service, _ = self._service(text)
        sid = self._session(service)
        turn = unwrap(service.run_round(self.ctx, sid, "你的性情如何？"))
        self.assertNotIn(FLAG_EVIDENCE_REF_OUT_OF_CONTEXT, turn.safety_flags)

    def test_no_citation_no_flag(self):
        service, _ = self._service("（模拟）我只是随口一说。")
        sid = self._session(service)
        turn = unwrap(service.run_round(self.ctx, sid, "随便聊聊。"))
        self.assertEqual(turn.referenced_evidence_ids, [])
        self.assertNotIn(FLAG_EVIDENCE_REF_OUT_OF_CONTEXT, turn.safety_flags)


# ==========================================================================
# N4：拒答格式 + prompt 版本
# ==========================================================================


class TestAnswerFormat(CtxTestCase):

    def test_machine_words_banned_and_prefix_no_longer_instructed(self):
        """P0 破壁修复：prompt 不再教模型说「资料没写」，反而**封杀**机器词汇。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你最喜欢什么颜色？"))
        system = self._system_prompt(provider)
        # 旧指令必须消失：不再要求以「资料没写：」开头回答
        self.assertNotIn(f"必须以「{NO_EVIDENCE_PREFIX}：」开头", system)
        # 第四面墙禁令在场：封杀清单 + 活人身份声明
        self.assertIn("机器词汇或工程术语", system)
        self.assertIn("活生生的人", system)
        self.assertIn("用你所在时代的角色身份去表达", system)

    def test_refusal_rule_is_three_tiered(self):
        """拒答规则必须是**三档**（RUNNING §5.26）。

        第一代只有一条无条件规则"问到上面没有的就必须拒答"，真跑里被证明作用域
        过宽：观点题和对话内承接也一律拒答 → D/E 维度全灭。三档各管一段：
        档案事实缺（拒答）/ 观点态度（可答）/ 对话承接（可答）。
        P0 破壁修复后档一仍拒答，只是改用**角色口吻**表达。
        """
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "旁人常拿你和别人比较，你怎么想？"))
        system = self._system_prompt(provider)
        # 档一：档案事实缺失 → 角色口吻承认不知（幻觉红线继续成立）
        self.assertIn("档案事实", system)
        self.assertIn("角色口吻承认不知", system)
        self.assertIn("不得编造", system)
        # 档二：观点态度 → 可以角色化回答
        self.assertIn("看法、态度、感受", system)
        self.assertIn("以角色身份直接回答", system)
        # 档三：对话承接 → 可以顺着上文说
        self.assertIn("本段对话里", system)
        self.assertIn("承接", system)
        # 后两档的共同边界：不得声称档案里记着没写过的内容
        self.assertIn("不得声称上面档案里记着", system)

    def test_no_tier_widening_of_fact_refusal(self):
        """防回退：三档化之后，"档案事实缺必须拒答"这条红线一个字不许弱化。

        P0 只准换表达外壳（角色口吻），"不得编造/不得猜测/蒙过去"若被稀释，
        幻觉红线（C 维度 100%）就丢了——那是拿红线换通过率。同时档一必须
        明令封杀机器词汇前缀。
        """
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你母亲的闺名是什么？"))
        system = self._system_prompt(provider)
        tier1 = system.split("一、")[1].split("二、")[0]
        self.assertIn("角色口吻承认不知", tier1)
        self.assertIn("不得编造", tier1)
        self.assertIn("不得猜测", tier1)
        self.assertIn("蒙过去", tier1)
        self.assertIn("严禁", tier1)
        self.assertIn(NO_EVIDENCE_PREFIX, tier1)  # 作为**被禁词**出现在档一里

    def test_p0_fourth_wall_block_comes_first(self):
        """P0 四法则块必须在最高优先级位置（模拟声明之后、档案内容之前）。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你好"))
        system = self._system_prompt(provider)
        self.assertLess(
            system.index("第四面墙禁令"), system.index("参与角色档案"),
            "破壁禁令必须先于档案内容出现（先立'你是谁'，再给'你知道什么'）")
        self.assertLess(
            system.index("第四面墙禁令"), system.index("没有依据时怎么答"))

    def test_p0_dual_rules_present(self):
        """P0 双法则：客观考据与日常闲聊都要在场，且闲聊档要放松证据包袱。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "今天天气不错"))
        system = self._system_prompt(provider)
        self.assertIn("客观考据", system)
        self.assertIn("日常闲聊", system)
        self.assertIn("放下考据的包袱", system)
        self.assertIn("松弛感", system)

    def test_p0_modern_things_rule_present(self):
        """P0 现代未知事物法则：不装懂、时代逻辑推测、主动反问。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你知道什么是快递吗？"))
        system = self._system_prompt(provider)
        self.assertIn("从未听过的新鲜事物", system)
        self.assertIn("不要假装知道", system)
        self.assertIn("推测", system)
        self.assertIn("主动反问访客", system)

    def test_p0_initial_identity_rule_present(self):
        """P0 初始身份认知：不脑补性别年龄、中性称呼、合乎身份地探问。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你好"))
        system = self._system_prompt(provider)
        self.assertIn("初次见面的礼数", system)
        self.assertIn("严禁凭空脑补访客的性别、年龄", system)
        self.assertIn("中性称呼", system)
        self.assertIn("阁下", system)

    def test_p1_relationship_stages_present(self):
        """第二阶段：关系发展三阶段（初识/熟识/老友）必须全部在场。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你好"))
        system = self._system_prompt(provider)
        self.assertIn("关系发展：称呼与态度随熟识程度而变", system)
        self.assertIn("初识", system)
        self.assertIn("熟识", system)
        self.assertIn("老友", system)
        # 熟识：访客自述称呼后改口（"叫我X"），并提到记忆允许则跨轮沿用
        self.assertIn("「叫我黛玉」", system)
        self.assertIn("从此刻起就改用这个称呼", system)
        self.assertIn("若你的记忆允许记下这一点", system)
        # 老友：判定材料是"本轮上下文里的既有记忆"，不是数对话次数
        self.assertIn("本轮上下文里已有多条关于这位访客的既有记忆", system)
        self.assertIn("关心起对方从前提过的事", system)

    def test_p1_stages_come_after_immersion_before_profiles(self):
        """三阶段块位于四法则之后、档案内容之前（同属最高优先级区）。"""
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你好"))
        system = self._system_prompt(provider)
        self.assertLess(
            system.index("关系发展"), system.index("参与角色档案"))
        self.assertLess(
            system.index("关系发展"), system.index("没有依据时怎么答"))

    def test_citation_rule_required_by_prompt(self):
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你的性情如何？"))
        system = self._system_prompt(provider)
        self.assertIn("[ev:", system)
        self.assertIn("只能", system)

    def test_citation_example_prefix_matches_real_ids(self):
        """示例 ID 的前缀必须与真实证据 ID 一致（真实库一律 `evd_`）。

        2026-10-20 真跑 [A2]：示例写成 `[ev:ev_abc123]`，模型把正确的 `evd_xxx`
        照示例"规范化"成 `ev_xxx` → 事实追溯维度假失败（RUNNING §5.22）。
        前缀不对的示例比没有示例更糟——它教模型**改动** ID。
        """
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "你的性情如何？"))
        system = self._system_prompt(provider)
        self.assertIn("evd_abc123", system)
        self.assertNotIn("ev_abc123", system)
        self.assertIn("原样抄写", system)

    def test_prompt_version_bumped_so_idempotency_keys_do_not_collide(self):
        """改了 prompt 内容必须 bump 版本，否则新旧调用共用幂等键 → 账本漏账。"""
        self.assertTrue(DIALOGUE_PROMPT_VERSION.startswith("p6-"),
                        f"prompt 改过了但版本没 bump：{DIALOGUE_PROMPT_VERSION}")

    def test_tier_two_three_cover_framings(self):
        """P1/P2（RUNNING §5.27）：档二要覆盖"有人说你X，你认吗"的转述句式、
        档三要覆盖"后来呢/再讲讲"的深入请求句式，否则这两类会被误读成拒答。

        D2 真跑的失败正是"有人说你心眼小，你认吗"被误读成档案记载询问；
        E1 的失败正是"后来呢/再讲讲"被误读成事实追问——两档边界必须显式写进 prompt。
        """
        service, provider = self._service()
        sid = self._session(service)
        unwrap(service.run_round(self.ctx, sid, "旁人常拿你和别人比较，你怎么想？"))
        system = self._system_prompt(provider)
        # 档二：转述指控句式
        self.assertIn("你认吗", system)
        self.assertIn("有人说你", system)
        # 档三：深入请求句式
        self.assertIn("后来呢", system)
        self.assertIn("再讲讲", system)
        # 档三边界：明确"没有依据只用于档案事实类问题"
        self.assertIn("档案事实类问题", system)


if __name__ == "__main__":
    unittest.main()
