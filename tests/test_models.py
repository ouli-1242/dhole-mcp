"""test_models.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

from dhole_mcp import context
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MIN_CHUNK_CHARS, _apply_chunking
import pytest
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import context as CT
from dhole_mcp import server as SV
import asyncio
from dhole_mcp import crawl as crawl_mod


# ============================================================================
# 来源：test_a_named_cap_that_is_over_taken_says_by_how_much.py
# ============================================================================
# 点了帽、实际给的比帽多 —— 这一封回执说得出「多了多少、为什么多、没丢东西」。

UNIT = "这是第{i}句，用来说明这一页的长度是怎么排布的，好让切口找得到界。"


def _page(total_len: int) -> str:
    return "".join(UNIT.format(i=i) for i in range(400))[:total_len]


def _read(total_len: int, cap: int, offset: int = 0, named: int | None = None):
    """走真 `_apply_chunking`；只把「调用方有没有点名」这一件事交给注记通道。"""
    result = ResponseModel(url="https://e.test/collections", status=200,
                           content=[_page(total_len)], extracted_type="markdown")
    token = context._MAX_CHARS_NAMED.set(named)
    try:
        with context._arg_notes_scope() as notes:
            out = _apply_chunking(result.model_copy(), max_chars=cap, offset=offset)
        return out, list(notes)
    finally:
        context._MAX_CHARS_NAMED.reset(token)


def _body(out) -> str:
    return "".join(out.content or [])


# ─── 1 · 画像那一发：点了 1200，给了 1446 ─────────────────────

def test_a_named_cap_that_was_over_taken_says_so():
    out, notes = _read(1446, 1200, named=1200)
    assert len(_body(out)) == 1446, "并回本身不许被改掉：交付的字节一个都不能少"
    line = " · ".join(notes)
    assert any("max_content_chars" in n for n in notes), (
        f"点了帽却被超过，回执一个字没提：notes={notes} summary={out.summary!r}")
    assert "1200" in line and "246" in line, line


def test_the_over_number_is_the_real_one_not_the_floor():
    """句子里那颗数是**这次**超出量（246），不是地板（500）——地板是原因，不是账。"""
    out, notes = _read(1446, 1200, named=1200)
    real = len(_body(out)) - 1200
    line = " · ".join(notes)
    assert f"{real:,} char" in line, (real, line)
    assert f"{MIN_CHUNK_CHARS:,} char" not in line or real == MIN_CHUNK_CHARS, line


def test_it_says_nothing_was_dropped_and_there_is_no_next_page():
    out, notes = _read(1446, 1200, named=1200)
    line = " · ".join(notes)
    assert out.is_truncated is False, "并回这一支不许假报分页"
    assert out.next_offset == 0, "没有下一页就不许留下 offset"
    assert "no next offset" in line, line
    assert "dropped" in line, line


def test_the_announcement_lands_on_the_line_every_caller_reads():
    """注记桶是内部形状，`summary` 才是客户端每封都读的那一行。"""
    out, _notes = _read(1446, 1200, named=1200)
    assert "max_content_chars" in (out.summary or ""), out.summary


# ─── 2 · 沉默的那几格：没点名的帽、没被超过的帽 ────────────────

def test_a_cap_nobody_named_stays_silent():
    """默认预算不是调用方的指示：超过了也没什么「指示被执行成什么样」可回答。
    （13,490 的页 / 13,000 的默认档 → 并回 490 个字符，与点名那一发同形。）"""
    _out, notes = _read(13490, 13000, named=None)
    assert not any("max_content_chars" in n for n in notes), notes


def test_a_page_shorter_than_the_named_cap_says_nothing():
    _out, notes = _read(500, 600, named=600)
    assert not any("max_content_chars" in n for n in notes), notes


def test_a_page_exactly_as_long_as_the_named_cap_says_nothing():
    _out, notes = _read(600, 600, named=600)
    assert not any("max_content_chars" in n for n in notes), notes


# ─── 3 · 边界：地板、地板 +1、一个字符 ────────────────────────

def test_the_boundary_at_the_floor_merges_and_declares():
    out, notes = _read(1100, 600, named=600)
    assert len(_body(out)) == 1100, out.next_offset
    assert out.is_truncated is False
    assert any("max_content_chars" in n for n in notes), notes


def test_one_char_past_the_floor_paginates_and_does_not_double_book():
    """正常分页那一支已经有自己的 167 字符说明；超帽句不许再挤进来。"""
    out, notes = _read(1101, 600, named=600)
    assert out.is_truncated is True
    assert out.truncation_note, "分页那一支的原有说明不许退化"
    assert not any("max_content_chars" in n for n in notes), notes


def test_the_smallest_over_run_names_one_char():
    out, notes = _read(601, 600, named=600)
    line = " · ".join(notes)
    assert len(_body(out)) - 600 == 1
    assert "1 char" in line and "1 chars" not in line, line


# ─── 4 · 续页：报的是这一片的超出量 ───────────────────────────

def test_a_continuation_page_that_merges_the_tail_declares_it_too():
    out, notes = _read(1446, 600, offset=600, named=600)
    delivered = len(_body(out))
    line = " · ".join(notes)
    assert delivered == 846, delivered
    assert f"{delivered - 600:,} char" in line, (delivered, line)


# ─── 5 · 一句话的作者只有一位 ─────────────────────────────────

def test_the_sentence_has_one_author():
    """句子的拼法住在 `context._over_cap_note`（与 `_timeout_floor_note` 同处）：
    `_apply_chunking` 只交出两个数。回执里那句就是这位亲手拼出来的那一句。"""
    said = context._over_cap_note(named=1200, delivered=1446, floor=MIN_CHUNK_CHARS)
    assert said, "作者不能交回空句"
    assert said.startswith("max_content_chars=1200"), said
    assert "246" in said and "500" in said, said
    _out, notes = _read(1446, 1200, named=1200)
    assert said in notes, (notes, said)
    assert context._over_cap_note(named=None, delivered=1446, floor=MIN_CHUNK_CHARS) == ""
    assert context._over_cap_note(named=1200, delivered=1200, floor=MIN_CHUNK_CHARS) == ""
    assert context._over_cap_note(named=1200, delivered=1100, floor=MIN_CHUNK_CHARS) == ""


# ─── 6 · 手册里要有这两种形状 ────────────────────────────────

def test_the_manual_carries_the_shape():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "max_content_chars=" in corpus
    assert "no next offset" in corpus, (
        "「比帽多但不是丢了」这个形状没进手册：读者只能把它当成又一次截断")


# ============================================================================
# 来源：test_paired_arguments_are_refused_in_both_directions.py
# ============================================================================
# 成对参数的**反向**组合也要具名拒绝，不许静默忽略。

@pytest.fixture
def server(monkeypatch):
    """一个**一个包都发不出**的 server：这些拒绝必须发生在取数之前。

    绊线挂在 `validate_url` 上而不是某个 tier 上：每一条取数路径（HTTP / stealthy /
    archive / bulk）都要先过它，所以"走到这里"就等于"已经打算发请求了" —— 那时
    拒绝就不再是"在门口拦下"，而这条纪律的全部价值恰恰在门口。
    """
    def _never(*a, **kw):  # pragma: no cover - 被调用即失败
        raise AssertionError("参数配对不成立时不该发出任何请求")
    monkeypatch.setattr(server_mod, "validate_url", _never)
    return MasterFetchServer(cache_ttl=0)


def _err(result: ResponseModel) -> str:
    return (result.error or "") + " || " + (result.next_action or "")


@pytest.mark.asyncio
async def test_nested_fields_without_a_selector_is_refused(server):
    r = await server.web_read(
        "https://site.test/", nested_fields={"title": "h1"})
    assert "nested_selector" in _err(r), r.error
    assert "nested_fields" in _err(r), r.error


@pytest.mark.asyncio
async def test_nested_max_without_a_selector_is_refused(server):
    """`nested_max` 单飞最容易被忽略：它看起来只是个上限旋钮。"""
    r = await server.web_read("https://site.test/", nested_max=3)
    assert "nested_selector" in _err(r), r.error
    assert "nested_max" in _err(r), r.error


@pytest.mark.asyncio
async def test_focus_mode_without_focus_is_refused(server):
    """wire 上写着 "Requires focus." —— 实现必须真的拒，否则是合同的两半对不上。"""
    r = await server.web_read("https://site.test/", focus_mode="best")
    assert "focus_mode" in _err(r), r.error
    assert "focus" in _err(r), r.error


@pytest.mark.asyncio
async def test_the_refusal_says_it_was_ignored_not_applied(server):
    """出路要说得清是"被忽略了"，否则调用方会以为自己拿到的是整页抽取结果。"""
    r = await server.web_read("https://site.test/", nested_fields={"title": "h1"})
    assert "ignored" in (r.next_action or ""), r.next_action
    assert "Nothing was fetched" in (r.next_action or ""), r.next_action


@pytest.mark.asyncio
async def test_a_whitespace_focus_still_counts_as_missing(server):
    """`focus="  "` 不是一次聚焦 —— `_FOCUS` 那条路本来就把它当"没给"。"""
    r = await server.web_read("https://site.test/", focus="   ", focus_mode="best")
    assert "focus_mode" in _err(r), r.error


@pytest.mark.asyncio
async def test_the_forward_pairing_is_still_refused_too(server):
    """反向修好了，正向不许被顺手改坏 —— 两个方向是同一条纪律。"""
    r = await server.web_read("https://site.test/", nested_selector=".card")
    assert "nested_fields" in _err(r), r.error


# ── url= 与 urls= 同时给：曾经由实现单方面选边，且注释说的是反话 ──────────────
#
# 真机复现（2026-10-09 体测）：`web_read(url="…/go/exporters",
# urls=["…/collector/configuration"])` 回 `total: 1, successful: 1` —— 只有
# urls 里那条被取，url 那条**静默消失**，error 空、diagnosis 空、summary 与
# 一次正常批量一字不差。而 wire 边上第 8628 行的注释写的是 "url wins"。


@pytest.mark.asyncio
async def test_url_alongside_urls_is_refused_not_resolved(server):
    r = await server.web_read("https://dropped.test/x",
                              urls=["https://kept.test/1",
                                    "https://kept.test/2"])
    assert r.successful == 0
    assert "url=" in _err(r.results[0]) and "urls=" in _err(r.results[0]), r.results[0].error


@pytest.mark.asyncio
async def test_every_named_url_gets_its_own_receipt(server):
    """拒绝也要占满点名的地址：total 报 3 而 results 只有 1 行，就是这一刀
    正在修的那个形状 —— 拿它去"修"静默丢弃，等于换个地方继续丢。"""
    r = await server.web_read("https://dropped.test/x",
                              urls=["https://kept.test/1",
                                    "https://kept.test/2"])
    assert r.total == 3
    assert len(r.results) == r.total, "有一个点名的 URL 没拿到回执"
    assert {row.url for row in r.results} == {
        "https://dropped.test/x", "https://kept.test/1", "https://kept.test/2"}


@pytest.mark.asyncio
async def test_the_refusal_says_neither_name_won(server):
    """出路不能暗示"其中一个其实被读了" —— 那会让调用方继续信那份不存在的结果。"""
    r = await server.web_read("https://dropped.test/x", urls=["https://kept.test/1"])
    action = r.results[0].next_action or ""
    assert "Nothing was fetched" in action or "nothing was fetched" in action
    assert "Neither name won" in action, action


@pytest.mark.asyncio
async def test_a_batch_without_url_still_reaches_the_fetch_layer(server):
    """正向半边：拒绝成对不等于拒绝批量 —— `urls=` 单飞必须照常往下走。

    批量路径把每行的异常**收进 row.error** 而不是往外抛（与单条路径不同），所以
    绊线的证据在行里。断言两件事：走到了取数层，且没有任何一行带着成对拒绝的话。
    没有这一条，"永远拒绝"这种改法也能让上面几条全绿。
    """
    r = await server.web_read("", urls=["https://a.test/1", "https://a.test/2"])
    assert r.total == 2 and len(r.results) == 2
    for row in r.results:
        assert "不该发出任何请求" in (row.error or ""), \
            f"没走到取数层，成对拒绝被顺手扩大成了拒绝批量: {row.error!r}"
        assert "cannot be combined with urls=" not in (row.error or "")


@pytest.mark.asyncio
async def test_a_single_url_still_reaches_the_fetch_layer(server):
    with pytest.raises(AssertionError, match="不该发出任何请求"):
        await server.web_read("https://a.test/1")


# ============================================================================
# 来源：test_a_pdf_only_argument_says_it_was_inert_on_a_page.py
# ============================================================================
# R02-F65 · 只在 PDF 那条腿上的两颗参数，打在页面上时要自己说一声（R02-063 · P3）。

class _FakePage:
    """一颗最小的应答：`_translate_response` 只读这几位。"""

    def __init__(self, body: bytes, ct: str):
        self.status = 200
        self.body = body
        self.raw_body = body
        self.content_type = ct
        self.url = "http://127.0.0.1:8399/whatever"
        self.total_size = len(body)
        self.encoding = "utf-8"
        self.headers = {"content-type": ct}


def _translate(body: bytes, ct: str, *, pages=None, password=None):
    """在申报桶里跑一次翻译，交回（回执, 桶）。"""
    tok_pages = CT._PDF_PAGES.set(pages)
    tok_pw = CT._PDF_PASSWORD.set(password)
    try:
        with CT._arg_notes_scope() as notes:
            got = SV._translate_response_body(_FakePage(body, ct), "markdown", None,
                                              fetcher_used="http", duration_ms=1.0)
    finally:
        CT._PDF_PAGES.reset(tok_pages)
        CT._PDF_PASSWORD.reset(tok_pw)
    return got, list(notes)


HTML = b"<html><body><h1>Report</h1><p>body text</p></body></html>"
PDFISH = b"%PDF-1.4\ntrailer\n%%EOF\n"


# ── 格 1：惰性参数要自报 ─────────────────────────────────────────────────

class TestAnInertArgumentReportsItself:

    def test_pages_on_a_page_is_named_as_inert(self):
        _got, notes = _translate(HTML, "text/html", pages="1-2")
        joined = " ".join(notes).lower()
        assert "pages" in joined, f"参数被收下、没人读过、也没人说：{notes}"

    def test_password_on_a_page_is_named_and_never_echoed(self):
        _got, notes = _translate(HTML, "text/html", password="hunter2")
        joined = " ".join(notes).lower()
        assert "password" in joined, notes
        assert "hunter2" not in joined, "申报不许把口令的值带进回执"

    def test_both_together_are_two_names_in_one_sentence(self):
        _got, notes = _translate(HTML, "text/html", pages="3", password="s3cret")
        joined = " ".join(notes).lower()
        assert "pages" in joined and "password" in joined, notes
        assert "s3cret" not in joined, notes
        assert len([n for n in notes if "pages" in n.lower()]) == 1, notes


# ── 格 2：不许多报（三种不许出现的形状）──────────────────────────────────

class TestItDoesNotClaimWhatDidNotHappen:

    def test_no_argument_no_sentence(self):
        _got, notes = _translate(HTML, "text/html")
        assert " ".join(notes).lower().find("inert") == -1, notes
        assert not any("only apply to a pdf" in n.lower() for n in notes), notes

    def test_the_pdf_leg_stays_quiet_because_the_argument_bit(self):
        # PDF 那条腿：`_extract_pdf_response` 真的读了这两颗，所以不许说"没作用"。
        seen = {}

        def fake_extract(body, raw_ct, total_size, url, extraction_type, fetcher_used,
                         duration_ms):
            seen["pages"] = CT._PDF_PAGES.get()
            seen["password"] = CT._PDF_PASSWORD.get()
            return ResponseModel(url=url, status=200, content=["page 1"],
                                 fetcher_used="http", extracted_type="markdown")

        orig = SV._extract_pdf_response
        SV._extract_pdf_response = fake_extract
        try:
            _got, notes = _translate(PDFISH, "application/pdf", pages="1", password="pw")
        finally:
            SV._extract_pdf_response = orig
        assert seen == {"pages": "1", "password": "pw"}, seen
        assert not any("only apply to a pdf" in n.lower() for n in notes), notes


# ── 格 3：正文一寸不改（对照面）─────────────────────────────────────────

class TestTheBodyIsTheSameBytes:

    @pytest.mark.parametrize("kw", [{"pages": "1-2"}, {"password": "x"},
                                    {"pages": "1", "password": "x"}])
    def test_the_announcement_changes_no_byte_of_the_body(self, kw):
        bare, _ = _translate(HTML, "text/html")
        withp, notes = _translate(HTML, "text/html", **kw)
        assert bare.content == withp.content, (bare.content, withp.content)
        assert bare.status == withp.status and bare.error == withp.error
        assert " ".join(notes), "这一格只允许多一行申报，别的不许多"


# ============================================================================
# 来源：test_a_side_request_before_the_browser_says_so.py
# ============================================================================
# R02-F63 · 强制浏览器档之前那一发探活是**多发请求**，它必须在事前说一声。

_SENTENCE = "extra direct request"


def _probe(url="http://127.0.0.1:8400/page", status=200, **kw):
    """跑一次作者点：只替 `get`（真发请求的那只手），别的都留原样。

    `MasterFetchServer.get` 是 **staticmethod**，所以这里两处都要按 staticmethod 办：
    替身自己不接 `self`，还原时还回 `__dict__` 里那颗描述符本身（把纯函数赋回类属性
    会把它变成普通方法，之后每一次 `self.get(url)` 都会把实例当成 url —— 这一格付过
    这个代价：它让全量红了 46 格，见 review/backlog.md 的 B-30）。
    """
    calls = []

    async def fake_get(u, **kwargs):
        calls.append(kwargs)
        return ResponseModel(url=u, status=status, content=["body"],
                             fetcher_used="http", **kw)

    orig = MasterFetchServer.__dict__["get"]
    MasterFetchServer.get = staticmethod(fake_get)
    try:
        with CT._arg_notes_scope() as notes:
            server = MasterFetchServer()
            got = asyncio.run(server._private_health_probe(url, {}, None, None, 0.0))
    finally:
        MasterFetchServer.get = orig
    return got, notes, calls


# ── 格 0：挂具不许把钩子留在进程里 ──────────────────────────────────────

class TestTheFixtureHandsTheHookBack:

    def test_get_is_still_a_staticmethod_after_the_probe(self):
        _probe()
        assert isinstance(MasterFetchServer.__dict__["get"], staticmethod), (
            "还原成纯函数就等于把 `get` 变成方法：后面的每一发都会把实例当 url")



# ── 格 1：活着的那一发要留名 ────────────────────────────────────────────

class TestTheSideRequestNamesItself:

    def test_an_alive_probe_says_that_it_dialed_too(self):
        got, notes, calls = _probe()
        assert got is None, "200 = 活着，浏览器照跑（这条判决不许动）"
        joined = " ".join(notes).lower()
        assert _SENTENCE in joined, (
            f"目标自己的日志上多了一条请求，回执里却没人说：{notes}")

    def test_the_sentence_says_who_and_how_many(self):
        joined = " ".join(_probe()[1]).lower()
        for needle in ("probe", "browser", "1"):
            assert needle in joined, joined


# ── 格 2：判死的那一发也只说一次 ─────────────────────────────────────────

class TestTheVerdictSideStillSaysItOnce:

    def test_a_dead_probe_is_announced_once_not_twice(self):
        got, notes, calls = _probe(status=0)
        assert got is not None, "status=0 = 死了，探测结果就是答案（这条也不许动）"
        hits = [n for n in notes if _SENTENCE in n.lower()]
        assert len(hits) == 1, hits


# ── 格 3：申报不许改动行为 ───────────────────────────────────────────────

class TestTheAnnouncementChangesNoBehaviour:

    def test_the_probe_still_dials_direct_once_and_plain(self):
        got, notes, calls = _probe()
        assert len(calls) == 1, calls
        one = calls[0]
        assert one["stealthy_headers"] is False, one
        assert one["retries"] == 0, one
        assert one["follow_redirects"] is False, one
        assert one["proxy"] is None, one

    def test_the_envelope_other_than_the_note_is_untouched(self):
        got, notes, calls = _probe(status=503)
        assert got.status == 503 and not got.error, got
        assert "extra direct" not in (got.error or "").lower()
        assert len([n for n in notes if _SENTENCE in n.lower()]) == 1


# ============================================================================
# 来源：test_a_crawl_that_ran_out_of_characters_blames_the_characters.py
# ============================================================================
# 字符预算先到时，不许把停下的原因记在路径过滤头上（2026-10-08 验收 · D-04 的回归锁）

SEED = "https://scoped.example.com/"


class _FakeSite:
    """每一页秒回；页面正文按 URL 查表，链接从 HTML 里现取。"""

    def __init__(self, pages: dict[str, str]) -> None:
        self.fetched: list[str] = []
        self.pages = pages

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        return ResponseModel(url=url, status=200, content=[self.pages.get(url, "<p>leaf</p>")],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _page_with_links(links: list[str], filler: int = 0) -> str:
    body = "<p>" + ("word " * filler) + "</p>" if filler else "<p>start</p>"
    return ("<html><body>" + body
            + "".join(f'<a href="{h}">link</a>' for h in links)
            + "</body></html>")


SCOPE_NOTE = "scoped out every"


@pytest.mark.asyncio
async def test_a_character_budget_does_not_blame_the_scope_filter():
    """症状本体：预算先到、队列里还挂着 83 个 URL，note 却怪 path_include。"""
    links = [f"/docs/p{i}" for i in range(12)]
    site = _FakeSite({SEED: _page_with_links(links, filler=900)})   # 一页就超帽

    out = await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                      path_include=["/docs"], max_total_chars=1500,
                                      cache_ttl=0)

    assert SCOPE_NOTE not in out.summary, out.summary
    assert "truncation=total_chars" in out.summary, out.summary
    assert "still queued" in out.summary, out.summary


@pytest.mark.asyncio
async def test_a_scope_that_really_did_scatter_everything_still_says_so():
    """反向锁：这条 note 不是被"少说"解决的 —— 队列真空时它是唯一说明这件事的地方。"""
    site = _FakeSite({SEED: _page_with_links(["/docs/a", "/docs/b", "/news/c"])})

    out = await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                      path_include=["/nothing-here"], cache_ttl=0)

    assert out.pages_crawled == 1, [p.url for p in out.pages]
    assert SCOPE_NOTE in out.summary, out.summary
    assert "/nothing-here" in out.summary, out.summary


@pytest.mark.asyncio
async def test_the_two_claims_are_never_both_made_about_one_stop():
    """一句里可以同时有预算与 scope 的陈述，但"过滤器清空了队列"这一条只能有一个真相。"""
    links = [f"/docs/p{i}" for i in range(10)]
    site = _FakeSite({SEED: _page_with_links(links, filler=900)})

    out = await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                      path_include=["/docs"], max_total_chars=1500,
                                      cache_ttl=0)

    blames_filter = SCOPE_NOTE in out.summary
    blames_budget = "truncation=total_chars" in out.summary
    assert not (blames_filter and blames_budget), out.summary


@pytest.mark.asyncio
async def test_a_run_that_kept_more_than_the_start_page_says_nothing_about_scope():
    """起始页之外还有页面时，"过滤器把每个链接都滤掉了"本身就是假话。"""
    links = ["/docs/a", "/docs/b", "/news/c"]
    site = _FakeSite({SEED: _page_with_links(links)})

    out = await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                      path_include=["/docs"], cache_ttl=0)

    assert out.pages_crawled > 1, out.pages_crawled
    assert SCOPE_NOTE not in out.summary, out.summary
