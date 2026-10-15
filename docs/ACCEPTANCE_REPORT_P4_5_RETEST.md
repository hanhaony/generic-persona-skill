# P4.5 受控真实 API 复测报告

> 项目：`generic-persona-skill`（`D:\AIprojects\generic-persona-skill`）
> 阶段：P4.5 复测（P4.5 验收报告 §8 提出的"仍需一次受控真实复测"）
> 时间：2026-10-15
> 授权范围：1 次 `/search` + 最多 2 次 `/extract`（合计 ≤ 3 次）
> 本轮真实请求：**3 次**（上限内用完）；真实模型 API：**0 次**
> 口径：**只报告实际验证过的结果**，模拟测试不写成"真实已验证"

---

## 0. 结论速览（按你问的六条逐条回答）

| # | 你问的 | 实测结果 |
| --- | --- | --- |
| 1 | 真实请求数、HTTP 状态 | **3 次，全部 HTTP 200**（1 次 `/search` + 2 次 `/extract`）。`max_retries=0`，无任何重试；第 4 次被脚本内硬熔断挡住（未发生） |
| 2 | `/search` 的 `request_id` 是否拿到 | **拿到了**：`30b9728a-ddc3-4bc6-9877-04276a6a2bf4`，并已写入逐笔账本 → **遗留 #2 的一半了结** |
| 3 | `/extract` 的 `usage.credits` 是否被正确读入账本 | **是**：两次真实响应 `usage={'credits': 0}` → 账本逐笔记 `cost_confirmed=0.0 / CONFIRMED / 来源 PROVIDER_USAGE / request_id 留存`。**估算（0.2）与实际（0）分开记，未被混用** |
| 4 | 质量门禁是否真的拦住了导航摘录 | **本次真实复测未复现该场景**（抓到的是 2,274 字纯散文短页，全文与摘录都判 `VALID`，材料正常交出 1 条）。但**0 请求复核发现判定对象错位**：门禁判"全文"、材料出口交"摘录"，在 P4.3 落盘的真实 314,743 字响应上，全文 `VALID` 而摘录 `LOW_QUALITY/NAV_DOMINATED`，**摘录照样进了材料**（见 §4，本次新发现缺陷） |
| 5 | 费用账本是否真实累加 | **是**：`requests=3`（SEARCH 1 / EXTRACT 2）、`cost_confirmed=1.0`、`cost_estimate=1.4`、`accountable=1.4`；逐笔落库 3 条，重复写入全部返回 `False`（幂等生效） |
| 6 | 是否仍有账户余额不可知等遗留 | **是，余额仍不可知**；extract 累计 4 次成功仍回报 `credits=0`；发布日期**真实确认官方不返回**；**失败/超时/未知费用路径本次一次都没触发**（3 次全成功），该逻辑仍只有模拟测试覆盖（见 §8） |

**一句话**：账本链路与 `/search` 契约在真实 API 上成立；**质量门禁"拦住导航摘录"这条没有被本次真实复测证成，反而暴露出它判的是错误的对象**。

---

## 1. 真实请求清单（3 次，全部 HTTP 200）

| # | 端点 | 请求参数 | HTTP | `response_time` | `usage` | `request_id` | 正文长度 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `POST /search` | `query="Alan Turing"`, `max_results=3`, `search_depth=basic`, `include_raw_content=false`, `include_usage=true` | **200** | 3.34 | `{'credits': 1}` | `30b9728a-ddc3-4bc6-9877-04276a6a2bf4` | —（3 条结果） |
| 2 | `POST /extract` | `urls=[chessprogramming.org/Alan_Turing]`, `extract_depth=basic`, `include_usage=true` | **200** | — | `{'credits': 0}` | `09401506-5408-4e4a-9c11-9e4507d71a9e` | 2,274 字 |
| 3 | `POST /extract` | 同 #2 + `query="Alan Turing biography"` + `chunks_per_source=3` | **200** | — | `{'credits': 0}` | `aec0406b-2774-4bad-896b-0c90f2c44df7` | 2,274 字（与 #2 逐字相同） |

- 三次均带 `Authorization: Bearer` 请求头；**密钥不进请求体**（离线验证请求体字段：不含 `api_key`）。
- 第 1、2 次由 `ResearchService.run` 发起（预留 → 发送 → 结算全流程）；第 3 次由复测脚本直接发起（同样先预留、后进账）。
- **无第 4 次请求**：脚本内计数器超过 3 即抛 `RuntimeError`。

---

## 2. `/search` 契约（本次真实确认，**了结 P4.5 遗留 #2 的一半**）

真实响应顶层字段：

```
answer, follow_up_questions, images, query, request_id, response_time, results, usage
```

| 项 | 真实结果 | 对遗留 #2 的处置 |
| --- | --- | --- |
| `request_id` | **存在**，取到 `30b9728a-…` 并已写入账本 | ✅ **已确认存在**（此前因上轮未落盘而无法自查） |
| 发布日期类字段 | 顶层与 `results[*]` 中**均无**（`results` 字段 = `content, id, raw_content, score, title, url`） | ✅ **已确认官方不返回** → 发布日期缺失是服务端事实，不是我方丢字段 |
| `usage.credits` | **1** | ✅ 真实确认"basic search = 1 credit/次"这条官方费率 |

> 发布日期缺失这条**由"无法确认"变为"确认官方不返回"**，是本次真实数据带来的确定结论。

---

## 3. `/extract` 的 `usage.credits` 入账（逐笔，真实）

| 请求 | 操作 | 结果 | `request_id` | 估算 | 实际 | 状态 | 来源 | 币种 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| #2 | EXTRACT | SUCCEEDED | `09401506-…` | 0.2 | **0.0** | CONFIRMED | PROVIDER_USAGE | TAVILY_CREDIT |
| #3 | EXTRACT | SUCCEEDED | `aec0406b-…` | 0.2 | **0.0** | CONFIRMED | PROVIDER_USAGE | TAVILY_CREDIT |
| #1 | SEARCH | SUCCEEDED | `30b9728a-…` | 1.0 | **1.0** | CONFIRMED | PROVIDER_USAGE | TAVILY_CREDIT |

**验证到的点**：

1. 真实 `usage.credits` 被**正确读入**账本（P4.3 时它是被整条丢掉的，本次 `requests` 与 `cost_confirmed` 都有值）；
2. **估算与实际分开**：extract 的真实回报 0，但账本估算列仍记 0.2（按官方"每 5 次成功提取 1 credit"），两者**没有互相覆盖**——这正是 P4.5 的核心语义修正，本次在真实响应上得到验证；
3. `request_id` 与 `idempotency_key` 双双留存，可直接拿去与服务端对账。

---

## 4. 质量门禁：本次实测 + 新发现的缺陷

### 4.1 本次真实抓取的结果（没有导航摘录可拦）

本次 `/search` 的 3 条结果按相关性排序为：

| 排序 | URL |
| --- | --- |
| 1（被抓取） | `https://chessprogramming.org/Alan_Turing` |
| 2 | `https://en.wikipedia.org/wiki/Alan_Turing` |
| 3 | `https://spartacus-educational.com/Alan_Turing.htm` |

`max_extracts=1` → 只抓了排序第 1 条（**不是** P4.3 那次抓到的 Wikipedia 长页）。抓到的正文 2,274 字，纯散文：

| 对象 | 门禁判定 | 度量 |
| --- | --- | --- |
| 全文（2,274 字） | `VALID` | prose=1,702 / nav=0.000 |
| 摘录（前 2,000 字） | `VALID` | prose=1,430 / nav=0.000 |
| 材料出口 snippet | `VALID` | 1 条材料，2,000 字 |

→ **本次没有出现"导航摘录"，门禁没有需要拦截的内容**。所以"门禁真的拦住导航摘录"这条**本次未能在真实数据上证成**（要复现需第 4 次请求针对长页，未授权，我不自行发起）。

### 4.2 但 0 请求复核发现：门禁判的是全文，材料出口交的是摘录（**新缺陷**）

代码事实（两处）：

- `ResearchService._gate_extract_quality()` 判的是 `item.content`（**全文**）；
- `materials_from_extracts()` 交出去的是 `snippet=item.excerpt`（**摘录**，= `raw[:2000]`）。

用**上轮落盘的真实 314,743 字响应**（0 请求）走这条链路，结果是：

| 对象 | 门禁判定 | 度量 |
| --- | --- | --- |
| 全文 314,743 字 | **VALID** | prose=86,212 / nav=0.788 |
| 摘录 2,000 字 | **LOW_QUALITY / NAV_DOMINATED** | prose=81 / nav=0.923 |
| 材料出口 snippet | **交出去了**（2,000 字），内容以 `[Jump to content](#bodyContent) [![](/static/images/icons/…` 开头 | LOW_QUALITY |

即：**门禁看的是全文（通过了），下游真正消费的是摘录（没通过也照样交出去）**。
对于"长页 + 开头是目录/导航"的页面——这正是 P4.3 真实暴露的那个场景——**导航摘录仍会作为材料流入 Claim Extractor**。

**为什么模拟测试没抓到**：P4.5 的 65 项测试里，构造 fixture 时 `content` 与 `excerpt` 用的是**同一段合成文本**（`excerpt=body[:2000]`，`body` 本身就是纯散文），两者质量必然一致，"判定对象 ≠ 出口对象"在测试里永远不可见。

### 4.3 官方补救参数的真实效果

请求 #3 带官方 `query` + `chunks_per_source=3`：

- 先离线（0 请求）确认**我方确实发出了**这两个参数（请求体含 `query`、`chunks_per_source: 3`，无 `api_key`）；
- 但真实响应 `raw_content` 与无参数时**逐字相同（2,274 → 2,274）**，摘录判定也都是 `VALID`。

→ 结论：在**这个短页**上官方分块参数**没有产生可观察的改善**（页面本身没有导航区可跳，而是短到不需要分块）。它在"长页导航摘录"场景上是否有效，**本次未验证**（需要 1 次针对长页的 `/extract`，未授权）。

---

## 5. 费用账本真实累加（隔离库核对）

`ResearchOutput.budget_used`：

```
requests        = 3
cost_estimate   = 1.4   （1.0 search + 0.2 + 0.2，按官方费率事前估算）
cost_confirmed  = 1.0   （search 真实回报 1 + extract 0 + extract 0）
currency        = TAVILY_CREDIT
accountable     = 1.4   （保守口径 = max(估算, 实际) 逐笔取大）
```

隔离库（`gps-p45-sr72yadi\p45.db`）落库 3 条，`external_usage_report`：

```
requests=3  confirmed=1.0  estimated=1.4  unknown=0  failed=0
by_operation={'SEARCH': 1, 'EXTRACT': 2}
```

| 核验项 | 结果 |
| --- | --- |
| 逐笔落库 | 3 条，均含 `request_id` / `idempotency_key` / 估算 / 实际 / 状态 / 来源 |
| **幂等**（把同样 3 条再写一遍） | 全部返回 `False` → **未重复计数** |
| 待人工核对（`cost_status=UNKNOWN`） | 0 笔（本次全部 CONFIRMED） |
| `should_pause(max_cost=5)` | `False`（未超上限、无未知费用） |

> P4.5 报告 §3 说的"逐笔 + 幂等 + 预留"在真实请求上成立。

---

## 6. 本次新发现的缺陷与建议的最小修法（**未改代码，等你定**）

| 项 | 内容 |
| --- | --- |
| 缺陷 | **门禁判定对象（全文）与材料出口对象（摘录）不一致** → 长页的导航摘录仍会进入材料 |
| 证据 | 代码两处 + 真实落盘数据 0 请求复核（全文 VALID / 摘录 LOW_QUALITY / 材料仍交出 2000 字导航摘录） |
| 漏过原因 | 测试 fixture 里 content 与 excerpt 同质，差异不可见 |
| 建议最小修法（三选一，等授权） | **A. 门禁改判"摘录"**（下游真正消费的对象）——改动面最小，直接满足"低质量正文不得进材料"；<br>**B. 摘录取"有正文的窗口"**而不是 `raw[:2000]`（治 P4.3 根因 #2，让长页也能产出可用摘录）；<br>**C. A+B**：先让摘录变成正文，再让门禁判摘录——这样既不漏过，也不会把已付费抓到的长页全部判废 |
| 我的建议 | **C**（只做 A 会让大量长页产出 0 材料 = 白花额度；只做 B 则门禁仍判错对象） |

**本阶段未改任何产品代码**（复测只做测量），等你验收后再决定是否动。

---

## 7. 本次**没有**验证到的部分（诚实清单）

| 项 | 状态 |
| --- | --- |
| 门禁"拦住导航摘录" | **未在真实数据上证成**（本次抓到的页面不含导航摘录） |
| 失败 / 超时 / 缺 `usage` → UNKNOWN 路径 | **真实环境一次都没触发**（3 次全 200），仍只有模拟测试覆盖 |
| 预算上限（`max_cost`）真实拦截 | 未触发（设为 5，实际用到 1.4） |
| 官方 `query`+`chunks_per_source` 对长页的效果 | 未验证（短页上无差异） |
| extract 的真实扣费粒度 | 仍为 0：本账户累计 4 次成功提取仍回报 `credits=0`，第 5 次才会触发 1 credit（官方口径） |
| 并发/连续请求绕过上限 | 预留逻辑在真实请求上走了，但没制造超限场景 |

---

## 8. 遗留问题（更新后）

1. **账户余额 / 剩余额度仍不可知**（API 不返回，无代码解）——记录为遗留，不处理。
2. ~~`/search` 是否有 `request_id`~~ → ✅ **本次确认有**；发布日期 → ✅ **确认官方不返回**（由"无法确认"变为确定结论）。
3. **请求级失败是否计费仍未知** → 代码一律按 UNKNOWN 处理（本次未触发）。
4. **credits → 货币换算不做**（公开价目表不是本账户单价）。
5. **`run_id` 字段预留未启用**（当前模型无研究运行 ID 概念）。
6. **跨进程并发未做锁**（记录已可按 `task_id` 汇总，但无跨进程互斥）。
7. **新增：门禁判定对象错位**（§6），待你决定是否修、怎么修。
8. 真实模型 API / P4.4 仍按要求暂停。

---

## 9. 证据落盘位置（可直接复核，无需再发请求）

| 文件 | 内容 |
| --- | --- |
| `C:\Users\36135\AppData\Local\Temp\p45_search_raw.json` | 第 1 次 `/search` 原始响应（落盘后才交给下游） |
| `…\p45_extract1_raw.json` | 第 2 次 `/extract` 原始响应（2,274 字） |
| `…\p45_extract2_raw.json` | 第 3 次 `/extract`（带官方参数）原始响应 |
| `…\p45_retest_output.txt` | 本次复测全过程输出 |
| `…\gps-p45-sr72yadi\p45.db` | 隔离库（逐笔账本 3 条 + 来源 / 实体记录） |

（复测脚本为一次性临时脚本，放在系统临时目录，**未写入仓库**。）

---

## 10. 纪律核对

| 项 | 结果 |
| --- | --- |
| 真实请求数 | **3 / 3 上限**，无重试、无第 4 次 |
| 真实模型 API | **0 次** |
| 密钥是否出现在输出 / 隔离库 | 均 **False** |
| 请求体是否含 `api_key` | **否**（仅 `Authorization: Bearer` 头） |
| 自动提交 Git | **无**（本阶段只新增本报告与临时脚本，产品代码零改动） |
| 删除 / 放宽测试 | 无 |

> ⚠️ 自查到的一个小问题：旁录里打印了 Authorization 头的**前 9 个字符**（`Bearer tv`），等于外露了密钥开头 2 个字符。密钥本体从未出现（`KEY in 输出` = False），且 Tavily 密钥前缀是可推测的通用前缀，不构成密钥泄露；但这一步本不该做，脚本已改为只截到 `Bearer `（7 字符）。本次已产生的输出文件里那 2 个字符如实告知，不做掩饰。

---

**结论**：账本链路、`/search` 契约、逐笔幂等在真实 API 上成立；
**"质量门禁拦住导航摘录"未被本次证成，且新发现门禁判错了对象**（§4/§6）。
本阶段未改产品代码，等你决定 §6 的修法后再动手。现停止，等待人工验收。
