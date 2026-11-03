"""P4.8 D-288：抽取提示词必须给出「显示名 = 主体 ID」映射。

## 这一轮修的是什么

R4.5 实测：26 条证据里 **25 条主体错绑**——15 条讲贾宝玉的说法全挂到林黛玉
ID 上、5 条讲林黛玉的全挂到贾宝玉 ID 上，只有那条双主体的"宝玉与黛玉青梅竹马"
是对的。落库后两份档案的 `traits` **互相张冠李戴**：

| 档案 | 实际内容 | 是谁的 |
|---|---|---|
| 林黛玉 | "贾宝玉厌恶四书和八股文…称作禄蠹" | 贾宝玉的 |
| 贾宝玉 | "林黛玉被描述为富有诗意美…" | 林黛玉的 |

**最危险的地方是条目数完全对得上**（黛玉 1+1=2、宝玉 1+5=6，8 组 == 8 条目）
——只对数量验收会判通过。

根因不是模型笨，是**没给它判断依据**：`_system_prompt` 只写

    本次允许使用的主体 ID：['pe_cb22…', 'pe_b815…']

`targets` 里明明带 `display_name`，却没写进提示词。模型面对两个裸 ID，
只能按列表顺序猜。R4.4 的 22/22 全对是**无保证的正确**——当轮"材料目标 =
材料内容"，按顺序蒙对了；R4.5 的林黛玉侧 5 份检索结果里 4 份是《红楼梦》总页
（大面积讲宝玉），材料与目标一错位就全崩。

## 本文件钉住的东西

1. **提示词契约**：每个 target 的显示名与 ID 必须**成对**出现在 system message 里，
   且明确禁止"按列表顺序填""按检索目标整份归给一个人"；
2. **口径唯一**：`target_labels_of` 与 `target_ids_of` 同一顺序、同一过滤规则
   （两者漂移会让白名单与幂等键各算各的）；
3. **缺名字必须显式**：`display_name` 为空时要发 `TARGET_NAME_MISSING` 告警，
   不能静默退化成裸 ID；
4. **集成**：给定多目标材料，模型按提示词的映射填主体 ⇒ 每条 claim 的
   `subject_entity_ids` 与**内容**一致（不是与材料归属一致、也不是与列表顺序一致），
   并且两份档案互不串味；
5. **区分力对照**：同一批材料换一个"只会按列表第一个填"的模型，必须复现
   R4.5 的张冠李戴——证明上面那条测试真的抓得住这个 bug，而不是碰巧通过。

全部离线：本地桩 Provider，不触网、不调真实模型、不产生费用。
"""

from __future__ import annotations

import json
import re
import unittest

from persona.core.context import new_call_context
from persona.core.result import Ok, unwrap
from persona.modules.extraction import (
    PROMPT_VERSION,
    WARNING_TARGET_NAME_MISSING,
    ProviderClaimExtractor,
    target_ids_of,
    target_labels_of,
)
from persona.modules.research import PersonQueryTarget
from persona.modules.trait_vocabulary import zh_classical_vocabulary
from persona.core.types import SourceMaterial
from persona.orchestrator.business import BusinessOrchestrator

_stability_groups = BusinessOrchestrator._stability_groups
_build_persona_draft = BusinessOrchestrator._build_persona_draft

PROJECT = "prj_p48_subject_mapping"

PE_DAIYU = "pe_00000000000000000000000000d1"
PE_BAOYU = "pe_00000000000000000000000000b1"


# ==========================================================================
# 夹具
# ==========================================================================

def _targets() -> list[PersonQueryTarget]:
    return [
        PersonQueryTarget(entity_id=PE_DAIYU, display_name="林黛玉"),
        PersonQueryTarget(entity_id=PE_BAOYU, display_name="贾宝玉"),
    ]


def _material(key: str, text: str, subjects: list[str]) -> SourceMaterial:
    return SourceMaterial(
        source_id=f"src_{key}", title=f"材料 {key}", url=f"https://example.invalid/{key}",
        canonical_key=key, snippet=text, subject_entity_ids=list(subjects),
        quality_status="VALID")          # 门禁结论的合法值（"OK" 会被判不可用）


#: 材料归属与内容**故意错位**，照抄 R4.5 的真实情形：
#: 「林黛玉 红楼梦」这条检索带回来的 5 份材料里 4 份都在大面积讲贾宝玉。
MATERIALS = [
    _material("m-daiyu",
              "林黛玉博览群书，学识渊博，有多种才能。"
              "贾宝玉厌恶四书和八股文，把追逐科举仕途经济的封建文人称作“禄蠹”。",
              [PE_DAIYU]),
    _material("m-baoyu",
              "林黛玉禀赋多愁，才藻绝伦。"
              "贾宝玉性格的核心是平等待人、尊重个性。",
              [PE_BAOYU]),
]

#: 模型要输出的四条候选（`subject_entity_ids` 由桩模型自己填）。
RAW_CLAIMS = [
    {"key": "c1", "material_key": "m-daiyu",
     "claim": "贾宝玉厌恶四书和八股文，把追逐科举仕途经济的封建文人称作“禄蠹”。",
     "tier": "VERBATIM", "verbatim_quote": "贾宝玉厌恶四书和八股文",
     "dimension": "TRAIT", "trait_tag": "rebellious"},
    {"key": "c2", "material_key": "m-daiyu",
     "claim": "林黛玉博览群书，学识渊博，有多种才能。",
     "tier": "VERBATIM", "verbatim_quote": "林黛玉博览群书，学识渊博",
     "dimension": "TRAIT", "trait_tag": "knowledgeable"},
    {"key": "c3", "material_key": "m-baoyu",
     "claim": "林黛玉禀赋多愁，才藻绝伦。",
     "tier": "VERBATIM", "verbatim_quote": "林黛玉禀赋多愁，才藻绝伦",
     "dimension": "TRAIT", "trait_tag": "melancholy"},
    {"key": "c4", "material_key": "m-baoyu",
     "claim": "贾宝玉性格的核心是平等待人、尊重个性。",
     "tier": "VERBATIM", "verbatim_quote": "贾宝玉性格的核心是平等待人",
     "dimension": "TRAIT", "trait_tag": "egalitarian"},
]

#: 每条候选**按内容**应当归属的主体（与它来自哪份材料无关）。
EXPECTED_SUBJECTS = {
    "c1": [PE_BAOYU],     # 来自黛玉侧材料，但讲的是宝玉
    "c2": [PE_DAIYU],
    "c3": [PE_DAIYU],     # 来自宝玉侧材料，但讲的是黛玉
    "c4": [PE_BAOYU],
}

_NAME_TO_ID_RE = re.compile(r"^- (.+?) = (pe_[0-9a-zA-Z]+)\s*$", re.M)


class _MappingAwareModel:
    """守规矩的桩模型：**读提示词里的映射**，按 claim 内容里出现的人名填 ID。

    它模拟的是"R4.5 之后、提示词补上映射以后"的模型行为——不猜顺序、
    不按材料归属，只看这句话在讲谁。这正是 D-288 要买到的行为。
    """

    offline = True

    def __init__(self) -> None:
        self.calls = 0
        self.seen_systems: list[str] = []

    def chat(self, ctx, request):
        from persona.providers.base import ChatResponse, UsageReport
        self.calls += 1
        system = request.messages[0].content
        self.seen_systems.append(system)
        mapping = dict(_NAME_TO_ID_RE.findall(system))
        claims = []
        for raw in RAW_CLAIMS:
            text = raw["claim"]
            ids = [eid for name, eid in mapping.items() if name in text]
            claims.append({**raw, "subject_entity_ids": ids})
        return Ok(ChatResponse(
            text=json.dumps({"claims": claims}, ensure_ascii=False),
            usage=UsageReport(requests=1, tokens_in=100, tokens_out=100)))


class _BlindOrderModel(_MappingAwareModel):
    """对照组：**不看名字、按列表第一个填**——复现 R4.5 的猜法。

    它存在的唯一理由是证明 `TestSubjectBinding` 有区分力：
    如果这个模型也能"通过"，说明测试根本抓不住主体错绑。
    """

    def chat(self, ctx, request):
        from persona.providers.base import ChatResponse, UsageReport
        self.calls += 1
        ids = [eid for _name, eid in _NAME_TO_ID_RE.findall(
            request.messages[0].content)]
        first = ids[0] if ids else ""
        claims = [{**raw, "subject_entity_ids": [first]} for raw in RAW_CLAIMS]
        return Ok(ChatResponse(
            text=json.dumps({"claims": claims}, ensure_ascii=False),
            usage=UsageReport(requests=1, tokens_in=100, tokens_out=100)))


#: 用 **L2 词表**：夹具里的 `rebellious` / `egalitarian` 是 L2 新增标签，
#: 拿 L1 跑会被 `TRAIT_TAG_NOT_IN_VOCABULARY` 拒收（与本文件要测的主体无关）。
def _extractor(model) -> ProviderClaimExtractor:
    return ProviderClaimExtractor(model, vocabulary=zh_classical_vocabulary())


class _Item:
    """`_build_persona_draft` 只读这些属性（`CandidateClaim` 没有 `.id`）。"""

    def __init__(self, candidate) -> None:
        self.id = candidate.key
        self.claim = candidate.claim
        self.dimension = candidate.dimension
        self.subject_entity_ids = list(candidate.subject_entity_ids)
        self.source_id = candidate.source_id
        self.stability = "UNKNOWN"
        self.effective_stability = "UNKNOWN"
        self.canon_level = ""
        self.applicable_variant_ids = []
        self.trait_tag = candidate.trait_tag


def _run(model):
    ctx = new_call_context(project_id=PROJECT, actor="test")
    return unwrap(_extractor(model).extract(ctx, MATERIALS, targets=_targets()))


# ==========================================================================
# 1. 提示词契约
# ==========================================================================

class TestPromptNamesTargets(unittest.TestCase):

    def test_every_target_name_is_paired_with_its_id(self):
        """核心契约：`名字 = ID` 成对出现，不是一串裸 ID。

        R4.5 的提示词只有 `['pe_cb22…', 'pe_b815…']`——模型没有任何依据判断
        "贾宝玉"该填哪个。这条测试锁住"名字必须在 ID 旁边"。
        """
        request = ProviderClaimExtractor.build_request(MATERIALS, _targets())
        system = request.messages[0].content
        for target in _targets():
            self.assertIn(f"{target.display_name} = {target.entity_id}", system)
        #: 旧格式的裸 ID 列表形态**不许回来**：一旦回来，模型又只能猜。
        self.assertNotIn("本次允许使用的主体 ID：[", system)

    def test_prompt_forbids_filling_by_list_order_or_by_source(self):
        """要写出**后果**，不只写规则（D-276 的经验）。

        R4.5 的错法有两种形态：按列表顺序填、以及"这份材料是为某人检索的
        就把整份的都算给他"。两句都得点名，否则模型只会避开其中一种。
        """
        system = ProviderClaimExtractor.build_request(
            MATERIALS, _targets()).messages[0].content
        self.assertIn("材料讲谁就填谁", system)
        self.assertIn("不要按列表顺序填", system)
        self.assertIn("这份材料是为某个目标", system)
        self.assertIn("张冠李戴", system)

    def test_prompt_tells_model_to_skip_people_outside_the_list(self):
        """材料里出现"薛宝钗"这类不在 targets 里的人时，不能让模型硬塞。

        旧行为下模型只能二选一：拒收（丢数据）或强行归给列表里的人
        （R4.5 正是后者）。提示词必须明说"不要输出那条 claim"。
        """
        system = ProviderClaimExtractor.build_request(
            MATERIALS, _targets()).messages[0].content
        self.assertIn("不在上面这个列表里", system)
        self.assertIn("不要输出那条 claim", system)

    def test_prompt_version_was_bumped(self):
        """提示词文本变了，版本必须 +1——幂等键由它参与生成。"""
        self.assertEqual(PROMPT_VERSION, "g2-extract-2026-11-03a")
        self.assertNotEqual(PROMPT_VERSION, "g2-extract-2026-10-18c")


# ==========================================================================
# 2. 口径唯一（labels 与 ids 不许漂移）
# ==========================================================================

class TestTargetLabels(unittest.TestCase):

    def test_labels_and_ids_share_one_order(self):
        """两个口径必须一一对应：`target_ids_of` 就是 labels 的 ID 列。

        漂移的后果不是"重复代码"，而是**白名单与幂等键各算各的**——
        一次 dict 形式的目标就能让白名单静默消失（`target_ids_of` 的注释
        已经记过这类事故）。这条测试把它钉死。
        """
        self.assertEqual([e for _n, e in target_labels_of(_targets())],
                         target_ids_of(_targets()))

    def test_dict_targets_are_supported(self):
        """dict 形态也要认（脚本与旧调用方会传 dict）。"""
        targets = [{"entity_id": "pe_x", "display_name": "甲"},
                   {"entity_id": "pe_y", "name": "乙"}]
        self.assertEqual(target_labels_of(targets), [("甲", "pe_x"), ("乙", "pe_y")])
        self.assertEqual(target_ids_of(targets), ["pe_x", "pe_y"])

    def test_missing_name_is_kept_as_empty_not_dropped(self):
        """缺名字时保留这一项（返回空串），不能把目标整个丢掉。

        丢掉会让提示词里少一个主体 ⇒ 模型更没依据；且"少了谁"在返回值上
        看不出来。保留空串才能让调用方显式处理（下面那条测警告）。
        """
        targets = [PersonQueryTarget(entity_id="pe_z")]
        self.assertEqual(target_labels_of(targets), [("", "pe_z")])
        self.assertEqual(target_ids_of(targets), ["pe_z"])


# ==========================================================================
# 3. 缺名字要显式告警
# ==========================================================================

class TestMissingDisplayName(unittest.TestCase):

    def test_extraction_warns_when_a_target_has_no_display_name(self):
        """没有显示名 ⇒ 提示词只剩裸 ID ⇒ 主体归属靠猜。**不能静默**。"""
        ctx = new_call_context(project_id=PROJECT, actor="test")
        targets = [PersonQueryTarget(entity_id=PE_DAIYU)]     # 无 display_name
        outcome = unwrap(_extractor(_MappingAwareModel()).extract(
            ctx, MATERIALS, targets=targets))
        codes = [w.code for w in outcome.warnings]
        self.assertIn(WARNING_TARGET_NAME_MISSING, codes)
        message = [w.message for w in outcome.warnings
                   if w.code == WARNING_TARGET_NAME_MISSING][0]
        self.assertIn(PE_DAIYU, message)
        self.assertIn("display_name", message)

    def test_prompt_says_the_name_is_missing_instead_of_printing_an_empty_pair(self):
        """提示词里不能出现 `-  = pe_xxx`（空名字），要写成显式占位。"""
        system = ProviderClaimExtractor.build_request(
            MATERIALS, [PersonQueryTarget(entity_id="pe_z")]).messages[0].content
        self.assertIn("（名称未提供） = pe_z", system)


# ==========================================================================
# 4. 集成：主体跟着内容走，档案不串味
# ==========================================================================

class TestSubjectBinding(unittest.TestCase):

    def test_claim_subject_follows_content_not_the_material_it_came_from(self):
        """**用户要求的核心用例**：多目标材料下，claim 的主体与内容一致。

        两条故意的陷阱：
          - `c1` 来自"黛玉侧"材料，但内容是贾宝玉的 ⇒ 必须挂宝玉；
          - `c3` 来自"宝玉侧"材料，但内容是林黛玉的 ⇒ 必须挂黛玉。

        R4.5 的错法正是"按材料归属整份算给目标"，这条直接对着它。
        """
        outcome = _run(_MappingAwareModel())
        by_key = {c.key: c for c in outcome.claims}
        self.assertEqual(set(by_key), set(EXPECTED_SUBJECTS))
        for key, expected in EXPECTED_SUBJECTS.items():
            self.assertEqual(by_key[key].subject_entity_ids, expected,
                             f"{key} 的主体应跟着内容走：{by_key[key].claim}")

    def test_profiles_do_not_cross_contaminate(self):
        """档案层不串味：黛玉的条目里不出现"贾宝玉"，反之亦然。

        这是 R4.5 的**直接回归**——那轮黛玉的 `traits` 写着"贾宝玉厌恶四书"。
        """
        outcome = _run(_MappingAwareModel())
        stabilities = {c.key: "REPEATED" for c in outcome.claims}

        for entity_id, name, other in ((PE_DAIYU, "林黛玉", "贾宝玉"),
                                       (PE_BAOYU, "贾宝玉", "林黛玉")):
            #: `_build_persona_draft` 的契约是**调用方先按主体筛好**
            #: （生产链路用 `by_entity[entity_id]`，双主体证据会同时进两边）。
            items = [_Item(c) for c in outcome.claims
                     if entity_id in c.subject_entity_ids]
            draft = _build_persona_draft(entity_id, items, "FICTIONAL_CHARACTER",
                                         stabilities=stabilities,
                                         vocabulary=zh_classical_vocabulary())
            texts = [i.text for bucket in (draft.traits, draft.habits,
                                           draft.single_events)
                     for i in bucket]
            self.assertTrue(texts, f"{name} 应当有自己的条目")
            joined = " ".join(texts)
            self.assertIn(name, joined)
            self.assertNotIn(other, joined,
                             f"{name} 的档案里混进了 {other} 的说法：{joined}")

    def test_the_test_itself_would_catch_the_r45_swap(self):
        """**区分力对照**：换成"按列表第一个填"的模型，必须复现张冠李戴。

        如果这条也"通过"（即盲填模型产出的档案也干净），说明上面那条测试
        根本抓不住 D-288——那样它只是装饰。
        """
        outcome = _run(_BlindOrderModel())
        stabilities = {c.key: "REPEATED" for c in outcome.claims}
        items = [_Item(c) for c in outcome.claims
                 if PE_DAIYU in c.subject_entity_ids]
        draft = _build_persona_draft(PE_DAIYU, items,
                                     "FICTIONAL_CHARACTER",
                                     stabilities=stabilities,
                                     vocabulary=zh_classical_vocabulary())
        joined = " ".join(i.text for bucket in (draft.traits, draft.habits,
                                               draft.single_events)
                          for i in bucket)
        self.assertIn("贾宝玉", joined,
                      "盲填模型本应把宝玉的说法塞进黛玉档案；没塞说明夹具失效了")


if __name__ == "__main__":
    unittest.main()
