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

    p_model = sub.add_parser(
        "model", help="模型抽取（G-2）的配置概览与 dry-run（**不调真实模型、不花钱**）")
    p_model.add_argument(
        "action", nargs="?", default="plan", choices=("plan", "dryrun"),
        help="plan=打印装配后的决策快照；dryrun=打印将要发出的 ChatRequest（默认 plan）",
    )
    p_model.add_argument(
        "--no-dotenv", action="store_true", help="忽略 .env 文件，只看默认值与环境变量",
    )

    p_biz = sub.add_parser(
        "business", help="业务链（研究→证据→人物→关系→对话）的装配与运行")
    bsub = p_biz.add_subparsers(dest="business_action", metavar="{plan,run}")

    b_plan = bsub.add_parser(
        "plan", help="打印业务链装配快照（**离线**：不联网、不调模型、不花钱）")
    b_plan.add_argument(
        "--no-dotenv", action="store_true", help="忽略 .env 文件，只看默认值与环境变量")
    b_plan.add_argument(
        "--analyzer", choices=("rule", "off"), default=None,
        help="覆盖 relationship.analyzer 配置（默认跟随配置，即 rule）")

    b_run = bsub.add_parser(
        "run", help="跑一次五阶段业务链（需要已配置模型 Provider 且显式 --allow-real；"
                    "否则**拒绝**且零调用）")
    b_run.add_argument("--names", required=True, help="人物名，逗号分隔（1 个或更多）")
    b_run.add_argument(
        "--type", dest="entity_type", default="FICTIONAL_CHARACTER",
        choices=("FICTIONAL_CHARACTER", "REAL_PERSON"),
        help="人物类型（默认 FICTIONAL_CHARACTER；虚构角色必须给作品与适用版本）")
    b_run.add_argument("--work-title", default="", help="虚构角色所属作品名（--type 虚构时必填）")
    b_run.add_argument("--adaptation-version", default="",
                       help="虚构角色的适用版本（--type 虚构时必填，如『2020 动画改编版』）")
    b_run.add_argument("--project", default="prj_local", help="项目 ID（默认 prj_local）")
    b_run.add_argument("--task-id", default="", help="任务 ID（留空自动生成）")
    b_run.add_argument("--max-rounds", type=int, default=1, help="对话轮数（默认 1）")
    b_run.add_argument(
        "--material", dest="materials", action="append", default=[], metavar="PATH",
        help="本地材料文件（可重复）。给出时研究阶段**不做检索**，只用这些文件；"
             "文件必须通过正文质量门禁，不合格的会被列出且不进入材料流")
    b_run.add_argument(
        "--allow-real", dest="allow_real", action="store_true",
        help="显式授权真实外呼（构造真实模型 Provider）。**默认拒绝**："
             "配置里写了真实服务商也不代表本次运行被允许花钱")
    b_run.add_argument("--no-dotenv", action="store_true", help="忽略 .env 文件")
    b_run.add_argument("--analyzer", choices=("rule", "off"), default=None,
                       help="覆盖 relationship.analyzer 配置")

    p_usage = sub.add_parser(
        "usage", help="外部用量账本（人工补记；**永不发起外部调用**）")
    usub = p_usage.add_subparsers(dest="usage_action", metavar="{backfill}")

    u_back = usub.add_parser(
        "backfill",
        help="补记一笔**已经发生但没落库**的外部支出（必须写 reason 与 operator）")
    u_back.add_argument(
        "--key", required=True,
        help="幂等键：标识**这笔支出事件**本身（重复提交不会重复记账；建议用账单号/轮次名）")
    u_back.add_argument("--amount", required=True, type=float,
                        help="金额（正数，单位由 --currency 决定）")
    u_back.add_argument("--reason", required=True,
                        help="补记原因（写进 trace_ref，必须可追溯）")
    u_back.add_argument("--operator", required=True,
                        help="操作人（写进 trace_ref；**不允许匿名补记**）")
    u_back.add_argument("--currency", default="USD",
                        help="计量单位（默认 USD；本账不做汇率换算）")
    u_back.add_argument("--provider", default="manual", help="服务商（默认 manual）")
    u_back.add_argument("--operation", default="CHAT",
                        choices=("CHAT", "SEARCH", "EXTRACT"),
                        help="调用类型（默认 CHAT）")
    u_back.add_argument("--task-id", default="", help="归属任务（可选，供单任务上限累计）")
    u_back.add_argument("--occurred-at", default="",
                        help="发生时间（ISO8601；留空=现在）")
    u_back.add_argument("--estimated", action="store_true",
                        help="只记估算（写 cost_estimate，实际留空、状态 UNKNOWN）")
    u_back.add_argument("--project", default="prj_local", help="项目 ID（默认 prj_local）")
    u_back.add_argument("--no-dotenv", action="store_true", help="忽略 .env 文件")

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
          "                        Persona→Relationship→Dialogue，复用既有任务能力）\n"
          "                        + model_setup（P4.4-B：配置→模型抽取器装配、预算上限、\n"
          "                        gate 开关、dry-run；**不调真实模型**）")
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
          "模型抽取（G-2）的服务商、单价、预算与 gate 开关已可按配置装配，"
          "`persona model plan/dryrun` 可离线查看将要发出的请求，"
          "但**尚未授权真实模型调用**，真实模型抽取质量未经验证；"
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


def _load_settings_or_report(*, use_dotenv: bool):
    """加载配置；失败时打印人话并返回 None（**不回显任何密钥值**）。"""
    from ..config import load_settings
    from ..core.result import error_of, is_ok, unwrap

    result = load_settings(use_dotenv=use_dotenv)
    if not is_ok(result):
        error = error_of(result)
        print("配置校验：未通过")
        print(f"  错误码    : {error.code}")
        print(f"  说明      : {error.message}")
        extra = error.details.get("all_problems")
        if isinstance(extra, list) and len(extra) > 1:
            print(f"  其它问题（共 {len(extra)} 条）:")
            for message in extra[1:]:
                print(f"    - {message}")
        print("  提示      : 参考 .env.example，或 `persona config show` 看当前生效值")
        return None
    for note in result.warnings:
        print(f"  提示      : {note.message}")
    return unwrap(result)


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _print_model(action: str, *, use_dotenv: bool) -> int:
    """`persona model plan|dryrun`。

    **两条命令都不会发出任何请求**：`plan` 只读配置，`dryrun` 只构造请求对象。
    所以它们不需要密钥（密钥缺失时打印"未设置"，绝不回显内容）。
    """
    from ..config.spec import ENV_PROVIDER_API_KEY
    from ..orchestrator import build_dry_run_request, model_plan

    settings = _load_settings_or_report(use_dotenv=use_dotenv)
    if settings is None:
        return 1

    plan = model_plan(settings)
    if action == "plan":
        _print_model_plan(plan, ENV_PROVIDER_API_KEY)
        return EXIT_OK

    # ---- dryrun ----
    request, admitted, rejected = build_dry_run_request(settings)
    _print_model_plan(plan, ENV_PROVIDER_API_KEY, compact=True)
    print()
    print("dry-run：构造一次抽取请求（**没有发送任何请求，也没有产生费用**）")
    print(f"  目标模型        : {request.model_hint}")
    print(f"  response_format : {request.response_format}"
          f"（统一契约的『要结构化输出』；DeepSeekProvider 发出前映射为 json_object）")
    print(f"  max_output_tokens: {request.max_output_tokens}")
    print(f"  prompt_version  : {request.metadata.get('prompt_version')}")
    print(f"  通过准入的材料  : {len(admitted)} 份"
          f"{f'，被门禁挡下 {len(rejected)} 份' if rejected else ''}")
    if rejected:
        for item in rejected:
            print(f"    - [{item.reason}] {item.message}")
    if not admitted:
        print("  ⚠️ 没有任何材料通过准入：真实运行时也**不会**发起请求。")
        return EXIT_OK
    for index, message in enumerate(request.messages):
        label = "system（规则）" if message.role == "system" else \
            "user（数据；材料已被标记块包裹）"
        print(f"--- [{index}] {message.role} —— {label} ---")
        print(message.content)
        print()
    print("说明：内容是 dry-run **合成样张**（不是任何真实人物/事件）；"
          "真实材料必须由研究阶段给出正文质量门禁结论才能通过准入。")
    print("本命令全程离线：不联网、不调模型、不记账。")
    return EXIT_OK


def _print_model_plan(plan: dict, key_env: str, *, compact: bool = False) -> None:
    """打印装配后的决策快照。**只显示密钥"是否设置"，不显示内容**（D-081）。"""
    def money(value: Any) -> str:
        return "未设置" if value is None else str(value)

    per_call = plan.get("est_cost_per_call")

    print("模型抽取决策快照（P4.4-B / P4.4-C 拍板）")
    print(f"  是否启用        : {_yes_no(plan['enabled'])}"
          f"（PERSONA_MODEL_PROVIDER={plan['provider'] or '未设置'}）")
    print(f"  服务商 / 模型   : {plan['provider'] or '—'} / {plan['model_id']}")
    print(f"  币种            : {plan['currency']}（固定 USD，不做汇率换算）")
    print(f"  价格档位        : {plan['price_tier']}")
    print(f"  单价（原币/1M） : 输入 {money(plan['price_per_1m_in'])}"
          f"  输出 {money(plan['price_per_1m_out'])}")
    print(f"  单价（账本/1K） : 输入 {money(plan['price_per_1k_in'])}"
          f"  输出 {money(plan['price_per_1k_out'])}")
    print(f"  单价来源        : {plan['price_source'] or '未记录'}"
          f"（核对日期 {plan['price_checked_at'] or '未记录'}）")
    is_set = plan["secret_status"].get(key_env)
    print(f"  模型密钥        : {key_env} "
          f"{'已设置（内容不显示）' if is_set else '未设置'}")
    if compact:
        return
    print(f"  预算            : 总上限 {money(plan['max_cost_total'])}"
          f" | 单任务上限 {money(plan['max_cost_per_task'])}"
          f" | **生效上限 {money(plan['max_cost_effective'])}** {plan['currency']}")
    print(f"  调用次数上限    : {money(plan['max_calls'])}")
    print(f"  事前 token 估算 : 输入 {plan['est_tokens_in']} / 输出 {plan['est_tokens_out']}")
    print(f"  单次调用估算    : {money(per_call)} {plan['currency']}")
    print(f"  gate 开关       : require_gate_verdict="
          f"{_yes_no(plan['require_gate_verdict'])}"
          f"（材料必须亮出正文质量门禁结论才允许进抽取器）")
    for note in plan.get("notes", []):
        print(f"  注意            : {note}")


def _run_business_action(args: Any) -> int:
    """`persona business plan|run`（P4.6 / D-A：业务链进 CLI）。

    两条命令**都经同一个装配层**（`build_business_stack`），CLI 自己不构造业务服务——
    否则 CLI 就成了第二份 composition root，正是 G4 那类"脚本代劳掩盖缺陷"。
    """
    action = getattr(args, "business_action", "")
    settings = _load_settings_or_report(
        use_dotenv=not getattr(args, "no_dotenv", False))
    if settings is None:
        return 1

    from ..core.context import new_call_context
    from ..core.result import error_of, is_ok, unwrap
    from ..orchestrator import StackOptions, build_business_stack
    from ..storage.sqlite import SqliteStorage

    project = getattr(args, "project", "prj_local")
    task_id = getattr(args, "task_id", "") or None
    analyzer = getattr(args, "analyzer", None)
    storage = SqliteStorage(settings.ensure_data_dir())
    ctx = new_call_context(project_id=project, actor="cli", task_id=task_id)
    try:
        built = build_business_stack(
            settings, ctx,
            storage=storage,
            options=StackOptions(
                task_id=task_id,
                # `run` 才按配置构造真实 Provider（只构造、不发请求）；
                # `plan` 必须保持"什么都不建"，否则快照命令也可能踩到密钥/网络配置。
                build_chat_provider=(action == "run"),
                enable_relationship_analyzer=(None if analyzer is None
                                              else analyzer == "rule"),
            ),
            # P4.7 整改：默认禁止真实外呼。配置允许联网 ≠ 本次运行被授权花钱，
            # 必须显式 `--allow-real`（2026-10-17 那次未授权调用就出在这里）。
            allow_real=bool(getattr(args, "allow_real", False)),
        )
        if not is_ok(built):
            print(f"装配失败：{error_of(built)}")
            return 1
        stack = unwrap(built)
        if action == "plan":
            return _print_business_plan(stack)
        return _run_business_chain(stack, args)
    finally:
        storage.close()


def _print_business_plan(stack: Any) -> int:
    """打印装配快照。**全程离线：不联网、不调模型、不记账。**"""
    facts = stack.describe()
    components = facts["components"]
    model = facts["model"]
    ledger = facts["ledger"]

    print("业务链装配快照（**离线**：不联网、不调模型、不产生费用）")
    print(f"  数据库          : {stack.settings.database_path()}")
    print(f"  项目 / 任务     : {stack.ctx.project_id} / {stack.ctx.task_id or '—'}")
    print("  组件            :")
    for key in ("evidence", "persona", "relationship", "dialogue", "research",
                "task_service"):
        print(f"    {key:<14}: {components.get(key)}")
    extractor = components.get("claim_extractor")
    print(f"  claim 抽取器    : {extractor or '未装配（未配置模型 Provider）'}")
    print(f"  关系分析器      : {components.get('relationship_analyzer') or '未装配'}")

    def money(value: Any) -> str:
        return "不限制" if value is None else f"{value} {model['currency']}"

    print(f"  单任务费用上限  : {money(model['max_cost_per_task'])}"
          f"（按 task_id 累计，含同任务历史）")
    print(f"  项目累计上限    : {money(model['max_cost_total'])}"
          f"（整本账累计，含 hydrate 读回的历史）")
    print(f"  调用次数上限    : {model['max_calls'] if model['max_calls'] is not None else '不限制'}")
    per_call = model["est_cost_per_call"]
    currency = model["currency"]
    if per_call is None:
        print("  单次调用估算    : 估不出来（缺单价或 token 估算）")
    else:
        print(f"  单次调用估算    : {per_call:.6f} {currency}")

    history = ledger.get("history") or {}
    if ledger.get("hydrated"):
        print(f"  账本（历史）    : 已读 {history.get('loaded', 0)} 笔，"
              f"占用 {ledger.get('occupied_total', 0.0):.6f} {model['currency']}"
              f"（未知 {history.get('unknown_requests', 0)} 笔、"
              f"无法估算 {ledger.get('unpriced_unknown', 0)} 笔）")
    else:
        print("  账本（历史）    : 未 hydrate（项目累计上限只统计本次进程）")
    print(f"  账本（本次）    : 占用 {ledger.get('occupied_session', 0.0):.6f}"
          f" {model['currency']}")
    if ledger.get("currency_invariant") == "MIXED":
        print("  ⚠️ 计量单位混用 : 历史记录里出现多种 currency，累计比较的前提不成立")
    codes = facts.get("warnings") or []
    print(f"  装配告警        : {', '.join(codes) if codes else '无'}")
    print("说明：本命令不发起任何外部请求；真实运行（含检索与模型调用）需另行授权。")
    return EXIT_OK


def _run_business_chain(stack: Any, args: Any) -> int:
    """跑一次五阶段业务链。**未配置抽取器时在发请求之前拒绝**（不白花钱）。"""
    from ..core.result import error_of, is_ok, unwrap
    from ..orchestrator import (
        BusinessRequest,
        TargetSpec,
        materials_from_files,
        register_targets,
    )

    names = [str(n).strip() for n in str(getattr(args, "names", "")).split(",")]
    names = [n for n in names if n]
    if not names:
        print("缺少 --names：至少给一个人物名（逗号分隔）")
        return 1

    entity_type = str(getattr(args, "entity_type", "FICTIONAL_CHARACTER"))
    work_title = str(getattr(args, "work_title", "") or "").strip()
    adaptation = str(getattr(args, "adaptation_version", "") or "").strip()
    if entity_type == "FICTIONAL_CHARACTER" and not (work_title and adaptation):
        print("虚构角色必须声明所属作品与适用版本（PF-V5 / D-115）：")
        print("  请补 --work-title 与 --adaptation-version；或改用 --type REAL_PERSON")
        return 1

    if stack.claim_extractor is None:
        print("拒绝运行：没有可用的 claim 抽取器。")
        blocked = False
        for note in getattr(stack, "warnings", []):
            if note.code in ("CHAT_PROVIDER_UNAVAILABLE", "EXTRACTOR_ABSENT",
                             "REAL_PROVIDER_BLOCKED"):
                print(f"  装配告警：{note.message}")
                blocked = blocked or note.code == "REAL_PROVIDER_BLOCKED"
        if blocked:
            print("  原因：**默认禁止真实外呼**——配置里有真实服务商，但本次没显式授权。")
            print("  授权方式：加 `--allow-real`（只授权本次进程；不会自动重跑）。")
        else:
            print("  原因：未配置模型 Provider（PERSONA_MODEL_PROVIDER）或未设置密钥。")
        print("  影响：研究材料不会变成候选 claim，跑下去只会产生空档案（等于白跑）。")
        print("  注意：`--material` 只解决**材料从哪来**，不替代模型——"
              "没有抽取器时本地材料同样变不成 claim。")
        print("  建议：先 `persona business plan` 看装配快照；"
              "配置模型后用 `persona model plan` 复核单价与上限。")
        print("  说明：本次**没有发起任何请求，也没有产生费用**（真实调用需单独授权）。")
        return EXIT_NOT_IMPLEMENTED

    specs = [
        TargetSpec(display_name=name, entity_type=entity_type,
                   work_title=work_title, adaptation_version=adaptation)
        for name in dict.fromkeys(names)          # 去重且保序
    ]
    registered = register_targets(stack.storage, stack.ctx, specs)
    if not is_ok(registered):
        print(f"登记人物失败：{error_of(registered)}")
        return 1
    targets = unwrap(registered)

    # —— 本地材料（P4.7 离线通道）：给了 `--material` 就不做检索 ——
    materials: list = []
    material_paths = list(getattr(args, "materials", []) or [])
    if material_paths:
        materials, material_warnings = materials_from_files(
            material_paths,
            target_entity_ids=[t.entity_id for t in targets],
            target_names=[t.display_name for t in targets],
        )
        print(f"本地材料        : 提供 {len(material_paths)} 份，"
              f"可用 {len(materials)} 份（已过正文质量门禁）")
        for note in material_warnings:
            print(f"      ⚠️ [{note.code}] {note.message}")
        if not materials:
            print("拒绝运行：没有任何本地材料通过正文质量门禁。")
            print("  原因：不合格的正文不能用来生成 claim"
                  "（否则等于拿不可用材料伪造档案）。")
            print("  建议：改成成段的中文/英文正文（每段 ≥40 字，总计 ≥200 字）。")
            print("  说明：本次**没有发起任何请求，也没有产生费用**。")
            return 1

    print(f"业务链开跑：{len(targets)} 名目标（{entity_type}）"
          f"{'，本地材料 ' + str(len(materials)) + ' 份（不检索）' if materials else ''}")
    result = stack.orchestrator.run(
        stack.ctx, BusinessRequest(targets=targets, materials=materials,
                                   max_rounds=int(getattr(args, "max_rounds", 1) or 1)))
    if not is_ok(result):
        print(f"运行失败：{error_of(result)}")
        return 1
    run = unwrap(result)
    print(f"  结论            : {run.status}（任务 {run.task_id} / {run.task_status}）")
    for step in run.steps:
        print(f"  - {step.name:<22}{step.status:<16}{step.message}")
        for note in step.warnings:
            print(f"      ⚠️ [{note.code}] {note.message}")
    print(f"  来源 / 证据     : {len(run.source_ids)} / {len(run.evidence_ids)}")
    print(f"  人物 / 关系档案 : {len(run.persona_profile_ids)} / "
          f"{len(run.relationship_profile_ids)}")
    if run.error is not None:
        print(f"  错误            : [{run.error.code}] {run.error.message}")
    return EXIT_OK if run.is_complete() else 1


def _run_usage_action(args: Any) -> int:
    """`persona usage backfill`（P4.7）：把**已经花掉但没落库**的钱补进账本。

    为什么不许匿名补记：账本是费用闸门的唯一依据，一条来源不明的金额既不能
    复核也无法追责。所以 `--reason` 与 `--operator` 都是必填，且都写进
    `trace_ref`（`manual-backfill:<operator>:<reason>`），可反查到人。

    幂等键**只标识这笔支出事件本身**（`manual-backfill:<key>`），不含 operator：
    同一个账单被两个人各补一次，是比"键冲突"严重得多的错误。

    `--estimated` 用于"只知道估算、还没拿到账单"的场合：写 `cost_estimate`，
    实际值与状态留 `UNKNOWN`（D-C：未知按保守金额计入累计，绝不填 0）。

    本命令**不发起任何外部调用**，也不改业务表。
    """
    from ..core.context import new_call_context
    from ..core.result import error_of, is_ok, unwrap
    from ..core.temporal import to_iso, utc_now
    from ..core.types import (
        COST_CONFIRMED,
        COST_UNKNOWN,
        OUTCOME_SUCCEEDED,
        SOURCE_NONE,
        SOURCE_OFFICIAL_RATE,
        ExternalUsageRecord,
    )
    from ..storage.sqlite import SqliteStorage

    if getattr(args, "usage_action", "") != "backfill":
        try:
            build_parser().parse_args(["usage", "--help"])
        except SystemExit:
            pass
        return EXIT_OK

    key = str(getattr(args, "key", "") or "").strip()
    reason = str(getattr(args, "reason", "") or "").strip()
    operator = str(getattr(args, "operator", "") or "").strip()
    amount = float(getattr(args, "amount", 0.0) or 0.0)

    problems: list[str] = []
    if not operator:
        problems.append("--operator 不能为空：补记必须能追溯到人（不允许匿名补记）")
    if not reason:
        problems.append("--reason 不能为空：补记必须写明原因")
    if not key:
        problems.append("--key 不能为空：补记必须能被幂等识别")
    elif any(ch.isspace() for ch in key):
        problems.append("--key 不能含空白：它会被拼进幂等键")
    if not amount > 0:
        problems.append("--amount 必须是正数（0 或负数不是一笔支出）")
    if problems:
        for item in problems:
            print(f"参数有问题：{item}")
        print("本次**没有写任何记录**。用法见 `persona usage backfill --help`。")
        return 1

    settings = _load_settings_or_report(
        use_dotenv=not getattr(args, "no_dotenv", False))
    if settings is None:
        return 1

    estimated = bool(getattr(args, "estimated", False))
    currency = str(getattr(args, "currency", "USD") or "USD").strip().upper()
    task_id = str(getattr(args, "task_id", "") or "").strip() or None
    occurred_at = str(getattr(args, "occurred_at", "") or "").strip() or to_iso(utc_now())
    record = ExternalUsageRecord(
        idempotency_key=f"manual-backfill:{key}",
        provider=str(getattr(args, "provider", "manual") or "manual").strip() or "manual",
        operation=str(getattr(args, "operation", "CHAT") or "CHAT").strip().upper(),
        outcome=OUTCOME_SUCCEEDED,
        requested_at=occurred_at,
        unit_count=1,
        success_unit_count=1,
        cost_estimate=amount,
        # `--estimated` 时**故意**不写实际值：估算不等于账单（D-060 / 账本三条硬规矩）
        cost_confirmed=None if estimated else amount,
        currency=currency,
        cost_source=SOURCE_NONE if estimated else SOURCE_OFFICIAL_RATE,
        cost_status=COST_UNKNOWN if estimated else COST_CONFIRMED,
        task_id=task_id,
        run_id="usage-backfill",
        trace_ref=f"manual-backfill:{operator}:{reason}",
    )

    project = str(getattr(args, "project", "prj_local") or "prj_local")
    storage = SqliteStorage(settings.ensure_data_dir())
    ctx = new_call_context(project_id=project, actor="cli", task_id=task_id)
    try:
        if not is_ok(storage.init_schema(ctx)):
            print("数据库初始化失败")
            return 1
        written = storage.record_external_usage(ctx, record)
        if not is_ok(written):
            print(f"补记失败：{error_of(written)}")
            return 1
        rows = storage.external_usage_records(ctx)
        if not is_ok(rows):
            print(f"补记已提交，但读回账本失败：{error_of(rows)}")
            return 1
        collected = list(rows.value)
        confirmed = sum(float(r.cost_confirmed or 0.0) for r in collected)
        estimated_total = sum(float(r.cost_estimate or 0.0) for r in collected)

        print("用量补记：" + ("已写入账本" if unwrap(written) else
                          "幂等键已存在，**未重复记账**"))
        print(f"  数据库        : {settings.database_path()}")
        print(f"  幂等键        : {record.idempotency_key}")
        print(f"  金额          : {amount:.7f} {currency}"
              f"（{'估算（实际留空）' if estimated else '实际'}）")
        print(f"  原因 / 操作者  : {reason} / {operator}")
        print(f"  发生时间      : {occurred_at}")
        print(f"  账本合计      : {len(collected)} 笔，"
              f"实际 {confirmed:.7f}，估算 {estimated_total:.7f}（{currency}）")
        print("说明：本命令**不发起任何外部调用**；只往本地账本补一笔可追溯的记录。")
        return EXIT_OK
    finally:
        storage.close()


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
    if args.command == "model":
        return _print_model(
            getattr(args, "action", "plan"),
            use_dotenv=not getattr(args, "no_dotenv", False),
        )
    if args.command == "business":
        if not getattr(args, "business_action", ""):
            try:
                build_parser().parse_args(["business", "--help"])
            except SystemExit:
                pass
            return EXIT_OK
        return _run_business_action(args)
    if args.command == "usage":
        return _run_usage_action(args)
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
    print("可用命令：persona info / persona selftest / persona config / persona model / "
          "persona business / persona usage / persona task")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
