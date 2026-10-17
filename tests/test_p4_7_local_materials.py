"""P4.7：CLI 离线材料注入（`persona business run --material PATH`）测试。

# 这条通道为什么必须存在

检索 Provider 尚未接线（H-17 / P3），所以 `persona business run` 在任何配置下都
产不出材料 —— CLI 入口等于从没被真正跑过。补一条"用户把自己的材料文件交给 CLI"
的通道，就能在**零外部调用**的前提下证明：装配层 → 编排器 → 五个阶段在 CLI
入口下真的能协同工作。

# 两条不能破的边界（G4 的教训）

1. **不内置合成数据**：材料只能来自用户给的文件；产品代码里没有"演示用假材料"，
   否则 CLI 看起来能生成档案，实际产出的是假数据；
2. **门禁不能绕过**：本地文件与抓取正文走同一个 `assess_content_quality`，
   判不合格的文件不进材料流（只留告警），全部不合格则**在发请求之前拒绝**。

# 本文件的测试策略

- 加载/门禁/登记：直接调 `load_local_materials` 与 `ResearchService.adopt`；
- CLI 五阶段：把 `build_business_stack` 换成"注入桩抽取器 + 会失败的 Provider"的
  包装（**不发任何真实请求**），其余全部走真实装配层与真实编排器。
  这样验证的是 CLI 接线与业务链，而不是模型能力。
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from persona.cli.main import EXIT_OK, main
from persona.core.context import new_call_context
from persona.core.enums import EvidenceDimension, EvidenceTier
from persona.core.errors import ErrorCode, new_error
from persona.core.result import Err, Ok, is_ok, unwrap
from persona.core.types import UsageReport
from persona.modules.evidence import EvidenceService
from persona.modules.extraction import CandidateClaim, MockClaimExtractor
from persona.modules.research import (
    WARNING_LOCAL_MATERIAL_GATE_REJECTED,
    WARNING_LOCAL_MATERIAL_UNREADABLE,
    ResearchDeps,
    ResearchService,
    load_local_materials,
)
from persona.providers.base import CapabilitySet, HealthStatus
from persona.providers.mock import FakeProvider
from persona.storage.sqlite import SqliteStorage

#: 通过门禁的材料（4 段，每段 ≥40 字，合计 ≥200 字；主语包含目标名"阿雾"）
GOOD_TEXT = "\n\n".join((
    "阿雾是雾港灯塔的常年值守者，据雾港地方志记载，她自少年时代起便接替家中长辈看守这座灯塔，一直未曾离开过半步。",
    "阿雾偏爱在傍晚独自巡视堤坝，随身带着一本记录潮位与风向的旧册子，册页边角早已被海风磨得发白卷曲。",
    "阿雾向来沉默寡言，很少与星灯会的同伴闲谈，只在每年灯会点亮的那一晚才会多说几句话，说完便又回到塔上。",
    "阿雾习惯在整点敲响灯塔的铜钟，这个习惯维持了整整十二年，雾港的老住户只要听见钟声就知道她还在值守。",
    "塔上的灯油账她记了满满三册，每一页都写着当夜的风向，字迹细密却从不潦草。",
))

#: 同上的第二份材料（写给"阿灯"）
GOOD_TEXT_2 = "\n\n".join((
    "阿灯是雾港星灯会的常任司仪，据雾港庆典档案记载，她自幼便随长辈学习掌灯礼数，至今未曾间断过一日。",
    "阿灯喜欢在灯会开场前独自登上堤坝看海，随身带着一盏用贝壳拼成的小灯，灯面早已被海风吹得温润发亮。",
    "阿灯生性热烈健谈，与雾港的每一位船工都熟识，只在深夜散场之后才会独坐片刻，然后重新回到灯棚里去。",
    "阿灯习惯在每次灯会结束时点燃最后一盏灯，这个习惯维持了整整九年，雾港的老住户只要看见那盏灯就知道灯会散了。",
    "散场之后阿灯总要沿着堤坝走一遍，把每一盏灯的位置记在心里，第二天再照着记下的次序重新摆正。",
))


class _ForbiddenProvider:
    """任何真实调用都会**失败并记账**的 Provider：用来证明离线运行没发请求。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="forbidden"))

    def chat(self, ctx, req):
        self.calls.append("chat")
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                             "测试禁止真实调用", trace_ref=ctx.trace_ref))

    def search(self, ctx, req):
        self.calls.append("search")
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                             "测试禁止真实调用", trace_ref=ctx.trace_ref))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=False))


class _EchoChatProvider:
    """对话阶段的桩 Provider：只回一句固定模拟文本，**永不联网**。"""

    def __init__(self) -> None:
        self.calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub-chat", supports_chat=True))

    def chat(self, ctx, req):
        from persona.core.result import UsageDelta
        from persona.providers.base import ChatResponse

        self.calls += 1
        return Ok(ChatResponse(text="（模拟）我照旧在黄昏点亮灯塔。",
                               finish_reason="stop", model_id="stub-chat",
                               usage=UsageDelta(requests=1)))

    def search(self, ctx, req):
        return Err(new_error(ErrorCode.EXTERNAL_UNAVAILABLE,
                             "对话桩不提供检索", trace_ref=ctx.trace_ref))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class _EchoExtractor:
    """按材料产候选的桩抽取器（**不联网、不读 fixture 表**）。

    与 `MockClaimExtractor` 的分工：那个按 canonical_key 查固定 fixture（测试
    需要预先知道实体 ID），而 CLI 的实体 ID 是 `register_targets` 现场生成的。
    所以这里直接读材料自带的 `subject_entity_ids`（装配层已经把目标 ID 绑上去了）。
    """

    def __init__(self) -> None:
        self.calls = 0
        self.material_keys: list[str] = []

    def extract(self, ctx, materials, *, targets=None):
        self.calls += 1
        materials = list(materials or [])
        self.material_keys = [str(m.canonical_key or m.url) for m in materials]
        fixtures = {}
        all_subjects = [str(t.entity_id) for t in (targets or [])]
        for index, material in enumerate(materials):
            rows: list[CandidateClaim] = []
            names = list(material.target_display_names or [])
            if names:
                rows.append(CandidateClaim(
                    claim=f"{names[0]}是雾港灯塔的常年值守者",
                    tier=EvidenceTier.PROFILE_BACKGROUND,
                    dimension=EvidenceDimension.BACKGROUND,
                    subject_entity_ids=list(material.subject_entity_ids),
                    confidence=0.9))
            if index == 0 and len(all_subjects) >= 2:
                rows.append(CandidateClaim(
                    claim="阿雾与阿灯每年灯会都要一同登上堤坝核对灯位",
                    tier=EvidenceTier.PROFILE_BACKGROUND,
                    dimension=EvidenceDimension.RELATION,
                    subject_entity_ids=list(all_subjects),
                    confidence=0.9))
            fixtures[str(material.canonical_key or material.url)] = rows
        return MockClaimExtractor(fixtures).extract(ctx, materials, targets=targets)


@contextlib.contextmanager
def _temp_data_dir():
    with tempfile.TemporaryDirectory(prefix="gps-materials-") as tmp:
        previous = os.environ.get("PERSONA_DATA_DIR")
        os.environ["PERSONA_DATA_DIR"] = tmp
        try:
            yield Path(tmp)
        finally:
            if previous is None:
                os.environ.pop("PERSONA_DATA_DIR", None)
            else:
                os.environ["PERSONA_DATA_DIR"] = previous


class LocalMaterialLoaderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gps-mat-"))
        self.good = self.tmp / "阿雾.txt"
        self.good.write_text(GOOD_TEXT, encoding="utf-8", newline="\n")
        self.short = self.tmp / "短.txt"
        self.short.write_text("阿雾只是很短的一句话。", encoding="utf-8", newline="\n")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_file_becomes_material_with_gate_verdict(self):
        materials, warnings = load_local_materials(
            [self.good], target_entity_ids=["pe_awu"], target_names=["阿雾"])
        self.assertEqual(len(materials), 1)
        material = materials[0]
        self.assertEqual(material.quality_status, "VALID",
                         "本地材料必须带上门禁结论（否则严格模式下会被拒收）")
        self.assertTrue(material.url.startswith("file:///"), material.url)
        self.assertEqual(material.canonical_key, material.url)
        self.assertEqual(material.subject_entity_ids, ["pe_awu"])
        self.assertEqual(material.snippet, GOOD_TEXT)
        self.assertEqual(warnings, [])

    def test_short_file_is_rejected_with_reason_not_silently_dropped(self):
        materials, warnings = load_local_materials(
            [self.short], target_names=["阿雾"])
        self.assertEqual(materials, [])
        self.assertEqual([w.code for w in warnings], [WARNING_LOCAL_MATERIAL_GATE_REJECTED])
        self.assertIn("TOO_SHORT", warnings[0].message)

    def test_missing_file_warns_instead_of_crashing(self):
        missing = self.tmp / "根本不存在.txt"
        materials, warnings = load_local_materials([missing])
        self.assertEqual(materials, [])
        self.assertEqual([w.code for w in warnings], [WARNING_LOCAL_MATERIAL_UNREADABLE])

    def test_mixed_inputs_keep_only_usable_materials(self):
        materials, warnings = load_local_materials([self.good, self.short])
        self.assertEqual(len(materials), 1)
        self.assertEqual(len(warnings), 1)

    def test_target_not_mentioned_is_rejected(self):
        """门禁的相关性判据同样生效（换了目标名就对不上了）。"""
        materials, warnings = load_local_materials(
            [self.good], target_names=["完全无关的人"])
        self.assertEqual(materials, [])
        self.assertIn("TARGET_NOT_MENTIONED", warnings[0].message)


class ResearchAdoptTest(unittest.TestCase):
    """`ResearchService.adopt`：登记来源、保留门禁结论、可重复调用。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="gps-adopt-"))
        self.storage = SqliteStorage(self.tmp / "adopt.db")
        self.ctx = new_call_context(project_id="prj_p47", actor="tests")
        unwrap(self.storage.init_schema(self.ctx))
        self.evidence = EvidenceService(self.storage)
        self.research = ResearchService(ResearchDeps(
            provider=FakeProvider(), evidence=self.evidence))
        self.file = self.tmp / "阿雾.txt"
        self.file.write_text(GOOD_TEXT, encoding="utf-8", newline="\n")

    def tearDown(self) -> None:
        self.storage.close()
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_adopt_registers_source_and_binds_it_to_material(self):
        materials, _ = load_local_materials([self.file], target_names=["阿雾"])
        outcome = unwrap(self.research.adopt(self.ctx, materials))
        self.assertEqual(len(outcome.source_ids), 1)
        self.assertEqual(outcome.planned_count, 0, "没有检索就是 0，不伪造成 1")
        self.assertEqual(outcome.executed_count, 0)
        self.assertEqual(outcome.materials[0].source_id, outcome.source_ids[0])
        self.assertEqual(outcome.materials[0].quality_status, "VALID",
                         "门禁结论不能被登记过程抹掉")

    def test_adopt_twice_reuses_the_same_source(self):
        materials, _ = load_local_materials([self.file], target_names=["阿雾"])
        first = unwrap(self.research.adopt(self.ctx, materials))
        second = unwrap(self.research.adopt(self.ctx, materials))
        self.assertEqual(first.source_ids, second.source_ids,
                         "同一文件重复接入必须命中去重（幂等）")

    def test_adopt_rejects_material_without_locator(self):
        from persona.core.types import SourceMaterial

        outcome = self.research.adopt(self.ctx, [SourceMaterial(title="无定位符")])
        self.assertTrue(not is_ok(outcome))
        self.assertIn("可追溯", outcome.error.message)


class CliBusinessRunWithMaterialsTest(unittest.TestCase):
    """CLI 入口真跑五阶段（桩抽取器 + 禁止外呼的 Provider）。"""

    def setUp(self) -> None:
        self._dir = _temp_data_dir()
        self.tmp = self._dir.__enter__()
        self.doc = self.tmp / "阿雾.txt"
        self.doc.write_text(GOOD_TEXT, encoding="utf-8", newline="\n")
        self.doc2 = self.tmp / "阿灯.txt"
        self.doc2.write_text(GOOD_TEXT_2, encoding="utf-8", newline="\n")
        self.bad = self.tmp / "bad.txt"
        self.bad.write_text("太短。", encoding="utf-8", newline="\n")

    def tearDown(self) -> None:
        self._dir.__exit__(None, None, None)

    # ------------------------------------------------------------ 工具

    @staticmethod
    def _patched_stack(extractor, provider):
        """把装配层包一层：注入桩抽取器与"禁止外呼"的 Provider。"""
        import persona.orchestrator as package
        from persona.orchestrator import assembly

        real = assembly.build_business_stack

        def factory(settings, ctx, **kwargs):
            kwargs.setdefault("claim_extractor", extractor)
            kwargs.setdefault("chat_provider", provider)
            kwargs.setdefault("search_provider", provider)
            kwargs.setdefault("dialogue_provider", _EchoChatProvider())
            return real(settings, ctx, **kwargs)

        return mock.patch.object(package, "build_business_stack", factory)

    def _run(self, argv: list[str]) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(argv)
        return code, buf.getvalue()

    # ------------------------------------------------------------ 用例

    def test_no_usable_material_refuses_before_any_request(self):
        extractor = _EchoExtractor()
        provider = _ForbiddenProvider()
        with self._patched_stack(extractor, provider):
            code, text = self._run([
                "business", "run", "--no-dotenv", "--names", "阿雾",
                "--work-title", "《雾港与星灯》",
                "--adaptation-version", "2020 动画改编版",
                "--material", str(self.bad)])
        self.assertEqual(code, 1)
        self.assertIn("没有任何本地材料通过正文质量门禁", text)
        self.assertIn(WARNING_LOCAL_MATERIAL_GATE_REJECTED, text)
        self.assertEqual(extractor.calls, 0, "拒绝必须在抽取之前发生")
        self.assertEqual(provider.calls, [], "拒绝不得发出任何外部请求")

    def test_cli_runs_five_stages_from_local_materials(self):
        extractor = _EchoExtractor()
        provider = _ForbiddenProvider()
        with self._patched_stack(extractor, provider):
            code, text = self._run([
                "business", "run", "--no-dotenv",
                "--names", "阿雾,阿灯",
                "--work-title", "《雾港与星灯》",
                "--adaptation-version", "2020 动画改编版",
                "--material", str(self.doc),
                "--material", str(self.doc2)])

        self.assertEqual(code, EXIT_OK, text)
        self.assertIn("SUCCEEDED", text)
        for stage in ("RESEARCH", "EVIDENCE_EXTRACT", "PERSONA_DISTILL",
                      "RELATIONSHIP_DISTILL", "DIALOGUE_RUN"):
            row = next(line for line in text.splitlines() if stage in line)
            self.assertIn("EXECUTED", row, f"{stage} 必须真的执行：{row}")
        self.assertIn("未做检索", text, "研究阶段必须如实说明没有检索")
        self.assertIn("本地材料 2 份（不检索）", text)
        self.assertEqual(provider.calls, [], "离线材料通道不得发出任何外部请求")
        self.assertEqual(extractor.calls, 1, "五阶段只允许一次抽取调用")

    def test_material_content_reaches_the_extractor_verbatim(self):
        extractor = _EchoExtractor()
        provider = _ForbiddenProvider()
        with self._patched_stack(extractor, provider):
            self._run([
                "business", "run", "--no-dotenv", "--names", "阿雾",
                "--work-title", "《雾港与星灯》",
                "--adaptation-version", "2020 动画改编版",
                "--material", str(self.doc)])
        self.assertEqual(len(extractor.material_keys), 1)
        self.assertIn("阿雾.txt", extractor.material_keys[0],
                      "交给抽取器的材料必须能指回用户给的那个文件")

    def test_second_run_reuses_sources_instead_of_duplicating(self):
        """同一批材料跑两次：来源去重，不产生重复来源（幂等）。"""
        extractor = _EchoExtractor()
        provider = _ForbiddenProvider()
        argv = ["business", "run", "--no-dotenv", "--names", "阿雾",
                "--work-title", "《雾港与星灯》",
                "--adaptation-version", "2020 动画改编版",
                "--material", str(self.doc)]
        with self._patched_stack(extractor, provider):
            self._run(argv)
            storage_rows_first = self._source_count()
            self._run(argv)
        self.assertEqual(self._source_count(), storage_rows_first,
                         "重跑不得新增来源（同一文件同一 canonical_key）")

    def _source_count(self) -> int:
        import sqlite3

        conn = sqlite3.connect(str(self.tmp / "persona.db"))
        try:
            return int(conn.execute(
                "SELECT COUNT(*) FROM records WHERE kind = 'Source'").fetchone()[0])
        finally:
            conn.close()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
