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
#: Ctrl-C 干净中止（P6.2-c）。用 130 而不是 0：轮次可能只跑了一半，
#: "成功退出"会把它伪装成一次完整运行；130 与 shell 对 SIGINT 的惯例一致。
EXIT_INTERRUPTED = 130

#: 省略 `--adaptation` 时的取值。**取默认值不是问题，悄悄取才是**——
#: 清单里必须标注"（未指定，默认）"（P5.1 §3.1.2）。
DEFAULT_ADAPTATION = "原著"

#: 单任务预算默认值（USD）。与 `.env.example` 的 `PERSONA_MODEL_MAX_COST_PER_TASK` 一致。
DEFAULT_BUDGET_USD = 0.25

#: `persona chat --memory-write` 的可选值。**产品面只暴露两个**：
#: `off`（默认，一条都不写）与 `rule`（按确定性规则从**用户原话**派生候选）。
#: 不暴露 `explicit`——命令行没有"调用方显式给候选"这条路径，给了也用不上，
#: 只会被误当成"另一种自动写"。
CHAT_MEMORY_WRITE_CHOICES = ("rule", "off")

# INTERFACES §11.2 里列出的命令；**仍为占位**的部分（P2.3 只接通了任务生命周期）
# `chat` 已于 P6.2-c 接通（docs/RUNNING.md §5.41），从这里移出。
PLACEHOLDER_COMMANDS = (
    "new",
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

    p_run = sub.add_parser(
        "run",
        help="研究并生成人物档案与关系档案（真实检索 + 模型；第一次会要求确认一次）")
    p_run.add_argument("--names", required=True, help="人物名，逗号分隔（1 个或更多）")
    p_run.add_argument("--work-title", default="", help="虚构角色所属作品（虚构角色必填）")
    p_run.add_argument(
        "--adaptation", default="",
        help="适用改编版本。省略时取「原著」，清单会标注是显式指定还是默认值")
    p_run.add_argument(
        "--type", dest="entity_type", default="FICTIONAL_CHARACTER",
        choices=("FICTIONAL_CHARACTER", "REAL_PERSON"),
        help="人物类型（默认 FICTIONAL_CHARACTER）")
    p_run.add_argument(
        "--vocabulary", default="l1",
        help="特征词表：l1（默认）/ l2（= l2-zh-classical，中文古典层 42 个标签）")
    p_run.add_argument(
        "--budget", type=float, default=0.25,
        help="单次任务最多花多少美元（默认 0.25）。项目累计上限由配置控制，两者取严。")
    p_run.add_argument(
        "--extract-temperature", dest="extract_temperature", type=float, default=None,
        help="抽取温度（默认 0.7 = 生产值）。99%% 的用户不需要改这个。"
             "设 0 可复现同一批材料的同一份结果，但会降低多样性。")
    p_run.add_argument("--out", default="", help="数据目录（默认取配置 PERSONA_DATA_DIR）")
    p_run.add_argument("--project", default="prj_local", help="项目 ID（默认 prj_local）")
    p_run.add_argument(
        "--dry", action="store_true",
        help="只打印清单：不执行、不落库、不花钱、不询问")
    p_run.add_argument(
        "--yes", action="store_true",
        help="非交互环境的显式确认（TTY 下第一次运行只问一次，之后记住）")
    p_run.add_argument("--no-dotenv", action="store_true", help="忽略 .env 文件")

    p_chat = sub.add_parser(
        "chat",
        help="和库里已有档案的人物对话（真实模型；第一次会要求确认一次）")
    p_chat.add_argument(
        "--names", required=True,
        help="人物名，逗号分隔（**至少 2 人**：对话要轮换发言，一个人开不了会话）")
    p_chat.add_argument(
        "--rounds", type=int, default=1,
        help="最多跑几轮（默认 1）。每轮 = 1 次模型调用；轮数就是调用数")
    p_chat.add_argument(
        "--message", default="",
        help="只发这一句（给了就不进交互模式）。缺省时逐行读你的输入，"
             "空行忽略、/exit 或 Ctrl-D 结束、Ctrl-C 干净中止")
    p_chat.add_argument(
        "--memory-write", dest="memory_write", default="off",
        choices=CHAT_MEMORY_WRITE_CHOICES,
        help="off（默认）：一条记忆都不写。rule：把**你原话**里值得记的片段写成"
             "**会话级模拟记忆**（确定性规则，零额外调用；不记模型说过的话）")
    p_chat.add_argument(
        "--topic", default="",
        help="跨会话检索的话题（不给 = **不跨会话取记忆**，与 P6.2 一致）。"
             "给了 topic，才会去库里找**同一批参与者**、内容命中该话题的"
             "会话级模拟记忆并注入上下文；至少 2 个字、不能全是停用词"
             "（「我」「这个」这类会被拦——一个字就能命中库里每条记忆）")
    p_chat.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET_USD,
        help=f"本次最多花多少美元（默认 {DEFAULT_BUDGET_USD}）")
    p_chat.add_argument("--db", default="", help="数据库路径（默认取配置）")
    p_chat.add_argument("--project", default="prj_local", help="项目 ID（默认 prj_local）")
    p_chat.add_argument(
        "--yes", action="store_true",
        help="非交互环境的显式确认（TTY 下第一次运行只问一次，之后记住）")
    p_chat.add_argument("--no-dotenv", action="store_true", help="忽略 .env 文件")

    p_show = sub.add_parser(
        "show", help="查看人物档案（稳定性格 / 习惯 / 说话方式 / 单次行为 / 背景 / 经历）")
    p_show.add_argument("name", help="人物名，或 pe_ 开头的实体 ID")
    p_show.add_argument("--db", default="", help="数据库路径（默认取配置）")
    p_show.add_argument("--project", default="prj_local")
    p_show.add_argument("--version", type=int, default=None, help="档案版本（默认最新）")
    p_show.add_argument("--no-dotenv", action="store_true")

    p_rel = sub.add_parser("show-relationship", help="查看两人关系档案（双向视角）")
    p_rel.add_argument("names", help="两个人物名，逗号分隔，如 林黛玉,贾宝玉")
    p_rel.add_argument("--db", default="", help="数据库路径（默认取配置）")
    p_rel.add_argument("--project", default="prj_local")
    p_rel.add_argument("--no-dotenv", action="store_true")

    p_ev = sub.add_parser("evidence", help="查看某人物的证据（按特征分组，可追到 URL）")
    p_ev.add_argument("name", help="人物名，或 pe_ 开头的实体 ID")
    p_ev.add_argument("--trait", default="", help="只看某个 trait_tag（如 caustic）")
    p_ev.add_argument("--db", default="", help="数据库路径（默认取配置）")
    p_ev.add_argument("--project", default="prj_local")
    p_ev.add_argument("--no-dotenv", action="store_true")

    p_export = sub.add_parser("export", help="导出可直接贴进文档的 Markdown")
    p_export.add_argument("name", help="人物名，或 pe_ 开头的实体 ID")
    p_export.add_argument("--format", default="md", help="导出格式（本期只支持 md）")
    p_export.add_argument("--out", default="", help="写到文件（默认打印到屏幕）")
    p_export.add_argument("--db", default="", help="数据库路径（默认取配置）")
    p_export.add_argument("--project", default="prj_local")
    p_export.add_argument("--version", type=int, default=None)
    p_export.add_argument("--no-dotenv", action="store_true")

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
            "sources": "查看来源清单（可选追溯，尚未实现）",
            "memory": "查看或清理记忆（尚未实现）",
            "eval": "运行评测（尚未实现）",
        }[name]
        p = sub.add_parser(name, help=help_text)
        if name == "new":
            p.add_argument("--names", default="", help="两个或更多人物名，用逗号分隔")
        if name in ("sources", "memory"):
            p.add_argument("project_id", nargs="?", default="")

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
                # 检索同理：给了 `--material` 就不构造检索出口（材料通道本来不检索）
                build_search_provider=(action == "run"
                                       and not bool(getattr(args, "materials", []) or [])),
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
        plan_calls,
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
    plan = plan_calls(stack, targets, materials=materials,
                      max_rounds=int(getattr(args, "max_rounds", 1) or 1))
    print("真实调用清单（执行前核对：授权 = 看过这份账再签）")
    for line in plan.as_lines():
        print(line)
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
            record = unwrap(outcome)
            _print_task(record)
            if getattr(args, "status_extra", False):
                # P5.1：回答两个只读命令最该回答的问题——"我在看哪个库"
                # 和"下一步敲什么"。其余字段沿用 `_print_task`，不改口径。
                print(f"  数据库      : {storage.path}")
                if record.status.value == "SUCCEEDED":
                    print("  下一步      : persona show <名字>")
                elif record.status.value == "PAUSED":
                    print(f"  下一步      : 提高预算后 persona resume {task_id}")
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


def _split_names(raw: str) -> list[str]:
    """逗号分隔的人名 → 去重保序列表（`run` 与只读命令共用一套口径）。"""
    parts = [piece.strip() for piece in str(raw or "").split(",")]
    return list(dict.fromkeys(piece for piece in parts if piece))


# ---------------------------------------------------------------------------
# 只读命令（show / show-relationship / evidence / export）
# ---------------------------------------------------------------------------


def _open_read_stack(args: Any) -> Any:
    """打开只读栈；失败时已打印原因并返回 `None`（不抛异常给命令层）。"""
    from pathlib import Path

    from ..core.result import error_of, is_err
    from ..orchestrator import open_read_services

    explicit = str(getattr(args, "db", "") or "").strip()
    if explicit:
        path = Path(explicit)
    else:
        settings = _load_settings_or_report(
            use_dotenv=not getattr(args, "no_dotenv", False))
        if settings is None:
            return None
        path = settings.database_path()

    opened = open_read_services(
        path, project_id=str(getattr(args, "project", "prj_local") or "prj_local"))
    if is_err(opened):
        print(f"找不到数据库：{path}")
        print(f"  原因    ：{error_of(opened)}")
        print("  提示    ：如果你刚跑完，检查 --out 是否换过目录"
              "（数据库在 <out>/persona.db）。只读命令**不会**新建数据库。")
        return None
    return opened.value


def _explain_missing_entity(name: str) -> None:
    print(f"没有找到「{name}」：本库里没有这个名字的实体。")
    print(f'  先运行：persona run --names "{name}" --work-title <作品> --adaptation <版本>')
    print("  或者确认 --db / --project 指向的是同一个库。")


def _cmd_show(args: Any) -> int:
    """`persona show`：六分区档案 + 未并入说明 + 同名提示（全部只读）。"""
    from ..core.result import is_err
    from ..orchestrator import persona_report
    from .render import render_persona

    services = _open_read_stack(args)
    if services is None:
        return 1
    try:
        outcome = persona_report(services, args.name,
                                 version=getattr(args, "version", None))
        if is_err(outcome):
            _explain_missing_entity(args.name)
            return 1
        report = outcome.value
        print(render_persona(report))
        return 1 if report.profile_missing else EXIT_OK
    finally:
        services.storage.close()


def _cmd_show_relationship(args: Any) -> int:
    """`persona show-relationship`：两人关系档案（A 看 B ≠ B 看 A，分列）。"""
    from ..core.result import is_err
    from ..orchestrator import relationship_report, resolve_entity
    from .render import render_relationship

    names = _split_names(args.names)
    if len(names) != 2:
        print("关系档案需要**两个**人物名，逗号分隔："
              "persona show-relationship 林黛玉,贾宝玉")
        return 1

    services = _open_read_stack(args)
    if services is None:
        return 1
    try:
        for name in names:
            if is_err(resolve_entity(services, name, need_profile=False)):
                _explain_missing_entity(name)
                return 1
        outcome = relationship_report(services, names[0], names[1])
        if is_err(outcome):
            print(f"「{names[0]}」和「{names[1]}」还没有关系档案："
                  f'先运行 persona run --names "{names[0]},{names[1]}" --work-title <作品>')
            print("  原因    ：关系档案只有在**同一次 run 里同时研究这两个人**时才会生成。")
            return 1
        print(render_relationship(outcome.value))
        return EXIT_OK
    finally:
        services.storage.close()


def _cmd_evidence(args: Any) -> int:
    """`persona evidence`：特征 → 证据 → 来源 URL 的完整链条（只读）。"""
    from ..core.result import is_err
    from ..orchestrator import evidence_report
    from .render import render_evidence

    services = _open_read_stack(args)
    if services is None:
        return 1
    try:
        outcome = evidence_report(services, args.name, trait=getattr(args, "trait", ""))
        if is_err(outcome):
            _explain_missing_entity(args.name)
            return 1
        print(render_evidence(outcome.value))
        # 不存在的 trait 不是错误：已经把"可用标签"列出来了，退出码仍是 0。
        return EXIT_OK
    finally:
        services.storage.close()


def _cmd_export(args: Any) -> int:
    """`persona export --format md`：可直接贴进文档的 Markdown。"""
    from datetime import date
    from pathlib import Path

    from ..core.result import is_err
    from ..orchestrator import persona_report
    from .render import render_markdown

    if str(getattr(args, "format", "md")).lower() != "md":
        print("本期只支持 --format md（json 需要 schema 约定，等有真实需求再加）")
        return 1

    services = _open_read_stack(args)
    if services is None:
        return 1
    try:
        outcome = persona_report(services, args.name,
                                 version=getattr(args, "version", None))
        if is_err(outcome):
            _explain_missing_entity(args.name)
            return 1
        report = outcome.value
        text = render_markdown(report, generated_on=date.today().isoformat())
        target = str(getattr(args, "out", "") or "").strip()
        if target:
            path = Path(target)
            try:
                if path.parent and not path.parent.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
            except OSError as exc:
                print(f"写入失败：{path}（{exc}）")
                return 1
            print(f"已写入 {path}（{len(text.splitlines())} 行）")
        else:
            print(text, end="")
        return 1 if report.profile_missing else EXIT_OK
    finally:
        services.storage.close()


# ---------------------------------------------------------------------------
# run（唯一会花钱的命令）
# ---------------------------------------------------------------------------


def _friendly_run_config_error(error: Any) -> None:
    """把配置错误翻译成人话（§7 文案表）。原始信息仍然打印，方便排查。"""
    message = str(getattr(error, "message", "") or "")
    print("配置校验：未通过")
    if "未提供任何密钥" in message or "未设置模型密钥" in message:
        print("缺少模型密钥：请设置 PERSONA_PROVIDER_API_KEY（或 DEEPSEEK_API_KEY）。")
        print("  密钥只放环境变量，不要写进 .env（写进去会被忽略并告警）。")
        print("  说明：`--dry` 也要读配置（否则清单会把检索报成 0 次，等于假账），"
              "所以缺密钥时它同样会停在这里。")
    elif "未设置检索密钥" in message:
        print("缺少检索密钥：请设置 PERSONA_SEARCH_API_KEY（或 TAVILY_API_KEY）。")
    elif "单价" in message:
        print("预算配了，但模型单价没配全：请补 PERSONA_MODEL_PRICE_IN_PER_1M / "
              "PERSONA_MODEL_PRICE_OUT_PER_1M 与 PERSONA_MODEL_PRICE_SOURCE。")
        print("  没有单价就无法在发送前估算，闸门不会用估计值冒充。")
    else:
        print(f"  说明    ：{message}")
    print("  提示    ：persona config show 可以看到当前生效值与来源。")


def _explain_missing_search() -> None:
    """检索出口不可用时说清"跑下去等于白花模型钱"。"""
    print("拒绝运行：本次计划里没有任何检索（检索出口离线或没配密钥）。")
    print("  为什么停    ：persona run 的材料全部来自检索；没有检索就只能拿空材料"
          "去调模型——钱照花，证据一条没有。")
    print("  怎么办      ：设置 PERSONA_SEARCH_API_KEY（或 TAVILY_API_KEY）后重试。")
    print("  说明：本次**没有发起任何请求**。")


def _explain_missing_extractor(stack: Any) -> None:
    """没有抽取器时，说清"为什么跑下去等于白跑"。"""
    print("拒绝运行：没有可用的 claim 抽取器（材料变不成证据）。")
    blocked = False
    for note in getattr(stack, "warnings", []):
        if note.code in ("CHAT_PROVIDER_UNAVAILABLE", "EXTRACTOR_ABSENT",
                         "REAL_PROVIDER_BLOCKED"):
            print(f"  装配告警：{note.message}")
            blocked = blocked or note.code == "REAL_PROVIDER_BLOCKED"
    if blocked:
        print("  原因    ：本次运行没有被授权真实外呼。")
    else:
        print("  原因    ：未配置模型 Provider（PERSONA_MODEL_PROVIDER）或未设置密钥。")
    print("  建议    ：参考 .env.example 配置模型；用 persona config check 复核。")
    print("  说明    ：本次**没有发起任何请求，也没有产生费用**。")


def _print_run_plan(plan: Any, stack: Any, *, names: list[str], work_title: str,
                    adaptation: str, adaptation_defaulted: bool, vocabulary_name: str,
                    vocabulary_count: int, budget: float | None,
                    settings: Any, dry: bool = False) -> None:
    """打印本次真实调用清单（§3.1.2：**默认值必须标注来源**）。"""
    print("本次将要发出的真实调用（钱从这里开始算）")
    print(f"  研究对象      : {'、'.join(names)}")
    if work_title:
        mark = "（未指定，默认）" if adaptation_defaulted else "（显式指定）"
        print(f"  作品 / 改编版本: {work_title} / {adaptation}{mark}")

    if plan.searches == 0:
        print("  检索          : 0 次")
    else:
        credits = "" if plan.est_search_credits is None else \
            f"（模板查询，basic ≈{plan.est_search_credits:g} credits）"
        print(f"  检索          : {plan.searches} 次{credits}")
    # D-B：清单必须反映**实际装配结果**，不能照着配置打印。
    # `plan.extracts_upper_bound` 来自 `research.max_extracts` 配置，但配置生效的
    # 前提是装配层真的装了提取策略——没装就是 0 次，打"上限 3 次"就是假账
    # （2026-10-19 第一次真跑就吃过这个：清单写 3，实际 0）。
    describe = stack.describe()
    extraction_on = bool((describe.get("components") or {}).get("extraction_strategy"))
    if not extraction_on:
        print(f"  抓取正文      : 0（未启用正文抓取：本库装配没有提取策略，"
              f"配置 PERSONA_RESEARCH_MAX_EXTRACTS={plan.extracts_upper_bound} 不生效）")
    elif plan.extracts_upper_bound == 0:
        print("  抓取正文      : 0 次（已启用，但上限配置为 0）")
    else:
        print(f"  抓取正文      : 上限 {plan.extracts_upper_bound} 次"
              f" + 补救 {plan.remediations} 次")
    print(f"  模型抽取      : {plan.extractions} 次")
    if plan.dialogues:
        # D-A：对话与抽取共用同一个聊天 Provider。装配层没接上时清单必须说出来，
        # 否则"模拟对话 1 次"是一句会在 DIALOGUE_RUN 阶段当场作废的承诺。
        components = describe.get("components") or {}
        if components.get("dialogue_provider") in (None, "FakeProvider"):
            print(f"  模拟对话      : {plan.dialogues} 次 ⚠️ "
                  "但对话阶段没有真实聊天 Provider（FakeProvider），"
                  "DIALOGUE_RUN 会失败（未登记的 fixture）")
        else:
            print(f"  模拟对话      : {plan.dialogues} 次")
    else:
        print(f"  模拟对话      : {plan.dialogues} 次")
    print(f"  词表          : {vocabulary_name}（{vocabulary_count} 个标签；"
          f"--vocabulary l2 启用 42 个中文古典标签）")

    currency = plan.currency
    if budget is None:
        print(f"  模型费用上限  : 未设置（单任务）")
    else:
        print(f"  模型费用上限  : {budget:.2f} {currency}（单任务，按 task_id 累计）")

    total_cap = getattr(getattr(settings, "model", None), "max_cost", None)
    if budget is not None and total_cap is not None and total_cap <= budget:
        print(f"  累计上限      : {total_cap:.2f} {currency}"
              f"（配置值，与 --budget 取严 → 本次实际生效 {min(total_cap, budget):.2f}）")

    if plan.est_model_cost is None:
        print("  预估模型费用  : 估不出来（缺单价或 token 估算）")
    else:
        print(f"  预估模型费用  : {plan.est_model_cost:.6f} {currency}"
              "（仅估算；实际以账单为准）")
        # 2026-10-19 首次真跑实测：真实 token 是估算常量的 3.5×/2.4×，
        # 单次实际比预估高约 33%。预算有硬上限兜底，但读者得知道这个数偏低。
        print(f"  口径提醒      : 实测曾为估算值的 1.3 倍（真实 token 远大于"
              f" EST_TOKENS 常量；见 docs/P5_1_REAL_RUN.md §9.5）")

    ledger = stack.describe().get("ledger") or {}
    if ledger.get("hydrated"):
        print(f"  账本已占用    : {ledger.get('occupied_total', 0.0):.6f} {currency}"
              "（本库历史，含此前运行）")
    elif dry:
        # `--dry` 不碰数据库（不建表、不读历史）。既然读不到，就不能假装算出
        # "本次实际生效 0.25"——累计上限是**减去已用额之后**才生效的。
        print("  账本已占用    : 未读取（--dry 不碰数据库，累计已用额未知；"
              "上面的\"实际生效\"未扣除历史支出）")
    print("  边界          : 清单之外不会有任何调用；出现清单外的调用即为缺陷")


def _cmd_run(args: Any) -> int:
    """`persona run`：真实检索 + 模型抽取 + 档案（**要过一次确认**）。"""
    from ..config import load_settings
    from ..core.context import new_call_context
    from ..core.result import error_of, is_err, unwrap
    from ..orchestrator import (
        BusinessRequest,
        StackOptions,
        build_business_stack,
        model_limits_from_settings,
        plan_calls,
        plan_targets,
        register_targets,
        resolve_vocabulary,
        TargetSpec,
    )
    from ..storage.sqlite import SqliteStorage
    from . import consent

    names = _split_names(args.names)
    if not names:
        print("缺少 --names：至少给一个人物名（逗号分隔）")
        return 1

    entity_type = str(getattr(args, "entity_type", "FICTIONAL_CHARACTER"))
    work_title = str(getattr(args, "work_title", "") or "").strip()
    adaptation_given = bool(str(getattr(args, "adaptation", "") or "").strip())
    adaptation = str(getattr(args, "adaptation", "") or "").strip()
    if entity_type == "FICTIONAL_CHARACTER":
        if not work_title:
            print("虚构角色必须声明所属作品（PF-V5）：请补 --work-title；"
                  "或改用 --type REAL_PERSON")
            return 1
        if not adaptation:
            adaptation = DEFAULT_ADAPTATION

    vocabulary_name = str(getattr(args, "vocabulary", "l1") or "l1")
    resolved_vocab = resolve_vocabulary(vocabulary_name)
    if is_err(resolved_vocab):
        print(error_of(resolved_vocab).message)
        return 1
    vocabulary = unwrap(resolved_vocab)
    vocabulary_count = len(vocabulary.all_tags())

    budget = getattr(args, "budget", DEFAULT_BUDGET_USD)
    if budget is not None and float(budget) < 0:
        print("--budget 必须是非负数（0 表示不允许任何模型花费）")
        return 1

    overrides = {
        # 检索出口：`PERSONA_PROVIDER` 默认 `fake`（离线）；真实检索必须显式指定。
        "PERSONA_PROVIDER": "tavily",
        # 真实外呼必然要求 offline_only=false；密钥是否齐备由配置校验兜住。
        "PERSONA_OFFLINE_ONLY": "false",
    }
    out_dir_arg = str(getattr(args, "out", "") or "").strip()
    if out_dir_arg:
        overrides["PERSONA_DATA_DIR"] = out_dir_arg
    if budget is not None:
        overrides["PERSONA_MODEL_MAX_COST_PER_TASK"] = str(float(budget))

    loaded = load_settings(use_dotenv=not getattr(args, "no_dotenv", False),
                           overrides=overrides)
    if is_err(loaded):
        _friendly_run_config_error(error_of(loaded))
        return 1
    settings = unwrap(loaded)
    for note in loaded.warnings:
        print(f"提示：{note.message}")

    data_root = settings.database_path().parent
    db_path = settings.database_path()
    db_existed = db_path.exists()
    storage = SqliteStorage(settings.ensure_data_dir())
    ctx = new_call_context(project_id=str(getattr(args, "project", "prj_local")),
                           actor="cli")
    try:
        built = build_business_stack(
            settings, ctx, storage=storage,
            options=StackOptions(
                build_chat_provider=True,
                build_search_provider=True,
                trait_vocabulary=vocabulary,
                extract_temperature=getattr(args, "extract_temperature", None),
                # `--dry` = 预览：**不建表**。`init_schema` 会把表真写进库文件，
                # 于是"只看一眼清单"就留下一个 80 KB 的空库；没建表就不能 hydrate，
                # 两者在装配层已经绑在一起关掉。
                initialize_schema=not getattr(args, "dry", False),
            ),
            # `--dry` 也走真实装配：**只构造 Provider，不发请求**。否则清单会把
            # "本次将会发生 2 次检索"报成 0 次，等于给用户看一份假账。
            allow_real=True,
        )
        if is_err(built):
            print(f"装配失败：{error_of(built)}")
            return 1
        stack = unwrap(built)
        if stack.claim_extractor is None:
            _explain_missing_extractor(stack)
            return 1

        specs = [
            TargetSpec(display_name=name, entity_type=entity_type,
                       work_title=work_title, adaptation_version=adaptation)
            for name in names
        ]
        # **先算账、后登记**：`register_targets` 每次都会新建实体（不去重），
        # 放在授权之前意味着"用户点了取消，库里多出几个同名实体"。
        plan = plan_calls(stack, plan_targets(specs), materials=[], max_rounds=1)
        model_calls = plan.extractions + plan.dialogues
        if plan.searches == 0:
            # 没有检索出口还继续跑 = 拿空材料去花模型钱。清单上会写"检索 0 次"，
            # 但那个 0 不是"省了钱"，是"这条路走不通"，必须当场停。
            _explain_missing_search()
            return 1
        _print_run_plan(
            plan, stack, names=names, work_title=work_title, adaptation=adaptation,
            adaptation_defaulted=not adaptation_given, vocabulary_name=vocabulary_name,
            vocabulary_count=vocabulary_count, budget=budget, settings=settings,
            dry=bool(getattr(args, "dry", False)),
        )

        limits = model_limits_from_settings(settings)
        if limits.max_calls is not None and model_calls > int(limits.max_calls):
            print()
            print(f"模型调用上限 {int(limits.max_calls)} 次 < 本次需要 {model_calls} 次。"
                  f"请设 PERSONA_MODEL_MAX_CALLS={model_calls}（或更大）。")
            print("  说明：本次**没有发起任何请求**。")
            return 1
        if (budget is not None and plan.est_model_cost is not None
                and plan.est_model_cost > float(budget)):
            print()
            print(f"预算不足：本次计划 {model_calls} 次模型调用、"
                  f"预估 {plan.est_model_cost:.4f} {plan.currency}，"
                  f"--budget 只给了 {float(budget):.3f}。")
            print("  要么调大 --budget，要么少传几个名字。")
            print("  说明：本次**没有发起任何请求**。")
            return 1

        if getattr(args, "dry", False):
            print()
            print("（--dry：只打印清单，没有发生任何调用）")
            # `--dry` 承诺"不落库"：装配阶段的账本 hydrate 会连库、sqlite 会顺手
            # 建一个 0 字节文件。若这个库**本来是空的**，把它删掉——否则用户
            # 只是看一眼清单，回来发现多了一个空数据库。
            storage.close()
            if not db_existed and db_path.exists() and db_path.stat().st_size == 0:
                try:
                    db_path.unlink()
                except OSError:
                    pass
            return EXIT_OK

        if not _authorize_real_calls(
            data_root=data_root, searches=plan.searches, model_calls=model_calls,
            estimated_usd=plan.est_model_cost, budget_usd=budget,
            currency=plan.currency, yes=bool(getattr(args, "yes", False)),
        ):
            return 1

        registered = register_targets(storage, ctx, specs)
        if is_err(registered):
            print(f"登记人物失败：{error_of(registered)}")
            return 1
        targets = unwrap(registered)

        print()
        print(f"开跑：{len(targets)} 名目标")
        result = stack.orchestrator.run(
            ctx, BusinessRequest(targets=targets, materials=[], max_rounds=1))
        if is_err(result):
            print(f"运行失败：{error_of(result)}")
            return 1
        run = unwrap(result)

        records = stack.ledger.records()
        spent = sum(float(getattr(record, "cost_confirmed", None) or 0.0)
                    for record in records)
        print()
        print(f"  结论            : {run.status}（任务 {run.task_id} / {run.task_status}）")
        for step in run.steps:
            print(f"  - {step.name:<22}{step.status:<16}{step.message}")
            for note in step.warnings:
                print(f"      ⚠️ [{note.code}] {note.message}")
        print(f"  来源 / 证据     : {len(run.source_ids)} / {len(run.evidence_ids)}")
        print(f"  人物 / 关系档案 : {len(run.persona_profile_ids)} / "
              f"{len(run.relationship_profile_ids)}")
        if records:
            print(f"  费用            : {spent:.7f} {plan.currency}"
                  f"（{len(records)} 次模型调用）")
        print()
        print("接下来可以：")
        for name in names:
            print(f"  persona show {name}")
        if len(names) >= 2:
            print(f"  persona show-relationship {names[0]},{names[1]}")
        print(f"  persona evidence {names[0]}")
        print(f"  persona export {names[0]} --format md --out {names[0]}.md")
        return EXIT_OK if run.is_complete() else 1
    finally:
        storage.close()


def _authorize_real_calls(
    *, data_root: Any, searches: int, model_calls: int,
    estimated_usd: float | None, budget_usd: float | None,
    currency: str = "USD", yes: bool = False,
) -> bool:
    """真实调用前的**那一次确认**（P5.1；P6.2-c 起 `run` 与 `chat` 共用这一份）。

    **为什么不许复制一份**：两个命令各写一套确认，用户就会在两个入口遇到
    两种问法、两种"记住"行为——一边记住了另一边还问，或者反过来。确认只有
    一种说法（同一个问句、同一个 `.persona_consent.json`、同一条"非交互必须
    `--yes`"），所以这里必须是**唯一实现**，两个命令都来调它。

    返回 `False` = 不该跑（调用方直接返回退出码 1，且**此时零调用**）。
    """
    from . import consent

    interactive = _stdin_is_tty() and _stdout_is_tty()
    if not interactive:
        if not yes:
            print()
            print("检测到非交互环境（stdin 不是终端）。真实调用需要显式确认，"
                  "请在命令末尾加 --yes 重跑。")
            return False
        return True
    if consent.is_confirmed(data_root):
        print()
        print(consent.previous_note(data_root))
        return True
    print()
    print(consent.build_question(
        searches=int(searches), model_calls=int(model_calls),
        estimated_usd=estimated_usd, budget_usd=budget_usd, currency=currency))
    try:
        answer = input("> ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        print("已取消，没有发生任何调用。")
        return False
    consent.remember(data_root, estimated_usd=estimated_usd)
    return True


# ---------------------------------------------------------------------------
# chat（P6.2-c：第二个会花钱的命令）
#
# 刻意不做：流式输出、多用户身份、记忆管理命令、任何"帮你润色"的改写。
# 这一版的唯一目标是让"规则引擎写记忆"这条链路**在产品路径上真的跑得起来**
# ——否则 P6.2 就又是 D 家族第 N 例：能力只存在于测试显式注入的那条路上。
# ---------------------------------------------------------------------------


def _chat_memory_policy(raw: str) -> str:
    """`--memory-write rule|off` → 会话的 `memory_write_policy`。

    `off` 映射到 `none`（不是新增一个值）：默认不写这件事**只有一种说法**，
    产品面换个词就够了，领域层不该长出第二个"什么都不写"。
    """
    # 经协调层取常量：CLI 不许直连 `persona.modules`（D-059 / F-7）。
    from ..orchestrator import POLICY_MEMORY_WRITE_NONE, POLICY_MEMORY_WRITE_RULE

    return (POLICY_MEMORY_WRITE_RULE if str(raw or "").strip() == "rule"
            else POLICY_MEMORY_WRITE_NONE)


def _print_chat_plan(*, names: list[str], rounds: int, policy: str,
                     estimated_usd: float, budget: float | None,
                     currency: str, db_path: Any, occupied: float,
                     topic: str | None = None) -> None:
    """对话的调用清单。**轮数就是调用数**，不玩"预估 × 系数"的花招。"""
    from ..orchestrator import POLICY_MEMORY_WRITE_RULE

    print("本次将要发出的真实调用（钱从这里开始算）")
    print(f"  对话人物      : {'、'.join(names)}")
    print(f"  数据库        : {db_path}")
    print(f"  对话轮次      : {rounds}（每轮 1 次模型调用，共 {rounds} 次）")
    if topic:
        print(f"  跨会话话题    : {topic}（只取**同一批参与者**、内容命中该话题的"
              "会话级模拟记忆；**额外 0 次调用**：记忆检索走本地库，不花钱）")
    else:
        print("  跨会话话题    : 无（**不跨会话取记忆**，只取本会话自己写下的）")
    if policy == POLICY_MEMORY_WRITE_RULE:
        print("  记忆写入      : rule（把**你原话**里值得记的片段写成会话级模拟记忆；"
              "确定性规则，0 次额外调用）")
    else:
        print("  记忆写入      : off（默认。**一条都不写**：对话内容不会进长期记忆）")
    if budget is None:
        print("  模型费用上限  : 未设置（单任务）")
    else:
        print(f"  模型费用上限  : {float(budget):.2f} {currency}（单任务）")
    print(f"  预估模型费用  : {estimated_usd:.6f} {currency}"
          "（按对话实测单价 0.0005/次，不是抽取用的 EST 常量）")
    if occupied:
        print(f"  账本已占用    : {occupied:.6f} {currency}（本库历史，含此前运行）")
    print("  边界          : 清单之外不会有任何调用；出现清单外的调用即为缺陷")


def _explain_missing_chat_provider(stack: Any) -> None:
    """没有真实聊天 Provider 时说清"为什么开不了会话"。"""
    print("拒绝运行：对话阶段没有真实聊天 Provider（装配结果里是 FakeProvider）。")
    for note in getattr(stack, "warnings", []):
        if note.code in ("CHAT_PROVIDER_UNAVAILABLE", "REAL_PROVIDER_BLOCKED"):
            print(f"  装配告警：{note.message}")
    print("  怎么办      ：设置 PERSONA_MODEL_PROVIDER 与 PERSONA_PROVIDER_API_KEY"
          "（或 DEEPSEEK_API_KEY），再用 persona config check 复核。")
    print("  说明        ：本次**没有发起任何请求，也没有产生费用**。")


def _read_user_line() -> str | None:
    """REPL 读一行。`None` = 结束（Ctrl-D / `/exit`）；`""` = 空行（忽略，不计轮次）。

    **不在这里捕获 `KeyboardInterrupt`**：Ctrl-C 要交给外层统一收尾
    （结束会话 + 打印已写记忆 + 返回中断退出码），在这里吞掉就没人知道
    该收尾了。
    """
    try:
        raw = input("你> ")
    except EOFError:
        print()
        return None
    text = str(raw).strip()
    if text in ("/exit", "/quit"):
        return None
    return text


def _print_chat_turn(index: int, message: str | None, view: Any,
                     display: dict[str, str]) -> None:
    if message:
        print(f"你：{message}")
    name = display.get(view.speaker_entity_id) or view.speaker_entity_id
    print(f"{name}：{view.content}")
    if view.referenced_evidence_ids:
        print(f"    依据：{'、'.join(view.referenced_evidence_ids)}")
    if view.generated_memory_ids:
        print(f"    记忆：{'、'.join(view.generated_memory_ids)}")


def _cmd_chat(args: Any) -> int:
    """`persona chat`：拿库里**已有**的档案开一次多轮对话（真实模型，要过确认）。

    三件事是硬要求（P6.2-c）：
      ① 开会话时把 `--memory-write` 映射成的策略**真的传进去**（默认 `none`）；
      ② 每轮走 `run_round`（`rule` 策略下由它派生候选并写记忆）；
      ③ 结束时打印写下的记忆 ID 与会话终态——**写了什么必须能看见**。
    """
    from pathlib import Path

    from ..config import load_settings
    from ..core.context import new_call_context
    from ..core.result import error_of, is_err, unwrap
    # 全部经协调层：CLI 不许直连 `persona.modules`（D-059 / F-7）。
    from ..orchestrator import (
        DIALOGUE_MEASURED_COST_PER_CALL_USD,
        POLICY_LEAST_RECENTLY_SPOKEN,
        POLICY_MEMORY_WRITE_RULE,
        ReadServices,
        SessionDraft,
        StackOptions,
        build_business_stack,
        model_limits_from_settings,
        resolve_entity,
        topic_is_retrievable,
    )
    from ..storage.sqlite import SqliteStorage

    names = _split_names(getattr(args, "names", ""))
    if len(names) < 2:
        print("persona chat 至少要 2 个人物：对话要在参与者之间轮换发言，"
              "一个人开不了会话。")
        print('  例：persona chat --names "林黛玉,贾宝玉" --rounds 3')
        return 1

    raw_rounds = getattr(args, "rounds", 1)
    try:
        rounds = int(raw_rounds)
    except (TypeError, ValueError):
        print(f"--rounds 必须是整数（收到 {raw_rounds!r}）")
        return 1
    if rounds < 1:
        print(f"--rounds 必须是正整数（收到 {rounds}）：0 轮就是不调用，不用开会话。")
        return 1

    policy = _chat_memory_policy(getattr(args, "memory_write", "off"))
    budget = getattr(args, "budget", DEFAULT_BUDGET_USD)
    if budget is not None and float(budget) < 0:
        print("--budget 必须是非负数（0 表示不允许任何模型花费）")
        return 1

    # topic 门禁：**在任何调用之前**，更在授权确认之前——坏 topic 一步钱都不动。
    # 「不给 topic」= 不跨会话取记忆（Ok(None)，不报错）；「给了坏 topic」= 报错退出。
    topic_check = topic_is_retrievable(getattr(args, "topic", ""))
    if is_err(topic_check):
        print(f"ERROR: {error_of(topic_check).message}")
        print('  例如：--topic "西厢记"')
        print("  说明：本次**没有发起任何请求**。")
        return 1
    topic = topic_check.value

    overrides = {
        # 对话要调模型，`offline_only=true` 下装配层根本不会构造真实 Provider。
        "PERSONA_OFFLINE_ONLY": "false",
    }
    explicit_db = str(getattr(args, "db", "") or "").strip()
    if explicit_db:
        # 确认文件放在**数据库旁边**：换库 = 重新问一次，符合"记住的是这个库"。
        overrides["PERSONA_DATA_DIR"] = str(Path(explicit_db).parent)

    loaded = load_settings(use_dotenv=not getattr(args, "no_dotenv", False),
                           overrides=overrides)
    if is_err(loaded):
        _friendly_run_config_error(error_of(loaded))
        return 1
    settings = unwrap(loaded)
    for note in loaded.warnings:
        print(f"提示：{note.message}")

    db_path = Path(explicit_db) if explicit_db else settings.database_path()
    if not db_path.exists():
        # 对话不建库：没有档案就没有角色依据，建一个空库只会让人以为"能聊"。
        print(f"找不到数据库：{db_path}")
        print("  对话用的是库里**已有的人物档案**，没有档案就没有角色依据。")
        print('  先运行：persona run --names "林黛玉,贾宝玉" --work-title <作品>')
        print("  或者确认 --db / --project 指向的是同一个库。")
        return 1
    data_root = db_path.parent

    storage = SqliteStorage(db_path)
    ctx = new_call_context(project_id=str(getattr(args, "project", "prj_local")),
                           actor="cli")
    try:
        built = build_business_stack(
            settings, ctx, storage=storage,
            options=StackOptions(build_chat_provider=True,
                                 build_search_provider=False),
            allow_real=True,
        )
        if is_err(built):
            print(f"装配失败：{error_of(built)}")
            return 1
        stack = unwrap(built)
        components = (stack.describe().get("components") or {})
        if components.get("dialogue_provider") in (None, "FakeProvider"):
            _explain_missing_chat_provider(stack)
            return 1

        services = ReadServices(
            storage=stack.storage, ctx=ctx, evidence=stack.evidence,
            persona=stack.persona, relationship=stack.relationship,
            database_path=db_path)
        people: list[tuple[str, str]] = []
        for name in names:
            got = resolve_entity(services, name, need_profile=True)
            if is_err(got):
                _explain_missing_entity(name)
                return 1
            ref = unwrap(got)[0]
            if is_err(stack.persona.get_persona(ctx, ref.entity_id)):
                print(f"「{name}」在库里还没有人物档案：对话要靠档案当角色依据，"
                      "没有档案就只能瞎编。")
                print(f'  先运行：persona run --names "{name}" --work-title <作品>')
                return 1
            people.append((ref.entity_id, ref.display_name))

        currency = str(getattr(settings.model, "currency", "") or "USD").strip() or "USD"
        estimated_usd = round(rounds * float(DIALOGUE_MEASURED_COST_PER_CALL_USD), 6)
        limits = model_limits_from_settings(settings)
        _print_chat_plan(
            names=[display for _, display in people], rounds=rounds, policy=policy,
            estimated_usd=estimated_usd, budget=budget, currency=currency,
            db_path=db_path, occupied=stack.ledger.occupied_cost(), topic=topic)

        if limits.max_calls is not None and rounds > int(limits.max_calls):
            print()
            print(f"模型调用上限 {int(limits.max_calls)} 次 < 本次需要 {rounds} 次。"
                  f"请设 PERSONA_MODEL_MAX_CALLS={rounds}（或更大）。")
            print("  说明：本次**没有发起任何请求**。")
            return 1
        if budget is not None and estimated_usd > float(budget):
            print()
            print(f"预算不足：本次计划 {rounds} 次模型调用、预估 "
                  f"{estimated_usd:.4f} {currency}，--budget 只给了 {float(budget):.3f}。")
            print("  要么调大 --budget，要么减少 --rounds。")
            print("  说明：本次**没有发起任何请求**。")
            return 1

        # 授权：与 `persona run` **同一个函数、同一份 `.persona_consent.json`**。
        if not _authorize_real_calls(
            data_root=data_root, searches=0, model_calls=rounds,
            estimated_usd=estimated_usd, budget_usd=budget, currency=currency,
            yes=bool(getattr(args, "yes", False)),
        ):
            return 1

        started = stack.dialogue.start_session(ctx, SessionDraft(
            participant_entity_ids=[pid for pid, _ in people],
            persona_profile_ids=[pid for pid, _ in people],
            speaker_selection_policy=POLICY_LEAST_RECENTLY_SPOKEN,
            memory_write_policy=policy, topic=topic))
        if is_err(started):
            print(f"开启会话失败：{error_of(started)}")
            return 1
        session = unwrap(started)
        display = dict(people)
        first = str(getattr(args, "message", "") or "").strip()

        print()
        memory_ids: list[str] = []
        done = 0
        interrupted = False
        try:
            for index in range(rounds):
                if first:
                    # `--message` 只发这一句；后续轮次没有新输入（角色接着说）。
                    message = first if index == 0 else None
                else:
                    message = _read_user_line()
                    if message is None:
                        break
                    if not message:
                        continue
                turn = stack.dialogue.run_round(ctx, session.id, message)
                if is_err(turn):
                    print(f"第 {index + 1} 轮失败：{error_of(turn)}")
                    return 1
                view = unwrap(turn)
                done += 1
                memory_ids.extend(view.generated_memory_ids)
                _print_chat_turn(done, message, view, display)
        except KeyboardInterrupt:
            interrupted = True
            print()
            print("（Ctrl-C：已中断。已完成的轮次与记忆**都保留**，不回滚）")
        finally:
            # 无论正常结束、Ctrl-C 还是 EOF，会话都不能留在 ACTIVE：
            # 一个 OPEN 的会话会被下一次 `chat` 当成"还能继续聊"，而它其实
            # 已经没有主人了。
            closed = stack.dialogue.end_session(
                ctx, session.id, reason="ctrl_c" if interrupted else "completed")
            if is_err(closed):
                print(f"会话结束失败：{error_of(closed)}")

        print()
        # 状态**照实报**：结束失败就说失败，不用"ENDED"把收尾失败盖过去。
        status = "" if is_err(closed) else str(getattr(closed.value, "status", "") or "")
        print(f"会话 {session.id}：完成 {done} 轮，状态 {status or '未能结束'}，"
              f"记忆写入策略 {policy}")
        if memory_ids:
            print(f"写入记忆 {len(memory_ids)} 条：{'、'.join(memory_ids)}")
            print("  （会话级模拟记忆，scope=SESSION；不会升级成人物档案或证据）")
        elif policy == POLICY_MEMORY_WRITE_RULE:
            print("写入记忆 0 条：这几句里没有规则认定该记的内容（不是故障）")
        else:
            print("写入记忆 0 条（默认 off：对话内容不进长期记忆；"
                  "要写请加 --memory-write rule）")
        records = stack.ledger.records()
        if records:
            spent = sum(float(getattr(record, "cost_confirmed", None) or 0.0)
                        for record in records)
            print(f"费用：{spent:.7f} {currency}（{len(records)} 次模型调用）")
        return EXIT_INTERRUPTED if interrupted else EXIT_OK
    finally:
        storage.close()


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _stdout_is_tty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, ValueError):
        return False


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
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "chat":
        return _cmd_chat(args)
    if args.command == "show":
        return _cmd_show(args)
    if args.command == "show-relationship":
        return _cmd_show_relationship(args)
    if args.command == "evidence":
        return _cmd_evidence(args)
    if args.command == "export":
        return _cmd_export(args)
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
                status_extra=(args.command == "status"),
            )
        )

    print(f"命令 `persona {args.command}` 尚未实现。")
    print(f"当前阶段：{STAGE}")
    print("可用命令：persona run / chat / status / show / show-relationship / evidence / "
          "export / info / selftest / config / model / business / usage / task")
    return EXIT_NOT_IMPLEMENTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
