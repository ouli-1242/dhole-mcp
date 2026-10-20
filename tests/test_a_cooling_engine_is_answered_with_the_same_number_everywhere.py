"""R01-016（并入 R01-032）· "还要几秒" 是量得到的数，就不许有三个读法各说一套。

现场（画像 P03 郑雨桐 S4，两发间隔 6m18s）：`engines=["brave"]` 而 brave 正在熔断，
回执给的是

```
engine_detail[0].why  "blocked/captcha (circuit opened)"          ← 无数
next_action           "No results (… rate-limited/timed out). Retry in a moment, or …"
error                 "… retry in a moment, rephrase, or set DHOLE_SEARCH_PROXY …"
```

而同一时刻默认池那一发的 `engine_detail` 里 brave 写的是
"circuit open (recently blocked; skipped; retried in 46s)"。同一个数（`cooldowns()` 算的
绝对解禁时刻）在三个读法里只上线了一个。

在进程内量成可重复的（`review/.runtime/p4/probe_r01_016.py` → `r016-measured.json`，
熔断表钉成 brave 41s / so360 61s / yandex 611s）：

```
engine_detail  brave/so360(status=blocked) → "blocked/captcha (circuit opened)"   无秒
               yandex(status=circuit_open) → "… retried in 611s)"                  有秒
web_search 零结果  next_action / error      → "Retry in a moment"                   无秒
sys_state(show=["engine_state"]) 有冷却时   → cooldown_seconds_left 60.2 / 610.2   有秒
sys_state 冷却已清（P07 撞的就是这一格）    → 这颗字段整个缺席                       歧义
```

所以 R01-032 那句"14 行里没有一处给出还剩几秒"要改账：**数在的时候一直上线**，缺的是
数不在的时候那一格说什么 —— `status: circuit_open` 而无该字段，读起来既像"在冷却但没说
多久"又像"没在冷却"，两种意思挤一格。这一刀把它说成 0（本进程没有冷却记录）。

F11（`test_a_cooling_engine_says_the_seconds_it_has_left.py`）立的规矩照用：
量不到就不写秒，一句关于没量过的事的秒数比不报更坏。本刀不搬那个门槛，只把已经量到的数
送到它该去的另外三个读法。
"""

from __future__ import annotations

from time import time

import pytest

import dhole_mcp.search as S
import dhole_mcp.search_engines as SE
import dhole_mcp.search_metasearch as M
from dhole_mcp.search_engines import EngineReport

TABLE = {"brave": 41.0, "so360": 61.0, "yandex": 611.0}


@pytest.fixture(autouse=True)
def restore_breaker_tables(monkeypatch):
    """本文件写的是进程内那两张表（熔断 + 产出统计）—— 走人时原样还回去。

    `_ENGINE_YIELD` / `_BACKEND_HEALTH` 是模块级字典，直接赋值会跨用例、甚至跨文件活下来
    （本轮已经吃过一次 skip 数在不同跑之间自己飘的账，见 backlog B-11）。这里用
    `monkeypatch.setattr` 换整张表，teardown 由 pytest 负责。
    """
    monkeypatch.setattr(M, "_BACKEND_HEALTH", {}, raising=False)
    monkeypatch.setattr(M, "_ENGINE_YIELD", {}, raising=False)
    yield


@pytest.fixture
def cooling(monkeypatch):
    """熔断表钉成 brave 41s / so360 61s / yandex 611s（绝对解禁时刻，与真代码同形状）。"""
    monkeypatch.setattr(M, "_BACKEND_HEALTH",
                        {k: time() + v for k, v in TABLE.items()}, raising=False)
    return dict(TABLE)


async def _reports(monkeypatch, status: dict):
    """真的 `multi_search` 适配层 + 只在最外的 metasearch 打桩。

    桩必须打在 metasearch 那一层：本文件量的是 `multi_search` 里那张 `for name, st in
    status.items()` 的分支表，替身替到 multi_search 就等于只测了自己的编排。
    """

    async def fake_meta(query, max_results, **kwargs):
        return [], dict(status)

    monkeypatch.setattr(SE, "_metasearch", fake_meta)
    _, reports = await SE.multi_search("竞品博客 更新", 3, engines=sorted(status))
    return {r.name: r for r in reports}


# ─── 1 · engine_detail：拒绝型那一格与"跳过"那一格必须是同一个数 ──────

@pytest.mark.asyncio
async def test_a_row_that_was_asked_and_refused_names_the_seconds(cooling, monkeypatch):
    by = await _reports(monkeypatch, {"brave": "blocked"})
    assert "retried in 41s" in by["brave"].error, by["brave"].error
    # 原话不删：这一轮真的被拒了这件事，与还要等多久，是两件事
    assert "blocked/captcha (circuit opened)" in by["brave"].error, by["brave"].error


@pytest.mark.asyncio
async def test_a_row_that_timed_out_names_the_seconds_too(cooling, monkeypatch):
    by = await _reports(monkeypatch, {"so360": "timeout"})
    assert "61s" in by["so360"].error, by["so360"].error


@pytest.mark.asyncio
async def test_the_number_is_written_once_even_where_it_already_lived(cooling, monkeypatch):
    """`circuit_open` 那一格从前自己就写秒 —— 共用一个作者之后不许变成两处。"""
    by = await _reports(monkeypatch, {"yandex": "circuit_open"})
    err = by["yandex"].error
    assert err.count("611s") == 1, err
    assert err.count("retried in") == 1, err


@pytest.mark.asyncio
async def test_a_refusal_with_no_cooldown_record_names_no_seconds(monkeypatch):
    """反向守卫（F11 的门槛）：表里没有这一家，就一个字都不许多说。"""
    by = await _reports(monkeypatch, {"brave": "blocked", "bing": "timeout"})
    assert "retried in" not in by["brave"].error, by["brave"].error
    assert "retried in" not in by["bing"].error, by["bing"].error


@pytest.mark.asyncio
async def test_an_engine_that_was_never_asked_is_not_given_a_wait(cooling, monkeypatch):
    """preempted 不是"被拒后请等待"：配额一到就取消，重发必然撞同一个停止点。"""
    by = await _reports(monkeypatch, {"duckduckgo": "preempted"})
    assert "not a block" in by["duckduckgo"].error, by["duckduckgo"].error
    assert "retried in" not in by["duckduckgo"].error, by["duckduckgo"].error


# ─── 2 · 零结果那一发：error 与 next_action 都不许把量到的数说成"a moment" ──

def test_the_wait_phrase_carries_the_number_and_who(cooling):
    got = S._cooling_wait(["brave", "so360"])
    assert got.startswith("in 61s"), got          # 等多久 = 剩得最久的那一家
    assert "brave 41s" in got and "so360 61s" in got, got


def test_the_wait_phrase_is_empty_when_nothing_was_measured(monkeypatch):
    """反向守卫：表里没有这一家就交白串 —— 不许编一个兑现不了的数。

    （第一版这条挂着 `cooling` 替身去断言"没量到"，自相矛盾：替身刚把三家钉进了表里。
    空表要自己钉出来，不能在有人刚填过表的夹具里断言缺席。）
    """
    monkeypatch.setattr(M, "_BACKEND_HEALTH", {}, raising=False)
    assert S._cooling_wait(["brave"]) == ""
    assert S._cooling_wait([]) == ""


def test_the_zero_result_reply_stops_saying_a_moment_when_it_knows(cooling):
    got = S._search_next_action([], ["brave", "so360"],
                                "No results from any engine. Engines were rate-limited"
                                "/CAPTCHA'd; retry in a moment.", [])
    assert "in 61s" in got, got
    assert "a moment" not in got, got


@pytest.mark.asyncio
async def test_both_fields_of_the_refusal_name_the_same_number(cooling, monkeypatch):
    """端到端：`error` 与 `next_action` 被人当同一句指令读，就不许一个报秒一个说"片刻"。"""
    reports = [EngineReport(name="brave", blocked=True, status="blocked",
                            error="blocked/captcha (circuit opened)")]

    async def fake_multi(query, max_results, **kwargs):
        return [], list(reports)

    async def pass_through(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi)
    monkeypatch.setattr(S, "get_cached", pass_through)
    monkeypatch.setattr(S, "set_cached", pass_through)
    monkeypatch.setattr(S, "ensure_reranker", pass_through)
    out = await S.web_search(None, "竞品博客 更新", 3, engines=["brave"], cache_ttl=0)

    assert "in 41s" in out.next_action, out.next_action
    assert "in 41s" in out.error, out.error
    assert "a moment" not in out.next_action and "a moment" not in out.error, out.error


@pytest.mark.asyncio
async def test_an_unmeasured_refusal_keeps_the_humble_word(cooling, monkeypatch):
    """反向守卫：熔断表里没有这一家时，"in a moment" 仍是诚实的话 —— 不许造一个数。"""
    monkeypatch.setattr(M, "_BACKEND_HEALTH", {}, raising=False)
    reports = [EngineReport(name="brave", blocked=True, status="blocked",
                            error="blocked/captcha (circuit opened)")]

    async def fake_multi(query, max_results, **kwargs):
        return [], list(reports)

    async def pass_through(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi)
    monkeypatch.setattr(S, "get_cached", pass_through)
    monkeypatch.setattr(S, "set_cached", pass_through)
    monkeypatch.setattr(S, "ensure_reranker", pass_through)
    out = await S.web_search(None, "竞品博客 更新", 3, engines=["brave"], cache_ttl=0)

    assert "a moment" in out.next_action, out.next_action
    assert "41s" not in out.next_action and "41s" not in out.error, out


# ─── 3 · sys_state：数不在的时候那一格说什么 ─────────────────────────

def test_a_row_without_a_cooldown_says_zero_rather_than_silence(monkeypatch):
    """P07 撞的正是这一格：`status: circuit_open` + 没有这颗字段 = 两种意思挤一格。"""
    monkeypatch.setattr(M, "_BACKEND_HEALTH", {}, raising=False)
    M._ENGINE_YIELD["brave"] = {"n": 3, "status": "circuit_open", "last": -1,
                                "last_nodes": -1, "last_kept": -1, "mean": 0,
                                "zero": 0, "drift": 0, "ts": time()}
    snap = M.engine_state_snapshot()
    assert snap["brave"]["cooldown_seconds_left"] == 0, snap["brave"]


def test_a_cooling_row_still_names_the_seconds_it_has(cooling):
    M._ENGINE_YIELD["so360"] = {"n": 3, "status": "blocked", "last": -1,
                                "last_nodes": -1, "last_kept": -1, "mean": 0,
                                "zero": 0, "drift": 0, "ts": time()}
    snap = M.engine_state_snapshot()
    assert 55 < snap["so360"]["cooldown_seconds_left"] <= 61, snap["so360"]


def test_the_field_promises_what_the_code_delivers():
    """字段描述不许比代码多说，也不许少说：这颗现在每一行都在，0 是"这里没有冷却记录"。"""
    from dhole_mcp.models import SysStateModel

    text = SysStateModel.model_fields["engine_health"].description
    assert "cooldown_seconds_left" in text, text
    assert "0" in text, text
