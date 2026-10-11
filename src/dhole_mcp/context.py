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

import asyncio
import contextlib
import contextvars
import re
import time
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
# 这次 focus **实际做成了什么**：{"dropped": [点名的结构性正文], "ignored": "为什么
# 一刀没切"}。`_apply_chunking` 从 `focus_content` 的 stats 里抄过来，`quality` 读它。
# 需要它的原因是那两处建议：`_agent_hints` 手上只有 `result`，而"用 focus= 再窄一点"
# 那句话与 focus 刚刚删掉的东西之间，隔着一个模块。
_FOCUS_OUTCOME: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("_focus_outcome", default=None)
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
# 页内重复样板行的折叠条数（boilerplate.py）。抽取时累加、`_with_agent_hints` 读取，
# 让"这次删了几行样板"在信封上可见 —— 静默删内容比留着样板更糟。默认 0 = 没删。
_BOILERPLATE_LINES_COLLAPSED: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_boilerplate_lines_collapsed", default=0)
# 本次 MCP 调用的 ServerSession（call_tool 入口存，调用结束清）。写门的
# out-of-band 确认（elicitation）靠它到达协议层的人机通道——放在 context.py
# 是因为 submit 闸在 actions.py，而 lower-layer 模块不许 import server。
_MCP_SESSION: contextvars.ContextVar[Optional[Any]] = contextvars.ContextVar(
    "_mcp_session", default=None)


def set_mcp_session(session: Optional[Any]) -> None:
    _MCP_SESSION.set(session)


# 这一次调用是**从哪一扇工具门进来的**（call_tool 入口存，调用结束清 —— 与
# `_MCP_SESSION` 同一处、同一条理由）。它存在的唯一原因是有一句建议要按它生成：
# "把 `anti_detect='off'` 传进来" 对 `web_read` 是一扇门，对 `web_request` 是一句
# `Unsupported argument(s)`（实测场景 9：一次写在 0 秒里被送回它自己收不下的参数）。
# 参数面本身不在这里复制 —— 读的人去查 `_TOP_LEVEL_ARGS` 与 `params._ENUM_ARGS`，
# 这颗只回答"名字是谁"。
#
# 空串 = 这一次没经过工具面（内部 bulk、直接调方法的测试与 evals）。那里没有"这一扇
# 门"这个概念，最接近的事实就是发起调用的那颗方法自己的签名，而仓库里的这些建议全部
# 生在 `web_read` 这条路上 —— 所以按 `web_read` 算，与 `_METHOD` 的 "GET" 默认值同一
# 条理由：默认值要让每条既有路一字不改。
_TOOL: contextvars.ContextVar[str] = contextvars.ContextVar("_tool", default="")


@contextlib.contextmanager
def tool_scope(name: str):
    """把"这次调用叫什么名字"交给下游，出去就摘掉（同 `call_deadline` 的规矩）。"""
    token = _TOOL.set(name or "")
    try:
        yield
    finally:
        _TOOL.reset(token)


# ─── The human channel's own clock ───────────────────────────────────────────
#
# `out_of_band_confirm` waits for a person to press a button. That wait used to
# have no ceiling of its own, so whichever ceiling the CALL had won first: with
# the actions tier's default 25 s budget an unanswered ask ended as
# "timeout: the 25000ms call budget ran out", the pending row was never written,
# and the advice pointed at `timeout=` / `force_fetcher='http'` / switch source
# —— none of which describes a button nobody pressed (measured on the real wire,
# `.scratch/liveverify/d35_silence.py`, and recorded as D-35). These two knobs
# give the ask a bound that is a fraction of what the call itself has left.
ELICIT_WAIT_MAX_S = 30.0
ELICIT_ASK_MIN_LEFT_S = 5.0

_CALL_DEADLINE: contextvars.ContextVar[Optional[float]] = contextvars.ContextVar(
    "_call_deadline", default=None)

#: How much later than the caller's own budget the `_within_call_budget` backstop
#: fires. See its docstring: it must lose the race to the tiers' own accounting.
#: 这颗数今天有**三处读者**：兜底自己（`server._within_call_budget`）、把预算说回给
#: 调用方的那句建议（`quality._budget_advice`），以及链里每一步自算的截止
#: （`actions._step_deadline_ms`）。作者只留一个，因为"交账要留多少"是一个事实而不是
#: 三个可调项 —— 步内那处从前没有读者，于是一步 10 秒的等待可以越过调用方点名的整笔
#: 预算，实测被掐在 60,000 那一次交回来的字节数是 0（体验模拟报告场景 10）。
BACKSTOP_GRACE_S = 1.5


def call_budget_left_s() -> Optional[float]:
    """这次调用还剩多少墙钟秒；不在预算作用域里时回 **None**。

    回 None 而不是回一个大数是有意的：下游拿它决定"这一步要不要收窄"，一个假的余额会
    把"没人点过预算"（CLI、直连 `dispatch`、预热）读成"预算很大"，而那句读错在报表上
    与真话长得一样。`elicit_wait_s` 在同一条边界上选的是另一个方向（那里按上限给，
    因为那时没有一条在跑的预算会被这次等待挤掉）—— 两处不同是各自的后果决定的，不是漂移。
    """
    deadline = _CALL_DEADLINE.get()
    if deadline is None:
        return None
    return deadline - time.time()


def elicit_wait_s() -> float:
    """这一次问人最多能等多久（秒）。0 = 现在问，也没有地方放答案。

    规则只有两条，都跟着**这次调用还剩多少**走：不超过 `ELICIT_WAIT_MAX_S`（一句
    弹窗话读完按下按钮的时间），也不超过剩余预算的一半（剩下那一半要走完剩下的动作
    并把信封交回去，问人不能把这次调用的时间吃掉大半）。为什么不写死一个数：默认
    预算实测只有 25 秒（`ACTIONS_DEFAULT_TIMEOUT_MS`），写死 60 秒等于让调用预算先
    炸 —— 那正是 D-35 量到的形状；而一次 120 秒的调用只给 12 秒，是把人赶出弹窗。

    没有截止日期可问（CLI、直连 dispatch、工具入口的那道 CDP 先问再记）时按上限给，
    因为那时没有一条在跑的预算会被这次等待挤掉。
    """
    deadline = _CALL_DEADLINE.get()
    if deadline is None:
        return ELICIT_WAIT_MAX_S
    left = deadline - time.time()
    if left < ELICIT_ASK_MIN_LEFT_S:
        return 0.0
    return min(ELICIT_WAIT_MAX_S, left / 2.0)


@contextlib.contextmanager
def call_deadline(seconds_from_now: float):
    """把"这次调用还有多少墙钟"交给下游，出去就摘掉。

    与 `_arg_notes_scope` 同一件事的另一半：按次的东西必须显式摘，长命的 task 上留
    一颗截止日期，下一次调用就会按上一次的预算决定要不要问人。
    """
    token = _CALL_DEADLINE.set(time.time() + max(0.0, float(seconds_from_now)))
    try:
        yield
    finally:
        _CALL_DEADLINE.reset(token)


async def out_of_band_confirm(message: str) -> Optional[bool]:
    """把"问用户"从模型签名的参数搬到协议层的人机通道（MCP elicitation）。

    三态语义（批次8 刀1，F-D-J1）：
      ``None``  本次调用没有 MCP 会话，或会话没声明 elicitation 能力 ——
                各扇门保持既有参数行为，一字不改（CLI、直连 dispatch、旧宿主）。
      ``True``  人在弹窗上点了接受。
      ``False`` 人拒绝 / 取消，或问不到人（NoBackChannelError 等任何应答失败），
                或者到了期限还没人答（`elicit_wait_s`）—— 三种都 fail-closed：
                调用方必须拒答，**绝不回落参数 confirm**，否则注入方制造一次
                elicitation 故障（或只是一台没人的键盘）就把门推回模型手里。

    按连接协商、不按次：能力为真的连接上 confirm 参数不参与开门（照问），
    避免同一连接两种门混用。requested_schema 为空：只借 accept/decline/cancel，
    不向用户收字段。
    """
    session = _MCP_SESSION.get()
    if session is None:
        return None
    from mcp.types import ClientCapabilities, ElicitationCapability
    try:
        capable = session.check_client_capability(
            ClientCapabilities(elicitation=ElicitationCapability()))
    except Exception:
        # 能力读不出来 = 客户端没能成功声明它，按未声明走参数门。
        return None
    if not capable:
        return None
    from mcp.shared.exceptions import NoBackChannelError
    # 空表单的最小合法形状：不带任何字段，只借 accept/decline/cancel。
    # （裸 {} 会被客户端侧的请求校验拒成 Invalid request parameters —— 实测。）
    wait = elicit_wait_s()
    if wait <= 0:
        # 这次调用已经没地方放一个答案了。不问了，与问不到人同权 —— 一发几秒的弹窗
        # 不是批准，是一次必然的到期，而它的代价（整条回执归错因）正是要避免的那件事。
        return False
    try:
        result = await asyncio.wait_for(
            session.elicit_form(
                message, {"type": "object", "properties": {}}),
            timeout=wait)
    except NoBackChannelError:
        return False
    except TimeoutError:
        # 期限到了没人答。这一支与 decline 同一条出路：调用方拿到的是 pending 行，
        # 而不是"这一档超时了"（D-35 —— 慢的不是网络，是没按的那颗按钮）。
        return False
    except Exception:
        # 其余应答失败同权：问不到人的门不能开。CancelledError 不在此列
        # （BaseException），取消就该取消。
        return False
    return getattr(result, "action", None) == "accept"


async def report_progress(progress: float, total: float | None = None,
                          message: str | None = None) -> bool:
    """把"这件事走到哪了"送给正在等的那位客户端（MCP notifications/progress）。

    与 `out_of_band_confirm` 共用同一根管子：会话在 call_tool 入口存进
    `_MCP_SESSION`，调用结束摘下。所以 CLI、直连 `_dispatch`、测试里都是 None ——
    那时什么都发不出去，也**不该**发，返回值只是"这次送出去了没有"。

    这颗不做能力判断：SDK 的 `ServerSession.report_progress` 自己就是"调用方没带
    progressToken 时 no-op"（实测如此），照它再判一遍只会多一处会写错的地方。

    任何发送失败都吞掉并返回 False。**一条进度通知不许弄坏正在跑的抓取** —— 这条
    通道是给等待的人看的，不是任务的一部分；客户端不支持、连接半断、序列化出错，
    后果都只能是"这一格没说出去"。取消（BaseException）照旧向上走，不在吞的范围内。
    """
    session = _MCP_SESSION.get()
    if session is None:
        return False
    try:
        await session.report_progress(progress, total=total, message=message)
    except Exception:
        return False
    return True


def stage_message(stage: str, spent_ms: Optional[float] = None,
                  show_left: bool = True) -> str:
    """一句阶段话：这一档在做什么 +（知道才说）已经花了多少 +（点了预算才有）还剩多少。

    作者只留一个，因为三处发射（档位跃迁、起浏览器、等 network-idle 稳定）说的是同一件事
    的三个时刻，各写一遍就会各自漂。**剩余一律从 `call_deadline` 那颗 ContextVar 现读**，
    不是参数 —— 让调用方自己估一个"还剩 30000ms"，正是这一族最便宜的谎。读不到预算
    （CLI、直连 dispatch、预热）时那半句整个缺席，而不是补一个看起来合理的数。

    `show_left=False` 不是关掉了算术，是关掉一次误导：首用的那一发下载走的是
    `_apply_install_shift` 事后**还给预算**的那段时间（装机成本不是这个页面的响应
    时间），此刻读到的剩余往往是 0，印出来就成了"0ms 还剩，而我要开始下一场几分钟"。
    """
    parts = [stage]
    if spent_ms is not None:
        parts.append(f"spent {int(round(spent_ms))}ms")
    left = call_budget_left_s() if show_left else None
    if left is not None:
        parts.append(f"{int(round(max(0.0, left * 1000)))}ms left in this call's budget")
    return " | ".join(parts)


async def report_stage(stage: str, spent_ms: Optional[float] = None,
                       *, show_left: bool = True) -> bool:
    """发一句阶段话，走**两条**通道：`notifications/progress` 与 `notifications/message`。

    progress 用"已花的秒数"（知道才给，否则 0 —— 这一类等待没有分母，编一个 `total` 就是
    编一个百分比）。为什么还要第二条：宿主对这两条的支持不重合 —— 只实现 logging 的宿主
    在 progress 上什么都不显示，那一句"这一等要几分钟"就又只剩开发者看得见了。

    两条各自 fail-soft：任何一条送不出去都不许弄坏正在跑的抓取，也不影响另一条。都不出
    去时返回 False（调用方不读这个返回值做事，它是一条消息的可观测性，不是任务的一部分）。
    SDK 在两条上都已经自己判过能力（没带 progressToken / `_meta` 的 log-level 键时 no-op），
    这里不重复判 —— 判第二遍就是第二个会写错的地方。
    """
    message = stage_message(stage, spent_ms, show_left)
    session = _MCP_SESSION.get()
    if session is None:
        return False
    sent = False
    try:
        await session.report_progress(round((spent_ms or 0.0) / 1000.0, 3),
                                      message=message)
        sent = True
    except Exception:
        pass
    try:
        await session.send_log_message("info", message, logger="dhole.stage")
        sent = True
    except Exception:
        pass
    return sent

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

# WHO took dhole's synthesized browser headers away for this call: "" = nobody did,
# "off" = the `anti_detect='off'` rung, "private" = dhole took them away itself
# because the target is loopback/private. They share one slot because the place that
# says the sentence (inside the HTTP tier) only ever sees the single boolean
# `stealthy_headers` — and that boolean cannot tell "the rung was off all along"
# apart from "this call is hitting your own service, so a fabricated google referer
# would land in your logs". Scoped by _smart_fetch_request_context like _ARG_NOTES.
_HEADERS_OFF_BY: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_headers_off_by", default="")


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


def _cache_ttl_note(requested: Any) -> str:
    """The ONE wording for "this ``cache_ttl`` was not a duration" ("" if it was).

    Three tools take ``cache_ttl`` (``web_read`` / ``web_crawl`` / ``web_search``)
    and all three silently treated a negative value as 0. The wire documents
    "0 = force fresh" and says nothing about negatives, so a caller who wrote -1
    had no way to learn it had been rewritten — while the same suite clamps
    ``max_pages`` / ``max_depth`` and *says so* in the summary. 2026-10-09 审计
    D5/D10 量到的就是这颗唯一的静默钳位。

    Owned here for the same reason :func:`_clamp_note` is: two copies of one
    sentence is how two tools start describing one event differently. The
    sentence also carries the half of ``0`` that the wire never states — a ``0``
    this call writes nothing back to the cache either, which is what makes a
    later default-TTL call fetch again.
    """
    if requested is None:
        return ""
    try:
        asked = int(str(requested).strip())
    except (TypeError, ValueError):
        return ""
    if asked >= 0:
        return ""
    return (f"cache_ttl={asked} is not a duration; using 0 - this call reads no "
            f"cache and writes none")


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
