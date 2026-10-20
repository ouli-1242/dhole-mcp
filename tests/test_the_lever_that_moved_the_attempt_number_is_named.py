"""`attempt_cap_ms` 说明里只点了一个因，而至少有三种约束会动这颗数 —— 要说得出是哪一个。

现场（复验 R02-F9 的 B 那一发量出来的新事实 · R02-041）：

    web_read(url='http://127.0.0.1:8399/article?run=…', timeout=5000, cache_ttl=0)
      → 200 完成、attempt_cap_ms=4000、timeout_applied 缺席、summary 里没有一个字

这颗字段的自述写的是「it is what your `timeout=` became after this tier's own
per-attempt ceiling (30,000ms) is applied」。但 4000 不是那顶帽：5000ms 远够不着 30s。
把 `_http_tier_budget` 的分岔逐格量过（探针记在 fix-R02-F10.md）：

    budget=5000 left=5.0  → (5, 0)      # 点名的那颗赢，没人收窄
    budget=5000 left=4.4  → (4, 0)      ← 现场这一格：**还剩多少**把它压到 4
    已见主机 latency=6s   → 学到的帽 18s（clamp(3×EMA, 5s, 60s)）
    slow_domain 表内      → 至少 20s（这是**抬宽**，同样没人说）

也就是说动这颗数的约束至少有三颗（还剩多少 / 该主机学到的习惯 / 慢站的最低保障），而字段
的一句话只说得出第四颗（30s 上限，那一支确实由 `timeout_applied` + bulk_get 的句子在讲）。
**一句话解释两个以上的因，等于哪个都没解释** —— F5 立的是"看得见数"、F9 立的是"抬宽要
说"，本条是第三问：说得**是哪个因**。

契约（本文件钉住）：
  * `_http_tier_budget` 交回它自己的分岔：`(秒, 重试, 哪颗约束生效)`，token ∈
    "" / "left" / "learned" / "slow-domain"；谁都不生效时是 ""。
  * 生效的那颗约束要说得出名字，且**只有与点名的那颗数不同时**才说（没动就不要有句子）。
  * 三位作者一位一件事，互不重复：30s 上限那支仍是 `timeout_applied` + bulk_get 那句；
    1s 下界那支仍是 `context._timeout_floor_note`；本条只管 HTTP 一档自己的分账。
  * 一句话只有一个作者：token→句子的映射在 `context`，量出 token 的人在 `server`。
  * 判决位与调度一位不动：本刀不改任何一次 `min()`/`max()` 的**数值**，只让它把"是谁生效"
    一起交回来。把自适应调度改成不 adaptive 是另一族决定，本刀明确不做。
  * 手册里 `attempt_cap_ms` 那一行要说得出"这颗数可能被几种约束量过，summary 里会点名
    生效的那一种"，而不是只指向 30s 那一颗。
"""

from __future__ import annotations

import http.server
import inspect
import pathlib
import socketserver
import threading

import pytest

from dhole_mcp import context as context_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

FAST_PAGE = (b"<html><head><title>Which lever</title></head><body><article>"
             b"<h1>The number moved, so the reason has to be named</h1>"
             + b"<p>A field that names one cause while three operate names none.</p>" * 4
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
        self.send_header("content-length", str(len(FAST_PAGE)))
        self.end_headers()
        self.wfile.write(FAST_PAGE)

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


@pytest.fixture(autouse=True)
def _no_learned_latency_leaks():
    """每发都从"这颗主机没见过"开始，否则上一发学到的习惯会替本发选约束。"""
    before = dict(server_mod._DOMAIN_LATENCY)
    server_mod._DOMAIN_LATENCY.clear()
    yield
    server_mod._DOMAIN_LATENCY.clear()
    server_mod._DOMAIN_LATENCY.update(before)


class _RecordingSession:
    """记下 HTTP 一档**实际**拿到的一试次秒数，然后失败（本文件比的是数与因）。"""

    seen: list = []

    def __init__(self, **kwargs):
        _RecordingSession.seen.append({"ctor": kwargs.get("timeout")})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, url, **kwargs):
        raise RuntimeError("timed out reading " + url)


@pytest.fixture
def sessions(monkeypatch):
    import dhole_mcp.fetcher as fetcher_mod

    _RecordingSession.seen = []
    monkeypatch.setattr(fetcher_mod, "HTTPSession", _RecordingSession)
    return _RecordingSession.seen


# ─── 1 · 量出约束的那一位：它必须把"是哪一颗"一起交回来 ───


class TestTheTierNamesItsOwnCeiling:

    def test_the_callers_own_figure_wins_silently(self):
        secs, retries, bound = server_mod._http_tier_budget(
            "http://fresh.test/", 5000.0, 5.0)
        assert (secs, retries, bound) == (5, 0, ""), (secs, retries, bound)

    def test_what_is_left_of_the_call_is_named_as_the_binder(self):
        secs, _retries, bound = server_mod._http_tier_budget(
            "http://fresh.test/", 5000.0, 4.4)
        assert (secs, bound) == (4, "left"), (secs, bound)

    def test_a_learned_host_ceiling_is_named_as_the_binder(self):
        server_mod._DOMAIN_LATENCY["learned.test"] = 6000.0   # ms → 3× = 18s
        secs, _r, bound = server_mod._http_tier_budget(
            "http://learned.test/", 60000.0, 60.0)
        assert (secs, bound) == (18, "learned"), (secs, bound)

    def test_a_slow_domain_floor_is_named_as_the_binder(self):
        host = next(iter(server_mod._SLOW_HTTP_DOMAINS))
        secs, _r, bound = server_mod._http_tier_budget(
            f"http://www.{host}/", 5000.0, 60.0, slow_domain=True)
        assert secs >= 20 and bound == "slow-domain", (secs, bound)

    def test_a_document_does_not_wear_the_pages_ceiling(self):
        """文档不戴那顶"页面习惯"的帽（既有设计），所以它也不该被说成 learned。"""
        server_mod._DOMAIN_LATENCY["learned.test"] = 6000.0
        secs, _r, bound = server_mod._http_tier_budget(
            "http://learned.test/report.pdf", 60000.0, 60.0, document=True)
        assert bound != "learned", (secs, bound)


# ─── 2 · token→句子只有一位作者 ───


class TestTheSentenceHasOneAuthor:

    def test_each_binder_names_its_own_lever(self):
        left = context_mod._attempt_budget_note(5000, 2, "left")
        learned = context_mod._attempt_budget_note(60000, 18, "learned")
        slow = context_mod._attempt_budget_note(5000, 20, "slow-domain")
        assert "2000" in left and "5000" in left and "left" in left.lower(), left
        assert "18" in learned and "learned" in learned.lower(), learned
        assert "20" in slow and "slow" in slow.lower(), slow

    def test_nothing_moved_means_nothing_to_say(self):
        for cause in ("", "left", "learned"):
            assert context_mod._attempt_budget_note(5000, 5, cause) == "", cause
        assert context_mod._attempt_budget_note(None, 4, "left") == ""
        assert context_mod._attempt_budget_note(5000, 4, "") == ""

    def test_the_clock_only_earnestly_speaks_when_it_ate_more_than_half(self):
        """每一发限时调用到档前都会花掉一点预算 —— 5000→4000 每次都发生，说它是噪声。

        材料性判据（本文件钉住这条取舍）：只剩一半以上时不说，丢掉一半以上才说。
        """
        assert context_mod._attempt_budget_note(5000, 4, "left") == "", "零头也开口"
        assert context_mod._attempt_budget_note(5000, 3, "left") == ""
        assert context_mod._attempt_budget_note(5000, 2, "left") != ""
        # 另两颗因与材料性无关：它们是"这颗主机/这个站"的意外，不是必然的零头。
        assert context_mod._attempt_budget_note(5000, 4, "learned") != ""

    def test_no_sentence_doubles_its_unit(self):
        """单位只写一次：`{asked}` 自己带 ms，模板再补一个就是「60000msms」。

        这一格是真 CLI 复验第一趟量出来的（`sessions/P02-R5-F10-pre.jsonl` 之后那趟 A 行
        逐字写着「not your 60000msms」）—— 单测只比子串时看不见它。
        """
        for cause in ("left", "learned", "slow-domain"):
            note = context_mod._attempt_budget_note(60000, 5, cause)
            assert "msms" not in note, (cause, note)
            assert "  " not in note.strip(), (cause, note)

    def test_an_unknown_cause_does_not_borrow_another_sentence(self):
        """认不出的 token 宁可不说，也不许套一句别的原因。"""
        assert context_mod._attempt_budget_note(5000, 4, "made-up") == ""


# ─── 3 · 端到端：现场那一格与它的反面锁 ───


@pytest.mark.asyncio
async def test_the_learned_ceiling_is_named_on_the_response(origin, sessions):
    """把主机"教"成 18s，再点名 60000 —— 回执要说得出是学到的那顶帽。"""
    host = origin.split("//")[1]
    server_mod._DOMAIN_LATENCY[host] = 6000.0
    r = await MasterFetchServer().web_read(
        url=origin + "/", timeout=60000, cache_ttl=0)
    text = f"{r.summary} {r.next_action}"
    assert "18" in text, f"没说这颗数被学到习惯量到：{text[:280]}"
    assert "learned" in text.lower(), text[:280]
    assert r.attempt_cap_ms == 18000, r.attempt_cap_ms


@pytest.mark.asyncio
async def test_a_figure_that_was_not_rewritten_says_nothing(origin, sessions):
    """反面锁：点名的那颗就是拿到的那颗时，一句话都不许多。"""
    r = await MasterFetchServer().web_read(url=origin + "/", timeout=5000, cache_ttl=0)
    text = f"{r.summary} {r.next_action}"
    assert "bound by" not in text, text[:280]
    assert 0 < r.attempt_cap_ms <= 5000, (
        f"这一发的前提是被压的只是零头（不是一半以上）：{r.attempt_cap_ms}")


@pytest.mark.asyncio
async def test_the_30s_ceiling_keeps_its_own_two_sentences(origin, sessions):
    """上限那一支的两位作者不许被本刀改写：`timeout_applied` 照写、bulk_get 那句照写。"""
    r = await MasterFetchServer().web_read(
        url=origin + "/", timeout=120000, cache_ttl=0)
    assert r.timeout_applied == 30000, r.timeout_applied
    assert r.attempt_cap_ms == 30000, r.attempt_cap_ms
    assert "clamped" in (r.summary or "").lower(), r.summary


# ─── 4 · 手册 ───


def test_the_manual_stops_naming_only_one_cause():
    from dhole_mcp import quality as quality_mod
    doc = (pathlib.Path(inspect.getsourcefile(quality_mod)).resolve().parent
           / "skills" / "dhole-web" / "references" / "fields.md"
           ).read_text(encoding="utf-8")
    rows = [ln for ln in doc.splitlines() if ln.startswith("| `attempt_cap_ms`")]
    assert rows, "手册里得有这颗字段"
    joined = "\n".join(rows).lower()
    assert "what is left" in joined and "learned" in joined, (
        f"手册仍只点 30s 那一颗因：{rows[0][:300]}")
