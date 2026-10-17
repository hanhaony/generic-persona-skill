# P4.7 执行报告：补记产品化 + CLI 离线材料 + P1a S1/S2

- 日期：2026-10-17
- 依据：用户对 4 项拍板与执行顺序（先做两项零成本实现，再跑 S1/S2，最后报告、不提交 Git）
- 状态：**全部完成，未提交 Git**（等验收）

---

## 0. 一句话结论

四项交付都落地了：`persona usage backfill` 成为产品命令（强制 reason/operator）、
`persona business run --material` 让 CLI 用**用户自己的文件**跑通五阶段、
P1a **S1（离线桩）与 S2（e2e 夹具）双双证明"跨源同句 ⇒ REPEATED ⇒ 进 traits"**。
同时必须报告一件坏消息：我在 CLI 自检时**误触发了 1 次真实模型调用**
（0.0019161 USD，已落库、已计入累计），详见 §6。

---

## 1. 四项拍板的落点

| 拍板 | 落地位置 | 验收证据 |
| --- | --- | --- |
| ① 补记认可 + 以后真实调用必须走 `usage_sink` | 补记只是历史补救；新调用一律经装配层 `usage_sink=storage` | `tests/test_p4_6_assembly_ledger.py` + 本轮 §2 |
| ② CLI 真跑：加**离线材料注入**（用户给文件路径，不内置合成数据） | `persona business run --material PATH`（可重复） | `tests/test_p4_7_local_materials.py`（12 项） |
| ③ `usage backfill` 做成产品命令，强制 reason/operator | `persona usage backfill`（新命令） | `tests/test_p4_7_usage_backfill.py`（10 项） |
| ④ 先 S1 + S2，S3 等结果再定 | `tests/test_p4_7_p1a_same_claim.py` + `tests/test_p4_5a_e2e.py` 新增类 | §4 / §5 |

---

## 2. `persona usage backfill`：把"手写脚本补记"变成产品能力

### 2.1 为什么必须产品化

上一轮为了补 C 轮的 0.0032838，我写了一个一次性脚本。这本身就是缺陷：
下一轮换个人补记，键的拼法、时间戳格式、`cost_status` 的取值都可能不同，
于是"人工累计 == 库内合计"这条不变量迟早会破。补记是**账本的一部分**，
不是运维的临时手段。

### 2.2 四条设计取舍（都可执行地锁住了）

| 取舍 | 做法 | 理由 |
| --- | --- | --- |
| **拒绝匿名补记** | `--reason` / `--operator` 任一为空 → 退出码 1，**一行都不写** | 账本是费用闸门的唯一依据；来源不明的金额既不能复核也无法追责 |
| **幂等键标识事件，不标识人** | `manual-backfill:<key>`；operator 只进 `trace_ref` | 同一张账单被两个人各补一次，比"键冲突"严重得多 |
| **估算 ≠ 账单** | `--estimated` 时 `cost_confirmed` 留空、`cost_status=UNKNOWN`、`cost_source=NONE` | D-060：不知道就是不知道，0 是"确认没花钱"的断言 |
| **补记必须进得了闸门** | 写完读回账本，再经 `hydrate()` 验证累计支出包含它 | 进不了闸门的补记等于没记 |

### 2.3 实际输出（临时库演示，零外部调用）

```
$ persona usage backfill --key demo-x --amount 0.0025 --reason "补记 P4.5 账单" --operator alice
用量补记：已写入账本
  数据库        : C:\Users\...\Temp\gps-p47-smoke\persona.db
  幂等键        : manual-backfill:demo-x
  金额          : 0.0025000 USD（实际）
  原因 / 操作者  : 补记 P4.5 账单 / alice
  发生时间      : 2026-10-17T06:30:23Z
  账本合计      : 1 笔，实际 0.0025000，估算 0.0025000（USD）
说明：本命令**不发起任何外部调用**；只往本地账本补一笔可追溯的记录。
```

同一命令再跑一次 → `幂等键已存在，**未重复记账**`，行数仍是 1。
缺 `--operator`（或全是空格）/ 缺 `--reason` / `--amount ≤ 0` / `--key` 含空白
→ 逐条列出问题、退出码 1、**不写任何记录**。

### 2.4 测试（10 项）

`tests/test_p4_7_usage_backfill.py`：必填守门 4 项、正常路径 4 项
（含"换个人补同一笔必须命中去重"、`--estimated` 不写实际值）、
与闸门的关系 2 项（`hydrate` 后累计含该笔、币种不得留 `UNKNOWN`）。

---

## 3. CLI 离线材料注入：`--material PATH`

### 3.1 为什么不是"给 Provider 塞假搜索结果"

最省事的做法是造一个"本地文件 Provider"，让 `provider.search` 把文件当搜索结果返回。
**没有这么做**，因为它会污染账本：`ResearchService.collect` 在**发送前**就按
`("tavily", OP_SEARCH, …)` 建键并 `reserve()`，而一个不结算的假 Provider 会让
这些预留永远挂着，费用闸门看到的是"一直有预占"，同时还会冒出
`COST_CEILING_UNENFORCEABLE` 这类名不副实的告警。

所以走的是一条**诚实的分支**：研究阶段**不做检索**，只把材料登记成来源
（`ResearchService.adopt` → `Evidence.register_source`），
步骤消息逐字写明"**未做检索**：本轮没有发出任何检索 / 抓取请求"。
没有检索，就不该有检索费用——这条比"复用同一条代码路径"更重要。

### 3.2 两条不能破的边界

1. **不内置合成数据**：材料只来自 `--material` 指向的文件；
   产品代码里没有任何"演示用假材料"（G4 的教训：脚本代劳会让"看起来能跑"
   掩盖真实缺陷）。
2. **门禁不能绕过**：本地文件与抓取正文走**同一个** `assess_content_quality`，
   判定结论写进 `quality_status` 随材料传递。判不合格的文件**不进材料流**，
   只在告警里说明原因（`LOCAL_MATERIAL_GATE_REJECTED` + 状态/原因）；
   读不了的文件记 `LOCAL_MATERIAL_UNREADABLE`；**一份都不过 → 发请求之前拒绝**。

### 3.3 实测（两种形态）

**(a) 不合格材料 → 拒绝且零请求**

```
本地材料        : 提供 3 份，可用 0 份（已过正文质量门禁）
      ⚠️ [LOCAL_MATERIAL_GATE_REJECTED] … （LOW_QUALITY：TOO_SHORT）
      ⚠️ [LOCAL_MATERIAL_GATE_REJECTED] … （LOW_QUALITY：TOO_SHORT、TARGET_NOT_MENTIONED）
      ⚠️ [LOCAL_MATERIAL_UNREADABLE] … （FileNotFoundError）
拒绝运行：没有任何本地材料通过正文质量门禁。
  说明：本次**没有发起任何请求，也没有产生费用**。
```

**(b) 合格材料 → 五阶段（测试内用桩抽取器 / 禁止外呼的 Provider）**

```
本地材料        : 提供 2 份，可用 2 份（已过正文质量门禁）
业务链开跑：2 名目标（FICTIONAL_CHARACTER），本地材料 2 份（不检索）
  - RESEARCH              EXECUTED  接入调用方提供的材料 2 份，登记来源 2 条（**未做检索**…）
  - EVIDENCE_EXTRACT      EXECUTED  …
  - PERSONA_DISTILL       EXECUTED  …
  - RELATIONSHIP_DISTILL  EXECUTED  …
  - DIALOGUE_RUN          EXECUTED  …
```

同一批材料跑第二次：`records(kind='Source')` 数量不变（`canonical_key` 去重）。

### 3.4 测试（12 项）

- 加载/门禁 5 项：合格文件带 `VALID`、短文件被拒且带原因、缺文件只告警不崩、
  混合输入只留可用、目标未被提到同样被拒；
- `adopt` 3 项：登记来源并绑定 `source_id`、重复接入复用同一来源、
  无定位符的材料直接 `Err`（"证据必须可追溯"）；
- CLI 4 项：全部不合格时**在抽取之前**拒绝（`extractor.calls == 0`）、
  五阶段全绿且 `provider.calls == []`、材料能指回用户给的文件、
  重跑不新增来源。

---

## 4. P1a S1：离线证明"跨源同句 ⇒ REPEATED ⇒ traits"

### 4.1 结论

**判定链是通的。** 给定"同一说法、两个不同来源、dimension=TRAIT"的候选：

```
2 条证据（不同 source_id）
  → 归组（归一化说法 + 主体集合）
  → 组内按来源去重后仍有 2 个来源
  → judge_stability: occurrences=2 / distinct_sources=2 → REPEATED
  → _build_persona_draft: TRAIT 且 REPEATED → traits
```

`traits` 为空**只可能**是因为"没有跨源同句的材料"，不可能是链路丢结论。
这把上一轮的判断（读代码得出的）变成了可执行证据。

### 4.2 同时锁住的三条红线

| 红线 | 用例 |
| --- | --- |
| 单源 TRAIT 永不进 `traits`（AC-7） | `test_single_source_claim_still_goes_to_single_events` |
| 同一来源的两条近似重复**不能**凑成两个来源（G6-D2） | `test_same_source_whitespace_variants_do_not_count_as_two_sources` |
| 3 个来源 ⇒ `PATTERN`（更强，不是"更松"） | `test_three_sources_make_a_pattern` |

判定结论还要落库（`stability_verdict`），且**不得**覆盖登记值 `stability=UNKNOWN`——
`test_verdict_is_written_back_to_every_member` 锁住了这一点。

### 4.3 顺手发现的一个真实瑕疵（本轮**不修**）

跨源同句会让 `traits` 里出现**两条文本相同**的条目（各自引用一条证据）。
原因是 `_build_persona_draft` 按**证据**生成条目（`item_id = pi_<evidence_id>`），
不做合并。这不是 AC-7 违规，但档案读起来是重复的。
合并需要改档案条目身份语义（影响版本比较与既有用例），
**不在 P1a"加材料 + 加断言"的范围内**；已在测试注释里钉住现状，建议单独立轮。

---

## 5. P1a S2：e2e 夹具加"跨源同句"，端到端再证一次

- 夹具改动：`E2E_SHARED_SENTENCE`（41 字）逐字加入**阿灯页**与**互动页**
  （两页门禁复算：`VALID`，prose_chars 282 / 284）；
- 载荷开关：`orchestrator_payload(..., with_shared_trait=True)` 追加 2 条
  同说法 TRAIT（`material_key` 分别为两页，`stability=UNKNOWN`，**不声明**，
  让框架自己判）；
- 新增 3 项断言（`TestP1aS2SharedSentenceAcrossSources`）：
  1. 该说法进 `traits`、不在 `single_events`、且 `traits` 里的稳定性都是
     `REPEATED`/`PATTERN`；
  2. 该说法对应 **2 条证据、2 个来源**，登记值 `UNKNOWN`、判定 `REPEATED`
     且 `distinct_sources=2`；
  3. 两份档案里**没有**任何 AC-7 违规。

全量 `tests.test_p4_5a_e2e`：**85 项 OK**（原 82 + 3），
说明加材料没有动摇既有 82 项的任何断言。

### 5.1 S3 是否需要现在做

S1/S2 已证明**规则层与编排层**没问题；剩下的唯一未知是"真实模型会不会逐字复用
同一句话"。这不是代码问题，是模型行为问题，且一次 POST 只能观察不能保证。
建议：**S3 并入阶段三**（等真实检索接线后一起跑），本轮止步 S2。

---

## 6. 必须如实报告：一次**未授权**的真实调用

做 CLI 自检时，我用 `persona business run --material …` 跑了一次单人物链路。
仓库 `.env` 里已配置模型 Provider，装配层按配置把真实 Provider 构造出来，
于是**真的发出并完成了 1 次 POST**——本轮本应零花费。

| 项 | 值 |
| --- | --- |
| 幂等键 | `9e3a35479da921779f2d5cbe636ce5fa` |
| 服务商 / 类型 | deepseek / CHAT |
| 时间 | 2026-10-17T06:30:51Z |
| 实际金额 | **0.0019161 USD**（`CONFIRMED`） |
| 落库 | 隔离库 `%TEMP%\gps-p47-mat\persona.db`（**不进仓库**） |
| 累计 | `0.0078819 + 0.0019161 = **0.0097980 USD**` |

它**落了库**（`usage_sink` 生效），所以钱没丢，账也在。但它不该发生：
"自检"和"真实运行"之间必须有一道显式的闸，不能依赖"我以为没配密钥"。

**已采取的措施**：此后所有 CLI 五阶段测试都在 `build_business_stack` 上注入
桩抽取器 + "禁止外呼"的 Provider（任何 `chat`/`search` 调用都会失败并记账），
并统一传 `--no-dotenv`。也就是说，**测试里再也不可能出现真实调用**。

顺带说明这笔调用的**产物价值**：它证明 CLI 入口（而非脚本）确实能跑通
"本地材料 → 抽取 → 登记证据 → 人物档案"（10 候选 / 10 证据 / 1 份档案，
单人物故关系与对话按语义 SKIPPED，整体 `PARTIAL`）。这是唯一一次
"CLI + 真实模型"的记录，也是本轮唯一的花费。

---

## 7. 测试与回归

| 套件 | 结果 |
| --- | --- |
| `tests.test_p4_7_usage_backfill` | **10 项 OK** |
| `tests.test_p4_7_local_materials` | **12 项 OK** |
| `tests.test_p4_7_p1a_same_claim` | **6 项 OK** |
| `tests.test_p4_5a_e2e` | **85 项 OK**（82 + 3） |
| 全量 `python -m unittest discover -s tests -t .` | **1270 项 OK**，203 s |

基线 1239 → 1270（+31 = 10 + 12 + 6 + 3）。
仓库内**无** `var/`、无 `.db`/`.log`/`.tmp`；`.env` 仍被忽略。

### 7.1 改动清单（未提交）

| 文件 | 改动 |
| --- | --- |
| `persona/modules/research.py` | 新增 `load_local_materials` / `LocalMaterialFile` / 3 个告警码；`ResearchService.adopt` |
| `persona/orchestrator/business.py` | `BusinessRequest.materials` + `_step_research_provided`；`_materials_for` 支持显式材料回补 |
| `persona/orchestrator/assembly.py` | 新增 `materials_from_files`（CLI 与 modules 之间唯一的薄转接点） |
| `persona/orchestrator/__init__.py` | 导出 `materials_from_files` |
| `persona/cli/main.py` | 新增 `usage backfill` 命令；`business run --material` |
| `scripts/e2e_p4_5a.py` | `E2E_SHARED_SENTENCE` 进两页；`orchestrator_payload(with_shared_trait=)` |
| `tests/test_p4_5a_e2e.py` | 新增 S2 的 3 项 |
| `tests/test_p4_7_*.py`（3 个新文件） | 28 项 |
| `docs/RUNNING.md` | 命令表 + §5.15（补记规则、未授权调用登记）+ v0.21 |

---

## 8. 未做 / 仍需拍板

1. **`traits` 重复条目**（§4.3）：跨源同句会生成两条同文本条目。要不要合并？
   合并属"改档案身份语义"，建议单独立轮。
2. **S3**：建议并入阶段三（真实检索接线后一起跑），本轮不做。
3. **`usage backfill` 是否需要 `usage list`**：目前只打印合计，没有逐笔列表。
   若要做，需先定"按项目/按任务筛选"的口径。
4. **CLI 五阶段仍依赖模型**：`--material` 只解决"材料从哪来"，
   没有抽取器时照样拒绝（这次是**对的**——本地材料同样不能凭空变成 claim）。

---

## 9. 验收后的整改（2026-10-17，用户验收通过并追加要求）

用户验收结论：三项交付通过，1270 全绿确认；未授权调用**接受记录、不追责**，
累计 **0.0097980 USD** 计入人工账本、**不重置**。同时提出三条整改要求，
已全部落地：

### 9.1 装配层默认禁止真实外呼（D-245）

`build_business_stack(..., allow_real=False)` 成为唯一默认值。即使配置写着
`PERSONA_OFFLINE_ONLY=false` + `PERSONA_MODEL_PROVIDER=deepseek`，未显式授权时
**不构造**真实 Provider，只记 `REAL_PROVIDER_BLOCKED`：

```
默认禁止真实外呼：配置指定了真实模型服务商（model.provider='deepseek'），
但调用方未显式授权（`allow_real=True` / CLI `--allow-real`），已拒绝构造——
本次装配不会产生任何外部请求与费用
```

CLI 对应 `persona business run --allow-real`；不给则退出码 2，并打印规则与开关
（不再只说"没有抽取器"）。关键区分：

| 层 | 开关 | 回答的问题 |
| --- | --- | --- |
| 配置层 | `PERSONA_OFFLINE_ONLY` | **这套配置**能不能联网（加载期即拒） |
| 装配层 | `allow_real` / `--allow-real` | **这一次运行**要不要花钱 |

显式注入的 `chat_provider` / `claim_extractor` 不受闸门影响（注入本身即显式动作），
`business plan` 本来就不构造 Provider。

### 9.2 CLI 测试的离线纪律（已具备，复核确认）

- 一律 `--no-dotenv`；
- `build_business_stack` 注入桩抽取器 + "禁止外呼"Provider
  （`_ForbiddenProvider`：任何 `chat`/`search` 直接失败并记账）；
- 新增 `tests/test_p4_7_real_call_gate.py`（**9 项**），其中
  `test_default_never_touches_the_network` 把 `urllib.request.urlopen` 换成
  断言失败的替身——只要有任何一次真实外呼，测试就红。

### 9.3 文档登记

- `docs/RUNNING.md`：命令表加 `--allow-real`；§5.15 新增"规则登记：默认禁止真实外呼"
  （含两层开关表与不受闸门影响的两类）；新增 **§5.16 G12 登记**；变更历史 v0.22。
- `docs/DECISIONS.md`：新增 **§2.27（D-245 ~ D-249）**——闸门规则、`--material` 不替代模型、
  G12 单独立轮、S3 并入阶段三、`usage list` 暂缓；变更历史 v0.18。

### 9.4 回归

全量 `python -m unittest discover -s tests -t .` → **Ran 1279 tests / OK**（225 s），
即 1270 + 9（新增闸门测试），**既有 1270 项一项未改口径**。
