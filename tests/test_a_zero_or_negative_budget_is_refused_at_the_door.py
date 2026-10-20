# -*- coding: utf-8 -*-
"""一颗预算要么是预算，要么得被拒 —— 不许把「0」说成「没点」（2026-10-18 · R02-F102）

现场（`review/.runtime/p1/r209_deadline_radius.json` · 真机 7 发 · B-56 的收口）：

  * `web_crawl(deadline_ms=0)` 走到循环里被零预算掐掉，回的是 `truncated_by_time: true`
    与「stopped: time deadline」，而申报整趟预算的 `deadline_ms_applied` **整颗缺席** ——
    该字段的说明写的是「0/absent = 这一发从没进到抓取循环」。同一封回执里两句话互相反驳。
    作者有两处：`wire.py` 的压实名单（这颗「等于默认值就省」，而默认值正是 0）与
    `crawl.py:2459` 那句 `if applied else "no deadline you named"`。
  * `deadline_ms=-1` 照收并原样申报 -1；`999999`（默认帽 25000 的 40 倍）不拒不夹 ——
    而同一族的另一颗 `timeout=` 早就在门口拒超帽/非正数/不是数字（R02-093 / R02-096）。

契约（本文件钉住）：
  * **非正数在门口拒**，信封与 `timeout=` 那一族同一副脸：`error` 以 `invalid_request:` 开头、
    点名 `deadline_ms`、`summary` 与 `next_action` 都在、行表为空且 `pages_crawled` 0、
    一颗包都不发。（不要求 `content_ok` —— 那一格住在抓取信封上，这扇门的模型里没有它。）
  * 拒的那句**不许**教「抬高点再试」之外的假出路：0 与负数不是一种预算，说「给个大点的数」可以，
    说「这一发跑了 4ms 后被时钟砍掉」不行。
  * 正数是合法预算：`deadline_ms=1` 照旧进循环、照旧被时间砍，申报字段**在场**且等于点名的值。
  * 进了循环的那一发，`deadline_ms_applied` 必须在场且 > 0 —— 这就是「缺席 = 没进循环」那句
    契约重新成立的那一格（本刀改的是可达性，不是那句话）。
  * 这颗**没有上限**，并且申报出来：`999999` 照收、`deadline_ms_applied` 就是 999999。
    （与 `timeout=` 的物理不同：那一颗帽住一次 HTTP 尝试会挂住这台机器，这一颗只决定整轮
    什么时候停，而 `max_pages` / `attempt_cap_ms` 各自还在管。取舍写在 `review/decisions.md`。）
  * 半径锁：`deadline_ms` 只有 `web_crawl` 从线上收；`web_discover` 递这颗仍按**陌生参数**拒
    （名单见 `server.py:4262-4264`），将来谁把它接进 discover，这里会红。
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from dhole_mcp.server import MasterFetchServer

PAGE = (b"<html><head><title>Fixture</title></head><body><p>plain fixture page</p>"
        b"</body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def _answer(self):
        _Handler.hits.append(self.path)
        body = PAGE
        if self.path.endswith("robots.txt"):
            body = b"User-agent: *\nAllow: /"
        self.send_response(200)
        self.send_header("content-type", "text/" + ("plain" if "robots" in self.path
                                                    else "html"))
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def origin():
    _Handler.hits = []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/page"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)
    yield
    identity.set_value("allow_private", False)


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


#: 零与负数不是一种预算 —— 一整族，按值取形状，不为个案开支
NON_POSITIVE = [0, -1, -86_400_000]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", NON_POSITIVE)
async def test_a_non_positive_deadline_is_refused_before_anything_is_fetched(
        bad, origin):
    """门口拒：点名这颗、三格齐全、一颗包都不发。

    这一族从前**全被接受**：0 与 -1 都进了循环，被零/负预算当场砍掉，交回
    「stopped: time deadline」，而申报那颗预算的字段在 0 那一发缺席。
    """
    mark = len(_Handler.hits)
    payload = await _call("web_crawl", {"url": origin, "max_pages": 2,
                                        "deadline_ms": bad})
    error = str(payload.get("error") or "")
    assert error.startswith("invalid_request"), (
        f"deadline_ms={bad} 没被门口拒: {error[:160]}")
    assert "deadline_ms" in error, f"那句拒绝没点名是哪颗参数: {error}"
    assert payload.get("summary"), payload
    assert payload.get("next_action"), f"拒绝只说了哪错了，没说出路: {error}"
    # 不要求 `content_ok`：那一格住在**抓取信封**上，而这扇门交回的是 `CrawlResponseModel`，
    # 它没有那颗字段（R02-115/F97 量的正是"每扇门有自己的形状"，替它发明一格才是假话）。
    # 这一族里"什么都没交付"的形状是空行表 + 零计数 —— 与紧邻的 `timeout=` 那一支同一副脸。
    assert payload.get("pages") == [], payload
    assert payload.get("pages_crawled") == 0, payload
    assert len(_Handler.hits) == mark, "一句预算拒绝却把包发出去了"


@pytest.mark.asyncio
async def test_the_refusal_does_not_narrate_a_run_that_never_happened(origin):
    """拒的那句不许顺口描述一次它自己拦掉的运行。

    修之前这一支的 summary 是「crawled 0 page(s) … stopped: time deadline」—— 一句关于
    一次**根本没开始**的爬取的描述。修之后它要说的是这颗值不是预算（正数才是）。
    """
    payload = await _call("web_crawl", {"url": origin, "deadline_ms": 0})
    text = json.dumps(payload, ensure_ascii=False)
    for talk in ("stopped: time deadline", "crawled 0 page",
                 "truncated_by_time"):
        assert talk not in text, f"拒绝里在描述一次它自己拦掉的运行（{talk}）: {text[:220]}"
    assert "no deadline you named" not in text, "第二处作者（crawl.py:2459）那句假话"


@pytest.mark.asyncio
@pytest.mark.parametrize("good", [1, 5_000])
async def test_a_positive_deadline_is_still_a_budget_and_is_declared(good, origin):
    """正数是合法预算：照旧进循环，申报字段在场且等于点名的值。

    这一格是**反向锁**：修法若图省事把「不给」与「0」一起处理（比如把整颗钳成默认 25000），
    这里就红 —— 申报的是这一发真被held住的那个数，不是默认值。
    """
    payload = await _call("web_crawl", {"url": origin, "max_pages": 1,
                                        "deadline_ms": good})
    assert not str(payload.get("error") or "").startswith("invalid_request"), payload
    if good == 1:
        assert payload.get("deadline_ms_applied") == 1, (
            "零预算那格修好之后，1ms 这一格必须仍然申报自己受哪个数管 "
            f"（拿到的是 {payload.get('deadline_ms_applied')!r}）")
        assert payload.get("truncated_by_time") is True, payload
    else:
        assert payload.get("deadline_ms_applied") == good, payload


@pytest.mark.asyncio
async def test_this_budget_has_no_ceiling_and_says_which_number_it_used(origin):
    """这颗**不设帽**，但要说清用的是你点的那个名（与 `timeout=` 的物理不同）。

    上限归上限的判据：`deadline_ms` 只决定整轮什么时候停，`max_pages` / `attempt_cap_ms`
    各自还在帽住行数与单发。这一格钉的是「别顺手给它造一顶帽」—— 造帽是改判据，不是修文案。
    """
    payload = await _call("web_crawl", {"url": origin, "max_pages": 1,
                                        "deadline_ms": 999_999})
    assert not str(payload.get("error") or "").startswith("invalid_request"), (
        f"给这颗新造了帽，而判据说没有: {payload.get('error')}")
    assert payload.get("deadline_ms_applied") == 999_999, payload


@pytest.mark.asyncio
async def test_a_run_that_reached_the_loop_declares_its_budget(origin):
    """那句契约重新成立：进了循环的，`deadline_ms_applied` 一定在场且 > 0。

    压实名单里这颗「等于默认值（0）就省」，于是真的 0 永远表达不出来 —— 本刀把 0 挡在门外之后，
    在场与否就重新等于「有没有进循环」。这一格按**在场**断言，不靠缺席。
    """
    payload = await _call("web_crawl", {"url": origin, "max_pages": 1})
    assert payload.get("deadline_ms_applied", 0) > 0, (
        "跑进了循环却没申报预算: " + json.dumps(payload, ensure_ascii=False)[:220])


@pytest.mark.asyncio
async def test_the_sibling_budget_and_this_one_wear_the_same_face(origin):
    """同族两颗预算同一副信封（R02-120 的落点）。

    不是要求同一句话 —— `timeout=` 有帽、这颗没有 —— 而是要求**同为门口拒**：
    `error` 都以 `invalid_request:` 开头、都点名自己、都三格齐全、都不发包。
    """
    got = {}
    for arg, value in (("timeout", 0), ("deadline_ms", 0)):
        payload = await _call("web_crawl", {"url": origin, arg: value})
        got[arg] = payload
        error = str(payload.get("error") or "")
        assert error.startswith("invalid_request"), f"{arg}=0 从兜底裸逃: {error[:160]}"
        assert arg in error, f"{arg} 的拒绝没点名自己: {error}"
        assert payload.get("summary") and payload.get("next_action"), payload
    assert got["timeout"]["error"] != got["deadline_ms"]["error"], (
        "两颗预算共用一句出路 = 又把哪一颗写错了抹掉了")


@pytest.mark.asyncio
async def test_discover_still_does_not_take_this_knob(origin):
    """半径锁：这颗只住 `web_crawl`，`web_discover` 递它仍按陌生参数拒。

    入账时我猜错过一次半径（以为那张 `_SC_PARAMS` 表也透给 discover），实测名单在
    `server.py:4262-4264`。这一格把实测钉住：将来谁把这颗接进 discover，这里先红，
    那时再来改判据 —— 而不是让它今天靠猜成立。
    """
    payload = await _call("web_discover", {"url": origin, "deadline_ms": 0})
    error = str(payload.get("error") or "")
    assert error, f"web_discover 收了它名单外的这颗: {json.dumps(payload)[:200]}"
