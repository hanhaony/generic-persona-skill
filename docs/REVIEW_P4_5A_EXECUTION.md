# P4.5-A 执行轮报告（步骤 1–6）

> ⚠️ **后续更新**：本报告 §6 记录的 G4（编排器丢抓取正文）与 G5（抽取器 warnings
> 被丢弃）**已修复**，详见 [`FIX_P4_5A_G4_G5.md`](./FIX_P4_5A_G4_G5.md)。
> 本文件保留为那一轮的**历史记录**，其中"未做事项/待授权"一节的状态以新报告为准。

> 状态：**步骤 1–5 已完成，步骤 6 为本报告，等待人工验收。**
> 真实模型调用：**未发生**。真实检索调用：**未发生**。费用：**0 USD**。
> 本文所有数字均为本机实测输出，非推断。

---

## 0. 纪律声明（先看这条）

| 约束 | 执行情况 |
|---|---|
| 不调真实模型 / 真实搜索 | ✅ 全部走桩 Provider；`--dry` 只做预检与编排器诊断 |
| 不输出密钥 | ✅ 仅打印「已设置 / 长度」，并做请求体、`repr`、产物三重自检 |
| 不重置账本 | ✅ 仅用**新建内存账本**；未触碰任何账本文件 |
| 不改产品代码 | ✅ `persona/` 下零改动；新增只在 `scripts/` 与 `tests/` |
| 不装依赖 | ✅ 未执行任何安装 |
| 不自动提交 Git | ✅ **第二轮未提交**，三个新文件仍为 untracked |
| 费用上限 0.25 USD | ✅ 配置生效值 0.25，`max_calls=1`，`max_retries=0` |

---

## 1. 交付物清单

### 1.1 第一轮 Git 入库（步骤 1）

提交 `c341d05`「P4.4~P4.5A 统一提交：真实 Claim Extractor 接入框架、DeepSeek Chat Provider、真实模型受控冒烟、端到端前置审查」。

提交前扫描结果（全部为 0/干净）：

- 密钥字面量 **0**；本地库（`*.db`/`*.sqlite`）**0**；临时文件（`*.tmp`/`*.log`/`__pycache__`）**0**
- 厂商 SDK（`openai`/`httpx`/`requests`/`anthropic`/`boto3`）**0**
- 真实人物名命中 **0**（AC-16，全小写匹配）
- 硬编码本机路径 **0**；大文件 **0**
- `.env` **未入库**（命中 `.gitignore:22`）；提交后工作区干净

目的达成：P4.4 全链成果 + P4.5-A 报告已进版本库，`git checkout .` 不再会丢成果。

### 1.2 三个新文件（步骤 2–3，尚未提交）

| 文件 | 行数 | 角色 |
|---|---|---|
| `scripts/e2e_p4_5a.py` | 1094 | 一次性端到端运行器。模式：`--dry` / `--a11-only` / `--run`；另有 `--path=script\|orchestrator`、`--dump PATH`（产物落盘供复算）。**不进 unittest 收集**（G1） |
| `scripts/verify_p4_5a_outputs.py` | 272 | **独立复算器**。刻意**不** import `persona.modules.*`，自己折叠空白、自己 `str.find` 定位摘录、自己按配置单价复算金额 —— 避免「用被测代码验证被测代码」 |
| `tests/test_p4_5a_e2e.py` | 509 | 全离线集成测试（桩 Provider），覆盖 A2–A8 + A10 + A11 + A12，并含 G4/G5 回归护栏 |

---

## 2. 离线测试（步骤 3 验收）

```
Ran 28 tests in 1.495s
OK
```

28 项全绿，按类分布：

- **`TestScriptSideChain`（18 项）** — A1 模型恰好调用 1 次、A1b 断网护栏真拦得住、A3 候选可追溯到本次运行、A4 摘录逐字命中且 `locator` 由框架算出、A4b 全部受理（无拒收/无 pending）、A5 费用可由 `response_usage` 跨组件复算、A6 账本语义、A7 档案引用可追溯、A8 单次事件绝不进 `traits`、A12 无「已验证」字段被置真；材料门禁判**摘录**而非全文；独立复算器对真实产物全绿 + **两项突变检验**（改坏摘录 / 改坏金额，复算器必须报错 —— 证明它不是恒真摆设）
- **`TestIdempotence`（A10）** — 重复结算/重复登记不产生第二条证据
- **`TestGateNegatives`（A11）** — 见 §4
- **`TestStageSemantics`（A2 三项）** — 研究阶段 EXECUTED；参与者 <2 人时关系/对话阶段 SKIPPED（**不伪造**）；人格阶段仍执行
- **`TestKnownGaps`（G4/G5 四项）** — 把两个缺口写成回归护栏，防止日后被静默"修好"或被忽略

> 注：`python -m unittest discover -s tests -t .` 全量发现会拉入触及网络的模块并在本机被信号终止，因此本节数据来自定向运行 `-m unittest tests.test_p4_5a_e2e`。

---

## 3. `--dry` 结果（步骤 4 验收）

命令：`python scripts/e2e_p4_5a.py --dry` → **预检全部通过，未发出任何真实请求。**

### 3.1 配置核验（[1]）

```
enabled=True  provider=deepseek  model_id=deepseek-flash  currency=USD  price_tier=peak
price_per_1m_in=0.3  price_per_1m_out=1.2   (→ per_1k: 0.0003 / 0.0012)
price_source=https://api-docs.deepseek.com/quick_start/pricing   price_checked_at=2026-10-16
max_cost_total=0.25  max_cost_per_task=0.25  max_cost_effective=0.25
max_calls=1  est_tokens_in=3000  est_tokens_out=2000  est_cost_per_call=0.0033
require_gate_verdict=True
```

11 项断言全 `[OK]`，含「**不换模型**」「Provider 重试次数强制 = 0」「研究抓取上限 `max_extracts` = 1」「模型密钥已设置（只检查存在性）」。

### 3.2 合成材料与门禁复算（[3]）

5 句合成正文（阿雾 / 雾港 / 星灯，全部虚构，AC-16 合规），逐句 ≥40 字：

1. 阿雾是雾港灯塔的常年值守者，据雾港地方志记载，她自少年时代起便接替家中长辈看守这座灯塔，一直未曾离开。
2. 阿雾偏爱在傍晚独自巡视堤坝，随身带着一本记录潮位与风向的旧册子，册页边角已被海风磨得发白卷曲。
3. 阿雾向来沉默寡言，很少与星灯会的同伴闲谈，只在每年灯会点亮的那一晚才会多说几句话，说完便又回到塔上。
4. 阿雾习惯在整点敲响灯塔的铜钟，这个习惯维持了整整十二年，雾港的老住户只要听见钟声就知道她还在值守。
5. **阿雾曾在一次夜雾中连续值守七十二小时**，直到潮水退去才离开塔身，这件事至今仍被雾港的老住户反复提起。 ← **AC-7 探针**（标 `TRAIT + SINGLE`，绝不能进 `traits`）

走**真实** `assess_content_quality` 复算：

```
status=VALID   reasons=（无）
prose_chars=246（下限 200）   longest_run=246（下限 200）
nav_line_ratio=0.0（上限 0.70）   target_mentioned=True   正文总字数=254
[OK] 合成正文通过真实正文质量门禁（status=VALID）
```

### 3.3 装配与请求预检（[4]）

- 临时库 `…/Temp/gps-p45a-*/p45a.db`（隔离，退出即删）；账本为**新建内存账本**
- 数据源 `StubSearchExtractProvider`（`search` + `extract` 均离线）
- 目标实体 `pe_3e170baada0a45e38e0f4b413b2dd20a`（`FICTIONAL_CHARACTER`）
- 研究产出：来源 1 / 抓取 1 / 材料（检索摘要）1 / 门禁拒收 0；抓取条目 `quality=VALID excerpt=254 字`；检索摘要材料 `quality_status=['(未声明)']`
- **脚本侧材料交接**：1 份，`quality_status=['VALID']`，`source_id` 已绑定 → `[OK] 至少 1 份材料（门禁 VALID）进入抽取器` / `[OK] 材料已绑定已登记来源 ID`

8 项请求断言全 `[OK]`：

```
[OK] 线上 model = deepseek-flash
[OK] response_format 已映射为 json_object      （线上 schema → 线上契约的 json_object）
[OK] thinking = disabled
[OK] stream = false
[OK] 提示词含 "json" 字样
[OK] metadata（含 prompt_version）不外发
[OK] 请求体里**不含**密钥
[OK] Provider repr 里**不含**密钥
```

---

## 4. A11 门禁负样例（步骤 5 验收）

命令：`python scripts/e2e_p4_5a.py --a11-only` → 两类负样例均 OK，**零成本**。

规则：材料质量门禁不合格 → 拒收 → **零模型调用**（费用为 0）。

| 负样例 | 拒收原因 | 模型调用 | 产出候选 | 判定 |
|---|---|---|---|---|
| `LOW_QUALITY`（抓取正文未过门禁） | `['MATERIAL_NOT_GATE_PASSED']` | **0 次** | **0 条** | ✅ |
| 未声明（`quality_status=""`） | `['MATERIAL_UNDECLARED']` | **0 次** | **0 条** | ✅ |

结论：门禁在**进模型之前**就把不合格材料挡住，`require_gate_verdict=True` 下不存在"先花钱再拒收"的路径。

---

## 5. 真实调用预告与费用确认（步骤 6 的核心确认项）

`--dry` 的 [6] 段原文：

```
[OK] 预算闸门未处于暂停态（正常）
将发生          : **1 次**真实 POST https://api.deepseek.com/chat/completions
预估费用        : ≤ 0.0033 USD（est 3000 in / 2000 out @ USD 0.0003/0.0012 per 1K）
上限            : 0.25 USD（已占用 0.000000）
max_retries     : 0（失败不重试）
门禁            : require_gate_verdict=True
```

**逐项确认：**

- ✅ 将发生 **正好 1 次**真实调用（`max_calls=1`，脚本本身也只发 1 次）
- ✅ 预估 **≤ 0.0033 USD**
- ✅ 上限 **0.25 USD**，当前已占用 **0.000000**
- ✅ `max_retries=0` —— 失败**不重试**，不产生隐藏的第二次费用
- ✅ 若预检任一断言失败，脚本在 `if problems:` 处直接返回，**不发出任何请求**

> 换算说明：0.0033 USD 是**最坏情形**（按 `est_tokens_in/out` 满额估）。实际 5 句材料 + 单实体抽取，token 用量会明显低于估额，真实费用应显著小于该值。

---

## 6. 本轮新发现：两个缺口（必须报告，不得隐瞒）

### 6.1 G4 —— **A 级阻塞**：编排器路径丢抓取正文，严格 gate 下模型永不被调用

**静态事实**：`persona/modules/research.py` 的 `materials_from_extracts()` 是**唯一**会把 `quality_status=QUALITY_VALID` 带给抽取器的函数，而它在产品代码里**零调用点**。

**动态事实**（`--dry` 的 [5] 段，桩 Provider、零成本、独立临时库）：

```
整体状态        : PARTIAL        任务终态: COMPLETED
证据数          : 0
桩模型调用次数   : 0（0 = 模型根本没被调用）
  阶段 RESEARCH             → EXECUTED
  阶段 EVIDENCE_EXTRACT     → SKIPPED
  阶段 PERSONA_DISTILL      → EXECUTED
  阶段 RELATIONSHIP_DISTILL → SKIPPED
  阶段 DIALOGUE_RUN         → SKIPPED
告警码          : ['CLAIM_REJECTED', 'NO_CANDIDATES', 'NO_DIALOGUE_PROFILE',
                   'NO_EVENTS', 'NO_EVIDENCE_FOR_ENTITY']
证据阶段说明     : 从 1 个候选中登记 0 条证据，拒收 1 条：证据阶段未产出任何证据
```

**根因链**：`BusinessOrchestrator._step_research` 只 `self._remember(research.materials)` —— 而 `outcome.materials` 装的是**检索摘要**（`quality_status=""`），抓取正文在 `output.extracted` 里被留在原地。于是：

```
抓了正文 → 正文没交给抽取器 → 摘要进抽取器但没有门禁结论
→ require_gate_verdict=True 判 MATERIAL_UNDECLARED → 每份材料都拒收
→ 模型零调用 → 证据 0 条 → 人格档案无依据
```

**这是"抓了正文却交不出去"的死局。** 注意 `--path=script`（本轮采用）在脚本侧显式调 `materials_from_extracts()` 补上了这一步，所以**只有脚本路径能到模型**。

**脚本的处置（按停止条件 S1）**：对 `--path=orchestrator --run` **拒绝执行**，真实退出码 **3**，并明确写出：

```
你选择了 --path=orchestrator：按 G4，这条路径**到达不了模型**，真实请求只会在花费为 0 的情况下空转。
按停止条件 S1（真实调用未发生）**拒绝执行**，不改用其它路径掩盖问题。
```

—— 没有偷偷换路径把问题盖过去。

### 6.2 G5 —— 抽取器的 `warnings` 被编排器丢弃

`BusinessOrchestrator._register_candidates` 只把 `rejected` / `pending` 转出去，`outcome.warnings` 被丢掉。后果：材料被拒收的**具体原因**（`MATERIAL_UNDECLARED`）不出现在任务告警里，运维只能看到笼统的 `CLAIM_REJECTED`，定位成本高、且容易误判成"抽取器不好用"。

两者均已写成 `TestKnownGaps` 回归护栏（4 项）。

---

## 7. 与拍板项（D1–D5 / G1–G3）的对应

| 拍板 | 内容 | 本报告落实处 |
|---|---|---|
| D1 | 方案 B：桩检索 + 真实模型 | `--path=script`；数据源为 `StubSearchExtractProvider` |
| D2 | 装配层只写一次性脚本，放 `scripts/`，不改产品代码 | §1.2；`persona/` 零改动 |
| D3 | `pending` 保持现状；停止条件如实降级为"pending 候选不得被登记为证据" | 离线测试含 A4b（无 pending）；fail-closed 另立一轮，本轮不做 |
| D4 | 接受 `traits` 可能全空，不放宽 AC-7 | A8 断言：第 5 句 `TRAIT+SINGLE` 绝不进 `traits` |
| D5 | 暂不做真实抓取轮次，先跑通 B | 本轮无真实抓取 |
| G1 | 一次性脚本 + `--dry`，不进 unittest 收集 | `scripts/` 目录，`tests/` 只收 `test_p4_5a_e2e.py` |
| G2 | 桩 Search + 桩 Extract 返回合成正文，真实 DeepSeek 抽取 | §3.2/§3.3 |
| G3 | 保持现状，停止条件按 D3 降级 | §7 D3 行 |

---

## 8. 单人物链路的阶段语义（如实说明，非缺陷）

单个人物实体意味着参与者 <2 人。`_step_relationship` 与 `_step_dialogue` 在此情形下报 `STEP_SKIPPED`，而 `_overall_status` 只在五阶段全部 `EXECUTED/ALREADY_CURRENT` 且任务 `COMPLETED` 时才给 `RUN_SUCCEEDED`。因此单人物链路**只能报 `RUN_PARTIAL`**。

这是诚实的阶段语义，不是 bug；A2 的三项断言据此改写（研究 EXECUTED / 关系与对话 SKIPPED / 人格仍执行）。

---

## 9. 未做与待授权事项

1. **未发真实请求**（步骤 7）—— 等单独授权冒烟。
2. **未提交第二轮 Git**（步骤 8）—— 三个新文件 + 本报告仍为 untracked，等指示。
3. **未修 G4 / G5** —— 属产品代码改动，超出本轮授权。G4 的修法指向 `_step_research` 应把抓取正文经 `materials_from_extracts()` 交给抽取器（并顺带补上 `source_id` 绑定），建议另立一轮。
4. **未跑全量回归** —— 本轮零代码改动，未复跑；全量发现会触及网络模块，需单独安排。

---

## 10. 请求授权（步骤 7）

当前状态：**唯一会真实计费的入口是 `--run`，尚未执行。**

若授权冒烟，将执行的是：

```
python scripts/e2e_p4_5a.py --run --path=script
```

实际发生：**1 次** `POST https://api.deepseek.com/chat/completions`（`max_retries=0`），预估 **≤ 0.0033 USD**，上限 **0.25 USD**，随后立即用 `scripts/verify_p4_5a_outputs.py` 独立复算产物。

**请指示：是否授权这 1 次真实调用。**
