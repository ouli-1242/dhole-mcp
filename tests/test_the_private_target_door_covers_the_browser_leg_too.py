# -*- coding: utf-8 -*-
"""R02-077 · 私网摘头那道门从前只管一条腿：浏览器那一发照旧发一枚编造的 google referer。

立案的形状（`sessions/P02-R5-F63-post.jsonl`，本轮在同一只 echo 上重量，见
`review/.runtime/p4/r77-shape.json`）：同一个私网 URL 两发 ——
探活那一发 14 颗、`referer` 无（HTTP 腿，门在它身上兑现），真 Chromium 导航那一发 **17 颗、
`referer: https://www.google.com/`**（浏览器腿）。摘头那条规则的理由写在 `server.py` 里：
「打回环或内网时没有那道墙，它们只落进你自己的服务日志」—— 这句话对浏览器那一发同样成立，
而它当初只写在 HTTP 腿的参数合流处。

两只手各读一颗旋钮（决定里写清了它们不是同一位的两名）：HTTP 腿读档位表第五位
（`stealthy_headers`），浏览器腿读 `google_search`。修法不是合并两颗旋钮（那会改公网的字节），
而是**在两条腿上各问同一把尺**：`security.target_is_private`。

一发量形把"17 颗"拆成三种作者，门只管第一种：
① dhole 打出来的 —— 只有那枚编造的 google `referer`；
② 那台浏览器自己发的 —— UA / `sec-ch-ua*` / `sec-fetch-*` / `accept-*`，摘掉它们只会让这一发
   更像机器人（不在这一条门里管）；
③ 作者还没归上名的 —— `cache-control` / `pragma: no-cache`：`src/` 里没有、playwright 与
   patchright 的 Python 层里也没有，形状与"驱动层把缓存关掉"一致。本刀不动它们，留在案上，
   防止下一轮把"17 颗都该摘"当成结论。

还有一条不许弯：调用方**点名**给的那颗 referer 照旧到线上（实测 L3 就是点名的那颗），
这一门摘的从来只是 dhole 自己编的那一份。申报位与 HTTP 腿共用同一个（`_HEADERS_OFF_BY`），
因为"少了什么、为什么少"必须读得到 —— 静默改变调用方点名的东西是这一族一直在修的罪。
"""
from __future__ import annotations

import inspect

import pytest

from dhole_mcp import browser as BR
from dhole_mcp import context as CT
from dhole_mcp import server as SR

LOOPBACK = "http://127.0.0.1:8400/echo"
LOCALHOST = "http://localhost:8400/echo"
PUBLIC = "https://docs.example.org/page"
GOOGLE = "https://www.google.com/"


@pytest.fixture(autouse=True)
def clean_flag():
    CT._HEADERS_OFF_BY.set("")
    yield
    CT._HEADERS_OFF_BY.set("")


def _ask(url, google_search=True, headers=None):
    return BR._invented_referer(url, google_search, headers or {})


class TestTheDoorNowCoversTheBrowserLeg:
    def test_a_loopback_target_gets_no_invented_referer(self):
        assert _ask(LOOPBACK) is None
        assert CT._HEADERS_OFF_BY.get() == "private_referer", "摘了却没说被谁摘的"

    def test_a_loopback_named_as_a_host_is_the_same_target(self):
        """`127.0.0.1` 与 `localhost` 是同一道门的两块牌子，不许只认字面 IP。"""
        assert _ask(LOCALHOST) is None
        assert CT._HEADERS_OFF_BY.get() == "private_referer"

    def test_a_public_target_still_gets_the_referer_it_always_sent(self):
        """公网那一条逐字节不变：这一刀只往私网那一支加判句。"""
        assert _ask(PUBLIC) == GOOGLE
        assert CT._HEADERS_OFF_BY.get() == ""

    def test_a_referer_you_named_is_yours_and_nothing_was_dropped(self):
        got = _ask(LOOPBACK, headers={"referer": "http://127.0.0.1:8399/about"})
        assert got is None, "点名的那一颗由下游发，这里既不该重复也不该覆盖"
        assert CT._HEADERS_OFF_BY.get() == "", "调用方自己的头没被摘，不许申报成摘过"

    def test_google_search_off_is_off_on_both_targets(self):
        assert _ask(LOOPBACK, google_search=False) is None
        assert _ask(PUBLIC, google_search=False) is None

    def test_the_ipv6_loopback_is_the_same_target_too(self):
        """`::1` 与 `127.0.0.1` 是同一块本机的两块牌子：那一发照样只落进用户自己的日志。"""
        assert _ask("http://[::1]:8400/echo") is None
        assert CT._HEADERS_OFF_BY.get() == "private_referer"


class TestOneAuthorOneReading:
    def test_the_fetch_reads_the_single_decision(self):
        """浏览器腿不再有第二处把 google 那串字面量敲进去。"""
        src = inspect.getsource(BR.BrowserSession.fetch)
        assert "_invented_referer(" in src, "那一枚 referer 又回到 fetch 里现编了"
        assert '"https://www.google.com/"' not in src, src[:400]

    def test_the_predicate_is_the_same_one_the_http_leg_uses(self):
        """两条腿读同一把尺：这一门问的与 `server._target_is_private` 是同一个函数。"""
        src = inspect.getsource(BR._invented_referer)
        assert "target_is_private" in src, src
        from dhole_mcp.security import target_is_private
        door = SR.MasterFetchServer._target_is_private
        assert all(door(u) == target_is_private(u) for u in
                   (LOOPBACK, LOCALHOST, PUBLIC, "http://[::1]:8400/", "http://10.0.0.5/"))


class TestEachLegSaysItsOwnSentence:
    """两句话各说各的腿。合成一句就会出现"关于一台没发过的请求的断言"那种谎。"""

    def test_the_http_leg_still_says_what_it_always_said(self):
        note = SR._headers_off_note("private")
        assert "no fabricated google referer" in note and "no generated UA" in note, note
        assert "browser" not in note, f"HTTP 那句不该提另一条腿：{note}"

    def test_the_browser_leg_names_the_one_thing_it_withheld(self):
        note = SR._headers_off_note("private_referer")
        assert "browser tier navigated without" in note, note
        assert "loopback/private" in note, note

    def test_the_browser_note_does_not_claim_no_ua(self):
        """那一发的 UA 出自画像档，是它自己发的；写"没发 UA"就是谎。"""
        note = SR._headers_off_note("private_referer")
        assert "no generated UA" not in note, note
        assert "sec-ch-ua" in note, note

    def test_the_browser_note_survives_a_replay_in_the_past_tense(self):
        note = SR._headers_off_note("private_referer", replay=True)
        assert "the fetch that stored this answer navigated without" in note, note
        assert "This replay sent no request" in note, note

    def test_the_off_rung_still_reads_as_the_off_rung(self):
        assert "anti_detect='off'" in SR._headers_off_note("off"), SR._headers_off_note("off")


class TestTheAnnouncementReachesTheReply:
    """摘了不说就是静默改变，而这句话在一份回执里只许出现一次。

    作者搬到门本身（`context._note_referer_off`）：往 `_ARG_NOTES` 那一份**列表**里 append 是
    改对象，浏览器那一层即便跑在复制过的上下文里也回得到回执；只 `_HEADERS_OFF_BY.set(...)`
    那种"换个值"的写法会留在那个子上下文里 —— 修前一发真调用就是这么哑的（`sessions/P02-R5-F71-post.jsonl`
    的第一趟：referer 没了，summary 里一个字都没提）。
    """

    def _notes(self):
        notes: list = []
        return notes, CT._ARG_NOTES.set(notes)

    def test_the_door_stands_up_and_says_it_in_one_motion(self):
        notes, tok = self._notes()
        try:
            got = _ask(LOOPBACK)
        finally:
            CT._ARG_NOTES.reset(tok)
        assert got is None
        assert CT._HEADERS_OFF_BY.get() == "private_referer"
        assert notes == [CT.REFERER_OFF_NOTE], notes

    def test_it_is_said_once_across_retries(self):
        notes, tok = self._notes()
        try:
            _ask(LOOPBACK)
            _ask(LOOPBACK)
        finally:
            CT._ARG_NOTES.reset(tok)
        assert notes == [CT.REFERER_OFF_NOTE], notes

    def test_a_public_call_says_nothing_about_being_let_off(self):
        notes, tok = self._notes()
        try:
            assert _ask(PUBLIC) == GOOGLE
        finally:
            CT._ARG_NOTES.reset(tok)
        assert notes == [], notes

    def test_the_replay_uses_the_same_sentence_author(self):
        """一句少发的话若有两个作者，回放那一发迟早会说成另一件事。"""
        assert SR._headers_off_note("private_referer") == CT.REFERER_OFF_NOTE
