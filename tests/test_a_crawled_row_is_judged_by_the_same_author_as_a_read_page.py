# -*- coding: utf-8 -*-
"""爬取的一行与读取的那一封用**同一位作者**判正文形状，而它们量的是各自的正文。

起因是一条实测（2026-10-10，本地靶站）：`/nav` 走 `web_read` 交回 39 个光秃秃的标签字
（没有链接可跟）→ 3.4 判 `nav_only`、`content_ok=false`；同一 URL 走 `web_crawl` 的那一行
交回的是**一条链接清单**（`[首页](/0)` × 10，509B html）→ `content_ok=true`。

第一反应是"又是一个工具两种真相"。量过之后才知道两种说法各自为真：读取那一发抽回来的是
菜单文字本身（抽取器把 href 丢了），那一行确实没有可引用的实质；爬取那一行交的是可以跟进
的链接，而"这一页的内容就是那些链接"正是目录页的正当形状 —— 同一句话在 `/collections`
上两边都判 `ok`。**要对齐的是判决的作者，不是把两种不同的正文假装成一种。**

所以这一族钉三件事：
1. 形状判据只有 `quality.body_substance` 一位作者，读取与爬取都调它（不许第二次写那三条阈值）；
2. 爬取的一行**真的**会被它翻闸：正文是一堆无链接的碎片时（`fallback` 那一支抽的就是整页
   可见字，正是报告里 developer.aliyun.com 那 666 字的形状），`content_ok=false` 且理由
   写在这一行唯一的抬头里（被拒的一行必须说得出为什么）；
3. 带链接的清单不许被翻 —— 那是目录，不是菜单。
"""

from __future__ import annotations

from dhole_mcp import crawl
from dhole_mcp.envelope import visible_text_chars
from dhole_mcp.quality import body_substance

MENU_HTML = ("<html><body><nav>" + "".join(f"<a href='/{i}'>{w}</a>" for i, w in enumerate(
    ["首页", "关于", "登录", "我的账户", "购物车", "结算", "文档", "集合", "作品", "订阅"]))
    + "</div><div id='app'></div></body></html>")
MENU_TEXT = "\n".join(["首页", "关于", "登录", "我的账户", "购物车", "结算", "文档",
                       "集合", "作品一", "订阅", "验证", "外壳"])
INDEX_LIST_MD = "\n".join(f"[专题 {i}：一件值得点进去的完整标题](/work/{i}) {i*3} 件"
                          for i in range(12))


def _row(md: str, html: str, kind: str) -> dict:
    """走真的那一行：`_measure_row` 量，`_row_verdict` 判，`_measure_note` 说。"""
    stats: dict = {}
    crawl._measure_row(stats, html, md, kind, "https://e.test/p", "https://e.test/p")
    stats.pop("item_set", None)
    return {"stats": stats, "ok": crawl._row_verdict(kind, md, stats),
            "note": crawl._measure_note(stats)}


def test_the_shape_author_is_shared_not_reimplemented():
    """爬取那一行不许有自己的三份阈值：判决与读取侧必须是同一次函数调用的产物。"""
    html = MENU_HTML
    stats: dict = {}
    crawl._measure_row(stats, html, MENU_TEXT, "fallback",
                       "https://e.test/p", "https://e.test/p")
    direct = body_substance(MENU_TEXT, visible_text_chars(html))
    assert (stats.get("substance"), stats.get("substance_basis")) == direct


def test_a_row_whose_body_is_bare_labels_is_refused_and_says_why():
    got = _row(MENU_TEXT, MENU_HTML, "fallback")
    assert got["stats"]["substance"] == "nav_only", got["stats"]
    assert got["ok"] is False, "一堆菜单碎片又被算成拿到了内容"
    assert "no prose in this row" in got["note"], got["note"]
    # 理由必须落在**这一行**有的那两格里（爬取的行没有 next_action）
    assert "force_fetcher='stealthy'" in got["note"] or "follow the links" in got["note"]


def test_a_link_list_is_content_because_there_the_links_are_the_content():
    """误报侧：目录页在爬取里交回的就是链接清单，这一行不许被同一把尺翻掉。"""
    html = ("<html><body><ul>" + "".join(
        f"<li><a href='/work/{i}'>专题 {i}：一件值得点进去的完整标题</a> {i*3} 件</li>"
        for i in range(12)) + "</ul></body></html>")
    got = _row(INDEX_LIST_MD, html, "list")
    assert got["stats"].get("substance") == "ok", got["stats"]
    assert got["ok"] is True


def test_a_row_with_prose_keeps_the_verdict_it_had():
    md = "\n".join(f"第 {i} 段讲一个完整的道理，读起来是一段正文，句末有标点。"
                   for i in range(9))
    html = "<html><body>" + "".join(f"<p>{ln}</p>" for ln in md.split("\n")) + "</body></html>"
    got = _row(md, html, "article")
    assert got["stats"]["substance"] == "ok"
    assert got["ok"] is True


def test_a_row_too_thin_to_have_an_opinion_is_left_unjudged_not_flipped():
    """一两个标签字的行没有形状判决可给 —— 留空不等于 `ok`，也不许因此翻闸（翻闸另有依据）。"""
    got = _row("关于", "<html><body><div id='app'>关于</div></body></html>", "fallback")
    assert got["stats"]["substance"] == "nav_only", got["stats"]
    # 2 个字符本来就过不了 `len(md) >= 50` 那把旧尺，这一行被拒的理由是**空**而不是判决
    assert got["ok"] is False


def test_the_collapse_branch_still_wins_when_both_apply():
    """塌陷与形状同在一行上时，理由要说塌陷那一句（它给出的出路不同：换 extraction_type）。"""
    html = "<html><body>" + "<p>这一段是页面上真实写着的正文，句末有标点。</p>" * 90 + "</body></html>"
    stats: dict = {}
    crawl._measure_row(stats, html, "一点点抽回来的字", "article",
                       "https://e.test/p", "https://e.test/p")
    assert stats.get("extraction_collapsed") is True
    assert crawl._row_verdict("article", "一点点抽回来的字", stats) is False
    assert "extraction collapsed" in crawl._measure_note(stats)


def test_a_short_but_real_body_is_not_flipped_by_a_floor_read_does_not_have():
    """靶站 `/work/1`：同一页 `web_read` 回 content_ok=true（39 字、一句正文），
    `web_crawl` 却因 `len(md) >= 50` 那把读取侧根本没有的长度尺翻成 false，且不给理由。
    同一 URL 不许一页一个样读成内容、爬成 shell —— 这里钉住两位作者的对齐。"""
    body = "作品 1\n街景\n夜景\n人像\n第 1 篇的独有正文，用来证明这一列真的每页不同。"
    got = _row(body, f"<html><body><h1>作品 1</h1><p>{body}</p></body></html>", "article")
    assert got["stats"].get("substance") != "nav_only"
    assert got["ok"] is True, got
