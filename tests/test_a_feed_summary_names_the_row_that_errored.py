"""R01-001：feed 批量里出错的那一行，顶层 summary 要点名它的 URL。

现场（2026-10-25 · P01 陈曦 · S2 靶站 feed 增量 · review/round-01）：
三发里一发 410 → 顶层 summary「1 rows errored (each row names its own error
and diagnosis)」。哪个 URL 错了要展开 rows 才知道——批量行通用形状是对的，
但顶层的这一句是唯一被读到的入口，它该把名字带上。

契约（本文件钉住）：
  * 有行出错时，summary 的 errored 句带上出错行的 source_url（至多点名 2 条，
    多了由「each row names its own error」兜底）；
  * 干净批次照旧不付这句话。
"""

from __future__ import annotations

from dhole_mcp.server import _feed_batch_envelope


def _row(url, error="", items=1):
    return {"source_url": url, "error": error,
            "items": [{"title": f"i{n}"} for n in range(items)] if not error else []}


def test_the_errored_clause_names_the_row_that_errored():
    env = _feed_batch_envelope(2, [_row("https://a.test/feed"),
                                   _row("https://b.test/feed", error="http_error_410: gone")])
    summary = env.get("summary", "")
    assert "1 rows errored" in summary, summary
    assert "https://b.test/feed" in summary, f"出错的 URL 要在顶层点名：{summary}"


def test_two_errored_rows_get_named_and_the_rest_stay_pointed_at_the_rows():
    env = _feed_batch_envelope(4, [
        _row("https://a.test/feed"),
        _row("https://b.test/feed", error="http_error_410: gone"),
        _row("https://c.test/feed", error="network_error: timeout - slow"),
        _row("https://d.test/feed", error="http_error_404: missing")])
    summary = env.get("summary", "")
    assert "3 rows errored" in summary, summary
    assert "https://b.test/feed" in summary, summary
    assert "https://c.test/feed" in summary, summary
    # 3 条以上不全列：那句"每行自己说"仍要留在句尾兜底
    assert "each row names its own error" in summary, summary


def test_a_clean_batch_still_skips_the_errored_clause():
    env = _feed_batch_envelope(1, [_row("https://a.test/feed")])
    summary = env.get("summary", "")
    assert "rows errored" not in summary, summary
