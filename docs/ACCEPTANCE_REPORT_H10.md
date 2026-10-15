# H-10 端到端复验报告（自动抽取路径 · 隔离 Mock · 全程离线）

> 项目：`D:\AIprojects\generic-persona-skill`
> 日期：2026-10-14
> 范围：对已验收的 H-10 业务编排器（含 G-2 自动抽取、G-4 关系研判）做**端到端复验**。
> 运行环境：**全程离线**。未调用任何真实搜索 / 语义模型 / Provider 网络 API，未产生任何费用。
> 结论先行：**未发现产品代码集成缺口**；AC-1 / AC-2 在全流程自动抽取路径下均满足。本轮新增 1 个集成测试文件 `tests/test_h10_e2e.py`（6 项）守护此前**未被覆盖**的「自动抽取 → 下游人格/关系真正看见证据」路径。全量回归见 §4。

---

## 1. AC-1 / AC-2 契约原文 + 逐项结论（任务一）

`docs/PROJECT_SPEC.md` 原文：

| 编号 | 原文 |
| --- | --- |
| **AC-1** | 输入 2 个人名，一条命令可跑完「研究 → 证据 → 人物档案 → 关系档案 → 对话」全流程 |
| **AC-2** | 输入 3 个人名时，关系档案中包含群体互动维度 |

### 1.1 与既有验收记录对照

| 来源 | 记录 | 本轮核对 |
| --- | --- | --- |
| G-2 验收（DECISIONS D-221~D-235） | AC-1 由自动抽取闭合 | ✅ 本轮 e2e 实测复现：2 人最简输入全链 SUCCEEDED |
| G-4 验收（DECISIONS D-236~D-244 + ACCEPTANCE_REPORT_G4） | AC-2 满足（规则实现，非真实语义模型） | ✅ 本轮 e2e 实测复现：3 人 GROUP + 群体维度 + 共现非联盟 |

> **重要方法论（对应用户要求"不得因 G-2/G-4 单测通过就判定全流程通过"）：**
> 既有 `TestAutoClaimExtraction` 用例断言的是「证据**已登记** + 五阶段均 EXECUTED ⇒ SUCCEEDED」，
> 并**没有**断言自动抽取路径下人物档案 / 关系档案**真的读取到了这些证据**。因此单测通过
> **不能**推出下游可见。本轮专门补上这一下游可见性断言（见 §3 场景 1/2）。

### 1.2 AC-1 逐项（最简输入 = 仅 2 个目标人名）

| 子要求 | 结论 | 证据 |
| --- | --- | --- |
| 最简输入：只给目标人名，不传 claim / EvidenceItem / PersonaProfile / RelationshipProfile | ✅ | 测试构造 `evidence_seeds == []`，仅 `targets=[两个实体]` |
| 研究 → 证据 → 人格 → 关系 → 对话 五阶段按序、由编排器聚合 | ✅ | `tests/test_h10_e2e.py::test_minimal_two_names_runs_full_chain_ac1` 断言 `research.run < evidence.add_evidence < persona.create_profile < relationship.create_relationship < dialogue.start_session` |
| 2 人 → PAIR，`group_dynamics is None` | ✅ | 同上用例断言 `cardinality==PAIR` 且 `group_dynamics is None` |
| 全流程成功（RUN_SUCCEEDED） | ✅ | 同上用例断言 `status==RUN_SUCCEEDED` |

### 1.3 AC-2 逐项（3 个目标人名）

| 子要求 | 结论 | 证据 |
| --- | --- | --- |
| ≥3 人 → GROUP 关系档案且**含群体互动维度** | ✅ | `test_minimal_three_names_group_and_cooccurrence_not_alliance_ac2` 断言 `cardinality==GROUP` 且 `group_dynamics is not None` |
| 仅有"共同出现"证据时，联盟/排斥/传话**不臆造** | ✅ | 同上用例断言 `alliances/exclusion/relay` 均为空 + note 说明证据不足（D-215 硬约束 / D-239 红线） |
| 关系真的基于 ≥1 条 RELATION 证据（下游可见） | ✅ | 同上用例正则解析关系阶段消息，断言 RELATION 证据条数 ≥1 |

> **结论：AC-1 / AC-2 均满足，未通过修改验收定义宣称通过。**

---

## 2. 最小输入示例 + 实际端到端调用链（任务二）

最小输入（业务入口 `BusinessOrchestrator.run(ctx, request)`）：

```python
# 只给目标人名（实体 ID），不传任何 claim / 证据 / 档案
ids = [fx.entity("阿雾"), fx.entity("阿灯")]        # 关键：实体只建一次，fixtures 与 request 共用同一组 ID
request = BusinessRequest(
    targets=[fx.target(ids[0], "阿雾"), fx.target(ids[1], "阿灯")],
    max_rounds=2)                                    # evidence_seeds 为空（默认 None）
result = unwrap(orch.run(ctx, request))
```

实际调用链（3 人场景，隔离 Mock，`record=True` 记录真实调用顺序）：

```
research.run                              → 登记 3 条来源，带回 3 份材料
evidence.add_evidence   × 9               → 从候选 claim 登记 9 条非推断证据
evidence.record_inference × 1             → 1 条有根推断（EV-V3 守卫）
persona.create_profile    × 3             → 3 份证据驱动人物档案（status=OK，非 INSUFFICIENT）
relationship.create_relationship × 1      → GROUP 关系档案（3 条 RELATION 证据支撑）
dialogue.start_session + dialogue.run     → 模拟对话（命名空间恒 SIMULATED）
```

输出：`status=SUCCEEDED`，`task_status=COMPLETED`，`evidence=10`，`persona=3`，`relationship=1`，`session` 1 个、`turn=2`，`conflict=1`。

---

## 3. 分阶段可验证证据（任务二 · 逐阶段）

| 子需求 | 场景 / 测试 | 实测结果 |
| --- | --- | --- |
| (1) 最简输入 | §1.2 用例 | `evidence_seeds==[]` 全链跑通 |
| (2) 阶段顺序 + 编排器聚合 | §1.2 用例 | 顺序断言通过；五阶段 EXECUTED |
| (3) 全部主张 / 关系判断可追溯到正确 source + evidence ID；Mock 材料不与真实数据混 | `_trace_relationship` + `test_every_registered_evidence_traces_to_a_research_source`（既有） | 关系条目引用 100% 落在本次 `evidence_ids` 内（不可追溯引用数=0）；证据 source_id 均在本次来源内 |
| (4) 共现不得触发联盟/排斥/传话 | §1.3 用例 | 三板块均空，写"证据不足"note |
| (5) 证据不足 / 来源矛盾 / 无材料 / 无可抽取主张 有明确安全结果 | `test_no_material_safe_partial_not_completed`、`test_contradiction_preserved`、既有 `test_no_claim_in_material_is_not_faked` | 无材料→证据 SKIPPED + 整链 PARTIAL（0 证据，不伪造）；矛盾→两种正典级别各自保留（不裁决不合并） |
| (6) Mock Provider 异常 / 预算 / 暂停恢复 / 重复调用 + 幂等 | `test_provider_failure_is_failed_not_wrongly_completed`、`test_idempotent_rerun_no_duplicate_evidence` + 既有 `test_task_faults.py` / `test_extractor_error_is_propagated_not_swallowed` | 抽取器返回 Err→整链 FAILED（非 COMPLETED）；重复调用证据集合不变（幂等）；暂停/恢复/预算由既有 TaskRunner 用例覆盖 |
| (7) 失败阶段不得误标 COMPLETED；PAUSED/FAILED/CANCELLED 语义区分 | `test_provider_failure_is_failed_not_wrongly_completed` + `test_no_material_safe_partial_not_completed` | 抽取失败→证据阶段 `STEP_FAILED`，后续阶段 `STEP_NOT_REACHED`，任务机 `FAILED`；无材料→证据 `STEP_SKIPPED` 且不在"已执行"集合，业务 `RUN_PARTIAL`（干净收敛为 `COMPLETED` 而非故障态） |
| (8) 回归不破坏已验收模块 | 见 §4 | 全量套件通过 |

> 关于 (6) 的暂停/恢复/预算：这些能力由 `TaskRunner` + `TaskService` 的既有测试
> （`tests/test_task_faults.py` 及 `TestAutoClaimExtraction` 幂等用例）守护；本轮 e2e
> 复验聚焦"自动抽取 → 下游可见"这一此前未覆盖的集成面，未重复造轮子、未扩大范围。

---

## 4. e2e + 全量回归：命令、通过/失败计数、结果（任务二 · 子需求 8）

| 命令 | 结果 |
| --- | --- |
| `python -m unittest tests.test_h10_e2e -v` | **Ran 6 tests — OK**（6 通过，0 失败） |
| `python -m unittest discover -s tests -t .` | **Ran 728 tests in 531.0s — OK**（728 通过，0 失败，0 错误） |

> 复验所用 Python：`C:\Users\36135\.workbuddy\binaries\python\versions\3.13.12\python.exe`（stdlib only，无第三方依赖）。
> 计数说明：722（既有）+ 6（本轮 `tests/test_h10_e2e.py`）= **728**。运行末段的 `ResourceWarning: unclosed database` 为既有用例未显式关闭 SQLite 连接导致的**非致命告警**（历史存在，与本轮改动无关），不影响 OK 结论。

---

## 5. 本轮修改的文件 + 每个的理由（任务三）

| 文件 | 动作 | 理由 |
| --- | --- | --- |
| `tests/test_h10_e2e.py` | **新增** | 关闭"自动抽取 → 下游人格/关系真正看见证据"的集成覆盖缺口；此前用例只断言证据已登记，不断言下游可见。仅新增测试，不改任何已验收模块。 |
| `docs/ACCEPTANCE_REPORT_H10.md` | **新增** | 本报告。 |
| （产品代码） | **零改动** | e2e 复验未发现产品集成缺口。先前一度观察到的"下游 0 证据"经定位是**演示脚本接线 bug**（`entity()` 不去重，`_plain_request` 重新建了一批实体 ID，与抽取器 fixtures 用的不是同一组），非产品缺陷；用同一组实体 ID 后全链 SUCCEEDED、下游可见。 |

> 未重写任何已验收模块、未降低任何断言、未删除任何失败测试、未改无关文件、未删除用户数据、未访问网络、未调用真实模型 / 搜索 API、未产生费用。

---

## 6. 能力边界（Mock / 规则分析器 / 真实网络研究 / 真实语义模型）

| 层 | 本轮实际使用 | 能力边界 |
| --- | --- | --- |
| Mock Provider（Search / Chat） | ✅ 使用（确定性离线） | 只返回合成虚构材料；**不触网** |
| Mock Claim Extractor（G-2） | ✅ 使用（按 `canonical_key` 查表） | 规则式候选 claim；**不是**真实模型抽取 |
| Rule Relationship Analyzer（G-4，`is_semantic_model=False`） | ✅ 使用（离线规则） | 确定性规则研判；**不是**真实语义模型；共现不升级为联盟/排斥/传话 |
| 真实网络研究 | ❌ 未使用 | 需注入真实 SearchProvider；本轮全程离线 |
| 真实语义模型（关系 / 抽取） | ❌ 未使用 | `ProviderRelationshipAnalyzer` 返回 `EXTERNAL_UNAVAILABLE` 占位；群体级语义需真实模型（缺口 H-17） |

---

## 7. 剩余缺口 + 下一步建议

| 缺口 | 现状 | 建议下一步 |
| --- | --- | --- |
| **H-17 真实语义模型** | 关系 / 抽取当前为规则 + Mock 实现 | 接入真实模型 Provider，保留 RL-V2/V3/V5/V6 与 D-215/239 红线不变 |
| **群体互动维度自动填充** | 共现不臆造；仅命中显式标记才填充 | 真实语义模型上线后评估群体级结盟/排斥/传话 |
| **Q-01~Q-03 未拍板项** | 维持现状 | 人工拍板后再决定是否扩展 |

> 本轮总结：端到端复验未发现产品集成缺口，AC-1/AC-2 满足；新增 1 个集成测试守护关键下游可见性路径；未改动任何已验收产品代码。建议人工确认是否将 `tests/test_h10_e2e.py` 一并纳入版本库。

---

## 8. Git 提交状态（供人工验收核对）

| 项 | 值 |
| --- | --- |
| 已提交（统一提交） | commit **`ba1fc48`** — P2.4 / P3.1~P3.6 / P5 / H-11A / H-10A / G-2 / G-4 全部改动，37 files，+21942/−196 |
| 提交前确认 | 无密钥、无本地数据库（`.db`/`.sqlite`）、无临时文件入库 |
| **未提交**（留待人工验收决定） | `tests/test_h10_e2e.py`（本轮新增，6 项，守护自动抽取下游可见性）<br>`docs/ACCEPTANCE_REPORT_H10.md`（本报告） |

> 依据任务三约束「不要自动提交 Git」与「完成后停止，等待人工验收」，本轮复验新增的两个文件**未纳入** `ba1fc48`，交由人工决定是否补充提交。
