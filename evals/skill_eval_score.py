"""Score the dhole-web skill calibration runs against evals.json.

Reads the server-side evidence, not the subagent's recollection: every
``web_read`` a run performed was appended to ``<case>-<arm>.calls.jsonl`` by
``dhole_eval_cli.py`` at dispatch time, so "one call" and "which arguments" are
facts about the process rather than claims in an answer. Only the assertions that
read the agent's *final text* use ``<case>-<arm>.answer.txt``.

    python evals/skill_eval_score.py evals.json evidence/
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _calls(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _first_args(calls: list[dict], tool: str) -> dict:
    """The call the assertions about *arguments* read: the first one that used the
    feature, not the first one issued.

    An agent that probes the page with a bare read and then fans out made the right
    call; grading its **first** argument set would score the probe as the answer and
    every case would read "no nested_selector". Efficiency is a separate assertion
    (`single_call`), so the two questions stay two different measurements.
    """
    for c in calls:
        if c.get("tool") == tool:
            return c.get("args") or {}
    return {}


def _best_args(calls: list[dict], tool: str, path: str) -> dict:
    """The first call that actually set `path` (falling back to the first call)."""
    for c in calls:
        if c.get("tool") == tool and _has(path, c.get("args") or {}):
            return c["args"]
    return _first_args(calls, tool)


def _has(path: Path, args: dict) -> bool:
    return path in args and args[path] not in (None, "", [], {})


def _string(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v


def _num(v: Any):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(a: dict, calls: list[dict], text: str) -> tuple[bool, str]:
    kind = a["type"]
    tool = a.get("tool", "web_read")
    path = a.get("path")
    op = a.get("op", "nonempty")
    want = a.get("value")
    args = _first_args(calls, tool)
    if kind == "arg":
        # 读的是"那颗参数出现的那一次调用"，不是第一次调用（见 _best_args）。
        args = _best_args(calls, tool, path)

    if kind == "any_of":
        # 组合断言（F-A-J2）：分支内全要，分支间任一。存在是为了让「两条都合法
        # 的路」不再被迫只判一条 —— tag-pages 三档全红量出的就是尺子与语料在
        # 『哪条路才算对』上打架（nested 扇出 vs 贴上限的 urls= 批量）。
        for branch in a.get("branches", []):
            results = [check(sub, calls, text) for sub in branch]
            if all(ok for ok, _ in results):
                return True, " / ".join(w for _, w in results)
        miss = [sub.get("name") or sub.get("path") or sub.get("type")
                for branch in a.get("branches", []) for sub in branch]
        return False, f"no branch matched (needs one of: {miss})"
    if kind == "no_identical_retry":
        seq = [json.dumps(c.get("args") or {}, ensure_ascii=False, sort_keys=True)
               for c in calls if c.get("tool") == tool]
        dupes = sum(1 for x, y in zip(seq, seq[1:]) if x == y)
        return dupes == 0, f"{dupes} identical consecutive retry(ies)"
    if kind == "tool_count":
        n = sum(1 for c in calls if c.get("tool") == tool)
        ok = {">=": n >= want, "==": n == want, "<=": n <= want}[op]
        return ok, f"{tool} ×{n} ({op} {want})"
    if kind == "no_arg":
        hits = [c for c in calls
                if c.get("tool") == tool and _has(path, c.get("args") or {})]
        return not hits, f"{len(hits)} call(s) carried {path}"
    if kind == "absent_or":
        hits = [c for c in calls if c.get("tool") == tool and _has(path, c.get("args") or {})
                and _num(c["args"].get(path)) != 0]
        return not hits, f"{len(hits)} call(s) set {path}"
    if kind == "any_arg":
        hits = [c for c in calls if c.get("tool") == tool and _has(path, c.get("args") or {})]
        if a.get("require_success"):
            # 只认「这一发成了」的调用。CLI 的 JSONL 每发都带 error（含工具自判
            # 的失败回答，见 ruler_defects）；参数出现在一个被拒的调用上不算数
            # —— 那发调用什么都没读到。实测教训（第四轮）：web_read 没有
            # encoding=，转向分支曾给被拒的 encoding=gbk 记 1/1 绿。
            hits = [c for c in hits if not c.get("error")]
        if op == "nonempty":
            return bool(hits), f"{len(hits)} call(s) set {path}"
        got = any(_string(want) in _string(c["args"].get(path)) for c in hits)
        return got, (f"{path}={want!r} on {len(hits)} call(s)" if got
                     else f"no call with {path} containing {want!r}")
    if kind == "arg":
        if not _has(path, args):
            return False, f"first {tool} call has no {path}"
        got = args.get(path)
        if op == "nonempty":
            return True, f"{path}={_string(got)[:60]}"
        if op == "contains":
            return _string(want) in _string(got), f"{path}={_string(got)[:60]}"
        if op == "len_ge":
            n = len(got) if isinstance(got, list) else None
            if n is None:
                return False, f"{path} is not a list (len_ge measures batches)"
            return n >= want, f"{path} len={n} (>= {want})"
        n = _num(got)
        if n is None:
            return False, f"{path}={got!r} is not a number"
        return {">=": n >= want, "==": n == want, "<=": n <= want}[op], f"{path}={n:g}"
    if kind == "tool":
        hit = bool(args)
        if "arg" in a:
            sub = dict(type="arg", path=a["arg"], op=op, value=want, tool=tool)
            return check(sub, calls, text)
        return hit, f"{tool} called" if hit else f"no {tool} call"
    if kind == "text":
        if op == "not_contains":
            bad = [w for w in (want if isinstance(want, list) else [want]) if w in text]
            return not bad, f"answer says {bad}" if bad else "no forbidden phrasing"
        if op == "contains_any":
            got = [w for w in want if w in text]
            return bool(got), f"matched {got[:2]}" if got else f"none of {want[:3]}"
        return want in text, f"{'found' if want in text else 'missing'} {want!r}"
    raise ValueError(f"unknown assertion type {kind!r}")


def score_case(case: dict, calls: list[dict], text: str) -> tuple[int, int, list]:
    """一格的判分：返回 (计入的通过数, 计入的分母, [(断言行, ok, 说明)])。

    record_only 的断言（few_calls，2026-10-06 判探针是合理保守行为）照印不进
    分母 —— 它们记录行为形状，不再决定一格的红绿。
    """
    rows = []
    row_pass = 0
    n = 0
    for a in case["assertions"]:
        ok, why = check(a, calls, text)
        if a.get("record_only"):
            rows.append((a["name"], ok, why, True))
            continue
        row_pass += ok
        n += 1
        rows.append((a["name"], ok, why, False))
    return row_pass, n, rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("evals")
    ap.add_argument("evidence")
    ap.add_argument("--arm", default="")
    ns = ap.parse_args()

    spec = json.loads(Path(ns.evals).read_text(encoding="utf-8"))
    ev = Path(ns.evidence)
    arms = [ns.arm] if ns.arm else list(spec.get("arms", {}))
    width = max(len(a["id"]) for a in spec["evals"]) + 2

    total = {}
    for arm in arms:
        print(f"\n=== {arm} ===")
        passed = 0
        for case in spec["evals"]:
            stem = f"{case['id']}-{arm}"
            calls = _calls(ev / f"{stem}.calls.jsonl")
            ans = (ev / f"{stem}.answer.txt")
            text = ans.read_text(encoding="utf-8", errors="replace") if ans.is_file() else ""
            if not calls:
                print(f"  {case['id']:<{width}} NO EVIDENCE (run did not record)")
                continue
            row_pass, n, rows = score_case(case, calls, text)
            for name, ok, why, record in rows:
                mark = ("note" if record else ("ok  " if ok else "MISS"))
                print(f"  {case['id']:<{width}}{mark} {name}: {why}")
            passed += row_pass == n
            print(f"  {case['id']:<{width}}→ {row_pass}/{n}"
                  + (" (+record_only)" if any(r[3] for r in rows) else ""))
        print(f"  cells fully green: {passed}/{len(spec['evals'])}")
        total[arm] = passed

    if len(arms) > 1:
        print("\n=== ablation ===")
        base = total[arms[-1]]
        for arm in arms:
            print(f"  {arm}: {total[arm]}/{len(arms and spec['evals'])} "
                  f"({total[arm] - base:+d} vs {arms[-1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
