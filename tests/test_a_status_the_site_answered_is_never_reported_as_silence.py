"""2026-10-08 实连验收（D-20 / D-12 / D-03）：失败的原因不许被说反。

现场（对一台可控源逐条喂确定性状态码，另加 httpbin 复核）：

    /status/204  → HTTP 档抽出空正文 → `_is_js_shell` 说"壳" → 升级浏览器 →
                   ERR_ABORTED → `all_tiers_failed: unknown (HTTP status 0)` +
                   "No HTTP response was received at all: the name may not exist"
                   —— 关于一个在状态行里写着"没有内容"的响应。
    /status/429  → 升级到浏览器，再升级到 archive.org；archive 自己超时之后**整条
                   替换**了源站的答复：调用方拿到 code=timeout / retryable=true /
                   "raise timeout"，而 429 唯一不能做的事就是很快再打一次。
    /status/503  → `all_tiers_failed: unknown (HTTP status 0)`，建议"试个代理"。
    image/png    → OCR 摘出一个字形 "o" → 正文质量判定按"body 大文字少"说成壳、
                   content_ok=false，诊断还附了一句"本机浏览器档不可用"——同一台
                   机器三秒前刚用浏览器档渲染过一页。
    dns_failure  → `diagnosis.retryable=true` 而同一响应的 next_action 写着
                   "do NOT retry"；5xx 反过来：retryable=false 而文案写着
                   "Retry once after a pause"。

四条同源：拿**一个档位的渲染结果**去否认**另一个档位收到的答复**，再把否认写成事实。
判据是"谁说的"而不是"看起来像什么"，所以本件钉的是归属，不是措辞。
"""

from __future__ import annotations

import pytest

from dhole_mcp.errors import classify_network_error, is_retryable
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (
    _detect_content_issue, _diagnose, _is_js_shell, _is_raster_image,
)
from dhole_mcp.server import _all_tiers_failed_tips, _archive_ran_out


def _result(*, status: int = 200, content=None, content_type: str = "text/html",
            url: str = "https://example.com/article", error: str = "") -> ResponseModel:
    return ResponseModel(status=status, content=content if content is not None else [],
                         url=url, fetcher_used="http", content_type=content_type,
                         total_size_bytes=1000, error=error)


# ─── 1 · "没有正文"写在状态行里的那些 ─────────────────────────────────────

class TestABodyTheStatusLineAlreadyDeclared:

    @pytest.mark.parametrize("status", [204, 205, 304])
    def test_no_body_by_definition_is_not_a_js_shell(self, status):
        """壳的定义是"页面有内容而需要渲染才能看到"。204/205/304 是站点**宣布**了
        没有正文；渲染不能把宣布掉的内容变出来（浏览器对一个 204 只能给
        ERR_ABORTED，这正是当时的实测第二跳）。"""
        result = _result(status=status, content=[])
        assert _is_js_shell(result) is False
        assert _detect_content_issue(result) == ""

    def test_an_empty_body_on_an_ordinary_200_is_still_suspect(self):
        """豁免只给"状态行宣布过没有正文"的那些。200 + 空正文仍然是该升级的形状，
        否则这一族豁免就成了又一次"用一个猜测盖掉一个检查"。"""
        assert _is_js_shell(_result(status=200, content=[])) is True

    def test_an_empty_body_on_a_404_is_not_offered_a_browser(self):
        assert _is_js_shell(_result(status=404, content=[])) is False


# ─── 2 · 位图与 SVG 不是一回事 ────────────────────────────────────────────

class TestAPictureIsNotJudgedByPageHeuristics:

    def test_a_sparse_ocr_read_of_a_png_is_the_answer_not_a_failure_shape(self):
        """实测现场：OCR 只找到一个字形，"1 个字符"落进"body 大 / 文字少"那条体积
        启发式，于是正确的 OCR 结果被打成 js_shell 并附带一句关于本机浏览器的假话。
        一张图没有文字是关于图的事实。"""
        png = _result(status=200, content=["o"], content_type="image/png",
                      url="https://httpbin.org/image/png")
        assert _is_raster_image(png) is True
        assert _detect_content_issue(png) == ""

    def test_an_empty_svg_read_is_still_reported(self):
        """SVG 的正文就是它的标记：抽干了是读取问题，要说出来。把 image/ 整体豁免
        会把这一族一起放走（现有回归件正钉着它）。"""
        svg = _result(status=200, content=[], content_type="image/svg+xml")
        assert _is_raster_image(svg) is False
        assert _is_js_shell(svg) is True


# ─── 3 · 状态码归状态码，够不着归够不着 ───────────────────────────────────

class TestAnAnsweredStatusIsNotCalledSilence:

    def test_a_throttle_is_told_as_a_throttle(self):
        tips = _all_tiers_failed_tips("http_429", 429)
        assert "429 Too Many Requests" in tips
        assert "Retry-After" in tips
        assert "A 5xx is not a bot block" not in tips
        # 代理与"换 URL 试低防护路径"都是把限流当成墙：那条路走不通。
        assert "Try a proxy stored with cred_defaults" not in tips
        assert "some paths have lower protection" not in tips

    def test_a_server_error_says_where_the_failure_is(self):
        tips = _all_tiers_failed_tips("http_503", 503)
        assert "A 5xx is not a bot block" in tips
        assert "the failure is at the server" in tips
        assert "No HTTP response was received" not in tips

    def test_silence_still_says_silence(self):
        """反向守卫：真的没有任何响应时，那句"没收到响应"仍然是对的（名字不存在
        那一族当年就是靠这句话把"试代理"的建议换掉的）。"""
        tips = _all_tiers_failed_tips("unknown", 0)
        assert "No HTTP response was received at all" in tips
        assert "Do NOT retry the same URL" in tips

    def test_a_category_named_by_status_is_carried_into_the_receipt(self):
        """分类标签必须含真实状态号：`unknown (HTTP status 0)` 之所以有害，是因为
        它把两件事（谁没答 / 答了什么）压成一件。"""
        assert "429" in _all_tiers_failed_tips("http_429", 429)


# ─── 4 · archive 那一跳失败时不许改写源站的答复 ───────────────────────────

class TestAnArchiveMissDoesNotOverwriteTheOrigin:

    def test_the_origin_keeps_its_own_verdict(self):
        origin = _result(status=429, content=[], error="http_error_429: server returned error status")
        late = _result(status=0, content=[], error="timeout: the 9000ms call budget ran out during the archive.org lookup")
        out = _archive_ran_out(origin, late)
        assert out.status == 429, "快照查询耗尽预算，不能把 429 改写成一次超时"
        assert out.error.startswith("http_error_429")
        assert "archive.org fallback was tried and ran out of budget" in out.error
        assert "+archive(no_budget)" in out.escalation_path
        assert out.content_ok is False

    def test_the_lookup_that_simply_found_nothing_is_not_blamed(self):
        """没有快照（返回 None）与查快照查超时是两件事：前者不提，后者必须说清是谁
        的时间花完了。"""
        origin = _result(status=503, content=[], error="http_error_503: server returned error status")
        assert "archive" not in origin.error


# ─── 5 · retryable 与那句人话只能有一个出处 ──────────────────────────────

class TestRetryableAgreesWithItsOwnSentence:

    @pytest.mark.parametrize("category, expected", [
        ("dns_failure", False),      # 名字不存在，重发同一字节不可能变
        ("connection_refused", False),
        ("tls_error", False),
        ("proxy_error", False),
        ("browser_launch", False),
        ("connection_reset", True),  # 可能是瞬变的
        ("timeout", True),
    ])
    def test_the_table_matches_the_prose_it_sits_next_to(self, category, expected):
        assert is_retryable(category) is expected

    def test_a_dead_hostname_is_no_longer_marked_retryable(self):
        """实测的矛盾本体：error 说 "do NOT retry"，diagnosis 说可以。"""
        result = _result(status=0, content=[],
                         error="network_error: dns_failure (TCP preflight)")
        result.fetcher_used = "none"
        diag = _diagnose(result)
        assert diag["code"] == "network"
        assert diag["retryable"] is False

    def test_a_reset_keeps_its_retry(self):
        result = _result(status=0, content=[],
                         error="network_error: connection_reset (peer closed)")
        result.fetcher_used = "none"
        assert _diagnose(result)["retryable"] is True

    def test_classification_itself_still_names_the_category(self):
        assert classify_network_error("network_error: dns_failure (TCP preflight)")[0] == "dns_failure"
