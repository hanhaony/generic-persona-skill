"""P4.5-A 端到端验证运行器（一次性、人工执行、**不进 unittest 收集**）。

    python scripts/e2e_p4_5a.py --dry                      # 预检 + A11 负样例 + 双路径诊断（**绝不发请求**）
    python scripts/e2e_p4_5a.py --a11-only                 # 只跑 A11 门禁负样例（零成本）
    python scripts/e2e_p4_5a.py --run                      # 真实执行**编排器路径**（1 次真实调用，会计费）
    python scripts/e2e_p4_5a.py --run --path=script        # 真实执行脚本侧装配（对照用，同样 1 次调用）

# 方案（P4.5-A 已拍板 = 方案 B）

桩检索（返回合成虚构正文，**门禁判真**）+ **真实 DeepSeek 抽取** + 真实证据登记 + 真实门禁
+ 真实人格档案。数据源为桩，其余全部是产品代码。

G4（已修复，本文件保留验证）
---------------------------

**原缺陷**：`materials_from_extracts()`（唯一会把 `quality_status=VALID` 带给
抽取器的函数）**从未被产品代码调用**；`BusinessOrchestrator._step_research`
只记住 `research.materials`（检索摘要，`quality_status=""`）。因此**编排器路径**
在 `require_gate_verdict=true` 下会把每一份材料都判 `MATERIAL_UNDECLARED`，
**模型永远不会被调用**（"抓了正文却交不出去"的死局）。

**修复**：`_step_research` 现在调 `materials_from_extracts()`，把过门禁的抓取
正文转成材料并绑定 `source_id`，同一来源的检索摘要被正文取代（见
`BusinessOrchestrator._material_flow`）。G5（抽取器 `warnings` 被编排器丢弃）
同步修复。

本脚本的 [5] 段用**桩 Provider、零成本**把修复后的行为摆出来：

| 用例 | 期望 |
| --- | --- |
| 5a 单人物 | 材料流非空、模型被调用 1 次、证据非空；整体仍 `PARTIAL`，因为关系/对话阶段按语义跳过（参与者 <2），与 G4 无关 |
| 5b 双人物 | 五阶段全 `EXECUTED` → 整体 `SUCCEEDED`（**不再是 PARTIAL**） |

两条路径的现状：

| 路径 | 材料交接 | 能否到达模型 | 真实运行 |
| --- | --- | --- | --- |
| `--path=orchestrator`（**默认**） | 产品代码 `_material_flow` | 能（G4 已修） | **已接线**，`--run` 默认走它 |
| `--path=script` | 脚本显式调 `materials_from_extracts()` 并绑 `source_id` | 能 | 已接线（对照用） |

**为什么 `--run` 默认走编排器**：编排器路径是**产品代码**端到端（研究 → 抽取 →
证据登记 → 门禁 → 人格档案全部由 `BusinessOrchestrator` 走），脚本只观测与断言，
不替产品代码补任何一环。验证强度高于脚本侧装配。

`--run` 还会做两件"事后举证"的事（都**不新增真实调用**）：

1. **原始响应落盘**：`RawResponseCapture` 包住 Provider 的**唯一网络出口**
   `_urlopen`，把原始 HTTP 响应体写到 `<dump>.raw.json`；请求头**只记名不记值**
   （`Authorization` 的值就是密钥）。
2. **幂等重跑**（见下方 `[7b]`）：同一条链再跑一次，验证两道闸门都在 Provider
   之前 —— ① 发送层幂等守卫（同一意图 → 幂等键相同）；② 预算闸门
   （`max_calls` 用满 → `BUDGET_EXCEEDED`，可恢复暂停）。断言：真实调用次数**不增加**。

**真实运行走单人物（阿雾）**，理由是"零桩掩盖"：

- 单人物请求下关系 / 对话阶段按语义 `SKIPPED`（参与者 < 2），**链上没有任何
  桩对话 Provider 被调用**；真正执行的阶段全部是产品代码 + 真实模型；
- 双人物请求虽然能报 `SUCCEEDED`，但 1 次调用授权只够抽取，对话阶段必须换成
  桩 Provider —— 那会让"SUCCEEDED"里掺进桩产物，不如单人物诚实。
  双人物仍保留在 `--dry` 的 [5b] 诊断里（用桩模型、零成本）。
- 数据源是桩：这是方案 B 的既定前提（合成人物在公网不存在，真实检索拿不到东西）。

# 省钱与安全纪律

1. `max_retries=0`（重试 = 再花一次钱）；`max_calls=1`；单任务/总上限 0.25 USD；
2. 密钥只在请求头；**任何输出行出现密钥子串立即终止**；
3. 失败**不重试**；费用未知记 `UNKNOWN`，**绝不记 0**；
4. 账本**新建独立实例**，不创建 / 不重置 / 不清空任何账本文件；
5. `--run` 是**唯一**会发真实请求的模式；`--dry` / `--a11-only` 物理上不联网。
"""

from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.config import load_settings                                   # noqa: E402
from persona.config.spec import ResearchLimits                             # noqa: E402
from persona.core.context import CallContext, new_call_context              # noqa: E402
from persona.core.result import (                                          # noqa: E402
    Err,
    Ok,
    UsageDelta,
    is_err,
    is_ok,
)
from persona.core.types import (                                           # noqa: E402
    MAX_QUERY_LIMIT,
    Query,
    SourceMaterial,
    UsageReport,
)
from persona.core.usage_ledger import ExternalUsageLedger                   # noqa: E402
from persona.modules.dialogue import DialogueDeps, DialogueService          # noqa: E402
from persona.modules.evidence import EvidenceService                        # noqa: E402
from persona.modules.extract_quality import (                               # noqa: E402
    QUALITY_LOW,
    QUALITY_VALID,
    assess_content_quality,
)
from persona.modules.memory import MemoryService                            # noqa: E402
from persona.modules.persona import PersonaService                          # noqa: E402
from persona.modules.relationship import RelationshipService                # noqa: E402
from persona.modules.extraction import (                                    # noqa: E402
    ProviderClaimExtractor,
    REJECT_MATERIAL_NOT_GATE_PASSED,
    REJECT_MATERIAL_UNDECLARED,
    WARNING_MODEL_CALL_DUPLICATE_SKIPPED,
)
from persona.modules.research import (                                      # noqa: E402
    PersonQueryTarget,
    ResearchDeps,
    ResearchService,
    TopNExtractionStrategy,
    materials_from_extracts,
)
from persona.orchestrator import (                                          # noqa: E402
    STAGE_DIALOGUE,
    STAGE_EVIDENCE,
    STAGE_PERSONA,
    STAGE_RELATIONSHIP,
    STAGE_RESEARCH,
    STEP_EXECUTED,
    STEP_SKIPPED,
    BusinessDeps,
    BusinessOrchestrator,
    BusinessRequest,
    TaskService,
)
from persona.orchestrator.model_setup import (                              # noqa: E402
    chat_provider_config,
    chat_provider_from_settings,
    claim_extractor_from_settings,
    estimate_call_cost_usd,
    model_limits_from_settings,
    model_plan,
)
from persona.providers.base import (                                        # noqa: E402
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    ExtractResponse,
    ExtractResult,
    HealthStatus,
    SearchRequest,
    SearchResponse,
    SearchResult,
)
from persona.storage.sqlite import SqliteStorage                            # noqa: E402

PROJECT = "prj_p45a_e2e"
TASK_ID = "task_p45a_e2e_001"

# --------------------------------------------------------------------------
# 拍板配置（P4.5-A / P4.4-B / P4.4-C）。**显式注入**，不改本机任何东西。
# --------------------------------------------------------------------------
E2E_CONFIG: dict[str, str] = {
    "PERSONA_OFFLINE_ONLY": "false",
    "PERSONA_MODEL_PROVIDER": "deepseek",
    "PERSONA_MODEL_ID": "deepseek-flash",
    "PERSONA_MODEL_CURRENCY": "USD",
    "PERSONA_MODEL_PRICE_TIER": "peak",
    "PERSONA_MODEL_PRICE_IN_PER_1M": "0.3",
    "PERSONA_MODEL_PRICE_OUT_PER_1M": "1.2",
    "PERSONA_MODEL_PRICE_SOURCE": "https://api-docs.deepseek.com/quick_start/pricing",
    "PERSONA_MODEL_PRICE_CHECKED_AT": "2026-10-16",
    "PERSONA_MODEL_MAX_COST": "0.25",
    "PERSONA_MODEL_MAX_COST_PER_TASK": "0.25",
    "PERSONA_MODEL_MAX_CALLS": "1",
    "PERSONA_MODEL_EST_TOKENS_IN": "3000",
    "PERSONA_MODEL_EST_TOKENS_OUT": "2000",
    "PERSONA_MODEL_REQUIRE_GATE_VERDICT": "true",
    "PERSONA_RESEARCH_MAX_QUERIES": "1",
    "PERSONA_RESEARCH_MAX_RESULTS": "1",
    "PERSONA_RESEARCH_MAX_EXTRACTS": "1",
    "PERSONA_RESEARCH_MAX_REMEDIATIONS": "0",
}

#: 研究阶段的量级上限。`max_cost=None` → 研究侧不做金额闸门
#: （本次数据源是桩、不产生费用；模型侧的钱由 `ModelCallLimits` 管）。
E2E_RESEARCH_LIMITS = ResearchLimits(
    max_queries=1, max_results_per_query=1, max_extracts=1,
    max_remediations=0, max_cost=None)

#: 双人物诊断用的上限：两个目标各要一次检索与一次抓取。
E2E_RESEARCH_LIMITS_TWO = ResearchLimits(
    max_queries=2, max_results_per_query=1, max_extracts=2,
    max_remediations=0, max_cost=None)

# --------------------------------------------------------------------------
# 合成虚构材料（AC-16：**不得**出现真实人物名）
#
# 为什么必须 ≥200 字：正文质量门禁要求 `prose_chars >= 200`（`DEFAULT_MIN_EFFECTIVE_CHARS`），
# 否则真实门禁会判 `LOW_QUALITY`，材料根本进不了抽取器（设计稿里的 110 字样张太短，已修正）。
# --------------------------------------------------------------------------
E2E_URL = "https://example.invalid/mist-harbor-lighthouse-notes"
E2E_TITLE = "[合成样张·虚构] 雾港灯塔值守手记（非真实人物）"

#: 5 句，主语全是"阿雾"；**第 5 句是 AC-7 探针**（单次事件，绝不能进 `traits`）。
#: 每段都 ≥40 字（`is_prose_line` 的下限），合计 ≥200 字（门禁 `prose_chars` 下限）。
E2E_PASSAGES: tuple[str, ...] = (
    "阿雾是雾港灯塔的常年值守者，据雾港地方志记载，她自少年时代起便接替家中长辈看守这座灯塔，一直未曾离开。",
    "阿雾偏爱在傍晚独自巡视堤坝，随身带着一本记录潮位与风向的旧册子，册页边角已被海风磨得发白卷曲。",
    "阿雾向来沉默寡言，很少与星灯会的同伴闲谈，只在每年灯会点亮的那一晚才会多说几句话，说完便又回到塔上。",
    "阿雾习惯在整点敲响灯塔的铜钟，这个习惯维持了整整十二年，雾港的老住户只要听见钟声就知道她还在值守。",
    "阿雾曾在一次夜雾中连续值守七十二小时，直到潮水退去才离开塔身，这件事至今仍被雾港的老住户反复提起。",
)
E2E_TEXT = "\n\n".join(E2E_PASSAGES)

E2E_TARGET = PersonQueryTarget(entity_id="pe_p45a_awu", display_name="阿雾")

# --------------------------------------------------------------------------
# 第二份合成材料（**只**用于 G4 修复后的"双人物全链"验证）
#
# 为什么需要它：单人物请求在 `_overall_status` 下**只能**到 `PARTIAL`
# —— 关系/对话阶段按语义（参与者 < 2）跳过，与 G4 无关。要证明"整链真的通了"，
# 必须跑一个参与者 ≥ 2 的请求，让五个阶段全部 EXECUTED。
# --------------------------------------------------------------------------
E2E_URL_2 = "https://example.invalid/starlamp-festival-notes"
E2E_TITLE_2 = "[合成样张·虚构] 雾港星灯会庆典记闻（非真实人物）"

#: 5 句，主语全是"阿灯"；每段 ≥40 字，合计 ≥200 字（门禁 prose_chars 下限）。
E2E_PASSAGES_2: tuple[str, ...] = (
    "阿灯是雾港星灯会的常任司仪，据雾港庆典档案记载，她自幼便随长辈学习掌灯礼数，至今未曾间断。",
    "阿灯喜欢在灯会开场前独自登上堤坝看海，随身带着一盏用贝壳拼成的小灯，灯面早已被海风吹得温润发亮。",
    "阿灯生性热烈健谈，与雾港的每一位船工都熟识，只在深夜散场之后才会独坐片刻，然后重新回到灯棚里。",
    "阿灯习惯在每次灯会结束时点燃最后一盏灯，这个习惯维持了整整九年，雾港的老住户只要看见那盏灯就知道灯会散了。",
    "阿灯曾在一次风暴夜独自护住整排灯架，直到风势减弱才离开堤坝，这件事至今仍被雾港的老住户反复提起。",
)
E2E_TEXT_2 = "\n\n".join(E2E_PASSAGES_2)

E2E_TARGET_2 = PersonQueryTarget(entity_id="pe_p45a_adeng", display_name="阿灯")

# --------------------------------------------------------------------------
# 第三份合成材料："互动页"（C 轮新增）
#
# 为什么必须单独一份：前两份材料**没有任何一句同时点名两人**，因此抽取阶段
# 结构性地产不出覆盖 ≥2 名参与者的 RELATION 证据，关系档案只能是
# `INSUFFICIENT_EVIDENCE`。链路是通的，但"关系条目产出路径"从未被验证过。
#
# 每句都点名两人、且分别覆盖：共同例行 / 互动方式 / 配合旧例 / 同一夜的双方视角 /
# 散场交接。复用已有事实（铜钟、掌灯、护灯架）以避免制造冲突噪声。
# 门禁实测：status=VALID, prose_chars=243, nav_line_ratio=0.0, target_mentioned=True。
# --------------------------------------------------------------------------
E2E_URL_3 = "https://example.invalid/mist-harbor-lantern-night-joint-watch"
E2E_TITLE_3 = "[合成样张·虚构] 雾港星灯会与灯塔值守旧例合记（非真实人物）"

E2E_PASSAGES_3: tuple[str, ...] = (
    "据雾港志补记，星灯会与灯塔值守其实是一套旧例：每年灯会那一晚，阿雾与阿灯都要一同登上堤坝，从东头到西头逐盏核对灯位。",
    "核对灯位时两人话都不多，阿灯提灯照路，阿雾报出每一处风向与潮位，只在发现偏差时才会多说一句。",
    "老住户说，阿雾敲的铜钟就是灯会的开场信号，钟声三响之后阿灯才开始掌灯，这个配合已经沿用多年。",
    "有一年风暴夜里，阿灯独自护住整排灯架，阿雾在塔上整夜守着铜钟，直到风势减弱两人才在堤坝上碰面。",
    "灯会散场之后，阿灯会点亮最后一盏灯，再上塔把当夜的情形说给阿雾听，说完便各自回到自己的位置。",
)
E2E_TEXT_3 = "\n\n".join(E2E_PASSAGES_3)

#: 第三条检索查询的话题后缀（挂在阿雾身上）。
#: `plan_queries` 的规则是"每目标 1 条基础词 +（若有 topic）1 条话题词"，
#: 所以给阿雾挂上它，就能**确定性地**多出第三条查询 → 第三次抓取 → 第三份材料。
#: 这样不依赖 `filter_and_rank` 的排序（把互动页塞成某个查询的第二条结果会被排序挤掉）。
E2E_PAIR_TOPIC = "与阿灯 灯会值守旧例"

#: 三条查询用的研究上限：两个目标共 3 条计划（阿雾 ×2 + 阿灯 ×1）。
#: 抓取上限同步开到 3——少一条就会被截断，材料只有 2 份，关系又退化成"没证据"。
E2E_RESEARCH_LIMITS_THREE = ResearchLimits(
    max_queries=3, max_results_per_query=1, max_extracts=3,
    max_remediations=0, max_cost=None)

#: A11 负样例用到的两个不合格状态
NEG_UNDECLARED = ""


# ==========================================================================
# 桩 Provider（确定性、零联网、带调用计数）
# ==========================================================================
class StubSearchExtractProvider:
    """桩数据源：`search` 返回 1 条合成结果，`extract` 返回合成正文。

    **不是第二套产品实现**——它只是"数据源"的替身，让"合成人物在公网不存在"
    与"要跑真实模型"这两个互斥条件同时成立（P4.5-A 方案 B）。
    """

    def __init__(self, *, snippet: str = E2E_TEXT, fail_extract: str = "") -> None:
        self.snippet = snippet
        self.fail_extract = fail_extract
        self.search_calls = 0
        self.extract_calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub-search-extract",
                                supports_search=True, supports_extract=True))

    def chat(self, ctx, req):
        return Err(_stub_error("桩数据源不支持 chat（那是 DeepSeek Provider 的职责）"))

    def search(self, ctx, req):
        self.search_calls += 1
        usage = UsageDelta(requests=1, latency_ms=1)
        return Ok(SearchResponse(results=[self._result()], usage=usage), usage=usage)

    def extract(self, ctx, req):
        self.extract_calls += 1
        if self.fail_extract:
            return Err(_stub_error(self.fail_extract))
        results = [ExtractResult(
            url=str(u), title=E2E_TITLE, raw_content=E2E_TEXT, excerpt=E2E_TEXT,
            content_type="text/markdown", published_at=None, truncated=False)
            for u in (req.urls or [])]
        usage = UsageDelta(requests=1, latency_ms=1)
        return Ok(ExtractResponse(results=results, usage=usage), usage=usage)

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=self.search_calls + self.extract_calls))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))

    @staticmethod
    def _result() -> SearchResult:
        return SearchResult(
            title=E2E_TITLE, url=E2E_URL, canonical_key=E2E_URL,
            snippet=E2E_TEXT, published_at=None, source_kind_guess="OFFICIAL_MATERIAL")


class StubChatProvider:
    """桩聊天 Provider：**永不联网**，可注入固定 JSON 输出，并计数调用次数。

    两个用途：
      1. A11 负样例与离线测试里扮演"模型"，证明门禁会**拦在调用之前**；
      2. `--dry` 的编排器路径诊断里扮演"模型"，确保诊断本身不可能花钱。
    """

    def __init__(self, payload: str = '{"claims": []}', *, fail: bool = False,
                 request_id: str = "stub-request-0001") -> None:
        self.payload = payload
        self.fail = fail
        self.request_id = request_id
        self.chat_calls = 0
        self.last_request: ChatRequest | None = None

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub-chat", supports_chat=True))

    def chat(self, ctx, req):
        self.chat_calls += 1
        self.last_request = req
        if self.fail:
            return Err(_stub_error("注入的桩模型失败"))
        # 与真实 Provider 同口径：报 token + 服务端 request_id，
        # 但 **不报金额**（D3：金额由装配层按配置单价算）。
        usage = UsageDelta(requests=1, tokens_in=1200, tokens_out=300,
                           request_id=self.request_id)
        return Ok(ChatResponse(text=self.payload, finish_reason="stop",
                               model_id="stub-chat", usage=usage,
                               raw_ref=self.request_id), usage=usage)

    def search(self, ctx, req):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=self.chat_calls))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class CountingChatProvider:
    """薄记录层：委托真 Provider，只记录调用次数与最后一次响应。

    `provider_name` 必须透传——否则抽取器的幂等键会变成包装类的类名。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.provider_name = getattr(inner, "provider_name", type(inner).__name__)
        self.calls = 0
        self.last_result = None

    def chat(self, ctx, req):
        self.calls += 1
        self.last_result = self._inner.chat(ctx, req)
        return self.last_result

    def search(self, ctx, req):
        return self._inner.search(ctx, req)

    def extract(self, ctx, req):
        fn = getattr(self._inner, "extract", None)
        if fn is None:
            raise AttributeError("inner provider has no extract()")
        return fn(ctx, req)

    def usage(self, ctx, scope):
        return self._inner.usage(ctx, scope)

    def health(self, ctx):
        return self._inner.health(ctx)

    def capabilities(self):
        return self._inner.capabilities()


def _stub_error(message: str):
    from persona.core.errors import ErrorCode, new_error
    return new_error(ErrorCode.EXTERNAL_UNAVAILABLE, message)


class RecordingExtractor:
    """薄记录层：委托真抽取器，**顺手留下来最后一次 `ExtractionOutcome`**。

    为什么需要它：编排器在 `_step_evidence` 内部自己调
    `deps.claim_extractor.extract(...)`，调用方拿不到 `outcome`。而离线复算器
    （`verify_p4_5a_outputs.py`）要复算摘录可追溯性与费用，必须拿到候选 claim 的
    `locator` / `verbatim_quote` / `source_id`。

    用代理是最不侵入的做法：**不改产品代码**，也不另写一套装配。`extract()` 的
    签名原样透传（`*args, **kwargs`），编排器只用到 `.extract()`；其余属性透传。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls = 0
        self.outcomes: list = []

    def extract(self, *args, **kwargs):
        self.calls += 1
        result = self._inner.extract(*args, **kwargs)
        if is_ok(result):
            self.outcomes.append(result.value)
        return result

    def build_request(self, *args, **kwargs):
        return self._inner.build_request(*args, **kwargs)

    @property
    def last_outcome(self):
        return self.outcomes[-1] if self.outcomes else None

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class RawResponseCapture:
    """薄代理：挂在 Provider 的**唯一网络出口**上，留下原始 HTTP 收发记录。

    为什么能在这里挂：`DeepSeekProvider` 把网络出口收敛成单个可替换的
    `self._urlopen`（其 docstring 明写"**唯一**发生真实网络调用的地方"，
    供测试桩替换）。所以在**脚本侧**包一层就能拿到原始响应体，
    **不改产品代码**。请求体由 Provider 自己构造，本层只读长度与头名，
    **绝不读取 `Authorization` 的值**（密钥不进产物）。

    用法：`provider._urlopen = RawResponseCapture(provider._urlopen)`。
    只有真实 `DeepSeekProvider` 有这个属性；注入的桩 Provider 没有 → 跳过。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.exchanges: list[dict] = []

    def __call__(self, request, timeout=None):
        entry = {
            "url": str(getattr(request, "full_url", "") or ""),
            "method": str(getattr(request, "method", "") or ""),
            "request_body_bytes": len(getattr(request, "data", b"") or b""),
            # ⚠️ 只记**头名**，不记头值（`Authorization` 的值就是密钥）
            "request_header_names": sorted(
                str(k).lower() for k in (getattr(request, "headers", {}) or {})),
            "status": None,
            "response_bytes": None,
            "raw_response": None,
        }
        self.exchanges.append(entry)
        response = self._inner(request, timeout=timeout)
        return _CapturingResponse(response, entry)


class _CapturingResponse:
    """包装 `urlopen` 的返回值：把 `read()` 到的字节**顺手留一份**。"""

    def __init__(self, inner, entry: dict) -> None:
        self._inner = inner
        self._entry = entry

    def read(self, *args, **kwargs):
        data = self._inner.read(*args, **kwargs)
        self._entry["status"] = getattr(self._inner, "status", None)
        self._entry["response_bytes"] = len(data or b"")
        self._entry["raw_response"] = (data or b"").decode("utf-8", errors="replace")
        return data

    def __enter__(self):
        self._inner.__enter__()
        return self

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class StubTwoTargetSourceProvider:
    """桩数据源（多目标版）：按**检索词**返回各自的合成正文。

    与单目标桩的差别是"per-query 结果"——真实 Provider 本来就是这样
    （同一个检索词不会返回同一个 URL），单目标桩只是把这一点简化掉了。

    C 轮起它有 **3 个页面**：阿雾页、阿灯页、以及两人共同的"互动页"。
    互动页由第三条查询（给阿雾挂了 `topic` 之后）取回，用来让抽取阶段
    有机会产出覆盖 ≥2 名参与者的 `RELATION` 证据——否则关系条目产出路径
    永远验证不到（前两份材料里没有一句同时点名两人）。
    """

    #: 检索词 → (url, title, text)。
    #:
    #: **精确匹配优先**：第三条查询（`阿雾 与阿灯 灯会值守旧例`）同时含两个人名，
    #: 若沿用"子串命中"的写法，它会同时返回阿雾页与阿灯页 → 3 条查询得到 4 份正文，
    #: 抓取上限被挤爆、材料数不再是 3。所以这里按**完整检索词**查表。
    _PAGES: dict[str, tuple[str, str, str]] = {
        "阿雾": (E2E_URL, E2E_TITLE, E2E_TEXT),
        "阿灯": (E2E_URL_2, E2E_TITLE_2, E2E_TEXT_2),
        f"阿雾 {E2E_PAIR_TOPIC}": (E2E_URL_3, E2E_TITLE_3, E2E_TEXT_3),
    }

    def __init__(self) -> None:
        self.search_calls = 0
        self.extract_calls = 0

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub-search-extract-2",
                                supports_search=True, supports_extract=True))

    def chat(self, ctx, req):
        return Err(_stub_error("桩数据源不支持 chat（那是 DeepSeek Provider 的职责）"))

    def search(self, ctx, req):
        self.search_calls += 1
        query = str(getattr(req, "query", "") or "")
        pair = self._PAGES[f"阿雾 {E2E_PAIR_TOPIC}"]
        entry = self._PAGES.get(query)
        if entry is not None:
            hits = [entry]
        elif E2E_PAIR_TOPIC in query:
            # 话题词被改写时的兜底：仍只返回互动页，绝不顺带返回人物页
            hits = [pair]
        else:
            hits = [e for name, e in self._PAGES.items()
                    if name in query and e is not pair]
        usage = UsageDelta(requests=1, latency_ms=1)
        results = [SearchResult(title=title, url=url, canonical_key=url,
                                snippet=text, published_at=None,
                                source_kind_guess="OFFICIAL_MATERIAL")
                   for url, title, text in hits]
        return Ok(SearchResponse(results=results, usage=usage), usage=usage)

    def extract(self, ctx, req):
        self.extract_calls += 1
        by_url = {url: (title, text) for url, title, text in self._PAGES.values()}
        results = []
        for target_url in (req.urls or []):
            title, text = by_url.get(str(target_url), (E2E_TITLE, E2E_TEXT))
            results.append(ExtractResult(
                url=str(target_url), title=title, raw_content=text, excerpt=text,
                content_type="text/markdown", published_at=None, truncated=False))
        usage = UsageDelta(requests=1, latency_ms=1)
        return Ok(ExtractResponse(results=results, usage=usage), usage=usage)

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=self.search_calls + self.extract_calls))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


class StubDialogueProvider:
    """桩对话 Provider：给**模拟对话**阶段返回自然文本（不是 JSON claim）。

    为什么单独一个：抽取与对话共用一个 Provider 时，桩若只返回 claim JSON，
    对话轮次会把 JSON 当台词记下来，产物看起来"像通了"其实是脏数据。
    """

    def __init__(self) -> None:
        self.chat_calls = 0
        self.provider_name = "stub-dialogue"

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="stub-dialogue", supports_chat=True))

    def chat(self, ctx, req):
        self.chat_calls += 1
        usage = UsageDelta(requests=1, tokens_in=20, tokens_out=20)
        return Ok(ChatResponse(text="（模拟台词）灯还亮着，我先去巡一遍堤坝。",
                               finish_reason="stop", model_id="stub-dialogue",
                               usage=usage), usage=usage)

    def search(self, ctx, req):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport(requests=self.chat_calls))

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


# ==========================================================================
# 输出缓冲 + 密钥泄漏自检
# ==========================================================================
class Out:
    """所有输出先过这里；最后统一做**密钥子串扫描**。"""

    def __init__(self, secret: str = "", *, quiet: bool = False) -> None:
        self._secret = secret or ""
        self._quiet = quiet
        self.lines: list[str] = []
        self.leak = False

    def __call__(self, text: str = "") -> None:
        if self._secret and self._secret in text:
            self.leak = True
            text = text.replace(self._secret, "***[REDACTED]***")
        self.lines.append(text)
        if not self._quiet:
            print(text)

    def scan(self, *values: str) -> bool:
        """对任意字符串做泄漏检查（返回 True = 发现泄漏）。"""
        if not self._secret:
            return False
        return any(self._secret in (v or "") for v in values)

    def hr(self, title: str) -> None:
        self("")
        self("=" * 74)
        self(title)
        self("=" * 74)


# ==========================================================================
# 装配
# ==========================================================================
class Stack:
    """一次性全链装配（脚本侧）：临时库 + 真实服务 + 桩数据源。

    与产品代码的**唯一差异**在材料交接（见模块 docstring 的 G4）：脚本显式调用
    `materials_from_extracts()` 并把 `source_id` 绑上；证据登记、门禁、人格档案
    全部复用 `BusinessOrchestrator` 的**同一批方法**，不另写一套。
    """

    def __init__(self, settings, ledger, out: Out, *,
                 chat_provider=None, source_provider=None,
                 research_limits=None) -> None:
        self.settings = settings
        self.out = out
        self.tmp = tempfile.mkdtemp(prefix="gps-p45a-")
        self.path = Path(self.tmp) / "p45a.db"
        self.storage = SqliteStorage(self.path)
        self.ctx: CallContext = new_call_context(
            project_id=PROJECT, actor="p45a-e2e", task_id=TASK_ID)
        if is_err(self.storage.init_schema(self.ctx)):
            raise RuntimeError("临时库 schema 初始化失败")

        self.ledger = ledger
        self.search_provider = source_provider or StubSearchExtractProvider()
        self.dialogue_provider = chat_provider or StubChatProvider()

        self.evidence = EvidenceService(self.storage)
        self.persona = PersonaService(self.storage)
        self.relationship = RelationshipService(self.storage)
        self.dialogue = DialogueService(DialogueDeps(
            profiles=self.persona, relations=self.relationship,
            evidence=self.evidence, memory=MemoryService(self.storage),
            storage=self.storage, provider=self.dialogue_provider,
            memory_reader=MemoryService(self.storage)))
        self.task_service = TaskService(self.storage)

        self.research = ResearchService(ResearchDeps(
            provider=self.search_provider,
            evidence=self.evidence,
            search_metadata={"fixture_key": "p45a"},
            extraction_strategy=TopNExtractionStrategy(),
            limits=research_limits or E2E_RESEARCH_LIMITS,
            task_id=TASK_ID))
        self._extractor = None
        self.entity_id: str = ""
        self.target: PersonQueryTarget | None = None
        #: 已登记的**全部**目标（`self.target` 只是最后一个）。双人物用它。
        self.targets: list[PersonQueryTarget] = []

    # -------------------------------------------------------------- 目标实体
    def ensure_entity(self, name: str) -> str:
        """登记目标实体。

        **注意**：实体 ID 由存储生成（`storage.put` 的返回值），不是调用方指定的——
        必须一路使用返回值，否则来源登记校验 `subject_entity_ids` 会报
        `VALIDATION.entity_missing`（dry-run 实测抓到过一次）。
        """
        from persona.core.enums import ProvenanceNamespace
        self.entity_id = str(self.storage.put(
            self.ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER",
             "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
             "fictional_character_meta": {
                 "works": [{"work_id": "wrk_mistharbor",
                            "title": "《雾港与星灯》", "medium": "ANIMATION"}],
                 "adaptation_version": "2020 动画改编版",
                 "adaptation_lineage": ["原作轻小说版", "2020 动画改编版"],
                 "official_canon_level": "CANON"}},
            project_id=PROJECT, namespace=ProvenanceNamespace.PUBLIC_SOURCE).value)
        self.target = PersonQueryTarget(entity_id=self.entity_id, display_name=name)
        self.targets.append(self.target)
        return self.entity_id

    def ensure_targets(self, names: list[str]) -> list[PersonQueryTarget]:
        """登记**多个**目标并返回它们（`self.target` 停在最后一个）。

        双人物诊断用它——单目标 `ensure_entity` 会把 `self.target` 覆盖掉。

        注意：`ensure_entity` **内部已经**把目标 append 进 `self.targets`，
        这里必须复用它返回的那个对象，不能另造一个（否则 `self.targets`
        会出现重复条目，后续按 targets 构造请求时人名会重复）。
        """
        targets: list[PersonQueryTarget] = []
        for name in names:
            self.ensure_entity(name)
            if self.target is not None:
                targets.append(self.target)
        return targets

    # -------------------------------------------------------------- 抽取器
    def use_real_extractor(self, provider) -> ProviderClaimExtractor:
        self._extractor = claim_extractor_from_settings(
            self.settings, provider, ledger=self.ledger, task_id=TASK_ID)
        return self._extractor

    def use_stub_extractor(self, chat_provider) -> ProviderClaimExtractor:
        self._extractor = claim_extractor_from_settings(
            self.settings, chat_provider, ledger=self.ledger, task_id=TASK_ID)
        return self._extractor

    # -------------------------------------------------------------- 编排器
    def orchestrator(self, *, analyzer=None) -> BusinessOrchestrator:
        return BusinessOrchestrator(BusinessDeps(
            research=self.research, evidence=self.evidence, persona=self.persona,
            relationship=self.relationship, dialogue=self.dialogue,
            task_service=self.task_service, storage=self.storage,
            claim_extractor=self._extractor, relationship_analyzer=analyzer))

    def request(self, *, max_rounds: int = 1,
                targets: list[PersonQueryTarget] | None = None) -> BusinessRequest:
        return BusinessRequest(
            targets=list(targets) if targets else [self.target],
            max_rounds=max_rounds)

    def teardown(self) -> None:
        try:
            self.storage.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)


def materials_from_research(stack: Stack, research_output
                            ) -> tuple[list[SourceMaterial], list]:
    """研究产出 → 可进抽取器的材料（脚本侧交接，等价于产品代码**应该**做但没做的事）。

    **为什么必须在这里做**（G4 的第二半）：`materials_from_extracts()` 把
    `source_id` 留成 `""`，而证据登记的可追溯闸门要求"候选的 `source_id` 必须在
    本次登记的来源里"。脚本按 `url` 反查已登记来源，把这一环补上。
    """
    materials, rejected = materials_from_extracts(
        list(research_output.extracted),
        target_entity_ids=[stack.entity_id],
        query_text=stack.target.display_name if stack.target else "")
    # url → source_id：来源是在检索阶段按 url 登记的，这里按 url 反查补上。
    page = stack.evidence.query_sources(
        stack.ctx, Query(filters={}, limit=MAX_QUERY_LIMIT))
    by_url = {}
    if is_ok(page):
        for row in page.value.items:
            if row.url:
                by_url[str(row.url)] = str(row.id)
    unbound: list[str] = []
    for material in materials:
        sid = by_url.get(str(material.url), "")
        if not sid:
            unbound.append(str(material.url))
        else:
            material.source_id = sid
    return materials, rejected + [{"unbound_urls": unbound}] if unbound else rejected


# ==========================================================================
# A11：门禁负样例（桩 Provider，**必须 0 次调用**）
# ==========================================================================
def a11_gate_negatives(settings, ledger, out: Out) -> list[dict]:
    """门禁负样例：不合格材料必须被拦在模型调用之前。

    返回每条样例的结果字典；`ok=True` 表示"按预期被拦且零调用"。
    """
    out("  规则：材料质量门禁不合格 → 拒收 → **零模型调用**（费用为 0）")
    cases = [
        ("LOW_QUALITY（抓取正文未过门禁）", QUALITY_LOW,
         REJECT_MATERIAL_NOT_GATE_PASSED),
        ("未声明（quality_status=\"\"）", NEG_UNDECLARED,
         REJECT_MATERIAL_UNDECLARED),
    ]
    ctx = CallContext(project_id=PROJECT, actor="p45a-e2e", task_id=TASK_ID)
    results: list[dict] = []
    for label, status, expect_code in cases:
        stub = StubChatProvider('{"claims": []}')
        extractor = claim_extractor_from_settings(
            settings, stub, ledger=ledger, task_id=TASK_ID)
        material = SourceMaterial(
            source_id="src_p45a_negative", title="[合成样张·虚构] 不合格材料",
            url="https://example.invalid/negative", canonical_key="neg:1",
            snippet=E2E_TEXT, source_kind="WEB_PAGE",
            subject_entity_ids=[E2E_TARGET.entity_id], quality_status=status)
        result = extractor.extract(ctx, [material], targets=[E2E_TARGET])
        outcome = result.value if is_ok(result) else None
        codes = [r.reason for r in (outcome.rejected if outcome else [])]
        claims = len(outcome.claims) if outcome else 0
        ok = (stub.chat_calls == 0 and expect_code in codes and claims == 0)
        results.append({"case": label, "expect": expect_code, "codes": codes,
                        "chat_calls": stub.chat_calls, "claims": claims, "ok": ok})
        out(f"  [{'OK ' if ok else 'FAIL'}] {label}")
        out(f"        拒收原因 : {codes}")
        out(f"        模型调用 : {stub.chat_calls} 次（必须 0）")
        out(f"        产出候选 : {claims} 条（必须 0）")
    return results


# ==========================================================================
# 编排器路径诊断（证明 G4，零成本）
# ==========================================================================
def diagnose_orchestrator_path(settings, out: Out, *,
                               two_person: bool = False) -> dict:
    """用桩 Provider 跑一次**编排器**路径（零成本），看抓取正文能不能交到抽取器手里。

    它的唯一目的是：把"抓了正文但交不出去"（G4）这件事用**证据**摆出来或证伪，
    而不是靠读代码断言。这一点在修复前后都必须能跑：修复前它证明"模型永不被
    调用"，修复后它证明"模型被调用且证据非空"。

    `two_person=True` 时用**两个人**跑：单人物请求在 `_overall_status` 下只能到
    `PARTIAL`（关系/对话阶段按语义跳过，与 G4 无关），只有参与者 ≥ 2 才能让
    五个阶段全部 EXECUTED —— 这是"整链真的通了"的唯一证据。

    用**独立的临时库**跑，避免它产生的产物污染真实运行。
    """
    names = ["阿雾"] + (["阿灯"] if two_person else [])
    source = (StubTwoTargetSourceProvider() if two_person
              else StubSearchExtractProvider())
    stack = Stack(settings, ExternalUsageLedger(), out,
                  chat_provider=StubDialogueProvider(), source_provider=source,
                  research_limits=(E2E_RESEARCH_LIMITS_THREE if two_person
                                   else E2E_RESEARCH_LIMITS))
    try:
        claims_stub = StubChatProvider(_orchestrator_payload_shell())
        stack.use_stub_extractor(claims_stub)
        target_list = stack.ensure_targets(names)
        if two_person:
            # C 轮：给第一个目标挂话题 → 多出第三条查询 → 取回"互动页"
            target_list[0].topic = E2E_PAIR_TOPIC
        ids = {t.display_name: t.entity_id for t in target_list}
        claims_stub.payload = orchestrator_payload(
            ids if two_person else {"阿雾": ids["阿雾"]},
            with_pair=two_person)
        orch = stack.orchestrator()
        result = orch.run(stack.ctx, stack.request(targets=target_list))
        info: dict = {"ok": is_ok(result), "two_person": two_person}
        if is_ok(result):
            run = result.value
            info["status"] = run.status
            info["task_status"] = str(run.task_status)
            info["evidence_count"] = len(run.evidence_ids)
            info["stage_status"] = {s.name: s.status for s in run.steps}
            warn_codes: list[str] = []
            for step in run.steps:
                for w in step.warnings:
                    warn_codes.append(str(w.code))
            info["warning_codes"] = sorted(set(warn_codes))
            step = run.step_of(STAGE_EVIDENCE)
            info["evidence_message"] = step.message if step else ""
            research_step = run.step_of(STAGE_RESEARCH)
            info["research_message"] = research_step.message if research_step else ""
            # —— G4 的核心观察点：材料真的进材料流了吗 ——
            materials = list(orch._materials.get(run.task_id, []) or [])
            info["materials_in_flow"] = len(materials)
            info["materials_gate_valid"] = sum(
                1 for m in materials if str(getattr(m, "quality_status", "")))
            info["materials_bound"] = sum(
                1 for m in materials if str(getattr(m, "source_id", "")))
            # C 轮：L4 —— 关系档案真的被写出来了吗（阳性对照的离线版本）
            rel_rows = []
            for rid in (run.relationship_profile_ids or []):
                view = stack.relationship.get_relationship(stack.ctx, rid)
                if is_ok(view):
                    rel_rows.append({
                        "id": rid, "version": view.value.version,
                        "status": str(view.value.status),
                        "shared_experiences": len(view.value.shared_experiences),
                    })
            info["relationship"] = rel_rows
        else:
            info["error"] = f"{result.error.code}: {result.error.message}"
        info["stub_chat_calls"] = claims_stub.chat_calls
        return info
    finally:
        stack.teardown()


def _orchestrator_payload_shell() -> str:
    """先给一个空载荷占位（真载荷在目标实体登记之后才能构造）。"""
    return json.dumps({"claims": []})


def orchestrator_payload(name_to_id: dict[str, str], *,
                         with_pair: bool = False) -> str:
    """编排器诊断用的**模型输出**：VERBATIM 候选，摘录逐字取自对应合成正文。

    每个主体 5 条；第 5 条是 `TRAIT + SINGLE`（AC-7 探针）。

    `with_pair=True` 时追加 **2 条双主体 RELATION**（摘录逐字取自互动页，
    `subject_entity_ids` 同时含两人）——这是"关系条目产出路径"的**阳性对照**：
    若连桩模型给的双人证据都变不成 `shared_experiences`，那是代码坏了，
    与真实模型是否愿意产出 RELATION 无关。

    `material_key` 用材料的 `canonical_key`（= url）——抽取器就是这么定位的。
    """
    catalog = {
        "阿雾": (E2E_URL, [
            ("a1", "阿雾是雾港灯塔的常年值守者", "BACKGROUND", "REPEATED"),
            ("a2", "阿雾偏爱在傍晚独自巡视堤坝", "HABIT", "REPEATED"),
            ("a3", "阿雾向来沉默寡言", "TRAIT", "PATTERN"),
            ("a4", "阿雾习惯在整点敲响灯塔的铜钟", "HABIT", "PATTERN"),
            ("a5", "阿雾曾在一次夜雾中连续值守七十二小时", "TRAIT", "SINGLE"),
        ]),
        "阿灯": (E2E_URL_2, [
            ("b1", "阿灯是雾港星灯会的常任司仪", "BACKGROUND", "REPEATED"),
            ("b2", "阿灯喜欢在灯会开场前独自登上堤坝看海", "HABIT", "REPEATED"),
            ("b3", "阿灯生性热烈健谈", "TRAIT", "PATTERN"),
            ("b4", "阿灯习惯在每次灯会结束时点燃最后一盏灯", "HABIT", "PATTERN"),
            ("b5", "阿灯曾在一次风暴夜独自护住整排灯架", "TRAIT", "SINGLE"),
        ]),
    }
    claims: list[dict] = []
    for name, entity_id in name_to_id.items():
        material_key, rows = catalog[name]
        for key, text, dimension, stability in rows:
            claims.append({
                "key": f"{key}_{name}", "claim": text, "tier": "VERBATIM",
                "dimension": dimension, "subject_entity_ids": [entity_id],
                "material_key": material_key, "verbatim_quote": text,
                "stability": stability, "canon_level": "CANON", "confidence": 0.9,
            })

    if with_pair and "阿雾" in name_to_id and "阿灯" in name_to_id:
        both = [name_to_id["阿雾"], name_to_id["阿灯"]]
        # 摘录必须是互动页里的**逐字子串**（框架会核验 locator）
        pair_rows = [
            ("p1", "每年灯会那一晚，阿雾与阿灯都要一同登上堤坝，从东头到西头逐盏核对灯位",
             "每年灯会那一晚，阿雾与阿灯都要一同登上堤坝，从东头到西头逐盏核对灯位。"),
            ("p2", "有一年风暴夜里，阿灯独自护住整排灯架，阿雾在塔上整夜守着铜钟",
             "有一年风暴夜里，阿灯独自护住整排灯架，阿雾在塔上整夜守着铜钟，"
             "直到风势减弱两人才在堤坝上碰面。"),
        ]
        for key, claim_text, quote in pair_rows:
            claims.append({
                "key": f"{key}_pair", "claim": claim_text, "tier": "VERBATIM",
                "dimension": "RELATION", "subject_entity_ids": list(both),
                "material_key": E2E_URL_3, "verbatim_quote": quote,
                "stability": "UNKNOWN", "canon_level": "CANON", "confidence": 0.9,
            })
    return json.dumps({"claims": claims}, ensure_ascii=False)


# ==========================================================================
# 打印辅助
# ==========================================================================
def print_quality(out: Out, text: str, names: list[str]) -> None:
    q = assess_content_quality(text, url=E2E_URL, target_names=names)
    out(f"  门禁复算（真实 `assess_content_quality`）")
    out(f"    status        : {q.status}")
    out(f"    reasons       : {q.reasons or '（无）'}")
    out(f"    prose_chars   : {q.prose_chars}（下限 200）")
    out(f"    longest_run   : {q.longest_prose_run}（下限 200）")
    out(f"    nav_line_ratio: {round(q.nav_line_ratio, 3)}")
    out(f"    target_mentioned: {q.target_mentioned}")
    out(f"    正文总字数     : {len(text)}")


def _print_diag(out: Out, diag: dict) -> None:
    """打印一次编排器路径诊断的结果（单/双人物共用）。"""
    out(f"  整体状态        : {diag.get('status', diag.get('error'))}"
        + ("（五阶段全执行）" if diag.get("status") == "SUCCEEDED"
           else "（单人物：关系/对话按语义跳过）"
           if diag.get("status") == "PARTIAL" else ""))
    out(f"  任务终态        : {diag.get('task_status')}")
    out(f"  证据数          : {diag.get('evidence_count')}")
    out(f"  桩模型调用次数   : {diag.get('stub_chat_calls')}"
        "（0 = 模型根本没被调用）")
    out(f"  材料流          : {diag.get('materials_in_flow')} 份"
        f"（带门禁结论 {diag.get('materials_gate_valid')}，"
        f"已绑定来源 {diag.get('materials_bound')}）")
    out(f"  研究阶段说明     : {diag.get('research_message')}")
    if diag.get("stage_status"):
        for k, v in diag["stage_status"].items():
            out(f"      阶段 {k:22s} → {v}")
    if diag.get("warning_codes"):
        out(f"  告警码          : {diag['warning_codes']}")
    out(f"  证据阶段说明     : {diag.get('evidence_message')}")
    for row in (diag.get("relationship") or []):
        out(f"  关系档案（L4）   : {row['id']} v{row['version']} "
            f"status={row['status']} 共享经历={row['shared_experiences']}")


def print_stage_table(out: Out, run) -> None:
    labels = {
        STAGE_RESEARCH: "① 研究（检索→抓取→门禁→材料）",
        STAGE_EVIDENCE: "② ③ 抽取 + 证据登记",
        STAGE_PERSONA: "④ ⑤ 门禁 + 人格档案",
        STAGE_RELATIONSHIP: "关系档案",
        STAGE_DIALOGUE: "模拟对话",
    }
    out("  阶段               状态            说明")
    for step in run.steps:
        label = labels.get(step.name, step.name)
        out(f"  {label:24s} {step.status:14s} {step.message}")
        for w in step.warnings:
            out(f"      ! [{w.code}] {w.message}")


def dump_outcome(out: Out, outcome) -> None:
    out(f"    method          : {outcome.method}")
    out(f"    整批 verdict     : {outcome.verdict}")
    out(f"    候选 claims     : {len(outcome.claims)}")
    out(f"    待审核 pending  : {len(outcome.pending)}")
    out(f"    拒收 rejected   : {len(outcome.rejected)}")
    for r in outcome.rejected:
        out(f"      ! 拒收 [{r.reason}] {r.message}")
    for p in outcome.pending:
        out(f"      ? 待审核 [{p.reason}] {p.message}")
    for c in outcome.claims:
        out(f"      * {c.key}")
        out(f"          tier/dimension : {c.tier} / {c.dimension}")
        out(f"          subjects       : {c.subject_entity_ids}")
        out(f"          source_id      : {c.source_id}")
        out(f"          claim          : {c.claim}")
        out(f"          quote          : {c.verbatim_quote!r}")
        out(f"          locator        : {c.locator}")
        out(f"          derivation_keys: {c.derivation_keys}")
    detail = outcome.validation_detail or {}
    if detail:
        out("    validation_detail: "
            + json.dumps(detail, ensure_ascii=False)[:1200])


def dump_ledger(out: Out, ledger: ExternalUsageLedger, cap: float | None,
                currency: str) -> None:
    for rec in ledger.records():
        out(f"    记录 ID(幂等键) : {rec.idempotency_key}")
        out(f"      provider/op   : {rec.provider} / {rec.operation}")
        out(f"      outcome       : {rec.outcome}")
        out(f"      request_id    : {rec.request_id}")
        out(f"      cost_estimate : {rec.cost_estimate}")
        out(f"      cost_confirmed: {rec.cost_confirmed}")
        out(f"      cost_status   : {rec.cost_status}")
        out(f"      cost_source   : {rec.cost_source}")
        out(f"      currency      : {rec.currency}")
    totals = ledger.totals()
    occupied = ledger.occupied_cost()
    out(f"    记录数          : {totals.requests}")
    out(f"    已确认费用       : {totals.confirmed_cost}")
    out(f"    估算费用         : {totals.estimated_cost:.6f}")
    out(f"    未知占位         : {totals.unknown_hold_cost:.6f}")
    out(f"    未知费用笔数     : {totals.unknown_requests}")
    out(f"    失败笔数         : {totals.failed_requests}")
    out(f"    已占用（保守）   : {occupied:.6f} {currency}")
    if cap is not None:
        out(f"    剩余预算         : {max(0.0, cap - occupied):.6f} / {cap} {currency}")


# ==========================================================================
# [7] 真实执行：编排器路径（产品代码全链）
# ==========================================================================
def run_orchestrator_real(settings, out: Out, problems: list[str], *,
                          expect, ledger, limits, dump_path: str = "",
                          provider=None, on_targets=None,
                          names: list[str] | None = None) -> None:
    """真实执行**编排器路径**：`BusinessOrchestrator.run` 全链 + 1 次真实模型调用。

    与 `--path=script` 的差别：材料交接、证据登记、门禁、人格档案**全部**由
    产品代码走一遍；脚本只做两件事——① 在发送前把真实请求体构造出来核验；
    ② 在运行后把 `BusinessRunResult` 与账本读出来如实汇报。**不替产品代码补任何
    一环**（这正是 G4 当初的教训：脚本侧代劳会让产品缺陷被掩盖）。

    装配取舍（已披露，不隐瞒）：
      - 数据源 = 桩（方案 B 既定：合成人物在公网不存在，真实检索拿不到东西）；
      - 对话 Provider = 桩：**单人物**请求下对话阶段按语义 SKIPPED，桩根本不会被
        调用（`StubDialogueProvider.chat_calls` 恒为 0，下面会断言）。若改成双人物，
        对话阶段就会真跑，那时桩产物会掺进 `SUCCEEDED`，不如单人物诚实；
      - 抽取 Provider = **真实 DeepSeek**，计数包装，恰好 1 次。

    两个**离线测试注入点**（默认 `None` = 真实运行，行为不受影响）：
      - `provider`：替掉真实 Provider，好让离线测试用桩模型把**同一条产品代码
        链路**零成本复跑一遍（否则"接线本身"就没有任何测试锁住）；
      - `on_targets`：目标实体登记完成后回调 `(stack, entity_id)`，让桩模型能拿到
        存储生成的实体 ID 去构造载荷（双人物时可从 `stack.targets` 取全部）。

    G8（双人物入口）：`names` 为 `None` 时退化成单人物（阿雾），与既有行为逐字一致。
    传 2 个名字时：桩数据源换成 `StubTwoTargetSourceProvider`（按检索词里的目标名
    返回各自的合成正文）、研究上限换成 `E2E_RESEARCH_LIMITS_TWO`、请求带 2 个目标，
    真实模型调用仍是 **1 次**（抽取器一次处理全部材料与目标）。
    """
    names = list(names) if names else [E2E_TARGET.display_name]
    two_person = len(names) > 1
    label = "双人物" if two_person else "单人物"
    out.hr(f"[7] 真实执行（编排器路径：产品代码全链，{label}，1 次调用，失败不重试）")

    if provider is not None:
        real_provider = provider
        out("  Provider        : **注入的桩 Provider**（离线测试路径，零成本）")
    else:
        provider_result = chat_provider_from_settings(settings)
        if is_err(provider_result):
            out(f"  [FAIL] 无法装配 Provider：{provider_result.error.message}")
            problems.append("Provider 装配失败")
            return
        real_provider = provider_result.value
    # 原始 HTTP 收发落盘：挂在 Provider 的唯一网络出口上（桩 Provider 没有该属性 → 跳过）
    raw_capture = None
    if getattr(real_provider, "_urlopen", None) is not None:
        try:
            raw_capture = RawResponseCapture(real_provider._urlopen)
            real_provider._urlopen = raw_capture
        except (AttributeError, TypeError):
            raw_capture = None

    recorder = CountingChatProvider(real_provider)
    dialogue_stub = StubDialogueProvider()

    # G8：双人物必须换数据源与上限——单目标桩只会返回阿雾那份正文，
    # 第二个人物拿不到材料（关系阶段就成了"没证据"，而不是"链路没通"）。
    # C 轮：双人物还要第三份"互动页"材料 → 上限开到 3 条查询 / 3 次抓取。
    source_provider = (StubTwoTargetSourceProvider() if two_person
                       else StubSearchExtractProvider())
    research_limits = (E2E_RESEARCH_LIMITS_THREE if two_person
                       else E2E_RESEARCH_LIMITS)

    stack = Stack(settings, ledger, out,
                  chat_provider=dialogue_stub,
                  source_provider=source_provider,
                  research_limits=research_limits)
    try:
        targets = stack.ensure_targets(names)
        if two_person:
            # C 轮：给第一个目标挂话题 → `plan_queries` 多产出一条话题查询 →
            # 桩返回"互动页"→ 材料流 3 份。不依赖排序，完全确定性。
            targets[0].topic = E2E_PAIR_TOPIC
        entity_id = targets[0].entity_id
        shown = "、".join(f"{t.display_name}={t.entity_id}" for t in targets)
        out(f"  目标实体        : {shown}（{label}）")
        out(f"  数据源          : {type(stack.search_provider).__name__}")
        out(f"  研究上限        : max_queries={research_limits.max_queries} "
            f"max_extracts={research_limits.max_extracts}")
        out(f"  临时库          : {stack.tmp}\\p45a.db（隔离，退出即删）")
        out("  路径            : BusinessOrchestrator.run（产品代码，无脚本代劳）")
        out(f"  原始响应捕获    : {'已挂载（Provider 唯一网络出口 _urlopen）' if raw_capture else '未挂载（注入的桩 Provider 无网络出口）'}")

        inner_extractor = stack.use_real_extractor(recorder)
        recorded = RecordingExtractor(inner_extractor)
        stack._extractor = recorded                # 编排器从 deps 取到的就是它
        orch = stack.orchestrator()
        if on_targets is not None:
            on_targets(stack, entity_id)

        # --- 发送前：把真实请求体构造出来核验（只构造，不发送）---
        preview = stack.research.run(stack.ctx, targets)
        if is_err(preview):
            out(f"  [FAIL] 研究链路失败：{preview.error.code} {preview.error.message}")
            problems.append("研究链路失败（编排器路径预检）")
            return
        preview_materials, _rejected = materials_from_research(stack, preview.value)
        if two_person:
            # ---- 方案 §9.1 发送前硬闸门（P1~P4）：任一不成立即**停在发送前** ----
            # 用 return 而不是 expect：软断言会照常发出请求，白花一次钱。
            gate: list[str] = []
            preview_keys = [str(getattr(m, "canonical_key", "") or getattr(m, "url", ""))
                            for m in preview_materials]
            if len(preview_materials) != 3:
                gate.append(f"P1 材料流 {len(preview_materials)} 份（应 3 份）")
            if not all(str(getattr(m, "quality_status", "")) == "VALID"
                       for m in preview_materials):
                gate.append("P1 有材料未过门禁（非 VALID）")
            if not all(str(getattr(m, "source_id", "")) for m in preview_materials):
                gate.append("P2 有材料未绑定已登记来源")
            if E2E_URL_3 not in preview_keys:
                gate.append("P3 互动页不在抽取输入里")
            if len({t.entity_id for t in targets}) != 2:
                gate.append("P4 目标不是 2 个互异的实体")
            if gate:
                out("  [FAIL] 发送前硬闸门未通过，**未发出任何请求**：")
                for g in gate:
                    out(f"      - {g}")
                problems.extend(gate)
                return
        req_built = inner_extractor.build_request(
            preview_materials, targets, model_hint=settings.model.model_id)
        body_builder = getattr(real_provider, "_build_body", None)
        if body_builder is None:
            out("  （注入的 Provider 无 _build_body：跳过线上请求体核验——离线测试用）")
        else:
            body_result = body_builder(stack.ctx, req_built)
            if is_err(body_result):
                out(f"  [FAIL] 请求体构造被拒：{body_result.error.message}")
                problems.append("请求体构造被拒")
                return
            body = body_result.value
            wire = json.dumps(body, ensure_ascii=False)
            expect(body.get("model") == "deepseek-flash",
                   "编排器路径：线上 model = deepseek-flash")
            expect(body.get("response_format") == {"type": "json_object"},
                   "编排器路径：response_format 已映射为 json_object")
            expect(body.get("thinking") == {"type": "disabled"},
                   "编排器路径：thinking = disabled")
            expect(body.get("stream") is False, "编排器路径：stream = false")
            expect(not out.scan(wire), "编排器路径：请求体里**不含**密钥")
            if two_person:
                # P3 的线上侧：请求体必须真的带 3 个 material_key，且含互动页。
                # 同样是硬闸门——不成立就**不发请求**。
                n_keys = wire.count("[material_key=")
                if n_keys != 3 or E2E_URL_3 not in wire:
                    out(f"  [FAIL] 发送前 P3 未通过：请求体 material_key {n_keys} 个"
                        f"（应 3）、互动页{'在' if E2E_URL_3 in wire else '**不在**'}"
                        "——**未发出任何请求**")
                    problems.append("发送前 P3：请求体 material_key 不符合预期")
                    return

        # --- 真实执行（1 次，失败不重试）---
        started = time.monotonic()
        result = None
        raised: BaseException | None = None
        try:
            result = orch.run(stack.ctx, stack.request(targets=targets))
        except BaseException as exc:                       # noqa: BLE001 —— 如实上报
            raised = exc
        elapsed = time.monotonic() - started
        out(f"  ① 编排器返回，耗时 {elapsed:.2f} s；真实模型调用次数 = {recorder.calls}")
        expect(recorder.calls == 1, "编排器路径：真实调用恰好 1 次（未重试）")
        expect(recorded.calls == 1, "编排器路径：抽取器被编排器调用恰好 1 次")

        if raised is not None:
            out(f"  [FAIL] 抛出异常：{type(raised).__name__}: {raised}")
            problems.append("编排器执行抛异常")
            return
        if is_err(result):
            out(f"  [FAIL] {result.error.code}: {result.error.message}")
            problems.append("编排器执行失败")
            return
        run = result.value

        out(f"  整体状态        : {run.status}"
            + ("（五阶段全执行）" if run.status == "SUCCEEDED"
               else (f"（{label}：{'关系/对话' if not two_person else '部分阶段'}按语义跳过）"
                     if run.status == "PARTIAL" else "")))
        out(f"  任务终态        : {run.task_status}")
        out("")
        print_stage_table(out, run)
        if two_person:
            # 双人物：关系阶段 EXECUTED → 对话阶段会真的跑，用的是**桩**对话 Provider
            # （零成本；桩产物只进 Conversation* / SIMULATED 记忆，绝不回流证据）
            expect(dialogue_stub.chat_calls >= 1,
                   "编排器路径：双人物下对话阶段真的执行（桩对话 Provider 被调用）")
        else:
            expect(dialogue_stub.chat_calls == 0,
                   "编排器路径：对话阶段被语义跳过，桩对话 Provider **零调用**")

        materials = list(orch._materials.get(run.task_id, []) or [])
        gate_valid = sum(1 for m in materials if str(getattr(m, "quality_status", "")))
        bound = sum(1 for m in materials if str(getattr(m, "source_id", "")))
        out("")
        out(f"  材料流          : {len(materials)} 份（带门禁结论 {gate_valid}，"
            f"已绑定来源 {bound}）")
        for m in materials:
            out(f"      · source_id={m.source_id} quality={m.quality_status} "
                f"url={m.url} 正文={len(m.snippet or '')} 字")
        expect(len(materials) >= 1, "编排器路径：材料流**非空**")
        expect(gate_valid >= 1, "编排器路径：至少 1 份材料带门禁结论（VALID）")
        expect(bound >= 1, "编排器路径：材料已绑定已登记来源（可追溯）")

        out(f"  来源数          : {len(run.source_ids)}")
        out(f"  证据数          : {len(run.evidence_ids)}")
        expect(len(run.evidence_ids) >= 1, "编排器路径：证据**非空**")

        # --- ④ 关系档案：直接回读（G7：版本号不得被空产出顶到 v2）---
        if run.relationship_profile_ids:
            out(f"  ④ 关系档案      : {run.relationship_profile_ids}")
            for rid in run.relationship_profile_ids:
                view = stack.relationship.get_relationship(stack.ctx, rid)
                if is_ok(view):
                    out(f"      · {rid} v{view.value.version} "
                        f"status={view.value.status} "
                        f"共享经历={len(view.value.shared_experiences)} "
                        f"互动模式={len(view.value.interaction_patterns)}")
                    expect(view.value.version == 1,
                           "关系档案版本停在 v1（重跑不得产生降级覆盖的新版本）")
        else:
            out("  ④ 关系档案      : （无）")
            if two_person:
                problems.append("双人物未产出关系档案")

        outcome = recorded.last_outcome
        if outcome is None:
            out("  [FAIL] 抽取器没有产出 outcome（编排器可能没走到证据阶段）")
            problems.append("抽取器未产出 outcome")
            return
        out("")
        dump_outcome(out, outcome)

        # --- ⑤ 人格档案：直接从产品代码产出的档案回读（不重建、不改写）---
        etypes = orch._entity_types(stack.ctx, [t.entity_id for t in targets])
        if is_err(etypes):
            problems.append("读取实体类型失败")
            return
        request_obj = stack.request(targets=targets)
        loaded = orch._load_evidence(stack.ctx, request_obj, list(run.evidence_ids))
        if is_err(loaded):
            problems.append("加载证据失败")
            return
        out("")
        out(f"  ⑤ 人格档案      : {run.persona_profile_ids}")
        drafts: dict[str, Any] = {}
        for t in targets:
            draft_t = BusinessOrchestrator._build_persona_draft(
                t.entity_id, loaded.get(t.entity_id, []), etypes.get(t.entity_id, ""))
            drafts[t.entity_id] = draft_t
            out(f"      · {t.display_name}（{t.entity_id}）"
                f"background_refs={len(draft_t.background.evidence_refs)} "
                f"traits={len(draft_t.traits)} habits={len(draft_t.habits)} "
                f"single_events={len(draft_t.single_events)} "
                f"experiences={len(draft_t.experiences)} "
                f"open_questions={len(draft_t.open_questions)}")
            for it in draft_t.traits:
                out(f"        * trait   : {it.text}  (stability={it.stability}, "
                    f"refs={it.evidence_refs})")
            for it in draft_t.single_events:
                out(f"        * single  : {it.text}  (stability={it.stability}, "
                    f"refs={it.evidence_refs})")
        draft = drafts[entity_id]

        # ---------------- [7b] 幂等重跑（**必须** 0 次真实调用） ----------------
        out.hr("[7b] 幂等重跑（同一条链再跑一次；**必须 0 次真实调用**）")
        calls_before = recorder.calls
        out("  两道闸门都在 Provider **之前**：① 发送层幂等守卫（同一意图 → 键相同）；"
            "② 预算闸门（max_calls 用满 → BUDGET_EXCEEDED）。")
        out("")

        # —— 闸门 ①：原样重跑同一条链（材料、目标、提示词版本全同 → 幂等键相同）——
        rerun = None
        rerun_raised: BaseException | None = None
        try:
            rerun = orch.run(stack.ctx, stack.request(targets=targets))
        except BaseException as exc:                        # noqa: BLE001 —— 如实上报
            rerun_raised = exc
        out(f"  ① 原样重跑 orch.run  → 真实模型调用次数 = {recorder.calls}"
            f"（重跑前 {calls_before}）")
        expect(recorder.calls == calls_before,
               "幂等重跑①：真实调用次数**未增加**（0 次新增请求）")
        if rerun_raised is not None:
            out(f"     重跑抛出 {type(rerun_raised).__name__}：{rerun_raised}"
                "（抛出也不花钱：守卫在 Provider 调用之前）")
        elif is_err(rerun):
            out(f"     重跑返回 Err：{rerun.error.code}: {rerun.error.message}")
        else:
            run2 = rerun.value
            codes2 = sorted({str(w.code) for s in run2.steps for w in s.warnings})
            out(f"     重跑整体状态 = {run2.status}；证据 {len(run2.evidence_ids)} 条；"
                f"告警码 {codes2}")
            if WARNING_MODEL_CALL_DUPLICATE_SKIPPED in codes2:
                out("     → 拦住它的是**发送层幂等守卫**（同一意图，幂等键相同）："
                    "在构造请求之前就返回，Provider 根本没被调用。")
            else:
                out("     → 未出现 DUPLICATE_SKIPPED 告警：需人工复核拦点。")

        # —— G7 回归锁：重跑的"空产出"不得把有内容的档案顶成空的新版本 ——
        # 重跑时证据登记被幂等拦下 ⇒ 本次没有可用证据 ⇒ 人物/关系草稿为空。
        # 修复前：会写出 v2（空）并把 v1 的 superseded_by 指过去 = 降级覆盖。
        for t in targets:
            pv = stack.persona.get_persona(stack.ctx, t.entity_id)
            if is_ok(pv):
                expect(pv.value.version == 1,
                       f"G7：重跑后 {t.display_name} 的人物档案仍为 v1（空产出未覆盖）")
                out(f"     · {t.display_name} 人物档案版本 = v{pv.value.version}"
                    f"（status={pv.value.status}，旧版内容未降级）")
        for rid in (run.relationship_profile_ids or []):
            rv = stack.relationship.get_relationship(stack.ctx, rid)
            if is_ok(rv):
                expect(rv.value.version == 1,
                       "G7：重跑后关系档案仍为 v1（空产出未覆盖）")
                out(f"     · {rid} 关系档案版本 = v{rv.value.version}"
                    f"（status={rv.value.status}，旧版内容未降级）")

        # —— 闸门 ②：换一份材料让幂等键变化，逼**预算闸门**自己出手 ——
        out("")
        out("  ② 预算探针：改掉材料的 canonical_key（幂等键随之变化）→ "
            "幂等守卫不再适用，必须由 **max_calls 闸门**硬拦。")
        probe_materials: list[SourceMaterial] = []
        if materials:
            probe = copy.deepcopy(materials[0])
            probe.canonical_key = f"{probe.canonical_key}#budget-probe"
            probe_materials = [probe]
        if not probe_materials:
            out("     材料流为空：跳过预算探针（前面已断言材料非空，此处仅为防御）")
        else:
            probe_result = inner_extractor.extract(
                stack.ctx, probe_materials, targets=targets)
            out(f"     探针后真实模型调用次数 = {recorder.calls}")
            expect(recorder.calls == calls_before,
                   "预算探针：真实调用次数**仍未增加**")
            if is_err(probe_result):
                out(f"     探针返回 Err：{probe_result.error.code} —— "
                    f"{probe_result.error.message}")
                expect("BUDGET" in str(probe_result.error.code),
                       "预算闸门以 BUDGET_EXCEEDED 拦下（可恢复暂停，不是静默跳过）")
            else:
                out("     ⚠️ 探针返回 Ok：预算闸门未按预期拦下，必须人工复核。")
                problems.append("预算探针未被拦下（防止多发一次真实请求）")

        # ---------------- [7c] 四层探针（C 轮：把"空"归因到唯一原因） ----
        facts = collect_round_c_facts(
            stack=stack, run=run, outcome=outcome, targets=targets,
            drafts=drafts, materials=materials)
        diag = facts["diagnostics"]
        out.hr("[7c] 四层探针：关系条目产出路径归因")
        out("  L1 材料        : "
            f"{diag['L1_materials']} 份进入材料流（其中带门禁结论 "
            f"{diag['L1_materials_with_gate']} 份）")
        out("  L2 抽取        : "
            f"{diag['L2_relation_candidates']} 条 RELATION 候选"
            f"（accepted 共 {len(outcome.claims)} 条）")
        if diag["L2_relation_claim_texts"]:
            for txt in diag["L2_relation_claim_texts"][:6]:
                out(f"      · {txt}")
        out("  L3 主体        : "
            f"{diag['L3_candidates_covering_both']} 条覆盖 ≥2 名目标")
        out("  L4 写入        : "
            f"shared_experiences={diag['L4_shared_experiences']} 条，"
            f"status={diag['L4_status']}")
        out("")
        out(f"  判定            : {diag['verdict']}")
        out(f"  含义            : {diag['verdict_text']}")
        if diag["verdict"] == "LINK_FAULT":
            out("  [FAIL] 落到判定矩阵第 4 行：RELATION 候选覆盖两人，档案却为空。")
            out("         这是**链路故障**，按 BUG 处理；**不得**用'再跑一次'掩盖。")
            problems.append("关系链路故障（L2/L3 有候选，L4 无条目）")
        stab = diag["stability"]
        out("")
        out(f"  稳定性判定      : {stab['with_verdict']}/{stab['total']} 条带 "
            "stability_verdict")
        if stab["missing"]:
            out(f"      [FAIL] 缺判定：{stab['missing']}")
            problems.append("存在缺稳定性判定的证据（G6 判定未覆盖全量）")
        if stab["declared_column_rewritten"]:
            out(f"      [FAIL] stability 列被改写（应保持登记值 UNKNOWN）："
                f"{stab['declared_column_rewritten']}")
            problems.append("stability 列被判定覆盖（破坏可追溯性）")
        if stab["occurrences_ne_distinct_sources"]:
            out(f"      [FAIL] occurrences != distinct_sources（G6-D2 去重失效）："
                f"{stab['occurrences_ne_distinct_sources']}")
            problems.append("稳定性判定未按来源去重")

        # ---------------- [8] 账本 ----------------
        out.hr("[8] 账本")
        dump_ledger(out, ledger, limits.max_cost, limits.currency)

        # ---------------- 产物落盘（供独立复算器） ----------------
        if dump_path:
            path = Path(dump_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            # 原始 HTTP 收发（含**原始响应体**）单独落盘：与复算产物分开，
            # 避免把"厂商原文"混进结构化的复算输入里。
            raw_path = path.with_name(path.stem + ".raw.json")
            raw_doc = {
                "schema": "p45a-raw-exchange/1",
                "produced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "note": "Provider 唯一网络出口（_urlopen）的应收发记录；"
                        "只记请求头**名**，不记头值（Authorization 的值就是密钥）",
                "exchanges": list(raw_capture.exchanges) if raw_capture else [],
            }
            raw_text = json.dumps(raw_doc, ensure_ascii=False, indent=2)
            if out.scan(raw_text):
                out("  [FAIL] 原始响应里出现密钥子串：**拒绝落盘**")
                problems.append("原始响应疑似含密钥（已拒绝落盘）")
            else:
                raw_path.write_text(raw_text, encoding="utf-8")
                out(f"  原始响应已落盘  : {raw_path}"
                    f"（{len(raw_doc['exchanges'])} 次 HTTP 收发，"
                    "请求头只记名不记值）")

            artifact = build_artifact(
                materials=materials, outcome=outcome,
                evidence_ids=list(run.evidence_ids),
                source_ids=list(run.source_ids), draft=draft, entity_id=entity_id,
                ledger=ledger, limits=limits, recorder=recorder,
                elapsed=elapsed, target=targets[0],
                mode="orchestrator-path",
                profile_id=(run.persona_profile_ids[0]
                            if run.persona_profile_ids else None),
                already_current=None,
                targets_info=facts["targets_info"],
                relationship_info=facts["relationship_info"],
                evidence_info=facts["evidence_info"],
                stage_warnings=facts["stage_warnings"],
                diagnostics=facts["diagnostics"])
            artifact["raw_exchange_path"] = str(raw_path)
            path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            out(f"  产物已落盘      : {path}（供 verify_p4_5a_outputs.py 离线复算）")

        # ---------------- [9] 安全自检 ----------------
        out.hr("[9] 安全自检")
        expect(not out.leak, "全部输出行均不含密钥")
        expect(recorder.calls <= 1, "真实调用次数 ≤ 1")
    finally:
        stack.teardown()


# ==========================================================================
# 主流程
# ==========================================================================
def parse_names(raw: str) -> list[str]:
    """`--names 阿雾,阿灯` → `["阿雾", "阿灯"]`（G8）。

    只做语法层面的拆分与去空；**是否与桩数据源目录对得上**由调用方如实报告，
    不在这里假装校验过（名称对不上时桩会返回默认样张，脚本会把这件事说出来）。
    """
    names = [part.strip() for part in str(raw or "").split(",") if part.strip()]
    if not names:
        raise ValueError("--names 不能为空")
    return names


def resolve_dump_path(given: str) -> Path:
    """`--dump` 的路径解析（G10：**绝不把产物写进仓库**）。

    根因：上一轮冒烟按文档用了 `--dump var/p45a_last_run.json`，于是在仓库里
    创建了 `var/`，把 `test_all_data_in_temp_dir_not_user_data`（断言仓库内不得
    出现用户数据目录）弄红。断言本身是对的，错的是脚本把默认落点放进了仓库。

    规则：
      - 未给 `--dump`（空串）→ `<系统临时目录>/p45a_last_run.json`；
      - 相对路径 → 一律解析到系统临时目录下（`var/x.json` → `<temp>/var/x.json`）；
      - 绝对路径 → 原样尊重（用户明确指定了落点）。
    """
    if not str(given or "").strip():
        return Path(tempfile.gettempdir()) / "p45a_last_run.json"
    path = Path(given)
    if not path.is_absolute():
        path = Path(tempfile.gettempdir()) / path
    return path


def build_parser() -> argparse.ArgumentParser:
    """命令行参数（独立出来是为了让离线测试能直接断言默认值）。"""
    parser = argparse.ArgumentParser(description="P4.5-A 端到端验证（--run 会真实计费）")
    parser.add_argument("--dry", action="store_true",
                        help="预检 + A11 + 双路径诊断，绝不发出真实请求")
    parser.add_argument("--a11-only", action="store_true", help="只跑 A11 负样例")
    parser.add_argument("--run", action="store_true",
                        help="真实执行（默认编排器路径；1 次真实调用，会产生费用）")
    parser.add_argument("--path", choices=("script", "orchestrator"),
                        default="orchestrator",
                        help="真实运行走哪条链路（默认 orchestrator = 产品代码全链）")
    parser.add_argument("--names", default=E2E_TARGET.display_name,
                        metavar="NAME[,NAME]",
                        help="目标人物名，逗号分隔：1 个 = 单人物，2 个 = 双人物全链"
                             f"（默认 {E2E_TARGET.display_name}；只能是合成虚构名，AC-16）")
    parser.add_argument("--dump", default="", metavar="PATH",
                        help="把本次运行产物写成 JSON（供 verify_p4_5a_outputs.py 离线复算）；"
                             "相对路径一律落到系统临时目录，绝不写进仓库")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        names = parse_names(args.names)
    except ValueError as exc:
        print(f"参数错误：{exc}")
        return 2
    # G10：产物落点由脚本统一解析——相对路径一律进系统临时目录，绝不写进仓库
    dump_path = resolve_dump_path(args.dump)

    loaded = load_settings(overrides=E2E_CONFIG)
    if is_err(loaded):
        print("配置加载失败：", loaded.error.message)
        return 2
    settings = loaded.value
    secret = str(settings.secrets.provider_api_key or "")
    out = Out(secret)

    ledger = ExternalUsageLedger()
    limits = model_limits_from_settings(settings)
    plan = model_plan(settings)
    provider_cfg = chat_provider_config(settings)
    problems: list[str] = []

    def expect(cond: bool, desc: str) -> None:
        out(f"  [{'OK ' if cond else 'FAIL'}] {desc}")
        if not cond:
            problems.append(desc)

    out("P4.5-A 端到端验证（方案 B：桩检索 + 真实模型）")
    mode = ("A11 门禁负样例" if args.a11_only
            else "真实执行（**会产生费用**，1 次调用）" if args.run
            else "DRY-RUN（绝不发请求）")
    out(f"模式   ：{mode}   路径：{args.path}")
    out(f"目标   ：{'、'.join(names)}（{len(names)} 人）")
    out(f"产物落点：{dump_path}（系统临时目录；仓库内不落任何文件）")
    out(f"时间   ：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    out(f"密钥   ：{'已设置（内容不显示）' if secret else '未设置'}  长度={len(secret)}")

    # ---------------- [1] 配置核验 ----------------
    out.hr("[1] 配置核验（不发请求）")
    for key in ("enabled", "provider", "model_id", "currency", "price_tier",
                "price_per_1m_in", "price_per_1m_out", "price_per_1k_in",
                "price_per_1k_out", "price_source", "price_checked_at",
                "max_cost_total", "max_cost_per_task", "max_cost_effective",
                "max_calls", "est_tokens_in", "est_tokens_out",
                "est_cost_per_call", "require_gate_verdict", "secret_status"):
        out(f"  {key:22s}: {plan[key]}")
    out("")
    expect(plan["enabled"] is True, "模型抽取已启用")
    expect(plan["provider"] == "deepseek", "服务商 = deepseek")
    expect(plan["model_id"] == "deepseek-flash", "模型 ID = deepseek-flash（**不换模型**）")
    expect(plan["currency"] == "USD", "币种 = USD")
    expect(plan["price_tier"] == "peak", "价格档位 = peak")
    expect(plan["max_cost_effective"] == 0.25, "生效费用上限 = 0.25 USD")
    expect(plan["max_calls"] == 1, "调用次数上限 = 1")
    expect(plan["require_gate_verdict"] is True, "require_gate_verdict = True（严格门禁）")
    expect(provider_cfg.max_retries == 0, "Provider 重试次数强制 = 0")
    expect(E2E_RESEARCH_LIMITS.max_extracts == 1, "研究抓取上限 max_extracts = 1")
    expect(secret != "", "模型密钥已设置（只检查存在性）")

    # ---------------- [2] A11 ----------------
    out.hr("[2] A11：门禁负样例（桩 Provider，零成本）")
    a11 = a11_gate_negatives(settings, ledger, out)
    for row in a11:
        if not row["ok"]:
            problems.append(f"A11 负样例未按预期拦住：{row['case']}")

    if args.a11_only:
        return _finish(out, problems, mode="A11")

    # ---------------- [3] 合成材料与门禁 ----------------
    out.hr("[3] 合成虚构材料（AC-16）与真实门禁复算")
    out(f"  title           : {E2E_TITLE}")
    out(f"  url             : {E2E_URL}")
    out(f"  目标            : {E2E_TARGET.entity_id} / {E2E_TARGET.display_name}"
        "（**单个人物**）")
    for i, p in enumerate(E2E_PASSAGES, 1):
        out(f"  [{i}] {p}")
    out("")
    print_quality(out, E2E_TEXT, [E2E_TARGET.display_name])
    q = assess_content_quality(E2E_TEXT, url=E2E_URL,
                               target_names=[E2E_TARGET.display_name])
    expect(q.status == QUALITY_VALID,
           f"合成正文通过真实正文质量门禁（status={q.status}）")

    # ---------------- [4] 装配与请求预检 ----------------
    out.hr("[4] 装配与请求预检（离线构造，不发送）")
    service_ledger = ExternalUsageLedger()
    stack = Stack(settings, service_ledger, out)
    try:
        out(f"  临时库          : {stack.tmp}\\p45a.db（隔离，退出即删）")
        out("  账本            : 新建内存账本（**未创建/未重置任何账本文件**）")
        out(f"  数据源          : {type(stack.search_provider).__name__}"
            "（桩：search + extract 均离线）")
        out(f"  研究上限        : max_queries={E2E_RESEARCH_LIMITS.max_queries} "
            f"max_extracts={E2E_RESEARCH_LIMITS.max_extracts}")

        # --- 4a. 目标实体（必须先登记：来源登记会校验 subject_entity_ids 存在）---
        entity_id = stack.ensure_entity("阿雾")
        out(f"  目标实体        : {entity_id}（FICTIONAL_CHARACTER）")

        # --- 4b. 研究链路（桩数据源，零成本）---
        research = stack.research.run(stack.ctx, [stack.target])
        if is_err(research):
            out(f"  [FAIL] 研究链路失败：{research.error.code} {research.error.message}")
            problems.append("研究链路失败")
            return _finish(out, problems, mode="预检")
        ro = research.value
        out(f"  ① 研究：来源 {len(ro.source_ids)} 条 / 抓取 {len(ro.extracted)} 条 / "
            f"材料（检索摘要）{len(ro.materials)} 份 / 门禁拒收 {len(ro.rejected_extracts)} 条")
        for e in ro.extracted:
            st = e.quality.status if e.quality else "?"
            out(f"      抓取条目 quality={st} excerpt={len(e.excerpt)} 字")
        out(f"      检索摘要材料 quality_status="
            f"{[m.quality_status or '(未声明)' for m in ro.materials]}")

        materials, rejected = materials_from_research(stack, ro)
        out(f"  ①→② 脚本侧材料交接：{len(materials)} 份，"
            f"quality_status={[m.quality_status for m in materials]}，"
            f"source_id={[bool(m.source_id) for m in materials]}")
        expect(len(materials) >= 1, "至少 1 份材料（门禁 VALID）进入抽取器")
        expect(all(m.source_id for m in materials), "材料已绑定已登记来源 ID")

        # --- 4b. 请求预检 ---
        provider_result = chat_provider_from_settings(settings)
        if is_err(provider_result):
            out(f"  [FAIL] 无法装配 Provider：{provider_result.error.message}")
            problems.append("Provider 装配失败")
            return _finish(out, problems, mode="预检")
        real_provider = provider_result.value
        recorder = CountingChatProvider(real_provider)
        extractor = stack.use_real_extractor(recorder)

        request: ChatRequest = extractor.build_request(
            materials, [stack.target], model_hint=settings.model.model_id)
        out(f"  response_format : {request.response_format}")
        out(f"  max_output_tokens: {request.max_output_tokens}")
        out(f"  temperature     : {request.temperature}")
        out(f"  消息数          : {len(request.messages)}")
        body_result = real_provider._build_body(stack.ctx, request)
        if is_err(body_result):
            out(f"  [FAIL] 请求体构造被拒：{body_result.error.message}")
            problems.append("请求体构造被拒")
            return _finish(out, problems, mode="预检")
        body = body_result.value
        wire = json.dumps(body, ensure_ascii=False)
        expect(body.get("model") == "deepseek-flash", "线上 model = deepseek-flash")
        expect(body.get("response_format") == {"type": "json_object"},
               "response_format 已映射为 json_object")
        expect(body.get("thinking") == {"type": "disabled"}, "thinking = disabled")
        expect(body.get("stream") is False, "stream = false")
        expect("json" in json.dumps(body.get("messages"), ensure_ascii=False).lower(),
               '提示词含 "json" 字样')
        expect("metadata" not in body, "metadata（含 prompt_version）不外发")
        expect(not out.scan(wire), "请求体里**不含**密钥")
        expect(not out.scan(repr(real_provider)), "Provider repr 里**不含**密钥")

        # ---------------- [5] 编排器路径诊断（G4 已修复的验证） ----------------
        out.hr("[5] 编排器路径诊断（G4 修复验证；桩 Provider，零成本）")

        out("  —— 5a. 单人物请求 ——")
        out("  （单人物在 `_overall_status` 下**只能** PARTIAL：关系/对话阶段按语义"
            "跳过，参与者 <2，与 G4 无关）")
        diag_one = diagnose_orchestrator_path(settings, out)
        _print_diag(out, diag_one)
        expect(diag_one.get("stub_chat_calls") == 1,
               "单人物：编排器路径**调用到模型**恰好 1 次（G4 已修）")
        expect(diag_one.get("materials_in_flow", 0) >= 1,
               "单人物：材料流**非空**（抓取正文已进入材料流）")
        expect(diag_one.get("materials_gate_valid", 0) >= 1,
               "单人物：材料流里**至少 1 份带门禁结论**（quality_status=VALID）")
        expect(diag_one.get("materials_bound", 0) >= 1,
               "单人物：材料已绑定已登记来源（可追溯）")
        expect((diag_one.get("evidence_count") or 0) >= 1,
               "单人物：证据**非空**（材料真的被抽取并登记了）")

        out("")
        out("  —— 5b. 双人物请求（五阶段全执行的唯一证据）——")
        diag_two = diagnose_orchestrator_path(settings, out, two_person=True)
        _print_diag(out, diag_two)
        expect(diag_two.get("status") == "SUCCEEDED",
               "双人物：整体状态 = SUCCEEDED（**不再是 PARTIAL**）")
        expect(all(v == STEP_EXECUTED
                   for v in (diag_two.get("stage_status") or {}).values()),
               "双人物：五个阶段全部 EXECUTED")
        expect(diag_two.get("stub_chat_calls") == 1,
               "双人物：编排器路径调用到模型恰好 1 次")
        expect((diag_two.get("evidence_count") or 0) >= 1, "双人物：证据非空")

        # ---------------- [5c] 真实运行路径 ----------------
        if args.path == "orchestrator":
            out("")
            out("  `--path=orchestrator`：真实运行**已接线**"
                "（产品代码全链：研究 → 抽取 → 证据登记 → 门禁 → 人格档案）。")
            out("  单人物请求：关系/对话阶段按语义 SKIPPED（参与者 <2），"
                "链上**没有**任何桩对话 Provider 被调用。")
            if not args.run:
                out("  本次是 --dry：**不发出任何真实请求**；"
                    "下方 [6] 给出『将发生什么』的预告。")
            else:
                out("  本次 `--run` 将走编排器路径：**1 次真实模型调用**，"
                    "失败不重试。")

        # ---------------- [6] 预算与"将发生什么" ----------------
        out.hr("[6] 预算与真实调用预告")
        est = estimate_call_cost_usd(settings)
        pause, why = service_ledger.should_pause(limits.max_cost)
        expect(not pause, f"预算闸门未处于暂停态（{why or '正常'}）")
        out(f"  将发生          : **1 次**真实 POST https://api.deepseek.com/chat/completions")
        out(f"  预估费用        : ≤ {est:.4f} USD（est {limits.est_tokens_in} in / "
            f"{limits.est_tokens_out} out @ USD {limits.price_per_1k_in}/"
            f"{limits.price_per_1k_out} per 1K）")
        out(f"  上限            : {limits.max_cost} USD（已占用 "
            f"{service_ledger.occupied_cost():.6f}）")
        out(f"  max_retries     : {provider_cfg.max_retries}（失败不重试）")
        out(f"  门禁            : require_gate_verdict="
            f"{settings.model.require_gate_verdict}")

        if not args.run:
            out("")
            out("DRY-RUN 结束：预检全部通过，**未发出任何真实请求**。")
            out("要真实执行请显式加 --run（会产生费用）。")
            return _finish(out, problems, mode="dry")

        if problems:
            out("")
            out("预检未通过：**不发出任何真实请求**。")
            return _finish(out, problems, mode="dry")

        # ---------------- [7] 真实执行（1 次，失败不重试） ----------------
        if args.path == "orchestrator":
            run_orchestrator_real(settings, out, problems, expect=expect,
                                  ledger=service_ledger, limits=limits,
                                  dump_path=str(dump_path), names=names)
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)

        out.hr("[7] 真实执行（脚本侧装配，1 次调用，失败不重试）")
        started = time.monotonic()
        extracted = None
        raised: BaseException | None = None
        try:
            extracted = extractor.extract(stack.ctx, materials, targets=[stack.target])
        except BaseException as exc:                        # noqa: BLE001 —— 如实上报
            raised = exc
        elapsed = time.monotonic() - started
        out(f"  ② 抽取返回，耗时 {elapsed:.2f} s；真实调用次数 = {recorder.calls}")

        if extracted is not None and is_err(extracted):
            out(f"  [FAIL] {extracted.error.code}: {extracted.error.message}")
            problems.append("模型抽取失败")
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)
        if raised is not None:
            out(f"  [FAIL] 抛出异常：{type(raised).__name__}: {raised}")
            problems.append("模型抽取抛异常")
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)

        outcome = extracted.value
        dump_outcome(out, outcome)
        expect(recorder.calls == 1, "真实调用恰好 1 次（未重试）")

        # ③④ 证据登记 + 可追溯闸门（复用产品代码同一批方法）
        orch = stack.orchestrator()
        step = orch._register_candidates(stack.ctx, outcome, list(ro.source_ids))
        if is_err(step):
            out(f"  [FAIL] 证据登记失败：{step.error.code} {step.error.message}")
            problems.append("证据登记失败（可追溯闸门）")
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)
        evidence_ids = list(step.artifact_ids.get("evidence_ids", []))
        out(f"  ③④ 证据登记     : {len(evidence_ids)} 条（来源可追溯闸门已通过）")
        for w in step.warnings:
            out(f"      ! [{w.code}] {w.message}")

        # ⑤ 人格档案（复用 _load_evidence / _build_persona_draft / _write_persona）
        etypes = orch._entity_types(stack.ctx, [entity_id])
        if is_err(etypes):
            problems.append("读取实体类型失败")
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)
        request_obj = stack.request()
        loaded = orch._load_evidence(stack.ctx, request_obj, evidence_ids)
        if is_err(loaded):
            problems.append("加载证据失败")
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)
        items = loaded.get(entity_id, [])
        draft = BusinessOrchestrator._build_persona_draft(
            entity_id, items, etypes.get(entity_id, ""))
        written = orch._write_persona(stack.ctx, entity_id, draft)
        if is_err(written):
            out(f"  [FAIL] 人格档案写入失败：{written.error.code}")
            problems.append("人格档案写入失败")
            return _finish(out, problems, mode="real", ledger=service_ledger,
                           limits=limits)
        profile_id, already = written
        out(f"  ⑤ 人格档案      : {profile_id}（already_current={already}）")
        out(f"      条目统计     : background_refs={len(draft.background.evidence_refs)} "
            f"traits={len(draft.traits)} habits={len(draft.habits)} "
            f"single_events={len(draft.single_events)} "
            f"experiences={len(draft.experiences)} "
            f"open_questions={len(draft.open_questions)}")
        for it in draft.traits:
            out(f"      * trait   : {it.text}  (stability={it.stability}, "
                f"refs={it.evidence_refs})")
        for it in draft.single_events:
            out(f"      * single  : {it.text}  (stability={it.stability}, "
                f"refs={it.evidence_refs})")

        out.hr("[8] 账本")
        dump_ledger(out, service_ledger, limits.max_cost, limits.currency)

        # ---------------- 产物落盘（供独立复算器） ----------------
        if dump_path:
            artifact = build_artifact(
                materials=materials, outcome=outcome, evidence_ids=evidence_ids,
                source_ids=list(ro.source_ids), draft=draft, entity_id=entity_id,
                ledger=service_ledger, limits=limits, recorder=recorder,
                elapsed=elapsed, target=stack.target,
                mode="script-side-assembly",
                profile_id=profile_id, already_current=already)
            path = Path(dump_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            out(f"  产物已落盘      : {path}（供 verify_p4_5a_outputs.py 离线复算）")

        out.hr("[9] 安全自检")
        expect(not out.leak, "全部输出行均不含密钥")
        expect(recorder.calls <= 1, "真实调用次数 ≤ 1")
        return _finish(out, problems, mode="real", ledger=service_ledger,
                       limits=limits)
    finally:
        stack.teardown()


def collect_round_c_facts(*, stack: "Stack", run, outcome, targets, drafts,
                          materials) -> dict:
    """回读本轮运行的事实，产出产物块 + **四层探针**判定（纯只读，不改库）。

    四层探针（方案 §7.1）：

    | 层 | 观测量 | 为空意味着 |
    |---|---|---|
    | L1 材料 | 进入材料流份数 / 带门禁结论份数 | 研究或门禁问题（与模型无关）|
    | L2 抽取 | accepted 里 `dimension=RELATION` 的候选条数 | 模型没读出来 |
    | L3 主体 | 上述候选中 `subject_entity_ids` 覆盖两人的条数 | 读出来的是单人视角 |
    | L4 写入 | `shared_experiences` 条数 + 档案 `status` | **L2/L3 都有而 L4 空 = 链路故障** |

    判定矩阵见方案 §7.2。把"空"归因到**唯一**原因，是为了避免把
    "链路故障"当成"证据不足"，再用"再跑一次"掩盖缺陷。
    """
    ctx = stack.ctx
    target_ids = {t.entity_id for t in targets}

    # ---------- L1：材料 ----------
    l1_total = len(materials)
    l1_valid = sum(1 for m in materials if str(getattr(m, "quality_status", "") or ""))

    # ---------- L2 / L3：抽取候选 ----------
    claims = list(getattr(outcome, "claims", []) or [])
    rel_claims = [c for c in claims if "RELATION" in str(getattr(c, "dimension", ""))]
    l2 = len(rel_claims)
    l3 = sum(1 for c in rel_claims
             if len(set(getattr(c, "subject_entity_ids", []) or []) & target_ids) >= 2)

    # ---------- 逐条证据（G6-D1 的运行时证据） ----------
    evidence_info: list[dict] = []
    stab_missing: list[str] = []
    declared_rewritten: list[str] = []
    occurrences_mismatch: list[str] = []
    for eid in list(run.evidence_ids or []):
        got = stack.evidence.get_evidence(ctx, eid)
        if is_err(got):
            stab_missing.append(f"{eid}（读取失败 {got.error.code}）")
            continue
        view = got.value
        verdict = view.stability_verdict if isinstance(view.stability_verdict, dict) else None
        evidence_info.append({
            "id": view.id,
            "claim": view.claim,
            "dimension": str(view.dimension),
            "subject_entity_ids": list(view.subject_entity_ids),
            "source_id": view.source_id,
            "stability": str(view.stability),
            "stability_verdict": dict(verdict) if verdict else None,
        })
        if verdict is None:
            stab_missing.append(view.id)
        else:
            # G6-D1：`stability` 列是登记时的声明，**不得**被判定覆盖
            if str(view.stability) != "UNKNOWN":
                declared_rewritten.append(view.id)
            # G6-D2：按 source_id 去重后 occurrences 恒等于 distinct_sources。
            # **只对有来源的证据成立**：INFERENCE 证据按登记规则不带单一来源
            # （`source_id=None`，推断来自多条证据而非某个来源），此时
            # occurrences=1 / distinct_sources=0 是正确结果，不是去重失效。
            if view.source_id:
                if int(verdict.get("occurrences", -1)) != int(
                        verdict.get("distinct_sources", -2)):
                    occurrences_mismatch.append(view.id)

    # ---------- L4：关系档案写入 ----------
    relationship_info: list[dict] = []
    l4_items = 0
    for rid in list(run.relationship_profile_ids or []):
        got = stack.relationship.get_relationship(ctx, rid)
        if is_err(got):
            relationship_info.append({"id": rid, "error": str(got.error.code)})
            continue
        view = got.value
        items = list(getattr(view, "shared_experiences", []) or [])
        l4_items += len(items)
        relationship_info.append({
            "id": view.id,
            "version": view.version,
            "status": str(view.status),
            "cardinality": str(view.cardinality),
            "participant_entity_ids": list(view.participant_entity_ids),
            "shared_experiences": [{
                "text": str(getattr(it, "text", "")),
                "evidence_refs": [str(r) for r in (getattr(it, "evidence_refs", []) or [])],
                "participant_entity_ids": [str(p) for p in
                                           (getattr(it, "participant_entity_ids", []) or [])],
            } for it in items],
            "interaction_patterns": len(getattr(view, "interaction_patterns", []) or []),
            "perspectives": sorted((getattr(view, "perspectives", {}) or {}).keys()),
            "open_questions": list(getattr(view, "open_questions", []) or []),
            "superseded_by": getattr(view, "superseded_by", None),
        })

    # ---------- 逐目标人物档案 ----------
    targets_info: list[dict] = []
    persona_by_id = {str(pid): pid for pid in (run.persona_profile_ids or [])}
    for t in targets:
        got = stack.persona.get_persona(ctx, t.entity_id)
        view = got.value if is_ok(got) else None
        d = drafts.get(t.entity_id)
        targets_info.append({
            "entity_id": t.entity_id,
            "display_name": t.display_name,
            "persona_profile_id": (view.id if view is not None
                                   else persona_by_id.get(t.entity_id)),
            "version": (view.version if view is not None else None),
            "status": (str(view.status) if view is not None else None),
            "background_refs": ([str(r) for r in d.background.evidence_refs]
                                if d is not None else []),
            "traits": [{"text": i.text, "stability": str(i.stability),
                        "evidence_refs": [str(r) for r in i.evidence_refs]}
                       for i in (d.traits if d is not None else [])],
            "habits": [{"text": i.text, "stability": str(i.stability)}
                       for i in (d.habits if d is not None else [])],
            "single_events": [{"text": i.text, "stability": str(i.stability)}
                              for i in (d.single_events if d is not None else [])],
            "experiences": len(d.experiences if d is not None else []),
            "open_questions": len(d.open_questions if d is not None else []),
        })

    # ---------- 阶段告警码 ----------
    stage_warnings = {str(s.name): sorted({str(w.code) for w in (s.warnings or [])})
                      for s in (run.steps or [])}

    # ---------- 判定矩阵（§7.2） ----------
    statuses = [r.get("status") for r in relationship_info if "status" in r]
    if l1_valid < 3:
        verdict = "PRECONDITION_NOT_MET"
        verdict_text = "未通过前置：材料不足 3 份 VALID，不进入归因"
    elif l4_items >= 1 and statuses and all(s == "OK" for s in statuses):
        verdict = "LINK_OK"
        verdict_text = "链路通：双人证据已变成条目，档案 status=OK"
    elif l2 == 0:
        verdict = "INSUFFICIENT_NO_RELATION_DIMENSION"
        verdict_text = "证据不足：模型未产出任何 RELATION 维度候选"
    elif l3 == 0:
        verdict = "INSUFFICIENT_SINGLE_SUBJECT"
        verdict_text = "证据不足：RELATION 候选都是单人视角（未覆盖两名参与者）"
    elif l4_items == 0:
        verdict = "LINK_FAULT"
        verdict_text = "**链路故障**：RELATION 候选覆盖两人，但关系档案没有条目"
    else:
        verdict = "PARTIAL"
        verdict_text = "关系档案有条目但 status 非 OK，需人工复核"

    return {
        "targets_info": targets_info,
        "relationship_info": relationship_info,
        "evidence_info": evidence_info,
        "stage_warnings": stage_warnings,
        "diagnostics": {
            "L1_materials": l1_total,
            "L1_materials_with_gate": l1_valid,
            "L2_relation_candidates": l2,
            "L2_relation_claim_texts": [str(getattr(c, "claim", ""))[:80]
                                        for c in rel_claims],
            "L3_candidates_covering_both": l3,
            "L4_shared_experiences": l4_items,
            "L4_status": statuses,
            "verdict": verdict,
            "verdict_text": verdict_text,
            "stability": {
                "total": len(evidence_info),
                "with_verdict": sum(1 for e in evidence_info
                                    if e.get("stability_verdict")),
                "missing": stab_missing,
                "declared_column_rewritten": declared_rewritten,
                "occurrences_ne_distinct_sources": occurrences_mismatch,
            },
        },
    }


def build_artifact(*, materials, outcome, evidence_ids, source_ids, draft,
                   entity_id, ledger, limits, recorder, elapsed, target,
                   mode: str = "script-side-assembly",
                   profile_id: str | None = None,
                   already_current: bool | None = None,
                   targets_info: list[dict] | None = None,
                   relationship_info: list[dict] | None = None,
                   evidence_info: list[dict] | None = None,
                   stage_warnings: dict | None = None,
                   diagnostics: dict | None = None) -> dict:
    """把本次运行的关键事实写成 JSON（**只写事实，不写密钥**）。

    落盘的意义：让 `verify_p4_5a_outputs.py` 能在**不重跑、不联网**的前提下
    独立复算摘录可追溯性与费用；复算器不 import `claim_validation`，
    避免"用被测代码验证被测代码"。

    C 轮新增四个**可选**块（默认 `None` → 不写，单人物旧产物逐字不变）：
      - `targets[]`：每名目标的人物档案摘要（双人物必须有 2 条）；
      - `relationship[]`：关系档案回读（含 `status` / `shared_experiences`）；
      - `evidence[]`：逐条证据的 `stability` 与 `stability_verdict`（G6-D1 的运行时证据）；
      - `diagnostics`：四层探针 + 阶段告警码（把"空"归因到唯一原因）。
    """
    records = []
    for rec in ledger.records():
        records.append({
            "idempotency_key": getattr(rec, "idempotency_key", ""),
            "provider": getattr(rec, "provider", ""),
            "operation": getattr(rec, "operation", ""),
            "outcome": getattr(rec, "outcome", ""),
            "request_id": getattr(rec, "request_id", None),
            "cost_estimate": getattr(rec, "cost_estimate", None),
            "cost_confirmed": getattr(rec, "cost_confirmed", None),
            "cost_status": getattr(rec, "cost_status", ""),
            "cost_source": getattr(rec, "cost_source", ""),
            "currency": getattr(rec, "currency", ""),
            "tokens_in": getattr(rec, "tokens_in", None),
            "tokens_out": getattr(rec, "tokens_out", None),
        })
    totals = ledger.totals()
    # Provider **响应里**报的 token（与抽取器/账本那条链路相互独立）——
    # 独立复算器用它复算金额，才能构成"跨组件"的交叉核对。
    resp = getattr(recorder, "last_result", None)
    resp_usage = None
    if resp is not None and not is_err(resp):
        u = getattr(resp.value, "usage", None)
        resp_usage = {
            "tokens_in": getattr(u, "tokens_in", None),
            "tokens_out": getattr(u, "tokens_out", None),
            "cost_confirmed": getattr(u, "cost_confirmed", None),
            "cost_status": getattr(u, "cost_status", ""),
            "model_id": getattr(resp.value, "model_id", ""),
            "finish_reason": getattr(resp.value, "finish_reason", ""),
            "raw_ref": getattr(resp.value, "raw_ref", ""),
        }
    return {
        "schema": "p45a-e2e-artifact/1",
        "produced_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": mode,
        "target": {"entity_id": target.entity_id if target else entity_id,
                   "display_name": target.display_name if target else ""},
        "config": {
            "provider": "deepseek", "model_id": "deepseek-flash",
            "currency": limits.currency, "price_tier": "peak",
            "price_per_1k_in": limits.price_per_1k_in,
            "price_per_1k_out": limits.price_per_1k_out,
            "max_cost": limits.max_cost, "max_calls": limits.max_calls,
            "est_tokens_in": limits.est_tokens_in,
            "est_tokens_out": limits.est_tokens_out,
            "require_gate_verdict": True,
        },
        "sources": list(source_ids),
        "materials": [{
            "source_id": m.source_id, "title": m.title, "url": m.url,
            "canonical_key": m.canonical_key, "snippet": m.snippet,
            "quality_status": m.quality_status,
        } for m in materials],
        "extraction": {
            "method": outcome.method,
            "verdict": outcome.verdict,
            "validation_detail": outcome.validation_detail,
            "rejected": [{"reason": r.reason, "message": r.message}
                         for r in outcome.rejected],
            "usage": {
                "tokens_in": getattr(outcome.usage, "tokens_in", None),
                "tokens_out": getattr(outcome.usage, "tokens_out", None),
            },
            "response_usage": resp_usage,
            "pending": [{"reason": p.reason, "message": p.message}
                        for p in outcome.pending],
            "claims": [{
                "key": c.key, "claim": c.claim, "tier": str(c.tier),
                "dimension": str(c.dimension),
                "subject_entity_ids": list(c.subject_entity_ids),
                "source_id": c.source_id, "verbatim_quote": c.verbatim_quote,
                "locator": c.locator,
                "derivation_keys": list(c.derivation_keys),
                "confidence": c.confidence, "tags": list(c.tags),
            } for c in outcome.claims],
        },
        "evidence_ids": list(evidence_ids),
        "persona": {
            "profile_id": profile_id, "already_current": already_current,
            "background_refs": list(draft.background.evidence_refs),
            "background_text": draft.background.text,
            "traits": [{"text": i.text, "stability": str(i.stability),
                        "evidence_refs": list(i.evidence_refs)} for i in draft.traits],
            "habits": [{"text": i.text, "stability": str(i.stability),
                        "evidence_refs": list(i.evidence_refs)} for i in draft.habits],
            "single_events": [{"text": i.text, "stability": str(i.stability),
                               "evidence_refs": list(i.evidence_refs)}
                              for i in draft.single_events],
            "experiences": [{"text": i.text, "evidence_refs": list(i.evidence_refs)}
                            for i in draft.experiences],
            "open_questions": list(draft.open_questions),
        },
        # ---- C 轮：多目标 / 关系 / 逐条稳定性（可选块，None → 省略） ----
        **({"targets": list(targets_info)} if targets_info is not None else {}),
        **({"relationship": list(relationship_info)}
           if relationship_info is not None else {}),
        **({"evidence": list(evidence_info)} if evidence_info is not None else {}),
        **({"stage_warnings": dict(stage_warnings)}
           if stage_warnings is not None else {}),
        **({"diagnostics": dict(diagnostics)} if diagnostics is not None else {}),
        "ledger": {
            "records": records,
            "requests": totals.requests,
            "confirmed_cost": totals.confirmed_cost,
            "estimated_cost": totals.estimated_cost,
            "unknown_hold_cost": totals.unknown_hold_cost,
            "unknown_requests": totals.unknown_requests,
            "failed_requests": totals.failed_requests,
            "occupied_cost": ledger.occupied_cost(),
        },
        "calls": {"real_chat_calls": recorder.calls,
                  "wallclock_seconds": round(elapsed, 3)},
    }


def _finish(out: Out, problems: list[str], *, mode: str, ledger=None,
            limits=None) -> int:
    out.hr("结论")
    if problems:
        out(f"  问题 {len(problems)} 条：")
        for p in problems:
            out(f"    - {p}")
    out(f"  模式            : {mode}")
    out(f"  预检            : {'全部通过' if not problems else '存在失败项'}")
    out(f"  密钥泄漏        : {'**是**' if out.leak else '否'}")
    if ledger is not None and limits is not None:
        out(f"  已占用          : {ledger.occupied_cost():.6f} {limits.currency}")
    return 3 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
