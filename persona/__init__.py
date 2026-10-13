"""generic-persona-skill —— 通用人物与关系模拟工具。

当前阶段：**P1.2-B 最小 Python 空壳**。

本包**只有骨架，没有业务逻辑**：

- `persona.core`      —— 公共契约（枚举、ID、时间、错误码、Result 信封、调用上下文、通用 DTO）
- `persona.storage`   —— SQLite 最小初始化与读写（不做业务判断）
- `persona.providers` —— 外部服务统一接口 + 四个 Mock 实现（**全程离线**）
- `persona.orchestrator` —— 六态任务状态机与检查点（**不含任务引擎**）
- `persona.modules`   —— 各业务模块的端口协议（**只声明不实现**）
- `persona.cli`       —— 命令行入口（只有启动、帮助、说明，业务命令尚未实现）

设计依据（先读文档，再看代码）：
    docs/DATA_MODEL.md   docs/INTERFACES.md   docs/ERROR_HANDLING.md   docs/DECISIONS.md
"""

__version__ = "0.1.0.dev0"

# 当前骨架所处的开发阶段，供 CLI 展示
STAGE = "P1.2-B（最小 Python 空壳：只有结构，没有业务实现）"
