"""test_reranker.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp import reranker as R
from dhole_mcp import search as S
from dhole_mcp.reranker import rerank
from dhole_mcp.search import EngineReport, RawResult
import inspect
import pathlib
import re
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, _TOP_LEVEL_ARGS
from dhole_mcp import reranker as reranker_mod


# ============================================================================
# 来源：test_raw_relevance_floor_b2.py
# ============================================================================
# B2 回归：原始分下限（`min_raw_relevance`）——让「这一轮全都不相关」说得出口。

class _FakeReranker:
    """A cross-encoder stand-in with a scripted score per document text."""

    def __init__(self, table):
        self.table = table
        self.calls = 0

    def score(self, query, docs):
        self.calls += 1
        # rerank() hands the model "title snippet"; every fixture title here is a
        # single token, so that is what the table is keyed by.
        return [self.table.get(d.split(" ", 1)[0], 0.0) for d in docs]


def _results(texts):
    return [RawResult(title=t, url=f"https://s{i}.test/", snippet="", source="bing",
                      position=i + 1) for i, t in enumerate(texts)]


# 一组「全垃圾」：引擎确实返回了 3 条，但原始分全在离题带里。
JUNK = {"a": 0.0002, "b": 0.0001, "c": 0.0173}
MIXED = {"good": 0.9811, "marginal": 0.2043, "junk": 0.0004}


@pytest.fixture
def wired(monkeypatch):
    """Install the fake reranker behind rerank()'s peek-only getter."""
    def _install(table):
        fake = _FakeReranker(table)
        monkeypatch.setattr(R, "_reranker", fake)
        monkeypatch.setattr(R, "get_reranker", lambda: fake)

        async def _no_real_load(*a, **k):
            return fake

        # search.web_search awaits ensure_reranker() before ranking; without this
        # it would load the real 288 MB model to answer a unit test.
        monkeypatch.setattr(S, "ensure_reranker", _no_real_load)
        return fake
    return _install


class TestTheNormalizedFloorCannotDoThis:

    def test_a_normalized_floor_always_keeps_the_best_of_a_bad_set(self, wired):
        wired(JUNK)
        results = _results(list(JUNK))

        kept = rerank("q", results, min_relevance=0.99)

        assert kept, "top 恒为 1.0，所以它永远活下来"
        assert len(kept) == 1
        assert kept[0][1] == 1.0


class TestTheRawFloorCanSayNothingIsRelevant:

    def test_an_all_junk_round_comes_back_empty_not_reassuring(self, wired):
        wired(JUNK)
        results = _results(list(JUNK))

        kept = rerank("q", results, min_raw_relevance=0.1)

        # [] = "模型跑了，并且把每一条都判为不相关"；None = "没有重排器"。
        # 把这两件事混成一个，调用方就分不清「查无结果」和「结果全是垃圾」。
        assert kept == []

    def test_the_measured_operating_point_keeps_every_real_hit(self, wired):
        """0.1 是从实测空档里取的：它必须放行勉强相关的（0.2043），也放行真命中的
        （≥0.93），只砍掉 1e-4 那一团。"""
        wired(MIXED)
        results = _results(list(MIXED))

        kept = rerank("q", results, min_raw_relevance=0.1)

        assert [r.title for r, _ in kept] == ["good", "marginal"]

    def test_a_junk_only_query_drops_at_the_floor_it_asked_for(self, wired):
        wired(JUNK)
        results = _results(list(JUNK))

        no_floor = rerank("q", results)
        floored = rerank("q", results, min_raw_relevance=0.1)

        assert len(no_floor) == 3 and floored == []

    def test_the_span_it_saw_comes_back_with_the_verdict(self, wired):
        """过滤掉东西却不报原始分范围，调用方下一步只能瞎猜；报了这个数，0.1→0.05
        这种决定才有依据。"""
        wired(MIXED)
        stats: dict = {}

        kept = rerank("q", _results(list(MIXED)), min_raw_relevance=0.1, stats=stats)

        assert len(kept) == 2
        assert stats["raw_min"] == pytest.approx(0.0004)
        assert stats["raw_max"] == pytest.approx(0.9811)
        assert stats["dropped_raw"] == 1

    def test_no_floor_means_no_change_for_anyone(self, wired):
        wired(MIXED)
        before = rerank("q", _results(list(MIXED)))
        after = rerank("q", _results(list(MIXED)), min_raw_relevance=0.0, stats={})

        assert [r.title for r, _ in before] == [r.title for r, _ in after]


@pytest.mark.asyncio
class TestTheSearchLayerReportsIt:

    async def test_an_emptied_round_says_which_floor_emptied_it(self, wired, monkeypatch):
        wired(JUNK)
        raw = _results(list(JUNK))

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "multi_search", fake_multi)
        out = await S.web_search(None, "任意查询", max_results=5, cache_ttl=0,
                                   engines=["bing"], min_raw_relevance=0.1)

        assert out.results == []
        assert "min_raw_relevance=0.1" in out.error, out.error
        # The span is in the same sentence so the next number is not a guess.
        assert "spanned" in out.error, out.error

    async def test_a_partial_drop_is_counted_in_the_hint(self, wired, monkeypatch):
        wired(MIXED)
        raw = _results(list(MIXED))

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "multi_search", fake_multi)
        out = await S.web_search(None, "任意查询", max_results=5, cache_ttl=0,
                                   engines=["bing"], min_raw_relevance=0.1)

        titles = [r.title for r in out.results]
        assert "junk" not in titles and "good" in titles
        assert "min_raw_relevance=0.1" in out.fetch_hint, out.fetch_hint
        assert "spanned" in out.fetch_hint, out.fetch_hint

    async def test_the_floor_is_part_of_the_cache_identity(self, wired, monkeypatch):
        """同一个查询带下限与不帯下限必须是两条缓存，否则 0.1 的那次调用会拿到一份
        没过滤的历史结果，看起来像「下限没生效」。"""
        wired(MIXED)
        raw = _results(list(MIXED))
        cache: dict = {}

        async def fake_get_cached(query, cache_type, css_selector, **kwargs):
            return cache.get((query, cache_type))

        async def fake_set_cached(query, cache_type, content, status, css_selector, ttl, **kw):
            cache[(query, cache_type)] = {"content": content}

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "get_cached", fake_get_cached)
        monkeypatch.setattr(S, "set_cached", fake_set_cached)
        monkeypatch.setattr(S, "multi_search", fake_multi)

        loose = await S.web_search(None, "同一查询", max_results=5, engines=["bing"])
        strict = await S.web_search(None, "同一查询", max_results=5, engines=["bing"],
                                       min_raw_relevance=0.1)

        assert len(strict.results) < len(loose.results)
        assert any("minraw=0.1" in k[1] for k in cache), list(cache)

    async def test_an_out_of_range_floor_is_clamped_and_said(self, wired, monkeypatch):
        wired(MIXED)
        raw = _results(list(MIXED))

        async def fake_multi(query, max_results, **kwargs):
            return list(raw), [EngineReport(name="bing", ok=True)]

        monkeypatch.setattr(S, "multi_search", fake_multi)
        out = await S.web_search(None, "q", max_results=5, cache_ttl=0,
                                   engines=["bing"], min_raw_relevance=5)

        assert "outside 0-1" in out.summary, out.summary
        assert "1.0" in out.summary, out.summary

@pytest.mark.live
@pytest.mark.asyncio
async def test_a_full_floor_empties_a_real_round_instead_of_passing_it_off():
    """真模型、真搜索：下限设成原始分几乎不可能到的 1.0，正确的答复是「这轮没有一条
    够相关」，而不是把最高分那条当结果端出去。"""
    from dhole_mcp.server import MasterFetchServer

    srv = MasterFetchServer()
    loose = await S.web_search(srv, "kubernetes liveness probe 配置", max_results=5,
                                 cache_ttl=0)
    if not loose.results:
        pytest.skip(f"no live engine answer: {loose.error[:80]}")

    strict = await S.web_search(srv, "kubernetes liveness probe 配置", max_results=5,
                                  cache_ttl=0, min_raw_relevance=1.0)

    assert strict.rerank_mode == "neural", strict.fetch_hint
    assert strict.results == [], [r.url for r in strict.results]
    assert "min_raw_relevance=1.0" in strict.error, strict.error
    assert "spanned" in strict.error, strict.error


# ============================================================================
# 来源：test_a_recovery_hint_must_name_a_call_the_tool_accepts.py
# ============================================================================
# `neural_focus_model_missing` 的出路必须指向一发**这本工具真的会接受**的调用。

SENTENCE_RE = re.compile(r"`?([a-z_]+)`?=")
# 单词边界是必要的：旧话里 "The reranker **model** is not downloaded" 数得出 "mode"
# 这四个字母，却一个参数都没点。`mode` 只有在不成词时才算被点名。
MODE_RE = re.compile(r"\bmode\b")


def _tool_accepts(tool: str, arg: str) -> bool:
    return arg in _TOP_LEVEL_ARGS[tool]


def _names_mode(text: str) -> bool:
    return MODE_RE.search(text) is not None


# ─── 1 · 出路点名的每一颗参数都必须真的存在 ───


class TestTheWayOutNamesReachableDoors:

    def test_the_remedy_exists_and_has_one_author(self):
        remedy = server_mod._neural_focus_remedy("call")
        assert "neural" not in remedy.lower() or "web_search has no" in remedy, remedy
        # 两处抄本并一位：主语随工具变，句子只有一个作者。
        assert server_mod._neural_focus_remedy("crawl") != remedy
        src = inspect.getsource(server_mod)
        assert src.count("The reranker model is not downloaded yet") == 1, (
            "出路的那句话在 server.py 里只能有一位作者（从前是两处抄本）")

    def test_every_argument_named_in_the_remedy_is_reachable(self):
        """一般守卫：`xxx=` 写进建议里，就必须是那颗工具收得到的名字。

        归属规则与句子同源：提到 web_search 的那半句里的名字归 web_search，其余归本行工具
        （web_read / web_crawl）。这里用整句扫，凡是扫到的名字必须在**某一侧**成立，且
        `mode` 必须在 web_search 侧、`focus_engine` 必须在本行工具侧。
        """
        for subject, own in (("call", "web_read"), ("crawl", "web_crawl")):
            remedy = server_mod._neural_focus_remedy(subject)
            names = set(SENTENCE_RE.findall(remedy))
            assert names, f"句子里一个参数名都没有：{remedy}"
            for name in names:
                assert _tool_accepts("web_search", name) or _tool_accepts(own, name), (
                    f"{name}= 在 {own} 与 web_search 两侧都不存在：{remedy}")
            assert _tool_accepts("web_search", "mode"), "mode 必须是 web_search 收的那颗"

    def test_the_remedy_says_web_search_has_no_focus_engine(self):
        remedy = server_mod._neural_focus_remedy("call")
        assert "focus_engine" in remedy and "has no" in remedy, remedy
        assert not _tool_accepts("web_search", "focus_engine"), (
            "前提：web_search 确实不收这颗 —— 旧那句话正是把它当成收的")

    def test_the_remedy_still_offers_the_no_model_way_out(self):
        remedy = server_mod._neural_focus_remedy("call")
        assert "bm25" in remedy and _tool_accepts("web_read", "focus_engine"), remedy
        assert "no request was made" in remedy.lower(), remedy


# ─── 2 · 判决位一位不动（本刀只修出路） ───


@pytest.mark.asyncio
async def test_the_refusal_still_refuses_without_fetching(monkeypatch):
    import dhole_mcp.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    # R02-F16 之后这道门按成因分两支，本文件钉的是「盘上没有模型」那一支：
    # 那一支的出路才是一颗普通 web_search（F11 的话）。
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    monkeypatch.setattr(reranker_mod, "ensure_reranker",
                        _async_none)
    r = await MasterFetchServer().web_read(
        url="https://example.com/", focus="reliability", focus_engine="neural")
    assert r.status == 0 and r.content_ok is not True
    assert (r.error or "").startswith("invalid_request: neural_focus_model_missing"), r.error
    assert _names_mode(r.next_action or ""), (
        f"出路仍然没点名那颗真的存在的参数：{r.next_action!r}")
    assert "no request was made" in (r.next_action or "").lower(), r.next_action


def test_the_shape_the_old_hint_pointed_at_is_refused():
    """旧那句话暗示的调用形状，当场证明它不可达（这就是 P1 的全部证据）。

    走的是 CLI 那一发实际撞上的闸门（`_dispatch` 第一件事就是 `_reject_unknown_args`），
    不是 Python 方法签名 —— 签名会在参数到达闸门之前就 TypeError，量到的不是产品的话。
    """
    from dhole_mcp.params import ArgumentRefusal

    with pytest.raises(ArgumentRefusal) as exc:
        server_mod._reject_unknown_args("web_search",
                                        {"query": "跨境仓配 合规",
                                         "focus_engine": "neural"})
    text = f"{exc.value} {getattr(exc.value, 'next_action', '') or ''}"
    assert "focus_engine" in text and "pported" in text, (
        f"这一发竟然被接受了？：{text[:200]}")


# ─── 3 · web_crawl 那一侧同一位作者 ───


@pytest.mark.asyncio
async def test_the_crawl_gate_shares_the_author(monkeypatch):
    import dhole_mcp.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    # R02-F16 之后这道门按成因分两支，本文件钉的是「盘上没有模型」那一支：
    # 那一支的出路才是一颗普通 web_search（F11 的话）。
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    monkeypatch.setattr(reranker_mod, "ensure_reranker", _async_none)
    r = await MasterFetchServer().web_crawl(
        url="https://example.com/", focus="reliability", focus_engine="neural")
    assert "neural_focus_model_missing" in (r.error or ""), r.error
    assert _names_mode(r.next_action or ""), r.next_action
    assert "crawl" in (r.next_action or ""), (
        f"爬取那一侧拿到的还是'这一发调用'的话：{r.next_action!r}")


# ─── 4 · 手册与实施同一个说法 ───


def test_the_manual_stops_pointing_at_a_door_that_does_not_open():
    """每一处讲这把拒绝的手册行文，都必须与实施同一位作者。

    手册里讲这件事的有五行（SKILL.md / tools.md / examples.md / configuration.md /
    troubleshooting.md），旧版本里五行都把调用方支向「a neural `web_search`」（
    configuration.md 讲模型那一行是第六处）。守卫按行扫**整本手册**，不按文件扫 ——
    一个文件改好了、另一个文件漏着，正是这种改法会留下的形状。
    """
    root = (pathlib.Path(inspect.getsourcefile(server_mod)).resolve().parent
            / "skills" / "dhole-web")
    all_lines = []
    for path in sorted(root.rglob("*.md")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            all_lines.append((f"{path.name}:{no}", line))
    for where, line in all_lines:
        assert "neural web_search" not in line.replace("`", ""), (
            f"{where} 还在把调用方支向一发不存在的形状：{line[:200]}")
    lines = [(w, body) for w, body in all_lines if "neural_focus_model_missing" in body]
    assert lines, "手册里找不到讲这把拒绝的那一行了 —— 守卫的前提先变了"
    for where, line in lines:
        assert "bm25" in line.lower(), f"{where} 把不需要模型的那条出路写丢了：{line[:200]}"
        if "web_search" in line:
            # 提到"让模型回来"那一侧，就必须当场拦住那颗不存在的名字。
            assert "has no `focus_engine`" in line, (
                f"{where} 说了去用 web_search，却没说它不收 `focus_engine`：{line[:260]}")
    # 手册要认得那颗真的参数名。
    joined = "\n".join(line for _, line in lines)
    assert _names_mode(joined), "手册没说清 web_search 是用 mode 排序的那颗工具"


async def _async_none(*a, **k):
    return None


# ============================================================================
# 来源：test_a_model_on_disk_is_not_reported_as_not_downloaded.py
# ============================================================================
# 一个 `None` 不许有三种读法：模型在盘上而加载失败时，不能说「还没下载」。

async def _none(*a, **k):
    return None


@pytest.fixture
def gate_blocked(monkeypatch):
    """把这道门的两种成因都摆出来：模型在不在盘上，是这里唯一变化的那颗。"""
    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    monkeypatch.setattr(reranker_mod, "ensure_reranker", _none)

    async def _read():
        return await MasterFetchServer().web_read(
            url="https://example.com/", focus="reliability", focus_engine="neural")
    return _read


# ─── 1 · 盘上有模型：不许说"还没下载" ───

@pytest.mark.asyncio
async def test_a_model_that_is_on_disk_is_not_reported_as_undownloaded(
        gate_blocked, monkeypatch):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    monkeypatch.setattr(reranker_mod, "unavailable_reason",
                        lambda: "reranker init failed: onnxruntime is not installed")
    r = await gate_blocked()
    text = f"{r.error} {r.next_action}"
    assert "not downloaded" not in text.lower(), text
    assert "onnxruntime" in text, f"过程知道的成因没上回执：{text!r}"
    assert "bring" not in (r.next_action or "").lower() or "already" in (
        r.next_action or "").lower(), r.next_action


@pytest.mark.asyncio
async def test_the_loaded_but_failed_branch_still_offers_the_no_model_way_out(
        gate_blocked, monkeypatch):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    r = await gate_blocked()
    assert "focus_engine='bm25'" in (r.next_action or ""), r.next_action
    assert "no request was made" in (r.error or "").lower(), r.error


# ─── 2 · 盘上没有：F11 的那句话一字不动 ───

@pytest.mark.asyncio
async def test_an_absent_model_still_gets_the_download_sentence(
        gate_blocked, monkeypatch):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    r = await gate_blocked()
    na = r.next_action or ""
    assert "not downloaded yet" in (r.error or "").lower(), r.error
    assert "A plain web_search brings it down" in na, na
    assert "has no `focus_engine`" in na, na


# ─── 3 · 判决位：两支一致，一位不动 ───
@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_the_verdict_is_the_same_door_both_ways(gate_blocked, monkeypatch, present):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: present)
    r = await gate_blocked()
    assert r.status == 0, r.status
    assert r.content_ok is not True, r.content_ok
    assert (r.error or "").startswith("invalid_request: neural_focus_model_missing"), r.error
    assert "No request was made" in (r.error or ""), r.error
    assert r.fetcher_used == "none", r.fetcher_used


# ─── 4 · 反面锁：盘上有模型时不许再指向那颗下载（F11 的口径） ───

@pytest.mark.asyncio
async def test_the_on_disk_branch_does_not_point_at_the_download_door(
        gate_blocked, monkeypatch):
    """`web_search` 那一发**取不回**这一发缺的东西 —— 模型已经在盘上。

    F11 立的是「出路不许点名一门开不了的门」；这一刀的现场正是它的同族：把调用方支去
    重下一份已经在的模型。所以这一支里 `web_search` 一个字都不许出现，而句子要说清
    "没有东西要下载"。
    """
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    r = await gate_blocked()
    na = r.next_action or ""
    assert "web_search" not in na, na
    assert "already on disk" in na, na
    assert "nothing to download" in na, na


@pytest.mark.asyncio
async def test_the_crawl_gate_shares_both_branches(monkeypatch):
    """两位读者读的同一位作者：`web_crawl` 那一侧也按成因分两支。"""
    import dhole_mcp.reranker as reranker_mod
    from dhole_mcp.server import MasterFetchServer

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    monkeypatch.setattr(reranker_mod, "ensure_reranker", _none)

    async def _crawl():
        return await MasterFetchServer().web_crawl(
            url="https://example.com/", focus="reliability",
            focus_engine="neural")

    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    monkeypatch.setattr(reranker_mod, "unavailable_reason",
                        lambda: "reranker init failed: onnxruntime is missing")
    on_disk = await _crawl()
    assert "neural_focus_model_missing" in (on_disk.error or ""), on_disk.error
    assert "not downloaded" not in f"{on_disk.error} {on_disk.next_action}".lower()
    assert "onnxruntime" in f"{on_disk.error} {on_disk.next_action}", on_disk.next_action
    assert "crawl" in (on_disk.next_action or ""), on_disk.next_action

    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    absent = await _crawl()
    assert "not downloaded yet" in (absent.error or "").lower(), absent.error
    assert "A plain web_search brings it down" in (absent.next_action or "")
