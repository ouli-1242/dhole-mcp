"""千页确定性批量：web_discover 的 plan_id → web_crawl 的 resume_token 续传
（批次8 刀2 ＝ P11 / F-D-J2，C 档，用户放行后实施）。

评审的算式：1000 个文档 URL 的任务按今天的形状要 agent 当传送带——10 轮 ×
100 条 urls= ≈ 76,100 字符的模型生成参数；plan_id ＋续传 token 同样 10 轮
≈ 370 字符。三阶段：discover 留清单回 plan_id（阶段1）；web_crawl 带三颗新参
selector/schema/resume_token 做确定性投影，预算到点用 next_resume_token 带回
断点（阶段2）；失败行照常随行返回而不是烂在服务器里（阶段3）。

验收判据（报告原文）：web_crawl 20 参 ≤ PARAM_CAP 27；连接期 ≤30,750 实读
（净额 −22，棘轮只降）；续传确定性＝同一计划同一 offset ⇒ 同一批页、同一断点。
真实千页端到端需真站点——留停机点（§9.2），这里用回环靶站钉全部算术与形状。

注：本刀测试写在实现之后（红测试顺序偏离七步契约第 2 步）——wire 面的
净额核算逼着"写一句量一次"的循环，先落描述再立测试。断言全部对着报告的
判据句写，实现回退任何一段算术它们都会红。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import fixture_site as fs  # noqa: E402

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402
from tests.test_wire_shape import PARAM_CAP  # noqa: E402

_REAL_GETADDRINFO = None


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    import socket
    global _REAL_GETADDRINFO
    if _REAL_GETADDRINFO is None:
        _REAL_GETADDRINFO = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    srv = fs.make_server("127.0.0.1", 0)
    thread = __import__("threading").Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def _private_ok():
    identity.set_value("allow_private", True)
    yield
    identity.set_value("allow_private", False)


async def _discover(site: str, items: int = 6):
    srv = MasterFetchServer()
    return await srv.web_discover(f"{site}/collections?items={items}",
                                  max_pages=10)


async def _plan_urls(site: str, items: int = 6):
    """discover 一份计划，返回（plan_id, 计划里的 /work/ 条数）。"""
    result = await _discover(site, items)
    assert not result.error, result.error
    assert result.plan_id, "阶段1：discover 必须回 plan_id"
    assert f"resume_token='{result.plan_id}:0'" in result.next_action, \
        "回执要点名下一步的写法，agent 才不必猜 token 的形状"
    return result.plan_id, sum("/work/" in p.url for p in result.pages)


class TestCrawlPicksUpASelectorAndSchema:

    @pytest.mark.asyncio
    async def test_crawl_picks_up_a_selector_and_schema(self, live_site):
        """selector=/schema= 投影计划里的每一页；不带它们形状退化为整页正文。"""
        srv = MasterFetchServer()
        plan_id, _ = await _plan_urls(live_site)
        # 带 schema：每页投成结构化记录（作品名来自 h1.title）
        out = await srv.web_crawl(
            live_site, resume_token=f"{plan_id}:0",
            schema={"properties": {"name": {"selector": "h1.title"}}})
        assert not out.error, out.error
        assert out.pages, "计划里的页要真的被抓回来"
        # 计划里除 /work/ 外还有种子页与 about——行必须 ⊆ 计划且真覆盖 /work/
        assert any("/work/" in p.url for p in out.pages)
        blob = "\n".join("".join(p.content) for p in out.pages)
        assert "作品" in blob and "name" in blob, "投影产物在行里"
        # 不带 selector/schema：整页 markdown（现状形状），正文仍在
        plain = await srv.web_crawl(live_site, resume_token=f"{plan_id}:0")
        assert not plain.error, plain.error
        blob = "\n".join("".join(p.content) for p in plain.pages)
        assert "独有正文" in blob, "无投影时退化回现状的整页形状"

    @pytest.mark.asyncio
    async def test_selector_without_a_plan_is_refused_not_ignored(self, live_site):
        """投影参数的作用对象只有计划：没有 resume_token 就具名拒绝，一个请求不出。"""
        srv = MasterFetchServer()
        out = await srv.web_crawl(live_site, selector=".title")
        assert "resume_token" in (out.error or "")
        assert out.pages == []

    @pytest.mark.asyncio
    async def test_a_plan_this_process_does_not_hold_is_named(self, live_site):
        srv = MasterFetchServer()
        out = await srv.web_crawl(live_site, resume_token="deadbeef:0")
        # 不是"过期了"：计划只存在创建它的那个进程里，而这一句必须把范围说对。
        assert "no plan 'deadbeef' is held by this server process" in (out.error or "")
        out = await srv.web_crawl(live_site, resume_token="not-a-cursor")
        assert "resume_token must be" in (out.error or "")

    @pytest.mark.asyncio
    async def test_two_discoveries_hold_two_plans(self, live_site):
        a, _ = await _plan_urls(live_site)
        b, _ = await _plan_urls(live_site)
        assert a != b, "每次 discover 是一份新计划，不互相覆盖"
        srv = MasterFetchServer()
        first = await srv.web_crawl(live_site, resume_token=f"{a}:0")
        assert not first.error, "旧计划在 TTL 内仍可续传"


class TestCrawlResumeTokenContinuesWhereTheDeadlineBit:

    @pytest.mark.asyncio
    async def test_crawl_resume_token_continues_where_the_deadline_bit(
            self, live_site):
        """预算到点 → 断点 token；续传从断点接着跑，一页不重、一页不漏。"""
        srv = MasterFetchServer()
        plan_id, plan_size = await _plan_urls(live_site, items=6)
        assert plan_size >= 4, "靶站的计划要真有十几条 /work/，续传才有意义"
        # ① 页帽切：max_pages=2 → 恰好 2 行 + 断点
        out = await srv.web_crawl(live_site, resume_token=f"{plan_id}:0",
                                  max_pages=2)
        assert len(out.pages) == 2 and out.next_resume_token, \
            (len(out.pages), out.next_resume_token)
        assert "plan URL(s) left" in (out.next_action or "")
        seen = [p.url for p in out.pages]
        # ② 断点续传：同一计划、token 里的 offset ⇒ 不重不漏
        out2 = await srv.web_crawl(live_site, resume_token=out.next_resume_token,
                                   max_pages=2)
        seen += [p.url for p in out2.pages]
        assert not (set(p.url for p in out.pages) & set(p.url for p in out2.pages)), \
            "确定性投影不许重发已抓过的页"
        # ③ 时间闸切（批间检查，与 BFS 爬取同语义）：1ms 预算至多放行一批，
        # 到点即在信封上说"被时间停了"并带回断点。
        cut = await srv.web_crawl(live_site, resume_token=f"{plan_id}:0",
                                  deadline_ms=1)
        assert cut.truncated_by_time and len(cut.pages) <= 3
        assert cut.next_resume_token, "计划未跑完时断点必须在场"
        # ④ 跑到计划尽头：token 缺席（省略即默认），计划条数对得上
        token = out2.next_resume_token
        out3 = None
        while token:
            out3 = await srv.web_crawl(live_site, resume_token=token, max_pages=40)
            seen += [p.url for p in out3.pages]
            token = out3.next_resume_token
        assert out3 is not None
        assert len(seen) == len(set(seen)), "一页不重"
        plan_urls = (await _discover(live_site, 6)).pages
        assert set(seen) >= {p.url for p in plan_urls if "/work/" in p.url}, \
            "一页不漏：计划里每条 /work/ 都被某一次续传抓到"
        assert "Plan exhausted" in (out3.next_action or "")


class TestCrawlParamCountStaysUnderTheRatchet:

    def test_crawl_param_count_stays_under_the_ratchet(self):
        """+3 参后 17→20；PARAM_CAP=27 是形状棘轮，这一格钉的是本刀的消耗。"""
        td = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}["web_crawl"]
        count = len(td["inputSchema"]["properties"])
        assert count == 20, count
        assert count <= PARAM_CAP

    def test_the_three_new_params_survive_dispatch_not_just_the_signature(self, monkeypatch):
        """schema 里宣告的键必须走通 dispatch（test_top_level_args 同一判据）。

        只测 handler 签名是自欺：_forward 只转发 _TOP_LEVEL_ARGS 里的名字，
        表格里少一个，agent 按 schema 调用就会被"未知参数"拒在门外 ——
        这正是那类"看起来成功"的失败（测试绿、真路死）。
        """
        seen: dict = {}

        async def fake(self, **kwargs):
            seen.update(kwargs)
            from dhole_mcp.crawl import CrawlResponseModel
            return CrawlResponseModel(start_url="https://example.com/", pages=[])

        monkeypatch.setattr(MasterFetchServer, "web_crawl", fake)
        import asyncio
        asyncio.run(MasterFetchServer()._dispatch("web_crawl", {
            "url": "https://example.com/",
            "resume_token": "abcd1234ef56:0",
            "selector": "h1.title",
            "schema": {"properties": {"name": {"selector": "h1.title"}}},
        }))
        assert seen.get("resume_token") == "abcd1234ef56:0"
        assert seen.get("selector") == "h1.title"
        assert seen.get("schema") == {"properties": {"name": {"selector": "h1.title"}}}
