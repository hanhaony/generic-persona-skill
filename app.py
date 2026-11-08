# -*- coding: utf-8 -*-
"""大观园 · 专属人物模拟器 —— generic-persona-skill v1.0.0 的 Streamlit 薄壳。

定位与边界（读代码前先看这三条）：
1. **本文件是薄壳**：所有业务逻辑（建档案 / 预算闸门 / 授权确认 / 记忆写回 /
   模拟隔离）全部在 `persona` 引擎里，本文件只做三件事——收集参数、经
   `subprocess` 调 `python -m persona run|chat`、把 CLI 输出渲染得好看一点。
   引擎仍是零第三方依赖；streamlit 只是这层壳的依赖。
2. **密钥安全**：侧边栏填的 API Key 只存在于本机内存与子进程环境变量里，
   不落盘、不写进任何文件、不出现在对话记录中（引擎侧设计：密钥只认环境变量）。
3. **诚实边界**：对话的跨进程连续性靠引擎的「跨会话记忆」（`--topic` 锚点 +
   `--memory-write rule`，只记你的原话）；角色互聊内容按引擎红线绝不进长期记忆。
   自定义 Base URL / OpenAI 网关需要核心装配层开放配置，v1.0.0 仅登记 DeepSeek。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import streamlit as st

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
PY_EXE = sys.executable


def _resolve_data_dir() -> Path:
    """数据目录三级解析：显式环境变量 > 本地 var/webapp（可写时）> 会话临时目录。

    Streamlit Community Cloud 的容器除 /tmp 外基本只读，且多个访客共享文件
    系统——本地目录探测写失败时回退到 tempfile.mkdtemp() 的会话级目录：
    ① 云端可写；② 每个访客一个独立目录，不跨用户串数据。
    ⚠️ 不能每轮 rerun 都 mkdtemp：目录句柄必须缓存进 st.session_state，
    否则第二次交互就是空库（档案"消失"）。
    """
    env_dir = os.environ.get("PERSONA_WEBAPP_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    probe = REPO_ROOT / "var" / "webapp"
    try:
        probe.mkdir(parents=True, exist_ok=True)
        marker = probe / ".write_probe"
        marker.touch()
        marker.unlink()
        return probe
    except OSError:
        try:
            cached = st.session_state.get("webapp_data_dir")
        except Exception:  # noqa: BLE001 —— 无脚本运行上下文（如纯导入）时不炸
            cached = None
        if cached:
            return Path(str(cached))
        data_dir = Path(tempfile.mkdtemp(prefix="persona-webapp-"))
        try:
            st.session_state["webapp_data_dir"] = str(data_dir)
        except Exception:  # noqa: BLE001
            pass
        return data_dir


DATA_DIR = _resolve_data_dir()                     # 云端回退临时目录，本地仍是 var/webapp
AVATAR_DIR = DATA_DIR / "avatars"
# —— 多会话（2026-11-05 P1，微信式多开）——
# 每个会话一个**独立 DB**：引擎预算是全库累计（external_usage_records 不分
# project），只传不同 --project 挡不住预算串台；独立 DB 让预算、长期记忆、
# 人物档案三层全部物理隔离。会话元数据（群名/人名/聊天记录/project_id）
# 另存 JSON，刷新页面后恢复列表。云端 /tmp 目录随容器生命周期，属平台限制。
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_FILE = DATA_DIR / "web_sessions.json"

DEFAULT_GROUP_NAME = "大观园日常"
DEFAULT_API_BASE = "https://api.deepseek.com"
CHAT_COST_PER_CALL_USD = 0.0005                   # 引擎实测口径（dialogue.py 常量）

PROVIDER_PRESETS = {
    "DeepSeek": {"base_url": DEFAULT_API_BASE, "model_id": "deepseek-chat"},
    "OpenAI": {"base_url": "https://api.openai.com/v1", "model_id": "gpt-4o-mini"},
    "自定义": {"base_url": DEFAULT_API_BASE, "model_id": ""},
}

# 默认头像 Emoji：知名角色精确匹配，其余按名字散列取色池
CHAR_EMOJI = {"黛玉": "🌸", "宝玉": "🎋", "宝钗": "🍵", "湘云": "🕯️",
              "凤姐": "🦚", "熙凤": "🦚", "袭人": "🧵", "晴雯": "🪶"}
EMOJI_POOL = ["🌿", "🏮", "🎐", "🦋", "🌙", "🍵", "🌸", "🎋", "🕊️", "🐟"]

NICK_SUGGEST = {"黛玉": "颦儿", "宝玉": "二爷", "宝钗": "蘅芜君", "湘云": "云丫头"}

THINKING_LINES = ["🌸 正在酝酿情绪...", "🍵 正在斟酌字句...", "📖 正在翻检记忆...",
                  "🏮 正在描眉理鬓...", "🪶 正在研墨铺纸...", "🌙 正在望月出神..."]

# --------------------------------------------------------------------------
# 轻量工具
# --------------------------------------------------------------------------


def parse_names(raw: str) -> list[str]:
    """解析人名输入（**空格绝不当分隔符**——灰度实测踩过）。

    用户输入 `Ben Affleck, Matt Damon` 必须解析成**两个人**，而不是四个。
    规则：中文逗号/顿号/分号/换行统一折算成英文逗号，然后**严格只用英文
    逗号** `split`，逐项 `strip` 后丢弃空串。空格在英文名内部是合法字符，
    一律保留。
    """
    text = re.sub(r"[,，、;；\r\n]+", ",", (raw or ""))
    return [p.strip() for p in text.split(",") if p.strip()]


def normalize_work_title(title: str) -> str:
    """作品名归一化——与引擎 `dialogue.normalize_work_title` 同款规则。

    为什么壳里也要归一化：引擎的跨宇宙认知按作品名判定"谁认识谁"。
    用户给托尼填「漫威」、给皮特填「《漫威》 」，如果壳里按原文分组，
    就会生成**两个**作品标签 → 引擎把同阵营战友判成陌生人。归一化
    （去空白/书名号、小写）后同键同组，一个宇宙就是一个宇宙。
    """
    return re.sub(r"[\s《》〈〉「」]+", "", str(title or "")).casefold()


def group_build_specs(role_types: dict[str, str],
                      role_works: dict[str, str]) -> list[dict]:
    """把逐名 (类型, 作品) 配置折叠成**最少批次**的建档分组。

    引擎 `run` 的类型与作品是**整批统一**的（一次 run 只能带一个
    `--type` + `--work-title`），所以混合阵营必须分组多次跑：
    - 真实人物：全部合成一批（`--type REAL_PERSON`，无作品）；
    - 虚构角色：按**归一化作品名**分组（同键 = 同一次 run = 同一宇宙）。

    返回项形状 `{"entity_type": str, "work_title": str, "names": list[str]}`，
    顺序稳定（真实组在前，作品组按首次出现排序）。类型/作品缺失的人名
    直接跳过——调用方负责前置校验，这里绝不编造分组。
    """
    specs: list[dict] = []
    real_names = [n for n, t in role_types.items()
                  if t == "真实人物" and n]
    if real_names:
        specs.append({"entity_type": "REAL_PERSON",
                      "work_title": "", "names": real_names})
    grouped: dict[str, dict] = {}
    for name, work in role_works.items():
        if role_types.get(name) != "虚构角色" or not name:
            continue
        title = str(work or "").strip()
        if not title:
            continue
        key = normalize_work_title(title)
        if key in grouped:
            grouped[key]["names"].append(name)
        else:
            grouped[key] = {"entity_type": "FICTIONAL_CHARACTER",
                            "work_title": title, "names": [name]}
    specs.extend(grouped.values())
    return specs


def pick_emoji(name: str) -> str:
    for key, emoji in CHAR_EMOJI.items():
        if key in name:
            return emoji
    return EMOJI_POOL[sum(ord(c) for c in name) % len(EMOJI_POOL)]


# --------------------------------------------------------------------------
# 多会话管理（2026-11-05 P1：微信式多开 + 历史保存）
# --------------------------------------------------------------------------


def new_session_id() -> str:
    """会话 ID：时间戳 + 2 字节随机尾巴（同一秒连点也不会撞）。"""
    return ("s_" + time.strftime("%Y%m%d_%H%M%S")
            + "_" + os.urandom(2).hex())


def make_project_id(session_id: str) -> str:
    """会话的底层引擎 project_id：`prj_web_<净化后的 sid>`。

    每个会话已有独立 DB（预算/记忆/档案物理隔离），`--project` 是第二道
    隔离：就算哪天把多个会话并进同一个库，命名空间也不会串。
    """
    return "prj_web_" + re.sub(r"\W+", "_", str(session_id)).strip("_")


def session_db_path(session_id: str) -> Path:
    """会话独立数据库：<DATA_DIR>/sessions/<sid>/persona.db。"""
    return SESSIONS_DIR / str(session_id) / "persona.db"


def session_label(group_name: str, names: list[str]) -> str:
    """会话列表显示名：群名优先；没填就用人物名（3 个起步 + 省略号）。"""
    base = str(group_name or "").strip()
    if base:
        return base
    if names:
        shown = "、".join(names[:3])
        return shown + ("…" if len(names) > 3 else "")
    return "未命名群聊"


def unique_label(base: str, existing: set[str]) -> str:
    """radio 选项必须唯一：重名会话自动加「（2）（3）」后缀。"""
    if base not in existing:
        return base
    n = 2
    while f"{base}（{n}）" in existing:
        n += 1
    return f"{base}（{n}）"


def save_sessions(chat_sessions: dict) -> None:
    """会话元数据落盘（聊天记录/群名/人名/project_id）——刷新后恢复。

    写盘失败（云端只读盘）静默降级：会话仍在 session_state 里活着，
    只是本次浏览器会话结束后不再恢复。
    """
    try:
        SESSIONS_FILE.write_text(
            json.dumps(chat_sessions, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def load_sessions() -> dict:
    """从磁盘恢复会话列表；文件缺失/损坏一律返回空（绝不炸启动）。"""
    try:
        data = json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------------------
# 实体分类推断（2026-11-04 P0 前端瘦身：用户只输名字，真实/虚构自动判）
# --------------------------------------------------------------------------

_CLASSIFY_PROMPT = (
    "你是人物资料助手。对下面每个人名，判断TA是真实存在的公众人物"
    "（real）还是文艺作品中的虚构角色（fictional）。虚构角色给出TA最著名的"
    "作品/世界观名（如：林黛玉→红楼梦，皮特帕克→漫威，布鲁斯韦恩→DC）；"
    "真实人物 work 留空。拿不准一律算 real。\n"
    "只输出 JSON 数组，格式："
    '[{"name":"人名原样","kind":"real|fictional","work":"作品名或空串"}]，'
    "不要输出任何其它文字。")


def parse_classifications(raw: str, names: list[str]) -> dict[str, dict[str, str]]:
    """解析分类模型的输出 → {name: {"kind": REAL_PERSON|FICTIONAL_CHARACTER, "work": str}}。

    宽松解析：优先 json.loads，失败再用正则抠 JSON 片段；再失败就全部兜底
    REAL_PERSON（引擎会按真实人物去搜索——宁可多搜一次，不可错标虚构）。
    虚构但作品名为空的一律降级为 REAL_PERSON（虚构没作品 = 引擎无法判宇宙，
    不如按真人搜索）。名字匹配做大小写/空格归一化。
    """
    default = {"kind": "REAL_PERSON", "work": ""}
    result = {name: dict(default) for name in names}
    if not raw or not names:
        return result

    entries: list[dict] = []
    try:
        loaded = json.loads(raw)
        if isinstance(loaded, dict):        # 允许 {"people": [...]} 之类的包裹
            for value in loaded.values():
                if isinstance(value, list):
                    entries = [e for e in value if isinstance(e, dict)]
                    break
        elif isinstance(loaded, list):
            entries = [e for e in loaded if isinstance(e, dict)]
    except (json.JSONDecodeError, ValueError):
        match = re.search(r"\[.*\]", raw, re.S)
        if match:
            try:
                entries = [e for e in json.loads(match.group(0))
                           if isinstance(e, dict)]
            except (json.JSONDecodeError, ValueError):
                entries = []

    def _norm(s: str) -> str:
        return re.sub(r"\s+", "", str(s or "")).lower()

    by_norm = {_norm(n): n for n in names}
    for entry in entries:
        name = by_norm.get(_norm(str(entry.get("name", ""))))
        if name is None:
            continue
        kind_raw = str(entry.get("kind", "")).lower()
        work = str(entry.get("work", "") or "").strip()
        is_fictional = ("fiction" in kind_raw or "虚构" in kind_raw
                        or "fake" in kind_raw)
        if is_fictional and work:
            result[name] = {"kind": "FICTIONAL_CHARACTER", "work": work}
        # fictional 但没作品 / kind 含糊 → 保持 REAL_PERSON 兜底
    return result


def classify_names(names: list[str], api_key: str, base_url: str,
                   model_id: str) -> dict[str, dict[str, str]]:
    """一次极低成本（≈0.0001 USD）的模型调用，推断人名的真实/虚构与作品名。

    任何失败（网络/密钥/超时/输出不可解析）都返回全部 REAL_PERSON 兜底，
    绝不让这个辅助步骤挡住建档主流程。
    """
    default = {name: {"kind": "REAL_PERSON", "work": ""} for name in names}
    if not names or not api_key:
        return default
    url = (base_url or DEFAULT_API_BASE).rstrip("/") + "/chat/completions"
    payload = json.dumps({
        "model": model_id or "deepseek-chat",
        "messages": [{"role": "user",
                      "content": _CLASSIFY_PROMPT + "\n人名：" +
                                 json.dumps(names, ensure_ascii=False)}],
        "temperature": 0.0,
        "max_tokens": 500,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        text = body["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 —— 推断是锦上添花，失败就走真人兜底
        return default
    return parse_classifications(text, names)


def save_avatar(upload, role_key: str) -> str | None:
    """把上传的头像存到数据目录，返回文件路径；失败返回 None（回退 Emoji）。"""
    if upload is None:
        return None
    try:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload.name).suffix or ".png"
        path = AVATAR_DIR / f"{role_key}{suffix}"
        path.write_bytes(upload.getvalue())
        return str(path)
    except Exception:  # noqa: BLE001 —— 头像存不下来不该挡住聊天
        return None


# --------------------------------------------------------------------------
# CLI 薄壳：调用与解析
# --------------------------------------------------------------------------


def build_env(api_key: str, model_id: str, max_calls: int,
              search_key: str = "") -> dict[str, str]:
    """子进程环境变量。密钥只走环境变量（引擎侧设计），Model ID 可被覆盖。

    云端没有 .env：设了预算（--budget）就必须给单价 + token 估算，
    否则配置校验拒绝启动（闸门不用估计值冒充）。这里用 setdefault 兜底
    DeepSeek 官方价目表数值——本地 .env 已配的不受影响（同名环境变量优先级
    高于 .env，且数值一致）。
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PERSONA_MODEL_PROVIDER"] = "deepseek"      # v1.0.0 仅登记 DeepSeek
    env["PERSONA_MODEL_ID"] = model_id
    env["PERSONA_MODEL_MAX_CALLS"] = str(int(max_calls))
    # 云端无 .env 的预算配套缺口（与仓库 .env 同源：DeepSeek 官方价目表 2026-10）
    env.setdefault("PERSONA_MODEL_PRICE_IN_PER_1M", "0.3")
    env.setdefault("PERSONA_MODEL_PRICE_OUT_PER_1M", "1.2")
    env.setdefault("PERSONA_MODEL_PRICE_SOURCE",
                   "https://api-docs.deepseek.com/quick_start/pricing")
    env.setdefault("PERSONA_MODEL_PRICE_CHECKED_AT", "2026-10-16")
    env.setdefault("PERSONA_MODEL_EST_TOKENS_IN", "3000")
    env.setdefault("PERSONA_MODEL_EST_TOKENS_OUT", "2000")
    if api_key:
        env["PERSONA_PROVIDER_API_KEY"] = api_key
        # 引擎的别名：同时给，防某条路径只认别名
        env.setdefault("DEEPSEEK_API_KEY", api_key)
    if search_key:
        # 检索密钥（云端无 .env 的通道）：TAVILY_API_KEY 是引擎认可的别名
        env["TAVILY_API_KEY"] = search_key
        env.setdefault("PERSONA_SEARCH_API_KEY", search_key)
    return env


def run_cli(cli_args: list[str], env: dict[str, str],
            timeout: float) -> tuple[int, str, str]:
    """跑一条 `python -m persona …`，返回 (退出码, stdout, stderr)。"""
    cmd = [PY_EXE, "-m", "persona", *cli_args]
    try:
        proc = subprocess.run(
            cmd, cwd=str(REPO_ROOT), env=env, timeout=timeout,
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"命令超时（{timeout:.0f} 秒）：引擎没有在时限内返回。"
    except Exception as exc:  # noqa: BLE001 —— 任何启动失败都转成友好提示
        return 125, "", f"无法启动引擎子进程：{exc}"


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_TURN_HEAD_RE = re.compile(r"^【(.+?)】\s?(.*)$")


def parse_turns(output: str) -> list[tuple[str, str]]:
    """从 `chat` 的输出里解析出发言块：(角色名, 台词)。

    CLI 的格式是 `【角色名】 首行` + 每行 4 空格缩进的续行 + 可选「记忆：id」行；
    证据引用 `[ev:...]` 已在引擎渲染层剥掉，这里不用再处理。ANSI 加粗只在
    TTY 下出现，子进程管道里不会有——但顺手剥掉更保险。
    """
    turns: list[tuple[str, str]] = []
    cur_name: str | None = None
    cur_lines: list[str] = []
    for raw in output.splitlines():
        line = _ANSI_RE.sub("", raw.rstrip())
        head = _TURN_HEAD_RE.match(line)
        if head:
            if cur_name is not None:
                text = "\n".join(cur_lines).strip()
                if text:
                    turns.append((cur_name, text))
            cur_name, cur_lines = head.group(1).strip(), [head.group(2)]
            continue
        if cur_name is None:
            continue
        if line.startswith("    "):
            body = line[4:]
            if body.startswith("记忆："):        # 记忆 ID 行只供追溯，不进对话
                continue
            cur_lines.append(body)
        elif line.strip():
            text = "\n".join(cur_lines).strip()   # 非缩进行 = 本块结束
            if text:
                turns.append((cur_name, text))
            cur_name, cur_lines = None, []
    if cur_name is not None:
        text = "\n".join(cur_lines).strip()
        if text:
            turns.append((cur_name, text))
    return turns


# --------------------------------------------------------------------------
# 动作 / 台词分离（2026-11-05 P1 阅读体验：让"说的"和"做的"视觉分离）
# --------------------------------------------------------------------------

# 动作包裹形态：全角括号 / 半角括号 / **强调** / *斜体舞台指示*
_ACTION_RE = re.compile(r"（[^（）]*）|\([^()]*\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*")


def split_action_segments(text: str) -> list[tuple[str, str]]:
    """把混合了动作描写与台词的发言拆成有序段：("action"/"speech", 内容)。

    动作 = （…）/ (…) / **…** / *…* 包裹的内容（剥掉包裹符，保留内文）；
    其余均为台词。段序与原文一致；无法解析时整体按台词兜底。
    """
    if not text or not text.strip():
        return []
    segments: list[tuple[str, str]] = []
    pos = 0
    for match in _ACTION_RE.finditer(text):
        if match.start() > pos:
            speech = text[pos:match.start()].strip()
            if speech:
                segments.append(("speech", speech))
        raw = match.group(0)
        inner = raw.strip("（）()*").strip()
        if inner:
            if raw.startswith("（") or raw.startswith("("):
                segments.append(("action", raw))   # 括号形态忠实保留原文笔迹
            else:
                segments.append(("action", inner))  # 星号形态剥掉包裹符
        pos = match.end()
    if pos < len(text):
        speech = text[pos:].strip()
        if speech:
            segments.append(("speech", speech))
    return segments or [("speech", text.strip())]


def _speech_chunks(text: str) -> list[str]:
    """把台词按句读切成小段，供逐句浮现（打字机效果按句粒度最流畅）。"""
    chunks = re.findall(r"[^，。！？；…!?;、]+[，。！？；…!?;、]?|…", text)
    return [c for c in chunks if c.strip()] or [text]


def friendly_error(returncode: int, output: str, err: str) -> str:
    """把引擎的失败翻译成人话；具体技术细节放可展开的调试区。"""
    blob = (output + "\n" + err)
    if returncode == 124:
        return "等了好一会儿引擎还没回来（网络超时或模型端太慢）。"
    if returncode == 125:
        return blob.strip()
    if "401" in blob or "Unauthorized" in blob or "认证" in blob:
        return "API Key 好像不对（引擎收到了 401 未授权）。检查一下侧边栏的密钥。"
    if "PERSONA_PROVIDER_API_KEY" in blob or "格式不符合要求" in blob:
        return ("API Key 没通过引擎校验（长度不足 8 或含空白字符）。"
                "检查侧边栏密钥是否填全、有没有误粘进空格或换行。")
    if "没有发起任何请求" in blob:
        return "引擎在**花钱之前**就把这次请求拦下了（预算/配置/门禁）。下面有具体原因。"
    if "找不到数据库" in blob or "还没有人物档案" in blob:
        return "数据库里还没有这几位的档案。先点左侧「🚀 生成人物档案」。"
    if "预算" in blob or "BUDGET" in blob:
        return "预算到顶了。可以在侧边栏调高「单次预算上限」，或减少回应人数/互聊轮数。"
    if "至少 2 人" in blob or "一个人开不了会话" in blob:
        return "群聊至少要两位角色。请在左侧多添一个人名。"
    if "topic" in blob.lower() and "记忆锚点" not in blob:
        return "记忆锚点不合规矩：至少 2 个字、不能全是「我」「这个」这类虚词。"
    return "引擎这次没有跑成。详情见下面的调试信息。"


def show_debug(output: str, err: str) -> None:
    tail = "\n".join((output or "").splitlines()[-18:])
    with st.expander("🔍 调试信息（把这段一起截图反馈更好定位）"):
        if tail:
            st.code(tail or "（无输出）", language=None)
        if err:
            st.code(err[-800:], language=None)


# --------------------------------------------------------------------------
# 页面骨架
# --------------------------------------------------------------------------

st.set_page_config(page_title="专属人物模拟器", page_icon="🎭", layout="wide")

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&family=Noto+Serif+SC:wght@400;500;600&display=swap');

      .stApp {
        background: linear-gradient(160deg, #fbf8f3 0%, #f6efe7 55%, #f3ebe1 100%);
        color: #3d3a34;
        font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
      }
      section[data-testid="stSidebar"] {
        background: #f4ede4;
        border-right: 1px solid #e8dccc;
      }
      h1, h2, h3, .stMarkdown, .stTextInput, .stButton, .stSelectbox,
      section[data-testid="stSidebar"] * {
        font-family: 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
      }
      /* —— 对话气泡：实体书排版（2026-11-05 P1 阅读体验）——
         衬线体只用于聊天气泡正文，侧边栏等 UI 仍是无衬线（功能可读性优先） */
      [data-testid="stChatMessage"] {
        background: rgba(255, 255, 255, 0.85);
        border: 1px solid #eee4d8;
        border-radius: 16px;
        box-shadow: 0 4px 12px rgba(120, 100, 80, 0.08);
        padding: 15px 18px;
        margin-bottom: 10px;
        font-family: 'Noto Serif SC', 'Songti SC', 'SimSun', serif !important;
        line-height: 1.8 !important;
        letter-spacing: 0.5px !important;
      }
      [data-testid="stChatMessage"] strong {
        color: #5c4f46;
        font-size: 1.05em;
      }
      /* 动作描写：灰色斜体小一号，和台词一眼分开 */
      .action-text {
        color: #8c8478;
        font-style: italic;
        font-size: 0.92em;
        margin-bottom: 8px;
        display: block;
      }
      /* —— 会话列表（微信式，2026-11-05 P1）：卡片化 + 选中高亮 —— */
      div[role="radiogroup"] label[data-baseweb="radio"] {
        width: 100%;
        padding: 8px 12px !important;
        border-radius: 10px;
        margin: 2px 0;
        border: 1px solid transparent;
        transition: background 0.15s;
      }
      div[role="radiogroup"] label[data-baseweb="radio"]:hover {
        background: rgba(124, 104, 82, 0.08);
      }
      div[role="radiogroup"] label[data-baseweb="radio"]:has(input[aria-checked="true"]) {
        background: rgba(124, 104, 82, 0.16);
        border-color: #d9c8b2;
      }
      /* 无会话时的欢迎语：居中留白 */
      .welcome-hero {
        text-align: center;
        padding: 6rem 1rem;
        color: #8c8478;
        font-size: 1.15rem;
        font-family: 'Noto Serif SC', 'Songti SC', serif;
      }
      [data-testid="stChatInput"] textarea, [data-testid="stChatInput"] {
        border-radius: 12px !important;
        border: 1px solid #e0d4c4 !important;
      }
      .stButton > button {
        border-radius: 12px;
        border: 1px solid #d8b4bc;
        background: linear-gradient(135deg, #f3e2e0, #ecd5d2);
        color: #7c4a52;
        font-weight: 500;
        box-shadow: 0 2px 8px rgba(150, 100, 100, 0.10);
        transition: all .18s ease;
      }
      .stButton > button:hover {
        background: linear-gradient(135deg, #ecd5d2, #e3c3c3);
        box-shadow: 0 3px 12px rgba(150, 100, 100, 0.18);
      }
      [data-testid="stExpander"] {
        background: rgba(255,255,255,.55);
        border: 1px solid #eee4d8;
        border-radius: 12px;
      }
      /* —— expander 图标兜底隐藏（前两轮温和方案公网实测仍重叠 → 暴力方案）——
         选择器不依赖 Streamlit 内部 testid 结构：details 原生标记 + svg 一网打尽 */
      details summary::-webkit-details-marker {
        display: none !important;
      }
      details summary svg,
      [data-testid="stExpander"] summary svg,
      [data-testid="stExpander"] summary .material-icons,
      [data-testid="stExpander"] summary [data-testid="stExpanderToggleIcon"] {
        display: none !important;
      }
      details summary,
      [data-testid="stExpander"] summary {
        padding-left: 0 !important;
        line-height: 1.5;
        font-weight: 600 !important;
        font-size: 16px !important;
        color: #5c4f46 !important;
      }
      [data-testid="stExpander"] summary p,
      [data-testid="stExpander"] summary div,
      [data-testid="stExpander"] summary span {
        margin: 0;
        line-height: 1.5;
      }
      .name-tag {
        display: inline-block;
        background: #f6ece2;
        border: 1px solid #e5d3c0;
        border-radius: 999px;
        padding: 2px 12px;
        margin: 2px 6px 2px 0;
        color: #6b5646;
        font-size: 0.92rem;
      }
      .garden-title {
        text-align: center; font-weight: 700; letter-spacing: .12em;
        color: #5a5248; margin: 4px 0 0 0;
      }
      .garden-sub {
        text-align: center; color: #8c8478; font-size: .92rem; margin-bottom: 6px;
      }
      .speaker-name { font-weight: 700; color: #7c5a68; }
      .speaker-name-user { font-weight: 700; color: #5f6f85; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------
# 会话状态
# --------------------------------------------------------------------------

DEFAULTS = {
    # —— 多会话（2026-11-05 P1）——
    "chat_sessions": {},       # session_id → {group_name, names, messages, project_id…}
    "current_session_id": None,
    "creating_new": False,     # 「新建群聊」模式：主区显示建档表单
    "provider": "DeepSeek",
    # widget 键的默认值统一在此落 session_state（首次渲染前初始化，
    # 避免条件渲染与重挂载时的键缺失——AppTest 实测过这条坑）。
    "base_url": PROVIDER_PRESETS["DeepSeek"]["base_url"],
    "model_id": PROVIDER_PRESETS["DeepSeek"]["model_id"],
    "budget": 2.0,
    # —— 新建群聊表单的 widget 键（条件渲染，必须预置默认值）——
    "new_group_name": "",
    "new_names_raw": "",
    "new_user_name": "访客",
    "memory_on": True,
    "memory_topic": "",
    "show_dress": False,
}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)

# 刷新/重开浏览器：从磁盘恢复会话列表（聊天记录随会话一起回来）。
if not st.session_state.get("sessions_loaded"):
    st.session_state.chat_sessions.update(load_sessions())
    st.session_state["sessions_loaded"] = True


def _sync_provider() -> None:
    """切换服务商即带出预设（模块层定义：widget ID 哈希需要稳定标识）。"""
    preset = PROVIDER_PRESETS.get(st.session_state.provider, {})
    st.session_state.base_url = preset.get("base_url", DEFAULT_API_BASE)
    st.session_state.model_id = preset.get("model_id", "")


# --------------------------------------------------------------------------
# 左侧边栏
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🏮 会话")

    chat_sessions = st.session_state.chat_sessions
    creating_new = bool(st.session_state.creating_new)
    cur_id = st.session_state.current_session_id

    # ---------- 会话列表（微信式多开，2026-11-05 P1） ----------
    if st.button("➕ 新建群聊", use_container_width=True, key="new_session_btn"):
        st.session_state.creating_new = True
        # 记住从哪个会话进来的，取消新建时能回去
        st.session_state.prev_session_id = st.session_state.current_session_id
        st.session_state.current_session_id = None
        st.session_state.pop("session_picker", None)   # 选项集将变化，清掉旧选择
        st.rerun()

    if chat_sessions:
        # radio 选项必须唯一：重名群聊自动加「（2）」后缀；列表按创建时间
        # 倒序（新会话在上，像微信的最近聊天）。**新建状态下也常驻**——
        # 点任意旧会话即退出新建模式（v0.43：之前新建时把列表藏了，
        # 用户点「新建群聊」后找不到老会话入口）。
        used_labels: set[str] = set()
        label_to_id: dict[str, str] = {}
        for sid, sess in chat_sessions.items():
            lab = unique_label(session_label(sess["group_name"], sess["names"]),
                               used_labels)
            used_labels.add(lab)
            label_to_id[lab] = sid
        labels = list(reversed(list(label_to_id)))
        if not creating_new and cur_id not in chat_sessions:
            # 刷新恢复/旧状态失效：自动选中最近一个会话（微信打开即最近聊天）
            cur_id = label_to_id[labels[0]]
            st.session_state.current_session_id = cur_id
        cur_label = next((lab for lab, sid in label_to_id.items() if sid == cur_id),
                         labels[0])
        # 选项集变化后旧 widget 状态可能失效——失效就清掉让 index 重新生效
        if st.session_state.get("session_picker") not in labels:
            st.session_state.pop("session_picker", None)

        def _pick_session() -> None:
            st.session_state.current_session_id = label_to_id.get(
                st.session_state.session_picker,
                st.session_state.current_session_id)
            st.session_state.creating_new = False

        if creating_new:
            st.caption("📝 正在新建群聊——点下面任一旧会话即可返回。")
            if st.button("↩️ 取消新建", use_container_width=True,
                         key="cancel_new_btn"):
                st.session_state.creating_new = False
                prev = st.session_state.pop("prev_session_id", None)
                if prev in chat_sessions:
                    st.session_state.current_session_id = prev
                elif labels:
                    st.session_state.current_session_id = label_to_id[labels[0]]
                st.session_state.pop("session_picker", None)
                st.rerun()
        st.radio("会话列表", labels,
                 index=labels.index(cur_label),
                 key="session_picker", on_change=_pick_session,
                 label_visibility="collapsed")
        st.caption("👆 点击切换会话——每个会话的档案、记忆与预算完全独立。")
    elif creating_new:
        st.caption("📝 正在新建群聊——在右侧填写人物并生成档案。")
    else:
        st.caption("还没有会话——点上面「新建群聊」开一个吧。")

    active = None if creating_new else chat_sessions.get(cur_id)

    st.divider()

    # ---------- 模型 API 配置（全局，常驻） ----------
    # 2026-11-04 P0：st.expander 的箭头图标与文字重叠（两轮 CSS 均被云端版本
    # 脱靶）→ 侧边栏弃用 expander，改分区标题 + 常驻布局。API 是必填的第一步，
    # 常驻比折叠更好；只有纯可选的角色装扮用 toggle 门控（带 session 绑定的
    # widget 条件卸载有状态清扫风险，AppTest 实测过）。
    st.markdown("**📡 模型 API 配置**")
    provider = st.selectbox("服务商", list(PROVIDER_PRESETS.keys()),
                            key="provider", on_change=_sync_provider,
                            help="v1.0.0 引擎目前仅登记 DeepSeek；"
                                 "其它网关待核心装配层开放。")
    base_url = st.text_input("Base URL", key="base_url",
                             help="当前版本仅 DeepSeek 官方端点生效；"
                                  "自定义网关属于核心装配层的后续需求。")
    model_id = st.text_input("Model ID", key="model_id",
                             placeholder="如 deepseek-chat")
    api_key = st.text_input("API Key", type="password", key="api_key",
                            placeholder="只存在本机内存里，绝不落盘")
    search_key = st.text_input("检索 Key（Tavily）", type="password",
                               key="search_key",
                               help="建档材料全部来自 Tavily 搜索，没有它引擎"
                                    "拒绝花钱（一条检索都不发起）。"
                                    "本地 .env 已配 TAVILY_API_KEY 的可留空；"
                                    "云端部署必须在这里填。")
    budget = st.number_input("单次预算上限（美元）", min_value=0.05,
                             max_value=10.0, value=2.0, step=0.05,
                             help="引擎软上限：花到这个数会暂停询问。")
    st.caption("🔐 密钥只经内存传给引擎子进程，不写盘、不进记录、不回显。")

    if provider != "DeepSeek":
        st.warning("v1.0.0 的引擎只登记了 DeepSeek 端点；选其它服务商暂时跑不了，"
                   "等核心装配层开放后这里会自动生效。")

    # ---------- 群聊节奏与角色装扮（属于当前会话） ----------
    if active:
        max_resp = max(1, len(active["names"]))
        # 切换会话后人数上限会变：先把存量值夹进新边界，再交给 slider
        stored_resp = min(int(st.session_state.get("responses", 2) or 2), max_resp)
        st.session_state["responses"] = max(1, stored_resp)
        responses = st.slider("每条消息后，几位角色依次回应", 1, max_resp,
                              key="responses",
                              help="后答的角色看得见先答角色的话。")
        group_turns = st.slider("群聊互动轮数（角色自主互聊）", 0, 6,
                                key="group_turns",
                                help="你说完之后角色自己接话的轮数；"
                                     "互聊内容绝不进长期记忆。")
        per_msg_calls = responses + group_turns
        st.caption(f"💰 每条消息 ≈ 最多 {per_msg_calls} 次模型调用"
                   f"（实测 ≈{CHAT_COST_PER_CALL_USD:.4f} USD/次，"
                   f"约 {per_msg_calls * CHAT_COST_PER_CALL_USD:.4f} USD）。")

        show_dress = st.toggle("🎎 角色装扮（可选）", value=False, key="show_dress",
                               help="换昵称、传头像；不展开就直接用默认。"
                                    "装扮属于当前会话，发消息时保存。")
        nicknames: dict[str, str] = {}
        char_avatars: dict[str, str | None] = {}
        if show_dress:
            sid_ = active["session_id"]
            for name in active["names"]:
                st.session_state.setdefault(
                    f"nick_{sid_}_{name}",
                    active.get("nicknames", {}).get(name, ""))
                c1, c2 = st.columns([1, 3])
                with c1:
                    st.markdown(f"<div style='font-size:1.9rem;text-align:center'>"
                                f"{pick_emoji(name)}</div>",
                                unsafe_allow_html=True)
                with c2:
                    nicknames[name] = st.text_input(
                        f"{name} · 角色昵称",
                        placeholder=NICK_SUGGEST.get(name, "（可选）"),
                        key=f"nick_{sid_}_{name}", label_visibility="collapsed")
                char_avatars[name] = save_avatar(
                    st.file_uploader(f"{name} · 头像",
                                     type=["png", "jpg", "jpeg", "webp"],
                                     key=f"avatar_{sid_}_{name}"),
                    role_key=f"char_{sid_}_{re.sub(r'\\W+', '_', name)}")

# --------------------------------------------------------------------------
# 生成档案（run）——按会话隔离：--db <会话独立库> --project <会话命名空间>
# --------------------------------------------------------------------------


def build_session_profiles(names: list[str], api_key: str, base_url: str,
                           model_id: str, budget: float, db_path: Path,
                           project_id: str, search_key: str = "",
                           cached_class: dict | None = None) -> dict | None:
    """为一个会话建档：实体推断（可带缓存）→ 分批 run → 逐个验证。

    所有引擎调用都带 `--db <会话独立库>` 与 `--project <会话命名空间>`：
    预算是全库累计的（external_usage_records 不分 project），所以独立 DB
    才是真隔离——预算、长期记忆、人物档案三层都不会与其它会话串台。

    推断（classify_names）失败一律按真实人物兜底——宁可多搜一次，不可错标
    虚构。引擎 `run` 的类型与作品整批统一，所以真实人物合一批、虚构角色按
    作品各一批（分组逻辑见 `group_build_specs`）。每批 = 一次检索 + 抽取
    （约 0.008 USD/批）；某批失败立即停止，不烧后面几批的钱。

    成功返回 classifications（dict）；失败返回 None（错误已就地展示）。
    """
    # 1) 实体分类推断（≈0.0001 USD，一次调用；结果按人名缓存防重复花钱）
    cache_key = ",".join(names)
    if cached_class and cached_class.get("_names") == cache_key:
        classifications = {k: v for k, v in cached_class.items()
                           if k != "_names"}
    else:
        with st.spinner("🔍 正在识别人物身份（真实 / 虚构）..."):
            classifications = classify_names(names, api_key, base_url, model_id)
    role_types = {n: ("虚构角色" if c["kind"] == "FICTIONAL_CHARACTER"
                      else "真实人物")
                  for n, c in classifications.items()}
    role_works = {n: c["work"] for n, c in classifications.items()}
    inferred = "；".join(
        f"{n}→{'虚构《' + role_works[n] + '》' if role_types[n] == '虚构角色' else '真实人物'}"
        for n in names)
    st.caption(f"🧠 已自动识别：{inferred}")

    # 2) 分组建档（虚构没推断出作品的已被 parse_classifications 降级为真人）
    specs = group_build_specs(role_types, role_works)
    if not specs:
        st.error("没有可建档的人物配置。")
        return None
    if len(specs) > 1:
        st.caption(f"🧩 混合阵营：分 {len(specs)} 批建档"
                   "（真实人物一批、每个作品一批）。")
    env = build_env(api_key, model_id, max_calls=200, search_key=search_key)
    for i, spec in enumerate(specs, start=1):
        label = (f"第 {i}/{len(specs)} 批："
                 + "、".join(spec["names"])
                 + (f"（{spec['work_title']}）" if spec["work_title"]
                    else "（真实人物）"))
        with st.spinner(f"📖 {label}——正在搜集资料..."):
            cli_args = ["run", "--names", ",".join(spec["names"]),
                        "--type", spec["entity_type"],
                        "--out", str(db_path.parent), "--project", project_id,
                        "--budget", str(budget),
                        "--yes"]
            if spec["work_title"]:
                cli_args += ["--work-title", spec["work_title"]]
            code, out, err = run_cli(cli_args, env, timeout=900.0)
        if code != 0:
            st.error(f"😤 {label} 没能建档：" + friendly_error(code, out, err))
            show_debug(out, err)
            return None
    # 逐个验证档案真的在库里（只读命令，不花钱）
    missing = []
    for name in names:
        rc, _, _ = run_cli(["show", name, "--db", str(db_path),
                            "--project", project_id], env, timeout=60.0)
        if rc != 0:
            missing.append(name)
    if missing:
        st.warning("⚠️ 档案部分生成，这几位还没就位：" + "、".join(missing)
                   + "。回到左侧再点一次「新建群聊」补齐（同样的人名会复用"
                     "已生成的档案，不重复花钱）。")
        return None
    return classifications

# --------------------------------------------------------------------------
# 右侧主区：新建表单 / 欢迎语 / 当前会话聊天（2026-11-05 P1 多会话）
# --------------------------------------------------------------------------

chat_sessions = st.session_state.chat_sessions
creating_new = bool(st.session_state.creating_new)
active = (None if creating_new
          else chat_sessions.get(st.session_state.current_session_id))

if creating_new or active is None:
    if creating_new:
        # ---------- 新建群聊表单 ----------
        st.markdown("<h1 class='garden-title'>✨ 新建群聊</h1>",
                    unsafe_allow_html=True)
        st.caption("填好人物名字，系统自动识别真实/虚构并生成档案。"
                   "新会话的档案、记忆与预算完全独立，不影响已有群聊。")
        c1, c2 = st.columns(2)
        group_name = c1.text_input("群名（选填）", key="new_group_name",
                                   placeholder="不填就用人物名当会话名")
        names_raw = c2.text_input("人物名字（必填，逗号分隔）",
                                  key="new_names_raw",
                                  placeholder="如：林黛玉,马斯克")
        st.caption("💡 多个名字用逗号分隔；英文名请保留中间空格。"
                   "真实/虚构身份会自动识别。")
        user_name = st.text_input("你的昵称", key="new_user_name")
        user_avatar_file = st.file_uploader(
            "你的头像", type=["png", "jpg", "jpeg", "webp"],
            key="new_user_avatar")
        st.caption("不上传就默认 🙎‍♂️。")
        memory_on = st.toggle("跨会话记忆（记得住你说的话）", key="memory_on",
                              help="引擎的确定性规则：只记**你的原话**，"
                                   "角色互聊内容绝不入档。")
        memory_topic = st.text_input(
            "记忆锚点（--topic）", key="memory_topic",
            help="角色只会召回**内容里包含这个词**的往事。"
                 "不填就用群名。至少 2 个字。")
        names = parse_names(names_raw)
        if names_raw and not names:
            st.warning("人名没解析出来，检查一下分隔符。")
        if len(names) == 1:
            st.info("群聊至少要两位角色——再加一位吧，一个人开不了会话。")
        can_build = (len(names) >= 2 and bool(api_key)
                     and provider == "DeepSeek" and bool(model_id))
        if st.button("🚀 创建群聊并生成档案", use_container_width=True,
                     disabled=not can_build):
            sid = new_session_id()
            classifications = build_session_profiles(
                names, api_key, base_url, model_id, budget,
                session_db_path(sid), make_project_id(sid),
                search_key=search_key)
            if classifications is not None:
                st.session_state.chat_sessions[sid] = {
                    "session_id": sid,
                    "project_id": make_project_id(sid),
                    "group_name": group_name.strip(),
                    "names": list(names),
                    "messages": [],
                    "user_name": (user_name or "访客").strip() or "访客",
                    "user_avatar": save_avatar(
                        user_avatar_file, role_key=f"user_{sid}") or "🙎‍♂️",
                    "memory_on": bool(memory_on),
                    "memory_topic": (memory_topic.strip()
                                     or session_label(group_name, names)),
                    "built_names": list(names),
                    "nicknames": {},
                    "classifications": {"_names": ",".join(names),
                                        **classifications},
                }
                st.session_state.current_session_id = sid
                st.session_state.creating_new = False
                save_sessions(st.session_state.chat_sessions)
                st.rerun()
    else:
        st.markdown("<div class='welcome-hero'>"
                    "👈 请在左侧新建一个群聊，开始你的跨宇宙之旅。</div>",
                    unsafe_allow_html=True)
    st.stop()

# ---------- 当前会话的聊天界面 ----------
names = active["names"]
display_name = {}
for n in names:
    nick = st.session_state.get(f"nick_{active['session_id']}_{n}", "")
    display_name[n] = (nick or "").strip() or n
cast_line = " · ".join(f"{pick_emoji(n)} {display_name[n]}（{n}）" for n in names)

st.markdown(f"<h1 class='garden-title'>🎭 "
            f"{session_label(active['group_name'], names)}</h1>",
            unsafe_allow_html=True)
st.markdown(f"<p class='garden-sub'>{cast_line}</p>", unsafe_allow_html=True)
st.divider()

user_avatar = active.get("user_avatar") or "🙎‍♂️"


def render_assistant_message(msg: dict, animate: bool = False,
                             pause_after: bool = False) -> None:
    """渲染一条角色发言：动作灰斜体、台词正常字；animate=True 时逐句浮现。

    animate 仅用于**本轮新发言**——历史消息每次交互都会静态重渲染，
    若对历史也做延时，用户每点一次都要重看一遍动画（Streamlit 全页重跑）。
    """
    with st.chat_message("assistant",
                         avatar=msg.get("avatar") or msg.get("emoji")):
        st.markdown(f"<span class='speaker-name'>"
                    f"{msg['name']}</span>", unsafe_allow_html=True)
        segments = split_action_segments(msg["text"])
        for kind, content in segments:
            if kind == "action":
                st.markdown(f"<span class='action-text'>{content}</span>",
                            unsafe_allow_html=True)
            elif animate:
                placeholder = st.empty()
                shown = ""
                for chunk in _speech_chunks(content):
                    shown += chunk
                    placeholder.markdown(shown + " ▌")
                    time.sleep(0.15)
                placeholder.markdown(shown)
            else:
                st.markdown(content)
    if animate and pause_after:
        with st.spinner(f"{pick_emoji(msg.get('name', ''))} "
                        f"{msg['name']} 正在斟酌词句…"):
            time.sleep(1.2)   # 角色间的"活人感"时间差（模拟打字与思考）


def render_history(messages: list[dict]) -> None:
    for msg in messages:
        if msg["role"] == "user":
            with st.chat_message("user", avatar=msg.get("avatar") or "🙎‍♂️"):
                st.markdown(f"<span class='speaker-name-user'>"
                            f"{msg['name']}</span>", unsafe_allow_html=True)
                st.markdown(msg["text"])
        else:
            render_assistant_message(msg, animate=False)


render_history(active["messages"])

# 闪存错误：失败路径经 rerun 回到这里显示一次，然后清掉
flash = st.session_state.pop("flash_error", None)
if flash:
    st.error(flash[0])
    show_debug(flash[1], flash[2])

prompt = st.chat_input(
    f"跟 {'、'.join(display_name.get(n, n) for n in names[:3])}"
    f"{'…' if len(names) > 3 else ''} 说点什么……")

if prompt:
    # ---------- 防呆闸门 ----------
    if provider != "DeepSeek" or not api_key:
        st.warning("先在左侧配好服务商与 API Key（v1.0.0 暂只支持 DeepSeek）。")
        st.stop()

    active["messages"].append({
        "role": "user", "name": active.get("user_name") or "访客",
        "text": prompt, "avatar": user_avatar})
    render_history(active["messages"])

    with st.spinner(THINKING_LINES[len(active["messages"]) % len(THINKING_LINES)]):
        cli_args = ["chat", "--names", ",".join(names), "--rounds", "1",
                    "--responses", str(responses), "--group", str(group_turns),
                    "--message", prompt,
                    "--db", str(session_db_path(active["session_id"])),
                    "--project", active["project_id"],
                    "--budget", str(budget), "--yes"]
        if active.get("memory_on"):
            cli_args += ["--memory-write", "rule"]
            topic = (active.get("memory_topic") or "").strip()
            if len(topic) >= 2:
                cli_args += ["--topic", topic]
        env = build_env(api_key, model_id, max_calls=per_msg_calls + 1,
                        search_key=search_key)
        code, out, err = run_cli(cli_args, env,
                                 timeout=120.0 * per_msg_calls + 60.0)

    if code != 0:
        # 回滚刚才那条用户消息：失败的发言不该留在历史里。
        # 用「闪存错误 + rerun」而不是就地报错——否则气泡会残留到下一次交互。
        active["messages"].pop()
        st.session_state.flash_error = ("😶 " + friendly_error(code, out, err),
                                        out, err)
        st.rerun()

    turns = parse_turns(out)
    if not turns:
        active["messages"].pop()
        st.session_state.flash_error = (
            "😶 角色们没接话（引擎正常返回但没有发言）。", out, err)
        st.rerun()

    new_msgs: list[dict] = []
    for idx, (speaker, text) in enumerate(turns):
        matched = next((n for n in names if n in speaker or speaker in n), None)
        key = matched or speaker
        new_msgs.append({
            "role": "assistant", "name": display_name.get(key, speaker),
            "text": text, "avatar": char_avatars.get(matched),
            "emoji": pick_emoji(key)})
    # 视觉小说式呈现：先动画播放本轮发言（角色间有时间差），播完才入历史
    for idx, msg in enumerate(new_msgs):
        render_assistant_message(msg, animate=True,
                                 pause_after=idx < len(new_msgs) - 1)
    active["messages"].extend(new_msgs)
    # 昵称随消息落盘（装扮面板的改动跟着会话持久化）
    active["nicknames"] = {n: display_name[n] for n in names
                           if display_name[n] != n}
    save_sessions(st.session_state.chat_sessions)
    st.rerun()
