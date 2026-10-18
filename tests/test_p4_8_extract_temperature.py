#!/usr/bin/env python
"""D-293：抽取温度是**可配置参数**，生产默认 0.7；R4.8 显式传 0。

R4.7 的对照失效：同一批材料、同一提示词、`temperature=0.7`，两轮证据只有
**4 条逐字重合（Jaccard 8.0%）**。先查了官方文档（2026-10-18，
`api-docs.deepseek.com/api/create-chat-completion`）：请求体参数里**没有 `seed`**，
响应里的 `system_fingerprint` 只是后端配置标识、不可回传 ⇒ "多样性 + 可复现"
做不到，只能靠温度旋钮。

用户拍板：R4.8 **跑一轮、温度 0**（0 温度下同输入同输出，跑两轮比重合度没有意义）。
因此本文件锁四件事：

1. **生产默认仍是 0.7**——R4.8 的 0 不能固化进默认值（那是实验配置）；
2. 温度**可配置**：构造器 / `build_request()` 都能收，且非法值**报错不夹值**；
3. **抽取与对话是两个独立旋钮**：改抽取不影响 `DialogueService` 的对话温度；
4. 提示词/材料没变（只动采样参数），避免"顺手改了别的"。

注：`extract()` 与 `build_request()` 走**同一个**构造函数，所以校验
`build_request()` 的返回值等价于校验真正发出去的那条请求。
"""

from __future__ import annotations

import unittest

from persona.core.types import SourceMaterial
from persona.modules.dialogue import DIALOGUE_TEMPERATURE, DialogueDeps, DialogueService
from persona.modules.extraction import (
    DEFAULT_EXTRACT_TEMPERATURE,
    ProviderClaimExtractor,
    resolve_temperature,
)
from persona.providers.base import ChatRequest


def _material(key: str = "m1") -> SourceMaterial:
    return SourceMaterial(
        source_id=f"src_{key}", title="为什么是林黛玉？",
        url=f"https://example.invalid/{key}", canonical_key=key,
        snippet="林黛玉言辞尖酸刻薄、喜欢怼人。",
        subject_entity_ids=["pe_dai"],
    )


class _NoopProvider:
    """只给构造器用（本文件不发送任何请求）。"""

    provider_name = "noop"
    offline = True

    def chat(self, ctx, request):        # pragma: no cover - 不会被调用
        raise AssertionError("本测试不应发送请求")

    def search(self, ctx, request):      # pragma: no cover
        raise AssertionError("本测试不应发送请求")

    def extract(self, ctx, request):     # pragma: no cover
        raise AssertionError("本测试不应发送请求")


class TestProductionDefault(unittest.TestCase):
    def test_production_default_is_still_07(self):
        """**生产默认必须还是 0.7**：R4.8 的 0 是实验值，不是产品行为。"""
        self.assertEqual(DEFAULT_EXTRACT_TEMPERATURE, 0.7)

    def test_default_flows_through_when_not_configured(self):
        request = ProviderClaimExtractor.build_request([_material()])
        self.assertEqual(request.temperature, DEFAULT_EXTRACT_TEMPERATURE)

    def test_extractor_without_parameter_uses_production_default(self):
        extractor = ProviderClaimExtractor(_NoopProvider())
        self.assertEqual(extractor.temperature, DEFAULT_EXTRACT_TEMPERATURE)

    def test_request_records_the_temperature_in_metadata(self):
        """报告要对账：温度必须落在请求元数据里，不能只存在于代码常量。"""
        request = ProviderClaimExtractor.build_request(
            [_material()], temperature=0.0)
        self.assertEqual(request.metadata.get("temperature"), "0")


class TestConfigurable(unittest.TestCase):
    def test_explicit_zero_is_forwarded(self):
        """R4.8 的用法：显式 0（不是默认值）。"""
        request = ProviderClaimExtractor.build_request(
            [_material()], temperature=0)
        self.assertEqual(request.temperature, 0.0)

    def test_extractor_forwards_its_configured_temperature(self):
        extractor = ProviderClaimExtractor(_NoopProvider(), temperature=0.0)
        self.assertEqual(extractor.temperature, 0.0)
        # `extract()` 用它构造请求——这里直接验构造路径的取值来源
        request = extractor.build_request(
            [_material()], temperature=extractor.temperature)
        self.assertEqual(request.temperature, 0.0)

    def test_string_and_int_are_coerced(self):
        self.assertEqual(resolve_temperature("0.3", default=0.7), 0.3)
        self.assertEqual(resolve_temperature(1, default=0.7), 1.0)
        self.assertEqual(resolve_temperature(None, default=0.7), 0.7)

    def test_non_numeric_raises_instead_of_falling_back(self):
        """**不静默容错**：发错温度会被厂商夹值，而报告仍写着你想的那个数。"""
        with self.assertRaises(ValueError):
            resolve_temperature("hot", default=0.7)
        with self.assertRaises(ValueError):
            ProviderClaimExtractor.build_request([_material()], temperature="hot")

    def test_out_of_range_raises(self):
        for bad in (-0.1, 2.1):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    resolve_temperature(bad, default=0.7)

    def test_boundary_values_are_allowed(self):
        for ok in (0.0, 2.0):
            with self.subTest(ok=ok):
                self.assertEqual(resolve_temperature(ok, default=0.7), ok)


class TestDialogueIsASeparateKnob(unittest.TestCase):
    def test_dialogue_temperature_is_still_07(self):
        self.assertEqual(DIALOGUE_TEMPERATURE, 0.7)

    def test_dialogue_service_default_is_independent_of_extraction(self):
        """改抽取温度**不许**波及对话：两者是不同的旋钮。"""
        # 只验构造期的温度取值，不触碰任何依赖（本文件不发请求）
        service = DialogueService(DialogueDeps(
            None, None, None, None, None, None))          # type: ignore[arg-type]
        self.assertEqual(service.temperature, DIALOGUE_TEMPERATURE)
        extractor = ProviderClaimExtractor(_NoopProvider(), temperature=0.0)
        self.assertEqual(extractor.temperature, 0.0)
        self.assertEqual(service.temperature, 0.7)   # 没被带走

    def test_global_chat_request_default_untouched(self):
        """`ChatRequest` 的默认值不动：它是兜底，不是抽取/对话的配置入口。"""
        self.assertEqual(ChatRequest().temperature, 0.7)


class TestPromptUnchanged(unittest.TestCase):
    def test_prompt_and_materials_are_unchanged(self):
        """只动采样参数：材料条数、条数上限与提示词版本都不该变。"""
        low = ProviderClaimExtractor.build_request([_material()], temperature=0.0)
        high = ProviderClaimExtractor.build_request([_material()], temperature=0.7)
        self.assertEqual(low.messages, high.messages)
        self.assertEqual(low.metadata.get("material_count"), "1")
        self.assertEqual(low.metadata.get("max_claims_per_material"), "4")
        self.assertIn("prompt_version", low.metadata)
        self.assertEqual(low.metadata.get("prompt_version"),
                         high.metadata.get("prompt_version"))


if __name__ == "__main__":
    unittest.main()
