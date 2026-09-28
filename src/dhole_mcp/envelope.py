"""v10 research-grade envelope: page-type, freshness, and source-authority
signals computed for every fetch so an agent gets trust + currency + the next
step without a second call.

All functions use only the standard library and run on already-extracted HTML /
the URL string, keeping the envelope self-contained and low-overhead.

Design principles:
- CONSERVATIVE over RECALL. A wrong ``is_official=True`` or a mislabelled
  ``page_type="list"`` sends the agent on a bad path. Default to "unknown" /
  False when the signal is weak; only assert on strong evidence.
- page_type is split: structural signals (forum/qa/list/docs/article) are
  detected from raw HTML in _translate_response; error-derived signals
  (js_shell/auth_wall) override in _with_agent_hints since they are
  definitive (set by _annotate_quality after extraction).
- freshness prefers the MODIFIED/updated date over the published date — a page
  updated last week is not stale even if first published in 2014.
"""
from __future__ import annotations

import re
from datetime import datetime, date, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

# A page older than this is flagged stale. Flat fallback for sources whose class
# we cannot determine; domain-aware horizons below refine it (G15).
STALE_DAYS = 365
# How long a source of THIS KIND usually stays true. A flat 365 days called a
# 14-month-old news article fresh and a 2001-created wiki article 24 years old -
# both the wrong question. News is measured in weeks; reference material, papers,
# docs, repos and Q&A are maintained for years and are rarely invalidated by age.
NEWS_STALE_DAYS = 30
LONG_LIVED_STALE_DAYS = 730

# ─── Source authority classification ───────────────────────────────

# Known news domains (conservative set). Best-effort; "unknown" is fine for
# anything not listed — source_type is a hint, not a verdict.
_NEWS_DOMAINS = (
    "nytimes.com", "bbc.com", "bbc.co.uk", "reuters.com", "theguardian.com",
    "washingtonpost.com", "bloomberg.com", "apnews.com", "aljazeera.com",
    "cnbc.com", "ft.com", "economist.com", "techcrunch.com", "theverge.com",
    "arstechnica.com", "wired.com", "cnn.com", "npr.org", "politico.com",
    "axios.com", "businessinsider.com", "forbes.com", "theatlantic.com",
    "time.com", "wsj.com", "cnet.com", "zdnet.com", "independent.co.uk",
    "lemonde.fr", "spiegel.de", "elpais.com", "scmp.com",
    "36kr.com", "ithome.com", "huxiu.com", "pingwest.com", "thepaper.cn",
    # NOTE: nature.com / science.org used to live here. They are journals, not
    # news: putting them under a 30-day news horizon would have flagged every
    # paper older than a month as stale. They are _PAPER_DOMAINS now.
)

_QA_DOMAINS = (
    "stackoverflow.com", "stackexchange.com", "serverfault.com",
    "superuser.com", "mathoverflow.com", "askubuntu.com", "quora.com",
    "stackapps.com",
)

_GITHUB_DOMAINS = (
    "github.com", "raw.githubusercontent.com", "gist.github.com",
)

# Encyclopedia / standards / canonical-reference sites: durable pages, and the
# staleness bucket they land in is what this list is for (730 days, not 30).
# "reference" is deliberately the same word search._source_type uses for search
# hits, so a fetched page and a search result describe the same site the same
# way. The ones that RUN a namespace (iana.org / rfc-editor.org / w3.org /
# ietf.org / unicode.org) also get is_official — see
# _REGISTRY_CONTROLLED_DOMAINS, which is checked before this list.
_REFERENCE_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "wikiwand.com", "britannica.com",
    "iana.org", "ietf.org", "rfc-editor.org", "w3.org", "unicode.org",
    "w3schools.com", "geeksforgeeks.org", "tutorialspoint.com", "caniuse.com",
)

# Peer-reviewed / preprint literature. Same reasoning as _REFERENCE_DOMAINS:
# durable, but the name is registrable, so NOT is_official.
_PAPER_DOMAINS = (
    "arxiv.org", "dl.acm.org", "ieeexplore.ieee.org", "openreview.net",
    "semanticscholar.org", "biorxiv.org", "medrxiv.org", "nature.com",
    "science.org", "sciencedirect.com", "link.springer.com", "pnas.org",
    "nejm.org", "pubmed.ncbi.nlm.nih.gov",
)

# Collaborative reference sites that are CONTINUOUSLY EDITED. Their "published"
# date is when the ARTICLE was created, not when its content was last true, so
# inferring staleness from it is a systematic false positive (see
# _is_continuously_edited).
_CONTINUOUSLY_EDITED_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "wikiwand.com", "britannica.com",
    "fandom.com",
)

# Names whose CONTROL, not just the string, sits with one body a third party
# cannot buy their way past: the registry operators and standards bodies
# themselves, and the package registries that decide what a name resolves to.
# These are the reference domains where is_official can say True without
# weakening it — the rule stays "the authority is whoever runs this name", not
# "the page looks authoritative". Encyclopedia sites (wikipedia.org) are NOT
# here: the content is community-edited, and the field is about the source, not
# about whether the page is right.
_REGISTRY_CONTROLLED_DOMAINS = (
    "iana.org", "icann.org", "rfc-editor.org", "ietf.org", "w3.org",
    "unicode.org", "ieee.org",
    "pypi.org", "npmjs.com", "crates.io", "rubygems.org", "pkg.go.dev",
    "metacpan.org", "packagist.org", "nuget.org",
)

# Country-code government/academic forms: "<x>.gov.<cc>" / "<x>.ac.<cc>"
# (gov.br, gov.cn, gov.uk, ac.uk). The label pair is reserved by the ccTLD
# registry, so a third party cannot own it. The 2-letter tail is what makes the
# match safe: "foo.gov.attacker.com" ends in ".com", not ".gov.<cc>".
_CC_GOV_RE = re.compile(r"\.gov\.[a-z]{2}$")
_CC_AC_RE = re.compile(r"\.ac\.[a-z]{2}$")


def classify_source(url: str) -> tuple[str, bool]:
    """Classify a URL's domain into a source_type + is_official flag.

    Returns (source_type, is_official). is_official is True ONLY where the name
    itself cannot be bought by a third party: the registry-controlled namespaces
    (gov, edu, github) and the bodies that run a namespace — IANA/ICANN/RFC
    Editor/IETF/W3C/Unicode and the package registries (see
    _REGISTRY_CONTROLLED_DOMAINS). Subdomain shapes such as "docs.*" /
    "developer.*" are reported as source_type="docs-site" but are NOT official:
    they only say the site named a subdomain "docs". Community-edited reference
    sites (wikipedia.org and friends) are NOT official either — the field is
    about who controls the source, not about whether the page reads as right.
    Everything else is False (conservative).
    """
    if not url:
        return "unknown", False
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "unknown", False
    # strip userinfo@ and :port
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if not host:
        return "unknown", False

    # Government / education / github: the only classes where the NAME ITSELF
    # cannot be bought by a third party (registry-controlled namespaces).
    #
    # NOTE: this used to test ".gov." in host, which any attacker-registrable
    # domain satisfies — "foo.gov.attacker.com" was classified gov/is_official
    # and handed straight to the agent as an authority signal.
    if host == "gov" or host.endswith(".gov") or _CC_GOV_RE.search(host):
        return "gov", True
    if host.endswith(".edu") or host.endswith(".ac.uk") or _CC_AC_RE.search(host):
        return "edu", True
    if host in _GITHUB_DOMAINS or host.endswith(".github.io"):
        return "github", True

    # The bodies that RUN a namespace (IANA/ICANN/RFC Editor/IETF/W3C/Unicode)
    # and the package registries that decide what a name resolves to: same
    # property as gov/edu — nobody else can acquire the name — so these do get
    # is_official=True (G17 asked why iana.org and rfc-editor.org did not).
    if any(host == d or host.endswith("." + d)
           for d in _REGISTRY_CONTROLLED_DOMAINS):
        return "reference", True

    # Vendor docs subdomains (docs.* / developer.*). This is a SHAPE signal, not
    # an authority one: any site can name a subdomain "docs." — including one
    # whose whole purpose is to look authoritative. So it is NOT is_official:
    # the agent must not treat it as the canonical source for a subject.
    if host.startswith("docs.") or host.startswith("developer.") or host.startswith("developers."):
        return "docs-site", False

    # Q&A sites.
    if host in _QA_DOMAINS or host.endswith(".stackexchange.com") or host.endswith(".stackoverflow.com"):
        return "qa", False

    # Forums / community.
    if any(m in host for m in ("forum", "forums", "community", "discourse", "board")):
        return "forum", False
    if host in ("reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com") or host.endswith(".reddit.com"):
        return "forum", False

    # Blogs.
    if host.startswith("blog.") or host in ("medium.com", "wordpress.com", "substack.com") or host.endswith(".substack.com") or host.endswith(".medium.com"):
        return "blog", False

    # Ecommerce.
    if host.startswith("shop.") or host.startswith("store.") or host in ("amazon.com", "ebay.com") or host.endswith(".shop"):
        return "ecommerce", False

    # Reference / standards / encyclopedia, then peer-reviewed literature.
    # Both are durable and authoritative-for-a-subject, and both are registrable
    # names (so is_official stays False) - the point is to stop reporting
    # "unknown" for iana.org / wikipedia.org / rfc-editor.org (G17).
    if any(host == d or host.endswith("." + d) for d in _REFERENCE_DOMAINS):
        return "reference", False
    if any(host == d or host.endswith("." + d) for d in _PAPER_DOMAINS):
        return "paper", False

    # News (known set).
    if any(host == d or host.endswith("." + d) for d in _NEWS_DOMAINS):
        return "news", False

    return "unknown", False


def _host_of(url: str) -> str:
    """Lowercased hostname with userinfo/port stripped, or ""."""
    try:
        host = urlparse(url or "").netloc.lower()
    except Exception:
        return ""
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def _is_continuously_edited(url: str) -> bool:
    """True for collaborative reference sites whose content is always being edited.

    On these, the only date usually available is the ARTICLE CREATION date, which
    says nothing about whether the content is still true. Measured before this
    fix: en.wikipedia.org/wiki/HTTP reported content_age_days=9098 / is_stale=true
    from a 2001 creation date, on a page edited continuously since. The gap report
    asks for exactly this treatment: "或对 wiki 类站点禁用此推断".
    """
    host = _host_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _CONTINUOUSLY_EDITED_DOMAINS)


def _stale_days_for(url: str) -> int:
    """How long a source of THIS KIND usually stays true (G15).

    news -> 30 days; reference / paper / docs / repo(github) / qa -> 730 days;
    everything else keeps the original flat 365. A URL-less caller therefore sees
    exactly the pre-16.0 behaviour.
    """
    if not url:
        return STALE_DAYS
    st, _ = classify_source(url)
    if st == "news":
        return NEWS_STALE_DAYS
    if st in ("reference", "paper", "docs-site", "github", "qa"):
        return LONG_LIVED_STALE_DAYS
    return STALE_DAYS


# ─── Freshness ─────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")


def _parse_date(s: str) -> date | None:
    """Parse a date from a metadata string. Handles ISO (with offset/Z),
    compact YYYYMMDD, and a few human formats. Returns None if unparseable."""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # Compact YYYYMMDD (wayback timestamps, some metadata).
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    # ISO 8601 (fromisoformat in 3.11+ handles offsets and 'Z'). Take the date
    # from the full timestamp; fall back to the first 10 chars.
    for cand in (s, s[:10]):
        try:
            return datetime.fromisoformat(cand.replace("Z", "+00:00")).date()
        except ValueError:
            continue
    # Human formats — try the whole string then a 32-char prefix.
    for fmt in _DATE_FORMATS:
        for cand in (s, s[:32]):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def compute_freshness(metadata: dict[str, Any], fetched_at_iso: str,
                      url: str = "") -> tuple[int | None, bool]:
    """Return (content_age_days, is_stale) from the page's own dates.

    Prefers the modified/updated date over the published date (a page updated
    last week is current even if first published in 2014). Returns (None, False)
    when no date is recoverable, when the date is in the future (bad data), or
    when the only date on a continuously-edited site is its creation date.

    ``url`` is optional and only sharpens the STALENESS verdict (G15): the age
    itself is a fact about the page, but whether that age means "stale" depends
    on how long this kind of source usually stays true - 30 days for news, 730
    for reference/docs/repo/QA, 365 otherwise. Omit it and the flat 365-day
    horizon applies, exactly as before.

    None, not -1: a negative age reads as "two days in the future", which is a
    different claim than "the page published no date". Agents that arithmetic on
    the value were silently comparing against a sentinel.
    """
    if not metadata:
        return None, False
    # Prefer modified > published > created > generic 'date'. Which field the
    # date came from is tracked, because it decides one case below.
    modified_str = (metadata.get("modified_time") or metadata.get("mod_date") or "")
    published_str = (metadata.get("published_time") or metadata.get("creation_date")
                     or metadata.get("date") or "")
    date_str = modified_str or published_str
    content_date = _parse_date(date_str) if date_str else None
    if content_date is None:
        return None, False
    # A wiki's only date is normally its creation date. Reporting "this page is
    # 9098 days old" about a page edited this morning is a wrong answer, and a
    # wrong is_stale sends the agent to re-fetch something that is current. With
    # no modification date there is no currency signal to report, so report none
    # (None = "no date we can stand behind", which is not the same claim as
    # "fresh"). Only applies to the continuously-edited class.
    if not modified_str and url and _is_continuously_edited(url):
        return None, False
    fetched_date = _parse_date(fetched_at_iso) if fetched_at_iso else None
    if fetched_date is None:
        # Fall back to today (UTC) so freshness still works if fetched_at missing.
        fetched_date = datetime.now(timezone.utc).date()
    delta = (fetched_date - content_date).days
    if delta < 0:
        # Future-dated content = bad metadata; can't trust the age signal.
        return None, False
    return delta, delta > _stale_days_for(url)


# ─── Page-type detection ────────────────────────────────────────────

# Forum / Q&A / docs markers in the raw HTML (class/id substring matches).
_FORUM_MARKERS = ("phpbb", "discourse", "class=\"forum", "id=\"forum",
                  "class=\"thread", "class=\"post-body", "class=\"message-body",
                  "data-post-id")
_QA_MARKERS = ("stackoverflow", "stackexchange", "class=\"question",
               "class=\"answer", "data-answerid", "data-questionid")
_DOCS_MARKERS = ("mkdocs", "docusaurus", "readthedocs", "sphinx-document",
                 "algolia-docsearch", "md-nav", "theme-doc", "class=\"rst-content",
                 "wy-nav-side")
_PAYWALL_MARKERS = ("subscribe to continue", "subscribe to read", "this article is for subscribers",
                    "create a free account to continue", "sign in to continue reading",
                    "you've reached your free article limit", "subscriber-only content",
                    "premium content")
# Explicit HTML data attributes are a structural paywall signal. Deliberately do
# not match the bare word "paywall": it can appear in ordinary README links,
# explanatory copy, scripts, or metadata. Parse HTML so attribute names are
# matched exactly and attribute values cannot impersonate names.
_PAYWALL_ATTRIBUTE_NAMES = frozenset({
    "data-paywall",
    "data-content-gate",
    "data-subscription-wall",
})
_FALSE_PAYWALL_ATTRIBUTE_VALUES = frozenset({
    "0", "false", "no", "off", "disabled", "none", "null",
})
_PAYWALL_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template"})
_PAYWALL_METADATA_TAGS = frozenset({"base", "link", "meta"})
_PAYWALL_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
})


class _PaywallEvidenceParser(HTMLParser):
    """Collect visible text and active, exact paywall attributes."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_text: list[str] = []
        self.has_active_attribute = False
        self._ignored_stack: list[str] = []

    def _inspect_attributes(self, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() not in _PAYWALL_ATTRIBUTE_NAMES:
                continue
            normalized = value.strip().lower() if value is not None else None
            if normalized not in _FALSE_PAYWALL_ATTRIBUTE_VALUES:
                self.has_active_attribute = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._ignored_stack:
            if tag in _PAYWALL_IGNORED_TAGS:
                self._ignored_stack.append(tag)
            return
        if tag in _PAYWALL_IGNORED_TAGS:
            self._ignored_stack.append(tag)
            return
        if tag not in _PAYWALL_METADATA_TAGS:
            self._inspect_attributes(attrs)
        if tag in _PAYWALL_BLOCK_TAGS:
            self.visible_text.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if (
            not self._ignored_stack
            and tag not in _PAYWALL_IGNORED_TAGS
            and tag not in _PAYWALL_METADATA_TAGS
        ):
            self._inspect_attributes(attrs)
            if tag in _PAYWALL_BLOCK_TAGS:
                self.visible_text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_stack:
            if tag in self._ignored_stack:
                while self._ignored_stack.pop() != tag:
                    pass
            return
        if tag in _PAYWALL_BLOCK_TAGS:
            self.visible_text.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_stack:
            self.visible_text.append(data)


def _paywall_evidence(html: str) -> tuple[str, bool]:
    """Return visible page text and whether an active paywall attribute exists."""
    parser = _PaywallEvidenceParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed upstream HTML must not break the fetch response.
        return "", False
    return " ".join("".join(parser.visible_text).split()), parser.has_active_attribute


# A redirect via <meta http-equiv=refresh> or a JS location.href assignment.
# The attribute value may be unquoted (http-equiv=REFRESH) — measured on a real
# page, and a quoted-only pattern classified it as an ordinary page.
_META_REFRESH_RE = re.compile(
    r'<meta\b[^>]*?http-equiv\s*=\s*["\']?refresh["\']?[^>]*?content\s*=\s*["\'][^"\']*url=',
    re.IGNORECASE,
)
_JS_REDIRECT_RE = re.compile(
    r'(?:location\.href\s*=|location\.replace|window\.location\s*=)',
    re.IGNORECASE,
)
# Same-domain content links (rough): <a href="/..."> or <a href="https://host...">
_ANCHOR_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\']', re.IGNORECASE)
# Blocks to strip before counting links (nav/header/footer/aside/script/style).
_STRIP_BLOCK_RE = re.compile(
    r'<(nav|header|footer|aside|script|style|noscript)\b[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)
_ARTICLE_TAG_RE = re.compile(r'<article\b', re.IGNORECASE)

# Below this share of the content area's text being link labels, the page is
# treated as prose that happens to link a lot, not as a link index.
# 0.10 sits in the measured gap between the two: real index pages 0.16-0.43
# (quotes.toscrape.com listing 0.26, a Python docs index 0.16), real article /
# reference pages 0.01-0.05 (RFC 9110 0.01, GNU's free-software definition 0.05,
# a Gentoo wiki article 0.03). Saved under tests/page_fixtures with their numbers
# in the regression test.
LIST_LABEL_SHARE_MIN = 0.10

# A page whose content area carries a long stretch of text OUTSIDE any link label
# is prose with links in it, not an index of links — whatever its label share
# says. This exists because the share alone misfires on exactly the kind of page
# agents fetch most: a Wikipedia article runs 0.12 (its links are multi-word
# article titles, and the References/Listed-elsewhere blocks are almost pure
# link text), which is above the 0.10 line the five-page sample put there.
# Measured longest non-label stretch: HN front page 79, quotes.toscrape.com
# listing 148, an FSF category index 279 — versus 705 for the first 200KB of
# en.wikipedia.org/wiki/Hypertext_Transfer_Protocol (the whole article runs
# higher), 807 for the RFC 9110 spec page, 4k+ for GNU's free-software
# definition. 400 leaves a full unit of margin on the index side.
LIST_PROSE_RUN_MAX = 400

# The same run, read the other way, is what lets a long-form page be called an
# ARTICLE instead of "unknown": <article> is a strong signal but far from the
# only shape prose comes in (Wikipedia's skin, standards pages and most
# self-hosted docs put the body in bare <div>s, and all of those used to return
# unclassified). 600 sits above every index page measured (79 / 148 / 227 / 279)
# and below the article pages (705 / 756 / 807), and the text floor keeps a
# one-paragraph stub out of it.
ARTICLE_PROSE_RUN_MIN = 600
ARTICLE_VISIBLE_TEXT_MIN = 4000


def _is_content_href(href: str, host: str) -> bool:
    """Whether this ``<a href>`` is a same-domain content link (not chrome-by-kind).

    Excludes in-page anchors (#), ``mailto:`` / ``tel:`` / ``javascript:``, and
    off-domain links. Kept as one predicate because the link COUNT and the
    link-LABEL text must describe the same set of links; if they disagreed, the
    share below would be measured over links the count never counted.
    """
    href = (href or "").strip()
    if not href:
        return False
    low = href.lower()
    if low.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    # Relative or same-host = content link candidate.
    if href.startswith("/") or href.startswith("?"):
        return True
    try:
        h = urlparse(href).netloc.lower()
    except Exception:
        return False
    return bool(h) and (h == host or h.endswith("." + host))


class _ListShapeParser(HTMLParser):
    """Measure the content area's shape: link-label share and prose runs.

    Counts text inside anchors whose href passes _is_content_href — the same
    predicate _count_content_links uses, so the share is measured over exactly
    the links that get counted.

    Alongside the share it tracks the longest contiguous stretch of visible text
    that is NOT one of those labels (``longest_run``): an index page's items are
    label + a short piece of metadata, so its runs stay short no matter how many
    links it carries, while an article's paragraphs run long. Reset at block tags
    so two adjacent short items cannot merge into one long run.
    """

    def __init__(self, host: str) -> None:
        super().__init__(convert_charrefs=True)
        self.host = host
        self.visible_chars = 0
        self.anchor_chars = 0
        self.longest_run = 0
        self._skip = 0
        self._anchor_depth = 0
        self._counted_anchor = False
        self._run = 0

    def _end_run(self) -> None:
        self.longest_run = max(self.longest_run, self._run)
        self._run = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _PAYWALL_IGNORED_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _PAYWALL_BLOCK_TAGS:
            self._end_run()
        if tag == "a":
            self._anchor_depth += 1
            self._counted_anchor = _is_content_href(dict(attrs).get("href") or "", self.host)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _PAYWALL_IGNORED_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _PAYWALL_BLOCK_TAGS:
            self._end_run()
        if tag == "a" and self._anchor_depth:
            self._anchor_depth -= 1
            self._counted_anchor = False
            self._end_run()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        n = len(" ".join(data.split()))
        if not n:
            return
        self.visible_chars += n
        if self._anchor_depth and self._counted_anchor:
            self.anchor_chars += n
            self._end_run()
        else:
            self._run += n


def _list_shape(html: str, host: str) -> tuple[float, int]:
    """(link-label share, longest non-label prose run) of the content area.

    See LIST_LABEL_SHARE_MIN and LIST_PROSE_RUN_MAX for what each one separates.
    """
    parser = _ListShapeParser(host)
    try:
        parser.feed(_STRIP_BLOCK_RE.sub("", html))
        parser.close()
    except Exception:
        return 0.0, 0
    parser._end_run()
    return parser.anchor_chars / max(parser.visible_chars, 1), parser.longest_run


def _label_share(html: str, host: str) -> float:
    """Share of the content area's visible text that IS a counted link's label.

    This is what separates "the page is a list of links" from "the page links a
    lot": an index page's prose is its link labels (0.16-0.43 on real saved
    pages), an article's is sentences between them (0.01-0.05).

    Runs over the nav/header/footer/aside-stripped HTML so both sides of the
    ratio describe the main content area, and counts the same hrefs
    _count_content_links counts, so the two cannot disagree about which links
    are in play. One parse pass, so callers ask for it only after the cheaper
    link-count and density gates have already suggested a list.

    Never raises: unparseable markup returns 0.0, i.e. no list verdict.
    """
    return _list_shape(html, host)[0]


def _count_content_links(html: str, host: str) -> int:
    """Count same-domain, non-trivial <a> links in the main content area.

    Strips nav/header/footer/aside/script/style first so chrome links don't
    inflate the count. Excludes anchors (#), mailto:, javascript:, and
    off-domain links.
    """
    return sum(
        1 for m in _ANCHOR_RE.finditer(_STRIP_BLOCK_RE.sub("", html))
        if _is_content_href(m.group(1), host)
    )


def detect_page_type(
    html: str,
    url: str,
    content_type: str = "",
    extracted_text_len: int = 0,
) -> str:
    """Classify a page's structure from raw HTML + content-type.

    Conservative: returns "unknown" when no strong signal. Error-derived
    signals (js_shell / auth_wall) are NOT detected here — they are set later
    by _with_agent_hints from result.error, which overrides this value.
    """
    ct = (content_type or "").lower()
    if ct.startswith("application/pdf"):
        return "pdf"
    if ct.startswith("application/json") or ct.startswith("text/json"):
        return "json"
    # Data XML (sitemap / RSS / Atom / +xml APIs) is returned raw by the fetch
    # path, so the structural verdict has to name it rather than leave "unknown"
    # next to a body full of tags. xhtml+xml is an HTML document and stays out.
    if (ct.startswith(("application/xml", "text/xml"))
            or (ct.endswith("+xml") and not ct.startswith("application/xhtml"))):
        return "xml"
    if ct.startswith("image/"):
        return "image"
    if not html or not html.strip():
        return "unknown"

    low = html.lower()

    # Redirect: meta refresh or JS location assignment (and little real text).
    if _META_REFRESH_RE.search(low) or (_JS_REDIRECT_RE.search(low) and extracted_text_len < 500):
        return "redirect"

    # Parse visible text and exact structural attributes. Raw-HTML prefilters are
    # unsafe here because entities and inline tags can split a real signal.
    visible_text, has_active_paywall_attribute = _paywall_evidence(html)
    if any(marker in visible_text.lower() for marker in _PAYWALL_MARKERS):
        return "paywall"
    if has_active_paywall_attribute:
        return "paywall"

    # Forum / Q&A / docs markers (substring match in the raw HTML).
    if any(m in low for m in _QA_MARKERS):
        return "qa"
    if any(m in low for m in _FORUM_MARKERS):
        return "forum"
    if any(m in low for m in _DOCS_MARKERS):
        return "docs"

    # List page: many same-domain content links whose LABELS make up most of the
    # text, NOT an <article> page and NOT a docs page (caught above). This catches
    # index / search-result / category / archive pages whose main value is the
    # links onward. Conservative: an <article> page is an article even if it has
    # many cross-reference links (precision over recall — mislabelling an
    # article as a list sends the agent on a wrong crawl).
    #
    # Two measurements this used to get wrong, both from the same habit — reading
    # one cheap number as "how much prose does this page carry":
    #
    #   * `extracted_text_len` is what the EXTRACTOR returned, not what the page
    #     holds. Trafilatura reports the densest block, so on a long multi-section
    #     document it can under-count by 50x: a saved copy of the RFC 9110 spec
    #     page has 446k chars of visible text, extraction gave 8.4k, and 8.4k over
    #     194 links reads as "a link index". The verdict must not depend on how
    #     well extraction happened to go, so the prose measure is the max of the
    #     two (visible text is the whole document, so it over-reports rather than
    #     under-reports).
    #   * A link COUNT cannot tell an article from an index — a wiki article
    #     cross-references hundreds of same-domain pages and stays an article. The
    #     distinguishing property is whether the text IS the link labels, so the
    #     share of content-area text inside counted links must be substantial.
    #     Measured on real saved pages: index 0.16-0.43, article/reference
    #     0.01-0.05 (tests/page_fixtures).
    #   * ...and the SHARE is not enough either, because it is a ratio: an article
    #     that both links densely and carries a References block of near-pure link
    #     text clears 0.10 (en.wikipedia.org/wiki/Hypertext_Transfer_Protocol runs
    #     0.12). The absolute shape discriminates where the ratio cannot: no index
    #     page has a long run of text that isn't somebody's link label.
    try:
        host = urlparse(url).netloc.lower()
        if ":" in host:
            host = host.split(":", 1)[0]
    except Exception:
        host = ""
    n_links = _count_content_links(html, host) if host else 0
    # `extracted_text_len` is only a prose measure when it can be one. A raw-HTML
    # extraction reports the length of the MARKUP (tags, attributes, entities),
    # which on a link-dense page is several times its text — measured: the same
    # quotes index gave 2179 through the markdown path and 11021 through
    # extraction_type="html", and only the second one cleared the list gate. Text
    # extracted FROM a page cannot exceed the page's own visible text beyond
    # entity decoding, so a number well above it is markup and says nothing about
    # prose. This is the same mistake G16 fixed for under-reporting, in the other
    # direction: the verdict must not depend on which shape the caller asked for.
    visible_len = len(visible_text)
    if extracted_text_len > visible_len * 1.5:
        extracted_text_len = visible_len
    prose_len = max(extracted_text_len, visible_len)
    # >=20 content links and either short text or low text-per-link ratio.
    list_candidate = (n_links >= 20
                      and (prose_len < 1500 or prose_len / n_links < 200))
    # One parse gives both verdicts' numbers: the share/longest-run for the list
    # gate, and the longest run for the article gate below. Asking for it only
    # when one of the two cheap gates has already fired keeps tiny pages (and
    # error shells) off the extra pass; `_paywall_evidence` has already parsed
    # the document once by this point.
    share = longest_run = None
    if host and (list_candidate or prose_len >= ARTICLE_VISIBLE_TEXT_MIN):
        share, longest_run = _list_shape(html, host)

    if list_candidate and share is not None \
            and not _ARTICLE_TAG_RE.search(low):
        if share >= LIST_LABEL_SHARE_MIN and longest_run <= LIST_PROSE_RUN_MAX:
            return "list"

    # Article: an <article> tag is a strong article signal.
    if _ARTICLE_TAG_RE.search(low):
        return "article"
    # ...and the tag is not the only one. Long-form pages that carry no
    # <article> element are the common case on the sites agents fetch most:
    # Wikipedia's skin puts the body in a div, standards pages in a <pre>-ish
    # flow, most self-hosted docs in bare <div>s — so all of those used to come
    # back "unknown" and the agent read that as "nothing classified this page"
    # when the page in fact answered. The positive signal is the same
    # non-link run the list gate rejects on: a stretch of text long enough to be
    # a paragraph of prose, on a page with enough of it. Measured: GNU's
    # free-software definition 756 over 25k, Wikipedia's HTTP article 705+ over
    # 22k, RFC 9110 807 over 53k — versus HN 79, a quotes listing 148, an FSF
    # category index 279, a Python genindex 227.
    if (longest_run is not None
            and longest_run >= ARTICLE_PROSE_RUN_MIN
            and prose_len >= ARTICLE_VISIBLE_TEXT_MIN):
        return "article"

    return "unknown"


def page_type_from_error(error: str) -> str:
    """Map a finalized error string to a page_type override (definitive signals).

    Returns "" when the error does not imply a page_type, so the structural
    page_type from detect_page_type stands.
    """
    if not error:
        return ""
    e = error.lower()
    if e.startswith("js_shell_detected"):
        return "js_shell"
    # auth_wall_detected comes from _is_auth_wall on the extracted content;
    # auth_required/not_a_pdf+auth from the PDF pipeline. Both were expected to
    # set page_type="auth_wall" and only the second one did, so a login wall
    # reported error="auth_wall_detected" with page_type="unknown" and agents
    # kept citing the sign-in form.
    if e.startswith(("auth_required", "auth_wall_detected")) or (e.startswith("not_a_pdf") and "auth" in e):
        return "auth_wall"
    if e.startswith(("bot_challenge_detected", "bot_wall_detected")):
        return "captcha"
    if e.startswith("geo_redirect_detected"):
        return "redirect"
    return ""
