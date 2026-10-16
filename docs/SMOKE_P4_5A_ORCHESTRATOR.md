# P4.5-A 单人物真实冒烟报告（编排器路径）

> 状态：**已执行完毕，等待人工验收。**
> 真实模型调用：**恰好 1 次**（1 次 POST）。真实检索调用：**0 次**。其他真实服务：**0 次**。
> 费用：**0.0012765 USD**（预估上限 0.0033，预算上限 0.25）。
> 本文所有数字均为本机实测输出，非推断。

---

## 0. 授权与边界（逐条对照）

| 授权边界 | 实际执行 | 证据 |
|---|---|---|
| 1 次 POST | **1 次** | `[7] 真实调用次数 = 1`；账本 `记录数 = 1`；原始收发 1 条 |
| `max_calls=1` | 生效并用于拦住重跑 | `[7b]② 探针返回 Err：BUDGET_EXCEEDED` |
| `max_retries=0` | 未重试 | `[7] 真实调用恰好 1 次（未重试）` |
| 预估 ≤ 0.0033 USD | **实际 0.0012765** | `cost_confirmed = 0.0012764999999999999` |
| 上限 0.25 USD | 已占用 **0.001276 / 0.25**（0.51%） | `[8] 剩余预算 0.248723 / 0.25 USD` |
| 合成虚构人物（阿雾/雾港/星灯） | 单人物「阿雾」，材料含雾港、星灯会 | `[3]` 合成正文 5 句 |
| 结果写隔离库 | 临时库，退出即删 | `gps-p45a-6vqk7kw5\p45a.db` |
| 记录 request_id | `4b8b6f85-c2b8-4c4a-95d8-aae23c20bead` | 账本 + 原始响应 `id` 一致 |
| 记录 token | in 859 / out 849（合计 1708） | 原始响应 `usage` |
| 记录费用 | 0.0012765 USD，`CONFIRMED` / `OFFICIAL_RATE` | 账本 + 独立复算 A5.2 |
| 记录 accepted/rejected/pending | **6 / 0 / 0** | `validation_detail` |
| 记录摘录核验 | 6 条 VERBATIM 全部按 locator 命中 | `[7]` 每条 claim 的 `locator` + 复算器 A4 |
| 记录 gate 行为 | 见 §4 | `MATERIAL_SUPERSEDED` 等告警码 |
| 记录档案可追溯性 | 5/5 引用命中已登记证据 | 复算器 A7 / A7b |
| 原始响应落盘 | `var/p45a_last_run.raw.json`（4560 B） | 1 次 HTTP 收发，状态 200 |
| 不调其他真实服务 | 数据源=桩、对话=桩（零调用） | `[7] 桩对话 Provider 零调用` |
| 跑完用复算器独立复算 | **25/25 通过** | §5 |
| 跑幂等重跑（0 真实调用） | 见 §6，**0 次新增请求** | `[7b]` |

---

## 1. 本次新增的两件"事后举证"（脚本侧，未改产品代码）

产品代码 `persona/orchestrator/business.py` **本轮一行未动**（仍是上一轮已验收的 `+136 / −9`）。

### 1.1 原始响应落盘 —— `RawResponseCapture`

`DeepSeekProvider` 把网络出口收敛成单个可替换的 `self._urlopen`（其 docstring 明写
"**唯一**发生真实网络调用的地方，测试可替换"）。所以在**脚本侧**包一层即可拿到原始响应，
不必改产品代码。落盘内容：

```
url                  : https://api.deepseek.com/chat/completions
method               : POST
request_body_bytes   : 3578
request_header_names : ['authorization', 'content-type', 'user-agent']   ← 只记**名**，不记值
status               : 200
response_bytes       : 3558
```

**请求头只记名字**：`Authorization` 的值就是密钥，记值等于把密钥写进产物。有测试锁住
（`test_raw_capture_records_body_but_never_header_values`）。

### 1.2 幂等重跑 `[7b]`

见 §6。

---

## 2. 链路结果总览（编排器路径 = 产品代码全链）

```
BusinessOrchestrator.run(ctx, request)
  ├─ ① 研究（检索→抓取→门禁→材料）  EXECUTED   1 来源 / 1 抓取 / 材料流 1 份（VALID，254 字）
  ├─ ② ③ 抽取 + 证据登记            EXECUTED   6 候选 → 登记 6 条，拒收 0，待审 0
  ├─ ④ ⑤ 门禁 + 人格档案            EXECUTED   形成 1 份档案（pnf_381a7976…）
  ├─ 关系档案                        SKIPPED   参与者 < 2（单人物，语义跳过）
  └─ 模拟对话                        SKIPPED   参与者 < 2（语义跳过）
整体状态：PARTIAL      任务终态：COMPLETED      墙钟：4.36 s
```

**关于 `PARTIAL`**：单人物请求在 `_overall_status` 下**只能**是 `PARTIAL` —— 关系/对话阶段
在参与者 < 2 时按语义跳过，而 `SUCCEEDED` 要求五阶段全执行。这与缺陷无关；上一轮已用
**双人物诊断**（`--dry` 的 `[5b]`，桩模型零成本）证成五阶段全 `EXECUTED` → `SUCCEEDED`。
本次**没有**为了让状态好看而换成双人物，因为双人物的对话阶段会掺进桩产物。

**零桩掩盖**：链上唯一一处桩是**数据源**（方案 B 既定）。对话 Provider 也是桩，但因为是单人物，
它**零调用**（脚本断言 `dialogue_stub.chat_calls == 0`）。也就是说，**实际执行的每个阶段都是
产品代码 + 真实模型**。

---

## 3. 真实模型输出（accepted / rejected / pending）

| 项 | 值 |
|---|---|
| 整批 verdict | `OK` |
| 原始字符数 | 1908 |
| **accepted** | **6** |
| **rejected** | **0** |
| **pending** | **0** |
| finish_reason | `stop` |

6 条候选全部 VERBATIM，逐条按 locator 复核：

| key | tier / dimension | 摘录 locator | 独立复算 |
|---|---|---|---|
| c1 | VERBATIM / BACKGROUND | `[0, 51)` | OK |
| c2 | VERBATIM / HABIT | `[53, 100)` | OK |
| c3 | VERBATIM / TRAIT | `[102, 152)` | OK |
| c4 | VERBATIM / HABIT | `[154, 203)` | OK |
| c5 | VERBATIM / EXPERIENCE | `[205, 254)` | OK |
| c6 | VERBATIM / RELATION | `[111, 122)` | OK |

> `locator` 全部由框架按 `str.find` 算出，**模型伪造不了位置**；复算器用**自己**实现的
> 空白折叠 + `str.find` 重跑一遍，25 项全绿（其中 A4 = 逐字摘录可按 locator 独立复算命中）。

---

## 4. 三处门禁的实际行为

| 门禁 | 落点 | 本次结果 |
|---|---|---|
| ① 材料正文质量门禁 | Research 出口 / 抽取器入口 | 抓取正文 254 字 → `VALID`；**同一来源的检索摘要被抓取正文取代**（`MATERIAL_SUPERSEDED`，不静默） |
| ② 证据登记门禁（可追溯） | Evidence 登记 | 6/6 候选的 `source_id` 均在本次登记来源内（复算器 A3 OK）；无悬空引用 |
| ③ 人格稳定性门禁（AC-7） | Persona 蒸馏 | c3 是 `TRAIT` 但 stability 恒 `UNKNOWN` → **落 `single_events`**，`traits` 保持为空 |

告警码：`['MATERIAL_SUPERSEDED', 'NO_EVENTS', 'NO_DIALOGUE_PROFILE']`（均为**说明性**，非失败）。

---

## 5. 独立复算（`scripts/verify_p4_5a_outputs.py`）

```
结果：25/25 通过        退出码 0
```

该复算器**刻意不 import 任何 `persona.modules.*`**，自己折空白、自己 `str.find`、自己按配置单价
重算金额 —— 避免"用被测代码验证被测代码"。其中最关键的是跨组件那条：

- **A5.2 费用可复算**：用**原始响应**里报的 token（859 in / 849 out）× 配置单价
  （0.0003 / 0.0012 per 1K） = `0.0012765`，与账本 `cost_confirmed` **完全一致**
  （抽取器链路与 Provider 响应是两条独立路径，能对上才算真对账）。
- **A5.5** 服务端 `request_id` 非空；**A5.3** `OFFICIAL_RATE`；**A5.4** `CONFIRMED`；
  **A5.8/A5.9** 无未知费用、无失败笔数。
- **A8.1/A8.2**：`traits` 内不含 `SINGLE`/`UNKNOWN` 稳定性条目 —— AC-7 保守满足。

---

## 6. 幂等重跑（`[7b]`，0 次真实调用）

两道闸门都位于 Provider **之前**，本次**两道都实测拦下**：

### 闸门 ① 发送层幂等守卫（原样重跑同一条链）

```
① 原样重跑 orch.run  → 真实模型调用次数 = 1（重跑前 1）
   重跑整体状态 = PARTIAL；证据 0 条
   告警码 ['MATERIAL_SUPERSEDED', 'MODEL_CALL_DUPLICATE_SKIPPED', 'NO_CANDIDATES',
          'NO_DIALOGUE_PROFILE', 'NO_EVENTS', 'NO_EVIDENCE_FOR_ENTITY']
   → 拦住它的是发送层幂等守卫（同一意图，幂等键相同）：在构造请求之前就返回，
     Provider 根本没被调用。
```

### 闸门 ② 预算闸门（改 key 逼它自己出手）

```
② 预算探针：改掉材料的 canonical_key（幂等键随之变化）→ 幂等守卫不再适用
   探针后真实模型调用次数 = 1
   探针返回 Err：BUDGET_EXCEEDED —— 模型调用次数已达上限 1：不再自动发起请求
```

> **为什么不另起一个进程跑第二次 `--run`**：新进程的账本是**全新内存实例**，看不到第一次的
> 消费记录 —— 那会真的发出**第 2 次 POST**，直接违反"1 次 POST / `max_calls=1`"的授权边界。
> 所以幂等重跑只能在**同一条链、同一个账本**内做，这也正是"重复意图"的真实条件。

---

## 7. 测试与回归

| 项 | 结果 |
|---|---|
| `tests.test_p4_5a_e2e` | **47 项全绿**（45 → 47：新增 `TestRawCaptureAndIdempotentRerun` 2 项；其中端到端离线测试扩了原始落盘与 `[7b]` 的断言） |
| 回归批次 1（编排器/研究/证据/人格/抽取） | **353 项 OK** |
| 回归批次 2（P4.x 各轮 + 分层铁律 + 导入） | **394 项 OK** |
| 回归批次 3（其余全部模块） | **422 项 OK** |
| 合计 | **1169 项全绿**（上一轮 1167 + 新增 2） |

其中最有价值的是 `test_orchestrator_real_run_end_to_end_offline`：**桩模型 + 断网护栏**下跑完整条
产品代码链，产物再过独立复算器全绿 —— "接线对了"因此是可离线判定的断言，而不是只能靠花钱发现。

---

## 8. 预检（花钱之前）

`--dry --path=orchestrator`：**退出码 0，预检全部通过，零真实请求**。

- 配置核验 11 项 OK（`deepseek-flash` / `USD` / `peak` / `max_calls=1` / `require_gate_verdict=True` / `max_retries=0`）
- A11 门禁负样例两类 OK（`MATERIAL_NOT_GATE_PASSED` / `MATERIAL_UNDECLARED`），均 **0 次模型调用 / 0 条候选**
- 请求体 8 项断言 OK（`json_object` 映射、`thinking=disabled`、`stream=false`、含 "json"、`metadata` 不外发、密钥不在 body 与 `repr`）
- `[6]` 预告：1 次 POST / ≤0.0033 USD / 上限 0.25 / `max_retries=0`

---

## 9. 产物与工作区

```
var/p45a_last_run.json        10082 B   复算输入（结构化事实）
var/p45a_last_run.raw.json     4560 B   原始 HTTP 收发（含原始响应体）
```

`var/` 已在 `.gitignore:45` 中，产物不会入库。合规扫描（改动文件）：厂商 SDK 0、
硬编码本机路径 0、密钥形态字符串 0；`.env` 未入库；**未提交 Git**。

---

## 10. 未做

- **未修 G6**（按拍板：立为下一轮）。G6 = stability 链路断在中间 → 真实链路下 `traits`
  **结构性为空**（本次实测 `traits=0 / habits=2 / single_events=1 / experiences=1`），
  AC-7 是**保守满足**。
- 未提交 Git（1 处产品改动 + 3 个新文件 + 2 份 docs 仍为 untracked）。
- 未把真实运行扩到双人物（会引入桩对话产物）。

---

## 11. 一个供决策的观察（不在本轮范围）

本次 **6 条候选**里含 1 条 `RELATION` 维度（c6：阿雾与星灯会存在同伴关系）。在**单人物**请求下，
关系阶段被语义跳过，这条 RELATION 证据因此**已登记但不进任何档案**（复算器 A7 只校验
"引用必须命中已登记证据"，不要求"已登记证据必须被引用"）。这本身不算缺陷，但说明：

> 单人物冒烟无法覆盖 RELATION 证据的下游消费。要验证"关系证据 → 关系档案"这一段，
> 必须走双人物链路（当前双人物只在 `--dry` 的桩模型诊断里跑过）。

建议与 G6 一并纳入下一轮的范围讨论。
