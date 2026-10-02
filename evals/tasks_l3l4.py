"""L3（recipe 录制回放）/ L4（watch 监控 diff）端到端场景。

计划第九章把编排轴最后两级定成"复现"与"监控"，实现落在
``src/dhole_mcp/tasks.py`` + 两个 opt-in 工具（``DHOLE_TASKS=1``）。
单元测试（``tests/test_tasks_l3l4.py``）钉的是语义；这个脚本走**完整工具调用**，
验的是那些语义在真页面上还成立：

1. 录制时坏链被拒，且**什么都没存**；
2. 重放点进长文：动作全过、拿到的确实是目标页；
3. 页面变了之后重放**大声失败**（actions_failed>0 + next_action 让重录）——
   一条悄悄返回旧页面的配方，比一条报错的配方危险得多；
4. watch 首跑只建基线，``changed`` 是 null 而**不是** false；
5. 静态页两次 check：``changed=false``；
6. 会变的页：``changed=true`` 且 diff 有行；
7. 抓取失败**不动基线**（否则一次网络故障被写成"页面清空了"）。

    python evals/tasks_l3l4.py          # 需要靶站在 127.0.0.1:8399
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8399"
DEAD = "http://127.0.0.1:8398"

# 用一次性的 DHOLE_HOME：这个脚本要写 recipe/watch 库，绝不能落在用户的
# ~/.dhole 里（那里面有真实的配方与快照）。
os.environ["DHOLE_HOME"] = tempfile.mkdtemp(prefix="dhole-l3l4-")
os.environ["DHOLE_TASKS"] = "1"
os.environ["DHOLE_IGNORE_ROBOTS"] = "1"
sys.path.insert(0, str(HERE.parent / "src"))

from dhole_mcp import tasks  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


async def main() -> int:
    s = MasterFetchServer()
    print(f"靶站 {BASE}　·　库 {os.environ['DHOLE_HOME']}\n")

    # 1. 录制时的坏链
    bad = await s.recipe(op="save", name="bad", url=f"{BASE}/",
                         actions=[{"click": ""}])
    record("录制时坏链被拒，且什么都没存", bool(bad.error)
           and tasks.get_recipe("bad") is None,
           f"error={bad.error!r} 库里={'bad' in {r['name'] for r in tasks.list_recipes()}}")

    # 2. 正常录制 + 重放
    await s.recipe(op="save", name="idx-article", url=f"{BASE}/",
                   actions=[{"click": "a[href='/article']"}],
                   note="从索引点进长文")
    run = await s.recipe(op="run", name="idx-article", allow_private=True,
                         timeout=60000)
    title = (run.result.metadata or {}).get("title", "") if run.result else ""
    record("重放点进长文：动作全过且拿到目标页",
           run.actions_failed == 0 and "配置指南" in title,
           f"actions_failed={run.actions_failed} title={title!r} "
           f"url={run.result.url if run.result else ''}")

    # 3. 页面变了之后：重放必须大声失败，而不是悄悄给回旧内容
    await s.recipe(op="save", name="idx-article", url=f"{BASE}/",
                   actions=[{"click": "a[href='/does-not-exist']"}])
    broken = await s.recipe(op="run", name="idx-article", allow_private=True,
                            timeout=60000)
    record("页面变了之后重放大声失败并让人重录",
           broken.actions_failed >= 1 and "re-record" in broken.next_action.lower(),
           f"actions_failed={broken.actions_failed} "
           f"next_action={broken.next_action[:70]!r}")
    await s.recipe(op="delete", name="idx-article")

    # 4/5. watch：首跑建基线，静态页第二次 check 无变化
    await s.web_watch(op="add", name="art", url=f"{BASE}/article")
    c1 = await s.web_watch(op="check", name="art", allow_private=True)
    record("watch 首跑只建基线，changed 是 null 而不是 false",
           c1.baseline_created and c1.changed is None,
           f"baseline_created={c1.baseline_created} changed={c1.changed!r}")
    c2 = await s.web_watch(op="check", name="art", allow_private=True)
    record("静态页第二次 check：changed=false",
           c2.changed is False and c2.added == 0 and c2.removed == 0,
           f"changed={c2.changed} added={c2.added} removed={c2.removed}")

    # 6. 会变的页
    await s.web_watch(op="add", name="chg", url=f"{BASE}/changing")
    await s.web_watch(op="check", name="chg", allow_private=True)      # 基线
    c3 = await s.web_watch(op="check", name="chg", allow_private=True)  # 必变
    record("会变的页：changed=true 且 diff 有行",
           c3.changed is True and c3.added > 0 and bool(c3.diff),
           f"changed={c3.changed} added={c3.added} removed={c3.removed} "
           f"diff_lines={len(c3.diff)}")

    # 7. 抓取失败不动基线
    await s.web_watch(op="add", name="dead", url=f"{DEAD}/")
    before = tasks.get_watch("dead")
    d1 = await s.web_watch(op="check", name="dead", allow_private=True)
    after = tasks.get_watch("dead")
    record("抓取失败不动基线（网络故障 ≠ 页面清空）",
           bool(d1.error) and after["baseline"] == before["baseline"] == "",
           f"error={d1.error[:60]!r} baseline={after['baseline']!r} "
           f"changed={d1.changed!r}")

    # 收尾
    for n in ("art", "chg", "dead"):
        await s.web_watch(op="remove", name=n)

    bad_names = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad_names)}/{len(results)} 项通过")
    return 1 if bad_names else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
