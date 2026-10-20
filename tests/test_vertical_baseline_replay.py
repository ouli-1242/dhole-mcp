"""The vertical baseline must run offline against its frozen corpus.

This is the reproducibility half of the benchmark: if a fixture is missing, a
parser regresses, or a known-item expectation stops matching, the default
(no-network) invocation must fail. Live availability is deliberately not
asserted here - it moves with the public APIs and belongs in a dated report,
not in the suite.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "evals" / "vertical_search_baseline.py"


def test_the_offline_baseline_passes_on_the_frozen_corpus():
    out = subprocess.run([sys.executable, str(SCRIPT), "--json"],
                         capture_output=True, text=True, cwd=ROOT, timeout=90)
    assert out.returncode == 0, out.stderr[-1200:] + out.stdout[-1200:]
    data = json.loads(out.stdout)
    assert data["mode"] == "offline"
    assert data["ok"] is True
    assert data["missing_fixtures"] == []
    assert data["queries"] == 6
    assert data["rows"] >= 30
    assert data["known_item_hit_at_5"] == 1.0
    assert data["dated_share"] == 1.0
    assert set(data["per_source"]) == {
        "news", "academic", "qa", "code", "community"}
    assert data["duplicates"] == 0
    assert data["sla_note"].startswith("targets for this benchmark")


def test_the_corpus_is_checked_in_with_its_own_provenance():
    corpus = ROOT / "evals" / "corpus" / "verticals"
    files = sorted(corpus.glob("*__*.json"))
    assert len(files) >= 7
    for path in files:
        rec = json.loads(path.read_text(encoding="utf-8"))
        assert rec["query_id"] and rec["source"] and rec["request_url"]
        assert rec["captured_at"] and rec["sha256"]
        assert rec["body_b64"], f"{path.name} froze an empty body"
        assert rec["status"] == 200, (
            f"{path.name} froze HTTP {rec['status']}; a blocked response is "
            "allowed but this corpus was captured on a healthy day")
