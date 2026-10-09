"""修复复验：直接驱动真实代码路径，确认修好在线上行为上确实发生了。

与 `evals/check_image_blocking.py` 的分工：那个证明**缺陷的因果**（A/B 单变量），
这个证明**修复的结果**（走完整 web_read：真浏览器真动作链真截图真解码，或真字节
真抽取真判定）。

每一条都断言**线路上的响应模型字段**（不是内部函数返回值），detail 里带上可观察的证据；
新落的两条还带上修复前的实测值 —— 没有前值就分不清"修好了"和"本来就这样"。需要靶站
先起来：`python evals/fixture_site.py`。

    python evals/verify_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
BASE = "http://127.0.0.1:8399"

# 靶站在回环上，而 web_read 的私有主机门只有 cred_defaults 一个入口：`allow_private`
# 在 17.0 从参数面退役，调用内作用域又会被每次调用重推的存值覆盖（那是 D1 记着的形状
# 问题，Round 3 才修）。既然要写存值，就写进一次性 DHOLE_HOME（照 ranking_baseline 的
# 做法）—— 复验脚本不该碰用户机器上的凭据库，更不该跑完留下一个撑开的栅栏。
os.environ["DHOLE_HOME"] = tempfile.mkdtemp(prefix="dhole_verifyfixes_")
# 截图动作在验证阶段就被多模态门控拦下（actions.py），复验要显式开旗。
os.environ.setdefault("DHOLE_MULTIMODAL", "1")

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


async def check_screenshot_carries_pixels() -> None:
    """D1：动作链里的元素截图要拿到真像素，decode_qr 要解出载荷。"""
    server = MasterFetchServer()
    # 这一支曾把 `allow_private=True` 当参数传 —— 那颗参数早已不在 web_read 的参数面上，
    # 于是 TypeError 被 except 记成「未通过」：脚本坏掉的样子和它要证的缺陷一模一样
    # （说的和做的对不上，且没有任何一处报错）。门现在由 main() 一次性开在 DHOLE_HOME 里。
    res = await server.web_read(
        url=f"{BASE}/qr",
        actions=[{"screenshot": {"selector": "#qr", "decode_qr": True}}],
    )
    rows = (res.metadata or {}).get("actions") or []
    shot = next((r for r in rows if r.get("action", "").startswith("screenshot")), {})
    qr = shot.get("qr") or []
    images = getattr(res, "_action_images", None) or []
    ok = (qr == ["https://example.com/checkin?token=DHOLE-EVAL-8399"]
          and bool(images))
    record("D1 元素截图拿到像素并解出二维码", ok,
           f"qr={qr!r} 内联图片={len(images)} 张 action={shot.get('action')!r}")


async def check_submit_label() -> None:
    """D2：索引寻址的 submit 要在响应与审计里写明目标。"""
    log = RUNTIME / "audit_verify.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()
    os.environ["DHOLE_USAGE_LOG"] = str(log)

    server = MasterFetchServer()
    res = await server.web_read(
        url=f"{BASE}/checkout",
        actions=[{"submit": {"index": 2}}],
    )
    rows = (res.metadata or {}).get("actions") or []
    label = rows[0].get("action", "") if rows else ""
    pending = bool(rows and rows[0].get("pending_approval"))

    writes = []
    if log.exists():
        writes = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
    audit_action = writes[0].get("action", "") if writes else ""

    ok = ("[2]" in label and "None" not in label
          and pending and writes and "[2]" in audit_action)
    record("D2 索引寻址 submit 的标签与审计写明目标", ok,
           f"响应标签={label!r} pending={pending} 审计 action={audit_action!r}")
    os.environ.pop("DHOLE_USAGE_LOG", None)


async def check_obfuscated_shell_is_not_a_success() -> None:
    """R1：正文穿过**每一道**长度闸的混淆壳页，结论位要跟着形状翻。

    前值（2026-10-09 北极星那家实测）：`status: 200`、`content_ok: true`、抽取 3,559
    个字符 —— 字全抽到了，而那些字里没有一句人话。`_is_js_shell` 量的是"大 body +
    几乎没字"，这一枚恰恰是从每一道长度闸底下钻过去的。靶站的 `/shell/obfuscated`
    就是照着那一发造的（确定性、无状态）。
    """
    server = MasterFetchServer()
    res = await server.web_read(url=f"{BASE}/shell/obfuscated",
                                force_fetcher="http")
    finding = (res.metadata or {}).get("obfuscated_body") or {}
    ok = (res.status == 200
          and res.content_ok is False
          and res.error.startswith("obfuscated_payload")
          and res.page_type == "js_shell"
          and (res.diagnosis or {}).get("code") == "js_shell"
          and res.total_extracted_chars > 2_000
          and bool(finding.get("signals")))
    record("R1 混淆壳页不再被报成成功", ok,
           f"status={res.status} 抽取={res.total_extracted_chars} 字 "
           f"content_ok={res.content_ok} page_type={res.page_type!r} "
           f"code={(res.diagnosis or {}).get('code')!r} 证据={finding.get('signals')} "
           f"error={res.error[:70]!r}（前值：content_ok=true、批量把它计入 successful）")


async def check_legit_pages_survive_the_flip() -> None:
    """R1 的反向：翻闸之后，靶站每一页合法页面逐份还是成功页。

    measure 那一刀的假阳性清单是离线语料量的；这一条走的是真 HTTP、真抽取、真结论位，
    它红了就说明闸开过头 —— 那正是这批唯一还没被 pytest 覆盖到的可能性（本地挂具能造
    出成功页的形状，造不出真站点的字节）。
    """
    server = MasterFetchServer()
    judged_shell = []
    for path in ("/", "/about", "/article", "/collections", "/work/1"):
        res = await server.web_read(url=f"{BASE}{path}", force_fetcher="http")
        if "obfuscated_payload" in (res.error or ""):
            judged_shell.append((path, res.total_extracted_chars))
    record("R1 合法页面没有被翻闸误伤", not judged_shell,
           f"被判成壳的合法页面={judged_shell}（前值：无）")


async def main() -> int:
    print(f"靶站 {BASE}\n")
    # 门开在本脚本自己的 DHOLE_HOME 里（见文件头），不落用户的机器。
    identity.set_value("allow_private", True)
    try:
        await check_screenshot_carries_pixels()
    except Exception as e:
        record("D1 元素截图拿到像素并解出二维码", False,
               f"{type(e).__name__}: {e}")
    try:
        await check_submit_label()
    except Exception as e:
        record("D2 索引寻址 submit 的标签与审计写明目标", False,
               f"{type(e).__name__}: {e}")
    for fn, name in ((check_obfuscated_shell_is_not_a_success,
                      "R1 混淆壳页不再被报成成功"),
                     (check_legit_pages_survive_the_flip,
                      "R1 合法页面没有被翻闸误伤")):
        try:
            await fn()
        except Exception as e:
            record(name, False, f"{type(e).__name__}: {e}")

    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 项通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
