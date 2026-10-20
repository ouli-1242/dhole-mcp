"""期4 的行级日期只许在**量过的引擎**上出现，而且出现时带着它读出来的那个精度。

这条守卫守的是一句谎的形状：一个日期字段一旦"看起来总是有"，读者就会把它的缺席读成
"这一行没有日期"，而缺席真正说的是"dhole 没在那家页面上量过这一格"。两种说法的修法相反
（前者要换查询，后者要补 xpath），所以这里钉的是哪几家量过、哪几家没量过、以及没量过的
行在线上花不花钱。

判据全部来自 `tests/engine_fixtures/` 的冻结夹具 + 各引擎**自己的**解析器（同一套
`items_xpath` / `elements_xpath`），日期文本再由唯一的作者 `serp_dates.read_row`
（→ `envelope.parse_serp_date`）读。这一段不许出现第二个解析器，也不许出现手写日期正则。

实测（这一轮在冻结夹具上跑出来的数，不是抄来的）：
  · sogou      8 个结果容器，4 个带 `span.cite-date`，绝对到日（"2020-08-31"）。
  · baidu A 版 5 个容器 2 个带 `span.cos-space-mr-3xs`（"2024年9月5日"）。
  · baidu B 版 6 个容器 3 个带 `span.cosc-source-caption`，其中 "4月23日" 没有年份，
               叶子如实拒判 —— 补一个年份就是替读者编一个页面上没有的日期。
  · bing 系    `span.news_dt`：cn 版两套版面 4/4 与 1/4 是绝对日期（阶段说明里写的
               "bing 两套版面零日期形状" 在这两个夹具上不成立，这里按跑出来的数钉）；
               国际版 10 个容器里 1 个，且是 "1 day ago"（relative）。
  · so360      7 个容器 1 个带 `span.g-c-dark`，原文混着上游写坏的 "&nbsp"，读不出。
  · yandex     0 个 —— 那一格里没有任何日期形状的字。
"""

from __future__ import annotations

import inspect
from pathlib import Path

from dhole_mcp import search_metasearch as ms
from dhole_mcp import serp_dates, wire
from dhole_mcp.search import SearchResponseModel, SearchResult

FIXTURE_DIR = Path(__file__).parent / "engine_fixtures"

DATE_KEYS = ("published", "published_precision", "published_basis")


def _rows(engine: str, fixture: str) -> list:
    """那一家自己的解析器在冻结页面上交出的行（走 `extract_kept`，生产那一条链）。"""
    text = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    return ms._TEXT_ENGINES[engine](proxy=None, timeout=5, verify=True).extract_kept(text)


def _dated(rows: list) -> list:
    return [r for r in rows if r.published]


class TestSogouStampsItsRows:
    """sogou 是量过的那一家：值、精度、原文三颗必须一起到位。"""

    def test_dated_rows_carry_a_day_precision_value(self):
        rows = _rows("sogou", "sogou_web.html")
        assert len(rows) == 8, len(rows)
        dated = _dated(rows)
        assert len(dated) == 4, [r.published for r in rows]
        assert all(r.published_precision == "day" for r in dated), \
            [(r.published, r.published_precision) for r in dated]

    def test_the_basis_quotes_the_literal_the_page_wrote(self):
        """`published_basis` 不是"解析成功"的同义反复：它得是版面上那几个字。"""
        for r in _dated(_rows("sogou", "sogou_web.html")):
            assert r.published_basis == r.published, (r.published, r.published_basis)
            assert len(r.published) == 10, r.published

    def test_the_undated_rows_say_so_by_saying_nothing(self):
        for r in _rows("sogou", "sogou_web.html"):
            if not r.published:
                assert r.published_precision == "" and r.published_basis == "", \
                    (r.title, r.published_precision, r.published_basis)


class TestBaiduReadsBothLayoutsAndRefusesWhatHasNoYear:

    def test_both_baidu_layouts_put_a_date_on_some_rows(self):
        a = _dated(_rows("baidu", "baidu_variant_a.html"))
        b = _dated(_rows("baidu", "baidu_variant_b.html"))
        assert len(a) == 2 and len(b) == 2, (len(a), len(b))
        assert all(r.published_precision == "day" for r in a + b)

    def test_a_month_and_day_without_a_year_is_not_invented_into_a_date(self):
        """B 版有 3 格日期形状的字，却只有 2 行拿到日期 —— 少的那行是 "4月23日"。

        它没有年份，叶子就不判（`serp_dates.read_row("4月23日").present is False`），
        而补一个年份出来的那一行会跟着 `before=` 被判掉：丢一行是不可逆的动作，读者在
        别处看不见它，也无从知道它是被哪一天判掉的。
        """
        rows = _rows("baidu", "baidu_variant_b.html")
        assert len(rows) == 6 and len(_dated(rows)) == 2, \
            [(r.published, r.published_basis) for r in rows]
        assert serp_dates.read_row("4月23日").value is None
        for r in rows:
            if r.published:
                assert len(r.published) == 10 and r.published_precision == "day", \
                    (r.published, r.published_precision)


class TestTheEnginesThatWereNotMeasuredPutNothingOnTheWire:

    def test_yandex_rows_carry_no_date_at_all(self):
        rows = _rows("yandex", "yandex.html")
        assert rows and not _dated(rows), [(r.title, r.published) for r in rows]

    def test_an_undated_row_costs_exactly_zero_bytes(self):
        """缺席就是它的含义：一行没有日期时那三颗不许以 `""` 的形态上线。"""
        sr = SearchResult(title="t", url="https://x.test/a", source="yandex")
        dumped = wire._wire_dump(sr)
        for key in DATE_KEYS:
            assert key not in dumped, dumped

    def test_an_undated_round_does_not_pay_for_an_empty_ledger(self):
        resp = SearchResponseModel(query="q", results=[])
        assert "date_coverage" not in wire._wire_dump(resp)

    def test_a_dated_row_pays_only_for_what_it_states(self):
        sr = SearchResult(title="t", url="https://x.test/a", source="sogou",
                          published="2020-08-31", published_precision="day",
                          published_basis="2020-08-31")
        dumped = wire._wire_dump(sr)
        assert dumped["published"] == "2020-08-31"
        assert dumped["published_precision"] == "day"
        assert dumped["published_basis"] == "2020-08-31"


class TestBingGlobalRelativeRowIsNeverABareDay:
    """国际版那一格写的是 "1 day ago"：精度必须跟着值走，否则 4.6 会按假日期丢行。"""

    def test_the_relative_row_is_stamped_relative(self):
        rows = _rows("bing_global", "bing_global.html")
        dated = _dated(rows)
        assert len(dated) == 1, [r.published for r in rows]
        row = dated[0]
        assert row.published_precision == "relative", (row.published, row.published_basis)
        assert row.published_basis == "1 day ago", row.published_basis
        assert len(row.published) == 10, row.published

    def test_the_cn_layouts_absolute_dates_come_through_as_day(self):
        """实测修正：cn 版两套版面**是**带 `span.news_dt` 的（4/4 与 1/4，绝对到日）。"""
        a = _dated(_rows("bing", "bing_variant_a.html"))
        assert len(a) == 4, [r.published for r in _rows("bing", "bing_variant_a.html")]
        assert all(r.published_precision == "day" for r in a)


class TestSo360KeepsTheRowItCannotRead:

    def test_the_one_date_shaped_cell_is_not_turned_into_a_guessed_day(self):
        """so360：7 个结果容器里 1 个带日期形状的字，交出去的 0 行有日期。

        两个数都要写出来，因为它们说的是两件不同的事：`7 → 2` 是"只有 2 个容器读得出
        真链"（href 的账，16.x 就钉住了），`2 → 0` 才是日期的账。那一格里躺着的是
        "1条评论 &nbsp55个收藏 &nbsp发贴时间：2026年5月8日" —— 一整段混着写坏实体的版面
        文本，叶子读不出一个干净的日期，于是这一行按"没有日期"入账，而不是被补成
        2026-05-08 再跟着 `before=` 判掉。
        """
        eng = ms._TEXT_ENGINES["so360"](proxy=None, timeout=5, verify=True)
        text = (FIXTURE_DIR / "so360.html").read_text(encoding="utf-8")
        rows = eng.extract_kept(text)
        assert eng.last_extract[0] == 7, eng.last_extract   # 结果容器数
        assert len(rows) == 2, len(rows)                    # 交得出的行数
        assert not _dated(rows), [r.published for r in rows]
        # 那一格躺着的是 "1条评论 &nbsp55个收藏 &nbsp发贴时间：2026年5月8日" —— 一整段
        # 混着写坏实体的版面文本，叶子读不出干净日期，于是这一行按"没有日期"入账。


class TestTheStamperIsTheOnlyAuthor:
    """盖日期这一步在链上只有一个调用点，读日期的函数也只有一个。"""

    def test_there_is_exactly_one_call_site_that_reads_the_row_date(self):
        src = inspect.getsource(ms)
        # 1 = `_stamp_row_date` 的定义，1 = 它自己的函数体里那句 `serp_dates.read_row`，
        # 1 = `extract_kept` 里唯一那处推导式调用。多出一处就是第二个调用点。
        assert src.count("_stamp_row_date(") == 2, src.count("_stamp_row_date(")
        assert src.count("serp_dates.read_row(") == 1, src.count("serp_dates.read_row(")

    def test_the_stamper_hands_the_judgement_to_the_leaf(self):
        """解析不出 → 三颗空串，而不是把版面上那句原文留在 `published` 里上线。"""
        row = ms.TextResult(title="t", href="https://x.test/1", body="b",
                            published="See 1984 by Orwell")
        ms._stamp_row_date(row)
        assert (row.published, row.published_precision, row.published_basis) == ("", "", "")
        assert serp_dates.read_row("2020-08-31").precision == "day"

    def test_no_engine_class_grew_a_date_regex(self):
        """日期词表只有一份，住在 `envelope`；引擎那一层只许交 xpath。"""
        src = inspect.getsource(ms)
        for pattern in (r"\\d{4}", r"months? ago", r"天前"):
            assert pattern.replace("\\\\", "\\") not in src, pattern
