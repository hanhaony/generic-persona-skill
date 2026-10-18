#!/usr/bin/env python
"""P4.8 R4：**真实检索** e2e 运行器（一次性、人工执行、不进 unittest 收集）。

    python scripts/e2e_p4_8_real_search.py --dry     # 只打印清单与预检；物理断网
    python scripts/e2e_p4_8_real_search.py           # 真实运行（花真钱）

# 它会花真钱

一次运行最多产生三类**计费**调用：

| 类别 | 上限 | 计量单位 | 说明 |
| --- | --- | --- | --- |
| 检索（Tavily search） | **2 次** | credit | 每个目标 1 条模板查询（无 topic 时只出 1 条） |
| 抓取（Tavily extract） | **≤2 次**（1 初始 + 1 补救） | credit | `max_extracts` 与 `max_remediations` 两份额度独立；单条失败官方不计费 |
| 模型（DeepSeek chat） | **3 次** | USD | 1 次批量抽取 + 2 次对话（2 目标 × 1 轮） |

**清单外出现任何一次调用即视为缺陷**——脚本自己数，数不对就标红（见 §[12]）。

# 为什么要单独写一个脚本

`persona business run` 是产品入口，它不该为了"留痕"长出一堆只有一次性运行才
需要的字段。真实检索 e2e 需要的额外能力是：

1. **原始响应落盘**（对账/报障要看官方返回的 `request_id` 与 `usage`）；
2. **逐笔调用计数**（把"清单 vs 实际"当断言，不是当感想）；
3. **物理断网自检**（`--dry` 把 `_urlopen` 换成必然失败的替身）。

这三件事都只在"这次运行"里成立，所以放在 `scripts/`，不进产品代码。

# 记录层次（谁记什么）

- **协议层**：`_RawDumper` 包在 `provider._urlopen` 外面，只抄 HTTP 往返原文，
  不解析、不判定（解析仍归 Provider，判定仍归模块）；
- **账本层**：`ExternalUsageLedger` 记逐笔费用，经 `usage_sink` 落到隔离库；
- **业务层**：`BusinessRunResult` 记五个阶段的状态与产物 ID。

三者互不覆盖：原文用于对账，账本用于闸门，阶段结果用于"到底跑没跑"。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from persona.config import load_settings                                    # noqa: E402
from persona.core.context import new_call_context                          # noqa: E402
from persona.core.result import error_of, is_err, is_ok, unwrap             # noqa: E402
from persona.core.types import Query                                        # noqa: E402
from persona.modules.dialogue import DIALOGUE_TEMPERATURE                    # noqa: E402
from persona.modules.extraction import (                                    # noqa: E402
    DEFAULT_EXTRACT_TEMPERATURE,
    DEFAULT_MAX_CLAIMS_PER_MATERIAL,
    OUTPUT_TOKEN_CEILING,
    OUTPUT_TOKEN_FLOOR,
    OUTPUT_TOKEN_OVERHEAD,
    PROMPT_VERSION,
    TOKENS_PER_CLAIM,
    estimate_output_tokens,
    output_ceiling_is_sufficient,
)
from persona.modules.material_replay import (                               # noqa: E402
    MaterialReplayError,
    MaterialReplayMiss,
    install_replay,
    load_replay_cache,
)
from persona.modules.research import TopNExtractionStrategy                 # noqa: E402
from persona.modules.trait_vocabulary import vocabulary_by_name            # noqa: E402
from persona.orchestrator import (                                          # noqa: E402
    BusinessRequest,
    StackOptions,
    TargetSpec,
    build_business_stack,
    plan_calls,
    register_targets,
)
from persona.orchestrator.business import (                                 # noqa: E402
    RUN_PARTIAL,
    RUN_SUCCEEDED,
)
from persona.orchestrator.model_setup import (                              # noqa: E402
    chat_provider_config,
    chat_provider_from_settings,
    model_limits_from_settings,
    model_plan,
)
from persona.storage.sqlite import SqliteStorage                            # noqa: E402

PROJECT = "prj_p48_r4"
TASK_ID = "task_p48_r4_real_search"
ACTOR = "p48-r4"

#: 目标（用户拍板：阿灯 / 阿雾，同一作品同一改编版本）
#: R4.1 换角复跑时用 `--targets`/`--work-title`/`--adaptation` 覆盖，
#: 验证"目标真实存在于网络"时的**有来源 → 有证据 → 有 traits** 主路径。
DEFAULT_TARGETS = "阿灯,阿雾"
DEFAULT_WORK_TITLE = "《雾港灯会》"
DEFAULT_ADAPTATION = "2020 动画改编版"

#: 与用户确认过的清单（**硬断言**：实际值必须与这三条一致）
EXPECTED_SEARCHES = 2
#: 抓取有**两份额度**（D-288 另记）：初始抓取 `max_extracts` 与低质量补救
#: `max_remediations` **互相独立**（补救不占初始名额）。R4.5 只写"≤1"，
#: 实际发了 2 次（1 初始 + 1 补救）就被自检判成"清单外调用"——
#: 判定没错，错在清单口径漏了补救援额度。
EXPECTED_EXTRACTS_INITIAL = 1
EXPECTED_REMEDIATIONS_MAX = 1
EXPECTED_EXTRACTS_MAX = EXPECTED_EXTRACTS_INITIAL + EXPECTED_REMEDIATIONS_MAX
#: 模型调用 = 1 次批量抽取 + `max_rounds` 次对话。
#: **P4.8 / D-4 修正**：对话每轮只生成 1 个 turn，所以是 `轮数` 不是 `目标数 × 轮数`
#: ——此前后者把清单报大了一倍（2 目标 × 1 轮曾报成 2 次对话，实际 1 次）。
EXPECTED_MODEL_CALLS_FIXED = 1          # 批量抽取，与目标数无关
EXPECTED_EST_MODEL_USD_PER_CALL = 0.0033
EXPECTED_EST_SEARCH_CREDITS = 2.2       # 2×1.0 + 1×0.2


# --------------------------------------------------------------------------
# 输出缓冲 + 密钥泄漏自检
# --------------------------------------------------------------------------
class Out:
    """所有输出先过这里：任一行出现密钥子串 → 立即替换并标记泄漏。"""

    def __init__(self, *secrets: str) -> None:
        self.secrets = [s for s in secrets if s]
        self.leak = False

    def __call__(self, text: str = "") -> None:
        for secret in self.secrets:
            if secret in text:
                self.leak = True
                text = text.replace(secret, "***[REDACTED]***")
        print(text)


def hr(out: Out, title: str) -> None:
    out("")
    out("=" * 72)
    out(title)
    out("=" * 72)


# --------------------------------------------------------------------------
# 原始响应落盘（只抄原文，不解析）
# --------------------------------------------------------------------------
class _CapturedResponse:
    """`urlopen` 返回值的薄代理：只在 `read()` 时把原文抄一份。"""

    def __init__(self, resp, entry: dict, sink: "_RawDumper") -> None:
        self._resp = resp
        self._entry = entry
        self._sink = sink
        self._done = False

    def read(self, *args, **kwargs):
        try:
            data = self._resp.read(*args, **kwargs)
        except BaseException as exc:
            # 超时经常发生在**读响应体**这一步（连接已建立、body 没回来）。
            # 此前不抓这里，于是"失败的那次请求"在原始记录里凭空消失——
            # 对账时看不到它，只能靠账本里的 UNKNOWN 猜。现在如实记一笔。
            if not self._done:
                self._done = True
                self._entry["error"] = f"{type(exc).__name__}: {exc}"
                self._sink.flush(self._entry)
            raise
        if not self._done:
            self._done = True
            self._entry["response"] = _decode(data)
            self._entry["bytes"] = len(data)
            self._sink.flush(self._entry)
        return data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self._resp.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._resp, name)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)


class _RawDumper:
    """把每一次 HTTP 往返写成一行 JSONL。**增量落盘**：崩溃也不会丢已发生的部分。"""

    def __init__(self, path: Path, *secrets: str) -> None:
        self.path = path
        self.secrets = [s for s in secrets if s]
        self.entries: list[dict] = []
        self._fh = path.open("w", encoding="utf-8", newline="\n")

    def _redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***[REDACTED]***")
        return text

    def install(self, provider, label: str) -> None:
        """把 `provider._urlopen` 换成"先记账再转发"的版本。"""
        real = provider._urlopen

        def wrapped(req, *args, **kwargs):
            entry: dict[str, Any] = {
                "label": label,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "url": getattr(req, "full_url", ""),
                "method": getattr(req, "get_method", lambda: "?")(),
                "request": _decode(getattr(req, "data", b"") or b""),
                "headers": {
                    key: ("***[REDACTED]***" if key.lower() == "authorization" else value)
                    for key, value in dict(getattr(req, "headers", {}) or {}).items()
                },
            }
            try:
                resp = real(req, *args, **kwargs)
            except BaseException as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
                self.flush(entry)
                raise
            return _CapturedResponse(resp, entry, self)

        provider._urlopen = wrapped

    def flush(self, entry: dict) -> None:
        for key in ("request", "response"):
            if isinstance(entry.get(key), str):
                entry[key] = self._redact(entry[key])
        self.entries.append(entry)
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    # ------------------------------------------------------------ 分类计数
    def count(self, needle: str) -> int:
        return sum(1 for e in self.entries if needle in str(e.get("url", "")))

    def counts(self) -> dict[str, int]:
        return {
            "search": self.count("/search"),
            "extract": self.count("/extract"),
            "chat": self.count("chat/completions"),
        }


# --------------------------------------------------------------------------
# Provider 包装：只计数，不改行为
# --------------------------------------------------------------------------
class _CountingChat:
    """委托给真 Provider，只额外数调用次数。

    `provider_name` 必须透传，否则抽取器的幂等键会退化成包装类名（对账会错）。
    """

    def __init__(self, inner) -> None:
        self.inner = inner
        self.provider_name = getattr(inner, "provider_name", type(inner).__name__)
        self.calls = 0

    def chat(self, ctx, req):
        self.calls += 1
        return self.inner.chat(ctx, req)

    def search(self, ctx, req):
        return self.inner.search(ctx, req)

    def extract(self, ctx, req):
        return self.inner.extract(ctx, req)

    def usage(self, ctx, scope):
        return self.inner.usage(ctx, scope)

    def health(self, ctx):
        return self.inner.health(ctx)

    def capabilities(self):
        return self.inner.capabilities()


def _forbidden(*_args, **_kwargs):
    raise AssertionError("--dry 模式禁止任何网络调用（本应无人调用 _urlopen）")


def _judge_probe(*, evidence: list[dict], sources: list[dict],
                 probe_info: dict, targets: list) -> tuple[str, list[str]]:
    """判定强判别探针：`(verdict, lines)`，verdict ∈ `OK / FAIL / WARN`。

    R4.6 的主体 28/28 有个致命弱点：24 条所在材料满足"检索目标 = 内容主体"，
    **一个仍然按材料目标整份填的坏模型也能满分**。探针把"宝玉的说法"放进
    "黛玉侧材料"，此时两种填法第一次分叉。

    三分支各自的含义（都必须是可解释的结论，不是"大概"）：
    - `OK`  ：该源里提到宝玉的证据**挂给了宝玉** ⇒ 主体按内容走；
    - `FAIL`：提到了但没挂给宝玉 ⇒ 错绑复现，D-288 不成立；
    - `WARN`：探针句**没被抽成 claim** ⇒ 本轮没测到（不是通过，也不是失败）。
    """
    by_name = {t.display_name: t.entity_id for t in targets}
    display_of = {t.entity_id: t.display_name for t in targets}
    lines: list[str] = []
    about_name = str(probe_info.get("about") or "")
    about_id = by_name.get(about_name)
    probe_sources = [s for s in sources if (s.get("url") or "") == probe_info.get("url")]
    if not probe_sources:
        lines.append(f"  [WARN] 注入的那份材料没进来源表（{probe_info.get('url')}）："
                     "本轮探针无效")
        return "WARN", lines
    if about_id is None:
        lines.append(f"  [WARN] `--probe-about {about_name}` 不是本次目标：无法判定")
        return "WARN", lines

    source_id = probe_sources[0].get("id") or probe_sources[0].get("source_id")
    lines.append(f"  探针材料：{source_id}（属于「{probe_info.get('target')}」的检索结果）")
    from_source = [e for e in evidence if e.get("source_id") == source_id]
    mentioning = [e for e in from_source
                  if about_name and about_name in str(e.get("claim") or "")]
    lines.append(f"  该源证据 {len(from_source)} 条，其中提到「{about_name}」的 "
                 f"{len(mentioning)} 条")
    right = [e for e in mentioning if about_id in (e.get("subject_entity_ids") or [])]
    wrong = [e for e in mentioning if about_id not in (e.get("subject_entity_ids") or [])]
    for item in mentioning:
        subject_names = [display_of.get(s, s)
                         for s in item.get("subject_entity_ids") or []]
        lines.append(f"    · {str(item.get('claim'))[:60]}")
        lines.append(f"      主体={subject_names}")
    if right and not wrong:
        lines.append(f"  [OK ] 结论：来自「{probe_info.get('target')}」侧材料的"
                     f"「{about_name}」说法**挂给了 {about_name}**"
                     "（按检索目标整份填的模型会挂错）")
        return "OK", lines
    if wrong:
        lines.append(f"  [FAIL] 结论：来自「{probe_info.get('target')}」侧材料的"
                     f"「{about_name}」说法**没有挂给 {about_name}**"
                     "——主体错绑复现，D-288 的修复不成立")
        return "FAIL", lines
    lines.append("  [WARN] 结论：探针句没有被抽成 claim（模型没选中这句话），"
                 "本轮无法判定主体映射——可换探针句/调 --probe-text 再试")
    return "WARN", lines


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P4.8 R4 真实检索 e2e（会真实计费）")
    parser.add_argument("--dry", action="store_true",
                        help="只做预检与清单，**物理断网**（不发出任何请求）")
    parser.add_argument("--targets", default=DEFAULT_TARGETS,
                        help=f"逗号分隔的目标名（默认 {DEFAULT_TARGETS}）")
    parser.add_argument("--work-title", default=DEFAULT_WORK_TITLE,
                        help=f"作品名，会拼进检索词（默认 {DEFAULT_WORK_TITLE}）")
    parser.add_argument("--adaptation", default=DEFAULT_ADAPTATION,
                        help=f"改编版本（默认 {DEFAULT_ADAPTATION}）")
    parser.add_argument("--out", default=None,
                        help="原始响应与库文件目录（默认 %%TEMP%%/gps-p48-r4）")
    parser.add_argument("--max-rounds", type=int, default=1,
                        help="对话轮数上限（默认 1；每轮 1 个 turn，故对话调用数 = 轮数）")
    parser.add_argument("--no-dotenv", action="store_true", help="忽略 .env")
    parser.add_argument("--report-only", action="store_true",
                        help="**不发任何请求**：只读取上次运行的产物重新出报告")
    parser.add_argument("--vocabulary", default="l1",
                        choices=["l1", "l2-zh-classical"],
                        help="受控特征标签词表：l1（默认，31 个）/ "
                             "l2-zh-classical（+11 个中文古典标签，"
                             "含 SPEECH_STYLE 族；D-286）")
    #: D-290：固定材料集。用上一次运行的检索/抓取往返做回放，
    #: 这样"同一个实验跑两次"才真的可比（R4.5 与 R4.6 同词同参只有 5/10 源重合）。
    parser.add_argument("--replay-materials", default=None, metavar="DIR",
                        help="回放该目录下 raw_responses.jsonl 里的检索/抓取往返："
                             "物理不联网、不计费（模型仍真实调用）")
    parser.add_argument("--probe-target", default=None, metavar="NAME",
                        help="强判别探针：往该目标的检索结果里追加一句话"
                             "（需配合 --replay-materials），用于测"
                             "『材料主体 ≠ 检索目标』时主体映射是否仍正确")
    parser.add_argument("--probe-index", type=int, default=-1,
                        help="探针注入第几条结果（默认 -1 最后一条：最不影响"
                             "『抓取哪一条』的排序，避免回放缺项）")
    parser.add_argument("--probe-about", default=None, metavar="NAME",
                        help="探针那句话讲的是谁（用来判定主体是否挂对）")
    parser.add_argument("--probe-text", default=None,
                        help="探针句（缺省用一句明确的宝玉说法）")
    #: D-293：抽取温度。**默认 None = 生产值 0.7**（`DEFAULT_EXTRACT_TEMPERATURE`）。
    #: R4.8 用 `0` 验"同材料同输入是否同输出"——这是实验配置，不是产品默认。
    #: 对话温度**不受此参数影响**（独立旋钮，见 `DIALOGUE_TEMPERATURE`）。
    parser.add_argument("--extract-temperature", type=float, default=None,
                        metavar="T",
                        help="抽取调用温度（0–2）。不传 = 生产默认 0.7；"
                             "R4.8 用 0 做可复现性验证。对话温度不受影响")
    args = parser.parse_args(argv)

    if args.probe_target and not args.replay_materials:
        print("--probe-target 必须配合 --replay-materials：探针只能注入缓存材料，"
              "不能凭空改真实检索结果。")
        return 2
    if args.probe_about and not args.probe_target:
        print("--probe-about 必须配合 --probe-target。")
        return 2
    if args.probe_target and not args.probe_about:
        print("--probe-target 必须同时给 --probe-about（否则无法判定主体是否挂对）。")
        return 2

    vocabulary = vocabulary_by_name(args.vocabulary)
    if vocabulary is None:                    # choices 已挡住，防御性兜底
        print(f"未知词表：{args.vocabulary}")
        return 2

    import os
    import tempfile

    base_dir = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "gps-p48-r4"

    if args.report_only:
        # 只读产物：不开 SqliteStorage（会写 schema），也不读密钥。
        return _report_from_artifacts(base_dir / "real" / "persona.db",
                                      base_dir / "real" / "raw_responses.jsonl")

    out_dir = base_dir / ("dry" if args.dry else "real")
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = out_dir / "persona.db"
    raw_path = out_dir / "raw_responses.jsonl"
    if db_path.exists() or raw_path.exists():
        print(f"拒绝运行：{out_dir} 里已有上一次的产物（{db_path.name} / {raw_path.name}）。")
        print("  请换一个 --out 目录，或先手工确认后删除——脚本不替你删数据。")
        return 2

    #: 本次运行**显式覆盖**的配置（不写回 .env）：
    #: `.env` 里没有 `PERSONA_PROVIDER`，默认 `fake` —— 即"检索出口默认不联网"。
    #: 真实检索 e2e 必须显式指定 tavily，且只在这一次进程里生效。
    RUN_OVERRIDES = {"PERSONA_PROVIDER": "tavily"}

    loaded = load_settings(use_dotenv=not args.no_dotenv, overrides=RUN_OVERRIDES)
    if is_err(loaded):
        print("配置加载失败：", error_of(loaded))
        return 2
    settings = loaded.value

    out = Out(str(settings.secrets.provider_api_key or ""),
              str(getattr(settings.secrets, "search_api_key", "") or ""))
    out("P4.8 R4：真实检索 e2e")
    if args.replay_materials:
        out("模式：回放运行（材料来自缓存，检索/抓取**物理不联网、不计费**；"
            "模型仍真实调用）")
    else:
        out(f"模式：{'DRY-RUN（物理断网，不发出任何请求）' if args.dry else '真实运行（会产生费用）'}")
    out(f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    out(f"目录：{out_dir}")
    out(f"隔离库：{db_path}")
    if args.replay_materials:
        out(f"材料缓存：{args.replay_materials}")
    out(f"本次覆盖：PERSONA_PROVIDER=tavily（不写回 .env；.env 里没有该项，默认 fake）")

    # ---------------- 1. 配置核验 ----------------
    hr(out, "[1] 配置核验（不发请求）")
    plan = model_plan(settings)
    limits = model_limits_from_settings(settings)
    cfg = chat_provider_config(settings)
    search_key = str(getattr(settings.secrets, "search_api_key", "") or "")
    problems: list[str] = []

    def expect(cond: bool, desc: str) -> None:
        out(f"  [{'OK ' if cond else 'FAIL'}] {desc}")
        if not cond:
            problems.append(desc)

    expect(settings.offline_only is False, "offline_only = false（允许联网）")
    expect(plan["provider"] == "deepseek", "模型服务商 = deepseek")
    expect(plan["max_calls"] == 3, "模型调用上限 = 3（本次唯一配置改动）")
    expect(limits.max_cost == 0.25, "模型费用上限 = 0.25 USD（未改）")
    expect(cfg.max_retries == 0, "模型 Provider max_retries = 0")
    expect(bool(settings.secrets.provider_api_key), "模型密钥已设置（只看存在性）")
    expect(settings.provider_name == "tavily", f"检索服务商 = {settings.provider_name}")
    expect(bool(search_key), "检索密钥已设置（只看存在性）")
    expect(int(settings.research.max_queries) >= EXPECTED_SEARCHES,
           f"research.max_queries = {settings.research.max_queries}（≥ 2）")
    expect(int(settings.research.max_extracts) == EXPECTED_EXTRACTS_INITIAL,
           f"research.max_extracts = {settings.research.max_extracts}"
           f"（= {EXPECTED_EXTRACTS_INITIAL}）")
    expect(int(settings.research.max_remediations) == EXPECTED_REMEDIATIONS_MAX,
           f"research.max_remediations = {settings.research.max_remediations}"
           f"（= {EXPECTED_REMEDIATIONS_MAX}；补救额度独立于初始抓取）")
    if problems:
        out("")
        out("预检未通过：**不发出任何请求**。")
        return 3

    # ---------------- 2. 装配 ----------------
    hr(out, "[2] 装配（只构造，不联网）")
    storage = SqliteStorage(db_path)
    ctx = new_call_context(project_id=PROJECT, actor=ACTOR, task_id=TASK_ID)

    built_chat = chat_provider_from_settings(settings)
    if is_err(built_chat):
        out("  [FAIL] 模型 Provider 构造失败：", error_of(built_chat))
        storage.close()
        return 3
    real_chat = built_chat.value
    chat = _CountingChat(real_chat)

    # `allow_real=True` 在两种模式下都给：这里**只构造**真 Provider，不发请求。
    # dry 模式的安全性不靠"不构造"，而靠下面把 `_urlopen` 换成必然失败的替身
    # （构造是纯内存动作，装配层与测试都已证明它不联网）。
    built = build_business_stack(
        settings, ctx,
        storage=storage,
        chat_provider=chat,
        dialogue_provider=chat,          # 同一个真 Provider：清单里 2 次对话就走它
        allow_real=True,
        options=StackOptions(
            task_id=TASK_ID,
            hydrate_ledger=False,        # 全新隔离库：hydrate 没有意义
            build_search_provider=True,  # 清单必须反映"真的会检索"
            research_limits=settings.research,
            extraction_strategy=TopNExtractionStrategy(),
            search_metadata={"r4_task_id": TASK_ID},
            #: D-286：词表**两侧同一份**（抽取器与编排器）——只喂一边会让
            #: 抽取侧按 L2 贴标签、编排侧按 L1 解析家族，板块会归错。
            trait_vocabulary=vocabulary,
            #: D-293：抽取温度。`None` ⇒ 生产默认 0.7；R4.8 用 `--extract-temperature 0`
            #: 验"同材料同输入是否同输出"。对话温度**不在这里**（独立旋钮，仍 0.7）。
            extract_temperature=args.extract_temperature,
        ),
    )
    if is_err(built):
        out("  [FAIL] 业务链装配失败：", error_of(built))
        storage.close()
        return 3
    stack = built.value
    for note in stack.warnings:
        out(f"  装配告警：[{note.code}] {note.message}")

    if args.dry:
        # 物理断网：即使后面误发请求，也只会在这里炸掉，不会真的出去。
        real_chat._urlopen = _forbidden
        guarded = getattr(stack.research.deps, "provider", None)
        if guarded is not None and hasattr(guarded, "_urlopen"):
            guarded._urlopen = _forbidden
        out("  已安装断网守卫：--dry 模式下任何网络调用都会立刻失败")

    names = [n.strip() for n in args.targets.split(",") if n.strip()]
    work_title = args.work_title
    adaptation = args.adaptation
    specs = [TargetSpec(n, "FICTIONAL_CHARACTER", work_title, adaptation)
             for n in names]
    targets = unwrap(register_targets(storage, ctx, specs, project_id=PROJECT))
    out(f"  目标：{[(t.display_name, t.entity_id) for t in targets]}")
    out(f"  作品：{work_title} / {adaptation}")
    out(f"  抽取器：{type(stack.claim_extractor).__name__ if stack.claim_extractor else '无'}")
    out(f"  关系分析器：{type(stack.relationship_analyzer).__name__ if stack.relationship_analyzer else '无'}")
    out(f"  词表：{args.vocabulary}"
        f"（{len(vocabulary.all_tags())} 个标签，启用维度 "
        f"{sorted(vocabulary.enabled_dimensions())}，"
        f"指纹 {vocabulary.fingerprint()}）")
    #: D-293：两个温度**分开报**，避免"改了对话却以为在改抽取"（或反之）。
    extract_temperature = getattr(stack.claim_extractor, "temperature", None)
    dialogue_temperature = getattr(stack.dialogue, "temperature", None)
    out(f"  抽取温度：{extract_temperature}"
        f"（{'生产默认' if args.extract_temperature is None else '实验配置 —— 非产品默认'}；"
        f"产品默认 {DEFAULT_EXTRACT_TEMPERATURE}）")
    out(f"  对话温度：{dialogue_temperature}（独立旋钮，默认 "
        f"{DIALOGUE_TEMPERATURE}，不随 --extract-temperature 变）")

    #: D-290：先把缓存读好（不联网）。读不到就在发请求之前停——
    #: 不能等跑到一半才发现"回放缺项"，那时候钱已经花在模型上了。
    replay_cache = None
    probe_info: dict[str, Any] | None = None
    if args.replay_materials:
        try:
            replay_cache = load_replay_cache(args.replay_materials)
            if args.probe_target:
                probe_text = args.probe_text or (
                    "贾宝玉自幼厌恶科举功名，把读书上进的人称作禄蠹，"
                    "从不肯走仕途经济那条路。")
                replay_cache, probe_url, placement = replay_cache.inject_probe(
                    query_contains=args.probe_target, text=probe_text,
                    index=args.probe_index)
                probe_info = {"target": args.probe_target, "about": args.probe_about,
                              "text": probe_text, "url": probe_url,
                              "index": args.probe_index, "placement": placement}
        except MaterialReplayError as exc:
            out(f"  [FAIL] 材料缓存不可用：{exc}")
            storage.close()
            return 3
        counts = replay_cache.counts()
        out(f"  材料缓存：{counts['search']} 次检索 / {counts['extract']} 次抓取"
            f"（回放，不联网）")
        if probe_info is not None:
            out(f"  强判别探针：往「{probe_info['target']}」侧材料注入一句"
                f"关于「{probe_info['about']}」的说法")
            where = ("抓取全文末尾" if probe_info["placement"] == "extract_fulltext"
                     else f"检索摘要第 {probe_info['index']} 条")
            out(f"     注入到（{where}）：{probe_info['url']}")
            out(f"     探针句：{probe_info['text']}")

    # ---------------- 3. 调用清单 ----------------
    hr(out, "[3] 调用清单（= 授权依据）")
    call_plan = plan_calls(stack, targets, max_rounds=args.max_rounds)
    for line in call_plan.as_lines():
        out(line)
    if replay_cache is not None:
        out("  说明：回放模式下检索/抓取**不产生真实费用**；上面的 credits 只是"
            "清单上限，不是本次支出（模型调用仍按实际计费）")

    #: D-4 之后的口径：1 次批量抽取 + `max_rounds` 次对话（每轮 1 个 turn）。
    expected_model_calls = EXPECTED_MODEL_CALLS_FIXED + max(0, int(args.max_rounds))
    expected_model_usd = (EXPECTED_EST_MODEL_USD_PER_CALL * expected_model_calls
                          if expected_model_calls else 0.0)
    planned_model = call_plan.extractions + call_plan.dialogues
    expect(call_plan.searches == EXPECTED_SEARCHES,
           f"检索次数 = {EXPECTED_SEARCHES}（实际计划 {call_plan.searches}）")
    expect(call_plan.extracts_upper_bound == EXPECTED_EXTRACTS_INITIAL,
           f"初始抓取上限 = {EXPECTED_EXTRACTS_INITIAL}"
           f"（实际 {call_plan.extracts_upper_bound}）")
    expect(planned_model == expected_model_calls,
           f"模型调用 = {expected_model_calls}（1 抽取 + {args.max_rounds} 轮对话；"
           f"实际 {planned_model}）")
    if call_plan.est_model_cost is not None:
        expect(abs(call_plan.est_model_cost - expected_model_usd) < 1e-6,
               f"预估模型费用 ≈ {expected_model_usd:.4f} USD"
               f"（实际 {call_plan.est_model_cost:.6f}）")
    if call_plan.est_search_credits is not None:
        expect(abs(call_plan.est_search_credits - EXPECTED_EST_SEARCH_CREDITS) < 1e-6,
               f"预估检索 ≈ {EXPECTED_EST_SEARCH_CREDITS} credits"
               f"（实际 {call_plan.est_search_credits:g}）")

    #: D-272：输出上限不再写死 4096（R4.2 就是撞死在它上面，21 条 claims 全废）。
    #: 现在按材料数动态算，夹在 [下限, 上限] 之间——把公式打出来供核对。
    out("")
    out("  抽取输出上限（D-272 动态，不再写死 4096）：")
    out(f"    · 每份材料条数上限 N = {DEFAULT_MAX_CLAIMS_PER_MATERIAL}"
        f"（提示词硬约束，PROMPT_VERSION={PROMPT_VERSION}）")
    for n in (1, 5, 9, 12, 20):
        cap = estimate_output_tokens(n)
        claims = (cap - OUTPUT_TOKEN_OVERHEAD) // TOKENS_PER_CLAIM
        enough = "装得下" if output_ceiling_is_sufficient(n) else "**装不下**"
        out(f"    · {n:2d} 份材料 → max_output_tokens={cap:5d}"
            f"（约容纳 {claims} 条 claim，{enough}）")
    out(f"    · 夹取区间 [{OUTPUT_TOKEN_FLOOR}, {OUTPUT_TOKEN_CEILING}]；"
        f"{OUTPUT_TOKEN_CEILING} 是 DeepSeek 官方 max_tokens 上限")

    planned_queries = stack.research.plan_queries(ctx, targets)
    out("")
    if is_ok(planned_queries):
        out(f"  将要发出的检索词（{len(planned_queries.value)} 条）：")
        for q in planned_queries.value:
            out(f"    - {q.query_text!r}（intent={q.intent}，"
                f"targets={q.target_entity_ids}）")
        expect(len(planned_queries.value) == EXPECTED_SEARCHES,
               f"检索词条数 = {len(planned_queries.value)}（清单写的是 2 次检索）")
    else:
        out("  [FAIL] 检索计划生成失败：", error_of(planned_queries))

    if problems:
        out("")
        out("清单与预估**不一致**：按纪律不执行。")
        storage.close()
        return 3

    if args.dry:
        hr(out, "[4] DRY-RUN 结束")
        out("  预检与清单全部通过；本模式把 `_urlopen` 换成必然失败的替身，")
        out("  所以**物理上不可能**发出请求。去掉 --dry 才会真实运行。")
        storage.close()
        return 0

    # ---------------- 4. 真实运行 ----------------
    hr(out, "[4] 真实运行（五阶段）")
    dumper = _RawDumper(raw_path, str(settings.secrets.provider_api_key or ""), search_key)
    dumper.install(real_chat, "chat")
    search_provider = stack.research.deps.provider
    search_provider_name = type(search_provider).__name__
    if search_provider_name == "FakeProvider":
        out("  [FAIL] 检索 Provider 是 FakeProvider：装配没接上真实出口，停止。")
        dumper.close()
        storage.close()
        return 3
    dumper.install(search_provider, "search")
    out(f"  检索 Provider：{search_provider_name}（已挂原始响应抄录）")
    out(f"  模型 Provider：{chat.provider_name}（已挂原始响应抄录）")

    replay_session = None
    if replay_cache is not None:
        #: 装在上面的抄录之后：回放直接换掉 `_urlopen`，物理上没有网络可走。
        #: `isolate_usage` 让 provider 用一本空账本，回放的 credits 不进真账。
        replay_session = install_replay(search_provider, replay_cache)
        out("  回放已安装：检索/抓取走缓存，**缺项即报错**（绝不回落到网络）；"
            "回放不记账")
    out("  → 开始，逐阶段见下。")

    started = time.monotonic()
    try:
        result = stack.orchestrator.run(
            ctx, BusinessRequest(targets=targets, max_rounds=args.max_rounds))
    except MaterialReplayMiss as exc:
        dumper.close()
        out(f"  [FAIL] 回放缺项，已停止：{exc}")
        out("  这**不是**产品缺陷：说明本次请求与缓存不是同一批材料（检索词/参数变了）。")
        storage.close()
        return 4
    elapsed = time.monotonic() - started
    dumper.close()
    out(f"  ← 结束，耗时 {elapsed:.2f} s")

    if is_err(result):
        out(f"  编排器返回 Err（{result.error.code}）：{result.error.message}")
        out(f"  原始响应已落盘：{raw_path}")
        storage.close()
        return 4
    run = result.value

    # ---------------- 5. 阶段状态 ----------------
    hr(out, "[5] 五阶段状态")
    out(f"  整体状态：{run.status}（task={run.task_id} / {run.task_status}）")
    for step in run.steps:
        out(f"  - {step.name:14s} {step.status}")
        if step.message:
            out(f"      {step.message}")
        for note in step.warnings:
            out(f"      告警 [{note.code}] {note.message}")

    # ---------------- 6. 调用 vs 清单 ----------------
    hr(out, "[6] 实际调用 vs 清单")
    actual = dumper.counts()
    if replay_session is not None:
        actual = {
            "search": replay_session.hits("search"),
            "extract": replay_session.hits("extract"),
            "chat": actual["chat"],
        }
        out(f"  检索/抓取：**回放命中** search={actual['search']}"
            f" / extract={actual['extract']}（物理不联网、不计费；"
            f"未命中 {replay_session.miss_count()} 次）")
    out(f"  检索（/search）      : 实际 {actual['search']} / 清单 {EXPECTED_SEARCHES}")
    out(f"  抓取（/extract）     : 实际 {actual['extract']} / 清单 ≤{EXPECTED_EXTRACTS_MAX}"
        f"（{EXPECTED_EXTRACTS_INITIAL} 初始 + {EXPECTED_REMEDIATIONS_MAX} 补救）")
    out(f"  模型（chat/completions）: 实际 {actual['chat']} / 清单 ≤{expected_model_calls}")
    out(f"  Provider 内部计数    : chat.calls = {chat.calls}")
    violations: list[str] = []
    if actual["search"] > EXPECTED_SEARCHES:
        violations.append(f"检索超清单：{actual['search']} > {EXPECTED_SEARCHES}")
    if actual["extract"] > EXPECTED_EXTRACTS_MAX:
        violations.append(f"抓取超清单：{actual['extract']} > {EXPECTED_EXTRACTS_MAX}")
    if actual["chat"] > expected_model_calls:
        violations.append(f"模型超清单：{actual['chat']} > {expected_model_calls}")
    if chat.calls != actual["chat"]:
        violations.append(f"计数不一致：Provider 计数 {chat.calls} ≠ 实际往返 {actual['chat']}")
    out(f"  结论：{'**清单外调用出现**——' + '；'.join(violations) if violations else '全部在清单内'}")
    for entry in dumper.entries:
        out(f"    · [{entry['label']}] {entry['url']}  (HTTP {entry.get('error') or 'OK'})")

    # ---------------- 7. 来源与门禁 ----------------
    hr(out, "[7] 来源与正文质量门禁")
    sources = _query_payloads(storage, ctx, "Source")
    out(f"  来源数：{len(sources)}")
    for src in sources:
        out(f"  - {src.get('id') or src.get('source_id')}  {src.get('title')!r}")
        out(f"      url={src.get('url')}")
        out(f"      quality_status={src.get('quality_status')!r}"
            f"  excerpt_chars={len(str(src.get('excerpt') or ''))}")

    research_step = run.step_of("RESEARCH")
    if research_step is not None:
        for note in research_step.warnings:
            out(f"  研究告警 [{note.code}] {note.message}")

    # ---------------- 8. 证据 ----------------
    hr(out, "[8] 证据")
    evidence = _query_payloads(storage, ctx, "EvidenceItem")
    out(f"  证据数：{len(evidence)}")
    for item in evidence[:12]:
        out(f"  - {item.get('id')}  tier={item.get('tier')} dim={item.get('dimension')}")
        out(f"      claim={str(item.get('claim'))[:110]}")
        out(f"      stability={item.get('stability')}"
            f" verdict={item.get('stability_verdict')}"
            f" effective={item.get('effective_stability')}")
    if len(evidence) > 12:
        out(f"  ……（其余 {len(evidence) - 12} 条略）")

    # ---------------- 8.1 强判别探针 ----------------
    #: 只有当"材料主体 ≠ 检索目标"时，才能区分"按内容填主体"与"按检索目标整份填"。
    #: R4.6 的材料恰好"目标=内容"，坏模型也能满分（见报告 §2.2），所以这里主动造一个：
    #: 往黛玉侧材料里塞一句明确的宝玉说法，看它挂给谁。
    probe_verdict: str | None = None
    if probe_info is not None:
        hr(out, "[8.1] 强判别探针（材料主体 ≠ 检索目标）")
        probe_verdict, probe_lines = _judge_probe(
            evidence=evidence, sources=sources, probe_info=probe_info, targets=targets)
        for line in probe_lines:
            out(line)

    # ---------------- 9. 档案 ----------------
    hr(out, "[9] 档案与关系")
    profiles = _query_payloads(storage, ctx, "PersonaProfile")
    out(f"  档案数：{len(profiles)}")
    for prof in profiles:
        out(f"  - entity={prof.get('entity_id')} version={prof.get('version')}"
            f" status={prof.get('status')}")
        for key in ("background", "traits", "single_events", "habits",
                    "expression_style", "experiences"):
            count, texts = _describe_items(prof.get(key))
            out(f"      {key:16s}: {count}")
            for text in texts[:5]:
                out(f"        · {text[:90]}")
    relationships = _query_payloads(storage, ctx, "RelationshipProfile")
    out(f"  关系档案数：{len(relationships)}")
    for rel in relationships:
        out(f"  - {rel.get('id')}  cardinality={rel.get('cardinality')}"
            f" status={rel.get('status')}")
        for key in ("perspectives", "shared_experiences", "interaction_patterns",
                    "emotional_expression_habits", "temporal_changes",
                    "asymmetry_notes", "open_questions"):
            count, texts = _describe_items(rel.get(key))
            out(f"      {key:26s}: {count}")
            for text in texts[:3]:
                out(f"        · {text[:90]}")
    turns = _query_payloads(storage, ctx, "ConversationTurn")
    out(f"  对话轮次：{len(turns)}")
    names = {t.entity_id: t.display_name for t in targets}
    for turn in turns[:6]:
        #: 字段名必须与 `ConversationTurn` 一致：`speaker_entity_id` / `content`
        #: （此前后报告器读 `speaker` / `text`，恒为 None/空 —— R4.1 实测暴露）。
        sid = turn.get("speaker_entity_id")
        rule = (turn.get("selection_reason") or {}).get("rule")
        out(f"  - {turn.get('id')} turn#{turn.get('index')}"
            f" speaker={names.get(sid, sid)}"
            + (f"（选定规则 {rule}）" if rule else ""))
        out(f"      {str(turn.get('content') or '')[:100]}")
        refs = (len(turn.get("referenced_persona_item_ids") or [])
                + len(turn.get("referenced_relationship_item_ids") or []))
        out(f"      引用：persona {len(turn.get('referenced_persona_item_ids') or [])}"
            f" / relation {len(turn.get('referenced_relationship_item_ids') or [])}"
            f" / memory {len(turn.get('generated_memory_ids') or [])}（合计 {refs}）")

    # ---------------- 10. 账本 ----------------
    hr(out, "[10] 逐笔账本（内存账本 + 落库记录）")
    for rec in stack.ledger.records():
        out(f"  - {rec.provider}/{rec.operation}  key={rec.idempotency_key}")
        out(f"      outcome={rec.outcome}  request_id={rec.request_id}")
        out(f"      estimate={rec.cost_estimate}  confirmed={rec.cost_confirmed}"
            f"  status={rec.cost_status}  unit={rec.currency}")
    totals = stack.ledger.totals()
    out(f"  记录数={totals.requests}  已确认={totals.confirmed_cost}"
        f"  估算={totals.estimated_cost:.6f}  未知费用笔数={totals.unknown_requests}")
    if replay_session is not None:
        out("  回放说明：检索/抓取走缓存，**不产生真实计费**；"
            "provider 已换用隔离账本，真账本里没有检索/抓取行")
    persisted = stack.storage.external_usage_records(ctx)
    if is_ok(persisted):
        out(f"  已落库逐笔记录：{len(persisted.value)} 条")
        for rec in persisted.value:
            out(f"    · {rec.provider}/{rec.operation} request_id={rec.request_id}"
                f" status={rec.cost_status} confirmed={rec.cost_confirmed}"
                f" {rec.currency}")
    else:
        out(f"  [FAIL] 读取落库记录失败：{error_of(persisted)}")

    # ---------------- 11. 安全与缺陷自检 ----------------
    hr(out, "[11] 自检")
    expect(not out.leak, "全部输出行均不含密钥")
    expect(not violations, "无清单外调用")
    #: 用**常量**而不是字面量：常量 `RUN_SUCCEEDED` 的值是 `"SUCCEEDED"`，
    #: 早期写成字面量 `"RUN_SUCCEEDED"` 永不匹配，会把一次成功的运行
    #: 报错成 FAIL（R4.3 实测踩到）。
    expect(run.status in (RUN_SUCCEEDED, RUN_PARTIAL),
           f"任务未失败（状态 {run.status}）")
    if probe_verdict == "FAIL":
        expect(False, "强判别探针：材料主体≠检索目标时主体挂错")
    elif probe_verdict == "WARN":
        out("  [WARN] 强判别探针未得出结论（见 [8.1]，本轮不能据此判主体映射）")
    elif probe_verdict == "OK":
        out("  [OK ] 强判别探针：材料主体≠检索目标时，主体仍按内容挂对")
    if replay_session is not None:
        expect(replay_session.miss_count() == 0,
               f"回放未缺项（命中 {replay_session.unique_hits} 个不同请求，"
               f"未命中 {replay_session.miss_count()}）")

    hr(out, "[12] 结论")
    executed = [s.name for s in run.steps if s.status == "EXECUTED"]
    out(f"  EXECUTED 阶段：{executed}")
    out(f"  原始响应：{raw_path}（{len(dumper.entries)} 次往返）")
    out(f"  隔离库：{db_path}")
    out(f"  问题：{len(problems)} 条" + (f" → {problems}" if problems else ""))
    storage.close()
    return 0 if not problems and not violations else 5


def _report_from_artifacts(db_path: Path, raw_path: Path) -> int:
    """**只读**上次运行的产物重新出报告（零请求、零费用）。

    存在的理由：真实运行只该发生一次。报告写错了就修报告器再读一遍产物，
    绝不为"把数字打全"再花一次钱。
    """
    import sqlite3

    out = Out()
    if not db_path.exists():
        print(f"找不到产物：{db_path}")
        return 2
    out("P4.8 R4：**只读重放**上次运行的产物（未发出任何请求）")
    out(f"库：{db_path}")
    out(f"原始响应：{raw_path}（存在={raw_path.exists()}）")

    # ---------------- 1. 原始往返 ----------------
    hr(out, "[1] 原始 HTTP 往返（按落盘顺序）")
    entries: list[dict] = []
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    if not entries:
        out("  （没有记录）")
    for index, entry in enumerate(entries, 1):
        state = "ERR" if entry.get("error") else "OK"
        out(f"  {index}. [{entry['label']}] {entry['url']}  {state}"
            f"  bytes={entry.get('bytes')}")
        if entry.get("request"):
            try:
                body = json.loads(entry["request"])
            except ValueError:
                body = {}
            for key in ("query", "search_depth", "max_results", "model"):
                if key in body:
                    out(f"      request.{key} = {body[key]!r}")
        if entry.get("error"):
            out(f"      error = {entry['error'][:160]}")
        response = entry.get("response")
        if response:
            try:
                data = json.loads(response)
            except ValueError:
                data = None
            if isinstance(data, dict):
                for key in ("request_id", "id", "usage"):
                    if key in data:
                        out(f"      response.{key} = {json.dumps(data[key], ensure_ascii=False)}")
                if "results" in data:
                    out(f"      response.results = {len(data['results'])} 条")
                    for item in data["results"][:5]:
                        out(f"        · {str(item.get('title') or '')[:70]}"
                            f"  <{str(item.get('url') or '')[:70]}>")

    # ---------------- 2. 逐笔外部用量 ----------------
    hr(out, "[2] 逐笔外部用量（落库）")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    credits = 0.0
    unknown = 0
    providers: set[str] = set()
    for row in conn.execute("SELECT * FROM external_usage_records ORDER BY requested_at"):
        providers.add(str(row["provider"]))
        out(f"  - {row['provider']}/{row['operation']}  {row['outcome']}")
        out(f"      request_id={row['request_id']}")
        out(f"      estimate={row['cost_estimate']}  confirmed={row['cost_confirmed']}"
            f"  status={row['cost_status']}  unit={row['currency']}")
        out(f"      key={row['idempotency_key'][:60]}")
        if row["cost_status"] == "CONFIRMED" and row["cost_confirmed"] is not None:
            if row["currency"] == "TAVILY_CREDIT":
                credits += float(row["cost_confirmed"])
        elif row["cost_status"] == "UNKNOWN":
            unknown += 1

    # ---------------- 3~7. 业务产物 ----------------
    hr(out, "[3] 来源与正文质量门禁")
    for payload in _rows(conn, "Source"):
        out(f"  - {payload['id']}  {payload.get('title')!r}")
        out(f"      url={payload.get('url')}")
        out(f"      quality_status={payload.get('quality_status')!r}"
            f"  excerpt_chars={len(str(payload.get('excerpt') or ''))}")

    hr(out, "[4] 证据")
    evidence = _rows(conn, "EvidenceItem")
    out(f"  证据数：{len(evidence)}")
    for item in evidence[:10]:
        out(f"  - {item['id']} tier={item.get('tier')} dim={item.get('dimension')}"
            f" stability={item.get('stability')} verdict={item.get('stability_verdict')}")
        out(f"      claim={str(item.get('claim'))[:110]}")

    hr(out, "[5] 档案")
    for payload in _rows(conn, "PersonaProfile"):
        out(f"  - entity={payload.get('entity_id')} version={payload.get('version')}"
            f" status={payload.get('status')}")
        for key in ("background", "traits", "single_events", "habits",
                    "expression_style", "experiences"):
            count, texts = _describe_items(payload.get(key))
            out(f"      {key:16s}: {count}")
            for text in texts[:5]:
                out(f"        · {text[:90]}")
        coverage = payload.get("coverage")
        if isinstance(coverage, dict):
            out(f"      coverage        : {json.dumps(coverage, ensure_ascii=False)[:200]}")
        questions = payload.get("open_questions")
        count, texts = _describe_items(questions)
        out(f"      open_questions  : {count}")
        for text in texts[:3]:
            out(f"        · {text[:90]}")

    hr(out, "[6] 关系档案")
    for payload in _rows(conn, "RelationshipProfile"):
        out(f"  - {payload['id']}  cardinality={payload.get('cardinality')}"
            f"  status={payload.get('status')}"
            f"  participants={len(payload.get('participant_entity_ids') or [])}")
        for key in ("perspectives", "shared_experiences", "interaction_patterns",
                    "emotional_expression_habits", "temporal_changes",
                    "asymmetry_notes", "open_questions"):
            count, texts = _describe_items(payload.get(key))
            out(f"      {key:26s}: {count}")
            for text in texts[:3]:
                out(f"        · {text[:90]}")

    hr(out, "[7] 对话")
    for payload in _rows(conn, "ConversationTurn"):
        out(f"  - turn#{payload.get('index')} speaker={payload.get('speaker_entity_id')}")
        out(f"      {str(payload.get('content') or '')[:120]}")
        out(f"      referenced_persona={len(payload.get('referenced_persona_item_ids') or [])}"
            f" referenced_relationship={len(payload.get('referenced_relationship_item_ids') or [])}")

    # ---------------- 8. 模块用量 ----------------
    hr(out, "[8] 模块用量记录（usage_records）")
    rows = list(conn.execute("SELECT * FROM usage_records ORDER BY created_at"))
    out(f"  记录数：{len(rows)}")
    total_requests = 0
    for row in rows:
        total_requests += int(row["requests"] or 0)
        out(f"  - requests={row['requests']} tokens_in={row['tokens_in']}"
            f" tokens_out={row['tokens_out']} cost={row['cost_estimate']}"
            f" {row['currency']}")
    out(f"  合计 requests（分子系统计，会重复计）：{total_requests}")

    # ---------------- 9. 小结 ----------------
    hr(out, "[9] 费用小结")
    out(f"  检索/抓取已确认：{credits:g} credits")
    out(f"  费用状态未知的请求：{unknown} 笔（失败≠免费，需人工核对账单）")
    chat_in = chat_out = 0
    for entry in entries:
        if entry.get("label") != "chat" or not entry.get("response"):
            continue
        try:
            usage = json.loads(entry["response"]).get("usage") or {}
        except ValueError:
            continue
        chat_in += int(usage.get("prompt_tokens") or 0)
        chat_out += int(usage.get("completion_tokens") or 0)
    out(f"  模型 token：in={chat_in} out={chat_out}")
    if chat_in or chat_out:
        usd = chat_in / 1_000_000 * 0.3 + chat_out / 1_000_000 * 1.2
        out(f"  模型费用（按配置单价 0.3/1.2 每 1M 手算）：{usd:.7f} USD")
    #: D-3 的自证：模型调用**必须**在逐笔账本里留下痕迹。
    #: R4 时这里恒为"没有"，是缺陷；R4.1 起按实际查表给结论，不再写死。
    model_rows = sorted(p for p in providers if p not in ("tavily",))
    if model_rows:
        out(f"  [OK ] 模型调用已落逐笔账本（D-3）：external_usage_records 里有 "
            f"{'/'.join(model_rows)} 的行（不再是 0 条）")
    elif chat_in or chat_out:
        out("  [FAIL] 发生了模型调用，但 external_usage_records 里**没有**模型行"
            "（D-3 回归：对话/抽取必须落库）")
    else:
        out("  说明：本次没有发生模型调用（无材料过门禁 ⇒ 不调用，符合纪律）")
    conn.close()
    return 0


def _rows(conn, kind: str) -> list[dict]:
    """读某个 kind 的全部记录，把 payload 摊平并补上 `id`。"""
    items: list[dict] = []
    for row in conn.execute("SELECT * FROM records WHERE kind=?", (kind,)):
        payload = json.loads(row["payload"])
        payload.setdefault("id", row["id"])
        items.append(payload)
    return items


def _describe_items(value: Any) -> tuple[int, list[str]]:
    """把档案字段读成「几个条目 + 文本列表」，**两种形状都认**。

    档案里有的字段是列表（`traits`/`habits`），有的是单个条目
    （`background`/`expression_style` 是 `{text, confidence, evidence_refs…}`）。
    一律按列表切片的写法会在单个条目上抛 `KeyError: slice(...)`——
    R4 第一次真实运行就是这么崩在报告阶段的（数据没坏，是打印坏了）。
    """
    if value is None:
        return 0, []
    if isinstance(value, str):
        return (1, [value]) if value.strip() else (0, [])
    if isinstance(value, dict):
        text = str(value.get("text") or "")
        return (1 if text else 0), ([text] if text else [])
    if isinstance(value, (list, tuple)):
        texts: list[str] = []
        for entry in value:
            if isinstance(entry, dict):
                text = str(entry.get("text") or entry.get("summary") or "")
            else:
                text = str(entry)
            if text:
                texts.append(text)
        return len(value), texts
    return 0, []


def _query_payloads(storage: SqliteStorage, ctx, kind: str,
                    limit: int = 200) -> list[dict]:
    """按 kind 读出记录（payload 里带 `id`，便于报告直接引用）。"""
    page = storage.query(ctx, kind, Query(limit=limit))
    if is_err(page):
        return []
    items: list[dict] = []
    for row in page.value.items:
        payload = dict(row.get("payload") or {})
        payload.setdefault("id", row.get("id"))
        payload["_created_at"] = row.get("created_at")
        items.append(payload)
    return items


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
