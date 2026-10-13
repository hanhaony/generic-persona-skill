"""`persona.config` —— **全项目唯一的配置入口**（D-077）。

用法（其他模块就这么写，不要自己 `os.getenv`）：

```python
from persona.config import load_settings, test_settings

result = load_settings()            # 返回 Result[AppSettings]
if is_err(result):
    ...                             # 配置有问题，按 error.message 提示用户
settings = unwrap(result)
```

- 数据结构、校验规则：`spec.py`
- 加载、合并、`.env` 解析：`loader.py`
- 完整说明：`docs/CONFIG.md`
"""

from .loader import (
    DEFAULT_DOTENV_NAME,
    WARNING_SECRET_IN_DOTENV,
    WARNING_UNKNOWN_KEY,
    default_settings,
    describe_fields,
    load_settings,
    parse_dotenv,
    read_dotenv,
    test_settings,
)
from .spec import (
    CONFIG_FIELDS,
    ENV_PREFIX,
    ENV_PROVIDER_API_KEY,
    ENV_SEARCH_API_KEY,
    MIN_SECRET_LENGTH,
    SECRET_ENV_KEYS,
    AppSettings,
    BudgetLimits,
    FieldSpec,
    LogLevel,
    RetryLimits,
    RunMode,
    Secrets,
    get_path,
    parse_value,
    set_path,
    validate_settings,
)

__all__ = [
    # 数据结构
    "AppSettings",
    "BudgetLimits",
    "RetryLimits",
    "Secrets",
    "RunMode",
    "LogLevel",
    "FieldSpec",
    "CONFIG_FIELDS",
    # 加载
    "load_settings",
    "test_settings",
    "default_settings",
    "parse_dotenv",
    "read_dotenv",
    "get_path",
    "parse_value",
    "set_path",
    "validate_settings",
    "describe_fields",
    # 常量
    "ENV_PREFIX",
    "ENV_PROVIDER_API_KEY",
    "ENV_SEARCH_API_KEY",
    "SECRET_ENV_KEYS",
    "MIN_SECRET_LENGTH",
    "DEFAULT_DOTENV_NAME",
    "WARNING_UNKNOWN_KEY",
    "WARNING_SECRET_IN_DOTENV",
]
