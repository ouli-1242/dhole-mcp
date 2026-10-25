"""R02-007：recall 同 URL 只出一行——保留最新，variants=N；stale 标注不许打架。

两轮同形（R01 P04 · R02 P10）：web_recall 里同一个 URL 出两行，一行 is_stale
true 一行 false——「到底信哪行」。根因：缓存键按抓取变体存（同一 URL 的
markdown/html 两行、不同时刻两行），结果组装按键逐行出门，没有按 URL 合并。

契约（本文件钉住）：
  * 同 URL 多键 → 一行：fetched_at 最新的那个变体胜出，is_stale 按它算；
  * 合并发生在 relevance 池截断之后、出榜之前——被合并的变体不占 limit 名额；
  * 行带 variants=N（N>1 时），N=1 不付这颗字段。
"""

from __future__ import annotations

from dhole_mcp.recall import _merge_url_variants


def _row(url, key, fetched_at, relevance=0.5):
    return {"url": url, "key": key, "fetched_at": fetched_at, "relevance": relevance}


def test_two_variants_of_one_url_collapse_to_the_freshest():
    rows = [_row("http://x.test/a", "k1", 1000, 0.9),
            _row("http://x.test/a", "k2", 2000, 0.5)]
    merged = _merge_url_variants(rows)
    assert len(merged) == 1
    assert merged[0]["key"] == "k2" and merged[0]["fetched_at"] == 2000
    assert merged[0]["variants"] == 2


def test_a_single_row_pays_no_variants_field():
    merged = _merge_url_variants([_row("http://x.test/a", "k1", 1000)])
    assert len(merged) == 1 and "variants" not in merged[0]


def test_distinct_urls_stay_distinct():
    rows = [_row("http://x.test/a", "k1", 1000),
            _row("http://x.test/b", "k2", 1000)]
    merged = _merge_url_variants(rows)
    assert len(merged) == 2 and all("variants" not in r for r in merged)
