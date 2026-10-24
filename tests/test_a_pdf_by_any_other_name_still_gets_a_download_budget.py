"""不以 `.pdf` 结尾的 PDF URL，同样该拿「下载」的预算（提升计划 3.3）。

现场（2026-10，真实站点实测）：11MB 的 arxiv PDF（`arxiv.org/pdf/<id>`，路径里没有
`.pdf` 三个字母）在 45s 预算下两档皆输——HTTP 档按「页面」走，学习天花板先把它掐死；
空 status 0 又过不了 `.pdf` 那颗**手抄**的升级闸（`quality._never_escalate`），于是起
浏览器去下同一个更慢的文件。三件本来都有的保护（整篇文档的单次尝试预算、尺寸预检、
不升级）全都挂在 `_is_document_url` 上，而它只认扩展名。

修法：路径段**等于** `pdf`（`/pdf/<id>`、`/pdf?id=`）也算文档 URL。误伤面窄
（`/pdf-tools/guide` 这类前缀段不算），且响应回来后 `_is_document` 会按 content-type
改判。`quality._never_escalate` 里那份手抄判据同步扩——两份定义由本文件的一致性用例
钉住，改一边不改另一边会当场红。
"""

from __future__ import annotations

import pytest

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _never_escalate
from dhole_mcp.server import _http_tier_budget, _is_document_url

DOCUMENT_URLS = [
    "https://arxiv.org/pdf/1706.03762",
    "https://arxiv.org/pdf/1706.03762v3",
    "https://www.biorxiv.org/content/10.1101/2024.01.01.000000v1.full.pdf",
    "https://openreview.net/pdf?id=abc123",
    "https://arxiv.org/pdf/1706.03762.pdf",   # 老形状照旧
    "https://arxiv.org/pdf/",                  # 尾段就是 pdf
]
PAGE_URLS = [
    "https://arxiv.org/abs/1706.03762",
    "https://site.test/pdf-tools/guide.html",  # 前缀段，不是 pdf 段
    "https://site.test/pdfx/1",
    "https://site.test/guide",
    "https://en.wikipedia.org/wiki/PDF",
]


@pytest.mark.parametrize("url", DOCUMENT_URLS)
def test_a_pdf_segment_is_a_document_url(url):
    assert _is_document_url(url) is True, url


@pytest.mark.parametrize("url", PAGE_URLS)
def test_pages_are_not_documents(url):
    assert _is_document_url(url) is False, url


def _bare(url: str) -> ResponseModel:
    """无 content-type 的 GET 结果：`_never_escalate` 只剩 URL 判据可读。"""
    return ResponseModel(url=url, status=0, content=[], fetcher_used="http")


@pytest.mark.parametrize("url", DOCUMENT_URLS + PAGE_URLS)
def test_the_two_pdf_predicates_agree(url):
    """双份定义的一致性：`_never_escalate` 的 URL 判据 == `_is_document_url`。"""
    assert _never_escalate(_bare(url), url) == _is_document_url(url), url


def test_the_download_budget_ignores_the_learned_page_ceiling(monkeypatch):
    """`/pdf/` URL 走 `document=True` 的那条账：不吃学习天花板、单次尝试。"""
    from dhole_mcp import server as srv
    # 哪怕这台主机「习惯 2 秒答完」，也与 45s 的文件下载无关
    monkeypatch.setattr(srv, "_adaptive_timeout", lambda *a, **k: 2_000)
    for document in (True, False):
        seconds, retries, bound = _http_tier_budget(
            "https://arxiv.org/pdf/1706.03762", 45_000, 45.0,
            document=document)
        if document:
            assert seconds >= 45, (seconds, bound)
            assert retries == 0, "文档下载不重试：重试从字节零重下"
