# P4.3 验收报告 —— Tavily 真实 API 受控冒烟测试

> 项目：`generic-persona-skill`（路径 `D:\AIprojects\generic-persona-skill`）
> 阶段：P4.3（继 P4.2 验收通过之后）
> 环境：Python 3.13.12（受管运行时）
> 结论口径：**只报告实际执行并验证的结果**

---

## 结论（先说结果）

**真实 API 请求累计 3 次，全部 HTTP 200**（`POST /search` 1 次 + `POST /extract` 2 次）。

- **搜索：成功**（见 §1–§4）。
- **正文提取：成功，内容级核验全部完成**（见 §5.2/§5.3）——真实抓到 314,743 字正文，
  截断为 2,000 字摘录，可追溯至来源，全文未落库，合规钩子执行 8 条。
- **其中第 1 次 `/extract` 的 API 调用本身是成功的**，但我的验证脚本在下游崩溃，
  真实正文未落盘即丢失（责任在脚本，见 §5.4）；修复后用第 2 次请求补齐，
  两次均在你的授权范围内（"补做 1 次" + "放行 1 次"）。
- **真实暴露 2 个问题**：① 抓取用量（含真实 credits）从不进入 `budget_used` 账本，
  导致 `research.max_cost` 对 `/extract` 失效（§5.5）；② 摘录按"正文前 2000 字"截取，
  真实抓到的是**导航/目录区，正文主体 0 字**（§5.6）。**均未擅自修改产品代码**。

全程未调用任何真实模型，未自动重试，每次均受 1 次硬上限熔断保护。

---

## 1. 第一步：本地预检查（发请求前，全部通过）

| # | 检查项 | 结果 |
| --- | --- | --- |
| 1 | Provider 配置 / 请求参数 / 响应解析 vs 官方规范 | **通过**（鉴权已于上一轮按官方规范改为 `Authorization: Bearer` 头） |
| 2 | `TAVILY_API_KEY` 是否存在且非空 | **通过**：User 级 `set=True`，非空。**全程只探测存在性，未读取/打印内容** |
| 3 | 预算 / 请求次数 / 正文提取限制是否生效 | **通过**（上一轮修复后离线实测：两道闸门均可拦截） |
| 4 | 隔离测试存储 | **通过**：`SqliteStorage(path)` 指向 `tempfile.mkdtemp(prefix="gps-p43-")` |
| 5 | 停止条件 | **未命中**，进入第二步 |

> 说明：密钥由你用 `setx` 写入 **User 级**环境变量。本进程启动早于 `setx`，故 Process 级为空；
> 冒烟脚本从 **User 级注册表**读取并注入进程内存，从未打印、从未落盘。

本次冒烟采用的硬护栏（经配置装配，非硬编码）：
```
offline_only=false, provider=tavily
research.max_queries=1        # 只计划 1 次检索
research.max_results_per_query=3
research.max_extracts=0       # 不做正文提取
research.max_cost=2           # 成本上限（单位=credit）
ProviderConfig(max_retries=0) # 失败即停，不重试
```

## 2. 第二步：一次真实搜索（实发 1 次请求）

- 接口：`POST https://api.tavily.com/search`（官方当前端点），鉴权走 `Authorization: Bearer`。
- 查询：公开人物 **Alan Turing** 的基础传记（`topic="biography"`），**非敏感个人信息、非批量**。
- 请求次数：**1**（经直通记录器旁录确认，未拦截、未伪造，真实 `urlopen` 发出的请求）。
- 未做正文提取；未调用任何模型 API；失败即停（`max_retries=0`）。

## 3. 第三步：真实结果检查

| 验证项 | 实测结果 | 判定 |
| --- | --- | --- |
| 调用确实成功而非 Mock | Provider 类型为 `TavilyProvider`（非 Mock、未套离线护栏）；真实 `urlopen` 打到 `api.tavily.com`；**HTTP 200**；返回的是真实站点的实时结果 | 通过 |
| 符合 Provider 数据契约 | 3 条 `SearchResult`（`max_results_per_query=3` 生效），均有 title/url/canonical_key/snippet | 通过 |
| URL / 标题 / 采集时间 / 发布日期 | url 与 title 已落库；`created_at=2026-10-15T12:59:11Z`（采集时间已存）；**`published_at` 三条均为 `None`** | 部分（见 §4.4） |
| 搜索摘要 vs 网页正文区分 | 材料只带 `snippet`；`extracted=0`；`Source.content_ref=None`（全文不落库） | 通过 |
| 正文摘录可追溯至来源 | **未执行**（未做提取，无正文可追溯） | 不适用 |
| ComplianceHook 实际执行 | 合规日志 **5 条**（对应 5 个检查点），结论均为 `ALLOW / rule=default` | 通过 |
| 写入隔离存储 | 临时库 `gps-p43-d2tgc_2y\p43.db`；未触碰正式画像/证据/长期记忆 | 通过 |
| 日志不含密钥 | 自检：密钥串是否出现在本次全部输出中 → **False** | 通过 |
| 未标记为已验证事实 | `EvidenceItem` 数 = **0**（Research 只登记来源，不产出证据） | 通过 |

**落库的三条来源（元数据，全文未落库）：**
```
1) https://en.wikipedia.org/wiki/Alan_Turing
   title=Alan Turing - Wikipedia            published_at=None  content_ref=None
2) https://www.britannica.com/biography/Alan-Turing
   title=Alan Turing | Biography, ... | Britannica   published_at=None  content_ref=None
3) https://www.youtube.com/watch?v=gtRLmL70TH0
   title=Alan Turing - Celebrating the life of a genius  published_at=None  content_ref=None
```

**用量（来自真实响应的 `usage.credits`）：** `requests=1, cost=1.0, currency=TAVILY_CREDIT`
—— 即 1 次 basic 搜索实际消耗 **1 credit**（成本闸门记账生效，未触发上限）。

## 4. 第四步：报告

### 4.1 真实请求是否成功、请求数量
**成功。真实 HTTP 请求数 = 1**（`POST /search`，HTTP 200）。模型调用 0 次。

### 4.2 搜索与正文提取分别是否成功
- **搜索：成功**（3 条结果，已登记来源）。
- **正文提取：未执行。** 依据第二步第 4 条——"若正文提取会产生额外费用，先确认当前账户额度和请求成本能够满足限制，否则只测试搜索"；
  **账户额度无法从可信账户信息确认**（响应不含余额字段），因此**只测搜索**，未扩大范围。
  后经你授权，已在补做轮实发 1 次 `/extract` —— **见 §5**（API 调用成功，但内容级核验因脚本崩溃未完成）。

### 4.3 实际调用的接口类型、HTTP 状态 / 脱敏错误类别
`POST https://api.tavily.com/search` → **HTTP 200**（无错误类别）。

### 4.4 返回字段与来源追溯检查结果
- `results[]` 的 `title` / `url` / `content` 三项**在真实响应中确认存在**且与解析一致。
- **新发现（重要）**：`published_date` 在本次真实响应中**未出现**，三条 `published_at` 均为 `None`。
  此前官方文档页为 JS 渲染无法抓取字段表，现已由真实响应确认：**默认 general topic 不返回发布日期**，
  因此"可用的发布日期"当前**无法回填**（代码行为正确——缺省留 `None`，未伪造）。
  若业务需要发布日期，后续需评估改用 `topic="news"` 或改从 `/extract` 结果、页面元数据获取。
- 追溯性：`Source` 行带 url/title/`created_at`；材料带 `source_id` + url；`content_ref=None`。

### 4.5 账户费用或额度
- **本次请求实际消耗：1 credit**（来源：真实响应 `usage.credits`，可信）。
- **补做轮 `/extract` 两次回报的 `usage.credits` 均为 0**（见 §5.2）—— 按官方口径这是
  "未达计费粒度"的回报值，**不等于免费**，实际扣减未知。
- **账户余额 / 剩余额度：未知** —— 响应不含余额信息，且我无任何可信账户凭据，**不做估计**。
  注册赠送 1,000 credits/月属官方公开口径，**不是本账户实际数值**。

### 4.6 测试数据保存位置及隔离方式
- 路径：`C:\Users\36135\AppData\Local\Temp\gps-p43-d2tgc_2y\p43.db`
- 隔离方式：`SqliteStorage(path)` 路径注入 + `tempfile.mkdtemp(prefix="gps-p43-")`，
  与正式库完全分离；`project_id="prj_p43_smoke"`；未写入正式人物画像、证据或长期记忆。
- 该临时目录**保留未删**，便于你复核；确认后可自行清理。

### 4.7 本次测试的限制和剩余问题
1. **只验证了连通性与数据契约**：1 次成功**不代表研究质量通过**，**更不代表真实语义分析通过**（本阶段未接任何真实模型）。
2. **正文提取已完成**：API 与内容级核验全部真实验证通过（见 §5.2/§5.3）。
   但真实跑出两个问题：**抓取用量不进账本**（§5.5）与**摘录取的是导航区**（§5.6）。
3. **本次成功只代表连通性与数据契约通过**：1 次抓取成功**不代表研究质量通过**，
   **更不代表真实语义分析通过**（本阶段未接任何真实模型）。§5.6 恰恰说明
   "抓到正文"≠"抓到可用内容"。
3. **发布日期无法回填**：真实响应无 `published_date`（见 §4.4），这是真实发现而非代码缺陷。
4. **费用上限未被真实触发**：本次成本 1.0 < 上限 2.0，闸门"能拦"此前由离线用例验证，**未在真实请求中实际触发过**。
5. **单次样本**：未覆盖限流(429)/超时/坏响应等真实错误路径（按约定不重试、不扩大）。
6. **成本单位是 credit 而非货币**（`currency=TAVILY_CREDIT`），按金额设限仍需你提供每 credit 单价。
7. **keyless 模式未使用**，仍属待你授权的备选路径。

### 4.8 相关测试命令及结果
```bash
# ① 本次受控冒烟（真实请求，仅 1 次）
#    脚本位于系统临时目录，未写入仓库：
#    C:\Users\36135\AppData\Local\Temp\p43_smoke.py
PYTHONPATH="D:/AIprojects/generic-persona-skill" python C:/Users/36135/AppData/Local/Temp/p43_smoke.py
# => HTTP 200；1 次调用；3 条来源；cost=1.0 TAVILY_CREDIT；密钥未出现在输出

# ② 离线全量回归（本轮未改产品代码，沿用上一轮结果）
python -m unittest discover -s tests -t .          # => 789 tests OK

# ③ 密钥存在性探测（只输出布尔值）
python -c "import os;[print(n, bool(os.environ.get(n))) for n in ['TAVILY_API_KEY']]"

# ④ 补做轮：真实 /extract（1 次，脚本在临时目录，未写入仓库）
PYTHONPATH="D:/AIprojects/generic-persona-skill" \
python C:/Users/36135/AppData/Local/Temp/p43_extract_smoke.py
# => HTTP 200；1 次调用；usage={'credits': 0}；failed_results=[]；results=1 条
#    随后脚本下游崩溃（已修，见 §5.3）

# ⑤ 补做轮的离线自检（0 次请求，用形状一致样本跑通下游）
PYTHONPATH="D:/AIprojects/generic-persona-skill" \
P43_REPLAY="C:/Users/36135/AppData/Local/Temp/p43_shape_sample.json" \
python C:/Users/36135/AppData/Local/Temp/p43_extract_smoke.py
# => 下游全绿：截断/落库/追溯/不落全文/合规 8 条，均不触网

# ⑥ 第二次真实 /extract（你授权的 1 次），响应先落盘再处理
PYTHONPATH="D:/AIprojects/generic-persona-skill" \
P43_SAVE="C:/Users/36135/AppData/Local/Temp/p43_extract_raw.json" \
python C:/Users/36135/AppData/Local/Temp/p43_extract_smoke.py
# => HTTP 200；1 次调用；正文 314,743 字 → 摘录 2,000 字；truncated=True
#    DerivedArtifact 1 条（无 content 字段）；EvidenceItem=0；追溯=True
#    全文未落库（字节探针）=True；合规 8 条 ALLOW；密钥未出现=False(即未出现)
#    budget_used: requests=0 cost=None currency=UNKNOWN  ← §5.5 缺陷证据
```

## 5. 补做轮：真实 `/extract`（共 2 次尝试，均 HTTP 200；第 2 次完成全部核验）

### 5.1 做法与边界

| 项 | 做法 |
| --- | --- |
| `/search` | **不重发**。回放上一轮真实搜索落到隔离库里的 3 条来源元数据（**0 次请求**） |
| `/extract` | 走**真实** `TavilyProvider`，**每次运行 1 次请求**；共运行 2 次（1 次崩溃 + 1 次你授权的重发） |
| 硬熔断 | 记录器内计数，**第 2 次调用直接抛 `RuntimeError`**，绝不多花 credit |
| 上限 | `research.max_extracts=1`、`research.max_cost=2`、`max_retries=0` |
| 防污染 | `tempfile.mkdtemp(prefix="gps-p43x-")` 隔离库，`project_id="prj_p43_smoke"` |

> 排序为确定性：3 条来源按 URL 稳定排序，`max_extracts=1` 只取第 1 条
> （`en.wikipedia.org/...`，文本型条目，非视频）。该上限同时被真实验证——
> **3 条候选只产生 1 次请求，未发生 3 次**。

### 5.2 真实调用结果（全部为实测值）

```
POST https://api.tavily.com/extract -> HTTP 200
Authorization 头存在 = True（前缀 'Bearer '，密钥本体未记录）
真实 HTTP 调用次数 = 1（上限 1）
原始响应已落盘：C:\Users\36135\AppData\Local\Temp\p43_extract_raw.json（322,744 字节）
```

**旁录到的真实响应形状（非 Mock）：**

| 层 | 字段 / 值 |
| --- | --- |
| 顶层 | `failed_results`、`request_id`、`response_time`、`results`、`usage` |
| `results[0]` | `images`、`raw_content`、`title`、`url` |
| `results` 条数 | **1** |
| `failed_results` | **`[]`（空，抓取成功）** |
| `usage` | **`{'credits': 0}`** |
| 发布日期类字段 | **`[]`（无）** —— 与 `/search` 一致，官方两个端点都不返回发布日期 |

与官方契约核对：`results[].url` / `results[].raw_content` / `failed_results[]` 三项
与官方文档一致，**响应解析实现方向正确**。

**真实抓到的正文（Wikipedia 全文）：**

| 项 | 实测值 |
| --- | --- |
| 目标 URL | `https://en.wikipedia.org/wiki/Alan_Turing` |
| 标题 | `Alan Turing - Wikipedia` |
| **正文长度** | **314,743 字** |
| **摘录长度** | **2,000 字**（>2000 截断生效，`truncated=True`） |
| 采集时间 | `extracted_at=2026-10-15T13:50:05Z` |
| 发布日期 | `published_at=None`（官方不返回，未伪造） |
| 访问状态 / 注入检测 | `access_hint=open`、`flagged_untrusted=False`、`error=None` |

摘录开头（真实内容，含 Wikipedia 页面导航结构）：
```
[Jump to content](#bodyContent)  [![](/static/images/icons/enwiki-25.svg)  ![Wikipedia](...
```

### 5.3 内容级核验（真实正文 + 真实存储，全部实测）

| 核验项 | 实测结果 | 判定 |
| --- | --- | --- |
| 非 Mock | 真实 `urlopen` 打到 `api.tavily.com`，HTTP 200，返回 314,743 字真实百科正文 | 通过 |
| 符合数据契约 | `ExtractResult(url/title/raw_content/excerpt/truncated)` 齐备 | 通过 |
| 正文摘录可追溯至来源 | **True**（摘录 URL 与已登记 `Source.url` 一致） | 通过 |
| 只落元数据+摘录，不落全文 | `DerivedArtifact` 1 条：摘录 2,000 字、记录内**无 `content` 字段**；<br>字节级探针 `content[2100:2160]` 在库文件中搜索 → **True（未落全文）** | 通过 |
| `Source.content_ref` | 三条均为 **None** | 通过 |
| 摘要 vs 正文区分 | 材料 `snippet` 与正文 `excerpt` 为两个独立字段；本轮 replay 的 snippet 为空，excerpt 长 2,000 | 通过（见备注） |
| ComplianceHook 实际执行 | **8 条**，全部 `ALLOW / rule=default`（含 1 条抓取后 13:50:05Z） | 通过 |
| 未标记为已验证事实 | `EvidenceItem` 数 = **0** | 通过 |
| 日志不含密钥 | 自检：密钥串是否出现在本次全部输出中 → **False** | 通过 |
| 写入隔离存储 | `…\Temp\gps-p43x-_4vhhdde\p43x.db`，未触碰正式画像/证据/长期记忆 | 通过 |

> 备注：本轮 `/search` 是回放（0 次请求），所以 `snippet` 为空；"摘要 vs 正文"的
> 区分是**结构上的**（两个不同类型、不同字段），且本轮 excerpt 的内容来源是 `/extract` 真实正文。

### 5.4 第一次尝试的崩溃（已闭环，记录留痕）

第一次 `/extract`（HTTP 200）后，脚本在打印响应形状之后崩溃，真实正文未落盘即随进程丢失。
崩溃点与修复：

| # | 缺陷 | 性质 | 状态 |
| --- | --- | --- | --- |
| 1 | `say("...%s" % [...])` 字符串与列表拼接 → `TypeError` | 纯脚本 bug | 已修（改 f-string） |
| 2 | `storage.put(kind="ResearchExtract")` —— 该 kind **不在 `ENTITY_KINDS` 白名单** → `VALIDATION.unknown_kind` | 脚本用错种类（产品闸门**正确拦截**） | 已修（改用已注册的 `DerivedArtifact`） |

两个缺陷都是**在第二次真实请求之前**用离线回放发现的：先把脚本改成「响应返回**立刻**落盘
（`P43_SAVE`）+ 支持 `P43_REPLAY` 离线回放」，再用形状一致样本（4380 字）**0 次请求**跑通全链路，
确认全部核验项变绿后才发第二次请求。因此第二次一次通过，未再浪费请求。

### 5.5 真实暴露的产品缺陷：抓取用量未进账本

实测到 `ResearchOutput.budget_used` 为：

```
requests=0  cost=None  currency=UNKNOWN     ← 尽管刚真实发出过 1 次 /extract
```

根因两处（均已定位到行）：

1. `TopNExtractionStrategy.extract()` 末尾 `return Ok(out)` —— **把 Provider 回报的
   `UsageDelta`（含 `usage.credits`）整个丢掉了**，没有往上传；
2. `ResearchService._maybe_extract()` 拿到结果后只 `extend(output.extracted)`，
   **从不调用 `_add_usage()`**。

后果：`research.max_cost` 的闸门读的是 `output.budget_used.cost_estimate`，
而抓取成本从不进账本 → **费用上限对 `/extract` 是瞎的**。本次因 `max_extracts=1`
且官方回报 0 credit 而侥幸无碍；若放开到 `max_extracts=3`，第 2、3 次抓取前的
`_cost_ceiling()` 将永远基于"未增长的成本"判断，拦不住。

**我没有自行修改产品代码**，等你授权后再动（建议改法见 §7）。

### 5.6 真实暴露的第二个问题：摘录取的是导航区，不是正文

对真实落盘的 314,743 字正文做了位置实测：

| 取样 | 内容 |
| --- | --- |
| `content[0:120]` | `[Jump to content](#bodyContent)  [![](/static/images/icons/enwiki-25.svg) …` |
| `content[800:920]` | `…Turingery](#Turingery) + [2.5 Delilah](#Delilah) + [2.6 Early computers…` |
| `content[1500:1620]` | `…Further reading](#Further_reading) + [6.1 Articles](#Articles)…` |
| **首个传记性表述位置** | **23,206**（`was a` / `born`） |
| `Bletchley` 首次出现 | 28,995 |

结论：**现在"摘录 = 正文前 2,000 字"的做法，抓到的全是目录/导航骨架，正文主体 0 字**。
本次摘录里没有任何一句关于 Turing 的传记内容。

这直接影响你列为"中"优先级的**真实 claim extractor**：若照现状把摘录喂给抽取器，
它拿到的是导航菜单而不是正文。**这是摘录策略问题，不是 `/extract` 接口问题**
（接口本身如实给了全文）。

可选改法（**未实施，等你决定**）：
1. 摘录改为**取正文主体区间**（跳过导航/TOC），例如从首个实质性段落起截断；
2. 或把"全文不落库"改为"存全文引用 + 摘录单独生成"，让摘录由下游按语义生成；
3. 或在 `ExtractedContent` 上标记 `excerpt_is_boilerplate=True`，让下游知道不可直接当证据。

顺带一个成本侧的观察：单页 314,743 字意味着**全文留在内存**时体量不小，
当前的"全文只留在内存、不落库"策略是对的，但也说明后续若要批量抓取需要单独评估内存与成本。

---

## 6. 三轮合计：是否修改了仓库文件

**三轮均未修改任何产品代码**（只执行受控真实请求、写报告、写/改系统临时目录下的验证脚本）。

上一轮的修复（鉴权改 Bearer 头、`usage.credits` → `cost_estimate`、`research.max_cost` 接线）
在三轮真实请求中**均得到真实验证**：两个端点都带 Bearer 头成功（HTTP 200），
`/search` 的成本（1.0 credit）真实记进账本。
补做轮还额外真实验证到 `research.max_extracts=1` 能挡住 3 条候选变成 3 次请求。
**未自动提交 Git**（按你的要求）。

---

## 7. 待授权：抓取用量进账本的修复（§5.5）

| 步骤 | 建议改法 |
| --- | --- |
| 1 | `TopNExtractionStrategy.extract()` 返回时带上 Provider 的 `UsageDelta`（`Ok(out, usage=...)`），或让策略返回 `(内容, usage)` 二元组 |
| 2 | `_maybe_extract()` 把该 usage 经 `_add_usage()` 累加进 `output.budget_used` |
| 3 | 补离线用例：多次抓取时，第 2 次之前的 `_cost_ceiling()` 能读到已累计的抓取成本并拦住 |

是否授权我改？改完我仍只做离线验证（不另发真实请求）。

## 8. 全部遗留问题（按你给的优先级）

| 优先级 | 项 | 状态 |
| --- | --- | --- |
| 高 → **已完成** | **补做 `/extract` 真实验证** | **通过**：HTTP 200，314,743 字正文，内容级核验全绿（§5.2/§5.3） |
| 高 | **抓取用量不进账本** | **真实暴露的产品缺陷**，待授权修复（§5.5 / §7） |
| 中 | **真实 claim extractor** | 未做（按你的排序）。**新增前置阻塞**：摘录目前是导航区（§5.6），直接喂抽取器会拿到菜单而非正文，建议先定摘录策略 |
| 中 | **摘录取的是导航区不是正文** | **真实暴露**，改法三种已列出，待你选（§5.6） |
| 低 | **费用余额** | **记录为遗留，无代码解** —— API 不返回余额，单次消耗可从 `usage.credits` 确认（/search 1.0、/extract 0），账户余额仍**未知** |
| 低 | **发布日期缺失** | **接受并记录**（你已确认）。两个端点真实响应均无日期字段 |

费用说明：`/extract` 两次回报的 `usage.credits` 均为 **0**，但按官方口径这是"未达计费粒度"
的回报值，**不等于免费**；本次账户实际扣减与余额仍**未知**，我不做估计。

---

**验收口径重申**：真实请求累计 **3 次（`/search` 1 + `/extract` 2），全部 HTTP 200**；
未调用真实模型；未重试；每次运行上限 1 且带熔断；未打印密钥；未写入正式存储；未提交 Git。
`/extract` 的**连通性、响应契约、内容级核验均已真实验证通过**（§5.2/§5.3）。
但真实跑出 **1 个产品缺陷**（抓取用量不进账本，§5.5），**未擅自修改产品代码**，待你授权。
不把本次成功描述为研究质量或语义分析已通过。现停止，等待人工验收。
