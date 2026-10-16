# P4.5 验收报告（方案 C：门禁改判摘录 + 摘录取正文窗口 + 长页真实复测）

**阶段**：P4.5 收尾（方案 C 实施 → 0 请求离线复核 → 补测试 → 1 次长页真实 `/extract` → 提交）
**日期**：2026-10-15
**纪律**：本轮**只发 1 次真实请求**（受控上限 1，硬熔断 1）；0 次真实模型调用；
先离线自检再发请求；原始响应立刻落盘；密钥只判存在性、从不打印。

---

## 0. 一句话结论

方案 C 已实施并验证：**门禁判定对象 = 下游交付对象**（两者都是 `excerpt`），
摘录不再是"页头 2000 字"而是"连续正文最密集的那段原文"。
长页真实复测同时证成了一件事：**官方 `query` + `chunks_per_source` 能把问题在源头解决**
（同一 URL 的 `raw_content` 从 314,743 字变成 1,937 字纯正文）。
费用账本本轮真实累加 1 笔，`usage.credits=1` 是**前 4 次成功提取之后的第 5 次**，
官方"每 5 次成功 URL 提取 1 credit"的计费粒度在真实数据上得到确认。

---

## 1. 做了什么（方案 C = A + B）

| | 内容 | 位置 |
|---|---|---|
| **A** | 门禁判 **`excerpt`**（下游真正拿到的那段），不再判 `content`（全文） | `persona/modules/research.py` `_gate_extract_quality` |
| **B** | 摘录取"**连续正文最密集的那段原文**"，不再取 `raw[:2000]` | 新 `persona/core/text_window.py` + `persona/providers/tavily.py` |
| 配套 | 行判定口径（什么是正文行 / 导航行）从 `modules/extract_quality.py` **下沉到 core**，Provider 与门禁共用同一套 | `persona/core/text_window.py`；`extract_quality.py` 改为 import |

### 为什么必须 A+B（缺一不可）

- **只做 A**：那时摘录还是 `raw[:2000]`，长页的摘录全是目录 → 长页会被**全部判废**，
  已经花掉的额度白花（实测：314,743 字长页的页头 2,000 字里正文只有 81 字）。
- **只做 B**：全文判定与摘录交付仍然是两回事，长页的"全文 VALID"会**掩盖摘录问题**
  （P4.5 复测实测到：全文 VALID，材料出口照样把目录摘录交出去）。

### 为什么行判定要下沉到 core

`persona/providers/*` 不允许 import `persona/modules/*`（那是反向依赖，会成环）。
但摘录必须由 Provider 生成（`ExtractResult.excerpt` 在 Provider 契约里）。
把共用的行判定 + 窗口算法放 `core/text_window.py`，两边口径强制一致。

### 窗口算法（启发式，不绑定任何站点）

1. 逐行判断"是不是正文行"（链接覆盖率 ≥ 0.5 的行 = 导航条目）；
2. 找**最长的一段连续正文**，取它的行首作为窗口起点；
3. 从起点向后取 2,000 字符，末尾对齐到换行（不切半句）；
4. **找不到任何正文行 → 回退到开头**（不假装找到了），由门禁去判废。

---

## 2. 证据一：0 请求离线复核（用 P4.3 落盘的真实 314,743 字响应）

```
真实原始响应：p43_extract_raw.json（Wikipedia / Alan_Turing），raw_content = 314,743 字

=== B：摘录窗口 ===
旧摘录 = raw[:2000]           → 偏移 0..2000
新摘录 = 连续正文最密集的那段  → 偏移 115227..116992（1,765 字）
  旧摘录开头：'[Jump to content](#bodyContent)  [![](/static/images/icons/enwiki-25.svg)...'
  新摘录开头：'In August 2009, British programmer [John Graham-Cumming](https://...'

=== A：门禁判摘录（不判全文）===
旧摘录（页头 2000 字）  status=LOW_QUALITY prose=81    nav_ratio=0.923 reasons=[NAV_DOMINATED]
新摘录（正文窗口）      status=VALID       prose=1,759 nav_ratio=0.000 reasons=[]
全文（仅供对照）        status=VALID       prose=86,212 nav_ratio=0.788

=== 材料出口 ===
旧摘录: 材料 0 条 / 被拒 1 条
新摘录: 材料 1 条 / 被拒 0 条（snippet = 那段正文，不是全文）
```

**结论**：在真实数据上，B 把摘录从导航骨架换成正文；A 让门禁判的就是交出去的那段。

---

## 3. 证据二：突变检验（新用例在旧行为下真的会失败）

把产品代码临时改回旧行为（`excerpt=raw[:2000]` + 门禁判 `content`），
只跑新加的用例：

```
FAILED (failures=3)
  test_provider_excerpt_skips_page_head        → '[章节' unexpectedly found in ...
  test_delivered_material_equals_judged_excerpt → '阿雾' not found in '[章节0-0](#锚点00)...'
  test_gate_judges_excerpt_in_research_flow    → 'VALID' != 'LOW_QUALITY'
```

**这不是为了凑通过率，而是为了让测试真的能抓到这个缺陷。**
检验后已把产品代码改回（仓库内 `grep MUTATION-TEST-ONLY` → 无残留）。

---

## 4. 证据三：1 次长页真实 `/extract`（授权范围 = 1 次）

| 项 | 结果 |
|---|---|
| 真实请求数 | **1**（硬熔断上限 1，第 2 次会被脚本拒绝） |
| 端点 | `POST https://api.tavily.com/extract` |
| HTTP | **200** |
| 请求体 | `urls=[Wikipedia/Alan_Turing]`, `extract_depth=basic`, `query="Alan Turing biography"`, `chunks_per_source=3`, `include_usage=true` |
| 鉴权 | `Authorization: Bearer` 头（旁录只截到 `Bearer ` 7 字符） |
| `request_id` | `605e62e9-561f-45ca-ad29-7c56f3e75196`（已入账本） |
| `usage` | **`{"credits": 1}`** |

### 官方 `query` + `chunks_per_source` 的效果（本轮唯一要回答的问题）

| | 基线（P4.3 真实，无 query/chunks） | 本次（带 query + chunks=3） |
|---|---|---|
| `raw_content` 长度 | **314,743** | **1,937**（缩短 312,806 字） |
| 内容是否逐字相同 | — | **否** |
| 正文窗口起点 | 115,227 | **0** |
| 前 120 字 | `[Jump to content](#bodyContent)...`（目录） | `Alan Mathison Turing (/ˈtjʊərɪŋ/; 23 June 1912 – 7 June 1954) was an English mathematician...`（正文） |

**结论：官方补救参数在长页上有效。** 它让服务端直接返回与目标最相关的若干内容块，
`raw_content` 从整篇大页变成 1,937 字纯正文——**问题在源头被解决**，
本地摘录窗口（B）在这种响应上取到的就是开头（旧规则 `raw[:2000]` 也会是 VALID）。
B 的价值在于**服务端返回整页时**（P4.3 基线那种情况，见 §2），两者互补、不是替代。

### 费用账本（本轮真实累加）

```
账本逐笔：op=EXTRACT outcome=SUCCEEDED request_id='605e62e9-...'
          估算=0.2  实际=1.0  状态=CONFIRMED  来源=PROVIDER_USAGE  币种=TAVILY_CREDIT
          unit=1/1  入库=True  重复写=False（幂等，第二次必须 False）
账本汇总：requests=1 confirmed_cost=1.0 estimated_cost=0.2 unknown_requests=0 by_operation={EXTRACT:1}
待人工核对（UNKNOWN）= 0 条
```

**这里正好印证了"估算与实际必须分开"的设计**：

- 前 4 次成功 URL 提取官方回报的都是 `credits=0`（未达 5 次计费粒度）；
- 本次是**第 5 次**，官方回报 `credits=1` → 官方"basic 每 5 次成功提取 1 credit"
  的计费粒度**在真实数据上被确认**（此前只能从文档读，现在有实测）。
- 若账本只看"实际值"，前 4 次会被记成"免费"；若只看"估算 0.2"，本次会少记 0.8。
  所以 `accountable_cost() = max(估算, 实际)`，本轮 = **1.0**。

---

## 5. 改动文件（本轮）

| 文件 | 改动 |
|---|---|
| `persona/core/text_window.py` | **新增**：行判定 + 正文窗口定位（供 Provider 与门禁共用） |
| `persona/providers/tavily.py` | 摘录取正文窗口；新增可配 `excerpt_window_chars`；`truncated` 改为"摘录比原文短" |
| `persona/modules/research.py` | 门禁判定对象改为 `excerpt`（A）；`ExtractedContent` 文档说明判定=交付 |
| `persona/modules/extract_quality.py` | 行判定下沉到 core 并改为 import（公开 API 不变）；文档补 A+B 说明 |
| `tests/test_p4_5_usage_quality.py` | 夹具改为 **content ≠ excerpt**；新增 2 个测试类共 11 个用例 |

## 6. 测试

```
tests.test_p4_5_usage_quality  → Ran 76 tests … OK   （原 65，+11）
tests.test_p4_2_tavily_compliance → Ran 52 tests … OK
tests.test_research_module     → Ran 52 tests … OK
python -m unittest discover -s tests -t .  → Ran 869 tests … OK
```

新用例刻意让 `content` 与 `excerpt` **不是同一段文本**（长页 = 前 50 行目录 + 后 40 行正文，
目录部分 > 2,000 字），否则"门禁判全文、交付的是摘录"这个差异永远不可见。

## 7. 遗留（未解决，不假装解决）

1. **账户余额 / 剩余额度仍不可知** —— API 不返回，无代码解（记录为遗留）。
2. **credits → 货币不做换算** —— 公开价目表 $0.008/credit 不是本账户单价，不冒充。
3. **请求级失败是否计费仍未触发** —— 代码按 UNKNOWN 处理，但**至今所有真实请求都是 200**，
   这条路径一次都没走到 → **未经实测验证，只有代码逻辑**。
4. **官方补救参数已接进自动流程**（§9，本阶段收尾）：`query+chunks_per_source` 进初始
   抓取（D4），`format=text` 作为兜底补救；受预算/次数上限/幂等三重约束。
   **但补救自动流程尚未经真实请求验证**（§9.6），初始带 chunks 的真实行为 likewise。

## 8. 提交前检查

| 检查 | 结果 |
|---|---|
| 待纳入文件 | 32 个（本轮 5 个 + P4.1~P4.3、H10 累计，此前从未提交） |
| 本机真实密钥是否出现在任何待提交文件里 | **否**（按真实密钥值全文件匹配，0 命中） |
| `tvly-` 前缀 / `Bearer <实值>` / 实值 `api_key=` | 无（命中的 `api_key="super-secret-key-123"` 是**故意**用来验证"密钥不进请求体"的假值） |
| 本地数据库 / `.db` / `.sqlite` / 临时文件 | 无（隔离库与复测脚本都在系统临时目录，**不在仓库内**） |
| `.env` | 仓库内只有 `.env.example`（无实值） |
| 密钥是否出现在运行输出里 | **否**（脚本自报 `[9] 密钥是否出现在输出中: False`） |

---

## 9. 补救接线（官方参数自动重抓）—— 方案 C 之后的收尾

用户逐条决定后实施：**D1** 补救预算不足 → 停手 + warning + 已抓材料照常返回（不整轮
`BUDGET_EXCEEDED`）；**D2** 补救不计入 `max_extracts`，改用 `max_remediations`（每 URL）
+ 本轮总量 ≤ `max_extracts`；**D3** 跨轮幂等（`UsageSinkPort` 加只读查询，存储层主键查）；
**D4** 初始抓取也带 `chunks_per_source`（官方默认 3），补救降级为兜底。

### 9.1 接线后的时序

```
_maybe_extract
  ② _cost_ceiling(reserve=max(1, max_extracts))              # 粗粒度最坏情况（既有）
  ④ query = _target_query_text(); chunks = 3 if query else None      # D4
  ⑤ key = make_idempotency_key("tavily", OP_EXTRACT,
         *extract_key_parts(urls, depth, query, chunks))             # 键含 query+chunks
  ⑥ 发送层守卫（D3）：ledger.has_attempted(key) 或 sink.has_external_usage(ctx, key)
       命中 → warning EXTRACT_DUPLICATE_SKIPPED，**不发送**
  ⑦ _reserve(key, estimate=0.2×URL 数) → ⑧ strategy.extract(..., query, chunks_per_source)
  ⑪ _gate_extract_quality（判 excerpt，方案 A）→ ⑫ _remediate_low_quality        # 新增

_remediate_low_quality（新增；三条硬约束全部落点）
  候选 = LOW/EMPTY 且无 error 且**有提取质量缺陷**（见 9.3）的条目
  for item: plan_remediation(url, quality, query, max_options=剩余次数, already_queried)
     sent ≥ max_extracts            → warning CAPPED，停手（D2）
     _cost_ceiling(reserve=1.0) 超限 → warning BUDGET_STOPPED，停手，**不返回 Err**（D1）
     ledger.should_pause() 为真      → warning UNKNOWN_PAUSE，停手（费用不明不继续花）
     幂等守卫命中                    → warning DUPLICATE_SKIPPED，试下一个选项（D3）
     _reserve → extract_urls(单 URL, **option) → _add_usage → 重新过门禁
        VALID → 原地更新条目、移出 rejected、warning REMEDIATED、break（不花后续选项）
        仍 LOW → 更新 rejected 诊断（记补救历史）
```

### 9.2 幂等键：预留键 == 结算键，由构造保证

新增 `core/usage_ledger.py: extract_key_parts()`——**research 预留与 Provider 兜底推导
共用同一个函数**（此前两边各写一遍：research 4 段 vs provider 6 段，顺序/空值过滤任何
一处漂移都会出事：预留键 ≠ 结算键 → 预留永不弹掉＝幽灵占额度；两笔不同请求共用一个键
→ 该记的静默不计）。键组成 = `provider|EXTRACT|排序 URLs|depth|query|format|chunks`。
初始键（D4 后含 query+chunks）与补救键（format=text）天然不同，不会互相抵消预留。

### 9.3 实施中实测发现并处理的一个设计问题（超出原方案，已按保守原则处理）

第一轮跑既有回归时，`test_extract_usage_reaches_research_output` 报
`requests 3 != 2`：夹具正文没提到目标人名 → 门禁按"与目标人物无关"判 LOW →
补救真的发起了一次 `format=text` 重抓。**对一页无关内容重抓一遍，回来还是无关**——
这是在拿预算赌运气。因此新增 `has_extraction_defect()`：只有"空 / 过短 / 导航占满 /
找不到连续正文"这类**提取质量缺陷**才值得补救；纯相关性判断（`TARGET_NOT_MENTIONED`）
与页面类型不补救。该判定不削弱任何门禁（条目照旧被拒、照旧清空 content），
只是不再为救不回来的条目花额度。同时把该夹具改成提到目标人物的正文
（测的是用量入账，不是质量，夹具必须干净）——**未放宽任何断言**。

### 9.4 改动文件

| 文件 | 改动 |
|---|---|
| `core/usage_ledger.py` | 新增 `extract_key_parts()`（键的唯一口径）、`ExternalUsageLedger.has_attempted()`（预留/未知占位/已结算三态） |
| `core/types.py` | 无改动 |
| `config/spec.py` | `ResearchLimits += max_remediations: int = 1`；快照 dict 加一行；`CONFIG_FIELDS` 加 `PERSONA_RESEARCH_MAX_REMEDIATIONS`（int，0..5） |
| `config/loader.py` | `ENV_ALIASES` 加 `RESEARCH_MAX_REMEDIATIONS` 短名映射 |
| `.env.example` | 新变量一行 + 注释 |
| `modules/extract_quality.py` | `plan_remediation(..., already_queried=False)`（R1：初始已带 query+chunks 时剔除选项①，理由=重复消费）；新增 `EXTRACTION_DEFECT_REASONS` / `has_extraction_defect()` |
| `modules/research.py` | Protocol 的 `extract` 加可选 kwarg `chunks_per_source`；`TopNExtractionStrategy` 拆出公开 `extract_urls()`（初始与补救共用同一套转换）；`UsageSinkPort` 加 `has_external_usage`；`ExtractedContent` 加 `remediation_attempts/remediation_detail`（纯诊断）；`_maybe_extract` 按 9.1 改；新增 `_remediate_low_quality`/`_duplicate_reason`/`_pause_reason`；6 个新 warning 码 |
| `providers/tavily.py` | 兜底键推导改用 `extract_key_parts`（**行为不变**，结构上与 research 统一）；发送逻辑本就正确（query 存在才发 chunks、夹紧 1..5） |
| `providers/base.py` | 无改动（`ExtractRequest` 已有 `query/format/chunks_per_source`） |
| `storage/sqlite.py` | 新增 `has_external_usage(ctx, key) -> Result[bool]`（主键查；dry_run → False） |
| `tests/test_p4_5_usage_quality.py` | +27 个用例（原 76 → 103），fixture `service()` 支持 `usage_sink/task_id`；新增按参数分流的 `/extract` 桩 `_ParamAwareExtractStub`（默认返导航、`format=text` 返正文——否则"补救换了参数"在响应上看不出差别） |

### 9.5 测试结果（全离线，零真实请求）

```
tests.test_p4_5_usage_quality  → Ran 103 tests … OK   （原 76，+27）
tests.test_config              → OK（新配置字段/别名/快照）
tests.test_p4_2_tavily_compliance → Ran 52 tests … OK
tests.test_research_module     → Ran 52 tests … OK
tests.test_dependency_rules    → OK
python -m unittest discover -s tests -t .  → Ran 896 tests … OK   （原 869，+27）
```

新增覆盖（用户点名"幂等键的坑必须显式覆盖，不能只靠代码审查"）：

| 约束 | 用例 |
|---|---|
| 预留键 == 结算键 | ① `test_research_key_formula_matches_provider_fallback`（走真实 Provider 兜底推导，与 research 公式比对）② `test_reserved_key_is_the_key_that_gets_settled`（端到端：账本记录的键就是预留的键，且 `reserved_cost == 0` 无幽灵占额度）③ `test_initial_and_remediation_keys_differ` ④ `test_chunks_value_changes_the_key` ⑤ `test_empty_params_do_not_change_the_key` |
| 重复请求不重复计费 | `test_same_request_twice_is_not_sent_twice`（轮内守卫，extract 调用次数不增长）、`test_cross_run_guard_via_storage`（跨轮：新内存账本 + 持久账本挡住）、`TestLedgerSendGuard`（`has_attempted` 三态）、`test_storage_has_external_usage` |
| 预算（D1） | `test_budget_insufficient_stops_remediation_but_keeps_run_ok`（一个补救请求都不发 + warning + 不返回 Err）、`test_budget_allows_remediation_and_usage_is_counted`（usage 进 budget_used） |
| 费用不明 | `test_unknown_cost_pauses_further_remediation`（500 → UNKNOWN + unknown_hold 占额度 + 第二个条目不再碰） |
| 次数（D2） | `test_max_remediations_zero_disables_remediation`、`test_max_extracts_zero_disables_remediation`、`test_total_remediation_capped_by_max_extracts` |
| 质量 | `test_remediation_rescues_nav_page_end_to_end`（救活：原地更新、移出 rejected、材料 snippet=新摘录、初始带 chunks + 补救 format=text 两次请求都断言到）、`test_remediation_still_low_stays_rejected`（没救活：留 rejected + 诊断记补救历史 + 出口清空）、`test_video_page_is_not_remediated`、`test_relevance_only_rejection_is_not_remediated` |
| D4 | `test_initial_request_carries_query_and_chunks`（payload 带 query + chunks=3）、`test_no_target_text_sends_neither_query_nor_chunks`（无目标文本 → 官方约束下都不带） |
| 纯函数 | `TestRemediationPlanOptions` 6 个（R1 剔除选项①、未预查询时保留、无 query、max_options 截断、视频页、`has_extraction_defect` 区分相关性） |

**突变检验**（临时把 5 处行为改回旧/缺陷形态，新用例是否抓得住）：全部抓到——
摘录改回 `raw[:2000]` → 4 个用例红；门禁改判 `content` → 2 个红（含既有的
`test_gate_judges_excerpt_in_research_flow`）；关掉发送守卫 → 2 个红；关掉相关性
carve-out → 1 个红；D4 关掉 → 2 个红。改回后 `grep MUTATION-TEST-ONLY` 为空，103 全绿。

### 9.6 本轮**未**验证的（不冒充已验证）

1. **初始抓取带 `chunks_per_source` 的真实行为未实测** —— 上轮真实复测证明的是
   "`query + chunks_per_source` **一起发**能把 314,743 字压成 1,937 字"；本轮把 chunks
   接进初始路径后，**尚未发过任何真实请求**（用户要求接线后另行授权再验）。
2. **补救自动流程（`format=text` 重抓）未经真实请求验证** —— 只有离线桩验证；
   真实场景里 `format=text` 是否真能改善正文，未实测。
3. 请求级失败是否计费：仍未触发（见 §7.3）。

### 9.7 与原方案的一处偏差（如实记录）

原方案说"补救选项①（query+chunks）在初始已查询时剔除"是**推荐处理（R1）请用户确认**；
用户批复"方案总体正确，可以操作"，本方案按 R1 实施。另外原方案未提及
`has_extraction_defect`（9.3 的 carved-out）——这是实施中被实测逼出来的必要修正，
已按"最小修复 + 不削弱门禁"原则处理并单列说明。

### 9.8 提交状态

**未提交 Git**（按验收纪律，等人工验收后统一提交）。提交前将重跑 §8 的扫描
（真实密钥 0 命中 / 无本地库 / 无临时文件）。
