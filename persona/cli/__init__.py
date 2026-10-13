"""入口层（L6）：命令行。只允许经 Orchestrator 驱动业务，本阶段不含业务逻辑。"""

from .main import main

__all__ = ["main"]
