#!/usr/bin/env python3
"""描述质量代理 —— 诚实版。它测 17 段（今天 14 段）工具描述**写得好不好**，
不是路由保证。

路由由宿主里的模型决定，我们没有"把宿主装进 CI"的办法；所以本脚本不冒充路由
评测。它量的是描述里可机械检验的写作质量维度（rubric），每一维都对应一个真实
的失败模式：

1. **purpose**      第一句有动词性的职责陈述 —— 没有它，agent 只能靠名字猜。
2. **boundary**     说了"什么时候不用我 / 用谁代替" —— 路由错的头号来源是边界
                    没写（smart_fetch 的描述里"many pages of one site: smart_crawl"
                    就是在画这条线）。
3. **hard_limits**  数值上限/单位写进描述或指向说明书 —— 单位混乱（ms vs 秒）
                    是这个仓库修过的真 bug 类。
4. **pointer**      深细节指向 dhole_doc/sys_guide 而不是全写在常驻层 —— 与
                    W9 判据同源：贵的不是工具多，是说明书写在常驻层。
5. **required_named** 必填参数在描述里被点名 —— schema 的 required 只对"已经
                    决定调用"的 agent 有用，选工具阶段看的是描述。

输出是每工具的 rubric 通过表 + 汇总。分数低不是罪 —— 是"这段描述可能让 agent
路由错"的证据，读的人自己判断。

用法：python evals/description_quality.py [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

BOUNDARY_CUES = re.compile(
    r"use .{0,40}(instead|for)|instead of|not a |NOT |don't|do not|rather than|"
    r"\buse [a-z_]+\b|instead:|if you (have|already)", re.IGNORECASE)
POINTER_CUES = re.compile(r"dhole_doc|sys_guide|handbook|guide\(")
UNIT_CUES = re.compile(r"\b(ms|milliseconds|seconds|chars|bytes|items|pages)\b|\bdefault \d", re.IGNORECASE)


def rubric(tool: dict) -> dict:
    desc = tool.get("description", "")
    schema = tool.get("inputSchema", {})
    required = list(schema.get("required") or [])
    props = schema.get("properties") or {}
    first_sentence = re.split(r"[.\n]", desc, maxsplit=1)[0]
    checks = {
        # 有动词、且长度像一句话职责而不是一个词
        "purpose": bool(first_sentence.strip()) and len(first_sentence) > 12,
        "boundary": bool(BOUNDARY_CUES.search(desc)),
        "hard_limits": bool(UNIT_CUES.search(desc)) or bool(POINTER_CUES.search(desc)),
        "pointer": bool(POINTER_CUES.search(desc)),
        "required_named": all(
            (req in desc)
            or any(req in (props.get(p, {}).get("description") or "") for p in props)
            for req in required) if required else True,
    }
    return {"tool": tool["name"], "checks": checks,
            "score": sum(checks.values()), "of": len(checks)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", metavar="OUT")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    from dhole_mcp.server import MasterFetchServer
    rows = [rubric(t) for t in MasterFetchServer._TOOL_DEFS]

    print("== 描述质量代理（rubric 通过表；测描述质量，不是路由保证）==")
    print(f"{'tool':<14} {'score':>5}  " + " ".join(
        f"{k[:9]:>9}" for k in rows[0]["checks"]))
    for r in rows:
        marks = " ".join(("yes" if v else " - ").rjust(9)
                       for v in r["checks"].values())
        print(f"{r['tool']:<14} {r['score']:>3}/{r['of']}  {marks}")
    avg = sum(r["score"] for r in rows) / len(rows)
    print(f"\naverage {avg:.2f}/{rows[0]['of']} over {len(rows)} tools")
    fails = [(r["tool"], k) for r in rows for k, v in r["checks"].items() if not v]
    if fails:
        print("未过项:", ", ".join(f"{t}.{k}" for t, k in fails))

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"saved: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
