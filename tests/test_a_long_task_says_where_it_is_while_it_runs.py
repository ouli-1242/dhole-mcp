"""批次 12 · 长任务要一边跑一边说"走到哪了"（notifications/progress）。

判据来自验收里的一条实测：一次 `web_crawl` 数十秒，期间 stdio 上**一个字节都没有**
（全 `src/` grep 无 `report_progress` / `notifications/progress`），客户端能显示的只
有"在转"。等待的人拿不到任何信息，超时与取消策略因此只能瞎猜；而 17.0 的
`deadline_ms` 默认 25s，正好落在"看不见进度"与"宿主自己掐了"两种猜测之间。

三处格点，都是**已经存在**的自然边界，不额外造轮子：

一、`crawl.web_crawl` 的每一批（一批就是 `concurrency` 页，一页一报没有意义），
   加收尾一次 —— 收尾那次要说最终数字，循环顶上报的是"最后一批开始前"的状态。
二、`server._crawl_plan_resume` 的每一批（续传一份 discover 计划同样是几十秒）。
三、`actions` 链的每一步 —— 一步 `wait` 是显式的秒数，一步 click 可能等 settle。

**进度不许弄坏任务**是这批唯一不能松的规矩：客户端不支持、连接半路断、序列化出错，
后果都只能是"这一格没说出去"。所以 `context.report_progress` 吞掉 `Exception` 并返回
False；`CancelledError` 不在吞的范围里（BaseException），取消就该取消。

没有 MCP 会话的那些路径（CLI、直连 `_dispatch`、测试）一律不发也不报错 —— 会话是
`call_tool` 入口存进 `_MCP_SESSION` 的，摘掉之后与改动前逐字节相同。
"""
import asyncio

import pytest

from dhole_mcp import actions as A
from dhole_mcp import context as C
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.server import MasterFetchServer, ResponseModel

SEED = "https://progress.example.com/"


class _RecordingSession:
    """只回答两件事：这次通知送出去了没有、送出去的是哪些数。"""

    def __init__(self, boom=False):
        self.events: list = []
        self.boom = boom

    async def report_progress(self, progress, total=None, message=None):
        if self.boom:
            raise RuntimeError("半断的连接：这条送不出去")
        self.events.append((progress, total, message))


@pytest.fixture()
def session():
    """把一颗假会话装上，用完摘下（ContextVar 会顺着同一 task 传下去）。"""
    def _install(rec):
        C.set_mcp_session(rec)
        return rec
    yield _install
    C.set_mcp_session(None)


class _FakeSite:
    def __init__(self, pages=None):
        self.fetched: list = []
        self.pages = pages or {}

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        body = self.pages.get(url, "<p>leaf</p>")
        return ResponseModel(url=url, status=200, content=[body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _page_with_links(links):
    return ("<html><body>"
            + "".join(f'<a href="{h}">t</a>' for h in links)
            + "</body></html>")


# ─── 1 · 那颗发送器自己的规矩 ────────────────────────────────────────────────

def test_no_session_sends_nothing_and_raises_nothing():
    """CLI / 直连 dispatch：会话是 None，进度这条通道必须静默。"""
    C.set_mcp_session(None)
    assert asyncio.run(C.report_progress(1.0, total=3.0, message="x")) is False


def test_a_send_failure_is_not_the_callers_problem(session):
    """客户端不收通知 ≠ 任务失败。这一条是整个改动的安全边界。"""
    rec = session(_RecordingSession(boom=True))
    assert asyncio.run(C.report_progress(1.0, total=3.0, message="x")) is False
    assert rec.events == []


def test_a_cancellation_is_not_swallowed(session):
    """取消是 BaseException，不能被"吞掉发送失败"顺手一起吃掉。"""
    class _Cancelled:
        async def report_progress(self, progress, total=None, message=None):
            raise asyncio.CancelledError()

    session(_Cancelled())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(C.report_progress(1.0, total=3.0, message="x"))


def test_the_recipient_sees_the_numbers_and_the_words(session):
    rec = session(_RecordingSession())
    asyncio.run(C.report_progress(2.0, total=5.0, message="two of five"))
    assert rec.events == [(2.0, 5.0, "two of five")]


# ─── 2 · 爬取：每一批一格，收尾说最终数字 ────────────────────────────────────

@pytest.mark.asyncio
async def test_a_crawl_reports_while_it_is_running(session):
    rec = session(_RecordingSession())
    links = [f"/p{i}" for i in range(6)]
    site = _FakeSite({SEED: _page_with_links(links)})
    out = await crawl_mod.web_crawl(site, SEED, max_pages=4, max_depth=1,
                                    concurrency=2, cache_ttl=0)
    assert out.pages_crawled >= 2, "爬取本身没跑起来，这条在测别的东西"
    assert rec.events, "几十秒的任务一条进度都没发 —— 那就是原来的行为"
    assert all(total == 4.0 for _, total, _ in rec.events), (
        "total 应当是调用方自己给的 max_pages，不是会涨会落的队列长度")
    seen = [p for p, _, _ in rec.events]
    assert seen == sorted(seen), f"进度倒退了：{seen}"


@pytest.mark.asyncio
async def test_the_last_crawl_report_is_the_final_count(session):
    """收尾那一格要说**最终**数字：只报"下一批开始前"会少说一整批。"""
    rec = session(_RecordingSession())
    links = [f"/p{i}" for i in range(6)]
    site = _FakeSite({SEED: _page_with_links(links)})
    out = await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                    concurrency=2, cache_ttl=0)
    assert rec.events[-1][0] == float(out.pages_crawled), (
        f"最后报的是 {rec.events[-1][0]}，实际爬了 {out.pages_crawled} 页")
    assert "crawl finished" in rec.events[-1][2]


@pytest.mark.asyncio
async def test_a_broken_progress_channel_does_not_stop_the_crawl(session):
    """反向守卫：客户端不收通知时，爬取照常返回它的页。"""
    rec = session(_RecordingSession(boom=True))
    links = [f"/p{i}" for i in range(4)]
    site = _FakeSite({SEED: _page_with_links(links)})
    out = await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                    concurrency=2, cache_ttl=0)
    assert out.pages_crawled == 2
    assert rec.events == []


# ─── 3 · 动作链：一步一步说 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_action_chain_reports_each_step(session):
    rec = session(_RecordingSession())
    page = object()          # wait / scroll 这类动作不碰 locator，假 page 就够
    action = A.build_page_action([{"wait": 1}, {"wait": 1}, {"wait": 1}])
    await action(page)   # page 只在需要 locator 的动作里才被碰；wait 不碰
    totals = [t for _, t, _ in rec.events]
    assert totals == [3.0, 3.0, 3.0], f"三步链的进度不该带别的 total：{totals}"
    assert [p for p, _, _ in rec.events] == [0.0, 1.0, 2.0], (
        "报的是「将要执行到第几步」，最后一步之后由响应本身收尾")
    assert "action 3/3" in rec.events[-1][2]


@pytest.mark.asyncio
async def test_a_single_action_chain_still_says_something(session):
    """一步的链也是长任务（那一步可能就是 20s 的 wait）：不能因为"只有一步"就不说。"""
    rec = session(_RecordingSession())
    action = A.build_page_action([{"wait": 5}])
    await action(object())
    assert len(rec.events) == 1 and rec.events[0][1] == 1.0


# ─── 4 · 续传计划的批边界 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_resumed_plan_reports_per_batch(session, monkeypatch):
    """`_crawl_plan_resume` 同样是几十秒的一趟，格点与 web_crawl 一致。"""
    rec = session(_RecordingSession())
    srv = MasterFetchServer()
    urls = [f"https://plan.example.com/p{i}" for i in range(6)]
    pid = crawl_mod.plan_store(urls)

    async def _fake_read(self, url, **kwargs):
        return ResponseModel(url=url, status=200, content=["body text"],
                             fetcher_used="http", content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_read", _fake_read)
    out = await srv._crawl_plan_resume(
        url="https://plan.example.com/", resume_token=f"{pid}:0",
        selector=None, schema=None, max_pages=4, max_content_chars_per=8000,
        max_total_chars=200000, concurrency=2, cache_ttl=0, force_fetcher=None,
        timeout=30000, deadline_ms=25000, ignore_robots=True)
    assert len(out.pages) >= 2, "续传本身没跑起来，这条在测别的东西"
    assert rec.events, "一份计划的续传几十秒，期间一个字都没发"
    assert all(t == 4.0 for _, t, _ in rec.events), (
        "total 该是这次调用被允诺的上限，不是计划里 URL 的条数")
    seen = [p for p, _, _ in rec.events]
    assert seen == sorted(seen), f"进度倒退了：{seen}"
