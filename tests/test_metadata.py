"""test_metadata.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

from dhole_mcp.metadata import extract_image_urls, extract_metadata


# ============================================================================
# 来源：test_metadata.py
# ============================================================================
# ``dhole_mcp.metadata`` 单元测试（纯函数：正则 + json，无网络）。

class TestExtractMetadata:
    def test_empty_html_returns_empty_dict(self):
        assert extract_metadata("", "https://example.com/") == {}

    def test_title_tag_is_used_as_fallback(self):
        html = "<html><head><title>  Hello   World </title></head></html>"
        meta = extract_metadata(html, "https://example.com/")

        assert meta["title"] == "Hello World"

    def test_opengraph_takes_priority_over_twitter_and_title(self):
        html = """<html><head>
            <title>title tag</title>
            <meta property="og:title" content="OG Title">
            <meta name="twitter:title" content="Twitter Title">
            <meta property="og:description" content="A description">
            <meta property="og:site_name" content="Example Site">
            <meta property="og:type" content="article">
            <meta property="og:image" content="https://cdn.example.com/a.png">
        </head></html>"""

        meta = extract_metadata(html, "https://example.com/")

        assert meta["title"] == "OG Title"
        assert meta["description"] == "A description"
        assert meta["site_name"] == "Example Site"
        assert meta["type"] == "article"
        assert meta["image"] == "https://cdn.example.com/a.png"

    def test_reversed_attribute_order_is_parsed(self):
        """content 写在 property 之前也要能解析。"""
        html = '<meta content="Rev Title" property="og:title">'

        assert extract_metadata(html, "https://example.com/")["title"] == "Rev Title"

    def test_meta_without_content_is_ignored(self):
        html = '<meta property="og:title" content="">'

        assert "title" not in extract_metadata(html, "https://example.com/")

    def test_canonical_is_made_absolute(self):
        html = '<link rel="canonical" href="/page">'

        meta = extract_metadata(html, "https://example.com/blog/post")

        assert meta["canonical"] == "https://example.com/page"

    def test_html_lang_is_extracted(self):
        assert (
            extract_metadata('<html lang="zh-CN">', "https://example.com/")["lang"]
            == "zh-CN"
        )

    def test_jsonld_fills_fields(self):
        html = """<script type="application/ld+json">
        {"@type": "Article", "headline": "LD Headline",
         "datePublished": "2026-01-02T03:04:05Z",
         "description": "LD desc", "author": {"name": "Ada"}}
        </script>"""

        meta = extract_metadata(html, "https://example.com/")

        assert meta["title"] == "LD Headline"
        assert meta["published_time"] == "2026-01-02T03:04:05Z"
        assert meta["author"] == "Ada"
        assert meta["description"] == "LD desc"

    def test_jsonld_author_list_uses_first_entry(self):
        html = """<script type="application/ld+json">
        {"author": [{"name": "First"}, {"name": "Second"}]}
        </script>"""

        assert (
            extract_metadata(html, "https://example.com/")["author"] == "First"
        )

    def test_a_date_with_an_offset_survives_whole(self):
        """批次 6b：`[:20]` 曾经从时区偏移的中间剪断一个合法的 ISO-8601。

        2026-10-05 实测的字段形状是 `'2026-03-13T14:17:06+'` —— 前 20 个字符正好
        停在 `+` 上。残缺串既不合法也不完整，却被原样端进 transcript 当引用依据，
        而调用方看不出它被剪过（同一页的 `content_age_days` 反而是对的，因为解析
        函数认得剩下的那半句 —— 一个字段说谎、另一个字段说真话，是最难查的组合）。
        """
        for raw, want in (
            ("2026-03-13T14:17:06+08:00", "2026-03-13T14:17:06+08:00"),
            ("2026-03-13T14:17:06.500Z", "2026-03-13T14:17:06.500Z"),
            ("2026-03-13T14:17:06", "2026-03-13T14:17:06"),
            ("2026-03-13", "2026-03-13"),
        ):
            html = ('<script type="application/ld+json">'
                    f'{{"@type": "Article", "datePublished": "{raw}"}}'
                    "</script>")
            got = extract_metadata(html, "https://example.com/")["published_time"]
            assert got == want, f"{raw} 进来、{got!r} 出去"
            assert not got.endswith(("+", ".", "-")), f"偏移被剪断：{got!r}"

    def test_a_stored_offset_still_drives_the_age_math(self):
        """不剪断不是为了好看：`content_age_days` 读的就是这一串。

        带偏移的日期被剪成 `...+` 之后，任何按 ISO 解析的下游都只能猜；这一条把
        "字段完整" 与 "年龄算得对" 钉成同一件事（Python 3.14 的 fromisoformat 认得
        空格分隔的写法，所以站点自己给 `2026-03-13 14:17:06Z` 也是同一条路）。
        """
        from dhole_mcp.envelope import _parse_date

        html = ('<script type="application/ld+json">'
                '{"datePublished": "2026-03-13T14:17:06+08:00"}</script>')
        meta = extract_metadata(html, "https://example.com/")
        parsed = _parse_date(meta["published_time"])
        assert parsed is not None, f"完整串反而解析不动：{meta['published_time']!r}"
        assert (parsed.year, parsed.month, parsed.day) == (2026, 3, 13)

    def test_an_absurd_date_value_is_still_bounded(self):
        """上限从 20 抬到 40 是为了容纳偏移，不是为了容纳整个对象。"""
        html = ('<script type="application/ld+json">'
                '{"datePublished": "' + ("9" * 400) + '"}</script>')
        meta = extract_metadata(html, "https://example.com/")
        assert len(meta["published_time"]) <= 40, len(meta["published_time"])

    def test_malformed_jsonld_is_skipped(self):
        html = '<script type="application/ld+json">{ not json </script>'

        assert extract_metadata(html, "https://example.com/") == {}

    def test_meta_values_are_truncated(self):
        html = f'<meta property="og:description" content="{"x" * 900}">'

        assert len(extract_metadata(html, "https://example.com/")["description"]) == 500


class TestExtractImageUrls:
    def test_relative_src_is_made_absolute(self):
        html = '<img src="/img/a.png">'

        assert extract_image_urls(html, "https://example.com/blog/") == [
            "https://example.com/img/a.png"
        ]

    def test_data_uris_are_skipped(self):
        html = '<img src="data:image/png;base64,AAAA"><img src="/a.png">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/a.png"
        ]

    def test_duplicates_are_collapsed_order_preserved(self):
        html = '<img src="/b.png"><img src="/a.png"><img src="/b.png">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/b.png",
            "https://example.com/a.png",
        ]

    def test_max_n_caps_the_result(self):
        html = "".join(f'<img src="/{i}.png">' for i in range(10))

        assert len(extract_image_urls(html, "https://example.com/", max_n=3)) == 3

    def test_empty_html_returns_empty_list(self):
        assert extract_image_urls("", "https://example.com/") == []

    def test_lazy_data_src_used_when_src_is_placeholder(self):
        """懒加载页：src 是 data: 占位图、真实图片在 data-src。"""
        html = ('<img src="data:image/gif;base64,AAAA" data-src="/lazy/real.png">'
                '<img src="/normal.png">')

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/lazy/real.png",
            "https://example.com/normal.png",
        ]

    def test_data_src_only_tag_is_included(self):
        html = '<img class="lazy" data-src="/only/data-src.png">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/only/data-src.png"
        ]

    def test_real_src_wins_over_data_src(self):
        html = '<img src="/first.png" data-src="/second.png">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/first.png"
        ]

    def test_data_src_data_uri_still_skipped(self):
        html = '<img data-src="data:image/png;base64,AAAA">'

        assert extract_image_urls(html, "https://example.com/") == []

    def test_srcset_alone_does_not_match(self):
        html = '<img srcset="/a.png 1x, /b.png 2x">'

        assert extract_image_urls(html, "https://example.com/") == []

    def test_html_entities_in_src_are_decoded(self):
        """R8：`&amp;` 不还原，交出去的 URL 请求方一定拿 404。

        本模块在 title/description/canonical 三处都做了 unescape
        （metadata.py:89/:95/:138），唯独媒体这条路径漏了。
        """
        html = '<img src="/th?id=abc&amp;pid=2.1">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/th?id=abc&pid=2.1"
        ]

    def test_two_urls_crammed_into_one_src_do_not_become_one_entry(self):
        """R8 的实测脏元素：Bing 的懒加载把两个地址写进了同一个 src。

        整串喂给 urljoin 会原样通过 `http(s)://` 前缀检查 —— 于是
        include_media 交出的是一个含两个地址的字符串。
        """
        html = ('<img src="//th.bing.com, '
                'https://r.bing.com/th?id=Gmm%5C1px.jpg&amp;pid=2.1">')

        out = extract_image_urls(html, "https://www.bing.com/search?q=x")

        assert all(", " not in u for u in out), f"两个地址挤成了一个元素：{out}"
        assert all(" " not in u for u in out), f"元素里还有空白：{out}"
        assert "https://r.bing.com/th?id=Gmm%5C1px.jpg&pid=2.1" in out

    def test_bare_comma_in_a_path_is_not_a_candidate_separator(self):
        """拆候选要求逗号后跟空白 —— 路径里合法的裸逗号不该被拆。"""
        html = '<img src="/img/a,b.png">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/img/a,b.png"
        ]

    def test_scheme_relative_second_entry_is_not_kept_bare(self):
        """逗号+空白拆开后，协议相对的第一个候选自己补全，不该带出残片。"""
        html = '<img src="/first.png, /second.png">'

        assert extract_image_urls(html, "https://example.com/") == [
            "https://example.com/first.png",
            "https://example.com/second.png",
        ]


# ============================================================================
# 来源：test_metadata_title_decodes_entities.py
# ============================================================================
# metadata.title 保留原始 HTML 实体而正文已解码 —— 同一响应两套实体口径。

def test_a_title_fallback_decodes_entities():
    meta = extract_metadata("<html><title>a &amp; b</title></html>", "https://x/")
    assert meta["title"] == "a & b"


def test_an_og_title_decodes_entities():
    html = ('<html><head>'
            '<meta property="og:title" content="Sale &amp; Clearance">'
            '</head><body></body></html>')
    meta = extract_metadata(html, "https://x/")
    assert meta["title"] == "Sale & Clearance"


def test_a_json_ld_headline_decodes_entities():
    html = ('<html><head><script type="application/ld+json">'
            '{"headline": "Q3 &amp; Beyond"}'
            '</script></head><body></body></html>')
    meta = extract_metadata(html, "https://x/")
    assert meta["title"] == "Q3 & Beyond"


def test_a_title_that_never_had_entities_is_untouched():
    meta = extract_metadata("<html><title>Plain Title</title></html>", "https://x/")
    assert meta["title"] == "Plain Title"
