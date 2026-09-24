"""Regressions for the document-download timeout path (external report P2, v15.2).

The report filed "large PDF: 30s and 60s both fail" as a server-side limit. It was
a dhole bug with a specific shape: ``_adaptive_timeout`` learned a per-domain
latency from PAGE responses and applied it as a ceiling to every request on that
host, so after one fast fetch of ``example.com/`` a 40MB ``example.com/big.pdf``
had five seconds — and because the learned term was taken through a ``min()``,
``timeout=60000`` could not lift it. A successful document was never recorded
either (the `.pdf` early-return sat ABOVE ``_record_latency``), so that EMA could
only ever be pushed down by HTML pages. On the forced path a literal
``min(timeout/1000, 30)`` did the same job again, while the timeout message told
the caller to raise a timeout the code was ignoring.

What is pinned here, in the order the fixes land:

* pages and documents are two facts about a host, learned and priced separately
* the caller's budget is the contract — the tier may neither undercut nor overstate it
* retries are counted against the budget that is left, and a document takes one
  attempt because a retry restarts its download from byte zero
* a body that cannot fit ``MAX_RESPONSE_BYTES`` is refused by a size preflight,
  instead of being downloaded until the clock runs out and called a timeout
* a document is recognised by WHAT CAME BACK, not only by the URL — arxiv serves
  PDFs at ``/pdf/<id>``, and classing those as pages puts a 26s download in the
  page table (that one is measured, not theorised: it survived into the first
  live run of this change)
* the advice names the size and the budget it needs, and stops blaming the browser
"""

import asyncio

import pytest

import dhole_mcp.fetcher as fetcher
from dhole_mcp import server as server_mod
from dhole_mcp.server import (
    MAX_RESPONSE_BYTES,
    MasterFetchServer,
    ResponseModel,
    _document_budget_advice,
    _http_tier_budget,
    _is_document_url,
    _record_latency,
)


PAGE_URL = "https://docs.test/guide"
DOC_URL = "https://docs.test/manual.pdf"


@pytest.fixture(autouse=True)
def _clean_latency_tables():
    """The latency tables are process-global; no test inherits another's host intel."""
    server_mod._DOMAIN_LATENCY.clear()
    server_mod._DOMAIN_DOC_LATENCY.clear()
    yield
    server_mod._DOMAIN_LATENCY.clear()
    server_mod._DOMAIN_DOC_LATENCY.clear()


class _ProbeResponse:
    """The two attributes _document_size reads off a primp response."""

    def __init__(self, headers=None):
        self.headers = headers or {}
        self.status_code = 206 if "content-range" in (headers or {}) else 200


# ─── A/B: two classes of request, learned and priced separately ─────────────


class TestDocumentsAreNotPricedByPageHabit:

    def test_a_fast_page_history_no_longer_gives_the_pdf_five_seconds(self):
        """The report's exact shape: 0.8s of page habit, 5s for a 40MB download."""
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0

        page_s, page_retries = _http_tier_budget(PAGE_URL, 60000, 60.0)
        doc_s, doc_retries = _http_tier_budget(DOC_URL, 60000, 60.0, document=True)

        assert (page_s, page_retries) == (5, 3), "a host that answers in 0.8s should not hold a page call open"
        assert (doc_s, doc_retries) == (60, 0), "and that habit must not price a download"

    def test_a_document_takes_one_attempt_because_a_retry_restarts_it(self):
        """Four attempts of 15s cannot deliver a file that needs 60s, ever; one
        attempt of 60s can. The caller retries the call if it was a blip."""
        _s, retries = _http_tier_budget(DOC_URL, 60000, 60.0, document=True)
        assert retries == 0

    def test_pages_keep_the_attempt_count_that_fits_the_budget_left(self):
        """Each attempt stays as long as the host needs; only the COUNT is what
        the budget affords. Shrinking an attempt to buy more of them would fail a
        slow-but-healthy site to insure against a stalled one."""
        assert _http_tier_budget(PAGE_URL, 60000, 60.0) == (60, 0)   # one attempt already fills the call
        assert _http_tier_budget(PAGE_URL, 30000, 30.0) == (30, 0)   # 4x30 would not fit 30 -> one try
        assert _http_tier_budget(PAGE_URL, 6000, 6.0) == (6, 0)
        assert _http_tier_budget(PAGE_URL, 1500, 1.5) == (1, 0)
        # A host learned to answer in 0.8s keeps its four short attempts, because
        # they fit: 4 x 5s inside the 60s that is left.
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0
        assert _http_tier_budget(PAGE_URL, 60000, 60.0) == (5, 3)

    def test_the_tier_never_asks_for_more_wall_time_than_is_left(self):
        for left in (1.5, 5, 8, 20, 31, 90, 120):
            for document in (False, True):
                per_attempt, retries = _http_tier_budget(
                    DOC_URL if document else PAGE_URL, left * 1000, left,
                    document=document)
                assert per_attempt * (retries + 1) <= left + 1, (left, document)

    def test_a_successful_document_does_not_inflate_the_page_budget(self):
        """The other direction of the same contamination: 40s of PDF is not 40s of HTML."""
        _record_latency(DOC_URL, 40000.0, document=True)

        assert server_mod._DOMAIN_DOC_LATENCY.get("docs.test") == pytest.approx(40000.0)
        assert server_mod._DOMAIN_LATENCY == {}
        assert server_mod._adaptive_timeout(PAGE_URL, 30000) == 30000

    def test_the_document_predicate_covers_what_the_rules_assume(self):
        assert _is_document_url("https://x.test/a/REPORT.PDF?v=3")
        assert not _is_document_url("https://x.test/report?file=a.pdf")
        assert not _is_document_url("https://x.test/report")
        assert not _is_document_url("not a url at all")


# ─── tier 1, through the real escalation code ───────────────────────────────


def _server_with_captured_get(result=None):
    """A server whose HTTP tier records its arguments instead of going out."""
    seen: dict = {}

    async def capture_get(*args, **kwargs):
        seen.update(kwargs)
        url = args[0] if args else kwargs.get("url", "")
        return result or ResponseModel(url=url, status=200, content=["text"],
                                       content_type="text/html")

    srv = MasterFetchServer()
    srv.get = capture_get
    return srv, seen


async def _escalate(srv, url, timeout_ms, body_size=None):
    """Run tier 1 with the network replaced: TCP preflight, size probe, HTTP."""

    async def fake_http_get(u, **kw):
        if body_size is None:
            return _ProbeResponse({})
        return _ProbeResponse({"content-range": f"bytes 0-0/{body_size}"})

    orig_preflight, orig_get = fetcher.tcp_preflight, fetcher.http_get
    fetcher.tcp_preflight = lambda url, timeout=2.0: (True, "")
    fetcher.http_get = fake_http_get
    try:
        return await srv._auto_escalate(
            url, "markdown", None, True, True, 0, 0,
            True, False, 0, None, timeout_ms, False, False, False, False,
            None, None, None,
        )
    finally:
        fetcher.tcp_preflight, fetcher.http_get = orig_preflight, orig_get


class TestTierOneAsksForTheRightBudget:

    @pytest.mark.asyncio
    async def test_a_pdf_asks_for_the_callers_budget_not_the_learned_five_seconds(self):
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0
        srv, seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000)

        assert seen["timeout"] >= 55, "the clock has spent microseconds; the point is it is not 5"
        assert seen["retries"] == 0

    @pytest.mark.asyncio
    async def test_a_page_still_gets_the_cheap_timeout_and_its_retries(self):
        server_mod._DOMAIN_LATENCY["docs.test"] = 800.0
        srv, seen = _server_with_captured_get()

        await _escalate(srv, PAGE_URL, 60000)

        assert seen["timeout"] == 5
        assert seen["retries"] == 3

    @pytest.mark.asyncio
    async def test_a_successful_pdf_is_recorded_as_a_document(self):
        srv, _seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000)

        assert server_mod._DOMAIN_DOC_LATENCY.get("docs.test") is not None
        assert server_mod._DOMAIN_LATENCY == {}, "the download must not become page history"

    @pytest.mark.asyncio
    async def test_an_extensionless_pdf_is_recorded_as_a_document_too(self):
        """arxiv serves PDFs at /pdf/<id>, so the URL alone cannot class them.

        Measured on a live run of this change: such a fetch took 26s and went into
        the PAGE table, which is the same contamination in the direction the URL
        predicate cannot see.
        """
        pdf = ResponseModel(url="https://arxiv.org/pdf/2103.00020", status=200,
                            content=["x"], content_type="application/pdf")
        srv, _seen = _server_with_captured_get(pdf)

        await _escalate(srv, "https://arxiv.org/pdf/2103.00020", 60000)

        assert server_mod._DOMAIN_DOC_LATENCY.get("arxiv.org") is not None
        assert server_mod._DOMAIN_LATENCY == {}


# ─── E: the size preflight, which is what the body cap should have been ──────


class TestOversizedDocumentIsRefusedBeforeItDownloads:

    @pytest.mark.asyncio
    async def test_a_body_over_the_cap_never_starts(self):
        """The cap used to be checked once the body was already in memory, so a
        200MB PDF was paid for at length and reported as a timeout."""
        srv, seen = _server_with_captured_get()

        out = await _escalate(srv, DOC_URL, 60000, body_size=200 * 1024 * 1024)

        assert seen == {}, "no document request may go out for a body that cannot fit"
        assert "Response body too large" in out.error
        assert "200.0MB" in out.error
        assert f"{MAX_RESPONSE_BYTES:,} bytes" in out.error
        assert "no timeout or fetcher changes that" in out.next_action
        assert "size preflight" in out.escalation_path

    @pytest.mark.asyncio
    async def test_a_body_that_fits_is_downloaded_normally(self):
        srv, seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000, body_size=8 * 1024 * 1024)

        assert seen["timeout"] >= 55

    @pytest.mark.asyncio
    async def test_a_host_that_wont_state_a_size_is_not_guessed_at(self):
        """Refusing on an unknown size would turn every chunked-response host into
        a failure. The probe is a chance to know, not a permission."""
        srv, seen = _server_with_captured_get()

        await _escalate(srv, DOC_URL, 60000, body_size=None)

        assert seen["timeout"] >= 55


# ─── F: advice the caller can act on ────────────────────────────────────────


class TestDocumentTimeoutAdviceIsTrue:

    @pytest.mark.asyncio
    async def test_the_advice_names_the_file_and_not_the_browser(self):
        """The generic sentence recommended force_fetcher='http' "to skip browser
        rendering" for a URL that never went near a browser."""
        srv, _seen = _server_with_captured_get()

        async def stalling_get(*args, **kwargs):
            await asyncio.sleep(5)
            return ResponseModel(url=DOC_URL, status=200, content=[])

        srv.get = stalling_get
        out = await _escalate(srv, DOC_URL, 1000, body_size=40 * 1024 * 1024)

        assert out.error.startswith("timeout: the ")
        assert "the file is 40.0MB" in out.next_action
        assert "raise timeout" in out.next_action
        assert "EXTRACTED, not what gets downloaded" in out.next_action
        assert "skip browser rendering" not in out.next_action

    def test_a_call_already_at_the_ceiling_is_not_told_to_raise_it_higher(self):
        """Dangling a raise the caller cannot make sends them round a loop that
        cannot terminate."""
        advice = _document_budget_advice(DOC_URL, None, "", 120000, "HTTP tier")

        assert "already the maximum budget" in advice
        assert "raise timeout" not in advice

    def test_a_host_that_couldnt_state_the_size_says_so(self):
        advice = _document_budget_advice(DOC_URL, None, "host reported no size (chunked)",
                                         30000, "HTTP tier")

        assert "chunked" in advice
        assert "raise timeout" in advice

    @pytest.mark.asyncio
    async def test_a_document_that_never_arrived_names_its_size_too(self):
        """primp reports a stalled download as a failed RESPONSE (status 0), which
        is not the over-budget result — so the tier's advice has to reach that
        path as well, or the common real failure still gets the generic sentence."""
        srv, _seen = _server_with_captured_get(ResponseModel(url=DOC_URL, status=0, content=[]))

        out = await _escalate(srv, DOC_URL, 30000, body_size=8 * 1024 * 1024)

        assert "the file is 8.0MB" in out.next_action
        assert "skip browser rendering" not in out.next_action

    @pytest.mark.asyncio
    async def test_a_pdf_that_arrived_keeps_its_own_advice(self):
        """A file that downloaded fine but extracted badly is a fact about its
        contents, not the clock — the size advice must not swallow the OCR one."""
        scanned = ResponseModel(url=DOC_URL, status=200, content=["[PDF: 12 pages]"],
                                content_type="application/pdf",
                                error="scanned_pdf: no extractable text layer")
        srv, _seen = _server_with_captured_get(scanned)

        out = await _escalate(srv, DOC_URL, 30000, body_size=3 * 1024 * 1024)

        assert "OCR" in out.next_action
        assert "the file is" not in out.next_action


# ─── C: the forced tier, same bug through a different door ──────────────────


class TestForcedHttpTierRespectsTheBudget:

    @pytest.mark.asyncio
    async def test_the_literal_30_second_ceiling_is_gone(self):
        """`min(timeout/1000, 30)` meant timeout=90000 bought thirty seconds."""
        srv, seen = _server_with_captured_get()
        orig_get = fetcher.http_get
        fetcher.http_get = lambda u, **kw: _ProbeResponse({})
        try:
            await srv._force_fetch(
                DOC_URL, "http", "markdown", None, True, True, 0, 0,
                True, False, 0, None, 90000, False, False, False, False,
                None, None, None,
            )
        finally:
            fetcher.http_get = orig_get

        assert seen["timeout"] == 90
        assert seen["retries"] == 0


# ─── G: the facts that used to live only in the code ────────────────────────


class TestTheWireStatesTheBudgetIsForBytes:

    @pytest.fixture()
    def props(self):
        tool = next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == "smart_fetch")
        return tool["inputSchema"]["properties"]

    def test_timeout_says_a_document_spends_it_on_the_download(self, props):
        assert "DOWNLOAD" in props["timeout"]["description"]

    def test_pages_no_longer_promises_to_save_time(self, props):
        """It was advertised as saving "tokens/time on big PDFs". It saves tokens;
        the file still arrives whole."""
        desc = props["pages"]["description"]

        assert "EXTRACTED, not what is downloaded" in desc
        assert "saves tokens/time" not in desc.lower()
