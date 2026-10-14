"""命令行入口（docs/INTERFACES.md §11）。

**职责**：解析命令、调用 `Orchestrator`、把状态讲成人话。
**非职责**（D-059）：⛔ **不绕过 `Orchestrator`** 直接驱动业务模块；⛔ 不写业务数据。

本阶段（P2.3）可用：`--help`、`--version`、`info`、`selftest`、`config`、**`task`（任务生命周期）**。
`task run` 用的是 **Mock 执行器**：它推进的是抽象"工作单元"，
**不是**真实的人物研究 / 证据提取 / 对话——那些业务尚未实现。
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

# INTERFACES §11.2 里列出的命令；**仍为占位**的部分（P2.3 只接通了任务生命周期）
PLACEHOLDER_COMMANDS = (
    "new",
    "chat",
    "sources",
    "memory",
    "eval",
)

# 已接通 `TaskService` 的命令（status/pause/resume/cancel 是 `task` 组的快捷方式）
TASK_ALIASES = ("status", "pause", "resume", "cancel")


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

    p_task = sub.add_parser("task", help="任务生命周期（创建 / 查询 / 推进 / 暂停 / 恢复 / 取消）")
    tsub = p_task.add_subparsers(dest="task_action", metavar="{create,show,list,...}")

    t_create = tsub.add_parser("create", help="建一个 PENDING 任务（不自动开跑）")
    t_create.add_argument("--project", default="prj_local", help="项目 ID（默认 prj_local）")
    t_create.add_argument("--type", default="RESEARCH", help="任务类型，见 info 输出")
    t_create.add_argument("--stages", default="RESEARCH,EVIDENCE_EXTRACT",
                          help="阶段序列，逗号分隔")
    t_create.add_argument("--key", default="", help="幂等键：同项目下重复提交会返回原任务")
    t_create.add_argument("--budget-requests", type=int, default=0,
                          help="调用次数上限（0 = 用配置默认值）")

    t_show = tsub.add_parser("show", help="查看任务状态与进度")
    t_show.add_argument("task_id")
    t_show.add_argument("--project", default="prj_local")

    t_list = tsub.add_parser("list", help="列出某项目下的任务")
    t_list.add_argument("--project", default="prj_local")

    t_start = tsub.add_parser("start", help="PENDING → RUNNING")
    t_start.add_argument("task_id")
    t_start.add_argument("--project", default="prj_local")

    t_run = tsub.add_parser(
        "run",
        help="用 **Mock 执行器**推进任务（只推抽象工作单元，不是真实业务，不联网）",
    )
    t_run.add_argument("task_id")
    t_run.add_argument("--project", default="prj_local")
    t_run.add_argument("--steps", type=int, default=50, help="最多推进多少步")

    t_pause = tsub.add_parser("pause", help="RUNNING → PAUSED（保留检查点，可续跑）")
    t_pause.add_argument("task_id")
    t_pause.add_argument("--project", default="prj_local")
    t_pause.add_argument("--reason", default="USER", choices=("USER", "BUDGET", "DEPENDENCY"))

    t_resume = tsub.add_parser("resume", help="PAUSED → RUNNING（必须有检查点）")
    t_resume.add_argument("task_id")
    t_resume.add_argument("--project", default="prj_local")

    t_cancel = tsub.add_parser("cancel", help="取消任务（**不可**续跑，与暂停不同）")
    t_cancel.add_argument("task_id")
    t_cancel.add_argument("--project", default="prj_local")

    t_sweep = tsub.add_parser("sweep", help="把上次中断的 RUNNING 任务安全回退为 PAUSED")
    t_sweep.add_argument("--project", default="prj_local")

    for name in TASK_ALIASES:
        help_text = {
            "status": "查看任务状态（`task show` 的快捷方式）",
            "pause": "暂停任务（`task pause` 的快捷方式）",
            "resume": "恢复暂停的任务（`task resume` 的快捷方式）",
            "cancel": "取消任务（`task cancel` 的快捷方式）",
        }[name]
        p = sub.add_parser(name, help=help_text)
        p.add_argument("task_id", nargs="?", default="")
        p.add_argument("--project", default="prj_local")
        if name == "pause":
            p.add_argument("--reason", default="USER", choices=("USER", "BUDGET", "DEPENDENCY"))

    for name in PLACEHOLDER_COMMANDS:
        help_text = {
            "new": "新建项目（尚未实现）",
            "chat": "运行多人对话（尚未实现）",
            "sources": "查看来源清单（可选追溯，尚未实现）",
            "memory": "查看或清理记忆（尚未实现）",
            "eval": "运行评测（尚未实现）",
        }[name]
        p = sub.add_parser(name, help=help_text)
        if name == "new":
            p.add_argument("--names", default="", help="两个或更多人物名，用逗号分隔")
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
    print("    persona.orchestrator 六态状态机 + 任务服务 + 预算闸门 + Mock 执行器（P2.3）\n"
          "                        + BusinessOrchestrator（业务编排：Research→Evidence→\n"
          "                        Persona→Relationship→Dialogue，复用既有任务能力）")
    print("    persona.modules      业务模块端口协议；ResearchService（资料搜集：检索计划/检索/")
    print("                        过滤排序/经 Evidence 登记来源；不写证据表）已实现、")
    print("                        EvidenceService（来源/事件/证据管理）、")
    print("                        PersonaService（人格档案：创建/版本/证据关联/闸门）、")
    print("                        RelationshipService（关系档案：双向视角/共同经历/群体互动）、")
    print("                        DialogueService（对话：会话/轮次/动态发言/模拟隔离，")
    print("                        已接入 Memory：按规则检索模拟记忆 + 显式授权才写入）、")
    print("                        MemoryService（长期记忆：写入/检索/去重/冲突/更新/过期/删除）、")
    print("                        EvaluationService（只读评测：INV-1~4 不变式/依赖检查/结构指标）已实现")
    print("    persona.cli          命令行入口（本命令）")
    print("  任务六态    : " + " / ".join(s.value for s in TaskStatus))
    print("  命名空间    : " + " / ".join(n.value for n in ProvenanceNamespace))
    print("  写入闸门    :")
    for kind, allowed in sorted(NAMESPACE_WRITE_GATE.items()):
        print(f"    {kind:<22} 仅允许 " + ", ".join(sorted(n.value for n in allowed)))
    print("  说明        : **真实检索接入**与 AI 蒸馏尚未实现；`task run` 推进的是"
          "抽象工作单元，不是真实业务；"
          "`ResearchService` 只经 `Provider` 抽象检索（当前全是 Mock），不接真实搜索 API；"
          "`BusinessOrchestrator` 已可一条命令跑完五阶段业务链，但**证据 claim 仍需调用方显式提供**"
          "（源→证据自动抽取未实现），故 AC-1/AC-2 仅为部分满足；"
          "对话/记忆只做本地基础能力，不调真实模型、不接入外网检索；"
          "对话与记忆已打通闭环，但**默认不把对话写入长期记忆**"
          "（须显式授权 + 显式候选）。`EvaluationService` 提供只读不变式与依赖检查。")


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


def _open_task_service(project_id: str) -> tuple[Any, Any] | None:
    """打开任务服务。**失败返回 None 并已打印原因**（不抛异常给命令层）。"""
    from ..core.result import error_of, is_ok, unwrap
    from ..config import load_settings
    from ..orchestrator import TaskService
    from ..storage.sqlite import SqliteStorage

    result = load_settings()
    if not is_ok(result):
        error = error_of(result)
        print(f"配置无法加载：{error}")
        print("  提示：先跑 `persona config check`，或参考 .env.example")
        return None
    settings = unwrap(result)
    storage = SqliteStorage(settings.ensure_data_dir())
    return TaskService(storage, settings), storage


def _ctx_for(project_id: str, task_id: str | None = None):
    from ..core.context import new_call_context

    return new_call_context(project_id=project_id, actor="cli", task_id=task_id)


def _print_task(record: Any) -> None:
    status = record.status.value
    extra = f"（原因：{record.paused_reason.value}）" if record.paused_reason else ""
    print(f"  任务        : {record.id}")
    print(f"  类型 / 项目  : {record.task_type.value} / {record.project_id}")
    print(f"  状态        : {status}{extra}")
    print(f"  阶段        : {record.current_stage}（已完成 "
          f"{len(record.completed_stages)}/{len(record.stage_sequence)}）")
    print(f"  进度        : {record.progress.items_done}/{record.progress.items_total}"
          f"  ({record.progress.percent}%)")
    print(f"  预算        : 已用 {record.budget_used.requests} 次调用"
          f"（上限 {record.budget_limit.max_requests}）")
    if record.checkpoint.resume_token:
        print(f"  检查点      : stage={record.checkpoint.stage or '-'} "
              f"cursor={record.checkpoint.cursor or '-'} "
              f"token={record.checkpoint.resume_token[:16]}…")
    else:
        print("  检查点      : 无")
    if record.last_error:
        print(f"  最近错误    : [{record.last_error.code}] {record.last_error.message}")


def _run_task_action(args: Any) -> int:
    """执行 `persona task <action>`。全部经 `TaskService`，CLI 不直接写业务表。"""
    from ..core.enums import PausedReason
    from ..core.result import error_of, is_err, is_ok, unwrap
    from ..orchestrator import BudgetSpec, MockUnitHandler, TaskDraft, TaskRunner

    opened = _open_task_service(args.project)
    if opened is None:
        return 1
    service, storage = opened
    action = getattr(args, "task_action", "")
    ctx = _ctx_for(args.project)

    try:
        if not is_ok(storage.init_schema(ctx)):
            print("数据库初始化失败")
            return 1

        if action == "create":
            stages = [s.strip() for s in str(args.stages).split(",") if s.strip()]
            budget = BudgetSpec(max_requests=args.budget_requests or None)
            outcome = service.create_task(
                ctx,
                TaskDraft(
                    task_type=args.type,
                    stage_sequence=stages,
                    idempotency_key=args.key or None,
                    budget_limit=budget if args.budget_requests else None,
                ),
            )
            if is_err(outcome):
                print(f"创建失败：{error_of(outcome)}")
                return 1
            record = unwrap(outcome)
            print("任务已创建（状态 PENDING，未自动开跑）")
            _print_task(record)
            print("  下一步      : persona task start <task_id> "
                  "（或 persona task run <task_id> 直接用 Mock 执行器推进）")
            return EXIT_OK

        if action == "list":
            outcome = service.list_tasks(ctx)
            if is_err(outcome):
                print(f"查询失败：{error_of(outcome)}")
                return 1
            items = unwrap(outcome).items
            if not items:
                print(f"项目 {args.project} 下还没有任务。")
                return EXIT_OK
            print(f"共 {len(items)} 个任务：")
            for record in items:
                print(f"  {record.id}  {record.status.value:<10}"
                      f"{record.task_type.value:<22}{record.progress.items_done}"
                      f"/{record.progress.items_total}")
            return EXIT_OK

        if action == "sweep":
            outcome = service.sweep_interrupted(ctx)
            if is_err(outcome):
                print(f"扫描失败：{error_of(outcome)}")
                return 1
            fixed = unwrap(outcome)
            print(f"已把 {len(fixed)} 个中断任务安全回退为 PAUSED(DEPENDENCY)：{fixed}")
            return EXIT_OK

        # 以下动作都需要 task_id
        task_id = getattr(args, "task_id", "")
        if not task_id:
            print("缺少 task_id。用法见 `persona task --help`")
            return 1

        if action == "show":
            outcome = service.get(ctx, task_id)
            if is_err(outcome):
                print(f"查询失败：{error_of(outcome)}")
                return 1
            _print_task(unwrap(outcome))
            return EXIT_OK

        if action == "start":
            outcome = service.start(ctx, task_id)
        elif action == "pause":
            outcome = service.pause(ctx, task_id, PausedReason(args.reason))
        elif action == "resume":
            outcome = service.resume(ctx, task_id)
        elif action == "cancel":
            outcome = service.cancel(ctx, task_id, "用户取消")
        elif action == "run":
            # 说明：这不是真实业务，只是把抽象工作单元往前推（D-093）
            runner = TaskRunner(service, MockUnitHandler())
            result = runner.run_to_completion(ctx, task_id, max_steps=int(args.steps))
            if is_err(result):
                print(f"推进失败：{error_of(result)}")
            outcome = service.get(ctx, task_id)
            if is_ok(outcome):
                record = unwrap(outcome)
                print("Mock 执行器推进结束（**不是**真实研究/蒸馏业务）")
                _print_task(record)
                if record.status.value == "PAUSED":
                    print("  提示        : 已达预算上限，进度已保存。"
                          "提高额度后可 resume 继续。")
            return 0 if is_ok(result) else 1
        else:
            print(f"未知的 task 动作：{action}")
            return 1

        if is_err(outcome):
            print(f"操作失败：{error_of(outcome)}")
            return 1
        print(f"任务状态已更新：{unwrap(outcome).status.value}")
        return EXIT_OK
    finally:
        storage.close()


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
    if args.command == "task":
        if not getattr(args, "task_action", ""):
            # 没有子动作时打印 task 的帮助（argparse 的 --help 会走 SystemExit）
            try:
                build_parser().parse_args(["task", "--help"])
            except SystemExit:
                pass
            return EXIT_OK
        return _run_task_action(args)

    # status / pause / resume / cancel 是 `task` 组的快捷方式（INTERFACES §11.2）
    if args.command in TASK_ALIASES:
        alias = {"status": "show", "pause": "pause", "resume": "resume", "cancel": "cancel"}
        return _run_task_action(
            argparse.Namespace(
                project=getattr(args, "project", "prj_local"),
                task_action=alias[args.command],
                task_id=getattr(args, "task_id", ""),
                reason=getattr(args, "reason", "USER"),
            )
        )

    print(f"命令 `persona {args.command}` 尚未实现。")
    print(f"当前阶段：{STAGE}")
    print("可用命令：persona info / persona selftest / persona config / persona task")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
