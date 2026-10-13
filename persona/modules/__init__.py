"""业务模块的**端口协议**（L3/L4）。

⚠️ 本阶段（P1.2-B）**只声明接口，不写任何实现**。
每个文件里的 `Protocol` 就是"插头图纸"：P2~P5 照着它填实现即可。

依赖方向（INTERFACES §13）提醒：
    - 同层业务模块**互不直接调用**（唯一例外：Relationship 可**只读** Persona）
    - 下层（本包）**永不调用** Orchestrator / CLI / Evaluation
    - 任何模块都不许 import 具体厂商 SDK（D-054）
"""

from . import dialogue, evidence, evaluation, memory, persona, relationship, research

__all__ = [
    "research",
    "evidence",
    "persona",
    "relationship",
    "dialogue",
    "memory",
    "evaluation",
]
