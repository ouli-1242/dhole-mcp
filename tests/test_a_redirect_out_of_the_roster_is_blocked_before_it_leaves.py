"""名册外的那一跳要掐在发出之前 —— `_DocumentFence` 的形状全部按真机实测钉住。

症状（D-31 / D-31b，2026-10-08 与 10-09 两轮真 Chrome + 本机靶站按 `Host` 头记账）：
`page.route` 看不见主文档重定向的下一跳，于是一条 302 就能带着已登录的 profile 走出
授权名册；上一批只能在落地之后拒绝交回正文（结果边界），而请求确实已经发出去了。
CDP `Fetch` 域看得见那一跳，所以这一批把它掐在请求边界上。

**这只闸的每一个形状细节都是从真机上量回来的**，不是设计出来的（下面每条注释给出
对应的实测），因为它们不是风格问题：

  · patterns 限死 `resourceType: "Document"` —— 管所有类型时靶站看到的 route 事件从
    2 笔掉到 1 笔，先应答的一侧把子资源直接放出去，route 里的内网复检与跨源凭据头
    剥离就没机会了（`.scratch/liveverify/attach_fetch_bypass.py` V1）。
  · 每一笔 paused 必须应答 —— 故意不回的那一笔把导航吊到 20 秒超时（同处 V3）；
    `Fetch.doNothing` 在这台 Chrome 上直接报错（`attach_fetch_donothing.py` V4）。
  · 主文档与 iframe 用 `Page.getFrameTree` 的根 frame id 分 —— 302 的下一跳带着根 id，
    iframe 的文档带着另一个 id（`frame_scope_probe.py`）。混为一谈会整条拒掉一个
    嵌着第三方 iframe 的正常站点，那是回归而不是升级。

同一把尺也量了**浏览器之外**的那条发送线（`.scratch/liveverify/fence_attrib.py` +
`http_tier_stack.py` 按代码路径钉的调用栈）：attach 路径上私网目标要先过一次探活，
而探活跟着 302 跑到了名册外的主机名 —— 账上那笔 `Host: localhost` 与围栏无关，UA 又
不是判别器（两条栈都发 Windows/Macintosh 的 Chrome 指纹，primp 还一轮一变）。见文件末尾
`TestTheLivenessProbeStaysOnTheHostItAskedAbout`。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp.browser import (CDPHostNotAllowedError, _DocumentFence,
                                _await_stable_or_escape, _fence_blocked_error)

ROOT = "Aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-ROOT"
OTHER = "Bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb-FRME"
ROSTER = frozenset({"127.0.0.1"})


class _FakeSession:
    """一只假 CDP 会话：记下每一笔 send，事件手动喂。"""

    def __init__(self, root_id=ROOT, fail_methods=()):
        self.sent: list = []
        self.handlers: dict = {}
        self._root = root_id
        self._fail = set(fail_methods)

    async def send(self, method, params=None):
        self.sent.append((method, params or {}))
        if method in self._fail:
            raise RuntimeError("Target page, context or browser has been closed")
        if method == "Page.getFrameTree":
            if self._root is None:
                return {"frameTree": {}}
            return {"frameTree": {"frame": {"id": self._root}}}
        return {}

    def on(self, event, callback):
        self.handlers[event] = callback

    def methods(self):
        return [m for m, _ in self.sent]

    def paused(self, url, frame_id=ROOT, rid="r1", rtype="Document"):
        """喂一个 Fetch.requestPaused 事件并把它的异步处理跑完。"""
        self.handlers["Fetch.requestPaused"]({
            "requestId": rid, "frameId": frame_id, "resourceType": rtype,
            "request": {"url": url}})
        return _drain()


class _FakeContext:
    def __init__(self, session):
        self._session = session
        self.detached = 0

    async def new_cdp_session(self, page):
        if self._session is None:
            raise RuntimeError("no cdp channel here")
        return self._session


class _FakePage:
    def __init__(self, session):
        self.context = _FakeContext(session)
        self.url = "http://127.0.0.1:9222/home"


def _drain():
    return asyncio.sleep(0)


async def _installed(root_id=ROOT, fail_methods=()):
    session = _FakeSession(root_id=root_id, fail_methods=fail_methods)
    watch: list = []
    fence = _DocumentFence(_FakePage(session), ROSTER, watch)
    await fence.install()
    return session, watch, fence


@pytest.mark.asyncio
class TestTheShapeIsTheMeasuredOne:

    async def test_only_documents_are_intercepted(self):
        """patterns 必须限死 Document：管所有类型会把子资源从 route 手里抢走（实测 V1）。"""
        session, _, _ = await _installed()
        enable = [params for method, params in session.sent if method == "Fetch.enable"]
        assert len(enable) == 1
        patterns = enable[0]["patterns"]
        assert patterns, "Fetch.enable 没带 patterns 等于拦下整个浏览器的每一个请求"
        for pattern in patterns:
            assert pattern["resourceType"] == "Document", (
                f"这只闸伸向了子资源：{pattern}")
            assert pattern["requestStage"] == "Request", (
                "Response 阶段已经发出去了，掐不掉 —— 这一刀要的就是发出之前")
        assert {p["urlPattern"] for p in patterns} == {"http://*", "https://*"}, (
            "只写 http(s)：data:/blob:/about: 不是网络请求，把它们暂停只会弄坏页面")

    async def test_install_fails_loudly_so_the_caller_can_fall_back(self):
        """装不上就抛 —— fetch() 的那一侧靠这个异常退回 route + 结果边界那条线。"""
        fence = _DocumentFence(_FakePage(None), ROSTER, [])
        with pytest.raises(RuntimeError):
            await fence.install()

    async def test_remove_is_idempotent_and_swallows_a_dead_target(self):
        session, _, fence = await _installed(fail_methods=("Fetch.disable",))
        await fence.remove()
        await fence.remove()          # 第二次连会话都没有了，也不许冒异常
        assert "Fetch.disable" in session.methods()


@pytest.mark.asyncio
class TestTheFenceDecision:

    async def test_an_off_roster_main_document_is_ended_before_it_leaves(self):
        session, watch, fence = await _installed()
        await session.paused("http://localhost:9222/whoami")
        assert ("Fetch.failRequest", {"requestId": "r1",
                                      "errorReason": "BlockedByClient"}) in session.sent, (
            "名册外的主文档没被掐 —— 这一跳会带着已登录的 profile 走出去")
        assert watch == [("localhost", True)], f"这一跳没记账：{watch}"

    async def test_an_in_roster_document_is_released_and_says_nothing(self):
        session, watch, fence = await _installed()
        await session.paused("http://127.0.0.1:9222/home")
        assert ("Fetch.continueRequest", {"requestId": "r1"}) in session.sent
        assert watch == []

    async def test_an_off_roster_iframe_is_blocked_but_does_not_refuse_the_page(self):
        """iframe 的文档不是"主文档跑出名册"：拦下那一棵 iframe，整页照返（实测的 root?False）。

        把这个形状与上一条混为一谈，代价是一个嵌着第三方 iframe 的正常站点被整条拒绝 ——
        今天那种页面只是 iframe 被 route 拦掉、正文照样交回。
        """
        session, watch, fence = await _installed()
        await session.paused("http://localhost:9222/frame", frame_id=OTHER)
        assert any(m == "Fetch.failRequest" for m, _ in session.sent), (
            "名册外的 iframe 该被拦下（route 今天就是这么做的）")
        assert watch == [], f"一棵 iframe 不该把整页判死：{watch}"

    async def test_an_unknown_root_frame_fails_closed(self):
        """读不到根 frame id 时，每一笔名册外的 Document 都按主文档记账。

        偏向哪一边是有代价的：误判成主文档会多拒一个带第三方 iframe 的页面，误判成
        iframe 会漏掉一次真的跑出名册。漏掉那次没有任何东西兜着（落地页是错误页，
        结果边界认不出主机名），多拒一次调用方换个写法就能拿到内容 —— 所以 fail-closed。
        """
        session = _FakeSession(root_id=None)
        watch: list = []
        fence = _DocumentFence(_FakePage(session), ROSTER, watch)
        await fence.install()
        await session.paused("http://localhost:9222/frame", frame_id=OTHER)
        assert watch == [("localhost", True)], watch

    async def test_a_late_event_after_the_page_closed_raises_nothing(self):
        """页面关完之后还有 paused 事件进来是实测有的（TargetClosedError）。

        这里没有可救的请求：让 send 的失败冒出去只会把一次关闭竞态变成一条 traceback，
        而这条链正跑在用户的浏览器上。
        """
        session, watch, fence = await _installed(fail_methods=(
            "Fetch.continueRequest", "Fetch.failRequest"))
        await session.paused("http://127.0.0.1:9222/home")
        await session.paused("http://localhost:9222/whoami")
        assert any(m == "Fetch.failRequest" for m, _ in session.sent)

    async def test_events_are_answered_even_after_the_fence_is_removed(self):
        session, _, fence = await _installed()
        await fence.remove()
        await session.paused("http://localhost:9222/late")   # 不该抛，也不该再发
        assert session.methods().count("Fetch.failRequest") == 0


@pytest.mark.asyncio
class TestTheStabilityWaitDoesNotEatTheBudget:
    """被掐掉的导航不会再有 load 事件 —— 稳定等待必须让位给那条拒绝。

    不这么写的后果是真机量出来的（2026-10-09 的 `-m e2e`）：围栏确实拦住了那一跳，
    可 `_wait_for_stability` 还在等一个永远不来的 load，30 秒预算花光，先说话的是
    调用级预算那句"这一档超时了"。围栏做了它的工作、回执却报告另一件事，等于白拦。
    """

    async def test_a_blocked_main_document_releases_the_waiter_early(self):
        session, watch, fence = await _installed()
        slept = []

        async def never_settles():
            await asyncio.sleep(30)
            slept.append("the wait finished on its own")

        waiter = asyncio.ensure_future(_await_stable_or_escape(never_settles(), fence))
        await session.paused("http://localhost:9222/whoami")
        await asyncio.wait_for(waiter, timeout=2.0)
        assert slept == [], "稳定等待自己跑完了 —— 那这条链又会把预算花光"

    async def test_an_off_roster_iframe_does_not_release_the_waiter(self):
        """iframe 被拦不算"页面跑出名册"：稳定等待该照常跑完，正文照常交回。"""
        session, watch, fence = await _installed()
        done = []

        async def settles():
            await asyncio.sleep(0.01)
            done.append(True)

        waiter = asyncio.ensure_future(_await_stable_or_escape(settles(), fence))
        await session.paused("http://localhost:9222/frame", frame_id=OTHER)
        await asyncio.wait_for(waiter, timeout=2.0)
        assert done == [True]
        assert watch == []


class TestTheRefusalWords:
    """拒绝的话必须说清这一跳出去没出去 —— 那正是这两批改动各自挣到的那半句。"""


    def test_blocked_at_the_request_boundary_does_not_claim_it_left(self):
        err = _fence_blocked_error(["localhost"], ROSTER, left_browser=False)
        text = str(err)
        assert text.startswith("cdp_host_not_allowed: ")
        assert "ended before it left the browser" in text, text
        assert "already left the browser" not in text, (
            f"一句已经兑现不了的旧话又回来了：{text}")
        assert isinstance(err, CDPHostNotAllowedError)

    def test_the_result_boundary_still_says_it_left(self):
        """装不上那只闸的通道，承诺要退回原位 —— 不许顺手把没挣到的话说满。"""
        text = str(_fence_blocked_error(["localhost"], ROSTER, left_browser=True))
        assert "already left the browser" in text, text
        assert "ended before it left" not in text, text

    def test_the_way_out_survives_both_wordings(self):
        for left in (True, False):
            text = str(_fence_blocked_error(["localhost"], ROSTER, left_browser=left))
            assert "name it as the url with confirm=true" in text, text

    def test_every_blocked_host_is_named(self):
        text = str(_fence_blocked_error(["localhost", "other.example.test"],
                                        ROSTER, left_browser=False))
        assert "localhost" in text and "other.example.test" in text, text
        assert "['127.0.0.1']" in text, f"名册本身也要说：{text}"


@pytest.mark.asyncio
class TestTheLivenessProbeStaysOnTheHostItAskedAbout:
    """私网目标的探活不许跟着 302 走出名册 —— 它问的从来只是"这个端口有人应答吗"。

    实测（`.scratch/liveverify/fence_attrib.py` 按代码路径记账，两轮一致）：一条
    `127.0.0.1 → localhost` 的 302 在围栏掐掉浏览器那一跳的同时，仍被探活自己跟到了
    `localhost` 并读了正文（靶站日志里那句 `All Trafilatura methods failed for
    http://localhost:.../whoami` 就是它在解析名册外那一页）。判"活"不需要第二跳：3xx
    本身就是一声应答。
    """

    async def _probe(self, status):
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        srv = MasterFetchServer(cache_ttl=0)
        seen: dict = {}

        async def fake_get(url, **kw):
            seen.update(kw)
            seen["url"] = url
            return ResponseModel(url=url, status=status, content=[], error="")

        srv.get = fake_get
        out = await srv._private_health_probe(
            "http://127.0.0.1:9222/fence", None, None, None, 0.0)
        return seen, out

    async def test_the_probe_asks_for_one_hop_only(self):
        seen, _out = await self._probe(302)
        assert seen.get("follow_redirects") is False, (
            f"探活跟了重定向 = 它替被探的那个主机又向外发了一笔：{seen}")

    async def test_a_redirect_still_counts_as_alive(self):
        """3xx 不跟也要算活着 —— 否则浏览器层被跳过，围栏那侧的事再也没人做。"""
        _seen, out = await self._probe(302)
        assert out is None, "3xx 被当成了目标不健康"

    async def test_a_dead_port_still_short_circuits(self):
        _seen, out = await self._probe(0)
        assert out is not None and "skipped_stealthy" in out.escalation_path
