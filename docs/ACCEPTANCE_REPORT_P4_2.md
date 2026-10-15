# P4.2 验收报告 —— Tavily 搜索 Provider / 正文提取策略 / 合规钩子

> 项目：`generic-persona-skill`（路径 `D:\AIprojects\generic-persona-skill`）
> 阶段：P4.2（继 P2 / P3 / G-2 / G-4 / H-10 / P4.1 之后）
> 实测环境：Python 3.13.12（受管运行时），全程离线、无密钥、不触网
> 结论口径：**只报告实际执行并验证的结果**

---

## 0. 本轮改动范围（与 P4.1 及之前未提交产物的关系）

P4.1 已确认 `Provider` 协议 / 注册表 / 配置 / 密钥脱敏 / 离线开关 / 预算闸门
**已满足真实 Provider 接入的全部前置要求**。P4.2 在之上**实现**了 Tavily Provider、
正文提取策略与合规钩子，并修复了两个在冒烟测试中发现、会破坏既有契约的缺陷。

| 文件 | 本轮状态 | 说明 |
| --- | --- | --- |
| `persona/providers/tavily.py` | 新增（P4.2） | Tavily 真实 Provider，仅标准库 |
| `persona/modules/compliance.py` | 新增（P4.2） | 合规钩子（5 检查点 + 规则集） |
| `persona/config/spec.py` | 修改（P4.2 本轮） | 新增 `ResearchLimits` 数据类；`AppSettings` 新增 `research` 组；`to_public_dict` 展开；`validate_settings` 新增 tavily 互斥/缺密钥校验；`CONFIG_FIELDS` 新增 4 个 `research.*` 字段 |
| `persona/modules/research.py` | 修改（P4.2 本轮） | `ResearchLimits` 迁至 config 并复用；新增 `TopNExtractionStrategy`；`ResearchDeps` 注入 `extraction_strategy`/`compliance`/`limits`；五个合规检查点接入；修复"域名黑名单未命中"缺陷；`ResearchRegisterOutcome` 增加 `warnings` 并回传合规跳过原因 |
| `persona/providers/base.py` | 修改（P4.1 已建） | `CapabilitySet.supports_extract`；`ExtractRequest`/`ExtractResponse`/`ExtractResult` |
| `persona/providers/registry.py` | 修改（P4.1 已建） | `ProviderRegistry.get(name, config, secrets)`；`_build_tavily` 从 `secrets` 取密钥 |
| `persona/providers/__init__.py` | 修改（P4.1 已建） | 导出 Extract/Tavily 类型 |
| `persona/core/errors.py` | 修改（P4.1 已建） | 新增 `EXTERNAL_RESPONSE_INVALID`、`VALIDATION_COMPLIANCE_DENIED` |
| `persona/config/loader.py` | 修改（P4.1 已建） | `RESEARCH_MAX_*` / `SEARCH_PROVIDER` 别名；`TAVILY_API_KEY` → `PERSONA_SEARCH_API_KEY` 别名 |
| `persona/modules/__init__.py` | 修改（P4.1 已建） | 导出 `compliance` |
| `.env.example` | 修改（P4.2 本轮） | 新增 Tavily / 研究上限占位说明（仅占位，无真实密钥） |
| `tests/test_p4_2_tavily_compliance.py` | 新增（P4.2 本轮） | 37 个离线测试，全绿 |
| `tests/test_provider_integration_readiness.py` | 修改（P4.2 本轮） | 修复工厂签名以匹配改写后的 `registry.get(name, config, secrets)` 两参工厂 |
| `tests/test_dependency_rules.py` | 修改（P4.2 本轮） | `test_no_api_key_literals` 改用正则 + 负向后查，避免误报环境变量名常量 |
| `tests/test_config.py` | 修改（P4.2 本轮） | `test_no_getenv_outside_config_package` 改用 AST 扫描，忽略文档字符串误报 |
| `tests/test_provider_integration_readiness.py` | 新增（P4.1，未提交） | 集成就绪度测试 |
| `tests/test_h10_e2e.py` | 新增（H-10，未提交） | H-10 端到端回归 |

> 本轮**未做自动 git 提交**（遵循用户限制 + 既有约定：新产物留待人工验收）。

---

## 1. 实际 Provider 接口与调用链

**新增 Tavily Provider 遵循既有"唯一外部出口"架构（D-030/D-054），不绕过 Orchestrator / Evidence / 预算。**

- `TavilyProvider`（`persona/providers/tavily.py`）：
  - `capabilities()` 返回 `CapabilitySet(provider_name="tavily", supports_search=True, supports_extract=True, cost_model="per-call")`。
  - `search(ctx, SearchRequest)` → 调官方 `/search`，把结果标准化为 `SearchResult`
    （`canonical_key=url`，`source_kind_guess="WEB_PAGE"`，`snippet=content`，`published_at=published_date`）。
  - `extract(ctx, ExtractRequest)` → 调官方 `/extract`，把结果标准化为 `ExtractResult`
    （`excerpt=raw[:2000]`，超长 `truncated=True`；`failed_results` 单独映射为带 `error` 的项）。
  - `chat()` → 明确返回 `EXTERNAL_UNAVAILABLE`（Tavily 不是聊天模型，**不伪装可用**）。
  - `health()` / `usage()` 不发起网络请求。
  - **网络出口唯一收敛点**：`_post_json(ctx, path, payload)`。所有超时 / 限流 / 鉴权 /
    不可用 / 响应格式错误在此统一分类与重试。
- **协议层面零破坏**：`Provider` 协议本身**未改签名**；`supports_extract` 是 `CapabilitySet`
  的可选布尔（默认 `False`），`extract` 是**非协议强制**的扩展方法（通过 `getattr` 探测），
  Mock Provider 不支持也不报错。
- **密钥注入路径**：`ProviderRegistry.get(name, config, secrets)` 把 `Secrets` 传给工厂；
  `_build_tavily(config, secrets)` 读 `secrets.search_api_key or secrets.provider_api_key`。
  TavilyProvider **从不** `os.getenv`、从不持有全局默认密钥（D-077）。
- **调用链（实测）**：
  `ResearchService.collect` → `provider.search`
  → `ResearchService._maybe_extract` → `TopNExtractionStrategy.extract` → `provider.extract`
  Provider 之外无第二出口；错误（`EXTERNAL_*` / `BUDGET_EXCEEDED`）一律原样向上传播。

---

## 2. 配置项与密钥保护

**新增研究上限配置（复用既有"配置即字段登记表"机制，不重复造轮子）：**

| 环境变量 | 别名 | 路径（AppSettings） | 默认 | 范围 |
| --- | --- | --- | --- | --- |
| `PERSONA_RESEARCH_MAX_QUERIES` | `RESEARCH_MAX_QUERIES` | `research.max_queries` | 5 | 1–100 |
| `PERSONA_RESEARCH_MAX_RESULTS` | `RESEARCH_MAX_RESULTS` | `research.max_results_per_query` | 5 | 1–50 |
| `PERSONA_RESEARCH_MAX_EXTRACTS` | `RESEARCH_MAX_EXTRACTS` | `research.max_extracts` | 3 | 0–20（0=不抓正文） |
| `PERSONA_RESEARCH_MAX_COST` | `RESEARCH_MAX_COST` | `research.max_cost` | 空（不限制） | ≥0，nullable |

- 权威类型 `ResearchLimits` 定义在 `persona/config/spec.py`（与 `CONFIG_FIELDS` 的 `research.*`
  一一对应），`persona/modules/research.py` 仅 `import` 复用，避免重复定义。
- **密钥保护（D-077 / D-078 / D-081 / D-079）：**
  - `TAVILY_API_KEY` 经 `ENV_SECRET_ALIASES` 映射到 `PERSONA_SEARCH_API_KEY`，**只从环境变量**读取；
    `.env` 里写密钥会被忽略并告警。
  - `api_key` **只在 HTTPS 请求体里随请求发出**，绝不进入日志、异常、`repr` 或错误信息。
    测试显式断言：注入密钥 `super-secret-key-123` 后，所有错误/异常字符串与 `details` 中**均无该密钥**。
  - `Secrets` 的 `repr` / `str` / `to_masked_dict()` 一律脱敏（只报"已设置/未设置"）。
  - `validate_settings` 两条硬校验：
    1. `provider_name in ("tavily",) and offline_only` → `VALIDATION_CONFIG_INVALID`（真实 Provider 不得在离线模式运行）；
    2. 允许联网但**无任何密钥** → `VALIDATION_REQUIRED_FIELD`。
  - `.env` 已被 `.gitignore`（`.env` 与 `.env.*`）忽略。
- **实测**：`load_settings` 默认 `research = ResearchLimits(5,5,3,None)`；经别名/路径覆盖可改
  （如 `research.max_queries=7`）；`to_public_dict()` 含 `research` 且无任何密钥值。

---

## 3. 正文提取策略与内容留存策略

**默认链路：搜索 → 按结构完整度筛选 → 选择性抓取正文。**

- **量级上限（`ResearchLimits`，默认 5 / 5 / 3，可经配置/注入覆盖）：**
  - `plan_queries` 的检索计划数封顶 `max_queries`；
  - `collect` 把 `max_results_per_query` 作为 `SearchRequest.limit` 下传给 Provider（实测透传验证）；
  - `TopNExtractionStrategy` 按相关性取前 `max_extracts` 个有 URL 的结果调用 `provider.extract`；
    `max_extracts<=0` 时直接返回空、不发任何抓取请求（实测 `max_extracts=0` 只产生 1 次 search、无 extract）。
- **优先用 Provider 正式 Extract**（如 Tavily Extract），**不实现无限制爬虫**；Provider 不支持
  `extract`（如 Mock）时返回 `[]` + `NO_EXTRACT_SUPPORT` 提示，不伪造抓取。
- **内容区分（如实建模，不混淆）：**
  - 搜索摘要 `SearchResult.snippet` → `SourceMaterial.snippet`（材料，**不是正文全文**）；
  - 抓取正文 `ExtractedContent.content` → **只留在内存**（`ResearchOutput.extracted`），默认**不落库**；
  - `Source` 行只存元数据（url / title / canonical_key / published_at / 来源类型），`content_ref` 默认 `None`
    （实测 `Source` 行 `content_ref is None`）。
- **可追溯字段**保留：url / title / 日期 / 采集时间 / content-type / 可定位引用；`ExtractedContent`
  带 `extracted_at`、`access_hint`、`flagged_untrusted`。
- **不越界**：不绕过登录 / 付费墙 / 验证码 / 访问控制；不对日期、内容、来源、成功状态做任何伪造。
- **默认不永久保存全部网页全文**：仅当显式把 `content_ref` 写入 `SourceDraft` 才持久化；本阶段
  `register` 从不设置 `content_ref`。

---

## 4. ComplianceHook 检查点与拒绝行为

**新增 `persona/modules/compliance.py`：可注入、可测试的统一合规接口（工程检查，非法律认证）。**

- **五个检查点（实测全部接入 `ResearchService`）：**
  `before_search` / `before_extract` / `before_register` / `before_persistence` / `before_output`。
- **结论类型**：`ALLOW` / `DENY` / `NEEDS_HUMAN`，每条带 `rule` + `reason` + `checked_at`，记入 `ResearchService._compliance_log`（可审计）。
- **规则集 `ComplianceRules`**（非敏感，可经配置注入）：
  - 域名限制（`denied_domains` / `allowed_domains`）；
  - 请求预算（`max_requests`）；
  - 内容长度限制（`max_content_length`，超限且 `metadata_only` → `NEEDS_HUMAN`）；
  - 来源可追溯性（`require_traceable_source` → 不可追溯在 register/persistence 阶段 `DENY`）；
  - 留存策略（`retention_policy`）；
  - 不确定访问（paywall / copyright / unknown）→ `NEEDS_HUMAN`，**绝不自动假定可永久保存全文**。
- **拒绝行为（实测）：**
  - 命中 `denied_domains` → `DENY`：候选**跳过、不登记**，并在 `ResearchOutput.warnings` 记录
    `COMPLIANCE_SKIPPED` 原因（不静默丢弃、不伪造成功）。被拒来源**不入 `Source` 表、不派生 `EvidenceItem`**。
  - `NEEDS_HUMAN` / `ALLOW` **不阻断**流程，但输出前闸门 `_sanitize_extracted_for_output` 会剔除
    被标记不可信 / 访问状态不确定 / 报错项的全文，只保留 metadata。
  - **网页内容视为不可信输入**：`detect_prompt_injection` 标记疑似注入片段（如 "ignore previous instructions"），
    命中后 `flagged_untrusted=True`，输出收口剔除其全文；钩子本身不执行任何网页内容作为指令。
- **边界**：合规钩子**只检查、下结论、记原因**，**不自行启动研究、不绕过 Orchestrator 或 Evidence**
  （F-2 / D-031）；它是工程检查，不是法律合规认证。

---

## 5. 测试命令 / 通过情况 / 改动文件

**新增测试：** `tests/test_p4_2_tavily_compliance.py`（37 个用例，全部通过）

```bash
# P4.2 专项
python -m unittest tests.test_p4_2_tavily_compliance -v
# 全量回归（含 P4.2）
python -m unittest discover -s tests -t .
```

**覆盖（已实测通过）：**
- Tavily 错误映射：401/403→`EXTERNAL_AUTH`、429→`EXTERNAL_RATE_LIMIT`（含 3 次调用验证重试）、
  5xx/其它4xx→`EXTERNAL_UNAVAILABLE`、网络错误→`EXTERNAL_TIMEOUT`、非法 JSON→`EXTERNAL_RESPONSE_INVALID`；
- 密钥只进请求体、绝不泄露到错误/异常（显式断言）；
- 搜索/抓取结果标准化与 `excerpt` 截断（>2000）、`failed_results` 处理；
- 量级上限：`max_queries` 截断计划、`max_results_per_query` 透传、搜索上限封顶抓取；
- 无抓取支持时不伪造、`max_extracts=0` 不发抓取；
- 合规：域名黑名单在 register/extract 阶段 DENY 并跳过、记录原因；paywall→输出收口剔除全文；
  注入内容标记并剔除；来源元数据/摘录/Evidence-ID 可追溯；`Source.content_ref` 默认不落库；
- 离线保证：全链路在 `network_blocked()` 下零 socket 跑通；`validate_settings` 拒绝离线 tavily、
  拒绝无密钥 tavily；默认 `provider_name=fake`、tavily 不被离线护栏包裹；
- Mock 家族仍可独立工作（不依赖 Tavily）；
- 任务契约：预算耗尽即时暂停且不调用 Provider、全失败不伪装、目标为空即报错、重跑幂等（去重）；
- 删除/归档不留违规派生内容：被拒来源根本不入 `Source`/`Evidence`，全文从不落库。

**全量回归（已实测）：**
```bash
python -m unittest discover -s tests -t .
# => Ran 776 tests in ~76s ... OK（全绿，零失败零错误）
```
跑批过程中暴露 **3 个测试侧缺陷**（均为既有/历史测试与 P4.1 注册表签名改写、Tavily 接入后的
误报，不影响产品代码），本阶段一并修复后复跑确认全绿：

| 测试 | 缺陷根因 | 修复 |
| --- | --- | --- |
| `test_provider_integration_readiness::test_provider_config_is_injected_via_registry` | 工厂签名 `def factory(cfg)` 与 P4.1 改写后的 `registry.get(name, config, secrets)`（两参）不匹配 → `TypeError` | 工厂签名改为 `def factory(cfg, secrets=None)` |
| `test_dependency_rules::test_no_api_key_literals` | 子串 `api_key = "` 误命中 `ENV_PROVIDER_API_KEY = "..."`（环境变量名常量，非密钥值） | 改用正则 + 负向后查 `(?<![A-Za-z0-9_])api_key\s*=\s*['"]`，只匹配"真正的字面量密钥赋值"，放过后缀式标识符与 `self.api_key = api_key` |
| `test_config::test_no_getenv_outside_config_package` | 子串扫描误命中 `tavily.py` 文档字符串中的"`os.getenv`"说明文字（运行时从未调用） | 改用 AST 扫描真实属性/名称节点（`getenv`/`environ`），忽略文档字符串与注释；仍严格保证除 `persona/config` 外无运行时 `os.getenv` |

> 上述 3 处均为**测试代码**修正，产品代码（`persona/` 下）未发生回退；修复后全量 776 项一次通过。

---

## 6. 如何确认"未触网"

- **双重保证**：① 所有 Tavily 测试用可脚本化的 `StubUrlopen` 替换 `TavilyProvider._urlopen`，
  **从不调用真实 `urllib.request.urlopen`**；② 涉及"整链路"的测试在 `network_blocked()` 上下文
  内执行（任何真实 socket 连接都会抛 `OfflineViolation`）。
- **配置层硬闸**：`validate_settings` 在"离线 + tavily"或"联网但无密钥"时直接拒绝构造/运行，
  因此真实装配路径在测试与默认配置下**不可能**发起真实请求。
- **虚构域名**：所有测试材料使用 `.invalid`（IANA 保留、guaranteed 不可路由），即便脱离护栏也连不到真实服务器。
- **结论**：本阶段**没有任何真实 API 请求、没有任何费用**。真实冒烟测试留待下一阶段（见第 8 节）。

---

## 7. 未解决风险（如实记录，不隐瞒）

1. **Provider 服务条款 / 速率 / 费用**：Tavily 的额度、计费、限流策略需在下阶段真实冒烟时按其实时
   ToS 复核；本阶段只把 `cost_model="per-call"` 如实声明，未做任何费用预测。
2. **抓取内容的版权 / 访问权限**：合规钩子对"付费墙/版权/未知"访问态一律判 `NEEDS_HUMAN`，
   **不自动假定可永久保存全文**；但"某篇具体网页是否可抓取/可留存"的最终判定属于法律/版权范畴，
   超出本工程模块的能力，需人工确认。
3. **`access_hint` 尚未从真实响应回填**：当前 `before_extract` 合规事件统一以 `access_hint="open"`
   传入，因此真实抓取的 paywall/copyright 信号目前要靠下游/人工补充；`uncertain_access` 规则的
   端到端触发在**单元测试**（钩子层）已验证，集成层触发需后续把真实响应信号接进 `ExtractedContent`。
4. **Tavily API Schema 漂移**：本实现按官方 REST（`/search` + `/extract`）当前文档映射；若 Tavily
   改字段名/新增必填项，需同步更新 `tavily.py` 的映射（已集中在 `_post_json` 与两处映射函数，便于修改）。
5. **本阶段未做真实 API 冒烟**：严格遵循用户限制（第六步）——不发起真实请求、不产生费用；
   真实可用性的"金标准"验证在下一阶段由人工显式开启后执行。

---

## 8. 下一阶段真实 API 冒烟测试步骤（供人工执行）

> 前置：确已获得 Tavily API Key，并理解其 ToS 与费用。

1. **开联网 + 给密钥**（不要提交到仓库 / `.env`）：
   ```bash
   export PERSONA_OFFLINE_ONLY=false
   export PERSONA_PROVIDER=tavily
   export TAVILY_API_KEY="<你的真实密钥>"      # 或通过 PERSONA_SEARCH_API_KEY
   ```
2. **小目标验证**：对一个确定存在的虚构/公开人物跑一次研究，确认 `search` → `extract` → 登记来源
   全链路成功；检查 `Source` 元数据与 `ResearchOutput.extracted` 内容形态。
3. **错误路径抽查**：临时用错密钥确认落到 `EXTERNAL_AUTH`；临时断网确认落到 `EXTERNAL_TIMEOUT`；
   触发限流确认落到 `EXTERNAL_RATE_LIMIT` 与重试语义。
4. **费用与限流看护**：观察 `usage` 回报与账单，确认 `research.max_cost` / `max_requests` 上限按预期生效。
5. **合规复核**：在真实流量下验证 `denied_domains` / `require_traceable_source` / `block_uncertain_access`
   的 DENY / NEEDS_HUMAN 行为，确认不确定内容未自动永久保存全文。
6. **密钥轮换与清理**：冒烟结束后务必撤销/轮换测试用密钥，并恢复到 `PERSONA_OFFLINE_ONLY=true` +
   `PERSONA_PROVIDER=fake`（或删除环境变量），避免误用真实服务产生费用。

---

**验收口径重申**：以上均为本阶段**实际执行并验证**的结果；本阶段未发起任何真实 API 请求、未产生费用、
未做自动 git 提交。完成后停止，等待人工验收。
