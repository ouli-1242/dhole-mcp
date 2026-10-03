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
/feed.xml            6 条带 pubDate（0/1/2/3/5/10 天前）+ 2 条无 pubDate 的 RSS
/article             多段落长文（focus / focus_mode=best 靶）
/changing            每次请求正文都不同的页（watch / diff 靶）
/collections         列表页：?items=N 张 .card，每张肚子里挂一个 /work/N 的链接（E-13 扇出靶）
/work/<n>            详情页：一列每页不同、一列每页同值（页眉）、一列永不命中
/shell               几百字符、组不成任何一组链接的页（壳页 → force_fetcher 那一支）
/about               孤链接的去处（候选选择器不该把只有一条链接的组报出来）
/captcha             HTML 页，内嵌 <img src="/captcha.png?token=..."> + 提交表单
/captcha.png         扭曲文字验证码 PNG（逐字旋转 + 正弦位移 + 干扰线；?level=hard 加码）
/captcha/verify      POST token+answer；对错分别回不同页，每次尝试落盘

服务端每次请求打印一行到 stdout，作为独立于 dhole 响应的旁证。
订单日志默认 evals/.runtime/orders.log —— 场景 1 用它判断"提交是否真的发生了"。
验证码尝试默认 evals/.runtime/captcha.log —— 视觉基线用它数"试了几次、最后有没有通"。

验证码的答案由 token **确定性派生**（`captcha_text`），所以靶站无状态、重启后
同一个 token 的答案不变——评测脚本据此独立算期望值，不需要靶站开一个"告诉我
答案"的后门。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
ORDERS = RUNTIME / "orders.log"
REQUESTS = RUNTIME / "requests.log"
CAPTCHA_LOG = RUNTIME / "captcha.log"

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
  <li><a href="/captcha">验证码</a></li>
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


# /changing 的请求计数：watch 需要一个**真会变**的页，否则 diff 那一半只能靠
# 单测覆盖。每次请求把计数写进正文，两次抓取之间必定有新增行。
_CHANGING_HITS = {"n": 0}


def _changing() -> bytes:
    _CHANGING_HITS["n"] += 1
    n = _CHANGING_HITS["n"]
    body = (
        "<h1>会变的页面</h1>"
        f"<p>第 {n} 次请求。</p>"
        "<p>这一段每次都在，用来给 diff 提供上下文。</p>"
        + "".join(f"<p>仅在本次出现的行 {n}-{i}</p>" for i in range(n))
    )
    return _page("会变的页面", body)


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


# ── 扇出靶（E-13：列表页 → 每项详情页，一次调用）──────────────────────
#
# 这一族以前只有内联 HTML 的桩测（`tests/test_nested_fanout_e13.py`）：桩递出去的
# 是测试自己写进字典的字符串，于是「选择器与页面之间的契约」从来没有过真链路的证人
# —— 过 content-type、过解码、过 robots、过本地缓存之后还成立吗？`nested_no_match`
# 那句「没发第二层请求」也一直是工具自述，没有独立旁证。下面三张页各还一截。

COLLECTIONS_ITEMS = 10          # 默认 10 条 > 默认 nested_max 8：不抬参数也截得到
COLLECTIONS_MAX = 200


def _collections_page(items: int = COLLECTIONS_ITEMS) -> bytes:
    """成组的列表页：每张 `.card` **肚子里**挂一个 `/work/N` 的 `<a>`。

    三种「少给了」的形状刻意摆在同一页上，各按各的原因少：

      - 一张没有 href 的卡片（`.card` 在那个站上只是样式）→ 计入 skipped
      - 第一项的第二条链接，只差一个 `#top` → 去重，**不是**截断
      - `?items=` 超过 `nested_max` → 那才是 `truncation=nested_budget`

    把去重报成截断，调用方就会为那 2 条再抬一次上限而永远拿不回来，所以这三个数
    必须在一次调用里各说各的。末尾那条 `.menu` 孤链接不构成一组，候选里不该有它。
    """
    cards = "".join(
        f'<div class="card"><a href="/work/{i}">专题 {i}</a>'
        f'<span class="count">{i * 3} 件</span></div>'
        for i in range(1, items + 1))
    body = (
        "<h1>推荐专题</h1>"
        + cards
        + '<div class="card"><a href="/work/1#top">专题 1（重复）</a></div>'
        + '<div class="card"><span>没有链接的一张</span></div>'
        + '<div class="menu"><a href="/about">关于</a></div>'
    )
    return _page("推荐专题", body)


_WORK_TAGS = (("静物", "黑白"), ("街景", "夜景", "人像"), ("胶片", "扫描"))


def _work_page(n: int) -> bytes:
    """详情页：一列每页都不同（真值）、一列每页同值（页眉）、一列永不命中。

    `.site-footer` 是故意摆的页眉：仓库自己的示例就被这种列骗过 —— 8 条作者页抽出
    8 个一模一样的段落，`ok 8 / empty 0`，响应读起来完全成功。`.rating` 这台靶站上
    根本没有，用来逼出「别的列都填上了，只有这颗选择器什么都不指」那一句。
    """
    tags = "".join(f'<span class="tag">{t}</span>'
                   for t in _WORK_TAGS[n % len(_WORK_TAGS)])
    body = (
        f'<h1 class="title">作品 {n}</h1>'
        f'<div class="tags">{tags}</div>'
        f'<p class="summary">第 {n} 篇的独有正文，用来证明这一列真的每页不同。</p>'
        '<footer class="site-footer">© 2026 dhole 靶站</footer>'
    )
    return _page(f"作品 {n}", body)


SHELL_PAGE = _page("加载中", """
<div id="root"></div>
<script>
  // 列表由脚本渲染：这份 HTML 里没有那 10 个容器，换任何选择器都还是 0 命中。
  window.__fixture = {"items": []};
</script>
<a href="/about">关于</a>
""")

ABOUT_PAGE = _page("关于", "<h1>关于</h1><p>靶站的孤链接去处。</p>")


def _feed() -> bytes:
    """6 条带 pubDate（0/1/2/3/5/10 天前）+ 2 条无 pubDate。

    无日期条目是刻意的：feed_read 的 since 语义是"无日期的条目**保留**并计数"
    （一个没有日期的 feed 无法诚实地过滤），不摆两条在靶站上就测不到那一半。
    """
    now = datetime.now(timezone.utc).replace(microsecond=0)
    items = []
    for i, days in enumerate((0, 1, 2, 3, 5, 10)):
        when = (now - timedelta(days=days)).strftime("%a, %d %b %Y %H:%M:%S +0000")
        items.append(
            "<item>"
            f"<title>靶站条目 {i + 1}（{days} 天前）</title>"
            f"<link>http://127.0.0.1:8399/article#item{i + 1}</link>"
            f"<guid>fixture-item-{i + 1}</guid>"
            f"<pubDate>{when}</pubDate>"
            f"<description>第 {i + 1} 条摘要，用于 since 增量测试。</description>"
            "</item>"
        )
    for i in (1, 2):
        items.append(
            "<item>"
            f"<title>靶站无日期条目 {i}</title>"
            f"<link>http://127.0.0.1:8399/article#undated{i}</link>"
            f"<guid>fixture-undated-{i}</guid>"
            "<description>没有 pubDate，since 过滤不该把它丢掉。</description>"
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


# ── 扭曲文字验证码 ──────────────────────────────────────────────────────
#
# 视觉辅助基线的靶（计划第十章：图形验证码试读 ≤N 次通过率与转交率）。
# 答案由 token 确定性派生 —— 靶站无状态，重启后同一个 token 的答案不变，
# 评测脚本据此独立算期望值，靶站不必开"告诉我答案"的后门。

# 去掉易混字符（0/O、1/I/L），否则测出来的是"看不清"而不是"认不出"。
CAPTCHA_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CAPTCHA_LEN = 5


def new_captcha_token() -> str:
    return secrets.token_hex(6)


def captcha_text(token: str) -> str:
    """token -> 答案。sha256 取前 N 字节映射到字母表，确定性、无状态。"""
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return "".join(CAPTCHA_ALPHABET[b % len(CAPTCHA_ALPHABET)]
                   for b in digest[:CAPTCHA_LEN])


CAPTCHA_LEVELS = ("normal", "hard")


def _captcha_png(token: str, level: str = "normal") -> bytes:
    """渲染扭曲文字 PNG。

    三样失真叠在一起，模拟真实站点：逐字随机旋转 + 整幅正弦波位移 +
    干扰线/噪点。**不做抗锯齿以外的美化** —— 这一页是拿来量"读图难不难"的，
    太干净会让基线虚高。

    ``level`` 只改失真强度，**不改答案**（答案永远只由 token 决定，见
    `captcha_text`）。`normal` 用于量"能读出来多少"；`hard` 把旋转/位移/
    干扰加到人眼也常读错的程度，用来逼出"≤N 次不通就转交"那条路径——
    没有真读不出的样张，转交纪律就永远测不到。
    """
    import cv2
    import numpy as np

    if level not in CAPTCHA_LEVELS:
        level = "normal"
    hard = level == "hard"

    text = captcha_text(token)
    rng = np.random.default_rng(zlib.crc32(token.encode("utf-8")))

    tile, step = 64, 40
    W = 12 + (len(text) - 1) * step + tile + 12
    H = 80
    bg = 245
    canvas = np.full((H, W), bg, np.uint8)

    x = 12
    for ch in text:
        # 每个字符画在自己的方片上、单独旋转，再按最小像素叠上去 ——
        # 直接对整幅旋转只会得到一条斜带，字间距不变，不够"扭曲"。
        buf = np.full((tile, tile), bg, np.uint8)
        scale = 1.7 if not hard else float(rng.uniform(1.4, 1.9))
        cv2.putText(buf, ch, (14, 48), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, 25, 3, cv2.LINE_AA)
        ang = float(rng.uniform(-30, 30) if not hard else rng.uniform(-55, 55))
        m = cv2.getRotationMatrix2D((tile / 2, tile / 2), ang, 1.0)
        buf = cv2.warpAffine(buf, m, (tile, tile), borderValue=bg)
        y = int(rng.uniform(4, 14) if not hard else rng.uniform(0, 18))
        # 切片显式对齐形状，免得以后改步长时踩到 (64,54) vs (64,64) 的广播错。
        h = min(tile, H - y)
        w = min(tile, W - x)
        seg = canvas[y:y + h, x:x + w]
        canvas[y:y + h, x:x + w] = np.minimum(seg, buf[:h, :w])
        x += step

    # 整幅正弦位移：y' = y + A*sin(2πx/λ)
    xs = np.arange(W, dtype=np.float32)
    ys = np.arange(H, dtype=np.float32)
    mapx, mapy = np.meshgrid(xs, ys)
    amp = float(rng.uniform(3.5, 6.5) if not hard else rng.uniform(5.0, 8.0))
    period = float(rng.uniform(30, 50) if not hard else rng.uniform(52, 78))
    mapy = mapy + amp * np.sin(2 * np.pi * mapx / period)
    canvas = cv2.remap(canvas, mapx, mapy, cv2.INTER_LINEAR, borderValue=bg)

    # 干扰线 + 噪点
    n_lines = 3 if not hard else 5
    for _ in range(n_lines):
        p1 = (int(rng.integers(0, W)), int(rng.integers(0, H)))
        p2 = (int(rng.integers(0, W)), int(rng.integers(0, H)))
        cv2.line(canvas, p1, p2,
                 int(rng.integers(150, 205) if not hard else rng.integers(120, 215)),
                 1, cv2.LINE_AA)
    n_speck = 140 if not hard else 380
    canvas[rng.integers(0, H, n_speck), rng.integers(0, W, n_speck)] = \
        rng.integers(90, 210, n_speck).astype(np.uint8)

    if hard:
        # 几块"擦除"：真实验证码常把笔画打断，这比加噪点更伤可读性。
        # 块要小 —— 大块擦除会连整字一起抹掉，那是"没字"不是"难读"。
        for _ in range(3):
            cx, cy = int(rng.integers(0, W)), int(rng.integers(0, H))
            rw, rh = int(rng.integers(4, 9)), int(rng.integers(3, 6))
            canvas[max(0, cy - rh):cy + rh, max(0, cx - rw):cx + rw] = bg

    ok, buf = cv2.imencode(".png", canvas)
    if not ok:
        raise RuntimeError("cv2.imencode failed for captcha")
    return buf.tobytes()


def _captcha_page(token: str) -> bytes:
    return _page("验证码", f"""
<h1>请输入图中的字符</h1>
<img id="captcha" src="/captcha.png?token={token}" alt="captcha" width="248" height="80">
<form method="post" action="/captcha/verify">
  <input type="hidden" name="token" value="{token}">
  <label for="answer">验证码</label>
  <input id="answer" name="answer" type="text" autocomplete="off">
  <button type="submit" id="captcha-btn">提交</button>
</form>
""")


CAPTCHA_OK = _page("验证码通过", "<h1>验证码正确</h1><p>可以继续。</p>")
CAPTCHA_BAD = _page("验证码错误", "<h1>验证码错误</h1><p>请重试。</p>")


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
        if path == "/captcha":
            return self._html(_captcha_page(new_captcha_token()))
        if path == "/captcha.png":
            q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            token = q.get("token", [""])[0]
            level = q.get("level", ["normal"])[0]
            if not token:
                return self._html(_page("400", "<h1>缺 token</h1>"), 400)
            try:
                return self._send(200, _captcha_png(token, level), "image/png")
            except Exception as e:  # cv2 缺失时给可读错误
                return self._html(
                    f"<h1>验证码生成失败</h1><pre>{type(e).__name__}: {e}</pre>".encode(),
                    500)
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
        if path == "/changing":
            return self._html(_changing())
        if path == "/collections":
            q = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            try:
                items = int(q.get("items", [str(COLLECTIONS_ITEMS)])[0])
            except ValueError:
                return self._html(_page("400", "<h1>items 不是整数</h1>"), 400)
            return self._html(_collections_page(max(1, min(items, COLLECTIONS_MAX))))
        if path.startswith("/work/"):
            try:
                n = int(path[len("/work/"):])
            except ValueError:
                return self._html(_page("404", "<h1>404</h1>"), 404)
            return self._html(_work_page(n))
        if path == "/shell":
            return self._html(SHELL_PAGE)
        if path == "/about":
            return self._html(ABOUT_PAGE)
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

        if path == "/captcha/verify":
            token = form.get("token", "")
            answer = (form.get("answer", "") or "").strip().upper()
            expected = captcha_text(token) if token else ""
            ok = bool(expected) and answer == expected
            RUNTIME.mkdir(parents=True, exist_ok=True)
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "token": token,
                "answer": answer,
                "expected": expected,
                "ok": ok,
                "remote": self.client_address[0],
            }
            with CAPTCHA_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            sys.stdout.write(f"    !! CAPTCHA {token} answer={answer!r} "
                             f"ok={ok}\n")
            sys.stdout.flush()
            return self._html(CAPTCHA_OK if ok else CAPTCHA_BAD,
                              200 if ok else 403)

        return self._html(_page("404", "<h1>404</h1>"), 404)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """客户端中途断开不算靶站故障。

    `ThreadingHTTPServer.handle_error` 会把任何未捕获异常打成整段 traceback。
    但「对端先走」是**正常**的：隐身浏览器的收尾会掐断连接，一次跑下来能留下
    六七条 `ConnectionResetError [WinError 10054]`，而每个请求其实都是 200。
    上一次靶站跑完的输出就是这样——看起来像崩了，实际全部通过。

    这个靶站是判据的来源，它的输出被噪音污染就等于判据被污染：真正的失败会
    淹在假的失败里（本项目最忌讳的失败形状，只是方向反过来）。所以只压「对端
    先走」这一类，且**不静默**——照旧打一行，让它可见但不刺眼；其余异常原样
    交给父类打全栈，否则真故障会变成「没有输出」。
    """

    CLIENT_GONE = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, self.CLIENT_GONE):
            host, port = client_address[0], client_address[1]
            sys.stdout.write(
                f"[{datetime.now().strftime('%H:%M:%S')}] client gone "
                f"({type(exc).__name__}) from {host}:{port}\n")
            sys.stdout.flush()
            return
        super().handle_error(request, client_address)


def make_server(host: str, port: int) -> QuietThreadingHTTPServer:
    """靶站唯一的服务器构造入口 —— `main()` 与测试都走这里。

    单开一个工厂是为了让「跑起来的那个服务器是静音的」这条能被测到，而不是
    只写在 `main()` 里、靠读代码确认。
    """
    srv = QuietThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    return srv


def main() -> int:
    ap = argparse.ArgumentParser(description="dhole 模拟测试靶站")
    ap.add_argument("--port", type=int, default=8399)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    RUNTIME.mkdir(parents=True, exist_ok=True)
    srv = make_server(args.host, args.port)
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
