"""一份中文 HTML 不是二进制 —— 「可打印 ASCII 字节的比例」量的不是这件事。

现场（2026-10-14 真实使用模拟 · P02-02 邝野（游戏叙事 L3）· S2）：本地靶站的
`/longaside` 是一份中文页（`text/html; charset=utf-8`，18,382 字节，整页可见字 7,149）。
`web_read` 交回的 `content[0]` 是一句 138 字符的占位：

    [Binary content detected. Cannot extract text from this URL. Content type may be
     PDF, image, or other non-text format. Size: 18,382 bytes]

同一份响应里 `content_type` 写着 `text/html; charset=utf-8`，`error` 写着
「the words ARE in the HTML, dhole's reader lost them」。**一份回执对自己说了两种话**，
而写 pipeline 的调用方读的是 `content[]`：英文那一句会被当成语料里的乱码、或者当成
「该换个工具」的信号 —— 两边都是产品账上的钱。

判据本身也是错的，而且错两个方向（`review/.runtime/p4/measure_binary_test.py`，
仓库自带的真件与靶页，窗口 4,096 字节）：

    样本                          旧的「可打印比例」  旧判成二进制   新的「解码后控制符比例」
    /longaside（中文 html）            0.069            **是**            0.0006
    tests/page_fixtures/gnu html       0.972            否                0.0000
    tests/dummy.pdf                    0.444            **否**            0.5226
    tests/sample.docx                  0.373            **否**            0.5606
    tests/figure_page.pdf              0.677            **否**            0.2782

一侧是「文本被判成二进制」，另一侧是「真的二进制（PDF / OOXML）从这条判据下面溜过去，
再由后面的抽取器一顿乱解码」。两侧差 **400 倍**，阈值取 0.05。

机制：`_is_probably_binary` 数的是字节落在 32–126（加 \\t\\n\\r）里的比例。UTF-8 的汉字
一个字节都不在那个区间，于是页面上中文越多，比例越接近 0 —— 这条判据实际上量的是
「这页是不是纯 ASCII」，而它被用来决定「这要不要送去当文本读」。

契约（本文件钉住）：
  * 一份中文 HTML（`text/html; charset=utf-8`）走正常抽取，答复里没有那句占位。
  * 二进制判据看的是**按响应自己的字符集解码之后**的样子：控制符 / 替换符比例超过
    阈值才算二进制。汉字解码后没有控制符，PDF / ZIP 那类解码后一半以上是。
  * 反面锁（不弱化门禁）：`tests/dummy.pdf`、`sample.docx`、`figure_page.pdf` 这些
    真件**仍然**判成二进制 —— 这一刀把判据换对，不是把判据拆掉。
  * 一句自我矛盾的话不许出现在回执里：content_type 说 text/html 时，占位里那句
    "may be PDF, image, or other non-text format" 不许被写进 `content[]`。
  * 路径锁：`web_read` 全链路（本地 HTTP 靶）也要读到那份中文正文。
"""

from __future__ import annotations

import http.server
import os
import socketserver
import threading

import pytest

from dhole_mcp import trafilatura_extractor
from dhole_mcp.fetcher import Response
from dhole_mcp.server import MasterFetchServer

PLACEHOLDER_HEAD = "Binary content detected"

CJK_SENTENCE = ("第 {i} 段把同一个专题往前推了一层：先说资料从哪儿来、谁在什么时候"
                "抄录得它，再说这些抄本彼此的出入集中在哪几处，最后给出一个不妨碍"
                "阅读的判断，让后面引用它的人不必再回到原卷去核每一句话的真伪。")


def _cjk_page(paragraphs: int = 40) -> bytes:
    """靶页同形：`<article>` 里 40 段中文，aside 里再挂同样多的一段。"""
    prose = "".join(f"<p>{CJK_SENTENCE.format(i=i)}</p>" for i in range(paragraphs))
    aside = "".join(f"<p>附注 {i}：同一批资料的另一份摘记，长度与正文相当。</p>"
                    for i in range(paragraphs))
    return (f"<html><head><meta charset=\"utf-8\"><title>专题综述</title></head><body>"
            f"<article>{prose}</article><aside>{aside}</aside></body></html>").encode("utf-8")


def _ascii_printable_share(raw: bytes) -> float:
    """旧判据数的那个东西：字节落在 ASCII 可打印区里的比例。"""
    return sum(1 for b in raw if 32 <= b <= 126 or b in (9, 10, 13)) / max(len(raw), 1)


def _response(body: bytes, content_type: str) -> Response:
    return Response(url="http://x.test/doc", body=body, status=200,
                    headers={"content-type": content_type})


def _fixture(name: str) -> bytes:
    return open(os.path.join(os.path.dirname(__file__), name), "rb").read()


class TestAChinesePageIsNotBinary:

    def test_the_page_under_test_still_has_the_defects_shape(self):
        """合成页必须仍然是那个形状 —— 否则这一测测的不再是本缺陷。

        这条红不是在测产品：谁把这段中文改短了、或者把标签加多了，ASCII 可打印比例回到
        0.10 以上，旧判据就不会再说「二进制」，后面那些断言全部失去意义。所以把它写成
        一枚闸，而不是一句注释。
        """
        window = _cjk_page()[:4096]
        assert _ascii_printable_share(window) < 0.10, (
            f"这一页的 ASCII 可打印字节比例是 {_ascii_printable_share(window):.3f}，"
            "已经不是现场那份中文页的形状了（实测 /longaside 是 0.069）")

    def test_the_cjk_page_is_not_answered_with_a_binary_placeholder(self):
        body = _cjk_page()
        out = trafilatura_extractor.extract_with_trafilatura(
            _response(body, "text/html; charset=utf-8"), extraction_type="markdown")
        text = "\n".join(out)
        assert PLACEHOLDER_HEAD not in text, (
            f"一份 text/html; charset=utf-8 的中文页被当成二进制交了出去：{text[:160]!r}")
        assert "同一个专题往前推了一层" in text, f"正文没回来：{text[:160]!r}"

    def test_the_receipt_does_not_contradict_its_own_content_type(self):
        """占位那句 "may be PDF, image..." 与同一份回执里的 content_type 是对立的。"""
        page = _cjk_page()
        out = "\n".join(trafilatura_extractor.extract_with_trafilatura(
            _response(page, "text/html; charset=utf-8"), extraction_type="text"))
        assert PLACEHOLDER_HEAD not in out

    def test_the_measured_ruler_separates_the_two_sides_by_two_orders(self):
        """阈值两侧的量是量出来的：文本 <=0.001，真件 >=0.25。"""
        assert trafilatura_extractor.BINARY_CONTROL_MAX == pytest.approx(0.05)
        text_samples = [("cjk html", _cjk_page(), "utf-8"),
                        ("gnu html", _fixture("page_fixtures/gnu_free_sw_definition.html"),
                         "utf-8")]
        binary_samples = [("dummy.pdf", _fixture("dummy.pdf")),
                          ("figure_page.pdf", _fixture("figure_page.pdf")),
                          ("sample.docx", _fixture("sample.docx"))]
        for name, raw, enc in text_samples:
            ratio = trafilatura_extractor._control_char_ratio(raw, enc)
            assert ratio < 0.01, f"{name} 的控制符比例是 {ratio:.4f}，文本那一侧不该这么高"
        for name, raw in binary_samples:
            ratio = trafilatura_extractor._control_char_ratio(raw, "utf-8")
            assert ratio > 0.2, f"{name} 的比例只有 {ratio:.4f} —— 真件这一侧被改小了"

    def test_the_real_binaries_still_refuse_to_be_read_as_text(self):
        """反面锁：这一刀换的是判据，不是把门禁拆掉（§12 不弱化）。"""
        for name in ("dummy.pdf", "figure_page.pdf", "sample.docx"):
            body = _fixture(name)
            assert trafilatura_extractor._is_binary_payload(
                body, "application/octet-stream"), f"{name} 不再被当成二进制了"

    def test_a_utf16_page_is_not_binary_because_its_bytes_hold_nuls(self):
        """UTF-16 的正文里一半字节是 0x00 —— 旧的字节级 NUL 判据在这里说假话。

        新判据先按响应自己的字符集解码再数控制符：解码成功后那些 0x00 是编码的一部分，
        不是字符。这一格是拆掉旧 NUL 短路时**自己**要还的债，不是设想出来的场景。
        """
        text = "第 3 段讲的是同一个专题的进展。"
        body = ("<html><head><meta charset=\"utf-16\">"
                "<title>专题</title></head><body><article>" + text +
                "</article></body></html>").encode("utf-16")
        assert b"\x00" in body[:1000], "前提：这份载荷的字节里确实满是 NUL"
        assert not trafilatura_extractor._is_binary_payload(body, "text/html; charset=utf-16")


# ─── 路径锁 ─────────────────────────────────────────────────────────────────

PAGE = _cjk_page()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


@pytest.mark.asyncio
async def test_the_online_route_reads_the_chinese_body(origin):
    r = await MasterFetchServer().web_read(origin + "/zhuanti", max_content_chars=200_000)
    body = "\n".join(r.content)
    assert PLACEHOLDER_HEAD not in body, (
        f"主路上交回来的还是那句二进制占位：{body[:200]!r}")
    assert "同一个专题往前推了一层" in body, f"正文没回来：{body[:200]!r}"
    assert r.content_ok is True, (
        f"读到了 40 段中文还写着 content_ok={r.content_ok}：{r.error}")
