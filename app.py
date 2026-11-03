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

import os
import re
import subprocess
import sys
from pathlib import Path

import streamlit as st

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
PY_EXE = sys.executable
DATA_DIR = REPO_ROOT / "var" / "webapp"          # 与仓库 .gitignore 的 var/ 对齐
DB_PATH = DATA_DIR / "persona.db"
AVATAR_DIR = DATA_DIR / "avatars"

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


def pick_emoji(name: str) -> str:
    for key, emoji in CHAR_EMOJI.items():
        if key in name:
            return emoji
    return EMOJI_POOL[sum(ord(c) for c in name) % len(EMOJI_POOL)]


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


def build_env(api_key: str, model_id: str, max_calls: int) -> dict[str, str]:
    """子进程环境变量。密钥只走环境变量（引擎侧设计），Model ID 可被覆盖。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PERSONA_MODEL_PROVIDER"] = "deepseek"      # v1.0.0 仅登记 DeepSeek
    env["PERSONA_MODEL_ID"] = model_id
    env["PERSONA_MODEL_MAX_CALLS"] = str(int(max_calls))
    if api_key:
        env["PERSONA_PROVIDER_API_KEY"] = api_key
        # 引擎的别名：同时给，防某条路径只认别名
        env.setdefault("DEEPSEEK_API_KEY", api_key)
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
      @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&display=swap');

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
      [data-testid="stChatMessage"] {
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid #eee4d8;
        border-radius: 12px;
        box-shadow: 0 2px 10px rgba(120, 100, 80, 0.07);
        padding: 12px 16px;
        margin-bottom: 10px;
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
      /* —— expander 头部防重叠（灰度实测：标题与图标挤在一起）——
         重置 padding 与 line-height，恢复图标与文字的间距，不动 flex 结构 */
      [data-testid="stExpander"] summary {
        padding: 0.65rem 1rem;
        line-height: 1.5;
        min-height: 2.2rem;
      }
      [data-testid="stExpander"] summary p,
      [data-testid="stExpander"] summary div,
      [data-testid="stExpander"] summary span {
        margin: 0;
        line-height: 1.5;
      }
      [data-testid="stExpander"] summary [data-testid="stExpanderToggleIcon"],
      [data-testid="stExpander"] summary svg {
        flex: 0 0 auto;
        margin-right: 8px;
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
    "messages": [],            # [{role, name, text, avatar(路径或 None), emoji}]
    "profiles_ready": False,
    "built_names": [],         # 已确认有档案的人名
    "provider": "DeepSeek",
}
for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


def reset_chat_history() -> None:
    st.session_state.messages = []
    st.session_state.profiles_ready = False
    st.session_state.built_names = []


# --------------------------------------------------------------------------
# 左侧边栏
# --------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🏮 设置")

    # ---------- 面板 1：模型 API 配置 ----------
    with st.expander("📡 模型 API 配置", expanded=True):
        # 标准模式：key + on_change 回调自动填充——切换服务商即带出预设，
        # 用户仍可手改（带 value 参数的 key 会互相打架，这是 Streamlit 的坑）。
        st.session_state.setdefault("base_url", PROVIDER_PRESETS["DeepSeek"]["base_url"])
        st.session_state.setdefault("model_id", PROVIDER_PRESETS["DeepSeek"]["model_id"])

        def _sync_provider() -> None:
            preset = PROVIDER_PRESETS.get(st.session_state.provider, {})
            st.session_state.base_url = preset.get("base_url", DEFAULT_API_BASE)
            st.session_state.model_id = preset.get("model_id", "")

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
        budget = st.number_input("单次预算上限（美元）", min_value=0.05,
                                 max_value=10.0, value=2.0, step=0.05,
                                 help="引擎软上限：花到这个数会暂停询问。")
        st.caption("🔐 密钥只经内存传给引擎子进程，不写盘、不进记录、不回显。")

    if provider != "DeepSeek":
        st.warning("v1.0.0 的引擎只登记了 DeepSeek 端点；选其它服务商暂时跑不了，"
                   "等核心装配层开放后这里会自动生效。")

    # ---------- 面板 2：场景与角色配置 ----------
    with st.expander("🏮 场景与角色配置", expanded=True):
        st.session_state.setdefault("group_name", DEFAULT_GROUP_NAME)
        st.session_state.setdefault("names_raw", "林黛玉,贾宝玉,薛宝钗")
        st.session_state.setdefault("work_title", "")
        st.session_state.setdefault("user_name", "访客")
        st.session_state.setdefault("memory_topic", DEFAULT_GROUP_NAME)
        group_name = st.text_input("群名", key="group_name")
        names_raw = st.text_input(
            "人物名字（必填）", key="names_raw",
            placeholder="用逗号分隔，如：林黛玉,贾宝玉,薛宝钗")
        st.caption("💡 多个名字请用逗号分隔；英文名请保留中间空格。")
        work_title = st.text_input("作品名称（选填）", key="work_title",
                                   placeholder="如：红楼梦（现实人物可留空）")
        user_name = st.text_input("你的昵称", key="user_name")
        user_avatar_file = st.file_uploader(
            "你的头像", type=["png", "jpg", "jpeg", "webp"], key="user_avatar")
        st.caption("不上传就默认 🙎‍♂️。")

        memory_on = st.toggle("跨会话记忆（记得住你说的话）", value=True,
                              key="memory_on",
                              help="引擎的确定性规则：只记**你的原话**，"
                                   "角色互聊内容绝不入档。")
        memory_topic = st.text_input(
            "记忆锚点（--topic）", key="memory_topic",
            help="角色只会召回**内容里包含这个词**的往事——建议用你聊天时"
                 "会反复出现的词（作品名、常用称呼、地名）。至少 2 个字。")

    names = parse_names(names_raw)
    if names_raw and not names:
        st.warning("人名没解析出来，检查一下分隔符。")
    if len(names) == 1:
        st.info("群聊至少要两位角色——再加一位吧，一个人开不了会话。")

    # ---------- 面板 3：角色装扮（随人名动态生成） ----------
    with st.expander("🎎 角色装扮", expanded=True):
        nicknames: dict[str, str] = {}
        char_avatars: dict[str, str | None] = {}
        for name in names:
            c1, c2 = st.columns([1, 3])
            with c1:
                st.markdown(f"<div style='font-size:1.9rem;text-align:center'>"
                            f"{pick_emoji(name)}</div>",
                            unsafe_allow_html=True)
            with c2:
                nicknames[name] = st.text_input(
                    f"{name} · 角色昵称",
                    placeholder=NICK_SUGGEST.get(name, "（可选）"),
                    key=f"nick_{name}", label_visibility="collapsed")
            char_avatars[name] = save_avatar(
                st.file_uploader(f"{name} · 头像",
                                 type=["png", "jpg", "jpeg", "webp"],
                                 key=f"avatar_{name}"),
                role_key=f"char_{re.sub(r'\\W+', '_', name)}")

        max_resp = max(1, len(names))
        responses = st.slider("每条消息后，几位角色依次回应", 1, max_resp,
                              value=min(2, max_resp), key="responses",
                              help="后答的角色看得见先答角色的话。")
        group_turns = st.slider("群聊互动轮数（角色自主互聊）", 0, 6, 3,
                                key="group_turns",
                                help="你说完之后角色自己接话的轮数；"
                                     "互聊内容绝不进长期记忆。")
        per_msg_calls = responses + group_turns
        st.caption(f"💰 每条消息 ≈ 最多 {per_msg_calls} 次模型调用"
                   f"（实测 ≈{CHAT_COST_PER_CALL_USD:.4f} USD/次，"
                   f"约 {per_msg_calls * CHAT_COST_PER_CALL_USD:.4f} USD）。")

        can_build = (len(names) >= 2 and bool(api_key)
                     and provider == "DeepSeek" and bool(model_id))
        if names:
            st.caption("系统识别到以下人物（生成档案前请确认）：")
            st.markdown(
                " ".join(f"<span class='name-tag'>🏷️ {n}</span>" for n in names),
                unsafe_allow_html=True)
        build_btn = st.button("🚀 生成人物档案", use_container_width=True,
                              disabled=not can_build)
        if not api_key:
            st.caption("⬆️ 先填 API Key 才能生成档案。")
        if st.session_state.profiles_ready:
            st.success(f"✅ 档案已加载：{'、'.join(st.session_state.built_names)}")
            if parse_names(names_raw) != st.session_state.built_names:
                st.caption("人名改过了——重新生成档案即可生效。")

# --------------------------------------------------------------------------
# 生成档案（run）
# --------------------------------------------------------------------------


def build_profiles() -> None:
    """触发引擎 `run`：检索公开资料 → 证据 → 人物/关系档案。约 0.008 USD。"""
    if work_title.strip():
        type_args = ["--work-title", work_title.strip()]
    else:
        type_args = ["--type", "REAL_PERSON"]
    cli_args = (["run", "--names", ",".join(names), *type_args,
                 "--out", str(DATA_DIR), "--budget", str(budget), "--yes"])
    env = build_env(api_key, model_id, max_calls=200)
    code, out, err = run_cli(cli_args, env, timeout=900.0)
    if code != 0:
        st.error("😤 档案没能生成：" + friendly_error(code, out, err))
        show_debug(out, err)
        return
    # 逐个验证档案真的在库里（只读命令，不花钱）
    missing = []
    for name in names:
        rc, _, _ = run_cli(["show", name, "--db", str(DB_PATH)],
                           env, timeout=60.0)
        if rc != 0:
            missing.append(name)
    if missing:
        st.warning("⚠️ 档案部分生成，这几位还没就位：" + "、".join(missing)
                   + "。可以点一次「生成人物档案」补齐。")
        st.session_state.profiles_ready = False
        return
    st.session_state.profiles_ready = True
    st.session_state.built_names = list(names)
    st.success(f"✅ 档案已加载：{'、'.join(names)}——去下面跟他们打个招呼吧！")


if build_btn:
    with st.spinner("📖 正在翻阅古籍获取档案..."):
        build_profiles()

# --------------------------------------------------------------------------
# 右侧主聊天区
# --------------------------------------------------------------------------

display_name = {n: (nicknames.get(n, "").strip() or n) for n in names}
cast_line = " · ".join(f"{pick_emoji(n)} {display_name[n]}（{n}）" for n in names)

st.markdown(f"<h1 class='garden-title'>🎭 {group_name or '专属人物模拟器'}</h1>",
            unsafe_allow_html=True)
st.markdown(f"<p class='garden-sub'>{cast_line}</p>", unsafe_allow_html=True)
st.divider()

user_avatar_path = save_avatar(user_avatar_file, role_key="user")
user_avatar = user_avatar_path or "🙎‍♂️"


def render_history() -> None:
    for msg in st.session_state.messages:
        if msg["role"] == "user":
            with st.chat_message("user", avatar=msg.get("avatar") or "🙎‍♂️"):
                st.markdown(f"<span class='speaker-name-user'>"
                            f"{msg['name']}</span>", unsafe_allow_html=True)
                st.markdown(msg["text"])
        else:
            with st.chat_message("assistant",
                                 avatar=msg.get("avatar") or msg.get("emoji")):
                st.markdown(f"<span class='speaker-name'>"
                            f"{msg['name']}</span>", unsafe_allow_html=True)
                st.markdown(msg["text"])


render_history()

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
    if not st.session_state.profiles_ready:
        st.warning("请先在左侧生成人物档案哦~")
        st.stop()
    if provider != "DeepSeek" or not api_key:
        st.warning("先在左侧配好服务商与 API Key（v1.0.0 暂只支持 DeepSeek）。")
        st.stop()

    st.session_state.messages.append({
        "role": "user", "name": user_name or "访客",
        "text": prompt, "avatar": user_avatar})
    render_history()

    with st.spinner(THINKING_LINES[len(st.session_state.messages) % len(THINKING_LINES)]):
        cli_args = ["chat", "--names", ",".join(names), "--rounds", "1",
                    "--responses", str(responses), "--group", str(group_turns),
                    "--message", prompt, "--db", str(DB_PATH),
                    "--budget", str(budget), "--yes"]
        if memory_on:
            cli_args += ["--memory-write", "rule", "--topic", memory_topic.strip()]
        env = build_env(api_key, model_id, max_calls=per_msg_calls + 1)
        code, out, err = run_cli(cli_args, env,
                                 timeout=120.0 * per_msg_calls + 60.0)

    if code != 0:
        # 回滚刚才那条用户消息：失败的发言不该留在历史里。
        # 用「闪存错误 + rerun」而不是就地报错——否则气泡会残留到下一次交互。
        st.session_state.messages.pop()
        st.session_state.flash_error = ("😶 " + friendly_error(code, out, err),
                                        out, err)
        st.rerun()

    turns = parse_turns(out)
    if not turns:
        st.session_state.messages.pop()
        st.session_state.flash_error = (
            "😶 角色们没接话（引擎正常返回但没有发言）。", out, err)
        st.rerun()

    for speaker, text in turns:
        matched = next((n for n in names if n in speaker or speaker in n), None)
        key = matched or speaker
        st.session_state.messages.append({
            "role": "assistant", "name": display_name.get(key, speaker),
            "text": text, "avatar": char_avatars.get(matched),
            "emoji": pick_emoji(key)})
    st.rerun()
