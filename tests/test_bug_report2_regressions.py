"""Regressions for the second external test report (dhole_fix_prompt.md).

That report numbers its items BUG-1..BUG-10 with its own scheme, which collides
with the numbering already pinned in test_bug_report_regressions.py (its BUG-5
is error-text-in-content; this report's BUG-5 is cookies). Each test below names
the observed symptom it locks down rather than the number.

Two items needed no code change and have no test here:

* report BUG-1 (parse resolving ``dhole_test.html`` to ``$HOME``) - relative
  paths already try cwd -> DHOLE_WORKDIR -> home and the miss lists every root
  (pinned by test_bug_report_regressions.py). What the report measured is the
  MCP host's cwd: it IS tried first, it is just the host's own install
  directory. DHOLE_WORKDIR is the lever.
* report BUG-10 (related_queries fragments) - the examples given predate 14.6's
  per-domain evidence counting + function-word lead filter; a live re-run
  produced no fragments of that kind.

Where the report's diagnosis was wrong (BUG-3), the test pins the defect that
actually reproduced: the scalar path returned elements[0], whose text is empty
whenever the first match is an image-only link.
"""

import asyncio
from pathlib import Path

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, ResponseModel

# The helpers under test are reached through the module, not by name: a name
# import of something a fix ADDS makes the whole file fail collection against
# the pre-fix revision, which hides which tests are actually red.
_safe_cookie_dict = server_mod._safe_cookie_dict

# A page whose anchors are assets and links, same-domain, no assets to trip the
# crawler's asset filter.
_LINKED_PAGE = (
    "<html><head><title>Start</title></head><body>"
    "<p>" + "Real body text so the extractor has something to chew on. " * 3 + "</p>"
    '<a href="/a">Alpha</a> <a href="/b">Beta</a> <a href="/c">Gamma</a>'
    "</body></html>"
)


class _Stop(Exception):
    """Aborts a stubbed fetch tier once the call under test has been observed."""


def _stub_crawl(monkeypatch, **kwargs):
    """Run a real smart_crawl against a stubbed fetch tier (no network)."""
    async def fake_smart_fetch(self, url=None, **ignored):
        return server_mod.ResponseModel(
            url=url or "", status=200, content=[_LINKED_PAGE],
            fetcher_used="http", content_ok=True, content_type="text/html",
        )

    monkeypatch.setattr(server_mod.MasterFetchServer, "smart_fetch", fake_smart_fetch)
    srv = server_mod.MasterFetchServer()
    return asyncio.run(srv.smart_crawl("https://example.com/", max_pages=5, cache_ttl=0, **kwargs))


class TestCookieShapes:
    """report BUG-5: ``options={cookies: "sessionid=abc123"}`` put no cookie on
    the wire at all - the string was iterated character by character, every
    entry was dropped, and the call still reported 200 + ``content_ok``.
    """

    def test_cookie_header_string_becomes_a_dict(self):
        assert _safe_cookie_dict("sessionid=abc123") == {"sessionid": "abc123"}

    def test_multiple_cookie_pairs(self):
        assert _safe_cookie_dict("a=1; b=2") == {"a": "1", "b": "2"}

    def test_plain_dict_and_documented_list_still_work(self):
        assert _safe_cookie_dict({"a": "b"}) == {"a": "b"}
        assert _safe_cookie_dict(
            [{"name": "n", "value": "v", "domain": "example.com"}]) == {"n": "v"}

    def test_junk_is_dropped_rather_than_raising(self):
        assert _safe_cookie_dict("not a cookie") is None
        assert _safe_cookie_dict("") is None

    def test_browser_tier_scopes_string_cookies_to_the_target_url(self):
        """The browser needs a domain; a Cookie header carries none."""
        assert server_mod._browser_cookies("a=1", "https://example.com/p") == [
            {"name": "a", "value": "1", "url": "https://example.com/p"}]

    def test_browser_tier_keeps_a_caller_supplied_list_untouched(self):
        """A list already carries its own domain/path scope - do not rewrite it."""
        jar = [{"name": "n", "value": "v", "domain": ".example.com"}]
        assert server_mod._browser_cookies(jar, "https://example.com/p") is jar


class TestUserAgentOverride:
    """report BUG-4: ``options.useragent`` was accepted and ignored - the HTTP
    tier always sent browserforge's rotating UA. The browser tier honoured it,
    so the same option worked or not depending on which tier answered.
    """

    @staticmethod
    def _headers(headers=None, useragent=None, stealthy=True):
        from dhole_mcp.fetcher import HTTPSession

        return HTTPSession(stealthy_headers=stealthy)._build_headers(headers, useragent)

    def test_explicit_useragent_replaces_the_generated_one(self):
        got = self._headers(useragent="DholeTestBot/1.0")
        assert [k for k in got if k.lower() == "user-agent"] == ["user-agent"]
        assert got["user-agent"] == "DholeTestBot/1.0"

    def test_extra_headers_user_agent_does_not_ride_along_as_a_second_header(self):
        """Header names are case-insensitive: a caller's "User-Agent" must
        replace browserforge's "user-agent", not sit next to it."""
        got = self._headers(headers={"User-Agent": "FromHeaders/1.0"})
        assert [v for k, v in got.items() if k.lower() == "user-agent"] == ["FromHeaders/1.0"]

    def test_the_option_outranks_extra_headers(self):
        got = self._headers(headers={"User-Agent": "FromHeaders/1.0"},
                            useragent="FromOption/1.0")
        assert [v for k, v in got.items() if k.lower() == "user-agent"] == ["FromOption/1.0"]

    def test_non_stealthy_session_still_honours_it(self):
        assert self._headers(useragent="Plain/1.0", stealthy=False) == {"user-agent": "Plain/1.0"}


class TestOptionsReachTheHttpTier:
    """report BUG-4/BUG-5 wiring: the option has to reach the HTTP request
    builder, not just the browser context."""

    def test_force_fetch_http_forwards_cookies_and_useragent(self, monkeypatch):
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_get(url, **kwargs):
            seen.update(kwargs)
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "get", staticmethod(fake_get))
        srv = server_mod.MasterFetchServer()
        with pytest.raises(_Stop):
            asyncio.run(srv._force_fetch(
                url="https://example.com/p", force_fetcher="http",
                extraction_type="markdown", css_selector=None,
                main_content_only=True, use_trafilatura=True, cache_ttl=0,
                offset=0, headless=True, real_chrome=False, wait=0, proxy=None,
                timeout=30000, network_idle=False, solve_cloudflare=True,
                block_webrtc=True, hide_canvas=True, extra_headers=None,
                useragent="DholeTestBot/1.0", cookies="sessionid=abc123",
            ))
        assert seen["cookies"] == {"sessionid": "abc123"}
        assert seen["useragent"] == "DholeTestBot/1.0"


class TestSchemaScalarAndAttribute:
    """report BUG-3 + BUG-9.

    BUG-3's diagnosis (a parser that chokes on large/nested HTML) was wrong: the
    same tree answers ``type: array`` fine, and ``.score`` works - what broke was
    the scalar path returning elements[0], whose text is empty whenever the first
    match is an image-only link. That is exactly the HN/iana repro.

    BUG-9: ``attribute`` was documented in neither schema path and implemented in
    neither.
    """

    _HTML = (
        "<html><body>"
        '<a href="/home"><img src="logo.png" alt="Home"></a>'
        '<a href="/next">Read more</a>'
        '<a href="/last">Last</a>'
        "</body></html>"
    )

    def _extract(self, **field):
        from dhole_mcp.structured import extract_structured

        return extract_structured(self._HTML, {"properties": {"x": field}})["x"]

    def test_scalar_selector_skips_an_image_only_first_match(self):
        """The first <a> wraps an <img>, so its text_content() is empty."""
        assert self._extract(selector="a") == "Read more"

    def test_text_is_still_returned_when_the_first_match_has_text(self):
        """The report's working case (example.com) must not regress."""
        from dhole_mcp.structured import extract_structured

        html = ('<html><body><a href="https://iana.org/domains/example">'
                "Learn more</a></body></html>")
        got = extract_structured(html, {"properties": {"x": {"selector": "a"}}})
        assert got["x"] == "Learn more"

    def test_attribute_returns_the_attribute_value(self):
        assert self._extract(selector="a", attribute="href") == "/home"

    def test_attribute_works_with_arrays(self):
        assert self._extract(selector="a", attribute="href", type="array") == [
            "/home", "/next", "/last"]

    def test_array_of_texts_still_works(self):
        assert self._extract(selector="a", type="array") == ["Read more", "Last"]

    def test_missing_attribute_falls_back_to_empty(self):
        assert self._extract(selector="a", attribute="data-nope") == ""

    def test_count_is_unaffected_by_attribute(self):
        assert self._extract(selector="a", type="count", attribute="href") == 3


class TestCrawlPathFilterAcceptsAString:
    """report BUG-6: ``path_exclude="/what/"`` was iterated character by
    character (``for p in path_exclude``), and ``startswith("/")`` holds for
    every path - so a string filter silently filtered the whole crawl away while
    the list form crawled normally.
    """

    def test_string_filter_does_not_drop_every_link(self, monkeypatch):
        result = _stub_crawl(monkeypatch, path_exclude="/what/")
        assert result.pages_crawled > 1, "一个字符串把整站都过滤掉了"

    def test_string_and_list_agree(self, monkeypatch):
        as_string = _stub_crawl(monkeypatch, path_exclude="/what/")
        as_list = _stub_crawl(monkeypatch, path_exclude=["/what/"])
        assert as_string.pages_crawled == as_list.pages_crawled == 4

    def test_the_filter_still_filters(self, monkeypatch):
        result = _stub_crawl(monkeypatch, path_exclude=["/a"])
        assert result.pages_crawled == 3
        assert not any(p.url.endswith("/a") for p in result.pages)
        assert any(p.url.endswith("/b") for p in result.pages)

    def test_string_include_only_keeps_matching_paths(self, monkeypatch):
        """The include filter had the same bug, in the opposite direction: the
        char-iterated string matched every path, so the filter was a no-op."""
        result = _stub_crawl(monkeypatch, path_include="/a")
        assert result.pages_crawled == 2
        assert not any(p.url.endswith("/b") for p in result.pages)


class TestCrawlSearchOptionAndSummary:
    """report BUG-7: ``search`` was documented for smart_crawl and implemented
    top-level, but the same key inside the options bag raised "Unsupported
    option key(s)".

    Found alongside it: when the filter matched nothing, the summary kept the
    PRE-filter stats - "crawled 1 page(s) ... 1 content_ok" next to
    ``pages_crawled: 0`` and ``pages: []``.
    """

    @staticmethod
    def _dispatched_search(monkeypatch, args):
        from dhole_mcp import server as server_mod
        from dhole_mcp.crawl import CrawlResponseModel

        seen: dict = {}

        async def fake_crawl(self, url=None, **kwargs):
            seen.update(kwargs)
            return CrawlResponseModel(start_url=url or "", pages=[])

        monkeypatch.setattr(server_mod.MasterFetchServer, "smart_crawl", fake_crawl)
        asyncio.run(server_mod.MasterFetchServer()._dispatch("smart_crawl", args))
        return seen.get("search")

    def test_search_in_the_options_bag_is_accepted(self, monkeypatch):
        assert self._dispatched_search(monkeypatch, {
            "url": "https://example.com/", "options": {"search": "interpreter"},
        }) == "interpreter"

    def test_top_level_search_still_wins(self, monkeypatch):
        assert self._dispatched_search(monkeypatch, {
            "url": "https://example.com/", "search": "top",
            "options": {"search": "opt"},
        }) == "top"

    def test_filtered_away_summary_describes_the_pages_returned(self, monkeypatch):
        result = _stub_crawl(monkeypatch, search="nomatch")
        assert result.pages == [] and result.pages_crawled == 0
        assert f"crawled {len(result.pages)} page(s)" in result.summary
        assert "nomatch" in result.next_action


class TestLocalPdfReportsItsEnvelope:
    """report BUG-8: parse returned ``table_of_contents: []`` and ``metadata: {}``
    for a local PDF while smart_fetch on the same PDF by URL returned both - the
    extractor already produced them and the parse path dropped them.
    """

    @pytest.fixture
    def pdf_with_outline(self, tmp_path):
        pytest.importorskip("pypdf")
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(Path(__file__).parent / "background_checks.pdf"))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_outline_item("Intro", 0)
        writer.add_metadata({"/Producer": "dhole-test"})
        out = tmp_path / "outlined.pdf"
        with open(out, "wb") as fh:
            writer.write(fh)
        return out

    def test_parse_file_detailed_returns_toc_and_metadata(self, pdf_with_outline):
        pytest.importorskip("pdfplumber")
        from dhole_mcp.parse import parse_file_detailed

        content, error, extras = parse_file_detailed(str(pdf_with_outline))
        assert error == "" and content
        assert extras["table_of_contents"][0]["title"] == "Intro"
        assert extras["table_of_contents"][0]["page"] == 1
        assert extras["metadata"]["producer"] == "dhole-test"
        assert extras["quality_score"] > 0
        assert extras["content_ok"] is True

    def test_parse_file_keeps_its_two_tuple(self, pdf_with_outline):
        pytest.importorskip("pdfplumber")
        from dhole_mcp.parse import parse_file

        content, error = parse_file(str(pdf_with_outline))
        assert error == "" and content

    def test_the_response_envelope_carries_them(self, pdf_with_outline):
        pytest.importorskip("pdfplumber")

        out = asyncio.run(MasterFetchServer().parse(file_path=str(pdf_with_outline)))
        assert out.table_of_contents[0]["title"] == "Intro"
        assert out.metadata["producer"] == "dhole-test"
        assert out.quality_score > 0
        # Carrying quality_score flips _agent_hints onto the "defer to the
        # extractor" branch, so content_ok has to travel with it: a healthy PDF
        # reported content_ok=False here, which reads to an agent as "do not cite".
        assert out.content_ok is True


class TestActionsTierBudget:
    """report BUG-2: actions force the stealthy tier but inherited the 30s
    HTTP-era default, so a cold browser start spent the whole budget on
    example.com (measured 30012ms).
    """

    def _budget_for_actions(self, monkeypatch, **kwargs):
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_force_fetch(self, url, force_fetcher, extraction_type,
                                   css_selector, main_content_only, use_trafilatura,
                                   cache_ttl, offset, headless, real_chrome, wait,
                                   proxy, timeout, network_idle, solve_cloudflare,
                                   block_webrtc, hide_canvas, extra_headers,
                                   useragent, cookies, max_chars=None, page_action=None):
            seen["timeout"] = timeout
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_force_fetch", fake_force_fetch)
        with pytest.raises(_Stop):
            asyncio.run(server_mod.MasterFetchServer().smart_fetch(
                url="https://example.com/", actions=[{"scroll": 300}], **kwargs))
        return seen["timeout"]

    def test_actions_get_a_browser_sized_default(self, monkeypatch):
        assert self._budget_for_actions(monkeypatch) == 60000

    def test_an_explicit_timeout_still_wins(self, monkeypatch):
        assert self._budget_for_actions(monkeypatch, timeout=12345) == 12345

    def test_plain_fetches_keep_the_documented_30s(self, monkeypatch):
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_auto_escalate(self, url, extraction_type, css_selector,
                                     main_content_only, use_trafilatura, cache_ttl,
                                     offset, headless, real_chrome, wait, proxy,
                                     timeout, network_idle, solve_cloudflare,
                                     block_webrtc, hide_canvas, extra_headers,
                                     useragent, cookies, max_chars=None):
            seen["timeout"] = timeout
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake_auto_escalate)
        with pytest.raises(_Stop):
            asyncio.run(server_mod.MasterFetchServer().smart_fetch(url="https://example.com/"))
        assert seen["timeout"] == 30000

    def test_the_dispatcher_leaves_the_default_to_smart_fetch(self, monkeypatch):
        """The MCP layer used to substitute 30000 itself, which is why the
        actions default could never take effect."""
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_smart_fetch(self, **kwargs):
            seen.update(kwargs)
            return ResponseModel(url=kwargs.get("url", ""), status=0, content=[],
                                 error="stub")

        monkeypatch.setattr(server_mod.MasterFetchServer, "smart_fetch", fake_smart_fetch)
        asyncio.run(server_mod.MasterFetchServer()._dispatch(
            "smart_fetch", {"url": "https://example.com/"}))
        assert seen["timeout"] is None
