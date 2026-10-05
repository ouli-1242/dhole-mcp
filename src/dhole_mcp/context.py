"""Dhole 的按次调用上下文：一次 web_read 里各处都要读的那几颗 ContextVar。

从 server.py 原样搬出（17.0 B6 的第四刀，零行为变更）。这是整个服务器里最干净的一块：
ast 分析过，它对 server 的模块级名字零依赖，只依赖 stdlib、`dhole_mcp.focus`
的那条别名与 `ResponseModel` 的类型标注。值得单独一个文件的理由是这些 var 的读写点
散在抓取、缓存、robots、身份四条路上，而它们必须是同一条通道 —— 一次调用里
`_SESSION_ID` 与 `_CACHE_CTX` 说的得是同一件事，散着读看不见这条约束。

为什么是 ContextVar 而不是参数：threading 两个新参数穿过每一层 fetcher 签名，
代价是所有既有路径的字节都变；而按次隔离又要求并发 bulk 的每一行读到自己的值，
那正是 task-local 的语义（`_PDF_PAGES` 一族注释里写的就是这件事）。
"""

from __future__ import annotations

import contextlib
import contextvars
import re
from typing import Any, Optional

from dhole_mcp.models import ResponseModel


# ─── Response translation helpers ──────────────────────────────────

# PDF extraction options flow from web_read down to _translate_response via
# contextvars (task-local, safe under concurrent bulk fetches) instead of
# threading two new params through every fetcher signature.
_PDF_PAGES: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("_pdf_pages", default=None)
_PDF_PASSWORD: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("_pdf_password", default=None)
# Query-focused content filter (web_read `focus` param). Applied POST-cache
# inside _apply_chunking: the full extracted text is cached once, and different
# focus queries are just different BM25 views over the same cached content.
_FOCUS: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("_focus", default=None)
# focus 的形态（M-2）：filter（默认，现有行为）| best（只回打分最高的答案块）。
_FOCUS_MODE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("_focus_mode", default=None)
# 本次调用用哪个 focus 打分器："" = 由操作者的 flag 决定（默认，行为与以前逐字节
# 相同）；"bm25" = 这一页明确要便宜的那条路；"neural" = 明确要语义分（仅在操作者
# 17.0：focus 打分引擎是纯按次选择（无许可门），别名只剩意愿侧判定。
# 这个 var 定义在 focus.py：crawl 也要按同一条规则判定，而 crawl 不能在模块级
# import server。这里只是别名，好让 reset token 与两个消费者读到同一个状态。
# 这三颗是给 server 再导出的（它按同一条规则判定 neural focus），context 自己一处不用。
from dhole_mcp.focus import (  # noqa: F401
    FOCUS_ENGINE as _FOCUS_ENGINE,
    engine_wants_neural as _neural_focus_requested,
    normalize_engine as _normalize_focus_engine,
)
# Opt-in: populate ResponseModel.media with the page's image URLs (multimodal).
_INCLUDE_MEDIA: contextvars.ContextVar[bool] = contextvars.ContextVar("_include_media", default=False)
_INCLUDE_LINKS: contextvars.ContextVar[bool] = contextvars.ContextVar("_include_links", default=False)
# Per-list cap for ResponseModel.links when include_links=true (web_read
# `max_links`; 0 = the documented per-category defaults in links.py). Links were
# capped silently at 30/20/20 with no way to ask for more or fewer and no signal
# that anything had been left out; links.total_found + links.is_truncated now
# report it, and this knob decides where the cut lands.
_MAX_LINKS: contextvars.ContextVar[int] = contextvars.ContextVar("_max_links", default=0)
# Per-call css_selector (web_read). Read by _with_agent_hints rather than at the
# extraction site, so a selector that could not apply to the response is reported on
# EVERY return path (fresh fetch, cache hit, bulk, archive) instead of only where
# extraction happened to run.
_CSS_SELECTOR: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_css_selector", default=None)
# Per-call cookie-jar id (web_read `session_id`, G7). "" = stateless, the
# pre-16.0 behaviour: cookies live for the one call and the response's
# Set-Cookie is dropped. Read by the HTTP tier (fetcher.HTTPSession.get) and by
# _stamp_session, which puts the id and the held cookie NAMES back on the
# result — values never go into the response (a session cookie is a credential,
# and tool output lands in the agent's transcript).
_SESSION_ID: contextvars.ContextVar[str] = contextvars.ContextVar("_session_id", default="")
# 17.0 §3-17: which cred_defaults this call was fetched with. A context var (not
# an argument) because the answer is decided once, at the top of web_read, and
# every return path — live fetch, cache hit, robots block, archive fallback —
# owes it to the caller. Bulk rows run in their own task, so each gets its own.
_IDENTITY_APPLIED: contextvars.ContextVar[str] = contextvars.ContextVar("_identity_applied", default="")
# A jar key is a plain word, nothing else (see _clean_session_id).
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}\Z")
# Per-call HTTP method / request body (G8). Read where the request is actually
# built (bulk_get) and by the guards that follow from it — cache, escalation,
# archive, quality heuristics — rather than threaded through six signatures.
# Default "GET" keeps every existing path byte-identical.
_METHOD: contextvars.ContextVar[str] = contextvars.ContextVar("_method", default="GET")
_BODY: contextvars.ContextVar[Optional[bytes]] = contextvars.ContextVar("_body", default=None)
_CONTENT_TYPE: contextvars.ContextVar[str] = contextvars.ContextVar("_content_type", default="")
# How robots was decided for THIS call, so the response can say it. Two reports
# asked for the same thing from opposite sides: a caller who could not tell
# "the site allows this" from "compliance is switched off process-wide", and an
# auditor who could not tell which of the two a fetched page came from.
_ROBOTS_MODE: contextvars.ContextVar[str] = contextvars.ContextVar("_robots_mode", default="")


def _stamp_robots_mode(result: ResponseModel) -> None:
    """Reconcile this response's robots receipt with the current process's mode.

    ``robots`` states a property of *this process* ("is compliance on here?"), so it
    cannot be frozen at the moment a body was first fetched. Both return paths need
    this: the fetch path stamps before the cache write, and the cache-hit path
    restores the whole metadata dict from the stored envelope — measured, a body
    cached under ``complied`` was served to a call that had passed
    ``ignore_robots=true`` and still reported plain ``complied``.

    When the two disagree, both are true of something, so both are said: the
    present first (that is what an auditor is asking about), the fetch-time
    permission named as history rather than as now.
    """
    mode = _ROBOTS_MODE.get()
    md = result.metadata or {}
    changed = False
    if mode:
        prior = md.get("robots")
        md["robots"] = (f"{mode} (this body was fetched under: {prior})"
                        if prior and prior != mode else prior or mode)
        changed = True
    # Same reasoning covers the SSRF posture: "was the DNS recheck skipped here?"
    # is a property of this process, not of the body. It is only said when it
    # actually happened, so an ordinary host pays nothing on the wire for it.
    from dhole_mcp import security as _sec
    note = _sec.recheck_posture_note()
    if note:
        md["ssrf_recheck"] = note
        changed = True
    if changed:
        result.metadata = md


def _stamp_browser_mode(result) -> None:
    """Say out loud when the browser tier would run **visibly** (批次 10).

    `fetcher_used` 只有一个 'stealthy'，它说不出这台是看得见的还是 headless 的，也说不出
    你刚登进去的东西会不会随临时目录一起没了 —— 而这两件事正是操作员按下
    `DHOLE_BROWSER_HEADED` 之后最想知道的。和 robots 那一颗同一个理由：说的是**本进程**
    的形态，所以两条返回路径都要过这里（抓取路径写缓存前先盖，缓存命中路径从存着的信封
    里读出来再重盖）—— 关掉旋钮之后，一条旧的"visible window"不许继续跟着正文走。
    """
    from dhole_mcp import browser as _b, identity as _id

    md = result.metadata or {}
    if not _b._browser_headed():
        if md.pop("browser", None) is not None:
            result.metadata = md
        return
    if result.fetcher_used != "stealthy":
        return
    prof = (_id.get("profile") or "").strip()
    md["browser"] = (
        f"visible window (DHOLE_BROWSER_HEADED), profile {prof!r} - logins typed "
        "here survive, and a visible browser is not closed by the idle reaper"
        if prof else
        "visible window (DHOLE_BROWSER_HEADED) on a THROWAWAY profile - nothing you "
        "log in here survives; store a `profile` with cred_defaults first (a visible "
        "browser is not closed by the idle reaper)")
    result.metadata = md
# Per-call credential headers (G25): ``(kind, header_names)`` — "basic"/"bearer"/
# "header" plus which header names this call treats as credentials. The names go
# to the HTTP tier so a cross-origin redirect drops them; the kind goes to the
# 401 hint, which has to say "the credentials you gave were refused" rather than
# "this page needs a login" when it is the former. Values themselves are kept
# only in the outgoing header — never in the response, never in a log.
_AUTH_SENT: contextvars.ContextVar[tuple] = contextvars.ContextVar("_auth_sent", default=("", ()))
# The verbs web_read speaks. OPTIONS/TRACE are left out on purpose: a fetch
# tool that proxies arbitrary methods to arbitrary hosts is a request forgery
# primitive with a nicer name.
_FETCH_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"})
# A request body is capped: this tool answers "what does this URL return", and a
# caller who meant to upload a file needs an upload tool, not a 40 MB payload
# riding through an MCP tool argument.
MAX_REQUEST_BODY_BYTES = 262144
# Request-context fingerprint for the content cache (see cache._cache_key).
# The SQLite cache is shared by every session on the machine and its key used to
# be "URL + extraction params" only, so a body fetched WITH cookies/auth was
# replayed to a later anonymous fetch of the same URL, and a fetch with
# include_media=false could answer a later include_media=true request. Requests
# that carry no credentials and change no flag keep the empty fingerprint, so
# plain fetches (and pre-existing cache entries) keep hitting as before.
_CACHE_CTX: contextvars.ContextVar[str] = contextvars.ContextVar("_cache_ctx", default="")
# 17.0 §6：条件请求不再是 agent 能传的参数，但 ETag 本身要继续用 —— 复验的发起者
# 已经存着基线，下一次检查真正想问的是"变了吗"，而不是再搬一遍正文。所以校验子走
# 这条进程内通道，由设置它的那次调用负责复位（try/finally）。刻意不做成参数：一个
# "请把你上次存的 etag 再传给我"的参数，等于把保管版本标记的责任推回调用方，而那
# 正是这次要删掉的合同。
_REVALIDATE: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_revalidate", default=None)

# Notes about THIS call's arguments that the caller has to be told about — right
# now, values that were clamped to a documented bound. Scoped per invocation by
# _smart_fetch_request_context exactly like _FOCUS, and folded into the response
# summary by _with_agent_hints. None (not an empty list) means "not inside a
# web_read call", which keeps the wrapper a no-op everywhere else.
_ARG_NOTES: contextvars.ContextVar[Optional[list]] = contextvars.ContextVar("_arg_notes", default=None)


def _clamp_note(name: str, requested: Any, applied: int, *, lo: int, hi: int) -> str:
    """The ONE wording for "this argument moved to a documented bound" ("" if not).

    Owned here because three layers emit it: `web_read` (through the ContextVar
    below), `web_crawl` (whose summary is assembled in crawl.py, not through
    `_with_agent_hints`), and the action/PDF/timeout clamps that live inside the
    tools that do the clamping. Two copies of a sentence is how the two tools
    started describing the same event differently — which is exactly the bug the
    crawl-side note was written to close.
    """
    if requested is None:
        return ""
    try:
        asked = int(str(requested).strip())
    except (TypeError, ValueError):
        # _coerce_int_arg already raised for anything unparseable.
        return ""
    if asked == applied:
        return ""
    return f"{name} clamped {asked}->{applied} (supported range {lo}-{hi})"


def _note_text(note: str) -> None:
    """Append one already-worded argument note, when a call is listening.

    For the bounds that are not a plain int-in-a-range: a PDF `pages='99-999'`
    against a 12-page file is clamped by the DOCUMENT, and the honest sentence
    names the page count rather than a 1-N range the caller could not have known.
    """
    notes = _ARG_NOTES.get()
    if notes is None or not note:
        return
    notes.append(note)


def _note_clamped(name: str, requested: Any, applied: int, *, lo: int, hi: int) -> None:
    """Record that a numeric argument was clamped to its documented bound.

    ``max_content_chars=499`` became 500 with no signal anywhere in the response,
    while the sibling search tool does tell the caller its ``max_results`` was
    clamped. Both bounds are on the wire, so the clamp is a known limit rather
    than a surprise — but "documented limit" is not the same as "you were told",
    and the caller here was measuring its own context budget.
    """
    if _ARG_NOTES.get() is None:
        return
    _note_text(_clamp_note(name, requested, applied, lo=lo, hi=hi))


@contextlib.contextmanager
def _arg_notes_scope():
    """Open a call's own argument-notes bucket, and close it on the way out.

    `web_read` gets this for free from `@_smart_fetch_request_context`. The other
    tools clamp too — an action's `wait` ms, a PDF's `pages=` range, a request's
    `timeout` — and a clamp recorded with no bucket open is a clamp that goes
    unannounced, which is the exact silence this file exists to end. Reset is
    explicit rather than inherited: a bucket left installed on a long-lived task
    would let one call's notes be printed on the next call's response.
    """
    token = _ARG_NOTES.set([])
    try:
        yield _ARG_NOTES.get()
    finally:
        _ARG_NOTES.reset(token)


def _cache_context(options: dict) -> str:
    """Short stable fingerprint of the request bits that change WHAT comes back.

    Pure function (unit-testable). Returns "" for a plain request so the default
    path is byte-identical to the pre-fix cache key.
    """
    import hashlib as _hashlib
    import json as _json
    bits: list[str] = []

    cookies = options.get("cookies")
    if cookies:
        try:
            bits.append("ck=" + _json.dumps(cookies, sort_keys=True, default=str))
        except Exception:
            bits.append(f"ck={cookies!r}")

    # 会话 jar（G7）：同一个 URL 在 session A 下（带着 A 的 cookie）取回的正文，
    # 不能回放给 session B —— 那是把一个人的登录态当公共内容端出去。和 cookies 同类：
    # 它改变「回来的是什么」，所以必须进指纹。
    session_id = options.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        bits.append(f"ss={session_id.strip()}")

    # 凭据（G25）：和 cookies/session 同一类 —— 它改变「回来的是什么」。少了这一位，
    # 用 admin 身份抓到的正文就会被之后的匿名请求回放出来（那是把别人的私有页面端给
    # 陌生人），而带不同 token 的两次调用会互相冒领。拼进指纹的字符串随即被 sha256
    # 截成 12 位，明文不落盘；能读到 cache.db 的人本来就能读到正文。
    auth = options.get("auth")
    if auth not in (None, "", {}, []):
        try:
            bits.append("au=" + _json.dumps(auth, sort_keys=True, default=str))
        except Exception:
            bits.append(f"au={auth!r}")

    # 条件请求：If-None-Match / If-Modified-Since 决定服务器答哪一个版本，和
    # cookies / auth 同一类，所以也要进指纹。17.0 之后没有公开发起者（唯一的那个
    # `watch` 已随四颗状态工具下线），校验子从 `_revalidate` 这一个键进来（由请求
    # 上下文注入，见 _smart_fetch_request_context）。
    # 条件请求本身不读也不写缓存（cache_ttl 被压成 0），这一位守的是反方向：一次带
    # 校验子的 200 不能被回放给之后没带校验子的调用。
    _cv = options.get("_revalidate") or {}
    if isinstance(_cv, dict):
        for _tag, _key in (("nm", "etag"), ("ms", "last_modified")):
            _v = _cv.get(_key)
            if isinstance(_v, str) and _v.strip():
                bits.append(f"{_tag}={_v.strip()}")

    headers = options.get("extra_headers")
    if headers:
        try:
            bits.append("h=" + _json.dumps(sorted(
                (str(k).lower(), str(v)) for k, v in dict(headers).items()
            )))
        except Exception:
            bits.append(f"h={headers!r}")

    useragent = options.get("useragent")
    if useragent:
        bits.append(f"ua={useragent}")

    proxy = options.get("proxy")
    if proxy:
        if isinstance(proxy, str):
            bits.append(f"px={proxy}")
        else:
            try:
                bits.append("px=" + _json.dumps(sorted(
                    (str(k), str(v)) for k, v in dict(proxy).items()
                )))
            except Exception:
                bits.append(f"px={proxy!r}")

    # PDF 口令：改变"能不能解出正文"，因此必须进指纹 —— 同一 URL 用口令解出来的
    # 正文，不能被之后的匿名请求回放。取的是值而不是布尔，因为不同口令解出的内容
    # 也不同（口令探测场景）。明文不进键：这里拼进去的字符串随后就被 sha256 截断，
    # 而能读到 cache.db 的人本来就能读到明文正文，口令并不构成额外的暴露类别。
    password = options.get("password")
    if isinstance(password, str) and password:
        bits.append(f"pw={password}")

    if options.get("include_media"):
        bits.append("im=1")
    if options.get("include_links"):
        bits.append("il=1")

    # Fetcher pin: it decides WHICH tier produced the body, so a pinned request
    # must never replay another tier's answer. It was missing from the key, so
    # force_fetcher="http" hit the stealthy entry written moments earlier and
    # came back cached=true / content_ok=true — the pin silently ignored. The
    # dangerous direction is the reverse: an http-tier JS shell (content_ok
    # false) gets cached, then an auto/stealthy request is served that bad body
    # from cache and never escalates.
    force_fetcher = options.get("force_fetcher")
    if force_fetcher:
        bits.append(f"ff={force_fetcher}")

    if not bits:
        return ""
    return _hashlib.sha256("|".join(bits).encode()).hexdigest()[:12]
