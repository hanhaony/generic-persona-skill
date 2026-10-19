# P5.1 CLI 收口方案（**五项已拍板 · 待授权实现 · 本文不含代码改动**）

> **状态**：方案定稿（2026-10-19 用户拍板五项，见 §8）。零代码改动、零真实调用、未安装依赖、未提交 Git。
> **前置**：R4.8 五条硬指标通过、D-294 / D-295 已修、全量 **1550 passed**、提交 `e157b7c`。
> **目标**：把"只有开发者知道怎么调用的实验脚本"变成"用户输入正常命令就能跑"的 MVP 入口。

---

## 0. 一句话结论

**能收口，但收的是"已跑通过的路径"，不是"新能力"。** 六条命令里有四条（`show` / `show-relationship` / `evidence` / `export`）
是纯只读查询，零风险零外呼；两条（`run` / `status`）复用已验证的业务链与任务服务。真正的设计难点只有三个：
**名字 → 实体 ID 的解析**（现在每次 `run` 都会新建实体）、**来源数怎么算**（不能编）、
**授权怎么说人话**（不做 `--allow-real` 这种开发者开关：密钥在 = 具备条件，但要过一次确认，并在同一数据目录里记住）。

---

## 1. 边界（先划死，再谈设计）

### 1.1 第一期做（本轮）

| 命令 | 性质 | 依据（已真实验证过的环节） |
| --- | --- | --- |
| `persona run` | 写 + 外呼 | R4.6/R4.7/R4.8 同一套 `build_business_stack` + `BusinessOrchestrator.run` |
| `persona status <task_id>` | 只读 | 已有 `status` 别名，P2.3 起就在用 |
| `persona show <名字>` | 只读 | 档案由 `PersonaService.get_persona` 产出，R4.8 库里有真实档案 |
| `persona show-relationship A,B` | 只读 | `RelationshipService.get_relationship(ids)`，R4.8 库里有真实关系档案 |
| `persona evidence <名字> [--trait]` | 只读 | `EvidenceService.get_evidence/query_evidence` + `Source` 表 |
| `persona export <名字> --format md` | 只读 | 同 `show`，只换渲染器 |

### 1.2 第二期不做（等真实验证后再进）

- `persona chat`：对话质量、多轮连贯性、**SINGLE 会不会被说成 TRAIT** 都没评估过。
- `persona memory list / forget`：真实链路里的删除、派生失效一次都没跑过。
- `persona run --material <文件>`（本地材料通道）：P4.7 只离线验证过，没在真实模型上对照过质量；
  一期不放进产品入口（`business run --material` 暂时保留给开发者）。

### 1.3 第一期明确不做

1. **不开放自由研究目标**：研究目标只由 `--names` / `--work-title` / `--adaptation` / `--vocabulary` 决定。
2. **不暴露探针**：`--probe-target` / `--probe-about` / `--probe-text` / `--probe-index` / `--replay-materials` **只留在 `scripts/e2e_p4_8_real_search.py`**。
3. 不做网页、不做 Web UI。
4. **不改已验收的产品逻辑**（只加 CLI 层 + 只读查询层）。
5. **不改数据模型**（含不改 `expression_style` / `background` 的"单段文本"形状）。

---

## 2. 架构：只加两层，不加第三份 composition root

现有铁律（D-059 / F-7）：**CLI 只能 import `orchestrator`，不得直接驱动 `modules`**。
所以只读层不能写在 `persona/cli/` 里——否则 `show` 会绕过编排层直接调 `PersonaService`，正是 G4 那类"脚本代劳掩盖缺陷"。

```
persona/cli/main.py            ← 参数解析 + 调 orchestrator + 打印（不加业务判断）
persona/cli/render.py          ← 纯函数：报告对象 → 文本 / Markdown（可单测，无 IO）
persona/orchestrator/readmodels.py  ← 新增只读查询层（只读、零外呼、零审计、零写入）
        ├── resolve_entity()      名字 → 实体（含同名歧义处理）
        ├── persona_report()      档案 + 每条特征的来源数（由 evidence_refs 现算）
        ├── relationship_report() 双向视角 + 共同经历 + 互动模式
        └── evidence_report()     特征 → 证据 → 来源 URL 的完整链条
```

复用的既有组件（**不重写**）：
`SqliteStorage.get/query`、`PersonaService.get_persona`、`RelationshipService.get_relationship`、
`EvidenceService.get_evidence/get_source`、`build_business_stack`、`register_targets`、`plan_calls`、
`vocabulary_by_name`、`TaskService.get`。

### 2.1 关键设计：来源数怎么算（不能编）

档案条目只带 `evidence_refs: [evd_...]`，**不带来源数**。`evidence_anchors` 里有 `source_count`，但
（a）它挂在**证据 claim** 上而不是档案条目上，（b）R4.8 实测 `in_traits` 恒 `False`（统计时点在晋升之前），
（c）`PersonaProfileView` 根本不返回这个字段。

所以只读层现算，规则只有一条：

```
条目的来源数 = |{ EvidenceItem[ref].source_id | ref ∈ item.evidence_refs }|
```

- **来源数 ≠ 证据数**。R4.8 黛玉 `expression_style` 是 2 条证据、但**只有 1 个来源**（都出自中青报），
  输出必须写"1 个来源（2 条证据）"。
- 证据引用在本库**找不到**时不静默丢弃：记一条 `MISSING_REF` 进报告的 `warnings`，在页脚显示
  "⚠️ 3 条证据引用在本库找不到（可能来自其他数据库）"。

### 2.2 关键设计：名字 → 实体 ID

现状：`register_targets` **每次都 `storage.put` 一个新 `PersonEntity`**（不去重）。
所以同一个名字跑两次，库里会有两个同名的不同 `pe_` ID——`show 林黛玉` 必须回答"看哪一份"。

规则（**如实优先于聪明**）：

1. 在本项目内取 `kind='PersonEntity'` 且 `display_name` 完全相等的全部实体；
2. **0 个** → 报错：`没有找到「林黛玉」。先运行 persona run --names "林黛玉" ...`；
3. **1 个** → 直接用；
4. **多个** → 用**最新创建**的那个（`created_at` 最大），并在**输出末尾**（不是标题下——标题下会盖住档案本身，用户第一眼看到的是警告而不是这个人）**必须**打印：
   ```
   检测到 3 个同名实体，已显示最新（pe_67e4…），其余：pe_abcd…、pe_aa11…。
   用 persona show pe_abcd… 查看另一份。
   ```
   - 两句话：**第一句说"发生了什么 + 我看的是哪份"，第二句给可复制命令**。不给"⚠️ 库里……merge_policy = NEVER_AUTO_MERGE"这种内部术语（那是给开发者的解释，不是给用户的提示）。
   - 多于 1 个"其余"时命令只给第一个，并加一句 `（其余用同一写法换 ID）`——三条命令堆在一起会让人不知道先敲哪条。
   同时允许把 `pe_` 前缀 ID 直接当"名字"传（`show pe_abcd...`）——这是零成本的逃生口，不是新参数。

> **不做**的事：不自动合并、不按作品名过滤（`work_title` 只存在于 `fictional_character_meta`，按它过滤等于
> 隐式改变"哪个是当前实体"的判断标准，用户看不出发生了什么）。

---

## 3. 命令规格

### 3.0 共同约定

| 项 | 约定 |
| --- | --- |
| 程序名 | `persona`（`pyproject.toml` 增 `[project.scripts]`）；未安装时 `python -m persona` 等价 |
| 数据库 | 默认取配置 `PERSONA_DATA_DIR` + `persona.db`；只读命令额外支持 `--db <路径>` 覆盖 |
| 项目 | `--project`，默认 `prj_local`（与现有 `business run` 一致） |
| 退出码 | `0` 成功 / `1` 用户可修正的问题（参数、缺密钥、预算、空档案）/ `2` 尚未实现 |
| 错误风格 | 一句话原因 + 一句话建议 + 可复制命令（**不打印堆栈、不回显密钥**，沿用 `ERROR_HANDLING.md`） |
| 语言 | 中文；`--json` 本期**不做**（先保证人读对，机器读等真有需求再加） |

---

### 3.1 `persona run`

把 R4.8 的调用流程固化成产品入口。**`run` 本身不是授权**——它要花钱，所以必须过一次确认（见 §3.1.1）；
确认过一次之后，同一个数据目录不再重复问。

```
persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 [--adaptation 曹雪芹原著]
            [--vocabulary l1|l2] [--budget 0.25] [--out ./var]
            [--type FICTIONAL_CHARACTER|REAL_PERSON] [--dry] [--yes] [--no-dotenv]
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--names` | **必填** | 逗号分隔，去重保序；空/全空 → 报错 |
| `--work-title` | `""` | 虚构角色必填（PF-V5 / D-115） |
| `--adaptation` | `""` → **取 `原著`** | **不悄悄取**：清单里必须打印 `改编版本：原著（未指定，默认）`，见 §3.1.2 |
| `--type` | `FICTIONAL_CHARACTER` | `REAL_PERSON` 时不要求作品与版本 |
| `--vocabulary` | `l1` | `l1` / `l2`（= `l2-zh-classical`），见 §5 |
| `--budget` | `0.25` | **单任务**模型费用上限（USD）；项目累计上限由配置控制，**两者取严**（见 §6.2） |
| `--out` | 配置值（默认 `var`） | 覆盖 `PERSONA_DATA_DIR`，目录不存在则创建 |
| `--dry` | 关 | 只打印清单，**不执行、不落库、零外呼、不询问** |
| `--yes` | 关 | **仅用于非交互环境**的显式确认；TTY 下第一次运行问一次并记住（见 §3.1.1） |
| `--no-dotenv` | 关 | 与现有命令一致 |

**执行流程**

1. `load_settings(overrides={PERSONA_PROVIDER: tavily, PERSONA_OFFLINE_ONLY: false, PERSONA_DATA_DIR: <--out>, PERSONA_MODEL_MAX_COST_PER_TASK: <--budget>})`
2. `build_business_stack(..., build_chat_provider=True, build_search_provider=True, allow_real=True, options=StackOptions(trait_vocabulary=..., extract_temperature=...))`
3. `register_targets` → `plan_calls`
4. **打印清单**（与 `business run` 同一份 `CallPlan`）＋预算对照（见 §3.1.2）
5. **授权检查**（§3.1.1）：`--dry` / 非 TTY / TTY 首次 / TTY 已确认，四条分支
6. 跑 → 打印结果 + **下一步命令**（这是"能接着用"的关键）：
   ```
   结论            : SUCCEEDED（任务 tsk_ab12…）
   来源 / 证据     : 10 / 24
   人物 / 关系档案 : 2 / 1
   费用            : 0.0075378 USD（2 次模型调用）

   接下来可以：
     persona show 林黛玉
     persona show-relationship 林黛玉,贾宝玉
     persona evidence 林黛玉
     persona export 林黛玉 --format md --out 林黛玉.md
   ```

#### 3.1.1 授权流程（判断顺序不能换）

**先查前置条件，再问"要不要花钱"**——没配密钥的人不该先被问一句"继续吗"、答完 y 才被告知缺密钥（那是浪费用户一次决策）。

| 场景 | 行为 | 文案 |
| --- | --- | --- |
| 缺密钥 | 直接退出 1，**不询问** | 见 §7 |
| `--dry` | 打印清单后退出 0；**不询问、不写确认记录、不落库** | `（--dry：只打印清单，没有发生任何调用）` |
| 非 TTY 且无 `--yes` | 拒绝执行，退出 1 | `检测到非交互环境（stdin 不是终端）。真实调用需要显式确认，请在命令末尾加 --yes 重跑。` |
| 非 TTY 且有 `--yes` | 直接执行，**不写确认记录** | — |
| TTY，本数据目录**无**确认记录 | 打印清单 → 问一次 → 按回答执行 | 见下 |
| TTY，本数据目录**已有**确认记录 | 清单**照打**（"看过账"这件事每次都保留）→ 直接执行 | `（此前已确认过真实调用，本次不再询问；重新启用询问请删掉 <out>/.persona_consent.json）` |

确认问句（数字**全部来自 `plan_calls` 的估算**，不写死）：

```
将要发生 2 次检索 + 2 次模型调用，预估 0.007 USD（单任务上限 0.25 USD）。继续？[y/N]
```

- `y`（或 `yes`）→ 写确认记录，然后执行；其他任意输入（**含直接回车**）→ 退出 1，打印 `已取消，没有发生任何调用。`，**且不写记录**（下次还问）。
- **默认是 `N`**：直接回车 = 不花钱。这是唯一安全的默认值。
- 回答 `n` 之后**只**打印 `已取消，没有发生任何调用。` 一行——不追问、不提示"再试一次"，取消就是取消。

**确认记录**：`<out>/.persona_consent.json`

```json
{"version": 1, "confirmed_at": "2026-10-19T00:40:12+08:00", "estimated_usd": 0.0066}
```

- 只存这三项：**不存密钥、不存命令、不存材料、不存名字**。
- 它证明的是"这个数据目录里有人同意过真实调用"，**不是"同意过某个金额"**——金额每次都在清单里重新打印，用户看得到。
- **按数据目录隔离**：换 `--out` = 换一本账，会重新问一次（合理）。
- `--yes` **不写**这个文件：脚本里写一个 `--yes` 就应该一直有效，而人能记住的确认不该被脚本继承。
- 不设 `--allow-real` 这类第二道开发者开关：**密钥在 = 条件具备**，再叠一层只会让所有人复制粘贴一个自己不理解的参数。
- 不提供"永久免确认"的配置项：要免打扰就走 `--yes`（脚本）或留着确认记录（人）。

#### 3.1.2 清单里的"默认值"必须标注来源

```
本次将要发出的真实调用（钱从这里开始算）
  研究对象      : 林黛玉、贾宝玉
  作品 / 改编版本: 红楼梦 / 原著（未指定，默认）
  检索          : 2 次（tavily basic ≈2 credits）
  抓取正文      : 上限 3 次 + 补救 1 次
  模型抽取      : 1 次
  模拟对话      : 1 次
  词表          : l1（默认；--vocabulary l2 启用 42 个中文古典标签）
  模型费用上限  : 0.25 USD（单任务，按 task_id 累计）
  累计上限      : 0.20 USD（配置值，与 --budget 取严 → 本次实际生效 0.20）
  预估模型费用  : 0.0066 USD（仅估算；实际以账单为准）
  账本已占用    : 0.0112 USD（本库历史，含此前运行）
```

规则：

1. `改编版本` 一行**必须**带来源标注：用户给了 `--adaptation` → `曹雪芹原著（显式指定）`；省略 → `原著（未指定，默认）`。**不许省略这半句**。
2. `累计上限` 只在配置的 `PERSONA_MODEL_MAX_COST` 比 `--budget` **更严或相等**时打印，并给出结论 `本次实际生效 X`；比 `--budget` 宽时不打印（不制造无意义的对比）。
3. `--dry` 打印同一份清单，末尾多一行 `（--dry：只打印清单，没有发生任何调用）`。
4. 清单里的次数与金额只有一处来源（`plan_calls` + 计价表），**不允许另算一遍**——两个地方各算一次，迟早会不一致。

**错误处理**

| 情况 | 提示 |
| --- | --- |
| 缺模型密钥 | `缺少模型密钥：请设置 PERSONA_PROVIDER_API_KEY（或 DEEPSEEK_API_KEY）。密钥只放环境变量，不要写进 .env。` |
| 缺检索密钥 | `缺少检索密钥：请设置 PERSONA_SEARCH_API_KEY（或 TAVILY_API_KEY）。` |
| 预算低于预估 | `预算 0.005 USD 低于本次预估 0.0132 USD（2 次模型调用）。要么调大 --budget，要么减少 --names。` **不发请求** |
| `max_calls` 不足 | `模型调用上限 1 次 < 本次需要 2 次。请设 PERSONA_MODEL_MAX_CALLS=2（或更大）。` **不发请求** |
| 虚构角色缺作品 | `虚构角色必须声明作品与适用版本（PF-V5）。请补 --work-title 与 --adaptation；或改 --type REAL_PERSON。` |
| 研究阶段 0 条合格材料 | `没有可用材料：检索到的页面都没过正文质量门禁（正文 ≥200 字）。本次没有生成任何特征——这是"证据不足"，不是"运行失败"。` |
| 档案为空 | `档案 status=INSUFFICIENT_EVIDENCE：24 条证据里没有一条达到"稳定特征"门槛（需 ≥2 个独立来源）。` |

---

### 3.2 `persona status <task_id>`

复用现有 `status` 别名（`task show`）。**只补两行**：

- 数据库路径（"我在看哪个库"）
- 下一步建议（`SUCCEEDED` → `persona show <名字>`；`PAUSED(BUDGET)` → "提高预算后 `persona resume <task_id>`"）

不改 `_print_task` 的既有字段。

---

### 3.3 `persona show <名字>`

```
persona show 林黛玉 [--db <路径>] [--project prj_local] [--version <n>]
```

**输出（真实 R4.8 数据）**

```
林黛玉  （pe_67e444bc305d459f9b6a9f0367c42930）
《红楼梦》 · 改编版本：原著
档案 v1 · 状态 OK · 基于 7 条证据 / 3 个来源

稳定性格（1）
  · 林黛玉有弱不禁风的一面。
    2 个来源支持 · 归并自 2 条措辞各异的说法

习惯（0）
  （无）本库没有能支撑"习惯"的证据

说话方式
  · 林黛玉言辞尖酸刻薄、喜欢怼人。 林黛玉初进贾府时告诫自己不能多说一句话、多走一步路，怕被人耻笑。
    1 个来源支持（2 条证据）

单次行为（3）
  · 林黛玉是个幽默爱笑的少女。                       1 个来源 · 非稳定特征
  · 林黛玉任情任性、真诚单纯，想说就说、想生气就生气。  1 个来源 · 非稳定特征
  · 林黛玉生性聪慧灵敏，精通诗词。                    1 个来源 · 非稳定特征

背景（0）
  （无）本库没有能支撑"背景"的证据

经历（0）
  （无）本库没有能支撑"经历"的证据

（另有 7 条证据未并入档案：CANON_FACT 4 条、RELATION 3 条，不用于性格推断；明细见 persona evidence 林黛玉）

说明：confidence 字段未校准（真实数据恒 0.0），故不展示为"可信度"；
      "N 个来源"是去重后的独立来源数，不是证据条数；
      「改编版本：原著」可能来自显式指定、也可能是省略后的默认值——实体里只存结果值，
      所以这里无法区分；persona run 的清单会明确标注本次是哪一种。
```

> 真实数字核对过（R4.8 库）：黛玉 **14 条证据 / 6 个来源**，档案引用 **7 条 / 3 个来源**，
> 未并入 **7 条 = CANON_FACT 4 + RELATION 3**。宝玉 12 / 6，档案 6 条 / 4 个来源，未并入 6 条（RELATION 3 + CANON_FACT 3）。

**规则**

- 头部固定三行：`名字（ID）` / `作品 · 改编版本` / `档案版本 · 状态 · 基于 N 条证据 / M 个来源`。
  作品与改编版本读自实体 `fictional_character_meta`，**不读档案**（档案里没有这个字段）。
- 分区顺序固定：稳定性格 / 习惯 / 说话方式 / 单次行为 / 背景 / 经历。
- **空分区必须显式打印 `（无）` + 一句原因**——绝不省略（省略会让用户以为"这个人没有习惯"，而不是"我们没查到"）。
- `traits`（稳定性格）天然只会出现 `REPEATED`；`single_events` 一律标注"非稳定特征"。
- **不展示 `confidence`**：真实数据里恒为 0.0，展示出来就是假精度。
- **"未并入"必须单独一行**：条数 + 维度分布（只列前两位，其余归"等"）+ 一句"不用于性格推断" + 指向 `evidence` 的明细入口。
  **为 0 时整行不打印**（没有噪音才是好默认）。
- 页脚固定说明（来源数口径 + confidence 口径 + 改编版本歧义），**三行都要有**。
- `status != OK` 时在标题下用一行说清：`INSUFFICIENT_EVIDENCE` → "证据不足：没有任何特征达到 2 个独立来源"。
- **同名提示放在最末**（见 §2.2），且在页脚之后——用户读完这个人才需要知道"还有另一份"。

**同名时的末尾（§2.2 规则的渲染形态，固定两句）**

```
检测到 2 个同名实体，已显示最新（pe_a312…），其余：pe_67e4…。
用 persona show pe_67e4… 查看另一份。
```

---

### 3.4 `persona show-relationship A,B`

```
persona show-relationship 林黛玉,贾宝玉 [--db <路径>] [--project prj_local]
```

**输出（真实 R4.8 数据）**

```
林黛玉 ↔ 贾宝玉  （PAIR · 档案 v1 · 状态 OK）

林黛玉 眼中的 贾宝玉（2）
  · 林黛玉和贾宝玉产生了生死与共的爱情。    1 个来源 · 非稳定特征
  · 贾宝玉与林黛玉青梅竹马，互为知己。      1 个来源 · 非稳定特征

贾宝玉 眼中的 林黛玉（2）
  · 林黛玉和贾宝玉产生了生死与共的爱情。    1 个来源 · 非稳定特征
  · 贾宝玉与林黛玉青梅竹马，互为知己。      1 个来源 · 非稳定特征

共同经历（2）
  · 林黛玉和贾宝玉产生了生死与共的爱情。    1 个来源
  · 贾宝玉与林黛玉青梅竹马，互为知己。      1 个来源

互动模式（0）
  （无）

情感表达习惯（1）
  · 林黛玉和贾宝玉产生了生死与共的爱情。    1 个来源

不对称说明（1）
  · 「林黛玉」和「贾宝玉」之间存在 2 种不同说法；规则未做语义矛盾判定，
    已分别保留并关联冲突记录
    2 个来源 · 注意：这与"语义矛盾"不是一回事，规则只做机械比对

说明：关系条目全部为 SYSTEM_DERIVED（由已有证据推导），不是"当事人自述"；
      "非稳定特征"= 只有 1 个来源提到。
```

**渲染规则（README 里要写的一条）**：`asymmetry_notes` / `uncertainty_note` 的正文里会出现裸实体 ID
（真实数据：`覆盖对 ['pe_67e444bc…', 'pe_a312748f…'] 存在 2 种不同说法…`）。
渲染层必须把**能解析到的 `pe_` 记号替换成「名字」**，其余原文一字不改——这是纯字面替换，
不改变任何结论；解析不到的 ID 原样保留并在行尾标 `⚠️ 未知实体`。

- 参与者必须有关系档案；没有 → `这两人还没有关系档案：先运行 persona run --names "林黛玉,贾宝玉" ...`。
- 三人及以上（`cardinality=GROUP`）时多打印"群体互动"分区；本期只做**两个名字**（多人关系档案结构已存在，但没有真实跑过）。

---

### 3.5 `persona evidence <名字> [--trait <标签>]`

```
persona evidence 林黛玉 [--trait caustic] [--db <路径>]
```

**输出（按特征分组，可逐条追到 URL）**

```
林黛玉 的证据（14 条证据 / 6 个来源）

■ 稳定性格
  · 林黛玉有弱不禁风的一面。   2 个来源
      ├ evd_7704…  VERBATIM  [TRAIT / frail]
      │   "也有弱不禁风的一面"
      │   《为什么是林黛玉？》 http://m.cyol.com/gb/articles/2022-06/14/content_6pqlqcjxW.html
      │   定位 snippet[1784:1793] · 抓取于 2026-10-18
      └ evd_c7ec…  VERBATIM  [TRAIT / frail]
          "…" （同上格式）

■ 说话方式
  · 林黛玉言辞尖酸刻薄、喜欢怼人。 …    1 个来源（2 条证据）
      ├ evd_09f6…  VERBATIM  [TRAIT / caustic]   ⚠️ TRAIT_TAG_DIMENSION_MISMATCH
      │   "林黛玉的尖酸刻薄，喜欢怼人，显示的不仅仅是个人性格"
      │   百度百科 https://baike.baidu.com/item/…
      └ …

■ 未并入档案的证据（7 条，均为背景事实/关系陈述，未构成性格特征）
  · 林黛玉是《红楼梦》中的女一号。            1 个来源  [CANON_FACT]
  · 林黛玉是绛珠仙草转世，美貌不俗。          1 个来源  [CANON_FACT]
  · 林黛玉在大观园中住在潇湘馆。              1 个来源  [CANON_FACT]
  · 林黛玉是史太君的外孙女、贾宝玉的姑表妹。  1 个来源  [RELATION]
  · …

说明：证据只证明"某来源这么说过"，不证明"这是事实"；
      标 ⚠️ 的条目在校验阶段有过告警（标签与维度不匹配）。
```

**规则**

- 分组口径 = 档案分区 → 条目 → 条目引用的证据（**用户问的是"这条特征凭什么"**，不是"库里有几条证据"）。
- **必须单列"未并入档案的证据"**：否则用户会以为档案 = 全部证据（R4.8 黛玉 14 条证据里只有 7 条进了档案，
  剩下 7 条是 CANON_FACT / RELATION，没被晋升成性格特征——这个差别必须让用户看见）。
- `--trait` 按 `EvidenceItem.trait_tag` 精确匹配（大小写不敏感）；无匹配时**列出本库实际可用的标签+计数**并退出码 `0`：
  ```
  没有 trait_tag=curious 的证据。「林黛玉」当前可用标签：
    frail(2)  caustic(1)  reserved(1)  …
  说明：词表决定可用标签；本次档案用的是 L2（l2-zh-classical）。
  ```
- 引用缺失（`evidence_refs` 指向别库）→ 该行打印 `⚠️ 证据不在本库（evd_xxxx）`，不静默跳过。

---

### 3.6 `persona export <名字> --format md [--out <文件>]`

- 默认写 stdout；`--out` 时写文件并打印 `已写入 <路径>（N 行）`。
- `--format` 本期只接受 `md`；`json` 明确拒绝：`本期只支持 --format md（json 需要 schema 约定，等有真实需求再加）`。

**输出（严格按用户给的格式；下面用 R4.8 真库内容，不是编的）**

```markdown
## 林黛玉

> 档案 v1 · 状态 OK · 基于 7 条证据 / 3 个来源 · 生成于 2026-10-18
> 来源数为去重后的独立来源数；"非稳定特征"= 只有 1 个来源提到。

### 稳定性格

- **林黛玉有弱不禁风的一面。**（2 个来源支持）

### 习惯

（本库没有能支撑"习惯"的证据）

### 说话方式

- **林黛玉言辞尖酸刻薄、喜欢怼人。**（1 个来源支持）

### 单次行为

- 林黛玉是个幽默爱笑的少女。（1 个来源，非稳定特征）
- 林黛玉任情任性、真诚单纯，想说就说、想生气就生气，不怕得罪人。（1 个来源，非稳定特征）
- 林黛玉生性聪慧灵敏，精通诗词。（1 个来源，非稳定特征）

### 背景

（本库没有能支撑"背景"的证据）

### 经历

（本库没有能支撑"经历"的证据）
```

- **可贴进文档**是硬指标：不要表格（贴进 Word 会散）、不要 `evd_` ID（贴进文档没意义）、不要 `confidence`。
- 空档案时不输出空标题占位，而是：
  ```markdown
  ## 林黛玉

  **证据不足：本库没有为「林黛玉」生成任何特征。**

  原因：14 条证据里没有一条达到"稳定特征"门槛（需 ≥2 个独立来源）；
        单次行为另有 3 条，但只出现 1 次，不作为性格下结论。
  ```

---

## 4. 改哪些文件

| 文件 | 改动 | 行数量级 |
| --- | --- | --- |
| `persona/orchestrator/readmodels.py` | **新增**：`resolve_entity` / `persona_report` / `relationship_report` / `evidence_report` / `load_read_services` | ~380 |
| `persona/cli/render.py` | **新增**：报告 → 文本 / Markdown 的纯函数 | ~300 |
| `persona/cli/consent.py` | **新增**：`is_confirmed(out_dir)` / `remember(out_dir, est)` / 问句构造（纯逻辑，**不碰数据库、不写 schema**） | ~80 |
| `persona/cli/main.py` | 加 5 个子命令解析 + 5 个 `_cmd_*` 函数；`PLACEHOLDER_COMMANDS` 去掉已实现的 | ~330 |
| `persona/orchestrator/__init__.py` | 导出新 readmodels 符号 | ~10 |
| `pyproject.toml` | `[project.scripts] persona = "persona.cli.main:main"` | 3 |
| `README.md` | 用 §9 草稿替换"怎么跑起来"一节 | — |
| `docs/RUNNING.md` | 追加 P5.1 命令表 | — |
| `tests/test_p5_1_readmodels.py` | **新增** | ~320 |
| `tests/test_p5_1_render.py` | **新增**（纯函数，快） | ~180 |
| `tests/test_p5_1_cli.py` | **新增**（端到端跑 CLI，Mock Provider） | ~260 |
| `tests/test_p5_1_consent.py` | **新增**（确认流程的全部分支） | ~120 |

**不改**：`persona/modules/*`（除零改动）、`persona/storage/*`（schema 不动——`<out>/.persona_consent.json` 是
普通文件，**不是数据库记录**，所以"不改数据模型"这条没有破例）、`scripts/e2e_*`（探针留在原地）、
`persona/orchestrator/business.py`、`assembly.py`（除只读层需要的 import 暴露）。

---

## 5. `--vocabulary` 如何暴露 L1/L2

```python
from persona.modules.trait_vocabulary import vocabulary_by_name  # 只读层/装配层用

ALIAS = {"l1": "l1", "l2": "l2-zh-classical", "l2-zh-classical": "l2-zh-classical"}
```

- 默认 `l1`（基线层 **31 个标签**）——中文古典角色拿不到 `caustic` / `frail` 这类细粒度标签，
  所以研究《红楼梦》这类角色时**应该显式给 `l2`**（这一条要写进 README，否则用户永远拿到粗标签）。
- `--vocabulary l2` 与 `--vocabulary l2-zh-classical` 等价（手打短的更省事）。
- 传生僻值 → **不静默回退**（`vocabulary_by_name` 本身返回 `None` 就是这个语义）：
  ```
  未知词表：curious。可选：l1（默认）、l2 / l2-zh-classical（中文古典层，42 个标签）
  ```
- `run` 打印一行让用户确认生效：`词表: l2-zh-classical（42 个标签）`。
- **只读命令不接 `--vocabulary`**：标签在抽取时已经写进证据，`show` 只展示已有标签。

---

## 6. `--extract-temperature` 与 `--budget`

### 6.1 `--extract-temperature`

**暴露（已拍板，默认不传 = 生产默认 0.7）**，理由：

- 用户复现问题时需要它（"同样的名字跑两次结果不一样"→ 用 `--extract-temperature 0` 确认是不是采样导致）；
- 它已经是 `StackOptions.extract_temperature`，CLI 只是透传，**不改任何产品逻辑**；
- 帮助文本必须写清代价，否则会被当成"调优旋钮"：
  ```
  --extract-temperature F  抽取温度（默认 0.7 = 生产值）。
                           99% 的用户不需要改这个。设 0 可复现同一批材料的同一份结果，
                           但会降低多样性（选材与措辞的自然变化会被压掉）。
                           对话温度不受影响（固定 0.7）。
  ```
- 越界（<0 或 >2）由 `resolve_temperature()` 抛错 → CLI 捕获成一句人话，不回退默认值。

### 6.2 `--budget`

| 项 | 决定 |
| --- | --- |
| 单位 | USD（与账本、DeepSeek 计价一致，不做汇率换算） |
| 默认 | `0.25`（与 `.env.example` 一致，也是 R4.6~R4.8 用的值） |
| 映射 | `overrides={"PERSONA_MODEL_MAX_COST_PER_TASK": str(x)}` → **单任务上限** |
| 不改 | `PERSONA_MODEL_MAX_COST`（项目累计上限）保持配置值 |
| 两层冲突 | 若配置的累计上限更严（`max_cost < --budget`），**必须明示**：`注意：项目累计上限 0.20 USD 更严，本次实际生效 0.20` |
| 前置检查 | `预估单次模型费用 × 计划模型调用次数 > budget` → 拒绝执行并给算法，不发请求 |

**为什么不把两个上限都设成 `--budget`**：累计上限是"整本账"口径，把它压到单次预算会让第 2 次运行莫名被拒
（历史已花掉的部分算在内）——用户会以为是 bug。

**帮助文本**（照抄进 `--help`）：

```
--budget USD  单次任务最多花多少美元（默认 0.25）。
              项目累计上限由配置控制（PERSONA_MODEL_MAX_COST），两者取严。
              超预算时不会偷偷降级，而是停下来告诉你差多少。
```

---

## 7. 错误提示文案表（面向普通用户）

统一格式：**发生了什么 → 为什么 → 怎么办（可复制命令）**。不打印异常类型、不回显密钥。

| 场景 | 检查点 | 文案 |
| --- | --- | --- |
| 缺模型密钥 | `settings.secrets.provider_api_key` | `缺少模型密钥：设置 PERSONA_PROVIDER_API_KEY（或 DEEPSEEK_API_KEY）后重试。密钥只放环境变量，写进 .env 会被忽略并告警。` |
| 缺检索密钥 | `settings.secrets.search_api_key` | `缺少检索密钥：设置 PERSONA_SEARCH_API_KEY（或 TAVILY_API_KEY）后重试。` |
| 非交互环境未确认 | `--yes` 缺失且 `stdin` 非 TTY | `检测到非交互环境（stdin 不是终端）。真实调用需要显式确认，请在命令末尾加 --yes 重跑。` |
| 用户取消确认 | 确认问句回答非 `y` | `已取消，没有发生任何调用。`（退出码 1，不写确认记录，下次还问） |
| 依赖缺失 | 无第三方依赖（D-004）；仅检查 Python ≥3.12 | `需要 Python 3.12 或更高（当前 3.10）。本项目零第三方依赖，无需 pip install。` |
| 预算不足 | `est_per_call × planned_calls > budget` | `预算不足：本次计划 2 次模型调用、预估 0.0132 USD，--budget 只给了 0.005。要么调大 --budget，要么少传几个名字。` |
| 调用次数上限不足 | `plan.max_calls < 模型调用数` | `模型调用上限 1 次 < 本次需要 2 次：请设 PERSONA_MODEL_MAX_CALLS=2。` |
| 材料不足 | `run.steps` 里 research 阶段 0 条合格材料 | `没有可用材料：抓到的页面都没过正文质量门禁（正文 ≥200 字），所以什么都没生成。这不是崩溃——换资料更完整的作品/名字重试。` |
| 证据不足 | `profile.status == INSUFFICIENT_EVIDENCE` | `证据不足：N 条证据里没有一条同时被 ≥2 个独立来源提到，因此没有"稳定性格"。单次行为有 M 条，已如实列出。` |
| 档案不存在 | `get_persona` → `NOT_FOUND.entity` | `没有「林黛玉」的档案。先运行：persona run --names "林黛玉" --work-title 红楼梦 --adaptation 曹雪芹原著` |
| 关系档案不存在 | `get_relationship` → `NOT_FOUND.entity` | `「林黛玉」和「贾宝玉」还没有关系档案：关系档案只有在同一次 run 里同时研究这两个人时才会生成。` |
| 数据库不存在 | 路径不存在 | `找不到数据库 <路径>。如果你刚跑完，检查 --out 是否换过目录（数据库在 <out>/persona.db）。` |

---

## 8. 五项拍板结果（2026-10-19 已确认，按此实现）

| # | 问题 | **最终决定** | 落地位置 |
| --- | --- | --- | --- |
| 1 | `run` 怎么授权 | **配了密钥 = 具备条件，但必须过一次确认。** TTY 下第一次运行问一次并**记住选择**（同数据目录不再问）；`--yes` **只用于非交互环境**的显式确认，且不写记忆。**"run 本身即授权"被否决**——会花钱，至少一次确认 | §3.1.1 |
| 2 | `--adaptation` 省略 | 取 **`原著`**，但**不许悄悄取**：`run` 的清单必须打印 `改编版本：原著（未指定，默认）`；`show` 头部打印实体里存的值（歧义见下方技术约束） | §3.1.2 / §3.3 |
| 3 | 同名实体 | 显示**最新** + 末尾列出其余 + 支持直传 `pe_` ID；提示固定两句（见 §3.3） | §2.2 / §3.3 |
| 4 | `--extract-temperature` | **暴露**，默认 0.7；帮助文本加 `99% 的用户不需要改这个。设 0 可复现，但会降低多样性。` | §6.1 |
| 5 | `--budget` | **只设单任务**；帮助文本写明 `项目累计上限由配置控制，两者取严` | §6.2 |

**一处必须说明的技术约束**：`show` 头部只能打印**实体的结果值** `改编版本：原著`——数据模型里
`fictional_character_meta.adaptation_version` 只存值、不存"这个值是显式给的还是默认的"。
不改数据模型的前提下，`show` 无法区分两者，所以页脚写明这个歧义；**"（未指定，默认）"这半句只在 `run` 的清单里出现**
（那里知道入参是空还是非空）。要做到 show 也能区分，需要往实体里加一个 provenance 字段——**那是数据模型改动，本期不做**。

---

## 9. README 草稿（三行跑通）

````markdown
# generic-persona-skill

输入几个名字，自动查公开资料，生成可追溯的人物档案与关系档案。

## 装（零依赖）

```bash
git clone <repo> && cd generic-persona-skill
python -m persona selftest          # 离线自检：20 秒，不联网、不花钱
```
需要 Python 3.12+。**不需要 pip install**，运行时零第三方依赖。

## 设密钥（只放环境变量）

```bash
export PERSONA_PROVIDER_API_KEY=sk-...   # 模型（DeepSeek）
export PERSONA_SEARCH_API_KEY=tvly-...   # 检索（Tavily）
```

## 跑第一次

```bash
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --adaptation 曹雪芹原著 --vocabulary l2
```

先看清单、再确认、然后开跑（**第一次会问一次，答 y 之后同一个数据目录不再问**）：

```
本次将要发出的真实调用（钱从这里开始算）
  研究对象      : 林黛玉、贾宝玉
  作品 / 改编版本: 红楼梦 / 曹雪芹原著（显式指定）
  检索          : 2 次
  抓取正文      : 上限 3 次 + 补救 1 次
  模型抽取      : 1 次
  模拟对话      : 1 次
  词表          : l2-zh-classical（42 个标签）
  模型费用上限  : 0.25 USD（单任务）
  预估模型费用  : 0.0066 USD
将要发生 2 次检索 + 2 次模型调用，预估 0.007 USD（单任务上限 0.25 USD）。继续？[y/N]
```

> 省掉 `--adaptation` 也能跑，但清单会写 `原著（未指定，默认）`——**默认值一定会告诉你**。
> 在脚本/CI 里跑要加 `--yes`（非交互环境不会替你回答）。只想看清单不花钱：加 `--dry`。

跑完会告诉你下一句该敲什么：

```bash
persona show 林黛玉                                # 看档案
persona show-relationship 林黛玉,贾宝玉            # 看关系
persona evidence 林黛玉                            # 看每条特征凭什么
persona export 林黛玉 --format md --out 林黛玉.md  # 导出可贴进文档的 Markdown
```

**先说清楚三件事**（写进 README，不退让）：
1. 档案里的每一条都能追到 URL；**追不到的不会出现在档案里**。
2. 只有 ≥2 个独立来源提到的特征才算"稳定性格"，其余标"非稳定特征"。
3. 证据不足时输出空档案 + 原因，**不会编一条凑数**。
````

---

## 10. 测试计划

### 10.1 离线测试（**实际 +147 项，全量 1550 → 1697；方案初估 +68，落地时写细了**）

| 文件 | 实测项数 | 覆盖 |
| --- | --- | --- |
| `test_p5_1_readmodels.py` | 35 | 名字解析（0/1/多命中、`pe_` 直传、同一秒按插入顺序兜底）；来源数去重（2 证据 1 来源 → `1`）；`MISSING_REF` 告警；空档案；关系档案双向视角；`evidence_report` 的"未并入档案"分组；`--trait` 无匹配；**未并入条数与维度分布统计**（夹具造 CANON_FACT 4 + RELATION 3 → 输出 `4 条、3 条`）；只读栈不建库 |
| `test_p5_1_render.py` | 55 | 六个分区顺序固定；空分区必出 `（无）`；`single_events` 必带"非稳定特征"；export md 精确文本比对（含空档案版本）；**show 头部三行齐全**（作品 · 改编版本）；**"未并入"一行**（有→打印、为 0→不打印）；**同名提示固定在最后两行**；`evidence` 的"未并入档案"小节 0 条也有标题 |
| `test_p5_1_cli.py` | 34 | 六条命令各跑一次（临时 SQLite）；退出码 0/1/2；缺检索/模型密钥、预算不足、未知词表的文案；**只读命令连跑三次库文件的字节数与 mtime 都不变**；`run --dry` **零写入零外呼**；非 TTY 无 `--yes` 必须拒绝；TTY 首次确认后写 consent、第二次不再问；回答 `n`/空回车退出 1 且不写 consent；`--yes` / `--dry` 不写 consent；删掉 consent 后重新询问；帮助文本两句话 |
| `test_p5_1_consent.py` | 23 | `<out>/.persona_consent.json` 结构（只有 3 个键，**断言不含 key/命令/材料/人名**）；坏 JSON / 未知版本 / 空时间戳都不炸；按数据目录隔离；问句里的次数与金额来自调用方；`--dry` 与 `--yes` 的写入边界 |

**关键断言（防回归的"锁"）**

1. `show` 输出里 **`confidence` 一个数字都不能出现**。
2. `show` 的每个空分区都必须有 `（无）`——删掉会被测出来。
3. 来源数用**去重后来源数**，测试固定造"2 条证据同源"的夹具。
4. `run --dry` 跑完后数据库文件**不存在**（或字节数不变），**且不产生 consent 文件**。
5. `evidence` 必须包含"未并入档案"小节（哪怕为空也要有标题）。
6. **省略 `--adaptation` 时，清单必须出现 `（未指定，默认）`**；显式给值时必须出现 `（显式指定）`。
7. **多实体夹具下，同名提示必须是输出的最后两行**（在页脚之后），且包含可复制的 `persona show pe_…`。
8. `--help` 文本包含 `99% 的用户不需要改这个` 与 `两者取严` 两句（防止实现时被"精简"掉）。

### 10.2 受控真实调用（1 次，**需要单独授权**）

```bash
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --adaptation 曹雪芹原著 \
  --vocabulary l2 --budget 0.05 --out %TEMP%\gps-p51 --yes
```

| 项 | 值 |
| --- | --- |
| 预估费用 | **2 次检索 ≈ 2 credits** + 最多 3 次抓取（≈1 credit）+ **2 次模型调用 ≈ 0.0075 USD** |
| 边界 | `max_retries` 沿用配置；`--budget 0.05` 硬上限；不注入探针、不回放材料 |
| 停止条件 | ① 出现清单外调用 → 立即停；② 实际费用 > 0.05 USD → 立即停；③ 探针参数被误接进产品路径 → 立即停（本轮设计上不允许） |
| 要验的 | 六条命令能在**真库**上串起来；`show` 的来源数与 `evidence` 的 URL 一一对应；导出 Markdown 能直接贴进文档 |

> 这次真跑只验"串起来"，不评估内容质量——质量稳定性是 P5.0 的事。

---

## 11. 工作量与顺序

| 步 | 内容 | 量级 |
| --- | --- | --- |
| 1 | `readmodels.py` + 单测（最容易出错的一层：来源数、同名、空档案、未并入统计） | 1.5 天 |
| 2 | `render.py` + 单测（纯函数，快） | 0.5 天 |
| 3 | `main.py` 五条命令 + `run` 收口 + **确认流程（consent 文件）** + CLI 测试 | 1.5 天 |
| 4 | README / RUNNING 更新 | 0.5 天 |
| 5 | 全量回归 + 受控真跑 1 次 + 报告 | 1 天 |
| — | 合计 | **约 5 天** |

---

## 12. 纪律（实施时逐条自检）

1. **探针不进产品路径**：`--probe-*` / `--replay-materials` 只存在于 e2e 脚本；`persona run` 的 `--help` 里绝不能出现。
2. **"证据不足"如实说**：空档案 + 明确原因 > 1 条编出来的特征。空分区一律打印 `（无）`。
3. **不调真实 API**（本轮方案阶段）、**不装依赖**、**不改无关文件**、**不自动提交 Git**。
4. 只读命令**绝不写库**：不写审计、不写 usage、不建 schema（`init_schema` 只在 `run` 里调用）。
5. CLI 不越过 orchestrator 直接调 `modules`（D-059）。
6. **默认值必须标注**：凡是"用户没给、我们替他选"的值（改编版本、词表），清单里必须印出 `（未指定，默认）`；**不许悄悄取**。
7. **花钱必须有一次确认**：`run` 不是授权命令；确认记录只证明"同意过真实调用"，不证明"同意过某个金额"，所以金额每次都重新打印。
