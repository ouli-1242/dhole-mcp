"""被拒的 session_id 不许在原地留下任何一次调用的状态（2026-10-08 验收 · D-02 的回归锁）

现场（修复前）：`session_id` 的形状校验发生在**这张 tokens 表之后** ——
`_SESSION_ID` / `_CACHE_CTX` / `_ARG_NOTES` 等等已经 set 进去了，然后那条路直接
return，`finally` 里的 reset 一次都没跑到。于是被拒的这一次把状态留给了同一上下文里
的下一次调用：缓存指纹是上一发的、notes 列表是上一发的。本轮把校验挪到了任何
ContextVar 写入之前（`server.py` 的闸门在 tokens 表上面），并留了一句
`AssertionError` 探针 —— 谁把闸门挪回去，正路上会立刻撞它。

这条回归锁钉的是**残留**本身，因为那句探针只在"非法名字仍然跑了一次抓取"时才响，
而移动闸门的人未必走那条形状。断言很朴素：一次被拒的调用之后，同上下文的 ContextVar
必须还停在它原来的位置；下一次调用不该看见上一次的任何东西。

反向锁也在这里：合法的名字照样要写进 ContextVar（抓取那一档真的看见它），并且在一发
结束后被 reset 回去 —— 把闸门改成"再也不写"同样能让上面的断言全绿，那却是另一件事。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.context import _ARG_NOTES, _CACHE_CTX, _SESSION_ID
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

URL = "https://jar.example.com/page"
BAD = "a session name with spaces"


class _Tiers:
    """HTTP 档的记录器：不发网络，只看那一发进来时上下文里有什么。

    `get` 必须是**未绑定**的函数存进类属性（第一个参数是 MasterFetchServer 自己）——
    批量那条路会按签名把 kwargs 往参数上绑，绑好的方法会把 url 吃掉。
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def install(self, monkeypatch) -> "_Tiers":
        recorder = self

        async def get(self, url, **kwargs):
            recorder.seen.append((url, _SESSION_ID.get()))
            return ResponseModel(url=url, status=200, content=["<p>page</p>"],
                                 content_type="text/html", fetcher_used="http",
                                 total_extracted_chars=4)

        monkeypatch.setattr(MasterFetchServer, "get", get)
        return recorder


@pytest.fixture()
def tiers(monkeypatch):
    # robots 那一档也会发包，本锁不测它。
    monkeypatch.setattr("dhole_mcp.robots.check_robots_allow",
                        lambda *a, **k: (True, ""), raising=False)
    return _Tiers().install(monkeypatch)


class TestTheRefusalWritesNothing:

    @pytest.mark.asyncio
    async def test_a_refused_session_id_leaves_the_jar_name_unset(self, tiers):
        before = (_SESSION_ID.get(), _CACHE_CTX.get(), _ARG_NOTES.get())
        r = await MasterFetchServer().web_read(url=URL, session_id=BAD, cache_ttl=0)
        assert r.status == 0 and "session_id" in r.error, (r.status, r.error)
        assert "Nothing was fetched" in r.next_action, r.next_action
        assert tiers.seen == [], "被拒的一次还是发了一发"
        after = (_SESSION_ID.get(), _CACHE_CTX.get(), _ARG_NOTES.get())
        assert after[0] == before[0] == "", after
        assert after[1] == before[1], "_CACHE_CTX 带着上一发的指纹进了下一次"
        assert after[2] == before[2], "_ARG_NOTES 还是上一发那只列表"

    @pytest.mark.asyncio
    async def test_the_next_call_in_the_same_context_sees_nothing_of_it(self, tiers):
        """残留只有在**下一次调用**上才算：那一发必须是干净的匿名一发。"""
        server = MasterFetchServer()
        await server.web_read(url=URL, session_id=BAD, cache_ttl=0)
        r2 = await server.web_read(url=URL, cache_ttl=0)
        assert r2.status == 200, r2.error
        assert not getattr(r2, "session_id", ""), r2.session_id
        assert tiers.seen[-1] == (URL, ""), f"上一发拒绝的名字还挂在上下文里: {tiers.seen}"

    @pytest.mark.asyncio
    async def test_a_batch_refuses_the_whole_call_not_one_row(self, tiers):
        """一颗坏名字不能让批量里的别行偷偷跑起来（同一个 id 覆盖整批）。

        两个位子上都要拦：分发层的白名单（`_ARG_NOTES` 之前、连一次 ContextVar 都不
        写），以及 `_with_smart_fetch_options` 那枚闸门。只留一处，另一处就会有人挪。
        """
        r = await MasterFetchServer()._dispatch(
            "web_read", {"urls": [URL, "https://jar.example.com/other"],
                         "session_id": BAD, "cache_ttl": 0})
        assert tiers.seen == [], tiers.seen
        payload = json.loads(r[0][0].text)
        rows = payload.get("results") or [payload]
        assert rows, payload
        assert all("session_id" in (row.get("error") or "") for row in rows), rows


class TestALegalNameStillReachesTheTier:
    """反向锁：闸门挪在前面不等于名字不再起作用。"""

    @pytest.mark.asyncio
    async def test_a_valid_name_is_visible_inside_the_fetch_and_reset_after(self, tiers):
        await MasterFetchServer().web_read(url=URL, session_id="alice-jar_1", cache_ttl=0)
        assert tiers.seen == [(URL, "alice-jar_1")], tiers.seen
        assert _SESSION_ID.get() == "", "写完没 reset：下一发会顶着别人的登录态跑"

    @pytest.mark.asyncio
    async def test_the_shape_that_is_allowed_unchanged_still_answers(self, tiers):
        """没给名字 = 匿名，一次都不许多余拒绝（这条门不能变成新的默认拦路）。"""
        r = await MasterFetchServer().web_read(url=URL, cache_ttl=0)
        assert r.status == 200, r.error
        assert tiers.seen == [(URL, "")], tiers.seen

    @pytest.mark.asyncio
    async def test_a_64_character_name_is_still_a_name(self, tiers):
        long = "a" * 64
        r = await MasterFetchServer().web_read(url=URL, session_id=long, cache_ttl=0)
        assert r.status == 200, r.error
        assert tiers.seen[-1] == (URL, long), tiers.seen
