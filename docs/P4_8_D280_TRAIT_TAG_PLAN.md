# D-280 方案：受控特征标签 `trait_tag`（**待授权，未写任何实现代码**）

- **状态**：方案评审稿
- **针对**：D-278（`traits` 结构性为空）
- **已否决**：方案 A（语义聚类，违反 AC-7）／方案 B（放宽 `SINGLE`，违反 D-012 / PF-V2）／方案 C（实体锚点词典，脆弱且不可迁移）
- **本轮已做**：`evidence_anchors` 只读字段（保守版，15 项测试，见文末 §9）

---

## 1. 一句话

**把归一化从"事后猜两条自然语言是不是一个意思"，前移到"抽取时就让模型从受控词表里选一个标签"。**

模型只做**归纳**（贴标签），不做**判定**（比对两条说法是否等价）。判定交给确定性规则：
三元组相同即同一组。

---

## 2. 为什么这样就不违反 AC-7

| | 方案 A（语义聚类） | 本方案 |
|---|---|---|
| 模型干什么 | 判断"这两条说法是不是同一个意思" | 判断"这条说法属于哪个标签" |
| 性质 | **判定** | **归纳 / 分类** |
| 谁决定"算不算重复出现" | **模型** | **确定性规则（三元组相等）** |
| AC-7 风险 | 模型给自己的产出背书 ⇒ **自证** | 模型不参与可靠性判定 ⇒ 不自证 |

**关键差异**：贴错标签的后果是**归并到错误的格子**（召回/精确率问题），
而让模型判"是否等价"的后果是**凭空制造出"重复出现"这个事实**（可靠性问题）。
AC-7 约束的是后者。

一个反证：如果本方案也违反 AC-7，那么**现有的 `dimension` 字段早就违反了**——
它同样是让模型把自然语言归入预定义槽位（7 选 1）。`trait_tag` 只是把槽位做得更细。

### 残留风险与缓解（不回避）

贴错标签会**误并**（两条不同的特征被贴上同一个 tag）。这是真实风险，缓解手段：

1. 词表标签必须**足够互斥**——太粗的标签（如"性格"）会让什么都往里塞；
2. 组内**保留所有原始说法**（`evidence_refs` + `evidence_anchors`），任何一步都可追溯；
3. 加确定性告警 `TRAIT_TAG_DIVERSITY`：同组内说法文本两两字符重合度低于阈值时告警
   （**不用模型**判断，纯字符指标）——把"可能误并"暴露出来，而不是藏起来。

---

## 3. `trait_tag` 与 `dimension` 的关系

**两级槽位：`dimension` 是抽屉，`trait_tag` 是抽屉里的格子。**

| | `dimension` | `trait_tag` |
|---|---|---|
| 粒度 | 粗（7 选 1） | 细（词表 N 选 1） |
| 回答 | "这是**哪类**信息" | "这是**哪个**具体特征" |
| 现状 | 已有，稳定 | **新增** |
| 归并中的地位 | 键的第一段 | 键的第三段 |

### 只对部分维度启用（重要）

**不是所有维度都该贴标签。** 叙述性维度贴标签会丢信息：

| 维度 | 是否启用 `trait_tag` | 理由 |
|---|---|---|
| `TRAIT` | **必填** | 归并需求最强烈（R4.3 有 5 条 TRAIT 证据全散着） |
| `HABIT` | **必填** | 习惯天然是可枚举的（吸烟斗/拉小提琴） |
| `SPEECH_STYLE` | **必填** | 语言风格可枚举 |
| `BACKGROUND` | **必须留空** | 身世经历是叙述，压缩成标签会丢掉时间线 |
| `EXPERIENCE` | **必须留空** | 同上，且已有 `TimelineItem` 承载时间 |
| `RELATION` | **必须留空** | 归关系档案处理 |
| `CANON_FACT` | **必须留空** | 不进人物档案 |

**校验规则**：启用维度缺 `trait_tag` ⇒ 拒收；未启用维度填了 `trait_tag` ⇒ 拒收。
两边都硬，不允许"填了也行不填也行"——那样模型会随机发挥，归并就废了。

---

## 4. 受控词表分层设计

三层，**具体层覆盖通用层**；未命中任何层 ⇒ 拒收（不是降级）。

### L1 通用层（默认启用，跨人物可迁移）

按"**能直接从文本观察**"设计，不按抽象心理构念——证据是文本片段，模型要能判断。

> 下面是**评审稿草拟清单**（约 32 个），正式版本须用 R4.3 的 16 条证据做
> **离线回放**验证覆盖率后再定（见 §8 验收）。

| 族 | 标签 |
|---|---|
| 智力/能力 | `analytical` 分析推理、`observant` 观察入微、`knowledgeable` 学识广博、`resourceful` 机智应变、`memory` 记忆力超群 |
| 意志/性格 | `courageous` 勇敢、`persistent` 执着坚韧、`disciplined` 自律、`impulsive` 冲动、`stubborn` 固执、`patient` 耐心 |
| 待人 | `compassionate` 富有同情心、`aloof` 疏离冷淡、`loyal` 忠诚、`arrogant` 傲慢、`humble` 谦逊、`witty` 幽默机智 |
| 情绪 | `calm` 冷静、`volatile` 情绪起伏、`anxious` 焦虑、`optimistic` 乐观、`melancholy` 忧郁 |
| 道德 | `just` 正义感、`ruthless` 不择手段、`honest` 诚实、`deceptive` 欺瞒 |
| 习惯（配 `HABIT`） | `substance_use` 成瘾/嗜好、`music` 音乐、`reading` 阅读、`exercise` 体能锻炼、`grooming` 仪容讲究 |

### L2 领域层（**可插拔，默认空**）

按人物类型配置，**不是硬编码**。默认空 ⇒ 默认路径只依赖 L1 ⇒ **保住可迁移性**。

- 侦探类示例：`deduction` 演绎、`disguise` 伪装、`forensics` 物证
- 武侠类示例：`martial_arts` 武艺、`chivalry` 侠义

由配置注入（`persona.config`），与 L1 同名时 **L2 优先**。

### L3 自定义层（项目/用户配置）

优先级最高，用于覆盖前两层的措辞或补充项目专属标签。

### 词表的形式

新建 `persona/modules/trait_vocabulary.py`：

```python
@dataclass(frozen=True)
class TraitVocabulary:
    generic: Mapping[str, str]   # tag -> 中文说明（注入提示词用）
    domain: Mapping[str, str]
    custom: Mapping[str, str]

    def all_tags(self) -> tuple[str, ...]        # 去重、排序（schema enum 用）
    def resolve(self, tag: str) -> TagSpec|None  # 层来源 + 说明
    def is_known(self, tag: str) -> bool
```

---

## 5. Schema 改动

`EXTRACTION_JSON_SCHEMA` 从**常量**变成**函数**（词表决定 enum）：

```python
def build_extraction_schema(vocabulary: TraitVocabulary | None) -> dict:
    """词表为空 ⇒ 不带 `trait_tag` 字段（**行为与今天完全一致**）。"""
```

新增字段：

```json
"trait_tag": {
    "type": ["string", "null"],
    "enum": ["aloof", "analytical", "...", null]
}
```

**`null` 是合法值，而且是刻意设计的出口**：

> 模型可以说"这条归不进任何标签" ⇒ 该条**回退到逐字归并**（与今天行为一致）。

这是合法的"我不知道"，与"我编一个标签"有本质区别。没有这个出口，
模型会被迫乱贴，误并率会失控。

**词表外的值 ⇒ 拒收 + 告警 `TRAIT_TAG_NOT_IN_VOCABULARY`**，
与既有 `QUOTE_NOT_IN_MATERIAL` 同一纪律：**不接受无法核验的，也不改写它。**

---

## 6. 归并规则

### 归并键（**唯一实现**，两处共用）

```python
def merge_key(item, vocabulary) -> tuple:
    dim = dimension_of(item.dimension)
    subjects = tuple(sorted(item.subject_entity_ids))
    tag = item.trait_tag
    if tag:                       # 有标签 ⇒ 按标签归并（不看措辞）
        return (dim, subjects, f"tag:{tag}")
    return (dim, subjects, f"text:{_fold_whitespace(item.claim)}")   # 回退
```

⚠️ **必须有唯一实现**。当前 `_stability_groups()`（稳定性判定）与
`_build_persona_draft()`（条目生成）**各写了一遍**分组键——本方案落地时
要把两处收敛到同一个函数。

> 这条不是洁癖。D-3 的教训就是"两条通道两套账"：抽取与对话各记一套，
> 结果 `external_usage_records` 里没有对话的账。归并键一旦漂移，
> 会出现"稳定性判 2 条、条目却生成 1 条"这种对不上的情况。

### 组内处理

- `evidence_refs` = 组内全部证据 ID（去重、保序）
- `stability` = 组内最强（沿用 `_strongest_stability`）
- `occurrences` = 组内证据数；`distinct_sources` = `source_id` 去重数
- 稳定性判定改喂这个新分组 ⇒ 跨源同义现在能产生 `occurrences ≥ 2` ⇒ `REPEATED` ⇒ **可进 `traits`**

### 归并后条目文本用哪条？

一组里有多条措辞不同的说法。方案：

- `text` 取组内**第一条**（按证据登记顺序，确定性）
- `uncertainty_note` 注明："本条由 N 个来源的 M 条说法归并，措辞各异，原文见证据引用"

这样条目可读，且**不假装这些措辞是同一句话**。

---

## 7. `PROMPT_VERSION` 变更

`g2-extract-2026-10-17` → **`g2-extract-2026-10-18`**

理由（缺一不可）：提示词增加 `trait_tag` 输出要求、词表内容注入系统消息、
幂等键由 `PROMPT_VERSION` 参与生成。**改提示词不改版本 ⇒ 新旧产出共用同一个幂等键**，
这正是当初定这条规矩的原因。

### 提示词增量

系统消息新增一段（措辞沿用 D-276 的经验：**写明后果，不只写规则**）：

> 每条 claim 若 `dimension` 为 `TRAIT` / `HABIT` / `SPEECH_STYLE`，**必须**给出
> `trait_tag`，且只能从下列标签中选一个：
> `analytical` 分析推理 / `observant` 观察入微 / …
> **禁止自造标签**——造出来的标签会被整条拒收，你前面写的内容一起作废。
> 若确实没有合适的标签，填 `null`：那只是这条回退到按原句归并，
> 不会作废。**贴错标签比填 null 更糟**，因为它会让不相干的说法被并到一起。

### token 影响（预估）

| 项 | 增量 |
|---|---|
| 输入：词表注入（32 标签 × ~22 token） | ≈ +750 token |
| 输出：每条 claim 多一个字段（36 条 × ~5） | ≈ +180 token |
| 合计对 7456 输出上限的影响 | 仍充裕（R4.3 用了 3803/7456） |

---

## 8. 验收标准

### 离线测试（零成本，先做）

| # | 用例 | 期望 |
|---|---|---|
| 1 | 3 条措辞不同的同义说法 + 同一 `trait_tag` + 2 个来源 | 归并 1 组，`REPEATED`，**进 `traits`** |
| 2 | 词表外的标签 | 拒收 + `TRAIT_TAG_NOT_IN_VOCABULARY` |
| 3 | **未配置词表（空）** | **行为与今天完全一致**（不退化） |
| 4 | 启用维度缺 `trait_tag` | 拒收 |
| 5 | 未启用维度（`BACKGROUND`）填了 `trait_tag` | 拒收 |
| 6 | `trait_tag=null` | 回退逐字归并 |
| 7 | 不同 `dimension` / 不同 `subject` | 不归并 |
| 8 | 归并键**唯一实现**：`_stability_groups` 与 `_build_persona_draft` 结果一致 | 同键 |
| 9 | `PROMPT_VERSION` 已 +1（且 ≠ 旧值） | 防漏改 |
| 10 | schema：词表为空时不带 `trait_tag` 字段 | 向后兼容 |

### 真实运行（R4.4，**另行授权**）

| 指标 | 门槛 |
|---|---|
| **traits > 0** | **核心验收项** |
| 归并后条目可追溯到 ≥2 条证据、≥2 个来源 | 是 |
| `TRAIT_TAG_NOT_IN_VOCABULARY` 拒收 | 拒收率 < 20%（超了说明词表太窄） |
| `TRAIT_TAG_DIVERSITY` 误并告警 | 如实报告，不静默 |
| 清单外调用 | 0 |
| 调用次数 | 与 R4.3 相同（2 检索 + ≤1 抓取 + 2 模型） |

---

## 9. 本轮已做的保守版：`evidence_anchors`

按你的安排，`evidence_anchors` 作为独立小任务**先做了**（零成本、零风险）：

- `PersonaProfileDraft` 增加只读字段 `evidence_anchors`
- 由 `_build_persona_draft` 从证据本体算出：每个特征列出
  `evidence_ids` / `source_ids` / `evidence_count` / `source_count` / `in_traits`
- **不进 `traits`**、不参与 `coverage`、不改变档案状态（15 项测试钉死）
- 计数**落盘前重算**（`_anchor_view`）：声称 5 个来源只列 2 个 ⇒ 记 2。
  调用方**可以留空、不可以编造**
- 按来源数降序，多来源的排前面

**它不解决 D-278**，只是让档案不再读起来全空。测试里专门写了一条**反向断言**
（`test_synonymous_wording_stays_separate`）钉死这点，防止将来有人误以为它解决了。

---

## 10. 不做的事（边界）

- 不让模型判断两条说法是否等价（方案 A）
- 不放宽稳定性闸门（方案 B）
- 不做实体锚点词典（方案 C）——跨人物不可迁移、误并风险高
- 不改调用次数；不引入第三方依赖（D-004）

---

## 11. 待你拍板的三个点

1. **词表规模**：先小步（L1 只放 ~32 个高置信标签，用 R4.3 证据离线回放验证），
   还是一次到位（L1 + 常用 L2）？
2. **误并告警 `TRAIT_TAG_DIVERSITY` 的阈值**：字符重合度低于多少告警？
   （先给 `0.3`，跑完 R4.4 再校准？）
3. **R4.4 是否沿用福尔摩斯/华生**：沿用便于与 R4.1/R4.2/R4.3 逐项对照；
   但也可以换一个目标来检验词表的**可迁移性**（这是你否掉方案 C 的核心理由）。

---

## 12. 建议的执行顺序

1. 用 R4.3 的 16 条证据做**词表离线回放**（零成本）：拿草拟词表人工/规则贴标，
   看能归并出几组、是否出现明显的误并 —— **先验证词表，再写代码**
2. 实现 `trait_vocabulary` + `build_extraction_schema` + 归并键收敛（离线）
3. 补 §8 的 10 项离线测试 + 全量回归
4. 出 R4.4 清单（dry-run）→ 等你授权 → 真实运行
