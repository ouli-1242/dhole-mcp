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

import pytest

from dhole_mcp import identity
from dhole_mcp.crawl import plan_store, plan_token
from dhole_mcp.server import MasterFetchServer

ROOT = "http://127.0.0.1:8399"


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
