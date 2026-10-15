"""正文质量门禁（P4.5 任务二）。

# 为什么需要它

P4.3 真实抓到 314,743 字正文，但"摘录 = 正文前 2000 字"抓到的全是
**导航/目录骨架**（`[Jump to content]`、目录条目），正文主体 0 字。
也就是说：**抓到正文 ≠ 抓到可用内容**。若不设门禁，这种内容会一路流到
Claim Extractor，生成"看似有来源支持"的 claim——那是最危险的产物。

# P4.5 复测暴露的**判定对象错位**（方案 C = A+B）

P4.5 受控复测发现：门禁判的是 `content`（全文），下游材料出口交的却是
`excerpt`（摘录）。于是**全文 VALID 但摘录是目录**时，摘录照样变成材料交出去——
门禁管的对象和交付的对象不是同一个。

修法两条，**缺一不可**：
- **A 门禁改判摘录**：门禁必须判"下游真正拿到的那段"（= `excerpt`）。
  只做 A 会把长页全部判废（因为摘录那时还是页头），白花额度。
- **B 摘录取窗口**：摘录取"连续正文最密集的那段原文"，而不是 `raw[:2000]`。
  只做 B 门禁仍判错对象，长页的全文判定掩盖了摘录问题。

B 的实现（行判定 + 窗口算法）放在 `core/text_window.py`，Provider 与门禁
共用同一套行判定口径，两边强制一致。

# 门禁判据（全部通用，不绑定任何具体站点）

1. 正文为空 → `EMPTY`
2. 有效正文（连续段落里的文字）过少 → `LOW_QUALITY`
3. 导航/页眉/页脚/菜单/重复链接占比过高 → `LOW_QUALITY`
4. 找不到可识别的连续正文段落 → `LOW_QUALITY`
5. 与目标人物明显无关（**仅当有可靠依据时才判**，否则跳过）

⚠️ 这是**启发式工程判断，不是法律或事实认证**。它的职责是拦住明显不可用的内容，
   不负责证明内容为真。`VALID` 只表示"看起来是像样的正文"，不表示"可信"。

# 补救：只走官方确认过的参数

Tavily Extract 官方文档（docs.tavily.com/documentation/api-reference/endpoint/extract，
2026-10-15 核对）确认可用的提取参数有：`urls` / `query` / `chunks_per_source` /
`extract_depth` / `include_images` / `include_favicon` / `format` / `timeout` /
`include_usage`。

其中 `query` + `chunks_per_source` 正是官方为"控制 raw_content 长度并按相关性
重排内容块"提供的正式选项——这是**优先补救手段**，不需要自己造爬虫。
官方文档同时明确：`chunks_per_source` **仅在提供 `query` 时可用**（1<=x<=5）。
本模块严格按这条约束生成补救方案。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..core.text_window import (
    ANCHOR_TARGET,
    DEFAULT_MIN_PROSE_LINE_CHARS,
    DEFAULT_MIN_PROSE_LINE_WORDS,
    MD_LINK,
    is_link_line,
    is_prose_line,
    iter_content_lines,
    select_prose_window,
)

# --------------------------------------------------------------------------
# 状态与原因
# --------------------------------------------------------------------------

QUALITY_VALID = "VALID"
QUALITY_LOW = "LOW_QUALITY"
QUALITY_EMPTY = "EMPTY"

REASON_EMPTY = "EMPTY_CONTENT"                      # 正文为空
REASON_TOO_SHORT = "TOO_SHORT"                      # 内容总量过少
REASON_NAV_DOMINATED = "NAV_DOMINATED"              # 主要由导航/链接构成
REASON_NO_PROSE = "NO_CONTINUOUS_PROSE"             # 找不到连续正文段落
REASON_TARGET_NOT_MENTIONED = "TARGET_NOT_MENTIONED"  # 与目标人物明显无关
REASON_UNSUPPORTED_PAGE_TYPE = "UNSUPPORTED_PAGE_TYPE"  # 页面类型不适合提正文

# 页面类型
PAGE_TYPE_GENERIC = "GENERIC"
PAGE_TYPE_WIKI = "WIKI"
PAGE_TYPE_VIDEO = "VIDEO"
PAGE_TYPE_PDF = "PDF"
PAGE_TYPE_SOCIAL = "SOCIAL"

# 默认阈值（可配置，不硬编码在具体网页上）
DEFAULT_MIN_EFFECTIVE_CHARS = 200     # 有效正文字数下限
DEFAULT_MIN_PROSE_RUN_CHARS = 200     # 单段连续正文的长度下限
DEFAULT_MAX_NAV_LINE_RATIO = 0.70     # 导航行占比上限
# 行判定口径（"什么是正文行/导航行"）与 Provider 生成摘录时用的**同一套**，
# 定义在 core/text_window.py —— 门禁判的和摘录取的必须是同一件事。

# 诊断用的正文取样长度（只用于定位问题，不落全文）
DIAGNOSTIC_SAMPLE_CHARS = 200

# 补救
DEFAULT_MAX_REMEDIATIONS = 1          # 每个 URL 最多补救 1 次

# 官方 Extract 参数的取值约束（来自官方文档，不是猜测）
OFFICIAL_EXTRACT_DEPTHS = ("basic", "advanced")
OFFICIAL_EXTRACT_FORMATS = ("markdown", "text")
OFFICIAL_CHUNKS_MIN, OFFICIAL_CHUNKS_MAX = 1, 5
DEFAULT_CHUNKS_PER_SOURCE = 3         # 官方默认值


# --------------------------------------------------------------------------
# 结果
# --------------------------------------------------------------------------


@dataclass
class ContentQuality:
    """一次正文质量评估的结果（含度量，便于诊断与调参）。"""

    status: str = QUALITY_EMPTY
    reasons: list[str] = field(default_factory=list)
    length: int = 0
    prose_chars: int = 0
    nav_line_ratio: float = 0.0
    anchor_link_ratio: float = 0.0
    longest_prose_run: int = 0
    page_type: str = PAGE_TYPE_GENERIC
    target_mentioned: bool | None = None   # None = 本次没有可靠依据，未判定
    sample: str = ""                       # 诊断取样（不含密钥）

    @property
    def is_usable(self) -> bool:
        """只有 `VALID` 才算可用研究材料。"""
        return self.status == QUALITY_VALID

    def as_detail(self) -> dict:
        """诊断信息（落库/审计用，不含密钥与个人数据）。"""
        return {
            "status": self.status,
            "reasons": list(self.reasons),
            "length": self.length,
            "prose_chars": self.prose_chars,
            "nav_line_ratio": round(self.nav_line_ratio, 3),
            "anchor_link_ratio": round(self.anchor_link_ratio, 3),
            "longest_prose_run": self.longest_prose_run,
            "page_type": self.page_type,
            "target_mentioned": self.target_mentioned,
            "sample": self.sample,
        }


# --------------------------------------------------------------------------
# 度量
# --------------------------------------------------------------------------

def detect_page_type(url: str) -> str:
    """按 URL 粗分页面类型（只用于选择补救策略，不做内容判断）。"""
    host = (urlparse(url or "").hostname or "").lower()
    path = (urlparse(url or "").path or "").lower()
    if path.endswith(".pdf"):
        return PAGE_TYPE_PDF
    if "wikipedia.org" in host:
        return PAGE_TYPE_WIKI
    if any(h in host for h in ("youtube.com", "youtu.be", "bilibili.com", "vimeo.com")):
        return PAGE_TYPE_VIDEO
    if any(h in host for h in ("x.com", "twitter.com", "weibo.com", "facebook.com",
                               "instagram.com", "reddit.com")):
        return PAGE_TYPE_SOCIAL
    return PAGE_TYPE_GENERIC


def _mentions_any(content: str, names: list[str]) -> bool:
    """目标人物的任一词是否出现在正文里。

    最小长度取 **2** 而不是 3：中文人名常是两字（如"阿雾"），
    用 3 会把两字名整个挡掉，导致明明提到了目标却被判"无关"。

    误判方向是安全的：这个信号只用于"目标**没被提到**才降质"，
    多匹配一个短词只会让我们**不**降质，不会误杀正常内容。
    """
    lowered = (content or "").lower()
    for name in names or []:
        for token in re.split(r"[\s\-_·,，]+", str(name or "")):
            token = token.strip().lower()
            if len(token) >= 2 and token in lowered:
                return True
    return False


# --------------------------------------------------------------------------
# 门禁
# --------------------------------------------------------------------------


def assess_content_quality(
    content: str,
    *,
    url: str = "",
    target_names: list[str] | None = None,
    min_effective_chars: int = DEFAULT_MIN_EFFECTIVE_CHARS,
    min_prose_run_chars: int = DEFAULT_MIN_PROSE_RUN_CHARS,
    max_nav_line_ratio: float = DEFAULT_MAX_NAV_LINE_RATIO,
    min_prose_line_words: int = DEFAULT_MIN_PROSE_LINE_WORDS,
    min_prose_line_chars: int = DEFAULT_MIN_PROSE_LINE_CHARS,
    check_target: bool = True,
) -> ContentQuality:
    """评估一段正文是否可作为研究材料。**纯函数，不碰网络与存储。**"""
    text = content or ""
    q = ContentQuality(length=len(text), page_type=detect_page_type(url))

    if not text.strip():
        q.status = QUALITY_EMPTY
        q.reasons.append(REASON_EMPTY)
        return q

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        q.status = QUALITY_EMPTY
        q.reasons.append(REASON_EMPTY)
        return q

    nav_lines = 0
    prose_lines: list[str] = []
    longest_run = 0
    current_run = 0
    for line in lines:
        if is_prose_line(line, min_prose_line_words, min_prose_line_chars):
            prose_lines.append(line)
            current_run += len(line)
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
        if is_link_line(line):
            nav_lines += 1

    all_links = MD_LINK.findall(text)
    anchors = sum(1 for lk in all_links if ANCHOR_TARGET.search(lk))

    q.prose_chars = sum(len(ln) for ln in prose_lines)
    q.longest_prose_run = longest_run
    q.nav_line_ratio = (nav_lines / len(lines)) if lines else 0.0
    q.anchor_link_ratio = (anchors / len(all_links)) if all_links else 0.0
    q.sample = text[:DIAGNOSTIC_SAMPLE_CHARS]

    # —— 判据 ——
    #
    # 判定顺序很重要：**先看"有没有能用的正文"，再看为什么不达标**。
    # 这样"页眉页脚很多但正文也很大段"的页面会被判 VALID（它确实可用），
    # 而"只有导航"的页面因为没有正文而落进 NAV_DOMINATED。
    if q.prose_chars < min_effective_chars:
        # 没有足够正文 → 区分"本来就短"还是"被导航占满"
        if q.nav_line_ratio >= 0.5:
            q.reasons.append(REASON_NAV_DOMINATED)
        else:
            q.reasons.append(REASON_TOO_SHORT)
    elif q.longest_prose_run < min_prose_run_chars:
        # 字够多但都是碎片（如目录、词条列表）→ 没有成段的正文
        q.reasons.append(REASON_NO_PROSE)
    elif q.nav_line_ratio > max_nav_line_ratio and q.prose_chars < (
            min_effective_chars * 2):
        # 导航占绝大多数，且正文只是"刚好够"→ 保守判低质量
        q.reasons.append(REASON_NAV_DOMINATED)

    # 相关性：**仅在有可靠依据时判**（给了目标名字才判，否则跳过）
    if check_target and target_names:
        q.target_mentioned = _mentions_any(text, target_names)
        if not q.target_mentioned:
            q.reasons.append(REASON_TARGET_NOT_MENTIONED)

    # —— 页面类型 ——
    if q.page_type in (PAGE_TYPE_VIDEO, PAGE_TYPE_SOCIAL):
        q.reasons.append(REASON_UNSUPPORTED_PAGE_TYPE)

    if not q.reasons:
        q.status = QUALITY_VALID
    elif q.reasons == [REASON_EMPTY]:
        q.status = QUALITY_EMPTY
    else:
        q.status = QUALITY_LOW
    return q


# --------------------------------------------------------------------------
# 补救策略
# --------------------------------------------------------------------------


@dataclass
class RemediationPlan:
    """按页面类型给出的**有限**补救方案。

    `options` 里的每个方案都是**官方确认过的 Extract 参数组合**；
    没有用任何未证实"可能有效"的参数。
    """

    page_type: str = PAGE_TYPE_GENERIC
    allowed: bool = True
    options: list[dict] = field(default_factory=list)
    reason: str = ""

    def as_detail(self) -> dict:
        return {"page_type": self.page_type, "allowed": self.allowed,
                "options": list(self.options), "reason": self.reason}


def plan_remediation(
    url: str,
    quality: ContentQuality,
    *,
    query: str = "",
    max_options: int = DEFAULT_MAX_REMEDIATIONS,
) -> RemediationPlan:
    """给出补救方案。**只按官方参数，且不超过 `max_options` 个。**

    顺序（优先官方正式选项）：
      1. `query` + `chunks_per_source`：官方的"按相关性重排内容块"选项，
         直接针对"拿到的是导航骨架"这个问题——它让 `raw_content` 变成
         **与 query 最相关的若干内容块**，而不是页面开头。
         （官方约束：`chunks_per_source` 仅在提供 `query` 时可用，1<=x<=5）
      2. `format=text`：官方的纯文本格式选项。

    对视频/社交页**不补救**：这类页面没有可提的文章正文，补救只会白花额度。
    """
    # 以**本次 URL** 为准判页面类型：传入的 quality 可能是另一个 URL 的评估结果。
    page_type = detect_page_type(url) if url else (quality.page_type or PAGE_TYPE_GENERIC)
    plan = RemediationPlan(page_type=page_type)

    if page_type in (PAGE_TYPE_VIDEO, PAGE_TYPE_SOCIAL):
        plan.allowed = False
        plan.reason = REASON_UNSUPPORTED_PAGE_TYPE
        return plan

    options: list[dict] = []
    # ① 官方 query + chunks_per_source（需要 query 才成立）
    if query:
        chunks = min(max(DEFAULT_CHUNKS_PER_SOURCE, OFFICIAL_CHUNKS_MIN),
                     OFFICIAL_CHUNKS_MAX)
        options.append({"query": query, "chunks_per_source": chunks})
    # ② 官方纯文本格式
    options.append({"format": "text"})

    if not query:
        # 没有研究目标文本时，① 不成立（官方约束），只剩 ②
        plan.reason = "无研究目标文本，官方 chunks_per_source 选项不可用"

    plan.options = options[:max(0, max_options)]
    if not plan.options:
        plan.allowed = False
    return plan
