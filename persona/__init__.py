"""generic-persona-skill —— 通用人物与关系模拟工具。

当前阶段：**G-2 —— 来源材料 → 候选 claim 的自动抽取**（用户称 G-2；仓库 `RUNNING.md`
§5.12 里 H-10A 遗留清单中的 G-2「源→证据自动抽取未实现」——**注意与 §5.8 里 P3.3 遗留的
同名 G-2（群体子结构关系，D-131 已接受）无关**；编号差异见
`docs/DECISIONS.md` §3 Q-37 第十轮 / §2.25 D-221；待人工验收）。

已完成的阶段：P0 规格与架构、P1.1 数据模型、P1.2-A 接口契约、P1.2-B 代码骨架、
P2.1 工程配置、P2.2 配置管理、**P2.3 任务生命周期基础闭环（已验收）**、
**P2.4 故障注入测试（已验收）**、**P2 证据管线第一步（Evidence 本地管理基础，待验收）**、
**P3 第一步（Persona 本地管理基础，待验收）**、
**P3 第二步（Relationship 本地管理基础，待验收）**、
**P3 第四步（Dialogue 本地生成基础，待验收）**、
**P3 第五步（Memory 长期记忆基础，待验收）**、
**P3 第六步（Dialogue ↔ Memory 集成，待验收）**、
**P3 第七步（Evaluation 核心 + 端到端集成验收，待验收）**、
**H-11A（Research 最小可运行实现，待验收）**、
**H-10A（业务编排型 Orchestrator，待验收）**、
**G-2（来源材料 → 候选 claim 自动抽取，待验收）**。

本包**有骨架、任务闭环、研究检索基础、证据管理基础、人格档案基础、关系档案基础、
对话生成基础、长期记忆基础、对话↔记忆集成与只读评测，但没有真实检索/蒸馏业务**：

- `persona.core`      —— 公共契约（枚举、ID、时间、错误码、Result 信封、调用上下文、通用 DTO）
- `persona.storage`   —— SQLite 最小初始化与读写（不做业务判断）
- `persona.providers` —— 外部服务统一接口 + 四个 Mock 实现（**全程离线**）
- `persona.config`    —— **唯一配置入口**（敏感配置只走环境变量，永不回显）
- `persona.orchestrator` —— 六态状态机、`TaskService`、预算闸门、**Mock 执行器**
- `persona.modules`   —— 业务模块端口协议；**`ResearchService` 已实现**（确定性检索计划/
  经 `Provider.search` 检索/过滤排序/经 `Evidence.register_source` 登记来源；
  **不写证据表**、不承担预算与检查点、不接真实搜索 API）；
  **`EvidenceService` 已实现**（来源/事件/证据的
  登记、查询、持久化、校验、追溯、去重、冲突记录、稳定性判定；只依赖 Storage）；
  **`PersonaService` 已实现**（档案创建/查询/版本化更新/证据关联与追溯/不确定性表达/
  冲突关联不裁决/虚构版本边界/模拟隔离；只依赖 Storage，`distill` 的 AI 蒸馏主体未实现）；
  **`RelationshipService` 已实现**（关系档案创建/查询/版本化更新/双向视角独立存储/
  共同经历与时间变化/≥3 人群体互动/RL-V6 证据直达/虚构版本反合并/模拟隔离；
  只依赖 Storage，`distill` 的 AI 蒸馏主体未实现）；
  **`DialogueService` 已实现**（会话与轮次管理/动态发言人选择/只读上下文引用/
  经 Mock Provider 生成/落盘/严格模拟隔离；**按规则检索模拟记忆注入上下文**；
  **仅在显式授权 + 显式候选时**写会话级模拟记忆，默认不写长期记忆）；
  **`MemoryService` 已实现**（记忆写入/持久化/关键词检索/去重/冲突记录/
  版本化更新不覆盖/过期/归档/删除与派生产物失效/对象与命名空间边界/模拟记忆隔离）；
  **`EvaluationService` 已实现**（只读评测：`check_invariants` 覆盖 INV-1~4、依赖分层检查、
  结构指标；**永不写业务表**）
- `persona.cli`       —— 命令行入口（启动、帮助、配置、任务生命周期；业务命令仍是占位）

⚠️ 真实检索接入、AI 人格/关系蒸馏等**真实业务尚未实现**：
`task run` 推进的是抽象工作单元，不调模型、不联网、不做人物研究。

设计依据（先读文档，再看代码）：
    docs/DATA_MODEL.md   docs/INTERFACES.md   docs/ERROR_HANDLING.md   docs/DECISIONS.md
"""

__version__ = "0.1.0.dev0"

# 当前骨架所处的开发阶段，供 CLI 展示
# 注意：阶段号**以仓库 ROADMAP 编号为准**（用户口头编号放括号里，见 DECISIONS §3 Q-37）
STAGE = ("P5 之后（G-2 来源材料 → 候选 claim 自动抽取：新增 modules/extraction.py 的"
         " ClaimExtractorPort（Mock 查表 / Provider 契约，不接真实模型）；Research 追加"
         " materials 输出；BusinessOrchestrator 不再要求调用方提供 claim，走 Research →"
         " 抽取 → Evidence 登记；三种 claim 机械区分且推断无根即丢弃；"
         "仍为 Mock 链路，真实检索与正文抓取未接，待人工验收）")
