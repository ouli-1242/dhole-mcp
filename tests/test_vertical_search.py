"""P0 纵向搜索：免密钥的 news/academic/qa/code/community。

这一批买的是"一次调用就能问五类源"，所以判据也按这个分三组：

1. **每个适配器只读那一家的真形状**：Atom / GDELT JSON / Stack Exchange JSON /
   GitHub repository JSON / HN JSON。注入的 fetch 让这些断言不碰网络；
   每家至少钉住"URL 从哪来、日期从哪来、正文从哪来"。
2. **一条死源不许抹掉别的源**：`search_one` 把失败收成报告行，`search_many`
   用 gather 并发。报告里的 status 是 ok|empty|blocked|timeout|error，不是布尔。
3. **纵向行不是索引家族**：它们参与排序、日期窗、去重与 max_results，
   但 `engines_consensus` 永远是空串。混排时 web 行保留自己的共识与身份。

集成那几条走 `server.web_search`，因为参数校验、缓存键与回执字段都在那一层：
`sources_used` / `source_detail` / `sort_applied` 必须是这一发真的做过的事。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from dhole_mcp import search as search_mod
from dhole_mcp import search_verticals as sv
from dhole_mcp.search import (
    _merge_vertical_hits,
    _source_failure_note,
    _source_note,
    _sort_newest,
    _validate_sort,
    _validate_sources,
    _vertical_dead_end,
    gate_by_consensus,
)
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.search_verticals import (
    VerticalHit,
    VerticalReport,
    filter_by_window,
    search_many,
    search_one,
)
from dhole_mcp.server import MasterFetchServer

# ─── fixtures: one real shape per backend ─────────────────────────────────

_ARXIV = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Task Groups   for Everyone</title>
    <summary>We study structured concurrency.</summary>
    <published>2024-01-02T03:04:05Z</published>
    <author><name>Ada Lovelace</name></author>
    <author><name>Alan Turing</name></author>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>Second Paper</title>
    <summary>More text.</summary>
    <published>2024-02-03T00:00:00Z</published>
  </entry>
</feed>"""

_GDELT = json.dumps({"articles": [{
    "title": "Quake hits the coast",
    "url": "https://news.example.test/quake",
    "seendate": "20240102T030000Z",
    "domain": "news.example.test",
    "language": "English",
    "sourcecountry": "US",
}]}).encode()

_STACK = json.dumps({"items": [{
    "title": "How to group tasks?",
    "link": "https://stackoverflow.com/q/1",
    "score": 12,
    "answer_count": 3,
    "is_answered": True,
    "tags": ["python", "asyncio"],
    "creation_date": 1704164645,
}]}).encode()

_GITHUB = json.dumps({"items": [{
    "full_name": "fastapi/fastapi",
    "html_url": "https://github.com/fastapi/fastapi",
    "description": "FastAPI framework, high performance",
    "stargazers_count": 103268,
    "language": "Python",
    "updated_at": "2026-10-20T00:00:00Z",
    "created_at": "2018-12-08T00:00:00Z",
}]}).encode()

_HN = json.dumps({"hits": [{
    "objectID": "42",
    "title": "Show HN: Thing",
    "url": "https://example.test/thing",
    "points": 120,
    "num_comments": 30,
    "created_at": "2024-02-03T00:00:00Z",
}, {
    "objectID": "77",
    "title": "Ask HN: No link",
    "url": None,
    "points": 1,
    "num_comments": 0,
    "created_at": "2024-02-04T00:00:00Z",
}]}).encode()


def _fetcher(routes: dict[str, tuple[int, bytes]]):
    calls: list[str] = []

    async def fetch(url: str, headers: dict, timeout: float):
        calls.append(url)
        for needle, response in routes.items():
            if needle in url:
                return response
        raise AssertionError(f"unexpected URL: {url}")

    return fetch, calls


def _hit(rank=1, title="task groups paper", url="https://example.test/a",
         kind="academic", backend="arxiv", published="2024-05-01",
         precision="day") -> VerticalHit:
    return VerticalHit(kind=kind, backend=backend, rank=rank, title=title,
                       url=url, snippet="snippet about task groups",
                       published=published,
                       published_precision=precision, published_basis="fixture")


def _web(url="https://example.test/web", title="A web row", consensus=2) -> RawResult:
    return RawResult(title=title, url=url, snippet="web snippet", source="brave",
                     position=1, consensus=consensus, sources=("brave", "bing"))


def _server() -> MasterFetchServer:
    return MasterFetchServer()


def _force_neural(monkeypatch):
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        search_mod, "neural_rerank",
        lambda q, ranked, **kw: [(r, 0.9 - 0.05 * i)
                                 for i, r in enumerate(ranked)])


# ─── 1 · adapters ─────────────────────────────────────────────────────────

class TestTheAdapters:

    @pytest.mark.asyncio
    async def test_arxiv_keeps_the_atom_id_title_authors_and_day(self):
        fetch, calls = _fetcher({"export.arxiv.org": (200, _ARXIV)})
        hits, report = await search_one("academic", "task groups", 2, fetch=fetch)

        assert report.status == "ok" and report.count == 2
        assert "search_query=all:%22task+groups%22" in calls[0], \
            "a plain multi-word query is a phrase; see the exact-title regression"
        first = hits[0]
        assert first.title == "Task Groups for Everyone"  # whitespace collapsed
        assert first.url == "https://arxiv.org/abs/2401.00001v1"
        assert first.snippet.startswith("Ada Lovelace, Alan Turing · ")
        assert (first.published, first.published_precision) == ("2024-01-02", "day")
        assert first.published_basis == "2024-01-02T03:04:05Z"
        assert first.kind == "academic" and first.backend == "arxiv"

    @pytest.mark.asyncio
    async def test_arxiv_boolean_operators_are_not_wrapped_in_quotes(self):
        fetch, calls = _fetcher({"export.arxiv.org": (200, _ARXIV)})
        await search_one("academic", "task AND groups", 2, fetch=fetch)
        assert "search_query=all:task+AND+groups" in calls[0]
        assert "%22" not in calls[0]

    @pytest.mark.asyncio
    async def test_a_direct_call_cannot_ask_for_more_than_the_cap(self):
        fetch, calls = _fetcher({"export.arxiv.org": (200, _ARXIV)})
        await search_one("academic", "task groups", 999, fetch=fetch)

        assert "max_results=50" in calls[0]

    @pytest.mark.asyncio
    async def test_gdelt_uses_the_publisher_url_and_seendate(self):
        fetch, calls = _fetcher({"api.gdeltproject.org": (200, _GDELT)})
        hits, report = await search_one("news", "quake", 5, fetch=fetch)

        assert report.status == "ok"
        assert "mode=artlist" in calls[0] and "format=json" in calls[0]
        hit = hits[0]
        assert hit.url == "https://news.example.test/quake"
        assert hit.published == "2024-01-02"
        assert "news.example.test" in hit.snippet

    @pytest.mark.asyncio
    async def test_gdelt_rate_limit_is_named_as_blocked(self):
        fetch, _ = _fetcher({"api.gdeltproject.org": (429, b"")})
        hits, report = await search_one("news", "quake", 5, fetch=fetch)

        assert hits == []
        assert report.status == "blocked"
        assert "one query per 5s" in report.error

    @pytest.mark.asyncio
    async def test_stackexchange_carries_score_answers_and_epoch_day(self):
        fetch, calls = _fetcher({"api.stackexchange.com": (200, _STACK)})
        hits, report = await search_one("qa", "group tasks", 5, fetch=fetch)

        assert report.status == "ok"
        assert "site=stackoverflow" in calls[0]
        hit = hits[0]
        assert hit.url == "https://stackoverflow.com/q/1"
        assert "score 12" in hit.snippet and "3 answers" in hit.snippet
        assert "accepted" in hit.snippet and "python, asyncio" in hit.snippet
        expected = datetime.fromtimestamp(1704164645, tz=timezone.utc).date().isoformat()
        assert (hit.published, hit.published_precision) == (expected, "day")

    @pytest.mark.asyncio
    async def test_github_searches_repositories_by_stars_not_issues(self):
        """Issue 搜索被每日工程日志淹没（实测 619/621/623 同标题）；仓库搜索才是
        "这个项目在哪"的耐久答案，星数还给了质量信号。"""
        fetch, calls = _fetcher({"api.github.com": (200, _GITHUB)})
        hits, report = await search_one("code", "fast api", 5, fetch=fetch)

        assert report.status == "ok"
        assert "/search/repositories" in calls[0]
        assert "sort=stars" in calls[0]
        hit = hits[0]
        assert hit.title == "fastapi/fastapi"
        assert hit.url == "https://github.com/fastapi/fastapi"
        assert "★103268" in hit.snippet and "Python" in hit.snippet
        assert hit.published == "2018-12-08"

    @pytest.mark.asyncio
    async def test_github_rate_limit_names_the_unauthenticated_quota(self):
        fetch, _ = _fetcher({"api.github.com": (
            403, json.dumps({"message": "API rate limit exceeded"}).encode())})
        hits, report = await search_one("code", "fast api", 5, fetch=fetch)

        assert hits == []
        assert report.status == "blocked"
        assert "10 requests/min" in report.error
        assert "API rate limit exceeded" in report.error

    @pytest.mark.asyncio
    async def test_hackernews_uses_the_story_url_and_falls_back_to_the_item(self):
        fetch, calls = _fetcher({"hn.algolia.com": (200, _HN)})
        hits, report = await search_one("community", "thing", 5, fetch=fetch)

        assert report.status == "ok"
        assert "tags=story" in calls[0]
        assert hits[0].url == "https://example.test/thing"
        assert "120 points" in hits[0].snippet
        assert hits[1].url == "https://news.ycombinator.com/item?id=77"

    @pytest.mark.asyncio
    async def test_naming_a_source_first_does_not_rank_all_of_it_first(self):
        """没有重排模型时位置参与打分；参数顺序不是权威信号。"""
        fetch, _ = _fetcher({
            "api.stackexchange.com": (200, _STACK),
            "api.github.com": (200, _GITHUB),
        })
        hits, _reports = await search_many(["qa", "code"], "task groups", 3,
                                           fetch=fetch)

        assert [h.backend for h in hits] == ["stackexchange", "github"]

    @pytest.mark.asyncio
    async def test_one_dead_source_does_not_erase_the_other(self):
        async def fetch(url, headers, timeout):
            if "export.arxiv.org" in url:
                return 200, _ARXIV
            raise RuntimeError("boom")

        hits, reports = await search_many(
            ["academic", "news"], "task groups", 2, fetch=fetch)

        # `academic` 现在有两个后端（arXiv + Crossref）：回执按后端各一行。
        # 一个后端倒下不会把同 kind 的另一个也判成失败 —— 这正是这条用例要钉的。
        by_backend = {(r.source, r.backend): r for r in reports}
        assert by_backend[("academic", "arxiv")].status == "ok"
        assert by_backend[("academic", "crossref")].status == "error"
        assert by_backend[("news", "gdelt")].status == "error"
        assert "RuntimeError" in by_backend[("news", "gdelt")].error
        assert [h.backend for h in hits] == ["arxiv", "arxiv"]

    @pytest.mark.asyncio
    async def test_a_timeout_is_a_timeout_not_an_empty_answer(self):
        async def fetch(url, headers, timeout):
            raise httpx.TimeoutException("no route")

        hits, report = await search_one("community", "thing", 5, timeout=3.5,
                                        fetch=fetch)

        assert hits == []
        assert report.status == "timeout"
        assert "no answer in 3.5s" in report.error

    @pytest.mark.asyncio
    async def test_an_exception_with_no_message_still_names_its_type(self):
        async def fetch(url, headers, timeout):
            raise ConnectionError()

        hits, report = await search_one("community", "thing", 5, fetch=fetch)

        assert hits == []
        assert report.status == "error"
        assert report.error == "ConnectionError"

    @pytest.mark.asyncio
    async def test_a_non_200_is_an_error_with_the_code(self):
        fetch, _ = _fetcher({"hn.algolia.com": (500, b"")})
        hits, report = await search_one("community", "thing", 5, fetch=fetch)

        assert hits == [] and report.status == "error"
        assert report.error == "HTTP 500"


# ─── 2 · the exact row window ────────────────────────────────────────────

class TestTheDateWindow:

    def test_after_drops_older_and_undated_rows_and_counts_both(self):
        hits = [_hit(1, "old", "https://a/1", published="2024-01-01"),
                _hit(2, "kept", "https://a/2", published="2024-06-01"),
                _hit(3, "undated", "https://a/3", published="", precision="")]
        kept, ledger = filter_by_window(hits, after=datetime(2024, 3, 1).date())

        assert [h.title for h in kept] == ["kept"]
        assert ledger == {"dropped_after": 1, "dropped_before": 0,
                          "undated_dropped_after": 1}

    def test_before_drops_only_provably_newer_rows(self):
        hits = [_hit(1, "new", "https://a/1", published="2024-12-01"),
                _hit(2, "kept", "https://a/2", published="2024-01-01"),
                _hit(3, "undated", "https://a/3", published="", precision="")]
        kept, ledger = filter_by_window(hits, before=datetime(2024, 6, 1).date())

        assert [h.title for h in kept] == ["kept", "undated"]
        assert ledger["dropped_before"] == 1
        assert ledger["dropped_after"] == 0

    def test_a_month_or_year_floor_is_not_a_provable_day(self):
        """`published_precision=year` 的日期是地板不是声明；行级窗口只比较
        precision=day 的行，别把 1 月 1 日的地板读成"确实在窗口内"。"""
        hits = [_hit(1, "year", "https://a/1", published="2024-01-01",
                     precision="year")]
        kept, ledger = filter_by_window(hits, after=datetime(2024, 3, 1).date())

        assert kept == []
        assert ledger["undated_dropped_after"] == 1


# ─── 3 · merge, dedup, consensus, sort ───────────────────────────────────

class TestTheMerge:

    def test_vertical_rows_have_no_consensus_and_keep_their_kind(self):
        out = _merge_vertical_hits([], [_hit()])

        assert len(out) == 1
        assert out[0].vertical is True
        assert out[0].vertical_kind == "academic"
        assert out[0].consensus == 0
        assert out[0].sources == ("arxiv",)

    def test_a_url_in_both_pools_keeps_the_web_row_and_gains_the_backend(self):
        web = _web("https://example.test/a?utm_source=x", "Web title", consensus=3)
        vertical = _hit(url="https://example.test/a", title="Vertical title")

        out = _merge_vertical_hits([web], [vertical])

        assert len(out) == 1
        row = out[0]
        assert row.title == "Web title"
        assert row.consensus == 3
        assert row.vertical is False
        assert row.sources == ("brave", "bing", "arxiv")

    def test_the_same_story_on_one_host_is_deduped_by_title(self):
        first = _hit(1, "Same  Title", "https://news.example.test/a")
        again = _hit(2, "same title", "https://news.example.test/b")
        other_host = _hit(3, "Same Title", "https://other.example.test/c")
        different = _hit(4, "Another story", "https://news.example.test/d")

        out = _merge_vertical_hits([], [first, again, other_host, different])

        assert [r.url for r in out] == [
            "https://news.example.test/a",
            "https://other.example.test/c",
            "https://news.example.test/d",
        ]

    def test_web_rows_are_not_title_deduped(self):
        """两个索引交回同一标题是我们想要的信号（共识），不是重复。"""
        rows = [
            RawResult(title="Same title", url="https://a.example.test/1",
                      snippet="", source="brave", position=1, consensus=1,
                      sources=("brave",)),
            RawResult(title="Same title", url="https://a.example.test/2",
                      snippet="", source="bing", position=2, consensus=1,
                      sources=("bing",)),
        ]
        out = _merge_vertical_hits(rows, [])

        assert len(out) == 2

    def test_diversity_does_not_cap_a_vertical_sources_single_host(self):
        """`sources=['qa']` 的每一行都在 stackoverflow.com —— 源就是主机。
        网页池的"同域最多 2 条"在这里会把 8 条问答悄悄压成 2 条。"""
        web = [RawResult(title=f"w{i}", url=f"https://a.example.test/{i}",
                         snippet="task groups", source="brave", position=i,
                         consensus=1, sources=("brave",))
               for i in range(1, 4)]
        qa = [_hit(i, f"task groups answer {i}",
                   f"https://stackoverflow.com/q/{i}",
                   kind="qa", backend="stackexchange") for i in range(1, 5)]
        ranked = _merge_vertical_hits(web, qa)

        kept, scores = search_mod._diversify(ranked, [0.9] * len(ranked))

        assert [r.url for r in kept if getattr(r, "vertical", False)] == [
            h.url for h in qa]
        assert kept[-1].url == "https://a.example.test/3"  # the web overflow row
        assert len(scores) == len(kept)

    def test_delivered_rows_carry_an_empty_consensus_after_build(self):
        ranked = _merge_vertical_hits([_web()], [_hit()])
        built = search_mod._build_results("q", ranked, [0.9, 0.8], 1)

        web = [r for r in built if r.source != "arxiv"][0]
        assert web.engines_consensus == "1 of 1 (no corroboration)"
        vertical = [r for r in built if r.source == "arxiv"][0]
        assert vertical.engines_consensus == ""
        assert vertical.source == "arxiv"


class TestTheSort:

    def test_newest_orders_stated_days_and_leaves_the_rest_after(self):
        rows = [_hit(1, "old", "https://a/1", published="2024-01-01"),
                _hit(2, "new", "https://a/2", published="2024-09-01"),
                _hit(3, "month", "https://a/3", published="2024-08-01",
                     precision="month"),
                _hit(4, "none", "https://a/4", published="", precision="")]
        ordered, scores, dated, undated = _sort_newest(
            rows, [0.1, 0.2, 0.3, 0.4])

        assert [r.title for r in ordered] == ["new", "old", "month", "none"]
        assert scores == [0.2, 0.1, 0.3, 0.4]
        assert (dated, undated) == (2, 2)

    def test_undated_rows_keep_their_relative_order(self):
        rows = [_hit(1, "u1", "https://a/1", published="", precision=""),
                _hit(2, "u2", "https://a/2", published="", precision="")]
        ordered, _, dated, undated = _sort_newest(rows, [0.1, 0.2])

        assert [r.title for r in ordered] == ["u1", "u2"]
        assert (dated, undated) == (0, 2)


class TestTheConsensusGate:

    def test_vertical_rows_pass_because_the_gate_has_nothing_to_judge(self):
        web = search_mod.SearchResult(title="web", url="https://a/1",
                                      engines_consensus="1 of 6",
                                      fetch_relevance="high")
        vertical = search_mod.SearchResult(title="paper", url="https://a/2",
                                           engines_consensus="",
                                           fetch_relevance="high")
        kept, skipped, note = gate_by_consensus([web, vertical], 2)

        assert kept == [vertical]
        assert skipped == [web]
        assert "fetched 1" in note and "skipped 1" in note


    def test_vertical_rows_get_their_own_not_applied_reason(self):
        vertical = search_mod.SearchResult(title="paper", url="https://a/1",
                                           engines_consensus="",
                                           fetch_relevance="high")
        kept, skipped, note = gate_by_consensus([vertical], 2)

        assert kept == [vertical] and skipped == []
        assert "vertical sources" in note
        assert "single index family" not in note


class TestTheSourceReceipt:

    def test_count_and_delivered_are_two_different_numbers(self):
        detail = [{"source": "academic", "backend": "arxiv", "status": "ok",
                   "count": 6, "delivered": 0, "duration_ms": 12.0}]
        note = _source_note(["academic"], detail)

        assert "academic=arxiv 6 row(s), 0 in this response" in note

    def test_equal_counts_do_not_repeat_the_number(self):
        detail = [{"source": "qa", "backend": "stackexchange", "status": "ok",
                   "count": 2, "delivered": 2}]
        note = _source_note(["qa"], detail)

        assert "qa=stackexchange 2 row(s)" in note
        assert "in this response" not in note

    def test_failure_note_names_only_the_sources_that_did_not_answer(self):
        detail = [
            {"source": "qa", "backend": "stackexchange", "status": "ok", "count": 2},
            {"source": "news", "backend": "gdelt", "status": "blocked",
             "count": 0, "error": "HTTP 429"},
        ]
        note = _source_failure_note(detail)

        assert "news=gdelt blocked" in note
        assert "qa=" not in note

    def test_the_dead_end_says_which_source_was_asked_and_how_it_failed(self):
        note = _vertical_dead_end([
            {"source": "news", "backend": "gdelt", "status": "timeout",
             "error": "no answer in 8s"}])

        assert "news=gdelt timeout" in note
        assert "try sources=['web']" in note

    def test_a_task_that_died_before_reporting_is_not_called_unasked(self):
        note = _vertical_dead_end([], "RuntimeError: fan-out died")

        assert "failed before any source reported" in note
        assert "RuntimeError: fan-out died" in note
        assert "no vertical source was asked" not in note

    def test_a_web_only_round_pays_nothing_for_the_vertical_receipt(self):
        from dhole_mcp.wire import _wire_dump
        from dhole_mcp.search import SearchResponseModel

        payload = _wire_dump(SearchResponseModel(query="q", results=[]))

        assert "sources_used" not in payload
        assert "source_detail" not in payload
        assert "sort_applied" not in payload

    def test_a_vertical_round_puts_its_receipt_on_the_wire(self):
        from dhole_mcp.wire import _wire_dump
        from dhole_mcp.search import SearchResponseModel

        payload = _wire_dump(SearchResponseModel(
            query="q", results=[], sources_used=["arxiv"],
            source_detail=[{"source": "academic", "backend": "arxiv",
                            "status": "ok", "count": 2, "delivered": 1,
                            "duration_ms": 12.0}],
            sort_applied="newest"))

        assert payload["sources_used"] == ["arxiv"]
        assert payload["source_detail"][0]["delivered"] == 1
        assert payload["sort_applied"] == "newest"


# ─── 4 · validation ──────────────────────────────────────────────────────

class TestValidation:

    def test_default_is_the_web_pool(self):
        assert _validate_sources(None) == ["web"]
        assert _validate_sources([]) == ["web"]

    def test_a_bare_string_is_the_one_pool_it_names(self):
        assert _validate_sources("academic") == ["academic"]

    def test_duplicates_and_noise_collapse(self):
        assert _validate_sources([" academic ", "ACADEMIC", "news"]) == [
            "academic", "news"]

    def test_an_unknown_pool_is_refused_with_every_value_named(self):
        with pytest.raises(Exception) as exc:
            _validate_sources(["academic", "blogs"])
        message = str(exc.value)
        assert "blogs" in message
        for value in sv.SOURCE_KINDS:
            assert value in message

    def test_sort_accepts_only_its_two_values(self):
        assert _validate_sort(None) == "relevance"
        assert _validate_sort(" NEWEST ") == "newest"
        with pytest.raises(Exception) as exc:
            _validate_sort("oldest")
        message = str(exc.value)
        assert "oldest" in message
        assert "relevance" in message and "newest" in message


# ─── 5 · integration through server.web_search ───────────────────────────

class TestTheCall:

    @pytest.mark.asyncio
    async def test_vertical_only_never_touches_the_web_pool(self, monkeypatch):
        called = []

        async def no_web(*args, **kwargs):
            called.append("web")
            raise AssertionError("the web pool must not run for sources=['academic']")

        monkeypatch.setattr(search_mod, "multi_search", no_web)
        _force_neural(monkeypatch)

        async def fake_many(kinds, query, max_results, **kwargs):
            assert kinds == ["academic"]
            return [_hit()], [VerticalReport("academic", "arxiv", "ok", 1)]

        monkeypatch.setattr(sv, "search_many", fake_many)

        out = await _server().web_search(
            "task groups", sources=["academic"], cache_ttl=0)

        assert called == []
        assert [r.source for r in out.results] == ["arxiv"]
        assert out.results[0].engines_consensus == ""
        assert out.sources_used == ["arxiv"]
        assert out.source_detail[0]["delivered"] == 1
        assert out.source_detail[0]["count"] == 1
        assert out.engine_detail == []
        assert "from arxiv" in out.summary
        assert "vertical sources" in out.next_action
        assert "cross-engine consensus" not in out.next_action

    @pytest.mark.asyncio
    async def test_sort_newest_says_when_nothing_states_a_day(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        _force_neural(monkeypatch)

        async def fake_many(kinds, query, max_results, **kwargs):
            return [_hit(published="", precision="")], [
                VerticalReport("academic", "arxiv", "ok", 1)]

        monkeypatch.setattr(sv, "search_many", fake_many)

        out = await _server().web_search(
            "task groups", sources=["academic"], sort="newest", cache_ttl=0)

        assert "no delivered row states a publish day" in out.summary
        assert "keep their relevance order" in out.summary

    @pytest.mark.asyncio
    async def test_all_rows_from_a_single_host_vertical_source_are_delivered(
            self, monkeypatch):
        hits = [_hit(i, f"task groups answer {i}",
                     f"https://stackoverflow.com/q/{i}",
                     kind="qa", backend="stackexchange")
                for i in range(1, 5)]
        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        _force_neural(monkeypatch)

        async def fake_many(kinds, query, max_results, **kwargs):
            return hits, [VerticalReport("qa", "stackexchange", "ok", 4)]

        monkeypatch.setattr(sv, "search_many", fake_many)

        out = await _server().web_search(
            "task groups", sources=["qa"], max_results=4, cache_ttl=0)

        assert len(out.results) == 4
        assert out.source_detail[0]["delivered"] == 4

    @pytest.mark.asyncio
    async def test_a_mixed_round_keeps_the_web_consensus_on_web_rows(
            self, monkeypatch):
        async def fake_multi(query, max_results, **kwargs):
            rows = [_web(consensus=2)]
            return rows, [EngineReport(name=n, ok=True)
                          for n in ("brave", "bing", "baidu")]

        monkeypatch.setattr(search_mod, "multi_search", fake_multi)
        _force_neural(monkeypatch)

        async def fake_many(kinds, query, max_results, **kwargs):
            return [_hit()], [VerticalReport("academic", "arxiv", "ok", 1)]

        monkeypatch.setattr(sv, "search_many", fake_many)

        out = await _server().web_search(
            "task groups", sources=["web", "academic"], cache_ttl=0)

        by_source = {r.source: r for r in out.results}
        web = next(r for r in out.results if "brave" in r.source)
        assert web.engines_consensus.startswith("2 of ")
        assert by_source["arxiv"].engines_consensus == ""
        assert out.sources_used == ["arxiv"]

    @pytest.mark.asyncio
    async def test_newest_reports_what_was_delivered_not_what_was_candidates(
            self, monkeypatch):
        hits = [
            _hit(1, "jan", "https://h1.example.test/1", published="2024-01-01"),
            _hit(2, "sep", "https://h2.example.test/2", published="2024-09-01"),
            _hit(3, "may", "https://h3.example.test/3", published="2024-05-01"),
            _hit(4, "year-only", "https://h4.example.test/4", published="2024-01-01",
                 precision="year"),
        ]
        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        _force_neural(monkeypatch)

        async def fake_many(kinds, query, max_results, **kwargs):
            return hits, [VerticalReport("academic", "arxiv", "ok", 4)]

        monkeypatch.setattr(sv, "search_many", fake_many)

        out = await _server().web_search(
            "task groups", sources=["academic"], sort="newest",
            max_results=2, cache_ttl=0)

        assert [r.title for r in out.results] == ["sep", "may"]
        assert out.sort_applied == "newest"
        assert "delivered 2 dated row(s)" in out.summary
        assert "0 undated/coarser" in out.summary

    @pytest.mark.asyncio
    async def test_the_vertical_date_window_is_exact_and_counted(self, monkeypatch):
        hits = [
            _hit(1, "old", "https://h1.example.test/1", published="2024-01-01"),
            _hit(2, "kept", "https://h2.example.test/2", published="2024-06-01"),
            _hit(3, "undated", "https://h3.example.test/3", published="",
                 precision=""),
        ]
        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        _force_neural(monkeypatch)

        async def fake_many(kinds, query, max_results, **kwargs):
            return hits, [VerticalReport("academic", "arxiv", "ok", 3)]

        monkeypatch.setattr(sv, "search_many", fake_many)

        out = await _server().web_search(
            "task groups", sources=["academic"], after="2024-03-01",
            cache_ttl=0)

        assert [r.title for r in out.results] == ["kept"]
        assert out.source_detail[0]["count"] == 1
        assert "vertical date window dropped 1 older than after=" in out.summary
        assert "1 undated dropped by after=" in out.summary

    @pytest.mark.asyncio
    async def test_a_vertical_source_filtered_to_zero_says_empty(self, monkeypatch):
        async def fake_many(kinds, query, max_results, **kwargs):
            return [_hit(url="https://elsewhere.test/a")], [
                VerticalReport("academic", "arxiv", "ok", 1)]

        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        monkeypatch.setattr(sv, "search_many", fake_many)
        _force_neural(monkeypatch)

        out = await _server().web_search(
            "task groups", sources=["academic"], site="example.test", cache_ttl=0)

        assert out.results == []
        assert out.sources_used == []
        row = out.source_detail[0]
        assert row["status"] == "empty"
        assert row["error"].startswith("source_filtered:")
        assert "removed by site=" in row["error"]

    @pytest.mark.asyncio
    async def test_the_receipt_replays_from_cache_with_sources_and_sort(
            self, monkeypatch):
        calls = []

        async def fake_many(kinds, query, max_results, **kwargs):
            calls.append(kinds)
            return [_hit()], [VerticalReport("academic", "arxiv", "ok", 1)]

        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        monkeypatch.setattr(sv, "search_many", fake_many)
        _force_neural(monkeypatch)

        first = await _server().web_search(
            "task groups", sources=["academic"], sort="newest", cache_ttl=60)
        second = await _server().web_search(
            "task groups", sources=["academic"], sort="newest", cache_ttl=60)

        assert len(calls) == 1
        assert first.cached is False and second.cached is True
        assert second.sources_used == ["arxiv"]
        assert second.source_detail == first.source_detail
        assert second.sort_applied == "newest"
        assert "replayed from cache" in second.summary
        assert "delivered 1 dated row(s)" in second.summary

    @pytest.mark.asyncio
    async def test_a_different_sort_does_not_reuse_the_newest_cache_entry(
            self, monkeypatch):
        calls = []

        async def fake_many(kinds, query, max_results, **kwargs):
            calls.append(kinds)
            return [_hit()], [VerticalReport("academic", "arxiv", "ok", 1)]

        monkeypatch.setattr(search_mod, "multi_search", AsyncMock())
        monkeypatch.setattr(sv, "search_many", fake_many)
        _force_neural(monkeypatch)

        await _server().web_search(
            "task groups", sources=["academic"], sort="newest", cache_ttl=60)
        out = await _server().web_search(
            "task groups", sources=["academic"], sort="relevance", cache_ttl=60)

        assert out.cached is False
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_engines_without_the_web_pool_is_refused_before_any_call(
            self, monkeypatch):
        monkeypatch.setattr(sv, "search_many", AsyncMock())
        out = await _server().web_search(
            "task groups", sources=["qa"], engines=["bing"], cache_ttl=0)

        assert out.results == []
        assert "engines= only applies to sources=['web']" in out.error
        sv.search_many.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_find_similar_refuses_vertical_pools(self):
        out = await _server().web_search(
            mode="find_similar", url="https://example.com/",
            sources=["academic"], cache_ttl=0)

        assert out.results == []
        assert "find_similar runs on the web pool only" in out.error

    @pytest.mark.asyncio
    async def test_an_unknown_pool_is_refused_by_dhole_with_the_value_list(self):
        out = await _server().web_search(
            "task groups", sources=["blogs"], cache_ttl=0)

        assert out.results == []
        assert "blogs" in out.error
        for value in sv.SOURCE_KINDS:
            assert value in out.error
