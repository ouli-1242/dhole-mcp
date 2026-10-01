"""feed_fetch 的 since 增量端到端场景（走完整工具路径）。

计划第十章的 Eval 缺口之一。此前 `feed_fetch` 没有 `allow_private`，回环被
SSRF 守卫拦下，这个场景只能靠单测覆盖逻辑（`tests/test_feed.py`、
`tests/test_revalidation_g23.py`）；D3 补上 `allow_private` 之后可以走完整路径。

判据不是"调通了"，是三条语义各自成立：

1. `since` 只保留 published >= cutoff 的条目；
2. 被过滤掉多少条要**报出来**（`items_older_than_since`）——一份变短的列表
   分不清"feed 一共就 3 条"和"50 条被过滤掉了、3 条是新的"；
3. 没有日期的条目**保留**并计数（`items_without_date`）——不日期的 feed
   无法诚实地过滤，丢掉它们等于悄悄少给内容。

另有一条反向判据：**不带 `since` 时两个回执计数保持 0**。它们只在过滤路径
（`feed.py::_keep_newer`）上被赋值，所以无过滤那一轮 `items_without_date=0`
是"这轮没有过滤动作可报"，不是"漏掉了无日期条目"——后者要靠 `items` 里确实
带着无日期条目来验，而不是靠计数。

靶站 `/feed.xml` 的日期是相对 now 生成的，所以期望值不自己算日期，而是**从
靶站自己的输出反推**：直接取回 XML、解析 pubDate，再据此构造 cutoff 与期望
计数。靶站改了日期也不会让这个用例变成假红。

    python evals/feed_since.py          # 需要靶站在 127.0.0.1:8399
"""

from __future__ import annotations

import asyncio
import re
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
BASE = "http://127.0.0.1:8399"

from dhole_mcp.server import MasterFetchServer  # noqa: E402

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


def field(obj, key, default=None):
    """feed_fetch 走的是 envelope 形状，逐条结果是 dict；两种形状都认。"""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def fixture_pubdates() -> list:
    """靶站自己声明的 pubDate，按 XML 顺序（= 最新在前）。"""
    raw = urllib.request.urlopen(f"{BASE}/feed.xml", timeout=10).read().decode("utf-8")
    stamps = re.findall(r"<pubDate>([^<]+)</pubDate>", raw)
    return [parsedate_to_datetime(s) for s in stamps]


async def call_feed(**kw) -> dict:
    server = MasterFetchServer()
    out = await server.feed_fetch(urls=[f"{BASE}/feed.xml"], allow_private=True, **kw)
    return out[0]


def dated_items(r) -> list:
    return [it for it in field(r, "items", []) if field(it, "published", "")]


def undated_items(r) -> list:
    return [it for it in field(r, "items", []) if not field(it, "published", "")]


async def check_no_filter(n_dated: int, n_undated: int) -> None:
    # 两个回执计数只在 since 过滤路径上被写（feed.py 的 _keep_newer 才赋值），
    # 不带 since 时它们保持默认 0 —— 这不是"漏报无日期条目"，而是"这轮没有
    # 过滤动作可报"。所以判据是：条目全回来、两个计数都是 0、无日期条目**在
    # items 里**（而不是被计数）。把 without_date 断言成 n_undated 是错的。
    r = await call_feed()
    n_items = len(field(r, "items", []))
    n_undated_back = len(undated_items(r))
    ok = (n_items == n_dated + n_undated
          and n_undated_back == n_undated
          and field(r, "items_older_than_since") == 0
          and field(r, "items_without_date") == 0
          and not field(r, "error", ""))
    record("不带 since：全部条目返回，无过滤计数", ok,
           f"条目={n_items}（期望 {n_dated + n_undated}）"
           f" 其中无日期={n_undated_back}（期望 {n_undated}）"
           f" older={field(r, 'items_older_than_since')}"
           f" without_date={field(r, 'items_without_date')}（无过滤时保持 0）"
           f" error={field(r, 'error', '')!r}")


async def check_since_keeps_newer(dated: list, n_undated: int, cutoff_idx: int) -> None:
    cutoff = dated[cutoff_idx]
    keep = cutoff_idx + 1                 # 含边界那一条
    drop = len(dated) - keep
    r = await call_feed(since=cutoff.isoformat())
    kept = dated_items(r)
    ok = (len(kept) == keep
          and field(r, "items_older_than_since") == drop
          and field(r, "items_without_date") == n_undated
          and len(field(r, "items", [])) == keep + n_undated
          and not field(r, "error", ""))
    record(f"since={cutoff.date()}：只留更新的，被丢掉的如实报数", ok,
           f"保留带日期={len(kept)}（期望 {keep}）"
           f" older_than_since={field(r, 'items_older_than_since')}（期望 {drop}）"
           f" without_date={field(r, 'items_without_date')}（期望 {n_undated}）"
           f" 总数={len(field(r, 'items', []))}")
    titles = [field(it, "title", "") for it in undated_items(r)]
    record("被过滤时，无日期条目仍在返回内容里（不是只计数）",
           len(titles) == n_undated, f"无日期条目: {titles}")


async def check_since_in_future(n_dated: int, n_undated: int) -> None:
    r = await call_feed(since="2099-01-01T00:00:00Z")
    ok = (len(dated_items(r)) == 0
          and field(r, "items_older_than_since") == n_dated
          and field(r, "items_without_date") == n_undated
          and len(field(r, "items", [])) == n_undated)
    record("since 在未来：带日期的全被过滤，无日期的仍保留", ok,
           f"保留={len(field(r, 'items', []))}（期望 {n_undated} 条无日期的）"
           f" older_than_since={field(r, 'items_older_than_since')}（期望 {n_dated}）")


async def check_unreadable_since() -> None:
    r = await call_feed(since="not-a-date")
    ok = bool(field(r, "error", "")) and not field(r, "items", [])
    record("since 读不出来时报错，而不是当成『全是新的』", ok,
           f"error={field(r, 'error', '')!r} 条目={len(field(r, 'items', []))}")


async def main() -> int:
    print(f"靶站 {BASE}\n")
    try:
        dated = fixture_pubdates()
    except Exception as e:
        print(f"取不到靶站 feed（先起 python evals/fixture_site.py）：{e}")
        return 2
    n_undated = 2
    print(f"靶站带日期条目 {len(dated)} 条：{[d.date().isoformat() for d in dated]}\n")

    steps = [
        ("不带 since：全部条目返回", lambda: check_no_filter(len(dated), n_undated)),
        ("since 保留更新的并报出被丢掉的", lambda: check_since_keeps_newer(dated, n_undated, 3)),
        ("since 在未来", lambda: check_since_in_future(len(dated), n_undated)),
        ("since 不可读", check_unreadable_since),
    ]
    for name, fn in steps:
        try:
            await fn()
        except Exception as e:
            record(name, False, f"{type(e).__name__}: {e}")

    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 项通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
