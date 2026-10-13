# RUNNING —— 怎么把代码跑起来（P2.3 更新）

- **版本**：v0.4（P2.3 阶段）
- **适用**：Python **3.12+**。本次**实测通过**的三个版本：**3.12.10 / 3.13.14 / 3.14.7**
- **依赖**：**零第三方依赖**。只用标准库 `unittest` / `sqlite3` / `dataclasses` / `argparse`
- **网络**：**不需要**。Provider 全是 Mock，不需要 API Key；测试里有"离线护栏"断言
- **安装**：**不需要安装**。`python -m` 直接跑；`pyproject.toml` 只是元信息声明（D-072）

> 本文件只记录**实际执行过**的命令与结果。未验证过的命令不写进来。

---

## 1. 统一命令（P2.1 定稿，D-069）

在本项目根目录（`D:\AIprojects\generic-persona-skill`）下执行：

```bash
# ① 统一测试命令（172 项）—— 这是唯一推荐入口
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

Windows（PowerShell / Git Bash）同理，把 `python` 换成解释器路径即可。本次**实测**过的三个解释器：

```bash
# Python 3.12.10（满足 requires-python 下限，重点保证对象）
"C:/Users/36135/AppData/Local/Programs/Python/Python312/python.exe" -m unittest discover -s tests -t .

# Python 3.13.14
"C:/Users/36135/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m unittest discover -s tests -t .

# Python 3.14.7
"C:/Users/36135/AppData/Local/Programs/Python/Python314/python.exe" -m unittest discover -s tests -t .
```

三个版本结果一致：`Ran 172 tests ... OK`（**172 通过 / 0 失败 / 0 错误**）。

> 备选写法 `python -m unittest discover`（不带参数）同样可用，同样 172 项通过；
> 但**统一用带 `-s tests -t .` 的写法**，避免将来根目录出现 `test*.py` 时误收。

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
| `persona/modules` | 7 个业务模块的 **Protocol 端口声明** | ❌ 只有"插头图纸"，方法体是 `...` |
| `persona/cli` | argparse 入口 | ❌ 不含业务逻辑（只调 `TaskService` / `Storage` 只读） |

测试在 `tests/`，共 6 个文件：

| 文件 | 测什么 |
| --- | --- |
| `test_imports.py` | 29 个模块都能导入 |
| `test_core_contracts.py` | `Result` / 错误码 / ID / **命名空间闸门** / 枚举取值 / 上下文 |
| `test_task_state.py` | 六态转换表、暂停≠取消、检查点幂等与幂等键 |
| `test_task_service.py`（P2.3） | 创建/查询/持久化、**非法转换不改状态**、暂停≠取消≠失败、**重启后读回**（含**真实子进程**）、`sweep_interrupted`、TS-V4 幂等去重 |
| `test_task_budget.py`（P2.3） | 四段式闸门、未知 token 不估算、80% 只告警、**预算不足暂停而非失败**、提额后续跑**不重复执行**、跳过已完成单元、可重试/不可重试 |
| `test_storage_sqlite.py` | 临时库建表、读写往返、分页、乐观锁、软删除、引用检查、检查点、用量、**隔离闸门拦截** |
| `test_provider_mock.py` | Fake 确定性、Recording 回放、Failing 注入错误码、**离线护栏** |
| `test_dependency_rules.py` | 端口签名（F-2/F-3/D-057）、导入方向（F-7/F-8/F-12）、无厂商 SDK、无真实人物姓名 |
| `test_cli.py` | 帮助、版本、info、selftest、占位命令诚实返回"尚未实现" |

---

## 4. 本阶段**明确没有做**的事

按用户指令"不扩大范围"，以下**尚未实现**（不是遗漏，是刻意留到后面阶段）：

1. **业务逻辑全未实现**：研究、证据提取/分级/去重/冲突、人物蒸馏、关系蒸馏、对话生成、长期记忆检索。
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

---

## 6. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | P1.2-B 初稿：运行方式、CLI 可用命令、目录说明、未实现清单、待确认项 |
| 2026-10-13 | v0.2 | P2.1 更新：统一测试命令（D-069）；补记实测通过版本 3.12.10 / 3.13.14 / 3.14.7；新增 §1.5 工程配置（`pyproject.toml` / `.gitignore`）与覆盖检查表；§4、§5 更新为 R-1~R-6 已拍板结论；新增 §5.1 仍未定问题（U-1 未初始化仓库 / U-2 Q-27） |
| 2026-10-13 | v0.3 | P2.2 更新：CLI 表新增 `config show` / `config check`；§1.5 补 `.env.example` 与 `.env` 说明并指向 `docs/CONFIG.md`；§5.1 标记 U-1（已 git init 并提交）、U-2（D-073）、U-3（D-074~D-076）**已解决**，新增 C-1~C-4 遗留问题 |
| 2026-10-13 | v0.4 | P2.3 更新：测试数 135 → **172**（新增 `test_task_service.py` / `test_task_budget.py`）；CLI 表新增 `task create/show/list/start/run/pause/resume/cancel/sweep` 与 `status|pause|resume|cancel` 快捷方式，并记录**预算不足再恢复不再多花**的实测示例；§3 补 `persona/config` 与 orchestrator 的 P2.3 说明；§4 更新"已做/未做"；§5.1 标记 C-1~C-4 已拍板 |
| 2026-10-13 | v0.5 | P2.3 验收通过（D-087~D-094）；§5.2 的 T-1~T-7 全部拍板（→ D-095~D-101，逐条结论见表内"决定编号"列）；测试数 172 → **209**（新增 `test_task_faults.py`：检查点保存失败 / 数据库操作失败 / 处理器抛异常 / 重复提交 / 重复恢复五类故障注入） |
