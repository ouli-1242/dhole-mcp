# -*- coding: utf-8 -*-
"""这一页自己有多少字要每发都量；拿回来的那堆字有没有一段能引用的话，也要说得出数。

两条实测（`review/2026-10-10-用户体验模拟报告.md` 场景 6/13）：

· `developer.aliyun.com/1686276` 回 **666 字全是导航**，`content_ok:true`；补抓时
  `extraction_type='text'` 回 **833 字仍是壳**，`content_ok` 照旧 true。报告的原话是
  「这里我第一次怀疑它的自检」。
· 两道闸中间那一段没人管：壳闸 `_JS_SHELL_MIN_TEXT_CHARS` 是 200 字，只抓"字极少"；
  混淆闸要求 `len >= 1000` 才去看"像不像话"。666/833 两头都不沾，于是它是成功。

而 `content_ok` 的公式（`quality._agent_hints`）当时**完全由 status 与 error 驱动**，
没有任何一条判据量过实质 —— 这不是阈值调错，是那一维根本不存在。

判据的形状：

1. **先给数再给判决**（3.3）：`visible_text_chars` 每个 HTML 响应都量并上线。从前只在
   抽得薄的那一支量，所以一份 3,000 字的塌陷页连对照数都没有。
2. 判决自带它背后的数（`substance_basis`），读者能复核而不是相信。
3. **缺席与"判过了且没问题"是两件事**：非 HTML / HEAD / 304 / 那些"这一行没有正文"的
   error（墙、壳、网络）留空，正常正文写 `ok`。`extraction_collapsed` 与
   `obfuscated_payload` 是例外 —— 那两句 error 说的就是正文，所以它们必须仍有判决。
4. 一条真目录（链接就是这页的内容）**不许**被翻闸 —— 只有"一堆短菜单碎片"才判 `nav_only`。
   阈值取在实测两簇的空带上（同一把尺：/nav 均行 2.3 字 / 站点根 3.6 字 / 真目录
   /collections 16.1 字 / 真正文 /article 36.9 字）。
5. 形状只有一位作者：`envelope.page_content_shape` 一次解析给列表判据与实质判据共用。
"""

from __future__ import annotations

import pytest

from dhole_mcp import wire
from dhole_mcp.envelope import main_content_run, page_content_shape, visible_text_chars
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (_body_line_shape, _detect_content_issue,
                               _with_agent_hints)
from dhole_mcp.server import _attach_dom_visible_chars

NAV_HTML = ("<html><body><nav>" + "".join(f"<a href='/{i}'>{w}</a>" for i, w in enumerate(
    ["首页", "关于", "登录", "我的账户", "购物车", "结算", "文档", "集合", "作品", "订阅"]))
    + "</nav><div id='app'></div></body></html>")
ARTICLE_HTML = ("<html><body><article>" + "".join(
    f"<p>第 {i} 段讲一个完整的道理，读起来是一段正文，句末有标点。</p>" for i in range(9))
    + "</article></body></html>")
INDEX_HTML = ("<html><body><ul>" + "".join(
    f"<li><a href='/work/{i}'>专题 {i}：一件值得点进去的完整标题</a> 更新于 2026-10-01</li>"
    for i in range(12)) + "</ul></body></html>")
JSONISH = "<html><body>{\"ok\": true}</body></html>"


def _read(html: str, *, body: list[str], content_type: str = "text/html",
          url: str = "https://e.test/p", **overrides) -> ResponseModel:
    """走真路径：先在 HTML 还在手边时量形（`_attach_dom_visible_chars`），再下判决。"""
    result = ResponseModel(**{"url": url, "status": 200, "content": body,
                              "extracted_type": "markdown", "content_type": content_type,
                              "total_size_bytes": len(html), "fetcher_used": "http",
                              **overrides})
    return _with_agent_hints(_attach_dom_visible_chars(result, html, url))


# ─── 1 · 3.3：每个 HTML 响应都有对照数 ───────────────────────────

def test_a_page_reports_its_own_visible_length_even_when_extraction_is_fat():
    """前值：抽出 >= 2,000 字时这一发根本不测，中型塌陷页连对照数都没有。"""
    body = ["第 %d 段是很长的一段正文，用来把抽回来的字数顶过从前那道两千字的探针闸。"
            % i * 120 for i in range(60)]
    out = _read(ARTICLE_HTML * 40, body=body)
    assert out.visible_text_chars > 0, "整页可见字数没有上线 —— 3.3 没落地"
    assert out.visible_text_chars == visible_text_chars(ARTICLE_HTML * 40)
    assert wire._wire_dump(out)["visible_text_chars"] == out.visible_text_chars


def test_a_non_html_response_pays_nothing_for_the_measurement():
    out = _read(JSONISH, body=['{"ok": true}'], content_type="application/json",
                url="https://e.test/api")
    assert out.visible_text_chars == 0
    assert "visible_text_chars" not in wire._wire_dump(out)


def test_the_collapse_branch_reads_the_shipped_number_not_a_private_copy():
    """`extraction_collapsed` 与 substance 必须读同一颗数，否则两处会说两种"这一页多少字"。"""
    html = "<html><body>" + "<p>这一段是页面上真实写着的正文，句末有标点。</p>" * 90 + "</body></html>"
    out = _read(html, body=["一点点抽回来的字"], url="https://e.test/thin")
    assert out.visible_text_chars == visible_text_chars(html)
    assert out.substance == "collapsed", out.substance_basis
    assert out.content_ok is False
    assert f"{out.visible_text_chars:,}" in out.substance_basis


# ─── 2 · 3.4：实质判决 ───────────────────────────────────────────

def test_a_body_of_menu_fragments_is_not_reported_as_content():
    """666 字那一格：两头旧闸都够不着，所以这一维必须自己存在。"""
    labels = ["首页", "关于", "登录", "我的账户", "购物车", "结算", "文档",
              "集合", "作品一", "作品二", "订阅", "验证", "外壳", "帮助"]
    body = ["\n".join(labels) for _ in range(1)]
    out = _read(NAV_HTML, body=body)
    assert out.substance == "nav_only", out.substance_basis
    assert out.content_ok is False, "菜单碎片又被说成成功了"
    assert "no prose" in out.summary, out.summary
    # 出路只指真换管路的两枚：渲染，或跟进链接（不是"再抓一次同样的"）
    assert "force_fetcher='stealthy'" in out.next_action, out.next_action
    assert "follow the links" in out.next_action


def test_the_verdict_carries_the_numbers_it_was_made_from():
    out = _read(NAV_HTML, body=["\n".join(["首页", "关于", "登录", "我的账户", "购物车", "结算"])])
    # R02-006 之后这颗读数多了一颗「句子行」—— 形状变了，前三颗的数值一位未动。
    lines, share, mean, _prose = _body_line_shape(out.content[0])
    assert out.substance_basis, "判了却不给数 —— 读者只能相信"
    assert str(lines) in out.substance_basis
    assert f"{share:.0%}" in out.substance_basis
    assert f"{mean:.1f}" in out.substance_basis


def test_a_link_index_stays_content_because_links_are_what_that_page_has():
    """翻闸的代价是把一次正确的回答报成失败：目录页的内容就是那些链接。"""
    out = _read(INDEX_HTML, body=["\n".join(
        f"[专题 {i}：一件值得点进去的完整标题](/work/{i}) 更新于 2026-10-01"
        for i in range(12))])
    assert out.substance == "ok", out.substance_basis
    assert out.content_ok is True


def test_a_real_short_article_is_content():
    out = _read(ARTICLE_HTML, body=["\n".join(
        f"第 {i} 段讲一个完整的道理，读起来是一段正文，句末有标点。" for i in range(9))])
    assert out.substance == "ok"
    assert out.content_ok is True


def test_no_content_on_the_page_is_not_invented_as_a_verdict():
    """error 那一支已经有自己的说法；这里不许再铸一个第二判决。"""
    out = _read(NAV_HTML, body=[], status=500, error="server_error: upstream said 500")
    assert out.substance == ""
    assert "substance" not in wire._wire_dump(out)


def test_a_probe_is_not_judged_for_prose():
    out = _read(NAV_HTML, body=[], url="https://e.test/h", fetcher_used="http")
    # HEAD/304 与错误行一样：正文为空由那一支解释，实质判决保持缺席
    assert out.substance == ""


def test_absent_means_not_judged_while_ok_means_judged_and_clean():
    """两种意思不能混 —— 那正是 R4 那一族谎的形状。"""
    judged = _read(ARTICLE_HTML, body=["\n".join(
        f"第 {i} 段讲一个完整的道理，读起来是一段正文，句末有标点。" for i in range(6))])
    unjudged = _read(JSONISH, body=['{"ok": true}'], content_type="application/json")
    assert judged.substance == "ok"
    assert unjudged.substance == ""
    assert wire._wire_dump(judged)["substance"] == "ok"
    assert "substance" not in wire._wire_dump(unjudged)


def test_a_body_too_short_to_have_a_shape_is_left_unjudged_not_clean():
    """一行带标点的话既不能证明是菜单，也不能证明是正文 —— 说"判过了且没问题"是替读者编结论。

    反向对照写在同一条里：**短正文落在字多的页上不是"没形状"，是 `collapsed`**（抽取器
    弄丢了字），那一支由比例闸先接走 —— 两支线不能互换，否则"这页只有两个字"与"这页有两
    千个字而我只拿到两个"在信封上就成了同一句话。
    """
    thin_page = "<html><body><p>答案：四十二。</p></body></html>"
    one_line = _read(thin_page, body=["答案：四十二。"])
    assert one_line.substance == "", one_line.substance_basis
    assert "substance" not in wire._wire_dump(one_line)
    assert one_line.content_ok is True, "没判过不等于判失败"

    rich = _read(ARTICLE_HTML * 30, body=["答案：四十二。"])
    assert rich.visible_text_chars > 4_000
    assert rich.substance == "collapsed", rich.substance_basis


def test_an_almost_empty_page_is_called_no_prose_not_clean():
    """靶站 /shell 实测：正文 2 字、整页可见 6 字，从前 substance 报 `ok`（判过了，有正文）。"""
    html = "<html><body><div id='app'>关于</div></body></html>"
    out = _read(html, body=["关于"])
    assert out.visible_text_chars < 200, out.visible_text_chars
    assert out.substance == "nav_only", out.substance_basis
    assert out.content_ok is False
    assert "no prose" in out.summary, out.summary


def test_a_small_link_index_is_not_a_shell_however_thin_the_page_is():
    """误报侧：靶站 /collections 实测整页只有 117 个可见字、15 行全是链接标签。

    那一页的内容**就是**那些链接。第一版"整页几乎是空的就判 nav_only"把它翻了闸 ——
    行数够一族门的情形归比例闸判，这一支只管一两个标签字的那种壳。
    """
    html = ("<html><body><ul>" + "".join(
        f"<li><a href='/w/{i}'>专题 {i}</a>{i * 3} 件</li>" for i in range(12))
        + "</ul></body></html>")
    out = _read(html, body=["\n".join(
        f"[专题 {i}]('/w/{i}'){i * 3} 件" for i in range(12))])
    assert out.visible_text_chars < 200, out.visible_text_chars
    assert out.substance == "ok", out.substance_basis
    assert out.content_ok is True


def test_the_packed_payload_row_gets_the_no_words_verdict_it_already_declares():
    """`obfuscated_payload` 那句 error 说的就是正文：这一维必须跟着判，否则 `no_words` 取不到。

    前值（2026-10-10 活体复验，靶站 `/shell/obfuscated`）：正文 9,017 字、error 已写明
    `obfuscated_payload`，而 `substance` 报 `ok` —— 那一版的分支写着 `len(body) < 1_000`，
    恰好把混淆闸判的那一类（长正文）整个漏掉。
    """
    html = "<html><body>" + "<div>" + "x"*40 + "</div>" * 400 + "</body></html>"
    packed = "QWxhZGRpbjpPcGVuUTFzZWNyZXQ=" * 400   # 无词的长载荷
    result = ResponseModel(url="https://e.test/obf", status=200, content=[packed],
                           extracted_type="markdown", content_type="text/html",
                           total_size_bytes=len(html), fetcher_used="http",
                           error="obfuscated_payload: the body is packed script, not prose")
    out = _with_agent_hints(_attach_dom_visible_chars(result, html, result.url))
    assert out.substance == "no_words", (out.substance, out.substance_basis)
    assert f"{len(packed):,}" in out.substance_basis
    assert "no words in the body" in out.summary, out.summary


# ─── 3 · 阈值站在实测的空带上 ───────────────────────────────────

def test_the_collapse_verdict_is_reachable_through_the_error_it_self_sets():
    """线上那一发的形状：`error` 先被 `_detect_content_issue` 写下，判决后算。

    这条是补的——前一版只测了"没带 error 的塌陷页"，于是 `collapsed` 这个取值在**声明里
    存在、在线上取不到**（靶站 `/longaside` 实测：带着 `extraction_collapsed` 回来，
    `substance` 是空）。门若改回"带 error 一律不判"，这一条红。
    """
    html = ("<html><body>" + "<p>这一段是页面上真实写着的正文，句末有标点。</p>" * 90
            + "</body></html>")
    result = ResponseModel(url="https://e.test/longaside", status=200,
                           content=["一点点抽回来的字"], extracted_type="markdown",
                           content_type="text/html", total_size_bytes=len(html),
                           fetcher_used="http")
    _attach_dom_visible_chars(result, html, result.url)
    issue = _detect_content_issue(result)          # 生产顺序：先写 error，再算 hint
    result.error = issue
    result.content_ok = not issue
    out = _with_agent_hints(result)
    assert out.error.startswith("extraction_collapsed"), out.error
    assert out.substance == "collapsed", (out.substance, out.error)
    assert "words lost by the extractor" in out.summary, out.summary


def test_a_wall_error_still_explains_itself_without_a_second_verdict():
    """门只放过"说正文"的那两句：墙那一句已经有自己的说法。"""
    result = ResponseModel(url="https://e.test/login", status=200,
                           content=["请登录\n用户名\n密码\n登录"],
                           extracted_type="markdown", content_type="text/html",
                           total_size_bytes=400, fetcher_used="http",
                           error="auth_wall_detected: page is a sign-in form")
    out = _with_agent_hints(_attach_dom_visible_chars(result, "<html></html>", result.url))
    assert out.substance == ""
    assert "substance" not in wire._wire_dump(out)


def test_the_nav_only_advice_does_not_ask_for_the_tier_that_already_answered():
    """渲染档已经跑过而答案就是这堆碎片时，再劝人去那一档就是 1.4 那一刀要杀的那句谎。"""
    out = _read(NAV_HTML, body=["\n".join(
        ["首页", "关于", "登录", "我的账户", "购物车", "结算"])],
        fetcher_used="stealthy")
    assert out.substance == "nav_only", out.substance_basis
    assert "force_fetcher='stealthy'" not in out.next_action, out.next_action
    assert "already rendered" in out.next_action or "not one a browser helps" in out.next_action
    assert "Do not cite this body" in out.next_action


def test_the_threshold_sits_in_the_measured_gap_between_menus_and_indexes():
    menus = ["\n".join(["首页", "关于", "登录", "我的账户", "购物车", "结算"]),
             "\n".join(["dhole 模拟测试靶站", "登录", "账户", "结算", "二维码", "验证码"])]
    indexes = ["\n".join(f"[专题 {i}](/work/{i}) 三件作品，更新于本周" for i in range(15))]
    for body in menus:
        lines, share, mean, _prose = _body_line_shape(body)
        assert lines >= 4 and share >= 0.8 and mean <= 8, (lines, share, mean)
    for body in indexes:
        lines, share, mean, _prose = _body_line_shape(body)
        assert mean > 8, (lines, share, mean)


# ─── 4 · 形状只有一个作者 ───────────────────────────────────────

def test_the_list_gate_and_the_substance_gate_read_one_shape_function():
    from dhole_mcp import envelope
    share, run, area = page_content_shape(ARTICLE_HTML, "e.test")
    assert main_content_run(ARTICLE_HTML, "e.test") == run
    assert envelope._list_shape(ARTICLE_HTML, "e.test") == (share, run), (
        "两处各解析各的、各数各的 —— 同一件事又有了第二个作者")
    assert area > 0 and run == visible_text_chars(ARTICLE_HTML) or area


@pytest.mark.parametrize("html,name", [(NAV_HTML, "nav"), (INDEX_HTML, "index"),
                                       (ARTICLE_HTML, "article")])
def test_the_shape_measurement_never_raises_on_any_page(html, name):
    assert page_content_shape(html, "e.test")[1] >= 0
    assert main_content_run(html, "e.test") >= 0


# ─── 5 · "这页没有话"是一句关于页面的断言，它要正面证据 ──────────────

# 实测的第二个假阳性（`test_write_gate_moves_out_of_band_when_client_elicts.py` 的回环
# POST）：一个 API 回执只回 2 字节 `ok`，**不发 Content-Type**。三个形状读数与一个真空壳
# 页面完全同值（1 行 / 100% 标签行 / 整页 2 字），于是那一版把它判成 `nav_only`、
# `content_ok` 翻 false，`recover_with` 还教调用者"这页没字，换个源"—— 而那 2 字节就是
# 这次 POST 的全部正当载荷。分不清的时候下判就是把载荷当页面，所以这一维只在类型写着
# html 时下判（`extractor.served_as_page`），留空是"没判"而不是"判过且没问题"。

_SHELL_HTML = "<!doctype html><html><body><a href='/x'>关于</a></body></html>"


def test_a_payload_not_served_as_a_page_earns_no_page_verdict():
    out = _read("ok", body=["ok"], content_type="", url="https://e.test/checkout")
    assert out.visible_text_chars == 2, "数照量（3.3），判决不动"
    assert out.substance == "" and out.substance_basis == ""
    assert out.content_ok is True
    dumped = wire._wire_dump(out)
    assert "substance" not in dumped, "没判的维度不许占字节，更不许让读者以为判过了"


def test_the_same_three_readings_served_as_html_are_still_judged():
    shell = _read(_SHELL_HTML, body=["关于"], content_type="text/html")
    ack = _read("ok", body=["ok"], content_type="")
    # 两发的形状读数一模一样 —— 分开它们的只有"这是一页"这条正面证据。
    from dhole_mcp.quality import _body_line_shape
    assert _body_line_shape("关于") == _body_line_shape("ok")
    assert shell.visible_text_chars == ack.visible_text_chars
    assert shell.substance == "nav_only" and shell.content_ok is False
    assert ack.substance == ""
