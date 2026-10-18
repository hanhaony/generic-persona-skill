# P4.8 R4.8 调用清单（**已执行**）

> **状态**：2026-10-18 22:06 已运行，`SUCCEEDED`，2 次真实外呼（全部模型）、0 credits。
> **结果见 `docs/P4_8_R48_REAL_SEARCH.md`**。5 条硬指标：①②③ 通过、④ 与本节 §5.2 的预测**不符**（实际 4 条）、
> ⑤ 费用/账本通过但 `metadata.temperature` **未落盘**（新缺陷 D-294）。
>
> 下方为授权时的原始清单，保留原样以便对照（含**已被证伪的 §5.2 预测**）。

---

## 0. 先回答核实项：DeepSeek **没有 `seed`**

查 `api-docs.deepseek.com/api/create-chat-completion`（2026-10-18）：

| 结论 | 依据 |
|---|---|
| **无 `seed`** | 请求体参数清单里只有 `messages / model / thinking / reasoning_effort / max_tokens / response_format / stop / stream / stream_options / temperature / top_p / tools / tool_choice / logprobs / top_logprobs / user_id`，**没有任何种子字段** |
| 响应里的 `system_fingerprint` | 只是"后端配置标识"，**不可回传**，不能当种子用 |
| `temperature` | 支持，0~2，默认 1；官方说低值"more focused and deterministic"，但**没有承诺逐字可复现** |

⇒ 你已拍板：**`temperature = 0`，跑一轮**。

---

## 1. 方案修正：0 温度、单轮（不是两轮）

上一版清单写"必须跑两轮"，那是 **0.3 的前提**——0.3 仍有采样随机性，只能靠两轮量重合度。
改成 0 之后这个前提**不成立**：同输入同温度下输出应当一致，重合度不需要用两轮去量；
真要验"0.7 下生产是否稳定"，那是产品化阶段用多次运行统计评估的事，不阻塞技术验证收尾。

| 项目 | 上一版（0.3） | **本版（0）** |
|---|---|---|
| 运行轮数 | 2 | **1** |
| 模型调用 | 4 | **2** |
| 费用 | ≈0.017 USD | **≈0.0085 USD** |
| "两轮重合度 >50%" | 硬指标 | **删除**（0 温度下理论上 100%，不验） |

---

## 2. 代码改动（已完成，零成本）

| 项 | 做法 |
|---|---|
| 温度可配置 | 新常量 `DEFAULT_EXTRACT_TEMPERATURE = 0.7`（**生产默认**）；`ProviderClaimExtractor(temperature=...)` 与 `build_request(temperature=...)` 都可收；`resolve_temperature()` 校验 0–2、非法值**抛错不夹值** |
| 显式传 | `extract()` 把 `self.temperature` 传给 `build_request()`；请求 `metadata.temperature` 落盘，报告可对账 |
| 装配通路 | `StackOptions.extract_temperature` → `claim_extractor_from_settings(temperature=...)` → 抽取器 |
| CLI | `--extract-temperature T`（`None` ⇒ 生产默认 0.7；R4.8 传 `0`） |
| 对话独立 | 新常量 `DIALOGUE_TEMPERATURE = 0.7`，`DialogueService(temperature=None)` 独立可配；**不随 `--extract-temperature` 变** |
| 注释 | 常量区写明"**生产环境默认恢复为 0.7**"，0 只是 R4.8 的实验配置 |
| 测试 | `tests/test_p4_8_extract_temperature.py` **14 项**：默认值仍是 0.7 / 显式 0 生效 / 非法值报错 / 边界 0 与 2 / 对话与抽取互不影响 / 提示词未变 |

---

## 3. 调用清单（**一轮**）

```
python scripts/e2e_p4_8_real_search.py ^
  --vocabulary l2-zh-classical ^
  --extract-temperature 0 ^
  --targets 林黛玉,贾宝玉 --work-title 红楼梦 --adaptation 曹雪芹原著 ^
  --replay-materials "%TEMP%\gps-p48-r46" ^
  --probe-target 林黛玉 --probe-about 贾宝玉 ^
  --out "%TEMP%\gps-p48-r48"
```

| 项目 | 次数 | 计费 |
|---|---|---|
| 检索 `/search` | 0 次真实（回放命中 2） | **0 credits** |
| 抓取 `/extract` | 0 次真实（回放命中 1） | **0 credits** |
| 模型 `chat` | **2**（1 抽取 + 1 对话） | **≈0.0085 USD** |
| 合计 | **2 次真实外呼** | **≈0.0085 USD + 0 credits** |

| 约束 | 值 |
|---|---|
| `max_retries` | 0 |
| 配置改动 | 零（温度走 CLI，不改 env、不改 settings） |
| 隔离库 | `%TEMP%\gps-p48-r48\real\persona.db`（全新） |
| 原始响应 | 落盘 `raw_responses.jsonl` |
| 清单外调用 | 0（出现即缺陷、立即停） |
| 跑前 | dry-run 打印清单（物理断网）后再跑真实 |

---

## 4. 硬指标（你给的 5 条）

| # | 指标 | 目标 |
|---|---|---|
| 1 | **探针** | **`OK`**：探针句**不被拒收**、`subject_entity_ids` 挂**宝玉**、`material_key` = **中青报** |
| 2 | **回放零落库** | `external_usage_records` 里 `provider=tavily` 行数 **= 0** |
| 3 | 探针污染 traits 的实际影响 | 宝玉是否 0→1，**记录为预期行为**（不是能力提升） |
| 4 | 中青报是否 5 条 | 超 `N=4` 如实记录，不截断、不判失败 |
| 5 | 费用 | 实付与账本累计（回放部分应为 **0**） |

辅助观察（不作硬指标）：主体正确率、拒收数、`CLAIM_NOT_ATOMIC`、L2 标签使用、`caustic`、覆盖率、输出是否撞顶。

---

## 5. 两个必须提前说清的副作用（与上一版相同，仍然成立）

### 5.1 探针句进库后，宝玉 `traits` 可能从 0 变 1——**这不是能力提升**

D-291 修好后，探针句（`rebellious`）会**真的进证据层**，来源是中青报（黛玉侧）。
`rebellious` 从「只有百度百科 1 源」变成「百度百科 + 中青报 2 源」⇒ **REPEATED** ⇒
宝玉 `traits` 可能变成 1。

> 归因必须是"**探针句成了第二源**"，不能算模型表现变好。
> 探针轮与 `traits` 统计轮不能混用——探针本身改变了材料。

需要干净数字的话，另跑一轮**不带探针**（+2 次模型、≈+0.0085 USD），看结果后再定。

### 5.2 中青报会变 5 条（超 N=4）——**本条预测已被 R4.8 证伪**

> **实测结果：仍是 4 条。** N=4 是提示词硬上限，探针占了 1 个名额，**挤掉了 `melancholy`**。
> 详见 `docs/P4_8_R48_REAL_SEARCH.md` §1④。以下保留原文以便追溯我的判断错在哪。

R4.7 时探针句被误归到宝玉页，中青报还是 4 条；修好后归回中青报，该源变 5 条。
`N=4` 是**提示词约束**，校验层不按材料卡条数（D-287）——如实记录，不偷偷截断。

---

## 6. 与 R4.7 的变量对照

| 变量 | R4.7 | R4.8 |
|---|---|---|
| 材料 | 10 源 + 1 句探针 | **同（逐字节回放）** |
| 提示词版本 | `g2-extract-2026-10-18c` | 同 |
| 词表 / 指纹 | `l2-zh-classical` / `8484832b7a060357` | 同 |
| **抽取 `temperature`** | **0.7** | **0** ← 唯一实质变量 |
| 对话 `temperature` | 0.7 | **0.7（不变）** |
| 引文核验 | 单段（材料标错即拒） | **两段式（D-291）** |
| 回放记账 | 落库 3 条（假 2.0 credits） | **零新增（D-292）** |

---

## 7. 离线完成度（授权前证据）

| 项目 | 结果 |
|---|---|
| seed 核实（官方文档） | ✅ 不支持 |
| D-291 + 10 项测试 | ✅ |
| D-291 真实数据复算（R4.7 的 27 条） | ✅ 27/27 accepted，仅 1 条改归属 |
| D-292 + 3 项测试（含真账本落库对照） | ✅ |
| 温度可配置 + 14 项测试（默认 0.7 / 显式 0 / 非法报错 / 对话独立） | ✅ |
| 旧断言按新规则改写（`test_quote_from_another_material_…`） | ✅ 1 条 |
| 全量回归 | **1528 passed / 0 failed**（基线 1519，+9） |
| **未提交 Git** | ✅ |

---

## 8. 边界（沿用）

- 不调真实 API、不装依赖、不改无关文件、不自动提交 Git；
- **清单外调用即缺陷立即停**；
- 探针 `FAIL`（宝玉说法挂黛玉）⇒ **立即停**，先定位根因；
- 探针 `WARN` ⇒ 记录"本轮没测到"，不判通过也不判失败，说明需调整注入位置或内容；
- 跑前打印清单确认（dry 先跑一次，物理断网）。
