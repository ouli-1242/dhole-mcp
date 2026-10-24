"""一条反爬灰页不能被当成正文，一篇门户正文也不能被当成索引。

现场（2026-10-24，真实站点实测）：

    web_read('https://www.bilibili.com/v/popular/rank/all')
      HTTP 档 → 4,927 字节的反爬灰页（`window._GreyResult`），markdownify 对它产出
      0 字符，而兜底把**整页标记**当 markdown 交回：`content_ok=true`、
      `substance=ok`、"4.3KB markdown"，正文以 `<!DOCTYPE html>` 打头。同一 URL
      走 stealthy 档拿到的是 11,946 字符的真实榜单。

    web_read('https://www.163.com/dy/article/L90AC7MN0511D5IC.html')
      → 2,844 字的正文被换成 117 条侧栏推荐链接，`page_type=list`，`next_action`
      建议去爬那些已经读到的页面。

本文件钉住四件事：

  * 转不出字时兜底交**空**，不交原始标记 —— 空答案才会让阶梯升级；
  * `answer_is_prose` 分得开"一段话"与"一串标签"；
  * 抽取器已经交回句子时，页面类型不是 `list`（那会开出爬取建议）；
  * 塌陷补救不把一段正文换成链接清单，但候选自己是一段话时照旧接管。
"""

from __future__ import annotations

from dhole_mcp.envelope import answer_is_prose, detect_page_type
from dhole_mcp.fetcher import Response
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _is_js_shell, extraction_collapsed
from dhole_mcp.server import _translate_response_body
from dhole_mcp.trafilatura_extractor import _fallback_extract

#: 哔哩哔哩那张灰页的形状：整页只有一段脚本与一条 meta description。
GREY = ('<html lang="zh-CN"><head><!--271266-->'
        '<script>window._GreyResult = {"method":"direct","versionId":"271266"}</script>'
        '<title></title><meta name="spm_prefix" content="333.934" />'
        '<meta charset="utf-8" /><meta name="description" '
        'content="bilibili是国内知名的视频弹幕网站，这里有最及时的动漫新番。" />'
        '</head><body></body></html>')

PROSE = ("中微子是构成物质世界的基本粒子之一，不带电、质量极轻，几乎不与物质作用，"
         "被称为幽灵粒子。在黑洞喷流与伽马射线暴的现场，只有它能笔直冲出天体核心，"
         "跨越数十亿光年抵达地球。")
LABELS = "\n".join(f"新闻标题{i:02d}" for i in range(30))


def _page(html: str, url: str = "https://grey.test/") -> Response:
    return Response(url=url, body=html.encode("utf-8"), status=200,
                    headers={"content-type": "text/html; charset=utf-8"})


# ─── 1 · 灰页：转不出字就交空，不交标记 ────────────────────────────────

def test_a_wall_that_converts_to_nothing_does_not_come_back_as_markup():
    """markdownify 对这张灰页产出 0 字符 —— 兜底从前把 4,376 个标记字符当正文交回。

    这条断言只看形状：交回来的那份里不许出现标签。整页标记不是"读到了内容"，
    而调用方无法从 `content_ok=true` 里分辨出这件事。
    """
    out = "\n".join(_fallback_extract(_page(GREY), "markdown", None))
    assert "<" not in out, out[:160]
    assert out.strip() == "", out[:160]


def test_a_text_extraction_of_the_same_wall_is_empty_too():
    out = "\n".join(_fallback_extract(_page(GREY), "text", None))
    assert out.strip() == "", out[:160]


def test_an_empty_answer_on_a_200_is_a_shell_so_the_ladder_can_escalate():
    """空答案让 `_is_js_shell` 说出"这是壳" —— 这才是 HTTP→stealthy 的扳机。

    从前这条闸看不见灰页：`extracted_chars` 数的是那 4,376 个标记字符，远在
    "字少"那条线之上，于是既不判壳、也不升级，灰页以 `content_ok=true` 交付。
    """
    wall = ResponseModel(status=200, content=[""], url="https://grey.test/",
                         fetcher_used="http", content_type="text/html; charset=utf-8",
                         total_size_bytes=len(GREY.encode("utf-8")))
    assert _is_js_shell(wall) is True


# ─── 2 · 形状：一段话 vs 一串标签 ──────────────────────────────────────

def test_a_paragraph_of_sentences_is_prose():
    assert answer_is_prose(PROSE * 8) is True


def test_a_run_of_link_labels_is_not_prose():
    assert answer_is_prose(LABELS) is False


def test_a_fragment_below_the_floor_is_not_judged():
    """40 个字里一个句号是 2.5/100 —— 分母太小时比例没有意义。"""
    assert answer_is_prose(PROSE[:40]) is False


# ─── 3 · 页面类型：有正文的门户页不是索引 ──────────────────────────────

def _portal_html() -> str:
    """一页中文门户正文：25 条同域推荐 + 6 段正文，没有 <article> 元素。

    读数与实测的 81.cn 同形：可见字 ~900、同域链接 25 条、链接标签占 0.17、
    最长非链接串 120 字（都落在旧判据的 list 那一侧）。
    """
    paras = "".join(f"<p>{PROSE}</p>" for _ in range(6))
    links = "".join(f'<li><a href="/news/{i}.html">新闻标题{i:02d}</a></li>'
                    for i in range(25))
    return (f"<html><body><div class='nav'><ul>{links}</ul></div>"
            f"<div class='body'>{paras}</div></body></html>")


def test_a_portal_page_whose_answer_is_prose_is_not_an_index():
    html = _portal_html()
    assert detect_page_type(html, "https://portal.test/a.html", "text/html", 700) == "list"
    assert detect_page_type(
        html, "https://portal.test/a.html", "text/html", 700,
        extracted_text=PROSE * 6) == "article"


def test_a_portal_page_whose_answer_is_labels_is_still_an_index():
    """误伤侧：同样的页面，抽取器交回的是标签时，`list` 那句仍然成立。"""
    assert detect_page_type(
        _portal_html(), "https://portal.test/a.html", "text/html", 700,
        extracted_text=LABELS) == "list"


# ─── 4 · 塌陷判决：一段话不算"读取器丢了字" ────────────────────────────

def test_a_prose_answer_is_not_reported_as_a_collapsed_extract():
    """163 的形状：正文 2,844 / 整页 8,593，缺的 5,700 是导航与侧栏。

    旧判据把这一发写成 `content_ok=false` 并附一句"the words ARE in the HTML,
    dhole's reader lost them" —— 读取器没丢，是分母里大半不是正文。豁免要两条：
    答案是一段话，**且**这页的可见字大半是链接标签（163 实测 0.40）。
    """
    prose = PROSE * 12
    ok = ResponseModel(status=200, content=[prose], url="https://portal.test/a.html",
                       fetcher_used="http", content_type="text/html; charset=utf-8",
                       total_extracted_chars=len(prose), visible_text_chars=8_593)
    ok._dom_label_share = 0.40
    assert extraction_collapsed(ok) is False


def test_a_prose_answer_on_a_clean_article_page_is_still_collapsed():
    """反面锁一：一篇干净文章的链接占比是 0.01–0.05 —— 同样的 33% 覆盖率照旧报塌陷。

    这一支才是"正文真被截断"的原形：答案是一段话，但这页看不出是门户，所以
    5,700 字的差不能被解释成导航。
    """
    prose = PROSE * 12
    thin = ResponseModel(status=200, content=[prose], url="https://blog.test/a.html",
                         fetcher_used="http", content_type="text/html; charset=utf-8",
                         total_extracted_chars=len(prose), visible_text_chars=8_593)
    thin._dom_label_share = 0.04
    assert extraction_collapsed(thin) is True


def test_the_prose_exemption_needs_page_evidence_not_just_a_shape():
    """反面锁二：DOM 没量过（`_dom_label_share` 默认 -1）时不许主张"分母是导航"。

    豁免是**关于页面的主张**，没有页面就没有主张。这一条同时护住"不拿答案的形状当
    通行证"：只凭 `answer_is_prose` 就放行，等于让任何一段被截断的正文自己证明自己完整。
    """
    prose = PROSE * 12
    unmeasured = ResponseModel(
        status=200, content=[prose], url="https://portal.test/a.html",
        fetcher_used="http", content_type="text/html; charset=utf-8",
        total_extracted_chars=len(prose), visible_text_chars=8_593)
    assert unmeasured._dom_label_share == -1
    assert extraction_collapsed(unmeasured) is True


def test_a_label_answer_is_still_reported_as_collapsed():
    """误伤侧：交回的是一串标签而整页有 6,000 字 —— 那才是真的丢。"""
    labels = LABELS
    thin = ResponseModel(status=200, content=[labels], url="https://portal.test/b.html",
                         fetcher_used="http", content_type="text/html; charset=utf-8",
                         total_extracted_chars=len(labels), visible_text_chars=6_000)
    assert extraction_collapsed(thin) is True


# ─── 5 · 补救：不拿正文换链接清单，但候选是正文时照旧接管 ──────────────

def _translate(monkeypatch, html: str, current: list[str], reclaimed: str):
    page = _page(html)
    monkeypatch.setattr("dhole_mcp.trafilatura_extractor.extract_with_trafilatura",
                        lambda *a, **k: current)
    monkeypatch.setattr("dhole_mcp.extractor.recover_a_lost_document",
                        lambda page, content, *a, **k: content)
    monkeypatch.setattr("dhole_mcp.extractor._collapse_boilerplate",
                        lambda content, *a, **k: content)
    monkeypatch.setattr("dhole_mcp.trafilatura_extractor.plain_markdown_from_html",
                        lambda h: reclaimed)

    from dhole_mcp.context import _ARG_NOTES
    notes: list[str] = []
    token = _ARG_NOTES.set(notes)
    try:
        result = _translate_response_body(page, "markdown", None, "http", 1.0)
    finally:
        _ARG_NOTES.reset(token)
    return result, notes


def _chrome_heavy_html() -> str:
    """一页"正文只占三成"的门户页：6 段正文 + 40 条推荐，可见字远超抽取的字。"""
    paras = "".join(f"<p>{PROSE}</p>" for _ in range(6))
    links = "".join(
        f'<li><a href="/r/{i}.html">推荐阅读第{i:02d}条：这里是一条很长的推荐标题</a></li>'
        for i in range(40))
    return (f"<html><body><div class='nav'><ul>{links}</ul></div>"
            f"<div class='body'>{paras}</div></body></html>")


def _prose_page_html(n: int = 20) -> str:
    """没有链接的正文页：走的是纯转换器那条路（`/longaside` 的形状）。"""
    return f"<html><body>{''.join(f'<p>{PROSE}</p>' for _ in range(n))}</body></html>"


def test_a_prose_answer_is_not_traded_for_a_link_list(monkeypatch):
    """163 的形状：正文被换成 117 条侧栏推荐，而调用方要的正是那段正文。"""
    html = _chrome_heavy_html()
    current = [PROSE * 6]
    result, notes = _translate(monkeypatch, html, current, reclaimed="* [x](y)\n" * 40)
    assert "\n".join(result.content) == current[0], "正文不该被清单顶掉"
    assert not any("rendered as a list" in n for n in notes), notes


def test_a_link_list_still_takes_over_when_the_answer_is_only_labels(monkeypatch):
    """误伤侧：索引页抽回来的是标签，清单仍然是更好的答案。"""
    html = _chrome_heavy_html()
    result, notes = _translate(monkeypatch, html, ["导航"] * 20,
                               reclaimed="* [第0条](https://list.test/n/0.html)\n" * 20)
    assert "* [" in "\n".join(result.content)
    assert any("rendered as a list" in n for n in notes), notes


def test_a_plain_converter_takeover_is_refused_when_the_candidate_is_chrome(monkeypatch):
    """东南网的形状：772 字的通稿被换成 37,206 字的整页 markdown（chrome 全带上）。"""
    html = _prose_page_html()
    current = [PROSE * 4]
    chrome = "\n".join(f"* [推荐阅读第{i:02d}条](https://portal.test/r/{i}.html)"
                       for i in range(300))
    result, notes = _translate(monkeypatch, html, current, reclaimed=chrome)
    assert "\n".join(result.content) == current[0], "整页 chrome 不该顶掉正文"
    assert not any("plain converter" in n for n in notes), notes


def test_a_plain_converter_takeover_still_happens_when_it_recovers_real_prose(monkeypatch):
    """`/longaside` 的形状：主内容过滤器丢掉了 aside 里的正文，补回来的仍是正文。"""
    html = _prose_page_html()
    current = [PROSE * 4]
    reclaimed = PROSE * 20
    result, notes = _translate(monkeypatch, html, current, reclaimed=reclaimed)
    assert "\n".join(result.content) == reclaimed
    assert any("plain converter" in n for n in notes), notes
