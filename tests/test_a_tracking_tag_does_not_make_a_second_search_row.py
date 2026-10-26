"""同一页的跟踪参数变体只许占一行（E2，search 侧；recall 侧已由 R02-007 修掉）。

现场（R01 P03）：知乎同文两行 —— 原链 + `?utm_id=0`。recall 的变体合并在 R02-007
落了地，search 侧仍把两者当两个 URL，因为归一化只认一张写死的参数名表，漏了
`utm_*` 这一族。

修的是**归一化本身**：以 `utm_` 开头的键一律不算页面身份（这是一族定义，不是一张
会过期的名单）；真正的内容参数（`page=`、`id=` 等）照旧区分页面。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dhole_mcp import search_metasearch as ms

PAGE = "https://zhuanlan.zhihu.com/p/473352034"


@pytest.mark.parametrize("tagged", [
    PAGE + "?utm_id=0",
    PAGE + "?utm_source=wechat&utm_medium=social",
    PAGE + "?UTM_CUSTOM=anything",
], ids=["utm-id", "utm-family", "case"])
def test_a_utm_tag_is_not_part_of_the_page_identity(tagged):
    assert ms._normalize_url(tagged) == ms._normalize_url(PAGE)


def test_a_real_query_parameter_still_separates_pages():
    """反向锁：修跟踪参数不许把 `?page=2` 与 `?page=3` 也合了。"""
    a = ms._normalize_url("https://e.test/list?page=2&utm_id=0")
    b = ms._normalize_url("https://e.test/list?page=2")
    c = ms._normalize_url("https://e.test/list?page=3")
    assert a == b
    assert a != c


def test_an_unknown_non_tracking_parameter_is_still_a_different_url():
    assert ms._normalize_url("https://e.test/a?x=1") != ms._normalize_url("https://e.test/a?x=2")


def test_two_engines_naming_one_page_with_different_tags_merge_into_one_row(monkeypatch):
    """合并层用的就是归一化键：两个后端各带一个跟踪变体，读者只该看到一行。"""
    def _stub(name: str, url: str):
        class _Engine:
            disabled = False
            priority = 1.0

            def __init__(self, **kw):
                pass

            def search(self, query, region, safesearch, timelimit, page):
                # 引擎交回的是 ddgs 的 href 形状（合并层读 .href），不是 RawResult
                return [SimpleNamespace(title="同一篇文章", href=url, body="x")]

        _Engine.name = name
        return _Engine

    monkeypatch.setitem(ms._TEXT_ENGINES, "baidu", _stub("baidu", PAGE + "?utm_id=0"))
    monkeypatch.setitem(ms._TEXT_ENGINES, "bing", _stub("bing", PAGE))
    monkeypatch.setattr(ms, "_is_circuit_open", lambda name: False)
    monkeypatch.setattr(ms, "_record_success", lambda name: None)

    rows, status = asyncio.run(ms.metasearch(
        "同一篇文章", max_results=5, engines=["baidu", "bing"]))

    assert len(rows) == 1, rows
    assert rows[0]["backends"] == ["baidu", "bing"], rows[0]
    assert status["baidu"] == status["bing"] == "ok", status
