# P4.5-B · B 轮方案（G6 / G9）

> **状态：只有方案，没有实施。** 本文不含任何代码改动，等拍板后另开一轮。
> 范围：G6（`judge_stability` 未接线）、G9（`<2` 参与者的 RELATION 证据被静默丢弃）
> 约束：不改产品代码、不调真实 API、不装依赖、不提交 Git

---

## 0. 先说结论

| 问题 | 结论 |
| --- | --- |
| **单来源下 `traits` 是否必然为空？** | **是，必然为空**，而且是结构性的，与材料质量、模型能力无关（证明见 §2.2） |
| G6 接线后能否填满 `traits`？ | **单来源下不能**；多来源且同一说法字面一致时能。真实抓取中"字面一致"近乎不发生 |
| G6 需要设计决策吗？ | **需要 4 项**（见 §2.8），核心是"同来源的多条近似重复算不算多次出现" |
| G9 三种处理如何选？ | **选"明确告警 + 分类计数"**，不选"降级为 note"（会把单主体证据谎报成"共同经历"） |
| G9 需要设计决策吗？ | **需要 3 项**（见 §3.6），核心是"告警粒度"与"单人物悬空证据是否单独报" |
| 两项是否影响已验收行为？ | **G6 会让 1 个测试必然变红**（缺口锁需反转）；**G9 对现有断言零影响**（全是 `assertIn`，无精确集合或数量断言） |

**推荐实施顺序**：G9 先（纯增量告警，零语义风险），G6 后（有语义变更，需先定 §2.8 的 4 项）。

---

## 1. 事实基线（全部代码级取证）

### 1.1 G6 的证据链断在三个地方

| 环节 | 位置 | 事实 |
| --- | --- | --- |
| 抽取 schema | `persona/modules/extraction.py:513-517` | **没有 `stability` 字段**。模型即使想报也无处可报 |
| 候选默认值 | `persona/modules/extraction.py:168` | `stability: Stability \| str = Stability.UNKNOWN` |
| 搬运 | `persona/orchestrator/business.py:779` | `stability=candidate.stability` 原样搬运（这条是**对的**） |
| 判定器 | `persona/modules/evidence.py:1035-1075` | `judge_stability()` 存在、只读、确定性强 |
| 判定器调用 | 全仓 grep | **产品代码零调用点**（只在 `tests/test_evidence_module.py` 里被调） |
| 消费 | `persona/orchestrator/business.py:1071` | `stability = _stability_of(item.stability)` → 读到 `UNKNOWN` |
| 分流 | `persona/orchestrator/business.py:164` | `_NON_TRAIT_STABILITIES = (SINGLE, UNKNOWN)` → TRAIT 落 `single_events` |

所以：**`stability` 从出生到消费，全程只有一个值 `UNKNOWN`。**

### 1.2 `judge_stability` 的确切语义（决定了接线的形态）

```python
judge_stability(ctx, subject_entity_id, claim_text, evidence_ids) -> Result[StabilityVerdict]
```

- 输入是一批**已经按"同一说法"归好组**的证据 ID（D-109 明说"未做交叉校验（E-3）"——
  它**不检查**这批 ID 是不是真的同一说法，给什么就算什么）。
- 规则：`occurrences = len(ids)`；`distinct_sources` = 去重后的 `source_id` 数；
  `occurrences <= 1 → SINGLE`；`distinct_sources >= 3 → PATTERN`；否则 `REPEATED`。
- 返回 `StabilityVerdict(stability, occurrences, distinct_sources, time_span, reason)`，**只读**。
- docstring 自述：**"语义级稳定性判定需要模型，未实现。"**

**关键推论**：`judge_stability` 是一个纯判定函数，**分组责任 100% 在调用方**。接线不是"调一下函数"，
是要先造出"同一说法组"。

### 1.3 G9 的丢弃点有两处，且**契约层并不要求丢弃**

| 位置 | 代码 | 行为 |
| --- | --- | --- |
| `persona/orchestrator/business.py:1272-1276`（兜底路径） | `covered = [p for p in participants if p in subject_ids]` → `if len(covered) < 2: continue` | **静默丢弃**，连计数都没有 |
| `persona/modules/relationship_analysis.py:372-378`（分析器路径） | `if len(covered) >= 2: covered_items.append(...)` | 同上，不满足即跳过，无痕 |
| `persona/modules/relationship.py:1064-1097`（`_validate_item`） | 只校验 text / confidence / item_id / evidence_refs / 版本边界 | **完全不管 `participant_entity_ids` 有几个** |

也就是说：**"必须 ≥2 参与者"是编排器的策略选择，不是模块契约的强制约束。**
这给了 G9 三个选项全部合法的空间——选哪个取决于语义正确性，不取决于能不能写进去。

### 1.4 单人物时，RELATION 证据**没有任何消费者**

`_step_relationship` 在 `persona/orchestrator/business.py:1194-1198`：

```python
if len(participants) < 2:
    return BusinessStepResult(status=STEP_SKIPPED, ...)   # ← 在过滤证据之前就返回
```

而 `_build_persona_draft`（`business.py:1079-1105`）的维度分流里**没有 `RELATION` 分支**。
两头都不接 ⇒ 单人物跑出来的 RELATION 证据**登记在库、永远不被任何档案引用**，
唯一痕迹是人物档案 `open_questions` 里的那句
"本次证据中没有可用于人物档案的维度（例如 RELATION 维度归关系档案）"——**那不是告警，筛不出来**。

上一轮真实冒烟就是这个状态：6 条证据中 1 条 RELATION，它进了库，但既不在 `traits/habits/...`，
也没有关系档案（阶段 SKIPPED），任务视图里**看不出这条证据去哪了**。

---

## 2. G6：`judge_stability` 怎么接

### 2.1 断点在哪

```
抽取（schema 无 stability）
   ↓  CandidateClaim.stability = UNKNOWN
搬运（原样）
   ↓  EvidenceDraft.stability = UNKNOWN
登记入库
   ↓  EvidenceItem.stability = "UNKNOWN"
   ✗  ←── 断在这里：judge_stability() 存在，但没人调
消费（_build_persona_draft）
   ↓  UNKNOWN ∈ _NON_TRAIT_STABILITIES
TRAIT → single_events（traits 空）
```

### 2.2 单来源下 `traits` 是否必然为空 —— **是，可证明**

**前提 P1**：证据幂等键是 `(source_id, 精确 claim 文本, subject 集合)`
（`business.py:961-977`，注意是**精确文本**比较，不是归一化）。

**前提 P2**：`judge_stability` 的 `occurrences = len(传入的 id 列表)`。

**前提 P3**：`_NON_TRAIT_STABILITIES = (SINGLE, UNKNOWN)`，TRAIT 证据落在其中即进 `single_events`。

**推导**：设某 TRAIT 说法 S 的"同说法组"为 G。

- 若只有**一个来源**（所有证据 `source_id` 相同）：G 内任两条证据的 `source_id` 相同。
  若它们的 claim 文本完全相同、subject 相同 ⇒ 幂等键相同 ⇒ 第二条被 `_find_evidence`
  命中并**复用第一条的 ID**，不会产生第二个 ID。⇒ `|G|` 中"不同 ID 数" = 1
  （唯一的例外是仅空白差异，见 §2.4 的陷阱）。⇒ `occurrences = 1` ⇒ **SINGLE**
  ⇒ 落 `single_events` ⇒ **`traits` 为空**。
- 若只是"某个说法恰好只被提到一次"：`occurrences = 1` ⇒ 同样 SINGLE。

**结论**：单来源下，任何 TRAIT 证据的 `occurrences` 上限就是 1，
`traits` **结构性地不可填充**。这不是"材料不够好"，换更长的材料、更贵的模型都一样——
除非引入第二个来源。

这与上一轮实测完全吻合：真实冒烟 6 条证据、TRAIT 1 条，`stability=UNKNOWN`，`traits=0`。

### 2.3 三种接法对比

| 方案 | 做法 | 优点 | 缺点 | 判断 |
| --- | --- | --- | --- | --- |
| **A. 写档案时即时判定**（推荐） | `_step_persona` 在调 `_build_persona_draft` 之前，按"同说法组"逐组调 `judge_stability`，把得到的 `Stability` 作为**参数**传进构建函数，覆盖条目的 stability | ① 不改存储契约（`stability` 列不动）；② `judge_stability` 保持"只读"承诺（D-109 不破）；③ 重跑幂等（判定是纯函数，同输入同输出）；④ 不改抽取器 | 证据表里仍是 `UNKNOWN`，档案里的 stability 是**派生值**，两者不一致，需要在文档里说清 | ✅ |
| B. 判定后落库 | 给 `Evidence` 加一个写 `stability` 的方法，登记后立刻判定并回写 | 单一事实源 | ① 破坏 D-109 的"只读"承诺，或要新增写接口 + 审计；② 重跑要重算并回写，多一路写风险（G7 刚教训过）；③ 违反"证据登记后不可变"的直觉 | ❌ |
| C. 让模型自评 | 抽取 schema 加 `stability` 字段 | 一行改动 | ① `CandidateClaim` docstring 明写"不得编造"；② PF-V2/D-012 的本意就是**不让模型宣布自己稳**；③ 不可审计 | ❌ |

### 2.4 推荐接法 A 的具体形态（伪代码，非实现）

**签名变更（向后兼容）**：

```python
@staticmethod
def _build_persona_draft(
    entity_id, items, entity_type,
    stabilities: dict[str, Stability] | None = None,   # 新增，默认 None = 旧行为
) -> PersonaProfileDraft
```

`stabilities` 是 `{evidence_id: Stability}`。为 `None` 时逐字走旧逻辑
⇒ `scripts/`（2 处）与 `tests/`（5 处）的既有直接调用**全部不受影响**。

**新增分组器（编排器私有静态方法）**：

```python
_group_by_claim(items) -> dict[key, list[evidence_id]]
key = (归一化 claim, frozenset(subject_entity_ids), dimension)
```

归一化口径：**只折空白**，与 `evidence._normalize_claim` 一致（D-104）。

**判定（`_step_persona` 内、构建草稿之前）**：

```python
for key, ev_ids in groups.items():
    # ⚠ 关键：组内按 source_id 去重，每来源只留 1 条代表
    rep = {source_id: evidence_id for ...}   # 同来源多id → 取首个
    verdict = evidence.judge_stability(ctx, subject, claim_text, list(rep.values()))
    for ev_id in ev_ids:
        stabilities[ev_id] = verdict.stability
```

> **为什么必须按来源去重**：幂等键用**精确** claim，分组键用**归一化** claim。
> 两者的差集就是"仅空白差异的近似重复"。同一来源里这类变体会被登记成 2 条 ID，
> 若原样传给 `judge_stability` ⇒ `occurrences=2`、`distinct_sources=1` ⇒ **REPEATED**
> ⇒ 进 `traits`。**这等于用一条材料自己给自己作证两次，绕过了 AC-7。**
> 按来源去重后，`occurrences` 恒等于 `distinct_sources`，这条缝就封死了。

### 2.5 与 AC-7 的关系（三种情形逐一对照）

AC-7 原文：**"单人单次行为的描述，不会出现在'稳定性格特征'字段中。"**
`D-012`：单次行为不得认定为稳定性格。`D-214`：`SINGLE`/`UNKNOWN` 的 TRAIT 进 `single_events`。

| 情形 | 接线前 | 接线后 | AC-7 |
| --- | --- | --- | --- |
| 1 个来源、1 次提及 | `UNKNOWN` → `single_events` | `SINGLE` → `single_events` | **不变，仍满足**（标签从"没判"变成"判过了"） |
| 1 个来源、空白变体重复 | `UNKNOWN` → `single_events` | 按来源去重 ⇒ `SINGLE` → `single_events` | **更好**：封住了"自己给自己作证"的缝 |
| 2+ 个来源、同一说法字面一致 | `UNKNOWN` → `single_events`（**误判为单次**） | `REPEATED`/`PATTERN` → **进 `traits`** | **更好**：这正是 AC-7 想允许的"多来源佐证" |

**一句话**：接线不是放宽 AC-7，是让 AC-7 从"永远说不知道"变成"能区分单次与重复"。
保守性只会**更严**（情形 2 被封住），不会更松。

### 2.6 接线后**仍不解决**什么（必须提前说清）

`judge_stability` 的确定性规则只能识别**归一化后完全一致**的说法。真实抓取里，
"他总在深夜写代码" 与 "他有熬夜工作的习惯" 是**两个组**，各自 `SINGLE`。

⇒ **真实链路下 `traits` 仍然大概率为空**，即使有 5 个来源。要突破这层，
必须做**语义聚类**（把措辞不同但语义相同的说法归组），那需要模型，属于另一个量级
（新判定器 + 新提示词 + 新的不确定性表达），本方案**不含**。

所以 G6 接线的真实价值是三条，而不是"填满 traits"：

1. **机制可用**：多来源佐证能真正生效（合成材料里可测、未来真实抓取里有机会）；
2. **可审计**：`StabilityVerdict` 带 `occurrences` / `distinct_sources` / `time_span` / `reason`，
   "为什么这条进了 traits"从"没有理由"变成"有账可查"；
3. **语义诚实**：`single_events` 的标签从"没判过（UNKNOWN）"变成"判过且判为单次（SINGLE）"。

### 2.7 影响面（会红 / 需改的测试）

| 测试 | 位置 | 影响 | 处理 |
| --- | --- | --- | --- |
| `test_judge_stability_has_no_product_call_site` | `tests/test_p4_5a_e2e.py:813` | **必然变红**（它断言的正是"产品代码不许有调用点"） | **反转为反向回归锁**：改成"必须有调用点，且调用点在 `_step_persona` 的判定函数里"（与 G4 那轮把缺口锁改写成回归锁同一手法） |
| `test_model_reported_stability_is_not_parsed` | `:819` | 仍绿（抽取器仍不解析） | 不动 |
| `test_unknown_stability_trait_lands_in_single_events_never_traits` | `:843` | **仍绿**（单来源 ⇒ SINGLE ⇒ 仍不进 traits） | 仅更新 docstring 与断言文案（`UNKNOWN` → `SINGLE` 亦可接受，因为断言用的是 `in ("SINGLE","UNKNOWN")`） |
| `test_orchestrator_real_run_end_to_end_offline` 等 5 处 `_build_persona_draft` 直接调用 | `:199`/`:991`/`:1012`/`:1030`/`:1037` | 不受影响（新参数默认 `None`） | 不动 |
| `scripts/e2e_p4_5a.py` 2 处直接调用 | `:1206`/`:1700` | 不受影响（同上） | 可选：在产物里多写一列 verdict 供复算 |

**新增测试计划（约 8 项）**：单来源恒 SINGLE / 双来源 REPEATED 进 traits / 三来源 PATTERN /
同来源空白变体不算多次 / 不同说法各自 SINGLE / `stabilities=None` 时行为逐字不变 /
判定器抛错时的降级 / 重跑幂等（version 不前进）。

### 2.8 需要你拍板的 4 项决策

| 编号 | 决策 | 选项 | 我的建议 |
| --- | --- | --- | --- |
| **G6-D1** | 判定结果落在哪？ | ① 写档案时即时判定、不落库（派生值）<br>② 落库回写证据 | **①**。不破 D-109 只读承诺、无新写风险；代价是"证据表 UNKNOWN、档案 SINGLE"的不一致，靠文档说明 |
| **G6-D2** | 同来源的多条近似重复算不算"多次出现"？ | ① 算（照 D-109 字面：occurrences=条数）<br>② 不算（按 source_id 去重，occurrences=来源数） | **②**。否则一条材料能自己给自己作证两次，AC-7 被绕过。**这是对 D-109 的收紧细化，需要你确认** |
| **G6-D3** | 分组键取什么？ | ① 归一化 claim + subject 集合 + dimension<br>② 再加上 `applicable_variant_ids` / `canon_level`（照 D-104 全键） | **①**。版本/正典状态不该影响"出现次数"；全键会把同一说法切碎成更小的组，更难达到 REPEATED |
| **G6-D4** | `UNKNOWN` 是否继续当"不进 traits"处理？ | ① 继续（现状，保守）<br>② 接线后 `UNKNOWN` 只会出现在"判定失败"，改为报错 | **①** 本轮不动。判定失败属新缺口，另立 |

---

## 3. G9：`<2` 参与者的 RELATION 证据怎么处理

### 3.1 先分清"<2"的三种成因（现在全被同一个 `continue` 吞掉）

| 类型 | 场景 | 例子（合成材料） | 现状 |
| --- | --- | --- | --- |
| **A. 单主体** | 证据只点名 1 名本次参与者 | "阿雾独自在塔顶守了一夜" | 静默丢弃 |
| **B. 涉及第三人** | 主体含 2 人，但与本次参与者交集 <2 | 请求 A、B，证据讲的是 A、C | 静默丢弃 |
| **C. 主体缺失** | 抽取器没填 `subject_entity_ids` | 主体为空的 RELATION 证据（若登记门禁放行） | 静默丢弃 |

这三种的处理优先级其实不同（B 可能有价值但需要扩大参与者集合；A 根本不是"关系"；
C 是抽取质量问题），现在一律无痕，**运维无法判断该补材料还是该查抽取器**。

### 3.2 三种处理方式对比

| 方案 | 做法 | 优点 | 缺点 | 判断 |
| --- | --- | --- | --- | --- |
| **① 保持丢弃（现状）** | `continue` | 零风险、零改动 | 静默。这正是 G5 刚修过的那类问题（"不允许静默丢弃"） | ❌ 与已确立原则冲突 |
| **② 降级为 note** | 写进 `open_questions`，或塞进 `group_dynamics.note` | 信息不丢、不新增告警码 | **语义失真**：`shared_experiences` 的定义是"共同经历"，单主体证据放进去就是谎报"共同"；放 note 里则既不可筛选也不可计数，等于把结构化信息拍平成散文 | ❌ |
| **③ 明确告警 + 分类计数**（推荐） | 按 A/B/C 各记一条告警，带**数量**与**证据 ID 列表**；不写进任何条目 | ① 可筛选（按码）、可计数（运维能看出"12 条关系证据，9 条是单主体"）；② 不伪造语义、不破坏 RL 契约；③ 与 G5 的处理口径一致 | 要多写 ~30 行 | ✅ |

**为什么坚决不选 ②**：`RelationshipProfile.shared_experiences` 是**结构化断言"这两个人共同经历了什么"**。
把"阿雾独自守夜"放进去，会让下游（未来的对话模拟、关系摘要）以为这是双人事件——
这比"丢失"更糟，是**制造假事实**。AC-15 / RL-V6 的整套纪律都在防这个。

### 3.3 推荐方案 ③ 的具体形态

**新增三个告警码**（在 `business.py` 的告警常量区）：

```python
WARNING_RELATION_SINGLE_SUBJECT = "RELATION_EVIDENCE_SINGLE_SUBJECT"   # A 类
WARNING_RELATION_OTHER_PARTICIPANT = "RELATION_EVIDENCE_OTHER_PARTICIPANT"  # B 类
WARNING_RELATION_SUBJECT_MISSING = "RELATION_EVIDENCE_SUBJECT_MISSING"  # C 类
```

**两处接入点都要改**（否则只修兜底路径，注入分析器时照样静默）：

| 路径 | 位置 | 改法 |
| --- | --- | --- |
| 兜底 | `business.py:1272-1276` | `continue` 前按 A/B/C 归类并累加，循环结束后逐类产**一条**告警（带数量 + 前 N 个 ID） |
| 分析器 | `relationship_analysis.py:372-378` | 在 `analyze()` 内同样归类，纳入已返回的 `warnings` 列表（`RelationshipAnalysisOutcome.warnings` 已存在，`business` 侧已有透传通道） |

**为什么"逐类一条"而不是"每条一条"**：避免告警爆炸（一次运行几百条同型告警会把真正的问题淹掉）。
数量 + 样例 ID 足够定位。

**同时补上单人物悬空（§1.4）**：在 `_step_relationship` 的 SKIPPED 分支之前，
若本次 `evidence_ids` 里**存在** RELATION 维度证据，多记一条告警：

```python
WARNING_RELATION_STAGE_SKIPPED_WITH_EVIDENCE = "RELATION_STAGE_SKIPPED_WITH_EVIDENCE"
# 文案：本次登记了 N 条 RELATION 证据，但参与者只有 1 人（阶段跳过），
#       这些证据不会被任何档案消费——需要建立关系档请提供 ≥2 名参与者
```

这一条解决的是上一轮真实冒烟里"那 1 条 RELATION 证据去哪了"完全不可见的问题。

### 3.4 影响面

| 项 | 影响 |
| --- | --- |
| 现有测试 | **零影响**。已核对：`tests/` 与 `scripts/` 里对告警的断言全是 `assertIn`（宽松），**没有**任何精确集合比对，也没有 `len(warnings) == N` 这类数量断言（`scripts/smoke_p4_4c.py:332` 只是打印） |
| 现有档案内容 | **零变化**。只加告警，不改条目、不改 status、不改 open_questions |
| 单人物真实冒烟结果 | 档案条目与上轮完全一致；任务告警里会多出 1 条 `RELATION_STAGE_SKIPPED_WITH_EVIDENCE` |
| 双人物 dry-run | 关系档案仍是 `INSUFFICIENT_EVIDENCE`（材料里没有双人句），但会多出 N 条 `RELATION_EVIDENCE_SINGLE_SUBJECT` —— **这正是要让运维看到的** |
| 新增测试 | 约 6 项：三类各 1 项（含数量与 ID）、两路径一致（兜底 vs 分析器）、单人物悬空告警、无 RELATION 证据时不报 |

### 3.5 G9 不做的事

- **不**新增"互动页"材料。那是 C 轮冒烟的前置（要真正产出关系条目，材料里必须有
  同时点名两人的句子），属于测试数据设计，与本轮代码口径无关。
- **不**改 `participant_entity_ids` 的下限校验（模块层本来就没有，也不需要加）。
- **不**动 `_has_any_content` / G7 护栏。

### 3.6 需要你拍板的 3 项决策

| 编号 | 决策 | 选项 | 我的建议 |
| --- | --- | --- | --- |
| **G9-D1** | 丢弃还是保留？ | ① 保持静默丢弃<br>② 降级为 note<br>③ 明确告警 + 分类计数 | **③**（理由见 §3.2） |
| **G9-D2** | 告警粒度？ | ① 每条证据一条告警<br>② 每类一条（带数量 + 样例 ID） | **②**。防告警爆炸；数量已足够定位 |
| **G9-D3** | 单人物时的 RELATION 证据要不要单独报？ | ① 不报（保持 SKIPPED 静默）<br>② 报（`RELATION_STAGE_SKIPPED_WITH_EVIDENCE`） | **②**。上一轮真实冒烟里这条证据"消失"了却无人可见，这是最该补的一处可观测性 |

---

## 4. 两项对已验收行为的影响汇总

| 项 | G6 | G9 |
| --- | --- | --- |
| 改动文件 | `business.py`（判定函数 + 签名）、`tests/test_p4_5a_e2e.py`（反转 1 个缺口锁） | `business.py`（告警常量 + 2 处归类）、`relationship_analysis.py`（1 处归类） |
| 产品文件数 | **1** | **2** |
| 档案条目内容 | 多来源时可从"不进 traits"变为"进 traits"（**这是目的**）；单来源零变化 | **零变化** |
| 现有测试 | **1 项必然红**（缺口锁，需反转） | **零影响** |
| 现有 dry-run / 冒烟输出 | 单来源：`single_events` 的 stability 由 `UNKNOWN` → `SINGLE`，其余不变 | 多出告警行 |
| 是否破既有决策 | 需你确认 **G6-D2**（对 D-109 的"occurrences"口径收紧） | 无 |
| 回滚成本 | 低（签名新增可选参数，回退即删） | 极低（纯增量） |

**分层合规**：G6 的判定在 `orchestrator` 层调 `modules.evidence`，方向合规（orchestrator → modules）；
G9 的归类分别在 `orchestrator` 与 `modules.relationship_analysis` 内部，均不新增跨层依赖。
`test_dependency_rules` 不会红。

---

## 5. 建议的实施顺序

1. **G9 先行**（1 轮）：零语义风险、纯可观测性、现有测试零影响。做完后 C 轮冒烟的诊断信息就完整了
   —— 双人物冒烟能直接看到"为什么关系档案是空的"（是没材料，还是材料里没有双人句）。
2. **G6 后做**（1 轮）：先按 §2.8 定完 4 项决策。实施时注意
   **反转 `test_judge_stability_has_no_product_call_site`**，别让它变成假绿灯。
3. **C 轮冒烟最后**：需要在材料里加一份"互动页"（同时点名两人 + 一句对话 + 一句共同事件），
   否则关系条目产出路径仍然验证不到（这是 §3.5 刻意不做的事）。

---

## 6. 本文没有做的事

- 没有改任何代码、测试、脚本；
- 没有调真实 API；
- 没有为 G6 做语义聚类设计（需要模型，属另一个量级）；
- 没有设计"互动页"材料（属 C 轮）；
- 没有提交 Git。

**等你对 §2.8 与 §3.6 共 7 项决策拍板后再实施。**
