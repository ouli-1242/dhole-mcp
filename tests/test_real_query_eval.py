"""真实查询盲评的纯函数与盲表生成：不联网、不加载模型。"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import random

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke
_spec = importlib.util.spec_from_file_location(
    "real_query_eval", REPO / "evals" / "real_query_eval.py")
rqe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rqe)


def test_load_queries_accepts_jsonl_and_plain_text_and_dedupes(tmp_path):
    path = tmp_path / "q.txt"
    path.write_text(
        '{"id": "a", "text": "python asyncio timeout"}\n'
        "# a comment\n"
        "sqlite wal mode\n"
        "sqlite wal mode\n"
        '{"query": "pydantic v2"}\n',
        encoding="utf-8")
    rows = rqe.load_queries(path)
    assert [r["query"] for r in rows] == [
        "python asyncio timeout", "sqlite wal mode", "pydantic v2"]
    assert rows[0]["qid"] == "a"
    assert len({r["qid"] for r in rows}) == 3


def test_rank_indices_breaks_ties_by_original_order():
    assert rqe.rank_indices([0.1, 0.9, 0.9, 0.5], 3) == [1, 2, 3]


def test_slot_order_is_seeded_and_uses_both_models():
    a = rqe.slot_order(("bge-zh", "mmarco"), random.Random(7))
    b = rqe.slot_order(("bge-zh", "mmarco"), random.Random(7))
    assert a == b
    assert set(a.values()) == {"bge-zh", "mmarco"}


def test_measured_rows_counts_relevant_items_in_each_top_k():
    cands = [{"qid": "q1", "query": "x",
              "docs": [{"title": f"d{i}", "snippet": ""} for i in range(5)]}]
    scores = {
        "base": {"q1": [0.1, 0.9, 0.8, 0.2, 0.3]},
        "cand": {"q1": [0.9, 0.1, 0.8, 0.2, 0.3]},
    }
    slots = {"q1": {"A": "base", "B": "cand"}}
    judgments = {"q1": {"A": [1], "B": [0, 2]}}
    rows = rqe.measured_rows(cands, scores, slots, judgments, k=2)
    assert rows[0]["base"] == 1.0
    assert rows[0]["cand"] == 2.0
    stats = rqe.paired_stats(rows, "base", "cand")
    assert stats["n"] == 1
    assert stats["mean_diff"] == 1.0
    assert stats["verdict"] == "insufficient"  # n < 100


def test_bootstrap_ci_reports_zero_crossing_for_mixed_diffs():
    mixed = [1.0, -1.0] * 50
    lo, hi = rqe.bootstrap_ci(mixed, iterations=2000, seed=3)
    assert lo < 0 < hi
    lo, hi = rqe.bootstrap_ci([1.0] * 20, iterations=2000, seed=3)
    assert lo > 0


def test_paired_stats_says_candidate_better_only_at_full_sample():
    rows = [{"qid": f"q{i}", "base": 1.0, "cand": 2.0} for i in range(100)]
    stats = rqe.paired_stats(rows, "base", "cand", iterations=2000)
    assert stats["wins"] == 100
    assert stats["verdict"] == "candidate_better"
    assert stats["ci"][0] > 0


def test_emit_writes_a_blind_sheet_and_keeps_models_only_in_the_key(tmp_path):
    cands = [{"qid": "q1", "query": "x",
              "docs": [{"title": f"d{i}", "snippet": "s"} for i in range(6)]}]
    (tmp_path / "candidates.jsonl").write_text(
        "".join(json.dumps(c) + "\n" for c in cands), encoding="utf-8")
    (tmp_path / "scores-bge-zh.jsonl").write_text(
        json.dumps({"qid": "q1", "model": "bge-zh",
                    "scores": [0.5, 0.9, 0.8, 0.1, 0.2, 0.3]}) + "\n",
        encoding="utf-8")
    (tmp_path / "scores-mmarco.jsonl").write_text(
        json.dumps({"qid": "q1", "model": "mmarco",
                    "scores": [0.9, 0.1, 0.8, 0.2, 0.3, 0.4]}) + "\n",
        encoding="utf-8")
    rc = rqe.cmd_emit(type("A", (), {"run_dir": str(tmp_path), "top_k": 5,
                                     "seed": 1})())
    assert rc == 0
    html = (tmp_path / "sheet.html").read_text(encoding="utf-8")
    assert "bge-zh" not in html and "mmarco" not in html
    key = json.loads((tmp_path / "key.json").read_text(encoding="utf-8"))
    assert set(key["slots"]["q1"].values()) == {"bge-zh", "mmarco"}
    sheet = json.loads((tmp_path / "sheet.json").read_text(encoding="utf-8"))
    assert len(sheet["queries"][0]["A"]) == 5
    assert len(sheet["queries"][0]["B"]) == 5
