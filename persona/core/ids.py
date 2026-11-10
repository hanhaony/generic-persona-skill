"""ID 生成与校验 —— 对应 docs/DATA_MODEL.md §2.1、INTERFACES.md §1.1。

三条规则：
    1. `uuid4().hex`（32 个十六进制字符）+ 业务前缀，**永不改变**；
    2. 禁止顺序自增整数（不可猜测）；
    3. 长度固定为 `len(前缀) + 32`。

给初学者的解释：
    所有对象（项目、人物、来源、证据……）都靠 ID 串起来。模块之间**只传 ID**，
    不传整包数据，这样任何一步中断后都能从数据库里接着干。
"""

from __future__ import annotations

import uuid

# 前缀表与 docs/DATA_MODEL.md §2.1 一致
ID_PREFIXES: dict[str, str] = {
    "Project": "prj_",
    "PersonEntity": "pe_",
    "Source": "src_",
    "SourceEvent": "sev_",
    "EvidenceItem": "evd_",
    "ConflictRecord": "cfl_",
    "PersonaProfile": "pnf_",
    "RelationshipProfile": "rlp_",
    "ConversationSession": "css_",
    "ConversationTurn": "ctn_",
    "MemoryItem": "mem_",
    "TaskRecord": "tsk_",
    "DerivedArtifact": "dar_",
    # P8.1-B：故事人物实例（第 14 个实体）。同一 PersonaProfile 可在多个故事中
    # 各有一个独立实例；实例状态全部在 payload（records 表文档式承载）。
    "CharacterInstance": "chi_",
}

# 支撑记录（用量、审计）不属于 13 个业务实体，但也用同样的规则
AUX_PREFIXES: dict[str, str] = {
    "UsageRecord": "usg_",
    "AuditRecord": "adt_",
    # P8.1-B：故事标识符与知识条目 ID。
    # - `Story`（sty_）是**引用型标识符**：把多个 CharacterInstance 归到同一场
    #   故事。它不是业务实体（不进 ENTITY_KINDS、不建表），由调用方生成并
    #   在创建实例时携带；与 ConversationSession 的桥接属 P8.3 接线决策。
    # - `KnowledgeEntry`（kn_）内嵌在实例 payload 里，但必须全局唯一
    #   （跨实例引用如 Belief.formed_from 可能指向他处）。
    "Story": "sty_",
    "KnowledgeEntry": "kn_",
}

ALL_PREFIXES: dict[str, str] = {**ID_PREFIXES, **AUX_PREFIXES}


def new_id(kind: str) -> str:
    """按实体种类生成一个新 ID。例：`new_id("EvidenceItem") -> "evd_88af…"`。"""
    try:
        prefix = ALL_PREFIXES[kind]
    except KeyError:
        raise KeyError(f"未知的实体种类：{kind!r}；已知种类：{sorted(ALL_PREFIXES)}") from None
    return prefix + uuid.uuid4().hex


def new_token(prefix: str = "tok_") -> str:
    """生成一个一次性的短令牌（如 resume_token）。"""
    return prefix + uuid.uuid4().hex


def is_valid_id(kind: str, value: str) -> bool:
    """校验 ID 是否属于该实体种类：前缀正确 + 总长度正确 + 后半段是十六进制。"""
    prefix = ALL_PREFIXES.get(kind)
    if prefix is None or not isinstance(value, str):
        return False
    if not value.startswith(prefix):
        return False
    body = value[len(prefix) :]
    if len(body) != 32:
        return False
    try:
        int(body, 16)
    except ValueError:
        return False
    return True


def prefix_of(value: str) -> str | None:
    """从 ID 反查它属于哪种实体；无法识别返回 `None`。"""
    for kind, prefix in ALL_PREFIXES.items():
        if value.startswith(prefix):
            return kind
    return None
