# P4.1 验收报告：真实研究 Provider 接入准备与安全性检查

> 项目：`D:\AIprojects\generic-persona-skill`
> 日期：2026-10-15
> 范围：为后续接入**真实公开信息采集服务**做准备；**本阶段不产生任何网络请求、不调用任何真实 API、不产生费用**。
> 结论先行：**现有架构已满足真实 Provider 接入的全部前置要求，产品代码零改动**；新增 1 个集成就绪度测试文件 `tests/test_provider_integration_readiness.py`（11 项）把"可安全插拔"跑成可断言证据。全量回归见 §5。

---

## 1. 现有 Research Provider 的实际结构及缺口

### 1.1 分层与出口（审查结论）

| 层 | 文件 | 作用 |
| --- | --- | --- |
| 外部出口协议 | `persona/providers/base.py` | `Provider` 协议（`capabilities`/`chat`/`search`/`usage`/`health`）——**全项目唯一外部出口**（D-030/D-054）；`ProviderConfig`（超时/重试/退避）；`SearchResult`（title/url/canonical_key/snippet/published_at/source_kind_guess） |
| 实现家族 | `persona/providers/mock.py` | `FakeProvider`（确定性）、`RecordingProvider`（记录）、`FailingProvider`（注入错误）、`OfflineGuardProvider`（一联网即抛错）、`network_blocked()`（屏蔽 socket） |
| 注册表 | `persona/providers/registry.py` | `ProviderRegistry.register(name, factory)` + `get(name, config)`；`default_registry()` 内置三个 Mock 并**统一套离线护栏** |
| 研究编排 | `persona/modules/research.py` | `ResearchService`：`plan_queries`（确定性模板）→ `collect`（经 `Provider.search`）→ `filter_and_rank`（去重/排序/拒因）→ `register`（只经 `Evidence.register_source`）；**不写证据表、不持密钥、不做预算记账** |
| 配置层 | `persona/config/{spec,loader}.py` | `AppSettings`：`provider_name`、`offline_only`(默认 true)、`budget`、`retry`；`Secrets`（仅环境变量、全脱敏）；`validate_settings`；`RetryLimits.to_provider_config()` |
| 预算闸门 | `persona/orchestrator/{budget,executor}.py` | `BudgetGate` 四段式（检查→预占→执行→记账）；`TaskRunner.run_step` **先查预算再执行**，超限**先存检查点再暂停** |
| 来源数据模型 | `core/types.py` + `modules/evidence.py` | `SourceDraft`（title/canonical_key/kind/url/published_at/subject_entity_ids/fetch_status）；`SourceMaterial`（+snippet/query_text/source_kind）；`Source` 记录由存储层记 `created_at`（即采集/登记时间） |

### 1.2 需求逐项对照（第二步清单）

| 需求 | 现状 | 是否缺口 |
| --- | --- | --- |
| 接口可替换真实 Provider | `Provider` 协议即唯一出口，`registry.register` 即接入点 | ✅ 无缺口 |
| 配置校验 | `validate_settings`：枚举/范围/退避倒置/密钥格式/`offline_only=false` 却无密钥 → 明确报错 | ✅ 无缺口 |
| 超时/重试边界 | `ProviderConfig` + `RetryLimits.to_provider_config()`，经 registry 注入 Provider | ✅ 无缺口 |
| 预算预检查 | `BudgetGate.check()` 在执行前调用；`BusinessOrchestrator.plan()` 设每阶段 estimate，RESEARCH 阶段受闸门保护 | ✅ 无缺口 |
| 错误分类 | `ErrorCategory`：TIMEOUT/RATE_LIMIT/UNAVAILABLE 可重试，AUTH/QUOTA 不可重试，BUDGET_EXCEEDED 可恢复(暂停) | ✅ 无缺口 |
| Provider 可注入 / Mock 默认 / 导入与测试不联网 | `ResearchDeps.provider` 注入；`provider_name` 默认 `fake`；`offline_only` 默认 true；`default_registry` 统一套 `OfflineGuardProvider` | ✅ 无缺口 |
| 未配置真实 Provider 时明确提示、不静默伪装 | `offline_only=false`+缺密钥 → `validate_settings` 报错；未注册 Provider 名 → `registry.get` 明确报错（不静默回退 Mock）；默认离线时结果带 `fake` 出处、CLI `info` 明示"当前全是 Mock" | ✅ 无缺口 |
| 记录来源标识/URL/采集时间/材料类型/可追溯引用，不伪造 | `Source` 落 url/canonical_key/published_at(FuzzyDate)/kind + `created_at`；`SourceMaterial` 带 snippet/query_text；Research 只登记不造证据 | ✅ 无缺口（采集时间由 `Source.created_at` 承载） |
| 预算耗尽暂停并存进度、不继续消费 | `TaskRunner` 超限先存检查点再 `pause(BUDGET)`，Provider 不被调用 | ✅ 无缺口 |
| 不泄露 API 密钥 | 密钥仅环境变量、`Secrets` repr/快照/错误信息全脱敏；Provider 层不持密钥 | ✅ 无缺口 |

> **结论：无功能性缺口。** 唯一"锦上添花"项（`SourceMaterial` 未单列采集时间戳、`collect` 未按配置覆写单条 `SearchRequest.timeout_ms`）经核对分别由 `Source.created_at` 与"Provider 自持 `ProviderConfig`"覆盖，按"保留接口、不做无意义重构"处理，**不改**。

---

## 2. 本次修改文件与理由

| 文件 | 动作 | 理由 |
| --- | --- | --- |
| `tests/test_provider_integration_readiness.py` | **新增**（11 项） | 把 P4.1 就绪度保证跑成可断言集成证据：插拔 seam、配置注入、未注册名明确报错、离线护栏、预算预检先行(Provider 不被调用)、错误分类、空结果、零 socket。纯新增测试，**零产品代码改动** |
| `docs/ACCEPTANCE_REPORT_P4_1.md` | **新增** | 本报告 |
| （产品代码 `persona/**`） | **零改动** | 依据：§1.2 逐项对照显示接口/配置/预算/错误分类/注入/离线/脱密**均已满足**；按第二步 #1"现有接口已满足要求，保留接口，不做无意义重构"，不改动任何已验收模块 |

---

## 3. Mock Provider ↔ 未来真实 Provider 的切换方式

切换**只发生在装配处**，`ResearchService` / `BusinessOrchestrator` 核心逻辑零改动：

```python
from persona.config import load_settings
from persona.providers.registry import ProviderRegistry
from persona.modules.research import ResearchService, ResearchDeps

settings = load_settings()                       # 已校验（含 offline_only=false 却无密钥 → 报错）
provider_cfg = settings.to_provider_config()     # 超时/重试边界（唯一来源）

registry = ProviderRegistry()
registry.register("my_search", lambda cfg: MySearchProvider(api_key=..., config=cfg))  # 真实 Provider 工厂
provider = unwrap(registry.get(settings.provider_name, provider_cfg))  # 按配置名取实现

research = ResearchService(ResearchDeps(provider=provider, evidence=evidence_service))
# 之后 research / orchestrator 用法与 Mock 完全一致
```

- **测试/默认**：`provider_name="fake"` + `offline_only=true` → `default_registry()` 返回**已套离线护栏**的 Mock。
- **真实接入**：实现 `Provider` 五方法（`search` 里建连接、按 `config.per_call_timeout_ms` 设超时、按 `respect_retry_after` 退避、把厂商错误映射为 `EXTERNAL_TIMEOUT/RATE_LIMIT/AUTH/QUOTA`）→ `registry.register(<名>, 工厂)` → 配置切到该名 + `offline_only=false` + 设密钥。
- **本测试的证明**：`StubRealLikeProvider`（真实形状替身）即按上述方式注入并跑通（见 §5）。

---

## 4. 超时、限流、预算与错误处理机制

| 机制 | 载体 | 行为 |
| --- | --- | --- |
| **超时** | `ProviderConfig.per_call_timeout_ms`（默认 30s，配置 `PERSONA_RETRY_TIMEOUT_MS`，范围 100ms~600s） | 由 Provider 自行强制；超时 → `EXTERNAL_TIMEOUT`（可重试） |
| **重试/限流** | `ProviderConfig`（max_retries/backoff_base/backoff_max/jitter/respect_retry_after） | 退避重试；限流 → `EXTERNAL_RATE_LIMIT`（可重试、可恢复，稍后自动继续） |
| **预算预检** | `BudgetGate`（检查→预占→执行→记账） | `check()` 在任何消耗前；80% 告警不改变行为；超限 → `BUDGET_EXCEEDED` |
| **预算耗尽处置** | `TaskRunner.run_step` | 超限 → **先存检查点、再 `pause(BUDGET)`**，返回 `Ok(paused=True)`（非失败）；**Provider 不再被调用**；提额后 `resume` 续跑 |
| **错误分类** | `ErrorCategory`/`ERROR_SPEC` | TIMEOUT/RATE_LIMIT/UNAVAILABLE=(可重试,可恢复)；AUTH/QUOTA=(不可重试,不可恢复)；BUDGET_EXCEEDED=(不可重试,可恢复) |
| **Research 内部分类** | `ResearchService.run` | 单条查询失败 → warning `QUERY_FAILED` + 继续；**全部**失败 → `Err`（不伪装成功）；`BUDGET_EXCEEDED` → 立即传播；登记失败 → 传播 |

> **诚实边界（观察，非本轮改动）**：若某个真实 Provider **自身**返回 `BUDGET_EXCEEDED`，`ResearchService.run` 会把它当"暂停信号"向上传播，但 `TaskRunner` 对该码 `retryable=False` 会将任务置 `FAILED` 而非 `PAUSED`。真实搜索 Provider 一般不返回该内部码（预算由 `BudgetGate` 前置把关，已正确暂停），故此路径实际不可达；修改它涉及已验收的 TaskRunner/Research 错误语义，**超出 P4.1 范围，不改**，仅如实记录。

---

## 5. 测试命令、通过/失败数量、未执行的测试

| 命令 | 结果 |
| --- | --- |
| `python -m unittest tests.test_provider_integration_readiness -v` | **Ran 11 tests — OK**（11 通过，0 失败） |
| `python -m unittest discover -s tests -t .` | **Ran 739 tests in 490.7s — OK**（739 通过，0 失败，0 错误） |

> 计数：728（H-10 复验后既有）+ 11（本轮 `tests/test_provider_integration_readiness.py`）= **739**。运行末段 `ResourceWarning: unclosed database` 为既有用例未显式关闭 SQLite 连接的**非致命告警**（历史存在，与本轮无关），不影响 OK。托管 Python：`C:\Users\36135\.workbuddy\binaries\python\versions\3.13.12\python.exe`（stdlib only，无第三方依赖）。

**新增 11 项覆盖（对应用户第三步清单）：**

| 子需求 | 测试 |
| --- | --- |
| 正常结果 + 来源追溯（url/日期/类型不伪造） | `test_real_like_provider_runs_full_research_and_preserves_source_metadata` |
| 配置注入（超时/重试边界） | `test_provider_config_is_injected_via_registry` |
| 未注册真实 Provider → 明确报错 | `test_unknown_real_provider_name_is_clear_error_not_silent_fallback` |
| 超时（可重试）单条失败继续 + 告警 | `test_retryable_timeout_on_one_query_continues_and_warns` |
| 全部超时上报错误码 | `test_all_queries_timeout_reports_error_with_code` |
| 限流可重试 | `test_rate_limit_is_retryable` |
| 认证失败不可重试 | `test_auth_failure_is_classified_not_retryable` |
| 空结果非错误、不造来源 | `test_empty_results_is_business_outcome_not_error` |
| 离线护栏拦截真实联网 | `test_offline_guard_blocks_real_network_attempt_at_seam` |
| 预算耗尽 → Provider 不被调用 + 暂停 + 存检查点 | `test_budget_exhaustion_pauses_and_never_calls_provider` |
| 整链零 socket 跑通（无真实网络） | `test_full_research_runs_under_network_block_with_zero_sockets` |

> **未由本轮新增、但已由既有测试覆盖**（不重复造轮子）：暂停/恢复生命周期与预算闸门（`tests/test_task_faults.py`）、FakeProvider/RecordingProvider/FailingProvider 家族与 registry（`tests/test_provider_mock.py`）、`offline_only=false`+缺密钥报错与密钥脱敏（`tests/test_config.py`）、Research 单测全链（`tests/test_research_module.py`）。真实 Provider 的**重试退避时序**与**墙钟超时**需接入真实 SDK 后才能端到端验证，本阶段以配置注入 + 错误分类断言覆盖其契约面。

---

## 6. 是否确认没有真实网络请求

**确认：测试期间没有任何真实网络请求。** 三重硬保证：
1. 所有 Provider 调用经 `OfflineGuardProvider`（`socket` 被替换，一连网即抛 `OfflineViolation`）；
2. 离线类测试额外包在 `network_blocked()` 上下文里；
3. 替身材料 URL 一律用保留的 `.invalid` 顶级域（保证即便脱离护栏也**不可能**路由到真实服务器）。

新增测试**不使用任何真实密钥**；未调用任何真实搜索/模型 API；未产生费用。

---

## 7. 下一步接入真实研究服务所需的最少决策

| # | 待决策 | 说明 |
| --- | --- | --- |
| 1 | **选定搜索服务商** | 现有配置/文档**未指定**具体 Provider；本阶段不猜测、不注册、不建号。需人工确定后按 §3 实现 `Provider` 五方法并 `registry.register` |
| 2 | **密钥环境变量命名** | 现成 `PERSONA_SEARCH_API_KEY`（搜索）已预留；若选定的服务商另有模型密钥，复用 `PERSONA_PROVIDER_API_KEY`。是否需要新增专用变量由 #1 决定 |
| 3 | **正文抓取策略（H-17）** | 当前只存检索 `snippet`，不抓正文。接入时需决定是否/如何抓取正文与快照（合规、robots/ToS） |
| 4 | **合规钩子（Q-02）** | `filter_and_rank` 已留白/黑名单与 robots 位置，接入前需拍板来源允许范围 |
| 5 | **墙钟超时接入** | `BudgetGate` 目前只算次数/token/金额，`max_duration_sec` 未接入执行器（既有诚实边界） |

---

## 8. Git 状态（供人工验收核对）

| 项 | 值 |
| --- | --- |
| 产品代码改动 | **无**（`git diff --name-only` 为空） |
| 本轮新增（未提交） | `tests/test_provider_integration_readiness.py`、`docs/ACCEPTANCE_REPORT_P4_1.md` |
| 既有未提交（H-10 复验遗留，等人工决定） | `tests/test_h10_e2e.py`、`docs/ACCEPTANCE_REPORT_H10.md` |
| 最近提交 | `ba1fc48`（P2.4~G-4 统一提交） |

> 依"不自动提交 Git / 完成后停止，等待人工验收"，本轮**未创建任何提交**。
