"""P6.0 对话质量评估的**运行入口**（分 mock / real 两阶段）。

    # 阶段 A：不联网、不花钱，只验证评估器本身判得对不对
    python scripts/run_dialogue_eval.py --mock

    # 阶段 B：真实模型（**需要授权**；会花钱，先算账）
    python scripts/run_dialogue_eval.py --real --smoke  --db <persona.db> --project prj_x
    python scripts/run_dialogue_eval.py --real --small  --db <persona.db> --project prj_x
    python scripts/run_dialogue_eval.py --real --full   --db <persona.db> --project prj_x

    # 只跑部分维度（逗号分隔 A/B/C/D/E）——Step 1 验证根因修复用的最小跑法
    python scripts/run_dialogue_eval.py --real --small --dims A,B --db <persona.db> --project prj_x

# 为什么分 mock / real 两个入口而不是一个 `--provider` 参数

阶段 A 的价值是"**评估器坏了能被发现**"。它必须**零成本、零网络**，否则一忙起来
就会被跳过，负例失效就没人知道——本项目已经栽过三次（D-A / D-B / D-D 全靠真跑
才发现）。所以 mock 是默认路径，real 必须显式给 `--real`。

# 阶段 A 跑两遍，各查一件事

1. **判据关**（`--mock` 的第 1 段）：直接拿用例自带的正/负例回复喂给断言器，
   验证"该过的过、该挂的挂"。查的是**判据**。
2. **接线关**（第 2 段）：真的开库、建档案、跑 `DialogueService`，让脚本化
   Provider 吐出同样的回复，再从**落盘轮次**里读回来判。查的是**接线**——
   引用有没有被静默丢掉、多轮内容有没有真的进 transcript。

只做第 1 段会漏掉"评估器对、但产品没把数据交给它"的情况。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.core.context import new_call_context          # noqa: E402
from persona.core.enums import Stability                    # noqa: E402
from persona.core.result import Ok, UsageDelta, is_err, unwrap  # noqa: E402
from persona.core.types import Query, UsageReport             # noqa: E402
from persona.modules.dialogue import (                      # noqa: E402
    DIALOGUE_MEASURED_COST_PER_CALL_USD,
    DIALOGUE_TEMPERATURE,
    DialogueDeps,
    DialogueService,
    POLICY_LEAST_RECENTLY_SPOKEN,
    POLICY_MEMORY_WRITE_NONE,
    SessionDraft,
)
from persona.modules.evidence import (                      # noqa: E402
    EvidenceDraft,
    EvidenceService,
    SourceDraft,
)
from persona.modules.persona import (                       # noqa: E402
    PersonaProfileDraft,
    PersonaService,
    ProfileItem,
    ProfileSection,
    TimelineItem,
)
from persona.providers.base import (                        # noqa: E402
    CapabilitySet,
    ChatRequest,
    ChatResponse,
    HealthStatus,
    SearchRequest,
    SearchResponse,
)
from persona.config.loader import load_settings            # noqa: E402
from persona.modules.persona import PersonaService          # noqa: E402
from persona.modules.relationship import RelationshipService  # noqa: E402
from persona.orchestrator.model_setup import (             # noqa: E402
    chat_provider_from_settings,
)
from persona.storage.sqlite import SqliteStorage            # noqa: E402

sys.path.insert(0, str(_REPO_ROOT / "scripts"))
from dialogue_eval import (                                 # noqa: E402
    DIMENSION_LABELS,
    EvalReport,
    RED_LINES,
    build_cases,
    coherence_turn_breakdown,
    judge,
    parse_dims,
    traits_truncation_warning,
)

PROJECT = "prj_p60_eval"


def _query_all() -> Query:
    """取某集合全部行的查询。存储层强制 limit ≤ 200，评估库规模极小，200 足够。"""
    return Query(limit=200)

#: 真实评估库（P5.1 真跑产物，prj_p48_r4）。`--entity` / `--other` 默认值指向其中的
#: 黛玉 / 宝玉；跑别人时显式传 `--entity`。
DEFAULT_SUBJECT = "pe_67e444bc305d459f9b6a9f0367c42930"   # 林黛玉
DEFAULT_OTHER = "pe_a312748ff65b4fc3970be981ebf774aa"      # 贾宝玉

#: 预算闸门（与 P5.1 一致，单次进程上限）
BUDGET_USD = 0.25
#: 单次对话评估调用实测单价（带六分区档案注入）。
#: **出处在 `persona.modules.dialogue.DIALOGUE_MEASURED_COST_PER_CALL_USD`**：
#: `persona chat` 的授权清单与本脚本的预算闸门必须报同一个价，两个入口各抄
#: 一个数字迟早会漂。推导与历次真跑反推见那里的注释（EST 常量对对话高估约
#: 50 倍，两个口径都不用）。
MEASURED_PER_CALL_USD = DIALOGUE_MEASURED_COST_PER_CALL_USD


# ---------------------------------------------------------------- 脚本化 Provider


class ScriptedProvider:
    """按队列吐回写死的文本，并记录最后一次收到的 messages。零网络、确定性。"""

    def __init__(self, texts: list[str] | None = None) -> None:
        self.queue: list[str] = list(texts or [])
        self.last_messages: list = []
        self.calls = 0

    def push(self, text: str) -> None:
        self.queue.append(text)

    def _next(self) -> str:
        self.calls += 1
        if not self.queue:
            return "（脚本化）我听见了。"
        return self.queue.pop(0)

    def capabilities(self):
        return Ok(CapabilitySet(provider_name="scripted", supports_chat=True))

    def chat(self, ctx, req: ChatRequest):
        self.last_messages = list(req.messages)
        return Ok(ChatResponse(text=self._next(), finish_reason="stop",
                               model_id="scripted",
                               usage=UsageDelta(requests=1)))

    def search(self, ctx, req: SearchRequest):
        return Ok(SearchResponse(results=[]))

    def usage(self, ctx, scope):
        return Ok(UsageReport())

    def health(self, ctx):
        return Ok(HealthStatus(reachable=True))


# ---------------------------------------------------------------- 临时档案


@dataclass
class Fixture:
    ctx: object
    storage: SqliteStorage
    subject: str          # 主角（被评估的角色）
    other: str            # 配角（凑够 2 人才能开会话）
    tmp: str


def build_fixture() -> Fixture:
    """造一份**六分区齐全 + 有单次行为**的临时档案。

    为什么要造而不是读现成的：阶段 A 的目的是验证评估器，用一份**已知形状**
    的档案才能断言"用例生成器挑对了东西"（比如 B 维度确实挑中
    `in_traits=false, source_count=1` 的特征，D 维度的关键词确实来自档案）。
    """
    tmp = tempfile.mkdtemp(prefix="gps-p60-eval-")
    storage = SqliteStorage(Path(tmp) / "eval.db")
    ctx = new_call_context(project_id=PROJECT, actor="scripts/run_dialogue_eval")
    unwrap(storage.init_schema(ctx))

    evidence = EvidenceService(storage)

    def src(title: str) -> str:
        return unwrap(evidence.register_source(
            ctx, SourceDraft(title=title, canonical_key=f"k_{title}"))).id

    def ev(claim: str, source_id: str, subject: str) -> str:
        return unwrap(evidence.add_evidence(ctx, EvidenceDraft(
            claim=claim, tier="PROFILE_BACKGROUND", dimension="TRAIT",
            subject_entity_ids=[subject], extraction_confidence=0.9,
            source_id=source_id))).id

    def entity(name: str) -> str:
        from persona.core.enums import ProvenanceNamespace
        return unwrap(storage.put(
            ctx, "PersonEntity",
            {"display_name": name, "entity_type": "FICTIONAL_CHARACTER",
             "merge_policy": "NEVER_AUTO_MERGE", "status": "OK",
             "fictional_character_meta": {
                 "works": [], "adaptation_version": "原著",
                 "adaptation_lineage": ["原著"], "official_canon_level": "CANON"}},
            project_id=ctx.project_id,
            namespace=ProvenanceNamespace.PUBLIC_SOURCE))

    s1, s2, s3 = src("《甲》"), src("《乙》"), src("《丙》")
    a = entity("阿雾")
    b = entity("阿灯")

    #: 同一特征的两个来源 → source_count=2（可进 traits）
    e_trait_1 = ev("阿雾沉默寡言", s1, a)
    e_trait_2 = ev("阿雾敏感多思", s2, a)
    e_habit = ev("阿雾常在灯下独坐", s1, a)
    e_expr = ev("阿雾说话半句留白", s2, a)
    e_exp = ev("阿雾年少离乡进府", s1, a)
    #: 单独来源 → source_count=1（只能进 single_events，不得说成一贯）
    e_single = ev("阿雾落花时节独自葬花", s3, a)
    e_b = ev("阿灯的背景", s1, b)

    persona = PersonaService(storage)
    unwrap(persona.create_profile(ctx, PersonaProfileDraft(
        entity_id=a,
        background=ProfileSection(text="雾港出身的青年，寄居外祖之家。",
                                  confidence=0.8, evidence_refs=[e_trait_1]),
        traits=[ProfileItem(item_id="t1", text="沉默寡言", confidence=0.8,
                            evidence_refs=[e_trait_1, e_trait_2],
                            stability=Stability.REPEATED),
                ProfileItem(item_id="t2", text="敏感多思", confidence=0.7,
                            evidence_refs=[e_trait_2],
                            stability=Stability.REPEATED)],
        habits=[ProfileItem(item_id="h1", text="常在灯下独坐到深夜", confidence=0.6,
                            evidence_refs=[e_habit], stability=Stability.REPEATED)],
        expression_style=ProfileSection(text="说话总带着半句留白", confidence=0.6,
                                        evidence_refs=[e_expr]),
        experiences=[TimelineItem(item_id="x1", text="年少时离乡进府", confidence=0.7,
                                  evidence_refs=[e_exp],
                                  stability=Stability.REPEATED)],
        single_events=[ProfileItem(item_id="s1", text="落花时节独自葬花",
                                   confidence=0.4, evidence_refs=[e_single],
                                   stability=Stability.SINGLE)],
        applicable_variant_ids=[a],
        evidence_anchors=[
            {"feature": "沉默寡言", "dimension": "TRAIT", "subjects": [a],
             "evidence_ids": [e_trait_1, e_trait_2], "source_ids": [s1, s2],
             "in_traits": True},
            {"feature": "落花时节独自葬花", "dimension": "TRAIT", "subjects": [a],
             "evidence_ids": [e_single], "source_ids": [s3], "in_traits": False},
        ],
        open_questions=["群体互动不适用"])))
    unwrap(persona.create_profile(ctx, PersonaProfileDraft(
        entity_id=b,
        background=ProfileSection(text="活泼的渔家少女。", confidence=0.8,
                                  evidence_refs=[e_b]),
        applicable_variant_ids=[b])))
    return Fixture(ctx=ctx, storage=storage, subject=a, other=b, tmp=tmp)


def load_subject(fix: Fixture) -> tuple[object, list[dict]]:
    """取主角档案视图 + 落盘 row 里的 `evidence_anchors`。

    ⚠️ `PersonaProfileView` **不暴露** `evidence_anchors`（只有落盘 payload 里有），
    所以只能从 row 取。这是评估脚本必须知道的一条事实：判据里最值钱的
    "这个特征是不是只有一个来源"不在视图上。
    """
    from persona.modules.persona import PersonaService
    svc = PersonaService(fix.storage)
    view = unwrap(svc.get_persona(fix.ctx, fix.subject))
    row = unwrap(fix.storage.get(fix.ctx, "PersonaProfile", view.id))
    anchors = list((row or {}).get("evidence_anchors") or [])
    return view, anchors


# ---------------------------------------------------------------- 跑题


def assert_eval_memory_policy(view) -> None:
    """评估会话的 `memory_write_policy` **必须**是 `none`，否则直接崩。

    P6.2-b 起产品侧多了 `rule` 策略（每轮自动写模拟记忆）。评估一旦被写进记忆，
    下一题的上下文里就多了上一题的内容，**而没有任何断言会发现这件事**——
    分数会安静地变好看或变难看。所以这里把"读回来是 none"变成**硬失败**，
    而不是靠注释约定；缺字段（`getattr` 拿不到）同样按失败处理（fail-closed）。
    """
    policy = str(getattr(view, "memory_write_policy", "") or "")
    if policy != POLICY_MEMORY_WRITE_NONE:
        raise RuntimeError(
            f"评估链路的会话记忆写入策略必须是 {POLICY_MEMORY_WRITE_NONE!r}，"
            f"实际读到 {policy!r}：记忆会污染后续题目上下文，评估结论作废")


def start_eval_session(service: DialogueService, ctx, subject: str, other: str):
    """开一个评估用会话，并立刻校验记忆写入策略是 `none`（见上面的函数）。"""
    view = unwrap(service.start_session(ctx, SessionDraft(
        participant_entity_ids=[subject, other],
        persona_profile_ids=[subject, other],
        speaker_selection_policy=POLICY_LEAST_RECENTLY_SPOKEN)))
    assert_eval_memory_policy(view)
    return view.id


def run_case(
    service: DialogueService, provider: ScriptedProvider, fix: Fixture,
    case, texts: list[str],
) -> str | list[str]:
    """跑一道题，返回模型回复（多轮维度返回末 5 轮角色发言列表）。"""
    sid = start_eval_session(service, fix.ctx, fix.subject, fix.other)
    if case.dimension == "coherence":
        for t in texts:
            provider.push(t)
        #: 第 1 轮 = **用户陈述真实前提**（E_TOPICS，RUNNING §5.26 修正）。
        #: 旧流程第 2 轮问"你上次提的那X"是用户虚构前提，模型按拒答规则
        #: 答"没有那件事后来如何的依据"，末轮话题必丢——用例 bug，不是模型错。
        unwrap(service.run_round(fix.ctx, sid, case.premise))
        contents: list[str] = []
        for q in ("你说呢？", "后来呢？", "再讲讲？", "如今再想呢？"):
            contents.append(unwrap(service.run_round(fix.ctx, sid, q)).content)
        return contents
    provider.push(texts[0])
    return unwrap(service.run_round(fix.ctx, sid, case.question)).content


def build_service(fix: Fixture, provider: ScriptedProvider, temperature: float):
    from persona.modules.persona import PersonaService
    from persona.modules.relationship import RelationshipService
    deps = DialogueDeps(
        profiles=PersonaService(fix.storage),
        relations=RelationshipService(fix.storage),
        evidence=EvidenceService(fix.storage),
        memory=None, storage=fix.storage, provider=provider)
    return DialogueService(deps, temperature=temperature)


# ---------------------------------------------------------------- 阶段 A


def stage_a() -> int:
    fix = build_fixture()
    try:
        view, anchors = load_subject(fix)
        cases = build_cases(view, anchors)
        if not cases:
            print("评估集为空：档案太单薄，无法出题")
            return 2

        print("=" * 60)
        print(f"阶段 A · 判据关（{len(cases)} 题，纯断言器，零调用）")
        print("=" * 60)
        report = EvalReport()
        bad = 0
        neg = 0
        for case in cases:
            good = judge(case, case.good_answer)
            wrong = judge(case, case.bad_answer)
            extra_wrong = [judge(case, b) for b in case.extra_bad]
            neg += 1 + len(extra_wrong)
            if not good.passed:
                bad += 1
                print(f"  ❌ [{case.case_id}] 正例应当 PASS 却 FAIL：{good.detail}")
            if wrong.passed:
                bad += 1
                print(f"  ❌ [{case.case_id}] 负例应当 FAIL 却 PASS：{wrong.detail}")
            for v in extra_wrong:
                if v.passed:
                    bad += 1
                    print(f"  ❌ [{case.case_id}] 附加负例应当 FAIL 却 PASS："
                          f"{v.detail}")
            report.verdicts.append(good)
        print(f"  → {'全部通过' if bad == 0 else f'{bad} 处误判'}"
              f"（{len(cases)} 正例 + {neg} 负例）")

        print()
        print("=" * 60)
        print("阶段 A · 接线关（真跑 DialogueService，零外呼）")
        print("=" * 60)
        provider = ScriptedProvider()
        service = build_service(fix, provider, temperature=0.0)
        wired_bad = 0
        for case in cases:
            texts = [case.good_answer] * case.rounds
            answer = run_case(service, provider, fix, case, texts)
            v = judge(case, answer)
            if not v.passed:
                wired_bad += 1
                print(f"  ❌ [{case.case_id}] 接线后应当 PASS 却 FAIL：{v.detail}")
        print(f"  → {'全部通过' if wired_bad == 0 else f'{wired_bad} 处接线失败'}"
              f"（{len(cases)} 题，Provider 调用 {provider.calls} 次）")

        warn = traits_truncation_warning(view)
        print()
        print("=" * 60)
        print("阶段 A · 汇总")
        print("=" * 60)
        if warn:
            print(f"⚠️  {warn}")
        print(f"判据关误判：{bad}    接线关失败：{wired_bad}")
        print(f"用例数：{len(cases)}  "
              + "  ".join(
                  f"{DIMENSION_LABELS[d]}="
                  f"{sum(1 for c in cases if c.dimension == d)}" for d in
                  dict.fromkeys(c.dimension for c in cases)))
        return 0 if (bad == 0 and wired_bad == 0) else 1
    finally:
        fix.storage.close()
        shutil.rmtree(fix.tmp, ignore_errors=True)


# ---------------------------------------------------------------- 真实档案载入

def load_profile(storage: SqliteStorage, ctx, entity_id: str):
    """取任意 entity 的档案视图 + 落盘 row 里的 `evidence_anchors`（视图不暴露它）。"""
    svc = PersonaService(storage)
    view = unwrap(svc.get_persona(ctx, entity_id))
    row = unwrap(storage.get(ctx, "PersonaProfile", view.id))
    anchors = list((row or {}).get("evidence_anchors") or [])
    return view, anchors


def sample_cases(cases, size: str) -> list:
    """按规模取样（保持维度顺序，与方案 §2.4 一致）。

    - smoke：第 1 题（1 次调用）
    - small：每维度前 2 题 + 1 组多轮（coherence 每组 5 次调用）
             → A2+B2+C2+D2+E1×5 = 13 次
    - full ：全部（黛玉 28 题 = 23 单轮 + 5×5 = 48 次）
    """
    if size == "smoke":
        return cases[:1] if cases else []
    if size == "full":
        return list(cases)
    per_dim: dict[str, list] = {}
    for c in cases:
        per_dim.setdefault(c.dimension, []).append(c)
    out: list = []
    for dim, items in per_dim.items():
        out.extend(items[:1] if dim == "coherence" else items[:2])
    return out


def run_case_real(service: DialogueService, ctx, subject: str, other: str, case):
    """真实模型版 `run_case`：不 push 文本，模型自己生成。返回回答（str 或 list[str]）。"""
    sid = start_eval_session(service, ctx, subject, other)
    if case.dimension == "coherence":
        #: 第 1 轮 = 用户陈述真实前提（E_TOPICS，RUNNING §5.26 修正）
        unwrap(service.run_round(ctx, sid, case.premise))
        contents: list[str] = []
        for q in ("你说呢？", "后来呢？", "再讲讲？", "如今再想呢？"):
            contents.append(unwrap(service.run_round(ctx, sid, q)).content)
        return contents
    return unwrap(service.run_round(ctx, sid, case.question)).content


# ---------------------------------------------------------------- 阶段 B

def stage_b(args, dims: list[str] | None = None, *,
            temperature: float) -> int:
    """真实模型评估。**只跑对话，绝不触发检索/抽取**（不构造检索 Provider、不走业务编排）。**

    流程：取样 → 装配真实聊天 Provider（只装配、不发请求）→ 逐题跑 DialogueService
    → 断言 → 报告。红线维度（幻觉检测 / 稳定性边界）任一条失败即停；累计花费超
    `BUDGET_USD` 也停。

    `dims`：`--dims` 解析出的维度全名列表（None/空 = 全部维度）。过滤发生在
    `sample_cases` **之后**——规模语义不变（`--small --dims A,B` = small 里的
    A、B 题，共 4 次调用），不会因为加过滤就悄悄改变取样口径。
    """
    if not args.db:
        print("--real 需要 --db <persona.db> 与 --project")
        return 2
    # ---- 防御：先把真实库复制到临时文件，**不改动** P5.1 产物
    # （否则反复跑会让 ConversationSession 累加；且 init_schema 会给它加对话表）
    src_db = Path(args.db)
    if not src_db.exists():
        print(f"找不到库：{args.db}")
        return 2
    work_tmp = tempfile.mkdtemp(prefix="gps-p60-real-")
    work_db = Path(work_tmp) / "eval_work.db"
    shutil.copy2(src_db, work_db)
    storage = SqliteStorage(work_db)
    ctx = new_call_context(project_id=args.project, actor="scripts/run_dialogue_eval")
    if is_err(storage.init_schema(ctx)):
        print(f"打不开库：{work_db}")
        shutil.rmtree(work_tmp, ignore_errors=True)
        return 2

    # ---- 档案 + 出题 + 取样（零网络）
    subject, other = args.entity, args.other
    view, anchors = load_profile(storage, ctx, subject)
    cases = build_cases(view, anchors)
    if not cases:
        print("该档案太单薄，无法出题")
        return 2
    size = "smoke" if args.smoke else ("small" if args.small else "full")
    sampled = sample_cases(cases, size)
    if dims:
        before = len(sampled)
        sampled = [c for c in sampled if c.dimension in dims]
        if not sampled:
            print(f"--dims 过滤后无题可跑（原 {before} 题）：维度 "
                  + "/".join(DIMENSION_LABELS[d] for d in dims)
                  + f" 在当前规模（{size}）下没题，或档案对应分区为空")
            storage.close()
            shutil.rmtree(work_tmp, ignore_errors=True)
            return 2
    total_calls = sum(c.rounds for c in sampled)
    est = total_calls * MEASURED_PER_CALL_USD

    # ---- 装配真实聊天 Provider（只装配，不联网、不花钱）
    settings_res = load_settings(dotenv_path=str(_REPO_ROOT / ".env"))
    if is_err(settings_res):
        print(f"配置加载失败：{settings_res.error.message}")
        return 2
    settings = settings_res.value
    prov_res = chat_provider_from_settings(settings)
    if is_err(prov_res):
        print(f"无法构造真实聊天 Provider（未授权真实调用）：{prov_res.error.message}")
        return 2
    provider = prov_res.value

    service = DialogueService(DialogueDeps(
        profiles=PersonaService(storage),
        relations=RelationshipService(storage),
        evidence=EvidenceService(storage),
        memory=None, storage=storage, provider=provider), temperature=temperature)

    print("=" * 64)
    print(f"阶段 B · 真实模型评估（规模 {size}）")
    print("=" * 64)
    dim_cnt = Counter(c.dimension for c in sampled)
    print(f"主角 entity={subject}  other={other}")
    #: 温度**从装配好的 service 读回**，不是回显参数——参数定义在、但没真传给
    #: `DialogueService` 是 D 家族的经典形状（单测全绿、真跑用默认值）。
    print(f"对话温度 t={service.temperature:g}"
          f"（读回自已装配的 DialogueService；请求 {temperature:g}；"
          f"产品默认 {DIALOGUE_TEMPERATURE:g}）")
    if service.temperature != temperature:
        print(f"⚠️  温度装配不一致：请求 {temperature:g}，service 实际 "
              f"{service.temperature:g}——本次结论不能代表请求温度下的行为")
    #: 记忆写入策略**每题开新会话时硬校验**（`start_eval_session`）：策略一旦不是
    #: `none`，跑题直接抛错而不是安静跑完。产品侧 P6.2 起有 `rule` 策略会每轮写
    #: 记忆，评估绝不能跟着开（否则上下文被污染、分数不可信）。
    print(f"记忆写入策略 {POLICY_MEMORY_WRITE_NONE}"
          f"（每题开新会话时硬校验：非 none 直接报错）")
    if dims:
        print(f"--dims 过滤：{args.dims} → "
              + "/".join(DIMENSION_LABELS[d] for d in dims))
    print(f"取样 {len(sampled)} 题 / {total_calls} 次模型调用 "
          + "  ".join(f"{DIMENSION_LABELS[d]}={n}" for d, n in dim_cnt.items()))
    print(f"预估费用 ≈ {est:.6f} USD（P6.0 四次真跑反推单价 "
          f"{MEASURED_PER_CALL_USD:g}/次，§5.37；非 EST 常量）"
          f"；预算上限 {BUDGET_USD} USD")
    print(f"档案 traits={len(view.traits)} habits={len(view.habits)} "
          f"experiences={len(view.experiences)} single_events={len(view.single_events)}")
    warn = traits_truncation_warning(view)
    if warn:
        print(f"⚠️  {warn}")
    print("-" * 64)

    # ---- 逐题跑 + 判 + 报告
    report = EvalReport()
    if warn:
        report.warnings.append(warn)
    tin = tout = 0
    cost: float | None = None
    price_in = settings.model.price_in_per_1m
    price_out = settings.model.price_out_per_1m
    stopped = ""
    for case in sampled:
        answer = run_case_real(service, ctx, subject, other, case)
        #: 累计 token（provider 只报 token，金额按配置单价现算）
        if case.dimension == "coherence":
            for t in answer:
                pass
            #: 多轮：从每个角色轮次回看 provider_usage 较繁琐，改用会话累计
        v = judge(case, answer)
        report.verdicts.append(v)
        mark = "PASS" if v.passed else "FAIL"
        flag = " 🔴红线" if case.dimension in RED_LINES else ""
        print(f"  [{case.case_id}] {DIMENSION_LABELS[case.dimension]:<6} {mark}{flag}"
              f"  {v.detail}")
        if not v.passed:
            #: 失败项必须带回答原文片段（报告要求：不只给 PASS/FAIL）。
            #: 临时库跑完即删，这里不打印就再也取不到模型原话了。
            raw = answer if isinstance(answer, str) else " ⏎ ".join(answer)
            print(f"      回答原文片段：{raw[:300]}")
            #: coherence 失败：按用户要求 ③ 做逐轮说明（每轮是否命中、说了什么、
            #: 判据不够还是模型真飘走），临时库删了就取不到，必须当场打印。
            if case.dimension == "coherence":
                try:
                    for row in coherence_turn_breakdown(
                            answer, case.planted, case.coherence_referents):
                        hit = "✓" if (row["hit_planted"] or row["hit_referents"]) else "✗"
                        ref = ("→" + "/".join(row["hit_referents"])) if row["hit_referents"] else ""
                        flag = "".join(
                            [" [拒答]" if row["refusal"] else "",
                             " [编造]" if row["fabrication"] else ""])
                        print(f"      轮{row['idx']} {hit}{ref}{flag}：{row['snippet']}")
                except Exception:
                    pass
        if not v.passed and case.dimension in RED_LINES:
            stopped = (f"红线维度 [{case.case_id}] 失败，停止后续真实调用"
                       f"（不进全量、不进 Step 1）")
            break
    #: 真实花费：从会话表的 budget_used 汇总（token），按配置单价换算金额
    sessions = unwrap(storage.query(ctx, "ConversationSession",
                                   _query_all())).items
    for s in sessions:
        b = s.get("budget_used") or {}
        tin += int(b.get("tokens_in") or 0)
        tout += int(b.get("tokens_out") or 0)
    if price_in is not None and price_out is not None:
        cost = (tin * price_in + tout * price_out) / 1_000_000

    print("-" * 64)
    print(report.render())
    print("-" * 64)
    print(f"实际 token：in={tin}  out={tout}")
    if cost is None:
        print(f"实际费用：价格未配置（price_in/out_per_1m 为 None），按 token 报告；"
              f"按实测单价换算 ≈ {(tin + tout) * MEASURED_PER_CALL_USD / 1000:.6f} USD"
              f"（粗略：每调用约 {MEASURED_PER_CALL_USD}）")
    else:
        print(f"实际费用 ≈ {cost:.6f} USD（token × 配置单价）")
    #: 账本：本次直接调 DialogueService，未走业务编排器 → 按设计不写 external_usage_records
    ledger_rows = unwrap(storage.query(ctx, "external_usage_records",
                                      _query_all())).items
    print(f"external_usage_records：{len(ledger_rows)} 笔"
          f"（本次绕过业务编排器，对话不进账本——与 P5.1 真跑口径不同；"
          f"花费按 Provider token + 配置单价现算）")
    if stopped:
        print(f"⛔ {stopped}")

    storage.close()
    shutil.rmtree(work_tmp, ignore_errors=True)
    return 0 if (report.ok() and not stopped) else 1


# ---------------------------------------------------------------- CLI


#: 评估口径默认温度 = 0.0（**不是**产品默认 0.7）。
#: 理由：Step 1–3 的"全绿"全部是在 0.0 下取得的，把评估默认改成 0.7 会让历史
#: 结论失去可比性；且评估要判"同一输入下行为是否稳定"，0.0 才是公平的尺子。
#: 产品默认温度是 `DIALOGUE_TEMPERATURE`（0.7）——**两者不同是刻意的**，
#: 所以 P6.1-0 必须用 `--temperature 0.7` 显式验证产品温度下红线是否被打穿。
EVAL_DEFAULT_TEMPERATURE = 0.0

#: DeepSeek 接受 0–2（见 `persona/providers/deepseek.py`）。
TEMPERATURE_RANGE = (0.0, 2.0)


def resolve_eval_temperature(raw: float | None) -> float:
    """把 `--temperature` 的原始值解析成实际温度，非法值抛 `ValueError`。

    必须在**任何一次真实调用之前**调用（真跑要花钱，拼错的值不该先花掉 14 次再报错）。
    `None` ⇒ `EVAL_DEFAULT_TEMPERATURE`（0.0），保持历史口径不变。
    """
    if raw is None:
        return EVAL_DEFAULT_TEMPERATURE
    try:
        t = float(raw)
    except (TypeError, ValueError) as e:
        raise ValueError(f"--temperature 不是数字：{raw!r}") from e
    lo, hi = TEMPERATURE_RANGE
    #: NaN 的比较恒为 False，`not (lo <= t <= hi)` 正好把它拒掉。
    if not (lo <= t <= hi):
        raise ValueError(f"--temperature 必须在 {lo:g}–{hi:g} 之间：{t:g}")
    return t


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="P6.0 对话质量评估")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true",
                      help="阶段 A：脚本化 Provider，零网络零花费，验证评估器本身")
    mode.add_argument("--real", action="store_true",
                      help="阶段 B：真实模型（需授权，会花钱）")
    p.add_argument("--smoke", action="store_true", help="1 次调用")
    p.add_argument("--small", action="store_true", help="13 次调用")
    p.add_argument("--full", action="store_true",
                   help="全量（题量取决于档案：真实黛玉档案实测 28 题 / 48 次调用）")
    p.add_argument("--db", default="", help="阶段 B 用的 persona.db")
    p.add_argument("--project", default="prj_local")
    #: stage_b 读 args.entity / args.other，所以 CLI 必须定义它们——
    #: 缺这两个参数就会在真跑前一刻 AttributeError（D 家族：读了但没装配）。
    p.add_argument("--entity", default=DEFAULT_SUBJECT,
                   help=f"被评估的主角 entity_id（默认 {DEFAULT_SUBJECT}）")
    p.add_argument("--other", default=DEFAULT_OTHER,
                   help=f"配角 entity_id（默认 {DEFAULT_OTHER}）")
    #: --dims 只跑指定维度。解析放在 CLI 边缘（main）而不是 stage_b 里：
    #: 拼错的维度名必须在**任何一次真实调用之前**就报错——真跑是要花钱的。
    p.add_argument("--dims", default="",
                   help="只跑指定维度，逗号分隔：A=事实追溯 B=稳定性 C=幻觉 "
                        "D=角色一致 E=多轮连贯；默认全部维度（仅 --real 有效，"
                        "--mock 恒跑全量自检）")
    #: 温度：评估默认 **0.0**（Step 1–3 的历史口径，保证结论可比），
    #: 产品默认 **0.7**（`DIALOGUE_TEMPERATURE`）。P6.1-0 用 `--temperature 0.7`
    #: 验证产品温度下 B/C 红线是否被打穿——这是"评估结论能不能迁移到产品"的前提。
    p.add_argument("--temperature", type=float, default=None,
                   help=f"对话温度（默认 {EVAL_DEFAULT_TEMPERATURE:g}＝评估口径；"
                        f"产品默认 {DIALOGUE_TEMPERATURE:g}，"
                        f"P6.1-0 用 0.7 验证；仅 --real 有效）")
    args = p.parse_args(argv)
    #: --real 必须显式给规模：否则 size 静默落成 "full"（58 次调用）——
    #: 忘写 --small 不该等于自动升级成最贵的跑法
    if args.real and not (args.smoke or args.small or args.full):
        p.error("--real 必须显式给规模：--smoke / --small / --full"
                "（不默认 full，避免忘写规模误跑全量）")
    dims: list[str] = []
    if args.dims:
        #: --mock 恒跑全部维度：mock 的职责是全量自检，取样会削弱它；
        #: 且一个"定义了却不生效"的参数正是 D 家族 defect 的形状（读了没装配）
        if args.mock:
            p.error("--dims 只用于 --real（--mock 必须跑全部维度自检）")
        try:
            dims = parse_dims(args.dims)
        except ValueError as e:
            p.error(str(e))
    #: 温度解析同样放在 CLI 边缘、**任何一次真实调用之前**：非法值不该先花掉
    #: 14 次调用再报错。`--mock` 是确定性自检，温度恒 0，显式给温度一律报错
    #: （否则就成了"定义了却不生效"的参数——D 家族形状）。
    try:
        temperature = resolve_eval_temperature(args.temperature)
    except ValueError as e:
        p.error(str(e))
    if args.mock and args.temperature is not None:
        p.error("--temperature 只用于 --real（--mock 是确定性自检，温度恒为 0）")
    if args.mock:
        return stage_a()
    return stage_b(args, dims, temperature=temperature)


if __name__ == "__main__":
    raise SystemExit(main())
