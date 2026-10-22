"""generic-persona-skill —— 通用人物与关系模拟工具。

当前阶段：**P6 二期完成（对话与记忆产品化，已验收）**——五维对话质量评估（P6.0 / P6.1）、
`persona chat` 与确定性规则记忆写回（P6.2）、`--topic` 跨会话检索（P6.3）。
阶段编号以 `docs/ROADMAP.md` 为准（用户口头编号的对应关系见 `docs/DECISIONS.md` §3 Q-37），
现状见 `docs/RUNNING.md` §5.44 二期总结。

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
**G-2（来源材料 → 候选 claim 自动抽取，待验收）**、
**P4 / P4.8（真实检索与正文抓取接入、模型 Provider 装配）**、
**P5 核心 + P5.1 CLI 收口（已验收）**、**P6 二期（对话与记忆产品化，已验收）**。

本包的产物路径已全链路接通：检索与正文抓取接真实服务（Tavily），
「来源材料 → 候选证据」的抽取接真实模型，对话生成接真实模型；
人格 / 关系档案则**由证据确定性装配**，不交给模型自由发挥：

- `persona.core`      —— 公共契约（枚举、ID、时间、错误码、Result 信封、调用上下文、通用 DTO）
- `persona.storage`   —— SQLite 最小初始化与读写（不做业务判断）
- `persona.providers` —— 外部服务统一接口 + 离线实现（`FakeProvider` 等，**全程离线**）
  + `TavilyProvider`（真实检索与正文抓取）+ `DeepSeekProvider` 等真实模型出口（需 API Key）
- `persona.config`    —— **唯一配置入口**（敏感配置只走环境变量，永不回显）
- `persona.orchestrator` —— 六态状态机、`TaskService`、预算闸门、Mock 执行器、
  `BusinessOrchestrator`（五阶段业务编排）、`model_setup`（模型 / 检索 Provider 装配）
- `persona.modules`   —— 业务模块端口协议；**`ResearchService` 已实现**（确定性检索计划/
  经 `Provider.search` 检索（产品路径为 Tavily）/经 `Provider.extract` 抓正文/
  过滤排序/经 `Evidence.register_source` 登记来源；
  **不写证据表**、不承担预算与检查点）；
  **`EvidenceService` 已实现**（来源/事件/证据的
  登记、查询、持久化、校验、追溯、去重、冲突记录、稳定性判定；只依赖 Storage）；
  **`PersonaService` 已实现**（档案创建/查询/版本化更新/证据关联与追溯/不确定性表达/
  冲突关联不裁决/虚构版本边界/模拟隔离；只依赖 Storage，档案内容由证据确定性装配）；
  **`RelationshipService` 已实现**（关系档案创建/查询/版本化更新/双向视角独立存储/
  共同经历与时间变化/≥3 人群体互动/RL-V6 证据直达/虚构版本反合并/模拟隔离；
  只依赖 Storage，档案内容由证据确定性装配）；
  **`DialogueService` 已实现**（会话与轮次管理/动态发言人选择/只读上下文引用/
  经 `Provider` 生成（产品路径接真实模型）/落盘/严格模拟隔离；
  **本会话记忆无条件注入上下文，跨会话记忆须显式给 `--topic` 才检索**；
  **仅在 `--memory-write rule` 时**按确定性规则写会话级模拟记忆，默认一条都不写）；
  **`MemoryService` 已实现**（记忆写入/持久化/关键词检索/去重/冲突记录/
  版本化更新不覆盖/过期/归档/删除与派生产物失效/对象与命名空间边界/模拟记忆隔离）；
  **`EvaluationService` 已实现**（只读评测：`check_invariants` 覆盖 INV-1~4、依赖分层检查、
  结构指标；**永不写业务表**）
- `persona.cli`       —— 命令行入口（启动、帮助、配置、任务生命周期；
  `run` / `chat` / `show` / `evidence` / `export` 等已接通，
  `new` / `sources` / `memory` / `eval` 仍是诚实的占位）

⚠️ **真实外部服务只用于三处**：检索与正文抓取（Tavily）、
「来源材料 → 候选证据」的抽取、对话生成。其余一律确定性，不交给模型。
`task run`（抽象工作单元）不调模型、不联网——那才是本包里"不是真实业务"的部分。

设计依据（先读文档，再看代码）：
    docs/DATA_MODEL.md   docs/INTERFACES.md   docs/ERROR_HANDLING.md   docs/DECISIONS.md
"""

__version__ = "0.1.0.dev0"

# 当前所处的开发阶段，供 CLI 的 `persona info` 与 `--help` 结尾展示
# 注意：阶段号**以仓库 ROADMAP 编号为准**（用户口头编号放括号里，见 DECISIONS §3 Q-37）
STAGE = ("P6 二期完成（对话与记忆产品化，已验收）："
         "真实检索（Tavily）与正文抓取、材料→候选证据的模型抽取（G-2）、"
         "`persona run` 五阶段、`persona chat` 对话、规则记忆写回、`--topic` 跨会话检索"
         "**均已接入并通过受控真跑验证**（二期真跑 12 次 / 0.0052962 USD，逐条对账全对）；"
         "人格 / 关系档案仍由证据**确定性装配**，不交给模型自由发挥；"
         "全量 1941 项测试全绿。仍为占位：`new` / `sources` / `memory` / `eval`"
         "（详见 docs/RUNNING.md §5.44）")
