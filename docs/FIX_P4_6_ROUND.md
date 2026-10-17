# P4.6 执行报告：装配层（P0-1）· G11 账本落库（P0-2）· 规则分析器装配（P2）

- 日期：2026-10-17
- 范围：`docs/PLAN_P4_6_RANGE.md` 的 P0-1 / P0-2 / P2（**按用户指定的顺序执行**）
- 性质：**只改代码 + 离线测试**。0 次真实外部调用，**未提交 Git**
- 决策依据：D-A ~ D-H 全部按用户拍板执行（见 §1）
- 累计费用：**0.0045603 USD**（本轮 0 次外部调用，未变）

---

## 0. 一句话结论

三件事都做完了，**账本跨进程累计闸门从"不成立"变成"成立"**，业务链第一次有了
唯一的 composition root，关系档案的 `perspectives` 不再结构性为空。
代价是三处**旧断言按新事实更新**（§5.2），没有任何一处放宽红线。

---

## 1. 决策落点（D-A ~ D-H）

| 决策 | 拍板 | 落地位置 | 可验证证据 |
| --- | --- | --- | --- |
| **D-A** | 业务链进 CLI，装配层放 `persona/orchestrator/` 作为正式 composition root | `persona/orchestrator/assembly.py`（`build_business_stack` / `register_targets`）+ `persona business plan\|run` | `TestBusinessStackAssembly`（14 项）、`TestBusinessCli`（4 项） |
| **D-B** | 项目累计封顶 + 单次 0.25 保留，**两层同时生效** | `ModelCallLimits.max_cost_total`（新增）+ `_budget_block` 两层独立判断 | `test_second_process_sees_history_and_blocks_on_project_cap`、`test_task_cap_is_scoped_to_task_id` |
| **D-C** | `cost_status=UNKNOWN` 按**最大估算**计入累计 | `ExternalUsageLedger.hydrate(unknown_fallback_cost=…)`：估算与"一次调用估算"取 `max` | `test_unknown_with_estimate_is_counted_by_the_larger_value` |
| **D-D** | 默认装配规则分析器，保留开关 | 配置 `PERSONA_RELATIONSHIP_ANALYZER=rule\|off`（默认 `rule`） | `test_default_assembles_rule_analyzer`、`test_config_can_disable_analyzer` |
| **D-E** | 分析器失败**降级 + 告警**，不整体失败 | `_step_relationship` 的 `Err` 分支改为兜底 + `RELATION_ANALYZER_DEGRADED` | `TestRelationshipAnalyzerDegradation`（2 项） |
| **D-F** | "同一说法" = 归一化完全相同（现状口径） | 未改（`_normalize_claim` 只折空白） | 本轮 P1a 未做，口径保持 |
| **D-G** | P1b 语义聚类暂不引入模型 | 未做（另立轮次） | — |
| **D-H** | P3 质量波动预案等 Q-02/Q-03 | 未做（P3 未启动） | — |

---

## 2. P0-1：装配层（composition root）

### 2.1 之前的问题（G4 的同型风险）

`BusinessOrchestrator` 的依赖**只在 `scripts/` 与 `tests/` 里组装**：`cli/main.py`
一句构造 `BusinessDeps` 的代码都没有。后果不是"少个命令"，而是每个"把能力装进产品"
的需求都在脚本里再写一遍装配——三份装配必然漂移。**C 轮就是活证据**：
`e2e_p4_5a.py` 构造抽取器时没传 `usage_sink`，于是那 0.0032838 USD
**只活在进程内存里，没进任何库**。

### 2.2 现在唯一的一份

```python
build_business_stack(settings, ctx, *, storage=None, ledger=None,
                     search_provider=None, chat_provider=None,
                     dialogue_provider=None, claim_extractor=None,
                     relationship_analyzer=None, options=None)
    -> Result[BusinessStack]
```

- 装配全部业务服务（Evidence / Persona / Relationship / Dialogue / Research / Task）
- 把 `SqliteStorage` **同时**接到用量落库（`usage_sink`）与账本 hydrate；
- 返回 `BusinessStack`，`describe()` 给出可打印的装配事实（不含任何凭据）；
- **只装配不发请求**：`build_chat_provider=False` 是默认值，真实 Provider 必须由
  调用方显式要求（`persona business run` 才这么做）。

`register_targets()` 也在这里，而不是 CLI：CLI 不得直接驱动业务模块（F-7 / D-059）。
这个位置是被**静态检查逼出来的**——最初把 `PersonQueryTarget` 的构造写在 CLI 里，
`test_dependency_rules` 立刻报 F-7 违规（§5.1 第 1 条）。

### 2.3 e2e 脚本改为复用装配层

`Stack.__init__` 不再自己 new 服务，改为调 `build_business_stack(...)`；
`Stack.orchestrator()` 只 `replace(deps, claim_extractor=…, relationship_analyzer=…)`，
不再重建整份依赖。**行为逐字不变**：`test_p4_5a_e2e` 82 项全绿。

顺带修掉了一个真实缺口：`use_real_extractor` / `use_stub_extractor` 现在都传
`usage_sink=self.storage`，与产品装配一致（e2e 也 82 项全绿，说明落库不影响判定）。

### 2.4 CLI

```
$ persona business plan
业务链装配快照（**离线**：不联网、不调模型、不产生费用）
  数据库          : ...\var\persona.db
  组件            : evidence/persona/relationship/dialogue/research/task_service
  claim 抽取器    : 未装配（未配置模型 Provider）
  关系分析器      : RuleRelationshipAnalyzer
  单任务费用上限  : 0.25 USD（按 task_id 累计，含同任务历史）
  项目累计上限    : 0.25 USD（整本账累计，含 hydrate 读回的历史）
  账本（历史）    : 已读 0 笔，占用 0.000000 USD（未知 0 笔、无法估算 0 笔）
  账本（本次）    : 占用 0.000000 USD
  装配告警        : SEARCH_PROVIDER_OFFLINE, EXTRACTOR_ABSENT
```

```
$ persona business run --names 阿雾,阿灯 --work-title … --adaptation-version …
拒绝运行：没有可用的 claim 抽取器。
  装配告警：按配置构造聊天 Provider 失败（VALIDATION.required_field）：model.provider 未设置…
  说明：本次**没有发起任何请求，也没有产生费用**（真实调用需单独授权）。
```

`run` 的四条前置拒绝（都在**任何请求之前**）：
① 无 `--names`；② 虚构角色缺作品/适用版本（PF-V5 / D-115）；
③ 无抽取器（未配模型）；④ 登记实体失败。任一命中 → 0 次调用。

### 2.5 与范围建议的**偏差**（必须说清）

范围建议写 P0-1 的验收是"CLI 能跑一次五阶段业务链（离线 Mock）"。实际交付是
**CLI 能离线打印装配快照 + 拒绝式运行**，"五阶段离线跑通"由
`test_p4_5a_e2e`（82 项，脚本经同一装配层）证明，而不是由 CLI 演示。

理由：CLI 要离线跑完整链，就必须内置一套合成材料 + 桩 fixture 的演示数据
（= 把测试夹具搬进产品代码）。那会让产品里出现"看起来能生成档案"的假数据路径，
比不做更危险。**如果要求 CLI 也能离线演示，需要你确认是否允许产品代码内置演示夹具。**

---

## 3. P0-2 / G11：账本落库与两层闸门

### 3.1 `hydrate()` 的计入口径（D-C：宁可保守）

| 记录状态 | 计入累计的金额 | 理由 |
| --- | --- | --- |
| `cost_confirmed` 有值 | **实际值** | 历史确认过的就是事实 |
| 只有 `cost_estimate` | `max(估算, 一次调用估算)` | 估算填低了会让累计闸门**少算**，这是唯一不可接受的误差方向 |
| `UNKNOWN` 且两者都没有 | `unknown_fallback_cost`；连它也没有 → 记 0 **并单独计数** | D-060：不知道不等于零；无法界定的笔数由闸门停手 |
| `NOT_CHARGED` | 0 | 官方明确"没产生费用"的断言 |

### 3.2 两本账，故意的

`_records`（本次进程）与 `_history`（hydrate 读回）**分成两本**：

- `occupied_cost()` = 两本之和 → **项目累计上限**看这个；
- `records()` 只返回本次 → `_chat_calls()` 数"本次调用了几次"用这个；
- `occupied_cost_for_task(task_id)` → **单任务上限**看这个（含同任务历史）。

为什么必须分：若把历史笔混进 `records()`，昨天花过的 1 次会让今天一启动就
"次数已满"——那不是保守，那是 bug。`test_history_does_not_pollute_session_counts`
把这条钉住。

### 3.3 两层闸门（D-B）

`_budget_block` 现在按顺序判：

1. 次数上限；
2. 任一层设了费用上限但**单价缺失** → 拒绝（无法估算 = 无法保证不超支）；
3. **单任务**：`occupied_cost_for_task(task_id) + 本次估算 > max_cost` → 停；
4. **项目累计**：`occupied_cost() + 本次估算 > max_cost_total` → 停；
   若有"未知且无任何估算"的历史笔 → 直接停（金额无法界定）；
5. 本次进程的未知笔数达上限 → 停。

**为什么配置校验要求 `单任务 ≤ 总上限` 之后，累计层还有意义**：累计层看的是
**别的任务/别的运行**已经花掉的钱。`test_second_process_sees_history_and_blocks_on_project_cap`
就是这个场景：任务 B 自己一分没花（单任务层放行），但整本账已被任务 A 花了 0.003，
再加一次 0.00301 的估算 ⇒ 累计层拦住。

### 3.4 历史未知不会永久关闸

`should_pause()` 的"未知笔数"规则**只数本次进程**。历史未知已经按保守金额计入
累计（金额维度保守），不需要让闸门永久关闭——`confirm()` 只作用于内存，
永久关闸等于这本账再也用不了。这条写在 `hydrate()` 与 `should_pause()` 的
docstring 里，并有 `test_historical_unknown_does_not_lock_the_gate_forever` 锁定。

### 3.5 hydrate 失败怎么办

| 情形 | 行为 |
| --- | --- |
| 读历史失败 **且** 配了项目累计上限 | **拒绝装配**（`BUDGET_EXCEEDED`）：读不到累计支出就无法保证不超支 |
| 读历史失败 **且** 没配累计上限 | 降级 + `LEDGER_NOT_HYDRATED`/`LEDGER_HYDRATION_FAILED` 告警 |

### 3.6 验收对照（范围建议 §2 P0-2 的 6 条）

| 验收项 | 结果 |
| --- | --- |
| 进程 A 花 0.10 → 进程 B `occupied_cost() ≥ 0.10` | ✅ `test_second_process_sees_history_and_blocks_on_project_cap` |
| 全局上限收紧后进程 B 在**发送前** `BUDGET_EXCEEDED`（0 次调用） | ✅ 断言 `provider.calls == 0` |
| `UNKNOWN` 按保守值计入（不当 0） | ✅ 3 项单测 + `test_unpriced_history_blocks_before_sending` |
| 同一 `idempotency_key` 跨进程重放被拦、`requests` 不增 | ✅ `test_duplicate_key_is_skipped_before_any_budget_check` |
| 迁移 | ✅ 不需要：表 `external_usage_records` 早已存在，`CREATE TABLE IF NOT EXISTS` 对旧库平滑 |
| 全量回归全绿 + 新增 ≥10 项 | ✅ 新增 33 项（G11 相关 20 项） |

---

## 4. P2：规则分析器装配与降级

### 4.1 装配（D-D）

- 默认注入 `RuleRelationshipAnalyzer()`（离线确定性、零成本、条目全部可追溯到已登记证据）；
- 配置 `PERSONA_RELATIONSHIP_ANALYZER=off` → 不注入，`RELATIONSHIP_ANALYZER_DISABLED` 告警；
- `StackOptions.enable_relationship_analyzer=True/False` 可一次性覆盖配置（测试用）。

### 4.2 降级（D-E）

修复前：分析器返回 `Err` → `return outcome`，**整条链判 PARTIAL/FAILED**。
现在：降级到兜底 → `shared_experiences` 照常产出，`perspectives` / `feeling_toward`
留空并记 `open_questions`，同时留 `RELATION_ANALYZER_DEGRADED` 告警（**不允许静默**）。

为什么不是"整体失败"：分析器只补方向性视角（增强项），`shared_experiences` 这类
底线条目不依赖它。用可选能力的故障否定已经成立的事实（证据已登记、登记已校验），
是把"部分成功"错判成"失败"。

红线保留：降级路径**不得**凭空产出视角（`test_degraded_relationship_stays_insufficient_not_fabricated`）。

---

## 5. 测试

### 5.1 新增

| 文件 | 内容 | 数量 |
| --- | --- | --- |
| `tests/test_p4_6_assembly_ledger.py` | hydrate 口径 11 项 / 两层闸门 5 项（含跨进程）/ 装配层 14 项 / CLI 4 项 | **33** |
| `tests/test_business_orchestrator.py` | `TestRelationshipAnalyzerDegradation`（降级 + 不臆造） | **2** |

### 5.2 按新事实更新的旧断言（3 处，**无一处放宽**）

1. `tests/test_p4_4b_model_setup.py::test_plan_shows_both_ceilings_and_the_effective_one`
   旧：`"暂取同一个值" in notes`（编码的是"只执行一个上限"的历史缺陷）
   新：`max_cost_total_effective == 0.25` + `"两层都会被判断" in notes`（**更强**）
2. `scripts/e2e_p4_5a.py` 的抽取器装配新增 `usage_sink`（补上 C 轮缺口，不是放宽）
3. `tests/test_p4_5a_e2e.py` 未改（82 项原样通过）

### 5.3 全量回归

`python -m unittest discover -s tests -t .` → **见 §5.4**（最后一轮结果）
基线：P4.5-B C 轮 1204 项 → 本轮 1239 项（+35：33 新增 + 2 降级）。

### 5.4 结果

| 命令 | 结果 |
| --- | --- |
| `python -m unittest tests.test_p4_5a_e2e` | **82 项 OK**（装配层改造后逐字不变） |
| `python -m unittest tests.test_p4_6_assembly_ledger` | **33 项 OK** |
| `python -m unittest tests.test_business_orchestrator` | **OK**（含新增降级 2 项） |
| `python -m unittest discover -s tests -t .` | **1239 项 OK**（216.8 s） |

基线对照：P4.5-B C 轮 **1204 项** → 本轮 **1239 项**（+35 = 33 新增 + 2 降级）。
输出里的 `ResourceWarning: unclosed database` 是既有测试风格的噪音（不是本轮引入），
不影响用例结果。

另做了一次**真实配置**的离线快照（`PERSONA_DATA_DIR` 指向临时目录，避免在仓库
里生成 `var/`）：单任务 0.25 / 累计 0.25 / 单次估算 0.003300 USD / 账本历史 0 笔
——与 C 轮实测的 0.0032838 USD 同一量级，说明单价与估算链路没被改动。

---

## 6. 未做 / 已知边界（不许当成"已解决"）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| P1a 同句多源（traits 非空） | **未做** | 本轮范围只到 P0-1/P0-2/P2 |
| P1b 语义聚类 | 未做 | D-G：暂不引入模型 |
| P3 真实检索 | 未做 | Q-02 / Q-03 未拍板，硬阻塞 |
| 真实模型 / 搜索调用 | **0 次** | 本轮全部离线；`business run` 在未配置时**拒绝** |
| 账本并发写 | 未处理 | 多进程同时写同一 SQLite 仍靠主键幂等；高并发对账另立轮次 |
| 历史旧花费 | 未回填 | C 轮的 0.0032838 USD 从未落库，只能人工记账（`RUNNING.md` §5.15） |
| CLI 离线演示五阶段 | 未做 | 见 §2.5（需要你确认是否允许产品内置演示夹具） |
| `ProviderRelationshipAnalyzer` | 仍是契约占位 | 一调用即 `EXTERNAL_UNAVAILABLE`；真实语义分析需模型授权 |

---

## 7. 待你确认

1. **CLI 是否要能离线演示五阶段**（需内置演示夹具）——见 §2.5；不做则当前形态即最终形态。
2. `business run` 的**真实**路径（模型已配置时）本轮**没有实跑**：需要密钥与真实调用授权。
   逻辑路径已由桩 Provider 覆盖（`TestTwoLayerBudgetGate` 5 项），但"真跑一次"未验证。
3. 是否按 §5.2 的三处断言更新入库（我判断它们是"事实变了"，不是"放宽标准"）。

---

## 回归结果

| 项 | 值 |
| --- | --- |
| 全量用例 | **1239 项，全部通过** |
| 用时 | 216.8 s |
| 环境 | Python 3.13.12（managed），全程离线 |
| 外部调用 | **0 次**（本轮累计费用不变：0.0045603 USD） |
| 工作区 | 未提交 Git（等你验收）；仓库内**无** `var/`、无 `.db`/`.log`/`.tmp` |
