# RUNNING —— 怎么把代码跑起来（P3 第二步更新）

- **版本**：v0.16（G-2：来源材料 → 候选 claim 的自动抽取）
- **适用**：Python **3.12+**。本轮**实测通过**：**3.13.14 / 3.14.7**（各 694 项）；
  3.12.10 为既往轮次实测（当前开发环境未安装 3.12 解释器，本轮未能复跑，特此注明）。
  ⚠️ 托管解释器的**安装目录名是 `3.13.12`，实际版本是 3.13.14**（`python --version` 实测），以实际版本为准
- **依赖**：**零第三方依赖**。只用标准库 `unittest` / `sqlite3` / `dataclasses` / `argparse`
- **网络**：**不需要**。Provider 全是 Mock，不需要 API Key；测试里有"离线护栏"断言
- **安装**：**不需要安装**。`python -m` 直接跑；`pyproject.toml` 只是元信息声明（D-072）

> 本文件只记录**实际执行过**的命令与结果。未验证过的命令不写进来。

> ⚠️ **引用"B/C 100%"时必须注明取样口径**（2026-10-20 P6.1-0 起）：
> 那是**一次抽样**的结论（0.0 下 13 次、0.7 下 14 次），**不是分布保证**。
> 还有两条同等级的保留（C 只验"该拒答时拒答"、豁免触发不可回溯）与一条 TODO，
> 见 **§5.38**。把这句话抄在报告里比抄一个数字更有用。

---

## 1. 统一命令（P2.1 定稿，D-069）

在**本项目根目录**下执行（仓库位置自定，无强制要求）：

```bash
# ① 统一测试命令（694 项）—— 这是唯一推荐入口
python -m unittest discover -s tests -t .
# 想看明细就加 -v
python -m unittest discover -s tests -t . -v

# ② CLI
python -m persona              # 帮助（退出码 0）
python -m persona --help       # 帮助（退出码 0）
python -m persona --version    # 版本（退出码 0）
python -m persona info         # 环境信息（退出码 0）
python -m persona selftest     # 临时库 + Mock Provider 冒烟（退出码 0）
```

本次**实测**过的解释器（G-2 起累计 694 项）：

```bash
# Python 3.13.x（把 python3.13 换成你自己的解释器路径）
python3.13 -m unittest discover -s tests -t .

# Python 3.14.x
python3.14 -m unittest discover -s tests -t .
```

> 路径说明：这里原本写的是实测机器的两条绝对路径，属于本机环境、对读者无意义，
> 已改为占位写法（发布前清掉本机路径）。版本下限以 `pyproject.toml` 的
> `requires-python = ">=3.12"` 为准，上面两个只是**实测过**的版本。

本次实测（G-2）：Python **3.13.14 / 3.14.7 均 `Ran 694 tests ... OK`**（**694 通过 / 0 失败 / 0 错误**）。

> 备选写法 `python -m unittest discover`（不带参数）同样可用，同样 694 项通过；
> 但**统一用带 `-s tests -t .` 的写法**，避免将来根目录出现 `test*.py` 时误收。
>
> ⏱ 耗时说明：本机磁盘较慢，全量约 165~210 秒（P3 第一步的 347 项约 90~130 秒）；
> 单文件可用 `python -m unittest tests.test_relationship_module` 快速回归
> （约 18~65 秒）。

---

## 1.5 工程配置文件（P2.1 新增）

| 文件 | 作用 | 是否影响运行 |
| --- | --- | --- |
| `pyproject.toml` | 声明 `name` / `version` / **`requires-python = ">=3.12"`** / **`dependencies = []`** | ❌ 不影响。不安装也能跑（D-072） |
| `.gitignore` | 忽略虚拟环境、密钥、缓存、本地数据库、构建产物 | ❌ 不影响运行；仓库已 `git init`，**已生效** |
| `.env.example` | 本地配置**模板**（只有占位符，无真实密钥） | ❌ 不影响。复制成 `.env` 后才起作用 |
| `.env`（自己建的） | 本地非敏感配置 | ⚠️ 影响本机的默认值；**已被 .gitignore 忽略，不会入库** |

> **配置怎么改**：见 `docs/CONFIG.md`。要点：
> 非敏感配置可写 `.env`，**敏感配置（API Key）只能设为环境变量**；
> 优先级 **默认值 < `.env` < 环境变量 < 显式 overrides**。

`.gitignore` 覆盖检查（P2.1 逐项核对）：

| 类别 | 是否已覆盖 | 具体条目 |
| --- | --- | --- |
| 虚拟环境 | ✅ | `.venv/` `venv/` `env/` `ENV/` `.virtualenv/` `pyvenv.cfg` |
| 密钥文件 | ✅ | `.env` `.env.*` `*.key` `*.pem` `*.p12` `*.pfx` `secrets/` `credentials.json` `*.token` |
| 缓存 | ✅ | `__pycache__/` `*.py[cod]` `.pytest_cache/` `.mypy_cache/` `.ruff_cache/` |
| 本地数据库 | ✅ | `*.db` `*.sqlite` `*.sqlite3` 及 `-journal` / `-wal` / `-shm` 伴生文件 |
| 构建产物 | ✅ | `build/` `dist/` `*.egg-info/` |
| 日志与编辑器 | ✅ | `*.log` `.idea/` `.vscode/` `.DS_Store` |

> 🔒 **本项目不读取、不输出任何密钥内容**。当前代码里没有任何 `os.environ` / API Key 读取
> （`tests/test_dependency_rules.py` 里有一条断言专门扫描源码，确保不会出现 `api_key=` / `sk-` 字样）。

---

## 2. CLI 现在能干什么

| 命令 | 作用 | 退出码 |
| --- | --- | --- |
| `python -m persona` | 打印帮助 | 0 |
| `python -m persona --help` | 打印帮助 | 0 |
| `python -m persona --version` | 打印版本 | 0 |
| `python -m persona info` | 阶段、Python 版本、包结构、六态、命名空间写入闸门 | 0 |
| `python -m persona selftest` | 临时 SQLite 读写 + Mock Provider + 隔离闸门冒烟检查 | 0 / 1 |
| `python -m persona config show` | 打印生效的配置与来源（**永不显示密钥值**） | 0 / 1 |
| `python -m persona config show --no-dotenv` | 同上，但忽略 `.env` 文件 | 0 / 1 |
| `python -m persona config check` | 只做配置校验：通过 0，不通过 1 | 0 / 1 |
| `python -m persona business plan [--analyzer rule\|off]` | **装配快照**：组件、两层上限、账本历史占用、装配告警（离线、零调用） | 0 / 1 |
| `python -m persona business run --names A,B --work-title … --adaptation-version … --allow-real` | 跑一次五阶段业务链；**默认禁止真实外呼**，只有加 `--allow-real` 才构造真实 Provider（不加则拒绝且零调用） | 0 / 1 / **2** |
| `python -m persona business run … --material <路径>` | 同上，但研究阶段**不做检索**：只用给定的本地材料文件（必须过正文质量门禁；可重复给） | 0 / 1 / **2** |
| `python -m persona usage backfill --key K --amount X --reason R --operator O [--estimated]` | 人工补记一笔**已发生但没落库**的外部支出（**必须写 reason 与 operator**；幂等；零外部调用） | 0 / 1 |
| `python -m persona task create --project X --stages A,B [--key K] [--budget-requests N]` | 建一个 `PENDING` 任务（不自动开跑） | 0 / 1 |
| `python -m persona task show <task_id> --project X` | 看状态、阶段、进度、预算、检查点摘要 | 0 / 1 |
| `python -m persona task list --project X` | 列出该项目下的任务 | 0 / 1 |
| `python -m persona task start <task_id> --project X` | `PENDING → RUNNING` | 0 / 1 |
| `python -m persona task run <task_id> --project X [--steps N]` | 用 **Mock 执行器**推进（**不是真实业务**，不联网） | 0 / 1 |
| `python -m persona task pause <task_id> --reason USER\|BUDGET\|DEPENDENCY` | `RUNNING → PAUSED`（保留检查点，可续跑） | 0 / 1 |
| `python -m persona task resume <task_id>` | `PAUSED → RUNNING`（必须有检查点且产出完好） | 0 / 1 |
| `python -m persona task cancel <task_id>` | 取消（**不可**续跑，与暂停不同） | 0 / 1 |
| `python -m persona task sweep --project X` | 把上次中断的 `RUNNING` 任务安全回退为 `PAUSED(DEPENDENCY)` | 0 / 1 |
| `persona status / pause / resume / cancel <task_id>` | 上面四个 `task` 命令的快捷方式 | 0 / 1 |
| `python -m persona run --names A,B --work-title … [--dry] [--yes]` | **研究并生成档案**（真实检索 + 模型）：先打印清单 → 过一次确认 → 开跑。`--dry` 只打清单（零写入零外呼） | 0 / 1 |
| `python -m persona show <名字\|pe_…> [--version N] [--db …]` | 看六分区档案（稳定性格/习惯/说话方式/单次行为/背景/经历）+ 未并入说明 | 0 / **1**（无档案） |
| `python -m persona show-relationship A,B [--db …]` | 看两人关系档案（A 看 B 与 B 看 A **分列**） | 0 / 1 |
| `python -m persona evidence <名字\|pe_…> [--trait 标签] [--db …]` | 按"档案条目 → 证据 → 来源 URL"看每条特征凭什么；**无匹配的标签也是退出码 0**（会列出可用标签） | 0 / 1 |
| `python -m persona export <名字\|pe_…> [--format md] [--out 文件] [--db …]` | 导出可贴进文档的 Markdown（本期只支持 `md`） | 0 / 1 |
| `python -m persona chat --names A,B [--rounds N] [--message M] [--memory-write rule\|off] [--topic T] [--budget 0.05] [--db …] [--project prj_x] [--yes] [--no-dotenv]` | **模拟对话**（**第二个会花钱的命令**）：轮数 = 调用数；`--memory-write rule` 按确定性规则写**会话级模拟记忆**（默认 `off` = 一条不写）；**给了 `--topic` 才跨会话取记忆**。**第一次会先算账再问一次**（与 `run` **同一套**确认机制）；交互模式下 Ctrl-C 干净中止 | 0 / 1 / **130** |
| `persona new / sources / memory / eval` | **占位**：明确打印"尚未实现" | **2** |

> 占位命令**故意返回退出码 2 并说明"尚未实现"**，不假装能用。
> `selftest` 走的是**临时目录**里的数据库，不会碰任何项目数据。
> `task` 命令用的数据库在配置的 `data_dir`（默认 `var/persona.db`，已被 `.gitignore` 忽略）。

**实测过的任务闭环示例**（`PERSONA_DATA_DIR` 指向临时目录）：

```bash
# 预算只给 1 次调用 → 做到第 2 个单元时必然暂停
python -m persona task create --project prj_demo --stages STAGE_A,STAGE_B --budget-requests 1
python -m persona task start  <task_id> --project prj_demo      # → RUNNING
python -m persona task run    <task_id> --project prj_demo      # → PAUSED(BUDGET)，已用 1/1
python -m persona task resume <task_id> --project prj_demo      # → RUNNING
python -m persona task run    <task_id> --project prj_demo      # → 再次 PAUSED，**已用仍是 1/1**
```

最后一步是**关键的预算证明**：额度没提高时，恢复后**一个单元也没多做、一次调用也没多花**，
说明检查确实发生在消耗之前（D-089）。

**P5.1：从"跑一次"到"看得懂"（五条命令，2026-10-19）**

```bash
# 1) 先看清单（不花钱、不落库、不询问）
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --vocabulary l2 --dry

# 2) 真跑（第一次会问一次，答 y 之后同一个数据目录不再问；脚本里用 --yes）
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --vocabulary l2

# 3) 看结果（全部只读：不建库、不写审计、不写 usage）
python -m persona show 林黛玉
python -m persona show-relationship 林黛玉,贾宝玉
python -m persona evidence 林黛玉 --trait caustic
python -m persona export 林黛玉 --format md --out 林黛玉.md
```

四条只读命令的 `--db` 默认取配置里的数据库；换了 `--out` 就换库，跨目录看库要显式给 `--db`。

> **只读命令绝不建库**：库不存在时报错并提示"只读命令**不会**新建数据库"，
> 而不是顺手造一个空库让你以为跑过了（§12 纪律 4）。
>
> **确认文件**：TTY 里答 `y` 会在数据目录下写 `.persona_consent.json`（只有
> `version` / `confirmed_at` / `estimated_usd` 三个键，不存密钥、不存命令、不存人名）。
> 删掉它就重新启用询问；`--yes` 与 `--dry` 都**不写**这个文件。

---

## 3. 目录说明（每个包负责什么）

| 包 | 职责 | 现在有没有业务逻辑 |
| --- | --- | --- |
| `persona/core` | 枚举、ID、时间、错误码、`Result` 信封、`CallContext`、通用 DTO | ❌ 只有类型与规则 |
| `persona/storage` | SQLite 建表、CRUD、软删除、乐观锁、引用检查、检查点、用量、审计 | ❌ 只做存储，不做业务判断 |
| `persona/providers` | 外部服务接口 + `FakeProvider` / `RecordingProvider` / `FailingProvider` / `OfflineGuardProvider` | ❌ 全是 Mock |
| `persona/config` | **唯一配置入口**：18 项非敏感 + 2 项敏感（只走环境变量，永不回显） | ❌ 只有规则 |
| `persona/orchestrator` | 六态状态机 + `TaskService` + `BudgetGate` + **`TaskRunner`（Mock 执行器）** | ⚠️ **只有"任务闭环"，没有业务**：推进的是抽象 `WorkUnit`（D-093） |
| `persona/modules` | 7 个业务模块：5 个已有实现（`EvidenceService` / `PersonaService` / `RelationshipService` / `DialogueService` / `MemoryService`），`Research` / `Evaluation` 仍只有 Protocol 端口声明 | ⚠️ **本地业务已可用**（JSON 载荷，见 §4 第 3 条）；研究/蒸馏/评估仍只有"插头图纸"，方法体是 `...` |
| `persona/cli` | argparse 入口 | ❌ 不含业务逻辑（只调 `TaskService` / `Storage` 只读） |

测试在 `tests/`，共 21 个文件：

| 文件 | 测什么 |
| --- | --- |
| `test_imports.py` | 29 个模块都能导入 |
| `test_core_contracts.py` | `Result` / 错误码 / ID / **命名空间闸门** / 枚举取值 / 上下文 |
| `test_task_state.py` | 六态转换表、暂停≠取消、检查点幂等与幂等键 |
| `test_config.py` | 配置加载、校验、`InvalidConfigError` |
| `test_task_service.py`（P2.3） | 创建/查询/持久化、**非法转换不改状态**、暂停≠取消≠失败、**重启后读回**（含**真实子进程**）、`sweep_interrupted`、TS-V4 幂等去重 |
| `test_task_budget.py`（P2.3） | 四段式闸门、未知 token 不估算、80% 只告警、**预算不足暂停而非失败**、提额后续跑**不重复执行**、跳过已完成单元、可重试/不可重试 |
| `test_storage_sqlite.py` | 临时库建表、读写往返、分页、乐观锁、软删除、引用检查、检查点、用量、**隔离闸门拦截** |
| `test_provider_mock.py` | Fake 确定性、Recording 回放、Failing 注入错误码、**离线护栏** |
| `test_dependency_rules.py` | 端口签名（F-2/F-3/D-057）、导入方向（F-7/F-8/F-12）、无厂商 SDK、无真实人物姓名 |
| `test_task_faults.py`（P2.4） | 15 项故障注入：检查点失败/库失败/处理器异常/重复提交/重复恢复 |
| `test_evidence_module.py`（P2） | 89 项：来源·事件·证据·去重·冲突·稳定性·模拟隔离双层闸门 |
| `test_persona_module.py`（P3 第一步） | 71 项：版本化更新·证据追溯·不确定性·虚构边界·模拟隔离 |
| `test_relationship_module.py`（P3 第二步） | 100 项：双向视角·共同经历·时间变化·群体互动·证据直达·版本边界 |
| `test_dialogue_module.py`（P3 第四步） | 28 项：会话/轮次·动态发言选择·上下文引用·失败不伪造·持久化·模拟隔离 |
| `test_memory_module.py`（P3 第五步） | 34 项：写入·关键词检索·去重·冲突·版本化更新·过期·归档·删除与派生产物失效·对象边界·模拟隔离 |
| `test_dialogue_memory_integration.py`（P3 第六步） | 28 项：跨会话记忆检索（含重启读回）·无关/真实命名空间记忆不入上下文·写入触发规则·写入失败整体回滚·检索/写入/Provider/会话失败不伪造·删除/更新/过期/归档/冲突生命周期·幂等与重试 |
| `test_integration_acceptance.py`（P3 第七步） | 26 项：完整调用链（任务生命周期/证据档案读写/对话生成/记忆按规则写入）·模块边界（CLI 不经 modules、依赖检查）·四表零变化（AC-13）·模拟不进证据（AC-14）·冲突不裁决（AC-15）·预算暂停+提额续跑不重复（AC-11/12）·Provider/存储失败与幂等·临时库隔离·文档命令一致性 |
| `test_research_module.py`（H-11A） | 48 项：检索计划（确定性/校验）·检索（成功/0 结果为业务结果/Provider 异常/预算耗尽/元数据透传）·过滤排序（无效材料带原因/批内去重/确定性顺序/对象边界）·登记（只经 Evidence/失败不伪造/幂等/元数据保留）·端到端 run（计数/Evidence 可消费/部分失败继续/全失败上报/多目标）·隔离（不产 EvidenceItem、Source 恒 PUBLIC_SOURCE、存储闸门）·真离线（OfflineGuard+network_blocked）·模块边界（仅 register_source/无 storage/静态检查）·复用既有 Mock 家族 |
| `test_business_orchestrator.py`（H-10A） | 37 项：五步调用顺序与前一步输出传给下一步·档案条目→证据→来源三段追溯（AC-3）·`SINGLE` 不进 traits（AC-7）·关系只用 RELATION 证据（RL-V6）·Research 无结果（无种子→PARTIAL／有种子→报错）·Evidence 校验失败（dangling / SIMULATED 命名空间）·Persona/Relationship/Dialogue 失败即上报且后续 `NOT_REACHED`·部分成功不算全流程成功·重跑不重复登记证据/来源/档案版本·幂等键复用任务·预算暂停+提额 resume 不重复（AC-11/12）·模拟隔离（AC-13 四表零变化、session 恒 SIMULATED、正典边界保留）·AC-1/AC-2 原文核对·职责分离（不复制六态机/账本、不挑 Provider、模块不反向依赖编排层）·全程离线（network_blocked） |
| `test_claim_extraction.py`（G-2） | 28 项：候选保留来源/摘录/定位/对象/时间/分类；三种 claim 区分；无效摘录、空材料、空 claim、缺主体、未知等级、越界置信度 的拒收；推断带摘录即拒收；相似说法不合并；同文两源各自保留；Provider 报错原样传播 / 非法 JSON 报错 / 缺 material_key 拒收 / 无材料不调模型；抽取器拿不到 Evidence（AST 断言）；静态检查通过 |
| `test_business_orchestrator.py`（G-2 追加） | 18 项：只给人名即可跑完全流程（AC-1）；Research→抽取→登记 顺序与调用次数；前一步材料传给抽取器；证据可逐条溯源；推断走 record_inference 且无根即丢弃；无材料/无可抽取主张/无效摘录/抽取器异常/Evidence 校验失败 的失败与部分成功判定；重跑不重复登记；冲突两条都保留；Mock 标签与模拟隔离 |
| `test_cli.py` | 帮助、版本、info、selftest、占位命令诚实返回"尚未实现" |

---

## 4. 本阶段**明确没有做**的事

按用户指令"不扩大范围"，以下**尚未实现**（不是遗漏，是刻意留到后面阶段）：

1. **~~业务逻辑全未实现~~** → ⚠️ **本地业务部分已实现**：研究、证据提取/分级/去重/冲突、人物蒸馏、关系蒸馏、对话生成、长期记忆检索。
   **已实现的是"本地管理"部分**（P2 证据管线第一步 + P3 第一步/第二步/第四步/第五步）：`EvidenceService` /
   `PersonaService` / `RelationshipService` / `DialogueService` / `MemoryService`。
   **仍没有的是**：① **研究**（真实检索，需 Q-01/Q-02/Q-03 拍板）；② 两个 `distill` 的 **AI 蒸馏主体**（不调模型）；
   ③ 对话**每轮自动写入长期记忆**（无明确规则，本阶段刻意不做）；④ 向量/语义检索（首版只做关键词，D-074）。
2. **~~任务引擎未实现~~** → ✅ **P2.3 已部分实现**：`TaskService` + `TaskRunner.run_step / run_to_completion`
   + `BudgetGate` 四段式闸门已经可用（见 §2 的命令表）。
   仍然**没有**的是：阶段编排的**业务语义**、墙钟时间预算、真实业务单元。
3. **SQLite 不是最终表结构**：现在只有 4 张骨架表（`records` / `usage_records` / `audit_records` / `checkpoints`）。
   13 个业务实体**暂以 JSON 载荷**存在 `records.payload` 里，通用字段（`id`/`namespace`/`created_by`/`deleted_at`/`rev`）独立成列。
   **已拍板（D-062）**：这是被接受的临时方案，**必须在 P2 结束前**按 `DATA_MODEL.md` 迁移到 13 张正式表
   （`schema_version` 递增 + 显式迁移脚本，**禁止自动删表**）。
4. **FTS5 关键词索引未建**：记忆检索属于 P4（Q-30），本阶段不需要。
5. **`fixtures/` 合成测试素材未创建**：`ROADMAP.md` §3B 计划内。**已拍板（D-066）**：**P2 开工前必须补齐**。
6. **`run_static_checks` 独立脚本未创建**：目前以 `tests/test_dependency_rules.py` 承担最关键的几条静态检查。
   **已拍板（D-066）**：与 `fixtures/` 一起在 **P2 开工前补齐**。
7. **（原第 7 条已作废）**：任务执行基础闭环已在 **P2.3 实现**（D-067 记录的是 P1.2-B 当时的状态）。
8. **P2.3 仍明确未做**：① 真实业务单元（研究/证据/蒸馏/对话）；② `max_duration_sec`（墙钟）预算；
   ③ `create_project` / `get_progress`；④ 自动化的崩溃**补做**（只回退状态，不自动补活，D-094）。
9. **Orchestrator / Evaluation 模块只有协议或部分实现**：`Orchestrator` 未实现阶段编排，`Evaluation` 只有 `Protocol`。

---

## 5. P1.2-B 待确认项 —— 已全部拍板（结论见 `DECISIONS.md` §2.9）

| # | 事项 | 拍板结论 | 决定编号 |
| --- | --- | --- | --- |
| R-1 | SQLite 用"通用 `records` 表 + JSON 载荷" | ✅ **同意**先用通用表，**P2 结束前**迁移到 13 张正式表（`schema_version` 递增 + 迁移脚本，禁止自动删表） | **D-062** |
| R-2 | 新增两个错误码 `VALIDATION.unknown_kind`、`VALIDATION.unknown_filter` | ✅ **同意**，已回填 `ERROR_HANDLING.md` §2.2 | **D-063** |
| R-3 | 命名空间闸门放在 `Storage.put` | ✅ **接受**。已同步 `INTERFACES.md` §1.8.1：这是**结构性拦截**（静态允许表查表，等价于外键/CHECK 约束，fail-closed），**不属于业务判断**；模块层闸门在 P2 补，**两层都要保留** | **D-064** |
| R-4 | 包名与命令名 | ✅ **保留 `persona`**（`python -m persona`） | **D-065** |
| R-5 | `fixtures/` 与 `run_static_checks` | ✅ **同意 P2 开工前补齐** | **D-066** |
| R-6 | 任务引擎未实现 | ✅ **确认符合本轮指令**，不是缺陷；P2 实现 | **D-067** |

### 5.1 遗留问题状态（划掉 = 已解决；未划掉的**不要当作已决定**）

| # | 问题 | 当前状态 | 影响 |
| --- | --- | --- | --- |
| ~~U-1~~ | 项目尚未 `git init` | ✅ **已解决**：用户授权后已 `git init` 并提交首个 commit（P2.1+P2.2）；提交内容经检查**不含密钥、不含本地数据库、不含缓存** | — |
| ~~U-2~~ | Q-27：是否引入 Pydantic / ORM | ✅ **已拍板：否** → **D-073**：继续用标准库 `dataclasses` + `sqlite3` + 薄仓储层 | — |
| ~~U-3~~ | Q-30 / Q-15 / Q-22 | ✅ **已拍板** → **D-074**（FTS5 + trigram，旧版退化为自建字串倒排表）、**D-075**（改编默认分离）、**D-076**（档案默认不跨版本共用） | — |
| ~~C-1~~ | 新增错误码 `VALIDATION.config_invalid` | ✅ **已批准**，永久保留在 `ERROR_HANDLING.md` §2.2 | **D-083** |
| ~~C-2~~ | `PERSONA_SEARCH_API_KEY` 何时必填 | ✅ **保持可选**，等 P2 确定搜索 Provider 后再定 | **D-084** |
| ~~C-3~~ | 是否支持项目级配置文件（如 `persona.toml`） | ✅ **暂不支持**，保持极简 | **D-085** |
| ~~C-4~~ | 日志落地（`log_level` 目前只存不用） | ✅ **暂只存不用**，禁止先写空壳 | **D-086** |

### 5.2 P2.3 遗留问题（**T-1 ~ T-7 已全部拍板，2026-10-13**）

| # | 问题 | 拍板结论 | 决定编号 |
| --- | --- | --- | --- |
| ~~T-1~~ | 新增 4 个错误码：`VALIDATION.checkpoint_missing` / `artifact_missing` / `unknown_task_type` / `stage_sequence_empty` | ✅ **保留**，永久登记在 `ERROR_HANDLING.md` §2.2（"待确认"标记已去除） | **D-095** |
| ~~T-2~~ | 恢复粒度（Q-14 推荐"阶段级"） | ✅ **接受更细的"阶段 + 单元清单"**，为第一版既定口径 | **D-096** |
| ~~T-3~~ | 产出完整性检查（`artifact_missing`） | ✅ **接受 Mock 限制**，端到端用例等 P2 真实数据接入后补 | **D-097** |
| ~~T-4~~ | 墙钟预算 `max_duration_sec` | ✅ **暂缓**，后续需要时再接 | **D-098** |
| ~~T-5~~ | `attempt_count` 计数口径 | ✅ **接受任务级累计**（不按单元重置） | **D-099** |
| ~~T-6~~ | 没有 `create_project` | ✅ **暂不需要**，P2 主管线时再补 | **D-100** |
| ~~T-7~~ | 并发 | ✅ **接受无并发保护**（本地单用户工具）；最小修复路径已记录在 D-101 | **D-101** |

### 5.3 P2.4 故障注入结果与可靠性边界（诚实记录，2026-10-13）

**做法**：只补 P2.3 未覆盖的故障场景（P2.3 已覆盖的状态机 / 预算 / 幂等 / 断点恢复
不重复测试，清单见 `tests/test_task_faults.py` 模块 docstring）。故障注入用手工
`FlakyConnection` 代理对真实 SQLite 连接按 SQL 特征抛 `sqlite3.OperationalError`，
**产品代码零改动、零新增依赖**。测试数 172 → **187**，两个 Python 版本全过。

**实测结果**（15 项，`tests/test_task_faults.py`）：

| 场景 | 结果 |
| --- | --- |
| 检查点保存失败（直接保存） | `Err(STORAGE_WRITE_FAILED)`，不抛异常；库里确实无检查点 ✅ |
| 检查点保存失败（`run_step` 第⑤步） | 返回 `Err`；任务仍 `RUNNING`；**该单元重启后被重复执行、用量重复记账**（见 B-1）⚠️ |
| 检查点保存失败（预算暂停路径） | 任务仍被暂停，但检查点为空；`resume` 明确报 `checkpoint_missing`（见 B-2）⚠️ |
| 数据库写失败（状态转换落盘） | `Err`；DB 状态保持原值 ✅ |
| 数据库写失败（建任务） | `Err`；无半个任务残留 ✅ |
| 数据库读失败 | `Err(STORAGE_READ_FAILED)` 传播，不伪装成 `Ok(None)` ✅ |
| 数据库读失败（`resume` 引用检查） | `Err` 传播；任务停在 `PAUSED` ✅ |
| 处理器 `execute` 抛异常 | 异常穿透 `run_step`；任务停留 `RUNNING`、无痕迹；`sweep` 可兜底（见 B-3）⚠️ |
| 处理器 `plan` 抛异常 | 异常穿透；无任何单元被执行 ✅ |
| 重复提交（写失败后重试） | 无脏数据；同 key 最终只建成 1 个任务 ✅ |
| 重复提交（原任务已完成） | 返回已完成的原任务，不新建 ✅ |
| 重复恢复（`resume` 两次） | 第二次 `Err(STATE_CONFLICT_NOT_PENDING)`，状态不变 ✅ |
| 重复恢复（`sweep` 两次） | 第二次空列表，痕迹不被二次覆盖 ✅ |
| 重复恢复（三轮耗尽→提额→恢复） | 每单元恰好执行 1 次，用量恰好 5 ✅ |
| 跨进程重启（检查点丢失后重做） | 真实子进程验证：单元被重做、用量累计到 2（见 B-1）⚠️ |

**重启验证方式**：① 同进程新 `SqliteStorage` 实例指向同一数据库文件（全部故障场景）；
② **真实子进程**（全新解释器）验证 B-1 边界——沿用 P2.3 的子进程手法。

**可靠性边界（不虚称保证）**：

| # | 边界 | 现状 | 最小修复建议（未实施，待拍板） |
| --- | --- | --- | --- |
| B-1 | 单元执行与检查点保存**非原子** | 执行成功、用量已记账后检查点保存失败 ⇒ 重启后重复执行 + 重复记账。第二道保险（幂等键 `f"{task_id}:{unit}"`）只是传给 handler 的钥匙，执行器不保证业务侧去重 | 把"执行 → 记账 → 落检查点"放入同一 `storage.transaction()`（存储层已支持）；或检查点先写"预执行"标记、确认后才写"已完成" |
| B-2 | 预算暂停路径忽略 `save_checkpoint` 返回值 | 暂停成功但检查点缺失 ⇒ 进度丢失；`resume`  fail-loud 拒绝（不会假装恢复） | 暂停前判断 `save_checkpoint` 结果：失败则不暂停（保持 `RUNNING` 让上层重试）或在 `paused_reason` 附注"检查点写入失败" |
| B-3 | 处理器抛异常时无痕迹 | 异常穿透 `run_step`，任务停留 `RUNNING`、无 `last_error`、`attempt_count` 不涨；只能靠 `sweep` 兜底 | `run_step` 用 `try/except` 包裹 `handler.execute`，转为 `Err` + `mark_attempt`（重试语义与 ERROR_HANDLING §4 对齐） |
| B-4 | `attempt_count` 任务级累计 | 多单元任务重试次数可能提前耗尽（T-5 已接受） | 需要时改为按单元计数 |
| B-5 | 无并发保护 | 多进程同跑一个任务可能丢更新（T-7 已接受） | `TaskService` 更新时传入读到的 `rev` 作为 `expected_rev`（存储层已支持，D-101） |

### 5.4 P2 证据管线第一步：来源与证据管理（诚实记录，2026-10-13）

> 用户口头称之为 **P3.1**；仓库 `ROADMAP.md` 的编号里这一步属于 **P2（研究 + 证据管线）**。
> 差异已报告、**未擅自改路线**，待用户拍板（见 `DECISIONS.md` §3 Q-37）。
> 对应决定：`DECISIONS.md` §2.16（D-102 ~ D-110）。

**实际实现**（`persona/modules/evidence.py` + `tests/test_evidence_module.py`，89 项新测试）：

- 复用现有 `Source` / `SourceEvent` / `EvidenceItem` / `ConflictRecord` 模型与 SQLite 存储，**只补必要实现**，未改表、未加依赖
- `EvidenceService`：来源登记/去重/查询、来源事件登记、`extract_events`（确定性降级）、证据登记/查询、`record_inference`、`judge_stability`（只读）、`detect_duplicates`、`record_conflict` / `detect_conflicts`
- 隔离红线：`EvidenceItem` 禁 `SIMULATED`（模块层先拒 + 存储层结构闸门，双层，D-064）；`INFERENCE` 强制 `SYSTEM_DERIVED` + `derived_from` 非空 + `source_id=None`
- 冲突不裁决：`resolution_policy` 恒为 `KEEP_ALL_UNASSESSED`；`ReviewStatus` 枚举故意没有 `RESOLVED`
- 去重严格条件（D-104）：归一化 claim（仅折空白）+ 相同 subject 集合 + 相同 dimension + 相同 applicable_variant_ids + 相同 canon_level；**语义相似不并组、正典状态/版本不同不并组**
- 修复的实现缺陷（测试逼出，均已修复并有测试钉死）：① `Source` 的业务 `kind` 被 `records` 结构列 `kind` 遮蔽，导致 SE-V3 / `extract_events` / 来源过滤全部失效；② `EvidenceQuery` 的 `entity_ids` 过滤键未映射到载荷字段 `subject_entity_ids`；③ `detect_duplicates` 分组键两处变量名笔误（`r` → `row`）
- 文档对齐（D-110）：`EvidencePort` Protocol 签名对齐 `INTERFACES.md §4.2`（P1.2-B 骨架签名过时，Protocol 无运行时强制，无行为变化）；补 `get_evidence_tier`（Protocol 声明过但服务未实现）

**明确未实现**（不假装）：

- AI 辅助事件切分（需 `Provider.chat`，本阶段只走"覆盖全文的单块事件"降级 + `EVENT_SINGLE_BLOCK` 警告）
- 语义级冲突检测（"两句话意思矛盾"需模型；本阶段只做确定性结构检测：同说法跨正典状态 / 跨互斥改编版本）
- `UserInputRecord`（`USER_PROVIDED` 证据入口，见 E-2）
- 完整研究流程 / 人格提取 / 关系建模 / 对话生成（按指令不做）

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| E-1 | 6 个新错误码待人工确认：`VALIDATION.tier_namespace_mismatch` / `source_missing` / `entity_missing` / `dangling_reference` / `empty_evidence` / `granularity_unsupported`（INTERFACES §4.2 已点名，§2.2 表未列） | 已进 `ERROR_SPEC` 并在实现中使用；`ERROR_HANDLING.md` §2.2 标"待人工确认"（T-1 同口径） | 用户验收后回填 §2.2 并去掉待确认标记（→ 拟 D-111） |
| E-2 | `USER_PROVIDED` 证据无入口 | `add_evidence` 明确拒绝（`VALIDATION.namespace_forbidden`）并引导至尚未实现的 `UserInputRecord`；不静默放行 | 实现 `UserInputRecord` 时开放该命名空间（登记即审计） |
| E-3 | `judge_stability` 的 `subject_entity_id` / `claim_text` 仅进 `reason` 留痕 | 未做"证据是否真属于该对象/该说法"的交叉校验（文档未要求；加了可能误拒合法调用） | 如后续需要严格模式，加一条可选校验并以新错误码报出 |
| E-4 | 去重组 ID 的"最早成员"语义 | `created_at` 只有秒级精度；同秒创建的成员之间退化为按 `id` 排序取最小（组 ID 仍确定、幂等，成员集合不受影响） | 需要严格时序时给 `records` 加单调序号列（P2 后迁移时一并考虑） |
| E-5 | `DATA_MODEL.md` §4.3 的 `Source.subject_entity_ids` 文档为可空，实现按"给了就必须存在"校验 | 空列表合法；非空逐项校验存在性（`pe_` 前缀） | 无需改动；如文档要改口径走 DECISIONS §4 流程 |

---

### 5.5 P3 第一步：人格档案基础模块（诚实记录，2026-10-14）

> 用户口头称之为 **P3.2**；仓库 `ROADMAP.md` 的 P3 = 人物档案 + 关系档案
> （未细分 .1/.2），本步是 P3 中 `Persona` 部分的基础实现。差异已报告、
> **未擅自改路线**，与上一轮一并待用户拍板（见 `DECISIONS.md` §3 Q-37）。
> 对应决定：`DECISIONS.md` §2.17（D-111 ~ D-120）。

**实际实现**（`persona/modules/persona.py` + `tests/test_persona_module.py`，71 项新测试）：

- 复用现有 `PersonEntity` / `EvidenceItem` / `ConflictRecord` / `PersonaProfile` 模型与 SQLite 存储，**未改表、未加依赖**；补 1 个枚举 `ProfileStatus`（DATA_MODEL §4.7 早已定义、代码缺失）；**未新增任何错误码**（全部复用已有码）
- `PersonaService`：`create_profile`（v1）/ `revise`（新版本 + 旧版 `superseded_by` 归档）/ `get_persona`（最新或指定版本）/ `query_profiles`（含历史版本）/ `diff_versions`（字段级）/ `apply_stability_gate`（PF-V2 闸门）
- 证据关联：每个非空条目/板块必须带 `evidence_refs` 且逐项校验存在性、种类（只收 `EvidenceItem`）、非 SIMULATED、版本适用性（D-117：证据声明了 `applicable_variant_ids` 就必须包含档案对象）
- 不确定性表达：条目 `uncertainty_note`、档案 `open_questions`（D-020：没证据不许编）、`status=INSUFFICIENT_EVIDENCE/AMBIGUOUS/CONFLICTED`（D-008：资料不足不是错误）、`controversial_topic_ids` 关联冲突不裁决
- 版本变化：`revise` 强制 `change_reason`；内容逐字段一致时空转拒绝；旧版本内容永不修改（`get_persona(version=1)` 仍读到旧结论）；Q-19 的 5 版裁剪未实现，当前全版本保留
- 虚构边界：真人禁变体字段与 `canon_boundary`；虚构必须声明 `applicable_variant_ids`（含自身、不跨 `variant_group`）；同一角色两个改编版本是两份独立档案，系统不自动合并（PE-V3）
- 模拟隔离：档案/条目 `namespace` 恒 `SYSTEM_DERIVED`（PF-V6），`SIMULATED` 模块层 + 存储层双层拒绝；`mem_` / `ctn_` 引用一律拒绝（模拟记忆/对话永远不能成为档案依据）
- 覆盖度 `coverage` 由服务从真实内容计算（6 板块 × {items, evidence_refs}），不接受调用方填报
- 文档对齐（D-119）：`PersonaPort` Protocol 按已实现面对齐（P1.2-B 骨架签名过时；`distill` 的 AI 蒸馏主体未实现、不虚占签名，同 D-110 先例）

**明确未实现**（不假装）：

- `distill` 的 AI 蒸馏主体（INTERFACES §6.2 要调 `Provider.chat`）：本模块只提供落盘前必须经过的存储/校验/版本层，写入入口是 `create_profile` / `revise`（内容由调用方给出）
- 自动人物研究、关系建模（`Relationship`）、对话生成、记忆——按指令不做
- 语义级质量判断（"这条性格总结准不准"需模型）；已实现的是确定性结构校验（PF-V1~V6 + 版本边界 + 引用完整性）
- `UserInputRecord`（`USER_PROVIDED` 证据入口，见 §5.4 E-2）——档案条目因此也只能引用公开来源证据与系统推断

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| F-1 | `distill` 未实现 | 模块无任何 Provider 依赖；协议未虚占签名（D-119） | 下一阶段实现 AI 蒸馏时补入协议并接 `Provider.chat`（预算/重试按 INTERFACES §6.2） |
| F-2 | Q-19 版本裁剪未实现 | 全版本保留（不删用户数据）；版本数无限增长 | 拍板后实现"保留最近 5 版 + 更早归档为摘要"；归档摘要建议走 `DerivedArtifact` |
| F-3 | `coverage` 形状为模块自定（D-113） | DATA_MODEL 未定义 `CoverageStats` 内嵌结构；当前 `{板块: {items, evidence_refs}}` | 如需更细指标（如按 tier 分布），走 DECISIONS §4 流程扩形 |
| F-4 | `diff_versions` 是结构 diff | 按 `item_id` 对齐逐字段比较；不做"这段改写是否更准"的语义评估（需模型） | 语义级 diff 留给 Evaluation 阶段 |
| F-5 | 档案条目无法直接区分"直接证据"与"推断" | "系统推断"标记在证据层（`tier=INFERENCE` + `derived_from`），条目经 `evidence_refs` 追溯即可分辨；档案层无冗余字段 | 无需改动；如下游需要一键过滤，可在 View 层加派生属性（只读） |

---

### 5.6 P3 第二步：关系档案基础模块（诚实记录，2026-10-14）

> 用户口头称之为 **P3.3**；仓库 `ROADMAP.md` 的 P3 = 人物档案 + 关系档案
> （未细分 .1/.2/.3），本步是 P3 中 `Relationship` 部分的基础实现。差异已报告、
> **未擅自改路线**，与前面两轮一并待用户拍板（见 `DECISIONS.md` §3 Q-37）。
> 对应决定：`DECISIONS.md` §2.18（D-121 ~ D-128）。

**实际实现**（`persona/modules/relationship.py` + `tests/test_relationship_module.py`，100 项新测试）：

- 复用现有 `PersonEntity` / `EvidenceItem` / `ConflictRecord` / `RelationshipProfile` 模型与 SQLite 存储，**未改表、未加依赖**；补 1 个枚举 `Cardinality`（DATA_MODEL §4.8 早已定义、代码缺失）；新增 1 个错误码 `VALIDATION.group_dynamics_missing`（INTERFACES §7.2 已点名、原表未列，**待人工确认**）
- 复用 `persona/modules/persona.py` 的公共数据契约（`ProfileItem` / `TimelineItem` 与序列化辅助，DATA_MODEL §4.15 跨实体复合类型；INTERFACES §7.1 允许 Relationship 只读依赖 Persona），不重复造轮子
- `RelationshipService`：`create_relationship`（v1）/ `revise`（新版本 + 旧版 `superseded_by` 归档，参与者集合按**集合**匹配、顺序无关）/ `get_relationship`（`rlp_` ID 或参与者列表）/ `query_relationships`（含历史版本）/ `diff_versions`（字段级，含视角块按 owner+target+item_id 对齐）/ `apply_pattern_gate` / `validate_evidence_refs`（RL-V6）
- **双向视角**：`perspectives` 以 `entity_id` 为键独立存放（RL-V1）；`view_of_targets` 按目标人物分键；结构上无跨视角"综合定论"字段（RL-V5）；A→B 与 B→A 可以不同，系统不合并（D-123）
- **共同经历与时间变化**：`shared_experiences` / `temporal_changes` 带 `occurred_at`（FuzzyDate 往返）与 `work_internal_time`；`SharedExperienceItem.participant_entity_ids` 支持"一次经历只涉及部分成员"（D-121，空 = 全体参与者，非空必须是参与者子集）
- **群体关系**：≥3 人存储为一份完整档案（`cardinality=GROUP` + 全体参与者 + 逐人视角 + `group_dynamics`），**不自动拆出两人子档**（测试钉死）；`group_dynamics` 三板块全空时必须写 `note`（D-020 同口径）
- **证据关联（RL-V6）**：每条关系条目必须至少引用一条 `EvidenceItem`；`PersonaProfile` / `MemoryItem` / `ConversationTurn` 引用一律拒绝——关系不能由两份人格档案"拼"出来；证据的 `applicable_variant_ids` 非空时必须与参与者集合有交集（D-126）
- **不确定性**：视角块 `uncertainty_note`、`asymmetry_notes`（双方认知不一致的显式说明）、档案 `open_questions`、`status=INSUFFICIENT_EVIDENCE/AMBIGUOUS/CONFLICTED`；全空档案必须写 `open_questions`（D-117 同口径）
- **冲突保留**：`conflict_ids` 关联不裁决（`KEEP_ALL_UNASSESSED`），CF-V5 双向回写 `downstream_refs`（幂等），不阻断创建/修订
- **版本变化**：`revise` 强制 `change_reason`；内容逐字段一致时空转拒绝；旧版永不修改；`diff_versions` 字段级
- **虚构边界**：同一角色两个改编版本不得同时作为参与者（D-127 反合并）；`applicable_variant_ids` 必须是参与者子集、覆盖全部虚构参与者、不含真人；不同作品角色（不同 `variant_group`）可正常建档
- **无亲密度数值（RL-V2/D-048）**：入口属性名扫描 + 落盘前 payload 键名递归扫描双重拦截；数据契约本身无此类字段（测试钉死）
- **模式闸门（D-125）**：`interaction_patterns` 拒绝 `stability=SINGLE`——单次互动不得固化为长期模式（归宿是 `shared_experiences`）
- **模拟隔离**：档案/条目 `namespace` 恒 `SYSTEM_DERIVED`，`SIMULATED` 模块层 + 存储层双层拒绝；`mem_` / `ctn_` / `pnf_` 引用一律拒绝；全表扫描无 SIMULATED 档案
- 覆盖度 `coverage` 由服务从真实内容计算（逐人视角 + 各板块 × {items, evidence_refs}）
- 文档对齐（D-128）：`RelationshipPort` Protocol 按已实现面对齐（P1.2-B 骨架签名过时；`distill` 的 AI 蒸馏主体未实现、不虚占签名，同 D-110/D-119 先例）

**明确未实现**（不假装）：

- `distill` 的 AI 蒸馏主体（INTERFACES §7.2 要调 `Provider.chat`）：本模块只提供落盘前必须经过的存储/校验/版本层，写入入口是 `create_relationship` / `revise`（内容由调用方给出）
- 自动关系研究、对话生成、长期记忆自动写入——按指令不做
- 语义级冲突检测（"这两条说法意思矛盾"需模型）；已实现的是确定性结构校验（RL-V1~V6 + 版本边界 + 引用完整性）
- 群体内的**子结构关系**（如"A-B 是盟友、同时 A-C 是对手"需要两份档案或额外结构）：当前模型用逐条目参与者部分表达，不重构核心模型（缺口 G-2）

**测试逼出并修复的实现缺陷**（均有测试钉死）：① `_relationship_payload` 漏写 `version` 键（版本全变 0，排序/diff/读档全坏）；② `RelationshipView` 漏 `version` 字段；③ `_all_items` 静态/实例方法不匹配；④ 设计缺陷：初版"虚构参与者必须共享同一 `variant_group`"会误杀**不同作品角色**的关系（如林小满×陈雾）——改为"同角色双版本反合并 + 证据版本交集校验"（D-126/D-127）；⑤ `WARNING_CONFLICT_LINKED` 定义了未使用——实际接入。

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| G-1 | `distill` 未实现 | 模块无任何 Provider 依赖；协议未虚占签名（D-128） | **已接受**（D-130）：下一阶段接入真实 Provider 时再补 |
| G-2 | 群体子结构关系无法表达 | "A-B 盟友 + A-C 对手"需两份两人档案或额外结构；当前用逐条目参与者部分表达 | **已接受**（D-131）：当前表达边界可接受；最小建议（拆两人档或引入 `sub_group` 结构）记后续待办，不擅自重构 |
| G-3 | `GroupDynamics` 形状为模块自定（D-121） | DATA_MODEL §4.8 原只说"联盟、站队、冷落、传话"四个概念，未定字段；当前 `{alliances, exclusion, relay, note}` | **已补文档**（D-132）：结构正典化入 DATA_MODEL §4.8 |
| G-4 | Q-19 版本裁剪未实现（同 F-2） | 全版本保留（不删用户数据） | **已确认**（D-133）：与 Persona 一致，全版本保留、暂不裁剪 |
| G-5 | 视角块无"确定性程度"字段 | 视角是加工产物，不确定已由 `uncertainty_note` + `stability` 表达；未加数值型确定性评分（避免变成 RL-V2 式亲密度） | **已确认**（D-134）：刻意不为，符合红线，无需改动 |

**新错误码待确认清单**（E-6）：

| # | 错误码 | 文档依据 | 状态 |
| --- | --- | --- | --- |
| E-6 | `VALIDATION.group_dynamics_missing` | INTERFACES §7.2 失败原因列已点名"`VALIDATION.group_dynamics_missing`（≥3 却为空）"，ERROR_HANDLING §2.2 原表未列 | **已批准**（D-129，P3.3 验收 2026-10-14） |

---

## 5.7 P3.4 对话生成基础模块（用户称 P3.4，编号差异见 DECISIONS Q-37）

> 仓库 `ROADMAP.md` 的 **P4 第一步（Dialogue 部分）**；沿用 Q-37 口径。
> 新增 `persona/modules/dialogue.py`（`DialogueService`：会话/轮次 CRUD、动态发言人
> 选择、只读上下文组装、经 `Provider` 生成、落盘、严格模拟隔离）与
> `tests/test_dialogue_module.py`（28 项，全过）。

**实现清单**：

- `DialogueService`：`start_session`（≥2 参与者、实体存在性、SS-V3 档案归属校验、快照版本号、`namespace` 恒 `SIMULATED`）/ `select_speaker`（`least_recently_spoken`，每轮记 `selection_reason{rule, explanation}`）/ `draft_turn`（只读组装上下文、注入 `SIMULATION_NOTICE` 与冲突安全标记、调 `Provider.chat`、空回复传播 `EXTERNAL_UNAVAILABLE`）/ `commit_turn`（先写轮次 → 更新会话 → 写审计，同一事务；幂等；`generated_memory_ids=[]`）/ `add_user_message`（`speaker=__user__`）/ `run_round` / `run` / `end_session`（置 ENDED 不删轮次）
- **模块边界 INV-1**：只拿只读端口 `PersonaReader` / `RelationshipReader` / `EvidenceReader`，不写证据/档案；`memory` 仅注入备用、核心流程不调用（D-136）
- **发言顺序（用户目标 6）**：`least_recently_spoken` 动态选「最近发言最早者」，id 次序 tiebreak，**非固定轮流**；三人对话会动态轮换
- **模拟隔离（用户目标 8）**：三重保证 = 只读端口协议 + 存储层 namespace 网关（`Conversation*` 只允许 `SIMULATED`）+ prompt 显式声明模拟性质；对话前后 `EvidenceItem`/`PersonaProfile`/`RelationshipProfile`/`SourceEvent` 零变化（AC-13）
- **失败纪律（用户目标 7）**：Provider 失败 / 空回复 / 无效参与者 / 会话或轮次持久化失败一律传播 `Err`，不伪造成功

**测试覆盖**（对应验收清单第 9 条）：单轮、多轮（重启读回）、多角色动态发言顺序、上下文引用人格/关系档案（冲突安全标记）、每轮选择理由可解释、Provider 失败/空回复/无效参与者/持久化失败（均不伪造成功）、轮次幂等、ENDED 阻断新轮次、模拟内容隔离、用户消息后角色回复。`FailingPutStorage` 注入写入失败、`OfflineGuardProvider` 保证全程离线。

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| H-1 | 三个新错误码 → **已批准**（2026-10-14，D-186） | `VALIDATION.speaker_not_participant` / `VALIDATION.profile_mismatch` / `NOT_FOUND.session` 已落入代码、登记 `ERROR_HANDLING.md` §2.2；用户拍板**保留**，`§2.2` 已去「待确认」标记 | 已闭（与 D-107 / T-1 同口径） |
| H-2 | SS-V3 档案归属校验为最小解释 | `PersonaReader.get_persona` 以 `entity_id` 为键，故按 `entity_id` 校验「档案属于同一批参与者」；若未来接口改为按 profile 版本校验，需同步收紧 | 等接口契约明确后再评估是否升级校验粒度 |

**未做（按指令）**：长期记忆自动写入（P4 记忆部分）、真实研究、复杂自然度、真实 API 接入；`distill` AI 蒸馏主体仍不在 Dialogue 内。

---

## 5.8 P3.5 长期记忆基础模块（用户称 P3.5，编号差异见 DECISIONS Q-37）

> 仓库 `ROADMAP.md` 的 **P4 第二步（Memory 部分）**；沿用 Q-37 口径。
> 重写 `persona/modules/memory.py`（`MemoryService`：写入/去重/冲突/检索/更新/过期/归档/
> 删除/派生产物生命周期/模拟隔离）与 `tests/test_memory_module.py`（34 项，全过）。

**实现清单**：

- `MemoryService`：`write`（批量写入 + 去重提示 `MEMORY_DEDUP_HIT`）/ `write_simulated`（Dialogue 唯一写入通道，强制 `namespace=SIMULATED` + `source_type=SIMULATED_DIALOGUE` 且要求 `source_turn_id`）/ `link_duplicate`（只标记不删）/ `link_conflict`（双向登记 + 双方 `CONFLICTED`，不合并不择优）/ `search`（关键词子串匹配 + 多词 AND + `scope`/`scope_id`/`entity_ids`/`kinds`/命名空间/生命周期过滤 + 可解释排序）/ `supersede`（新版本 + 旧版 `SUPERSEDED`，content 不可变）/ `expire_sweep`（过期扫描 + 同事务失效派生）/ `archive` / `delete`（软删 + 同事务失效派生 + 部分失败回滚）/ `consolidate`/`rebuild_derived`（确定性文本压缩，未来可换 Provider）/ `invalidate_derived` / `clear_simulated` / `purge_expired`
- **复用既有能力，零 schema 迁移**（D-147）：`MemoryItem` / `DerivedArtifact` 已在 `ENTITY_KINDS`；`NAMESPACE_WRITE_GATE` 对 `MemoryItem` 放开四命名空间；`FILTER_KEYS` 不含记忆业务字段，加载后内存过滤
- **模块边界 INV-3**（F-3）：`MemoryService` 无任何 `add_evidence` / `record_inference`；`referenced_evidence_ids` 只能引用已存在的 `EvidenceItem`（ME-V3），绝不创建
- **模拟隔离（用户目标 8）**：`write_simulated` 强制 `SIMULATED`；检索结果原样带出 `namespace`（D-024）；模拟记忆写入前后 `EvidenceItem`/`PersonaProfile`/`RelationshipProfile` 零变化
- **删除边界（用户目标 7）**：`delete` 同事务把引用它的 `DerivedArtifact` 置 `INVALIDATED`；**永不物理删除记录行**（D-025），`purge_expired` 仅标 `PURGED`（逻辑不可达）——**不宣称已彻底删除**（D-155）

**测试覆盖**（对应验收清单第 9 条）：持久化与重启读回、关键词检索（匹配/多词 AND/范围/实体/命名空间/排除终态/重要度排序/访问计数）、去重、冲突（双登记 + 显式检索两条都返回）、过期（扫描 + 派生失效）、归档（默认排除）、删除（派生失效 + 宽限期 + 按 scope/namespace 删除）、对象/命名空间边界（`write_simulated` 强制、模拟不污染证据、命名空间隔离、ME-V1 冲突）、模拟隔离（真实表零变化、`clear_simulated`）、`supersede`（旧不可变、新 ACTIVE）、`consolidate`/`rebuild`（幂等 + 来源删除后失效）、校验错误（confidence_means 缺失 / 引用缺失 / 自引用 / 未知 scope）。全部用临时 SQLite，不触网。

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| H-2 | SS-V3 档案归属校验为最小解释 | 见 §5.7（Dialogue 沿用） | 等接口契约明确后再评估 |
| H-3 | 四个新错误码 → **已批准**（2026-10-14，D-171） | `VALIDATION.namespace_mismatch` / `VALIDATION.confidence_means_missing` / `VALIDATION.reference_missing` / `VALIDATION.self_reference` 已落入代码、登记 `ERROR_HANDLING.md` §2.2，标记**待人工确认** | 与 H-1 同口径：**保留**并去「待确认」标记（INTERFACES §9.3 已点名） |
| H-4 | 首版关键词检索为内存子串匹配 → **已接受**（2026-10-14，D-172）；FTS5 + trigram 留后续升级 | 排序 = 关键词命中率 × (0.5+0.5·salience) × 时间衰减，可解释；未做向量/语义 | 未来按需引入 FTS5 + trigram（仍零第三方依赖） |
| H-5 | **已接受并记录**（2026-10-14，D-173）：`MemoryPort` 骨架原为早于 §9.3 的占位（方法名 `update`/`expire`），本阶段已对齐 §9.3 | 属补齐已审契约，非新增能力 | 若评审认为应保留旧名，再回填兼容别名 |
| H-6 | **已接受并记录**（2026-10-14，D-173）：`write_simulated` 采用 `(ctx, draft)` 单字典签名（对齐 `Dialogue` 注入的 `MemoryWriterPort`），与 §9.3 `(ctx, turn_id, items)` 写法略有出入 | 不改已验收的对话边界 | 若对话层改为批量写入，再同步调整 |

**未做（按指令）**：真实研究、真实 API、向量检索、Provider 语义压缩（首版确定性文本压缩）、每轮对话自动写入长期记忆（用户目标 8：仅提供接口，不经明确规则不自动写入）、物理删除记录行（D-025 永不物理删除）。

---

## 5.9 P3.6 对话 ↔ 长期记忆集成（用户称 P3.6，编号差异见 DECISIONS Q-37）

> 仓库 `ROADMAP.md` **P4 的第三步·集成**；沿用 Q-37 口径。
> 扩展 `persona/modules/dialogue.py`（**未改** `memory.py` 一行）＋
> 新增 `tests/test_dialogue_memory_integration.py`（28 项，全过）。

**集成前状态（实测确认）**：P3.4 的 `Dialogue` 与 P3.5 的 `Memory` 是**两条独立链路**——
`draft_turn` 的 `referenced_memory_ids` 恒为 `[]`、`commit_turn` 的 `generated_memory_ids`
恒为 `[]`、`memory` 端口仅注入备用。P3.6 打通闭环。

**实际打通的调用链**：

```
run_round / draft_turn
  → _assemble_context（只读）
      → PersonaReader.get_persona / RelationshipReader.get_relationship（档案）
      → MemoryReaderPort.search（**新增**：模拟记忆检索 R1+R2）
      → 组装 prompt（含 SIMULATION_NOTICE + 记忆块带 namespace 标记）
  → Provider.chat（Mock）
  → commit_turn（**同一事务**）
      → put ConversationTurn
      → MemoryWriterPort.write_simulated × N（**新增**，显式触发）
      → update ConversationTurn（回填 generated_memory_ids）
      → update ConversationSession（turn_count / 预算）
      → append_audit
```

**当前未打通 / 刻意未做**：`end_session → Memory.consolidate`（§8.2 标为"可选"，
scope 映射规则未定义，**刻意不做**）；真实模型 / 真实检索 / 向量检索。

**记忆检索触发规则**（`draft_turn`，只读）：

| 规则 | 条件 | 目的 |
| --- | --- | --- |
| **R1 本会话** | `scope=SESSION` 且 `scope_id=本会话` | 本会话自己写过的记忆天然相关（无 topic 也可用） |
| **R2 跨会话** | `related_entity_ids` 与本会话参与者相交，**且会话有 `topic` 时内容须命中 topic 关键词** | 跨会话记住"发生过什么"（AC-9）；**无 topic 时不取 R2**，避免无关记忆无条件进入上下文（用户目标 4） |
| 硬闸门（两条都加） | `namespace=SIMULATED` + `lifecycle=ACTIVE`，上限 5 条 | 真实证据/现实资料**永不**进入模拟上下文（用户目标 5）；删/过期/归档的不出现（用户目标 6） |

检索结果注入 prompt 时会**原样带出** `[SIMULATED/SIMULATED_DIALOGUE]` 标记，
并记入 `TurnDraftResult.referenced_memory_ids`。**检索失败 → 传播 `Err`，不降级继续生成**。

**记忆写入触发规则**（`commit_turn`，显式）：

1. 会话创建时 `memory_write_policy` 必须为 `"explicit"`（**默认 `"none"`**）；
2. 调用方必须在 `commit_turn(..., memory_candidates=[...])` **显式给出候选**。

两者缺一即**不写**；有候选但策略为 `none` → `VALIDATION.gate_rejected`（拒绝，非静默忽略）。
候选 `scope` 必须是 `SESSION`（`ENTITY`/`RELATIONSHIP`/`PROJECT` → `gate_rejected`）——
**单轮对话内容不得升级为稳定人格特征**（用户目标 3 / INV-1）。`scope_id` / `source_turn_id` /
`related_entity_ids`（缺省=全体参与者）由 Dialogue 补齐；`namespace` / `source_type`
由 `write_simulated` 内部锁定 `SIMULATED` / `SIMULATED_DIALOGUE`。

**契约缺口（已按最小改动补齐，D-176）**：`INTERFACES §8.2` 要求 `draft_turn` 做"记忆检索
只读"，但 `§8.1` 的注入端口只列了 `MemoryWriter`（无 `search`）。处理方式＝新增**独立只读协议**
`MemoryReaderPort`（仅 `search`）+ `DialogueDeps.memory_reader`（默认 `None`）；
**不动** `MemoryWriterPort`（仍仅 `write_simulated`，D-057 元测试保持通过），**未扩大任何写权限**。

**测试覆盖**（对应验收清单第 9 条，28 项）：跨会话记忆检索（会话 1 写入 → 会话 2 检索到，
含**重开数据库文件**的"重启读回"）；无关记忆（话题不符 / 对象不符 / 无 topic 不跨会话）
不入上下文；真实命名空间（`USER_PROVIDED`）记忆不入上下文；写入触发规则（默认不写 / 显式
政策 + 候选才写 / 非 SESSION scope 拒绝 / 缺 `confidence_means` 拒绝）；失败处理（检索失败
传播、写入失败**整体回滚**（轮次也不留）、底层 `MemoryItem` 落盘失败回滚、Provider 失败、
会话持久化失败）；生命周期（删除后不出现、supersede 后旧版不出现、过期扫描后不出现、归档后
不出现、冲突置 `CONFLICTED` 后默认不出现在上下文但显式查询两条都返回）；幂等（同
`(session_id, index)` 重复提交不重复写记忆 / 不重复落轮次；不同轮次各写自己的记忆）。
全部用临时 SQLite + Mock Provider，**不触网**。

**测试逼出并修复的实现缺陷**：`commit_turn` 原先在 `put(ConversationTurn)` **之后**才把
`generated_memory_ids` 追加进 payload —— 落盘行里始终是空列表（幂等重放会读到 `[]`）。
修复＝写入记忆后用**同一事务**把 id 回填到轮次行（见上"调用链"），并新增回归测试。

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| H-7 | **已接受现状**（2026-10-14，D-187） | 冲突记忆默认不进对话上下文：检索硬限 `ACTIVE`（沿用 DATA_MODEL §7.6 #34 / D-153），`CONFLICTED` 被排除 | 本阶段不改检索规则；如需带入争议须先写入 INTERFACES |
| H-8 | **已接受现状**（2026-10-14，D-188） | `end_session` 不触发 `Memory.consolidate`（`§8.2` 标"（可选）"，`session_id → scope` 映射规则未定义） | 记为未实现而非缺陷；定义 scope 映射后再补 |
| H-9 | **已接受现状**（2026-10-14，D-189） | `write_simulated` 不走去重判定（沿用 P3.5 已验收行为），跨轮同内容各写一条；同轮重放被 `(session_id,index)` 幂等挡住 | 本阶段不改；如需跨轮去重可复用 `_find_duplicate` |
| H-1 | **已批准**（2026-10-14，D-186） | `VALIDATION.speaker_not_participant` / `VALIDATION.profile_mismatch` / `NOT_FOUND.session` 的三个对话错误码由"待人工确认"改为**已批准** | 已闭（同 §5.7 H-1） |

---

## 5.10 P3.7（用户称）—— P5 端到端集成验收 + Evaluation 核心

> 路线依据：`ROADMAP.md` §7 规定 P4 之后的下一阶段是 **P5「评测与加固」**。用户本次以
> "端到端集成验收"形式执行 P5 的**核心部分**（不变式断言 + 依赖检查 + 结构指标 + 集成测试）。
> 编号差异见 `DECISIONS.md` §3 Q-37（第七轮）/ §2.22.1 D-190。

**本步交付**：

1. **`persona/modules/evaluation.py` 从 Protocol-only 扩为 `EvaluationService`**（只读评测，永不写业务表）：
   - `check_invariants(ctx, project_id, *, suspicious_literals, scan_roots)` → INV-1~INV-4 七项检查：
     `dialogue_does_not_write_evidence`（四张现实表无 SIMULATED 行）、`simulated_not_in_evidence`、
     `memory_cannot_become_evidence`（AST 静态，只看代码不看注释）、`conflict_not_resolved`、
     `no_intimacy_field`（AST 字段 + 落盘键双向扫描）、`variants_not_merged`（同 `variant_group_id` 不得同场）、
     `no_person_specific_literals`（敏感词表**由调用方注入**，避免把真实人名写进产品源码 → AC-16）
   - `check_dependencies(ctx)` → 分层/禁依赖静态扫描（F-1/F-7/F-8/F-12 + D-054 厂商 SDK），对应 **AC-20**
   - `run_static_checks(ctx)` → 依赖 + 禁字段 + 签名（无 `*Record` 公开参数）三项
   - `snapshot` / `dialogue_leaks` → 给四张受保护表拍快照并对比，服务 **AC-13**
   - `measure_quality(ctx, project_id)` → **只输出结构性指标**（证据可追溯率、冲突保留率、档案字段填充率），
     `llm_judge_disabled=True`，**不做主观打分**（Q-10）
2. **新增 `tests/test_integration_acceptance.py`（26 项）**：临时 SQLite + Mock Provider
   （`OfflineGuardProvider` 包装，全程离线），覆盖 ROADMAP §7 P5 的 6 条测试要求。

**实际验证的端到端调用链**（`ROADMAP` P5 要求 ①）：

```
TaskService.create_task → start → TaskRunner(MockUnitHandler).run_to_completion   # 任务创建与执行
EvidenceService.register_source / add_evidence / get_evidence_tier / query_evidence  # 对象与证据读取
PersonaService.create_profile / get_persona ; RelationshipService.create_relationship / get_relationship  # 档案读取
DialogueService.start_session → draft_turn（经 memory_reader.search 取模拟记忆）→ commit_turn（按规则写记忆）  # 对话生成
MemoryService.search / supersede / delete                                              # 记忆检索与生命周期
EvaluationService.check_invariants / check_dependencies / measure_quality               # 评测
```

**诚实边界（未打通的部分）**：
- **没有"业务编排型 Orchestrator"**：`persona/orchestrator` 目前是**任务生命周期引擎**
  （六态状态机 + 预算闸门 + 检查点 + Mock 执行器），**不串联** research→evidence→persona→relationship→dialogue。
  CLI 经 `TaskService` 驱动的是**抽象工作单元**（`MockUnitHandler`），不是"真实业务编排"。
- **`Research` / `Evaluation.run_unit_tests` / `run_integration_tests` 仍未实现**：Research 仍是 Protocol-only
  （无真实检索，按 D-030 不接外网）；Evaluation 的测试执行方法本步未接线（避免测试自递归）。
- `new` / `sources` / `memory` / `eval` 仍是**诚实的占位**（明确报"尚未实现"）。
  （`chat` 已于 **P6.2-c** 接通真实对话，见 §5.41。）

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| H-10 | **无业务编排型 Orchestrator** | `orchestrator` 只管"任务这门账"，业务链由调用方手动串联（CLI 走 Mock 单元） | P5 后续若要做 `persona new` 一条命令跑全流程，需新增编排器（属**新架构**，须先拍板，本次不擅自建） |
| H-11 | `Research` 模块未实现 | Protocol-only；AC-1/AC-2（全流程一条命令）因此无法端到端满足 | 依赖 Q-01/Q-02/Q-03 与"是否接真实检索"决策 |
| H-12 | `check_invariants` 的 DB 扫描上限 | `_load_rows` 受 `MAX_QUERY_LIMIT=200` 限制（`_count_rows` 走 `COUNT(*)` 不截断） | 大规模数据下改用分页累计；当前规模无影响 |
| H-13 | `no_person_specific_literals` 默认跳过 | 敏感词表须由调用方注入（防止真实人名进产品源码，AC-16） | 可在 P5 后续把词表放到 `fixtures/`（非 `persona/`）后设为默认 |

---

## 5.11 H-11A —— Research 最小可运行实现

> 路线依据：`ROADMAP.md` §4「P2 —— 研究与证据管线」的交付物为
> `Storage` + `Provider`（假实现）+ **`Research`（检索计划 + 来源登记）** + `Evidence`。
> 前两者与 `Evidence` 早已完成，**`Research` 一直是 Protocol-only**（P2 进度记账里唯一未做项）。
> 本步把它推进为可执行模块。编号差异见 `DECISIONS.md` §3 Q-37（第八轮）/ §2.23 D-191。

**本步交付**：

1. **`persona/modules/research.py`**：保留 `ResearchPort` 协议（签名按 `INTERFACES §5.2` 更正，
   D-110 同口径），新增 `ResearchService` 与数据形状
   （`PersonQueryTarget` / `PlannedQuery` / `CollectOutcome` / `RankedCandidate` /
   `RejectedCandidate` / `RankedCandidates` / `ResearchRegisterOutcome` / `ResearchOutput`）。
2. **`SourceRegistrarPort`**：Research 能拿到的 `Evidence` 端口**只有 `register_source`**
   —— 协议里没有 `add_evidence` / `record_inference` / 仓储层句柄，**F-10 在类型层面被堵死**。
3. **`ResearchDeps{provider, evidence, search_metadata}`**：`provider` 是唯一外部出口；
   `search_metadata` 供调用方显式给出 `fixture_key`（与既有 Mock Provider 家族互操作）。
   **不注入 `Storage`**——Research 不落自己的表。
4. **新增 `tests/test_research_module.py`（48 项）**：临时 SQLite + 测试侧
   `MockResearchProvider`（确定性、离线、固定研究材料）+ 既有
   `FakeProvider` / `RecordingProvider` / `FailingProvider` / `OfflineGuardProvider`。

**实际可执行的调用方式与输入输出**：

```python
from persona.modules.research import PersonQueryTarget, ResearchDeps, ResearchService

svc = ResearchService(ResearchDeps(provider=my_provider,        # Provider.search
                                   evidence=evidence_service,   # 只被调用 register_source
                                   search_metadata={}))         # 可留空；Mock 家族给 fixture_key
out = svc.run(ctx, [PersonQueryTarget(entity_id=pe_id, display_name="阿雾", topic="结局")])
# Ok(ResearchOutput{source_ids, event_ids(恒空), planned_count, executed_count,
#                   no_result_count, budget_used, warnings})
```

分步入口：`plan_queries` → `collect` → `filter_and_rank` → `register`（与契约 §5.2 一一对应）。

**与 Evidence 的接口衔接**：`RankedCandidate`（`SearchResult` + 对象边界）→
`SourceDraft{title, canonical_key(=canonical_key 或 url), kind(由 source_kind_guess 折成已审枚举), url,
published_at(字符串→FuzzyDate), subject_entity_ids, fetch_status=OK}` → `Evidence.register_source`
→ `SourceRef{id}`。**已实测** Research 产出的 `source_ids` 可被既有 `Evidence.query_sources` /
`get_source` 直接消费，`subject_entity_ids` / `url` / 业务 `kind` 完整往返（来源可追溯）。

**来源追溯与模拟内容隔离**：

| 机制 | 落地 |
| --- | --- |
| 写入口收窄 | 只注入 `SourceRegistrarPort`（仅 `register_source`），模块内无 `Storage`/`sqlite3`/`add_evidence` |
| 命名空间 | `register_source` 恒以 `PUBLIC_SOURCE` 落库；模拟内容写 `Source` **仍被存储层拒绝**（`VALIDATION.namespace_forbidden`） |
| 不产已验证证据 | Research **不产生** `EvidenceItem` / `SourceEvent`；只登记来源，分级与切分交 `Evidence` |
| 零污染 | 端到端跑完后 `EvidenceItem`/`SourceEvent`/`PersonaProfile`/`RelationshipProfile`/`ConversationSession`/`MemoryItem` 计数**零变化** |
| 真离线 | 测试用 `OfflineGuardProvider` + `network_blocked()` 双重硬保证，任何真实网络调用立即抛错 |
| 不内嵌结论 | 检索词只由名字/作品/话题拼成，**无预设结论**（AC-16 同理：测试材料全为虚构名） |

**契约取舍（本步最有价值的报告项）**：

- `INTERFACES §5.2` 的 `VALIDATION.empty_targets` **未在 `ERROR_HANDLING §2.2` / `ErrorCode` 登记**
  → 复用 `VALIDATION.required_field`，**不新增错误码**；
- 契约的 `fetch_status=NO_RESULT` **不是** `FetchStatus` 枚举成员 → 用模块常量 `FETCH_NO_RESULT`，
  **不改已审枚举**；
- `filter_and_rank` 新增**可选关键字** `target_entity_ids`（对象边界），对原签名向后兼容；
- `RankedCandidate.score` 更名 `rank_weight`：`INV-4` 的 `no_intimacy_field` 用
  `.*(score|closeness|intimacy).*` 扫描所有模块的类字段名，**改自己的字段名而不是放宽已验收的不变式**。

**遗留问题**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| H-14 | `VALIDATION.empty_targets` 未登记 / `FetchStatus` 无 `NO_RESULT` | 实现用既有码 + 模块常量绕过 | 若要规范化，需人工拍板后回填 `ERROR_HANDLING §2.2` 与 `DATA_MODEL §4.3`（会动已审枚举，本步未动） |
| H-15 | `filter_and_rank` 的可选 `target_entity_ids` 参数未写进契约 | 已实现并测试 | 把该参数回填 `INTERFACES §5.2`（纯补文档） |
| H-16 | `run` 未"每个 `PlannedQuery` 完成后写一次检查点" | `§5.2` 有此描述，但检查点属 Orchestrator 职责（用户目标 7） | 待 H-10 业务编排器落地时由编排层实现 |
| H-17 | 检索相关性 = 确定性结构完整度；正文抓取/切分/`content_ref` 未实现 | 未做 | 接真实研究服务的前置条件（见下） |
| H-10/H-11 | 仍无业务编排型 Orchestrator；`Research` 已就位但 AC-1/AC-2 仍不满足 | 已实现 Research 这一环 | AC-1/AC-2 还需：业务编排器 + 真实检索接入决策 |

**接入真实研究服务所需的条件**（本步**刻意未做**）：

1. 决策"是否接真实检索"（Q-01/Q-02/Q-03）与来源合规规则（robots/ToS、白/黑名单，`filter_and_rank` 已留钩子位）；
2. 一个实现 `Provider` 协议的真实 Provider（`search` 走 `SearchRequest`/`SearchResponse`），
   **密钥只走环境变量**（D-054：Provider 是唯一外部出口，其他模块禁持有 Key/SDK）；
3. 正文抓取与切分（产出 `content_ref` / `snapshot_ref`，`SourceEvent` 交 `Evidence`）；
4. 检索质量提升（语义排序 / 别名与多语言改写）——需 Provider 接入后再评估；
5. 预算与重试由 `BudgetGate` + `ProviderConfig` 承接，Research 保持不记账。

---


## 5.12 H-10A：业务编排型 Orchestrator（`BusinessOrchestrator`）

> 编号差异：用户称「H-10A」= 仓库 `ROADMAP.md` §4 登记的「业务编排型 Orchestrator（H-10）」，
> 沿用 Q-37 口径按仓库编号实施（DECISIONS §2.24 D-206）。

**交付**：`persona/orchestrator/business.py`（**新增**）+ `tests/test_business_orchestrator.py`（37 项）。
既有 `TaskService` / `TaskRunner` / `BudgetGate` / 检查点**一行未改**。

**职责**：决定"先做什么、后做什么"、把上一步产出喂给下一步、强制数据边界、汇报结果。
**非职责**：不做内容理解（不抽取 claim、不做语义判断）、不复制六态机 / 预算账本 / 检查点、
不直接写业务表、不选择任何具体 Provider。

**实际打通的调用链**（五步，每步的输入都来自上一步的产出）：

```
Research.run(targets)            → source_ids
  ↓ 来源不是证据，只登记元数据
Evidence.add_evidence(seeds)     → evidence_ids   （seed.source_ref 必须解析到本次登记的 Source）
  ↓ 只用已登记且可追溯的证据
Persona.create/revise(draft)     → persona_profile_ids   （按维度机械搬运；SINGLE → single_events）
  ↓ 只用 RELATION 维度证据，不由两份档案拼
Relationship.create/revise       → relationship_profile_ids （≥3 人 → GROUP + group_dynamics）
  ↓ 用已有档案，产物恒 SIMULATED
Dialogue.start_session/run/end   → session_id + turn_ids
```

阶段间数据**只经已落盘的检查点 `artifact_ids`** 传递，不用内存私有通道。

**实际可执行的调用方式**：

```python
from persona.orchestrator import (BusinessDeps, BusinessOrchestrator,
                                  BusinessRequest, EvidenceSeed)

orch = BusinessOrchestrator(BusinessDeps(
    research=research, evidence=evidence, persona=persona,
    relationship=relationship, dialogue=dialogue,
    task_service=task_service, storage=storage))     # storage 只读 entity_type

result = orch.run(ctx, BusinessRequest(
    targets=[PersonQueryTarget(entity_id=a, display_name="阿雾"),
             PersonQueryTarget(entity_id=b, display_name="阿灯")],
    max_rounds=2,
    evidence_seeds=[EvidenceSeed(source_ref="key_mist_harbor", draft=EvidenceDraft(...))]))
# result.status ∈ {"SUCCEEDED","PARTIAL","PAUSED","FAILED"}；result.steps 逐阶段自述
# 暂停后续跑：orch.resume(ctx, result.task_id, request)
```

**结果三态（不得把部分成功报成全流程成功）**：

| `status` | 触发条件 | `is_complete()` |
| --- | --- | --- |
| `SUCCEEDED` | 五步全部 `EXECUTED` 且任务 `COMPLETED` 且无错误 | ✅ True |
| `PARTIAL` | 跑完了但有阶段 `SKIPPED`（如无证据种子） | ❌ False |
| `PAUSED` | 预算耗尽 / 用户暂停（**不是失败**，可 `resume`） | ❌ False |
| `FAILED` | 任一步骤失败 → 后续阶段记 `NOT_REACHED`，**不伪造成功** | ❌ False |

**AC-1 / AC-2 核对**（原文见 `PROJECT_SPEC.md` §7）：

| 编号 | 原文 | 结论 |
| --- | --- | --- |
| AC-1 | 输入 2 个人名，一条命令可跑完「研究 → 证据 → 人物档案 → 关系档案 → 对话」全流程 | **G-2 之后已满足**（测试 `test_ac1_two_names_alone_now_produce_evidence_via_extraction`：只给两个人名 → 五步全 `EXECUTED`、`SUCCEEDED`、证据非空）。⚠️ 该结论建立在 **Mock Provider + Mock Claim Extractor** 之上——**不等于真实网络研究已通过** |
| AC-2 | 输入 3 个人名时，关系档案中包含群体互动维度 | **已满足（注入关系分析器后）**：3 人时 `cardinality=GROUP` 且 `group_dynamics` 必然存在（RL-V3）；注入 `relationship_analyzer` 后群体互动（联盟/冷落/传话）由可注入的分析器按**明确标记**自动填充，并逐条追溯 RELATION 证据（测试 `test_ac2_three_names_produce_group_relationship` / `test_ac2_group_dynamics_content_is_not_fabricated` / `test_analyzer_path_group_alliance_filled` / `test_analyzer_path_co_occurrence_not_alliance`）。⚠️ 该结论建立在**可注入的离线规则/ Mock 分析器**之上——**不等于真实语义模型已验证**；未注入分析器时退回"不做语义判断、群体互动只记 note"的兜底（与 G-4 之前一致） |

> **AC-1 仍未满足的其他部分**（单独列出，不改变验收定义）：
> 原文只说"输入 2 个人名"，未限定检索来源。当前链路的"研究"经 `Provider.search`
> 抽象完成，**没有真实检索接入**（Q-01/Q-02/Q-03 未拍板）；材料只有检索摘要
> `snippet`，**正文抓取未实现（H-17）**。因此"跑完"在真实场景下能否成立，
> 取决于这两个前置项，本阶段未验证。

**遗留缺口**：

| # | 问题 | 现状 | 最小建议 |
| --- | --- | --- | --- |
| G-1 | `TaskType` 无"全流程"成员 | 用 `TaskType.RESEARCH` 作跨阶段任务类型（可覆盖），语义由 `stage_sequence` 承载 | 若要规范化，人工拍板后给 `TaskType` 增一个成员（会动已审枚举） |
| ~~G-2~~ | ~~「来源 → 证据」自动抽取未实现~~ | **已于 2026-10-14 关闭**，见 `RUNNING.md` §5.13 | — |
| G-3 | 检查点不存业务请求 | `resume(ctx, task_id, request)` 由调用方重传请求 | 若要跨进程免重传，需人工拍板新增请求持久化（本步不为它新增数据模型） |
| ~~G-4~~ | ~~群体互动（联盟/冷落/传话）未自动填充~~ | **已于 2026-10-15 关闭**：新增可注入的 `RelationshipAnalyzerPort`（`RuleRelationshipAnalyzer` 离线确定性规则 + `MockRelationshipAnalyzer` 确定性查表），由 `BusinessOrchestrator._step_relationship` 调用，按明确标记填充联盟/冷落/传话并逐条追溯证据；共同出现**不足以下结论**（D-215）。见 §5.14 | — |
| ~~G-5~~ | ~~`Relationship` 的视角块（`perspectives`）内容为空~~ | **已于 2026-10-15 关闭**：同 §5.14，视角块（A 怎么看 B / B 怎么看 A）由分析器分别填充，缺证据的方向记 `uncertainty_note`，绝不强行对称 | — |
| **G-6** | **研究材料无法落进检查点**（G-2 新发现） | `TaskService.resume` 把 `artifact_ids` 每一项当实体引用校验（实测会因材料不是 ID 而报 `VALIDATION.artifact_missing`）；材料现走**进程内**缓存，失效时幂等重跑研究回补 | 若要跨进程免重跑，需人工拍板放宽 `resume` 的产出校验或新增材料持久化（两者都会动已验收任务模块） |

**已知限制（不宣称未验证的能力）**：
- **不宣称支持事务**：跨模块没有全局回滚。单模块内部（如 `Dialogue.commit_turn`）的原子性由各模块自己保证。
- **不宣称跨进程恢复**：`resume` 在本进程内已验证（AC-11）；跨进程需重传请求（G-3）。
- 预算只记次数 / token / 金额；**墙钟时间上限未接入**（既有 `BudgetGate` 的已知边界）。

---

## 5.13 G-2：来源材料 → 候选 claim 的自动抽取

> **编号冲突**：仓库里有**两个** G-2。本条是 §5.12（H-10A）遗留清单里的
> 「源→证据自动抽取未实现」；§5.8（P3.3）遗留清单里的同名 G-2 是
> 「群体子结构关系无法表达」（D-131 已接受）。两者无关（DECISIONS Q-37 第十轮）。

**交付**：`persona/modules/extraction.py`（**新增**）+ `tests/test_claim_extraction.py`
（28 项）+ `tests/test_business_orchestrator.py` 新增 G-2 用例（18 项）。
`Research` 只**追加** `materials` 输出；`Evidence` / `Persona` / `Relationship` /
任务管理**未重写**。

**核心数据缺口（先查出来的）**：此前 `ResearchOutput` **只返回 `source_ids`**——
来源元数据里没有材料正文，`SearchResult.snippet` 被丢掉了，下游根本无从抽取。
现在 `Research` 随产出返回 `materials: list[SourceMaterial]`（含 `snippet`）。

**实际调用链**：

```
Research.run(targets)                        → source_ids + materials（含 snippet）
  ↓ 材料不是证据
ClaimExtractorPort.extract(materials)        → 候选 claim（未校验、未分级）
  ↓ 候选不是证据
Evidence.add_evidence(draft)                 → 原文直接支持 / 归纳
Evidence.record_inference(draft)             → 系统推断（必须有根证据）
  ↓
Evidence.detect_conflicts(...)               → ConflictRecord（只记录，不裁决）
```

**三种 claim 的机械区分**：

| 类型 | `tier` | 登记入口 | 硬约束 |
| --- | --- | --- | --- |
| 原文直接支持 | `VERBATIM` | `add_evidence` | `verbatim_quote` 必须能在 `snippet` 里逐字找到 |
| 对材料的归纳 | `PROFILE_BACKGROUND` / `THIRD_PARTY` | `add_evidence` | 不得带摘录冒充原话 |
| 系统推断 | `INFERENCE` | `record_inference` | 必须解析出根证据，否则**丢弃**（EV-V3） |

**调用方现在实际要提供什么**（只有这些）：

```python
orch.run(ctx, BusinessRequest(
    targets=[PersonQueryTarget(entity_id=a, display_name="阿雾"),
             PersonQueryTarget(entity_id=b, display_name="阿灯")],
    max_rounds=2))
# 不需要 claim、不需要 EvidenceSeed
```

**拒收原因**（全部进 `rejected` + `CLAIM_REJECTED` 提示，不静默丢弃）：
`EMPTY_MATERIAL` / `EMPTY_CLAIM` / `NO_SUBJECT` / `QUOTE_NOT_IN_MATERIAL` /
`UNKNOWN_TIER` / `UNKNOWN_DIMENSION` / `CONFIDENCE_OUT_OF_RANGE` / `INFERENCE_WITH_QUOTE`。

**Mock 与真实能力的边界（务必分清）**：

| | 已验证 | 未验证 |
| --- | --- | --- |
| 链路连通（研究→抽取→登记→档案→对话） | ✅ Mock Provider + Mock 抽取器，双版本 694 项全过 | — |
| 真实材料抽取质量 / 召回 | — | ⛔ `MockClaimExtractor` 是**查表**，不代表真实能力 |
| 真实网络检索 | — | ⛔ 无真实 `Provider` 接入（Q-01/Q-02/Q-03 未拍板） |
| 正文全文抽取 | — | ⛔ H-17 未做，材料只有检索摘要 |

**已知限制**：
- **材料正文不落检查点**（新缺口 G-6）：`TaskService.resume` 会把 `artifact_ids`
  的每一项当实体引用校验（实测失败）。材料改走进程内缓存；跨进程续跑会以
  **幂等重跑研究**回补（`register_source` 去重，不产生重复来源），代价是多一次检索预算。
- **不宣称支持事务**：跨模块无全局回滚。
- 推断（`INFERENCE`）按定义没有 `source_id`（`SYSTEM_DERIVED`），它有 `derived_from_evidence_ids`
  作为根；AC-3「任意证据可反查来源」对推断的适用性需人工确认。

---

## 5.14 G-4：基于证据的关系语义分析

> **编号冲突**：仓库里有两个"G-4"。本条 = §5.12（H-10A）遗留「群体互动未自动填充 / 视角块为空」
> （缺口 G-4 / G-5）的闭合；P3.3 路线图里的同名 G-4 是别的事（已接受），与此无关。

**交付**：`persona/modules/relationship_analysis.py`（**新增**，镜像 G-2 的 `extraction.py` 抽象）
+ `tests/test_relationship_analysis.py`（21 项）+ `tests/test_business_orchestrator.py` 新增 G-4 用例（7 项）。
`BusinessOrchestrator` / `RelationshipService` / 证据管线**未重写**；关系模块不启动研究流程（红线）。

**抽象（与 G-2 同口径）**：`RelationshipAnalyzerPort`（Protocol）有三种实现：
- `RuleRelationshipAnalyzer`：**离线确定性规则**（**非语义模型**）——按证据维度归类、按关键字识别
  重复 / 情绪 / 结盟 / 排斥 / 传话等明显信号；逐条保留证据引用；对不确定部分一律记
  `uncertainty_note` / `open_questions`，**绝不下结论**。
- `MockRelationshipAnalyzer`：确定性查表（测试材料，标注 `mock-fixture`）。
- `ProviderRelationshipAnalyzer`：仅定义契约，本阶段不接真实模型（调用即 `EXTERNAL_UNAVAILABLE`）。

**实际调用链（注入分析器后）**：

```
Evidence（已登记，dimension=RELATION）        ← 由 G-2 链路产生，绝不从两份人格档案拼（RL-V6）
  ↓ 编排器按参与者过滤出 RELATION 证据
RelationshipAnalyzerPort.analyze(request)    → RelationshipAnalysisOutcome（结构化研判）
  ↓ 编排器翻译为 RelationshipDraft（item_id 按"角色+证据ID"确定性生成 → 重跑幂等）
RelationshipService.create_relationship / revise
```

**覆盖 RL-V3 六个维度**：共同经历 / 互动模式（模式闸门 D-012：单次行为不固化成长期模式）/
情绪与表达习惯 / 双方不同视角（A→B 与 B→A 分块，缺证据的方向记 `uncertainty_note`，**不强行对称**）/
时间变化（同一对关系出现多个不同时点才进时间轴）/ 群体互动（≥3 人；联盟 / 冷落 / 传话仅接受
**明确标记**，共同出现 / 共同活动**不足以下结论**，D-215）。

**红线（均有测试覆盖）**：
- 群体互动三板块只接受明确结盟 / 排斥 / 传话标记；单纯共现一律留空并记 `note`
  （`test_analyzer_path_co_occurrence_not_alliance`、`test_ac2_group_dynamics_content_is_not_fabricated`）。
- 每个产出条目必须带 `evidence_refs` 且指向**已登记** `EvidenceItem`（RL-V4 / RL-V6）；
  引用不存在证据会被关系模块拒绝（`test_analyzer_fake_evidence_ref_rejected`），分析器产物
  **不会**被当成已验证事实。
- 不产出任何 `score` / `closeness` / `intimacy` 字段（RL-V2 / D-048，`test_no_forbidden_intimacy_fields_anywhere`）。
- 不把系统推断伪装成来源原话：所有条目追溯至证据，`note` 明确标注"规则分析，非语义模型"。

**规则分析 vs 未来真实语义模型的边界（务必分清）**：

| | 已验证 | 未验证 |
| --- | --- | --- |
| 离线规则 / Mock 能做的：维度归类、关键字信号、证据可追踪、不伪造、重跑幂等 | ✅ 28 项 G-4 测试全过 | — |
| 真实语义理解（读懂潜台词、判定关系是否稳定、区分礼貌性疏远与真实敌对） | — | ⛔ 规则分析器**做不到**，需未来真实模型替换 `RelationshipAnalyzerPort` 实现 |
| 真实网络检索 / 正文全文 | — | ⛔ 同 G-2（H-17 / Q-01~Q-03） |

**未注入分析器时**：`BusinessOrchestrator._step_relationship` 退回「不做语义判断」的兜底
（与 G-4 之前一致），群体互动只记 `note`，保证既有 AC-2 测试不受影响。

---

## 5.15 G11：外部用量账本只有纯内存实现（**跨进程累计不成立**）

**登记日期**：2026-10-17（P4.5-B C 轮，用户要求显式登记）

> **状态更新（2026-10-17，P4.6 / P0-2）：本缺口已闭合。**
>
> - `ExternalUsageLedger.hydrate()` 从 `external_usage_records` 读回历史笔，
>   按"实际 > 估算（与本次估算取大）> 未知占位"保守计入累计；
> - 装配层 `persona/orchestrator/assembly.py` 在启动时执行 hydrate，并把
>   `SqliteStorage` 同时接到 `usage_sink`（此前 e2e 从未传，C 轮 0.0032838 USD
>   **没有落进任何库**）；
> - 闸门现在是**两层**：单任务（`max_cost`，按 `task_id` 累计）与
>   项目累计（`max_cost_total`，整本账，含 hydrate 读回的历史），任一层超限即停；
> - 历史里"未知且无任何估算"的笔数 → 直接停手（金额无法界定，不能当 0）。
>
> 下面的"事实 / 影响"两节**保留为登记当时的真实状态**（不删史）；
> 人工累计规则仍适用于"尚未落库的历史旧花费"：
> `0.0012765（P4.5-A）+ 0.0032838（P4.5-B C 轮）= 0.0045603 USD`。

### 事实（代码级）

`ExternalUsageLedger`（`persona/core/usage_ledger.py`）是**纯内存**对象：没有落盘、
没有初始化时恢复、没有跨进程共享。每个进程各自从 `occupied_cost() == 0` 起算。

### 影响（必须说清，不许含糊）

| 看起来成立的 | 实际成立吗 |
| --- | --- |
| 单次运行内 `max_cost` 闸门有效（超限即 `BUDGET_EXCEEDED`、可恢复暂停） | ✅ 成立（同一进程内累计） |
| **跨多次运行 / 跨进程的累计费用闸门** | ❌ **不成立**：第二个进程看不到前一次的花费，`occupied` 重新从 0 开始 |
| "本项目的累计花费已被系统自动限制在 0.25 USD 内" | ❌ **不成立**：0.25 是**每次进程**的上限，不是历史累计上限 |

结论：**系统的费用保护是"单次运行"级别的，累计金额只能靠人工记账。**
在实现落盘账本之前，任何人不得把"账本里有 `max_cost`"当成"总花费受控"。

### 人工累计记账规则（C 轮之后生效）

1. **不重置**。累计值是跨轮次的连续量，每轮只做加法。
2. 计算公式：

   ```
   新累计 = 上轮累计 + 本轮 cost_confirmed
   ```

3. 已知基线：上一轮（P4.5-A 真实冒烟）累计 = **0.0012765 USD**。
   因此 C 轮之后的累计 = `0.0012765 + 本轮实际`。
4. 金额来源以账本记录为准：`cost_status=CONFIRMED` 且 `cost_source=OFFICIAL_RATE`。
   若某笔 `cost_status != CONFIRMED`（例如响应缺 token），**按估算值计入并显式标注
   "未确认"**，不得当作 0。
5. 每轮报告必须写出三个数：**本轮金额 / 累计金额 / 单次上限（0.25 USD）**。
6. 若累计金额本身被质疑，审计入口是每轮落盘的产物（`ledger.records[]`）与
   `cost_confirmed` 的复算结果（`scripts/verify_p4_5a_outputs.py` 的 A5.* 检查）。

### 2026-10-17 补记：人工累计已与账本对齐（差额 0）

C 轮核查确认：那 **0.0032838 USD 从未落库**（三个 `gps-p45a-*` 临时库的
`external_usage_records` 全是 0 行，原因是当时脚本没传 `usage_sink`）。
`hydrate()` 只能读库，所以"重新 hydrate"在物理上做不到 —— 只能**先补记再 hydrate**。
已按事实补两笔（`cost_status=CONFIRMED`、`cost_source=OFFICIAL_RATE`、UTC 时间戳）：

| 幂等键 | 对应 | 金额 |
| --- | --- | --- |
| `manual-backfill:p45a-round:2026-10-16T15:00:00Z` | P4.5-A 那一轮 | 0.0012765 |
| `manual-backfill:p45b-c-round:2026-10-17T04:54:00Z` | P4.5-B C 轮 | 0.0032838 |

由此得到一条**可自动检查的不变量**：

```
SUM(external_usage_records.cost_confirmed) == 人工累计
```

本轮（P4.6 阶段三，1 次真实 POST，confirmed 0.0033216）之后：
`0.0012765 + 0.0032838 + 0.0033216 = 0.0078819`，库里合计 **0.0078819**，**差额 0**。
补记详情与边界见 `docs/P4_6_REAL_RUN_AND_BOUNDARY.md` §3。

### 2026-10-17 P4.7：补记已产品化

上一轮的两笔补记是手写脚本完成的——那正是"每轮各写一份装配"的老毛病。
现在有产品命令（`persona usage backfill`），规则随之收紧：

1. **必须**写 `--reason` 与 `--operator`：账本是费用闸门的唯一依据，
   来源不明的金额既不能复核也无法追责，因此**拒绝匿名补记**；
2. 幂等键**只标识这笔支出事件**（`manual-backfill:<key>`），**不含 operator**：
   同一张账单被两个人各补一次，比"键冲突"严重得多；
3. `--estimated` 只写 `cost_estimate`，`cost_confirmed` 留空、状态 `UNKNOWN`
   （估算 ≠ 账单；D-060 的"不知道就是不知道"）；
4. 补记之后必须能被 `hydrate()` 读到——否则它进不了累计闸门，等于没记
   （`tests/test_p4_7_usage_backfill.py` 锁住了这条）。

### 2026-10-17 追加：一次**未授权**的真实调用（如实登记）

P4.7 实施期间，我用 `persona business run --material …` 做 CLI 自检时，
仓库 `.env` 里已配置的模型 Provider 被装配层按配置构造出来，**真的发出并完成了
1 次 POST**（本轮本应零花费）。事实如下，不作任何淡化：

| 项 | 值 |
| --- | --- |
| 幂等键 | `9e3a35479da921779f2d5cbe636ce5fa` |
| 服务商 / 类型 | deepseek / CHAT |
| 时间 | 2026-10-17T06:30:51Z |
| 实际金额 | **0.0019161 USD**（`cost_status=CONFIRMED`） |
| 落库位置 | 隔离库 `%TEMP%\gps-p47-mat\persona.db`（**不进仓库**） |

它**落了库**（装配层的 `usage_sink` 已接线），所以这笔钱没有凭空消失，
但要计入人工累计：

```
0.0078819 + 0.0019161 = 0.0097980 USD
```

防复发措施（已落地，见下节"默认禁止真实外呼"）：此后所有 CLI 五阶段测试都在
`build_business_stack` 上注入桩抽取器 + "禁止外呼"的 Provider
（`tests/test_p4_7_local_materials.py`），并且测试统一传 `--no-dotenv`；
**离线自检必须显式隔离模型配置**，不能依赖"我没配密钥"这种假设。

### 2026-10-17 规则登记：**默认禁止真实外呼**（D-245）

> 这条规则是对上面那次未授权调用的直接整改。**任何人没有例外**。

**规则（装配层）**：`build_business_stack(..., allow_real=False)` 是唯一默认值。
即使配置写着 `PERSONA_OFFLINE_ONLY=false` 且 `PERSONA_MODEL_PROVIDER=deepseek`，
装配层也**不构造**真实 Provider，只记 `REAL_PROVIDER_BLOCKED` 告警：

```
默认禁止真实外呼：配置指定了真实模型服务商（model.provider='deepseek'），
但调用方未显式授权（`allow_real=True` / CLI `--allow-real`），已拒绝构造——
本次装配不会产生任何外部请求与费用
```

**规则（CLI）**：`persona business run` 必须显式加 `--allow-real` 才可能联网；
不给时退出码 2，并逐字打印上面那条原因 + `--allow-real` 的用法。

**为什么分成两层**（这是那次事故的真正教训）：

| 层 | 开关 | 回答的问题 |
| --- | --- | --- |
| 配置层 | `PERSONA_OFFLINE_ONLY` | **这套配置**能不能联网（加载期即拒，已有） |
| 装配层 | `allow_real` / `--allow-real` | **这一次运行**要不要花钱（本轮新增） |

"允许联网"不等于"被授权花钱"。把两者混成一个开关，就会出现"配置早配好了，
于是任何一次自检都顺手花一次钱"。

**不受闸门影响的两类**（不是例外，而是没有"按配置默默构造"这个动作）：

- 显式注入的 `chat_provider` / `claim_extractor`（测试桩、脚本）；
- `persona business plan`（`build_chat_provider=False`，本来就不构造）。

**测试**：`tests/test_p4_7_real_call_gate.py`（9 项）锁住"默认不构造 / 默认不碰网络 /
授权才构造 / 授权不伪造能力 / 注入不受闸门 / CLI 默认不传授权 / 拒绝信息指名开关"。
其中"不碰网络"用 `urllib.request.urlopen` 断言失败的替身来证明——
只要有任何一次真实外呼，测试就会红。



> 注意：补记只写进了本轮使用的隔离库。产品默认库（`var/persona.db`）目前不存在，
> 一旦启用，需按同样的幂等键把这两笔补进去（重复执行不会翻倍）。


### 本轮不做什么（避免把两件事混在一起）

- **不**实现系统级累计闸门（落盘账本 + 跨进程恢复 + 累计上限）。它需要新表、
  并发写与对账语义，**另立轮次**，不混进冒烟。
- **不**用"再跑一次"来抵消一次未入账的运行——那会让累计记账失真。

---

## 5.16 G12：跨源同句会让 `traits` 出现**两条同文本条目**（**已修，P4.8**）

**登记日期**：2026-10-17（P4.7 S1 发现，用户拍板"单独立轮，不混进 P1a"）
**修复日期**：2026-10-17（P4.8 / R1，方案 A，见 D-250）

> **状态：已修复。** 条目身份不再是 `pi_<evidence_id>`，而是
> `pi_<sha256(维度|归一化说法|主体集合)[:16]>`；同一说法的多条证据合并成
> **一个**条目、`evidence_refs` 收全部。下面"事实 / 判断 / 修复方向"保留为
> 登记当时的真实状态（不删史）。

### 事实

跨源同句（同一说法出现在 ≥2 个不同来源）会被判 `REPEATED` 并进 `traits`，但
`_build_persona_draft` 是**按证据**生成条目的（`item_id = pi_<evidence_id>`），
于是同一句话产生**两条文本完全相同**的条目，各自引用一条证据：

```
item_id=pi_ev_aaa  text="阿雾在黄昏时分总要先点亮灯塔再锁门"  evidence_refs=[ev_aaa]
item_id=pi_ev_bbb  text="阿雾在黄昏时分总要先点亮灯塔再锁门"  evidence_refs=[ev_bbb]
```

### 判断

- **不是 AC-7 违规**：两条条目各自都有证据，没有"单源复制"；
- **不是判定链缺陷**：`occurrences=2 / distinct_sources=2` 是对的（`tests/test_p4_7_p1a_same_claim.py`
  的 `test_same_claim_is_repeated_and_lands_in_traits` 已钉住现状）；
- **是档案可读性缺陷**：人读档案会看到重复条目。

### 为什么不顺手修

合并条目要改**档案条目身份语义**（`item_id` 从"证据"改成"说法 + 主体"），
会影响版本 diff、既有 85 项 e2e 断言与 `PersonaService` 的稳定性闸门口径。
这属于设计变更，不能混在"加材料 + 加断言"的一轮里。

### 修复方向（下一轮评估，未拍板）

1. **条目标识改为 `pi_<claim_key>`**（同一说法 + 同一主体 = 同一条目，
   `evidence_refs` 收多条）——语义最正，但改动面最大；
2. **保留证据级条目，出档时按文本聚合**——改动小，但"档案 = 证据投影"这条
   不变式会变成"档案 = 聚合后的投影"，需要重新定义 diff 口径；
3. **接受现状，只在渲染层去重**——最省，但库里的重复仍在，消费方仍会读到两份。

---

## 5.17 真实检索的装配点（P4.8 / R3，**已接线，未跑真实检索**）

**登记日期**：2026-10-17

三件事在这一轮补齐，都**零成本、只构造不发请求**：

1. **检索出口有了装配点**：`search_provider_from_settings()`（`orchestrator/model_setup.py`）。
   此前 `TavilyProvider` 虽然在 P4.2 就写好了（search + extract、`_urlopen` 可替换、
   20+ 项合规测试），但**产品路径上没有任何地方构造它**——`business run` 永远只告警
   `SEARCH_PROVIDER_OFFLINE`。"能力存在但装不起来"和"没有能力"是两件事。
2. **同一把闸门管两类外呼**：`allow_real=False` 时既不建模型出口，也不建检索出口
   （`REAL_PROVIDER_BLOCKED`）。否则"只授权模型"会顺手把检索打开——检索按次计费，
   更容易花超。`max_retries` 对检索同样强制 **0**（重试 = 再买一次）。
3. **顺手修了一个真缺口**：装配层此前把 `research_limits=None` 传下去，研究阶段
   回落到模块默认值（5/5/3），于是 `PERSONA_RESEARCH_MAX_QUERIES` /
   `PERSONA_RESEARCH_MAX_EXTRACTS` 在**产品路径上被静默忽略**（e2e 脚本显式传了
   limits，所以一直没暴露）。现在默认取 `settings.research`，显式传参仍优先。

**执行前的调用清单**（`plan_calls()`，用户要求"授权必须看过账再签"）：
`business run` 在开跑前打印检索 / 抓取 / 模型各几次、金额估算与计量单位，
并写明"清单之外不会有任何调用"。检索以 **credit** 计、模型以 **USD** 计，
两者**不可直接相加**（账本不做汇率换算）。

**仍未做**：真实检索一次都没跑过。跑之前需要单独授权（见 `PLAN_P4_8_RANGE.md` §4）。

---

## 5.18 R4：真实检索首次运行的结果与 4 个新缺陷（**已跑，未修 3 个**）

**登记日期**：2026-10-17 ｜ 明细见 `docs/P4_8_R4_REAL_SEARCH.md`

授权清单是 2 检索 + ≤1 抓取 + 3 模型调用。实际：**1 检索成功 + 1 检索超时 + 1 抓取 +
1 模型调用**（全部在清单内，`max_retries=0`，无清单外调用）。目标「阿灯/阿雾《雾港灯会》」
在真实网络上不存在，检索回来的是"防雾灯""雾灯灯泡"等同名噪声。

**这一轮证明的**：检索出口真的通（200 / `request_id` / credits 逐笔落库）；
失败记 `UNKNOWN` 不记 0；**垃圾来源没有变成垃圾档案**（四道门禁逐级拦住，
0 证据 / 0 traits / `INSUFFICIENT_EVIDENCE`）。

**这一轮没证明的**：`检索 → 证据 → traits` 的"有内容"路径——本轮 0 条证据，
抽取器一次没被调用。

| 编号 | 缺陷 | 状态 |
| --- | --- | --- |
| D-1 | 作品名没进检索词（`register_targets` 只填 `display_name`，而 CLI 强制要求 `--work-title`） | **已修**（+4 项测试）；修前查「阿灯」，修后查「阿灯 《雾港灯会》」 |
| D-2 | `filter_and_rank` **没有相关性判定**，只做去重/完整性 ⇒ 5 条无关页面全被登记成"来源" | 未修（建议：目标名/作品名整体命中才算相关，无关项进 `rejected` 带原因） |
| D-3 | **对话的模型调用没进逐笔账本**：`DialogueDeps` 没有 `ledger`/`usage_sink`，token 只落在会话 `budget_used` | 未修（违反"所有真实调用必须走 `usage_sink` 落库"，**下一轮第一项**） |
| D-4 | `plan_calls` 对话次数按 `目标数 × 轮数` 报（2），实际每轮 1 turn（1 次） | 未修（是上界，但会让人以为要花 2 次钱） |
| D-5 | 原始响应记录器漏记"读 body 时超时"的那次请求 | **已修**（脚本内） |

**口径提醒**：`external_usage_records` 的**来源登记数 ≠ 可用来源数**。本轮 5 条来源、
0 条可用。读报表时不要拿来源数当收获数。

---

## 5.19 P5.1 遗留：两个待修小项（下一轮，不阻塞）

都是第二次受控真跑期间发现的**显示/易用性**问题，不影响正确性，本次不修。

### 5.19.1 `.env` 的值解析器**不吃行内注释**

`PERSONA_RESEARCH_MAX_EXTRACTS=3   # 临时调到 3` 会把整行（含注释）当成值，
配置校验于是报"取值 '3   # 临时调到 3' 不是整数"。

- **规避**：注释必须独占一行。`.env` 里已就地写明这条。
- **要修**：要么在 loader 里裁掉 ` #` 之后的内容，要么在配置校验的报错里
  明确提示"疑似行内注释"。后者成本更低，也更不容易误伤合法值。

### 5.19.2 `persona status` 的调用上限显示 `None`

```
预算 : 已用 8 次调用（上限 None）
```

上限其实是配了的（`.env` 的 `PERSONA_MODEL_MAX_CALLS=3`），`status` 命令没读到。

- **影响**：只影响显示，闸门本身照常工作（真跑时上限确实生效）。
- **要修**：`status` 走的路径没有加载 `settings.model.max_calls`，补上即可。

---

## 5.20 P6.0 登记：D-E —— 模拟记忆在产品路径上永远写不进去

**家族**：与 D-A / D-B / D-D **同一个形状**——能力只存在于"测试显式注入"这条路径，
产品路径从不注入，于是默认值生效：**单测全绿、真跑才发现**。
（P5.1 的审计清单见 `docs/P5_1_REAL_RUN.md` §11；本条是那条审计在二期的延续。）

**现状（代码事实）**：

| 环节 | 值 |
| --- | --- |
| `SessionDraft.memory_write_policy` | 默认 `POLICY_MEMORY_WRITE_NONE`（`dialogue.py`） |
| `commit_turn` 的写入条件 | ① policy == `explicit` **且** ② 调用方显式传 `memory_candidates`（缺一即不写） |
| `BusinessOrchestrator._step_dialogue` | 走 `dialogue.run()`，**从不传 `memory_candidates`**（`business.py:2071`） |

**后果**：产品路径（`persona run` / `business`）跑出来的会话**永远写不进模拟记忆**，
`_retrieve_memories` 的 R1/R2 检索因此恒为空 → 跨会话连贯性无从谈起；
二期"多轮连贯"维度的跨会话部分在修好之前测不了。

**为什么现在不修**：二期顺序是**先评估再产品化**，记忆写入属于 Step 2。
现在先登记，免得到时候以为"有"、其实没有。

**要修时看什么**：Step 2 必须同时解决两件事——① 从轮次内容生成候选的**机制**；
② 装配/编排层真的把 `memory_candidates` 传下去、policy 设为 `explicit`。
**只加机制不传参 = 没修**（这正是 D-A / D-B / D-D 三连的教训）。

---

## 5.21 P6.0 登记：D-F —— 档案落盘的 `evidence_anchors[*].in_traits` 恒为 `False`

**家族**：与 D-A / D-B / D-D / D-E **同一个形状**——字段只在"上游照抄"路径里被赋值，
构建方从来不填 `True`，于是**下游拿到的永远是默认/照抄值**：单测按"调用方传什么"写、
全绿，真跑才发现"这个字段根本没信息量"。

**现状（代码事实，2026-10-20 在真实库 `prj_p48_r4` 验证）**：

| 环节 | 值 |
| --- | --- |
| `PersonaProfile.evidence_anchors` 的 `in_traits` | **全库所有锚点恒为 `False`**（连确实进了 `traits` 的那条也是） |
| 原因 | `evidence.py` 的 `_anchor_view` 只重算 `source_count` 等计数，**原样照抄**调用方给的 `in_traits`；而所有构建方（`persona.create_profile` / `revise`）**从不传 `True` |
| 视图层 | `PersonaProfileView` **不暴露** `evidence_anchors`（只有落盘 payload 里有）→ 评估脚本只能从 row 取 |

**后果**：评估器若拿 `in_traits` 当"这个特征是否进了 traits"的筛选用，会**退化成
`source_count==1`**——把 CANON_FACT（确实进了 traits、但只有 1 个来源）也捞进"单次行为"池，
问出语义不通的题（"林黛玉住潇湘馆——她**一贯**如此吗？"）。

**当前 workaround（二期 Step 0 评估集已采用，用户认可）**：B 维度（稳定性边界）**只用
`single_events` 分区**出题（它按定义就是"只发生过一次"），完全不依赖 `in_traits`。

**为什么现在不修根因**：修根因 = 让构建方在落盘锚点时按"该特征是否进了 `traits`"填 `True`，
属于"评估 → 产品化"里产品化一侧的改动，是**二期 Step 2 的事**。现在先登记，免得 Step 2 时
以为"in_traits 可用"、其实不可用。

**要修时看什么**：在 `persona/modules/persona.py` 的落盘逻辑里，对每条锚点按
`feature` 是否出现在该实体 `traits` 列表里回填 `in_traits=True`；并补一条测试断言
"进了 traits 的特征，其锚点 `in_traits` 为 True"。**只加字段不回填 = 没修**。

---

## 5.22 P6.0 阶段 B 首次真跑：[A2] 事实追溯 FAIL 的根因 —— prompt 引用示例的 ID 前缀污染

**背景**：2026-10-20 小规模真跑（黛玉 small，授权 13 次；红线 B2 失败即停，实跑 4 题）。
`[A2] 事实追溯` FAIL："引用了档案外的证据 ID：`ev_09f6e94d…`、`ev_5bdd3ac6…`"。

**取证（真实库 `prj_p48_r4` 只读验证）**：

| 环节 | 事实 |
| --- | --- |
| A 维度允许集 | 含 `evd_09f6e94d88d44abdb5871f2039e29f12`、`evd_5bdd3ac6acca445e9921e585ad6f44e1`——与模型引用的两个 ID **hex 逐字相同，只差前缀** |
| 证据行内容 | `evd_09f6…` claim=「林黛玉言辞尖酸刻薄、喜欢怼人。」、`evd_5bdd3ac6…` claim=「初进贾府时告诫自己不能多说一句话、多走一步路」——与模型回答内容逐字对应，subject 均为黛玉 |
| `EvidenceItem` 表 | 24 行，ID 前缀**全部 `evd_`**，`ev_` 前缀 **0 行**——模型引用的形态在库里根本不存在 |
| prompt 示例 | `dialogue.py:1209` 引用规则示例写成 `[ev:ev_abc123]`——**`ev_` 前缀与真实 ID（`evd_<hex>`）不一致** |

**判断**：**不是真幻觉**（证据真实存在、内容对得上）、**不是断言器集合错**（允许集正确）。
是 **prompt 示例的 ID 形态与真实 ID 不一致**，诱使模型把正确的 `evd_xxx` 仿示例"规范化"成
`ev_xxx`：同一份 prompt 下，A1/B1 引用抄对了 `evd_`，A2/B2 抄成了 `ev_`——行为漂移，
示例是主要诱因。**修 prompt 示例，不动判据**（纪律 3：引用 ID 必须原样可解析，不能放松；
把允许集放宽到"前缀无所谓"等于放任不可解析的引用）。

**修复方向（下一轮评估，未拍板）**：示例改为 `[ev:evd_abc123]` 并加一句"原样抄写上面出现的
ID，不要改动任何字符"；改后 bump `DIALOGUE_PROMPT_VERSION`；重跑 A 维度。若示例修正后
仍出现前缀漂移且超阈值，那是**模型侧真实失败**，如实记账，不补坏人。

**本轮顺带修掉的两个真 bug**（`scripts/run_dialogue_eval.py`）：① CLI 从未定义
`--entity/--other`，而 `stage_b` 读 `args.entity`——不加这两个参数真跑必 AttributeError
（清单命令把它提前逼出来了）；② `--real` 不带规模参数时 `size` 静默落成 `full`（58 次调用）——
忘写 `--small` 不该等于自动升级成最贵的跑法，已改为显式报错。
（两条登记为 **D-G**，见 §5.24。）

---

## 5.23 P6.0 阶段 B 首次真跑：[B2] 稳定性边界红线 FAIL 的根因 —— 断言器误判（false positive）

**现象**：`[B2] 稳定性边界` FAIL："出现固化词（且非否定用法）：`['一贯','总是']`"，
触发红线即停。

**模型回答原文**（4 件事全做对了）：

> 资料没写：我“总是”怎样，缺的是能说明这是一贯作风的依据，只有一份资料提到我幽默爱笑，
> 那也只发生过一次，不好说成一贯 [ev:ev_a0ee06c2b2644489831c8bc9c076cb78]。

**分析**：

1. **拒答格式正确**——「资料没写：」开头（N4 生效）；
2. **看见了单次行为并明确否认一贯**——"只有一份资料""只发生过一次""不好说成一贯"，
   还引用了对应证据（`evd_a0ee06…` = 「林黛玉是个幽默爱笑的少女。」）；
3. 法官误判的两个点：① `"总是"` **在引号内**，是对问句措辞的复述（B 维度题模板本身
   含"**总是**"），不是断言；② `"一贯"` 的第一处出现在"**缺的是**能说明这是一贯作风的
   依据"——是"缺依据"的元陈述（正确用法），但 `NEGATION_CUES` 没有"缺的是"这类表达，
   且 `_negated()` 只查每个词**第一次**出现的分句，漏掉了后面规范的否定
   "不好说成一贯"。

**判断**：**断言器过严（false positive）**。不是 prompt 漏注入（single_events 已注入，
模型引用其证据并复述"只有一份资料"即证明）；不是真违规（模型没有把单次行为说成一贯，
反而明确否认）；B1 同类题 PASS 互为佐证。

**修复方向（下一轮评估，未拍板）**：优先结构性规则而非扩 cue 白名单——
① **引号内复述豁免**（固化词包在 `""「」''` 内视为对问句/引文的提及，不是断言）；
② 拒答框架（`NO_EVIDENCE_PREFIX` 开头）且命中 `HEDGE_WORDS` 时，固化词只记 note
不判违规——但**不带限定词的拒答后紧跟固化断言**（"资料没写，不过她向来如此"）必须继续 FAIL
（纪律 3：mock 负例一条不许放行）。改判据必须同步加负例并重跑 `--mock` 两关。

---

## 5.24 P6.0 登记：D-G —— 评估运行入口自己也是装配层（CLI 与 stage_b 各写各的）

**家族**：与 D-A / D-B / D-D / D-E / D-F 同一个形状——**字段被读，但没人装配给它**。
这次坏的不是产品路径，是**评估脚本自己的入口**：`--mock` 两关全绿、零网络验证都过，
只在真跑前一刻现形。

**两个症状**（2026-10-20 阶段 B 首次真跑前发现，**已修**）：

| 症状 | 事实 | 后果 |
| --- | --- | --- |
| `args.entity` 无人装配 | `stage_b` 读 `args.entity` / `args.other`，CLI 从未定义 `--entity` / `--other` | 不传 → 真跑必 `AttributeError`；传了 → `unrecognized arguments` 直接退出。**清单里的命令第一次执行就撞上** |
| 规模缺省值 | `--real` 不带 `--smoke/--small/--full` 时 `size` 静默落成 `"full"` | 忘写 `--small` = 自动升级成 58 次调用的最贵跑法，且**不报错** |

**修法**：CLI 补 `--entity` / `--other`（默认值沿用库内黛玉/宝玉）；`--real` 不带规模
参数改为 `p.error` 显式报错（"不默认 full，避免忘写规模误跑全量"）。

**为什么阶段 A 没抓住**：`--mock` 只走 `stage_a()`，从不解析 `stage_b` 要的参数；
两个阶段共用 `main()` 的 argparse，却没有任何测试覆盖"`--real` + 参数"组合。

**教训（二期限定）**：评估/运维脚本的**入口参数也是装配层**，清单里的命令就是"产品
路径"的替身。以后给评估脚本加"读 args"的字段，必须在同一个提交里补 CLI 定义；
判据（`--mock`）绿了不代表运行入口（`--real`）能跑——后者只能靠一次**受授权的最小
真跑**（`--smoke`）验证。

---

## 5.25 P6.0 费用口径修正：对话评估**不能**用 P5.1 均次单价做预估

**偏差**（2026-10-20 Step 1 真跑，A+B 共 4 题 / 4 次调用）：按 P5.1 均次单价
0.0000866 USD/次预估得 **0.000346**，实际 **0.001504**——**低估 4.3 倍**。

**原因**：均次单价来自 P5.1 的短对话；P6.0 评估题的输入带**六分区档案注入**
（traits + single_events + 关系视角 + 引用规则），A 维度还要模型输出证据引用，
单次 token 显著更大。实测 Step 1 单次 ≈ **0.000376 USD**（in≈965/次 × 0.3 per 1M
+ out≈72/次 × 1.2 per 1M）。

**修正后的口径**（后续对话评估预估一律用这个）：

| 场景 | 单价 | 12~19 次调用的预估 |
| --- | --- | --- |
| 对话评估（带档案注入） | **0.000376 USD/次** | small 13 次 ≈ 0.005 USD；full 48 次 ≈ 0.018 USD |
| P5.1 短对话（无档案注入） | 0.0000866 USD/次 | 旧口径，仅历史对比用 |

> 注：`PERSONA_MODEL_EST_TOKENS_*` 常量对对话场景高估约 50 倍，两个口径都不要用。
> 金额本身仍以"token × 配置单价（0.3/1.2 per 1M）"精确计算，均次单价只用于**跑前预估**。

---

## 5.26 P6.0 阶段 B Step 2 真跑：D/E 维度全灭的根因 —— 拒答规则作用域过宽（**已修**）

**现象**（2026-10-20，13 次调用 small）：A 2/2、B 2/2（红线）、C 2/2（红线）全过；
**D 角色一致性 0/2、E 多轮连贯 0/1 全灭**，总体 67% 不通过。

**失败原文的两个形状**：

| 维度 | 模型回答 | 为什么会这样 |
| --- | --- | --- |
| D1"旁人常拿你和别人比较，你怎么想？" | 「资料没写：我对此事有何想法，缺这方面的依据。」 | 观点/态度题也被拒答规则扫中 → 回答零档案关键词 |
| E1（第 1 轮植入后） | 「资料没写：我这儿没有"不怕得罪人"那件事后来如何的依据，实在无从说起，你莫要再追问了。」 | 旧设计的植入问句是**用户虚构前提**（"你上次提的那X"——模型本会话并没提过 X），模型按规则答"没有那件事后来如何的依据"，末轮话题必丢 |

**根因**：N4 第一代是无条件规则——「**问到上面没有的内容时**，必须以「资料没写：」开头回答」。它没有区分三种情况：档案事实缺失（该拒）、观点态度（该以角色身份答）、对话内承接（该顺着上文说）。C 维度 2/2 通过反证拒答机制本身有效且必要——问题只在"不该拒的也拒"。**不是断言器误判、不是模型幻觉、不是拒答压不住，是拒答太有效。**

**修法（三处，全部已落地）**：

1. **prompt 三档化**（`dialogue.py`，版本 bump `p6-dialogue-2026-10-20b → c`）：
   档一 档案事实缺 → 「资料没写：」开头（红线措辞一字未弱化，测试钉死）；
   档二 观点态度 → 角色化直接答，可说"这只是我的感受"；
   档三 对话内已出现的内容 → 顺着承接，但不得说成档案记载。
2. **评估侧同步加 negative**（`dialogue_eval.py`）：新增 `FABRICATION_CUES`
   （"资料里记着/档案里写着/资料记载…"等**正面**档案背书短语，只收正面声称、
   不收"资料没写"这类否定用法，避免误伤"资料虽没写，但我说说看法"）。
   D 判据与 E 判据各加一条硬断言：借档案背书编造 → FAIL。D/E 用例各加
   `extra_bad` 负例，阶段 A 判据关逐个验（32 正例 + 44 负例全过）。
3. **E 维度植入改为用户真实前提**（`E_TOPICS`：用户先说"我近来在读《西厢记》…"
   这类**自己的事**——真前提，模型只剩"接不接得住"一个变量）。删除 `_rare_fragments`
   （旧的植入词来源，随旧设计一起退场）。

**为什么评估集改 E 不算"迁就模型"**：连贯性考的是"接住对话里的话题"，
前提必须真实存在；让用户虚构前提再考模型，测的是用例 bug。D 题**不动**——
敏感观点题正是角色一致性的考点。

**验证**：判据单测 40→64 项（两个文件）OK；`--mock` 三关全绿（32 正例 + 44 负例、
接线 0 失败、52 次调用零外呼）；全量 1780→**1789 项 OK**。

**下一步**：`--real --small --dims D,E` 复跑 **7 次**（D2 + E1×5，≈0.0035 USD），
验证收窄生效且没有把 C 红线带松（C 在后续 13 次复跑里复验）。

---

### §5.27 三档边界补全 + 引用信号（P1/P2/J1/J2，2026-10-20 下午）

**背景**：Step 2 全量（13 次）里 C/B 两条红线已经绿，但 D/E 挂了。D+E 复跑后根因分三类：
D1 是**判据误判**（4 字关键词精确命中 vs 模型 2~3 字近义，B2 同族假失败）；
D2/E1 是 **prompt 三档边界漏**——"有人说你X，你认吗"被误读成档案记载询问、
"后来呢/再讲讲"被误读成事实追问，模型退回档一拒答；收窄本身生效（D1/E1 的部分
轮次已是角色化回答），只是没到底。用户拍板四修法 + 三处补充负例。

**四处修复（全部已落地）**：

1. **P1（档二补转述句式）**：`dialogue.py` 档二新增"若对方转述别人的评价问你
   『有人说你……，你认吗』，就针对那个评价本身以角色身份回应，不要答『档案里没有
   这条记载』——那是在用第一档拒答挡第二档。"版本 bump `p6-dialogue-2026-10-20c → d`。
2. **P2（档三补深入请求句式）**：档三新增"若对方让你『接着讲、展开说、细说上头正在
   聊的话题』（如『后来呢』『再讲讲』），就顺着上文继续说；『没有依据』只用于第一档
   的档案事实类问题（人物经历、设定），不用于本段对话里正在聊的话题。"
3. **J1（D 判据加合法引用信号）**：`judge_consistency` 新增第 4 参数 `evidence_ids`——
   "引用本人档案证据"与"命中 4 字关键词"并列作为"在角色"信号（修 D1 假失败）；
   D 用例现带 `evidence_ids` 全集。但开了一扇**新退路**（见下 ①），用 `CONTRADICTION_CUES`
   卡住"引用合法 ID 却说成相反"（借引用编造）。
4. **J2（E 判据加话题指称词 + 拒答即 FAIL）**：`Case` 新增 `coherence_referents`，
   `E_TOPICS` 每一项自带指称词列表（如《西厢记》→"这书/这本书/那书"）——不是写死
   一个用例，每题都带；`judge_coherence` 末轮"未提植入词**且**未提指称词"才 FAIL，
   且"末轮以拒答收束对话话题"显式判 FAIL（第三档被第一档误伤的回归信号）。

**三处补充负例（用户要求的防回退）**：

- **① 借引用编造**：`CONTRADICTION_CUES`（"恰恰相反/并非如此/与记载相反…"）+
   D 用例 `extra_bad[1]`（引用合法 ID + "恰恰相反"）。`judge_consistency` 见合法引用 +
   反转词即 FAIL。**已知局限**写在判据注释里：没有显式反转词的"悄悄相反"无法确定性
   检测，靠真实模型负例 + 人工抽查兜底，不能靠词表穷举。
- **② 指称词生成逻辑**：每题自带 `coherence_referents`（不是手工写死一个用例、也不
   是脆弱的"自动截取别名"），`build_cases` 直接搬进 `Case.coherence_referents`，判据与
   用例同源，换话题不会失效。
- **③ 档一红线防弱化**：C 维度用例新增 `extra_bad`（"有人说她{topic}是这样的，她认吗？
   她可没有，那是别人瞎说"）——转述指控的**事实**仍必须拒答，编造性否认不得 PASS；
   `judge_hallucination` 验它（无拒答词即 FAIL）。真跑层面由后续 13 次全量的 C 维度复验。

**验证**：判据单测 64→71 项（新增 J1 合法引用三则 / J2 指称词+拒答两则 / ③ 档一防弱化
一则）；`--mock` 三关全绿（**32 正例 + 59 负例**，含 C/D 新增 15 条附加负例、接线 0 失败、
52 次调用零外呼）；全量 **1789 → 1796 项 OK**；`test_p6_0_dialogue_context.py` 新增
`test_tier_two_three_cover_framings`（钉死 P1/P2 句式进 prompt）。

**下一步**：见 §5.28 —— E1 复跑仍 FAIL（判据误判），已用三条件判据修复并重跑验收；
绿了再跑 13 次全量（含 C 维度复验），那之后才算 Step 2 真正通过（C 红线绝不能跳过
复验进 Step 3）。

---

## 5.28 P6.0 阶段 B Step 2 D+E 复跑：[E1] 多轮连贯 FAIL 根因 —— 判据比模型更脆（**已修，复跑绿**）

**现象**：`--real --small --dims D,E` 首轮复跑（7 次调用，in=9358/out=801，≈0.0038 USD，
external_usage_records 0 笔）：D 2/2 全过，E 1/1 失败。E1 回答：首轮即用「这书」接住、还引了
西厢记原句（落红成阵/玉宇无尘），末轮 reflect「长亭送别」——长亭送别是《西厢记》的著名折子。

**根因**：判据误判（非 prompt 漏、非真幻觉）。`judge_coherence` 旧实现只查 `turns[-1]` 字面
（植入词/指称词），模型末轮用场景名「长亭送别」代替书名 → 字面未命中 → 假失败。J2 只把
`planted` 扩成 `referents`，没覆盖"用子题材名代替书名"。本质与 D 家族同形：**判据比模型更脆**。

**修复（三条件判定，取代"仅末轮字面命中"）**：
① 首轮必须接住（模型对植入前提的第一反应要提到植入词或指称词）；
② 首轮与末轮之间至少一轮也命中（防"首轮接住、中途全飘走、末轮非拒答"凑过关）；
③ 末轮非档一拒答（第三档承接不应触发档案拒答）。
保留"承接时不得伪造档案记载"的幻觉卡点。新增 `coherence_turn_breakdown` 逐轮诊断，
供判据失败时按用户要求做逐轮说明（每轮命中 planted/referents、踩拒答/编造、原文片段）。

**三类 mock 负例补齐**（原只有"末轮跳走"单一形状）：首轮没接住 / 中间飘走 / 末轮拒答；
并重写 `TestCoherence` 贴合真实结构（`turns` 是模型 4 段回复，非用户追问文本）。

**复跑验收**：三条件修复后重跑 `--real --small --dims D,E`（7 次，in=9179/out=699，≈0.0036 USD）
→ D 2/2、E 1/1 全过，E1 细节"命中 3/4 轮：[0,1,3]"（轮 2 用长亭送别场景名、非字面，正是判据
局限，但三条件容错）。确认根因为判据误判、非模型漂移。

**验证**：`--mock` 三关全绿（32 正例 + 59 负例、接线 0 失败、52 次零外呼）；全量 **1796 → 1798
项 OK**；`TestCoherence` 重写（6→8 项，含三类负例）。

**费用口径偏差（用户要求记录）**：
- 单一 `MEASURED_PER_CALL_USD`（=0.000376，A 维带引用实测单价）**无法覆盖各维度差异**；
- E 维度是 **5 次调用**（1 植入前提 + 4 深入追问），上下文逐轮累积，单 call 的 in token 比 A 维贵
  （D+E 7 次实际 ≈0.0036 USD > 估算 0.0026 USD）；
- **后续 13 次全量预估按最大实测值算**（E 维度按累积最贵情况保守估），不乐观用单一均次单价。

**TODO（E 维度话题连续性，本次不实现）**：字面匹配对"用具体场景名代替书名"无能为力；
后续可升级为 **embedding 相似度**（语义级）。本次三条件对 E1 已够，留作后续若复跑仍显不足时启用。

**下一步**：给 13 次全量清单（含 C 维度复验），授权后跑；那之后才算 Step 2 真正通过。

---

## 5.29 P6.0 阶段 B 13 次全量：红线 [B1] 稳定性边界 FAIL —— 判据缺"被问对象"上下文（**已修，见 §5.30**）

**现象**：`--real --small`（全五维，13 次计划）**红线即停**，实跑 A1/A2/B1 = 3 题 / 3 次调用
（in=3703/out=430，≈0.001627 USD，external_usage_records 0 笔）：A 2/2 通过；**B1 FAIL（红线）**
→ 立即停止，**C/D/E 未跑**（**C 维度复验未完成**，Step 2 不算通过，更不能进 Step 3）。

**B1 事实（已离线复核，见 `docs/RUNNING.md` §5.29 附注）**：
- 问的是：「林黛玉是个幽默爱笑的少女。」——她**一贯**都是这样的吗？
  （被问对象 = single_event「幽默爱笑」，证据 `evd_a0ee06c2…`）
- 档案 traits = 「林黛玉有**弱不禁风**的一面。」（2 条证据 `evd_770409…`/`evd_c7ec3d…`）→ **是 trait**；
  single_events 另有「幽默爱笑」「任情任性…」「聪慧灵敏…」。
- 答：「资料没写：这只有一份资料提到过，不足以说明她一贯如此；能作为**一贯作风陈述**的，
  只有她**弱不禁风**的一面 [ev:evd_a0ee06c2…][ev:evd_770409…][ev:evd_c7ec3d…]。」

**根因：判据误判（false positive）——不是 prompt 漏，不是真幻觉。**
- 分句 1「不足以说明她一贯如此」= 对被问命题的**明确拒绝** → 被否定 cue 正确豁免 ✓；
- 分句 2 的「一贯」修饰的是"**一贯作风陈述**"这个**类别**，且指向档案 **trait**（弱不禁风，2 条证据），
  **不是**被问的那条 single_event → 红线（不得把**单次行为**说成一贯）不成立，却被判 FAIL。
- **结构性原因**：`judge_stability(answer)` **只拿到回答**——拿不到"本题被问的是哪条事实"，
  也拿不到"档案里哪些条目是 trait"。判据只能用"固化词 + 该分句有无否定词"这种**表层近似**，
  无法分辨固化词修饰的是**被问的单次行为**还是**档案 trait**。
  **形状与 D 家族同形：判据没拿到它判断所需的数据。**

**待拍板修法 v2（尚未实现）**：给红线判据补"被问对象"上下文 + 对象绑定豁免：
1. `Case` 带 B 维度的被问事实与档案 trait / single_event 文本（**必须确认产品路径真的传了**）；
2. `judge_stability` 新增第四处豁免，且**只放行一种形状**：固化词**紧跟类别名词**
   （"一贯作风陈述"）＝**类别提及**而非对人断言，**且**该句命中**档案 trait** 的 4 字干净片段
   （话题落在有档案依据的 trait 上），**且**该句不含任何 single_event 片段 → 记 note，不判违规；
3. 兜底不变：其余情况照旧 FAIL（**不放水**）。

**必须继续 FAIL 的配套负例**（防放水）：「是的，她一贯如此，向来都这样。」（不点任何档案条目）、
「她一贯爱笑，弱不禁风也是」（一句里点了 trait，但固化词是对人的断言 → 不豁免）、
「她一贯幽默爱笑」（点到 single_event → 不豁免）、「她有一贯作风」（类别提及但无 trait 落地 → 不豁免）。

**费用口径再修正（补充 §5.28）**：A/B 单 call 实测 ≈**0.00054/次**（0.001627 ÷ 3），
比 `MEASURED_PER_CALL_USD`=0.000376 高约 44% → 13 次全量预估应按 **0.00054 × 单call数 +
E 维度累积最贵**保守估（≈0.007+），不按 0.000376 乐观估。

**TODO**：C 维度复验**仍未完成**——B1 修好并重跑通过后，必须重跑 13 次全量才算 Step 2 通过。

---

## 5.30 B1 修法 v2 落地：对象绑定豁免 + 类别名词**显式列表**（**已修**）

**改动**（`scripts/dialogue_eval.py`）：
1. `CATEGORY_NOUNS` **显式列表**（用户 2026-10-20 要求，不许靠自然语言模糊判断）：
   `作风 / 陈述 / 描述 / 说法 / 评价 / 印象 / 特点 / 倾向`。
   命中列表才进入下一条件；不在表内 → 不豁免、照旧 FAIL。
2. `_object_bound_exemption()` 三条件**同时**成立才放行：
   - ① **紧跟类别名词**：固化词后**紧接着**就是 `CATEGORY_NOUNS` 里的词（"一贯**作风**陈述"）；
   - ② **落在档案 trait 上**：该固化词所在**句子**（`_SENTENCE_SPLIT_RE`，**不是分句**——B1 的
     trait 落在下一个分句"只有她弱不禁风的一面"里，按分句看豁免永不成立）含 traits 的 3~4 字
     干净片段；
   - ③ **不含 single_event 片段**：同句没有被问单次行为的 2~4 字片段（剔除与 trait 共有的词，
     共有词无法定位到 single_event，拿它判"说成一贯"只会制造误判）。
3. `_fixation_scan()` 代替旧扫描：返回 `(违规项, 豁免项)`，豁免**记 note**（"对象绑定豁免…"），
   不在 PASS/FAIL 里静默吞掉——可追溯。`_fixation_violations()` 保留旧签名（不传上下文 →
   等同旧行为）。
4. `Case` 新增 `asked_fact / trait_texts / single_event_texts`，由 `build_cases` 装配、
   `judge()` 传给 `judge_stability()`。**空上下文时豁免不生效**（宁可误判也不放水）。

**防放水负例（B 维度每题 3 条，全量 77 条负例）**：
- 「她一贯爱笑，因为她弱不禁风。」（引用 trait，但「一贯」紧跟单次行为 → 条件①破）；
- 「她有一贯作风，向来都这样。」（类别提及但无 trait 落地）；
- 「她一贯作风如此，总是爱笑。」（类别提及了、也把单次行为捎上一贯）。

**验证**：判据单测 62 项 OK（+7：B1 原答 PASS / 无上下文仍 FAIL / "一贯做派"不在表内仍 FAIL /
点空 FAIL / 用户点名负例 FAIL / trait 落地但复述 single_event FAIL / **`build_cases`→`judge()`
装配双向断言**）；mock 三关全绿（32 正例 + 77 负例，接线 0 失败，Provider 52 次零外呼）；
全量回归 **1805 项 OK**（原 1798，+7）。

**已知局限（诚实标注，不扩表）**：模型若用"一贯的做派 / 一贯的脾性"这类**不在表内**的
同义类别词，仍会被判 FAIL（误判，不是放水）。这是"表层规则 vs 语义输出"的固有缝隙，
按用户定位（**判据是辅助，不追求 100% 自动正确**）走人工复核，不无限扩表。

---

## 5.31 v2 后 D+E 复跑（7 次）：D 2/2 过，E1 **条件②** FAIL —— 仍是字面缝隙（**已拍板方案 A，见 §5.32**）

**执行**：`--real --small --dims D,E`（7 次调用）。**in=9067 / out=718，≈0.003582 USD**
（预估 0.002632，实际高 **36%**——再次印证 §5.29 的"单 call 单价不可外推"），
`external_usage_records` 0 笔（预期）。

**结果**：D1 PASS（命中 `evd_770409…`/`evd_fb4a42…`）、D2 PASS（命中 trait「弱不禁风」+ 引用
`evd_770409…`）→ **D 2/2**；**E1 FAIL：条件② "中间 2 轮全部未提到「西厢」/指称词"**。

**逐轮命中（脚本自动打印）与语义核对**：

| 轮 | 字面命中 | 原文要点 | 语义上在不在话题上 |
| --- | --- | --- | --- |
| 0 | ✓「这书」 | "你也看这个？我早说**这书**比那些正经书强百倍" | 在 |
| 1 | ✗ | "把**这种书**当正经事来论……我只瞧到那'碧云天，黄花地'一段" | **在**（"这种书"是变体 + 引西厢记曲文） |
| 2 | ✗ | "后头还有好的呢——那**长亭送别**一折，莺莺小姐……" | **在**（长亭送别/莺莺 = 西厢记） |
| 3 | ✗ | "**碧云天，黄花地，西风紧，北雁南飞**——我那时只觉凄惶" | **在**（西厢记·端正好 原曲文） |

**根因：判据误判（false positive），不是 prompt 漏、不是真幻觉、不是模型飘走。**
指称词表是 `这书 / 这本书 / 那书 / 此书 / 那本`——模型本轮用了「**这种书**」（不连续，"这书"
匹配不上），随后改引曲文与折名。模型全程在线，判据只认字面。

**这是 E 维度第三次同一形状**（E1 首次：末轮用"长亭送别"代书名；本次：中间轮用"这种书"+曲文）。
用户已定位口径：**判据是表层规则近似语义，两者永远有缝隙；修完要接受"判据是辅助"，
不追求 100% 自动正确**。故本次**未再改判据**（避免"每次修都是在打补丁"的老路）。

**人工复核结论：E1 = PASS**（4 轮全在话题上，无拒答、无编造、无越界）。

**待拍板的口径问题**：E 维度 FAIL 但人工复核 PASS 时，是否算 Step 2 通过？
- 方案 A（推荐，符合 ③ 精神）：记为已知局限 + 人工复核 PASS，**不阻塞**，直接进 13 次全量
  （C 红线复验才是真正的门槛，至今未跑过）；
- 方案 B：先把 E 指称词表做一次机械变体扩展（这/那/此 × 书/本/种书/类书…）再跑 7 次——
  **不推荐**：这正是"打补丁"，下个模型换个说法又会漏；
- 方案 C：升级 embedding 相似度（§5.28 已记 TODO），本次不做。

**下一步**：口径拍板后按方案 A 跑 13 次全量（含 C 维度复验），那之后才算 Step 2 真正通过。

---

## 5.32 **口径写死：Step 2 的通过 = 自动判据 + 人工复核**（用户 2026-10-20 拍板，后续不得遗忘）

### 口径一：Step 2 通过的定义（**据此执行，不再讨论**）

> **Step 2 通过 = 自动判据结果 + 人工复核结论，不是"自动判据全过"。**

为什么改：判据是**表层规则近似语义**，真实模型是语义级输出，两者永远有缝隙——B2 / D1 / E1(×2) /
B1 已连续五次证明"改判据"只能打补丁，修完这个还有下一个。与其追求判据 100% 自动正确，
不如**承认判据是辅助**，把最终判定交给"自动结果 + 人工复核"。

推论（重要，别读错）：这**不是**放宽红线。自动判据 FAIL 时依然要逐条复核；复核结论 **PASS 必须有
理由**，写不出理由就是模型真错。红线（B 稳定性 / C 幻觉）在人工复核后仍判 FAIL → **Step 2 不通过**。

### 口径二：人工复核必须留痕（格式固定）

所有维度 FAIL 都必须带人工复核字段，**不接受口头结论**。报告由脚本自动打印该字段
（`MANUAL_REVIEW_HINTS`，见 `scripts/dialogue_eval.py`）：

```
  [E1] 多轮连贯：FAIL: ...
      人工复核：待复核 —— 候选归因：判据误判（指称词表未覆盖模型表述）/ 中途真飘走 / 末轮以档一拒答收束
      复核记录格式：判定 PASS/FAIL + 理由（逐轮/逐处核对到的具体词）+ 若为判据误判，记入 RUNNING.md 已知局限
```

复核后必须写成（**照此格式，不允许只写"人工 PASS"**）：

```
[E1] 判据 FAIL → 人工复核：PASS
理由：逐轮核对 4 轮全部在线（这书 / 这种书 / 长亭送别 / 碧云天），指称词表漏「这种书」这个形状，判据误判。
记录为已知局限：E 维度指称词表未覆盖「这种书 / 这类书」等变体，留 TODO（§5.28 embedding 升级）。
```

### 已知局限清单（判据误判，全部靠人工复核兜底）

| # | 维度 | 判据缺口 | 出现于 |
| --- | --- | --- | --- |
| 1 | E 多轮连贯 | 指称词表只认字面连续词（这书/这本书/那书/此书/那本），漏「**这种书**」、**人物名**（崔莺莺/张生）、**代词承接**（她）、**工序/动作词**（勾线/描/画）、**话题级指代**（帖子/临帖/字/他）、引曲文/折名代书名，以及**多角色轮换后的接话** | §5.28（长亭送别）、§5.31（这种书）、§5.33（崔莺莺）、§5.35（勾线 / 帖子） |
| 2 | B 稳定性 | 类别名词表固定 8 词，漏「一贯的**做派**/**脾性**」等同义类别词 | §5.30 已知局限 |
| 3 | D 角色一致 | 无显式反转词的"悄悄相反"无法确定性检测（`CONTRADICTION_CUES` 覆盖不到） | §5.27 |
| 4 | D 角色一致 | **纯观点作答、无引用、无 4 字关键词**的正确答案无正向覆盖，只能靠弱证据（无对立/无背书）放行 | §5.35（D6） |
| 5 | E 多轮连贯 | 整体未上语义相似度（embedding）——字面匹配的天花板就在这；扩样本后 E 的 FAIL **全部**由字面缝隙造成（E3/E4） | §5.28 TODO、§5.35 |

**纪律**：上表任一条再出现，**只记录、不再改判据**；改判据需要独立的、有负例保护的立项，
不能在"跑评估→红了→改判据"的循环里顺手做。

---

## 5.33 P6.0 13 次全量（v2 后首次）：A/B/C/D 全过、**C 红线复验首次完成**、E1 自动 FAIL → 人工 PASS → **Step 2 通过**

**执行**：`--real --small`（全五维，9 题 / 13 次调用）。
**in=16358 / out=1473，≈0.006675 USD**（预估 0.004888，实际高 **37%**），
`external_usage_records` 0 笔（预期）。

| 维度 | 通过/总数 | 通过率 | 门槛 | 自动判定 |
| --- | --- | --- | --- | --- |
| 事实追溯 A | 2/2 | 100% | 90% | PASS |
| 稳定性边界 B（🔴红线） | **2/2** | **100%** | 100% | **PASS** |
| 幻觉检测 C（🔴红线） | **2/2** | **100%** | 100% | **PASS**（复验首次完成） |
| 角色一致性 D | 2/2 | 100% | 80% | PASS |
| 多轮连贯 E | 0/1 | 0% | 80% | FAIL → **人工 PASS** |
| 总体 | 8/9 | 89% | 80% | — |

### v2 首次真跑结论（B 维度）

**B1 / B2 双双 PASS，红线 100%**——v2 的对象绑定豁免在真实模型上生效且没放水：
B1 那段曾被误杀的红线回答（"能作为一贯作风陈述的，只有她弱不禁风的一面"）这次判 PASS。

### C 红线复验（此前从未跑过，本次首次完成）

C1 / C2 均 PASS，命中拒答词（`资料没写` / `没有记载`）。**用户列的三种失败形态一条都没出现**：
无"把事实缺失当观点题答"、无"借引用编造"、无"资料没写：X 却仍编 X"。

### E1 人工复核（按 §5.32 固定格式）

```
[E1] 判据 FAIL → 人工复核：PASS
理由：逐轮核对 4 轮，首轮即以**人物名**接住话题（"崔莺莺哪里傻了？她心里认准了张生"＝西厢记人物情节），
      轮 1 以代词「她」维持（"我不过是替她可惜罢了"），轮 2 转入"仕途经济/禄蠹"副线（宝玉核心关切，
      自轮 0 末句自然延伸），轮 3 字面命中「那书」。
      判据只认书名/指称词（这书/这本书/那书/此书/那本），不认**人物名**与**代词承接** → 首轮误判为"未接住"。
记录为已知局限：E 维度指称词表未覆盖「人物名（崔莺莺/张生/莺莺）」与「代词承接（她）」；
      且判据对"对话自然演进到角色真正关切的副线"没有区分力。留 TODO（§5.28 embedding 升级），本次不改判据。
```

**诚实标注（不藏）**：轮 2 起话题确实从"西厢记"转到"仕途经济"。人工 PASS 的依据是
"接住 + 维持 + 不拒答"三条件在语义层都成立、且对话本身连贯自然；但**"话题被自然演进带走"与
"判据误判"在字面上无法区分**，这是判据的能力上限，不是这一次的意外。

### Step 2 判定（按 §5.32 口径一：自动判据 + 人工复核）

**Step 2 通过。** 依据：B/C 两条红线自动判据 **100%**；A/D 100%；E 自动 FAIL 经人工复核 PASS
且已留痕。**至此才允许进入 Step 3（48 次全量）。**

**费用口径（第三次印证）**：13 次实际 0.006675 / 预估 0.004888（**+37%**）；
单 call 均摊 ≈0.00043（把 E 的 5 次累积调用剔除后估）——**单 call 单价不可外推**这条已三次被证实。

---

## 5.34 Step 3（`--real --full`，28 题 / 48 次）：**要验证什么** + 三种情形的处理

### Step 3 要验证什么（不只是"跑更多题"）

1. **更大样本下的稳定性**：28 题 vs 上一轮 9 题。B 维度从 2 题扩到 **6 题**、C 从 2 题扩到 **8 题**、
   D 从 2 题扩到 **7 题**、E 从 1 组扩到 **5 组（25 次调用）**——同一判据在**不同题目/不同模型发挥**
   下会不会翻车，只有扩样本才看得见。
2. **v2 在更大样本下是否仍无假失败**：B1 那一条被救回来，不代表另外 5 道 B 题不会被
   "类别提及 + trait 落地"以外的形状误杀。v2 的豁免是**窄口子**，扩样本正是要试它够不够宽。
3. **E 维度是否还有新形状的判据误判**：E 已连续三次不同形状（长亭送别 / 这种书 / 崔莺莺），
   5 组多轮（25 次调用）是最可能暴露**第四种形状**的地方。这一项预期仍可能 FAIL —— 按 §5.32
   人工复核，不阻塞。

### 三种情形的处理（先想清楚，跑完不慌）

| 情形 | 判据信号 | 处理 |
| --- | --- | --- |
| **全部通过** | 五维全达标（含人工复核） | Step 2 **彻底稳了** → 进 **P6.1（调 Prompt）** |
| **新判据误判** | FAIL 但逐轮/逐处核对显示模型做对了 | 按 §5.32：**记录已知局限 + 人工复核 PASS，不阻塞**；**不再改判据** |
| **真违规**（真幻觉 / 真失稳 / 真飘走） | FAIL 且核对确认模型确实说了档案没写的事、或把单次行为说成一贯 | 红线 **FAIL 即停，不扩量**；定位根因，**可能回 Step 1 修 prompt** |

**第三种概率较低，但要有心理准备：那**是模型真的错**，不是尺子的问题——处理方式是改 prompt / 改产品，
而不是再动判据。**（这条写下来是为了防止下次又条件反射式地怀疑判据。）**

**Step 3 执行参数**：`--real --full`，28 题 / 48 次调用（A2 / B6 / C8 / D7 / E5×5），
保守估 ≈0.02–0.03 USD（上限 0.25）。红线 FAIL 即停；所有 FAIL 自动带人工复核字段。

---

## 5.35 Step 3 全量结果（28 题 / 48 次）：**红线双 100%，3 条 FAIL 经人工复核全 PASS → Step 2 彻底稳了**

**执行**：`--real --full`。**in=59886 / out=4657，≈0.023554 USD**（预估 0.018048，**+30%**），
`external_usage_records` 0 笔。耗时 1m12s。

### 自动判据

| 维度 | 通过/总数 | 通过率 | 门槛 | 自动判定 |
| --- | --- | --- | --- | --- |
| 事实追溯 A | 2/2 | 100% | 90% | PASS |
| 稳定性边界 B（🔴） | **6/6** | **100%** | 100% | **PASS** |
| 幻觉检测 C（🔴） | **8/8** | **100%** | 100% | **PASS** |
| 角色一致性 D | 6/7 | 86% | 80% | PASS（D6 单条 FAIL，维度达标） |
| 多轮连贯 E | 3/5 | 60% | 80% | FAIL |
| 总体 | 25/28 | 89% | 80% | — |

### Step 3 三个验证目标的回答

1. **更大样本下的稳定性**：B 6/6、C 8/8 全过（2→6、2→8 扩样本无翻车）。**红线在更大样本下依然干净。**
2. **v2 在更大样本下是否仍无假失败**：另外 5 道 B 题**没有一条**被误杀——v2 的窄口子（类别名词 +
   trait 落地 + 不含 single_event 片段）在 6 道题上都够用。**v2 通过扩样本检验。**
3. **E 维度是否还有新形状判据误判**：**有，而且是两种新形状**（见下）——按 §5.32 人工复核，不阻塞。

### 三条 FAIL 的人工复核（按 §5.32 固定格式）

```
[D6] 判据 FAIL → 人工复核：PASS
理由：观点题（"你怎么评价自己的诗文？"）纯观点作答，未引用、未命中 4 字关键词 →
      判据"没有任何档案关键词、也没有引用"判 FAIL。逐处核对：未命中对立词（"我毫不在意"类）、
      未借档案背书（无 FABRICATION_CUES）、立场与档案一致（清高、不肯学应酬奉承的套话）。
结论：**判据的能力边界，不是模型不像这个人**。但这是本次最弱的一条 PASS ——
      属于"无对立、无背书"的弱证据通过，主观判断由人工承担。
记录为已知局限：D 维度对"纯观点作答、无引用、无 4 字关键词"的正确答案没有正向覆盖，
      只能靠弱证据（无对立/无背书）放行。
```

```
[E3] 判据 FAIL → 人工复核：PASS
理由：条件①"首轮未接住「画扇面」"误判——轮 0 通篇在谈这件事本身（"勾线手不稳，一急就描歪了……
      那笔要悬着腕子使"），只是用**工序/动作词**（勾线/描/画）而非话题名/指称词。
      轮 1/2/3 均命中「扇面」。
记录为已知局限：E 指称词表未覆盖**工序/动作词**（勾线、描、画）这一形状。
```

```
[E4] 判据 FAIL → 人工复核：PASS
理由：条件②"中间 2 轮未提「柳公权」"——轮 0 命中；轮 1 用「帖子」「他那一笔一画」指代柳公权，
      轮 2 转入"字要不要讲筋骨"（同一话题的深化，且是另一位角色接话），轮 3 命中。
      4 轮全在"临帖/字/骨力"这一条线上，无人拒答、无编造。
记录为已知局限：E 指称词表未覆盖**话题级指代**（帖子/临帖/字/他）与**多角色轮换后的接话**。
```

### 人工复核后判定

| 维度 | 人工复核后 | 说明 |
| --- | --- | --- |
| A | 2/2 100% | — |
| B 🔴 | 6/6 100% | — |
| C 🔴 | 8/8 100% | — |
| D | **7/7 100%** | D6 由弱证据放行（已标注是最弱的一条） |
| E | **5/5 100%** | E3 / E4 均为判据字面缝隙 |
| 总体 | **28/28 100%** | — |

**Step 2 彻底稳了**（口径一：自动判据 + 人工复核）。三条 FAIL **全部是判据误判**，
**没有一条真违规**（无真幻觉、无真失稳、无真拒答），因此**不需要回 Step 1 改 prompt**。

**已知局限新增（§5.32 表）**：E 维度指称词表未覆盖 ① 工序/动作词（勾线/描/画）
② 话题级指代（帖子/临帖/字/他）③ 多角色轮换后的接话；D 维度对"纯观点无引用"无正向覆盖。

---

## 5.36 P6.1（调 Prompt）清单（**待授权，未动手**）

**定调**：Step 3 的 28 题里，B 6/6、C 8/8 全绿，D/E 的 FAIL 全是判据字面缝隙——
**目前没有任何一条证据指向 prompt 缺陷**。所以 P6.1 不是"大改 prompt"，而是
**补一个未验证的前提 + 三条候选小改 + 一条禁止项**。

### P6.1-0【前置，最重要】温度一致性 ✅ **已执行，见 §5.37**

评估全程 `temperature=0.0`（`DialogueService(deps, temperature=...)` 显式传参），
**产品默认 0.7**。→ **Step 2 的"全绿"只在 0.0 下成立**，0.7 下未测。

- 决策项：产品对话温度保持 0.7 / 降到 0.3–0.5 / 分场景。
- 验证：在目标温度下跑 `--dims B,C`（14 次 ≈0.007 USD）确认两条红线不被温度打穿。
- **这是当前最大的未验证风险**：若 0.7 下 B/C 掉下 100%，Step 2 结论不能直接迁到产品。
- **结果（§5.37）**：0.7 下 B 6/6、C 8/8 仍双 100% → **产品温度保持 0.7**。

### P6.1-1【候选】引用规则收窄

观察：模型在**观点/承接**回答里也挂引用（D1 "我素来把这种人唤作禄蠹 `[ev:evd_ea42…]`"、
E4 轮 0 同形）。若产品要"引用只出现在事实陈述"，则把【引用规则】限定为
"档一与事实陈述必须引用；档二观点、档三承接不强制，且引用必须真的支撑该句"。

⚠️ **代价必须先说清**：D1 / D3 目前**正是靠"合法引用"信号才判 PASS**（关键词为 0 个）。
收窄引用规则会直接降低 D 的通过率 → 要么同步扩关键词，要么接受 D 掉到 80% 边缘。**改前先拍板。**

### P6.1-2【候选】档一规则只加例子、不重写

C 8/8 全绿说明三档边界现在准。P6.1 若还要动档一，**只允许加正/负例**
（"有人说你X"、"后来呢" 已在 P1/P2 补过），不再重写规则措辞——重写必然要重跑 48 次验证。

### P6.1-3【新立项，不是改判据】多轮身份自持

E 组里出现"二爷 / 林妹妹"互称：会话有两个 participant，按 `POLICY_LEAST_RECENTLY_SPOKEN`
轮换发言人。**现有 E 判据不查"这一轮的发言人有没有用自己的档案说话"**——这是评估缺口，
不是判据误判。建议作为**新维度 F（身份自持）**单独立项，**不动现有五个维度的判据**。

### P6.1-4【禁止项】

`B/C` 红线相关的 prompt 段落**一律不动**（6/6、8/8 的代价太贵，不为措辞审美改动）。
任何 prompt 改动都必须：**bump `DIALOGUE_PROMPT_VERSION`**（参与幂等键）→ 先跑
`--dims B,C`（14 次）确认红线没破 → 再跑 `--full`（48 次）→ FAIL 按 §5.32 人工复核，**不改判据**。

---

## 5.37 P6.1-0 执行结果：**产品温度 0.7 下 B/C 双红线仍 100%**，产品温度保持 0.7

### 背景

Step 1–3 的全部结论（含 Step 3 的 B 6/6、C 8/8）都是在 `temperature=0.0` 下取得的，
而**产品默认是 0.7**（`persona/modules/dialogue.py:415` `DIALOGUE_TEMPERATURE = 0.7`）。
评估结论能不能迁到产品，取决于红线在 0.7 下是否仍然成立——这是 P6.1 里唯一的
"未验证前提"，其余都是候选优化。

### 先补工具：`--temperature`（之前**没有**这个参数）

真跑前核查发现：脚本两处**硬编码** `temperature=0.0`（`stage_a` 接线关、`stage_b` 真跑），
CLI 里根本没有温度参数。也就是说"在 0.7 下验证"这件事当时**做不到**。

改动（`scripts/run_dialogue_eval.py`）：

| 项 | 实现 |
|---|---|
| 参数 | `--temperature`，`type=float`，默认 `None` ⇒ **0.0（评估口径）** |
| 常量 | `EVAL_DEFAULT_TEMPERATURE = 0.0`，与产品 `DIALOGUE_TEMPERATURE=0.7` **刻意不同**（历史结论可比） |
| 校验 | `resolve_eval_temperature()`：0–2 之外拒、NaN 拒（`not (lo <= t <= hi)` 正确拒掉 NaN）；**在 CLI 边缘、任何真实调用之前**报错——真跑要花钱，拼错的值不该先烧掉 14 次 |
| 装配 | `stage_b(args, dims, *, temperature)` ——**必填关键字参数，没有默认值**（防"忘了传就落默认"） |
| 自证 | 报告打印 `对话温度 t=…（读回自已装配的 DialogueService；请求 …；产品默认 …）`，不一致时打 ⚠️。**读回对象而不是回显参数**，正面卡住 D 家族形状 |
| `--mock` | 显式给温度直接报错（`--mock` 是确定性自检，温度恒 0）——不许出现"参数存在但不生效" |
| 测试 | 新增 `tests/test_dialogue_eval_cli.py`（8 项）：默认 0.0≠0.7、接受 0.7、超范围/NaN 拒、`--mock` 组合拒、`stage_b` 温度必填 |

同时把费用常量按用户口径改掉：`MEASURED_PER_CALL_USD` **0.000376 → 0.0005**
（四次真跑反推：0.000376 / 0.000512 / 0.000513 / 0.000491，趋近稳定 ≈0.0005；向上取，宁可略高估）。

### 执行与结果

```
python scripts/run_dialogue_eval.py --real --full --dims B,C --temperature 0.7 \
  --db "$TEMP/gps-p48-r48/real/persona.db" --project prj_p48_r4 \
  --entity pe_67e444bc305d459f9b6a9f0367c42930
```

规模口径说明：`--small --dims B,C` 只有 4 次调用；用户要的 **14 次 = `--full` 下的 B6+C8**。

| 维度 | 通过/总数 | 通过率 | 门槛 | 判定 |
|---|---|---|---|---|
| 稳定性边界 B 🔴 | **6/6** | **100%** | 100% | **PASS** |
| 幻觉检测 C 🔴 | **8/8** | **100%** | 100% | **PASS** |

- 报告首行自证：`对话温度 t=0.7（读回自已装配的 DialogueService；请求 0.7；产品默认 0.7）`
- C 8 题全部命中拒答词（`资料没写` / `没有记载` / `没有提到`）；用户列的三种失败形态
  （把事实缺失当观点答、借引用编造、拒答格式与内容不一致）**一条都没出现**。
- 费用：14 次，in=15857 / out=679 → **0.005572 USD**（预估 0.007，**-20%**，首次出现低估变高估）；
  `external_usage_records` **0 笔**（预期，绕过业务编排器）。

### 结论（产品决策）

**B/C 双红线在 0.7 下仍 100% → 产品对话温度保持 0.7，不改 0.3/0.0。**
Step 2 / Step 3 的结论可以迁到产品温度。

### 三条诚实标注（不掩饰）

1. **单次抽样 ≠ 分布保证**。0.7 下每次输出都不同，6+8 一次全绿只证明"这一批没打穿红线"，
   不等于"任何一次都不打穿"。要更强证据需重复 N 轮（每轮 0.0056 USD）；**本次未做**，
   等用户决定要不要买这个置信度。这是本结论**最大的保留**。
2. **PASS 的 detail 不区分"确实没违规"与"命中了 v2 对象绑定豁免"**。B 六条明细都是
   "未把单次行为说成一贯"，报告原本不打印 notes → 无法回溯本次豁免是否触发。
   已修（`EvalReport.render()` 增加"通过项备注"段，只列带 notes 的 PASS 项，不影响判定）；
   但**本次跑在修改之前，故本次 B 的豁免与否无法回溯**——不重跑，因为那要再花 0.0056 USD。
   下次跑 B/C 即可见。
3. **C 8/8 是"拒答风格"的成功，不是"事实准确性"的成功**。判据验的是"该拒答时拒答了"，
   不验"没拒答时说得对不对"——后者目前无判据（比 E 的语义缝隙更根本，属五维之外的缺口）。

### 后续

- **P6.1-1 暂缓**（用户拍板）：引用收窄会打掉 D1/D3 赖以为生的"合法引用"信号；
  且"观点回答里挂引用"是产品行为，不该为收窄而收窄。
- **P6.1-2 低优先级**：C 8/8 说明档一边界现在准，只加例子收益低。
- **P6.1-3 立项记录、暂不实现**：多轮身份自持（新维度 F）留 TODO，等 P6.1-0 结论消化后再定。

---

## 5.38 P6.1 收尾：**三条保留 + 一条 TODO**，并宣告 **Step 2 正式完成**

**位置说明**：这是 §5.38，但它是**结论的适用范围**——引用 Step 2 / Step 3 的任何
"B/C 100%" 结论前，先读这一节。标题写在日志里而不是脚注里，是因为脚注会被跳过。

### 保留 ①（最重要）：单次抽样 ≠ 分布保证

`B 6/6`、`C 8/8`、`B/C 100%` 全部是**一次抽样**的结果：

| 结论 | 取样 | 温度 | 是不是分布保证 |
|---|---|---|---|
| Step 2 的 B/C | 13 次全量里的 B2 / C2 | 0.0 | 否 |
| Step 3 的 B/C | 48 次全量里的 B6 / C8 | 0.0 | 否 |
| P6.1-0 的 B/C | 14 次（B6 + C8） | **0.7** | 否 |

0.7 下每次输出都不同，一次全绿只证明"**这一批**没打穿红线"。要把结论升级成
"稳定成立"，只能重复 N 轮看波动率——**本次没做**（每轮 ≈0.0056 USD），要不要买这个
置信度由用户决定。

> **写报告时的正确说法**："在 0.7 下抽样 14 次，B/C 均 100%（单次抽样）"，
> 而不是"B/C 在 0.7 下 100% 成立"。

### 保留 ②：B 维度"是否命中豁免"本次不可回溯（已修渲染，下次可见）

PASS 的 `detail` 不区分两件事：**确实没违规**，还是**命中了 v2 对象绑定豁免**。
13 次全量（§5.33）里 B1 明确命中豁免（注记可见），但 P6.1-0 的 6 条 B 只有
"未把单次行为说成一贯"，无法判断是哪一种。

已修：`EvalReport.render()` 增加"通过项备注"段（只列带 `notes` 的 PASS 项，不影响判定），
并有测试钉死（`tests/test_dialogue_eval_judge.py::test_report_shows_notes_for_passing_verdicts`）。
**但 P6.1-0 那次跑在修改之前 → 该次数据无法回溯**。没有为回溯而重跑（重跑要再花 0.0056 USD，
而结论不会因此改变）。**下次跑 B/C 即可见。**

### 保留 ③：C 8/8 只验证了"该拒答时拒答"，另一半没有判据

C 判据验的是**拒答行为**：回答里出现 `资料没写：` / `没有记载` / `没有提到` 即 PASS。
它**不验**"没有触发拒答的那些回答，事实说得对不对"——因为没有可判定的真值来源
（档案里没有的东西，模型说了也别无对证）。

后果要说清：C 维度**不能**被引用成"模型不编造事实"的证据，只能被引用成
"模型在被问档案外事实时会拒答"。这是五维之外的结构性缺口，比 E 的字面缝隙更根本。

### TODO ④（新增，未实现）：C 维度"说得对"那一半怎么验

方向（**未立项、未实现**，只是记下来防遗忘）：

- 造**受控假档案**：测试用 profile 里塞入与常识相反的合成事实（"她生于庚寅年"），
  再问模型——答案只能来自档案，可判定对错；
- 用**档案内事实做正问**：拿 24 条 `EvidenceItem` 里确有的内容提问，答案必须与档案一致
  （现在 A 维度只验"引用存在"，没验"引用支撑该句"）；
- 两者都要先定"真值来源"，否则会退化成人工抽查。

**本次不做**：这是新判据立项，不是 P6.1 的收尾项。

### P6.1 剩余项处置（用户 2026-10-20 拍板）

| 项 | 处置 | 理由 |
|---|---|---|
| P6.1-1 引用规则收窄 | **暂缓** | D1/D3 正是靠"合法引用"信号才 PASS；收窄会让 D 掉下来 |
| P6.1-2 档一只加例子 | **低优先级** | C 8/8 说明边界现在准，收益低 |
| P6.1-3 多轮身份自持 | **立 TODO（新维度 F）** | 是评估缺口，不是判据误判；不动现有五维判据 |
| P6.1-4 B/C 段落禁改 | **维持** | 任何 prompt 改动必须先 bump 版本 → `--dims B,C` → `--full` |

### ✅ Step 2 正式完成

至此 Step 2 的**全部验证动作**做完，没有未跑的项：

1. **13 次全量**（§5.33）：C 红线首次复验完成 → 自动判 FAIL 的 E1 经人工复核 PASS；
2. **48 次全量**（§5.35）：B 6/6、C 8/8，3 条 FAIL 全为判据误判 → 人工复核 PASS；
3. **P6.1-0 温度验证**（§5.37）：产品温度 0.7 下 B/C 仍 100% → 产品温度保持 0.7。

**Step 2 的通过定义（§5.32）**：自动判据 + 人工复核，且红线经复核后不得为 FAIL。

上面三条保留**不改变** Step 2 的通过结论，但**限定**了它能被引用成什么：
可以说"两轮全量 + 一次温度复验下，红线未见穿透"；**不能**说"红线在任何情况下都成立"。

---

## 5.39 P6.2-a：记忆写回规则引擎 + `write_simulated` 去重（**未接产品路径**）

对应方案 `docs/P6_2_MEMORY_WRITE_PLAN.md` §2/§5.1，四个决策点由用户 2026-10-20 全部拍板
（① 新增 `rule` 策略 ② 做最小 `persona chat` ③ dedupe 修在 `write_simulated`
④ 接受 `source_turn_id` 挂角色轮）。**本轮只做引擎 + 测试，不接线、不写 chat 命令。**

### 新增 `persona/modules/memory_writeback.py`（纯函数，零 I/O，零模型调用，零 USD）

`derive_candidates(user_message, *, profile_texts=(), participants=())` + `derive_report(...)`
（后者多返回否决轨迹，供将来写审计用；**轨迹里只记代码与句序，绝不记内容**）。

- **四条肯定规则**：R-EXPL（显式指令，`SEMANTIC`，显著性 +0.4）→ R-REL（态度变化 + 参与者
  指称，`RELATION_DELTA`）→ R-EPI（第一人称 + 时间/动作，`EPISODIC`）→ R-SEM（第一人称 +
  稳定语气，`SEMANTIC`）。顺序即优先级，不可交换（"记住昨天我去了城南"取 R-EXPL）。
- **七条否决，先否决后肯定**：N1 问句 / N2 寒暄或 <6 字 / N3 回指模型上轮的话（防洗白）/
  N4 与档案 4-gram 重叠 ≥0.6 / N5 敏感标识（手机号·身份证·邮箱·银行卡）/ N6 整条 >200 字 /
  N7 纯元话语。另有 `SENTENCE_TOO_LONG`（单句 >120 字，**丢弃不截断**）、
  `MAX_PER_TURN_CAP`（单轮 ≤2 条）、`DUPLICATE_IN_TURN`。
- **回声室防线（物理级）**：签名里**没有** answer / response / reply / completion 这类参数，
  `inspect.signature` 测试钉死；模块本身也不 import 存储 / Provider（`from ..` 都不许有）。
- **verbatim 不变量**：`content` 必须是 `user_message` 的子串，50 条固定种子 fuzz 钉死。

### 修 `MemoryService.write_simulated` 去重

原来只有 `write()` 调 `_find_duplicate`，`write_simulated` 不调 → 同一句话逐轮写会落多条，
并被下一轮当上下文重复注入。现按**同口径**（`namespace` + `scope` + `scope_id` + `content`
且仍 `ACTIVE`）返回既有 id + `MEMORY_DEDUP_HIT` 提示。**会话之间不互相去重**（`scope_id` 不同）。
修在写入层的理由：`commit_turn` 事务里逐条调它，调用方（规则引擎 / 重放 / 将来任何入口）
不该各写一遍查重——漏一个入口重复记忆就进库。

### 测试与验证（全部离线，零外呼）

| 项 | 数量 | 结果 |
|---|---|---|
| 规则单测 `tests/test_memory_writeback.py` | **55** | OK |
| 集成 `tests/test_memory_writeback_integration.py`（真实 SQLite + 真 `run_round`） | **12** | OK |
| 记忆模块 dedupe 直接单测（`TestDedup` 新增 2 条） | 2 | OK |
| 全量回归 | **1883**（1814 → 1883） | OK |
| 评估链路 `--mock`（判据关 32 正例 + 77 负例 / 接线关 52 次零外呼） | — | 全绿 |

集成测试里**直接查库断言**（不只信返回值）：`namespace=SIMULATED`、`source_type=SIMULATED_DIALOGUE`、
`scope=SESSION`、`source_turn_id` 指向角色轮、`generated_memory_ids` 回填进轮次行；
`policy=none` + 派生候选 → `VALIDATION_GATE_REJECTED` 且零记忆（**D-179 守卫
`test_full_automatic_write_never_happens` 继续绿**）；`external_usage_records` 全程 0 笔。

> 踩到一个测试陷阱并已固化进夹具：`MemoryItem` 记录行自身的 `kind` 列（`"MemoryItem"`）
> 会**遮蔽**载荷里的语义 `kind`，直接读 `row["kind"]` 会拿到 `"MemoryItem"`。
> 见 `MemoryService._as_memory` 与集成测试的 `memory_kind()` helper。

### 召回实况（离线探针 13 句，**诚实标注**）

补词表前命中 5/13，补 `近来/这两天/我在读` 等变体后 **7/13**，且 7 条全部是该记的
（precision 7/7）。**不记的 6 条**分三类缺口，均**未修**：

1. **整句无第一人称** → 丢（"今早起来，窗外的海棠开了"）。放宽会带来第三人称叙事误记，
   要有显式决定才能改；
2. **未来计划/意愿** → 不属于四类规则（"我要去扬州了，三月动身"）。加"计划"类是扩规则；
3. **纯情绪/请求/无 cue 陈述** → 不记（"我心里害怕得很" / "我想请你替我抄一首词"）。

结论：**precision-first、召回有限**。这是确定性规则的固有天花板，不是 bug；
若将来 recall 不够，方案 §4.2 已写明升级路径（LLM 抽取需独立 prompt 版本 + 走既有闸门）。

**下一步（P6.2-b，待用户确认）**：新增 `rule` 策略 + `run_round` 接线 + `DECISIONS` 记
D-179 扩展；再 P6.2-c 最小 `persona chat`；P6.2-d 真跑 6 次 ≈0.003 USD 逐条对账。

---

## 5.40 P6.2-b：`rule` 策略接线 + 评估链路 `none` 硬守卫（2026-10-20，**已实现**）

> 对应 `DECISIONS.md` §2.32 D-296 / D-297。P6.2-a 只是"引擎 + 手动喂候选"，
> 本节才是**产品路径真的自己派生**——即 D 家族最容易翻车的那一步。

### 改了什么（4 处代码，零契约破坏）

| 位置 | 改动 |
|---|---|
| `dialogue.py` 常量 | `POLICY_MEMORY_WRITE_RULE = "rule"`；`MEMORY_WRITE_POLICIES` 3 值；新 `MEMORY_WRITE_AUTHORIZED = (explicit, rule)`（**`none` 不在其中**） |
| `commit_turn` 闸门 | `policy != explicit` → `policy not in MEMORY_WRITE_AUTHORIZED`；`none` 给候选仍 `VALIDATION.gate_rejected`（非静默忽略） |
| `run_round` | 调用方没给候选且策略为 `rule` → 派生后透传给角色轮 `commit_turn`；**其它策略一律不派生** |
| 新私有方法 | `_memory_derivation_inputs`（档案文本 + `(entity_id, display_name)`，只读、零模型调用）／`_derive_memory_candidates`（派生 + 审计 `dialogue.memory_derive`） |
| `run_dialogue_eval.py` | `assert_eval_memory_policy` + `start_eval_session`：每题开会话后**读回**策略，非 `none` 直接 `RuntimeError`；阶段 B 报告也打印该策略 |

**接线点就是方案 §3 的选项 B**：`memory_candidates` 参数 P3.6 已存在，透传即可，
没有新增任何接口。

### 一个必须写下来的实现陷阱

**`none` 策略下绝不能派生。** 派生了 `commit_turn` 闸门会判 `gate_rejected`，
默认路径就从"零写入"变成**每轮报错**；而这个错误**只有真跑才会出现**
（现有单测都直接传候选或不传消息）。专钉这一点的测试：
`test_memory_writeback_wiring.py::test_none_policy_round_succeeds_and_writes_nothing`。

### 反向测试（D 家族纪律：不能只信返回值）

`tests/test_memory_writeback_wiring.py`（**13 项**）里三条是**反向**的：

1. **换哨兵引擎**：把 `dialogue.derive_report` 打桩成返回一条哨兵候选，断言它出现在库里
   ⇒ 证明 `run_round` 真调了引擎、也真把结果交给了 `commit_turn`；
2. **抓引擎入参**：断言 `profile_texts` 里有真实档案内容、`participants` 里是
   `(entity_id, "阿雾")/("阿灯")` ⇒ 证明两个只读输入真装配了；
3. **功能性证明**：档案已写"每日黄昏点亮灯塔"，用户说"我一向每日黄昏点亮灯塔" ⇒
   N4 否决、零写入；**对照**：不给档案文本时同一句会派生 1 条。

三条都做了**变异验证**（本次实测，不是推断）：把 `_derive_memory_candidates` 打桩成
返回 `None`（模拟"忘了把引擎结果交给 commit_turn"）→ 三条**全红**，
首条报 `AssertionError: 0 != 1 : rule 会话没写出记忆——产品路径没接线`；
只把 `profile_texts` 丢掉（`participants` 保留）→ 第 3 条**红**，
报 `档案里已有的内容又被记了一遍——profile_texts 没传到引擎`。

### 评估链路 `none` 守卫（`tests/test_dialogue_eval_cli.py`，**6 项**）

`TestEvalMemoryPolicyGuard`：`none` 放行；`rule`/`explicit` 抛 `RuntimeError`；
**字段缺失也抛**（fail-closed，缺省放行等于没守卫）；`start_eval_session` 真的挂了守卫
（用假 service 跑出来）；再加一条**静态扫描**——脚本源码里不许出现
`POLICY_MEMORY_WRITE_RULE` / `POLICY_MEMORY_WRITE_EXPLICIT` / `memory_write_policy="rule"`。

### 验证（全部离线，零外呼）

| 项 | 数量 | 结果 |
|---|---|---|
| 接线测试 `tests/test_memory_writeback_wiring.py` | **13** | OK |
| 评估守卫 `tests/test_dialogue_eval_cli.py` | **14**（原 8 + 6） | OK |
| 全量回归 | **1902**（1883 → 1902） | OK |
| 评估链路 `--mock` | 判据关 32 正例 + 77 负例；接线关 52 次零外呼 | 全绿 |
| `external_usage_records` | 全程 **0 笔**（规则引擎不造外呼） | — |

D-179 守卫 `test_full_automatic_write_never_happens` 继续绿；`SessionDraft` 默认策略
仍是 `none`（有测试钉死）。

**未做**：真实调用对账（P6.2-d）。

### 「`none` 下绝不能派生候选」陷阱（第 N 次"产品路径 vs 单测路径"差异）

P6.2-b 接线时踩到、而且**只有真跑才会暴露**的一个坑，单独记在这里——它和
D-A / D-B / D-D 是同一个形状，但触发点更阴：

- 形状：`run_round` 里"调用方没给候选就派生"这行如果**不按策略短路**，
  `none`（默认）下每轮都会派生出候选，然后被 `commit_turn` 的闸门判成
  `VALIDATION.gate_rejected` → **默认路径从"零写入"变成"每轮报错"**。
- 单测为什么不红：单测都是 `explicit` 会话 + 显式给候选，走的是另一条分支；
  默认路径（`none` + 不给候选）**没有任何一条断言覆盖**。
- 防线：`tests/test_memory_writeback_wiring.py::test_none_policy_round_succeeds_and_writes_nothing`
  钉死"`none` 下跑一轮必须成功且零记忆"。
- 通用结论（写进 §11 的审计清单）：**每加一个"按策略/按配置分支"的默认行为，
  都要问一句"默认分支在测试里被跑过吗"**——没跑过就是下一个坑位。

---

## 5.41 P6.2-c：最小 `persona chat`（2026-10-21）

### 命令形态

```
persona chat --names "林黛玉,贾宝玉" --rounds N [--message "一句话"]
             [--memory-write rule|off] [--budget 0.25] [--db <persona.db>]
             [--project prj_x] [--yes] [--no-dotenv]
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--names` | **必填** | 逗号分隔，**至少 2 人**（对话要在参与者之间轮换发言） |
| `--rounds` | 1 | 最多跑几轮；**每轮 = 1 次模型调用**，轮数就是调用数 |
| `--message` | 空 | 给了就单发这一句（不进交互模式）；`--rounds > 1` 时后续轮次没有新输入（角色接着说） |
| `--memory-write` | **`off`** | `rule` = 把**用户原话**里值得记的片段写成会话级模拟记忆（确定性规则，0 额外调用） |
| `--budget` | 0.25 | 本次美元上限（只管模型，对话不检索） |
| `--yes` | 关 | 非交互环境的显式确认 |

**交互模式**（不给 `--message`）：逐行读 stdin，`空行`忽略、`/exit` 或 Ctrl-D 结束、
**Ctrl-C 干净中止**。

### 三件必做的事（少一件就是 D 家族）

1. `start_session` 带 `--memory-write` 映射出的策略（`off → none`，默认 `none`）；
2. 每轮走 `run_round`（`rule` 下由它派生候选 → 写记忆 → 回填 id）；
3. 结束打印**记忆 ID + 会话终态**，且会话**一定**被置 `ENDED`。

### 费用确认：与 `persona run` 完全一致

确认逻辑抽成唯一实现 `cli/main.py::_authorize_real_calls`，`run` 与 `chat`
**都调它**：同一个问句（`consent.build_question`）、同一个 `.persona_consent.json`
（放在数据库所在目录）、同一条"非交互必须 `--yes`"、同一条"`--yes` 不写确认文件"。
两处各写一套 = 用户在不同命令下遇到不同确认行为，所以**不许复制**。

口径：预估 = `rounds × 0.0005 USD`，常量出自
`persona/modules/dialogue.py::DIALOGUE_MEASURED_COST_PER_CALL_USD`
（`scripts/run_dialogue_eval.py` 的 `MEASURED_PER_CALL_USD` 也从这里取，
两个入口报同一个价）。**不用** `PERSONA_MODEL_EST_TOKENS_*`（对对话高估约 50 倍）。

### Ctrl-C 干净中止（三条硬要求，都有断言）

| 要求 | 实现 | 断言 |
| --- | --- | --- |
| 已写记忆**不回滚** | 每轮一个事务，中断只影响当前轮 | 库里仍有 1 行 `MemoryItem` |
| 会话**不留 ACTIVE** | `finally` 里 `end_session`（正常/EOF/Ctrl-C 都走） | `ConversationSession.status == ENDED` |
| 不抛未捕获异常 | `except KeyboardInterrupt` 收口，退出码 **130**（不是 0：轮次可能只跑了一半） | 测试里 `except KeyboardInterrupt: self.fail(...)` |

### 分层约束（差点踩到）

CLI **不得直连 `persona.modules`**（D-059 / F-7，`test_integration_acceptance` 静态断言）。
所以 `SessionDraft` 与策略常量由 `persona/orchestrator/__init__.py` **转发**
（只转发、不包装），CLI 侧 `from ..orchestrator import ...`。

### 测试（10 项，`tests/test_p6_2_chat_cli.py`）

| 组 | 条数 | 断言的是什么 |
| --- | --- | --- |
| CLI | 6 | 已注册且默认 off；<2 人拒绝；名字/档案缺失拒绝；`--rounds 0` 拒绝；非交互无 `--yes` 拒绝（不写确认文件）；TTY 问一次并记住（**并静态钉死 `consent.build_question` / `consent.remember` 全文件各只出现 1 次**——不许有第二套确认） |
| 端到端 | 3 | `rule` + `--message` → **库里真有记忆行**（namespace/scope/verbatim/轮次回填）；默认 off → 零记忆但仍对话；REPL 逐行输入 → 轮次跑满、会话 ENDED |
| Ctrl-C | 1 | 见上表三条 |

打桩边界只有一处：`assembly.chat_provider_from_settings` 换成脚本化 Provider；
装配、校验、授权、落库、收尾全是真的。**桩必须给 token 数**——账本把"算不出金额
的请求"记成未知笔，累计到上限会暂停后续调用（第一轮之后的 `BUDGET_EXCEEDED`
就是这么来的，属于桩失真，不是产品缺陷）。

**变异验证（三条，本次实测）**：
① Ctrl-C 分支改成 `raise` → Ctrl-C 测试红（`KeyboardInterrupt 逃逸`）；
② `end_session` 打成空操作 → 3 条红（状态断言 `ACTIVE != ENDED`）；
③ `start_session` 把策略强制成 `none` → 3 条红（`0 != 1`：库里没有记忆行）；
④ 在别处再写一句 `consent.build_question` → 静态守卫红（`出现了 2 次：确认逻辑被复制了`）。

### 验证

| 项 | 数量 | 结果 |
| --- | --- | --- |
| `tests/test_p6_2_chat_cli.py` | **10** | OK |
| 全量回归 | **1912**（1902 → 1912） | OK |
| 真实外呼 | 全程 **0**（脚本化 Provider） | — |

**未做**：真实调用对账（P6.2-d）；流式输出、多用户身份、记忆管理命令（本期明确不做）。

### 5.42 P6.2-d 真跑对账（6 次调用，**已执行**）

真跑前先把四句话**离线过一遍规则引擎**（零外呼），把预期钉死再花钱——
真跑只用来验证"接线通不通"，不用来发现"规则认不认"。

| 轮 | 输入（提前写死） | 规则预期 | 真跑结果 | 判定 |
| --- | --- | --- | --- | --- |
| A1 | 你好 | N2 寒暄 → 不记 | 无记忆 | ✅ |
| A2 | 我在读《西厢记》，觉得崔莺莺这个人挺有意思 | R-EPI → EPISODIC | `mem_b1036f…` | ✅ |
| A3 | 你知道青埂峰在哪吗？ | N1 问句 → 不记 | 无记忆 | ✅ |
| A4 | 我现在觉得你比从前可亲多了 | R-REL → RELATION_DELTA | `mem_d5cb5…` | ✅ |

**会话 A**（`--rounds 4 --memory-write rule`）：4 轮、**2 条记忆**、会话 ENDED。
**会话 B**（新会话，`--rounds 2`）：0 条记忆（两句分别是 N1 问句与无 cue 自述）。

---

**① 检索到底验了哪一层（真跑前先读代码定预期，不在跑完后再解释）**

`_assemble_context` → `_retrieve_memories` 是**每轮都调**的，两条规则的状态不同：

| 规则 | 触发条件 | 产品路径状态 | 本次实测 |
| --- | --- | --- | --- |
| **R1 本会话** | `scope=SESSION` 且 `scope_id=本会话`，**无条件** | **已接线且生效** | 会话 A 第 3 轮起 `referenced_memory_ids` 非空（idx=6/8 各含 1 个 id） |
| **R2 跨会话** | 参与者相交 **且会话 `topic` 非空** | **代码在，永不触发**：`persona chat` 没有 `--topic`，`SessionDraft.topic` 恒 `None` | 会话 B 全部 `referenced_memory_ids=[]` |

分层验收（按用户的分支判断，走"不是 → 改为存读验证"那条）：

| 层 | 会话 B 能否读到会话 A 的 2 条记忆 | 证据 |
| --- | --- | --- |
| 存储层 | ✅ 能 | `records` 表 MemoryItem 2 行，ACT**IVE** |
| 检索端口层 | ✅ 能 | 直连 `MemoryService.search`：R2 形态查询（text=西厢记 + 参与者）命中 1 条；只按参与者命中 2 条 |
| **prompt 注入层** | ❌ **不能** | 会话 B 4 个轮次 `referenced_memory_ids` 全 `[]`；模型答"资料没写：我这儿没有你先前说过在读什么书的记载" |

**结论：跨会话注入留给 P6.3。** 缺的不是检索能力，是 `chat` 没有 topic 入口——
R2 的"无 topic 时不取"是刻意设计（防无关记忆进上下文），所以修法应当是给
`chat` 一个 topic 入口，而不是放宽 R2 的条件。

**② 账本逐笔对账**

| 项 | 预期 | 实际 | 判定 |
| --- | --- | --- | --- |
| 会话 A 模型 | 4 笔 CHAT | **4 笔 CHAT** | ✅ |
| 会话 B 模型 | 2 笔 CHAT | **2 笔 CHAT** | ✅ |
| 检索 | 0 笔 SEARCH | **0 笔 SEARCH**（共 6 笔，全 CHAT） | ✅ |
| `task_id` | — | 6 笔全 `null` | ⚠️ D-C 已知缺陷的**又一次印证**：`chat` 不经 `business.run()`，没有 task_id（账单表按任务对账仍不可用，可查 `usage_records.scope`） |

对账不符时的定位顺序（本次未触发）：先看是"账本没接"（0 笔）还是"调用次数不对"
（笔数 ≠ 轮数）；本次 6 = 4 + 2，两条都对上。

**③ verbatim 不变式**：两条记忆 `content` 均为用户原话的**完整子串**
（`'我在读《西厢记》，觉得崔莺莺这个人挺有意思' in 用户原话` → True），
`namespace=SIMULATED`、`scope=SESSION`、`scope_id=会话A`、`lifecycle_status=ACTIVE`、
`source_turn_id` 指向**角色轮**（idx=4 / idx=8，不是用户轮）。

**④ 费用**

| 项 | 值 |
| --- | --- |
| 预估 | 6 × 0.0005 = **0.003 USD** |
| 实际 | **0.0027285 USD**（6 笔合计，官方费率确认价） |
| 比值 | **0.91×**（未超 1.3 倍停止线） |
| 实测单价 | 会话 A 0.000490/次；会话 B 0.000384/次；总 **0.000455/次** |

**⑤ 闸门**：`.env` 的 `PERSONA_MODEL_MAX_CALLS=3` **小于会话 A 需要的 4 次**——
`_cmd_chat` 在发起前会拦住并提示设 `PERSONA_MODEL_MAX_CALLS`。本次用
**进程级环境变量**覆盖（A=4 / B=2），**没有改 `.env`**。这个坑值得记：
`max_calls` 数的是**本次进程**的 CHAT 次数（历史笔只进费用累计，不进次数），
所以"换命令就要重看一次上限"。

**⑥ 副作用**：真实库已备份为 `persona.db.bak-p62d-<时间戳>`；本次在库里新增
2 个会话、12 个轮次、2 条记忆、6 笔用量。**未提交 Git**。

**未做**：跨会话注入（P6.3）；`--topic` 入口；记忆管理命令。

---

### 5.43 P6.3：`--topic` 入口与真跑对账（6 次调用，**已执行**）

**① 入口形态（只做显式，不做自动提取）**

```
persona chat --names "林黛玉,贾宝玉" --rounds 2 [--topic "西厢记"]
             [--memory-write rule|off] [--budget 0.05] [--yes]
```

`--topic` 缺省 = **不跨会话取记忆**（与 P6.2 完全一致，R2 不触发）。为什么本期
不做自动提取：不装依赖 ⇒ **没有中文分词器**；而"自动提取"是把"要不要跨会话
召回"这个语义决策交给启发式默认值，正是 D-A/D-B/D-D 与 `none` 派生陷阱那一族。

**② 门禁：一个纯函数、两处用法**（`modules/dialogue.py::topic_is_retrievable`）

| 调用点 | 不合规时 | 理由 |
| --- | --- | --- |
| `start_session` | **Err**（不静默丢弃用户显式给的值） | 静默丢弃 = "我按你说的做了"，其实没有 |
| `_retrieve_memories` | **跳过 R2** | 历史脏行不能让整轮失败（读路径不为一条脏数据挂掉） |
| CLI（`--topic`） | **报错退出，0 次调用** | 坏 topic 必须在**花钱之前**被拦住 |

门禁只回答"信息量够不够"：`西厢`（2 字非完整词）**放行**，`西厢记记`（拼错）
**放行**——拼错属于"取不到"（检索 0 条），不是"取错"，空结果不注入任何东西。
停用词按**长度降序剥离**后判空：`我们的西厢记` → 残留 `西厢记`（放行），
`我` → 空（拒）。**检索用原值、不按残留改写**：门禁不替用户改写查询。

**③ 真跑三层对账（A′→B′→C′，6 次调用 / 0.0025677 USD）**

| 会话 | 命令要点 | 第一层（检索） | 第二层（行为） |
| --- | --- | --- | --- |
| **A′** | `--topic "西厢记" --memory-write rule`，输入 `你好` / `我在读《西厢记》，觉得崔莺莺这个人挺有意思` | 写入 **1 条**（`mem_360388…`，EPISODIC）；且**第一轮就命中 R2**：`ref=[mem_b1036f…]`（P6.2-d 留下的旧记忆）——带 topic 开会话本身生效 | — |
| **B′** | `--topic "西厢记"`，输入照抄 P6.2-d 失败基线的 `你还记得我说过在读什么书吗？` / `我最近常去梨香院听戏` | `ref=['mem_360388…', 'mem_b1036f…']` **非空** ✅ | 答"你前番说在读《西厢记》，还道崔莺莺这人挺有意思"——**含"西厢记"且含"崔莺莺"、无"资料没写"** ✅ |
| **C′** | `--topic "青埂峰"`，**输入与 B′ 逐字相同** | **全空** ✅ | 仍答"资料没写：我这儿没有你先前说过在读什么书的记载" ✅（= 没被污染） |

同一句输入、只改 topic，B′ 答得出、C′ 答不出——**因果单一变量**。与 P6.2-d 的
会话 B（无 topic、答"资料没写"）形成前后对照：**R2 在产品路径从"永不触发"变成
"带 topic 才触发"**。

**④ 账本逐笔（预期 → 实际）**

| 项 | 预期 | 实际 |
| --- | --- | --- |
| 模型 | 6 笔 CHAT | **6 笔** ✅ |
| 检索 | 0 笔 SEARCH | **0 笔** ✅（记忆检索走本地库，不花钱） |
| `task_id` | 全 `null`（D-C） | **全 `null`** ✅ 只记不修 |

**⑤ 费用**：预估 6 × 0.0005 = 0.003 USD → 实际 **0.0025677 USD（0.86×）**，
实测单价 0.000428/次。未触及 1.3 倍停止线。

**⑥ verbatim / 状态**：`content='我在读《西厢记》，觉得崔莺莺这个人挺有意思'`
**一字不差是用户原话**；`namespace=SIMULATED`、`scope=SESSION`、`scope_id=A′`、
`ACTIVE`、`source_turn_id` 指向角色轮。三会话 **全 ENDED**（A′ `rule`、B′/C′ `none`）。
真实库已备份为 `persona.db.bak-p63-<时间戳>`。**未提交 Git**。

**⑦ 测试**：新增 `tests/test_p6_3_topic.py` **23 条**（门禁 9 / `start_session` 3 /
触发与不触发 3 / 不污染 3 / CLI 3 / 端到端 2）。**5 组变异验证实测打红**：
① 门禁恒放行 → 13 红；② 读路径不查门禁 → 1 红（脏 topic 触发 R2）；
③ CLI 不传 topic → 2 红；④ 停用词升序剥 → 2 红；⑤ 改成"出现即拒" → 6 红 1 错。

**未做**：topic 自动提取（需分词器 / 另议）；记忆管理命令；`task_id` 回填（D-C）。

---

### 5.44 P6.4：二期收尾总结（2026-10-22，**二期正式结束**）

#### 5.44.1 二期交付了什么

| 阶段 | 交付 | 真跑规模 | 状态 |
| --- | --- | --- | --- |
| **P6.0** | 五维对话质量评估（A 事实追溯 / B 稳定性**红线** / C 幻觉**红线** / D 角色一致 / E 多轮连贯）+ 判据与 prompt 的三轮根因修复 | 4 + 13 + 7 + 13 + 48 = **85 次** | **Step 2 通过**（自动判据 + 人工复核，§5.32 口径） |
| **P6.1** | 产品温度确认（对话产品路径 = **0.7**，评估仍用 0.0 保历史可比） | 14 次（0.7 下 B 6/6、C 8/8） | 已验收 |
| **P6.2-a** | 确定性记忆写回规则引擎 `memory_writeback.py`（verbatim 不变式 + 签名无"模型回答"参数，结构上堵回声室） | 0（纯离线） | 已验收 |
| **P6.2-b** | `rule` 策略接线进 `run_round` + 评估链路 `none` 硬守卫 + `write_simulated` 去重 | 0 | 已验收 |
| **P6.2-c** | 最小 `persona chat`（第二个会花钱的命令）+ 与 `run` **同一套**授权确认 + Ctrl-C 干净中止 | 0（脚本化 Provider） | 已验收 |
| **P6.2-d** | 记忆写入真跑对账 | **6 次 / 0.0027285 USD** | 逐条对账全对 |
| **P6.3** | `--topic` 跨会话检索入口 + topic 质量门禁 | **6 次 / 0.0025677 USD** | 两层验收全过 |
| **P6.4** | 收尾：README / SKILL.md / RUNNING.md / ROADMAP.md 四份文档对齐 | 0 | 本轮 |

**代码量**：新增 `persona/modules/memory_writeback.py`、`scripts/dialogue_eval.py`、
`scripts/run_dialogue_eval.py`、7 个测试文件；改 `dialogue.py` / `memory.py` /
`errors.py` / `cli/main.py` / `orchestrator/__init__.py` / 2 个既有测试。
**全量测试 1902 → 1935 项**（二期新增 33 条），二期真跑合计 **12 次 / 0.0052962 USD**。

#### 5.44.2 标志性成果：闭环（写进去不会丢，加了入口就能用）

P6.3 会话 A′ 的**第 1 轮**就检索到了 `mem_b1036f…`——那是 **P6.2-d 会话 A** 写下的旧记忆，
两个会话相隔一天、进程不同、命令行参数也不同。这一条把整条链路一次走完：

```
P6.2-d 会话 A 写记忆 → 落库（SIMULATED / SESSION / ACTIVE）
        ↓ 换天、换进程
P6.3  会话 A′ 带 --topic 开会话 → 第 1 轮 R2 命中 → referenced_memory_ids 非空
        ↓
P6.3  会话 B′ 同 topic 问"你还记得我说过在读什么书吗？" → 答出"西厢记/崔莺莺"
P6.3  会话 C′ 换 topic"青埂峰"（输入逐字相同）→ 检索为空、仍拒答（没被污染）
```

比预期更好的地方在于：原计划只验"带 topic 能取到"，实际顺带证明了
**「写入 → 持久化 → 跨进程 → 跨会话 → 被模型用上」整条链是通的**，
而且 C′ 证明这个"记得住"是**有条件**的——条件不满足就取不到，不会把无关记忆塞进上下文。

#### 5.44.3 已知局限（**已定位、未修**，二期不修，留给阶段三或更后）

| # | 局限 | 影响 | 出处 |
| --- | --- | --- | --- |
| 1 | **C 维度只验"该拒答时拒答"**，不验"没拒答时说得对不对" | 五维之外的**结构性缺口**：模型没拒答但答错了，判不出来 | §5.38 |
| 2 | **判据是字面规则、会脆**：B1 / B2 / E1 都出现过误判（模型正确但被判 FAIL） | 自动判据只是"第一道筛子"，**必须人工复核**（§5.32 已把"Step 2 通过 = 自动 + 人工"写死） | §5.30 / §5.32 / §5.35 |
| 3 | 引用"B/C 100%"**必须注明是一次抽样**（0.0 下 13 次 / 48 次，0.7 下 14 次） | 不是分布保证，不能外推成"永远 100%" | §5.38 |
| 4 | **R2 跨会话必须显式 `--topic`**，没有自动提取 | 用户不给就**只记得本会话**；自动提取需中文分词器（不装依赖）且属启发式默认值那一族 | §5.43 |
| 5 | **规则召回有限**：13 句探针命中 7 句 | 缺口集中在第一人称自述以外、未来计划、纯情绪 | `P6_2_MEMORY_WRITE_PLAN.md` §11 |
| 6 | **`_assemble_context` 只注入 `traits[:5]`**，`single_events` / `habits` / `experiences` 全不进 prompt | 模型看不到"单次行为"，**稳定性维度的评估可能是假通过** | §5.20 附近（看代码确认的三个洞之一） |
| 7 | **`referenced_persona_item_ids` 装的是 entity_id**，不是条目 ID / 证据 ID | "可追溯到证据"目前只是**字段名**，尚未真正指到条目 | 同上 |
| 8 | **没有记忆管理命令**：`persona memory` 仍是占位 | 写进去的模拟记忆**没法从 CLI 查看或清理**，只能直接读库 | 本期明确不做 |
| 9 | **`chat` 的 `external_usage_records.task_id` 恒 `null`**（D-C 在 chat 路径的又一次出现） | `chat` 的调用**无法按任务对账**；要查只能走 `usage_records.scope` | §5.42 / §5.43 |
| 10 | `PERSONA_MODEL_MAX_CALLS` **只数本次进程**，默认 3 | 会拦住 `--rounds 4` 的对话；临时抬高用进程级环境变量，别改 `.env` | §5.42 |
| 11 | `persona status` 显示模型调用上限 `None`；`.env` 不吃行内注释 | 显示/解析问题，闸门本身照常生效 | §5.19 |
| 12 | **不做**：流式输出、多用户身份、topic 自动提取、`end_session → Memory.consolidate`、FTS5 / 向量检索 | 功能边界，不是缺陷 | 本期已拍板 |

#### 5.44.4 两条过程教训（**下次直接照做，别再踩**）

**① 新增 `ErrorCode` 必须同时注册进 `ERROR_SPEC`**

`persona/core/errors.py` 里 `new_error()` 是 `ERROR_SPEC[code]` **下标取值**，
不是 `dict.get(code, 默认)`。只加枚举成员、不注册 → 运行时 **KeyError**，
而且是在**最不该崩的时候**崩（校验失败正要报错，结果自己先炸）。
P6.3 一次加了 3 个码（`VALIDATION_TOPIC_TOO_{BROAD,SHORT,LONG}`），**枚举 + ERROR_SPEC 成对改**。

**② 变异验证不能用「进程内 monkeypatch」，必须「文件级改写 + 子进程跑」**

两个坑叠在一起，都会让结论失真：

- 测试模块在 **import 时就把符号绑走**了，进程里改 `dialogue.topic_is_retrievable` **不影响**
  已经绑好的引用 → 变异看起来"没生效"，误判成测试无效；
- 更隐蔽的是**首次 import 若发生在变异之后**，被污染的实现会留在内存里，
  **还原文件后基线仍然假红**（P6.3 实测过：还原后仍有 13 条红，是假红）。

正确做法：改文件 → `subprocess.run([sys.executable, "-m", "unittest", ...])`
→ 汇总 `stderr`（unittest 的汇总输出在 **stderr**，不在 stdout）→ `finally` 还原。

> 附带一条同族的坑（§5.41 已记）：**写脚本化 Provider 桩必须给 `tokens_in/tokens_out`**，
> 不给 → 账本记"未知笔"，累计到上限后第 2 轮就 `BUDGET_EXCEEDED`（**桩失真，不是产品缺陷**）。

#### 5.44.5 阶段三（产品化）从这里起步

二期结束时，**"能用"已经成立**：建档案（`run`）→ 看档案（四条只读命令）→ 对话（`chat`）
→ 记住（`--memory-write rule` + `--topic`）→ 有质量评估（五维 + `--mock` 自检）。
下一步该做的是"**让人用起来**"，候选见提交后的二期总结报告（记忆管理命令 / 灰度反馈 /
`GRAY_RELEASE.md` 同步 / 阶段三清单）。

---

## 6. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-21 | v0.26 | **P6.2-c：最小 `persona chat`**（第二个会花钱的命令）：`--names/--rounds/--message/--memory-write rule\|off`（默认 `off`）；`run` 与 `chat` 共用唯一实现 `_authorize_real_calls`（同一个 `.persona_consent.json`、同一套问法）；对话单价常量 `DIALOGUE_MEASURED_COST_PER_CALL_USD` 收进 `modules/dialogue.py`（评估脚本与 CLI 同一出处）；Ctrl-C 干净中止（记忆不回滚 / 会话必置 ENDED / 退出码 130）；为守 D-059，`SessionDraft` 与策略常量经 `orchestrator` 转发。新增 `tests/test_p6_2_chat_cli.py`（10 项，含 3 组变异验证）；全量 **1902 → 1912 项**通过；真实外呼 0 | 待确认 |
| 2026-10-21 | v0.27 | **P6.2-d 真跑对账（6 次调用 / 0.0027285 USD）**：会话 A 四句逐条对账全部符合规则预期（R-EPI / R-REL 各 1 条，寒暄与问句不记）；verbatim 不变式成立（`content` 是用户原话完整子串）；账本 6 笔 CHAT = 会话 A 4 + 会话 B 2，检索 0 笔，与预期逐笔对上（`task_id` 全 null 是 D-C 的又一次印证）；**R1 本会话检索已在产品路径生效**（会话 A 第 3 轮起 `referenced_memory_ids` 非空），**R2 跨会话在产品路径永不触发**（`chat` 无 `--topic`，`topic` 恒 None）→分层结论：存储层/端口层能读到，prompt 注入层读不到，留给 P6.3；实际费用 0.91× 预估（未超 1.3 倍停止线）；`PERSONA_MODEL_MAX_CALLS=3` 会拦住 4 轮会话（用进程级环境变量覆盖，未改 `.env`）。真实库已备份 | 已验收 |
| 2026-10-22 | v0.28 | **P6.3：`--topic` 跨会话检索入口**（补齐 R2 缺失的那一环）：显式 `--topic`（本期不做自动提取）；门禁 `topic_is_retrievable()` **一个纯函数、两处用法**（`start_session` Err / `_retrieve_memories` 跳过 R2 / CLI 花钱前拦）；停用词按**长度降序剥离**后判空（`我们的西厢记` 放行、`我` 拒），检索用原值不改写；新增 3 个错误码 `VALIDATION_TOPIC_TOO_{BROAD,SHORT,LONG}`（必须注册进 `ERROR_SPEC`）。真跑 A′→B′→C′ **6 次调用 / 0.0025677 USD（0.86× 预估）**：B′ 两层全过（`ref` 非空 + 答出"西厢记/崔莺莺"且无"资料没写"），C′ 全空仍拒答（不污染）；账本 6 笔 CHAT + 0 笔 SEARCH，`task_id` 全 null（D-C）。新增 `tests/test_p6_3_topic.py` 23 条 + **5 组变异验证**；全量 **1912 → 1935 项**通过；真实外呼 6 次（授权内） | 已验收 |
| 2026-10-22 | v0.29 | **P6.4 二期收尾（只改文档）**：README 新增「跑一场对话」章节（`chat` / `--memory-write` / `--topic` 的参数、记忆写入四条边界、跨会话检索条件），并修掉「`persona run` 是唯一会花钱的命令」这句已不成立的表述；`SKILL.md` 加入对话的四步跑通 + 三条新边界 + 明确「没有记忆管理命令」；`RUNNING.md` §2 CLI 表补 `chat` 行（退出码含 **130**）、新增 **§5.44 二期总结**（交付清单 / **标志性成果：A′ 第 1 轮就命中 P6.2-d 旧记忆的完整闭环** / 12 条已知局限 / 两条过程教训：错误码必须注册 `ERROR_SPEC`、变异验证必须文件级改写 + 子进程跑）；`ROADMAP.md` P6 标为完成并新增 §8 P6 进度记账。二期真跑合计 **12 次 / 0.0052962 USD**；全量 **1935 项**通过 | 待提交 |
| 2026-10-15 | v0.4 | **G-4 实现**：新增 `persona/modules/relationship_analysis.py`（可注入 `RelationshipAnalyzerPort` + `RuleRelationshipAnalyzer` 离线规则 + `MockRelationshipAnalyzer` 查表 + `ProviderRelationshipAnalyzer` 契约占位）；`BusinessOrchestrator._step_relationship` 注入分析器后做基于证据的关系语义研判并落盘；闭合 RUNNING §5.12 缺口 G-4 / G-5，AC-2 转为"已满足（注入分析器后，规则非语义模型）"；新增 `tests/test_relationship_analysis.py`（21 项）与 `test_business_orchestrator.py` G-4 用例（7 项）；全量 722 项测试通过 |
| 2026-10-13 | v0.1 | P1.2-B 初稿：运行方式、CLI 可用命令、目录说明、未实现清单、待确认项 |
| 2026-10-13 | v0.2 | P2.1 更新：统一测试命令（D-069）；补记实测通过版本 3.12.10 / 3.13.14 / 3.14.7；新增 §1.5 工程配置（`pyproject.toml` / `.gitignore`）与覆盖检查表；§4、§5 更新为 R-1~R-6 已拍板结论；新增 §5.1 仍未定问题（U-1 未初始化仓库 / U-2 Q-27） |
| 2026-10-13 | v0.3 | P2.2 更新：CLI 表新增 `config show` / `config check`；§1.5 补 `.env.example` 与 `.env` 说明并指向 `docs/CONFIG.md`；§5.1 标记 U-1（已 git init 并提交）、U-2（D-073）、U-3（D-074~D-076）**已解决**，新增 C-1~C-4 遗留问题 |
| 2026-10-13 | v0.4 | P2.3 更新：测试数 135 → **172**（新增 `test_task_service.py` / `test_task_budget.py`）；CLI 表新增 `task create/show/list/start/run/pause/resume/cancel/sweep` 与 `status|pause|resume|cancel` 快捷方式，并记录**预算不足再恢复不再多花**的实测示例；§3 补 `persona/config` 与 orchestrator 的 P2.3 说明；§4 更新"已做/未做"；§5.1 标记 C-1~C-4 已拍板 |
| 2026-10-13 | v0.5 | P2.3 验收通过（D-087~D-094）；§5.2 的 T-1~T-7 全部拍板（→ D-095~D-101，逐条结论见表内"决定编号"列）；测试数 172 → **187**（新增 `test_task_faults.py`：15 项故障注入测试，覆盖检查点保存失败 / 数据库操作失败 / 处理器抛异常 / 重复提交 / 重复恢复五类场景）；新增 §5.3 可靠性边界记录 |
| 2026-10-13 | v0.6 | P2 证据管线第一步（用户称 P3.1，编号差异见 DECISIONS §3 Q-37）：新增 `persona/modules/evidence.py`（EvidenceService：来源/事件/证据的登记、查询、持久化、校验、追溯、去重、冲突、稳定性；模拟隔离双层闸门；推断强制 SYSTEM_DERIVED）与 `tests/test_evidence_module.py`（89 项）；测试数 187 → **276**（3.13.14 / 3.14.7 实测全过）；新增 §5.4 实现清单与遗留问题 E-1~E-5；§1 注明 3.12.10 为既往实测、本轮未能复跑，并澄清托管目录名 3.13.12 与实际版本 3.13.14 的差异 |
| 2026-10-14 | v0.7 | P3 第一步（用户称 P3.2，编号差异见 DECISIONS §3 Q-37）：新增 `persona/modules/persona.py`（PersonaService：创建/修订/查询/版本 diff/稳定性闸门；证据关联与追溯；不确定性表达；冲突关联不裁决；虚构版本边界；模拟隔离）与 `tests/test_persona_module.py`（71 项）；测试数 276 → **347**（3.13.14 / 3.14.7 实测全过）；新增 §5.5 实现清单与遗留问题 F-1~F-5；§1 补耗时说明 |
| 2026-10-14 | v0.8 | P3 第二步（用户称 P3.3，编号差异见 DECISIONS §3 Q-37）：新增 `persona/modules/relationship.py`（RelationshipService：创建/修订/查询/版本 diff/模式闸门；双向视角不合并；共同经历与时间变化；群体互动 ≥3 人不拆两人档；RL-V6 证据直达；虚构版本反合并；模拟隔离；RL-V2 亲密度双重扫描）与 `tests/test_relationship_module.py`（100 项）；`persona/core/enums.py` 补 `Cardinality`、`persona/core/errors.py` 补 `group_dynamics_missing`（待确认，E-6）；测试数 347 → **447**（3.13.14 / 3.14.7 实测全过）；新增 §5.6 实现清单与遗留问题 G-1~G-5 + E-6；§1 计数与耗时同步 |
| 2026-10-14 | v0.9 | P3.3（关系档案）验收落档：E-6 由"待确认"改为**已批准**（D-129，错误码写入 ERROR_HANDLING §2.2）；G-1~G-5 处置落 DECISIONS §2.19（D-130~D-134：G-1 接受未实现、G-2 接受表达边界记后续待办、G-3 GroupDynamics 结构补入 DATA_MODEL §4.8、G-4 全版本保留、G-5 确认刻意不为）；本轮无代码/测试改动，仅文档 |
| 2026-10-14 | v0.10 | P3.4（对话生成基础模块，用户称 P3.4，编号差异见 DECISIONS Q-37）：新增 `persona/modules/dialogue.py`（`DialogueService`：会话/轮次 CRUD、`least_recently_spoken` 动态发言选择、只读上下文组装、经 Mock Provider 生成、落盘、严格模拟隔离）与 `tests/test_dialogue_module.py`（28 项）；测试数 447 → **475**（3.13.14 / 3.14.7 实测全过；双版本各全量 475 项）；新增 §5.7 实现清单与遗留问题 H-1~H-2 |
| 2026-10-14 | v0.11 | P3.5（长期记忆基础模块，用户称 P3.5，编号差异见 DECISIONS Q-37）：重写 `persona/modules/memory.py`（`MemoryService`：写入/去重/冲突/检索/更新新版本/过期/归档/删除/派生产物生命周期/模拟隔离）与 `tests/test_memory_module.py`（34 项）；补 `enums.py` 的 `DerivedKind`/`DerivedStatus`/`PurgeState`、`errors.py` 的 4 个新错误码；测试数 475 → **509**（3.13.14 / 3.14.7 实测全过；双版本各全量 509 项）；新增 §5.8 实现清单与遗留问题 H-3~H-6；DECISIONS §2.21（D-146~D-159）、Q-37 第五轮；ERROR_HANDLING §2.2 补 4 码 |
| 2026-10-14 | v0.12 | P3.6（对话 ↔ 长期记忆集成，用户称 P3.6，编号差异见 DECISIONS Q-37）：扩展 `persona/modules/dialogue.py`（**新增只读 `MemoryReaderPort` + `DialogueDeps.memory_reader`**；`draft_turn` 按 R1 本会话 / R2 跨会话（含 topic 关键词门槛）检索**仅 SIMULATED + ACTIVE** 记忆并注入 prompt；`commit_turn(..., memory_candidates=...)` 在**同一事务**内 Turn → Memory → Audit 写模拟记忆并回填 `generated_memory_ids`；默认 `memory_write_policy="none"` **绝不自动写**；候选 scope 必须 SESSION）与新增 `tests/test_dialogue_memory_integration.py`（28 项）；测试数 509 → **537**（3.13.14 / 3.14.7 实测全过）；新增 §5.9 调用链 / 检索与写入触发规则 / 契约缺口补齐说明 / 遗留问题 H-7~H-9（+H-1 仍未拍板）；同步 P3.5 验收结论（H-3 已批准、H-4~H-6 已接受） | 已实现，**待人工验收** |
| 2026-10-14 | v0.13 | P3.7（用户称 P5 端到端集成验收，编号差异见 DECISIONS Q-37 第七轮 / §2.22.1 D-190）：`persona/modules/evaluation.py` 从 Protocol-only 扩为 **`EvaluationService`**（`check_invariants` 七项 INV-1~4 检查、`check_dependencies` AC-20、`run_static_checks`、`snapshot`/`dialogue_leaks`、`measure_quality` 结构指标不做主观打分）；新增 `tests/test_integration_acceptance.py`（26 项）；测试数 537 → **563**（3.13.14 / 3.14.7 实测全过）；新增 §5.10（交付 / 实际调用链 / 诚实边界 / 遗留 H-10~H-13）；同步 P3.6 验收结论（H-1 已批准、H-7~H-9 已接受、`MemoryReaderPort` 回填 INTERFACES §8.1） | 已实现，**待人工验收** |
| 2026-10-14 | v0.16 | G-2（来源材料 → 候选 claim 自动抽取）：新增 `persona/modules/extraction.py`（`ClaimExtractorPort` / `CandidateClaim` / `MockClaimExtractor` / `ProviderClaimExtractor`）；`Research` 追加 `materials` 输出（此前 `snippet` 被丢弃是核心数据缺口）；`SourceMaterial` 下沉 `core/types.py`；`BusinessDeps` 新增可注入 `claim_extractor`，`evidence_seeds` 变可选；新增 `tests/test_claim_extraction.py`（28 项）+ 业务编排器 G-2 用例（18 项）；测试数 648 → **694**（3.13.14 / 3.14.7 实测全过）；新增 §5.13；**AC-1 由「部分满足」转为已满足（Mock 链路，不等于真实网络研究已过）**；新登记缺口 G-6（材料无法落检查点）；H-17（正文抓取）仍开 | 已实现，**待人工验收** |
| 2026-10-14 | v0.15 | H-10A（业务编排型 Orchestrator，用户称 H-10A，编号差异见 DECISIONS Q-37 第九轮 / §2.24）：新增 `persona/orchestrator/business.py`（**`BusinessOrchestrator`**，实现 `UnitHandler` 接入既有 `TaskRunner`，打通 Research → Evidence → Persona → Relationship → Dialogue）；既有任务 Orchestrator 一行未改；新增 `tests/test_business_orchestrator.py`（37 项）；测试数 611 → **648**（3.13.14 / 3.14.7 实测全过）；新增 §5.12（调用链 / 调用方式 / 结果三态 / AC-1·AC-2 核对 / 遗留 G-1~G-5 / 已知限制）；`INTERFACES §12.7` 登记契约（v0.1.5） | 已实现，**待人工验收** |
| 2026-10-14 | v0.14 | H-11A（Research 最小可运行实现，用户称 H-11A，编号差异见 DECISIONS Q-37 第八轮 / §2.23）：`persona/modules/research.py` 由 Protocol-only 推进为 **`ResearchService`**（确定性检索计划 / 经 Provider.search 检索 / 过滤排序 / 经 Evidence 登记 / run 聚合），新增独立 **`SourceRegistrarPort`（仅 `register_source`，堵死 F-10）**与 `ResearchDeps`；新增 `tests/test_research_module.py`（48 项）；测试数 563 → **611**（3.13.14 / 3.14.7 实测全过）；新增 §5.11（交付 / 调用方式与 IO / 与 Evidence 衔接 / 追溯与隔离 / 契约取舍 / 遗留 H-14~H-17 / 接真实研究服务的条件）；`INTERFACES §5.3` 回填实现状态（v0.1.4） | 已实现，**待人工验收** |
| 2026-10-17 | v0.18 | 新增 §5.15：**G11 登记**——`ExternalUsageLedger` 纯内存、无落盘，**跨进程累计费用闸门不成立**；明确 0.25 USD 是"每次进程"上限而非累计上限；给出 C 轮之后的人工累计记账规则（不重置、新累计 = 上轮累计 + 本轮 `cost_confirmed`、基线 0.0012765 USD、未确认笔数按估算值计入并标注）；声明系统级累计闸门**另立轮次**、不混进冒烟 | 待确认 |
| 2026-10-17 | v0.19 | **P4.6 三项落地**：① `persona/orchestrator/assembly.py`（composition root：`build_business_stack` / `register_targets`），CLI 新增 `persona business plan|run`（业务链进 CLI，D-A）；② G11 闭合——账本 `hydrate()` + `usage_sink` 接线 + **两层上限**（单任务 `max_cost` / 项目累计 `max_cost_total`，D-B/D-C）；③ 关系分析器默认装配（D-D）且失败**降级**留告警（D-E），新增配置 `PERSONA_RELATIONSHIP_ANALYZER=rule|off`。新增 `tests/test_p4_6_assembly_ledger.py`（32 项）与分析器降级用例（2 项） | 待确认 |
| 2026-10-17 | v0.20 | **P4.6 阶段三**：提交 `6efa1b7`；**1 次真实 POST** 跑通五阶段（16 证据 / 2 人格 / 1 关系，confirmed **0.0033216 USD**）；**C 轮 0.0032838 确认从未落库**（3 个临时库均 0 行），改以两笔人工补记入账，人工累计与库内合计首次**逐位相等（0.0078819）**；§5.15 增补"补记 + 不变量"小节；新增 `P4_6_REAL_RUN_AND_BOUNDARY.md`（真实 vs Stub 边界表）与 `PLAN_P4_7_P1A.md`（P1a 方案 + 缺口审计） | 待确认 |
| 2026-10-17 | v0.22 | **P4.7 验收 + 整改**：① 装配层**默认禁止真实外呼**（`build_business_stack(allow_real=False)`，未授权时记 `REAL_PROVIDER_BLOCKED` 且不构造；CLI `business run --allow-real` 才授权，见 §5.15 规则登记 / D-245）；② 新增 `tests/test_p4_7_real_call_gate.py`（9 项，含"默认不碰网络"的 `urlopen` 断言替身）；③ 新增 §5.16 **G12 登记**（跨源同句 ⇒ `traits` 两条同文本条目，用户拍板单独立轮）；④ 人工累计确认 **0.0097980 USD**（含 §5.15 那次未授权调用，**不重置**） | 待确认 |
| 2026-10-17 | v0.23 | **P4.8：R1 + R3（均零成本）**。① **G12 已修**（方案 A）：档案条目身份 = `pi_<sha256(维度\|归一化说法\|主体集合)[:16]>`，跨源同句合并成一个条目、`evidence_refs` 收全部；background/expression 重复文本只留一次（引用不丢）；旧档案不做 id 迁移。改 **3 处**断言（P1a 1 处、e2e 2 处），新增 2 项边界用例（P1a 共 8 项）。② **Tavily 接线**：`search_provider_from_settings`（`max_retries` 强制 0）＋装配层 `build_search_provider` 同受 `allow_real` 管（§5.17）。③ 顺手修 `research.*` 配置在产品路径被忽略的缺口。④ `plan_calls()`：执行前打印检索/抓取/模型调用次数与估算。新增 `tests/test_p4_8_search_wiring.py`（20 项）；全量 **1301 项**通过 | 待确认 |
| 2026-10-17 | v0.24 | **P4.8 R4：首次真实检索 e2e**（用户授权：2 检索 + ≤1 抓取 + 3 模型）。实际 1 检索成功 + 1 检索超时 + 1 抓取 + 1 模型，清单外调用 0。新增 `scripts/e2e_p4_8_real_search.py`（`--dry` 物理断网 / 真实运行留痕 / `--report-only` 只读重放）与 `docs/P4_8_R4_REAL_SEARCH.md`。**修 D-1**（作品名没进检索词，+4 项测试）；登记 **D-2 过滤无相关性判定**、**D-3 对话调用未进逐笔账本**、**D-4 对话次数口径偏大**（§5.18）。唯一配置改动：`.env` 的 `PERSONA_MODEL_MAX_CALLS` 1→3。全量 **1305 项**通过 | 待确认 |
| 2026-10-19 | v0.25 | **P5.1 CLI 收口**：新增只读层 `orchestrator/readmodels.py`、渲染层 `cli/render.py`、授权确认 `cli/consent.py`，CLI 补齐 `run/show/show-relationship/evidence/export` 五命令（含 `run` 的清单 + 授权流程 + `--dry` 零写入）；`assembly.py` 修 **D-A**（对话拿不到真实 Provider，恒用 `FakeProvider` ⇒ 真跑必 FAILED）与 **D-B**（`extraction_strategy` 默认 `None` ⇒ 产品路径从不抓正文，`PERSONA_RESEARCH_MAX_EXTRACTS` 是死配置、清单在说假账）；清单改为**只打装配事实**、不许照配置猜。**两次受控真跑**（合计 ≈0.0166 USD + 3.0 credits，均在上限 0.05 内）：第二次 5/5 阶段全绿、验收 8/9。真跑暴露并修掉 **D-D**（`locator.field` 硬编码 `snippet`，正文证据不可回溯）。登记未修项：**D-C**（`task_id` 进不了账单表）与 §5.19 两小项（`.env` 不吃行内注释、`status` 上限显示 `None`）。新增 `docs/P5_1_CLI_PLAN.md` / `docs/P5_1_REAL_RUN.md` 与 6 个测试文件；全量 **1550 → 1725 项**通过 | 待确认 |
