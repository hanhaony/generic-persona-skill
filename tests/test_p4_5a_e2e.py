"""P4.5-A 端到端集成测试（**全离线**：桩 Provider，零 socket、零真实请求、零费用）。

覆盖 P4.5-A 设计稿的 A1–A8 / A10 / A11 / A12：

| 断言 | 落在哪个测试 |
| --- | --- |
| A1 真实调用恰好 1 次 | `TestScriptSideChain.test_a1_*` |
| A2 阶段语义（单人物） | `TestStageSemantics.*` |
| A3 证据可追溯 | `TestScriptSideChain.test_a3_*` |
| A4 摘录可独立复算 | `TestScriptSideChain.test_a4_*` |
| A5 / A6 费用复算与记账语义 | `TestScriptSideChain.test_a5_*` / `test_a6_*` |
| A7 档案引用可追溯 | `TestScriptSideChain.test_a7_*` |
| A8 AC-7 单次事件不进 traits | `TestScriptSideChain.test_a8_*` |
| A10 幂等 + 预算闸门 | `TestIdempotence.*` |
| A11 门禁负样例（零调用） | `TestGateNegatives.*` |
| A12 格式正确 ≠ 事实可信 | `TestScriptSideChain.test_a12_*` |
| **G4/G5 修复回归锁** | `TestG4FixedMaterialFlow.*` / `TestG5WarningsPropagation.*` |
| **编排器真实运行接线锁** | `TestOrchestratorRealRunWiring.*` |
| **原始响应捕获 + 幂等重跑（0 真实调用）** | `TestRawCaptureAndIdempotentRerun.*` |
| **G6 已知缺口锁（stability 未接线）** | `TestKnownGapStabilityNotWired.*` |
| **G7 空产出不得降级覆盖（A 级写风险）** | `TestG7DegradedRevisionSkipped.*` |
| **G8 双人物真实运行入口（`--names`）** | `TestG8DualPersonEntry.*` |
| **G10 `--dump` 落点不得进仓库** | `TestG10DumpPath.*` |

设计纪律：
- **不 import 被测校验逻辑**做断言：A4 的复算走 `scripts/verify_p4_5a_outputs.py`，
  它是独立实现（自己切片、自己 `str.find`）；
- 不联网：`StubChatProvider` 与 `StubSearchExtractProvider` 全部离线，
  并用 `network_blocked` 兜底（既是断言也是护栏）；
- 配置用 `load_settings(env=...)` **显式注入**，不依赖本机环境变量与 `.env`。
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (_REPO_ROOT, _REPO_ROOT / "scripts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import e2e_p4_5a as runner                                              # noqa: E402
import verify_p4_5a_outputs as verifier                                 # noqa: E402

from persona.config import load_settings                                # noqa: E402
from persona.core.result import (                                       # noqa: E402
    Ok,
    WarningNote,
    is_err,
    unwrap,
)
from persona.core.usage_ledger import ExternalUsageLedger                # noqa: E402
from persona.modules.extraction import (                                # noqa: E402
    REJECT_MATERIAL_NOT_GATE_PASSED,
    REJECT_MATERIAL_UNDECLARED,
    REJECT_QUOTE_NOT_IN_MATERIAL,
    WARNING_MATERIAL_QUALITY_UNDECLARED,
    WARNING_MODEL_CALL_DUPLICATE_SKIPPED,
    ExtractionOutcome,
    RejectedClaim,
)
from persona.core.enums import Cardinality, ProfileStatus              # noqa: E402
from persona.modules.evidence import EvidenceDraft, SourceDraft         # noqa: E402
from persona.modules.relationship_analysis import (                     # noqa: E402
    RuleRelationshipAnalyzer,
)
from persona.modules.persona import (                                   # noqa: E402
    WARNING_PROFILE_DEGRADED_SKIPPED,
)
from persona.modules.relationship import (                              # noqa: E402
    WARNING_RELATIONSHIP_DEGRADED_SKIPPED,
    PerspectiveBlock,
    RelationshipDraft,
    SharedExperienceItem,
)
from persona.orchestrator import (                                      # noqa: E402
    STAGE_DIALOGUE,
    STAGE_EVIDENCE,
    STAGE_PERSONA,
    STAGE_RELATIONSHIP,
    STAGE_RESEARCH,
    RUN_PARTIAL,
    RUN_SUCCEEDED,
    STEP_EXECUTED,
    STEP_SKIPPED,
    BusinessOrchestrator,
)
from persona.providers.mock import network_blocked                      # noqa: E402
import persona.orchestrator.business as runner_business                # noqa: E402

#: **假**密钥占位值，仅用于验证"不会被打印"。不是任何真实凭据。
SECRET_PLACEHOLDER = "placeholder-not-a-real-key-p45a"

PROJECT = runner.PROJECT


def settings_for() -> object:
    """显式注入一份齐全配置（不读 `.env`、不依赖本机环境变量）。"""
    env = dict(runner.E2E_CONFIG)
    env["DEEPSEEK_API_KEY"] = SECRET_PLACEHOLDER
    result = load_settings(use_dotenv=False, env=env)
    if is_err(result):
        raise AssertionError(f"测试配置本身不合法：{result.error.message}")
    return unwrap(result)


def payload_for(entity_id: str, material_key: str) -> str:
    """构造一份**形状与真实模型一致**的载荷（离线扮演模型输出）。

    5 条候选全部 VERBATIM，摘录都是 `E2E_TEXT` 的**逐字子串**（这样 locator 才核验得过）。
    第 5 条刻意是 `TRAIT + SINGLE` —— AC-7 探针（单次事件不得进 `traits`）。
    """
    import json

    def claim(key, claim_text, quote, dimension, stability):
        return {
            "key": key, "claim": claim_text, "tier": "VERBATIM",
            "dimension": dimension, "subject_entity_ids": [entity_id],
            "material_key": material_key, "verbatim_quote": quote,
            "stability": stability, "canon_level": "CANON", "confidence": 0.9,
        }

    return json.dumps({"claims": [
        claim("c1", "阿雾是雾港灯塔的常年值守者",
              "阿雾是雾港灯塔的常年值守者", "BACKGROUND", "REPEATED"),
        claim("c2", "阿雾偏爱在傍晚独自巡视堤坝",
              "阿雾偏爱在傍晚独自巡视堤坝", "HABIT", "REPEATED"),
        claim("c3", "阿雾向来沉默寡言",
              "阿雾向来沉默寡言", "TRAIT", "PATTERN"),
        claim("c4", "阿雾习惯在整点敲响灯塔的铜钟",
              "阿雾习惯在整点敲响灯塔的铜钟", "HABIT", "PATTERN"),
        claim("c5", "阿雾曾在一次夜雾中连续值守七十二小时",
              "阿雾曾在一次夜雾中连续值守七十二小时", "TRAIT", "SINGLE"),
    ]}, ensure_ascii=False)


class ChainResult:
    """一次脚本侧全链的运行结果（供各断言复用）。"""

    def __init__(self, *, stack, ledger, out, entity_id, research_output,
                 materials, outcome, evidence_ids, draft, profile_id,
                 artifact, provider, recorder) -> None:
        self.stack = stack
        self.ledger = ledger
        self.out = out
        self.entity_id = entity_id
        self.research_output = research_output
        self.materials = materials
        self.outcome = outcome
        self.evidence_ids = evidence_ids
        self.draft = draft
        self.profile_id = profile_id
        self.artifact = artifact
        self.provider = provider
        self.recorder = recorder

    def teardown(self) -> None:
        self.stack.teardown()


def run_chain(*, payload: str | None = None, chat=None) -> ChainResult:
    """跑一遍脚本侧全链（桩数据源 + 桩模型），返回全部中间产物。"""
    settings = settings_for()
    ledger = ExternalUsageLedger()
    out = runner.Out(SECRET_PLACEHOLDER, quiet=True)   # 假密钥 → 顺便做泄漏自检
    stack = runner.Stack(settings, ledger, out)

    entity_id = stack.ensure_entity("阿雾")
    research = stack.research.run(stack.ctx, [stack.target])
    if is_err(research):
        stack.teardown()
        raise AssertionError(f"研究链路失败：{research.error.message}")
    ro = research.value

    materials, _rejected = runner.materials_from_research(stack, ro)

    stub = chat if chat is not None else runner.StubChatProvider(
        payload if payload is not None else payload_for(entity_id, runner.E2E_URL))
    recorder = runner.CountingChatProvider(stub)
    extractor = stack.use_real_extractor(recorder)

    extracted = extractor.extract(stack.ctx, materials, targets=[stack.target])
    if is_err(extracted):
        stack.teardown()
        raise AssertionError(f"抽取失败：{extracted.error.message}")
    outcome = extracted.value

    orch = stack.orchestrator()
    step = orch._register_candidates(stack.ctx, outcome, list(ro.source_ids))
    if is_err(step):
        stack.teardown()
        raise AssertionError(f"证据登记失败：{step.error.message}")
    evidence_ids = list(step.artifact_ids.get("evidence_ids", []))

    loaded = orch._load_evidence(stack.ctx, stack.request(), evidence_ids)
    if is_err(loaded):
        stack.teardown()
        raise AssertionError("加载证据失败")
    items = loaded.get(entity_id, [])
    draft = BusinessOrchestrator._build_persona_draft(entity_id, items, "FICTIONAL_CHARACTER")
    written = orch._write_persona(stack.ctx, entity_id, draft)
    if is_err(written):
        stack.teardown()
        raise AssertionError(f"人格档案写入失败：{written.error.message}")
    profile_id, _already = written

    artifact = runner.build_artifact(
        materials=materials, outcome=outcome, evidence_ids=evidence_ids,
        source_ids=list(ro.source_ids), draft=draft, entity_id=entity_id,
        ledger=ledger, limits=extractor.limits, recorder=recorder,
        elapsed=0.0, target=stack.target)
    artifact["persona"]["profile_id"] = profile_id

    return ChainResult(
        stack=stack, ledger=ledger, out=out, entity_id=entity_id,
        research_output=ro, materials=materials, outcome=outcome,
        evidence_ids=evidence_ids, draft=draft, profile_id=profile_id,
        artifact=artifact, provider=stub, recorder=recorder)


class TestScriptSideChain(unittest.TestCase):
    """A1 / A3–A8 / A12：脚本侧全链（真实研究代码 + 真实门禁 + 桩模型）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run_chain()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.r.teardown()

    # ---------------------------------------------------------- A1
    def test_a1_model_called_exactly_once(self):
        self.assertEqual(self.r.recorder.calls, 1, "真实调用必须恰好 1 次")
        self.assertEqual(self.r.provider.chat_calls, 1)
        self.assertEqual(self.r.out.leak, False, "输出里不得出现密钥")

    def test_a1b_offline_guard_actually_blocks(self):
        """断网护栏生效：护栏内任何真实连接都会抛 `OfflineViolation`。"""
        import socket

        from persona.providers.mock import OfflineViolation
        with network_blocked():
            with self.assertRaises(OfflineViolation):
                socket.create_connection(("api.deepseek.com", 443), timeout=1)

    # ---------------------------------------------------------- 材料与门禁
    def test_materials_are_gate_valid_and_bound(self):
        self.assertEqual(len(self.r.materials), 1)
        m = self.r.materials[0]
        self.assertEqual(m.quality_status, "VALID", "只有过门禁的正文才能进抽取器")
        self.assertTrue(m.source_id, "材料必须绑定已登记来源 ID")
        self.assertIn(m.source_id, list(self.r.research_output.source_ids))
        self.assertEqual(m.snippet, runner.E2E_TEXT, "摘录就是门禁判定的那段正文")

    def test_research_gate_judged_the_extract_not_the_fulltext(self):
        """P4.5 方案 C：门禁判**摘录**（下游真正拿到的那段）。"""
        extracts = self.r.research_output.extracted
        self.assertEqual(len(extracts), 1)
        item = extracts[0]
        self.assertIsNotNone(item.quality)
        self.assertEqual(item.quality.status, "VALID")
        self.assertGreaterEqual(item.quality.prose_chars, 200)

    # ---------------------------------------------------------- A3 / A4
    def test_a3_every_candidate_traceable_to_this_run(self):
        known = set(self.r.research_output.source_ids)
        self.assertTrue(known, "本次研究必须登记了来源")
        for c in self.r.outcome.claims:
            self.assertIn(c.source_id, known, f"{c.key} 的来源越界")

    def test_a4_verbatim_quotes_land_in_material_with_framework_locator(self):
        """摘录必须能在材料里**逐字命中**，且 `locator` 由框架算出。"""
        snippet = self.r.materials[0].snippet
        for c in self.r.outcome.claims:
            self.assertTrue(c.verbatim_quote, f"{c.key} 声称原话却没摘录")
            self.assertIsNotNone(c.locator, f"{c.key} 缺 locator")
            self.assertEqual(c.locator.get("field"), "snippet")
            start, end = c.locator.get("start"), c.locator.get("end")
            self.assertIsInstance(start, int)
            self.assertIsInstance(end, int)
            self.assertEqual(snippet[start:end], c.verbatim_quote,
                             f"{c.key} 的 locator 与摘录不一致")

    def test_a4b_all_claims_accepted_no_reject_no_pending(self):
        self.assertEqual(self.r.outcome.verdict, "OK")
        self.assertEqual(len(self.r.outcome.claims), 5)
        self.assertEqual(self.r.outcome.rejected, [])
        self.assertEqual(self.r.outcome.pending, [])

    # ---------------------------------------------------------- A5 / A6
    def test_a5_cost_is_recomputable_from_response_tokens(self):
        """跨组件核对：Provider 响应报的 token × 配置单价 == 账本 cost_confirmed。"""
        rec = [x for x in self.r.ledger.records() if x.operation == "CHAT"][0]
        resp_usage = self.r.artifact["extraction"]["response_usage"]
        expected = (resp_usage["tokens_in"] / 1000.0 * 0.0003
                    + resp_usage["tokens_out"] / 1000.0 * 0.0012)
        self.assertAlmostEqual(rec.cost_confirmed, expected, places=12)
        self.assertLessEqual(self.r.ledger.occupied_cost(), 0.25)

    def test_a6_ledger_semantics(self):
        recs = self.r.ledger.records()
        self.assertEqual(len(recs), 1, "只允许 1 笔外部调用")
        rec = recs[0]
        self.assertEqual(rec.operation, "CHAT")
        self.assertEqual(rec.currency, "USD")
        self.assertEqual(rec.cost_status, "CONFIRMED")
        self.assertEqual(rec.cost_source, "OFFICIAL_RATE")
        self.assertTrue(rec.request_id, "服务端 request_id 必须留痕以便对账")

    # ---------------------------------------------------------- A7 / A8
    def test_a7_persona_refs_all_traceable(self):
        known = set(self.r.evidence_ids)
        self.assertTrue(known, "必须登记了证据")
        refs: list[str] = list(self.r.draft.background.evidence_refs)
        for bucket in (self.r.draft.traits, self.r.draft.habits,
                       self.r.draft.single_events):
            for item in bucket:
                refs += list(item.evidence_refs)
        self.assertTrue(refs, "档案必须引用证据（不得凭空空转）")
        untraceable = [r for r in refs if r not in known]
        self.assertEqual(untraceable, [], f"不可追溯引用：{untraceable}")

    def test_a8_single_event_never_becomes_trait(self):
        """AC-7 / PF-V2 / D-012：单次行为不得固化成稳定性格。"""
        probe = "阿雾曾在一次夜雾中连续值守七十二小时"
        trait_texts = [i.text for i in self.r.draft.traits]
        single_texts = [i.text for i in self.r.draft.single_events]
        self.assertNotIn(probe, trait_texts, "单次事件不得进 traits")
        self.assertIn(probe, single_texts, "单次事件应进 single_events")
        for item in self.r.draft.traits:
            self.assertNotIn(str(item.stability), ("SINGLE", "UNKNOWN"),
                             f"traits 里的 {item.text} 稳定性不足")

    # ---------------------------------------------------------- A12
    def test_a12_no_truthiness_field_inflated(self):
        """格式正确 ≠ 事实可信：产物里不得出现"已验证/已批准"被置真。"""
        self.assertNotIn("verified", self.r.artifact)
        for c in self.r.artifact["extraction"]["claims"]:
            self.assertNotIn("evidence_id", c)
            self.assertNotIn("verified", c)

    # ---------------------------------------------------------- 独立复算器
    def test_independent_verifier_passes_on_real_artifact(self):
        """`verify_p4_5a_outputs.py` 是**独立实现**，它对真实产物必须全绿。"""
        rep = verifier.verify(self.r.artifact)
        passed, total = rep.summary()
        failed = [name for name, ok, _ in rep.rows if not ok]
        self.assertEqual(failed, [], f"独立复算失败项：{failed}")
        self.assertGreaterEqual(total, 20)
        self.assertEqual(passed, total)

    def test_independent_verifier_detects_tampered_quote(self):
        """**突变检验**：把摘录改坏，复算器必须报错（否则它只是恒真的摆设）。"""
        import copy
        broken = copy.deepcopy(self.r.artifact)
        broken["extraction"]["claims"][0]["verbatim_quote"] = "阿雾根本不存在"
        rep = verifier.verify(broken)
        failed = [name for name, ok, _ in rep.rows if not ok]
        self.assertTrue(any("A4" in n for n in failed),
                        f"复算器没抓到被篡改的摘录：failed={failed}")

    def test_independent_verifier_detects_tampered_cost(self):
        import copy
        broken = copy.deepcopy(self.r.artifact)
        broken["ledger"]["records"][0]["cost_confirmed"] = 0.0
        rep = verifier.verify(broken)
        failed = [name for name, ok, _ in rep.rows if not ok]
        self.assertTrue(any("A5" in n for n in failed),
                        f"复算器没抓到被篡改的费用：failed={failed}")


class TestIdempotence(unittest.TestCase):
    """A10：重复意图不重复花钱；次数上限拦住第二次。"""

    def setUp(self) -> None:
        self.settings = settings_for()
        self.ledger = ExternalUsageLedger()
        self.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        self.stack = runner.Stack(self.settings, self.ledger, self.out)
        self.entity_id = self.stack.ensure_entity("阿雾")
        research = self.stack.research.run(self.stack.ctx, [self.stack.target])
        self.ro = unwrap(research)
        self.materials, _ = runner.materials_from_research(self.stack, self.ro)
        self.stub = runner.StubChatProvider(
            payload_for(self.entity_id, runner.E2E_URL))
        self.extractor = self.stack.use_real_extractor(self.stub)

    def tearDown(self) -> None:
        self.stack.teardown()

    def test_a10_duplicate_intent_skips_second_send(self):
        first = unwrap(self.extractor.extract(
            self.stack.ctx, self.materials, targets=[self.stack.target]))
        self.assertEqual(self.stub.chat_calls, 1)
        second = unwrap(self.extractor.extract(
            self.stack.ctx, self.materials, targets=[self.stack.target]))
        self.assertEqual(self.stub.chat_calls, 1, "重复意图不得再发一次")
        codes = [w.code for w in second.warnings]
        self.assertIn(WARNING_MODEL_CALL_DUPLICATE_SKIPPED, codes)
        self.assertEqual(len(self.ledger.records()), 1, "账本不得多记一笔")

    def test_a10b_call_cap_blocks_further_calls(self):
        """`max_calls=1`：换一份材料（幂等键不同）也必须被次数闸门拦住。"""
        unwrap(self.extractor.extract(
            self.stack.ctx, self.materials, targets=[self.stack.target]))
        other = list(self.materials)
        other_material = other[0]
        other_material.canonical_key = "p45a:other-material"
        result = self.extractor.extract(
            self.stack.ctx, other, targets=[self.stack.target])
        # 换 key 后幂等守卫不生效 → 应由 `max_calls` 闸门拦下（Err）
        if is_err(result):
            from persona.core.errors import ErrorCode
            self.assertEqual(result.error.code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(self.stub.chat_calls, 1, "次数上限必须拦住第二次调用")


class TestGateNegatives(unittest.TestCase):
    """A11：不合格材料必须被拦在模型调用之前（零费用）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = settings_for()
        cls.ledger = ExternalUsageLedger()
        cls.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        cls.rows = runner.a11_gate_negatives(cls.settings, cls.ledger, cls.out)

    def test_a11_all_negatives_blocked(self):
        for row in self.rows:
            self.assertTrue(row["ok"], f"负样例未按预期拦住：{row}")

    def test_a11_zero_model_calls(self):
        for row in self.rows:
            self.assertEqual(row["chat_calls"], 0, "负样例不得触发模型调用")
            self.assertEqual(row["claims"], 0)

    def test_a11_codes_are_specific(self):
        codes = {row["case"]: row["codes"] for row in self.rows}
        self.assertIn(REJECT_MATERIAL_NOT_GATE_PASSED,
                      next(v for k, v in codes.items() if "LOW_QUALITY" in k))
        self.assertIn(REJECT_MATERIAL_UNDECLARED,
                      next(v for k, v in codes.items() if "未声明" in k))

    def test_a11_ledger_untouched(self):
        self.assertEqual(len(self.ledger.records()), 0, "零调用 → 账本不得有记录")


class TestStageSemantics(unittest.TestCase):
    """A2（单人物修订版）：阶段语义必须**诚实**反映单人物链路。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        cls.diag = runner.diagnose_orchestrator_path(settings_for(), cls.out)

    def test_a2_research_stage_executed(self):
        self.assertTrue(self.diag["ok"], self.diag)
        self.assertEqual(self.diag["stage_status"][STAGE_RESEARCH], STEP_EXECUTED)

    def test_a2b_single_person_skips_relationship_and_dialogue(self):
        """参与者 <2 人 → 关系与对话阶段 SKIPPED（不伪造），任务仍干净收敛。"""
        self.assertEqual(self.diag["stage_status"][STAGE_RELATIONSHIP], STEP_SKIPPED)
        self.assertEqual(self.diag["stage_status"][STAGE_DIALOGUE], STEP_SKIPPED)
        self.assertEqual(self.diag["task_status"], "COMPLETED")
        self.assertEqual(self.diag["status"], RUN_PARTIAL,
                         "有阶段 SKIPPED → 只能报 PARTIAL，不得报全流程成功")

    def test_a2c_persona_stage_still_runs(self):
        self.assertIn(self.diag["stage_status"][STAGE_PERSONA],
                      (STEP_EXECUTED, STEP_SKIPPED))


class TestG4FixedMaterialFlow(unittest.TestCase):
    """G4 回归锁：**编排器路径**必须真的把抓取正文交到抽取器手里。

    修复前这里是"模型永不被调用、证据恒为 0"；这些断言在修复被回退时会立刻变红。

    单人物用例整体仍 `PARTIAL` —— 这一点**如实保留**：参与者 <2 时关系/对话阶段
    按语义跳过，与 G4 无关。要证明"整链真的通了"，必须看双人物用例（见
    `test_two_person_run_reaches_succeeded`）。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        cls.settings = settings_for()
        cls.diag = runner.diagnose_orchestrator_path(cls.settings, cls.out)
        cls.diag_two = runner.diagnose_orchestrator_path(
            cls.settings, cls.out, two_person=True)

    # ------------------------------------------------------- 静态：调用点必须存在
    def test_materials_from_extracts_has_a_product_call_site(self):
        """产品代码**必须**有 `materials_from_extracts()` 的调用点。

        用 AST 精确定位真正的调用（注释与 docstring 提及不算），避免 grep 误报。
        """
        import ast

        calls: list[str] = []
        for path in (_REPO_ROOT / "persona").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                        getattr(node.func, "id", "") == "materials_from_extracts"):
                    calls.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
        self.assertTrue(calls,
                        "产品代码里没有 materials_from_extracts() 调用点：G4 回来了")

    # ------------------------------------------------------- 动态：单人物
    def test_material_flow_is_non_empty_and_gate_valid(self):
        self.assertTrue(self.diag["ok"], self.diag)
        self.assertGreaterEqual(self.diag["materials_in_flow"], 1,
                                "抓取正文必须进入材料流（修复前只有检索摘要）")
        self.assertGreaterEqual(self.diag["materials_gate_valid"], 1,
                                "材料流里必须有带门禁结论（VALID）的材料")
        self.assertGreaterEqual(self.diag["materials_bound"], 1,
                                "材料必须绑定已登记来源，否则可追溯闸门会拒收")

    def test_orchestrator_reaches_model_exactly_once(self):
        self.assertEqual(self.diag["stub_chat_calls"], 1,
                         "G4 修复后编排器路径必须调用到模型（修复前恒为 0）")

    def test_evidence_is_non_empty_and_stage_executed(self):
        self.assertGreaterEqual(self.diag["evidence_count"], 1)
        self.assertEqual(self.diag["stage_status"][STAGE_EVIDENCE], STEP_EXECUTED)

    def test_single_person_partial_reason_shrinks_to_relationship_stages(self):
        """单人物仍 PARTIAL，但原因**只剩**关系/对话按语义跳过。"""
        self.assertEqual(self.diag["status"], RUN_PARTIAL)
        self.assertEqual(self.diag["stage_status"][STAGE_RESEARCH], STEP_EXECUTED)
        self.assertEqual(self.diag["stage_status"][STAGE_EVIDENCE], STEP_EXECUTED)
        self.assertEqual(self.diag["stage_status"][STAGE_PERSONA], STEP_EXECUTED)
        self.assertEqual(self.diag["stage_status"][STAGE_RELATIONSHIP], STEP_SKIPPED)
        self.assertEqual(self.diag["stage_status"][STAGE_DIALOGUE], STEP_SKIPPED)

    def test_no_material_undeclared_rejection_remains(self):
        """修复后不该再有"每份材料都判未声明门禁结论"的拒收。"""
        self.assertNotIn("MATERIAL_UNDECLARED", self.diag["warning_codes"])
        self.assertNotIn("MATERIAL_NOT_GATE_PASSED", self.diag["warning_codes"])

    def test_superseded_summary_is_reported_not_silent(self):
        """同来源的检索摘要被抓取正文取代时必须**说出来**，不能静默丢。"""
        self.assertIn("MATERIAL_SUPERSEDED", self.diag["warning_codes"])

    # ------------------------------------------------------- 动态：双人物
    def test_two_person_run_reaches_succeeded(self):
        """参与者 ≥ 2 → 五阶段全 EXECUTED → 整体状态**不再是 PARTIAL**。"""
        self.assertTrue(self.diag_two["ok"], self.diag_two)
        self.assertEqual(self.diag_two["status"], RUN_SUCCEEDED)
        for stage, status in self.diag_two["stage_status"].items():
            self.assertEqual(status, STEP_EXECUTED, f"{stage} 未执行：{status}")

    def test_two_person_covers_both_entities(self):
        self.assertGreaterEqual(self.diag_two["evidence_count"], 2)
        self.assertGreaterEqual(self.diag_two["materials_in_flow"], 2,
                                "两个目标各自都要有材料进入材料流")
        self.assertEqual(self.diag_two["materials_bound"],
                         self.diag_two["materials_in_flow"],
                         "双人物下每份材料都必须绑定来源")


class TestG5WarningsPropagation(unittest.TestCase):
    """G5 回归锁：抽取器的 `outcome.warnings` 必须透传到任务告警。

    修复前 `_register_candidates` 把 `outcome.warnings` 整个丢掉，材料级拒收
    被笼统标成 `CLAIM_REJECTED`：运维看不到"材料没过门禁"，只会误判成
    "模型抽不出东西"。
    """

    def setUp(self) -> None:
        self.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        self.settings = settings_for()
        self.stack = runner.Stack(self.settings, ExternalUsageLedger(), self.out)
        self.orch = self.stack.orchestrator()

    def tearDown(self) -> None:
        self.stack.teardown()

    @staticmethod
    def _codes(step) -> list[str]:
        return [str(w.code) for w in step.warnings]

    def test_extractor_warnings_are_propagated_verbatim(self):
        outcome = ExtractionOutcome(
            method="MODEL",
            warnings=[WarningNote(
                code=WARNING_MATERIAL_QUALITY_UNDECLARED,
                message="1 份材料未声明正文质量门禁结论（按检索摘要处理）")])
        step = self.orch._register_candidates(self.stack.ctx, outcome, [])
        self.assertFalse(is_err(step), step)
        self.assertIn(WARNING_MATERIAL_QUALITY_UNDECLARED, self._codes(step))

    def test_material_level_rejection_uses_its_own_reason_as_code(self):
        """材料级拒收（没有 claim）→ 告警码就是该原因本身。"""
        outcome = ExtractionOutcome(
            method="MODEL",
            rejected=[RejectedClaim(
                reason=REJECT_MATERIAL_UNDECLARED,
                message="材料「合成样张」没有声明正文质量门禁结论：拒收")])
        step = self.orch._register_candidates(self.stack.ctx, outcome, [])
        codes = self._codes(step)
        self.assertIn(REJECT_MATERIAL_UNDECLARED, codes,
                      "MATERIAL_UNDECLARED 必须是可筛选的告警码")
        self.assertNotIn("CLAIM_REJECTED", codes,
                         "材料级拒收不得再被笼统标成 CLAIM_REJECTED")

    def test_claim_level_rejection_keeps_claim_rejected_code(self):
        """claim 级拒收（无效摘录）保持 `CLAIM_REJECTED` —— 既有契约不变。"""
        outcome = ExtractionOutcome(
            method="MODEL",
            rejected=[RejectedClaim(
                reason=REJECT_QUOTE_NOT_IN_MATERIAL,
                message="第 0 条候选未通过 QUOTE_NOT_IN_MATERIAL 校验",
                key="bad", claim="这句摘录不在材料里")])
        step = self.orch._register_candidates(self.stack.ctx, outcome, [])
        self.assertIn("CLAIM_REJECTED", self._codes(step))

    def test_material_rejection_reaches_task_level_warnings(self):
        """端到端：材料级拒收原因必须出现在**编排器路径**的任务告警里。"""
        stack = runner.Stack(self.settings, ExternalUsageLedger(), self.out,
                             source_provider=runner.StubSearchExtractProvider())
        try:
            # 桩模型返回形状合法但**门禁未过**的材料：把材料级拒收逼出来
            stack.ensure_entity("阿雾")
            orch = stack.orchestrator()
            rejected_material = runner.SourceMaterial(
                source_id="src_g5", title="[合成样张·虚构] 不合格材料",
                url=runner.E2E_URL, canonical_key=runner.E2E_URL,
                snippet=runner.E2E_TEXT, quality_status="LOW_QUALITY")
            extractor = stack.use_real_extractor(
                runner.StubChatProvider('{"claims": []}'))
            outcome = unwrap(extractor.extract(
                stack.ctx, [rejected_material], targets=[stack.target]))
            step = orch._register_candidates(stack.ctx, outcome, ["src_g5"])
            codes = self._codes(step)
            self.assertIn(REJECT_MATERIAL_NOT_GATE_PASSED, codes)
        finally:
            stack.teardown()


class TestOrchestratorRealRunWiring(unittest.TestCase):
    """接线轮回归锁：`--run` 默认走**编排器路径**，且该路径真的端到端可跑。

    "可跑"用**桩模型注入**证明（`run_orchestrator_real(provider=...)`）：同一条
    产品代码链路（研究 → 抽取 → 证据登记 → 门禁 → 人格档案）在**零成本、零 socket**
    下跑通，产物再交给独立复算器逐条核对。真实运行只是把注入的桩换成真 DeepSeek
    —— 接线本身因此被离线锁住，而不是"只能靠花钱才发现接错"。
    """

    def test_default_path_is_orchestrator(self):
        args = runner.build_parser().parse_args([])
        self.assertEqual(args.path, "orchestrator",
                         "--run 默认必须走编排器路径（产品代码全链）")
        self.assertFalse(args.run, "解析默认值不得触发真实执行")
        self.assertFalse(args.dry)

    def test_explicit_script_path_still_available(self):
        args = runner.build_parser().parse_args(["--run", "--path=script"])
        self.assertEqual(args.path, "script", "脚本侧路径保留为对照通道")
        self.assertTrue(args.run)

    def test_recording_extractor_delegates_and_captures(self):
        """代理必须**原样透传**调用，并留下最后一次 outcome。"""

        class _Inner:
            def __init__(self) -> None:
                self.calls = 0
                self.saw_build = 0

            def extract(self, ctx, materials, *, targets):
                self.calls += 1
                return Ok(ExtractionOutcome(method="MODEL"))

            def build_request(self, *args, **kwargs):
                self.saw_build += 1
                return "REQ"

        inner = _Inner()
        proxy = runner.RecordingExtractor(inner)
        self.assertIsNone(proxy.last_outcome, "没调用过就不得有 outcome")
        result = proxy.extract(object(), [], targets=[])
        self.assertEqual(inner.calls, 1)
        self.assertEqual(proxy.calls, 1)
        self.assertIs(proxy.last_outcome, result.value)
        self.assertEqual(proxy.build_request(), "REQ", "其余方法必须透传")
        self.assertEqual(inner.saw_build, 1)

    def test_orchestrator_real_run_end_to_end_offline(self):
        """编排器真实运行函数在**桩模型 + 断网护栏**下跑通，产物过独立复算器。"""
        settings = settings_for()
        ledger = ExternalUsageLedger()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []

        def expect(cond: bool, desc: str) -> None:
            if not cond:
                problems.append(desc)

        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            # 实体 ID 由存储生成 → 载荷只能在登记之后构造（与 diag 同一手法）
            stub.payload = runner.orchestrator_payload({"阿雾": entity_id})

        with tempfile.TemporaryDirectory(prefix="p45a-wiring-") as td:
            dump = Path(td) / "artifact.json"
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems, expect=expect, ledger=ledger,
                    limits=runner.model_limits_from_settings(settings),
                    dump_path=str(dump), provider=stub, on_targets=on_targets)
            self.assertTrue(dump.exists(), "接线路径必须产出可复算的产物")
            artifact = json.loads(dump.read_text(encoding="utf-8"))
            raw_path = dump.with_name(dump.stem + ".raw.json")
            self.assertTrue(raw_path.exists(),
                            "原始响应必须单独落盘（<dump>.raw.json）")
            raw_doc = json.loads(raw_path.read_text(encoding="utf-8"))

        self.assertEqual(problems, [], "接线路径不得有任何失败项")
        self.assertEqual(out.leak, False, "输出里不得出现密钥")

        # 原始收发产物：注入的桩 Provider 没有 `_urlopen` → 记录为空但文件必须存在
        self.assertEqual(raw_doc["schema"], "p45a-raw-exchange/1")
        self.assertEqual(raw_doc["exchanges"], [],
                         "桩 Provider 无网络出口 → 不得凭空编造 HTTP 记录")
        self.assertEqual(artifact["raw_exchange_path"], str(raw_path))
        self.assertNotIn(SECRET_PLACEHOLDER, json.dumps(raw_doc, ensure_ascii=False),
                         "原始收发产物里不得出现密钥")

        joined = "\n".join(out.lines)
        self.assertIn("[7b] 幂等重跑", joined, "真实运行必须包含幂等重跑步骤")
        self.assertIn("发送层幂等守卫", joined, "必须报出拦住重跑的那道闸门")
        self.assertIn("BUDGET_EXCEEDED", joined, "预算探针必须被次数闸门拦下")

        self.assertEqual(artifact["mode"], "orchestrator-path",
                         "产物必须标明来自编排器路径")
        self.assertEqual(artifact["calls"]["real_chat_calls"], 1)
        self.assertEqual(len(artifact["materials"]), 1)
        self.assertEqual(artifact["materials"][0]["quality_status"], "VALID")
        self.assertTrue(artifact["materials"][0]["source_id"])
        self.assertGreaterEqual(len(artifact["evidence_ids"]), 1)
        self.assertEqual(len(artifact["extraction"]["claims"]), 5)
        self.assertEqual(artifact["extraction"]["rejected"], [])

        rep = verifier.verify(artifact)
        passed, total = rep.summary()
        failed = [name for name, ok, _ in rep.rows if not ok]
        self.assertEqual(passed, total,
                         f"独立复算器未全绿，失败项={failed}")

    def test_orchestrator_stage_table_is_single_person_honest(self):
        """单人物下关系/对话阶段必须如实 SKIPPED，桩对话 Provider 零调用。"""
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []
        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            stub.payload = runner.orchestrator_payload({"阿雾": entity_id})

        with tempfile.TemporaryDirectory(prefix="p45a-wiring-"):
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems,
                    expect=lambda cond, desc: problems.append(desc) if not cond else None,
                    ledger=ExternalUsageLedger(),
                    limits=runner.model_limits_from_settings(settings),
                    provider=stub, on_targets=on_targets)

        self.assertEqual(problems, [])
        joined = "\n".join(out.lines)
        self.assertIn("关系档案", joined)
        self.assertIn("模拟对话", joined)
        self.assertIn("SKIPPED", joined)
        # 只锁"整体状态"那一行：账本里也有其它 outcome=SUCCEEDED 字样，不能全串搜
        status_lines = [ln for ln in out.lines if ln.startswith("  整体状态")]
        self.assertEqual(len(status_lines), 1, f"理应只有一行整体状态：{status_lines}")
        self.assertIn("PARTIAL", status_lines[0])
        self.assertNotIn("SUCCEEDED", status_lines[0],
                         "单人物不得报 SUCCEEDED（关系/对话按语义跳过）")


class TestKnownGapStabilityNotWired(unittest.TestCase):
    """**G6 回归锁（已接线）**：`stability` 链路曾经断在中间。

    历史事实（修复前）：
      1. 抽取器**刻意不解析**模型自报的 `stability`（`CandidateClaim.stability`
         默认 `UNKNOWN`，docstring 明写"不得编造"）——模型无权宣布自己的说法稳定；
      2. 框架自带的确定性判定器 `Evidence.judge_stability()`（1 条=SINGLE、
         ≥2=REPEATED、≥3 个不同来源=PATTERN）在生产代码里**零调用点**。

    结果：TRAIT 维度证据的 stability 恒为 `UNKNOWN` → 按 AC-7 全部落
    `single_events` → `traits` 在真实链路上**结构性为空**。

    **现在已接线**（`business._assess_stabilities`）：本类里的
    `test_judge_stability_has_a_product_call_site` 从"断言缺口存在"
    **反转**为"断言接线存在"，防止将来有人把接线删掉而没人发现。
    抽取器仍然不解析模型自报的 stability（那一条**不变**，是 PF-V2 的要求）。
    """

    @staticmethod
    def _judge_stability_callers() -> list[str]:
        import ast

        found: list[str] = []
        for path in (_REPO_ROOT / "persona").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and getattr(node.func, "attr", "") == "judge_stability"):
                    found.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
        return found

    def test_judge_stability_has_a_product_call_site(self):
        """G6 已接线：判定器**必须**有产品调用点，且落在编排器里。

        反转自修复前的 `test_judge_stability_has_no_product_call_site`。
        """
        calls = self._judge_stability_callers()
        self.assertTrue(
            calls,
            "产品代码里找不到 judge_stability() 调用点——G6 的接线被删掉了？")
        self.assertTrue(
            any("orchestrator" in c for c in calls),
            f"判定器应在编排器里被调用（它是 L5 协调层），实际：{calls}")

    def test_model_reported_stability_is_not_parsed(self):
        """抽取器不解析模型自报的 stability：无论载荷写什么，候选一律 UNKNOWN。"""
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        stack = runner.Stack(settings, ExternalUsageLedger(), out)
        try:
            entity_id = stack.ensure_entity("阿雾")
            ro = unwrap(stack.research.run(stack.ctx, [stack.target]))
            materials, _ = runner.materials_from_research(stack, ro)
            # 载荷里 c1/c2 写 REPEATED、c4 写 PATTERN、c5 写 SINGLE
            stub = runner.StubChatProvider(
                payload_for(entity_id, runner.E2E_URL))
            extractor = stack.use_real_extractor(stub)
            outcome = unwrap(extractor.extract(
                stack.ctx, materials, targets=[stack.target]))
            self.assertEqual(len(outcome.claims), 5)
            for c in outcome.claims:
                self.assertEqual(
                    str(c.stability), "UNKNOWN",
                    f"{c.key} 的 stability 被解析成了 {c.stability}"
                    "（G6 可能已接线：请更新本测试）")
        finally:
            stack.teardown()

    def test_unknown_stability_trait_lands_in_single_events_never_traits(self):
        """AC-7 的保守后果：TRAIT + UNKNOWN → `single_events`，`traits` 为空。"""
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []
        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            stub.payload = runner.orchestrator_payload({"阿雾": entity_id})

        with tempfile.TemporaryDirectory(prefix="p45a-wiring-") as td:
            dump = Path(td) / "artifact.json"
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems,
                    expect=lambda cond, desc: problems.append(desc) if not cond else None,
                    ledger=ExternalUsageLedger(),
                    limits=runner.model_limits_from_settings(settings),
                    dump_path=str(dump), provider=stub, on_targets=on_targets)
            artifact = json.loads(dump.read_text(encoding="utf-8"))

        self.assertEqual(problems, [])
        persona = artifact["persona"]
        self.assertEqual(persona["traits"], [],
                         "当前接线下 traits 必然为空（G6：stability 无人判定）")
        self.assertTrue(persona["single_events"],
                        "TRAIT 维度证据必须落 single_events，不得凭空丢")
        for item in persona["single_events"]:
            self.assertIn(str(item["stability"]), ("SINGLE", "UNKNOWN"))
        # AC-7 是**保守满足**：单次事件绝不进 traits
        trait_texts = {str(i["text"]) for i in persona["traits"]}
        single_texts = {str(i["text"]) for i in persona["single_events"]}
        self.assertFalse(trait_texts & single_texts, "单次事件不得出现在 traits")


class TestRawCaptureAndIdempotentRerun(unittest.TestCase):
    """冒烟轮新增两件事的离线锁：原始响应捕获 + 幂等重跑（都 0 真实调用）。"""

    def test_raw_capture_records_body_but_never_header_values(self):
        """`RawResponseCapture` 必须留下响应体，但**只记请求头名**。

        请求头里 `Authorization` 的值就是密钥 —— 记值等于把密钥写进产物。
        """
        body = b'{"id":"chatcmpl-test","choices":[{"message":{"content":"{}"}}]}'

        class _Resp:
            status = 200

            def read(self, *a, **k):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        class _Req:
            full_url = "https://api.deepseek.com/chat/completions"
            method = "POST"
            data = b'{"model":"deepseek-flash"}'
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {SECRET_PLACEHOLDER}"}

        seen: dict = {}

        def fake_urlopen(request, timeout=None):
            seen["timeout"] = timeout
            return _Resp()

        capture = runner.RawResponseCapture(fake_urlopen)
        with capture(_Req(), timeout=30) as response:
            self.assertEqual(response.read(), body)

        self.assertEqual(len(capture.exchanges), 1)
        exchange = capture.exchanges[0]
        self.assertEqual(exchange["status"], 200)
        self.assertEqual(exchange["response_bytes"], len(body))
        self.assertEqual(exchange["raw_response"], body.decode("utf-8"))
        self.assertEqual(exchange["request_body_bytes"], len(_Req.data))
        self.assertIn("authorization", exchange["request_header_names"])
        # 关键：名字记了，值**没记**
        blob = json.dumps(capture.exchanges, ensure_ascii=False)
        self.assertNotIn(SECRET_PLACEHOLDER, blob,
                         "请求头值（密钥）绝不进原始收发记录")

    def test_rerun_never_adds_a_second_real_call(self):
        """同一条链重跑：真实调用次数不变，且探针被 `BUDGET_EXCEEDED` 拦下。"""
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []
        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            stub.payload = runner.orchestrator_payload({"阿雾": entity_id})

        with tempfile.TemporaryDirectory(prefix="p45a-rerun-"):
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems,
                    expect=lambda cond, desc: problems.append(desc) if not cond else None,
                    ledger=ExternalUsageLedger(),
                    limits=runner.model_limits_from_settings(settings),
                    provider=stub, on_targets=on_targets)

        self.assertEqual(problems, [], "幂等重跑不得产生失败项")
        self.assertEqual(stub.chat_calls, 1, "整段流程只允许 1 次真实调用")

        joined = "\n".join(out.lines)
        self.assertIn("原样重跑 orch.run", joined)
        self.assertIn("真实模型调用次数 = 1（重跑前 1）", joined,
                      "重跑后真实调用次数不得变化")
        self.assertIn("BUDGET_EXCEEDED", joined, "预算闸门必须报 BUDGET_EXCEEDED")
        self.assertIn("PROFILE_DEGRADED_SKIPPED", joined,
                      "G7：重跑的空产出必须被拦下并给出专属告警码")


# ==========================================================================
# G7：修订"空产出"不得降级覆盖有内容的旧版（A 级写风险）
# ==========================================================================
class TestG7DegradedRevisionSkipped(unittest.TestCase):
    """P4.5-B 审查实测的写风险：重跑按"本次证据"重建档案，本次没有证据 ⇒ 空产出。

    `revise` 原有的"内容完全一致 → 拒绝空转"挡不住这件事：旧版有 1 条、新版 0 条，
    两者**不相等**，于是写出空的新版本，并把旧版的 `superseded_by` 指向它；默认读取
    取 version 最大者 ⇒ 读到空档案（旧版行还在库里，但已事实降级）。

    修复口径：不写新版本、不动旧版归档指针、返回旧版引用 + 专属告警。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run_chain()          # 真实链路产出一份**有内容**的 v1 人格档案

    @classmethod
    def tearDownClass(cls) -> None:
        cls.r.teardown()

    def test_v1_has_content_and_no_archive_pointer(self):
        view = unwrap(self.r.stack.persona.get_persona(
            self.r.stack.ctx, self.r.entity_id))
        self.assertEqual(view.version, 1)
        self.assertEqual(view.status, ProfileStatus.OK.value)
        self.assertIsNone(view.superseded_by, "v1 不该有归档指针")
        self.assertGreaterEqual(len(view.habits), 1, "v1 必须有内容（否则本测试无意义）")

    def test_empty_revise_returns_old_version_with_warning(self):
        ctx = self.r.stack.ctx
        empty = BusinessOrchestrator._build_persona_draft(
            self.r.entity_id, [], "FICTIONAL_CHARACTER")
        empty.change_reason = "测试：本次没有可用证据（模拟重跑）"

        result = self.r.stack.persona.revise(ctx, self.r.entity_id, empty)
        self.assertFalse(is_err(result), "空产出应被**拦下并告警**，而不是报错")
        self.assertEqual(result.value.version, 1, "版本号不得前进")
        self.assertIn(WARNING_PROFILE_DEGRADED_SKIPPED,
                      [w.code for w in result.warnings],
                      "必须给出可筛选的专属告警码")

        view = unwrap(self.r.stack.persona.get_persona(ctx, self.r.entity_id))
        self.assertEqual(view.version, 1, "默认读取仍是最新有内容的版本")
        self.assertIsNone(view.superseded_by, "旧版不得被打上归档指针")
        self.assertGreaterEqual(len(view.habits), 1, "旧版内容必须原样保留")
        self.assertTrue(is_err(self.r.stack.persona.get_persona(
            ctx, self.r.entity_id, version=2)), "不得存在 v2")

    def test_orchestrator_propagates_degraded_warning(self):
        """步骤级必须看得到这件事——否则任务视图里"档案没更新"无从解释。"""
        ctx = self.r.stack.ctx
        empty = BusinessOrchestrator._build_persona_draft(
            self.r.entity_id, [], "FICTIONAL_CHARACTER")
        notes: list[WarningNote] = []
        written = self.r.stack.orchestrator()._write_persona(
            ctx, self.r.entity_id, empty, notes)
        self.assertFalse(is_err(written))
        profile_id, already = written
        self.assertTrue(already, "没产生新版本 ⇒ already_current 必须为 True")
        self.assertIn(WARNING_PROFILE_DEGRADED_SKIPPED,
                      [n.code for n in notes], "模块告警必须透传到步骤告警")

    def test_guard_does_not_block_when_old_version_is_empty(self):
        """护栏必须**窄**：旧版本来就没内容时，正常修订不应被误拦。"""
        settings = settings_for()
        stack = runner.Stack(settings, ExternalUsageLedger(),
                             runner.Out(SECRET_PLACEHOLDER, quiet=True))
        try:
            entity_id = stack.ensure_entity("阿雾")
            first = runner.BusinessOrchestrator._build_persona_draft(
                entity_id, [], "FICTIONAL_CHARACTER")
            first.change_reason = "v1：没有证据"
            created = stack.persona.create_profile(stack.ctx, first)
            self.assertFalse(is_err(created))
            self.assertEqual(created.value.version, 1)

            second = runner.BusinessOrchestrator._build_persona_draft(
                entity_id, [], "FICTIONAL_CHARACTER")
            second.open_questions = ["换一条缺口说明"]
            second.change_reason = "v2：缺口说明更新"
            revised = stack.persona.revise(stack.ctx, entity_id, second)
            self.assertFalse(is_err(revised), "旧版无内容时不应被 G7 护栏拦住")
            self.assertEqual(revised.value.version, 2, "正常修订必须照常产生新版本")
        finally:
            stack.teardown()

    def test_empty_relationship_revise_keeps_content(self):
        """关系档案同口径：v1 `shared=1` 不得被 v2 `shared=0` 顶掉。"""
        settings = settings_for()
        stack = runner.Stack(settings, ExternalUsageLedger(),
                             runner.Out(SECRET_PLACEHOLDER, quiet=True))
        try:
            participants = stack.ensure_targets(["阿雾", "阿灯"])
            ids = [t.entity_id for t in participants]
            content = RelationshipDraft(
                participant_entity_ids=ids, cardinality=Cardinality.PAIR,
                perspectives={p: PerspectiveBlock(entity_id=p) for p in ids},
                shared_experiences=[SharedExperienceItem(
                    item_id="pi_fixture", text="两人在灯会当夜共同值守堤坝",
                    confidence=0.0, evidence_refs=["ev_fixture"],
                    participant_entity_ids=list(ids))],
                # PF-V5：虚构参与者必须逐个声明适用版本（就是参与者自身）
                applicable_variant_ids=list(ids),
                status=ProfileStatus.OK,
                change_reason="fixture：构造一份**有内容**的 v1")
            # 用服务自己的写入方法落一条 v1（只跳过"证据存在性"，不绕过写路径）
            created = stack.relationship._write_relationship(
                stack.ctx, content, version=1)
            self.assertFalse(is_err(created))
            self.assertEqual(created.value.version, 1)

            empty = RelationshipDraft(
                participant_entity_ids=ids, cardinality=Cardinality.PAIR,
                perspectives={p: PerspectiveBlock(entity_id=p) for p in ids},
                open_questions=["互动模式待补"],
                applicable_variant_ids=list(ids),
                status=ProfileStatus.INSUFFICIENT_EVIDENCE,
                change_reason="测试：本次没有 RELATION 证据")
            revised = stack.relationship.revise(stack.ctx, ids, empty)
            self.assertFalse(is_err(revised), "空产出应被拦下并告警，而不是报错")
            self.assertEqual(revised.value.version, 1, "版本号不得前进")
            self.assertIn(WARNING_RELATIONSHIP_DEGRADED_SKIPPED,
                          [w.code for w in revised.warnings])

            view = unwrap(stack.relationship.get_relationship(stack.ctx, ids))
            self.assertEqual(view.version, 1)
            self.assertEqual(len(view.shared_experiences), 1, "有内容的旧版必须保留")
            self.assertIsNone(view.superseded_by)
        finally:
            stack.teardown()


# ==========================================================================
# G8：双人物真实运行入口（`--names`）
# ==========================================================================
class TestG8DualPersonEntry(unittest.TestCase):
    """`--run --path=orchestrator` 必须支持双人物，且**仍然只发 1 次真实请求**。"""

    def test_names_default_is_single_person(self):
        args = runner.build_parser().parse_args([])
        self.assertEqual(args.names, runner.E2E_TARGET.display_name,
                         "不写 --names 时行为与旧版逐字一致（阿雾）")

    def test_parse_names_splits_trims_and_rejects_empty(self):
        self.assertEqual(runner.parse_names("阿雾, 阿灯"), ["阿雾", "阿灯"])
        self.assertEqual(runner.parse_names("阿雾"), ["阿雾"])
        with self.assertRaises(ValueError):
            runner.parse_names(" , ")

    def test_dual_person_real_run_offline(self):
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []
        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            # 双人物：两个实体 ID 都在登记之后才有 → 从 stack.targets 取全
            stub.payload = runner.orchestrator_payload(
                {t.display_name: t.entity_id for t in stack.targets})

        with tempfile.TemporaryDirectory(prefix="p45a-dual-") as td:
            dump = Path(td) / "artifact.json"
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems,
                    expect=lambda cond, desc: problems.append(desc) if not cond else None,
                    ledger=ExternalUsageLedger(),
                    limits=runner.model_limits_from_settings(settings),
                    dump_path=str(dump), provider=stub, on_targets=on_targets,
                    names=["阿雾", "阿灯"])
            artifact = json.loads(dump.read_text(encoding="utf-8"))

        self.assertEqual(problems, [], f"双人物接线不得有失败项：{problems}")
        self.assertEqual(stub.chat_calls, 1, "双人物仍只允许 1 次真实调用")
        self.assertEqual(artifact["calls"]["real_chat_calls"], 1)
        # C 轮：阿雾带话题 → 多一条话题查询 → 额外取回"互动页"，共 3 份
        # （前两份是两个目标各一份，第三份是双人合记材料）。
        self.assertEqual(len(artifact["materials"]), 3,
                         "两个目标各一份材料 + 一份双人互动页")
        self.assertGreaterEqual(len(artifact["evidence_ids"]), 2)

        joined = "\n".join(out.lines)
        self.assertIn("双人物", joined)
        self.assertIn("SUCCEEDED", joined, "双人物五阶段全执行 → SUCCEEDED")
        self.assertIn("关系档案", joined)
        dialogue_row = next(ln for ln in out.lines if "模拟对话" in ln)
        self.assertIn("EXECUTED", dialogue_row, "双人物对话阶段必须真的执行")
        self.assertIn("生成 1 轮模拟对话", dialogue_row)

    def test_single_person_path_unchanged(self):
        """不传 names 时不得改变既有行为（单人物 → PARTIAL，对话零调用）。"""
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []
        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            stub.payload = runner.orchestrator_payload({"阿雾": entity_id})

        with tempfile.TemporaryDirectory(prefix="p45a-single-"):
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems,
                    expect=lambda cond, desc: problems.append(desc) if not cond else None,
                    ledger=ExternalUsageLedger(),
                    limits=runner.model_limits_from_settings(settings),
                    provider=stub, on_targets=on_targets)

        self.assertEqual(problems, [])
        joined = "\n".join(out.lines)
        self.assertIn("单人物", joined)
        dialogue_row = next(ln for ln in out.lines if "模拟对话" in ln)
        self.assertIn("SKIPPED", dialogue_row, "单人物对话阶段必须如实跳过")


# ==========================================================================
# G10：`--dump` 落点必须在系统临时目录（根因修复，不是改断言）
# ==========================================================================
class TestG10DumpPath(unittest.TestCase):
    """上一轮 `--dump var/p45a_last_run.json` 在仓库里造出 `var/`，把
    `test_all_data_in_temp_dir_not_user_data` 弄红。断言是对的，错的是落点。"""

    REPO = Path(__file__).resolve().parents[1]

    def test_default_dump_is_in_system_temp(self):
        path = runner.resolve_dump_path("")
        self.assertEqual(path.parent, Path(tempfile.gettempdir()))
        self.assertEqual(path.name, "p45a_last_run.json")

    def test_relative_dump_never_lands_in_repo(self):
        path = runner.resolve_dump_path("var/p45a_last_run.json")
        self.assertTrue(path.is_absolute(), "相对路径必须被解析成绝对路径")
        self.assertEqual(path.parent.parent, Path(tempfile.gettempdir()))
        self.assertNotIn(self.REPO, path.resolve().parents,
                         "产物**绝不允许**落进仓库")

    def test_absolute_dump_is_honored(self):
        with tempfile.TemporaryDirectory(prefix="p45a-dump-") as td:
            explicit = Path(td) / "x.json"
            self.assertEqual(runner.resolve_dump_path(str(explicit)), explicit)

    def test_cli_default_never_points_into_repo(self):
        for argv in ([], ["--run"], ["--run", "--path=orchestrator"]):
            args = runner.build_parser().parse_args(argv)
            path = runner.resolve_dump_path(args.dump)
            self.assertNotIn(self.REPO, path.resolve().parents,
                             f"{argv} 的默认落点不得在仓库内")


class TestG9RelationEvidenceClassification(unittest.TestCase):
    """G9 回归锁：没被档案消费的 RELATION 证据必须**按原因分类**报出来。

    修复前的三个毛病：
      1. `covered < 2` 直接 `continue`，一条痕迹都不留；
      2. "有证据但主体不足"与"根本没有证据"共用一句
         `NO_RELATION_EVIDENCE`，运维无法分辨该补材料还是该查抽取；
      3. 单人物时阶段 SKIPPED，而已登记的 RELATION 证据**没有任何消费者**
         （人物档案没有 RELATION 维度），那条证据等于凭空消失。
    """

    def setUp(self) -> None:
        self.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        self.settings = settings_for()
        self.stack = runner.Stack(self.settings, ExternalUsageLedger(), self.out)
        self.orch = self.stack.orchestrator()
        self._source_id = ""
        self._targets: list = []

    def tearDown(self) -> None:
        self.stack.teardown()

    # ---------------------------------------------------------------- 工具

    def _source(self) -> str:
        if not self._source_id:
            ref = unwrap(self.stack.evidence.register_source(
                self.stack.ctx,
                SourceDraft(
                    title="合成互动页（G9 夹具）",
                    canonical_key="https://example.test/g9-relation",
                    url="https://example.test/g9-relation")))
            self._source_id = str(ref.id)
        return self._source_id

    def _evidence(self, text: str, subjects: list[str],
                  dimension: str = "RELATION") -> str:
        """登记一条**归纳型**证据（不带摘录，绕开 EV-V2 的原话核验）。"""
        ref = unwrap(self.stack.evidence.add_evidence(
            self.stack.ctx,
            EvidenceDraft(
                claim=text, tier="PROFILE_BACKGROUND", dimension=dimension,
                subject_entity_ids=subjects, source_id=self._source(),
                extraction_confidence=0.9)))
        return str(ref.id)

    @staticmethod
    def _codes(step) -> list[str]:
        return [str(w.code) for w in step.warnings]

    def _run_relationship(self, evidence_ids: list[str], targets=None):
        return self.orch._step_relationship(
            self.stack.ctx,
            self.stack.request(targets=targets or self._targets
                               or [self.stack.target]),
            {"evidence_ids": evidence_ids})

    def _pair(self) -> tuple[str, str]:
        """登记阿雾 / 阿灯两名参与者，并把 targets 记下来供请求用。"""
        self._targets = self.stack.ensure_targets(["阿雾", "阿灯"])
        return self._targets[0].entity_id, self._targets[1].entity_id

    # ---------------------------------------------------------------- 用例

    def test_single_subject_evidence_is_reported_not_silently_dropped(self):
        """主体只含 1 名参与者 ⇒ 分类告警，且带数量。"""
        a, b = self._pair()
        ev = self._evidence("阿雾独自在塔顶守了一夜", [a])
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        codes = self._codes(step)
        self.assertIn(runner_business.WARNING_RELATION_SUBJECT_INSUFFICIENT, codes)
        message = " ".join(str(w.message) for w in step.warnings
                           if str(w.code) == runner_business.WARNING_RELATION_SUBJECT_INSUFFICIENT)
        self.assertIn("1 条", message, "告警必须带数量，不能只说'有证据没用上'")
        self.assertIn(ev, message, "告警必须带证据 ID，否则无法定位")

    def test_subject_insufficient_does_not_masquerade_as_no_evidence(self):
        """有证据、只是主体不足时，**不得**再说"没有 RELATION 证据"。"""
        a, b = self._pair()
        ev = self._evidence("阿灯只是路过雾港", [b])
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        codes = self._codes(step)
        self.assertIn(runner_business.WARNING_RELATION_SUBJECT_INSUFFICIENT, codes)
        self.assertNotIn(runner_business.WARNING_NO_RELATION_EVIDENCE, codes,
                         "明明有 RELATION 证据，不能说'没有可用的 RELATION 证据'")

    def test_no_relation_evidence_still_reports_the_classic_code(self):
        """真的一条 RELATION 证据都没有 ⇒ 仍报 NO_RELATION_EVIDENCE。"""
        a, b = self._pair()
        ev = self._evidence("阿雾在雾港长大", [a], dimension="BACKGROUND")
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        codes = self._codes(step)
        self.assertIn(runner_business.WARNING_NO_RELATION_EVIDENCE, codes)
        self.assertNotIn(runner_business.WARNING_RELATION_SUBJECT_INSUFFICIENT, codes)

    def test_both_subjects_are_consumed_without_classification_noise(self):
        """覆盖 2 名参与者的证据正常进档案，不产生"未消费"告警。"""
        a, b = self._pair()
        ev = self._evidence("两人在灯会当夜共同值守堤坝", [a, b])
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        codes = self._codes(step)
        self.assertNotIn(runner_business.WARNING_RELATION_SUBJECT_INSUFFICIENT, codes)
        self.assertNotIn(runner_business.WARNING_RELATION_EVIDENCE_UNUSED_OTHER, codes)

    def test_single_person_reports_suspended_relation_evidence(self):
        """单人物：阶段 SKIPPED，但已登记的 RELATION 证据必须被点名。"""
        a = self.stack.ensure_entity("阿雾")
        ev = self._evidence("阿雾曾与阿灯约定在灯塔见面", [a])
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        self.assertEqual(step.status, STEP_SKIPPED)
        codes = self._codes(step)
        self.assertIn(runner_business.WARNING_RELATION_STAGE_SKIPPED_WITH_EVIDENCE, codes)
        message = " ".join(str(w.message) for w in step.warnings)
        self.assertIn("1 条", message)
        self.assertIn(ev, message)

    def test_single_person_without_relation_evidence_stays_quiet(self):
        """单人物且没有 RELATION 证据 ⇒ 不报悬空告警（避免噪声）。"""
        a = self.stack.ensure_entity("阿雾")
        ev = self._evidence("阿雾习惯在夜里检查灯芯", [a], dimension="HABIT")
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        self.assertEqual(step.status, STEP_SKIPPED)
        self.assertNotIn(runner_business.WARNING_RELATION_STAGE_SKIPPED_WITH_EVIDENCE,
                         self._codes(step))

    def test_analyzer_path_propagates_its_own_classification(self):
        """注入分析器时，它内部的"主体不足"计数必须透传到步骤告警（G5 同型）。"""
        self.orch.deps = replace(
            self.orch.deps, relationship_analyzer=RuleRelationshipAnalyzer())
        a, b = self._pair()
        ev_bad = self._evidence("阿雾在灯塔里独自醒来", [a])
        ev_good = self._evidence("两人在堤坝上并肩守到天亮", [a, b])
        step = self._run_relationship([ev_bad, ev_good])
        self.assertFalse(is_err(step), step)
        codes = self._codes(step)
        self.assertIn("RELATION_EVIDENCE_SUBJECT_INSUFFICIENT", codes,
                      "分析器自己的分类告警被丢掉了（G5 同型回归）")

    def test_analyzer_with_no_usable_evidence_reports_analysis_failed(self):
        """证据全被主体筛掉 ⇒ 分析器产出为空 ⇒ 报'分析失败'而不是装作没事。"""
        self.orch.deps = replace(
            self.orch.deps, relationship_analyzer=RuleRelationshipAnalyzer())
        a, b = self._pair()
        ev = self._evidence("阿雾独自修了一夜灯", [a])
        step = self._run_relationship([ev])
        self.assertFalse(is_err(step), step)
        codes = self._codes(step)
        self.assertIn(runner_business.WARNING_RELATION_ANALYSIS_FAILED, codes)


class TestG6StabilityAssessment(unittest.TestCase):
    """G6 回归锁：稳定性判定**已接线**，且按 7 项拍板的口径工作。

    拍板口径：
      - D1 落库，作为证据的**附加字段**（不覆盖 `stability` 列）；
      - D2 同来源不算多次（组内按 `source_id` 去重）；
      - D3 分组键 = 归一化文本 + 主体集合；
      - D4 无判定时保持 `UNKNOWN`，不进 `traits`，并记录为"未判定"。

    核心结论（已证明、此处的测试就是它的证据）：**单来源下 `traits` 必然为空**
    —— 同来源的同一说法只会有一个代表，`occurrences` 恒为 1，按 D-109 判
    `SINGLE`，而 `SINGLE` 按 AC-7 绝不进 `traits`。
    """

    def setUp(self) -> None:
        self.out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        self.settings = settings_for()
        self.stack = runner.Stack(self.settings, ExternalUsageLedger(), self.out)
        self.orch = self.stack.orchestrator()
        self._sources: dict[str, str] = {}

    def tearDown(self) -> None:
        self.stack.teardown()

    # ---------------------------------------------------------------- 工具

    def _source(self, key: str) -> str:
        if key not in self._sources:
            ref = unwrap(self.stack.evidence.register_source(
                self.stack.ctx,
                SourceDraft(
                    title=f"合成材料-{key}",
                    canonical_key=f"https://example.test/{key}",
                    url=f"https://example.test/{key}")))
            self._sources[key] = str(ref.id)
        return self._sources[key]

    def _trait(self, text: str, subject: str, source_key: str) -> str:
        ref = unwrap(self.stack.evidence.add_evidence(
            self.stack.ctx,
            EvidenceDraft(
                claim=text, tier="PROFILE_BACKGROUND", dimension="TRAIT",
                subject_entity_ids=[subject], source_id=self._source(source_key),
                extraction_confidence=0.9)))
        return str(ref.id)

    def _view(self, evidence_id: str):
        return unwrap(self.stack.evidence.get_evidence(self.stack.ctx, evidence_id))

    def _judge(self, evidence_ids: list[str]):
        items = [self._view(i) for i in evidence_ids]
        warnings: list[WarningNote] = []
        judged = self.orch._assess_stabilities(self.stack.ctx, items, warnings)
        return judged, warnings

    def _draft(self, entity_id: str, evidence_ids: list[str], judged=None):
        items = [self._view(i) for i in evidence_ids]
        return BusinessOrchestrator._build_persona_draft(
            entity_id, items, "FICTIONAL_CHARACTER", judged)

    # ---------------------------------------------------------------- 用例

    def test_single_source_trait_never_reaches_traits(self):
        """单来源 ⇒ occurrences=1 ⇒ SINGLE ⇒ 落 single_events（traits 空）。"""
        a = self.stack.ensure_entity("阿雾")
        ev = self._trait("阿雾总在深夜修灯", a, "src-a")
        judged, warnings = self._judge([ev])
        self.assertEqual(str(judged[ev]), "SINGLE")
        self.assertEqual(warnings, [])
        draft = self._draft(a, [ev], judged)
        self.assertEqual(draft.traits, [], "单来源的 TRAIT 证据绝不可进 traits")
        self.assertEqual(len(draft.single_events), 1)
        self.assertEqual(str(draft.single_events[0].stability), "SINGLE",
                         "标签应从'没判过(UNKNOWN)'变成'判过且是单次(SINGLE)'")

    def test_same_wording_from_two_sources_becomes_repeated(self):
        """两个来源说同一句话 ⇒ REPEATED ⇒ 进 traits（AC-7 允许的情形）。"""
        a = self.stack.ensure_entity("阿雾")
        ev1 = self._trait("阿雾总在深夜修灯", a, "src-a")
        ev2 = self._trait("阿雾总在深夜修灯", a, "src-b")
        judged, _ = self._judge([ev1, ev2])
        self.assertEqual(str(judged[ev1]), "REPEATED")
        self.assertEqual(str(judged[ev2]), "REPEATED")
        draft = self._draft(a, [ev1, ev2], judged)
        self.assertEqual(len(draft.traits), 2, "多来源佐证的性格才允许进 traits")
        self.assertEqual(draft.single_events, [])

    def test_whitespace_variant_from_same_source_does_not_count_twice(self):
        """**D2 的核心**：同来源的空白变体不得凑成"多次出现"。

        幂等键用**精确** claim、分组键用**归一化** claim，两者的差集就是这类
        近似重复。若原样喂给 `judge_stability`，`occurrences=2/distinct=1`
        会被判 REPEATED 并进 traits —— 等于一条材料自己给自己作证两次，
        绕过 AC-7。按 `source_id` 去重后这扇门就关上了。
        """
        a = self.stack.ensure_entity("阿雾")
        ev1 = self._trait("阿雾总在深夜修灯", a, "src-a")
        ev2 = self._trait("阿雾总在深夜  修灯", a, "src-a")   # 仅多一个空格
        self.assertNotEqual(ev1, ev2, "前提：两条证据都真的登记下来了")
        judged, _ = self._judge([ev1, ev2])
        self.assertEqual(str(judged[ev1]), "SINGLE")
        self.assertEqual(str(judged[ev2]), "SINGLE")
        draft = self._draft(a, [ev1, ev2], judged)
        self.assertEqual(draft.traits, [],
                         "同来源的近似重复不得被算成 REPEATED（那会绕过 AC-7）")
        self.assertEqual(len(draft.single_events), 2)

    def test_three_sources_become_pattern(self):
        """三个不同来源 ⇒ PATTERN。"""
        a = self.stack.ensure_entity("阿雾")
        ids = [self._trait("阿雾总在深夜修灯", a, k)
               for k in ("src-a", "src-b", "src-c")]
        judged, _ = self._judge(ids)
        self.assertEqual({str(v) for v in judged.values()}, {"PATTERN"})

    def test_verdict_is_persisted_as_extra_field_without_touching_stability(self):
        """**D1**：判定作为附加字段落库，`stability` 列仍是登记值 `UNKNOWN`。"""
        a = self.stack.ensure_entity("阿雾")
        ev1 = self._trait("阿雾总在深夜修灯", a, "src-a")
        ev2 = self._trait("阿雾总在深夜修灯", a, "src-b")
        self._judge([ev1, ev2])

        view = self._view(ev1)
        self.assertEqual(view.stability, "UNKNOWN",
                         "登记时的声明必须原样保留（不得被判定覆盖）")
        self.assertIsInstance(view.stability_verdict, dict)
        self.assertEqual(view.stability_verdict["stability"], "REPEATED")
        self.assertEqual(view.stability_verdict["occurrences"], 2)
        self.assertEqual(view.stability_verdict["distinct_sources"], 2)
        self.assertIn("reason", view.stability_verdict)
        self.assertEqual(view.effective_stability, "REPEATED",
                         "消费方读 effective_stability，判定优先于登记值")

    def test_verdict_survives_reread_from_storage(self):
        """判定必须是**真的落库**（重开一次读取仍在），不是内存里的临时值。"""
        a = self.stack.ensure_entity("阿雾")
        ev = self._trait("阿雾总在深夜修灯", a, "src-a")
        self._judge([ev])
        row = unwrap(self.stack.storage.get(self.stack.ctx, "EvidenceItem", ev))
        self.assertIsInstance(row.get("stability_verdict"), dict)
        self.assertEqual(row["stability_verdict"]["stability"], "SINGLE")

    def test_without_judgement_behaviour_is_unchanged(self):
        """**D4**：拿不到判定时保持 `UNKNOWN`，仍不进 traits。"""
        a = self.stack.ensure_entity("阿雾")
        ev = self._trait("阿雾总在深夜修灯", a, "src-a")
        draft = self._draft(a, [ev], None)          # 传 None = 旧行为
        self.assertEqual(draft.traits, [])
        self.assertEqual(str(draft.single_events[0].stability), "UNKNOWN",
                         "无判定时必须如实记 UNKNOWN（不猜）")

    def test_declared_stability_is_never_overwritten_by_the_judge(self):
        """已**明确声明**的 stability 不被机械判定覆盖（与 G7 不降级覆盖同精神）。

        真实链路里抽取器永远给 `UNKNOWN`（PF-V2 不允许模型自评），所以判定该跑；
        但夹具 / 显式种子 / 人工标注写了 `REPEATED` 时那是更强的信息，
        机械规则把它盖成 `SINGLE` 会把本该进 `traits` 的证据踢出去。
        """
        a = self.stack.ensure_entity("阿雾")
        declared = unwrap(self.stack.evidence.add_evidence(
            self.stack.ctx,
            EvidenceDraft(
                claim="阿雾总在深夜修灯", tier="PROFILE_BACKGROUND", dimension="TRAIT",
                subject_entity_ids=[a], source_id=self._source("src-a"),
                extraction_confidence=0.9, stability="REPEATED")))
        judged, _ = self._judge([str(declared.id)])
        self.assertEqual(judged, {}, "已声明的证据不该被重新判定")
        self.assertIsNone(self._view(str(declared.id)).stability_verdict,
                          "已声明的证据不该被写入判定字段")
        draft = self._draft(a, [str(declared.id)], judged)
        self.assertEqual(len(draft.traits), 1, "已声明 REPEATED 的 TRAIT 必须留在 traits")

    def test_declared_members_still_count_towards_occurrences(self):
        """已声明的成员参与计数：声明 + 未声明同说法 ⇒ 未声明那条判 REPEATED。"""
        a = self.stack.ensure_entity("阿雾")
        declared = unwrap(self.stack.evidence.add_evidence(
            self.stack.ctx,
            EvidenceDraft(
                claim="阿雾总在深夜修灯", tier="PROFILE_BACKGROUND", dimension="TRAIT",
                subject_entity_ids=[a], source_id=self._source("src-a"),
                extraction_confidence=0.9, stability="REPEATED")))
        pending = self._trait("阿雾总在深夜修灯", a, "src-b")
        judged, _ = self._judge([str(declared.id), pending])
        self.assertEqual(str(judged[pending]), "REPEATED",
                         "另一个来源的佐证必须算进 occurrences（否则会低估）")

    def test_step_persona_wires_judgement_end_to_end(self):
        """整链：`_step_persona` 必须把判定接进档案，而不只是库里有字段。"""
        a = self.stack.ensure_entity("阿雾")
        ev1 = self._trait("阿雾总在深夜修灯", a, "src-a")
        ev2 = self._trait("阿雾总在深夜修灯", a, "src-b")
        step = self.orch._step_persona(
            self.stack.ctx, self.stack.request(), {"evidence_ids": [ev1, ev2]})
        self.assertFalse(is_err(step), step)
        loaded = unwrap(self.stack.persona.get_persona(self.stack.ctx, a))
        self.assertEqual(len(loaded.traits), 2,
                         "多来源佐证的 TRAIT 必须真的写进 traits")
        self.assertEqual(loaded.single_events, [])
        for item in loaded.traits:
            self.assertIn(str(item.stability), ("REPEATED", "PATTERN"))


# ==========================================================================
# C 轮 E1：离线阳性对照 —— "关系条目产出路径"的可证伪定义
# ==========================================================================
class TestRoundCPairSmoke(unittest.TestCase):
    """E1：给定**合格的双主体 RELATION claim**，产品代码能否把它变成条目。

    **边界（必须写进报告，不许外推）**：

      - E1 只证明"链路能把合格输入变成条目"，**不**证明畸形输入下的鲁棒性
        （摘录错位、主体 ID 越界、参与者不足等由别的测试覆盖）；
      - E1 通过 **不等于**"真实模型一定会抽出双人句"——那是 E2 的乙级验收项，
        不可控，失败也不判 E1 失败；
      - E1 的价值在于：若 E2 出现 L2/L3 有候选而 L4 为空，可以直接判定为
        **代码缺陷**，而不是"模型没抽出来"，也**不许**用"再跑一次"掩盖。
    """

    def _run_pair(self, *, with_pair: bool):
        """跑一次双人物编排器路径（桩模型，零网络、零费用），返回产物。"""
        settings = settings_for()
        out = runner.Out(SECRET_PLACEHOLDER, quiet=True)
        problems: list[str] = []
        stub = runner.StubChatProvider('{"claims": []}')

        def on_targets(stack, entity_id: str) -> None:
            ids = {t.display_name: t.entity_id for t in stack.targets}
            stub.payload = runner.orchestrator_payload(ids, with_pair=with_pair)

        with tempfile.TemporaryDirectory(prefix="p45c-e1-") as td:
            dump = Path(td) / "pair.json"
            with network_blocked():
                runner.run_orchestrator_real(
                    settings, out, problems,
                    expect=lambda cond, desc: problems.append(desc) if not cond else None,
                    ledger=ExternalUsageLedger(),
                    limits=runner.model_limits_from_settings(settings),
                    dump_path=str(dump), provider=stub, on_targets=on_targets,
                    names=["阿雾", "阿灯"])
            artifact = json.loads(dump.read_text(encoding="utf-8"))
        return artifact, problems, out, stub

    def test_e1_pair_claims_become_shared_experiences(self):
        """阳性对照：2 条双主体 RELATION claim → 2 条 shared_experiences + status=OK。"""
        artifact, problems, _out, stub = self._run_pair(with_pair=True)
        self.assertEqual(problems, [], f"E1 不得有失败项：{problems}")
        self.assertEqual(stub.chat_calls, 1, "桩仍只被调用 1 次（零网络、零费用）")

        rel = artifact["relationship"]
        self.assertEqual(len(rel), 1, "双人物必须产出恰好 1 份关系档案")
        profile = rel[0]
        self.assertEqual(profile["status"], "OK", "有条目 ⇒ status 必须是 OK")
        self.assertEqual(profile["cardinality"], "PAIR")
        self.assertEqual(len(profile["participant_entity_ids"]), 2)
        self.assertEqual(len(profile["shared_experiences"]), 2,
                         "两条双主体 RELATION 证据必须各成一条条目")

        # 每条条目的引用都必须落在本次已登记证据里（可追溯，不是凭空生成）
        evidence_ids = set(artifact["evidence_ids"])
        for item in profile["shared_experiences"]:
            self.assertTrue(item["evidence_refs"], "条目必须带 evidence_refs")
            self.assertTrue(set(item["evidence_refs"]) <= evidence_ids,
                            f"条目引用了未登记证据：{item['evidence_refs']}")

        # 两份人物档案都在、且 traits 不违反 AC-7
        self.assertEqual(len(artifact["targets"]), 2)
        for t in artifact["targets"]:
            for item in t["traits"]:
                self.assertIn(str(item["stability"]), ("REPEATED", "PATTERN"),
                              "SINGLE/UNKNOWN 绝不能进 traits")

    def test_e1_four_layer_probe_reports_link_ok(self):
        """四层探针必须落在"链路通"行，且能从原始计数复算。"""
        artifact, _problems, _out, _stub = self._run_pair(with_pair=True)
        diag = artifact["diagnostics"]
        self.assertEqual(diag["L1_materials_with_gate"], 3, "三份材料全部过门禁")
        self.assertGreaterEqual(diag["L2_relation_candidates"], 2)
        self.assertGreaterEqual(diag["L3_candidates_covering_both"], 2)
        self.assertGreaterEqual(diag["L4_shared_experiences"], 1)
        self.assertEqual(diag["verdict"], "LINK_OK", diag["verdict_text"])

        # G6：每条证据都有判定，且 stability 列未被改写
        stab = diag["stability"]
        self.assertEqual(stab["with_verdict"], stab["total"])
        self.assertEqual(stab["missing"], [])
        self.assertEqual(stab["declared_column_rewritten"], [])
        self.assertEqual(stab["occurrences_ne_distinct_sources"], [])
        for row in artifact["evidence"]:
            self.assertIsNotNone(row["stability_verdict"], "G6-D1 判定必须落库")
            self.assertEqual(row["stability"], "UNKNOWN",
                             "stability 列是登记时声明，判定不得覆盖")

    def test_e1_artifact_passes_independent_verifier(self):
        """独立复算器（含新增 REL.* / STAB.* / C*.）对 E1 产物必须全绿。"""
        artifact, _problems, _out, _stub = self._run_pair(with_pair=True)
        rep = verifier.verify(artifact)
        failed = [name for name, ok, _ in rep.rows if not ok]
        self.assertEqual(failed, [], f"独立复算失败项：{failed}")
        names = [name for name, _ok, _ in rep.rows]
        for required in ("REL.2", "REL.4", "STAB.1", "STAB.2", "C1"):
            self.assertTrue(any(n.startswith(required) for n in names),
                            f"复算器缺少 {required} 检查（新增项没生效）")

    def test_e1_without_pair_claims_is_insufficient_not_fault(self):
        """阴性对照：没有双主体 claim 时必须判"证据不足"，**不得**判"链路故障"。

        这是"区分链路通与证据不足"的反向锁——否则探针可能把所有空结果
        都报成 LINK_FAULT（或反之全报成证据不足），归因就失效了。
        """
        artifact, problems, _out, _stub = self._run_pair(with_pair=False)
        self.assertEqual(problems, [], f"证据不足不是故障，不得记失败项：{problems}")
        diag = artifact["diagnostics"]
        self.assertEqual(diag["L1_materials_with_gate"], 3)
        self.assertEqual(diag["L2_relation_candidates"], 0)
        self.assertEqual(diag["L4_shared_experiences"], 0)
        self.assertEqual(diag["verdict"], "INSUFFICIENT_NO_RELATION_DIMENSION",
                         diag["verdict_text"])
        rel = artifact["relationship"][0]
        self.assertEqual(rel["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("NO_RELATION_EVIDENCE",
                      artifact["stage_warnings"].get("RELATIONSHIP_DISTILL", []),
                      "证据不足必须带对应告警码，不允许静默返回空档案")


if __name__ == "__main__":
    unittest.main()
