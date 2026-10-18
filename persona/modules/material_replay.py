#!/usr/bin/env python
"""固定材料集：把一次真实检索/抓取的 HTTP 往返缓存下来，供后续运行**原样回放**。

# 为什么需要它（R4.6 的教训）

R4.5 与 R4.6 的目标、检索词、参数**完全相同**，10 个来源却只有 **5 个重合**——
Tavily 的排序与索引会变。后果是任何"条数对比"都不可归因：R4.6 的 `traits` 由 1 变 0，
真正的原因是 R4.5 里那两个"第二来源"（`維基大典`、`红楼梦` 站）本轮根本没检索到，
而不是提示词或归并逻辑变了。

> **不固定材料，"同一实验做两次"这句话就不成立。**

本模块把 `raw_responses.jsonl` 里 tavily 的往返原样读回，替换 `provider._urlopen`，
让检索/抓取走缓存、**物理上不联网**。模型调用**不缓存**——那样才能测模型在
同一批材料上的真实行为。

# 三条设计约束

1. **回放不记账**：`install_replay(..., isolate_usage=True)` 会把 provider 的账本换成
   一本干净的空账本。否则回放会把"1.0 credit × 2"记进真账本，费用口径就假了。
   回放期的检索/抓取 = 0 次真实调用、0 费用，报告里要如实这么写。
2. **缺项必须炸**：缓存里没有的请求一律抛 `MaterialReplayMiss`，**绝不回落到真实网络**
   ——"以为在回放、实际偷偷联网"是最坏的一种静默行为。
3. **探测句只改材料、不改流程**：`inject_probe()` 往指定检索词命中的第一份材料里追加
   一句话（如"黛玉侧材料里放一句宝玉的说法"），用来测"材料主体 ≠ 检索目标"时
   主体映射还准不准。它只改缓存里的响应正文，其余参数一概不动。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.usage_ledger import ExternalUsageLedger

#: 只回放检索侧（模型调用必须真实发生，否则测的是缓存不是模型）
REPLAY_URL_MARKERS = ("api.tavily.com",)

CACHE_FILENAME = "raw_responses.jsonl"


class MaterialReplayError(ValueError):
    """回放配置/缓存内容有问题（路径不对、没有可用往返、探测目标不存在）。"""


class MaterialReplayMiss(RuntimeError):
    """缓存里没有这次请求。

    **刻意抛异常而不是回落到网络**：回放模式一旦偷偷联网，实验的可比性就没了，
    而且没人会发现。宁可整轮失败。
    """


@dataclass(frozen=True)
class ReplayEntry:
    """一次 HTTP 往返的最小记录。`key = (url, request_body)`。"""

    url: str
    request: str
    response: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.url, self.request)


@dataclass
class ReplayCache:
    """`(url, request_body) → response_body`，保持文件里的原始顺序。"""

    entries: dict[tuple[str, str], ReplayEntry] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, url: str, body: str) -> str | None:
        entry = self.entries.get((url, body))
        return entry.response if entry is not None else None

    def queries(self) -> list[tuple[str, tuple[str, str]]]:
        """列出缓存里的检索词：`[(query, key), …]`（按文件顺序）。"""
        out: list[tuple[str, tuple[str, str]]] = []
        for key, entry in self.entries.items():
            if "/search" not in entry.url:
                continue
            try:
                payload = json.loads(entry.request or "{}")
            except ValueError:
                continue
            query = str(payload.get("query") or "")
            if query:
                out.append((query, key))
        return out

    def counts(self) -> dict[str, int]:
        return {
            "search": sum(1 for e in self.entries.values() if "/search" in e.url),
            "extract": sum(1 for e in self.entries.values() if "/extract" in e.url),
        }

    def inject_probe(self, *, query_contains: str, text: str,
                     index: int = -1) -> tuple["ReplayCache", str, str]:
        """往 `query_contains` 命中的材料里追加一句探测文本。

        返回（新缓存、被改写材料的 URL、注入位置）。**不改原缓存对象**——回放要可重复。
        找不到目标检索词 / 该检索没有结果 → 抛 `MaterialReplayError`，不静默跳过。

        # 注入位置的选择顺序（这是踩过的坑）

        优先 **`extract_fulltext`**：如果这次缓存里有一段抓取正文，且它的 URL 出现在
        该检索词的结果里，就把探测句追加到全文末尾。理由：

        - 全文材料**不会被检索摘要取代**（`MATERIAL_SUPERSEDED` 是"摘要 → 全文"单向），
          而往摘要里注入时，一旦那份摘要正好是要抓全文的源，注入句就静默消失了；
        - 追加在全文末尾**不影响"抓哪一条"的排序**（排序发生在检索结果上），
          不会造成回放缺项。

        只有当缓存里没有可用的抓取正文时，才退到 **`search_snippet`**（第 `index` 条结果，
        默认最后一条——同样是为了尽量不影响抽取目标的选择）。
        """
        if not text.strip():
            raise MaterialReplayError("探测文本为空")
        for query, key in self.queries():
            if query_contains not in query:
                continue
            entry = self.entries[key]
            try:
                payload = json.loads(entry.response)
            except ValueError as exc:
                raise MaterialReplayError(f"检索缓存不是合法 JSON：{query!r}") from exc
            results = payload.get("results") or []
            if not results:
                raise MaterialReplayError(f"检索 {query!r} 没有结果，无法注入探测句")
            urls = {str(item.get("url") or "") for item in results if isinstance(item, dict)}

            placed = self._probe_into_extract(urls, text)
            if placed is not None:
                return placed

            if not -len(results) <= index < len(results):
                raise MaterialReplayError(
                    f"检索 {query!r} 只有 {len(results)} 条结果，index={index} 越界")
            picked = results[index]
            if not isinstance(picked, dict):
                raise MaterialReplayError(f"检索 {query!r} 的结果形状异常")
            picked["content"] = str(picked.get("content") or "") + "\n" + text.strip()
            updated = dict(self.entries)
            updated[key] = ReplayEntry(
                url=entry.url, request=entry.request,
                response=json.dumps(payload, ensure_ascii=False))
            return (ReplayCache(entries=updated), str(picked.get("url") or ""),
                    "search_snippet")
        raise MaterialReplayError(f"缓存里没有包含 {query_contains!r} 的检索词")

    def _probe_into_extract(self, urls: set[str], text: str
                            ) -> tuple["ReplayCache", str, str] | None:
        """把探测句追加到"与该检索词结果同源"的抓取正文末尾。没有可用正文时返回 None。"""
        for key, entry in self.entries.items():
            if "/extract" not in entry.url:
                continue
            try:
                request = json.loads(entry.request or "{}")
                payload = json.loads(entry.response)
            except ValueError:
                continue
            requested = {str(u) for u in (request.get("urls") or [])}
            if not (requested & urls):
                continue
            for item in payload.get("results") or []:
                if not isinstance(item, dict):
                    continue
                if str(item.get("url") or "") not in urls:
                    continue
                if not item.get("raw_content"):
                    continue
                item["raw_content"] = str(item["raw_content"]) + "\n" + text.strip()
                updated = dict(self.entries)
                updated[key] = ReplayEntry(
                    url=entry.url, request=entry.request,
                    response=json.dumps(payload, ensure_ascii=False))
                return (ReplayCache(entries=updated), str(item.get("url") or ""),
                        "extract_fulltext")
        return None


# --------------------------------------------------------------------------
# 读缓存
# --------------------------------------------------------------------------
def resolve_cache_path(path: str | Path) -> Path:
    """接受"运行目录"或"raw_responses.jsonl 的路径"，两种都认。"""
    candidate = Path(path)
    if candidate.is_file():
        return candidate
    #: 隔离库的布局是 `<out>/real/raw_responses.jsonl`（dry 在 `<out>/dry/`）。
    #: 两者同时存在时**优先 real**——回放要的是真实材料，不是断网演练那一份。
    for option in (candidate / "real" / CACHE_FILENAME, candidate / CACHE_FILENAME):
        if option.exists():
            return option
    raise MaterialReplayError(
        f"{candidate} 下找不到 {CACHE_FILENAME}（也没找到 {CACHE_FILENAME} 在 real/ 里）")


def load_replay_cache(path: str | Path) -> ReplayCache:
    cache_path = resolve_cache_path(path)
    cache = ReplayCache()
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        url = str(entry.get("url") or "")
        response = entry.get("response")
        if not response or not any(marker in url for marker in REPLAY_URL_MARKERS):
            # 失败往返（`error`）与模型调用都不进缓存：
            # 前者没有可回放的内容，后者必须真实发生。
            continue
        cache.entries[(url, str(entry.get("request") or ""))] = ReplayEntry(
            url=url, request=str(entry.get("request") or ""), response=str(response))
    if not cache.entries:
        raise MaterialReplayError(f"{cache_path} 里没有可回放的检索/抓取往返")
    return cache


# --------------------------------------------------------------------------
# 安装回放
# --------------------------------------------------------------------------
class _CachedResponse:
    """`urlopen` 返回值的最小替身：只提供 `read()` 与上下文管理。"""

    def __init__(self, body: str) -> None:
        self._body = body.encode("utf-8")
        self.headers: dict[str, str] = {"content-type": "application/json"}
        self.status = 200

    def read(self, *args, **kwargs) -> bytes:
        return self._body

    def getcode(self) -> int:
        return 200

    def __enter__(self) -> "_CachedResponse":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def close(self) -> None:
        return None


def inject_probe_via(
    path: str | Path,
    *,
    query_contains: str,
    text: str,
    index: int = -1,
) -> tuple[ReplayCache, str, str]:
    """`load_replay_cache(path).inject_probe(...)` 的便捷写法（脚本用）。"""
    return load_replay_cache(path).inject_probe(
        query_contains=query_contains, text=text, index=index)


@dataclass
class ReplaySession:
    """回放期的计数与账本，供运行报告如实说明"没有真实调用"。"""

    used: dict[tuple[str, str], int] = field(default_factory=dict)
    misses: list[tuple[str, str]] = field(default_factory=list)
    ledger: ExternalUsageLedger | None = None

    def hits(self, kind: str) -> int:
        needle = f"/{kind}"
        return sum(n for (url, _), n in self.used.items() if needle in url)

    def miss_count(self) -> int:
        return len(self.misses)

    @property
    def unique_hits(self) -> int:
        return len(self.used)


class _DiscardingUsageLedger(ExternalUsageLedger):
    """回放期账本：接受 `settle()`，但**什么都留不下**（D-292）。

    只把 `provider.usage_ledger` 换成一本普通空账本是不够的：
    `research._persist_usage()` 遍历的**正是**这本账本，回放时 `settle()` 写进去的
    检索/抓取笔会在收尾时被原样 `record_external_usage()` 落库。
    R4.7 实测：内存账本干净（只有 2 笔模型），`external_usage_records` 里却多出
    `SEARCH 1.0 + SEARCH 1.0 + EXTRACT 0.0 = 2.0 credits`，`request_id` 还是上一轮的
    真 id ⇒ "回放不记账"**只在内存成立、落库层不成立**，假支出还带着真 id。

    这里让 `settle()` 不写入、`records()` 恒空：内存与落库同时干净，
    而"回放命中了什么"由 `ReplaySession.used` 负责（不靠账本）。
    """

    def settle(self, record: Any) -> bool:      # noqa: D401 - 语义见类说明
        return False

    def records(self) -> list[Any]:
        return []


def install_replay(
    provider: Any,
    cache: ReplayCache,
    *,
    isolate_usage: bool = True,
) -> ReplaySession:
    """把 `provider._urlopen` 换成读缓存，并（默认）换一本干净账本。

    `provider` 只要求有 `_urlopen` 与 `usage_ledger` 两个属性——生产里是
    `TavilyProvider`，测试里可以是任何桩。
    """
    session = ReplaySession()

    def replay_open(req, *args, **kwargs):
        url = str(getattr(req, "full_url", "") or "")
        body = getattr(req, "data", b"") or b""
        body_text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
        cached = cache.lookup(url, body_text)
        if cached is None:
            session.misses.append((url, body_text))
            raise MaterialReplayMiss(
                f"回放缓存里没有这次请求：{url}\n"
                f"  请求体：{body_text[:200]}\n"
                "  回放模式**不允许回落到真实网络**——请确认用的是同一批材料"
                "（检索词/参数一致），或重新录一次缓存。")
        session.used[(url, body_text)] = session.used.get((url, body_text), 0) + 1
        return _CachedResponse(cached)

    provider._urlopen = replay_open
    if isolate_usage:
        # 回放不花钱：给 provider 一本**写了也不留**的账本（D-292）。
        # 用普通空账本只挡住了内存，落库照样会把回放笔搬进 external_usage_records。
        session.ledger = _DiscardingUsageLedger()
        provider.usage_ledger = session.ledger
    return session
