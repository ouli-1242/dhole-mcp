"""Dhole 的内容质量判定与 agent 提示：一次抓取"算不算答上了"的全部规则。

从 server.py 原样搬出（17.0 B6 的第六刀，零行为变更）。这一块是 17 颗工具的回答
在上线前被读解一遍的地方：哪些信号算登录墙/校验页/JS 空壳（`_is_auth_wall` /
`_is_bot_wall` / `_is_js_shell`），这页该活多久（`_TTL_BY_PAGE_TYPE`），
agent 该读哪一句（`_agent_hints` / `_with_agent_hints`）。它们与"怎么把页面抓回来"
是两件事，所以此前 1,141 行一直压在 11k 行文件的中间，改提示语要重读整个抓取层。

依赖是查过的，不是猜的：ast 把块内定义与块外引用分开之后，这一块对 server 的
模块级名字零需求，需要的只有 stdlib、`actions` 的那一句可抄形状、`context` 的按次
通道、`audit` / `extractor` / `envelope` / `errors` / `models` / `pdf_extractor` /
`robots` 这几处早已独立成模块的东西。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from dhole_mcp import install as _install
from dhole_mcp import injection
from dhole_mcp.actions import _CONFIRM_SNIPPET, _EVALUATE_RERUN
from dhole_mcp.audit import _audit_event
from dhole_mcp.focus import snap_to_boundary
from dhole_mcp.context import (
    BACKSTOP_GRACE_S,
    _ARG_NOTES,
    _note_text,
    _AUTH_SENT,
    _BOILERPLATE_LINES_COLLAPSED,
    _CSS_SELECTOR,
    _FOCUS_OUTCOME,
    _IDENTITY_APPLIED,
    _METHOD,
    _SESSION_ID,
    nothing_went_out,
)
from dhole_mcp.envelope import (
    answer_is_prose,
    is_extraction_collapsed,
    _stale_days_for,
    classify_source,
    compute_freshness,
    page_type_from_error,
)
from dhole_mcp.extractor import (_is_data_document_content_type,
                                 _is_xml_content_type, served_as_page)
from dhole_mcp.models import ResponseModel
from dhole_mcp.robots import (
    ENV_IGNORE_ROBOTS,
    USER_AGENT as ROBOTS_USER_AGENT,
    Verdict as RobotsVerdict,
    robots_url_for,
)
from dhole_mcp.security import (TIMEOUT_MAX_MS, URL_SHAPE_RECOVERY, target_is_private)


# ─── 内容质量判定（模块级，类里共用） ─────

# 用来识别 JS 渲染型 SPA 的启发式阈值：这类站点的 HTTP 空壳文本与已知信号句
# 对不上（例如 quotes.toscrape.com/js 只交回一个纯导航壳）。只作用于 HTTP 档，
# 免得 stealthy 渲染出来的低文字页面（图片画廊、canvas 应用）被误判。
_JS_SHELL_MIN_BODY_BYTES = 3000   # 原始 HTML body 至少要有这么大
_JS_SHELL_MIN_TEXT_CHARS = 200    # ……而抽出的文字要低于这个数
_JS_SHELL_SMALL_FLOOR_BYTES = 500  # 小空壳也得有这么重……
_JS_SHELL_MARKUP_RATIO = 8         # ……且每个可见字对应这么多字节

_JS_SHELL_SIGNALS = [
    "enable javascript", "you need to enable javascript",
    "javascript is required", "javascript is disabled",
    "javascript to run this app", "javascript must be enabled",
    "please enable javascript", "requires javascript",
    "we've detected that javascript is disabled",
    "javascript is disabled in this browser",
    "enable javascript to run this app",
]

# 同一条墙按访客的出口语言出文案。实测（本机，问题-4）：
# https://www.google.com/search?q=quantum+entanglement 的 http 层拿到 200 +
# 「启用 JavaScript 才能使用搜索功能 / 您使用的浏览器已关闭 JavaScript」，1053 字符，
# 而上面的信号表整张是英文 —— 于是 content_ok=true、page_type=unknown，调用方读到的是
# 一篇「讲怎么开 JavaScript 的文章」，而不是一堵挡住搜索的墙。
# 中文句子没有词边界，所以匹配的是**整句开头**而不是 "javascript" 这个裸词。
_JS_SHELL_SIGNALS_ZH = [
    "启用 javascript", "开启 javascript", "已关闭 javascript",
    "请启用 javascript", "需要启用 javascript",
    "启用javascript", "开启javascript", "已关闭javascript",
]
# 只有短页面才按文案判成墙。一篇讲「如何开启 JavaScript」的教程会比这长得多，它才是
# 那种「提到了这句话」的正常正文（与 _BOT_WALL_MAX_TEXT_CHARS 同一个道理）。
_JS_SHELL_ZH_MAX_TEXT_CHARS = 1500

# ─── 混淆载荷：长 body、抽取也"没塌"，因为抽出来的就是那堆噪声 ─────────────
#
# 上面那把尺子量的是「大 body + 几乎没字」。实测（2026-10-09，北极星那家）是另一形：
# 3,559 个字符**全都抽到了** —— 抽取是忠实的，页面本身就是混淆脚本。于是
# `status=200 content_ok=true`，批量里计入 `successful: 2`，而那几千个字里没有一个是一句
# 人话。长度闸在这里帮不上忙（它恰恰通过了每一道长度闸），需要的是一把量**形状**的尺。
_OBFUSC_HEX_RUN = re.compile(r"\b[0-9a-fA-F]{24,}\b")
#: 候选的"一长串 base64 字母表字符"。光看长度不够 —— 一份术语表里
#: `internationalization` 重复几十次也是 48 个字母一跑。真的编码块一定**同时**含大小写
#: 与数字（那是 base64 字母表的性质），人写的词没有。这一条是 measure 那一刀在语料上
#: 量出来的第一个假阳性，规则写在这里而不是藏在数里。
_OBFUSC_RUN_CANDIDATE = re.compile(r"\b[A-Za-z0-9+/=]{40,}\b")
#: 混淆器与反爬挑战留下的字面标记。`arg1=` 是"参数名就是挑战"的那类形状，`_0x` 是
#: javascript-obfuscator 的默认变量前缀，`getRender` 是实测那枚壳的入口函数。
_OBFUSC_MARKERS = ("_0x", "arg1=", "getrender", "string.fromcharcode")
#: 连续两枚 `\xNN` 转义：人写的正文不会这样说话，打包过的载荷几乎一定有。
_OBFUSC_ESCAPES = re.compile(r"\\x[0-9a-fA-F]{2}\\x[0-9a-fA-F]{2}")
#: 词形合法度。人写的正文里 token 短、几乎全小写；base64/hex 载荷里 token 长、大小写
#: 混排。三个都是**比值**，所以与页面长短无关 —— 一个绝对字符数在这里永远是错的尺。
_OBFUSC_MIN_TOKENS = 30
_OBFUSC_MEAN_TOKEN_LEN = 9.0
_OBFUSC_LONG_TOKEN_RATIO = 0.25
_OBFUSC_MIXED_CASE_RATIO = 0.35
#: `mean_len` 那条支路单独不够长：一份全小写的术语表能把平均值顶到天上去。所以它必须
#: 与"至少有些 token 是大小写混排"一起成立 —— 长度是必要条件，混排才是那类编码块的指纹。
_OBFUSC_MIXED_CASE_FLOOR = 0.15


def _encoded_runs(text: str) -> list:
    """真正像 base64 编码块的那些长串：大写字母、小写字母、数字**三者都齐**。"""
    out = []
    for run in _OBFUSC_RUN_CANDIDATE.findall(text):
        if (any(c.isupper() for c in run) and any(c.islower() for c in run)
                and any(c.isdigit() for c in run)):
            out.append(run)
    return out


def looks_obfuscated(text: str) -> dict:
    r"""这串文本像不像混淆过的脚本载荷。干净时 {}，命中时交出证据。

    要**两类独立证据**同时成立才算，这是整批的论点所在：单信号就翻结论位的闸，一定会
    吃掉图集、changelog 和日语法语那种短词正文（`_JS_SHELL` 被限定在 HTTP 档就是同一
    条教训的先前版本）。
      ① 结构证据 —— 长 hex/base64 串，或 marker 表里的一项，或连续 `\xNN` 转义；
      ② 词形合法度低 —— token 平均很长，或"长 token + 大小写混排"两样都占。
    只有一类时返回 {}：一篇讲 base64 的教程有 ①，它的正文词形过不了 ②。

    纯函数，不碰信封：它只回答"这堆字像不像人写的"。"那又该怎么处置"由调用它的闸决定
    （档位、body 大小、抽取塌没塌、类型豁免）—— 两问分家，第二问的答案才不会把第一问
    的形状扭歪（`_is_js_shell` 与 `extraction_collapsed` 就是这么分开的，见那两处注释）。
    """
    if not text or len(text) < 1000:
        # 短到一千字以内谈"词形分布"没有意义：证据量不足，一律算干净。
        return {}
    lowered = text.lower()
    evidence = []
    hex_runs = len(_OBFUSC_HEX_RUN.findall(text))
    b64_runs = len(_encoded_runs(text))
    if hex_runs:
        evidence.append(f"{hex_runs} long hex run(s)")
    if b64_runs:
        evidence.append(f"{b64_runs} long base64 run(s)")
    hit_markers = [m for m in _OBFUSC_MARKERS if m in lowered]
    if hit_markers:
        evidence.append(", ".join(hit_markers))
    if _OBFUSC_ESCAPES.search(text):
        evidence.append(r"\xNN escape runs")
    if not evidence:
        return {}

    tokens = re.findall(r"[A-Za-z]{2,}", text)
    if len(tokens) < _OBFUSC_MIN_TOKENS:
        return {}
    mean_len = sum(len(t) for t in tokens) / len(tokens)
    long_ratio = sum(1 for t in tokens if len(t) >= 12) / len(tokens)
    mixed_ratio = sum(1 for t in tokens
                      if any(c.islower() for c in t) and any(c.isupper() for c in t)
                      ) / len(tokens)
    illegible = (mean_len >= _OBFUSC_MEAN_TOKEN_LEN
                 and mixed_ratio >= _OBFUSC_MIXED_CASE_FLOOR) or (
        long_ratio >= _OBFUSC_LONG_TOKEN_RATIO
        and mixed_ratio >= _OBFUSC_MIXED_CASE_RATIO)
    if not illegible:
        return {}
    return {"signals": evidence,
            "mean_token_len": round(mean_len, 2),
            "long_token_ratio": round(long_ratio, 3),
            "mixed_case_ratio": round(mixed_ratio, 3),
            "chars": len(text)}


def _obfuscation_gate_applies(result: ResponseModel) -> bool:
    """这一发够不够格按混淆载荷来判。两处咨询它：上报证据，和翻结论位。

    四道前置与 `_is_js_shell` 的长度闸同源，每一条都对应一次"误伤"：
    * 只 HTTP 档 —— stealthy 的低文字页是合法页面（图集、canvas 应用）；
    * body 要够大 —— 小响应里几串 base64 是正常的图标内联；
    * `_never_escalate` 认得的那些先豁免（JSON/XML/CSV/PDF/图片/非 GET）：那是**数据
      文档**，里面的长串就是数据本身，把 `api_schema.json` 判成壳是这批最不该出现的
      那种新错；
    * 抽取塌陷时不算 —— "DOM 里有字而抽取没交回"是另一件事，那条路已经有自己的 token。

    measure 那一刀（0e1369d）里这道闸只喂 `metadata`；语料上假阳性为零之后，同一道闸
    现在也喂 `_detect_content_issue`。**闸只有一道**，所以两处永远同判 —— 不会出现
    "结论说是壳而证据说不是"那种两个作者的分歧。
    """
    if result.fetcher_used != "http" or result.status >= 400:
        return False
    if (result.total_size_bytes or 0) <= _JS_SHELL_MIN_BODY_BYTES:
        return False
    if _never_escalate(result, result.url):
        return False
    if extraction_collapsed(result):
        return False
    if (result.error or "").startswith(("js_shell_detected", "bot_wall_detected",
                                        "bot_challenge_detected", "auth_wall_detected")):
        # 已经有名字的失败不挂第二块牌子：一个响应里两个结论位各说各话正是 D5 点名的形状。
        return False
    return True


def _report_obfuscation(result: ResponseModel) -> None:
    """把混淆判定的**证据**发进 `metadata`，与 `error` 里那句话是同一次判定。

    measure-then-flip 的第一刀里这是唯一的出口（0e1369d：结论位逐字节不动，语料上每
    一发命中留得下名字）。翻闸之后它仍然要走 —— 结论会翻转，证据不该要调用方去解析
    一句散文：`signals` / `mean_token_len` / `long_token_ratio` / `mixed_case_ratio`
    是结构化的，可以按数字分支。两处都由 `_obfuscation_gate_applies` 说话，所以它们
    不会各判各的。
    """
    if not _obfuscation_gate_applies(result):
        return
    finding = looks_obfuscated("\n".join(
        chunk for chunk in (result.content or []) if isinstance(chunk, str)))
    if not finding:
        return
    result.metadata = {**(result.metadata or {}), "obfuscated_body": finding}


# Cloudflare 校验页的标记。它们出现在 CF 插页 / Turnstile 校验页的原始 HTML 里
# （这类页面可能返回 200）。`_is_js_shell` 靠它们认出绕过了通用 JS 空壳信号
# （大 HTML body + CF 专有脚本）的 CF 页面。没有这一条，extraction_type=html
# （web_crawl 走的那条）会把校验页当成「正文」交回，而且永远不会升级。
_CF_CHALLENGE_SIGNALS = [
    "challenges.cloudflare.com/turnstile",
    "cf-turnstile",
    "cf_chl_opt",
    "__cf_chl",
    "cf-browser-verification",
    "challenge-platform",
    "cf-mitigated",
]

_GEO_REDIRECT_SIGNALS = [
    "choose a country", "select your country", "select your region",
    "shopping in the u.s.", "choose your country",
    "country selector", "region selector",
]

# 登录墙判定。被重定向到登录端点、正文又以登录提示为主的页面是一堵墙，不是正文
# （例如 zhihu.com -> /signin 只返回登录表单，状态码却是 HTTP 200）。URL 路径信号
# 与正文信号合起来判，免得把导航栏里只是带了一条 "Sign in" 链接的正常页面误报。
_AUTH_WALL_PATH_SIGNALS = (
    "/signin", "/sign-in", "/login", "/accounts/login", "/member/login",
    "/passport/login", "/login.aspx", "/auth/login", "/logon",
)
_AUTH_WALL_CONTENT_SIGNALS = (
    "登录/注册", "验证码登录", "获取短信验证码", "密码登录",
    "请登录", "点击登录", "立即登录",
    "sign in to continue", "please sign in", "please log in",
    "log in to continue", "sign in to view", "log in to view",
    "login to view", "you must be logged in", "please login",
)

# 更宽的词表，只在 URL 已经指向登录端点时用作**确认**。不能单独用："sign in"/"登录"
# 在普通导航栏里就有，单凭它们证明不了什么。
_AUTH_WALL_WEAK_SIGNALS = (
    "sign in", "log in", "password", "username", "忘记密码", "登录",
)


def _is_auth_wall(result) -> bool:
    """页面是登录墙而不是正文时返回 True。

    两条命中路径：(a) URL 指向登录端点，且出现任意一条登录词（强或弱）；或
    (b) 无论 URL 是什么，抽出的文字里强登录提示达到 3 条以上并占主导。非登录页
    导航栏里偶见的一条 "Sign in" 链接不会触发本判定。
    """
    try:
        path = urlparse(result.url).path.lower()
    except Exception:
        path = ""
    path_hit = any(s in path for s in _AUTH_WALL_PATH_SIGNALS)
    content_str = " ".join(result.content or []).lower().strip()
    strong_hits = sum(1 for s in _AUTH_WALL_CONTENT_SIGNALS if s in content_str)
    if path_hit:
        weak_hits = sum(1 for s in _AUTH_WALL_WEAK_SIGNALS if s in content_str)
        return (strong_hits + weak_hits) >= 1
    return strong_hits >= 3


# 以 HTTP 200 + 校验页正文作答的反爬墙。原有的 bot-challenge 检查只在 403/503 上
# 触发，于是一个 200 的校验页（搜狗的 /antispider 链接包装页，"此验证码用于确认…"
# + VerifyCode）回来时 content_ok=true，agent 把验证码当成文章引用了。
# 故意加长度闸：一篇状态 200、**讲**验证码的文章不是墙。
_BOT_WALL_PATH_SIGNALS = (
    "/antispider", "/anti-spider", "/captcha", "/checkcaptcha", "/verifycode",
    "/__cf_chl", "/_sec/verify", "/check_account", "/risk", "/tbui",
)
_BOT_WALL_CONTENT_SIGNALS = (
    "此验证码用于确认", "请输入验证码", "安全验证", "人机验证", "访问验证",
    "请完成验证", "您的访问出错了", "verifycode", "checkcode",
    "please verify you are a human", "checking your browser", "are you a robot",
    "unusual traffic", "access denied", "request blocked", "blocked your request",
    "captcha-delivery.com", "enter the captcha", "solve the captcha",
)
# 校验页不会有大段真正的文章。超过这个字符数就按「只是提到了验证」的正文处理，不再判墙。
_BOT_WALL_MAX_TEXT_CHARS = 1500


def _is_bot_wall(result: ResponseModel) -> bool:
    """2xx 响应是反爬/CAPTCHA 校验页而不是正文时返回 True。"""
    if result.status and not (200 <= result.status < 400):
        return False
    content_str = " ".join(result.content or []).lower().strip()
    if not content_str or len(content_str) > _BOT_WALL_MAX_TEXT_CHARS:
        return False
    try:
        path = urlparse(result.url).path.lower()
    except Exception:
        path = ""
    if any(s in path for s in _BOT_WALL_PATH_SIGNALS):
        return True
    return any(s in content_str for s in _BOT_WALL_CONTENT_SIGNALS)


# 软失败页：HTTP 200，body 技术上非空却没有内容 —— 客户端错误视图、卡住的加载器、
# 或插页。一次约 60 次调用的上限测试里实测：x.com 返回 200 + "Try reloading"，
# douyin.com 返回 200 + "Please wait..."，两者都是 content_ok=true。
# 它们与 bot 墙的区别是完全没有校验文案，所以光有词表、没有长度闸没有用 ——
# 真文章也可能顺口引一句 "something went wrong"。这道闸比 bot 墙的更紧
# （600 对 1500），因为这些话在普通文章里常见得多，body 一长就是真内容的证据，
# 不是空壳。
_SOFT_FAILURE_SIGNALS = (
    "try reloading", "try again later", "please try again",
    "something went wrong", "this page isn't working",
    "an error occurred", "an error has occurred",
    "please wait...", "please wait…",
    "页面加载失败", "加载失败", "请稍后重试", "网络异常",
)
# 超过这个字符数就按「只是提到了失败」的正文处理，不再判。
_SOFT_FAILURE_MAX_TEXT_CHARS = 600


def _is_soft_failure(result: ResponseModel) -> bool:
    """2xx 响应是错误/占位视图而不是正文时返回 True。"""
    if result.status and not (200 <= result.status < 400):
        return False
    # PDF 抽出的文字永远不是 HTML 错误视图；那一类失败归 `_detect_content_issue`
    # 的 PDF 分支（"pdf_no_text"）。
    if "application/pdf" in (result.content_type or "").lower():
        return False
    content_str = " ".join(result.content or []).lower().strip()
    if not content_str or len(content_str) > _SOFT_FAILURE_MAX_TEXT_CHARS:
        return False
    return any(signal in content_str for signal in _SOFT_FAILURE_SIGNALS)


def _is_cloudflare_from_response(result: ResponseModel) -> bool:
    """判断一个 ResponseModel 是否指向 bot 校验页。

    识别页面内容里常见的 bot 校验特征，包括内嵌的 Cloudflare 校验、通用 CAPTCHA
    页与验证提示。不区分 DataDome/Turnstile 与普通 bot 检查。

    重要：只在错误状态码（403、503）上有意义。一个状态 200、讲 Web 安全的页面
    提到 "cloudflare"，不是 bot 校验页。
    """
    # 守卫：只在 bot 校验说得通的错误状态码上检查
    if result.status not in (403, 503):
        return False
    content_str = " ".join(result.content).lower()
    cf_signals = ["cloudflare", "cf-browser", "challenge-platform", "cf_chl_opt", "ray id"]
    dd_signals = ["captcha-delivery.com", "datadome", "dd="]
    generic_signals = ["please verify you are a human", "are you a robot", "checking your browser"]
    all_signals = cf_signals + dd_signals + generic_signals
    return any(signal in content_str for signal in all_signals)


def _never_escalate(result: ResponseModel, url: str) -> bool:
    """浏览器渲染改变不了结果时返回 True —— 这一档就不必花。

    只有 GET 才升级（G8）。浏览器档是**导航**：它重发不了请求体，而 HEAD 的答案
    在响应头里而不是文档里。所以渲染后的重放会展示一个与失败请求**不同**的请求，
    还把它的结果当成你那一发的答案：一个吃了 500 的 POST 在登录表单上变成「成功」，
    一个 HEAD 变成整页下载。GET 说了什么就说什么，让调用方自己决定。

    .pdf URL：body 是二进制 —— 要么是 %PDF，要么是登录/错误重定向。
    image/* 响应：同理 —— 对 PNG 做 stealthy 渲染加不出任何文字（patchright 拿到
    的是一份合成 HTML 文档，不是原始字节），而 `_translate_response` 给出的 OCR
    判决是终审。这道守卫是承重的：OCR 缺失、失败或什么也没找到时，图片分支在 200
    上返回 content=[]，而空的 200 body 正是 `_is_js_shell()` 会升级的形状 ——
    为同一张图白花 30–40 秒。

    故意**不**用于 archive 兜底：一个已经消失的图片 URL 仍应拿到 Wayback 快照，
    所以这只拦浏览器档。

    XML/JSON 与图片同理拒绝，实测更糟：docs.vllm.ai/sitemap.xml 在 HTTP 档
    0.6 秒交回 428KB 干净的 ``<?xml``，而顶替它的 stealthy 档在 5.4 秒返回
    2,335,300 个字符的 Chromium 自带 XML 查看器（``<html xmlns=...><style id=
    "xml-viewer-style">``）—— 字节数是 5.5 倍，没有一个是那份文档，却带着
    content_ok=true 与 page_type=xml，看上去毫无问题。浏览器不渲染数据文档，
    它把数据文档装进框里。URL 与媒体类型一起查，因为值得拒绝的那一档，往往正是
    连 Content-Type 头都没有、失败掉的那一档。
    """
    if _METHOD.get() != "GET":
        return True
    _path = url.lower().split("?")[0]
    # PDF 的 URL 判据与 `server._is_document_url` 是**双份定义**（quality 不能 import
    # server——那是循环；两份由 tests/test_a_pdf_by_any_other_name_still_gets_a_download_
    # budget.py 的一致性用例钉住，改一边不改另一边会当场红）。首段 `pdf` 是 3.3 补的：
    # arxiv 一族的无扩展名 PDF URL，手抄版只认 `.pdf` 结尾时它们漏过这道闸。段要按
    # `urlparse().path` 切——对整条 URL 切会把 scheme/host 也切成段。
    try:
        _pdf_path = urlparse(url).path.lower()
    except (ValueError, AttributeError):
        _pdf_path = _path
    _segments = [s for s in _pdf_path.split("/") if s]
    if _pdf_path.endswith(".pdf") or (_segments and _segments[0] == "pdf"):
        return True
    ct = (result.content_type or "").lower()
    if ct.startswith("image/"):
        return True
    if _is_data_document_content_type(ct):
        return True
    return _path.endswith((".xml", ".rss", ".atom", ".rdf", ".json"))


#: 浏览器档"这次帮不上忙"的两个来源写在同一句里，因为调用方要的是同一个答案：别升级。
_BROWSER_UNAVAILABLE_MARKS = ("browser_binary_missing", "browser_unavailable")


def _browser_cannot_help(result: ResponseModel, url: str) -> bool:
    """这一档帮不上的两种原因：被**故意**跳过（数据文档），或**试过了而起不来**。

    旧的门只有前者。于是浏览器层坏掉的那台机器上 —— 2026-10-05 的那一次 —— 每一条
    壳页 / 403 / bot_wall 的 `next_action` 都在建议一次刚刚被现场证明做不到的升级，
    而它是调用方当时唯一拿到手的建议（实测那一整轮 0% 可用）。事实其实一直在信封上：
    `escalation_path="browser_binary_missing"` 在跳过 stealthy 之前就写好了，只是没人读。
    """
    if _never_escalate(result, url):
        return True
    err = (result.error or "").lower()
    return any(mark in (result.escalation_path or "") or mark in err
               for mark in _BROWSER_UNAVAILABLE_MARKS)


def _escalation_skipped_by_kind(result: ResponseModel, url: str) -> bool:
    """这一档没跑，是因为 dhole **按类型故意不升**，不是机器起不来。

    `_never_escalate` 把 .pdf / 图片 / 数据文档挡在浏览器档外：渲染会把字节换成
    一张查看器页，而那不是调用方要的文档。这个"不肯升"与 `_BROWSER_UNAVAILABLE_MARKS`
    的"升不了"对调用方是同一个答案（别升级），但**原因**不同：对一台能跑浏览器的
    机器说"浏览器档不可用"是假话，而 2026-10-28 的 PDF 403 现场正是这样 ——
    `bot_wall` 诊断对了，`next_action` 却把调用方支去 `dhole --doctor` 查一台没坏的
    浏览器。
    """
    if not _never_escalate(result, url):
        return False
    path = (result.escalation_path or "").lower()
    err = (result.error or "").lower()
    return not any(mark in path or mark in err for mark in _BROWSER_UNAVAILABLE_MARKS)


def _tiers_that_ran(result: ResponseModel) -> tuple[bool, bool]:
    """这次调用真跑过哪几档，返回 (http, stealthy)。

    事实只有两个产地，都在信封上，所以这里不需要新状态：`fetcher_used` 是**交回这份
    答案的那一档**，`escalation_path` 是 server 在跳档**之前**写好的行程单
    （`http→stealthy` / `direct:http` / `preflight:timeout(skipped_stealthy)` /
    `http(stealthy_skipped_no_budget)` / `http->stealthy:browser_binary_missing`）。

    认识的形状按"名字出现过、且没被写成 skipped"判。两种 skip 写法都要认：
    `skipped_http+stealthy`（预检一跳出两个）与 `stealthy_skipped_no_budget`
    （只跳一个，且名字在前）。漏掉后者就是把一次没跑的档当成跑过 —— 那会把
    "还有一档没试"的建议一起杀错。
    """
    path = (result.escalation_path or "").lower()
    used = (result.fetcher_used or "").lower()
    m = re.search(r"skipped_([a-z_+]+)", path)
    skipped_names = set(m.group(1).split("+")) if m else set()

    def _skipped(name: str) -> bool:
        return name in skipped_names or f"{name}_skipped" in path

    http_ran = ("http" in path and not _skipped("http")) or used == "http"
    stealthy_ran = (("stealthy" in path and not _skipped("stealthy"))
                    or used == "stealthy")
    return http_ran, stealthy_ran


def _render_tier_was_spent(result: ResponseModel) -> bool:
    """浏览器档**跑了、答了、交回来的就是手里这一份**。

    与"这一档被跳过 / 起不来"是三件事，而 `_agent_hints` 的门只认前两件。于是实测
    形状变成一句自相否决的回执：`fetcher_used="stealthy"`、escalation_path 写着
    `http→stealthy(all_failed)`，`next_action` 却还在说 "re-fetch auto-escalates to
    the stealthy browser" —— 叫调用方去重试那一档，而那一档刚刚把这张页交出来。
    C13 那次换档重试因此烧掉 31.5s 才超时：建议本身就是那条死路。
    """
    _http_ran, stealthy_ran = _tiers_that_ran(result)
    if not stealthy_ran:
        return False
    # 故意不升的类型（.pdf / 图片 / 数据文档）在 `_never_escalate` 那里恒为真；
    # 但 `fetcher_used='stealthy'` 说明这一档确实被 force_fetcher 点名跑过，
    # 那一份就是它交回来的答案 —— 不能因为类型守卫把它重新说成"没跑"。
    if _escalation_skipped_by_kind(result, result.url):
        return True
    return not _browser_cannot_help(result, result.url)


def _auth_wall_step(url: str) -> str:
    """登录墙的下一步，按**墙在哪台机器上**说。

    D4 的现场：`web_read("http://127.0.0.1:8765/admin")` 撞出 auth_wall，而
    `next_action` 建议"换个源，或去 Internet Archive 找公开副本"。调用方点名要的正是
    这台机器上的这个端口，换源换不掉它，而档案站**从不持有登录态后的那一份** —— 这句
    建议在这台机器上不是"没用"，是"永远不可能有用"。两扇真门：把凭据存进
    cred_defaults，或让浏览器走页面自己的表单（web_act）。

    公网那一半保留原句 —— 那里"公开副本"确实是一条真实在的退路 —— 但补一句它是什么：
    某一天登出态的快照，不是你账号里的数据。
    """
    if target_is_private(url or ""):
        return ("this login wall is on the host YOU named, so neither another source nor "
                "a snapshot can answer it - the archive never holds a logged-in copy. "
                "Two real doors: store the credentials for this host with cred_defaults "
                "(auth / cookies), or drive the page's own sign-in form with web_act.")
    return ("Switch source, or try the Internet Archive for a public copy - a snapshot "
            "is a logged-out page from some past day, so it is not this account's data")


def _is_js_shell(result: ResponseModel) -> bool:
    """响应只有 JS 占位符、没有真正正文时返回 True。

    web_read 用它决定要不要从 HTTP 升级到 stealthy。
    """
    # 数据文档不是 JS 空壳。下面「大 body、少文字」那条启发式描述的是 HTML 空壳；
    # PDF 是容器，而被 OCR 救回来的那一份**天生**字就少（一页扫描件对着 ~10 KB 的
    # 文件只会读出 ~100 个字符）。`_detect_content_issue` 只守住了零文字那一支，
    # 于是一份 OCR 成功的扫描件落到启发式里，回来是
    # error=js_shell_detected / content_ok=false —— 正文是对的，判词是错的，而且
    # 出在刚刚付出最多努力去挣信任的那条路上。
    #
    # 这道守卫放在这里而不是 `_detect_content_issue` 里，是因为本函数还回答**升级**
    # 那个问题（见抓取管线里的三处调用）：在浏览器里渲染 PDF 产不出抽取器手上没有的
    # 东西，而错误的 next_action 正是在叫调用方这么做。一道守卫同时管住两件事。若改成
    # 在 `_detect_content_issue` 里提前 return ""，反而会吞掉它后面的 http_error_4xx
    # 检查。
    if "application/pdf" in (result.content_type or "").lower():
        return False

    content_str = " ".join(result.content).lower().strip()
    if not content_str:
        # 成功状态码上出现空正文 = JS 空壳（或真的空白页）。4xx/5xx 上是 HTTP
        # 错误，不是渲染问题：httpbin /status/404 返回的就是空 body，把它叫作
        # "JS shell" 等于让 agent 白烧一次 40 秒的浏览器升级，而那次升级改变不了
        # 结果。status 0（网络/本地失败）保留旧的答案 —— 0 < 400。
        #
        # 204/205/304 与 4xx 同理排除，只是从另一头看：这三者是在状态行里**说**
        # 了「没有 body」。渲染它们产不出内容 —— 浏览器对 204 只会回
        # ERR_ABORTED —— 所以把其中一个叫空壳，承诺的是一个不存在的修法。判定与
        # `_detect_content_issue` 的提前返回一致；两处不许漂移，否则标签与升级
        # 建议会互相矛盾。
        if result.status in (204, 205, 304):
            return False
        return result.status < 400
    if any(signal in content_str for signal in _JS_SHELL_SIGNALS):
        return True
    if len(content_str) <= _JS_SHELL_ZH_MAX_TEXT_CHARS and any(
            signal in content_str for signal in _JS_SHELL_SIGNALS_ZH):
        # 本地化的墙是同一个失败；长度闸的作用就是把一篇讲「如何启用 JavaScript」
        # 的中文教程挡在这个分支之外。
        return True
    # Cloudflare 校验页可以带着大 HTML 返回 200（Turnstile 脚本、
    # challenge-platform 的 div）。在 extraction_type=html（web_crawl 走的那条）
    # 下，原始 HTML 很大，下面的文字长度启发式不会触发。查 CF 专有标记来抓这些。
    if result.fetcher_used == "http" and result.status == 200:
        if any(signal in content_str for signal in _CF_CHALLENGE_SIGNALS):
            return True
    # 启发式：HTTP 档返回 200 + 大 HTML body，却几乎抽不出文字 -> 页面是 JS 渲染
    # 的，HTTP 只拿到了空壳（例如一个纯导航壳、与已知信号句对不上的 SPA）。
    # 只作用于 HTTP 档：stealthy 结果字少但页面很大，那是真正的低文字页面
    # （图片画廊 / canvas），不是空壳。
    if result.fetcher_used == "http" and result.status == 200 \
            and result.total_size_bytes > _JS_SHELL_MIN_BODY_BYTES:
        text_len = extracted_chars(result)
        if text_len < _JS_SHELL_MIN_TEXT_CHARS:
            # 同一个"字少"有两种成因，而它们要求的下一步正好相反：
            #   * 站点给了空壳 —— DOM 自己也没几个字（内容要 JS 才长出来），起浏览器对。
            #   * 抽取器没读懂这页 —— DOM 里明明写着话，抽出来的却少。起浏览器只是把
            #     同一份静态标记再交给同一个抽取器，多付 3–5 秒拿回同样少的字。
            # 分岔用的数在 HTML 还在手边时就量好了（`visible_text_chars`，期 3.3 起直接
            # 上线给调用方），这里只读。
            # 实测：books.toscrape 的 travel 列表页 196 个抽出字符对 1,374 个 DOM 可见
            # 字符，被旧判据当成壳 —— 差 4 个字符就过 200 那条线，而它是个纯静态页面。
            if _collapsed_by_both_rulers(result, text_len):
                return False
            return True
    # 反向的一格：不是"壳很大而字很少"，是"壳本身就只有一两行"。
    # 上面那道 3,000 字节的下限按设计只管大壳（它要排除的是"小响应里几串正常文本"），
    # 于是今天最常见的一种 SPA 入口从它下面漏过去 —— 实测 dsh.market：HTTP 档 200、
    # 正文 1,936 字节、抽出 34 字符（页面标题），`content_ok=true`、无任何下一步提示。
    # 判据要的是**脚本壳的证据**，不是"字少"本身：小体量（500-3,000 字节）、整页可见字
    # 低于壳闸、字节数至少是可见字的 8 倍（1,936/34 ≈ 57；一条真答案的短页是 3-8 倍），
    # 且整页没有一句人话。任何一条不成立都不动 —— 这条闸每误判一次，调用方就白付一次
    # 3-5 秒的浏览器启动（本仓库的测试夹具正是这种小页面，2026-10-23 第一版宽判据把它们
    # 全部打成了"壳"，conftest 当场拦下了真实浏览器启动）。
    _visible_tiny = result.visible_text_chars or 0
    _size = result.total_size_bytes or 0
    if (result.fetcher_used == "http" and result.status == 200
            and served_as_page(result.content_type)
            and 0 < _visible_tiny < _JS_SHELL_MIN_TEXT_CHARS
            and _JS_SHELL_SMALL_FLOOR_BYTES <= _size <= _JS_SHELL_MIN_BODY_BYTES
            and _size >= _JS_SHELL_MARKUP_RATIO * _visible_tiny
            and not _has_a_sentence(content_str)):
        return True
    return False


def extracted_chars(result: ResponseModel) -> int:
    """抽取器自己交回了多少字 —— 不含调用方点名的那一刀（评审 R02-016）。

    `content[]` 是**切完之后**的那份：用户点了 `focus='缓存'`，10 块里留 1 块，字数就从
    387 掉到 130。而和它比的那颗 `visible_text_chars` 是切之前的整页量 —— 一把尺的两端
    不在同一条时间线上，塌陷判据就把"按你说的筛过了"读成"正文提取失败"，并开出
    `focus=''`（用户那一句的反面）当"没试过的杠杆"。

    切之前的那颗数这一封信里本来就带着，而且是两颗，各管一段：
      * `page_total_chars` —— focus 切之前的整页长度，只在 focus 真的删了字时在场；
      * `total_extracted_chars` —— 预算切之前的长度（`_apply_chunking` 与 `content[]`
        同一次装配），每一发都带。
    这里按"最靠前的那一段"取：focus 切过 → 用 `page_total_chars`；只有 `max_content_chars`
    /`offset` 这一刀 → 用 `total_extracted_chars`。两条都没有才退回 `content[]` 自己的
    长度 —— 三条路上量的是同一件事：抽取器交回的那份。
    """
    return (result.page_total_chars or result.total_extracted_chars
            or sum(len(c) for c in result.content))


def _collapsed_by_both_rulers(result: ResponseModel, extracted: int) -> bool:
    """抽取器在这份响应自带的两把尺上都丢了字。

    `visible_text_chars` 数的是整页，含页头页脚导航；`_dom_chrome_free_chars`
    去掉 nav/header/footer 但保留 <aside>，由 `server._attach_dom_visible_chars`
    懒测量 —— 薄 body 每次都量，大 body 只在整页比值已经触发时才量。一篇完整文章
    压在很重的导航下面，在第一把尺上看着像塌陷，而第二把尺说抽取器盖住了正文
    （R04-016a：39k 的页面抽出 19k，文章一直排到最后一节）。

    两把都问；第二把没量过（-1）就维持旧判词。<aside> 是故意留着的：`/longaside`
    对照组在那里丢了真字，而一个把它剥掉的分母会把那次丢失说成看不见。
    """
    if not is_extraction_collapsed(extracted, result.visible_text_chars):
        return False
    prose = result._dom_chrome_free_chars
    if prose > 0 and not is_extraction_collapsed(extracted, prose):
        return False
    return True


def _ruler_applies(result: ResponseModel) -> bool:
    """这一发量不量塌陷：只有"真抓回来一份 HTML"的那几档才量。

    四种不量的情形，各自不是"没塌陷"而是"这一问不成立"：缓存/浏览器之外的回放没有
    整页对照数；4xx/5xx 的正文是错误页；PDF 走 `pdf_extractor`，`visible_text_chars`
    对它从来没有数；`structured`（`schema=` / `nested_*`）交回的是**投影**，按定义丢掉
    不匹配的字，拿整页可见字去比是拿错的量具量对的东西 —— 投影抽得太窄是真问题，但它
    要求的下一步是**改选择器**，不是 `extraction_type='html'`；让塌陷闸在这里说话，
    开出的就是反药。
    """
    if result.fetcher_used not in ("http", "stealthy") or result.status >= 400:
        return False
    if "application/pdf" in (result.content_type or "").lower():
        return False
    if (result.extracted_type or "") == "structured":
        return False
    return True


def extraction_lost_text(result: ResponseModel) -> bool:
    """尺子本身：抽取器交回来的字，比这页自己的可见字少了一大截吗？

    与 `extraction_collapsed` 分开是因为两问的答案**可以不同**：
      * 补救（`server._translate_response_body`）问"值不值得再抽一次" —— 一段正文也可能
        丢掉了一整块 aside，所以这一问不许被答案的形状豁免；
      * 判决（`content_ok` / `substance`）问"该不该说它塌陷了" —— 一段话读得好好的，
        分母里大半是导航时不该被报成失败。
    一个函数答两问，第二问迟早被第一问的形状扭弯（`_is_js_shell` 的注释里同一个教训）。

    两问共用 `extracted_chars` 这一颗数（预算切/focus 切之前的那份），谁也不许自己再数
    一遍 `content[]` —— 否则同一页在两条路上会得到两个"丢了多少"。
    """
    if not _ruler_applies(result):
        return False
    return _collapsed_by_both_rulers(result, extracted_chars(result))


#: 链接标签占内容区可见字的比例，高过这条线才承认"这页的可见字大半是导航"。
#: 两个实测簇之间：文章页 0.01–0.05（`envelope._label_share` 的注释）、目录页
#: 0.16–0.43；三处被误报的中文门户正文页落在 0.22–0.46。取 0.15 而不是某个簇的中位数，
#: 是因为空带本身就在 0.05–0.16 之间，线落在哪里都不该由样本数决定。
_PROSE_EXEMPT_LABEL_SHARE = 0.15


def extraction_collapsed(result: ResponseModel) -> bool:
    """这页有字而抽取没给回来 —— 把"塌陷"作为一个**说得出口的事实**留出来。

    为什么要单独一个谓词而不是让 `_is_js_shell` 直接返回个元组：前者回答"要不要起
    浏览器"（答否），后者回答"这次回得少该怎么说"。一个函数答两问，第二问的答案迟早
    被第一问的形状扭成"壳"。

    量的那颗是 `extracted_chars` —— 抽取器交回的份，不是调用方点名的 `focus=` 切过的份。
    与 `extraction_lost_text` 读的是**同一颗数、同一把尺**，只是判决多两条让路（清单、
    门户正文），所以两个函数各自把 `extracted_chars(result)` 传进 `_collapsed_by_both_rulers`，
    谁也不自己数 `content[]`。

    为什么量不量的四种情形在 `_ruler_applies` 里：它们与判决无关，两问共用。
    """
    if (result.metadata or {}).get("recovered_as") == "link_list":
        # 只给**真的被渲染成链接清单**的那一发让路，而不是给所有 `page_type=list`
        # 让路：清单按定义丢掉非链接的字，拿整页可见字去比是拿错的量具；而一个被判
        # list 却仍在丢正文的页面，塌陷那句话照旧要说。实测人民网首页：清单 1,883
        # 字符 / 整页可见 6,969 —— 旧判据把它写成 `content_ok=false` 并建议换 html 档。
        return False
    if not _ruler_applies(result):
        return False
    if not _collapsed_by_both_rulers(result, extracted_chars(result)):
        return False
    if (answer_is_prose("\n".join(c for c in result.content if isinstance(c, str)))
            and result._dom_label_share >= _PROSE_EXEMPT_LABEL_SHARE):
        # 第五种让路（2026-10-24 真实站点实测）：答案是一段话，而**这页的可见字大半是
        # 链接标签**。
        #
        # 这把尺子量的是"抽取回来的字 ÷ 整页可见字"，而中文门户的正文页里那个分母
        # 大部分不是正文 —— 实测 163 的一条稿子：正文 2,844 字、整页 8,548 字（33%），
        # 缺的 5,700 字是导航与侧栏推荐；东南网同形（772/6,362，12%），而 772 字就是
        # 那篇新华社通稿的全部。两处都被判 `content_ok=false` 并收到一句假话
        # （"the words ARE in the HTML, dhole's reader lost them"）—— 读取器没丢，
        # 是分母里本来就有大半不是正文。第二把尺子（`visible_text_chars_without_chrome`）
        # 在这两页上给出同一个数，救不了：门户导航住在裸 `<div>` 里，按标签剥不掉它。
        #
        # 所以豁免要的是**页面自己的证据**，不是答案的形状：`_dom_label_share` 是内容区
        # 里"算作链接标签的字"的占比，剥掉 nav/header/footer/aside 之后仍然量出来的那份
        # —— 它高，就说明分母确实是导航与推荐。实测三处 163 / 东南网 / 81.cn = 0.40 /
        # 0.46 / 0.22，而 `envelope._label_share` 的注释记着文章页 0.01–0.05、目录页
        # 0.16–0.43 —— 0.15 这条线落在那条空带里，不是现调出来的。
        #
        # 两条同时成立才让路，缺一不可：
        #   * 答案是一段话（`answer_is_prose`：≥200 字、每 100 字 ≥1 个句末标点）——
        #     一串标签过不了这一条，所以目录页不会被豁免；
        #   * 这页的链接标签占比够高 —— 一篇干净的文章页过不了这一条，所以"正文真的被
        #     截断了"照旧报塌陷。没有 DOM 证据时 `_dom_label_share` 是 -1（没量过），
        #     这一支不放行：豁免是**关于页面的主张**，没有页面就不许主张。
        #
        # 代价说清楚：链接占比低于 0.15 的门户页仍会被报成塌陷（真阳性里的假阳性），
        # 一篇真被截断的正文也可能落进来（假阴性）。选这个方向是因为"把好答案报成失败"
        # 会让调用方重抓一次，而重抓会拿到同一份东西。补救那一问不受这条豁免影响
        # （`extraction_lost_text` 照旧量），真正丢了一整块 aside 的页面仍会被补回来。
        return False
    # 走到这里：尺子说"丢了"，而交回来的不是一段话（或这页看不出是门户）—— 塌陷成立。
    return True


# ─── 3.4 · 实质（substance）：`content_ok` 第一次看内容长什么样 ───────────
#
# 在这一刀之前，`content_ok` 完全由 status 与 error 驱动 —— 没有任何一条判据量过"拿回来
# 的这堆字是不是一段能引用的东西"。实测两处假阳性正是从两道闸中间漏过去的：666 字与 833
# 字的纯导航，既超不过 200 字的壳闸（`_JS_SHELL_MIN_TEXT_CHARS`，它只管"字极少"），也够不
# 着混淆闸的 `len < 1000` 早退（它要"字够多才值得像不像话"）。中间那一大段没人管。
#
# 三条读数都算在**返回正文**上 —— `content_ok` 说的就是这一堆字，不是那页 HTML。阈值取在
# 实测的两簇中间的空带上（同一把尺，靶站与三份存页夹具，2026-10-10）：
#   导航壳 /nav          12 行 · 标签行占比 1.00 · 均行 2.3 字
#   站点根 /              8 行 · 1.00 · 3.6 字
#   真目录 /collections  15 行 · 1.00 · 16.1 字   ← 目录是答案，不该翻闸
#   真正文 /article      10 行 · 0.00 · 36.9 字
# 8 与 16 之间没有样本，所以线划在 8：高于一切量过的菜单条，低于一切量过的目录与正文行。
# 一条"标签行"= 24 字以内且整行没有句末标点（`_LABEL_LINE_MAX_CHARS`）。
_NAV_LINES_MIN = 4
_NAV_LABEL_SHARE_MIN = 0.8
_NAV_MEAN_LINE_MAX = 8
_LABEL_LINE_MAX_CHARS = 24
#: 一行算"句子"的地板：带句末标点**且**至少这么长。5 个字的「好的。」仍是碎片，
#: 而靶站那一句 25 字的正文一定过。取 8 是量出来的空带下沿 —— 一切量过的菜单碎片
#: 都在 4 字以内（/nav 均行 2.3、/work 的碎片行 2-4），一切量过的正文句都在 24 字
#: 以上（评审 R02-006：这一族以前没有地板，判决只看占比）。
_SENTENCE_LINE_MIN_CHARS = 8
_SENTENCE_PUNCT = re.compile(r"[.!?。！？]")
#: markdown 链接的目标（`文字](http://…)` 里那半截）。它不是页面说出口的话，
#: 而它里面的 `.` 会被 `_SENTENCE_PUNCT` 当成句点 —— 见 `_visible_line`。
_LINK_TARGET = re.compile(r"\]\([^)]*\)")


def _visible_line(line: str) -> str:
    """一行**摆在页面上的字**：把 markdown 链接的目标剪掉，只留链接文字与它前后的话。

    靶站 `/shell` 的实测形状（2026-10-20）：抽取器把页脚那一枚 `<a>` 交回成
    `[关于](http://127.0.0.1:8399/about)` —— 33 个字符、含两颗点，于是"整页只有 6 个
    可见字的一枚裸链接"被读成了一句散文（`prose=1`），`nav_only` 那一支的门就此走不进
    去，一具壳页被判 `content_ok=true`。URL 里的点不是句点：形状判据量的是字，不是跳转
    地址的字节数。
    """
    return _LINK_TARGET.sub("", line).strip()

#: `content_ok` 要为假的那些判决。`""`（没判）不在里面 —— 缺席是"这一发不归这条闸管"
#: （非 HTML / HEAD / 304），不是"判过了且没问题"，两种意思不能混。
_NOT_SUBSTANTIVE = ("nav_only", "collapsed", "no_words")


def _body_line_shape(body: str) -> tuple[int, float, float, int]:
    """正文的形状读数：(非空行数, 标签行占比, 平均行长度, 句子行数)。

    第四颗读数是 R02-006 补的：`nav_only` 这句话的意思就是"没有散文"（手册里那格写的
    是 `no prose (menu fragments only)`），所以判决必须**看见**一颗句子行就不判 ——
    以前它只看占比，而占比 `>= 0.8` 本来就容许两成的行不是标签，于是"多一枚标签"
    就把带着整句正文的页翻成了菜单残渣（靶站 /work 的 18 条里精确翻掉 6 条）。
    同一函数第二支的 basis 早就写着 "with no sentence in them" —— 一把尺的两个刻度，
    一处要求没有句子，一处不要求。
    """
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return 0, 0.0, 0.0, 0
    # 标签/散文这两支量的是**摆在页面上的字**（`_visible_line`）；平均行长那一颗量的
    # 是"这一行交出去多少字符"，故意不折 —— 靶站 /collections 的 15 行是
    # `[专辑 1](/work/1)3 本` 那种形状，把目标剪掉后均行从 16.1 掉到 8 以下，
    # 那一页就会被翻成 nav_only，而它的内容正是那些链接（评审 R02 量过的那一侧）。
    shown = [_visible_line(ln) for ln in lines]
    labels = [ln for ln in shown
              if len(ln) <= _LABEL_LINE_MAX_CHARS and not _SENTENCE_PUNCT.search(ln)]
    prose = [ln for ln in shown if len(ln) >= _SENTENCE_LINE_MIN_CHARS
             and _SENTENCE_PUNCT.search(ln)]
    return (len(lines), len(labels) / len(lines), sum(map(len, lines)) / len(lines),
            len(prose))


_CJK_SENTENCE_ENDERS = "。！？；…"


def _has_a_sentence(text: str) -> bool:
    """这一页有没有一句人话 —— ASCII 的句读之外，中文的句末标点也算。

    `_body_line_shape` 的 `prose` 只认它自己那张 ASCII 标点表，于是「答案：四十二。」
    这类中文单句会被读成"没有句子的标签行"（实测：靶站那条短页控制组正是这个形状）。
    两支判据（`nav_only` 的短页一支、壳闸的小页一支）问的是同一个问题，所以答案只算一处。
    """
    if _body_line_shape(text)[3] > 0:
        return True
    return any(ch in text for ch in _CJK_SENTENCE_ENDERS)


def _substance_verdict(result: ResponseModel) -> tuple[str, str]:
    """这封回执拿回来的正文有没有可引用的实质 —— 判决与它背后的数一起出口。

    四个取值各有自己的作者：`collapsed` 是 `extraction_collapsed` 那条比例闸（它同时
    也是一条 error，这里只是把同一个事实放进 content_ok 的算术里）；`no_words` 是混淆闸
    已经点名的那种"整页是打包脚本"（判决由那句 error 映射而来，不重跑一次形状判定）；
    `nav_only` 是这一刀新加的、专管两道旧闸中间那一段的形状判据；`ok` 是"量过了，没什么
    可说的" —— 它要求正文**有得出形状的那么多行**，一两行的短标签既不能证明是菜单也不能
    证明是正文，那种行留空（不判），而**整页自己**不足壳闸那 200 字的判成 `nav_only`。

    只在**有正文可判**时判：非 HTML（PDF/JSON/图片）、HEAD、304 与那些"这一行根本没有
    正文"的 error（墙、壳、网络、robots）一律留空 —— 那些行有自己的说法，这里插一句就是
    第二个作者。这一道门用的是 `extractor.served_as_page` 而不是抽取那一侧的
    `is_markup_response`：**"这页没有话"是一句关于页面的断言**，它要正面证据（类型写着
    html）。缺 Content-Type 的 2 字节 API 回执与一个真空壳页面在三个形状读数上同值，
    分不开的时候判就是拿载荷当页面（实测见那颗函数自己的 docstring）。

    但 error 门**不挡**`extraction_collapsed` 与 `obfuscated_payload`：那两句说的就是正文
    本身，`substance` 是同一件事的另一个面。把它们一起挡掉曾让这两个取值在线上永远取不到
    （实测 2026-10-10：靶站 `/longaside` 带着 `extraction_collapsed` 回来，`substance`
    却是空的）。一个声明了却永远取不到的取值比少一个取值更糟 —— 读手册的人会以为判过了。
    """
    _BODY_IS_THE_ERROR = ("extraction_collapsed", "obfuscated_payload")
    _err = result.error or ""
    if (result.status == 0 or result.status >= 400
            or (_err and not _err.startswith(_BODY_IS_THE_ERROR))):
        return "", ""
    if _METHOD.get() == "HEAD" or result.status == 304:
        return "", ""
    if not served_as_page(result.content_type):
        return "", ""
    if not result.content or not any(c.strip() for c in result.content):
        return "", ""
    if _CSS_SELECTOR.get():
        # R06-002：css_selector 在场 = 回执量的是**用户点名的碎片**，不是整页。
        # 散文尺（nav_only）与塌陷尺（collapsed）都拿「整页可见字」当分母——量到
        # 点名的 11 行卡片标题上只会说「没有散文」（round-06 P01/P08 现场）。原则
        # 在册：extractor 侧的塌陷回收早就豁免了 css_selector（"narrowed 的调用方
        # 要的是更少"）。留空判 + basis 说清尺子为何不适用。
        return "", ("css_selector narrowed this answer to the requested fragment - "
                    "the whole-page prose and collapse rulers do not apply")
    if extraction_collapsed(result):
        visible = result.visible_text_chars
        got = extracted_chars(result)
        return "collapsed", (f"this page's own markup holds {visible:,} visible characters "
                             f"while the extractor returned {got:,}")
    body = "\n".join(c for c in result.content if isinstance(c, str))
    if _err.startswith("obfuscated_payload"):
        # 作者是混淆闸本身（`_detect_content_issue` 那句 error），这里只把它的判决映进
        # 实质这一维。**不重跑一次形状判定**：从前这一支写的是 `len(body) < 1_000`，而混淆
        # 闸判的正是**长**正文（实测靶站 `/shell/obfuscated`：正文 9,017 字、error 已经
        # 写着 `obfuscated_payload`，`substance` 却报 `ok`）—— 那一版把 `no_words` 做成了
        # 一个线上取不到的取值，与它声称要修的谎是同一族。
        return "no_words", (f"the body is {len(body):,} characters and dhole's shape check "
                            f"reads them as a packed payload, not prose")
    return body_substance(body, result.visible_text_chars, result.total_size_bytes or 0)


def body_substance(body: str, visible: int, page_bytes: int = 0) -> tuple[str, str]:
    """正文形状这一维的唯一作者：读 `web_read` 的抽取正文，也读 `web_crawl` 的一行。

    两个入口必须同判（这一族的旧账：同一个 URL 走 read 写着 `content_ok=false`、走
    crawl 写着 `true`，`_row_verdict` 的 docstring 就是为那笔账写的）。所以这里只收
    `(正文, 整页可见字)` 两个数，不收 ResponseModel —— 爬取的一行没有那封信，但它有
    同样这两个量。

    返回 `("ok"｜"nav_only"｜"", basis)`；`""` 是"薄到没有形状意见可给"（见
    `_substance_verdict` 那一处），`collapsed` / `no_words` 两支不在这里判 —— 它们各有
    自己的作者（比例闸与混淆闸），这里重复一遍就是第二个作者。
    """
    lines, label_share, mean_line, prose = _body_line_shape(body)
    if (lines >= _NAV_LINES_MIN and label_share >= _NAV_LABEL_SHARE_MIN
            and mean_line <= _NAV_MEAN_LINE_MAX and prose == 0):
        return "nav_only", (f"{lines} lines, {label_share:.0%} of them are short fragments "
                            f"without sentence punctuation, mean line {mean_line:.1f} "
                            f"characters (page holds {visible:,} "
                            f"visible characters in total)")
    if (1 <= lines < _NAV_LINES_MIN and not _has_a_sentence(body)
            and 0 < visible < _JS_SHELL_MIN_TEXT_CHARS):
        # 行数不够那一族的门，但**整页自己**就没写字：判据从"几行碎片"换成"这页一共
        # 多少可见字"，而那颗数是 3.3 刚上线的 `visible_text_chars`。靶站 `/shell` 实测
        # 正文 2 字、整页可见 6 字 —— 说它"判过了，有正文"是一句谎；它确实只有版面碎片，
        # 而出路（渲染，或跟进链接）与 `nav_only` 是同一扇。用壳闸自己那 200 字作帽，
        # 是为了让"页面几乎是空的"这一维只有一位作者。
        # 上限 `_NAV_LINES_MIN` 是**误报侧**的量出来的：第一版没写它，靶站 `/collections`
        # （15 行、12 条链接、整页可见 117 字）就被翻成 nav_only —— 那一页的内容正是那些
        # 链接。行数够一族门的情形归上面那一支判，这一支只管"整页一两个标签字"那种壳。
        #
        # "字少"不是证据（2026-10-23 两次量出来的）：一整页就是一个 34 字标题的小型 SPA
        # 挂载页（dsh.market：1,936 字节 / 34 可见字 / 没有一句人话）与一页真的只写了一行
        # 的短答案（"one page"）在正文形状上一模一样，分开它们的是**标记的重量**：前者
        # 字节数是可见字的 57 倍，后者是个位数倍。取不到字节数（爬取的一行）时退回旧判据
        # ——"每一行都是 ≤8 字的标签"——也就是这一支从前的那句话。
        markup_heavy = (page_bytes >= _JS_SHELL_MARKUP_RATIO * visible
                        if page_bytes else label_share == 1.0)
        if markup_heavy:
            return "nav_only", (f"the whole page holds {visible:,} visible "
                                f"characters and the body is {lines} line(s) with no "
                                f"sentence in them")
    if lines < _NAV_LINES_MIN:
        # 薄到没有形状意见可给：一两行既不能证明是菜单，也不能证明是正文 —— 留空，
        # 让读者知道这一维**没判**，而不是"判过且干净"。
        return "", ""
    return "ok", ""


def _schema_value_empty(value: Any) -> bool:
    """schema 字段没有作答时返回 True：''、[]、{}、0、None。

    用来区分「抽取器跑了、什么也没匹配到」（调用方必须看见的失败）与
    「某个可选字段本来就没有」（正常的部分结果）。

    递归，因为嵌套记录（G14）藏着同一种失败：一行里每个字段回来都是 ''，它其实是
    只含一个元素的列表，而把「非空列表」当成「匹配到了」会把一个什么也没答的页面
    报成成功。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list):
        return all(_schema_value_empty(v) for v in value)
    if isinstance(value, dict):
        return all(_schema_value_empty(v) for v in value.values())
    return value == 0


_RASTER_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp",
    "image/bmp", "image/tiff", "image/avif", "image/x-icon", "image/vnd.adobe.photoshop",
})


def _is_raster_image(result: ResponseModel) -> bool:
    """是位图图片（OCR 转写的那种）时返回 True，SVG 不算。

    区别在 body 自己的性质，不在 MIME 前缀：SVG **就是**它的标记，所以读出来是空的
    是一个该说出来的读取器问题；而 PNG 根本没有可读的文字层，OCR 已经说了它能看见
    什么。
    """
    ct = (result.content_type or "").lower().split(";")[0].strip()
    return ct in _RASTER_IMAGE_TYPES


#: `extraction_collapsed` 的出路，**一个作者、三个落点**（`error` 散文 /
#: `next_action` / `diagnosis.recover_with`）。
#:
#: `extraction_type` 那几枚分两族：markdown / text / article 都过**同一个抽取器**，
#: 而塌陷正是那个抽取器出的事 —— 在这三枚之间换一枚，量的还是同一件坏掉的东西。只有
#: `html`（以及 `schema=` 那条结构化路）绕开抽取器、把标记原样交回。
#:
#: 实测的形状（15 场景体测，场景 3）：一处静态文本站触发 collapsed，dhole 三处话里并列劝
#: 了 'html' 与 'text' 两枚；照 'text' 试的那一发仍是 527/1998 字、仍报同一条 collapsed、
#: 仍劝同样那两句 —— 一句建议把人送回他刚刚失败的那一步，而那一发确实花了 1.5 秒和一次
#: 往返。所以这里只推荐真能换档的那两枚，并说清其余几枚为什么**不算**换档。
#:
#: 长度是判据的一部分，不是排版：`server._inline_hole_reason` 给一行的 reason 有
#: `HOLE_REASON_CAP=400` 的帽，而那一行是爬取通道上唯一带着这句话的地方（没有别的字段兜
#: 着）。前缀那句实测 170 字，所以这一句必须留在 230 字以内，否则爬取回执里的出路被剪成
#: 半截。这条对齐由 `test_a_collapsed_page_offers_only_the_doors_that_change_the_reader.py` 量。
_COLLAPSE_RECOVERY = (
    "take extraction_type='html' (or schema=) for this URL - both skip the reader, "
    "while markdown / text / article all re-run the reader that just lost the text, "
    "and a browser hands that reader the same markup")

#: 抽取器给“这一页没有文字”写的占位符（OCR 跑过、什么也没认出来），连同 PDF 抽取
#: 自己加的信封行（OCR 头、页分隔线、兜底注记）—— 它们都不是文档的正文。
_PDF_EMPTY_PAGE = re.compile(
    r"\[\s*no text detected on this page\.?\s*\]"
    r"|OCR-extracted from scanned PDF[^\n]*"
    r"|--- Page \d+ ---"
    r"|OCR fallback used[^\n]*", re.I)


def pdf_text_is_absent(content) -> bool:
    """True when a PDF extraction produced no text of its own.

    Empty-page markers plus the extractor's own envelope lines (OCR header, page
    separators, the fallback note) are not text to quote. A mixed document (some
    pages with a text layer, some without) still has text, so this only fires
    when scaffolding and whitespace are all that came back. ``web_read`` and
    ``doc_parse`` share this one predicate: the same bytes must not read as a
    failure on one door and a success on the other.
    """
    text = "\n".join(c for c in (content or []) if isinstance(c, str))
    return not _PDF_EMPTY_PAGE.sub(" ", text).strip(" \t\r\n>-—")


def _detect_content_issue(result: ResponseModel) -> str:
    """检测响应里的内容质量问题，返回错误串或 ''。

    在最终结果上调用，给调用方一个「内容可能不可用」的信号，即使 HTTP 状态是 200。
    它把结果写进 error 字段，让 AI agent 不必自己解析正文就能发现失败。

    注：bot 校验检测只对 403/503 响应生效。状态 200、讲 Web 安全的正常文章正文里
    可能含有 "cloudflare"。
    """
    content_str = " ".join(result.content).lower().strip()

    # 304 没有 body，因为调用方本来就没要（G23）。下面每一项检查都在评判一个不
    # 存在的 body 的质量，而 js_shell_detected 把「变了吗？」这个问题的正确答案
    # 变成一次失败，agent 于是更用力地重试（实测那一发还起了浏览器）。
    #
    # 204/205 是同一形状的另一面：**源站**在状态行里就声明了「没有内容」。在
    # httpbin /status/204 上实测：空 body 落到空壳启发式里，升级到一个在 204 上
    # 只能产出 ERR_ABORTED 的浏览器，调用回来是 `all_tiers_failed`，声称没有收到
    # 任何 HTTP 响应 —— 而那份响应连状态码都带回来了。
    if result.status in (304, 204, 205):
        return ""

    # PDF 永远不是 JS 空壳。没有文字层的扫描/图片 PDF 是另一类失败
    # （"pdf_no_text"），不是 JavaScript 渲染问题。此前「大 body、少文字」的启发式
    # 把它误判成 JS 空壳，误导 agent 去修错误的根因。
    if "application/pdf" in (result.content_type or "").lower():
        if pdf_text_is_absent(result.content):
            return "pdf_no_text: scanned/image PDF with no extractable text layer (OCR found nothing)"
        # 有文字层的 PDF：继续往下走，status/error 检查仍然适用。

    # 图片的 OCR 结果不是网页，不许用描述网页的启发式来评判它。在
    # httpbin /image/png 上实测：OCR 跑了，转写出它唯一能找到的那个字形，而因为
    # 1 个字符就是「大 body、几乎没有文字」，响应被标成 `js_shell_detected`、
    # content_ok=false —— OCR 的答案是对的，关于它的判词是错的，诊断还接着声称浏览器
    # 档不可用，而就在这台机器上刚刚用过它渲染页面。
    # 一张没有文字的图片是关于图片本身的事实；在浏览器里渲染它是把它装框，不是转写
    # 它（见 `_never_escalate`）。SVG 被故意排除在外：它的标记就是内容，所以读出空
    # 来仍然是值得说出口的读取器问题。
    if _is_raster_image(result):
        return ""

    # 墙在通用 JS 空壳启发式**之前**检查。登录表单或验证码同样是「大 body、少文字」，
    # 所以空壳启发式从前会先认领这些页面，告诉调用方为 JS 渲染再抓一次，而真正的答案
    # 是「这页是墙，换个源」。
    if _is_auth_wall(result):
        return "auth_wall_detected: page is a login/sign-in wall, not content"

    if _is_bot_wall(result):
        return "bot_wall_detected: page is an anti-bot/CAPTCHA challenge, not content"

    # 在 JS 空壳启发式之前检查："Please wait..." / "Try reloading" 同样是短 body，
    # 把它们标成空壳等于让 agent 白烧一次浏览器升级，而渲染出来还是同一个错误视图。
    if _is_soft_failure(result):
        return "soft_failure_detected: page returned an error or placeholder view with HTTP 200, not content"

    # R13-001：narrowed 的调用方要的是更少 —— 与 `_substance_verdict` 的 R06-002 同一枚
    # 豁免。分母是整页可见字，点名取 h1 会被读成「读取器丢字」。恢复尺
    # `extraction_lost_text` 不在豁免内：它问的是值不值得再抽一次。
    if extraction_collapsed(result) and not _CSS_SELECTOR.get():
        # 抽取塌陷与 JS 壳在"正文很少"这一条上长得一模一样，而下一步正好相反：壳靠渲染
        # 补，塌陷是 dhole 自己的读取问题 —— 起浏览器是把同一份静态标记交给同一个抽取器
        # 再说一次。所以这一支排在 `_is_js_shell` **前面**，给的也不是"再抓一次"而是
        # `_COLLAPSE_RECOVERY` 那两枚真能换档的取值（这一族的建议只有一个作者：散文、
        # `next_action` 与 `diagnosis.recover_with` 都读那颗常量，见那里的说明）。
        #
        # 它同时改写了 page_type 的命运：`page_type_from_error` 只认 `js_shell_detected`
        # 那个前缀，所以这个新 token 不会把 `detect_page_type` 的结构化判定覆盖成壳 ——
        # 那正是"用一个猜测盖掉一个检查"的本体。
        visible = result.visible_text_chars
        return (f"extraction_collapsed: this page's own markup holds {visible} "
                f"characters of visible text while the extract returned "
                f"{extracted_chars(result)} - the words ARE in the HTML, "
                f"dhole's reader lost them. Recover: {_COLLAPSE_RECOVERY}")

    if _is_js_shell(result):
        return "js_shell_detected: page requires JavaScript rendering but fetcher returned placeholder"

    if _obfuscation_gate_applies(result):
        # 第二把尺：`_is_js_shell` 量的是「大 body + 几乎没字」，而实测那一枚壳把
        # 3,559 个字符**全都交回来了** —— 抽取是忠实的，页面本身就是打包过的脚本。
        # 每一道长度闸都放行，因为它通过的正是那些闸。这里量的不是多少个字，是
        # **这些字像不像人写的**（两条独立证据都要成立，见 `looks_obfuscated`）。
        finding = looks_obfuscated("\n".join(
            chunk for chunk in (result.content or []) if isinstance(chunk, str)))
        if finding:
            return (f"obfuscated_payload: the body is a packed script challenge, not "
                    f"prose ({'; '.join(finding['signals'])}; mean token "
                    f"{finding['mean_token_len']} chars across {finding['chars']} "
                    "extracted) - the extractor was faithful, this page has no words "
                    "in it")

    if any(signal in content_str for signal in _GEO_REDIRECT_SIGNALS):
        return "geo_redirect_detected: page returned region/country selector instead of content"

    # 只在错误状态码上检查 bot 校验。正文提到 "cloudflare" 的正常页面
    # （状态 200）不是 bot 校验页。
    if result.status in (403, 503) and _is_cloudflare_from_response(result):
        return "bot_challenge_detected: page returned bot challenge/verification page"

    # 通用规则：任何 4xx/5xx 都是错误，即使服务器把一张 HTML 错误页当作正文交回。
    # 没有这一条，404 页会被当成真正的内容（error="" -> agent 信了它）。把 error
    # 字段设上，agent、缓存与档案兜底才会都把它看成失败。
    if result.status >= 400:
        return status_error_sentence(result.status)
    if result.status == 0:
        # 保留原始错误内容（来自 stealthy_fetch / HTTP 层），下游
        # classify_network_error() 才能认出失败类型。
        existing = result.error or ""
        if existing and not existing.startswith("network_error"):
            return f"network_error: {existing[:150]}"
        return "network_error: request failed (DNS/timeout/connection refused)"

    return ""


def _action_receipts(result: ResponseModel) -> list:
    """这一发自己的动作回执，无论它此刻住在哪一格。

    两个住处，一件事：`metadata.actions` 是**上线以后**的样子（由
    `_report_action_outcomes` 在信封写好之后填），`_action_outcomes` 是浏览器层跑完链
    随响应交回来的原件（同一批 dict）。判决必须读得到后者，否则"这一发办成了没有"是由
    一个还不知道链跑过的人写的（round 01, R01-028 —— P4/P5 两轮真调用复验量出来的：
    降级逻辑写对了，却跑在信息到场之前）。
    """
    wire = (result.metadata or {}).get("actions")
    if isinstance(wire, list) and wire:
        return [one for one in wire if isinstance(one, dict)]
    early = getattr(result, "_action_outcomes", None)
    if isinstance(early, list):
        return [one for one in early if isinstance(one, dict)]
    return []


def _stamp_chain_receipts(result: ResponseModel, page_action: Any) -> None:
    """浏览器档跑完链之后、写判决之前，把回执随这份响应同行。

    接线为什么在 `bulk_stealthy_fetch` 的结果循环里而不是在 `server._force_fetch`：
    插桩实测（review/.runtime/p4/spy_r01_028.py）显示 `stealthy_fetch` 交回来的响应
    **已经**过了一遍 `_annotate_quality`，在那里贴只会晚于判决 —— 第一版就是这么写的，
    单测绿而真调用照旧。同一批 dict，不是第二份事实；PrivateAttr 不上线也不进缓存信封。
    """
    if page_action is None:
        return
    result._action_outcomes = list(getattr(page_action, "outcomes", None) or [])


def _chain_ran_ok(result: ResponseModel) -> bool:
    """这一发自己的动作链跑到底了吗？

    `ok=false` 但带着一个**故意**的停止键（`pending_approval` / `not_submitted` /
    `cut_by_budget`）的回执不是失败 —— 那是提交闸在做它生来要做的事，前面的步骤
    确实跑了。

    之所以需要它，是因为页面质量的判词与动作链回答的是两个不同的问题，而顶层的
    `error` 格子只能放一句话（round 01, R01-028）：一条填完表单、停在确认闸前的链
    回来是 `error="auth_wall_detected…"` + `content_ok=false`，L1 读者把它读成
    「什么都没成」，而得到的建议是重跑那条链，也就是把表单填两遍。
    """
    receipts = _action_receipts(result)
    if not receipts:
        return False
    _stops = ("pending_approval", "not_submitted", "cut_by_budget")
    for one in receipts:
        if one.get("ok") is True:
            continue
        if not any(one.get(k) for k in _stops):
            return False
    return True


def _quality_way_out(result: ResponseModel) -> str:
    """正文判决失败时总要欠一个下一步（R01-028, P08：那一发被判失败，而
    `next_action=""` —— 读者只学到一句「不行」）。"""
    fired = (result.error
             or (result.metadata or {}).get("content_issue")
             or "the content check found no usable body on this page")
    return (f"content_ok=false because of this verdict: {fired}. "
            "The page was fetched; what failed is the extraction of a readable body. "
            "Untried levers on THIS url: extraction_type='html' (the raw structure), "
            "css_selector='…' (name the block), focus='' (the whole page without "
            "selection), offset= (the next slice). Switching source is the last move, "
            "not the first — this page answered.")


def _annotate_quality(result: ResponseModel) -> ResponseModel:
    """检查内容质量，发现问题就写 error 字段。返回同一个 result。

    HEAD 响应**按定义**没有 body（RFC 9110：对 HEAD 的响应就是对 GET 的响应去掉
    内容），所以每一条「body 是空的 / 看起来像空壳」的启发式都会在一个完全正常的
    探测上触发。HEAD 的答案是状态、长度和类型 —— 这三样都有值。
    """
    if not result.error and _METHOD.get() != "HEAD":
        # 一次 `save_to` 的回执不是一页，别拿页面判词去读它（R4）。它交出去的是
        # `saved 62,914,560 bytes to D:\...`，于是"大字少"的每一条启发式都会命中它 ——
        # 实测 60MB 的 .bin 被读成 `js_shell_detected`，一句话把一个成功的下载说成
        # "这页需要 JS 渲染"。判据是**回执自己点的名**，不是 `_SAVE_TO` 这颗作用域变量：
        # 爬行/批读也在同一层作用域里，而它们读到的每一页都该照常接受判读。
        if "saved_to" in (result.metadata or {}):
            return result
        issue = _detect_content_issue(result)
        if issue:
            if _chain_ran_ok(result):
                # 链的问题由 `metadata.actions` 回答；这个格子描述的是**页面**。
                # 把页面的判词放在这里会让这次调用本身看起来失败了，而建立在它上面的
                # 建议会是「再跑一遍」（R01-028）。发现仍然必须送达读者：
                # `metadata.content_issue` 让它保持可寻址，note 桶把它放进 `summary`，
                # 不让任何东西被悄悄丢掉。
                #
                # 幂等靠记录在案的判词，不靠计数器：一条浏览器页面在真实路径上会被
                # 标注两次（一次在 `bulk_stealthy_fetch`，一次在 `_finalize_result`），
                # 而同一句话在一个 summary 行里说两遍，读起来就是两条发现
                # （实测，R01-028 P5）。
                already = (result.metadata or {}).get("content_issue")
                if already == issue:
                    return result
                try:
                    if isinstance(result.metadata, dict):
                        result.metadata["content_issue"] = issue
                except Exception:
                    pass
                _note_text(issue)
            else:
                result.error = issue
    return result


def _is_cacheable(result: ResponseModel) -> bool:
    """只有干净、可用、值得缓存的内容才返回 True。

    排除：错误状态码（4xx/5xx）、JS 空壳、bot 校验页、地区重定向、all_tiers_failed，
    以及空白/空的抽取结果。缓存其中任何一个，都会在整个 TTL 里从缓存里交出坏页面。

    动作链站过的页面根本到不了这里：`web_act` 在门口就强制 ``cache_ttl=0``
    （server.py，"an action's content exists only after the actions"），所以这道闸
    不是守那条线的那一道 —— 这是在把链的页面判词从 `error` 里降级时核对过的
    （R01-028），因为降级拆掉了一层**意外**保护：那份 body 从前在这里被拒，只是
    因为墙的判词还坐在 `error` 里。
    """
    if not (0 < result.status < 400):
        return False
    if result.error:
        return False
    return bool(result.content) and any(c.strip() for c in result.content)


# HTTP 档直接交给档案兜底的状态码：页面已经消失或被合法移除，浏览器档明确不试
# （浏览器拿到的也是同样的 404/410/451）。写成模块常量而不是内联元组，免得与
# `_should_try_archive()` 失去同步：410 曾经坐在元组里，而那道闸拒绝它，让这个分支
# 变成不可达（KB-9）。
_ARCHIVE_FALLBACK_STATUSES = (404, 410, 451)


def _is_api_error_body(result: ResponseModel) -> bool:
    """4xx/5xx 带着机器可读的 body（JSON/XML）时返回 True。

    只针对**有** body 的错误状态码：空 body 意味着快照真的是唯一可用的内容，
    那条兜底保留。
    """
    if result.status < 400:
        return False
    if not any((c or "").strip() for c in result.content or []):
        return False
    return "json" in (result.content_type or "").lower() or \
        "xml" in (result.content_type or "").lower()


def _should_try_archive(result: ResponseModel) -> bool:
    """Internet Archive 可能持有这个 URL 的可用快照时返回 True。

    在硬封锁（404/410/451）、网络失败（status 0）、服务器错误（5xx）、bot 校验与
    all_tiers_failed 上触发。auth_required 上**不**触发（档案站也不会有登录门后的
    内容）。
    """
    if result.status == 304:
        # 真站答对了（G23）：「没变，没有 body」。快照会把一个真答案换成更旧的。
        return False
    err = (result.error or "").lower()
    if _METHOD.get() != "GET":
        # 快照是对这个 URL 在某个过去日期的 GET。它回答不了「我的 POST 成功了吗」，
        # 也没看见站点对我们刚发出的 body 的反应 —— 在这里替换掉，报的就是另一个请求。
        return False
    if _is_api_error_body(result):
        # API 自己的错误载荷就是答案；Wayback Machine 不索引 API 响应，所以快照
        # 只能是另一个时间的陈旧 HTML 页。实测：一个 body 为
        # '{"error":"item 42 not found"}' 的 404，在 archive.org 手里有这个 URL 的
        # 任何东西的那一刻，回来变成 2019 年的快照、error=""、status 200 ——
        # 服务器真正的答案被替换了，而且没人事先说明。
        # 一个 HTML 的 "404 Not Found" body 不一样：一个**页面**消失后的快照，往往
        # 比错误视图更有用，所以那种情况仍然兜底。
        return False
    if err.startswith("proxy_unreachable"):
        # 请求根本没离开本进程，所以关于站点我们什么也**没学到** —— 它可能完全正常。
        # 在这里换成 Wayback 快照，等于回答一个没人问过的问题（调用方还会把带日期的
        # 快照当成实时页面），而且要在调用方真正需要修的失败（代理）之上再花 10–30 秒。
        return False
    if result.status in (404, 410):   # 页面消失/被删，或永久消失
        return True
    if result.status == 451:      # 法律封锁
        return True
    if result.status == 0:        # 网络/DNS/超时 —— 档案站可能有
        return True
    if result.status >= 500:      # 服务器错误
        return True
    if result.status in (403, 503) and "bot_challenge" in err:
        return True
    if err.startswith("all_tiers_failed"):
        return True
    return False


def _format_size(n: int) -> str:
    """summary 行用的人类可读字节大小。"""
    if not n:
        return "0B"
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MB"
    if n >= 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n}B"


# 列表页的「首要目标」是它**指向**的那些页面。下面这些不是。
_LIST_TARGET_SKIP_RE = re.compile(
    r"(^|/)(login|signin|sign-in|signup|register|account|profile|subscribe|support|"
    r"contact|about|privacy|terms|cookie|cart|checkout|wishlist|search|rss|feed|"
    r"sitemap)([/?#]|$)",
    re.IGNORECASE,
)
_LIST_TARGET_ASSET_RE = re.compile(
    r"\.(jpg|jpeg|png|gif|webp|svg|ico|css|js|woff2?)(\?|#|$)", re.IGNORECASE)


def _best_list_targets(citations: list, page_url: str, limit: int = 3) -> list[str]:
    """从列表页里挑出值得跟进的链接，供 next_action 使用。

    theverge.com/news 上实测：提示写着 "Top targets: /, /auth/login, /subscribe" ——
    按 DOM 顺序的前三条引用，正是站点的页头。调用方要的文章链接在下面几行，
    列出这些页面外框只会把 agent 送去一堵登录墙。
    """
    try:
        base_host = (urlparse(page_url).netloc or "").lower()
    except Exception:
        base_host = ""
    scored: list[tuple[int, int, str]] = []
    for i, c in enumerate(citations or []):
        u = (c.get("url") or "").strip() if isinstance(c, dict) else ""
        if not u.startswith("http"):
            continue
        try:
            parsed = urlparse(u)
        except Exception:
            continue
        path = parsed.path or ""
        if _LIST_TARGET_SKIP_RE.search(path) or _LIST_TARGET_ASSET_RE.search(path):
            continue
        if path in ("", "/") or u.rstrip("/") == page_url.rstrip("/"):
            continue  # 从列表页出发，首页不是目标
        score = 0
        if path.count("/") >= 2:
            score += 2          # 深路径是文章；浅路径是栏目
        if (c.get("text") or "").strip():
            score += 1          # 带锚文本的链接描述了它的目的地
        if base_host and (parsed.netloc or "").lower() == base_host:
            score += 1
        scored.append((-score, i, u))
    scored.sort()
    return [u for _score, _i, u in scored[:limit]]


#: 只有"一次 HTTP 应答都没拿到"的这一族才换成那句本地诊断。`dns_failure` 也在里面：
#: 私网目标的名字是 localhost 时，"域名不存在"同样不是调用方要做的事。
#: `private_target_blocked` **不在**里面：那一支一根线都没拨，"本地服务起了吗"是对
#: 一扇关着的门说话 —— 它自己的 hint 点名的是 `allow_private`。
_NO_ANSWER_CATEGORIES = ("connection_refused", "connection_reset", "timeout",
                         "dns_failure", "dns_unresolvable")


def _local_target_no_answer(category: str, url: str) -> str:
    """回环 / 私网目标没拿到任何 HTTP 应答时的那句出路，否则空串。

    通用那几句（"站点可能挂了、换个源"、"换个说法重试"）在这里都不是答案：调用方问
    的是自己机器上某一个端口，而它没在听。TCP 预检定论的两条支路早就这么说（实测
    回执 `this private target never answered a direct TCP probe…`），HTTP 档自己拨了
    却被拒/没答的那一条却没有 —— 同一件事在两条路上两种说法（D-08b，本轮实测：同一
    个死端口两次跑出两种回执）。句子本身在 `errors` 里只有一个作者。
    """
    if category in _NO_ANSWER_CATEGORIES and target_is_private(url or ""):
        from dhole_mcp.errors import PRIVATE_TARGET_NO_ANSWER
        return PRIVATE_TARGET_NO_ANSWER
    return ""


def _replay_age_seconds(fetched_at: str) -> float | None:
    """这封回执读到的正文是多久**抓**的；时间戳读不出来就返回 None（不说假话）。

    负值钳到 0：本机时钟与写缓存那一刻的时钟可能差几秒，而"3 分钟前抓的"说成
    "-3 分钟前"是一次毫无意义的炫技。
    """
    if not fetched_at:
        return None
    try:
        stamp = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - stamp).total_seconds())


def _network_clause(result: ResponseModel) -> str:
    """这一发自己记下的网络事实 —— 只在**有东西没成**时说话。

    js_shell / nav_only 的每一句话都在猜"为什么渲染出来还是空的"，而答案常常就在
    这一发已经量到的账上：数据接口 403、被护栏拦下。把它说出来，下一步才从"再试
    一次"变成"带 cookie 重来"或"这条路走不通"。

    只在 `content_ok` 为假时由 `_agent_hints` 追加：正文已经拿到的那一发不需要一份
    "某个图标 404"的账 —— 那是噪声，不是诊断。
    """
    net = getattr(result, "network", None) or {}
    failed = [f for f in (net.get("failed") or []) if isinstance(f, dict)]
    guard = [str(h) for h in (net.get("blocked_by_guard") or []) if h]
    if not failed and not guard:
        return ""
    total = int(net.get("requests") or 0)
    bits: list[str] = []
    if failed:
        named = "; ".join(
            f"{f.get('type') or 'request'} {f.get('status')} {f.get('url')}"
            for f in failed[:2])
        more = f" (+{len(failed) - 2} more)" if len(failed) > 2 else ""
        bits.append(f"{len(failed)} of {total} requests failed - {named}{more}")
    if guard:
        bits.append("dhole refused to load " + ", ".join(guard)
                    + " (an internal address, or outside the host fence): that is a "
                      "security boundary, not a site error - do not route around it")
    return "This call's own network log says why: " + "; ".join(bits) + "."


def _agent_hints(result: ResponseModel) -> tuple[str, str, bool]:
    """为一次定稿的抓取结果构造 (summary, next_action, content_ok)。

    summary       —— 一行状态，agent 扫一眼就能按模式匹配。
    next_action   —— 显而易见的下一发调用，如果有的话（翻页 / 绕过 robots /
                     换源）。无事可做时为空。
    content_ok    —— 只有真拿到内容时才为 True（status<400、无 error、不是 JS
                     空壳 / 登录墙 / 空页）。agent 信任内容之前应先看这一位。
    """
    has_content = bool(result.content) and any(c.strip() for c in result.content)
    # HEAD 按定义没有 body，所以对一次探测而言「可用」就是状态行本身：
    # 200 + Content-Length + Content-Type 已经回答了「这个 URL 通不通、有多大」。
    # 这里要求必须有内容，会把每一次成功的探测都报成失败（然后建议重试）。
    probe_ok = _METHOD.get() == "HEAD"
    # 304 回答「变了吗」，**按设计**没有 body（G23）。它的全部内容就是状态行，
    # 否则每一条以 body 为形状的检查都会把这份「站点正确作答」的响应读成失败。
    unchanged = result.status == 304
    # PDF 带着抽取器给出的、基于质量的 content_ok 判决（CID 乱码 / 损坏 -> 即使
    # HTTP 200 也是 False）。要尊重它，别让 status 200 + 有内容把损坏掩盖掉
    # （P3 那个 bug）。
    if (result.quality_score or 0) > 0:
        content_ok = result.content_ok and result.status > 0 and not result.error and has_content
    else:
        content_ok = (
            result.status > 0 and result.status < 400
            and not result.error
            and (has_content or probe_ok or unchanged)
        )
    # 3.4 · 实质判决。它必须在上面那段算完之后判：那时 `error` 已经是最终形态
    # （`_detect_content_issue` 的塌陷/壳/混淆 token 都在里面），判决才不会与 error 那条
    # 说法各写各的。判决顺手盖在信封上（`substance` / `substance_basis`），因为调用方
    # 需要能复核那个数，而不只是接受一个布尔。
    substance, substance_basis = _substance_verdict(result)
    result.substance = substance
    result.substance_basis = substance_basis
    if substance in _NOT_SUBSTANTIVE:
        content_ok = False
    if result.page_type == "redirect":
        # 客户端跳转页的正文是一段脚本，不是这一页；page_type 已经判过，判决位不能
        # 再说「可引用」（R05-023：同一封里 redirect 与 content_ok=true 互相否决）。
        content_ok = False

    size = result.total_extracted_chars or sum(len(c) for c in result.content)
    window = sum(len(c) for c in result.content)
    parts: list[str] = []
    if result.status == 0:
        # 本地文件失败与网络无关；说成 "network error" 会让人为一个坏路径去查代理。
        # 安全闸是同一种错误反过来：CDP 首次 attach 被拒时回来是 "network error"，
        # 而那时一个请求都还没尝试过（2026-10-05）—— 读者于是去查代理，而不是问那个
        # 唯一能推动问题的东西。
        if (result.error or "").startswith("cdp_authorization_required"):
            parts.append("awaiting approval")
        elif (result.error or "").startswith("cdp_no_logged_in_context"):
            # 批次 8：attach 连上了却没有登录上下文可借，也是"一次请求都没发"。
            # 说成 network error 会把人送去查代理，而这里没什么可查——那一窗口的
            # profile 里没有可复用的上下文。
            parts.append("no logged-in context")
        elif (result.error or "").startswith("cdp_endpoint_unreachable"):
            # 批次 9：端点这次连话都没答。说成 network error 仍然不对 —— 没有
            # 请求在路上，也没有代理可查；把 timeout 调大更不会有用。
            parts.append("no cdp endpoint")
        elif (result.error or "").startswith("browser_busy"):
            # 批次 11：要一台可见的而这个 profile 上已经有一台在跑。没有任何请求发出，
            # 而"网络错误"会把人送去查代理，这里要查的是屏幕上有没有窗口。
            parts.append("browser in use")
        elif _is_over_budget(result):
            # 预算用完不是网络故障（D-35b 实测：一条已经跑完 `fill` 的动作链被 25 秒
            # 预算掐断时，summary 写的是 "network error · 0B markdown"）。站点可能一路
            # 都在应答，只是这次调用给它的时间用完了 —— 该拧的旋钮是 timeout，不是代理。
            parts.append("out of time")
        else:
            parts.append("local error" if result.fetcher_used == "parse" else "network error")
    else:
        parts.append(f"{int(result.status)} {'OK' if result.status < 400 else 'ERR'}")
    if probe_ok:
        # 对一次 HEAD 说 "0B markdown"，是把「没有」描述成一种格式。探测时真正
        # 要紧的数字是声明的长度。
        parts.append(f"{_format_size(result.total_size_bytes)} head probe (no body by definition)")
    elif unchanged:
        parts.append("unchanged since your validators (no body by design)")
    else:
        type_label = result.extracted_type or 'markdown'
        # E3（R01-011）：这一行回答的是"这一发给了我多少"，不是"这一页总共多长"。
        # 窗口短于总量时把两个数并排说 —— 只报总量会被读成"整页已到手"，于是
        # 调用方不再续读。`[No more content.]` 是替身不是正文，不许被算成一窗。
        if (window and size > window and result.status < 400
                and not result.error
                and result.content != ["[No more content.]"]):
            parts.append(f"{_format_size(window)} of {_format_size(size)} {type_label}")
        else:
            parts.append(f"{_format_size(size)} {type_label}")
    if result.fetcher_used:
        parts.append(result.fetcher_used)
    if (result.source or "").lower() == "archive.org":
        # 降级要活在**结论**里，不是活在 metadata 第 14 行。这一发读的不是那个 URL，
        # 是 Internet Archive 某一天的副本：`source` / `archived_at` / `escalation_path`
        # 三个字段本来各说一段，而调用方扫的是 summary 那一行 —— 一行里看不见，就等于
        # 没说过（实测一条 200 OK · 12.4KB markdown · http 的回执，正文其实来自 2023 年
        # 的快照）。日期与整条路径都拼进来，因为"哪一年"与"怎么走到那儿的"是两个问题：
        # 前者决定引用价值，后者决定这次要不要换源重问。
        when = f", {result.archived_at}" if result.archived_at else ", date unknown"
        route = f" (via {result.escalation_path})" if result.escalation_path else ""
        parts.append(f"archived copy{when}{route}")
    if result.cached:
        # 一个 "cached" 说的是机制，不说时刻：一次 400 秒前的回放与一次刚写进去的回放
        # 在那一行上同形，而调用方读的正是那一行。这里的年龄是从 `fetched_at` 到此刻
        # 的**回放年龄**，不是 `content_age_days` —— 那颗是"这一页发布于多久之前"，
        # 两个问句共用一个词就会开始互相冒充（R4 本体要修的正是这种冒充）。
        replay = _replay_age_seconds(result.fetched_at)
        if replay is None:
            parts.append("cached")
        elif replay < 14400:
            parts.append(f"cached copy, fetched {replay / 60:.1f} min ago")
        else:
            parts.append(f"cached copy, fetched {replay / 3600:.1f} h ago")
    if result.is_truncated:
        parts.append("truncated")
    if substance in _NOT_SUBSTANTIVE:
        # 降级要活在**结论行**里：一个 39 字的"200 OK"与一篇 4 万字的正文在同一行上只差
        # 一个数字，而调用方扫的就是这一行。
        parts.append({"nav_only": "no prose (menu fragments only)",
                      "collapsed": "words lost by the extractor",
                      "no_words": "no words in the body"}[substance])
    summary = " · ".join(parts)

    next_action = ""
    err = result.error or ""
    # 登录墙、反爬校验与 JS 空壳是同一个错误换了一身衣服：在 github 的重定向到登录页
    # 上实测，截断提示排在**前面**，墙自己的建议排在后面，两句拼在一个串里，中间
    # 没有分隔。对着一张登录表单翻页到不了内容，所以那句在那里不只是顺序错了 ——
    # 它是错的。
    _NOT_A_DOCUMENT = ("auth_wall_detected", "bot_wall_detected",
                       "js_shell_detected", "obfuscated_payload")
    if substance == "nav_only":
        # 出路必须是**真换管路**的那两枚：壳页靠渲染，目录页靠跟进链接 —— 而
        # "再抓一次同样的"只是把同一堆菜单字再拿一遍（1.4 那一刀定的口径）。
        # 渲染那一枚照旧要过门：渲染帮不上（数据文档、这一档从来不渲染这一族）或**已经
        # 渲染过而答案就是这堆碎片**时还劝人去那一档，正是这一族守卫钉着的那句谎。
        _nav_can_render = (not _browser_cannot_help(result, result.url)
                           and not _render_tier_was_spent(result))
        if _nav_can_render:
            next_action = (
                "the body returned is short menu-style fragments, not prose: this page's "
                "content is rendered by JavaScript or lives behind those links. Two doors: "
                "re-read with force_fetcher='stealthy' (which renders), or follow the links. "
                "Do not cite this body as the article.")
        else:
            next_action = (
                "the body returned is short menu-style fragments, not prose, and rendering "
                "will not change that answer (this URL was already rendered, or this tier is "
                "not one a browser helps). The only door left is the links themselves: "
                "follow them or take a different source. Do not cite this body as the article.")
    elif result.page_type == "redirect":
        # 页面自己写着跳转；正文只是脚本。HTTP 档不会跟随，浏览器档会 —— P3 实测
        # 浏览器档落到目标页并返回真实内容（R05-023）。与别处同一条门：这台机器
        # 起不来的浏览器不许被点名。
        _redirect_can_render = (
            not _browser_cannot_help(result, result.url)
            and not _never_escalate(result, result.url))
        if _redirect_can_render:
            next_action = (
                "this URL is a client-side redirect: the body is the redirect script, "
                "not the page. Re-fetch with force_fetcher='stealthy' (a browser follows "
                "it), or open the target the script names directly. Do not cite this body.")
        else:
            next_action = (
                "this URL is a client-side redirect: the body is the redirect script, "
                "not the page. Open the target the script names directly - the browser "
                "tier is unavailable here, so it cannot follow it for you. "
                "Do not cite this body.")
    # 404 页也可能长到被截断，而截断提示从前在顺序上会赢：一个 Wikipedia 404 被
    # 答以 "offset=... to continue paginating"，让 agent 在一个错误页上翻页。状态码
    # 是更具体的事实，所以错误状态码落到下面它们自己的分支。
    elif (result.is_truncated and result.next_offset and result.status < 400
            and not err.startswith(_NOT_A_DOCUMENT)
            and not err.startswith("extraction_collapsed")):
        # 塌陷时更具体的事实是「抽取器丢字」，不是「还有下一页」：`diagnosis` 已经在
        # 说 `extraction_type='html'`，翻页提示不许在同一封里改口（R04-016b）。
        _repeat = _focus_repeat_warning(
            f"offset={result.next_offset} continues THIS filtered text, or pass focus='' "
            "to get the whole page back")
        if _repeat:
            next_action = f"page truncated. {_repeat}."
        else:
            next_action = (
                f"page truncated. Use focus='query' to extract only relevant blocks, "
                f"or offset={result.next_offset} to continue paginating")
    elif unchanged:
        next_action = ("304 not modified: the origin says this URL still serves the "
                       "version dhole already holds, so no body was transferred - that "
                       "is a successful answer, do NOT report it as a failed fetch. "
                       "Revalidation is asked internally, never by a caller; to read the "
                       "body again, make a plain fetch (nothing to compare against).")
    elif err.startswith(("js_shell_detected", "bot_challenge_detected",
                         "obfuscated_payload")):
        _wall = ("bot challenge page" if err.startswith("bot_challenge_detected")
                 else "JS shell" if err.startswith("js_shell_detected")
                 # 说清是哪一形：它不是"没字"，是"字全是载荷"，而这两形的下一步其实
                 # 相同（渲染），所以共用上面那三档建议；不同的是调用方读到的事实。
                 else "packed script payload")
        if _browser_cannot_help(result, result.url):
            if _never_escalate(result, result.url):
                # 建议不能承诺管线自己拒绝做的事。数据文档（.xml/.rss/.atom/.json，
                # 或任何非 GET）是故意不交给浏览器的，所以 "re-fetch auto-escalates"
                # 把调用方送回去重抓一次、答案一模一样。在
                # docs.vllm.ai/sitemap.xml 上实测：新请求撞上 Cloudflare 429 校验，
                # dhole 正确地拒绝渲染它，而提示还在告诉调用方「下次浏览器会接住」。
                # 再问一遍不是计划；点名拒绝它的是源站才是。
                next_action = (
                    f"the origin answered this {'request' if _METHOD.get() != 'GET' else 'URL'} "
                    f"with a {_wall}, but dhole will NOT render it in a browser - a "
                    "render of a data document returns the browser's own viewer markup "
                    "instead of the document (and a render cannot resend a write). "
                    "Re-fetching unchanged gets the same answer: wait and retry, or "
                    "switch source.")
            else:
                # 同一个规矩的另一半：这一次不是"不肯升"，是"升不了"。2026-10-05 那次
                # 浏览器层整体起不来，而每一条壳页的 next_action 都在说"再抓一次会自动
                # 升级到 stealthy"—— 那是调用方当时唯一拿到手的建议，命中率是 0。
                # 事实写在 `escalation_path` 上（跳过 stealthy 之前就写好了），读它就行。
                next_action = (
                    f"page is a {_wall}, and the browser tier that would render it is "
                    "not available on this machine - the auto-escalation this usually "
                    "promises was already attempted and skipped. `dhole --doctor` names "
                    "the state and the directory it searched; meanwhile this HTTP body "
                    "is what there is (extraction_type='html' shows it as sent).")
        elif _render_tier_was_spent(result):
            # 门里第三件事：那一档不是起不来、也不是不肯升，是**已经升过了**。这一支
            # 从前不存在，所以浏览器交回一张壳页时，建议还在叫调用方去叫那一档 ——
            # 而那一次重试除了把预算烧到超时（实测 31.5s）不会带回别的东西。
            next_action = (
                f"page is a {_wall} that the browser tier ALREADY rendered and "
                f"returned as-is - `fetcher_used='{result.fetcher_used}'`. No untried "
                "tier is left on this URL, so re-fetching (with or without "
                "force_fetcher) is not a plan. What the site sent is readable with "
                "extraction_type='html'; otherwise switch source, or leave this one "
                "to a human.")
        else:
            next_action = (f"page is a {_wall}; re-fetch auto-escalates to the "
                           "stealthy browser")
    elif err.startswith("auth_wall_detected"):
        # 从错误链到达，不是下面那个 page_type 分支：墙会设置 error，而 error 强制
        # content_ok 为假，那个分支只在 content_ok 为真时才跑 —— 所以它永远没机会
        # 对一堵墙给建议。
        next_action = ("page is a login/sign-in wall, not content - do NOT cite it. "
                       + _auth_wall_step(result.url))
    elif err.startswith("bot_wall_detected"):
        if _render_tier_was_spent(result):
            _wall_step = ("the browser tier already rendered this page and the challenge "
                          "is what it got back - there is no tier left to escalate to, "
                          "so another re-fetch is not an untried option. "
                          "Switch source, or ask for a copy that does not gate.")
        elif _escalation_skipped_by_kind(result, result.url):
            _wall_step = ("dhole does not send .pdf / image / data-document responses to "
                          "the browser tier (a render would replace the bytes with a "
                          "viewer), so there is no stealthy retry on this URL - switch "
                          "source, or download the file yourself and parse it.")
        elif _browser_cannot_help(result, result.url):
            _wall_step = ("The browser tier that would render it is unavailable on this "
                          "machine (the skip is recorded in error/escalation_path), so "
                          "force_fetcher='stealthy' cannot help this time - switch source "
                          "or `dhole --doctor` to see why.")
        else:
            _wall_step = ("Retry once with force_fetcher='stealthy' (renders the "
                          "challenge), otherwise switch source")
        next_action = ("page is an anti-bot/CAPTCHA challenge, not content - do NOT cite it. "
                       + _wall_step)
    elif err.startswith("robots_disallowed"):
        # 预检拒绝，不是站点故障：这么说，别用泛泛的 "retry / switch source" 措辞，
        # 并点名文档里写的覆盖开关。
        next_action = (
            "robots.txt disallows this URL for dhole, so nothing was fetched - do "
            "NOT cite it. Switch source, or re-call with ignore_robots=true if you "
            f"have the site's permission ({ENV_IGNORE_ROBOTS}=1 disables the check "
            "process-wide)."
        )
    elif err.startswith("proxy_unreachable"):
        # 失败发生在请求**之前**，所以 "retry / raise timeout" 是错的建议 —— 而对
        # 一个死掉的代理，泛化的预算耗尽消息从前说的正是这句（G21）。
        next_action = (
            "the proxy did not answer, so no request was sent - do NOT retry the "
            "same way. Check the proxy host/port and that the proxy is running, or "
            "forget it (cred_defaults unset=['proxy']) to fetch directly. Raising "
            "timeout will not help: the failure happens before the request."
        )
    elif err.startswith("cdp_authorization_required"):
        # 一道等批准的闸，不是站点故障：能做的只有"问过用户，再带 confirm 重发同一条"。
        # 通用的 "换个来源或调参数重试" 在这里是错的 —— 没有任何参数能放宽它，而把
        # cdp_url= 删掉回答的是另一个问题（一次不带登录态的抓取）。
        next_action = (
            "Ask the user, then re-call the SAME url with confirm=true - that "
            "authorizes the host this call names and nothing else. No parameter "
            "widens the fence, and dropping cdp_url= answers a different question "
            "(an unauthenticated fetch of a page you wanted read logged-in)."
        )
    elif err.startswith("cdp_host_not_allowed"):
        next_action = (
            "the host fence refused a site that was never authorized - no parameter "
            "widens it. Re-call with cdp_url= plus confirm=true to authorize the "
            "host THIS call names, or revoke one you no longer want with "
            "sys_state(clear=['cdp:host=<name>'])."
        )
    elif err.startswith("cdp_no_logged_in_context"):
        # 批次 8：这一态从前不报错——它退回一个匿名 context，200、真内容、没有
        # cookie，而"没有"只表现为一个缺席的字段。现在它拒答，所以这里的任务是把
        # 两条真的出路说清楚，别让读者把空回执当成"这页是空的"。
        next_action = (
            "the attached browser exposed no logged-in context, so NOTHING was "
            "fetched - an empty result here is not an empty page. Re-call the SAME "
            "url once (a just-started endpoint sometimes reports its contexts late); "
            "if it repeats, that cdp_url is not the window holding your logins - "
            "start the signed-in browser with --user-data-dir pointing at that "
            "profile and --remote-debugging-port at this port. Dropping cdp_url= "
            "answers a different question (the anonymous view of this page)."
        )
    elif err.startswith("cdp_endpoint_unreachable"):
        # 批次 9：这一支从前根本没有词 —— 整条响应退化成裸 {"error": ...}，读者只能
        # 拿一段 Playwright call log 自己猜。修的方向都在**浏览器那一侧**。
        next_action = (
            "the CDP endpoint is not answering, so nothing was fetched and nothing "
            "was read - this is fixed on the browser side, not by a parameter: "
            "start the browser holding your logins (Chrome or Edge) with "
            "--remote-debugging-port=<this port> plus its --user-data-dir, then "
            "re-call the SAME url. Raising timeout will not help (nothing is "
            "listening is not the same as slow), and dropping cdp_url= answers a "
            "different question (the anonymous view of this page)."
        )
    elif err.startswith("browser_busy"):
        # 批次 11：dhole 既不接管一台正在隐藏的浏览器（用户的 profile 会突然出现在屏幕
        # 上，那像魔法），也不关掉一台（扔掉的可能是他正在用的标签页）。所以这一步归人。
        next_action = (
            "a browser is already running on that profile and this call asked for a "
            "VISIBLE one - nothing was fetched. Either hand the current browser back "
            "first (sys_state() lists session ids, sys_state(clear=['<session id>']) "
            "closes one, clear=['sessions'] with confirm=true closes all) and re-call, "
            "or drop headed=true and ride the browser that is already up. A visible "
            "browser can serve hidden work, so the other direction never blocks."
        )
    elif err.startswith("schema_no_match"):
        next_action = ("no selector matched this page - re-check the selectors, or fetch "
                       "with extraction_type='html' to inspect the markup yourself")
    elif err.startswith("geo_redirect_detected"):
        next_action = "geo redirect: try a different regional URL or a proxy"
    elif err.startswith("extraction_collapsed"):
        # 批次 2：这一页的字在 HTML 里，是 dhole 没抽到。推荐"再抓一次/起浏览器"会把
        # 调用方送回同一条抽取管路 —— 唯一真的换个结果的旋钮是 extraction_type。
        #
        # 场景 3 的那次实测把这条建议自己也送回了管路：'text' 与 'html' 并列劝过一次，
        # 而 'text' 与默认的 markdown 过的是同一个读取器（换了它，527/1998 那发一模一样）。
        # 原先句尾还挂着"or web_crawl this one URL" —— 同一枚读取器的第三个入口，同样
        # 不算出路，所以没有搬进 `_COLLAPSE_RECOVERY`。
        next_action = (f"the extract lost this page's own text - {_COLLAPSE_RECOVERY}")
    elif "browser_binary_missing" in err or "browser_unavailable" in err:
        # 批次 3：这一句是全场最多发的一条建议，而它以前每次都在推荐刚刚失败的那一档。
        # 升级被跳过时 `escalation_path` 与 `error` 里已经带着事实（server 在跳过
        # stealthy 之前写的），所以这里不需要新状态，只需要读它。
        next_action = (
            "the browser tier is not available on this machine, so this answer came "
            "from the HTTP tier only - do NOT re-fetch with force_fetcher='stealthy' "
            "(the same skip will happen). `dhole --doctor` names the state and the "
            "directory it searched; search, plain reads, docs and feeds all still work.")
    elif err.startswith("Response body too large"):
        # 这个拒绝是 dhole 自己的判词，不是主机的；分类器那句泛化的「查 error 字段，
        # 换个源」回答的是一个没人问的问题：超时、档位或重试都改变不了 body 大小的
        # 上限。
        next_action = ("the body is over dhole's size cap - no timeout or fetcher "
                       "changes that. Fetch the page that links the file instead, or "
                       "download it yourself and hand it to parse")
    elif err.startswith("scanned_pdf"):
        from dhole_mcp.pdf_extractor import ocr_advice
        # 看能力说话："install [all] to auto-OCR" 在一台已经装了 OCR 的机器上是假话。
        # 装着 OCR 还走到这里，说明恢复步骤什么也没产出，那是另一句话。
        next_action = f"scanned/image-only PDF - {ocr_advice()}"
    elif err.startswith("image_ocr_thin"):
        # 上面那一支说的是"一个字都没读到"；这一支读到过东西（所以正文里真有它），
        # 要说的是**那不是这页的话**。两句不许互换。
        next_action = ("this image has no text layer worth reading - the stray "
                       "character(s) in `content` are OCR noise, not a quote. Read "
                       "the picture with a vision-capable model, or a web_act "
                       "screenshot action on the page that holds it")
    elif err.startswith("image_ocr"):
        # 图片分支返回 content=[]（它们从前携带的占位文字是正文里的一条错误消息），
        # 所以恢复步骤必须放在这里。门开在 error 上而不是 page_type=="image" 上：
        # OCR 成功的图片同样还是 page_type image，而告诉它的读者「文字读不出来」
        # 会是假话。
        next_action = ("no text could be read from this image - "
                       "use a vision-capable model or a web_act screenshot action, or switch source")
    elif (not result.content_ok) and (result.quality_score or 0) > 0 and result.quality_score < 0.7 and not err:
        from dhole_mcp.pdf_extractor import ocr_advice
        next_action = ("low-quality PDF extraction (CID font corruption / garbled "
                       f"text) - {ocr_advice()}")
    elif err.startswith("encrypted_pdf"):
        next_action = "encrypted PDF - pass a password via the 'password' option"
    elif err.startswith("pdf_deps_missing"):
        next_action = _install.pdf_install_advice()
    elif err.startswith("not_a_pdf") or err.startswith("pdf_open_failed") or err.startswith("pdf_extract_failed"):
        next_action = "PDF could not be parsed - see error field"
    elif "all_tiers_failed" in err:
        from dhole_mcp.errors import classify_network_error
        raw_err = " ".join(result.content) if result.content else err
        category, _ = classify_network_error(raw_err)
        if category == "connection_refused":
            next_action = ("All fetch tiers failed: connection refused. The site is unreachable "
                          "from this network (site down or outbound connections blocked). "
                          "Do NOT retry - switch to a different source.")
        elif category == "connection_reset":
            next_action = ("All fetch tiers failed: connection reset by remote host (anti-bot or "
                          "firewall). Try a proxy, or switch sources.")
        elif category == "timeout":
            next_action = ("All fetch tiers failed: timeout. The site is unresponsive. "
                          "Retry once with a longer timeout, or switch sources.")
        elif category in ("dns_failure", "dns_unresolvable"):
            next_action = ("All fetch tiers failed: DNS resolution failed. Verify the "
                          "URL is correct; do NOT retry.")
        elif category == "browser_launch":
            # 说清是三种状态中的哪一种，而不是推荐刚刚失败的那一档：
            # "re-fetch auto-escalates to the stealthy browser" 是给一台起得来的
            # 浏览器提的建议。
            next_action = (
                "All fetch tiers failed: the browser process could not be started, so "
                "the target is not implicated and another fetch will fail the same way. "
                "`dhole --doctor` names the state (absent / present but never launched / "
                "refused by the OS) and the directory it searched. HTTP-tier tools still "
                "work; attach to a browser you started yourself with "
                "cdp_url='http://127.0.0.1:9222' plus confirm=true.")
        else:
            if result.status >= 400:
                # 站点**答了**一个状态码，catch-all 那两句就都是假的：它不是"不可达",
                # 也不是"墙"。实测（2026-10-08，重启后的真 MCP）
                # `web_read("https://httpbin.org/status/503")` 的 `error` 明写
                # "A 5xx is not a bot block"，同一份回执的 `next_action` 却说
                # "may use unbypassable protection (DataDome/Akamai/Turnstile) … switch
                # sources" —— 两处判据各有作者：server 的 tips 按状态码说话，这里按
                # 文本分类说话，而文本分类读不到状态码。这一族的答案已经在那份 error
                # 里，指过去，别再猜一遍。
                next_action = (
                    f"every tier came back with the site's own HTTP {result.status} - the "
                    "origin answered, so this is neither an unreachable host nor a render "
                    "problem a browser could undo. What that status means and what can "
                    "change it is in `error`; nothing else in this call moved it.")
            else:
                next_action = ("All fetch tiers failed. The site may use unbypassable protection "
                              "(DataDome/Akamai/Turnstile) or is unreachable - switch sources.")
        # 「换个源」对一个本地端口不是答案：这一族的失败在私网目标上只有一个解释。
        next_action = _local_target_no_answer(category, result.url) or next_action
    elif err.startswith("encoding_undecodable"):
        # 一次猜错的编码，不是网络或路径问题 —— 下面那句泛化的 parse 提示会把调用方
        # 支去检查本来就没问题的路径。
        next_action = (
            "the file's charset was guessed wrong, so the text is mojibake - "
            "re-call parse with encoding='<charset>' (e.g. 'big5', 'shift_jis', "
            "'euc-kr', 'cp1252', 'latin-1'), or read the file with your own tool"
        )
    elif result.fetcher_used == "parse" and err:
        # 本地文件失败从来不是网络问题，所以不许捡起下面 status==0 分支本来会给的
        # 网络提示。
        #
        # 这一族四支讲的是同一件事：**一个字节都没读过**（名字没找到 / 是目录 / 被两道门
        # 挡住）。所以它们的行首申报只许装配一次（评审 R02-123 · 真机 `r219b_doc_parse_faces.json`：
        # 同扇参数层 5 发行首交 `Nothing was fetched.`，这一族 5 发行首要么是小写手抄的另一套
        # 申报 `nothing was read - …`，要么根本没占申报位 —— 一扇两种口径）。
        # 下面的 else 不在这一族：那一支的文件**读过**，「这一发什么也没发生」对它是假话。
        _refusal_way_out = None
        if err.startswith("Path is a directory"):
            # 这一发的路径**解析成功了**，所以那句「换绝对路径 / 传 cwd」是往错的方向
            # 指（实测：绝对写法的目录照样收到那句）。要说的只有一件：一次调用读一颗
            # 文件，而名字在 error 里列着。
            _refusal_way_out = (
                "The directory was FOUND - nothing about the path is wrong, and "
                "retrying with an absolute path or cwd= cannot change the answer. "
                "doc_parse opens one file per call: pick one of the names listed "
                "in `error` and pass the directory plus that name. Listing a folder "
                "is not what this tool does."
            )
        elif err.startswith("File not found"):
            # 出路那句由 server 那一侧按形状写一次（`os.path.isabs(file_path)` 在那儿），
            # 这里不复述它：一句出路在回执里写三遍，改一处漏两处就自相矛盾 —— 而实测正是
            # 三遍都写着「换成绝对形式」，包括那一发**本来就是绝对形式**的那两遍（R02-062）。
            # 指回 `error` 既不逼下游去猜形状，也不许它把不该说的话说错。
            _refusal_way_out = (
                "The name you gave is not where this call looked. `error` "
                "lists the places it tried and, for the shape of the path you passed, what "
                "could still change the answer; re-sending the same string unchanged reaches "
                "the same stop."
            )
        elif err.startswith("Access denied: path is in a restricted system directory"):
            # 这一族说的是路径，兜底那句说的也是路径，但意思正好相反。实测
            # doc_parse(file_path='C:\\Windows\\System32\\drivers\\etc\\hosts')：
            # diagnosis 让"换一个不在受限目录里的路径"，next_action 却说"路径没什么
            # 可修的"—— 而**一个字节都没读过**，那份文件是被门挡住的，不是内容坏了。
            _refusal_way_out = (
                "The PATH was refused before anything was read - the file's contents "
                "were never looked at. Pick a path outside the restricted system "
                "directory (copy the file somewhere else if you need it), and note "
                "this is a guard, not a knob: no argument opens it."
            )
        elif err.startswith("Access denied: path names an NTFS alternate data stream"):
            _refusal_way_out = (
                "The PATH was refused before anything was read. Drop the ':stream' "
                "part and name the file itself - the stream's contents would come "
                "back as the document."
            )
        if _refusal_way_out is not None:
            next_action = f"{nothing_went_out()} {_refusal_way_out}"
        else:
            # 路径**不是**问题：文件找到了、打开了、读完了，然后它的内容出了错。
            # 四种情况实测 —— 坏 JSON、坏 YAML、不是 %PDF 的 body、不支持的扩展名
            # —— 四种全被送到上面那句路径建议，读起来就是「你的路线错了」，而那条
            # 路线早已走通。
            #
            # 但这四种形状不该共用**同一句**下一步（评审 R02-022）：前一句还替它们所有人
            # 说「按 error 建议的那个改名去修」，而 CFB 那一发的 error 明说"改名不改字节"——
            # 一封响应里两句话互相否认，假的那句才是调用方照着行动的；坏 JSON 那一发根本没人
            # 问过扩展名。所以下一步现在由写 error 的人交出（parse 的 `way_forward`），这里
            # 只在没有作者说话时补一句与扩展名无关的实话。
            if result.next_action:
                next_action = result.next_action
            else:
                next_action = (
                    "the file was found and read; what failed is its contents, named "
                    "in the error field. Nothing to fix about the path; a .pdf/.docx/"
                    ".xlsx whose bytes disagree with the name is a mislabelled file, "
                    "not a missing one."
                )
    elif err.startswith("timeout: the ") and result.next_action:
        # 预算结果已经点名是哪一档用完、该改什么。下面那句泛化的网络提示更含糊，
        # 所以保留具体这句。
        next_action = result.next_action
    elif result.status in (404, 410):
        # 因为截断提示让位给错误状态码，现在才会走到这里：对一个不存在的页面，
        # "keep paginating" 是错的答案。
        next_action = ("page does not exist (HTTP 404/410) - the URL is stale or "
                       "misspelled. Do NOT paginate it: check the URL, or "
                       "web_search for the page's current location")
    elif result.status in (401, 407):
        # 401 不是「客户端像 bot」（那是 403 的建议），也不是浏览器升级能修的东西
        # —— 正因为如此，我们对 401 不升级。它是关于凭据的陈述，所以提示要说清我们
        # 到底发没发凭据：「这里没有 auth」与「你的 auth 被拒了」需要相反的处理。
        _auth_kind = _AUTH_SENT.get()[0]
        if _auth_kind:
            next_action = (
                f"HTTP {result.status}: the credentials were REFUSED. auth "
                f"(type={_auth_kind}) was sent to this host and rejected - re-check "
                "the username/password or token, with cred_defaults. Retrying "
                "unchanged cannot change the "
                "answer, and they will not be sent to any other host.")
        else:
            next_action = (
                f"HTTP {result.status}: this URL needs credentials. Store them once "
                "with cred_defaults (set={auth: {type:'basic',username,password} / "
                "{type:'bearer',token} / {type:'header',name,value}, plus the host) "
                "- no fetcher "
                "tier can invent them, so a browser retry is wasted here.")
    elif result.status == 403:
        if _render_tier_was_spent(result):
            _denied_step = (f"the browser tier already made this request "
                            f"(fetcher_used='{result.fetcher_used}') and the site refused "
                            "it the same way - rendering is not the untried lever here. "
                            "Switch source, or re-call later if the refusal looks "
                            "session-dependent.")
        elif _escalation_skipped_by_kind(result, result.url):
            _denied_step = ("dhole does not send .pdf / image / data-document responses "
                            "to the browser tier (a render would replace the bytes with "
                            "a viewer), so there is no stealthy retry on this URL - "
                            "switch source, or download the file yourself and parse it.")
        elif _browser_cannot_help(result, result.url):
            _denied_step = ("A browser render is the usual move and it is not available on "
                            "this machine right now (see error/`dhole --doctor`), so do not "
                            "spend a call on force_fetcher='stealthy' - switch source.")
        else:
            _denied_step = ("Retry once with force_fetcher='stealthy', otherwise switch "
                            "source")
        next_action = ("access denied (HTTP 403) - the site is refusing this client. "
                       + _denied_step)
    elif result.status == 429:
        next_action = ("rate limited (HTTP 429) - wait before retrying, or switch source")
    elif result.status >= 500:
        # 实测：httpbin /status/500 回来时套的是下面那句兜底提示，因为网络分类器没有
        # 东西可对 "http_error_500" 做匹配，只能答「换个源或用不同参数重试」。这次调用
        # 的任何参数都推不动一个服务端失败，而 dhole 本来就拒绝把 5xx 升级进浏览器，
        # 正是这个原因 —— 提示不许暗示浏览器档是那个没试过的选项。
        #
        # 5xx 里有**一种**形状不是服务器在说话，把它说成服务器说话会让值班工程师
        # 提错单（round 01, R01-041）：一个 502，零字节，几十毫秒就回来，而路径上
        # 有中间人。`no-such-host-*.invalid`（RFC 6761 保证解析不了的名字）在这里
        # 答出 502/0B/32ms，是因为这台机器自己的代理截住了假 IP 查询，替一个它够不到
        # 的目标作答 —— 而回执却说「服务器在服务端失败了……这次请求里没有任何东西
        # 导致它……站点宕了」。所以归因需要两个事实，且只有两个都成立才触发：没有
        # 一个字节回来，且源站上游有东西为这个主机配置了代理（G31 的 loopback 豁免
        # 意味着本地目标保留旧的、正确的句子）。
        from dhole_mcp.fetcher import system_proxy_for
        _no_bytes = not has_content and (result.total_size_bytes or 0) == 0
        _via = system_proxy_for(result.url or "") if _no_bytes else ""
        if _via:
            next_action = (
                f"HTTP {result.status} with no bytes at all, after "
                f"{int(result.duration_ms or 0)}ms, and this machine hands that "
                f"scheme to a proxy ({_via}) which dhole's HTTP tiers inherit. "
                "An empty answer that fast is the shape of that proxy speaking for a "
                "target it could not reach - nothing here proves the site itself "
                "returned this status, so the site is NOT established as down. "
                "Retry once after a pause; if it persists, tell the two apart: ask "
                "the same URL where no proxy is configured, or "
                "read the proxy's own log for this request. dhole inherits an "
                "OS-level proxy and has no per-call switch for it; a proxy stored "
                "with cred_defaults does have one (cred_defaults(unset=[\"proxy\"])). "
                "A 5xx is still not a bot block, and no parameter fixes it - on "
                "either side of that proxy.")
        else:
            next_action = (
                f"the server failed at the server (HTTP {result.status}). Nothing in "
                "this request caused it and no parameter fixes it. Retry once after a "
                "pause; if it persists the site is down or the path is broken, so "
                "switch source. A 5xx is not a bot block.")
    elif result.status == 0 or result.status >= 400:
        from dhole_mcp.errors import classify_network_error
        category, hint = classify_network_error(err)
        next_action = _local_target_no_answer(category, result.url) or hint

    # v10 由信封驱动的下一步：只在抓取成功、拿到真内容，且没有 error 驱动的
    # next_action 已经触发时才发。把信封（page_type/freshness）变成具体的一步，
    # agent 不必自己再推一遍。优先级：页面结构（list/auth/paywall/redirect）
    # 高于新鲜度。
    if not next_action and content_ok:
        if result.page_type == "pdf" and result.total_extracted_chars > 20000:
            _repeat = _focus_repeat_warning(
                "pages='X-Y' fetches specific sections from the table_of_contents "
                "instead of cutting this one down")
            next_action = _repeat or (
                f"large PDF ({result.total_extracted_chars} chars extracted). "
                "Use focus='query' to extract only relevant paragraphs, or "
                "pages='X-Y' to fetch specific sections from the table_of_contents"
            )
        elif result.page_type == "list":
            cits = (result.links or {}).get("citations") or []
            top = _best_list_targets(cits, result.url)
            if top:
                next_action = (
                    "this is a list page; the content you want is likely behind its "
                    f"links. Top targets: {', '.join(top)}. Or call web_crawl on this URL."
                )
            else:
                next_action = (
                    "this is a list page (links to other pages); call web_crawl on "
                    "this URL, or fetch the linked pages directly"
                )
        elif result.page_type == "auth_wall":
            next_action = ("content behind login/authentication - "
                           + _auth_wall_step(result.url))
        elif result.page_type == "paywall":
            next_action = "paywalled content; try the Internet Archive or a different source"
        elif result.page_type == "redirect":
            canon = (result.metadata or {}).get("canonical") or ""
            next_action = (
                f"page redirected; the real URL is {canon}" if canon
                else "page redirected; check the final URL field"
            )
        elif result.is_stale and result.page_type in ("article", "unknown"):
            # 「内容过期」这句提示只对正文说得出作用，所以只列讲结构的两个值：
            # article，以及 unknown（没认出结构，默认按正文对待）。§3 第 28 条之前
            # 这里还列着 docs —— 那个值已经并给 source_type。
            # 点名实际套用的时间视界（G15）：新闻 30 天，参考/文档/仓库/问答
            # 730 天，其余 365 天。「内容已 400 天」在一条新闻和一张 wiki 页面上
            # 含义完全不同。
            next_action = (
                f"content is {result.content_age_days} days old - past the "
                f"{_stale_days_for(result.url)}-day staleness horizon for this kind "
                "of source; for current info, web_search a recent query (e.g. add "
                "the current year)"
            )
    # 批次 6e：encoding 字段在没人声明编码时写的是一个"看起来像测量结果"的名字。真实的
    # 实测边界（charset.py 上面记着）是 GB18030 也严格解得开 Shift_JIS 与 EUC-KR 的字节，
    # 出来的是看着像中文的错字、damage 0 —— 便宜的判别器不存在，所以能诚实说的只有
    # "这一个是 dhole 选的"，不能说"这一个猜错了"。
    # 不进 content_ok：那会把一次正确的中文读取报成失败，而读者从此不信这个字段 —— 用一
    # 个新的谎换掉一句旧的含糊，是本仓库反复拒绝的那种修法。
    _basis = (result.metadata or {}).get("encoding_basis")
    if _basis == "guessed" and not result.error:
        hedge = (
            "the charset here is a guess, not a declaration: this file named no "
            "encoding and gb18030 decoded it cleanly - but gb18030 decodes Shift_JIS "
            "and EUC-KR bytes cleanly too, into plausible-looking wrong Chinese, and "
            "nothing cheap tells the three apart. If the text reads like the wrong "
            "language, re-call with encoding= ('shift_jis', 'euc-kr', 'big5', 'gbk') "
            "- that turns the guess into a verdict. The content is not being doubted "
            "by this note.")
        next_action = f"{next_action} {hedge}" if next_action else hedge
    elif _basis == "fallback" and not result.error:
        # 点名的那一颗没在跑。这一句要说得出**两个名字**，因为缺席会被读成一个肯定：
        # 「没有 encoding_basis 键」在高杉那发里被读成「这就是我要的编码」，而回执正文是
        # gb18030 从 EUC-KR 字节里解出的错中文（R02-021，P3 又在 GBK 件点名 big5 上重演）。
        _md = result.metadata or {}
        hedge = (
            f"the encoding you named ({_md.get('encoding_asked', '?')!r}) is not the "
            f"one that read this file: those bytes do not decode under it, so dhole's "
            f"detection used {_md.get('encoding', 'an unknown charset')!r} instead. "
            "That is a detection, not your instruction - if the text reads like the "
            "wrong language, name the charset that does decode it ('euc-kr', "
            "'shift_jis', 'big5', 'gbk'). The content is not being doubted by this note.")
        next_action = f"{next_action} {hedge}" if next_action else hedge
    elif _basis == "contested" and not result.error:
        # 点名的那颗**在跑**，正文也是它的读数 —— 这一句不能说"猜"，也不能说"换"。
        # 它说的是同一件事的另一半（R02-037）：名字与字节相容不等于字节属于那个名字。
        # GBK 件点名 big5 时，仓库里三把现成的尺子全读 0/False（错语言的每一个字都是
        # big5 表里的合法字符），所以 dhole 唯一能交代的实话是"还有第二种干净的读法，
        # 它叫 gb18030"，并把可执行的解药给出来。判词一位不动，理由与 guessed 那一支
        # 同一条：一个新的谎不换一句旧的含糊。
        _md = result.metadata or {}
        hedge = (
            f"this text was read as you asked ({_md.get('encoding_asked', _md.get('encoding', '?'))!r}) - "
            f"that name decodes these bytes cleanly. The same bytes also decode "
            f"cleanly under {_md.get('encoding_rival', 'another charset')!r}, and "
            f"those are DIFFERENT texts: which one this file really is cannot be "
            "told from the bytes. If the words above are not the language you "
            f"expected, re-call with encoding="
            f"{_md.get('encoding_rival', '…')!r}. Nothing about this content is "
            "being doubted by this note.")
        next_action = f"{next_action} {hedge}" if next_action else hedge
    _issue = (result.metadata or {}).get("content_issue")
    if _issue:
        # 被降级的页面判词仍然是内容判词：`content_ok` 继续说明 body 是什么，而
        # `error` 不再声称这次调用本身失败了。
        content_ok = False
        _chain_note = (f"the page reads as: {_issue}. That is about the content, "
                       "not about this call: `metadata.actions` holds the receipts "
                       "of the chain that ran. Do not re-issue the same chain - "
                       "every step already marked ok would run again.")
        if not next_action:
            next_action = _chain_note
        else:
            # 别的建议先占了这个格子（实测那一发是"正文太薄，去读它的链接"）。它回答的
            # 是内容那一半，说不出"这一发办成了没有"，所以不许把它当成已经说过 —— 降级
            # 之后读者手上只剩两条路：照着别的建议重发，或自己猜。两句都留下，顺序是
            # 先内容出路、后"别重发这条链"。
            next_action = f"{next_action} {_chain_note}"
    if not content_ok and not next_action:
        next_action = _quality_way_out(result)
    if not content_ok:
        # 排在最后：先给这一发**该做什么**，再说**已经发生了什么**。两句的顺序不能反
        # —— 网络账是证据，不是出路；把它放在前面会让"再试一次"那类建议读起来像结论。
        _net_note = _network_clause(result)
        if _net_note:
            next_action = f"{next_action} {_net_note}".strip() if next_action else _net_note
    return summary, next_action, content_ok


# 按结构化 page_type 分的缓存 TTL，只在调用方留默认值时生效（见 web_read 的写入
# 路径）。放在模块级而不内联，是因为 test_envelope 的词表守卫会读它：一个键点名了
# 代码再也产不出的 page_type，就是一段看起来像规则、实际是死代码的东西 —— "docs"
# 那一行正是这样活得比它对应的值还久。
_TTL_BY_PAGE_TYPE = {"article": 21600}


def _apply_envelope(result: ResponseModel) -> None:
    """就地计算 v10 研究级信封字段。

    page_type：确定的 error/content_type 信号覆盖 `_translate_response` 里设的结构化
    值（js_shell/auth_wall/redirect 来自 error；pdf/json/image 来自 content_type）。
    source_type/is_official 来自 URL（廉价的启发式）。content_age_days/is_stale
    来自 metadata 日期 + fetched_at。每次返回都重算（包括缓存命中），因为它几乎免费，
    而输入（url、metadata、fetched_at）总是在场。
    """
    # page_type 覆盖：确定信号赢过结构化的猜测。
    # 链的页面判词住在 `metadata.content_issue` 里（它的 `error` 格子是空的，因为链
    # 自己跑过了 —— R01-028），所以页面的形状要从那个判词最终落到的地方读；否则
    # 降级这句话也会一并删掉信封里关于「这是什么页面」的陈述（实测：`page_type`
    # 从 "auth_wall" 变成了缺席）。
    err_type = page_type_from_error(result.error
                                    or (result.metadata or {}).get("content_issue")
                                    or "")
    if err_type:
        result.page_type = err_type
    else:
        ct = (result.content_type or "").lower()
        if ct.startswith("application/pdf"):
            result.page_type = "pdf"
        elif ct.startswith("application/json") or ct.startswith("text/json"):
            result.page_type = "json"
        elif _is_xml_content_type(ct):
            # 抓取路径原样交回的同一批媒体类型（P2-7）；xhtml+xml 被谓词排除在外，
            # 所以一份 HTML 文档保住它的结构化判词，而不是被改名。
            result.page_type = "xml"
        elif ct.startswith("image/"):
            result.page_type = "image"
        # else：保住 `_translate_response` 给出的结构化 page_type
        # （article/list/paywall/redirect/unknown）。
    # 源权威性：廉价的 URL 启发式，每次都重算（不缓存）。
    st, off = classify_source(result.url)
    result.source_type = st
    result.is_official = off
    # 新鲜度：metadata 日期对本响应的 fetched_at。每次都重算（metadata 可能来自缓存
    # 恢复；年龄是相对现在而言的）。URL 传进去，让陈旧**视界**匹配源类别（G15），
    # 也让一个持续被编辑的 wiki 不被按创建日期评判。
    age, stale = compute_freshness(result.metadata, result.fetched_at, result.url)
    result.content_age_days = age
    result.is_stale = stale
    # 错误页的链接是站点的外框，不是页面的来源链。在 Wikipedia 404 +
    # include_links=true 上实测：回来 53 条链接（Main page / Contents / Donate /
    # Cookie policy ...），links.is_truncated=true，而且 primary_source 指向一个
    # Wikimedia 的**搜索** URL。调用方为一张引用图付了钱，而那图画的是导航栏，
    # 还是在这一份 next_action 明说「此页不存在 —— 不要翻页」的响应上。
    if result.status >= 400 and result.links:
        result.links = {}


def _ignored_selector_note(result: ResponseModel) -> str:
    """本响应无法兑现调用方的 ``css_selector`` 时给的一条注记。

    ``css_selector`` 只收窄 HTML。在 XML/JSON/纯文本响应上它匹配不到任何东西，而
    调用方会悄悄收到**整份**文档。在 httpbin/xml 上用 ``css_selector='h1'`` 实测：
    整份 XML 回来了，而响应里没有一个字说选择器被丢掉了。一个要窄切片的 agent
    分不清「这里没有 h1」与「这就是整份文档」，于是会把整份东西当成那个切片来引用。

    故意报成注记而不是错误：文档本身可能正好是调用方要的（一个 JSON API 里本来
    就没有 h1 可找），所以把 ``content_ok`` 翻成 False 等于叫他们不信任可用的内容。
    空的或 HTML 的 content-type 意味着选择器有过机会 —— 什么也别说。
    """
    selector = _CSS_SELECTOR.get()
    if not selector:
        return ""
    ctype = (result.content_type or "").strip()
    if not ctype or "html" in ctype.lower():
        return ""
    return (f"css_selector '{selector}' was NOT applied: content-type '{ctype}' is "
            f"not HTML (css_selector only narrows HTML) - the whole document was "
            f"returned")


def _focus_effect() -> dict:
    """本次 focus 实际做成了什么（`_apply_chunking` 从 focus_content 的 stats 抄来）。"""
    outcome = _FOCUS_OUTCOME.get()
    return outcome if isinstance(outcome, dict) else {}


def _focus_repeat_warning(alternative: str) -> str:
    """一句"用 focus= 再窄一点"的建议；focus 已经切过并删掉结构性正文时改口。

    C4 的形状：调用方**已经**用了 `focus='3.5 电池包 里程碑'`，用户点名要的 3.5 节那张
    表就是这一次 focus 删掉的（focus 的安全网只保"与 query 共享至少一个词"的表），而
    `next_action` 紧接着推荐 `focus=`。同一颗 query 只会把同一样东西再删一遍 —— 工具
    在给自己挖坑，而那句话是响应里教调用方做的事。
    """
    dropped = _focus_effect().get("dropped") or []
    if not dropped:
        return ""
    named = ", ".join(f"{d['label']}" for d in dropped)
    return (f"focus= is already on this call and it dropped {named} - the same query "
            f"drops them again. {alternative}")


def _focus_ignored_note() -> str:
    """focus 点名了却一刀没切时的那句说法，否则空串。

    与 `_ignored_selector_note` 同一条规矩：整页原样返回是**可用内容**，不是失败，
    所以进 summary 而不是翻 `content_ok`。但没有那句说法时，调用方分不清"这页只有一
    块所以没什么可切"与"切了、恰好什么都留着"。
    """
    reason = _focus_effect().get("ignored") or ""
    if not reason:
        return ""
    return f"focus was NOT applied: {reason}"


# ─── 结构化诊断：把自由文本 `error` 归纳成可分支的枚举 ─────────────────────
# 调用方要"推理而不是猜"：`error` 是一句话，agent 得字符串匹配才知道下一步。
# diagnosis 把同一件事说成一个稳定的 code + 一个布尔 + 一个具体杠杆名，让 agent
# 能 if/else 而不是正则。prose 一个字不删——它是给人读的那一份，diagnosis 是它的
# 机器可读孪生。前缀优先，再退到子串，再退到"首词当作 code"（新错误也能有个稳定的
# 枚举名，不至于静默缺席）。
_DIAGNOSIS_PREFIXES: tuple[tuple[str, str, bool, str], ...] = (
    ("js_shell_detected", "js_shell", True,
     "re-fetch (auto-escalates to the stealthy browser tier), or extraction_type='html'"),
    # 同一个 code 的第二块产地牌子：`obfuscated_payload` 说的是"字全抽到了，字是载荷"，
    # 而下一步与空壳完全一致（渲染，或换 extraction_type）—— 两个前缀一个 code 的先例
    # 就在下面两行（bot_challenge_detected / bot_wall_detected 都归 bot_wall）。
    ("obfuscated_payload", "js_shell", True,
     "re-fetch (auto-escalates to the stealthy browser tier), or extraction_type='html'"),
    ("bot_challenge_detected", "bot_wall", True,
     "retry with force_fetcher='stealthy', else switch source"),
    ("bot_wall_detected", "bot_wall", True,
     "retry with force_fetcher='stealthy', else switch source"),
    ("auth_wall_detected", "auth_wall", False,
     "switch source, or the Internet Archive copy (a logged-out snapshot from some "
     "past day, not this account's data); cred_defaults auth if you have it"),
    ("auth_required", "auth_wall", False,
     "store credentials with cred_defaults (auth) for this host"),
    ("robots_disallowed", "robots_disallowed", False,
     "switch source, or ignore_robots=true if you have the site's permission"),
    ("proxy_unreachable", "proxy_unreachable", False,
     "fix the proxy (DHOLE_SEARCH_PROXY / cred_defaults unset=['proxy']); raising timeout will not help"),
    ("cdp_authorization_required", "cdp_authorization_required", True,
     "re-call the SAME url with confirm=true to authorize this host"),
    ("cdp_host_not_allowed", "cdp_host_not_allowed", False,
     "re-call with cdp_url= plus confirm=true for this host, or sys_state(clear=['cdp:host=<name>'])"),
    ("cdp_no_logged_in_context", "cdp_no_logged_in_context", True,
     "re-call the SAME url once; else point cdp_url at the browser holding your logins"),
    ("cdp_endpoint_unreachable", "cdp_endpoint_unreachable", False,
     "start the browser with --remote-debugging-port / --user-data-dir; no parameter fixes this"),
    ("browser_busy", "browser_busy", False,
     "sys_state(clear=['<session id>']) to hand the browser back, or drop headed=true"),
    ("schema_no_match", "schema_no_match", False,
     "re-check the selectors, or re-fetch with extraction_type='html'"),
    # 扇出那一族的**行**：每个请求的字段都在第二层页面上落空（`server._nested_read`）。
    # 与 schema_no_match 同一件事实的两个位置 —— 一处是一篇文档里的作用域，一处是另一篇
    # 文档的整页 —— 所以名字分开、杠杆同一句（r301 现场：12 行里 `/feed.xml` 那一行交的就是它）。
    ("fields_no_match", "fields_no_match", False,
     "read ONE of those pages without nested_* and re-derive the selector - name the "
     "element that holds the value, not the bare tag"),
    ("geo_redirect_detected", "geo_redirect", False,
     "use a regional URL or a proxy"),
    ("extraction_collapsed", "extraction_collapsed", False,
     _COLLAPSE_RECOVERY),
    ("encoding_undecodable", "encoding_undecodable", False,
     "re-call with encoding='<charset>'"),
    ("scanned_pdf", "scanned_pdf", False,
     "install OCR ([all]) or read the page images with a vision model"),
    ("image_ocr_empty", "image_ocr_empty", False,
     "use a vision-capable model or a web_act screenshot action, or switch source"),
    ("image_ocr_failed", "image_ocr_failed", False,
     "use a vision-capable model or a web_act screenshot action, or switch source"),
    # 认出来一个字符就说"读到了正文"，是把噪声当引文交给调用方（实测：httpbin
    # /image/png 那张 8090B 的照片交回 'o'，而 diagnosis 整格缺席）。这一族的正文
    # 仍然在 content 里 —— 这一条改的是说法，不是数据。
    ("image_ocr_thin", "image_ocr_thin", False,
     "read the image with a vision-capable model (or a web_act screenshot) - "
     "what OCR found is a stray glyph, not this image's text"),
    ("encrypted_pdf", "encrypted_pdf", False, "pass password= for this PDF"),
    ("pdf_deps_missing", "pdf_deps_missing", False, "install the PDF extra ([all])"),
    ("not_a_pdf", "not_a_pdf", False, "the bytes are not a PDF - check the URL/content-type"),
    # feed 的那一半此前没有格子：`not a feed: StartTag: ...` 走到首词兜底，量出来的
    # code 是 "not" —— 一个由兜底规则发明的词，调用方按 code 分支时什么也认不出。
    ("not a feed", "not_a_feed", False,
     "pass the feed's own URL (the address in <link rel=alternate>), not the site page"),
    ("pdf_open_failed", "pdf_open_failed", False, "the PDF could not be opened - see error"),
    ("pdf_extract_failed", "pdf_extract_failed", False, "the PDF could not be parsed - see error"),
    ("empty_pdf", "empty_pdf", False, "the PDF has no pages - nothing to read"),
    ("no_pages_in_range", "no_pages_in_range", False, "the pages= range is out of bounds"),
    ("empty or non-bytes PDF body", "empty_pdf", False, "the body was not a PDF"),
    ("Response body too large", "body_too_large", False,
     "fetch the page that links the file, or download it yourself and parse it locally"),
    ("invalid_request", "invalid_request", False, "fix the named argument"),
    ("schema validation error", "schema_validation_error", False, "fix the schema= / the page's markup"),
    ("timeout", "timeout", True, "raise timeout, or switch source"),
    # 整趟预算（`deadline_ms`）把这一页放下，而不是一次 HTTP 尝试超时：两种"时间用完了"
    # 的修法相反 —— 前者要抬整趟的钟或从游标续，后者才是抬 `timeout=`。
    ("deadline_ms", "deadline_ms", True,
     "raise deadline_ms= for the whole run (timeout= bounds ONE HTTP attempt), or "
     "continue from the returned resume cursor"),
    ("File not found", "file_not_found", False,
     "name a file that exists - `error` says where this call looked and what could still "
     "change the answer"),
    # 目录不是"没找到"：这一句是我们自己写的，按 startswith 认得出，所以它在册
    # （兜底规则会给首词，把 "Path" 当成 code —— 外人写的句子才归兜底）。
    ("Path is a directory", "path_is_directory", False,
     "pass a file inside the directory - doc_parse opens one file per call"),
    # parse 自家那两句大写开头的句子（评审 R02-129 · r247/r251 数到）：兜底那条
    # `_LEADING_CODE_RE` 只认小写开头的 token（它是给**外人写的**句子留的），于是这两句
    # 既进不了表也兜不出词，`_diagnose` 交回 None ⇒ 手册教的那根把手（`diagnosis.code`）
    # 在两颗设计过的拒绝上整格缺席。补进表而不是放宽兜底：放宽会把外人的句子也发明成 token。
    # `file_too_large` 与 fetch 侧那颗 `body_too_large` 是**两颗**：一顶帽管的是本地文件的大小，
    # 另一顶管的是发回来的正文，读者按 code 分支时要能分辨（合并成一颗就是叫读者去改错的那一只手）。
    ("Unsupported file type", "unsupported_file_type", False,
     "rename a plain-text file to .txt/.md, or pick one of the extensions `error` lists"),
    ("File too large", "file_too_large", False,
     "split the file or copy out a smaller one - no argument raises this cap"),
    ("Access denied: path is in a restricted system directory", "path_restricted", False,
     "choose a path outside the restricted system directory"),
    # 同一句门的两支，此前只有一支在册（实测 2026-10-09：备用数据流那条被 `_agent_hints`
    # 按 startswith 认得出、diagnosis 认不出，于是 code 空 → 整格缺席，而 next_action 照样
    # 说话）。一句我们自己写的话不该靠首词兜底 —— 兜底是给外人写的句子留的。
    ("Access denied: path names an NTFS", "alternate_data_stream", False,
     "drop the ':stream' segment and name the file itself - no argument opens this guard"),
    # 两扇状态门的自家判决句（评审 R02-130 · 真机 `r258_state_door_faces.json`）：此前都不在册，
    # 于是一句大写开头的 `Nothing matches …` 量出 code 空（整格缺席，`error` 明明写着），
    # 而 `models is show-only …` 被首词兜底发明成一颗 'models' —— 兜底的射程只该是外人写的句子。
    ("Nothing matches", "unknown_state_token", False,
     "name one of the tokens `error` lists - a state class, or an open session id"),
    ("network_error", "network", True,
     "check network/proxy; the classifier hint in `error` names the fix"),
    # find_similar 读不到源页那一面（评审 R02-137 · 现场 r276）：这句是 dhole 自己写的，
    # 从前表里没有它的格子，于是上线的 code 是小写首词兜底发明的 `could` —— 换措辞就换名字，
    # 而手册教调用方 switch on `code`。现在 `_source_leg_refusal` 已按
    # `fetch_source_for_similar` 记下的 `reason` 分别命名原因（blocked / http-error /
    # no-bytes / no-text，R02-138）；这一格留给没有原因可记的那一支，所以它仍然只声称门里
    # **确实知道**的事（源页没读出来），不替它猜原因。`retryable=true` 与同封 `next_action`
    # 第一句「Retry, or web_read…」说的是同一件事（两只手不许互相否决）。
    ("could not fetch the source URL", "source_unreadable", True,
     "web_read that URL first - it names which cause this is (blocked / offline / an "
     "empty page), then call find_similar again on one that answers"),
    # 协议边界上那两颗门名判决（评审 R02-130 的 unknown-tool 半边 · F123 · 现场 r337/r338）：
    # 这两句都是大写开头，兜底那条 `_LEADING_CODE_RE` 对它们交回**空** code（不是造一个错名字），
    # 于是线上读数是 `is_error=True` 加一格 `error` —— 申报、summary、把手三格全无，而同一对
    # 出口在参数那一族早就齐了。补进表而不是放宽兜底，与上面 `Unsupported file type` 同一判据。
    ("Unknown tool", "unknown_tool", False,
     "call one of the names `error` lists - a name that is not in this server ran nothing"),
)

# 子串匹配（前缀认不出的）：升级整链失败、浏览器档缺席这两个是句中出现的。
_DIAGNOSIS_CONTAINS: tuple[tuple[str, str, bool, str], ...] = (
    ("all_tiers_failed", "all_tiers_failed", True,
     "switch source, or check network/proxy - no single parameter fixes a full-tier failure"),
    ("browser_binary_missing", "browser_unavailable", False,
     "run `dhole --doctor`; do NOT retry with force_fetcher='stealthy'"),
    ("browser_unavailable", "browser_unavailable", False,
     "run `dhole --doctor`; do NOT retry with force_fetcher='stealthy'"),
    # 与上面那颗前缀同一族的第二句自家话（R02-130）：句子以**类名**开头（`models is show-only: …`），
    # 所以它天生进不了前缀表 —— 首词兜底会把那个类名当成 code。
    ("is show-only", "show_only_class", False,
     "read it with show=[] instead - no tool call erases this class (config knobs go on the "
     "operator side, model files have no erasure)"),
    # 边界上那第二颗门名判决（R02-130 的另一支 · F123）：句子以 `Tool '<name>' is not
    # enabled in this server:` 开头，门名夹在中间，所以它进不了前缀表 —— 与上面那颗同一块
    # 牌子的规矩。修一颗留一颗就是把同一种事交成两个名字：这两发都出自 `_dispatch` 门口、
    # 都在任何请求之前返回，差别只在"这副树没有这一扇"与"这台进程把它关了"。
    ("is not enabled in this server", "tool_not_enabled", False,
     "add the name to DHOLE_TOOLS (or unset it to enable every tool) and restart - "
     "this build has the door, this process switched it off"),
)

_LEADING_CODE_RE = re.compile(r"[a-z][a-z0-9_]{2,}")


def _lookup_prefix_row(code: str) -> tuple[str, bool, str]:
    """按 code 取表里那一行的 (code, retryable, recover_with) —— 一位读者，不重抄句子。"""
    for _prefix, c, r, rec in _DIAGNOSIS_PREFIXES:
        if c == code:
            return c, r, rec
    return "", False, ""


def _is_search_arg_refusal(err: str) -> bool:
    """这句是不是搜索门那族"某个参数写错了"的判决。名单的作者是那颗修法表，不是这里。"""
    from dhole_mcp.search import _PARAM_REPAIR_HINTS
    return any(err.startswith(prefix) for prefix, _hint in _PARAM_REPAIR_HINTS)


def classify_error_text(err: str) -> tuple[str, bool, str]:
    """一份 error 串对应的 (code, retryable, recover_with) —— 只看串，不看信封。

    存在这一层的唯一理由是**同一张判据表不许有两个读者**：`_diagnose` 认 ResponseModel，
    而 feed 的行、别的通道的行也带着同一族串。各处各抄一遍前缀表，迟早有一处只抄到一半。

    串自己就能定的那一步也在这里做掉：一个 `network_error` 前缀背着一整类成因，而它们
    对"重发会不会变"的回答相反（名字不存在与对端拒绝不会变，重置与超时可能会），所以
    布尔不猜，去 `errors` 那张表读 —— 同一张表正写着给人看的那句 hint。

    首词兜底（最后一档）的射程只该是**别人写的句子**：httpx 的原文、站方返回的解析器
    报错。dhole 自己写的话不靠它 —— 那种串要么在表里，要么在产地就自带 `token:`，
    因为"取第一个词"意味着改写句子就改写 code，而 fields.md 把 code 承诺成一个固定的
    枚举。这条分工由 tests/test_a_refusal_we_authored_is_not_named_by_its_first_word.py
    静态量着（它把产地拼的前缀也算进去，所以 `invalid_request: …` 那族算在册，不算兜底）。
    """
    code, retryable, recover = "", False, ""
    for prefix, c, r, rec in _DIAGNOSIS_PREFIXES:
        if err.startswith(prefix):
            code, retryable, recover = c, r, rec
            break
    else:
        for needle, c, r, rec in _DIAGNOSIS_CONTAINS:
            if needle in err:
                code, retryable, recover = c, r, rec
                break
        else:
            # 首词兜底之前先问一次网络分类器。实测：HTTP 档把库的原文留在 `error` 上
            # （`error sending request for url (…) > operation timed out…`），三张表都
            # 认不出这形状，于是首词兜底交出 code="error" —— 一个由兜底规则发明的词，
            # 而 retryable 停在 False，与同一份回执 next_action 里那句 "Retry once with
            # a longer timeout" 正好相反。分类器认得出类别的，布尔也去 `is_retryable`
            # 那张表读，不在这里另判一次。
            # 那位在册作者写的状态句（`status_error_sentence`）：名字、布尔、杠杆三样都
            # 出自同一处。这里只是读者 —— 不在第二处再判一遍「重发会不会变」。
            _answered = _HTTP_ERROR_RE.match(err)
            if _answered:
                _st = int(_answered.group(1))
                _retry, _lever = status_verdict(_st)
                return status_error_code(_st), _retry, _lever
            if _is_search_arg_refusal(err):
                # 搜索门那一族以**参数名**打头（`Invalid engine: …`）：那是它自己立的规矩
                # （`test_the_enum_argument_values_are_checked_by_dhole` 说前缀会把第一格词
                # 占掉），所以它进不了大写前缀档，而首词兜底只会拿"Invalid" 后面那颗词当 code。
                # 种类与别扇的参数拒绝同一颗 `invalid_request`，recover 那一行仍由表里那格
                # 一份写；名单的作者也不在这里，是 `search._PARAM_REPAIR_HINTS` 的键
                # （那颗表本来就在回答"哪颗参数错、怎么改"），这里只是读者。
                return _lookup_prefix_row("invalid_request")
            from dhole_mcp.errors import (classify_network_error, get_hint,
                                          is_retryable)
            _category, _hint = classify_network_error(err)
            if _category != "unknown":
                return _category, is_retryable(_category), get_hint(_category)
            m = _LEADING_CODE_RE.match(err)
            if m:
                code = m.group(0)
    if code == "network":
        from dhole_mcp.errors import classify_network_error, is_retryable
        _category, _ = classify_network_error(err)
        retryable = is_retryable(_category)
    return code, retryable, recover


# ─── 一颗状态码说了什么：句子与判决同一位作者（评审 R02-134 · F116）──────────
#
# 实测（`review/.runtime/p1/r282_same_404_across_doors.json` · 本机靶站 · 零外网）：同一件
# 事实「origin 回了 404」曾有**两位句子作者** —— 这里的一位写
# `http_error_404: server returned error status`（`web_read` 的信封与 `web_crawl` 的行都从它
# 取词），`feed._grab` 那一位写裸的 `HTTP 404`。裸句子既不在三张表里也不带自记号，判据认不出，
# 于是那一行带着 `error` 却**整格没有把手**，而同门的 `next_action` 正指着「逐行的 diagnosis」。
# 修法不是给裸句子在表里补一格（那是给第二位作者补户口：从此一件事实两个名字），而是让别的
# 通道来问这里 —— 名字、布尔、杠杆三样都出自同一处，跨门就无从各判一遍。
#
# 布尔与那句杠杆不是新立的意见：同一封回执的 `next_action` 早就按状态码说过话
# （429「wait before retrying」/ 5xx「Retry once after a pause」，见 `_agent_hints`），
# 而把手从前一律答 `false` —— 两只手互相否决就是 D5 那条法在判决位的形状。4xx 的其余支
# （404 / 410 / 451…）照旧 false：站方说的是「没有这个页面」，重发同一串改不了它。

_RETRYABLE_STATUSES = frozenset({408, 425, 429})
_HTTP_ERROR_RE = re.compile(r"^http_error_(\d{3})\b")


def status_error_code(status: int) -> str:
    """这颗状态码的 code —— 句子的名字与句子同一位作者（改措辞不许改 code）。"""
    return f"http_error_{status}"


def status_error_sentence(status: int) -> str:
    """站点回了 4xx/5xx 时那一句人话 —— 唯一的作者（别的通道要写这件事就来问这里）。"""
    return f"{status_error_code(status)}: server returned error status"


def status_verdict(status: int) -> tuple[bool, str]:
    """这颗状态码对「重发同一发会不会变」的回答，加上那一格的杠杆 —— 与句子同一位作者。

    R02-003：401/403 不许交白卷——同封回执的 next_action 教着 cred_defaults，
    机器可读孪生却给空串，两只手打架。杠杆措辞与 `auth_required` 那行（同一张
    表）及 401/403 的 next_action 同源。
    """
    if status == 429:
        return True, ("the limit is on rate, not on your arguments - wait for the window, "
                      "then re-call this exact URL")
    if status == 401:
        return False, ("store credentials for this host with cred_defaults (set={auth: "
                       "{type:'basic',username,password} or {type:'bearer',token}}), or "
                       "switch source / the Internet Archive copy")
    if status == 403:
        return False, ("this URL refuses this client - retry with force_fetcher='stealthy', "
                       "store credentials with cred_defaults, or switch source / the "
                       "Internet Archive copy")
    if status in _RETRYABLE_STATUSES or status >= 500:
        return True, ("the origin failed at the origin - re-call after a pause; if it "
                      "persists, switch source")
    return False, ""


def row_diagnosis(error: str) -> Optional[Dict[str, Any]]:
    """一条**行级**失败（feed 的每个源、批量结果的每一行）的结构化判据。

    与 `_diagnose` 共用同一张表，但不需要一份 ResponseModel —— 那些通道本来就没有信封
    的其余格子。没有 error、或串里认不出任何 code，都交回 None：**缺席即成功**这一约定
    不许在第二处变。
    """
    if not error:
        return None
    code, retryable, recover = classify_error_text(error)
    if not code:
        return None
    return {"code": code, "retryable": retryable, "recover_with": recover}


def _diagnose(result: ResponseModel) -> Optional[Dict[str, Any]]:
    """本次响应"不是干净成功"时的结构化原因，否则 None（缺席即成功）。

    优先级：error 前缀 → error 子串 → error 首词；无 error 但 content_ok 为假 →
    按 page_type 归类；都干净但被截断 → truncated。js_shell / bot_wall 的 retryable
    要看这台机器上浏览器档能不能起（`_browser_cannot_help` / `_never_escalate`）——
    建议"再抓一次会升级"对一台起不来的浏览器是假话。
    """
    err = result.error or ""
    code = ""
    retryable = False
    recover = ""
    if _chain_ran_ok(result) and (err or not result.content_ok):
        # R01-028：跑过的链拥有「这次调用成没成」这个问题的答案，而建议里绝不能
        # 出现的一件事是「把同一条链再发一遍」—— 已经标了 ok 的步骤会跑第二遍
        # （一次 fill 会填两次）。`retryable=false` 让这份响应的两只手意见一致，
        # 而不是互相否决。
        #
        # 但"链跑完了"只有在**页面判决仍然失败**时才需要这份诊断。content_ok=true
        # 且没有 error 的一发是干净成功：diagnosis 的合同是"缺席即成功"。从前这里
        # 不看 content_ok，于是一次成功的登录/截图/上传也带着
        # `diagnosis.code=no_content`（2026-10-28 第二轮：22 个带 no_content 的
        # web_act 场景里有 13 个 content_ok=true、error 为空），按 diagnosis 分支的
        # agent 会把成功读成"页面没有可用正文"。
        _issue = (result.metadata or {}).get("content_issue") or err
        _code = (_issue.split(":")[0].split("_detected")[0] if _issue else "no_content")
        return {"code": _code, "retryable": False,
                "recover_with": ("the action chain ran — `metadata.actions` holds one "
                                 "receipt per step, and that is the answer about this "
                                 "call. The content verdict describes the page it left "
                                 "behind, not the chain. Do NOT re-issue the same "
                                 "chain: every step already marked ok would run "
                                 "again (a fill would fill twice).")}
    if err:
        code, retryable, recover = classify_error_text(err)
        if code in ("js_shell", "bot_wall"):
            if _render_tier_was_spent(result):
                # 表里那句模板（"retry with force_fetcher='stealthy'"）是**无条件**的：
                # 它写的是这一族失败的通用杠杆，而这一次那根杠杆已经拉过了。同一个响应
                # 里 `next_action` 与 `diagnosis.recover_with` 两只手不许互相否决 —— 这
                # 是 D5 那条法在建议位的形状。retryable 不动：墙会不会撤是站点的事，
                # 这一次调用能做的事才是这里的事。
                retryable = True
                recover = ("the browser tier already rendered this and returned what is "
                           "in `content` - no untried tier is left on this URL")
            elif _escalation_skipped_by_kind(result, result.url):
                retryable = False
                recover = ("dhole does not send .pdf / image / data-document responses "
                           "to the browser tier (a render would replace the bytes with "
                           "a viewer) - switch source, or download the file yourself and "
                           "parse it")
            elif _browser_cannot_help(result, result.url):
                retryable = False
                recover = ("the browser tier is unavailable on this machine - switch "
                           "source (see error / `dhole --doctor`)")
            else:
                retryable = True
        if code == "auth_wall":
            # 表里那句是**没有 URL 可问**的那一条路的作者（`row_diagnosis` 给 feed 的
            # 每一行、批量的每一行，它们手上只有 error 串）。这里手上有 url，就换成按
            # 地理位置说话的那一句：墙在调用方自己点名的那台机器上时，"去档案站"不是
            # 没用，是永远不可能有用 —— 档案站不持有登录态后的那份。
            recover = _auth_wall_step(result.url)
    elif not result.content_ok:
        if result.page_type == "redirect":
            # 与 `_agent_hints` 的 redirect 分支同一位作者：正文是跳转脚本，出路是
            # 让浏览器跟随（R05-023）。不落到下面的 no_content/extraction_collapsed。
            _redirect_can_render = (
                not _browser_cannot_help(result, result.url)
                and not _never_escalate(result, result.url))
            if _redirect_can_render:
                return {"code": "redirect", "retryable": True,
                        "recover_with": ("re-fetch with force_fetcher='stealthy' (a "
                                         "browser follows it), or open the target the "
                                         "script names")}
            return {"code": "redirect", "retryable": False,
                    "recover_with": ("open the target the script names directly - the "
                                     "browser tier is unavailable here")}
        code = {"auth_wall": "auth_wall", "paywall": "paywall",
                "captcha": "bot_wall", "js_shell": "js_shell"}.get(
                    result.page_type, "no_content")
        retryable = code in ("bot_wall", "js_shell")
        if code == "no_content":
            # R01-028，P09 的现场：交回的 `content` **就是**可引用的官方文字，
            # 而这句话却在教调用方换源。一个判词不能否认同一份响应持有的事实。
            _chars = sum(len(str(c)) for c in (result.content or []))
            if _chars >= 200:
                code = "extraction_collapsed"
                recover = ("the text below IS on the page - what collapsed is the "
                           "extraction of a readable body, not the page. Before "
                           "changing source, try the untried levers on THIS url: "
                           "extraction_type='html', css_selector='…', focus='' "
                           "(whole page), or offset= for the next slice")
            else:
                recover = ("the page carried no usable text - switch source, or try "
                           "extraction_type='html'")
    elif result.is_truncated:
        code = "truncated"
        retryable = True
        if result.next_offset:
            recover = f"offset={result.next_offset} to continue"
        else:
            recover = (_focus_repeat_warning(
                "pass focus='' for the whole page, or a smaller max_content_chars")
                or "focus='query' to narrow, or a smaller max_content_chars")
    if not code:
        return None
    return {"code": code, "retryable": retryable, "recover_with": recover}


# CJK 统一表意文字 / 扩展 A / 兼容 / 假名 / 谚文：这些字符在多数 tokenizer 里
# 一字 ≈ 一 token，与拉丁文的 ~4 字/token 差一个量级。估计量级时分开算。
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")


def _approx_tokens(chunks) -> int:
    """content 的粗略 token 估计：CJK/假名/谚文按 1 字 1 token，其余按 4 字 1 token。

    不是分词器的精确数（dhole 不带 tokenizer），是一个**脚本感知**的量级信号：
    英文用 chars/4 尚可，中文按同一把尺会低报约 4 倍——那正是要预算上下文的人
    最不该被误导的方向。
    """
    text = "\n".join(c for c in (chunks or []) if isinstance(c, str))
    if not text:
        return 0
    cjk = sum(1 for _ in _CJK_RE.finditer(text))
    return cjk + round((len(text) - cjk) / 4)


def _with_agent_hints(result: ResponseModel) -> ResponseModel:
    """把 v10 信封 + 面向 agent 的提示盖在结果上。

    这是通用的最终包装（`_apply_chunking` 在每次返回时都调用：实时抓取、缓存命中、
    robots 拦截、档案兜底），所以信封会出现在 agent 见到的每一个响应上。
    """
    # `fetched_at` 是"这份**内容**什么时候被抓取"，不是"这封回执什么时候生成的"。
    # 一次缓存命中没有去抓任何东西：盖上当下的时刻，等于把一次回放化妆成一次新核验
    # （实测：`cached: true` + `duration_ms: 0` 的回执，`fetched_at` 与真重抓那一发
    # 逐位相同，`content_age_days` / `is_stale` 也一起说谎）。缓存那两条路径负责把
    # 存进去的时间带回来。
    #
    # 带不回来时（存量老行没有这一列）留**空**而不是补一个 now：一句"没说"好过一句
    # 假话 —— 那正是这一刀要消灭的形状。summary 那一支看见空值就只说 `cached`。
    if not result.cached:
        result.fetched_at = datetime.now(timezone.utc).isoformat()
    _apply_envelope(result)
    _report_obfuscation(result)
    summary, next_action, content_ok = _agent_hints(result)
    # 参数注记（某个值被钳到文档写明的边界）属于一行状态：它们描述的是**这一份**
    # 响应，而 next_action 必须保持「空 = 无事可做」。
    notes = _ARG_NOTES.get()
    if notes:
        summary = " · ".join([summary, *notes]) if summary else " · ".join(notes)
    selector_note = _ignored_selector_note(result)
    if selector_note:
        summary = f"{summary} · {selector_note}" if summary else selector_note
    focus_note = _focus_ignored_note()
    if focus_note:
        summary = f"{summary} · {focus_note}" if summary else focus_note
    # G25 阶段4：正文是数据，不是指令——命中注入启发式就点名（标记不拦截，
    # 形状与边界见 injection.py 的模块 docstring）。content 里混着的图片块
    # 不是文本，跳过；空 content（错误/robots 拒绝）scan 返回 None。
    # chunking 路径已在 focus/截断前扫过整页并挂了值——见已有值就不重扫
    # （重扫窗口会把页级计数改写成窗口级）；不经 chunking 的路径在这里
    # 补扫，作后备。
    finding = result.untrusted_content
    if finding is None:
        finding = injection.scan("\n".join(
            chunk for chunk in result.content if isinstance(chunk, str)))
    if finding:
        result.untrusted_content = finding
        note = f"untrusted_content: {finding['total']} hit(s)"
        summary = f"{summary} · {note}" if summary else note
    # 页内重复样板行折叠的条数：抽取时设定，这里报在信封上（删了内容就必须说）。
    folded = _BOILERPLATE_LINES_COLLAPSED.get()
    if folded:
        result.duplicate_lines_collapsed = folded
        note = f"duplicate_lines_collapsed: {folded}"
        summary = f"{summary} · {note}" if summary else note
    # 评审R2刀5：缓存按 URL 键，它不认识"当时是谁的 cookie"。命名会话登录之后，TTL
    # 内的回读可以整批发自登录前的那一份，而响应原本只说这一发没发包 —— 调用方于是
    # 把匿名页面当登录视图引用（评审实测：saucedemo 登录后回读还是 `Login`）。
    # 注记而不是重取：`cache_ttl=0` 值不值一次抓取是调用方的决定；这一刀负责的是
    # "这一发没问过站点"必须说得出。缓存按会话身份分键在评审的「不修清单」里。
    if result.cached and result.session_id:
        note = ("cached copy may predate this session's cookies - the cache is "
                "keyed by URL, not by who was logged in; pass cache_ttl=0 to "
                "re-fetch under this session")
        summary = f"{summary} · {note}" if summary else note
    result.summary = summary
    result.content_ok = content_ok
    result.next_action = next_action
    # 结构化孪生：content_ok 已定，才能据它归类"不是干净成功"的原因；token 估计
    # 只描述**这一份** content 的成本（信封本身不计——它是常数级）。专项路径
    # （浏览器超时的 _over_budget_result）构造时就写好了更懂的那一份 —— 前缀表按
    # error 前缀归一出的泛化句不许把它盖掉（对浏览器超时说 "raise timeout" 恰是
    # 那条路径的专项 prose 要拦的动作）。缺席时前缀表仍是兜底。
    _attach_diagnosis(result)
    result.approx_tokens = _approx_tokens(result.content)
    applied = _IDENTITY_APPLIED.get()
    if applied and not result.identity_applied:
        result.identity_applied = applied
    return result


# _within_call_budget 兜底比调用方自己的预算晚多少才动手 —— 那颗数的作者在
# `context.BACKSTOP_GRACE_S`（链里每一步的截止也读它），这里只是把同一个名字留给
# 本模块的建议句用。见它的 docstring：它必须在竞争中输给各档自己的记账。
_BACKSTOP_GRACE_S = BACKSTOP_GRACE_S


def _as_sentence(text: str) -> str:
    """由子句碎片拼起来的建议，落到字段开头时仍然要像一句话。

    这些串本来是为「跟在 "; " 之后或括号里」写的，那里小写开头是对的。原样抄到
    `next_action` 开头，同一个碎片就用小写词起头，读起来像上一句没说完的子句
    （实测，R01-010："Budget exhausted after 32033ms at the fetch tiers. the fetch
    tiers on a document download"）。开头的数字本来就像句首，所以数字放行。
    """
    return text[:1].upper() + text[1:] if text[:1].isalpha() else text


def _budget_advice(stage: str, fetcher_used: str, asked_ms: float = 0.0) -> str:
    """超时建议里不许出现这次根本没用到的旋钮（批次 3 的同一条规矩）。

    `force_fetcher='http'` 只有在浏览器档真的参与过这次调用时才是一个选项。实测的形状是
    一次 PDF 下载在 HTTP 档耗尽预算，而建议写着"传 force_fetcher='http' 以跳过浏览器渲染"
    —— 那次调用离渲染一步都没沾，读者因此以为浏览器是成本所在，去查一个不存在的环节。

    同一族里另一半从前是空的：那句 "Raise timeout (e.g. timeout=60000)" **不看你已经给了
    多少**。实测（15 场景体测，场景 10）：调用方传的正是 `timeout=60000`，61,529ms 之后
    被掐，交回的建议劝他"抬到 60000" —— 一句劝人去做他刚刚做过的事的话，比不说更贵，
    因为它让人怀疑自己没读懂参数。文档那一路早有对应的写法
    （`server._document_budget_advice`：预算已经越过一次尝试的上限就不再劝抬），这一颗
    通用建议照同一条口径补齐：说清这次已经带了哪颗数、dhole 还收得到多大、以及抬上去
    买到的究竟是什么（浏览器时间，不是更快的下载）。
    """
    asked = int(asked_ms or 0)
    if asked >= TIMEOUT_MAX_MS:
        head = (f"timeout was already at dhole's ceiling ({TIMEOUT_MAX_MS}ms), so there "
                "is nothing left to raise - the lever is less work in one call")
    elif asked:
        # 说的数是**还剩多少**，不是再报一遍调用方刚给过的那一颗。
        head = (f"Raise timeout - this call already had {asked}ms and dhole accepts up "
                f"to {TIMEOUT_MAX_MS}ms ({TIMEOUT_MAX_MS - asked}ms more); raising buys "
                "more time at this stage, not a faster site")
    else:
        head = "Raise timeout (e.g. timeout=60000) for a slow host"
    lowered = (stage or "").lower()
    if "browser" in lowered or "stealthy" in lowered or fetcher_used == "stealthy":
        return _as_sentence(f"{head}, pass force_fetcher='http' to skip browser rendering, "
                            "or switch source.")
    return _as_sentence(
        f"{head}, or switch source - the browser tier never ran on this call (it "
        f"ran out at the {stage}), so there is no rendering here to skip.")


def _over_budget_result(url: str, budget_ms: float, elapsed_ms: float,
                        stage: str, fetcher_used: str,
                        diagnosis: str = "",
                        diagnosis_struct: Optional[Dict[str, Any]] = None) -> ResponseModel:
    """「调用预算用完了」的 FetchResult —— 一个正常响应，不是异常。

    调用方要求在 `timeout` 之内给出答案；这就是那个诚实的答案。它过去只在理论上
    可能发生：预算按档施加，所以一个慢主机会跑过头，然后 MCP 客户端杀掉请求
    （-32001），连一个 FetchResult 都没有。

    当某一档知道泛化建议会答错时，``diagnosis`` 会替换掉它 —— 一次文档下载被告知
    "pass force_fetcher='http' to skip browser rendering"，而它根本没碰过浏览器。
    ``diagnosis_struct`` 是同一份知识的机器可读孪生：没有它，finalize 会用前缀表
    把 error 重新归一，信封的 recover_with 会对那唯一一个「抬高 timeout 恰好是错的」
    超时说 "raise timeout"（浏览器档更清楚）。
    """
    head = f"Budget exhausted after {int(elapsed_ms)}ms at the {stage}."
    # 一份响应、两个数字，而在此之前没有一个字说明二者的关系：`error` 点名的是
    # 要求的预算，`next_action` 点名的是实际花掉的时间（round 01, R01-042 —— 本地慢
    # 服务器上实测 10000 请求 / 10876 花掉，persona 的真机案例是 10000 / 68113）。
    # 这个差额有两种诚实的读法，导向不同的动作，所以要说清这一次是哪种：兜底宽限
    # 期内的溢出（一个在途步骤正在收尾），还是真的越过它跑超了（这次调用里有东西
    # 被叫停时没有停）。
    asked, spent = int(budget_ms), int(elapsed_ms)
    grace = int(_BACKSTOP_GRACE_S * 1000)
    spill = spent - asked
    if spill > grace:
        relation = (f"it ran {spill}ms past the budget and its {grace}ms grace - the "
                    "budget is not a hard kill, and something inside this call did not "
                    "stop when told")
    elif spill > 0:
        relation = (f"{spill}ms over it is the {grace}ms grace that lets an in-flight "
                    "step finish, not a second budget")
    else:
        relation = "it stopped inside the figure you asked for"
    result = ResponseModel(
        url=url, status=0, content=[], fetcher_used=fetcher_used,
        duration_ms=elapsed_ms,
        error=(f"timeout: the {asked}ms call budget ran out during "
               f"the {stage} (spent {spent}ms; {relation}). "
               "No content was extracted."),
        next_action=(
            f"{head} {_as_sentence(diagnosis)}" if diagnosis else
            f"{head} {_as_sentence(relation)}. "
            f"{_budget_advice(stage, fetcher_used, budget_ms)}"),
    )
    if diagnosis_struct is not None:
        result.diagnosis = diagnosis_struct
    return result


def _is_over_budget(result) -> bool:
    """针对构造出来的「调用预算用完了」结果返回 True。

    按定义就是终态，所以每一档在继续花时间之前都先检查它：「页面拿到了吗」的答案
    已经是「没有，而且时间用完了」。
    """
    return bool(result) and result.error.startswith("timeout: the ")


# 剪 summary 标签时能停的地方，按"说完一件事"的程度分两级。两级都是**带尾随分隔符的
# 二元组**，这样 `rfind` 找到的位置就是剪口，不必再判断标点是不是落在了句尾。
def _summary_label(msg: str, cap: int = 80) -> str:
    """为 ``summary`` 格子缩短的一句话，停在**子句**结束的地方。

    ``msg[:80]`` 产出的标签像 "…can compile (S" —— 读者分不清是缩短的句子还是坏掉的
    字符串。改在最后一个*词*处断开修好了那个，却留下更糟的："…stored credentials
    for"、"…POST / PUT / PATCH /"、"…Valid values:"（实测，一次会话里三条拒绝），
    这些不算短，只是坏的方式不同 —— 一个停在介词或冒号上的标签，承诺了 summary
    里并没有的东西；而在枚举那个案例里，被切掉的恰恰是调用方需要的那份清单。
    整句无论哪种情况都留在 ``error`` 里，所以标签只欠读者一件事：它停在句子
    该停的地方。

    排序与判据现在住在 `snap_to_boundary`（分页那一路也要它，见 `server._apply_chunking`）；
    这一层只负责"短到 cap"和"收尾不留悬空空白"。
    """
    if len(msg) <= cap:
        return msg
    head = msg[:cap]
    return head[:snap_to_boundary(head)].rstrip()


def _invalid_request_result(url: str, msg: str, next_action: str = "",
                            tool: str = "web_read") -> ResponseModel:
    """任何请求发出之前就被拒的一次调用，形状像一个 FetchResult。

    输入校验从前会抛异常，通用处理器把它变成一个 ``is_error`` 的 MCP 结果 ——
    「你传了个坏参数」与「站点失败了」成了两种不同的响应形状，于是每个调用方都得
    特判它。现在拒绝走同一份契约：status 0、空 content、content_ok False、原因在
    ``error``、该做什么在 ``next_action``。

    ``next_action`` 默认是 URL 形状的建议，适配常见情形（一个畸形的 ``url``）。
    关于**别的**参数的拒绝必须传自己的那一句：把一个 ``max_content_chars`` 弄错的
    调用方叫去「传一个绝对的 http(s) URL」，等于让他去修那件本来就对的事。
    ``tool`` 点名调用方实际调用的工具（评审R1刀3）：URL 校验路径是共用的，而
    web_request 的拒绝如果引用 web_read 的套话，读起来就是关于另一次调用的建议。

    默认那一句**以本门的申报开头**（评审 R02-098 · 真机 `r119_url_shape_clauses.json`）：
    形状那一族（scheme / 空 url / 反斜杠）14 格里 13 格只有"改参数"没有"这一发没出去过"，
    因为申报那半句从来不住在这里，而这一支正是形状拒绝的落点。句子本体仍由
    `security.URL_SHAPE_RECOVERY` 一份写，动词由 `context.nothing_went_out()` 按门取。
    """
    result = ResponseModel(
        url=url, status=0, content=[], fetcher_used="none",
        extracted_type="markdown",
        error=f"invalid_request: {msg}",
        summary=f"invalid request · {_summary_label(msg)}",
        next_action=next_action or (
            f"{nothing_went_out(tool)} {URL_SHAPE_RECOVERY.format(tool=tool)}"),
    )
    _apply_envelope(result)
    # 拒绝信封也要交出那根把手（评审 R02-129 · r250 数到 10 扇 JSON 门全缺）：句子已由
    # `invalid_request:` 前缀在册，缺的从来不是判据，是有人去问它。
    _attach_diagnosis(result)
    return result


def _attach_diagnosis(result: ResponseModel) -> None:
    """把「结构化孪生」那一格交给**判据表**写 —— 一处守卫，两处信封共用（评审 R02-129）。

    从前只有 `_agent_hints` 那条路问过表；`_invalid_request_result`（全部 10 扇 JSON 门的
    参数层与 URL 守卫拒绝）与 robots 那扇预检拒绝各自造完信封就收工，于是设计过的拒绝
    `diagnosis` 整格缺席，而手册写的正是「ABSENT = 干净成功 / switch on `code`」
    （`references/fields.md` 的 `diagnosis` 一行、`ResponseModel.diagnosis` 的字段描述）。
    同一封回执里 `content_ok: false` 与
    「没有把手」并排，就是 D5 那条法在建议位的形状。

    守卫（已有则不覆盖）留着：专项路径（浏览器超时、动作链）构造时就写好了更懂的那一份。
    """
    if result.diagnosis is None:
        result.diagnosis = _diagnose(result)


def _report_action_outcomes(result: ResponseModel, page_action: Any) -> None:
    """把每个交互实际做了什么，公开进响应里。

    从前每个动作自己的失败只是一条 `logger.warning`，别的什么都没有，于是一个从未
    发生的动作，与一个发生了却没改变任何东西的动作，无法区分。验证轮撞上的正是这个：
    HN 上的 `click a.more-link`（一个匹配不到任何元素的选择器）回来是正常的第 1 页
    成功，还被读成「click 不等导航」—— 报告于是要求加等待，而代码里早就有了。
    每个动作一份回执，加上某个动作拒绝执行时在 summary 里的一行，把读者送到选择器
    那里，而不是送到时序那里。

    元素捕获（{\"screenshot\"} 动作）在这里一分为二：JSON 载荷保留一条只有名字的回执
    （`image: true`，外加解码后的 `qr` 串，它们很小且对每个客户端都有用），而像素
    移到 ``result._action_images`` 供 content 列表使用 —— 载荷里的 base64 会让这份
    响应落进的每一份纯文本 transcript 都背上税。
    """
    outcomes = list(getattr(page_action, "outcomes", None) or [])
    if not outcomes:
        return
    wire_rows: list = []
    images: list = []
    for o in outcomes:
        row = {k: v for k, v in o.items() if k != "image_b64"}
        if "image_b64" in o:
            row["image"] = True
            images.append({"data": o["image_b64"],
                           "mime_type": o.get("image_mime", "image/png")})
        wire_rows.append(row)
    result._action_images = images
    failed = [o for o in outcomes if not o.get("ok")]
    pending = [o for o in outcomes if o.get("pending_approval")]
    result.metadata = {**(result.metadata or {}), "actions": wire_rows}
    # 写操作审计（安全闸剩余项 2，DHOLE_USAGE_LOG 打开时落盘）：浏览器的
    # submit 是"哪个 session 对哪个站做了什么写操作"的典型——已确认的提交和
    # 被闸拦下的尝试都值得留痕。evaluate 走自己的一条通道：它不写任何东西，
    # 但它能把 cookie/登录态读进 transcript，值得单独一笔账。
    for o in outcomes:
        if (o.get("pending_gate") == "evaluate" or o.get("evaluated")
                or o.get("evaluate_attempted")):
            _audit_event("evaluate", channel="browser_evaluate",
                         url=(result.url or "")[:200],
                         session_id=_SESSION_ID.get() or "",
                         pending=bool(o.get("pending_approval")),
                         action=str(o.get("action", ""))[:80])
        elif o.get("submitted") or o.get("pending_approval"):
            _audit_event("write", channel="browser_submit",
                         url=(result.url or "")[:200],
                         session_id=_SESSION_ID.get() or "",
                         pending=bool(o.get("pending_approval")),
                         action=str(o.get("action", ""))[:80])
    if pending:
        # 审批闸（安全红线 3）的回应面：暂停要成为整个响应的头号信号，next_action
        # 直接指向"去问用户"，不能淹没在普通失败提示里。evaluate 与 submit 是
        # 两道不同的门——把 JS 求值的暂停写成"提交暂停"会让调用方去问一件
        # 没有发生的事（也拿错钥匙重跑）。
        pending_eval = [o for o in pending
                        if o.get("pending_gate") == "evaluate"]
        pending_submit = [o for o in pending
                          if o.get("pending_gate") != "evaluate"]
        names = "; ".join(str(o.get("action", "submit")) for o in pending)
        result.summary = (f"{result.summary} · {len(pending)} action(s) waiting "
                          f"for user approval ({names})")
        asks: list[str] = []
        if pending_eval:
            asks.append(
                "an evaluate action is PAUSED for user approval "
                "(pending_approval in metadata.actions) - it runs arbitrary page "
                "JavaScript, which can read cookies and logged-in state, and can "
                "also change the page or send requests; describe what the "
                "expression would do and ask the user, then "
                f"{_EVALUATE_RERUN}. Nothing was run.")
        if pending_submit:
            asks.append(
                "a submit action is PAUSED for user approval (pending_approval in "
                "metadata.actions) - describe the submission to the user and ask "
                "for confirmation; on a yes, re-run the same actions with "
                f"{_CONFIRM_SNIPPET}. Nothing irreversible was done.")
        result.next_action = " ".join(asks)
    elif failed:
        detail = "; ".join(
            f"{o['action']}: {str(o.get('error', ''))[:120]}" for o in failed)
        # 是「没有生效」，不是「没有运行」：某一步的 click 落在了一个 click 提交不了
        # 的元素上，它**确实跑了** —— 它没做的是把任何东西发出去。
        result.summary = (f"{result.summary} · {len(failed)} of {len(outcomes)} "
                          f"actions did not take effect ({detail})")
        if not result.next_action:
            if any(o.get("not_submitted") for o in failed):
                result.next_action = (
                    "a submit ran but no request left the page (not_submitted in "
                    "metadata.actions) - the target is an element no click submits, "
                    "so nothing was sent; that row's `error` names the element and "
                    "the two ways that do submit. The steps that did land are on the "
                    "page, so read the content as it stands")
            else:
                ran_first = [o for o in outcomes if o.get("ok")]
                if ran_first:
                    # 从前这一句是 "these interactions never happened, so the content
                    # is the page BEFORE them" —— 而它是在**至少有一步成功了**的时候
                    # 说出口的（实测：`fill '#qty' ok:true` 与 `read '#qty'` 失败同在一
                    # 条链里）。一句话抹掉已经落地的动作，读的人就只剩两个选择：要么信
                    # 一句假话，要么重跑整条链 —— 而重跑会再填一遍那个框。这比不说更贵。
                    names = "; ".join(str(o.get("action", ""))[:60]
                                      for o in ran_first)
                    result.next_action = (
                        "the step(s) listed as ok DID take effect on this page "
                        f"({names}); the failed one never happened - retry just that "
                        "action against a fresh copy of the page (metadata.actions "
                        "lists one row per action). Re-running the whole chain "
                        "replays the ok steps")
                else:
                    result.next_action = (
                        "these interactions never happened, so the content is the "
                        "page BEFORE them - check each failed selector against a "
                        "fresh copy of the page and retry just that action "
                        "(metadata.actions lists one row per action)")
    cut = [o for o in outcomes if o.get("cut_by_budget")]
    if cut:
        # 预算掐在**第几步**是这一格唯一决定下一步动作的信息：8 步的链断在第 7 步，
        # 该从那一步续上；一步没走，该整条重来。从前这两种都只写成
        # "No content was extracted"，读的人只能重跑整条链，代价是一整轮浏览器时间
        # （实测：一次元素截图 61,529ms，回执一个字节都没有）。
        where = "; ".join(str(o.get("step", "?")) for o in cut)
        landed = len([o for o in outcomes if o.get("ok")])
        prefix = f"{result.summary} · " if result.summary else ""
        result.summary = (f"{prefix}chain cut by the budget at step {where} - "
                          f"{landed} step(s) landed before it")
    ran = [o for o in outcomes if o.get("ok")]
    if ran and _is_over_budget(result):
        # 预算掐断一条动作链时，"什么都没发生"是最容易读错的一句话：列在这里的那几步是
        # 真的落在页面上了（D-35b 实测：`fill` 那一行交回 ok:true，同一份回执的 error 说
        # "No content was extracted"，而 next_action 的建议里有一条是"换个源" —— 换个源
        # 丢掉的就是那个已经填好的框）。
        names = "; ".join(str(o.get("action", ""))[:60] for o in ran)
        prefix = f"{result.summary} · " if result.summary else ""
        result.summary = (f"{prefix}{len(ran)} of {len(outcomes)} actions "
                          f"already ran ({names})")
        result.next_action = (
            (result.next_action + " " if result.next_action else "")
            + "those steps are ALREADY on the page, so re-running the whole chain "
              "replays them: raise the timeout to finish this chain, or continue from "
              "the action that did not run. Switching source discards what is already "
              "filled in.")
    if result.page_type == "js_shell":
        # SPA 的动作后回读仍然是空壳的「启用 JavaScript」文字 —— 这就是 DOM 里有的
        # 东西。没有这条注记，调用方会把空壳读成「动作什么都没做」（在一个 React
        # 登录页上实测：每一次 fill 都报 ok，body 从未变过）。
        result.next_action = (
            (result.next_action + " " if result.next_action else "")
            + "this page renders by JavaScript, so the readback is the shell, "
            "not the post-action state: what happened is in metadata.actions, "
            "and a screenshot action shows the rendered result")


def _robots_blocked_result(url: str, verdict: RobotsVerdict) -> ResponseModel:
    """「robots.txt 说不，所以没有发请求」的响应形状。

    形状与 ``_invalid_request_result`` 一致（status 0、空 content、原因在 ``error``），
    因为它**就是**一次预检拒绝：站点没有任何东西失败，报一个 4xx/5xx 或一个假的 200
    只会把 agent 支去调试错误的对象。``next_action`` 带上两个真实选项 —— 换源，或
    用文档里写的覆盖开关重新调用。
    """
    where = verdict.robots_url or robots_url_for(url) or "robots.txt"
    override = (f"re-call with ignore_robots=true if you have the site's "
                f"permission, or {ENV_IGNORE_ROBOTS}=1 to disable the check "
                f"process-wide")
    result = ResponseModel(
        url=url, status=0, content=[], fetcher_used="none",
        extracted_type="markdown",
        error=f"robots_disallowed: {where} disallows this URL for {ROBOTS_USER_AGENT}",
        summary=f"robots.txt disallowed · no request made · {where}",
        next_action=(
            "robots.txt disallows this URL for dhole, so nothing was fetched - do "
            f"NOT cite it. Switch source, or {override}."
        ),
    )
    _apply_envelope(result)
    # 同一颗病、同一个修法（r250 之后在同一个文件里读到的第三位）：这也是一封「什么都没发出去」
    # 的预检拒绝，`robots_disallowed:` 前缀早就在册，缺的还是那句「有人去问表」。
    _attach_diagnosis(result)
    return result
