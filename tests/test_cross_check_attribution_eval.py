"""`evals/cross_check_attribution.py` 的纯函数：切分、统计与 clear 口径。"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.smoke
_spec = importlib.util.spec_from_file_location(
    "cross_check_attribution", REPO / "evals" / "cross_check_attribution.py")
cca = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cca)


def test_split_aspects_keeps_both_sides_usable():
    left, right = cca.split_aspects("python asyncio 并发限制 semaphore 用法")
    assert left and right
    assert len(left) >= 6 and len(right) >= 6
    assert "asyncio" in left and "semaphore" in right


def test_split_aspects_refuses_a_query_it_cannot_halve():
    assert cca.split_aspects("Model Context Protocol") is None
    assert cca.split_aspects("a b c d") is None


def _row(*, claim=0, assigned=0, attributed=True, clear=True,
         stance="support", stance_legacy="support", negation=False):
    return {
        "claim": claim, "assigned": assigned, "attributed": attributed,
        "clear": clear, "margin": 0.2, "score": 0.3,
        "stance": stance, "stance_legacy": stance_legacy,
        "triggers": {"negation": negation, "antonym": False, "numeric": False},
        "triggers_legacy": {"negation": negation, "antonym": False,
                            "numeric": False},
        "sentence": "sentence",
    }


def test_summarise_counts_clear_and_legacy_separately():
    rows = [
        _row(),                                    # correct, clear
        _row(),                                    # correct, clear
        _row(assigned=1, clear=False),             # wrong, ambiguous
        _row(attributed=False, clear=False),       # below floor
        _row(stance="oppose", stance_legacy="oppose", negation=True,
             clear=False),                         # legacy-only noise? both oppose
    ]
    summary = cca.summarise(rows)

    assert summary["sentences"] == 5
    assert summary["attributed"] == 4
    assert summary["misassigned"] == 1
    assert summary["misassignment_rate"] == 0.25
    assert summary["clear"] == 2
    assert summary["clear_misassigned"] == 0
    assert summary["oppose"] == 1
    assert summary["oppose_legacy"] == 1
    assert summary["negation_only"] == 1
    assert summary["negation_only_legacy"] == 1
