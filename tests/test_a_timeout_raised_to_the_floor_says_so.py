"""点名的 `timeout=` 被 1s 下界**抬宽**了 —— 回执看得见数，却说不出数为什么变了。

现场（修 R02-031 时量出来的新事实 · P02-08 傅霖（公益组织项目官员 L1）· S5B · R02-039）：

    web_read(url=<本地回环靶>, timeout=250, cache_ttl=0)
      → 200 完成、attempt_cap_ms=1000（R02-031 之后才读得出来）
      → summary / next_action 里没有一个字提到 250 被改过

同一封响应里另外两句话都在说别的事：`timeout_applied` 自己的口径是「只有被上限砍才写」
（250 没有被砍，所以它正确地缺席），`attempt_cap_ms` 给的是"一次尝试实际拿到多少"。于是
读者手上有 250 与 1000 两个数，中间没有任何解释 —— 而 `bulk_get` 那一路的钳位是有句子的
（"max_total_chars clamped 520->1000"），"抬宽"这件事在这一颗参数上从来没有过。

机制（读码而不是猜）：一次调用 budgets 的下界写死在两处同一族的算式里 ——
`server.web_read` 的 `budget_ms = max(1000.0, float(timeout or 30000))`（整轮那颗）与
`_http_tier_budget` 的 `max(1.0, …)`（一档那颗）。**下界本身是设计**：在那一层 `0` 不是
"立刻"而是"不设时钟"，秒级颗粒必须有个下界，否则一次亚秒点名的调用会把整条阶梯拆成零预算。
本刀不动那颗下界，也不动任何一次调度 —— 缺的是申报。

契约（本文件钉住）：
  * 点名了一颗**低于下界**的 `timeout=` → 回执必须有一句话，句里同时出现**点名的那颗数**
    与**实际用的那颗数**，并说明这是抬宽而不是砍除（作者：`context._timeout_floor_note`）。
  * 那句话要出现在 `summary` 里，与同一封响应的 `attempt_cap_ms` **同一个数** —— 散文与
    字段不许各说一个（R02-012 那一族的旧病）。
  * 反面锁（不许成噪声）：没点名（`None`/0/默认）不写；点名的那颗本来就 ≥ 下界不写；
    `timeout_applied` 的"只有被砍才写"口径一个字不改。
  * 一句"被抬宽"的话**只有一个作者**（`context.py`），与 `_clamp_note` / `_cache_ttl_note`
    并排住在同一处 —— 两处同义不同形的句子等于没有句子。
  * 手册里 `attempt_cap_ms` 那一行必须点出这颗下界存在（今天它只说"一次尝试给了多少"）。
"""

from __future__ import annotations

import http.server
import inspect
import pathlib
import socketserver
import threading

import pytest

from dhole_mcp import context as context_mod
from dhole_mcp.server import MasterFetchServer

TIMEOUT_FLOOR_MS = 1000

PAGE = (b"<html><head><title>Floor</title></head><body><article>"
        b"<h1>What the receipt should say about a raised budget</h1>"
        + b"<p>A parameter that moved by four times is not the parameter named.</p>" * 4
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


# ─── 1 · 那句话的作者 ───


class TestTheSentenceHasOneAuthor:

    def test_a_named_value_under_the_floor_is_announced_with_both_numbers(self):
        note = context_mod._timeout_floor_note(250, TIMEOUT_FLOOR_MS)
        assert "250" in note and "1000" in note, note
        assert "floor" in note.lower(), note
        # 抬宽不是砍除：`timeout_applied` 那句"被砍"的措辞不许被借用。
        assert "clamped" not in note.lower() or "raised" in note.lower(), note

    def test_nothing_named_means_nothing_to_say(self):
        for asked in (None, "", 0):
            assert context_mod._timeout_floor_note(asked, TIMEOUT_FLOOR_MS) == "", (
                f"没点名的一发被写了话：{asked!r}")

    def test_a_value_at_or_above_the_floor_is_quiet(self):
        for asked in (TIMEOUT_FLOOR_MS, 5000, 120000):
            assert context_mod._timeout_floor_note(asked, TIMEOUT_FLOOR_MS) == "", (
                f"这一发没被抬过，却开口了：{asked}")

    def test_the_note_is_not_number_typed_input(self):
        """非数字的点名额由参数层处理，这一句不许替它猜一个数。"""
        assert context_mod._timeout_floor_note("soon", TIMEOUT_FLOOR_MS) == ""


# ─── 2 · 端到端：散文与字段说同一个数 ───


@pytest.mark.asyncio
async def test_the_raised_budget_shows_up_in_the_summary(origin):
    r = await MasterFetchServer().web_read(url=origin + "/", timeout=250, cache_ttl=0)
    text = f"{r.summary} {r.next_action}"
    assert "250" in text, f"点名的那颗数不见了：{text[:260]}"
    assert "floor" in text.lower(), f"没有说这是 1s 下界抬宽：{text[:260]}"
    assert r.attempt_cap_ms == TIMEOUT_FLOOR_MS, (
        f"散文说的数与字段不是同一个：{r.attempt_cap_ms}")
    assert "1000" in text, f"实际用的那颗数不见了：{text[:260]}"


@pytest.mark.asyncio
async def test_a_budget_that_was_not_raised_says_nothing_about_a_floor(origin):
    """反面锁（§不许成噪声）：5s 的点名没被改过，一句话都不许多。"""
    r = await MasterFetchServer().web_read(url=origin + "/", timeout=5000, cache_ttl=0)
    text = f"{r.summary} {r.next_action}"
    assert "floor" not in text.lower(), text[:260]


@pytest.mark.asyncio
async def test_a_default_call_says_nothing_about_a_floor(origin):
    r = await MasterFetchServer().web_read(url=origin + "/", cache_ttl=0)
    assert "floor" not in f"{r.summary} {r.next_action}".lower(), r.summary


# ─── 3 · 一位作者与手册 ───


def test_the_floor_sentence_has_exactly_one_author():
    src_root = pathlib.Path(inspect.getsourcefile(context_mod)).resolve().parent
    hits = []
    for py in sorted(src_root.glob("*.py")):
        body = py.read_text(encoding="utf-8")
        if "is under the" in body and "floor" in body:
            hits.append((py.name, body.count("is under the")))
    assert len(hits) == 1, f"同一句话有两个作者：{hits}"
    assert hits[0][0] == "context.py", hits


def test_the_manual_names_the_floor_next_to_the_number():
    from dhole_mcp import quality as quality_mod
    doc = (pathlib.Path(inspect.getsourcefile(quality_mod)).resolve().parent
           / "skills" / "dhole-web" / "references" / "fields.md"
           ).read_text(encoding="utf-8")
    rows = [ln for ln in doc.splitlines() if ln.startswith("| `attempt_cap_ms`")]
    assert len(rows) >= 1, "手册里得有这颗字段"
    for row in rows:   # 两颗模型各一行，说的是同一件事：下界存在
        assert "1,000" in row or "1000" in row, (
            f"这一行只说给了多少，没说那颗下界：{row[:300]}")
        assert "floor" in row.lower(), row[:300]
