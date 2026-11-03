"""P4.8 整改：D-272（输出上限动态化）+ D-273（重试别把失败原因盖掉）。

两条都来自 **R4.2 诊断性复跑**的实测（`docs/P4_8_R42_REAL_SEARCH.md`）：

- **D-272**：`build_request` 写死 `max_output_tokens=4096`。R4.2 实测 9 份材料 /
  7758 输入 token，输出正好撞满 4096（`finish_reason="length"`）→ JSON 在
  第 21 条 claim 中途被切断 → `INVALID_JSON` → **21 条可用 claims 全部作废**。
  比 D-6 更致命：D-6 是门禁挡住不花钱，D-272 是**花了 0.0072426 USD 买回 0 条证据**。
  修法（用户拍板方案 ②）：上限随材料数动态计算，夹在 `[4096, 8192]`。
  - 下限 4096：材料再少也给足。省下来的额度毫无价值，一旦截断，
    已花的钱和**已生成好的 claims**一起作废。
  - 上限 8192：DeepSeek 官方 `max_tokens` 的取值上限，**不猜更大的值**。
- **D-273**：阶段级重试（`retry_policy.max_attempts=3`）会把上一次的 `FAILED`
  覆盖掉。R4.2 里抽取第一次已经花钱并失败（`EXTERNAL_UNAVAILABLE`），
  重试被幂等键挡下返回 `Ok` ⇒ 阶段最终显示 **SKIPPED / 0 候选**，
  真正的失败原因只剩 `TaskRecord.last_error` 一处——**极易被读成
  "没有东西可抽"**，而真相是"抽过并且废了"。
  修法：① 去重跳过的告警写明"跳过 ≠ 没有材料可抽"并附上上次调用的结果；
  ② 阶段结果被覆盖时，把上次的失败原因并进本次告警。

全部离线：本地替身，不触网、不调真实模型、不产生费用。
"""

import unittest

from persona.core.context import new_call_context
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Ok
from persona.core.types import SourceMaterial
from persona.modules.extraction import (
    CLAIMS_PER_MATERIAL,
    DEFAULT_MAX_CLAIMS_PER_MATERIAL,
    OUTPUT_TOKEN_CEILING,
    OUTPUT_TOKEN_FLOOR,
    OUTPUT_TOKEN_OVERHEAD,
    PROMPT_VERSION,
    TOKENS_PER_CLAIM,
    WARNING_MODEL_CALL_DUPLICATE_SKIPPED,
    WARNING_OUTPUT_CEILING_TIGHT,
    ProviderClaimExtractor,
    estimate_output_tokens,
    output_ceiling_is_sufficient,
)
from persona.orchestrator.business import (
    STAGE_EVIDENCE,
    STEP_FAILED,
    STEP_SKIPPED,
    WARNING_PRIOR_ATTEMPT_SUPERSEDED,
    BusinessOrchestrator,
    BusinessStepResult,
)

from tests.test_business_orchestrator import BusinessFixture, _plain_request

PROJECT = "prj_p48_d272"


def _materials(count: int) -> list[SourceMaterial]:
    return [
        SourceMaterial(
            source_id=f"src_{i}", title=f"材料 {i}",
            url=f"https://example.invalid/{i}", canonical_key=f"key_{i}",
            snippet="福尔摩斯与华生住在贝克街。", source_kind="WEB_PAGE",
            subject_entity_ids=["pe_a"], quality_status="SUMMARY_ONLY")
        for i in range(count)
    ]


# ==========================================================================
# D-272：输出上限随材料数走
# ==========================================================================


class OutputCeilingTest(unittest.TestCase):

    def test_nine_materials_breaks_through_the_old_fixed_ceiling(self):
        """R4.2 就是 9 份材料撞死在写死的 4096 上——现在必须高于它。"""
        self.assertGreater(estimate_output_tokens(9), 4096)

    def test_twenty_one_claims_fit_without_truncation(self):
        """用户要求的核心用例：**9 份材料、21 条 claim 时输出不被截断**。

        两个口径都要能过：
        - `TOKENS_PER_CLAIM`（D-287 取 180）是**上界口径**，算出来的上限必须 ≥ 它；
        - R4.2 **实测**密度 ≈195 token/条，用它钉死"旧的 4096 确实不够"。
        """
        needed = OUTPUT_TOKEN_OVERHEAD + 21 * TOKENS_PER_CLAIM
        self.assertGreaterEqual(estimate_output_tokens(9), needed)
        #: 实测口径：21 条 + 骨架 ≈ 4351 > 4096。这条存在的理由不能被改没了。
        measured = OUTPUT_TOKEN_OVERHEAD + 21 * 195
        self.assertLess(4096, measured)

    def test_single_material_still_gets_the_floor(self):
        """下限 4096：材料再少也给足，不为省额度冒截断的风险。"""
        self.assertEqual(estimate_output_tokens(1), OUTPUT_TOKEN_FLOOR)
        self.assertEqual(estimate_output_tokens(0), OUTPUT_TOKEN_FLOOR)

    def test_ceiling_is_never_exceeded(self):
        """上限 8192 是厂商硬顶，材料再多也不猜更大的值。"""
        self.assertEqual(estimate_output_tokens(1000), OUTPUT_TOKEN_CEILING)
        self.assertLessEqual(estimate_output_tokens(50), OUTPUT_TOKEN_CEILING)

    def test_estimate_is_monotonic_in_material_count(self):
        values = [estimate_output_tokens(n) for n in range(0, 40)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], OUTPUT_TOKEN_CEILING)

    def test_build_request_uses_the_estimate(self):
        """`build_request` 与 `estimate_output_tokens` 必须是**同一个数**——
        不允许出现"估算一套、真发另一套"。"""
        request = ProviderClaimExtractor.build_request(_materials(9), None)
        self.assertEqual(request.max_output_tokens, estimate_output_tokens(9))
        self.assertEqual(request.metadata["material_count"], "9")
        self.assertEqual(request.metadata["max_output_tokens"],
                         str(estimate_output_tokens(9)))

    def test_constants_stay_sane(self):
        self.assertLess(OUTPUT_TOKEN_FLOOR, OUTPUT_TOKEN_CEILING)
        self.assertGreater(CLAIMS_PER_MATERIAL, 0)
        self.assertGreater(TOKENS_PER_CLAIM, 0)


# ==========================================================================
# D-276（方案 B）：给"需求"加上界——每份材料最多 N 条
# ==========================================================================


class PerMaterialCapTest(unittest.TestCase):
    """D-272 只把墙推后了，**没有给需求加上界**。这一组把上界钉死。

    依据：R4.2 实测**单份**抓取正文（百度百科 2255 字）一口气产出 20 条 claims，
    9 份材料的真实需求可能是 44~100 条——而厂商硬顶 8192 只够约 39 条。
    所以提示词必须有条数约束，否则提高上限只是把截断的墙从 4096 挪到 7456。
    """

    def test_default_cap_is_four(self):
        self.assertEqual(DEFAULT_MAX_CLAIMS_PER_MATERIAL, 4)
        self.assertEqual(CLAIMS_PER_MATERIAL, DEFAULT_MAX_CLAIMS_PER_MATERIAL)

    def test_nine_materials_at_four_claims_each_exactly_fits(self):
        """用户要求的核心用例：**9 份材料 × 每份 4 条 = 36 条 → 装进 6736**。

        D-287：`TOKENS_PER_CLAIM` 200 → 180 后，同样的需求只要 6736；
        10 份 × 4 条 = 7456 也在 8192 以内（R4.4 正是 10 份材料）。
        """
        needed = OUTPUT_TOKEN_OVERHEAD + 9 * 4 * TOKENS_PER_CLAIM
        self.assertEqual(needed, 6736)
        self.assertEqual(estimate_output_tokens(9), 6736)
        self.assertTrue(output_ceiling_is_sufficient(9))
        #: 36 条 claim 本身的开销 + 骨架，必须**不超过**算出来的上限。
        self.assertLessEqual(OUTPUT_TOKEN_OVERHEAD + 36 * TOKENS_PER_CLAIM,
                             estimate_output_tokens(9))
        #: R4.4 的实际材料数是 10 份——它也必须装得下，否则告警会误报。
        self.assertEqual(estimate_output_tokens(10), 7456)
        self.assertTrue(output_ceiling_is_sufficient(10))

    def test_prompt_states_the_cap_as_a_hard_constraint(self):
        request = ProviderClaimExtractor.build_request(_materials(9), None)
        system = request.messages[0].content
        self.assertIn("每份材料最多 4 条", system)
        self.assertIn("硬约束", system)
        #: 光说"最多"不够，得说清楚**为什么**——模型才知道要挑重点而不是少写。
        self.assertIn("作废", system)

    def test_prompt_version_was_bumped(self):
        """提示词改了，版本必须 +1：幂等键由它参与生成。

        `g2-extract-2026-10-18b`：D-287 把规则 9/10 的**优先级**写进提示词
        （事实多于 N 时宁可少写、不要合并），规则文本变了 ⇒ 版本 +1。
        """
        self.assertEqual(PROMPT_VERSION, "g2-extract-2026-11-03a")
        #: 钉死"不是旧版本"——旧值回来说明有人改了提示词却没动版本。
        for old in ("g2-extract-2026-10-17", "g2-extract-2026-10-18",
                    "g2-extract-2026-10-18c"):
            self.assertNotEqual(PROMPT_VERSION, old)

    def test_cap_is_configurable_and_drives_both_prompt_and_ceiling(self):
        """N 可配置：改一处，提示词和输出上限**一起**变（不允许漂移）。"""
        request = ProviderClaimExtractor.build_request(
            _materials(9), None, max_claims_per_material=3)
        self.assertIn("每份材料最多 3 条", request.messages[0].content)
        self.assertEqual(request.max_output_tokens, estimate_output_tokens(9, 3))
        self.assertTrue(output_ceiling_is_sufficient(9, 3))

    def test_extractor_uses_its_own_configured_cap(self):
        extractor = ProviderClaimExtractor(object(), max_claims_per_material=2)
        self.assertEqual(extractor.max_claims_per_material, 2)
        request = extractor.build_request(
            _materials(9), None,
            max_claims_per_material=extractor.max_claims_per_material)
        self.assertIn("每份材料最多 2 条", request.messages[0].content)

    def test_ceiling_shortfall_is_reported_not_hidden(self):
        """N 调到装不下时，必须能**查出来**，不能等花了钱才发现。"""
        self.assertFalse(output_ceiling_is_sufficient(9, 10))
        request = ProviderClaimExtractor.build_request(
            _materials(9), None, max_claims_per_material=10)
        self.assertEqual(request.metadata["output_ceiling_sufficient"], "false")
        self.assertEqual(request.max_output_tokens, OUTPUT_TOKEN_CEILING)
        #: 够用的时候也要如实报 true——反过来谎报同样危险。
        ok = ProviderClaimExtractor.build_request(_materials(9), None)
        self.assertEqual(ok.metadata["output_ceiling_sufficient"], "true")
        self.assertEqual(ok.metadata["max_claims_per_material"], "4")

    def test_tight_ceiling_is_warned_before_spending(self):
        """材料数 × N 超过厂商硬顶时，**发请求之前**就要喊出来。

        不静默调小 N（那是隐藏行为），也不假装没事（那会白花一次钱）。
        """
        from persona.core.result import unwrap

        class _Stub:
            offline = True

            def chat(self, request, ctx=None):
                from persona.providers.base import ChatResponse, UsageReport
                return Ok(ChatResponse(
                    text='{"claims": []}',
                    usage=UsageReport(requests=1, tokens_in=10, tokens_out=10)))

        extractor = ProviderClaimExtractor(_Stub())
        ctx = new_call_context(project_id=PROJECT, actor="test")
        #: D-287 后 N=4 的保证到 **10 份**（7456 ≤ 8192），12 份才超：
        #: 12 × 4 × 180 + 256 = 8896 > 8192 ⇒ 告警。
        self.assertTrue(output_ceiling_is_sufficient(10))
        self.assertFalse(output_ceiling_is_sufficient(12))
        outcome = unwrap(extractor.extract(ctx, _materials(12)))
        codes = [w.code for w in outcome.warnings]
        self.assertIn(WARNING_OUTPUT_CEILING_TIGHT, codes)
        message = [w.message for w in outcome.warnings
                   if w.code == WARNING_OUTPUT_CEILING_TIGHT][0]
        self.assertIn("8896", message)
        self.assertIn("8192", message)
        self.assertIn("截断＝整批作废", message)

    def test_no_warning_when_the_ceiling_is_enough(self):
        """9 份 × 4 条装得下——不该凭空吓人。"""
        from persona.core.result import unwrap

        class _Stub:
            offline = True

            def chat(self, request, ctx=None):
                from persona.providers.base import ChatResponse, UsageReport
                return Ok(ChatResponse(
                    text='{"claims": []}',
                    usage=UsageReport(requests=1, tokens_in=10, tokens_out=10)))

        extractor = ProviderClaimExtractor(_Stub())
        ctx = new_call_context(project_id=PROJECT, actor="test")
        outcome = unwrap(extractor.extract(ctx, _materials(9)))
        self.assertNotIn(WARNING_OUTPUT_CEILING_TIGHT,
                         [w.code for w in outcome.warnings])

    def test_cap_is_at_least_one(self):
        """配置成 0 或负数等于"一条都不许抽"——收敛到 1，不静默变无限。"""
        self.assertEqual(ProviderClaimExtractor(object(),
                                                max_claims_per_material=0)
                         .max_claims_per_material, 1)


# ==========================================================================
# D-273：重试别把失败原因盖掉
# ==========================================================================


class _EmptyOutcomeExtractor:
    """先成功一次（把幂等键记进账本），后续调用必然撞上去重。"""

    offline = True

    def __init__(self) -> None:
        self.calls = 0

    def extract(self, ctx, materials, *, targets=None):
        from persona.modules.extraction import ExtractionOutcome
        from persona.core.enums import ExtractMethod
        self.calls += 1
        return Ok(ExtractionOutcome(method=ExtractMethod.MODEL))


class PriorAttemptVisibilityTest(unittest.TestCase):

    def _extractor_with_ledger(self):
        from persona.core.usage_ledger import ExternalUsageLedger
        from persona.modules.extraction import ProviderClaimExtractor

        class _Stub:
            offline = True

            def __init__(self):
                self.calls = 0

            def chat(self, request, ctx=None):
                from persona.providers.base import ChatResponse, UsageReport
                self.calls += 1
                return Ok(ChatResponse(
                    text='{"claims": []}',
                    usage=UsageReport(requests=1, tokens_in=10, tokens_out=10)))

        ledger = ExternalUsageLedger()
        provider = _Stub()
        extractor = ProviderClaimExtractor(provider, ledger=ledger)
        return extractor, provider, ledger

    def test_duplicate_skip_says_it_is_not_absence_of_materials(self):
        """**跳过 ≠ 没有材料可抽**——这句话必须出现在告警里。

        R4.2 的教训：阶段显示"SKIPPED / 0 候选"，被读成"没东西可抽"，
        真相是"同一意图的调用已经发生过（并且已经花过钱）"。
        """
        extractor, provider, _ = self._extractor_with_ledger()
        ctx = new_call_context(project_id=PROJECT, actor="test")
        materials = _materials(9)

        from persona.core.result import unwrap
        unwrap(extractor.extract(ctx, materials))
        self.assertEqual(provider.calls, 1)

        second = unwrap(extractor.extract(ctx, materials))
        self.assertEqual(provider.calls, 1, "第二次不该再花钱")
        codes = [w.code for w in second.warnings]
        self.assertIn(WARNING_MODEL_CALL_DUPLICATE_SKIPPED, codes)
        message = [w.message for w in second.warnings
                   if w.code == WARNING_MODEL_CALL_DUPLICATE_SKIPPED][0]
        self.assertIn("跳过 ≠ 没有材料可抽", message)
        self.assertIn("9 份材料", message)

    def test_prior_attempt_note_reports_the_recorded_outcome(self):
        """能查到上次调用的结果就写出来——不让人只看到"跳过"两个字。"""
        extractor, _provider, _ = self._extractor_with_ledger()
        ctx = new_call_context(project_id=PROJECT, actor="test")
        materials = _materials(3)
        from persona.core.result import unwrap
        unwrap(extractor.extract(ctx, materials))
        second = unwrap(extractor.extract(ctx, materials))
        message = [w.message for w in second.warnings
                   if w.code == WARNING_MODEL_CALL_DUPLICATE_SKIPPED][0]
        self.assertIn("上一次同一意图的调用", message)
        self.assertIn("outcome=", message)


class RetryOverwriteTest(unittest.TestCase):
    """阶段结果被覆盖时，上次的失败原因要跟着进本次告警。

    白盒说明：这里直接喂 `_steps` 并替掉 `_dispatch`，因为要测的是
    **"重试覆盖"这一条路径本身**，而不是再搭一条会失败的完整链路。
    走完整链路要真实模型失败才能触发，那既不稳定也不该在离线测试里做。
    """

    def test_previous_failure_is_carried_into_the_new_step(self):
        fx = BusinessFixture(record=False)
        self.addCleanup(fx.teardown)
        orch = BusinessOrchestrator(fx._deps())
        orch._request = _plain_request(fx, "阿雾", "阿灯", max_rounds=1)
        orch._steps[STAGE_EVIDENCE] = BusinessStepResult(
            name=STAGE_EVIDENCE, status=STEP_FAILED,
            message="模型返回无法解析为约定结构（INVALID_JSON）：输出不是合法 JSON")

        orch._dispatch = lambda ctx, request, name, prior: BusinessStepResult(
            name=name, status=STEP_SKIPPED,
            message="从 0 个候选中登记 0 条证据：证据阶段未产出任何证据")

        from persona.orchestrator.business import WorkUnit
        result = orch.execute(fx.ctx, WorkUnit(name=STAGE_EVIDENCE,
                                               stage=STAGE_EVIDENCE))
        self.assertTrue(result.is_ok() if hasattr(result, "is_ok") else True)
        step = orch._steps[STAGE_EVIDENCE]
        self.assertEqual(step.status, STEP_SKIPPED)
        codes = [w.code for w in step.warnings]
        self.assertIn(WARNING_PRIOR_ATTEMPT_SUPERSEDED, codes)
        carried = [w.message for w in step.warnings
                   if w.code == WARNING_PRIOR_ATTEMPT_SUPERSEDED][0]
        self.assertIn("INVALID_JSON", carried)
        self.assertIn("上次失败的唯一留痕", carried)

    def test_no_warning_when_there_was_no_previous_failure(self):
        """没有上次失败就不该凭空造一条——告警只在真的覆盖失败时出现。"""
        fx = BusinessFixture(record=False)
        self.addCleanup(fx.teardown)
        orch = BusinessOrchestrator(fx._deps())
        orch._request = _plain_request(fx, "阿雾", "阿灯", max_rounds=1)
        orch._dispatch = lambda ctx, request, name, prior: BusinessStepResult(
            name=name, status=STEP_SKIPPED, message="无材料")

        from persona.orchestrator.business import WorkUnit
        orch.execute(fx.ctx, WorkUnit(name=STAGE_EVIDENCE, stage=STAGE_EVIDENCE))
        step = orch._steps[STAGE_EVIDENCE]
        self.assertNotIn(WARNING_PRIOR_ATTEMPT_SUPERSEDED,
                         [w.code for w in step.warnings])


if __name__ == "__main__":
    unittest.main()
