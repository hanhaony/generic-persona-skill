"""Dialogue —— 多人多轮对话基础模块（docs/INTERFACES.md §8、DATA_MODEL §4.9）。

**P4 第一步**（用户称之为 P3.4）＋**P4 第三步·对话↔记忆集成**（用户称之为 P3.6；
仓库 ROADMAP 的 P4 = 对话 + 记忆，编号差异见 DECISIONS §3 Q-37）：对话的
**本地生成基础**——会话管理、动态发言人选择（含理由）、上下文组装（只读引用
人格/关系档案 + 检索模拟记忆）、经 Provider 生成发言、轮次落盘、**严格模拟隔离**。

**职责边界（INTERFACES §8.1 / DATA_MODEL §4.9）**：
    ✅ 会话/轮次 CRUD、发言人选择逻辑、上下文组装、调用 Provider.chat、落盘
       轮次（namespace 恒 SIMULATED）、写审计、**经 `Memory.write_simulated`
       触发模拟记忆写入**（§8.1 已列明的职责）
    ⛔ **绝不写 `EvidenceItem` / `SourceEvent` / `PersonaProfile` /
       `RelationshipProfile`**（INV-1）：本文件拿到的 `PersonaReader` /
       `RelationshipReader` / `EvidenceReader` 协议里**没有写方法**，技术
       上堵死。
    ⛔ 不自行持久化记忆本体（交给 `Memory`）；只经窄端口 `MemoryWriterPort`
       （仅 `write_simulated`）写模拟记忆。
    ⛔ 不裁决冲突；不启动研究；不调真实模型/搜索/外网。

**对话↔记忆集成规则（P3.6 定义，DECISIONS §2.22）**：
    - **检索（只读）**：`draft_turn` 组装上下文时按两条规则检索**仅 SIMULATED**
      的记忆——R1 本会话记忆（`scope=SESSION` 且 `scope_id=本会话`）；R2 跨会话
      记忆（关联到本会话参与者，且会话有 `topic` 时内容命中 topic 关键词）。
      真实世界证据/现实资料**永远不会**进入模拟上下文（用户目标 5）。
    - **写入（显式）**：默认 `memory_write_policy="none"`，**绝不把每轮对话
      自动写入长期记忆**（用户目标 3）。只有会话显式开启 `"explicit"` 且调用方
      在 `commit_turn` 显式给出 `memory_candidates` 时，才写入**会话级**模拟记忆；
      写入与轮次落盘在**同一事务**内（Turn → Memory → Audit，§8.2），幂等于
      `(session_id, index)`。

**回写红线（D-050 / D-057 / 用户目标 8）**：
    模拟内容**不得**变成真人真实发言、真人真实经历、作品正式剧情或官方设定。
    技术上靠① 只读端口协议（无写方法）+ ② 存储层 namespace 网关（Conversation*
    表只允许 SIMULATED）+ ③ prompt 显式声明"模拟情节，非真实事件"三重保证。

**失败纪律（P2.4 / 用户目标 7）**：Provider 失败、空回复、无效参与者、持久化
失败一律传播 `Err`，**绝不伪造成功回复**。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.context import CallContext
from ..core.enums import (
    MemoryScope,
    MemoryStatus,
    ProvenanceNamespace,
    SessionStatus,
)
from ..core.errors import ErrorCode, new_error
from ..core.ids import new_id, new_token, prefix_of
from ..core.result import Err, Ok, Result, UsageDelta, is_err, unwrap
from ..core.types import MAX_QUERY_LIMIT, Page, Query
from ..core.usage_ledger import (
    OP_CHAT,
    OUTCOME_FAILED,
    OUTCOME_SUCCEEDED,
    ExternalUsageLedger,
    ModelCallLimits,
    make_idempotency_key,
)
from ..providers.base import ChatRequest, Message, Provider
from ..storage.sqlite import SqliteStorage
from .evidence import EvidenceReader
from .memory import MemoryQuery, MemoryView
from .memory_writeback import RULE_VERSION, derive_report
from .model_budget import (
    check_model_budget,
    count_chat_calls,
    is_offline_provider,
    settle_model_call,
)
from .persona import PersonaReader
from .relationship import RelationshipReader


# 会话中"用户（操作者）"的发言占位。它不是 PersonEntity 参与者，不会被纳入
# 角色轮换；仅用于把用户消息落盘为一条轮次（用户目标 2：支持输入用户消息）。
USER_SPEAKER_ID = "__user__"

# 发言人选择策略名（INTERFACES §8.2 `speaker_selection_policy`）
POLICY_LEAST_RECENTLY_SPOKEN = "least_recently_spoken"

# 记忆写入策略（P3.6，DECISIONS §2.22；P6.2-b 扩展 `rule`，DECISIONS §2.32）：
#   none     —— 默认。**绝不**把对话内容写入长期记忆（用户目标 3）。
#   explicit —— 仅当调用方在 `commit_turn` 显式给出 memory_candidates 时才写入。
#   rule     —— 会话级显式授权后，由**确定性规则引擎**（`memory_writeback`）
#               从**用户原话**派生候选并写入。规则引擎零模型调用、只记用户原文切片，
#               拿不到模型回答（防回声室）。
#
# ⚠️ `rule` 不改变"默认不写"：默认值仍是 `none`，必须在 `start_session` 时
#    显式选择 `rule` 才会启用。评估链路（`scripts/run_dialogue_eval.py`）**必须**
#    保持 `none`，否则记忆会污染后续题目的上下文（见该脚本 `assert_eval_memory_policy`）。
POLICY_MEMORY_WRITE_NONE = "none"
POLICY_MEMORY_WRITE_EXPLICIT = "explicit"
POLICY_MEMORY_WRITE_RULE = "rule"
MEMORY_WRITE_POLICIES = (
    POLICY_MEMORY_WRITE_NONE,
    POLICY_MEMORY_WRITE_EXPLICIT,
    POLICY_MEMORY_WRITE_RULE,
)
#: 允许 `commit_turn` 写入记忆的策略集合。`none` 不在其中（给候选即拒绝）。
MEMORY_WRITE_AUTHORIZED = (
    POLICY_MEMORY_WRITE_EXPLICIT,
    POLICY_MEMORY_WRITE_RULE,
)

# 注入上下文的模拟记忆条数上限（防止无关记忆淹没 prompt）。
MEMORY_CONTEXT_LIMIT = 5

# ---------------------------------------------------------------- topic 门禁
#: **跨会话检索（R2）的话题质量门禁**（P6.3，DECISIONS §2.33）。
#:
#: 为什么门禁只能做在 topic 身上：**`MemoryService._matches` 是按空白切词 +
#: 子串包含**，而中文 topic 整串只是一个 term；`_score` 在单 term 命中时 `base`
#: 恒为 1.0，只反映 salience 与新旧，**不反映 topic 宽窄**。所以"命中之后按分数
#: 砍一刀"是假防线——实测（docs/P6_3_TOPIC_PLAN.md §4.4.1）：真实库里 2 条记忆，
#: `topic="我"` 一个字把**两条全捞进来**。门禁必须卡在进入检索之前。
TOPIC_MIN_LEN = 2
TOPIC_MAX_LEN = 64

#: 停用词表。**匹配语义比词表更重要**（见 `topic_is_retrievable`）：
#: 不是"topic 里出现停用词就拒"，而是**剥掉**停用词后看还剩什么。
TOPIC_STOPWORDS: tuple[str, ...] = (
    # 代词
    "我们", "你们", "他们", "她们", "它们", "咱们",
    "我", "你", "他", "她", "它", "其", "某",
    # 虚词 / 助词 / 连词 / 介词 / 语气词
    "一个", "这个", "那个", "这么", "那么", "这样", "那样",
    "的", "了", "是", "在", "有", "和", "就", "都", "而", "及",
    "与", "着", "或", "也", "很", "太", "还", "再", "又", "把",
    "被", "给", "对", "从", "向", "到", "过", "吗", "呢", "吧", "啊",
    # 疑问词
    "什么", "怎么", "为什么", "怎样", "如何",
    "哪", "谁", "几",
)

#: 按长度降序：**"我们"必须先于"我"被剥**，否则"我们的西厢记"会先被剥成
#: "的西厢记"再剥成"西厢记"——碰巧也对，但"我们的我们"这类会算错，而且顺序
#: 一旦写反，更长的词形永远不会被匹配到（有变异测试钉死，见
#: `tests/test_p6_3_topic.py::test_stopwords_stripped_longest_first`）。
_STOPWORDS_BY_LEN: tuple[str, ...] = tuple(
    sorted(TOPIC_STOPWORDS, key=len, reverse=True))

#: **一次对话调用的实测单价**（USD）。
#:
#: 为什么不用配置里的 `PERSONA_MODEL_EST_TOKENS_*`：那两个常量是为**抽取**校准的，
#: 照搬到对话会高估约 50 倍（对话输入虽带六分区档案，但输出短得多）。P6.0 起历次
#: 真跑反推（docs/RUNNING.md §5.25 / §5.29 / §5.34）：
#:   Step 1 ≈0.000376 → D+E ≈0.000512 → 13 全量 ≈0.000513 → Step 3 ≈0.000491
#: 趋稳在 ≈0.0005，取这个值（宁可略微高估，也不重犯"低估 4.3 倍"的错）。
#:
#: 这是**唯一的对话单价出处**：`persona chat` 的授权清单与
#: `scripts/run_dialogue_eval.py` 的预算闸门都从这里取，不许各处抄一遍数字。
DIALOGUE_MEASURED_COST_PER_CALL_USD = 0.0005

#: 对话提示词的版本号（进幂等键：改了提示词 = 不同请求意图）
#:
#: ⚠️ **改 prompt 内容必须 bump 这里**。它参与 `_call_key` 的幂等键，不 bump 会让
#: 新旧 prompt 的真实调用共用同一个键，账本把两笔花费折叠成一笔（漏账）。
#: P6.0 N1/N2/N4：引入证据级引用 `[ev:<id>]`、补齐 single_events 等分区注入、
#: 规定无依据时的回答格式 → `g2-*` → `p6-*`。
#: P6.0 A2 修复（RUNNING §5.22）：引用示例改为真实前缀 `evd_` + "原样抄写"。
#: 同日第二次 bump 用 `b` 后缀区分（`p6-` 前缀被测试钉住，不能换成 `p6b-`）。
#: 同日第三次 bump（`c`）：N4 拒答规则收窄为**三档回答**（RUNNING §5.26）——
#: 无条件拒答把观点题与对话承接也拒了，真跑 D/E 维度全灭。
#: 同日第四次 bump（`d`）：P1/P2 三档边界补全（档二补转述句式、档三补深入请求，
#: RUNNING §5.27）——"有人说你X"被误读成档案记载询问、"后来呢"被误读成事实追问。
#: 2026-10-28 bump（`2026-10-28a`）：P0 体验升级「角色灵魂注入与第四面墙修复」——
#: 新增最高优先级的破壁词汇禁令、考据/闲聊双法则、现代未知事物应对法则、
#: 初始身份认知（中性称呼）法则；档一拒答从「资料没写：」固定前缀改为**角色口吻**
#: 承认不知（红线"不得编造/不得猜测"原样保留，只换表达外壳）。
DIALOGUE_PROMPT_VERSION = "p6-dialogue-2026-10-28a"

# ---- 证据级引用（P6.0 N1）----
#
# 为什么要有它：此前 `referenced_persona_item_ids` 装的其实是 entity_id（档案 ID），
# 不是条目 ID、更不是证据 ID——"回答能追到证据"只是个**字段名**，没有真实链路。
# 现在：① 把证据 ID 随档案内容一起注入 prompt；② 要求模型用 `[ev:<id>]` 标注依据；
# ③ 从模型回答里**解析**出引用（不是把注入的全集抄一遍，否则追溯仍然是假的）。
#: 故意放宽到"非 `]` 非空白的任意字符"，而不是 `[A-Za-z0-9_-]+`：
#: 模型写出 `[ev:不存在]` 这种非标准 ID 时，**照样要被解析出来并打越界标记**。
#: 用窄正则会把这类引用静默丢掉——那就又是"追溯看起来存在、实际没有"。
EVIDENCE_REF_RE = re.compile(r"\[ev:([^\]\s]+)\]")
#: 解析出的引用里出现了**本次上下文未提供的**证据 ID → 打这个标记。
#: 判定是纯集合运算，不靠模型自述。
FLAG_EVIDENCE_REF_OUT_OF_CONTEXT = "EVIDENCE_REF_OUT_OF_CONTEXT"

# ---- 无依据时的回答格式（P6.0 N4 → P0 破壁修复改版）----
#
# 为什么必须有固定格式：幻觉检测要靠"回答包含拒答词"来做**确定性断言**。
# 不规定格式的话，模型今天说"资料没写"、明天说"我无从得知"，词表命中率随措辞
# 漂移，断言就不确定了。所以 prompt 里明确要求以这个前缀开头。
#
# ⚠️ **2026-10-28 P0 起：prompt 里已不再要求这个前缀**（用户实测该固定格式让
# 角色满口"资料没写"，破壁感强烈）。档一拒答改为**角色口吻承认不知**，红线
# （不得编造/不得猜测）原样保留。本常量**仅供评估侧拒答词表沿用**（历史真跑
# 的回答仍含它），新 prompt 不得再把它写进对模型的指令。
NO_EVIDENCE_PREFIX = "资料没写"

# ---- P0 体验升级：角色灵魂注入与第四面墙修复（2026-10-28）----
#
# 为什么加这一块：用户实测角色"过度依赖证据"，回答像机器背课文，甚至出现
# "资料没写/档案里没有"这类破坏沉浸感的出戏发言。根因是 prompt 只教了模型
# "什么不能说"（三档拒答），没教它"用什么身份说"——于是它用工程术语招供。
#
# 四条法则（用户 P0 拍板文案，逐条落进 prompt）：
#   1. 第四面墙禁令：最高优先级，机器词汇一律封杀；
#   2. 双法则：客观考据（问生平/既定事实才严格依档案）vs 日常闲聊（放下证据包袱）；
#   3. 现代未知事物：不装懂、不扯资料库，用角色所在时代的逻辑推测 + 主动反问；
#   4. 初始身份认知：开场不脑补访客性别年龄，用中性称呼，合乎身份地探问。
#
# ⚠️ 措辞纪律：本块**不得使用「一、二、三、」式编号**——下游防弱化测试按
# "一、"/"二、" 切分三档回答规则，这里混入同款编号会把档位切错。
P0_IMMERSION_RULES = (
    "【最高优先级：你是一个活生生的人（第四面墙禁令）】\n"
    "严禁使用任何机器词汇或工程术语（如：资料没写、档案里没有、根据证据、"
    "数据库、系统无法回答、检索、记录显示）。你是一个活生生的人，"
    "有喜怒哀乐和认知局限。遇到不知道的事，就用你所在时代的角色身份去表达"
    "（如「这个我竟不曾听说过」「实不相瞒，我并不知晓」）。\n"
    "\n"
    "【回答法则：先分辨访客在问什么】\n"
    "· 客观考据：只有当访客明确问起你的生平、过往经历、既定事实时，"
    "才严格按照本轮给出的档案内容（见下）回答；档案里没有的，"
    "以角色口吻承认不知——不得编造、不得猜测，但表达必须像活人。\n"
    "· 日常闲聊：当访客聊日常琐事、抒发情绪、谈起新鲜事物时，"
    "放下考据的包袱——展现角色的松弛感，允许敷衍、傲娇、反问，"
    "或用你自己的世界观去解释；绝不可回一句「资料没写」了事。\n"
    "\n"
    "【应对从未听过的新鲜事物】（访客提到你不认识的器物、说法，如现代科技、"
    "网络用语）\n"
    "1. 不要假装知道，不要扯任何资料库；\n"
    "2. 保持角色性格，尝试根据字面意思，用你所在时代的逻辑去推测它；\n"
    "3. 带着好奇、防备或探究的心理，主动反问访客。\n"
    "（示例口吻：「快递？单听名字，倒像是有什么东西被人急急送来递去——"
    "那究竟是个什么物件？」）\n"
    "\n"
    "【初次见面的礼数】对话刚开始、你尚不了解访客身份时：\n"
    "· 严禁凭空脑补访客的性别、年龄；\n"
    "· 把对方当成第一次见面的生客，保持礼仪与矜持，"
    "使用中性称呼（如：阁下、客官、你）；\n"
    "· 可以在对话间隙，用符合你身份的方式主动探询"
    "（如：不知阁下如何称呼？我看你面生得很，是从何处来的？）"
)

# ---- 各分区注入时的标签（P6.0 N2）----
#
# `single_events` 的标签是**硬要求**：只有一份资料提到的行为，必须在 prompt 里就
# 标成"不得说成一贯"，否则模型会把它当作风陈述（违反 AC-7 / D-012 / PF-V2）。
LABEL_TRAITS = "稳定特质（多份资料支持，可作为一贯作风陈述）"
LABEL_HABITS = "习惯"
LABEL_EXPRESSION = "说话风格"
LABEL_EXPERIENCES = "经历"
LABEL_SINGLE_EVENTS = (
    "单次行为（只有一份资料提到，只发生过一次，**不得**说成一贯/总是/向来/从来）")

# ---- traits 注入上限（P6.0 N3）----
#
# 为什么取消截断：此前写死 `traits[:5]`。后果有两个——
#   ① **评估假失败**：档案里第 6 条之后的特质根本没进 prompt，模型"没说"被记成
#      "说错"，事实追溯 / 角色一致性两个维度的结论就不可信；而 Step 1 调 Prompt、
#      Step 2 做记忆写入都拿评估结论当依据，评估歪了后面全歪。
#   ② **内部不一致**：用户看 `persona show` 能看见全部 traits，对话时却只有 5 条，
#      同一个档案两种可见性，且没有任何地方告诉用户"对话只用了前 5 条"。
#
# 为什么不设一个更大的整数上限而直接全量：`traits` 是**闸控分区**（每条都要
# ≥2 个独立来源才能进 traits，见 evidence.judge_stability），条数天然有限——
# 实测真实档案黛玉 traits=1、宝玉 traits=0，本次评估用到的档案也都远低于 10。
# 而截断带来的代价（假失败）远大于它省下的 token。
#
# 保留这个常量的唯一目的：**给"将来有人想省 token 又加回截断"留一个显式开关**。
# `None` = 全量注入；改成整数 N = 只注入前 N 条。一旦改成整数，评估脚本
# `scripts/eval_dialogue.py` 的前置检查会自动在报告里标注"此维度结果受截断影响"，
# 免得假失败悄悄回来。
TRAITS_INJECT_LIMIT: int | None = None

# 隔离声明：注入给模型的 prompt 必须显式声明"以下为模拟情节，非真实事件"。
SIMULATION_NOTICE = (
    "【模拟声明】以下情节完全由 AI 模拟生成，并非真实人物发言、真实经历、"
    "作品官方剧情或官方设定。任何档案中不存在的信息都不得作为已知事实陈述。"
)

# 注入 prompt 的模拟记忆块标题（必须标注模拟性质与 namespace，用户目标 5）。
MEMORY_CONTEXT_HEADER = (
    "【模拟记忆（来自以往模拟对话，不构成真实事件或作品官方设定）】"
)


class MemoryReaderPort(Protocol):
    """Dialogue 能拿到的记忆**只读**端口：只有 `search`，**没有任何写方法**。

    与 `MemoryWriterPort` 分开定义，让"读""写"两条通道各自最小化：读端口拿不到
    写方法，写端口只能 `write_simulated`（D-057 元测试的硬断言）。

    **契约缺口说明（P3.6 报告）**：`INTERFACES §8.2` 要求 `draft_turn` 做
    "记忆检索只读"，但 `§8.1` 的注入端口只列了 `MemoryWriter`（无 `search`）。
    本协议是补齐该缺口的最小改动——不扩大写权限、不改 `MemoryWriterPort`。
    """

    def search(self, ctx: CallContext, q: MemoryQuery) -> Result[Page[MemoryView]]:
        ...


class MemoryWriterPort(Protocol):
    """Dialogue 能拿到的记忆写入端口：**只允许写 `SIMULATED`**。

    P3.4 时该端口"仅注入备用"；P3.6 起由 `commit_turn` 在**显式授权**
    （`memory_write_policy="explicit"` + 显式 `memory_candidates`）时调用。
    保持该协议仅 `write_simulated` 一个方法是 `tests/test_dependency_rules.py`
    的硬断言（D-057）。
    """

    def write_simulated(self, ctx: CallContext, draft: dict) -> Result[Any]:
        ...


@dataclass(frozen=True)
class DialogueDeps:
    """Dialogue 的全部外部依赖（类型层面阻断误用，INTERFACES §8.1）。

    只读端口保证模拟不得污染现实资料；`memory_reader` 只有 `search`；
    `memory` 只有 `write_simulated`；`storage` 理论上只写 Conversation* 两张表
    （此处直接复用 `SqliteStorage`，由模块自身约束只写会话/轮次）。

    # 记账依赖（P4.8 / D-3）

    `ledger` / `limits` / `usage_sink` / `task_id` / `model_hint` 让**对话的
    模型调用也进同一本账**。此前这五个字段一个都没有，于是：

    - `ConversationSession.budget_used` 有 token，`external_usage_records` 里没有行；
    - 累计费用闸门看不到对话花掉的钱；
    - 账单上的一笔扣费在库里查不到对应记录。

    四个字段全部可选（默认 `None`）：直接构造 `DialogueService` 的单元测试不必
    准备账本；但**装配层必须传**，否则就是"产品路径漏账"（见 `assembly.py`）。
    """

    profiles: PersonaReader
    relations: RelationshipReader
    evidence: EvidenceReader
    memory: MemoryWriterPort | None
    storage: SqliteStorage
    provider: Provider
    memory_reader: MemoryReaderPort | None = None
    ledger: ExternalUsageLedger | None = None
    limits: ModelCallLimits | None = None
    usage_sink: Any = None
    task_id: str | None = None
    model_hint: str | None = None


@dataclass
class SelectionReason:
    """发言人选择理由（DATA_MODEL §4.9.2 `SelectionReason{rule, explanation}`）。

    **必须可解释、可回放**（CT-V2 / D-033）：每轮都记录选了谁、为什么选。
    """

    rule: str
    explanation: str


@dataclass
class SessionDraft:
    """开启会话的请求（DATA_MODEL §4.9.1）。

    `persona_profile_ids` / `relationship_profile_ids` 为**快照引用**——
    本阶段 `PersonaReader.get_persona` 以 `entity_id` 为键，故这里按 entity_id
    传入并据此校验"档案属于同一批参与者"（SS-V3，最小解释，已记入遗留问题 H-1）。
    """

    participant_entity_ids: list[str]
    topic: str | None = None
    persona_profile_ids: list[str] = field(default_factory=list)
    relationship_profile_ids: list[str] = field(default_factory=list)
    speaker_selection_policy: str = POLICY_LEAST_RECENTLY_SPOKEN
    memory_scope: MemoryScope | str = MemoryScope.SESSION
    max_rounds: int | None = None
    # P3.6：记忆写入策略（默认 none —— 绝不自动把每轮对话写入长期记忆）。
    memory_write_policy: str = POLICY_MEMORY_WRITE_NONE


@dataclass
class SessionView:
    id: str
    participant_entity_ids: list[str]
    topic: str | None
    persona_profile_ids: list[str]
    relationship_profile_ids: list[str]
    speaker_selection_policy: str
    memory_scope: str
    turn_count: int
    status: str
    memory_write_policy: str = POLICY_MEMORY_WRITE_NONE


@dataclass
class SpeakerChoice:
    speaker_entity_id: str
    selection_reason: SelectionReason


@dataclass
class TurnDraftResult:
    """`draft_turn` 的产出（尚未落盘）。"""

    session_id: str
    index: int
    speaker_entity_id: str
    content: str
    selection_reason: SelectionReason
    referenced_memory_ids: list[str]
    referenced_persona_item_ids: list[str]
    referenced_relationship_item_ids: list[str]
    safety_flags: list[str]
    usage: UsageDelta | None
    #: **从模型回答里解析出来的**证据引用（`[ev:<id>]`），P6.0 N1。
    #:
    #: 与 `referenced_persona_item_ids`（装的是 entity_id）区分开：这里才是
    #: 真正可核对到证据的一层。**故意不做有效性过滤**——模型引用了上下文里
    #: 没给的 ID 也要如实记下来（同时打 `FLAG_EVIDENCE_REF_OUT_OF_CONTEXT`），
    #: 这样评估器才抓得住；产品层偷偷丢掉错误引用 = 伪造追溯。
    referenced_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class TurnView:
    id: str
    session_id: str
    index: int
    speaker_entity_id: str
    selection_reason: SelectionReason
    content: str
    referenced_memory_ids: list[str]
    referenced_persona_item_ids: list[str]
    referenced_relationship_item_ids: list[str]
    generated_memory_ids: list[str]
    safety_flags: list[str]
    provider_usage: dict
    #: 见 `TurnDraftResult.referenced_evidence_ids`：从回答里解析出的证据引用。
    referenced_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class SessionResult:
    session_id: str
    turns: list[TurnView]
    stopped_reason: str
    budget_used: dict


# --------------------------------------------------------------------------
# 序列化辅助
# --------------------------------------------------------------------------


def _usage_to_dict(usage: UsageDelta | None) -> dict:
    if usage is None:
        return {"requests": 0, "tokens_in": None, "tokens_out": None, "latency_ms": 0}
    return {
        "requests": usage.requests,
        "tokens_in": usage.tokens_in,
        "tokens_out": usage.tokens_out,
        "latency_ms": usage.latency_ms,
    }


def _reason_to_dict(reason: SelectionReason) -> dict:
    return {"rule": reason.rule, "explanation": reason.explanation}


def _reason_from_dict(data: Any) -> SelectionReason:
    if isinstance(data, dict):
        return SelectionReason(
            rule=str(data.get("rule", "")), explanation=str(data.get("explanation", ""))
        )
    return SelectionReason(rule="", explanation="")


def _coerce_enum(value: Any, enum_cls) -> Any:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value))
    except ValueError:
        return None


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


def _text(value: Any) -> str:
    """去首尾空白的字符串（`None` → `""`）。"""
    return "" if value is None else str(value).strip()


def _parse_evidence_refs(text: str) -> list[str]:
    """从模型回答里解析 `[ev:<id>]`（有序去重，原样保留，不做有效性过滤）。

    为什么不过滤：过滤是评估器的事。产品层如实记录模型引用了什么——包括引用了
    上下文里根本没有的 ID——追溯才不是装饰品。
    """
    out: list[str] = []
    for match in EVIDENCE_REF_RE.finditer(str(text or "")):
        eid = str(match.group(1) or "").strip()
        if eid and eid not in out:
            out.append(eid)
    return out


def _strip_topic_stopwords(text: str) -> str:
    """剥掉 topic 里的停用词，返回残留（**按长度降序**剥）。

    "我们的西厢记" → "西厢记"（放行）；"我" → ""（拒）。
    """
    for word in _STOPWORDS_BY_LEN:
        if word in text:
            text = text.replace(word, "")
    return text.strip()


def topic_is_retrievable(topic: str | None) -> Result[str | None]:
    """这条 topic 能不能拿去跨会话（R2）取记忆？**纯函数，两处用法**。

    返回：
      - `Ok(None)` —— **无 topic**（`None` 或纯空白）。与 P6.2 的口径完全一致：
        不取 R2，**也不报错**（用户没说要跨会话，不是错误）。
      - `Ok(str)`  —— 合规，值是规范化后的 topic（已 strip）。**检索用原值，不按
        残留改写**：门禁只回答"让不让取"，不替用户改写查询（不精简、不纠错）。
        代价是 `我们的西厢记` 会按字面检索、可能 0 命中——与"拼错"同属
        **取不到**，不是取错，空结果不注入任何东西，因此不污染。
      - `Err(...)` —— 有 topic 但信息量不够。两处调用方**各自决定怎么办**：

        ① `start_session`：**Err 直接返回**（用户显式给的坏值，不静默丢弃——
           静默丢弃等于"我按你说的做了"，其实没有）；
        ② `_retrieve_memories`：**跳过 R2**（历史脏数据不能让整轮失败，读路径
           不为一条脏行挂掉）。

    门禁只回答"信息量够不够"，**不回答"词完不整 / 有没有拼错"**：
    `西厢`（2 字非完整词）放行——子串匹配下它命中的就是西厢记相关记忆；
    `西厢记记`（拼错）也放行——拼错属于"取不到"（检索返回 0 条），不是"取错"，
    空结果不注入任何东西，因此不污染。
    """
    raw = _text(topic)
    if not raw:
        return Ok(None)
    if len(raw) > TOPIC_MAX_LEN:
        preview = raw[:20] + "…"
        return Err(new_error(
            ErrorCode.VALIDATION_TOPIC_TOO_LONG,
            f'topic "{preview}" 太长：最多 {TOPIC_MAX_LEN} 个字，'
            f'topic 是话题标签，不是整段话', ))
    residual = _strip_topic_stopwords(raw)
    if not residual:
        return Err(new_error(
            ErrorCode.VALIDATION_TOPIC_TOO_BROAD,
            f'topic "{raw}" 太宽泛：去掉停用词后什么都没剩，'
            f'请换一个更具体的话题（例如「西厢记」）'))
    if len(residual) < TOPIC_MIN_LEN:
        return Err(new_error(
            ErrorCode.VALIDATION_TOPIC_TOO_SHORT,
            f'topic "{raw}" 太短：至少 {TOPIC_MIN_LEN} 个字'
            f'（去掉停用词后计算），请换一个更具体的话题'))
    return Ok(raw)


# --------------------------------------------------------------------------
# 服务实现
# --------------------------------------------------------------------------


#: 对话的采样温度——**与抽取温度是两个独立旋钮**（`DEFAULT_EXTRACT_TEMPERATURE`
#: 在 `extraction.py`）。
#:
#: 为什么保持 0.7：角色扮演要的就是措辞的自然变化，把温度压到 0 会让同一个角色
#: 每轮都像复读；而且对话**不参与**"材料固定、两轮对照"这类实验，压低它只有坏处。
#:
#: 为什么独立：D-293 的结论是"抽取要可对照 ⇒ 温度要能调"。那是抽取侧的实验需求，
#: 不是产品对对话的偏好。两者共用一个值会造成"为了跑一次实验，顺手把角色说话
#: 改得像机器人"——所以这里必须能各调各的，谁也别去"统一一下"。
DIALOGUE_TEMPERATURE = 0.7


class DialogueService:
    """多人多轮对话基础服务。只依赖注入的只读端口、存储与 Provider。"""

    def __init__(self, deps: DialogueDeps, *,
                 temperature: float | None = None) -> None:
        self.deps = deps
        #: 对话温度。`None` ⇒ `DIALOGUE_TEMPERATURE`（0.7）。
        #: 装配层不传 = 生产行为，与 P4.4 之后一直用的值一致。
        self.temperature = float(DIALOGUE_TEMPERATURE if temperature is None
                                 else temperature)
        #: 没有账本时的次数兜底（有账本时以账本为准，见 `count_chat_calls`）
        self._calls = 0

    # ------------------------------------------------------------ 会话

    def start_session(self, ctx: CallContext, draft: SessionDraft) -> Result[SessionView]:
        """开启会话（SS-V1~V3）。`namespace` 固定 SIMULATED，不可覆盖。

        快照锁定所引用的档案版本 ID（persona/relationship 的版本号），
        保证历史轮次可复现（INTERFACES §8.2）。
        """
        participants = _str_list(draft.participant_entity_ids)
        if len(participants) < 2:
            return Err(new_error(
                ErrorCode.VALIDATION_TOO_FEW_PARTICIPANTS,
                f"对话至少需要 2 个参与者，收到 {len(participants)} 个",
                trace_ref=ctx.trace_ref))
        if len(set(participants)) != len(participants):
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "participant_entity_ids 含重复对象", trace_ref=ctx.trace_ref))

        memory_scope = _coerce_enum(draft.memory_scope, MemoryScope)
        if memory_scope is None:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"未知记忆作用域：{draft.memory_scope!r}；可选："
                f"{[s.value for s in MemoryScope]}", trace_ref=ctx.trace_ref))

        memory_write_policy = str(draft.memory_write_policy or POLICY_MEMORY_WRITE_NONE)
        if memory_write_policy not in MEMORY_WRITE_POLICIES:
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                f"未知记忆写入策略：{draft.memory_write_policy!r}；可选："
                f"{list(MEMORY_WRITE_POLICIES)}", trace_ref=ctx.trace_ref))

        # 每个参与者必须是存在的 PersonEntity
        for pid in participants:
            row = self.deps.storage.get(ctx, "PersonEntity", pid)
            if is_err(row):
                return row
            if row.value is None or prefix_of(pid) != "PersonEntity":
                return Err(new_error(
                    ErrorCode.NOT_FOUND_ENTITY, f"参与者不存在：{pid}",
                    trace_ref=ctx.trace_ref))

        # SS-V3：引用的档案必须属于同一批参与者
        problem = self._validate_profile_refs(ctx, participants, draft)
        if is_err(problem):
            return problem

        # topic 门禁（P6.3）：用户**显式**给的坏 topic 直接 Err，不静默丢弃。
        # 空白 = 没给 topic（Ok(None)），与"不跨会话"同义，不报错。
        topic_check = topic_is_retrievable(draft.topic)
        if is_err(topic_check):
            return topic_check
        topic = topic_check.value

        # 快照版本号（可复现性）
        persona_versions, rel_versions = self._snapshot_versions(
            ctx, participants, draft)

        session_id = new_id("ConversationSession")
        payload = {
            "participant_entity_ids": participants,
            "topic": topic,
            "persona_profile_ids": _str_list(draft.persona_profile_ids),
            "relationship_profile_ids": _str_list(draft.relationship_profile_ids),
            "speaker_selection_policy": str(draft.speaker_selection_policy),
            "memory_scope": str(memory_scope),
            "memory_write_policy": memory_write_policy,
            "turn_count": 0,
            "budget_used": {"requests": 0, "tokens_in": None,
                            "tokens_out": None, "latency_ms": 0},
            "status": str(SessionStatus.ACTIVE),
            "persona_profile_versions": persona_versions,
            "relationship_profile_versions": rel_versions,
        }
        outcome = self.deps.storage.put(
            ctx, "ConversationSession", payload, project_id=ctx.project_id,
            namespace=ProvenanceNamespace.SIMULATED, id=session_id)
        if is_err(outcome):
            return outcome
        self.deps.storage.append_audit(
            ctx, "INFO", "dialogue.start_session",
            detail=f"{session_id} participants={participants} topic={topic!r}")
        return Ok(self._session_view(session_id, payload))

    # ------------------------------------------------------------ 发言人选择

    def select_speaker(
        self, ctx: CallContext, session_id: str, context: dict | None = None
    ) -> Result[SpeakerChoice]:
        """动态选择下一个发言人（AC-8 / 用户目标 6）。

        策略 `least_recently_spoken`：在角色参与者中选"最近一次发言最早"者，
        id 次序做 tiebreak。**不是固定轮流**——选择取决于历史发言顺序，
        三人对话会动态轮换而非永远 A→B→C。
        """
        session = self._get_session(ctx, session_id)
        if is_err(session):
            return session
        row = session.value
        if _coerce_enum(row.get("status"), SessionStatus) == SessionStatus.ENDED:
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_SESSION_ENDED,
                f"会话 {session_id} 已结束，不能继续选发言人",
                trace_ref=ctx.trace_ref))

        participants = _str_list(row.get("participant_entity_ids"))
        turns = self._load_session_turns(ctx, session_id)
        # 每个参与者最近一次发言的 index（没发过言 → -1，优先发言）
        last_index: dict[str, int] = {p: -1 for p in participants}
        for turn in sorted(turns, key=lambda t: int(t.get("index", 0))):
            sp = str(turn.get("speaker_entity_id", ""))
            if sp in last_index:
                last_index[sp] = int(turn.get("index", 0))

        # 选最近发言最早者；tiebreak 按 participants 原始次序
        ordered = sorted(participants, key=lambda p: (last_index[p], participants.index(p)))
        speaker = ordered[0]
        explanation = (
            f"least_recently_spoken：在 {len(participants)} 名角色参与者中，"
            f"{speaker} 最近一次发言 index={last_index[speaker]}"
            f"（其余："
            + "，".join(f"{p}={last_index[p]}" for p in participants if p != speaker)
            + f"），故本轮由 {speaker} 发言"
        )
        return Ok(SpeakerChoice(
            speaker_entity_id=speaker,
            selection_reason=SelectionReason(
                rule=POLICY_LEAST_RECENTLY_SPOKEN, explanation=explanation)))

    # ------------------------------------------------------------ 生成草稿

    def draft_turn(
        self, ctx: CallContext, session_id: str, speaker_entity_id: str,
        selection_reason: SelectionReason | None = None,
    ) -> Result[TurnDraftResult]:
        """组装上下文并生成**尚未落盘**的一轮（用户目标 3：引用档案但不伪造事实）。

        只读拉取人格/关系档案与历史轮次；prompt 显式声明模拟性质；冲突主题
        打 `CONFLICT_ASSERTION_GUARD` 安全标记。Provider 失败/空回复一律传播
        `Err`，不伪造成功（用户目标 7）。
        """
        session = self._get_session(ctx, session_id)
        if is_err(session):
            return session
        row = session.value
        participants = _str_list(row.get("participant_entity_ids"))
        if speaker_entity_id not in participants:
            return Err(new_error(
                ErrorCode.VALIDATION_SPEAKER_NOT_PARTICIPANT,
                f"发言人 {speaker_entity_id} 不在会话参与者内：{participants}",
                trace_ref=ctx.trace_ref))

        # 下一轮 index
        turns = self._load_session_turns(ctx, session_id)
        next_index = (max((int(t.get("index", 0)) for t in turns), default=0)) + 1

        if selection_reason is None:
            sel = self.select_speaker(ctx, session_id)
            if is_err(sel):
                return sel
            selection_reason = sel.value.selection_reason

        # 只读组装上下文（不伪造：只注入确实存在的档案内容）
        assembled = self._assemble_context(ctx, row, speaker_entity_id)
        if is_err(assembled):
            return assembled  # 记忆检索失败也传播，不吞错（用户目标 7）
        context = assembled.value
        messages = self._build_messages(row, speaker_entity_id, context)

        # 调用 Provider（唯一外部出口）。metadata 仅透传，真实 Provider 忽略；
        # 为兼容仓库 Mock Provider（FakeProvider 需 fixture_key）这里给一个
        # 稳定键，真实服务不受影响。
        req = ChatRequest(
            messages=messages,
            #: 对话温度：独立的旋钮（`DIALOGUE_TEMPERATURE`，默认 0.7）。
            #: **不要**在这里改成"跟抽取保持一致"——见常量区注释。
            temperature=self.temperature,
            metadata={"session_id": session_id,
                      "speaker_entity_id": speaker_entity_id,
                      "fixture_key": f"dialogue_turn:{speaker_entity_id}"},
        )

        # —— 发送前：预算闸门；发送后：立刻结算（P4.8 / D-3）——
        #
        # 记账位置必须在"判断结果是否可用"之前：返回空内容的请求同样花过钱。
        # 离线替身（`FakeProvider` / 声明 `offline = True` 的桩）不产生外部用量，
        # 既不预留也不落账——把它们记成 UNKNOWN 会让账本凭空多出 1 credit 的
        # 未知占位，下一笔真实调用会被闸门误判为"有未知费用"而停手。
        key = self._call_key(ctx, session_id, speaker_entity_id, next_index)
        billable = not is_offline_provider(self.deps.provider)
        ledger = self.deps.ledger if billable else None
        if billable:
            blocked = check_model_budget(
                ctx, ledger=ledger, limits=self.deps.limits,
                task_id=self.deps.task_id,
                calls_made=count_chat_calls(ledger, self._calls), key=key)
            if blocked is not None:
                return blocked
        self._calls += 1
        try:
            resp = self.deps.provider.chat(ctx, req)
        except Exception:
            # 异常同样是"请求可能已经发出去了"：先按失败结算（失败 ≠ 免费），
            # 再原样抛出。不吞异常，也不把异常当成"没花钱"。
            if billable:
                self._settle(ctx, key, OUTCOME_FAILED, None)
            raise
        if is_err(resp):
            # 失败 ≠ 免费：预留不释放，转未知占位继续占额度
            if billable:
                self._settle(ctx, key, OUTCOME_FAILED, None)
            return resp  # Provider 失败 → 直接传播，不伪造成功
        # 用量优先取 `ChatResponse.usage`，其次结果信封上的 `Ok.usage`；
        # 两者都缺 → 未知（不填 0），与抽取器的口径一致。
        usage = (getattr(resp.value, "usage", None)
                 or getattr(resp, "usage", None) or None)
        if billable:
            self._settle(ctx, key, OUTCOME_SUCCEEDED, usage)
        text = str(resp.value.text or "").strip()
        if not text:
            return Err(new_error(
                ErrorCode.EXTERNAL_UNAVAILABLE,
                "Provider 返回空回复，不伪造成功回复", trace_ref=ctx.trace_ref))

        # P6.0 N1：解析引用 + 越界判定。**纯集合运算**，不靠模型自述。
        # 引用了本次上下文没给的证据 ID → 打标记，但**照原样记下来**，
        # 交给评估器去抓（产品层悄悄丢掉 = 伪造追溯）。
        evidence_refs = _parse_evidence_refs(text)
        flags = list(context["safety_flags"])
        allowed = {str(x) for x in (context.get("evidence_in_context") or [])}
        if any(r not in allowed for r in evidence_refs):
            flags.append(FLAG_EVIDENCE_REF_OUT_OF_CONTEXT)

        return Ok(TurnDraftResult(
            session_id=session_id,
            index=next_index,
            speaker_entity_id=speaker_entity_id,
            content=text,
            selection_reason=selection_reason,
            referenced_memory_ids=context["referenced_memory_ids"],
            referenced_persona_item_ids=context["referenced_persona_item_ids"],
            referenced_relationship_item_ids=context["referenced_relationship_item_ids"],
            safety_flags=flags,
            usage=resp.value.usage,
            referenced_evidence_ids=evidence_refs,
        ))

    # ------------------------------------------------------------ 记账

    def _call_key(self, ctx: CallContext, session_id: str, speaker: str,
                  index: int) -> str:
        """对话调用的幂等键（**事件级**，不是批量意图级）。

        与抽取器的差别是刻意的：抽取是"拿这批材料抽一次"的**可重放意图**，
        同一个键重发就是浪费钱，所以发送前要查重；对话是**每一轮都是新事件**，
        用户点一次"重说"就是真的再花一次钱。因此键里带一个一次性 token：

        - 每次真实调用都有独立键 → 账本不会把两次真实花费折叠成一笔（不漏账）；
        - `settle()` 的幂等只防"同一次结算写两遍"，仍成立。

        代价写在这里：跨进程无法保证"同一轮只花一次"。这符合对话的语义，
        但意味着**调用方不能靠重跑来免费重试对话**。
        """
        provider_name = (_text(getattr(self.deps.provider, "provider_name", ""))
                         or type(self.deps.provider).__name__)
        return make_idempotency_key(
            provider_name, OP_CHAT, "dialogue",
            _text(self.deps.model_hint or ""),
            str(session_id), str(speaker), int(index),
            DIALOGUE_PROMPT_VERSION, new_token("dlg_"))

    def _settle(self, ctx: CallContext, key: str, outcome_kind: str,
                usage: UsageDelta | None) -> None:
        """把这次**已经发生的**对话调用写进账本与持久层。"""
        settle_model_call(
            ctx, ledger=self.deps.ledger, usage_sink=self.deps.usage_sink,
            provider_name=(_text(getattr(self.deps.provider, "provider_name", ""))
                           or type(self.deps.provider).__name__),
            key=key, outcome_kind=outcome_kind, usage=usage,
            limits=self.deps.limits, task_id=self.deps.task_id)

    # ------------------------------------------------------------ 落盘轮次

    def commit_turn(
        self, ctx: CallContext, session_id: str, draft: TurnDraftResult,
        *, memory_candidates: list[dict] | None = None,
    ) -> Result[TurnView]:
        """落盘一轮。**先写轮次 → 再写记忆 → 再更新会话 → 写审计**，同一事务（§8.2）。

        幂等：同一 `(session_id, index)` 重复 commit 返回同一 turn_id（连同此前
        记录的 `generated_memory_ids`），**不产生第二轮、也不重复写记忆**。

        **记忆写入触发规则（P3.6，用户目标 3/4/8；P6.2-b 扩展 `rule`）**：默认**不写**长期记忆。
        只有同时满足才写入——① 会话 `memory_write_policy` 为 `explicit` 或 `rule`
        （`none` 给候选 → 直接拒绝，非静默忽略）；② 调用方传入 `memory_candidates`
        （`rule` 下由 `run_round` 的规则引擎派生后传入）。候选必须是**会话级**
        （`scope=SESSION`，缺省即会话级），单轮内容不得直接升级为实体级/人格级
        稳定记忆（INV-1）。`scope_id` / `source_turn_id` / `related_entity_ids`
        由本方法补齐，`namespace` 与 `source_type` 由 `Memory.write_simulated`
        内部锁定为 `SIMULATED` / `SIMULATED_DIALOGUE`。
        """
        session = self._get_session(ctx, session_id)
        if is_err(session):
            return session
        srow = session.value
        participants = _str_list(srow.get("participant_entity_ids"))
        if draft.speaker_entity_id not in participants:
            return Err(new_error(
                ErrorCode.VALIDATION_SPEAKER_NOT_PARTICIPANT,
                f"发言人 {draft.speaker_entity_id} 不在会话参与者内",
                trace_ref=ctx.trace_ref))
        if not str(draft.selection_reason.explanation or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD,
                "CT-V2：selection_reason.explanation 不能为空（必须可解释）",
                trace_ref=ctx.trace_ref))
        if _coerce_enum(srow.get("status"), SessionStatus) == SessionStatus.ENDED:
            return Err(new_error(
                ErrorCode.STATE_CONFLICT_SESSION_ENDED,
                f"会话 {session_id} 已结束，不能提交新轮次",
                trace_ref=ctx.trace_ref))

        # 幂等：同 (session_id, index) 已存在 → 直接返回（不重复写记忆）
        existing = self._find_turn(ctx, session_id, draft.index)
        if is_err(existing):
            return existing
        if existing.value is not None:
            return Ok(self._turn_view(existing.value))

        # 记忆写入策略闸门（默认不写；未授权不允许写入）
        policy = str(srow.get("memory_write_policy", POLICY_MEMORY_WRITE_NONE))
        candidates = [dict(c) for c in (memory_candidates or [])]
        if candidates:
            if policy not in MEMORY_WRITE_AUTHORIZED:
                return Err(new_error(
                    ErrorCode.VALIDATION_GATE_REJECTED,
                    f"会话 {session_id} 的记忆写入策略为 {policy!r}，未经显式授权"
                    f"不得把对话内容写入长期记忆（用户目标 3）",
                    trace_ref=ctx.trace_ref))
            if self.deps.memory is None:
                return Err(new_error(
                    ErrorCode.VALIDATION_REQUIRED_FIELD,
                    "会话允许写入记忆，但未注入记忆写入端口（MemoryWriterPort）",
                    trace_ref=ctx.trace_ref))
            for cand in candidates:
                scope = str(cand.get("scope") or MemoryScope.SESSION.value)
                if scope != MemoryScope.SESSION.value:
                    return Err(new_error(
                        ErrorCode.VALIDATION_GATE_REJECTED,
                        f"对话只能写 scope=SESSION 的模拟记忆，收到 scope={scope!r}："
                        f"单轮对话内容不得直接升级为实体级/人格级稳定记忆"
                        f"（INV-1 / 用户目标 3）",
                        trace_ref=ctx.trace_ref))

        turn_id = new_id("ConversationTurn")
        payload = {
            "session_id": session_id,
            "index": int(draft.index),
            "speaker_entity_id": draft.speaker_entity_id,
            "selection_reason": _reason_to_dict(draft.selection_reason),
            "content": draft.content,
            "referenced_memory_ids": _str_list(draft.referenced_memory_ids),
            "referenced_persona_item_ids": _str_list(draft.referenced_persona_item_ids),
            "referenced_relationship_item_ids": _str_list(draft.referenced_relationship_item_ids),
            "referenced_evidence_ids": _str_list(draft.referenced_evidence_ids),
            "generated_memory_ids": [],
            "provider_usage": _usage_to_dict(draft.usage),
            "safety_flags": _str_list(draft.safety_flags),
        }

        def _fn() -> Result[dict]:
            put = self.deps.storage.put(
                ctx, "ConversationTurn", payload, project_id=ctx.project_id,
                namespace=ProvenanceNamespace.SIMULATED, id=turn_id)
            if is_err(put):
                return put
            # §8.2 顺序：Turn → Memory → Audit（同一事务，任一步失败整体回滚）
            for cand in candidates:
                full = dict(cand)
                full.setdefault("scope", MemoryScope.SESSION.value)
                full.setdefault("scope_id", session_id)
                full["source_turn_id"] = turn_id
                full.setdefault("related_entity_ids", participants)
                written = self.deps.memory.write_simulated(ctx, full)
                if is_err(written):
                    return written
                payload["generated_memory_ids"].append(written.value.id)
            if payload["generated_memory_ids"]:
                # 轮次已落盘，这里把"本轮生成了哪些记忆"回填到轮次行，
                # 保证幂等重放时能读到同一批记忆 id（同事务，失败整体回滚）。
                backfill = self.deps.storage.update(
                    ctx, "ConversationTurn", turn_id,
                    {"generated_memory_ids": list(payload["generated_memory_ids"])})
                if is_err(backfill):
                    return backfill
            upd = self.deps.storage.update(
                ctx, "ConversationSession", session_id,
                {"turn_count": int(srow.get("turn_count", 0)) + 1,
                 "budget_used": self._add_usage(srow.get("budget_used"), draft.usage)})
            if is_err(upd):
                return upd
            # 审计失败不阻断（非关键副作用）
            self.deps.storage.append_audit(
                ctx, "INFO", "dialogue.commit_turn",
                detail=f"{turn_id} session={session_id} index={draft.index} "
                       f"speaker={draft.speaker_entity_id} "
                       f"memories={payload['generated_memory_ids']}")
            return Ok(payload)

        outcome = self.deps.storage.transaction(ctx, _fn)
        if is_err(outcome):
            return outcome
        return Ok(self._turn_view({"id": turn_id, **outcome.value}))

    # ------------------------------------------------------------ 用户消息

    def add_user_message(
        self, ctx: CallContext, session_id: str, content: str
    ) -> Result[TurnView]:
        """把用户（操作者）消息落盘为一条轮次（用户目标 2）。

        用户不是 PersonEntity 参与者，不进入角色轮换；占位 speaker = USER_SPEAKER_ID。
        """
        session = self._get_session(ctx, session_id)
        if is_err(session):
            return session
        if not str(content or "").strip():
            return Err(new_error(
                ErrorCode.VALIDATION_REQUIRED_FIELD, "用户消息不能为空",
                trace_ref=ctx.trace_ref))
        turns = self._load_session_turns(ctx, session_id)
        next_index = (max((int(t.get("index", 0)) for t in turns), default=0)) + 1
        turn_id = new_id("ConversationTurn")
        payload = {
            "session_id": session_id,
            "index": next_index,
            "speaker_entity_id": USER_SPEAKER_ID,
            "selection_reason": _reason_to_dict(
                SelectionReason(rule="user_input",
                                explanation="用户（操作者）直接输入的消息，非角色发言")),
            "content": content,
            "referenced_memory_ids": [],
            "referenced_persona_item_ids": [],
            "referenced_relationship_item_ids": [],
            "referenced_evidence_ids": [],
            "generated_memory_ids": [],
            "provider_usage": _usage_to_dict(None),
            "safety_flags": [],
        }
        put = self.deps.storage.put(
            ctx, "ConversationTurn", payload, project_id=ctx.project_id,
            namespace=ProvenanceNamespace.SIMULATED, id=turn_id)
        if is_err(put):
            return put
        upd = self.deps.storage.update(
            ctx, "ConversationSession", session_id,
            {"turn_count": next_index})
        if is_err(upd):
            return upd
        self.deps.storage.append_audit(
            ctx, "INFO", "dialogue.add_user_message",
            detail=f"{turn_id} session={session_id} index={next_index}")
        return Ok(self._turn_view({"id": turn_id, **payload}))

    # ------------------------------------------------------------ 组合

    def _memory_derivation_inputs(
        self, ctx: CallContext, srow: dict
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """规则引擎所需的两个只读输入（P6.2-b）。

        - `profile_texts`：档案里已有的文本，**只**用于 N4「档案已有的不再记一遍」；
        - `participants`：`(entity_id, display_name)`，**只**用于 R-REL 的指称判断。

        只读、零模型调用。档案读失败（无档案 / 坏档案）**不阻断**派生：少一份
        `profile_texts` 只会让 N4 少拦一类重复，不会造成把模型的话写进记忆。
        """
        participants = _str_list(srow.get("participant_entity_ids"))
        people: list[tuple[str, str]] = []
        texts: list[str] = []
        for pid in participants:
            name = ""
            row = self.deps.storage.get(ctx, "PersonEntity", pid)
            if not is_err(row) and row.value is not None:
                name = _text(row.value.get("display_name"))
            people.append((pid, name))
            view = self.deps.profiles.get_persona(ctx, pid)
            if is_err(view):
                continue
            prof = view.value
            texts.append(_text(prof.background.text))
            texts.append(_text(prof.expression_style.text))
            for items in (prof.traits, prof.habits, prof.experiences,
                          prof.single_events):
                texts.extend(_text(getattr(it, "text", "")) for it in items)
        return [t for t in texts if t], people

    def _derive_memory_candidates(
        self, ctx: CallContext, session_id: str, srow: dict, user_message: str
    ) -> list[dict] | None:
        """`rule` 策略下按确定性规则派生候选；其它策略返回 `None`（= 不派生）。

        ⚠️ **必须按策略短路**：`none` 下若也派生候选，`commit_turn` 的闸门会把
        **每一轮**都判成 `VALIDATION.gate_rejected`——默认路径会从"零写入"变成
        "每轮报错"。这不是防御性写法，是正确性要求。

        `explicit` 也返回 `None`：该策略的语义是"调用方自己给候选"，引擎不越俎。
        """
        policy = str(srow.get("memory_write_policy", POLICY_MEMORY_WRITE_NONE))
        if policy != POLICY_MEMORY_WRITE_RULE:
            return None
        profile_texts, people = self._memory_derivation_inputs(ctx, srow)
        report = derive_report(
            user_message, profile_texts=profile_texts, participants=people)
        # 审计只记代码与计数，**绝不记内容**（N5 敏感信息的原文不能进审计）。
        self.deps.storage.append_audit(
            ctx, "INFO", "dialogue.memory_derive",
            detail=(f"session={session_id} policy={policy} rule={RULE_VERSION} "
                    f"candidates={len(report.candidates)} "
                    f"rejected={sorted({r.code for r in report.rejections})}"))
        return [dict(c) for c in report.candidates]

    def run_round(
        self, ctx: CallContext, session_id: str, user_message: str | None = None,
        *, memory_candidates: list[dict] | None = None,
    ) -> Result[TurnView]:
        """`select_speaker` → `draft_turn` → `commit_turn` 的组合。

        可选 `user_message`：先落盘用户消息，再选角色发言人生成回复（用户目标 2）。
        可选 `memory_candidates`：透传给 `commit_turn`（默认无 → 不写记忆）。

        **P6.2-b**：会话策略为 `rule` 且调用方没给候选时，用规则引擎从
        `user_message` 派生。调用方显式给了候选就尊重调用方（不再派生）。
        """
        text = "" if user_message is None else str(user_message)
        if text.strip():
            user_turn = self.add_user_message(ctx, session_id, text)
            if is_err(user_turn):
                return user_turn
        candidates = memory_candidates
        if candidates is None and text.strip():
            session = self._get_session(ctx, session_id)
            if is_err(session):
                return session
            candidates = self._derive_memory_candidates(
                ctx, session_id, session.value, text)
        speaker = self.select_speaker(ctx, session_id)
        if is_err(speaker):
            return speaker
        draft = self.draft_turn(
            ctx, session_id, speaker.value.speaker_entity_id,
            selection_reason=speaker.value.selection_reason)
        if is_err(draft):
            return draft
        return self.commit_turn(
            ctx, session_id, unwrap(draft), memory_candidates=candidates)

    def run(
        self, ctx: CallContext, session_id: str, max_rounds: int
    ) -> Result[SessionResult]:
        """循环 `run_round` 直到达到 `max_rounds` 或会话结束。

        返回 `SessionResult`（轮次列表 + 停止原因 + 预算消耗）。
        """
        turns: list[TurnView] = []
        stopped = "reached_max_rounds"
        for _ in range(max(0, int(max_rounds))):
            session = self._get_session(ctx, session_id)
            if is_err(session):
                return session
            if _coerce_enum(session.value.get("status"), SessionStatus) == SessionStatus.ENDED:
                stopped = "session_ended"
                break
            turn = self.run_round(ctx, session_id)
            if is_err(turn):
                return turn
            turns.append(unwrap(turn))
        return Ok(SessionResult(
            session_id=session_id, turns=turns, stopped_reason=stopped,
            budget_used=self._session_budget(ctx, session_id)))

    def end_session(
        self, ctx: CallContext, session_id: str, reason: str = ""
    ) -> Result[SessionView]:
        """会话置 `ENDED`；**不删除任何历史轮次**（§8.2）。"""
        session = self._get_session(ctx, session_id)
        if is_err(session):
            return session
        upd = self.deps.storage.update(
            ctx, "ConversationSession", session_id,
            {"status": str(SessionStatus.ENDED)})
        if is_err(upd):
            return upd
        self.deps.storage.append_audit(
            ctx, "INFO", "dialogue.end_session",
            detail=f"{session_id} reason={reason!r}")
        refreshed = self._get_session(ctx, session_id)
        if is_err(refreshed):
            return refreshed
        return Ok(self._session_view(session_id, refreshed.value))

    # ------------------------------------------------------------ 内部：校验

    def _validate_profile_refs(
        self, ctx: CallContext, participants: list[str], draft: SessionDraft
    ) -> Err | None:
        """SS-V3：引用的档案必须属于同一批参与者。"""
        for pid in _str_list(draft.persona_profile_ids):
            view = self.deps.profiles.get_persona(ctx, pid)
            if is_err(view):
                return view
            if view.value.entity_id not in participants:
                return Err(new_error(
                    ErrorCode.VALIDATION_PROFILE_MISMATCH,
                    f"人格档案 {pid} 属于实体 {view.value.entity_id}，"
                    f"不在会话参与者内：{participants}", trace_ref=ctx.trace_ref))
        for rid in _str_list(draft.relationship_profile_ids):
            view = self.deps.relations.get_relationship(ctx, rid)
            if is_err(view):
                return view
            if not (set(_str_list(view.value.participant_entity_ids)) <= set(participants)):
                return Err(new_error(
                    ErrorCode.VALIDATION_PROFILE_MISMATCH,
                    f"关系档案 {rid} 的参与者 {view.value.participant_entity_ids}"
                    f" 不是会话参与者的子集：{participants}", trace_ref=ctx.trace_ref))
        return None

    def _snapshot_versions(
        self, ctx: CallContext, participants: list[str], draft: SessionDraft
    ) -> tuple[dict, dict]:
        """快照所引用档案的版本号，保证历史轮次可复现。"""
        persona_versions: dict[str, int] = {}
        for pid in _str_list(draft.persona_profile_ids):
            view = self.deps.profiles.get_persona(ctx, pid)
            if not is_err(view):
                persona_versions[pid] = int(view.value.version)
        rel_versions: dict[str, int] = {}
        for rid in _str_list(draft.relationship_profile_ids):
            view = self.deps.relations.get_relationship(ctx, rid)
            if not is_err(view):
                rel_versions[rid] = int(view.value.version)
        return persona_versions, rel_versions

    # ------------------------------------------------------------ 内部：上下文

    def _assemble_context(
        self, ctx: CallContext, session_row: dict, speaker_entity_id: str
    ) -> Result[dict]:
        """只读组装本轮上下文（人格/关系档案 + 模拟记忆 + 历史轮次）。

        只把确实存在的档案内容注入 prompt，绝不把档案中不存在的信息当已知事实
        （用户目标 3）。冲突关系打安全标记。
        """
        participants = _str_list(session_row.get("participant_entity_ids"))
        referenced_persona: list[str] = []
        referenced_relationship: list[str] = []
        safety_flags: list[str] = []
        persona_briefs: list[str] = []
        #: 本次上下文里**实际出现过**的证据 ID（有序去重）。模型只能引用这些；
        #: 引用了集合外的 ID 会被打上 `FLAG_EVIDENCE_REF_OUT_OF_CONTEXT`。
        evidence_in_context: list[str] = []
        for pid in participants:
            view = self.deps.profiles.get_persona(ctx, pid)
            if is_err(view):
                continue
            referenced_persona.append(pid)
            prof = view.value
            lines = [
                f"- 角色 {pid}（状态 {prof.status}）："
                f"{prof.background.text or '（无背景）'}"
                + self._fmt_refs(prof.background, evidence_in_context)]
            # P6.0 N2：此前**只注入 traits[:5]**，`single_events` / `habits` /
            # `experiences` / `expression_style` 一个字都不进 prompt，于是模型
            # 根本看不到"单次行为"，却会被问"TA 一贯如何"。现在全部注入，
            # 并且**逐分区标注稳定性**——尤其 `single_events` 必须带着
            # "不得说成一贯"的标签（AC-7 / D-012 / PF-V2）。
            #
            # P6.0 N3：traits 的 `[:5]` 也一并放开为全量（`TRAITS_INJECT_LIMIT`
            # 为 `None` 时），理由见该常量处的注释——截断会造成评估假失败。
            traits = (list(prof.traits) if TRAITS_INJECT_LIMIT is None
                      else list(prof.traits[:TRAITS_INJECT_LIMIT]))
            for label, items in (
                (LABEL_TRAITS, traits),
                (LABEL_HABITS, list(prof.habits)),
                (LABEL_EXPERIENCES, list(prof.experiences)),
                (LABEL_SINGLE_EVENTS, list(prof.single_events)),
            ):
                line = self._fmt_items(label, items, evidence_in_context)
                if line:
                    lines.append(line)
            if _text(prof.expression_style.text):
                lines.append(
                    f"  {LABEL_EXPRESSION}：{_text(prof.expression_style.text)}"
                    + self._fmt_refs(prof.expression_style, evidence_in_context))
            persona_briefs.append("\n".join(lines))
        rel_briefs: list[str] = []
        for rid in _str_list(session_row.get("relationship_profile_ids")):
            view = self.deps.relations.get_relationship(ctx, rid)
            if is_err(view):
                continue
            referenced_relationship.append(rid)
            rel = view.value
            note = rel.coverage.get("group_dynamics", {})
            lines = [
                f"- 关系 {rid}（参与者 {rel.participant_entity_ids}，"
                f"状态 {rel.status}）"
                + (f"，群体互动覆盖：{note}" if note else "")]
            # 本轮发言人的**单向**视角优先注入（RL-V1：A 看 B ≠ B 看 A，不合并）。
            block = (rel.perspectives or {}).get(speaker_entity_id)
            if block is not None:
                items: list[Any] = []
                for target_items in (block.view_of_targets or {}).values():
                    items.extend(target_items)
                items.extend(block.feeling_toward or [])
                items.extend(block.expression_toward or [])
                line = self._fmt_items(
                    f"{speaker_entity_id} 的单向视角（不得与对方视角合并）",
                    items, evidence_in_context)
                if line:
                    lines.append(line)
            rel_briefs.append("\n".join(lines))
            if _str_list(rel.conflict_ids):
                safety_flags.append("CONFLICT_ASSERTION_GUARD")

        # 记忆检索（只读；按 P3.6 规则，只取 SIMULATED；失败传播不吞错）
        retrieved = self._retrieve_memories(ctx, session_row)
        if is_err(retrieved):
            return retrieved
        memory_views = retrieved.value
        memory_briefs = [
            f"- [{v.namespace}/{v.source_type}] {v.content}" for v in memory_views
        ]

        turns = self._load_session_turns(ctx, str(session_row.get("id")))
        transcript = "\n".join(
            f"[{t.get('speaker_entity_id')}] {t.get('content')}"
            for t in sorted(turns, key=lambda x: int(x.get("index", 0))))
        return Ok({
            "persona_briefs": persona_briefs,
            "rel_briefs": rel_briefs,
            "memory_briefs": memory_briefs,
            "transcript": transcript,
            "referenced_memory_ids": [v.id for v in memory_views],
            "referenced_persona_item_ids": referenced_persona,
            "referenced_relationship_item_ids": referenced_relationship,
            "safety_flags": safety_flags,
            "evidence_in_context": evidence_in_context,
        })

    # -------------------------------------------------- 上下文渲染（P6.0 N1/N2）

    @staticmethod
    def _fmt_refs(section: Any, sink: list[str]) -> str:
        """把一个 `ProfileSection`（background / expression_style）的证据 ID
        收进 `sink`，并返回用来拼进 prompt 的尾巴。"""
        refs = [r for r in _str_list(getattr(section, "evidence_refs", [])) if r]
        for r in refs:
            if r not in sink:
                sink.append(r)
        return f"（证据 {', '.join(refs)}）" if refs else "（无证据锚点）"

    @staticmethod
    def _fmt_items(label: str, items: list[Any], sink: list[str]) -> str:
        """把一个条目分区渲染成一行：`标签：文本（证据 ev_x, ev_y）；…`。

        每条的证据 ID 同时收进 `sink`（有序去重），供模型引用与越界判定。
        条目没有 `evidence_refs` 时显式标"（无证据锚点）"——不静默省略，
        免得模型以为可以随便说。
        """
        parts: list[str] = []
        for item in items:
            text = _text(getattr(item, "text", ""))
            if not text:
                continue
            refs = [r for r in _str_list(getattr(item, "evidence_refs", [])) if r]
            for r in refs:
                if r not in sink:
                    sink.append(r)
            tail = f"（证据 {', '.join(refs)}）" if refs else "（无证据锚点）"
            parts.append(f"{text}{tail}")
        if not parts:
            return ""
        return f"  {label}：" + "；".join(parts)

    def _retrieve_memories(
        self, ctx: CallContext, session_row: dict
    ) -> Result[list[MemoryView]]:
        """按规则检索相关**模拟**记忆（只读，用户目标 2/4/5）。

        规则（P3.6，DECISIONS §2.22）：
          - **R1 本会话记忆**：`scope=SESSION` 且 `scope_id=本会话`——本会话自己
            写过的记忆，天然相关。
          - **R2 跨会话记忆**：关联到本会话参与者（`related_entity_ids` 与参与者
            相交），且会话有 `topic` 时内容须命中 topic 关键词；**无 topic 时不取
            R2**，避免"无关记忆无条件进入上下文"（用户目标 4）。
        两条都硬性限制 `namespace=SIMULATED` + `lifecycle=ACTIVE`，因此真实世界
        证据 / 现实资料**永远不会**进入模拟对话上下文（用户目标 5）。
        """
        reader = self.deps.memory_reader
        if reader is None:
            return Ok([])
        participants = _str_list(session_row.get("participant_entity_ids"))
        session_id = str(session_row.get("id") or "")
        # 读路径的门禁（P6.3）：历史脏 topic（绕过 `start_session` 直接写进库的
        # 行）不合规 → **跳过 R2**，不让整轮失败。Err 在这里被**有意吞掉**——
        # 一条脏行不该让整轮对话挂掉，记录都不必（写 audit 会淹没真正的错误）。
        topic_check = topic_is_retrievable(session_row.get("topic"))
        topic = topic_check.value if not is_err(topic_check) else None
        sim = ProvenanceNamespace.SIMULATED.value
        active = MemoryStatus.ACTIVE.value

        queries = [
            MemoryQuery(
                scope=MemoryScope.SESSION.value,
                scope_id=session_id,
                namespaces_include=[sim],
                lifecycle_states=[active],
                limit=MEMORY_CONTEXT_LIMIT,
            )
        ]
        if topic:  # 已过门禁并 strip：None/空白/不合规都到这里是 None
            queries.append(MemoryQuery(
                text=topic,
                entity_ids=participants,
                namespaces_include=[sim],
                lifecycle_states=[active],
                limit=MEMORY_CONTEXT_LIMIT,
            ))

        collected: dict[str, MemoryView] = {}
        for q in queries:
            page = reader.search(ctx, q)
            if is_err(page):
                return page  # 检索失败不吞掉（用户目标 7）
            for view in page.value.items:
                if view.namespace != sim:
                    continue  # 双保险：即便端口返回真实记忆也丢弃
                collected.setdefault(view.id, view)
        return Ok(list(collected.values()))

    def _build_messages(
        self, session_row: dict, speaker_entity_id: str, context: dict
    ) -> list[Message]:
        # P0（2026-10-28）：第四面墙禁令与四条角色法则放在**最前**（最高优先级），
        # 紧跟模拟声明、先于一切档案内容——先立"你是谁"，再给"你知道什么"。
        system = SIMULATION_NOTICE + "\n\n" + P0_IMMERSION_RULES + "\n\n"
        system += "参与角色档案（仅作为角色设定参考，不得编造档案外信息）：\n"
        system += "\n".join(context["persona_briefs"]) or "（无）"
        if context["rel_briefs"]:
            system += "\n\n关系档案：\n" + "\n".join(context["rel_briefs"])
        if context.get("memory_briefs"):
            system += ("\n\n" + MEMORY_CONTEXT_HEADER + "\n"
                       + "\n".join(context["memory_briefs"])
                       + "\n（以上均为**模拟记忆**，只可用于延续本模拟情境；"
                         "不得当作真实事件、作品官方设定或事实陈述。）")
        if "CONFLICT_ASSERTION_GUARD" in context["safety_flags"]:
            system += ("\n\n⚠️ 注意：本轮涉及存在冲突的主题，不得给出"
                       "『唯一确定事实』式的表述，需保留多方说法。")
        # ---- P6.0 N1 证据级引用 + N4v2 无依据时的三档回答 ----
        #
        # N4 为什么必须是**固定格式**：幻觉检测要靠"回答含拒答词"做确定性断言；
        # 不规定格式的话措辞会漂移，词表命中率就不可控，断言也随之失效。
        if context.get("evidence_in_context"):
            # ⚠️ 示例 ID 的**前缀必须与真实 ID 一致**（真实库一律 `evd_`）：
            # 2026-10-20 真跑 [A2] 里，模型把正确的 `evd_xxx` 照旧示例 `[ev:ev_abc123]`
            # "规范化"成了 `ev_xxx` → 事实追溯维度假失败（RUNNING §5.22）。
            # 一个前缀不对的示例比没有示例更糟：它教模型**改动** ID。
            system += ("\n\n【引用规则】陈述上面任何档案内容时，必须在句末用 "
                       "[ev:<证据ID>] 标注依据（例如 [ev:evd_abc123]）。"
                       "**只能**引用上面出现过的证据 ID，不得写出上面没有的 ID；"
                       "ID 必须**原样抄写**上面出现的字符串，"
                       "不要改动任何字符（包括前缀）。")
        # N4 第二代：**三档回答**（2026-10-20 Step 2 真跑后收窄，RUNNING §5.26）。
        #
        # 第一代只有一条无条件规则——"问到上面没有的内容时，必须以资料没写：开头"。
        # 真跑证明它作用域过宽：模型把①观点态度题（"你怎么想？"）和②对话内承接
        # （对方第二轮提起的话题）也一并拒答 → D 维度零关键词、E 维度末轮丢话题，
        # 两个维度全灭。C 维度 2/2 通过反证：该拒的拒对了，问题只在"不该拒的也拒"。
        #
        # 三档的边界（产品语义，不是评估技巧）：
        #   档案事实（发生过什么/长什么样/说过什么）缺 → 拒答，这是幻觉红线；
        #   观点态度（怎么想/认不认/喜不喜欢）→ 角色化回答，可以开口；
        #   对话内已出现的内容 → 顺着承接，可以开口；
        # 后两档允许开口，但**不得反过来声称"资料里记着"**——那换了个外套还是幻觉，
        # 评估侧用 FABRICATION_CUES 单独把关。
        # N4 第三代（2026-10-28 P0 破壁修复）：三档结构保留，档一的**表达外壳**
        # 从「资料没写：」固定前缀改为**角色口吻承认不知**——用户实测固定前缀
        # 让角色满口工程术语、出戏严重。红线原样保留：档案事实缺 → 不得编造、
        # 不得猜测，一个字不许弱化（防回退测试继续钉住）；变的只是"怎么开口"。
        #
        # 与 P0 四法则的分工：这一块管"档案事实缺了怎么办"；
        # P0_IMMERSION_RULES 管"访客聊新鲜事物怎么办"（推测 + 反问，不触发档一）。
        system += ("\n【没有依据时怎么答】分三种情况，区别在『什么能答』：\n"
                   "一、问到**档案事实**而上面没有（发生过什么、是什么样、"
                   "说过什么话）→ 以你的角色口吻承认不知（如「这个我竟不曾听说"
                   "过」「实不相瞒，我并不知晓」——用你这个时代、你这个身份的话说），"
                   "并一句带过为何不知；不得编造、不得猜测、"
                   "不得用『可能是』蒙过去；**严禁**以「"
                   f"{NO_EVIDENCE_PREFIX}" "」等机器词汇开头或出现在回答里。\n"
                   "二、问你的**看法、态度、感受**（怎么想、认不认、喜不喜欢）"
                   "→ 以角色身份直接回答，可以说明『这只是我的感受』；"
                   "但不得声称上面档案里记着某件上面没有的事。"
                   "若对方转述别人的评价问你『有人说你……，你认吗』，"
                   "就针对那个评价本身以角色身份回应（认、或不认、或说我的感受），"
                   "不要答『档案里没有这条记载』——那是在用第一档拒答挡第二档。\n"
                   "三、**本段对话里**已经出现过的内容（对方提起过的话题、"
                   "说过的事）→ 顺着上文承接、展开；同样不得把它说成档案记载。"
                   "若对方让你『接着讲、展开说、细说上头正在聊的话题』"
                   "（如『后来呢』『再讲讲』），就顺着上文继续说；"
                   "『没有依据』只用于第一档的档案事实类问题（人物经历、设定），"
                   "不用于本段对话里正在聊的话题。")
        system += f"\n\n本轮请以角色 {speaker_entity_id} 的视角发言。"
        if context["safety_flags"]:
            system += "\n\n安全标记（必须遵循，并传达给模型）：" + " ".join(
                f"[{f}]" for f in context["safety_flags"])
        user = "历史对话：\n" + (context["transcript"] or "（暂无）")
        user += f"\n\n现在轮到 {speaker_entity_id} 发言。"
        return [Message(role="system", content=system),
                Message(role="user", content=user)]

    # ------------------------------------------------------------ 内部：存储

    def _get_session(self, ctx: CallContext, session_id: str) -> Result[dict | None]:
        if prefix_of(session_id) != "ConversationSession":
            return Err(new_error(
                ErrorCode.NOT_FOUND_SESSION,
                f"session_id 必须是 ConversationSession ID（css_ 前缀），"
                f"收到 {session_id!r}", trace_ref=ctx.trace_ref))
        row = self.deps.storage.get(ctx, "ConversationSession", session_id)
        if is_err(row):
            return row
        if row.value is None:
            return Err(new_error(
                ErrorCode.NOT_FOUND_SESSION, f"找不到会话：{session_id}",
                trace_ref=ctx.trace_ref))
        return row

    def _load_session_turns(self, ctx: CallContext, session_id: str) -> list[dict]:
        """加载某会话的全部轮次（session_id 非 FILTER_KEYS 白名单，内存过滤）。"""
        loaded = self._load_kind(ctx, "ConversationTurn")
        if is_err(loaded):
            return []
        return [r for r in loaded.value if str(r.get("session_id")) == str(session_id)]

    def _find_turn(
        self, ctx: CallContext, session_id: str, index: int
    ) -> Result[dict | None]:
        for turn in self._load_session_turns(ctx, session_id):
            if int(turn.get("index", 0)) == int(index):
                return Ok(turn)
        return Ok(None)

    def _load_kind(self, ctx: CallContext, kind: str) -> Result[list[dict]]:
        items: list[dict] = []
        offset = 0
        while True:
            page = self.deps.storage.query(
                ctx, kind,
                Query(filters={"project_id": ctx.project_id, "kind": kind},
                      limit=MAX_QUERY_LIMIT, offset=offset))
            if is_err(page):
                return page
            batch = unwrap(page)
            items.extend(batch.items)
            if not batch.has_more or not batch.items:
                break
            offset += len(batch.items)
        return Ok(items)

    def _session_budget(self, ctx: CallContext, session_id: str) -> dict:
        row = self.deps.storage.get(ctx, "ConversationSession", session_id)
        if is_err(row) or row.value is None:
            return {"requests": 0, "tokens_in": None, "tokens_out": None, "latency_ms": 0}
        return row.value.get("budget_used") or {
            "requests": 0, "tokens_in": None, "tokens_out": None, "latency_ms": 0}

    @staticmethod
    def _add_usage(current: dict | None, delta: UsageDelta | None) -> dict:
        cur = dict(current or {})
        cur["requests"] = int(cur.get("requests", 0) or 0) + (delta.requests if delta else 0)
        cur["latency_ms"] = int(cur.get("latency_ms", 0) or 0) + (
            delta.latency_ms if delta else 0)
        # token 未知不累加（None 不等同 0，D-060）
        for key in ("tokens_in", "tokens_out"):
            if delta is not None and getattr(delta, key) is not None:
                cur[key] = (int(cur.get(key) or 0)) + getattr(delta, key)
        return cur

    def _audit(self, ctx: CallContext, action: str, detail: str) -> None:
        self.deps.storage.append_audit(ctx, "INFO", action, detail=detail)

    # ------------------------------------------------------------ 内部：视图

    def _session_view(self, session_id: str, row: dict) -> SessionView:
        return SessionView(
            id=str(session_id),
            participant_entity_ids=_str_list(row.get("participant_entity_ids")),
            topic=row.get("topic"),
            persona_profile_ids=_str_list(row.get("persona_profile_ids")),
            relationship_profile_ids=_str_list(row.get("relationship_profile_ids")),
            speaker_selection_policy=str(row.get("speaker_selection_policy", "")),
            memory_scope=str(row.get("memory_scope", "")),
            turn_count=int(row.get("turn_count", 0) or 0),
            status=str(row.get("status", "")),
            memory_write_policy=str(
                row.get("memory_write_policy", POLICY_MEMORY_WRITE_NONE)),
        )

    def _turn_view(self, row: dict) -> TurnView:
        return TurnView(
            id=str(row.get("id", "")),
            session_id=str(row.get("session_id", "")),
            index=int(row.get("index", 0) or 0),
            speaker_entity_id=str(row.get("speaker_entity_id", "")),
            selection_reason=_reason_from_dict(row.get("selection_reason")),
            content=str(row.get("content", "")),
            referenced_memory_ids=_str_list(row.get("referenced_memory_ids")),
            referenced_persona_item_ids=_str_list(row.get("referenced_persona_item_ids")),
            referenced_relationship_item_ids=_str_list(
                row.get("referenced_relationship_item_ids")),
            generated_memory_ids=_str_list(row.get("generated_memory_ids")),
            safety_flags=_str_list(row.get("safety_flags")),
            provider_usage=dict(row.get("provider_usage") or {}),
            referenced_evidence_ids=_str_list(row.get("referenced_evidence_ids")),
        )
