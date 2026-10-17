"""P4.7 / P1a S1：**同一说法跨两个来源 ⇒ REPEATED ⇒ 进 `traits`**（零成本离线）。

# 这一轮要回答的问题

真实运行里 `traits` 恒为 0。前一轮的结论是"规则写对了，缺的是材料"，
但那只是**读代码读出来的**。S1 的职责是把这件事变成**可执行的证据**：

    给定两条"同一说法、不同来源、dimension=TRAIT"的候选，
    判定链真的会产出 `REPEATED` 并把这条说法放进 `traits` 吗？

只有这一步先过了，真实运行里 `traits=0` 才能被解释成"模型没给跨源同句"，
而不是"链路某处把结论丢了"。

# 与真实运行的关系

- 本文件**不调模型、不联网**：候选由 `MockClaimExtractor` 按 fixture 表给出；
- 它证明的是**规则层 + 编排层**（分组 → 按来源去重 → 1/≥2/≥3 源 → 进档案）；
- 它**不能**证明"真实模型会逐字复用同一句话"——那是 S3（需要 1 次真实 POST）。

# 三条红线（不因本轮的便利而放宽）

1. `SINGLE` / `UNKNOWN` 的 TRAIT **永不进 `traits`**（AC-7）；
2. 同一来源的两条近似重复**不能**算两个来源（`_assess_stabilities` 的按来源去重）；
3. 不得用语义聚类冒充"完全相同"（D-F：归一化完全相同才叫同一说法）。
"""

from __future__ import annotations

from unittest import TestCase

from persona.core.enums import EvidenceDimension, EvidenceTier, Stability
from persona.core.result import unwrap
from persona.modules.extraction import CandidateClaim
from persona.orchestrator import RUN_PARTIAL, RUN_SUCCEEDED, BusinessRequest
from persona.providers.base import SearchResult
from tests.test_business_orchestrator import BusinessFixture

#: 跨源同句的"同一说法"（逐字相同；空白差异会被归一化折叠）
SAME_CLAIM = "阿雾在黄昏时分总要先点亮灯塔再锁门"

#: 第二句用来证明"只有 1 个来源"的情形仍然进 `single_events`
SINGLE_CLAIM = "阿雾曾在一次夜雾中连续值守七十二小时"


def _material(name: str, url: str, kind: str = "OFFICIAL_MATERIAL") -> SearchResult:
    """合成材料（虚构作品/人物；正文足够长以通过正文质量门禁）。"""
    body = (f"{name}是雾港灯塔的看守，雾港地方志对她的日常有详细记载，"
            "包括她值守的时辰、巡查的路线，以及灯塔里那些旧物的来历。")
    return SearchResult(title=f"雾港手记·{name}", url=url, canonical_key=url,
                        snippet=body * 3, published_at="2021-05-04",
                        source_kind_guess=kind)


class SameClaimAcrossSourcesTest(TestCase):
    """S1 主体：2 个来源的同一说法 → `REPEATED` → `traits`。"""

    def setUp(self) -> None:
        self.material_a = _material("阿雾", "https://example.invalid/a")
        self.material_b = _material("阿雾", "https://example.invalid/b", "INTERVIEW")
        self.fx = BusinessFixture(materials={"阿雾": [self.material_a, self.material_b]})
        self.entity_id = self.fx.entity("阿雾")
        self.target = self.fx.target(self.entity_id, "阿雾")

    def tearDown(self) -> None:
        self.fx.teardown()

    # ---------------------------------------------------------------- 工具

    def _fixtures(self, *, trait_claim: str | None = SAME_CLAIM,
                  single_claim: str | None = SINGLE_CLAIM,
                  extra_a: str | None = None) -> dict:
        """两份材料各给一条**同说法** TRAIT；单源说法**只给材料 A**。

        单源说法刻意只放一份：若两份都给，它也会变成跨源重复（那正是本文件的
        主题），"单源 ⇒ single_events" 这条回归就没有对照组了。
        """
        shared = []
        if trait_claim:
            shared.append(CandidateClaim(
                claim=trait_claim,
                tier=EvidenceTier.PROFILE_BACKGROUND,
                dimension=EvidenceDimension.TRAIT,
                subject_entity_ids=[self.entity_id],
                confidence=0.8))
        only_a = []
        if single_claim:
            only_a.append(CandidateClaim(
                claim=single_claim,
                tier=EvidenceTier.PROFILE_BACKGROUND,
                dimension=EvidenceDimension.TRAIT,
                subject_entity_ids=[self.entity_id],
                confidence=0.8))
        fixtures = {
            self.material_a.canonical_key: list(shared) + list(only_a),
            self.material_b.canonical_key: list(shared),
        }
        if extra_a:
            fixtures[self.material_a.canonical_key].append(CandidateClaim(
                claim=extra_a,
                tier=EvidenceTier.PROFILE_BACKGROUND,
                dimension=EvidenceDimension.TRAIT,
                subject_entity_ids=[self.entity_id],
                confidence=0.8))
        return fixtures

    def _run(self, fixtures: dict):
        extractor = self.fx.use_extractor(fixtures)
        result = unwrap(self.fx.orch.run(
            self.fx.ctx, BusinessRequest(targets=[self.target], max_rounds=0)))
        self.extractor = extractor
        return result

    def _profile(self):
        return unwrap(self.fx.persona.get_persona(self.fx.ctx, self.entity_id))

    def _evidence_of(self, claim: str):
        rows = [unwrap(self.fx.evidence.get_evidence(self.fx.ctx, ev_id))
                for ev_id in self.result.evidence_ids]
        return [row for row in rows if row.claim == claim]

    # ---------------------------------------------------------------- P1a-1

    def test_cross_source_same_claim_is_judged_repeated(self):
        """P1a-1：判定器看到 2 条证据、2 个不同来源 ⇒ `REPEATED`。"""
        self.result = self._run(self._fixtures())
        # 单目标：关系/对话阶段按语义跳过 → 整体 PARTIAL。这里只关心证据与档案。
        self.assertEqual(self.result.status, RUN_PARTIAL)

        rows = self._evidence_of(SAME_CLAIM)
        self.assertEqual(len(rows), 2, "跨源同句必须登记成两条证据（不跨源去重）")
        self.assertEqual(len({row.source_id for row in rows}), 2,
                         "两条证据必须来自两个不同来源")

        verdict = unwrap(self.fx.evidence.judge_stability(
            self.fx.ctx, self.entity_id, SAME_CLAIM, [str(r.id) for r in rows]))
        self.assertEqual(str(verdict.stability), Stability.REPEATED)
        self.assertEqual(verdict.occurrences, 2)
        self.assertEqual(verdict.distinct_sources, 2)

    def test_cross_source_same_claim_lands_in_traits(self):
        """P1a-2：`REPEATED` 的 TRAIT ⇒ 进 `traits`，且**不**重复进 `single_events`。"""
        self.result = self._run(self._fixtures())
        profile = self._profile()

        trait_texts = [item.text for item in profile.traits]
        self.assertIn(SAME_CLAIM, trait_texts, f"traits={trait_texts}")
        self.assertNotIn(SAME_CLAIM, [item.text for item in profile.single_events],
                         "同一条说法不得同时进两个桶")
        # 只有这一条是跨源重复的：不同说法一条都不许混进来
        self.assertEqual(set(trait_texts), {SAME_CLAIM}, f"traits={trait_texts}")

        # G12 / D-250（已修，登记于 D-247）：档案条目身份 = **维度 + 归一化说法 + 主体集合**，
        # 所以跨源同句只产出**一个**条目，两条证据都收进同一个 `evidence_refs`。
        # 这正是"有多少来源支持"变得可读的地方（AC-7 的方向）。
        self.assertEqual(len(trait_texts), 1,
                         f"同句跨两源 ⇒ 合并成一个条目，traits={trait_texts}")
        self.assertEqual(len(profile.traits[0].evidence_refs), 2,
                         "合并后的条目必须保留**两条**证据引用（不是留一条）")

        for item in profile.traits:
            self.assertEqual(str(item.stability), Stability.REPEATED)

    def test_merged_item_id_is_stable_across_reruns(self):
        """合并后的 `item_id` 必须**可复现**（重跑同一份材料 → 同一身份）。

        用内置 `hash()` 会随 `PYTHONHASHSEED` 变化，每次重跑都会判定成
        "删一条 + 增一条"，版本 diff 变成噪声。这里用同一份材料**再跑一次**
        来钉住：条目身份与"一条说法一个条目"都必须保持不变。
        """
        self.result = self._run(self._fixtures())
        first = [(item.item_id, item.text) for item in self._profile().traits]
        self.assertRegex(first[0][0], r"^pi_[0-9a-f]{16}$")

        self.result = self._run(self._fixtures())
        second = [(item.item_id, item.text) for item in self._profile().traits]
        self.assertEqual(second, first, "同一份材料重跑必须得到同一个条目身份")

    def test_verdict_is_written_back_to_every_member(self):
        """判定结论要落库（G6-D1），否则重启后档案会重算成另一副样子。"""
        self.result = self._run(self._fixtures())
        rows = self._evidence_of(SAME_CLAIM)
        for row in rows:
            self.assertEqual(str(row.effective_stability), Stability.REPEATED)
            self.assertIsInstance(row.stability_verdict, dict)
            self.assertEqual(row.stability_verdict.get("distinct_sources"), 2)

    # ---------------------------------------------------------------- P1a-3

    def test_single_source_claim_still_goes_to_single_events(self):
        """P1a-3：只有 1 个来源的说法**仍然**进 `single_events`（现状不得改变）。"""
        self.result = self._run(self._fixtures())
        profile = self._profile()
        self.assertIn(SINGLE_CLAIM, [item.text for item in profile.single_events])
        self.assertNotIn(SINGLE_CLAIM, [item.text for item in profile.traits],
                         "单源说法永远不许进 traits（AC-7）")

    def test_same_source_whitespace_variants_do_not_count_as_two_sources(self):
        """红线 2：同一来源的两条近似重复不能凑成 `REPEATED`。

        构造方式：只给一份材料，两条 claim 仅差一个空格 ⇒ 精确 claim 不同
        （所以是两条证据），但归一化后是同一说法、来源也只有一个。
        判定必须是 `SINGLE`，这两条都只能进 `single_events`。
        """
        variant = SAME_CLAIM.replace("总要先", "总要 先")
        self.fx.teardown()
        self.fx = BusinessFixture(materials={"阿雾": [self.material_a]})
        self.entity_id = self.fx.entity("阿雾")
        self.target = self.fx.target(self.entity_id, "阿雾")
        fixtures = {self.material_a.canonical_key: [
            CandidateClaim(claim=SAME_CLAIM, tier=EvidenceTier.PROFILE_BACKGROUND,
                           dimension=EvidenceDimension.TRAIT,
                           subject_entity_ids=[self.entity_id], confidence=0.8),
            CandidateClaim(claim=variant, tier=EvidenceTier.PROFILE_BACKGROUND,
                           dimension=EvidenceDimension.TRAIT,
                           subject_entity_ids=[self.entity_id], confidence=0.8),
        ]}
        self.result = self._run(fixtures)

        rows = self._evidence_of(SAME_CLAIM) + self._evidence_of(variant)
        self.assertEqual(len(rows), 2, "两条 claim 精确文本不同 → 两条证据")
        self.assertEqual(len({row.source_id for row in rows}), 1)
        for row in rows:
            self.assertEqual(str(row.effective_stability), Stability.SINGLE,
                             "同一来源不得凑成 REPEATED（G6-D2）")

        profile = self._profile()
        self.assertEqual([item.text for item in profile.traits], [],
                         "同一来源的近似重复绝不能固化成 traits")

    # ---------------------------------------------------------------- P1a-4

    def test_three_sources_make_a_pattern(self):
        """P1a-4：同一说法出现在 3 个来源 ⇒ `PATTERN`（比 REPEATED 更强）。"""
        self.fx.teardown()
        third = _material("阿雾", "https://example.invalid/c", "BIOGRAPHY")
        self.fx = BusinessFixture(materials={"阿雾": [
            self.material_a, self.material_b, third]})
        self.entity_id = self.fx.entity("阿雾")
        self.target = self.fx.target(self.entity_id, "阿雾")
        claim = CandidateClaim(claim=SAME_CLAIM, tier=EvidenceTier.PROFILE_BACKGROUND,
                               dimension=EvidenceDimension.TRAIT,
                               subject_entity_ids=[self.entity_id], confidence=0.8)
        fixtures = {m.canonical_key: [claim] for m in
                    (self.material_a, self.material_b, third)}
        self.result = self._run(fixtures)

        rows = self._evidence_of(SAME_CLAIM)
        self.assertEqual(len(rows), 3)
        verdict = unwrap(self.fx.evidence.judge_stability(
            self.fx.ctx, self.entity_id, SAME_CLAIM, [str(r.id) for r in rows]))
        self.assertEqual(str(verdict.stability), Stability.PATTERN)
        traits = self._profile().traits
        self.assertEqual([item.text for item in traits], [SAME_CLAIM])
        # G12 / D-250：3 个来源合并成**一个**条目，引用 3 条证据
        self.assertEqual(len(traits[0].evidence_refs), 3,
                         "合并后的条目必须保留全部 3 条证据引用")
        self.assertEqual(str(traits[0].stability), Stability.PATTERN)

    # ---------------------------------------------------------------- G12 边界

    def test_only_whitespace_equal_claims_are_merged(self):
        """G12 的诚实边界：**归一化完全相同**才合并，标点不同就不合并。

        `_fold_whitespace` 只折空白（D-104）。两句仅差一个句号时是**两个说法**，
        各自成一个条目——这正是"M 只能合并同句，不能聚类同义句"的可执行证据。
        如果这里合并了，说明有人在归一化里偷偷做了语义处理（红线 3）。
        """
        punctuated = SAME_CLAIM + "。"
        claim_a = CandidateClaim(claim=SAME_CLAIM, tier=EvidenceTier.PROFILE_BACKGROUND,
                                 dimension=EvidenceDimension.TRAIT,
                                 subject_entity_ids=[self.entity_id], confidence=0.8)
        claim_b = CandidateClaim(claim=punctuated, tier=EvidenceTier.PROFILE_BACKGROUND,
                                 dimension=EvidenceDimension.TRAIT,
                                 subject_entity_ids=[self.entity_id], confidence=0.8)
        fixtures = {self.material_a.canonical_key: [claim_a, claim_b],
                    self.material_b.canonical_key: [claim_a, claim_b]}
        self.result = self._run(fixtures)

        self.assertEqual(len(self._evidence_of(SAME_CLAIM)), 2)
        self.assertEqual(len(self._evidence_of(punctuated)), 2)
        trait_texts = sorted(item.text for item in self._profile().traits)
        self.assertEqual(trait_texts, sorted([SAME_CLAIM, punctuated]),
                         "两个不同说法必须各自成条（不得语义聚类）")
        for item in self._profile().traits:
            self.assertEqual(len(item.evidence_refs), 2,
                             "每个条目各自有 2 条证据支撑")
