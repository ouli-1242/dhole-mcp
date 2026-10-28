"""recall 字面候选洞的纯函数：不联网、不加载模型。"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke
_spec = importlib.util.spec_from_file_location(
    "recall_lexical_gap", REPO / "evals" / "recall_lexical_gap.py")
gap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gap)


def test_load_qrels_skips_empty_queries_and_keeps_empty_positives(tmp_path):
    path = tmp_path / "qrels.jsonl"
    rows = [
        {"query": " alpha ", "positives": ["p1"], "negatives": ["n1"]},
        {"query": "", "positives": ["ignored"], "negatives": []},
        {"query": "no-positive", "positives": [], "negatives": ["n2"]},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    loaded = gap.load_qrels(path)
    assert [r["query"] for r in loaded] == ["alpha", "no-positive"]
    assert loaded[0]["positives"] == ["p1"]
    assert loaded[1]["positives"] == []


def test_build_corpus_dedupes_passages_and_maps_both_labels():
    rows = [
        {"query": "q1", "positives": ["same page"], "negatives": ["other"]},
        {"query": "q2", "positives": ["same page"], "negatives": []},
    ]
    docs, pos_idx, neg_idx = gap.build_corpus(rows)
    assert docs == ["same page", "other"]
    assert pos_idx == [[0], [0]]
    assert neg_idx == [[1], []]


def test_query_tokens_dedupes_and_caps_at_eight():
    tokens = gap.query_tokens("aa bb cc dd ee ff gg hh ii jj")
    assert tokens == ["aa", "bb", "cc", "dd", "ee", "ff", "gg", "hh"]


def test_candidate_indices_is_case_insensitive_and_empty_on_no_tokens():
    docs_lower = ["alpha page", "beta page"]
    assert gap.candidate_indices(["ALPHA"], docs_lower) == {0}
    assert gap.candidate_indices([], docs_lower) == set()


def test_measure_counts_zero_candidates_and_excludes_rows_without_positives():
    rows = [
        {"query": "alpha", "positives": ["alpha page"], "negatives": ["beta page"]},
        {"query": "gamma", "positives": ["delta page"], "negatives": []},
        {"query": "nothing", "positives": [], "negatives": []},
    ]
    docs, pos_idx, _ = gap.build_corpus(rows)
    stats = gap.measure(rows, docs, pos_idx, k=10)
    assert stats["queries"] == 3
    assert stats["queries_without_positives"] == 1
    assert stats["zero_candidate_queries"] == 2
    assert stats["positive_unreachable_queries"] == 1
    assert stats["positive_unreachable_rate"] == 0.5
    assert stats["lexical_R@10"] == 0.5
    assert stats["lexical_MRR"] == 0.5


def test_format_report_names_the_two_different_losses():
    stats = {
        "queries": 2, "passages": 2, "positives": 2,
        "queries_without_positives": 0,
        "zero_candidate_queries": 1, "zero_candidate_rate": 0.5,
        "positive_unreachable_queries": 1, "positive_unreachable_rate": 0.5,
        "positive_reachable_rate": 0.5,
        "lexical_R@5": 0.5, "lexical_R@10": 0.5, "lexical_MRR": 0.5,
        "candidates_median": 1, "candidates_mean": 1.0, "failures": [],
    }
    report = gap.format_report(stats, source="toy.jsonl")
    assert "零候选查询" in report
    assert "正例不可达查询" in report
    assert "排序" in report
