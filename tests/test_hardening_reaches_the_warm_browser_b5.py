"""B5-14a-2：档位得真的落在那台浏览器上 —— 共享的自动会话以前是按"不设防"启动的。

发现路径不在测试里，在下一刀的计划里：14b 要给 `web_screenshot` 补 `anti_detect`
（§4 第 3 行），先去看它接不接得住，答案是接不住。

`hide_canvas` 与 `block_webrtc` 是**启动级**的：一个进 chromium 的启动参数，一个进
context 的 init script，浏览器一起来就改不动。而 `_ensure_auto_session` 起那台共享
浏览器时只传了 `headless` 与 `profile_name` —— 其余全用 `open_session` 自己的
`hide_canvas=False, block_webrtc=False, solve_cloudflare=False`。于是每一次走自动
会话的浏览器抓取（也就是绝大多数 stealthy 调用）跑在一台**没有画布噪声、没有 WebRTC
屏蔽、不答 Cloudflare 挑战**的浏览器上，而 `auto` 档的定义里这三条都在。

链路是通了一半的：`_force_fetch` 把档位摊开成 kwargs 交给 `stealthy_fetch`，
`bulk_stealthy_fetch` 在 `if session_id:` 那一支里只往下传 timeout / headers /
disable_resources / wait_selector / proxy / solve_cloudflare / page_action ——
那两颗启动级的 kwargs 到了这一层蒸发，一句响的都没有。

所以这一刀做三件事：

1. 那台共享浏览器按**当下生效的档位**（cred_defaults 存的，缺省 auto）启动，
   与 profile 同一套规矩：换了就轮换（冷启动一次是该付的），没换就复用；
2. 命名会话（cookie 桥的载体）也按同一档位启动 —— 它没有理由比共享的那台更不设防；
3. 一次调用要的档位如果与它要复用的那台浏览器不一致，**这一趟自己起一台**，并在
   回执里说明代价。静默丢掉是原来那个 bug 的形状：说一套做一套，还看不出来。

第三条同时是一条兜底：以后再有"启动级参数"进这张表，路径不会再悄悄漏。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import browser as browser_mod
from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server
from dhole_mcp.server import _ANTI_DETECT, _anti_detect

URL = "https://example.com/"


def _fake_open(monkeypatch, opened: list) -> None:
    """`open_session` 的桩：记下启动参数，并登记一台带着这些参数的活会话。

    登记的会话必须**带着**那两颗启动级开关，因为轮换与复用的判据就是"这台浏览器是
    按什么档起来的" —— 桩如果不带，测的就成了桩。
    """
    from dhole_mcp.server import _SessionEntry

    async def fake_open(self, **kw):
        opened.append(kw)
        sid = kw.get("session_id") or ("sid-" + str(kw.get("profile_name")))

        class _Live:
            _is_alive = True
            cdp_attached = False
            launch_note = ""      # 起的是隐形的那台，所以真会话在这一句上会回空串
            _hide_canvas = kw.get("hide_canvas", False)
            _block_webrtc = kw.get("block_webrtc", False)
            _solve_cloudflare = kw.get("solve_cloudflare", False)

            async def close(self):
                pass

        self._sessions[sid] = _SessionEntry(session=_Live(), session_type="stealthy")
        return type("R", (), {"session_id": sid})()

    monkeypatch.setattr(Server, "open_session", fake_open)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)


def _launch_rung(kw: dict) -> tuple:
    """这台浏览器起来时真正带着的两颗启动级开关。"""
    return (kw.get("hide_canvas"), kw.get("block_webrtc"))


def _expected(level: str) -> tuple:
    hard = _anti_detect(level)
    return (hard["hide_canvas"], hard["block_webrtc"])


class TestTheWarmBrowserIsBuiltAtARung:

    def test_the_default_rung_is_what_the_auto_browser_launches_with(self, monkeypatch):
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_auto_session())
        assert opened, "自动会话根本没建起浏览器"
        assert _launch_rung(opened[0]) == _expected("auto"), (
            f"共享浏览器带着 {_launch_rung(opened[0])} 起来了，而 auto 档要的是 "
            f"{_expected('auto')} —— 每一次 stealthy 抓取都跑在这台上")
        assert opened[0]["solve_cloudflare"] is True, \
            "web_screenshot 不逐次传这颗，它读的是会话启动时的默认"

    def test_a_stored_rung_is_what_the_auto_browser_launches_with(self, monkeypatch):
        """档位能落盘（§3-17），落的就必须是建浏览器时用的那一份。"""
        identity.set_value("anti_detect", "off")
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_auto_session())
        assert _launch_rung(opened[0]) == (False, False), opened[0]
        assert opened[0]["solve_cloudflare"] is False

    def test_changing_the_rung_rotates_the_warm_browser(self, monkeypatch):
        """与 profile 同一判据：换档之后还复用旧的那台，新的默认就永远不生效。"""
        server = Server()
        opened: list = []
        closed: list = []

        async def fake_close(self, sid):
            closed.append(sid)
            self._sessions.pop(sid, None)
            return 0, False, False

        _fake_open(monkeypatch, opened)
        monkeypatch.setattr(Server, "_close_one_session", fake_close)
        asyncio.run(server._ensure_auto_session())
        assert _launch_rung(opened[0]) == _expected("auto")
        identity.set_value("anti_detect", "off")
        asyncio.run(server._ensure_auto_session())
        assert closed, "换了档位还复用按旧档起来的浏览器"
        assert _launch_rung(opened[-1]) == (False, False), opened[-1]

    def test_the_same_rung_reuses_the_warm_browser(self, monkeypatch):
        """轮换只在真的换了的时候付钱。"""
        server = Server()
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(server._ensure_auto_session())
        asyncio.run(server._ensure_auto_session())
        assert len(opened) == 1, "每次抓取都冷启动一遍浏览器"

    def test_a_named_session_gets_the_same_rung(self, monkeypatch):
        """命名会话是登录态的载体，没有理由比共享的那台更不设防。"""
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_session_browser("sess1"))
        assert _launch_rung(opened[0]) == _expected("auto"), opened[0]


# ─── 复用的那台浏览器：档位不合就不骑它 ─────────────────────────────

def _serve(monkeypatch, *, warm: tuple, asked: str) -> dict:
    """让一次调用去复用一台按 `warm` 档起来的浏览器，问的是 `asked` 档。

    返回"真的跑了这一趟的那台浏览器"带着哪一对启动级开关、它是复用的还是新起的，
    以及回执里的 notes。两条路的 fetch 一律抛出去：`bulk_stealthy_fetch` 用
    gather(return_exceptions) 收，异常成一条失败结果而不会炸测试 —— 这一刀要看的
    只是哪台浏览器服务了这一次，不是它答了什么。
    """
    seen: dict = {"how": None, "launch": None, "created": [], "notes": []}

    class _WarmSession:
        _is_alive = True
        cdp_attached = False
        # 与 _OneShotBrowser 同一句理由：真会话对外说得出这两件事，假件就得说得出。
        launch_note = ""
        _hide_canvas, _block_webrtc = warm

        async def fetch(self, url, **kw):
            seen["how"] = "reused"
            seen["launch"] = (self._hide_canvas, self._block_webrtc)
            raise RuntimeError("probe: stop on the reused session")

    class _OneShotBrowser:
        def __init__(self, **kw):
            seen["created"].append(kw)
            seen["how"] = "one-shot"
            seen["launch"] = (kw.get("hide_canvas"), kw.get("block_webrtc"))
            # 一台真会话对外说得出两件事：attach 没 attach、它是被怎么起来的
            # （批次 11 的 `launch_note`，headless 时是空串）。假件少一颗就是比真件
            # 宽容 —— 那种宽容正好藏住"回执读不到那句话"这类事故。
            self.cdp_attached = False
            self.launch_note = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetch(self, url, **kw):
            raise RuntimeError("probe: stop after the launch args")

    from dhole_mcp.server import _SessionEntry

    monkeypatch.setattr(browser_mod, "StealthyBrowser", _OneShotBrowser)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    server = Server()
    server._sessions["warm"] = _SessionEntry(session=_WarmSession(),
                                             session_type="stealthy")
    hard = _anti_detect(asked)
    token = server_mod._ARG_NOTES.set(seen["notes"])
    try:
        asyncio.run(server.bulk_stealthy_fetch(
            [URL], session_id="warm", hide_canvas=hard["hide_canvas"],
            block_webrtc=hard["block_webrtc"],
            solve_cloudflare=hard["solve_cloudflare"], timeout=5000))
    finally:
        server_mod._ARG_NOTES.reset(token)
    return seen


_HALF = (True, False)
WARMS = {"auto": _expected("auto"), "off": _expected("off"), "half": _HALF}


class TestTheRungIsNotDroppedOnAReusedSession:

    def test_a_matching_session_is_still_reused(self, monkeypatch):
        """这一刀不许把快路径拆了：档位对上就接着用那台热浏览器。"""
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="auto")
        assert seen["how"] == "reused", seen
        assert not seen["created"], seen["created"]

    def test_a_session_at_another_rung_is_not_ridden(self, monkeypatch):
        """以前：off 的请求落在一台开着画布噪声的共享浏览器上，kwargs 走到那一支
        就蒸发，一句响的都没有。现在：这一趟自己起一台。"""
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="off")
        assert seen["how"] == "one-shot", seen
        assert seen["launch"] == _expected("off"), seen

    @pytest.mark.parametrize("warm_name", sorted(WARMS))
    @pytest.mark.parametrize("asked", sorted(_ANTI_DETECT))
    def test_every_rung_reaches_the_browser_that_served_it(self, monkeypatch,
                                                          asked, warm_name):
        """最强的一条：不管复用还是新起，真跑了这一趟的那台浏览器带的就是这一档。

        断言不打在"kwargs 送到了哪一层"—— 上一版的说法正是"送到了"，而送到之后在
        复用的那一支被丢掉。十二种组合全过，才叫档位落在了地上。
        """
        warm = WARMS[warm_name]
        seen = _serve(monkeypatch, warm=warm, asked=asked)
        assert seen["launch"] == _expected(asked), (asked, warm_name, seen)


class TestTheCostIsSaidOutLoud:

    def test_giving_up_the_shared_session_is_reported(self, monkeypatch):
        """换一台不只是慢一点：那一台的 context（登录态）不跟过去。

        静默换台与静默丢档位是同一种错的两面 —— 调用方以为说的是"在这一台上按 off
        跑"，实际是"换一台跑"。回执里那条 note 是唯一看得见的地方。
        """
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="off")
        assert seen["how"] == "one-shot", seen
        assert any("browser" in n.lower() for n in seen["notes"]), seen["notes"]

    def test_the_fast_path_says_nothing(self, monkeypatch):
        seen = _serve(monkeypatch, warm=_expected("auto"), asked="auto")
        assert not seen["notes"], seen["notes"]


class TestTheNoteReachesTheCaller:
    """note 不能只活在一个没人读的 list 里 —— 那条路等于没说。

    上面那组测的是"哪台浏览器跑了这一趟"，这一条测的是"调用方看得见吗"：走一次
    真实的 web_read，桩只换浏览器本身，envelope 那几层（summary / agent hints）
    照原样跑。
    """

    @staticmethod
    def _html_response(url):
        from dhole_mcp.fetcher import Response
        return Response(url=url, body=b"<html><body><p>page</p></body></html>",
                        status=200, headers={"content-type": "text/html"},
                        encoding="utf-8")

    def test_a_rung_that_needed_its_own_browser_says_so_in_the_summary(self, monkeypatch):
        from dhole_mcp.server import _SessionEntry

        class _WarmSession:
            _is_alive = True
            cdp_attached = False
            launch_note = ""
            _hide_canvas, _block_webrtc = _expected("auto")

            async def fetch(self, url, **kw):  # pragma: no cover - 不该被走到
                raise AssertionError("the off call rode the hardened shared browser")

        class _Own:
            cdp_attached = False
            launch_note = ""

            def __init__(self, **kw):
                assert (kw.get("hide_canvas"), kw.get("block_webrtc")) == (False, False)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetch(self, url, **kw):
                return TestTheNoteReachesTheCaller._html_response(url)

        server = Server()
        server._sessions["warm"] = _SessionEntry(session=_WarmSession(),
                                                 session_type="stealthy")

        async def fake_auto(self, **kw):
            return "warm"

        monkeypatch.setattr(Server, "_ensure_auto_session", fake_auto)
        monkeypatch.setattr(browser_mod, "StealthyBrowser", _Own)
        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
        out = asyncio.run(server.web_read(URL, force_fetcher="stealthy",
                                         anti_detect="off", cache_ttl=0))
        assert not out.error, out
        assert "own browser" in (out.summary or "").lower(), out.summary


class TestWhatACaptureCanSteerPerCall:
    """A1：web_screenshot 那扇门下线了，捕获住进 web_act 的动作链。

    原来钉在这个类里的三件事各有去处：档位逐次决定"要不要答挑战"由 web_act
    自己的 anti_detect 处理（同一扇门上的既有类守）；缺席读存储由 cred_defaults
    的接线守；"捕获不许挡资源"由 actions 的 _has_screenshot 分支与
    test_eval_fixes_g35::test_a_screenshot_action_turns_resource_blocking_off
    守。这里只留接线仍在的凭证：screenshot 键就是那个分支的开关。
    """

    def test_the_screenshot_key_is_the_resource_shortcut_switch(self):
        from dhole_mcp.actions import actions_want_visual
        assert actions_want_visual([{"screenshot": "img"}]) is True
        assert actions_want_visual([{"click": "a"}]) is False
        assert actions_want_visual([{"click": "a"}, {"snapshot": True},
                                {"screenshot": {"selector": "img",
                                                "decode_qr": True}}]) is True

