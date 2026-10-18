# P4.8 R4.2 真实检索报告（诊断性实验）

> **性质**：诊断性实验。目的是**拿到数据再决定修什么**，本轮不改任何生产代码。
> **时间**：2026-10-17 19:18:48 — 19:19:12（耗时 **24.13 s**）
> **目标**：福尔摩斯 + 华生 /《福尔摩斯探案集》/ 柯南·道尔原著（与 R4.1 同目标，便于逐项对照）
> **产物**：`%TEMP%\gps-p48-r42\real\`（隔离库 `persona.db` + `raw_responses.jsonl`）

---

## 0. 一句话结论

**D-6 + D-7 都验证通过，主路径的卡点从"门禁"前移到了"模型输出上限"**：
9 份材料全部进了抽取器、模型**真的被调用**、模型**真的返回了 21 条可用 claims**，
但输出撞满 `max_tokens=4096` 被截断，JSON 非法 → **整批作废** → 证据 0。

**0 证据的原因在证据层/模型调用层，不在门禁。**门禁这一次两边都放行了。

---

## 1. 实际调用 vs 清单

| 类别 | 清单 | 实际 | 判定 |
|---|---|---|---|
| 检索 `/search` | 2 | **2** | ✔ |
| 抓取 `/extract` | ≤1 | **1** | ✔ |
| 模型 `chat/completions` | ≤2（1 抽取 + 1 对话） | **2** | ✔ |
| HTTP 往返 | ≤4 | **4**（实际 5 笔：2 检索 + 1 抓取 + 2 模型） | ✔ |

**清单外调用 0**（自检 `[OK] 无清单外调用`）。配置零改动（`MAX_CALLS=3`、`MAX_EXTRACTS=1`）。

| # | 端点 | request_id | 结果 | 计费 |
|---|---|---|---|---|
| 1 | `/search` 福尔摩斯 | `7cb9bfac-6c49-42eb-8d25-785438e4737e` | 5 条候选 | 1.0 credit CONFIRMED |
| 2 | `/search` 华生 | `2c81135e-9f37-4b06-869d-5477c4273dea` | 5 条候选 | 1.0 credit CONFIRMED |
| 3 | `/extract` 百度百科 | `2b8d7433-cc59-42d9-99c5-11471c88e507` | **成功**，正文 2255 字 | 0 credit CONFIRMED |
| 4 | `/chat` 抽取 | `4f376698-32e8-4ee7-b617-bd7de37ce7aa` | 7758 in / **4096 out（截断）** | **0.0072426 USD** |
| 5 | `/chat` 对话 | `ac26c41c-2a83-4ae4-8d9e-dbbb84d806de` | 302 in / 11 out | 0.0001038 USD |

> 与 R4.1 对比：**这次抓取成功了**（R4.1 是 `Failed to fetch url`）。百度百科正文 2255 字，
> 直接进入材料流并通过正文门禁（VALID）。所以本轮的 0 证据**与抓取失败无关**。

---

## 2. 五阶段

| 阶段 | 状态 |
|---|---|
| RESEARCH | **EXECUTED** |
| EVIDENCE_EXTRACT | **SKIPPED**（见 §3：真实原因是 Err 被覆盖，不是"没东西可抽"） |
| PERSONA_DISTILL | EXECUTED |
| RELATIONSHIP_DISTILL | EXECUTED |
| DIALOGUE_RUN | EXECUTED |

整体 `PARTIAL`，任务本身 `COMPLETED`。

---

## 3. 重点五项

### ① 证据数 > 0？ → **否，0 条**

**归因：证据层 / 模型调用层，不是门禁。**

证据链在这一步断的：

```
9 份材料进抽取器（正文 VALID 1 + SUMMARY_ONLY 8）
  → 模型调用真的发出（request_id 4f376698）
  → 模型真的返回了内容（7948 字符，含 c1…c21 共 21 条完整 claims）
  → finish_reason = "length"，completion_tokens = 4096（撞满上限）
  → JSON 在 c21 中途被切断 → json.loads 抛 JSONDecodeError
  → Err(EXTERNAL_UNAVAILABLE, "输出不是合法 JSON：不把它当成『抽取成功但零结果』")
  → 21 条 claims 全部丢弃
```

被丢弃的 claims 质量是好的，举例：

- `c1` EXPERIENCE / VERBATIM：一八七七年"福尔摩斯侦探社"正式开业
- `c2` RELATION / VERBATIM：后来经济稍宽裕时与华生合租贝克街 221 号 B
- `c3` HABIT / VERBATIM：嗜好包括吸烟斗、拉小提琴、听音乐会，喜欢洗土耳其浴
- `c21`（半截）TRAIT / VERBATIM：福尔摩斯冷静、勇敢、智慧超群

维度覆盖 TRAIT / HABIT / EXPERIENCE / RELATION，**只要输出不被截断，traits > 0 是必然的**。

### ② traits > 0？ → **否，0**

两份档案均为 `INSUFFICIENT_EVIDENCE`，六个维度全 0。这是 ① 的直接后果。
关系档案 PAIR 七维度全空 + `open_questions=1`。

### ③ 繁体页面是否出现在来源里？ → **是** ✔ D-7 生效

`src_92580ba31efa410d8570fee17a04d181`
`「福爾摩斯的懸疑世界 說到福爾摩斯探案系列…」`（threads.com，801 字）

R4.1 里这条被 `CANDIDATES_IRRELEVANT` 误杀，本轮**正常登记为来源**。
且本轮 `CANDIDATES_IRRELEVANT` **拒收 0 条**（10 条候选全部相关）——归一没有把噪声放进来。

### ④ `external_usage_records` 是否 2 行 deepseek？ → **是** ✔ D-3 复核通过

库里 5 条逐笔：

| provider | operation | request_id | 状态 | 实际 | 单位 |
|---|---|---|---|---|---|
| tavily | SEARCH | 7cb9bfac… | CONFIRMED | 1.0 | TAVILY_CREDIT |
| tavily | SEARCH | 2c81135e… | CONFIRMED | 1.0 | TAVILY_CREDIT |
| tavily | EXTRACT | 2b8d7433… | CONFIRMED | 0.0 | TAVILY_CREDIT |
| **deepseek** | CHAT | 4f376698… | CONFIRMED | 0.0072426 | USD |
| **deepseek** | CHAT | ac26c41c… | CONFIRMED | 0.0001038 | USD |

未知费用笔数 **0**（本轮没有新增 UNKNOWN）。

### ⑤ `LOW_QUALITY` / `EMPTY` 是否仍被拒？ → **本轮无法证实**

如实说明：本轮 9 份材料里 **没有出现** `LOW_QUALITY` / `EMPTY`——正文抓取成功且通过门禁，
其余 8 份是 `SUMMARY_ONLY`。所以**没有样本可供验证**，不能拿"没发生"冒充"验证过"。

该项由离线测试覆盖，`tests/test_p4_8_summary_gate.py` 17 项全绿，其中：

- `test_failed_body_is_still_rejected` —— 正文判废仍拒收
- `test_mixed_batch_splits_by_verdict` —— 混合批次按结论分流
- `test_valid_body_is_admitted` —— VALID 放行

---

## 4. 费用

| 项 | 本轮 | 人工累计 |
|---|---|---|
| 检索 | **2 credits**（CONFIRMED） | 5 CONFIRMED + 1 UNKNOWN（R4 那笔，待核对账单） |
| 模型 | **0.0073464 USD** | **0.0173469 USD** |
| 合计 | 2 credits + 0.0073464 USD | 5(+1?) credits + 0.0173469 USD |

R4 那笔 UNKNOWN **保持原样**，等控制台核对结果再决定是否补正。

---

## 5. 本轮新发现的缺陷（**均未修，等拍板**）

### D-272 —— 主路径新卡点，已取代 D-6

`persona/modules/extraction.py:856` 硬编码 `max_output_tokens=4096`。
材料越多、claims 越多，越必然撞满 → 截断 → INVALID_JSON → **整批作废**。
本轮 9 份材料 / 7758 输入 token，输出正好卡在 4096。

**这条比 D-6 更致命**：D-6 至少不花钱（门禁挡住 → 不调模型），
D-272 是**花了 0.0072426 USD，买回 0 条证据**。

修法方向（未做）：

| 方案 | 做法 | 代价 |
|---|---|---|
| ① 分批 | 每批 N 份材料，多次调用 | 调用次数翻倍 → **超清单**，需重新授权 |
| ② 提高/动态化输出上限 | 按材料数估算 `max_output_tokens`（如 8192 或动态） | **零新增调用**，仍在清单内；需同步修 D-274 的估算 |
| ③ 限流 | 限制单批材料数 + 提示词限制条数 | 会丢材料，等于主动降质 |
| ④ 截断 salvage | JSON 不完整时抢救已解析部分 | 会引入"半截证据"，需要 tier 标注，风险最高 |

### D-273 —— 阶段级重试与幂等键冲突，失败原因被吞

`retry_policy.max_attempts = 3`。第一次抽取已经花钱并失败（Err），
重试时幂等键相同 → `MODEL_CALL_DUPLICATE_SKIPPED` → 返回 `Ok`（0 候选）
→ **阶段状态被覆盖成 `SKIPPED`，真正的 `EXTERNAL_UNAVAILABLE` 只留在 `TaskRecord.last_error`**。

去重本身是对的（没让我们再花一次钱），但**阶段视图里看不到失败原因了**——
只显示"SKIPPED / 0 候选"，很容易被误读成"没东西可抽"。

### D-274 —— 事前估算系统性偏低

抽取这一次：预估 0.0033 → 实际 **0.0072426**（**2.2 倍**）。
原因：`est_tokens_out` 的默认值远小于真实输出，而这次输出恰好撞满上限，偏差最大。
费用上限 0.25 未被击穿，所以没造成后果，但**估算不能当预算用的数字看**。

### D-275 —— 登记来源数 ≠ 唯一来源数（R4.1 已发现，仍未修）

阶段消息报"登记 **10** 条来源"，实际唯一来源 **8** 条（2 条按 canonical key 去重）。
R4.1 是 9 vs 7。同一个口径问题，两轮都在。

---

## 6. 建议

**先修 D-272，它是当前主路径上唯一的卡点。**其余三项都不阻塞产出。

倾向方案 ②（提高/动态化 `max_output_tokens`）：**同样的 2 次调用、同样的清单**，
只把输出上限打开，就能拿到那 21 条 claims——性价比最高，也不需要重新授权。
方案 ①（分批）能根治，但会把模型调用次数从 2 变成 2×批次数，必须重新批清单。

如果你倾向 ①，我会先出一份零成本的 dry-run 清单给你看次数和费用。

---

## 7. 附：本轮可复现的证据

- 原始响应：`%TEMP%\gps-p48-r42\real\raw_responses.jsonl`（5 次往返全量落盘）
- 隔离库：`%TEMP%\gps-p48-r42\real\persona.db`（`external_usage_records` 5 行）
- 失败原因原文：`TaskRecord.last_error.code = EXTERNAL_UNAVAILABLE`
- 截断证据：抽取响应 `choices[0].finish_reason = "length"`、`usage.completion_tokens = 4096`
