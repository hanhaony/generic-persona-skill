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
from ..core.ids import new_id, prefix_of
from ..core.result import Err, Ok, Result, UsageDelta, is_err, unwrap
from ..core.types import MAX_QUERY_LIMIT, Page, Query
from ..providers.base import ChatRequest, Message, Provider
from ..storage.sqlite import SqliteStorage
from .evidence import EvidenceReader
from .memory import MemoryQuery, MemoryView
from .persona import PersonaReader
from .relationship import RelationshipReader


# 会话中"用户（操作者）"的发言占位。它不是 PersonEntity 参与者，不会被纳入
# 角色轮换；仅用于把用户消息落盘为一条轮次（用户目标 2：支持输入用户消息）。
USER_SPEAKER_ID = "__user__"

# 发言人选择策略名（INTERFACES §8.2 `speaker_selection_policy`）
POLICY_LEAST_RECENTLY_SPOKEN = "least_recently_spoken"

# 记忆写入策略（P3.6，DECISIONS §2.22）：
#   none     —— 默认。**绝不**把对话内容写入长期记忆（用户目标 3）。
#   explicit —— 仅当调用方在 `commit_turn` 显式给出 memory_candidates 时才写入。
POLICY_MEMORY_WRITE_NONE = "none"
POLICY_MEMORY_WRITE_EXPLICIT = "explicit"
MEMORY_WRITE_POLICIES = (POLICY_MEMORY_WRITE_NONE, POLICY_MEMORY_WRITE_EXPLICIT)

# 注入上下文的模拟记忆条数上限（防止无关记忆淹没 prompt）。
MEMORY_CONTEXT_LIMIT = 5

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
    """

    profiles: PersonaReader
    relations: RelationshipReader
    evidence: EvidenceReader
    memory: MemoryWriterPort | None
    storage: SqliteStorage
    provider: Provider
    memory_reader: MemoryReaderPort | None = None


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


# --------------------------------------------------------------------------
# 服务实现
# --------------------------------------------------------------------------


class DialogueService:
    """多人多轮对话基础服务。只依赖注入的只读端口、存储与 Provider。"""

    def __init__(self, deps: DialogueDeps) -> None:
        self.deps = deps

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

        # 快照版本号（可复现性）
        persona_versions, rel_versions = self._snapshot_versions(
            ctx, participants, draft)

        session_id = new_id("ConversationSession")
        payload = {
            "participant_entity_ids": participants,
            "topic": draft.topic,
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
            detail=f"{session_id} participants={participants} topic={draft.topic!r}")
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
            temperature=0.7,
            metadata={"session_id": session_id,
                      "speaker_entity_id": speaker_entity_id,
                      "fixture_key": f"dialogue_turn:{speaker_entity_id}"},
        )
        resp = self.deps.provider.chat(ctx, req)
        if is_err(resp):
            return resp  # Provider 失败 → 直接传播，不伪造成功
        text = str(resp.value.text or "").strip()
        if not text:
            return Err(new_error(
                ErrorCode.EXTERNAL_UNAVAILABLE,
                "Provider 返回空回复，不伪造成功回复", trace_ref=ctx.trace_ref))

        return Ok(TurnDraftResult(
            session_id=session_id,
            index=next_index,
            speaker_entity_id=speaker_entity_id,
            content=text,
            selection_reason=selection_reason,
            referenced_memory_ids=context["referenced_memory_ids"],
            referenced_persona_item_ids=context["referenced_persona_item_ids"],
            referenced_relationship_item_ids=context["referenced_relationship_item_ids"],
            safety_flags=context["safety_flags"],
            usage=resp.value.usage,
        ))

    # ------------------------------------------------------------ 落盘轮次

    def commit_turn(
        self, ctx: CallContext, session_id: str, draft: TurnDraftResult,
        *, memory_candidates: list[dict] | None = None,
    ) -> Result[TurnView]:
        """落盘一轮。**先写轮次 → 再写记忆 → 再更新会话 → 写审计**，同一事务（§8.2）。

        幂等：同一 `(session_id, index)` 重复 commit 返回同一 turn_id（连同此前
        记录的 `generated_memory_ids`），**不产生第二轮、也不重复写记忆**。

        **记忆写入触发规则（P3.6，用户目标 3/4/8）**：默认**不写**长期记忆。
        只有同时满足才写入——① 会话 `memory_write_policy="explicit"`；
        ② 调用方显式传入 `memory_candidates`。候选必须是**会话级**
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
            if policy != POLICY_MEMORY_WRITE_EXPLICIT:
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

    def run_round(
        self, ctx: CallContext, session_id: str, user_message: str | None = None,
        *, memory_candidates: list[dict] | None = None,
    ) -> Result[TurnView]:
        """`select_speaker` → `draft_turn` → `commit_turn` 的组合。

        可选 `user_message`：先落盘用户消息，再选角色发言人生成回复（用户目标 2）。
        可选 `memory_candidates`：透传给 `commit_turn`（默认无 → 不写记忆）。
        """
        if user_message is not None and str(user_message).strip():
            user_turn = self.add_user_message(ctx, session_id, user_message)
            if is_err(user_turn):
                return user_turn
        speaker = self.select_speaker(ctx, session_id)
        if is_err(speaker):
            return speaker
        draft = self.draft_turn(
            ctx, session_id, speaker.value.speaker_entity_id,
            selection_reason=speaker.value.selection_reason)
        if is_err(draft):
            return draft
        return self.commit_turn(
            ctx, session_id, unwrap(draft), memory_candidates=memory_candidates)

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
        for pid in participants:
            view = self.deps.profiles.get_persona(ctx, pid)
            if is_err(view):
                continue
            referenced_persona.append(pid)
            traits = "; ".join(f"{i.text}(可信度{i.confidence})"
                               for i in view.value.traits[:5])
            persona_briefs.append(
                f"- 角色 {pid}（状态 {view.value.status}）："
                f"{view.value.background.text or '（无背景）'}"
                + (f" 关键特质：{traits}" if traits else ""))
        rel_briefs: list[str] = []
        for rid in _str_list(session_row.get("relationship_profile_ids")):
            view = self.deps.relations.get_relationship(ctx, rid)
            if is_err(view):
                continue
            referenced_relationship.append(rid)
            note = view.value.coverage.get("group_dynamics", {})
            rel_briefs.append(
                f"- 关系 {rid}（参与者 {view.value.participant_entity_ids}，"
                f"状态 {view.value.status}）"
                + (f"，群体互动覆盖：{note}" if note else ""))
            if _str_list(view.value.conflict_ids):
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
        })

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
        topic = session_row.get("topic")
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
        if topic and str(topic).strip():
            queries.append(MemoryQuery(
                text=str(topic),
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
        system = SIMULATION_NOTICE + "\n\n"
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
        )
