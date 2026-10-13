# ARCHITECTURE —— 系统架构设计

- **版本**：v0.2（P1.2-A 阶段同步版）
- **状态**：待人工验收
- **相关文档**：`PROJECT_SPEC.md`、`DATA_MODEL.md`、`INTERFACES.md`、`ERROR_HANDLING.md`、`ROADMAP.md`、`DECISIONS.md`
- **技术选型**：Python 3.12+ / 本地 CLI / 模块化单体 / SQLite / 第一版关键词检索（`DECISIONS.md` D-040~D-043）
- **说明**：本文讲**模块怎么切、谁调用谁、数据怎么流**；每个模块的**正式接口契约见 `INTERFACES.md`**，错误与恢复规则见 `ERROR_HANDLING.md`。三份文档冲突时以 `INTERFACES.md` 为准。

---

## 1. 架构设计原则

| 编号 | 原则 | 为什么重要 |
| --- | --- | --- |
| P-1 | **单向依赖**：只允许上层调用下层，下层永远不知道上层存在 | 从根上杜绝循环依赖 |
| P-2 | **Orchestrator 唯一编排**：只有 `Orchestrator` 能驱动跨模块的业务流程 | 流程集中在一处，好读好改好测 |
| P-3 | **黑板模式（Blackboard）**：模块之间**不直接传大对象**，而是通过 `Storage` 写入产出、用 ID 互相引用 | 解耦；天然支持断点续跑 |
| P-4 | **Provider 是唯一外部出口**：任何模型/搜索调用必须走 `Provider` | 测试可替换成假实现；预算可控 |
| P-5 | **写入权限最小化**：每个模块对其他模块的数据有明确的"可读/可写"边界 | 落实"模拟内容不得污染真实资料"的硬约束 |
| P-6 | **失败不静默**：任何阶段失败都要记录原因并落盘，不吞异常 | 支持任务恢复与排查 |
| P-7 | **接口先行，实现后补**：先定数据契约，再写逻辑 | 适合小步开发，每步可验证 |
| P-8 | **每次资源消耗之前必有检查**：预算闸门在调用之前，不在之后 | 见 `ERROR_HANDLING.md` §5 |
| P-9 | **只读端口优于君子协定**：禁止写的地方用"协议里没有写方法"来保证，而不是靠注释 | 见 `INTERFACES.md` §8.1 |

---

## 2. 术语表（文档间共用，务必一致）

> ⚠️ **v0.2 命名更新**：`Run` → `Project`；`Person` → `PersonEntity`；`Persona` → `PersonaProfile`；`Evidence` 表 → `EvidenceItem`；`origin` → `namespace`。旧名在早期文档中可能出现，一律以本表为准。

| 术语 | 代号 | 含义 |
| --- | --- | --- |
| 项目 | `Project` | 一次「输入对象 → 产出对话」的完整容器（原 `Run`） |
| 模拟对象 | `PersonEntity` | 被模拟的对象，可为真人也可为虚构角色 |
| 来源 | `Source` | 一份公开资料 |
| 来源事件 | `SourceEvent` | 来源内可定位的原始事件（一次采访、一集剧情、一次官方发布） |
| 证据 | `EvidenceItem` | 从来源提取的一条原子化说法 |
| 证据级别 | `Tier` | `VERBATIM` / `PROFILE_BACKGROUND` / `THIRD_PARTY` / `INFERENCE` |
| 来源世界 | `namespace` | `PUBLIC_SOURCE` / `SYSTEM_DERIVED` / `USER_PROVIDED` / `SIMULATED` |
| 对象类型 | `EntityType` | `REAL_PERSON` / `FICTIONAL_CHARACTER` |
| 人物档案 | `PersonaProfile` | 单个对象的结构化档案 |
| 关系档案 | `RelationshipProfile` | 两个或多个对象之间的关系档案 |
| 记忆 | `MemoryItem` | 对话产生、需跨轮保留的信息 |
| 派生产物 | `DerivedArtifact` | 记忆的摘要/索引/缓存，删除需联动失效 |
| 检查点 | `Checkpoint` | 支持续跑的阶段性落盘状态 |
| 任务 | `TaskRecord` | 一次长任务的状态机载体 |
| 预算 | `Budget` | 一次任务的资源消耗上限 |

---

## 3. 分层结构

```
┌────────────────────────────────────────────────────────────────────┐
│ L6  入口层                                                          │
│     ┌──────────────────┐        ┌────────────────────────────┐     │
│     │  CLI             │        │  Evaluation（审片人）        │     │
│     │  命令与状态反馈   │        │  自动化测试与质量评估        │     │
│     └────────┬─────────┘        └─────────────┬──────────────┘     │
└──────────────┼────────────────────────────────┼────────────────────┘
               │ 调用                            │ 调用
┌──────────────▼────────────────────────────────▼────────────────────┐
│ L5  协调层                                                          │
│     ┌──────────────────────────────────────────────────────────┐   │
│     │  Orchestrator（制片主任）                                  │   │
│     │  唯一有权编排其他模块；管状态机、预算、检查点、错误恢复     │   │
│     └───┬────────┬─────────┬──────────┬──────────┬─────────────┘   │
└─────────┼────────┼─────────┼──────────┼──────────┼─────────────────┘
          │        │         │          │          │
┌─────────▼──┐ ┌───▼────┐ ┌──▼──────┐ ┌▼────────┐ │
│ L4  领域层  │ │        │ │         │ │         │ │
│  Research  │ │ Persona│ │Relation-│ │Dialogue │ │   ← 业务主流程
│  资料搜集  │ │ 人物档案│ │ ship    │ │ 对话    │ │
└─────┬──────┘ └───┬────┘ │ 关系档案│ └────┬────┘ │
      │            │      └────┬───┘      │      │
      │            │           │          │      │
┌─────▼────────────▼───────────▼──────────▼──┐   │
│ L3  领域支撑层                              │◄──┘
│   Evidence（证据登记/分级/去重/冲突）        │
│   Memory（记忆保存/检索/更新/删除）          │
└──────────────────┬─────────────────────────┘
                   │
┌──────────────────▼─────────────────────────────────────────────────┐
│ L2  基础设施层                                                       │
│   Provider（模型/搜索/嵌入 的统一接口）   Storage（持久化）           │
└─────────────────────────────────────────────────────────────────────┘

依赖方向：始终自上而下（L6 → L5 → L4 → L3 → L2）。
禁止反向调用。下层需要"通知"上层时，写入 Storage 的状态字段即可。
```

### 3.1 为什么这样分层

- `Storage` 和 `Provider` 在最底层：它们最稳定、被所有人用，且**不依赖任何业务知识**。
- `Evidence` 和 `Memory` 在领域支撑层：它们是"数据的看门人"，其他业务模块都要经过它们读写。
- `Research / Persona / Relationship / Dialogue` 是四个相对独立的业务能力：**同层之间互不调用**，各自干活、各自落盘。
- 跨模块的串联全部交给 `Orchestrator`：比如"研究完了要蒸馏人物"，不是 `Research` 去叫 `Persona`，而是 `Orchestrator` 看到 `Research` 完成后，自己去叫 `Persona`。
  → **这是避免循环依赖的关键设计。**

---

## 4. 模块职责与接口

> 图例：✅ 允许调用 / ⛔ 禁止调用 / 👁 只读

### 4.1 L2 · Storage（仓库）

| 项 | 内容 |
| --- | --- |
| **职责** | 所有数据的持久化：来源、证据、人物档案、关系档案、会话、记忆、检查点、预算账本、审计日志 |
| **调用** | ✅ 无（最底层，不依赖任何业务模块） |
| **不做** | 不做业务判断、不做检索排序逻辑（只提供基础查询能力） |

> 完整签名与失败列表见 `INTERFACES.md` §3.2。此处只列主干：

| 操作 | 说明 |
| --- | --- |
| `transaction(ctx, fn)` | 事务包装，回调失败整体回滚 |
| `put / get / query / update / soft_delete` | 带 `CallContext` 的 CRUD；删除一律软删除 |
| `check_references(ctx, kind, ids)` | 引用完整性检查，返回缺失项（resume 必用） |
| `save_checkpoint / load_checkpoint` | 检查点读写 |
| `record_usage / append_audit` | 用量与审计写入 |

**关键约定**：

- 所有写入记录 `created_at`、`updated_at`、`namespace`、`created_by`，且**删除一律为软删除**（便于审计与恢复）。
- **Storage 不做任何业务判断**：不判断 tier、不判断冲突、不做排序打分。语义决策全部在领域模块。
- 第一版用标准库 `sqlite3` + WAL + FTS5 关键词索引（`INTERFACES.md` §3.3）。

---

### 4.2 L2 · Provider（统一外部接口 / 机房）

| 项 | 内容 |
| --- | --- |
| **职责** | 把"模型调用""搜索调用""可能需要的向量化"统一成一个插头；**负责计量与限流** |
| **调用** | ✅ `Storage`（写调用日志与费用账本） |
| **不做** | 不做提示词编排（那是上层的事）、不做业务判断 |

> 完整签名、能力声明与 Mock 家族见 `INTERFACES.md` §2。

| 操作 | 说明 |
| --- | --- |
| `capabilities() -> CapabilitySet` | 声明支持什么，调用方据此降级 |
| `chat(ctx, ChatRequest) -> Result[ChatResponse]` | 模型调用，支持 JSON Schema 结构化输出 |
| `search(ctx, SearchRequest) -> Result[SearchResponse]` | 检索调用 |
| `usage(ctx, scope) -> UsageReport` | 用量查询（**只读**） |
| `health(ctx) -> HealthStatus` | 探活，**永不失败**，且不计入预算 |

**关键约定**：

- 所有调用**必须带 `ctx`**（含 `task_id` / `trace_ref` / `idempotency_key`），用于统一记账与追踪。
- 第一版**只有两类能力**：`chat` 与 `search`。**不引入 `embed()`**（Q-06 已定：第一版关键词检索）。
- 提供 `FakeProvider` / `RecordingProvider` / `FailingProvider` / `OfflineGuardProvider`（`INTERFACES.md` §2.6）。**测试全程不联网**。
- 预算由 `Orchestrator` 设定，`Provider` 在**调用发出之前**检查；超限返回可捕获的 `BUDGET_EXCEEDED`，交给 `Orchestrator` 转 PAUSED，**Provider 自行不决定降级策略**。
- 重试参数在 `ProviderConfig`（`max_retries=3`，指数退避 + 抖动），配置由 `Orchestrator` 注入。

---

### 4.3 L3 · Evidence（证据管理员）

| 项 | 内容 |
| --- | --- |
| **职责** | ① 证据提取与原子化 ② 分级（Tier） ③ 重复来源/重复证据识别与归并 ④ 冲突检测与记录 ⑤ 稳定性判定（防止"单次行为当性格"） |
| **调用** | ✅ `Storage`（读写） ✅ `Provider`（需要模型做抽取与分类时） |
| **不做** | 不做"谁对谁错"的裁决；不删除任何冲突方 |

> 完整签名与失败列表见 `INTERFACES.md` §4.2。

| 操作 | 说明 |
| --- | --- |
| `register_source(ctx, SourceDraft)` | 来源登记 + 去重（`canonical_key` / `content_hash`） |
| `extract_events(ctx, source_id, granularity)` | 切成 `SourceEvent`，带 `locator` 精确定位 |
| `add_evidence(ctx, EvidenceDraft)` | 证据原子化落地，**拒绝 `namespace=SIMULATED`** |
| `record_inference(ctx, InferenceDraft)` | 登记推断，必须带 `derived_from` |
| `detect_duplicates(ctx, scope)` | 同一说法归并，**保留全部来源引用** |
| `detect_conflicts(ctx, scope)` | 冲突记录，双方都保留 |
| `judge_stability(ctx, ...)` | **稳定性闸门**：`SINGLE` 不得进 `traits` |
| `query_evidence(ctx, EvidenceQuery)` | 只读查询（对 Persona / Relationship / Dialogue 只读端口开放） |

**关键约定**：

- 每条证据必须挂 `source_id`；`INFERENCE` 必须挂 `derived_from[]`，**不允许无根推断**。
- 去重后：保留全部来源引用，只合并"说法本身"。→ 既去重又不丢追溯。
- 冲突记录为**只增不改**结构；`review_status` 只有 `UNREVIEWED` / `ACKNOWLEDGED`，**没有 `RESOLVED`**。
- ⛔ **`Evidence` 不得自行启动研究循环**：它不使用 `Provider.search`，公开签名中不存在任何基于检索的方法。找资料只能走 `Research`。

---

### 4.4 L3 · Memory（场记本）

| 项 | 内容 |
| --- | --- |
| **职责** | 记忆的保存、检索、更新、删除；控制记忆的生命周期（衰减、上限、留存期限） |
| **调用** | ✅ `Storage`（读写） ✅ `Provider`（检索需要向量化时，待定 Q-06） |
| **不做** | ⛔ **绝不允许**把记忆写成 `Evidence`；不做人物档案更新 |

> 完整签名与失败列表见 `INTERFACES.md` §9.3。

| 操作 | 说明 |
| --- | --- |
| `write(ctx, drafts)` | 写入记忆，返回 `MemoryRef` 列表（重复命中返回已有 id） |
| `write_simulated(ctx, turn_id, items)` | **Dialogue 唯一可用的写入通道**，`namespace` 内部固定为 `SIMULATED` |
| `link_duplicate / link_conflict` | 重复与冲突**只做关联，不合并、不择优** |
| `search(ctx, MemoryQuery)` | 第一版关键词检索（SQLite FTS5 + trigram），默认只返回 `ACTIVE` |
| `supersede(ctx, id, draft)` | "更新"= 新建版本，旧版置 `SUPERSEDED`（`content` 不可变） |
| `expire_sweep / archive` | 过期转为 `EXPIRED`，可归档 |
| `delete(ctx, DeleteTarget)` | 三级删除；**同事务**失效相关 `DerivedArtifact` |
| `consolidate / rebuild_derived / invalidate_derived` | 摘要与缓存的生命周期管理 |

**关键约定**：

- 所有记忆**必须**带 `namespace` 与 `confidence_means`；检索结果**必须原样带出** `namespace`，让调用方无法"洗白"。
- ⛔ **不存在把 `MemoryItem` 写成 `EvidenceItem` 的路径**，也不存在 `namespace` 提升操作。
- 删除支持三级：单条 / 按会话 / 按 `namespace`（例如"清空所有模拟记忆"），且务必联动清理摘要与索引缓存（`INTERFACES.md` §9.3 `delete`）。
- 第一版**不使用向量检索**（Q-06 已定）；Keyword 之外无依赖。

---

### 4.5 L4 · Research（资料搜集员）

| 项 | 内容 |
| --- | --- |
| **职责** | 为每个人物检索公开资料，筛选、登记来源，产出"待提取的语料" |
| **调用** | ✅ `Provider.search()` ✅ `Storage`（写来源） ✅ `Evidence`（登记来源、触发提取） |
| **不做** | 不做证据分级判断（交给 `Evidence`）；不做人物蒸馏 |

> 完整签名与失败列表见 `INTERFACES.md` §5.2。

| 操作 | 说明 |
| --- | --- |
| `plan_queries(ctx, targets)` | 生成检索计划（第一版用**确定性模板**，不用模型生成检索词，见 Q-29） |
| `collect(ctx, planned_query)` | 经 `Provider.search` 检索；**0 结果也返回 `Ok`**（`fetch_status=NO_RESULT`） |
| `filter_and_rank(ctx, results)` | 过滤排序；被拒绝项**必须记录原因** |
| `register(ctx, ranked)` | 经 `Evidence` 接口登记来源与事件（**不直接写库**） |
| `run(ctx, targets, budget)` | 主入口；每个查询完成后写一次检查点 |

**关键约定**：

- `Research` **自己不写 `Source` / `EvidenceItem` 表**，一律调用 `Evidence` 的接口。写入口集中，便于审计。
- 检索失败 / 无结果：**记录并继续**，不抛异常中断任务。单个查询失败不影响其他查询。
- 合规性钩子：`filter_and_rank` 中预留来源白/黑名单与 robots / ToS 检查位（**具体规则待定 Q-02**）。
- 只取公开资料，**不触碰 `SIMULATED` 或 `USER_PROVIDED` 内容**。

---

### 4.6 L4 · Persona（人物档案员）

| 项 | 内容 |
| --- | --- |
| **职责** | 把某个人的证据集合蒸馏成结构化人物档案（背景/性格/习惯/对话风格/经历/关系） |
| **调用** | ✅ `Storage`（读写档案） ✅ `Provider`（蒸馏需要模型时） 👁 `Evidence`（**只读**） |
| **不做** | ⛔ 不修改证据；不做关系建模（那是 `Relationship`）；不写记忆 |

> 完整签名与失败列表见 `INTERFACES.md` §6.2。

| 操作 | 说明 |
| --- | --- |
| `distill(ctx, entity_id, options)` | 蒸馏档案，返回 `PersonaView` |
| `get_persona(ctx, entity_id, version=None)` | 只读，默认最新版 |
| `revise(ctx, entity_id, reason)` | 仅在有**新证据**时才允许重算，否则 `STATE_CONFLICT.no_new_evidence` |
| `diff_versions(ctx, entity_id, from, to)` | 版本差异，供审查与评测 |

**内部顺序（实现与测试的基准）**：

```
query_evidence → 按 dimension 分组 → Evidence.judge_stability【闸门】
→ 冲突主题：两边都引用或整条省略（禁止只选一边）
→ 无支撑维度留空 + open_questions → 写盘 → PersonaView
```

**关键约定**：

- 档案中**每一个非空字段都必须带有证据引用**（`evidence_refs[]`）。没有证据支撑的字段必须留空，并写入 `open_questions`，**不允许靠想象补全**。
- ⛔ **不得引用 `MemoryItem` 作为依据**：记忆不得反向喂养人物档案。
- 每个字段带 `confidence`，但 `confidence` 只用于排序展示，**不代表真伪**。
- 资料不足时状态为 `INSUFFICIENT_EVIDENCE`，**仍返回 `Ok`**，流程继续。
- ⛔ **不直接调用任何具体模型供应商**：只允许依赖 `Provider` 抽象，不得持有 API Key / SDK / 厂商地址。

---

### 4.7 L4 · Relationship（关系档案员）

| 项 | 内容 |
| --- | --- |
| **职责** | 把多人的证据与人物档案蒸馏成关系档案：共同经历、互动模式、情绪与表达习惯、双方视角、时间变化、群体互动 |
| **调用** | ✅ `Storage` ✅ `Provider` 👁 `Evidence`（只读） 👁 `Persona`（**只读**） |
| **不做** | ⛔ 不改证据、不改人物档案；不合并双方视角 |

> 完整签名与失败列表见 `INTERFACES.md` §7.2。

| 操作 | 说明 |
| --- | --- |
| `distill(ctx, participant_entity_ids, options)` | 蒸馏关系档案 |
| `get_relationship(ctx, relationship_id \| participant_ids, version=None)` | 只读 |
| `revise(ctx, relationship_id, reason)` | 同 Persona 规则：无新证据不重算 |

**内部顺序**：

```
find_shared_events → find interaction patterns → capture perspectives（分人存，不合并）
→ track_temporal_change → analyze_group_dynamics（≥3 人时）
```

**关键约定**：

- **双方视角分开存**：`perspectives` 是以 `entity_id` 为键的字典，不存在"综合定论"字段。
- 参与者 ≥ 3 时 `group_dynamics` 必填；= 2 时必须为 `N/A`。
- ⛔ **不得从两份人物档案直接推断现实关系**（规则 `RL-V6`）：任一条目的 `evidence_refs` 必须**至少含一条指向 `EvidenceItem` 的引用**，仅指向档案字段的依赖视为非法。档案只能用于交叉校验。
- ⛔ **不得产出任何"亲密度分数"**：结构上不允许 `intimacy` / `closeness` / `*score*` 字段。

---

### 4.8 L4 · Dialogue（现场导演）

| 项 | 内容 |
| --- | --- |
| **职责** | 多人多轮对话：动态选择发言人、生成发言、记录发言、触发记忆写入 |
| **调用** | ✅ `Storage`（读写会话与轮次） ✅ `Provider.chat()` 👁 `Persona`（只读） 👁 `Relationship`（只读） ✅ `Memory`（**仅经 `write_simulated()` 通道写入，`namespace` 恒为 `SIMULATED`**） |
| **不做** | ⛔⛔ **绝不允许写 `Evidence` / `Persona` / `Relationship`**（硬约束 INV-1） |

> 完整签名与失败列表见 `INTERFACES.md` §8.2。

| 操作 | 说明 |
| --- | --- |
| `start_session(ctx, SessionDraft)` | 建会话，**快照锁定**所用档案版本 |
| `select_speaker(ctx, session_id, context)` | 动态选择发言人 + 记录理由 |
| `draft_turn(ctx, session_id, speaker)` | 生成草稿，**不落盘**（便于失败重试） |
| `commit_turn(ctx, session_id, draft)` | 落盘 + 写模拟记忆 + 审计，**同一事务** |
| `run_round / run / end_session` | 组合操作；每轮结束写一次检查点 |

**依赖注入（类型层面阻断误用）**：

```
DialogueDeps{ profiles: PersonaReader,      # 只有 get_* 
              relations: RelationshipReader, # 只有 get_* 
              evidence: EvidenceReader,      # 只有 query_*/get_*，无任何写方法
              memory: MemoryWriter,          # namespace 恒为 SIMULATED
              storage: ConversationStore,    # 只能写 Conversation* 两张表
              provider: Provider }
```

**关键约定**：

- **发言人选择必须记录理由**（`selection_reason{rule, explanation}`），可解释、可回放。
- 每一轮 `namespace = SIMULATED`，且记录 `referenced_memory_ids`、`referenced_persona_item_ids`、`referenced_relationship_item_ids`。
- 注入给模型的上下文里，**记忆必须带着 `SIMULATED` 标记**，防止模型把模拟情节当真事复述。
- 涉及冲突主题时追加 `safety_flags=[CONFLICT_ASSERTION_GUARD]`，**不生成统一结论**。
- ⛔⛔ **`Dialogue` 对 `EvidenceItem` / `SourceEvent` / `PersonaProfile` / `RelationshipProfile` 只可读**（硬约束 INV-1），且它拿到的 `EvidenceReader` 协议里**根本没有写方法**。

---

### 4.9 L5 · Orchestrator（制片主任）

| 项 | 内容 |
| --- | --- |
| **职责** | 任务状态机、阶段串联、预算控制、检查点与恢复、错误分类与重试、进度上报 |
| **调用** | ✅ 全部下层（`Research`/`Persona`/`Relationship`/`Dialogue`/`Evidence`/`Memory`/`Storage`/`Provider`） |
| **不做** | 不做任何具体业务逻辑（不自己提取证据、不自己生成对话） |

> 完整签名与失败列表见 `INTERFACES.md` §12.3；状态机与错误分流见 `ERROR_HANDLING.md` §6。

| 操作 | 说明 |
| --- | --- |
| `create_project(ctx, ProjectDraft)` | 建项目（原 `create_run`） |
| `create_task(ctx, TaskDraft)` | 建任务，状态 `PENDING` |
| `start(ctx, task_id)` / `run_step` / `run_to_completion` | 推进 |
| `pause(ctx, task_id, reason)` / `resume(ctx, task_id)` / `cancel(ctx, task_id, reason)` | 控制 |
| `get_task_status(ctx, task_id)` / `list_tasks(ctx, project_id)` | 只读查询与进度 |

**状态机（六态）**：

```
PENDING ──start──► RUNNING ──► COMPLETED
   │                 │  ▲
   │                 │  └──resume── PAUSED（paused_reason: BUDGET / USER / DEPENDENCY）
   │                 ├────────────► PAUSED        ← 预算耗尽也走这里
   │                 ├────────────► FAILED        ← 致命错误（保留已完成阶段产出）
   └──cancel──► CANCELLED ◄──cancel──┘
```

| 状态 | 可否 resume | 终态 |
| --- | :-: | :-: |
| `PENDING` | — | 否 |
| `RUNNING` | — | 否 |
| `PAUSED` | ✅ | 否 |
| `COMPLETED` / `FAILED` / `CANCELLED` | ⛔ | ✅ |

**关键约定**：

- **暂停 ≠ 取消**：`PAUSED` 保留可用检查点并可续跑；`CANCELLED` 使检查点失效，只能新建任务（已产出数据保留可复用）。
- **预算超支不是失败**：转 `PAUSED(paused_reason=BUDGET)` 并返回 `Ok(paused=True)`（`ERROR_HANDLING.md` §5.3）。
- 每完成一个可划分单元，**立刻写检查点**（阶段级 + 单元清单），不等阶段结束。
- `resume` 时先校验 `artifact_ids` 完整性，再用 `completed_units` + 幂等键跳过已完成部分，不重复调用 Provider。
- **冲突、资料不足、查不到结果都不算失败**（`ERROR_HANDLING.md` §2.3），流程继续。
- 阶段失败时：记录 `last_error` 与 `trace_ref`，按错误类别决定「重试 / 降级跳过 / 任务失败」，**不静默吞异常**。

---

### 4.10 L6 · CLI（前台）

| 项 | 内容 |
| --- | --- |
| **职责** | 解析命令、调用 `Orchestrator`、把状态与进度讲清楚给用户 |
| **调用** | ✅ `Orchestrator` 👁 `Storage`（只读查询） ✅ `Evaluation`（手动跑测试） |
| **不做** | 不含任何业务逻辑 |

> 命令与 Orchestrator 操作的映射见 `INTERFACES.md` §11.2。

| 命令 | 写库？ |
| --- | --- |
| `persona new --names ... [--topic] [--budget]` | 经 Orchestrator |
| `persona status <task_id>` | ❌ 只读 |
| `persona pause <task_id>` / `persona resume <task_id>` | 经 Orchestrator |
| `persona cancel <task_id>` | 经 Orchestrator |
| `persona chat <project_id> [--rounds N]` | 经 Orchestrator |
| `persona sources <project_id>` | ❌ 只读（**可选追溯，不是必经步骤**） |
| `persona memory <scope> --list \| --forget <id> \| --clear-simulated` | 经 Orchestrator |
| `persona eval [--suite]` | ❌ 不影响正式数据 |

**关键约定**：输出要**说人话**。进度展示「找到多少来源 / 提取多少证据 / 记录几处冲突」；错误展示「一句话原因 + 一句话建议 + `trace_ref`」，不展示堆栈。
⛔ **CLI 不得绕过 Orchestrator 直接驱动业务模块**，也不得写业务数据。

---

### 4.11 L6 · Evaluation（审片人）

| 项 | 内容 |
| --- | --- |
| **职责** | ① 自动化单元/集成测试 ② 基于固定素材的质量评估 ③ 架构约束检查（依赖方向、循环依赖） |
| **调用** | ✅ `Orchestrator`（跑完整流程） 👁 `Storage`（读产出做断言） |
| **不做** | 不修改业务数据（除测试库） |

> 详细契约见 `INTERFACES.md` §10。

| 操作 | 说明 |
| --- | --- |
| `run_unit_tests / run_integration_tests` | 跑测试，输出 `TestReport` |
| `run_static_checks` | 依赖方向、循环依赖、禁字段名、签名违规 |
| `check_invariants(ctx, project_id)` | INV-1~INV-4 + 扩展断言（见下表） |
| `measure_quality(ctx, project_id)` | **只输出结构性指标**，不做主观打分 |
| `report(ctx, kinds)` | 汇总报告 |

`check_invariants` 必含：`dialogue_does_not_write_evidence`（对话前后证据表零变化）、`simulated_not_in_evidence`、`memory_cannot_become_evidence`、`conflict_not_resolved`、`no_intimacy_field`、`variants_not_merged`、`no_person_specific_literals`。

**关键约定（三条不可协商）**：

- 测试素材必须是**合成的虚构对象**，可离线运行（对应 AC-17）。
- 全部测试跑在**隔离的临时 SQLite** 上；数据**只经模块公开接口播种**，不得绕过校验直插数据库。
- ⛔ **不得篡改正式业务数据来制造通过的结果**；断言只读查询，发现问题就报错。
- 无标准答案时不假装能评"像不像"，只报可追溯率 / 冲突保留率 / 去重命中率（Q-10 待定）。

---

## 5. 依赖矩阵（谁可以调用谁）

行 = 调用方，列 = 被调用方。✅ 允许｜👁 只读｜⛔ 禁止

| 调用方 ↓ \ 被调用 → | Storage | Provider | Evidence | Memory | Research | Persona | Relation | Dialogue | Orchestr | Eval |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **L2 Storage** | — | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| **L2 Provider** | ✅ | — | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| **L3 Evidence** | ✅ | ✅ | — | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| **L3 Memory** | ✅ | ✅ | ⛔ | — | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| **L4 Research** | ✅ | ✅ | ✅ | ⛔ | — | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ |
| **L4 Persona** | ✅ | ✅ | 👁 | ⛔ | ⛔ | — | ⛔ | ⛔ | ⛔ | ⛔ |
| **L4 Relationship** | ✅ | ✅ | 👁 | ⛔ | ⛔ | 👁 | — | ⛔ | ⛔ | ⛔ |
| **L4 Dialogue** | ✅ | ✅ | ⛔ | ✅ | ⛔ | 👁 | 👁 | — | ⛔ | ⛔ |
| **L5 Orchestrator** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | — | ⛔ |
| **L6 CLI** | 👁 | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ✅ | ✅ |
| **L6 Evaluation** | 👁 | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | ✅ | — |

### 5.1 从矩阵里读出的 6 条硬规矩

1. **同层禁止横向调用**：`Research / Persona / Relationship / Dialogue` 之间**互不直接调用**（唯一例外：`Relationship` 可以**只读** `Persona`，且只能用于交叉校验，不能当作关系的唯一依据）。
2. **`Dialogue` 对 `Evidence` 是 ⛔**：这就是"模拟不能污染真实资料"的技术落地。
3. **`Memory` 对 `Evidence` 是 ⛔**：记忆永远不能升级成证据。
4. **只有 `Orchestrator` 能横跨多个业务模块**：其他模块的跨模块需求，改成"写盘 + Orchestrator 下一步读取"。
5. **下层永不回调上层**：下层需要通知时，写 `Storage` 的状态字段或审计日志。
6. **入口层不碰业务数据**：`CLI` / `Evaluation` 只能通过 `Orchestrator` 发起流程改动。
7. **`Evidence` 不得反过来驱动 `Research`**：研究只能由 `Orchestrator` 编排启动，`Evidence` 不持有检索能力。
8. **`Persona` / `Relationship` 不得依赖具体厂商**：只能依赖 `Provider` 抽象。
9. **`Evaluation` 不得写正式业务数据**，只能在隔离库上跑。
10. **`Storage` 不做业务判断**，也不调用任何外部服务。

> 完整的禁止依赖清单（F-1 ~ F-12）与 Mermaid 依赖图见 `INTERFACES.md` §13。

### 5.2 依赖矩阵补充说明

- `Orchestrator → Storage` 包含对 `TaskRecord` 与 `Checkpoint` 的读写，这是**唯一**有权写 `TaskRecord` 的模块。
- `Memory → Storage` 包含对 `DerivedArtifact` 的读写，这是**唯一**有权写 `DerivedArtifact` 的模块。
- `Dialogue → Memory` 只能经由 `write_simulated()` 通道（`namespace` 由 `Memory` 内部固定）。
- `SourceEvent` 与 `ConflictRecord` 没有独立模块：前者由 `Evidence` 产出，后者由 `Evidence` 登记。

---

## 6. 数据流（数据到底怎么流动）

### 6.1 主流程数据流图

```
  用户输入名字
       │
       ▼
┌──────────────┐   names[]    ┌──────────────┐
│     CLI      │ ───────────► │ Orchestrator │  建 Project、设预算、建 Task
└──────────────┘              └──────┬───────┘
                                     │ ① research(person)
                                     ▼
                              ┌──────────────┐
                              │   Research   │
                              └──────┬───────┘
                    ② search()       │       ③ register_source()
              ┌─────────────────────┘└──────────────────┐
              ▼                                          ▼
       ┌────────────┐                            ┌─────────────┐
       │  Provider  │  raw 搜索结果               │  Evidence   │
       └────────────┘                            │  登记来源   │
                                                 │  去重(哈希) │
                                                 └──────┬──────┘
                                       ④ extract_evidence()
                                                        │
                                                        ▼
                                             ┌────────────────────┐
                                             │  Storage           │
                                             │  sources / evidence│
                                             │  dedupe / conflicts│
                                             └─────────┬──────────┘
                                                       │ ⑤ evidence_ids
                          ┌────────────────────────────┴─────────┐
                          ▼                                        ▼
                  ┌──────────────┐                        ┌────────────────┐
                  │   Persona    │  ⑥ distill            │  Relationship  │ ⑦ distill
                  │  人物档案     │ ──────► persona_id    │  关系档案       │ ──► rel_id
                  └──────┬───────┘                        └────────┬───────┘
                         │  只读 Evidence                 只读 Evidence + Persona
                         └───────────────┬─────────────────────────┘
                                         ▼ ⑧ 档案就绪
                                  ┌──────────────┐
                                  │   Dialogue   │
                                  └──────┬───────┘
                        ⑨ select_speaker + retrieve memory
                                         │
                        ┌────────────────┼─────────────────┐
                        ▼                ▼                 ▼
                 ┌────────────┐   ┌────────────┐   ┌──────────────┐
                 │  Provider  │   │   Memory   │   │   Storage    │
                 │  生成发言  │   │ 取/存记忆  │   │ 落盘 turn    │
                 └────────────┘   └────────────┘   └──────────────┘
                        │                │
                        └────────┬───────┘
                                 ▼
                        下一轮（回到 ⑨，循环）
                                 │
                                 ▼
                         transcript（全部标记 SIMULATED）
```

### 6.2 关键：数据是怎么"落地"的（黑板模式）

模块之间**不传大对象**，只传 ID：

| 步骤 | 产出写入 | 传给下一步的东西 |
| --- | --- | --- |
| Research | 候选 `Source`（经 Evidence 登记） | `source_ids[]` |
| Evidence | `SourceEvent`、`EvidenceItem`、去重组、`ConflictRecord` | `evidence_ids[]` |
| Persona | `PersonaProfile` | `persona_profile_ids[]` |
| Relationship | `RelationshipProfile` | `relationship_profile_ids[]` |
| Dialogue | `ConversationTurn`、`MemoryItem`（`SIMULATED`） | `session_id` |
| Memory | `DerivedArtifact`（摘要/索引/缓存） | `artifact_ids[]` |

**好处**：

1. 任何一步崩了，之前的结果都在磁盘上，`resume` 直接接上。
2. 模块之间不会因为"对象结构改了"而互相牵连。
3. 测试时可以只跑某一段，手工塞入上一层的产出 ID。

### 6.3 冲突数据怎么流（重点）

```
Evidence.detect_conflicts()
   │
   ├─ 发现：来源 S1 说「X」，来源 S2 说「非 X」
   │
   ├─ 写入 conflicts 表：
   │     conflict_id
   │     topic: "关于 X"
   │     members: [ {evidence_id=E1, claim="X",   source_id=S1, tier=VERBATIM},
   │                {evidence_id=E2, claim="非 X", source_id=S2, tier=PROFILE_BACKGROUND} ]
   │     resolution_policy: "KEEP_ALL_UNASSESSED"   ← 固定常量，不可改（无 RESOLVED 状态）
   │     uncertainty_note: "两者时间点不同，可能是变化而非矛盾"
   │
   ├─ ❌ 不删除 E1，不删除 E2，不修改任何一条
   │
   └─ 下游影响：
        Persona    → 该主题写入 `controversial_topics`，两个说法都引用
        Relationship → 视角差异字段中记录分歧
        Dialogue   → 允许角色各自坚持自己的说法（不生成"统一结论"）
```

### 6.4 模拟内容怎么流（隔离路径）

```
Dialogue.run_round()
   │
   ├─ 生成一句发言 u
   │     u.namespace = SIMULATED
   │
   ├─ 落盘 turns 表（含 selection_reason、referenced_memory_ids）
   │
   ├─ 调用 Memory.write_simulated(turn_id, [...]) → 全部 namespace = SIMULATED
   │
   └─ ⛔ 数据流在此终止
        └── 不存在任何通往 Evidence / Persona / Relationship 的写入路径
              （由 check_invariants 自动断言）
```

---

## 7. 三个横切机制

### 7.1 预算控制

> 完整规则见 `ERROR_HANDLING.md` §5。**检查必须发生在消耗之前**。

```
① estimate(单元预估成本)        ← 进入工作单元之前
     不足 ⇒ 【尚未消耗】⇒ save_checkpoint ⇒ PAUSED(paused_reason=BUDGET)
② reserve(预占)                ← 防止并发同时通过检查
③ Provider 调用前再查一次余额   ← 真正发出请求之前（最后一道）
④ record_usage + release       ← 调用返回后立刻记账
⑤ save_checkpoint(已完成单元 + budget_used)
```

| 项 | 约定 |
| --- | --- |
| 计量单位 | 调用次数 + token 数，双指标取严者先触发（可选 `max_cost` / `max_duration_sec`） |
| 告警阈值 | 达 80% 写 `warnings=[BUDGET_NEAR_LIMIT]`，**不改变行为** |
| 未知用量 | token 未知则 `None`，**绝不估算**；至少记 `requests` 与 `latency_ms` |
| 探测调用 | `Provider.health()` 不计入预算 |
| 重试 | 重试的调用同样计费，**不得无限重试烧钱** |

**超预算 ⇒ 暂停，不是失败**（D-043）：已完成的产出保留，用户提高额度后可 `resume` 续跑。

### 7.2 任务恢复

> 完整算法见 `ERROR_HANDLING.md` §6.4 ~ §6.6。

```
检查点内容 = {
  task_id, stage, cursor,
  completed_units[],        # 幂等单元清单，resume 时据此跳过
  artifact_ids { source_ids[], evidence_ids[], persona_profile_ids[],
                 relationship_profile_ids[], session_id },
  budget_used, resume_token, saved_at
}

resume(task_id):
  1. 校验 status == PAUSED，否则 Err(STATE_CONFLICT.not_paused)
  2. 读检查点；缺失则 Err(VALIDATION.checkpoint_missing)
  3. check_references(artifact_ids)   ← 缺 piece 就报错，不允许"假装没做过"
  4. PAUSED → RUNNING
  5. 跳过 completed_units；其余单元携带 idempotency_key 执行
  6. 完成后 COMPLETED（部分失败按 retry_policy 处理，失败项进 warnings）
```

**避免重复的两道保险**：`completed_units` 集合（防重复跑）+ 下游幂等键（防重复写入/扣费）。少一道都不够。

**写入时机**：阶段完成时写一次（粗粒度跳过）；阶段内每完成一个单元写一次（细粒度不丢 90% 的进度）。

崩溃（进程被杀 / Ctrl+C / 断电）恢复：启动时扫描所有 `RUNNING` 态任务 → 回退为 `PAUSED(paused_reason=DEPENDENCY)` → 按上述算法续跑。

### 7.3 可追溯性（Provenance）

每一条数据都带一张"身份证"：

```
任何一条记录 = {
  id,
  namespace,     # PUBLIC_SOURCE | SYSTEM_DERIVED | USER_PROVIDED | SIMULATED
  created_by,    # 哪个模块/哪个任务产生的
  created_at,
  source_refs[], # 上游 ID（来源 / 来源事件 / 证据）
  project_id
}
```

**五类内容必须始终可区分**（`INTERFACES.md` §1.8 写入闸门）：

| 类别 | namespace | 典型载体 |
| --- | --- | --- |
| 公开来源原始资料 | `PUBLIC_SOURCE` | `Source` / `SourceEvent` / `EvidenceItem` |
| 档案及其推断 | `SYSTEM_DERIVED` | `PersonaProfile` / `RelationshipProfile` / INFERENCE 证据 |
| 用户输入 | `USER_PROVIDED` | `UserInputRecord` |
| AI 模拟对话与事件 | `SIMULATED` | `ConversationSession` / `ConversationTurn` |
| 由模拟互动形成的记忆 | `SIMULATED` | `MemoryItem`（另由 `source_type=SIMULATED_DIALOGUE` 二次标注） |

反向查询能力（供 `CLI` 与 `Evaluation` 使用）：

- 给定 `persona` 字段 → 找到它引用的 `evidence_ids` → 找到 `source_event_ids` → 找到 `source_id` 与精确定位
- 给定 `source_id` → 找到它支撑的所有证据 → 找到所有引用了它的档案字段

---

## 8. 目录结构规划（P1 阶段才会创建）

> **本阶段（P0）不创建以下任何目录或代码文件。**

```
src/
├── orchestrator/
├── research/
├── evidence/
├── persona/
├── relationship/
├── dialogue/
├── memory/
├── storage/
├── provider/
├── evaluation/
└── cli/
tests/
├── unit/
├── integration/
└── invariants/       # INV-1..INV-4 的断言测试
fixtures/             # 合成虚构人物素材，不含真实人物
```

---

## 9. 本架构的已知风险

| 编号 | 风险 | 当前缓解方式 |
| --- | --- | --- |
| R-1 | 模块切得较细，初期开发节奏偏慢 | 按 `ROADMAP.md` 分阶段，每阶段只动 2–3 个模块 |
| R-2 | "黑板模式"靠 ID 传递，容易出现悬空引用 | `resume` 时校验产出完整性；`Storage` 层做引用完整性检查 |
| R-3 | 证据分级依赖模型判断，可能不稳定 | 分级规则先做成**可配置的显式规则 + 模型辅助**，并保留人工可覆盖的钩子 |
| R-4 | 冲突可能过多，导致档案碎片化 | 冲突按主题聚合；`uncertainty_note` 字段鼓励记录"可能是时间差/视角差" |
| R-5 | 记忆无限增长 | `Memory` 内置上限、衰减与 `consolidate` 摘要机制（具体策略待定 Q-09） |
| R-6 | 接口契约与架构文档双重维护，容易漂移 | 以 `INTERFACES.md` 为唯一接口真源，`ARCHITECTURE.md` 只做总览与指向；`run_static_checks` 校验签名与依赖 |
| R-7 | 记忆删除后残留在摘要/索引/缓存中 | `DerivedArtifact` 一等实体 + 同事务失效 + 只允许 `FRESH` 产物被检索（`INTERFACES.md` §9.3） |
| R-8 | 虚构角色跨改编版本设定串味 | `variant_group_id` + `merge_policy=NEVER_AUTO_MERGE` + `applicable_variant_ids` 三重约束 |
