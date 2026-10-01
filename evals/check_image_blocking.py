"""A/B 实验：``disable_resources`` 是否真的拦掉了页面图片子资源。

背景
----
``smart_fetch`` 的 stealthy 路径（server.py:7091，即带 ``actions`` 的那条）硬编码
``disable_resources=True``，而 ``DISABLED_RESOURCE_TYPES`` 含 ``"image"``。
现象：``{"screenshot": {"selector": "#qr", "decode_qr": true}}`` 在动作链里截到的是
破图占位符，``qr: []``。

本脚本把因果坐实，而不是停在旁证：**同一个页面、同一个浏览器层，只翻转
``disable_resources``**，用靶站的请求落盘（``.runtime/requests.log``）判定浏览器
到底有没有发出 ``GET /qr.png``。

    python evals/check_image_blocking.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
REQUESTS = RUNTIME / "requests.log"
TARGET = "http://127.0.0.1:8399/qr"

from dhole_mcp.browser import StealthyBrowser  # noqa: E402


def _png_hits() -> int:
    if not REQUESTS.is_file():
        return 0
    return sum(1 for ln in REQUESTS.read_text(encoding="utf-8").splitlines()
               if "\tGET\t/qr.png" in ln)


async def _probe(disable: bool) -> tuple[int, int, bool]:
    """跑一次抓取，返回 (抓取前 png 命中数, 抓取后, 是否报告成功)。"""
    before = _png_hits()
    async with StealthyBrowser() as browser:
        res = await browser.fetch(TARGET, disable_resources=disable,
                                  timeout=30_000)
    ok = bool(getattr(res, "ok", True)) and not getattr(res, "error", "")
    return before, _png_hits(), ok


async def main() -> int:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    print(f"靶站: {TARGET}\n请求日志: {REQUESTS}\n")

    rows = []
    for disable in (True, False):
        before, after, ok = await _probe(disable)
        got = after - before
        rows.append((disable, got, ok))
        print(f"disable_resources={str(disable):5} → "
              f"浏览器发出 GET /qr.png: {'是' if got else '否'} "
              f"({got} 次)，抓取 ok={ok}")

    print()
    blocked = rows[0][1] == 0
    loads = rows[1][1] > 0
    if blocked and loads:
        print("结论：因果成立——disable_resources=True 时图片子资源被路由层 abort，")
        print("      False 时正常发出。动作链里的元素截图因此拿不到图片像素。")
        return 0
    print("结论：因果不成立或另有原因（见上面的计数），需要继续排查。")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
