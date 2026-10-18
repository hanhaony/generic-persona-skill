"""D-287：规则 9（原子化）与规则 10（每份材料最多 N 条）的冲突裁决。

来源：**R4.4 真实检索**实测（`docs/P4_8_R44_REAL_SEARCH.md`）。

事实：25 条证据里 **19 条含 ≥3 个分句**，一条 claim 装了 2~4 个事实
（最典型：`林黛玉自幼体弱多病，但生性聪慧灵敏，曾师从贾雨村，精通诗词`
—— 4 个事实只贴得出 `knowledgeable` 一个标签，其余全丢）。
根因不是模型不听话，是两条规则在打架：规则 9 要求拆细，规则 10 要求每份
材料最多 4 条；事实多于 4 时，模型只能"合并"来保住条数。

修法（本轮）：
1. 提示词写出**优先级**——事实多于 N 时"宁可少写，不要合并"，并禁止在
   事实后追加评价（"体现出……""折射出……"）；
2. `TOKENS_PER_CLAIM` 200 → 180：10 份材料 × 4 条由 8256 降到 7456，
   `OUTPUT_CEILING_TIGHT` 不再误报（实测 4405/27 ≈ 163 token/条）；
3. 运行期加 `CLAIM_NOT_ATOMIC` 批级告警——把"原子化做到没有"变成
   每次运行自己报的数字，而不是靠人事后数（本轮就是人事后数的）。

全部离线：本地替身，不触网、零费用。
"""

import json
import unittest

from persona.core.context import new_call_context
from persona.core.result import Ok, unwrap
from persona.core.types import SourceMaterial
from persona.modules.extraction import (
    COMPOUND_CLAIM_MIN_CLAUSES,
    DEFAULT_MAX_CLAIMS_PER_MATERIAL,
    TOKENS_PER_CLAIM,
    WARNING_CLAIM_NOT_ATOMIC,
    ProviderClaimExtractor,
    claim_looks_compound,
    count_clauses,
    estimate_output_tokens,
    output_ceiling_is_sufficient,
)

PROJECT = "prj_p48_d287"
PE = "pe_a"

#: **R4.4 实测的 25 条证据原文**（`%TEMP%\gps-p48-r44\real\persona.db`）。
#: 留在这里当回归夹具：它是"原子化只做到一半"这个结论的原始数据，
#: 换成别的样本就锁不住当时那个数字了。
R44_CLAIMS: tuple[str, ...] = (
    "林黛玉言辞尖酸刻薄、喜欢怼人，这种个性折射出在发乎情止于礼仪的社会中真性情只能曲折表达的困境。",
    "林黛玉刚进贾府时告诫自己不能多说一句话、多走一步路，体现出谨言慎行的特点。",
    "林黛玉有封闭在自己的潇湘馆里静静阅读与写诗的一面。",
    "林黛玉多次讽刺挖苦薛宝钗后又坦诚道歉，检讨自己“多心”，与宝钗互剖“金兰语”。",
    "林黛玉是《红楼梦》及其衍生作品中的角色。",
    "1987年大陆央视版电视剧《红楼梦》中林黛玉由陈晓旭饰演。",
    "林黛玉是幽默爱笑的少女，曾以“争粽子吃”打趣宝玉、晴雯和袭人的争吵。",
    "林黛玉之父林如海是钦点的巡盐御史，祖上曾承袭列侯。",
    "林黛玉少聪慧，过目成诵，尤其擅长诗词。",
    "林黛玉孤高自许，与薛宝钗的圆融形成对峙。",
    "林黛玉本为太虚幻境绛珠仙草，因受宝玉前身神瑛侍者甘露之惠，愿降谪凡尘以一生眼泪相报。",
    "林黛玉祖籍姑苏，出身世代袭爵的侯门贵族，亦是书香世家。",
    "林黛玉自幼体弱多病，但生性聪慧灵敏，曾师从贾雨村，精通诗词。",
    "林黛玉与贾宝玉青梅竹马、互为知己，但最终二人未成眷属。",
    "在通行本续书中，林黛玉最终于贾宝玉、薛宝钗大婚之夜病逝。",
    "宝玉丢失通灵宝玉后昏聩，使家长的调包计得以成功，宝玉在不知情下娶了宝钗。",
    "宝玉得知黛玉亡故后放声大哭、昏死过去，病稍好后到潇湘馆见黛玉灵柩哭得死去活来。",
    "贾宝玉前世真身为神瑛侍者，是荣国府贾政与王夫人所生的次子。",
    "据脂批与《好了歌》伏笔，贾宝玉最终结局是沦为乞丐、“寒冬噎酸虀，雪夜围破毡”。",
    "贾宝玉是《红楼梦》的男主角与第一主角，深受贾母疼爱，同姐妹们一处娇养。",
    "贾宝玉性格核心是平等待人、尊重个性，主张各人按自己的意志自由生活。",
    "贾宝玉从小在女儿堆里长大，喜欢亲近女孩儿、讨厌男人，曾说“女孩都是水做的骨肉”。",
    "贾宝玉与林黛玉的爱情是世间少有的纯爱，两人关系历经猜忌、亲密、争执、相知、相惜。",
    "贾宝玉为大观园景致题名时主张“编新不如述旧，刻古终胜雕今”，提议直书“曲径通幽处”。",
    "宝玉论“泻玉”不妥，另拟“沁芳”二字，显出才情与对雅致含蓄的讲究。",
)


def _material(text: str, key: str = "key_0") -> SourceMaterial:
    return SourceMaterial(
        source_id="src_1", title="材料", url="https://example.invalid/1",
        canonical_key=key, snippet=text, source_kind="WEB_PAGE",
        subject_entity_ids=[PE], quality_status="SUMMARY_ONLY")


class _StubProvider:
    offline = True

    def __init__(self, payload: str) -> None:
        self.calls = 0
        self._payload = payload

    def chat(self, request, ctx=None):
        from persona.providers.base import ChatResponse, UsageReport
        self.calls += 1
        return Ok(ChatResponse(
            text=self._payload,
            usage=UsageReport(requests=1, tokens_in=10, tokens_out=10)))


def _candidate(key: str, claim: str) -> dict:
    return {"key": key, "material_key": "key_0", "claim": claim,
            "tier": "VERBATIM", "dimension": "TRAIT",
            "subject_entity_ids": [PE], "confidence": 0.8,
            "verbatim_quote": claim}


class TestClauseCounter(unittest.TestCase):

    def test_counts_clauses_by_chinese_separators(self):
        self.assertEqual(count_clauses("林黛玉是《红楼梦》及其衍生作品中的角色。"), 1)
        self.assertEqual(count_clauses("林黛玉少聪慧，过目成诵，尤其擅长诗词。"), 3)
        self.assertEqual(count_clauses("祖籍姑苏、出身侯门、书香世家、幼年丧母。"), 4)
        #: 空串也要给 1（"没有分句"和"一条 claim"不是一回事，不能让 0 混进去）
        self.assertEqual(count_clauses(""), 1)

    def test_threshold_is_three_clauses(self):
        self.assertEqual(COMPOUND_CLAIM_MIN_CLAUSES, 3)
        self.assertFalse(claim_looks_compound("孤高自许，与宝钗的圆融形成对峙。"))
        self.assertTrue(claim_looks_compound("林黛玉少聪慧，过目成诵，尤其擅长诗词。"))

    def test_r44_fixture_is_nineteen_of_twenty_five(self):
        """**锁住 R4.4 的实测数字**：25 条里 19 条疑似复合。

        这个数字就是"原子化只做到一半"的证据。它变了说明：
        要么夹具被改了，要么检测口径被改了——两种都该被看见。
        """
        self.assertEqual(len(R44_CLAIMS), 25)
        flagged = [c for c in R44_CLAIMS if claim_looks_compound(c)]
        self.assertEqual(len(flagged), 19)
        #: 明确不该被误报的：单事实的经典叙述。
        for atomic in (R44_CLAIMS[4], R44_CLAIMS[5], R44_CLAIMS[2]):
            self.assertFalse(claim_looks_compound(atomic), atomic)


class TestPromptResolvesTheConflict(unittest.TestCase):

    def setUp(self):
        request = ProviderClaimExtractor.build_request(
            [_material("材料正文")], None,
            max_claims_per_material=DEFAULT_MAX_CLAIMS_PER_MATERIAL)
        self.system = request.messages[0].content

    def test_priority_when_facts_exceed_the_cap(self):
        """只说"最多 4 条"模型就会合并；必须写清"拆细优先"。"""
        self.assertIn("第 9 条优先", self.system)
        self.assertIn("宁可少写几条", self.system)
        self.assertIn("也不要把几个事实合并成一条", self.system)

    def test_evaluation_clauses_are_named_and_banned(self):
        """R4.4 里 7 条带尾随评价（"体现出…""折射出…"），占掉一半字数。"""
        self.assertIn("不要在一个事实后面追加评价", self.system)
        self.assertIn("体现出", self.system)
        self.assertIn("折射出", self.system)

    def test_cap_is_still_stated_as_hard_constraint(self):
        #: 拆细优先**不是**取消条数上限——截断仍然是 100% 全损。
        self.assertIn("每份材料最多 4 条", self.system)
        self.assertIn("作废", self.system)


class TestBatchWarning(unittest.TestCase):

    def test_compound_claims_are_counted_in_one_warning(self):
        ctx = new_call_context(project_id=PROJECT, actor="test")
        compound = R44_CLAIMS[12]          # 4 个事实的 #13
        atomic = "林黛玉孤高自许。"
        payload = json.dumps({"claims": [
            _candidate("c1", compound), _candidate("c2", atomic)]},
            ensure_ascii=False)
        extractor = ProviderClaimExtractor(
            _StubProvider(payload),
            max_claims_per_material=DEFAULT_MAX_CLAIMS_PER_MATERIAL)
        outcome = unwrap(extractor.extract(
            ctx, [_material(f"{compound}{atomic}")]))
        #: 告警，不是拒收：两条都进来了。
        self.assertEqual(len(outcome.claims), 2)
        notes = [w for w in outcome.warnings if w.code == WARNING_CLAIM_NOT_ATOMIC]
        self.assertEqual(len(notes), 1)
        self.assertIn("1/2 条候选疑似复合说法", notes[0].message)
        self.assertIn("trait_tag", notes[0].message)

    def test_atomic_batch_stays_quiet(self):
        ctx = new_call_context(project_id=PROJECT, actor="test")
        claims = ["林黛玉孤高自许。", "林黛玉擅长诗词。"]
        payload = json.dumps({"claims": [
            _candidate(f"c{i}", c) for i, c in enumerate(claims)]},
            ensure_ascii=False)
        extractor = ProviderClaimExtractor(_StubProvider(payload))
        outcome = unwrap(extractor.extract(
            ctx, [_material("".join(claims))]))
        self.assertEqual(len(outcome.claims), 2)
        self.assertNotIn(WARNING_CLAIM_NOT_ATOMIC,
                         [w.code for w in outcome.warnings])


class TestCeilingAfterTokenDownshift(unittest.TestCase):

    def test_ten_materials_no_longer_trigger_the_warning(self):
        """R4.4 的真实材料数是 10——它撞过一次 `OUTPUT_CEILING_TIGHT` 误报。"""
        self.assertEqual(TOKENS_PER_CLAIM, 180)
        self.assertEqual(estimate_output_tokens(10), 7456)
        self.assertTrue(output_ceiling_is_sufficient(10))
        #: 12 份仍然装不下（48 条 × 180 + 256 = 8896 > 8192）——告警没被关掉。
        self.assertFalse(output_ceiling_is_sufficient(12))


if __name__ == "__main__":
    unittest.main()
