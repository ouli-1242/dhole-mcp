"""`dhole read|search|crawl|recall` 的渲染器与命令层契约。

两条都不联网：渲染器吃假回执；命令层把 `ops._run_tool` 换成假函数，只量
stdout/stderr 的分工、参数透传与退出码 0/1/2。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import cli_render as render
from dhole_mcp import ops

pytestmark = pytest.mark.smoke


def _read(**over) -> dict:
    payload = {
        "url": "https://example.com/a",
        "status": 200,
        "content": ["# Hello", "world"],
        "content_ok": True,
        "fetcher_used": "http",
        "duration_ms": 5200,
        "error": "",
        "next_offset": 0,
    }
    payload.update(over)
    return payload


# ── 渲染器：stdout 是内容，stderr 是元数据 ────────────────────────────────


def test_read_joins_blocks_and_puts_the_receipt_on_stderr():
    body, meta = render.read(_read())
    assert body == "# Hello\nworld"
    assert "example.com" in meta and "http" in meta
    assert "13 chars" in meta and "5.2s" in meta


def test_read_names_the_next_offset_as_a_command():
    _, meta = render.read(_read(next_offset=42))
    assert "--offset 42" in meta


def test_read_without_content_is_exit_one_and_says_why():
    payload = _read(content=[], content_ok=False,
                    error="network_error: connection refused")
    body, meta = render.read(payload)
    assert body == ""
    assert "network_error" in meta
    assert render.exit_code("read", payload) == 1


def test_a_non_empty_error_is_exit_one_even_if_content_ok_is_true():
    assert render.exit_code("read", _read(error="partial: 3 blocks dropped")) == 1


def test_read_bulk_renders_one_section_per_page():
    payload = {
        "total": 2, "successful": 1, "usable": 1,
        "results": [
            _read(url="https://a.test/x", content=["alpha"]),
            _read(url="https://b.test/y", content=[], content_ok=False,
                  error="http_error_404", duration_ms=1200),
        ],
    }
    body, meta = render.read(payload)
    assert "## https://a.test/x\nalpha" in body
    assert "https://b.test/y" not in body, "失败页不往 stdout 里塞空节"
    assert "2 pages" in meta and "1 usable" in meta
    assert "failed: https://b.test/y" in meta and "http_error_404" in meta
    assert render.exit_code("read", payload) == 0


def test_read_bulk_with_no_usable_page_is_exit_one():
    payload = {"total": 1, "usable": 0,
               "results": [_read(content=[], content_ok=False, error="boom")]}
    assert render.exit_code("read", payload) == 1


def test_search_prints_title_url_and_snippet_per_row():
    payload = {
        "query": "python asyncio",
        "results": [
            {"position": 1, "title": "Asyncio docs", "url": "https://docs.python.org/3/",
             "snippet": "Coroutines and tasks."},
            {"position": 2, "title": "A tutorial", "url": "https://example.com/t",
             "snippet": ""},
        ],
        "engines_used": ["baidu", "bing"],
        "rerank_model": "bge-zh",
        "duration_ms": 4500,
    }
    body, meta = render.search(payload)
    assert body.splitlines() == [
        "1. Asyncio docs",
        "   https://docs.python.org/3/",
        "   Coroutines and tasks.",
        "2. A tutorial",
        "   https://example.com/t",
    ]
    assert "2 results" in meta and "baidu,bing" in meta
    assert "rerank bge-zh" in meta and "4.5s" in meta
    assert render.exit_code("search", payload) == 0


def test_search_error_is_exit_one():
    payload = {"query": "q", "results": [], "error": "no engine answered"}
    body, meta = render.search(payload)
    assert body == ""
    assert "no engine answered" in meta
    assert render.exit_code("search", payload) == 1


def test_search_shows_vertical_sources_and_per_backend_failures():
    payload = {
        "query": "torvalds/linux",
        "results": [{"position": 1, "title": "torvalds/linux",
                     "url": "https://github.com/torvalds/linux",
                     "snippet": "★253720", "source": "github_repo",
                     "published": "2026-10-28"}],
        "sources_used": ["github_repo"],
        "source_detail": [
            {"source": "repo", "backend": "github_repo", "status": "ok",
             "count": 1, "delivered": 1},
            {"source": "academic", "backend": "arxiv", "status": "blocked",
             "count": 0, "delivered": 0, "error": "HTTP 429"},
        ],
        "requested": 5,
        "delivered": 1,
        "duration_ms": 2600,
    }
    body, meta = render.search(payload)
    assert body.splitlines() == [
        "1. torvalds/linux",
        "   https://github.com/torvalds/linux · github_repo · 2026-10-28",
        "   ★253720",
    ]
    assert "github_repo" in meta
    assert "requested 5" in meta and "delivered 1" in meta
    assert "source arxiv" in meta and "blocked" in meta and "HTTP 429" in meta
    assert "source github_repo" not in meta, "an ok backend needs no row"


def test_search_names_why_a_round_ran_without_the_model():
    payload = {
        "query": "q", "results": [], "duration_ms": 500, "rerank_mode": "merge",
        "fetch_hint": (
            "0 high, 0 med, 0 low. no reranker on this round: order is consensus | "
            "the local reranker is still being fetched (one-time, resumable); this "
            "round ranked by cross-engine consensus."),
    }
    _, meta = render.search(payload)
    assert "still being fetched" in meta
    assert meta.count("still being fetched") == 1


def test_a_failed_response_carries_next_action_without_verbose():
    payload = _read(content=[], content_ok=False,
                    error="pdf_no_text: OCR found nothing",
                    next_action="read a text-layer copy of the document")
    body, meta = render.read(payload)
    assert body == ""
    assert "error: pdf_no_text" in meta
    assert "next_action: read a text-layer copy" in meta


def test_research_cross_check_puts_warnings_and_agreement_on_stderr():
    payload = {
        "evidence": ["[1] https://a.test/1\ntext"],
        "sources": [{"n": 1, "url": "https://a.test/1", "chars": 10}],
        "failed": [],
        "claims": [{"claim": "What causes X?", "verdict": "unverified",
                    "agreement_score": 0.3, "support": ["https://a.test/1"],
                    "oppose": []}],
        "agreement_map": {"green": [], "yellow": ["What causes X?"], "red": []},
        "cross_check_basis": {
            "sources_used": 1, "domains": 1,
            "model": "tfidf-cosine+cjk-ngram",
            "warnings": ["agreement is a cross-source signal, not a fact-check",
                         "the query is an open question"],
        },
    }
    body, meta = render.research(payload)
    assert body == "[1] https://a.test/1\ntext"
    assert "unverified 0.3" in meta
    assert "warning: agreement is a cross-source signal" in meta
    assert "open question" in meta
    assert "agreement: confirmed 0 · unverified 1 · contested 0" in meta
    assert "cross_check_basis: 1 source(s) · 1 domain(s)" in meta


def test_ndjson_research_carries_failed_sources_and_the_cross_check_report():
    payload = {
        "evidence": ["[1] x"],
        "failed": [{"n": 2, "url": "https://b.test", "why": "timeout"}],
        "claims": [{"claim": "c", "verdict": "unverified",
                    "agreement_score": 0.3}],
        "agreement_map": {"green": [], "yellow": ["c"], "red": []},
        "cross_check_basis": {"warnings": ["w1"]},
    }
    rows = render.ndjson_rows("research", payload)
    assert rows[0] == {"type": "evidence", "text": "[1] x"}
    assert rows[1]["type"] == "source_failed" and rows[1]["why"] == "timeout"
    assert rows[2]["type"] == "cross_check"
    assert rows[2]["claims"][0]["verdict"] == "unverified"
    assert rows[2]["warnings"] == ["w1"]


def test_ndjson_search_names_a_failed_backend():
    rows = render.ndjson_rows("search", {
        "results": [{"title": "t", "url": "u", "position": 1}],
        "source_detail": [
            {"backend": "arxiv", "status": "blocked", "error": "429"},
            {"backend": "crossref", "status": "ok"},
        ],
    })
    assert rows[0]["type"] == "result"
    assert rows[1]["type"] == "source_error" and rows[1]["backend"] == "arxiv"
    assert len(rows) == 2


def test_crawl_prints_a_section_per_usable_page_and_accounts_for_failures():
    payload = {
        "pages": [
            {"url": "https://a.test/1", "content": ["one"], "content_ok": True},
            {"url": "https://a.test/2", "content": [], "content_ok": False,
             "error": "http_error_500"},
        ],
        "duration_ms": 8100,
    }
    body, meta = render.crawl(payload)
    assert "## https://a.test/1\none" in body
    assert "https://a.test/2" not in body
    assert "2 pages" in meta and "1 skipped" in meta and "8.1s" in meta
    assert "failed: https://a.test/2" in meta and "http_error_500" in meta
    assert render.exit_code("crawl", payload) == 0

    payload["pages"][0]["content_ok"] = False
    payload["pages"][0]["error"] = "timeout"
    assert render.exit_code("crawl", payload) == 1


def test_crawl_level_error_is_exit_one_even_with_pages():
    payload = {"pages": [{"url": "u", "content": ["x"], "content_ok": True}],
               "error": "plan expired"}
    assert render.exit_code("crawl", payload) == 1


def test_recall_prints_url_and_snippet_with_its_mode():
    payload = {
        "query": "取消传播", "engine": "bm25",
        "results": [{"url": "https://example.com/asyncio",
                     "snippet": "asyncio.CancelledError …"}],
    }
    body, meta = render.recall(payload)
    assert body == "https://example.com/asyncio\n   asyncio.CancelledError …"
    assert "recall_mode: bm25" in meta and "1 hits" in meta
    assert render.exit_code("recall", payload) == 0


def test_recall_error_is_exit_one():
    payload = {"query": "q", "engine": "bm25", "results": [], "error": "db unreadable"}
    assert render.exit_code("recall", payload) == 1


def test_verbose_adds_the_next_call_to_stderr_only():
    payload = _read(next_action="web_search the title", summary="200 OK · http")
    body, meta = render.read(payload, verbose=True)
    assert body == "# Hello\nworld"
    assert "next_action: web_search the title" in meta
    assert "summary: 200 OK · http" in meta


# ── 命令层：参数透传、stdout/stderr 分工、退出码 ──────────────────────────


def test_read_command_keeps_stdout_for_the_page(monkeypatch, capsys):
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: _read())
    assert ops._cmd_read(["https://example.com/a"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "# Hello\nworld\n"
    assert "example.com" in captured.err and "http" in captured.err


def test_json_prints_the_exact_envelope_and_nothing_on_stderr(monkeypatch, capsys):
    payload = _read()
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: payload)
    assert ops._cmd_read(["https://example.com/a", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"))
    assert captured.err == ""


def test_json_still_exits_one_when_the_content_is_not_usable(monkeypatch, capsys):
    payload = _read(content=[], content_ok=False, error="auth_wall")
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: payload)
    assert ops._cmd_read(["https://example.com/a", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["content_ok"] is False


def test_read_flags_reach_the_tool_and_seconds_become_milliseconds(monkeypatch):
    seen = {}

    def fake(name, args):
        seen.update({"name": name, "args": args})
        return _read()

    monkeypatch.setattr(ops, "_run_tool", fake)
    assert ops._cmd_read(["https://example.com/a", "--max-chars", "200",
                          "--offset", "50", "--timeout", "2.5"]) == 0
    assert seen["name"] == "web_read"
    assert seen["args"] == {
        "url": "https://example.com/a",
        "offset": 50,
        "max_content_chars": 200,
        "timeout": 2500,
    }


def test_a_batch_goes_through_urls_never_url_plus_urls(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        ops, "_run_tool",
        lambda name, args: seen.update(args) or {"results": [], "usable": 0})
    ops._cmd_read(["https://a.test", "https://b.test"])
    assert seen == {"urls": ["https://a.test", "https://b.test"]}


def test_offset_with_a_batch_is_refused_instead_of_silently_dropped(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: calls.append(args) or {})
    assert ops._cmd_read(["https://a.test", "https://b.test", "--offset", "10"]) == 2
    assert calls == []
    assert "--offset" in capsys.readouterr().err


def test_search_crawl_and_recall_forward_their_own_flags(monkeypatch):
    seen = []

    def fake(name, args):
        seen.append((name, args))
        if name == "web_search":
            return {"query": args.get("query"), "results": []}
        if name == "web_crawl":
            return {"pages": []}
        return {"query": args.get("query"), "results": [], "engine": "bm25"}

    monkeypatch.setattr(ops, "_run_tool", fake)
    ops._cmd_search(["python", "asyncio", "--max-results", "3"])
    ops._cmd_crawl(["https://example.com", "--max-pages", "4", "--timeout", "30"])
    ops._cmd_recall(["注意力机制", "--max-results", "5"])
    assert seen == [
        ("web_search", {"query": "python asyncio", "max_results": 3}),
        ("web_crawl", {"url": "https://example.com", "max_pages": 4,
                       "timeout": 30000}),
        ("web_recall", {"query": "注意力机制", "limit": 5}),
    ]


def test_usage_errors_exit_two_without_calling_the_tool(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: calls.append(name) or {})
    assert ops._cmd_read([]) == 2
    assert ops._cmd_search([]) == 2
    assert ops._cmd_crawl([]) == 2
    assert ops._cmd_crawl(["https://a.test", "https://b.test"]) == 2
    assert ops._cmd_recall([]) == 2
    assert ops._cmd_read(["https://a.test", "--timeout", "0"]) == 2
    assert calls == []
    assert "usage:" in capsys.readouterr().err


def test_an_unknown_flag_names_itself(monkeypatch, capsys):
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: {})
    assert ops._cmd_search(["q", "--timeout", "2"]) == 2
    assert "unknown flag: --timeout" in capsys.readouterr().err


def test_a_refusal_becomes_exit_two_not_a_traceback(monkeypatch, capsys):
    def refuse(name, args):
        raise ValueError(
            "Unknown tool: web_read. Registered names: [...]")

    monkeypatch.setattr(ops, "_run_tool", refuse)
    assert ops._cmd_read(["https://example.com"]) == 2
    assert "Unknown tool" in capsys.readouterr().err


def test_run_tool_rejects_a_dispatch_result_without_the_pair(monkeypatch):
    class _Srv:
        async def _dispatch(self, name, args):
            return ["only a content list"]

    monkeypatch.setattr(ops, "MasterFetchServer", lambda: _Srv())
    with pytest.raises(ValueError, match="structured"):
        ops._run_tool("web_read", {"url": "https://example.com"})
