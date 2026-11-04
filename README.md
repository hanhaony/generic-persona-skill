# generic-persona-skill

**跨世界观多智能体群聊引擎** —— 输入任意人物的名字（现实公众人物、游戏动漫影视角色，
甚至把不同世界的人拉进同一间屋），引擎自动检索公开资料、蒸馏出**带证据链的人物档案
与关系档案**，然后让这些档案驱动一场多人、多轮、有长期记忆的模拟群聊。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-2036%20passed-brightgreen.svg)]()
[![Deps](https://img.shields.io/badge/engine%20deps-zero-success.svg)]()

---

## 它能干什么

```bash
# 红楼梦三人同台：你一句问话，三个人自己接话、拌嘴、互聊
python -m persona run  --names "林黛玉,贾宝玉,薛宝钗" --work-title 红楼梦
python -m persona chat --names "林黛玉,贾宝玉,薛宝钗" --responses 3 --group 3 \
    --message "今日园子里可有什么新鲜事？"
```

更刺激的——**跨世界观同台**：现实演员 + 漫威英雄 + DC 英雄关进一个群。

- 同作品的角色**彼此相识**（托尼和史蒂夫不必互相自我介绍）；
- 跨作品的角色互为陌生人（布鲁斯对漫威众人好奇又防备）；
- 现实演员遇到自己演过的角色，允许来一次「我演过你」的破次元吐槽；
- 内敛深沉的角色惜字如金，活泼年轻的角色抢话碎碎念——**没有人用复读机的腔调说话**。

## 截图

<!-- TODO(开源发布前补图)：跑一场对话后截图替换下面两个占位 -->

| CLI | Streamlit Web 界面 |
| --- | --- |
| ![CLI 演示](docs/screenshots/cli-demo.png) | ![Web 界面演示](docs/screenshots/web-demo.png) |

Web 界面由仓库根的 `app.py` 提供（Streamlit 薄壳）：逐名配置类型与作品、
头像昵称装扮、聊天气泡渲染，适合不想碰命令行的用户。

## 核心特性

**🛡️ 防幻觉证据链**
每一条人设都能追到具体来源 URL。只有 ≥2 个独立来源提到的才写成「稳定性格」；
单次行为显式标注「只发生过一次，不得说成一贯」；材料里没有的，角色以自己的
口吻承认不知道——**绝不编造一条凑数**（红线有 2036 项自动化测试守着）。

**🧠 长期记忆**
`--memory-write rule` 按确定性规则把**你的原话**写进会话记忆（0 额外调用，
只记你说的话、不记模型编的话，结构上堵死回声室）；`--topic` 跨会话检索，
下次开聊她还记得你。说「叫我 XX」，之后每个会话都会这么称呼你。

**🎭 角色人格隔离**
人物档案与关系档案按证据装配、按作品版本隔离，绝不跨版本合并；对话产物永远
带 `SIMULATED` 标记，**模拟出来的话永远不会反过来污染档案与证据库**
（不变式断言 + 存储层闸门双层把守）。关系档案双向视角独立存储：A 看 B ≠ B 看 A。

**⏳ 时代自适应**
确定性时代探测 + 动态身份块：现代人说现代口语、古人用古典用语，两边示例互斥——
Ben Affleck 不会满口「阁下客官」，林黛玉也不会蹦出「Hi 兄弟」。材料的说话方式
描写会作为「表达 DNA」注入（口音、幽默、口头禅、句式长短）。

**👥 多角色群聊 + 跨宇宙认知**
`--responses N` 让 N 个角色依次回应你，`--group N` 让他们自主互聊 N 轮
（互聊内容绝不进长期记忆）；群像法则让每个角色按性格决定话多话少；
同作品旧识免客套、跨作品陌生人、真人×虚构角色的次元壁处理，全部由登记数据
确定性判定，不靠模型猜。

## 快速开始

**前置**：Python 3.12+（引擎零第三方依赖）；模型密钥（DeepSeek）与检索密钥（Tavily）。

**① 获取代码**

```bash
git clone https://github.com/hanhaony/generic-persona-skill.git
cd generic-persona-skill
```

**② 配置密钥**（只走环境变量，绝不写进文件）

```bash
# Git Bash / Linux / macOS
export PERSONA_PROVIDER_API_KEY="sk-你的DeepSeek密钥"   # 模型
export PERSONA_SEARCH_API_KEY="tvly-你的Tavily密钥"     # 检索

# Windows PowerShell
$env:PERSONA_PROVIDER_API_KEY = "sk-你的DeepSeek密钥"
$env:PERSONA_SEARCH_API_KEY = "tvly-你的Tavily密钥"
```

**③ 开跑**

```bash
# 先离线自检（不联网、不花钱）
python -m persona selftest

# 建档案：先 --dry 只看清单（0 花费），确认后去掉 --dry 真跑（会联网、会花钱）
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦 --dry
python -m persona run --names "林黛玉,贾宝玉" --work-title 红楼梦

# 查看成果（四条只读命令，不花钱）
python -m persona show 林黛玉                                # 六分区人物档案
python -m persona show-relationship 林黛玉,贾宝玉            # 双向关系视角
python -m persona evidence 林黛玉                            # 每条特征 → 证据 → 来源 URL
python -m persona export 林黛玉 --format md --out 林黛玉.md  # 导出 Markdown
```

**（可选）④ 图形界面**

```bash
pip install streamlit          # Web 壳的唯一依赖
python -m streamlit run app.py
```

侧边栏填密钥 → 输入人名 → 给每个人选「真实人物 / 虚构角色」和所属作品 →
点「生成人物档案」→ 开聊。

## 费用与安全

- **默认离线**：没有密钥、没有过一次确认时，所有外部服务都是 Mock，一个请求都不发；
- **只有 `run` 和 `chat` 两个命令花钱**，都先打印清单（几次检索、几次调用、预估多少
  美元）再问一次「继续？[y/N]」——直接回车 = 取消，零调用零花费；
- 实测成本：对话 ≈0.0005 USD/次调用，一次建档 ≈0.008 USD（预算软上限 2.00 USD，
  花到会暂停询问，硬上限 5.00 USD 兜底）；
- 密钥只读环境变量，`.env` 里写敏感变量会被忽略并告警。

## 30 秒理解工作原理

```
人名输入 ──► 检索公开资料（每条来源登记在册）
        ──► 资料拆成证据（分级 / 去重 / 冲突只记录不裁决）
        ──► 按证据装配人物档案 + 关系档案（确定性搬运，不是模型自由发挥）
        ──► 多智能体对话（档案驱动发言；产物恒为模拟，绝不回写档案）
```

AI 只用在三处：检索、材料→证据抽取、对话生成。**人格与关系档案是确定性装配**——
这既是防幻觉的根基，也是这个项目与「让模型随便演」的做法最大的区别。

## 新手 FAQ

**Q：预算被「卡死」了？** 那是三道闸在正常工作：① 清单后「继续？[y/N]」回车 = 取消；
② 花到软上限 2.00 USD 暂停询问，y 再给 2.00、n 安全退出；③ `PERSONA_MODEL_MAX_CALLS`
只数本次进程，多轮对话会被拦——抬高用进程级环境变量
`PERSONA_MODEL_MAX_CALLS=8 python -m persona chat …`，别改 `.env`。

**Q：提示「不认识这个人」？** 对话只读已有档案：同一批人名必须先跑过 `persona run`；
`run` 与 `chat` 的 `--db` / `--project` 必须一致（换了等于换了库）。

**Q：真跑后仓库根多了 `var/`？** 正常——那是默认数据目录（已被 `.gitignore` 忽略），
删掉即可，或真跑时加 `--out <其它目录>`。

**Q：角色互聊的话会被「记住」吗？** 绝不会（红线）：只有你的原话经
`--memory-write rule` 才落库，角色互聊轮物理零写入。

## 文档导航

| 想做什么 | 看哪份 |
| --- | --- |
| 只想用起来 | `SKILL.md`（五分钟上手）或本文件「快速开始」 |
| 命令、配置、实测记录 | `docs/RUNNING.md` |
| 配置项与环境变量 | `docs/CONFIG.md` |
| 架构总览 / 接口契约 | `docs/ARCHITECTURE.md` / `docs/INTERFACES.md` |
| 设计决定与变更历史 | `docs/DECISIONS.md` / `docs/RUNNING.md` §6 |
| 试用边界与反馈格式 | `docs/GRAY_RELEASE.md` |

## 当前阶段与已知限制

当前阶段：**v1.0.0 灰度发布**（代码冻结，tag `v1.0.0`）：CLI 六命令 + 对话 + 记忆写入 +
跨会话检索 + 多角色群聊 + 跨世界观适配 + Streamlit Web 壳，全量 **2036 项测试**。

三条已定位、未修的小问题（不影响结果正确性，内部记录见 `docs/RUNNING.md` §5.19）：

| # | 现象 | 实际影响 | 怎么绕 |
| --- | --- | --- | --- |
| 1 | 账单表 `task_id` 恒为 `null` | 无法按任务对账（累计口径更严，不会超支） | 按 `usage_records.scope` 查 |
| 2 | `persona status` 上限显示 `None` | 只是显示问题，闸门照常生效 | 以 `config show` 实际值为准 |
| 3 | `.env` 不吃行内注释 | 整行会被当值 | 注释独占一行 |

## 参与贡献

欢迎 Issue 与 PR。修改前请先读 `docs/DECISIONS.md`（标记「已确认」的决定不得擅自
更改）与 `docs/INTERFACES.md`（接口契约的真源）；跑 `python -m unittest discover
-s tests -t .` 确保全绿再提交（仓库纪律：运行时零第三方依赖，测试只用标准库）。

## License

本项目基于 [MIT License](LICENSE) 开源发布。

```
MIT License

Copyright (c) 2026 hanhaony / haohanyang5@gmail.com
```

使用本项目即表示同意遵守协议全文；软件按「现状」提供，不含任何形式的担保。
