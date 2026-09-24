"""上限测试报告的处置守卫（第三轮外部实测，~60 次调用 / 8 个工具）。

这份文件对应三条实测发现，每条都是「静默返回成功」类问题 —— 不报错、
不崩溃，只是把不可用的内容标记为可用，所以只有断言能拦住它：

1. content_ok 误报：x.com 的 "Try reloading"、douyin.com 的 "Please wait..."
   都以 HTTP 200 返回且 content_ok=true，agent 会把失败页当正文引用。
2. archive.org 第三层降级在工具描述与 README 里都不存在 —— agent 调用前
   无从得知内容可能是存档快照。
3. smart_crawl 的 max_total_chars 被硬钳到 500000，文档没写，于是
   "调大 max_pages 就能多爬" 是错的预期。
"""

import json

import pytest
from mcp.types import Tool

from dhole_mcp.server import (
    DHOLE_INSTRUCTIONS,
    MasterFetchServer,
    ResponseModel,
    _agent_hints,
    _annotate_quality,
    _is_soft_failure,
    _with_agent_hints,
)


def _result(**kwargs):
    defaults = dict(
        status=200, content=["Real article body text about Python packaging."],
        url="https://example.com/article", fetcher_used="http",
        content_type="text/html", total_size_bytes=4000,
        extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


def _tools() -> dict[str, dict]:
    return {td["name"]: Tool(**td).model_dump(by_alias=True, exclude_none=True)
            for td in MasterFetchServer._TOOL_DEFS}


@pytest.fixture(scope="module")
def tools() -> dict[str, dict]:
    return _tools()


def _desc(tools: dict[str, dict], name: str) -> str:
    return tools[name]["description"]


# ─── 1. 软失败页：200 + 非空正文，但不是内容 ─────────────────────────────


class TestSoftFailureDetection:
    """实测样本：x.com「Try reloading」、douyin.com「Please wait...」。

    三者的共同点是 HTTP 200 且正文非空，所以 ``_agent_hints`` 的
    ``has_content`` 为真，content_ok 就为真。它们不含任何反爬/验证措辞，
    bot wall 与 auth wall 的检测都覆盖不到，需要单独一类。
    """

    @pytest.mark.parametrize("body,why", [
        ("Try reloading the page.", "x.com 实测：失败页被当作正文"),
        ("Please wait...", "douyin.com 实测：249 字符的占位页"),
        ("Something went wrong.", "通用客户端错误视图"),
        ("An error occurred while loading.", "通用错误视图"),
        ("页面加载失败，请稍后重试", "中文错误视图"),
    ])
    def test_measured_samples_are_detected(self, body, why):
        assert _is_soft_failure(_result(content=[body])), why

    def test_length_gate_keeps_real_articles(self):
        """门限存在的理由：短语本身在普通散文里很常见。

        一篇真的在讲故障排查的长文会含 "something went wrong"，若只匹配
        短语就会把正文判成失败页。长度是唯一的区分证据。
        """
        article = ("Something went wrong during the deploy. " * 30)
        assert len(article) > 600
        assert not _is_soft_failure(_result(content=[article]))

    def test_short_normal_page_is_left_alone(self):
        """短但不带失败措辞的页面（example.com 那类）必须保持 content_ok。"""
        body = ("Example Domain. This domain is for use in illustrative "
                "examples in documents. You may use this domain in literature "
                "without prior coordination or asking for permission.")
        assert not _is_soft_failure(_result(content=[body]))

    @pytest.mark.parametrize("status", [400, 403, 404, 500, 503])
    def test_error_statuses_are_not_claimed(self, status):
        """4xx/5xx 归 ``http_error_<status>`` 分支。

        软失败检测若也吃掉它们，错误原因就从「服务器拒绝」变成「页面是
        占位页」，agent 会去换源而不是去看状态码。
        """
        assert not _is_soft_failure(
            _result(status=status, content=["Something went wrong."]))

    def test_pdf_is_not_claimed(self):
        """PDF 无文本层是 ``pdf_no_text`` 的失败类，不归软失败。"""
        assert not _is_soft_failure(
            _result(content=["Something went wrong."],
                    content_type="application/pdf"))

    def test_empty_content_is_not_claimed(self):
        """空正文是 JS shell / HTTP 错误的地盘，两处判会给出矛盾的 next_action。"""
        assert not _is_soft_failure(_result(content=[]))

    def test_end_to_end_content_ok_is_false(self):
        """端到端：agent 真正读到的那个 content_ok 必须为 False。

        只测 ``_is_soft_failure`` 不够 —— 它得真的接进 ``_annotate_quality``
        才会写进 error，而 content_ok 又依赖 error。这条钉住整条链路。
        """
        out = _with_agent_hints(_annotate_quality(
            _result(content=["Try reloading"])))
        assert "soft_failure_detected" in out.error, out.error
        assert out.content_ok is False, "失败页仍被标记为可引用"

    def test_end_to_end_normal_short_page_stays_ok(self):
        # total_size_bytes 必须跟着正文一起小：否则 JS-shell 启发式（大 body
        # + 极少文本）会命中，这条就变成在测 js_shell 而不是"没被误判"。
        out = _with_agent_hints(_annotate_quality(
            _result(content=["Example Domain. This domain is for use in "
                             "illustrative examples in documents."],
                    total_size_bytes=600)))
        assert out.error == "", out.error
        assert out.content_ok is True


# ─── 2. archive.org 第三层降级必须写在 agent 看得见的地方 ────────────────


class TestArchiveTierDocumented:
    """降级本身是有意设计（字段描述里写了），缺的是「存在性」与「触发条件」。

    agent 只看工具描述决定怎么调，只在响应里看到 source 字段 —— 于是它
    无法在调用前知道时效敏感的请求可能拿到旧快照。
    """

    def test_smart_fetch_names_the_third_tier(self, tools):
        desc = _desc(tools, "smart_fetch").lower()
        assert "archive.org" in desc, "第三层降级在工具描述里不存在"
        assert "metadata.source" in desc, "没告诉 agent 去哪里辨认存档来源"

    def test_smart_fetch_says_it_cannot_be_disabled(self, tools):
        desc = _desc(tools, "smart_fetch").lower()
        assert "disabl" in desc, "没说明这一层无法关闭（实测无开关可关）"

    def test_instructions_point_at_metadata_source(self):
        low = DHOLE_INSTRUCTIONS.lower()
        assert "metadata.source" in low, (
            "instructions 里 content_ok=true 的例外情况没写清楚")


# ─── 3. crawl 的 500000 字符硬顶 ────────────────────────────────────────


class TestCrawlBudgetDocumented:
    """实测：max_pages=100 仍只抓 31 页。

    根因是 crawl.py 把 max_total_chars 钳在 500000，而 max_pages 只在
    max_total_chars 未显式给出时才参与推导 —— 撞上钳制后调 max_pages
    不再有任何效果。这是文档必须说的，否则 agent 会一直加参数。
    """

    def test_crawl_documents_the_hard_clamp(self, tools):
        desc = _desc(tools, "smart_crawl")
        assert "500000" in desc, "max_total_chars 的硬顶没写进描述"


# ─── 4. 错误状态的 next_action 不许被「截断」抢走 ────────────────────────


class TestErrorStatusNextAction:
    """实测：Wikipedia 404 页返回 next_action "page truncated...offset=".

    根因是 ``_agent_hints`` 里截断提示排在 elif 链最前，而 404 页的正文
    足够长、会被 max_content_chars 截断 —— 于是 agent 被引导去翻一个
    不存在的页面的下一页。状态码是比截断更具体的事实。
    """

    def _next_action(self, **kwargs):
        result = _result(**kwargs)
        _, next_action, _ = _agent_hints(_annotate_quality(result))
        return next_action

    def test_truncated_404_does_not_say_paginate(self):
        next_action = self._next_action(
            status=404, content=["Wikipedia does not have an article with this "
                                 "exact name. "] * 40,
            is_truncated=True, next_offset=1000)
        assert "truncated" not in next_action.lower(), (
            f"404 仍被当成可分页的截断页: {next_action}")
        assert "404" in next_action

    def test_truncated_200_still_says_paginate(self):
        """防回归：正常的长页面仍然要拿到分页提示。"""
        next_action = self._next_action(
            status=200, content=["Real article body. " * 400],
            is_truncated=True, next_offset=4000)
        assert "offset=4000" in next_action, next_action

    @pytest.mark.parametrize("status,needle", [
        (403, "403"), (429, "429"), (410, "410"),
    ])
    def test_other_error_statuses_get_their_own_advice(self, status, needle):
        next_action = self._next_action(
            status=status, content=["Nothing here."],
            is_truncated=True, next_offset=100)
        assert needle in next_action, next_action
        assert "truncated" not in next_action.lower()


# ─── 5. 声明的 charset 与字节不符时的 mojibake ───────────────────────────


class TestDeclaredCharsetLies:
    """实测：you’ve → youâ€™ve / you???ve，· → ??，content 与 summary 都中招。

    根因在 fetcher：encoding 只从 Content-Type header 取，页面自己的
    <meta charset> 完全不参与，且没有一致性校验 —— Apache 默认发
    ISO-8859-1 而内容其实是 UTF-8 时，声明编码赢。

    修法不是猜字符集，而是两种解码各打一次分、取更干净的。所以「真·非
    UTF-8 内容不被改坏」和「错声明被纠正」同样重要，各有一条。
    """

    _UTF8_HTML = ('<html><head><meta charset="utf-8"></head><body>'
                  '<p>you’ve · café</p></body></html>')

    def _decoded(self, body: bytes, declared: str) -> str:
        from dhole_mcp.trafilatura_extractor import _get_html_from_page
        from types import SimpleNamespace
        html = _get_html_from_page(SimpleNamespace(body=body, encoding=declared))
        return html[html.find("<p>") + 3:html.find("</p>")]

    def test_utf8_body_declared_latin1(self):
        assert self._decoded(self._UTF8_HTML.encode("utf-8"), "iso-8859-1") == \
            "you’ve · café"

    def test_utf8_body_declared_ascii(self):
        """us-ascii 声明会把每个非 ASCII 字节变成 U+FFFD —— 报告的 ?? 形态。"""
        assert self._decoded(self._UTF8_HTML.encode("utf-8"), "us-ascii") == \
            "you’ve · café"

    def test_utf8_body_declared_cp1252(self):
        assert self._decoded(self._UTF8_HTML.encode("utf-8"), "windows-1252") == \
            "you’ve · café"

    def test_true_latin1_body_is_left_alone(self):
        """真 latin-1 内容按 utf-8 解码会产生大量 U+FFFD，评分更差 → 保留。"""
        body = "<html><body><p>café naïve</p></body></html>".encode("iso-8859-1")
        assert self._decoded(body, "iso-8859-1") == "café naïve"

    def test_true_gbk_body_is_left_alone(self):
        """中文站最常见的真实场景，改坏比不改严重得多。"""
        body = "<html><body><p>中文内容测试</p></body></html>".encode("gbk")
        assert self._decoded(body, "gbk") == "中文内容测试"

    def test_unknown_charset_name_falls_back_to_utf8(self):
        from dhole_mcp.fetcher import _decode_html_bytes
        assert _decode_html_bytes("中文".encode("utf-8"), "not-a-real-charset") == "中文"


# ─── 预算：这一轮新增的描述不许挤爆 connect-time 成本 ────────────────────


def test_connect_time_total_still_within_budget(tools):
    """与 test_tool_descriptions 的预算同源，这里再钉一次是为了让本轮改动
    自己就能被发现：新增描述若把总量推过 13800，这条会先红。

    15.1（wire 裁剪）：14000 -> 13800。裁剪后实测 12601，上限跟着下调，
    让省下来的 1004 字符不能变成新的可用额度。"""
    total = sum(len(json.dumps(t, ensure_ascii=False)) for t in tools.values())
    total += len(DHOLE_INSTRUCTIONS)
    assert total <= 13800, f"connect-time 合计 {total} > 13800"
