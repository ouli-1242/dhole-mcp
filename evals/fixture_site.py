"""本地模拟测试靶站（计划第十章 eval 基线的底座）。

零外部依赖：只用 stdlib http.server + cv2（cv2 仅 /qr.png 需要，缺了也能起）。
**不打任何真实站点**——200 次真实请求会触发反爬、可能封 IP，且改版即失效。

    python evals/fixture_site.py            # 127.0.0.1:8399
    python evals/fixture_site.py --port 9000

端点
----
/                    索引页（crawl 发现的入口）
/login               GET 登录表单；POST 发 sid cookie -> 302 /account
/account             验 sid：已登录给"欢迎回来"+退出控件；未登录给登录壳
/checkout            GET 订单表单；POST **写订单日志文件**（不可逆步的服务端凭据）
/qr                  HTML 页，内嵌 <img src="/qr.png">
/qr.png              cv2 QRCodeEncoder 生成的真二维码 PNG
/robots.txt          Disallow: /private
/private/secret      robots 合规靶（应被合规模式拦下）
/feed.xml            3 条带 pubDate 的 RSS
/article             多段落长文（focus / focus_mode=best 靶）

服务端每次请求打印一行到 stdout，作为独立于 dhole 响应的旁证。
订单日志默认 evals/.runtime/orders.log —— 场景 1 用它判断"提交是否真的发生了"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
ORDERS = RUNTIME / "orders.log"
REQUESTS = RUNTIME / "requests.log"

QR_PAYLOAD = "https://example.com/checkin?token=DHOLE-EVAL-8399"

SID_COOKIE = "sid"
SID_VALUE = "sess-8399-ok"

# /article 的靶：一段带唯一事实的正文，供 focus_mode=best 定位。
ARTICLE_FACT = "缓存默认保留 3600 秒，改 cache_ttl 可覆盖。"


# ── 页面 ────────────────────────────────────────────────────────────────

def _page(title: str, body: str) -> bytes:
    return (
        "<!doctype html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head><body>{body}</body></html>"
    ).encode("utf-8")


INDEX = _page("靶站索引", """
<h1>dhole 模拟测试靶站</h1>
<ul>
  <li><a href="/login">登录</a></li>
  <li><a href="/account">账户</a></li>
  <li><a href="/checkout">结算</a></li>
  <li><a href="/qr">二维码</a></li>
  <li><a href="/article">长文</a></li>
  <li><a href="/feed.xml">订阅源</a></li>
</ul>
""")

LOGIN_PAGE = _page("登录", """
<h1>请登录</h1>
<form method="post" action="/login">
  <label for="username">用户名</label>
  <input id="username" name="username" type="text" placeholder="用户名">
  <label for="password">密码</label>
  <input id="password" name="password" type="password" placeholder="密码">
  <button type="submit" id="login-btn">登录</button>
</form>
""")

ACCOUNT_LOGGED_IN = _page("账户", """
<h1>欢迎回来</h1>
<p>这是仅登录用户可见的内容。</p>
<button id="logout-btn">退出登录</button>
""")

ACCOUNT_SHELL = _page("账户", """
<h1>需要登录</h1>
<p>你没有登录。</p>
<form method="post" action="/login">
  <input id="username" name="username" type="text">
  <input id="password" name="password" type="password">
  <button type="submit" id="login-btn">登录</button>
</form>
""")

CHECKOUT_PAGE = _page("结算", """
<h1>确认订单</h1>
<form method="post" action="/checkout">
  <label for="item">商品</label>
  <input id="item" name="item" type="text" value="机械键盘">
  <label for="qty">数量</label>
  <input id="qty" name="qty" type="text" value="1">
  <button type="submit" id="place-order">提交订单</button>
</form>
""")

QR_PAGE = _page("二维码", """
<h1>签到二维码</h1>
<img id="qr" src="/qr.png" alt="qr" width="240" height="240">
""")

QR_DONE = _page("订单已提交", """
<h1>订单已提交</h1>
<p>服务端已写入订单日志。</p>
""")


def _article() -> bytes:
    paras = [
        "本地抓取工具在默认配置下会把结果写进一个本地缓存库，避免重复请求同一页面。",
        "缓存条目带有过期时间，过期后再次抓取会重新发起请求并覆盖旧条目。",
        "抓取器分成两层：一层是快速 HTTP 层，一层是能执行脚本的反检测浏览器层。",
        "自动升级只在 HTTP 层判定失败时发生，判定依据是状态码、正文长度与反爬特征串。",
        "反爬特征串包括常见的人机校验页面标题，命中后不会重试而是直接升级。",
        "缓存键包含目标地址、会话标识与凭据指纹，因此登录态内容不会与公开内容混放。",
        f"关于缓存保留时长：{ARTICLE_FACT}",
        "抓取结果的正文会经过抽取器转成 Markdown，超长时按字符数截断并给出续读偏移。",
        "截断点始终落在字符边界上，续读时把偏移量传回来即可拿到剩余部分。",
        "所有网络请求都受 robots 规则约束，除非显式声明调用方已获得站点授权。",
    ]
    body = "<h1>本地抓取工具配置指南</h1>\n" + "\n".join(
        f"<p>{p}</p>" for p in paras
    )
    return _page("本地抓取工具配置指南", body)


def _feed() -> bytes:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    items = []
    for i in range(3):
        when = (now - timedelta(days=i)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        items.append(
            "<item>"
            f"<title>靶站条目 {i + 1}</title>"
            f"<link>http://127.0.0.1:8399/article#item{i + 1}</link>"
            f"<guid>fixture-item-{i + 1}</guid>"
            f"<pubDate>{when}</pubDate>"
            f"<description>第 {i + 1} 条摘要，用于 since 增量测试。</description>"
            "</item>"
        )
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<rss version="2.0"><channel>'
        "<title>dhole 靶站订阅源</title>"
        "<link>http://127.0.0.1:8399/</link>"
        "<description>fixture feed</description>"
        + "".join(items)
        + "</channel></rss>"
    )
    return xml.encode("utf-8")


_QR_CACHE: bytes | None = None


def _qr_png() -> bytes:
    """生成二维码 PNG，并在返回前本地解码回读自校验。

    ``enc.encode()`` 返回的已经是 0/255 的 uint8。**不要再乘 255**——uint8 会
    溢出（255*255 % 256 == 1），白模块变近黑，二维码静默解不出。
    """
    global _QR_CACHE
    if _QR_CACHE is not None:
        return _QR_CACHE

    import cv2  # 延迟导入：只有这个端点需要
    import numpy as np

    enc = cv2.QRCodeEncoder_create()
    code = enc.encode(QR_PAYLOAD)
    if code.max() <= 1:  # 有的版本给 0/1，统一到 0/255
        code = code.astype(np.uint8) * 255
    # 放大 + 补静区：解码器需要足够的模块尺寸与四周留白。
    scale, quiet = 8, 4
    big = np.kron(code, np.ones((scale, scale), dtype=np.uint8))
    big = np.pad(big, quiet * scale, constant_values=255)
    ok, buf = cv2.imencode(".png", big)
    if not ok:
        raise RuntimeError("cv2.imencode failed")
    data = buf.tobytes()

    # 自校验：解不出来就直接报错，别让坏图静默发出去。
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE))
    if decoded != QR_PAYLOAD:
        raise RuntimeError(
            f"QR self-check failed: decoded {decoded!r} != payload {QR_PAYLOAD!r}")
    _QR_CACHE = data
    return data


# ── 处理器 ──────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "dhole-fixture/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        line = (f"[{datetime.now().strftime('%H:%M:%S')}] {self.command} "
                f"{self.path} -> {args[1] if len(args) > 1 else ''}")
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        # 同时落盘：场景判定需要"浏览器到底请求了什么"这个独立于 dhole 响应的
        # 旁证（例：图片子资源是否真的发出过）。
        try:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            with REQUESTS.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()}\t"
                        f"{self.command}\t{self.path}\n")
        except Exception:
            pass

    # ── 工具 ──
    def _send(self, code: int, body: bytes, ctype: str,
              extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _html(self, body: bytes, code: int = 200) -> None:
        self._send(code, body, "text/html; charset=utf-8")

    def _has_sid(self) -> bool:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == SID_COOKIE and value == SID_VALUE:
                return True
        return False

    # ── 路由 ──
    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/":
            return self._html(INDEX)
        if path == "/login":
            return self._html(LOGIN_PAGE)
        if path == "/account":
            return self._html(ACCOUNT_LOGGED_IN if self._has_sid() else ACCOUNT_SHELL)
        if path == "/checkout":
            return self._html(CHECKOUT_PAGE)
        if path == "/qr":
            return self._html(QR_PAGE)
        if path == "/qr.png":
            try:
                return self._send(200, _qr_png(), "image/png")
            except Exception as e:  # cv2 缺失时给可读错误，别让服务静默 500
                return self._html(
                    f"<h1>QR 生成失败</h1><pre>{type(e).__name__}: {e}</pre>".encode(),
                    500)
        if path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /private\n"
            return self._send(200, body, "text/plain; charset=utf-8")
        if path == "/private/secret":
            return self._html(_page("私密", "<h1>私密内容</h1><p>robots 靶。</p>"))
        if path == "/feed.xml":
            return self._send(200, _feed(), "application/rss+xml; charset=utf-8")
        if path == "/article":
            return self._html(_article())
        return self._html(_page("404", "<h1>404</h1>"), 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        form = {k: v[0] for k, v in parse_qs(raw).items()}

        if path == "/login":
            # 任何非空凭据都算成功：这里测的是编排与 cookie 桥，不是口令校验。
            if not form.get("username") or not form.get("password"):
                return self._html(_page("登录失败", "<h1>凭据为空</h1>"), 400)
            return self._send(
                302, b"", "text/plain",
                extra={"Location": "/account",
                       "Set-Cookie": f"{SID_COOKIE}={SID_VALUE}; Path=/; HttpOnly"},
            )

        if path == "/checkout":
            RUNTIME.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "item": form.get("item", ""),
                "qty": form.get("qty", ""),
                "sid": SID_VALUE if self._has_sid() else "",
                "remote": self.client_address[0],
            }
            with ORDERS.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            sys.stdout.write(f"    !! ORDER WRITTEN: {record}\n")
            sys.stdout.flush()
            return self._html(QR_DONE)

        return self._html(_page("404", "<h1>404</h1>"), 404)


def main() -> int:
    ap = argparse.ArgumentParser(description="dhole 模拟测试靶站")
    ap.add_argument("--port", type=int, default=8399)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    RUNTIME.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    srv.daemon_threads = True
    base = f"http://{args.host}:{args.port}"
    print(f"fixture 靶站已启动: {base}", flush=True)
    print(f"订单日志: {ORDERS}", flush=True)
    print(f"二维码载荷: {QR_PAYLOAD}", flush=True)
    print("Ctrl-C 停止", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n停止中…", flush=True)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
