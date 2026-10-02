"""Dhole search engine layer (v7.5: vendored ddgs metasearch backbone).

No API key, no account, no third-party service at runtime. The actual multi-
backend scraping + parsing + rotation lives in `search_metasearch.py` (vendored
+ stripped from ddgs, MIT, attributed in NOTICE.ddgs.txt). This module is the
thin dhole-side adapter: maps dhole's web_search params (engines, freshness,
site, region, page) onto the metasearch, maps results back to RawResult with
cross-backend consensus, and builds the per-engine reports.

Backends (all keyless, 8 in the registry, **all 8 in the default pool**): baidu,
bing, sogou, so360, bing_global, yandex, brave, duckduckgo. No engine is opt-in —
the pool is the registry, so `engines=` only narrows it, never widens it past a
name that exists.
17.0 dropped 6 backends and with them two capabilities the reader may miss:
**微信公众号文章的检索**（sogou_weixin 是唯一的公众号索引，Bing/百度都搜不全这个池子）
和**知识库直查**（wikipedia / grokipedia / baidu_baike 的 JSON/词条入口；通用引擎仍能搜到
这些站点，只是不再把词条当成结构化答案）。yahoo/mwmbl 是覆盖面窄的补充索引。
Engines run in PARALLEL; one that CAPTCHAs / rate-limits /
has no topic-match just yields nothing and the others carry. Search is 100%
HTTP (no browser) - the single Patchright browser stays for web_read only.

DHOLE_SEARCH_PROXY (http/https/socks5) is the one egress a caller can name for
per-IP throttling - the one thing no scraper can escape from one IP.
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
# web_search uses this via the metasearch.
# 组合：国内 4（baidu/bing/sogou/so360）+ 国际 4（bing_global/yandex/brave/duckduckgo），
# 8 家全部入池 —— 17.0 之后没有 opt-in 引擎，注册表就是默认池。
# baidu 排首位：国内直连、独立索引（百度自家索引，非 bing/google 代理），实测无反爬。
# bing 走 cn.bing.com，同样无需 VPN；sogou/so360 是另外两家国内独立索引（服务端渲染）。
# bing_global 是 www.bing.com 那套国际索引：与 cn 版结果几乎不重合，所以它补的是**覆盖面**
# 而不是家族数（家族按底层索引归，两者同属 bing）。国内直连常需代理，被墙时由引擎冷却
# （连续 3 次连接失败 → 冷却 10 分钟）兜住，不会每轮陪跑。
# 共识家族：**8 引擎 / 6 家族**（baidu、bing+bing_global+duckduckgo、sogou、so360、
# yandex、brave），渲染出来是 "x of 6"。分母是**写死的 6**，不是 len(池子)：一个 URL
# 出现在几家引擎里取决于覆盖面重叠，池子增减时家族数不变，所以分母跟着家族走而不是引擎走。
# NOTE 双份定义：search_metasearch._DEFAULT_BACKENDS 是同一份列表的 backend 名
# 版本。合成一处需要 search_engines 在模块顶层 import metasearch 链（primp/lxml），
# 而这里的惰性导入正是为了避免拖重依赖 —— 所以留两份 + 由
# tests/test_engine_registry.py::test_default_pool_definitions_agree 钉住一致性。
DEFAULT_ENGINES = ("baidu", "bing", "sogou", "so360",
                   "bing_global", "yandex", "brave", "duckduckgo")

# 国内裸网可达的引擎（其余要 VPN/代理）。只用于 `dhole -v` 那行说明与文档措辞 ——
# 网络可达性是环境问题（实测 brave 有时直连也通），所以别把它当抓取策略用。
_CN_DIRECT = frozenset({"baidu", "bing", "so360", "sogou", "yandex"})

# Index family per backend (by the underlying index/provider, for consensus).
# A URL returned by duckduckgo AND bing is ONE family (both Bing's index);
# returned by duckduckgo AND brave is TWO families = a stronger authority signal.
_INDEX_FAMILY = {
    "duckduckgo": "bing", "bing": "bing",
    # bing_global 是 bing 的国际索引：同一家族的第二个入口。家族按**底层索引**归，
    # 不按入口归 —— 与 bing 同源的 URL 同时出现在两边时必须算一个家族。
    "bing_global": "bing",
    "brave": "brave",
    "yandex": "yandex",
    "sogou": "sogou",
    "baidu": "baidu", "so360": "so360",
}

_FRESHNESS_TO_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}

# Which backends actually put a date constraint on the wire. Measured (16.0) by
# diffing each engine's build_payload(timelimit=None) against
# build_payload(timelimit='w'); a test asserts this table against those payloads so
# it cannot drift when an engine gains or loses the parameter. Everything not
# listed accepts `timelimit` and ignores it — which is why the response names who
# was filtered rather than claiming the round was.
_DATE_AWARE_ENGINES = frozenset({
    "bing", "bing_global",     # filters=ex1:"ez{d|w|m|y}"
    "brave",                   # tf=p{d|w|m|y}
    "duckduckgo",              # df={d|w|m|y}
    "bocha",                   # keyed: freshness=oneWeek
    "tavily",                  # keyed: time_range=week
})


def date_support(engines) -> tuple:
    """``(can_be_filtered, cannot_be_filtered)`` for one round's pool."""
    pool = set(engines or ())
    return sorted(pool & _DATE_AWARE_ENGINES), sorted(pool - _DATE_AWARE_ENGINES)


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


def _cooldowns() -> dict:
    """Active engine cooldowns ({backend: seconds left}); no observation -> {}."""
    try:
        from dhole_mcp import search_metasearch
        return search_metasearch.cooldowns()
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
    cooldowns = _cooldowns()
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
            # 被取消 ≠ 被拦。第一轮的测试者正是把这一格读成"引擎被墙"，进而误判
            # dhole 没有走 VPN。写清楚它不是失败，也不涉及网络。
            reports.append(EngineReport(name=name, preempted=True,
                                        error="preempted - other engines met the result "
                                              "quota first, so this one was cancelled "
                                              "mid-flight (not a block, not a failure)",
                                        **common))
        elif st == "blocked":
            reports.append(EngineReport(name=name, blocked=True,
                                        error="blocked/captcha (circuit opened)", **common))
        elif st == "circuit_open":
            left = cooldowns.get(name, 0)
            retry = f"; retried in {int(round(left))}s" if left else ""
            reports.append(EngineReport(name=name, blocked=True,
                                        error="circuit open (recently blocked; skipped" + retry + ")",
                                        **common))
        elif st == "timeout":
            reports.append(EngineReport(name=name, blocked=True, error="timed out", **common))
        elif st.startswith("error") or st.startswith("init_error") or st.startswith("no_key"):
            reports.append(EngineReport(name=name, blocked=True, error=st, **common))
        else:  # "empty" —— 引擎答了但一条可用结果都没解析出来
            # "查了，没有" 与 "根本没看到结果页" 是两件事：状态码是 3xx 时上游没给
            # 正文，而 metasearch 把这两种一起记成 empty（实测 bing_global 直连被
            # www.bing.com 地域跳转挡掉，每轮白等 1-5 秒，读起来却是"这个查询没结果"）。
            # token 保持 "empty" 不动（它同时是 engine_empty 的判据），只把原因写进
            # error —— 分类不许为了换个字符串而把引擎从两个列表里同时抹掉。
            code = y.get("http")
            why = "no results"
            if isinstance(code, int) and code >= 300:
                why = f"no parsable results (HTTP {code}"
                why += ", redirect not followed)" if code < 400 else ")"
            reports.append(EngineReport(name=name, error=why, **common))

    return ranked, reports
