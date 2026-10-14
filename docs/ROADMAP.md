# ROADMAP —— 开发路线图

- **版本**：v0.6（G-2：来源材料 → 候选 claim 的自动抽取）
- **状态**：待人工验收
- **已完成**：P0、P1.1、P1.2-A、P1.2-B、P2.1~P2.4、**P2 研究与证据管线（`Research` 最小可运行实现 + `Evidence` 本地管理基础）**、P3（`Persona` + `Relationship`）、P4（`Dialogue` + `Memory` + 对话↔记忆集成）、P5 核心（`Evaluation` + 端到端集成验收）；**当前停在 G-2（来源材料 → 候选 claim 自动抽取），等待人工验收**
- **相关文档**：`PROJECT_SPEC.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`DECISIONS.md`

---

## 0. 开发方式约定（给初学者的话）

- **小步开发**：一次只做一件事，做完立刻验证，验证通过才继续。
- **每个阶段都必须有"能看见的结果"**：不是"写了代码"，而是"跑了一条命令，看到了预期输出"。
- **每个阶段结束都要停下来自查**，不满足「进入下一阶段的条件」就不往下走。
- **P0–P5 全程不联网、不接真实 API**：全部使用固定素材和假实现，这样每次运行结果都一样，方便验证。

---

## 1. 阶段总览

| 阶段 | 名称 | 主要内容 | 是否动业务代码 |
| --- | --- | --- | --- |
| **P0** | 规格与架构设计 ✅ | 6 份设计文档 | ❌ 不动 |
| **P1.1** | 核心数据模型 ✅ | 13 个实体、字段、校验清单 | ❌ 只出文档 |
| **P1.2-A** | 接口契约与错误处理 ✅ | `INTERFACES.md`、`ERROR_HANDLING.md`，并同步四份旧文档 | ❌ 只出文档 |
| **P1.2-B** | 骨架与空壳 ✅ | 类型定义、接口签名、Mock Provider、空壳模块 | ⚠️ 只定义，**不写业务逻辑** |
| **P2.1** | 工程配置 ✅ | `.gitignore`、`pyproject.toml`、统一测试命令、临时库纪律 | ❌ 不动 |
| **P2.2** | 配置与密钥管理 ✅ | `persona/config`（统一入口、敏感配置只走环境变量）、`.env.example` | ❌ 不动 |
| **P2.3** | 任务执行与状态管理基础闭环 ✅ | `TaskRecord` / `TaskService` / 预算四段式闸门 / `TaskRunner`（Mock） | ⚠️ **只做任务闭环，不实现业务** |
| **P2** | 研究 + 证据管线 | `Research` + `Evidence` + `Storage` + `Provider`；**`Evidence` 本地管理基础与 `Research` 最小可运行实现均已实现待验收**（§4 进度记账） | ✅ |
| **P3** | 人物档案 + 关系档案 | `Persona` + `Relationship`；**两者本地管理基础均已实现待验收**（§5 进度记账） | ✅ |
| **P4** | 对话 + 记忆 | `Dialogue` + `Memory`；**两者及"对话↔记忆集成"均已实现待验收**（§6 进度记账） | ✅ |
| **P5** | 评测与加固（**当前**） | `Evaluation`、不变式断言、预算、恢复；**核心已实现待验收**（§7 进度记账） | ✅ |
| **P6+** | （未确定）真实 Provider 接入等 | 待讨论 | — |

---

## 2. P0 —— 规格与架构设计 ✅ 当前阶段

**目标**：把"要做什么"和"怎么搭"写清楚，取得一致。

| 项 | 内容 |
| --- | --- |
| **交付物** | `README.md`、`docs/PROJECT_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/DATA_MODEL.md`、`docs/ROADMAP.md`、`docs/DECISIONS.md` |
| **测试要求** | 文档交叉一致性检查（术语一致、编号一致、无矛盾表述） |
| **进入 P1 的条件** | ① 人工确认 `PROJECT_SPEC.md` 的需求范围；② `DECISIONS.md` 中 P1 相关待定问题（Q-04 存储选型、Q-13 实现语言）已给出方向；③ 无未解决的自相矛盾 |

---

## 3. P1.1 —— 核心数据模型 ✅ 已完成

| 项 | 内容 |
| --- | --- |
| **交付物** | `docs/DATA_MODEL.md` v0.2：13 个实体（`Project` / `PersonEntity` / `Source` / `SourceEvent` / `EvidenceItem` / `ConflictRecord` / `PersonaProfile` / `RelationshipProfile` / `ConversationSession` / `ConversationTurn` / `MemoryItem` / `TaskRecord` / `DerivedArtifact`）、Mermaid ER 图、四类隔离示例、40 项校验清单 |
| **测试要求** | 文档级：术语一致、编号一致、与架构无矛盾 |
| **结果** | ✅ 已完成并通过交叉一致性检查 |

---

## 3A. P1.2-A —— 接口契约与错误处理 ✅ 已完成（本阶段）

**目标**：把每个模块"能干什么、不能干什么、失败时返回什么"定死，让后续实现和测试有唯一依据。

| 项 | 内容 |
| --- | --- |
| **交付物** | ① `docs/INTERFACES.md`：11 个模块的职责/非职责、允许调用、操作级输入输出与失败原因、DB/API/预算副作用、隔离约束；公共 `Result` 信封与 DTO 规则；Provider 契约与 Mock 家族；六态任务机；Mermaid 依赖图 + 12 条禁止依赖<br>② `docs/ERROR_HANDLING.md`：错误分类与错误码表、重试规则、预算四段式闸门、六态转换表、检查点与 resume 算法、隔离违规处置、测试要求<br>③ 同步更新 `ARCHITECTURE.md`（v0.2）、`DATA_MODEL.md`（v0.2.1）、`DECISIONS.md`（v0.2）、`README.md` |
| **测试要求** | 静态一致性检查 8 项（见本报告 §6）：接口↔数据模型一致、输入输出明确、状态与错误处理一致、无循环依赖、预算暂停恢复可执行、五类内容可区分、**未引入强制人工审核**、无特定人物专属逻辑 |
| **验收结果** | 见交付报告，问题清单已列明 |
| **进入 P1.2-B 的条件** | ① 人工批准 `DECISIONS.md` §2.8 的 D-040 ~ D-061；② 静态一致性检查中**阻塞项清零**；③ Q-27（是否引入 ORM/Pydantic）给出方向 → ✅ **已拍板：否（D-073）** |

---

## 3B. P1.2-B —— 骨架与空壳 ✅ 已完成

**目标**：把类型与接口落成可运行的代码骨架。**仍然禁止写业务逻辑**。

| 项 | 内容 |
| --- | --- |
| **交付物** | ① 数据结构定义（`dataclasses`，对应 `DATA_MODEL.md`）；② 模块接口签名（对应 `INTERFACES.md`，实现抛 `NotImplementedError`）；③ `FakeProvider` / `RecordingProvider` / `FailingProvider` / `OfflineGuardProvider`；④ `fixtures/` 合成测试素材（**虚构对象**，至少 2 人与 3 人两组，含一个虚构角色变体组）；⑤ `run_static_checks` 依赖与签名检查脚本 |
| **测试要求** | ① 数据结构可创建、可校验（如 `EvidenceItem.namespace` 不能是 `SIMULATED`）；② 依赖方向检查脚本通过；③ `FakeProvider` 离线可跑通全流程空壳；④ `OFFLINE` 断言：测试期间无网络调用 |
| **进入 P2 的条件** | ① 依赖检查脚本自动报告违规；② 测试素材不含任何真实人物；③ 所有接口签名已人工过一遍 |

> **为什么先做这一步**：先把"插头"定好，后面每个模块就可以独立开发、独立测试，不会互相卡住。

---

## 3C. P2.1 ~ P2.3 —— 工程、配置与任务闭环 ✅ 已验收

| 阶段 | 交付物 | 状态 |
| --- | --- | --- |
| **P2.1** 工程与运行环境 | `.gitignore`、`pyproject.toml`、统一测试命令（D-069）、临时库纪律（D-070）；实测 3.12 / 3.13 / 3.14 三个解释器 | ✅ |
| **P2.2** 配置与密钥 | `persona/config`（统一入口 D-077、敏感配置只走环境变量、错误不含密钥值）、`.env.example`、`docs/CONFIG.md`、`config show/check` | ✅ |
| **P2.3** 任务闭环 | `TaskRecord` / `TaskService`（创建·查询·六态转换·持久化）/ `BudgetGate`（四段式）/ `TaskRunner` + `MockUnitHandler` / `sweep_interrupted` / CLI `task` 命令组 | ✅ |

**P2.3 的测试要求**（已落地）：① 六态合法与非法转换，非法不改状态；② 暂停 ≠ 取消 ≠ 失败；
③ 预算不足 ⇒ 暂停 + 落检查点（不是失败）；④ 检查点可在"重启"后读回（含**真实子进程**验证）；
⑤ 重复提交/重复执行不重复干活；⑥ 可重试错误按上限重试，不可重试立即失败。

**P2.4 故障注入测试**（已落地，`tests/test_task_faults.py`）：只补 P2.3 未覆盖的故障场景——
① 检查点保存失败；② 数据库操作失败；③ 处理器抛异常；④ 重复提交；⑤ 重复恢复。
已覆盖场景不重复测试。诚实记录的可靠性边界见 `docs/RUNNING.md` §5.3。

**进入 P2（研究与证据管线）的条件**：
① 本阶段人工验收通过（✅ 已通过，T-1~T-7 已拍板 → D-095~D-101）；② 按 D-066 补齐 `fixtures/` 合成素材与 `run_static_checks` 脚本；
③ 拍板 `docs/RUNNING.md` §5.2 的 T-1 ~ T-7（✅ 已全部拍板）。

---

## 4. P2 —— 研究与证据管线

**目标**：能把"一批固定素材"变成"去重、分级、带冲突记录的证据集合"。

| 项 | 内容 |
| --- | --- |
| **交付物** | `Storage`（可持久化）、`Provider`（假实现）、`Research`（检索计划 + 来源登记）、`Evidence`（提取 + 分级 + 去重 + 冲突 + 稳定性判定） |
| **测试要求** | ① 同一来源重复出现只登记 1 条（AC-4）；② 每条证据都有 `tier`（AC-6）；③ `INFERENCE` 必带 `derived_from`，无根推断被拒绝；④ 冲突发生时两条说法都在，任务不中断（AC-5）；⑤ `stability=SINGLE` 的证据不会进入 `traits`（AC-7）；⑥ 任一条证据可反查来源（AC-3） |
| **进入 P3 的条件** | 上述 6 项全部通过；且用**另一组不同的测试素材**重复跑，结果依然正确（证明通用化，AC-18） |

**进度记账（只记账，不改编号）**：

- **P2 第一步（2026-10-13，待人工验收）**：`Evidence` 本地管理基础——`persona/modules/evidence.py` + `tests/test_evidence_module.py`（89 项，全量 276 项通过）。用户口头称这一步为"P3.1"，与本节编号的差异已记录为 `DECISIONS.md` §3 **Q-37**（按仓库编号实施，未改路线）。已覆盖：来源登记/去重/追溯、来源事件、证据分级与三条评分分离、冲突记录不裁决、去重五元组、模拟隔离双层闸门、稳定性只读判定、重启持久化。**未覆盖**：`Storage` 的 13 张表迁移、AI 辅助切分与语义冲突检测。
- **P2 第二步 · `Research` 最小可运行实现（2026-10-14，待人工验收）**：`persona/modules/research.py` 由 Protocol-only 推进为 **`ResearchService`**——`plan_queries`（确定性模板，Q-29）→ `collect`（经 `Provider.search`，0 结果为业务结果而非错误）→ `filter_and_rank`（无效材料带原因拒绝 / 批内去重 / 确定性结构排序 / 可选对象边界）→ `register`（**只经 `Evidence.register_source`**，失败整体传播）→ `run`（聚合计数与 `warnings`）。新增 `SourceRegistrarPort`（**仅 `register_source`**，结构性堵死 F-10）与 `tests/test_research_module.py`（48 项）。已覆盖：来源可追溯（产出可被 `Evidence.query_sources`/`get_source` 直接消费）、模拟内容隔离（不产 `EvidenceItem`、`Source` 恒 `PUBLIC_SOURCE`、存储闸门仍生效）、真离线（`OfflineGuardProvider` + `network_blocked`）、重复调用幂等、Provider 失败/空结果/无效材料/部分失败/全失败上报/BUDGET_EXCEEDED 立即传播。**未覆盖**：真实检索接入（依赖 Q-01/Q-02/Q-03）、正文抓取与切分（`content_ref`）、检索结果语义排序、真实来源合规名单。用户口头称这一步为"H-11A"，编号差异同 Q-37（第八轮）/ §2.23 D-191。详见 `RUNNING.md` §5.11、`INTERFACES.md` §5.3。
- **H-10A 业务编排型 Orchestrator（2026-10-14，待人工验收）**：新增 `persona/orchestrator/business.py`（`BusinessOrchestrator`），实现 `executor.UnitHandler` 接入**既有** `TaskRunner`，打通 Research → Evidence → Persona → Relationship → Dialogue；既有 `TaskService` / `TaskRunner` /`BudgetGate` / 检查点**一行未改**。新增 `tests/test_business_orchestrator.py`（37 项，全量 648 项通过）。**AC-1 / AC-2 仍为部分满足**：一条命令能跑完五步，但证据 claim 需调用方显式提供（缺口 G-2：源→证据自动抽取未实现），群体互动维度存在但未自动填充内容（G-4）。用户口头称这一步为「H-10A」，编号差异同 Q-37（第九轮）/ §2.24 D-206。详见 `RUNNING.md` §5.12、`INTERFACES.md` §12.7。
- **G-2 来源材料 → 候选 claim 自动抽取（2026-10-14，待人工验收）**：新增 `persona/modules/extraction.py`（`ClaimExtractorPort` + `MockClaimExtractor` 确定性离线 + `ProviderClaimExtractor` 只定义契约不接真实模型）；`Research` 追加 `materials` 输出（此前 `snippet` 被丢弃是核心数据缺口）；`SourceMaterial` 下沉 `core/types.py`；`BusinessDeps` 新增可注入 `claim_extractor`，`evidence_seeds` 变可选——**业务入口不再要求调用方提供 claim**。新增 `tests/test_claim_extraction.py`（28 项）+ 业务编排器 G-2 用例（18 项），全量 611 → 648 → **694** 项通过。**AC-1 由「部分满足」转为已满足**（但基于 Mock 链路，不等于真实网络研究已通过）；**AC-2 仍为部分满足**（群体互动维度存在但未自动填充，G-4）。新登记缺口 G-6（研究材料无法落检查点）。H-17（正文抓取）仍开。注意仓库有**两个** G-2：本条是 §5.12（H-10A）遗留的「源→证据抽取」，与 §5.8（P3.3）遗留的同名「群体子结构关系」无关，见 Q-37 第十轮 / §2.25 D-221。详见 `RUNNING.md` §5.13、`INTERFACES.md` §5.4。
- 下一步候选：P2 测试素材泛化验证（AC-18），或真实 `Provider` 接入决策（Q-01/Q-02/Q-03），或补齐 H-17（正文抓取与切分）。

---

## 5. P3 —— 人物档案与关系档案

**目标**：能把证据集合变成结构化的 `Persona` 和 `Relationship`。

| 项 | 内容 |
| --- | --- |
| **交付物** | `Persona`（背景/性格/习惯/对话风格/经历/关系）、`Relationship`（共同经历/互动模式/情绪表达/双方视角/时间变化/群体互动） |
| **测试要求** | ① 档案每个非空字段都有 `evidence_refs`（AC-3）；② 无证据支撑的字段留空并写入 `open_questions`（不靠想象补全）；③ `traits` 与 `single_events` 严格分离（AC-7）；④ 3 人素材下 `group_dynamics` 必填，2 人下为 `N/A`；⑤ 双方视角字段独立存储，未被合并（对应 D-018）；⑥ 冲突主题出现在 `controversial_topics` 中 |
| **进入 P4 的条件** | 上述全部通过；人工抽查至少 2 份档案，确认"读起来像人话"且可追溯 |

**进度记账（只记账，不改编号）**：

- **P3 第一步（2026-10-14，待人工验收）**：`Persona` 本地管理基础——`persona/modules/persona.py` + `tests/test_persona_module.py`（71 项，全量 347 项通过）。用户口头称这一步为"P3.2"，与仓库 P3 未细分编号的差异已并入 `DECISIONS.md` §3 **Q-37**（按仓库编号实施，未改路线）。已覆盖：档案创建/查询/版本化更新/持久化、证据关联与追溯（只收 EvidenceItem）、不确定性表达（uncertainty_note / open_questions / status）、冲突关联不裁决（CF-V5 双向回写）、虚构版本边界（PF-V5 / PE-V3）、模拟隔离（PF-V6 双层闸门）、稳定性闸门（PF-V2 / D-012）、字段级 diff、重启持久化。**未覆盖**：`distill` 的 AI 蒸馏主体（需 `Provider.chat`）、`Relationship` 模块、`group_dynamics`（属 Relationship）、Q-19 版本裁剪。
- **P3 第二步（2026-10-14，待人工验收）**：`Relationship` 本地管理基础——`persona/modules/relationship.py` + `tests/test_relationship_module.py`（100 项，全量 447 项通过）。用户口头称这一步为"P3.3"，编号差异同样并入 Q-37。已覆盖：关系档案创建/查询/版本化更新/持久化、**双向视角独立存储不合并**（RL-V1/RL-V5）、共同经历与时间变化（含逐条目参与者）、**≥3 人群体档案不拆两人档**（group_dynamics 必填，D-121/D-122）、RL-V6 证据直达（不得凭两份人格档案拼关系）、冲突关联不裁决（CF-V5）、虚构版本反合并（同角色双版本不得同时作为参与者，D-127）、模拟隔离双层闸门、RL-V2 亲密度双重扫描、模式闸门（单次互动不进 interaction_patterns，D-125）、字段级 diff、重启持久化。**未覆盖**：`distill` 的 AI 蒸馏主体（需 `Provider.chat`）、群体子结构关系（缺口 G-2）、Q-19 版本裁剪。
- 下一步候选：`Research` 模块（依赖 Q-01 / Q-02 / Q-03 拍板）、P2 测试素材泛化验证（AC-18），或两个 `distill` 的 AI 蒸馏主体（依赖 Provider 接入决策）。

---

## 6. P4 —— 对话与记忆

**目标**：能跑起多人多轮对话，并跨会话记住发生过什么。

| 项 | 内容 |
| --- | --- |
| **交付物** | `Dialogue`（动态发言人选择 + 生成 + 落盘）、`Memory`（保存/检索/更新/删除） |
| **测试要求** | ① 每轮都记录了 `speaker` 与 `selection_reason`（AC-8）；② 3 人对话中发言者会被动态切换（不是固定轮流）；③ 重启进程后仍能检索到之前的记忆（AC-9）；④ 删除某条记忆后，后续对话不再引用它（AC-10）；⑤ 记忆检索结果**原样带出** `namespace=SIMULATED` 标记；⑥ 不变式断言：对话跑完后 `Evidence` 表零变化（AC-13） |
| **进入 P5 的条件** | 上述全部通过；且人工确认对话输出中不会出现"把模拟内容当真事"的表述 |

**进度记账（只记账，不改编号）**：

- **P4 第一步（Dialogue 基础）已实现，待人工验收（2026-10-14）**：`DialogueService` 支持会话/轮次管理、动态发言人选择（`least_recently_spoken` + 每轮 `selection_reason`）、只读上下文组装、经 Mock Provider 生成、落盘（namespace 恒 `SIMULATED`）、严格模拟隔离；覆盖 AC-8（选择可解释）、②（3 人动态切换非固定轮流）、⑥（对话后 `Evidence` 表零变化，AC-13）。长期记忆（AC-9/AC-10）属 P4 第二步，本轮未做。详见 `DECISIONS.md` §2.20（D-135~D-145）、`RUNNING.md` §5.7。
- **P4 第二步（Memory 基础）已实现，待人工验收（2026-10-14）**：`MemoryService` 支持记忆的写入、关键词检索（`text` + `scope` / `namespace` / `status` / 时间范围过滤）、去重（`MEMORY_DEDUP_HIT` 返回既有条目而非新建）、冲突记录（`link_conflict` 双向登记、**不自动裁定**）、版本化更新（`supersede` 新条目作为新版本、旧条目变 `SUPERSEDED` **不静默覆盖**）、过期（写入即判 + `expire_sweep` 扫描）、归档、删除（含派生产物 `DerivedArtifact` 级联失效）；对象/命名空间边界与模拟记忆隔离均有闸门。已覆盖 AC-9（重启进程后可检索到先前记忆）、AC-10（删除后不再被检索引用）⑤（检索结果原样带出 `namespace=SIMULATED` 标记）。**未做**：对话每轮自动写长期记忆（目标 8，无明确规则不自动写）、向量检索、跨项目记忆合并。详见 `DECISIONS.md` §2.21、`RUNNING.md` §5.8。
- **P4 第三步·对话↔记忆集成 已实现，待人工验收（2026-10-14）**：打通「对话 → 上下文记忆检索 → 记忆写入 → 后续会话再检索」闭环。`draft_turn` 按 R1 本会话 / R2 跨会话（含 topic 关键词门槛）检索**仅 `SIMULATED` + `ACTIVE`** 的记忆并注入 prompt（检索结果原样带出 `namespace` 标记）；`commit_turn(..., memory_candidates=[...])` 在**同一事务**内 `Turn → Memory → Audit` 写入模拟记忆并回填 `generated_memory_ids`，失败整体回滚；**默认 `memory_write_policy="none"` 绝不自动写长期记忆**，候选 `scope` 必须 `SESSION`（不得升级为稳定人格特征）。**契约缺口已按最小改动补齐**：新增只读 `MemoryReaderPort`（仅 `search`），不改 `MemoryWriterPort`。已覆盖 ROADMAP 测试要求 ③（跨会话检索到先前记忆，含重开库文件）、④（删除后不再被引用）、⑤（原样带出 `SIMULATED`）、⑥（`Evidence` 表零变化，AC-13）。**未做**：`end_session → Memory.consolidate`（§8.2 标可选，scope 映射未定义）、真实模型 / 真实检索 / 向量检索。详见 `DECISIONS.md` §2.22（D-174~D-184）、`RUNNING.md` §5.9。

---

## 7. P5 —— 评测与加固

**目标**：让质量可度量，让系统在异常情况下不崩、不乱花钱、能续跑。

| 项 | 内容 |
| --- | --- |
| **交付物** | `Evaluation`（单元测试 / 集成测试 / 质量评分 / 不变式断言 / 依赖检查）、预算控制、检查点与恢复、`CLI` 完善 |
| **测试要求** | ① `check_invariants` 覆盖 INV-1 ~ INV-4 且全部通过（AC-13、AC-14、AC-15）；② 预算耗尽时任务暂停并提示，不静默继续（AC-12）；③ 中途中断后可 `resume`，已完成阶段不重复执行（AC-11）；④ `check_dependencies` 通过，无循环依赖（AC-20）；⑤ 全流程离线可跑（AC-19）；⑥ 全仓库检索无真实人物硬编码（AC-16） |
| **完成条件** | 上述全部通过 + `PROJECT_SPEC.md` §7 的 A/B/C/D 四组验收标准逐条核对通过 |

**进度记账（只记账，不改编号）**：

- **P5 核心（Evaluation + 端到端集成验收）已实现，待人工验收（2026-10-14，用户称 P3.7，编号差异见 `DECISIONS.md` §3 Q-37 第七轮 / §2.22.1 D-190）**：`persona/modules/evaluation.py` 扩为 **`EvaluationService`**——`check_invariants` 覆盖 **INV-1~INV-4**（`dialogue_does_not_write_evidence` / `simulated_not_in_evidence` / `memory_cannot_become_evidence` / `conflict_not_resolved` / `no_intimacy_field` / `variants_not_merged` / `no_person_specific_literals`，服务 AC-13/14/15）、`check_dependencies`（**AC-20**）、`run_static_checks`、`snapshot`/`dialogue_leaks`、`measure_quality`（结构指标，不做主观打分）。新增 `tests/test_integration_acceptance.py`（26 项）验证 **只读断言式端到端链**：任务创建/执行 → 证据/档案读写 → 对话生成 → 记忆按规则写入 → 评测；并验证 AC-11（`resume` 不重复）、AC-12（预算暂停非失败）、AC-19（全程离线）。**未做/未打通**：`Evaluation.run_unit_tests`/`run_integration_tests` 接线。注：`Research` 模块已于 H-11A 交付、业务编排型 Orchestrator 已于 **H-10A** 交付，AC-1 已因 **G-2（源→证据自动抽取）** 交付而由「部分满足」转为**已满足（Mock 链路）**；AC-2 仍因群体互动内容未自动填充（G-4）为部分满足。详见 `RUNNING.md` §5.10、`DECISIONS.md` §2.22.1。

---

## 8. P6 及以后（未确定，不要提前做）

以下事项**尚未决定，本阶段不承诺、不排期**：

- 接入真实搜索 API 与模型 API
- 网页界面
- 多语言支持范围
- 记忆向量化检索
- 更多人物 / 更大规模关系网络

见 `DECISIONS.md` §3 待定问题。

---

## 9. 每个阶段的通用测试要求（贯穿全程）

每一阶段结束时，都必须能回答下面 5 个问题：

| # | 问题 | 不满足时的处理 |
| --- | --- | --- |
| 1 | 这一阶段交付的东西**能跑起来**吗？ | 停下修好 |
| 2 | 有**自动化测试**守着吗？新增的功能有没有测试？ | 补测试 |
| 3 | 换一组**不同的测试素材**，结果还对吗？ | 说明存在硬编码，必须修 |
| 4 | **断网**的情况下还能跑吗？ | 说明存在隐藏的外部依赖，必须修 |
| 5 | 之前的测试**全都还是绿的**吗？ | 停下修好，不许带着红灯往下走 |

---

## 10. 阶段与验收标准的对应关系

| 验收标准 | 由哪个阶段验证 |
| --- | --- |
| AC-1 ~ AC-2（全流程跑通） | P5（依赖 P2–P4）+ **H-10A 业务编排器** |
| AC-3（可追溯） | P2 起逐步验证，P5 汇总 |
| AC-4（来源去重） | P2 |
| AC-5（冲突保留且不阻断） | P2 |
| AC-6（证据分级） | P2 |
| AC-7（单次行为不算性格） | P2 + P3 |
| AC-8（发言人选择可解释） | P4 |
| AC-9 ~ AC-10（记忆持久化与删除） | P4 |
| AC-11（任务恢复） | P5 |
| AC-12（预算控制） | P5 |
| AC-13 ~ AC-15（真实性与冲突不变式） | P4 起，P5 汇总 |
| AC-16 ~ AC-18（通用化） | P1 起，P5 汇总 |
| AC-19（离线可跑） | P1 起，P5 汇总 |
| AC-20 ~ AC-21（依赖方向与术语一致） | P1 起，P5 汇总 |
