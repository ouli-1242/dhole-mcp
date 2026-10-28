#!/usr/bin/env python3
"""句子→claim 归因质量：用真实查询切出的两个子问题量一次挂错率。

`corroborate.cross_check` 先把 query 切成 claim，再把来源正文的每一句按 TF-IDF
余弦归到最近的 claim。切错 claim 的代价不是排序差一名，而是把 A 的说法算进 B 的
支持/反对里 —— 所以这里用**来源归属已知**的样本量它：

  1. 从 `evals/queries.jsonl`（real_query_eval.py 的真查询集）取一条查询，
     在词边界切成两个子问题 A / B；`A？B` 就是一条两条 claim 的复合问句；
  2. 分别用 A、B 各搜一次，拿回的每个结果都带着「它属于哪个 claim」的标签；
  3. 把两边结果混在一起跑归因，逐句比较「模型归到的 claim」与「它来自哪个子问题」；
  4. 另给一个 clear 子集：句子里有本 claim 的独有词、没有另一 claim 的独有词 ——
     这些句子的归属没有歧义，挂错就是真挂错。

`--pages N` 会把每个子问题的前 N 条结果读成全文（不只用标题+摘要），这样
`--window 1` 的「句子 + 前后各一句」才有东西可比。两种窗口都会跑，报告里并排。

用法：

    python evals/cross_check_attribution.py run --limit 10
    python evals/cross_check_attribution.py run --limit 5 --pages 2 --window 1

这是测量脚本，不是 MCP 工具；不改变 `corroborate` 的判据。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import statistics
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dhole_mcp import corroborate as c  # noqa: E402

DEFAULT_QUERIES = REPO_ROOT / "evals" / "queries.jsonl"
DEFAULT_OUT = REPO_ROOT / "evals" / ".runtime" / "cross_check_attribution"
MAX_EXAMPLES = 5


def load_queries(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = str(obj.get("text") or obj.get("query") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append({"qid": str(obj.get("id") or f"q{len(rows) + 1:03d}"),
                     "query": text})
    return rows


def split_aspects(text: str) -> tuple[str, str] | None:
    """在词边界把一条真实查询切成两个子问题；切不动就跳过。"""
    tokens = text.split()
    if len(tokens) < 4:
        return None
    for cut in sorted(range(2, len(tokens) - 1),
                      key=lambda value: abs(value - len(tokens) / 2)):
        left, right = " ".join(tokens[:cut]), " ".join(tokens[cut:])
        if len(left) >= 6 and len(right) >= 6:
            return left, right
    return None


def _run(args: list[str], timeout: float = 180.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dhole_mcp", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def search(query: str, max_results: int) -> list[dict]:
    proc = _run(["search", query, "--max-results", str(max_results),
                 "--json", "--quiet"])
    if proc.returncode not in (0, 1):
        return []
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    out = []
    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        out.append({"url": str(row.get("url") or ""),
                    "text": (title + ". " + snippet).strip(". "),
                    "via": "snippet"})
    return out


def read_page(url: str) -> str:
    proc = _run(["read", url, "--json", "--quiet"])
    if proc.returncode not in (0, 1):
        return ""
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    if not payload.get("content_ok"):
        return ""
    content = payload.get("content")
    if isinstance(content, list):
        return "\n".join(str(part) for part in content)
    return str(content or "")


def collect_blocks(aspects: tuple[str, str], max_results: int,
                   pages: int) -> list[dict]:
    """每个结果带 ``claim`` 标签：0 = 子问题 A，1 = 子问题 B。"""
    blocks: list[dict] = []
    seen_urls: set[str] = set()
    page_budget = {0: pages, 1: pages}
    for claim_index, aspect in enumerate(aspects):
        for row in search(aspect, max_results):
            url = row["url"]
            if url and url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            text = row["text"]
            via = "snippet"
            if pages > 0 and url and page_budget[claim_index] > 0:
                page_budget[claim_index] -= 1
                page = read_page(url)
                if len(page) >= 200:
                    text, via = page, "page"
            blocks.append({"claim": claim_index, "url": url,
                           "text": text, "via": via})
    return blocks


def _unique_tokens(claim_tokens: list[set[str]],
                   index: int) -> tuple[set[str], set[str]]:
    own = set(claim_tokens[index])
    other = set().union(*(tokens for i, tokens in enumerate(claim_tokens)
                          if i != index)) if len(claim_tokens) > 1 else set()
    return own - other, other - own


def _legacy_stance(claim: "c._Claim", sentence: str, open_question: bool) -> str:
    """改前的判据：非开放式问句一律用否定极性。只用于同一批句子的前后对照。"""
    if (not open_question
            and c._negation_parity(claim.text) != c._negation_parity(sentence)):
        return "oppose"
    if c._antonym_flip(claim.text, sentence) or c._numeric_flip(claim.text, sentence):
        return "oppose"
    return "support"


def attribute(claims_text: list[str], blocks: list[dict], window: int) -> list[dict]:
    """逐句归因；``window`` = 归因时额外带上的前后句数（分类仍看当前句）。"""
    open_question = c._is_open_question("？".join(claims_text))
    yes_no_question = c._is_yes_no_question("？".join(claims_text))
    claims = [c._Claim(text=text, yes_no_question=yes_no_question)
              for text in claims_text]
    for claim in claims:
        claim.tokens = c._tokenize(c._QUESTION_PREFIX.sub("", claim.text))

    rows: list[dict] = []
    for block in blocks:
        sentences = c._split_sentences(block["text"])
        for index, sentence in enumerate(sentences):
            context = sentence
            if window > 0:
                start, stop = max(0, index - window), index + window + 1
                context = " ".join(sentences[start:stop])
            rows.append({"sentence": sentence, "context": context,
                         "claim": int(block["claim"]), "via": block["via"]})

    if not rows:
        return []
    vectors = c._vectors([claim.tokens for claim in claims]
                         + [c._tokenize(row["context"]) for row in rows])
    claim_vectors = vectors[:len(claims)]
    sentence_vectors = vectors[len(claims):]

    claim_tokens = [set(claim.tokens) for claim in claims]
    for row, vector in zip(rows, sentence_vectors):
        scores = [c._cosine(vector, claim_vector)
                  for claim_vector in claim_vectors]
        best = max(range(len(scores)), key=lambda i: scores[i])
        ordered = sorted(scores, reverse=True)
        row["assigned"] = best
        row["score"] = round(scores[best], 4)
        row["margin"] = round(ordered[0] - (ordered[1] if len(ordered) > 1 else 0.0), 4)
        row["attributed"] = scores[best] >= c.ATTRIBUTION_FLOOR
        if not row["attributed"]:
            continue
        stance = c._stance(claims[best], row["sentence"])
        row["stance"] = stance
        row["stance_legacy"] = _legacy_stance(
            claims[best], row["sentence"], open_question)
        if stance == "oppose":
            row["triggers"] = {
                "negation": (yes_no_question
                             and c._negation_parity(claims[best].text)
                             != c._negation_parity(row["sentence"])),
                "antonym": c._antonym_flip(claims[best].text, row["sentence"]),
                "numeric": c._numeric_flip(claims[best].text, row["sentence"]),
            }
        if row["stance_legacy"] == "oppose":
            row["triggers_legacy"] = {
                "negation": (not open_question
                             and c._negation_parity(claims[best].text)
                             != c._negation_parity(row["sentence"])),
                "antonym": c._antonym_flip(claims[best].text, row["sentence"]),
                "numeric": c._numeric_flip(claims[best].text, row["sentence"]),
            }
        own, other = _unique_tokens(claim_tokens, row["claim"])
        sentence_tokens = set(c._tokenize(row["sentence"]))
        row["clear"] = bool(sentence_tokens & own) and not (sentence_tokens & other)
    return rows


def summarise(rows: list[dict]) -> dict:
    attributed = [row for row in rows if row.get("attributed")]
    clear = [row for row in attributed if row.get("clear")]
    wrong = [row for row in attributed if row["assigned"] != row["claim"]]
    clear_wrong = [row for row in clear if row["assigned"] != row["claim"]]
    correct_margins = [row["margin"] for row in attributed
                       if row["assigned"] == row["claim"]]
    wrong_margins = [row["margin"] for row in wrong]
    oppose = [row for row in attributed if row.get("stance") == "oppose"]
    oppose_legacy = [row for row in attributed
                     if row.get("stance_legacy") == "oppose"]
    triggers = {"negation": 0, "antonym": 0, "numeric": 0}
    triggers_legacy = {"negation": 0, "antonym": 0, "numeric": 0}
    negation_only = 0
    negation_only_legacy = 0
    for row in oppose:
        hit = row.get("triggers") or {}
        for key, value in hit.items():
            if value:
                triggers[key] += 1
        if hit.get("negation") and not hit.get("antonym") and not hit.get("numeric"):
            negation_only += 1
    for row in oppose_legacy:
        hit = row.get("triggers_legacy") or {}
        for key, value in hit.items():
            if value:
                triggers_legacy[key] += 1
        if hit.get("negation") and not hit.get("antonym") and not hit.get("numeric"):
            negation_only_legacy += 1

    def rate(values: int, total: int) -> float:
        return round(values / total, 4) if total else 0.0

    return {
        "sentences": len(rows),
        "attributed": len(attributed),
        "attribution_rate": rate(len(attributed), len(rows)),
        "misassigned": len(wrong),
        "misassignment_rate": rate(len(wrong), len(attributed)),
        "clear": len(clear),
        "clear_misassigned": len(clear_wrong),
        "clear_misassignment_rate": rate(len(clear_wrong), len(clear)),
        "correct_margin_median": round(statistics.median(correct_margins), 4)
        if correct_margins else 0.0,
        "wrong_margin_median": round(statistics.median(wrong_margins), 4)
        if wrong_margins else 0.0,
        "oppose": len(oppose),
        "oppose_triggers": triggers,
        "negation_only": negation_only,
        "oppose_legacy": len(oppose_legacy),
        "oppose_triggers_legacy": triggers_legacy,
        "negation_only_legacy": negation_only_legacy,
        "oppose_examples": [
            {"claim": row["claim"], "assigned": row["assigned"],
             "score": row["score"], "triggers": row.get("triggers") or {},
             "sentence": row["sentence"][:180]}
            for row in oppose[:MAX_EXAMPLES]
        ],
        "examples": [
            {"claim": row["claim"], "assigned": row["assigned"],
             "score": row["score"], "margin": row["margin"],
             "sentence": row["sentence"][:180]}
            for row in wrong[:MAX_EXAMPLES]
        ],
    }


def evaluate(queries: list[dict], max_results: int, pages: int,
             windows: tuple[int, ...]) -> dict:
    per_query: list[dict] = []
    for number, item in enumerate(queries, 1):
        aspects = split_aspects(item["query"])
        if aspects is None:
            print(f"[{number}/{len(queries)}] skip {item['qid']}: "
                  "cannot split into two aspects", file=sys.stderr)
            continue
        compound = aspects[0] + "？" + aspects[1]
        claims_text, _truncated = c._split_claims(compound)
        if len(claims_text) != 2:
            print(f"[{number}/{len(queries)}] skip {item['qid']}: "
                  f"split into {len(claims_text)} claims", file=sys.stderr)
            continue
        print(f"[{number}/{len(queries)}] {item['qid']} · "
              f"{aspects[0]!r} / {aspects[1]!r}", file=sys.stderr)
        blocks = collect_blocks(aspects, max_results, pages)
        if not blocks:
            print("  no blocks; skipped", file=sys.stderr)
            continue
        result = {"qid": item["qid"], "query": item["query"],
                  "aspects": list(aspects),
                  "blocks": len(blocks),
                  "pages": sum(1 for block in blocks if block["via"] == "page"),
                  "runs": {}}
        for window in windows:
            rows = attribute(claims_text, blocks, window)
            result["runs"][str(window)] = summarise(rows)
        per_query.append(result)

    aggregate = {}
    for window in windows:
        combined: list[dict] = []
        for result in per_query:
            if str(window) not in result["runs"]:
                continue
            summary = result["runs"][str(window)]
            combined.append(summary)
        totals = {
            "queries": len(combined),
            "sentences": sum(s["sentences"] for s in combined),
            "attributed": sum(s["attributed"] for s in combined),
            "misassigned": sum(s["misassigned"] for s in combined),
            "clear": sum(s["clear"] for s in combined),
            "clear_misassigned": sum(s["clear_misassigned"] for s in combined),
            "oppose": sum(s["oppose"] for s in combined),
            "negation_only": sum(s["negation_only"] for s in combined),
            "oppose_legacy": sum(s["oppose_legacy"] for s in combined),
            "negation_only_legacy": sum(s["negation_only_legacy"] for s in combined),
            "oppose_triggers": {
                key: sum(s["oppose_triggers"][key] for s in combined)
                for key in ("negation", "antonym", "numeric")
            },
            "oppose_triggers_legacy": {
                key: sum(s["oppose_triggers_legacy"][key] for s in combined)
                for key in ("negation", "antonym", "numeric")
            },
        }
        totals["misassignment_rate"] = (
            round(totals["misassigned"] / totals["attributed"], 4)
            if totals["attributed"] else 0.0)
        totals["clear_misassignment_rate"] = (
            round(totals["clear_misassigned"] / totals["clear"], 4)
            if totals["clear"] else 0.0)
        aggregate[str(window)] = totals
    return {"queries": per_query, "aggregate": aggregate}


def print_summary(report: dict) -> None:
    for window, totals in report["aggregate"].items():
        print(f"\nwindow={window}")
        print(f"  queries              {totals['queries']}")
        print(f"  sentences/attributed {totals['sentences']} / {totals['attributed']}")
        print(f"  misassigned          {totals['misassigned']} "
              f"({totals['misassignment_rate'] * 100:.1f}%)")
        print(f"  clear subset         {totals['clear']} sentences, "
              f"{totals['clear_misassigned']} wrong "
              f"({totals['clear_misassignment_rate'] * 100:.1f}%)")
        print(f"  oppose (new)         {totals['oppose']} "
              f"(negation {totals['oppose_triggers']['negation']}, "
              f"antonym {totals['oppose_triggers']['antonym']}, "
              f"numeric {totals['oppose_triggers']['numeric']}; "
              f"negation-only {totals['negation_only']})")
        print(f"  oppose (legacy)      {totals['oppose_legacy']} "
              f"(negation {totals['oppose_triggers_legacy']['negation']}, "
              f"antonym {totals['oppose_triggers_legacy']['antonym']}, "
              f"numeric {totals['oppose_triggers_legacy']['numeric']}; "
              f"negation-only {totals['negation_only_legacy']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"], nargs="?", default="run")
    parser.add_argument("--queries", type=pathlib.Path, default=DEFAULT_QUERIES)
    parser.add_argument("--limit", type=int, default=10,
                        help="queries to use (0 = all)")
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--pages", type=int, default=0,
                        help="read this many top results per aspect as full pages")
    parser.add_argument("--window", type=int, action="append", default=None,
                        help="sliding-window size to measure (repeatable; 0 = sentence only)")
    parser.add_argument("--out", type=pathlib.Path, default=None)
    args = parser.parse_args(argv)

    queries = load_queries(args.queries)
    if args.limit:
        queries = queries[:args.limit]
    windows = tuple(args.window) if args.window else (0,)
    out_dir = args.out or DEFAULT_OUT / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report = evaluate(queries, args.max_results, args.pages, windows)
    report["settings"] = {"queries": str(args.queries), "limit": args.limit,
                          "max_results": args.max_results, "pages": args.pages,
                          "windows": list(windows)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(report)
    print(f"\nreport: {out_dir / 'report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
