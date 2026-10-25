"""R11-001：feed_read 的 max_items 负数要拒绝，不许静默按「全部」处理。

现场（round-11 P10）：max_items=-3 → 静默返回全量 8 条（feed.py 的
`if max_items <= 0: return` 把负数同 0 一起放行）。行为指南在册绝对句
「An out-of-range number is either clamped-and-named, or refused — never
silently rewritten」；工具描述只许「default 20; 0 = all」——负数不在声明域里，
又没有有意义的正域钳制目标，按合同**拒绝**。0 的在册语义（= all）与正数路径
零变化。
"""
from __future__ import annotations

import pytest

from dhole_mcp.feed import FeedItem, FeedResult, _cap_applied


def _result(n):
    return FeedResult(source_url="http://fixture.test/feed.xml",
                      items=[FeedItem(title=f"item {i}") for i in range(n)])


def test_negative_max_items_is_refused():
    with pytest.raises(ValueError, match="max_items"):
        _cap_applied(_result(3), -3)


def test_zero_keeps_documented_all_semantics():
    r = _result(2)
    _cap_applied(r, 0)
    assert len(r.items) == 2  # 0 = all（描述在册）


def test_positive_cap_still_slices_and_notes():
    r = _result(8)
    _cap_applied(r, 3)
    assert len(r.items) == 3
    assert "max_items" in (r.note or "")
