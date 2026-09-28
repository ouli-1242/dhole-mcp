"""robots.txt compliance for every fetch path (v16.0, gap G3).

Until v16.0 dhole fetched any URL an agent asked for without looking at the
site's robots.txt at all (README said so out loud). This module is the checker:
one per-origin, TTL'd, single-flight cache of parsed rules shared by every fetch
path, consulted by ``smart_fetch`` before any tier runs and by ``smart_crawl``
before a discovered URL is queued.

Policy (deliberately narrow, and stated here because it is a behaviour change):

* ``200`` with a body  -> the rules are parsed and enforced for ``USER_AGENT``.
* ``401``/``403``       -> treated as "no access": the host refuses to serve its
  rules to this client, which RFC 9309 reads as a full disallow. Conservative.
* any other ``4xx``     -> no robots.txt on this host = no restrictions.
* ``5xx`` / ``429`` / network error / timeout -> FAIL OPEN (allowed) with a
  short TTL. dhole is an on-demand fetcher for a single page a human asked for,
  not a crawler; refusing that page because /robots.txt was momentarily
  erroring would be reported to the agent as a site failure and blamed on the
  wrong thing. The short TTL means the next call re-reads.
* a non-http(s) URL, or ``ignore_robots`` / ``DHOLE_IGNORE_ROBOTS`` -> allowed.

The matched UA token is ``USER_AGENT`` ("dhole-mcp"): rules written for ``*``
apply, and a site that blocks a named bot token blocks that token. The HTTP
request that FETCHES robots.txt identifies itself with the same token, so an
operator reading their logs can see who asked.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Callable, NamedTuple, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

logger = logging.getLogger("dhole-mcp.robots")

# Set to 1/true to skip robots.txt checking entirely (the documented escape
# hatch for a caller who has its own agreement with the site it fetches).
ENV_IGNORE_ROBOTS = "DHOLE_IGNORE_ROBOTS"
# The token matched against User-agent groups, and sent as our HTTP UA.
USER_AGENT = "dhole-mcp"
ROBOTS_PATH = "/robots.txt"
# A host's rules are re-read at most once per hour...
TTL_S = 3600.0
# ...but a failure (5xx / timeout) is retried after a minute, so a transient
# error cannot latch "no rules" onto a host for a whole hour.
UNAVAILABLE_TTL_S = 60.0
# Cap on the fetched rules body: real robots.txt files are kilobytes, and a
# half-gigabyte "robots.txt" must not become a memory problem.
MAX_BYTES = 512 * 1024
FETCH_TIMEOUT_S = 8.0
# Ceiling on an obeyed ``Crawl-delay`` (G27). The number is a request about load,
# not a contract about waiting: a site asking for 10 minutes between requests
# would otherwise turn a crawl into a hang that no caller can distinguish from a
# dead server. Clamped, and the crawl summary says which value it used.
MAX_CRAWL_DELAY_S = 60.0

# Reasons returned in Verdict.reason. "allowed" is a real answer from the host's
# rules; "unavailable" means no usable rules (fail-open).
ALLOWED = "allowed"
DISALLOWED = "disallowed"
UNAVAILABLE = "unavailable"
DISABLED = "disabled"


class Verdict(NamedTuple):
    """Outcome of one robots.txt check.

    ``reason`` is ``allowed`` | ``disallowed`` | ``unavailable`` | ``disabled``;
    ``robots_url`` is the file that was consulted (empty when unknown).
    """

    allowed: bool
    reason: str
    robots_url: str = ""


def env_disabled() -> bool:
    """True when ``DHOLE_IGNORE_ROBOTS`` disables compliance process-wide."""
    raw = (os.environ.get(ENV_IGNORE_ROBOTS) or "").strip().lower()
    return raw not in ("", "0", "false", "no", "off", "none", "null")


def robots_url_for(url: str) -> str:
    """``https://host/robots.txt`` for a page URL, or "" when not http(s)."""
    try:
        parts = urlparse(url or "")
    except Exception:
        return ""
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}{ROBOTS_PATH}"


def _origin(url: str) -> str:
    """Cache key: scheme://netloc, lowercased. Rules are per-origin."""
    try:
        parts = urlparse(url or "")
    except Exception:
        return ""
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _parse(text: str, robots_url: str) -> RobotFileParser:
    """Parse a robots.txt body. Never raises: an unparseable file means
    "no rules we can enforce", which is the fail-open shape every other
    failure path here takes."""
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
    except Exception:
        pass
    try:
        rp.parse((text or "").splitlines())
        return rp
    except Exception:
        return _no_rules(robots_url)


def _no_rules(robots_url: str) -> RobotFileParser:
    rp = RobotFileParser()
    try:
        rp.set_url(robots_url)
    except Exception:
        pass
    return rp


def _disallow_all(robots_url: str) -> RobotFileParser:
    """A parser that refuses everything (used for 401/403 on robots.txt)."""
    return _parse("User-agent: *\nDisallow: /\n", robots_url)


def _ua_header() -> str:
    try:
        from dhole_mcp import __version__ as _v
    except Exception:
        _v = "0"
    return f"{USER_AGENT}/{_v} (+https://github.com/ouli-1242/dhole-mcp)"


def _blocking_fetch(robots_url: str, proxy: Optional[str], timeout_s: float
                    ) -> Optional[tuple[int, bytes]]:
    """One blocking GET of robots.txt: ``(status, body)`` or None on no answer.

    Mirrors the sitemap fetcher's shape (primp first, stdlib as the fallback for
    hosts that reject primp's fingerprint) so dhole's two side-channel fetches
    behave the same way.
    """
    try:
        import primp  # type: ignore
    except Exception:
        primp = None  # type: ignore
    if primp is not None:
        try:
            kwargs = {
                "timeout": max(3, int(timeout_s)),
                "impersonate": "random", "impersonate_os": "random", "verify": True,
            }
            if proxy:
                kwargs["proxy"] = proxy
            client = primp.Client(**kwargs)
            resp = client.get(robots_url, headers={"User-Agent": _ua_header()})
            return int(resp.status_code), bytes(resp.content or b"")[:MAX_BYTES]
        except Exception:
            pass  # fall through to stdlib
    import urllib.request as _urllib_req
    try:
        handlers = []
        if proxy:
            handlers.append(_urllib_req.ProxyHandler({"http": proxy, "https": proxy}))
        opener = _urllib_req.build_opener(*handlers)
        req = _urllib_req.Request(robots_url, headers={"User-Agent": _ua_header()})
        with opener.open(req, timeout=timeout_s) as resp:  # noqa: S310
            return int(resp.status), resp.read(MAX_BYTES)
    except _urllib_req.HTTPError as e:
        return int(e.code), b""
    except Exception:
        return None


class _Entry(NamedTuple):
    expires: float
    parser: Optional[RobotFileParser]  # None = no rules on this origin (allow all)
    disallow_all: bool = False


class RobotsCache:
    """Per-origin robots.txt rules with a TTL, single-flight and fail-open.

    ``fetch`` (optional) replaces the network call: ``async (url, proxy) ->
    (status, body) | None``. Tests inject a fake; the default runs the blocking
    fetcher in a worker thread.
    """

    def __init__(
        self,
        *,
        ttl: float = TTL_S,
        unavailable_ttl: float = UNAVAILABLE_TTL_S,
        fetch: Optional[Callable[[str, Optional[str]], object]] = None,
    ) -> None:
        self._ttl = ttl
        self._unavailable_ttl = unavailable_ttl
        self._fetch = fetch
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # ─── cache surface ───────────────────────────────────────────────

    def clear(self) -> None:
        self._entries.clear()

    def _fresh(self, origin: str) -> Optional[_Entry]:
        entry = self._entries.get(origin)
        if entry is None:
            return None
        if entry.expires <= time.monotonic():
            self._entries.pop(origin, None)
            return None
        return entry

    def cached(self, url: str, *, user_agent: str = USER_AGENT) -> Optional[bool]:
        """Answer from the cache only: True/False, or None when not yet read.

        ``smart_crawl`` uses this to drop discovered URLs without a network
        call: the seed page's rules are already cached by then, so filtering a
        same-origin candidate is free.
        """
        entry = self._fresh(_origin(url))
        if entry is None:
            return None
        if entry.disallow_all:
            return False
        if entry.parser is None:
            return True
        try:
            return bool(entry.parser.can_fetch(user_agent, url))
        except Exception:
            return None

    def cached_delay(self, url: str, *, user_agent: str = USER_AGENT) -> Optional[float]:
        """``Crawl-delay`` from rules ALREADY in the cache; None = unknown/unset.

        The crawl asks per page. Reading the cache (rather than fetching) is what
        makes that free: the seed page's rules are in there by then, so a
        100-page crawl still costs one robots.txt read.
        """
        entry = self._fresh(_origin(url))
        if entry is None or entry.disallow_all or entry.parser is None:
            return None
        return self._delay_of(entry.parser, user_agent)

    async def crawl_delay(self, url: str, *, proxy: Optional[str] = None,
                          user_agent: str = USER_AGENT) -> Optional[float]:
        """The site's own requested spacing for ``url`` (None = it asked for none).

        Same origin/TTL/single-flight as the allowed() check, so this never costs
        an extra robots.txt read on a path that already did one.
        """
        robots_url = robots_url_for(url)
        if not robots_url:
            return None
        origin = _origin(url)
        entry = self._fresh(origin)
        if entry is None:
            entry = await self._load(origin, robots_url, proxy)
        if entry is None or entry.disallow_all or entry.parser is None:
            return None
        return self._delay_of(entry.parser, user_agent)

    @staticmethod
    def _delay_of(parser: RobotFileParser, user_agent: str) -> Optional[float]:
        """``Crawl-delay`` for our user-agent, clamped to something a call budget
        can survive.

        urllib already prefers our group over ``*`` and returns None when neither
        is set. It also reads only WHOLE seconds (3.14 parses behind an isdigit()
        guard), so a host writing ``Crawl-delay: 0.5`` reads as "asked for
        nothing" — sub-second spacing is the caller's own ``delay`` knob, not
        something a site can request.

        A site asking for hours is not obeyed to the letter: the number is a
        request about load, and an unclamped one turns a crawl into a hang.
        """
        try:
            raw = parser.crawl_delay(user_agent)
        except Exception:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return min(value, MAX_CRAWL_DELAY_S)

    def prime(self, url: str, text: str, *, robots_url: str = "") -> None:
        """Seed rules from a robots.txt body (tests, or a caller that has one)."""
        target = robots_url or robots_url_for(url)
        origin = _origin(target or url)
        if not origin:
            return
        self._entries[origin] = _Entry(time.monotonic() + self._ttl, _parse(text, target))


    # ─── the check ───────────────────────────────────────────────────

    async def allowed(self, url: str, *, proxy: Optional[str] = None,
                      user_agent: str = USER_AGENT) -> Verdict:
        """Is ``url`` fetchable under its origin's robots.txt?"""
        robots_url = robots_url_for(url)
        if not robots_url:
            return Verdict(True, UNAVAILABLE, "")
        origin = _origin(url)
        entry = self._fresh(origin)
        if entry is None:
            entry = await self._load(origin, robots_url, proxy)
        if entry is None:
            return Verdict(True, UNAVAILABLE, robots_url)
        if entry.disallow_all:
            return Verdict(False, DISALLOWED, robots_url)
        if entry.parser is None:
            return Verdict(True, UNAVAILABLE, robots_url)
        try:
            ok = bool(entry.parser.can_fetch(user_agent, url))
        except Exception:
            return Verdict(True, UNAVAILABLE, robots_url)
        return Verdict(ok, ALLOWED if ok else DISALLOWED, robots_url)

    async def _load(self, origin: str, robots_url: str, proxy: Optional[str]
                    ) -> Optional[_Entry]:
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            entry = self._fresh(origin)  # a concurrent task may have filled it
            if entry is not None:
                return entry
            status, text, answered = await self._fetch_rules(robots_url, proxy)
            if not answered:
                # No answer at all: allow, and retry soon instead of caching an
                # allow for the full TTL.
                self._entries[origin] = _Entry(
                    time.monotonic() + self._unavailable_ttl, None)
                return None
            if status in (401, 403):
                # The host refuses to serve the rules to this client. RFC 9309
                # reads that as no access; honouring it is the conservative
                # choice, and it is reported as such.
                entry = _Entry(time.monotonic() + self._ttl,
                               _disallow_all(robots_url))
                self._entries[origin] = entry
                return entry
            if status != 200:
                # 404/410/451 -> there is no robots.txt here, and that will not
                # change within the hour: full TTL. 5xx/429 -> the host is
                # momentarily broken or throttling us, so the answer may differ a
                # minute from now: short TTL, exactly like no answer at all. (The
                # policy has always said so; the code used to cache a 5xx for the
                # full hour, so one bad moment kept a host's rules unknown - and
                # therefore unenforced - for as long as a real rules file.)
                short = status >= 500 or status == 429
                self._entries[origin] = _Entry(
                    time.monotonic() + (self._unavailable_ttl if short else self._ttl),
                    None)
                return None
            entry = _Entry(time.monotonic() + self._ttl, _parse(text, robots_url))
            self._entries[origin] = entry
            return entry


    async def _fetch_rules(self, robots_url: str, proxy: Optional[str]
                           ) -> tuple[int, str, bool]:
        """``(status, text, answered)``; ``answered=False`` = no usable answer.

        The robots URL derives from an already-validated page URL, but it is
        re-validated here: a redirect or a hostile DNS answer must not turn this
        side channel into an SSRF primitive.
        """
        try:
            from dhole_mcp.security import validate_url
            validate_url(robots_url)
        except Exception as e:
            logger.debug("robots.txt target refused for %s: %s", robots_url, e)
            return 0, "", False
        try:
            if self._fetch is not None:
                result = await self._fetch(robots_url, proxy)  # type: ignore[misc]
            else:
                result = await asyncio.to_thread(
                    _blocking_fetch, robots_url, proxy, FETCH_TIMEOUT_S)
        except Exception as e:
            logger.debug("robots.txt fetch failed for %s: %s", robots_url, e)
            return 0, "", False
        if not result:
            return 0, "", False
        status, body = result  # type: ignore[misc]
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            # An unusable status is "no answer", NOT "no rules". Treating it as
            # an answer would let a broken fetcher latch allow-all onto a host
            # for a full TTL.
            return 0, "", False
        try:
            if isinstance(body, str):
                # An injected fetcher may hand back text rather than bytes.
                # bytes(str) raises, and the old except swallowed that into an
                # EMPTY rules body - which parses to "no rules" and silently
                # ALLOWS every path. A compliance check must not fail open on a
                # type mix-up, so coerce instead of dropping the body.
                body = body.encode("utf-8", errors="replace")
            text = bytes(body or b"")[:MAX_BYTES].decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return status_code, text, True


_CACHE: Optional[RobotsCache] = None


def get_robots_cache() -> RobotsCache:
    """Process-wide cache: one robots.txt read per origin per TTL, shared by
    every fetch path so a 100-page crawl reads the file once."""
    global _CACHE
    if _CACHE is None:
        _CACHE = RobotsCache()
    return _CACHE


def reset_robots_cache() -> None:
    """Drop the process-wide cache (tests, and ``cache_clear``)."""
    global _CACHE
    _CACHE = None


async def check(url: str, *, proxy: Optional[str] = None, ignore: bool = False
                ) -> Verdict:
    """One-call check used by the fetch paths.

    ``ignore`` is the per-call escape hatch (``ignore_robots=true``);
    ``DHOLE_IGNORE_ROBOTS`` disables checking for the whole process.
    """
    if ignore or env_disabled():
        return Verdict(True, DISABLED, robots_url_for(url))
    return await get_robots_cache().allowed(url, proxy=proxy)


async def crawl_delay(url: str, *, proxy: Optional[str] = None,
                     ignore: bool = False) -> Optional[float]:
    """The spacing this site asked for (G27); None = it asked for none.

    ``ignore`` is the same opt-out as check(): a caller who said "do not comply
    with this host's robots.txt" should not then be quietly rate-limited by it.
    """
    if ignore or env_disabled():
        return None
    return await get_robots_cache().crawl_delay(url, proxy=proxy)


def cached_crawl_delay(url: str) -> Optional[float]:
    """Read the asked-for spacing from the cache only — no request, ever.

    A crawl asks this per page, so this has to be free; the seed page's rules are
    already in the cache by the time it is asked.
    """
    if env_disabled():
        return None
    return get_robots_cache().cached_delay(url)
