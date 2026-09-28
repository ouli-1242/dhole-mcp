"""RSS/Atom feed fetching for Dhole.

Fetches and parses RSS 2.0 / Atom feeds so an agent can track what a source
*has published* (fresh items), as opposed to fetching a page and reading it.

No caching: feeds are inherently fresh content; a cached feed defeats the
purpose. Uses the existing fetcher.HTTPSession (TLS impersonation) so feeds
behind basic bot protection still parse.

Parser is lenient (lxml, tolerant of malformed XML): a feed that partially
fails still returns whatever items parsed, with the error surfaced per-source.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("dhole_mcp.feed")

_MAX_FEED_BYTES = 2 * 1024 * 1024  # 2MB cap — feeds are small text; larger is junk

# An item's description is a preview, not the article: full-text bodies from
# feedburner-style feeds run to 50KB and would bury the 20-item list. The cut is
# disclosed per item (summary_truncated) rather than silently, because a
# truncated mid-sentence preview is otherwise indistinguishable from a feed that
# simply wrote that little.
_SUMMARY_CHARS = 500


def _preview(text: str) -> tuple[str, bool]:
    """(the capped preview, whether anything was cut)."""
    return text[:_SUMMARY_CHARS], len(text) > _SUMMARY_CHARS


class FeedItem(BaseModel):
    """A single entry from an RSS/Atom feed."""
    title: str = Field(default="", description="Entry title")
    url: str = Field(default="", description="Entry link")
    published: str = Field(default="", description="ISO-8601 publish date (empty if unknown)")
    summary: str = Field(default="", description=f"Entry summary/description, previewed to {_SUMMARY_CHARS} chars")
    summary_truncated: bool = Field(
        default=False,
        description="True when the feed's own text was longer than the preview cap, "
                    "so the caller knows the summary above is a cut and not the whole thing.")


class FeedResult(BaseModel):
    """Parsed feed for one source URL."""
    source_url: str = Field(description="The feed URL requested")
    source_title: str = Field(default="", description="Feed/channel title")
    items: list[FeedItem] = Field(default=[], description="Entries, newest first")
    error: str = Field(default="", description="Fetch/parse error (empty = ok)")
    discovered_from: str = Field(
        default="",
        description="Set when the URL was a web page: the feed was found through "
                    "that page's <link rel=\"alternate\" type=\"application/rss+xml\">, "
                    "so source_url is the page and the parsed feed is elsewhere.")
    since: str = Field(default="", description="The cutoff this result was filtered by (normalized to ISO-8601 UTC), empty when no filter was asked for.")
    items_older_than_since: int = Field(default=0, description="Entries dropped because they were published BEFORE `since` - the 'nothing new' half of a poll. Reported rather than hidden: a bare list of items cannot tell 'the feed has 3 items total' apart from '50 were filtered out and 3 are new'.")
    items_without_date: int = Field(default=0, description="Entries KEPT despite having no usable publish date. A feed that does not date its items cannot be filtered honestly, so these are returned and counted instead of silently dropped.")
    cache_validators: dict = Field(
        default_factory=dict,
        description="The feed server's own version markers for this URL: {etag, last_modified} "
                    "(either may be absent). Save them and pass them back as if_none_match / "
                    "if_modified_since to ask 'anything new?' without downloading the whole "
                    "document - an unchanged feed then answers not_modified=true with no items, "
                    "which is a successful poll, not an empty feed.")
    not_modified: bool = Field(
        default=False,
        description="True = this call sent a conditional request and the feed server answered "
                    "304 'not modified'. items is empty BY DESIGN (there was no body to parse); "
                    "error stays empty. Read it as 'nothing new since your last poll'.")
    note: str = Field(
        default="",
        description="One line the caller could not otherwise infer from this result. Set "
                    "when (a) the document parsed as a feed and genuinely carried zero "
                    "entries - an empty items list with no error cannot tell 'nothing "
                    "published today' from 'our parser stopped matching this feed', so the "
                    "feed's own markers (lastBuildDate / pubDate / skipDays / updated) are "
                    "quoted; and when (b) a conditional poll was answered with 200 + the "
                    "whole document instead of a 304, which means nothing was compared and "
                    "is not evidence of change. Both can appear at once, separated by ' | '. "
                    "Empty for feeds with items that answered a plain request, for a real "
                    "304, and wherever `error` or the since-receipts already explain it.")


def _parse_date(value: str) -> str:
    """Best-effort RFC822 / ISO-8601 parse to ISO-8601. Returns '' if unparseable."""
    if not value:
        return ""
    v = value.strip()
    # RFC 822 (RSS): "Tue, 05 Aug 2026 12:00:00 +0000"
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(v)
        if dt:
            return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        pass
    # ISO-8601 (Atom): "2026-08-05T12:00:00Z"
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return ""
    # A date with no offset is UTC, not "whatever clock this machine runs on".
    # astimezone() on a naive datetime reads it as LOCAL time, so a feed that
    # published "2026-09-26" used to land at 2026-09-25T16:00Z on a +08:00 host -
    # eight hours of drift in a filter whose whole job is comparing dates.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _local_name(tag: str) -> str:
    """Strip XML namespace: '{http://www.w3.org/2005/Atom}entry' -> 'entry'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(elem: Any, *tags: str) -> str:
    """First non-empty text of the given child tags (namespace-agnostic)."""
    for child in elem:
        if _local_name(child.tag) in tags and child.text and child.text.strip():
            return child.text.strip()
    return ""


def _add_note(existing: str, extra: str) -> str:
    """Append a second observation to `note` without losing the first.

    A feed can legitimately carry two at once: the origin ignored our
    conditional AND the document held no entries. Both explain the same empty
    list, and dropping either leaves a caller to guess.
    """
    return f"{existing} | {extra}" if existing else extra


def _empty_feed_note(root: Any) -> str:
    """What the document itself says about carrying nothing.

    ``items: []`` with an empty ``error`` is genuinely ambiguous: the site may
    have published nothing today, or our parser may have stopped matching the
    shape it now serves. Those two call for opposite actions (wait vs. fix the
    parser), and before this the response could not tell them apart at all.

    A feed that is doing this on purpose usually says so in its own channel -
    arXiv's weekend edition is the everyday case: zero ``<item>``, but
    ``lastBuildDate`` plus ``skipDays`` (Saturday, Sunday) explain the gap. So
    quote what is there instead of guessing; the absence of any marker is itself
    the signal worth seeing.
    """
    markers: list[str] = []
    for tag in ("lastBuildDate", "pubDate", "updated", "generator"):
        for elem in root.iter():
            if _local_name(elem.tag) == tag:
                value = (elem.text or "").strip()
                if value:
                    markers.append(f"{tag}={value[:40]}")
                break
    for elem in root.iter():
        if _local_name(elem.tag) == "skipDays":
            days = [(c.text or "").strip() for c in elem if (c.text or "").strip()]
            if days:
                markers.append("skipDays=" + ",".join(days)[:60])
            break
    head = "0 entries in a document that parsed as a feed"
    return f"{head}; its own markers: {'; '.join(markers)}" if markers else head


def _parse_feed_xml(raw: str, source_url: str) -> FeedResult:
    """Parse RSS 2.0 or Atom XML into a FeedResult (newest-first)."""
    from lxml import etree
    root = etree.fromstring(raw.encode("utf-8", errors="replace"))
    root_name = _local_name(root.tag)

    source_title = _text(root, "title", "subtitle")
    items: list[FeedItem] = []

    if root_name == "feed":  # Atom
        for entry in root.iterfind("*"):
            if _local_name(entry.tag) != "entry":
                continue
            link = ""
            for child in entry:
                if _local_name(child.tag) == "link":
                    link = child.get("href", "") or link
            summary, cut = _preview(_text(entry, "summary", "content"))
            items.append(FeedItem(
                title=_text(entry, "title"),
                url=link,
                published=_parse_date(_text(entry, "published", "updated", "date")),
                summary=summary,
                summary_truncated=cut,
            ))
    else:  # RSS 2.0 (channel/item), also handles rdf:RDF
        channel = next((c for c in root if _local_name(c.tag) == "channel"), None)
        if channel is not None:
            source_title = _text(channel, "title")
        for item in root.iter():
            if _local_name(item.tag) != "item":
                continue
            summary, cut = _preview(_text(item, "description"))
            items.append(FeedItem(
                title=_text(item, "title"),
                url=_text(item, "link"),
                published=_parse_date(_text(item, "pubDate", "published", "date")),
                summary=summary,
                summary_truncated=cut,
            ))

    # Newest first. The undated group is split out rather than expressed as a
    # sort flag: sorting (has_date, date) with reverse=True also reverses the
    # flag, which used to put every undated item at the TOP of a "newest first"
    # list - the opposite of the comment that stood here.
    dated = [it for it in items if it.published]
    undated = [it for it in items if not it.published]
    dated.sort(key=lambda it: it.published, reverse=True)
    return FeedResult(source_url=source_url, source_title=source_title,
                      items=dated + undated,
                      note=_empty_feed_note(root) if not items else "")


async def _grab(url: str, timeout: int,
                headers: dict | None = None) -> tuple[bytes, int, str, str, dict]:
    """(body, status, encoding, error, response-headers) for one URL. Never raises.

    The headers come back because a feed's ETag/Last-Modified are the whole point
    of a poll: without them the only way to ask "anything new?" is to download the
    whole document and compare it yourself.
    """
    try:
        from dhole_mcp.fetcher import HTTPSession
        async with HTTPSession(stealthy_headers=False, retries=1, timeout=timeout) as session:
            resp = await session.get(url, follow_redirects="safe",
                                     headers=headers or None)
        hdrs = {str(k).lower(): str(v)
                for k, v in (getattr(resp, "headers", None) or {}).items()}
        status = int(getattr(resp, "status_code", getattr(resp, "status", 0)) or 0)
        if status == 304:
            # The answer to "anything new?" is "no", and there is no body to
            # parse. Reporting that as a fetch failure is the mistake this path
            # exists to avoid.
            return b"", 304, "", "", hdrs
        body = getattr(resp, "body", b"") or b""
        if len(body) > _MAX_FEED_BYTES:
            return b"", 0, "", f"feed too large ({len(body)} bytes, cap {_MAX_FEED_BYTES})", hdrs
        if status >= 400:
            return b"", status, "", f"HTTP {status}", hdrs
        return body, status, (getattr(resp, "encoding", None) or "utf-8"), "", hdrs
    except Exception as e:
        logger.debug("feed fetch failed for %s: %s", url, e)
        return b"", 0, "", f"{type(e).__name__}: {str(e)[:160]}", {}


# A feed's own site nearly always says where the feed is; the tag is the contract.
# Both attribute orders appear in the wild, and JSON feeds (Ghost et al.) announce
# themselves as application/feed+json.
_FEED_TYPES = r"(?:rss|atom)\+xml|feed\+json"
_FEED_LINK_RE = re.compile(
    r"<link\b[^>]*?(?:rel\s*=\s*[\"']?alternate[\"']?[^>]*?type\s*=\s*[\"']?application/"
    + _FEED_TYPES + r"[\"']?|type\s*=\s*[\"']?application/" + _FEED_TYPES +
    r"[\"']?[^>]*?rel\s*=\s*[\"']?alternate[\"']?)[^>]*?>",
    re.IGNORECASE,
)
_HREF_RE = re.compile(r"href\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
_FEED_MIME = ("application/rss+xml", "application/atom+xml", "application/feed+json",
              "text/xml", "application/xml", "application/rdf+xml")


def _discover_feed_links(html: str, base_url: str) -> list[str]:
    """Feed URLs announced by a page's <link rel=alternate type=application/…+xml>.

    Regex, not an HTML parser, on purpose: this runs on pages we already failed to
    parse, so a parse step here could fail on exactly the input that needs it.
    Attribute order varies in the wild, which is why both orders are matched.
    """
    from urllib.parse import urljoin

    out: list[str] = []
    for tag in _FEED_LINK_RE.finditer(html):
        href = _HREF_RE.search(tag.group(0))
        if not href:
            continue
        candidate = href.group(1).strip()
        if not candidate or candidate.startswith(("javascript:", "data:")):
            continue
        absolute = urljoin(base_url, candidate)
        if absolute not in out:
            out.append(absolute)
    return out


def _looks_like_html(body: bytes) -> bool:
    head = body[:600].lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<html" in head


def _friendly_parse_error(exc: Exception | None, raw: str, tried_html: bool) -> str:
    """One actionable line instead of lxml's multi-line parse dump.

    `XMLSyntaxError: Opening and ending tag mismatch: meta line 5 column 99, line
    6, column 14` is a true statement about a document nobody meant to feed to an
    XML parser: it is the site's HOME PAGE. The useful answer is what we then did
    about it, not where the tag didn't close.
    """
    if exc is None:
        detail = "no <rss>/<feed> root element"
    else:
        text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        detail = text[:140]
        if any(n in text for n in ("mismatched tag", "not well-formed", "Invalid document",
                                   "tag mismatch", "Start tag found")):
            detail = "the document is not well-formed XML"
    where = " (the response is an HTML page" if tried_html else ""
    return (f"not a feed: {detail}{where}). Pass the feed URL itself - the address "
            "in a browser bar is usually the site page, not the feed.")


def _parse_or_none(raw: str, url: str) -> tuple[FeedResult | None, Exception | None]:
    try:
        result = _parse_feed_xml(raw, url)
    except Exception as e:
        return None, e
    return result, None


def _since_cutoff(value: Any) -> tuple:
    """Normalize a ``since`` option to ``(aware datetime, ISO-8601, reason)``.

    ``datetime`` is None when no filter was asked for; ``reason`` is non-empty when
    a value arrived that cannot be read as a date. A poller asking "anything since
    last Tuesday?" needs the whole answer refused in that case, not a filter that
    quietly keeps everything and reads like a slow news week.
    """
    if value in (None, "", False):
        return None, "", ""
    if not isinstance(value, str):
        return None, "", "since must be a date string (ISO-8601 or an RSS timestamp)"
    iso = _parse_date(value)
    if not iso:
        return None, "", (f"since={value[:60]!r} is not a date - use ISO-8601 "
                          "(2026-09-20 or 2026-09-20T08:00:00Z) or an RFC-822 feed "
                          "timestamp (Sat, 20 Sep 2026 08:00:00 +0000)")
    # Same rule as _parse_date: a date with no offset is UTC, not local time.
    stamp = datetime.fromisoformat(iso)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp, iso, ""


def _keep_newer(result: FeedResult, cutoff_iso: str, cutoff, max_items: int) -> FeedResult:
    """Drop entries published before ``cutoff``, then apply ``max_items``.

    The order matters: a feed that lists 60 old items and 2 new ones has to be
    filtered BEFORE the cap, or the cap decides what 'new' means.
    """
    result.since = cutoff_iso
    kept: list[FeedItem] = []
    older = undated = 0
    for item in result.items:
        iso = _parse_date(item.published)
        stamp = None
        if iso:
            try:
                stamp = datetime.fromisoformat(iso)
            except ValueError:
                stamp = None
        if stamp is None:
            # Undated is not the same as old. Dropping it would hide items the
            # feed never dated; keeping them silently would pretend the filter
            # covered everything. So: keep, and say how many.
            undated += 1
            kept.append(item)
            continue
        if stamp < cutoff:
            older += 1
            continue
        kept.append(item)
    result.items = kept
    result.items_older_than_since = older
    result.items_without_date = undated
    if max_items > 0:
        result.items = result.items[:max_items]
    return result


def _feed_conditional_headers(if_modified_since: str,
                              if_none_match: str) -> tuple[dict, str]:
    """(headers, rejection) for a conditional poll; same rules smart_fetch uses.

    A bare etag token is the usual thing a caller writes after copying
    `cache_validators.etag` — the quotes belong to the header, not to the tag.
    Line breaks are refused: they would let a caller's value become a new request
    header.
    """
    out: dict[str, str] = {}
    raw = (if_none_match or "").strip()
    if raw:
        if len(raw) > 256 or any(c in raw for c in "\r\n"):
            return {}, ("if_none_match must be an ETag string without line breaks "
                        "(256 characters max)")
        if not raw.startswith(('"', "W/")) and raw != "*":
            raw = f'"{raw}"'
        out["If-None-Match"] = raw
    raw = (if_modified_since or "").strip()
    if raw:
        if any(c in raw for c in "\r\n"):
            return {}, "if_modified_since must be a date string without line breaks"
        out["If-Modified-Since"] = raw
    return out, ""


async def fetch_feed(url: str, timeout: int = 20, max_items: int = 20,
                     since: str = "", if_modified_since: str = "",
                     if_none_match: str = "") -> FeedResult:
    """Fetch and parse a single RSS/Atom feed. Never raises.

    A URL that turns out to be a web page is not a dead end: the page announces
    its own feed with ``<link rel="alternate" type="application/rss+xml">``, so
    that is followed (up to two candidates) and reported back with
    ``discovered_from`` set. That is the difference between "feed_fetch on a
    homepage errors" and it answering the question the caller actually had.

    Returns a FeedResult with items parsed so far; on fetch failure the error
    field carries the reason and items is empty.

    ``since`` (G23, the incremental half of monitoring) keeps only entries
    published at or after that date and reports what the filter cost in
    ``items_older_than_since`` / ``items_without_date`` instead of shrinking the
    list in silence.

    ``if_none_match`` / ``if_modified_since`` ask the question ``since`` cannot:
    ``since`` still downloads the whole feed to discover nothing was added, while a
    conditional GET costs one header and a 304. The server's markers are returned
    in ``cache_validators`` for the next poll; an unchanged feed is
    ``not_modified=true`` with no items, which is an answer, not an empty feed.
    """
    from dhole_mcp.security import SecurityError, validate_url

    cutoff, cutoff_iso, since_rejected = _since_cutoff(since)
    if since_rejected:
        return FeedResult(source_url=url, error=since_rejected)
    cond, cond_rejected = _feed_conditional_headers(if_modified_since, if_none_match)
    if cond_rejected:
        return FeedResult(source_url=url, error=cond_rejected)

    def _shape(result: FeedResult) -> FeedResult:
        if validators and not result.cache_validators:
            result.cache_validators = validators
        if cond and status == 200 and body:
            # The question was asked and refused. This origin answered our
            # conditional with 200 + the whole document rather than a 304, so
            # nothing was compared and "not a 304" is not evidence that anything
            # changed - the reading a caller is otherwise left with. Measured:
            # arXiv does exactly this to its RSS ETags, as it does to every PDF
            # etag (the same disclosure exists on smart_fetch for that reason).
            result.note = _add_note(
                result.note,
                f"the origin answered our {' + '.join(sorted(cond))} with 200 and "
                "the whole feed instead of a 304, so it does not honour "
                "conditional requests for this URL. Nothing was compared, so "
                "this is NOT evidence the content changed; expect to download "
                "the full document on every poll, or filter with since= instead.")
        if cutoff is not None:
            return _keep_newer(result, cutoff_iso, cutoff, max_items)
        if max_items > 0:
            result.items = result.items[:max_items]
        return result

    body, status, encoding, err, hdrs = await _grab(url, timeout, cond)
    validators = {k: v for k, v in (("etag", (hdrs or {}).get("etag", "")),
                                    ("last_modified", (hdrs or {}).get("last-modified", "")))
                  if v}
    if status == 304:
        return FeedResult(source_url=url, not_modified=True,
                          cache_validators=validators, since=cutoff_iso)
    if err and not body:
        return FeedResult(source_url=url, error=err, cache_validators=validators)

    raw = body.decode(encoding or "utf-8", errors="replace")
    parsed, parse_exc = _parse_or_none(raw, url)
    if parsed is not None and parsed.items:
        return _shape(parsed)

    # Not a feed (or a feed we could not read): try what the page itself says.
    is_html = _looks_like_html(body)
    candidates = _discover_feed_links(raw, url) if is_html or parse_exc is not None else []
    for candidate in candidates[:2]:
        try:
            feed_url = validate_url(candidate)
        except SecurityError as se:
            logger.debug("discovered feed target refused: %s", str(se)[:120])
            continue
        cbody, _cst, cenc, cerr, chdrs = await _grab(feed_url, timeout)
        if cerr and not cbody:
            continue
        cparsed, _cexc = _parse_or_none(
            cbody.decode(cenc or "utf-8", errors="replace"), feed_url)
        if cparsed is not None and cparsed.items:
            cparsed.source_url = feed_url
            cparsed.discovered_from = url
            return _shape(cparsed)

    if parsed is not None and (parsed.items or parsed.source_title):
        # Parsed as XML and said something, just no entries: honest, not an error.
        return _shape(parsed)

    error = _friendly_parse_error(parse_exc, raw, is_html)
    if candidates:
        error += (" The page advertises feed candidate(s): "
                  + ", ".join(candidates[:3]) + " - none of them parsed.")
    return FeedResult(source_url=url, error=error)


async def fetch_feeds(
    urls: list[str],
    timeout: int = 20,
    max_items: int = 20,
    concurrency: int = 5,
    since: str = "",
    if_modified_since: str = "",
    if_none_match: str = "",
) -> list[FeedResult]:
    """Fetch multiple feeds with bounded concurrency. Per-source errors are
    isolated (one bad feed never fails the batch).

    The conditional markers are one set for the whole call, so the caller above
    refuses them together with several URLs (server.feed_fetch): asking feed B
    with feed A's etag produces a 304 that means nothing.
    """
    import asyncio
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(url: str) -> FeedResult:
        async with sem:
            return await fetch_feed(url, timeout=timeout, max_items=max_items,
                                    since=since, if_modified_since=if_modified_since,
                                    if_none_match=if_none_match)

    return list(await asyncio.gather(*(_one(u) for u in urls)))