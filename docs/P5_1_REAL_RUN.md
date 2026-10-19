# P5.1 受控真跑清单（**已执行，见 §9 实测**）

> 状态：**第一次已执行（2026-10-19 20:20）**；**第二次待授权（见 §10）**。
> 第一次真实账单见 §9.1，验收结论见 §9.3：6 条里 **5 条通过、1 条不通过**
> （DIALOGUE_RUN FAILED），根因是两个**产品路径上的既有缺陷** D-A / D-B（§9.4，
> 与 P5.1 新增代码无关，但被这次真跑第一次暴露出来）。
> **两个缺陷已修**（§9.4）、回归 1712 全绿、并顺手做了装配层默认值审计（§11，
> 新发现 D-C：`task_id` 进不了账本，未修）。**Git 未提交**，等第二次真跑通过再提交。
>
> 目标只有一个：**验证六条命令能在真库上串起来**。内容质量（特征准不准、措辞好坏）
> 属于 P5.0 的事，这次不做评估——用一次真跑同时评质量，会把两件事的判断搅在一起。

---

## 1. 唯一要执行的命令

```bash
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 \
  --adaptation 曹雪芹原著 --vocabulary l2 --budget 0.05 \
  --out %TEMP%\gps-p51 --yes
```

为什么是这几个参数：

| 参数 | 为什么这么给 |
| --- | --- |
| `--names "林黛玉,贾宝玉"` | 两个人才能同时产出**关系档案**（单目标不生成关系），一次覆盖 `show` 与 `show-relationship` 两条命令 |
| `--work-title 红楼梦` | 虚构角色必填（PF-V5），缺了会在**任何调用之前**停住 |
| `--adaptation 曹雪芹原著` | 显式给值，顺手验证清单里打的是「显式指定」而不是「未指定，默认」 |
| `--vocabulary l2` | 42 个中文古典标签；L1 的 `SPEECH_STYLE` 族是空的（D-286），用 L1 会漏掉黛玉最核心的"说话方式" |
| `--budget 0.05` | 比默认 0.25 更严，刻意压小：这次只要"跑通"，不要跑满 |
| `--out %TEMP%\gps-p51` | 独立目录，不污染已有库（R4.8 的真库在别处，不动它） |
| `--yes` | 非交互环境（自动化/粘贴执行）必须显式确认；**它不会写确认文件**，下次还得重新给 |

---

## 2. 预估费用（数字来自 `--dry`，已实测）

先用下面这条拿到与真跑**同一份**清单（`--dry` 只打清单，一个请求都不发）：

```bash
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 \
  --adaptation 曹雪芹原著 --vocabulary l2 --budget 0.05 \
  --out %TEMP%\gps-p51 --dry
```

实测输出（2026-10-19，本机配置）：

```
本次将要发出的真实调用（钱从这里开始算）
  研究对象      : 林黛玉、贾宝玉
  作品 / 改编版本: 红楼梦 / 曹雪芹原著（显式指定）
  检索          : 2 次（模板查询，basic ≈2.2 credits）
  抓取正文      : 上限 1 次 + 补救 1 次
  模型抽取      : 1 次
  模拟对话      : 1 次
  词表          : l2（42 个标签；--vocabulary l2 启用 42 个中文古典标签）
  模型费用上限  : 0.05 USD（单任务，按 task_id 累计）
  预估模型费用  : 0.006600 USD（仅估算；实际以账单为准）
  账本已占用    : 未读取（--dry 不碰数据库，累计已用额未知；上面的"实际生效"未扣除历史支出）
  边界          : 清单之外不会有任何调用；出现清单外的调用即为缺陷
```

| 项 | 数量 | 钱 |
| --- | --- | --- |
| 检索（Tavily basic） | 2 次 | ≈ **2.2 credits**（在免费额度内，不产生现金） |
| 正文抓取 | 上限 1 次 + 补救 1 次 = **≤2 次** | 0（本地 HTTP，不经付费 API） |
| 模型抽取 | 1 次（两目标**一次批量**） | — |
| 模拟对话 | 1 次（一轮只出 1 个 turn） | — |
| 模型合计 | 2 次调用 | **≈0.0066 USD**（按 `PERSONA_MODEL_PRICE_*` 预留价估） |
| 硬上限 | — | **0.05 USD**（`--budget`，超过即拒跑，不会"先花再说"） |

**两个必须知道的偏差**

1. **抓取只有 1 次**（不是 3 次）：上限来自 `PERSONA_RESEARCH_MAX_EXTRACTS=1`。
   想让档案更厚，改这个配置再跑，但那是**下一次**的事——这次保持原配置，只为验证
   "配置怎么写就怎么跑"。
2. **`--dry` 读不到账本历史**（它不建表、不读库），所以清单里的"实际生效 0.05"
   **没有扣除本库已花掉的钱**。真跑时装配层会 hydrate 历史支出，真正生效的上限
   只会 ≤0.05。

---

## 3. 边界（这次会发生什么、不会发生什么）

**会发生**

- 2 次 Tavily 检索（模板查询：`"<名字>" "<作品>"` 那类确定式查询，不让模型自由发挥）
- ≤2 次正文抓取（其中 1 次是摘要不完整时的补救）
- 1 次批量抽取（两个目标写在同一个请求里）+ 1 次对话生成
- 新建 `%TEMP%\gps-p51\persona.db`（schema 在这里初始化，`run` 是唯一会建表的命令）
- 在 `%TEMP%\gps-p51\` 下写 `.persona_consent.json`（`--yes` 不写；只有 TTY 里答 `y` 才写）

**不会发生**

- 不会调用清单之外的任何 API（出现即为缺陷 → 见停止条件 ①）
- 不会碰 R4.8 的真库（`--out` 是独立目录）
- 不会注入探针、不会回放材料（`--probe-*` / `--replay-materials` 只存在于 e2e 脚本，
  `persona run --help` 里没有这些参数）
- 不会自动登记"以后都同意"（`--yes` 是一次性的）
- 不会因为"链接看起来像真库"就往真实库里写测试数据

---

## 4. 停止条件（任一命中，立刻停手）

| # | 条件 | 怎么发现 | 停下来做什么 |
| --- | --- | --- | --- |
| ① | **出现清单外的调用** | 输出里有清单未列出的检索/模型调用；或账单条目数 > 2 次模型调用 | 立即杀进程，记录当时的输出，按缺陷处理（这是"清单即承诺"的直接违约） |
| ② | **实际费用 > 0.05 USD** | `run` 结束打印的费用行 > 0.05 | 立即停，之后不再重跑；先查单价配置与 token 估算口径 |
| ③ | **探针参数被接进产品路径** | `persona run --help` 出现 `--probe` / `--replay-materials` / `--allow-real` | 立即停，本轮设计上不允许（§12 纪律 1） |
| ④ | **库被写到了预期之外的位置** | `%TEMP%\gps-p51\persona.db` 不存在，却在别处出现新库 | 停手，先确认 `--out` 是否被配置覆盖；只读命令不许建库 |
| ⑤ | **`run` 中途抛异常/退出码非 0（且原因不是预算）** | 退出码 + 错误文案 | 停手并保留输出；预算类拒绝（"预算不足"）不算失败，那是闸门在工作 |

预算类拒绝的正确形态（**这是功能正常的证明**，不是故障）：

```
预算不足：本次计划 2 次模型调用、预估 0.0066 USD，--budget 只给了 0.005。
  要么调大 --budget，要么少传几个名字。
  说明：本次**没有发起任何请求**。
```

---

## 5. 跑完之后要验的 6 条

命令都在 `%TEMP%\gps-p51` 这个库上跑（`--db` 显式指路，避免看错库）：

```bash
set DB=%TEMP%\gps-p51\persona.db

persona show 林黛玉 --db %DB%
persona show-relationship 林黛玉,贾宝玉 --db %DB%
persona evidence 林黛玉 --db %DB%
persona export 林黛玉 --format md --out %TEMP%\gps-p51\林黛玉.md --db %DB%
persona status <task_id> --project prj_local            # task_id 从 run 的输出里抄
```

| # | 要验的 | 通过长什么样 |
| --- | --- | --- |
| 1 | 六条命令串起来 | 五条只读命令退出码 0（`show` 在无档案时才是 1），`status` 能打印数据库路径与"下一步" |
| 2 | `show` 的来源数与 `evidence` 的 URL 一一对应 | `show` 里"基于 N 条证据 / M 个来源"与 `evidence` 里实际列出的条数、去重来源数一致 |
| 3 | 空分区如实说 | 没有内容的分区打 `（无）` + 原因，不是省略 |
| 4 | 未并入有交代 | `show` 末尾有"另有 K 条证据未并入档案（维度分布…）"，且 `evidence` 的"未并入档案的证据"小节条数一致 |
| 5 | 导出可直接粘 | `林黛玉.md` 里没有 `evd_` ID、没有 `confidence` 数字，每条特征后带"N 个来源支持" |
| 6 | 再次运行不再询问 | 同目录第二次 `python -m persona run ...`（TTY 下）打印"此前已确认过真实调用"，不再问 `[y/N]` |

---

## 6. 善后

```bash
# 想留着看：什么都不用做，库在 %TEMP%\gps-p51\persona.db
# 想彻底清掉（含确认记录）：
rmdir /s /q %TEMP%\gps-p51
```

- 删除数据目录 = 删除确认记录（`.persona_consent.json` 就在里面），下次会重新问一次。
- 这次跑出来的库**不进 Git**（在 `%TEMP%` 下，且 `.gitignore` 已忽略 `*.db`）。

---

## 7. 这次不验的（别把两件事混在一起）

- **内容质量**：特征准不准、措辞像不像——P5.0 的事。
- **多轮对话质量**：这次 `max_rounds=1`，只出 1 个 turn。
- **多人（≥3）群体档案**：结构已存在，但没有真实跑过（§3.4 已备注）。
- **`export --format json`**：本期明确拒绝（需要 schema 约定）。

---

## 8. 等你一句话

看完这份清单，如果要跑，回一句"**授权真跑**"即可；跑完我把真实输出、实际费用与
6 条验收结果逐条贴回来。如果要改参数（名字、作品、预算、抓取上限），先说改哪一项，
我更新这份清单再等你确认——**不会先跑一版"试试看"**。

---

## 9. 实测记录（2026-10-19 20:20，`PERSONA_RESEARCH_MAX_EXTRACTS` 临时 =3，已恢复 1）

### 9.1 实际发生的调用

| 项 | 预估（清单） | **实际** | 来源 |
| --- | --- | --- | --- |
| 检索 | 2 次 / ≈2.6 credits | **2 次全成功 / 2.0 credits**（每笔 1.0，CONFIRMED） | `external_usage_records` |
| 正文抓取 | 上限 3 次 + 补救 1 次 | **0 篇**（一次都没发） | 库里**没有** EXTRACT 用量记录 |
| 模型调用 | 2 次（抽取 1 + 对话 1） | **1 次**（只有抽取；对话在调用前失败） | `external_usage_records` |
| 模型 token | 估 3000 in / 2000 out | **10492 in / 4711 out** | `usage_records` |
| 模型费用 | 0.006600 USD | **0.0088008 USD**（OFFICIAL_RATE / CONFIRMED） | `external_usage_records` |
| 账本累计 | — | **0.0088008 USD**（本库首跑） | `run` 输出"账本已占用 0.000000"→跑完 0.0088008 |
| 硬上限 | 0.05 USD | 未触发（实际占 17.6%） | — |

库落在 `%TEMP%\gps-p51\persona.db`（204,800 字节），**没有**在别处建库。

### 9.2 停止条件逐条

| # | 条件 | 结果 |
| --- | --- | --- |
| ① | 清单外调用 | **未触发**：检索 2 = 计划 2，模型 1 < 计划 2，抓取 0 ≤ 4 |
| ② | 费用 > 0.05 | **未触发**（0.0088） |
| ③ | 探针参数进产品路径 | **未触发**：`persona run --help` 无 `probe` / `replay` / `allow-real` |
| ④ | 库写到预期外位置 | **未触发** |
| ⑤ | 中途异常/退出码非 0（非预算） | **触发**：退出码 1，`DIALOGUE_RUN FAILED`（见 §9.4 D-A） |

### 9.3 6 条验收

| # | 项 | 结果 |
| --- | --- | --- |
| 1 | 命令跑通、五阶段全 EXECUTED | ❌ **不过**：`RESEARCH / EVIDENCE_EXTRACT / PERSONA_DISTILL / RELATIONSHIP_DISTILL` 四阶段 EXECUTED，`DIALOGUE_RUN` FAILED |
| 2 | 人物档案非空 | ✅ 黛玉 v1 OK：稳定性格 0 / 习惯 1 / 说话方式 1（2 源 3 证据）/ 单次行为 4 / 背景 1 / 经历 1，基于 13 条证据 / 3 个来源 |
| 3 | 关系档案非空 | ✅ PAIR v1 OK：双向视角各 3、共同经历 3、互动模式 0、情感表达习惯 1、不对称说明 1 |
| 4 | 四条只读命令可用 | ✅ `show` / `show-relationship` / `evidence` / `export`（含 `--format json` 拒绝）退出码全 0；`status` 打印阶段 4/5 + 数据库路径 + 最近错误 |
| 5 | 输出与方案示例对齐 | ✅ 六分区顺序、`（无）`+原因、"未并入"说明（`CANON_FACT 2 / RELATION 4`，与 `evidence` 的 6 条一致）、证据树 `├/└`、来源数去重（2 证同源→1）全部对齐；`export` 的 md 无 `evd_` ID、无 confidence |
| 6 | 实际费用 vs 预估 | ⚠️ 差异 **+33%**（0.0088 vs 0.0066），绝对差 0.0022 USD：真实 token 是估算常量的 3.5 倍/2.4 倍。**在预算内**，但"预估"口径偏低，见 §9.5 |

### 9.4 根因：两个产品路径上的既有缺陷（都不是 P5.1 新代码）

> **状态：已修（2026-10-19 晚）**。修法与回归测试见下；第二次真跑（§10）验证。

**D-A 对话阶段永远拿不到真实 Provider → 真跑必 FAILED**

- `persona/orchestrator/assembly.py:731`：`provider=dialogue_provider or FakeProvider()`；
  `StackOptions` **没有** `dialogue_provider` 这一项，`persona run` 从不传 → 恒为 `FakeProvider`。
- `FakeProvider.chat` 要求预先登记的 `chat:dialogue_turn:<entity_id>` fixture
  （`persona/providers/mock.py:135`），真跑的实体 ID 是现生成的 → 必报
  `VALIDATION.required_field 未登记的 fixture`。
- 之前没暴露：离线测试与 e2e 都显式注入 `dialogue_provider`；而 `run` 走真实链路是第一次。
- 最小修法：把 dialogue 的构造移到 chat provider 之后，`provider=dialogue_provider or chat_provider or FakeProvider()`
  （`allow_real=False` 时 `chat_provider` 本来就是 None，闸门不受影响）。
- **已落地**：`assembly.py` 新增第 5.5 步"对话服务"，并新增告警
  `DIALOGUE_PROVIDER_OFFLINE`——**回落不再静默**：装配期就说明"真跑会在
  DIALOGUE_RUN 失败"。CLI 清单同步加一行：答应了"模拟对话 N 次"却用桩时，
  当场标 `⚠️ DIALOGUE_RUN 会失败`。

> **状态：已修（2026-10-19 晚）**。修法与回归测试见下；第二次真跑（§10）验证。

**D-B 正文抓取在整个产品路径上从未发生 → `PERSONA_RESEARCH_MAX_EXTRACTS` 是死配置**

- `StackOptions.extraction_strategy` 默认 `None`（`assembly.py:150`），只有测试注入
  `TopNExtractionStrategy()`；`research.py:1461` 拿到 `None` 就直接 `rule="no_extraction"` 返回。
- 所以这次把上限从 1 调到 3 **没有任何效果**（0 篇 → 仍 0 篇），清单上那行
  "抓取正文 上限 3 次 + 补救 1 次" 是**假账**——它承诺了一件永远不会发生的事。
- **已落地**（两条都做了）：
  1. 装配层：授权真实外呼且检索出口是真实 Provider 时，默认装
     `TopNExtractionStrategy()`；`max_extracts` 仍按配置走（不覆盖 R3 的修复）。
  2. 诚实告警 + 诚实清单：没启用时报 `EXTRACTION_STRATEGY_DISABLED`，
     清单打印 `抓取正文: 0（未启用正文抓取：…配置 PERSONA_RESEARCH_MAX_EXTRACTS=N 不生效）`，
     不再假打"上限 3 次"。
  3. 装配事实可读：`stack.describe()` 新增 `components.extraction_strategy`
     与 `components.dialogue_provider`，清单据此打印，不许照配置猜。

### 9.5 记一笔：费用估算口径**系统性偏低 33%**（调估算常量的依据）

一次真实抽取的实测值，与清单所用估算常量的对照：

| 项 | 估算常量（`PERSONA_MODEL_EST_TOKENS_*`） | **实测**（2026-10-19，2 目标 / 10 份检索摘要） | 倍数 |
| --- | --- | --- | --- |
| 输入 token | 3000 | **10492** | **3.50×** |
| 输出 token | 2000 | **4711** | **2.36×** |
| 单次费用 | 0.0033 USD | **0.0088008 USD** | **2.67×**（整轮 1 次调用，故 1.33× 于"2 次调用"的预估） |

单价口径（`.env`）：`price_in_per_1m=0.3` / `price_out_per_1m=1.2`，
验算 `10492/1e6×0.3 + 4711/1e6×1.2 = 0.0088008` ✓ 与 `external_usage_records` 的
`cost_confirmed` 完全一致，说明**偏差只来自 token 估算，不来自单价**。

结论与后续：

1. 要"预估可信"，把常量调到实测量级（**建议 `EST_TOKENS_IN≈10500 / OUT≈4700`**，
   对应 2 目标 + 10 份摘要材料的一次批量抽取；目标数或材料数变化时应再取一次实测）。
2. 更稳的做法：抽取完成后按真实 token **回填**估算（账单已有 `PROVIDER_USAGE`），
   让"预估"在下一次清单里自动收敛。
3. **不修也能用**：硬上限 `--budget` 在兜底，偏差只会让"看起来还剩很多"，
   不会真超支。所以这条不阻塞 P5.1 提交。
4. CLI 清单已加"口径提醒"一行（实测曾为估算值的 1.3 倍），读者不会只看到低估的那个数。

---

## 10. 第二次真跑清单（修完 D-A / D-B 之后，**已执行**）

> 状态：**已执行（2026-10-19 22:06）**。实测见 **§10.7**，验收结论 **8/9 通过**：
> D-A、D-B 两个修复都被独立证据证实生效；唯一不过的是第 7 条（正文证据可回溯），
> 根因是一个**新的同类缺陷 D-D**（`locator.field` 硬编码 `"snippet"`），见 §11.2。

### 10.1 命令（与第一次同参数，只换输出目录）

```bash
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 \
  --adaptation 曹雪芹原著 --vocabulary l2 --budget 0.05 \
  --out %TEMP%\gps-p51-r2 --yes
```

- **换目录**（`gps-p51-r2`）而不是复用 `gps-p51`：第一次的库里已经有同名实体
  （`register_targets` 不去重）和 0.0088 USD 的历史支出，混在一起既看不清
  本次抓了几篇，也会让"账本已占用"失去对照意义。
- `--yes` 同前：非交互环境一次性授权，**不写确认文件**。

### 10.2 抓取上限怎么给（`PERSONA_RESEARCH_MAX_EXTRACTS`）

D-B 修好后这个配置**第一次真正生效**（第一次真跑它完全是死配置）。两个选项：

| 选项 | 抓取 | credits | 模型费用（估） | 取舍 |
| --- | --- | --- | --- | --- |
| **A（推荐）保持 3** | ≤3 篇 + 补救 ≤1 | ≈2.8 | ≈0.017 USD | 当初临时调到 3 就是为了看"抓正文"这条链路；现在它真的会跑了，用 3 才能验证到位，档案也更厚 |
| B 恢复 1 | ≤1 篇 + 补救 ≤1 | ≈2.4 | ≈0.013 USD | 最省；但只抓 1 篇，验证强度弱（"抓到了"和"抓够了"分不开） |

**推荐 A**，跑完恢复 1（与第一次同样的处理）。若你选 B，只需把配置留在 1，其余不变。

### 10.3 新预估（**我的口径**，不是清单那个偏低的数）

清单仍会打印 `预估模型费用 0.006600 USD`（它按 EST_TOKENS 常量算，见 §9.5）；
下面这行是按**第一次实测 token** 外推的更可信数字：

| 项 | 数量 | 钱 / credits |
| --- | --- | --- |
| 检索（Tavily basic） | 2 次 | **2.0 credits**（实测 1.0/次，CONFIRMED） |
| 正文抓取 | ≤3 次 + 补救 ≤1 | **≤0.8 credits**（0.2/篇，清单口径） |
| 模型抽取 | 1 次（2 目标批量） | 材料从"10 份摘要"变成"10 份摘要 + ≤3 篇正文"，估 in≈22k / out≈6k → **≈0.0138 USD** |
| 模拟对话 | 1 次（1 轮 1 turn） | 估 in≈6k / out≈0.8k → **≈0.0028 USD** |
| **合计** | — | **≈0.017 USD（保守上界 0.025）+ ≈2.8 credits** |
| 硬上限 | — | **0.05 USD**（超过即拒跑，不会先花再说） |

对照第一次实测 0.0088 USD：**第二次约为其 2 倍**（多一次对话 + 更多材料），
仍在上限的 34%~50% 区间。

### 10.4 边界

**会发生**

- 2 次 Tavily 检索（模板查询，确定性）
- **≤4 次正文抓取**（3 次初始 + 1 次低质量补救；补救只在正文没过质量门禁时发生）
- 1 次批量抽取 + **1 次对话生成**（D-A 修好后对话走真实 DeepSeek，不再碰 FakeProvider）
- 新建 `%TEMP%\gps-p51-r2\persona.db`；**每笔用量都带 `task_id`**（见 §11 的 D-C：
  第一次真跑的 `task_id` 全是 `null`，这次要确认是否仍如此）

**不会发生**

- 不会调用清单之外的 API（出现即为缺陷）
- 不会碰 `%TEMP%\gps-p51`（第一次的库）与 R4.8 的真库
- 不会自动登记"以后都同意"

### 10.5 停止条件（任一命中立刻停手）

| # | 条件 | 怎么发现 | 停下来做什么 |
| --- | --- | --- | --- |
| ① | 清单外调用 | 账单条目 > 2 次模型调用；检索 ≠ 2 次；抓取 > 4 次 | 立即杀进程，按缺陷处理 |
| ② | 实际费用 > 0.05 USD | `run` 结束的费用行 | 立即停，不再重跑；查单价与 token 估算 |
| ③ | **DIALOGUE_RUN 仍 FAILED** | 阶段行不是 5/5 | 停手保留输出：D-A 没修好，或真实 Provider 侧另有原因 |
| ④ | **抓取仍是 0 篇** | 阶段行"带回 N 份检索摘要、**0 份抓取正文**" | 停手：D-B 没修好（可能是合规拦截或 Provider 不支持 extract） |
| ⑤ | 抓取失败但被静默吞掉 | 阶段行没有 `COMPLIANCE_SKIPPED` / `extract_error` 之类的交代 | 停手：失败必须有交代，不能只是"少了材料" |
| ⑥ | 库写到预期外位置 | `%TEMP%\gps-p51-r2\persona.db` 不存在却在别处出现新库 | 停手查 `--out` |
| ⑦ | 退出码非 0 且原因不是预算 | 退出码 + 错误文案 | 停手保留输出 |

### 10.6 跑完要验的（比第一次多两条）

1. **五阶段全 EXECUTED**（第一次是 4/5）—— 最关键的一条
2. **抓取真的发生**：阶段行出现"带回 N 份正文"，且库里**有** `operation=EXTRACT` 的用量记录
3. 人物档案非空（traits / habits / expression_style 各几条）
4. 关系档案非空（shared_experiences 几条）
5. 四条只读命令可用（show / show-relationship / evidence / export）
6. 格式与方案对齐（"未并入"说明、来源数、证据树）
7. **（新）正文证据可回溯**：`evidence` 里至少有一条 `定位` 指向正文而非 `snippet`
8. **（新）用量归属**：`external_usage_records.task_id` 是否仍为 `null`（见 §11 D-C）
9. 实际费用 vs §10.3 预估的差异

### 10.7 第二次真跑实测（2026-10-19 22:06，任务 `tsk_34586cbf…`）

命令按 §10.1 原样执行（`.env` 的 `MAX_EXTRACTS` 临时 1→3，**跑完已恢复 1**）。
退出码 **0**，任务 `COMPLETED`，**5/5 阶段全 EXECUTED**。

#### 10.7.1 真实账单（`external_usage_records`，6 笔）

| # | provider | operation | unit / success | 预估 | **实际** | 状态 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | tavily | SEARCH | 1 / — | 1.0 | **1.0 credit** | CONFIRMED |
| 2 | tavily | SEARCH | 1 / — | 1.0 | **1.0 credit** | CONFIRMED |
| 3 | tavily | **EXTRACT** | **3 / 3** | 0.6 | **1.0 credit** | CONFIRMED |
| 4 | tavily | **EXTRACT**（补救） | **1 / 1** | 0.2 | **0.0 credit** | CONFIRMED |
| 5 | deepseek | CHAT（抽取） | 1 / 1 | 0.0033 | **0.0076233 USD** | OFFICIAL_RATE |
| 6 | deepseek | CHAT（对话） | 1 / 1 | 0.0033 | **0.0001731 USD** | OFFICIAL_RATE |

- 检索 **2 次 / 2.0 credits**；抓取 **2 笔请求、4 篇、成功 4、API 失败 0 / 1.0 credit**。
- 模型 **2 次调用 / 0.0077964 USD**，占硬上限 0.05 的 **15.6%**。
- **无清单外调用**：清单说"检索 2 / 抓取 ≤3+1 / 抽取 1 / 对话 1"，实际完全落在里面。
- 停止条件 ①~⑦ **一条都没触发**。

#### 10.7.2 抓取这一段的完整账（验收 4）

| 环节 | 数 |
| --- | --- |
| 检索登记来源 | 10 |
| 抓取 API 请求 | 2（批量：3 篇 + 补救 1 篇） |
| 提交 / 成功返回 | **4 / 4**（API 层面失败 0） |
| 质量门禁 | **拒 1 篇**（`cctv.com` 视频页：`NAV_DOMINATED`、`TARGET_NOT_MENTIONED`） |
| 进入材料流 | 10 份 = **正文过门禁 2 份** + 摘要 8 份 |
| 被正文取代的摘要 | 2 份（`MATERIAL_SUPERSEDED`：同一来源不重复交给抽取器） |
| credits | **1.0**（3 篇那笔 1.0，补救 1 篇那笔 0.0——不足一个计费单位） |

#### 10.7.3 9 条验收逐条结果

| # | 验收项 | 结果 | 实测 |
| --- | --- | --- | --- |
| 1 | 五阶段全 EXECUTED | ✅ | 5/5，`DIALOGUE_RUN EXECUTED 生成 1 轮模拟对话`，退出码 0 |
| 2 | 抓取真的发生 | ✅ | 阶段行"带回 10 份检索摘要、**3 份抓取正文**…正文过门禁 2 份"；库里 **2 笔 `operation=EXTRACT`** |
| 3 | 人物档案非空 | ✅ | 稳定性格 **1** / 习惯 0 / 说话方式 **1** / 单次 6 / 背景 0 / 经历 4；13 条证据 / 5 个来源 |
| 4 | 关系档案非空 | ✅ | PAIR v1 OK；双向视角各 5、**共同经历 5**、情感表达习惯 3、不对称说明 1 |
| 5 | 只读命令可用 | ✅ | `show` / `show-relationship` / `evidence` / `export` 退出码全 0（`status` 也 0，显示 5/5） |
| 6 | 格式与方案对齐 | ✅ | 空分区打 `（无）`+原因；末尾"另有 **7** 条未并入（CANON_FACT 2 / RELATION 5）"与 `evidence` 一致；证据树 `├/└`；来源数去重；导出 md 无 `evd_` ID、无 confidence |
| 7 | **正文证据可回溯** | ❌ | **24 条证据 `locator.field` 全是 `"snippet"`**，无一条指向正文 → 新缺陷 **D-D**，见 §11.2 |
| 8 | `task_id` 归属 | ✅ 确认仍为 null | 6 笔 `external_usage_records.task_id` **全为 `null`**（D-C 依旧，但 `usage_records.scope` 里有任务 ID） |
| 9 | 费用 vs 预估 | ✅ 低于预估 | 预估 0.017（上界 0.025）→ **实际 0.0077964 USD**（-54%）；清单口径 0.0066 → 实际 **+18.1%** |

#### 10.7.4 费用与 token 口径（用户要求的第 6 点）

| | 清单预估（EST 3000/2000） | **实测** | 倍数 |
| --- | --- | --- | --- |
| 抽取 in | 3000 | **10515** | 3.51× |
| 抽取 out | 2000 | **3724** | 1.86× |
| 抽取单次费用 | 0.0033 | **0.0076233** | 2.31× |
| 对话单次费用 | 0.0033 | **0.0001731** | 0.05× |
| **两次合计** | 0.0066 | **0.0077964** | **1.18×** |

- 单价验算：`10515/1e6×0.3 + 3724/1e6×1.2 = 0.0076233`，与账单 `cost_confirmed`
  **完全一致** → 偏差仍然**只来自 token 估算，不来自单价**（与第一次同一结论）。
- 为什么合计只差 18%，而单次差 2.31 倍：对话那次极便宜（0.00017）把总数拉下来了。
  抽取用大常量、对话用小常量，两个方向的误差互相抵消——**这不是"估得准"，是运气**。
- **关于用户提的 `EST_IN=10500 / EST_OUT=4700`**：
  - `in`：建议 10500 vs 本次实测 **10515**（误差 **+0.14%**）→ 吻合得几乎完美；
  - `out`：建议 4700 vs 第一次实测 **4711**（**-0.23%**）、本次 **3724**（4700 高估 26%）。
    两次实测 3724 / 4711，均值 4217 → 4700 是**偏保守的上界**，方向正确（宁高勿低）。
  - 若采用：单次抽取预估变 0.00879 USD，偏差从 **-57%** 收窄到 **+15%**。
  - 但**对话调用会被高估约 50 倍**（实测 0.00017 vs 预估 0.00879）。真要调，
    应当给"抽取"和"对话"**两套常量**，而不是共用 `PERSONA_MODEL_EST_TOKENS_*`。
    **本次未改**（用户明确要求跑完对比后再定）。

#### 10.7.5 意外收获：黛玉 `traits` 从 0 → 1（用户重点看的第 7 点）

第一次真跑：稳定性格 **0 条**。本次：**1 条**，且是跨来源归并的结果——

```
稳定性格（1）
  · 林黛玉少聪慧，过目成诵，尤工诗词。
    2 个来源支持 · 归并自 2 条措辞各异的说法
      ├ evd_fba8…  VERBATIM  《林黛玉 - 維基大典》
      └ evd_8c5f…  VERBATIM  《林黛玉 - 维基百科》
```

原因链：D-B 修好 → 抓到正文 → 同一来源的正文**取代**摘要（`MATERIAL_SUPERSEDED`）
→ 两份不同措辞的独立来源都进了材料流 → 满足"≥2 个独立来源"的稳定性闸门 → 晋升 traits。
**这正是 AC-7（单次行为不进 traits）该有的样子**：不是放宽闸门，是材料真的够了。

---

## 11. 装配层"默认值导致产品路径失效"审计（D-A / D-B 之后的全面排查）

形状都一样：**能力只存在于"测试显式注入"这条路径上，产品路径从不注入 →
默认值为 `None` / `FakeProvider` → 单元测试全绿、真跑才炸**。

| # | 装配项 | 默认值 | 产品路径（`persona run`）是否真的接上 | 结论 |
| --- | --- | --- | --- | --- |
| 1 | `search_provider` | `FakeProvider` | ✅ `build_search_provider=True` + `allow_real=True`，且未授权时有 `REAL_PROVIDER_BLOCKED` 告警 | 接上了（有告警兜底） |
| 2 | `chat_provider` → 抽取器 | `None` | ✅ `build_chat_provider=True`；缺密钥时 `EXTRACTOR_ABSENT` 告警 + CLI 直接拒绝 | 接上了 |
| 3 | `dialogue_provider` | `FakeProvider` | ❌→✅ **已修（D-A）**：现在 `dialogue_provider or chat_provider`，回落时新增 `DIALOGUE_PROVIDER_OFFLINE` 告警 | **本次修复** |
| 4 | `extraction_strategy` | `None` | ❌→✅ **已修（D-B）**：授权真实检索时默认装 `TopNExtractionStrategy()`；未启用时新增 `EXTRACTION_STRATEGY_DISABLED` 告警 + 清单如实打 0 | **本次修复** |
| 5 | `relationship_analyzer` | 按配置 | ✅ `settings.relationship.analyzer_enabled()`；第一次真跑阶段行显示"语义分析=True" | 接上了 |
| 6 | `research_limits` | `settings.research` | ✅ P4.8/R3 已修（此前回落到模块默认值，配置被静默忽略） | 接上了 |
| 7 | `trait_vocabulary` | `None` = L1 | ✅ CLI 显式 `resolve_vocabulary(l1/l2)`，未知词表不静默回退 | 接上了 |
| 8 | `extract_temperature` | `None` = 0.7 | ✅ 产品默认 0.7，仅对照实验用 | 接上了 |
| 9 | `search_metadata` | `{}` | ✅ 只做透传；第一次真跑 2 次检索均成功 | 接上了 |
| 10 | `quality_min_effective_chars` | `None` = 模块默认 | ✅ 默认值即生产值 | 接上了 |
| 11 | `initialize_schema` / `hydrate_ledger` | `True` | ✅ `--dry` 显式关掉（P5.1） | 接上了 |
| 12 | `MemoryService` | 装配即注入 | ✅ `DialogueDeps.memory` / `memory_reader` 都接的是同一个实例 | 接上了 |
| 13 | `claim_extractor` | 由 `chat_provider` 派生 | ✅ 与对话**共用同一个 Provider 实例**（已加断言锁住） | 接上了 |
| 14 | consent（授权确认） | CLI 独立模块 | ✅ 按数据目录隔离、只存 3 键、原子写、坏文件不抛（23 项测试） | 接上了 |
| 15 | **`task_id`** | **`None`** | ⚠️ **未接上（新发现 D-C）**：任务 ID 是 `BusinessOrchestrator.run` **内部**生成的，装配时还不存在，于是抽取/对话的用量记录 `task_id` 全是 `null` | **待修，见 §11.1** |
| 16 | **`locator.field`** | **硬编码 `"snippet"`** | ❌→✅ **已修（D-D）**：材料是摘要还是正文，`MaterialText.quality_status` 早就知道，但写 locator 时没读 → 24 条证据的定位一律叫 `snippet`。现在按门禁结论写 `content` / `snippet` | **本次修复，见 §11.2** |

### 11.1 D-C：`task_id` 进不了账本（新发现，**未修**）

- 现象：第一次真跑的 `external_usage_records` 三条记录 `task_id` **全是 `null`**，
  而任务 `tsk_42dc…` 确实存在。
- 根因：`_cmd_run` 建栈时任务还没建（`BusinessRequest` 没有 `task_id` 字段，
  任务由 `business.py:565` 在 `run()` 里创建），`StackOptions.task_id` 只能是 `None`。
- 影响：`occupied_cost_for_task(None)` 会**退化成全库累计**
  （`persona/core/usage_ledger.py:569`）——保守、不会超支，但：
  1. 清单里"单任务上限（按 task_id 累计）"这句话名不副实；
  2. 事后无法回答"这个任务花了多少"，跨任务账目混在一起。
- 修法（两种，都要动 `BusinessRequest`，故**不在本次授权内**）：
  ① `BusinessRequest` 加可选 `task_id`，由 CLI 先建任务再传入、编排器复用；
  ② 或编排器把建出来的 `task_id` 回调给栈（需要改装配契约）。
- 现状可接受：硬上限退化为"全库累计"是**更严**的口径，不会多花钱。
- **第二次真跑复核（§10.7 验收 8）**：6 笔记录 `task_id` **仍全为 `null`**，D-C 确认依旧存在。
  同时确认一个此前不知道的事实：`usage_records.scope` **是有任务 ID 的**
  （`tsk_34586cbf…`），所以"事后查不到单任务花费"的说法要修正——
  `usage_records` 能查（按 scope），只是 `external_usage_records`（账单表）查不到。
  也就是说 D-C 的真实影响只有两条：账单表无法按任务对账；清单里"按 task_id 累计"名不副实。

### 11.2 D-D：`locator.field` 恒为 `"snippet"`，正文证据不可回溯（**已修**）

- **现象**：第二次真跑抓回了 3 份正文、2 份过门禁进材料流，但库里 **24 条证据的
  `locator` 全是 `{"field": "snippet", ...}`**，一条都不指向正文。
  `persona evidence 林黛玉` 因此把"正文第 1816 字符"显示成 `定位 snippet[1816:1848]`
  ——摘要不可能有 1816 字符，这个标签是**错的**（验收 7 不过的直接原因）。
- **根因**：`persona/modules/claim_validation.py:732`

  ```python
  locator = {"field": "snippet", "start": span[0], "end": span[1]}
  ```

  `field` 被写死。而材料到底是不是摘要，**同一个函数早就算出来了**：
  `MaterialText.quality_status`（`claim_validation.py:393` 从 `material.quality_status`
  填进来，取值 `QUALITY_VALID` / `QUALITY_SUMMARY_ONLY` 等）。知识有了，写的时候没用。
- **与 D-A / D-B 同形**：能力存在（字段已算好），产品路径不接（写死常量），
  于是"看起来没问题"，真跑才暴露。这次是第三次。
- **影响**：
  1. 证据无法自证来源类型——`SUMMARY_ONLY`（未过正文门禁、tier 更低）的摘要证据
     与实打实的正文证据在输出里**长得一模一样**，用户无法据此判断可信度；
  2. 质量门禁拒掉的那 1 篇，与通过的 2 篇，在证据层面无从分辨；
  3. 直接导致 §10.7 验收 7 不过。
- **最小修法**（一行，且不需要真跑验证）：

  ```python
  field = "snippet" if material.quality_status == QUALITY_SUMMARY_ONLY else "content"
  locator = {"field": field, "start": span[0], "end": span[1]}
  ```

  `material` 在 line 719 可能被 D-291 重绑定到别处的 `MaterialText`，
  两个分支都带 `quality_status`，所以直接用即可。渲染层 `render.py` 已把
  `locator` 原样打印为 `定位 <field>[start:end]`，改完立刻可见。
- **验证方式**：不需要第三次真跑——用单元测试构造 `QUALITY_VALID` 与
  `QUALITY_SUMMARY_ONLY` 两份材料，断言产出的 `locator.field` 不同即可。
  D-B"真的抓了正文"这件事已由验收 2（EXTRACT 用量记录 + 材料取代 + 门禁拒绝）
  **独立证明**，修 D-D 只是让它在输出里**可见**。

#### 11.2.1 实际改动与验证结果

**改动**（`persona/modules/claim_validation.py:732` 一处，加一个导入）：

```python
# 注意一个反直觉的地方：**不能靠"字段名是不是 snippet"来判断**。
# `materials_from_extracts()` 只交 excerpt 不交全文，正文摘录同样装在
# `snippet` 字段里（P4.4-A）。所以唯一可靠的判据是门禁结论。
if material.quality_status == QUALITY_VALID:
    field_name = "content"          # 抓回来的正文（过了质量门禁）
elif material.quality_status == QUALITY_SUMMARY_ONLY:
    field_name = "snippet"          # 检索摘要（明确声明，不是"没判过"）
else:
    # 门禁结论缺失（""）或既非 VALID 也非 SUMMARY_ONLY（如 LOW）。
    # 不猜成正文——往低可信的那一类记，宁可低估也不虚增。
    field_name = "snippet"
locator = {"field": field_name, "start": span[0], "end": span[1]}
```

**连带改动两处断言**（不是迁就实现，是把"写死 snippet"这个过时的验收标准
换成"照材料性质写"，验证反而**更强**了——现在能抓住 D-D 本身）：

- `scripts/verify_p4_5a_outputs.py`（独立复算脚本）A4 第 ③ 项：按材料自己声明的
  `quality_status` 反推应有的 `field`，不再写死 `snippet`。
- `tests/test_p4_5a_e2e.py:286`：同上。这批材料出自 `materials_from_extracts()`
  （`quality_status=VALID`，是正文），所以现在断言 `content`。

**新增 `tests/test_p5_1_locator_field.py`（13 项，全离线）**：

| 覆盖 | 断言 |
| --- | --- |
| 两条主干 | `VALID` → `content`；`SUMMARY_ONLY` → `snippet` |
| 同批共存 | 一次抽取里两种材料并存时，产出的定位必须能区分开 |
| 保守兜底 | 门禁结论缺失（`""`）不晋升为 `content` |
| 语义不漂 | `start/end` 切片仍**逐字等于**摘录；两种材料偏移不同（可佐证归属） |
| **D-291 重定位** | 模型报错材料、引文被改判到另一份材料时，`field` **必须跟着新归属走**（最容易漏的一条） |
| 认知钉子 | 正文摘录也装在 `snippet` 字段里 → 只有 `quality_status` 能分 |

**回归**：`1712 → 1725`，80s，全绿。改动前先跑过一次，暴露出 4 项断言过时
（都在 P4.5a 的 e2e 与独立复算脚本里），已按上述原则更新，不是放宽。

**未跑第三次真跑**：D-B 已被验收 2 独立证明，D-D 只影响输出可见性，单测足够。
