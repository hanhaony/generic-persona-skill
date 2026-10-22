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

### 2.27 P4.7：补记产品化 / CLI 离线材料 / P1a S1-S2 / 默认禁止真实外呼（2026-10-17，用户拍板）

> 触发事件：P4.7 自检时发生 **1 次未授权真实调用**（deepseek / CHAT，0.0019161 USD，
> 已落库）。用户验收结论：**接受记录、不追责，累计 0.0097980 USD 计入人工账本、不重置**，
> 但要求把"默认禁止真实外呼"变成产品规则（D-245）。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-245 | **默认禁止真实外呼（装配层闸门）**：`build_business_stack(..., allow_real=False)` 是唯一默认值。即使配置为 `PERSONA_OFFLINE_ONLY=false` + `PERSONA_MODEL_PROVIDER=deepseek`，未显式授权时装配层**不构造**真实 Provider，只记 `REAL_PROVIDER_BLOCKED`；CLI 对应 `persona business run --allow-real`，不给则退出码 2 并打印规则与开关。**配置层（能否联网）与装配层（本次是否花钱）必须是两个开关**——2026-10-17 的未授权调用正是把"配置允许"当成"已授权"导致的。显式注入的 `chat_provider` / `claim_extractor` 不受闸门约束（注入本身即显式动作），`business plan` 不构造 Provider 故天然不受影响 | 用户 P4.7 验收整改要求；实测事故 0.0019161 USD | 已实现（`tests/test_p4_7_real_call_gate.py` 9 项） |
| D-246 | **`--material` 不替代模型**：本地材料只解决"材料从哪来"，研究阶段如实报告"未做检索"（`planned/executed` 恒 0，不伪造成 1）；材料仍须通过同一个 `assess_content_quality`，无抽取器时同样拒绝运行（本地材料不能凭空变成 claim）。**不在产品代码内置任何演示材料**（G4 教训：假数据会让"看起来能跑"掩盖缺陷） | 用户 P4.7 拍板第 4 项 | 已实现（`tests/test_p4_7_local_materials.py` 12 项） |
| D-247 | **G12 登记：跨源同句 ⇒ `traits` 两条同文本条目**：`_build_persona_draft` 按**证据**生成条目（`item_id = pi_<evidence_id>`），同一说法跨源时产生两条文本相同、各引一条证据的条目。**不是 AC-7 违规、不是判定链缺陷**，是档案可读性缺陷；修复要改档案条目身份语义（影响版本 diff 与既有断言），用户拍板**单独立轮**，不混进 P1a。详见 `RUNNING.md` §5.16 | 用户 P4.7 拍板第 1 项 | **已登记，未修** |
| D-248 | **P1a S3 并入阶段三**：S1（离线桩）+ S2（e2e 夹具）已证明规则层与编排层"跨源同句 ⇒ REPEATED ⇒ traits"成立；S3 剩下的未知是"真实模型会不会逐字复用同一句"，属模型行为而非代码，一次 POST 只能观察不能保证。故**本轮止步 S2**，S3 与真实检索接线一起做 | 用户 P4.7 拍板第 2 项 | 已决定 |
| D-249 | **`usage list` 暂缓**：当前 `usage backfill` 只打印账本合计，没有逐笔列表；要做需先定筛选口径（按项目 / 按任务 / 按时间窗 / 按 `cost_status`），否则会先做出一个"看起来能对账、实际口径不明"的命令 | 用户 P4.7 拍板第 3 项 | **暂缓，待定口径** |

### 2.28 P4.8：G12 修复 + 检索接线 + 调用清单（2026-10-17，用户拍板）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-250 | **G12 修复采用方案 A**：档案条目身份从"证据"改为"**维度 + 归一化说法 + 主体集合**"的摘要（`pi_<sha256(...)[:16]>`），同一说法的多条证据合并成**一个**条目、`evidence_refs` 收全部；background / expression 的重复文本只留一次但证据引用一条不丢；**旧档案不做 id 迁移**（档案版本化，新口径只影响新版本）。诚实边界：只合并**归一化（折空白）完全相同**的说法，措辞不同的同义句仍各自成条（那是 P1b 语义聚类）。用 `sha256` 而非内置 `hash()`：后者随 `PYTHONHASHSEED` 变化，会让每次重跑都变成"删一条 + 增一条" | 用户拍板 R1 = 方案 A；`RUNNING.md` §5.16 | 已实现（P1a 8 项、e2e 85 项） |
| D-251 | **检索出口的装配点补齐，且与模型共用同一把闸门**：新增 `search_provider_from_settings()`（`max_retries` **强制 0**——按次计费，重试等于再买一次）；装配层新增 `StackOptions.build_search_provider`，同样只有 `allow_real=True` 才构造，否则记 `REAL_PROVIDER_BLOCKED`。**不给 `--material` 才建检索出口**（材料通道不检索） | 用户拍板"allow_real 兼管检索"；Q-02 已批准 | 已实现（`test_p4_8_search_wiring.py` 20 项），**未跑真实检索** |
| D-252 | **执行前必须打印调用清单**：`plan_calls()` 给出检索次数（来自 `plan_queries`，不是猜）、抓取上限（`max_extracts`）、模型调用（1 次批量抽取 + 多目标时才有的对话）、金额估算与**计量单位**。检索按 credit、模型按 USD，**不可直接相加**；"清单之外不会有任何调用，出现即缺陷"写成输出的一部分 | 用户拍板第 3 项 | 已实现 |
| D-253 | **顺手修的真缺口（行为变更，需登记）**：装配层此前传 `research_limits=None`，研究阶段回落到模块默认 5/5/3，导致 `PERSONA_RESEARCH_MAX_QUERIES` / `PERSONA_RESEARCH_MAX_EXTRACTS` 在**产品路径上被静默忽略**（e2e 脚本显式传 limits，所以一直没暴露）。现在默认取 `settings.research`，显式传参仍优先；默认值与旧行为一致（5/5/3），因此不影响既有测试 | 实现 R3 时发现 | 已实现 |

---

### 2.29 P4.8 R4：首次真实检索运行的结论与整改项（2026-10-17）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-254 | **检索词必须带上作品名**：`register_targets` 要把 `work_titles` / `adaptation_version` / `entity_type` 一起传给 `PersonQueryTarget`；`_query_target` 不得清空已有的 `work_titles`。此前 CLI 强制要求 `--work-title`，装进实体后却被丢掉，真实检索退化成查两字人名（实测查「阿灯」）。**"声明了就必须用"**，否则等于让用户白填 | R4 dry-run 实测；`plan_queries` 本就支持"名字 + 作品名" | 已修（+4 项测试） |
| D-255 | **`filter_and_rank` 不做相关性判定是有意还是遗忘，必须明确**：现状只去重/查完整性，5 条"防雾灯"页面全部登记成来源。**登记 ≠ 可用**。下一轮要加"目标名/作品名整体命中标题或摘要"的硬判定，无关项进 `rejected` 并带原因；在此之前，任何报表**不得**把来源数当收获数 | R4 实测：5 条来源、0 条可用 | 已修（见 §2.30 / D-260） |
| D-256 | **对话的模型调用必须走逐笔账本**：`DialogueDeps` 加 `ledger` + `usage_sink`（或在编排层结算），使每一次 `chat` 都产生一条 `ExternalUsageRecord`。现状 token 只落在 `ConversationSession.budget_used`，`external_usage_records` 里**没有这笔**——累计闸门与逐笔对账双重漏账，违反"所有真实调用必须落库"的既有拍板 | R4 实测：3 条 tavily、0 条 deepseek | 已修（见 §2.30 / D-259） |
| D-257 | **调用清单的对话次数改为按"每轮 1 turn"标定**（或明确标注为上界）。现状 `目标数 × 轮数` 报 2 次，实际 1 次——上界没错，但会让人按 2 次的钱做预算判断。**清单的意义是"能对上账"，不是"给个安全的数字"** | R4 实测：清单 3 次模型、实际 1 次 | 已修（见 §2.30 / D-261） |
| D-258 | **真实检索 e2e 的复现纪律**：`--dry` 必须**物理断网**（把 `_urlopen` 换成必然失败的替身，而不是"相信没人调用"）；原始响应要**增量落盘**且**读 body 失败也要记**；真实运行只发生一次，报告写错就修报告器 + `--report-only` 重放产物，**绝不为打全数字再花一次钱** | R4 工具本身出过两个错（漏记超时、档案字段形状假设错） | 已实现 |

---

### 2.30 P4.8 R4 整改（D-3 / D-2 / D-4）与 R4.1 换角复跑（2026-10-17）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-259 | **对话与抽取共用同一套预算闸门与结算**：把抽取器里已验收的闸门逻辑提到 `persona/modules/model_budget.py`，`dialogue.draft_turn` 与 `extraction` 调**同一个** `check_model_budget()` / `settle_model_call()`。**不允许两条通道两套账**——账本最不能容忍的就是两套口径。`DialogueDeps` 增补 `ledger` / `usage_sink` / `limits` / `task_id`，装配层注入 | D-256；R4 实测账单有扣费、库里无记录 | 已修（`model_budget.py` 新建；+专项测试） |
| D-260 | **离线替身明确声明 `offline = True` 且不记账**：请求没离开进程 ⇒ 不产生外部用量 ⇒ 既不预留也不落账。若不声明就按真实出口记账。**宁可多记一笔本地调用（几厘），也不要漏记一笔真实扣费**；反之把桩记成 UNKNOWN 会凭空产生 1 credit 未知占位，下一笔真实调用会被闸门误判停手 | D-259 落地时 e2e 桩被当在线，撞 `max_calls=1` | 已修（`FakeProvider.offline`、`e2e_p4_5a` 两个桩同步声明） |
| D-261 | **`filter_and_rank` 加硬相关性判定**：用 `display_name` / `work_titles` / `adaptation_version` 构词表（最小长度 2），候选的标题 + 摘要 + 链接路径均未命中即进 `rejected`（原因 `IRRELEVANT_TO_TARGET: …`），**不再登记成来源**。无词表可判时**不做过滤而显式告警 `RELEVANCE_NOT_CHECKED`**（不能假装查过） | D-255；"登记 ≠ 可用" | 已修（+专项测试） |
| D-262 | **`plan_calls` 的对话次数按实际轮次计**：`dialogues = max_rounds`（每轮 1 个 turn），不再是 `目标数 × 轮数`。e2e 脚本的硬断言同步改为 `1 抽取 + max_rounds` | D-257 | 已修 |
| D-263 | **R4.1 换角复跑须用"真实存在"的目标**：R4 用虚构的《雾港灯会》导致 0 条可用来源，无法验证"有来源 → 有证据 → 有 traits"主路径。改用公有领域角色**福尔摩斯 / 华生**（《福尔摩斯探案集》）。**先给清单、零成本 dry-run 通过后，等用户书面授权才发真实请求** | R4 §7"没证明"第 1 条 | 已定；授权待批 |

---

### 2.31 P4.8 R4.1 换角复跑（福尔摩斯 / 华生）的实测结论（2026-10-17）

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-264 | **D-6 是主路径的唯一卡点，必须先修它**：材料门禁要求"每份材料都亮门禁结论"，但**检索摘要天生没有 `quality_status`**（只在正文抓取路径产生）。于是 `max_extracts=1` 的单条抓取一旦失败 ⇒ 没有任何材料能进抽取器 ⇒ 模型一次不调 ⇒ **0 证据是 100% 必然**，与目标是否真实存在无关。**这不是门禁有效，是门禁把"我们没抓到正文"当成了"内容不可用"**——误杀。R4 之所以没看出来，是因为目标不存在、来源全是噪声，"0 证据"看起来像正确结果，缺陷被掩盖 | R4.1 实测：7 条来源**全部真实相关**，仍 0 证据；百度百科 `Failed to fetch url` | **已修（D-268 方案 ①）；R4.2 实测验证通过**：抓取成功（百度百科 2255 字）、正文 VALID 1 + `SUMMARY_ONLY` 8 全部进抽取器、模型**真的被调用** ⇒ 0 证据与门禁无关，卡点前移至 D-272 |
| D-265 | **相关性判定必须做繁简归一**：词表用简体「福尔摩斯/华生」，页面写繁体「福爾摩斯/華生」⇒ 字面不匹配 ⇒ **本轮最相关的一条（801 字、明确讲福尔摩斯与华生）被误杀**。D-2 的过滤器生效了，但"生效"不等于"准" | R4.1 实测：`CANDIDATES_IRRELEVANT` 拒了 1 条，正是繁体那一条 | **已修（D-270 字形归一）；R4.2 实测验证通过**：繁体「福爾摩斯的懸疑世界」正常登记为来源，`CANDIDATES_IRRELEVANT` 拒收 0 条（归一没有放进噪声） |
| D-266 | **报告器写错就修报告器，绝不重跑花钱**（承 D-258）：R4.1 实测出 3 处报告器缺陷（对话 `speaker`/`content` 字段读错恒为 None、`--report-only` 里 D-3 结论写死成 R4 时期文案、来源计数用候选登记数 9 而唯一来源是 7），全部用 `--report-only` 只读重放验证，**没有为打全数字再跑一次真实检索** | R4.1 实测 | 已修（前两处）；计数口径差异与"PARTIAL 被自检判 FAIL"先记录 |
| D-267 | **换角复跑有效**：R4 用不存在的《雾港灯会》导致来源全是"防雾灯"噪声；R4.1 换公有领域的福尔摩斯/华生后，7 条来源全部真实相关。**验证"检索出口在真实网络上可用"这一层，换角是必要的**；但它只解决了"有没有来源"，没解决"来源能不能变成证据"（那是 D-6） | R4 vs R4.1 对比 | 已定 |
| D-268 | **D-6 采用方案 ①：给摘要通道新增 `SUMMARY_ONLY` 一等状态**。②（抓取失败换下一个候选重试）超清单、费用不可控；③（对摘要放松门禁）有风险；① 最诚实——**摘要就是摘要，明确标注，让下游决定怎么用，门禁语义不变**。推论：**门禁只负责"亮结论"，不负责"判死刑"；证据层的 `tier` 决定最终可用性** | 用户拍板（2026-10-17） | 已修 |
| D-269 | **`""`（未声明）与 `SUMMARY_ONLY`（声明自己是摘要）必须区别对待**：前者是**信息缺失**，严格模式下仍可拒收；后者是**信息完整**，不得因"未声明"被拒。混为一谈正是 D-6 的成因 | D-268 | 已修 |
| D-270 | **字形归一自建字表，不引第三方库**：`persona/modules/text_variants.py` 提供 `fold_variants()`（繁→简单向），判定前**词表与待判文本两侧都折**，实现双向可对上。理由：`pyproject.toml` 写着运行时零第三方依赖（D-004/D-068），为"词表匹配"引入 opencc/zhconv 的代价远大于收益。字表是**常用字集不是全集**，未收录字原样参与匹配（不会更差），漏字补表必须同时补测试 | D-7 + D-004 | 已修（404 字） |
| D-272 | **主路径卡点已从 D-6 前移到"模型输出上限"**：`persona/modules/extraction.py:856` 硬编码 `max_output_tokens=4096`。材料越多、claims 越多越必然撞满 → 截断 → `INVALID_JSON` → **整批作废**。R4.2 实测：9 份材料 / 7758 输入 token，输出正好卡死在 4096（`finish_reason="length"`），**21 条完整 claims 全部丢弃**。**这条比 D-6 更致命**：D-6 是门禁挡住 → 不调模型 → 不花钱；D-272 是**花了钱（0.0072426 USD）买回 0 条证据**。修法四选一：① 分批（调用次数翻倍 → 超清单，须重新授权）② 提高/按材料数动态化 `max_output_tokens`（零新增调用，仍在清单内，但须同步修 D-274 的估算）③ 限制单批材料数（主动降质）④ 截断 salvage（引入"半截证据"，风险最高）。**倾向 ②** | R4.2 实测 | **已修（用户拍板方案 ②）**：`estimate_output_tokens(材料数)` = `256 + 材料数×4×200`，夹在 `[4096, 8192]`；`build_request` 元数据带 `material_count` / `max_output_tokens`（**估算与真发必须是同一个数**）。**仍未闭合的部分见 D-276** |
| D-273 | **阶段级重试与模型幂等键冲突，失败原因被吞**：`retry_policy.max_attempts=3`，第一次抽取已花钱并失败（Err），重试时幂等键相同 → `MODEL_CALL_DUPLICATE_SKIPPED` → 返回 `Ok`（0 候选）→ **阶段状态被覆盖成 `SKIPPED`，真正的 `EXTERNAL_UNAVAILABLE` 只留在 `TaskRecord.last_error`**。去重本身是对的（没再花一次钱），但**阶段视图看不到失败原因**，极易被误读成"没东西可抽" | R4.2 实测 | **未修** |
| D-274 | **事前费用估算系统性偏低**：抽取这一次预估 0.0033 → 实际 **0.0072426**（**2.2 倍**）。`est_tokens_out` 默认值远小于真实输出，输出撞满上限时偏差最大。费用上限 0.25 未被击穿所以没造成后果，但**估算不能当预算用的数字看** | R4.2 实测 | **未修** |
| D-279 | **报告器自检用字面量而非常量，会把成功的运行报错成 FAIL**：`e2e` 脚本 `[11] 自检` 写 `run.status in ("RUN_SUCCEEDED", "RUN_PARTIAL")`，而常量 `RUN_SUCCEEDED` 的实际值是 `"SUCCEEDED"` ⇒ 断言永不匹配。R4.3 五阶段全 EXECUTED、任务 SUCCEEDED，自检却打出 `[FAIL] 任务未失败（状态 SUCCEEDED）`。**报告器的错误会让人误判运行本身有缺陷**（与 D-266 同类）。已改为引用常量 | R4.3 实测 | 已修 |
| D-277 | **"每份材料最多 N 条"只在材料数 ≤ 9 时保证不截断**：`N=4` 时 9 份 × 4 条 = 7456（正好等于算出来的上限），10 份就要 8256 > 厂商硬顶 8192。**需求有上界了，材料数本身还是变量**。处置：撞到边界时用 `OUTPUT_CEILING_TIGHT` 告警**在发请求之前**喊出来（写明需要多少 token / 硬顶多少 / "截断＝整批作废"），**不静默调小 N**（那是隐藏行为），也不假装没事（那会白花一次钱）。R4.2 恰好是 9 份，正好卡在保证的边界上 | D-276 的推论 | 已修（告警） |
| D-280 | **D-278 的修法定为"受控特征标签 `trait_tag`"——把归一化前移到抽取时**：模型输出时除 `claim` 外还要给一个**只能从受控词表选**的 `trait_tag`（禁止自造）；归并按 `dimension + subject + trait_tag` 三元组**确定性**归并，纯离线。**与方案 A 的本质区别**：A 让模型判断"两条说法是否等价"（= **判定**，模型给自己的产出背书 ⇒ 自证，违反 AC-7）；本方案只让模型做**归纳/分类**（贴标签），判定由确定性规则完成（三元组相等即同组）。贴错标签的后果是**归并到错误格子**（精确率问题），不会凭空制造"重复出现"这个**事实**（可靠性问题）——AC-7 约束的是后者。**反证**：若本方案也违反 AC-7，则现有 `dimension` 字段（同样是模型把自然语言归入 7 个预定义槽位）早就违反了。配套：① 词表三层（L1 通用 ~32 个 / L2 领域**可插拔、默认空**保住可迁移性 / L3 自定义），层间覆盖优先；② 只对 `TRAIT`/`HABIT`/`SPEECH_STYLE` 启用，其余维度**必须留空**（双向硬校验）；③ `trait_tag=null` 是合法出口（回退逐字归并）——**没有这个出口模型会被迫乱贴，误并率失控**；④ 词表外值 ⇒ 拒收 + `TRAIT_TAG_NOT_IN_VOCABULARY`（与 `QUOTE_NOT_IN_MATERIAL` 同纪律）；⑤ 归并键**必须唯一实现**（当前 `_stability_groups` 与 `_build_persona_draft` 各写一遍，须收敛——D-3 的教训就是"两条通道两套账"）；⑥ `PROMPT_VERSION` +1；⑦ 误并告警 `TRAIT_TAG_DIVERSITY` 用**字符重合度**（不用模型）。**已否决**：A（违反 AC-7）／B（违反 D-012/PF-V2）／C 实体锚点（脆弱、跨人物不可迁移、误并风险高）。方案见 `docs/P4_8_D280_TRAIT_TAG_PLAN.md` | D-278 + 用户拍板 | **方案已出，实现待授权** |
| D-281 | **`evidence_anchors`：只读证据锚点（D-278 的保守版）**：R4.3 后档案读起来"traits 0"一片空，短期先让证据可见。**放在 `_build_persona_draft` 由服务计算**（与 `coverage` 同级），每项含 `feature`/`dimension`/`subjects`/`evidence_ids`/`source_ids`/两个计数/`in_traits`，按来源数降序。**三条硬边界**：① **不进 `traits`**（`in_traits` 恒 False，不绕过 AC-7）；② **不参与 `coverage`**、不改变档案状态；③ 计数**落盘前重算**（`_anchor_view` 去重后按 ID 数计数 + 键白名单）⇒ 调用方**可以留空、不可以编造**（声称 5 源只列 2 个 ⇒ 记 2）。**不解决 D-278**：分组键仍是逐字口径，跨源同义仍各占一行——测试里专门写了反向断言钉死这点，防止将来误以为它解决了 | R4.3 后续 | 已实现（15 项测试） |
| D-278 | **证据层的"说法身份"用逐字归一化 ⇒ `traits` 结构性为空**：`evidence._normalize_claim()` 只折空白（注释明写"压缩语义属于语义判断，不做"），`business._stability_groups()` 的分组键 = `(逐字归一化的 claim 全文, 主体集合)` ⇒ 只要措辞不同就归并不了 ⇒ `occurrences` 恒为 1 ⇒ `stability` 恒 `SINGLE` ⇒ D-012/PF-V2 闸门（`traits` 只接受 `REPEATED`/`PATTERN`）把所有 TRAIT 证据挡下、落进 `single_events`。**R4.3 实测：16 条证据 stability 全 SINGLE、traits 桶 0 条，而材料里至少 5 组跨源同义（小提琴 3 条/2 源、可卡因 2/2、烟斗 3/**3 源（PATTERN 级）**、贝克街 2/2、阿富汗军医 2/2）——若可归并，5 组全部够进 traits。**所以归因既不是 N=4 太严（输出只用了 51%），也不是材料没有跨源同义（有，且充分），而是归并粒度。这是已知设计限制，但构成结构性必然：只要 claim 由模型抽取，措辞必然每次不同 ⇒ `traits` 永远为空，与材料多少、检索次数无关**。三个方向：A 引入语义级聚类（需模型 ⇒ 让模型给自己作证，与 AC-7 冲突）／B 放宽闸门收 `SINGLE`（**直接违反 D-012/PF-V2**）／C 按"维度+主体+实体锚点"做确定性归并（纯离线、可测试，**倾向**；风险是误并，须在 `single_events` 保留原始说法以便追溯） | R4.3 实测 | **未修（待拍板，倾向 C）** |
| D-276 | **输出上限有顶，而需求没有上界——D-272 只解决了一半**：`OUTPUT_TOKEN_CEILING=8192` ≈ 39 条 claim，但 R4.2 实测**单份**抓取正文（百度百科 2255 字）就一口气产出 **20 条**（c1…c20），之后才轮到第 2 份材料 ⇒ 9 份材料的真实需求可能是 20 + 8×(3~10) = **44 ~ 100 条**。所以按现状跑 R4.3 **仍可能截断、仍可能 0 证据、仍可能再白花一次钱**，只是撞的墙从 4096 变成 7456/8192。闭合办法（**未做，待拍板**）：给提示词加"每份材料最多 N 条"硬约束（需 `PROMPT_VERSION +1`），例如 4 条/份 → 36 条 → 稳装在 7456 内。**理由：截断是 100% 全损，少拿几条只是部分损失** | R4.2 实测密度 + D-272 | **已修 + R4.3 实测验证通过**：`finish_reason` 从 `length` 变 `stop`，输出占用 3803/7456 = **51%**，17 条 claims 全部保留；2 份材料（百度百科正文、大英图书馆）**正好顶到 N=4**，证明提示词硬约束被遵守（是"写到 4 条停"而非"少写一点"） |
| D-275 | **登记来源数 ≠ 唯一来源数，口径不一致**：阶段消息报"登记 10 条来源"，实际唯一来源 8 条（2 条按 canonical key 去重）；R4.1 是 9 vs 7。**同一个问题两轮都在**。会让人按登记数判断收获，而登记 ≠ 可用 | R4.1 + R4.2 实测 | **未修** |
| D-282 | **只上 `trait_tag` 不会让 `traits` > 0 —— 真正的卡点是"一条 claim 装了 3~6 个事实"（复合说法）**：用 R4.3 的 16 条证据 + 草拟 L1 词表做**离线回放**（贴标由人完成，脚本 `%TEMP%\d280_vocab_replay.py`，**不进仓库**，零外部调用）。结果：6 条可贴标证据按 `(维度, 主体, tag)` 归并 ⇒ **6 组、0 组可归并**，与 R4.3 实测的 `traits=0` 完全一致。原因：#2 一条 claim 里装了 5 个习惯、#3 装了 6 个特征、#7 装了 4 个，而 `trait_tag` 只能填**一个** ⇒ 两个来源要归并必须**碰巧选到同一个标签**，概率仅 **~6%~8%**（#3 有 6 个候选、#13 有 3 个，交集只有 `analytical`）。**根因回溯到 D-276/方案 B**：`max_claims_per_material=4` 逼模型把多事实压缩进一条 claim —— **"防截断"的措施制造了复合说法，复合说法又让归并失效，两个改动互相打架**。反事实验证：把 25 条原子事实各自独立贴标后归并 ⇒ **3 组可归并**（`analytical` PATTERN 3 源 / `music` REPEATED 2 源 / `substance_use` PATTERN 3 源），预估 `traits` 0 → **1**、`habits` 1 条 1 证据 → **3 条有多源引用**。**结论：`trait_tag` 必须与"claim 原子化"（提示词加"一条 claim 只陈述一个事实"）一起做，否则 R4.4 会拿到与 R4.3 相同的结果**。代价：原子化后 claim 条数 17 → 约 30~40，输出 token 上升（R4.3 用了 3803/7456 = 51%，余量够但不再宽裕），实现时须同步调 `estimate_output_tokens` 并在 `--dry` 确认不撞 8192 硬顶 | D-280 离线回放 | **待拍板** |
| D-283 | **归并键不应含 `dimension`——改为 `(主体集合, tag)`，并让每个标签自带归属族**：`_stability_groups()`（`business.py:1223`）的键**刻意不含 dimension**，注释写明理由（"同一句话被抽成两个维度仍是同一个说法的同一次出现，按维度切开会人为压低 `occurrences`"）。而 D-280 §6 提议的键把 dimension 加了回去。R4.3 数据给了反证：**同一个事实在不同来源被抽成不同维度**——小提琴（baike 给 HABIT / bl、#3 给 TRAIT）、可卡因（baike HABIT / bl TRAIT）、烟斗（baike HABIT / qidian TRAIT / #8 CANON_FACT）。维度留在键里 ⇒ 这些永远合不到一起。**去掉后必须回答的新问题**：组里成员维度不一致时条目落进哪个板块 ⇒ 解法是**每个标签在词表里声明自己属于 TRAIT / HABIT / SPEECH_STYLE 哪一族，板块由标签族决定，不看模型给的 `dimension`**；两者冲突时**以标签族为准、原样保留 `dimension`、记告警 `TRAIT_TAG_DIMENSION_MISMATCH`**（不静默，也不让模型裁决）。**顺带暴露一处既存不一致**：`_build_persona_draft`（`business.py:1375`）的键**含** dimension 而 `_stability_groups` **不含**，两处各写一遍且口径不同 ⇒ 不收敛就会出现"稳定性判了 2 条、条目却生成 1 条"（D-3「两条通道两套账」同类） | D-280 离线回放 | **待拍板** |
| D-284 | **`TRAIT_TAG_DIVERSITY` 字符重合度告警**取消**——该指标无区分度**：回放对每个真归并组算组内两两字符 bigram Jaccard：`analytical` 组 **0.000~0.000**、`music` 组 0.000~0.750、`substance_use` 组 0.000~0.250；而**明显不该并**的对照对（吸烟斗 vs 注射可卡因、拉小提琴 vs 听音乐会、冷静 vs 勇敢、聪明如超人 vs 特立独行）**全部也是 0.000**。**两头的取值完全重叠 ⇒ 无论阈值取多少，要么把所有真归并报成可疑，要么把所有误并放行**。根因：**同义必然异形**——两条说法之所以需要归并恰恰因为措辞不同，用"字面像不像"检测"语义对不对"方向是反的。**改法**：不做告警，改为在 `uncertainty_note` 如实写"本条由 N 个来源的 M 条说法归并，措辞各异，原文见证据引用"，配合已有的 `evidence_refs` 保证可回溯。**理由：假告警比没告警更糟，它会训练人忽略告警**；误并的真正防线是词表设计的互斥性（设计期）+ `evidence_refs` 可回溯（运行期），不是事后算字符 | D-280 §11 待拍点 ② 的校准结果 | **待拍板（建议取消）** |
| D-285 | **草拟 L1 词表覆盖率：25 条原子事实中干净命中 64%、勉强 28%、无法标注 8%（可覆盖合计 92%）**；**17/31 个标签本轮未用上，但不据此删词表**（单样本：一个西方侦探角色；未用上的 `melancholy`/`impulsive`/`compassionate` 对林黛玉、贾宝玉这类角色很可能是主力——这正是 R4.4 换角要验证的）。**三个真实缺口**：善于刀剑（词表无"武艺/格斗"，勉强归 `exercise`）、特立独行（"不循常规" ≠ "对人冷淡"，勉强归 `aloof`）、外貌与"思维简单直接"（无标签 → `null`，**回退 null 是正确行为，外貌本就不该进 traits**）。另：方案写"约 32 个"，逐条点下来是 **31 个** | D-280 离线回放 | 已定 |
| D-286 | **L2 中文古典层（11 个标签，默认仍走 L1）**：R4.4 实测暴露两个缺口——① **`SPEECH_STYLE` 族在 L1 一个标签都没有**，而黛玉最核心的特征恰恰是"说话方式"（"言辞尖酸刻薄、喜欢怼人"）⇒ 模型只能硬塞 `TRAIT.witty`（定义是"幽默机智"）⇒ 与"幽默爱笑"那条**误并成同一个 traits 条目**；② 材料反复强调、L1 无格子的：敏感/多心、叛逆、痴情、病弱、俊美、崇古、平等待人。新增 `L2_ZH_CLASSICAL_TAG_SPEC`：`SPEECH_STYLE` 4 个（`caustic` 刻薄/毒舌、`reserved` 含蓄、`incisive` 犀利、`tactful` 委婉）+ `TRAIT` 7 个（`sensitive`/`devoted`/`rebellious`/`egalitarian`/`traditional`/`frail`/`attractive`），合计 **42**。**不塞进 L1**：L1 是"跨人物可迁移"的基线，硬编码中文古典语汇会让"换角还能不能覆盖"再也测不出来（D-280 §分层）；代价（候选格 31→42、误贴面变大）由"必须显式选层"承担（`vocabulary_by_name` / e2e `--vocabulary`）。**顺带修一个真 bug**：标签清单不在 `PROMPT_VERSION` 里，而幂等键只含 模型|材料|目标|提示词版本 ⇒ **L1 切 L2 会撞上同键被当重复跳过、复用 L1 产出**（"我想换层"的意图被静默吃掉，与 D-283 空词表被 `or` 换掉同型）；修法 `TraitVocabulary.fingerprint()` 进键，空串兼容对话等不涉及词表的调用 | R4.4 实测 + 用户拍板 A | 已实现（12 项测试 + 回放） |
| D-287 | **规则 9（原子化）与规则 10（每份材料最多 N 条）的冲突裁决**：R4.4 实测 25 条证据里 **19 条含 ≥3 个分句**，一条 claim 装 2~4 个事实（`林黛玉自幼体弱多病，但生性聪慧灵敏，曾师从贾雨村，精通诗词` 装 4 个，只贴得出一个标签）。**根因不是模型不听话**：事实多于 4 时它只能合并来保住条数——**防截断的措施又在制造复合说法**（D-282 已记过同一对矛盾，这次是它在真实数据上的兑现）。修法三条：① 提示词写出**优先级**——"事实多于 N 时第 9 条优先，宁可少写几条，也不要把几个事实合并成一条"，并禁止在事实后追加评价（"体现出…""折射出…""显出…"，R4.4 有 7 条、占掉 claim 一半字数）；② `TOKENS_PER_CLAIM` 200→**180**（实测 163/条），10 份材料从 8256 降到 **7456** ⇒ `OUTPUT_CEILING_TIGHT` 不再误报，12 份（8896）才告警；③ 运行期加 `CLAIM_NOT_ATOMIC` **批级告警**（只报数不拒收，阈值 ≥3 分句），把"原子化做到没有"变成每次运行自报的数字。`PROMPT_VERSION` → `g2-extract-2026-10-18b`。测试 9 项含 **19/25 夹具锁**（锁住 R4.4 的实测数字，换成别的样本就锁不住了） | R4.4 实测 + 用户拍板 B | 已实现（9 项测试） |
| D-288 | **抽取提示词只注入主体 ID、不注入"ID ↔ 显示名"映射 ⇒ 主体归属不可控（P0）**。R4.5 实测：26 条证据里 **25 条主体错绑**——15 条讲贾宝玉的全挂黛玉 ID、5 条讲林黛玉的全挂宝玉 ID，只有那条双主体的"宝玉与黛玉青梅竹马"是对的。**后果是两个 `traits` 条目互相张冠李戴**（黛玉档案里是"贾宝玉厌恶四书"、宝玉档案里是"林黛玉…悲剧形象"），而**条目数与组数完全对得上**（黛玉 1+1=2、宝玉 1+5=6）⇒ 只对数量验收会判通过，这是最危险的形态。**根因**：`extraction.py:1233-1236` 只写 `本次允许使用的主体 ID：[...]`，`targets` 里本有显示名却没写进提示词，模型面对裸 ID 只能按列表顺序猜。**R4.4 的 22/22 全对是无保证的正确**——当轮"材料目标 = 材料内容"，模型按顺序蒙对；R4.5 因黛玉侧 5 份检索结果里 4 份是《红楼梦》总页/回文（大面积讲宝玉），材料与目标一错位就全崩。修法：① 提示词注入 `林黛玉 = pe_…、贾宝玉 = pe_…` 映射（零成本，必做）；② 可选加 `SUBJECT_CONTENT_MISMATCH` 门禁兜底；③ 无人物 claim（网页/回目描述）不应硬挂人物 ID。**另记一条验收口径错误**：授权清单写"抓取 ≤1"，实际产品里初始抓取与补救是**独立额度**（`max_extracts` + `max_remediations`），本轮 1 初始 + 1 补救 = 2 次属设计内行为、未多花钱（第二次 0 credit），清单应写"≤1 初始 + ≤1 补救" | R4.5 实测 + 用户拍板 | **已修（12 项测试 + 离线回放）** |
| D-289 | **引文核验的"折空白"是假的 ⇒ 中文长材料被系统性误杀（P1，待修）**。R4.6 唯一一条拒收 `QUOTE_NOT_IN_MATERIAL` 复核后发现**引文就在材料里**：原文 PDF 硬换行留下"想生气 **空格** 就生气的"，引文连着写。根因 `claim_validation._norm_ws` 把连续空白折成**一个空格**（`" ".join(text.split())`），材料折后多一个空格、引文没有 ⇒ `find` 返回 -1 ⇒ 拒收；而告警文案写的是"允许折空白差异"，**"折"的本意（容忍换行）恰好被实现破坏**（换行变成了真空格）。**影响面**：用"空白可选"匹配（`\s*` 连接每字符）复核三轮全部拒收候选，**R4.4 2/2、R4.5 1/1、R4.6 1/1 全是误拒**（4/4），丢失比例 3.4%~7.4%，且丢的都是长材料（PDF/硬换行）的句子——本轮丢的那条紧邻"她不止一次奚落贾宝玉…出言讽刺过薛宝钗"，正是 `caustic` 语料。**修法（未实施）**：`_norm_index_map` 折空白时**丢弃**空白字符（不是压成一个空格），或加第三轮"两侧删空白后 find + 映射回原文"；红线不变——改写/删字/换序仍必须拒收（补反向测试）。**与 `traits`=0 无关**：放行后新增的 `impulsive` 仍是单源，仍落 `single_events`，两件事不要混着归因。**已修（用户拍板「先修 D-289」）**：`_norm_ws`/`_norm_index_map` 改为**丢弃空白**，`locator` 仍映射回原文下标；15 项测试（正向 7：PDF 句内空格/空行/硬换行/全角空格/制表符/CRLF/多空格；反向 5：改词/删字/换序/跨句拼接/跨材料；夹具 1：4 条历史误拒原样锁；校验层 2）；`scripts/replay_p4_8_quote_rejections.py` 用**当时的真实缓存**复核 **R4.4 2/2、R4.5 1/1、R4.6 1/1 全部放行（4/4）**。另记一条边界：修复会让 R4.5 那条「引文对、概括错」的 claim（题的是蘅芜苑却写成潇湘馆）进入证据层——**引文核验只保证引文在材料里，不保证 claim 忠于引文**，claim–引文一致性校验列为 R4.8 候选 | R4.6 实测 + 用户拍板 | **已修（15 项测试 + 4/4 复核）** |
| D-290 | **固定材料集：把检索/抓取缓存下来回放（`--replay-materials`）+ 强判别探针**。R4.6 暴露的**方法论缺陷**：R4.5 与 R4.6 目标/检索词/参数全同，10 个来源却只有 **5 个重合**（Tavily 排序与索引会变）⇒ 任何"这一轮比上一轮多/少几条"都不可归因（R4.6 的 `traits` 1→0 就是被这个搅的）。**不固定材料，"同一实验做两次"这句话就不成立。**实现 `persona/modules/material_replay.py`：① 缓存只装 tavily 的**成功**往返（模型调用不进缓存——模型必须真实发生）；② 缓存键 = `(url, 请求体原文)`，参数一变就查不到，**刻意如此**；③ **缺项抛 `MaterialReplayMiss`，绝不回落到网络**（"以为在回放、实际偷偷联网"是最坏的静默）；④ **回放不记账**——provider 换一本空账本，真账本不落检索/抓取行，否则费用口径就假了。**强判别探针**（`--probe-target/--probe-about/--probe-text/--probe-index`）：往某目标侧材料里追加一句"另一个人"的说法，测"材料主体 ≠ 检索目标"时主体挂给谁——R4.6 的主体 28/28 区分力弱，正因为 24 条材料满足"目标=内容"，坏模型也能满分。**注入点踩过两个坑并已避开**：往检索摘要注入时，若该摘要正好是被抓全文的源，注入句会被 `MATERIAL_SUPERSEDED`（摘要→全文）静默吃掉 ⇒ 优先注入**抓取全文末尾**；注入改变文本会影响"抓哪一条"的排序 ⇒ 全文末尾不影响检索排序。判定三分支 `OK/FAIL/WARN`（WARN = 探针句没被抽成 claim，本轮没测到，不算失败但要如实说）。27 项测试；R4.6 真实缓存冒烟：search 5 条、extract 1959 字、真账本 0 笔。边界：回放只对**同一批参数**有效（换了检索词就缺项报错），探针会改变材料文本（`traits` 具体文本不可与 R4.6 逐字比） | R4.6 方法论教训 + 用户拍板 | 已实现（27 项测试） |
| D-291 | **`material_key` 被"语义就近"填错 ⇒ 引文核验误杀（与 D-288 不同层）**。R4.7 强判别探针把「贾宝玉自幼厌恶科举功名…称作禄蠹」注入**中青报黛玉页**，模型抽成 claim 后`subject_entity_ids` 挂对了宝玉、`verbatim_quote` 逐字一致，却把 `material_key` 填成**百度百科宝玉页**（该页原文有"厌恶四书和八股文…叫做禄蠹"，语义高度相似）⇒ 核验在该页找不到逐字引文 ⇒ 拒收。**性质**：D-288 错的是"讲谁"（静默且致命），D-291 错的是"出自哪份材料"（有告警但不该丢）；**核验本身执行正确，是上游归因错**。修法候选：① 提示词加"`material_key` 按**逐字出现**选，不按语义相似选"；② 核验两段式——按 key 找不到就在**全材料集**里找，找到则改归属 + `MATERIAL_KEY_CORRECTED` 告警，找不到才拒收；③ 补跨材料语义相似的测试。**注**：②会让"引文对、概括错"更容易进来，必须与"claim–引文一致性校验"一起做。**已修（用户拍板「与 temperature 无关，并行修」）**：`_validate_item` 改为**两段式**——先按声明材料找引文，找不到就在**全部材料**里按**引文逐字**再找：唯一命中 ⇒ 改归属 + `MATERIAL_KEY_CORRECTED`（`source_id`/`locator` 全指向真材料）；多份命中 ⇒ `MATERIAL_KEY_AMBIGUOUS` 并按第一份；哪儿都没有 ⇒ 仍 `QUOTE_NOT_IN_MATERIAL` 拒收。**未做 claim 文本相似度匹配**（claim 是概括、相似度不可判定，且那正是本缺陷的成因）。10 项测试；`scripts/replay_p4_8_material_key.py` 用 R4.7 真实请求/响应复算：**27/27 accepted、0 拒收、仅 1 条改归属**，探针句归回中青报、主体仍是宝玉。旧断言 `test_quote_from_another_material_is_rejected` 按新规则改写（防伪造由 `test_fabricated_quote_is_rejected` 继续守） | R4.7 实测 + 用户拍板 | **已修（10 项测试 + 真实数据 27/27）** |
| D-292 | **回放"不记账"只在内存成立，落库仍记账**。R4.7 全回放运行后 `external_usage_records` 里仍有 **3 条 tavily**（`SEARCH 1.0 + SEARCH 1.0 + EXTRACT 0.0 = 2.0 credits`，`cost_status=CONFIRMED`），`request_id` 是 **R4.6 的真实 id**、`requested_at` 是本轮时刻。根因：`install_replay()` 换的是 `provider.usage_ledger`，内存层确实干净（`[10]` 只有 2 笔模型），但 `research._persist_usage()` 遍历的正是这本**空账本**——回放的 `settle()` 写了 3 笔，随后被原样落库。**影响**：假支出进累计账（`hydrate()` 会当历史消费），且**带真 id，人工对账看起来像真花过钱**。修法：换只读丢弃型账本（`records()` 空 / `settle()` 不落），或 `ReplaySession.replayed_keys` 让 `_persist_usage` 过滤；测试锁"回放后 provider∈{tavily} 的行数为 0"。**已修（用户拍板并行修）**：`install_replay` 换的账本改为 `_DiscardingUsageLedger`（`settle()` 恒 False、`records()` 恒空、`totals()` 自然为零），`ResearchService._persist_usage()` 遍历不到任何回放笔 ⇒ 内存与落库同时干净；"命中过什么"由 `ReplaySession.used` 承担、不再借账本。**走真链路的测试 3 项**（回放→provider 记账→`_persist_usage()`→SqliteStorage，断言 `external_usage_records` 为 0；外加一条**对照**：非回放账本必须照常落库 1 条，否则测的是"落库坏了"） | R4.7 实测 + 用户拍板 | **已修（3 项测试）** |
| D-293 | **`temperature=0.7` ⇒ 固定材料也不等于可对照（方法论 P0）**。R4.7 与 R4.6 参数逐字比对：`model`/`max_tokens`/`thinking`/`response_format`/`system`(4132 字) 全同，**`temperature` 都是 0.7**。结果：材料逐字节相同（仅多 1 句探针），两轮证据只有 **4 条逐字重合，Jaccard 8.0%**；逐源看 5 份材料重合 0 条。**直接后果**：黛玉 `traits` 从 0 变 1（本轮中青报抽到"弱不禁风"、R4.6 抽的是"谨言慎行/阅读"）——**R4.6 把 traits 0/0 归因给"材料漂移"是不完整的**，真实的不稳定源有两个：**每份材料 4 条上限** × **0.7 温度的采样**。**在修掉之前，任何"这轮比上轮多/少 N 条"的对比都不可归因**，包括探针带来的变化。**已修（用户两次拍板）**：查官方 `create-chat-completion`（2026-10-18）**请求体没有 `seed` 参数**（只有 temperature/top_p 等；响应里的 `system_fingerprint` 不可回传）。第一版退到 0.3 + 跑两轮量重合度；**用户随后修正为 `temperature=0`、只跑一轮**（0 温度下同输入同输出，"两轮重合度"这个前提不成立；"0.7 下生产是否稳定"属产品化阶段的多次运行统计，不阻塞技术验证收尾）。**落地**：新增 `DEFAULT_EXTRACT_TEMPERATURE = 0.7`（**生产默认**，注释写明不要固化实验值）；`ProviderClaimExtractor(temperature=...)` / `build_request(temperature=...)` 均可配，`resolve_temperature()` 校验 0–2、非法值**抛错不夹值**；`StackOptions.extract_temperature` → `claim_extractor_from_settings` → 抽取器；CLI `--extract-temperature`；请求 `metadata.temperature` 落盘可对账。**对话是另一个旋钮**：`DIALOGUE_TEMPERATURE = 0.7` + `DialogueService(temperature=...)`，**不随 `--extract-temperature` 变**（不许"为了跑实验顺手把角色说话改得像机器人"）。14 项测试锁"生产默认仍 0.7 / 显式 0 生效 / 非法报错 / 边界 0 与 2 / 对话与抽取互不影响 / 提示词未变" | 官方文档核实 + 用户两次拍板 | **已修（14 项测试）** |
| D-294 | **`ChatRequest.metadata` 全字段不落盘 ⇒ 运行参数事后不可对账（P2）**。R4.8 实测发现：`extraction.build_request()` 把 `prompt_version` / `max_output_tokens` / `material_count` / `max_claims_per_material` / `temperature` / `trait_tag_enabled` / `trait_tags` / `output_ceiling_sufficient` 写进 `ChatRequest.metadata`（`extraction.py:1267`），**但没有任何持久化路径**——`persona/providers/deepseek.py:329` 明确注释"`metadata` **不发送**：含 prompt_version 等内部审计信息，不外发"（**不外发是对的**，不该把内部审计信息交给第三方），而本地也没有落盘；全仓库只有 `cli/main.py:422` 在 dry-run 时打印 `prompt_version`。**后果**：① 只能从 `raw_responses.jsonl` 的 HTTP body 反推 `temperature`（body 里 `temperature` 确实是 **0.0**，所以"温度是否生效"可证），但 `trait_tag_enabled` / 词表指纹 / 输出上限是否够用这些**没有对应的 body 字段，彻底查不到**；② P2 遗留的"`metadata.trait_tag_enabled` 未落库"与本条**同一根因**，不是一个孤立遗漏。**修法（未实施）**：在 `TaskRecord` 或 `audit_records` 增一条 `model_request` 记录（只存 metadata + 幂等键 + request_id，**不存提示词正文**），或让 `raw_responses.jsonl` 的落盘条目带上 `metadata`（本地文件，不涉及外发）。**注意**：本缺陷**不影响"温度=0 已生效"这个结论**（body 是铁证），它影响的是"参数留痕完整性" | R4.8 实测 | **已修**（`_audit_model_request`：发送前写 `audit_records.model.request`，11 项测试 + 离线真链路 + dry 预览；旁路失败记 `AUDIT_WRITE_FAILED` 不阻断；外发零变化） |
| D-295 | **e2e 探针判定有盲区：只查主体、不查 `material_key`**。R4.7 探针判 `WARN` 的真因是 `material_key` 填错（D-291），而脚本判定逻辑只在该源**已登记**的证据里找探针句，看不到被拒收的候选 ⇒ **误报"模型没选中这句话"**。D-291 修好后 R4.8 判 `OK`，但脚本**仍然只打印 `subject_entity_ids`、不打印 `material_key`**——三态里的"`material_key` = 中青报"这一项是靠**人工查库**才确认的，脚本自己没验。**修法（未实施）**：`[8.1]` 直接输出 `source_id` + 对应的 `Source.url`，并把"主体对不对 + `material_key` 对不对"拆成两行分别判定（任一错判 `FAIL`）；同时把 `MATERIAL_KEY_CORRECTED` / `MATERIAL_KEY_AMBIGUOUS` 告警数一并打印。**性质**：测试工具的盲区，不是产品缺陷 | R4.7 + R4.8 实测 | **已修**（全库按字符 bigram 定位探针句、不按 `source_id` 过滤；主体与 `material_key` 两行分判、任一错即 FAIL；11 项测试含"主体对、材料错 ⇒ FAIL"；R4.8 真实数据复算 OK，人为改错 `source_id` 复算 FAIL；旧断言 5 处按新口径改写） |
| D-271 | **`test_strict_gate_makes_a_snippet_only_chain_produce_nothing` 的旧断言被推翻**：该用例曾断言"严格 gate 下只抓摘要的链路产出 0 证据"，并把这解释为"`require_gate_verdict=True` 的定义"。R4.1 证明那是**误杀**（7 条相关来源仍 0 证据）。已改写为断言新语义（摘要放行、模型被调用、产出证据且留痕 `MATERIAL_SUMMARY_ONLY`）。**测试固化了错误理解时，改的是测试，而且要写明为什么** | D-6 / D-264 | 已修 |

---

### 2.32 P6.2 记忆写回：`rule` 策略、评估链路隔离、D-189 修正（2026-10-20，用户拍板）

> 背景：写记忆的管道（`commit_turn(memory_candidates=)` + `write_simulated`）P3.6 就通了，
> 但**没有人自动派生候选**、也没有产品入口，所以默认路径永远是零写入。P6.2 补的正是
> "谁派生、谁授权"这一层。方案见 `docs/P6_2_MEMORY_WRITE_PLAN.md`。

| 编号 | 决定 | 依据 | 状态 |
| --- | --- | --- | --- |
| D-296 | **D-179 扩展：新增会话级策略 `memory_write_policy="rule"`，默认仍是 `none`**。语义：会话创建时**显式**选 `rule` ⇒ `run_round` 用确定性规则引擎（`persona/modules/memory_writeback.py`）从**用户原话**派生候选并写入；`explicit` 保持"调用方自己给候选"；`none` 一律不写。`commit_turn` 的闸门由"必须 == explicit"放宽为"∈ {explicit, rule}"（`MEMORY_WRITE_AUTHORIZED`）。**四条不可让**：① 默认值不改（`SessionDraft.memory_write_policy` 仍是 `none`）；② `none` 下**不得派生**——派生了会被闸门判 `gate_rejected`，默认路径从"零写入"变成"每轮报错"；③ 引擎签名**没有**任何接收模型回答的参数（防回声室）；④ 候选内容必须是用户原话切片（verbatim）。审计新增 `dialogue.memory_derive`，**只记规则版本/候选数/否决代码，不记内容** | 用户拍板（P6.2 决策点 ①） | 已实现（接线 13 项测试 + 变异验证） |
| D-297 | **评估链路必须保持 `memory_write_policy=none`，且做成硬失败**。`scripts/run_dialogue_eval.py` 起每题会话后**读回**策略（`start_eval_session` → `assert_eval_memory_policy`），非 `none` 直接 `RuntimeError`；拿不到该字段同样失败（fail-closed）。理由：`rule` 一旦在评估里开着，记忆会进下一题上下文，**而没有任何断言会发现**——分数会安静地变好看或变难看。另加静态扫描测试，禁止脚本出现 `POLICY_MEMORY_WRITE_RULE/EXPLICIT` 或 `memory_write_policy="rule"` | 用户要求（P6.2-b 第 ② 条） | 已实现（6 项测试） |
| D-298 | **D-189 修正：`write_simulated` 改为走去重判定**（与 `write()` 同口径：`namespace`+`scope`+`scope_id`+`content` 且 `ACTIVE` 时返回既有 id + `MEMORY_DEDUP_HIT`）。D-189 原为"接受不去重"（跨轮同内容各写一条），P6.2-a 起**推翻**：规则引擎每轮都会派生，同一句"我昨天去城南看桃花"连续两轮就会写出两条，并被下一轮当上下文重复注入。**会话之间仍不互相去重**（`scope_id` 不同）。修在写入层而非规则层：漏一个入口就会重复入库 | 用户拍板（P6.2 决策点 ③） | 已实现（dedupe 2 项直接测试 + 集成 12 项） |
| D-299 | **授权确认只有一份实现：`cli/main.py::_authorize_real_calls`**，`persona run` 与 `persona chat` **都调它**。理由：两个命令各写一套确认，用户就会在两个入口遇到两种问法、两种"记住"行为（一边记住了另一边还在问）。同口径包括：同一个问句（`consent.build_question`）、同一个 `.persona_consent.json`（放在**数据库所在目录**，换库即重问）、同一条"非交互必须显式 `--yes`"、同一条"`--yes` 不写确认文件"（脚本的同意不等于人的长期约定） | 用户要求（P6.2-c 第 ② 条） | 已实现（`run` 的授权块原样抽出，P5.1 六分支测试继续绿） |
| D-300 | **对话单价只在一个地方定义：`modules/dialogue.py::DIALOGUE_MEASURED_COST_PER_CALL_USD = 0.0005`**。`persona chat` 的授权清单与 `scripts/run_dialogue_eval.py` 的 `MEASURED_PER_CALL_USD` **都从这里取**（后者改为引用，不再各抄一个数字）。不用 `PERSONA_MODEL_EST_TOKENS_*`：那是给抽取校准的，照搬到对话高估约 50 倍 | 用户要求"费用口径一致"+ 反重复（同 D-298 的合并原则） | 已实现 |
| D-301 | **Ctrl-C 干净中止的退出码是 130，不是 0**。`persona chat` 被中断时：已完成的轮次与记忆**保留**（每轮一个事务，不回滚）、会话**必置 `ENDED`**（`finally` 里 `end_session`；留 ACTIVE 的会话会被下次 `chat` 当成"还能继续聊"）、`KeyboardInterrupt` 不许逃逸。用 130 而非 0：轮次可能只跑了一半，返回 0 会把"半截运行"伪装成完整运行 | 用户要求（P6.2-c 第 ① 条） | 已实现（1 项测试 + 变异验证：把处理分支改成 `raise` 即红） |
| D-302 | **CLI 需要的对话符号由 `persona.orchestrator` 转发，不由 CLI 直连 `persona.modules`**（D-059 / F-7 的静态断言 `test_cli_does_not_import_business_modules` 会红）。转发清单：`SessionDraft` / `POLICY_LEAST_RECENTLY_SPOKEN` / `POLICY_MEMORY_WRITE_NONE` / `POLICY_MEMORY_WRITE_RULE` / `DIALOGUE_MEASURED_COST_PER_CALL_USD`。**只转发、不包装**：语义仍只有领域层那一份，CLI 侧不许出现第二个"策略名字符串" | P6.2-c 实现中被静态断言挡下 | 已实现 |
| D-303 | **记忆检索分两层记账，缺哪一层要分开说**（P6.2-d 真跑实测）：R1 本会话（`scope_id=本会话`，无条件）**已在产品路径生效**——会话 A 第 3 轮起 `referenced_memory_ids` 非空；R2 跨会话**代码在、产品路径永不触发**，因为 `persona chat` 没有 `--topic` 入口、`SessionDraft.topic` 恒 `None`，而"无 topic 不取 R2"是刻意设计（防无关记忆进上下文）。因此**修法是给 `chat` 加 topic 入口，不是放宽 R2 的条件**；在入口出现前，跨会话能力只在存储层与检索端口层成立（直连 `MemoryService.search` 能查到），prompt 注入层不成立 | 用户要求"报告里必须明确说明这次验证了哪一层" | 已定位（P6.3） |
| D-304 | **`PERSONA_MODEL_MAX_CALLS` 数的是"本次进程"的 CHAT 次数**，历史笔只进费用累计（`occupied_cost`）、不进次数（`count_chat_calls` 只看 `ledger.records()`）。所以"换个命令跑几轮"要重看一次上限：`.env` 里的 3 会拦住 4 轮会话。需要临时抬高时用**进程级环境变量**（第 3 层覆盖 `.env` 第 2 层），**不改 `.env`**；`_cmd_chat` 在发起前就会拦住并提示该设多少 | P6.2-d 真跑前发现（`.env` 为 3，会话 A 需 4） | 已记录（RUNNING §5.42） |
| D-305 | **跨会话检索（R2）的入口是显式 `--topic`，本期不做自动提取**。理由两条：① 不装依赖 ⇒ 没有中文分词器，规则法对"我现在觉得你比从前可亲多了"这类句子提不出有效 topic；② 自动提取 = 把"要不要跨会话召回"这个语义决策交给**启发式默认值**，正是 D-A/D-B/D-D 与 `none` 派生陷阱那一族。`--topic` 缺省 = 不跨会话取记忆（与 P6.2 同口径），**不是"默认捞一批"** | 用户拍板（P6.3 决策点 1） | 已实现 |
| D-306 | **topic 门禁是一个纯函数 `topic_is_retrievable()`、两处用法**，`Ok(None)`=无 topic（不报错、不取 R2）/ `Ok(str)`=放行 / `Err`=不合规。`start_session` 用它 **Err**（显式给的坏值不静默丢弃）；`_retrieve_memories` 用它 **跳过 R2**（历史脏行不让整轮失败，读路径不为一条脏数据挂掉）。**防线不能只放 CLI**——只放 CLI 就是"能力只存在于一条路径" | 用户拍板（P6.3 决策点 2） | 已实现（3 条 + 变异 ②） |
| D-307 | **门禁只能卡 topic 本身的质量，不能卡命中之后的打分**。`MemoryService._matches` 是按空白切词 + 子串包含，中文 topic 整串只是一个 term；`_score` 在单 term 命中时 `base` 恒为 1.0，只反映 salience 与新旧，**不反映 topic 宽窄** ⇒ 加 score 阈值是**假防线**。实测依据（`docs/P6_3_TOPIC_PLAN.md` §4.4.1）：真实库 2 条记忆，`topic="我"` 一个字把**两条全捞进来**；`topic="西厢"` 命中 1 条（放行是对的，拦了才是错）；`topic="西厢记记"` 0 条（拼错属于"取不到"，不是"取错"） | 方案阶段对真实库实测（零外呼） | 已实现 |
| D-308 | **停用词按长度降序剥离后判空，且检索用原值**。"出现即拒"会误杀 `我们的西厢记`（很具体的话题）；剥离后判空才能区分 `我`（拒）与 `我们的西厢记`（放行）。检索**不按残留改写查询**：不精简、不纠错，门禁只回答"让不让取" | 用户要求"停用词表要明确列出"+ 配套变异测试 ④⑤ | 已实现 |
| D-309 | **跨会话验收分两层，归属要分开**：第一层看 `referenced_memory_ids` 是否非空（检索层，自动断言）；第二层看回答是否含具体词（如"西厢记"/"崔莺莺"）且**不含"资料没写/没有记载"**（行为层，人工判定，防"泛泛而谈看起来对了但没真用上记忆"）。**第一层过、第二层不过 ⇒ prompt 问题，记为已知局限，P6.3 照常判通过**（与 P6.0 里 E1 的处理一致，不为此放宽判据） | 用户要求（P6.3 验收标准） | 已实现（真跑 B′ 两层全过） |

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
| 2026-10-17 | v0.18 | 新增 §2.27（D-245 ~ D-249）：P4.7 验收与整改——① **装配层默认禁止真实外呼**（`allow_real=False`，未授权记 `REAL_PROVIDER_BLOCKED`；CLI `--allow-real` 才授权；配置层与装配层是两个开关，D-245）；② `--material` 不替代模型（D-246）；③ **G12 登记**：跨源同句 ⇒ `traits` 两条同文本条目，单独立轮（D-247，详见 `RUNNING.md` §5.16）；④ P1a **S3 并入阶段三**（D-248）；⑤ `usage list` 暂缓、先定口径（D-249）。事件背景：P4.7 期间 1 次未授权真实调用 0.0019161 USD，累计 **0.0097980 USD**，人工账本不重置 | 已实现 / 已登记 |
| 2026-10-17 | v0.19 | 新增 §2.28（D-250 ~ D-253）：P4.8 R1 + R3——① **G12 修复采用方案 A**（条目身份 = 维度+归一化说法+主体的 sha256 摘要，跨源同句合并、引用全收，旧档案不迁移，只合并归一化完全相同者）；② **检索出口接线**（`search_provider_from_settings`，`max_retries` 强制 0，与模型共用 `allow_real` 闸门，`--material` 时不建检索出口）；③ **调用清单**（`plan_calls`：检索/抓取/模型次数 + 金额 + 计量单位，清单外调用即缺陷）；④ 顺手修 `research.*` 配置在产品路径被忽略的缺口（D-253）。全量 1301 项通过；**未跑真实检索** | 已实现 |
| 2026-10-17 | v0.20 | 新增 §2.29（D-254 ~ D-258）：P4.8 R4 首次真实检索——① **检索词必须带作品名**（D-254，已修）；② **过滤阶段无相关性判定**须补齐，登记 ≠ 可用（D-255，未修）；③ **对话模型调用必须进逐笔账本**（D-256，未修，下一轮第一项）；④ **清单对话次数按"每轮 1 turn"标定**（D-257，未修）；⑤ 真实检索 e2e 复现纪律（dry 物理断网 / 增量落盘 / 只读重放，D-258）。R4 实际：1 检索成功 + 1 超时 + 1 抓取 + 1 模型，清单外调用 0；0 证据 / 0 traits / `INSUFFICIENT_EVIDENCE`（门禁有效） | 已实现 / 已登记 |
| 2026-10-17 | v0.21 | 新增 §2.30（D-259 ~ D-263）：P4.8 R4 整改三项——① **对话与抽取共用同一套预算闸门与结算**（新建 `persona/modules/model_budget.py`，`DialogueDeps` 补 `ledger`/`usage_sink`/`limits`/`task_id`，D-259）；② **离线替身须显式声明 `offline=True` 且不记账**（未声明一律按真实出口记，D-260）；③ **`filter_and_rank` 加硬相关性判定**，未命中目标名/作品名即进 `rejected`，无词表可判时告警 `RELEVANCE_NOT_CHECKED`（D-261）；④ **`plan_calls` 对话次数改为 `max_rounds`**（D-262）。全量 **1326 项通过**（上一基线 1305，+21）。R4.1 换角复跑定为福尔摩斯/华生，**清单已出、授权待批**（D-263） | 已实现 |
| 2026-10-17 | v0.22 | 新增 §2.31（D-264 ~ D-267）：P4.8 R4.1 换角复跑（福尔摩斯/华生）实测——① **D-6 是主路径唯一卡点**：检索摘要天生无门禁结论，抓取一失败就必然 0 证据，这是**误杀不是门禁有效**（D-264，未修，三个方向待拍板）；② **相关性判定须做繁简归一**，繁体「福爾摩斯」被误杀（D-265，未修）；③ 报告器缺陷修报告器 + `--report-only` 重放，**不为打全数字再花钱**（D-266）；④ 换角有效：来源 7 条全部真实相关（D-267）。实际 2 检索 + 1 抓取 + 1 模型，**清单外调用 0**；D-3 验证通过（出现 deepseek 行）、D-2 生效但有 1 条误杀；仍是 0 证据 / 0 traits。费用：2 credits + 0.0001020 USD | 已实现 / 已登记 |
| 2026-10-17 | v0.26 | 新增 §2.32 续（D-276 / D-277）：**D-276 按方案 B 修完**——① 提示词加"每份材料最多 N 条"**硬约束**（`DEFAULT_MAX_CLAIMS_PER_MATERIAL=4`，`ProviderClaimExtractor(max_claims_per_material=…)` 可配），并**写明为什么**（"写超了会被从中间截断，整批连同已写好的部分一起作废"）——只说"最多 4 条"会让模型理解为"少写一点"，说清后果它才会**挑重点**而不是**少写**；② `PROMPT_VERSION` → `g2-extract-2026-10-17`（提示词一改版本必须 +1，幂等键由它参与生成）；③ **N 与输出上限共用同一个值**，`estimate_output_tokens` 因此从"猜测"变成**上界**：9 份 × 4 条 = 36 条 = 7456 token，正好装进算出来的上限；④ D-277：N=4 的保证只到 9 份材料（10 份要 8256 > 8192），撞界时用 `OUTPUT_CEILING_TIGHT` 在发请求前告警，**不静默调小 N**。测试 21 项（含"9 份 × 4 条 = 36 条装进 7456"、提示词含约束、版本已 +1、N 可配且同时驱动提示词与上限）。全量 **1364 项通过**（基线 1354，+10；累计自 1343 起 +21） | 已实现 |
| 2026-10-17 | v0.25 | 新增 §2.32 续（D-276）：**D-272 按方案 ② 修完**（动态输出上限 `256 + 材料数×4×200`，夹 `[4096, 8192]`，下限 4096 的理由是"省下来的额度毫无价值，一旦截断，已花的钱和已生成的 claims 一起作废"）。**D-273 顺带做了告警留痕**：去重跳过的告警写明"跳过 ≠ 没有材料可抽"并附上次调用结果；阶段结果被覆盖时把上次失败原因并进 `PRIOR_ATTEMPT_SUPERSEDED`（D-273 本体仍未修，只做告警）。新增 `tests/test_p4_8_output_ceiling.py` 11 项（含"9 份材料 / 21 条 claim 不被截断"）。**D-276（未修）**：上限有顶（8192 ≈ 39 条）而**需求没有上界**——R4.2 实测**单份**正文就产出 20 条，9 份可能要 44~100 条 ⇒ 按现状跑 R4.3 **仍可能截断**。建议加"每份材料最多 N 条"硬约束给需求加上界（方案 B）。D-274 / D-275 登记为下一轮候选。全量 **1354 项通过**（+11） | 已实现（D-276 待拍板） |
| 2026-10-17 | v0.24 | 新增 §2.32（D-272 ~ D-275）：**R4.2 诊断性复跑实测**——① **D-6 实测验证通过**：抓取成功（百度百科 2255 字）、正文 VALID 1 份 + `SUMMARY_ONLY` 8 份全部进抽取器、模型**真的被调用**，0 证据与门禁无关（D-264 据此关闭）；② **D-7 实测验证通过**：繁体「福爾摩斯的懸疑世界」正常登记为来源，`CANDIDATES_IRRELEVANT` 拒收 0 条（D-265 关闭）；③ **D-272 成为主路径新卡点**：`extraction.py:856` 硬编码 `max_output_tokens=4096`，9 份材料 7758 in → 输出撞满 4096（`finish_reason=length`）→ JSON 截断非法 → 21 条可用 claims（TRAIT/HABIT/EXPERIENCE/RELATION）**整批作废**。比 D-6 更致命：D-6 不花钱，D-272 **花了 0.0072426 USD 买回 0 条证据**（未修，四个方向待拍板，倾向方案 ② 提高/动态化输出上限——零新增调用、仍在清单内）；④ D-273 阶段重试与幂等键冲突，失败被覆盖成 SKIPPED；⑤ D-274 预估 0.0033 vs 实际 0.0072426（2.2×）；⑥ D-275 登记来源数 10 ≠ 唯一来源数 8（R4.1 已发现未修）。实际 2 检索 + 1 抓取 + 2 模型，**清单外调用 0**；D-3 复核通过（2 行 deepseek）。费用：2 credits + 0.0073464 USD | 已登记（待拍板 D-272） |
| 2026-10-17 | v0.23 | 新增 §2.31 续（D-268 ~ D-271）：**D-6 按方案 ① 修完**——① 新增 `QUALITY_SUMMARY_ONLY` 一等状态，检索摘要携带它进入材料流，**门禁只负责"亮结论"、不判死刑**，最终可用性交证据层 `tier`（D-268）；② `""`（信息缺失）与 `SUMMARY_ONLY`（信息完整）区别对待，前者严格模式仍拒收（D-269）；③ **D-7 字形归一**：自建 `persona/modules/text_variants.py`（`fold_variants`，404 字，两侧都折 → 双向可对上），**不引第三方库**（D-270）；④ 推翻并改写 `test_strict_gate_makes_a_snippet_only_chain_produce_nothing` 的旧断言（D-271）。新增 `tests/test_p4_8_summary_gate.py` 覆盖三条通道（摘要/正文/抓取失败退回）+ 繁简双向。全量 **1343 项通过**（+17） | 已实现 |
| 2026-10-17 | v0.27 | 新增 §2.33（D-278 / D-279）：**R4.3 真实运行实测**——① **D-272 + 方案 B 双双验证通过**：`finish_reason` 由 `length` 变 **`stop`**，输出占用 **3803/7456 = 51%**，17 条 claims 全保留；7 份材料全部有产出，2 份（百度百科正文、大英图书馆）**正好顶到 N=4**，证明提示词硬约束被遵守；② **VERBATIM 引文 16/16 逐字命中**（另有 1 条候选因 `QUOTE_NOT_IN_MATERIAL` 被拒收，纪律生效）；③ **D-278（新卡点，待拍板）**：`traits` 仍为 0，根因是证据层说法身份用逐字归一化，跨源同义归并不了 ⇒ stability 恒 SINGLE ⇒ 闸门挡下；**已用数据排除另两种归因**（输出只用了 51%，不是 N 太严；材料有 5 组跨源同义含 1 组 PATTERN 级，不是材料不够）；④ **D-279**：报告器自检用字面量 `"RUN_SUCCEEDED"` 而常量值是 `"SUCCEEDED"`，把成功的运行报错成 FAIL，已改引用常量。实际 2 检索 + 1 抓取 + 2 模型，**清单外调用 0**，五阶段全 EXECUTED，任务 **SUCCEEDED**，16 条证据。费用：2 credits + 0.0069888 USD（累计模型 0.0243357 USD）。D-274 复核：合计接近（1.06×）是抽取低估 2.06× 与对话高估 0.06× 相互抵消的**巧合**，单次预估仍不可当预算 | 已登记（待拍板 D-278） |
| 2026-10-17 | v0.28 | 新增 §2.34（D-280 / D-281）：**D-278 的修法定为"受控特征标签 `trait_tag`"（方案已出，实现待授权）**——核心是**把归一化前移到抽取时**：模型只做归纳（从受控词表选标签），不做判定（不比对两条说法是否等价），判定交确定性规则（`dimension+subject+trait_tag` 三元组）。与方案 A 的本质区别写进条目：**贴错标签是精确率问题，让模型判等价是可靠性问题**，AC-7 约束的是后者。**已否决 A（违反 AC-7）/ B（违反 D-012/PF-V2）/ C 实体锚点（脆弱、不可迁移、误并风险高）**。配套设计：词表三层（L1 通用 ~32 个 / L2 领域可插拔默认空 / L3 自定义）、只对 TRAIT/HABIT/SPEECH_STYLE 启用且双向硬校验、`null` 是合法出口（**没有它模型会被迫乱贴**）、词表外拒收、归并键须唯一实现（现两处各写一遍，D-3 教训）、`PROMPT_VERSION` +1、误并告警用字符重合度不用模型。方案见 `docs/P4_8_D280_TRAIT_TAG_PLAN.md`（含 §8 十项离线验收 + §11 三个待拍板点）。**本轮同期已实现 D-281 `evidence_anchors`**（保守版，零成本零风险）：只读证据锚点，由服务计算、**不进 traits、不参与 coverage**、计数落盘前重算（可以留空、不可以编造）；测试含**反向断言**（跨源同义仍各占一行）钉死"它不解决 D-278"。新增 `tests/test_p4_8_evidence_anchors.py` 15 项。全量 **1379 项通过**（基线 1364，+15） | 已实现（D-281）/ 方案待授权（D-280） |
| 2026-10-17 | v0.29 | 新增 §2.35（D-282 ~ D-285）：**D-280 的 `trait_tag` 词表离线回放**（R4.3 的 16 条证据 × 草拟 L1 词表 31 个，贴标由人完成，脚本放系统临时目录**不进仓库**，零外部调用零费用）——① **只上 `trait_tag` 不会让 `traits` > 0**：6 条可贴标证据按 `(维度, 主体, tag)` 归并 ⇒ **0 组可归并**。根因是**复合说法**（一条 claim 装 3~6 个事实，只能贴一个标签，两个来源碰巧同标签的概率仅 ~6%~8%），而复合说法源自 D-276 的 `max_claims_per_material=4`（逼模型把多事实压缩进一条 claim）——**防截断的措施制造了让归并失效的东西，两个改动互相打架**。反事实：原子化后 **3 组可归并**，预估 `traits` 0 → 1、`habits` 1 → 3 且有多源引用；② **归并键不应含 `dimension`**（与 `_stability_groups` 的 G6-D3 冲突；小提琴/可卡因/烟斗在不同来源被抽成不同维度），改为 `(主体集合, tag)` + **标签自带归属族**（板块由标签族决定，冲突记 `TRAIT_TAG_DIMENSION_MISMATCH`）；③ **`TRAIT_TAG_DIVERSITY` 字符重合度告警无区分度，建议取消**：真归并组 `analytical` 的组内重合度是 0.000，明显不该并的对照对也是 0.000，取值完全重叠 ⇒ **同义必然异形，用字面像不像判语义对不对，方向是反的**；改为在 `uncertainty_note` 如实写「由 N 源 M 条说法归并」；④ 词表覆盖率 92%（干净 64% / 勉强 28% / 回退 null 8%），17/31 标签未用上但**不据此删**（单样本）。**结论：`trait_tag` 必须与 claim 原子化一起做，否则 R4.4 会拿到与 R4.3 相同的 `traits=0`**。报告见 `docs/P4_8_D280_VOCAB_REPLAY.md`（§9 列了四个待拍板点）。本轮**未写任何实现代码、未跑真实检索、未装依赖、未提交 Git** | 已定（待拍板） |
| 2026-10-18 | v0.30 | 新增 §2.35 续（D-286 / D-287）：**R4.4 换角实测 + A/B 两项整改**——① R4.4（林黛玉/贾宝玉）验收通过：3 组归并、黛玉 `traits=2`/`habits=1`、两本账对上（8 组 == 8 条目）、账本 5 笔全 CONFIRMED、输出 4405/8192 未撞顶；宝玉 `traits=0` 精确归因为**单源**（G6-D2 单源不得自证），非词表/非归并逻辑，**AC-7 正确行为，不修**。② **D-286**：L2 中文古典层 11 个标签（`SPEECH_STYLE` 空族补齐 4 个 + 缺口 7 个），默认仍走 L1；顺带修"词表不进幂等键 ⇒ 切层复用旧结果"的真 bug（`TraitVocabulary.fingerprint()`）。③ **D-287**：规则 9/10 冲突裁决（事实多于 N 时"宁可少写不要合并"、禁止尾随评价）+ `TOKENS_PER_CLAIM` 180（10 份材料不再误报告警）+ `CLAIM_NOT_ATOMIC` 批级温度计，`PROMPT_VERSION` → `g2-extract-2026-10-18b`。④ **离线回放**（`scripts/replay_p4_8_l2.py`，零调用）：只换词表 ⇒ 误并**仍在**（证明"词表是候选格、贴法才决定落格"）；按 L2 重贴 ⇒ 误并**消除**、黛玉 `expression_style` 首次非空，但 `traits` 2 → **1**（原来那条是错贴凑出来的假重复）；宝玉仍 0（单源，A/B 都修不了）。可贴标签维度覆盖率 12/12，L2 新标签本轮用上 4 个（`caustic`/`frail`/`egalitarian`/`traditional`），其余 7 个材料未触发、**不据此删**。报告 `docs/P4_8_L2_ZH_REPLAY.md` | 用户拍板 A+B；R4.4 已验收 |
| 2026-10-18 | v0.31 | 新增 §2.36（D-288）：**R4.5（L2 词表）实测暴露 P0 主体错绑**——L2/原子化两项整改本身生效（标签 0 自造、`CLAIM_NOT_ATOMIC` 34.6% vs 上轮 76%、输出 4254/7456 未撞顶、费用 0.0084315 USD + 3.0 credits），但 26 条证据里 **25 条主体错绑**（讲宝玉的全挂黛玉 ID、讲黛玉的全挂宝玉 ID），两个 `traits` 条目互相张冠李戴，而**条目数/组数完全对得上**⇒ 只验数量会误判通过。根因：提示词只给裸 ID 列表、不给名字映射（`extraction.py:1233`），**R4.4 的 22/22 全对是无保证的正确**（当轮材料内容与目标恰好一致，模型按顺序蒙对）。另记清单口径错误："抓取 ≤1" 应写 "≤1 初始 + ≤1 补救"（product 里两份额度独立，本轮 2 次未多花钱）。报告 `docs/P4_8_R45_REAL_SEARCH.md`；R4.4 报告已加追溯注记 | R4.5 实测 | 待修 |
| 2026-10-18 | v0.32 | **D-288 已修（P0 主体错绑）**：① `extraction.py` 新增 `target_labels_of(targets)`（与 `target_ids_of` **同源同序**的唯一口径，对象/dict 都认，缺名字保留空串不丢弃）；`_system_prompt` 主体段从裸 ID 列表改为「名字 = ID」逐行映射，并写明三条约束——「材料讲谁就填谁」「不要按列表顺序填 / 不要因为材料是为某目标检索的就整份算给他」「列表外的人不输出那条 claim」。② 新增 `TARGET_NAME_MISSING` 告警：`display_name` 为空时提示词只能退化成裸 ID，运行视图必须看得见。③ `PROMPT_VERSION` → `g2-extract-2026-10-18c`。④ **清单口径修正**：`CallPlan` 增 `remediations` 字段，`as_lines` 输出「≤2 次（1 初始 + 1 补救）」；脚本 `EXPECTED_EXTRACTS_INITIAL` / `EXPECTED_REMEDIATIONS_MAX` 分开，自检阈值 2（R4.5 的"清单外调用"是清单漏算，不是产品越界）。⑤ **离线回放**（`scripts/replay_p4_8_subject.py`，零调用）：R4.5 的 26 条证据 **22 条主体被纠正**，双主体 #4 不变、无人物 3 条单列；纠正后两个档案的 `traits` **正好换回来**（黛玉=富有诗意美、宝玉=厌恶四书），黛玉/宝玉各 1 条 `traits`。⑥ **12 项新测试**含"区分力对照"（换盲填模型必须复现 R4.5 的张冠李戴，证明测试抓得住）。全量回归 1459 passed / 0 failed | 用户拍板「立即修，零成本」 | 已实现 |
| 2026-10-18 | v0.33 | **R4.6 实测（D-288 修复后首跑）**：① **主体错绑消失**——28 条证据 **28/28** 主体正确（R4.5 是 1/26），`caustic` 落在黛玉 `expression_style`、宝玉 `expression_style` 是自己的"泻玉→沁芳"，串味 0 条。**但必须写明边界**：本轮 24 条所在材料"检索目标 = 内容主体"，坏模型也能满分，强判别样本 0 条 ⇒ 只判"达标"，**不判"修复被真实模型验证"**（与 R4.4 的 22/22 同型"无保证的正确"）。② **两个 `traits` 都是 0**（预期各 1）：宝玉"厌恶四书/禄蠹"归属正确但 `distinct_sources=1` ⇒ D-109 单源不得自证 ⇒ `single_events`；黛玉"富有诗意美"本轮 11 份材料中 **"诗意" 出现 0 次** ⇒ 说法不存在。**归因是材料集漂移**：同词同参两轮 **10 源只有 5 源重合**，R4.5 的第二来源（`維基大典`、`红楼梦` 站）本轮未检索到，11 个标签组全部单源、`REPEATED` 组数 0，与 `traits`=0 **两本账对得上**。③ 其余：`CLAIM_NOT_ATOMIC` 34.6%→**25.0%**；L2 用上 **7/11**（`caustic` 首次用上，推翻"供给缺口"预期；`devoted`/`sensitive` 被每份 4 条上限挤掉、`tactful` 无供给、`reserved` 疑似词表少"谨慎自持"一格）；覆盖率 11/13=84.6%；输出 4005/7456=53.7% `stop`；费用 **0.0081585 USD + 2.0 credits**（抓取成功但官方计费 0）。④ **新缺陷 D-289**（引文核验折空白实现错误，三轮 4/4 拒收全是误杀）。⑤ R4.7 首要变量不是换角，是**固定材料集**（否则任何条数对比不可归因）。报告 `docs/P4_8_R46_REAL_SEARCH.md` | R4.6 实测 | 已登记（D-289 待授权） |
| 2026-10-18 | v0.34 | **D-289 已修 + D-290 固定材料集 + 强判别探针（用户拍板「先修 D-289 + 做固定材料集，再跑 R4.7，不换角」）**：① `_norm_ws`/`_norm_index_map` 由"压成一个空格"改为**丢弃空白**，`locator` 仍映射回原文下标；15 项测试（正向 7 / 反向 5 / 4 条历史误拒夹具 / 校验层 2）；`scripts/replay_p4_8_quote_rejections.py` 用当时的真实缓存复核 **R4.4 2/2、R4.5 1/1、R4.6 1/1 全部放行**；同步修掉 `verify_p4_5a_outputs.py` 里同名独立实现。② 新增 `persona/modules/material_replay.py` + e2e `--replay-materials`：缓存 tavily 成功往返、**缺项即报错不回落到网络**、**回放不记账**（provider 换空账本，真账本 0 笔）；27 项测试；R4.6 真实缓存冒烟 search 5 条 / extract 1959 字 / 真账本 0 笔。③ 强判别探针 `--probe-target/--probe-about/--probe-text/--probe-index`：优先注入**抓取全文末尾**（避开"摘要被全文取代"与"影响抓取排序"两个坑），判定 `OK/FAIL/WARN`。④ 全量回归 **1501 passed / 0 failed**（基线 1459，+42）。⑤ **R4.7 清单已出**（`docs/P4_8_R47_PLAN.md`，待授权）：回放材料 + 真实模型，**0 credits + ≈0.0085 USD**；目标仍是拍板项（建议仍用林黛玉+贾宝玉，否则材料不可比）。**未提交 Git** | 用户拍板 | 已实现 |
| 2026-10-18 | v0.35 | **R4.7 实测（全回放材料 + 真实模型）**：14.19 s，`SUCCEEDED`，**2 次真实外呼（全部模型）**、清单外 0；检索/抓取真实 0。① **强判别探针**：脚本判 `WARN`，但原始响应证明**模型抽出了探针句且主体挂给了宝玉**（`pe_5c92…`），失败在 `material_key` 被填成百度宝玉页 ⇒ 拒收 ⇒ **新缺陷 D-291**；脚本 `WARN` 归因"模型没选中"是错的（判定逻辑只在该源证据里找，看不到被拒收的候选）。② `traits` **黛玉 1 / 宝玉 0**（不是 0/0）：黛玉靠 `frail` **2 源 REPEATED**（中青报+维基）；宝玉 `rebellious` 仅 1 源 ⇒ **结构性单源**，放行探针句也不改变。③ **D-289 生效**：R4.6 那条误拒已放行（`impulsive`，`SINGLE`，落 `single_events`）；拒收数 1 的成因换成 D-291。④ `caustic` **连续两轮用上**（黛玉 `expression_style`）⇒ 供给缺口已被中青报正文补上。⑤ 费用 **0.008403 USD**（内存账本 2 笔、未知 0）；**落库另有 3 条 tavily = 2.0 credits 假支出** ⇒ **D-292**。⑥ **对照资格被证伪**：材料逐字节相同，两轮证据仅 **4 条重合（Jaccard 8.0%）**，因 **`temperature=0.7`** ⇒ **D-293**；R4.6 的"材料漂移"归因需补成"材料 + 采样"两源。其余：主体 26/26、`CLAIM_NOT_ATOMIC` 8/26=30.8%、L2 用上 6/11（未触发 attractive/devoted/incisive/sensitive/tactful）、覆盖率 11/11=100%（分母缩小）、输出 4190/7456=56.2% `stop`。**未提交 Git** | 用户授权 R4.7 | 已执行 |
| 2026-10-18 | v0.36 | **seed 核实 + D-291/292/293 三项已修（用户拍板：先核实 seed、并行修两缺陷、再出 R4.8 清单）**。① **核实**：DeepSeek `create-chat-completion` 请求体**无 `seed`**（`system_fingerprint` 只是响应侧配置标识、不可回传）⇒ "seed + 0.7" 不存在，按规则先退 **`temperature=0.3`**（v0.37 按用户修正改为可配置 + 0 温度单轮）。② **D-291 已修**：引文核验两段式（声明材料找不到 → 全材料按**引文逐字**找 → 改归属/标歧义/仍拒收），**未做 claim 相似度匹配**（那正是缺陷成因）；10 项测试；`scripts/replay_p4_8_material_key.py` 用 R4.7 真实数据复算 **27/27 accepted、0 拒收、仅 1 条改归属**，探针句归回中青报且主体仍是宝玉；旧断言按新规则改写 1 条。③ **D-292 已修**：`_DiscardingUsageLedger`（settle 恒 False / records 恒空），真链路测试断言 `external_usage_records` 零新增 + **对照**（非回放必须落库 1 条）。④ **D-293 已修（后按用户修正改为可配置 + 0 温度单轮）**：见 v0.37。⑤ 全量回归 **1519 passed / 0 failed**（基线 1501，+18：10+3+5）。⑥ **R4.8 清单已出**（`docs/P4_8_R48_PLAN.md`，**待授权**）：见 v0.37（已改为单轮 0 温度）；已预警"探针句进库会让宝玉 `traits` 0→1，属探针污染不算能力提升"。**未提交 Git** | 官方文档核实 + 用户拍板 | 已实现 |
| 2026-10-18 | v0.37 | **用户修正 R4.8 方案：抽取 `temperature=0`、单轮（不是 0.3 / 两轮）**。理由：0.3 仍有采样随机性、只能靠两轮量重合度；改成 0 后"同输入同输出"是前提，**"两轮重合度"这个指标本身失去意义**（理论上 100%，不验）；"0.7 下生产是否稳定"属产品化阶段的多次运行统计，不阻塞技术验证收尾。**代码**：① 温度做成**可配置参数**——`DEFAULT_EXTRACT_TEMPERATURE = 0.7`（生产默认，注释明确写着"生产环境默认恢复为 0.7"、不要把实验值 0 固化）、`resolve_temperature()` 校验 0–2 且非法值抛错不夹值、`ProviderClaimExtractor(temperature=)` / `build_request(temperature=)` / `StackOptions.extract_temperature` / CLI `--extract-temperature`，请求 `metadata.temperature` 落盘可对账；② **抽取与对话分开配置**——新常量 `DIALOGUE_TEMPERATURE = 0.7` + `DialogueService(temperature=)`，**不随 `--extract-temperature` 变**，防止"为了跑实验把角色说话改得像机器人"；③ `tests/test_p4_8_extract_temperature.py` 重写为 **14 项**（生产默认 0.7 / 显式 0 生效 / 非法报错 / 边界 0 与 2 / 对话与抽取互不影响 / 提示词与材料未变）。**全量回归 1528 passed / 0 failed**（基线 1519，+9）。**R4.8 清单已重写**（`docs/P4_8_R48_PLAN.md`）：**单轮、2 次模型调用、≈0.0085 USD、0 credits**；硬指标改为用户给的 5 条（探针 OK / 回放零落库 / 探针污染 traits 的实际影响 / 中青报是否 5 条 / 费用），**删除"两轮重合度 >50%"**。**未提交 Git** | 用户修正 | 已实现 |
| 2026-10-18 | v0.38 | **R4.8 实测（回放材料 + `temperature=0` + 强判别探针）**：`SUCCEEDED`，**2 次真实外呼（全部模型）**、检索/抓取真实 0、清单外 0、回放缺项 0。① **探针三态全过**：探针句入库（`VERBATIM`）、`subject_entity_ids` = **宝玉**、`material_key` → `source_id` = **中青报**（`src_f13d8e…`）⇒ **D-288 + D-291 在真实模型上同时验证通过**，这是 R4.4 之后**第一个真正的强判别样本**（材料主体 ≠ 检索目标）。② **`provider=tavily` 行数 = 0**（落库仅 2 条 deepseek）⇒ **D-292 修复确认**（R4.6/R4.7 各有 3 条假支出 2.0 credits）。③ **宝玉 `traits` 0→1**，`rebellious` 2 源（百度宝玉页 + 中青报探针）；**反事实**：去掉探针百度页只有 1 源 ⇒ 仍 `SINGLE` ⇒ 仍是 0，故 **0→1 完全由探针造成**，按预期记录、不计能力提升。④ **中青报仍是 4 条，不是清单预测的 5 条**——**我的预测错了**：N=4 是提示词硬上限，探针占了 1 个名额、**挤掉了黛玉的 `melancholy`（常常伤感流泪）**。教训两条：**探针不是"加一条"而是"换一条"**；只验条目数会漏掉这个变化（黛玉 `traits` 仍 1，看不出来）。⑤ 费用 **0.0075378 USD + 0 credits**（估 0.0066 / 实 1.14×），2 笔全 `CONFIRMED`；**但 `metadata.temperature` 未落盘** ⇒ **D-294**（`metadata` 不外发是对的，但也没有本地落盘；能查到的只有 HTTP body 的 `temperature=0.0`）——**我上一轮"metadata.temperature 落盘可对账"的说法是过度陈述，已在报告中更正**。⑥ **D-295**：e2e 探针判定只查主体不查 `material_key`，R4.7 的盲区仍在，本轮靠人工查库才确认三态。⑦ 其余：主体 **24/24**（22 单 + 2 条 RELATION 双主体）、**拒收 0**、`CLAIM_NOT_ATOMIC` **0/24=0.0%**（R4.7 30.8%，单轮 N=1 不下"已解决"结论）、L2 用上 **9 个**标签、可贴标覆盖率 **12/12=100%**、`finish_reason=stop` 输出 3457/7456=46.4%、`caustic` 连续三轮用上。⑧ **判定**：5 条硬指标中 ①②③ 通过、④ 与预测不符（观察项非缺陷）、⑤ 费用/账本过但 `metadata` 落盘不过 ⇒ **按用户规则暂不宣布"技术验证完成"**，先修 D-294。报告 `docs/P4_8_R48_REAL_SEARCH.md`。**未提交 Git** | 用户授权 R4.8 | 已执行（D-294/D-295 待修） |
| 2026-10-18 | v0.39 | **D-294 + D-295 已修（用户拍板方案 A：先修再宣布技术验证完成）**。① **D-294**：`ProviderClaimExtractor.extract()` 在 `build_request()` 之后、`provider.chat()` **之前**写一条 `audit_records.model.request`，落 `temperature` / `prompt_version` / `trait_tag_enabled` / `trait_tags` / `max_output_tokens` / `material_count` / `max_claims_per_material` / `output_ceiling_sufficient` 八项；**P2 遗留的 `trait_tag_enabled` 未落库同根因一并修好**。三条约束：**旁路**（写失败记 `AUDIT_WRITE_FAILED` 告警、不阻断抽取，"以为留痕了其实没有"比没有更糟）、**不改外发**（`detail` 只进库不进请求体，`DeepSeekProvider` 的"metadata 不发送"保持不变）、**发送前写**（失败的那次也要留参数）。老 `usage_sink`（只有账本两方法）用 `getattr` 探测、不因新增方法失效。**11 项测试**：字段齐全 / 显式 0 记 0 / 审计值与真请求同源 / 无词表记 false / 失败调用仍审计 / 老 sink 不崩 / 抛异常与返回 Err 都不阻断且告警 / 不改外发请求 / **真 SQLite 可 SQL 查询** / 一次调用一行。**dry 清单新增"本次将落盘的审计字段"预览**。② **D-295**：探针判定改为**全库按字符 bigram（阈值 0.5）定位探针句、不按 `source_id` 过滤**——旧实现先过滤再查找，`material_key` 这一项**结构上不可能判错**（R4.7 的 WARN 误报即源于此）；主体与 `material_key` **两行分判、任一错即 FAIL**，全库找不到才 WARN（文案明确"拒收"与"没选中"在证据层同形）。**11 项测试**含用户点名用例"主体对、材料错 ⇒ FAIL"；**R4.8 真实数据复算**：原样 OK（相似度 0.79）、人为把探针句 `source_id` 改成百度宝玉页复算 **FAIL** 并打出 `[OK] 主体 / [FAIL] material_key`。旧测试 5 处按新口径改写（4 处夹具文本 + `test_evidence_from_other_sources_is_ignored` 断言由 WARN 反转为 FAIL，**旧断言本身就是盲区**）。③ **一处偏离已说明**：用户要求"跑 dry-run 确认落盘"，但 `--dry` 在 `[4]` 返回、不执行五阶段、物理上不产生审计行；改用**离线真链路**（真抽取器 → 真 build_request → 真 `append_audit` → 真 SQLite + mock provider + `network_blocked()`）验证，比 dry 更硬。④ 全量回归 **1550 passed / 0 failed**（基线 1528，+22）。零外呼、零费用。⑤ **产品化路线建议**已写入 `docs/P4_8_D294_D295_FIX.md` §4：三道坎（**质量稳定性最要命**——0.7 温度下两轮 Jaccard 8%，建议"质量闸门 + 条件重跑"方案 C / CLI 收口缺 `--vocabulary` `--extract-temperature` `--out` 与 `show`/`evidence`/`export md` / 文档与分发不急于 PyPI）、四阶段 P5.0~P5.3 约 2~3 周、两条纪律（探针不进产品路径、"证据不足"必须如实说）。**未提交 Git** | 用户拍板方案 A | 已实现 |
