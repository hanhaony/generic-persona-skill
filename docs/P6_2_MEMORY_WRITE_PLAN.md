# P6.2 记忆写入接进对话 —— 方案

- 状态：**P6.2-a 已落地**（引擎 + 测试 + `write_simulated` 去重）；**P6.2-b 已落地**
  （`rule` 策略 + `run_round` 接线 + 评估链路 `none` 硬守卫，见 §11）
- 前置：Step 2 已正式完成（RUNNING §5.38）；P6.1 剩余项全部暂缓/低优先级/TODO
- 一句话结论：**能用"零模型调用"的确定性规则做，不需要每轮多花一次抽取钱**；
  真正的风险不在规则写得好不好，而在**产品路径有没有人真去调它**（D 家族第 N 例）。
- ⚠️ **召回设计边界（不是 bug）**：确定性规则**召回有限**——离线探针实测 **7/13**
  （13 句里只记 7 句），precision 7/7。不记的三类是故意保留的：整句无第一人称、
  未来计划/意愿、纯情绪/请求。**不要把"它没记住"当缺陷报**；要提召回就得走
  §4.2 的升级路径（LLM 抽取 = 独立的 prompt 版本 + 走同一道闸门），那会重新引入
  成本与幻觉风险，必须单独立项。
- 四个决策点已由用户 2026-10-20 全部拍板：① 新增 `rule` 策略（默认仍 `none`）
  ② 本期做最小 `persona chat` ③ dedupe 修在 `write_simulated` ④ 接受
  `source_turn_id` 挂角色轮。

---

## 0. 先摆事实：写记忆的管道**已经修好了，只是没人拧开关**

这是本方案最重要的前提，否则会重复造轮子。

| 能力 | 现状 | 位置 |
|---|---|---|
| 会话级模拟记忆写入 | **已实现** | `MemoryService.write_simulated(ctx, draft)` `persona/modules/memory.py:300` |
| 写入闸门（策略 + 显式候选） | **已实现** | `DialogueService.commit_turn(..., memory_candidates=)` `dialogue.py:708` |
| 候选合法性约束 | **已实现**：`scope` 必须 `SESSION`（INV-1）；`namespace`/`source_type` 强制 `SIMULATED`/`SIMULATED_DIALOGUE`；`source_turn_id` 必填 | `dialogue.py:753-778`、`memory.py:300-320` |
| 同事务（Turn → Memory → Audit） | **已实现** | `commit_turn` 内 `storage.transaction` `dialogue.py:832` |
| 幂等（同 `(session,index)` 不重复写） | **已实现 + 有测试** | `tests/test_dialogue_memory_integration.py::test_same_turn_committed_twice_writes_memory_once` |
| 检索 + 注入（R1 本会话 / R2 跨会话按 topic） | **已实现**，上限 `MEMORY_CONTEXT_LIMIT = 5` | `dialogue.py:1141-1185` |
| **自动产生候选**（"什么值得记"） | ❌ **完全没有** | —— |
| **产品入口**（谁会带着候选调 `run_round`） | ❌ `persona chat` 仍是占位（"尚未实现"） | `persona/cli/main.py:271` |

**所以 P6.2 的真实内容 = 写两样新东西 + 一条产品路径**：① 规则引擎（纯函数）；
② 一个"声明式开关"（会话创建时选策略）；③ 让某条产品命令真的用上它。

**同时要正视一条已有硬约束**：`tests/test_dialogue_memory_integration.py:502`
的 `test_full_automatic_write_never_happens` 断言"多轮对话不给候选 → 一条记忆都不写"。
这条测试是 **D-179 的守卫**，P6.2 **不许弄坏它**——默认路径必须依旧零写入。

---

## 1. 设计原则（三条，先定下来再谈规则）

1. **只记用户说的话，绝不记模型自己说的话。**
   模型输出是**扮演**，不是事实。一旦把模型的话写进记忆，下一轮又把它当上下文注入，
   就会形成"它编 → 它记 → 它更确信"的回声室，且会污染后续所有会话。
   → 规则引擎的函数签名**只收 `user_message`**，物理上拿不到模型回答。
2. **记忆内容必须是用户原文的切片（verbatim），不许改写。**
   一改写就等于引入了第二个"生成器"，幻觉从这条路又溜回来。
   → 不变量：`candidate["content"]` 必须是 `user_message` 的子串。这是可 fuzz 的硬断言。
   顺带好处：`confidence_means` 可以理直气壮写"用户单轮自述"，不需要模型自评置信度。
3. **默认不写，写在会话创建时就声明。**
   延续 D-179 的口径：策略缺省 `none`。要写必须**开会话时**选，事后不可自动升级。

---

## 2. "什么值得记"的规则设计（确定性，零模型调用）

### 2.1 三个类别 → 映射到已有 `MemoryKind`

| 规则 | 触发条件 | `kind` | 例子 |
|---|---|---|---|
| R-EPI 事件 | 第一人称 + 时间/动作 cue（`昨天`/`今日`/`那天`/`后来`/`我去了`/`我读了`/`我梦见`） | `EPISODIC` | "昨天我去城南看了桃花" |
| R-SEM 偏好 | 第一人称 + 稳定语气 cue（`我一向`/`我素来`/`我最喜欢`/`我最讨厌`/`我从不`/`我惯常`） | `SEMANTIC` | "我素来不爱那些应酬话" |
| R-REL 关系变化 | 态度变化 cue（`我现在觉得`/`我改主意了`/`我不再`/`我从前以为`）+ 参与者指称 | `RELATION_DELTA` | "我现在觉得你比从前可亲" |
| R-EXPL 显式指令 | `记住`/`记着`/`别忘了`/`你记得` | `SEMANTIC`（`salience=0.9`，优先） | "记住我三月要去扬州" |

### 2.2 七条否决（**先否决、后肯定**，顺序不可交换）

| # | 否决条件 | 为什么必须有 |
|---|---|---|
| N1 | 问句（`？`/`?` 结尾，或 `吗/呢/什么/怎么/为何` 开头且无陈述） | 问题不是事实，记下来就是噪音 |
| N2 | 寒暄/应答（`你好`/`谢谢`/`嗯`/`好的`/`哈哈`，或长度 < 6） | 否则记忆表会被"嗯"灌满 |
| N3 | 回指模型上轮的话（`你刚说`/`你刚才`/`你上面`/`你说过`） | **防幻觉洗白**：用户复述模型的话时，不能反手记成"用户提供的事实" |
| N4 | 与档案条目高重叠（与 `traits`/`background`/`expression_style` 的 4-gram 重叠 ≥ 0.6） | 档案已有的东西再记一遍 = 上下文里同一事实出现两次，浪费 token 且放大权重 |
| N5 | 敏感标识（手机号/身份证/邮箱/银行卡正则） | 隐私：**丢弃 + 记审计（不落内容）** |
| N6 | 长度 > 200 字 | 不截断（截断会改语义），直接丢 |
| N7 | 纯元话语（`我们开始吧`/`继续`/`接着来`） | 无信息量 |

### 2.3 候选的字段长什么样（对照 `write_simulated` 的 8 个必填字段）

```python
{
  "kind": "EPISODIC",
  # 含 cue 的那一句，句级切分（。！？；\n）后取原句，≤120 字；**是原文切片**
  "content": "昨天我去城南看了桃花",
  "confidence": 0.4,                    # 固定值，不装"我很确定"
  "confidence_means": "用户单轮自述，未经核实（规则 v1：R-EPI 事件）",
  "salience": 0.3 + 0.2(时间cue) + 0.2(情感词) [+0.4 若 R-EXPL]，clamp [0,1],
  # 以下由 commit_turn / write_simulated 补齐，规则引擎**不填**
  # scope=SESSION、scope_id、source_turn_id、related_entity_ids、namespace、source_type
}
```

注意两个实现细节（都容易被忽略）：
- `memory.py` 对 `confidence` / `salience` **只做 `float()`，不校验区间**
  （`memory.py:880`），所以 clamp 必须由规则引擎自己做。
- `write_simulated` **不去重**（它没调 `_find_duplicate`；只有 `write()` 调了，`memory.py:260`）
  → 同一句话重复出现会写两条。见 §6 决策点 ③。

### 2.4 三条硬上限（防膨胀）

- 每轮最多 **2 条**候选；
- 每会话最多 **20 条**写入（超限只记审计，不写）；
- 注入侧已有 `MEMORY_CONTEXT_LIMIT = 5`，不动它——**写多读少**是刻意的：
  记忆是账本，上下文只取最相关的 5 条。

---

## 3. 接线点：三个选项，推荐 B

| 选项 | 做法 | 代价 | 结论 |
|---|---|---|---|
| A | 给 `add_user_message` 加候选 + 闸门 + 幂等 | 语义最正（`source_turn_id` 就是用户那轮），但 `add_user_message` **现在既无闸门也无幂等**（`dialogue.py:840`），要补两套机制，动契约面大 | 后续可选 |
| **B（推荐）** | 在 `run_round` 里由规则引擎从 `user_message` 生成候选，透传给**角色轮**的 `commit_turn` | **零契约改动**：`run_round` 已有 `memory_candidates` 参数（`dialogue.py:890`）；写入仍在 `commit_turn` 的同一事务里 | **本期采用** |
| C | 轮次提交后再单独写记忆 | 破坏 §8.2"同事务"，且失败会留下"有轮次没记忆"的半截 | ❌ 否决 |

**B 的已知近似（要在文档和审计里写明）**：`source_turn_id` 指向**角色的回答轮**，
而不是用户那轮。原因是用户轮走 `add_user_message`，它没有记忆写入通道。
缓解办法：`confidence_means` 里写明规则来源，`content` 是用户原文切片（可对着
`ConversationTurn` 里 `speaker=USER` 的那条做内容比对）。

**规则引擎放哪**：新建 `persona/modules/memory_writeback.py`，纯函数、零 I/O、零模型：

```python
def derive_candidates(user_message: str, *, profile_texts: Sequence[str] = (),
                      participants: Sequence[tuple[str, str]] = ()) -> list[dict]:
    """从用户消息派生会话级记忆候选。**不接收模型回答**（防回声室）。"""
```

不塞进 `dialogue.py` 的理由：规则会频繁调参，塞进对话模块会让契约和规则互相牵制；
纯函数可以单独 fuzz（verbatim 不变量就是靠 fuzz 保证的）。

### 3.1 开关怎么声明（这是唯一需要动契约的地方）

现状只有两个策略值（`dialogue.py:89-91`）：`none`（默认）/ `explicit`（调用方给候选）。
问题：`explicit` 的语义是"**人**给了候选"，自动派生不满足它，硬塞等于偷偷改 D-179。

**建议新增第三个值 `rule`**：

| 策略 | 行为 |
|---|---|
| `none`（默认，不变） | 永不写。**既有守卫测试 `test_full_automatic_write_never_happens` 继续通过** |
| `explicit`（不变） | 只写调用方显式给的候选 |
| `rule`（新增） | `run_round` 用规则引擎派生候选并写入；**仍只写 `scope=SESSION`** |

代价：要改 `MEMORY_WRITE_POLICIES` 元组 + 在 `docs/DECISIONS.md` 记一条
**D-179 扩展**（"默认仍 none；`rule` 必须开会话时显式选"）。**这条要你拍板（决策点 ①）。**

### 3.2 产品路径（不做就又变成"只在测试里生效"）

`persona chat` 目前是占位命令（`cli/main.py:271` "尚未实现"）。P6.2 若只做规则引擎 +
测试，**等于把 D 家族第 N 例又写一遍**：能力只存在于测试显式注入的路径。

所以建议 P6.2 附带一个**最小产品入口**：
`persona chat <project_id> --entity <id> --other <id> --memory-write rule|off --rounds N`
（单会话、stdin 逐行读用户输入、每轮打角色回复、结束时打印 `generated_memory_ids`）。
**不做**花哨 REPL、不做流式、不做 UI。这是决策点 ②。

---

## 4. 预算与幂等约束

### 4.1 预算（本期是 **0 额外调用**）

| 项 | 结论 |
|---|---|
| 规则引擎 | 纯函数，**0 次模型调用、0 USD**。不碰 ledger、不碰 `usage_sink` |
| 对话本身 | 与现在一致（每轮 1 次），成本不变 |
| 上下文变长的代价 | 每条约 30–50 tokens，注入上限 5 条 → **每轮最多多 ~250 tokens**（≈0.0000003 USD，可忽略） |
| 评估链路 | **必须保持 policy=none**：`run_dialogue_eval.py` 建会话时用 `SessionDraft` 默认值，天然不写。**要加测试钉死**，否则将来有人把默认改成 `rule`，评估结果会被记忆污染（而且没人会发现） |

### 4.2 幂等（三层，缺一不可）

| 层 | 机制 | 现状 |
|---|---|---|
| 轮次级 | 同 `(session_id, index)` 重复 `commit_turn` → 返回同一 `turn_id`，**不重复写记忆** | 已有 + 有测试 |
| 派生级 | 规则引擎**必须确定性**：不吃随机数、不吃当前时间（时间 cue 只看文本），同输入必同输出 | 待实现，用"同输入两次同输出"测试钉死 |
| 内容级 | 同一句话重复出现不应写两条 | **现状缺**（`write_simulated` 无 `_find_duplicate`），见 §6 决策点 ③ |

若将来升级成"LLM 抽取候选"，追加两条约束：
- 独立的 `MEMORY_PROMPT_VERSION` 参与幂等键（照 `DIALOGUE_PROMPT_VERSION` 的做法）；
- 抽取调用必须走既有闸门：`check_model_budget(task_id=...)` → 发送 → **先结算后判结果**
  （与 `dialogue.py:610-640` 同口径，失败也要记账）。

---

## 5. 测试计划

### 5.1 离线单测（新 `tests/test_memory_writeback.py`，约 30 条）

- **正例**：R-EPI / R-SEM / R-REL / R-EXPL 各 3 句 → 恰好 1 条候选，`kind` 正确；
- **负例**：N1–N7 各 1–2 句 → **0 条**（每类都要有，别只测问句）；
- **verbatim 不变量（fuzz）**：造 50 条随机中文语料，断言
  `content in user_message` 恒成立（这条比任何单点用例都值钱）；
- **确定性**：同输入跑两遍，输出逐字段相等；
- **上限**：一句话命中 3 条 cue → 仍 ≤2 条；
- **签名护栏**：`inspect.signature` 断言 `derive_candidates` **没有**接收模型回答的参数
  （防将来"顺手把 answer 传进来"，那是回声室的入口）；
- **区间**：`confidence`/`salience` 落在 `[0,1]`，且 `confidence_means` 非空。

### 5.2 集成测试（扩 `tests/test_dialogue_memory_integration.py`，约 8 条）

- `policy=rule` + 用户自述 → 写 1 条，`namespace=SIMULATED`、`source_type=SIMULATED_DIALOGUE`、
  `source_turn_id` 非空、`turn.generated_memory_ids` 非空；
- `policy=none` + 完全相同输入 → **0 条**（既有守卫测试必须继续绿）；
- `policy=explicit` 不给候选 → 0 条（既有行为不许松动）；
- `rule` + 连续 4 轮寒暄 → 0 条；
- 同一句说两遍 → **1 条**（等 dedupe 修好之后）；
- 记忆写失败 → 轮次回滚（复用既有注入故障点）；
- 跨会话：新会话带 `topic` 命中 → R2 检索到这条记忆；
- **台账**：`rule` 模式不新增 `external_usage_records` / ledger 行（证明 0 外呼）。

### 5.3 产品路径（防 D 家族，必做）

- `persona chat --memory-write rule --rounds 2`（ScriptedProvider）→ 断言 **DB 里真有记忆行**，
  且 `ConversationTurn.generated_memory_ids` 里真有 id。**只断言函数返回值不算数**——
  这正是 D-A/D-B 栽过的坑。

### 5.4 真实验证（一次性，最小规模）

- 1 个会话 4 轮（用户 4 句里 2 句该记、2 句不该记）→ **4 次调用**；
- 再开 1 个新会话（带 `topic`）跑 2 轮 → 验证跨会话记忆确实被检索进上下文 → **2 次调用**；
- 跑完查库：`MemoryItem` 行数、内容 verbatim、`namespace` 全 SIMULATED；
- 逐条对账：**该记的记了、不该记的没记**（这是规则质量唯一可信的验收）。

---

## 6. 预计成本与调用次数

| 项 | 调用次数 | 费用 |
|---|---|---|
| 规则引擎（设计本身） | **0** | **0 USD** |
| 离线单测 + 集成测试 + mock | 0（ScriptedProvider） | 0 |
| 真实验证（4 轮 + 跨会话 2 轮） | **6** | **≈0.003 USD**（按 0.0005/次） |
| 合计 | **6 次** | **≈0.003 USD** |

对比一下另一条路（每轮一次 LLM 抽取）：4 轮就是 +4 次（≈0.002 USD），看似不贵，但
**多了一个会编造记忆的生成器**，还要额外一套 prompt 版本、负例、判据——本方案先用 0 成本
把"确定性记得对"做出来，等 recall 不够再考虑升级（§8 的 Phase 2）。

---

## 7. 分阶段落地（建议顺序，每阶段都可独立验收）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P6.2-a | 规则引擎 `memory_writeback.py` + 30 条单测（**不接线**） | 单测绿 + fuzz 不变量成立 |
| P6.2-b | 新增 `rule` 策略 + `run_round` 接线 + 集成测试 | 集成绿；`none` 守卫测试仍绿 |
| P6.2-c | 最小 `persona chat`（含 `--memory-write`）+ 产品路径测试 | 脚本跑通，**DB 断言**通过 |
| P6.2-d | 真实验证 6 次调用 + 逐条对账 | 该记的记了、不该记的没记 |
| P6.2-e（可选） | dedupe 修进 `write_simulated` | 重复句只写一条 |

---

## 8. 明确不做（防 scope creep）

- ❌ 不用 LLM 抽取候选（Phase 2 再议，且要先有 verbatim 落地校验）；
- ❌ 不把记忆升级成 `ENTITY` / 人格特征（INV-1，永久禁止）；
- ❌ 不把记忆当证据（INV-3，`memory.py` 里根本没有 `add_evidence`）；
- ❌ 不做记忆的 UI/检索排序优化（`MEMORY_CONTEXT_LIMIT=5` 不动）；
- ❌ 不改评估链路的任何判据（P6.1 已收尾）。

---

## 9. 需要你拍板的 4 个决策点

1. **新增会话策略 `rule`**（= D-179 的扩展）：同意就写进 `DECISIONS.md`，默认仍是 `none`。
   不同意的话，P6.2 只能停在"引擎 + 测试"，产品路径无法打开（又会是 D 家族）。
2. **本期是否做最小 `persona chat`**：不做则 P6.2 无法端到端验证"记忆真的被写进库"。
3. **dedupe 修在哪**：① 修 `write_simulated` 调 `_find_duplicate`（推荐，2 行 + 测试，
   与 `write()` 一致）；② 在规则引擎侧先 `search` 查重（不改 memory.py，但多一次读、且并发下不严）。
4. **`source_turn_id` 的近似是否接受**：方案 B 记在**角色轮**上。若要求"记忆必须挂用户轮"，
   就得走选项 A（改 `add_user_message`，补闸门 + 幂等），工作量明显变大。

---

## 10. P6.2-a 落地记录（2026-10-20，详见 RUNNING §5.39）

**已做**：`persona/modules/memory_writeback.py`（规则引擎）+ `MemoryService.write_simulated`
去重 + 测试（规则 55 / 集成 12 / dedupe 直接 2）。全量回归 **1883 OK**，评估 mock 全绿。

**与本文的 3 处偏差**（都是执行中发现的，不是随意改的）：

1. **dedupe 提前到 P6.2-a**（原计划在 P6.2-e）：用户拍板"一处修复覆盖所有调用方"，
   且集成测试 §5.2 的"同一句说两遍 → 1 条"依赖它，放后面会让 P6.2-b 的验收无法闭环。
2. **R-REL 的"参与者指称"接受第二人称「你/您」**：本文写的是"参与者指称"，但真实对话里
   用户说的是"你"而不是角色名；只认角色名会把最典型的关系变化（"我现在觉得你比从前可亲"）
   全部误否决。
3. **时间/动作 cue 补了变体**（`近来`/`这两天`/`我在读` 等）：离线探针 13 句里 3 句是这种
   说法，按原文词表会漏。补后命中 5/13 → **7/13**，precision 仍 7/7。

**已知召回缺口（未修，故意保留）**：整句无第一人称 → 丢；未来计划/意愿 → 不记；
纯情绪/请求 → 不记。见 RUNNING §5.39 "召回实况"。

**未做**：`rule` 策略、`run_round` 接线、`persona chat`、真实调用——即 P6.2-b/c/d。


## 11. P6.2-b 落地记录（2026-10-20，详见 RUNNING §5.40）

**已做**（4 处代码改动，零契约破坏）：

| 位置 | 改动 |
|---|---|
| `dialogue.py` 常量 | 新增 `POLICY_MEMORY_WRITE_RULE = "rule"`，`MEMORY_WRITE_POLICIES` 扩为 3 值；另加 `MEMORY_WRITE_AUTHORIZED = (explicit, rule)` 供闸门用（**`none` 不在其中**） |
| `dialogue.py::commit_turn` | 闸门由 `policy != explicit` 改为 `policy not in MEMORY_WRITE_AUTHORIZED`——`rule` 与会话级显式授权同权 |
| `dialogue.py::run_round` | 调用方没给候选、且策略为 `rule` 时，用引擎从 `user_message` 派生后透传；**其它策略一律不派生** |
| `dialogue.py` 新私有方法 | `_memory_derivation_inputs`（档案文本 + 参与者姓名，只读）／`_derive_memory_candidates`（派生 + 审计） |
| `run_dialogue_eval.py` | `assert_eval_memory_policy` + `start_eval_session`：评估每题开会话后**读回策略**，非 `none` 直接抛错；报告也打印该策略 |

**与本文 §3 的一致**：选的就是方案 B（在 `run_round` 里派生、透传给角色轮
`commit_turn`），`memory_candidates` 参数早已存在，**零契约改动**。

**一处必须记住的实现陷阱**：`none` 策略下**绝不能派生**。派生了会被 `commit_turn`
的闸门判 `VALIDATION.gate_rejected`，默认路径就从"零写入"变成"每轮报错"——
单测若不覆盖 `none + run_round(user_message)` 这条路径，问题只在真跑时爆。
`tests/test_memory_writeback_wiring.py::test_none_policy_round_succeeds_and_writes_nothing`
专钉这一点。

**测试**：接线 13 条（含 3 条反向测试 + 变异验证）、评估守卫 6 条；全量回归 **1902 OK**。


## 12. P6.2-c 落地记录（2026-10-21，详见 RUNNING §5.41）

**已做**：最小 `persona chat`（`cli/main.py::_cmd_chat` + 五个小助手）。

| 位置 | 改动 |
|---|---|
| `cli/main.py` | 新增 `chat` 子命令（`--names/--rounds/--message/--memory-write/--budget/--db/--project/--yes/--no-dotenv`）；`chat` 从 `PLACEHOLDER_COMMANDS` 移出 |
| `cli/main.py` | 新增 `_authorize_real_calls`：`run` 的授权块**原样抽出**，`run` 与 `chat` 共用一份（D-299） |
| `modules/dialogue.py` | 新增 `DIALOGUE_MEASURED_COST_PER_CALL_USD = 0.0005`（对话单价唯一出处，D-300） |
| `run_dialogue_eval.py` | `MEASURED_PER_CALL_USD` 改为引用上面的常量，不再抄数字 |
| `orchestrator/__init__.py` | 转发 `SessionDraft` / 三个策略常量 / 单价常量——CLI 不许直连 `persona.modules`（D-059/F-7，D-302） |

**与本文 §3.2 的两处偏差**（都是实现约束逼出来的，不是随意改）：

1. **参数是 `--names` 而不是 `--entity/--other`**：本文当时写的是实体 ID，
   但命令行用户手上只有人名；`--names` 走 `resolve_entity` 与 `show`/`evidence`
   同一套解析口径（含 `pe_` 前缀逃生口）。
2. **交互模式用 REPL 而不是"一句话一命令"**：P6.2-d 要"1 个会话 4 轮"，
   一次 `chat` 一个会话，靠 `--message` 只能发一句；所以缺省时逐行读 stdin，
   `--message` 作为单发通道保留。

**Ctrl-C**：退出码 **130**（不是 0），记忆不回滚、会话必置 `ENDED`、异常不逃逸（D-301）。

**测试**：`tests/test_p6_2_chat_cli.py` **10 条**（CLI 6 + 端到端 3 + Ctrl-C 1），
三条变异验证（中断不接管 / `end_session` 空操作 / 策略不装配）分别打红
1 / 3 / 3 条；全量回归 **1902 → 1912 OK**；真实外呼 **0**。

**未做**：真实调用对账（P6.2-d）。

---

## 13. P6.2-d 真跑对账（已执行）

**真跑前先离线跑规则引擎**（零外呼）把四句话的预期钉死，真跑只验证接线：

| 轮 | 输入 | 规则预期 | 实际 |
| --- | --- | --- | --- |
| A1 | 你好 | N2 → 不记 | 不记 ✅ |
| A2 | 我在读《西厢记》，觉得崔莺莺这个人挺有意思 | R-EPI → EPISODIC | `mem_b1036f…` ✅ |
| A3 | 你知道青埂峰在哪吗？ | N1 → 不记 | 不记 ✅ |
| A4 | 我现在觉得你比从前可亲多了 | R-REL → RELATION_DELTA | `mem_d5cb5…` ✅ |

**账本**：6 笔 CHAT（A 4 + B 2）、0 笔 SEARCH，与预期逐笔一致；
`task_id` 全 `null`（D-C 的又一次印证——`chat` 不经 `business.run()`）。
**费用**：实际 0.0027285 USD vs 预估 0.003 → **0.91×**，未超 1.3 倍停止线。
**verbatim**：两条 `content` 均为用户原话的完整子串；`namespace=SIMULATED` /
`scope=SESSION` / `ACTIVE` / `source_turn_id` 指向角色轮。

**检索分层**（本次最重要的结论，D-303）：

| 层 | 会话 B 读得到会话 A 的记忆吗 |
| --- | --- |
| 存储层 | ✅ |
| 检索端口层（直连 `MemoryService.search`） | ✅ |
| prompt 注入层（`referenced_memory_ids`） | ❌ —— R2 需要 `topic`，`chat` 无入口 |

R1 本会话**已在产品路径生效**（会话 A 第 3 轮起 `referenced_memory_ids` 非空）；
R2 缺的是 topic 入口，不是检索能力 → **P6.3 做 `--topic` 入口，不放宽 R2 条件**。

**未做**：跨会话注入（P6.3）、`--topic`、记忆管理命令。

