# P4.5-A 前置审查：端到端验证状态表 + 最小 E2E 测试案例设计

- 项目：`D:\AIprojects\generic-persona-skill`
- 性质：**设计稿 / 前置审查**。本轮**未执行任何真实调用**、**未改动任何代码**、**未提交 Git**。
- 依据：`ACCEPTANCE_REPORT_P4_2/P4_3/P4_4A/P4_4B/P4_4C_SMOKE/P4_5/P4_5_RETEST/P4_5_PLANC/H10/G4` + 本轮代码复核（读源码，不跑真实链路）。
- 本轮离线复跑（相关面，零真实请求）：**646 项通过**。

---

## 0. 一句话结论

端到端五环节中，**只有两个环节碰过真实外部系统**：`Research Material`（真实 Tavily，但经临时脚本、且检索侧为回放）
与 `Claim Extraction`（真实 DeepSeek，但材料是**直接注入**、未经过 Research）。
**"从真实检索到真实人格档案"这条完整链路一次都没跑过**；`Evidence Registration` 仅 Mock 覆盖；
`Persona Distillation` 的 AI 蒸馏主体**尚未实现**。因此 P4.5-A 的端到端测试**有实质增量价值**，但同时存在
**3 个必须先关闭的结构性缺口**（见 §4）。

---

## 1. 端到端验证状态表（主交付物）

### 1.1 总表

| # | 环节 | 已实现 | 仅 Mock 验证 | 真实模型/API 已验证 | 端到端（真实模型串全链） | **判定** |
| --- | --- | --- | --- | --- | --- | --- |
| ① | **Research Material**（检索 → 抓取 → 摘要 → 质量门禁 → `SourceMaterial`） | ✅ | ✅（`test_research_module` / `test_provider_integration_readiness`） | ⚠️ **部分**：真实 Tavily `/search`、`/extract` 均成功；`/extract` 经 `ResearchService._maybe_extract()` 发出（P4.3 §5.5、P4.5_RETEST §1） | ❌ | **真实 Provider 已验证，但无生产装配；检索侧从未与真实模型串联** |
| ② | **Claim Extraction**（模型抽候选 → 严格结构与摘录校验 → `CandidateClaim`） | ✅ | ✅（`test_p4_4a_model_extraction` 84 项） | ✅ **是**：P4.4-C 冒烟 1 次真实 DeepSeek，`verdict=OK`、5/5 VERBATIM（P4.4-C §3） | ❌（材料为直接注入，未经过 ①） | **真实模型已验证（单点、孤立）** |
| ③ | **Evidence Registration**（候选 → `EvidenceDraft` → `add_evidence` / `record_inference`；来源可追溯闸门） | ✅ | ✅（`test_business_orchestrator`、`test_h10_e2e`、`test_evidence_module`） | ❌ **从未** | ❌ | **仅 Mock 验证；真实模型产出的候选从未走过登记** |
| ④ | **Gate**（材料质量门禁 / 证据登记门禁 / 人格稳定性门禁，三处分布，见 §1.3） | ✅ | ✅（正负样例均离线覆盖） | ⚠️ **部分**：材料门禁在 P4.4-C 用桩做正负样例；"拦住导航摘录"在真实数据上**未证成**，且暴露过"判定对象错位"缺陷（已按方案 C 修为判摘录） | ❌ | **部分真实已验证；真实拦截能力未证成** |
| ⑤ | **Persona Distillation**（证据 → 人物档案） | ⚠️ **规则式搬运**（`_build_persona_draft`，不做语义理解）；**AI 蒸馏主体 `distill` 未实现**（`persona.py` 顶部显式声明） | ✅（`test_persona_module`、`test_business_orchestrator`） | ❌ **从未** | ❌ | **尚未验证；且"蒸馏"目前不是模型行为，是确定性规则** |

> 图例：✅ 已完成 ／ ⚠️ 部分完成 ／ ❌ 未做。

### 1.2 逐环节判定依据（可回溯出处）

| 环节 | 判定 | 出处 |
| --- | --- | --- |
| ① | 真实 `/search`、`/extract` 成功；`/extract` 确实由 `ResearchService` 发起（预留→发送→结算全流程） | `ACCEPTANCE_REPORT_P4_3.md` §5.5；`ACCEPTANCE_REPORT_P4_5_RETEST.md` §1（"第 1、2 次由 `ResearchService.run` 发起"） |
| ① | **P4.3 那次 `/search` 是回放**（从隔离库读已存来源），不是重发 | `ACCEPTANCE_REPORT_P4_3.md` §5.1 表格首行 |
| ① | 全仓 `ResearchService(` 只出现在 `tests/`，**产品侧无装配点** | 本轮复核：`grep -rn "ResearchService(" --include=*.py .` → 仅 `tests/test_*.py` |
| ② | 真实模型 1 次调用成功、`response_format` 映射为 `json_object`、`thinking=disabled`、5/5 摘录可追溯 | `ACCEPTANCE_REPORT_P4_4C_SMOKE.md` §1–§3 |
| ② | 材料为**直接注入**的合成材料，未经 ① | `ACCEPTANCE_REPORT_P4_4C_SMOKE.md` §8.3；`scripts/smoke_p4_4c.py` 第 230 行构造材料 |
| ③ | 三路径桥接已实现：`_to_evidence_draft` → `add_evidence`；`INFERENCE` → `record_inference`；`source_id` 不在本次来源内 → `Err(VALIDATION_DANGLING_REFERENCE)` | 本轮复核 `persona/orchestrator/business.py` L587–L720 |
| ③ | 但驱动它的只有 Mock Extractor + Mock Search | `tests/test_h10_e2e.py` §6 能力边界表（"Mock Claim Extractor ✅ / 真实语义模型 ❌"） |
| ④ | 材料门禁判**摘录**（方案 C 已落地） | 本轮复核 `persona/modules/research.py` L1511 `assess_content_quality(item.excerpt, ...)` |
| ④ | 出口只放行 `VALID`，并把结论随材料交给下游（`quality_status=QUALITY_VALID`） | 本轮复核 `persona/modules/research.py` `materials_from_extracts()` L428–L481 |
| ④ | "拦住导航摘录"真实未证成 + 判定对象错位缺陷 | `ACCEPTANCE_REPORT_P4_5_RETEST.md` §0 第 4 条、§4.2、§7 |
| ④ | 材料门禁正负样例（LOW_QUALITY → 拒收；未声明 → 拒收；两者**零模型调用**） | `ACCEPTANCE_REPORT_P4_4C_SMOKE.md` §3.3 |
| ⑤ | `distill`（AI 蒸馏）**未实现**，不调 `Provider.chat` | 本轮复核 `persona/modules/persona.py` L13–L14、L27、L110；`persona/modules/relationship.py` L13–L14 |
| ⑤ | 编排器里的人格构建是**规则式搬运**（"按维度搬运，不做语义理解"） | 本轮复核 `persona/orchestrator/business.py` `_build_persona_draft` L916–L920 |

### 1.3 三个必须知道的结构性事实

**事实 1：真实链路的"装配层"不存在。**
`ResearchService(` 与 `BusinessOrchestrator(` 在全仓只出现在 `tests/`；产品侧只有 `model_setup.py` 这一个**只覆盖模型抽取**的装配层。
含义：**今天没有任何一条命令能跑"真实检索 → 真实模型 → 真实证据 → 真实档案"**。P4.4-C 的冒烟是靠一次性脚本手工拼的。

**事实 2：门禁不是一处，是三处，位置与你给的五段链顺序不一致。**

| 门禁 | 位置 | 在链上的作用点 |
| --- | --- | --- |
| 材料正文质量门禁 | `research.assess_content_quality` + `materials_from_extracts` + `extraction.admit_materials` | 在 **②之前**（Research 出口 / 抽取器入口） |
| 证据登记门禁 | `evidence.add_evidence`（EV-V2：VERBATIM 必有 `verbatim_quote`；SE-V2：`locator` 至少一个子字段）+ 编排器 `source_id ∈ 本次来源` 闸门 | 在 **③之中** |
| 人格稳定性门禁 | `persona.apply_stability_gate` + `business._build_persona_draft`（AC-7：`SINGLE`/`UNKNOWN` 的 TRAIT 证据不得进 `traits`） | 在 **⑤之中** |

你给的顺序是 `… → Evidence Registration → Gate → Persona Distillation`。**代码里没有"证据登记之后再设一道总闸"**。
设计 E2E 时我按"三处门禁各自断言"处理，而不是假设存在一道后置总闸。

**事实 3：`pending`（待审核）不阻断整链，只"不登记"。**
`business._register_candidates` 把 `outcome.pending` 转成 `WARNING_CLAIM_PENDING_REVIEW` 提示，**顺序继续、任务不 FAILED**。
"阻断下游"目前的实际语义是：**pending 候选不进证据池 → 下游（人格）看不见它们**，而不是"整链停下"。
是否需要改成 fail-closed，属待拍板项（见 §5-D3）。

---

## 2. Git 工作区状态（本轮只读检查，未覆盖任何改动）

| 项 | 值 |
| --- | --- |
| 当前 HEAD | `35c9722`（P4.1~P4.5 统一提交） |
| 暂存区 | **空**（`git diff --cached --name-only` 无输出） |
| 已修改未提交 | 20 个文件（`persona/cli/main.py`、`persona/config/*` 3 个、`persona/core/types.py`、`persona/core/usage_ledger.py`、`persona/modules/extract_quality.py`、`persona/modules/extraction.py`、`persona/modules/research.py`、`persona/orchestrator/{__init__,business}.py`、`persona/providers/{__init__,registry,tavily}.py`、`persona/storage/sqlite.py`、`tests/test_p4_5_usage_quality.py`、`tests/test_provider_mock.py`、`.env.example`、`docs/CONFIG.md`、`docs/ACCEPTANCE_REPORT_P4_5_PLANC.md`） |
| 未跟踪（新增） | 11 项：`docs/ACCEPTANCE_REPORT_P4_4A.md`、`docs/ACCEPTANCE_REPORT_P4_4B.md`、`docs/ACCEPTANCE_REPORT_P4_4C_SMOKE.md`、`docs/IMPLEMENTATION_REPORT_P4_4C_DEEPSEEK_PROVIDER.md`、`docs/PLAN_P4_4C_DEEPSEEK_PROVIDER.md`、`persona/modules/claim_validation.py`、`persona/orchestrator/model_setup.py`、`persona/providers/deepseek.py`、`scripts/`、`tests/test_p4_4a_model_extraction.py`、`tests/test_p4_4b_model_setup.py`、`tests/test_p4_4c_deepseek_provider.py` |
| 本轮动作 | **只读查看**（`git status` / `git diff --cached`）。未 `add`、未 `commit`、未 `checkout`、未 `stash`、未覆盖任何文件 |
| `.env` | 存在且**被 `.gitignore:22` 忽略**，`git status` 里不出现 |
| 风险提示 | 全链 P4.4 成果（含 3 个报告 + 3 个测试文件 + 2 个新模块）**仍未入库**。若后续误操作 `git checkout .` 会一次性丢失 |

---

## 3. 最小端到端测试案例设计（**只设计，不执行**）

### 3.1 一个必须先说清的设计约束：为什么必须"桩检索 + 真实模型"

你要用**合成虚构人物**（阿雾／雾港／星灯，符合 AC-16）。而合成人物在公网上**不存在** ——
真实 Tavily 检索必然返回 0 条相关结果。

因此"合成材料"与"真实检索"在逻辑上互斥，只能二选一：

| 方案 | 检索 | 抽取 | 检查什么 | 代价 |
| --- | --- | --- | --- | --- |
| A. 真实检索 + 真实模型 | 真实 Tavily | 真实 DeepSeek | 真实检索质量 + 真实抽取 | **必须用真实人物**，与 AC-16「测试素材用合成虚构名」冲突，且无法回收 |
| **B. 桩检索 + 真实模型**（推荐） | 桩（返回合成正文，但**门禁判真**） | **真实 DeepSeek** | ②③④⑤ 四段真链 + 全链记账 | 1 次真实调用；① 只验证"材料形状与门禁" |
| C. 桩检索 + 桩模型 | 桩 | 桩 | 只能验编排接线（≈ 现有 H-10） | 0 成本，但**无增量价值** |

**本设计选 B**：唯一能同时满足「合成材料」「真实模型」「5 段全链」的方案。
①（Research Material）在这一案里**不是真实检索**，而是"用真实 Research 代码 + 真实门禁 + 桩数据源"，
判定口径如实写作 **"仅形状与门禁经过真实代码，数据源为桩"** —— 不冒充真实检索。

### 3.2 输入

| 项 | 取值 |
| --- | --- |
| 目标（单个人物） | `pe_awu`（阿雾），`entity_type = FICTIONAL_PERSON`（虚构角色须声明适用版本，见 PF-V5） |
| 数据源 | **桩** `SearchProvider` + 桩 `ExtractPort`，返回下面 1 份合成正文（等价于 `/extract` 的 `raw_content`） |
| 材料数 | **1**（`research.max_extracts = 1`、`max_results = 1`） |
| 合成正文（约 110 字，**4 句话，主语全是阿雾**） | ① 阿雾是雾港灯塔的常年值守者。<br>② 据雾港地方志记载，阿雾偏爱在傍晚独自巡视堤坝。<br>③ 阿雾向来沉默寡言，很少与星灯会的同伴闲谈。<br>④ 阿雾曾在一次夜雾中连续值守七十二小时。 |
| 白名单（主体） | `{pe_awu}` —— **只含阿雾**，不含第三方主体（避免"未知主体被拒"混入噪声） |
| Claim 数量上限 | ≤ 4（期望 4；`max_extracts=1` 保证不会因多材料放大） |
| 模型 | `deepseek` / `deepseek-flash`，**不换模型** |
| 币种 / 档位 | USD / peak（$0.3 · $1.2 per 1M） |
| 预算 | 单任务 **0.25 USD**、总 **0.25 USD**、`max_calls = 1`、`max_retries = 0` |
| 门禁开关 | `require_gate_verdict = true` |
| 账本 | **新建独立实例**；不创建、不重置、不清空任何已有账本文件 |
| 存储 | `tempfile.mkdtemp(prefix="gps-p45a-")` 隔离库，`project_id="prj_p45a_e2e"` |

**第 ④ 句是刻意埋的探针**：它是**单次事件**。按 AC-7 / PF-V2 / D-012，
单次行为**不得**被固化成稳定性格 —— 它必须落进 `single_events`（或干脆不进档案），**绝不能进 `traits`**。

### 3.3 预期输出

| 环节 | 预期 |
| --- | --- |
| ① Research Material | 1 条 `Source` 登记；1 份 `SourceMaterial`，`quality_status = VALID`（门禁判**摘录**，不是判全文）；`snippet` = 上面 4 句话；被拒条目 0 |
| ② Claim Extraction | 真实调用 **恰好 1 次**；整批 `verdict = OK`；`accepted = 4`、`rejected = 0`、`pending = 0`；4 条全部 `tier = VERBATIM` 且带 `verbatim_quote`；`locator` **由框架算出**（模型伪造不了位置）；`subject_entity_ids` 全为 `{pe_awu}` |
| ③ Evidence Registration | 登记 **4 条** `EvidenceItem`；每条 `tier = VERBATIM` 且 `verbatim_quote` 非空；每条 `source_id` ∈ 本次登记的来源；`INFERENCE` 0 条；`detect_conflicts` 无冲突（4 句互不矛盾） |
| ④ Gate | 材料门禁：1 份材料 `VALID` 放行；证据门禁：4/4 通过 EV-V2 + SE-V2；人格稳定性门禁：第 ④ 句对应条目不进 `traits` |
| ⑤ Persona Distillation | `pe_awu` 生成档案 **v1**；条目 `evidence_refs` **全部**命中本次已登记证据 ID（不可追溯引用 = 0）；第 ④ 句进 `single_events` 或不在档案中 |
| 编排状态 | 5 个阶段全部 `STEP_EXECUTED`；任务终态 `COMPLETED`；`RUN_COMPLETED` |
| 账本 | 1 条记录：`op = CHAT`、`provider = deepseek`、`cost_status = CONFIRMED`、`cost_source = OFFICIAL_RATE`、`currency = USD`、`request_id` **非空**（= 服务端响应 `id`）；`unknown_requests = 0` |

### 3.4 验收标准（逐条可量化，可自动断言）

| # | 验收标准 | 断言方式 |
| --- | --- | --- |
| A1 | 真实网络调用**恰好 1 次**（记录器计数），且 `max_retries = 0` | 计数器 `== 1`；配置断言 `max_retries == 0` |
| A2 | 5 个阶段顺序 `RESEARCH → EVIDENCE_EXTRACT → PERSONA_DISTILL → RELATIONSHIP_DISTILL → DIALOGUE_RUN`，全部 `STEP_EXECUTED`（无 `SKIPPED`/`NOT_REACHED`） | 顺序与状态等值断言 |
| A3 | 卡片可追溯：**0 条**证据的 `source_id` 不在本次来源集合内 | 集合包含断言 |
| A4 | 摘录可核验：每条 `VERBATIM` 证据的 `verbatim_quote` 能在材料 `snippet` 中按 `locator` 命中 | 独立复算（另写离线复算脚本，不信模型自报） |
| A5 | 费用可复算：`tokens_in/1000×0.0003 + tokens_out/1000×0.0012 == cost_confirmed`（容差 1e-9） | 数值断言 |
| A6 | 记账语义正确：`cost_status = CONFIRMED`、`currency = USD`、`cost_source = OFFICIAL_RATE`；**若响应无 `usage` 必须记 `COST_UNKNOWN` 且不得记 0** | 枚举 + 反例断言 |
| A7 | 人格可追溯：每个档案条目的 `evidence_refs` ⊆ 已登记证据 ID 集合，**不可追溯引用数 = 0** | 集合包含断言 |
| A8 | AC-7：进入 `traits` 的任一条目，其证据 `stability` 必须**不是** `SINGLE`/`UNKNOWN`；第 ④ 句 **不在** `traits` | 反例断言（**不要求 `traits` 非空** —— 全空是保守正确结果） |
| A9 | 预算：`occupied_cost() ≤ 0.25`，且账本记录数 = 1 | 数值断言 |
| A10 | 幂等：同 task 重跑 → 证据集合不变、档案不产生新版本；且在 `max_calls = 1` 下重跑**被预算闸门拦下**（`BUDGET_EXCEEDED`，可恢复暂停），**不产生第 2 次真实调用** | 计数 + 状态断言 |
| A11 | 门禁负样例（离线桩，零真实调用）：`LOW_QUALITY` → `MATERIAL_NOT_GATE_PASSED`；`quality_status = ""` 且 `require_gate_verdict = true` → `MATERIAL_UNDECLARED`；两者**模型调用数 = 0** | 拒收码 + 计数断言 |
| A12 | 不把"格式正确"当"事实可信"：产出物是 `CandidateClaim` / `EvidenceItem`，**不存在**任何"已验证/已批准"字段被置真 | 结构断言 |

### 3.5 失败条件 / 停止条件

**触发任一条立即停止，不做重试、不进入下一阶段：**

| # | 停止条件 |
| --- | --- |
| S1 | 真实调用**未发生**（预检失败 / 装配失败 / 密钥缺失）或返回 `Err` |
| S2 | 出现**第 2 次**真实调用（连重试也不行；`max_retries` 必须为 0） |
| S3 | 输出**无法追溯**：出现摘录不在材料中 / `locator` 对不上 / 证据 `source_id` 越界 |
| S4 | **预算核算不可靠**：`cost_confirmed` 为 `None` 却未标 `COST_UNKNOWN`；或 `CONFIRMED` 但金额为 0 而 `tokens > 0`；或 `occupied_cost() > 0.25` |
| S5 | **门禁失效**：`LOW_QUALITY` 或未声明材料仍触发了模型调用；或 `pending` 候选被登记成了证据 |
| S6 | **关键回归失败**（任一项既有断言变红） |
| S7 | 账本状态不明（读不出记录 / 汇总与实际调用笔数不符） |

> 说明：S5 的判定**不包含**"`pending` 是否让整链失败" —— 因为按现状它不会（见 §1.3 事实 3）。
> 该项留作 §5-D3 拍板，不伪装成已满足。

### 3.6 费用上限

| 项 | 值 |
| --- | --- |
| 单任务上限 | **0.25 USD** |
| 总上限 | **0.25 USD**（两层取更严，即 0.25） |
| 事前估算（`est_tokens_in=3000` / `out=2000`） | `3000/1000×0.0003 + 2000/1000×0.0012` = **0.0033 USD** |
| 参照实测（P4.4-C 同规模） | 718 in / 476 out → **0.0007866 USD** |
| 预留策略 | 发送**前**预留 0.0033；结算时按真实 token 复算并冲回差额 |
| 风险 | 4 句材料的 token 量与 P4.4-C 同量级，预估实际 ≤ 0.002 USD，**距上限有 100 倍余量**。即便模型返回异常长内容，`max_tokens` 约束下也不会上探到 0.25 |

### 3.7 建议的执行顺序（未来轮次）

| 步 | 动作 | 是否触网 |
| --- | --- | --- |
| 0 | 关闭 §4 的三个前置缺口（需授权） | 否 |
| 1 | **离线**装配 + `--dry`：只打印"将要发出的请求"与全部预检断言 | 否 |
| 2 | **离线**跑 A11 门禁负样例（桩 Provider） | 否 |
| 3 | 打印"将发生 1 次真实调用 / 预估 ≤0.0033 USD / 上限 0.25"，**人工确认后**再执行 | 否 |
| 4 | 执行 **1 次**全链 E2E（真实模型） | ✅ 1 次 |
| 5 | 离线复算 A3–A8（独立脚本，不信模型自报） | 否 |
| 6 | 跑 A10 幂等重跑（被预算拦下，0 真实调用） | 否 |
| 7 | 全量回归 + 合规扫描 | 否 |

### 3.8 建议的落点（**本轮不创建**）

| 文件 | 性质 |
| --- | --- |
| `scripts/e2e_p4_5a.py` | 一次性运行器（含 `--dry`），不进 `unittest` 收集 |
| `scripts/verify_p4_5a_outputs.py` | 离线复算器（摘录可追溯、费用复算、transitivity） |
| `tests/test_p4_5a_e2e.py` | **全离线**的 E2E 集成测试（桩 Provider，覆盖 A2–A8 + A10 + A11） |

---

## 4. 执行前必须关闭的三个前置缺口（**需你拍板**）

| # | 缺口 | 影响 | 可选做法 |
| --- | --- | --- | --- |
| **G1** | **无生产装配层**：`ResearchService` / `BusinessOrchestrator` 只在 `tests/` 被 new 过 | 真实 E2E 只能靠一次性脚本手工拼装；每跑一次都要重写一遍接线，接错的风险高（H-10 就曾被"演示脚本接线 bug"误导过一次 —— 见 `ACCEPTANCE_REPORT_H10.md` §5） | ① 只写在 `scripts/` 的一次性脚本里（**不改产品代码**，最小侵入）；② 新增 `orchestrator/e2e_setup.py` 作为正式装配层（可复用，但要改产品代码） |
| **G2** | **DeepSeek Provider 只有 `chat()`**，没有 `extract()`；且合成人物检索必然 0 结果 | ① 无法真实检索；② 无法真实抓取 | 已在上文按方案 B 处理（**桩检索 + 真实模型**）。若你要求真实检索，则必须改用真实人物材料 —— 与 AC-16 冲突，需你明确豁免 |
| **G3** | **`pending` 不阻断整链**（只不登记 + 出 warning） | 你的停止条件里有"PENDING 门禁失效"一项；按现状它**不会**让链路停下，因此该项无法作为"整链级"停止条件 | 保留现状（把停止条件降为"pending 候选不得被登记为证据"）／改为 fail-closed（**要改产品代码**） |

---

## 5. 待拍板决策项

| # | 决策 | 我的建议 |
| --- | --- | --- |
| **D1** | E2E 数据源：**B（桩检索 + 真实模型）** 还是 A（真实检索 + 真实模型，须用真实人物）？ | **B**。唯一同时满足 AC-16「合成素材」与「真实模型」的方案 |
| **D2** | 装配层落点：一次性脚本（不改产品码）还是正式 `e2e_setup.py`？ | **一次性脚本**（`scripts/`），与 P4.4-C 冒烟同口径，不扩大改动面 |
| **D3** | `pending` 语义：保持"不登记 + warning"，还是改 fail-closed？ | **保持现状**，但把停止条件如实降级为"pending 不得被登记为证据"；fail-closed 另立一轮 |
| **D4** | 档案 `traits` 全空是否接受？（模型若不声明 `stability`，按 AC-7 全部走 `single_events`） | **接受**。这是保守正确结果，**不得**为了让 `traits` 非空而放宽 AC-7 |
| **D5** | 是否允许在 E2E 中补一条"真实抓取 + 真实模型"的后续轮次（需真实人物材料与 AC-16 豁免）？ | **暂不做**。先跑通 B，把 ①②③④⑤ 的接线证据拿到，再评估是否需要真实检索 |

---

## 6. 本轮明确未做的事（不冒充已验证）

1. **未执行任何真实调用**：无 Tavily 请求、无 DeepSeek 请求、无费用产生。
2. **未改动任何代码**：`persona/`、`tests/`、`scripts/` 均未改动；本轮**只读**复核。
3. **未创建任何文件到 `scripts/`**：本报告只是设计稿，运行器与测试留待授权的轮次。
4. **未重置/创建/清空任何账本**：本轮根本没有碰账本。
5. **未安装任何依赖**；未访问网络；未提交 Git（暂存区仍为空）。
6. **未复跑全量**：只跑了相关面 **646 项**（`test_h10_e2e`、`test_business_orchestrator`、`test_claim_extraction`、`test_research_module`、`test_persona_module`、`test_evidence_module`、`test_p4_4a/b/c`、`test_p4_5_usage_quality`、`test_dependency_rules`）→ **OK**。
   全量上轮实测为 **1122 项通过**（`ACCEPTANCE_REPORT_P4_4C_SMOKE.md` §6），本轮无代码改动故未复跑。
7. **未验证的结论继续标为未验证**：真实检索质量、真实抽取质量、真实证据登记、真实人格档案 —— 四者都还没在真实链路上跑过一次。

---

## 7. 结论

- P4.5-A 端到端测试**值得做**：五段里有 **三段（③ 证据登记、④ 门禁、⑤ 人格档案）从未接触过真实模型输出**，
  且这三段恰好是"格式正确 ≠ 事实可信"这条红线的落点。
- 但**先要拍 D1–D5、关 G1–G3**，否则跑出来的结果无法归因（分不清是接线错、门禁错还是模型错）。
- 建议路径：**方案 B + 一次性脚本 + 1 次真实调用 + 0.25 USD 上限**，先证明"真实模型产出的候选能否安全穿过证据门禁并形成可追溯档案"。
