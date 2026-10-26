"""R13-002：一次缓存回放不许把「selector 没生效」这句话弄丢。

现场（round-13 p22 #02/#03）：同一 URL + 同一 `css_selector="#no-such"` 读两遍。
第一遍 live，summary 带 R06-001 的诚实句「matched 0 elements … the whole document
was returned」；第二遍命中缓存（TTL 内），**这句话消失**，整页照回。p20 S1 / p21 A1
在旧缓存行上同样复现。

根因：注记由 `trafilatura_extractor._note_selector_matched_nothing` 写进
`_ARG_NOTES`，只在抽取发生时生成；缓存 envelope 存了质量判决与元数据，却没存参数
注记，回放侧无从重说。修法与 R01-021（headers_off_by）同形：写入侧把与缓存键同源的
selector 注记存进信封，回放侧原样带出。focus / max_content_chars 不在缓存键里，
它们的注记不许跟着旧行回到新参数上。
"""
from __future__ import annotations

import asyncio
import uuid

import pytest

from dhole_mcp import identity
from dhole_mcp.context import _ARG_NOTES, _note_text
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

LOOPBACK = "http://127.0.0.1:8901/replay-selector-"
SELECTOR = "#no-such"
NOTE = (f"css_selector '{SELECTOR}' was NOT applied: it matched 0 elements on this "
        "document - the whole document was returned (narrowing never empties the answer)")


@pytest.fixture(autouse=True)
def private_door_open():
    before = identity.get("allow_private")
    identity.set_value("allow_private", True)
    yield
    identity.set_value("allow_private", bool(before))


def _stub_http(monkeypatch) -> list[str]:
    """live 那一发：模拟抽取器在 selector 0 匹配时写下那句话。"""
    seen: list[str] = []

    async def fake(self, url, **kwargs):
        seen.append(url)
        _note_text(NOTE)
        return ResponseModel(url=url, status=200, content=["body text " * 30],
                             fetcher_used="http", content_ok=True,
                             content_type="text/html",
                             total_extracted_chars=300, visible_text_chars=300)

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    return seen


def _read(url, **extra):
    args = {"url": url, "force_fetcher": "http", **extra}
    out = asyncio.run(MasterFetchServer()._dispatch("web_read", args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


def test_live_and_replay_both_say_the_selector_matched_nothing(monkeypatch):
    seen = _stub_http(monkeypatch)
    url = LOOPBACK + uuid.uuid4().hex
    first = _read(url, css_selector=SELECTOR)
    second = _read(url, css_selector=SELECTOR, cache_ttl=3600)
    assert len(seen) == 1, f"第二遍不该再发一次包：{seen}"
    assert second.get("fetcher_used") == "cache", second.get("summary")
    assert "matched 0 elements" in (first.get("summary") or ""), first.get("summary")
    assert "matched 0 elements" in (second.get("summary") or ""), second.get("summary")


def test_only_notes_that_share_the_cache_key_travel_with_the_envelope():
    """focus / max_content_chars 不在缓存键里：旧行的注记不许贴到新参数上。"""
    from dhole_mcp.server import _selector_notes_for_cache
    token = _ARG_NOTES.set([
        NOTE,
        "focus was NOT applied: the selector matched nothing",
        "max_content_chars clamped 100->500 (500-200000)",
    ])
    try:
        assert _selector_notes_for_cache() == [NOTE]
    finally:
        _ARG_NOTES.reset(token)
