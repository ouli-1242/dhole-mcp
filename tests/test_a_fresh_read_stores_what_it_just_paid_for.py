"""被迫取新的那一发读到的正文，不该在下一发普通读时被更旧的一份顶掉。

现场（2026-10-14 真实使用模拟 · P02-09 沈序（跨境电商风控运营 L2）· S4 变卦 + 第三遍对照）：

    19:24:03 的一份进了缓存
    19:26:47  web_read(url, cache_ttl=0)  → http、4,433ms、fetched_at=11:26:47  ← 刚给用户看过
    19:27:24  web_read(url)               → cache、5ms、fetched_at=**11:24:03** ← 更旧的一份
    +30s      web_read(url)               → 仍是 11:24:03

两发的回执各自都自洽（第二发确实说了"cached copy, fetched 2.8 min ago"），缺的是**新旧
关系**：用户刚花 4.4 秒取回一份新的，转头普通读拿到的却是那份旧的，而那些新字节被丢掉了。
他的原话：「`cache_ttl=0` 取回来的那份，为什么下一次普通读拿不到它。」

机制（读码定到一行）：`_finalize_result` 的写入门是 `if cache_ttl > 0 and _is_cacheable(...)`
—— 这颗参数同时管着两件不同的事：**读侧**「别看缓存」（这是 `0 = force fresh` 的全部含义）
与**写侧**「这一发不值得存」。前者是用户点名的，后者没有人点名过。`cache_ttl <= 0` 在
`_cache_hit_response` 那里已经单独把读侧办了，写侧这一半是顺带漏掉的。

但"顺带"里裹着两件**真的不该存**的东西，把它们一起放开就是新问题：
  * 一次动作链落地的那一页（`web_act` 在门口压 `cache_ttl=0`：动作之后的正文只存在于那条
    链之后，回放它等于回答一个链从没问过的问题）；
  * 一次**写**操作的应答（`web_request` 的 POST 那一支，同一颗 0：写的答案不许被下一次读
    端出来当公共正文）。
所以这一刀不是把 `cache_ttl > 0` 删掉，是**把两个问题分开问**：读侧照旧绕开缓存；写侧
由"这一次是不是普通读"决定，而决定权交给一个纯函数（`_store_ttl`），三个格子都能单独测。

契约（本文件钉住）：
  * 普通读 + `cache_ttl=0` → 取新**并且**按页型/默认 TTL 存下来（本刀的全部后果）。
  * 普通读 + `cache_ttl>0` → 存那颗 TTL（一字不改）。
  * 动作链的一页 → 永远不存（反面锁，不许被这一刀放开）。
  * 非 GET 的请求（`web_request` 的 POST 与 HEAD 探针）→ 永远不存（另一条反面锁）。
  * 壳页 / 错误页 / 空正文 → 永远不存（`_is_cacheable` 那道闸一个字节都不动）。
  * 读侧那一半不许被顺手改掉：`cache_ttl=0` 的那一发仍然必须真的拨号。
  * 负值的申报句子（`cache_ttl=-1 is not a duration; using 0 …`）不许再承诺一件已经
    不做的事 —— 话要跟着实施走。
"""

from __future__ import annotations

import http.server
import socketserver
import threading

import pytest

from dhole_mcp import cache as cache_mod
from dhole_mcp.cache import DEFAULT_TTL, get_cached
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import _store_ttl, MasterFetchServer

# ─── 本地靶：每次请求正文都不同（现场就是"两份谁新"的问题）───

_HITS = {"n": 0}

# 正文是中性题材（跨境仓配月报）。这件事本身要记一笔：靶页的文字里带"反爬/人机校验"
# 这类词时，`quality` 的墙启发式会把这一页判成 bot 墙并写下 `error` —— 而 `_is_cacheable`
# 读的就是 `error`，于是这一发**从来就不进缓存**（修前修后都一样），本文件要量的那件事
# 就再也量不到。第一版踩的就是这颗坑。
_PARAS = [
    "跨境仓配的结算周期是十四天，账期从包裹出库那天开始计算。",
    "汇率按出库当日的中间价折算，退款单独走一条对账队列。",
    "每个仓的库存周转天数不同，华东仓平均十一天，华南仓平均九天。",
    "退货率超过百分之四的品类要重新核一次毛利，核不过就下架。",
    "物流签收时间以承运商回传的扫描节点为准，不以买家点确认为准。",
    "月度对账单在次月五日前生成，差异要在三个工作日内提报。",
    "同一笔订单的多包裹合并按首包裹的出库时间归档，避免跨月。",
]


def _body() -> bytes:
    _HITS["n"] += 1
    n = _HITS["n"]
    paras = list(_PARAS)
    paras.insert(3, f"第 {n} 次快照：这一句每次请求都不同，用来分辨两份正文。")
    inner = "<h1>跨境仓配风控月报</h1>\n" + "\n".join(f"<p>{p}</p>" for p in paras)
    return ('<!doctype html><html lang="zh"><head><meta charset="utf-8">'
            f'<title>月报 {n}</title></head><body>{inner}</body></html>').encode("utf-8")


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.endswith("robots.txt"):
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"User-agent: *" + chr(10).encode() + b"Allow: /")
            return
        body = _body()
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def origin():
    _HITS["n"] = 0
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


def _clean(page_type: str = "other") -> ResponseModel:
    return ResponseModel(url="https://x.test/a", status=200,
                         content=["real prose worth caching " * 12],
                         content_ok=True, page_type=page_type)


# ─── 1 · 写入的那道门现在分两个问题问 ───


class TestTheStoreGateAnswersOneQuestionAtATime:
    def test_a_forced_fresh_read_is_worth_storing(self):
        """本刀的全部后果：读侧绕开缓存，不等于写侧丢掉这一发。"""
        assert _store_ttl(_clean(), 0) == DEFAULT_TTL

    def test_an_explicit_ttl_is_still_the_callers_own(self):
        assert _store_ttl(_clean(), 600) == 600

    def test_the_page_type_rule_applies_to_a_fresh_read_and_is_untouched_otherwise(
            self):
        """默认那一发仍然按页型抬到 6h —— 这一刀不许顺手改掉等待账。

        强制取新的那一发走的是同一条规则：它存的是同一份正文，凭什么比一次普通读更短命。
        """
        assert _store_ttl(_clean("article"), DEFAULT_TTL) == 21600
        assert _store_ttl(_clean("article"), 0) == 21600
        assert _store_ttl(_clean("list"), DEFAULT_TTL) == DEFAULT_TTL
        assert _store_ttl(_clean("article"), 600) == 600

    def test_an_action_chains_page_is_never_stored(self):
        """反面锁 I：链的正文只存在于那条链之后（`web_act` 在门口压的那颗 0 的真正理由）。"""
        assert _store_ttl(_clean(), 0, chain_ran=True) == 0
        assert _store_ttl(_clean(), 600, chain_ran=True) == 0

    def test_a_request_that_is_not_a_read_is_never_stored(self):
        """反面锁 II：`web_request` 的任何具名动词都不存 —— POST 的应答不是公共正文，
        HEAD 是一颗探针。旧代码里这件事也是 `cache_ttl=0` 顺带办的，而那颗门自己写着
        "this tool never sends a bare GET"，所以"非 GET 即不存"是它的语义而不是巧合。"""
        assert _store_ttl(_clean(), 0, non_read=True) == 0
        assert _store_ttl(_clean(), 600, non_read=True) == 0

    def test_the_substance_gate_still_refuses_what_it_always_refused(self):
        """闸不弱化：`_is_cacheable` 拒的三样（错误状态 / 带 error 判词 / 空正文）照旧拒。

        壳页与挑战页是被它们各自的 `error` 判词挡在这一层的（`_is_cacheable` 读的就是
        status / error / 正文三件），而 R01-028 把**动作链**那一支的判词从 `error` 里降级
        之后，链的"不存"就只剩 `_NO_STORE` 这一道 —— 那正是本刀要给它一个名字的理由，
        不是顺手多加的一颗旗子。
        """
        errored = ResponseModel(url="https://x.test/e", status=500,
                                content=["boom " * 20])
        assert _store_ttl(errored, 0) == 0
        wall = ResponseModel(url="https://x.test/w", status=200,
                             content=["solve this captcha to continue " * 8],
                             error="challenge_page")
        assert _store_ttl(wall, 0) == 0
        blank = ResponseModel(url="https://x.test/b", status=200, content=["   "])
        assert _store_ttl(blank, 0) == 0


# ─── 2 · 端到端：沈序那三发的顺序，在本地靶上重演 ───


@pytest.mark.asyncio
async def test_the_plain_read_after_a_fresh_read_shows_the_fresh_copy(origin):
    first = await MasterFetchServer().web_read(origin + "/report")
    fresh = await MasterFetchServer().web_read(origin + "/report", cache_ttl=0)
    third = await MasterFetchServer().web_read(origin + "/report")

    assert "第 1 次快照" in first.content[0], first.content[0][:120]
    assert "第 2 次快照" in fresh.content[0], fresh.content[0][:120]
    assert "第 2 次快照" in third.content[0], (
        f"普通读顶回了更旧的一份：{third.content[0][:120]}")
    assert third.fetcher_used == "cache", (
        f"修好之后第三发应当命中刚存下的那一行：{third.fetcher_used}")


@pytest.mark.asyncio
async def test_the_stored_row_ages_from_the_fresh_calls_instant(origin):
    """`fetched_at` 的账与正文的账必须说同一件事（R01-021 那一族的另一位作者）。

    比的是**磁盘上那一行**的抓取时刻，不是两次调用各自的字符串：live 那一发的 `fetched_at`
    是信封装配那一刻（写盘之后几毫秒），回放那一发读的是写盘那一刻 —— 两个值天生差几毫秒，
    而它们讲的是同一次抓取。要断言的正是"回放没有盖一个新的现在"。
    """
    from dhole_mcp.cache import fetched_at_iso

    await MasterFetchServer().web_read(origin + "/report")
    fresh = await MasterFetchServer().web_read(origin + "/report", cache_ttl=0)
    third = await MasterFetchServer().web_read(origin + "/report")

    row = await get_cached(origin + "/report", "markdown", None, ttl=DEFAULT_TTL, ctx="")
    assert row is not None, "第二发根本没写进缓存"
    assert third.fetched_at == fetched_at_iso(row), (third.fetched_at, row["fetched_at"])
    assert third.fetched_at <= fresh.fetched_at, (fresh.fetched_at, third.fetched_at)


@pytest.mark.asyncio
async def test_a_fresh_read_still_reads_nothing_from_the_cache(origin):
    """反面锁（读侧那一半不许被顺手改掉）：`cache_ttl=0` 仍然真的拨号。"""
    await MasterFetchServer().web_read(origin + "/report")
    fresh = await MasterFetchServer().web_read(origin + "/report", cache_ttl=0)

    assert _HITS["n"] == 2, f"第二发没有出网，读侧那一半被动了：{_HITS}"
    assert fresh.fetcher_used != "cache", fresh.fetcher_used
    assert "第 2 次快照" in fresh.content[0]


@pytest.mark.asyncio
async def test_a_fresh_read_of_a_page_not_worth_caching_still_stores_nothing(origin,
                                                                              monkeypatch):
    """闸还在：`_is_cacheable` 拒的东西不会因为这一刀进缓存（用桩直接验那一道）。"""
    import dhole_mcp.server as server_mod

    monkeypatch.setattr(server_mod, "_is_cacheable", lambda r: False)
    await MasterFetchServer().web_read(origin + "/report", cache_ttl=0)

    hit = await get_cached(origin + "/report", "markdown", None, ttl=DEFAULT_TTL,
                           ctx="")
    assert hit is None, "不该存的进了缓存"


# ─── 3 · 话要跟着实施走 ───


class TestWhatTheProsePromisesAboutZero:
    def _note(self, value) -> str:
        from dhole_mcp.context import _cache_ttl_note
        return _cache_ttl_note(value)

    def test_the_negative_value_sentence_no_longer_promises_a_missing_write(self):
        note = self._note(-1)
        assert "cache_ttl=-1 is not a duration" in note, note
        assert "reads no cache" in note, note
        assert "writes none" not in note, f"话还停在旧实施上：{note}"

    def test_the_wire_phrase_is_left_alone_because_it_was_never_false(self):
        """常驻层那句 "0 = force fresh" 讲的是读侧，它一直是真的 —— 这一刀不许为了
        解释写侧而把每颗工具的 `cache_ttl` 描述变长（连接期载荷的账由
        `test_the_dhole_tools_price_is_the_wire_itself.py` 公开定价，动它要单独决定）。
        说一句假话的那份文件是 references/tools.md，改的是那里。
        """
        seen = 0
        for tool in MasterFetchServer._TOOL_DEFS:
            schema = (tool["inputSchema"].get("properties") or {}).get("cache_ttl")
            if not schema:
                continue
            seen += 1
            assert "0 = force fresh" in schema["description"], (tool["name"], schema)
        assert seen >= 3, seen

    def test_the_manual_says_what_zero_does_now(self):
        from dhole_mcp import guide

        tools = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        assert "skip the cache in both directions" not in tools, (
            "手册还在承诺那一发不写回")
        assert "action chain" in tools.lower(), tools[:400]
        assert "reads no cache, and the copy it fetches is stored" in tools, tools[:400]

    def test_the_fresh_round_of_a_search_is_stored_too(self):
        """同一颗参数在搜索那条路上问的是同一个问题 —— 两处都得改，手册才只有一句。

        `web_search` 的写入点住在它自己的装配里（不经过 `_store_ttl`），所以这里按仓库
        既有先例（`_cap_in_the_formula`）锁形状：读侧仍看 `cache_ttl`，写侧在它非 0 时
        用它、否则用本工具的默认那颗。
        """
        import inspect

        import dhole_mcp.search as search_mod

        src = inspect.getsource(search_mod.web_search)
        assert "cache_ttl if cache_ttl > 0 else SEARCH_CACHE_TTL" in src, (
            "搜索那一轮的写侧又回到 `cache_ttl > 0` 上了")
        assert "if cache_ttl > 0 and results_list" not in src, (
            "旧条件回来了：强制取新的那一轮什么都不存")

    def test_the_fresh_copy_is_what_lands_on_disk(self):
        """存进去的是**这一发**的正文，而不是"下次再说"：直接读磁盘那一行确认。"""
        import asyncio

        url = "https://store-check.test/page"
        row = _clean()
        ttl = _store_ttl(row, 0)
        assert ttl == DEFAULT_TTL
        asyncio.run(cache_mod.set_cached(url, "markdown", row.content, 200, None, ttl))
        back = asyncio.run(get_cached(url, "markdown", None, ttl=ttl))
        assert back and back["content"] == row.content, back
