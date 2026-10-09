# -*- coding: utf-8 -*-
"""围栏装不上的那一发：退路必须是接线，不是一句注释

D-31b 把主机围栏从结果边界搬到请求边界，代价是它多出一条**装不上**的退路。`browser.py`
里那条线写的是：`_DocumentFence.install()` 抛 → `fence = None` → `await candidate.remove()`
→ 回到 route + 结果边界，并且拒绝措辞换成"那一跳已经出去了"。§3 里这条一直挂在"未验"，
原因很实在：它外面是 `BrowserSession.fetch`（1809-2134 行，三百多行的 async 体），要一台
真 Chrome 才能整条驱动。

这一把不假装能量整条 `fetch` —— 它量退路的三环，每环都用它能被真正驱动的那一层：

 1. **接线（静态）**：那个 try/except 的 except 臂必须同时做两件事 —— 把 `fence` 交回
    None，以及真去 `await candidate.remove()`。只写日志不算摘：`install()` 是在
    `new_cdp_session` 成功、监听器已经挂上之后才可能抛的（`Fetch.enable` 是最后一步），
    那时留下的是一只**半挂上的 Fetch 钩子** —— 而"每一笔 paused 都必须应答"是 D-31b
    在真机上量出来的第一条（故意不回那一笔把导航吊到 20 秒）。
    还钉两件事：`fence` 在 try **之前**就是 None（少了这一步，后面每一次
    `if fence is not None` 都是一颗 NameError），以及稳定等待与赛跑那一支必须被
    `fence is not None` 看着（fence 不在时还去 `wait_for_escape()` 就是等一只不存在的闸）。
 2. **半只钩子（行为）**：`Fetch.enable` 抛的那只 fence，`remove()` 必须不抛、并把
    session 交回 None；此后晚到的 `Fetch.requestPaused` 走到底既不炸也不对死 session
    说话。
 3. **措辞（行为）**：两支不许越界 —— 请求边界那句说"在发出之前终止"，装不上那句说
    "已经离开浏览器"。这一对是 §1 D-31b 那句"承诺跟着实际发生的走"唯一的可执行形式。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib

import pytest

from dhole_mcp import browser as browser_mod
from dhole_mcp.browser import (_DocumentFence, _fence_blocked_error,
                               _landed_host_outside_fence)

BROWSER_SRC = pathlib.Path(inspect.getsourcefile(browser_mod)).read_text(encoding="utf-8")


def _fetch_body() -> ast.AsyncFunctionDef:
    tree = ast.parse(BROWSER_SRC)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "fetch":
            return node
    raise AssertionError("browser.py 里找不到 fetch —— 判据的坐标断了")


def _install_try(func: ast.AST):
    """那个 `await candidate.install()` 所在的 try —— 按被包住的语句认，不按行号。"""
    for node in ast.walk(func):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.body:
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
                    and isinstance(stmt.value.value, ast.Call)
                    and getattr(stmt.value.value.func, "attr", None) == "install"):
                yield node, stmt
    return


class TestTheFallbackIsWired:
    """退路的三颗钉：except 臂摘干净、fence 先有 None、赛跑那一支看着 fence。"""

    def test_the_except_arm_both_clears_the_fence_and_removes_the_candidate(self):
        tries = list(_install_try(_fetch_body()))
        assert len(tries) == 1, f"装不上这条线只该有一处，量到 {len(tries)} 处"
        node, _install_stmt = tries[0]
        assert node.handlers, "install 的 try 没有 except 臂 —— 装不上就是整条调用炸掉"
        arm = node.handlers[0]
        dumped = ast.dump(arm)
        # ① fence 交回 None
        clears = [a for a in arm.body if isinstance(a, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "fence" for t in a.targets)
                  and isinstance(a.value, ast.Constant) and a.value.value is None]
        assert clears, "except 臂没把 fence 交回 None —— 后面每一支都还在当它有闸"
        # ② candidate.remove() 真被 await（只 logger.debug 不算摘：那时钩子还挂着）
        removes = [n for n in ast.walk(arm)
                   if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "attr", None) == "remove"]
        assert removes, ("except 臂没有 await …remove()：install 是在监听器已挂上之后才抛的，"
                         "不摘就是留一只半挂上的 Fetch 钩子（不回的那一笔把导航吊 20 秒）")

    def test_the_fence_has_a_none_before_the_try(self):
        """`fence = None` 必须住在 try 之前。

        少了它，`_allowed_hosts is None`（非 attach 的每一条通道）那次 `fetch` 走到
        `if fence is not None` 就是一次 NameError —— 而它会被外面那个通用兜底包成
        "一次失败的抓取"，看不出是接线断了。
        """
        func = _fetch_body()
        installs = list(_install_try(func))
        assert installs, "找不到 install 那处 try"
        install_line = installs[0][1].lineno
        priors = [n for n in ast.walk(func) if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == "fence" for t in n.targets)
                  and isinstance(n.value, ast.Constant) and n.value.value is None
                  and n.lineno < install_line]
        assert priors, ("install 之前没有 `fence = None` —— 那条 None 是这一支唯一的"
                        f"入口证据（install 在第 {install_line} 行）")

    def test_the_escape_race_and_the_teardown_both_guard_on_the_fence(self):
        func = _fetch_body()
        for name in ("_await_stable_or_escape",):
            calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == name]
            assert calls, f"fetch 里再没有 {name} 的调用 —— 那一支被摘掉了？"
            for call in calls:
                guarded = [n for n in ast.walk(func) if isinstance(n, ast.If)
                           and n.lineno <= call.lineno <= n.end_lineno
                           and "fence" in ast.dump(n.test)]
                assert guarded, f"{name} 的调用没被 `if fence …` 看着（第 {call.lineno} 行）"
        # 收尾那一次 remove 也只看 fence：None 的那一发已经没有可摘的东西
        removes = [n for n in ast.walk(func) if isinstance(n, ast.Await)
                   and isinstance(n.value, ast.Call)
                   and getattr(n.value.func, "attr", None) == "remove"
                   and getattr(n.value.func, "value", None) is not None
                   and getattr(n.value.func.value, "id", None) == "fence"]
        assert removes, "收尾没有 await fence.remove() —— 闸会活过这一页"


class TestAHalfInstalledFenceRemovesCleanly:
    """`Fetch.enable` 抛的那一刻，session 与监听器都已经在了 —— 摘必须真摘。"""

    class _Session:
        def __init__(self, *, fail_enable: bool = True, fail_disable: bool = False):
            self.sent: list[str] = []
            self.events: list = []
            self._fail_enable = fail_enable
            self._fail_disable = fail_disable

        async def send(self, method, params=None):
            self.sent.append(method)
            if method == "Fetch.enable" and self._fail_enable:
                raise RuntimeError("Fetch.enable refused by this Chrome build")
            if method == "Fetch.disable" and self._fail_disable:
                raise RuntimeError("TargetClosedError: page already closed")
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "ROOT"}}}
            return {}

        def on(self, event, handler):
            self.events.append(event)

    class _Context:
        def __init__(self, session):
            self._session = session

        async def new_cdp_session(self, _page):
            return self._session

    class _Page:
        def __init__(self, session):
            self.context = TestAHalfInstalledFenceRemovesCleanly._Context(session)

    def _fence(self, *, fail_disable: bool = False):
        session = self._Session(fail_disable=fail_disable)
        fence = _DocumentFence(self._Page(session), frozenset({"in.test"}), [])
        return fence, session

    @pytest.mark.asyncio
    async def test_install_raises_after_the_listener_is_already_attached(self):
        """失败的形状是"钩子挂上了、开关没开成"，不是"什么都没发生"。"""
        fence, session = self._fence()
        with pytest.raises(RuntimeError, match="Fetch.enable"):
            await fence.install()
        assert session.events == ["Fetch.requestPaused"], session.events
        assert "Page.getFrameTree" in session.sent

    @pytest.mark.asyncio
    async def test_remove_after_a_failed_install_does_not_raise(self):
        fence, session = self._fence()
        with pytest.raises(RuntimeError):
            await fence.install()
        await fence.remove()                      # 必须安静
        assert fence._session is None, "摘完还留着 session —— 后面那笔事件仍会去应答它"
        assert "Fetch.disable" in session.sent

    @pytest.mark.asyncio
    async def test_remove_is_silent_when_the_page_already_closed(self):
        """摘不成也不许冒出去：那一发已经输了，回执该说拒绝，不该说 traceback。"""
        fence, _session = self._fence(fail_disable=True)
        with pytest.raises(RuntimeError):
            await fence.install()
        await fence.remove()
        assert fence._session is None

    @pytest.mark.asyncio
    async def test_a_late_event_after_removal_answers_nothing_and_does_not_raise(self):
        fence, session = self._fence()
        with pytest.raises(RuntimeError):
            await fence.install()
        await fence.remove()
        before = list(session.sent)
        await fence._resolve({"requestId": 7, "frameId": "ROOT",
                              "request": {"url": "https://out.test/next"}})
        assert session.sent == before, "摘掉之后还对着死 session 发号 —— 那是一条 traceback"
        assert fence._watch == [], "摘掉的闸不许再把这一笔记进围栏账上"

    @pytest.mark.asyncio
    async def test_remove_without_an_installed_session_is_a_no_op(self):
        fence, session = self._fence()
        await fence.remove()
        assert session.sent == []


class TestTheRefusalSaysWhichSideItIs:
    """两支措辞互不越界 —— 这是"装不上"那条退路对用户唯一可见的形状。"""

    def test_the_request_boundary_says_it_never_left(self):
        err = _fence_blocked_error(["out.test"], frozenset({"in.test"}),
                                   left_browser=False)
        text = str(err)
        assert "ended before it left the browser" in text
        assert "already left" not in text, "请求边界那句不许顺手承认第二件事"

    def test_the_result_boundary_admits_the_request_went_out(self):
        err = _fence_blocked_error(["out.test"], frozenset({"in.test"}),
                                   left_browser=True)
        text = str(err)
        assert "already left the browser" in text
        assert "ended before it left" not in text

    def test_both_branches_still_name_the_way_out(self):
        for left in (True, False):
            text = str(_fence_blocked_error(["out.test"], frozenset({"in.test"}),
                                             left_browser=left))
            assert "confirm=true" in text, left
            assert "cdp_host_not_allowed:" in text, left

    def test_the_landed_check_is_the_one_the_no_fence_path_falls_back_on(self):
        """退路仍然拦得住内容：落地名册外那一支读的是 landed，与 fence 在不在无关。"""
        assert _landed_host_outside_fence("https://out.test/x", frozenset({"in.test"})) \
            == "out.test"
        assert _landed_host_outside_fence("https://in.test/x", frozenset({"in.test"})) == ""
