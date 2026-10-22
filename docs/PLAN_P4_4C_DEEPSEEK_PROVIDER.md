# P4.4-C 实施方案：DeepSeek Chat Provider

> **状态：待人工确认。本文档不含任何代码改动，未发起任何真实请求，未提交 Git。**
>
> 前置：P4.4-B 已通过（配置层 `model.*` 16 项、装配层 `model_setup.py`、1041 项测试全绿）。
> 本方案回应你授权的缺口 1（无真实聊天 Provider）与缺口 2（无真实材料）。

---

## 0. 一句话概览

新增 `persona/providers/deepseek.py`，实现 `Provider.chat()`，把抽取器的**统一契约**翻译成 DeepSeek 的**线格式**。

三条贯穿全篇的设计线：

1. **不用任何厂商 SDK** —— 沿用既有 `TavilyProvider` 的标准库 `urllib` 范式（D-054 禁令 + 全包静态扫描）。
2. **Provider 只说 token，不碰钱** —— 单价/币种/档位已在 P4.4-B 接好，记账点仍在抽取器；Provider 若也记一笔就会**双重占用预算**（见 §2.3，这是本方案最关键的一处判断）。
3. **默认安全** —— 关掉 Provider 内部重试（重试 = 重复计费）、显式关闭思考模式、prompt 不合规就 fail loud 而不是静默改写。

---

## 1. 已核实的 DeepSeek 官方契约

全部来自官方文档，核对日期 **2026-10-16**。凡官方文档没有明说的，本文档标「**未知**」而不猜。

| 项 | 官方值 | 来源 |
| --- | --- | --- |
| Base URL | `https://api.deepseek.com` | [Your First API Call](https://api-docs.deepseek.com/) |
| 端点 | `POST /chat/completions` | [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion) |
| 鉴权头 | `Authorization: Bearer ${DEEPSEEK_API_KEY}` | 官方 curl 示例原文 |
| 模型名 | `deepseek-flash` / `deepseek-v4-pro`（旧名 `deepseek-v4-flash` 仍被接受，但已退役，按 Flash 价计费） | 同上 |
| `response_format` | **仅** `text` / `json_object`，**没有** `json_schema` | Create Chat Completion |
| JSON 模式硬要求 | ① 设 `response_format={'type':'json_object'}`；② **prompt 里必须出现 "json" 字样**并给出期望格式示例；③ 合理设置 `max_tokens` 防截断 | [JSON Output](https://api-docs.deepseek.com/guides/json_mode) |
| 官方承认的缺陷 | 原文：**"the API may occasionally return empty content"** | JSON Output |
| prompt 缺 "json" 的后果 | 原文：模型可能 **"generate an unending stream of whitespace until the generation reaches the token limit"**（长跑、看起来像卡死） | Create Chat Completion |
| 输出上限字段 | **只有 `max_tokens`**（1 ~ 384K）；**没有** `max_completion_tokens` | Create Chat Completion |
| `temperature` | 0–2，**默认 1**；官方注：**"Has no effect in thinking mode"** | Create Chat Completion |
| `thinking` | `{"type": "enabled"/"disabled"}`，文档标默认 `enabled` | Create Chat Completion |
| `reasoning_effort` | `none`/`low`/`high`/`max`，默认 `high` | Create Chat Completion |
| `stream` | 默认非流式；官方另注：非流式等待期会持续返回空行 keep-alive，**10 分钟未开始推理则服务端关连接** | Pricing / Rate limit 页 |
| 响应 `id` | 顶层 `id`（唯一标识，用于对账/报障） | Create Chat Completion |
| 响应内容 | `choices[0].message.content`（string, nullable）；思考模式下另有 `message.reasoning_content` | Create Chat Completion |
| `finish_reason` | `stop` / `length` / `content_filter` / `tool_calls` / `insufficient_system_resource` / `aborted` —— **注意：没有 `refusal`** | Create Chat Completion |
| `usage` | `prompt_tokens`（= `prompt_cache_hit_tokens` + `prompt_cache_miss_tokens`）、`completion_tokens`、`total_tokens`；思考模式下 `completion_tokens_details.reasoning_tokens` | Create Chat Completion |
| 是否 OpenAI 兼容 | **是**（官方原文："uses an API format compatible with OpenAI/Anthropic"）—— 但我们**不用** openai SDK，仍走 urllib | Your First API Call |

### 1.1 一处文档内部不一致（**不猜，列为实测项**）

`create-chat-completion` 页标注 `thinking.type` 默认 **`enabled`**，但首页示例却**显式**传 `"thinking": {"type": "enabled"}`。两种可能：

- 默认确实是 enabled，示例只是明示；
- 或该字段必填 / 默认值随版本变化。

**本方案的处理：一律显式传 `{"type": "disabled"}`** —— 无论默认是什么，显式传值让行为**确定**，且这是本任务需要的取值（理由见下）。

### 1.2 为什么建议关闭思考模式（**请你拍板，D1**）

抽取是**确定性结构化任务**，不需要推理链。而思考模式开启时会：

- 生成 `reasoning_content`，其 token **计入 `completion_tokens`** → **直接推高费用**；
- `reasoning_effort` 默认 `high`，推理更长；
- 我们给的是 `max_tokens=4096`，**推理 token 与正文内容共享这个上限** —— 极端情况下推理把额度吃光，返回 `finish_reason=length` 且 content 为空，被 `claim_validation` 判为 `EMPTY_OUTPUT`（业务上等于"白花一次钱"）。

即：**开启思考 = 更贵、更慢、格式风险更高**，对本任务没有收益。建议 `disabled`。

---

## 2. 架构落点

### 2.1 位置（不破坏既有分层）

```
persona/config/          ← 已就绪：model.* 16 项（P4.4-B）
        │  AppSettings
        ▼
persona/orchestrator/model_setup.py   ← 本方案**加一个函数**：chat_provider_from_settings()
        │  Provider + ModelCallLimits + ledger
        ▼
persona/modules/extraction.py         ← 已就绪（P4.4-A）：_budget_block / _settle / 严格校验
        │  ChatRequest
        ▼
persona/providers/deepseek.py         ← **本方案新增**：HTTP + 线格式映射 + 归一化
```

分层自查（`FORBIDDEN_VENDOR_IMPORTS` 与 `LAYER_RULES` 均不被触碰）：

- `providers/deepseek.py` 只 import `..core.*` 与 `.base` —— F-12 禁止的是 import `..modules` / `..orchestrator`，本文件不碰；
- 不 import `openai` / `httpx` / `requests` / `anthropic` / `boto3` —— 只用 `urllib` / `json` / `time`；
- 不读环境变量（D-077），密钥由装配层经 `Secrets` 注入；
- `model_setup.py` 本来就是"配置 → 组件"的唯一装配点，加一个工厂函数符合它的既定职责。

### 2.2 与抽取器的接口面：**零协议改动**

只用既有的 `ChatRequest` / `ChatResponse`（`Provider` 五方法签名被测试钉死，不动）：

| 方向 | 用到的字段 | 说明 |
| --- | --- | --- |
| 进 | `messages` / `model_hint` / `response_format` / `json_schema` / `temperature` / `max_output_tokens` / `stop` / `metadata` | `metadata` **不发往网络**（含 `prompt_version`，属内部审计信息） |
| 出 | `text` / `structured` / `finish_reason` / `model_id` / `usage` / `raw_ref` | `usage` 只填 token，**不填金额**（见 §2.3） |

### 2.3 ⚠️ 记账点：Provider **不记账**（本方案最关键的一处判断）

P4.4-A 已把记账接在抽取器的 `_settle()` 里，幂等键口径是 `_call_key()`：

```
provider_name | CHAT | 模型 ID | 排序后的材料键 | 排序后的目标 ID | prompt_version
```

**如果 DeepSeek Provider 也照 Tavily 的样子自己 `settle` 一笔**，它只能用 Provider 侧**自己推导的键**（Provider 无从得知 `_call_key` 里的 `prompt_version`、材料键排序口径）→ 同一笔真实请求在账本里**出现两条不同键的记录** → 后果不是"重复计费"（钱只花一次），而是：

- `ledger.occupied_cost()` 被**虚占一倍**；
- `P4.4-C` 的单任务上限 0.25 USD 实际只剩一半可用；
- `unknown_requests` 计数可能虚高，触发 `should_pause` 误停。

所以本方案的落点是：

```
Provider  负责：HTTP 调用 + 错误映射 + 响应归一化 + 报 token 数（客观事实）
抽取器    负责：预算预留（发送前）+ 记账（唯一键口径）+ 未通过校验则拒收
装配层    负责：把配置里的单价/币种/档位翻译成 ModelCallLimits 注入抽取器
```

**关于你的要求「复用既有 UsageLedger，单价、币种、档位从配置读取」——已满足，但落点如上表**：

- **同一本账**：装配层把**同一个** `ExternalUsageLedger` 实例注入抽取器，Provider 的 token 数经 `ChatResponse.usage` **流入这本账**（无第二本账）；
- **单价/币种/档位**：P4.4-B 已由 `model_setup.model_limits_from_settings()` 从 `PERSONA_MODEL_PRICE_*` / `PERSONA_MODEL_CURRENCY` / `PERSONA_MODEL_PRICE_TIER` 读出并换算成 `ModelCallLimits`；
- **Provider 侧不读单价**：Provider 只知道"这次用了多少 token"，**不知道也不该知道**这些 token 值多少钱 —— 那是账单口径，属于装配层的唯一换算点。**若 Provider 也持有一份单价，就出现了第二个换算点**，正是 `model_setup.py` 开场白里点名要避免的事。

`tokens_in` / `tokens_out` 的语义**完全对得上**：抽取器的 `_settle()` 会走这个分支 —

```
elif limits is not None:
    confirmed = cost_from_model_usage(limits, tokens_in=…, tokens_out=…)
    source = SOURCE_OFFICIAL_RATE        # ← 语义准确：不是官方回报金额，是按官方费率推算
```

DeepSeek 响应里**只有 token、没有金额**，所以这笔被标成 `SOURCE_OFFICIAL_RATE`（而不是 `SOURCE_PROVIDER_USAGE`）**正是准确的说法**。因此 Provider **绝不填 `cost_confirmed`** —— 填了就会把"推算值"冒充成"官方回报值"。

> **备选方案（不推荐，供你对比）**：让 Provider 自记账，则需改 `extraction.py` 的 `build_request()` 把 `_call_key()` 塞进 `metadata["idempotency_key"]`，两边共用一键。代价：① 改动 P4.4-A 已验收模块；② 抽取器要**跳过**自己的结算逻辑（否则还是双记）；③ 键从"发送前算不出"变成"发送后才知道"，预留与结算的先后关系被打破。**三条都是净损失，故不推荐。**

### 2.4 ⚠️ Provider 内部重试必须关掉

`persona/config/spec.py` 的 `RetryLimits.max_retries` 默认 **3**，`to_provider_config()` 会把它带进 `ProviderConfig`。Tavily 靠这个配置在 429/5xx 时重试。

**对按 token 计费的聊天模型，重试 = 真的再花一次钱**（Tavily 按次计费也一样，只是当时没暴露）。

所以装配层的 `chat_provider_from_settings()` 会构造：

```python
ProviderConfig(max_retries=0, per_call_timeout_ms=<配置值>, ...)
```

即**复用 `settings.retry` 的超时与退避参数，但把 `max_retries` 强制置 0**，并在 `model_plan()` 的 `notes` 里打印这条。这样"忘了改配置就重复花钱"这个陷阱**默认不存在**。这也与你 P4.4-C 的"不循环重试"要求一致。

### 2.5 超时语义（**真实风险，需在冒烟时盯着**）

`PERSONA_RETRY_TIMEOUT_MS` 默认 30 000 ms。但 DeepSeek 官方说明：非流式请求在等待期会**持续返回空行 keep-alive**，10 分钟未开始推理才由服务端关连接。

urllib 的 `timeout` 是 **socket 级**（连接/读空闲）超时 —— 服务端持续吐 keep-alive 空行时，socket 并不空闲，**该超时可能不触发**，于是实际等待可能远超 30 秒。

处理：**保持 30 s 不改**（早失败比长挂起安全，且不重试），但在冒烟报告里记录真实耗时。若实测经常触顶，再单独讨论调大（属配置改动，需你授权）。

---

## 3. 文件改动清单

| 文件 | 新增/修改 | 内容 | 估算 |
| --- | --- | --- | --- |
| `persona/providers/deepseek.py` | **新增** | `DeepSeekProvider`：`capabilities` / `chat` / `search`（拒绝）/ `usage` / `health` + 线格式映射 + 错误映射 | ≈ 340 行 |
| `persona/providers/registry.py` | 修改 | 注册 `"deepseek"` 工厂；密钥取 `secrets.provider_api_key`（不包裹离线护栏，同 Tavily） | +14 行 |
| `persona/providers/__init__.py` | 修改 | 导出 `DeepSeekProvider` | +2 行 |
| `persona/orchestrator/model_setup.py` | 修改 | 新增 `chat_provider_from_settings()`；`model_plan()` 加两条 notes（重试已置 0、思考模式取值） | +45 行 |
| `tests/test_p4_4c_deepseek_provider.py` | **新增** | 离线测试（全部打桩 `_urlopen`，零联网） | ≈ 620 行 / ≈ 41 项 |
| `docs/CONFIG.md` | 修改 | §11.7 新增"接入 DeepSeek Provider"一节（含本文档 §1 的契约表与本文档 §2.3/2.4 的边界说明） | +40 行 |

**不改**：`persona/config/*`（本轮无新配置项）、`persona/modules/extraction.py`、`persona/modules/claim_validation.py`、`persona/core/*`、`.env.example`（密钥变量名 `PERSONA_PROVIDER_API_KEY` 已存在）。

---

## 4. 映射规则表：统一契约 → DeepSeek 线格式

| `ChatRequest` | DeepSeek body | 规则 |
| --- | --- | --- |
| `model_hint` | `model` | 空则用装配层注入的 `model_id`（即 `deepseek-flash`） |
| `messages` | `messages` | `[{role, content}]` 直传；`role` 仅 system/user/assistant |
| `response_format="json_schema"` | `response_format={"type":"json_object"}` | **统一契约 → 厂商线格式**的唯一映射点。DeepSeek 不支持严格 schema，故降级为 `json_object`；schema 内容**不需要额外注入**，因为抽取器的 system prompt 已嵌入完整 JSON Schema 文本 |
| `response_format="text"` | **不发送该字段** | 与服务端默认一致，不制造差异 |
| `json_schema` | （不发送） | 见上行说明 |
| `max_output_tokens` | `max_tokens` | DeepSeek **只有** `max_tokens`，没有 `max_completion_tokens` |
| `temperature` | `temperature` | 直传（**注意 D2**：DeepSeek 默认 1，抽取器默认 0.7 —— 我们会显式发 0.7，覆盖其默认） |
| `stop` | `stop` | 空列表 → **不发送** |
| `metadata` | （不发送） | 含 `prompt_version`，属内部审计信息，不外发 |
| — | `thinking={"type":"disabled"}` | **显式**发送（见 §1.2 / D1） |
| — | `stream=false` | 显式发送，避免依赖默认值 |

### 4.1 JSON 模式的前置校验（**不静默改写 prompt**）

映射为 `json_object` 时，Provider 会检查 messages 里是否出现 "json"（**不区分大小写**）：

- **有** → 照常发送。*当前状态：抽取器的 system prompt 含"只输出 JSON"、"JSON Schema"等多处，实测满足。*
- **没有** → 返回 `Err(EXTERNAL_UNAVAILABLE)`，消息写明"DeepSeek 的 `json_object` 模式要求提示词包含 json 字样，否则模型可能输出到 token 上限为止的空白"。

**为什么是"拒绝"而不是"自动补一句"**：自动往调用方的 prompt 里塞文字是**隐藏行为** —— 调用方以为自己控制了 prompt，实际被改过，而且 dry-run 看到的与实际发出的不是同一份请求。本项目一贯"不静默降级"，故此。这条也让 dry-run 具备真实价值：打印出来的请求若通不过这道校验，就说明**真跑也会被拦**。

### 4.2 响应归一化

| DeepSeek 响应 | `ChatResponse` | 规则 |
| --- | --- | --- |
| `choices[0].message.content` | `text` | 原样（**不做 JSON 解析**，解析口径归 `claim_validation`） |
| — | `structured` | 若 `text` 恰能 `json.loads` 成 dict 则填入，否则 `None`。**解析失败不报错**（不越权判断"模型输出质量"） |
| `choices[0].finish_reason` | `finish_reason` | 原样。既有 `claim_validation` 已把 `content_filter` 判为 `REFUSAL`（DeepSeek 无 `refusal` 值，走这条） |
| `model` | `model_id` | 原样 |
| `id` | `raw_ref` | 对账/报障标识 |
| `usage.prompt_tokens` | `usage.tokens_in` | 直取 |
| `usage.completion_tokens` | `usage.tokens_out` | 直取 |
| `usage.*` | `usage.cost_confirmed` | **不填（保持 `None`）** —— 见 §2.3 |
| `usage.prompt_cache_hit_tokens` / `miss_tokens` | （无承载字段） | **已知缺口**，见 D5 |
| `choices` 为空 / 非列表 | `Err(EXTERNAL_RESPONSE_INVALID)` | 拿不到 content 就不假装成功 |

---

## 5. 错误映射表（与 Tavily 同口径）

| 情形 | 错误码 |
| --- | --- |
| HTTP 401 / 403 | `EXTERNAL_AUTH` |
| HTTP 429 | `EXTERNAL_RATE_LIMIT` |
| HTTP 5xx | `EXTERNAL_UNAVAILABLE` |
| 其它 4xx | `EXTERNAL_UNAVAILABLE` |
| URLError / TimeoutError / OSError | `EXTERNAL_TIMEOUT` |
| 响应体非 JSON / 顶层非对象 | `EXTERNAL_RESPONSE_INVALID` |
| prompt 缺 "json" | `EXTERNAL_UNAVAILABLE`（消息写明原因） |
| `choices` 空 | `EXTERNAL_RESPONSE_INVALID` |

消息一律**只描述状态码与原因**，绝不回显密钥、请求体或完整响应。

---

## 6. 安全与纪律

| 项 | 落地方式 |
| --- | --- |
| 密钥不进请求体 | 只走 `Authorization: Bearer`（与 Tavily 同，避免出现在服务端日志/代理记录里） |
| 密钥不进异常 | 异常消息只写"HTTP <code>"与中文原因 |
| 密钥不进 `repr` | 类不实现 `__repr__` 展开密钥；测试断言 `repr(provider)` 不含密钥值 |
| 不打印、不落盘 | Provider 内**无任何 print / logging / 文件写入** |
| 不读环境变量 | 密钥由 `provider_api_key` 注入；类内零 `os.environ` |
| 默认离线安全 | `offline_only=true` 时配置校验根本不会让 `provider=deepseek` 通过（P4.4-B 已实现） |
| 单一网络出口 | 所有网络调用收敛到 `self._urlopen` 一处，测试整类替换 |
| 无厂商 SDK | 只用 `urllib` / `json` / `time`；`test_dependency_rules.py` 已有全包扫描 |
| 不自动提交 | 本轮不 `git add` / `git commit` |

---

## 7. 测试计划（**全部离线**，零真实请求）

单一测试文件 `tests/test_p4_4c_deepseek_provider.py`，脚手架沿用既有 `StubUrlopen` 范式（`tests/test_p4_2_tavily_compliance.py` 第 117–162 行），把 `provider._urlopen` 整个替换掉。

| 组 | 项数 | 覆盖 |
| --- | --- | --- |
| 请求构造 | 6 | URL = base + `/chat/completions`；`Authorization: Bearer` 存在且**不在 body**；`model` 取值；`max_tokens` 映射；`stream=false`；`metadata` 不外发 |
| `response_format` 映射 | 5 | `json_schema` → `{"type":"json_object"}`；`text` → 字段不出现；prompt 含 "JSON"（大写）放行；含 "json"（小写）放行；**全无 → `Err`** |
| 响应归一化 | 6 | `text`；`finish_reason`（含 `content_filter`）；`model_id`；`raw_ref`；`structured` 解析成功；`structured` 解析失败为 `None` 且**不报错** |
| 用量映射 | 5 | `tokens_in`/`tokens_out` 直取；缺 `usage` → 两者 `None`；`requests=1`；**`cost_confirmed` 恒为 `None`**；cache 字段存在时 `tokens_in` 仍取 `prompt_tokens` |
| 错误映射 | 8 | 401 / 403 / 429 / 500 / 503 / 超时 / 非法 JSON / `choices` 空 |
| 密钥安全 | 5 | 不进 body；不进异常消息；不进 `repr`；`health()` 不联网；`health()` 无密钥时 `degraded=True` |
| 账本边界 | 4 | **Provider 调用后 ledger 仍为空**（钉死"不双记"）；抽取器端到端（桩 provider + 真抽取器）算出 `CONFIRMED` + `SOURCE_OFFICIAL_RATE`；幂等键含 `provider_name="deepseek"`；失败路径记 `UNKNOWN` 不记 0 |
| 能力与协议 | 4 | `capabilities`（chat=True / search=False / cost_model=per-token）；`search()` → `Err`；`usage()`；`Provider` 协议 `isinstance` 通过 |
| 装配接线 | 4 | `chat_provider_from_settings()` 用配置的 `model_id`；**`max_retries == 0`**；无密钥时返回 `Err`；`model_plan()` 的 notes 含重试与思考模式两条 |
| 合计 | **47** | |

复用的既有回归：`test_claim_extraction`（G-2）、`test_p4_4a_model_extraction`、`test_business_orchestrator`、`test_h10_e2e`（H-10）、`test_p4_2_tavily_compliance`（G-4）、`test_dependency_rules`、`test_config`、`test_p4_4b_model_setup`。

---

## 8. 需要你拍板的决策点

| # | 决策 | 我的建议 | 理由要点 |
| --- | --- | --- | --- |
| **D1** | 是否显式 `thinking={"type":"disabled"}` | **建议 disabled** | 抽取无需推理链；开启会生成 `reasoning_content` 并**计入 `completion_tokens`**（直接推高费用），且与 `max_tokens=4096` 抢额度，可能撑出空内容 |
| **D2** | `temperature` 取值 | **建议保持抽取器现状 0.7**（显式发送） | DeepSeek 默认是 **1**；我们不接受这个默认。降得更低（如 0.2）可能更稳，但要改 P4.4-A 的 `build_request`（属改动已验收模块），建议本轮不动、冒烟后按实测再议 |
| **D3** | 记账点 | **建议 Provider 不记账**（§2.3） | 双记会让 0.25 USD 上限实际只剩一半可用；备选方案有 3 项净损失 |
| **D4** | prompt 缺 "json" 时 | **建议拒绝（fail loud）** | 自动补 prompt 是隐藏行为，且会让 dry-run 与实际请求不是同一份 |
| **D5** | cache hit / miss 的计价 | **建议本轮按 miss 保守口径，cache 明细记为已知缺口** | 配置只有两个单价（P4.4-B 拍板取 cache **miss** 的 peak 价），统一按 miss 算 = **高估** = 保守安全；而 `ChatResponse` 没有承载 cache 明细的字段，加字段属改已验收结构，需另行授权 |

### 8.1 UI 上的一个连带后果（**需你知情**）

既有 `persona model plan` 会打印一条 notes，内容是"须由 DeepSeek 适配层映射为 `json_object`"。本方案正是那个适配层，**落地后这条 notes 应当改写**成陈述句（"已由 `DeepSeekProvider` 映射为 `json_object`"）。我会在实施时一并改掉 —— 否则文档会说一件已经不成立的事。

---

## 9. 本方案**不做**的事

- ❌ 不复制 P4.3 的 Wikipedia 全文进项目（按你指示）；
- ❌ 不发起任何真实请求（真实冒烟另行授权）；
- ❌ 不改 `ExtractionJSONSchema` / 不改 `Provider` 协议 / 不改 `extraction.py`；
- ❌ 不新增配置项、不改 `.env.example` 的变量名（`PERSONA_PROVIDER_API_KEY` 已存在）；
- ❌ 不实现两层预算、不做 `max_retries` 的真实重试策略、不做 pending 持久化队列（仍为遗留项）；
- ❌ 不 `git add` / `git commit`。

---

## 10. 缺口 2 的落实（测试材料）

按你的决定：**测试材料用合成虚构人物，不硬编码真实人物**（AC-16）。

- 复用 `model_setup.py` 已有的合成样张（`阿雾` / `雾港`，已带 `QUALITY_VALID` 门禁结论）；
- 冒烟所需的**短小可核验**材料：在同一命名体系下另写一段（阿雾/雾港/星灯），满足
  ① 足够短（输入 token 可控）；② 每句都能被"逐字摘录"核验（用于验证 `VERBATIM` 路径）；
③ 含一处**推理可得但未明写**的信息（用于验证 `INFERENCE` 路径被正确区分）；
- **不引入任何真实人物、真实机构、真实事件**；材料在代码里明确标注为合成。

---

## 11. 实施后的验证命令（预告，供你预期）

```bash
# 新增测试（离线，零联网）
python -m unittest tests.test_p4_4c_deepseek_provider

# 相关回归
python -m unittest tests.test_p4_4a_model_extraction tests.test_claim_extraction \
                   tests.test_p4_4b_model_setup tests.test_dependency_rules tests.test_config

# 全量
python -m unittest discover -s tests -t .

# dry-run（不联网、不记账）——用来看"真正会发出去的请求"
python -m persona.cli.main model dryrun --no-dotenv
```

---

**等你确认 D1–D5 后我再写代码。未确认前不动任何文件。**
