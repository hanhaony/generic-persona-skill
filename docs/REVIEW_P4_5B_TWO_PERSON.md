# P4.5-B 双人物关系链路前置审查

> 状态：**只审查与准备，未执行任何真实 API 调用，未修改任何业务代码。**
> 本轮改动：仅新增本报告（`docs/`）。零真实请求、零费用、未装依赖、未动账本、未提交 Git。
> 文中所有"结论"都附**实际调用链**或**零成本实测输出**，不靠函数名/类名推断。

---

## 0. 结论摘要

| 问题 | 结论 |
| --- | --- |
| 双人物正式入口 | CLI **没有**；`new --names` / `chat` 是占位命令（打印"尚未实现"）。唯一入口是 `tests/` 与 `scripts/e2e_p4_5a.py` |
| `--run --path=orchestrator` 支持双人物？ | **否**。`run_orchestrator_real()` 把"阿雾"写死（`ensure_entity("阿雾")` + `E2E_RESEARCH_LIMITS`）。列为缺口 **G8**，本轮不实现 |
| 双人物是否走产品组件？ | **是**。`--dry` 的 5b 诊断与真实路径都经 `BusinessOrchestrator.run()` → 真实 `EvidenceService` / `PersonaService` / `RelationshipService` / `DialogueService` / `MemoryService` / `SqliteStorage`。**不是 dry-run 专用逻辑** |
| RELATION 是什么 | `EvidenceDimension.RELATION`（**维度**，不是 tier、不是独立 claim 类型）。候选 claim 与证据共用该枚举，`_to_evidence_draft` 原样搬运 |
| 关系证据怎么被选中 | 不用 gate 结论；按 `prior["evidence_ids"]` → `_load_evidence`（按目标实体归拢）→ `dimension == RELATION` 过滤 → 进度兜底路径再要求**覆盖 ≥2 名参与者** |
| 档案标识键 / 方向性 | 标识 = **参与者集合**（顺序无关）。**A→B 与 B→A 是同一份档案**（不区分）。方向性由档案内 `perspectives[owner]` 表达 |
| 单条证据能否成条目 | **能**。最低条件 = **1 条 RELATION 证据，其 `subject_entity_ids` 覆盖 ≥2 名本次参与者**（零成本探针实测） |
| 已登记未入档证据能否重新消费 | **不能**。且实测更糟：重跑会产生**降级新版本**覆盖好版本 → 缺口 **G7**（A 级） |
| 四种原因归因（当前单人物冒烟） | **① 阶段 SKIPPED**（参与者 <2）。非 gate 过滤、非分析失败、非写入失败 |
| 新发现缺口 | **G7**（重跑降级覆盖）、**G8**（编排器真实运行不支持双人物）、**G9**（RELATION 覆盖不足被**静默**丢弃 + 验收测试对 `var/` 的脆弱断言） |
| 离线测试 | 1169 项中 **1168 通过、1 失败**。失败项 `test_all_data_in_temp_dir_not_user_data` 的根因是**上一轮冒烟把产物写进了 `var/`**（详见 §5.2） |

---

## 1. 任务 1：双人物运行入口与调用链

### 1.1 入口清单与验证级别

| 入口 | 位置 | 双人物 | 验证级别 |
| --- | --- | --- | --- |
| `persona new --names` | `persona/cli/main.py:140-141` | 名义支持 | **占位命令**：`PLACEHOLDER_COMMANDS`（`main.py:28-34`），执行到 `main.py:617` 打印"尚未实现"并返回 `EXIT_NOT_IMPLEMENTED` |
| `persona chat` | `main.py:134, 142-145` | — | 同上，占位 |
| `persona task run` | `main.py:545-559` | 无目标概念 | 走 `MockUnitHandler`，**推进抽象工作单元**，与业务五阶段无关（代码注释自陈 D-093） |
| `persona model plan/dryrun` | `main.py:327-357` → `model_setup.build_dry_run_request` | 单材料固定样例 | 只构造请求对象，不发请求 |
| `BusinessOrchestrator.run(ctx, BusinessRequest(targets=[...]))` | `persona/orchestrator/business.py:351` | **支持**（targets 是列表） | 产品代码里**零调用方**：全仓 `BusinessOrchestrator(` 仅出现在 `tests/` 与 `scripts/e2e_p4_5a.py` |
| `scripts/e2e_p4_5a.py --dry`（5b 段） | `scripts/e2e_p4_5a.py` `diagnose_orchestrator_path(two_person=True)` | **支持** | 桩模型，零成本 |
| `scripts/e2e_p4_5a.py --run` | `run_orchestrator_real()` | **不支持** | 真实模型，单人物硬编码 |

> **结论**：双人物在**产品代码里没有正式入口**；能跑通的是「测试」与「一次性脚本」两条路。这与 P4.5-A 的 G4 定性一致 —— 属于装配层尚未接线，不是能力缺失。

### 1.2 缺口 G8：`--run --path=orchestrator` 不支持双人物

`scripts/e2e_p4_5a.py:1039-1048`：

```python
    stack = Stack(settings, ledger, out,
                  chat_provider=dialogue_stub,
                  source_provider=StubSearchExtractProvider(),   # ← 单目标桩
                  research_limits=E2E_RESEARCH_LIMITS)           # ← 单人物上限
    try:
        entity_id = stack.ensure_entity("阿雾")                   # ← 写死一个人
```

`stack.request()`（`scripts/e2e_p4_5a.py:699-703`）默认只回 `[self.target]`。

**脚本已经具备双人物所需的全部零件**（只是没接上）：

- `Stack.ensure_targets(names)`（`e2e_p4_5a.py:669-678`）—— 登记多个目标
- `Stack.request(targets=[...])` —— 传目标列表
- `StubTwoTargetSourceProvider`（`e2e_p4_5a.py:477`）—— 按目标名分发页面的桩数据源
- `E2E_RESEARCH_LIMITS_TWO`（`e2e_p4_5a.py:196-198`）—— `max_queries=2, max_extracts=2`
- `E2E_PASSAGES_2` / `E2E_URL_2` / `E2E_TARGET_2` —— 第二个人物的合成材料
- `orchestrator_payload(name_to_id)`（`e2e_p4_5a.py:851-883`）—— 双主体载荷构造器

**按本轮约束，不擅自实现。** 下一轮接线只需把 `run_orchestrator_real` 的 3 处换成按参数选（详见 §3.2）。

### 1.3 双人物运行确实调用产品组件

`--dry` 的 5b 诊断（`diagnose_orchestrator_path(two_person=True)`，`e2e_p4_5a.py:785-843`）用的是**同一个** `BusinessOrchestrator`：

```
Stack.__init__（e2e_p4_5a.py:625-641）
  ├─ EvidenceService(self.storage)
  ├─ PersonaService(self.storage)
  ├─ RelationshipService(self.storage)
  ├─ DialogueService(DialogueDeps(profiles, relations, evidence,
  │                              memory=MemoryService(storage), storage, provider,
  │                              memory_reader=MemoryService(storage)))
  ├─ TaskService(self.storage)
  └─ ResearchService(ResearchDeps(provider, evidence, ...))

Stack.orchestrator()（e2e_p4_5a.py:692-697）
  └─ BusinessOrchestrator(BusinessDeps(research, evidence, persona,
                                       relationship, dialogue, task_service,
                                       storage, claim_extractor, relationship_analyzer))
```

调用的是 `orch.run(...)` → `TaskRunner` → `BusinessOrchestrator.plan/execute` → `_dispatch`（`business.py:458-473`）→ 五个阶段方法。**没有旁路实现，没有看 `dry_run` 标志分叉**（唯一读 `ctx.dry_run` 的地方是存储层 `has_external_usage`，`storage/sqlite.py:530`）。

`--dry` 与真实运行的差别**只在 Provider 一侧**，见 §1.5。

### 1.4 逐环节：文件 / 入口 / 输入输出 / 验证级别

| # | 环节 | 文件 | 入口函数 | 输入 → 输出 | 验证级别 |
| --- | --- | --- | --- | --- | --- |
| ① | Research Material | `persona/modules/research.py` | `ResearchService.run` → `plan_queries`（:750）/`collect`（:814） | `targets` → `ResearchOutput{materials(摘要), extracted(正文), source_ids, warnings}` | 真实 Tavily 已验证（P4.2）；**编排器内接桩数据源** |
| ①b | 材料交接（G4 修复） | `persona/orchestrator/business.py` | `_material_flow`（:850） | `research` → `list[SourceMaterial]`（带 `quality_status` + 绑好的 `source_id`） | 已验证（G4/G5 轮）；`--dry` 实测"材料流 1 份（门禁 1 / 绑来源 1）" |
| ② | Claim Extraction | `persona/modules/extraction.py` | `ProviderClaimExtractor.extract`（:621 起） | `materials, targets` → `ExtractionOutcome{claims, rejected, pending, warnings, usage}` | **真实模型已验证（单人物，目标数=1）**；双人物**未验证** |
| ③ | Evidence Registration | `persona/orchestrator/business.py` | `_register_candidates`（:617） | `outcome` → `evidence_ids` | 桩模型 + 单测（`test_business_orchestrator`）+ 真实单人物已验证 |
| ④a | 材料门禁 | `research.admit_materials` / `assess_content_quality` | `admit_materials`（`extraction.py:615` 调用） | `materials` → 准入/拒收（`MATERIAL_NOT_GATE_PASSED` / `MATERIAL_UNDECLARED`） | A11 负样例实测（2 类，0 次模型调用） |
| ④b | 证据门禁 | `evidence.add_evidence`（:778 起） | 同上 | `EvidenceDraft` → 证据（EV-V1~V5） | 单测已验证 |
| ④c | 人格稳定性门禁（AC-7） | `business._build_persona_draft`（:1043） | 同上 | 证据 → `traits` / `single_events` | 已实测（`TRAIT + SINGLE` 落 `single_events`） |
| ⑤ | Persona Distill | `business.py` | `_step_persona`（:981）→ `_build_persona_draft`（:1043）→ `_write_persona`（:1146） | `evidence` → `persona_profile_ids` | 真实模型单人物已验证 |
| ⑥ | **Relationship** | `business.py` | `_step_relationship`（:1173）→ `_relationship_draft_fallback`（:1251）/ `_draft_from_analysis`（:1300）→ `_write_relationship`（:1444） | `RELATION` 证据 → `relationship_profile_ids` | **仅桩/单测验证（`--dry` 5b 桩模型 + `test_business_orchestrator` 双人例）**；**真实模型未验证** |
| ⑦ | Dialogue | `business.py` | `_step_dialogue`（:1467） | 档案 → `session_id` / `turn_ids` | 桩 Provider；双人物 `--dry` 已跑（`EXECUTED`） |
| — | Memory / Storage | `persona/modules/memory.py` / `persona/storage/sqlite.py` | `MemoryService` / `SqliteStorage` | — | 单测已验证 |

### 1.5 `--dry` 与真实运行的差异（哪些被桩替代）

| 组件 | `--dry` | `--run --path=orchestrator`（现状） |
| --- | --- | --- |
| 数据源（搜索 / 抓取） | `StubSearchExtractProvider` / `StubTwoTargetSourceProvider` | **同样是桩**（方案 B 既定：合成人物公网不存在） |
| 抽取模型 | `StubChatProvider`（回放固定载荷，`chat_calls` 计数） | **真实 DeepSeek**（`chat_provider_from_settings`），`CountingChatProvider` 计数 |
| 对话模型 | `StubDialogueProvider` | **同样是桩**（单人物下对话阶段 `SKIPPED`，断言 `chat_calls == 0`） |
| 抽取器类 | `ProviderClaimExtractor`（**真实类**） | 同 |
| 编排器 / 五阶段 / 门禁 / 存储 | **全部产品代码** | 同 |
| 网络 | 零请求 | **1 次 POST** |
| 账本 | `ExternalUsageLedger()`（内存，见 §4） | 同 |

`--dry` 会额外把两条路径各跑一遍（5a 单人物、5b 双人物），并逐条断言；`--run` 只跑 `--path` 指定的那条。

---

## 2. 任务 2：RELATION 证据 → 关系档案 完整路径

### 2.1 RELATION 是 Claim 类型、Evidence 类型，还是映射？

**都不是"类型"，而是共用的一套维度值。**

- `persona/core/enums.py:148-157`：`class EvidenceDimension`，含 `BACKGROUND / TRAIT / HABIT / SPEECH_STYLE / EXPERIENCE / RELATION / CANON_FACT`。
- 抽取器 `_DIMENSION_VALUES = tuple(str(v) for v in EvidenceDimension)`（`extraction.py:142`），并把它写进 JSON Schema 的 `enum`（`extraction.py:514`）；非法值 → `REJECT_UNKNOWN_DIMENSION`（`extraction.py:363-364`）。
- 候选 claim 的字段是 `dimension: EvidenceDimension | str`（`extraction.py:162`）。
- 编排器 `_to_evidence_draft`（`business.py:765-782`）**原样搬运** `dimension=candidate.dimension`，不提升、不改写。
- **没有** `ClaimType` / `ClaimDimension` 枚举；claim 的"类型"是 `EvidenceTier`（VERBATIM / PROFILE_BACKGROUND / THIRD_PARTY / INFERENCE，见 `enums.py:52`）。

⇒ `RELATION` 是一条**维度**，从候选 claim 到证据**恒等映射**（tier 与 dimension 是两条正交的轴）。

### 2.2 实际调用链（含行号）

```
[② 抽取]  ProviderClaimExtractor.extract                     extraction.py:621
            └─ 逐条校验（含 dimension 白名单）               extraction.py:363
            └─ 产出 CandidateClaim(dimension=RELATION, subject_entity_ids=[...])
                        │
[③ 登记]  BusinessOrchestrator._step_evidence                business.py:519
            └─ _register_candidates(ctx, outcome, source_ids) business.py:617
                 ├─ ①非推断：_to_evidence_draft → _find_evidence 去重
                 │            → evidence.add_evidence          business.py:680-696
                 │            （EV-V1 命名空间 → EV-V2 VERBATIM 必带摘录
                 │              → EV-V4 必带 source_id）        evidence.py:778-840
                 └─ ②推断：record_inference（须有根）           business.py:701-737
            ← BusinessStepResult.artifact_ids["evidence_ids"]
                        │  写进检查点
                        ▼
[④ 关系]  _step_relationship(ctx, request, prior)            business.py:1173
   ④.0  participants = [t.entity_id for t in request.targets]  business.py:1184
         if len(participants) < 2 → STEP_SKIPPED                business.py:1185-1188   ← 单人物止步于此
   ④.1  evidence_ids = prior["evidence_ids"]                    business.py:1190
   ④.2  _load_evidence(ctx, request, evidence_ids)              business.py:1191 / 1022
         └─ evidence.query_evidence(filters={"entity_ids": targets})  business.py:1029-1031
            → 按 item.subject_entity_ids 归拢成 {entity_id: [items]} business.py:1035-1039
   ④.3  for entity in participants: for item in by_entity[entity]:
            if item.dimension != RELATION: continue              business.py:1202
            if item.id in seen: continue（同一证据只收一次）      business.py:1200-1201
   ④.4  cardinality = GROUP if len>=3 else PAIR                  business.py:1207
   ④.5  analyzer 注入？ → _draft_from_analysis(:1300)            business.py:1217-1227
        否则            → _relationship_draft_fallback(:1251)     business.py:1230
   ④.6  _write_relationship(ctx, participants, draft)            business.py:1233 / 1444
         ├─ get_relationship(participants) 命中 → revise(...)      business.py:1448-1455
         └─ 未命中 → create_relationship(...)                      business.py:1456-1463
                        │
[⑤ 落盘]  RelationshipService._validate_draft（RL-V1~V6 + D-020） relationship.py:779
            └─ _write_relationship(ctx, draft, version, supersede_id) relationship.py:705
```

### 2.3 Gate 批准后的关系证据如何被选中？

**关系阶段不读 gate 结论。** 门禁只作用于"材料能否进抽取器"（`admit_materials`）与"证据能否登记"（`add_evidence` 的 EV-V1~V5）。证据一旦登记，关系阶段的选取规则只有三条：

1. **来源**：`prior["evidence_ids"]`（本次任务的检查点产出，`business.py:1190`）；
2. **可及性**：`_load_evidence` 用 `filters={"entity_ids": [目标实体]}` 查，并按 `subject_entity_ids` 归拢 —— 证据必须**挂在某个参与者身上**才可能被看到（`business.py:1029-1039`）；
3. **维度**：`dimension == RELATION`（`business.py:1202`）。

**兜底路径额外一条**（`business.py:1261-1270`）：

```python
        for item in relation_items:
            covered = [p for p in participants
                       if p in set(_str_list(item.subject_entity_ids))]
            if len(covered) < 2:
                continue                                   # ← 静默丢弃
            shared.append(SharedExperienceItem(...))
```

即：**RELATION 证据的 `subject_entity_ids` 必须覆盖 ≥2 名本次参与者**，否则不进 `shared_experiences`。

> 注入 `relationship_analyzer` 时走另一条路（`business.py:1217-1227` → `_draft_from_analysis`），`relation_items` 非空即交给分析器研判；`e2e_p4_5a.py` 未注入分析器，走兜底路径。

### 2.4 关系档案的标识键与方向性

- **标识键 = 参与者集合**。`RelationshipService._relationships_of`（`relationship.py:1154-1162`）按**集合相等**匹配：

  ```python
        return Ok([r for r in loaded.value
                   if set(_str_list(r.get("participant_entity_ids")))
                   == set(participant_set)])
  ```

  `revise` 的 docstring 明确写「参与者集合按**集合**匹配：传入顺序不影响身份」（`relationship.py:506`）。

- **因此 A→B 与 B→A 是同一份档案**，系统**不为方向建两份**。方向性表达在档案**内部**：
  `perspectives: dict[owner_entity_id, PerspectiveBlock]`（`relationship.py:299` 起），块内字段为 `view_of_targets` / `feeling_toward` / `expression_toward`（`relationship.py:101-102`）。
  RL-V1 强制 `set(perspectives) == set(participants)`（`relationship.py:851-859`），且 `view_of_targets` 只能指向**非自身的参与者**（`relationship.py:896-901`）。
- 版本化：同集合下 `create` 只允许一次（`relationship.py:478-489`），后续必须 `revise` 生成新版本并给旧版打 `superseded_by`（`relationship.py:498-539`）。
- 反合并：同一角色的不同改编版本不得同时作为参与者（`relationship.py:818-831`）。

### 2.5 只有一条关系证据，能否形成档案条目？

**能。** 最低证据条件 = **1 条 `dimension=RELATION` 的证据，其 `subject_entity_ids` 覆盖 ≥2 名本次参与者**。

**零成本实测（桩 Provider，探针 B）**：

```
关系阶段: EXECUTED | 关系档案 PAIR（2 人，1 条 RELATION 证据支撑；语义分析=False)
  档案 status      : OK
  shared_experiences: 1
     · 阿雾与阿灯共同守护雾港的灯塔与灯会 | refs = ['evd_d6a8...'] |
       participants = [pe_9477..., pe_214d...] | stability = UNKNOWN
  open_questions   : []
```

并**确认不需要**摘录：另一次探针用 `tier=PROFILE_BACKGROUND`（归纳型、无 `verbatim_quote`）的 RELATION 证据挂双主体，同样登记成功并进入 `shared_experiences`。
（`add_evidence` 只在 `tier == VERBATIM` 时强制 `verbatim_quote`，`evidence.py:830-833`。）

**若一条都没有**（或全部只覆盖 1 人）：档案**仍会写**，但状态为 `INSUFFICIENT_EVIDENCE` 且必须带 `open_questions`（D-020 反白卷，`relationship.py:963-969`）：

```
档案 status      : INSUFFICIENT_EVIDENCE
shared_experiences: 0
open_questions   : ['互动模式待补']
warning          : NO_RELATION_EVIDENCE
```

### 2.6 已登记但未进入档案的证据：能否重新消费？重复风险？

**结论：不能重新消费；而且实测会产生"降级覆盖"（G7）。**

三件事分开说：

**(a) 同一任务重跑（同 `idempotency_key`）** —— `BusinessOrchestrator.run`（`business.py:365-377`）用 `create_task` 拿回既有任务；`is_terminal(record.status)` 为真时**直接 `_summarize` 只读返回**，`_steps = {}`，不执行任何阶段，也不重新消费证据。

**(b) 新任务、同请求（无 `idempotency_key`）** —— 整链重跑。但抽取器的**发送层幂等守卫**（`extraction.py:720-731` 的 `_call_key` = provider + OP_CHAT + model + 材料键 + 目标 ID + `PROMPT_VERSION`）会命中 → `MODEL_CALL_DUPLICATE_SKIPPED` → **本次无候选** → `_register_candidates` 落入 `if not evidence_ids:` 分支（`business.py:748-757`）→ `STEP_SKIPPED` + `NO_CANDIDATES`。**已登记的证据不会被回捞**（`prior` 读的是**新任务**的检查点）。

**(c) 由此产生的写风险 —— 实测（探针 3，桩 Provider，零成本）**：

```
--- 第一次（新任务） ---
  状态: SUCCEEDED | 证据数: 3
  关系档案 rlp_7e81... v1 status=OK shared=1 superseded_by=None

--- 第二次（新任务、同请求） ---
  状态: PARTIAL | 证据数: 0
  关系阶段: EXECUTED | 关系档案 PAIR（2 人，0 条 RELATION 证据支撑）
    warning: NO_RELATION_EVIDENCE
  关系历史版本: [(2, 'INSUFFICIENT_EVIDENCE', 0, None),
                (1, 'OK', 1, 'rlp_6996...')]        ← v1 被空 v2 取代
  人物档案 阿雾: v2 status=INSUFFICIENT_EVIDENCE items=0
    人物历史版本 阿雾: [(1, 'OK', 1, 'pnf_9232...'), (2, 'INSUFFICIENT_EVIDENCE', 0, None)]
  人物档案 阿灯: 同样被降级
```

**逐项回答风险问题**：

| 风险 | 是否发生 | 依据 |
| --- | --- | --- |
| 重复**登记**同一证据 | **否** | `_find_evidence`（同 source_id + 同 claim + 同主体）→ 复用原记录并报 `EVIDENCE_DEDUP_HIT`（`business.py:681-691`）；`record_inference` 另有 `_find_derived`（:784-805） |
| 重复**花费** | **否** | 发送层幂等守卫在构造请求前返回（`extraction.py:635-640`）；同一进程内 `chat_calls` 不增加（实测 1 → 1） |
| 重复**蒸馏 / 写入** | **是（新版本）** | `_write_persona`（:1146）/ `_write_relationship`（:1444）在内容**不一致**时必定 `revise`。空内容 ≠ 旧内容 → 生成 v2。**没有任何"新版证据支撑更弱则拒绝降级"的守卫** |
| 旧好版本被归档 | **是** | `revise` 给旧版打 `superseded_by`（`relationship.py:537-539`），**读接口默认取最高 version**（`relationship.py:580`）→ 默认读到的是空 v2 |

⇒ **G7 是真实缺口**：重跑不是"无副作用"，而是**把已验收的档案降级覆盖**。触发条件在真实配置下更宽 —— 只要 `usage_sink` 被接上（跨进程持久账本），或同一进程内重复执行，守卫就会命中。**当前产品代码未接 `usage_sink`**（全仓仅 `tests/` 使用，见 §4.3），所以现在只在"同进程重复执行"时发生。

### 2.7 四种原因的判定（当前属于哪一种）

对**上一轮单人物真实冒烟**里那条 `RELATION` 证据（`c6`「阿雾与星灯会存在同伴关系」，`subject_entity_ids` 只有 阿雾）：

| 候选原因 | 判定 | 证据 |
| --- | --- | --- |
| ① **阶段 SKIPPED** | ✅ **就是这一种** | `_step_relationship` 在 `len(participants) < 2` 时直接 `STEP_SKIPPED`（`business.py:1184-1188`）；冒烟实测 `阶段 RELATIONSHIP_DISTILL → SKIPPED` |
| ② 证据被 gate 过滤 | ❌ 否 | 冒烟 `accepted 6 / rejected 0 / pending 0`，`RELATION` 证据**已登记**（`evidence 6` 条） |
| ③ 分析失败 | ❌ 否 | 未注入 `relationship_analyzer`（走兜底）；阶段未报 Err |
| ④ 写入失败 | ❌ 否 | 该轮根本没有档案写入动作（阶段被跳过） |

**补充（当前不属于、但下一轮会遇到的第 5 种）**：若把它放进**双人物**请求，原因会变成
**⑤ 证据未覆盖 ≥2 名参与者 → 兜底路径 `continue` 静默丢弃**（`business.py:1261-1265`）。
实测（探针 A，桩 Provider）：

```
场景 A：RELATION 只挂单主体（阿雾）   → 关系阶段 EXECUTED
  档案 status = INSUFFICIENT_EVIDENCE, shared_experiences = 0
  warning: NO_RELATION_EVIDENCE
  已登记证据里那条 RELATION 确实在库：evd_d268... | RELATION | ['pe_2931...']
场景 B：RELATION 挂双主体（阿雾+阿灯）→ 档案 status = OK, shared_experiences = 1
```

### 2.8 是否代码缺口？

| 现象 | 是否缺口 | 说明 |
| --- | --- | --- |
| 单人物 → 关系阶段 `SKIPPED` | **不是**。设计如此 | 关系至少需要两个参与者（`relationship.py:792-797` 亦强制 ≥2） |
| RELATION 只覆盖 1 人 → 不进 `shared_experiences` | **判定规则本身不是缺口**；**静默**是缺口 → **G9** | 一条只点名一个人的关系证据无法确立两人关系，丢弃是对的；但 `continue` 不留逐条痕迹，只留下笼统的 `NO_RELATION_EVIDENCE`（与 G5 同类问题：拒收原因不可筛选） |
| 重跑降级覆盖好版本 | **是缺口 → G7** | 无"新版证据支撑更弱则拒绝"的守卫，且旧版被 `superseded_by`，读接口默认取新版 |
| 编排器真实运行不支持双人物 | **是缺口 → G8** | 装配层未接线，非能力问题 |

---

## 3. 任务 3：最小双人物真实 E2E 方案（**只提供，本轮不执行**）

### 3.1 材料设计（合成虚构，AC-16）

沿用既有 阿雾 / 阿灯 / 雾港 / 星灯 体系，**追加一份互动页**（关键：现在的两份材料里**没有任何一句同时点名两人**，模型因此**无法**产出双主体的 VERBATIM 关系证据）：

| # | URL | 内容 | 句数 / 约束 |
| --- | --- | --- | --- |
| P1 | `https://example.invalid/mist-harbor-lighthouse-notes`（已有 `E2E_URL`） | 阿雾 5 句 | 每段 ≥40 字、`prose_chars ≥ 200`（门禁下限） |
| P2 | `https://example.invalid/starlamp-festival-notes`（已有 `E2E_URL_2`） | 阿灯 5 句 | 同上 |
| **P3（新增）** | `https://example.invalid/mist-harbor-two-keepers` | **互动页**：5 句同时点名两人，含 **① 一句对话**（「……」）、**② 一件共同事件**（如：两人在风暴夜合力护住灯架，阿雾掌灯、阿灯护架） | 同上；且**至少一句可被逐字引用**，让模型有依据产出双主体 VERBATIM 关系候选 |

P3 的取回方式（两种，择一，都只需改桩数据源，不动产品代码）：

- **(推荐)** 让 阿雾 的那次检索返回 **2 条结果**（P1 + P3），`max_results_per_query=2`、`max_extracts=3`；
- 或给请求一个 `topic`，让 `plan_queries` 多产一条话题查询（`research.py:799-805`），桩按话题返回 P3。

> 不要求模型一定产出关系结论。**"档案为 `INSUFFICIENT_EVIDENCE` + `open_questions`" 是正确结果**，只要能从事后数据说明原因（§3.7）。

### 3.2 前置改动（下一轮，仅 `scripts/`，≤25 行，**不碰产品代码**）

在 `scripts/e2e_p4_5a.py` 加一个 `--names` 参数并接进 `run_orchestrator_real`：

1. `build_parser()` 增 `--names`（默认 `"阿雾"`，逗号分隔）；
2. `run_orchestrator_real` 增 `names` 形参，替换 3 处：
   - `source_provider=StubSearchExtractProvider()` → `StubTwoTargetSourceProvider()`（多目标时）
   - `research_limits=E2E_RESEARCH_LIMITS` → `E2E_RESEARCH_LIMITS_TWO`
   - `stack.ensure_entity("阿雾")` → `stack.ensure_targets(names)`，`stack.request()` → `stack.request(targets=target_list)`
3. 桩数据源 `_CATALOG` 增 P3，并让 阿雾 查询返回 2 条。
4. 发送前请求体核验里，把"目标 ID 白名单"从 1 个改为 2 个一并断言。

> 本轮**不动手**，等确认。

### 3.3 可执行命令

**（A）前置检查（零成本，必跑）**

```bash
# A1 预检 + 双人物 dry（桩模型，零请求）
python scripts/e2e_p4_5a.py --dry --path=orchestrator --names 阿雾,阿灯

# A2 A11 门禁负样例（桩模型，零调用）
python scripts/e2e_p4_5a.py --a11-only

# A3 离线回归
python -m unittest tests.test_p4_5a_e2e tests.test_business_orchestrator tests.test_relationship_module
```

**（B）唯一一次真实运行（会产生费用）**

```bash
python scripts/e2e_p4_5a.py --run --path=orchestrator --names 阿雾,阿灯 \
    --dump var/p45b_last_run.json
```

> ⚠️ `--dump var/...` 会创建仓库根下的 `var/`，而 `tests/test_integration_acceptance.py:639` 断言
> `assertFalse((REPO_ROOT / "var").exists())` —— 现有 `var/` 已经让该测试失败（§5.2）。
> 若要避免再踩，把 dump 指到仓库外，例如 `--dump "$TEMP/p45b_last_run.json"`（`$TEMP` 为系统临时目录）。

**（C）今日即可用的等价命令（若下一轮暂不做 §3.2 的接线）**

复用脚本内现有零件，零新增文件（**已验证该形态可跑通，离线、桩模型**）：

```bash
python - <<'PY'
import json, sys
from pathlib import Path
REPO = Path(r"D:\AIprojects\generic-persona-skill")
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
from persona.core.result import unwrap
from persona.core.usage_ledger import ExternalUsageLedger
from persona.config import load_settings
import e2e_p4_5a as runner

settings = unwrap(load_settings(overrides=runner.E2E_CONFIG))   # 读 .env 的真实密钥
out = runner.Out(runner.E2E_CONFIG.get("DEEPSEEK_API_KEY", ""))
ledger = ExternalUsageLedger()
stack = runner.Stack(settings, ledger, out,
                     chat_provider=runner.StubDialogueProvider(),
                     source_provider=runner.StubTwoTargetSourceProvider(),
                     research_limits=runner.E2E_RESEARCH_LIMITS_TWO)
try:
    targets = stack.ensure_targets(["阿雾", "阿灯"])
    stack.use_real_extractor(runner.CountingChatProvider(
        unwrap(runner.chat_provider_from_settings(settings))))
    run = unwrap(stack.orchestrator().run(stack.ctx, stack.request(targets=targets)))
    print(run.status, len(run.evidence_ids), run.relationship_profile_ids)
finally:
    stack.teardown()
PY
```

> 形态验证输出（**桩模型**，零成本）：`形态验证 OK： SUCCEEDED | 证据 10 | 关系档案 1 | 调用 1`。
> 注意：该形态**没有**发送前请求体核验、没有幂等重跑、没有独立复算器 —— 因此**推荐走 (A)+(B)**。

### 3.4 预算与预留

| 项 | 值 | 来源 |
| --- | --- | --- |
| 调用次数上限 | `max_calls = 1` | `E2E_CONFIG["PERSONA_MODEL_MAX_CALLS"]` |
| 重试 | `max_retries = 0`（强制覆盖） | `model_setup.py` 第 2 条边界 |
| 单价（peak） | in `0.0003` / out `0.0012` USD per 1K | `E2E_CONFIG` |
| 最坏估算 | `3000 × 0.0003 + 2000 × 0.0012` = **0.0033 USD** | `estimate_model_cost(limits)` |
| 单任务上限 | **0.25 USD** | `PERSONA_MODEL_MAX_COST_PER_TASK` |
| 总上限 | **0.25 USD**（与单任务同值；两层独立计数**未实现**） | `P4.4-C` 拍板 |
| **发送前预留** | `ledger.reserve(key, estimate)` | `extraction.py:797`（四段式：检查 → 预留 → 执行 → 结算） |
| 超支拦截 | `ledger.would_exceed(max_cost, estimate)` → `Err(BUDGET_EXCEEDED)`（可恢复暂停） | `extraction.py:784-798` |

**"先检查历史支出与预留，不得重置账本"**：

- 账本是 `ExternalUsageLedger()`，**进程内内存对象**，`e2e_p4_5a.py` 每次运行新建 → 新进程天然 `occupied = 0`。
- 仓库内**没有任何数据库文件**（`find . -name "*.db"` → 空），因此**没有可被重置的持久账本**；本轮也**不会**创建/清空任何既有文件。
- 可执行的检查（零成本）：

```bash
python -c "from persona.core.usage_ledger import ExternalUsageLedger as L; l=L(); print('occupied', l.occupied_cost(), '| calls', len(l.records()))"
# 期望：occupied 0.0 | calls 0
ls var/*.db 2>/dev/null || echo "无持久账本/用户库（符合预期）"
```

- **不得重置**的落地做法：本方案只**新建**临时库（`Stack` 的 `tempfile.mkdtemp`），**不**对 `var/`、`data/` 或任何既有路径执行删除/清空。

### 3.5 预期结果

| 项 | 期望 | 判定方式 |
| --- | --- | --- |
| 真实 POST 次数 | **恰好 1** | `recorder.calls == 1`（脚本内断言） |
| 费用 | ≤ 0.0033 USD，远低于 0.25 | 账本 `cost_confirmed` + 独立复算器 A5 |
| accepted / rejected / pending | 逐条列出 | 产物 `extraction.claims` / `.rejected` / `.pending` |
| 门禁行为 | 正文过门禁 → `VALID`；摘录按 locator 命中 | 产物 `materials[].quality_status`、A4 复算 |
| 关系阶段 | `EXECUTED`（参与者 ≥2 必达） | `run.steps` |
| 关系档案 | **两种都算通过**：① `OK` + `shared_experiences ≥1`（模型产出了双主体 RELATION 证据）；② `INSUFFICIENT_EVIDENCE` + `open_questions`（模型没产出） | 产物档案条目 + 告警码 |
| PENDING | **不得**进入任何档案 | `pending` 非空时，档案条目应只引用 `evidence_ids` 内的证据 |
| REJECTED | **不得**进入档案，但**保留审计** | 产物 `extraction.rejected` 含 reason/message；档案不引用它们 |
| 档案可追溯性 | 每条 `evidence_refs` ∈ 本次 `evidence_ids` | 独立复算器 A7 |
| 整体状态 | `SUCCEEDED`（五阶段全 `EXECUTED`） | `run.status` |
| 幂等重跑 | **0 次新增调用** | 同进程再 `orch.run` → `calls` 不变 |

### 3.6 停止条件

| # | 条件 | 动作 |
| --- | --- | --- |
| S1 | `--dry` 任一预检断言失败 | **停**，不发请求 |
| S2 | 账本 `occupied ≠ 0` 或发现既有 `.db` 被本方案触碰 | **停**，人工确认 |
| S3 | `max_calls ≠ 1` / `max_retries ≠ 0` / 估算 ≠ 0.0033 / 单价未解析 | **停**（费用无法保证） |
| S4 | 运行后 `recorder.calls ≠ 1` | **停**，如实报告，不重试 |
| S5 | 抽取器返回 `Err(BUDGET_EXCEEDED)` | 视为**可恢复暂停**，不自动重试 |
| S6 | 幂等重跑出现**新增**真实调用 | **停**，立即人工复核（授权只覆盖 1 次） |
| S7 | 出现任何非 DeepSeek 的外部调用（检索等） | **停**（本方案数据源为桩） |
| S8 | 产物/输出中出现密钥形态字符串 | **停**，删除产物，报告 |

### 3.7 验收判据（"无档案条目也是正确结果"如何判定）

必须能**从数据说明**属于下列哪一种，而不是只看状态：

| 观测 | 结论 |
| --- | --- |
| 档案 `OK` 且 `shared_experiences ≥1`，每条 ref 指向本次 RELATION 证据 | 关系链路**完成端到端** |
| 档案 `INSUFFICIENT_EVIDENCE` + `open_questions` + 告警 `NO_RELATION_EVIDENCE`，且 `extraction.claims` 里**确实没有任何** `dimension=RELATION` 且 `subject_entity_ids` 覆盖两人的候选 | 模型未产出可用关系结论 → **正确结果**（不是缺陷） |
| 档案 `INSUFFICIENT_EVIDENCE`，但 `claims` 里**存在**双主体 RELATION 候选且它落进了 `rejected`/`pending` | 归因到具体拒收码 → 需要看是 EV-V2 摘录核验还是别的门禁 |
| 档案 `INSUFFICIENT_EVIDENCE`，但存在双主体 RELATION **已登记**证据 | 命中 **G9**（覆盖判定或维度过滤）→ 需要排查 |

---

## 4. 任务 4：隔离与回滚

### 4.1 现有隔离机制

| 机制 | 位置 | 说明 |
| --- | --- | --- |
| 临时库（脚本） | `e2e_p4_5a.py:613-615` `tempfile.mkdtemp(prefix="gps-p45a-")` → `p45a.db` | 退出即 `shutil.rmtree`（`teardown()`，:705-709） |
| 临时库（测试） | `tests/test_business_orchestrator.py:321` 等，`mkdtemp(prefix="gps-*")` | 每个 fixture 独立 |
| 默认数据目录 | `config/spec.py:273` `data_dir = "var"` → `var/persona.db` | `.gitignore:45` 忽略 `var/`；仓库内**当前无 `.db`** |
| 命名空间闸门 | `EV-V1`（`evidence.py:785-788`） | 模拟内容永不进证据表 |
| dry-run 标志 | `storage/sqlite.py:530` | `ctx.dry_run` 时不查持久用量 |

### 4.2 数据面现状（已核实）

```
find . -name "*.db" -not -path "./.git/*"   →  （空）
ls var/                                      →  p45a_last_run.json / p45a_last_run.raw.json
```

⇒ **没有用户数据库可被误伤**；`var/` 里只有上一轮冒烟的产物（JSON，非数据库）。

### 4.3 账本持久化现状（与预算话题相关）

`usage_sink` 在**产品代码里没有任何接线点**（`grep -rn usage_sink persona/` → 只有 `extraction.py` 的类型声明/使用与 `model_setup.py` 的透传参数；实际传入仅出现在 `tests/`）。
`SqliteStorage.has_external_usage`（`storage/sqlite.py:519`）已实现但**当前无人调用**。

⇒ 跨进程的"重复意图守卫"与"逐笔用量持久化"**在当前配置下不生效**；这正是"两层上限机制未实现"的同源现象。

### 4.4 清理方式（不触碰用户数据）

| 产物 | 清理方式 | 是否影响用户数据 |
| --- | --- | --- |
| 临时库 `gps-p45a-*` | `teardown()` 自动 `rmtree`；异常退出时手动删 `%TEMP%/gps-p45a-*` | 否 |
| `--dump` 产物 | 手动删除该文件；**建议 dump 到系统临时目录**（`$TEMP`）避免创建仓库内 `var/` | 否 |
| 任何既有 `var/persona.db` | **本轮不动**；方案也不创建它 | — |

**明确不做**：不 `git clean`、不清空/重置/覆盖任何数据库、不删除 `var/`（其中是上一轮验收证据）、不修改 `.env`。

---

## 5. 任务 5：测试结果与未解决问题

### 5.1 离线测试与诊断（全部零真实调用）

| 项 | 结果 |
| --- | --- |
| `tests.test_p4_5a_e2e` | **47 项全绿** |
| 批次 1（编排器 / 研究 / 证据 / 人格 / 抽取） | `Ran 353 tests … OK` |
| 批次 2（P4.4A/B/C / P4.5 / 分层铁律 / 关系模块） | `Ran 515 tests … OK` |
| 批次 3（CLI / config / 存储 / 任务 / 集成验收等） | `Ran 301 tests … **FAILED (failures=1)**` |
| **合计** | **1169 项，1 失败**（详见 §5.2） |
| `--a11-only` | 2 类负样例全 `[OK]`，`MATERIAL_NOT_GATE_PASSED` / `MATERIAL_UNDECLARED`，**模型调用 0 次、候选 0 条** |
| `--dry --path=orchestrator` | 退出码 **0**，预检全通过，**零真实请求** |

`--dry` 的双人物诊断（5b，桩模型）实测：

```
整体状态        : SUCCEEDED（五阶段全执行）
任务终态        : COMPLETED
证据数          : 10
桩模型调用次数   : 1
材料流          : 2 份（带门禁结论 2，已绑定来源 2）
   阶段 RESEARCH             → EXECUTED
   阶段 EVIDENCE_EXTRACT     → EXECUTED
   阶段 PERSONA_DISTILL      → EXECUTED
   阶段 RELATIONSHIP_DISTILL → EXECUTED
   阶段 DIALOGUE_RUN         → EXECUTED
告警码          : ['MATERIAL_SUPERSEDED', 'NO_EVENTS', 'NO_RELATION_EVIDENCE']
证据阶段说明     : 从 10 个候选中登记 10 条证据，拒收 0 条
```

**注**：5b 的 `RELATIONSHIP_DISTILL → EXECUTED` 但带 `NO_RELATION_EVIDENCE` —— 因为
`orchestrator_payload()`（`e2e_p4_5a.py:851-883`）的固定载荷**只含 BACKGROUND/HABIT/TRAIT**，
**没有任何 RELATION 候选**。这解释了"为何双人物 dry-run 里也看不到关系条目"，
同时说明该 5b 诊断**没有覆盖**关系条目的产出路径 —— 这是 §3.1 要新增互动页的直接原因。

### 5.2 唯一的失败项及其根因（**须人工决策**）

```
FAIL: test_all_data_in_temp_dir_not_user_data
      (tests.test_integration_acceptance.TestIsolatedDataAndOffline)
AssertionError: True is not false
```

断言（`tests/test_integration_acceptance.py:639`）：

```python
        self.assertFalse((REPO_ROOT / "var").exists())
```

**根因**：`var/` 现在存在 —— 里面是**上一轮我执行真实冒烟时用 `--dump var/p45a_last_run.json` 写下的产物**
（`p45a_last_run.json` 10082 B、`p45a_last_run.raw.json` 4560 B）。
上一轮的 1169 项全绿是在**冒烟之前**跑的，所以当时 `var/` 尚不存在。

**性质**：不是本次审查引入的缺陷，是**上一轮冒烟产物的副作用**；同时暴露该断言**过于脆弱** —— 任何合法的 `--dump var/...` 都会让它失败。

**本轮处理**：**不动**（`var/` 是上一轮验收证据；且本轮约束为只审查、禁止破坏性操作）。
**建议（下一轮，供你择一）**：
- (a) 把断言改为"本测试不得创建 `var/`"（检查测试前后差异，而非绝对不存在）；
- (b) 把冒烟产物移出仓库（`--dump "$TEMP/..."`），并删除现有 `var/`；
- (c) 保留现状，接受该项长期红。

### 5.3 未解决问题（缺口清单）

| ID | 级别 | 描述 | 本轮是否处理 |
| --- | --- | --- | --- |
| **G6** | 中 | `stability` 链路断在中间：抽取器**刻意不解析**模型自报 `stability`（一律 `UNKNOWN`）；`Evidence.judge_stability()` 生产代码零调用点 → `traits` 结构性为空。本轮**新证据**：`shared_experiences[0].stability = UNKNOWN` —— 同一问题也作用于关系档案 | 否（已立为下一轮） |
| **G7** | **A（写风险）** | 重跑（新任务 / 命中发送层幂等守卫）会产生**降级新版本**（人物档案 + 关系档案 v2 `INSUFFICIENT_EVIDENCE items=0`）并 `superseded_by` 覆盖好版本 v1 | 否（本轮只锁住事实） |
| **G8** | A（阻塞双人物真实冒烟） | `--run --path=orchestrator` 单人物硬编码，不支持双人物 | 否（§3.2 给出接线方案） |
| **G9** | 低-中 | 兜底路径对"覆盖 <2 参与者"的 RELATION 证据 `continue` **静默丢弃**，只留笼统 `NO_RELATION_EVIDENCE`（与 G5 同类：原因不可筛选） | 否 |
| **G10** | 低 | 验收测试 `assertFalse((REPO_ROOT/"var").exists())` 过于脆弱，被合法 `--dump var/...` 打破 | 否（§5.2 给出三个选项） |

**另外两条状态说明（非缺口，但影响下一轮设计）**：

1. **`usage_sink` 未接线** → 跨进程幂等守卫与用量持久化当前不生效；预算上限是**进程内**的。
2. **单人物请求永远只能 `PARTIAL`**（`_overall_status` 要求五阶段全执行，而关系/对话在参与者 <2 时按语义 SKIPPED）—— 这是诚实的阶段语义，不是缺陷。

---

## 6. 本轮纪律确认

- ✅ **零真实 API 调用**（未调 DeepSeek、未调 Tavily、未调任何外部服务）
- ✅ **零费用**；账本未被重置（本轮根本没构造持久账本）
- ✅ **未修改任何业务代码**：`git status --porcelain` 与轮初一致（`M persona/orchestrator/business.py` 是**上一轮已验收**的 G4/G5 改动；3 个新增脚本/测试文件同样是上一轮的）
- ✅ **未安装依赖**、未扩大范围、未修改无关文件
- ✅ **未提交 Git**
- ✅ 本轮**新增文件仅 1 个**：`docs/REVIEW_P4_5B_TWO_PERSON.md`（本报告）
- ✅ 探针脚本写在**系统临时目录**（`%TEMP%/p45b_probe*.py`），**未进仓库**

**停止，等待人工验收。**
