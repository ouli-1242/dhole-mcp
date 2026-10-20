# -*- coding: utf-8 -*-
"""R01-021（P03 安全用户 · CONFIRM B P2）：一次缓存回放不许把「这一份是怎么发出去的」弄丢。

实测（P3 的受控对照：同一 URL、同一身份，S5b 走缓存 vs S7 带 `?cb=` 走实时）：
实时那一发的 `summary` 里有这句 ——

  the target is loopback/private, so dhole dropped its own synthesized header
  template - no fabricated google referer, no generated UA.

第二次读同一颗 URL 命中缓存，**这句话没了**，而 `identity_applied` 还在。缺的不是一个
装饰：那位用户拿这句去写"这次请求里没有伪造来路"的证据，回放看不到它就会写成一句
指控不存在的指控 —— 而同一颗 URL 的两个答案里，被引用的那份正好是没说话的那份。

根因（读码定案，不再是猜测）：`_HEADERS_OFF_BY` 由**发包那一层**在自己摘掉头时写下
（`server.py` 的 `stealthy_headers and _target_is_private(url)` 那一支），这句话也只由
那一层说一次（`_note_text(_headers_off_note())` 在两处 fetch 落点）。而
`_cache_hit_response` 在任何 fetch 之前返回：它不带这枚读数，ContextVar 又是每发清空的
（`_reset_context`），于是回放**结构上不可能**知道写缓存那一发有没有摘。
`content_ok` / `substance` / `cache_validators` 这一族"过去那一刻量到的数"早就随信封进
了缓存，唯独这一颗没进 —— 同一族的漏项，不是新的设计。

修法与那一族同形：写入侧把 `_HEADERS_OFF_BY.get()` 存进信封（`headers_off_by`），回放侧
读出来并由**同一个作者**（`_headers_off_note`）说一句过去时的话。回放没发过包，所以
不许再用那句现在时的 "What went out is what you named…"；旧缓存行里没有这一颗，就
什么都不说（缺席不是"没摘"，也不许被补成一句凭缺席编造的断言 —— 与 `substance` 那三颗
同一口径）。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

import pytest

from dhole_mcp import identity
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

SERVER = (Path(__file__).resolve().parents[1] / "src" / "dhole_mcp" / "server.py"
          ).read_text(encoding="utf-8")

LOOPBACK = "http://127.0.0.1:8901/replay-"
PUBLIC = "https://site.example.test/article-"

STORED = "the fetch that stored this answer"
SENTENCE = "no fabricated google referer"
NO_PACKET = "replay sent no request"
PRESENT_TENSE = "What went out is what you named"


@pytest.fixture(autouse=True)
def private_door_open():
    """这一族测的是"到了那台机器门口还发什么头 / 说了什么"，不是"能不能进门"。"""
    before = identity.get("allow_private")
    identity.set_value("allow_private", True)
    yield
    identity.set_value("allow_private", bool(before))


def _stub_http(monkeypatch) -> list[str]:
    """记下真正交给 HTTP 层的 URL；回放那一发必须一条都不多。"""
    seen: list[str] = []

    async def fake(self, url, **kwargs):
        seen.append(url)
        return ResponseModel(url=url, status=200, content=["body text " * 20],
                             fetcher_used="http", content_ok=True,
                             content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    return seen


def _read(url, **extra):
    args = {"url": url, "force_fetcher": "http", **extra}
    out = asyncio.run(MasterFetchServer()._dispatch("web_read", args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


def _replayed(url, monkeypatch, **extra):
    """同一颗 URL 读两遍：第一遍发包并写缓存，第二遍只许从缓存出。"""
    seen = _stub_http(monkeypatch)
    first = _read(url, **extra)
    second = _read(url, cache_ttl=3600, **extra)
    assert len(seen) == 1, f"第二遍不该再发一次包：{seen}"
    assert second.get("fetcher_used") == "cache", second.get("summary")
    return first, second


# ─── 1 · 实时那一发本来就会说（回归锚，不许被这一刀弄坏） ───────────

def test_the_live_loopback_read_still_says_it_dropped_the_template(monkeypatch):
    first, _second = _replayed(LOOPBACK + uuid.uuid4().hex, monkeypatch)
    assert "loopback/private" in (first.get("summary") or ""), first.get("summary")
    assert SENTENCE in (first.get("summary") or ""), first.get("summary")


# ─── 2 · 回放这一格：话必须跟着内容回来，且要说成过去 ───────────────

def test_the_replay_still_says_the_stored_fetch_dropped_the_template(monkeypatch):
    _first, second = _replayed(LOOPBACK + uuid.uuid4().hex, monkeypatch)
    summary = second.get("summary") or ""
    assert SENTENCE in summary, summary
    assert STORED in summary, summary
    assert "loopback/private" in summary, summary


def test_the_replay_says_it_sent_no_packet_itself(monkeypatch):
    """回放没有包可发。让它复述一句现在时的"发出去的就是你点名的那些"，
    就是一句关于没发生过的事的断言 —— 与 `duration_ms` 报 0 是同一族的错。"""
    _first, second = _replayed(LOOPBACK + uuid.uuid4().hex, monkeypatch)
    summary = second.get("summary") or ""
    assert NO_PACKET in summary, summary
    assert PRESENT_TENSE not in summary, summary


def test_a_replay_of_a_page_that_kept_its_template_claims_nothing(monkeypatch):
    """反面：公网、档位照常的那一发，回放不许凭空多出一句"没发合成头"。"""
    first, second = _replayed(PUBLIC + uuid.uuid4().hex, monkeypatch)
    for label, out in (("live", first), ("replay", second)):
        summary = out.get("summary") or ""
        assert SENTENCE not in summary, (label, summary)
        assert STORED not in summary, (label, summary)


def test_the_off_rung_replays_as_the_rung_not_the_geography(monkeypatch):
    """两个扳法两句话：`anti_detect='off'` 是调用方选的档位，回环是地理。
    回放必须把当初那一个扳法带回来，不许把它们混成一句。"""
    _first, second = _replayed(PUBLIC + uuid.uuid4().hex, monkeypatch,
                               anti_detect="off")
    summary = second.get("summary") or ""
    assert STORED in summary and "anti_detect='off'" in summary, summary
    assert "loopback/private" not in summary, summary


def test_no_reason_means_no_sentence(monkeypatch):
    """缺席不许被补成断言：不知道当初是哪只手摘的头，回放就一个字都不说。

    旧缓存行（写入时还没存这一颗键）走的是同一格：`env.get(…, "")` 读到空 —— 那是
    "没被告知"，不是"没摘过头"，与 `substance` 那三颗同一口径。
    """
    from dhole_mcp.server import _headers_off_note
    assert _headers_off_note("", replay=True) == "", _headers_off_note("", replay=True)
    assert _headers_off_note("", replay=False) == ""
    said = _headers_off_note("private", replay=True)
    assert STORED in said and SENTENCE in said and PRESENT_TENSE not in said, said


# ─── 3 · 两处必须共认同一颗键（这一族的失效方式就是"少搬一颗"） ──────

def _block(start_marker: str, needle: str) -> str:
    i = SERVER.index(start_marker)
    j = SERVER.index(needle, i) + len(needle) - 1
    opening = SERVER[j]
    closing = {"{": "}", "(": ")"}[opening]
    depth, k = 0, j
    while k < len(SERVER):
        ch = SERVER[k]
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return SERVER[j:k + 1]
        k += 1
    raise AssertionError(f"没找到 {start_marker} 之后的括号体")


def test_the_writer_stores_which_hand_lowered_the_headers():
    writer = _block("async def _finalize_result", "envelope={")
    assert re.search(r'"headers_off_by"\s*:', writer), writer[:600]


def test_the_replay_reads_it_and_hands_it_to_the_one_author():
    start = SERVER.index("async def _cache_hit_response")
    body = SERVER[start:SERVER.index("async def _cache_hit_under_resolver_guard", start)]
    assert "headers_off_by" in body, body[:400]
    assert "_headers_off_note(" in body, body[:400]
