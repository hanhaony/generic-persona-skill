---
name: generic-persona-skill
description: 输入 2 个或更多人名，自动检索公开资料，建立人物档案与关系档案，可导出 Markdown。触发词：蒸馏人物、建立人物档案、分析人物关系。
---

# Generic Persona Skill

## 何时激活

用户说「蒸馏 XX」「建立 XX 的档案」「分析 XX 和 XX 的关系」。

## 前置条件

- Python 3.12+。运行时**零第三方依赖**（只用标准库），`pip install -e .` 是可选的。
- 两个密钥都只对 `persona run` 必需，且**只认环境变量**（写进 `.env` 会被忽略并告警）：

| 用途 | 主变量名（推荐） | 别名（服务商惯用名） |
| --- | --- | --- |
| 模型抽取（特征抽取 / 判定） | `PERSONA_PROVIDER_API_KEY` | `DEEPSEEK_API_KEY` |
| 真实检索（找公开资料） | `PERSONA_SEARCH_API_KEY` | `TAVILY_API_KEY` |

> 主次说明：两者的地位是「同一份密钥的两个名字」，不是两套配置。填任意一个都生效，
> 同时填以 `PERSONA_` 前缀为准。缺检索密钥时 `run` 会**直接拒绝运行**——拿空材料去调模型
> 等于钱照花、证据一条没有。

## 四行跑通

```bash
pip install -e .                        # 可选；不装就全程用 python -m persona …
export PERSONA_PROVIDER_API_KEY=xxx     # 主（别名 DEEPSEEK_API_KEY）
export PERSONA_SEARCH_API_KEY=xxx       # 主（别名 TAVILY_API_KEY）
persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --vocabulary l2
```

> 为什么是四行不是三行：检索和模型是**两个独立服务商、两把密钥**，只配模型那把会跑不起来。
>
> ⚠️ **第一次 `run` 会先算账再问一次**：打印「将要发生 N 次检索 + M 次模型调用，预估 X USD
> （单任务上限 0.25 USD）。继续？[y/N]」。**直接回车 = N = 取消**，此时一分钱不花、一个请求不发。
> 同一个数据目录确认过一次之后会记住；非交互 / CI 环境请显式加 `--yes`。
> 想只看清单不执行，用 `--dry`（不执行、不落库、不花钱、不询问）。

## 查看结果

```bash
persona status                          # 任务跑到哪一步了
persona show 林黛玉                      # 人物档案（稳定性格 / 习惯 / 说话方式 / 单次行为 / 背景 / 经历）
persona show-relationship 林黛玉,贾宝玉   # 关系档案（双向视角）
persona evidence 林黛玉                  # 证据（按特征分组，可追到 URL）
persona export 林黛玉 --format md        # 导出可直接贴进文档的 Markdown
```

## 边界

- 不编造：没有证据支撑的字段留空 + 写明原因；
- 单次行为不进 `traits`（AC-7）；
- 证据不足时输出空档案 + 原因，好过 1 条编出来的特征；
- 探针参数（`--probe-*`）不暴露给用户，只留在 e2e 脚本；
- **「N 个来源」是去重后的独立来源数，不是证据条数**——同一篇文章里出现两次只算 1 个来源；
- **凑够 2 个独立来源会自动晋升**：同一个人、同一个说法，第 2 个独立来源出现后，稳定性从
  `SINGLE` 变 `REPEATED` 并进入 `traits`（≥3 个来源再升 `PATTERN`）。这是确定性规则，不是模型判断；
  所以「单次行为不进 traits」的正确理解是**「只有 1 个来源的说法不进 traits」**，不是「永远不进」。

## 不做的事

- 不做网页、不训练模型；
- 第一版不支持非公众人物（隐私边界待拍板）；
- 对话和记忆管理推到第二期（真实链路未验证）。
