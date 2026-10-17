# P4.8 R4：真实检索 e2e（首次真实 Tavily 调用）

**执行时间**：2026-10-17 15:53（GMT+8）
**授权范围**：2 次检索 + ≤1 次抓取 + 3 次模型调用（1 抽取 + 2 对话），`max_retries=0`，隔离库，跑满五阶段
**实际发生**：**1 次检索成功 + 1 次检索超时 + 1 次抓取 + 1 次模型调用**（全部在清单内，无超限）
**唯一配置改动**：`.env` 的 `PERSONA_MODEL_MAX_CALLS` 1 → 3（其余未动）
**产物**：`%TEMP%\gps-p48-r4\real\persona.db`、`raw_responses.jsonl`（不删，供核对）

---

## 0. 一句话结论

检索出口**真的通了**（HTTP 200、`request_id`、credits 逐笔入账），门禁**真的挡住了**无关内容（0 条证据、0 条 traits、档案标 `INSUFFICIENT_EVIDENCE`），但这一轮**没有产出一个可用的来源**——因为目标（阿灯/阿雾《雾港灯会》）在真实网络上不存在，检索回来的是"防雾灯""雾灯灯泡"这类同名噪声。系统没有把噪声变成档案，这一点是**对的**；同时暴露了 4 个新缺陷（见 §5）。

---

## 1. 调用次数：清单 vs 实际

| 类别 | 清单（授权） | 实际 | 差异原因 |
| --- | --- | --- | --- |
| 检索 | 2 | **2 发出**（1 成功 + 1 超时） | 第 2 条「阿雾 《雾港灯会》」`EXTERNAL_TIMEOUT` |
| 抓取正文 | ≤1 | **1**（`credits=0`） | 正文返回但没拿到可用内容，官方不计费 |
| 模型调用 | 3（1 抽取 + 2 对话） | **1**（1 次对话） | 0 份材料过门禁 ⇒ 抽取器未被调用；对话每轮 1 个 turn |
| 合计 HTTP 往返 | ≤6 | **3** | — |

**清单外调用：0**。脚本按 URL 分类自己数，Provider 内部计数（`chat.calls=1`）与实际往返一致。

逐笔（含 `request_id`）：

| # | 类别 | request_id | 用量 |
| --- | --- | --- | --- |
| 1 | search「阿灯 《雾港灯会》」 | `59e19683-373c-445e-94b8-e86ef0a7131c` | 1 credit（CONFIRMED） |
| 2 | search「阿雾 《雾港灯会》」 | **无**（超时） | **UNKNOWN**（不是 0） |
| 3 | extract（1 URL） | `b8e63438-0e78-4bdf-9122-75964691b2d5` | 0 credit（CONFIRMED） |
| 4 | chat（对话 turn#1） | `1b5558e3-3551-4acb-bdde-2b834880791a` | 271 in / 16 out |

---

## 2. 五阶段：全部 EXECUTED，但只有第 1、3、5 阶段有实质产物

| 阶段 | 状态 | 产物 | 说明 |
| --- | --- | --- | --- |
| RESEARCH | EXECUTED | 5 条来源 | 1 条查询成功、1 条失败；5 条结果全部登记成来源 |
| EVIDENCE_EXTRACT | EXECUTED | **0 条证据** | 5 条候选全部拒收：材料没有门禁结论 ⇒ 不调模型 |
| PERSONA_DISTILL | EXECUTED | 2 份档案（空） | 双方均标 `INSUFFICIENT_EVIDENCE`，`traits=0` |
| RELATIONSHIP_DISTILL | EXECUTED | 1 份 PAIR | 0 条维度、`open_questions=1` 写"互动模式待补" |
| DIALOGUE_RUN | EXECUTED | 1 轮对话 | 1 次模型调用；台词是"（沉默地站着…）" |

任务记录：`status=COMPLETED`，`progress=5/5`。

**"EXECUTED" 的含义要说清**：它表示"这一阶段跑了并如实记录了结果"，**不等于**"产出了内容"。空档案是正确输出，不是失败。

---

## 3. 真实检索质量：通路可用，内容不可用

**通路（可用）**：
- 请求真的发出去了（`POST https://api.tavily.com/search`，`basic`，`max_results=5`）；
- 官方返回 `request_id` 与 `usage.credits`，逐笔落库（`external_usage_records` 3 行）；
- 失败的那次记为 `cost_status=UNKNOWN`，**没有当 0**（失败≠免费）；
- 抓取 `credits=0` 也如实记 0，不是"猜的 0"。

**内容（不可用）**：查询「阿灯 《雾港灯会》」返回 5 条，全部是**汽车防雾灯 / 雾灯百科 / 淘宝雾灯灯泡**。原因很直白：这个词组在真实网络上不存在，Tavily 按"雾""灯"字面匹配。

**门禁（有效）**：

| 位置 | 结论 |
| --- | --- |
| 抓取正文门禁 | `LOW_QUALITY`：`NO_CONTINUOUS_PROSE`、`TARGET_NOT_MENTIONED` ⇒ 正文不进材料流 |
| 材料门禁 | 5 份检索摘要都没有 `quality_status` ⇒ `MATERIAL_UNDECLARED` 全拒 ⇒ **不调用模型** |
| 档案 | 0 条证据 ⇒ `INSUFFICIENT_EVIDENCE`，不补全、不臆测 |
| 关系 | 0 条 RELATION 证据 ⇒ 各维度留空 + `open_questions` |

结论：**检索→证据之间的闸门是有效的**。垃圾来源没有变成垃圾档案——这是本轮最值得肯定的一条。

---

## 4. 档案与关系：都是空的（符合预期）

| 对象 | 结果 |
| --- | --- |
| 阿灯 / 阿雾 档案 | 各 1 份 v1，`status=INSUFFICIENT_EVIDENCE` |
| `traits` | **0**（预期；本轮没有任何证据） |
| `single_events` / `habits` / `expression_style` / `experiences` | 0 |
| `background` | 0（不是 4——之前那行是报告器把 dict 的键数当条目数，脚本已修） |
| 关系档案 | 1 份 `PAIR`，`perspectives=0`，7 个维度全空，`status=INSUFFICIENT_EVIDENCE` |
| 对话 | 1 轮，发言人按 `least_recently_spoken` 选定，引用了 2 个 persona 条目 + 1 个关系条目（均为空壳） |

---

## 5. 本轮发现的缺陷（4 个新 + 1 个已修）

### D-1 已修（未提交）：作品名没进检索词

**症状（dry-run 抓到，花钱之前）**：`register_targets` 把作品名写进了实体载荷，却只给 `PersonQueryTarget` 填 `display_name`，于是真实检索查的是「阿灯」「阿雾」两个两字人名。CLI 又强制要求 `--work-title`——声明了却不用。

**修法**：`register_targets` 把 `work_titles` / `adaptation_version` / `entity_type` 一起带下去；`_query_target` 不再把已有的 `work_titles` 清空。修后查询 = 「阿灯 《雾港灯会》」。测试 +4 项（`test_p4_8_search_wiring.py` 20 → 24）。

### D-2 未修：过滤阶段**没有相关性判定**

`filter_and_rank` 只做去重与完整性检查（标题非空、locator 非空、批内同键），**没有任何"这条结果跟目标有没有关系"的判断**。5 条防雾灯页面因此全部"accepted"并登记成来源，库里看起来是"5 条来源"，实际可用 0 条。

真正挡住它们的是下游两道门禁（正文 `TARGET_NOT_MENTIONED`、材料 `MATERIAL_UNDECLARED`）。门禁兜住了，但**来源表的语义被稀释**：登记 ≠ 可用。建议下一轮加"目标名/作品名必须整体命中标题或摘要"的硬相关性判定，并把无关候选记进 `rejected`（带原因），而不是登记完再靠门禁拦。

### D-3 未修（记账红线）：对话的模型调用**没进逐笔账本**

- `external_usage_records` 里只有 3 条 tavily，**没有 deepseek**；
- 对话的 token 落在 `ConversationSession.budget_used` / `ConversationTurn.provider_usage`（271 in / 16 out），**看得见但记不进账**；
- `DialogueDeps` 没有 `ledger` / `usage_sink`（抽取器有）。

后果：累计费用闸门与"逐笔对账"都漏了这笔（本轮 0.0001005 USD，金额小，**机制不对**）。这违反上轮拍板的"所有真实调用必须走 `usage_sink` 落库"。建议下一轮第一项就修它（给 `DialogueDeps` 加 `ledger` + `usage_sink`，或在编排层结算），并加"对话一次就必须多一条外部用量记录"的断言。

### D-4 未修（清单精度）：对话次数口径偏大

`plan_calls` 按 `目标数 × 轮数` 报（2 目标 × 1 轮 = 2 次），实际每轮只生成 1 个 turn（1 次调用）。清单数字是**上界**，但容易让人以为会花 2 次的钱。建议改成 `max_rounds`（或按"每轮 1 turn"重新标定）并在文案里写明是上界。

### D-5 已修（本轮工具）：原始响应记录器漏记"读响应体超时"

第 2 次检索在原始记录里**消失了**（只有 3 行而不是 4 行），因为超时发生在 `resp.read()` 里，而代理没在那一步兜异常。已修：`read()` 失败也落一行（带 `error`）。下次运行不会再丢。

---

## 6. 费用与累计

**本轮（真实发生）**：

| 项目 | 金额 | 状态 |
| --- | --- | --- |
| 检索 #1 | 1.0 credit | CONFIRMED |
| 检索 #2（超时） | 未知 | **UNKNOWN，需人工核对 Tavily 账单** |
| 抓取 | 0 credit | CONFIRMED |
| 模型（对话 1 次） | 271 in × 0.3/1M + 16 out × 1.2/1M = **0.0001005 USD** | 手算，未进账本（D-3） |

**累计**：
- 模型侧：0.0097980 + 0.0001005 = **0.0098985 USD**
- 检索侧：**1 credit 已确认 + ≤1 credit 待核**（两个计量单位不相加，账本不做汇率换算）

---

## 7. 这一轮到底证明了什么、没证明什么

**证明了**：
1. 装配层能把真实 Tavily 接上，且在 `allow_real` 闸门下只按授权构造；
2. 真实请求能发出、能拿到 `request_id` 与 credits、能逐笔落库；
3. 失败请求记 `UNKNOWN` 而不是 0；
4. 垃圾来源**不会**变成垃圾档案（四道门禁逐级拦住）；
5. 五阶段在"零可用来源"下仍然跑完并如实报告（不伪造成功）。

**没证明**：
1. **检索→证据→档案的"有内容"路径**：本轮 0 条证据，抽取器一次没被调用，`traits` 自然为 0——不能据此说"判定链在生产条件下也通"；
2. **模型抽取的真实产出**：只发生了 1 次对话调用（16 token）；
3. **两次检索都能成功**：第 2 次超时，只有 1 个 `request_id`。

**建议的下一步（需另行授权）**：换一个**真实存在**的公有领域虚构角色（如《西游记》孙悟空）跑同样的清单（2 检索 + ≤1 抓取 + 3 模型），才能验证"有来源 → 有证据 → 有 traits"这条主路径。预估与本次同量级：≈2.2 credits + ≈0.0099 USD。

---

## 8. 复现方式

```bash
# 只看清单，物理断网（零费用）
python scripts/e2e_p4_8_real_search.py --dry

# 真实运行（花真钱；换新目录，脚本拒绝覆盖旧产物）
python scripts/e2e_p4_8_real_search.py --out "$TEMP/gps-p48-r4b"

# 只读重放上次产物出报告（零请求、零费用）
python scripts/e2e_p4_8_real_search.py --report-only
```

脚本自带三条纪律：预检不过就停（发请求之前）、清单与实际不符就标红、原始响应逐条增量落盘（崩溃也不丢）。
