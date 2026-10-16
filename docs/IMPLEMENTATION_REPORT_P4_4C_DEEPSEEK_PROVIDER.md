# P4.4-C 实施报告：DeepSeek Chat Provider

> **状态：实现完成、离线测试全绿；真实冒烟测试尚未执行（等你单独授权）。**
>
> 本轮**未发起任何真实请求、未产生任何费用、未 `git add` / `git commit`**。
>
> 前置：`docs/PLAN_P4_4C_DEEPSEEK_PROVIDER.md`（方案，已拍板 D1–D5）。

---

## 0. 一句话结论

新增 `persona/providers/deepseek.py`（`Provider.chat` 走 DeepSeek 官方
`POST /chat/completions`），把它接进装配层与注册表；**Provider 只报 token、不记账**，
金额由装配层按配置单价算；`model plan` 那条过时 notes 已改成陈述句。

---

## 1. 拍板项 → 落地位置对照

| # | 拍板 | 落地位置 | 证据 |
| --- | --- | --- | --- |
| **D1** | `thinking` = disabled | `deepseek.py` `_THINKING_DISABLED`，**每个请求显式发送** | `test_thinking_is_explicitly_disabled` |
| **D2** | `temperature` 保持抽取器 0.7 | `deepseek.py` 直传 `req.temperature`（不覆盖） | `test_temperature_is_passed_through` |
| **D3** | Provider 不记账，只报 token | `deepseek.py` `_usage_delta()` 里 `cost_confirmed=None` / `cost_estimate=None`；Provider **不接收** Ledger | `test_cost_confirmed_is_always_none`、`test_provider_alone_never_writes_to_a_ledger`、`test_end_to_end_confirms_cost_from_official_rate` |
| **D4** | 提示词缺 `"json"` → 拒绝 | `deepseek.py` `_prompt_mentions_json()` + `Err(EXTERNAL_UNAVAILABLE)` | `test_missing_json_keyword_is_a_hard_rejection`（并断言 **0 次网络调用**） |
| **D5** | cache 按 miss 保守口径 + 登记缺口 | `tokens_in` 取 `prompt_tokens`（= hit+miss）；无 cache 明细字段，缺口写进 `CONFIG.md §11.7.5` | `test_cache_fields_do_not_pollute_tokens_in` |

边界（均未改动）：币种 USD、汇率 1.0 不换算、档位 peak、单任务上限 0.25 USD、
`max_calls=1`、`max_retries=0`、合成虚构材料（阿雾/雾港/星灯）、P4.3 的 Wikipedia 全文未进项目。

---

## 2. 改动清单

| 文件 | 类型 | 内容 |
| --- | --- | --- |
| `persona/providers/deepseek.py` | **新增** | `DeepSeekProvider`：`capabilities` / `chat` / `search`(拒绝) / `usage` / `health`；请求映射、响应归一化、错误映射 |
| `persona/providers/registry.py` | 修改 | 注册 `"deepseek"` 工厂；密钥**只**取 `provider_api_key`（不回退搜索密钥） |
| `persona/providers/__init__.py` | 修改 | 导出 `DeepSeekProvider` |
| `persona/orchestrator/model_setup.py` | 修改 | 新增 `chat_provider_config()`、`chat_provider_from_settings()`；`_plan_notes()` 的过时说明改陈述句 + 两条新 notes |
| `persona/orchestrator/__init__.py` | 修改 | 导出上述两个函数 |
| `persona/cli/main.py` | 修改 | `model dryrun` 里同一句过时表述改为陈述句 |
| `tests/test_p4_4c_deepseek_provider.py` | **新增** | 九组 77 项离线测试 |
| `tests/test_provider_mock.py` | 修改 | 注册表名单精确断言由 4 个改为 5 个（新增 `deepseek`）——**未放宽** |
| `docs/CONFIG.md` | 修改 | 新增 §11.7「接入 DeepSeek 聊天 Provider」+ 变更历史 v0.4 |

**未改动**：`persona/config/*`（本轮无新配置项）、`persona/modules/extraction.py`、
`persona/modules/claim_validation.py`、`persona/core/*`、`.env.example`。

---

## 3. 关键实现点

### 3.1 统一契约 → DeepSeek 线格式（唯一映射点）

| `ChatRequest` | 线上请求 | 说明 |
| --- | --- | --- |
| `model_hint`（空则装配层的 `model_id`） | `model` | 两者都空 → 拒绝发送 |
| `messages` | `messages` | 角色只允许 system/user/assistant，其它 → 拒绝发送 |
| `response_format="json_schema"` | `{"type":"json_object"}` | DeepSeek 不支持严格 schema |
| `response_format="text"` | **不发送该字段** | 与服务端默认一致，不制造差异 |
| `response_format=<其它>` | 拒绝发送 | 不认识的取值不猜 |
| `max_output_tokens` | `max_tokens` | DeepSeek **没有** `max_completion_tokens` |
| `temperature` | `temperature` | 直传 0.7（D2） |
| `stop`（空） | **不发送** | — |
| `metadata` | **不发送** | 含 `prompt_version`，属内部审计信息 |
| — | `stream=false` | 显式，不依赖默认 |
| — | `thinking={"type":"disabled"}` | 显式（D1） |

### 3.2 Provider **不记账**（D3）

```
Provider   → HTTP + 错误映射 + 归一化 + 报 token 数（客观事实）
抽取器      → 预算预留（发送前）+ 记账（唯一键口径）+ 严格校验
装配层      → 把配置的单价/币种/档位翻译成 ModelCallLimits（唯一换算点）
```

端到端实测（桩网络）：`prompt_tokens=1200 / completion_tokens=300` →
按配置价（$0.3 / $1.2 per 1M）算出 **0.00072 USD**，`cost_status=CONFIRMED`、
`cost_source=SOURCE_OFFICIAL_RATE`（语义准确：**不是**官方回报金额，而是按官方费率推算）。

### 3.3 默认不重试

`RetryLimits.max_retries` 默认 3（给按次计费的检索写的）。装配层
`chat_provider_config()` 强制置 0，`DeepSeekProvider` 的**兜底默认也是 0** ——
"忘了改配置"不该等于"重复花钱"。`model plan` 会打印这条。

### 3.4 `model plan` 的过时 notes 已改

- 旧：`…**须由 DeepSeek 适配层映射为 json_object**；接入时仍需实测确认。`
- 新：`…**DeepSeekProvider 已在发出前映射为 json_object**；映射时若检测不到 "json"
  会**拒绝发送**（不静默改写提示词）。cache hit/miss 明细无承载字段，统一按 miss
  保守口径计价（D5，已知缺口）。`
- 另加两条：`max_retries=0`（D3）、`thinking=disabled`（D1）。

---

## 4. 离线测试结果

```
python -m unittest tests.test_p4_4c_deepseek_provider
→ Ran 77 tests … OK
```

九组覆盖（共 77 项）：请求构造 15 / `response_format` 映射 6 / 响应归一化 8 /
用量映射 7 / 错误映射 13 / 密钥安全 6 / 账本边界 6 / 能力与协议 6 / 装配接线 10。
**全部离线**：网络出口 `DeepSeekProvider._urlopen` 整类替换，零 socket、零真实请求。

---

## 5. 合规扫描

| 项 | 结果 |
| --- | --- |
| 厂商 SDK 导入（openai/httpx/requests/anthropic/boto3） | **0**（唯一命中是既有测试里的断言字符串） |
| `deepseek.py` 内 `os.getenv` / `os.environ` | **0**（仅 docstring 提到"不得读环境变量"） |
| `deepseek.py` 内 `print` / `logging` / 文件写入 | **0** |
| 密钥进请求体 / 异常消息 / `repr` | 有测试逐条钉住，均为**否** |
| 真实人物名（AC-16） | **0**（`capturing` 因含 `turing` 子串误报一次，已确认非人名） |
| 仓库内 `.db` / 临时文件 | 无新增 |
| `git add` / `git commit` | **未执行**（暂存区为空） |

---

## 6. 仍未做的事（如实登记）

1. **真实冒烟测试未执行** —— 等你的单独授权。预检口径不变：1 个任务、`max_calls=1`、
   `max_extracts ≥ 1`、0.25 USD 上限、`max_retries=0`；冒烟时记录**真实耗时**
   （官方非流式等待期会持续吐空行 keep-alive，urllib 的 socket 级 30s 超时**可能不触发**）。
2. **cache hit / miss 明细未计价**（D5）：`ChatResponse.usage` 无承载字段，统一按 miss
   保守口径（高估），**列为已知缺口**。要精确计价需给 `UsageDelta` 加字段（改已验收结构，需另行授权）。
3. **两层预算**仍只执行一个金额上限（取更严的 0.25）；`pending` 无持久化复核队列；
   `max_retries` 的真实重试策略未实现——均为**既有遗留项**，本轮未改。
4. **未验证真实抽取质量**：本轮只证明"请求构造 / 响应归一化 / 错误映射 / 记账边界"
   在离线可复现，**不能**据此宣称真实模型抽取质量已通过。

---

## 7. 复现命令

```bash
# 新增测试（离线、零联网）
python -m unittest tests.test_p4_4c_deepseek_provider

# 相关回归
python -m unittest tests.test_p4_4a_model_extraction tests.test_claim_extraction \
                   tests.test_p4_4b_model_setup tests.test_provider_mock \
                   tests.test_p4_2_tavily_compliance tests.test_dependency_rules tests.test_config

# 全量
python -m unittest discover -s tests -t .

# 离线看"真正会发出去的请求"（不联网、不记账、不花钱）
python -m persona.cli.main model dryrun --no-dotenv
```
