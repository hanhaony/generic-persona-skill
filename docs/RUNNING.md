# RUNNING —— 怎么把代码跑起来（P2.1 更新）

- **版本**：v0.3（P2.2 阶段）
- **适用**：Python **3.12+**。本次**实测通过**的三个版本：**3.12.10 / 3.13.14 / 3.14.7**
- **依赖**：**零第三方依赖**。只用标准库 `unittest` / `sqlite3` / `dataclasses` / `argparse`
- **网络**：**不需要**。Provider 全是 Mock，不需要 API Key；测试里有"离线护栏"断言
- **安装**：**不需要安装**。`python -m` 直接跑；`pyproject.toml` 只是元信息声明（D-072）

> 本文件只记录**实际执行过**的命令与结果。未验证过的命令不写进来。

---

## 1. 统一命令（P2.1 定稿，D-069）

在本项目根目录（`D:\AIprojects\generic-persona-skill`）下执行：

```bash
# ① 统一测试命令（89 项）—— 这是唯一推荐入口
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

三个版本结果一致：`Ran 89 tests ... OK`（**89 通过 / 0 失败 / 0 错误**）。

> 备选写法 `python -m unittest discover`（不带参数）同样可用，同样 89 项通过；
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
| `persona new / status / resume / pause / cancel / chat / sources / memory / eval` | **占位**：明确打印"尚未实现" | **2** |

> 占位命令**故意返回退出码 2 并说明"尚未实现"**，不假装能用。
> `selftest` 走的是**临时目录**里的数据库，不会碰任何项目数据。

---

## 3. 目录说明（每个包负责什么）

| 包 | 职责 | 现在有没有业务逻辑 |
| --- | --- | --- |
| `persona/core` | 枚举、ID、时间、错误码、`Result` 信封、`CallContext`、通用 DTO | ❌ 只有类型与规则 |
| `persona/storage` | SQLite 建表、CRUD、软删除、乐观锁、引用检查、检查点、用量、审计 | ❌ 只做存储，不做业务判断 |
| `persona/providers` | 外部服务接口 + `FakeProvider` / `RecordingProvider` / `FailingProvider` / `OfflineGuardProvider` | ❌ 全是 Mock |
| `persona/orchestrator` | 六态状态机（`PENDING→RUNNING→PAUSED→…`）+ 检查点结构 | ❌ **没有任务引擎**（不实现 `run_step` / `resume` 执行流程） |
| `persona/modules` | 7 个业务模块的 **Protocol 端口声明** | ❌ 只有"插头图纸"，方法体是 `...` |
| `persona/cli` | argparse 入口 | ❌ 不含业务逻辑 |

测试在 `tests/`，共 6 个文件：

| 文件 | 测什么 |
| --- | --- |
| `test_imports.py` | 29 个模块都能导入 |
| `test_core_contracts.py` | `Result` / 错误码 / ID / **命名空间闸门** / 枚举取值 / 上下文 |
| `test_task_state.py` | 六态转换表、暂停≠取消、检查点幂等与幂等键 |
| `test_storage_sqlite.py` | 临时库建表、读写往返、分页、乐观锁、软删除、引用检查、检查点、用量、**隔离闸门拦截** |
| `test_provider_mock.py` | Fake 确定性、Recording 回放、Failing 注入错误码、**离线护栏** |
| `test_dependency_rules.py` | 端口签名（F-2/F-3/D-057）、导入方向（F-7/F-8/F-12）、无厂商 SDK、无真实人物姓名 |
| `test_cli.py` | 帮助、版本、info、selftest、占位命令诚实返回"尚未实现" |

---

## 4. 本阶段**明确没有做**的事

按用户指令"不扩大范围"，以下**尚未实现**（不是遗漏，是刻意留到后面阶段）：

1. **业务逻辑全未实现**：研究、证据提取/分级/去重/冲突、人物蒸馏、关系蒸馏、对话生成、长期记忆检索。
2. **任务引擎未实现**：只有状态机与检查点数据结构，**没有** `run_step` / `run_to_completion` / `resume` 的执行流程，也没有预算四段式闸门的执行代码。
3. **SQLite 不是最终表结构**：现在只有 4 张骨架表（`records` / `usage_records` / `audit_records` / `checkpoints`）。
   13 个业务实体**暂以 JSON 载荷**存在 `records.payload` 里，通用字段（`id`/`namespace`/`created_by`/`deleted_at`/`rev`）独立成列。
   **已拍板（D-062）**：这是被接受的临时方案，**必须在 P2 结束前**按 `DATA_MODEL.md` 迁移到 13 张正式表
   （`schema_version` 递增 + 显式迁移脚本，**禁止自动删表**）。
4. **FTS5 关键词索引未建**：记忆检索属于 P4（Q-30），本阶段不需要。
5. **`fixtures/` 合成测试素材未创建**：`ROADMAP.md` §3B 计划内。**已拍板（D-066）**：**P2 开工前必须补齐**。
6. **`run_static_checks` 独立脚本未创建**：目前以 `tests/test_dependency_rules.py` 承担最关键的几条静态检查。
   **已拍板（D-066）**：与 `fixtures/` 一起在 **P2 开工前补齐**。
7. **任务引擎未实现**（`run_step` / 预算四段式闸门执行 / `resume` 执行流程）。
   **已确认（D-067）**：这**符合 P1.2-B 指令范围，不是缺陷**，在 P2 实现。
7. **Orchestrator / Evaluation 模块只有协议或状态机**：`Orchestrator` 未实现编排，`Evaluation` 只有 `Protocol`。

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
| C-1 | 新增错误码 `VALIDATION.config_invalid` | 已写入 `errors.py` 与 `ERROR_HANDLING.md` §2.2，**待人工确认** | 影响配置类报错的口径 |
| C-2 | `PERSONA_SEARCH_API_KEY` 何时必填 | 暂为可选，等 P2 研究管线确定搜索 Provider 后再定 | P2 |
| C-3 | 是否支持项目级配置文件（如 `persona.toml`） | 暂不支持 | 有需要再提 |
| C-4 | 日志落地（`log_level` 目前只存不用） | 等 P2 有真实日志需求再接 | P2 |

---

## 6. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | P1.2-B 初稿：运行方式、CLI 可用命令、目录说明、未实现清单、待确认项 |
| 2026-10-13 | v0.2 | P2.1 更新：统一测试命令（D-069）；补记实测通过版本 3.12.10 / 3.13.14 / 3.14.7；新增 §1.5 工程配置（`pyproject.toml` / `.gitignore`）与覆盖检查表；§4、§5 更新为 R-1~R-6 已拍板结论；新增 §5.1 仍未定问题（U-1 未初始化仓库 / U-2 Q-27） |
| 2026-10-13 | v0.3 | P2.2 更新：CLI 表新增 `config show` / `config check`；§1.5 补 `.env.example` 与 `.env` 说明并指向 `docs/CONFIG.md`；§5.1 标记 U-1（已 git init 并提交）、U-2（D-073）、U-3（D-074~D-076）**已解决**，新增 C-1~C-4 遗留问题 |
