# P4.8 A+B：L2 中文古典层 + 规则冲突修复（离线回放）

- **时间**：2026-10-18
- **范围**：**零成本**（不调真实 API、不装依赖、不跑检索）
- **依据**：R4.4 实测（`docs/P4_8_R44_REAL_SEARCH.md`）——原子化只做到一半、
  `SPEECH_STYLE` 空族、1 处贴错导致的误并
- **回放数据**：R4.4 的 **25 条真实证据**（`%TEMP%\gps-p48-r44\real\persona.db`），
  走产品自己的 `_stability_groups` + `_build_persona_draft`
- **脚本**：`scripts/replay_p4_8_l2.py`（零调用，可复跑）

---

## 1. A：L2 中文古典层（D-286）

新增 `L2_ZH_CLASSICAL_TAG_SPEC`（11 个），`zh_classical_vocabulary()`
= L1 + L2 = **42 个标签**；`default_vocabulary()` **仍是纯 L1 的 31 个**。

| 族 | 新增标签 | 对应的 R4.4 缺口 |
|---|---|---|
| `SPEECH_STYLE` | `caustic` 刻薄/毒舌、`reserved` 含蓄、`incisive` 犀利、`tactful` 委婉 | **L1 该族为空**；黛玉"言辞尖酸刻薄"被迫贴 `witty` |
| `TRAIT` | `sensitive` 敏感/多心 | "检讨自己'多心'"（原来落在 RELATION、无标签） |
| `TRAIT` | `rebellious` 叛逆/离经叛道 | 宝玉核心特征，L1 无格子 |
| `TRAIT` | `devoted` 痴情/专一 | "一生眼泪相报""哭得死去活来" |
| `TRAIT` | `frail` 病弱 | "自幼体弱多病" |
| `TRAIT` | `attractive` 俊美/风流 | 外貌类，L1 无格子 |
| `TRAIT` | `egalitarian` 平等待人 | "平等待人、尊重个性"（原来勉强贴 `compassionate`） |
| `TRAIT` | `traditional` 崇古/守旧 | "编新不如述旧"（原来勉强贴 `knowledgeable`） |

**没有塞进 L1** 的理由沿用 D-280 §分层：L1 是"跨人物可迁移"的基线；
把中文古典语汇硬编码进去，"换角还能不能覆盖"就再也测不出来了。
代价（候选格 31 → 42，误贴表面变大）由"必须显式选层"承担。

选择入口：`vocabulary_by_name("l1" | "l2-zh-classical")`；
e2e 脚本新增 `--vocabulary l2-zh-classical`（默认仍是 `l1`）。

### 1.1 顺带修掉一个真 bug：词表不进幂等键

`model_call_key_parts` 原来只含 模型 | 材料键 | 目标 | 提示词版本。
标签清单**不在**提示词版本里 ⇒ 同一批材料从 L1 切到 L2，键完全相同，
会被当成"重复调用"静默跳过、复用 L1 的产出——**"我想换层"的意图被悄悄吃掉**
（与 D-283 空词表被 `or` 换掉同型）。

修法：`TraitVocabulary.fingerprint()`（内容 sha256 前 16 位）进键；
空串表示"这次调用不涉及词表"（对话等），对既有键完全兼容。
`tests/test_p4_8_l2_vocabulary.py` 锁住"同材料同目标，只有词表不同 ⇒ 键不同"。

---

## 2. B：规则 9 / 10 的冲突裁决（D-287）

| 改动 | 内容 | 理由 |
|---|---|---|
| 提示词 | 规则 9 增补：**禁止在事实后追加评价**（"体现出…""折射出…""显出…"），并写明后果（标签只能贴一个，其余事实全丢） | R4.4 有 7 条带尾随评价，占掉 claim 一半字数 |
| 提示词 | 规则 10 增补：**事实多于 N 时第 9 条优先**——"宁可少写几条…也不要把几个事实合并成一条" | R4.4 的复合句根因：3/10 材料顶到 N=4，模型只能合并 |
| 常量 | `TOKENS_PER_CLAIM` 200 → **180** | 实测 163/条；10 份材料从 8256 降到 **7456**，`OUTPUT_CEILING_TIGHT` 不再误报 |
| 提示词版本 | `g2-extract-2026-10-18` → **`g2-extract-2026-10-18b`** | 规则文本变了，幂等键必须跟着变 |
| 运行期 | 新告警 `CLAIM_NOT_ATOMIC`（**批级一条**，只报数不拒收） | 让"原子化做到没有"每次运行自己报，不再靠人事后数 |
| 测试 | `tests/test_p4_8_atomicity.py` 9 项：把 R4.4 的 25 条原文当夹具，锁住 **19/25 疑似复合**；锁住提示词里的优先级与"评价"禁令；锁住 10 份材料不再告警 | "原子化后每条只含 1 个事实"无法离线断言模型输出，能锁的是**规则文本 + 温度计 + 上界** |

`CLAIM_NOT_ATOMIC` 的阈值：≥3 个分句（`、，；` 切）。取 3 不取 2 的理由：
`、` 也用于单一事实内部并列（"打趣宝玉、晴雯和袭人"），阈值太低会把正常句全报进来。

---

## 3. 回放结果（`scripts/replay_p4_8_l2.py` 实测）

两个变体回答两个不同问题：**光加词表有没有用**、**补族+重贴之后误并会不会消失**。

| 主体 | 变体 | 可归并组 | traits | habits | single_events | 误并（尖酸 × 幽默） |
|---|---|---|---|---|---|---|
| 林黛玉 | **A 只换词表** | 2 | 2 | 1 | 2 | **仍在** |
| 林黛玉 | **B 按 L2 重贴** | 1 | **1** | 1 | 4 | **已消除** |
| 贾宝玉 | A | 0 | 0 | 0 | 3 | — |
| 贾宝玉 | B | 0 | 0 | 0 | 4 | — |

### 3.1 变体 A 的意义：**只换词表等于没换**

黛玉的 traits 仍是 2 条，其中一条仍是"尖酸刻薄"（引用里躺着"幽默爱笑"）。
这不是意外，是 D-280 的原话：归并键只看标签，标签不变，格子就不变。
**词表是候选格，贴法才决定落格。** 所以 L2 的价值必须靠"重贴"来兑现。

### 3.2 变体 B：误并消除，代价是 traits 2 → 1

- `尖酸刻薄 → caustic`（SPEECH_STYLE）后，它与 `幽默爱笑 → witty`（TRAIT）
  不再同格：**误并消失**；
- 黛玉 `expression_style` **首次非空**（L1 下该族是空的，这一块恒为 0）；
- 但 `witty` 只剩"幽默爱笑"一条（单源）⇒ 从 traits 掉到 `single_events`，
  于是 **traits 2 → 1**。

**这个"变少"是正确的**：原来那 2 条里，有 1 条是错贴标签凑出来的假重复。
去掉假重复之后，黛玉真正**跨源成立**的稳定特征只有"聪慧/博学"一条。
把这条写进结论，比守住"traits=2"这个数字重要。

### 3.3 宝玉仍是 0：A 和 B 都修不了，也不该修

宝玉的 `compassionate` 两条来自**同一个来源**（维基百科贾宝玉页），
G6-D2 单源不得自证 ⇒ SINGLE。这与词表、与原子化都无关，
是 AC-7 的设计意图（单次行为不得固化为性格）。
B 变体里他只是 `single_events` 3 → 4（`egalitarian` 那条重贴后仍是单源）。
**要改这个数只能加来源，不能改标签。**

### 3.4 覆盖率

| 口径 | L1（R4.4 实测） | L2（变体 B） |
|---|---|---|
| 可贴标签维度内有标签 | 11/11（schema 强制） | **12/12（100%）** |
| 人判"贴切" | 4/11（36%） | 重贴后 12/12 都有对应格子（人工判定） |
| 使用标签数 | 7 / 31 | **11 / 42**（L1 的 7 + L2 的 `caustic`/`frail`/`egalitarian`/`traditional`） |
| `SPEECH_STYLE` 族 | **空** | `caustic` 用上 |

L2 的 11 个新标签本轮只用上 4 个，其余 7 个（`reserved`/`incisive`/`tactful`/
`sensitive`/`rebellious`/`devoted`/`attractive`）是**材料没触发**，
**不据此删**（同 D-285 口径：单样本不删词表）。

### 3.5 诚实声明

变体 B 的标签是**人工按 L2 词表贴的**，模拟"模型在 L2 下会怎么选"，
**不是模型的真实输出**。它证明的是"贴对了下游会怎样"，
**不能**证明"模型一定会贴对"。真实贴法要等 R4.5 跑一次才知道。

---

## 4. 本轮改动清单

| 文件 | 改动 |
|---|---|
| `persona/modules/trait_vocabulary.py` | L2 规格 11 个、`zh_classical_vocabulary()`、`vocabulary_by_name()`、`fingerprint()` |
| `persona/core/usage_ledger.py` | `model_call_key_parts(..., vocabulary_fingerprint="")` |
| `persona/modules/extraction.py` | `TOKENS_PER_CLAIM` 180、规则 9/10 优先级、`CLAIM_NOT_ATOMIC` + `count_clauses`/`claim_looks_compound`、`PROMPT_VERSION` → `…-18b`、幂等键带词表指纹 |
| `scripts/e2e_p4_8_real_search.py` | `--vocabulary l1|l2-zh-classical`；词表两侧同供；清单里打印词表/启用维度/指纹 |
| `scripts/replay_p4_8_l2.py` | 新增：零调用离线回放（A/B 两变体） |
| `tests/test_p4_8_l2_vocabulary.py` | 新增 12 项（含"切层不能共用幂等键"） |
| `tests/test_p4_8_atomicity.py` | 新增 9 项（含 19/25 夹具锁） |
| `tests/test_p4_8_output_ceiling.py` | 6736 / 7456 口径、12 份才告警、版本钉 `-18b` |
| `tests/test_p4_8_trait_tag.py` | 版本钉 `-18b` |

---

## 5. 待确认

1. 是否跑 **R4.5 真实检索**验证 L2（`--vocabulary l2-zh-classical`，林黛玉+贾宝玉或换角）？
   清单口径与 R4.4 相同：2 检索 + ≤1 抓取 + 2 模型。
2. P2（`metadata.trait_tag_enabled` 未落库）留到下一轮，本轮未动。
3. Git 仍未提交，等 R4.5 或"不再跑"的决定。
