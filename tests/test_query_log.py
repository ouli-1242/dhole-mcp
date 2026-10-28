"""`DHOLE_QUERY_LOG`：真实查询采集是显式、就地的，而且只记查询词。"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import audit

pytestmark = pytest.mark.smoke


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_query_log_is_off_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("DHOLE_QUERY_LOG", raising=False)
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    audit._log_query("python asyncio timeout", mode="auto", results=3)
    assert not (tmp_path / "queries.jsonl").exists()


def test_one_writes_the_query_and_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setenv("DHOLE_QUERY_LOG", "1")
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    audit._log_query("python asyncio timeout", mode="auto", max_results=6,
                     results=3, engines=["baidu", "bing"], rerank_model="bge-zh")
    row = _rows(tmp_path / "queries.jsonl")[0]
    assert row["query"] == "python asyncio timeout"
    assert row["results"] == 3
    assert row["engines"] == ["baidu", "bing"]
    assert set(row) == {"ts", "query", "mode", "max_results", "results",
                        "engines", "rerank_model"}


def test_a_path_writes_there_and_zero_stays_off(monkeypatch, tmp_path):
    target = tmp_path / "real-queries.jsonl"
    monkeypatch.setenv("DHOLE_QUERY_LOG", str(target))
    audit._log_query("sqlite wal", mode="auto")
    assert _rows(target)[0]["query"] == "sqlite wal"

    monkeypatch.setenv("DHOLE_QUERY_LOG", "0")
    before = target.read_text(encoding="utf-8")
    audit._log_query("should not land")
    assert target.read_text(encoding="utf-8") == before


def test_an_empty_query_is_not_a_sample(monkeypatch, tmp_path):
    target = tmp_path / "queries.jsonl"
    monkeypatch.setenv("DHOLE_QUERY_LOG", str(target))
    audit._log_query("   ", mode="auto")
    assert not target.exists()


def test_a_bad_target_never_breaks_the_search(monkeypatch, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("DHOLE_QUERY_LOG", str(blocker / "queries.jsonl"))
    audit._log_query("still a real query", mode="auto")  # must not raise
    assert blocker.exists()


def test_key_shaped_strings_are_redacted_from_the_query(monkeypatch, tmp_path):
    target = tmp_path / "queries.jsonl"
    monkeypatch.setenv("DHOLE_QUERY_LOG", str(target))
    audit._log_query("look up sk-1234567890123456789012", mode="auto")
    assert "sk-1234567890123456789012" not in target.read_text(encoding="utf-8")
