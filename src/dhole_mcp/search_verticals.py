"""``web_search(sources=...)`` 的无密钥垂直搜索源。

每个公开 API 一个适配器，不要 key、不要账号：

* ``academic`` -> arXiv Atom API
* ``news``     -> GDELT DOC 2.0 文章列表（真实发布者 URL + 日期）
* ``qa``       -> Stack Exchange API（Stack Overflow）
* ``code``     -> GitHub 仓库搜索（按 star 排序）
* ``community``-> Hacker News Algolia

这些行带着与网页路相同的 ``published`` / ``published_precision`` /
``published_basis`` 字段，所以现有重排器、日期账与 ``fetch_content`` 扇出原样
可用。垂直源**不是**一个网页索引家族：它的行永不参与 ``engines_consensus``
（调用方看到空 consensus 串，并在 ``source`` 里看到后端名）。

每次网络调用都受 ``timeout`` 约束；失败交回一行 ``VerticalReport`` 而不是抛
异常，因为一个死掉的源不能抹掉其它源的答案。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from time import monotonic
from typing import Awaitable, Callable, Optional
from urllib.parse import quote_plus, urlparse

import httpx
from lxml import etree

VERTICAL_KINDS = ("news", "academic", "qa", "code", "community")
SOURCE_KINDS = ("web",) + VERTICAL_KINDS

VERTICAL_TIMEOUT = 8.0  # 单源预算：一个慢源不能拖住整轮
MAX_PER_SOURCE = 50     # 单源上限：调用方的 max_results 才是它要的数量

_USER_AGENT = "dhole-mcp (https://github.com/ouli-1242/dhole-mcp)"
_ATOM = "http://www.w3.org/2005/Atom"

Fetcher = Callable[[str, dict[str, str], float], Awaitable[tuple[int, bytes]]]


@dataclass
class VerticalHit:
    """One normalised result row from a keyless vertical source.

    ``published``/``published_precision``/``published_basis`` use the same date ledger as
    web rows; when the source gives no usable date all three stay empty — never backfilled
    with the crawl time.
    """

    kind: str
    backend: str
    rank: int
    title: str
    url: str
    snippet: str
    published: str = ""
    published_precision: str = ""
    published_basis: str = ""


@dataclass
class VerticalReport:
    """One source's receipt: how it answered, and why.

    Network failure, timeout, blocked and unreadable body all land here as a row with a
    ``status`` instead of raising: one dead source must not erase the others' answers.
    """

    source: str
    backend: str
    status: str = "error"       # ok | empty | blocked | timeout | error
    count: int = 0
    delivered: int = 0
    error: str = ""
    duration_ms: float = 0.0

    def as_dict(self) -> dict:
        """The receipt row in wire shape; ``error`` is omitted when empty.
        """
        row = {
            "source": self.source,
            "backend": self.backend,
            "status": self.status,
            "count": self.count,
            "delivered": self.delivered,
            "duration_ms": round(self.duration_ms, 1),
        }
        if self.error:
            row["error"] = self.error
        return row


def _clean(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate(text: str, limit: int) -> str:
    text = _clean(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _safe_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return raw


def _iso_day(raw: object) -> tuple[str, str, str]:
    value = str(raw or "").strip()
    if not value:
        return "", "", ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "", "", ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.date().isoformat(), "day", value[:80]


def _gdelt_day(raw: object) -> tuple[str, str, str]:
    value = str(raw or "").strip()
    digits = re.sub(r"\D", "", value)
    if len(digits) < 8:
        return "", "", ""
    try:
        dt = datetime.strptime(digits[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return "", "", ""
    return dt.date().isoformat(), "day", value[:80]


def _epoch_day(raw: object) -> tuple[str, str, str]:
    value = str(raw or "").strip()
    try:
        dt = datetime.fromtimestamp(int(float(value)), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return "", "", ""
    return dt.date().isoformat(), "day", f"epoch:{value}"[:80]


def _as_day(hit: VerticalHit) -> Optional[date]:
    if hit.published_precision != "day" or not hit.published:
        return None
    try:
        return date.fromisoformat(hit.published)
    except ValueError:
        return None


def _hit(kind: str, backend: str, rank: int, title: object, url: object,
         snippet: object, published: tuple[str, str, str] = ("", "", "")) -> Optional[VerticalHit]:
    title_s = _clean(title)
    url_s = _safe_url(url)
    if not title_s or not url_s:
        return None
    # 420 字符：够读完一条摘要，又不让单行撑爆转录。
    return VerticalHit(
        kind=kind, backend=backend, rank=rank,
        title=title_s, url=url_s, snippet=_truncate(str(snippet or ""), 420),
        published=published[0], published_precision=published[1],
        published_basis=published[2],
    )


def _http_error(status: int) -> tuple[str, str]:
    if status in (401, 403, 429):
        return "blocked", f"HTTP {status} (rate limited or refused)"
    return "error", f"HTTP {status}"


async def _http_get(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    kwargs: dict = {}
    try:
        from dhole_mcp.search_proxy import configured_proxy
        proxy, _source = configured_proxy()
        if proxy:
            kwargs["proxy"] = proxy
    except Exception:
        pass
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT, **headers},
        **kwargs,
    ) as client:
        resp = await client.get(url)
        return resp.status_code, resp.content


async def _get(url: str, headers: dict[str, str], timeout: float,
               fetch: Optional[Fetcher]) -> tuple[int, bytes]:
    if fetch is not None:
        return await fetch(url, headers, timeout)
    return await _http_get(url, headers, timeout)


def _xml_parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, recover=True)


async def _search_arxiv(query: str, n: int, page: int, region: str,
                        language: Optional[str], timeout: float,
                        fetch: Optional[Fetcher]):
    start = max(0, page) * n
    # arXiv 的相关度排序对未加引号的多词查询足够宽松，以至于标题正是
    # "Attention Is All You Need" 的那篇论文，用自己的标题都进不了前五
    # （2026-10-20 实测，冻结在 evals/corpus/verticals）。短语查询把它放回第 2；
    # 宽泛的短语查询（"transformer attention"、"large language model"）仍然返回
    # 相关工作。布尔运算符保持原义，不加引号。
    phrase = query.strip()
    if not re.search(r"\b(AND|OR|ANDNOT)\b", phrase):
        phrase = f'"{phrase}"'
    url = (
        "https://export.arxiv.org/api/query?search_query=all:"
        f"{quote_plus(phrase)}&start={start}&max_results={n}"
        "&sortBy=relevance&sortOrder=descending"
    )
    status, body = await _get(url, {"Accept": "application/atom+xml"}, timeout, fetch)
    if status != 200:
        return [], "arxiv", *_http_error(status)
    try:
        root = etree.fromstring(body, parser=_xml_parser())
    except Exception as exc:
        return [], "arxiv", "error", f"unreadable Atom ({type(exc).__name__})"
    hits: list[VerticalHit] = []
    for i, entry in enumerate(root.findall(f"{{{_ATOM}}}entry"), start=1):
        authors = [
            _clean(a.text)
            for a in entry.findall(f"{{{_ATOM}}}author/{{{_ATOM}}}name")
            if _clean(a.text)
        ]
        title = entry.findtext(f"{{{_ATOM}}}title", default="")
        link = entry.findtext(f"{{{_ATOM}}}id", default="")
        abstract = entry.findtext(f"{{{_ATOM}}}summary", default="")
        published = _iso_day(entry.findtext(f"{{{_ATOM}}}published", default=""))
        prefix = f"{', '.join(authors[:3])} · " if authors else ""
        row = _hit("academic", "arxiv", i, title, link.replace("http://", "https://", 1),
                   prefix + abstract, published)
        if row:
            hits.append(row)
    return hits, "arxiv", ("ok" if hits else "empty"), ""


async def _search_gdelt(query: str, n: int, page: int, region: str,
                        language: Optional[str], timeout: float,
                        fetch: Optional[Fetcher]):
    url = (
        "https://api.gdeltproject.org/api/v2/doc/doc?query="
        f"{quote_plus(query)}&mode=artlist&format=json&maxrecords={n}&sort=hybridrel"
    )
    if page:
        url += f"&startrecord={page * n}"
    status, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status == 429:
        return [], "gdelt", "blocked", "GDELT rate limit (one query per 5s)"
    if status != 200:
        return [], "gdelt", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "gdelt", "error", f"unreadable JSON ({type(exc).__name__})"
    hits: list[VerticalHit] = []
    for i, item in enumerate(data.get("articles") or [], start=1):
        bits = [item.get("domain"), item.get("language"), item.get("sourcecountry")]
        snippet = " · ".join(str(b) for b in bits if b)
        row = _hit("news", "gdelt", i, item.get("title"), item.get("url"),
                   snippet, _gdelt_day(item.get("seendate")))
        if row:
            hits.append(row)
    return hits, "gdelt", ("ok" if hits else "empty"), ""


async def _search_stackexchange(query: str, n: int, page: int, region: str,
                                language: Optional[str], timeout: float,
                                fetch: Optional[Fetcher]):
    url = (
        "https://api.stackexchange.com/2.3/search/advanced?"
        "order=desc&sort=relevance&q=" + quote_plus(query) +
        f"&site=stackoverflow&pagesize={n}&page={page + 1}&filter=default"
    )
    status, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status != 200:
        return [], "stackexchange", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "stackexchange", "error", f"unreadable JSON ({type(exc).__name__})"
    hits: list[VerticalHit] = []
    for i, item in enumerate(data.get("items") or [], start=1):
        tags = ", ".join((item.get("tags") or [])[:4])
        snippet = (
            f"score {item.get('score', 0)} · {item.get('answer_count', 0)} answers"
            + (" · accepted" if item.get("is_answered") else "")
            + (f" · {tags}" if tags else "")
        )
        row = _hit("qa", "stackexchange", i, item.get("title"), item.get("link"),
                   snippet, _epoch_day(item.get("creation_date")))
        if row:
            hits.append(row)
    return hits, "stackexchange", ("ok" if hits else "empty"), ""


async def _search_github(query: str, n: int, page: int, region: str,
                         language: Optional[str], timeout: float,
                         fetch: Optional[Fetcher]):
    # 搜仓库，不搜 issue：宽泛词的 issue 搜索会被日常工程日志与一句话笔记淹没。
    # 仓库搜索才是"这段代码/这个项目在哪"的耐久答案，star 给读者的质量信号是
    # issue 流没有的。
    url = (
        "https://api.github.com/search/repositories?q=" + quote_plus(query) +
        f"&sort=stars&order=desc&per_page={n}&page={page + 1}"
    )
    status, body = await _get(url, {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }, timeout, fetch)
    if status in (403, 429):
        detail = ""
        try:
            detail = _clean((json.loads(body) or {}).get("message", ""))
        except Exception:
            pass
        return [], "github", "blocked", (
            f"HTTP {status} (search rate limit; unauthenticated GitHub allows "
            f"10 requests/min)" + (f": {detail[:120]}" if detail else ""))
    if status != 200:
        return [], "github", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "github", "error", f"unreadable JSON ({type(exc).__name__})"
    hits: list[VerticalHit] = []
    for i, item in enumerate(data.get("items") or [], start=1):
        stars = item.get("stargazers_count", 0)
        lang = item.get("language") or ""
        updated = _iso_day(item.get("updated_at"))[0]
        bits = [f"★{stars}", lang, f"updated {updated}" if updated else ""]
        snippet = " · ".join(bit for bit in bits if bit)
        if item.get("description"):
            snippet += f" · {_clean(item.get('description'))[:260]}"
        row = _hit("code", "github", i, item.get("full_name"), item.get("html_url"),
                   snippet, _iso_day(item.get("created_at")))
        if row:
            hits.append(row)
    return hits, "github", ("ok" if hits else "empty"), ""


async def _search_community(query: str, n: int, page: int, region: str,
                            language: Optional[str], timeout: float,
                            fetch: Optional[Fetcher]):
    url = (
        "https://hn.algolia.com/api/v1/search?query=" + quote_plus(query) +
        f"&tags=story&hitsPerPage={n}&page={page}"
    )
    status, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status != 200:
        return [], "hackernews", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "hackernews", "error", f"unreadable JSON ({type(exc).__name__})"
    hits: list[VerticalHit] = []
    for i, item in enumerate(data.get("hits") or [], start=1):
        object_id = item.get("objectID")
        url_value = item.get("url") or (
            f"https://news.ycombinator.com/item?id={object_id}" if object_id else "")
        snippet = (
            f"HN · {item.get('points', 0)} points · {item.get('num_comments', 0)} comments"
            + (f" · {_clean(item.get('story_text'))[:240]}" if item.get("story_text") else "")
        )
        row = _hit("community", "hackernews", i, item.get("title"), url_value,
                   snippet, _iso_day(item.get("created_at")))
        if row:
            hits.append(row)
    return hits, "hackernews", ("ok" if hits else "empty"), ""


_ADAPTERS = {
    "news": _search_gdelt,
    "academic": _search_arxiv,
    "qa": _search_stackexchange,
    "code": _search_github,
    "community": _search_community,
}

_BACKENDS = {
    "news": "gdelt",
    "academic": "arxiv",
    "qa": "stackexchange",
    "code": "github",
    "community": "hackernews",
}


async def search_one(kind: str, query: str, max_results: int, *,
                     page: int = 0, region: str = "us-en",
                     language: Optional[str] = None,
                     timeout: float = VERTICAL_TIMEOUT,
                     fetch: Optional[Fetcher] = None
                     ) -> tuple[list[VerticalHit], VerticalReport]:
    """Ask one vertical backend once.

    Args:
        kind: One of :data:`VERTICAL_KINDS` (news/academic/qa/code/community).
        query: The search text, handed to the backend as-is.
        max_results: How many rows to want; clamped to ``1..MAX_PER_SOURCE``.
        page: A 0-based page number; each adapter converts it to its own offset.
        region: A backend region hint (not every adapter uses it).
        language: An optional language hint.
        timeout: The per-call network budget in seconds.
        fetch: An injected ``(url, headers, timeout) -> (status, bytes)`` transport;
            ``None`` uses httpx with the configured proxy.

    Returns:
        ``(hits, report)``. Timeouts, network errors, blocks and unreadable bodies become
        an error/blocked report with empty ``hits``; those expected failures do not raise.

    Raises:
        KeyError: ``kind`` is not a registered vertical.
    """
    backend = _BACKENDS[kind]
    started = monotonic()
    n = max(1, min(max_results, MAX_PER_SOURCE))
    try:
        hits, backend, status, error = await _ADAPTERS[kind](
            query, n, page, region, language, timeout, fetch)
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        hits, status, error = [], "timeout", f"{type(exc).__name__}: no answer in {timeout:g}s"
    except Exception as exc:
        _detail = str(exc).strip()
        hits, status, error = [], "error", (
            f"{type(exc).__name__}: {_detail[:160]}" if _detail
            else type(exc).__name__)
    report = VerticalReport(
        source=kind, backend=backend,
        status=status, count=len(hits), error=error,
        duration_ms=(monotonic() - started) * 1000,
    )
    return hits, report


def _interleave_by_backend(hits: list[VerticalHit]) -> list[VerticalHit]:
    """按后端轮转交错每个后端的命中列表，保留各后端自己的顺序。

    `sources=` 的顺序不是排名信号。轻装安装（没有 reranker）时位置参与打分，
    所以先把第一个点名的源全部交完再交第二个，会把参数顺序变成权威。这与网页池
    跨引擎用的是同一种合并。
    """
    buckets: dict[str, list[VerticalHit]] = {}
    for hit in hits:
        buckets.setdefault(hit.backend, []).append(hit)
    queues = [list(rows) for rows in buckets.values()]
    out: list[VerticalHit] = []
    while queues:
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
        queues = [queue for queue in queues if queue]
    return out


async def search_many(kinds: list[str], query: str, max_results: int, *,
                      page: int = 0, region: str = "us-en",
                      language: Optional[str] = None,
                      timeout: float = VERTICAL_TIMEOUT,
                      fetch: Optional[Fetcher] = None
                      ) -> tuple[list[VerticalHit], list[VerticalReport]]:
    """Ask several vertical backends concurrently.

    Returns:
        ``(hits, reports)`` — one report per requested kind, in request order. Hit rows are
        interleaved round-robin by backend, so their order is not a ranking signal. A
        backend that raises contributes an error report and does not cancel the others.
    """
    tasks = [
        asyncio.create_task(search_one(
            kind, query, max_results, page=page, region=region,
            language=language, timeout=timeout, fetch=fetch))
        for kind in kinds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    hits: list[VerticalHit] = []
    reports: list[VerticalReport] = []
    for kind, result in zip(kinds, results):
        if isinstance(result, BaseException):
            _detail = str(result).strip()
            reports.append(VerticalReport(
                source=kind, backend=_BACKENDS[kind], status="error",
                error=(f"{type(result).__name__}: {_detail[:160]}" if _detail
                       else type(result).__name__)))
            continue
        rows, report = result
        hits.extend(rows)
        reports.append(report)
    return _interleave_by_backend(hits), reports


def filter_by_window(hits: list[VerticalHit], after: Optional[date] = None,
                     before: Optional[date] = None
                     ) -> tuple[list[VerticalHit], dict]:
    """对垂直行应用一个精确的日期窗口。

    ``after`` 会丢掉没有日期的行（无法证明它们在窗口内）；``before`` 保留它们，
    与网页路的逐行账完全一致。返回保留的行与消失的计数。
    """
    kept: list[VerticalHit] = []
    dropped_after = dropped_before = undated_after = 0
    for hit in hits:
        day = _as_day(hit)
        if after is not None:
            if day is None:
                undated_after += 1
                continue
            if day < after:
                dropped_after += 1
                continue
        if before is not None and day is not None and day > before:
            dropped_before += 1
            continue
        kept.append(hit)
    return kept, {
        "dropped_after": dropped_after,
        "dropped_before": dropped_before,
        "undated_dropped_after": undated_after,
    }
