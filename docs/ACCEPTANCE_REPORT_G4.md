# G-4 验收汇报：基于证据的关系语义分析

> 项目：`D:\AIprojects\generic-persona-skill`
> 日期：2026-10-15
> 范围：闭合 `RUNNING.md` §5.12 缺口 **G-4**（H-10 业务编排器的 AC-2 缺口），并顺带闭合 **G-5**（视角块无确定性程度数值）。
> 运行环境：**全程离线**，未调用任何真实搜索 / 模型 / Provider 网络 API，未产生任何费用。
> 结论：**AC-2 已满足（注入关系分析器后，规则实现，非真实语义模型）**，全量 **722 项测试通过，0 失败，0 放宽断言**。

---

## 0. 第一步：契约冲突检查（先查后做，不重写已验收模块）

在落笔前，逐项核对了 G-4 新要求与已验收契约是否冲突：

| 检查项 | 结论 |
| --- | --- |
| G-4 要求"关系语义分析接口"是否会改写已验收的 `Relationship` 模块（`create_relationship`/`revise`/`validate_evidence_refs` 等）？ | **否**。新增独立的 `persona/modules/relationship_analysis.py` 分析层，只**产出研判候选**，落盘仍由既有 `RelationshipService` 做校验。已验收接口一字未动。 |
| RL-V3（≥3 人 `group_dynamics` 必填）、RL-V5（不跨视角综合）、RL-V6（每条目带 `EvidenceItem` 引用）、RL-V2（无亲密度字段）是否支持新增规则分析器？ | **完全兼容**。规则分析器产出的 `RelationshipDraft` 仍走既有 `_validate_item` / RL-V6 悬空引用守卫，约束天然复用。 |
| 是否会破坏"关系不能由两份人格档案拼出来"（RL-V6 / D-215 / F-6）？ | **否**。分析器输入是编排器**预先过滤出的 RELATION 维度已登记证据**，结构上无法访问 `PersonaProfile`，更不可能从两份档案推断。 |
| 是否会让 `Relationship` 模块反向触发研究流程（F-2）？ | **否**。分析层不持有 `Research`/`Evidence`/`Relationship` 引用（仅 `TYPE_CHECKING` 引入 `EvidenceView`），运行期零反向依赖。 |

**无契约冲突，故直接进入实现，未改动任何已验收模块的核心逻辑。**

---

## 1. AC-2 原文 + 逐项结论

`docs/PROJECT_SPEC.md` 原文：

| 编号 | 原文 |
| --- | --- |
| **AC-1** | 输入 2 个人名，一条命令可跑完「研究 → 证据 → 人物档案 → 关系档案 → 对话」全流程 |
| **AC-2** | 输入 3 个人名时，关系档案中包含群体互动维度 |

### AC-2 逐项结论

| 子要求 | 结论 | 证据 |
| --- | --- | --- |
| 三人输入能产生**群体（GROUP）关系档案** | ✅ 满足 | 编排器在参与者 ≥3 时设 `cardinality=GROUP`；`test_ac2_three_names_produce_group_relationship` 实测 `cardinality == GROUP` 且 `group_dynamics` 非空。 |
| 群体档案包含**群体互动维度**（联盟/排斥/传话） | ✅ 满足（且**不伪造**） | `test_ac2_group_dynamics_content_is_not_fabricated` 证明：仅有共同出现证据时三个板块**全空**并写 `note`；仅当命中明确结盟/排斥/传话标记才填充。 |
| 群体互动内容**不得凭空捏造** | ✅ 满足 | 见上；红线见 §4 与 D-239。 |
| 兼容"未注入分析器"的旧行为（回归） | ✅ 满足 | 既有 `test_ac2_*` 两条测试在 G-4 实现后**仍全绿**（兜底路径逐字保留）。 |

> **AC-2 状态（RUNNING.md）：** 由"未满足"改为 **"已满足（注入关系分析器后，规则实现，非真实语义模型）"**。

> **AC-1 状态：** 此前已由 G-2（自动抽取）闭合（见 D-221~D-235）；本轮无回归，全量套件仍 722 通过。

---

## 2. G-2 claim → 关系分析 实际调用链

```
[G-2 链路：来源材料 → 候选 claim → 已登记证据]
  Research.collect (Mock)                     → materials (SourceMaterial)
  ClaimExtractorPort.analyze                  → 候选 claim（Mock 确定性查表）
  Evidence.add_evidence / record_inference    → EvidenceItem(dimension=RELATION, ...)   ← 由 G-2 产生，绝不从两份人格档案拼（RL-V6）
                         │
                         ▼
[BusinessOrchestrator._step_relationship]
  1) 按参与者过滤出 dimension=RELATION 的已登记证据
  2) 若 BusinessDeps.relationship_analyzer 已注入 且 relation_items 非空：
        RelationshipAnalyzerPort.analyze(request)  → RelationshipAnalysisOutcome（结构化研判）
        _draft_from_analysis(outcome, …)           → RelationshipDraft（item_id 按"角色+证据ID"确定性生成 → 重跑幂等）
  3) 否则（未注入）→ _relationship_draft_fallback（逐字保留 G-4 之前行为，群体只记 note）
                         │
                         ▼
  RelationshipService.create_relationship / revise   （仍走既有 RL-V3/V4/V5/V6/RL-V2 校验）
                         │
                         ▼
  RelationshipProfile（落地，带 version / evidence_refs / group_dynamics）
```

**关键点：**
- 关系研判层的输入是**已登记的 RELATION 证据**，不是原始材料；分析层**不写任何业务表**，落盘归编排器（与 `ClaimExtractorPort` 同一套路）。
- 编排器**依赖端口**（`RelationshipAnalyzerPort`），**不硬编码**具体实现——测试静态断言 `business.py` 源码中不含 `MockRelationshipAnalyzer` / `RuleRelationshipAnalyzer` 字符串，只含 `RelationshipAnalyzerPort`（`test_analyzer_not_hardcoded_in_orchestrator`）。

---

## 3. RL-V3 六个维度 实现状态

`RelationshipAnalysisOutcome` 字段与 `RelationshipProfile` 一一对应，覆盖 RL-V3 全部六个维度：

| RL-V3 维度 | 实现位置 | 状态 | 关键纪律 |
| --- | --- | --- | --- |
| 共同经历（shared experiences） | `shared_experiences`（带 `occurred_at` + `participant_entity_ids`） | ✅ | 凡覆盖 ≥2 人的 RELATION 证据均记录，逐条带 `evidence_refs`。 |
| 互动模式（interaction patterns） | `interaction_patterns` | ✅ | **模式闸门 D-012**：仅当 `stability ∈ {REPEATED, PATTERN}` **且**命中重复标记才算"模式"；单次（SINGLE）命中重复标记只归共同经历并记 `SINGLE_BEHAVIOR_NOT_PATTERN` 提示（`test_single_behavior_not_treated_as_pattern`）。 |
| 情绪与表达习惯（emotional expression） | `emotional_expression_habits` | ✅ | 命中情绪标记时记录，注"未单独确认归属与方向"。 |
| 双方不同视角（directional perspectives） | `perspectives`（A→B 与 B→A 两个独立 `PerspectiveBlock`） | ✅ | 缺证据的方向记 `uncertainty_note`；证据为双方共同记录时两方向各保留同一引用并标注"未单独确认单向态度"，**不强行对称**（RL-V5）。 |
| 时间变化（temporal changes） | `temporal_changes` | ✅ | 同一覆盖对出现**多个不同时点**（`stated_at.start` 去重 ≥2）才进时间轴；单一时点不生成（`test_single_date_no_temporal_change`）。 |
| 群体互动（group interaction，≥3 人） | `group_dynamics`（仅 GROUP 时出现；PAIR 时必须为 `None`，RL-V3） | ✅ | 联盟/排斥/传话仅接受**明确标记**；共同出现/共同活动**不足以下结论**，留空并记 `note`（D-215 / D-239）。 |

> **RL-V2（无亲密度数值）：** 分析层所有形状**无任何** `score`/`closeness`/`intimacy` 字段，测试 `test_no_forbidden_intimacy_fields_anywhere` 递归扫描全部产出形状确认无此类字段；G-5（视角块无确定性程度数值）按 D-134 确认**刻意不为**。

---

## 4. 关系研判 ↔ 证据引用映射（RL-V4 / RL-V6）

- **每条目必带 `evidence_refs`**：`RelationshipAnalysisItem` / `…TimelineItem` 均由 `_item()` 强制写入来源证据 ID，引用 ID **全部来自输入证据**，结构上不可能凭空生成关系。
- **可追溯至本次登记证据**：`test_analyzer_path_every_item_refs_registered_evidence` 遍历落盘后的 `RelationshipProfile` 全部条目（shared / patterns / emotional / temporal / asymmetry / perspectives / group_dynamics），断言每条 `evidence_refs` 都落在 `result.evidence_ids` 内，且对应证据 `dimension == RELATION`。
- **伪造引用被拒（红线）**：`test_analyzer_fake_evidence_ref_rejected` 用 `MockRelationshipAnalyzer` 返回引用不存在证据（`EvidenceItem_does_not_exist`）的条目 → 关系步骤被 `RelationshipService` 的 RL-V6 守卫判 `VALIDATION_DANGLING_REFERENCE`，运行结果为 `RUN_FAILED` + 关系步骤 `STEP_FAILED`。**证明分析器产物不会被当成已验证事实**。
- **不跨视角综合（RL-V5）**：无"综合定论"字段；`shared_*` 也仅在有双方证据共同支撑时存在且带引用。

---

## 5. 反例 / 矛盾 / 时间 / 群体互动 测试结果

| 场景 | 测试 | 结果 |
| --- | --- | --- |
| **反例：共同出现 ≠ 联盟** | `test_co_occurrence_not_alliance`（单元）、`test_analyzer_path_co_occurrence_not_alliance`（编排）、`test_ac2_group_dynamics_content_is_not_fabricated`（既有 AC-2） | ✅ 三个板块全空 + 写说明 note，绝不伪造。 |
| **矛盾：多说法分别保留** | `test_contradictory_sources_preserved_separately` | ✅ 同一覆盖对出现不同 claim → 分别保留，记 `asymmetry_note` + `POSSIBLE_CONTRADICTION` 提示，关联 `conflict_ids`，规则**不裁决**。 |
| **时间变化：多时点** | `test_temporal_changes_from_distinct_dates` / `test_single_date_no_temporal_change` | ✅ 多时点进时间轴；单时点不生成。 |
| **群体互动：明确标记才填充** | `test_group_alliance_from_explicit_marker` / `test_group_exclusion_from_explicit_marker` / `test_group_relay_from_explicit_marker` | ✅ 命中结盟/排斥/传话标记才落对应板块。 |
| **不对称方向（A→B 有、B→A 无）** | `RuleRelationshipAnalyzer` 内 `asymmetry_notes` 生成（带真实证据引用） | ✅ 记单向证据缺失说明，不强行对称。 |
| **证据不足：诚实留空** | `test_no_evidence_returns_empty_with_open_questions` / `test_weak_single_subject_evidence_excluded` | ✅ 无 RELATION 证据 / 单主体证据被排除 → 返回带 `open_questions` 的安全空结果，不伪造。 |
| **确定性重复运行** | `test_deterministic_repeat_runs_equal` / `test_analyzer_path_idempotent_rerun` | ✅ 同输入产出逐字段相等；重跑不产生新关系版本（item_id 按证据 ID 推导，幂等）。 |

---

## 6. 测试命令 / 通过失败数 / 改动文件

### 测试命令（全部离线，`unittest` 标准库，零第三方依赖）

```bash
# 全量（核心结论：722 项，OK，exit 0）
python -m unittest discover -s tests -t .

# 仅 G-4 关系分析单元（21 项）
python -m unittest tests.test_relationship_analysis

# 仅编排器 G-4 路径（7 项）
python -m unittest tests.test_business_orchestrator.TestG4RelationshipSemantic

# 既有验收回归（AC-1/AC-2 等，4 项）
python -m unittest tests.test_business_orchestrator.TestAcceptanceCriteria
```

### 通过 / 失败数

| 套件 | 用例数 | 结果 |
| --- | --- | --- |
| **全量 `discover`** | **722** | ✅ OK（0 失败 / 0 错误 / exit 0） |
| `test_relationship_analysis`（G-4 新增单元） | 21 | ✅ OK |
| `TestG4RelationshipSemantic`（G-4 编排路径） | 7 | ✅ OK |
| `TestAcceptanceCriteria`（AC-1/AC-2 回归） | 4 | ✅ OK |
| 既有全量（G-4 之前基线） | 694 | ✅ 无回归（722 = 694 + 21 + 7） |

> 红线纪律：本轮回**未删除任何既有测试、未降低任何断言强度**；两条既有 AC-2 测试（`test_ac2_three_names_produce_group_relationship`、`test_ac2_group_dynamics_content_is_not_fabricated`）随套件一起保持全绿。

### 改动文件清单

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `persona/modules/relationship_analysis.py` | **新增** | G-4 核心：可注入 `RelationshipAnalyzerPort` + `RuleRelationshipAnalyzer`（离线规则）+ `MockRelationshipAnalyzer`（查表）+ `ProviderRelationshipAnalyzer`（契约占位）。 |
| `persona/orchestrator/business.py` | 修改 | `BusinessDeps` 增加 `relationship_analyzer`；重写的 `_step_relationship` 在注入后走 `_draft_from_analysis`，否则走 `_relationship_draft_fallback`（旧行为逐字保留）；新增 `_first_ref` 帮助函数。 |
| `tests/test_relationship_analysis.py` | **新增** | 21 项：覆盖规则分析器 15 项 + Mock 3 项 + Provider 1 项 + 边界/确定性/可追溯/无禁字段。 |
| `tests/test_business_orchestrator.py` | 修改 | 新增 `TestG4RelationshipSemantic`（7 项）+ `BusinessFixture.use_analyzer` / `_deps(relationship_analyzer=)` 接线；既有测试零改动。 |
| `docs/RUNNING.md` | 修改 | §5.14 G-4 专节；AC-2 状态改"已满足"；G-4/G-5 标记闭合；变更历史 v0.4。 |
| `docs/INTERFACES.md` | 修改 | §7.4 关系语义分析端口（G-4 新增）契约与红线。 |
| `docs/DECISIONS.md` | 修改 | §2.26 G-4 新增 D-236~D-244 决策条目（D-215 的伴生落地）。 |

> 未改动任何已验收模块（`research` / `evidence` / `persona` / `relationship` / 任务管理）的核心逻辑；未触碰用户数据；未自动提交 Git。

---

## 7. 规则分析器 vs 未来真实语义模型 边界

| 维度 | 已验证（规则 / Mock 能做的） | 未验证（需未来真实语义模型） |
| --- | --- | --- |
| 证据维度归类（共同经历/模式/情绪/视角/时间/群体） | ✅ 28 项 G-4 测试全过 | — |
| 关键字信号识别（重复/情绪/结盟/排斥/传话） | ✅ 确定性规则命中 | ⛔ 无法判断是否"礼貌性疏远 vs 真实敌对" |
| 证据可追溯 / 不伪造 / 重跑幂等 / RL-V6 守卫 | ✅ 全过 | — |
| **真正"读懂"潜台词、判定关系是否稳定、区分微妙态度** | — | ⛔ 规则分析器**做不到** |
| **真实网络检索 / 来源正文全文（非 snippet）** | — | ⛔ 同 G-2（H-17 / Q-01~Q-03 未拍板） |

- 当前三种实现 `method` 分别为 `RULE_BASED_OFFLINE` / `MOCK_FIXTURE` / `MODEL`（占位），且 `is_semantic_model` 对本阶段所有实现**恒为 `False`**。
- 未来接入真实语义模型**只需在 `BusinessDeps` 注入新的 `RelationshipAnalyzerPort` 实现**，端口与产出形状（`RelationshipAnalysisOutcome`）保持不变，**无需改动分析层、编排器或落盘链路**。
- **严禁宣称**：任何人不得以本轮规则分析器的"通过"宣称"真实语义模型已验证"。`MockRelationshipAnalyzer` 仅证明**链路连通**（A→B/B→A 不对称、矛盾、时间变化可控），不代表真实研判能力。

---

## 8. 未解决缺口（如实列示，不掩盖）

1. **H-17（材料仅 snippet，无正文全文）**：与 G-2 同源，真实覆盖面受检索摘要长度限制；要真正提升召回需先做正文抓取与切分。**未关闭**。
2. **无真实语义模型**：`RuleRelationshipAnalyzer` 是离线确定性规则，非语义理解；`ProviderRelationshipAnalyzer` 仅契约占位（调用即 `EXTERNAL_UNAVAILABLE`），尚未接入真实模型。**本阶段设计上即"能力边界明确"，非缺陷**。
3. **Q-01 / Q-02 / Q-03（重名处理 / 资料获取渠道与合规 / 在世人物隐私边界）**：仍未拍板，影响真实研究管线，但与本 G-4 解耦（G-4 输入是已登记证据）。
4. **G-5（视角块无确定性程度数值）**：按 D-134 **刻意不为**，符合 RL-V2 红线，无需改动——列此仅为完整披露。
5. **AC-2 满足的前提**：必须在 `BusinessDeps` 注入 `relationship_analyzer` 才走语义研判路径；若未注入则退回"不做语义判断"的旧兜底（群体只记 note）。这是有意的兼容设计，但意味着"AC-2 群体互动维度"在**未注入分析器时**仅以 note 形式存在、不填充三个板块。

---

## 9. 验收签字栏（待人工）

- [ ] AC-2 原文逐项结论接受
- [ ] 调用链与 RL-V3 覆盖接受
- [ ] 红线（不伪造 / 不跨视角综合 / RL-V6 守卫）接受
- [ ] 规则 vs 真实模型边界认知一致
- [ ] 未解决缺口（H-17 / 真实模型 / Q-01~Q-03）知悉

> 完成后如需我继续：可接 H-17（正文抓取与切分）、接入真实 Provider 语义模型、或推进 Q-01~Q-03 拍板。**本轮按指令在完成后停止，等待人工验收。**
