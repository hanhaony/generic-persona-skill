# P4.4-C 真实模型受控冒烟测试 · 验收报告

- 项目：`D:\AIprojects\generic-persona-skill`
- 执行日期：2026-10-16（+08:00）
- 运行器：`scripts/smoke_p4_4c.py`（一次真实调用）+ `scripts/verify_p4_4c_outputs.py`（离线复算）
- 结论：**真实调用已执行且成功；输出可追溯；门禁生效；预算核算可靠；回归全绿。未发现 A 级阻塞。**
  验收后又按拍板完成 **3 项决定**（§9），两个缺口均已关闭。
- Git：**未提交**（暂存区为空），未改动本机环境变量。

---

## 1. 调用是否执行

| 项 | 值 |
| --- | --- |
| 是否真实调用 | **是**，且**仅 1 次**（`recorder.calls = 1`，`max_retries=0`） |
| 端点 | `POST https://api.deepseek.com/chat/completions` |
| 发出时间 | 2026-10-16T10:15:13Z（本地 18:15:13） |
| 端到端耗时 | **2.61 s**（远小于 30 s 超时，未触发超时路径） |
| 结果 | `Ok`，`finish_reason = stop` |
| 失败重试 | 未发生（`max_retries` 已被装配层强制为 0，预检已断言） |

## 2. Provider / 模型

| 项 | 值 | 结论 |
| --- | --- | --- |
| Provider | `DeepSeekProvider`（`persona/providers/deepseek.py`） | 与 P4.4-C 实现一致 |
| 模型 ID | `deepseek-flash`（线上 `model` 字段同名） | **未擅自换模型** |
| 币种 / 档位 | USD / **peak**（$0.3 / $1.2 per 1M） | 与拍板一致 |
| `response_format` | 统一契约 `json_schema` → 线上 `{"type":"json_object"}` | 映射点唯一，预检已断言 |
| `thinking` | `{"type":"disabled"}` | D1 落地，响应无 `reasoning_content` |
| `stream` | `false` | 显式发送，不依赖服务端默认 |
| `temperature` | 0.7 | D2：直传抽取器现值，未被 DeepSeek 默认的 1 覆盖 |
| `metadata` | **未外发** | 含 `prompt_version` 的内部审计信息不外泄 |
| 超时 | 30000 ms（配置默认） | 与拍板一致 |
| 密钥 | 仅走 `Authorization: Bearer` 请求头；输出全行扫描**无密钥子串** | 通过 |

## 3. Schema / 追溯 / Gate 结果

### 3.1 结构（Schema）

整批结论 `verdict = OK`；`validation_detail = {"accepted": 5, "rejected": 0, "pending": 0, "issues": [], "raw_chars": 1132}`；
`malformed = false`；顶层为 `{"claims":[...]}`，5 条候选字段齐备（`material_key` / `claim` / `tier` / `dimension` / `subject_entity_ids` 全在）。

### 3.2 可追溯（逐字摘录 + 框架定位）

5/5 条均为 `VERBATIM`，每条都带 `verbatim_quote`，且 `locator` **由框架算出**（模型伪造不了位置）。独立复算结果：

| key | 摘录 | 复算下标 | locator |
| --- | --- | --- | --- |
| smoke1-001 | 阿雾在雾港灯塔值守 | 0 | `{snippet,0,9}` ✓ |
| smoke1-002 | 每日记录潮位与风向 | 10 | `{snippet,10,19}` ✓ |
| smoke1-003 | 据地方志记载，阿雾偏爱在傍晚独自巡视堤坝 | 20 | `{snippet,20,40}` ✓ |
| smoke1-004 | 星灯会由阿灯发起 | 41 | `{snippet,41,49}` ✓ |
| smoke1-005 | 成员多为雾港的老住户 | 50 | `{snippet,50,60}` ✓ |

主体 ID 全部落在白名单 `{pe_smoke_awu, pe_smoke_adeng}` 内，**无自造 ID**。
`tier` 语义正确：VERBATIM 必有摘录；本批无 INFERENCE（若出现，必须带 `derivation_keys` 且不得带摘录，已有负样例覆盖）。

### 3.3 Gate（`require_gate_verdict=true`）

- **正样例**：材料 `quality_status = VALID` → 放行，模型被调用 1 次。
- **负样例（离线、桩 Provider）**：
  - `LOW_QUALITY` → 拒收 `MATERIAL_NOT_GATE_PASSED`，**零模型调用**；
  - 未声明 `""` → 拒收 `MATERIAL_UNDECLARED`，**零模型调用**。
- 结论：**PENDING 门禁未失效**；材料不合格时连模型都看不到（费用为 0）。

### 3.4 不因格式正确就默认事实可信

产出物是 `CandidateClaim`（带 `model-extract` 标记），**不是** `EvidenceItem`；
`CandidateClaim` 结构上没有"已验证 / 证据 ID"字段；未发生任何证据登记。
模型这一批恰好全部命中原文，但"格式正确"只证明结构合法，**不等于**事实已被核实——登记与否仍由 `Evidence` 决定。

## 4. token / 费用 / 账本记录 ID

| 项 | 值 |
| --- | --- |
| `tokens_in` | 718 |
| `tokens_out` | 476 |
| **账本记录 ID（幂等键）** | `bc740fc5e7a82b6cbd19256c562c7ffc` |
| DeepSeek 服务端请求 ID | `d7567163-87eb-4844-b659-909be09110a2`（落在 `ChatResponse.raw_ref`，见 §7 缺口 2） |
| `cost_estimate`（发送前预留） | 0.0033 USD |
| `cost_confirmed`（结算） | **0.0007866 USD** |
| `cost_source` | `OFFICIAL_RATE`（按官方单价 × 真实 token 复算） |
| `cost_status` | `CONFIRMED` |
| 复算核对 | 718/1000×0.0003 + 476/1000×0.0012 = **0.0007866** ✓ |
| 未知费用笔数 / 失败笔数 | 0 / 0（失败路径**不记 0** 的纪律由既有测试钉住） |

`Provider` 上报的 `UsageDelta.cost_confirmed = None`、`cost_status = UNKNOWN`——**符合 D3**：Provider 只报 token，金额由装配层按配置单价算。

## 5. 累计费用与剩余预算

账本为**内存新建**（项目内无 `.db`、无 `var/`；未创建、未重置、未清空任何账本文件）。

| 项 | 值 |
| --- | --- |
| 记录数 | 1 |
| 已确认费用 | 0.0007866 USD |
| 预留费用 | 0.000000 USD（已结算） |
| 未知占位 | 0.000000 USD |
| **已占用（保守口径）** | **0.000787 USD** |
| **剩余预算** | **0.249213 / 0.25 USD** |

两层预算（总上限 vs 单任务上限）仍取同一个 0.25 值的更严那一个，**两层独立计数属既有遗留项**，本轮未做。

## 6. 回归测试通过数

| 范围 | 结果 |
| --- | --- |
| G-2（`test_claim_extraction`）+ G-4（`test_relationship_analysis` / `test_relationship_module`）+ H-10（`test_h10_e2e` / `test_business_orchestrator`）与相关模块 | **605 项全部通过** |
| 全量 `unittest discover -s tests -t .`（冒烟当轮） | **1118 项全部通过**（与 P4.4-C 实现后的基线一致） |
| `test_p4_4c_deepseek_provider`（`request_id` 修复后） | 77 → **81 项全部通过**（新增 4 项护栏） |
| 全量 `unittest discover -s tests -t .`（修复后复跑） | **1122 项全部通过**（= 1118 + 4 新增，**零回归**） |
| 离线输出与门禁复算（`scripts/verify_p4_4c_outputs.py`） | **39 项全部通过** |
| 合规扫描 | 厂商 SDK 真实导入 0；`scripts/` 内 `os.getenv`/`os.environ` 0；`deepseek.py` 内 `os.*`/`print`/`logging` 的**可执行代码**命中 0（唯一文本命中是模块 docstring 里引述 D-077 的一句话，已用 AST 复核）；密钥字面量 0；真实人物名 0；无 `.db`/临时文件；暂存区为空 |

冒烟当轮**未改动任何测试文件**；验收后的 `request_id` 修复**只新增 4 项**护栏测试，
既有断言**一项未删、未放宽**（`persona/` 仅改 `providers/deepseek.py` 一个文件）。

## 7. 阻塞问题与需拍板缺口

**A 级阻塞：无。** 五条停止条件全部未触发——真实调用已执行、输出可追溯、预算核算可靠、PENDING 门禁有效、关键回归通过。

需拍板的缺口（均不推翻本次结论）：

1. **模型段配置未持久化（执行前发现的真问题）** —— **已关闭**（见 §9.1）
   本机环境变量里只有 `PERSONA_PROVIDER_API_KEY`，**没有任何 `PERSONA_MODEL_*`**，项目内也没有 `.env`。
   即：拍板的 provider / 模型 / 币种 / 档位 / 单价 / 上限原本**只存在于决策里，未落到任何配置载体**。
   本次冒烟用 `load_settings(overrides=...)` **显式注入**（跑什么配置一目了然、不改本机任何东西）。
   → 验收后经授权写入本机 `.env`（只含非敏感项，已被 `.gitignore` 忽略），配置已成为项目事实。

2. **账本记录缺服务端请求 ID（新发现）** —— **已修复**（见 §9.2）
   DeepSeek 响应的 `id` 落进了 `ChatResponse.raw_ref`，但 `extraction._settle()` 只从
   `UsageDelta.request_id` 取值，而 `deepseek._usage_delta()` 把 `request_id` 设为 `None`
   → 账本记录的 `request_id` **恒为 `None`**。现已由 Provider 补填，并补 4 项护栏测试。

3. **cache 计价（既有已知缺口 D5）**：`tokens_in = 718` 一律按 **cache miss** 保守口径计价。
   若响应含 `prompt_cache_hit_tokens`，实际费用更低——本轮是**高估**（安全方向）。

4. **档位假设可能偏保守**：本地 18:15 = **10:15 UTC 周五**，按已核实的档位窗口
   （peak：01:00–04:00 与 06:00–10:00 UTC 工作日），该时刻**已落在 peak 之外**。
   本轮配置固定按 peak 计价，若实际为 off-peak，则费用被**高估一倍**（0.0007866 vs 约 0.0003933）。
   方向仍是保守（不超支），但建议核实档位窗口后再决定计价口径。

## 8. 下一步建议

1. ~~拍板缺口 1~~ —— 已按拍板关闭（§9.1）。
2. ~~修缺口 2~~ —— 已按拍板修复（§9.2）。
3. 真实链路的**端到端**冒烟（跑一次真实 `Research` → 门禁 → 抽取）仍**未做**：
   本次是"直接注入合成材料 + 单次抽取"，只证明了 Provider 接入与校验/门禁/记账在真实响应下成立，
   **不代表真实检索质量、真实抽取质量、真实人物场景已验证**。下一步为**真实人物受控测试**（待授权）。
4. 遗留项不变：两层预算独立计数、`pending` 持久化复核队列、抽取器 `max_retries` 参数未实现、
   语义核验未做、**cache miss 保守计价（§9.3，已接受为遗留）**。

---

## 9. 验收后的三项决定（本轮执行）

### 9.1 配置落盘：写入本机 `.env`

授权后新增项目根 `.env`，**只含非敏感项**，共 43 行：

| 分组 | 键 |
| --- | --- |
| 联网开关 | `PERSONA_OFFLINE_ONLY=false` |
| 用谁 | `PERSONA_MODEL_PROVIDER=deepseek` / `PERSONA_MODEL_ID=deepseek-flash` / `PERSONA_MODEL_CURRENCY=USD` |
| 多少钱 | `PERSONA_MODEL_PRICE_TIER=peak` / `..._IN_PER_1M=0.3` / `..._OUT_PER_1M=1.2` / `..._SOURCE=…deepseek.com/quick_start/pricing` / `..._CHECKED_AT=2026-10-16` |
| 闸门 | `PERSONA_MODEL_MAX_COST=0.25` / `..._PER_TASK=0.25` / `..._MAX_CALLS=1` / `..._EST_TOKENS_IN=3000` / `..._EST_TOKENS_OUT=2000` / `PERSONA_MODEL_REQUIRE_GATE_VERDICT=true` |
| 门禁联动 | `PERSONA_RESEARCH_MAX_EXTRACTS=1`（启用模型 + gate=true 时该值必须 ≥1，否则加载期即拒） |

安全核验（脚本比对，非人工目视）：
- `.env` 内**不含**模型密钥 / 搜索密钥子串，也**没有任何** `..._API_KEY=` 赋值行；密钥仍只走进程环境变量。
- `git check-ignore -v .env` → `.gitignore:22:.env`；`git status --short` 里**不出现** `.env`。

`persona model plan` 已从"未启用"变为：

```
是否启用        : 是（PERSONA_MODEL_PROVIDER=deepseek）
服务商 / 模型   : deepseek / deepseek-flash
币种 / 档位     : USD / peak
单价（原币/1M） : 输入 0.3  输出 1.2
单价（账本/1K） : 输入 0.0003  输出 0.0012
单价来源        : …/quick_start/pricing（核对日期 2026-10-16）
模型密钥        : PERSONA_PROVIDER_API_KEY 已设置（内容不显示）
预算            : 总上限 0.25 | 单任务上限 0.25 | **生效上限 0.25** USD
调用次数上限    : 1
事前 token 估算 : 输入 3000 / 输出 2000
单次调用估算    : 0.0033 USD
gate 开关       : require_gate_verdict=是
```

### 9.2 `request_id` 修复（Provider 侧补填）

**选择**：让 **Provider 补填** `UsageDelta.request_id`，**不**让抽取器回退读 `raw_ref`。
理由：`raw_ref` 是"对账 / 报障"的**通用**字段，假设它等于 `request_id` 是把某家厂商的
约定泄漏进 L4 业务层，换 Provider 就错；"谁最清楚自己的服务端标识，谁负责填"。

- `persona/providers/deepseek.py`：新增 `_request_id_of(data)`（取响应顶层 `id`，
  口径与 `TavilyProvider._request_id_of` 一致：**没有 `id` → `None`，不填 `""`**）；
  `_usage_delta(raw, *, request_id=None)` 带上传入值；`_normalize` 传入。
  `ChatResponse.raw_ref` 行为不变。
- `extraction._settle()` **未改动**（它本来就读 `UsageDelta.request_id`，是上游没填）。
- 护栏测试 4 项（`tests/test_p4_4c_deepseek_provider.py`）：`UsageDelta` 带上顶层 `id`；
  缺 `id` → `None`；`usage` 缺失时标识仍留住；**端到端**账本记录的 `request_id` = 服务端 `id`。
- `docs/CONFIG.md`：§11.7.1 契约表补 `请求 id` 一行，新增 §11.7.7 记录本修复与取舍。

### 9.3 cache 计价：接受 miss 保守口径，登记为遗留

`docs/CONFIG.md` §11.7.5 已明确标注为**遗留项（已接受该口径，不再改）**，并写入重启条件：
出现"cache 命中比例高到费用被明显高估"的实测证据，或需要与账单**分毫对账**时才引入
cache 明细字段（加字段属改已验收结构，需另行授权）。在此之前一律按 miss 保守计价（高估、安全）。

### 附：本次运行如何复现

```bash
# 预检（不发请求）
python scripts/smoke_p4_4c.py --dry
# 真实调用 1 次（会产生费用）
python scripts/smoke_p4_4c.py
# 离线复算输出与门禁
python scripts/verify_p4_4c_outputs.py
```

前置条件：环境变量里有模型密钥（`PERSONA_PROVIDER_API_KEY` 或其别名 `DEEPSEEK_API_KEY`）。
模型段非敏感配置由 `scripts/smoke_p4_4c.py` 顶部的 `SMOKE_CONFIG` 显式注入。
