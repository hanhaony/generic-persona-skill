# P4.5-A / G4 + G5 修复报告

> 状态：**修复完成，离线全绿，等待人工验收与真实冒烟授权。**
> 真实模型调用：**未发生**。真实检索调用：**未发生**。费用：**0 USD**。
> 本文所有数字均为本机实测，非推断。

---

## 0. 结论摘要

| 项 | 结果 |
| --- | --- |
| G4（编排器丢抓取正文） | **已修** —— `materials_from_extracts()` 已接线，抓取正文进入材料流 |
| G5（抽取器 warnings 被丢弃） | **已修** —— `outcome.warnings` 原样透传，材料级拒收原因可作告警码筛选 |
| 改动范围 | **仅 1 个产品文件**：`persona/orchestrator/business.py`（+136 / −9） |
| 离线测试 | `tests/test_p4_5a_e2e.py` **37 项全绿**（原 28 项） |
| 全量回归 | **1159 项全绿**（31 个测试模块，无跳过） |
| `--dry --path=orchestrator` | 单人物：材料流非空、模型调用 1 次、证据 5 条；双人物：**SUCCEEDED**、五阶段全 EXECUTED |
| A11 门禁负样例 | 两类均 OK，**0 次模型调用** |
| 真实请求 | **未发出**（`--run` 未执行） |

---

## 1. 两个缺口的根因（复核后确认）

### G4 —— 编排器把抓取正文丢在 `research.extracted` 里没人取

`ResearchService.run` 产出两类东西：

- `research.materials` —— **检索摘要**（`quality_status=""`），每条检索结果一份；
- `research.extracted` —— **抓取到的正文**，过了正文质量门禁的条目 `quality_status=VALID`。

而 `materials_from_extracts()`（唯一会把 `quality_status=VALID` 转成 `SourceMaterial`
的函数）在**产品代码里零调用点**；`_step_research` 只做了：

```python
self._remember(research.materials)      # 只记摘要，正文留在原地
```

于是上线配置（`require_gate_verdict=True`）下，抽取器入口的 `admit_materials`
把每一份"未声明门禁结论"的摘要都判 `MATERIAL_UNDECLARED` 全部拒收：

```
抓了正文 → 正文没交给抽取器 → 摘要进抽取器但没有门禁结论
→ 严格门禁判 MATERIAL_UNDECLARED → 每份材料都拒收
→ 模型零调用 → 证据 0 条
```

**这是"抓了正文却交不出去"的死局**，与模型能力无关，是接线遗漏。

### G5 —— `_register_candidates` 丢掉 `outcome.warnings`

抽取器（`MockClaimExtractor` / `ProviderClaimExtractor`）在入口会产出
`MATERIAL_NOT_GATE_PASSED`、`MATERIAL_QUALITY_UNDECLARED`、`MODEL_RETURNED_EMPTY`、
`MODEL_REFUSED`、`MODEL_CALL_DUPLICATE_SKIPPED` 等告警，但编排器**只**从
`outcome.rejected` 合成一句 `CLAIM_REJECTED`，`outcome.warnings` 整个丢弃。

后果有两层：

1. "材料为什么没进抽取器"看不到 —— 运维会把**材料门禁问题**误判成"模型抽不出东西"；
2. 材料级拒收被冠以 `CLAIM_REJECTED` 的码 —— 明明拒的是**材料**，却标成**候选 claim**，
   且 `MATERIAL_UNDECLARED` 只以文字藏在 message 里，无法按码筛选。

---

## 2. 修复内容

### 2.1 新增 `BusinessOrchestrator._material_flow()`（G4 的唯一落点）

把"研究产出 → 送进抽取器的材料"这件事收敛成一个函数，做四件事：

1. 调 `materials_from_extracts(research.extracted, target_entity_ids=…, query_text=…)`
   取回**过门禁的**正文材料；
2. **绑 `source_id`** —— 该函数按设计留 `""`，而证据登记的可追溯闸门要求候选的
   `source_id` 必须在本次登记的来源里。按 `url` / `canonical_key` 反查已登记来源
   （复用既有的 `_source_locator` 只读反查表）；
3. **同一来源的检索摘要被抓取正文取代**；
4. 未过门禁 / 绑不上来源 / 被取代的，**逐类产出告警**，不静默丢。

没有可用正文时**退回摘要**（保持既有行为，不新增阻断）——此时严格门禁会如实
把它们判为"未声明"，而不是假装有材料。

### 2.2 接线到两条路径

- `_step_research`：改为调用 `_material_flow()` 后 `_remember(materials)`；阶段说明
  现在报"带回 N 份检索摘要、M 份抓取正文，进入材料流 K 份（其中带门禁结论 J 份）"。
- `_materials_for`（**缓存失效时的幂等回补路径**）：同样改走 `_material_flow()`。
  这一步不能漏 —— 否则回补时又只取回摘要，等于埋一个"只在续跑/跨进程时触发"的坑。

### 2.3 新增 4 个告警码

| 告警码 | 含义 |
| --- | --- |
| `MATERIAL_GATE_REJECTED` | 抓取正文未过质量门禁，未进材料流（带 url + 状态 + 原因） |
| `MATERIAL_SOURCE_UNBOUND` | 抓取正文找不到对应已登记来源（不得凭空声称来源） |
| `MATERIAL_SUPERSEDED` | 同来源的检索摘要被抓取正文取代（同一来源不重复交给抽取器） |
| `NO_MATERIAL_FROM_EXTRACTS` | 抓到了正文但无一可用 → 退回摘要 |

### 2.4 G5：透传 `outcome.warnings` + 材料级拒收用自身 reason 当码

```python
warnings.extend(getattr(outcome, "warnings", []) or [])          # 原样透传
for rejected in outcome.rejected:
    reason = _text(rejected.reason)
    if reason and not _text(getattr(rejected, "claim", "")):      # 材料级
        warnings.append(WarningNote(code=reason, ...))            # 码 = MATERIAL_UNDECLARED
        continue
    warnings.append(WarningNote(code=WARNING_CLAIM_REJECTED, ...)) # claim 级，契约不变
```

判定依据是 `RejectedClaim.claim` 是否为空：材料级拒收由 `admit_materials` 构造，
只有 `reason` / `message` / `source_id`；claim 级拒收由 `_rejected()` 构造，带 `claim`。

**既有契约保持不变**：claim 级拒收（例如无效摘录）仍然报 `CLAIM_REJECTED`
（`test_business_orchestrator` / `test_p4_4a_model_extraction` 的断言原样通过）。

---

## 3. 需要你确认的一个设计决策

**同一来源既有检索摘要又有抓取正文时，我选择"正文取代摘要"。**

理由：两者是**同一个 URL 的同一份内容**。同时交出去会有两个后果 ——
模型看到重复材料（同一来源可能抽出重复 claim），而摘要必然被严格门禁拒收
（凭空多出一批 `MATERIAL_UNDECLARED` 噪声）。取代动作**不是静默的**：
会产出 `MATERIAL_SUPERSEDED` 告警并把份数报出来。

替代方案（若你更倾向）：保留摘要一起交，让严格门禁如实拒收它们 —— 信息更全但噪声更大。
**当前实现按"取代 + 告警"落地**，如需改成保留请说一声。

---

## 4. 测试（新增/改写 9 项，`tests/test_p4_5a_e2e.py` 28 → 37）

### 4.1 原"已知缺口护栏"改写为"修复回归锁"

原来的 3 项护栏是**为了在缺口存在时通过**（例如断言"产品代码里没有
`materials_from_extracts()` 调用点"）。缺口修好它们就会红 —— 这正是它们的设计意图。
本轮把它们改写为**反向锁**：

| 原测试 | 现在锁什么 |
| --- | --- |
| `test_g4_materials_from_extracts_is_unused_by_product_code` | → `test_materials_from_extracts_has_a_product_call_site`：AST 断言**必须存在**调用点 |
| `test_g4_orchestrator_path_never_reaches_model` | → `test_orchestrator_reaches_model_exactly_once`：断言调用次数**必须是 1** |
| `test_g5_orchestrator_drops_extractor_warnings` | → `TestG5WarningsPropagation.*`：断言告警**必须透传** |

### 4.2 新增行为锁（`TestG4FixedMaterialFlow`）

- 材料流**非空**、至少 1 份**带门禁结论**、至少 1 份**已绑定来源**（可追溯）；
- 编排器路径**恰好调用模型 1 次**（修复前恒为 0）；
- **证据非空**且 `EVIDENCE_EXTRACT` = `EXECUTED`；
- 单人物仍 `PARTIAL` —— 但断言原因**只剩**关系/对话跳过（研究/证据/人格三阶段均 `EXECUTED`）；
- 不再出现 `MATERIAL_UNDECLARED` / `MATERIAL_NOT_GATE_PASSED` 拒收；
- `MATERIAL_SUPERSEDED` 必须出现在告警里（**取代不静默**）；
- **双人物**：`status == SUCCEEDED` 且五阶段**全部** `EXECUTED`，两实体各自有材料且全部绑定来源。

### 4.3 新增行为锁（`TestG5WarningsPropagation`）

- 抽取器 `outcome.warnings` 逐条透传到 `BusinessStepResult.warnings`；
- 材料级拒收 → 告警码 = 该原因本身（`MATERIAL_UNDECLARED`），且**不再**被标成 `CLAIM_REJECTED`；
- claim 级拒收 → 仍报 `CLAIM_REJECTED`（既有契约）；
- 端到端：门禁未过的材料在**编排器路径**上确实产出 `MATERIAL_NOT_GATE_PASSED` 告警。

---

## 5. 回归结果

定向跑了**全部 31 个测试模块**（`discover` 全量会拉入触网模块并被信号终止，故按模块定向跑）：

| 批次 | 模块 | 结果 |
| --- | --- | --- |
| 1 | business_orchestrator / h10_e2e / claim_extraction / evidence_module / persona_module / research_module | **306 项 OK** |
| 2 | p4_4a_model_extraction / p4_4b_model_setup / p4_4c_deepseek_provider / p4_5_usage_quality / dependency_rules / imports / p4_2_tavily_compliance | **394 项 OK** |
| 3 | p4_5a_e2e / cli / config / core_contracts / dialogue_memory_integration / dialogue_module / memory_module / provider_integration_readiness / provider_mock / relationship_analysis / relationship_module / storage_sqlite / task_budget / task_faults / task_service / task_state / integration_acceptance | **459 项 OK** |

**合计 1159 项，全绿，0 失败 0 跳过。** 其中 `test_dependency_rules` 与 `test_imports`
证明分层铁律未被破坏（`orchestrator` → `modules` 是既有允许方向）。

---

## 6. `--dry --path=orchestrator` 实测（步骤 5）

命令：`python scripts/e2e_p4_5a.py --dry --path=orchestrator` → **预检全部通过，未发出任何真实请求。**

### 6.1 单人物请求（5a）

```
整体状态        : PARTIAL（单人物：关系/对话按语义跳过）
任务终态        : COMPLETED
证据数          : 5
桩模型调用次数   : 1（0 = 模型根本没被调用）
材料流          : 1 份（带门禁结论 1，已绑定来源 1）
研究阶段说明     : 登记 1 条来源（计划 1 条检索，执行 1 条，无结果 0 条；
                  带回 1 份检索摘要、1 份抓取正文，进入材料流 1 份（其中带门禁结论 1 份））
  阶段 RESEARCH             → EXECUTED
  阶段 EVIDENCE_EXTRACT     → EXECUTED     ← 修复前是 SKIPPED
  阶段 PERSONA_DISTILL      → EXECUTED
  阶段 RELATIONSHIP_DISTILL → SKIPPED
  阶段 DIALOGUE_RUN         → SKIPPED
告警码          : ['MATERIAL_SUPERSEDED', 'NO_DIALOGUE_PROFILE', 'NO_EVENTS']
证据阶段说明     : 从 5 个候选中登记 5 条证据，拒收 0 条   ← 修复前是"登记 0 条，拒收 1 条"
```

### 6.2 双人物请求（5b）

```
整体状态        : SUCCEEDED（五阶段全执行）        ← **不再是 PARTIAL**
任务终态        : COMPLETED
证据数          : 10
桩模型调用次数   : 1
材料流          : 2 份（带门禁结论 2，已绑定来源 2）
  阶段 RESEARCH / EVIDENCE_EXTRACT / PERSONA_DISTILL / RELATIONSHIP_DISTILL / DIALOGUE_RUN
                                                    → 全部 EXECUTED
告警码          : ['MATERIAL_SUPERSEDED', 'NO_EVENTS', 'NO_RELATION_EVIDENCE']
证据阶段说明     : 从 10 个候选中登记 10 条证据，拒收 0 条
```

### 6.3 关于"状态不再是 PARTIAL"的**如实说明**

必须把两件事分开：

- **G4 造成的 `PARTIAL`**（`EVIDENCE_EXTRACT=SKIPPED`、证据 0、模型 0 调用）—— **已消除**；
- **单人物请求残余的 `PARTIAL`**（`RELATIONSHIP_DISTILL` / `DIALOGUE_RUN` = `SKIPPED`）
  —— **与 G4 无关**：`_step_relationship` / `_step_dialogue` 在参与者 <2 时本就跳过，
  而 `_overall_status` 只在五阶段全执行时才给 `SUCCEEDED`。

因此**单人物请求永远只能是 `PARTIAL`**。为了让"不再是 PARTIAL"这句话有真正的证据，
本轮新增了**双人物诊断（5b）**：参与者 ≥ 2 → 五阶段全部 `EXECUTED` →
`status = SUCCEEDED`。这是编排器整链真正跑通的证明。

### 6.4 A11 门禁负样例（复查，未回退）

| 负样例 | 拒收原因 | 模型调用 |
| --- | --- | --- |
| `LOW_QUALITY` | `['MATERIAL_NOT_GATE_PASSED']` | **0 次** |
| 未声明 `""` | `['MATERIAL_UNDECLARED']` | **0 次** |

---

## 7. 改动清单与安全自检

| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `persona/orchestrator/business.py` | **已修改**（+136 / −9） | 唯一的产线代码改动 |
| `scripts/e2e_p4_5a.py` | 未跟踪（新增） | 新增双人物诊断、第二份合成材料、per-query 桩数据源、G4 验证断言 |
| `tests/test_p4_5a_e2e.py` | 未跟踪（新增） | 28 → 37 项 |
| `scripts/verify_p4_5a_outputs.py` | 未跟踪（新增） | 未改动 |
| `docs/FIX_P4_5A_G4_G5.md` | 未跟踪（新增） | 本报告 |

自检（全部为 0）：厂商 SDK 引用 0、硬编码本机路径 0、密钥字面量 0、
真实人物名 0（新增合成材料用 阿雾 / 阿灯 / 雾港 / 星灯，全虚构，AC-16 合规）。

---

## 8. 未做事项

1. **未发真实请求** —— `--run` 未执行，等你单独授权。
2. **未提交 Git** —— 产品改动与 3 个新文件仍待你验收后再定。
3. **未把真实调用接到编排器路径** —— `--path=orchestrator` 目前只支持 `--dry`
   诊断；`--run --path=orchestrator` 会以退出码 3 **明确拒绝**（理由已改为
   "该路径真实运行尚未接线"，不再是"到不了模型"）。真实冒烟仍走
   `--run --path=script`。

---

## 9. 建议的下一轮（供你决定，本轮不做）

1. **把真实冒烟改走编排器路径**。G4 修好后，`BusinessOrchestrator` 已是能到达模型的
   **产品代码**端到端链路，验证强度高于脚本侧装配（脚本路径仍需手写材料交接）。
   建议下一轮把 `--path=orchestrator` 的真实运行接线，并让 `--run` 默认走它 ——
   那时的冒烟才算"产品代码全链 + 真实模型"。
2. **`_step_relationship` 的 `NO_RELATION_EVIDENCE`** 在双人物用例里出现（合成材料
   没有 RELATION 维度证据）—— 属预期，不是缺陷；但若要跑更完整的双人物链，可补
   一条 RELATION 维度的合成材料。
3. **`pending` 的 fail-closed**（D3 拍板"另立一轮"）仍未做。
4. **`_build_persona_draft` 仍是规则式搬运**（`distill` 的 AI 蒸馏主体未实现）——
   上一轮报告已列为"尚未验证"，本轮未触碰。

---

## 10. 请求确认（步骤 6）

请确认：

1. §3 的"**正文取代摘要**"设计决策是否接受（当前实现）；
2. 是否授权真实冒烟；
3. 若授权 —— 冒烟走哪条路径：`--run --path=script`（**当前唯一已接线**，1 次 POST，
   预估 ≤ 0.0033 USD，上限 0.25 USD，`max_retries=0`），还是先补 §9.1 的编排器接线再跑。
