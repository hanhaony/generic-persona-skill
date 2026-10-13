"""核心契约测试：Result 信封、错误码、ID 规则、命名空间闸门。

对应文档：INTERFACES §1.4/§1.5、ERROR_HANDLING §2、DATA_MODEL §2.1/§5.4。
"""

import unittest

from persona.core.context import new_call_context
from persona.core.enums import (
    EntityType,
    EvidenceTier,
    ProvenanceNamespace,
    TaskStatus,
    namespace_allowed,
)
from persona.core.errors import ErrorCategory, ErrorCode, new_error
from persona.core.ids import is_valid_id, new_id
from persona.core.result import Err, Ok, UsageDelta, WarningNote, is_err, is_ok, unwrap


class TestResultEnvelope(unittest.TestCase):
    def test_ok_and_err(self):
        self.assertTrue(is_ok(Ok(1)))
        self.assertTrue(is_err(Err(new_error(ErrorCode.INTERNAL))))
        self.assertEqual(unwrap(Ok("value")), "value")

    def test_warning_is_not_failure(self):
        """冲突、去重命中、资料不足都走 Ok + warnings，任务继续（D-055）。"""
        result = Ok("ok", warnings=[WarningNote(code="CONFLICT_RECORDED")])
        self.assertTrue(is_ok(result))
        self.assertEqual(result.warnings[0].code, "CONFLICT_RECORDED")

    def test_usage_unknown_tokens_stay_none(self):
        """D-060：token 未知必须留 None，禁止填估计值。"""
        delta = UsageDelta(requests=1)
        self.assertIsNone(delta.tokens_in)
        self.assertIsNone(delta.tokens_out)
        self.assertIsNone(delta.cost_estimate)

    def test_error_keeps_category_when_adding_context(self):
        err = new_error(ErrorCode.EXTERNAL_TIMEOUT, trace_ref="trc_1")
        richer = err.with_context(stage="PERSONA_BUILDING")
        self.assertEqual(richer.code, err.code)          # 不得改写 code
        self.assertEqual(richer.category, err.category)  # 不得改写 category
        self.assertEqual(richer.details["stage"], "PERSONA_BUILDING")


class TestErrorCatalogue(unittest.TestCase):
    def test_budget_exceeded_is_paused_not_failed(self):
        """D-043：超预算不重试，但**可以续跑**。"""
        err = new_error(ErrorCode.BUDGET_EXCEEDED)
        self.assertFalse(err.retryable)
        self.assertTrue(err.recoverable)
        self.assertEqual(err.category, ErrorCategory.BUDGET_EXCEEDED)

    def test_retryable_external_errors(self):
        for code in (ErrorCode.EXTERNAL_TIMEOUT, ErrorCode.EXTERNAL_RATE_LIMIT,
                     ErrorCode.EXTERNAL_UNAVAILABLE, ErrorCode.STORAGE_READ_FAILED):
            with self.subTest(code=code):
                self.assertTrue(new_error(code).retryable)

    def test_validation_errors_not_retryable(self):
        for code in (ErrorCode.VALIDATION_NAMESPACE_FORBIDDEN, ErrorCode.EXTERNAL_AUTH,
                     ErrorCode.INVARIANT_VIOLATION_ISOLATION):
            with self.subTest(code=code):
                self.assertFalse(new_error(code).retryable)


class TestIds(unittest.TestCase):
    def test_id_format(self):
        for kind in ("Project", "PersonEntity", "EvidenceItem", "MemoryItem", "TaskRecord"):
            with self.subTest(kind=kind):
                value = new_id(kind)
                self.assertTrue(is_valid_id(kind, value), value)
                self.assertEqual(len(value), len(value[: value.index("_") + 1]) + 32)

    def test_id_rejects_wrong_kind(self):
        evidence_id = new_id("EvidenceItem")
        self.assertFalse(is_valid_id("Source", evidence_id))

    def test_id_not_sequential(self):
        self.assertNotEqual(new_id("Source"), new_id("Source"))


class TestNamespaceGate(unittest.TestCase):
    def test_evidence_item_rejects_simulated(self):
        """D-023：证据里永远不许出现模拟内容。"""
        self.assertFalse(namespace_allowed("EvidenceItem", ProvenanceNamespace.SIMULATED))
        self.assertTrue(namespace_allowed("EvidenceItem", ProvenanceNamespace.PUBLIC_SOURCE))

    def test_conversation_turn_only_simulated(self):
        self.assertTrue(namespace_allowed("ConversationTurn", ProvenanceNamespace.SIMULATED))
        self.assertFalse(namespace_allowed("ConversationTurn", ProvenanceNamespace.PUBLIC_SOURCE))

    def test_profile_only_system_derived(self):
        for kind in ("PersonaProfile", "RelationshipProfile"):
            with self.subTest(kind=kind):
                self.assertTrue(namespace_allowed(kind, ProvenanceNamespace.SYSTEM_DERIVED))
                self.assertFalse(namespace_allowed(kind, ProvenanceNamespace.SIMULATED))
                self.assertFalse(namespace_allowed(kind, ProvenanceNamespace.PUBLIC_SOURCE))

    def test_unknown_namespace_string_rejected(self):
        self.assertFalse(namespace_allowed("EvidenceItem", "MADE_UP"))


class TestEnumsMatchDocs(unittest.TestCase):
    def test_key_values(self):
        self.assertEqual(EvidenceTier.PROFILE_BACKGROUND.value, "PROFILE_BACKGROUND")
        self.assertEqual(EntityType.FICTIONAL_CHARACTER.value, "FICTIONAL_CHARACTER")
        self.assertEqual(
            [s.value for s in TaskStatus],
            ["PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"],
        )
        self.assertEqual(
            sorted(n.value for n in ProvenanceNamespace),
            ["PUBLIC_SOURCE", "SIMULATED", "SYSTEM_DERIVED", "USER_PROVIDED"],
        )


class TestCallContext(unittest.TestCase):
    def test_context_autofills_trace_and_scope(self):
        ctx = new_call_context(project_id="prj_1", actor="test", task_id="tsk_1")
        self.assertTrue(ctx.trace_ref)
        self.assertEqual(ctx.budget_scope, "tsk_1")

    def test_context_requires_project_and_actor(self):
        with self.assertRaises(ValueError):
            new_call_context(project_id="", actor="test")
        with self.assertRaises(ValueError):
            new_call_context(project_id="prj_1", actor="")


if __name__ == "__main__":
    unittest.main()
