"""开发者纵向源：`package` / `repo`，以及 `academic` 的 Crossref 后端。

三条判据：

1. **精确名，不猜关键词**：带空格的查询在任何一个 registry 上都不是包名，
   一个请求都不发，返回 `bad_query` 并指回普通 web 池。`repo=` 只收
   `owner/repo` 或 github.com URL。
2. **失败按后端各说各的**：未知包是 `empty` 不是 `error`；GitHub 404 是
   `not_found`；限流是 `blocked`（并且不伪装成 empty）。`academic` 的
   arXiv 与 Crossref 互不拖累，同标题只留先到的 arXiv。
3. **行形状与既有竖源一致**：title/url/snippet/published 齐全，published 是
   真实发布日而不是抓取日；只有年份的 Crossref 日期降精度，不冒充"1 月 1 日"。
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from dhole_mcp import search_verticals as sv
from dhole_mcp.search_verticals import search_many, search_one

pytestmark = pytest.mark.smoke


_CROSSREF = json.dumps({"message": {"items": [{
    "DOI": "10.1000/xyz",
    "title": ["Task Groups for Everyone"],
    "author": [{"given": "Ada", "family": "Lovelace"},
               {"given": "Alan", "family": "Turing"}],
    "abstract": "<jats:p>We study structured concurrency.</jats:p>",
    "issued": {"date-parts": [[2024, 1, 2]]},
    "URL": "https://doi.org/10.1000/xyz",
    "container-title": ["Journal of Tasks"],
}, {
    "DOI": "10.1000/abc",
    "title": ["A Crossref Only Paper"],
    "author": [{"given": "Grace", "family": "Hopper"}],
    "issued": {"date-parts": [[2024]]},
    "URL": "https://doi.org/10.1000/abc",
    "container-title": ["Journal of Compilers"],
}]}}).encode()

_ARXIV = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Task Groups for Everyone</title>
    <summary>We study structured concurrency.</summary>
    <published>2024-01-02T03:04:05Z</published>
    <author><name>Ada Lovelace</name></author>
  </entry>
</feed>"""

_NPM = json.dumps({
    "dist-tags": {"latest": "5.0.0"},
    "versions": {"5.0.0": {
        "license": "MIT", "description": "Promise based HTTP client",
        "dependencies": {"follow-redirects": "^1.0.0"},
        "repository": {"url": "git+https://github.com/axios/axios.git"},
    }},
    "time": {"5.0.0": "2026-01-02T03:04:05Z"},
}).encode()

_PYPI = json.dumps({
    "info": {"name": "requests", "version": "2.32.3",
             "summary": "Python HTTP for Humans.",
             "license": "Apache-2.0",
             "requires_dist": ["urllib3>=1.21", "certifi>=2017",
                               "chardet; extra == 'use-chardet'"],
             "project_urls": {"Source": "https://github.com/psf/requests"}},
    "releases": {"2.32.3": [{"upload_time_iso_8601": "2024-05-29T00:00:00Z"}]},
}).encode()

_CRATES = json.dumps({
    "crate": {"name": "serde", "downloads": 1234567,
              "description": "A serialization framework",
              "repository": "https://github.com/serde-rs/serde"},
    "versions": [{"num": "1.0.200", "license": "MIT OR Apache-2.0",
                  "created_at": "2024-03-01T00:00:00Z"}],
}).encode()

_GO = json.dumps({"Version": "v1.10.0",
                  "Time": "2025-05-01T00:00:00Z"}).encode()

_REPO = json.dumps({
    "full_name": "fastapi/fastapi",
    "html_url": "https://github.com/fastapi/fastapi",
    "description": "FastAPI framework, high performance",
    "stargazers_count": 103268, "forks_count": 8930,
    "open_issues_count": 512, "pushed_at": "2026-10-20T00:00:00Z",
    "license": {"spdx_id": "MIT"}, "language": "Python", "archived": False,
}).encode()

_COMMITS = json.dumps([{
    "commit": {"committer": {"date": "2026-10-26T05:06:07Z"}}}]).encode()


def _fetcher(routes: dict[str, tuple[int, bytes]]):
    calls: list[str] = []

    async def fetch(url: str, headers: dict, timeout: float):
        calls.append(url)
        for needle, response in routes.items():
            if needle in url:
                return response
        raise AssertionError(f"unexpected URL: {url}")

    return fetch, calls


def _not_called():
    calls: list[str] = []

    async def fetch(url: str, headers: dict, timeout: float):
        calls.append(url)
        raise AssertionError(f"no request was expected, got {url}")

    return fetch, calls


# ─── 1 · package: exact names ─────────────────────────────────────────────

class TestThePackageSource:

    @pytest.mark.asyncio
    async def test_npm_returns_version_license_deps_and_repo(self):
        fetch, calls = _fetcher({"registry.npmjs.org/axios": (200, _NPM)})
        hits, report = await search_one("package", "axios", 3, fetch=fetch)
        # search_one 只问主后端（npm）；四个 registry 走 search_many。
        assert report.status == "ok" and report.backend == "npm"
        row = hits[0]
        assert row.title == "axios (npm)"
        assert row.url == "https://www.npmjs.com/package/axios"
        assert "v5.0.0" in row.snippet and "MIT" in row.snippet
        assert "deps=1" in row.snippet
        assert "github.com/axios/axios" in row.snippet
        assert row.published == "2026-01-02"
        assert "registry.npmjs.org/axios" in calls[0]

    @pytest.mark.asyncio
    async def test_pypi_returns_the_release_day_and_counts_extra_free_deps(self):
        fetch, _ = _fetcher({"pypi.org/pypi/requests/json": (200, _PYPI)})
        from dhole_mcp.search_verticals import _search_pypi
        hits, backend, status, error = await _search_pypi(
            "requests", 3, 0, "us-en", None, 8.0, fetch)
        row = hits[0]
        assert status == "ok" and backend == "pypi" and error == ""
        assert row.title == "requests (PyPI)"
        assert row.url == "https://pypi.org/project/requests/"
        assert "v2.32.3" in row.snippet and "Apache-2.0" in row.snippet
        assert "deps=2" in row.snippet, "extra-only deps 不该算进运行依赖"
        assert row.published == "2024-05-29"

    @pytest.mark.asyncio
    async def test_crates_and_go_answer_their_own_shapes(self):
        fetch, _ = _fetcher({
            "crates.io/api/v1/crates/serde": (200, _CRATES),
            "proxy.golang.org/github.com/gin-gonic/gin/@latest": (200, _GO),
        })
        from dhole_mcp.search_verticals import _search_crates, _search_go
        crates, backend, status, _ = await _search_crates(
            "serde", 3, 0, "us-en", None, 8.0, fetch)
        go, backend_go, status_go, _ = await _search_go(
            "github.com/gin-gonic/gin", 3, 0, "us-en", None, 8.0, fetch)

        assert (backend, status) == ("crates", "ok")
        assert crates[0].title == "serde (crates.io)"
        assert "1234567 downloads" in crates[0].snippet
        assert (backend_go, status_go) == ("go", "ok")
        assert go[0].title == "github.com/gin-gonic/gin (Go)"
        assert go[0].url == "https://pkg.go.dev/github.com/gin-gonic/gin"
        assert go[0].published == "2025-05-01"

    @pytest.mark.asyncio
    async def test_a_keyword_query_is_refused_by_every_registry_without_a_request(self):
        fetch, calls = _not_called()
        hits, reports = await search_many(["package"], "http client library", 3,
                                          fetch=fetch)
        assert hits == []
        assert calls == []
        assert {r.backend for r in reports} == {
            backend for backend, _adapter in sv._KIND_ADAPTERS["package"]}
        assert all(r.status == "bad_query" for r in reports), [
            (r.backend, r.status) for r in reports]
        assert all("exact package name" in r.error for r in reports)

    @pytest.mark.asyncio
    async def test_an_unknown_package_is_empty_not_an_error(self):
        fetch, _ = _fetcher({
            "registry.npmjs.org/no-such-pkg": (404, b"{}"),
            "pypi.org/pypi/no-such-pkg/json": (404, b"{}"),
            "crates.io/api/v1/crates/no-such-pkg": (404, b"{}"),
        })
        hits, reports = await search_many(["package"], "no-such-pkg", 3, fetch=fetch)
        assert hits == []
        statuses = {(r.backend, r.status) for r in reports}
        assert ("npm", "empty") in statuses
        assert ("pypi", "empty") in statuses
        assert ("crates", "empty") in statuses
        assert ("go", "empty") in statuses  # slashless Go name: explained, no request

    @pytest.mark.asyncio
    async def test_a_slashless_go_name_says_what_a_module_path_looks_like(self):
        fetch, calls = _not_called()
        from dhole_mcp.search_verticals import _search_go
        go, _backend, status, error = await _search_go(
            "serde", 3, 0, "us-en", None, 8.0, fetch)
        assert go == [] and status == "empty"
        assert "full path" in error
        assert calls == []


# ─── 2 · repo: one repository's health ────────────────────────────────────

class TestTheRepoSource:

    @pytest.mark.asyncio
    async def test_a_repo_row_carries_stars_issues_commit_and_license(self):
        fetch, calls = _fetcher({
            "commits": (200, _COMMITS),
            "api.github.com/repos/fastapi/fastapi": (200, _REPO),
        })
        hits, report = await search_one("repo", "fastapi/fastapi", 3, fetch=fetch)
        row = hits[0]
        assert report.status == "ok" and report.backend == "github_repo"
        assert row.title == "fastapi/fastapi"
        assert row.url == "https://github.com/fastapi/fastapi"
        assert "★103268" in row.snippet and "8930 forks" in row.snippet
        assert "512 open issues" in row.snippet
        assert "last commit 2026-10-26" in row.snippet
        assert "MIT" in row.snippet and "Python" in row.snippet
        assert row.published == "2026-10-26"
        assert any("/commits" in call for call in calls)

    @pytest.mark.asyncio
    async def test_a_github_url_is_accepted_as_the_same_selector(self):
        fetch, _ = _fetcher({
            "commits": (200, _COMMITS),
            "api.github.com/repos/fastapi/fastapi": (200, _REPO),
        })
        hits, report = await search_one(
            "repo", "https://github.com/fastapi/fastapi", 3, fetch=fetch)
        assert report.status == "ok" and hits[0].title == "fastapi/fastapi"

    @pytest.mark.asyncio
    async def test_a_missing_repo_is_not_found_not_an_error(self):
        fetch, _ = _fetcher({"api.github.com/repos/no/such": (404, b"{}")})
        hits, report = await search_one("repo", "no/such", 3, fetch=fetch)
        assert hits == []
        assert report.status == "not_found"
        assert "does not exist" in report.error

    @pytest.mark.asyncio
    async def test_a_rate_limit_is_blocked_and_names_the_quota(self):
        fetch, _ = _fetcher({
            "api.github.com/repos/fastapi/fastapi": (
                403, json.dumps({"message": "API rate limit exceeded"}).encode()),
        })
        hits, report = await search_one("repo", "fastapi/fastapi", 3, fetch=fetch)
        assert hits == []
        assert report.status == "blocked"
        assert "60 requests/hour" in report.error
        assert "API rate limit exceeded" in report.error

    @pytest.mark.asyncio
    async def test_keywords_are_refused_before_any_request(self):
        fetch, calls = _not_called()
        hits, report = await search_one("repo", "fast api framework", 3, fetch=fetch)
        assert hits == [] and calls == []
        assert report.status == "bad_query"
        assert "owner/repo" in report.error


# ─── 3 · academic: arXiv + Crossref ───────────────────────────────────────

class TestTheAcademicSource:

    @pytest.mark.asyncio
    async def test_every_academic_backend_answers_and_a_shared_title_is_kept_once(self):
        fetch, _ = _fetcher({
            "export.arxiv.org": (200, _ARXIV),
            "api.crossref.org": (200, _CROSSREF),
            "api.openalex.org": (200, b'{"results": []}'),
        })
        hits, reports = await search_many(["academic"], "task groups", 5,
                                          fetch=fetch)
        by_backend = {r.backend: r for r in reports}
        assert {"arxiv", "crossref", "openalex"} <= set(by_backend)
        assert by_backend["arxiv"].status == "ok"
        assert by_backend["crossref"].status == "ok"
        assert by_backend["openalex"].status == "empty"
        titles = [h.title for h in hits]
        assert titles.count("Task Groups for Everyone") == 1, "同标题只留 arXiv"
        crossref_only = [h for h in hits if h.backend == "crossref"]
        assert [h.title for h in crossref_only] == ["A Crossref Only Paper"]

    @pytest.mark.asyncio
    async def test_a_year_only_crossref_date_is_not_a_day(self):
        fetch, _ = _fetcher({"api.crossref.org": (200, _CROSSREF)})
        from dhole_mcp.search_verticals import _search_crossref
        hits, _backend, status, _ = await _search_crossref(
            "task groups", 5, 0, "us-en", None, 8.0, fetch)
        year_row = next(h for h in hits if h.title == "A Crossref Only Paper")
        assert status == "ok"
        assert year_row.published == "2024-01-01"
        assert year_row.published_precision == "year", (
            "只有年份的日期是地板，不许冒充精确发布日")

    @pytest.mark.asyncio
    async def test_a_dead_crossref_does_not_erase_arxiv(self):
        async def fetch(url, headers, timeout):
            if "export.arxiv.org" in url:
                return 200, _ARXIV
            raise RuntimeError("crossref is down")

        hits, reports = await search_many(["academic"], "task groups", 3,
                                          fetch=fetch)
        by_backend = {r.backend: r for r in reports}
        assert by_backend["arxiv"].status == "ok"
        assert by_backend["crossref"].status == "error"
        assert [h.backend for h in hits] == ["arxiv"]


# ─── 4 · through web_search ───────────────────────────────────────────────

class TestTheCall:

    @pytest.mark.asyncio
    async def test_package_and_repo_are_real_sources_on_the_search_door(
            self, monkeypatch):
        from dhole_mcp.server import MasterFetchServer
        import dhole_mcp.search as search_mod
        from dhole_mcp.search_engines import EngineReport
        from dhole_mcp.search_verticals import VerticalHit, VerticalReport

        async def fake_multi(*args, **kwargs):
            return [], [EngineReport(name="brave", ok=True)]

        async def fake_many(kinds, query, max_results, **kwargs):
            assert kinds == ["package", "repo"]
            return [
                VerticalHit(kind="package", backend="pypi", rank=1,
                            title="requests (PyPI)", url="https://pypi.org/project/requests/",
                            snippet="v2.32.3", published="2024-05-29",
                            published_precision="day", published_basis="fixture"),
                VerticalHit(kind="repo", backend="github_repo", rank=1,
                            title="psf/requests", url="https://github.com/psf/requests",
                            snippet="★53000", published="2026-10-26",
                            published_precision="day", published_basis="fixture"),
            ], [VerticalReport("package", "pypi", "ok", 1),
                VerticalReport("repo", "github_repo", "ok", 1)]

        monkeypatch.setattr(search_mod, "multi_search", fake_multi)
        monkeypatch.setattr("dhole_mcp.search_verticals.search_many", fake_many)
        out = await MasterFetchServer().web_search(
            "requests", sources=["package", "repo"], cache_ttl=0)

        by_source = {r.source: r for r in out.results}
        assert by_source["pypi"].engines_consensus == ""
        assert by_source["github_repo"].engines_consensus == ""
        assert set(out.sources_used) == {"pypi", "github_repo"}
        assert {row["source"] for row in out.source_detail} == {"package", "repo"}

    def test_the_new_kinds_are_in_the_refused_value_list(self):
        from dhole_mcp.search import _validate_sources

        with pytest.raises(Exception) as exc:
            _validate_sources(["blogs"])
        assert "package" in str(exc.value) and "repo" in str(exc.value)

    def test_a_repo_row_sorted_newest_uses_its_last_commit_day(self):
        from dhole_mcp.search import _sort_newest
        from dhole_mcp.search_engines import RawResult

        rows = [
            RawResult(title="a", url="https://github.com/a/a", snippet="",
                      source="github", position=0, consensus=0,
                      sources=("github",), published="2024-01-01",
                      published_precision="day", vertical=True,
                      vertical_kind="repo"),
            RawResult(title="b", url="https://github.com/b/b", snippet="",
                      source="github", position=0, consensus=0,
                      sources=("github",), published="2026-10-26",
                      published_precision="day", vertical=True,
                      vertical_kind="repo"),
        ]
        ordered, _scores, dated, undated = _sort_newest(rows, [0.5, 0.4])
        assert [r.title for r in ordered] == ["b", "a"]
        assert (dated, undated) == (2, 0)
        assert date.fromisoformat(ordered[0].published).year == 2026
