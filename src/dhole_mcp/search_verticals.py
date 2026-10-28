"""``web_search(sources=...)`` 的无密钥垂直搜索源。

每个公开 API 一个适配器，不要 key、不要账号：

* ``academic`` -> arXiv Atom API + Crossref works（两个后端，按标题去重）
* ``news``     -> GDELT DOC 2.0 文章列表（真实发布者 URL + 日期）
* ``qa``       -> Stack Exchange API（Stack Overflow）
* ``code``     -> GitHub 仓库搜索（按 star 排序）
* ``community``-> Hacker News Algolia
* ``package``  -> npm / PyPI / crates.io / Go proxy 的**精确包名**元数据
* ``repo``     -> GitHub REST 的单个仓库健康度（stars / issues / 最近提交）

这些行带着与网页路相同的 ``published`` / ``published_precision`` /
``published_basis`` 字段，所以现有重排器、日期账与 ``fetch_content`` 扇出原样
可用。垂直源**不是**一个网页索引家族：它的行永不参与 ``engines_consensus``
（调用方看到空 consensus 串，并在 ``source`` 里看到后端名）。

一个 kind 可以有多个后端（``academic`` 的 arXiv 与 Crossref）。回执按**后端**
各一行：一个后端被限流不会把同 kind 的另一个也判成失败。

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
from urllib.parse import quote, quote_plus, urlparse

import httpx
from lxml import etree

VERTICAL_KINDS = ("news", "academic", "qa", "code", "community", "package", "repo")
SOURCE_KINDS = ("web",) + VERTICAL_KINDS

VERTICAL_TIMEOUT = 8.0  # 单源预算：一个慢源不能拖住整轮
MAX_PER_SOURCE = 50     # 单源上限：调用方的 max_results 才是它要的数量

_USER_AGENT = "dhole-mcp (https://github.com/ouli-1242/dhole-mcp)"
_ATOM = "http://www.w3.org/2005/Atom"

Fetcher = Callable[[str, dict[str, str], float], Awaitable[tuple[int, bytes]]]
Adapter = Callable[[str, int, int, str, Optional[str], float, Optional[Fetcher]],
                   Awaitable[tuple[list["VerticalHit"], str, str, str]]]


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
    status: str = "error"       # ok | empty | not_found | bad_query | blocked | timeout | error
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


async def _search_crossref(query: str, n: int, page: int, region: str,
                           language: Optional[str], timeout: float,
                           fetch: Optional[Fetcher]):
    url = (
        "https://api.crossref.org/works?query.bibliographic=" + quote_plus(query) +
        f"&rows={n}&offset={max(0, page) * n}"
        "&select=DOI,title,author,abstract,issued,URL,container-title"
    )
    status, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status in (401, 403, 429):
        return [], "crossref", "blocked", (
            f"HTTP {status} (Crossref rate limit or polite-pool refusal)")
    if status != 200:
        return [], "crossref", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "crossref", "error", f"unreadable JSON ({type(exc).__name__})"
    hits: list[VerticalHit] = []
    for i, item in enumerate(((data.get("message") or {}).get("items") or []),
                             start=1):
        if not isinstance(item, dict):
            continue
        doi = str(item.get("DOI") or "").strip()
        title = item.get("title")
        title = title[0] if isinstance(title, list) and title else title
        link = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
        authors = [
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in (item.get("author") or []) if isinstance(a, dict)
        ]
        venue = item.get("container-title")
        venue = venue[0] if isinstance(venue, list) and venue else venue
        published = _crossref_day((item.get("issued") or {}).get("date-parts"))
        abstract = _clean(item.get("abstract"))
        bits = [f"{', '.join(authors[:3])} · " if authors else "", venue or "",
                published[0], f"DOI {doi}" if doi else ""]
        prefix = " · ".join(bit for bit in bits if bit)
        snippet = prefix + (f" · {abstract}" if abstract else "")
        row = _hit("academic", "crossref", i, title, link, snippet, published)
        if row:
            hits.append(row)
    return hits, "crossref", ("ok" if hits else "empty"), ""


def _crossref_day(value: object) -> tuple[str, str, str]:
    """Crossref 的 ``date-parts`` -> (ISO 日, 精度, 原始依据)。

    只有年或只有年月时补到 1 月 1 日并降低精度：日期账只让 precision=day 参与
    精确窗口与 newest 排序，补出来的"1 日"不许冒充发布日。
    """
    try:
        parts = list(value[0])  # type: ignore[index]
    except (TypeError, IndexError, KeyError):
        return "", "", ""
    values: list[int] = []
    for part in parts[:3]:
        try:
            values.append(int(part))
        except (TypeError, ValueError):
            return "", "", ""
    if not values:
        return "", "", ""
    month = values[1] if len(values) > 1 else 1
    day = values[2] if len(values) > 2 else 1
    try:
        stamp = date(values[0], month, day)
    except ValueError:
        return "", "", ""
    precision = ("day", "month", "year")[3 - len(values)]
    return stamp.isoformat(), precision, "-".join(str(v) for v in values)[:80]


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
        return [], "github_repo", "blocked", (
            f"HTTP {status} (search rate limit; unauthenticated GitHub allows "
            f"10 requests/min)" + (f": {detail[:120]}" if detail else ""))
    if status != 200:
        return [], "github_repo", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "github_repo", "error", f"unreadable JSON ({type(exc).__name__})"
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


_PACKAGE_NAME = re.compile(r"^@?[A-Za-z0-9][A-Za-z0-9._~/-]*$")
_KEYWORDS_REFUSAL = (
    "package= takes an exact package name, not keywords - use the normal web "
    "pool for keyword search")


def _package_name(query: str, backend: str, *,
                  allow_slash: bool) -> tuple[str, str, str]:
    """精确包名的门口。返回 ``(name, status, error)``；status 非空即拒绝。

    带空格的查询在任何 registry 上都不是包名 —— 发出去只会换回 404 或更糟的
    模糊匹配，所以一个请求都不发，直接说清楚该换哪条路。
    """
    name = (query or "").strip()
    if not name:
        return "", "bad_query", "package= needs an exact package name"
    if any(ch.isspace() for ch in name):
        return "", "bad_query", _KEYWORDS_REFUSAL
    if len(name) > 214:
        return "", "bad_query", "that is longer than any package name"
    if not _PACKAGE_NAME.fullmatch(name) or ".." in name:
        return "", "bad_query", f"{name[:60]!r} is not a valid {backend} package name"
    if not allow_slash and "/" in name:
        return "", "bad_query", f"{name[:60]!r} is not a valid {backend} package name"
    return name, "", ""


async def _search_npm(query: str, n: int, page: int, region: str,
                      language: Optional[str], timeout: float,
                      fetch: Optional[Fetcher]):
    name, status, error = _package_name(query, "npm", allow_slash=True)
    if status:
        return [], "npm", status, error
    if "/" in name and not name.startswith("@"):
        return [], "npm", "bad_query", (
            f"{name[:60]!r} is not a valid npm name (a slash needs the @scope/)")
    url = "https://registry.npmjs.org/" + quote(name, safe="@/")
    status_code, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status_code == 404:
        return [], "npm", "empty", f"no package named {name!r} on npm"
    if status_code in (401, 403, 429):
        return [], "npm", "blocked", f"HTTP {status_code} from the npm registry"
    if status_code != 200:
        return [], "npm", *_http_error(status_code)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "npm", "error", f"unreadable JSON ({type(exc).__name__})"
    latest = str((data.get("dist-tags") or {}).get("latest") or "")
    version = (data.get("versions") or {}).get(latest) or {}
    stamp = (data.get("time") or {}).get(latest) or (data.get("time") or {}).get("modified")
    published = _iso_day(stamp)
    repository = version.get("repository") or data.get("repository") or ""
    if isinstance(repository, dict):
        repository = repository.get("url") or ""
    repository = str(repository).removeprefix("git+").removesuffix(".git")
    dependencies = version.get("dependencies") or {}
    bits = [f"v{latest}" if latest else "", _clean(version.get("license")),
            f"updated {published[0]}" if published[0] else "",
            f"deps={len(dependencies)}" if dependencies else "", repository]
    snippet = " · ".join(bit for bit in bits if bit)
    description = _clean(version.get("description") or data.get("description"))
    row = _hit("package", "npm", 1, f"{name} (npm)",
               f"https://www.npmjs.com/package/{name}",
               snippet + (f" · {description}" if description else ""), published)
    return ([row] if row else []), "npm", ("ok" if row else "empty"), ""


async def _search_pypi(query: str, n: int, page: int, region: str,
                       language: Optional[str], timeout: float,
                       fetch: Optional[Fetcher]):
    name, status, error = _package_name(query, "PyPI", allow_slash=False)
    if status:
        return [], "pypi", status, error
    url = f"https://pypi.org/pypi/{quote(name)}/json"
    status_code, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status_code == 404:
        return [], "pypi", "empty", f"no package named {name!r} on PyPI"
    if status_code != 200:
        return [], "pypi", *_http_error(status_code)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "pypi", "error", f"unreadable JSON ({type(exc).__name__})"
    info = data.get("info") or {}
    version = str(info.get("version") or "")
    files = ((data.get("releases") or {}).get(version) or [])
    stamp = ""
    if files and isinstance(files[0], dict):
        stamp = files[0].get("upload_time_iso_8601") or files[0].get("upload_time") or ""
    published = _iso_day(stamp)
    requires = [r for r in (info.get("requires_dist") or [])
                if isinstance(r, str) and "extra ==" not in r]
    project_urls = info.get("project_urls") or {}
    repository = (project_urls.get("Source") or project_urls.get("Repository")
                  or info.get("home_page") or "")
    license_name = _clean(info.get("license_expression") or info.get("license"))[:60]
    bits = [f"v{version}" if version else "", license_name,
            f"updated {published[0]}" if published[0] else "",
            f"deps={len(requires)}" if requires else "", str(repository)[:120]]
    snippet = " · ".join(bit for bit in bits if bit)
    summary = _clean(info.get("summary"))
    row = _hit("package", "pypi", 1, f"{info.get('name') or name} (PyPI)",
               f"https://pypi.org/project/{name}/",
               snippet + (f" · {summary}" if summary else ""), published)
    return ([row] if row else []), "pypi", ("ok" if row else "empty"), ""


async def _search_crates(query: str, n: int, page: int, region: str,
                         language: Optional[str], timeout: float,
                         fetch: Optional[Fetcher]):
    name, status, error = _package_name(query, "crates.io", allow_slash=False)
    if status:
        return [], "crates", status, error
    url = f"https://crates.io/api/v1/crates/{quote(name)}"
    status_code, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status_code == 404:
        return [], "crates", "empty", f"no crate named {name!r} on crates.io"
    if status_code != 200:
        return [], "crates", *_http_error(status_code)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "crates", "error", f"unreadable JSON ({type(exc).__name__})"
    crate = data.get("crate") or {}
    versions = data.get("versions") or []
    version = versions[0] if versions and isinstance(versions[0], dict) else {}
    number = str(version.get("num") or crate.get("max_stable_version") or "")
    published = _iso_day(version.get("created_at") or crate.get("updated_at"))
    downloads = crate.get("downloads")
    bits = [f"v{number}" if number else "", _clean(version.get("license")),
            f"updated {published[0]}" if published[0] else "",
            f"{downloads} downloads" if downloads else "",
            str(crate.get("repository") or "")[:120]]
    snippet = " · ".join(bit for bit in bits if bit)
    description = _clean(crate.get("description"))
    row = _hit("package", "crates", 1, f"{crate.get('name') or name} (crates.io)",
               f"https://crates.io/crates/{name}",
               snippet + (f" · {description}" if description else ""), published)
    return ([row] if row else []), "crates", ("ok" if row else "empty"), ""


async def _search_go(query: str, n: int, page: int, region: str,
                     language: Optional[str], timeout: float,
                     fetch: Optional[Fetcher]):
    name, status, error = _package_name(query, "Go", allow_slash=True)
    if status:
        return [], "go", status, error
    if "/" not in name:
        return [], "go", "empty", (
            "Go modules need a full path (e.g. github.com/owner/repo)")
    url = "https://proxy.golang.org/" + quote(name, safe="/-._~") + "/@latest"
    status_code, body = await _get(url, {"Accept": "application/json"}, timeout, fetch)
    if status_code in (404, 410):
        return [], "go", "empty", f"no module named {name!r} on the Go proxy"
    if status_code != 200:
        return [], "go", *_http_error(status_code)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "go", "error", f"unreadable JSON ({type(exc).__name__})"
    version = str(data.get("Version") or "")
    published = _iso_day(data.get("Time"))
    bits = [version, f"updated {published[0]}" if published[0] else ""]
    row = _hit("package", "go", 1, f"{name} (Go)",
               f"https://pkg.go.dev/{name}",
               " · ".join(bit for bit in bits if bit), published)
    return ([row] if row else []), "go", ("ok" if row else "empty"), ""


_REPO_SHAPE = re.compile(
    r"github\.com[:/]+([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?(?:[/?#]|$)")


def _repo_slug(query: str) -> Optional[tuple[str, str]]:
    text = (query or "").strip()
    match = _REPO_SHAPE.search(text)
    if match:
        return match.group(1), match.group(2)
    parts = text.removeprefix("https://").removeprefix("http://").strip("/").split("/")
    if len(parts) != 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if (re.fullmatch(r"[A-Za-z0-9_.-]+", owner)
            and re.fullmatch(r"[A-Za-z0-9_.-]+", repo)):
        return owner, repo
    return None


async def _search_repo(query: str, n: int, page: int, region: str,
                       language: Optional[str], timeout: float,
                       fetch: Optional[Fetcher]):
    slug = _repo_slug(query)
    if slug is None:
        return [], "github_repo", "bad_query", (
            "repo= takes owner/repo or a github.com URL, not keywords - use "
            "sources=['code'] to search repositories by words")
    owner, repo = slug
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    status, body = await _get(f"https://api.github.com/repos/{owner}/{repo}",
                              headers, timeout, fetch)
    if status == 404:
        return [], "github_repo", "not_found", (
            f"{owner}/{repo} does not exist, or is private (an unauthenticated "
            "call cannot see it)")
    if status in (403, 429):
        detail = ""
        try:
            detail = _clean((json.loads(body) or {}).get("message", ""))
        except Exception:
            pass
        return [], "github_repo", "blocked", (
            f"HTTP {status} (unauthenticated GitHub REST allows 60 requests/hour)"
            + (f": {detail[:120]}" if detail else ""))
    if status != 200:
        return [], "github_repo", *_http_error(status)
    try:
        data = json.loads(body)
    except Exception as exc:
        return [], "github_repo", "error", f"unreadable JSON ({type(exc).__name__})"

    last_commit = ""
    try:
        commit_status, commit_body = await _get(
            f"https://api.github.com/repos/{owner}/{repo}/commits?per_page=1",
            headers, timeout, fetch)
        if commit_status == 200:
            commits = json.loads(commit_body)
            if isinstance(commits, list) and commits:
                commit = commits[0].get("commit") or {}
                last_commit = ((commit.get("committer") or {}).get("date")
                               or (commit.get("author") or {}).get("date") or "")
    except Exception:
        last_commit = ""
    stamp = last_commit or data.get("pushed_at") or ""
    published = _iso_day(stamp)

    license_info = data.get("license") or {}
    bits = [f"★{data.get('stargazers_count', 0)}",
            f"{data.get('forks_count', 0)} forks",
            f"{data.get('open_issues_count', 0)} open issues",
            f"last commit {published[0]}" if published[0] else "",
            _clean(license_info.get("spdx_id") or license_info.get("name")),
            str(data.get("language") or "")]
    if data.get("archived"):
        bits.append("ARCHIVED")
    snippet = " · ".join(bit for bit in bits if bit)
    description = _clean(data.get("description"))
    row = _hit("repo", "github_repo", 1, str(data.get("full_name") or f"{owner}/{repo}"),
               str(data.get("html_url") or f"https://github.com/{owner}/{repo}"),
               snippet + (f" · {description}" if description else ""), published)
    return ([row] if row else []), "github_repo", ("ok" if row else "empty"), ""


#: 每个 kind 的后端列表，第一个是 `search_one` 直接问的那一个。`academic` 有
#: 两个后端（arXiv + Crossref），`package` 有四个 registry —— 其余 kind 是一对一。
_KIND_ADAPTERS: dict[str, tuple[tuple[str, "Adapter"], ...]] = {
    "news": (("gdelt", _search_gdelt),),
    "academic": (("arxiv", _search_arxiv), ("crossref", _search_crossref)),
    "qa": (("stackexchange", _search_stackexchange),),
    "code": (("github", _search_github),),
    "community": (("hackernews", _search_community),),
    "package": (("npm", _search_npm), ("pypi", _search_pypi),
                ("crates", _search_crates), ("go", _search_go)),
    "repo": (("github_repo", _search_repo),),
}

#: 主后端名：`sources=['academic']` 在回执里仍然报 `arxiv` 的那一份，也是
#: `search_one` 直接问的那一家。
_BACKENDS = {kind: rows[0][0] for kind, rows in _KIND_ADAPTERS.items()}

#: 同一 kind 里跨后端去重。arXiv 与 Crossref 会交回同一篇论文的两份记录。
_KIND_DEDUPE = {
    "academic": lambda rows: _dedupe_same_title(rows),
}


def _dedupe_same_title(rows: list[VerticalHit]) -> list[VerticalHit]:
    """跨后端的同标题行只留第一条（后端顺序 = 优先级，arXiv 在前）。"""
    seen: set[str] = set()
    out: list[VerticalHit] = []
    for row in rows:
        key = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", row.title.casefold())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(row)
    return out


async def _run_adapter(kind: str, backend: str, adapter: "Adapter",
                       query: str, n: int, page: int, region: str,
                       language: Optional[str], timeout: float,
                       fetch: Optional[Fetcher]
                       ) -> tuple[list[VerticalHit], VerticalReport]:
    started = monotonic()
    try:
        hits, backend, status, error = await adapter(
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


async def search_one(kind: str, query: str, max_results: int, *,
                     page: int = 0, region: str = "us-en",
                     language: Optional[str] = None,
                     timeout: float = VERTICAL_TIMEOUT,
                     fetch: Optional[Fetcher] = None
                     ) -> tuple[list[VerticalHit], VerticalReport]:
    """Ask one kind's primary backend once.

    Kinds with several backends (``academic`` = arXiv + Crossref, ``package`` =
    npm/PyPI/crates/Go) ask all of them through :func:`search_many`; this entry
    keeps the single-backend contract its callers were written against.

    Args:
        kind: One of :data:`VERTICAL_KINDS`.
        query: The search text (or exact package/repo name), handed to the backend as-is.
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
    backend, adapter = _KIND_ADAPTERS[kind][0]
    n = max(1, min(max_results, MAX_PER_SOURCE))
    return await _run_adapter(kind, backend, adapter, query, n, page, region,
                              language, timeout, fetch)


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
    """Ask every backend of every requested kind concurrently.

    Returns:
        ``(hits, reports)`` — **one report per backend** (``academic`` has two),
        in kind order and within a kind in backend order. Hit rows are interleaved
        round-robin by backend, so their order is not a ranking signal. A backend
        that raises contributes an error report and does not cancel the others;
        same-titled rows from two backends of one kind are deduped to the first.
    """
    plan = [(kind, backend, adapter)
            for kind in kinds
            for backend, adapter in _KIND_ADAPTERS[kind]]
    n = max(1, min(max_results, MAX_PER_SOURCE))
    tasks = [
        asyncio.create_task(_run_adapter(
            kind, backend, adapter, query, n, page, region, language,
            timeout, fetch))
        for kind, backend, adapter in plan
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    hits: list[VerticalHit] = []
    reports: list[VerticalReport] = []
    by_kind: dict[str, list[VerticalHit]] = {}
    for (kind, backend, _adapter), result in zip(plan, results):
        if isinstance(result, BaseException):
            _detail = str(result).strip()
            reports.append(VerticalReport(
                source=kind, backend=backend, status="error",
                error=(f"{type(result).__name__}: {_detail[:160]}" if _detail
                       else type(result).__name__)))
            continue
        rows, report = result
        by_kind.setdefault(kind, []).extend(rows)
        reports.append(report)
    for kind in kinds:
        rows = by_kind.get(kind, [])
        dedupe = _KIND_DEDUPE.get(kind)
        if dedupe is not None:
            rows = dedupe(rows)
        hits.extend(rows)
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
