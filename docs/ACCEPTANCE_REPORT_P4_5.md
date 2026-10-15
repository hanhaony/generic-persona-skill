# P4.5 验收报告 —— 用量账本与正文质量门禁

> 项目：`generic-persona-skill`（路径 `D:\AIprojects\generic-persona-skill`）
> 阶段：P4.5（继 P4.3 真实 API 冒烟之后）
> 环境：Python 3.13.12（受管运行时）
> 本阶段约束：**零真实 Search/Extract 请求、零真实模型调用**
> 结论口径：**只报告实际验证过的结果**

---

## 0. 结论（先说结果）

| 项 | 结果 |
| --- | --- |
| 真实 API 请求 | **0 次**（本阶段全部离线验证） |
| 真实模型 API | **0 次**（P4.4 及真实模型接入已按要求暂停） |
| 任务一 用量与预算账本 | **已实现并离线验证**（65 项新测试 + 全量回归） |
| 任务二 正文质量门禁 | **已实现并离线验证**，并用**上轮落盘的真实响应**做了 0 请求复核 |
| 仍需受控真实复测 | **是**（见 §8）—— 模拟测试不能替代真实计费验证 |

**两个根问题都已修到根因，不是绕过：**

- P4.3 真实暴露的两个缺陷（抓取用量丢失、摘录取导航区）现在都有**针对性回归测试**。
- 门禁在**上轮真实落盘的 314,743 字正文**上实测：全文 `VALID`、当前摘录 `LOW_QUALITY`。
  这是**真实内容上的验证**，不是只用合成数据自证。

---

## 1. 两个问题的根因与证据

### 1.1 根因一：抓取用量从不进账本（`budget_used: requests=0 cost=None`）

**证据**（P4.3 第 5 轮真实运行实测，HTTP 200 之后打印）：
```
budget_used: requests=0 cost=None currency=UNKNOWN     ← 尽管刚真实发过 1 次 /extract
```

**根因两处（均已定位到行）：**

| # | 位置 | 问题 |
| --- | --- | --- |
| 1 | `TopNExtractionStrategy.extract()` 末尾 `return Ok(out)` | 把 Provider 回报的 `UsageDelta`（含 `usage.credits`）**整个丢掉**，没往上传 |
| 2 | `ResearchService._maybe_extract()` | 拿到结果后只 `extend(output.extracted)`，**从不调用 `_add_usage()`** |

**后果**：`research.max_cost` 读的是 `output.budget_used`，抓取成本从不进账 →
费用上限对 `/extract` 是**瞎的**。`max_extracts=3` 时第 2、3 次抓取前的判断
永远基于"没增长的成本"，拦不住。

**修法**：策略 `return Ok(out, usage=response.usage)`；`_maybe_extract` 调用
`_add_usage(output.budget_used, result.usage)`。
**回归证据**：`test_extract_usage_reaches_research_output` 断言
`requests == 2`（search 1 + extract 1）、`cost_confirmed == 1.0`、`cost_estimate == 1.2`。

### 1.2 根因二：摘录取的是导航骨架，正文主体 0 字

**证据**（P4.3 落盘的真实响应，位置实测）：
```
content[0:120]    = [Jump to content](#bodyContent)  [![](/static/images/icons/...
content[800:920]  = ...Turingery](#Turingery) + [2.5 Delilah](#Delilah) + ...
content[1500:1620]= ...Further reading](#Further_reading) + [6.1 Articles](#Articles)...
首个传记性表述位置 = 23,206
Bletchley 首次出现 = 28,995
```
摘录 = 正文前 2,000 字 → **正文主体 0 字**。

**修法**：不是改摘录起点（那样治标），而是加**通用质量门禁**——
先判断"这段内容能不能用"，再决定它能不能变成研究材料。

---

## 2. Tavily 官方文档中实际确认的字段与规则

来源：`docs.tavily.com/documentation/api-reference/endpoint/extract` 与
`docs.tavily.com/documentation/api-credits`（2026-10-15 核对）。

### 2.1 已确认（有据可查）

**Extract 请求参数**：

| 参数 | 类型 / 默认 | 官方文档说明 |
| --- | --- | --- |
| `urls` | string / string[] **required** | 要提取的 URL |
| `extract_depth` | enum **默认 basic**（basic / advanced） | "basic extraction costs 1 credit per 5 successful URL extractions, while advanced extraction costs 2 credits" |
| `format` | enum **默认 markdown**（markdown / text） | text "may increase latency" |
| `query` | string | "User intent for reranking extracted content chunks" |
| `chunks_per_source` | int **默认 3**（1..5） | "Available only when `query` is provided" |
| `include_images` / `include_favicon` | bool 默认 false | — |
| `timeout` | float（1..60 秒） | basic 默认 10 秒 / advanced 默认 30 秒 |
| `include_usage` | bool **默认 false** | "The value may be 0 if the total successful URL extractions has not yet reached 5 calls" |

**Extract 响应字段**：`results[]`（url / raw_content / images）、`failed_results[]`、
`response_time`、**`usage`（`{credits: N}`）**、**`request_id`**（官方称"unique request identifier"）。

**计费规则（官方原文口径）**：
- **Search**：basic **1 credit/次**，advanced **2 credits/次**
- **Extract**：basic **每 5 次成功 URL 提取 1 credit**，advanced 每 5 次 2 credits
- **"You never get charged if a URL extraction fails"** ← 唯一可判"不计费"的官方依据
- 价目表：Free 1,000 credits/月；pay-as-you-go $0.008/credit

**鉴权**：`Authorization: Bearer <key>`（header / required）—— 与既有实现一致（P4.3 已改）。

### 2.2 无法确认（单独列出，不猜测）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| **Search 响应是否含 `request_id` / `published_date`** | **无法确认** | 官方文档页为 JS 渲染，多次抓取只得到标题；上轮真实响应**未落盘**，无法自查 |
| **请求级失败（超时/5xx/429）是否计费** | **无法确认** | 官方只说了"单条 URL 提取失败不收费"，**没说整个请求失败是否收费** |
| **credits → 货币的实际单价** | **无法确认** | 公开价目表不是本账户单价；本模块**不做货币换算**，一律以 credit 计量 |
| **账户余额 / 剩余额度** | **无法确认** | API 不返回；本阶段无任何可信账户凭据 |

> 以上四项**一律按"未知"处理**，代码中体现为 `cost_confirmed=None` +
> `cost_status=UNKNOWN` + 显式告警，**绝不用 0 代替**。

**本阶段实测补充**：上轮 `/extract` 真实响应里 `usage={'credits': 0}`，
与官方"未达 5 次不计费粒度会回报 0"的说明**一致**——这解释了为什么
"回报 0"不能当成"没花钱"，也是本轮把估算（0.2）与实际（0）分开的直接动因。

---

## 3. 账本数据模型与预留/结算流程

### 3.1 数据模型（新增，与既有账本并存）

**新表 `external_usage_records`（逐笔）** —— 采用**新增表**而不是改既有
`usage_records`：`CREATE TABLE IF NOT EXISTS` 的语义决定既有库不会自动获得新列，
新增表才能对旧库平滑生效（不删数据、不迁移）。

```
idempotency_key    TEXT PRIMARY KEY   ← 幂等主键：同一请求只记一笔
provider           TEXT NOT NULL      （tavily）
operation          TEXT NOT NULL      （SEARCH / EXTRACT）
outcome            TEXT NOT NULL      （SUCCEEDED / FAILED）
requested_at       TEXT NOT NULL
request_id         TEXT               服务端 request_id（失败时为 NULL）
unit_count         INTEGER            请求规模（search=1；extract=URL 数）
success_unit_count INTEGER            成功单元数
cost_estimate      REAL               估算（按官方费率）— 未知为 NULL
cost_confirmed     REAL               实际（响应 usage.credits）— 未知为 NULL
currency           TEXT               TAVILY_CREDIT / UNKNOWN
cost_source        TEXT               PROVIDER_USAGE / OFFICIAL_RATE / NONE
cost_status        TEXT               CONFIRMED / UNKNOWN / NOT_CHARGED
task_id / run_id / trace_ref
```

> `run_id` 字段已预留但**当前模型没有研究运行 ID 概念**，恒为 `NULL`（如实说明）。

**估算与实际分开**：这是本次的核心语义修正。此前 `cost_estimate` 被直接填成
**实际值** `usage.credits`——名字叫 estimate 却是实际值，导致：

- 请求**前**无法用它做预算判断（事前拿不到 credits）；
- 响应缺 `usage` 时只能填 `None`，而 `None` 又被闸门当成"不花钱"跳过。

### 3.2 流程：检查 → 预留 → 发送 → 结算/占位

```
① 检查   _cost_ceiling()  已耗 + 已占(预留+未知占位) + 本次预留 > max_cost → BUDGET_EXCEEDED
② 预留   ledger.reserve(key, 官方费率估算)          ← 请求**发出之前**
③ 发送   provider.search / extract（attach usage）
④ 结算   ledger.settle(record)  幂等；同一 key 只记一次
         ├─ 费用已知 → 预留释放，计入已确认
         └─ 费用未知 → 预留**不释放**，转为 unknown_hold 继续占额度
```

**为什么必须预留**：不预留的话，连续两次请求看到的都是"第一次花掉之前"的余额，
两次都能通过检查 = 绕过上限。预留发生在 `_cost_ceiling` 之后、请求之前。

**保守口径**：`accountable_cost() = max(估算, 实际) + 预留 + 未知占位`。
只看实际值会被"未达计费粒度回报 0"骗过去（上轮真实数据就是这个情况）。

---

## 4. 失败 / 超时 / 未知费用 / 幂等的处理

| 场景 | 记录 | 费用 | 额度处置 |
| --- | --- | --- | --- |
| 成功且响应带 `usage.credits` | SUCCEEDED，`request_id` 留存 | `CONFIRMED`（实际值） | 预留释放，计入已耗 |
| 成功但**响应无 `usage`** | SUCCEEDED | **`UNKNOWN`**（不填 0） | 预留转为**未知占位**，继续占额度 |
| **超时 / 断连** | FAILED，`request_id=None` | **`UNKNOWN`** | 预留转未知占位 |
| **服务端错误（5xx / 429 / 401）** | FAILED | **`UNKNOWN`** | 预留转未知占位 |
| 响应无法解析 | FAILED | **`UNKNOWN`** | 预留转未知占位 |
| **单条 URL 抓取失败** | SUCCEEDED（请求本身成功） | **`NOT_CHARGED`** ← 官方明确不计费 | 预留释放，计 0 |

**关键规矩：失败 ≠ 免费。** 请求失败时我们**无从得知**服务端是否已扣费，
所以费用是 `UNKNOWN` 且**预占不释放**，继续占用额度直到人工核对。

**保守暂停**：`ExternalUsageLedger.should_pause()` 在两种情况下停手：
① 会超成本上限；② 未知费用请求累计达上限（默认 1）→ 费用状态不明，停下来等人工核对。

**幂等**（三道）：
1. 客户端 `idempotency_key` = `sha256(provider|operation|归一化请求要素)[:32]`，
   **请求发出之前**生成（服务端 `request_id` 要等响应才知道，失败时更是拿不到）；
2. 内存账本 `settle()`：同 key 第二次直接忽略，不重复计；
3. 存储层 `idempotency_key` 主键：主键冲突视为已记过，返回 `Ok(False)`。

**人工核对流程**：
```
storage.external_usage_records(cost_status="UNKNOWN")   # 列出待核对项
   → 拿到账单 / 控制台数据
   → ledger.confirm(idempotency_key, cost, source="PROVIDER_USAGE")
   → 未知占位释放，转为已确认
```
记录里同时留着 `idempotency_key` 与 `request_id`，可直接与服务方对账。

**不伪造费用**：官方费率只用于**估算**（`cost_estimate`），
credits → 货币的换算**一律不做**（公开价目表不是本账户单价）。

---

## 5. 正文质量门禁与拦截位置

### 5.1 判据（通用，不绑定任何具体站点）

1. 正文为空 → `EMPTY`
2. 有效正文字数 < 可配置下限（默认 200）→ `LOW_QUALITY`
3. 无成段连续正文（最长连续正文段 < 200 字）→ `LOW_QUALITY`
4. 导航占比过高**且**正文只是"刚好够"→ `LOW_QUALITY`
5. 目标人物完全没被提到（**仅在有目标姓名时才判**）→ `LOW_QUALITY`
6. 页面类型是视频/社交 → `LOW_QUALITY`（这类页面没有可提的文章正文）

**判定顺序是关键**：先看"有没有能用的正文"，再看为什么不达标。
所以"页眉页脚很多但正文也很大段"的页面是 **VALID**（不能误杀），
而"只有导航"因为没有正文落进 `NAV_DOMINATED`。

**导航行识别**用**链接字符覆盖率 ≥ 50%**（而不是"行短"）——
真实的目录行往往很长（一行串起多个章节链接），只靠行短抓不住。

### 5.2 状态与拦截位置

```
Provider.extract
   ↓
_maybe_extract → _gate_extract_quality()   【标记】每段正文打上 VALID/LOW_QUALITY/EMPTY
   ↓                                      所有条目都留在 extracted（供诊断）
   ↓                                      不合格的另进 rejected_extracts（不静默丢弃）
_sanitize_extracted_for_output()          【物理阻断】不合格的 content 直接清空 ""
   ↓
materials_from_extracts()                 【出口闸门】只有 VALID 才变成 SourceMaterial
   ↓
Claim Extractor
```

**两道保证**：
- 出口清空：低质量正文在 `_sanitize_extracted_for_output` 被清空，
  下游就算拿到对象也取不到正文 → **物理上不可能据此生成 claim**；
- 材料闸门：`materials_from_extracts()` 只转 `VALID` 的，
  **未评估（quality=None）的也一律不交**（防止漏过门禁）。

### 5.3 在真实内容上的验证（0 请求）

用**上轮落盘的 P4.3 真实响应**离线复核（不是合成数据）：

| 片段 | 判定 | 度量 |
| --- | --- | --- |
| 真实全文（314,743 字） | **VALID** | prose_chars=86,212；nav_ratio=0.788 |
| 当前摘录（前 2,000 字） | **LOW_QUALITY / NAV_DOMINATED** | prose_chars=81；nav_ratio=0.923 |
| 正文主体段（23,206 起） | **VALID** | prose_chars=267 |

即：**门禁能在真实数据上正确区分"导航骨架"与"正文"**，
且不会误杀"导航多但正文也大段"的正常页面。

### 5.4 补救策略（受限，只走官方参数）

优先级与约束：

1. **`query` + `chunks_per_source`**（官方正式选项）——直接针对"拿到导航骨架"：
   官方说明它会"按与 query 的相关性重排内容块"，让 `raw_content` 变成
   最相关的若干块而不是页面开头。严格遵守官方约束
   （`chunks_per_source` 仅在提供 `query` 时可用，1..5）。
2. **`format=text`**（官方纯文本格式选项）。
3. **视频 / 社交页不补救** —— 没有可提的文章正文，补救只会白花额度。

补救**每次都要过预算检查并计入账本**（补救是另一次请求 → 另立一个幂等键 → 另记一笔账）。
补救方案数受 `max_options` 限制（默认 1），费用状态不明时停止。
**本阶段未实际执行任何补救请求**（0 次）。

### 5.5 诊断信息（可区分三类问题）

`RejectedExtract` 区分：

| 情况 | 特征 |
| --- | --- |
| **API/抓取层失败**（目标网页不可访问） | `extract_error` 有值 |
| 服务端返回空内容 | 状态 EMPTY 且 `extract_error=None` |
| **本地门禁拒收** | 状态 LOW_QUALITY，带 reasons 与度量 |

诊断里保存 `status / reasons / prose_chars / nav_line_ratio / longest_prose_run /
page_type / sample`，**不含 API 密钥，不含无关个人数据**。

---

## 6. 修改文件

### 新增
| 文件 | 说明 |
| --- | --- |
| `persona/core/usage_ledger.py` | 逐笔用量账本：模型外的规则、官方费率估算、幂等键生成、预留/结算/释放/暂停 |
| `persona/modules/extract_quality.py` | 正文质量门禁 + 页面类型识别 + 受限补救方案 |
| `persona/storage/schema.py`（改） | 新增 `external_usage_records` 表与 2 个索引 |
| `tests/test_p4_5_usage_quality.py` | **65 项**新测试 |

### 修改（均为**向后兼容的可选扩展**，不改既有行为）
| 文件 | 改动 |
| --- | --- |
| `persona/core/types.py` | `ExternalUsageRecord` / `ExternalUsageTotals` 数据形状；`BudgetUsage` 增 `cost_confirmed` 与 `accountable_cost()` |
| `persona/core/result.py` | `UsageDelta` 增 `cost_confirmed` / `cost_status` / `cost_source` / `request_id`（**`cost_estimate` 语义改为"估算"**） |
| `persona/providers/base.py` | `ExtractRequest` 增 4 个官方参数（默认 `None` = 不发送） |
| `persona/providers/tavily.py` | 逐笔记账（成功/失败两条路径）、`request_id` 留存、官方参数、失败记 UNKNOWN、全部 URL 失败记 NOT_CHARGED |
| `persona/modules/research.py` | 抓取用量入账、预留/结算、质量门禁标记、出口清空、材料闸门、持久化端口、两条费用告警 |
| `persona/storage/sqlite.py` | `record_external_usage`（幂等）/ `external_usage_report` / `external_usage_records` |
| `tests/test_p4_2_tavily_compliance.py` | 5 个计费用例按新语义**加强**断言（同时验估算+实际+状态+来源）；新增 2 项 |
| `tests/test_research_module.py` | metadata 透传断言细化（+ 幂等键）+ 新增 2 项幂等键用例 |

### 未改动
- 既有 `usage_records` 表（新增独立表，不迁移、不删数据）
- `config/` 配置层、Orchestrator、`Evidence`、`extraction`(Claim Extractor) 本体

---

## 7. 测试命令与结果

```bash
# ① P4.5 新增套件（全离线）
python -m unittest tests.test_p4_5_usage_quality
# => Ran 65 tests … OK

# ② P4.2/P4.3 既有套件（含按新语义加强的计费用例）
python -m unittest tests.test_p4_2_tavily_compliance
# => Ran 52 tests … OK

# ③ Research 模块（含 Mock Research 回归）
python -m unittest tests.test_research_module
# => Ran 52 tests … OK

# ④ 全量回归
python -m unittest discover -s tests -t .
# => Ran 858 tests … OK
```

**回归范围确认**（均通过，未删除/放宽/绕过）：

- Mock Research（`test_research_module`、`test_provider_mock`）
- G-2（`test_g2_materials_to_claims` 系列）
- G-4、H-10（`test_h10_e2e` 等）
- 任务生命周期（`test_orchestrator_*`、`test_task_lifecycle*`、`test_checkpoint*`）
- 依赖规则 / 密钥规则 / 配置规则（`test_dependency_rules`、`test_config`）

**未采用的"通过手段"**（明确声明）：
- 未删除任何测试；
- 未放宽任何断言（5 个计费用例改为**同时**校验估算、实际、状态、来源，比原来更严）；
- 测试用的正文**全部是合成内容**，按结构构造（导航行 = markdown 链接条目；
  正文 = 连续句子），**刻意没有硬编码 P4.3 那次真实网页的内容**——
  用真实内容写断言等于把"本次这个网页能过"当成"门禁能用"。

---

## 8. 受控真实 API 复测：已执行（结论见单独报告）

复测已于 2026-10-15 执行（1 次 `/search` + 2 次 `/extract` = 3 次真实请求，全部 HTTP 200），
结论见 **`docs/ACCEPTANCE_REPORT_P4_5_RETEST.md`**。

要点：账本链路、`/search` 契约（含 `request_id` 真实拿到）、逐笔幂等均**成立**；
但**"质量门禁拦住导航摘录"未被证成**，且复测新发现"门禁判全文、材料交摘录"的判定对象错位缺陷。

**（以下是复测前的原文，保留以说明当时的判断依据）**

**需要。** 理由与范围：

| 项 | 为什么模拟测不够 |
| --- | --- |
| **真实计费是否已按预期入账** | 模拟响应里的 `usage.credits` 是我们自己写的，不能证明真实回报被正确读取 |
| **`request_id` 真实取值** | 上轮只在 `/extract` 上见过；`/search` 侧**未落盘**，无法确认 |
| **官方 `query`+`chunks_per_source` 补救是否真能改善正文** | 这是**官方参数效果**问题，只有真实请求才能验证（文档只说了它会重排内容块） |
| **credits 的实际扣减粒度** | 上轮回报 0，真实扣减未知；复测可再取一次 `usage.credits` 交叉验证 |

**建议的复测范围（需你另行授权，我不自行发起）：**
1 次 `/search` + 最多 2 次 `/extract`（同一页面：1 次原始 + 1 次带官方
`query`+`chunks_per_source` 的补救），合计 ≤ 3 次真实请求；
`max_retries=0`、不调真实模型、结果写入隔离临时库。

**到那时才能说的**："真实计费入账已验证"。
**现在只能说**："链路、模型、规则、幂等、闸门逻辑均已离线验证，
并在上轮落盘的真实响应上验证了门禁判定"。

---

## 9. 遗留问题

1. **账户余额 / 实际单价仍未知**（API 不返回，无代码解）。
2. **Search 响应的 `request_id` / `published_date` 无法确认**（文档页 JS 渲染 + 上轮未落盘原始响应）。
   → 建议复测时把 `/search` 原始响应也落盘。
3. **请求级失败是否计费未知** → 一律按 UNKNOWN + 预留占位处理。
4. **`run_id` 字段预留但未启用**（当前模型无研究运行 ID 概念）。
5. **并发是"逻辑并发"**：预留/占位在内存账本内保证连续请求与并发预留不被绕过；
   跨进程并发需以存储账本为准（记录已落库、可按 `task_id` 汇总），
   但**尚未做跨进程锁**。
6. **真实模型 API 与 P4.4 按要求暂停**，未接入。
7. **补救策略尚未真实执行过**（本阶段 0 次请求）。

---

**验收口径重申**：本阶段**零真实请求、零模型调用**；
所有结论来自离线测试 + 上轮落盘真实响应的 0 请求复核；
不把模拟测试描述成"真实计费已验证"。现停止，等待人工验收。
