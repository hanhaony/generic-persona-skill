"""正文窗口定位：**证据摘录取哪一段**。

# 为什么单独放在 core

1. `persona/providers/*` **不允许** import `persona/modules/*`（那是反向依赖：
   模块层依赖 Provider 层，反过来就成环了）。
2. 但"摘录取原文哪一段"必须在 Provider 里完成——`ExtractResult.excerpt`
   是 Provider 契约里的字段，落库的证据摘录就来自它。
3. 而摘录一旦取错（取到页头导航），门禁就算再准也管不住：门禁判的若是全文、
   下游用的却是摘录，等于**判定对象与交付对象不是同一个**——P4.5 真实复测
   正是撞在这上面（全文 VALID，摘录全是目录，材料照样交出去）。

所以把**行判定口径 + 窗口算法**放在 core，让 Provider（生成摘录）与
`modules/extract_quality`（门禁判定）共用同一套行判定，两边口径强制一致。

# 算法（启发式，不绑定任何具体站点）

1. 把文本按行切开，逐行判断"是不是正文行"（链接覆盖率高的是导航条目）；
2. 找出**最长的一段连续正文**，取它的起点作为摘录起点；
3. 从该起点向后取 `window_chars` 个字符，末尾对齐到换行（不切半行）；
4. 找不到任何正文行 → **回退**到原文开头（保持旧行为），由门禁去判它不合格。

⚠️ 这是启发式工程判断，不是内容正确性认证。它只解决"摘录窗口指的是哪段原文"，
   不解决"这段原文说的是不是真的"。
"""

from __future__ import annotations

import re

# 摘录窗口默认长度（与 Provider 契约里"截断后的摘要"一致）
DEFAULT_EXCERPT_WINDOW_CHARS = 2000

# 判定"正文行"的下限
DEFAULT_MIN_PROSE_LINE_WORDS = 6      # 西文按空格分词
DEFAULT_MIN_PROSE_LINE_CHARS = 40     # 中文按字计

# 末尾对齐到换行时，窗口至少要保留的长度比例（不为了对齐牺牲一半内容）
MIN_WINDOW_FILL_RATIO = 0.5

# markdown / html 形态的链接
MD_LINK = re.compile(r"!?\[[^\]]*\]\([^)]*\)")
ANCHOR_TARGET = re.compile(r"\]\(#")


def is_link_line(line: str) -> bool:
    """这一行是否"主要是链接/图片/导航条目"。

    判据通用（不绑定任何具体站点）：
      1. 链接部分占到该行一半以上字符 → 这是条目，不是正文；
      2. 整行去掉链接后几乎没剩字 → 就是个链接条目；
      3. 短行里堆了 2 个以上链接 → 菜单 / 页眉页脚。

    第 1 条是关键：真实的目录行往往很长（一条里串起多个章节链接），
    只靠"行短"是抓不住的，必须看链接覆盖率。
    """
    stripped = line.strip()
    if not stripped:
        return False
    links = MD_LINK.findall(stripped)
    if not links:
        return False
    link_chars = sum(len(lk) for lk in links)
    if link_chars / len(stripped) >= 0.5:      # 链接占了半行以上
        return True
    residue = MD_LINK.sub("", stripped)
    if len(residue.strip()) <= 4:
        return True
    return len(links) >= 2 and len(stripped) <= 160


def is_prose_line(line: str,
                  min_words: int = DEFAULT_MIN_PROSE_LINE_WORDS,
                  min_chars: int = DEFAULT_MIN_PROSE_LINE_CHARS) -> bool:
    """这一行是否像正文（有足够长度、有实词、不是纯链接）。"""
    stripped = line.strip()
    if is_link_line(stripped):
        return False
    if len(stripped) < min_chars:
        return False
    # 中文按字计、西文按空格分词；两者都覆盖
    words = stripped.split()
    if len(words) >= min_words:
        return True
    return len(stripped) >= max(min_chars, 40) and not stripped.startswith(("#", "*", "|", "-"))


def iter_content_lines(text: str) -> list[tuple[int, str]]:
    """返回 `[(行首字符偏移, 原始行文本)]`，跳过空行。

    返回原始行（含缩进）而不是 strip 后的文本，是为了让"连续正文段"的长度
    与门禁里统计的度量口径一致。
    """
    entries: list[tuple[int, str]] = []
    if not text:
        return entries
    offset = 0
    for line in text.splitlines():
        if line.strip():
            entries.append((offset, line))
        offset += len(line) + 1          # +1 = 被 splitlines 吃掉的换行
    return entries


def locate_prose_window(
    text: str,
    *,
    window_chars: int = DEFAULT_EXCERPT_WINDOW_CHARS,
    min_line_words: int = DEFAULT_MIN_PROSE_LINE_WORDS,
    min_line_chars: int = DEFAULT_MIN_PROSE_LINE_CHARS,
) -> tuple[int, int]:
    """定位"连续正文最密集"的窗口，返回 `(start, end)` 字符偏移。

    找不到任何正文行时回退 `(0, min(window_chars, len(text)))`——**不假装找到**，
    交回开头那段原文，由下游门禁去判它不合格。
    """
    total = len(text or "")
    if total == 0:
        return (0, 0)
    if total <= window_chars:
        return (0, total)

    best_start: int | None = None
    best_len = 0
    run_start: int | None = None
    for offset, line in iter_content_lines(text):
        if is_prose_line(line, min_line_words, min_line_chars):
            if run_start is None:
                run_start = offset
            run_len = (offset + len(line)) - run_start
            if run_len > best_len:                 # 只取更长的 → 平手时取更早的
                best_len = run_len
                best_start = run_start
        else:
            run_start = None

    if best_start is None:
        return (0, min(window_chars, total))

    start = best_start
    end = min(start + window_chars, total)
    # 末尾对齐到换行：不把最后一行切成半句（除非对齐会让窗口短掉一半以上）
    newline = text.rfind("\n", start, end)
    if newline > start and (newline + 1 - start) >= window_chars * MIN_WINDOW_FILL_RATIO:
        end = newline + 1
    return (start, end)


def select_prose_window(
    text: str,
    *,
    window_chars: int = DEFAULT_EXCERPT_WINDOW_CHARS,
    min_line_words: int = DEFAULT_MIN_PROSE_LINE_WORDS,
    min_line_chars: int = DEFAULT_MIN_PROSE_LINE_CHARS,
) -> str:
    """取"连续正文最密集"的那段原文作为摘录。**纯函数，不碰网络与存储。**"""
    start, end = locate_prose_window(
        text, window_chars=window_chars,
        min_line_words=min_line_words, min_line_chars=min_line_chars)
    return text[start:end]
