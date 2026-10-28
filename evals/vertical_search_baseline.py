#!/usr/bin/env python3
"""Reproducible baseline for the five keyless vertical search sources.

The default mode replays **frozen API responses** from
``evals/corpus/verticals/``: no network, deterministic, and it fails when a
parser or a known-item expectation regresses. ``--live`` measures the same
checks against the public APIs right now (availability and latency), and
``--capture`` writes a new frozen corpus.

What this measures
------------------
* availability: a source that answered *and returned at least one row*,
  divided by the source-query attempts (a 429 is not "no results")
* known-item Hit@5: a query whose expected URL/title is actually in the top 5
* structural checks: minimum row count and day-level date coverage
  (news has no stable "known item", so its check is structural)
* latency: per-source p50/p95, **live mode only** — replay latency says
  nothing about the network
* duplicates: identical URLs delivered twice within one query

What this does NOT measure
--------------------------
Ranking quality. There are no qrels here; the fixed expectations are a smoke
test for "the parser still finds the named thing". Ranking is measured by
``evals/ranking_baseline.py`` (set A) and ``evals/model_swap_b8.py`` (set B).

Usage
-----
    python evals/vertical_search_baseline.py              # offline replay
    python evals/vertical_search_baseline.py --json
    python evals/vertical_search_baseline.py --live       # current APIs
    python evals/vertical_search_baseline.py --capture    # refresh corpus

SLA in the output is a **target for this benchmark**, not a service guarantee:
the sources are free public APIs (see README "已知限制").
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import json
import math
import os
import pathlib
import statistics
import sys
import tempfile
import time
from urllib.parse import urlparse

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# Never read the operator's credentials/config while benchmarking: the public
# APIs need none, and a stored proxy/identity would make two machines disagree.
os.environ["DHOLE_HOME"] = tempfile.mkdtemp(prefix="dhole_vertical_baseline_")

QUERIES = REPO_ROOT / "evals" / "vertical_queries.jsonl"
CORPUS = REPO_ROOT / "evals" / "corpus" / "verticals"

_HOT = {
    "export.arxiv.org": "academic",
    "arxiv.org": "academic",
    "api.gdeltproject.org": "news",
    "api.stackexchange.com": "qa",
    "api.github.com": "code",
    "hn.algolia.com": "community",
}

SLA = {
    "availability": 0.80,
    "known_item_hit_at_5": 0.80,
    "dated_share": 0.90,
    "live_p95_ms": 8000.0,
}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def load_queries(path: pathlib.Path = QUERIES) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        row["max_results"] = int(row.get("max_results", 5))
        rows.append(row)
    return rows


def _kind_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return _HOT.get(host, "")


def _fixture_path(qid: str, kind: str) -> pathlib.Path:
    return CORPUS / f"{qid}__{kind}.json"


def _load_fixtures() -> dict[tuple[str, str], dict]:
    out = {}
    if not CORPUS.is_dir():
        return out
    for path in sorted(CORPUS.glob("*__*.json")):
        qid, kind = path.stem.split("__", 1)
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            rec["_path"] = path
            out[(qid, kind)] = rec
        except (OSError, ValueError) as exc:
            raise SystemExit(f"unreadable fixture {path.name}: {exc}") from exc
    return out


def _records_to_rows(hits, reports) -> tuple[list[dict], list[dict]]:
    rows = [{
        "kind": h.kind, "backend": h.backend, "rank": h.rank,
        "title": h.title, "url": h.url, "snippet": h.snippet,
        "published": h.published,
        "published_precision": h.published_precision,
    } for h in hits]
    return rows, [r.as_dict() for r in reports]


def _check_expectation(exp: dict, rows: list[dict], report: dict) -> dict:
    """Evaluate one expectation against one source's rows.

    A source that was blocked/timeout/error in the frozen snapshot is marked
    ``comparable=False``: the corpus cannot answer a question about rows that
    the API never sent, and silently failing the parser would be a false
    accusation. Availability still counts the miss.
    """
    source = exp["source"]
    if report.get("status") not in ("ok", "empty"):
        return {"source": source, "pass": None, "comparable": False,
                "reason": f"source {report.get('status')}: "
                          f"{report.get('error') or 'no answer'}"}
    mine = [r for r in rows if r["kind"] == source]
    min_rows = int(exp.get("min_rows") or 0)
    if min_rows and len(mine) < min_rows:
        return {"source": source, "pass": False, "comparable": True,
                "reason": f"{len(mine)} row(s), wanted at least {min_rows}"}
    if exp.get("require_dated"):
        undated = [r for r in mine if r["published_precision"] != "day"]
        if undated:
            return {"source": source, "pass": False, "comparable": True,
                    "reason": f"{len(undated)} of {len(mine)} row(s) carry no day"}
    title = (exp.get("title_contains") or "").casefold()
    prefix = exp.get("url_prefix") or ""
    if title or prefix:
        matched = [
            r for r in mine
            if (not title or title in r["title"].casefold())
            and (not prefix or r["url"].startswith(prefix))
        ]
        if not matched:
            return {"source": source, "pass": False, "comparable": True,
                    "reason": f"no top-{len(mine)} row matched "
                              f"title={title!r} url_prefix={prefix!r}"}
        return {"source": source, "pass": True, "comparable": True,
                "rank": matched[0]["rank"], "url": matched[0]["url"]}
    return {"source": source, "pass": True, "comparable": True}


def _summarize(results: list[dict], *, mode: str, missing: list[str],
               extra: dict | None = None) -> dict:
    attempts = usable = answered = errors = 0
    rows_count = dated_count = duplicate_count = 0
    latencies: list[float] = []
    per_source: dict[str, dict] = {}
    expectations: list[dict] = []
    for res in results:
        qid = res["id"]
        for report in res["reports"]:
            source = report["source"]
            attempts += 1
            if report["status"] in ("ok", "empty"):
                answered += 1
            else:
                errors += 1
            if report["status"] == "ok" and report["count"] > 0:
                usable += 1
            if report.get("duration_ms"):
                latencies.append(float(report["duration_ms"]))
            slot = per_source.setdefault(source, {
                "attempts": 0, "answered": 0, "usable": 0, "errors": 0,
                "rows": 0, "dated": 0, "latencies": [],
            })
            slot["attempts"] += 1
            slot["answered"] += int(report["status"] in ("ok", "empty"))
            slot["errors"] += int(report["status"] not in ("ok", "empty"))
            slot["usable"] += int(report["status"] == "ok" and report["count"] > 0)
            slot["latencies"].append(float(report.get("duration_ms") or 0.0))
            mine = [r for r in res["rows"] if r["kind"] == source]
            dated = sum(1 for r in mine if r["published_precision"] == "day")
            slot["rows"] += len(mine)
            slot["dated"] += dated
            rows_count += len(mine)
            dated_count += dated
            duplicate_count += len(mine) - len({r["url"] for r in mine})
        for exp in res.get("expectations") or []:
            expectations.append({"id": qid, **exp})

    for slot in per_source.values():
        lats = sorted(slot.pop("latencies"))
        slot["p50_ms"] = round(statistics.median(lats), 1) if lats else 0.0
        slot["p95_ms"] = round(lats[max(0, math.ceil(0.95 * len(lats)) - 1)], 1) if lats else 0.0

    comparable = [e for e in expectations if e.get("comparable")]
    # The expectation is tagged by _evaluate_query; never infer "known item" from
    # the prose of a failure reason.
    content_expectations = [e for e in comparable if e.get("kind") == "content"]
    hits = sum(1 for e in content_expectations if e["pass"])
    structural = [e for e in comparable if e.get("kind") == "structural"]
    structural_hits = sum(1 for e in structural if e["pass"])
    availability = usable / attempts if attempts else 0.0
    known_rate = hits / len(content_expectations) if content_expectations else None
    dated_share = dated_count / rows_count if rows_count else 0.0
    all_lats = sorted(latencies)
    p95 = all_lats[max(0, math.ceil(0.95 * len(all_lats)) - 1)] if all_lats else 0.0
    sla_checks = {
        "availability": {
            "value": round(availability, 3), "target": SLA["availability"],
            "pass": availability >= SLA["availability"],
        },
        "known_item_hit_at_5": {
            "value": round(known_rate, 3) if known_rate is not None else None,
            "target": SLA["known_item_hit_at_5"],
            "pass": (known_rate is None or known_rate >= SLA["known_item_hit_at_5"]),
        },
        "dated_share": {
            "value": round(dated_share, 3), "target": SLA["dated_share"],
            "pass": dated_share >= SLA["dated_share"],
        },
    }
    if mode == "live":
        sla_checks["live_p95_ms"] = {
            "value": round(p95, 1), "target": SLA["live_p95_ms"],
            "pass": p95 <= SLA["live_p95_ms"],
        }
    out = {
        "mode": mode,
        "queries": len(results),
        "attempts": attempts,
        "answered": answered,
        "usable": usable,
        "errors": errors,
        "rows": rows_count,
        "dated_rows": dated_count,
        "duplicates": duplicate_count,
        "availability": round(availability, 3),
        "known_item_hit_at_5": round(known_rate, 3) if known_rate is not None else None,
        "known_item": f"{hits}/{len(content_expectations)}"
                      if content_expectations else "not counted",
        "structural": f"{structural_hits}/{len(structural)}" if structural else "not counted",
        "dated_share": round(dated_share, 3),
        "p50_ms": round(statistics.median(all_lats), 1) if all_lats else 0.0,
        "p95_ms": round(p95, 1),
        "per_source": per_source,
        "expectations": expectations,
        "missing_fixtures": missing,
        "sla": sla_checks,
        "sla_note": ("targets for this benchmark, not a service guarantee; the "
                     "sources are free public APIs"),
    }
    if extra:
        out.update(extra)
    out["ok"] = (not missing and not any(
        e.get("comparable") and not e.get("pass") for e in expectations)
        and all(c["pass"] for c in sla_checks.values()))
    return out


def _evaluate_query(q: dict, rows: list[dict], reports: list[dict]) -> list[dict]:
    from dhole_mcp import search_verticals as sv

    out = []
    for exp in (q.get("expect") or q.get("expectations") or []):
        source = exp["source"]
        # A kind can have several backends (academic = arXiv + Crossref). The
        # expectations were written against the kind's primary backend, so
        # judge the primary one and let the extra backend report on its own.
        candidates = [r for r in reports if r["source"] == source]
        primary = sv._BACKENDS.get(source)
        report = (next((r for r in candidates if r["backend"] == primary),
                       candidates[0] if candidates else None)
                  or {"status": "error", "error": "source not run"})
        result = _check_expectation(exp, rows, report)
        result["kind"] = ("content" if (exp.get("title_contains") or exp.get("url_prefix"))
                          else "structural")
        out.append(result)
    return out


def replay(queries: list[dict], fixtures: dict[tuple[str, str], dict]) -> dict:
    from dhole_mcp import search_verticals as sv

    async def _run() -> list[dict]:
        results = []
        missing = []
        for q in queries:
            used: set[tuple[str, str]] = set()

            async def fetch(url, headers, timeout, _qid=q["id"], _used=used):
                kind = _kind_for_url(url)
                if not kind:
                    raise RuntimeError(f"no frozen source for {urlparse(url).hostname}")
                rec = fixtures.get((_qid, kind))
                if rec is None:
                    raise FileNotFoundError(f"missing fixture {_qid}__{kind}.json")
                _used.add((_qid, kind))
                body = base64.b64decode(rec["body_b64"])
                digest = hashlib.sha256(body).hexdigest()
                if rec.get("sha256") and digest != rec["sha256"]:
                    raise RuntimeError(
                        f"fixture {_qid}__{kind}.json sha256 mismatch: the file "
                        "was edited after capture")
                return int(rec.get("status") or 0), body

            started = time.perf_counter()
            hits, reports = await sv.search_many(
                q["sources"], q["query"], q["max_results"], fetch=fetch,
                timeout=8.0)
            rows, report_rows = _records_to_rows(hits, reports)
            # A kind may have backends this snapshot never captured (academic
            # gained Crossref after the corpus was frozen). Their error is "this
            # corpus does not hold that backend", not "the source is broken", so
            # they stay out of the offline receipt instead of failing it.
            frozen_kinds = {kind for (qid, kind) in fixtures if qid == q["id"]}
            frozen_backends = {sv._BACKENDS[kind] for kind in frozen_kinds
                               if kind in sv._BACKENDS}
            rows = [row for row in rows if row["backend"] in frozen_backends]
            report_rows = [row for row in report_rows
                           if row["backend"] in frozen_backends]
            for kind in q["sources"]:
                if (q["id"], kind) not in used:
                    missing.append(f"{q['id']}__{kind}.json")
            results.append({
                "id": q["id"], "query": q["query"],
                "sources": q["sources"], "rows": rows, "reports": report_rows,
                "expectations": _evaluate_query(q, rows, report_rows),
                "replay_ms": round((time.perf_counter() - started) * 1000, 1),
            })
        return results, missing

    results, missing = asyncio.run(_run())
    return _summarize(results, mode="offline", missing=missing,
                      extra={"corpus": str(CORPUS.relative_to(REPO_ROOT)),
                             "corpus_fixtures": len(fixtures)})


def _pace(source: str, last: dict[str, float]) -> None:
    gap = {"news": 5.5, "code": 6.2}.get(source, 1.2)
    waited = time.monotonic() - last.get(source, 0.0)
    if waited < gap:
        time.sleep(gap - waited)
    last[source] = time.monotonic()


def capture(queries: list[dict], *, force: bool = False) -> dict:
    from dhole_mcp import search_verticals as sv

    CORPUS.mkdir(parents=True, exist_ok=True)
    last: dict[str, float] = {}
    written = 0
    skipped = 0

    async def _run() -> tuple[list[dict], list[str]]:
        nonlocal written, skipped
        results = []
        missing = []
        for q in queries:
            for source in q["sources"]:
                path = _fixture_path(q["id"], source)
                if path.exists() and not force:
                    skipped += 1
                    print(f"  skip  {path.name} (exists; --force to recapture)",
                          file=sys.stderr)
                    continue
                _pace(source, last)
                calls = []
                retries = 0

                async def fetch(url, headers, timeout):
                    nonlocal retries
                    status, body = await sv._http_get(url, headers, timeout)
                    # GDELT's limiter returns 429 when two calls land inside its
                    # 5s window. One polite retry is the difference between a
                    # frozen "blocked" fixture and a real news sample; the
                    # status is still recorded if it refuses again.
                    while status == 429 and retries < 2:
                        retries += 1
                        await asyncio.sleep(6)
                        status, body = await sv._http_get(url, headers, timeout)
                    calls.append((url, status, body))
                    return status, body

                hits, report = await sv.search_one(
                    source, q["query"], q["max_results"], fetch=fetch, timeout=12.0)
                if not calls:
                    missing.append(path.name)
                    continue
                url, status, body = calls[0]
                if len(calls) > 1:
                    print(f"  warn  {path.name}: source made {len(calls)} calls; "
                          "only the first is frozen", file=sys.stderr)
                path.write_text(json.dumps({
                    "query_id": q["id"],
                    "query": q["query"],
                    "source": source,
                    "request_url": url,
                    "status": status,
                    "captured_at": _utc_now(),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "body_b64": base64.b64encode(body).decode("ascii"),
                    "parsed_status": report.status,
                    "parsed_count": len(hits),
                    "parsed_error": report.error,
                }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                written += 1
                print(f"  freeze {path.name}  HTTP {status} | parsed {report.status} "
                      f"| {len(hits)} row(s)", file=sys.stderr)
        return results, missing

    results, missing = asyncio.run(_run())
    fixtures = _load_fixtures()
    summary = replay(queries, fixtures)
    summary["capture"] = {"written": written, "skipped": skipped,
                          "missing": missing}
    return summary


def live(queries: list[dict]) -> dict:
    from dhole_mcp import search_verticals as sv

    async def _run() -> list[dict]:
        results = []
        for q in queries:
            started = time.perf_counter()
            hits, reports = await sv.search_many(
                q["sources"], q["query"], q["max_results"], timeout=8.0)
            rows, report_rows = _records_to_rows(hits, reports)
            results.append({
                "id": q["id"], "query": q["query"], "sources": q["sources"],
                "rows": rows, "reports": report_rows,
                "expectations": _evaluate_query(q, rows, report_rows),
                "wall_ms": round((time.perf_counter() - started) * 1000, 1),
            })
        return results

    results = asyncio.run(_run())
    return _summarize(results, mode="live", missing=[],
                      extra={"live_wall_ms": [r["wall_ms"] for r in results]})


def _human(summary: dict) -> str:
    lines = [
        f"vertical search baseline | mode={summary['mode']} | "
        f"{summary['queries']} queries / {summary['attempts']} source attempts",
        "",
        f"  {'source':<12} {'attempts':>8} {'answered':>9} {'usable':>7} "
        f"{'rows':>5} {'dated':>6} {'p50':>8} {'p95':>8}",
    ]
    for source, slot in sorted(summary["per_source"].items()):
        lines.append(
            f"  {source:<12} {slot['attempts']:>8} {slot['answered']:>9} "
            f"{slot['usable']:>7} {slot['rows']:>5} {slot['dated']:>6} "
            f"{slot['p50_ms']:>7.1f}ms {slot['p95_ms']:>7.1f}ms")
    lines += [
        "",
        f"  availability {summary['availability']:.1%} | "
        f"known-item@5 {summary['known_item']} | "
        f"structural {summary['structural']} | "
        f"dated {summary['dated_share']:.1%} | duplicates {summary['duplicates']}",
    ]
    if summary["missing_fixtures"]:
        lines.append("  missing fixtures: " + ", ".join(summary["missing_fixtures"]))
    bad = [e for e in summary["expectations"]
           if e.get("comparable") and not e.get("pass")]
    for e in bad:
        lines.append(f"  FAIL {e['id']}/{e['source']}: {e.get('reason', '')}")
    skipped = [e for e in summary["expectations"] if not e.get("comparable")]
    for e in skipped:
        lines.append(f"  skip {e['id']}/{e['source']}: {e.get('reason', '')}")
    lines.append("")
    for name, check in summary["sla"].items():
        mark = "pass" if check["pass"] else "FAIL"
        value = check["value"]
        shown = "n/a" if value is None else (
            f"{value:.1%}" if isinstance(value, float) and name != "live_p95_ms"
            else (f"{value:.0f}ms" if name == "live_p95_ms" else str(value)))
        lines.append(f"  SLA[{mark}] {name}: {shown} (target {check['target']})")
    lines.append(f"  {summary['sla_note']}")
    lines.append(f"  overall: {'PASS' if summary['ok'] else 'FAIL'}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--live", action="store_true",
                        help="query the public APIs instead of replaying the corpus")
    parser.add_argument("--capture", action="store_true",
                        help="refresh the frozen corpus (implies --live) and then replay it")
    parser.add_argument("--force", action="store_true",
                        help="with --capture: overwrite existing fixtures")
    parser.add_argument("--queries", type=pathlib.Path, default=QUERIES)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    queries = load_queries(args.queries)
    if args.capture:
        summary = capture(queries, force=args.force)
    elif args.live:
        summary = live(queries)
    else:
        summary = replay(queries, _load_fixtures())
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    else:
        print(_human(summary))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
