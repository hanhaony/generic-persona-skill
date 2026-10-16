# P4.4-A 验收报告：真实 Claim Extractor 接入框架与严格输出校验

- 阶段：P4.4-A（G-2 自动抽取链路的"接真实模型之前"必备部分）
- 日期：2026-10-15
- 前置状态：P4.2 已通过；P4.3 与 P4.3-R 已通过受控真实 API 复测；真实 Tavily 搜索、正文提取、用量账本、正文质量门禁均已有可用实现
- 本轮授权边界：**未调用任何真实模型 API**，未发起任何真实搜索 / 正文提取请求，未注册账号、未创建密钥
- 测试基线：改动前全量回归 896 项通过

---

## 0. 一句话结论

在不改动 `Provider` 协议、不新增第三方依赖、不重写已验收模块的前提下，建成了**可替换的模型抽取接口 + 四道闸门**（材料准入 / 严格输出校验 / 提示注入边界 / 预算与幂等），并补上了一处会让严格模式"永远跑不起来"的真实接线缺口。

**模型抽取质量本身仍未经验证**——本轮的"模型"全部是本地固定字符串，真实服务商一次都没连过。这一点在下面第 8 节列成了人工决策项。

---

## 1. 当前 Claim Extractor 的实际调用链与缺口

### 1.1 实际调用链（改动后的现状，按代码逐层列出）

```
BusinessOrchestrator.run(ctx, BusinessRequest)
│
├─ _step_research()                       # 既有，未改
│    └─ ResearchService.run() → source_ids + materials
│         └─ materials_from_extracts()    # 只把门禁判为 VALID 的摘录转成 SourceMaterial
│                                        # ← 本轮补上 quality_status=VALID
│
└─ _step_evidence()                       # 本轮扩展
     │
     ├─【路径 A】request.evidence_seeds 非空 → _register_seeds()（既有，显式优先）
     │
     └─【路径 B】claim_extractor 非空（G-2 自动抽取）
          ├─ _materials_for()                  ← 取自 ResearchOutput.materials
          ├─ claim_extractor.extract(ctx, materials, targets=request.targets)
          │    │
          │    ├─ MockClaimExtractor.extract()          # 确定性查表（测试材料）
          │    │     ├─ admit_materials()                       【闸门 1：材料准入】
          │    │     ├─ fixture 查表 + _validate_candidate()
          │    │     └─ _pending_for_mock()（VERBATIM 无摘录 → 待审核）
          │    │
          │    └─ ProviderClaimExtractor.extract()      # 模型驱动（本阶段未接真实模型）
          │          ├─ admit_materials()                       【闸门 1：材料准入】
          │          ├─ _call_key() / _duplicate_reason()        【闸门 4：幂等】
          │          ├─ _budget_block()                          【闸门 4：预算】
          │          ├─ _build_request()                         【闸门 3：注入边界】
          │          ├─ provider.chat()                          ← 唯一外部出口（D-054）
          │          ├─ _settle()                                【账本结算】
          │          └─ validate_model_payload()                 【闸门 2：严格校验】
          │
          └─ _register_candidates(outcome, source_ids, usage)      # 本轮扩展
               ├─ outcome.rejected → WARNING_CLAIM_REJECTED
               ├─ outcome.pending  → WARNING_CLAIM_PENDING_REVIEW（**不登记**）
               ├─ claims（非 INFERENCE）→ Evidence.add_evidence()
               ├─ claims（INFERENCE）  → Evidence.record_inference()（无根 → 丢弃）
               └─ Evidence.detect_conflicts()                       # 冲突不裁决、不合并
```

**职责边界**：抽取器**拿不到** `Evidence`（结构上无法把 claim 直接标成"已验证"）；`Evidence` 仍是唯一的校验 / 去重 / 登记入口（既有 `tests/test_claim_extraction.py` 的 AST 断言继续生效，本轮新测试同样钉住 `claim_validation.py` 不 import 存储与 Evidence）。

### 1.2 接入真实模型前，实际存在的缺口（本轮修补情况）

| # | 缺口（改动前的真实状态） | 后果 | 本轮 |
| --- | --- | --- | --- |
| 1 | `json.loads` 成功即当成功，字段 / 类型 / 枚举无人把关 | 模型瞎编字段会一路流到 Evidence | **已补**：`claim_validation.py` 逐字段机械校验 |
| 2 | 模型声称的"原文摘录"只要非空就算"有原文支持" | 伪造引用可被当作原文证据 | **已补**：摘录必须在送给模型的那段材料里逐字找到 |
| 3 | 材料正文被直接拼进用户消息 | 网页里的注入指令落在指令位置 | **已补**：规则进 system、材料进带标记的数据块 |
| 4 | 模型调用无预算闸门、无幂等键 | 重试 / 恢复会真的重复花钱 | **已补**：复用 `ExternalUsageLedger` 四段式 |
| 5 | 抽取器不知道材料有没有过正文质量门禁 | 严格模式无法启用 | **已补**：`SourceMaterial.quality_status` 随材料传递 |
| 6 | 抽取器无"待审核"这一去处 | 核验不了的候选只能"硬拒"或"硬收" | **已补**：`pending` 第三清单 + 编排器提示 |

### 1.3 **仍然存在**的缺口（本轮明确不解决，不得含糊）

1. **真实模型从未连过**：`ProviderClaimExtractor` 的全部测试都跑在本地固定字符串上。`model_hint` 是否被服务商接受、`response_format="json_schema"` 的实际效果、真实 JSON 合规率、真实摘录可核验率——**全部未知**。
2. **语义核验不做**：本模块只回答"这段摘录是否真的在该材料里"。它**不回答**"这句话是否真的支持这条主张"。后者需要模型或人工，本轮不假装能做。
3. **日期只做字面核验**：`stated_at` 只检查"这个字符串有没有出现在材料里"，不做格式归一、不做语义一致性判断；核验不过就**弃用该日期**（不采用、不换算），并在候选上留 `DATE_NOT_VERIFIED_DROPPED`。
4. **`max_retries` 存在但未实现**：构造参数已留，实际保持 0，不做自动重试（避免"重试"变成隐形的第二次花费）。
5. **`pending` 没有持久化队列**：目前只以 `WARNING_CLAIM_PENDING_REVIEW` 的形式出现在运行结果里，没有"人工复核工作台 / 待办表"。第 8 节列为人工决策项。
6. **`admit_materials` 的"未声明"分支**：检索摘要（`snippet`）本就不需要过正文质量门禁，所以未声明时**默认放行**并显式告警；只有 `require_gate_verdict=True` 才强制每份材料亮出结论。上线的严格取值属人工决策。

---

## 2. 本次修改的文件与理由

| 文件 | 状态 | 理由（为什么必须改） |
| --- | --- | --- |
| `persona/modules/claim_validation.py` | **新增**（≈640 行） | 严格输出校验是本阶段的核心新能力。做成**纯函数模块**：不联网、不写库、不调模型、不 import 存储与 Evidence，因此既可以被抽取器调用，也可以脱离"材料是怎么来的"独立测试。 |
| `persona/modules/extraction.py` | **重写** | G-2 主体。加入四道闸门与 `pending` 清单；`CandidateClaim` / `ClaimExtractorPort` 等既有契约保持兼容（既有 28 项测试全部原样通过）。 |
| `persona/core/usage_ledger.py` | 扩展 | 新增 `ModelCallLimits`、`model_call_key_parts`、`estimate_model_cost`、`cost_from_model_usage`，并重导出 `OP_CHAT`。**不内置任何服务商单价**：单价是"某账户在某时间点的商业条款"，写死进代码迟早过期，而过期的单价会被误当成官方费率。 |
| `persona/core/types.py` | 扩展 | ① `SourceMaterial.quality_status`：下游只看到 `snippet` 字段，**分不清**"检索摘要"与"抓取正文"，也就无法判断它有没有过门禁；② `OP_CHAT` 常量（与其他 op 常量并列）。 |
| `persona/orchestrator/business.py` | 扩展 | ① 把抽取器的 `usage` 传下去（否则用量在这一跳丢失）；② 新增 `WARNING_CLAIM_PENDING_REVIEW`，把 `pending` 显式转成提示——**否则待审核就成了静默丢弃**；③ 运行结果 message 追加"待审核 N 条"。 |
| `persona/modules/research.py` | 扩展（1 行 + 注释） | 见 2.1，本轮发现的真实接线缺口。 |
| `tests/test_p4_4a_model_extraction.py` | **新增** | 84 项测试，逐条对应用户第六步的覆盖清单。 |
| `docs/ACCEPTANCE_REPORT_P4_4A.md` | **新增** | 本报告。 |

### 2.1 为什么动了"已验收"的 `research.py`

**发现**：`materials_from_extracts()` 交出的 `SourceMaterial` **没有填 `quality_status`**（既有测试只断言 `snippet` / `url`，没人断言过这个字段，所以此前无感）。

**后果是具体的、不是理论上的**：真实链路上每一份材料都是"未声明"状态 →

- 抽取器侧的材料准入**永远看不到结论**，四道闸门里的第一道形同虚设；
- 而计划在上线时开启的 `require_gate_verdict=True` 会把**所有**真实材料都拒掉——等于模型永远跑不起来。

**改法**：走到该处的条目必然 `is_usable_material`（即门禁已判 `VALID`），所以补一句 `quality_status=QUALITY_VALID`，把"已经判过了"这件事告诉抽取器。这不是重复判定，也不是变更门禁逻辑；新增一条测试锁住它（`test_research_declares_the_gate_verdict_on_the_material_it_hands_out`），并验证严格模式下该材料**不会被误拒**。

---

## 3. 输出 schema 与原文摘录验证机制

### 3.1 约定的输出 schema（发给模型的 `response_format="json_schema"`）

```json
{
  "type": "object",
  "properties": {
    "claims": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "key":               {"type": "string"},
          "material_key":      {"type": "string"},
          "claim":             {"type": "string"},
          "tier":              {"type": "string", "enum": ["<EvidenceTier 的全部取值>"]},
          "dimension":         {"type": "string", "enum": ["<EvidenceDimension 的全部取值>"]},
          "subject_entity_ids":{"type": "array", "items": {"type": "string"}},
          "confidence":        {"type": "number"},
          "verbatim_quote":    {"type": "string"},
          "stated_at":         {"type": "string"},
          "derivation_keys":   {"type": "array", "items": {"type": "string"}},
          "note":              {"type": "string"}
        },
        "required": ["material_key", "claim", "tier", "dimension", "subject_entity_ids"]
      }
    }
  },
  "required": ["claims"]
}
```

`enum` 不是自己编的枚举，而是从 `EvidenceTier` / `EvidenceDimension` 现取（`tuple(str(v) for v in ...)`）——新增枚举值会自动进 schema，不会出现"schema 与代码两套枚举"。

### 3.2 逐条校验的顺序（即拒绝原因的优先级）

必填字段 → `claim` 非空 / 长度 → `tier` / `dimension` 枚举 → `material_key` 存在 → 自报元数据 → 主体（严格 `str` 数组 + 白名单）→ `confidence`（`bool` 不算数字、字符串**不转换**）→ 摘录（长度 / 推断不得带摘录 / **逐字核验**）→ `derivation_keys` → `note` → 日期（必须在材料里）→ **待审核判定** → 入库。

### 3.3 三类结论，三种去处（**互斥**：一条候选只会出现在一个清单里）

| 结论 | 含义 | 去处 |
| --- | --- | --- |
| `accepted` | 结构合法 **且** 引用可核验 | 交给 `Evidence` 走既有校验 / 去重 / 登记 |
| `pending` | 结构合法但**核验不了** | **不进**候选清单，转 `WARNING_CLAIM_PENDING_REVIEW`，交人工 |
| `rejected` | 硬性不合格 | 如实记录原因（`WARNING_CLAIM_REJECTED`），不静默丢弃 |

**拒收原因（14 条）**：`ITEM_NOT_OBJECT`、`MISSING_FIELD`、`TYPE_ERROR`、`EMPTY_CLAIM`、`UNKNOWN_ENUM`、`UNKNOWN_MATERIAL`、`UNKNOWN_SUBJECT`、`EMPTY_SUBJECT`、`CONFIDENCE_OUT_OF_RANGE`、`QUOTE_NOT_IN_MATERIAL`、`INFERENCE_WITH_QUOTE`、`INFERENCE_NO_DERIVATION`、`TOO_LONG`、`TOO_MANY_CLAIMS`

**待审核原因（5 条）**：`EMPTY_QUOTE`、`SOURCE_METADATA_MISMATCH`、`UNTRUSTED_MATERIAL`、`INSTRUCTION_LIKE_CLAIM`、`DERIVATION_UNRESOLVED`

**整批结论（8 种）**：`OK` / `EMPTY_OUTPUT` / `REFUSAL` / `INVALID_JSON` / `NOT_OBJECT` / `CLAIMS_MISSING` / `CLAIMS_NOT_LIST` / `TOO_LARGE`。其中前三种是**业务性结果**（返回 `Ok` + 明确 warning），后五种是**模型输出本身不可用**（返回 `Err`）。

### 3.4 摘录核验机制（本轮最关键的一处）

**核验对象**：只核验 `material.snippet`，即**实际送给模型的那一段**。拿全文去核验会得出"模型没引用过的东西也算核验通过"的假结论。

**匹配算法** `find_quote_span(quote, text)`：两轮，**先严格后宽容**——

1. **逐字匹配** `haystack.find(needle)`；
2. 不中，再**折空白后**逐字匹配（容忍换行 / 缩进差异）。

两轮都不中 → `None`，即**拒绝**。不接受"意思差不多"，也不改写摘录去凑。

**定位不是假的**：折空白会改变字符下标，所以 `_norm_index_map(text)` 在折空白的同时记录**每个折后字符对应的原文本下标**，把"折空白文本里的位置"还原成"原文里的位置"。测试直接断言 `material.snippet[start:end]` 等于原文切片。

**`locator` 由框架算出**，不是照抄模型给的位置（测试里故意让模型给一个离谱的 `{"start":999,"end":12345}`，断言被忽略）：`{"field": "snippet", "start": <int>, "end": <int>}`。**模型伪造不了一个它没引用的位置。**

### 3.5 不允许模型凭空生成的字段

`FORGED_METADATA_KEYS = ("source_id", "source_title", "url", "publisher", "author", "published_at", "source_kind", "reliability")`。

- 模型填了这些键 → 记 `FORGED_METADATA_IGNORED`（非致命观察）；
- 填了且**与材料不符**（`source_id` / `url` / `source_title`）→ **转待审核** `SOURCE_METADATA_MISMATCH`，以材料里的真实来源为准。

**主体 ID 白名单**：允许集合 = 材料声明的对象 ∪ 调用方给的目标对象。模型自造一个 `pe_zzz` → `UNKNOWN_SUBJECT` 拒收（防编造引用在**抽取器这一层**就被挡住，而不是等 `Evidence` 才发现"对象不存在"）。

### 3.6 关键原则：**不修补、不猜测**

- `confidence` 收到 `"0.8"`（字符串）→ 拒收，**不自动转数字**。自动修补等于用猜测伪造来源证据。
- `subject_entity_ids` 收到 `"pe_a"`（字符串）→ 拒收，不包成数组。
- `bool` 不算数字（`True` 不是 `1.0`）。
- 摘录核验不过 → 拒收，**不改写**。

---

## 4. 推断、归纳、直接支持主张的区分规则

机械区分，全部可被测试断言：

| 类型 | `tier` | 硬约束 | 登记入口 |
| --- | --- | --- | --- |
| **原文直接支持** | `VERBATIM` | `verbatim_quote` **必须**能在材料里逐字找到，否则拒收（`QUOTE_NOT_IN_MATERIAL`）；**没给摘录** → 待审核（`EMPTY_QUOTE`），**不降级、不补写** | `Evidence.add_evidence` |
| **对材料的归纳** | `PROFILE_BACKGROUND` / `THIRD_PARTY` | 无强制摘录；**带**摘录时同样要能核验（不得拿摘录冒充原话以外的用途） | `Evidence.add_evidence` |
| **系统推断** | `INFERENCE` | ① **不得带摘录**（推断没有"原话"）→ `INFERENCE_WITH_QUOTE` 拒收；② 必须有 `derivation_keys` → `INFERENCE_NO_DERIVATION` 拒收；③ 根必须落在**本批已接受**的候选内 | `Evidence.record_inference` |

**第 ③ 条的实现细节值得单独说明**：`_resolve_derivations()` 在整批校验完成后统一解析——根不在本批已接受候选里（包括"根候选自己也被拒收了"这种情况），该推断会被**从 `accepted` 移出**并转 `PENDING_DERIVATION_UNRESOLVED`。

**为什么是"移出"而不是"加个备注"**：宁可少登记一条，也不登记一条"看着有根、其实指向空气"的推断（EV-V3 禁止无根推断）。编排器侧还有第二道同样的检查（`WARNING_INFERENCE_NO_ROOT`）。

**冲突不合并**：互相矛盾的主张（"阿雾每晚点灯" vs "阿雾隔晚点灯"）各自保留成独立候选，`canon_level` 也各自保留；同一说法来自两个来源时，来源独立性一并保留（两条候选、两个 `source_id`）。AC-15 在抽取器这一层同样成立。

---

## 5. 预算账本兼容情况

**结论：完全复用既有 `ExternalUsageLedger`，没有第二套账本，没有第二套状态机。**

| 复用项 | 说明 |
| --- | --- |
| 四段式闸门（D-089） | 检查 → **预留（发送之前）** → 执行 → 结算 / 占位，逐段对应 `_budget_block()` 与 `_settle()` |
| 估算 vs 实际分离 | `cost_estimate`（事前，按装配层给的单价推算）/ `cost_confirmed`（事后，来自响应）永不混用 |
| 失败 ≠ 免费 | `Err` 或抛异常 → 先结算一笔 `FAILED` + `COST_UNKNOWN`（预留不释放，转 `unknown_hold`），再原样传播 / 抛出 |
| 未知不等于 0（D-060） | 缺 usage → `cost_status=UNKNOWN`、`cost_confirmed=None`，**绝不填 0** |
| 幂等三层 | ① 发送层守卫（`ledger.has_attempted` + sink `has_external_usage`）→ ② 内存 `settle()` 返回 `False` → ③ 存储主键冲突 |
| `BUDGET_EXCEEDED` | 保持"可恢复暂停信号"语义（不可重试但可续跑） |

**本轮为模型调用新增的最小兼容能力**（都在 `usage_ledger.py`，不新建模块）：

- `ModelCallLimits(max_calls, max_cost, price_per_1k_in, price_per_1k_out, currency, est_tokens_in, est_tokens_out)`——**单价由装配层注入，本模块不内置任何单价**；
- `model_call_key_parts(model, material_keys, target_ids, prompt_version)`——模型调用幂等键的**唯一口径**（材料键排序，顺序无关；提示词版本进键，改提示词 = 另一次抽取意图）；
- `estimate_model_cost()`（事前）/ `cost_from_model_usage()`（事后）。

**两条必须记住的硬规则**：

1. **单价缺失 + 设了费用上限 → 拒绝自动发起请求**（`BUDGET_EXCEEDED`）。"无法估算"就等于"无法保证不超上限"。
2. `estimate_model_cost()` 在**单价有、token 数却是 0** 时也返回 `None`。用 0 当估算会让"设了上限"的闸门形同虚设（未知不是 0）。

**费用来源的优先级**：`usage.cost_confirmed`（服务商回报）→ `SOURCE_PROVIDER_USAGE`；否则按装配层单价 × 响应回报的真实 token 数 → `SOURCE_OFFICIAL_RATE`；都不行 → `COST_UNKNOWN`。

**本轮未产生任何模型 API 费用**：所有调用都打在本地 stub provider 上。

---

## 6. 两个候选模型服务商的官方资料、价格与接入差异

**尚未选定服务商**，以下是按用户第四步要求核实的官方资料（信息以 2026-10-15 取到的官方页面为准；**价格与条款会变，接入前必须重新核对**）。

### 6.1 候选 A：DeepSeek

| 项 | 实际情况 | 来源 |
| --- | --- | --- |
| 结构化输出 | **只支持 `response_format={"type":"json_object"}`**，**不支持**严格 schema 模式 | <https://api-docs.deepseek.com/guides/json_mode> |
| 提示词要求 | 官方要求 **prompt（system 或 user）里必须出现 "json" 这个词**，并建议给出 JSON 示例 | 同上 |
| 官方承认的坑 | 原文：**"When using the JSON Output feature, the API may occasionally return empty content."** | 同上 |
| 模型名 | `deepseek-flash`（DeepSeek-V4.1-Flash）、`deepseek-v4-pro`（DeepSeek-V4-Pro-0813）；旧名 `deepseek-v4-flash` 仍接受但已下线，请求由 V4.1-Flash 承接并按 Flash 价计费 | <https://api-docs.deepseek.com/quick_start/pricing> |
| 上下文 / 输出 | 上下文 1M，最大输出 384K；支持 thinking / non-thinking 两种模式 | 同上 |
| 价格（每 1M token，**USD**） | `deepseek-flash` / `deepseek-v4-pro`：输入命中缓存 闲时 $0.003 / $0.022、峰时 $0.006 / $0.044；输入未命中缓存 闲时 $0.15 / $0.66、峰时 $0.3 / $1.32；输出 闲时 $0.6 / $1.98、峰时 $1.2 / $3.96 | 同上 |
| 峰时定义 | 峰时 = **01:00–04:00 与 06:00–10:00 UTC，周一至周五**，不含中国法定节假日；其余时段（含周末与完整节假日）一律闲时 | 同上 |
| 限流 | 并发连接数 **2500（flash）/ 500（v4-pro）**，按**账号**计算（与用哪个 API Key 无关）；超限返回 **HTTP 429** | <https://api-docs.deepseek.com/quick_start/rate_limit> |
| 保活与超时 | 非流式请求在等待期间**持续返回空行**、流式返回 SSE `: keep-alive` 注释（自行解析 HTTP 响应时必须能容忍）；**若请求 10 分钟内仍未开始推理，服务端会关闭连接** | 同上 |
| 用户隔离 | 可选传 `user_id` 做内容安全隔离 / KVCache 隔离 / 调度隔离；官方明确要求 `user_id` **不得包含用户隐私信息** | 同上 |
| 接入方式 | OpenAI 兼容 base URL `https://api.deepseek.com`；另有 Anthropic 格式入口 | <https://api-docs.deepseek.com/quick_start/pricing> |
| 隐私（**已核实部分**） | DeepSeek 的 Harness 产品条款写明：在加密、严格去标识化、不可反向识别特定个人的前提下，**可能以最小限度使用输入输出**来提供、维护、运营、开发或改进服务及其底层技术；用户可通过联系渠道**选择退出（opt out）** | <https://deepseek.com/harness/terms-of-use>（II. 输入与输出 第(3)条） |
| 隐私（**未核实部分**） | **开放平台 API 的专属隐私条款原文本轮未取到**。上述引用来自 DeepSeek Harness 产品的条款，**不等同于** API 平台的条款 | —— |

**接入本项目时的注意点**：
- 本项目 `ChatRequest.response_format` 目前会传 `"json_schema"`——对 DeepSeek **不适用**，接入时要切成 `"json_object"`，并确保提示词含 "json" 字样（现有 system prompt 已含 `JSON`，但需显式确认大小写与位置）。
- 官方承认"偶发返回空内容"——本项目已把 `EMPTY_OUTPUT` 当**业务性结果**处理（`Ok` + `WARNING_MODEL_RETURNED_EMPTY`），不报错、也不谎报成功，这个设计正好对上。
- 价格币种是 **USD**，`ModelCallLimits.currency` 必须相应设置，否则账本里的金额单位会失真。

### 6.2 候选 B：阿里云百炼 / 通义千问

| 项 | 实际情况 | 来源 |
| --- | --- | --- |
| 结构化输出 | 支持**两种**模式：`{"type":"json_object"}`（需提示词含 "JSON"）与 **`{"type":"json_schema","json_schema":{...},"strict":true}`**（**严格遵循 schema，提示词无需含 JSON**） | <https://help.aliyun.com/zh/model-studio/qwen-structured-output> |
| JSON Schema 模式的支持范围 | 官方标注"**仅支持部分模型**"，具体系列以官方页面表格为准（千问 Max / Plus / Flash 的若干系列在列） | 同上 |
| OpenAI 兼容参数 | `response_format` 可取 `text` / `json_object` / `json_schema`；`json_schema` 需带 `name` 与 `schema`，`strict` 推荐 `true`，`additionalProperties` 推荐 `false` | <https://help.aliyun.com/zh/document_detail/3016807.html> |
| 价格（每 1M token，**CNY**，华北 2 北京） | `qwen3.8-flash`：输入 **0.8 元**、输出 **2.7 元**（单档，0 < Token ≤ 1M）。`qwen3.7-plus`：0 < Token ≤ 256K 输入 **2.4 元** / 输出 **9.6 元**；256K < Token ≤ 1M 输入 **7.2 元** / 输出 **28.8 元**。`qwen3.8-max`：0 < Token ≤ 1M 输入 **14.4 元** / 输出 **43.2 元** | <https://help.aliyun.com/zh/document_detail/2987148.html> |
| 计费粒度 | 按**单次请求的输入 token 总量**落档结算（阶梯）；部分模型有 90 天免费额度（华北 2 北京） | 同上 |
| 缓存 | 上下文缓存仅对**输入** token 打折，且与免费额度**不能同时生效** | 同上 |
| 限流维度 | 按**主账号**维度（所有 RAM 子账号 / 业务空间 / API Key 的调用量**合并**计算）；按模型独立限流；除 RPM / TPM 外还可能按秒级 **RPS（RPM/60）/ TPS（TPM/60）** 执行——短时突发即使未超分钟总量也会被拒 | <https://help.aliyun.com/zh/model-studio/rate-limit> |
| 限流阈值（华北 2 北京） | `qwen3.7-plus`：**RPM 30,000 / TPM 5,000,000**；`qwen3.8-flash`、`qwen3.8-max`：**动态限流**（按百炼月消费档位软限流，无固定数值） | 同上 |
| 超限表现与恢复 | **429** = 速率限制（`Requests rate limit exceeded` / `You exceeded your current requests list` = RPM；`Allocated quota exceeded` / `You exceeded your current quota` = TPM；`Request rate increased too quickly` = 突发保护）；**通常一分钟内自动恢复**。**403** = 免费额度耗尽（二者都是拒绝机制） | 同上 |
| 充值是否提额 | **不会**。限流按主账号配置，与计费独立；充值只保证不欠费停服 | 同上 |
| 隐私（**已核实**） | 官方原文：**"阿里云严格保护数据隐私，绝不会将您的数据用于模型训练"**；数据经 **AES-256** 加密；以**无保留意见**通过 **SOC 2** 审计；但同时明确 **"根据相关法律法规要求，阿里云百炼将存储模型与应用调用时产生的数据"** | <https://help.aliyun.com/zh/model-studio/privacy-notice> |
| 隐私条款的例外 | 上述"不用于训练"承诺**仅适用于按量付费 API 与 Token Plan 团队版**；**Token Plan 个人版与 Coding Plan 的数据使用条款不同**（Coding Plan 期间输入与生成内容将用于服务改进与模型优化），选型时必须确认自己走的是哪一类 | <https://help.aliyun.com/zh/model-studio>（常见问题） |

**接入本项目时的注意点**：
- 币种是 **CNY**，与 DeepSeek 的 USD 不同 → 两个候选**必须**分别配置 `ModelCallLimits.currency`，且**不可混用同一本账的金额判断**。
- 阶梯计费意味着"单次请求输入 token 总量"决定单价档位，**事前估算的分档假设会直接影响预算预留**：估算时若按 256K 以内档位，而实际输入超过 256K，实际单价比估算高。这一条要在装配层显式决定（保守做法：按更高档位估算）。
- `strict: true` 是本项目最想要的能力（模型严格遵守 schema，可以减少 `TYPE_ERROR` / `UNKNOWN_ENUM` 类拒收），但**只对部分模型生效**，选型时要逐一核实目标模型是否在列。

### 6.3 两者的关键差异（一句话对比）

| 维度 | DeepSeek | 阿里云百炼 |
| --- | --- | --- |
| 严格 schema | ✗（只有 json_object） | ✓（`strict: true`，仅部分模型） |
| 提示词是否必须含 "json" | ✓ 必须 | json_object 需要；json_schema **不需要** |
| 官方承认空输出 | ✓ 明确承认（"may occasionally return empty content"） | 未在本次核实的页面中看到同类表述（**未知**） |
| 限流表达方式 | 并发连接数上限（2500 / 500），超限 429 | RPM + TPM（+ 秒级 RPS/TPS），超限 429，通常 1 分钟内恢复 |
| 币种 | **USD** | **CNY** |
| 超时行为 | 10 分钟未开始推理 → 服务端关连接；先持续回空行 | 未在限流页看到同类超时表述（**未知**） |
| 隐私（对"是否用于训练"） | 在产品条款里写明**可能以最小限度使用**输入输出改进服务，**可 opt out**；API 专属条款原文**未取到** | 按量付费 API 明确**不会**用于模型训练；但会**存储**调用产生的数据；个人版 / Coding Plan 例外 |

**接入差异对本项目的直接影响（三条）**：

1. **提示词**：DeepSeek 要求 prompt 里必须出现 "json"。现有 `_system_prompt()` 已嵌入 `json.dumps(EXTRACTION_JSON_SCHEMA)`（含 "JSON Schema" 字样），但仍需在接入时**显式确认**大小写与位置，不要依赖"应该包含"。
2. **`response_format` 适配**：`_build_request()` 当前固定传 `"json_schema"`。DeepSeek 只认 `"json_object"`，接入时必须按服务商切换——这是**已知的、待接入时处理的差异点**，不是遗漏。
3. **金额单位**：USD 与 CNY 不能混进同一本账的金额比较；`ModelCallLimits.currency` 必须随服务商设置。DeepSeek 还有峰时 / 闲时价差（峰时为 01:00–04:00 与 06:00–10:00 UTC 工作日），同一模型在不同时段单价差一倍，估算时要显式选定按哪个档位预留。

**本报告未做的核实**（不得当作已知）：两家 API 的**数据保留期限**、**服务等级 / 超时承诺细节**、以及 **DeepSeek 开放平台 API 的专属隐私条款原文**，本轮均未取到官方原文。这些**不作为选型依据**，列入第 8 节人工决策清单。

---

## 7. 测试命令与通过 / 失败数量

（本节数字来自本轮**实际执行**的命令输出。）

### 7.1 本轮新增单文件

```
"C:/Users/36135/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m unittest tests.test_p4_4a_model_extraction
```

```
Ran 84 tests in 1.346s

OK
```

**通过 84 / 失败 0 / 错误 0。**

### 7.2 全量回归（改动全部落地后执行）

```
"C:/Users/36135/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m unittest discover -s tests -t .
```

```
Ran 980 tests in 159.227s

OK
```

**通过 980 / 失败 0 / 错误 0。**

### 7.3 与基线的对照

| | 改动前 | 改动后 |
| --- | --- | --- |
| 全量测试数 | 896 | 980 |
| 结果 | OK | OK |

差值 +84，等于本轮新增的测试数，即 **既有测试一项未删、一项未放宽、一项未被改动**（既有 `tests/test_claim_extraction.py` 的 28 项 G-2 契约、G-4、H-10、依赖规则等全部原样通过）。

### 7.4 覆盖清单与测试落点对照

| 用户第六步要求 | 测试类 / 方法（`tests/test_p4_4a_model_extraction.py`） |
| --- | --- |
| 合格的原文支持主张 | `TestStrictPayloadValidation.test_verbatim_claim_with_verifiable_quote_is_accepted`、`test_locator_is_computed_by_the_framework_from_the_material`、`test_quote_across_line_breaks_is_matched_by_folding_whitespace`、`test_find_quote_span_rejects_a_paraphrase` |
| 材料归纳与系统推断的区分 | `TestThreeKindsOfClaims`（7 项，含 `test_three_kinds_are_distinguishable`） |
| 伪造摘录 | `test_fabricated_quote_is_rejected`、`test_quote_from_another_material_is_rejected` |
| 错误 source ID | `test_unknown_material_key_is_rejected`、`test_fabricated_subject_id_is_rejected`、`test_self_reported_source_metadata_mismatch_goes_pending` |
| 缺失字段 | `test_missing_required_field_is_rejected`（5 个必填字段逐个覆盖） |
| 无效 JSON | `test_invalid_json`、`TestModelOutcomeMapping.test_invalid_json_is_an_error_not_an_empty_success` |
| 空输出 | `test_empty_output`、`TestModelOutcomeMapping.test_empty_output_is_a_warning_not_a_fake_success` |
| 过长输出 | `test_oversized_output_is_treated_as_truncated`、`test_too_long_claim_and_quote_and_note_are_rejected`、`test_claims_beyond_the_per_call_limit_are_rejected_not_silently_dropped` |
| 拒答 | `TestMalformedAndBusinessOutcomes` 的 3 条（`finish_reason`、payload 内 `refusal`、非 JSON 启发式）+ `test_a_valid_json_payload_containing_refusal_words_is_not_misread` |
| 网页提示注入 | `TestPromptInjectionBoundary`（4 项，含 `test_injected_material_never_overrides_the_research_rules`） |
| 互相矛盾的主张 | `test_contradicting_claims_are_kept_separately`、`test_same_claim_from_two_materials_keeps_both_sources` |
| 低质量正文被阻止进入抽取器 | `TestMaterialAdmission`（7 项，含"模型一次都没被调用"的断言与 Research→抽取器的结论传递） |
| 重复调用 / 幂等性 | `TestIdempotency`（6 项） |
| 异常处理 | `test_failed_call_is_settled_as_failed_and_unknown`、`test_raising_provider_is_settled_then_re_raised`、`test_sink_failure_is_treated_conservatively_as_already_charged` |
| 候选 claim 仍需 Evidence 模块校验 | `TestCandidatesStillNeedEvidence`（3 项）+ `TestOrchestratorWiring.test_model_output_still_goes_through_evidence_validation`（端到端落到证据表） |
| 现有 G-2 / G-4 / H-10 及完整回归 | 见 7.2 全量回归 |

---

## 8. 下一阶段真实模型冒烟测试所需的最少配置与人工决策

### 8.1 必须由人做出的决策（不能由代码替用户决定）

| # | 决策 | 为什么必须人工 | 建议 |
| --- | --- | --- | --- |
| D1 | **选哪家、哪个模型 ID** | 涉及采购、隐私条款、可用性；且 DeepSeek 不支持严格 schema，会改变校验预期 | 若最看重"模型严格遵循 schema"→ 百炼 `strict: true`；若最看重成本 → DeepSeek 闲时价（注意峰时 / 闲时切换） |
| D2 | **单价与币种** | 本项目**刻意不内置任何单价**（会过期）。必须由装配层填入 `ModelCallLimits.price_per_1k_in/out` 与 `currency` | 按本报告第 6 节价格逐项填入；**USD 与 CNY 不可以混进同一本账的金额比较** |
| D3 | **预算上限** `max_calls` / `max_cost` | 这是"允许多少钱被自动花掉"的业务决定 | 首次冒烟建议 `max_calls=1`、`max_cost` 取单次估算的 2~3 倍 |
| D4 | **是否开启 `require_gate_verdict=True`** | 严格模式要求每份材料都亮出门禁结论；检索摘要本来就没有结论 | 上线接真实模型时建议**开启**（本轮已补上 `research.py` 的缺口，使真实链路不再被误拒） |
| D5 | **`pending` 的处置方式** | 目前只以 warning 暴露，没有复核队列 / 待办表 | 首次冒烟先"人看日志"；若要长期运行，需要建持久化队列（本轮未做） |
| D6 | **隐私条款是否可接受** | 两家对"数据是否用于训练 / 保留多久"的表述不同，影响能否上传真实人物资料。**已核实的事实**：① 百炼按量付费 API 明确"绝不会将您的数据用于模型训练"，但同时会**存储**调用产生的数据（个人版 / Coding Plan 例外，条款不同）；② DeepSeek 在其产品条款里写明**可能以最小限度使用**输入输出来改进服务（可 opt out），**开放平台 API 的专属条款原文本轮未取到** | 若处理的是**真实人物资料**，建议优先选明确承诺"不用于训练"的那一方，并**另行确认其数据保留期限**；无论选谁，都应在装配层避免把不必要的原文整段送入模型 |
| D7 | **API key 的注入方式** | 密钥绝不能进仓库 | 走既有 `persona.config`（唯一的读环境变量入口，D-077）+ 本地 `.env`（已在 `.gitignore`）；不写入任何源码或测试 |

### 8.2 最少配置（代码样例）

```python
from persona.core.usage_ledger import ExternalUsageLedger, ModelCallLimits

extractor = ProviderClaimExtractor(
    provider,                              # 已装配的真实 Provider（唯一外部出口，D-054）
    model_hint="<选手的模型 ID>",
    limits=ModelCallLimits(
        max_calls=1,                       # D3
        max_cost=<单次估算的 2~3 倍>,        # D3
        price_per_1k_in=<官方单价>,          # D2
        price_per_1k_out=<官方单价>,         # D2
        currency="<USD 或 CNY>",            # D2
        est_tokens_in=<材料 token 估算>,     # 必须 >0，否则费用视为未知、请求被拦
        est_tokens_out=<预期输出 token 估算>,
    ),
    ledger=ExternalUsageLedger(),           # 复用 P4.5 账本
    usage_sink=storage,                     # 落库 → 跨轮幂等 + 事后对账
    task_id=<任务 ID>,
    require_gate_verdict=True,              # D4
)
```

**注意**：DeepSeek 需要把 `ProviderClaimExtractor._build_request()` 里的 `response_format` 从 `"json_schema"` 改为 `"json_object"`（并确认提示词含 "json"）。这是一处**已知的、待接入时按服务商调整**的差异点，不是遗漏。

### 8.3 最小冒烟步骤（含"先不发请求"的一步）

1. **构造 1 份材料 + 1 个目标**（合成虚构人物，AC-16；**不要**用真实人物资料做首测）。
2. **dry-run 打印请求**：不调 `provider.chat`，只构造 `ChatRequest` 并打印 messages 与 schema——确认规则进 system、材料进带标记的数据块、白名单主体 ID 正确。
3. **真发 1 次**（`max_calls=1`）：记录服务端 `request_id`、`usage`、实际费用，与事前估算对比。
4. **核对四件事**：① 模型返回是否为合法 JSON 且符合 schema；② 声称摘录的核验通过率；③ 是否有候选落到 `pending`（哪些原因）；④ 账本记录的 `cost_status` / `cost_source` 是否为预期值。
5. **对照**：把真实产出的候选与"人工读原文应该抽到什么"做一次比对，给出**真实抽取质量**的结论——这一步**只有人能下判断**，本轮未做。

### 8.4 冒烟测试**不得**宣称的结论

- 不得因为本文档第 7 节的测试全部通过，就宣称"真实模型抽取质量已通过"——那些测试跑的是**本地固定字符串**。
- 冒烟一次成功 ≠ 质量达标；至少需要多份材料、多种页面类型，并统计**摘录核验失败率**与**待审核率**才有意义。

---

## 9. 本轮明确未做的事（留待人工验收后处理）

1. **不改动 `Provider` 协议**：其五方法签名被既有测试钉死（`capabilities` / `chat` / `search` / `usage` / `health`），新增能力一律放独立协议（`AttemptSinkPort`）。
2. **不登记决策文档**：`DECISIONS.md` / `INTERFACES.md` / `RUNNING.md` 未改动。是否为本阶段补记决策条目（如"模型单价不内置"、"pending 作为第三清单"）留待人工验收时一并决定，避免擅自改动已验收文档。
3. **不做页面类型黑名单**：`WEBSITE_BLACKLIST` 在 `persona/` 中**无任何消费者**，而检索侧已有 Tavily 的 `exclude_domains` / `include_domains`；再加一层属于重复建设，本轮不加。
4. **不自动提交 Git**：本轮改动全部留在工作区，未 `git add` / `git commit`。
5. **未注册账号、未创建密钥、未请求用户发送密钥**；未产生任何模型 API 费用。
