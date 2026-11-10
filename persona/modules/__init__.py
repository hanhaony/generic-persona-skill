"""业务模块端口协议与实现（L3/L4）。

每个文件里既有 `Protocol`（"插头图纸"），也有已落地的实现类：
    `ResearchService` / `EvidenceService` / `PersonaService` / `RelationshipService` /
    `DialogueService` / `MemoryService` / `EvaluationService`。
`research` / `evaluation` 曾长期只有 Protocol，现已推进为可运行实现
（Research：H-11A；Evaluation：P5 核心）。

**G-2 追加** `extraction`：把研究材料转成**候选** claim 的 `ClaimExtractorPort`
（`MockClaimExtractor` 确定性离线 / `ProviderClaimExtractor` 只定义契约，
本阶段不接真实模型）。它**拿不到** `Evidence`——候选能否成为证据由 Evidence 说了算。

依赖方向（INTERFACES §13）提醒：
    - 同层业务模块**互不直接调用**（唯一例外：Relationship 可**只读** Persona；
      Dialogue 可**只读** 档案/证据并按规则写模拟记忆；Research 只**登记**来源）
    - 下层（本包）**永不调用** Orchestrator / CLI / Evaluation
    - 任何模块都不许 import 具体厂商 SDK（D-054）
"""

from . import (
    character,
    compliance,
    dialogue,
    evidence,
    evaluation,
    extraction,
    memory,
    persona,
    relationship,
    research,
)

__all__ = [
    "research",
    "extraction",
    "evidence",
    "persona",
    "relationship",
    "character",
    "dialogue",
    "memory",
    "evaluation",
    "compliance",
]
