# RUNNING —— 怎么把代码跑起来（P3 第二步更新）

- **版本**：v0.16（G-2：来源材料 → 候选 claim 的自动抽取）
- **适用**：Python **3.12+**。本轮**实测通过**：**3.13.14 / 3.14.7**（各 694 项）；
  3.12.10 为既往轮次实测（当前开发环境未安装 3.12 解释器，本轮未能复跑，特此注明）。
  ⚠️ 托管解释器的**安装目录名是 `3.13.12`，实际版本是 3.13.14**（`python --version` 实测），以实际版本为准
- **依赖**：**零第三方依赖**。只用标准库 `unittest` / `sqlite3` / `dataclasses` / `argparse`
- **网络**：**不需要**。Provider 全是 Mock，不需要 API Key；测试里有"离线护栏"断言
- **安装**：**不需要安装**。`python -m` 直接跑；`pyproject.toml` 只是元信息声明（D-072）

> 本文件只记录**实际执行过**的命令与结果。未验证过的命令不写进来。

---

## 1. 统一命令（P2.1 定稿，D-069）

在本项目根目录（`D:\AIprojects\generic-persona-skill`）下执行：

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
# Python 3.13.14（托管；安装目录名为 3.13.12，实际版本以 --version 为准）
"C:/Users/36135/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m unittest discover -s tests -t .

# Python 3.14.7（系统）
"C:/Users/36135/AppData/Local/Programs/Python/Python314/python.exe" -m unittest discover -s tests -t .
```

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
| `persona new / chat / sources / memory / eval` | **占位**：明确打印"尚未实现" | **2** |

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
- `persona chat` / `new` / `sources` / `memory` / `eval` 仍是**诚实的占位**（明确报"尚未实现"）。

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

## 6. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
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
