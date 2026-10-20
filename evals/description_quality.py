#!/usr/bin/env python3
"""描述质量代理 —— 诚实版。它测 17 段（今天 14 段）工具描述**写得好不好**，
不是路由保证。

路由由宿主里的模型决定，我们没有"把宿主装进 CI"的办法；所以本脚本不冒充路由
评测。它量的是描述里可机械检验的写作质量维度（rubric），每一维都对应一个真实
的失败模式：

1. **purpose**      第一句有动词性的职责陈述 —— 没有它，agent 只能靠名字猜。
2. **boundary**     说了"什么时候不用我 / 用谁代替" —— 路由错的头号来源是边界
                    没写（web_read 的描述里"many pages of one site: web_crawl"
                    就是在画这条线）。
3. **hard_limits**  数值上限/单位写进描述或指向说明书 —— 单位混乱（ms vs 秒）
                    是这个仓库修过的真 bug 类。
4. **pointer**      深细节指向 sys_guide/sys_guide 而不是全写在常驻层 —— 与
                    W9 判据同源：贵的不是工具多，是说明书写在常驻层。
5. **required_named** 必填参数在描述里被点名 —— schema 的 required 只对"已经
                    决定调用"的 agent 有用，选工具阶段看的是描述。
6. **advice_consistent** 描述点名的 `param='value'` 是**这颗工具**真收得下的那一颗 ——
                    17.0 的期 1.3 / 1.4 / 1.5 三条谎长在响应侧（建议教人抬一颗已经给过的
                    `timeout`、教人换一枚过不了同一个读取器的 `extraction_type`、教人向
                    `web_request` 传一颗它名单外的 `anti_detect`）；同一句话在常驻侧的对
                    应物就是这一段文字。参数名单与取值名单都不在这里复制，读
                    `server._TOP_LEVEL_ARGS` 与 `params._ENUM_ARGS`，所以"描述与实现各说
                    一套"这一族只有一个修法：把表改对。点名别的工具的参数是允许的 ——
                    只要那句话把那扇门的名字一起写出来。

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
POINTER_CUES = re.compile(r"sys_guide|sys_guide|handbook|guide\(")
UNIT_CUES = re.compile(r"\b(ms|milliseconds|seconds|chars|bytes|items|pages)\b|\bdefault \d", re.IGNORECASE)


#: `param='value'` 是"把这颗传进来"的语法。裸 `param=`（没有取值）不在射程：描述里
#: "caps: sys_guide(topic='behavior')"、"takes `headers=`" 这类是在**指名字**，不是给取值。
MENTION_RE = re.compile(r"\b([a-z][a-z0-9_]{2,})='([a-z_]+)'")


def _unowned_knobs(tool: dict) -> list[str]:
    """这段描述里点名的 `param='value'`，哪些是**这颗工具**传不进去的。

    两张名单都不是这里的作者：参数面是 `server._TOP_LEVEL_ARGS`（dispatch 逐颗工具读的
    那份，"Unsupported argument(s)" 就是它写的），取值面是 `params._ENUM_ARGS`
    （`_coerce_arg_types` 当场按它拒）。

    允许的一种写法：那句话把拿着它的**另一扇门**一起点名了（`tools.md` 为
    `extraction_type='check'` 立的说法，"that receipt is web_read's"）—— 那是调用方撞得
    到的下一条地址，不是谎。不写名字才是。
    """
    from dhole_mcp.models import ResponseModel
    from dhole_mcp.params import _ENUM_ARGS
    from dhole_mcp.server import _TOP_LEVEL_ARGS

    name = tool["name"]
    texts = [tool.get("description", "")]
    texts += [(p or {}).get("description", "")
              for p in (tool.get("inputSchema") or {}).get("properties", {}).values()]
    # 只量"它说这是一颗参数"的那一类名字。`error='robots_disallowed'` 讲的是**响应字段**
    # 的取值（web_read 与 web_crawl 的描述里各有一句），不是让人传的东西；把它算成谎，
    # 这一维就只剩噪声。名单也不在这里抄：一颗名字是参数当且仅当它在某一扇门的
    # `_TOP_LEVEL_ARGS` 里，是响应字段当且仅当它在 `ResponseModel` 的字段里 —— 两个都不在
    # 的名字（`anti_detct='off'` 那种拼错）照旧算谎。
    any_param = {p for names in _TOP_LEVEL_ARGS.values() for p in names}
    any_field = set(ResponseModel.model_fields)

    def passable(tool_name: str, param: str, value: str) -> bool:
        if param not in _TOP_LEVEL_ARGS.get(tool_name, ()):
            return False
        allowed = _ENUM_ARGS.get(tool_name, {}).get(param)
        return allowed is None or value in allowed

    all_tools = set(_TOP_LEVEL_ARGS)
    offenders: list[str] = []
    for text in texts:
        for sentence in re.split(r"(?<=[.;]) |-- ", text):
            for param, value in MENTION_RE.findall(sentence):
                if param not in any_param and param in any_field:
                    continue   # 讲的是响应字段的取值，不是一颗要人传的参数
                if passable(name, param, value):
                    continue
                holders = [t for t in all_tools if passable(t, param, value)]
                if any(h in sentence for h in holders):
                    continue
                offenders.append(f"{param}={value!r} (holders: {holders or 'none'})")
    return sorted(set(offenders))


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
        # 描述里的建议只许点这颗工具真收得下的参数（17.0 的 1.3/1.4/1.5 三条谎都长在
        # **响应侧**，那一族由 tests/test_advice_names_only_the_knobs_this_tool_can_take.py
        # 钉；这一维是同一句话在常驻侧的对应物：agent 选工具阶段读的是这段文字）
        "advice_consistent": not _unowned_knobs(tool),
    }
    return {"tool": tool["name"], "checks": checks,
            "score": sum(checks.values()), "of": len(checks),
            "unowned_knobs": _unowned_knobs(tool)}


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
    named = [(r["tool"], r["unowned_knobs"]) for r in rows if r["unowned_knobs"]]
    for tool_name, knobs in named:
        print(f"  {tool_name} 点名的传不进去的参数: {knobs}")

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2) + "\n",
                                   encoding="utf-8")
        print(f"saved: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
