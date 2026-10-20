"""`dhole watch`: a watchlist that survives restarts and exits 0/1/2 honestly.

The fetch is the existing check path; these tests pin the store/runner contract
(no network): first run is a baseline, a later run can say changed/unchanged, a
dead page is an error rather than a quiet "unchanged", and one corrupt state
file is named instead of being silently replaced.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import ops
from dhole_mcp import watch as w


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    return tmp_path


def _checker(verdicts):
    calls = []

    async def run(url):
        calls.append(url)
        return verdicts.pop(0)

    return run, calls


def test_add_list_remove_round_trip():
    first = w.add("https://example.com/a#section", label="pricing")
    assert first["created"] is True
    assert first["watch"]["url"] == "https://example.com/a"  # fragment never reaches origin
    second = w.add("https://example.com/a", label="pricing page")
    assert second["created"] is False
    assert second["watch"]["label"] == "pricing page"

    state = w.load_state()
    assert [row["url"] for row in state["watches"]] == ["https://example.com/a"]
    assert state["watches"][0]["checks"] == 0

    assert w.remove("https://example.com/a") is True
    assert w.load_state()["watches"] == []
    assert w.remove("https://example.com/a") is False


def test_a_url_with_userinfo_is_refused_before_it_is_stored():
    with pytest.raises(ValueError) as exc:
        w.add("https://user:secret@example.com/a")
    assert "credentials" in str(exc.value)
    assert w.load_state()["watches"] == []


def test_first_run_is_a_baseline_not_a_change():
    url = "https://example.com/a"
    w.add(url)
    checker, calls = _checker([{"changed": None, "status": 200,
                                "note": "baseline recorded"}])
    result = asyncio.run(w.run(checker))
    assert calls == [url]
    assert result["baseline"] == [url] and result["changed"] == []
    assert result["exit_code"] == 0, "a freshly primed watch is not an alert"
    row = w.load_state()["watches"][0]
    assert row["last_verdict"] == "baseline"
    assert row["checks"] == 1


def test_second_run_reports_changed_and_keeps_the_diff_window():
    url = "https://example.com/a"
    w.add(url)
    checker, _ = _checker([
        {"changed": None, "status": 200},
        {"changed": True, "status": 200, "diff_window": "the price moved"},
    ])
    asyncio.run(w.run(checker))
    result = asyncio.run(w.run(checker))
    assert result["changed"] == [url] and result["exit_code"] == 1
    row = w.load_state()["watches"][0]
    assert row["last_verdict"] == "changed"
    assert row["last_changed_at"]
    assert "last_diff_window" not in row, "state stores verdicts, not page bodies"
    assert result["rows"][0]["diff_window"] == "the price moved"
    assert row["checks"] == 2


def test_unchanged_is_exit_zero_and_an_error_is_exit_two():
    a, b = "https://example.com/a", "https://example.com/b"
    w.add(a)
    w.add(b)
    checker, _ = _checker([
        {"changed": False, "status": 304},
        {"changed": None, "status": 404, "note": "not comparable"},
    ])
    result = asyncio.run(w.run(checker))
    assert result["unchanged"] == [a]
    assert result["errors"] == [{"url": b, "error": "HTTP 404 without a changed/unchanged verdict"}]
    assert result["exit_code"] == 2, "a dead page must not pass as a quiet night"
    state = {row["url"]: row for row in w.load_state()["watches"]}
    assert state[b]["last_verdict"] == "error"
    assert "404" in state[b]["last_error"]


def test_a_raising_checker_becomes_an_error_row_not_a_crash():
    url = "https://example.com/a"
    w.add(url)

    async def boom(_url):
        raise TimeoutError("took too long")

    result = asyncio.run(w.run(boom))
    assert result["exit_code"] == 2
    assert "TimeoutError" in result["errors"][0]["error"]


def test_only_narrows_the_sweep_and_names_unknown_urls():
    a, b = "https://example.com/a", "https://example.com/b"
    w.add(a)
    w.add(b)
    checker, calls = _checker([{"changed": False, "status": 304}])
    result = asyncio.run(w.run(checker, only=[a, "https://example.com/nope"]))
    assert calls == [a]
    assert result["unknown"] == ["https://example.com/nope"]


def test_a_corrupt_state_file_is_named_not_replaced():
    from dhole_mcp import paths
    paths.file("watchlist.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(w.WatchStateError) as exc:
        w.load_state()
    assert "watchlist.json" in str(exc.value)


def test_a_live_run_lock_blocks_a_second_sweep():
    from dhole_mcp import paths
    lock = paths.file("watchlist.json.lock")
    lock.write_text("123 running\n", encoding="utf-8")
    w.add("https://example.com/a")
    checker, calls = _checker([{"changed": False, "status": 304}])
    with pytest.raises(w.WatchBusy):
        asyncio.run(w.run_locked(checker))
    assert calls == []
    lock.unlink()


class TestWatchCLI:

    def test_cli_add_list_remove_and_json(self, capsys):
        assert ops._cmd_watch(["add", "https://example.com/a", "--label", "docs"]) == 0
        out = capsys.readouterr().out
        assert "watching" in out and "baseline" in out

        assert ops._cmd_watch(["list", "--json"]) == 0
        state = json.loads(capsys.readouterr().out)
        assert state["watches"][0]["label"] == "docs"

        assert ops._cmd_watch(["remove", "https://example.com/a"]) == 0
        assert "stopped watching" in capsys.readouterr().out
        assert ops._cmd_watch(["remove", "https://example.com/a"]) == 1

    def test_cli_run_returns_the_exit_code_and_json(self, capsys):
        w.add("https://example.com/a")

        async def checker(_url):
            return {"changed": True, "status": 200, "diff_window": "new"}

        # _cmd_watch drives asyncio.run itself, so this test stays sync.
        assert ops._cmd_watch(["run", "--json"], checker=checker) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["changed"] == ["https://example.com/a"]
        assert payload["exit_code"] == 1

    def test_cli_refuses_a_run_with_no_timeout_math(self, capsys):
        assert ops._cmd_watch(["run", "--timeout", "nope"]) == 2
        assert "--timeout" in capsys.readouterr().err
