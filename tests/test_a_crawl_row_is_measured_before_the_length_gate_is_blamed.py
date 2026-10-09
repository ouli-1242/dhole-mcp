r"""R2 第一刀（measure）：crawl 的一行先被**量到**，而计划里那颗"50 字符的闸"被量掉了。

计划对这一刀的判断是："`content_ok = len(md) >= 50` 就是缺陷本身 —— 把它抬到 web_read
那侧标定过的档位（塌陷比 + 列表要有 ≥3 个条目 + 按 kind 分档）。"量完之后三条里只有
两条活下来，第三条被一份正对照当场推翻：

* **抬到 200 会吃掉 `/collections?items=1`。** 那一页整页可见字 37 而 markdown 80，
  它的"内容"就是三张卡片。`visible_text_chars` 那把尺天生量不到以链接与数据为正文的
  页面 —— 把"字少"读成"没内容"，吃的正是这批一口咬定不能吃的形状（与 R1 语料清扫里
  那两发假阳性同形：术语表被判成编码块、全小写长标识符被判成 base64）。所以这颗阈值
  根本没进产品，`visible_chars` 作为**数字**照报，"够不够 200"那个判断不报。
* **导航栏与列表在一页之内分不开。** 借 web_read 的 `_count_content_links`（先剥
  `<nav|header|footer|aside>`）在真语料上试过一次，直接错：books.toscrape 的 travel
  列表页把**全部 11 本书**装在 `<aside class="sidebar">` 里，剥完之后主内容区只剩 2 条
  链接，而那一页恰恰是这批不能误伤的正对照。反过来按 URL 形状聚也不行：靶站那枚导航页
  的 12 个 section（`/about`、`/cart`…）与一个扁平 slug 列表（`/quote/1`、`/quote/2`）
  同构。所以"这一行其实是站点菜单"只在**跨行**时才可证 —— 同一组条目链接逐字出现在
  好几行里 —— 而今天它只上报，不翻任何一行的结论位。
* **活下来的是那把塌陷的尺。** `is_extraction_collapsed` 是 web_read 用来把"字在页上而
  抽取器丢了它们"和"这页本来就是壳"分开的，它有实测比值带（travel 196/1374=0.14 塌；
  quotes 2179/1681、gnu 25785/25245 不塌）。crawl 从来没有问过这一问，所以一行的
  `content_ok=true` 旁边今天站着的是一个没人检查过的假设。

分两笔的理由与 R1 同一条：这一族闸的失败模式从来不是漏判而是过度开火，而 `content_ok`
在 crawl 里被三处读（`pages_crawled` 那几句 summary、批量数学、以及 `resp.content_ok`
的门）。第一笔唯一的可观察变化是**话**：量到的写进那一行的 summary 与整轮的 summary。
"""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import fixture_site as fs  # noqa: E402

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.crawl import (  # noqa: E402
    CHROME_REPEAT_MIN, LIST_ITEMS_MIN, _chrome_rows, _classify_and_extract,
    _measure_note, _measure_row,
)
from dhole_mcp.envelope import is_extraction_collapsed  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "page_fixtures"
START = "http://127.0.0.1:8399/"
TRAVEL = "http://books.toscrape.com/catalogue/category/books/travel_2/index.html"

_REAL_GETADDRINFO = socket.getaddrinfo


def _html(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8", errors="replace")


def _run(html: str, url: str = f"{START}x", start: str = START):
    stats: dict = {}
    md, kind, ok, cut = _classify_and_extract(html, url, start, None, 40_000,
                                              stats=stats)
    return md, kind, ok, stats


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    """靶站真起在 0 号端口：这一族判据要的是真 HTTP 回来的字节。"""
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    # 爬的门只认存量放行（cred_defaults 的 allow_private；conftest 把身份库重定向进
    # tmp_path，所以这一句不会碰用户的机器）。
    identity.set_value("allow_private", True)
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


class TestTheCollapseRulerIsTheOneWebReadAlreadyCalibrated:

    def test_the_sidecar_numbers_still_read_as_collapsed(self):
        """先确认这一刀用的就是 web_read 那把尺，不是又一颗阈值。"""
        assert is_extraction_collapsed(196, 1374), "travel 那一份的塌陷形状变了"
        assert not is_extraction_collapsed(2179, 1681)
        assert not is_extraction_collapsed(25785, 25245)

    def test_collapse_is_only_asked_where_the_extractor_chose(self):
        """同一份 travel 页：抽成 196 字的 article 是塌陷，渲染成 5,874 字的列表不是。

        list 那一支的 md 是 `* [标题](url)` 清单，它**故意**丢掉非链接的字 —— 拿整页
        可见字去比是拿错的量具量对的东西。这一条钉的就是那个分岔。
        """
        html = _html("travel_2.html")
        collapsed: dict = {}
        _measure_row(collapsed, html, "x" * 196, "article", TRAVEL, TRAVEL)
        assert collapsed == {"visible_chars": 1374, "extracted_chars": 196,
                             "extraction_collapsed": True}, collapsed

        rendered_md, kind, ok, stats = _run(html, TRAVEL, TRAVEL)
        assert (kind, ok) == ("list", True), (kind, ok, stats)
        assert "extraction_collapsed" not in stats, stats
        assert len(rendered_md) > 1374, "渲染出来的列表比整页可见字还短，比值那一问就跑偏了"

    @pytest.mark.parametrize("name,md_len,visible", [
        ("gnu_free_sw_definition.html", 25785, 25245),
        ("quotes_index.html", 2179, 1681),
    ])
    def test_a_faithful_extraction_is_not_reported_as_a_failure(self, name, md_len,
                                                                visible):
        """这两份页的比值带（1.02 / 1.30）就是 sidecar 里那组数 —— 抽取是忠实的。"""
        url = json.loads((FIXTURES / name).with_suffix(".meta.json")
                         .read_text(encoding="utf-8"))["captured_from_url"]
        _, kind, ok, stats = _run(_html(name), url, url)
        assert kind == "article", (name, kind)
        assert ok is True
        assert not stats.get("extraction_collapsed"), stats
        assert (stats["extracted_chars"], stats["visible_chars"]) == (md_len, visible)

    def test_the_sentence_names_both_numbers_and_the_way_out(self):
        """上报的那一句话要自带证据与解药，不是"有点问题"。"""
        note = _measure_note({"extraction_collapsed": True, "visible_chars": 1374,
                              "extracted_chars": 196}, content_ok=True)
        assert "1,374" in note and "196" in note and "14%" in note, note
        assert "extraction_type='html'" in note, note
        assert "dhole's reader lost them" in note, note

    def test_a_row_that_already_failed_says_nothing_more(self):
        """上报落在**缝隙**上：一行已经写着 content_ok=false 时再补一句是噪音。"""
        assert _measure_note({"extraction_collapsed": True, "visible_chars": 1374,
                              "extracted_chars": 196}, content_ok=False) == ""


class TestMeasuringMovesNoVerdict:
    """这一刀唯一的可观察变化是话，不是结论。这一条要在下一笔之前一直是绿的。"""

    def test_every_corpus_page_judges_the_same_with_and_without_the_ruler(self):
        pages = [(name, getattr(fs, name)) for name in dir(fs)
                 if isinstance(getattr(fs, name), bytes)]
        pages.append(("_obfuscated_shell_page", fs._obfuscated_shell_page()))
        assert len(pages) >= 12, sorted(n for n, _ in pages)
        for name, raw in pages:
            html = raw.decode("utf-8", "replace")
            a = _classify_and_extract(html, f"{START}x", START, None, 40_000)
            b = _classify_and_extract(html, f"{START}x", START, None, 40_000,
                                      stats={})
            assert a[1:] == b[1:], (name, a[1], b[1])

    def test_the_nav_only_row_is_still_called_a_success_today(self):
        """这一条写下的是**当下的事实**，不是期望，也不是这一刀要改的东西。

        `/nav` 的 12 条链接全在 `<nav>` 里、主内容区为空，今天它是
        `kind="list"`、`content_ok=true`、448 个字符。这一刀只把它量出来
        （`list_items` 12 与那个 item set）；把它说成菜单需要**别的行**也带着同一组
        链接，那是跨行的事，见 `TestTheChromeRuleNeedsMoreThanOneRow`。
        """
        md, kind, ok, stats = _run(fs.NAV_PAGE.decode(), f"{START}nav", START)
        assert (kind, ok) == ("list", True), (kind, ok, stats)
        assert stats["list_items"] == 12, stats
        assert not stats.get("thin_list"), stats
        assert len(md) > 300, len(md)

    def test_the_thin_list_flag_needs_a_list_below_three_items(self):
        """`LIST_ITEMS_MIN`（=3）的判据本身：一条列表行只有两三条条目链接时的差别。"""
        assert LIST_ITEMS_MIN == 3
        html = ('<html><body><nav>' + "".join(
            f'<a href="/section/{i}">栏目 {i}</a>' for i in range(12)) +
            '</nav><p>' + "正文太短以至于不成列表。" * 1 + '</p></body></html>')
        stats: dict = {}
        _measure_row(stats, html, "* [a](/x)\n* [b](/y)", "list",
                     f"{START}nav", START)
        assert stats["list_items"] == 12, stats  # nav 里的链接照样数得到（这就是它不够格当闸的原因）

        thin: dict = {}
        _measure_row(thin, '<a href="/one">一</a><a href="/two">二</a>',
                     "* [一](/one)\n* [二](/two)", "list", f"{START}x", START)
        assert thin.get("thin_list") is True, thin
        assert "2 item link(s)" in _measure_note(thin, content_ok=True), thin


class TestTheRejectedFloorStaysRejected:
    """一颗看起来合理的阈值怎样被一份正对照挡在门外 —— 写下来免得有人再提。"""

    def test_a_legit_list_page_has_far_fewer_visible_chars_than_its_markdown(self):
        """/collections?items=1：可见字 37 而 markdown 80 —— 那颗"整页要 ≥200 字"的闸
        会把这一页判成没内容，而它的三张卡片一个字都没丢。"""
        md, kind, ok, stats = _run(fs._collections_page(1).decode(),
                                   f"{START}collections", START)
        assert ok is True, stats
        assert stats["visible_chars"] == 37, stats
        assert stats["extracted_chars"] == 80, stats
        assert "专题 1" in md and "/work/1" in md, md

    @pytest.mark.parametrize("items,kind", [(1, "fallback"), (8, "article")])
    def test_visible_text_is_not_a_measure_of_content_for_pages_of_links(self, items,
                                                                        kind):
        md, got_kind, ok, stats = _run(fs._collections_page(items).decode(),
                                       f"{START}collections", START)
        assert got_kind == kind, stats
        assert ok is True
        # 卡片页的正文字数天生低于它交回去的 markdown：内容是链接，不是散文。
        assert stats["visible_chars"] < stats["extracted_chars"], stats


class TestTheChromeRuleNeedsMoreThanOneRow:
    """"这一行其实是菜单"只在跨行时可证，而它今天只上报。"""

    @staticmethod
    def _measured(sets):
        return {f"http://s/{i}": {"item_set": frozenset(s), "list_items": len(s)}
                for i, s in enumerate(sets)}

    def test_three_rows_carrying_the_identical_item_set_are_named(self):
        group = {"/about", "/cart", "/login"}
        rows = _chrome_rows(self._measured([group, group, group]))
        assert len(rows) == 3 and all(v == CHROME_REPEAT_MIN for v in rows.values()), rows

    def test_a_pair_is_not_evidence_yet(self):
        """两行同集还可能是两个姊妹栏目页真的列了同一批东西 —— 阈值不是装饰。"""
        group = {"/a", "/b", "/c"}
        assert _chrome_rows(self._measured([group, group])) == {}

    def test_a_row_with_its_own_entries_is_never_flagged(self):
        nav = {"/about", "/cart", "/login"}
        rows = _chrome_rows(self._measured([nav | {"/p/1"}, nav | {"/p/2"},
                                            nav | {"/p/3"}, nav, nav, nav]))
        assert sorted(rows) == ["http://s/3", "http://s/4", "http://s/5"], rows

    def test_a_page_that_contributed_no_item_set_is_not_chrome(self):
        """只有散文的页面没有 item set —— 它不该被任何一行的判据牵连。"""
        assert _chrome_rows(self._measured([set(), set(), set()])) == {}


class TestTheLiveCrawlCarriesTheDisclosure:
    """端到端：这一族尺在真 HTTP 字节上不误伤，而报出来的话落在该行与整轮上。"""

    @pytest.mark.asyncio
    async def test_a_collapsing_row_says_it_in_the_row_and_in_the_run(self, live_site):
        """管路证明：量到塌陷的那一行要把话说在**自己的 summary** 里，整轮要数得出几行。

        挂具 `/longaside` 是量出来的形状（正文 2.5k 抽得到、aside 里 4.6k 个可见字被主内容
        过滤器整块丢掉），不是照着断言捏的：先确认抽取器确实丢，才立成路由。
        """
        result = await MasterFetchServer().web_crawl(
            f"{live_site}/longaside", max_pages=1, max_depth=0)
        assert result.pages, result.error
        row = result.pages[0]
        assert row.page_type == "article", row.summary
        assert row.content_ok is True, "这一刀不动结论位：" + (row.summary or "")
        found = re.search(r"holds ([\d,]+) visible characters while we returned "
                          r"([\d,]+) \((\d+)%\)", row.summary or "")
        assert found, row.summary
        visible, got = (int(x.replace(",", "")) for x in found.groups()[:2])
        assert got < visible * 0.5, (visible, got)
        assert visible > 4_000, visible
        assert "extraction_type='html'" in row.summary, row.summary
        assert "1 page(s) hold far more text in their own markup" in result.summary, \
            result.summary

    @pytest.mark.asyncio
    async def test_legit_rows_carry_no_measurement_noise(self, live_site):
        result = await MasterFetchServer().web_crawl(
            f"{live_site}/nav", max_pages=6, max_depth=1)
        assert result.pages, result.error
        for p in result.pages:
            assert "extraction collapsed" not in (p.summary or ""), p.summary
            assert "site chrome" not in (p.summary or ""), p.summary
            assert "thin list" not in (p.summary or ""), p.summary
        assert "site chrome" not in result.summary, result.summary

    @pytest.mark.asyncio
    async def test_the_nav_seed_row_still_comes_back_as_a_list(self, live_site):
        """翻闸不在这这一笔：`/nav` 这一行今天仍然 `content_ok=true`。

        下一笔也**不**打算凭单行判据翻它 —— 上面 TestTheChromeRule 那组用例说清了
        为什么（单行分不开菜单与列表），能说的是跨行那一组同集。
        """
        result = await MasterFetchServer().web_crawl(
            f"{live_site}/nav", max_pages=1, max_depth=0)
        assert len(result.pages) == 1, result.pages
        row = result.pages[0]
        assert row.page_type == "list", row.summary
        assert row.content_ok is True, row.summary
