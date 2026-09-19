"""``hound_mcp.links`` 单元测试（纯函数：lxml 解析，无网络）。

该模块把页面外链分类为 citations / navigation / external / primary_source，
此前无测试覆盖。分类错了会让 agent 把站点导航当成引用来源，或漏掉真正的
一手来源（arxiv / doi 等）。
"""

from __future__ import annotations

from hound_mcp.links import _norm_host, extract_links

PAGE = "https://example.com/blog/post"


class TestGuards:
    def test_empty_html_returns_empty_lists(self):
        out = extract_links("", PAGE)

        assert out == {
            "citations": [],
            "navigation": [],
            "external": [],
            "primary_source": "",
        }

    def test_empty_page_url_returns_empty_lists(self):
        out = extract_links('<a href="/x">x</a>', "")

        assert out["citations"] == [] and out["external"] == []

    def test_broken_markup_never_raises(self):
        out = extract_links("<a href=", PAGE)

        assert isinstance(out, dict)
        assert set(out) == {"citations", "navigation", "external", "primary_source"}


class TestSchemeFiltering:
    def test_non_http_schemes_and_fragments_are_skipped(self):
        html = (
            '<a href="mailto:a@b.c">mail</a>'
            '<a href="javascript:void(0)">js</a>'
            '<a href="#top">frag</a>'
            '<a href="tel:+123">tel</a>'
            '<a href="data:text/plain,x">data</a>'
        )

        out = extract_links(html, PAGE)

        assert out["citations"] == []
        assert out["navigation"] == []
        assert out["external"] == []


class TestClassification:
    def test_same_domain_in_content_is_a_citation(self):
        out = extract_links('<p>see <a href="/other">Other post</a></p>', PAGE)

        assert out["citations"] == [
            {"url": "https://example.com/other", "text": "Other post"}
        ]

    def test_same_domain_inside_nav_is_navigation(self):
        out = extract_links('<nav><a href="/about">About</a></nav>', PAGE)

        assert out["navigation"] == [
            {"url": "https://example.com/about", "text": "About"}
        ]
        assert out["citations"] == []

    def test_off_domain_link_goes_to_external(self):
        out = extract_links('<a href="https://other.test/x">X</a>', PAGE)

        assert out["external"] == [{"url": "https://other.test/x", "text": "X"}]
        assert out["citations"] == []

    def test_www_prefix_is_not_a_different_host(self):
        """example.com 与 www.example.com 视为同域。"""
        out = extract_links('<a href="https://www.example.com/x">X</a>', PAGE)

        assert out["external"] == []

    def test_fragments_are_not_a_second_entry(self):
        html = '<a href="/a">1</a><a href="/a#section">2</a>'

        out = extract_links(html, PAGE)

        assert len(out["citations"]) == 1

    def test_long_anchor_text_is_truncated(self):
        html = f'<p><a href="/a">{"w" * 300}</a></p>'

        out = extract_links(html, PAGE)

        assert len(out["citations"][0]["text"]) == 160


class TestPrimarySource:
    def test_offdomain_canonical_wins(self):
        out = extract_links(
            '<p><a href="https://arxiv.org/abs/1234">paper</a></p>',
            PAGE,
            {"canonical": "https://journal.test/article"},
        )

        assert out["primary_source"] == "https://journal.test/article"

    def test_same_domain_canonical_is_not_a_primary_source(self):
        """同域 canonical 只是站点自身，不能当作一手来源。"""
        out = extract_links(
            '<p><a href="/x">x</a></p>',
            PAGE,
            {"canonical": "https://example.com/blog/post"},
        )

        assert out["primary_source"] == ""

    def test_known_primary_host_in_content_is_used(self):
        out = extract_links('<p><a href="https://arxiv.org/abs/1234">paper</a></p>', PAGE)

        assert out["primary_source"] == "https://arxiv.org/abs/1234"

    def test_primary_host_inside_nav_is_not_used(self):
        """导航里的外链不算引用（不是正文中的一手来源）。"""
        out = extract_links(
            '<nav><a href="https://github.com/some/repo">repo</a></nav>', PAGE
        )

        assert out["primary_source"] == ""


class TestNormHost:
    """回归：_norm_host 曾用 lstrip("www.") 剥前缀——lstrip 按字符集剥离，
    会把 wikipedia.org 变成 ipedia.org、web.example.com 变成 eb.example.com，
    导致 w 开头域名的比较与 .wikipedia.org 一类后缀匹配全部失效。"""

    def test_host_starting_with_w_is_not_mangled(self):
        assert _norm_host("https://wikipedia.org/wiki/Foo") == "wikipedia.org"
        assert _norm_host("https://web.example.com/x") == "web.example.com"
        assert _norm_host("https://weather.com/forecast") == "weather.com"

    def test_www_prefix_still_stripped(self):
        assert _norm_host("https://www.example.com/x") == "example.com"
        # 旧 lstrip 会把 "www.wikipedia.org" 剥成 "ikipedia.org"
        assert _norm_host("https://www.wikipedia.org/wiki/Foo") == "wikipedia.org"

    def test_userinfo_and_port_stripped(self):
        assert _norm_host("https://user:pass@example.com:8080/x") == "example.com"

    def test_non_www_subdomain_kept(self):
        assert _norm_host("https://en.wikipedia.org/wiki/Foo") == "en.wikipedia.org"


class TestPrimarySourceWwwSubdomain:
    """集成：wikipedia 系一手来源识别（旧 lstrip bug 下 www 子域失效）。"""

    def test_www_wikipedia_citation_is_primary_source(self):
        out = extract_links(
            '<p><a href="https://www.wikipedia.org/wiki/X">wiki</a></p>', PAGE
        )
        assert out["primary_source"] == "https://www.wikipedia.org/wiki/X"

    def test_en_wikipedia_citation_is_primary_source(self):
        out = extract_links(
            '<p><a href="https://en.wikipedia.org/wiki/X">wiki</a></p>', PAGE
        )
        assert out["primary_source"] == "https://en.wikipedia.org/wiki/X"
