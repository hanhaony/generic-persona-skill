# generic-persona-skill

> **一句话说明**：你输入两个或更多人物的名字，系统自己去查公开资料，整理出「人物档案」和「关系档案」，然后让这些档案驱动一场多人、多轮、可记忆的模拟对话。

> **✨ v1.0.0 两大核心卖点**
>
> 1. **多人物群聊（神仙打架）**：一次拉起 3 个或更多角色同台——`--responses N` 让 N 个角色依次回应你，
>    `--group N` 让他们**自主互聊 N 轮**：黛玉宝玉拌嘴、宝钗劝架，角色互相听得见、互相接话拆台。
>    互聊内容绝不进长期记忆（红线），只活在本会话里。
> 2. **渐进式关系养成**：关系随互动升温——初识中性称呼、熟识改口、老友亲昵有分寸；
>    说「**叫我XX**」+ `--memory-write rule` + 下次带 `--topic`，**跨会话她还记得怎么称呼你**。
>    角色已打破第四面墙：遇到认知外的现代事物会用**角色口吻推测并反问**，绝不说「资料里没有」。
>
> 五分钟上手见 `SKILL.md`；三人同台实战示例见下方 §0.5。

---

## 0. 先跑通（P5.1，零依赖）

```bash
python -m persona selftest          # 离线自检：不联网、不花钱
```

真正跑一个人（**会联网、会花钱，第一次会问你一次**）：

```bash
export PERSONA_PROVIDER_API_KEY=sk-...   # 模型（密钥只放环境变量）
export PERSONA_SEARCH_API_KEY=tvly-...   # 检索

python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --dry   # 只打清单，不花钱
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦         # 真跑：先看清单 → 确认 → 开跑
```

跑完告诉你下一句敲什么（四条只读命令都**不会**建库、不会写任何东西）：

```bash
python -m persona show 林黛玉                                # 六分区档案
python -m persona show-relationship 林黛玉,贾宝玉            # A 看 B 与 B 看 A 分列
python -m persona evidence 林黛玉                            # 每条特征 → 证据 → 来源 URL
python -m persona export 林黛玉 --format md --out 林黛玉.md  # 可贴进文档的 Markdown
```

**三件不退让的事**：① 每条特征都能追到 URL，追不到的不会出现在档案里；
② 只有 ≥2 个独立来源提到的才算"稳定性格"，其余标"非稳定特征"；
③ 证据不足就输出空档案 + 原因，**不编一条凑数**。

命令清单与实测示例见 `docs/RUNNING.md` §2；受控真跑清单见 `docs/P5_1_REAL_RUN.md`。

---

## 0.5 跑一场对话（P6.2 / P6.3，会花钱）

**前提**：同一批人名**先跑过 `persona run`**。对话只读已有档案，不会自己造设定；
库里没有档案就直接拒绝，不假装认识这个人。

```bash
# 交互模式：逐行输入，/exit 或 Ctrl-D 结束，Ctrl-C 干净中止（已写的记忆不回滚）
python -m persona chat --names "林黛玉,贾宝玉" --rounds 3

# 单发一句（不进交互）
python -m persona chat --names "林黛玉,贾宝玉" --message "你最近在读什么书？"
```

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--names` | **必填** | 逗号分隔，**至少 2 人**（对话要在参与者之间轮换发言） |
| `--rounds` | 1 | **轮数就是调用数**——每轮恰好 1 次模型调用，不玩"预估 × 系数" |
| `--message` | 空 | 给了就单发这一句（不进交互模式） |
| `--memory-write` | **`off`** | `rule` = 按确定性规则把**你原话**里值得记的片段写成会话级模拟记忆（0 额外调用） |
| `--topic` | 空 | 跨会话检索的话题。**不给 = 不跨会话取记忆** |
| `--responses` | 1 | 你每说一句，**N 个角色依次回应**（后答者看得见先答者的话）；每个回应 = 1 次调用 |
| `--group` | 0 | 你说完之后，角色**自主互聊 N 轮**（prompt 注入「在场众人」）；每轮 1 次调用；**互聊绝不进长期记忆** |
| `--budget` / `--db` / `--project` / `--yes` | — | 与 `persona run` **同口径**，授权确认是**同一套**（同一个 `.persona_consent.json`） |

**总调用数 = `轮数 × responses + group`**，开跑前计划里会打印「模型调用总数：最多 N 次」。

**三人同台实战**（林黛玉、贾宝玉、薛宝钗）：

```bash
# ① 建档案（三人一批；同一批人名只需跑一次）
python -m persona run --names "林黛玉,贾宝玉,薛宝钗" --work-title 红楼梦

# ② 你一句话，两个角色依次回应（最多 2 次调用）
python -m persona chat --names "林黛玉,贾宝玉,薛宝钗" --responses 2 \
    --message "宝姑娘也来了？今日园子里可有什么新鲜事？"

# ③ 你说完，三人自主互聊 3 轮——神仙打架（最多 3 次调用）
python -m persona chat --names "林黛玉,贾宝玉,薛宝钗" --group 3 --message "你们仨接着聊，我在一旁听着。"
```

**记忆写入（`--memory-write rule`，默认 `off` 即一条不写）的四条边界**：

1. 只记**你的原话**，**不记模型说过的话**——否则就是让角色把 AI 自己编的话当成"你说的"，
   滚雪球成回声室（规则引擎的签名里**没有**模型回答这个参数，结构上堵死）；
2. 只写**会话级**模拟记忆（`namespace=SIMULATED` + `scope=SESSION`），
   **永远不进真实证据库**，也不会升级成"稳定性格"；
3. 寒暄、问句、纯情绪**不记**；同一句说两遍只留一条（去重在写入层）；
4. 写入内容与你的原话**逐字一致**（`content` 恒为用户原话的子串），规则不改写、不总结。

**跨会话检索（`--topic`）**：不给 topic 就**只取本会话**的记忆；给了 topic，才会去库里找
**同一批参与者**且内容命中该话题的会话级模拟记忆并注入上下文。topic 至少 2 个字、
不能全是停用词——`--topic "我"` 会命中库里**每一条**记忆，所以它在**花钱之前**就被拦下
（报错退出、0 次调用）。topic 拼错 = **取不到**（0 条），不是取错：空结果不注入任何东西，
因此不会污染上下文。

> 对话是**模拟**：产出的一切都带 `SIMULATED` 标记，永远不会反过来改写档案或证据
> （这是本项目的技术红线之一，有不变式断言守着）。

命令细节见 `docs/RUNNING.md` §5.41（`chat`）与 §5.43（`--topic`）；实测对账见 §5.42 / §5.43。

---

## 1. 项目当前状态

| 项目 | 内容 |
| --- | --- |
| 当前阶段 | **v1.0.0 灰度发布（P8.0 一轮多响应 + P8.1 自主群聊已落地，代码已冻结打 tag）**。基础 = 二期（对话 + 记忆写入 + 跨会话检索，已完成）：`persona chat`（P6.2-c）+ 确定性规则记忆写入（P6.2-a/b）+ `--topic` 跨会话检索（P6.3）+ 五维对话质量评估（P6.0/P6.1）；**二期真跑合计 12 次调用 / 0.0052962 USD，逐条对账全对**；P8 真跑验收 3 次调用 / 0.0018666 USD（黛玉宝玉真·互相拌嘴、互聊零记忆）；全量 **1985 项测试全绿**。P5.1（六命令 `run`/`status`/`show`/`show-relationship`/`evidence`/`export` + 只读层 + 授权流程）见 `docs/P5_1_REAL_RUN.md`；二期总结与已知局限见 `docs/RUNNING.md` §5.44 |
| 已完成 | P0 规格与架构、P1.1 核心数据模型、P1.2-A 接口契约与错误处理、P1.2-B 代码骨架、P2.1 工程配置、P2.2 配置管理、P2.3 任务生命周期基础闭环（已验收）、P2.4 故障注入测试（已验收）、P2 证据管线第一步（Evidence 本地管理：登记/查询/持久化/校验/追溯/去重/冲突/稳定性；待验收）、P3 第一步（Persona 本地管理：创建/查询/版本更新/证据追溯/不确定性/冲突关联/虚构边界/模拟隔离；待验收）、P3 第二步（Relationship 本地管理：双向视角/共同经历/时间变化/群体互动/证据直达/版本边界/模拟隔离；待验收）、P3 第四步（Dialogue 本地生成基础：会话/轮次管理/动态发言选择/只读上下文组装/经 Mock Provider 生成/严格模拟隔离；待验收）、P3 第五步（Memory 长期记忆基础：写入/关键词检索/去重/冲突记录/版本化更新/过期/归档/删除与派生产物失效/对象边界/模拟隔离；待验收）、P3 第六步（对话 ↔ 记忆集成：模拟记忆检索注入上下文/显式授权的会话级记忆写入/同事务回滚/幂等；待验收）、P3 第七步（Evaluation 核心 + 端到端集成验收：INV-1~4 不变式断言/依赖分层检查/结构指标/真实调用链集成测试；待验收）、H-11A（Research 最小可运行实现：确定性检索计划/经 Provider 检索/过滤排序/经 Evidence 登记/失败纪律/严格隔离；待验收）、H-10A（业务编排型 Orchestrator：一条命令跑完 Research→Evidence→Persona→Relationship→Dialogue，复用既有任务六态机/预算/检查点，区分成功/部分成功/失败；待验收）、G-2（来源材料 → 候选 claim 自动抽取：可注入 `ClaimExtractorPort`，业务入口不再要求调用方提供 claim；三种 claim 机械区分，推断无根即丢弃；待验收）、**P5.1（CLI 收口：六命令 `run`/`status`/`show`/`show-relationship`/`evidence`/`export` + 只读层 `orchestrator/readmodels.py` + 授权流程「先看清单 → 再确认 → 才开跑」；1725 项测试全绿，两次受控真跑通过，**已验收**，commit `fe18087` / `d9ebc88`）、**P6.2-a/b/c（按确定性规则写会话级模拟记忆：规则引擎 + `rule` 策略 + `run_round` 接线 + 最小 `persona chat`；默认仍是一条不写；1912 项测试全绿，真实外呼 0，详见 `docs/RUNNING.md` §5.39~§5.41）、
**P6.2-d（真跑 6 次逐条对账：该记的记了、不该记的没记，详见 §5.42）**、
**P6.3（`--topic` 跨会话检索入口 + topic 质量门禁；真跑 A′→B′→C′ 6 次调用两层验收全过，
详见 §5.43）**、**P6.4（二期收尾：README / SKILL.md / RUNNING.md / ROADMAP.md 四份文档对齐，
二期总结见 §5.44；1935 项测试全绿）** |
| 代码状态 | **CLI 六命令可用，真实检索与模型抽取已通过两次受控真跑**：公共契约 / SQLite 最小读写 / Mock Provider / 统一配置 / 六态状态机 + `TaskService` + 预算闸门 + Mock 执行器 / 端口协议 / CLI / **`EvidenceService`（来源·事件·证据·冲突·去重·稳定性）** / **`PersonaService`（档案·版本·证据关联·闸门）** / **`RelationshipService`（双向视角·共同经历·时间变化·群体互动）** / **`DialogueService`（会话·轮次·动态发言·模拟隔离）** / **`MemoryService`（写入·关键词检索·去重·冲突·版本化更新·过期·归档·删除·派生产物失效）** / **Dialogue↔Memory 集成（检索注入 + 显式授权写入 + 同事务回滚）** / **`EvaluationService`（INV-1~4 不变式·依赖分层检查·结构指标，只读不写业务表）** / **`ResearchService`（检索计划·经 Provider 检索·过滤排序·经 Evidence 登记来源，不写证据表）** / **`BusinessOrchestrator`（五阶段业务编排：实现 `UnitHandler` 接既有 `TaskRunner`，阶段间数据只经检查点传递，结果三态自述）** |
| 尚未实现 | 正文抓取与切分（`content_ref`）、检索结果语义排序、AI 人格/关系蒸馏（`distill`）、**记忆管理命令**（查看/清理/编辑已写入的记忆）、`end_session → Memory.consolidate`、`Evaluation.run_unit_tests`/`run_integration_tests` 接线、FTS5 检索升级、向量检索。**topic 自动提取**（本期只做显式 `--topic`）、流式输出、多用户身份。`task run` 推进的是**抽象工作单元**，**不是**真实业务；`persona new/sources/memory/eval` 仍是诚实的占位（`persona chat` 已于 **P6.2-c** 接通真实对话、`--topic` 于 **P6.3** 接通跨会话检索，均已真跑验证；详见 `docs/RUNNING.md` §5.41 / §5.43 / §5.44） |
| 外部调用 | **默认不调用**：没有密钥 / 没有显式确认时，Provider 全是 Mock，离线可跑。**只有 `persona run` 与 `persona chat` 会花钱**，都要求"配了密钥 + 过一次确认"（`chat` 只需模型密钥，不发检索）；`run` 的 `--dry` 永远零外呼 |
| 依赖安装 | **无第三方依赖**。只用 Python 标准库（`unittest` / `sqlite3` / `dataclasses`） |
| 技术选型 | Python 3.12+ / 本地 CLI / 模块化单体 / SQLite / 第一版关键词检索 |
| 支持对象 | 公开资料较丰富的现实公众人物 + 游戏动漫影视中的虚构角色 |
| 目标路径 | 仓库根目录（本机位置自定，无强制要求） |

**怎么跑起来**：见 `docs/RUNNING.md`（统一测试命令 + CLI 用法，全部经过实测）。
**P1.2-B 的 R-1~R-6 已由用户拍板**，结论写入 `docs/DECISIONS.md` §2.9（D-062~D-067）；工程约定见 §2.10（D-068~D-072）。
**P2.2 前用户又拍板了 U-2 / U-3**：不引入 ORM/Pydantic（D-073）、FTS5 + trigram（D-074）、改编默认分离（D-075）、档案不跨版本共用（D-076），见 §2.11；配置管理约定见 §2.12（D-077~D-082）。

**配置怎么改**：见 `docs/CONFIG.md`。`python -m persona config show` 看生效值，`config check` 做校验。
**P2.2 的 C-1~C-4 已由用户拍板**：见 `DECISIONS.md` §2.13（D-083~D-086）；P2.3 的任务闭环约定见 §2.14（D-087~D-094）。
**P2.3 已验收通过**，遗留问题 T-1~T-7 全部拍板：见 `DECISIONS.md` §2.15（D-095~D-101）。

**P2 证据管线第一步（来源与证据管理基础）已实现，待人工验收**：`EvidenceService` 支持来源/事件/证据的
登记、查询、持久化、校验与追溯，去重只按严格五元组并组，冲突只记录不裁决，模拟内容被双层闸门拦截。
用户称这一步为 P3.1，与 `ROADMAP.md` 编号的差异见 `DECISIONS.md` §3 Q-37（按仓库编号实施，未改路线）；
实现口径见 §2.16（D-102~D-110），遗留问题见 `RUNNING.md` §5.4（E-1~E-5）。

**P3 第一步（人格档案基础模块）已实现，待人工验收**：`PersonaService` 支持档案的创建、查询、
**版本化更新**（`revise` 生成新版本、旧版归档不覆盖、空转拒绝）、证据关联与追溯
（每个非空条目必带 `evidence_refs`，只收 `EvidenceItem`）、不确定性表达
（`uncertainty_note` / `open_questions` / `INSUFFICIENT_EVIDENCE`）、冲突关联不裁决
（CF-V5 双向回写）、虚构角色版本边界（PF-V5 / PE-V3，不同改编版本不自动合并）、
模拟内容隔离（PF-V6 双层闸门）。`distill` 的 AI 蒸馏主体**未实现**（不调模型）；
用户称这一步为 P3.2，编号差异同 Q-37；实现口径见 `DECISIONS.md` §2.17（D-111~D-120），
遗留问题见 `RUNNING.md` §5.5（F-1~F-5）。

**P3 第二步（关系档案基础模块）已实现，待人工验收**：`RelationshipService` 支持关系档案的
创建、查询、版本化更新与持久化，**A→B 与 B→A 双向视角独立存储、不自动合并**（RL-V1/RL-V5），
共同经历与时间变化（含逐条目参与者），**≥3 人群体档案不拆成两人关系**（`group_dynamics` 必填），
证据直达（RL-V6：不得凭两份人格档案拼关系），冲突关联不裁决，虚构角色版本反合并
（同角色双版本不得同时作为参与者），模拟内容双层闸门隔离，**无亲密度数值**
（RL-V2 双重扫描），单次互动不进 `interaction_patterns`（模式闸门）。
`distill` 的 AI 蒸馏主体**未实现**（不调模型、不启动研究）；
用户称这一步为 P3.3，编号差异同 Q-37；实现口径见 `DECISIONS.md` §2.18（D-121~D-128），
遗留问题见 `RUNNING.md` §5.6（G-1~G-5、E-6）。

**P3 第四步（对话生成基础模块）已实现，待人工验收**：`DialogueService` 支持会话与轮次的
创建、动态发言人选择（`least_recently_spoken`，每轮记录可解释的 `selection_reason`）、
只读引用人格/关系档案组装上下文、经 Mock Provider 生成发言、落盘（namespace 恒 `SIMULATED`）、
严格模拟隔离（只读端口协议 + 存储层 namespace 网关 + prompt 显式声明模拟性质，对话前后真实资料零变化）。
不调真实模型、不写证据/档案；用户消息落盘为 `speaker=__user__` 的轮次。
用户称这一步为 P3.4，编号差异同 Q-37（= 仓库 P4 第一步 Dialogue 部分）；
实现口径见 `DECISIONS.md` §2.20（D-135~D-145），遗留问题见 `RUNNING.md` §5.7（H-1~H-2）。

**P3 第五步（长期记忆基础模块）已实现，待人工验收**：`MemoryService` 支持记忆的写入、持久化与
重启后读取、**关键词检索**（`text` 命中 `content`/`summary`，`scope` / `namespace` / `status` /
时间范围过滤，按相关性与时间排序）、**去重**（内容归一化后命中既有条目则返回原条目并给出
`MEMORY_DEDUP_HIT` 警告，不新增重复）、**冲突记录**（`link_conflict` 双向登记、只记录不裁决）、
**版本化更新**（`supersede` 生成新版本、旧条目置 `SUPERSEDED` 并保留指向，不静默覆盖）、
**过期**（写入即判定 + `expire_sweep` 批量扫描）、**归档**、**删除**（级联失效 `DerivedArtifact`
等派生产物并如实报告边界）。严格区分所属对象（`scope` / `scope_id`）、类型、来源、时间范围、
重要性与置信度；**模拟会话记忆只能服务后续模拟，禁止反向写入真实证据库**，
且未经明确规则不把每轮对话自动写入长期记忆。
用户称这一步为 P3.5，编号差异同 Q-37（= 仓库 P4 第二步 Memory 部分）；
实现口径见 `DECISIONS.md` §2.21，遗留问题见 `RUNNING.md` §5.8（H-3 已批准、H-4~H-6 已接受）。

**P3 第六步（对话 ↔ 长期记忆集成）已实现，待人工验收**：`DialogueService` 已接入 `MemoryService`，
打通「对话 → 上下文记忆检索 → 记忆写入 → 后续会话再检索」闭环。`draft_turn` 按 **R1 本会话**
（`scope=SESSION` 且 `scope_id=本会话`）与 **R2 跨会话**（关联到本会话参与者，且有 `topic` 时须
命中话题关键词）两条规则检索记忆，**两条都硬限 `namespace=SIMULATED` + `ACTIVE`**，
真实证据/现实资料**永不**进入模拟上下文；检索结果原样带出 `SIMULATED` 标记并写入 prompt。
`commit_turn(..., memory_candidates=[...])` 在**同一事务**内按 `Turn → Memory → Audit` 写入
**会话级**模拟记忆并回填 `generated_memory_ids`，任一步失败**整体回滚**；
**默认 `memory_write_policy="none"，绝不把每轮对话自动写入长期记忆**，写入须「显式授权 + 显式候选」
两条同时满足，候选 `scope` 非 `SESSION` 一律拒绝（不得升级为稳定人格特征）。
用户称这一步为 P3.6，编号差异同 Q-37（= 仓库 P4 的集成环节）；
实现口径见 `DECISIONS.md` §2.22（D-174~D-184），遗留问题见 `RUNNING.md` §5.9（H-7~H-9，均已接受）。

**P3 第七步（P5 端到端集成验收 + Evaluation 核心）已实现，待人工验收**：`persona/modules/evaluation.py`
扩为只读 **`EvaluationService`**——`check_invariants` 覆盖 **INV-1~INV-4**
（`dialogue_does_not_write_evidence` / `simulated_not_in_evidence` / `memory_cannot_become_evidence` /
`conflict_not_resolved` / `no_intimacy_field` / `variants_not_merged` / `no_person_specific_literals`，
服务 AC-13/14/15）、`check_dependencies`（AC-20）、`run_static_checks`、`snapshot`/`dialogue_leaks`、
`measure_quality`（**只输出结构性指标，不做主观打分**）。新增 `tests/test_integration_acceptance.py`（26 项）
验证真实支持的端到端链路（任务创建/执行 → 证据/档案读写 → 对话生成 → 记忆按规则写入 → 评测）、
数据隔离（四张现实表零变化）、预算暂停+提额续跑不重复、Provider/存储失败与幂等、临时库隔离。
**诚实边界**：尚无业务编排型 Orchestrator（CLI 经 `TaskService` 驱动的是 Mock 抽象工作单元），
且真实检索未接入，故 AC-1/AC-2「一条命令跑全流程」尚不满足。
用户称这一步为下一阶段，编号差异同 Q-37（= 仓库 P5）；实现口径见 `DECISIONS.md` §2.22.1（D-185~D-190），
遗留问题见 `RUNNING.md` §5.10（H-10~H-13）。

**H-11A（Research 最小可运行实现）已实现，待人工验收**：`persona/modules/research.py` 由 Protocol-only
推进为 **`ResearchService`**，为后续业务编排器提供稳定接口。流程：`plan_queries`（**确定性模板**，不调模型）
→ `collect`（经 `Provider.search`；**0 结果不是错误**，返回 `fetch_status=NO_RESULT`）
→ `filter_and_rank`（无效材料**带原因**拒绝、批内去重、确定性结构排序、可带对象边界）
→ `register`（**只经 `Evidence.register_source`**，失败整体传播、绝不伪造部分成功）
→ `run`（聚合 `planned_count`/`executed_count`/`no_result_count`/`budget_used`/`warnings`）。
**隔离**：只注入 `SourceRegistrarPort`（协议里**只有 `register_source`**，F-10 结构上被堵死），
模块内**没有 `Storage`/`sqlite3`/`add_evidence`**；只登记 `Source`（恒 `PUBLIC_SOURCE`），
**不产生 `EvidenceItem`/`SourceEvent`**，模拟内容仍被存储层闸门拒绝。
**失败纪律**：单查询失败→提示并继续；全部失败→上报 `Err`；`BUDGET_EXCEEDED`→立即传播（暂停信号）；
`budget` 参数仅为签名兼容，**不记账**（预算归 Orchestrator）。
**契约取舍**：`VALIDATION.empty_targets` 未登记故复用 `required_field`（零新增错误码）；
`NO_RESULT` 用模块常量而不改已审 `FetchStatus`；`RankedCandidate.score` → `rank_weight` 以配合 INV-4。
用户称这一步为 H-11A，编号差异同 Q-37（= 仓库 P2 的 Research 部分）；
实现口径见 `DECISIONS.md` §2.23（D-191~D-205）、`INTERFACES.md` §5.3，遗留问题见 `RUNNING.md` §5.11（H-14~H-17）。

**H-10A（业务编排型 Orchestrator）已实现，待人工验收**：新增 `persona/orchestrator/business.py`
（`BusinessOrchestrator`），把 `Research → Evidence → Persona → Relationship → Dialogue` 串成一条命令。
**职责分离**：既有 `TaskService` / `TaskRunner` / `BudgetGate` / 检查点**一行未改**——业务编排器实现
`executor.UnitHandler`（`plan` / `execute`）被既有 `TaskRunner` 驱动，**不复制第二套六态机或预算账本**
（源码级静态断言：不含 `can_transition` / `TaskState` / `gate_for` / `BudgetGate` / `.complete(` / `.pause(` / `.fail(`）。
**调用链**：`Research.run → source_ids` → `Evidence.add_evidence → evidence_ids`（seed 的 `source_ref`
必须解析到本次研究**实际登记**的来源）→ `Persona → persona_profile_ids`（按维度机械搬运；
`SINGLE` 的行为只进 `single_events`，不进 `traits`，AC-7）→ `Relationship → relationship_profile_ids`
（**只用 RELATION 维度证据**，绝不从两份档案拼关系；≥3 人 → `GROUP` + `group_dynamics`）→
`Dialogue → session_id + turn_ids`（用已有档案，产物恒 `SIMULATED`）。阶段间数据**只经已落盘的检查点**
`artifact_ids` 传递。
**结果三态**：`SUCCEEDED` / `PARTIAL` / `PAUSED` / `FAILED`——**只有五步全部执行且任务 `COMPLETED` 才算全流程成功**，
部分成功绝不报成成功；任一步失败 → 后续阶段记 `NOT_REACHED`（不伪造成功）。
**恢复**：预算耗尽 → `PAUSED`（不是失败），提额后 `resume` 继续且不重复已完成阶段（AC-11）；
重跑不重复登记来源/证据、不产生空转档案版本。
**AC-1 / AC-2 仍为部分满足**：一条命令能跑完五步，但证据 claim 需调用方显式提供
（缺口 G-2：源→证据自动抽取未实现，编排器**不伪造 claim**）；3 人时群体互动维度存在但未自动填充内容（G-4）。
实现口径见 `DECISIONS.md` §2.24（D-206~D-220）、`INTERFACES.md` §12.7、遗留见 `RUNNING.md` §5.12（G-1~G-5）。

**G-2（来源材料 → 候选 claim 自动抽取）已实现，待人工验收**：新增
`persona/modules/extraction.py`（`ClaimExtractorPort` 端口 + `MockClaimExtractor` 确定性离线
+ `ProviderClaimExtractor` 只定义契约、本阶段**不接真实模型**）。
**核心数据缺口**：此前 `Research` **只返回 `source_ids`**，把检索摘要 `snippet` 丢掉了，
下游根本没有材料可抽——现在 `ResearchOutput` 追加 `materials: list[SourceMaterial]`
（`SourceMaterial` 下沉到 `core/types.py`，避免 L4↔L4 同层调用）。
**调用方实际只需要给目标人物**：`evidence_seeds` 变为**可选的显式覆盖通道**。
**实际链路**：`Research.run → source_ids + materials` → `ClaimExtractorPort.extract(materials)
→ 候选 claim` → `Evidence.add_evidence`（原文/归纳）/ `Evidence.record_inference`（推断）
→ `Evidence.detect_conflicts`（只记录不裁决）。
**三种 claim 机械区分**：`VERBATIM` 必须带可在材料里逐字找到的摘录，否则
`QUOTE_NOT_IN_MATERIAL` 拒收；归纳不得带摘录冒充原话；`INFERENCE` 走 `record_inference`
且**必须解析出根证据，否则丢弃**（EV-V3）。**等级沿用既有定义，不等于真实性**。
**冲突不合并**：去重只认「同来源 + 同 claim + 同主体」精确匹配，说法不同各自保留。
**抽取器拿不到 `Evidence`**（AST 断言：不 import evidence、无 `add_evidence`/`record_inference`
调用），结构上无法把候选直接标成"已验证证据"。
**Mock 与真实能力的边界**：`MockClaimExtractor` 是**查表**，产出一律打 `mock-fixture` 标签；
它证明的是**链路连通**，**不代表真实材料抽取质量/召回已验证**，也不代表真实网络研究已通过。
实现口径见 `DECISIONS.md` §2.25（D-221~D-235）、`INTERFACES.md` §5.4、遗留见 `RUNNING.md` §5.13
（新缺口 G-6：材料无法落检查点；H-17 正文抓取仍开）。

**任务怎么跑**：见 `docs/RUNNING.md` §2。要点：`task create → start → run → (预算不足) pause → resume`，
预算不够时**暂停并保存进度**（不是失败），提高额度后可续跑且**不重复执行**已完成的单元。

**下一步**：① 真实 `Provider` 接入决策（Q-01/Q-02/Q-03）；② 补齐 **H-17（正文抓取与切分）** 以提升抽取召回；
③ 两个 `distill` 的 AI 蒸馏主体（依赖 Provider 接入决策）；④ `Evaluation.run_unit_tests`/`run_integration_tests` 接线。

---

## 2. 文档地图（先看哪份？）

按推荐阅读顺序：

| 顺序 | 文件 | 用途 | 适合谁 |
| --- | --- | --- | --- |
| 1 | `README.md`（本文件） | 项目是什么、现在到哪一步、怎么理解整体 | 所有人，先看这个 |
| 2 | `SKILL.md` | **对外发布的技能说明**：何时激活、前置条件、四行跑通、边界与不做的事 | **只想拿来用的人，看这一份就够** |
| 3 | `docs/PROJECT_SPEC.md` | 产品需求：用户能用它做什么、不能做什么、怎么算做完 | 想知道「要做什么」的人 |
| 4 | `docs/ARCHITECTURE.md` | 模块怎么切、谁调用谁、数据怎么流（**总览**） | 想知道「怎么搭」的人 |
| 5 | `docs/DATA_MODEL.md` | 13 个核心实体长什么样、字段与校验规则 | 想知道「数据怎么存」的人 |
| 6 | `docs/INTERFACES.md` | **正式接口契约**：每个模块能干什么、不能干什么、每个操作的输入输出与失败原因 | **要写代码或写测试的人，必读** |
| 7 | `docs/ERROR_HANDLING.md` | 错误分类、重试、预算暂停、任务恢复 | 要处理异常与长任务的人 |
| 8 | `docs/ROADMAP.md` | 分几个阶段、每阶段交什么、什么条件才能往下走 | 想知道「什么时候做完」的人 |
| 9 | `docs/DECISIONS.md` | 哪些事已经定了（不许随便改）、哪些还没定 | 后续参与开发的人，**必读** |
| 10 | `docs/RUNNING.md` | **怎么把代码跑起来**：跑测试、用 CLI、目录说明、本阶段未实现清单 | **要动手运行的人，先看这个** |
| 11 | `docs/CONFIG.md` | **配置项、环境变量与密钥管理**：18 项非敏感配置 + 2 项敏感配置、优先级、本地设置方法 | 要改配置或接真实 Provider 的人 |
| 12 | `docs/P5_1_REAL_RUN.md` | P5.1 的**两次受控真跑**记录：调用清单、账单、验收结论、暴露并修掉的缺陷 | 想知道「真实链路到底跑成什么样」的人 |
| 13 | `docs/GRAY_RELEASE.md` | **试用说明**：当前状态、预算事实、必读边界、已知限制、反馈格式 | **准备试用的人，先看这个** |
| 14 | `docs/P6_0_DIALOGUE_EVAL_PLAN.md` / `P6_2_MEMORY_WRITE_PLAN.md` / `P6_3_TOPIC_PLAN.md` | 二期（对话质量评估 / 记忆写入 / 跨会话检索）的**方案与真跑对账** | 想知道「二期怎么验的、验到什么程度」的人 |

> **接口真源**：`ARCHITECTURE.md` 只做总览与指向；**接口细节一律以 `INTERFACES.md` 为准**。两者冲突时以 `INTERFACES.md` 为准。

> **给后续 Agent / 协作者的重要提醒**：`docs/DECISIONS.md` 里标记为「已确认」的决定，未经人工确认**不得擅自更改**。有任何冲突，先报问题，不要自己拍板。

---

## 3. 这个项目到底要做什么

### 3.1 用户看到的

```
用户输入：  "A 和 B"（两个或更多人名）
                │
                ▼
系统自己去查公开资料 ──► 建人物档案 ──► 建关系档案
                │
                ▼
     进入多人对话：系统自动决定"下一句谁来说、说什么"
```

用户**不需要**做的事：

- ❌ 不需要逐条审核证据
- ❌ 不需要读研究报告
- ❌ 不需要手动整理资料
- ❌ 不需要配置复杂的参数

### 3.2 系统内部依然要做的（用户看不见，但必须有）

- ✅ 每一条说法都能追溯来源
- ✅ 同一个来源重复出现要能识别出来
- ✅ 证据要分级（原话 > 背景 > 他人评价；系统推断单独标记）
- ✅ 来源互相矛盾时，**全部保留**并标注不确定性，不强行统一
- ✅ 有质量检查和自动化测试

### 3.3 四条证据原则（本项目的核心红线）

1. **优先级**：人物原话 `VERBATIM` > 人物背景/档案/经历 `PROFILE` > 他人提及与评价 `THIRD_PARTY`；系统推断单独用 `INFERENCE` 标记，**不参与前三者的排序**。
2. **优先级 ≠ 真伪**：原话级别高，不代表它一定是对的；也不代表它比"背景资料"更值得相信。优先级只用于**取舍排序**，不用于**真假判定**。
3. **单次行为不等于性格**：一个人某一次发火，不能写成"脾气暴躁"。稳定特征需要多次出现或跨场景重复才能成立。
4. **冲突不强行统一**：A 说 X，B 说 Y，两条都留着，各自标来源和不确定性，流程继续跑，不卡住。

---

## 4. 怎么理解整体架构（给编程初学者的比喻）

整个系统像一个**小型纪录片剧组**：

| 剧组角色 | 对应模块 | 干什么 |
| --- | --- | --- |
| 🎬 **制片主任** | `Orchestrator` | 唯一有权"指挥别人"的人。排期、控预算、卡住了能续跑 |
| 🔍 **资料搜集员** | `Research` | 去找公开资料，并把"从哪找的"登记在册 |
| 🗂️ **证据管理员** | `Evidence` | 把资料拆成一条条证据，分级、去重、记冲突 |
| 📇 **人物档案员** | `Persona` | 把证据浓缩成一个人的性格/习惯/说话风格 |
| 🔗 **关系档案员** | `Relationship` | 把证据浓缩成两个人之间怎么相处 |
| 🎭 **现场导演** | `Dialogue` | 决定"下一句谁说、说什么" |
| 📔 **场记本** | `Memory` | 记下对话里发生过什么，下次能想起来 |
| 🏢 **仓库 + 机房** | `Storage` / `Provider` | 存东西；对外接模型和搜索的统一插头 |
| ✅ **审片人** | `Evaluation` | 自动化测试和打分 |
| 💻 **前台** | `CLI` | 你在黑窗口里敲命令的地方 |

**一条最重要的规矩**：现场导演（`Dialogue`）可以往场记本（`Memory`）里写东西，但**绝对不能**回头去改证据管理员（`Evidence`）的档案。
——因为对话是**模拟**出来的，模拟出来的话不能变成"这个人真的说过这句话"。这是本项目的技术红线之一。

详细的模块关系请看 `docs/ARCHITECTURE.md`。

---

## 5. 目录结构（当前实际状态）

```
generic-persona-skill/
├── README.md
├── docs/                        # 设计文档（P0~P1.2-A 的产物）
│   ├── PROJECT_SPEC.md  ARCHITECTURE.md  DATA_MODEL.md
│   ├── INTERFACES.md    ERROR_HANDLING.md
│   ├── ROADMAP.md       DECISIONS.md     RUNNING.md
├── persona/                     # 主包（P1.2-B 建立的骨架，**不含业务逻辑**）
│   ├── core/                   # 公共契约：枚举 / ID / 时间 / 错误码 / Result / 上下文 / DTO
│   ├── storage/                # SQLite 最小读写（不做业务判断）
│   ├── providers/              # 外部服务统一接口 + 4 个 Mock 实现
│   ├── orchestrator/           # 六态任务状态机 + 检查点（**尚无任务引擎**）
│   ├── modules/                # 各业务模块的端口协议（**只声明，不实现**）
│   └── cli/                    # 命令行入口
└── tests/                       # 标准库 unittest 测试（临时库、离线、无第三方依赖）
```

> `fixtures/`（合成测试素材）与 `run_static_checks` 脚本按 `ROADMAP.md` §3B 属于 P1.2-B 计划内，
> **本轮按用户指令未创建**，已列入 `docs/RUNNING.md` §5 的待办。

---

## 6. 本项目明确不做的事（节选，完整版见 PROJECT_SPEC）

- 不做网页界面（先做本地命令行）
- 不训练、不微调自己的模型
- **默认离线；仅在显式授权 + 先看清单之后才真跑**：没有密钥、没有过一次确认时，Provider 全是 Mock，
  一个请求都不发。**只有 `persona run` 与 `persona chat` 两个命令会花钱**，两者共用**同一套**
  确认机制：先打印清单（几次检索 / 几次调用、预估多少 USD）**再问一次**「继续？[y/N]」——
  直接回车 = 取消、零调用零花费。`run` 的 `--dry` 永远只打清单不执行
- 不硬编码任何特定人物的设定、事实、关系或"标准答案"
- 不生成给用户看的长篇研究报告
- 不做多租户、登录鉴权

---

## 7. 已知限制（试用前请先看）

这三条都是**已定位、未修**的小问题，**不影响结果正确性**，但会让人困惑（内部记录见 `docs/RUNNING.md` §5.19）：

| # | 现象 | 实际影响 | 怎么绕 |
| --- | --- | --- | --- |
| 1 | 账单表 `external_usage_records.task_id` 恒为 `null` | 账单**无法按任务对账**；项目累计口径退化为全库累计（**比原设计更严**，不会超支） | 用量表 `usage_records.scope` 里有任务 ID，可按它查 |
| 2 | `persona status` 里模型调用上限显示为 `None` | 只是**显示**问题，预算闸门照常生效 | 以 `persona config show` 里的实际值为准 |
| 3 | `.env` **不吃行内注释**（`KEY=3 # 注释` 会把整行当值） | 配置校验报「不是整数」 | 注释必须**独占一行** |
| 4 | 账单表 `external_usage_records.task_id` 在 `chat` 路径恒为 `null` | `chat` 的调用**无法按任务对账**（`run` 路径同此，见上表 #1） | 按 `usage_records.scope` 查 |
| 5 | `PERSONA_MODEL_MAX_CALLS` 只数**本次进程**的调用次数 | 默认 3 会拦住 `--rounds 4` 的对话 | 临时抬高用**进程级环境变量**（`PERSONA_MODEL_MAX_CALLS=4 python -m persona chat …`） |

> 二期（对话 / 记忆 / 跨会话检索）的**完整已知局限清单**见 `docs/RUNNING.md` §5.44.3。

## 7.5 新手避坑 FAQ（灰度用户高频踩坑实录）

**Q：预算被「卡死」了？** 多半是三道闸在正常工作，不是坏了：
① 清单后问「继续？[y/N]」——回车 = 取消，输 y 才跑；② 花到软上限 2.00 USD 暂停询问，y 再给 2.00、n 安全退出；
③ `PERSONA_MODEL_MAX_CALLS` 默认 3 且只数**本次进程**——`--rounds 4` 或多响应/群聊会被拦（退出码 1、零花费），
抬高用进程级环境变量 `PERSONA_MODEL_MAX_CALLS=8 python -m persona chat …`，**别改 `.env`**。

**Q：提示找不到档案 / 不认识这个人？** 对话只读已有档案：同一批人名必须**先跑过 `persona run`**；
跑 `run` 时带了 `--db` / `--project` 的话，`chat` 必须传一模一样的值（默认 project 是 `prj_local`，换了等于换了库）。

**Q：改了环境变量没生效？** 环境变量在进程启动时读取——Windows `setx` 后要**新开终端**才生效；
另外 `.env` 注释必须独占一行（`KEY=3 # 注释` 会把整行当值）。

**Q：真跑后多了 `var/`、跑测试红一条？** 正常：`var/` 是默认数据目录（已 gitignore），
测试要求仓库根无真实数据——删掉即可，或真跑时加 `--out <其它目录>`。

**Q：角色互聊的话会被「记住」吗？** 绝不会（红线）：只有**你的原话**经 `--memory-write rule` 才落库，
角色互聊轮物理零写入，跨会话检索也验过不污染。

## 8. 现在该做什么

1. **跑一次 `persona run` 试用**（最推荐）：照 `SKILL.md` 的四行跑通来——先 `--dry` 看清单，确认后再真跑；
   跑完用 `show` / `show-relationship` / `evidence` / `export` 看结果。
2. **接着跑一场 `persona chat`**（会花钱，但很便宜：实测 ≈0.0005 USD/次）：先 `--rounds 2` 试交互，
   再加 `--memory-write rule --topic "某个话题"` 看记忆写入与跨会话记住。
3. 或者**读 `SKILL.md` 了解边界**：能做什么、绝不编造什么、来源怎么数、什么时候算「够」。
4. 准备给别人试用：见 `docs/GRAY_RELEASE.md`（预算事实、必读边界、已知限制、反馈格式）。

如需修改任何标记为「已确认」的决定，请走 `docs/DECISIONS.md` 里的变更流程，不要直接改文档内容。
