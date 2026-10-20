"""期 0.4：池子基线 —— 每一轮到底有几个独立家族真的说了话，说不了的各是因为什么。

`ranking_baseline.py` 量的是"结果排得好不好"（离线、可复现、有 qrels）。这一条量的是
另一件事：**判别力的地基有多宽**。dhole 免密搜索里唯一免费、零 token、确定性的权威信号
是跨索引家族共识（`engines_consensus`），而它今天每轮实际有几个家族参与，只有跑的人
自己看得见 —— `consensus_basis` 说 "degraded_pool"，不说 *谁* 没说话、*为什么* 没说。

为什么这件事必须单独一个量具，而且不能离线：`tests/engine_fixtures/` 冻的是**字节**
不是时序。一家引擎"3.1 秒才答"与"它被 2.0 秒的软截止取消了"在离线夹具里是同一个形状，
而这两件事的修法相反（前者要抬截止，后者要认下这一家确实贡献不了）。所以这一条是
**运行期**的量具：改 quorum（`_SOFT_DEADLINE`）/ 改超时 / 动引擎名单之前跑一遍，之后
再跑一遍，把慢多少摊开在同一张表上。

报的每一格都来自响应或 `engine_health()` 已有的字段，不发明数：

* 每一问：`duration_ms`、结果条数、`consensus_basis`、`engines_used` / `engine_blocked`
  / `engine_empty` / `engine_preempted` 四桶（这四桶的分工是响应自己的判据：
  blocked=答不了，empty=答了而没可用结果，preempted=够数了被取消，**不是失败**）。
* 每一轮之后：`engine_health()` 的 per-engine `{verdict, status, last_nodes, last}`。
  注意这里**没有每引擎耗时** —— `_ENGINE_YIELD` 记的是 status / 节点数 / 可用数 /
  时间戳，没有时钟。想按引擎排慢的先后，得先给那一层加一颗表，那是另一刀的账。

用法
----
    python evals/pool_baseline.py                      # 12 问 × 3 轮
    python evals/pool_baseline.py --queries 4 --rounds 1 --json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"

DEFAULT_QUERIES = 12
DEFAULT_ROUNDS = 3


def load_queries(n: int) -> list[dict]:
    """从 `evals/queries.jsonl` 取前 n 条。

    为什么是这一份：它是 `evals/README.md` 记的 72 题活集（只增不减），也是
    `capture_serp.py` 冻结集 A 语料时读的同一颗常量。`ranking_baseline.py` 拿的是那份
    语料，不是这份题集 —— 两条基线"同源"靠的是这条生成链，所以题目不许在这儿另起一张表。
    """
    path = HERE / "queries.jsonl"
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        rows.append(row)
        if len(rows) >= n:
            break
    if len(rows) < n:
        print(f"!! 题集只有 {len(rows)} 条（要 {n}）", file=sys.stderr)
    return rows


def family_tally(round_rows: list[dict]) -> dict[str, Counter]:
    """把一轮里每一家的下落归到四桶，并且**逐家点名**。

    存在的理由是这条判据：`1 of 6 (degraded_pool)` 这一句今天分不清五家是拒答、被取消、
    还是根本没轮到。桶与响应字段一一对应，不在这里重判（`_classify_yield` 才是属主）：

      answered   ← engines_used        答了且带回结果
      blocked    ← engine_blocked     限速 / 验证码 / 超时 / 报错：答不了
      empty      ← engine_empty       答了而这轮没有可用结果
      cancelled  ← engine_preempted   够数了被取消 —— 这不是失败，别混进上面三桶
    """
    tally: dict[str, Counter] = {}
    for row in round_rows:
        for bucket, key in (("answered", "used"), ("blocked", "blocked"),
                            ("empty", "empty"), ("cancelled", "preempted")):
            for engine in row.get(key) or []:
                tally.setdefault(engine, Counter())[bucket] += 1
    return tally


def why_that_engine(name: str, buckets: Counter, health: dict) -> str:
    """一家的这一轮结论，写成一句人话（哪一桶最多 + 池子记的 verdict）。

    两家都"没带回结果"而原因相反：一家被取消（快池的正常代价），一家在冷却（真的坏了）。
    这句话是给改 quorum 的人看的，不是给日志看的。
    """
    top = buckets.most_common(1)[0][0] if buckets else "not_seen"
    verdict = (health.get(name) or {}).get("verdict", "unknown")
    if top == "cancelled":
        return (f"{top} (verdict={verdict}) - 够数了被取消，这一轮没有观测；"
                "抬软截止才有机会看到它")
    if top == "blocked":
        return f"{top} (verdict={verdict}) - 答不了：限速 / 验证码 / 超时 / 报错"
    if top == "empty":
        return f"{top} (verdict={verdict}) - 答了而没可用结果"
    return f"{top} (verdict={verdict})"


async def run_round(server, queries: list[dict], round_no: int) -> list[dict]:
    from dhole_mcp.search_metasearch import engine_health

    rows = []
    for q in queries:
        res = await server.web_search(q["text"], max_results=10)
        # 四颗字段名直接读，不 getattr 兜底：响应上任何一颗改了名，这张表要**当场炸**，
        # 而不是静默把那一桶报成 0 —— 一张"空=0"的基线正是本量具要消灭的那种谎。
        rows.append({
            "round": round_no, "id": q["id"], "lang": q.get("lang", ""),
            "ms": round(float(res.duration_ms or 0), 1),
            "results": len(res.results or []),
            "basis": res.consensus_basis,
            "used": list(res.engines_used or []),
            "blocked": list(res.engine_blocked or []),
            "empty": list(res.engine_empty or []),
            "preempted": list(res.engine_preempted or []),
            "error": res.error or "",
        })
        print(f"   r{round_no} · {q['id']:<5} {rows[-1]['ms']:>7.0f}ms "
              f"{rows[-1]['results']:>3} 条  basis={rows[-1]['basis'] or '-':<15} "
              f"答={len(rows[-1]['used'])} 拒={len(rows[-1]['blocked'])} "
              f"空={len(rows[-1]['empty'])} 取消={len(rows[-1]['preempted'])}")
    health = engine_health()
    tally = family_tally(rows)
    print(f"   ── 第 {round_no} 轮的池子（每一家各是因为什么）")
    for name in sorted(set(tally) | set(health)):
        print(f"      {name:<14} {why_that_engine(name, tally.get(name, Counter()), health)}")
    return rows


async def main() -> int:
    ap = argparse.ArgumentParser(description="池子健康基线（要联网）")
    ap.add_argument("--queries", type=int, default=DEFAULT_QUERIES)
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--json", action="store_true", help="另存 .runtime/pool_baseline.json")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from dhole_mcp.server import MasterFetchServer

    queries = load_queries(args.queries)
    if not queries:
        print("没有可用题目", file=sys.stderr)
        return 1
    server = MasterFetchServer()
    print(f"池子基线：{len(queries)} 问 × {args.rounds} 轮（真联网，走这台机器的代理）\n")

    all_rows: list[dict] = []
    for round_no in range(1, args.rounds + 1):
        all_rows.extend(await run_round(server, queries, round_no))
        print()

    answered = [len(r["used"]) for r in all_rows]
    cancelled = [len(r["preempted"]) for r in all_rows]
    blocked = [len(r["blocked"]) for r in all_rows]
    print("═══ 汇总")
    print(f"    每问实答家族数  mean={statistics.mean(answered):.2f} "
          f"min={min(answered)} max={max(answered)}")
    print(f"    每问被取消数    mean={statistics.mean(cancelled):.2f}")
    print(f"    每问答不了数    mean={statistics.mean(blocked):.2f}")
    print(f"    单问耗时        median={statistics.median([r['ms'] for r in all_rows]):.0f}ms "
          f"p95={sorted(r['ms'] for r in all_rows)[int(len(all_rows) * 0.95) - 1]:.0f}ms")
    basis = Counter(r["basis"] or "-" for r in all_rows)
    print(f"    consensus_basis 分布：{dict(basis)}")
    if len(all_rows) < 20:
        # 分位数在三五个样本上不是分布，只是"这几个数里最大的那个"。冒烟跑出来的数
        # 不许被读成基线 —— 基线的口径是 `--queries 12 --rounds 3`。
        print(f"    !! 样本只有 {len(all_rows)} 问次：上面那几个分位数不构成基线，"
              "默认口径是 12 问 × 3 轮")
    print("    （每引擎耗时这里没有：`_ENGINE_YIELD` 不记时钟。要按引擎排慢的先后，"
          "先得给那一层加一颗表 —— 那是要摊开的代价，不是这条基线的产物。）")

    if args.json:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        out = RUNTIME / "pool_baseline.json"
        out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        print(f"\n已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
