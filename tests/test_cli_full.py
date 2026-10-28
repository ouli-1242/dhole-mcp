"""13 颗工具的 CLI 命令面：其余 9 条渲染器与命令层。

与 `test_cli_render.py` 同一套纪律：渲染器吃假回执，命令层把 `ops._run_tool`
换成假函数 —— 不联网、不碰真实服务器。
"""

from __future__ import annotations

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


# ── 渲染器 ──────────────────────────────────────────────────────────────


def test_discover_prints_one_url_per_line_and_names_truncation():
    payload = {
        "pages": [{"url": "https://a.test/1"}, {"url": "https://a.test/2"}],
        "pages_discovered": 2,
        "sitemap_used": True,
        "truncated_by_max_pages": True,
        "next_resume_token": "plan-1:2",
        "duration_ms": 1200,
    }
    body, meta = render.discover(payload)
    assert body.splitlines() == ["https://a.test/1", "https://a.test/2"]
    assert "2 URLs" in meta and "sitemap" in meta and "1.2s" in meta
    assert "max-pages" in meta and "--resume-token plan-1:2" in meta
    assert render.exit_code("discover", payload) == 0


def test_discover_with_no_page_or_an_error_is_exit_one():
    assert render.exit_code("discover", {"pages": []}) == 1
    assert render.exit_code("discover", {"pages": [], "error": "boom"}) == 1


def test_research_prints_evidence_and_accounts_for_failed_sources():
    payload = {
        "query": "q",
        "evidence": ["[1] https://a.test\nbody one", "[2] https://b.test\nbody two"],
        "sources": [{"n": 1, "url": "https://a.test", "chars": 8, "fetcher": "http"},
                    {"n": 2, "url": "https://b.test", "chars": 8, "fetcher": "http"}],
        "failed": [{"n": 3, "url": "https://c.test", "why": "timeout"}],
    }
    body, meta = render.research(payload)
    assert body == "[1] https://a.test\nbody one\n\n[2] https://b.test\nbody two"
    assert "2 sources" in meta and "1 failed" in meta
    assert "failed: [3] https://c.test · timeout" in meta
    assert render.exit_code("research", payload) == 0


def test_research_without_evidence_is_exit_one():
    assert render.exit_code(
        "research", {"evidence": [], "error": "search leg failed"}) == 1


def test_request_puts_the_status_on_stderr_and_headers_only_when_verbose():
    payload = _read(content=[], status=204, content_ok=True, fetcher_used="http",
                    metadata={"response_headers": {"server": "nginx"}})
    body, meta = render.request(payload)
    assert body == ""
    assert "HTTP 204" in meta and "http" in meta
    assert "server" not in meta
    _, verbose = render.request(payload, verbose=True)
    assert "server: nginx" in verbose
    assert render.exit_code("request", payload) == 0


def test_doc_reports_the_charset_only_when_verbose():
    payload = _read(url="file:///tmp/a.md", content=["# T"], fetcher_used="parse",
                    metadata={"encoding": "gbk"})
    _, meta = render.doc(payload)
    assert "file:///tmp/a.md" in meta and "parse" in meta
    _, verbose = render.doc(payload, verbose=True)
    assert "encoding: gbk" in verbose


def test_act_prints_one_receipt_per_action_and_fails_on_a_failed_step():
    payload = _read(content=["post-action page"], content_ok=True, metadata={
        "actions": [
            {"action": "click .more", "ok": True, "selector": ".more"},
            {"action": "fill #q", "ok": False, "error": "no element"},
            {"action": "submit", "ok": False, "pending_approval": True},
            {"action": "screenshot", "ok": True, "image": True},
        ]})
    body, meta = render.act(payload)
    assert body == "post-action page"
    assert "ok: click .more · .more" in meta
    assert "failed: fill #q · no element" in meta
    assert "pending: submit · waiting for approval" in meta
    assert "no image channel" in meta
    assert render.exit_code("act", payload) == 1

    payload["metadata"]["actions"] = [{"action": "click", "ok": True}]
    assert render.exit_code("act", payload) == 0


def test_feed_splits_by_source_and_counts_items_errors_and_flagged_entries():
    payload = {"feeds": [
        {"source_url": "https://a.test/feed", "source_title": "A",
         "items": [
             {"title": "one", "url": "https://a.test/1", "published": "2026-10-20",
              "summary": "s1"},
             {"title": "two", "url": "https://a.test/2",
              "untrusted_content": {"total": 2, "hits": []}},
         ], "items_older_than_since": 3},
        {"source_url": "https://b.test/feed", "error": "network_error: tls"},
    ]}
    body, meta = render.feed(payload)
    assert "## https://a.test/feed  (A)" in body
    assert "2026-10-20 · one" in body and "s1" in body
    assert "## https://b.test/feed" not in body
    assert "2 feeds" in meta and "2 items" in meta and "3 older skipped" in meta
    assert "1 failed" in meta and "1 flagged" in meta
    assert "failed: https://b.test/feed · network_error: tls" in meta
    assert render.exit_code("feed", payload) == 0
    assert render.exit_code("feed", {"feeds": [payload["feeds"][1]]}) == 1


def test_state_renders_the_census_and_the_erasure_receipt():
    payload = {
        "sessions": [{"session_id": "work", "hosts": {"example.com": ["sid"]},
                      "cookies": 2, "browser_open": True, "browser_type": "stealthy",
                      "pinned_url": "https://example.com/x"}],
        "cache": {"entries": 12, "expired": 3, "cap": 10000, "oldest_age_s": 7200},
        "cred_defaults": [{"key": "proxy"}],
        "cdp_hosts": ["example.com"],
        "config_keys": ["DHOLE_DEFAULT_CONTENT_CHARS"],
        "reranker": {"state": "not_downloaded", "model": "bge-zh", "label": "bilingual"},
        "cleared": ["cache"], "purged": 4,
        "message": "Erased cache: 4 cache entry(ies) erased.",
    }
    body, meta = render.state(payload)
    assert "sessions (1):" in body
    assert "work · example.com · 2 cookie(s) · browser stealthy · pinned" in body
    assert "cache: 12 entries · 3 expired · cap 10000 · oldest 2.0h" in body
    assert "identity: 1 stored row(s)" in body
    assert "cdp hosts: example.com" in body
    assert "cleared: cache" in body and "cache rows 4" in body
    assert "Erased cache" in meta
    assert render.exit_code("state", payload) == 0


def test_a_session_this_process_cannot_present_is_marked_in_the_census():
    """磁盘那一份罐里的行不是"open here"：`dhole state` 要说它在哪。"""
    payload = {"sessions": [{
        "session_id": "other-proc", "store": "disk",
        "hosts": {"shop.example.com": ["sid"]}, "cookies": 1,
        "expires_in_s": 3600,
    }]}
    body, _meta = render.state(payload)
    assert "sessions (1):" in body
    assert "other-proc · shop.example.com · 1 cookie(s) · expires in 1.0h · on disk" in body

    assert render.exit_code("state", {"pending_approval": True,
                                      "message": "not erased"}) == 1
    assert render.exit_code("state", {"error": "store refused"}) == 1


def test_creds_shows_the_redacted_rows_and_never_echoes_a_secret():
    payload = {"rows": [{"key": "auth", "scope": "example.com",
                         "value": "bearer ****1234", "stored_s_ago": 90}],
               "message": "1 default(s) in force: auth@example.com."}
    body, meta = render.creds(payload)
    assert body == "auth · @example.com · bearer ****1234 · 1m ago"
    assert "1 default(s) in force" in meta
    assert render.exit_code("creds", payload) == 0
    assert render.exit_code("creds", {"error": "unknown key"}) == 1


def test_guide_is_the_text_or_a_failure():
    assert render.guide("handbook text") == ("handbook text", "")
    assert render.exit_code("guide", "handbook text") == 0
    assert render.exit_code("guide", "   ") == 1


def test_security_marks_are_not_hidden_behind_verbose():
    payload = _read(
        untrusted_content={"total": 2, "hits": []},
        identity_applied="auth@example.com",
        source="archive.org", archived_at="2026-09-01",
        links={"citations": [{"url": "https://ref.test"}], "navigation": [],
               "external": [], "total_found": 1},
        media=["https://example.com/a.png"],
        session_cookie_names=["sid", "csrf"],
        login_check={"logged_in": True, "method": "dom+cookie-names"},
    )
    _, meta = render.read(payload)
    assert "untrusted_content: 2 hit(s)" in meta
    assert "identity_applied: auth@example.com" in meta
    assert "archive.org snapshot" in meta
    assert "links: citations 1 · navigation 0 · external 0 · 1 found" in meta
    assert "media: 1 item(s)" in meta
    assert "session cookies: sid, csrf" in meta
    assert "login_check: logged_in=True" in meta
    _, verbose = render.read(payload, verbose=True)
    assert "citations: https://ref.test" in verbose


def test_crawl_names_its_truncation_and_resume_token():
    payload = {"pages": [{"url": "https://a.test", "content": ["x"], "content_ok": True}],
               "truncated_by_time": True, "next_resume_token": "plan-2:1"}
    _, meta = render.crawl(payload)
    assert "truncated by the deadline" in meta
    assert "--resume-token plan-2:1" in meta


def test_search_names_same_document_groups():
    payload = {"results": [], "same_document_groups": [
        {"positions": [1, 4], "hosts": 2, "title": "same"}]}
    _, meta = render.search(payload)
    assert "same document: positions [1, 4] · 2 host(s)" in meta


def test_search_with_fetch_content_puts_the_pages_on_stdout():
    payload = {
        "query": "q",
        "results": [{"position": 1, "title": "T", "url": "https://a.test", "snippet": "s"}],
        "fetched_pages": [
            {"url": "https://a.test", "content": ["page body"], "content_ok": True},
            {"url": "https://b.test", "content": [], "content_ok": False,
             "error": "timeout"},
        ],
        "engines_used": ["baidu"], "duration_ms": 2000,
    }
    body, meta = render.search(payload)
    assert "1. T" in body and "## https://a.test\npage body" in body
    assert "2 fetched page(s)" in meta
    assert "failed page: https://b.test · timeout" in meta


# ── 命令层 ──────────────────────────────────────────────────────────────


def test_discover_research_and_feed_forward_their_flags(monkeypatch):
    seen = []

    def fake(name, args):
        seen.append((name, args))
        if name == "web_discover":
            return {"pages": [{"url": "https://a.test"}]}
        if name == "web_research":
            return {"evidence": ["[1] x"], "sources": []}
        return {"feeds": [{"source_url": "u", "items": []}]}

    monkeypatch.setattr(ops, "_run_tool", fake)
    ops._cmd_discover(["https://a.test", "--sitemap", "--max-pages", "3",
                       "--timeout", "2"])
    ops._cmd_research(["python", "asyncio", "--max-sources", "2",
                       "--per-source-chars", "1000", "--timeout", "90"])
    ops._cmd_feed(["https://a.test/feed", "https://b.test/feed",
                   "--max-items", "5", "--since", "3d", "--timeout", "10"])
    assert seen == [
        ("web_discover", {"url": "https://a.test", "sitemap": True,
                          "max_pages": 3, "timeout": 2000}),
        ("web_research", {"query": "python asyncio", "max_sources": 2,
                          "per_source_chars": 1000, "timeout": 90000}),
        ("feed_read", {"urls": ["https://a.test/feed", "https://b.test/feed"],
                       "max_items": 5, "since": "3d", "timeout": 10000}),
    ]


def test_request_needs_a_method_and_reads_the_body_from_a_file(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: seen.update({"name": name, "args": args})
                        or _read(content=[], status=200, content_ok=True))
    body_file = tmp_path / "body.json"
    body_file.write_text('{"a": 1}', encoding="utf-8")
    assert ops._cmd_request([
        "https://api.test/x", "--method", "POST", "--body-file", str(body_file),
        "--content-type", "application/json", "--confirm",
        "--headers", '{"x-trace": "1"}', "--timeout", "5",
    ]) == 0
    assert seen["name"] == "web_request"
    assert seen["args"] == {
        "url": "https://api.test/x", "method": "POST", "body": '{"a": 1}',
        "content_type": "application/json", "confirm": True,
        "headers": {"x-trace": "1"}, "timeout": 5000,
    }
    assert ops._cmd_request(["https://api.test/x"]) == 2


def test_request_refuses_two_bodies(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: _read())
    mon = tmp_path / "b"
    mon.write_text("x", encoding="utf-8")
    assert ops._cmd_request(["https://api.test/x", "--method", "POST", "--body", "a",
                             "--body-file", str(mon)]) == 2
    assert "not both" in capsys.readouterr().err


def test_act_reads_the_actions_from_a_file_and_rejects_bad_shapes(
        monkeypatch, tmp_path, capsys):
    seen = {}
    monkeypatch.setattr(
        ops, "_run_tool",
        lambda name, args: seen.update({"name": name, "args": args})
        or {"content": ["ok"], "content_ok": True, "metadata": {"actions": []}})
    actions = tmp_path / "actions.json"
    actions.write_text('[{"snapshot": true}, {"click": "#go"}]', encoding="utf-8")
    assert ops._cmd_act(["https://a.test", "--actions", f"@{actions}",
                         "--session-id", "s1", "--headed"]) == 0
    assert seen["name"] == "web_act"
    assert seen["args"] == {"url": "https://a.test",
                            "actions": [{"snapshot": True}, {"click": "#go"}],
                            "session_id": "s1", "headed": True}

    assert ops._cmd_act(["https://a.test"]) == 2
    assert ops._cmd_act(["https://a.test", "--actions", "nope"]) == 2
    assert ops._cmd_act(["https://a.test", "--actions", '{"a": 1}']) == 2
    assert "JSON array" in capsys.readouterr().err


def test_doc_and_guide_forward_their_positionals(monkeypatch):
    seen = []

    def fake(name, args):
        seen.append((name, args))
        if name == "doc_parse":
            return _read(url="file:///x", content=["x"], fetcher_used="parse")
        return "guide text"

    monkeypatch.setattr(ops, "_run_tool", fake)
    assert ops._cmd_doc(["/tmp/a.md", "--encoding", "gbk", "--offset", "10",
                         "--pages", "1-2"]) == 0
    assert ops._cmd_guide(["fields", "PDF"]) == 0
    assert seen == [
        ("doc_parse", {"file_path": "/tmp/a.md", "encoding": "gbk",
                       "offset": 10, "pages": "1-2"}),
        ("sys_guide", {"topic": "fields", "section": "PDF"}),
    ]


def test_state_flags_become_lists_and_a_confirm(monkeypatch):
    seen = {}
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: seen.update({"name": name, "args": args})
                        or {"message": "census"})
    assert ops._cmd_state(["--show", "cache,models", "--show", "cdp_hosts",
                           "--clear", "sessions", "--confirm"]) == 0
    assert seen == {"name": "sys_state",
                    "args": {"show": ["cache", "models", "cdp_hosts"],
                             "clear": ["sessions"],
                             "confirm": True}}
    assert ops._cmd_state(["unexpected"]) == 2


def test_creds_refuses_to_write_when_persistence_is_off(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    monkeypatch.setattr(ops, "_cred_persistence_enabled", lambda: False)
    called = []
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: called.append(name) or {})
    assert ops._cmd_creds(["set", '{"proxy": "http://127.0.0.1:7890"}']) == 2
    assert called == []
    assert "DHOLE_PERSIST_CREDENTIALS=1" in capsys.readouterr().err


def test_creds_set_and_unset_forward_their_shapes(monkeypatch, tmp_path):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    monkeypatch.setattr(ops, "_cred_persistence_enabled", lambda: True)
    seen = []

    def fake(name, args):
        seen.append((name, args))
        return {"rows": [], "message": "ok"}

    monkeypatch.setattr(ops, "_run_tool", fake)
    creds_file = tmp_path / "creds.json"
    creds_file.write_text('{"proxy": "http://127.0.0.1:7890"}', encoding="utf-8")
    assert ops._cmd_creds(["set", f"@{creds_file}"]) == 0
    assert ops._cmd_creds(["unset", "auth", "cookies"]) == 0
    assert ops._cmd_creds(["show"]) == 0
    assert seen == [
        ("cred_defaults", {"set": {"proxy": "http://127.0.0.1:7890"}}),
        ("cred_defaults", {"unset": ["auth", "cookies"]}),
        ("cred_defaults", {}),
    ]


def test_arg_passes_any_tool_argument_and_json_values_keep_their_types(monkeypatch):
    seen = {}
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: seen.update(args) or _read())
    assert ops._cmd_read(["https://a.test", "--arg", "max_links=5",
                          "--arg", "include_links=true", "--arg", "focus=asyncio"]) == 0
    assert seen["max_links"] == 5
    assert seen["include_links"] is True
    assert seen["focus"] == "asyncio"


def test_arg_duplicating_a_named_flag_is_refused(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: called.append(name) or _read())
    assert ops._cmd_read(["https://a.test", "--max-links", "3",
                          "--arg", "max_links=5"]) == 2
    assert called == []
    assert "duplicates a named flag" in capsys.readouterr().err


def test_run_tool_reads_the_prose_channel_for_sys_guide(monkeypatch):
    class _Block:
        type = "text"
        text = "handbook text"

    class _Srv:
        async def _dispatch(self, name, args):
            return [_Block()]

    monkeypatch.setattr(ops, "MasterFetchServer", lambda: _Srv())
    assert ops._run_tool("sys_guide", {}) == "handbook text"


def test_help_lists_every_content_command():
    text = ops._help_epilog()
    for name in ("read", "search", "crawl", "discover", "research", "recall",
                 "request", "act", "doc", "feed", "state", "creds", "guide"):
        assert f"dhole {name} " in text, name
