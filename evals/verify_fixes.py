"""修复复验：直接驱动真实代码路径，确认 D1/D2 在线上行为上确实修好。

与 `evals/check_image_blocking.py` 的分工：那个证明**缺陷的因果**（A/B 单变量），
这个证明**修复的结果**（走完整 smart_fetch：真浏览器、真动作链、真截图、真解码）。

    python evals/verify_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
BASE = "http://127.0.0.1:8399"

from dhole_mcp.server import MasterFetchServer  # noqa: E402

# 截图动作在验证阶段就被多模态门控拦下（actions.py），复验要显式开旗。
os.environ.setdefault("DHOLE_MULTIMODAL", "1")

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


async def check_screenshot_carries_pixels() -> None:
    """D1：动作链里的元素截图要拿到真像素，decode_qr 要解出载荷。"""
    server = MasterFetchServer()
    res = await server.smart_fetch(
        url=f"{BASE}/qr",
        actions=[{"screenshot": {"selector": "#qr", "decode_qr": True}}],
        allow_private=True,
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
    res = await server.smart_fetch(
        url=f"{BASE}/checkout",
        actions=[{"submit": {"index": 2}}],
        allow_private=True,
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


async def main() -> int:
    print(f"靶站 {BASE}\n")
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

    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 项通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
