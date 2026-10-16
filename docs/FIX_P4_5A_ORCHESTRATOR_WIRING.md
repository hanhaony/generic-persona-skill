# P4.5-A 接线轮：`--run` 默认走编排器路径（产品代码全链）

> 状态：**接线完成，`--dry` 已实测，等待真实冒烟授权。**
> 真实模型调用：**未发生**。真实检索调用：**未发生**。费用：**0 USD**。
> 本文所有数字均为本机实测输出，非推断。

---

## 0. 本轮范围（用户拍板）

| # | 拍板 | 落实情况 |
| --- | --- | --- |
| 1 | 「正文取代摘要」接受，保留 `MATERIAL_SUPERSEDED` 告警 | 保留原样，未改 |
| 2 | 授权真实冒烟 | **尚未执行**（等本轮确认后再授权） |
| 3 | 先补 `--path=orchestrator` 真实运行接线，让 `--run` 默认走编排器 | ✅ 本轮完成 |

本轮**只改脚本与测试**：`scripts/e2e_p4_5a.py`、`tests/test_p4_5a_e2e.py`。
产品代码 `persona/orchestrator/business.py` **一行未动**（仍是上一轮已验收的 `+136 / −9`）。

---

## 1. 接线做了什么

### 1.1 `--run` 默认走编排器

```python
parser.add_argument("--path", choices=("script", "orchestrator"),
                    default="orchestrator", ...)   # 原默认值 = "script"
```

`--path=script` 保留为对照通道（同一套断言，可随时对比两条链路）。

### 1.2 新增 `run_orchestrator_real()`

与脚本侧路径的**唯一差别就是"不代劳"**：

| | `--path=script` | `--path=orchestrator`（新） |
| --- | --- | --- |
| 材料交接 | **脚本**调 `materials_from_extracts()` + 绑 `source_id` | 产品代码 `_material_flow` |
| 证据登记 | `_register_candidates` | `_register_candidates` |
| 门禁 | 真实 | 真实 |
| 人格档案 | `_build_persona_draft` / `_write_persona` | 同上（由 `_step_persona` 内部调用） |
| 谁在跑 | 脚本拼装 | `BusinessOrchestrator.run()` 走完五个阶段 |

脚本只剩两件事：**发送前**把真实请求体构造出来核验；**运行后**把
`BusinessRunResult` + 账本 + 材料流读出来如实汇报。**不替产品代码补任何一环**
—— 这正是 G4 的教训：脚本侧代劳会让产品缺陷被掩盖。

### 1.3 两个离线测试注入点（`provider` / `on_targets`，默认 `None`）

真实运行不传这两个参数，行为完全不受影响。它们存在的理由是：**否则"接线本身"
没有任何测试锁住**，只能靠花钱才发现接错。有了它们，同一条产品代码链路可以在
桩模型 + 断网护栏下零成本复跑（见 §3.2）。

### 1.4 `RecordingExtractor`

编排器在 `_step_evidence` 内部自己调 `deps.claim_extractor.extract(...)`，调用方
拿不到 `ExtractionOutcome`。加一层**透明代理**把最后一次 outcome 留下来，喂给独立
复算器（复算摘录可追溯性与费用需要候选的 `locator` / `verbatim_quote` /
`source_id`）。代理不改产品代码、不另写装配，`*args/**kwargs` 原样透传。

### 1.5 `build_parser()` 独立出来 + 产物加 `mode` / `profile_id`

- `build_parser()`：让离线测试能直接断言"默认路径 = orchestrator"，不必去跑 `main()`（`main()` 依赖本机环境变量，违反测试纪律）。
- 产物 JSON 新增 `mode`（`orchestrator-path` / `script-side-assembly`）与真实 `persona.profile_id`（原来恒为 `None`）。

---

## 2. 真实运行走**单人物**，理由是「零桩掩盖」

`--run` 默认跑**阿雾一个人**：

| 阶段 | 单人物下的实际执行体 |
| --- | --- |
| ① 研究（检索→抓取→门禁→材料） | 桩数据源（方案 B 既定：合成人物在公网不存在）+ 真实门禁 |
| ②③ 抽取 + 证据登记 | **真实 DeepSeek**（1 次）+ 真实登记/闸门 |
| ④⑤ 门禁 + 人格档案 | 真实产品代码 |
| 关系档案 | `SKIPPED`（参与者 < 2，语义跳过） |
| 模拟对话 | `SKIPPED`（同上） |

**关键**：两个 `SKIPPED` 的阶段**按语义跳过**，因此链上**没有任何桩对话
Provider 被调用**（脚本会断言 `dialogue_stub.chat_calls == 0`）。也就是说，
**真正执行的每一个阶段都是产品代码 + 真实模型**，"桩"只剩数据源一处。

为什么不跑双人物？双人物能报 `SUCCEEDED`，但 1 次调用授权只够抽取，对话阶段必须
换成桩 Provider —— 那会让 `SUCCEEDED` 里**掺进桩产物**。相比之下，单人物虽然只能
报 `PARTIAL`，但 `PARTIAL` 的原因（参与者 < 2）是**诚实的阶段语义**，不是缺陷，
也不是被桩掩盖的结果。双人物仍保留在 `--dry` 的 [5b] 诊断里（桩模型、零成本）。

> 与用户给出的授权边界一致：合成虚构人物写的是「阿雾/雾港/星灯」，即单人物材料集。

---

## 3. 测试结果

### 3.1 `tests/test_p4_5a_e2e.py`：**37 → 45 项全绿**（4.7 s）

新增 8 项，落在两个新类：

| 测试 | 锁住什么 |
| --- | --- |
| `TestOrchestratorRealRunWiring.test_default_path_is_orchestrator` | `--run` 默认路径 = orchestrator，且默认不触发真实执行 |
| `...test_explicit_script_path_still_available` | 对照通道未被拆掉 |
| `...test_recording_extractor_delegates_and_captures` | 代理原样透传 + 留下最后一次 outcome |
| `...test_orchestrator_real_run_end_to_end_offline` | **接线端到端可跑**：桩模型 + 断网护栏下跑完整链，产物过独立复算器 **全绿** |
| `...test_orchestrator_stage_table_is_single_person_honest` | 单人物必须如实 `SKIPPED` + `PARTIAL`，不得报 `SUCCEEDED` |
| `TestKnownGapStabilityNotWired`（3 项） | 锁住新发现的 **G6**（见 §5） |

其中 `test_orchestrator_real_run_end_to_end_offline` 是最有价值的一项：它把
"接线对了"变成**可离线判定的断言**（`problems == []`、材料 `VALID` 且绑来源、
`real_chat_calls == 1`、5 条证据、`verify_p4_5a_outputs` 全部通过），而不是"靠花钱
才能验证"。

### 3.2 全量回归：**1167 项全绿**（3 批定向跑）

| 批次 | 模块数 | 用例数 | 结果 |
| --- | --- | --- | --- |
| 1 | 7（P4.5-A / 编排器 / h10 / 研究 / 证据 / 人格 / 抽取） | 351 | OK |
| 2 | 7（P4.4-A/B/C / P4.5 用量质量 / 分层铁律 / imports / P4.2 合规） | 394 | OK |
| 3 | 16（CLI / 配置 / 契约 / 对话 / 记忆 / Provider / 关系 / 存储 / 任务 / 验收） | 422 | OK |
| **合计** | **30** | **1167** | **全绿** |

> 上一轮为 1159；本轮 +8（新增测试），1159 + 8 = 1167，对得上。
> 含 `test_dependency_rules`（分层铁律）与 `test_imports`。

### 3.3 A11 门禁负样例（零成本复跑）

| 负样例 | 拒收原因 | 模型调用 | 候选 |
| --- | --- | --- | --- |
| `LOW_QUALITY` | `['MATERIAL_NOT_GATE_PASSED']` | **0** | **0** |
| 未声明 `""` | `['MATERIAL_UNDECLARED']` | **0** | **0** |

面板仍是一处未改的护栏；`--a11-only` 退出码 0。

---

## 4. `--dry --path=orchestrator` 实测

退出码 **0**，预检**全部通过**，**未发出任何真实请求**。

### [1] 配置核验（11 项全 OK）

`provider=deepseek` / `model_id=deepseek-flash` / `currency=USD` / `price_tier=peak` /
`max_cost_effective=0.25` / `max_calls=1` / `require_gate_verdict=True` /
`max_retries=0` / `max_extracts=1` / 密钥已设置（只检查存在性）。

### [3] 合成材料门禁复算（真实 `assess_content_quality`）

`status=VALID`、`prose_chars=246`（下限 200）、`nav_line_ratio=0.0`、
`target_mentioned=True`。

### [4] 装配与请求预检（离线构造，不发送）

```
① 研究：来源 1 条 / 抓取 1 条 / 材料（检索摘要）1 份 / 门禁拒收 0 条
    抓取条目 quality=VALID excerpt=254 字
    [OK ] 至少 1 份材料（门禁 VALID）进入抽取器
    [OK ] 材料已绑定已登记来源 ID
    [OK ] 线上 model = deepseek-flash
    [OK ] response_format 已映射为 json_object
    [OK ] thinking = disabled
    [OK ] stream = false
    [OK ] 提示词含 "json" 字样
    [OK ] metadata（含 prompt_version）不外发
    [OK ] 请求体里**不含**密钥
    [OK ] Provider repr 里**不含**密钥
```

### [5] 编排器路径诊断（桩 Provider，零成本）

**5a 单人物**：整体状态 `PARTIAL`（关系/对话按语义跳过）、任务终态 `COMPLETED`、
**材料流 1 份（带门禁结论 1 / 已绑定来源 1）**、**证据 5 条**、**模型调用 1 次**、
告警码 `['MATERIAL_SUPERSEDED','NO_DIALOGUE_PROFILE','NO_EVENTS']`、
证据阶段 `从 5 个候选中登记 5 条证据，拒收 0 条`。

**5b 双人物**：整体状态 **`SUCCEEDED`**、五阶段全 `EXECUTED`、**证据 10 条**、
材料流 2 份（门禁结论 2 / 已绑定 2）。

### [5c] 路径可用性

```
`--path=orchestrator`：真实运行**已接线**（产品代码全链：研究 → 抽取 → 证据登记 → 门禁 → 人格档案）。
单人物请求：关系/对话阶段按语义 SKIPPED（参与者 <2），链上**没有**任何桩对话 Provider 被调用。
本次是 --dry：**不发出任何真实请求**；下方 [6] 给出『将发生什么』的预告。
```

原来这里的「按停止条件**拒绝执行**」已删除 —— 接线完成后该分支不再是诚实行为。

### [6] 预算与真实调用预告

```
将发生          : **1 次**真实 POST https://api.deepseek.com/chat/completions
预估费用        : ≤ 0.0033 USD（est 3000 in / 2000 out @ USD 0.0003/0.0012 per 1K）
上限            : 0.25 USD（已占用 0.000000）
max_retries     : 0（失败不重试）
门禁            : require_gate_verdict=True
```

### 对照：`--dry --path=script` 仍可用（退出码 0，预检全通过）

---

## 5. 新发现：G6 —— `stability` 链路断在中间

> **本轮发现，未授权修复，已用测试锁住（`TestKnownGapStabilityNotWired`）。**
> 它不是接线缺陷，是"有个现成函数没人调"的同类问题（与 G4/G5 同族）。

两个事实叠加：

1. **抽取器刻意不解析**模型自报的 `stability`。载荷里写 `REPEATED` / `PATTERN` /
   `SINGLE` 都无效 —— 实测 5 条候选**一律 `UNKNOWN`**。
   这是刻意的：`CandidateClaim.stability` 的 docstring 明写"无法可靠取得的字段一律
   留空 / 记 UNKNOWN，**不得编造**"。模型无权宣布自己的说法稳定（PF-V2 / D-012）。
2. 框架**自带**确定性判定器 `Evidence.judge_stability()`（1 条=SINGLE、
   ≥2 条=REPEATED、≥3 个不同来源=PATTERN），但在**生产代码里零调用点**
   （全仓只在 `tests/test_evidence_module.py` 里被调）。

**后果**：TRAIT 维度证据的 `stability` 恒为 `UNKNOWN` → 按 AC-7 全部落
`single_events` → **`traits` 在真实链路下结构性为空**。

实测（本轮离线跑编排器真实运行函数，桩模型）：

```
条目统计 : background_refs=1 traits=0 habits=2 single_events=2 experiences=0 open_questions=0
  * single : 阿雾曾在一次夜雾中连续值守七十二小时 (stability=UNKNOWN)
  * single : 阿雾向来沉默寡言              (stability=UNKNOWN)
```

**判断**：
- AC-7 是**保守满足**的 —— 安全性没问题（单次行为绝不会被固化成稳定性格）；
- 但 D4 拍板的「接受 `traits` 可能全空」有一个**更硬的原因**：不是"可能"，而是
  **当前接线下的必然**。`traits` 段在真实链路上**不可填充**；
- 补 `judge_stability()` 的接线**也不足以**填满 `traits`：它按**证据条数 / 不同来源数**
  判定，单来源冒烟里同一说法的 occurrence 仍是 1 → 仍是 `SINGLE`。要拿 `PATTERN`
  必须 ≥3 个不同来源对同一说法作证。所以这是一个**需要设计决策**的议题，不是一行接线。

建议：`traits` 填充作为独立一轮（需要先定"同一说法的多次出现如何计数/如何跨来源聚拢"，
再决定 `judge_stability()` 的接入点与调用时机）。

---

## 6. 真实冒烟的边界（授权后执行，先不动）

| 项 | 值 |
| --- | --- |
| 路径 | `--run`（默认 `--path=orchestrator`） |
| 请求数 | **1 次 POST** `https://api.deepseek.com/chat/completions` |
| 重试 | `max_retries=0`、`max_calls=1`（失败不重试，无隐藏二次费用） |
| 预估费用 | **≤ 0.0033 USD**（est 3000 in / 2000 out） |
| 上限 | **0.25 USD**（已占用 0.000000） |
| 目标 | 单人物「阿雾」，合成虚构材料（AC-16：无真实人物名） |
| 结果库 | 临时隔离库（`tempfile.mkdtemp`，退出即删）+ 新建**内存**账本 |
| 落盘 | `--dump var/p45a_orchestrator.json`（原始响应 usage 一并入产物，供独立复算） |
| 会记录 | `request_id`、token、费用（含 `cost_status` / `cost_source`）、accepted / rejected / pending、逐条摘录核验（`locator` 切片）、gate 行为（告警码 + 阶段表）、档案可追溯性（引用是否命中已登记证据） |
| 不调 | 任何其它真实服务（检索侧是桩） |
| 联网面 | 仅 1 次 HTTPS POST；有断网护栏测试证明其它位置无 socket |

授权后按此执行；执行完会再跑 `verify_p4_5a_outputs.py --dump <产物>` 做**离线独立复算**
（不 import 被测模块）。

---

## 7. 本轮未做（如实列出）

- **未发真实请求**（等授权）；
- **未提交 Git**（工作区 1 处改动 + 3 个新文件，按指示等验收）；
- **未修 G6**（`stability` 接线，属设计决策，建议独立一轮）；
- 未跑双人物真实冒烟（1 次调用预算不够同时覆盖抽取与对话）。

---

## 8. 交付物（均为本机实测）

| 文件 | 变化 |
| --- | --- |
| `scripts/e2e_p4_5a.py` | 1339 → **1605** 行（+266）：`run_orchestrator_real`、`RecordingExtractor`、`build_parser`、`--path` 默认值、去掉拒绝分支、产物加 `mode` / `profile_id` |
| `tests/test_p4_5a_e2e.py` | 619 → **848** 行（+229）：37 → **45** 项 |
| `scripts/verify_p4_5a_outputs.py` | 272 行，**未改**（独立复算器，仍然不 import 被测模块） |
| `persona/orchestrator/business.py` | **本轮未动**（仍是上一轮已验收的 `+136 / −9`） |

合规扫描（改动文件）：厂商 SDK 0、硬编码本机路径 0、真实人物名 0、密钥字面量 0。
