# DECISIONS —— 设计决定记录

- **版本**：v0.17（P2 的 Research 部分 + H-10A 业务编排型 Orchestrator + G-2 自动抽取）
- **状态**：§2.9（R-1~R-6）、§2.10（工程约定）、§2.11（U-2/U-3 拍板）、§2.12（配置约定）、§2.13（C-1~C-4 拍板）、**§2.14（P2.3 任务闭环，已验收）**、**§2.15（T-1~T-7 拍板）**为**用户已拍板条目**；§2.8 待人工验收；**§2.24 为 H-10A 新增（待人工验收）**、**§2.25 为 G-2 新增（待人工验收）**；§2.1~§2.7 为 P0 已确认条目
- **相关文档**：`PROJECT_SPEC.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`INTERFACES.md`、`ERROR_HANDLING.md`、`ROADMAP.md`

---

## 0. 这份文档怎么用

- §2 是**已经确认的决定**。标记为「已确认」的条目，**后续 Agent / 开发者不得擅自更改**。
- §3 是**尚未确定的问题**。任何人不许假装它们已经有答案，也不许默默按自己的理解实现。
- 如需更改 §2 的内容，走 §4 的变更流程。

---

## 1. 决定编号规则

| 前缀 | 含义 |
| --- | --- |
| `D-0xx` | 已确认的设计决定（Decision） |
| `Q-0xx` | 待定的开放问题（Question） |
| `INV-x` | 不可违反的硬约束（Invariant），定义在 `PROJECT_SPEC.md` §6.2 |
| `AC-xx` | 验收标准（Acceptance Criteria），定义在 `PROJECT_SPEC.md` §7 |

---

## 2. 已确认的设计决定

### 2.1 项目定位与范围

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-001 | 项目必须**通用化**。代码、配置、测试素材中不得出现任何特定人物的专属设定、事实、关系或"标准答案" | 这是项目成立的前提；一旦硬编码就失去通用性 | 已确认 |
| D-002 | 初期采用**本地 CLI**，暂不开发网页/图形界面 | 降低复杂度，先验证核心链路 | 已确认 |
| D-003 | **不训练、不微调**自己的基础模型，只调用现有模型 | 成本与复杂度远超当前目标 | 已确认 |
| D-004 | **不引入不必要的复杂依赖**。新增依赖需说明理由 | 保持项目可维护、可理解 | 已确认 |
| D-005 | 系统只需支持「用户输入 2 个或更多人名」这一种启动方式；三人及以上必须支持 | 产品核心场景 | 已确认 |

### 2.2 用户体验

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-006 | **普通用户零审核负担**：全流程不要求用户逐条审核证据，不要求用户阅读研究报告 | 产品规则 2 | 已确认 |
| D-007 | 来源清单与证据细节**保留但可选查看**（`persona sources`），不强制展示 | 需要时可追溯，但不打扰普通用户 | 已确认 |
| D-008 | 资料不足时**不报错退出**，标记 `INSUFFICIENT_EVIDENCE` 并继续流程 | 避免流程脆弱 | 已确认 |

### 2.3 证据规则

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-009 | 证据优先级：`VERBATIM` > `PROFILE_BACKGROUND` > `THIRD_PARTY`；系统推断单独用 `INFERENCE` 标记，**不参与前三者排序**（P1.1 由原 `PROFILE` 更名为 `PROFILE_BACKGROUND`） | 产品规则 4 | 已确认 |
| D-010 | **优先级不等于真伪**。优先级只用于取舍排序，绝不用于真假判定 | 产品规则 5 | 已确认 |
| D-011 | 三个评分维度**互相独立、不合并**：`tier`（证据类型）、`source_reliability`（来源可靠性）、`extraction_confidence`（提取置信度）。可合成 `display_weight` 用于排序展示，但必须标注"这是排序权重，不是真假判定" | 落实 D-010 | 已确认 |
| D-012 | **单次行为不得认定为稳定性格**。引入 `stability`（`SINGLE`/`REPEATED`/`PATTERN`）闸门；`Persona.traits` 与 `Persona.single_events` 物理分离 | 产品规则 5 | 已确认 |
| D-013 | 来源冲突时**保留全部说法及其来源和不确定性**，`resolution_policy` 恒为 `KEEP_ALL_UNASSESSED`（P1.2-A 精确化，P0 记为 `KEEP_ALL`），**不设 `RESOLVED` 状态**，不阻断后续处理，**且冲突本身不得导致任务失败**（另见 D-055） | 产品规则 6 | 已确认 |
| D-014 | **禁止无根推断**：`tier=INFERENCE` 的证据必须带 `derived_from[]`，指向其他证据 ID | 保证推断可追溯 | 已确认 |
| D-015 | 去重时**保留全部来源引用**，只合并"说法本身"（`DedupeGroup`） | 既去重又不丢追溯 | 已确认 |

### 2.4 档案内容

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-016 | 人物提取固定覆盖 6 个维度：背景、性格、习惯、对话风格、经历、关系 | 产品规则 7 | 已确认 |
| D-017 | 关系提取固定覆盖 6 个维度：共同经历、互动模式、情绪及表达习惯、双方不同视角、时间变化、群体互动（≥3 人） | 产品规则 8 | 已确认 |
| D-018 | 双方视角**按 `person_id` 分键独立存储**，系统不得自动生成"综合定论"字段 | 产品规则 8；避免抹平分歧 | 已确认 |
| D-019 | 参与者 ≥ 3 时 `group_dynamics` **必填**；= 2 时为 `N/A` | 三人以上要考虑群体互动 | 已确认 |
| D-020 | 档案字段**没有证据支撑时必须留空并记入 `open_questions`**，不允许靠想象补全 | 防止幻觉固化成"事实" | 已确认 |

### 2.5 真实性与隔离

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-021 | 严格区分四类内容，字段名统一为 **`namespace`**：`PUBLIC_SOURCE` / `SYSTEM_DERIVED` / `USER_PROVIDED` / `SIMULATED`（P1.2-A 更名：原 `origin` 的 `REAL_WORLD`/`INFERENCE`/`USER_INPUT`/`SIMULATED`）。`tier`（证据级别）与 `namespace`（来源世界）是**两个独立维度** | 产品规则 9 | 已确认 |
| D-022 | **模拟对话绝不能反向成为真实人物说过的话或真实发生过的事件**。`Dialogue` 对 `Evidence`/`Persona`/`Relationship` **只可读不可写**；`Memory` 不得写 `Evidence` | 产品规则 9；硬约束 INV-1~INV-3 | 已确认 |
| D-023 | `EvidenceItem.namespace` **禁止**取值为 `SIMULATED` | 从数据模型层面堵死污染路径 | 已确认 |
| D-024 | 所有记忆**必须**带 `namespace`，检索结果**必须原样带出** `namespace`，禁止"洗白"；并必须填 `confidence_means` 说明该置信度代表什么 | 让调用方无法假装不知道 | 已确认 |
| D-025 | 所有删除一律为**软删除 + 审计日志**，永不物理删除 | 可追溯、可恢复 | 已确认 |
| D-026 | 用户输入的内容（`USER_INPUT`）**单独登记**，不混入公开资料证据集合 | 防止用户输入被当作公开证据 | 已确认 |

### 2.6 架构

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-027 | 采用**分层架构 + 单向依赖**：L2 基础设施 → L3 领域支撑 → L4 领域 → L5 协调 → L6 入口。下层永不回调上层 | 从根上杜绝循环依赖 | 已确认 |
| D-028 | **`Orchestrator` 是唯一允许编排其他模块的模块**。同层业务模块（`Research`/`Persona`/`Relationship`/`Dialogue`）互不直接调用（唯一例外：`Relationship` 可**只读** `Persona`） | 流程集中，好读好测 | 已确认 |
| D-029 | 模块间采用**黑板模式**：产出写入 `Storage`，模块之间只传递 ID，不直接传大对象 | 解耦 + 天然支持断点续跑 | 已确认 |
| D-030 | **`Provider` 是唯一的外部出口**。所有模型/搜索调用必须走 `Provider`，由它统一记账 | 可替换成假实现、预算可控 | 已确认 |
| D-031 | 写入权限最小化：`Research` 不直接写 `Evidence` 表，统一走 `Evidence` 模块的接口 | 写入口集中，便于审计 | 已确认 |
| D-032 | 系统必须支持：长期记忆、多人多轮对话、**动态发言人选择**、预算限制、任务恢复、自动化测试 | 产品规则 10 | 已确认 |
| D-033 | **发言人选择必须记录理由**（`selection_reason`），且可解释、可回放 | 动态选择不能是黑箱 | 已确认 |

### 2.7 工程与流程

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-034 | **P0 阶段只交付设计文档**，不创建任何代码目录、不安装依赖、不调用真实 API | 用户明确要求；先对齐再动手 | 已确认 |
| D-035 | **P0–P5 全程不接真实搜索/模型 API**。`Provider` 提供 `FakeProvider`（固定返回）与 `RecordingProvider`（记录调用） | 结果可复现，便于验证 | 已确认 |
| D-036 | 测试素材必须是**合成的虚构人物**，可完全离线运行 | 通用化 + 可复现 | 已确认 |
| D-037 | 失败**不静默**：任何阶段失败都记录原因并落盘，不吞异常 | 支持恢复与排查 | 已确认 |
| D-038 | 采用**小步开发**：每阶段只动有限模块，做完即验证，验证通过才进入下一阶段 | 用户要求"可验证、可解释" | 已确认 |
| D-039 | INV-1 ~ INV-4 四条硬约束必须有**自动化断言测试**覆盖 | 红线不能只靠自觉 | 已确认 |

### 2.8 已批准的技术选型与接口契约（P1.2-A 新增）

> 以下条目**来自用户在 P1.2-A 指令中明确给出的批准**，或是对既有规则的精确化。
> 状态标注为「已批准」的条目，后续**不得擅自更改**；标注为「已批准（推荐值待定）」的，指大方向已定、具体参数仍见对应 Q 条目。

| 编号 | 决定 | 来源 | 状态 |
| --- | --- | --- | --- |
| D-040 | 实现语言为 **Python 3.12+**，形态为**本地 CLI + 模块化单体** | 用户批准（解 Q-13） | 已批准 |
| D-041 | 第一版持久化使用 **SQLite**（推荐标准库 `sqlite3` + 薄仓储层；是否引入 ORM/Pydantic 见 Q-27） | 用户批准（解 Q-04） | 已批准（推荐值待定） |
| D-042 | 第一版记忆检索使用**关键词检索**；推荐 SQLite `FTS5` + `trigram` 分词器（内置、零第三方依赖），**不引入向量检索** | 用户批准（解 Q-06） | 已批准（推荐值待定） |
| D-043 | 超预算时**暂停任务 + 保存进度 + 允许之后恢复**；**不做自动降级继续**。`BUDGET_EXCEEDED` 不是失败 | 用户批准（解 Q-07） | 已批准 |
| D-044 | 支持对象类型为 **`REAL_PERSON`** 与 **`FICTIONAL_CHARACTER`**（游戏/动漫/电影/电视剧中的角色） | 用户批准 | 已批准 |
| D-045 | `SourceEvent` 作为**一等实体**（一次采访 / 一集剧情 / 一次官方设定发布），带精确定位 | P1.1 提出，本轮确认 | 已批准 |
| D-046 | 证据级别命名统一为 `PROFILE_BACKGROUND` | P1.1 提出，本轮确认 | 已批准 |
| D-047 | 真人与虚构角色**共用一套基础结构**，差异用 `entity_type` + 两个互斥扩展块表达，**不复制两套系统** | P1.1 提出，本轮确认 | 已批准 |
| D-048 | **禁止**任何关系亲密度/强度类数值指标（`intimacy` / `closeness` / `*score*`） | 用户批准 | 已批准 |
| D-049 | **不同改编版本 / 平行时间线不自动合并**：分别为独立 `PersonEntity`，仅用 `variant_group_id` 关联，`merge_policy` 默认 `NEVER_AUTO_MERGE` | 用户批准 | 已批准 |
| D-050 | **模拟内容禁止回写**为：真人真实发言、真人真实经历、作品正式剧情、官方设定。AI 无创建 `SourceEvent` 的路径 | 用户批准 | 已批准 |
| D-051 | 记忆的摘要 / 索引 / 缓存（`DerivedArtifact`）必须是**一等实体**，删除记忆时同事务失效 | P1.1 提出，本轮确认 | 已批准 |
| D-052 | 任务状态采用**六态**：`PENDING` / `RUNNING` / `PAUSED` / `COMPLETED` / `FAILED` / `CANCELLED`；暂停原因单独存 `paused_reason` | 用户批准 | 已批准 |
| D-053 | 跨模块调用统一使用 **`Result` 信封**，严格区分**业务结果 / 运行错误 / 验证错误**；跨模块边界不抛业务异常 | 本轮新增 | 已批准 |
| D-054 | `Provider` 是**唯一外部出口**；任何模块不得依赖具体厂商 SDK、不得直接持有 API Key | 本轮新增（含"Persona 不直接调用供应商"） | 已批准 |
| D-055 | **冲突不得导致任务失败**，也不得导致流程中断；冲突映射为 `warnings` + 冲突记录 | 用户批准 | 已批准 |
| D-056 | `Evaluation` **不得修改正式业务数据来制造通过的结果**；测试跑在隔离库，数据只经模块公开接口播种，断言只读 | 用户批准 | 已批准 |
| D-057 | `Dialogue` 只接收**只读端口**（`PersonaReader` / `RelationshipReader` / `EvidenceReader`）与 `MemoryWriter(SIMULATED)`；协议中没有写方法 | 本轮新增 | 已批准 |
| D-058 | 预算闸门采用**四段式：预估 → 预占 → 调用 → 记账**，检查必须在资源消耗**之前** | 本轮新增 | 已批准 |
| D-059 | `CLI` **不得绕过 `Orchestrator`** 直接驱动业务模块，也不得写业务数据（仅只读查询） | 用户批准 | 已批准 |
| D-060 | 每次外部调用**必须记账**；token/金额未知时留 `None`，**禁止填估计值** | 本轮新增 | 已批准 |
| D-061 | 恢复时的两道保险：**`completed_units` 集合** + **下游幂等键**，二者缺一不可 | 本轮新增 | 已批准（推荐值待定） |

### 2.9 P1.2-B 验收结论（用户对 R-1 ~ R-6 的拍板）

> 以下条目来自用户对 P1.2-B 汇报中 R-1 ~ R-6 的逐一拍板，**已生效**。
> 后续 Agent 不得以「文档里没写」为由推翻，也不得把未实现的 R-6 当成遗漏去偷偷补。

| 编号 | 决定 | 对应项 | 状态 |
| --- | --- | --- | --- |
| D-062 | P1.2-B 的 SQLite 骨架采用**通用 `records` 表 + JSON 载荷**（4 张骨架表）是**被接受的临时方案**；**必须在 P2 结束前迁移**到 `DATA_MODEL.md` 的 13 张正式表。迁移方式：`schema_version` 递增 + 显式迁移脚本，**禁止自动删表** | R-1 | 已批准 |
| D-063 | 新增两个错误码 **`VALIDATION.unknown_kind`**（实体种类不在注册表）与 **`VALIDATION.unknown_filter`**（查询过滤键不在白名单），均属 `VALIDATION` 类、不可重试、不影响任务状态。已回填 `ERROR_HANDLING.md` §2.2 | R-2 | 已批准 |
| D-064 | 命名空间写入闸门是**双层**的：**模块接口层**（解释「为什么不行」）+ **`Storage.put` 结构层**（保证「一定不行」）。结构层只做**静态允许表查表**，等价于外键/CHECK 约束，**属于 fail-closed 的完整性约束，不属于业务判断**，因此不违反「`Storage` 不做业务判断」。P2 补齐模块实现时**两层都要保留** | R-3 | 已批准 |
| D-065 | 包名与 CLI 命令名**保持 `persona`**（`python -m persona`），不改名 | R-4 | 已批准 |
| D-066 | `fixtures/` 合成测试素材与独立静态检查脚本（`run_static_checks`）**必须在 P2 开工前补齐**；在此之前只由 `tests/test_dependency_rules.py` 承担最关键的静态检查 | R-5 | 已批准 |
| D-067 | 「任务引擎未实现」在 P1.2-B 属于**符合指令范围的正常状态**，**不是缺陷**。当前只交付六态状态机 + 检查点数据结构 + 幂等键；`run_step` / 预算四段式闸门执行代码 / `resume` 执行流程在 P2 实现 | R-6 | 已批准（确认为符合指令） |

### 2.10 工程与环境约定（P2.1 新增）

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-068 | **运行时与测试期零第三方依赖**。测试框架固定为**标准库 `unittest`**，不引入 pytest；`python_requires >= 3.12` | D-004（不引入不必要依赖）+ 用户指令「优先使用标准库」 | 已批准 |
| D-069 | **统一测试命令**为 `python -m unittest discover -s tests -t .`（加 `-v` 看明细）。不提供 Makefile / tox 等额外入口 | 一条命令跑全项目，降低新手门槛 | 已批准 |
| D-070 | 测试**必须**使用临时目录中的 SQLite 数据库（`tempfile.mkdtemp` / `TemporaryDirectory`），**禁止**在项目目录内生成任何 `.db` 文件；测试结束清理 | 避免污染仓库、避免误把测试库当真实库 | 已批准 |
| D-071 | 项目**不需要也不读取任何 API Key**。`.env` / 密钥类文件一律进 `.gitignore`；任何情况下**不得读取或输出密钥内容** | 本阶段无真实外部调用；提前设防 | 已批准 |
| D-072 | `pyproject.toml` **仅作元信息与 `requires-python` 声明**（`dependencies = []`），**不是运行前置条件**；不安装也能用 `python -m persona` 直接跑 | 保持「零安装即可运行」，同时让工具能识别 Python 版本下限 | 已批准 |

### 2.11 用户对 U-2 / U-3 的拍板（P2.2 前）

| 编号 | 决定 | 对应问题 | 状态 |
| --- | --- | --- | --- |
| D-073 | **不引入 Pydantic / ORM**。持久化与数据结构统一使用**标准库 `dataclasses` + `sqlite3` + 薄仓储层** | **U-2 / Q-27 = 否** | 已批准 |
| D-074 | 中文关键词检索采用 **SQLite FTS5 + `trigram` 分词器**（内置、零依赖）；**旧版 SQLite 不支持时退化为自建字串倒排表**，不得因此引入第三方分词库 | **U-3 / Q-30** | 已批准 |
| D-075 | 同一角色的**不同改编默认分离为独立 `PersonEntity`**，仅用 `variant_group_id` 关联（`merge_policy` 默认 `NEVER_AUTO_MERGE`），与 D-049 一致 | **U-3 / Q-15 = 是** | 已批准 |
| D-076 | **一份 `PersonaProfile` 默认不共用多个改编版本**（`U-3 / Q-22 = 否`）；确需共用时必须**显式声明**并**逐条标注**每个字段适用哪些版本 | **U-3 / Q-22 = 否** | 已批准 |

### 2.12 配置管理约定（P2.2 新增）

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-077 | **统一配置入口**为 `persona.config`。**任何模块都不得自行 `os.getenv` 读环境变量**，必须经配置对象取值 | 避免配置散落、来源不可追溯（本阶段任务 1） | 已批准 |
| D-078 | 配置**分两类**：**非敏感配置**（运行模式、数据目录、预算上限、日志级别、Provider 选择、重试参数等）可出现在文件与日志；**敏感配置**（各类 API Key）**只能来自环境变量**，不入文件、不入日志、不入库、不进 Git | 任务 2/3；配合 D-071 | 已批准 |
| D-079 | **不引入 `python-dotenv` 等依赖**。项目自带**极简 `.env` 解析**（仅 `KEY=VALUE` 与 `#` 注释），只用于**非敏感**配置；`.env.example` 只放占位符，**严禁写入真实密钥** | D-004 最小依赖 + D-068 零第三方依赖 | 已批准 |
| D-080 | 配置优先级（由低到高）：**内置默认值 < `.env` 文件 < 进程环境变量 < 显式传入的 overrides**。测试一律使用显式构造的测试配置 | 让默认值可预测、让 CI 可覆盖、让测试不依赖外部环境 | 已批准 |
| D-081 | **错误信息绝不包含密钥值**。密钥相关错误只说明「哪个环境变量、缺失还是格式不符」，**不回显任何字符内容**；`Secrets` 的 `repr` / `str` / `to_public_dict()` 一律脱敏 | 任务 4；配合 D-071 | 已批准 |
| D-082 | 未知配置项（`PERSONA_*` 前缀但不认识）**只产生 warning，不失败**；**值格式错误或超出范围**才失败（→ `Err`，`VALIDATION.config_invalid`） | 避免因多打一个变量就崩溃，同时不放过真错误 | 已批准 |

### 2.13 用户对 C-1 ~ C-4 的拍板（P2.2 验收）

| 编号 | 决定 | 对应问题 | 状态 |
| --- | --- | --- | --- |
| D-083 | **确认新增错误码 `VALIDATION.config_invalid`**，并**永久保留**在 `ERROR_HANDLING.md` §2.2。取值非法/超范围的错误一律用它，不另起新码 | **C-1** | 已批准 |
| D-084 | **`PERSONA_SEARCH_API_KEY` 保持可选**。是否改为必填，等 P2 确定搜索 Provider 之后再决定；在此之前缺它**不得**导致配置校验失败 | **C-2** | 已批准 |
| D-085 | **暂不支持项目级配置文件**（如 `persona.toml` / `persona.yaml`）。配置来源只有：内置默认值、`.env`、环境变量、显式 overrides（D-080）。确有需求时再单独拍板 | **C-3** | 已批准 |
| D-086 | **`log_level` 目前"只存不用"**，等 P2 接入真实日志时再启用。**禁止**先写空壳日志器占位——宁可没有，也不要假装有 | **C-4** | 已批准 |

### 2.14 任务执行与状态管理基础闭环（P2.3 新增）

| 编号 | 决定 | 原因 | 状态 |
| --- | --- | --- | --- |
| D-087 | **任务状态严格六态**：`PENDING` / `RUNNING` / `PAUSED` / `COMPLETED` / `FAILED` / `CANCELLED`。合法转换表以 `ERROR_HANDLING.md` §6.2 为准，**非法转换返回 `Err` 且原状态不变** | 任务 2/3；配合 D-052 | 已实现，**已验收（P2.3，2026-10-13）** |
| D-088 | **暂停 ≠ 失败 ≠ 取消**：`PAUSED` 保留检查点且**唯一**可 `resume`；`FAILED` 保留检查点但不可续跑；`CANCELLED` 使 `resume_token` 失效、只能新建任务。三者在代码里是三个独立分支，不共用处理路径 | ERROR_HANDLING §6.3 | 已实现，**已验收（P2.3，2026-10-13）** |
| D-089 | **预算闸门顺序固定为「检查 → 预占 → 执行 → 记账」**，检查发生在**任何资源消耗之前**；不足时**先落检查点再暂停**（`paused_reason=BUDGET`），返回 `Ok(StepOutcome{paused=True})` 而非 `Err` | D-043、ERROR_HANDLING §5.1/§5.3 | 已实现，**已验收（P2.3，2026-10-13）** |
| D-090 | 检查点的**权威存储**是 `checkpoints` 表（`Storage.save_checkpoint` / `load_checkpoint`）；`TaskRecord` 里只镜像 `resume_token` / `saved_at` / `stage` / `cursor` 四个摘要字段，**避免两处数据打架** | 单一事实来源（single source of truth） | 已实现，**已验收（P2.3，2026-10-13）** |
| D-091 | 防重复执行**两道保险同时生效**：① `Checkpoint.completed_units` 集合（跳过已完成单元，不调用、不计费）；② 下游幂等键 `f"{task_id}:{unit}"`（经 `CallContext.idempotency_key` 传递） | D-061 | 已实现，**已验收（P2.3，2026-10-13）** |
| D-092 | `TaskRecord.idempotency_key` 在同一 `project_id` 下唯一：重复提交（`create_task`）**直接返回原任务**，不新建第二条 | TS-V4（DATA_MODEL §4.12） | 已实现，**已验收（P2.3，2026-10-13）** |
| D-093 | 执行器只依赖 `UnitHandler` 协议；**本阶段唯一实现是 `MockUnitHandler`**，不调用真实模型、搜索与网络 | 任务 6；不提前实现业务 | 已实现，**已验收（P2.3，2026-10-13）** |
| D-094 | 崩溃/重启恢复：启动时扫描仍为 `RUNNING` 的任务 → 置 `last_error=PROCESS_INTERRUPTED`、状态回退为 `PAUSED(paused_reason=DEPENDENCY)`。**只做到"状态与检查点可读可续"**，不宣称已实现完整业务恢复 | ERROR_HANDLING §6.6；任务 4 | 已实现，**已验收（P2.3，2026-10-13）** |

### 2.15 用户对 T-1 ~ T-7 的拍板（P2.3 验收）

| 编号 | 决定 | 对应问题 | 状态 |
| --- | --- | --- | --- |
| D-095 | **保留 P2.3 新增的 4 个错误码**：`VALIDATION.checkpoint_missing` / `artifact_missing` / `unknown_task_type` / `stage_sequence_empty`，永久登记在 `ERROR_HANDLING.md` §2.2（去掉"待确认"标记） | **T-1** | 已批准 |
| D-096 | **任务恢复粒度接受"阶段 + 阶段内单元清单"**（比 Q-14 推荐的阶段级更细），作为第一版既定口径，不再收敛为纯阶段级 | **T-2** | 已批准 |
| D-097 | **接受 Mock 执行器不产出实体 ID 的限制**：`artifact_missing` 的端到端用例推迟到 P2 真实数据接入后再补，此前只用手工构造的检查点验证该分支 | **T-3** | 已批准 |
| D-098 | **墙钟预算 `max_duration_sec` 暂缓接入**：`BudgetGate` 本阶段只算次数 / token / 金额；后续确有需要时再接，不提前实现 | **T-4** | 已批准 |
| D-099 | **接受 `attempt_count` 任务级累计**（不按单元重置）。多单元任务重试次数可能提前耗尽这一影响被明确知晓并接受 | **T-5** | 已批准 |
| D-100 | **暂不需要 `create_project`**：任务继续直接挂 `project_id` 字符串，等 P2 主管线开工时再补项目实体 | **T-6** | 已批准 |
| D-101 | **接受无并发保护**：本地单用户工具，暂不做多进程同时跑同一任务的场景；`records.rev` 乐观锁保留在存储层，`TaskService` 暂不传 `expected_rev`。若未来出现并发需求，**最小修复**为 `TaskService` 更新时传入读到的 `rev` 作为 `expected_rev`（存储层已支持，无需改表） | **T-7** | 已批准 |

### 2.16 P2 证据管线第一步：来源与证据管理（用户称 P3.1，编号差异见 Q-37）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-102 | `EvidenceService` **只依赖 `Storage`、不持 `Provider`**：本阶段不接入真实研究服务；模块内没有 `search` / `collect` / `query_plan`（连方法都不存在，测试钉死 F-2）。研究编排归 `Orchestrator`，本模块不绕过现有模块接口 | 用户目标 9；INTERFACES §4.1 | 已实现，**待人工验收** |
| D-103 | **命名空间入口规则**：`add_evidence` 只收 `PUBLIC_SOURCE`；`INFERENCE` 只能经 `record_inference`（强制 `namespace=SYSTEM_DERIVED`、`derived_from` 非空、`source_id=None`）；`SIMULATED` 永久拒绝（EV-V1，模块层先于业务校验 + 存储层结构闸门双层）；`USER_PROVIDED` 暂拒（引导至尚未实现的 `UserInputRecord`，见 RUNNING §5.4 E-2） | 用户目标 3/5；EV-V1/EV-V5；D-023/D-064 | 已实现，**待人工验收** |
| D-104 | **去重五元组条件**（刻意严格）：归一化 claim（仅折空白，不改大小写不压缩语义）+ 相同 subject 集合 + 相同 dimension + 相同 applicable_variant_ids + 相同 canon_level。正典状态不同、适用版本不同 ⇒ **不并组**；语义相似（"喜欢" vs "喜爱"）⇒ **不并组**（不假装语义判断） | 用户目标 7；DATA_MODEL §4.5 | 已实现，**待人工验收** |
| D-105 | **去重组无新实体**：组 ID = **最早成员**的 `evidence_id`（"规范成员指针"）。不新建前缀、不建新表、完全确定性，重复检测幂等。已知边界：`created_at` 秒级精度，同秒创建的成员之间退化为按 `id` 取最小（RUNNING §5.4 E-4） | D-015；用户目标 7 | 已实现，**待人工验收** |
| D-106 | **冲突结构检测规则**（确定性，未实现语义检测）：① 同一说法（归一化 claim + 相同 subject）在不同来源的 canon_level 分歧（CANON vs NON_CANON/AMBIGUOUS）⇒ VERSION_DIFFERENCE；② 同一说法适用于**互斥的改编版本集合** ⇒ VERSION_DIFFERENCE。检测到的冲突只记录、不裁决；已记录的成员集合不重复建（幂等） | 用户目标 4/6；D-013/D-055 | 已实现，**待人工验收** |
| D-107 | **6 个新错误码**（`VALIDATION.tier_namespace_mismatch` / `source_missing` / `entity_missing` / `dangling_reference` / `empty_evidence` / `granularity_unsupported`）：INTERFACES §4.2 已点名这些失败原因，§2.2 表原先未列；**同 T-1 口径，待人工确认**（RUNNING §5.4 E-1） | INTERFACES §4.2；T-1 先例 | 待人工确认 |
| D-108 | `extract_events` **只走确定性降级**：来源不可切分时创建"覆盖全文的单块事件"（`locator={"chapter": "全文"}`）+ `warnings=[EVENT_SINGLE_BLOCK]`；已有事件幂等返回。AI 辅助切分需 `Provider.chat`，属后续阶段 | 用户目标 1；INTERFACES §4.2 | 已实现，**待人工验收** |
| D-109 | `judge_stability` **确定性规则**：1 条 ⇒ SINGLE；≥2 条 ⇒ REPEATED；≥3 个**不同来源** ⇒ PATTERN。只读（不改证据）；`subject_entity_id` / `claim_text` 进 `reason` 留痕，未做交叉校验（E-3） | D-012；INTERFACES §4.2 | 已实现，**待人工验收** |
| D-110 | **`EvidencePort` Protocol 签名对齐 `INTERFACES.md §4.2`**（P1.2-B 骨架签名过时，如 `judge_stability(entity_id, dimension) -> Result[Stability]`）；补服务侧 `get_evidence_tier`（Protocol 早声明但服务未实现，只读端口会踩空）。Protocol 无运行时强制，此次更正**不影响任何既有调用方、无行为变化** | 以仓库文档为准（用户指令） | 已实现，**待人工验收** |

---

### 2.17 P3 第一步：人格档案基础模块（用户称 P3.2，编号差异见 Q-37）

> 范围：`PersonaProfile` 的本地管理基础（存储 / 证据关联 / 版本 / 校验）。
> **不实现** `distill` 的 AI 蒸馏主体（要调 `Provider.chat`，属下一阶段），
> 不启动研究流程，不调模型提供商。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-111 | **版本模型**：每个版本是一条独立 `PersonaProfile` 记录（新 `pnf_` ID），以 `entity_id + version` 定位，旧版打 `superseded_by` 指向新版；**对象身份锚定 `entity_id`**（一对一）。旧版永不删除、永不修改内容（Q-19 的"保留最近 5 版、更早归档为摘要"**未实现**：当前全版本保留，不删用户数据） | DATA_MODEL §4.7 生命周期；用户目标 2/6 | 已实现，**待人工验收** |
| D-112 | **`revise` 必须带 `change_reason`（更新依据）**；内容与最新版**逐字段完全一致时拒绝生成新版本**（`STATE_CONFLICT.no_new_evidence`）——不产生空转版本，版本变化永不静默发生 | 用户目标 6；INTERFACES §6.2 `revise` 语义 | 已实现，**待人工验收** |
| D-113 | **复合类型补形**（DATA_MODEL 未定义内嵌结构，本模块定形）：`ProfileSection = {text, confidence, evidence_refs, uncertainty_note}`；`CanonBoundary = {canonical_material_refs, non_canonical_material_refs, note}`；`CoverageStats = {板块: {items, evidence_refs}}`。**`coverage` 由服务从真实内容计算，不接受调用方填报**（防伪造覆盖度） | DATA_MODEL §4.15 缺口；用户目标 1 | 已实现，**待人工验收** |
| D-114 | **`item_id` 由调用方提供且必填**（跨版本稳定才能产出可读的 `diff_versions`）；不提供则拒绝（而不是静默生成——否则每次修订都看起来像全量替换） | INTERFACES §6.2 `diff_versions` 可用性 | 已实现，**待人工验收** |
| D-115 | **版本边界规则**：真人实体 `applicable_variant_ids` / `do_not_apply_to_variants` 必须为空；虚构角色必须非空、**必须包含档案对象自身**、不得跨 `variant_group`（PE-V3）；证据的 `applicable_variant_ids` 非空时**必须包含档案 `entity_id`**（版本专属证据不得串用到其他版本的档案） | DATA_MODEL PE-V3 / PF-V5；用户目标 7 | 已实现，**待人工验收** |
| D-116 | **`evidence_refs` 只接受 `EvidenceItem`**：`MemoryItem` / `ConversationTurn` / `Source` 等一律拒绝（模拟对话、模拟事件、模拟记忆永远不能成为档案依据，INTERFACES §6.3）；档案与条目 `namespace` 恒 `SYSTEM_DERIVED`（PF-V6），`SIMULATED` 模块层 + 存储层双层拒绝 | INTERFACES §6.3；用户目标 8 | 已实现，**待人工验收** |
| D-117 | **全空档案必须写 `open_questions`**：没有任何证据支撑内容时，不接受"交白卷也不说明"（D-020 操作化）；资料不足不是错误——`status=INSUFFICIENT_EVIDENCE` 仍返回 `Ok`（D-008） | D-020 / D-008；用户目标 5 | 已实现，**待人工验收** |
| D-118 | **`controversial_topic_ids` 双向一致（CF-V5）**：档案引用冲突时，把 `{profile_id, entity_id, version, field}` 回写到 `ConflictRecord.downstream_refs`（幂等）；只记录关联，**不裁决**冲突 | DATA_MODEL CF-V5；D-013 | 已实现，**待人工验收** |
| D-119 | **`distill`（AI 蒸馏主体）未实现**：本模块只提供落盘前必须经过的存储/校验/版本层；`PersonaPort` Protocol 按**已实现面**对齐（`distill` 待下一阶段补入协议，不虚占签名，同 D-110 先例）；模块零 Provider 依赖、无任何研究方法（F-2/F-5） | INTERFACES §6.1/§6.2；用户目标 10 | 已实现，**待人工验收** |
| D-120 | **真人档案禁填 `canon_boundary`**（虚构角色专用字段，DATA_MODEL §4.7 ⭕ 注记）；真人也没有改编版本，故变体字段必须为空 | DATA_MODEL §4.7 字段语义 | 已实现，**待人工验收** |

**不确定性表达口径**（回应用户目标 5）：模型**已能**表达不确定性，无需重构——
条目的 `uncertainty_note`（有证据但存疑）、档案的 `open_questions`（无证据的缺口，
D-020）、`status=AMBIGUOUS/INSUFFICIENT_EVIDENCE/CONFLICTED`（D-008）、
`controversial_topic_ids`（矛盾主题挂接，不裁决）。没有证据的内容**不允许**写成
档案条目——这是 PF-V1 拒绝的路径，缺口只能进 `open_questions`。

---

### 2.18 P3 第二步：关系档案基础模块（用户称 P3.3，编号差异见 Q-37）

> 范围：`RelationshipProfile` 的本地管理基础（存储 / 双向视角 / 共同经历 /
> 时间变化 / 证据关联 / 版本 / 群体互动 / 校验）。
> **不实现** `distill` 的 AI 蒸馏主体（要调 `Provider.chat`，属下一阶段），
> 不启动研究流程，不调模型提供商，不绕过 Orchestrator。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-121 | **群体互动复合类型补形**（DATA_MODEL §4.8 未定义 `GroupDynamics` 内嵌结构，本模块定形）：`GroupDynamics = {alliances, exclusion, relay, note}`（联盟 / 冷落 / 传话 + 备注）；`SharedExperienceItem = TimelineItem + participant_entity_ids`（共同经历"含参与者"，DATA_MODEL §4.8 原文要求而 `TimelineItem` 不带该字段——**本地扩展，不修改已验收的 `TimelineItem`**；空 = 全体档案参与者，非空必须是参与者子集） | DATA_MODEL §4.8；用户目标 4/6 | 已实现，**待人工验收** |
| D-122 | **`GroupDynamics` 必填形状**：≥3 人时 `group_dynamics` 非空（三个板块至少一项有内容，或写 `note` 说明缺口，D-020 同口径）；=2 时必须为 `None`。全空且无 note → `VALIDATION.group_dynamics_missing`（新错误码，E-6 **已批准**） | RL-V3；用户目标 6 | 已实现，**已验收（P3.3，2026-10-14）** |
| D-123 | **双向视角不合并**：`perspectives` 以 `entity_id` 为键独立存放（RL-V1 键集合必须等于参与者集合）；`view_of_targets` 的目标必须是**其他参与者**；结构上不存在任何跨视角"综合定论"字段（RL-V5）；`shared_*` 系列允许存在但每条仍须带 `evidence_refs` | DATA_MODEL §4.8；D-018；用户目标 3 | 已实现，**待人工验收** |
| D-124 | **RL-V2 亲密度禁令落地为双重扫描**：入口扫描 `draft` 属性名 + 落盘前递归扫描 payload 键名，命中 `score` / `closeness` / `intimacy` 一律 `gate_rejected`；数据契约（dataclass 字段）本身不含此类字段（测试钉死） | RL-V2 / D-048；用户目标 7 | 已实现，**待人工验收** |
| D-125 | **模式闸门（用户目标 5 操作化）**：`interaction_patterns` 是模式性声明，`stability=SINGLE` 一律拒绝（D-012 同精神）；单次互动的归宿是 `shared_experiences`（带时间的具体经历）。其余板块不受闸门约束（用 `uncertainty_note` / `stability` 表达不确定） | 用户目标 5；D-012 | 已实现，**待人工验收** |
| D-126 | **RL-V6 证据直达**：关系条目 `evidence_refs` 只接受 `EvidenceItem`——`PersonaProfile`（档案字段）/ `MemoryItem` / `ConversationTurn` 一律拒绝（"不凭两份人格档案拼关系"可测）；证据的 `applicable_variant_ids` 非空时**必须与档案参与者集合有交集**（空交集 = 证据讲的是别的版本的事，D-117 同口径；注意与 D-115 的单档案规则不同：关系证据常只覆盖部分参与者，只要求不矛盾） | INTERFACES §7.1；用户目标 5 | 已实现，**待人工验收** |
| D-127 | **反合并规则（关系版）**：同一角色的两个改编版本（同 `variant_group_id`）**不得同时作为参与者**出现在一份关系档案里；不同作品的角色（不同组）不受限。`applicable_variant_ids` 必须是参与者的子集、必须覆盖全部虚构参与者、不得含真人（D-115）。同一参与者集合的两份档案按**集合**匹配（存储顺序不影响身份） | 用户目标 7；PE-V3 精神 | 已实现，**待人工验收** |
| D-128 | **`distill` 未实现 + 新错误码**：关系模块只提供落盘前必须经过的存储/校验/版本层（同 D-119）；`RelationshipPort` Protocol 按已实现面对齐；新增 1 个错误码 `VALIDATION.group_dynamics_missing`（INTERFACES §7.2 已点名、原表未列，E-6 **已批准**） | INTERFACES §7.2；用户目标 10 | 已实现，**已验收（P3.3，2026-10-14）** |

**群体关系口径**（回应用户目标 6）：≥3 人的关系存储为**一份完整档案**
（`cardinality=GROUP` + 全体参与者 + 逐人视角 + `group_dynamics`），
系统**不自动拆出**两人子档（测试钉死：建群体档后查询只有 1 条）；
逐条目参与者（`SharedExperienceItem.participant_entity_ids`）支持
"一次经历只涉及部分成员"的群体场景。当前模型**不能**表达
"群体内的子结构关系"（如 A-B 是盟友、同时 A-C 是对手需要两份档案或
额外结构）——记为缺口 G-2，不擅自重构核心模型。

---

### 2.19 P3 第二步（关系档案）验收决定（2026-10-14，用户拍板）

> 用户于 2026-10-14 验收 P3.3（关系档案基础模块），并对遗留项 E-6 / G-1~G-5 逐一拍板。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-129 | **E-6 批准**：保留错误码 `VALIDATION.group_dynamics_missing`（INTERFACES §7.2 已点名）；错误码表与关系模块的实现口径均落档（ERROR_HANDLING §2.2 / RUNNING §5.6） | 用户验收意见 | **已批准** |
| D-130 | **G-1 接受未实现**：`distill` 的 AI 蒸馏主体继续留空，下一阶段接入真实 Provider 时再补；关系模块只提供落盘前必须经过的存储/校验/版本层 | 用户验收意见 | 接受，**待下一阶段** |
| D-131 | **G-2 接受当前表达边界**：群体子结构关系（"A-B 盟友且 A-C 对手"）当前无法用单份档案表达，最小建议是"拆成多份两人档案或引入额外子结构实体"，**不擅自重构核心模型**，记后续待办 | 用户验收意见 | 接受，**后续待办** |
| D-132 | **G-3 补文档**：`GroupDynamics` 的内嵌结构（`alliances` / `exclusion` / `relay` / `note`，每项为带 `evidence_refs` 的 `ProfileItem`）补入 `DATA_MODEL.md` §4.8（D-121 定形内容正典化） | 用户验收意见 | 已实现（见 DATA_MODEL §4.8） |
| D-133 | **G-4 全版本保留**：关系档案与人格档案一致，全部版本保留、不做裁剪（Q-19 统一策略） | 用户验收意见 | 已确认 |
| D-134 | **G-5 确认刻意不为**：`RelationshipProfile` 数据契约不含"亲密度总分 / 单一数值关系强度"字段，RL-V2 双重扫描只为防回归；符合红线，无需改动 | 用户验收意见 | 已确认 |

---

### 2.20 P4 第一步：对话生成基础模块（用户称 P3.4，编号差异见 Q-37）

> 范围：多人多轮对话的**本地生成基础**——会话/轮次管理、动态发言人选择（含
> 可解释理由）、只读上下文组装、经 `Provider` 生成发言、轮次落盘、**严格模拟隔离**。
> **不实现**长期记忆自动写入（记忆模块 P4 第二部分）、真实研究、复杂自然度优化、
> 真实 API 接入（用户目标 10）。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-135 | **编号映射**：用户称「P3.4 对话生成基础模块」= 仓库 `ROADMAP.md` 的 **P4 第一步（Dialogue 部分）**；沿用 Q-37 口径，**按仓库编号实施、未改路线** | Q-37（第四轮扩展） | 已确认 |
| D-136 | **模块边界 INV-1 落地**：`DialogueDeps` 只拿只读端口 `PersonaReader` / `RelationshipReader` / `EvidenceReader` + `MemoryWriterPort`（仅 `write_simulated`） + `Storage` + `Provider`；核心流程**不调用** `memory`（用户目标 10：长期记忆留待 P4 记忆部分）。只读端口协议里**没有写方法**，技术层面堵死模拟污染现实资料（D-057） | INTERFACES §8.1；D-022 | 已实现，**待人工验收** |
| D-137 | **发言顺序策略 `least_recently_spoken`**：动态选「最近一次发言最早」的角色，`participants` 原始次序做 tiebreak；**非固定轮流**——三人对话会动态轮换而非永远 A→B→C；每轮必记 `selection_reason{rule, explanation}`（可解释、可回放，满足 AC-8 / D-033） | 用户目标 6；AC-8 | 已实现，**待人工验收** |
| D-138 | **SS-V3 档案归属校验**：`start_session` 校验会话引用的 `persona_profile_ids` / `relationship_profile_ids` 必须属于同一批参与者（`EntityReader` 以 `entity_id` 为键，故按 `entity_id` 校验；此为最小解释，记为遗留 **H-2** 待确认接口契约） | SS-V3；DATA_MODEL §4.9 | 已实现，**待人工验收** |
| D-139 | **上下文组装只读引用 + 不伪造**：`_assemble_context` 只读拉取人格/关系档案与历史轮次，只注入确实存在的档案内容；prompt 显式注入 `SIMULATION_NOTICE`（模拟声明）与冲突安全标记 `[CONFLICT_ASSERTION_GUARD]`；绝不把档案外信息当已知事实（用户目标 3） | 用户目标 3；D-050 | 已实现，**待人工验收** |
| D-140 | **模拟隔离三重保证**：① 只读端口协议（无写方法，D-057）② 存储层 namespace 网关（`ConversationSession` / `ConversationTurn` 只允许 `SIMULATED`，`start_session` 不可覆盖）③ prompt 显式声明模拟性质。对话前后 `EvidenceItem` / `PersonaProfile` / `RelationshipProfile` / `SourceEvent` 零变化（AC-13） | 用户目标 8；INV-1 | 已实现，**待人工验收** |
| D-141 | **失败纪律（P2.4）**：Provider 失败 / 空回复 / 无效参与者 / 会话或轮次持久化失败一律传播 `Err`，**绝不伪造成功回复**；`turn_count` 在失败路径保持 0，不落盘任何轮次 | 用户目标 7；P2.4 | 已实现，**待人工验收** |
| D-142 | **`namespace` 恒 `SIMULATED`，无需 schema 迁移**：`ConversationSession` / `ConversationTurn` 已在 `ENTITY_KINDS` 注册，`NAMESPACE_WRITE_GATE` 锁定 `SIMULATED`；`FILTER_KEYS` 不含 `session_id` / `index`，故按会话查轮次走「`_load_all` 后内存过滤」（与 evidence/relationship 同模式） | DATA_MODEL §4.9；D-064 | 已实现，**待人工验收** |
| D-143 | **本阶段不写长期记忆（用户目标 10）**：`commit_turn` 的 `generated_memory_ids=[]`，`memory` 端口仅注入备用、核心流程不调用；长期记忆自动写入留待 P4 记忆部分 | 用户目标 10 | 已实现，**待人工验收** |
| D-144 | **用户消息落盘**：`add_user_message` 把用户（操作者）输入落盘为 `speaker=__user__` 的轮次；用户不是 `PersonEntity` 参与者，不进入角色轮换（用户目标 2） | 用户目标 2；INTERFACES §8 | 已实现，**待人工验收** |
| D-145 | **三个新错误码** `VALIDATION.speaker_not_participant` / `VALIDATION.profile_mismatch` / `NOT_FOUND.session` 已落入代码并登记 `ERROR_HANDLING.md` §2.2，标记为**待人工确认 H-1**（与 D-107 / T-1 同口径，INTERFACES §8.2 已点名） | INTERFACES §8.2 | 待人工确认（H-1） |

### 2.21 P4 第二步：长期记忆基础模块（用户称 P3.5，编号差异见 Q-37）

> 范围：跨会话可持久化、可检索、可更新的记忆基础能力——创建/查询/更新（新版本）/
> 去重/冲突记录/过期/归档/删除、派生产物（DerivedArtifact）生命周期、严格模拟隔离。
> **不实现**真实研究、真实 API、向量检索、Provider 语义压缩（首版用确定性文本压缩）。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-146 | **编号映射**：用户称「P3.5 长期记忆基础模块」= 仓库 `ROADMAP.md` 的 **P4 第二步（Memory 部分）**；沿用 Q-37 口径，**按仓库编号实施、未改路线** | Q-37（第五轮扩展） | 已确认 |
| D-147 | **复用既有能力，零 schema 迁移**：`MemoryService` 复用 `SqliteStorage`、`ENTITY_KINDS` 已含 `MemoryItem` / `DerivedArtifact`、`NAMESPACE_WRITE_GATE` 对 `MemoryItem` 放开四命名空间、`FILTER_KEYS` 不含记忆业务字段故按项目/命名空间加载后内存过滤（与 evidence/persona/relationship/dialogue 同模式） | DATA_MODEL §4.10/§4.11；D-064 | 已实现，**待人工验收** |
| D-148 | **`MemoryPort` 骨架对齐 §9.3**：原 `memory.py` 的 `MemoryPort` 只是早于 INTERFACES §9.3 详细契约的占位骨架（方法名 `update` / `expire` 与 §9.3 的 `supersede` / `expire_sweep` 不符，且缺 `link_duplicate` / `link_conflict` / `consolidate` / `rebuild_derived` / `invalidate_derived`）；本阶段将协议对齐到 §9.3 并实现 `MemoryService`。**未改动** `Dialogue` 注入的窄端口 `MemoryWriterPort`（仍仅 `write_simulated`） | INTERFACES §9.3；D-022 | 已实现，**待人工验收** |
| D-149 | **`write_simulated` 签名对齐对话契约**：§9.3 写为 `write_simulated(ctx, turn_id, items)`，但 `Dialogue` 依赖的 `MemoryWriterPort.write_simulated(ctx, draft: dict)` 是已验收注入契约；本实现采用 `(ctx, draft)` 单字典形式，`draft` 内含 `source_turn_id`，`namespace` 与 `source_type` 由方法内部强制锁定为 `SIMULATED` / `SIMULATED_DIALOGUE` | INTERFACES §9.3；D-057 | 已实现，**待人工验收** |
| D-150 | **首版关键词检索（D-042 / D-074 退化方案）**：在内存中对已加载 `MemoryItem` 做子串匹配（多词 AND），排序 = 关键词命中率 × (0.5+0.5·salience) × 时间衰减，三者可解释；**不引入 FTS5 表**（避免未审查的 schema 迁移），FTS5 列为未来优化项 | INTERFACES §9.2；D-074 | 已实现，**待人工验收** |
| D-151 | **内容不可变（ME-V4）**：更新一律走 `supersede`（新建版本 + 旧版本置 `SUPERSEDED` 并回填 `superseded_by_memory_id`），**绝不原地改 `content`**；原 `MemoryPort.update` 的"原地 patch"语义被显式废除 | DATA_MODEL §4.10 ME-V4 | 已实现，**待人工验收** |
| D-152 | **去重（D-051 关联，不删除）**：`write` 命中相同（scope + scope_id + namespace + content）的 ACTIVE 记忆时返回已存在 id 并带 `MEMORY_DEDUP_HIT` 提示；**只做关联标记，不删被指为重复的那一条**（可追溯） | INTERFACES §9.3；D-051 | 已实现，**待人工验收** |
| D-153 | **冲突保留不裁决（D-013 / INV-3）**：`link_conflict` 双向登记 `conflict_with_memory_ids` 并把两条都置 `CONFLICTED`，**不合并、不择优**；检索默认只返回 `ACTIVE`（DATA_MODEL §7.6 #34），调用方显式把 `CONFLICTED` 纳入 `lifecycle_states` 时两条都返回（INTERFACES §9.3 "两条都返回"） | D-013；DATA_MODEL §7.6 #34 | 已实现，**待人工验收** |
| D-154 | **删除同事务失效派生产物（D-051）**：`delete` 把目标记忆置 `DELETED` + `purge_state=PENDING_PURGE`，**同一事务**内把所有 `source_memory_ids` 含该 id 的 `DerivedArtifact` 置 `INVALIDATED`；部分失败整体回滚；**永不物理删除记录行**（D-025，storage 层无物理删除） | DATA_MODEL §4.10；D-025 | 已实现，**待人工验收** |
| D-155 | **软删除宽限期与清理边界（Q-21 / 用户目标 7）**：`PENDING_PURGE` 记录经 30 天宽限期后由 `purge_expired` 置 `PURGED`（逻辑不可达）；本阶段**不物理删除行**（D-025），`content_ref` 外部 blob 在 v1 不存在（内容存于 payload），故"物理删除 content_ref"为 N/A——**不宣称已彻底删除**，完整物理清理列为未来项 | Q-21；用户目标 7 | 已实现，**待人工验收** |
| D-156 | **模拟隔离（INV-3 / 用户目标 8）**：`write_simulated` 强制 `namespace=SIMULATED` + `source_type=SIMULATED_DIALOGUE` 且要求 `source_turn_id`；`MemoryItem.referenced_evidence_ids` 只能引用已存在的 `EvidenceItem`（ME-V3，绝不创建）；检索结果原样带出 `namespace` 防止"洗白"（D-024）；模拟记忆写入前后 `EvidenceItem` / `PersonaProfile` / `RelationshipProfile` 零变化 | INV-3；ME-V3；D-024 | 已实现，**待人工验收** |
| D-157 | **对话衔接方式（用户目标 8 / §9.4）**：本阶段仅提供记忆检索（`search`）与写入（`write_simulated`）接口；**未经明确规则不把每轮对话自动写入长期记忆**（与 D-143 一致），自动写入留待明确规则/下一阶段 | INTERFACES §9.4；用户目标 8 | 已实现，**待人工验收** |
| D-158 | **四个新错误码** `VALIDATION.namespace_mismatch` / `VALIDATION.confidence_means_missing` / `VALIDATION.reference_missing` / `VALIDATION.self_reference` 已落入代码并登记 `ERROR_HANDLING.md` §2.2 | INTERFACES §9.3 | **已批准**（P3.5 验收，2026-10-14，H-3 → D-171） |
| D-159 | **新增三个枚举** `DerivedKind` / `DerivedStatus` / `PurgeState` 已补入 `enums.py`，取值与 `DATA_MODEL.md` §5.1 完全一致（旧 `MemoryPort` 骨架未含，属补齐已审数据模型，非新增字段） | DATA_MODEL §5.1 | 已实现，**待人工验收** |

### 2.21.1 P3.5 遗留问题验收结论（2026-10-14，人工拍板）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-171 | **H-3 同意保留** 4 个新错误码（`VALIDATION.namespace_mismatch` / `confidence_means_missing` / `reference_missing` / `self_reference`）：由"待人工确认"改为**已批准**，同步更新 `ERROR_HANDLING.md` §2.2 与变更历史（v0.1.9）+ `RUNNING.md` §5.8 | 用户拍板 | 已批准 |
| D-172 | **H-4 接受现状**：首版保留内存子串匹配关键词检索（D-074 退化方案，零第三方依赖），**FTS5 + trigram 留作后续升级**，本阶段不引入 | 用户拍板 | 已批准（接受） |
| D-173 | **H-5 / H-6 接受并记录**：① `MemoryPort` 骨架方法名曾与 §9.3 不符、本阶段已对齐（属补齐已审契约），保持现名；② `write_simulated` 采用 `(ctx, draft)` 单字典签名以兼容已验收的 `MemoryWriterPort`，与 §9.3 `(ctx, turn_id, items)` 写法并存，**不改**已验收的对话边界 | 用户拍板 | 已批准（接受） |

---

### 2.22 P4 第三步：对话 ↔ 长期记忆集成（用户称 P3.6，编号差异见 Q-37）

> 范围：打通「对话 → 上下文记忆检索 → 记忆写入 → 后续会话再检索」闭环。
> **不改已验收模块的内部规则**：Dialogue 按 §8.1/§8.2 早已列明的职责补齐
> "触发模拟记忆写入"，Memory 侧一行未改。**不实现**真实模型、真实检索、
> 对话结束后自动 consolidate、向量检索。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-174 | **编号映射**：用户称「P3.6 对话与长期记忆集成」= 仓库 `ROADMAP.md` **P4（对话与记忆）的第三步·集成**；沿用 Q-37 口径，**按仓库编号实施、未改路线** | Q-37（第六轮扩展） | 已确认 |
| D-175 | **确认集成前状态**：P3.4 的 `Dialogue` **未接入** P3.5 的 `Memory`——`draft_turn` 的 `referenced_memory_ids` 恒为 `[]`、`commit_turn` 的 `generated_memory_ids` 恒为 `[]`、`memory` 端口仅注入备用。P3.6 之前"对话与记忆"确实是两条独立链路 | 代码实测 | 已确认 |
| D-176 | **契约缺口（最小补齐）**：`INTERFACES §8.2` 要求 `draft_turn` 做"记忆检索只读"，但 `§8.1` 的注入端口只列了 `MemoryWriter`（无 `search`）。**最小改动**＝在 `dialogue.py` 新增独立只读协议 `MemoryReaderPort`（仅 `search`）并给 `DialogueDeps` 增加 `memory_reader`（默认 `None`）；**不改** `MemoryWriterPort`（仍仅 `write_simulated`，保住 D-057 元测试），**不扩写权限** | INTERFACES §8.1/§8.2 | 已实现，**待人工验收** |
| D-177 | **检索规则（用户目标 2/4/5）**：`draft_turn` 按两条规则检索，**两条都硬限 `namespace=SIMULATED` + `lifecycle=ACTIVE`**——**R1 本会话**：`scope=SESSION` 且 `scope_id=本会话`；**R2 跨会话**：`related_entity_ids` 与本会话参与者相交，**且会话有 `topic` 时内容须命中 topic 关键词**；**无 topic 时不取 R2**（避免无关记忆无条件进入上下文）。上限 5 条 | 用户目标 2/4/5；§8.2 | 已实现，**待人工验收** |
| D-178 | **检索失败不吞错（用户目标 7）**：记忆检索返回 `Err` 时 `draft_turn` 直接传播同一 `Err`，**不降级为"无记忆继续生成"**——否则会静默产出缺少既定上下文的发言 | 用户目标 7；P2.4 失败纪律 | 已实现，**待人工验收** |
| D-179 | **写入触发规则（用户目标 3，核心）**：**默认 `memory_write_policy="none"`，绝不把每轮对话写入长期记忆**。只有 ① 会话创建时显式选择 `"explicit"` **且** ② 调用方在 `commit_turn` 显式传入 `memory_candidates` 时才写。两者缺一即不写；有候选但策略为 `none` → `VALIDATION.gate_rejected`（**拒绝**，非静默忽略） | 用户目标 3；§8.2 | 已实现，**待人工验收** |
| D-180 | **不得升级为稳定人格特征（用户目标 3 / INV-1）**：候选 `scope` 必须是 `SESSION`（缺省即会话级）；`scope` 为 `ENTITY` / `RELATIONSHIP` / `PROJECT` 一律 `VALIDATION.gate_rejected`——单轮对话内容**不得**直接变成实体级/人格级稳定记忆。`scope_id` / `source_turn_id` / `related_entity_ids`（缺省=全体参与者）由 Dialogue 补齐；`namespace` / `source_type` 由 `write_simulated` 内部锁定 `SIMULATED` / `SIMULATED_DIALOGUE` | 用户目标 3；INV-1 | 已实现，**待人工验收** |
| D-181 | **写入与落盘同事务、顺序 Turn → Memory → Audit（用户目标 7/8）**：记忆写入在 `commit_turn` 的**同一事务**内完成（`SqliteStorage.transaction` 支持嵌套，只有最外层开事务）。任一步失败 → **整体回滚**，不出现"有对话无记忆"或"有记忆无出处"。写入后用**同事务**把 `generated_memory_ids` 回填到轮次行，保证幂等重放读到同一批 id | INTERFACES §8.2；用户目标 7/8 | 已实现，**待人工验收** |
| D-182 | **幂等（用户目标 8）**：同一 `(session_id, index)` 重复 `commit_turn` → 返回同一 `turn_id`（连同已记录的 `generated_memory_ids`），**不产生第二轮、不重复写记忆**。不同 `index` 的轮次各自写入自己的记忆（幂等只针对同一轮，不吞掉合法的新记忆） | INTERFACES §8.2；用户目标 8 | 已实现，**待人工验收** |
| D-183 | **零新增错误码**：集成闸门复用既有 `VALIDATION.gate_rejected`（策略未授权 / scope 非 SESSION）与 `VALIDATION.required_field`（候选缺必填 / 未注入写入端口），不引入新码，**不新增待确认项** | ERROR_HANDLING §2.2 | 已实现，**待人工验收** |
| D-184 | **`end_session` 不触发 `Memory.consolidate`**：`§8.2` 标为"（可选）"，且 consolidate 的 scope 映射规则未定义；本阶段**刻意不做**，记为遗留（见 `RUNNING.md` §5.9 H-8）。另：`write_simulated` 不走去重判定（沿用 P3.5 已验收行为），故跨轮同内容会各写一条，记为 H-9 | INTERFACES §8.2（可选）；P3.5 D-152 | 已实现（刻意不做），**待人工验收** |

---

### 2.22.1 P3.6 遗留问题验收结论（2026-10-14，人工拍板）

> 用户确认四项要求（跨会话闭环、写入可控、重试安全、数据边界）均已验证吻合，并对 P3.6 遗留问题逐条拍板。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-185 | **同意把 `MemoryReaderPort` 回填进 INTERFACES §8.1**：`DialogueDeps` 正式新增 `memory_reader: MemoryReader \| None`，与 `MemoryWriter` 并列。**只回填契约、不改代码**（代码已于 P3.6 落地）；契约与实现由此一致。同步 `INTERFACES.md` 版本 v0.1.3 与变更历史 | 用户拍板（2026-10-14） | 已批准 |
| D-186 | **H-1 同意保留** 3 个对话错误码（`VALIDATION.speaker_not_participant` / `VALIDATION.profile_mismatch` / `NOT_FOUND.session`（代码值 `NOT_FOUND.session`））：由"待人工确认"改为**已批准**，同步 `ERROR_HANDLING.md` §2.2 与变更历史 + `RUNNING.md` §5.7 | 用户拍板 | 已批准 |
| D-187 | **H-7 接受现状**：冲突记忆（`MemoryStatus.CONFLICTED`）默认不进对话上下文——沿用 `DATA_MODEL` §7.6 #34 / D-153 的检索契约（显式纳入 `CONFLICTED` 时两条都返回）。**不在本阶段改检索规则**；如需把争议带入上下文，须先写入 `INTERFACES.md` | 用户拍板 | 已批准（接受） |
| D-188 | **H-8 接受现状**：`end_session` **不**触发 `Memory.consolidate`（§8.2 标"可选"、scope 映射未定义）。保持刻意不做，记为未实现而非缺陷 | 用户拍板 | 已批准（接受） |
| D-189 | **H-9 接受现状**：`write_simulated` 不走去重判定（沿用 P3.5 已验收行为），跨轮同内容会各写一条；同轮重放由 `(session_id, index)` 幂等挡住。**不改** | 用户拍板 | 已批准（接受） |
| D-190 | **下一阶段 = ROADMAP P5（评测与加固）**：按 `ROADMAP.md` §7 执行——`check_invariants` 覆盖 INV-1~INV-4（AC-13/14/15）、预算耗尽暂停（AC-12）、中断后 `resume` 不重复（AC-11）、`check_dependencies` 无循环（AC-20）、全流程离线（AC-19）、无真实人物硬编码（AC-16）。**用户确认纳入本阶段执行**（本次以"端到端集成验收"形式先落 `Evaluation` 核心 + 集成测试） | ROADMAP §7；用户指令 | 已确认 |

### 2.23 P2 的 Research 部分：最小可运行实现（用户称 H-11A，编号差异见 Q-37）

> 范围：把 `Research` 从「只有 Protocol」推进为**可执行、可测试**的最小模块
> （`persona/modules/research.py` 的 `ResearchService`），为后续 H-10 业务编排器提供稳定接口。
> **不实现**真实检索/搜索 API、不做正文抓取、不承担任务状态机与预算账本。
> 已验收模块**一行未改**（`evidence.py` / `storage` / `providers` 保持原样）。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-191 | **编号映射**：用户称「H-11A Research 模块最小可运行实现」= 仓库 `ROADMAP.md` **P2「研究 + 证据管线」的 Research 部分**（`ROADMAP §4` 交付物之一，P2 进度记账里唯一未做的模块）；沿用 Q-37 口径，**按仓库编号实施、未改路线** | Q-37（第八轮扩展）；ROADMAP §4 | 已确认 |
| D-192 | **保留 `ResearchPort` 协议、新增 `ResearchService` 实现**：协议签名按 `INTERFACES §5.2` **更正**（旧骨架 `plan_queries(ctx, entity_ids)` / `collect(ctx, query)` / `register(ctx, raw_results)` 与文档不一致），与 `Evidence` 的 D-110 同一处理口径；该协议**没有任何既有调用方**，对齐不破坏任何代码 | INTERFACES §5.2；D-110 | 已实现，**待人工验收** |
| D-193 | **数据形状按契约落地**：`PersonQueryTarget` / `PlannedQuery`（含 `target_entity_ids`）/ `CollectOutcome` / `RankedCandidate` / `RejectedCandidate` / `RankedCandidates` / `ResearchRegisterOutcome` / `ResearchOutput`，字段与 §5.2 一致 | INTERFACES §5.2 | 已实现，**待人工验收** |
| D-194 | **只注入 `SourceRegistrarPort`（仅 `register_source`）**：Research 拿不到 `add_evidence` / `record_inference` / 存储仓储层，**F-10「Research → Evidence 表直接写入」在类型层面被堵死**；`ResearchService` 全体公开方法里不存在任何证据写方法 | INTERFACES §13 F-10；D-031 | 已实现，**待人工验收** |
| D-195 | **不注入 `Storage`**：Research 不落任何自己的过程记录——`ENTITY_KINDS` 里没有研究过程实体，**不为此新增数据模型**；需要留痕的数据一律经 `Evidence` 落库，检查点归 Orchestrator | DATA_MODEL §5.1；用户目标 7 | 已实现，**待人工验收** |
| D-196 | **`plan_queries` 确定性模板**（不调模型，Q-29）：每目标 1~2 条（基础辨识词 + 有 `topic` 时的话题词）；检索词只含名字/作品/话题，**不内嵌任何预设结论**；只做形状校验（目标非空、`entity_id`、名字来自 `display_name` 或首个别名） | INTERFACES §5.2；Q-29 | 已实现，**待人工验收** |
| D-197 | **`collect`：0 结果不是错误**——返回 `Ok(fetch_status=NO_RESULT)` + `NO_RESULT` 提示；`Provider` 的错误（`EXTERNAL_*` / `BUDGET_EXCEEDED`）**原样传播，绝不吞**（P2.4 失败纪律） | ERROR_HANDLING §2.3 | 已实现，**待人工验收** |
| D-198 | **`filter_and_rank`：空输入不是失败**（返回空 `accepted` + `EMPTY_INPUT` 提示）；**被拒项必须记录原因**（`EMPTY_TITLE` / `MISSING_LOCATOR` / `DUPLICATE_IN_BATCH` / `INVALID_MATERIAL`）；排序为**确定性结构完整度**（title/url/snippet/published_at 是否齐备），并列按去重键稳定排序；**可选关键字参数 `target_entity_ids`** 用于把对象边界带到 `Evidence`（对契约签名的**向后兼容扩展**，不传时行为不变） | INTERFACES §5.2；用户目标 5 | 已实现，**待人工验收** |
| D-199 | **`register` 只经 `Evidence.register_source`**：逐条登记，**任一条失败即整体传播该错误**（不静默跳过、不伪造部分成功）；登记幂等（同键命中去重返回既有 id，`dedup_hits` 计数）；`event_ids` 恒空（`SourceEvent` 切分是 `Evidence` 的事） | INTERFACES §5.2；用户目标 4/6 | 已实现，**待人工验收** |
| D-200 | **`run` 的失败纪律**：单个 `PlannedQuery` 检索失败 → 记录 `QUERY_FAILED` 提示并**继续**其他查询；**全部失败 → 返回 `Err`（上报，不伪装成功）**；`BUDGET_EXCEEDED` → **立即传播**（这是"暂停"信号，不是"跳过继续"）；登记失败 → 直接传播。`run(ctx, targets, budget=None)` 的 `budget` **仅为签名兼容保留，不查询、不扣减**（预算账本归 Orchestrator / `BudgetGate`），`budget_used` 是 Provider 如实回报的用量之和 | INTERFACES §5.2；用户目标 6/7 | 已实现，**待人工验收** |
| D-201 | **隔离**：Research 只产出 `Source`（命名空间恒 `PUBLIC_SOURCE`，存储层闸门仍在），**不产生** `EvidenceItem` / `SourceEvent` / 档案 / 会话 / 记忆；不触碰 `SIMULATED` 内容；模拟内容写 `Source` 仍被存储层拒绝（`VALIDATION.namespace_forbidden`） | INV-1~INV-3；用户目标 5 | 已实现，**待人工验收** |
| D-202 | **`fetch_status=NO_RESULT` 用模块常量表达**：`FetchStatus`（DATA_MODEL §4.3）只有 OK/PARTIAL/FAILED/BLOCKED，**不含 `NO_RESULT`**；实现定义 `FETCH_NO_RESULT = "NO_RESULT"`（模块级），**不改动已审枚举** | DATA_MODEL §4.3 vs INTERFACES §5.2 | 已实现，**待人工验收** |
| D-203 | **零新增错误码**：`INTERFACES §5.2` 把空目标列为 `VALIDATION.empty_targets`，但 `ERROR_HANDLING §2.2` / `ErrorCode` 未登记该码 → 复用既有 `VALIDATION.required_field`；查询为空复用 `VALIDATION.query_empty`；"实体是否存在"下沉到 `Evidence`（`VALIDATION.entity_missing`）。**不新增待确认项** | ERROR_HANDLING §2.2 | 已实现，**待人工验收** |
| D-204 | **INV-4 兼容：`RankedCandidate.score` → `rank_weight`**。`no_intimacy_field` 用 `.*(score\|closeness\|intimacy).*` 扫描**所有模块**的类级字段名（防"关系/人格打分"），原字段名会被判违规。这里是"来源结构完整度"，与亲密/评价无关，**改字段名而不是放宽已验收的不变式** | `evaluation.py` INV-4；AC-13 | 已实现，**待人工验收** |
| D-205 | **Mock Research Provider 落在测试侧**：不新增产品内 Provider，也不改已验收的 `providers/mock.py`；测试内定义确定性离线的 `MockResearchProvider`（固定研究材料 + 可注入失败），并额外用既有 `FakeProvider`（经 `search_metadata={"fixture_key": ...}` 互操作）/ `RecordingProvider` / `FailingProvider` / `OfflineGuardProvider` 验证复用与真离线 | INTERFACES §2.6；用户目标 3 | 已实现，**待人工验收** |

### 2.24 H-10A：业务编排型 Orchestrator（用户称 H-10A）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-206 | **编号映射**：用户称「H-10A 新增业务编排型 Orchestrator」= 仓库 `ROADMAP.md` §4 里长期悬置的「业务编排型 Orchestrator（H-10）」。沿用 Q-37 口径，**按仓库编号实施、未改路线**；另在 `INTERFACES` §12.7 登记契约 | Q-37（第九轮扩展）；ROADMAP §4 | 已确认 |
| D-207 | **保留既有任务 Orchestrator，新增独立业务编排器**：`orchestrator/business.py::BusinessOrchestrator`。既有 `TaskService` / `TaskRunner` / `BudgetGate` / 检查点**一行未改、公共接口不变**；业务编排器实现 `executor.UnitHandler`（`plan` / `execute`）被 `TaskRunner` 驱动 | 用户目标 1/4；INTERFACES §12 | 已实现，**待人工验收** |
| D-208 | **不复制第二套六态机 / 预算账本 / 检查点**：状态转换、预算预占与记账、检查点落盘、重试与 `FAILED` 置位全部由既有 `TaskRunner` 完成。业务编排器源码内不含 `can_transition` / `TaskState` / `gate_for` / `BudgetGate` / `.complete(` / `.pause(` / `.fail(`，并由测试静态断言 | 用户目标 4；D-052 | 已实现，**待人工验收** |
| D-209 | **五个阶段复用 `TaskType` 同名 stage**：`RESEARCH` → `EVIDENCE_EXTRACT` → `PERSONA_DISTILL` → `RELATIONSHIP_DISTILL` → `DIALOGUE_RUN` 作为 `stage_sequence` 与 `WorkUnit.stage`。**缺口 G-1**：`TaskType` 没有"全流程"成员 → 最小兼容用 `TaskType.RESEARCH` 作跨阶段任务类型（可由请求覆盖），真实语义由 `stage_sequence` 承载 | INTERFACES §12.3 | 已实现，**待人工验收** |
| D-210 | **阶段间数据只经已落盘的检查点 `artifact_ids` 传递**（`source_ids` / `evidence_ids` / `persona_profile_ids` / `relationship_profile_ids` / `session_ids` / `turn_ids`）。不用内存私有通道，因此 `resume` 之后后续阶段仍能拿到前序产出；`plan(task)` 顺带记下 `task_id` 供 `execute` 读检查点 | 用户目标 5；D-061 | 已实现，**待人工验收** |
| D-211 | **证据 claim 不由编排器生成（缺口 G-2）**：「来源 → 证据」的自动抽取依赖正文抓取（未实现，H-17 同源）。编排器**不伪造 claim**——证据由调用方以 `EvidenceSeed{source_ref, draft}` 显式提供；编排器只做①把 `source_ref`（`canonical_key` / `url`）解析为本次研究**实际登记**的 `Source` ②校验绑定成功。解析失败即 `Err(VALIDATION.dangling_reference)` | 用户目标 2；AC-1 | 已实现，**待人工验收** |
| D-212 | **证据幂等守卫**：`Evidence.add_evidence` 本身不去重，重跑会产生重复证据。编排器在登记前按「同 `source_id` + 同 `claim` + 同主体集合」查已有证据，命中则复用原 ID 并记 `EVIDENCE_DEDUP_HIT` 提示（**不改 `Evidence` 已验收实现**） | 用户目标 5；D-051 同精神 | 已实现，**待人工验收** |
| D-213 | **档案写入幂等**：`item_id` 由证据 ID 推导（`pi_{evidence_id}`）而非随机生成——条目身份可复现，`PersonaService` / `RelationshipService` 才能判定"内容与最新版一致"从而拒绝空转版本（`STATE_CONFLICT.no_new_evidence` → 复用旧版，记 `ALREADY_CURRENT`）。**重试不产生重复档案版本** | 用户目标 5；D-112 | 已实现，**待人工验收** |
| D-214 | **Persona 只搬运证据，不做理解**：按 `EvidenceDimension` 机械归类（BACKGROUND→background、TRAIT→traits/single_events、HABIT→habits、SPEECH_STYLE→expression_style、EXPERIENCE→experiences、CANON_FACT→canon_boundary）。**AC-7**：`stability` 为 `SINGLE` / `UNKNOWN` 的 TRAIT 证据进 `single_events`，绝不进 `traits`（PF-V2 / D-012）。无证据时留 `open_questions`，**不补全不臆测**（D-020） | 用户目标 2；AC-7；D-020 | 已实现，**待人工验收** |
| D-215 | **Relationship 只用 `RELATION` 维度的证据**：绝不从两份人物档案拼关系（RL-V6）。≥3 人时 `cardinality=GROUP` 且**必须**给 `group_dynamics`（RL-V3）；编排器**不做语义判断**，不把"共同出现于同一材料"断言成联盟/冷落/传话——群体级证据落在 `shared_experiences`，`group_dynamics` 以 `note` 如实说明当前填充程度 | 用户目标 2；RL-V3/RL-V6 | 已实现，**待人工验收** |
| D-216 | **PF-V5 / D-115 需要 `entity_type`**：虚构角色档案必须声明 `applicable_variant_ids`（且含自身），真人必须为空。编排器**不靠猜**——`BusinessDeps.storage` 只读取 `PersonEntity.entity_type` 后据此判定；除此之外编排器不碰存储 | PF-V5；D-115/D-120 | 已实现，**待人工验收** |
| D-217 | **Dialogue 用已有档案生成模拟对话**：`persona_profile_ids` 传参与者 `entity_id`（SS-V3 最小解释），`relationship_profile_ids` 传关系档案 ID（`rlp_`）。模拟产物只进 `Conversation*` 与 `MemoryItem(SIMULATED)`；**默认 `memory_write_policy=none`**，不自动把每轮写进长期记忆 | 用户目标 2；D-057；D-143 | 已实现，**待人工验收** |
| D-218 | **结果三态自述，部分成功绝不成"全流程成功"**：`BusinessRunResult.status ∈ {SUCCEEDED, PARTIAL, PAUSED, FAILED}`。只有**五步全部执行且任务 `COMPLETED` 且无错误**才 `SUCCEEDED`；步骤状态细分 `EXECUTED` / `ALREADY_CURRENT` / `SKIPPED` / `FAILED` / `NOT_REACHED`，`is_complete()` 只在 `SUCCEEDED` 为真 | 用户目标 3 | 已实现，**待人工验收** |
| D-219 | **失败即上报，后续步骤不伪造成功**：任一步骤 `Err` → 由 `TaskRunner` 置 `FAILED` 并返回 `Err`；后续阶段状态为 `NOT_REACHED`（不是 `EXECUTED`）。`BUDGET_EXCEEDED` 由既有闸门转成 `PAUSED`（不是失败），提额后 `resume` 继续 | 用户目标 5；D-043 | 已实现，**待人工验收** |
| D-220 | **`resume` 需重新提供请求（缺口 G-3）**：检查点只存产出 ID，不存业务请求。最小兼容方案：`resume(ctx, task_id, request)` 由调用方重传；已完成单元由检查点幂等跳过（AC-11）。**不为跨进程恢复新增数据模型** | 用户目标 4/5；AC-11 | 已实现，**待人工验收** |

### 2.25 G-2：来源材料 → 候选 claim 的自动抽取

> ⚠️ **编号冲突**：仓库里有**两个** G-2。`RUNNING.md` §5.8 的 G-2 = P3.3 遗留
> 「群体子结构关系无法表达」（D-131 已接受）；本节做的是 `RUNNING.md` §5.12 的
> G-2 = H-10A 遗留「来源 → 证据自动抽取未实现」。两者无关，冲突已登记（Q-37 第十轮）。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-221 | **新增 `persona/modules/extraction.py` 作为抽取层**，不重写已验收的 `Research` / `Evidence` / `Persona` / `Relationship` / 任务管理。原 Protocol 与既有实现全部保留 | 用户限制；D-207 同精神 | 已实现，**待人工验收** |
| D-222 | **核心数据缺口**：此前 `ResearchOutput` **只返回 `source_ids`（来源元数据）**，把 `SearchResult.snippet`（检索摘要）丢掉了——下游根本没有材料可抽。现给 `ResearchOutput` / `ResearchRegisterOutcome` 增加 `materials: list[SourceMaterial]`（`SourceDraft` 仍只搬元数据，职责不变） | 用户目标 2；INTERFACES §5.3 | 已实现，**待人工验收** |
| D-223 | **`SourceMaterial` 下沉到 `persona/core/types.py`**：`Research`（产出方）与抽取器（消费方）同为 L4，ARCHITECTURE §3 规定同层互不调用。共享形状放 core 才能在不出循环依赖的前提下传材料 | ARCHITECTURE §3；F-11 | 已实现，**待人工验收** |
| D-224 | **抽取器拿不到 Evidence**：`extraction.py` 不 import `evidence` 模块、不含 `add_evidence` / `record_inference` 调用（AST 断言）。它在结构上就无法把候选直接标记成"已验证证据" | 用户目标 5；F-10 同精神 | 已实现，**待人工验收** |
| D-225 | **三种 claim 机械区分**：VERBATIM 必须带可在材料里逐字找到的 `verbatim_quote`（否则 `QUOTE_NOT_IN_MATERIAL`）；归纳不带摘录；INFERENCE 走 `record_inference` 且必须解析出 `derivation_keys` 对应的根证据，**无根即丢弃**（EV-V3 / D-014）。INFERENCE 带摘录 → `INFERENCE_WITH_QUOTE` 拒收——**推断没有"原话"** | 用户目标 4；EV-V3 | 已实现，**待人工验收** |
| D-226 | **等级沿用既有定义，且不等于真实性**：`tier` 用 `EvidenceTier` 原值，编排器**不提升**候选的可信度（原样搬进 `EvidenceDraft`，只追加 `extract:<method>` 标签） | 用户目标 4；D-009 / D-010 | 已实现，**待人工验收** |
| D-227 | **拒收不静默**：8 种拒收原因（`EMPTY_MATERIAL` / `EMPTY_CLAIM` / `NO_SUBJECT` / `QUOTE_NOT_IN_MATERIAL` / `UNKNOWN_TIER` / `UNKNOWN_DIMENSION` / `CONFIDENCE_OUT_OF_RANGE` / `INFERENCE_WITH_QUOTE`）全部进 `ExtractionOutcome.rejected`，编排器转成 `CLAIM_REJECTED` 提示。**不降级登记、不静默丢弃** | 用户目标 3/9 | 已实现，**待人工验收** |
| D-228 | **冲突保留、不合并**：去重只认「同 source_id + 同 claim + 同主体」的**精确**匹配；说法不同就各自登记成独立证据。登记后请 `Evidence.detect_conflicts` 做确定性结构检测并记 `ConflictRecord`（**只记录不裁决**，D-055），冲突 ID 进 `BusinessRunResult.conflict_ids` | 用户目标 6；AC-15 | 已实现，**待人工验收** |
| D-229 | **推断也要幂等**：`record_inference` 本身不去重，重跑会产生第二条内容一样的推断。编排器按「同 claim + 同主体 + tier=INFERENCE」查已有记录后复用——**不改已验收的 `Evidence`** | 用户目标 9；D-212 同精神 | 已实现，**待人工验收** |
| D-230 | **材料无法落检查点（新缺口 G-4）**：`TaskService.resume` 把 `artifact_ids` 的**每一项**当实体引用校验（`VALIDATION.artifact_missing`），材料正文不是 ID，塞进去会让续跑直接失败（实测）。既有任务模块不在改动范围内，故材料改为**进程内**缓存（按 `task_id`）；缓存失效时以**幂等重跑研究**回补（`register_source` 去重，不产生重复来源），代价是多一次检索预算 | 实测；D-210 | 已实现，**待人工验收** |
| D-231 | **`MockClaimExtractor` 是确定性查表**（按 `canonical_key` 取 fixture），产出一律打 `mock-fixture` 标签。**它证明的是链路连通，不代表真实材料抽取能力已验证** | 用户目标 7；AC-19 | 已实现，**待人工验收** |
| D-232 | **`ProviderClaimExtractor` 只定义契约与错误处理，本阶段不接真实模型**：`Provider.chat` 返回 `Err` **原样传播**；返回非约定 JSON → `Err(EXTERNAL_UNAVAILABLE)`（**不谎报"抽取成功但零结果"**）；单条取值非法 → 记 `rejected` 并继续；缺 `material_key` → 无法溯源，拒收 | 用户目标 10；D-054 | 已实现，**待人工验收** |
| D-233 | **业务入口不再要求 claim**：`BusinessRequest.evidence_seeds` 变为**可选**的显式覆盖通道（给出时跳过自动抽取）。不传 + 有抽取器 → 走 Research → 抽取 → 登记；不传 + 无抽取器 → `SKIPPED` + `PARTIAL`，**不伪造** | 用户目标 1/8 | 已实现，**待人工验收** |
| D-234 | **两个既有测试被"改口径"而非删除**：`test_missing_evidence_seeds_yields_partial_not_success` → `test_no_materials_and_no_seeds_yields_partial_not_success`（改断言"真正无材料"）；`test_ac1_requires_claim_input_not_only_two_names` → `test_ac1_two_names_alone_now_produce_evidence_via_extraction`（断言两个人名即可成功）。**它们断言的旧行为正是 G-2 要移除的东西**；改后断言强度未降反升，已在 docstring 中记录改动原因 | 用户限制"不得放宽断言制造通过" | 已实现，**待人工验收** |
| D-235 | **H-17 仍未关闭**：材料只有检索摘要 `snippet`，**没有正文全文**。真实覆盖面受检索摘要长度限制；要真正提升召回需先做正文抓取与切分 | 用户目标 10；H-17 | **遗留** |

### 2.26 G-4：基于证据的关系语义分析

> ⚠️ **编号冲突**：仓库 `RUNNING.md` §5.8 的 G-4 = P3.3 遗留「群体子结构关系无法表达」（D-131 已接受，与关系维度无关）；本节做的是 `RUNNING.md` §5.12 的 G-4 = H-10A 遗留「关系维度证据已落库但编排器未做语义研判」。两者无关，沿用 G-2（§2.25）的处理方式点名区分。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-236 | **新增 `persona/modules/relationship_analysis.py` 作为关系研判层**，不重写已验收的 `Research` / `Evidence` / `Persona` / `Relationship` / 任务管理。原 Protocol 与既有实现全部保留；本模块**不写任何业务表**，落盘仍由 `BusinessOrchestrator` 负责（与 `ClaimExtractorPort` 同一套路：分析器只产候选研判，不登记） | 用户限制；D-207 同精神 | 已实现，**待人工验收** |
| D-237 | **`RelationshipAnalyzerPort` 端口（与 `ClaimExtractorPort` 同一抽象层级）**：`analyze(ctx, request) -> Result[RelationshipAnalysisOutcome]`。实现不持有 `Evidence` / `Relationship` 的引用（仅 `TYPE_CHECKING` 引入 `EvidenceView`，运行期不耦合），结构上无法反向写证据/档案；`Relationship` 模块也**绝不被研判层触发去启动研究流程**（F-2 同精神） | 用户目标 9；ARCHITECTURE §3；F-1/F-2 | 已实现，**待人工验收** |
| D-238 | **`RuleRelationshipAnalyzer` 是离线确定性规则，非语义模型**：① A→B 与 B→A 分两个 `PerspectiveBlock` 放（RL-V5，绝不融合成"综合定论"）；② 证据为双方共同记录时，两个方向各自保留同一证据引用并标注"未单独确认单向态度"，不强行对称；③ 单次行为（SINGLE）不固化为"互动模式"（D-012 同精神）；④ 任何形状都无 score/closeness/intimacy 字段（RL-V2 / D-048） | 用户目标 2/3；RL-V2/RL-V5；D-048/D-012 | 已实现，**待人工验收** |
| D-239 | **群体互动红线（结盟/排斥/传话）——只接受明确标记**：三个板块（alliances / exclusion / relay）仅当证据文本命中 `_ALLIANCE_MARKERS` / `_EXCLUSION_MARKERS` / `_RELAY_MARKERS` 才落记录；**仅共同出现/共同活动（命中 `_CO_OCCURRENCE_MARKERS` 且未命中上述三类）不足以判定**，一律留空并记 `CO_OCCURRENCE_NOT_ALLIANCE` 提示（D-215 硬约束的落地）；`group_dynamics` 的 `note` 如实说明当前填充程度 | 用户目标 4；RL-V3；D-215 | 已实现，**待人工验收** |
| D-240 | **`MockRelationshipAnalyzer` 是确定性查表**（按 `scenario_key()` 取 fixture），产出一律 `method=MOCK_FIXTURE`、`is_semantic_model=False`。**它证明的是链路连通（A→B/B→A 不对称、矛盾、时间变化可控），不代表真实关系研判能力已验证**。未命中场景键返回"证据不足"安全空结果，不崩溃不伪造。`ProviderRelationshipAnalyzer` 仅定义契约与错误处理，本阶段不接真实模型：被调用即返回 `Err(EXTERNAL_UNAVAILABLE)`，绝不谎报"研判成功但零结果" | 用户目标 7/10；D-231 同精神；D-054 | 已实现，**待人工验收** |
| D-241 | **编排器接线 `_step_relationship`**：仅在 `BusinessDeps.relationship_analyzer is not None` **且** 存在 RELATION 证据时走 `_draft_from_analysis`（调用 analyzer）；否则走 `_relationship_draft_fallback`（**逐字保留 G-4 之前的行为**，使既有 `test_ac2_*` 仍全绿，零回归）。analyzer 注入点是 `BusinessDeps.relationship_analyzer`，**不在编排器源码里硬编码具体实现**（测试静态断言 `MockRelationshipAnalyzer` / `RuleRelationshipAnalyzer` 字符串未出现） | 用户目标 1/9；AC-2 回归纪律 | 已实现，**待人工验收** |
| D-242 | **G-4 关闭 H-10 的 AC-2 缺口、并顺带关闭 G-5**：此前 Relationship 维度证据已落库但编排器未做语义研判（D-215 只约束"不看档案拼关系 + 群体级证据落 shared_experiences"）。现注入 analyzer 后，AC-2「三人产生群体关系档案 + group_dynamics 内容不伪造」满足；RL-V3（≥3 人必须有 group_dynamics）、RL-V5（不跨视角综合）、RL-V6（每条目都带 EvidenceItem 引用、绝不从两份档案推断）全部合规 | 用户目标 2/10；RL-V3/V5/V6；D-215 | 已实现，**待人工验收** |
| D-243 | **不破坏已验收数据模型与校验**：`_draft_from_analysis` 产出的 `RelationshipDraft` 条目仍走 `RelationshipService` 既有 `_validate_item` / RL-V6 悬空引用守卫——编造的证据 ID（不存在/非 PUBLIC 命名空间/版本不一致）会被判 `VALIDATION_DANGLING_REFERENCE` 而拒绝（`test_analyzer_fake_evidence_ref_rejected` 实测 RUN_FAILED + STEP_FAILED）。规则分析器自身也不生成任何伪造引用：每个条目必带 `evidence_refs`，引用 ID 全部来自输入证据（结构上不可能凭空生成关系） | 用户目标 8/9；RL-V4/RL-V6；D-215 | 已实现，**待人工验收** |
| D-244 | **能力边界明示**：`RelationshipAnalysisOutcome.is_semantic_model` 字段对所有本阶段实现恒为 `False`（Rule / Mock / Provider）；`method` 区分 `RULE_BASED_OFFLINE` / `MOCK_FIXTURE` / `MODEL`。任何人不得以本阶段规则分析器的"通过"宣称"真实语义模型已验证"。未来替换实现只需在 `BusinessDeps` 注入新的 `RelationshipAnalyzerPort` 实现，接口与产出形状不变 | 用户目标 9；D-231 同精神 | 已实现，**待人工验收** |

---

## 3. 尚未确定的开放问题

> ⚠️ 以下问题是**真的没定**。任何人不得以"我猜是这样"为由自行实现。
> 需要人工拍板后，才有资格转成 §2 的 `D-0xx`。

| 编号 | 问题 | 为什么难定 | 影响范围 | 阻塞哪个阶段 |
| --- | --- | --- | --- | --- |
| Q-01 | 人物重名时，是**中断询问用户**，还是**自动猜测 + 记录不确定性**？ | 前者打断"零审核"体验，后者可能搞错人 | `Research`、`Person` | P2 |
| Q-02 | 公开资料怎么拿？只用官方/授权 API，还是允许网页抓取？是否遵守 robots / ToS？是否保存页面快照？ | 涉及合规与法律风险 | `Research`、`Source` | P2 |
| Q-03 | 是否支持**在世人物**？隐私边界怎么划？是否有"仅限公众人物/已故人物"的限制？ | 涉及伦理与法律风险 | 全局 | P2 前 |
| ~~Q-04~~ | 存储选型 | ✅ **已决定 → D-041 SQLite** | `Storage` | 已解决 |
| Q-05 | `Provider` 需要哪些能力？只有 `search` + `chat` 够吗？要不要结构化输出/JSON 模式支持？ | 影响接口设计；第一版已按 `chat` + `search` + 可选 JSON Schema 设计 | `Provider` | P1.2-B |
| ~~Q-06~~ | 记忆检索方式 | ✅ **已决定 → D-042 关键词检索，不引入向量** | `Memory`、`Provider` | 已解决 |
| ~~Q-07~~ | 预算单位与超预算行为 | ✅ **已决定 → D-043 暂停 + 保存进度 + 可恢复** | `Orchestrator`、`Provider` | 已解决 |
| Q-08 | 存在冲突时，**对话中**该怎么表现？让角色各自坚持？还是回避该话题？ | 影响对话自然度与安全性 | `Dialogue` | P4 |
| Q-09 | 模拟记忆的**留存期限**与自动清理策略？默认永久保存还是设 TTL？ | 影响存储与隐私 | `Memory` | P4 |
| Q-10 | **没有标准答案**的前提下，怎么给质量打分？用 rubric 人工评？还是用可测的结构性指标（覆盖度、可追溯率、冲突保留率）？ | 质量难以自动量化 | `Evaluation` | P5 |
| Q-11 | 三人以上群体互动要建模到**多深**？只做"联盟/站队"，还是要建模"传话/冷落/ subgroup"？ | 复杂度差异很大 | `Relationship` | P3 |
| Q-12 | 是否允许**用户提供私有资料**（非公开）作为来源？如何与公开资料区分？ | 影响来源模型与合规 | `Source`、`Research` | P2 |
| ~~Q-13~~ | 实现语言 | ✅ **已决定 → D-040 Python 3.12+** | 全局 | 已解决 |
| Q-14 | 任务恢复的粒度：**阶段级**还是**证据级**？ | 粒度越细，恢复越灵活，实现越复杂。第一版采用**阶段级 + 阶段内单元清单**（另见 Q-34） | `Orchestrator` | P5 |
| Q-37 | **阶段编号差异**：用户口头称之为"P3.1 来源与证据管理 / P3.2 人格档案基础 / P3.3 关系档案基础"，但 `ROADMAP.md` 规定 **P2 = 研究 + 证据管线**（Research + Evidence + Storage + Provider）、**P3 = 人物档案 + 关系档案**（Persona + Relationship，未细分 .1/.2/.3）。三轮均已按**仓库编号**实施（P3.1 → P2 证据管线第一步、P3.2 → P3 第一步 Persona 部分、P3.3 → P3 第二步 Relationship 部分）、**未改路线**；**第四轮扩展（2026-10-14）**：用户口头「P3.4 对话生成基础模块」= 仓库 **P4 第一步的 Dialogue 部分**，同样按仓库编号实施、未改路线；**第五轮扩展（2026-10-14）**：用户口头「P3.5 长期记忆基础模块」= 仓库 **P4 第二步的 Memory 部分**（Dialogue + Memory 共同构成 ROADMAP 的 P4「对话与记忆」），同样按仓库编号实施、未改路线；**第六轮扩展（2026-10-14）**：用户口头「P3.6 对话与长期记忆集成」= 仓库 **P4 的第三步·对话↔记忆集成**（P4 的交付物是 Dialogue + Memory 两者的闭环），同样按仓库编号实施、未改路线；**第七轮扩展（2026-10-14）**：用户口头「下一阶段 / P3.7 端到端集成验收」= 仓库 **P5（评测与加固）的核心部分**（不变式断言 + 依赖检查 + 集成测试；P4 之后 ROADMAP 规定的下一阶段），按仓库编号实施、未改路线；**第八轮扩展（2026-10-14）**：用户口头「H-11A Research 模块最小可运行实现」= 仓库 **P2 的 Research 部分**（P2 交付物 = `Research` + `Evidence` + `Storage` + `Provider`，其中 Research 是 P2 进度记账里唯一未做的模块），同样按仓库编号实施、未改路线；**第九轮扩展（2026-10-14）**：用户口头「H-10A 新增业务编排型 Orchestrator」= 仓库 `ROADMAP.md` §4 里登记为「业务编排型 Orchestrator（H-10，属新架构须先拍板）」的那一项（其作用是打通 AC-1 / AC-2），同样按仓库编号实施、未改路线；**第十轮扩展（2026-10-14）**：用户口头「G-2 实现来源材料到候选 claim 的自动抽取」= 仓库 `RUNNING.md` §5.12 里 H-10A 遗留清单中的 **G-2（源→证据自动抽取未实现）**——**注意与 `RUNNING.md` §5.8 里 P3.3 遗留的同名 G-2（群体子结构关系无法表达，D-131 已接受）无关**，属**同一编号、两个不同遗留项**，本步未改任何编号，只在 §2.25 与各文档点名区分 | 两种口径长期并存会让文档与对话对不上；需要明确是改 ROADMAP 编号，还是在文档里固定"用户口头编号 → 仓库编号"映射表 | `Evidence`、`Persona`、`Relationship`、`Dialogue`、`Memory`、`Evaluation`、`Research`、文档体系 | 待用户拍板 |

### 3.1 拍板优先级（已更新）

**已解决**：Q-04（SQLite）、Q-06（关键词检索）、Q-07（超预算暂停）、Q-13（Python 3.12+）、
**Q-15**（改编默认分离 → D-075）、**Q-22**（档案默认不跨版本共用 → D-076）、
**Q-27**（不引入 ORM/Pydantic → D-073）、**Q-30**（FTS5 + trigram → D-074）。

**仍建议优先拍板**：

1. **Q-03（在世人物与隐私边界）** —— 涉及风险，越早越好
2. **Q-02（资料获取渠道与 robots / ToS）** —— 涉及合规
3. **Q-01（重名处理）** —— 影响 P2 研究管线的用户流程

其余问题可以在对应阶段开工前再定。

### 3.2 后续问题的存放位置（更正说明）

> 📌 **更正**：v0.2 的变更历史写「新增 §3.2 / §3.3」，实际上**本文件并没有这两节**。
> Q-15 ~ Q-26 实际记录在 **`DATA_MODEL.md` §8**；Q-27 ~ Q-36 实际记录在 **`INTERFACES.md` §15**
> 与 `ERROR_HANDLING.md` §12。此处如实更正，避免后续按不存在的章节去查。

| 编号 | 问题 | 现状 |
| --- | --- | --- |
| ~~Q-15~~ | 不同改编是否默认分离 | ✅ **已决定 → D-075（是）** |
| ~~Q-22~~ | 档案是否可跨版本共用 | ✅ **已决定 → D-076（默认否）** |
| ~~Q-27~~ | 是否引入 ORM / Pydantic | ✅ **已决定 → D-073（否）** |
| ~~Q-30~~ | 中文关键词检索方案 | ✅ **已决定 → D-074（FTS5 + trigram，旧版退化倒排表）** |
| Q-16 ~ Q-21 / Q-23 ~ Q-26 | 数据模型细节（非官方解读、时间线、冲突表现等） | 待决定，见 `DATA_MODEL.md` §8 |
| Q-28 / Q-29 / Q-31 ~ Q-36 | 接口与错误处理细节 | 待决定，见 `INTERFACES.md` §15 / `ERROR_HANDLING.md` §12 |

---

## 4. 决定变更流程

```
1. 提出变更动议
     └─ 说明：改哪一条、为什么改、影响哪些模块/文档

2. 评估影响
     └─ 检查是否与其他 D-0xx 冲突
     └─ 检查是否影响 AC-xx 验收标准
     └─ 检查是否需要同步修改其他文档

3. 人工确认
     └─ 未确认前，原决定继续有效

4. 更新文档
     └─ 修改本文件对应条目（保留变更历史记录）
     └─ 同步修改受影响的文档（ARCHITECTURE / DATA_MODEL / PROJECT_SPEC / ROADMAP）

5. 记录
     └─ 在本文件 §5「变更历史」中追加一条
```

**禁止**：

- 在实现代码时默默偏离 §2 的决定
- 把某个 Q-0xx 的猜测答案当作既定事实写进代码
- 直接覆盖本文件内容而不留变更记录

---

## 5. 变更历史

| 日期 | 版本 | 变更内容 | 确认人 |
| --- | --- | --- | --- |
| 2026-10-13 | v0.1 | 初稿：建立 D-001 ~ D-039 共 39 条已确认决定，Q-01 ~ Q-14 共 14 个待定问题 | 待确认 |
| 2026-10-13 | v0.2 | 新增 §2.8（D-040 ~ D-061，共 22 条）：写入已批准的技术选型（Python 3.12+ / SQLite / 关键词检索 / 超预算暂停）与接口契约决定；精确化 D-009、D-013、D-021、D-023、D-024；标记 Q-04 / Q-06 / Q-07 / Q-13 已解决；新增 §3.2（Q-15~Q-26）与 §3.3（Q-27~Q-36），均标为**待决定** | 待验收 |
| 2026-10-13 | v0.3 | 新增 §2.9（D-062 ~ D-067）：用户对 P1.2-B 汇报 R-1 ~ R-6 的拍板结论（通用表临时方案与 P2 前迁移、回填两个错误码、命名空间双层闸门、保留包名 `persona`、P2 前补 fixtures 与静态检查、任务引擎未实现属符合指令）；新增 §2.10（D-068 ~ D-072）：零第三方依赖 / 统一测试命令 / 临时库 / 不读密钥 / pyproject 仅作元信息。**Q-27（是否引入 Pydantic/ORM）仍未拍板** | 用户已拍板（R-1~R-6） |
| 2026-10-13 | v0.4 | 新增 §2.11（D-073 ~ D-076）：用户拍板 **Q-27=否**（不引入 ORM/Pydantic，继续标准库 dataclasses + sqlite3 + 薄仓储层）、**Q-30**（FTS5 + trigram，旧版退化为自建字串倒排表）、**Q-15=是**（改编默认分离）、**Q-22=否**（档案默认不跨版本共用，确需共用须显式声明并逐条标注）；新增 §2.12（D-077 ~ D-082）：P2.2 配置管理约定（统一入口 / 敏感配置只走环境变量 / 不引入 dotenv / 四级优先级 / 错误信息不含密钥值 / 未知项只告警）；新增 §3.2 更正 v0.2 变更历史中关于 §3.2、§3.3 的不实描述，并列出 Q-15/Q-22/Q-27/Q-30 已解决 | 用户已拍板（U-2、U-3） |
| 2026-10-13 | v0.5 | P2.2 落地：新增 `persona/config/`（spec + loader）与 `tests/test_config.py`（46 项）、`.env.example`、`docs/CONFIG.md`；CLI 新增 `config show` / `config check`；新增错误码 **`VALIDATION.config_invalid`**（待人工确认，见 `docs/CONFIG.md` §10 C-1）；同步 `DATA_MODEL.md` §8、`INTERFACES.md` §15、`ROADMAP.md` §3A、`RUNNING.md`、`README.md` | 已实现，待验收 |
| 2026-10-13 | v0.6 | 新增 §2.13（D-083 ~ D-086）：用户对 C-1 ~ C-4 的拍板（确认 `VALIDATION.config_invalid` 并永久保留在 `ERROR_HANDLING.md` §2.2；`PERSONA_SEARCH_API_KEY` 保持可选；不支持 `persona.toml`；`log_level` 只存不用、禁止写空壳）；新增 §2.14（D-087 ~ D-094）：P2.3 任务执行与状态管理基础闭环（六态与非法转换、暂停≠失败≠取消、预算四段式闸门、检查点权威存储、两道幂等保险、`idempotency_key` 去重、Mock 执行器、崩溃回退为 PAUSED） | 用户已拍板（C-1~C-4）；§2.14 待验收 |
| 2026-10-13 | v0.7 | §2.14 八条决定（D-087 ~ D-094）状态更新为**已验收**（P2.3 通过）；新增 §2.15（D-095 ~ D-101）：用户对 T-1 ~ T-7 的拍板（保留 4 个新错误码、接受更细恢复粒度、Mock 产出限制推迟到 P2、墙钟预算暂缓、接受任务级 attempt_count、暂不需要 create_project、接受无并发保护并记录最小修复路径） | 用户已拍板（T-1~T-7） |
| 2026-10-13 | v0.8 | 新增 §2.16（D-102 ~ D-110）：P2 证据管线第一步的实现口径（服务只依赖 Storage 且无研究方法、命名空间入口规则、去重五元组、规范成员指针、确定性结构冲突检测、6 个错误码待确认同 T-1 口径、extract_events 降级、judge_stability 规则、Protocol 对齐文档）；新增 §3 Q-37（P3.1 口头编号与 ROADMAP P2 的差异，按仓库编号实施未改路线） | 已实现，**待人工验收** |
| 2026-10-14 | v0.9 | 新增 §2.17（D-111 ~ D-120）：P3 第一步人格档案基础模块的实现口径（版本模型每版独立记录 + superseded_by 链、revise 强制 change_reason 且拒绝空转版本、复合类型补形且 coverage 服务端计算、item_id 调用方必填、版本边界规则含证据不串用、evidence_refs 只收 EvidenceItem、全空档案必须写 open_questions、CF-V5 双向回写、distill 未实现且 Protocol 对齐已实现面、真人禁 canon_boundary）；Q-37 扩展为覆盖"P3.1/P3.2 → 仓库 P2/P3"两轮编号差异 | 已实现，**待人工验收** |
| 2026-10-14 | v0.10 | 新增 §2.18（D-121 ~ D-128）：P3 第二步关系档案基础模块的实现口径（GroupDynamics/SharedExperienceItem 复合类型补形、群体必填形状与新错误码待确认、双向视角不合并、RL-V2 双重扫描、模式闸门、RL-V6 证据直达与版本交集规则、同角色双版本反合并、distill 未实现）；Q-37 扩展为覆盖第三轮"P3.3 → 仓库 P3 第二步" | 已实现，**待人工验收** |
| 2026-10-14 | v0.11 | 新增 §2.20（D-135 ~ D-145）：P4 第一步对话生成基础模块（用户称 P3.4）的实现口径（编号映射 Q-37 第四轮、INV-1 只读边界落地、least_recently_spoken 动态发言顺序含可解释理由、SS-V3 档案归属校验最小解释、只读上下文不伪造、模拟隔离三重保证、失败不伪造成功、namespace 恒 SIMULATED 无需迁移、本阶段不写长期记忆、用户消息落盘、3 个新错误码待确认 H-1）；Q-37 扩展覆盖第四轮"P3.4 → 仓库 P4 Dialogue" | 已实现，**待人工验收** |
| 2026-10-14 | v0.12 | 新增 §2.21（D-146 ~ D-159）：P4 第二步长期记忆基础模块（用户称 P3.5）的实现口径（编号映射 Q-37 第五轮、复用存储零迁移、MemoryPort 对齐 §9.3、write_simulated 签名对齐对话契约、首版关键词检索 D-074 退化方案、content 不可变走 supersede、去重只标记不删、冲突保留不裁决、删除同事务失效派生、软删宽限期与清理边界、模拟隔离 INV-3、对话衔接只提供接口不自动写入、4 个新错误码待确认 H-2、补 DerivedKind/DerivedStatus/PurgeState 枚举）；Q-37 扩展覆盖第五轮"P3.5 → 仓库 P4 Memory" | 已实现，**待人工验收** |
| 2026-10-14 | v0.13 | 新增 §2.21.1（D-171 ~ D-173）：P3.5 遗留问题验收结论（H-3 同意保留 4 个新错误码 → 已批准；H-4 接受内存子串匹配、FTS5 留后续；H-5/H-6 接受并记录）；新增 §2.22（D-174 ~ D-184）：P4 第三步对话↔长期记忆集成（用户称 P3.6）的实现口径（编号映射 Q-37 第六轮、确认集成前两条链路互相独立、**契约缺口最小补齐**＝新增只读 `MemoryReaderPort` 不改 `MemoryWriterPort`、检索规则 R1 本会话 + R2 跨会话含 topic 关键词门槛且硬限 SIMULATED/ACTIVE、检索失败不吞错、**默认不写长期记忆**的写入触发规则、候选 scope 必须 SESSION 不得升级为稳定人格特征、Turn→Memory→Audit 同事务失败整体回滚、`generated_memory_ids` 同事务回填、按 `(session_id,index)` 幂等、零新增错误码、`end_session` 刻意不触发 consolidate）；Q-37 扩展覆盖第六轮"P3.6 → 仓库 P4 集成" | 已实现，**待人工验收** |
| 2026-10-14 | v0.14 | 新增 §2.22.1（D-185 ~ D-190）：P3.6 遗留问题验收结论（H-1 同意保留 3 个对话错误码 → 已批准；`MemoryReaderPort` 同意回填 `INTERFACES.md` §8.1 并同步 v0.1.3；H-7 冲突记忆默认不进上下文接受现状；H-8 `end_session` 不触发 consolidate 接受；H-9 `write_simulated` 不走去重接受）；确认四项要求（跨会话闭环、写入可控、重试安全、数据边界）验证吻合；**D-190 确认下一阶段 = ROADMAP P5（评测与加固）**，本次以端到端集成验收形式落 `Evaluation` 核心（`check_invariants` / `check_dependencies` / `run_static_checks` / `measure_quality`）与集成测试 | 已实现，**待人工验收** |
| 2026-10-14 | v0.17 | 新增 §2.25（D-221 ~ D-235）：G-2 来源材料 → 候选 claim 自动抽取的实现口径——编号冲突（与 P3.3 的同名 G-2 无关，Q-37 第十轮）、新增 `modules/extraction.py` 不重写已验收模块、核心数据缺口是 Research 此前**丢掉了 `snippet`**（现返回 `materials`）、`SourceMaterial` 下沉 core 以避开 L4↔L4 同层调用、抽取器拿不到 Evidence（AST 断言）、三种 claim 的机械区分与推断无根即丢弃（EV-V3）、等级不等于真实性、8 种拒收原因不静默丢弃、冲突只记录不裁决且不做模糊合并、推断也要幂等、材料无法落检查点（新缺口 G-4，改进程内缓存 + 幂等重跑回补）、Mock 打 `mock-fixture` 标签且不代表真实能力、模型抽取只定义契约不接真实模型、业务入口不再要求 claim、两个既有测试改口径而非删除（D-234）；H-17（正文抓取）仍遗留 |
| 2026-10-14 | v0.16 | 新增 §2.24（D-206 ~ D-220）：H-10A 业务编排型 Orchestrator 的实现口径——编号映射 Q-37 第九轮、保留既有任务 Orchestrator 并新增独立 `BusinessOrchestrator`（实现 `UnitHandler` 被既有 `TaskRunner` 驱动）、不复制第二套六态机/预算/检查点（源码级静态断言）、五阶段复用 `TaskType` 同名 stage（`TaskType` 无全流程成员 → 缺口 G-1）、阶段间数据只经检查点 `artifact_ids`、证据 claim 不由编排器生成（缺口 G-2，用 `EvidenceSeed` 绑定来源）、证据与档案写入的幂等守卫（`item_id` 由证据 ID 推导）、Persona 按维度机械搬运且 `SINGLE` 不进 traits（AC-7）、Relationship 只用 RELATION 维度证据且不为群体关系臆造语义（RL-V3/RL-V6）、`entity_type` 只读判定（PF-V5/D-115）、结果三态自述且部分成功不算全流程成功、失败即上报且后续步骤 `NOT_REACHED`、`resume` 需重传请求（缺口 G-3）；Q-37 扩展覆盖第九轮"H-10A → 仓库 H-10 业务编排器" |
| 2026-10-14 | v0.15 | 新增 §2.23（D-191 ~ D-205）：P2 的 `Research` 部分最小可运行实现（用户称 H-11A）的实现口径——编号映射 Q-37 第八轮、保留 `ResearchPort` 并按 INTERFACES §5.2 更正签名（D-110 同口径）、只注入 `SourceRegistrarPort`（仅 `register_source`，结构性堵死 F-10）、**不注入 Storage 不落自己的表**、确定性模板检索计划（Q-29）、0 结果为业务结果而非错误、过滤排序确定性且被拒项必带原因、登记只经 Evidence 且失败整体传播、run 的失败纪律（单查询失败继续 / 全失败上报 / `BUDGET_EXCEEDED` 立即传播 / `budget` 参数不记账）、隔离（只产 `Source` 恒 `PUBLIC_SOURCE`、不产 `EvidenceItem`）、`NO_RESULT` 用模块常量不改已审 `FetchStatus`、`VALIDATION.empty_targets` 未登记故复用 `required_field`（零新增错误码）、`RankedCandidate.score` → `rank_weight`（配合 INV-4 而不放宽不变式）、Mock Research Provider 放测试侧；Q-37 扩展覆盖第八轮"H-11A → 仓库 P2 Research" | 已实现，**待人工验收** |
