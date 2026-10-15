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
4. **官方补救参数尚未接进自动流程** —— `plan_remediation` 目前只给建议、不自动发起
   （按"先不要写新业务代码"的要求，未接线）。本轮已实测它有效（§4），
   是否接线、如何接线（预算/次数上限/幂等）待人工决定。

## 8. 提交前检查

| 检查 | 结果 |
|---|---|
| 待纳入文件 | 32 个（本轮 5 个 + P4.1~P4.3、H10 累计，此前从未提交） |
| 本机真实密钥是否出现在任何待提交文件里 | **否**（按真实密钥值全文件匹配，0 命中） |
| `tvly-` 前缀 / `Bearer <实值>` / 实值 `api_key=` | 无（命中的 `api_key="super-secret-key-123"` 是**故意**用来验证"密钥不进请求体"的假值） |
| 本地数据库 / `.db` / `.sqlite` / 临时文件 | 无（隔离库与复测脚本都在系统临时目录，**不在仓库内**） |
| `.env` | 仓库内只有 `.env.example`（无实值） |
| 密钥是否出现在运行输出里 | **否**（脚本自报 `[9] 密钥是否出现在输出中: False`） |
