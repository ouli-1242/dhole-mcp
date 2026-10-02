"""Envelope tests: page-type detection, source authority, freshness, paywall
evidence parser (HTMLParser-based, PR #9 fix).

Tests the real detect_page_type/classify_source/compute_freshness functions
against real HTML. No mocks. Adversarial: false positives (bare "paywall"
token in copy/links/scripts) must not classify as paywall; real paywalls
(visible text, active attributes) must.
"""

from datetime import datetime, timedelta, timezone
import re
from dhole_mcp.envelope import (
    detect_page_type, classify_source, compute_freshness, page_type_from_error,
    _paywall_evidence,
)


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
