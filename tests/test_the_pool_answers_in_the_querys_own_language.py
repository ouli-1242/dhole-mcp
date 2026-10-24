"""搜索池用查询自己的语言回答（提升计划 2.4 · 语言车道）。

现场（2026-10，真实搜索实测）：英文查询 `transformer attention mechanism survey`
回 8 条**全部**中文站点（知乎、51CTO、CSDN、B 站），3.0s 完成——默认池里境内三家 +
cn.bing 在本网络快、先答、先填满配额，境外索引还在飞就被「够数早退」砍掉。语言不是
`region` 参数的事（它说的是"结果给哪个市场"），是引擎池的事。

修法是**车道**：按查询的 CJK 字符占比选 preferred 侧（>30% 中文侧、<5% 英文侧、
中间地带不选边），然后只做两件事——早退不砍 preferred 侧一条都没答话的那侧（等到
第一条落地即可），合并序 preferred 侧在前（稳定分区，一条不丢）。引擎数一个不加。

本文件钉住：阈值三档；英文查询不被快引擎的中文答案霸占、慢的境外引擎不被砍；中文
查询反向同理；混合查询逐字不变；分区不丢行。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import dhole_mcp.search_metasearch as ms
from dhole_mcp.search_engines import _CN_LANE, _EN_LANE, language_lane
from dhole_mcp.search_metasearch import _prefer_lane, metasearch

EN_QUERY = "transformer attention mechanism survey"
CN_QUERY = "神经网络 注意力机制 综述"
MIXED_QUERY = "PCIe 7.0 规范 发布 128GT/s"


# ─── 1 · 阈值三档 ──────────────────────────────────────────────────────

def test_a_pure_english_query_picks_the_english_lane():
    assert language_lane(EN_QUERY) == _EN_LANE


def test_a_pure_chinese_query_picks_the_chinese_lane():
    assert language_lane(CN_QUERY) == _CN_LANE


def test_a_mixed_query_picks_no_side():
    """中文意图 + 英文术语的查询不选边——行为与从前逐字相同。"""
    assert language_lane(MIXED_QUERY) == frozenset()
    assert language_lane("") == frozenset()


# ─── 2 · 分区：一条不丢、车道内保持先后 ───────────────────────────────

def _row(href: str, *backends: str) -> dict:
    return {"href": href, "backends": sorted(backends)}


def test_the_partition_is_stable_and_loses_nothing():
    order = [_row("a", "baidu"), _row("b", "brave"), _row("c", "sogou"),
             _row("d", "yandex"), _row("e", "baidu", "bing_global")]
    out = _prefer_lane(order, _EN_LANE)
    assert [r["href"] for r in out] == ["b", "d", "e", "a", "c"]
    # 双侧共识的行（e：baidu + bing_global）属于两侧，任一侧 preferred 都收它
    assert _prefer_lane(order, _CN_LANE)[0]["href"] == "a"


def test_the_partition_is_a_noop_without_a_lane_or_without_two_sides():
    order = [_row("a", "baidu"), _row("b", "sogou")]
    assert _prefer_lane(order, frozenset()) is order
    assert _prefer_lane(order, _CN_LANE) is order      # 只有 preferred 一侧


# ─── 3 · 整轮 metasearch：等待与分区都真的发生 ─────────────────────────

class _FakeEngine:
    """按配置延迟回答的假引擎；rows 是 (href, title) 列表。"""
    disabled = False
    delay = 0.0
    rows: list[tuple[str, str]] = []

    def __init__(self, proxy=None, timeout=None, *, verify=True):
        pass

    def search(self, *args, **kw):
        time.sleep(self.delay)
        return [SimpleNamespace(href=h, title=t, body="snippet")
                for h, t in self.rows]


def _wire_pool(monkeypatch, **engines: tuple[float, int]):
    """engines: 名字 -> (延迟秒, 行数)。往 _TEXT_ENGINES 里塞假类。"""
    fake = {}
    for name, (delay, n) in engines.items():
        cls = type(f"_Fake_{name}", (_FakeEngine,), {
            "delay": delay,
            "rows": [(f"https://{name}.test/{i}", f"{name} result {i}")
                     for i in range(n)],
        })
        fake[name] = cls
    monkeypatch.setattr(ms, "_TEXT_ENGINES", fake)


def test_an_english_query_waits_for_the_global_side_and_leads_with_it(monkeypatch):
    """英文查询：境内快引擎先填满配额，境外那侧慢——不许在它一条都没答话时砍它。"""
    # 快慢差距 0.02 → 0.7：8 进程 xdist 满载时 time.sleep 会整体漂移，
    # 差距太小会把「等 preferred 侧」的窗口挤没（全量下偶发红的根源）。
    _wire_pool(monkeypatch, baidu=(0.02, 6), bing=(0.02, 6), sogou=(0.02, 6),
               bing_global=(0.7, 6), brave=(0.7, 6))
    results, status = asyncio.run(metasearch(
        EN_QUERY, 8, engines=["baidu", "bing", "sogou", "bing_global", "brave"]))
    # 等的是 preferred 侧的**第一条**，不是完整性：第一条落地后，其余照旧按
    # 「够数早退」处置（preempted 不是失败）。两家都在 preferred 里，至少一家 ok。
    answered = [b for b in ("bing_global", "brave") if status.get(b) == "ok"]
    assert answered, status
    assert all(status[b] in ("ok", "preempted") for b in ("bing_global", "brave")), status
    head = {r["backend"] for r in results[:6]}
    assert head <= {"bing_global", "brave"}, (
        f"英文查询的前排仍是 {head} —— 快引擎的中文侧答案霸占了它")
    tail = {r["backend"] for r in results}
    assert {"baidu", "bing", "sogou"} <= tail, "另一侧的行被丢了"
    assert len(results) >= 24, len(results)


def test_a_chinese_query_leads_with_the_cn_side(monkeypatch):
    _wire_pool(monkeypatch, baidu=(0.7, 6), bing=(0.7, 6), sogou=(0.7, 6),
               bing_global=(0.02, 6), brave=(0.02, 6))
    results, status = asyncio.run(metasearch(
        CN_QUERY, 8, engines=["baidu", "bing", "sogou", "bing_global", "brave"]))
    assert status.get("baidu") == "ok", status
    head = [r["backend"] for r in results[:6]]
    assert set(head) <= {"baidu", "bing", "sogou"}, head


def test_a_mixed_query_keeps_the_arrival_order(monkeypatch):
    """中间地带不选边：快的先答就在前，等待与分区都不生效。"""
    _wire_pool(monkeypatch, baidu=(0.05, 4), brave=(0.4, 4))
    results, status = asyncio.run(metasearch(
        MIXED_QUERY, 8, engines=["baidu", "brave"]))
    assert [r["backend"] for r in results[:4]] == ["baidu"] * 4
