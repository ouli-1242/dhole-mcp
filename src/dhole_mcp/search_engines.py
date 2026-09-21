"""Dhole search engine layer (v7.5: vendored ddgs metasearch backbone).

No API key, no account, no third-party service at runtime. The actual multi-
backend scraping + parsing + rotation lives in `search_metasearch.py` (vendored
+ stripped from ddgs, MIT, attributed in NOTICE.ddgs.txt). This module is the
thin dhole-side adapter: maps dhole's smart_search params (engines, freshness,
site, region, page) onto the metasearch, maps results back to RawResult with
cross-backend consensus, and builds the per-engine reports.

Backends (all keyless, 8 in the registry): bing, duckduckgo, brave, yahoo,
yandex, sogou_weixin, wikipedia, grokipedia. The default pool is DEFAULT_ENGINES
below (5; bing first because it is reachable from CN without a VPN — Bing was
re-enabled in 14.x, this paragraph used to claim it was disabled). wikipedia and
grokipedia are JSON APIs and opt-in only. Engines run in PARALLEL; one that
CAPTCHAs / rate-limits / has no topic-match just yields nothing and the others
carry. Search is 100% HTTP (no browser) - the single Patchright browser stays
for smart_fetch only.

DHOLE_SEARCH_PROXY (http/https/socks5) is the power-user rotating-proxy escape
hatch for per-IP throttling - the one thing no scraper can escape from one IP.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_metasearch = None


def _get_metasearch():
    """Load the heavy scraper backend only for live searches.

    Importing search_metasearch pulls in primp/httpx/lxml/fake_useragent. Keep
    that cost off cached searches, validation failures, and server startup.
    Tests may monkeypatch _metasearch directly; this helper respects that.
    """
    global _metasearch
    if _metasearch is None:
        from dhole_mcp.search_metasearch import metasearch as loaded
        _metasearch = loaded
    return _metasearch


# Public default engine pool (order = rough preference). `engines=None` in
# smart_search uses this via the metasearch.
# bing 排首位：国内网无需 VPN 即可用（cn.bing.com），其余受网络环境影响。
# NOTE 双份定义：search_metasearch._DEFAULT_BACKENDS 是同一份列表的 backend 名
# 版本。合成一处需要 search_engines 在模块顶层 import metasearch 链（primp/lxml），
# 而这里的惰性导入正是为了避免拖重依赖 —— 所以留两份 + 由
# tests/test_engine_registry.py::test_default_pool_definitions_agree 钉住一致性。
DEFAULT_ENGINES = ("bing", "duckduckgo", "brave", "yahoo", "yandex")

# Index family per backend (by the underlying index/provider, for consensus).
# A URL returned by duckduckgo AND yahoo is ONE family (both Bing's index);
# returned by duckduckgo AND brave is TWO families = a stronger authority signal.
_INDEX_FAMILY = {
    "duckduckgo": "bing", "yahoo": "bing", "bing": "bing",
    "brave": "brave", "grokipedia": "grokipedia", "wikipedia": "wikipedia",
    "yandex": "yandex",
}

_FRESHNESS_TO_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}


@dataclass
class RawResult:
    title: str
    url: str
    snippet: str
    source: str              # backend that won the dedup (first to return the URL)
    position: int = 0        # 1-indexed within the merged order
    consensus: int = 1       # distinct independent index-families that returned this URL
    sources: tuple = ()      # all backends that returned this URL (set by multi_search)


@dataclass
class EngineReport:
    name: str
    ok: bool = False        # parsed >=1 result
    blocked: bool = False   # rate-limited / CAPTCHA'd / refused / timed out / errored
    preempted: bool = False # cancelled because enough backends delivered (NOT blocked)
    error: str = ""
    # 上游 metasearch 的原始状态 token（ok/empty/blocked/circuit_open/timeout/
    # error:*/init_error:*/no_key:*/preempted）。三个布尔把它有损地折叠过：
    # "empty"（引擎答了、解析出 0 条）此前既不进 ok 也不进 blocked，于是在
    # engines_used / engine_blocked 两个列表里同时消失 —— 解析器坏了的形态正是
    # 这样。留着原 token 让下游能把它单独报出来，而不必再发明第四个布尔。
    status: str = ""
    # 该引擎最近一轮的解析产出（来自 metasearch 的产出统计）。gate 需要它们才能把
    # "这个查询真没结果"和"我们的解析器跟不上页面了"分开 —— 前者改写查询有用，
    # 后者再打一轮只是给同一个坏掉的解析器重复加压。-1 = 没观测。
    item_nodes: int = -1
    usable: int = -1
    yield_verdict: str = ""


def _engine_yield() -> dict:
    """metasearch 层的每引擎产出快照。惰性取，拿不到就当作无观测。

    注意 `_metasearch` 缓存的是**函数**不是模块（`from … import metasearch`），
    在它身上找 engine_health 只会静默拿到空字典 —— 那样 gate 就永远看不到产出，
    且因为异常被吞掉，看起来"什么都没坏"。所以这里直接 import 模块。
    """
    try:
        from dhole_mcp import search_metasearch
        return search_metasearch.engine_health()
    except Exception:
        return {}


def _normalize_domain(value: str) -> str:
    """Return a comparable hostname without a cosmetic leading ``www.``."""
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value if "://" in value or value.startswith("//") else f"//{value}")
        host = parsed.hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_domain_or_subdomain(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith(f".{domain}"))


def _passes_site_filter(url: str, site: Optional[str], exclude_sites: Optional[list[str]]) -> bool:
    try:
        host = _normalize_domain(urlparse(url).hostname or "")
    except ValueError:
        return False
    if site and not _is_domain_or_subdomain(host, _normalize_domain(site)):
        return False
    for ex in exclude_sites or []:
        if _is_domain_or_subdomain(host, _normalize_domain(ex)):
            return False
    return True


async def fetch_source_for_similar(url: str, *, timeout: int = 10, max_chars: int = 4000
                                    ) -> tuple[str, str]:
    """Fetch a URL for find_similar: returns (title, body_text). Uses a one-off
    impersonated HTTP fetch (primp) - a single arbitrary page, not a repeated
    engine hit, so the metasearch's backend rotation does not apply."""
    try:
        from dhole_mcp.fetcher import HTTPSession
        from dhole_mcp.search_metasearch import _PROXY as _p
        from bs4 import BeautifulSoup
        # rotation pool kept here (not imported from the removed SERL module)
        _pool = ["chrome", "safari", "firefox", "edge"]
        async with HTTPSession(impersonate=_pool, proxy=_p,
                               stealthy_headers=True, retries=1) as sess:
            resp = await sess.get(url, timeout=timeout)
            text = (getattr(resp, "body", None) or b"").decode(
                getattr(resp, "encoding", None) or "utf-8", errors="replace")
    except Exception:
        return "", ""
    if not text:
        return "", ""
    # light blocked-page heuristic
    low = text[:4000].lower()
    if any(m in low for m in ("access denied", "are you a robot", "captcha", "403 forbidden")):
        return "", ""
    title = ""
    try:
        soup = BeautifulSoup(text[:60000], "lxml")
        t = soup.find("title")
        if t:
            title = t.get_text(" ", strip=True)
    except Exception:
        pass
    try:
        import trafilatura
        body = (trafilatura.extract(text[:60000], include_comments=False,
                                    include_tables=False) or "")
    except Exception:
        body = ""
    return title, body[:max_chars]


async def multi_search(
    query: str,
    max_results: int = 10,
    *,
    engines: Optional[list[str]] = None,
    site: Optional[str] = None,
    exclude_sites: Optional[list[str]] = None,
    region: str = "us-en",
    freshness: Optional[str] = None,
    page: int = 0,
    server=None,  # accepted for signature compat; search is 100% HTTP (no browser)
    query_map: Optional[dict[str, str]] = None,
) -> tuple[list[RawResult], list[EngineReport]]:
    """Run the keyless metasearch backends in parallel; return (ranked, reports).

    `engines` selects backends (None = the full default pool). `freshness` maps
    to the engines' time filter. `site` / `exclude_sites` are applied both as a
    query prefix (so backends that honor site: filter upstream) and on the final
    URL (a safety net for backends that do not). `page` is 0-indexed (dhole API)
    -> 1-indexed for the backends. `server` is unused (kept for call-site compat;
    search never touches the browser).
    """
    # Build the query with site:/-site: prefixes (best-effort upstream filter).
    def _apply_site(q: str) -> str:
        if site:
            q = f"site:{site} {q}"
        for ex in exclude_sites or []:
            q = f"-site:{ex} {q}"
        return q

    q = _apply_site(query)
    # Apply site: filters to each per-engine query in the query_map (fan-out).
    if query_map:
        query_map = {eng: _apply_site(qq) for eng, qq in query_map.items()}
    timelimit = _FRESHNESS_TO_TIMELIMIT.get(freshness) if freshness else None
    backend_page = page + 1  # dhole 0-indexed -> backends 1-indexed

    # Map dhole engine names -> metasearch backends (it handles 'auto'/None/legacy).
    mapped = list(engines) if engines else None

    metasearch = _get_metasearch()
    results_dicts, status = await metasearch(
        q, max_results, region=region, timelimit=timelimit,
        page=backend_page, engines=mapped, query_map=query_map,
    )

    # Map to RawResult with cross-backend consensus + apply the final site filter.
    ranked: list[RawResult] = []
    for i, d in enumerate(results_dicts, start=1):
        url = d.get("href", "")
        if not _passes_site_filter(url, site, exclude_sites):
            continue
        backends = d.get("backends") or [d.get("backend", "")]
        families = {_INDEX_FAMILY.get(b, b) for b in backends}
        ranked.append(RawResult(
            title=d.get("title", ""),
            url=url,
            snippet=d.get("body", ""),
            source=d.get("backend", backends[0] if backends else ""),
            position=i,
            consensus=len(families),
            sources=tuple(backends),
        ))

    # Per-backend reports from the metasearch status. 每个分支都带上原始 token：
    # 布尔是有损折叠，empty 就是被折掉的那一格。
    yield_rows = _engine_yield()
    reports: list[EngineReport] = []
    for name, st in status.items():
        y = yield_rows.get(name, {}) if isinstance(yield_rows, dict) else {}

        def _y(key: str, default: int) -> int:
            # 不能用 `y.get(k) or default`：0 条容器 / 0 条可用正是这里的信号本身，
            # `or` 会把它变成"没观测"，判据就永远看不到漂移。
            try:
                return int(y[key])  # type: ignore[literal-required]
            except (KeyError, TypeError, ValueError):
                return default

        common = {
            "status": st,
            "item_nodes": _y("last_nodes", -1),
            "usable": _y("last", -1),
            "yield_verdict": str(y.get("verdict", "") or ""),
        }
        if st == "ok":
            reports.append(EngineReport(name=name, ok=True, **common))
        elif st == "preempted":
            reports.append(EngineReport(name=name, preempted=True,
                                        error="preempted (enough backends delivered)", **common))
        elif st == "blocked":
            reports.append(EngineReport(name=name, blocked=True,
                                        error="blocked/captcha (circuit opened)", **common))
        elif st == "circuit_open":
            reports.append(EngineReport(name=name, blocked=True,
                                        error="circuit open (recently blocked; skipped)", **common))
        elif st == "timeout":
            reports.append(EngineReport(name=name, blocked=True, error="timed out", **common))
        elif st.startswith("error") or st.startswith("init_error") or st.startswith("no_key"):
            reports.append(EngineReport(name=name, blocked=True, error=st, **common))
        else:  # "empty" —— 引擎答了但一条可用结果都没解析出来
            reports.append(EngineReport(name=name, error="no results", **common))

    return ranked, reports
