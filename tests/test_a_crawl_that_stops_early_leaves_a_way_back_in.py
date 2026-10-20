# -*- coding: utf-8 -*-
"""R02-009 · 一页一页爬的那一路早停时，也要留下一个按得回去的坐标。

题面（P02-02 邝野·S4 扇出）：`web_crawl` 早停时 `plan_id` 是空的、也没给续跑坐标，
出路只有「把 `max_pages` 调大重跑」；而同一产品里 `web_discover` 已经把地图留在服务器侧
并给 resume 号。台账要求先用一发真调用证明 discover 那一路仍可续（本文件不重复那一发，
量具 `review/.runtime/p4/f41_measure2.py` 与靶站证据
`review/round-02/sessions/P02-R5-F41-probe.jsonl` 记着重跑的代价：同一发重跑，
抓回来的还是**同样那 3 页**）。

读码量到的形状（修法因此不是"再发明一套"）：
* 计划的那套设施全在 —— `crawl.plan_store` / `plan_token` / `plan_parse` / `plan_load`，
  服务器那一侧 `web_crawl(resume_token='<id>:<offset>')` **今天就在读它**
  （`server.py` 里 resume 那一支会 `plan_load` 并把剩下的 URL 逐条抓成正文）。
* 只有 BFS 那一停在**从来不写**它：早停时报的是 `re-run web_crawl with a higher
  max_pages / deadline_ms`，而那条队列（`heap` 里剩的"发现过、一次都没发过"的 URL）
  就在同一个函数里 —— 算得出、报得出（`_shortfall_sentence` 已经报了几条 never sent），
  就是不留下来。
* 字段自己的说法也停在地图那一路：`plan_id` 写的是 "web_crawl sets it when a map had
  rows held back"，`next_resume_token` 写的却是 "Set when a run stopped with URLs left
  (page cap, char budget or deadline)" —— **一格里已经承诺的另一格，正是这一条缺陷的形状**。

判据：BFS 早停只要还有"一次都没发过"的 URL，就把那份队列交给**同一位作者**存起来，
并回发 `<plan_id>:0`；一句都不许多于算术本身。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import plan_load, plan_parse
from dhole_mcp.server import ResponseModel

SEED = "https://resume.example.com/"
ROOT = "resume.example.com"


class _FakeSite:
    """每页秒回，只记录被请求到的 URL —— 续跑的那一发要能数出"没再付的钱"。

    `delay` 让时钟那一闸能被造出来：靶站再快也要有真实的一发"抓到一半墙到了"。
    """

    def __init__(self, pages: dict[str, str] | None = None, delay: float = 0.0):
        self.fetched: list[str] = []
        self.pages = pages or {}
        self.delay = delay

    async def web_read(self, url, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.fetched.append(url)
        body = self.pages.get(url, _PROSE)
        return ResponseModel(url=url, status=200, content=[body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


# 正文要有**分得开的**句子：塌陷/低质那两把尺会把重复句砍空，字符闸就永远走不到。
_PROSE = "<p>" + " ".join(
    f"Row {i} of the plan carries its own wording, so extraction returns it whole."
    for i in range(20)) + "</p>"


def _links(names: list[str]) -> str:
    return ("<html><body>" + "".join(f'<a href="{h}">l</a>' for h in names)
            + "</body></html>")


def _page(names: list[str]) -> str:
    """一页既有很长的正文（喂字符闸）又挂着站内链接（喂队列）。"""
    return ("<html><body>" + _PROSE
            + "".join(f'<a href="{h}">l</a>' for h in names) + "</body></html>")


def _queued_urls(out) -> list[str]:
    return list(plan_load(out.plan_id) or [])


def _quoted_never_sent(na: str) -> int:
    """句子里那个"never sent"的数：两种说法各带一个位置（`_shortfall_sentence` 的两个分支）。

    按位置数词会挑错（"6 URL(s) were discovered but never sent" 的倒数第二词是 but），
    所以锚在短语本身。
    """
    import re
    m = re.search(r"([\d,]+) URL\(s\) were discovered but never sent"
                  r"|;\s*([\d,]+) were never sent", na)
    assert m, na
    return int((m.group(1) or m.group(2)).replace(",", ""))


@pytest.fixture
def a_page_cap_stop():
    """seed 上挂着 6 条站内链接，帽 = 3：跑完 3 页（含 seed）后停，队列里还剩着没发的。"""
    async def _build():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(6)])})
        out = await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                        cache_ttl=0)
        return site, out
    return asyncio.run(_build())


def test_a_page_cap_stop_leaves_a_cursor_back_into_the_queue(a_page_cap_stop):
    _site, out = a_page_cap_stop
    assert out.truncated_by_max_pages is True
    assert out.plan_id, "早停留下了没发的 URL，就必须留下一个 id"
    assert out.next_resume_token == f"{out.plan_id}:0"


def test_the_cursor_is_parsable_by_the_same_vocabulary_the_resume_path_reads(a_page_cap_stop):
    _site, out = a_page_cap_stop
    parsed = plan_parse(out.next_resume_token)
    assert parsed == (out.plan_id, 0), parsed


def test_the_plan_holds_exactly_the_urls_that_were_never_sent(a_page_cap_stop):
    site, out = a_page_cap_stop
    held = _queued_urls(out)
    sent = set(site.fetched)
    assert held, "队列里有货才有这一格"
    assert not (set(held) & sent), f"发过的不许再存一遍：{sorted(set(held) & sent)}"
    # 已经交回的页也不许在计划里（它们是"做完了的那一半"）
    returned = {p.url for p in out.pages}
    assert not (set(held) & returned)


def test_the_plan_size_is_the_number_the_reply_already_quoted(a_page_cap_stop):
    """算术锁：句子里报的 never-sent 条数 == 计划里的行数。两个数各说各话就是谎。"""
    _site, out = a_page_cap_stop
    assert len(_queued_urls(out)) == _quoted_never_sent(out.next_action), (
        out.next_action, _queued_urls(out))


def test_the_way_out_names_the_cursor_and_keeps_the_old_lever(a_page_cap_stop):
    _site, out = a_page_cap_stop
    na = out.next_action
    assert f"resume_token='{out.next_resume_token}'" in na, na
    assert "max_pages" in na, " raising 帽那条出路没被换掉，只是不再是唯一一条"
    assert "without re-fetching" in na or "without re-crawling" in na, na


def test_a_run_that_finished_its_queue_publishes_no_plan():
    """队列见底就不是早停：不许为不存在的东西留一份状态。"""
    async def _go():
        site = _FakeSite({SEED: _links(["/a"])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                         cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_max_pages is not True
    assert not out.plan_id, out.plan_id
    assert "resume_token" not in out.next_action


def test_the_char_budget_stop_publishes_the_same_kind_of_cursor():
    async def _go():
        site = _FakeSite({SEED: _page([f"/p{i}" for i in range(8)])})
        # 帽的下限是 1,000 字符（`MIN_TOTAL_CHARS`），一页正文 ~1.7k ⇒ 交回 seed 那一页
        # 之后 while 顶上的字符闸就到点：这才是"预算 spent、队列还在"的形状。
        return await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                         max_total_chars=1_000, cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_total_chars is True, (out.summary, out.next_action)
    assert out.plan_id and out.next_resume_token == f"{out.plan_id}:0"
    assert len(_queued_urls(out)) == _quoted_never_sent(out.next_action), out.next_action


def test_the_deadline_stop_publishes_the_same_kind_of_cursor():
    async def _go():
        site = _FakeSite({SEED: _page([f"/p{i}" for i in range(8)])}, delay=0.05)
        return await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                         deadline_ms=120, cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_time is True, (out.summary, out.next_action)
    if _queued_urls(out):
        assert out.next_resume_token == f"{out.plan_id}:0"
        assert "without re-fetching" in out.next_action, out.next_action


def test_the_plan_is_written_by_the_one_author_the_resume_path_reads(monkeypatch):
    """不许有第二个存储：BFS 这一支也走 `crawl.plan_store`。"""
    seen: list[list[str]] = []
    real = crawl_mod.plan_store

    def spy(urls):
        seen.append(list(urls))
        return real(urls)

    monkeypatch.setattr(crawl_mod, "plan_store", spy)

    async def _go():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(5)])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                         cache_ttl=0)
    out = asyncio.run(_go())
    assert seen, "计划必须经唯一的存储写出去"
    assert out.plan_id and _queued_urls(out) == seen[-1]


def test_urls_filtered_out_before_queueing_never_reach_the_plan():
    """子树过滤在入队之前，所以计划里不许混进被剪掉的东西 —— 续跑不能变成走私。"""
    async def _go():
        site = _FakeSite({SEED: _links(["/work/1", "/work/2", "/other/3", "/other/4"])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                         path_include=["/work"], cache_ttl=0)
    out = asyncio.run(_go())
    held = _queued_urls(out)
    assert all("/other/" not in u for u in held), held


def test_a_map_stop_hands_its_tail_to_the_same_plan_author():
    """discover_only 那一停**现在**把尾巴交出去：存的是整张地图（R02-057 的口径）。

    这一格原本钉的是相反的话（"这一支不交队列，那颗 `plan_id` 另有作者、另有含义"），
    它钉住的正是当时实测到的那条洞：`web_discover(max_pages=3)` 说 `mapped 42 URL(s)`，
    计划里只有已交出的 3 行，`next_resume_token` 空，照建议 `<id>:0` 再问一次读回的还是
    那 3 行，下一句 "Map exhausted: all 3 row(s) have shipped" —— 地图 42 行，"exhausted"
    说的是计划。口径定了（尾巴连着前半存、游标发在已交出的行数上）之后，旧的那句就成了
    反判据，所以**改写**这一格而不是删掉：形状仍由同一发问出，判据换成"两式都读得东西"。
    正文那一支的口径没动，仍只装一次都没发过的 URL（上面那几格守着）。
    """
    async def _go():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(6)])})
        return await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                         discover_only=True, cache_ttl=0)
    out = asyncio.run(_go())
    assert out.truncated_by_max_pages is True, out.summary
    held = _queued_urls(out)
    assert held[:len(out.pages)] == [p.url for p in out.pages]
    assert len(held) == out.pages_discovered, (
        f"地图 {out.pages_discovered} 行，计划 {len(held)} 行：{held}")
    assert out.next_resume_token == f"{out.plan_id}:{len(out.pages)}"
    assert f"resume_token='{out.next_resume_token}'" in out.next_action, out.next_action


def test_the_two_field_names_now_cover_the_page_by_page_run_too():
    """字段自己的说法以前只讲地图（`plan_id` 那句），而游标那颗早就承诺了"帽/预算/时钟"。"""
    fields = crawl_mod.CrawlResponseModel.model_fields
    assert "max_pages" in fields["plan_id"].description, fields["plan_id"].description[:300]
    assert "without re-fetching" in fields["next_resume_token"].description or \
        "continue" in fields["next_resume_token"].description


def test_the_manual_says_a_stopped_crawl_can_be_picked_up():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "resume_token" in corpus, "手册里没这把游标"
    assert "never sent" in corpus or "were never sent" in corpus, (
        "手册没写这条计划存的是**一次都没发过**的那一段")
