"""每一份引擎夹具：`last_extract` 必须等于**真正交出去的行**，不是中间形状。

2026-10-29 的 baidu B 版面事故就是这条不变量破了：6 个容器、6 行交付，`last_extract`
却报 `(6, 0)`，于是引擎被判成 `parser_drift`。当时唯一的 baidu 回归只钉了它自己，
其余 12 份夹具没有一个人问过同一个问题 —— 下一次某家引擎在 `post_extract_results`
里改 href/解包装，同一个假 verdict 还会从另一家冒出来。

这份守卫逐份夹具独立复算两个数：
  * 容器数：按 `items_xpath` 在原始树上再数一遍；
  * 可用行数：`_usable_count(kept)`（交出去的那批里 href 与 title 都成立的行）。
两者都必须与 `extract_kept` 落下的 `last_extract` 相等。它同时钉住"交出去的每一行
都是可用的"—— 不可用的行应该由引擎自己的后处理丢掉，而不是留给下游去发现。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dhole_mcp import search_metasearch as ms

FIXTURES = Path(__file__).parent / "engine_fixtures"
METAS = sorted(FIXTURES.glob("*.meta.json"))


def _load(meta_path: Path) -> tuple[str, str]:
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    engine = meta.get("engine", "")
    html = meta_path.with_suffix("").with_suffix(".html")
    return engine, html.read_text(encoding="utf-8")


@pytest.mark.parametrize("meta_path", METAS, ids=lambda p: p.name)
def test_the_count_is_the_rows_the_engine_actually_delivers(meta_path):
    engine, html = _load(meta_path)
    cls = ms._TEXT_ENGINES.get(engine)
    if cls is None:
        pytest.skip(f"{engine} 不是 HTML 文本引擎，没有容器计数")

    eng = cls(proxy=None, timeout=5, verify=True)
    kept = eng.extract_kept(html)

    tree = eng.extract_tree(eng.pre_process_html(html))
    containers = len(tree.xpath(eng.items_xpath))
    usable = ms._usable_count(kept)

    assert eng.last_extract == (containers, usable), (
        f"{meta_path.name}: last_extract={eng.last_extract}，"
        f"独立复算=({containers}, {usable})，kept={len(kept)}")
    assert len(kept) == usable, (
        f"{meta_path.name}: 交回了 {len(kept) - usable} 行不可用的结果 —— "
        "后处理应当把它们丢掉，而不是让下游发现")
    assert kept, f"{meta_path.name}: 这份夹具至少该交回一行"


def test_the_cross_fixture_guard_would_have_caught_the_baidu_b_bug():
    """机制自检：把 B 版面的后处理换成"原样返回"，计数必须回到 (6, 0)。

    这不是测 baidu，而是测这份守卫的判别力 —— 一份永远为真的不变量守不住任何东西。
    """
    meta = FIXTURES / "baidu_variant_b.meta.json"
    engine, html = _load(meta)
    eng = ms._TEXT_ENGINES[engine](proxy=None, timeout=5, verify=True)
    eng.post_extract_results = lambda rows: rows  # 不解 data-log 的旧形状
    kept = eng.extract_kept(html)
    assert len(kept) == 6
    assert eng.last_extract == (6, 0), (
        "旧形状应当在计数里留下 (6, 0) —— 守卫正是靠这个差分辨真假")
