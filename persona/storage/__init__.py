"""持久化层（L2）。只做存储，**不做业务判断**（INTERFACES §3.1）。"""

from .schema import DDL_STATEMENTS, RECORD_KINDS, SCHEMA_VERSION
from .sqlite import SqliteStorage

__all__ = ["SqliteStorage", "SCHEMA_VERSION", "DDL_STATEMENTS", "RECORD_KINDS"]
