"""推荐一棵子树之前，先看这棵子树是不是**已经把整条队列罩住了**。

现场（2026-10-14 真实使用模拟 · P02-02 邝野（游戏叙事 L3）· S4 一百二十项列表页扇出 · R02-008 · P2 · 组 C）：

    web_crawl(url=<站点>, path_include=['/work'], max_pages=1)
      → 早停，next_action：「… or scope it with path_include=['/work'] — that subtree
        holds **111 of the 111** URLs still queued …」

调用方这一发**已经点了** `path_include=['/work']`，回执把同一个动作再推荐一次，而且那句话
自己承认"这一棵子树装着整条队列"（111 of 111）—— 选了它等于什么都没选。假不在数字（数字是
对的），在**行动性**：它是照着做也不会有任何变化的一条建议。

`_scope_hint`（R3刀3 立的判据）当时治的是另一头：样板无脑写 `/docs`，站点没有 /docs 就是一条
修不了任何东西的建议。它的门槛是"一段至少占 3 条、且过半"。**过半**在有余量时是对的门，在
`n == 全部` 时却刚好放行那条最没用的建议 —— 因为此时候选子树不是"砍一刀"，而是"保持现状"。

这一把不动那三条件里的任何一条（不许放宽门禁），只把它补全：候选必须**真选出一些东西**，
也就是 `n < len(pending)`。副作用是同一条假话在调用方**没**点 `path_include` 时也不许出现
（全站只有一棵子树时，"scope it with /work" 同样是空动作）—— 那是同一种病，不是特例。

契约（本文件钉住）：
  * 队列整个落在一段里 → 不推荐任何 `path_include=`；
  * 队列里确有少数派（6 of 8）→ 照旧点名多数派与它的份额（R3刀3 的既有契约一字不改）；
  * 句子永远不许自证空动作：`holds N of the N` 这种形状在 src 与 wire 上都归零；
  * 出路被撤掉时**不许把整个 next_action 撤空**：抬预算那一条（`max_pages` / `deadline_ms`）
    必须还在，调用方拿到的是一条真能改变结果的话；
  * 判决位一位不动：`pages_discovered` / `pages_crawled` / 截断申报都照旧 —— 本刀修的是那句
    建议，不是那次数账。
  * 装配点不许替作者起头：连接词由 `_scope_hint` 自己带（推荐是"or scope it with …"，事实句
    是"and note that …"），两处 break 用同一种接法。这一条是真机复验里抓到的第二伤 ——
    判据改对了，出路却被通用的 `", or scope it with {hint}"` 装配成断句。
"""

from __future__ import annotations

import http.server
import re
import socketserver
import threading

import pytest

from dhole_mcp.crawl import _scope_hint
from dhole_mcp.server import MasterFetchServer

NEWLINE = chr(10).encode()
# 整棵队列只有一段子树：`n == len(pending)`，正是"选了等于没选"那一格。
WORK_LINKS = "".join(f'<a href="/work/post-{i}">p{i}</a>' for i in range(1, 7))
PROSE = ("<p>A page about embedding search results and the tradeoff between waiting "
         "for a quorum and answering early from a thin pool.</p>")

MIXED = ([f"https://s.test/blog/post-{i}" for i in range(6)]
         + ["https://s.test/about", "https://s.test/contact"])


def _saturated():
    return [f"https://s.test/work/post-{i}" for i in range(6)]


def _page() -> bytes:
    body = ("<html><head><title>Index</title></head><body><article>"
            "<h1>Work posts</h1>" + PROSE * 16 + WORK_LINKS
            + "</article></body></html>")
    return body.encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *" + NEWLINE + b"Allow: /")
            return
        self.send_response(200)
        self.send_header("content-type", "text/html")
        body = _page()
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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


# ─── 1 · 判据本身 ───


class TestTheLeverMustMoveSomething:

    def test_a_queue_inside_one_subtree_is_not_offered_that_subtree(self):
        """111 of 111 那一格：候选装着整条队列，推荐它等于推荐"保持现状"。

        不许出现的是**那条动作**（`path_include=...`），不是这句话本身 —— 整条队列都在
        /work 里是一件调用方看得见的实事，报出来比沉默有用（上一把 R02-F10 立的规矩：
        一把杠杆不在这里，要说是为什么）。
        """
        hint = _scope_hint(_saturated())
        assert "path_include=" not in hint, hint
        assert "/work" in hint and "cannot select a subset" in hint, hint

    def test_the_sentence_never_reports_the_whole_queue(self):
        """守卫打在句子形状上：'holds N of the N' 是自证空动作，归零。"""
        for queue in (_saturated(), MIXED,
                      [f"https://s.test/work/{i}" for i in range(3)]
                      + ["https://s.test/news/a"]):
            hint = _scope_hint(queue)
            assert not re.search(r"holds (\d+) of the \1", hint), (queue[:2], hint)

    def test_a_real_minority_is_still_left_alone(self):
        """无候选可推时仍然空手（R3刀3 的另一半：不许编一棵不存在的子树）。"""
        queue = [f"https://s.test/{n}" for n in ("about", "pricing", "careers", "press")]
        assert _scope_hint(queue) == ""

    def test_the_majority_case_keeps_its_contract(self):
        """反面锁：真有少数派时那句照旧——份额、数字、子树名一位不许缩。"""
        hint = _scope_hint(MIXED)
        assert "path_include=['/blog']" in hint, hint
        assert "6 of the 8" in hint, hint


# ─── 2 · 线上那一发（台账现场） ───


@pytest.mark.asyncio
async def test_a_caller_who_already_named_the_subtree_is_not_told_to_name_it(origin):
    r = await MasterFetchServer().web_crawl(
        url=origin + "/", path_include=["/work"], max_pages=1, cache_ttl=0)
    na = r.next_action or ""
    assert r.pages_crawled == 1 and r.pages_discovered > 1, (
        f"这一发本该早停，量到的账：{r.pages_crawled}/{r.pages_discovered}")
    assert "path_include=" not in na, (
        f"调用方这一发已经点了 /work，出路把同一个动作再推荐一次：{na!r}")
    assert na.strip(), "撤掉空动作建议时不许把整条出路撤空"
    assert "max_pages" in na or "deadline_ms" in na, (
        f"出路必须留一条真能改变结果的话：{na!r}")


@pytest.mark.asyncio
async def test_the_same_no_op_is_not_recommended_to_an_unscoped_caller(origin):
    """同一种病：全站只有一棵子树时，'scope it with /work' 同样选不出任何东西。"""
    r = await MasterFetchServer().web_crawl(url=origin + "/", max_pages=1, cache_ttl=0)
    na = r.next_action or ""
    assert "path_include=" not in na, na
    assert "holds" not in na, na
    assert r.pages_discovered > r.pages_crawled, (r.pages_discovered, r.pages_crawled)


# ─── 3 · 装配点不许替作者起头（真机量到的那条断句） ───


def test_the_clause_carries_its_own_lead_in():
    """连接词跟着作者走：推荐接得住"or scope it with"，事实句自己说自己的。

    这是真机复验里抓到的第二伤：出路被装配成
    "…deadline_ms, or scope it with every URL still queued is under /work…" ——
    判据改对了，句子却被一处通用的 `", or scope it with {hint}"` 说坏了。
    单测只比子串看不见这种伤（与 R02-F10 那把同一个教训）。
    """
    from dhole_mcp.crawl import _scope_hint as hint

    rec = hint(MIXED)
    fact = hint(_saturated())
    assert rec.startswith("or scope it with "), rec
    assert fact.startswith("and note that "), fact
    for clause in (rec, fact):
        assert not clause.startswith("every URL"), clause
        assert " or scope it with every URL" not in (", " + clause)


def test_the_two_break_sites_share_one_connector():
    """字符预算与页数/时间两处从前各起各的头（"or scope the run with" /
    "or scope it with"）—— 同一句话两个连接词也是同一把尺子该并的账。"""
    import inspect

    src = inspect.getsource(__import__("dhole_mcp.crawl", fromlist=["crawl"]))
    assert "or scope the run with" not in src
    assert src.count('f", {scope_hint}"') == 2, "两处装配点必须同一种接法"


@pytest.mark.asyncio
async def test_the_character_budget_branch_reads_whole_too(origin):
    """字符预算那一支拿到的同一条事实句，不许被接成断句。

    页要**够重**这一支才打得上：轻页总是先撞上 max_pages，两条 break 走的是同一句
    装配的两个分支（从前各起各的连接词），所以这里必须真把字符帽压下来。
    """
    r = await MasterFetchServer().web_crawl(
        url=origin + "/", path_include=["/work"], max_pages=8,
        max_total_chars=1000, cache_ttl=0)
    na = r.next_action or ""
    assert "character budget" in na, (na[:200], r.summary)
    assert "or scope" not in na, na
    assert "with every URL" not in na, na
    if "cannot select a subset" in na:
        assert "and note that every URL" in na, na
