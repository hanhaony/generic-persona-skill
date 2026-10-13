"""generic-persona-skill —— 通用人物与关系模拟工具。

当前阶段：**P2.3 任务执行与状态管理基础闭环**。

已完成的阶段：P0 规格与架构、P1.1 数据模型、P1.2-A 接口契约、P1.2-B 代码骨架、
P2.1 工程配置、P2.2 配置管理、**P2.3 任务生命周期基础闭环**。

本包**有骨架与任务闭环，但没有业务实现**：

- `persona.core`      —— 公共契约（枚举、ID、时间、错误码、Result 信封、调用上下文、通用 DTO）
- `persona.storage`   —— SQLite 最小初始化与读写（不做业务判断）
- `persona.providers` —— 外部服务统一接口 + 四个 Mock 实现（**全程离线**）
- `persona.config`    —— **唯一配置入口**（敏感配置只走环境变量，永不回显）
- `persona.orchestrator` —— 六态状态机、`TaskService`、预算闸门、**Mock 执行器**
- `persona.modules`   —— 各业务模块的端口协议（**只声明不实现**）
- `persona.cli`       —— 命令行入口（启动、帮助、配置、任务生命周期；业务命令仍是占位）

⚠️ 研究与证据等**真实业务尚未实现**：`task run` 推进的是抽象工作单元，
不调模型、不联网、不做人物研究。

设计依据（先读文档，再看代码）：
    docs/DATA_MODEL.md   docs/INTERFACES.md   docs/ERROR_HANDLING.md   docs/DECISIONS.md
"""

__version__ = "0.1.0.dev0"

# 当前骨架所处的开发阶段，供 CLI 展示
STAGE = "P2.3（任务执行与状态管理基础闭环：业务实现尚未开始）"
