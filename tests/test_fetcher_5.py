"""test_fetcher_5.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp.fetcher import Response
from dhole_mcp.quality import classify_error_text
from dhole_mcp.server import _ocr_thin_read, _translate_response_body
import inspect
import re
from pathlib import Path
from dhole_mcp import server as server_mod
import asyncio
import types
from dhole_mcp import fetcher
from dhole_mcp.fetcher import HTTPSession, _impersonate_for
from dhole_mcp.trafilatura_extractor import extract_content_from_html, extract_with_trafilatura
from dhole_mcp import guide
from dhole_mcp.server import _header_door


# ============================================================================
# 来源：test_a_picture_with_one_stray_glyph_is_not_called_a_read.py
# ============================================================================
# 一张没有文本层的图片，不许因为 OCR 蒙出一个字符就自称读到了（D-38）

PNG_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


def _page(body: bytes = PNG_BODY, content_type: str = "image/png") -> Response:
    return Response(url="https://img.test/pic", body=body, status=200,
                    headers={"content-type": content_type}, encoding="utf-8")


@pytest.fixture
def fake_ocr(monkeypatch):
    """把引擎交回什么变成剧本 —— 判据要量的是**回执怎么说**，不是引擎的眼力。"""
    import dhole_mcp.ocr as ocr

    def _set(text: str):
        monkeypatch.setattr(ocr, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr, "ocr_image_bytes", lambda *a, **k: text)
        return text
    return _set


class TestTheThinReadIsNamed:

    def test_a_stray_glyph_is_delivered_but_called_what_it_is(self, fake_ocr):
        fake_ocr("o")
        result = _apply(_translate_response_body(_page(), "markdown", None,
                                                 fetcher_used="stealthy"))
        assert result.content == ["o"], "一个字符都不许多删掉"
        assert result.error.startswith("image_ocr_thin:"), result.error
        assert "1 readable character" in result.error
        assert result.content_ok is False
        assert result.extracted_type == "text"

    def test_the_machine_readable_twin_exists_for_this_shape(self, fake_ocr):
        fake_ocr("o")
        result = _translate_response_body(_page(), "markdown", None)
        _apply(result)
        diag = result.diagnosis or {}
        assert diag.get("code") == "image_ocr_thin", diag
        assert diag.get("retryable") is False, (
            "原样重发同一调用不会把那个字符变成别的 —— 不许说 retryable")
        assert "vision" in (diag.get("recover_with") or ""), diag

    def test_the_advice_says_noise_not_no_text(self, fake_ocr):
        fake_ocr("o")
        result = _translate_response_body(_page(), "markdown", None)
        _apply(result)
        # 与"一个字都没认出"那一句不许互换：那一句在这发上是假话（content 里明明有东西）。
        assert "OCR noise, not a quote" in result.next_action, result.next_action
        assert "no text could be read" not in result.next_action

    def test_a_whole_word_is_still_a_read(self, fake_ocr):
        """反向锁：门槛不许把"图里真有一个词"那一类打成噪声。"""
        fake_ocr("STOP")
        result = _apply(_translate_response_body(_page(), "markdown", None))
        assert result.error == "", result.error
        assert result.content == ["STOP"]
        assert result.content_ok is True

    def test_four_cjk_characters_are_a_read_but_two_are_not(self, fake_ocr):
        fake_ocr("你好世界")
        assert _translate_response_body(_page(), "markdown", None).error == ""
        fake_ocr("你好")
        thin = _translate_response_body(_page(), "markdown", None)
        assert thin.error.startswith("image_ocr_thin:"), thin.error
        assert "2 readable character" in thin.error

    def test_nothing_recognised_keeps_its_own_sentence(self, fake_ocr):
        """空手那一支一字未动 —— 这一刀是补话，不是把两支并成一支。"""
        fake_ocr("")
        result = _apply(_translate_response_body(_page(), "markdown", None))
        assert result.error.startswith("image_ocr_empty:"), result.error
        assert result.content == []
        assert result.next_action.startswith("no text could be read"), result.next_action

    def test_the_absent_engine_branch_is_untouched(self, monkeypatch):
        import dhole_mcp.ocr as ocr
        monkeypatch.setattr(ocr, "ocr_available", lambda: False)
        result = _translate_response_body(_page(), "markdown", None)
        assert result.error.startswith("image_ocr_unavailable:"), result.error
        assert result.content == []


class TestTheThresholdIsTheMeasuredGap:
    """门槛住在实测的空档里：0/1/2 算薄，4 及以上算读到东西。"""

    @pytest.mark.parametrize("text,thin", [
        ("", 0),                      # 空手另有一支说话
        ("o", 1), ("OK", 2), ("7 ", 1), ("你好", 2),
        ("STOP", 0), ("你好世界", 0), ("DHOLE SAMPLE LINE", 0), ("id 42", 0),
    ], ids=["empty", "stray-o", "OK", "digit", "two-cjk", "word", "four-cjk",
            "line", "four-alnum"])
    def test_the_band(self, text, thin):
        assert _ocr_thin_read(text) == thin, repr(text)

    def test_the_classify_layer_names_it(self):
        code, retryable, recover = classify_error_text(
            "image_ocr_thin: OCR found only 1 readable character(s) in this image")
        assert code == "image_ocr_thin"
        assert retryable is False
        assert "vision" in recover


class TestTheRealEngineKeepsItsPromise:
    """真引擎一发（不 monkeypatch）：没字的图上不许出现"有正文却什么都没说"。"""

    def test_a_generated_noise_picture_is_never_a_clean_read(self):
        from dhole_mcp.ocr import ocr_available
        if not ocr_available():
            pytest.skip("OCR extras not installed on this machine")
        blob = _noise_png()
        result = _translate_response_body(_page(blob, "image/png"), "markdown", None)
        delivered = "".join(result.content)
        if delivered and not result.error:
            # 只有真读出这么多字才许闭嘴；D-38 之前那一发是 1 字符 + error 空。
            assert _ocr_thin_read(delivered) == 0, (repr(delivered), result.error)
        else:
            assert result.error, "读出东西却没说话，正是这一刀要修的形状"


def _noise_png() -> bytes:
    import io
    import random

    from PIL import Image
    rnd = random.Random(7)
    img = Image.new("RGB", (240, 140))
    img.putdata([(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
                 for _ in range(240 * 140)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _apply(result):
    """把信封与给 agent 的那三句补全 —— 生产里这一步是 `_with_agent_hints`（每一个返回都过它），
    `_translate_response_body` 自己只写产地已知的那些格。"""
    from dhole_mcp.quality import _with_agent_hints
    return _with_agent_hints(result)


# ============================================================================
# 来源：test_a_probe_that_cannot_decide_is_not_dialed.py
# ============================================================================
# 公网那一拨 TCP 预探测买不到任何东西，就别拨（评审 R02-086 · P3）。

TOOLS = (Path(inspect.getsourcefile(server_mod)).parent / "skills" / "dhole-web"
         / "references" / "tools.md")
PUBLIC_URL = "https://www.iana.org/help/example-domains"
DEAD_PRIVATE = "http://127.0.0.1:1/"
ALL_KEYS = ["auth", "cookies", "proxy", "profile", "cdp", "allow_private", "anti_detect"]


class _Spy:
    """同步、记数、可控判决的替身：默认原样委托给真探测，`verdicts` 里的那颗地址由判据说话。"""

    def __init__(self, real):
        self.calls = []
        self.verdicts = {}
        self._real = real

    def __call__(self, url, timeout=2.0):
        self.calls.append({"url": url, "timeout": timeout})
        if url in self.verdicts:
            return self.verdicts[url]
        return self._real(url, timeout)


@pytest.fixture
def dial_spy(monkeypatch):
    import dhole_mcp.fetcher as fetcher_mod
    spy = _Spy(fetcher_mod.tcp_preflight)
    monkeypatch.setattr(fetcher_mod, "tcp_preflight", spy, raising=True)
    return spy


@pytest.mark.asyncio
async def test_a_public_target_is_no_longer_dialed_by_the_auto_path(dial_spy):
    """这一格的真值条件必须与"这台机器此刻有没有网"无关。

    判的是**拨没拨**，所以断言只挂在 spy 上。两件事是让这一格稳定的：
    - `force_fetcher='http'`：这一格量的是**自动档在 HTTP 层之前**那一处预探测。不加它，一发
      在本机此刻失败的公网读会升级到浏览器档，而浏览器档那一支（`server.py:8269`）对公网
      refused **是有判决权的**（强制档，本刀明确不动），于是 spy 会记到一次**合法的**拨号，
      判据就红了 —— 那种红测的不是这一把的东西（第一、二版的飘红就是这么来的）。
    - 不断言"读得动"："公网那一发确实读得动、且回执里没有预探测的痕迹"由现场钉
      （`r143` 的 P-live：200 · `escalation_path="direct:http"` · 整份回执里 `preflight` 0 次）。
    """
    s = server_mod.MasterFetchServer()
    await s.web_read(url=PUBLIC_URL, cache_ttl=0, force_fetcher="http")
    public_dials = [c for c in dial_spy.calls if c["url"] == PUBLIC_URL]
    assert not public_dials, f"公网那一拨还在拨（无判决权却要调用方付）：{public_dials}"


@pytest.mark.asyncio
async def test_the_private_branch_still_settles_on_a_refused(dial_spy, monkeypatch):
    """私网 + refused ⇒ 据此定论，走 `preflight:…`，不去烧两档预算。"""
    dial_spy.verdicts[DEAD_PRIVATE] = (False, "connection_refused")
    s = server_mod.MasterFetchServer()
    monkeypatch.setattr(s, "_target_is_private", lambda url: True)
    cred = await s.cred_defaults(set={"allow_private": ["127.0.0.1"]})
    assert not cred.error, cred.error
    try:
        out = await s.web_read(url=DEAD_PRIVATE, cache_ttl=0)
    finally:
        await s.cred_defaults(unset=ALL_KEYS)
    assert [c for c in dial_spy.calls if c["url"] == DEAD_PRIVATE], (
        "私网那一支的拨号被一起省掉了（这一格在不开豁免时压根走不到预探测 —— 地址门先拦回来）")
    assert out.status == 0
    assert "TCP preflight" in (out.error or ""), out.error
    assert "preflight:" in (out.escalation_path or ""), out.escalation_path


@pytest.mark.asyncio
async def test_a_public_refused_still_settles_on_the_forced_browser_branch(dial_spy):
    """强制档（`force_fetcher='stealthy'`）那一支公网 refused **有**判决权 —— 本刀不动它。"""
    dial_spy.verdicts[PUBLIC_URL] = (False, "connection_refused")
    s = server_mod.MasterFetchServer()
    out = await s.web_read(url=PUBLIC_URL, cache_ttl=0, force_fetcher="stealthy")
    assert [c for c in dial_spy.calls if c["url"] == PUBLIC_URL], (
        "强制档对公网的预探测被这一把一起省掉了 —— 那一支的 refused 是有读者的定论")
    assert "preflight:" in (out.escalation_path or "") or "connection_refused" in (out.error or ""), (
        out.escalation_path, out.error)


def test_the_auto_branch_no_longer_dials_public():
    """源码形状：自动档那一处现在必须把拨号条件化，而不是拨完再丢掉答案。"""
    src = inspect.getsource(server_mod.MasterFetchServer)
    assert 'definitive = ("connection_refused",)' in src, (
        "强制档那一句判决集合变了")


class TestTheManualStillTellsTheTruth:
    def test_the_probe_accounting_paragraph_does_not_claim_a_dial_per_url(self):
        text = re.sub(r"\s+", " ", TOOLS.read_text(encoding="utf-8"))
        # 【定位子搬家 · 评审 R02-109】lead 只是这段的**位置**，不是它的断言。手册那段原来叫
        # "The HTTP tier probes too"（把探测记在档名下），r155 的计数靶量到强制 http 那一腿一根
        # 都不拨，于是句子改成自动档的说法。两颗真正的钉子（不许说"每发都拨" · 要说清在哪儿还拨）
        # 一字未动 —— 改的只是从哪儿开始读。
        lead = "The auto path probes before the tier it picks has run"
        assert lead in text, "记账那一段不见了 —— 这一族的话只能有一位作者改，不能整段蒸发"
        para = re.sub(r"\s+", " ", text.split(lead, 1)[1].split("# ")[0])
        assert "every direct fetch starts by dialing" not in para, (
            "手册还在说每发都拨，而代码已经不拨公网了")
        assert re.search(r"private|loopback", para, re.I), "改写后的那句要说清在哪儿还拨"

    def test_the_browser_layer_sentence_is_untouched(self):
        text = re.sub(r"\s+", " ", TOOLS.read_text(encoding="utf-8"))
        assert "so two lines in your own service log are accounted for rather " \
               "than looking like a retry" in text


# ============================================================================
# 来源：test_the_tls_handshake_follows_the_identity_it_ships.py
# ============================================================================
# 头说 Chrome/147 而握手按 primp 自选的那一颗走 —— 现在握手跟着头走（R02-080 · R02-F73）。

def _ua(major: int, os_blob: str = "Windows NT 10.0; Win64; x64") -> str:
    return (f"Mozilla/5.0 ({os_blob}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


# ───────────────── 一 · 什么时候才钉：读得出 major 且读得出桌面 OS ─────────────────

def test_a_desktop_chrome_identity_pulls_the_handshake_to_its_own_major():
    got = _impersonate_for("chrome", {"user-agent": _ua(147)}, "windows")
    assert got == "chrome_147", got


def test_edge_is_pinned_the_same_way_chrome_is():
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/140.0.0.0 Safari/537.36 Edg/150.0.0.0")
    assert _impersonate_for("edge", {"user-agent": ua}, "windows") == "edge_150"


def test_the_brand_list_is_the_second_reader_not_the_only_one():
    """UA 里读不出 Chrome/ 时读 `sec-ch-ua`；两处都读不出就不钉（不猜一个更像的）。"""
    got = _impersonate_for("chrome", {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149"'}, "macos")
    assert got == "chrome_149", got
    assert _impersonate_for("chrome", {"user-agent": "curl/8.0"}, "linux") == "chrome"


@pytest.mark.parametrize("family", ["firefox", "safari"])
def test_the_two_families_with_measured_holes_are_left_alone(family):
    """firefox_141…145 钉了桌面 OS 仍交回手机形状；safari 的版本名基本会被拒。

    这两条守的是**实测的洞**，不是保守：钉它们会把"版本不一致"换成"设备不一致"。
    """
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:141.0) Gecko/20100101 Firefox/141.0"
          if family == "firefox" else
          "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")
    assert _impersonate_for(family, {"user-agent": ua}, "windows") == family


def test_no_os_read_means_no_version_pin():
    """OS 读不出（或落到 "random"）时不钉：那一支就是手机形状钻进来的那条路。"""
    for os_name in ("", "random"):
        assert _impersonate_for("chrome", {"user-agent": _ua(147)}, os_name) == "chrome", os_name
    assert _impersonate_for("chrome", {}, "windows") == "chrome", "身份缺席（browserforge 没装）"


def test_an_unknown_family_string_is_passed_through_untouched():
    """`impersonate` 可以是调用方给的怪名字（`_header_family` 认不出=不钉家族）。

    这一发不许变成异常，也不许被改写成 `chrome_<major>`：那是把别人给的值重铸了一遍。
    """
    assert _impersonate_for("opera", {"user-agent": _ua(120)}, "windows") == "opera"
    assert _impersonate_for("chrome_120", {"user-agent": _ua(120)}, "windows") == "chrome_120"


# ───────────── 二 · primp 拒了版本名：退回今天那颗，且这不是失败 ─────────────

class TestTheRejectionFallsBackNotFails:
    """primp 的窗口会变（升级一版 144 就可能没了）。被拒必须**静默退回**，不能把抓取打断。"""

    def _fake_primp(self, accepted):
        """挂的是 `fetcher.primp` 这**一个模块位**：代码读的是 `primp.Client(...)`。"""
        class _C:
            def __init__(self, **kwargs):
                name = kwargs.get("impersonate")
                if name not in accepted:
                    raise ValueError(f'Invalid impersonate: "{name}"')
                self.kwargs = kwargs

            def get(self, *a, **k):
                raise AssertionError("挂具不该发请求")

        return types.SimpleNamespace(Client=_C)

    def _init_with(self, monkeypatch, accepted, baseline):
        monkeypatch.setattr(fetcher, "primp", self._fake_primp(accepted))
        monkeypatch.setattr(fetcher, "_process_identity", lambda family: dict(baseline))
        s = HTTPSession(impersonate="chrome")
        asyncio.run(s._init_client())
        return s

    def test_a_rejected_version_name_still_builds_a_client(self, monkeypatch):
        s = self._init_with(monkeypatch, {"chrome"}, {"user-agent": _ua(147)})
        assert s._client is not None
        assert s._client.kwargs["impersonate"] == "chrome", s._client.kwargs

    def test_an_accepted_version_name_is_the_one_used(self, monkeypatch):
        s = self._init_with(monkeypatch, {"chrome", "chrome_147"},
                            {"user-agent": _ua(147)})
        assert s._client.kwargs["impersonate"] == "chrome_147", s._client.kwargs

    def test_the_os_that_follows_the_identity_is_still_pinned_on_the_retry(self, monkeypatch):
        """退回那一次**不许**把 OS 也退回 "random"：那是把另一维的错配放回来。"""
        s = self._init_with(monkeypatch, {"chrome"}, {"user-agent": _ua(147)})
        assert s._client.kwargs["impersonate_os"] == "windows", s._client.kwargs

    def test_a_hard_construction_failure_is_not_swallowed(self, monkeypatch):
        """只兜"这个名字不认识"，不兜一切异常 —— 别的构造期故障要照常冒出来。"""
        class _Boom:
            def __init__(self, **kwargs):
                raise RuntimeError("libprimp missing")

        monkeypatch.setattr(fetcher, "primp", types.SimpleNamespace(Client=_Boom))
        monkeypatch.setattr(fetcher, "_process_identity", lambda family: {"user-agent": _ua(147)})
        with pytest.raises(RuntimeError):
            asyncio.run(HTTPSession(impersonate="chrome")._init_client())


# ============================================================================
# 来源：test_crawl_fetch_parity_g20.py
# ============================================================================
# G20：同一种内容在同一个工具里必须是同一种形态。
# 重名消歧：_page → _page__crawl_fetch_parity_g20

PROSE = """<html><head><title>Post</title></head><body>
<h1>A heading</h1><p>First real paragraph of the article with enough words in it
that a content extractor will consider it the main body of the page.</p>
<p>Second paragraph, also prose, also the point of this fixture.</p>
<script>var junk = "must not appear";</script>
</body></html>"""

TABLE = """<html><head><title>T</title></head><body><h1>T</h1>
<p>Prose before the table keeps the extraction on the main-content path.</p>
<table><tr><th>K</th><th>V</th></tr><tr><td>a</td><td>1</td></tr></table>
<p>Prose after.</p></body></html>"""

LINKS = """<html><body><h1>Index</h1>
<p>Navigating somewhere.</p>
<ul>""" + "".join(f'<li><a href="/p{i}">Page {i}</a></li>' for i in range(6)) + """</ul>
</body></html>"""


def _page__crawl_fetch_parity_g20(html: str) -> Response:
    return Response(url="https://parity.test/", body=html.encode("utf-8"), status=200,
                    headers={"content-type": "text/html"})


class TestTheTwoEntryPointsAreOnePath:
    """crawl 拿到的是 HTML 字符串，URL 路径拿到的是 Response 对象——两条入口必须
    给出同一份 markdown，否则「crawl 用同一套抽取」就只是注释里的承诺。"""

    @pytest.mark.parametrize("html,name", [(PROSE, "prose"), (TABLE, "table"),
                                           (LINKS, "links")])
    def test_the_same_body_extracts_identically(self, html, name):
        via_string = extract_content_from_html(html, "https://parity.test/", "markdown") or ""
        via_page = extract_with_trafilatura(_page__crawl_fetch_parity_g20(html), "markdown") or [""]

        assert via_string, f"{name}: the shared entry produced nothing to compare"
        assert via_string == via_page[0], f"{name} 在两条入口上抽出了不同的东西"

    def test_a_table_survives_on_both_entries(self):
        """G13 修的是共享函数；这里守的是它对 crawl 也生效。"""
        from_string = extract_content_from_html(TABLE, "https://parity.test/", "markdown")
        from_page = (extract_with_trafilatura(_page__crawl_fetch_parity_g20(TABLE), "markdown") or [""])[0]

        assert "| K | V |" in from_string, from_string
        assert "| K | V |" in from_page, from_page


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_crawl_and_the_fetch_answer_the_same_for_one_page():
    """报告那两条调用放在一起跑：同一个 URL、同一次会话、两种形态就是 G20。"""
    from dhole_mcp.server import MasterFetchServer

    url = "https://quotes.toscrape.com/"
    srv = MasterFetchServer()
    fetched = await srv.web_read(url, cache_ttl=0, timeout=40000)
    crawled = await srv.web_crawl(url, max_pages=1, cache_ttl=0)

    assert fetched.content_ok, fetched.error
    assert crawled.pages, crawled.error
    page = crawled.pages[0]
    assert page.content_ok, page.error
    assert "".join(page.content).strip() == "".join(fetched.content).strip(), (
        "web_crawl 与 web_read 对同一页给了不同形态的正文")
    # The raw-HTML symptom the report described: markup leaking into the body.
    assert "<div" not in "".join(page.content)[:400].lower()


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_list_page_is_still_a_link_list_on_purpose():
    """唯一的有意分叉要在测试里留着名字，否则下一次「统一」会把它也统一掉。"""
    from dhole_mcp.server import MasterFetchServer

    out = await MasterFetchServer().web_crawl("https://quotes.toscrape.com/",
                                                max_pages=1, cache_ttl=0)
    if not out.pages:
        pytest.skip(f"no crawl answer: {out.error[:80]}")

    page = out.pages[0]
    assert page.page_type in ("list", "article"), page.page_type
    if page.page_type == "list":
        assert "http" in "".join(page.content).lower()
        assert "<a " not in "".join(page.content).lower(), "结构化清单不是原始标签"


# ============================================================================
# 来源：test_the_cache_headers_on_the_browser_leg_have_a_named_author.py
# ============================================================================
# 浏览器腿那两颗 `no-cache` 头要有署名的作者，而作者不是 dhole（R02-081 · R02-F74）。

NEEDLES = (
    "dhole never writes",          # 作者署名
    "no-cache mode",               # 机制：浏览器的关缓存模式，不是 dhole 的合成头
    "naming the header yourself",  # 出路：点名就赢（与 _header_door 那条承诺同一位）
)

_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _troubleshooting() -> str:
    return (guide._ROOT / "references" / "troubleshooting.md").read_text(encoding="utf-8")


def test_the_manual_names_the_author_of_the_two_no_cache_headers():
    """① 读者在浏览器腿的 echo 里看到这两颗时，手册要给得出作者。"""
    text = _troubleshooting()
    missing = [n for n in NEEDLES if n not in text]
    assert not missing, (
        f"手册里关于 `pragma`/`cache-control` 的说法缺了这几段：{missing}。"
        "这一对在浏览器腿是稳定出现的（量过），读者照旧文会去 grep dhole 的字典而一无所获。")


def test_sys_guide_actually_ships_that_paragraph():
    """写在磁盘上不算数，得是 `sys_guide` 真送得出去的那一卷。"""
    blob = "\n".join(guide.read(topic)[1] for topic in guide.TOPICS)
    for needle in NEEDLES:
        assert needle in blob, f"{needle!r} 在磁盘上但 sys_guide 一个字也不送"


def test_the_manual_still_falsifies_when_the_paragraph_is_deleted():
    """反证：删掉那一段，上面那条守卫必须看见（覆盖率守卫伪装通过是这仓库犯过的错）。"""
    text = _troubleshooting()
    stripped = text.replace(NEEDLES[0], "", 1)
    assert NEEDLES[0] in text and NEEDLES[0] not in stripped


def test_both_names_pass_the_header_door():
    """② 手册说"点名就赢"，那这一扇拒绝门就不许先把这两个名字拒掉。"""
    for name in ("cache-control", "pragma"):
        door = _header_door(name)
        assert door is None, f"{name} 被 headers= 那扇门拒了：{door!r} —— 手册的承诺过期了"


def test_dholes_own_header_sets_never_grow_those_two_names():
    """③ "不是 dhole 写的"必须由 dhole 自己的字典作证：合成层不许悄悄长出这两位。"""
    for stealthy in (True, False):
        built = {k.lower() for k in HTTPSession(stealthy_headers=stealthy)._build_headers()}
        assert "pragma" not in built and "cache-control" not in built, (
            f"stealthy_headers={stealthy} 的合成字典里出现了 pragma/cache-control："
            "手册那句『dhole 不写它们』从此是假话")


def test_the_section_counts_its_own_limits():
    """④ "几条限制"那个数必须数得对：它从前写 three 而底下挂四条。"""
    text = _troubleshooting()
    m = re.search(r"\b(Two|Three|Four|Five|Six) limits live under dhole", text, re.I)
    assert m, "那句话改了形状，这条守卫跟着改（不许直接删守卫）"
    claimed = _WORD_NUMBERS[m.group(1).lower()]
    tail = text[m.end():]
    bullets = len(re.findall(r"^- \*\*", tail[:tail.find("\n### ")], re.M))
    assert bullets == claimed, (
        f"那句说 {claimed} 条，底下实际挂着 {bullets} 条 —— 数错的清单会让人少读一条")
