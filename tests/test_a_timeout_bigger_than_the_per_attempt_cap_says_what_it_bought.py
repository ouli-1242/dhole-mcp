"""`timeout=` 说的是一份**整次调用**的毫秒预算，而 HTTP 一档钳的是**每次尝试**的秒 ——
这颗参数是唯一一处"声明的范围与实施的单位不是一回事"的钳，而一句提示做不了算术。

现场（15 场景真人体测，`timeout=120000` 的一份在线 PDF）：每次尝试实际只有 30s，而文档
只有 1 次尝试（重试会把下载从第 0 字节重启，见 `_http_tier_budget` 的 document 分支），
所以那 120s 从来没有属于过一次连接。`summary` 当时确实把这句话说了
（"timeout clamped 120->30 s per attempt"），而说了话在这一批里是**及格线**不是答案：

* 一句话做不了算术。调用方要判的是"这一发实际有多少毫秒可用、还能有几试"，那需要两个
  数（每一试次的预算 + `retry_count`），不是一个从句。
* 那句话只活在 `summary` 里，而爬取那条路不组装它（`web_crawl` 的行是 `CrawlPage`，不
  经过 `_with_agent_hints`）—— 同一颗钳在两颗工具上一颗有落点、一颗没有，正是
  `test_every_clamp_is_announced.py` 第七节收掉的形状。
* 更要紧的是**建议**也跟着落空：`_document_budget_advice` 在预算已经大于 30s 时还劝
  "raise timeout"，而那颗杠杆对一次下载能做的早在 30s 就用完了。建议不许推荐一件不会
  改变结果的事（R6 / D4 / R7a 立的同一条款）。

于是这一刀把同一件事实送到它该去的三个落点：**钳住的那一档自己发一个数**
（`timeout_applied`，毫秒/每试次，只在钳真的咬了的时候上线）、**常驻层在人抬 `timeout=`
之前就把上限说出口**（`web_read` / `web_request` / `web_crawl` 的 `timeout` 描述）、
**手册解释这个数怎么用**（`references/fields.md` 与 troubleshooting 的 Fetch timeouts）。

顺手量到的另一颗同族缺陷（不是本次计划里的条目，实测之后一并收）：`feed_read` 的
`timeout=` 在 wire 上写的是毫秒，而它下传的 `fetch_feed` / `HTTPSession` 那一层吃的是
**秒**（`fetcher.py:715`），于是 `timeout=30000` 在 primp 那里是 30000 秒 —— 一次挂死的
取回没有任何时钟，而调用方以为自己点的正是最短的那一档。

三条边界，都是刻意的：

1. **算式仍然只有一个作者**：`bulk_get` 里那行 `max(1, min(int(timeout), 30))`，由
   `test_every_clamp_is_announced.py:216` 钉着，本刀一个字没动。新的
   `_HTTP_ATTEMPT_CAP_MS` 是给"说话的地方"用的读数，不是第二处算式 —— 所以它必须与那行
   算式同值，这条对齐由本文件量（数有两个家而没有测试，就是漂移的开始）。
2. **上限本身不改**：抬不抬得动 30s 是一次抓取行为的决定（一次挂死的连接能吃掉多少整轮
   预算需要另一次测量）。本刀只让人**看得见**它。
3. **钳没咬就不发数**：发一个与调用方自己传的一模一样的数是噪声不是收据，而 `wire.py`
   的压实机制删默认值正是为了这一件事。
"""

from __future__ import annotations

import asyncio
import inspect
import re

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp import wire
from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _budget_advice, _over_budget_result
from dhole_mcp.security import TIMEOUT_MAX_MS, validate_timeout
from dhole_mcp.server import (
    _HTTP_ATTEMPT_CAP_MS,
    _document_budget_advice,
    _feed_timeout_s,
    MasterFetchServer,
)

PROBE = "http://dhole-c5-probe.invalid/"


class _RecordingSession:
    """假 HTTPSession：记下这一档**实际**拿到的一试次预算，然后照挂死的样子失败。

    失败而不是成功，因为本文件量的两件事都在失败那一边：收据最需要发给那一发真的没拨通
    的调用（一次超时最需要"你给的其实是 30s"），而伪造一整个 primp 应答会把用例变成对
    `_translate_response` 的重复测试。
    """

    seen: list[dict] = []

    def __init__(self, **kwargs):
        _RecordingSession.seen.append({"ctor": kwargs.get("timeout")})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kwargs):
        _RecordingSession.seen[-1]["get"] = kwargs.get("timeout")
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


def _timeout_schema(tool_name: str) -> str:
    tool = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == tool_name)
    return tool["inputSchema"]["properties"]["timeout"]["description"]


def _cap_in_the_formula() -> int:
    """`bulk_get` 那行算式里的秒数 —— 全仓库唯一的一试次上限**作者**。"""
    src = inspect.getsource(MasterFetchServer.bulk_get)
    m = re.search(r"max\(1, min\(int\(timeout\), (\d+)\)\)", src)
    assert m, "算式换了形状：这条对齐守卫要跟着换，而不是放宽"
    return int(m.group(1))


# ─── 1 · 收据：发出去的数必须就是实施了的那个数 ──────────────────

def test_the_receipt_is_the_figure_the_tier_actually_used(sessions):
    """"钳到 30s"不许只留在散文里，而它发出去的数不许与实施的不是同一个。

    反向：把行上的 `timeout_applied` 抄成 `timeout`（调用方传的那颗）而不是
    `_per_attempt_s`，这一条立刻红 —— 它比的是两处**同一个值**，不是任何一句措辞。
    """
    bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

    assert sessions, "假的 session 没有被构造，这条用例没在量它声称量的事"
    assert {s["ctor"] for s in sessions} == {30}, sessions
    assert {s["get"] for s in sessions} == {30}, sessions
    assert bulk.results[0].timeout_applied == _HTTP_ATTEMPT_CAP_MS


def test_a_failed_attempt_still_carries_the_receipt(sessions):
    """最需要这颗数的一发，正是没有正文的那一发。

    `bulk_get` 的异常分支自己构造一具 ResponseModel（不走 `_translate_response`），所以
    "抄到每一行"必须在那一支也做得到；抄漏了它，收据就只在成功时上线，而失败才是调用方
    要决定"还抬不抬 timeout"的那一刻。
    """
    bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

    row = bulk.results[0]
    assert row.status == 0 and not row.content, row
    assert row.timeout_applied == _HTTP_ATTEMPT_CAP_MS


def test_the_sentence_and_the_number_describe_the_same_clamp(sessions):
    """双保险不许各说一套：`summary` 那句话与那颗字段说的是同一次钳制。

    这句话只活在 `summary` 里，而 `summary` 由 `_ARG_NOTES` 的桶组装（一次调用一个桶，
    见 `test_every_clamp_is_announced.py` 第六节），所以这里要自己把桶开起来 —— 真实
    调用里桶由 `_dispatch` 开。
    """
    with _arg_notes_scope():
        bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=120, retries=0))

    row = bulk.results[0]
    assert "timeout clamped 120->30 s per attempt" in row.summary, row.summary
    assert row.timeout_applied == 30 * 1000, row.timeout_applied


def test_a_budget_that_was_not_cut_pays_no_receipt(sessions):
    """没咬就不许发：一个与调用方自己传的一模一样的数是噪声。"""
    bulk = asyncio.run(MasterFetchServer.bulk_get([PROBE], timeout=20, retries=0))

    assert bulk.results[0].timeout_applied == 0
    assert sessions[0]["ctor"] == 20, sessions
    payload = wire._wire_dump(bulk.results[0])
    assert "timeout_applied" not in payload, (
        f"默认值上线了（压实表里没这颗？）: {sorted(payload)}")


def test_no_http_attempt_means_no_said_figure():
    """这颗只描述 HTTP 一档的一试次；别的来源没有这个时钟。

    缓存回放的信封里根本没有这一列（`cache.py` 存的是白名单字段），浏览器档不经过
    `bulk_get` —— 两处都留默认值。这条钉的是"缺席=这一发没有 HTTP 尝试被钳"，否则一次
    旧回放的数值可以冒充这一次调用的钳制（与 R4 那颗 `fetched_at` 同一族）。
    """
    row = ResponseModel(url="https://cached.example/", status=200,
                        content=["body"], cached=True, fetcher_used="cache")
    assert row.timeout_applied == 0
    assert "timeout_applied" not in wire._wire_dump(row)


# ─── 2 · 一个数的几个家必须同值 ─────────────────────────────────

def test_the_number_the_prose_names_is_the_number_the_formula_uses():
    """三处读数（算式 / 那颗常量 / 常驻层与字段描述里的句子）必须同值。

    `test_every_clamp_is_announced.py:216` 钉死了算式只有一份；本条钉的是"说话的地方"
    引用同一个数时不许各抄一个版本。把 30 改成 45 而忘了改别处，这里红。
    """
    cap_s = _cap_in_the_formula()
    assert _HTTP_ATTEMPT_CAP_MS == cap_s * 1000, (
        f"算式钳 {cap_s}s，说话的地方用 {_HTTP_ATTEMPT_CAP_MS}ms")

    said_it = []
    for tool in MasterFetchServer._TOOL_DEFS:
        schema = (tool["inputSchema"].get("properties") or {}).get("timeout")
        if not schema or "attempt" not in schema["description"]:
            continue
        said_it.append(tool["name"])
        assert f"caps at {cap_s}s" in schema["description"], (tool["name"], schema)
    assert said_it == ["web_read", "web_request", "web_crawl"], said_it

    field_text = ResponseModel.model_fields["timeout_applied"].description
    assert f"{_HTTP_ATTEMPT_CAP_MS}ms" in field_text, field_text


def test_a_description_promising_a_longer_download_names_the_attempt_cap_too():
    """"max 120000" 加一句"下载"就是一个抬上去也够不着的承诺。

    这条是本刀的形状守卫：一颗 `timeout` 描述既承诺得比一试次上限更多、又在这颗工具上
    谈到下载，就必须把上限一起说完。搬家时删掉那半句（或给 web_request 加上 "download"
    却忘了上限），这里红。
    """
    cap_s = _cap_in_the_formula()
    checked = []
    for tool in MasterFetchServer._TOOL_DEFS:
        schema = (tool["inputSchema"].get("properties") or {}).get("timeout")
        if not schema:
            continue
        text = schema["description"]
        promised = re.search(r"max (\d{5,})", text)
        if not promised or "download" not in text.lower():
            continue
        checked.append(tool["name"])
        assert int(promised.group(1)) / 1000 <= cap_s or "attempt" in text, (
            f"{tool['name']} 的 `timeout` 承诺了 {promised.group(1)}ms 的下载，"
            "却没有说一次尝试到不了那么多")
    assert checked, "没有一颗描述既承诺过大预算又谈到下载：这条守卫没在量任何东西"


def test_crawls_timeout_stops_calling_itself_a_whole_call_budget():
    """同一张表里两句互相矛盾过：`timeout` 自述"whole-call"，`deadline_ms` 说它是每页。

    单位与口径都要说的是**页**，否则抬 `timeout=` 的人以为自己抬的是整轮的上司。
    """
    text = _timeout_schema("web_crawl")
    assert text.lower().startswith("per-page"), text
    assert "whole-call" not in text.lower(), text
    assert "deadline_ms" in text, text


def test_the_feed_default_the_wire_prints_is_the_default_the_code_uses():
    """`feed_read` 括号里印的默认值必须就是签名与换算函数共同的那个数。"""
    default_ms = _feed_timeout_s(None) * 1000
    assert default_ms == 20_000, default_ms
    assert f"default {default_ms}" in _timeout_schema("feed_read"), _timeout_schema("feed_read")


# ─── 3 · 建议不许推荐一件不会改变结果的事 ────────────────────────

def test_a_document_past_the_attempt_cap_is_not_told_to_raise_timeout():
    advice = _document_budget_advice("https://d.test/a.pdf", None, "", 120_000,
                                     "HTTP tier")
    assert "raise timeout" not in advice, advice
    assert f"{_HTTP_ATTEMPT_CAP_MS // 1000}s" in advice, advice
    # 出路还在：换源，或把文件取到本地再交给人解析 —— 两句都要说得出。
    assert "switch source" in advice and "parse" in advice, advice


def test_a_document_under_the_attempt_cap_is_still_told_the_lever_works():
    """另一半：12s 预算里抬 `timeout=` 是真有效的（上限还没碰到），别把有用的话删了。

    建议给的数不许超过一次尝试真正拿得到的那个 —— 否则这句话本身就成了下一颗
    "抬上去也够不着"的承诺。
    """
    advice = _document_budget_advice("https://d.test/a.pdf", None, "", 12_000,
                                     "HTTP tier")
    assert "raise timeout" in advice, advice
    suggested = int(re.search(r"timeout=(\d+)", advice).group(1))
    assert suggested <= _HTTP_ATTEMPT_CAP_MS, advice
    assert f"caps at {_HTTP_ATTEMPT_CAP_MS // 1000}s" in advice, advice


def test_the_generic_advice_never_pitches_the_number_already_in_play():
    """通用那一句（非文档路径）从前只有一个写死的例子：`Raise timeout (e.g.
    timeout=60000)` —— 而它不看调用方已经给了多少。

    现场（15 场景体测，场景 10）：`timeout=60000` 的一发元素截图跑了 61,529ms 被掐，
    建议劝的就是"抬到 60000"。一句劝人去做他刚刚做过的事的话不是建议，是让人怀疑自己
    没读懂参数（实测之后那一格没有再试，直接换了路）。
    """
    advice = _budget_advice("actions (stealthy) tier", "", 60_000)
    assert "timeout=60000" not in advice, advice
    assert "already had 60000ms" in advice, advice
    assert "120000" in advice, advice


def test_the_advice_shows_the_room_rather_than_a_second_guess_at_the_ceiling():
    """剩下的余量直接算出来给人看，人就不必去猜 dhole 还收不收得下。"""
    advice = _budget_advice("actions (stealthy) tier", "", 60_000)
    assert f"{TIMEOUT_MAX_MS - 60_000}ms more" in advice, advice


def test_a_budget_at_the_ceiling_is_told_there_is_nothing_left_to_raise():
    """另一半：已经碰到门的那一发，"再抬抬"是一句做不到的话。"""
    advice = _budget_advice("HTTP tier", "", TIMEOUT_MAX_MS)
    assert "Raise" not in advice, advice
    assert "nothing left to raise" in advice, advice


def test_a_default_sized_budget_still_hears_the_verb():
    """反面守卫：25000（actions 那一档的默认）时候 "Raise timeout" 必须还在 ——
    这一刀是把谎撤掉，不是把一句有用的话删了。"""
    advice = _budget_advice("actions (stealthy) tier", "", 25_000)
    assert advice.startswith("Raise timeout"), advice


def test_the_built_over_budget_answer_carries_what_was_asked():
    """整条线：`_over_budget_result` 传下去的就是它自己那发用的预算，不是别的常数。"""
    r = _over_budget_result("http://127.0.0.1:8399/captcha", 60_000, 61_529,
                            "actions (stealthy) tier", "")
    assert "already had 60000ms" in r.next_action, r.next_action
    assert "timeout=60000" not in r.next_action, r.next_action


def test_the_ceiling_the_advice_names_is_the_ceiling_the_door_enforces():
    """一个数的几个家必须同值：门口拒的那颗、建议里念的那颗、常驻层写的那句。

    与 `_HTTP_ATTEMPT_CAP_MS` 那条对齐守卫同一个理由 —— 数有两处家而没有测试，
    就是漂移的开始。
    """
    from dhole_mcp.security import SecurityError

    assert validate_timeout(TIMEOUT_MAX_MS) == TIMEOUT_MAX_MS
    with pytest.raises(SecurityError, match=str(TIMEOUT_MAX_MS)):
        validate_timeout(TIMEOUT_MAX_MS + 1)
    # 常驻层那颗 `timeout` 描述的口径（"max 120000"）必须与门一致
    named = [t["name"] for t in MasterFetchServer._TOOL_DEFS
             if (t["inputSchema"].get("properties") or {}).get("timeout")
             and f"max {TIMEOUT_MAX_MS}" in
             t["inputSchema"]["properties"]["timeout"]["description"]]
    assert named, "没有任何一颗 `timeout` 描述写着这道门：建议与门口各说一套"


def test_the_cap_and_the_receipt_are_explained_where_an_agent_will_look():
    """两颗话都上了常驻层，解释就必须住在 sys_guide 读得到的卷里。

    常驻层与语料各说一套是这一族反复付过的账（`test_wire_moves_land_in_the_manual`）。
    """
    from dhole_mcp import guide

    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    for needle in ("timeout_applied", "one HTTP attempt of at most 30s",
                   "one attempt never exceeds 30s"):
        assert needle in corpus, f"{needle!r} 不在语料里：只读手册的 agent 解释不了这颗数"


# ─── 4 · feed_read：同一颗参数在另一条通道上根本不换算 ────────────

def _feeds_captured(monkeypatch) -> list:
    seen: list = []

    async def fake_fetch_feeds(urls, timeout=20, max_items=20, since=""):
        seen.append(timeout)
        return []

    monkeypatch.setattr(feed_mod, "fetch_feeds", fake_fetch_feeds)
    return seen


def test_feed_read_sends_seconds_not_the_milliseconds_it_was_given(monkeypatch):
    """`timeout=30000`（wire 的口径 = 30 秒）从前原样下传，而那一层吃的是**秒**。

    于是 primp 拿到 30000 秒。这条按线路上下传的那颗参数真正的样子量（换算之后的秒）。
    """
    seen = _feeds_captured(monkeypatch)
    asyncio.run(MasterFetchServer().feed_read(urls=["https://d.test/rss.xml"],
                                              timeout=30_000))
    assert seen == [30], seen


def test_feed_read_omitted_timeout_keeps_the_wait_it_always_had(monkeypatch):
    """修单位不许顺手改掉一次真实的等待：不传时仍然等 20s。"""
    seen = _feeds_captured(monkeypatch)
    asyncio.run(MasterFetchServer().feed_read(urls=["https://d.test/rss.xml"]))
    assert seen == [20], seen


def test_a_feed_timeout_above_the_documented_ceiling_is_refused_by_name(monkeypatch):
    """拒必须具名：一颗越界的数不许换算成"更长的等待"然后照跑。"""
    called = _feeds_captured(monkeypatch)
    with pytest.raises(ValueError) as exc:
        asyncio.run(MasterFetchServer().feed_read(urls=["https://d.test/rss.xml"],
                                                  timeout=200_000))
    assert "120000" in str(exc.value), str(exc.value)
    assert not called, "拒绝之前已经发过取回了"


def test_the_feed_conversion_is_second_granularity_and_floors_at_one():
    """换算的颗粒是秒：亚秒的值进 1s，而不是变成 0（0 在那一层是"不设超时"）。"""
    assert _feed_timeout_s(30_000) == 30
    assert _feed_timeout_s(500) == 1
    assert _feed_timeout_s(120_000) == 120
    assert _feed_timeout_s(None) == 20
