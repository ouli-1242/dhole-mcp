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


class TestParseDecodesLegacyEncodings:
    """report BUG-3 (P0): parse read .csv/.html as UTF-8 with errors="replace",
    so a GBK file - Excel's default "CSV (逗号分隔)" export on a Chinese Windows
    box - came back as U+FFFD soup while content_ok stayed true and error stayed
    empty. Reproduced live through the MCP server on a 90-byte CSV:
    "| ���� | ���� |" with content_ok=true.

    The fix has to hold three separate lines, so there are three groups below:
    the bytes decode to the right text, a guess that went wrong is visible, and
    the caller can correct it.
    """

    GBK_CSV = "姓名,年龄,备注\n张三,30,工程师\n李四,25,设计师\n"

    def _write(self, tmp_path, name, text, encoding):
        path = tmp_path / name
        path.write_bytes(text.encode(encoding))
        return path

    # ── the bytes decode to the right text ──────────────────────────────

    def test_gbk_csv_is_not_mojibake(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "gbk.csv", self.GBK_CSV, "gbk")
        content, error, extras = parse_file_detailed(str(path))
        assert error == ""
        # Exact text, not just "no replacement char": a decode that silently
        # produced the wrong CJK characters would pass a weaker assertion.
        assert "| 姓名 | 年龄 | 备注 |" in content
        assert "| 张三 | 30 | 工程师 |" in content
        assert "\ufffd" not in content
        assert extras["encoding"] == "gb18030"
        assert "decode_damage" not in extras

    def test_gbk_html_is_not_mojibake(self, tmp_path):
        pytest.importorskip("trafilatura")
        from dhole_mcp.parse import parse_file_detailed

        html = ("<html><body><h1>标题</h1>"
                "<p>这是一段中文正文，用来验证解码是否正确。</p></body></html>")
        path = self._write(tmp_path, "gbk.html", html, "gbk")
        content, error, extras = parse_file_detailed(str(path))
        assert error == ""
        assert "这是一段中文正文" in content
        assert "\ufffd" not in content

    def test_utf8_is_untouched(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "utf8.csv", self.GBK_CSV, "utf-8")
        content, error, extras = parse_file_detailed(str(path))
        assert error == "" and "| 姓名 | 年龄 | 备注 |" in content
        assert extras["encoding"] == "utf-8"

    def test_a_bom_outranks_detection(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed

        for enc, expected in (("utf-8-sig", "utf-8-sig"), ("utf-16", "utf-16"),
                              ("utf-32", "utf-32")):
            path = self._write(tmp_path, f"bom-{expected}.csv", self.GBK_CSV, enc)
            content, error, extras = parse_file_detailed(str(path))
            assert error == "", f"{enc}: {error}"
            assert "姓名" in content, f"{enc} decoded to {content!r}"
            assert extras["encoding"] == expected

    def test_utf16_without_a_bom_is_still_read(self, tmp_path):
        """A stripped BOM used to mean GB18030 "succeeded" on the NUL bytes and
        returned text full of them - damage 0, content garbage."""
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "nobom.csv", self.GBK_CSV, "utf-16-le")
        content, error, extras = parse_file_detailed(str(path))
        assert error == "" and "姓名" in content
        assert "\x00" not in content

    def test_semicolon_delimited_csv_keeps_its_columns(self, tmp_path):
        """Excel in a comma-decimal locale writes ';'. Parsed as comma-
        separated, the whole row lands in column 1 - silently wrong, not empty.
        """
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "eu.csv", "姓名;年龄\n张三;30\n", "gbk")
        content, error, _ = parse_file_detailed(str(path))
        assert error == ""
        assert "| 姓名 | 年龄 |" in content

    # ── a wrong guess is visible, not silent ────────────────────────────

    def test_a_damaged_decode_is_reported_not_returned_as_content(self, tmp_path):
        """Big5 decoded as GB18030 comes out wrong. Measured: GB18030 maps the
        Big5 byte pairs into the Private Use Area, which is what makes it
        detectable - and a flagged answer beats a confident wrong one.
        """
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "big5.csv", "姓名,年齡,備註\n張三,30\n", "big5")
        _, _, extras = parse_file_detailed(str(path))
        assert extras["decode_damage"] > 0, "Big5-as-GB18030 should be flagged"

    def test_the_response_says_do_not_trust_this(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed  # noqa: F401

        path = self._write(tmp_path, "big5.csv", "姓名,年齡,備註\n張三,30\n", "big5")
        out = asyncio.run(MasterFetchServer().parse(file_path=str(path)))
        assert out.error.startswith("encoding_undecodable")
        assert out.content_ok is False
        # The content still travels - it is what the caller needs to look at -
        # but status stays 200 because the file itself was read fine.
        assert out.content and out.status == 200
        # Without this branch the generic parse hint fired and sent the caller
        # off to check paths that were never the problem.
        assert "encoding=" in out.next_action

    def test_a_clean_decode_is_not_flagged(self, tmp_path):
        path = self._write(tmp_path, "gbk.csv", self.GBK_CSV, "gbk")
        out = asyncio.run(MasterFetchServer().parse(file_path=str(path)))
        assert out.error == ""
        assert out.content_ok is True
        assert out.metadata["encoding"] == "gb18030"

    def test_an_unreadable_file_still_returns_no_content(self, tmp_path):
        """The damaged-decode branch keeps its content; a real failure must not
        start leaking an empty string as content."""
        out = asyncio.run(MasterFetchServer().parse(file_path=str(tmp_path / "nope.csv")))
        assert out.status == 0 and out.content == [] and out.content_ok is False

    # ── the caller can correct it ───────────────────────────────────────

    def test_encoding_argument_overrides_detection(self, tmp_path):
        from dhole_mcp.parse import parse_file

        path = self._write(tmp_path, "latin.csv", "café,naïve\nrésumé,30\n", "latin-1")
        content, error = parse_file(str(path), "latin-1")
        assert error == ""
        assert "café" in content and "naïve" in content

    def test_a_lying_meta_charset_falls_through_to_detection(self, tmp_path):
        """The document says utf-8; the bytes are GBK. The declaration is a
        hint, so a failed strict decode hands over to detection instead of
        producing replacement soup."""
        pytest.importorskip("trafilatura")
        from dhole_mcp.parse import parse_file_detailed

        html = ("<html><head><meta charset='utf-8'></head><body>"
                "<p>中文正文内容，验证解码。</p></body></html>")
        path = self._write(tmp_path, "liar.html", html, "gbk")
        content, error, extras = parse_file_detailed(str(path))
        assert error == "" and "中文正文内容" in content
        assert extras["encoding"] == "gb18030"

    def test_the_encoding_argument_is_on_the_wire(self):
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        props = tools["parse"]["inputSchema"]["properties"]
        assert "encoding" in props, "客户端看不到 encoding，就无法从乱码里恢复"
        assert "encoding" in tools["parse"]["description"]

    def test_the_decoder_needs_no_fetch_stack(self):
        """parse.py promises to work without the [all] extra, and its fallback
        path used to import dhole_mcp.fetcher - which imports primp. Pinned
        structurally because the import only fires on the rare damaged-decode
        path, so an in-process test would pass on a machine that has primp."""
        import ast

        from dhole_mcp import charset as charset_mod

        tree = ast.parse(Path(charset_mod.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "dhole_mcp" not in imported, (
            f"charset.py 不能依赖项目内模块（否则 parse 会拖进 primp）: {imported}"
        )


class TestCrawlPathScopeIsASubtree:
    """report BUG-1 (P0): path_include/path_exclude were applied as raw string
    prefixes (`path.startswith(p)`), which failed in four ways - all of them
    silent. Measured on one link set before the rewrite:

      path_include=["docs"]    -> 0 links kept (URL paths start with "/")
      path_include=["/docs/*"] -> 0 links kept (a glob is not a prefix)
      path_include=["/docs"]   -> kept /docs-old/legacy and /docsomething
      path_exclude=["docs"]    -> excluded nothing

    A crawl that returns fewer pages than the site has, for a reason the caller
    cannot see, is the failure this locks down. The contract now is a path
    subtree, matched at a segment boundary.
    """

    def test_a_slashless_pattern_keeps_the_subtree(self, monkeypatch):
        """The reported symptom: the most natural spelling scoped the whole
        crawl away. It used to keep 0 of 3 links, i.e. crawl only the start URL.
        """
        result = _stub_crawl(monkeypatch, path_include=["a"])
        assert result.pages_crawled == 2, "path_include=['a'] 又变成空爬了"
        assert any(p.url.endswith("/a") for p in result.pages)

    def test_every_spelling_of_one_subtree_agrees(self, monkeypatch):
        spellings = ["a", "/a", "/a/", "/a/*"]
        counts = {s: _stub_crawl(monkeypatch, path_include=[s]).pages_crawled
                  for s in spellings}
        assert len(set(counts.values())) == 1, f"同一子树的不同写法结果不一致: {counts}"
        assert counts["a"] == 2

    def test_a_slashless_exclude_pattern_excludes(self, monkeypatch):
        """The no-op direction: `path_exclude=["docs"]` excluded nothing, so the
        caller believed a crawl was scoped that had not been."""
        result = _stub_crawl(monkeypatch, path_exclude=["a"])
        assert result.pages_crawled == 3
        assert not any(p.url.endswith("/a") for p in result.pages)

    # ── the segment boundary, at unit level ─────────────────────────────

    def test_a_shared_prefix_is_not_a_subtree(self):
        from dhole_mcp.crawl import path_allowed

        for path in ("/docs", "/docs/", "/docs/x", "/docs/x/y"):
            assert path_allowed(path, ["/docs"]), f"{path} 应该在 /docs 子树里"
        for path in ("/docs-old", "/docs-old/legacy", "/docsomething", "/doc"):
            assert not path_allowed(path, ["/docs"]), f"{path} 只是前缀相同，不在 /docs 子树里"

    def test_root_and_star_mean_everything(self):
        from dhole_mcp.crawl import path_allowed

        for pattern in ("/", "*", "/*"):
            assert path_allowed("/anything/at/all", [pattern]), f"{pattern} 应表示全部"

    def test_exclude_wins_over_include(self):
        from dhole_mcp.crawl import path_allowed

        assert not path_allowed("/docs/x", ["/docs"], ["/docs/x"])

    def test_the_sitemap_path_uses_the_same_decision(self):
        """The sitemap map carried the identical `startswith` defect - the two
        call sites are now one implementation."""
        from dhole_mcp.crawl import _sitemap_passes_filters

        assert _sitemap_passes_filters("/docs/x", ["/docs"], None)
        assert not _sitemap_passes_filters("/docs-old/x", ["/docs"], None)
        assert not _sitemap_passes_filters("/docs/x", None, ["docs"])

    # ── a pattern the matcher cannot honour must not be silent ──────────

    def test_an_unsupported_glob_fails_the_call(self, monkeypatch):
        """A mid-path glob used to match nothing, so the caller saw an empty
        crawl instead of a bad pattern. Fail fast, before any fetching."""
        result = _stub_crawl(monkeypatch, path_include=["/api/*/v1"])
        assert result.pages_crawled == 0
        assert result.error.startswith("invalid path filter")
        assert "'/api/*/v1'" in result.error
        assert "path_include" in result.next_action, "下一步必须说清怎么改"

    def test_a_non_string_pattern_fails_the_call(self, monkeypatch):
        result = _stub_crawl(monkeypatch, path_exclude=[123])
        assert result.pages_crawled == 0
        assert "must be strings" in result.error

    def test_an_empty_pattern_fails_the_call(self, monkeypatch):
        """An empty string is not a scoping instruction; silently ignoring it
        would let a caller believe a filter was applied."""
        result = _stub_crawl(monkeypatch, path_include=[""])
        assert result.pages_crawled == 0
        assert "empty" in result.error

    def test_a_bad_pattern_does_not_claim_a_network_failure(self, monkeypatch):
        """The generic classify hint says "try a different source", which is
        nonsense here: nothing was fetched."""
        result = _stub_crawl(monkeypatch, path_include=["/api/*/v1"])
        assert "different source" not in result.next_action

    def test_the_options_bag_route_behaves_identically(self, monkeypatch):
        """The options bag is the documented home for path_include, and it
        reaches smart_crawl through a different promotion path than the
        top-level key - so both routes need exercising, not just one."""
        seen: dict = {}

        async def fake_smart_crawl(self, **kwargs):
            seen.update(kwargs)
            from dhole_mcp.crawl import CrawlResponseModel
            return CrawlResponseModel(start_url="https://example.com/", pages=[])

        monkeypatch.setattr(server_mod.MasterFetchServer, "smart_crawl", fake_smart_crawl)
        asyncio.run(MasterFetchServer()._dispatch("smart_crawl", {
            "url": "https://example.com/", "options": {"path_include": ["/docs"]},
        }))
        assert seen["path_include"] == ["/docs"]

    def test_the_wire_description_names_the_subtree_rule(self):
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        desc = tools["smart_crawl"]["inputSchema"]["properties"]["options"]["description"]
        assert "path subtree" in desc
        assert "NOT /docs-old" in desc, "描述必须点明前缀相同的兄弟目录不算"


class TestContentBudgetArgIsHonouredOrRejected:
    """report P1-5: ``max_content_chars`` silently ignored the caller's value.

    The old code was ``if not isinstance(v, int): v = MAX_CONTENT_CHARS``. Several
    MCP clients stringify numbers - the exact behaviour ``_coerce_options`` was
    written for - so ``max_content_chars="2000"`` silently became 40,000: a 20x
    context overspend reported as an ordinary 200. ``offset`` had the sibling
    defect that a negative value slices from the END (``text[-5:]`` returns the
    last 5 chars), i.e. a wrong result also delivered as an ordinary 200.

    Both are the "looks like it worked" family the project closes everywhere
    else, so the policy is: coerce what parses, raise on what cannot be an int,
    clamp only where the bound is a documented resource cap.
    """

    def _call(self, **args):
        return asyncio.run(MasterFetchServer()._dispatch("smart_fetch", args))

    @staticmethod
    def _no_request(monkeypatch) -> list:
        """Fail the test if any request is attempted."""
        calls: list = []

        async def fake_get(url, **kwargs):
            calls.append(url)
            raise AssertionError(f"a request was attempted for {url}")

        monkeypatch.setattr(server_mod.MasterFetchServer, "get",
                            staticmethod(fake_get))
        return calls

    # ─── policy, at the unit level ───────────────────────────────────

    @pytest.mark.parametrize("value,expected", [
        (None, 40000),          # unset -> documented default
        ("2000", 2000),         # stringified number -> honoured, not defaulted
        ("  2000  ", 2000),     # clients pad
        (2000, 2000),
        (100, 500),             # below the floor -> clamped
        (999999, 200000),       # above the ceiling -> clamped
    ])
    def test_coerces_and_clamps(self, value, expected):
        assert server_mod._coerce_int_arg(
            value, "max_content_chars", lo=500, hi=200000,
            default=40000) == expected

    @pytest.mark.parametrize("value", [True, False, "abc", "2.5", 2.5, [2000],
                                       {"v": 2000}])
    def test_raises_on_what_cannot_be_an_int(self, value):
        """``True`` is an ``int`` subclass - without the bool guard it would
        silently mean 1 character."""
        with pytest.raises(ValueError):
            server_mod._coerce_int_arg(value, "max_content_chars", lo=500,
                                       hi=200000, default=40000)

    def test_clamp_can_be_turned_off_for_nonsense_bounds(self):
        """A negative offset has no sensible reading, so it raises rather than
        being quietly corrected to 0."""
        with pytest.raises(ValueError, match="between 0 and"):
            server_mod._coerce_int_arg(-1, "offset", lo=0, hi=2 ** 31,
                                       default=0, clamp=False)

    # ─── wiring: the rejection travels the normal contract ───────────

    @pytest.mark.parametrize("args,needle", [
        ({"url": "https://example.com/", "max_content_chars": "abc"},
         "max_content_chars"),
        ({"url": "https://example.com/", "max_content_chars": True},
         "max_content_chars"),
        ({"url": "https://example.com/", "offset": -1}, "offset"),
    ])
    def test_a_bad_value_is_rejected_before_any_request(self, monkeypatch, args,
                                                        needle):
        calls = self._no_request(monkeypatch)
        result = self._call(**args)[1]
        assert calls == [], "参数被拒绝时不该发出任何请求"
        assert result["status"] == 0
        assert result["error"].startswith("invalid_request:")
        assert needle in result["error"]
        # 参数错误必须给出对得上的下一步，不能复用「传绝对 URL」那句
        assert needle in result["next_action"]
        assert "absolute http(s) URL" not in result["next_action"]

    def test_the_rejection_does_not_claim_the_url_was_wrong(self, monkeypatch):
        """The default next_action is URL advice; a param error that reused it
        would send the caller to fix the one argument that was already right."""
        self._no_request(monkeypatch)
        result = self._call(url="https://example.com/",
                            max_content_chars="abc")[1]
        assert "no request was made" in result["next_action"]

    def test_a_stringified_number_reaches_the_fetch(self, monkeypatch):
        """The coercion half: ``"2000"`` must get past normalization (it used to
        be silently replaced, which also got past - so the point is that the
        value is honoured, which the unit test above pins)."""
        sentinel = _Stop()

        async def fake_get(url, **kwargs):
            raise sentinel

        monkeypatch.setattr(server_mod.MasterFetchServer, "get",
                            staticmethod(fake_get))
        with pytest.raises(_Stop):
            self._call(url="https://example.com/", max_content_chars="2000")

    # ─── the documented bound ────────────────────────────────────────

    def test_the_wire_documents_the_real_range(self):
        """The 200000 ceiling is a deliberate clamp, so it has to be stated -
        otherwise an agent asking for 500000 cannot tell why it got 200000."""
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        desc = tools["smart_fetch"]["inputSchema"]["properties"]["max_content_chars"]["description"]
        assert "200000" in desc
        assert "500" in desc


class TestScreenshotErrorsAreActionableNotRawDriverLogs:
    """P1-3: ``screenshot`` did ``raise captured["error"]`` verbatim, so the
    agent received patchright's multi-line dump - a headline plus a "Call log:"
    block of driver internals ("waiting for fonts to load"). None of it names an
    argument the caller could change, and the whole reason an ``is_error``
    result is worth returning is that the caller can decide what to do next.
    """

    @staticmethod
    def _msg(exc: BaseException) -> str:
        return server_mod._screenshot_error_message(exc)

    def test_the_call_log_never_reaches_the_agent(self):
        exc = Exception(
            "Page.screenshot: Timeout 30000ms exceeded.\n"
            "Call log:\n"
            "  - taking page screenshot\n"
            "    - waiting for fonts to load...\n"
            "    - fonts loaded\n")
        out = self._msg(exc)
        assert "Call log" not in out
        assert "fonts" not in out
        assert "taking page screenshot" not in out

    def test_the_headline_survives(self):
        """Dropping the log must not drop the one line that names the failure."""
        out = self._msg(Exception(
            "Page.screenshot: Timeout 30000ms exceeded.\nCall log:\n  - x\n"))
        assert "Timeout 30000ms exceeded" in out

    @pytest.mark.parametrize("raw,needle", [
        ("Page.screenshot: Timeout 30000ms exceeded.", "timeout"),
        ("Target page, context or browser has been closed", "close_session"),
        ("browserType.launch: Executable doesn't exist at /x/chrome",
         "playwright install"),
    ])
    def test_each_known_failure_names_the_knob_that_fixes_it(self, raw, needle):
        assert needle in self._msg(Exception(raw))

    def test_an_unrecognised_failure_still_says_something(self):
        out = self._msg(Exception(
            "Protocol error (Page.captureScreenshot): Internal error"))
        assert "Protocol error" in out
        assert len(out) > 40

    def test_the_exception_type_is_kept(self):
        """The type is the cheapest signal the caller has; dropping it makes a
        timeout indistinguishable from a protocol error."""
        assert self._msg(TimeoutError("x")).startswith("TimeoutError:")

    def test_an_empty_message_does_not_crash(self):
        """``Exception()`` stringifies to ``""``. Without the fallback the type
        name would be the only content and the line would read
        ``"Exception:  - call again"`` - a blank field in the middle of the one
        sentence the agent gets. ``startswith("Exception:")`` alone does not
        catch that, hence the double-space check."""
        out = self._msg(Exception())
        assert out.startswith("Exception:")
        assert "(no message)" in out
        assert "  " not in out

    def test_a_runaway_headline_is_bounded(self):
        assert len(self._msg(Exception("E" * 5000))) <= 300

    def test_every_shape_fits_the_transport_cap(self):
        """``call_tool`` sends ``str(e)[:300]``. A message past that arrives cut
        mid-sentence - an unactionable error that still looks like a complete
        one, which is exactly the failure mode this fix exists to remove."""
        shapes = [
            Exception("Page.screenshot: Timeout 30000ms exceeded.\nCall log:\n"
                      + "  - step\n" * 40),
            Exception("Target page, context or browser has been closed"),
            Exception("E" * 5000),
            Exception(),
        ]
        for exc in shapes:
            assert len(self._msg(exc)) <= 300, repr(exc)

    def test_the_hint_reads_the_headline_only(self):
        """A call log mentioning "timeout" under a headline that is not a
        timeout must not produce timeout advice - a confidently wrong next step
        is worse than no next step."""
        exc = Exception(
            "Protocol error (Page.captureScreenshot): Internal error\n"
            "Call log:\n"
            "  - retrying after timeout\n")
        out = self._msg(exc)
        assert "options.timeout" not in out
        assert "Protocol error" in out

    def test_the_dispatched_error_is_the_sanitised_one(self, monkeypatch):
        """End to end through ``_dispatch``: the raw dump used to be what
        ``call_tool`` stringified into the ``is_error`` payload."""

        class _Timeout(Exception):
            pass

        class _Page:
            url = "https://example.com/"

            async def screenshot(self, **kwargs):
                raise _Timeout(
                    "Page.screenshot: Timeout 30000ms exceeded.\n"
                    "Call log:\n"
                    "  - taking page screenshot\n"
                    "    - waiting for fonts to load...\n")

        class _Session:
            _is_alive = True

            async def fetch(self, url, **kwargs):
                await kwargs["page_action"](_Page())

        entry = server_mod._SessionEntry(session=_Session(), session_type="stealthy")

        async def fake_get_session(self, session_id, expected_type):
            return entry

        async def fake_ensure(self, **kw):
            return "auto"

        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_get_session",
                            fake_get_session)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_ensure_auto_session",
                            fake_ensure)

        with pytest.raises(RuntimeError) as ei:
            asyncio.run(MasterFetchServer()._dispatch(
                "screenshot", {"url": "https://example.com/"}))
        text = str(ei.value)
        assert "Call log" not in text
        assert "fonts" not in text
        assert "Timeout 30000ms exceeded" in text
        assert "options.timeout" in text
