"""R4 真实下载实测：真起一个 HTTP 服务、真写盘、真核对 sha256。

不依赖网络（除回环），所以它可以随时重跑；但那不代表它只测了 mock —— 走的是
web_read 的完整链路：validate_url → 守卫 → HTTP 层 → `_translate_response_body`
的第一段分叉 → `_write_download`。

用法：python evals/r4_live_download_check.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 靶站在回环上，而回环默认被 SSRF 守卫拒 —— 这条实测必须**显式**开门，与真实
# 用户读本机 dev 服务走的是同一颗开关（不是测试专用后门）。
os.environ.setdefault("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

from dhole_mcp.server import MasterFetchServer  # noqa: E402

BIG_MB = 60          # > 50MB 抽取红线，< 256MB 落盘默认红线
BIG_BYTES = BIG_MB * 1024 * 1024


def _blob(n: int) -> bytes:
    """可复算的伪随机字节（不花 60MB 随机数的时间）。"""
    seed = hashlib.sha256(b"r4").digest()
    out = bytearray()
    chunk = seed
    while len(out) < n:
        chunk = hashlib.sha256(chunk).digest()
        out.extend(chunk * 16)
    return bytes(out[:n])


BIG = _blob(BIG_BYTES)
SMALL_TEXT = ("<html><body><h1>Receipt check</h1>"
              "<p>This page exists so a save_to call cannot poison the cache "
              "for the plain read that follows it.</p></body></html>").encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 安静
        pass

    def do_HEAD(self):
        self._send(head_only=True)

    def do_GET(self):
        self._send(head_only=False)

    def _send(self, head_only: bool):
        if self.path.startswith("/big.pdf"):
            body, ctype = BIG, "application/pdf"
        elif self.path.startswith("/big.bin"):
            body, ctype = BIG, "application/octet-stream"
        else:
            body, ctype = SMALL_TEXT, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)


def start_server() -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS " if ok else "  FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILS.append(name)


async def main() -> int:
    srv, port = start_server()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp") / "dhole-r4"
    # 每次跑之前清空自己那一格：上一轮的文件留着，第 1 项就会撞上"拒绝覆盖"——
    # 那条规则本身是对的，撞上它只是说明这个脚本没打扫自己的房间。
    if tmp.exists():
        for p in sorted(tmp.rglob("*"), reverse=True):
            p.unlink() if p.is_file() else p.rmdir()
    tmp.mkdir(parents=True, exist_ok=True)
    s = MasterFetchServer()

    print(f"\n[1] {BIG_MB}MB 的 .pdf 落盘（超过 50MB 抽取红线，不该被它挡住）")
    t0 = time.time()
    r = await s.web_read(url=f"{base}/big.pdf", save_to=str(tmp / "big.pdf"),
                        cache_ttl=0)
    dt = time.time() - t0
    got = tmp / "big.pdf"
    print(f"      content={r.content} err={r.error!r} {dt:.1f}s")
    check("回执没有 error", not r.error, str(r.error))
    check("文件真的在", got.exists(), str(got))
    if got.exists():
        data = got.read_bytes()
        check("字节数一致", len(data) == BIG_BYTES, f"{len(data):,}")
        check("sha256 一致", hashlib.sha256(data).hexdigest()
              == hashlib.sha256(BIG).hexdigest())
        check("回执的 sha256 是同一个", (r.metadata or {}).get("sha256")
              == hashlib.sha256(BIG).hexdigest())
        check("回执点名落点", (r.metadata or {}).get("saved_to") == str(got))
        check("回执带 content-type", "pdf" in (r.content_type or "").lower(),
              repr(r.content_type))
        check("没有 .part 残留", not Path(str(got) + ".part").exists())

    print("\n[2] 同一个路径再写一次 —— 拒绝覆盖，且文件没被动过")
    before = got.read_bytes()[:64] if got.exists() else b""
    r = await s.web_read(url=f"{base}/big.pdf", save_to=str(tmp / "big.pdf"),
                        cache_ttl=0)
    check("有 error", bool(r.error), str(r.error))
    check("话里说到 already exists", "already exists" in (r.error or ""))
    check("文件字节没变", (got.read_bytes()[:64] if got.exists() else b"") == before)

    print("\n[3] 父目录不存在 —— 发请求之前就拒")
    r = await s.web_read(url=f"{base}/big.pdf",
                        save_to=str(tmp / "nope" / "x.bin"), cache_ttl=0)
    check("有 error", bool(r.error), str(r.error))
    check("话里说到 parent", "parent" in (r.error or ""))
    check("什么都没写", not (tmp / "nope").exists())

    print("\n[4] save_to 指一个目录 —— 拒")
    r = await s.web_read(url=f"{base}/big.bin", save_to=str(tmp), cache_ttl=0)
    check("有 error", bool(r.error), str(r.error))
    check("话里说到 directory", "directory" in (r.error or ""))

    print("\n[5] 批量读 + save_to —— 互斥")
    r = await s.web_read(url=None, urls=[f"{base}/a", f"{base}/b"],
                        save_to=str(tmp / "batch.bin"), cache_ttl=0)
    check("有 error", bool(r.error), str(r.error))
    check("话里说到 batch", "batch" in (r.error or ""))

    print("\n[6] max_save_bytes 压到 1MB —— 拒，且不写半个文件")
    r = await s.web_read(url=f"{base}/big.bin", save_to=str(tmp / "capped.bin"),
                        max_save_bytes=1024 * 1024, cache_ttl=0)
    check("有 error", bool(r.error), (r.error or "")[:80])
    check("话里说到 save_to limit", "save_to limit" in (r.error or ""))
    check("没写文件", not (tmp / "capped.bin").exists())

    print("\n[7] max_save_bytes 单独给 —— 拒（它只管一次下载）")
    r = await s.web_read(url=f"{base}/a", max_save_bytes=1024, cache_ttl=0)
    check("有 error", bool(r.error), str(r.error))
    check("话里说到 without save_to", "without save_to" in (r.error or ""))

    print("\n[8] 落盘不吃缓存、也不写缓存：随后的普通读拿到的是正文")
    r1 = await s.web_read(url=f"{base}/page", save_to=str(tmp / "page.html"))
    check("落盘成功", not r1.error, str(r1.error))
    r2 = await s.web_read(url=f"{base}/page")
    text = "".join(c for c in r2.content if isinstance(c, str))
    check("普通读拿到正文而不是回执", "Receipt check" in text, text[:60])
    check("普通读没有 saved_to", "saved_to" not in (r2.metadata or {}))

    print("\n[9] 二进制（octet-stream）也照样落盘")
    r = await s.web_read(url=f"{base}/big.bin", save_to=str(tmp / "big.bin"),
                        cache_ttl=0)
    check("回执没有 error", not r.error, str(r.error))
    check("字节数一致", (tmp / "big.bin").stat().st_size == BIG_BYTES
          if (tmp / "big.bin").exists() else False)

    srv.shutdown()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
