# -*- coding: utf-8 -*-
"""写的那一扇门不许回答「只是没抓到东西」（R02-084 · P3）。

真机现场（`review/.runtime/p1/r87_resend.json` 的 B1/B2 · `r86_web_request.json` 的 W11）：
`web_request` 上四条 headers= 拒绝里，两条的结尾是

    … half a header set is a request you did not ask for. Nothing was fetched.
    … cred_defaults set={auth: {type: 'bearer', token: '…'}, …}. Nothing was fetched.

另两条在同样的门上说的是

    … Nothing was sent.

同一扇门对"这一发有没有付出去"两种口径（class B：句子出自 dhole 源码，读的是 dhole 的字段，
与仪器无关；靶站侧只用来确认一发没发 —— `fixture_rows` 与 `tcp_accepts` 都是 0）。

为什么这不是措辞洁癖：`web_request` 对外的承诺是 "A write is sent ONCE: not retried (an
earlier call may already have committed)"。被门拦下时，调用方必须先确定**服务器有没有收到过
这一发**才敢改一头再打。"Nothing was fetched" 在写的那一扇上还可能有第二种读法："请求到了
对面，只是响应没抓回来" —— 那正好是他最不该在此刻相信的话。

而分岔的规矩这个文件早就有：`_header_door` 的 user-agent 支与条件头支都先问 `_knob_here(...)`
再决定说什么（注释写着「对它说抬 deadline_ms 等于把调用方支到一个它够不着的旋钮上」是同一条
规矩的另一种写法）。这一把不是发明规矩，是把漏下的两处补上、并把六个作者并成一个。
"""

from __future__ import annotations

import asyncio
import re

import pytest

from dhole_mcp.server import MasterFetchServer

WRITE_URL = "https://write-door.invalid/"
READ_URL = "https://read-door.invalid/"

SENT = "Nothing was sent."
FETCHED = "Nothing was fetched."

#: 四条设计边界各取一颗会撞上的头名（凭据 / UA / 条件头 / 内部管理的头）。
HEADER_CASES = [
    ("cookie", {"Cookie": "sid=abc"}),
    ("user-agent", {"User-Agent": "MyOwn/1.0"}),
    ("if-none-match", {"If-None-Match": '"v1"'}),
    ("host", {"Host": "elsewhere.invalid"}),
]


def _call(tool: str, method: str, headers: dict) -> str:
    """走真门（`_dispatch`）拿 `next_action`，不直接调内部函数：判据要读调用方读得到的那一份。

    地址都是 `.invalid`（RFC 2606 保留，永远连不上）—— 这几格赌的是"门在付费之前就拦"，
    所以最后一格（`host`）撞的是 `validate_headers` 那一支也要在发出任何东西之前返回。
    """
    srv = MasterFetchServer()
    args = {"url": WRITE_URL if tool == "web_request" else READ_URL,
            "headers": headers}
    if tool == "web_request":
        args["method"] = method
        args["confirm"] = True
    out = asyncio.run(srv._dispatch(tool, args))
    payload = out[0][0].text
    import json
    try:
        return (json.loads(payload).get("next_action") or "") + " " + payload
    except Exception:
        return payload


class TestTheWriteDoorSaysSent:

    @pytest.mark.parametrize("name,headers", HEADER_CASES)
    def test_every_header_refusal_on_the_write_door_says_sent(self, name, headers):
        text = _call("web_request", "HEAD", headers)
        assert SENT in text, f"{name} 在写门上没说自己没发出去：{text[:300]}"
        assert FETCHED not in text, (
            f"{name} 在写门上的口径是「没抓到」，而这一扇门的调用方要问的是"
            f"「有没有付过一次请求」：{text[:300]}")


class TestTheReadDoorKeepsItsOwnWord:
    """反面锁：不许为了统一把读的门也说成 sent —— 那一扇可能从缓存答题，压根没发过请求。"""

    @pytest.mark.parametrize("name,headers", HEADER_CASES)
    def test_every_header_refusal_on_the_read_door_says_fetched(self, name, headers):
        text = _call("web_read", "", headers)
        assert FETCHED in text, f"{name} 在读门上不再说 fetched：{text[:300]}"
        assert SENT not in text, (
            f"读的那一扇会说 sent 就等于宣称这一发非付出去不可，"
            f"而它能从缓存答题：{text[:300]}")


class TestTheRuleHasOneAuthor:

    def test_the_two_spellings_come_from_one_author(self):
        """拒绝链上不许有第二个手抄句子口径的地方：正反两面都由同一个助手交回。

        这一格第一版写的是「整份 server.py 里 `"Nothing was sent."` 手抄不许超过两处」——
        量到 3 就以为是漏分岔。读一下那三处才知道判据套错了对象：其中两处是**别条写路径**
        自己的句子（confirm 门与凭据回落，本来就该说 sent），一处是这个助手自己的 docstring
        在引用被修的那句原话。规矩管的是 `headers=` 这条拒绝链，不是整份文件。
        """
        import inspect

        from dhole_mcp import server as server_mod
        from dhole_mcp.server import _nothing_went_out

        for fn in (server_mod._header_door, server_mod._caller_headers):
            src = inspect.getsource(fn)
            for spelling in (SENT, FETCHED):
                assert spelling not in src, (
                    f"{fn.__name__} 里手抄了句子口径 {spelling!r} —— 分岔该只有一位作者")
            assert "_nothing_went_out()" in src, (
                f"{fn.__name__} 没从助手取词，它交出的那句会跟着门乱")
        assert _nothing_went_out() in (SENT, FETCHED), _nothing_went_out()

    def test_a_write_door_never_leaves_a_fetched_word_in_a_header_refusal(self):
        """一般式：把四族头名在写门上各打一遍，回执里出现的每一句「什么都没发生」都得是 sent。"""
        variants = [SENT, FETCHED, "Nothing was fetched or changed."]
        for _name, headers in HEADER_CASES:
            text = _call("web_request", "POST", headers)
            present = [v for v in variants if v in text]
            assert present in ([SENT], []), f"写门上的申报口径 {present}：{text[:240]}"

    def test_the_promise_that_makes_this_a_correctness_matter(self):
        """这一扇门外写着 sent ONCE —— 所以"没发生什么"这句话必须按它的动词说。"""
        srv = MasterFetchServer()
        doc = srv.__doc__ or ""
        found = re.search(r"sent ONCE", doc)
        if not found:  # 承诺也可能住在工具描述里；两处任一处算数
            import inspect
            from dhole_mcp import server as server_mod
            src = inspect.getsource(server_mod)
            assert "sent ONCE" in src or "not retried" in src, "承诺的名字变了，判据要跟着改"
        else:
            assert found.group(0)
