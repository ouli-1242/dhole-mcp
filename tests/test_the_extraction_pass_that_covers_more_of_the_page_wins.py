"""两次抽取都跑了，该用「盖住了页面上多少块字」选哪一次，而不是只用字数比。

现场（2026-10-14 真实使用模拟 · P02-10 学术图书馆员 · S2）：一份书目页 —— h1、一句
更新日期、四条 `<li>` 的书单、一段比书单长的说明文字。`web_read` 交回 503 个字符，
`content_ok: true`、`substance: ok`，而这一页自己可见的是 947 个字符。**h1 与四条书目
全部不见了**，留下的只有那段说明。同一份字节用 `css_selector='ul'` 能取回 349 个字符
的四条条目，用 `extraction_type='html'` 能取回 1,214 —— 字在 markup 里，是抽取器丢的。

机制（同一份字节两条臂各量一遍，`review/.runtime/p4/measure_block_coverage2.py`）：
trafilatura 的 `favor_recall=False`（dhole 的默认臂）把内容选到那段长说明上，块外的
东西一律不算正文；`favor_recall=True`（recall 臂）在同一页上交回 854 个字符、四条书目
都在。两条臂都试过一次的是「塌陷」那一条路，而它的判据是**总量比**
（`is_extraction_collapsed`：抽回来的 < 整页可见字的 50%）。这一页 503/947 = 53%，
正好从总量那条线上面走过去 —— 总量看得见「丢了一半」，看不见「一整块没了」。

阈值是量出来的，不是拍的（`review/.runtime/p4/sample_ratio_band.py`，119 份页面：
111 份存档真 SERP + gnu.org 定义页 + quotes.toscrape 首页 + books.toscrape travel 页
+ 5 份本缺陷靶页）：

    recall 臂按块覆盖占优的页面共 12 份，它们的字数比全在 0.13–0.75；
    字数比 >= 0.90 的页面上，recall 臂一块都没多盖住（12/12 落在 0.90 之下）。

于是重试的带宽取 0.90：比 0.50 宽，多花的是一次进程内抽取；换回来的是那 12 份里
今天被静默丢掉的那 8 份。0.50 那条线本身一字未动（`quality` 用它判 JS 壳，
放宽它等于让浏览器档去背抽取的债）。

契约（本文件钉住）：
  * 默认臂整块漏掉页面自己的字，且 recall 臂按块覆盖**严格更多** → 用 recall 臂。
  * 覆盖打平 → 保留默认臂（今天的形状不许被这批改坏，包括字数更多但覆盖不增的那一类）。
  * 字数比 >= 0.90 → 根本不再跑第二趟（成本闸：实测这一带上没有一页能靠它多盖一块）。
  * 块覆盖这把尺自己：只数叶子块（`<li>` 是一个单元，`<ul>` 不算两次），按标签剥
    nav/header/footer/aside —— 且**明知**这条剥不干净（books.toscrape 把全部 11 本书
    装在 `<aside class="sidebar">` 里，见 crawl.py `_item_links` 的那笔账）。它之所以
    敢这么用，是因为它只用于**两条臂互相比较**：两边同一套块、同一套剥法，误差对消。
  * 路径锁：`web_read` 全链路与 `web_crawl` 用的 `extract_content_from_html` 都要走通
    （2026-10-06 那一刀的教训：守卫钉住了函数，没钉住调用它的路上）。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp import trafilatura_extractor
from dhole_mcp.server import MasterFetchServer

P1 = ("The repository grew out of a working group that met twice a month between "
      "2019 and 2023, reading aloud from the same three chapters and arguing about "
      "what counts as participation. ")
P2 = ("Minutes were kept in a shared document and later became the backbone of this "
      "list, which is why the entries follow the calendar rather than any ranking. ")
P3 = ("Entries were added by whoever brought a chapter worth arguing about, and the "
      "group never voted on anything. ")
BENIGN = ("<div class=\"notice\">"
          f"<p>{P1}</p><p>{P2}</p><p>{P3}</p>"
          "</div>")
ITEMS = ("Fung", "Sullivan", "Holland", "Wang")


def _reading_list_page() -> str:
    """h1 + 一句日期 + 四条书目 + 一段比书单长的说明 —— 本缺陷的形状。"""
    lis = "".join(
        f"<li>{n}, A. <em>Title number {i}: Participation and Place</em>. "
        f"Academic Press, 20{10 + i:02d}.</li>"
        for i, n in enumerate(ITEMS, start=1))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>A Reading List</title></head><body>"
            "<h1>Community Self-Organization: An Open Reading List</h1>"
            "<p>Last updated 2023-05-18 by the repository working group.</p>"
            f"<ul>{lis}</ul>" + BENIGN +
            "<p>The list above is maintained by volunteers and is not peer reviewed."
            "</p></body></html>")


def _plain_page() -> str:
    """同一份 h1 + 四条书目，但**没有**那段长说明 —— 默认臂本来就全盖住的形状。"""
    lis = "".join(
        f"<li>{n}, A. <em>Title number {i}: Participation and Place</em>. "
        f"Academic Press, 20{10 + i:02d}.</li>"
        for i, n in enumerate(ITEMS, start=1))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>A Reading List</title></head><body>"
            "<h1>Community Self-Organization: An Open Reading List</h1>"
            "<p>Last updated 2023-05-18 by the repository working group.</p>"
            f"<ul>{lis}</ul>"
            "<p>The list above is maintained by volunteers and is not peer reviewed."
            "</p></body></html>")


def _counting_monkey(monkeypatch):
    """数清楚第二趟到底跑没跑（成本闸也要能被钉住，不然它只是一句注释）。"""
    real = trafilatura_extractor._trafilatura_extract
    seen: list = []

    def spy(html, url, *, favor_recall=False):
        seen.append(favor_recall)
        return real(html, url, favor_recall=favor_recall)

    monkeypatch.setattr(trafilatura_extractor, "_trafilatura_extract", spy)
    return seen


class TestTheWiderPassWins:

    def test_the_list_the_default_pass_left_out_comes_back(self, monkeypatch):
        html = _reading_list_page()
        answer = trafilatura_extractor._trafilatura_markdown(html, "http://x.test/")
        missing = [n for n in ITEMS if n not in answer]
        assert not missing, (
            f"四条书目里 {missing} 仍然不在答复里（答复 {len(answer):,} 字符，"
            f"整页可见 {trafilatura_extractor.visible_text_chars(html):,} 字符）")

    def test_the_headline_is_not_the_only_thing_the_wider_pass_brought(self):
        """块覆盖这把尺量的就是「整块没了」，它必须在这页上说出两条臂的差别。"""
        html = _reading_list_page()
        default = trafilatura_extractor._trafilatura_extract(html, "http://x.test/")
        recall = trafilatura_extractor._trafilatura_extract(
            html, "http://x.test/", favor_recall=True)
        d_gone = trafilatura_extractor.omitted_body_chars(html, default)
        r_gone = trafilatura_extractor.omitted_body_chars(html, recall)
        assert d_gone > r_gone, (
            f"默认臂丢了 {d_gone} 个块内字符，recall 臂丢了 {r_gone} —— 这把尺"
            "分不出两条臂，选择就无从下手")

    def test_a_page_the_default_pass_already_covers_is_untouched(self, monkeypatch):
        html = _plain_page()
        seen = _counting_monkey(monkeypatch)
        answer = trafilatura_extractor._trafilatura_markdown(html, "http://x.test/")
        assert all(n in answer for n in ITEMS), "反面锁的前提：默认臂本来就全盖住"
        assert True not in seen, (
            f"默认臂一块没丢的这一页被跑了第二趟（{len(html):,} 字节的页面白付一次抽取）")

    def test_the_second_pass_is_not_taken_when_it_covers_no_more(self, monkeypatch):
        """覆盖打平 / 更差 → 保留默认臂，哪怕第二趟更长。

        这一格有真页打底，不是设想出来的：存档的 baidu SERP
        `evals/corpus/serp/q041__baidu.html` 里宽臂**更长**（745 对 518 字符），整块
        漏掉的页面字符反而**更多**（141 对 58）。旧的按长度挑会把这份答复换成一个
        更长而更空的文本。这里用一条假臂把「更长但覆盖不增」钉成规则。
        """
        html = _reading_list_page()
        real = trafilatura_extractor._trafilatura_extract
        default_answer = real(html, "http://x.test/")

        def fake(html_, url, *, favor_recall=False):
            if favor_recall:
                return default_answer + (" padding " * 400)
            return default_answer

        monkeypatch.setattr(trafilatura_extractor, "_trafilatura_extract", fake)
        answer = trafilatura_extractor._trafilatura_markdown(html, "http://x.test/")
        assert "padding" not in answer, "更长但一块也没多盖住的答复不许换掉默认臂"
        assert all(n in answer for n in ITEMS) or not any(
            n in default_answer for n in ITEMS), (
            "换臂只允许由覆盖决定，别的都不算理由")


    def test_the_band_is_measured_and_sits_on_the_sampled_gap(self):
        assert trafilatura_extractor.EXTRACT_RECALL_BAND == pytest.approx(0.90)
        html = _reading_list_page()
        default = trafilatura_extractor._trafilatura_extract(html, "http://x.test/")
        ratio = len(default) / trafilatura_extractor.visible_text_chars(html)
        assert ratio < 0.90, f"这一页的比值是 {ratio:.2f}，本该落在带内"
        from dhole_mcp.envelope import EXTRACT_COLLAPSE_RATIO
        assert ratio >= EXTRACT_COLLAPSE_RATIO, (
            "这一页必须是从总量那条线**上面**过去的 —— 否则本测试钉的不是本缺陷")


class TestTheRulerItself:

    def test_leaf_blocks_are_counted_once_at_leaf_granularity(self):
        """`<li>` 是单元，`<ul>` 不再算第二次 —— 否则「列表丢了一半」被读成「没丢」。"""
        html = _reading_list_page()
        blocks = trafilatura_extractor.visible_leaf_blocks(html)
        tags = [t for t, _x in blocks]
        assert tags.count("li") == len(ITEMS)
        assert "ul" not in tags

    def test_a_block_is_covered_when_any_of_its_phrases_survives(self):
        html = _plain_page()
        whole = trafilatura_extractor._trafilatura_extract(html, "http://x.test/")
        assert trafilatura_extractor.omitted_body_chars(html, whole) == 0
        assert trafilatura_extractor.omitted_body_chars(html, "") == sum(
            len(x) for _t, x in trafilatura_extractor.visible_leaf_blocks(html))

    def test_the_chrome_tags_are_stripped_and_that_is_a_known_limit(self):
        """按标签剥 nav/aside —— 并且这把尺**明知**它剥不干净（books.toscrape 把 11 本
        书全装在 `<aside>` 里）。只因为它用于两条臂互比，误差对消，才敢用。

        这条测试钉的是「它剥」这件事本身，外加一句提醒：谁要把这把尺单独当"内容丢了"
        的判据来用，就先在 travel 页上翻车。
        """
        html = ("<html><body><nav><ul>" + "<li>Home page link</li>" * 6 +
                "</ul></nav><p>Body sentence about the reading list itself.</p>"
                "</body></html>")
        blocks = trafilatura_extractor.visible_leaf_blocks(html)
        assert [t for t, _x in blocks] == ["p"], (
            "nav 里的条目被算成了正文块 —— 剥标签这一步没跑")


# ─── 路径锁 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def origin():
    page = _reading_list_page().encode("utf-8")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


@pytest.mark.asyncio
async def test_the_online_route_delivers_the_list(origin):
    """`web_read` 全链路：书单要在 `content` 里，不是在残卷的封面上。"""
    r = await MasterFetchServer().web_read(origin + "/reading-list",
                                           max_content_chars=200_000)
    body = "\n".join(r.content)
    missing = [n for n in ITEMS if n not in body]
    assert not missing, f"主路上交回来的还是那份只剩说明的残卷：{missing}"


@pytest.mark.asyncio
async def test_the_crawl_route_shares_the_same_choice():
    """`web_crawl` 走 `extract_content_from_html` —— 同一把选择必须也在那条路上。"""
    from dhole_mcp.trafilatura_extractor import extract_content_from_html

    md = extract_content_from_html(_reading_list_page(), "http://x.test/", "markdown")
    assert md and all(n in md for n in ITEMS), (
        f"爬取那条路仍然只拿到说明段：{(md or '')[:120]!r}")


def test_an_empty_default_answer_still_takes_the_answer_that_is_not_empty(monkeypatch):
    """覆盖这把尺在"两条臂都没盖住任何块"时打平 —— 那时空答复不许赢。

    这一格是改选择规则时**自己**开出来的口子：旧规则比字数，空答复必输；新规则比块覆盖，
    一页若没有非套壳的叶子块就两边都是 0 个字符丢失。堵住它，而不是假设它不存在。
    """
    html = _reading_list_page()
    real = trafilatura_extractor._trafilatura_extract

    def only_recall(html_, url, *, favor_recall=False):
        return "" if not favor_recall else real(html_, url, favor_recall=True)

    monkeypatch.setattr(trafilatura_extractor, "_trafilatura_extract", only_recall)
    answer = trafilatura_extractor._trafilatura_markdown(html, "http://x.test/")
    assert answer.strip(), "默认臂交回空的这一趟，答复不能还是空的"

