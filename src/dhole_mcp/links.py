"""Outgoing-link extraction + classification for smart_fetch (v8).

Given a page's HTML, return its outgoing links classified by context so an
agent can follow a page's source chain in one step instead of eyeballing
markdown links:

  citations   - same-domain links that are NOT site chrome. Classification is by
                exclusion (see _NAV_TAGS, plus role=navigation/menu/menubar),
                not by a main-content match; no such check exists. These are the
                page's referenced sources (papers, primary documents, related
                reads) - the highest-value links.
  navigation  - links inside <nav>/<header>/<footer>/<aside>/role=navigation.
                Site chrome, rarely useful to follow.
  external    - links to a different domain than the page (split out from the
                above so an agent can see off-site references at a glance).
  primary_source - one best-effort "the actual primary source for this page"
    hint, derived from canonical/JSON-LD metadata or a citation pointing at a
    known primary host (arxiv.org, doi.org, biorxiv.org, github.com, ...).

Robustness: a cheap, forgiving lxml pass. Malformed HTML, missing sections, or
no <a> tags all yield empty lists, never raise. Classification is heuristic
(container walk) - a link in an ambiguous container falls back to "citation"
(main-content bias, since that is the high-value default an agent wants).
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse

from lxml import html as lxml_html

logger = logging.getLogger("dhole-mcp.links")

# Containers that count as site chrome -> links inside are navigation.
_NAV_TAGS = {"nav", "header", "footer", "aside"}
# Hosts that are very often the primary source a secondary article references.
_PRIMARY_HOSTS = (
    "arxiv.org", "doi.org", "biorxiv.org", "medrxiv.org",
    "github.com", "gitlab.com", "opencode.dev",
    "wikipedia.org", "wikimedia.org",
    "nature.com", "science.org", "sciencedirect.com", "springer.com",
    "ieee.org", "acm.org", "plos.org",
)

_MAX_CITATIONS = 30
_MAX_NAV = 20
_MAX_EXTERNAL = 20
# Bounds for the caller-facing `max_links` cap (smart_fetch). Same numbers the
# tool description quotes; anything outside is clamped here, at the point where
# the cap is actually applied, so the documented range cannot drift from it.
MAX_LINKS_FLOOR = 1
MAX_LINKS_CEILING = 100


def _norm_host(u: str) -> str:
    """Hostname for host comparisons: lowercase, userinfo/port stripped via
    urlparse().hostname, cosmetic leading ``www.`` removed. Uses startswith
    (NOT lstrip — lstrip strips a CHAR SET, so 'wikipedia.org' would lose its
    leading 'w' and 'web.example.com' would become 'eb.example.com')."""
    try:
        host = (urlparse(u).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _clean_text(s: str) -> str:
    return " ".join((s or "").split())


def _caps(max_links: int | None) -> tuple[int, int, int]:
    """(citations, navigation, external) caps for this call.

    ``None`` = the module defaults (30/20/20). An explicit ``max_links`` sets
    all three, clamped to the documented 1-100 range. The cap is per list: an
    agent that asked for 5 links does not want 90 of them because the page
    happened to have three categories.
    """
    if max_links is None:
        return _MAX_CITATIONS, _MAX_NAV, _MAX_EXTERNAL
    try:
        cap = int(max_links)
    except (TypeError, ValueError):
        return _MAX_CITATIONS, _MAX_NAV, _MAX_EXTERNAL
    if cap <= 0:
        return _MAX_CITATIONS, _MAX_NAV, _MAX_EXTERNAL
    cap = max(MAX_LINKS_FLOOR, min(cap, MAX_LINKS_CEILING))
    return cap, cap, cap


def extract_links(html_text: str, page_url: str, metadata: dict[str, Any] | None = None,
                  max_links: int | None = None) -> dict[str, Any]:
    """Classify a page's outgoing links.

    Returns {citations, navigation, external, primary_source, total_found,
    is_truncated}. Each list item is {url, text}. ``total_found`` counts every
    distinct http(s) link seen on the page (before the caps), and
    ``is_truncated`` is True when the caps dropped at least one of them - the
    agent can tell "this page links to 3 things" from "this page links to 300
    things, here are 30", and ask for a different slice. Never raises; on any
    error returns empty lists with zero totals.
    """
    out: dict[str, Any] = {
        "citations": [], "navigation": [], "external": [],
        "primary_source": "", "total_found": 0, "is_truncated": False,
    }
    if not html_text or not page_url:
        return out
    cap_cit, cap_nav, cap_ext = _caps(max_links)
    try:
        tree = lxml_html.fromstring(html_text)
    except Exception:
        # lxml refuses truly broken markup sometimes; fall back to no links.
        return out
    if tree is None:
        return out

    page_host = _norm_host(page_url)
    seen: set[str] = set()
    citations: list[dict[str, str]] = []
    navigation: list[dict[str, str]] = []
    external: list[dict[str, str]] = []
    content_externals: list[dict[str, str]] = []  # off-domain links in main-content area (real references) - for primary_source
    total_found = 0

    try:
        anchors = tree.xpath('//a[@href]')
    except Exception:
        anchors = []
    for a in anchors:
        try:
            href = (a.get("href") or "").strip()
        except Exception:
            continue
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            continue
        try:
            absu = urljoin(page_url, href)
        except Exception:
            continue
        parsed = urlparse(absu)
        if parsed.scheme not in ("http", "https"):
            continue
        host = _norm_host(absu)
        if not host:
            continue
        key = absu.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        total_found += 1
        try:
            text = _clean_text(a.text_content() or "")
        except Exception:
            text = ""
        if len(text) > 160:
            text = text[:157] + "..."
        entry = {"url": key, "text": text}

        # Container classification: is this anchor inside site chrome?
        try:
            in_nav = next(
                (True for anc in a.iterancestors()
                 if (anc.tag if isinstance(anc.tag, str) else "") in _NAV_TAGS
                 or (anc.get("role") or "") in ("navigation", "menu", "menubar")),
                False,
            )
        except Exception:
            in_nav = False

        is_external = host != page_host
        if is_external:
            if len(external) < cap_ext:
                external.append(entry)
            if not in_nav:
                content_externals.append(entry)  # off-domain reference in main content
            continue
        # Same-domain: nav chrome vs main-content citation.
        if in_nav:
            if len(navigation) < cap_nav:
                navigation.append(entry)
        else:
            if len(citations) < cap_cit:
                citations.append(entry)

    out["citations"] = citations
    out["navigation"] = navigation
    out["external"] = external
    out["primary_source"] = _primary_source(page_url, metadata or {}, content_externals)
    # Transparency (G1): the caps used to be invisible, so a page with 300
    # in-content links looked exactly like a page with 30.
    out["total_found"] = total_found
    out["is_truncated"] = total_found > (len(citations) + len(navigation) + len(external))
    return out


def _primary_source(page_url: str, metadata: dict[str, Any],
                    content_externals: list[dict[str, str]]
                    ) -> str:
    """Best-effort single primary-source URL, or "".

    Priority: a canonical/JSON-LD URL on a different host than the page (the
    publisher's authoritative location); else the first OFF-DOMAIN link that sits
    in the page's main-content area (a real in-content reference, not site
    chrome) and points at a known primary host (arxiv/doi/github/...). Same-
    domain links are never a primary source (they're the site itself).
    """
    page_host = _norm_host(page_url)
    # 1) canonical / JSON-LD @id / og:url on a different host.
    for key in ("canonical", "og:url", "url"):
        val = metadata.get(key)
        if isinstance(val, str) and val.startswith("http"):
            if _norm_host(val) and _norm_host(val) != page_host:
                return val
    # 2) an in-content off-domain reference on a known primary host.
    for e in content_externals:
        host = _norm_host(e["url"])
        if any(host == p or host.endswith("." + p) for p in _PRIMARY_HOSTS):
            return e["url"]
    return ""
