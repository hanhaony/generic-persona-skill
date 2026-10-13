# ERROR_HANDLING —— 错误分类、重试、暂停与恢复

- **版本**：v0.1.2（P1.2-A 契约 + P1.2-B 验收回填 + P2.2 配置错误码）
- **状态**：主体待人工验收；§2.2 中 `unknown_kind` / `unknown_filter`（R-2 回填）、`config_invalid`（P2.2 新增）为**实现后补录**，均待人工确认
- **相关文档**：`INTERFACES.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`DECISIONS.md`

> 本阶段只定义规则。不实现代码、不安装依赖、不调用真实服务。

---

## 1. 这份文档解决什么问题

一个长任务要跑几十分钟、调用上百次外部服务，一定会遇到：网络超时、模型限流、数据库锁、预算打满、用户中途按 Ctrl+C。

本文规定四件事：

1. **什么叫"出错"**（哪些其实不算错）
2. **出错后能不能重试**（退避多少次、由谁决定）
3. **钱不够了怎么办**（暂停而不是失败）
4. **中断后怎么接着跑**（不重复花钱、不重复干活）

配套：`INTERFACES.md` §1.4 定义信封，§12 定义任务状态机。本文定义**每个状态下的错误如何处理**。

---

## 2. 错误分类总表

### 2.1 类别（category）

| 类别 | 含义 | 是否可重试 | 是否可恢复（resume） | 典型责任方 |
| --- | --- | --- | --- | --- |
| `VALIDATION` | 输入/数据不合规 | ⛔ | ⛔（需修正后重开任务） | 调用方 |
| `NOT_FOUND` | 引用的对象不存在 | ⛔ | ⛔ | 调用方 / 数据一致性 |
| `STATE_CONFLICT` | 状态不允许该操作（如已 COMPLETED 又要 start） | ⛔ | ⛔ | 调用方 |
| `EXTERNAL_TIMEOUT` | 外部调用超时 | ✅ | ✅ | Provider |
| `EXTERNAL_RATE_LIMIT` | 外部限流 | ✅ | ✅ | Provider |
| `EXTERNAL_UNAVAILABLE` | 外部服务不可用（5xx、连接失败） | ✅ | ✅ | Provider |
| `EXTERNAL_AUTH` | 鉴权失败（Key 错、过期） | ⛔ | ⛔ | 配置 |
| `EXTERNAL_QUOTA` | 供应商配额耗尽 | ⛔ | ⛔ | 配置 / 财务 |
| `STORAGE` | 数据库读写失败、锁冲突 | ✅（读失败与锁冲突） | ✅ | Storage |
| `BUDGET_EXCEEDED` | 预算耗尽 | ⛔ | ✅（续跑） | Orchestrator |
| `CANCELLED` | 任务被用户取消 | ⛔ | ⛔ | Orchestrator |
| `INVARIANT_VIOLATION` | 违反 INV-1~INV-4 等硬约束 | ⛔ | ⛔ | 实现缺陷（严重） |
| `INTERNAL` | 未预期的程序缺陷 | ⛔ | ⛔ | 实现缺陷 |

### 2.2 错误码清单（实现时按此建 `ErrorCode` 常量）

| 错误码 | 类别 | 重试 | 对任务的影响 | 建议的用户提示 |
| --- | --- | --- | --- | --- |
| `VALIDATION.required_field` | VALIDATION | ⛔ | FAILED | 内部参数不完整，请报告该 trace |
| `VALIDATION.namespace_forbidden` | VALIDATION | ⛔ | FAILED | 试图写入不允许的数据类型（多为实现问题） |
| `VALIDATION.tier_quote_missing` | VALIDATION | ⛔ | FAILED | 原话证据缺少原文 |
| `VALIDATION.no_derivation` | VALIDATION | ⛔ | FAILED | 不允许无依据的推断 |
| `VALIDATION.gate_rejected` | VALIDATION | ⛔ | FAILED | 单次行为不得写入稳定性格 |
| `VALIDATION.evidence_refs_insufficient` | VALIDATION | ⛔ | FAILED | 关系缺少证据支撑 |
| `VALIDATION.too_few_participants` | VALIDATION | ⛔ | FAILED | 至少需要两个对象 |
| `VALIDATION.limit_too_large` | VALIDATION | ⛔ | 无（单次调用失败） | 查询条数超限 |
| `VALIDATION.query_empty` | VALIDATION | ⛔ | 无 | 空查询不会发起请求 |
| `VALIDATION.unknown_kind` | VALIDATION | ⛔ | 无（单次调用失败） | 实体种类不在注册表内（P1.2-B 实现后回填） |
| `VALIDATION.unknown_filter` | VALIDATION | ⛔ | 无（单次调用失败） | 查询过滤键不在白名单内（P1.2-B 实现后回填） |
| `VALIDATION.config_invalid` | VALIDATION | ⛔ | 失败（改完重开） | 配置项取值非法或超出允许范围（P2.2 新增，详见 `CONFIG.md` §6） |
| `NOT_FOUND.task` / `.project` / `.source` / `.entity` / `.version` | NOT_FOUND | ⛔ | FAILED | 找不到指定对象 |
| `STATE_CONFLICT.not_pending` | STATE_CONFLICT | ⛔ | 无 | 任务已启动，不能重复启动 |
| `STATE_CONFLICT.not_running` | STATE_CONFLICT | ⛔ | 无 | 任务未在运行中，无法暂停 |
| `STATE_CONFLICT.not_paused` | STATE_CONFLICT | ⛔ | 无 | 只有暂停中的任务能恢复 |
| `STATE_CONFLICT.terminal_state` | STATE_CONFLICT | ⛔ | 无 | 任务已结束 |
| `STATE_CONFLICT.session_ended` | STATE_CONFLICT | ⛔ | 无 | 会话已结束 |
| `STATE_CONFLICT.no_new_evidence` | STATE_CONFLICT | ⛔ | 无 | 没有新证据，不重复蒸馏（省预算） |
| `EXTERNAL_TIMEOUT` | EXTERNAL | ✅ | 重试耗尽 → 见 §6 | 外部服务响应超时 |
| `EXTERNAL_RATE_LIMIT` | EXTERNAL | ✅ | 同上 | 外部服务限流，稍后自动继续 |
| `EXTERNAL_UNAVAILABLE` | EXTERNAL | ✅ | 同上 | 外部服务暂时不可用 |
| `EXTERNAL_AUTH` | EXTERNAL | ⛔ | FAILED | 凭据无效，请检查配置 |
| `EXTERNAL_QUOTA` | EXTERNAL | ⛔ | FAILED | 供应商配额已用尽 |
| `STORAGE.read_failed` | STORAGE | ✅ | 重试耗尽 → FAILED | 读取本地数据失败 |
| `STORAGE.write_failed` | STORAGE | ✅（幂等写） | 重试耗尽 → FAILED | 写入本地数据失败 |
| `STORAGE.conflict` | STORAGE | ✅（乐观锁） | 重试耗尽 → FAILED | 并发写冲突 |
| `BUDGET_EXCEEDED` | BUDGET | ⛔ | **PAUSED（paused_reason=BUDGET）** | 已达预算上限，进度已保存，可之后继续 |
| `CANCELLED` | CANCELLED | ⛔ | CANCELLED | 任务已被取消 |
| `INVARIANT_VIOLATION.*` | INVARIANT | ⛔ | FAILED + 隔离标记 | 违反数据隔离红线，任务已停止 |

### 2.3 明确**不算错误**的情况（关键）

| 情况 | 如何表达 | 任务是否继续 |
| --- | --- | --- |
| 搜索返回 0 条结果 | `Ok(CollectOutcome{fetch_status=NO_RESULT})` | ✅ 继续 |
| 某人资料不足 | `Ok(PersonaView{status=INSUFFICIENT_EVIDENCE})` | ✅ 继续 |
| 发现证据冲突 | `Ok([ConflictRef{...}])`，冲突数 > 0 | ✅ 继续（**禁止因冲突失败**） |
| 来源重复命中去重 | `Ok(SourceRef)` + `warnings=[SOURCE_DEDUP_HIT]` | ✅ 继续 |
| 记忆重复 | `Ok(MemoryRef)` + `warnings=[MEMORY_DEDUP_HIT]` | ✅ 继续 |
| 某个来源抓取失败但其他成功 | `Ok(ResearchOutput)` + `warnings` | ✅ 继续 |
| 记忆过期扫描清理了 N 条 | `Ok(SweepOutcome{expired_ids})` | ✅ 继续 |

> **设计理由**：把这七类当错误，会诱导实现者"为了消掉错误"而去合并冲突、编造缺失字段、或中断整个流程。全部用 `Ok + warnings` 表达，流程既稳定又诚实（D-055）。

---

## 3. `Result` 使用纪律

### 3.1 三条硬规则

| # | 规则 |
| --- | --- |
| EH-1 | **跨模块边界不抛业务异常**。所有可预期的失败都进 `Err(AppError)`。 |
| EH-2 | 只有 `INTERNAL` 与 `INVARIANT_VIOLATION` 允许抛 Python 异常，且必须携带 `trace_ref`。 |
| EH-3 | **禁止吞错**：捕获到 `Err` 后必须做三件事之一 —— ① 处理并继续；② 加上下文后向上返回；③ 记录并返回 PAUSED/FAILED。不允许 `except: pass`。 |

### 3.2 错误传播时的"加上下文"约定

下层错误向上传递时**必须补充**上层语义，但**不得改写**下层 `code` 与 `category`：

```python
# 好的写法
return Err(error.with_context(stage="PERSONA_BUILDING", entity_id=eid))

# 禁止的写法（丢了原始错误、伪造了类别）
return Err(AppError(code="INTERNAL", category=..., message="档案生成失败", ...))
```

### 3.3 异常的边界

```python
try:
    return impl(...)
except AssertionError as exc:            # 程序缺陷 / 不变式被破坏
    audit.append(AuditRecord(level="CRITICAL", trace_ref=ctx.trace_ref, detail=repr(exc)))
    raise                                  # 允许抛出，由 Orchestrator 置 FAILED
```

---

## 4. 重试规则

### 4.1 什么可以重试

```
可重试   ⟺  error.category ∈ {EXTERNAL_TIMEOUT, EXTERNAL_RATE_LIMIT,
                              EXTERNAL_UNAVAILABLE, STORAGE}
         且  该操作具备幂等保证（见 4.3）
         且  attempt_count < max_retries
         且  预算仍有余额
```

**任何一条不满足即停止重试。**

### 4.2 退避与参数位置

| 参数 | 默认值 | 配置位置 |
| --- | --- | --- |
| `max_retries` | 3 | `ProviderConfig`（外部调用）/ `TaskDraft.retry_policy`（任务级） |
| `backoff_base_ms` | 500 | `ProviderConfig` |
| `backoff_max_ms` | 8000 | `ProviderConfig` |
| `jitter` | True | `ProviderConfig` |
| `per_call_timeout_ms` | 30000 | `ProviderConfig` |
| `respect_retry_after` | True | `ProviderConfig` |
| `stage_max_retries` | 2 | `TaskDraft.retry_policy`（阶段级重试） |

退避公式：

```
delay = min(backoff_max_ms, backoff_base_ms * 2 ** attempt)
final = random.uniform(0.5 * delay, 1.5 * delay)   # 抖动，避免惊群
```

**重试本身也算预算消耗**（重试的调用同样记账）——防止无限重试烧钱。

### 4.3 幂等是重试的前提

| 层 | 幂等键 | 语义 |
| --- | --- | --- |
| Provider 单次调用 | `ctx.idempotency_key` | 相同 key 的重复请求不重复计费 |
| 任务内的一个工作单元 | `f"{task_id}:{unit_name}"` | 重复执行直接返回上次结果 |
| 对话轮次 | `f"{session_id}:{index}"` | 重复 commit 返回同一 `turn_id` |
| 来源登记 | `canonical_key` / `content_hash` | 重复登记命中去重，返回已有 id |

**没有幂等键的写操作，一律不允许自动重试**（宁可贵一条，不可脏一片）。

### 4.4 重试耗尽之后

| 场景 | 行为 |
| --- | --- |
| 单个工作单元重试耗尽 | 记录 `last_error` → 该单元标记为失败 → 其余单元继续 → 任务最终可 `COMPLETED` 并带 `warnings`（推荐）或按 `retry_policy` 转 `FAILED` |
| 阶段整体重试耗尽 | 任务 `FAILED`，**保留已完成阶段的产出** |
| 不可重试错误 | 立刻停止该分支；是否 `FAILED` 由 Orchestrator 依据所属阶段的重要性决定 |

---

## 5. 预算控制：检查必须发生在消耗之前

### 5.1 四段式闸门

```
① 预估 estimate(amount)      —— 进入一个工作单元之前
       │  不足 ⇒ 【尚未消耗】⇒ 写检查点 ⇒ status=PAUSED(paused_reason=BUDGET)
       ▼
② 预占 reserve(amount)       —— 防止并发同时通过检查
       ▼
③ 调用 Provider.xxx()        —— Provider 内部发出请求前再查一次余额
       ▼
④ 记账 record_usage() + release_reserve()
       ▼
⑤ 单元完成 ⇒ save_checkpoint(stage, cursor, completed_units, artifact_ids, budget_used)
```

**关键顺序**：`check → reserve → call → account`。第 ① 步**必须在任何资源被消耗之前**完成；一旦不足，**先落检查点再暂停**，保证进度不丢。

### 5.2 预算的单位与阈值

| 项 | 约定 |
| --- | --- |
| 第一版计量单位 | **调用次数（requests）+ token 数**，双指标取严者先触发 |
| 也可配置 | `max_cost`、`max_duration_sec`（可选项，留空即不限制） |
| 告警阈值 | 消耗达 80% 时写入 `warnings=[BUDGET_NEAR_LIMIT]`，**不改变行为** |
| 未知用量处理 | token 未知时**按 1 次调用计**，`tokens_in/out` 留 `None`，绝不估算 |
| 探测调用 | `Provider.health()` **不计入预算** |

### 5.3 超预算 ⇒ 暂停，不是失败（D-043）

```
BUDGET_EXCEEDED
   ⇒ Orchestrator.save_checkpoint(当前已完成单元)
   ⇒ TaskRecord.status = PAUSED, paused_reason = BUDGET
   ⇒ 返回 Ok(StepOutcome{paused=True})        ← 注意：是 Ok
   ⇒ CLI 提示："已达预算上限，进度已保存。提高额度后执行 persona resume <id> 继续。"
```

**为什么不直接失败**：已经花了钱做出的产出不该丢；用户提高预算后应当能接着跑。这就是为什么 §5.1 强调"先落检查点再暂停"。

### 5.4 预算"续跑"的两个前提

1. `TaskRecord.status == PAUSED`（其它状态不可 resume）
2. 用户/系统提高了 `budget_limit`，或已完成单元的产出足以让剩余步骤在新额度内跑完

---

## 6. 任务生命周期：状态、转换与恢复

### 6.1 状态详解

| 状态 | 触发条件 | 检查点 | 可否 resume | 终态 |
| --- | --- | --- | --- | --- |
| `PENDING` | `create_task` 完成 | 无 | — | 否 |
| `RUNNING` | `start` / `resume` 成功 | 持续写入 | — | 否 |
| `PAUSED` | `pause(reason=USER)` / `BUDGET_EXCEEDED` / 依赖未就绪 | 已保存 | ✅ | 否 |
| `COMPLETED` | 全部阶段成功 | 保留 | ⛔ | ✅ |
| `FAILED` | 不可恢复错误且重试耗尽 | **保留**（便于排查） | ⛔ | ✅ |
| `CANCELLED` | 用户主动取消 | 标记 `abandoned` | ⛔ | ✅ |

`paused_reason ∈ {BUDGET, USER, DEPENDENCY}` 独立成字段，**不新增额外的状态名**。

### 6.2 合法转换表

| 从 ＼ 到 | PENDING | RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED |
| --- | :-: | :-: | :-: | :-: | :-: | :-: |
| **PENDING** | — | ✅ start | ⛔ | ⛔ | ⛔ | ✅ cancel |
| **RUNNING** | ⛔ | — | ✅ pause / 超预算 | ✅ 全部完成 | ✅ 致命错误 | ✅ cancel |
| **PAUSED** | ⛔ | ✅ resume | — | ⛔ | ✅ resume 后仍失败 | ✅ cancel |
| **COMPLETED** | ⛔ | ⛔ | ⛔ | — | ⛔ | ⛔ |
| **FAILED** | ⛔ | ⛔ | ⛔ | ⛔ | — | ⛔ |
| **CANCELLED** | ⛔ | ⛔ | ⛔ | ⛔ | ⛔ | — |

非法转换 → `Err(STATE_CONFLICT.*)`，**不改变原状态**（保证状态不会被"改坏"）。

### 6.3 暂停 ≠ 取消

| 维度 | PAUSED | CANCELLED |
| --- | --- | --- |
| 用户意图 | "先停一下，等会儿继续" | "不要了" |
| 检查点 | 保留，`resume_token` 有效 | 标记 `abandoned`，`resume_token` 失效 |
| 已产出证据/档案 | 全部保留 | 全部保留（可被新任务复用，Q-31 推荐） |
| 预算 | 保留剩余额度 | 释放并结算 |
| 后续动作 | `resume` | 只能 `create_task` 新任务（可传入已有产出 ID 避免重做） |
| 是否可逆转 | ✅ | ⛔ |

### 6.4 检查点：写什么、什么时候写

```python
Checkpoint = {
    task_id, stage, cursor,
    completed_units: list[str],   # 幂等单元清单，resume 时据此跳过
    artifact_ids: {source_ids[], evidence_ids[], persona_ids[],
                   relationship_ids[], session_id},
    budget_used: {requests, tokens_in, tokens_out, latency_ms},
    resume_token, saved_at,
}
```

**写入时机**（两者都要有）：

| 时机 | 粒度 | 目的 |
| --- | --- | --- |
| 每个阶段完成时 | 粗 | 快速跳过整个已完成阶段 |
| 阶段内每完成 N 个工作单元（N 默认 1）时，或每轮对话结束时 | 细 | 避免一个大阶段做了 90% 全丢（Q-14 推荐阶段级 + 单元清单） |

`completed_units` 与 `cursor` 一起，让**同一次 resume 不会重复执行同一单元**。

### 6.5 resume 算法（可执行的六步）

```
1. t = tasks.get(task_id); 若 t.status != PAUSED ⇒ Err(STATE_CONFLICT.not_paused)
2. cp = storage.load_checkpoint(task_id); 若为空 ⇒ Err(VALIDATION.checkpoint_missing)
3. missing = storage.check_references(cp.artifact_ids)
   若 missing 非空 ⇒ Err(VALIDATION.artifact_missing)      ← 不允许"假装没做过"
4. t.status = RUNNING（同一事务内写回）
5. for unit in remaining_units:
       unit.idempotency_key = f"{task_id}:{unit.name}"
       若 unit.name ∈ cp.completed_units ⇒ skip（不调用 Provider，不计费）
       否则执行 → 成功则 append 到 completed_units → save_checkpoint
6. 全部完成 ⇒ COMPLETED（如部分失败，按 §4.4 处理，warnings 记录）
```

**避免重复执行的两道保险**：`completed_units` 集合 + 下游幂等键。少了一道都不够——前者防"重复跑"，后者防"重复写入扣两次钱"。

### 6.6 崩溃恢复（非正常中断）

进程被杀、Ctrl+C、断电：

1. 下次启动时扫描所有 `status == RUNNING` 的任务 → 标记 `last_error=PROCESS_INTERRUPTED`
2. 状态回退为 `PAUSED(paused_reason=DEPENDENCY)` —— **因为不知道它到底跑到哪了**
3. `resume` 时按 §6.5 校验 `artifact_ids`：完整则跳过已完成单元；缺 piece 则报错而非假装没做过

> 这就是为什么"先把对话轮次落盘，再写记忆"（`INTERFACES.md` §8 `commit_turn`）——事务保证不会出现"有记忆无出处"的半截状态。

---

## 7. 冲突与降级：什么时候**不**失败

| 事件 | 处理方式 | 是否失败 |
| --- | --- | --- |
| 检测到证据冲突 | 记 `ConflictRecord`，双方证据标 `DISPUTED`，双方均进入相关档案字段 | ⛔ 不失败 |
| 资料不足无法填写某字段 | 字段留空 + `open_questions` | ⛔ 不失败 |
| 单个来源抓取失败 | 记录原因入 `warnings` | ⛔ 不失败 |
| 蒸馏时某维度 extract 失败 | 该维度留空 + warning | ⛔ 不失败 |
| 记忆过期待清理 | 正常扫描处理 | ⛔ 不失败 |
| 对话中遇到冲突主题 | 角色各自表述 + `safety_flags` | ⛔ 不失败 |

**系统可以在输出中谨慎表达不确定性**（"这件事的时间点有不同说法"），但**不得**：

- 把未解决冲突标记成确定事实
- 自动选择其中一方当作真相
- 因为"不好处理"就跳过整个主题且不记录

---

## 8. 隔离违规：严重级别最高

以下情况**立即停止任务**并置 `FAILED`，同时把相关记录打上隔离标记（quarantine），不做任何自动修复：

| 违规 | 检测方式 |
| --- | --- |
| `EvidenceItem` 出现 `namespace=SIMULATED` | 写入闸门 + 全表断言 |
| `Dialogue` 写入了证据/档案/来源事件 | 只读端口协议 + 运行前后表零变化断言 |
| `MemoryItem` 被提升为其它命名空间 | 无此类 API（结构上不存在）+ 扫描 |
| AI 创建了 `SourceEvent` 或把虚构内容标成 `CANON` | 写入路径白名单 |
| 不同改编版本/平行时间线被自动合并 | `merge_policy` 校验 + 断言 |
| 冲突被标记为已解决 | 枚举中无该值 |
| 出现了 `intimacy_score` 类字段 | 字段名静态扫描 |

处理流程：

```
检测到 ⇒ audit(level=CRITICAL) ⇒ task.status = FAILED
      ⇒ 相关实体 quarantine=True（保留现场，不得删除）
      ⇒ 向用户明确报告："发现数据隔离违规，任务已停止，请人工检查。"
```

**不允许**静默修复后继续——那会让红线变成建议。

---

## 9. 可观测性要求

| 要求 | 内容 |
| --- | --- |
| 每次错误必写 `AuditRecord` | `{trace_ref, task_id, module, code, message, level, at}` |
| `trace_ref` 贯穿 | 从 `CallContext` 一路带到日志、错误、CSV 导出；用户报错能凭它定位 |
| 禁止静默吞异常 | `except: pass` 视为缺陷，由 `run_static_checks` 扫描（D-037） |
| 用户可见的错误格式 | 一句话原因 + 一句话建议 + `trace_ref`；不展示堆栈 |
| 进度可解释 | CLI 显示"已完成阶段 / 当前阶段 / 已用预算 / 冲突数"，而不是百分比黑箱 |

---

## 10. 测试要求（每个错误码至少一个用例）

| 测试 | 方法 |
| --- | --- |
| 每个 `EXTERNAL_*` 的行为符合 §2.2 | 用 `FailingProvider` 注入脚本化错误 |
| 重试耗尽后的降级/失败符合 §4.4 | 注入持续失败，断言 `attempt_count` 与最终状态 |
| `BUDGET_EXCEEDED ⇒ PAUSED` 而非 FAILED | 注入超预算，断言 status 与 checkpoint 存在 |
| resume 不重复执行 | 对比恢复前后的 `UsageReport.requests` 与 `completed_units` |
| resume 遇 artifact 缺失时报错而非重跑 | 手工删除产出后 resume，断言 `VALIDATION.artifact_missing` |
| pause 与 cancel 语义不同 | 分别断言检查点可用性、能否 resume |
| 冲突不导致失败 | 构造冲突数据，断言任务 `COMPLETED` 且冲突数 > 0 |
| 隔离违规立即停止 | 断言状态 FAILED + quarantine 标记 |

---

## 11. 与其他文档的对应关系

| 本文条款 | 对应约定 |
| --- | --- |
| §2 错误分类 | `INTERFACES.md` §1.5（三类结果）；`AppError` 定义见 §1.4 |
| §4 重试 | `INTERFACES.md` §2.5 Provider 重试表、§2.6 Mock 家族 |
| §5 预算闸门 | `INTERFACES.md` §12.4；`DECISIONS.md` D-043 |
| §6 生命周期 | `INTERFACES.md` §12.2 状态图；`DATA_MODEL.md` §4.12 TaskRecord |
| §7 冲突不失败 | `DECISIONS.md` D-013 / D-055；`DATA_MODEL.md` §4.6 |
| §8 隔离违规 | `PROJECT_SPEC.md` INV-1~INV-4；`DECISIONS.md` D-022 / D-050 |

---

## 12. 待决定事项

| 编号 | 问题 | 推荐默认值 |
| --- | --- | --- |
| Q-33 | 单个工作单元失败是"跳过继续"还是"任务失败"？ | **默认跳过并记录 warning**，由各任务类型的 `retry_policy` 配置是否为致命 |
| Q-34 | 恢复粒度用"阶段级 + 单元清单"是否足够？ | **足够**（Q-14 推荐），暂不做证据级细粒度 |
| Q-35 | 是否需要"每个对象独立的子预算"？ | **不需要**，第一版按任务整体预算即可 |
| Q-36 | 崩溃恢复是否需要自动扫描重启？ | **是**，启动时扫描 RUNNING 态任务并回退为 PAUSED |

---

## 13. 变更历史

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-10-13 | v0.1 | 初稿：错误分类与错误码表、重试规则、预算四段式闸门、六态转换表、检查点与 resume 算法、隔离违规处置、测试要求 |
| 2026-10-13 | v0.1.2 | §2.2 补两个 P1.2-B 实现后回填的错误码（`VALIDATION.unknown_kind` / `VALIDATION.unknown_filter`，R-2）与一个 P2.2 新增错误码（`VALIDATION.config_invalid`，配置取值非法/超范围） |
