"""命令行入口（docs/INTERFACES.md §11）。

**职责**：解析命令、调用 `Orchestrator`、把状态讲成人话。
**非职责**（D-059）：⛔ **不绕过 `Orchestrator`** 直接驱动业务模块；⛔ 不写业务数据。

本阶段（P1.2-B）只有：`--help`、`--version`、`info`、`selftest`。
其余业务命令**保留占位**，执行时明确提示"尚未实现"，**不会假装能用**。

输出约定：面向非技术用户——一句话原因 + 一句话建议 + `trace_ref`，不堆堆栈。
"""

from __future__ import annotations

import argparse
import platform
import sys
from typing import Sequence

from .. import STAGE, __version__
from ..core.enums import NAMESPACE_WRITE_GATE, ProvenanceNamespace, TaskStatus

EXIT_OK = 0
EXIT_NOT_IMPLEMENTED = 2

# INTERFACES §11.2 里列出的命令；本阶段全部为占位
PLACEHOLDER_COMMANDS = (
    "new",
    "status",
    "resume",
    "pause",
    "cancel",
    "chat",
    "sources",
    "memory",
    "eval",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="persona",
        description="generic-persona-skill —— 通用人物与关系模拟（本地 CLI）",
        epilog="当前阶段：" + STAGE,
    )
    parser.add_argument("--version", action="version", version=f"persona {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{info,selftest,new,status,...}")

    sub.add_parser("info", help="显示当前阶段、Python 版本与包结构")
    sub.add_parser("selftest", help="运行一次最小自检（临时 SQLite + Mock Provider，全程离线）")

    p_config = sub.add_parser("config", help="查看或校验配置（**永不显示密钥值**）")
    p_config.add_argument(
        "action", nargs="?", default="show", choices=("show", "check"),
        help="show=打印生效的配置与来源；check=只做校验（默认 show）",
    )
    p_config.add_argument(
        "--no-dotenv", action="store_true", help="忽略 .env 文件，只看默认值与环境变量",
    )

    for name in PLACEHOLDER_COMMANDS:
        help_text = {
            "new": "新建项目（尚未实现）",
            "status": "查看任务状态（尚未实现）",
            "resume": "恢复暂停的任务（尚未实现）",
            "pause": "暂停任务（尚未实现）",
            "cancel": "取消任务（尚未实现）",
            "chat": "运行多人对话（尚未实现）",
            "sources": "查看来源清单（可选追溯，尚未实现）",
            "memory": "查看或清理记忆（尚未实现）",
            "eval": "运行评测（尚未实现）",
        }[name]
        p = sub.add_parser(name, help=help_text)
        if name == "new":
            p.add_argument("--names", default="", help="两个或更多人物名，用逗号分隔")
        if name == "status" or name == "resume" or name == "pause" or name == "cancel":
            p.add_argument("task_id", nargs="?", default="")
        if name in ("chat", "sources", "memory"):
            p.add_argument("project_id", nargs="?", default="")
        if name == "chat":
            p.add_argument("--rounds", type=int, default=0)

    return parser


# ---------------------------------------------------------------------------


def _print_info() -> None:
    print("generic-persona-skill")
    print(f"  版本        : {__version__}")
    print(f"  当前阶段    : {STAGE}")
    print(f"  Python      : {platform.python_version()} ({platform.system()} {platform.machine()})")
    print("  包结构      :")
    print("    persona.core         公共契约（枚举 / ID / 时间 / 错误码 / Result / 上下文）")
    print("    persona.storage      SQLite 最小读写（不做业务判断）")
    print("    persona.providers    外部服务统一接口 + Mock（全程离线，不需要 API Key）")
    print("    persona.orchestrator 六态任务状态机 + 检查点（尚无任务引擎）")
    print("    persona.modules      各业务模块的端口协议（只声明，不实现）")
    print("    persona.cli          命令行入口（本命令）")
    print("  任务六态    : " + " / ".join(s.value for s in TaskStatus))
    print("  命名空间    : " + " / ".join(n.value for n in ProvenanceNamespace))
    print("  写入闸门    :")
    for kind, allowed in sorted(NAMESPACE_WRITE_GATE.items()):
        print(f"    {kind:<22} 仅允许 " + ", ".join(sorted(n.value for n in allowed)))
    print("  说明        : 本阶段只有骨架，研究/蒸馏/对话/记忆等业务逻辑**尚未实现**。")


def _print_selftest() -> int:
    """最小自检：临时 SQLite 读写 + Mock Provider 调用，**不联网、不写项目数据**。

    注意：这只是一次"骨架能不能跑"的冒烟检查，走的是临时文件数据库，
    与 `persona eval`（正式评测，尚未实现）不是一回事。
    """
    import tempfile
    from pathlib import Path

    from ..core.context import new_call_context
    from ..core.enums import ProvenanceNamespace
    from ..core.result import is_ok, unwrap
    from ..providers.base import ChatRequest, Message
    from ..providers.mock import FakeProvider
    from ..storage.sqlite import SqliteStorage

    print("自检：临时 SQLite + Mock Provider（离线）")
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteStorage(Path(tmp) / "selftest.db")
        ctx = new_call_context(project_id="prj_selftest", actor="cli")
        store.init_schema(ctx)

        rid = unwrap(
            store.put(
                ctx,
                "PersonEntity",
                {"display_name": "自检对象", "entity_type": "FICTIONAL_CHARACTER"},
                project_id="prj_selftest",
                namespace=ProvenanceNamespace.PUBLIC_SOURCE,
            )
        )
        got = unwrap(store.get(ctx, "PersonEntity", rid))
        print(f"  SQLite 读写   : OK（写入 {rid}，读回 {got['display_name']!r}）")

        # 隔离红线：证据不允许 SIMULATED（D-023）
        blocked = store.put(
            ctx,
            "EvidenceItem",
            {"claim": "模拟内容不该进证据表"},
            project_id="prj_selftest",
            namespace=ProvenanceNamespace.SIMULATED,
        )
        print(f"  隔离闸门      : {'OK（已拦截）' if not is_ok(blocked) else '失败（未拦截！）'}")

        provider = FakeProvider(fixtures={"chat:ping": "pong"})
        resp = provider.chat(
            ctx,
            ChatRequest(messages=[Message(role="user", content="ping")],
                        metadata={"fixture_key": "ping"}),
        )
        print(f"  Mock Provider : {'OK（' + unwrap(resp).text + '）' if is_ok(resp) else '失败'}")
        store.close()

    ok = is_ok(blocked) is False and is_ok(resp)
    print("自检结论        : " + ("通过" if ok else "未通过"))
    return EXIT_OK if ok else 1


def _print_config(action: str, *, use_dotenv: bool) -> int:
    """打印或校验配置。**任何情况下都不打印密钥值**（D-081）。"""
    from ..core.result import is_ok, unwrap
    from ..config import describe_fields, get_path, load_settings
    from ..config.spec import SECRET_ENV_KEYS

    result = load_settings(use_dotenv=use_dotenv)
    if not is_ok(result):
        from ..core.result import error_of

        error = error_of(result)
        print("配置校验：未通过")
        print(f"  错误码    : {error.code}")
        print(f"  说明      : {error.message}")
        extra = error.details.get("all_problems")
        if isinstance(extra, list) and len(extra) > 1:
            print(f"  其它问题（共 {len(extra)} 条）:")
            for message in extra[1:]:
                print(f"    - {message}")
        print("  提示      : 用 `persona config show` 看当前生效值，或参考 .env.example")
        return 1

    settings = unwrap(result)
    if action == "check":
        print("配置校验：通过")
        for note in result.warnings:
            print(f"  提示      : {note.message}")
        return EXIT_OK

    print("当前生效的配置（来源：default / dotenv / env / explicit）")
    print(f"  {'环境变量':<38}{'字段':<28}{'当前值':<22}来源")
    for field in describe_fields():
        value = get_path(settings, field["path"])
        text = value.value if hasattr(value, "value") else str(value)
        source = settings.sources.get(field["path"], "default")
        print(f"  {field['env_key']:<38}{field['path']:<28}{text:<22}{source}")

    print("  敏感配置（只显示是否设置，**不显示内容**）")
    for env_key, is_set in settings.secrets.status().items():
        print(f"  {env_key:<38}{'—':<28}{'已设置' if is_set else '未设置':<22}env")
    print(f"  说明      : 敏感变量 {', '.join(SECRET_ENV_KEYS)} 只从环境变量读取，"
          f".env 中出现会被忽略并告警。")
    for note in result.warnings:
        print(f"  提示      : {note.message}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command is None:
        parser.print_help()
        return EXIT_OK
    if args.command == "info":
        _print_info()
        return EXIT_OK
    if args.command == "selftest":
        return _print_selftest()
    if args.command == "config":
        return _print_config(
            getattr(args, "action", "show"),
            use_dotenv=not getattr(args, "no_dotenv", False),
        )

    print(f"命令 `persona {args.command}` 尚未实现。")
    print(f"当前阶段：{STAGE}")
    print("可用命令：persona info / persona selftest")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
