"""配置的**加载**与合并（`docs/CONFIG.md`，DECISIONS §2.12）。

优先级（D-080，由低到高）：

    内置默认值  <  .env 文件  <  进程环境变量  <  显式传入的 overrides

为什么是这个顺序：
    默认值保证"什么都不配也能跑"；`.env` 方便本地持久化个人设置；
    环境变量方便 CI / 临时覆盖；显式 overrides 只给测试和程序内部使用，必须说了算。

两条安全红线：
    - **敏感变量（API Key）只从 `env` 读取**，`.env` 里写了也**直接忽略**并告警（D-078、D-079）；
    - **未知配置项只告警不失败**，避免多打一个变量就崩溃（D-082）。
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from ..core.errors import AppError
from ..core.result import Err, Ok, Result, WarningNote
from .spec import (
    CONFIG_FIELDS,
    ENV_PREFIX,
    ENV_PROVIDER_API_KEY,
    ENV_SEARCH_API_KEY,
    SECRET_ENV_KEYS,
    AppSettings,
    FieldSpec,
    LogLevel,
    RunMode,
    Secrets,
    config_error,
    parse_value,
    set_path,
    validate_settings,
)

DEFAULT_DOTENV_NAME = ".env"

# 未识别的环境变量（只告警）
WARNING_UNKNOWN_KEY = "CONFIG_UNKNOWN_KEY"
# .env 里出现敏感变量（已忽略）
WARNING_SECRET_IN_DOTENV = "CONFIG_SECRET_IN_DOTENV"

# 用户更顺手的短名 → 集中配置字段（`PERSONA_*` 本体）。D-077 要求所有配置集中在
# `persona.config` 且统一前缀；这里只做"别名映射"，最终仍以 `PERSONA_*` 为准、走同一校验。
ENV_ALIASES: dict[str, str] = {
    "SEARCH_PROVIDER": "PERSONA_PROVIDER",
    "RESEARCH_MAX_QUERIES": "PERSONA_RESEARCH_MAX_QUERIES",
    "RESEARCH_MAX_RESULTS": "PERSONA_RESEARCH_MAX_RESULTS",
    "RESEARCH_MAX_EXTRACTS": "PERSONA_RESEARCH_MAX_EXTRACTS",
    "RESEARCH_MAX_COST": "PERSONA_RESEARCH_MAX_COST",
    "RESEARCH_MAX_REMEDIATIONS": "PERSONA_RESEARCH_MAX_REMEDIATIONS",
}
# 密钥别名：服务商惯用名 → 本项目的敏感变量名。
#   TAVILY_API_KEY    → 搜索密钥（真实搜索 Provider，P4.2）
#   DEEPSEEK_API_KEY  → 模型密钥（真实模型 Provider，P4.4-B）
# 别名有两层作用，**缺一不可**：
#   1) 环境变量里写了惯用名，本项目也能读到（不用把密钥改个名字再存一遍）；
#   2) `.env` 文件里写了惯用名，会被识别为"敏感变量写进文件"并**告警忽略**
#      ——没有这一层，`DEEPSEEK_API_KEY=...` 会落在 `PERSONA_` 前缀之外被**静默忽略**，
#      用户以为配好了，实际一直没生效。
ENV_SECRET_ALIASES: dict[str, str] = {
    "TAVILY_API_KEY": ENV_SEARCH_API_KEY,
    "DEEPSEEK_API_KEY": ENV_PROVIDER_API_KEY,
}


def _apply_aliases(raw: dict[str, str]) -> dict[str, str]:
    """把用户短名键重命名为集中字段名（非 PERSONA_ 短名只在此消歧义）。"""
    out: dict[str, str] = {}
    for key, value in raw.items():
        target = ENV_ALIASES.get(key) or ENV_SECRET_ALIASES.get(key, key)
        out[target] = value
    return out


# ---------------------------------------------------------------------------
# 极简 .env 解析（不引入 python-dotenv，D-079）
# ---------------------------------------------------------------------------


def parse_dotenv(text: str) -> dict[str, str]:
    """解析 `.env` 文本。只支持三件事：`KEY=VALUE`、`#` 注释、可选的引号。

    不支持变量插值（`$OTHER`）—— 那是复杂依赖才需要的，本项目不需要。
    """
    parsed: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            parsed[key] = value
    return parsed


def read_dotenv(path: str | Path) -> dict[str, str]:
    """读取 `.env` 文件；文件不存在时返回空字典（**不报错**）。"""
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    return parse_dotenv(file_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def load_settings(
    *,
    env: Mapping[str, str] | None = None,
    dotenv_path: str | Path | None = None,
    use_dotenv: bool = True,
    overrides: Mapping[str, Any] | None = None,
    cwd: str | Path | None = None,
) -> Result[AppSettings]:
    """集中加载配置。**这是全项目唯一的 `os.getenv` 调用点**（D-077）。

    参数：
        env          环境变量来源，默认取 `os.environ`；测试可传一个假字典。
        dotenv_path  `.env` 路径；为 None 时取当前目录下的 `.env`（不存在则跳过）。
        use_dotenv   False 则完全不读文件（测试常用）。
        overrides    显式覆盖，键用**环境变量名**；优先级最高。
        cwd          解析相对 `.env` 路径的基准目录。

    返回：`Ok(AppSettings)`（可能带 warnings）或 `Err(AppError)`。
    """
    environment = dict(os.environ if env is None else env)
    warnings: list[WarningNote] = []
    sources: dict[str, str] = {}

    # ---------- 第 1 层：内置默认值 ----------
    settings = AppSettings()
    for spec in CONFIG_FIELDS:
        sources[spec.path] = "default"

    # ---------- 第 2 层：.env 文件（非敏感项） ----------
    if use_dotenv:
        env_file = Path(dotenv_path) if dotenv_path is not None else (
            Path(cwd) if cwd is not None else Path.cwd()
        ) / DEFAULT_DOTENV_NAME
        for key, raw in _apply_aliases(read_dotenv(env_file)).items():
            if not key.startswith(ENV_PREFIX):
                continue                      # 非本项目的变量，与本项目无关
            if key in SECRET_ENV_KEYS:
                # 安全红线：密钥不许写进文件
                warnings.append(WarningNote(
                    code=WARNING_SECRET_IN_DOTENV,
                    message=f"{env_file} 里出现了敏感变量 {key}，已忽略。"
                            f"敏感配置请改用环境变量设置。",
                ))
                continue
            applied, problem = _apply_raw(settings, key, raw)
            settings = applied
            if problem is not None:
                return _fail([problem], warnings)
            sources[_spec_of(key)] = "dotenv"

    # ---------- 第 3 层：环境变量 ----------
    for key, raw in _apply_aliases(environment).items():
        if not key.startswith(ENV_PREFIX):
            continue
        if key in SECRET_ENV_KEYS:
            continue                          # 密钥在下面单独处理
        if key not in {spec.env_key for spec in CONFIG_FIELDS}:
            warnings.append(WarningNote(
                code=WARNING_UNKNOWN_KEY,
                message=f"未知的环境变量 {key}（已忽略）。也许是拼写错误？",
            ))
            continue
        applied, problem = _apply_raw(settings, key, str(raw))
        settings = applied
        if problem is not None:
            return _fail([problem], warnings)
        sources[_spec_of(key)] = "env"

    # ---------- 第 4 层：显式 overrides（只覆盖非敏感项） ----------
    for key, value in _apply_aliases(dict(overrides or {})).items():
        if key in SECRET_ENV_KEYS:
            warnings.append(WarningNote(
                code=WARNING_SECRET_IN_DOTENV,
                message=f"overrides 里出现了敏感变量 {key}，已忽略。"
                        f"敏感配置只能来自环境变量。",
            ))
            continue
        if key not in {spec.env_key for spec in CONFIG_FIELDS}:
            warnings.append(WarningNote(
                code=WARNING_UNKNOWN_KEY,
                message=f"未知的覆盖项 {key}（已忽略）。",
            ))
            continue
        applied, problem = _apply_raw(settings, key, _stringify(value))
        settings = applied
        if problem is not None:
            return _fail([problem], warnings)
        sources[_spec_of(key)] = "explicit"

    # ---------- 敏感配置：只从环境变量来 ----------
    # 用**位置参数**构造：既避免写出字段名，也保证顺序固定。
    # 取值顺序：`PERSONA_*` 本体优先，其次才是服务商惯用别名（见 ENV_SECRET_ALIASES）。
    secrets = Secrets(
        _secret_from_env(environment, ENV_PROVIDER_API_KEY),
        _secret_from_env(environment, ENV_SEARCH_API_KEY),
    )
    settings = replace(settings, secrets=secrets, sources=dict(sources))

    # ---------- 校验 ----------
    problems = validate_settings(settings)
    if problems:
        return _fail(problems, warnings)

    return Ok(settings, warnings=warnings)


def test_settings(**overrides: Any) -> AppSettings:
    """给测试用的**显式**配置：离线、无密钥、不碰用户目录。

    `overrides` 的键用**属性路径**：`mode` / `data_dir` / `budget.max_requests` …
    传入非法值会直接抛 `AssertionError`（这是测试代码写错，不是业务错误）。
    """
    base = AppSettings(
        mode=RunMode.TEST,
        log_level=LogLevel.WARNING,
        data_dir="var-test",
        provider_name="fake",
        offline_only=True,
    )
    for path, value in overrides.items():
        base = set_path(base, path, value)
    problems = validate_settings(base)
    if problems:
        raise AssertionError("测试配置本身不合法：" + "；".join(p.message for p in problems))
    return base


def default_settings() -> AppSettings:
    """不读任何外部输入，纯默认配置（用于对比与文档示例）。"""
    return AppSettings()


# ---------------------------------------------------------------------------
# 内部小工具
# ---------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    """去掉首尾空白；空串视为"未设置"。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _secret_from_env(environment: Mapping[str, str], primary_key: str) -> str | None:
    """读一个敏感变量：`PERSONA_*` 本体优先，其次按 `ENV_SECRET_ALIASES` 找别名。

    大小写**不敏感**（见 `_env_lookup_ci`）。这不是假想防御：2026-10-16 在本机
    实测到环境变量名被改写大小写的情况（注册表里存的是小写 `deepseek_api_key`），
    而密钥读不到时上层只报"未设置"，用户完全看不出是大小写问题。

    返回值**只进内存**，不落任何日志/文件/错误信息（D-078/D-081）；
    本函数也不校验格式（那是 `validate_settings` 的事，且它只报"哪个变量有问题"）。
    """
    value = _env_lookup_ci(environment, primary_key)
    if value is not None:
        return value
    for alias, target in ENV_SECRET_ALIASES.items():
        if target != primary_key:
            continue
        found = _env_lookup_ci(environment, alias)
        if found is not None:
            return found
    return None


def _env_lookup_ci(environment: Mapping[str, str], name: str) -> str | None:
    """大小写不敏感地取一个环境变量的值（取不到返回 `None`）。

    为什么需要：Windows 上 `os.environ` 会把键名规范成大写，本项目的变量又全是
    大写命名，所以默认路径精确取就能命中；但**显式传入的 env 字典**（测试）和
    非 Windows 平台上的用户环境可能是任意大小写。密钥这种"读不到只报未设置"
    的配置，一旦栽在大小写上极难排查，所以这里兜一次。

    优先级确定且可预期：**精确大小写命中 > 遍历中第一个忽略大小写的命中**。
    """
    direct = _clean(environment.get(name))
    if direct is not None:
        return direct
    wanted = name.upper()
    for key, value in environment.items():
        if key.upper() == wanted:
            found = _clean(value)
            if found is not None:
                return found
    return None


def _spec_of(env_key: str) -> str:
    spec = _SPEC_INDEX[env_key]
    return spec.path


_SPEC_INDEX: dict[str, FieldSpec] = {spec.env_key: spec for spec in CONFIG_FIELDS}


def _stringify(value: Any) -> str:
    """把 overrides 里的值转成字符串，走和环境变量完全相同的解析路径。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _apply_raw(settings: AppSettings, key: str, raw: str) -> tuple[AppSettings, AppError | None]:
    """把一条原始字符串应用到配置上，返回 `(新配置, 错误|None)`。

    出错时返回 `AppError`（`VALIDATION.config_invalid`），**信息里不含任何密钥值**。
    """
    spec = _SPEC_INDEX.get(key)
    if spec is None:  # pragma: no cover —— 调用方已过滤
        return settings, None
    value, reason = parse_value(spec, raw)
    if reason is not None:
        return settings, config_error(spec, reason)
    return set_path(settings, spec.path, _coerce_enum(spec, value)), None


def _coerce_enum(spec: FieldSpec, value: Any) -> Any:
    if spec.path == "mode" and not isinstance(value, RunMode):
        return RunMode(value)
    if spec.path == "log_level" and not isinstance(value, LogLevel):
        return LogLevel(value)
    return value


def _fail(problems, warnings) -> Err:
    """把校验问题变成 `Err`。**第一个**问题作主信息，全部问题放进 `details`。"""
    first = problems[0]
    merged = first.with_context(
        problem_count=len(problems),
        all_problems=[p.message for p in problems],
    )
    return Err(merged)


def describe_fields() -> list[dict[str, Any]]:
    """列出所有非敏感配置项（供 `persona config show` 与文档使用）。"""
    return [
        {
            "env_key": spec.env_key,
            "path": spec.path,
            "kind": spec.kind,
            "default": spec.default,
            "choices": list(spec.choices or []),
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "help": spec.help,
        }
        for spec in CONFIG_FIELDS
    ]
