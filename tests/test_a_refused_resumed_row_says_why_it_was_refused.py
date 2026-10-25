"""续传那一发的三行必须能加回去：不是 ok、不是 error 的那一行要说得出为什么。

现场（2026-10-15 · P02-04 白榆（专利代理师 L2）· S3 用 plan 号续页 · R02-013 · P2 · 组 D）：

    web_crawl(url='https://milvus.io', resume_token='5331724450c2:3', max_pages=3)
      → 三行 status 全 200、正文各 7,000+ 字符
      → 第一行 content_ok=False 而 error=None、substance=None
      → 顶层 summary=「plan resumed at 4-6 of 40: 2 ok, 0 with error」

两个数相加不等于行数。用户的自然动作就是把它们加起来 —— 加不上就是一句假话；而中间那一档
既不绿也不红，也不给原因。他的说法：「它给我绿的和红的，中间那档它不说。」

根因（读到码，issue 里那条 unknown 现在有答案）：续传那一支是行信封的**第二份更薄的实现** ——
`server.py` 的 resume 循环从 `web_read` 造 `CrawlPage` 时抄了一张固定的字段表，
`substance` / `substance_basis` / `summary` 三颗都不在里面（`CrawlPage` 自己声明了这三颗，
普通爬取那一路 1977 行也真的填了）。而 `web_read` 的 `nav_only` / `collapsed` 那一族恰恰是
**不带 error 的 false**（本机实测：`web_read(/shell)` → content_ok=false、error=''、
substance='nav_only'）—— 理由在 substance 那一格里，抄字段时掉在了地上。
顶层那句又只数两颗桶（`sum(r.content_ok)` 与 `sum(r.error)`），所以第三档在数上也没有落点。

契约（本文件钉住）：
  * 一行 `content_ok=false` 时必须有一个理由在上面：`error` 非空，或 `substance` 非空
    （理由的数是 `substance_basis`）—— 不许出现"既不绿也不红也没原因"那一格；
  * summary 的桶相加必须等于行数：不是 ok 也不是 error 的那些行要有一颗自己的数，
    且那句要说出去哪里读理由；
  * 反面锁：ok 的行照旧 ok（不许为了凑数把它拖进第三档）、真的网络失败行照旧
    `status=-1` + `error` 非空并算进 "with error"；
  * 这一刀不许改判据本身：`nav_only` 该翻的仍翻，`collapsed` 该报的仍报。
"""

from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dhole_mcp import identity
from dhole_mcp.crawl import plan_store, plan_token
from dhole_mcp.server import MasterFetchServer

ROOT = "http://127.0.0.1:0"  # _local_target 每次重绑成自己的临时端口

_PAGE = ("<!doctype html><html><head><title>{title}</title></head><body>"
         "<nav><a href='/a'>A</a><a href='/b'>B</a></nav><main>{body}</main></body></html>")
_ROUTES = {
    "/article": ("Article", "<p>" + "Dhole resume fixture article body. " * 12 + "</p>"),
    "/shell": ("Shell", ""),
    "/nav": ("Nav", ""),
}


class _TargetHandler(BaseHTTPRequestHandler):
    """三张脸对应三档行：/article 有正文（ok）、/shell 与 /nav 是 nav_only 壳
    （不绿不红的第三档）、其余路径 404（error 桶）。"""

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in _ROUTES:
            title, body = _ROUTES[path]
            data = _PAGE.format(title=title, body=body).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass


@pytest.fixture(autouse=True)
def _local_target():
    """真机靶站收进 fixture，suite 自包含。

    从前它假设操作员手动起着一台 8399 靶站——没有它，4 条用例红，还有 2 条
    **空转绿**（所有行都 error，判据恰好被满足，比红更会骗人）。端口用临时的，
    不与任何常驻靶站抢 8399。
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    global ROOT
    ROOT = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    identity.set_value("allow_private", True)


async def _resume(urls):
    pid = plan_store(list(urls))
    return await MasterFetchServer().web_crawl(
        url=urls[0], resume_token=plan_token(pid, 0), max_pages=len(urls), cache_ttl=0)


# ─── 1 · 被拒的那一行带着自己的理由 ───

@pytest.mark.asyncio
async def test_a_refused_resumed_row_carries_a_reason():
    r = await _resume([f"{ROOT}/article", f"{ROOT}/shell"])
    rows = {p.url: p for p in r.pages}
    refused = rows[f"{ROOT}/shell"]
    assert refused.content_ok is False, "现场那一格没了：/shell 现在竟然算内容"
    assert refused.error == "", refused.error
    assert refused.substance, ("content_ok=false、没有 error、也没有 substance —— "
                               "这一行既不绿也不红也没原因")
    assert refused.substance_basis, refused.substance_basis


@pytest.mark.asyncio
async def test_no_row_is_left_between_the_two_buckets():
    r = await _resume([f"{ROOT}/article", f"{ROOT}/shell", f"{ROOT}/nav"])
    for p in r.pages:
        if not p.content_ok:
            assert p.error or p.substance, (p.url, p.error, p.substance)


# ─── 2 · 顶层那两个数加得回来 ───

@pytest.mark.asyncio
async def test_the_resume_summary_adds_up_to_the_row_count():
    r = await _resume([f"{ROOT}/article", f"{ROOT}/shell", f"{ROOT}/nav"])
    ok = sum(1 for p in r.pages if p.content_ok)
    err = sum(1 for p in r.pages if p.error)
    third = len(r.pages) - ok - err
    assert third > 0, "没有第三档，这条判据就是空的"
    nums = [int(x) for x in re.findall(r"\b(\d+)\b", r.summary.split(": ", 1)[-1])]
    assert ok in nums, (r.summary, ok)
    assert err in nums, (r.summary, err)
    assert third in nums, f"summary 里没有第三档那颗数：{r.summary!r}"
    assert f"{third} " in r.summary and third <= len(r.summary.split()), r.summary
    # 说了第三档，就得说去哪儿读理由
    assert "substance" in r.summary or "reason" in r.summary.lower(), r.summary


# ─── 3 · 反面锁 ───

@pytest.mark.asyncio
async def test_the_ok_row_stays_ok_and_is_counted_once():
    r = await _resume([f"{ROOT}/article"])
    p = r.pages[0]
    assert p.content_ok is True and p.error == "", (p.content_ok, p.error)
    assert "1 ok" in r.summary, r.summary


@pytest.mark.asyncio
async def test_a_dead_url_is_still_the_error_bucket():
    r = await _resume([f"{ROOT}/no-such-page-404-zzz", f"{ROOT}/article"])
    dead = next(p for p in r.pages if "no-such-page" in p.url)
    assert dead.error, dead.substance
    assert " with error" in r.summary, r.summary


@pytest.mark.asyncio
async def test_the_verdict_itself_is_unchanged():
    """这一刀只把理由抬上信封，不改判据：/shell 仍是 nav_only（不是 ok）。"""
    r = await _resume([f"{ROOT}/shell"])
    assert r.pages[0].substance == "nav_only", r.pages[0].substance


# ─── 4 · 续传支的钳制与礼貌层（2026-10-25 全项目审查 P1）────────────────────
# _crawl_plan_resume 从前只钳 max_pages：concurrency / max_content_chars_per 裸进
# web_read、max_total_chars 不钳也不在批内判、负数帽变"立即停 0 行 + 游标原地
# 打转"。这一组钉住修复后的三件事：负数=不设帽、帽在批内逐行判且游标不前进、
# 钳位出口进 summary。


async def _resume_args(urls, **extra):
    pid = plan_store(list(urls))
    return await MasterFetchServer().web_crawl(
        url=urls[0], resume_token=plan_token(pid, 0),
        max_pages=len(urls), cache_ttl=0, **extra)


@pytest.mark.asyncio
async def test_a_negative_total_chars_is_no_cap_not_a_spin():
    """负数 max_total_chars 与爬取路同语义（"这一轮不要帽"）：行照常随行、计划
    走完，而不是"立即停 0 行、next_resume_token 指向同一 offset"让调用方原地打转。"""
    urls = [f"{ROOT}/article", f"{ROOT}/shell"]
    r = await _resume_args(urls, max_total_chars=-1)
    assert len(r.pages) == 2, (len(r.pages), r.summary)
    assert not r.truncated_by_total_chars
    assert not r.next_resume_token, r.next_resume_token


@pytest.mark.asyncio
async def test_the_char_cap_stops_inside_the_batch_and_holds_the_cursor():
    """帽在批内逐行判（crawl.py:1971 同一形状）：越界行不随行、游标停在第一
    未随行的行上，summary 点名"付了没返回"的行数与出路。/article 每行 ~450
    字符、帽 1000、并发 4：第 3 行越过帽（~1350），第 4 行在批内被挡下。"""
    urls = [f"{ROOT}/article"] * 4
    r = await _resume_args(urls, max_total_chars=1000, concurrency=4)
    assert r.truncated_by_total_chars is True, r.summary
    assert len(r.pages) == 3, (len(r.pages), r.summary)
    assert "held 1 fetched row" in r.summary, r.summary
    assert r.next_resume_token, "被帽挡下的行必须可续传"
    # 游标 = 第一未随行的行：下一发从那里继续（这些行会被重读，而不是丢）。
    assert r.next_resume_token.endswith(":3"), r.next_resume_token


@pytest.mark.asyncio
async def test_resume_clamps_are_announced_in_the_summary():
    """concurrency / max_content_chars_per 的钳位要出口（"静默改变"不许存在）。"""
    urls = [f"{ROOT}/article"]
    r = await _resume_args(urls, concurrency=99, max_content_chars_per=999999)
    assert "concurrency clamped" in r.summary, r.summary
    assert "max_content_chars_per clamped" in r.summary, r.summary
