"""test_envelope.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from dhole_mcp.envelope import detect_page_type, classify_source, compute_freshness, page_type_from_error, _paywall_evidence
import pytest
from dhole_mcp import wire
from dhole_mcp.envelope import main_content_run, page_content_shape, visible_text_chars
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _body_line_shape, _detect_content_issue, _with_agent_hints
from dhole_mcp.server import _attach_dom_visible_chars
import inspect
from dhole_mcp import guide, quality
from dhole_mcp.quality import _agent_hints, _COLLAPSE_RECOVERY, _diagnose, _DIAGNOSIS_PREFIXES, classify_error_text
from dhole_mcp.server import _clip_reason, HOLE_REASON_CAP


# ============================================================================
# 来源：test_envelope.py
# ============================================================================
# Envelope tests: page-type detection, source authority, freshness, paywall
# evidence parser (HTMLParser-based, PR #9 fix).

# ─── Page type detection ───────────────────────────────────────────

class TestDetectPageType:

    def test_pdf_from_content_type(self):
        assert detect_page_type("", "https://x.com", "application/pdf") == "pdf"

    def test_json_from_content_type(self):
        assert detect_page_type("", "https://x.com", "application/json") == "json"

    def test_image_from_content_type(self):
        assert detect_page_type("", "https://x.com", "image/png") == "image"

    def test_empty_html_returns_unknown(self):
        assert detect_page_type("", "https://x.com", "text/html") == "unknown"

    def test_meta_refresh_is_redirect(self):
        html = '<meta http-equiv="refresh" content="0;url=https://x.com/new">'
        assert detect_page_type(html, "https://x.com", "text/html") == "redirect"

    def test_js_location_assignment_with_low_text_is_redirect(self):
        html = '<script>location.href="/new"</script><body></body>'
        assert detect_page_type(html, "https://x.com", "text/html", 100) == "redirect"

    def test_article_tag_detected(self):
        html = '<article><p>Real article content here that is long enough.</p></article>'
        assert detect_page_type(html, "https://example.com", "text/html", 80) == "article"

    # 17.0 §3 第 28 条：page_type 收窄为「只讲结构」。qa / forum / docs 说的是
    # 「这是个什么站」，那句话 source_type 已经在说（classify_source 回 qa / forum /
    # docs-site，见本文件下方的 TestClassifySource 三条）；两个字段各说一遍就是两处
    # 会互相矛盾的事实。这里钉住删除后的形状：靠站方软件名撑起来的壳页既没有正文结构
    # 也没有列表结构，回 unknown。
    def test_qa_markup_is_not_a_page_type(self):
        html = '<div class="question">How to X?</div><div class="answer">Do Y.</div>'
        assert detect_page_type(
            html, "https://stackoverflow.com/q/1", "text/html", 80) == "unknown"

    def test_forum_markup_is_not_a_page_type(self):
        html = '<div class="post-body">Forum post text.</div>'
        assert detect_page_type(
            html, "https://forum.example.com/t/1", "text/html", 80) == "unknown"

    def test_docs_markup_is_not_a_page_type(self):
        html = '<div class="md-nav">Docs sidebar</div><div class="rst-content">Content</div>'
        assert detect_page_type(
            html, "https://docs.example.com", "text/html", 80) == "unknown"

    def test_list_page_detected_many_links(self):
        # 25 same-domain links with little text -> list
        links = "".join(f'<a href="/page/{i}">Link {i}</a>' for i in range(25))
        html = f'<div>{links}</div>'
        assert detect_page_type(html, "https://example.com", "text/html", 200) == "list"

    def test_article_with_many_links_not_list(self):
        # <article> with many links -> article, not list
        links = "".join(f'<a href="/page/{i}">Link {i}</a>' for i in range(25))
        html = f'<article><p>Article text. {links}</p></article>'
        assert detect_page_type(html, "https://example.com", "text/html", 2000) == "article"


# ─── page_type 的词表：代码能回的值 == 描述里承诺的值 ─────────────────

_RETURNED = re.compile(r'return "([a-z_]+)"')
_ASSIGNED = re.compile(r'result\.page_type = "([a-z_]+)"')


def _page_types_the_code_can_produce() -> set:
    """每一个 page_type 产出点，从源码里读而不是手抄一份清单。

    手抄的第二份清单就是下一次漂移的来源（同一个理由见
    test_tool_descriptions._response_field_names）。加一个新值的人会在这里
    被要求同时写进描述；删一个值的人会被要求把描述里的也删掉。
    """
    import inspect

    from dhole_mcp import envelope, server

    out = set(_RETURNED.findall(inspect.getsource(envelope.detect_page_type)))
    out |= set(_RETURNED.findall(inspect.getsource(envelope.page_type_from_error)))
    out |= set(_ASSIGNED.findall(inspect.getsource(server._apply_envelope)))
    return out


def _page_types_named_in_the_description() -> set:
    from dhole_mcp.server import ResponseModel

    desc = ResponseModel.model_fields["page_type"].description
    return set(desc.split(": ", 1)[1].split(".", 1)[0].split("|"))


def test_page_type_vocabulary_matches_its_documentation():
    produced, named = _page_types_the_code_can_produce(), _page_types_named_in_the_description()
    assert produced, "推导空了：这条守卫会退化成永远通过"
    assert named - produced == set(), (
        f"描述承诺了代码回不出的值：{sorted(named - produced)}"
    )
    assert produced - named == set(), (
        f"代码会回描述没解释的值：{sorted(produced - named)}"
    )


def test_page_type_no_longer_names_a_site_kind():
    """§3 第 28 条删掉的三个值不许从任何产出点漂回来。"""
    from dhole_mcp.server import _TTL_BY_PAGE_TYPE

    produced = _page_types_the_code_can_produce()
    assert not ({"qa", "forum", "docs"} & produced)
    assert not ({"qa", "forum", "docs"} & _page_types_named_in_the_description())
    # 按 page_type 定缓存时长的那张表也在产出点里：一个代码回不出的键不是规则，
    # 是一段长得像规则的废代码 ——「docs」那行正是这样在值删掉之后留下的。
    assert set(_TTL_BY_PAGE_TYPE) <= produced, (
        f"缓存 TTL 表里有回不出的 page_type：{sorted(set(_TTL_BY_PAGE_TYPE) - produced)}"
    )


# ─── Paywall detection (PR #9 fix) ─────────────────────────────────

class TestPaywallDetection:
    """The bare 'paywall' token must NOT trigger paywall classification.
    Visible subscription phrases and active structural attributes must."""

    def test_real_paywall_phrase_detected(self):
        html = "<main><p>Subscribe to continue reading this article.</p></main>"
        assert detect_page_type(html, "https://x.com", "text/html", 80) == "paywall"

    def test_readme_login_paywall_link_not_paywall(self):
        html = '<article><a href="/login/paywall">login/paywall</a><p>Project README.</p></article>'
        assert detect_page_type(html, "https://github.com/x/y", "text/html", 80) != "paywall"

    def test_paywall_detector_explanation_not_paywall(self):
        html = "<article><p>The paywall detector labels subscription prompts.</p></article>"
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_paywall_phrase_in_script_not_detected(self):
        html = '<script>const x = "subscribe to continue";</script><article><p>Docs.</p></article>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_paywall_phrase_in_style_not_detected(self):
        html = '<style>.x::after { content: "subscribe to read"; }</style><article><p>Docs.</p></article>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_inline_split_paywall_phrase_detected(self):
        # "Sub<strong>scribe</strong> to continue" must still match visible text
        html = "<article>Sub<strong>scribe</strong> to continue</article>"
        assert detect_page_type(html, "https://x.com", "text/html", 80) == "paywall"

    def test_char_reference_paywall_phrase_detected(self):
        html = "<article>subscr&#105;be to continue</article>"
        assert detect_page_type(html, "https://x.com", "text/html", 80) == "paywall"

    def test_words_split_across_block_boundaries_not_paywall(self):
        html = "<article><p>Sub</p><p>scribe to continue.</p></article>"
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_active_data_paywall_attribute_detected(self):
        html = '<main><div data-paywall="true">Subscription required.</div></main>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) == "paywall"

    def test_data_paywall_without_value_detected(self):
        html = '<aside data-content-gate>Subscription required.</aside>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) == "paywall"

    def test_disabled_data_paywall_not_detected(self):
        html = '<main><div data-paywall="false">Public content.</div></main>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_zero_value_data_paywall_not_detected(self):
        html = '<main><div data-paywall="0">Public content.</div></main>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_data_paywall_in_template_not_detected(self):
        html = '<template><div data-paywall="true">Hidden</div></template><article><p>Docs.</p></article>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_data_paywall_in_noscript_not_detected(self):
        html = '<noscript><div data-paywall="true">Hidden</div></noscript><article><p>Docs.</p></article>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_prefixed_attribute_not_detected(self):
        html = '<div x-data-paywall="true">Public.</div>'
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"

    def test_metadata_tags_ignored_for_attributes(self):
        html = ('<meta data-paywall="true" content="docs">'
                '<link data-content-gate="active">'
                '<base data-subscription-wall="true">'
                '<article><p>Docs.</p></article>')
        assert detect_page_type(html, "https://x.com", "text/html", 80) != "paywall"


# ─── _paywall_evidence (direct HTMLParser test) ────────────────────

class TestPaywallEvidence:

    def test_returns_visible_text(self):
        text, has_attr = _paywall_evidence("<article><p>Hello world</p></article>")
        assert "hello world" in text.lower()
        assert has_attr is False

    def test_excludes_script_content(self):
        text, _ = _paywall_evidence("<script>alert('secret')</script><p>visible</p>")
        assert "secret" not in text
        assert "visible" in text

    def test_excludes_style_content(self):
        text, _ = _paywall_evidence("<style>.x{color:red}</style><p>visible</p>")
        assert "color:red" not in text
        assert "visible" in text

    def test_detects_active_attribute(self):
        _, has_attr = _paywall_evidence('<div data-paywall="true">x</div>')
        assert has_attr is True

    def test_detects_attribute_without_value(self):
        _, has_attr = _paywall_evidence('<div data-content-gate>x</div>')
        assert has_attr is True

    def test_false_value_attribute_not_active(self):
        _, has_attr = _paywall_evidence('<div data-paywall="false">x</div>')
        assert has_attr is False

    def test_malformed_html_does_not_crash(self):
        text, has_attr = _paywall_evidence("<div><p>unclosed")
        assert isinstance(text, str)
        assert isinstance(has_attr, bool)


# ─── Source authority classification ──────────────────────────────

class TestClassifySource:

    def test_github_is_official(self):
        st, off = classify_source("https://github.com/ouli-1242/dhole-mcp")
        assert st == "github" and off is True

    def test_github_raw_is_official(self):
        st, off = classify_source("https://raw.githubusercontent.com/user/repo/main/file")
        assert st == "github" and off is True

    def test_github_pages_is_official(self):
        st, off = classify_source("https://user.github.io/project/")
        assert st == "github" and off is True

    def test_gov_is_official(self):
        st, off = classify_source("https://www.nasa.gov/mission")
        assert st == "gov" and off is True

    def test_edu_is_official(self):
        st, off = classify_source("https://www.mit.edu/research")
        assert st == "edu" and off is True

    def test_docs_subdomain_is_not_official(self):
        """docs.* is a SHAPE signal, not authority: any site can name a subdomain
        "docs.", so it must not come back as is_official."""
        st, off = classify_source("https://docs.python.org/3/")
        assert st == "docs-site" and off is False

    def test_developer_subdomain_is_not_official(self):
        st, off = classify_source("https://developer.mozilla.org/en-US/")
        assert st == "docs-site" and off is False

    def test_gov_inside_an_attacker_domain_is_not_official(self):
        """Regression: this used to test ".gov." in host, so any registrable
        domain could mint a gov/is_official label for itself."""
        st, off = classify_source("https://foo.gov.attacker.com/a")
        assert off is False
        assert st != "gov"

    def test_gov_under_a_third_party_tld_is_not_official(self):
        for url in ("https://evil.gov.com/x", "https://gov.example.net/x"):
            st, off = classify_source(url)
            assert off is False, url
            assert st != "gov", url

    def test_cc_gov_domains_are_official(self):
        """Registry-controlled ccTLD forms (label pair reserved by the registry)."""
        for url in ("https://www.gov.uk/x", "https://servicos.gov.br/x", "https://www.gov.cn/x"):
            st, off = classify_source(url)
            assert (st, off) == ("gov", True), url

    def test_subdomain_of_a_gov_domain_is_official(self):
        st, off = classify_source("https://data.nasa.gov/mission")
        assert st == "gov" and off is True

    def test_stackoverflow_is_qa_not_official(self):
        st, off = classify_source("https://stackoverflow.com/q/123")
        assert st == "qa" and off is False

    def test_reddit_is_forum(self):
        st, off = classify_source("https://www.reddit.com/r/python")
        assert st == "forum" and off is False

    def test_medium_is_blog(self):
        st, off = classify_source("https://medium.com/@user/post")
        assert st == "blog" and off is False

    def test_known_news_domain(self):
        st, off = classify_source("https://www.bbc.com/news/world")
        assert st == "news" and off is False

    def test_unknown_domain(self):
        st, off = classify_source("https://random-site.example.com/page")
        assert st == "unknown" and off is False

    def test_empty_url(self):
        st, off = classify_source("")
        assert st == "unknown" and off is False

    def test_url_with_userinfo_and_port(self):
        st, off = classify_source("https://user:pass@github.com:443/repo")
        assert st == "github" and off is True


# ─── Freshness ─────────────────────────────────────────────────────

class TestFreshness:

    def test_recent_content_not_stale(self):
        meta = {"published_time": "2026-07-01T00:00:00Z"}
        age, stale = compute_freshness(meta, "2026-07-22T00:00:00Z")
        assert age == 21
        assert stale is False

    def test_old_content_is_stale(self):
        meta = {"published_time": "2020-01-01T00:00:00Z"}
        age, stale = compute_freshness(meta, "2026-07-22T00:00:00Z")
        assert stale is True
        assert age > 365

    def test_modified_date_preferred_over_published(self):
        meta = {
            "published_time": "2020-01-01T00:00:00Z",
            "modified_time": "2026-07-20T00:00:00Z",
        }
        age, stale = compute_freshness(meta, "2026-07-22T00:00:00Z")
        assert age == 2
        assert stale is False

    def test_no_date_returns_none(self):
        """BUG-2: -1 as the "unknown" sentinel reads as a negative age."""
        age, stale = compute_freshness({}, "2026-07-22T00:00:00Z")
        assert age is None
        assert stale is False

    def test_future_date_returns_none(self):
        meta = {"published_time": "2030-01-01T00:00:00Z"}
        age, stale = compute_freshness(meta, "2026-07-22T00:00:00Z")
        assert age is None
        assert stale is False

    def test_compact_date_format(self):
        meta = {"date": "20260701"}
        age, stale = compute_freshness(meta, "2026-07-22T00:00:00Z")
        assert age == 21

    def test_no_fetched_at_falls_back_to_today(self):
        # Date relative to today so the test never rots: no fetched_at means
        # compute_freshness falls back to now(UTC), so age is exactly the delta.
        days_ago = 5
        published = (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat() + "T00:00:00Z"
        meta = {"published_time": published}
        age, stale = compute_freshness(meta, "")
        assert days_ago - 1 <= age <= days_ago + 1
        assert stale is False


# ─── page_type_from_error ─────────────────────────────────────────

class TestPageTypeFromError:

    def test_js_shell_error(self):
        assert page_type_from_error("js_shell_detected: page requires JS") == "js_shell"

    def test_auth_required_error(self):
        assert page_type_from_error("auth_required: login needed") == "auth_wall"

    def test_geo_redirect_error(self):
        assert page_type_from_error("geo_redirect_detected: region selector") == "redirect"

    def test_empty_error_returns_empty(self):
        assert page_type_from_error("") == ""

    def test_unrelated_error_returns_empty(self):
        assert page_type_from_error("network_error: timeout") == ""


# ============================================================================
# 来源：test_a_thin_body_says_whether_it_holds_any_prose.py
# ============================================================================
# 这一页自己有多少字要每发都量；拿回来的那堆字有没有一段能引用的话，也要说得出数。

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


def test_a_bare_link_whose_url_has_dots_is_not_a_sentence():
    """同一具壳，抽取器换了版本后交回的是 `[关于](http://127.0.0.1:8399/about)`。

    33 个字符、URL 里两颗点 —— 形状读数把它记成一句散文（`prose=1`），于是 `nav_only`
    那一支的门根本走不进去，一具整页只有几个可见字的壳被判成 `content_ok=true`。
    2026-10-20 在 trafilatura 2.3.1 下实测到（本机 2.2.0 不把页脚那枚链接吐进正文，
    洞一直藏着）。**URL 不是页面说出口的话**：判形状数的是摆在页面上的字，
    而"这一行交回来多少字符"那把尺照旧数交出去的字节 —— 两把尺各有各的作者。
    """
    html = ("<html><body><div id='root'></div>"
            "<a href='http://127.0.0.1:8399/about'>关于</a></body></html>")
    out = _read(html, body=["[关于](http://127.0.0.1:8399/about)"])
    assert out.visible_text_chars < 200, out.visible_text_chars
    assert out.substance == "nav_only", out.substance_basis
    assert out.content_ok is False


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


# ============================================================================
# 来源：test_a_collapsed_page_offers_only_the_doors_that_change_the_reader.py
# ============================================================================
# `extraction_collapsed` 的出路只有一个作者，而它只推荐真能换一条管路的那几枚。

URL = "https://example.com/travel"

#: 过 dhole 读取器的那几枚 —— 塌陷页换它们不是出路。
READER_VALUES = frozenset({"markdown", "text", "article"})
#: 绕开读取器的那两条路。
SKIP_THE_READER = frozenset({"html"}) | {"schema="}


def _collapsed(**updates) -> ResponseModel:
    """现场形状：静态列表页，DOM 有 1,374 个可见字符而抽取只回 176。

    `visible_text_chars`（3.3 起上线的公开字段）是 HTML 还在手边时量好的（见 `_attach_dom_visible_chars`），
    判据只读它，所以这里直接摆数 —— 与
    `test_a_list_page_is_not_called_a_js_shell.py` §2 用的是同一个形状。

    `error` 与 `content_ok` 按**产地**的写法补齐（`check_content_quality` 把
    `_detect_content_issue` 的判决抄进 `error`，server 再把 `content_ok` 翻成 False）：
    本文件量的是三个落点的话术，而它们读的都是装配完的那一层 —— 留 `error` 空着的话，
    `_agent_hints` 会走 page_type 那一支（实测先撞上过一次：塌陷的一张 list 页被劝去
    `web_crawl`，正是同一条管路）。
    """
    base = dict(status=200, content=["£45.17 In stock " * 11], url=URL,
                fetcher_used="http", content_type="text/html",
                total_size_bytes=48_000, page_type="list")
    base.update(updates)
    result = ResponseModel(**base)
    result.visible_text_chars = 1_374
    issue = _detect_content_issue(result)
    result.error = issue
    result.content_ok = not issue
    return result


def _prose(result: ResponseModel) -> str:
    return _detect_content_issue(result)


def _hint(result: ResponseModel) -> str:
    return _agent_hints(result)[1]


def _recover(result: ResponseModel) -> str:
    return (_diagnose(result) or {}).get("recover_with", "")


# ─── 1 · 一个作者，三个落点 ─────────────────────────────────────

def test_the_three_landing_points_quote_the_one_sentence():
    """散文、`next_action`、`recover_with` 三处都带着那颗常量**原文**。

    反向：把任意一处改回手写的一句同义话，这一条红 —— 它比的是同一串字符，不是口径。
    """
    r = _collapsed()
    prose = _prose(r)
    assert prose.startswith("extraction_collapsed:"), prose
    for where, text in (("error", prose), ("next_action", _hint(r)),
                        ("diagnosis.recover_with", _recover(r))):
        assert _COLLAPSE_RECOVERY in text, (where, text)


def test_the_table_holds_the_constant_itself_not_a_copy_of_it():
    """`_DIAGNOSIS_PREFIXES` 里那一格必须是同一个对象，不是第二次打字。

    `is` 是有意的：两张不同的字符串可以长得一模一样并各自演化，而 `row_diagnosis`
    （feed 的行、批量的行）读的正是这张表 —— 那里是这句话的第四个落点。
    """
    entry = [t for t in _DIAGNOSIS_PREFIXES if t[0] == "extraction_collapsed"]
    assert len(entry) == 1, entry
    assert entry[0][3] is _COLLAPSE_RECOVERY

def test_the_row_channel_says_the_same_thing_as_the_envelope():
    """没有 ResponseModel 的那条通道（`classify_error_text`）也读同一句。"""
    code, retryable, recover = classify_error_text(_prose(_collapsed()))
    assert code == "extraction_collapsed", code
    assert retryable is False, "换 extraction_type 是一次新调用，不是同一份的重发"
    assert recover is _COLLAPSE_RECOVERY


def test_nobody_retyped_the_advice_next_to_the_constant():
    """静态守卫：`quality.py` 里点名"不过读取器"那几枚取值的句子必须一处都没有。

    `schema_no_match` 那支（"fetch with extraction_type='html' to inspect the markup"）
    不在射程：它说的是另一件事（选择器没中，要看标记），而它推荐的正是绕开读取器的那枚。
    判据只认带引号的 `'markdown'` / `'text'` / `'article'` —— 常量讲道理时用无引号的
    markdown / text / article，一旦有人把它抄回带引号的形状，那就是把失败的那一步重新
    包装成出路（场景 3 的账）。
    """
    src = inspect.getsource(quality)
    quoted = [line.strip() for line in src.splitlines()
              if re.search(r"extraction_type='(?:markdown|text|article)'", line)]
    assert not quoted, f"有人手抄了一句建议，还点了共用读取器的取值：{quoted}"
    assert src.count("_COLLAPSE_RECOVERY") >= 5, (
        f"常量现在有 {src.count('_COLLAPSE_RECOVERY')} 处引用（定义之外该有 5 处："
        "散文 / next_action / 表 / 两处注释指向）；少了就是有一处改回了手写")


# ─── 2 · 点名的取值必须换一条管路 ───────────────────────────────

def test_every_extraction_type_the_advice_names_skips_the_reader():
    names = set(re.findall(r"extraction_type='([a-z]+)'", _COLLAPSE_RECOVERY))
    assert names, "建议里没有点名任何 extraction_type 取值"
    assert names <= SKIP_THE_READER, (
        f"塌陷页被劝去换 {sorted(names - SKIP_THE_READER)} —— 那一枚过的是刚刚丢掉正文的"
        "同一个读取器")


def test_the_other_door_is_named_too():
    """只劝 `html` 会缺一半：`schema=` 同样不经过读取器，而它是有名字地取字段的那条。"""
    assert "schema=" in _COLLAPSE_RECOVERY


def test_the_sentence_says_why_the_three_values_are_not_a_change_of_door():
    """光说"用 html"不够：调用方手里那份默认就是 markdown，他要知道换一枚不算换档。

    实测的那一发是照着 `'text'` 那句去做的 —— 所以这一句必须点名 markdown/text/article
    并且说明它们**共用**同一个刚刚失败的读取器。
    """
    for value in sorted(READER_VALUES):
        assert value in _COLLAPSE_RECOVERY, value
    assert "same reader" in _COLLAPSE_RECOVERY or "re-run the reader" in _COLLAPSE_RECOVERY
    assert "browser" in _COLLAPSE_RECOVERY, "起浏览器是同一族的第二条回路，也要说否"
    assert "stealthy" not in _COLLAPSE_RECOVERY and "escalat" not in _COLLAPSE_RECOVERY


def test_the_quoted_form_of_the_failed_value_is_offered_nowhere():
    """措辞的分工：带引号的是"把这颗传进来"（建议），不带引号的是提到有一枚这样的取值。

    本文件的常量用无引号的 markdown / text / article 讲道理；一旦有人把它抄回带引号的
    `'text'`，那就是把刚刚失败的那一步重新包装成出路 —— 场景 3 的账。
    """
    r = _collapsed()
    for where, text in (("error", _prose(r)), ("next_action", _hint(r)),
                        ("recover_with", _recover(r))):
        assert "'text'" not in text, (where, text)
        assert "'markdown'" not in text and "'article'" not in text, (where, text)


# ─── 3 · 反面守卫：新话术不许把别的判决一起改掉 ─────────────────

def test_a_real_shell_still_hears_about_the_browser():
    """塌陷那一支改了措辞，壳那一支一个字不许跟着变弱。"""
    shell = ResponseModel(status=200, content=["<div id=root></div>"], url=URL,
                          fetcher_used="http", content_type="text/html",
                          total_size_bytes=48_000, page_type="js_shell",
                          error="js_shell_detected: 48,000-byte body with 120 chars")
    hint = _hint(shell)
    assert "stealthy" in hint, hint
    assert "extraction_collapsed" not in hint, hint


def test_the_collapse_is_still_not_a_js_shell():
    """两支的分工没被合并：塌陷不产出"要渲染"的判决，也不盖掉结构化 page_type。"""
    issue = _prose(_collapsed())
    assert "js_shell" not in issue, issue
    assert page_type_from_error(issue) == "", issue


def test_a_page_that_was_read_whole_gets_no_advice_at_all():
    """没塌就不许说话：1,374 个可见字符而抽回 1,640 —— 闸在另一边也要关着。"""
    fine = _collapsed(content=["£45.17 In stock " * 120])
    assert _prose(fine) == "", _prose(fine)
    assert _recover(fine) == ""


# ─── 4 · 帽与手册 ──────────────────────────────────────────────

def test_the_whole_sentence_fits_the_crawl_rows_reason_cap():
    """爬取行是这句话唯一的落点，所以它不许被 `HOLE_REASON_CAP` 剪掉出路。

    两边都要量：塌陷句本身（最宽的位数），和 `_clip_reason` 的原样返回 —— 后者是"这一行
    确实没被剪"的收据，而不是"帽大概够"。
    """
    worst = _collapsed(content=["x"])
    worst.visible_text_chars = 999_999
    prose = _prose(worst)
    assert len(prose) <= HOLE_REASON_CAP, (len(prose), HOLE_REASON_CAP, prose)
    assert _clip_reason(prose) == prose
    assert _clip_reason(prose).endswith(_COLLAPSE_RECOVERY)


def test_the_manual_names_the_same_two_doors_and_the_same_measured_case():
    """三个落点上了话，解释就必须住在只读手册的 agent 也看得见的卷里。

    量的是那一条**实测**（527/1,998）和"哪两枚是门"—— 一句改写过的建议如果没有留下
    证据，读手册的人就只知道结论，下一次照样并列劝两枚。
    """
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "extraction_collapsed" in corpus, "手册里没有这一族的词条"
    for needle in ("This is the door", "not doors", "1,998 characters"):
        assert needle in corpus, f"{needle!r} 不在语料里"
    # 旧文案把两枚并列成 `(or 'text')`，原样存在过 —— 手册与代码各说一套正是这一族反复
    # 付过的账（`test_wire_moves_land_in_the_manual.py`）。这一条不许那句话回来。
    assert "(or 'text')" not in corpus
