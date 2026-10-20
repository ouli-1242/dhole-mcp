"""点名的 `timeout=` 生效成多少，回执里要看得见 —— 没掐到的那一半也要有一张收据。

现场（2026-10-14 真实使用模拟 · P02-08 傅霖（公益组织项目官员 L1）· S5/S5B，P3）：

    web_read(url='…/newsDetail_forward_30790229', timeout=1200) → 238ms 完成
    web_read(url=同一颗, timeout=250, cache_ttl=0)          → 125ms 完成

两发都顺利，两发的字段集合里都**没有** `timeout_applied` —— 不是 0、不是空串，是这个键
缺席。按那颗字段自己的定义（"present ONLY when this tier's own per-attempt ceiling cut
it"）缺席是对的：没有帽被咬。于是傅霖分不清两件不同的事：「我点的 250ms 生效了，而这台
主机就是快」与「我点的那颗数根本没进这次调度的算术」。他想打一次超时（R01-010 那一发），
结果主机太快打不出来，而回执对这一点一字不提。

机制（测量而不是推测，见本文件每一条断言的取值方式）：一次尝试的预算只有一个作者 ——
`bulk_get` 里那行 `max(1, min(int(timeout), 30))`（`test_every_clamp_is_announced.py:216`
钉着"算式只有一份"，而 `test_a_timeout_bigger_than_the_per_attempt_cap_says_what_it_bought.py`
钉着那行的形状）。`web_read` 的 HTTP 一档经 `get()` 走的正是这一条路，所以**每一发拨过号
的调用**手里都有那颗数，只是它从来没有被抄到信封上：被帽咬的那一格抄了（`timeout_applied`），
没咬的那一格什么都不是。

本条与三处相邻而不重复：
  * R01-042 立的是「被掐掉那层的事实要上回执」—— 那一半早就有 `timeout_applied` 了；
    本刀是它的**补集**，旧字段自己的口径（只有被砍才写）一个字不改。
  * R02-012（本轮组 B 的另一发）是同一族在**爬取**那一侧的形状，字段同名同义。
  * W1（点名的头看不见）：都是"参数进了代码、没进回执"。

契约（本文件钉住）：
  * 一次 HTTP 尝试真的跑过 → `attempt_cap_ms` = 那一发**实际**拿到的 ms，与假 session
    收到的那颗数逐字相同（比较的是同一个值，不是任何一句措辞）。
  * 帽咬下来时**两**颗都在且同值：`attempt_cap_ms` 与 `timeout_applied`。
  * 没被咬的那一格照样发数 —— 这一颗答的是"一次尝试被给了多少"，确认本身就是收据，
    与 C5 那条"与你自己传的一模一样的数是噪声"不冲突：那条管的是"被砍"这个判决。
  * `bulk_get` 那一路多行共用一次算式，抄同一个数，不许每行再算一遍。
  * 写这颗数的地方**只有一处**（真拨号的那一档）。缓存回放与浏览器档不经过它，于是
    "这一发没有 HTTP 尝试"结构上只能是缺席，不可能是旧数冒充新数。
  * 亚秒的点名额会被本档的 1s 下界**抬**宽（`_http_tier_budget` 的 `max(1.0, …)`）：
    回执从此看得见那次抬宽（250 → 1000）。把抬宽说成一句散文是另一件事，另立 R02-039。
"""

from __future__ import annotations

import asyncio
import http.server
import inspect
import pathlib
import re
import socketserver
import threading

import pytest

from dhole_mcp import wire
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

PROBE = "http://dhole-r02-31-probe.invalid/"
PROBE2 = "http://dhole-r02-31-probe-b.invalid/"

PAGE = (b"<html><head><title>Budget</title></head><body><article>"
        b"<h1>What the receipt should say about the clock</h1>"
        + b"<p>A named budget that never reaches the response is a parameter the "
        b"caller cannot audit.</p>" * 4
        + b"</article></body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *" + chr(10).encode() + b"Allow: /")
            return
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


class _RecordingSession:
    """假 HTTPSession：记下这一档**实际**拿到的一试次预算，然后照挂死的样子失败。

    一个 `bulk_get` 只造一个 session，逐次拨号在那一颗上各记一笔（`gets`）—— 本文件既
    要比"信封上的数 == 实施里的数"，也要比"多行是不是共用同一次算式"。

    失败而不是成功，因为本文件比的是"发出去的数"与"实施的那个数"；伪造一整个 primp
    应答只会把用例变成对 `_translate_response` 的重复测试。
    """

    seen: list[dict] = []

    def __init__(self, **kwargs):
        _RecordingSession.seen.append({"ctor": kwargs.get("timeout")})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kwargs):
        _RecordingSession.seen[-1].setdefault("gets", []).append(
            kwargs.get("timeout"))
        raise RuntimeError("timed out reading " + url)


@pytest.fixture
def sessions(monkeypatch):
    import dhole_mcp.fetcher as fetcher_mod

    _RecordingSession.seen = []
    # `bulk_get` 在函数体里 `from dhole_mcp.fetcher import HTTPSession`，所以摘的是源头
    # 那颗名字 —— server 的模块命名空间里根本没有它（摘错地方的话桩根本不生效，而用例
    # 会去拨真网络）。
    monkeypatch.setattr(fetcher_mod, "HTTPSession", _RecordingSession)
    return _RecordingSession.seen


# ─── 1 · 收据与实施的是同一个数，两种格子都要在场 ───


class TestTheFigureIsTheOneTheTierUsed:
    def test_a_budget_that_was_never_cut_still_ships_its_number(self, sessions):
        """现场那一格（顺利的一发）：没有帽被咬，回执里也得有那颗数。"""
        bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=20, retries=0))

        row = bulk.results[0]
        assert sessions, "假的 session 没有被构造，这条用例没在量它声称量的事"
        assert row.attempt_cap_ms == 20_000, row.attempt_cap_ms
        # 比较的是同一个值：假 session 收到的就是实施里的那一颗
        assert row.attempt_cap_ms == sessions[0]["ctor"] * 1000, sessions
        # 旧字段自己的口径一字不改：没咬就不出现
        assert row.timeout_applied == 0, row.timeout_applied

    def test_the_ceiling_writes_both_numbers_and_they_agree(self, sessions):
        """帽咬下来那一格：两颗数同一个值，读者不必自己算。"""
        bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

        row = bulk.results[0]
        assert row.timeout_applied == 30_000, row.timeout_applied
        assert row.attempt_cap_ms == 30_000, (
            f"同一颗帽两个数：{row.attempt_cap_ms} vs {row.timeout_applied}")
        assert row.attempt_cap_ms == sessions[0]["ctor"] * 1000, sessions

    def test_a_failed_attempt_still_carries_the_receipt(self, sessions):
        """最需要这颗数的一发，正是没有正文的那一发（异常分支自己造信封）。"""
        bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=20, retries=0))

        row = bulk.results[0]
        assert row.status == 0 and not row.content, row
        assert row.attempt_cap_ms == 20_000, row.attempt_cap_ms

    def test_bulk_rows_share_the_one_figure(self, sessions):
        """算式在调用层算一次（一个 session），行只抄 —— 不许每行再算一遍。"""
        bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE, PROBE2], timeout=20,
                                                      retries=0))

        caps = {row.attempt_cap_ms for row in bulk.results}
        assert caps == {20_000}, caps
        assert len(sessions) == 1, f"一次 bulk 构造了多个 session：{sessions}"
        assert sessions[0]["gets"] == [20, 20], sessions
        assert bulk.results[0].attempt_cap_ms == sessions[0]["ctor"] * 1000, sessions


# ─── 2 · 这颗数只有一个作者，所以"没拨号"只能是缺席 ───


class TestOnlyTheTierThatDialsWritesIt:
    def _writers(self) -> list:
        root = pathlib.Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"
        out = []
        for path in sorted(root.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), 1):
                if re.search(r"\w\.attempt_cap_ms\s*=(?!=)", line):
                    out.append((path.name, lineno, line.strip()))
        return out
        return out

    def test_the_number_has_exactly_one_author(self):
        """写这颗数的地方全仓库只有一处，且就在真拨号的那一档里。

        反面锁不用构造：缓存回放与浏览器档不经过 `bulk_get`，所以它们**没有能力**写这颗
        数 —— "这一发没有 HTTP 尝试"于是结构上只能是缺席，而不是一个旧数冒充这一次的
        时钟（R4 那颗 `fetched_at` 同一族）。把这一行抄进 `_finalize_result` 或缓存出口
        的那一刻，这里红。
        """
        writers = self._writers()
        assert len(writers) == 1, f"一颗数两处家（漂移的开始）：{writers}"
        module, _, _ = writers[0]
        assert module == "server.py", writers
        src = inspect.getsource(MasterFetchServer.bulk_get)
        assert "attempt_cap_ms" in src, "唯一的那处不在拨号的那一档里"

    def test_absent_when_no_attempt_ran(self):
        """0 = 没话可说，所以不许上线（手册对这颗的承诺就是"0/absent"）。"""
        row = ResponseModel(url=PROBE, status=200, content=["body"],
                            cached=True, fetcher_used="cache")
        assert row.attempt_cap_ms == 0
        assert "attempt_cap_ms" not in wire._wire_dump(row), (
            "默认值上线了：压实表里没这颗")

    def test_the_figure_ships_when_it_carries_information(self):
        row = ResponseModel(url=PROBE, status=200, content=["body"],
                            attempt_cap_ms=20_000)
        assert wire._wire_dump(row)["attempt_cap_ms"] == 20_000


# ─── 3 · 端到端：顺利的那一发带着它自己那颗数回去 ───


@pytest.mark.asyncio
async def test_the_web_read_that_succeeded_names_what_it_was_given(origin):
    """R02-031 的现场形状：200、有正文、没有帽被咬 —— 而那颗数在场。"""
    r = await MasterFetchServer().web_read(origin + "/a", timeout=1000, cache_ttl=0)

    assert r.status == 200 and r.content, r.error
    assert r.attempt_cap_ms == 1000, (
        f"点名的 1000ms 没有落到回执上：{r.attempt_cap_ms}")
    assert r.timeout_applied == 0, r.timeout_applied


@pytest.mark.asyncio
async def test_a_sub_second_timeout_shows_the_floor_it_became(origin):
    """点名的比本档的 1s 下界还小 —— 回执报的是**实施**的那颗，不是人传的那颗。

    `timeout=250` 进来的是 250，一次尝试实际被给了 1000（`_http_tier_budget` 的
    `max(1.0, …)` 把它抬宽了）。这一刀只保证"看得见"；把这次抬宽说成一句散文是另一件
    事，另立 R02-039 —— 在这里替它说话会把两条缺陷修成一条，而把另一条藏进 commit。
    """
    r = await MasterFetchServer().web_read(origin + "/b", timeout=250, cache_ttl=0)

    assert r.attempt_cap_ms == 1000, (
        f"回执说不出一次尝试实际被给了多少：{r.attempt_cap_ms}")
    assert r.timeout_applied == 0, (
        f"一次下界抬宽被冒充成了上限砍除：{r.timeout_applied}")
