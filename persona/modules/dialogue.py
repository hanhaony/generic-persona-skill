"""Dialogue 端口 —— 多人多轮对话（docs/INTERFACES.md §8）。

**职责**：会话管理、动态发言人选择（**必须记录理由**，D-033）、生成发言、落盘轮次。
**非职责**：⛔ 对 `Evidence` / `Persona` / `Relationship` **只可读不可写**（INV-1）；
          ⛔ 自己不写记忆表——只把"待记忆内容"交给 `Memory`（且只能是 `SIMULATED`）。

回写红线（D-050、D-057）：
    模拟内容**不得**变成真人真实发言、真人真实经历、作品正式剧情或官方设定。
    技术上靠"只读端口协议"堵死：本模块拿到的 `EvidenceReader` 里**没有写方法**。
"""

from __future__ import annotations

from typing import Protocol

from ..core.context import CallContext
from ..core.result import Result
from ..core.types import Ref


class MemoryWriterPort(Protocol):
    """Dialogue 能拿到的记忆写入端口：**只允许写 `SIMULATED`**。"""

    def write_simulated(self, ctx: CallContext, draft: dict) -> Result[Ref]:
        ...


class DialoguePort(Protocol):
    def start_session(
        self, ctx: CallContext, participant_entity_ids: list[str], topic: str | None = None
    ) -> Result[Ref]:
        """开启会话。会话与轮次的 `namespace` **固定为 `SIMULATED`**，不可传参覆盖。"""
        ...

    def select_speaker(self, ctx: CallContext, session_id: str) -> Result[dict]:
        """动态选择发言人，返回 `{"speaker_id": ..., "selection_reason": ...}`，理由必填。"""
        ...

    def generate_utterance(self, ctx: CallContext, session_id: str, speaker_id: str) -> Result[dict]:
        """生成一句发言（经 `Provider.chat`）。"""
        ...

    def commit_turn(self, ctx: CallContext, session_id: str, draft: dict) -> Result[Ref]:
        """落盘一轮。幂等键 = `(session_id, index)`。**先落轮次，再写记忆**。"""
        ...

    def run_round(self, ctx: CallContext, session_id: str) -> Result[Ref]:
        ...
