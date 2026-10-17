# P4.5-B 缺口修复 · B 轮实施报告（G9 / G6）

> 范围：G9（RELATION 证据分类告警）、G6（`judge_stability` 接线并落库）
> 拍板：7 项决策已按用户口径全部落地（见 §0 对照表）
> 约束：不调真实 API、不装依赖；提交仅限本轮指定范围
> 产物：4 个产品文件 + 1 个脚本 + 1 个测试文件

---

## 0. 七项决策的执行情况

| 决策 | 拍板 | 落地方式 | 位置 |
| --- | --- | --- | --- |
| **G6-D1** | 落库，作为证据的附加字段 | 新增 `stability_verdict` 字段（dict），**不覆盖** `stability` 列 | `evidence.py` |
| **G6-D2** | 同来源不算多次，按 `source_id` 去重后再判定 | 组内每来源只留一条代表喂给判定器 | `business._assess_stabilities` |
| **G6-D3** | 分组键用归一化文本 + 主体 | `(折空白 claim, frozenset(subjects))`，**不含 dimension** | `business._stability_groups` |
| **G6-D4** | 保持 `UNKNOWN`、不进 `traits`、记录为"未判定" | 判定失败退回 `UNKNOWN` + `STABILITY_UNASSESSED` 告警 | `business._assess_stabilities` |
| **G9-D1** | 告警 + 分类计数，不降级为 note | 三类告警码，每类一条、带数量与样例 ID | `business.py` / `relationship_analysis.py` |
| **G9-D2** | 按原因分类（主体不足 / 分析失败 / 其他） | 三个独立告警码，各自可筛选 | 同上 |
| **G9-D3** | 单人物单独报 | `RELATION_STAGE_SKIPPED_WITH_EVIDENCE` | `_step_relationship` 的 SKIPPED 分支 |

**与方案的一处差异（需你知情）**：方案 §2.8 的 G6-D1 我建议的是"即时判定、不落库"（选项①），
你拍板选的是"落库，作为附加字段"。实现时我没有把判定写进 `stability` 列，而是**另开
`stability_verdict` 字段**——因为 `stability` 是**登记时**抽取侧声明的值（按 PF-V2/D-012，
模型无权宣布自己的说法稳定，一律 `UNKNOWN`），覆盖它等于抹掉"当时声明了什么"这一事实，
可追溯性就没了。落库后一条证据上会同时存在：

```
stability          = "UNKNOWN"    ← 登记时的声明（历史事实，永不改写）
stability_verdict  = {"stability": "REPEATED", "occurrences": 2,
                      "distinct_sources": 2, "time_span": null,
                      "reason": "...", "evaluated_at": "..."}
```

消费方统一读 `EvidenceView.effective_stability`（判定优先，无判定退回登记值）。

---

## 1. G9：RELATION 证据的分类告警

### 1.1 修复前有三个毛病

| 毛病 | 位置 | 后果 |
| --- | --- | --- |
| `covered < 2` 直接 `continue` | `business.py` 兜底路径 / `relationship_analysis.py` 分析器路径 | 一条痕迹都不留 |
| "有证据但主体不足"与"根本没证据"共用一句 `NO_RELATION_EVIDENCE` | `_relationship_draft_fallback` | 运维无法分辨该补材料还是该查抽取器 |
| 单人物时 RELATION 证据**没有任何消费者** | 阶段在过滤证据**之前**就 SKIPPED；人物档案没有 RELATION 维度 | 证据登记在库、永远不被引用，任务视图里"消失" |

第二条尤其误导：上一轮双人物 dry-run 的 `RELATIONSHIP_DISTILL→EXECUTED` 却带
`NO_RELATION_EVIDENCE`，文案说的是"没有可用的 RELATION 维度证据"——**明明有**，
只是都不覆盖两人。

### 1.2 三类告警（按拍板口径）

| 告警码 | 含义 | 落点 |
| --- | --- | --- |
| `RELATION_EVIDENCE_SUBJECT_INSUFFICIENT` | 主体不足：覆盖 <2 名本次参与者 | 兜底路径 + 分析器路径（两处都改） |
| `RELATION_ANALYSIS_FAILED` | 分析失败：证据交进去了，分析器一个条目都没产出 | 分析器路径 |
| `RELATION_EVIDENCE_UNUSED_OTHER` | 其他：本次证据一条都没写进新版本（复用旧版） | 写档后（`already=True` 且本次有证据） |

每条都带**数量 + 样例 ID**，不堆几百个 ID：

```
3 条 RELATION 证据未覆盖 ≥2 名本次参与者（evd_aa11, evd_bb22, evd_cc33），
未进关系档案——常见原因：材料只描述了单人的事，或只提到本次之外的第三人。
这不是丢数据，是这些证据不能支撑"共同经历"
```

### 1.3 顺手补掉的两个同型漏洞

1. **`NO_RELATION_EVIDENCE` 语义纠正**：现在只有 `not relation_items`（本次**真的一条都没有**）
   才报。修复前"有证据但全被筛掉"也报这句，属于**误报**。
2. **分析器的 `outcome.warnings` 之前被丢掉**（与 G5 完全同型）：`_draft_from_analysis`
   只取了 draft，`outcome.value.warnings` 从未进步骤告警。修好后分析器内部的
   "主体不足"计数、"单次行为不固化为互动模式"等提示才看得见。

### 1.4 单人物悬空（G9-D3）

```
参与者只有 1 人，关系阶段按语义跳过；但本次登记了 1 条 RELATION 证据（evd_aa11），
它们不会被任何档案消费（人物档案没有 RELATION 维度，关系档案未建立）
——要消费请提供 ≥2 名参与者
```

这条直接回答了上一轮真实冒烟里"那 1 条 RELATION 证据去哪了"——**之前完全不可见**。

读取失败时不伪装成"没有证据"：记一条 `RELATION_EVIDENCE_UNUSED_OTHER` 说明"统计失败"，
阶段仍保持 SKIPPED（不因为附加诊断把状态改成 FAILED）。

---

## 2. G6：稳定性判定接线并落库

### 2.1 断点与修法

修复前三处断点：抽取 schema 没有 `stability` 字段（`extraction.py`）→ 候选恒 `UNKNOWN`
→ `judge_stability` 产品零调用点 → 消费时全部落 `single_events`。

现在链路：

```
_step_persona
  └─ _assess_stabilities（**全局一次**，不放进 per-target 循环）
       ├─ _stability_groups：按 (折空白 claim, 主体集合) 归组        ← G6-D3
       ├─ 组内按 source_id 去重，每来源留一条代表                    ← G6-D2
       ├─ Evidence.judge_stability(...)  → StabilityVerdict
       └─ Evidence.record_stability_verdict(...) → 落 stability_verdict  ← G6-D1
  └─ _build_persona_draft(..., stabilities)
       └─ 判定优先 → SINGLE/UNKNOWN 进 single_events；REPEATED/PATTERN 进 traits
```

**判定为什么必须在全局做一次**：一条双主体证据会同时出现在两个目标的 `items` 里。
若放进 per-target 循环，它会被判两次，第二次的分组可能只剩它自己 ⇒ 判成 `SINGLE`
并覆盖前一次的 `REPEATED`，既多写一遍库、结果也不稳定。实现时先取 `by_entity` 的
并集去重，再判一次。

**只判定"未声明"的证据**：`stability != UNKNOWN` 的证据**跳过判定、不写判定字段**。
真实链路里抽取器永远给 `UNKNOWN`（PF-V2 不允许模型自评），所以生产路径全都会判；
而测试夹具 / 显式种子 / 将来的人工标注写了 `REPEATED` / `PATTERN` 时，那是**更强的
信息**，机械规则把它盖成 `SINGLE` 会把本该进 `traits` 的证据踢出去（与 G7
「不降级覆盖」同一精神）。但**计数时仍把已声明的成员算作一次出现**——它确实是一个
独立来源的佐证，只是不需要再判一次。

> 这条是跑全量回归时暴露的：首轮实现无条件判定，把两个既有产品测试
> （`test_single_occurrence_never_lands_in_traits` /
> `test_analyzer_invoked_and_enriches_two_actor_profile`）的夹具声明覆盖成了
> `SINGLE`，导致本该进 `traits` / `interaction_patterns` 的条目消失。修法就是上面的
> "只判未声明"，并补了两条测试锁住它。

### 2.2 `judge_stability` 保持只读（D-109 不破）

判定器本身仍是纯函数、不写库。落库是**新增的** `record_stability_verdict()` 的职责，
两件事分开，`judge_stability` 的"只读"承诺没有被破坏。

### 2.3 G6-D2 为什么是关键（这轮最重要的安全约束）

幂等键用**精确** claim（`_find_evidence`）、分组键用**归一化** claim（D-104 口径），
两者的差集就是"仅空白差异的近似重复"：

```
"阿雾总在深夜修灯"   ← 来源 A，已登记
"阿雾总在深夜  修灯" ← 来源 A，多一个空格，幂等键不同 ⇒ 也登记了
```

原样喂给判定器 ⇒ `occurrences=2 / distinct_sources=1` ⇒ **REPEATED** ⇒ 进 `traits`。
这等于**一条材料自己给自己作证两次**，AC-7 被绕过。

按 `source_id` 去重后 `occurrences` 恒等于 `distinct_sources`，这扇门关上了。
测试 `test_whitespace_variant_from_same_source_does_not_count_twice` 专门锁这一条。

### 2.4 顺带修的一致性问题

`stability_verdict` 落库后，如果只有人物档案读它、关系分析仍读 `stability`，
就会出现"同一证据在两个档案里 stability 不同"的割裂，而且关系侧的模式闸门
（只放 `REPEATED`/`PATTERN`）会因 `stability` 恒为 `UNKNOWN` **永远打不开**。
所以两处消费点统一改读 `effective_stability`：

- `relationship_analysis.py:414`（分析器的条目稳定性）
- `business.py` 兜底路径的 `SharedExperienceItem`

### 2.5 单来源下 `traits` 仍然必然为空 —— **这是设计，不是 bug**

已证明（方案 §2.2）：同来源的同一说法只会有一个代表 ⇒ `occurrences=1` ⇒ `SINGLE`
⇒ 按 AC-7 绝不进 `traits`。所以接线后：

| 场景 | 结果 |
| --- | --- |
| 单来源（上一轮真实冒烟、默认 dry-run） | `traits` 仍为 **0**，但 `single_events` 的标签从 `UNKNOWN` 变成 `SINGLE` |
| 双来源同一说法 | `REPEATED` → **进 traits**（AC-7 允许的情形） |
| 三来源同一说法 | `PATTERN` → 进 traits |
| 多来源但**措辞不同**（真实抓取的常态） | 各自成组 ⇒ 各自 `SINGLE` ⇒ 仍为 0 |

最后一行是必须说清的边界：`judge_stability` 只认"归一化后完全一致"的说法，
语义聚类需要模型，**不在本轮**。所以真实链路里 `traits` 仍大概率为空。
G6 接线的价值是"机制可用 + 可审计 + 语义诚实"，不是"填满 traits"。

---

## 3. 顺带修掉的一个脚本 bug

`scripts/e2e_p4_5a.py` 的 `Stack.ensure_targets()` 里，`ensure_entity()` 内部**已经**把目标
append 进 `self.targets`，外层又 `PersonQueryTarget(...)` 新建了一个 ⇒ `self.targets`
出现重复条目。它没有影响 `--run`（那条路径显式传 `targets=`），但任何按 `self.targets`
构造请求的代码都会拿到重复人名。已改为复用 `ensure_entity` 返回的那个对象。

---

## 4. 测试

### 4.1 新增 18 项

| 类 | 项数 | 覆盖 |
| --- | --- | --- |
| `TestG9RelationEvidenceClassification` | 8 | 主体不足报数带 ID / 不冒充"没有证据" / 真没证据仍报原码 / 双主体正常不报噪声 / 单人物悬空点名 / 单人物无 RELATION 不报 / 分析器分类透传 / 分析器空产出报"分析失败" |
| `TestG6StabilityAssessment` | 10 | 单来源恒 SINGLE 不进 traits / 双来源 REPEATED 进 traits / **空白变体不算两次** / 三来源 PATTERN / 判定落库且不改 `stability` 列 / 判定重读仍在 / 无判定退回 UNKNOWN / **已声明的不被覆盖** / **已声明成员仍参与计数** / `_step_persona` 整链接线 |

### 4.2 反转一个缺口锁

`test_judge_stability_has_no_product_call_site`（原断言"产品代码不许有调用点"）
→ **`test_judge_stability_has_a_product_call_site`**（断言"必须有调用点，且在编排器里"）。
这是方案 §2.7 预判的**唯一**必然变红项。

其余两个缺口锁**保持不变**且仍绿：
`test_model_reported_stability_is_not_parsed`（抽取器仍不解析模型自报的 stability，
这是 PF-V2 的要求）、`test_unknown_stability_trait_lands_in_single_events_never_traits`
（单来源仍落 single_events）。

### 4.3 结果

```
tests.test_p4_5a_e2e                        78 项  OK（60 → 78，新增 18）
全量 discover                                <见 §4.4>
```

### 4.4 全量回归

```
Ran 1200 tests in 201.8s
OK          （1182 → 1200，新增 18；0 失败 0 错误）
```

> 第一次全量回归是 **2 失败**（1198 项时）：`test_single_occurrence_never_lands_in_traits`
> 与 `test_analyzer_invoked_and_enriches_two_actor_profile`。根因是首轮实现**无条件**
> 判定，把夹具里显式声明的 `REPEATED` 覆盖成了 `SINGLE`。修法见 §2.1 末尾
> （只判 `UNKNOWN`，已声明的跳过但参与计数），并补了两条测试锁住。

**dry-run（零真实请求）**：

```
python scripts/e2e_p4_5a.py --dry --path=orchestrator          → 退出码 0
python scripts/e2e_p4_5a.py --dry --path=orchestrator --names 阿雾,阿灯 → 退出码 0
```

---

## 5. 改动清单

| 文件 | 内容 |
| --- | --- |
| `persona/modules/evidence.py` | `EvidenceView.stability_verdict` + `effective_stability`；`record_stability_verdict()` |
| `persona/modules/relationship_analysis.py` | 主体不足分类计数；`WARNING_SUBJECT_INSUFFICIENT`；稳定性读 `effective_stability` |
| `persona/orchestrator/business.py` | G9 四类告警 + 单人物悬空 + 分析器告警透传；G6 `_stability_groups` / `_assess_stabilities` / `_build_persona_draft` 新参数 / `_relation_evidence_of` |
| `scripts/e2e_p4_5a.py` | `ensure_targets` 重复登记修复 |
| `tests/test_p4_5a_e2e.py` | +16 项；反转 1 个缺口锁 |
| `docs/PLAN_P4_5B_ROUND_B.md` | 方案文档（本轮一并入库） |

未改动：`persona/cli/`、`persona/providers/`、`persona/storage/`、`persona/modules/persona.py`、
`persona/modules/relationship.py`。

---

## 6. 已知边界（本轮不做）

1. **语义聚类缺失**：措辞不同但语义相同的说法不会被归为一组，真实抓取下 `traits`
   仍大概率为空（§2.5）。
2. **`UNKNOWN` 的处置**：判定失败仍按"不进 traits"保守处理，只有告警提示，
   没有把失败升级成错误（D4 口径）。
3. **"其他"类告警的覆盖面**：目前只覆盖"本次证据未写入新版本"这一种情形
   （其余情形已被前两类覆盖）。未来出现新原因时在这里扩展，不必新增告警码。
4. **C 轮冒烟仍需"互动页"材料**：本轮没有造测试数据。关系条目产出路径
   （覆盖 ≥2 参与者的 RELATION 证据 → `shared_experiences`）仍未在真实链路上验证过。
