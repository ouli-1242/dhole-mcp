"""靶站新增的扇出靶页（E-13）：文档写了这四个端点，就得真能按路径取到。

`evals/fixture_site.py` 的模块 docstring 把 `/collections` `/work/<n>` `/shell`
`/about` 列在端点表里，`_collections_page` / `_work_page` / `SHELL_PAGE` /
`ABOUT_PAGE` 也都在文件里 —— 但路由那一段一度没有把它们接上，于是文档描述的是
四个 404。这个项目最忌讳的失败形状就是「文档说 X、现实说 Y」，所以这里钉的不是
页面函数的返回值，而是**按那个 URL 请求能不能拿到它**：走一遍真 socket。

另一半钉的是这几张页**故意摆出来的坑** —— 它们存在的理由是让 E-13 的判据有靶可打，
靶子形状悄悄变了，判据就会在量空气：

  - `/collections` 默认 10 张 `.card`，外加三处「少给了」：一张没有 href 的卡片、
    第一项那条只差 `#top` 的重复链接、以及末尾一条不构成一组的 `.menu` 孤链接。
    去重、跳过、截断三种账各说各的，靶页就得各摆一个。
  - `/work/<n>` 上有一列每页不同（`.summary`）、一列每页同值（`.site-footer`，
    页眉陷阱）、以及一颗这站上根本不存在的 `.rating`（选择器不指东西）。
  - `/shell` 组不成任何一组链接：用来逼出「这一页没内容 vs 选择器写窄了」两条路
    里前一条。

`/collections` 与 `/work/<n>` 的计数口径（绝对化 + 去重）与 `nested.resolve_nested_links`
一致，所以这里的断言数的是"扇出真会抓几条"，不是 DOM 里有几个 `<a>`。
"""

from __future__ import annotations

import socket
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import fixture_site as fs  # noqa: E402

# `conftest._offline_dns`（autouse）把 `socket.getaddrinfo` 换成一个固定的公网
# 地址，好让套件不碰机器自己的 resolver。这个模块是在收集期 import 的，那时它还没
# 被换 —— 于是这里留一份真的，供 `live_site` 在回环上真连一次。
_REAL_GETADDRINFO = socket.getaddrinfo


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    """真起一次靶站（回环、0 号端口），并把请求日志引到 tmp，不落进 `evals/.runtime`。"""
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def _get(base: str, path: str) -> tuple[int, str]:
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, r.read().decode("utf-8")


class TestTheDocumentedTargetsAreActuallyRouted:
    @pytest.mark.parametrize("path", ["/collections", "/work/1", "/shell", "/about"])
    def test_the_documented_endpoints_resolve(self, live_site, path):
        status, body = _get(live_site, path)
        assert status == 200
        assert body.strip()

    def test_a_non_numeric_work_id_is_a_404_not_a_crash(self, live_site):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(live_site, "/work/abc")
        assert e.value.code == 404

    def test_a_non_numeric_items_is_a_400(self, live_site):
        with pytest.raises(urllib.error.HTTPError) as e:
            _get(live_site, "/collections?items=x")
        assert e.value.code == 400


class TestTheCollectionPageCarriesItsThreeDeliberateShortfalls:
    def test_default_is_ten_cards_plus_a_duplicate_and_a_linkless_one(self, live_site):
        _, html = _get(live_site, "/collections")
        # 10 张正常卡片 + 1 张重复 + 1 张无链接
        assert html.count('class="card"') == fs.COLLECTIONS_ITEMS + 2
        # 11 条 /work/ 链接：10 条唯一 + 1 条只差 #top 的重复（去重要把它并掉）
        assert html.count('href="/work/') == fs.COLLECTIONS_ITEMS + 1
        assert 'href="/work/1#top"' in html, "重复项要只差一个 fragment，否则不是去重靶"
        assert "<span>没有链接的一张</span>" in html, "要有卡片本身不带 href 的形状"
        assert 'class="menu"' in html, "孤链接不构成一组，候选选择器里不该有它"

    def test_items_controls_the_count(self, live_site):
        _, html = _get(live_site, "/collections?items=3")
        assert html.count('class="card"') == 3 + 2

    def test_items_clamps_to_the_ceiling(self, live_site):
        _, html = _get(live_site, f"/collections?items={fs.COLLECTIONS_MAX + 50}")
        assert html.count('class="card"') == fs.COLLECTIONS_MAX + 2


class TestTheWorkPageHoldsTheColumnTraps:
    def test_one_column_differs_and_one_is_page_chrome(self, live_site):
        _, a = _get(live_site, "/work/1")
        _, b = _get(live_site, "/work/2")
        assert '<h1 class="title">作品 1</h1>' in a
        assert '<h1 class="title">作品 2</h1>' in b
        assert "第 1 篇的独有正文" in a and "第 2 篇的独有正文" in b
        # 页眉那一列：两页必须**逐字相同**，否则"整列同值"的靶就没了。
        footer_a = a[a.index('class="site-footer"'):].split("</footer>")[0]
        footer_b = b[b.index('class="site-footer"'):].split("</footer>")[0]
        assert footer_a == footer_b

    def test_a_selector_that_points_at_nothing_has_a_home(self, live_site):
        _, html = _get(live_site, "/work/1")
        assert "rating" not in html, "这站上不该有 .rating —— 它是'选择器不指东西'的靶"


class TestTheShellPageHasNoGroupOfLinks:
    def test_shell_offers_at_most_a_lone_link(self, live_site):
        _, html = _get(live_site, "/shell")
        assert html.count("<a ") == 1, "壳页的判据就是'组不成一组链接'"
