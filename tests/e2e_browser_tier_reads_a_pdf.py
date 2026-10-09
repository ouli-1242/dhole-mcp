# -*- coding: utf-8 -*-
"""A REAL Chrome must hand back the file itself when it is aimed at a PDF/PNG (D-41).

Why this file exists: the D-27 judgment for PDFs was written against a fake whose
``resp.body()`` returned real PDF bytes. Chrome does not do that. Measured on the
connected server (2026-10-09, pid 7280), reading ``rfc2616.pdf`` with
``force_fetcher='stealthy'``:

    content-type  : application/pdf                     <- the headers are real
    resp.body()   : 601 bytes starting ``<!doctype html>``  <- Chrome's viewer document
    page.content(): 271 bytes of empty shell (the PDF is painted by a plugin)
    context.request.get(same url, max_redirects=0)
                  : 200 / 550,558 bytes / ``%PDF-1.1``   <- the file lives on this layer

So "take the original, not the frame" became a two-layer question, and the layer that
carries a PDF is only reachable by a real browser. A fake cannot be trusted here again,
which is why this e2e serves a locally generated PDF over loopback and asserts what the
*reader* gets back.

Run manually:
    pytest -m e2e tests/e2e_browser_tier_reads_a_pdf.py -v

Skips cleanly when no patchright/chromium is installed. Never runs in CI.
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.e2e

PDF_MARKER = "D41 ORIGINAL PDF MARKER 31415"
PNG_MARKER_TEXT = "D41PNG"


def _make_pdf() -> bytes:
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 120), PDF_MARKER, fontsize=16)
    out = doc.tobytes()
    doc.close()
    return out


def _make_png() -> bytes:
    import io
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (520, 150), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=56)
    except TypeError:
        font = ImageFont.load_default()
    draw.text((20, 50), PNG_MARKER_TEXT, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@contextlib.contextmanager
def _document_target():
    """本机靶子：一份 PDF、一张 PNG，外加一本按路径记的账。

    那本账是这一把的证人：修 D-41 之后同一 URL 会被要两次（导航一次、上下文自己的
    请求通道一次），账上必须**只有这一个路径** —— 第二发若换了地址，这里就看得见。
    """
    pdf, png = _make_pdf(), _make_png()
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            body, ct = ((pdf, "application/pdf") if self.path == "/doc.pdf"
                        else (png, "image/png") if self.path == "/pic.png"
                        else (b"no such path", "text/plain; charset=utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", seen
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()


async def _fetch_stealthy(url: str):
    from dhole_mcp.browser import StealthyBrowser
    session = StealthyBrowser(headless=True, timeout=60000)
    await session.start()
    try:
        return await session.fetch(url, timeout=60000)
    finally:
        await session.close()


def _reader_text(response, content_type: str):
    from dhole_mcp.server import _translate_response_body
    from dhole_mcp.fetcher import Response
    built = Response(url=response.url, body=response.body, status=response.status,
                     headers={"content-type": content_type}, encoding="utf-8")
    result = _translate_response_body(built, "markdown", None, fetcher_used="stealthy")
    return result, " ".join(result.content)


def test_a_pdf_navigation_returns_the_file_not_the_viewer():
    """真 Chrome 打一份 PDF：读者的正文里必须有那行字，而不是"这不是 PDF"。"""
    async def run() -> str:
        from dhole_mcp.browser import check_browser_available
        if not check_browser_available():
            return "skip"
        with _document_target() as (base, seen):
            response = await _fetch_stealthy(f"{base}/doc.pdf")
            body = response.body or b""
            assert body[:5] == b"%PDF-", (
                f"浏览器档交回来的不是那份 PDF，开头是 {body[:40]!r}")
            assert len(body) == len(_make_pdf()) or len(body) > 300, len(body)
            result, text = _reader_text(response, "application/pdf")
            assert not result.error, result.error
            assert PDF_MARKER in text.replace("\n", " "), (result.error, text[:200])
            assert "/pic.png" not in seen and "/nope" not in seen
            assert set(seen) == {"/doc.pdf"}, f"第二发换了地址：{seen}"
        return "done"

    outcome = asyncio.run(run())
    if outcome == "skip":
        pytest.skip("no patchright/chromium on this machine")


def test_an_image_navigation_still_returns_its_own_bytes():
    """图片那一半在真浏览器里再锁一次：它的原件挂在网络响应那一层，不该多跳一发。"""
    async def run() -> str:
        from dhole_mcp.browser import check_browser_available
        if not check_browser_available():
            return "skip"
        with _document_target() as (base, seen):
            response = await _fetch_stealthy(f"{base}/pic.png")
            body = response.body or b""
            assert body[:8] == b"\x89PNG\r\n\x1a\n", body[:40]
            assert set(seen) == {"/pic.png"}, seen
        return "done"

    outcome = asyncio.run(run())
    if outcome == "skip":
        pytest.skip("no patchright/chromium on this machine")
