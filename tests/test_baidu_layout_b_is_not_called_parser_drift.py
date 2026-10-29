"""baidu B 版面交回的 6 行，不许被报成 `parser_drift`。

现场（2026-10-29 · 本机真实 `web_search`）：

    engine_detail: {"name":"baidu","status":"ok","verdict":"parser_drift",
                    "why":"12 result containers on its page, 0 usable rows parsed"}

而同一轮 `engines_used` 里有 baidu，结果行的 `source` 也写着 baidu —— 它明明交回了行。
冻结夹具上复现同一个假判决：`baidu_variant_b.html` 的 6 个容器**全部**交回真链
（`https://tokio.rs/` 等），`last_extract` 却是 `(6, 0)`。

成因：B 版面的真链藏在 `data-log` 的 JSON 里，`href` 在原始解析时是一段以 `{` 开头的
字符串；`_is_usable` 要求 href 以 http(s) 开头，于是原始计数是 0。把 JSON 解成真链的
是 `Baidu.post_extract_results` —— 它跑在**计数之后**。计数因此量的不是"这一家交出了
多少行"，而是"中间形状里有多少行已经是 URL"，后者对 B 版面恒为 0。

契约：
  * `extract_kept`（两家 search 的唯一出口）在引擎自滤之后重记可用数；
  * 两套版面各自的冻结夹具都报 `(容器数, 实交行数)`，且实交行 href 全是 http(s)；
  * 原始解析的计数仍由 `extract_results` 保留给直接读它的调用方，语义不变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dhole_mcp import search_metasearch as ms

FIXTURES = Path(__file__).parent / "engine_fixtures"


def _engine():
    return ms._TEXT_ENGINES["baidu"](proxy=None, timeout=5, verify=True)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("variant, containers", [("a", 5), ("b", 6)])
def test_the_usable_count_is_what_the_engine_actually_delivers(variant, containers):
    eng = _engine()
    kept = eng.extract_kept(_fixture(f"baidu_variant_{variant}.html"))

    assert len(kept) == containers, (variant, len(kept))
    assert eng.last_extract == (containers, containers), (
        f"版 {variant} 交回 {len(kept)} 行，计数却是 {eng.last_extract} —— "
        "post_extract_results 解出的真链没有被算进可用行")
    assert all(r.href.lower().startswith(("http://", "https://")) for r in kept), (
        [(r.title[:20], r.href[:50]) for r in kept])


def test_layout_b_keeps_the_link_it_resolved_from_data_log():
    """B 版面的证据：真链来自 data-log.mu，且它就是交出去的那一条。"""
    eng = _engine()
    kept = eng.extract_kept(_fixture("baidu_variant_b.html"))
    assert kept[0].href == "https://tokio.rs/", kept[0].href


def test_the_raw_parse_count_is_still_available_before_the_engine_filter():
    """原始计数不被抹掉：直接读 `extract_results` 的人仍看到中间形状的真实数字。"""
    eng = _engine()
    rows = eng.extract_results(_fixture("baidu_variant_b.html"))
    assert len(rows) == 6
    assert eng.last_extract == (6, 0), (
        "extract_results 的语义是原始解析，不该被 extract_kept 的修正改写")
