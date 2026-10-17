# P4.6 阶段三：提交 · 真实运行（1 次 POST）· C 轮落库核查 · 验证边界表

- 日期：2026-10-17
- 范围：用户本轮指令的第 1~4 项（第 5 项见 `docs/PLAN_P4_7_P1A.md`）
- 性质：**含 1 次真实模型调用**（已授权：1 次 POST，≤0.0033 USD，上限 0.25 USD）
- 仓库：`D:\AIprojects\generic-persona-skill`

---

## 0. 一句话结论

阶段二成果已提交（`6efa1b7`）；真实链路跑通 1 次（5 阶段全 EXECUTED，16 条证据 / 2 份人格 / 1 份关系档案）；
**C 轮那 0.0032838 USD 确认从未落库**（三个临时库全是 0 行），已用两笔**人工补记**把它和更早的
0.0012765 一起补进账本 —— 现在"库里的合计"与"人工累计"第一次**逐位相等**（0.0078819 USD）。

但这不等于"CLI 的真实路径已可用"：本次真实运行是**经装配层驱动**的，检索与对话仍是**桩**。
边界见 §4，不许把这张表读成"全都真实验证过了"。

---

## 1. 提交（第 1 项）

| 项 | 值 |
| --- | --- |
| commit | **`6efa1b7`**（`P4.6 阶段二：业务链装配层 + 账本跨进程闸门 + 关系分析器降级`） |
| 变更 | 18 files changed, **+2400 / −111**（14 改 + 4 新增） |
| 新增文件 | `persona/orchestrator/assembly.py`、`tests/test_p4_6_assembly_ledger.py`、`docs/FIX_P4_6_ROUND.md`、`docs/PLAN_P4_6_RANGE.md` |
| 前一个提交 | `a20071f`（P4.5-B C 轮） |

### 1.1 提交前扫描

| 轮次 | 方法 | 结果 |
| --- | --- | --- |
| **严格** | `sk-*` / `tvly-*` / `AKIA*` / `LTAI*` / `ghp_*` / `xox*-` / `AIza*` / PEM 私钥 / `Bearer <token>` / JWT / `key=value` 赋值 | **0 命中**（2400 行：diff 新增行 + 新增文件全文） |
| **宽松（长 token）** | `[A-Za-z0-9_-]{32,}` | 58 处，**逐处核对全是标识符**（`WARNING_*` 常量、`test_*` 用例名），无密钥 |
| **宽松（关键词）** | `api_key/secret/password/token/credential` | 19 行，逐行核对：全是**字段名与文档说明**（`SECRET_ENV_KEYS`、`est_tokens_in`、`PERSONA_SEARCH_API_KEY` 的"未配置"说明） |
| **暂存区复扫** | 对 `git diff --cached` 再跑一次严格规则 | **0 命中** |
| **危险文件类型** | `.db/.sqlite/.log/.tmp/.bak/.env`、`^var/` | **0 个** |
| `.env` | `git check-ignore -v .env` | 仍被 `.gitignore:22` 忽略 |

暂存方式：**显式逐文件 `git add --`**（18 个路径），未用 `-A`。

---

## 2. 真实运行（第 2 项）

### 2.1 参数（与授权逐条对齐）

| 授权项 | 实际 |
| --- | --- |
| 1 次 POST | ✅ `max_calls=1`（配置）+ 1 次真实调用 |
| ≤ 0.0033 USD | 实测 **0.0033216 USD**（略高于估算，见 §5-③） |
| 上限 0.25 USD | ✅ 单任务 0.25 / 项目累计 0.25，双层都在发送前判断 |
| 合成人物 | ✅ 阿雾 / 阿灯（`FICTIONAL_CHARACTER`，作品《雾港与星灯》，版本 "2020 动画改编版"） |
| 走 assembly.py | ✅ `build_business_stack()` + `register_targets()`，与 CLI 同一个 composition root |
| 隔离库 | ✅ `%TEMP%\gps-p46-real\persona.db`，不碰仓库、不碰用户数据 |

### 2.2 执行结果

```
整体状态        : SUCCEEDED（任务 tsk_19438c… / COMPLETED）
耗时            : 8.45 s
  RESEARCH              EXECUTED   登记 3 条来源（3 检索 / 3 抓取 / 3 份材料，门禁结论 3/3）
  EVIDENCE_EXTRACT      EXECUTED   从 16 个候选中登记 16 条证据，拒收 0 条
  PERSONA_DISTILL       EXECUTED   形成 2 份人物档案
  RELATIONSHIP_DISTILL  EXECUTED   PAIR（2 人，5 条 RELATION 证据支撑）
  DIALOGUE_RUN          EXECUTED   1 轮模拟对话（SIMULATED）
来源 / 证据     : 3 / 16
人物 / 关系档案 : 2 / 1
```

回读档案（不经脚本重建，直接查库）：

| 档案 | 版本 / 状态 | 条目 |
| --- | --- | --- |
| 人物 ×2 | v1 / OK | habits 2、single_events 1、experiences 2、**traits 0** |
| 关系 ×1 | v1 / OK | shared_experiences **5**、perspectives **2**、interaction_patterns 0 |

告警（都合理，不是故障）：`NO_EVENTS ×3`（Research 只登记来源）、`MATERIAL_SUPERSEDED`
（3 份检索摘要被同来源正文取代）、`POSSIBLE_CONTRADICTION`（覆盖对存在多种说法，**分别保留**不裁决）。

### 2.3 费用与落库

| 读法 | 值 |
| --- | --- |
| 账本 hydrate（发送前） | 读入 1 笔、占用 **0.0032838**（= 回填的 C 轮） |
| 本次会话 | 1 次请求、confirmed **0.0033216** |
| 账本 `occupied_cost()` | **0.0066054**（历史 + 本次） |
| 库里行数 | 2（1 回填 + 1 真跑），合计 **0.0066054** |

**这一条是 G11 的物证**：`external_usage_records` 里第一次出现"真跑出来的那一笔"，
而不是只在进程内存里存在。

### 2.4 同一本账的跨进程复核

另起一个进程（CLI）读同一个库：

```
$ PERSONA_DATA_DIR=…\gps-p46-real python -m persona.cli.main business plan
  账本（历史）    : 已读 3 笔，占用 0.007882 USD（未知 0 笔、无法估算 0 笔）
  账本（本次）    : 占用 0.000000 USD
```

「3 笔」是 §3 补记后的结果（2 笔补记 + 1 笔真跑）。跨进程累计闸门**在真实数据上成立**。

---

## 3. C 轮落库核查（第 3 项）

### 3.1 核查方法

C 轮的库在 `tempfile.mkdtemp(prefix="gps-p45a-")`，退出不删（脚本行为）。逐个打开：

| 临时库 | 修改时间 | `external_usage_records` 行数 |
| --- | --- | --- |
| `gps-p45a-d2fozq2h` | 10-16 23:35 | **0** |
| `gps-p45a-l04vqn1v` | 10-16 22:56 | **0** |
| `gps-p45a-u2ntigno` | 10-16 22:56 | **0** |

### 3.2 结论

**确认没落库。** 原因链完整：C 轮 `use_real_extractor` 没传 `usage_sink` → 抽取器只在内存账本结算
→ `record_external_usage` 从未被调用 → 那 0.0032838 USD 只活在进程里，随进程消失。
（该缺口已在 P4.6 阶段二修复：装配层统一接 `usage_sink=storage`。）

### 3.3 补救：人工记账 → 两笔幂等补记

`hydrate` 只能读库，而库里没有这笔，所以"重新 hydrate"在物理上做不到 —— 只能**先补记再 hydrate**。
补记按"事实"写，不伪装成新调用（`provider=deepseek` 是事实，人工身份由 `trace_ref` 标明）：

| 幂等键 | 对应支出 | 金额 | task_id | trace_ref |
| --- | --- | --- | --- | --- |
| `manual-backfill:p45a-round:2026-10-16T15:00:00Z` | P4.5-A 那一轮 | 0.0012765 | `task_p45a_001` | `manual-backfill:p45a-round` |
| `manual-backfill:p45b-c-round:2026-10-17T04:54:00Z` | P4.5-B C 轮（tokens 1358/2397） | 0.0032838 | `task_p45b_c_001` | `manual-backfill:p45b-c-round` |

两笔都是 `cost_status=CONFIRMED`、`cost_source=OFFICIAL_RATE`、`currency=USD`。
时间戳统一成 UTC（与平台真实笔的 `…Z` 同口径），否则 `ORDER BY requested_at` 会把它们排错位。

### 3.4 账本与人工累计第一次对齐

| 口径 | 值 |
| --- | --- |
| 人工累计（`RUNNING.md` §5.15） | 0.0012765 + 0.0032838 + **0.0033216（本轮）** = **0.0078819** |
| 库里 `SUM(cost_confirmed)` | **0.0078819** |

差额 = **0**。从此这条不变量可被自动检查（`SUM(cost_confirmed)` 应恒等于人工累计），
下一轮起"账本说的"和"人记的"不再是两本账。

> 仍要记住的边界：这笔补记只写进了本轮使用的隔离库。产品默认库（`var/persona.db`）
> 目前**不存在**，一旦启用，需要把这两笔按同样的键补进去（幂等，重复执行不会翻倍）。

---

## 4. 真实 vs Stub 验证边界表（第 4 项 · 核心交付）

读法：**"真实"= 走的是生产代码 + 真实外部服务；"规则"= 生产代码里的确定性算法（无模型、无网络）；
"桩"= 本次运行注入的假实现；"占位"= 接口在、实现只会返回 `EXTERNAL_UNAVAILABLE`。**

| # | 能力 | 本次怎么验证的 | 结论 | 证据 | 风险 / 备注 |
| --- | --- | --- | --- | --- | --- |
| 1 | 业务链装配（composition root） | `build_business_stack()` 真跑 | **真实** | 7 个服务全部装配，`warnings=[]` | CLI 与脚本同一份代码 |
| 2 | SQLite 存储 / 档案落库 | 真跑后直接查库 | **真实** | 16 证据、2 人物档案、1 关系档案、3 用量行 | — |
| 3 | 检索（Tavily `search`） | 注入 `StubTwoTargetSourceProvider` | **桩** | 3 条查询全部由桩应答 | 真实实现 `TavilyProvider.search` 已写好，但**装配层从不构造它**（§5-①）；Q-02/Q-03 未拍板 |
| 4 | 正文抓取（Tavily `extract`） | 同上（桩同时实现 extract） | **桩** | 3 份正文来自合成样张 | 同上；正文质量门禁是真实的 |
| 5 | **claim 抽取（模型）** | **真实 deepseek-flash，1 次 POST** | **真实** | 16 条候选 / 拒收 0 / 落 16 条证据 | 这是本轮唯一花钱的环节 |
| 6 | 材料质量门禁 | 真跑 | **规则** | 3/3 材料带 `VALID` 结论才进抽取器 | `require_gate_verdict=True` 生效 |
| 7 | 证据登记 / 幂等去重 / 冲突检测 | 真跑 | **规则** | 16 条登记，0 重复；1 条 `POSSIBLE_CONTRADICTION` 分开保留 | 去重键 = 来源 + claim + 主体 |
| 8 | 稳定性判定 | 真跑 | **规则** | 16 条全部判 `SINGLE`（每条证据只有 1 个来源） | 1=SINGLE / ≥2=REPEATED / ≥3 源=PATTERN |
| 9 | 人格档案构建 | 真跑 | **规则** | 每人 habits 2 / single 1 / exp 2；**traits 0** | traits=0 是 AC-7 的**正确**结果，不是 bug（见 `PLAN_P4_7_P1A.md`） |
| 10 | 关系研判 | 默认 `RuleRelationshipAnalyzer` | **规则** | shared_experiences 5、perspectives 2 | 不是语义模型；`ProviderRelationshipAnalyzer` 仍是**占位** |
| 11 | 对话 | 注入 `StubDialogueProvider` | **桩** | 1 轮模拟对话，命名空间 SIMULATED | 产品默认 `FakeProvider` 也需要 `fixture_key`，CLI 直接跑会失败/降级（§5-①） |
| 12 | 账本 / 费用结算 | 真跑 | **真实** | confirmed 0.0033216、落库 1 行 | — |
| 13 | hydrate / 跨进程累计闸门 | 1 次真跑 + 1 次独立进程读库 | **真实** | 发送前占用 0.0032838；新进程读回 3 笔 0.007882 | 跨进程幂等也成立（`has_attempted(历史键)=True`） |
| 14 | `persona business plan` | 真实 CLI | **真实** | 打印装配快照 + 两个上限 + 账本历史 | 全程离线 |
| 15 | **`persona business run` 的完整真实路径** | **没有跑** | **未验证** | — | CLI 无材料注入通道；检索、对话都没接真（§5-①）。本次是"脚本经同一装配层驱动"，不是"CLI 直跑" |
| 16 | 任务服务 / 任务终态 | 真跑 | **真实** | `task_status=COMPLETED` | — |
| 17 | 真实检索（H-17 / P3） | 未做 | **未验证** | — | Q-02/Q-03 硬阻塞 |

**一句话**：真实已验证的是 **模型抽取 + 存储 + 账本 + 规则链路**；检索、抓取、对话仍是桩；
CLI 的"一键真跑"还没打通。

---

## 5. 本次运行暴露的问题（按严重度）

| # | 问题 | 严重度 | 说明 | 建议 |
| --- | --- | --- | --- | --- |
| ① | **CLI 无法真跑完整链路** | 中 | 装配层从不构造检索 Provider（只在 `search_provider is None` 时告警并退到 `FakeProvider`）；CLI 不给对话 Provider（默认 `FakeProvider` 要 `fixture_key`）；也没有材料注入参数。三者任一都让 `business run` 跑不出真东西 | 二选一：**(a)** 给 CLI 加 `--material-file`（离线材料注入，成本 0）；**(b)** 把 Tavily 接线进装配层（需 Q-02/Q-03 + 检索费用授权） |
| ② | 关系阶段文案误导 | 中 | `语义分析=True` 只表示"注入了分析器"，实际上是**规则**分析器，不是语义模型 | 文案改成 `analyzer=rule` / `analyzer=provider`，让人一眼看出用了什么 |
| ③ | 实测费用高于估算 | 低 | 0.0033216 > 估算 0.0033（+0.0000216，+0.65%）。闸门按估算预留、按实际结算 | 不要宣称"估算值就是上限"。0.25 的上限才是兜底；估算偏保守性可后续再评估 |
| ④ | 历史旧花费只能靠人补 | 中 | 没有产品化的补记入口，每次都要手写脚本（本轮就是这么做的） | 加 `persona usage backfill --amount --key --note`（幂等、写 `trace_ref`），并给测试 |
| ⑤ | `traits` 恒为 0 | 中 | AC-7 的正确结果，但意味着"稳定性格"这一维度目前**没有任何真实产出** | P1a 专项（见 `docs/PLAN_P4_7_P1A.md`） |
| ⑥ | 账本并发写 | 低 | 多进程同时写同一 SQLite 仍只靠主键幂等 | 高并发对账另立轮次 |

---

## 6. 未验证边界（不许当成已解决）

- 真实检索 / 抓取（Tavily）：代码在，未接线、未实跑、未计费核对。
- `ProviderRelationshipAnalyzer`：仍是占位，一调用即 `EXTERNAL_UNAVAILABLE`。
- 真实对话：没有真实对话 Provider；对话产物恒为 `SIMULATED`。
- `persona business run` 直跑：未验证（§4-15）。
- 多进程并发写账本、汇率、cache hit/miss 计价：均未处理。
- 本轮的隔离库在 `%TEMP%`，**不是**产品库；产品库启用时需要重放两笔补记。

---

## 7. 待确认

1. §3.3 的两笔人工补记，是否认可"写进账本"这种做法（我判断这是唯一能让累计闸门真正生效的路；
   纯文档记账等于闸门永远看不见这笔钱）。
2. §5-① 走 (a) 还是 (b)：离线材料注入（0 成本、立刻可用） vs Tavily 接线（需 Q-02/Q-03 与检索预算）。
3. §5-④ 是否把 `usage backfill` 做成产品命令（并补测试）。

---

## 附：本轮数字汇总

| 项 | 值 |
| --- | --- |
| 提交 | `6efa1b7`，18 files，+2400/−111 |
| 真实外部调用 | **1 次 POST**（deepseek-flash），耗时 8.45 s |
| 本轮费用 | **0.0033216 USD**（confirmed） |
| 人工累计 | 0.0045603 + 0.0033216 = **0.0078819 USD** |
| 账本合计 | **0.0078819 USD**（3 笔：0.0012765 + 0.0032838 + 0.0033216） |
| 差额 | **0** |
| 产物 | 隔离库 `%TEMP%\gps-p46-real\persona.db`；日志 `%TEMP%\p46_real_run.log`；摘要 `%TEMP%\p46_real_run.json` |
