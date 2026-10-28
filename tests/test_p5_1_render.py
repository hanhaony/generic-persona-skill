"""P5.1 渲染层（`persona/cli/render.py`）离线测试。

渲染层是**纯函数**：只吃 dataclass、只吐字符串。所以这里不建数据库、不写夹具，
直接构造报告对象——每一条格式规则都能被一句话锁住：

1. 六个分区的顺序固定，空分区必须有 `（无）` + 原因；
2. `single_events` 一律"非稳定特征"，`traits` 里的「归并自 N 条」只在 N>1 时出现；
3. `confidence` 不展示（真实数据恒 0.0，展示出来是假精度）；
4. 「未并入」一行有内容才打印；同名提示**固定在最后两行**；
5. `evidence` 的"未并入档案的证据"小节**永远有标题**（0 条也要有）；
6. `export` 的 Markdown 逐字比对（它是要贴进文档的，多一行空格都算改口径）。

这些断言刻意写得"死"：输出形状就是用户看到的界面，宽松的断言等于没有断言。
"""

from __future__ import annotations

import re
import unittest

from persona.cli.render import (
    render_evidence,
    render_markdown,
    render_persona,
    render_relationship,
    shorten_id,
    strip_evidence_refs,
    unmerged_line,
)
from persona.orchestrator.readmodels import (
    EntityRef,
    EvidenceGroupView,
    EvidenceReport,
    EvidenceRow,
    ItemView,
    PersonaReport,
    RelationshipPerspective,
    RelationshipReport,
    SectionView,
    UnmergedView,
)

HERO_ID = "pe_67e444bc305d459f9b6a9f0367c42930"
OTHER_ID = "pe_a312748f7c2b4e77a0f1c9d2e3b4a5c6"


# ==========================================================================
# 构造器
# ==========================================================================


def _entity(works: tuple[str, ...] = ("红楼梦",), adaptation: str = "曹雪芹原著",
            name: str = "林黛玉", entity_id: str = HERO_ID) -> EntityRef:
    return EntityRef(entity_id=entity_id, display_name=name,
                     created_at="2026-10-18T00:00:00Z",
                     works=list(works), adaptation_version=adaptation)


def _item(text: str, *, sources: int = 1, evidence: int | None = None,
          stability: str = "REPEATED") -> ItemView:
    count = sources if evidence is None else evidence
    return ItemView(text=text, evidence_ids=[f"evd_{i:04d}" for i in range(count)],
                    evidence_count=count, source_count=sources, stability=stability)


def _section(key: str, title: str, items: list[ItemView] | None = None,
             *, is_text: bool = False) -> SectionView:
    return SectionView(key=key, title=title, items=list(items or []), is_text=is_text)


def _sections(**overrides: list[ItemView]) -> list[SectionView]:
    spec = [
        ("traits", "稳定性格", False),
        ("habits", "习惯", False),
        ("expression_style", "说话方式", True),
        ("single_events", "单次行为", False),
        ("background", "背景", True),
        ("experiences", "经历", False),
    ]
    return [_section(key, title, overrides.get(key), is_text=is_text)
            for key, title, is_text in spec]


def _report(**kwargs) -> PersonaReport:
    """一份贴近真实黛玉的档案（数字与 R4.8 库一致）。"""
    defaults = dict(
        entity=_entity(),
        profile_version=1,
        status="OK",
        evidence_total=14,
        source_total=6,
        referenced_evidence=7,
        referenced_sources=3,
        sections=_sections(
            traits=[_item("林黛玉有弱不禁风的一面。", sources=2, evidence=2)],
            expression_style=[_item("林黛玉言辞尖酸刻薄、喜欢怼人。",
                                    sources=1, evidence=2, stability="SINGLE")],
            single_events=[
                _item("林黛玉是个幽默爱笑的少女。", stability="SINGLE"),
                _item("林黛玉生性聪慧灵敏，精通诗词。", stability="SINGLE"),
            ],
        ),
        unmerged=UnmergedView(total=7,
                              by_dimension=[("CANON_FACT", 4), ("RELATION", 3)]),
    )
    defaults.update(kwargs)
    return PersonaReport(**defaults)


def _row(claim: str = "林黛玉是《红楼梦》中的女一号。", **kwargs) -> EvidenceRow:
    defaults = dict(
        evidence_id="evd_7704ab1234567890abcdef1234567890",
        claim=claim, tier="VERBATIM", dimension="CANON_FACT", trait_tag="",
        quote="", source_id="src_1", source_title="百度百科",
        source_url="https://baike.example/item/1",
        retrieved_at="2026-10-18T06:07:08Z", locator="snippet[1:9]", flags=[])
    defaults.update(kwargs)
    return EvidenceRow(**defaults)


# ==========================================================================
# 一、show：头部
# ==========================================================================


class TestShowHeader(unittest.TestCase):
    def test_first_line_is_name_and_id(self):
        out = render_persona(_report()).splitlines()
        self.assertEqual(out[0], f"林黛玉  （{HERO_ID}）")

    def test_second_line_is_work_and_adaptation(self):
        out = render_persona(_report()).splitlines()
        self.assertEqual(out[1], "《红楼梦》 · 改编版本：曹雪芹原著")

    def test_missing_work_still_states_the_adaptation(self):
        report = _report(entity=_entity(works=(), adaptation="原著"))
        out = render_persona(report).splitlines()
        self.assertEqual(out[1], "（未登记作品） · 改编版本：原著")

    def test_no_work_no_adaptation_prints_work_only(self):
        report = _report(entity=_entity(works=("红楼梦",), adaptation=""))
        out = render_persona(report).splitlines()
        self.assertEqual(out[1], "《红楼梦》")

    def test_third_line_carries_version_status_and_counts(self):
        out = render_persona(_report()).splitlines()
        self.assertEqual(out[2], "档案 v1 · 状态 OK · 基于 7 条证据 / 3 个来源")

    def test_insufficient_evidence_is_explained_below_the_header(self):
        report = _report(status="INSUFFICIENT_EVIDENCE")
        out = render_persona(report).splitlines()
        self.assertIn("状态 证据不足", out[2])
        self.assertEqual(out[3], "证据不足：没有任何特征达到 2 个独立来源")


# ==========================================================================
# 二、show：六个分区
# ==========================================================================


class TestShowSections(unittest.TestCase):
    def test_section_titles_keep_their_order(self):
        text = render_persona(_report())
        order = ["稳定性格", "习惯", "说话方式", "单次行为", "背景", "经历"]
        found = [text.index(title) for title in order]
        self.assertEqual(found, sorted(found))

    def test_empty_section_says_none_and_why(self):
        text = render_persona(_report())
        self.assertIn("习惯（0）", text)
        self.assertIn('  （无）本库没有能支撑"习惯"的证据', text)
        self.assertIn("背景（0）", text)
        self.assertIn("经历（0）", text)

    def test_text_section_with_content_has_no_count_but_keeps_evidence_count(self):
        text = render_persona(_report())
        lines = text.splitlines()
        index = lines.index("说话方式")
        self.assertEqual(lines[index + 1], "  · 林黛玉言辞尖酸刻薄、喜欢怼人。")
        self.assertEqual(lines[index + 2], "    1 个来源支持（2 条证据）")

    def test_list_section_header_carries_the_count(self):
        text = render_persona(_report())
        self.assertIn("稳定性格（1）", text)
        self.assertIn("单次行为（2）", text)

    def test_merged_item_explains_how_many_wordings_collapsed(self):
        text = render_persona(_report())
        self.assertIn("  · 林黛玉有弱不禁风的一面。", text)
        self.assertIn("    2 个来源支持 · 归并自 2 条措辞各异的说法", text)

    def test_single_source_item_does_not_claim_merging(self):
        report = _report(sections=_sections(traits=[_item("寡言", sources=1)]))
        text = render_persona(report)
        self.assertIn("    1 个来源支持", text)
        self.assertNotIn("归并自", text)

    def test_single_events_are_always_marked_unstable(self):
        text = render_persona(_report())
        block = text.split("单次行为（2）")[1].split("背景（0）")[0]
        self.assertEqual(block.count("非稳定特征"), 2)

    def test_stable_sections_never_say_unstable(self):
        report = _report(sections=_sections(traits=[_item("寡言", sources=2)]))
        block = render_persona(report).split("稳定性格（1）")[1].split("习惯（0）")[0]
        self.assertNotIn("非稳定特征", block)

    def test_long_single_events_fall_back_to_two_lines(self):
        long_text = "林黛玉任情任性、真诚单纯，想说就说、想生气就生气，不怕得罪人。"
        report = _report(sections=_sections(
            single_events=[_item(long_text, stability="SINGLE")]))
        lines = render_persona(report).splitlines()
        index = lines.index(f"  · {long_text}")
        self.assertEqual(lines[index + 1], "    1 个来源 · 非稳定特征")


# ==========================================================================
# 三、show：未并入 / 页脚 / 同名提示
# ==========================================================================


class TestShowFooterAndSameName(unittest.TestCase):
    def test_unmerged_line_lists_dimensions(self):
        line = unmerged_line(_report())
        self.assertIn("另有 7 条证据未并入档案", line)
        self.assertIn("CANON_FACT 4 条、RELATION 3 条", line)
        self.assertIn("不用于性格推断", line)
        self.assertIn("明细见 persona evidence 林黛玉", line)

    def test_unmerged_line_is_empty_when_nothing_is_left(self):
        self.assertEqual(unmerged_line(_report(unmerged=UnmergedView())), "")

    def test_unmerged_line_is_not_printed_when_zero(self):
        text = render_persona(_report(unmerged=UnmergedView()))
        self.assertNotIn("未并入档案", text)

    def test_more_than_two_dimensions_collapse_into_etc(self):
        report = _report(unmerged=UnmergedView(
            total=6, by_dimension=[("CANON_FACT", 3), ("RELATION", 2), ("BACKGROUND", 1)]))
        self.assertIn("CANON_FACT 3 条、RELATION 2 条、等", unmerged_line(report))

    def test_confidence_never_appears_as_a_value(self):
        """① 号断言：分区里不许出现 `confidence`；它只能出现在页脚的"不展示"说明里。"""
        text = render_persona(_report())
        body = text.split("说明：confidence")[0]
        self.assertNotIn("confidence", body)
        # 夹具里的 confidence 是 0.8 / 0.9 之类；任何一位小数都不该漏出来
        self.assertIsNone(re.search(r"\b0\.\d", body))

    def test_footer_has_all_three_statements(self):
        text = render_persona(_report())
        self.assertIn('"N 个来源"是去重后的独立来源数，不是证据条数', text)
        self.assertIn("confidence 字段未校准", text)
        self.assertIn("可能来自显式指定、也可能是省略后的默认值", text)

    def test_footer_quotes_the_actual_adaptation_value(self):
        text = render_persona(_report(entity=_entity(adaptation="原著")))
        self.assertIn("「改编版本：原著」可能来自显式指定", text)

    def test_missing_refs_are_surfaced(self):
        report = _report(missing_refs=["evd_deadbeef00112233"])
        self.assertIn("⚠️ 有 1 条被档案引用的证据不在本库", render_persona(report))

    def test_same_name_hint_is_the_last_two_lines(self):
        """⑦ 号断言：同名提示必须是**最后两行**（页脚之后），且可复制。"""
        report = _report(other_entities=[
            EntityRef(entity_id=OTHER_ID, display_name="林黛玉")])
        lines = render_persona(report).splitlines()
        self.assertEqual(len(lines[-2:]), 2)
        self.assertEqual(
            lines[-2],
            f"检测到 2 个同名实体，已显示最新（{shorten_id(HERO_ID)}），"
            f"其余：{shorten_id(OTHER_ID)}。")
        self.assertEqual(lines[-1],
                         f"用 persona show {shorten_id(OTHER_ID)} 查看另一份。")

    def test_same_name_hint_says_when_the_shown_one_is_not_the_newest(self):
        report = _report(chosen_is_latest=False,
                         other_entities=[EntityRef(entity_id=OTHER_ID,
                                                   display_name="林黛玉")])
        lines = render_persona(report).splitlines()
        self.assertIn("已显示最近一份有档案的", lines[-2])

    def test_no_same_name_hint_without_candidates(self):
        self.assertNotIn("同名实体", render_persona(_report()))

    def test_empty_profile_tells_you_what_to_run(self):
        report = _report(profile_missing=True, profile_version=None, status="MISSING",
                         sections=[], referenced_evidence=0, referenced_sources=0)
        text = render_persona(report)
        self.assertIn("没有档案：本库有这个名字的实体，但还没有生成人格档案", text)
        self.assertIn('persona run --names "林黛玉" --work-title <作品>', text)
        self.assertNotIn("稳定性格", text)


# ==========================================================================
# 四、export --format md
# ==========================================================================

MD_STANDARD = """## 林黛玉

> 《红楼梦》 · 改编版本：曹雪芹原著
> 档案 v1 · 状态 OK · 基于 7 条证据 / 3 个来源
> 来源数为去重后的独立来源数；"非稳定特征"= 只有 1 个来源提到。

### 稳定性格

- **林黛玉有弱不禁风的一面。**（2 个来源支持）

### 习惯

（本库没有能支撑"习惯"的证据）

### 说话方式

- **林黛玉言辞尖酸刻薄、喜欢怼人。**（1 个来源支持）

### 单次行为

- 林黛玉是个幽默爱笑的少女。（1 个来源，非稳定特征）
- 林黛玉生性聪慧灵敏，精通诗词。（1 个来源，非稳定特征）

### 背景

（本库没有能支撑"背景"的证据）

### 经历

（本库没有能支撑"经历"的证据）

> 另有 7 条证据未并入档案（CANON_FACT 4 条、RELATION 3 条），不用于性格推断。
"""


class TestExportMarkdown(unittest.TestCase):
    def test_markdown_is_exact(self):
        self.assertEqual(render_markdown(_report()), MD_STANDARD)

    def test_markdown_marks_single_events_as_unstable(self):
        text = render_markdown(_report())
        self.assertIn("- 林黛玉是个幽默爱笑的少女。（1 个来源，非稳定特征）", text)

    def test_markdown_keeps_confidence_out(self):
        self.assertNotIn("confidence", render_markdown(_report()))

    def test_markdown_for_empty_profile_says_why(self):
        report = _report(profile_missing=True, profile_version=None, status="MISSING",
                         sections=[], referenced_evidence=0, referenced_sources=0)
        text = render_markdown(report)
        self.assertIn("**证据不足：本库没有为「林黛玉」生成任何特征。**", text)
        self.assertIn("但还没有生成人格档案", text)

    def test_markdown_for_profile_without_any_stable_feature(self):
        report = _report(referenced_evidence=0, referenced_sources=0,
                         sections=_sections())
        text = render_markdown(report)
        self.assertIn("**证据不足：本库没有为「林黛玉」生成任何特征。**", text)
        self.assertIn("没有一条达到\"稳定特征\"门槛（需 ≥2 个独立来源）", text)
        self.assertIn("另有 7 条证据未并入档案", text)


# ==========================================================================
# 五、show-relationship
# ==========================================================================


def _relationship(**kwargs) -> RelationshipReport:
    a = EntityRef(entity_id=HERO_ID, display_name="林黛玉")
    b = EntityRef(entity_id=OTHER_ID, display_name="贾宝玉")
    defaults = dict(
        relationship_id="rlp_8abf4a691234567890abcdef12345678",
        version=1, status="OK", cardinality="PAIR", participants=[a, b],
        perspectives=[
            RelationshipPerspective(from_id=HERO_ID, from_name="林黛玉",
                                    to_id=OTHER_ID, to_name="贾宝玉",
                                    items=[_item("贾宝玉与林黛玉青梅竹马，互为知己。",
                                                 stability="SINGLE")]),
            RelationshipPerspective(from_id=OTHER_ID, from_name="贾宝玉",
                                    to_id=HERO_ID, to_name="林黛玉",
                                    items=[_item("林黛玉和贾宝玉产生了生死与共的爱情。",
                                                 stability="SINGLE")]),
        ],
        shared_experiences=[_item("两人一起葬花。")],
        interaction_patterns=[],
        emotional_expression_habits=[_item("说话总带试探。")],
        asymmetry_notes=[_item(f"覆盖对 ['{HERO_ID}', '{OTHER_ID}'] 存在 2 种不同说法。")],
    )
    defaults.update(kwargs)
    return RelationshipReport(**defaults)


class TestRelationshipRender(unittest.TestCase):
    def test_header_states_cardinality_version_and_status(self):
        first = render_relationship(_relationship()).splitlines()[0]
        self.assertEqual(first, "林黛玉 ↔ 贾宝玉  （PAIR · 档案 v1 · 状态 OK）")

    def test_both_perspectives_are_rendered_separately(self):
        text = render_relationship(_relationship())
        self.assertIn("林黛玉 眼中的 贾宝玉（1）", text)
        self.assertIn("贾宝玉 眼中的 林黛玉（1）", text)

    def test_empty_relationship_sections_say_none(self):
        text = render_relationship(_relationship())
        self.assertIn("互动模式（0）\n  （无）", text)

    def test_shared_experiences_are_not_called_unstable(self):
        """共同经历是**事件**，给它挂"非稳定特征"读起来像在说两人感情不稳。"""
        text = render_relationship(_relationship())
        block = text.split("共同经历（1）")[1].split("互动模式（0）")[0]
        self.assertIn("两人一起葬花。", block)
        self.assertNotIn("非稳定特征", block)

    def test_perspective_items_are_marked_when_single_source(self):
        text = render_relationship(_relationship())
        block = text.split("林黛玉 眼中的 贾宝玉（1）")[1].split("贾宝玉 眼中的")[0]
        self.assertIn("非稳定特征", block)

    def test_raw_entity_ids_are_replaced_by_names(self):
        text = render_relationship(_relationship())
        self.assertIn("覆盖对 ['林黛玉', '贾宝玉']", text)
        self.assertNotIn(HERO_ID, text)
        self.assertNotIn("未知实体", text)

    def test_unresolvable_entity_id_is_kept_and_flagged(self):
        unknown = "pe_ffffffffffffffffffffffffffffffff"
        report = _relationship(asymmetry_notes=[
            _item(f"覆盖对 ['{unknown}'] 存在 2 种说法。")])
        text = render_relationship(report)
        self.assertIn("⚠️ 未知实体", text)
        self.assertIn(unknown, text)   # 原样保留，不截断、不猜

    def test_footer_defines_unstable(self):
        text = render_relationship(_relationship())
        self.assertIn('"非稳定特征"= 只有 1 个来源提到', text)

    def test_group_dynamics_are_rendered_when_present(self):
        from persona.orchestrator.readmodels import RelationshipReport as R
        report = _relationship(group_dynamics={"联盟": [_item("两人结盟。")]})
        self.assertIsInstance(report, R)
        text = render_relationship(report)
        self.assertIn("群体互动", text)
        self.assertIn("联盟（1）", text)


# ==========================================================================
# 六、evidence
# ==========================================================================


def _evidence_report(**kwargs) -> EvidenceReport:
    defaults = dict(
        entity=_entity(),
        evidence_total=14,
        source_total=6,
        groups=[EvidenceGroupView(
            title="林黛玉有弱不禁风的一面。", section="稳定性格",
            source_count=2, evidence_count=2,
            items=[_row(claim="他自幼体弱多病", trait_tag="frail"),
                   _row(claim="也有弱不禁风的一面", trait_tag="frail")]),
         EvidenceGroupView(
             title="林黛玉言辞尖酸刻薄、喜欢怼人。", section="说话方式",
             source_count=1, evidence_count=2,
             items=[_row(claim="尖酸刻薄", dimension="TRAIT", trait_tag="caustic")])],
        unmerged=[_row(claim="林黛玉是《红楼梦》中的女一号。")],
    )
    defaults.update(kwargs)
    return EvidenceReport(**defaults)


class TestEvidenceRender(unittest.TestCase):
    def test_section_heading_is_printed_once(self):
        text = render_evidence(_evidence_report())
        self.assertEqual(text.count("■ 稳定性格"), 1)
        self.assertEqual(text.count("■ 说话方式"), 1)

    def test_item_line_carries_source_count(self):
        text = render_evidence(_evidence_report())
        self.assertIn("  · 林黛玉有弱不禁风的一面。    2 个来源", text)

    def test_item_line_shows_evidence_count_when_it_differs(self):
        text = render_evidence(_evidence_report())
        self.assertIn("1 个来源（2 条证据）", text)

    def test_evidence_rows_quote_and_trace_to_url(self):
        text = render_evidence(_evidence_report())
        self.assertIn("      ├ evd_7704…  VERBATIM  [CANON_FACT / frail]", text)
        self.assertIn("      │   《百度百科》 https://baike.example/item/1", text)
        self.assertIn("      │   定位 snippet[1:9] · 抓取于 2026-10-18", text)

    def test_verbatim_quote_is_printed_below_the_row(self):
        report = _evidence_report(groups=[EvidenceGroupView(
            title="说话方式里的刺", section="说话方式", source_count=1, evidence_count=1,
            items=[_row(dimension="TRAIT", trait_tag="caustic",
                        quote="林黛玉的尖酸刻薄，喜欢怼人")])], unmerged=[])
        self.assertIn('          "林黛玉的尖酸刻薄，喜欢怼人"', render_evidence(report))

    def test_last_row_in_a_group_uses_the_corner_branch(self):
        text = render_evidence(_evidence_report())
        block = text.split("■ 稳定性格")[1].split("■ 说话方式")[0]
        self.assertIn("├ evd_7704…", block)
        self.assertIn("└ evd_7704…", block)

    def test_unmerged_section_always_has_a_title(self):
        """⑤ 号断言：0 条也要有标题——否则"全都并入"和"没查别的"读不出区别。"""
        report = _evidence_report(unmerged=[])
        text = render_evidence(report)
        self.assertIn("■ 未并入档案的证据（0 条，全部证据都已并入上面的条目）", text)

    def test_unmerged_section_counts_rows(self):
        text = render_evidence(_evidence_report())
        self.assertIn("■ 未并入档案的证据（1 条，均为背景事实/关系陈述，未构成性格特征）", text)

    def test_unmerged_section_explains_missing_profile(self):
        report = _evidence_report(groups=[], unmerged=[], profile_missing=True)
        text = render_evidence(report)
        self.assertIn("（0 条，本库还没有针对该人物的人格档案）", text)
        self.assertIn("（本库还没有「林黛玉」的人格档案：以上证据全部未并入）", text)

    def test_trait_filter_is_stated_with_hit_count(self):
        report = _evidence_report(
            trait_filter="caustic",
            groups=[EvidenceGroupView(
                title="林黛玉言辞尖酸刻薄、喜欢怼人。", section="说话方式",
                source_count=1, evidence_count=1,
                items=[_row(dimension="TRAIT", trait_tag="caustic")])],
            unmerged=[_row(dimension="TRAIT", trait_tag="caustic")])
        self.assertIn("筛选：trait_tag=caustic（命中 2 条）", render_evidence(report))

    def test_unknown_trait_lists_available_tags(self):
        report = _evidence_report(groups=[], unmerged=[], trait_filter="curious",
                                  available_tags=[("frail", 2), ("caustic", 1)])
        text = render_evidence(report)
        self.assertIn("没有 trait_tag=curious 的证据。「林黛玉」当前可用标签：", text)
        self.assertIn("frail(2)  caustic(1)", text)

    def test_warning_flags_are_visible(self):
        report = _evidence_report(groups=[EvidenceGroupView(
            title="说话方式里的刺", section="说话方式", source_count=1, evidence_count=1,
            items=[_row(dimension="TRAIT", trait_tag="caustic",
                        flags=["TRAIT_TAG_DIMENSION_MISMATCH"])])], unmerged=[])
        self.assertIn("⚠️ TRAIT_TAG_DIMENSION_MISMATCH", render_evidence(report))


# ==========================================================================
# 七、小工具
# ==========================================================================


class TestHelpers(unittest.TestCase):
    def test_shorten_id_keeps_prefix_and_four_chars(self):
        self.assertEqual(shorten_id(HERO_ID), "pe_67e4…")
        self.assertEqual(shorten_id("evd_7704ab1234"), "evd_7704…")

    def test_shorten_id_passes_short_or_odd_values_through(self):
        self.assertEqual(shorten_id("pe_1234"), "pe_1234")
        self.assertEqual(shorten_id("nodash"), "nodash")
        self.assertEqual(shorten_id(""), "")


class TestStripEvidenceRefs(unittest.TestCase):
    """P0 破壁修复：`[ev:...]` 引用标注只进 TurnView 字段，不进用户视野。"""

    def test_trailing_ref_is_stripped(self):
        self.assertEqual(
            strip_evidence_refs("我素来如此 [ev:evd_abc123]。"),
            "我素来如此。")

    def test_ref_without_space_is_stripped(self):
        self.assertEqual(
            strip_evidence_refs("我素来如此[ev:evd_abc123]。"),
            "我素来如此。")

    def test_multiple_refs_all_stripped(self):
        self.assertEqual(
            strip_evidence_refs("其一如此 [ev:evd_a]，其二亦然 [ev:evd_b]。"),
            "其一如此，其二亦然。")

    def test_no_ref_returns_text_unchanged(self):
        text = "寻常回答，一个标注也没有。"
        self.assertEqual(strip_evidence_refs(text), text)

    def test_malformed_ref_is_still_stripped(self):
        """非标准 ID（打越界标记那种）同样不许出现在用户视野里。"""
        self.assertEqual(strip_evidence_refs("我说 [ev:不存在] 而已。"),
                         "我说 而已。")

    def test_punctuation_spacing_is_tightened(self):
        self.assertEqual(
            strip_evidence_refs("其一如此[ev:evd_a] ，其二亦然[ev:evd_b] 。"),
            "其一如此，其二亦然。")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
