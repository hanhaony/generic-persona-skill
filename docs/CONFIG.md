# CONFIG —— 配置项、环境变量与密钥管理

- **版本**：v0.1（P2.2 阶段）
- **适用**：Python 3.12+，零第三方依赖
- **相关文档**：`DECISIONS.md` §2.12（D-077 ~ D-082）、`INTERFACES.md` §2.5、`ERROR_HANDLING.md` §2.2、`RUNNING.md`

---

## 1. 三句话讲清楚

1. **只有一个入口**：配置全在 `persona.config`。**其他任何模块都不许自己 `os.getenv`**（D-077）。
2. **分两类**：非敏感配置（模式、目录、预算、日志、重试……）可以打印、可以写进 `.env`；
   **敏感配置（API Key）只能来自环境变量**，不进文件、不进日志、不进数据库、不进 Git（D-078）。
3. **出错不回显密钥**：配置报错只说"哪个变量、是缺失还是格式不对"，**一个字符的值都不显示**（D-081）。

给初学者的比喻：配置像"点菜"，`AppSettings` 是最终菜单；`Secrets` 是收银台里的支付密码，
菜单上只写"已设置/未设置"，密码本身谁也不给看。

---

## 2. 优先级（D-080）

**由低到高**：

| 层 | 来源 | 用途 | 能不能覆盖上一层 |
| --- | --- | --- | --- |
| 1 | **内置默认值** | 保证"什么都不配也能跑" | — |
| 2 | **`.env` 文件**（项目根目录） | 本地个人设置，不入库 | 能覆盖默认值 |
| 3 | **进程环境变量** | CI、临时覆盖、敏感配置 | 能覆盖 `.env` |
| 4 | **显式 `overrides`** | 仅供测试与程序内部使用 | 能覆盖环境变量 |

> 敏感配置**例外**：不从 `.env` 读、不从 `overrides` 读，**只认环境变量**（读到也会忽略并告警）。

查看当前生效值与来源：

```bash
python -m persona config show          # 打印每一项 + 来源（default / dotenv / env / explicit）
python -m persona config check         # 只做校验，通过退出码 0，不通过退出码 1
python -m persona config show --no-dotenv   # 忽略 .env，只看默认值与环境变量
```

---

## 3. 非敏感配置项（18 项）

| 环境变量 | 字段 | 类型 | 默认值 | 允许范围 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `PERSONA_MODE` | `mode` | 枚举 | `dev` | `dev` / `test` / `prod` | 运行模式 |
| `PERSONA_LOG_LEVEL` | `log_level` | 枚举 | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | 日志级别 |
| `PERSONA_DATA_DIR` | `data_dir` | 字符串 | `var` | 非空 | 数据目录；相对路径以**当前工作目录**为基准 |
| `PERSONA_DATABASE_FILENAME` | `database_filename` | 字符串 | `persona.db` | 非空 | SQLite 文件名 |
| `PERSONA_PROVIDER` | `provider_name` | 字符串 | `fake` | 非空 | Provider 名；当前只有 Mock 家族 `fake` / `recording` / `failing` / `offline_guard` |
| `PERSONA_OFFLINE_ONLY` | `offline_only` | 布尔 | `true` | `true/false/1/0/yes/no/on/off` | **默认离线**。设为 `false` 时才允许真实外部调用，且必须提供密钥 |
| `PERSONA_BUDGET_MAX_REQUESTS` | `budget.max_requests` | 整数 | `200` | 1 ~ 1,000,000 | 一次任务最多多少次外部调用 |
| `PERSONA_BUDGET_MAX_TOKENS_IN` | `budget.max_tokens_in` | 整数 | `500000` | ≥ 0 | 输入 token 上限 |
| `PERSONA_BUDGET_MAX_TOKENS_OUT` | `budget.max_tokens_out` | 整数 | `200000` | ≥ 0 | 输出 token 上限 |
| `PERSONA_BUDGET_MAX_COST` | `budget.max_cost` | 数字 | 空（不限制） | ≥ 0 | 金额上限；留空 = 不限制 |
| `PERSONA_BUDGET_MAX_DURATION_SEC` | `budget.max_duration_sec` | 整数 | 空（不限制） | ≥ 1 | 最长运行秒数；留空 = 不限制 |
| `PERSONA_RETRY_MAX_RETRIES` | `retry.max_retries` | 整数 | `3` | 0 ~ 10 | 最大重试次数 |
| `PERSONA_RETRY_BACKOFF_BASE_MS` | `retry.backoff_base_ms` | 整数 | `500` | 1 ~ 60,000 | 退避基数（毫秒） |
| `PERSONA_RETRY_BACKOFF_MAX_MS` | `retry.backoff_max_ms` | 整数 | `8000` | 1 ~ 600,000 | 退避上限（毫秒），**不得小于基数** |
| `PERSONA_RETRY_JITTER` | `retry.jitter` | 布尔 | `true` | — | 退避是否加随机抖动 |
| `PERSONA_RETRY_TIMEOUT_MS` | `retry.per_call_timeout_ms` | 整数 | `30000` | 100 ~ 600,000 | 单次调用超时（毫秒） |
| `PERSONA_RETRY_RESPECT_RETRY_AFTER` | `retry.respect_retry_after` | 布尔 | `true` | — | 是否尊重服务端 `retry_after` |

> 预算超出的行为是**暂停 + 保存进度 + 可恢复**，**不是失败**（D-043）。

---

## 4. 敏感配置（2 项，只走环境变量）

| 环境变量 | 用途 | 缺失时会怎样 |
| --- | --- | --- |
| `PERSONA_PROVIDER_API_KEY` | 模型服务的密钥 | `offline_only=false` 时缺失 → **报错拒绝启动** |
| `PERSONA_SEARCH_API_KEY` | 搜索服务的密钥 | `offline_only=true`（默认）时不要求 |

**怎么设置**：

```powershell
# Windows PowerShell（仅当前会话）
$env:PERSONA_PROVIDER_API_KEY = "你的密钥"
```

```bash
# Git Bash / Linux / macOS
export PERSONA_PROVIDER_API_KEY="你的密钥"
```

**红线（有测试守着）**：

- `.env` 里出现敏感变量 → **忽略并告警**，绝不静默使用；
- `overrides` 里出现敏感变量 → 同样忽略；
- `Secrets` 的 `repr` / `str` / `to_public_dict()` 一律脱敏（只显示"已设置（长度 N，内容不显示）"）；
- 错误信息**不回显任何字符**；
- 密钥长度 < 8 或含空白 → 判为格式错误（只粗筛，不做真伪判断）。

---

## 5. 本地怎么设置（`docs/RUNNING.md` 也有）

```bash
cp .env.example .env      # 复制模板（.env 已被 .gitignore 忽略）
# 然后按需编辑 .env 里的**非敏感**项；密钥不要写进去
python -m persona config show
```

`.env` 只支持最简单的写法：`KEY=VALUE`、`#` 注释、可选引号、可选的 `export` 前缀。
**不支持**变量插值（`$OTHER`）—— 那需要引入 `python-dotenv`，本项目按 D-079 **不引入**。

---

## 6. 错误与告警

| 情况 | 结果 | 错误码 | 用户该做什么 |
| --- | --- | --- | --- |
| 值不是允许的取值范围（如 `PERSONA_MODE=bogus`） | ❌ 失败 | `VALIDATION.config_invalid` | 按提示改成本表里允许的取值 |
| 类型不对（该是整数却给了 `abc`） | ❌ 失败 | `VALIDATION.config_invalid` | 改成合法数字 |
| 超出上下限（如预算写 `0`） | ❌ 失败 | `VALIDATION.config_invalid` | 改到允许范围内 |
| 必填项为空（如 `PERSONA_DATA_DIR="  "`） | ❌ 失败 | `VALIDATION.config_invalid` | 填写非空值 |
| 退避上限 < 退避基数 | ❌ 失败 | `VALIDATION.config_invalid` | 调大上限或调小基数 |
| 允许真实调用却没给密钥 | ❌ 失败 | `VALIDATION.required_field` | 设置 `PERSONA_PROVIDER_API_KEY`，或把 `offline_only` 设回 `true` |
| 密钥格式明显不对（太短 / 含空白） | ❌ 失败 | `VALIDATION.config_invalid` | 重新设置（**不会回显原值**） |
| 未知配置项（如 `PERSONA_TYPO=1`） | ⚠️ **只告警，继续跑** | 无（warning） | 检查是不是拼错了 |

配置错误**不可重试、不可恢复**（属于"改完重开"类），与 `ERROR_HANDLING.md` §2.1 的 `VALIDATION` 类别一致。
一次加载会**收集全部问题**（`details.all_problems`），CLI 只打印第一条 + 其余条数。

---

## 7. 代码里怎么用

```python
from persona.config import load_settings, test_settings
from persona.core.result import is_err, unwrap

result = load_settings()              # 返回 Result[AppSettings]
if is_err(result):
    ...                               # 用 error.message 提示用户
settings = unwrap(result)

settings.database_path()              # 数据库文件路径（相对 data_dir）
settings.ensure_data_dir()            # 建好目录并返回路径（SQLite 不会自动建目录）
settings.to_provider_config()         # 转成 ProviderConfig（重试/超时的唯一来源）
settings.to_public_dict()             # 可安全写日志的快照（不含密钥）
```

**测试里**请用显式配置，不要依赖外部环境：

```python
settings = test_settings()                       # 离线、无密钥、目录 var-test
settings = test_settings(data_dir="/tmp/x")      # 覆盖单项
```

---

## 8. 与现有模块的衔接（只改了必要部分）

| 模块 | 衔接方式 | 改了什么 |
| --- | --- | --- |
| **CLI** | 新增 `persona config show` / `config check`；失败退出码 1 | 只加了子命令与展示函数 |
| **SQLite 存储** | `settings.ensure_data_dir()` 给出数据库路径，`SqliteStorage(path)` 照旧 | **未改** `SqliteStorage` 本身 |
| **Provider 抽象** | `settings.retry.to_provider_config()` 直接产出 `ProviderConfig` | **未改** Provider 接口 |
| **错误体系** | 新增错误码 `VALIDATION.config_invalid` | 只加了一个枚举值 + 一条注册 |

---

## 9. 测试覆盖（`tests/test_config.py`，46 项）

| 组 | 测什么 |
| --- | --- |
| `.env` 解析 | 注释、引号、`export` 前缀、文件不存在 |
| 默认配置 | 默认离线、无密钥、路径推导、绝对路径 |
| 环境变量覆盖 | 解析正确、来源标记正确、未设置的项仍是默认 |
| 优先级 | 默认 < `.env` < 环境变量 < overrides（四层逐一验证） |
| 非法配置 | 枚举/整数/布尔/范围/空值/退避倒置，错误码与不可重试属性 |
| 密钥安全 | repr/str/公开快照/错误信息**都不含密钥值**；`.env` 与 overrides 里的密钥被忽略 |
| 未知项 | 只告警不失败；无关环境变量（如 `PATH`）不干扰 |
| 衔接 | `to_provider_config()` 形状正确；`ensure_data_dir()` 能真正喂给 `SqliteStorage` |
| 静态规则 | **除 `persona/config` 外没有任何模块出现 `getenv` / `os.environ`** |

测试**全程离线**、使用假的密钥字符串、**不需要真实 API Key**。

---

## 10. 遗留问题（**未决定，不要当作已定**）

| # | 问题 | 现状 |
| --- | --- | --- |
| C-1 | 新增错误码 `VALIDATION.config_invalid` | 已在 `errors.py` 与 `ERROR_HANDLING.md` §2.2 补齐，**待人工确认**（与 R-2 同一处理口径） |
| C-2 | `PERSONA_SEARCH_API_KEY` 何时成为必填 | 目前只在联网模式下"可选"；等 P2 研究管线确定搜索 Provider 后再定 |
| C-3 | 是否支持**项目级配置文件**（如 `persona.toml`） | 暂不支持，只有 `.env`；有需要再提 |
| C-4 | 日志落地方式（`log_level` 目前只存不用） | 等 P2 有真实日志需求时再接，避免先写空壳 |

---

## 11. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | P2.2 初稿：18 项非敏感配置 + 2 项敏感配置、四级优先级、`.env.example`、错误与告警规则、`persona config show/check`、46 项测试 |
