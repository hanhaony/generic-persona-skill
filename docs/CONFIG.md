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

## 10. 遗留问题状态（C-1 ~ C-4 **已由用户拍板**）

| # | 问题 | 结论 | 决定编号 |
| --- | --- | --- | --- |
| ~~C-1~~ | 新增错误码 `VALIDATION.config_invalid` | ✅ **已批准**：确认新增并**永久保留**在 `ERROR_HANDLING.md` §2.2；配置取值非法/超范围一律用它，不另起新码 | **D-083** |
| ~~C-2~~ | `PERSONA_SEARCH_API_KEY` 何时成为必填 | ✅ **保持可选**：等 P2 确定搜索 Provider 后再决定是否改为必填；在此之前缺它**不得**导致配置校验失败 | **D-084** |
| ~~C-3~~ | 是否支持项目级配置文件（如 `persona.toml`） | ✅ **暂不支持**，保持极简。配置来源只有：内置默认值、`.env`、环境变量、显式 overrides | **D-085** |
| ~~C-4~~ | 日志落地（`log_level` 目前只存不用） | ✅ **暂只存不用**，等 P2 接真实日志时再启用；**禁止**先写空壳日志器占位 | **D-086** |

---

## 11.5 模型抽取配置（`model.*`，P4.4-B / P4.4-C）

模型抽取（G-2）的**服务商决策与预算**集中在 `model.*` 一组字段里。它**不在** §3 的表里
（§3 是 P2.2 的核心 18 项），但走的是**完全同一套**加载/校验/来源追踪机制：
`CONFIG_FIELDS` 统一登记、`describe_fields()` 统一展示、`persona config show` 一并打印。

**决策（P4.4-B，用户拍板；P4.4-C 最终拍板）**：服务商 DeepSeek API / 模型 `deepseek-flash` /
币种 `USD`、档位 `peak`（$0.3 / $1.2 per 1M，cache-miss 口径）、单任务与总上限暂取同一个
保守值 `0.25`、`require_gate_verdict=true`。只选 DeepSeek 一家，但 `Provider` 抽象保留
不变，后续可换。

**币种固定 USD，不做汇率换算**（P4.4-C 拍板）：DeepSeek 官方计价即 USD，一本账只会有
一种币。原 P4.4-B 的 `fx_usd_cny` / `fx_source` 折算通道**已移除**——汇率是会过期的
商业条款，折算结果又不参与任何预算判定，留着只会多一个"看起来算了其实没算"的数。
旧键现在按 D-082 降级为"未知项"（告警、不失败）。

| 环境变量 | 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `PERSONA_MODEL_PROVIDER` | `model.provider` | 字符串 | 空（**不启用**） | 模型服务商；当前允许联网的只有 `deepseek`。空 = 关闭模型抽取（离线安全默认） |
| `PERSONA_MODEL_ID` | `model.model_id` | 字符串 | `deepseek-flash` | 模型 ID |
| `PERSONA_MODEL_CURRENCY` | `model.currency` | 枚举 | `USD` | `USD` / `CNY`；**一本账只能有一个币种** |
| `PERSONA_MODEL_PRICE_TIER` | `model.price_tier` | 枚举 | `off_peak` | `off_peak` / `peak`；DeepSeek 同一模型峰时/闲时差一倍，必须显式选一个 |
| `PERSONA_MODEL_PRICE_IN_PER_1M` | `model.price_in_per_1m` | 数字 | 空 | 输入单价（原币 / 每 100 万 token） |
| `PERSONA_MODEL_PRICE_OUT_PER_1M` | `model.price_out_per_1m` | 数字 | 空 | 输出单价（同上） |
| `PERSONA_MODEL_PRICE_SOURCE` | `model.price_source` | 字符串 | 空 | 单价来源（官方价目表地址） |
| `PERSONA_MODEL_PRICE_CHECKED_AT` | `model.price_checked_at` | 字符串 | 空 | 单价核对日期 |
| `PERSONA_MODEL_MAX_COST` | `model.max_cost` | 数字 | 空 | **项目累计**上限（原币；含从库里 hydrate 的历史支出，P4.6 / D-B） |
| `PERSONA_MODEL_MAX_COST_PER_TASK` | `model.max_cost_per_task` | 数字 | 空 | **单任务**上限（原币；按 `task_id` 累计，含同任务历史） |
| `PERSONA_MODEL_MAX_CALLS` | `model.max_calls` | 整数 | 空 | 一条流程最多几次模型调用 |
| `PERSONA_MODEL_EST_TOKENS_IN` | `model.est_tokens_in` | 整数 | `0` | 事前估算输入 token（只用于**预留**） |
| `PERSONA_MODEL_EST_TOKENS_OUT` | `model.est_tokens_out` | 整数 | `0` | 事前估算输出 token |
| `PERSONA_MODEL_REQUIRE_GATE_VERDICT` | `model.require_gate_verdict` | 布尔 | `true` | 材料必须亮出正文质量门禁结论才允许进抽取器 |

### 11.5.1 四条跨字段校验（违反即**拒绝加载**，不是运行期才报）

| 条件 | 为什么 |
| --- | --- |
| 设了金额上限 → **必须**同时给出输入与输出单价 | 没单价就在发送前算不出"本次最多花多少"，闸门只能拒绝自动发起请求 |
| 设了金额上限 → **必须**有事前 token 估算（两者不能都是 0） | 闸门按"可能产生的最大费用"预留，没有 token 估算就算不出这个数（未知不是 0） |
| 给了单价 → **必须**记录 `price_source` | 来源不明的单价一旦过期会被当成官方费率误信，比没有单价更危险 |
| `max_cost_per_task` ≤ `max_cost` | 单任务花费不可能超过总额 |

另外两条与既有字段的联动：

- `model.provider=deepseek` 与 `PERSONA_OFFLINE_ONLY=true` **互斥**（真实服务商不得在离线开关下启用）；
- `model.provider=deepseek` 要求已设置模型密钥；
- **交叉检查**：启用模型 + `require_gate_verdict=true` + `PERSONA_RESEARCH_MAX_EXTRACTS=0`
  → 拒绝加载。检索摘要天生没有正文门禁结论，这个组合会让**材料永远进不了抽取器、
  模型一次都不会被调用**，而运行日志看起来像"业务流程正常跑完了"。

### 11.5.2 金额闸门**两层**（P4.6 / D-B：缺口已闭合）

修复前金额判断只有**一个**上限参数，装配层只能取 `min(总上限, 单任务上限)` 近似，
于是"项目累计封顶"从未独立生效。

P4.6 之后是两层，各算各的：

| 层 | 配置 | 计数口径 | 参数 |
| --- | --- | --- | --- |
| 单任务 | `model.max_cost_per_task` | 该 `task_id` 的累计（含同任务历史） | `ModelCallLimits.max_cost` |
| 项目累计 | `model.max_cost` | 整本账（含启动时 hydrate 读回的历史） | `ModelCallLimits.max_cost_total` |

任一层超限 = 发送前 `BUDGET_EXCEEDED`（0 次调用）。历史里"未知且无任何估算"的笔数
会让闸门直接停手（金额无法界定，**不能当 0**，D-060）。

`effective_max_cost()` 保留下来表示"单任务层生效值"（仍然是两个配置的更严者），
因为配置校验要求 `单任务 ≤ 总上限`，取 `min` 只是为了不让单任务层成为累计层的漏洞。

---

## 11.5.3 关系研判配置（`relationship.*`，P4.6 / D-D）

| 环境变量 | 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `PERSONA_RELATIONSHIP_ANALYZER` | `relationship.analyzer` | 枚举 | `rule` | `rule` = 装配离线规则分析器（确定性、零成本、条目全部可追溯到已登记证据）；`off` = 不装配，关系阶段走兜底 |

关闭的影响只在"是否装配分析器"：`perspectives` / `feeling_toward` /
`interaction_patterns` 会留空并记 `open_questions`（**不臆造**），
`shared_experiences` 仍由兜底路径产出。分析器运行期返回 `Err` 时按 D-E
**降级到兜底并留 `RELATION_ANALYZER_DEGRADED` 告警**，不整体失败。

---

## 11.6 敏感配置的别名（P4.2 / P4.4-B）

| 别名 | 等价于 | 用于 |
| --- | --- | --- |
| `TAVILY_API_KEY` | `PERSONA_SEARCH_API_KEY` | 真实搜索 Provider |
| `DEEPSEEK_API_KEY` | `PERSONA_PROVIDER_API_KEY` | 真实模型 Provider |

两条规矩：

1. `PERSONA_*` 本体**优先于**别名（两个都设时以本体为准）；
2. 别名写进 `.env` 文件同样会被识别为"敏感变量进文件"，**忽略并告警**。
   （没有这一层，`DEEPSEEK_API_KEY=...` 会因为不在 `PERSONA_` 前缀内被**静默忽略**，
   用户以为配好了、实际一直没生效。）

---

## 11.7 接入 DeepSeek 聊天 Provider（P4.4-C）

`persona/providers/deepseek.py` 把统一契约 `ChatRequest` / `ChatResponse` 翻译成
DeepSeek 的线格式。**它只做外部调用与结果标准化**，不读环境变量（D-077）、
不记账、不打印、不落盘，并且只用标准库 `urllib`（不引入任何厂商 SDK，D-054）。

### 11.7.1 官方契约（核对日期 2026-10-16，来源 api-docs.deepseek.com）

| 项 | 官方值 | 本项目的处理 |
| --- | --- | --- |
| Base URL / 端点 | `https://api.deepseek.com` + `POST /chat/completions` | 常量 `DEFAULT_BASE_URL` + `CHAT_PATH`，可注入覆盖（测试指向桩） |
| 鉴权 | `Authorization: Bearer ${DEEPSEEK_API_KEY}` | 密钥**只**走请求头；绝不进请求体 / 异常 / `repr` |
| `response_format` | **仅** `text` / `json_object`（**无** `json_schema`） | 统一契约的 `json_schema` → 映射为 `json_object`；`text` → 不发送该字段 |
| JSON 模式硬要求 | 提示词**必须**含 `"json"` 字样，否则可能"一直输出空白到 token 上限" | 映射前检查（大小写不敏感）；**没有就拒绝发送** |
| 输出上限字段 | **只有** `max_tokens`（无 `max_completion_tokens`） | `max_output_tokens` → `max_tokens` |
| `temperature` | 0–2，官方默认 **1** | 抽取器显式发 **0.7**（D2），覆盖官方默认 |
| `thinking` | `{"type": "enabled"/"disabled"}`，文档标默认 `enabled` | **显式**传 `{"type":"disabled"}`（D1） |
| `stream` | 默认非流式 | 显式传 `false`（不依赖默认） |
| `finish_reason` | `stop`/`length`/`content_filter`/`tool_calls`/`insufficient_system_resource`/`aborted`，**无** `refusal` | 原样透传；`content_filter` 由 `claim_validation` 判为拒答 |
| `usage` | `prompt_tokens`（= hit + miss）、`completion_tokens`、`total_tokens` | `tokens_in` / `tokens_out` 直取 |
| 响应 `id` | 顶层 `id`（形如 `chatcmpl-…`） | 同时填 `ChatResponse.raw_ref` **与** `UsageDelta.request_id`——抽取器据此把服务端标识写进账本（见 §11.7.7） |

### 11.7.2 记账点：**Provider 不记账**（D3）

```
Provider（deepseek.py）  只做：HTTP 调用 + 错误映射 + 响应归一化 + 报 token 数
抽取器（extraction.py）  只做：预算预留（发送前）+ 记账（唯一键口径）+ 严格校验
装配层（model_setup.py） 只做：把配置的单价/币种/档位翻译成 ModelCallLimits
```

金额的**唯一换算点**在装配层；`ExternalUsageLedger` 的**同一本实例**挂在**抽取器**上，
Provider 的 token 数经 `ChatResponse.usage` 流入这本账。**Provider 绝不填 `cost_confirmed`**：
DeepSeek 响应里只有 token、没有金额，填了就是把"按费率推算的值"冒充成"官方回报值"；
抽取器因此走 `SOURCE_OFFICIAL_RATE`（语义准确：不是官方回报金额，是按官方费率推算）。

为什么不让 Provider 自己记一笔（曾评估过的备选方案）：记账键口径
（`_call_key()`）含 `prompt_version` 与排序后的材料键，Provider 无从得知，
只能用自己推导的键 → 同一笔真实请求在账本里出现**两条不同键**的记录 →
`occupied_cost()` 被**虚占一倍**（0.25 USD 上限实际只剩一半可用），
`unknown_requests` 也可能虚高触发 `should_pause` 误停。三条都是净损失，故不采用。

### 11.7.3 默认不重试（`max_retries=0`）

配置层 `RetryLimits.max_retries` 默认为 **3**（那是给按次计费的检索写的）。
对按 token 计费的聊天模型，**重试 = 再花一次钱**，所以：

- 装配层 `chat_provider_from_settings()` 通过 `chat_provider_config()` **强制置 0**
  （超时 / 退避参数仍沿用配置）；
- `DeepSeekProvider` 自己的兜底默认值也是 `max_retries=0`——"忘了改配置"不该等于"重复花钱"；
- `persona model plan` 的 notes 会打印这条。

### 11.7.4 JSON 模式缺 `"json"` → 拒绝发送（D4）

检查 messages 的 `content` 里是否出现 `json`（不区分大小写）：

- **有** → 照常发送（抽取器的系统消息已嵌入 "JSON Schema" 文本，实测满足）；
- **没有** → `Err(EXTERNAL_UNAVAILABLE)`，**不发送**。

**为什么不自动补一句**：往调用方提示词里塞文字是**隐藏行为**——调用方以为自己控制了
提示词，实际被改过，而且 dry-run 看到的与实际发出的不是同一份请求。这条也让
`persona model dryrun` 具备真实价值：打出来的请求通不过这道校验，就说明**真跑也会被拦**。

### 11.7.5 cache hit / miss 的计价（D5，**遗留项：已接受该口径，不再改**）

官方 `usage` 里有 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`，但
`ChatResponse.usage` / `UsageDelta` **没有承载 cache 明细的字段**，加字段属改已验收结构，
需另行授权。因此本轮**统一按 cache miss 的保守口径**计价——配置里取的正是 miss 的
peak 价（$0.3 / $1.2 per 1M），即**高估**，属于保守安全的一侧。

**P4.4-C 验收拍板：接受当前 miss 保守口径，登记为遗留项。** 触发条件（何时才值得做）：
出现"cache 命中比例高到费用被明显高估"的实测证据（例如逐月账单与估算差额持续偏大），
或需要与账单做**分毫对账**。在此之前不引入 cache 明细字段。

### 11.7.6 装配入口

| 函数 | 作用 |
| --- | --- |
| `chat_provider_config(settings)` | `ProviderConfig`：沿用配置的超时/退避，**`max_retries` 强制 0** |
| `chat_provider_from_settings(settings)` | 按配置构造 `DeepSeekProvider`；**返回 `Err` 的三种情形都不构造对象**：`model.provider` 为空 / 不认识的服务商 / 密钥未设置 |

密钥只从 `settings.secrets.provider_api_key` 取（**不**回退到 `search_api_key`：
拿搜索密钥调模型只会得到 401，那种"看起来配好了"的失败不如"密钥为空、`health()` 报
degraded"来得清楚）。`persona model plan` 只回答密钥"是否设置"，绝不回显内容（D-081）。

**配置载体（P4.4-C 验收）**：本节那组拍板值已落成本机 `.env`（**只含非敏感项**；
路径已被 `.gitignore` 第 22 行 `.env` 忽略）。密钥**不在** `.env` 里，只走进程环境变量
`PERSONA_PROVIDER_API_KEY`（别名 `DEEPSEEK_API_KEY`）——`.env` 里出现敏感变量会被
loader 告警并忽略（D-078/D-079）。优先级仍是 D-080：默认值 < `.env` < 环境变量 < overrides。

### 11.7.7 对账标识 `request_id`（P4.4-C 验收修复）

账本的 `ExternalUsageRecord.request_id` 是**逐笔对账与服务商报障的唯一标识**，
而它**只**从 `UsageDelta.request_id` 取（`extraction._settle()` 的唯一口径）。

- **修复前**：`DeepSeekProvider._usage_delta()` 把 `request_id` 恒填 `None`
  → 账本里每笔模型费用的服务端凭据都是空的。响应 `id` 虽然进了 `ChatResponse.raw_ref`，
  但那只是"对账 / 报障"的**通用**字段；**不让 L4 抽取器反过来假设它等于 `request_id`**
  ——那是把某家厂商的约定泄漏进业务层，换 Provider 就错。
- **修复后**：`DeepSeekProvider._request_id_of(data)` 取响应顶层 `id` 并传入 `UsageDelta`。
  口径与 `TavilyProvider._request_id_of` 一致：**响应没有 `id` → `None`，绝不填 `""`**
  （D-060：未知不是空串；空串会伪装成"有标识但对不上"）。`raw_ref` 保留不变。

护栏测试（`tests/test_p4_4c_deepseek_provider.py`）：`UsageDelta` 带上顶层 `id`；
响应缺 `id` 时为 `None`；`usage` 缺失时标识仍留住；端到端账本记录带上服务端 `id`。

---

## 12. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | P2.2 初稿：18 项非敏感配置 + 2 项敏感配置、四级优先级、`.env.example`、错误与告警规则、`persona config show/check`、46 项测试 |
| 2026-10-16 | v0.2 | **P4.4-B**：新增 `model.*` 16 项（服务商/模型/币种/档位/单价/来源/汇率/预算/gate）、5 条跨字段校验 + 3 条联动规则、密钥别名机制（`TAVILY_API_KEY`/`DEEPSEEK_API_KEY`）、`persona model plan/dryrun` 两个只读命令 |
| 2026-10-16 | v0.3 | **P4.4-C 拍板**：币种固定 USD 并**移除** USD→CNY 折算通道（`fx_usd_cny`/`fx_source` 两个字段及全部使用处；旧键按 D-082 降级为未知项告警）；档位保留 `peak`（$0.3/$1.2 per 1M）；总上限与单任务上限暂取同一个保守值 `0.25`、`max_calls=1`（冒烟范围）；两层预算（任务级 + 全局）登记为遗留项 |
| 2026-10-16 | v0.4 | **P4.4-C 验收三项决定**：①配置落盘——拍板值写入本机 `.env`（只含非敏感项，密钥仍只走环境变量）；②§11.7.7 修复对账标识：Provider 把响应顶层 `id` 补进 `UsageDelta.request_id`（此前恒为 `None`，账本缺逐笔凭据），并补 4 项护栏测试；③§11.7.5 明确 cache miss 保守口径**接受为遗留项**（含重启条件） |
| 2026-10-16 | v0.4 | **P4.4-C 实现**：新增 `persona/providers/deepseek.py`（`Provider.chat`、Bearer 鉴权、`json_schema`→`json_object` 映射、缺 "json" 拒绝、`thinking=disabled`、`max_retries=0`、只报 token 不记账）；`registry` 注册 `deepseek`；装配层新增 `chat_provider_config()` / `chat_provider_from_settings()`；`model plan` 那条"须由适配层映射"的 notes 改为陈述句并补重试/思考模式两条；cache 明细计价列为已知缺口（D5）。**未发起任何真实请求、未提交 Git** |
