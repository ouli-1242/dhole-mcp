# -*- coding: utf-8 -*-
"""R02-057 · 走链接的地图早停时，尾巴要连着已交出的那几行一起存下来。

题面（真机一发 + 离线同形）：`web_discover(url=..., max_pages=3)` 打在一张 42 行的地图上，
回执 `pages_discovered=42`、`next_resume_token=null`，而它自己那句 next_action 写着
"39 URL(s) were discovered but never sent" —— 少了多少报得出，怎么续回一条都没有。
照它教的那一句 `resume_token='<plan_id>:0'` 再问一次，读回的还是**已经交出的那 3 行**，
下一句是 "Map exhausted: all 3 row(s) have shipped"：地图有 42 行、计划里只有 3 行，
这句 "exhausted" 说的是计划而不是地图，调用方按它读会得出"这个站就 3 页"。

作者分工（读码定位，两处各自都对、合起来才是洞）：
* 走链接那一停在把早停队列交给计划**之前**先把它清空（`crawl.py` 里
  `stop_queue = [] if discover_only else ...`），尾巴因此从没进过存储；
* `server.py` 的 `if not result.plan_id:` 随后只存**已交出的那几行**。它注释里写明了理由：
  "被体积闸砍过一刀的地图已经在 crawl 里整份存下了（连未交出的那些行）" —— 而那一句只对
  sitemap 那一路成立（`crawl._hold_map_tail`）。走链接的地图没有这位作者，于是同一颗
  `plan_id` 在两种地图上说的是两种话。

判据（口径：计划的大小就是地图的大小）：尾巴连着前半存，游标发在已交出的行数上，
`<id>:0` 与 `<id>:<N>` 各读出什么都要在 next_action 里点得到，而且教的那一句要照抄就能跑
（`url=` 在内，因为这颗门非要有 url）。正文爬取那一支的口径**不动**：那里的计划仍只装
一次都没发过的 URL —— 那一支的游标每前进一步都要付一次请求，把已交出的行存进去等于
教调用方重付。
"""

from __future__ import annotations

import asyncio
import re

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import plan_load, plan_parse
from dhole_mcp.server import MasterFetchServer, ResponseModel

BASE = "https://tail.test"
SEED = BASE + "/"
N_LINKS = 6
# 帽=3 ⇒ 交出 seed、p0、p1，队列里剩 p2..p5（一次都没发过）。
TAIL = [f"{SEED}p{i}" for i in range(2, N_LINKS)]


def _links(names):
    return ("<html><body>" + "".join(f'<a href="{h}">l</a>' for h in names)
            + "</body></html>")


class _Counter:
    """数得清"这一发付了几次请求"的替身站点：续页那几发必须是 0。"""

    def __init__(self, pages):
        self.fetched = []
        self.pages = pages

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        return ResponseModel(url=url, status=200,
                             content=[self.pages.get(url, "<p>plain body.</p>")],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _rows(r):
    return [p.url for p in r.pages]


def _plan(r):
    return list(plan_load(r.plan_id) or []) if r.plan_id else []


def _door():
    srv = MasterFetchServer()
    counter = _Counter({SEED: _links([f"/p{i}" for i in range(N_LINKS)])})
    srv.web_read = counter.web_read
    return srv, counter


@pytest.fixture
def map_stop():
    async def _go():
        srv, counter = _door()
        r = await srv.web_discover(SEED, max_pages=3)
        return srv, counter, r
    return asyncio.run(_go())


def _taught_tokens(text: str) -> list[str]:
    return re.findall(r"resume_token='([^']*)'", text)


class TestThePlanHoldsTheWholeMap:
    def test_the_tail_rows_are_held_after_the_rows_that_shipped(self, map_stop):
        _srv, _counter, r = map_stop
        plan = _plan(r)
        assert plan[:len(r.pages)] == _rows(r), "计划的前半就是这一发交出的那几行"
        assert plan[len(r.pages):] == TAIL, (
            f"尾巴必须是这一发本会接着发出的那几行，按同一顺序：{plan}")

    def test_the_plan_size_is_the_map_size(self, map_stop):
        _srv, _counter, r = map_stop
        assert len(_plan(r)) == r.pages_discovered, (
            f"地图有 {r.pages_discovered} 行，计划里只有 {len(_plan(r))} 行")

    def test_the_held_tail_is_exactly_what_the_reply_called_never_sent(self, map_stop):
        _srv, _counter, r = map_stop
        quoted = int(re.search(r"([\d,]+) URL\(s\) were discovered but never sent",
                               r.next_action).group(1).replace(",", ""))
        assert len(_plan(r)) - len(r.pages) == quoted, r.next_action


class TestTheCursorLandsOnTheFirstHeldRow:
    def test_the_cursor_counts_the_rows_that_shipped(self, map_stop):
        _srv, _counter, r = map_stop
        assert r.next_resume_token == f"{r.plan_id}:{len(r.pages)}", r.next_resume_token
        assert plan_parse(r.next_resume_token) == (r.plan_id, len(r.pages))

    def test_the_way_out_teaches_both_cursors_by_name(self, map_stop):
        _srv, _counter, r = map_stop
        na = r.next_action
        tokens = _taught_tokens(na)
        assert f"{r.plan_id}:{len(r.pages)}" in tokens, na
        assert f"{r.plan_id}:0" in tokens, na
        assert f"url='{SEED}'" in na, f"教的这一句少了 url= 就照抄不跑：{na}"


class TestTheTaughtCursorsDoWhatTheySay:
    @pytest.mark.asyncio
    async def test_the_tail_cursor_lists_the_held_rows_for_free(self):
        srv, counter = _door()
        r = await srv.web_discover(SEED, max_pages=3)
        paid = len(counter.fetched)
        page = await srv.web_discover(SEED, resume_token=r.next_resume_token)
        assert _rows(page) == TAIL, _rows(page)
        assert len(counter.fetched) == paid, "读尾巴那一发一个请求都不该付"
        assert page.pages_crawled == 0
        assert page.next_resume_token == "", page.summary
        assert "Map exhausted" in page.next_action, page.next_action

    @pytest.mark.asyncio
    async def test_the_zero_cursor_lists_the_whole_map_from_row_one(self):
        srv, counter = _door()
        r = await srv.web_discover(SEED, max_pages=3)
        paid = len(counter.fetched)
        page = await srv.web_discover(SEED, resume_token=f"{r.plan_id}:0")
        assert _rows(page) == _plan(r), "游标 0 读回的是整张地图，不是只读尾巴"
        assert len(counter.fetched) == paid, "重读地图也不该付请求"
        assert page.pages_discovered == r.pages_discovered

    @pytest.mark.asyncio
    async def test_every_token_the_reply_teaches_runs_from_outside_the_process(self):
        """`_dispatch` 才是真门：建议文案里每一个 resume_token 都要能在门外照抄就跑。"""
        import json
        srv, _counter = _door()
        r = await srv._dispatch("web_discover", {"url": SEED, "max_pages": 3})
        payload = json.loads(r[0][0].text)
        assert payload["next_resume_token"]
        for token in _taught_tokens(payload["next_action"]):
            out = await srv._dispatch("web_discover", {"url": SEED,
                                                       "resume_token": token})
            got = json.loads(out[0][0].text)
            assert not got.get("error"), f"{token} 照抄被拒：{got.get('error')}"
            assert got["pages"], f"{token} 教了一条走不通的路"


class TestTheOldShapeIsNotDisturbed:
    @pytest.mark.asyncio
    async def test_a_content_stop_still_holds_only_the_never_sent_urls(self):
        """正文那一支不许把已交出的行存进计划：那里的游标每走一步都付一次请求。"""
        srv, counter = _door()
        r = await srv.web_crawl(SEED, max_pages=3)
        plan = _plan(r)
        assert plan, r.plan_id
        assert not (set(plan) & {p.url for p in r.pages}), plan
        assert r.next_resume_token == f"{r.plan_id}:0"

    def test_a_map_that_shipped_every_row_publishes_no_plan_of_its_own(self):
        async def _go():
            counter = _Counter({SEED: _links([f"/p{i}" for i in range(2)])})
            return await crawl_mod.web_crawl(counter, SEED, max_pages=30,
                                             max_depth=1, discover_only=True,
                                             cache_ttl=0)
        r = asyncio.run(_go())
        assert r.truncated_by_max_pages is not True
        assert not r.plan_id, "队列见底就不该为不存在的东西留一份状态"
        assert not r.next_resume_token


class TestOneAuthorForBothMapKinds:
    def test_the_tail_is_held_by_the_same_helper_a_sitemap_map_uses(self, monkeypatch):
        seen: list[tuple[list, list]] = []
        real = crawl_mod._hold_map_tail

        def spy(pages, held):
            seen.append(([p.url for p in pages], list(held)))
            return real(pages, held)

        monkeypatch.setattr(crawl_mod, "_hold_map_tail", spy)

        async def _go():
            counter = _Counter({SEED: _links([f"/p{i}" for i in range(N_LINKS)])})
            return await crawl_mod.web_crawl(counter, SEED, max_pages=3,
                                             max_depth=1, discover_only=True,
                                             cache_ttl=0)
        r = asyncio.run(_go())
        assert seen == [([p.url for p in r.pages], TAIL)], seen

    def test_the_door_stores_one_plan_for_a_truncated_map(self, monkeypatch):
        """两处各存一份会把游标指向一个没有尾巴的新计划 —— 存储只许发生一次。"""
        calls: list[list[str]] = []
        real = crawl_mod.plan_store

        def spy(urls):
            calls.append(list(urls))
            return real(urls)

        monkeypatch.setattr(crawl_mod, "plan_store", spy)

        async def _go():
            srv, counter = _door()
            return await srv.web_discover(SEED, max_pages=3)
        r = asyncio.run(_go())
        assert len(calls) == 1, f"计划被写了 {len(calls)} 次：{calls}"
        assert calls[0] == _plan(r)


class TestTheManualCarriesTheNewShape:
    def test_the_field_cell_names_both_halves_of_a_map_plan(self):
        from dhole_mcp import guide
        text = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
        line = next(r for r in text.splitlines() if r.startswith("| `plan_id`"))
        assert "never sent" in line, line
        assert "map ROWS it shipped" not in line, (
            "这一格还在说 discover 只存已交出的行 —— 地图的尾巴今天也在计划里")
