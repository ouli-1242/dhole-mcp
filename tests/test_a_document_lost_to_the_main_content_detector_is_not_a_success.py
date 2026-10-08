"""一份被"正文检测"丢掉的文档，不许当成提取成功（2026-10-08 真实使用模拟 · 刀8）

现场：`web_read("https://www.rfc-editor.org/rfc/rfc9110.html")` 交出 8,357 字符，
`content_ok: true`，正文从 "### 6.4." 开始 —— 一份 1,187,554 字节、去掉标签后仍有
379,285 个非空白字符的规范文档，**开头到 6.3 节整段不见了**。

机制（同一份字节直接对比量的）：trafilatura 自己只返回 7,128 字符（占文档正文的
2.20%），而 dhole 的 markdownify 兜底返回 587,795。`extract_content` 的判据是
"candidate 非空就采纳"，于是主内容检测器把这份文档误判成大半是模板，99% 的正文被
静默丢掉，读者拿到一份看着成功的残卷。

阈值是量出来的，不是拍的（`.scratch/measure_trafo_yield.py`，20 个真页面）：

    2.20%  rfc-editor.org/rfc/rfc9110.html      <-- 唯一异常
   46.74%  pypi.org/project/requests/            <-- 次低
   47.47%  nytimes.com/  ·  71.59% docs.astral.sh  ·  其余全部 >= 76%

异常与正常之间有 20 倍空档，取 12%；再加一道"文档本身够大"的下限（20,000 个非空白
字符），因为 447 字符的小页算不出"丢了 99%"这种话。

契约（本文件钉住）：
  * 大文档 + 提取量占其自身正文低于地板 → 换兜底提取器，并在 summary announce
    换了谁、为什么（每一处钳制都要 announce 是这仓已有的纪律）。
  * 健康的提取一律不动（反向锁）。
  * 文档不够大不触发；`css_selector=` 是调用方自己收窄的，不触发。
  * trafilatura 整段空白的老路照旧走兜底。
  * 量不到"这份文档有多少字"就**不动**（fail closed）：判据是来评判提取厚薄的，
    没有权利把一次成功的抓取弄成异常。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp import context, extractor, trafilatura_extractor
from dhole_mcp.fetcher import Response
from dhole_mcp.server import MasterFetchServer

PARA = "The hypothesis that mind is embodied in action has a long pedigree. "


def _document(paragraphs: int = 500) -> bytes:
    """A page whose real text is `paragraphs` x ~70 chars, plus chrome around it."""
    body = "".join(f"<p>{PARA}{i} end{i:03d}</p>" for i in range(paragraphs))
    return (f"<html><head><title>Spec</title><style>{'x' * 200}</style></head>"
            f"<body><nav>{'menu ' * 40}</nav><main>{body}</main>"
            f"<footer>{'legal ' * 40}</footer></body></html>").encode()


def _page(body: bytes) -> Response:
    return Response(url="https://spec.test/rfc9110", body=body, status=200,
                    headers={"content-type": "text/html; charset=utf-8"})


def _fragment_of(body: bytes, fraction: float) -> list:
    """A stand-in for "the detector kept a slice and called it the document"."""
    text = body.decode()
    keep = max(1, int(len(text) * fraction))
    return ["### \n6.4. Content\n\n" + text[:keep]]


@pytest.fixture
def no_trafilatura(monkeypatch):
    """把真检测器换成一个可控的"它交回多少"，其余（兜底、折叠、宣布）全走原路。"""
    box: dict = {"answer": None}

    def fake(page, extraction_type="markdown", css_selector=None, **kw):
        a = box["answer"]
        return a if a is not None else ["real trafilatura answer"]

    monkeypatch.setattr(trafilatura_extractor, "extract_with_trafilatura", fake)
    return box


class TestADocumentLostToTheDetectorIsRecovered:

    def test_the_fallback_answers_and_the_swap_is_announced(self, no_trafilatura):
        body = _document()
        no_trafilatura["answer"] = _fragment_of(body, 0.02)
        with context._arg_notes_scope() as notes:
            out = extractor.extract_content(_page(body), extraction_type="markdown")
        text = "\n".join(out)
        assert "end499" in text, (
            f"文档的结尾没回来，还是那份残卷：{len(text):,} chars")
        swap = [n for n in notes if "main-content detector" in n]
        assert swap, f"换了提取器却没说：{notes}"
        assert "%" in swap[0] and "32,754" in swap[0], swap[0]

    def test_a_healthy_extraction_is_left_alone(self, no_trafilatura):
        body = _document()
        no_trafilatura["answer"] = _fragment_of(body, 0.70)
        with context._arg_notes_scope() as notes:
            out = extractor.extract_content(_page(body), extraction_type="markdown")
        assert "\n".join(out).startswith("###"), "健康的那一路不许被换掉"
        assert not any("main-content detector" in n for n in notes), notes

    def test_a_small_page_never_trips_the_floor(self, no_trafilatura):
        """447 个字符的页算不出"丢了 99%"这种话，也就没有被换的理由。"""
        body = _document(6)
        no_trafilatura["answer"] = _fragment_of(body, 0.02)
        with context._arg_notes_scope() as notes:
            out = extractor.extract_content(_page(body), extraction_type="markdown")
        assert "\n".join(out).startswith("###"), "小页不许触发翻转"
        assert not any("main-content detector" in n for n in notes), notes

    def test_a_selector_the_caller_chose_is_not_a_loss(self, no_trafilatura):
        body = _document()
        no_trafilatura["answer"] = _fragment_of(body, 0.02)
        out = extractor.extract_content(_page(body), extraction_type="markdown",
                                        css_selector="main")
        assert "\n".join(out).startswith("###"), (
            "调用方自己收窄了范围，交回的少是预期，不是提取器丢了东西")

    def test_an_empty_answer_still_falls_back(self, no_trafilatura):
        """老行为不许被这批改坏：trafilatura 整段空白时本来就走兜底。"""
        body = _document()
        no_trafilatura["answer"] = ["   "]
        out = extractor.extract_content(_page(body), extraction_type="markdown")
        assert "end499" in "\n".join(out)

    def test_the_measured_boundary_from_the_20_page_sample(self, no_trafilatura):
        """量出来的那两个数：rfc 的 2.20% 必须翻，次低的 46.74% 必须不翻。"""
        body = _document()
        for ratio, flips in ((0.022, True), (0.4674, False)):
            no_trafilatura["answer"] = _fragment_of(body, ratio)
            with context._arg_notes_scope() as notes:
                out = "\n".join(extractor.extract_content(
                    _page(body), extraction_type="markdown"))
            swapped = "end499" in out
            assert swapped is flips, f"ratio={ratio:.2%} 判成了 {swapped}"
            announced = any("main-content detector" in n for n in notes)
            assert announced is flips, (
                f"ratio={ratio:.2%}: 换了却没说" if flips else f"没换却说成了换：{notes}")


class TestTheFloorIsStatedInOnePlace:

    def test_the_constants_are_named_and_the_sample_sits_in_the_gap(self):
        assert extractor._MAIN_CONTENT_FLOOR == pytest.approx(0.12)
        assert extractor._MIN_DOCUMENT_CHARS == 20_000
        # 空档：异常侧 2.20%，正常侧 46.74% —— 阈值两边都差着一个数量级。
        assert 0.022 < extractor._MAIN_CONTENT_FLOOR < 0.4674


class TestAPageThatWillNotAnswerTheMeasurement:
    """`extract_content` 收的是"响应形状的对象"（它自己的 docstring 就这么写的），
    不是 fetcher.Response 这一个类。量不到就 fail closed —— 原样交回检测器的答复，
    而不是把一次本来成功的抓取弄成异常。

    这是全套跑出来的，不是设想出来的：5 个原本绿的 `bulk_get` 测试挂了，挂具的
    `.content` 是自动长出来的桩物（属性在，但不是文本），新判据在正则门口
    TypeError。一个"判断提取够不够厚"的探针没有权利决定这次抓取成不成。
    """

    def _shape(self, content):
        from unittest.mock import MagicMock
        page = MagicMock()
        page.status = 200
        page.body = _document()
        page.url = "https://spec.test/rfc9110"
        page.encoding = "utf-8"
        page.headers = {"content-type": "text/html; charset=utf-8"}
        page.content = content
        return page

    @pytest.mark.parametrize("not_text", ["stand-in", None, 42],
                             ids=["stand-in", "none", "int"])
    def test_a_non_text_content_yields_no_measurement(self, not_text):
        if not_text == "stand-in":
            from unittest.mock import MagicMock
            not_text = MagicMock()
        assert extractor._document_text_chars(self._shape(not_text)) == 0

    def test_a_content_that_raises_yields_no_measurement(self):
        class _Wrecked:
            @property
            def content(self):
                raise RuntimeError("body will not decode")
            body = _document()
        assert extractor._document_text_chars(_Wrecked()) == 0

    def test_raw_bytes_are_still_measured(self):
        """不是文本就不量 —— 但字节是文本的一种，解码后照常算，不许误判成 0。"""
        assert extractor._document_text_chars(self._shape(_document())) > 20_000

    def test_the_fetch_that_cannot_be_measured_still_succeeds(self, no_trafilatura):
        """上面那些形状走到提取链上，答复照旧交回，不炸也不announce 假话。"""
        body = _document()
        no_trafilatura["answer"] = _fragment_of(body, 0.02)
        with context._arg_notes_scope() as notes:
            out = extractor.extract_content(self._shape(None), extraction_type="markdown")
        assert out == no_trafilatura["answer"], "量不到却换了答复，等于拿猜测定成败"
        assert not any("main-content detector" in n for n in notes), notes


# ─── 路径锁 ─────────────────────────────────────────────────────────────────
# 2026-10-06 那一刀的教训写在 test_a_live_fetch_folds_the_boilerplate_it_reports.py
# 的第一段里：守卫钉住了函数、没钉住路径 —— 线上主路 server.py 直接调
# extract_with_trafilatura，`extract_content` 里的判据一次也没跑。同一个洞换了个
# 判据又出现一次，所以这批改完必须从 `web_read` 全链路再钉一遍。

BIG_PAGE = (b"<html><head><title>Spec</title></head><body><main>"
            + b"".join(f"<p>Clause {i} of the specification has operative text. "
                       f"end{i:03d}</p>".encode() for i in range(600))
            + b"</main></body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *\nAllow: /")
            return
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(BIG_PAGE)))
        self.end_headers()
        self.wfile.write(BIG_PAGE)

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
async def test_the_online_route_runs_the_guard_not_just_the_unit(origin, monkeypatch):
    """`web_read` 全链路：检测器交回切片时，主路要换成完整的答复并说一声。"""
    def lost(page, extraction_type="markdown", css_selector=None, **kw):
        return ["### \n6.4. Content\n\n" + page.content[:900]]

    monkeypatch.setattr(trafilatura_extractor, "extract_with_trafilatura", lost)
    r = await MasterFetchServer().web_read(origin + "/spec", max_content_chars=200_000)
    body = "\n".join(r.content)
    assert "end599" in body, (
        f"主路又采纳了那份切片（{len(body):,} chars）—— 守卫只在单元里跑")
    assert "main-content detector" in r.summary, "换了提取器要在 summary 里看得见"
