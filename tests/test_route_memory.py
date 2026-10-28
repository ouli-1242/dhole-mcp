"""按域抓取档位记忆（P2）：进程内、advisory、只记成功。

钉住七件事：记录与读取、`www.` 归一、子域隔离、TTL 过期、LRU 上限、
「记住浏览器档 → 下一发直接走浏览器档」、以及「提示不成立就回退完整阶梯并重新学」。
这一层不改任何参数与响应字段，所以集成断言只看**哪条车道被走了**，不看新增字段。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import route_memory
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

import dhole_mcp.server as server_mod

pytestmark = pytest.mark.smoke


# ─── 1 · the memory itself ────────────────────────────────────────────────

class TestTheMemory:

    def test_a_success_is_read_back_for_the_same_host(self):
        route_memory.remember("https://example.test/a", "stealthy")
        assert route_memory.hint("https://example.test/b") == "stealthy"

    def test_www_is_the_same_host_and_subdomains_are_not(self):
        route_memory.remember("https://www.example.test/a", "http")
        assert route_memory.hint("https://example.test/b") == "http"
        assert route_memory.hint("https://docs.example.test/b") == ""
        route_memory.remember("https://docs.example.test/a", "stealthy")
        assert route_memory.hint("https://example.test/b") == "http"

    def test_an_expired_entry_is_not_a_hint(self, monkeypatch):
        route_memory.remember("https://example.test/a", "stealthy")
        monkeypatch.setattr(route_memory, "TTL_S", 0.0)
        assert route_memory.hint("https://example.test/b") == ""
        assert route_memory.snapshot() == {}

    def test_the_lru_cap_drops_the_oldest_host(self):
        for i in range(route_memory.MAX_ENTRIES + 5):
            route_memory.remember(f"https://h{i}.test/", "http")
        assert len(route_memory.snapshot()) == route_memory.MAX_ENTRIES
        assert route_memory.hint("https://h0.test/") == ""
        assert route_memory.hint(f"https://h{route_memory.MAX_ENTRIES + 4}.test/") == "http"

    def test_reading_a_hint_refreshes_its_lru_position(self):
        route_memory.remember("https://keep.test/", "http")
        for i in range(route_memory.MAX_ENTRIES - 1):
            route_memory.remember(f"https://h{i}.test/", "http")
        assert route_memory.hint("https://keep.test/") == "http"
        route_memory.remember("https://overflow.test/", "http")
        assert route_memory.hint("https://keep.test/") == "http", "刚读过的最该留下"

    def test_forget_removes_one_host_only(self):
        route_memory.remember("https://a.test/", "stealthy")
        route_memory.remember("https://b.test/", "stealthy")
        route_memory.forget("https://a.test/")
        assert route_memory.hint("https://a.test/") == ""
        assert route_memory.hint("https://b.test/") == "stealthy"

    def test_only_the_two_modes_are_recorded(self):
        route_memory.remember("https://example.test/", "dynamic")
        assert route_memory.hint("https://example.test/") == ""


# ─── 2 · the lanes through web_read ───────────────────────────────────────

def _ok(url: str, *, stealthy: bool, status: int = 200,
        error: str = "") -> ResponseModel:
    return ResponseModel(
        url=url, status=status, content=["a real page body with words in it " * 40],
        fetcher_used="stealthy" if stealthy else "http",
        content_ok=not error, error=error)


def _lanes(monkeypatch, *, forced=None, auto=None):
    calls = {"force": [], "auto": []}

    async def fake_force(self, url, tier, **kwargs):
        calls["force"].append((url, tier))
        if callable(forced):
            return forced(url, tier)
        return forced or _ok(url, stealthy=True)

    async def fake_auto(self, url, extraction_type, **kwargs):
        calls["auto"].append(url)
        if callable(auto):
            return auto(url)
        return auto or _ok(url, stealthy=False)

    monkeypatch.setattr(MasterFetchServer, "_force_fetch", fake_force)
    monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake_auto)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    return calls


def _read(url: str) -> ResponseModel:
    return asyncio.run(MasterFetchServer().web_read(url=url, cache_ttl=0))


class TestTheLanes:

    def test_a_remembered_stealthy_host_skips_the_http_attempt(self, monkeypatch):
        calls = _lanes(monkeypatch)
        route_memory.remember("https://wall.test/article", "stealthy")

        out = _read("https://wall.test/next-page")

        assert calls["force"] == [("https://wall.test/next-page", "stealthy")]
        assert calls["auto"] == []
        assert out.fetcher_used == "stealthy"
        assert out.escalation_path == "stealthy(remembered)"
        assert route_memory.hint("https://wall.test/next-page") == "stealthy"

    def test_no_hint_runs_the_normal_ladder_and_records_the_mode(self, monkeypatch):
        calls = _lanes(monkeypatch)
        out = _read("https://plain.test/page")
        assert calls["auto"] == ["https://plain.test/page"]
        assert calls["force"] == []
        assert out.escalation_path != "stealthy(remembered)"
        assert route_memory.hint("https://plain.test/page") == "http"

    def test_a_hint_that_does_not_hold_falls_back_and_relearns(self, monkeypatch):
        calls = _lanes(
            monkeypatch,
            forced=lambda url, tier: _ok(url, stealthy=True, status=403,
                                         error="bot_wall_detected"),
            auto=lambda url: _ok(url, stealthy=False))
        route_memory.remember("https://changed.test/page", "stealthy")

        out = _read("https://changed.test/page")

        assert calls["force"] == [("https://changed.test/page", "stealthy")]
        assert calls["auto"] == ["https://changed.test/page"], "提示失败必须回退完整阶梯"
        assert out.fetcher_used == "http"
        assert route_memory.hint("https://changed.test/page") == "http"

    def test_a_failure_records_nothing(self, monkeypatch):
        _lanes(monkeypatch, auto=lambda url: _ok(url, stealthy=False, status=503,
                                                  error="all_tiers_failed"))
        _read("https://broken.test/page")
        assert route_memory.hint("https://broken.test/page") == ""

    def test_a_subdomain_does_not_inherit_the_parent_hint(self, monkeypatch):
        calls = _lanes(monkeypatch)
        route_memory.remember("https://example.test/a", "stealthy")
        _read("https://docs.example.test/b")
        assert calls["force"] == []
        assert calls["auto"] == ["https://docs.example.test/b"]

    def test_a_document_keeps_the_http_answer_shape(self, monkeypatch):
        """PDF 的答案是字节，浏览器档给不了 —— 提示再热也不许抢这条车道。"""
        calls = _lanes(monkeypatch)
        route_memory.remember("https://files.test/doc.pdf", "stealthy")
        _read("https://files.test/doc.pdf")
        assert calls["force"] == []
        assert calls["auto"] == ["https://files.test/doc.pdf"]

    def test_a_forced_tier_still_wins_over_the_memory(self, monkeypatch):
        calls = _lanes(monkeypatch)
        route_memory.remember("https://wall.test/a", "stealthy")
        asyncio.run(MasterFetchServer().web_read(
            url="https://wall.test/a", force_fetcher="http", cache_ttl=0))
        assert calls["force"] == [("https://wall.test/a", "http")], (
            "调用方点了名，记忆不许替他改")
        assert calls["auto"] == []
