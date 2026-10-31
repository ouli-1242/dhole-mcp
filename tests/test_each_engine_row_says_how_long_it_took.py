"""每一家引擎这一轮花了多久，要跟着它的那一行一起交回。

`engine_detail` 原先只说 status / verdict / why，说不出"这家慢"：一家 3 秒才答的
引擎与一家 200 毫秒才答的引擎，在回执上是同一行。耗时是分开"这个索引该换掉"与
"我们的期限太短"的唯一一个数。硬截止时还在飞的那几家没有答案 —— 它们的行不该
补一个 0 或整轮时长来冒充观测值。
"""

import time
from types import SimpleNamespace

import pytest

from dhole_mcp import search_engines as se
from dhole_mcp import search_metasearch as ms
from dhole_mcp.search import _engine_detail_rows
from dhole_mcp.search_engines import EngineReport

pytestmark = pytest.mark.smoke


def test_an_engine_row_carries_its_measured_wall_clock():
    rows = _engine_detail_rows([
        EngineReport(name="bing", ok=True, status="ok", duration_ms=812.4),
    ])
    assert rows[0]["duration_ms"] == 812


def test_an_unobserved_engine_row_omits_the_number_instead_of_reporting_zero():
    rows = _engine_detail_rows([
        EngineReport(name="yandex", status="timeout", duration_ms=0.0),
    ])
    assert "duration_ms" not in rows[0]


@pytest.mark.asyncio
async def test_metasearch_times_each_engine_at_the_moment_it_answers(monkeypatch):
    def _stub(name: str, delay: float):
        class _Engine:
            disabled = False
            priority = 1.0

            def __init__(self, **kw):
                pass

            def search(self, query, region, safesearch, timelimit, page):
                time.sleep(delay)
                return [SimpleNamespace(title=name,
                                        href=f"https://{name}.test/1", body="b")]

        _Engine.name = name
        return _Engine

    monkeypatch.setitem(ms._TEXT_ENGINES, "bing", _stub("bing", 0.0))
    monkeypatch.setitem(ms._TEXT_ENGINES, "yandex", _stub("yandex", 0.15))
    monkeypatch.setattr(ms, "_is_circuit_open", lambda name: False)
    monkeypatch.setattr(ms, "_record_success", lambda name: None)
    monkeypatch.setattr(ms, "_get_search_proxy", lambda: None)

    stats: dict = {}
    _rows, status = await ms.metasearch(
        "q", 5, engines=["bing", "yandex"], round_stats=stats)

    assert status == {"bing": "ok", "yandex": "ok"}, status
    engine_ms = stats["engine_ms"]
    assert engine_ms["bing"] < engine_ms["yandex"], engine_ms
    assert engine_ms["yandex"] >= 100, engine_ms


@pytest.mark.asyncio
async def test_multi_search_carries_the_timing_into_the_reports(monkeypatch):
    async def fake_metasearch(q, n, **kw):
        kw["round_stats"]["engine_ms"] = {"brave": 120.5, "google": 4000.0}
        return [], {"brave": "ok", "google": "blocked"}

    monkeypatch.setattr(se, "_metasearch", fake_metasearch)
    monkeypatch.setattr(se, "_engine_yield", lambda: {})
    monkeypatch.setattr(se, "_cooldowns", lambda: {})

    _, reports = await se.multi_search("test", 6, round_stats={})
    by_name = {r.name: r for r in reports}
    assert by_name["brave"].duration_ms == pytest.approx(120.5)
    assert by_name["google"].duration_ms == pytest.approx(4000.0)
