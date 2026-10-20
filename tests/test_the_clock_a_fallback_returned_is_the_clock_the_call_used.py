"""R01-039 · 回退那一路交回的钟，必须是这一次调用真用掉的钟（P09 邓佳 S3b）。

现场（`sessions/P09-s3b-filter-empty.json` + `sessions/calls.jsonl` 同一行）：

```
web_discover(url="https://milvus.io/docs", max_pages=30,
             search="api_reference_sdk_python_grpc_rest", timeout=90000)
  duration_ms            269          ← 只有补考那一步
  summary                "The 45629ms this call had left went into a focus=… read"
  harness wall           44.65s       ← 整发真用的时间
  对照 sessions/P09-s2-discover.json  duration_ms 76030 与 wall 76.04 一致 —— 只有回退这一支错
```

三方互相打不开：字段说 0.27 秒，同一封响应自己写 45629ms，量表的墙钟说 44.65 秒，差约
165 倍。数据/BI 型用户拿这一列做逐源耗时报表 —— **错的耗时比没有耗时更坏**，因为它
看起来是个读数。

根因不在计时器，在**返回的是谁的对象**：`_discover_search_fallback` 把补考那一步的
`scan` 原封不动当本调用的响应交出去，而 `scan.duration_ms` 是 `web_crawl` 自己那一步的
耗时 —— 地图阶段（这里 44.4 秒）从没被加进去。第二半是那句话把"还剩多少预算"
（`left` = 时钟 − 已用）当成"这次读页用了多少"来写，两者在这发里差了 170 倍。

修法两处都是记账，不是新行为：交回之前把地图阶段的耗时并进 `duration_ms`，并把那句
话改成点名**两个花费**（地图用了多少、随后那一次读页又用了多少、它花掉了余额里的
多少），"还剩多少"只在它真的是余额的地方出现。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as SM
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
from dhole_mcp.server import MasterFetchServer

URL = "https://site.test/docs"
WORD = "grpc_rest"
MAP_MS = 44_371.0
SCAN_MS = 269.0
CLOCK_MS = 90_000


def _map_rows(n: int) -> list[CrawlPage]:
    return [CrawlPage(url=f"{URL}/page-{i}", title=f"Page {i}", depth=1) for i in range(n)]


def _hit_pages(n: int = 3) -> list[CrawlPage]:
    return [CrawlPage(url=f"{URL}/doc-{i}", title=f"Doc {i}", depth=1, content_ok=True,
                      content=[f"prose about {WORD} on page {i}"], error="")
              for i in range(n)]


@pytest.fixture
def stub(monkeypatch):
    """把两次 web_crawl（地图 / 补考）钉成可编程的替身，并把 `now` 换成假钟。

    假钟必须自己钉：这一刀审的就是数，数不许来自真实流逝的时间（那会造出一条今天绿、
    明天红的用例）。两次读数 = 地图开始、地图结束，于是 spent_ms = MAP_MS。
    """
    state = {"map_rows": 0, "discovered": 270, "pages": _hit_pages(),
             "map_error": "", "scan_error": ""}
    reads = [0.0, MAP_MS / 1000]
    monkeypatch.setattr(SM, "now", lambda: reads.pop(0) if reads else reads[-1])

    async def fake(self, url, **kw):
        if kw.get("discover_only"):
            return CrawlResponseModel(
                start_url=url, pages=_map_rows(state["map_rows"]),
                pages_discovered=state["discovered"], discover_only=True,
                duration_ms=MAP_MS, error=state["map_error"],
                summary="map of 270 URL(s)", next_action="")
        return CrawlResponseModel(
            start_url=url, pages=state["pages"], pages_crawled=len(state["pages"]),
            pages_discovered=state["discovered"], duration_ms=SCAN_MS,
            error=state["scan_error"], summary="crawled 3 page(s)", next_action="")

    monkeypatch.setattr(MasterFetchServer, "web_crawl", fake)
    return state


def _call(monkeypatch, **kw):
    """跑一次真调度，拿线上的那份载荷（`_dispatch` 交回的是 wire dict）。"""
    args = {"url": URL, "max_pages": 30, "search": WORD, "timeout": CLOCK_MS}
    args.update(kw)
    out = asyncio.run(MasterFetchServer()._dispatch("web_discover", args))
    if isinstance(out, tuple) and len(out) == 2:
        out = out[1]
    assert isinstance(out, dict), type(out)
    return out


# ─── 1 · 那一条读数 ─────────────────────────────────────────────

def test_the_clock_returned_is_the_clock_the_call_used(stub, monkeypatch):
    out = _call(monkeypatch)
    assert out["pages"], "这一发走的必须是补考接管那一路"
    assert out["duration_ms"] == pytest.approx(MAP_MS + SCAN_MS), out["duration_ms"]


def test_the_map_phase_is_not_dropped_when_its_result_is_replaced(stub, monkeypatch):
    """字段不许再说 0.27 秒：地图用了 44.4 秒这一件事必须在总数里。"""
    out = _call(monkeypatch)
    assert out["duration_ms"] > MAP_MS, out["duration_ms"]


def test_the_prose_and_the_field_name_the_same_total(stub, monkeypatch):
    """一句话与一颗字段不许各说一套：把句子里的数加起来必须等于字段。"""
    out = _call(monkeypatch)
    text = out.get("summary") or ""
    assert f"{MAP_MS:,.0f}ms" in text, text
    assert f"{SCAN_MS:,.0f}ms" in text, text
    assert out["duration_ms"] == pytest.approx(MAP_MS + SCAN_MS), (out["duration_ms"], text)


def test_the_budget_left_is_not_reported_as_time_spent(stub, monkeypatch):
    """`left` 是余额不是花费：这一发的读页只用了 269ms，不是那 45,629ms。"""
    out = _call(monkeypatch)
    text = out.get("summary") or ""
    assert f"{int(CLOCK_MS - MAP_MS)}ms this call had left went into" not in text, text
    assert "left" in text.lower(), text   # 余额仍可说，但要作为余额说


# ─── 2 · 其余三支：不许有的多算、不许有的漏算 ───────────────────

def test_the_no_hit_branch_counts_both_phases(stub, monkeypatch):
    """补考跑了但一个词都没命中：交回的是地图那份模型，两段的账同样都要在。"""
    stub["pages"] = [CrawlPage(url=f"{URL}/doc-{i}", title=f"Doc {i}",
                               content=["unrelated prose"], error="") for i in range(3)]
    out = _call(monkeypatch)
    assert out["pages"] == [], out["pages"]
    assert out["duration_ms"] == pytest.approx(MAP_MS + SCAN_MS), out["duration_ms"]
    assert f"{SCAN_MS:,.0f}ms" in (out.get("summary") or ""), out.summary


def test_the_branch_that_ran_no_scan_counts_the_map_only(stub, monkeypatch):
    """没跑补考就不许凭空多出一段耗时：那一支用的钟就是地图自己。"""
    stub["discovered"] = 270
    out = _call(monkeypatch, timeout=45_000)   # 45,000 - 44,371 < 4,000ms 门槛
    assert out["duration_ms"] == pytest.approx(MAP_MS), out["duration_ms"]
    assert "No content scan was run" in (out.get("summary") or ""), out.summary


def test_a_map_that_answered_keeps_its_own_clock(stub, monkeypatch):
    """正向不许被连带：地图筛得出行时压根没有第二次读，钟就是地图的。"""
    stub["map_rows"] = 5
    out = _call(monkeypatch)
    assert len(out["pages"]) == 5, out["pages"]
    assert out["duration_ms"] == pytest.approx(MAP_MS), out["duration_ms"]
