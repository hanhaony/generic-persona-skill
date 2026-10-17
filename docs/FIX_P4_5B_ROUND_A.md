# P4.5-B 缺口修复 · A 轮报告

> 范围：G7（A 级写风险）、G8（双人物真实运行入口）、G10（`--dump` 落点）
> 约束：不装依赖、不调真实 API、不改无关文件、不自动提交 Git
> 产物：3 个产品文件 + 1 个脚本 + 1 个测试文件（共 +522 / −49）

---

## 0. 结论速览

| 项 | 结果 |
| --- | --- |
| G7 空产出降级覆盖 | **已修复**（product: `persona.py` / `relationship.py` / `business.py`） |
| G8 双人物真实运行入口 | **已接线**（仅 `scripts/`，产品代码零改动） |
| G10 `--dump` 落点 | **根因已修**（落点统一进系统临时目录，断言未改） |
| `tests/test_p4_5a_e2e.py` | **60 项全绿**（47 → 60，新增 13） |
| 全量回归 | **1182 项全绿**（`var/` 残余闭环后，见 §3.3） |
| 历史 `var/` 残留 | **已按方案 A 闭环**：证据迁至临时目录、字节校验一致后删除 |
| `--dry --path=orchestrator` | 退出码 0、预检全通过、**零真实请求** |
| `--dry --names 阿雾,阿灯` | 退出码 0、**零真实请求** |
| 真实 API 调用 | **0 次**（本轮全程未联网调模型） |

---

## 1. G7：修订的"空产出"不得降级覆盖（A 级写风险）

### 1.1 根因（不是"没内容"，是"内容不相等"）

`revise` 原有两道闸门：

1. `change_reason` 必填（D-112）；
2. **内容与最新版完全一致 → `Err(STATE_CONFLICT.no_new_evidence)`**（不生成空转版本）。

第 2 道挡不住本次的场景：**旧版有 1 条、新版 0 条**，两个内容指纹**不相等**，
于是被判定为"有变化"，写出 v2（空），并把 v1 的 `superseded_by` 指向 v2。
而 `get_persona(version=None)` 取的是 **version 最大者** ⇒ 默认读取拿到空档案。
v1 的行确实还在库里（旧版永不删除），但**事实上已被降级覆盖**。

P4.5-B 审查实测（修复前）：

| 档案 | v1 | v2（重跑产生） | 结果 |
| --- | --- | --- | --- |
| 关系档案 | `OK shared=1` | `INSUFFICIENT_EVIDENCE shared=0` | `superseded_by` 覆盖 v1 |
| 人物档案 | `OK items=1` | `INSUFFICIENT_EVIDENCE items=0` | 默认读取变空 |

触发条件极普通：**重跑同一条链**。证据登记被幂等拦下 ⇒ 本次没有可用证据
⇒ 编排器按"本次证据"重建草稿 ⇒ 空草稿。也就是说，**任何一次无害重跑都会削掉档案**。

### 1.2 修复口径

在 `persona.PersonaService.revise` / `relationship.RelationshipService.revise` 的
"内容一致"判定之后、写新版本之前，加一道**窄**护栏：

```
新草稿没有任何内容  且  最新版有内容  ⇒
    不写新版本
    不动旧版的 superseded_by
    返回**旧版**的 Ref + 专属告警
```

新增告警码：

- `PROFILE_DEGRADED_SKIPPED`（`persona.py`）
- `RELATIONSHIP_DEGRADED_SKIPPED`（`relationship.py`）

新增行级判定 `_has_any_content_row(row)`：与既有 `_has_any_content(draft)` 同口径，
只是作用在存储行的原始 dict 上（人物：两个文本板块 + 四个条目板块；
关系：五个条目板块 + 视角块 + 群体互动）。

**为什么返回 `Ok` 而不是 `Err`**：

- 这属于"本次没有新证据"，不是失败（D-008：如实降级，不报错）；
- `with_warning()` 只接受 `Ok`，`Err` 带不了告警——而本缺口的要求就是"产告警"。

### 1.3 编排器侧：告警必须能看见

只改模块不够：`business._write_persona` 原来只返回 `(profile_id, already_current)`，
模块告警会被丢掉（与 G5 同型问题）。因此：

- `_write_persona` / `_write_relationship` 增加**可选** `notes` 参数，把
  `revised.warnings` 原样追加到步骤告警；
- `already_current` 改为按**版本号是否前进**判定（拦下时返回的是旧版，版本号不变）；
- 只有当模块没给出更具体的说明时，才补通用的 `PROFILE_ALREADY_CURRENT`，
  避免"一次事件两条告警"。

`notes` 是可选参数，既有调用方（`scripts/`、`tests/`）签名不受影响。

### 1.4 护栏必须窄（有测试锁住）

旧版本**本来就没内容**时，正常修订不得被误拦：

- v1 空（只有 `open_questions`）→ revise 一条不同的空草稿 → **仍照常产生 v2**；
- 覆盖 `test_revise_only_open_questions_change_is_allowed` 等既有用例（全绿）。

### 1.5 回归证据（整链实测，非单元）

`[7b] 幂等重跑` 段落里，重跑后的告警码已包含 `PROFILE_DEGRADED_SKIPPED`：

```
重跑整体状态 = PARTIAL；证据 0 条；
告警码 ['MATERIAL_SUPERSEDED', 'MODEL_CALL_DUPLICATE_SKIPPED', 'NO_CANDIDATES',
        'NO_EVENTS', 'NO_EVIDENCE_FOR_ENTITY', 'PROFILE_DEGRADED_SKIPPED']
· 阿雾 人物档案版本 = v1（status=OK，旧版内容未降级）
```

脚本内新增断言：重跑后每个人物的档案版本仍为 **v1**、关系档案版本仍为 **v1**。
这条断言在未来的真实冒烟里同样生效。

---

## 2. G8：双人物真实运行入口（`--names`）

### 2.1 改动（仅在 `scripts/e2e_p4_5a.py`，产品代码零改动）

```bash
# 单人物（默认，行为与旧版逐字一致）
python scripts/e2e_p4_5a.py --run --path=orchestrator

# 双人物全链
python scripts/e2e_p4_5a.py --run --path=orchestrator --names 阿雾,阿灯
```

`run_orchestrator_real(..., names=None)` 的差异点（`None` = 单人物 = 旧行为）：

| 环节 | 单人物 | 双人物 |
| --- | --- | --- |
| 桩数据源 | `StubSearchExtractProvider` | `StubTwoTargetSourceProvider`（按检索词里的目标名返回各自正文） |
| 研究上限 | `max_queries=1 / max_extracts=1` | `max_queries=2 / max_extracts=2` |
| 请求目标 | 1 个 | 2 个 |
| 真实模型调用 | 1 次 | **仍 1 次**（抽取器一次处理全部材料与目标） |
| 对话阶段 | SKIPPED（桩零调用） | EXECUTED（桩对话 Provider 被调用） |

顺手修掉一处硬编码：阶段表把"关系档案（单人物 → 跳过）"写死在标签里，
双人物下会自相矛盾（状态列明明 EXECUTED）。标签改为中性的"关系档案 / 模拟对话"，
隔离语义交给状态列与阶段说明。

### 2.2 离线实测（桩模型 + 断网护栏，零成本）

```
[7] 真实执行（编排器路径：产品代码全链，双人物，1 次调用，失败不重试）
  目标实体     : 阿雾=pe_9d91…、阿灯=pe_e7cb…（双人物）
  数据源       : StubTwoTargetSourceProvider
  研究上限     : max_queries=2 max_extracts=2
  整体状态     : SUCCEEDED（五阶段全执行）
  材料流       : 2 份（带门禁结论 2，已绑定来源 2；254 字 / 249 字，均 VALID）
  证据数       : 10（候选 10，拒收 0，pending 0）
  ④ 关系档案   : rlp_2759… v1 status=INSUFFICIENT_EVIDENCE 共享经历=0 互动模式=0
  ⑤ 人格档案   : 2 份（阿雾 habits=2 single_events=2；阿灯 habits=2 single_events=2）
  模拟对话     : EXECUTED（生成 1 轮）
  真实调用     : 1 次
```

**如实说明一个仍然存在的空洞**：现有两份合成材料**没有任何一句同时点名两人**，
所以抽取阶段不会产出覆盖 ≥2 参与者的 `RELATION` 证据，关系档案只能是
`INSUFFICIENT_EVIDENCE`（告警 `NO_RELATION_EVIDENCE`）。链路是通的（阶段 EXECUTED），
但**关系条目产出路径仍未被验证**。要真正跑出关系条目，需要新增一份"互动页"材料
（含对话或共同事件，且明确点名两人）。这件事与 G9 的"<2 参与者的 RELATION 证据
如何处理"同源，**归入 B 轮方案 + C 轮冒烟**，不在本轮擅自加材料。

---

## 3. G10：`--dump` 落点不得进仓库

### 3.1 根因

上一轮冒烟按文档执行了 `--dump var/p45a_last_run.json`，于是在**仓库里创建了 `var/`**，
把 `test_all_data_in_temp_dir_not_user_data`（断言仓库内不得出现用户数据目录）弄红。
**断言本身是对的**，错的是脚本允许产物落进仓库。

### 3.2 改法（不改断言）

新增 `resolve_dump_path(given)`：

| 输入 | 解析结果 |
| --- | --- |
| 未给 `--dump`（空串） | `<系统临时目录>/p45a_last_run.json` |
| 相对路径 `var/p45a_last_run.json` | `<系统临时目录>/var/p45a_last_run.json` |
| 绝对路径 `D:/tmp/x.json` | 原样尊重 |

启动横幅会打印落点，例如：

```
产物落点：C:\Users\…\AppData\Local\Temp\p45a_last_run.json（系统临时目录；仓库内不落任何文件）
```

### 3.3 残留闭环（已按方案 A 执行）

`var/` 里是**修复前**那次冒烟留下的两份证据（共 14.6 KB），不是本轮产生的：

```
var/p45a_last_run.json      10082 B
var/p45a_last_run.raw.json   4560 B
```

收尾方式（方案 A，已执行）：

1. 复制到 `<系统临时目录>/p45a_evidence/`；
2. **三重校验**：`sha256` 一致 + 大小一致 + `cmp` 二进制逐字节一致；
3. 校验通过后删除两个文件，再 `rmdir var/`（不做 `rm -rf`，删除范围严格限定为这两个已校验文件）；
4. 重跑全量回归 → **1182 项全绿**，原失败项 `test_all_data_in_temp_dir_not_user_data` 转绿。

迁移后的证据位置与校验值（**证据未丢失**）：

```
C:\Users\<user>\AppData\Local\Temp\p45a_evidence\p45a_last_run.json
  sha256 a56e52574382b1dc1b744be542f886d821620bf873ca03bbc604b3d2aa4bf53c  10082 B
C:\Users\<user>\AppData\Local\Temp\p45a_evidence\p45a_last_run.raw.json
  sha256 04d48883092a3641d4e52557b1da636b2c3b63d297276791ac9465144efe9505   4560 B
```

> 断言本身**没有改**——`var/` 消失后它自然通过，这印证了修的是根因（`--dump` 落点），而不是把红灯刷绿。

---

## 4. 测试与回归

### 4.1 新增测试（13 项）

| 类 | 覆盖 |
| --- | --- |
| `TestG7DegradedRevisionSkipped`（5） | v1 有内容且无归档指针；空修订返回旧版 + 专属告警；编排器透传告警；旧版为空时护栏不误拦；关系档案同口径 |
| `TestG8DualPersonEntry`（4） | 默认单人物；`--names` 拆分/去空/拒绝空串；双人物整链离线跑通；单人物行为未变 |
| `TestG10DumpPath`（4） | 默认落点在临时目录；相对路径不进仓库；绝对路径被尊重；CLI 默认值组合均不在仓库内 |

另在既有用例 `test_orchestrator_real_run_end_to_end_offline` / `test_rerun_never_adds_a_second_real_call`
上追加两处断言：重跑后档案版本不变、输出含 `PROFILE_DEGRADED_SKIPPED`。

### 4.2 结果

```
tests.test_p4_5a_e2e          60 项  OK（47 → 60）
全量 discover                 1182 项  OK（198.9 s，0 失败 0 错误）
```

`var/` 残留闭环后原失败项转绿：

```
test_all_data_in_temp_dir_not_user_data ... ok
  assertFalse((REPO_ROOT / "var").exists())   ← var/ 已删除，见 §3.3
```

回归日志写在 `<系统临时目录>/p45b_round_a_full_regression.log`，
**不落仓库**（与 G10 同一口径）。

### 4.3 分层合规

全量套件包含 `test_dependency_rules` / `test_imports` / `test_core_contracts`，
均通过：本轮没有引入任何跨层依赖（改动仅落在 `modules` 与 `orchestrator` 的既有调用方向内）。

---

## 5. 改动清单

| 文件 | 规模 | 内容 |
| --- | --- | --- |
| `persona/modules/persona.py` | +32 | 告警码、G7 护栏、`_has_any_content_row` |
| `persona/modules/relationship.py` | +46 | 告警码、G7 护栏、`_has_any_content_row` |
| `persona/orchestrator/business.py` | +36/−10 | 告警透传、`already_current` 按版本号判定、去重通用告警 |
| `scripts/e2e_p4_5a.py` | +195/−49 | `--names`、双人物接线、`resolve_dump_path`、阶段表标签中性化、G7 回归断言 |
| `tests/test_p4_5a_e2e.py` | +262 | 13 项新测试 + 2 处断言 |

未改动：`persona/cli/`、`persona/providers/`、`persona/storage/`、`docs/` 下既有文件。

`var/` 已按 §3.3 迁移并删除（证据保存在系统临时目录，字节校验一致）。

---

## 6. 下一步

- **B 轮（先出方案）**：G6（`judge_stability` 如何接线）+ G9（<2 参与者的 RELATION 证据
  该如何处理：丢弃 / 降级为 note / 记告警）。
- **C 轮（另行授权）**：双人物真实冒烟。命令已就绪：

```bash
python scripts/e2e_p4_5a.py --run --path=orchestrator --names 阿雾,阿灯
# 产物自动落在 <系统临时目录>/p45a_last_run.json
```

> 提醒：C 轮若要在关系档案里看到真实条目，必须先有"互动页"材料（见 §2.2）。
> 否则 `--run` 的诚实结果是"关系档案 INSUFFICIENT_EVIDENCE"，而不是"链路没通"。
