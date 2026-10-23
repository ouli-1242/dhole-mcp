"""Dhole 的参数面：调用方传来的 JSON 在进任何一条抓取路径之前读一遍。

从 server.py 原样搬出（17.0 B6 的第七刀下半，零行为变更）。这一层只回答两个问题：
"这个值能不能被读成它声称的类型"（`_coerce_*`：客户端会把数字发成字符串，
会把 false 发成 "false"），以及"这个工具到底收不收这个名字"（`_ARG_TYPES` 加
`_validate_tool_args`）。答案必须是同一条：一处放宽一处收紧，就是"工具描述里
写着能传、传进来被静默改回默认"那一类缺陷的产地。

依赖只有 stdlib、档位表 `_ANTI_DETECT` / `_RUNG_TOOLS`（rungs.py）与一处对
`MasterFetchServer` 的回看 —— 那颗是刻意的：`_arg_defaults` 从方法签名现读默认值，
抄来的默认值会漂，而签名是工具已经写明它们的地方。

越界：钳还是拒（全仓唯一一条判据；2026-10-09 审计 D5 / C16 收口）
--------------------------------------------------------------------
C16 记的是"同类参数的验证策略三分裂：拒绝 / 钳制并说明 / 静默强转三者混用"。判据
只有一句，写在这里因为分界就在这一层（`_coerce_int_arg(..., clamp=)`），而其余每一颗
自钳的参数（`search.max_results` 的 `max(1, min(x, 50))`、crawl 的四颗上限）判的也是
同一件事：

  **越界的那个值，有没有唯一一种合理的读法。**

* **有** —— 并且那个界是 wire 上写明的资源帽（"你要的比允许的多"）→ 钳到边界，
  并且**点名**（`context._clamp_note` / `_note_clamped`，落进 `summary`）。
  例：`max_content_chars`、`max_links`、`max_pages`、`max_depth`、`concurrency`、
  `max_total_chars`、`cache_ttl<0`、`max_results`、`min_relevance`、
  `min_raw_relevance`、动作的 `wait` / `scroll.steps`。
* **没有** —— 猜一下就把这颗参数变成了**另一个意思** → **具名拒绝**（`_refuse`；
  走到协议边界折成 invalid_request 信封，见
  `test_a_designed_refusal_never_leaves_naked_through_the_transport`）。
  例：`offset=-5`（Python 的负数切片从末尾数，钳到 0 等于把"最后 5 个字"当成"从第 0
  个字开始"发回去 —— 一个错的正文以普通 200 交出去）、`page=99`（"第 99 页"不是
  "第 10 页"）。

两条伴生纪律，缺一条这条判据就白写：

* **钳必须点名** —— 钳了不说，调用方以为自己点的那个数生效了。那正是审计 D5 原来
  记的形状（"静默强转"）；现在这一类一条都复现不出来，守卫在
  `test_every_clamp_is_announced.py`。
* **拒必须具名** —— 拒绝要说清哪颗参数、它的范围、以及正确的写法（`_refuse` 的
  `next_action` 那一句是共用的，理由见 `_TYPE_REFUSAL_WAY_OUT`）。

三种策略里的第三种 —— **静默强转** —— 在本仓没有合法用法：一颗越界的数只许走上面
两条之一。
"""

from __future__ import annotations

import inspect
import json
from typing import Any, List, Optional

from dhole_mcp.rungs import _ANTI_DETECT, _RUNG_TOOLS


class ArgumentRefusal(ValueError):
    """一句写完了的「你这参数不对」，走到协议边界才折成信封。

    它是 ValueError 的子类而不是新分支：`_dispatch` 那一层照旧抛（CLI 读异常，全仓
    十处 `raises(ValueError, match=...)` 的锁一字不动），只有 `call_tool` 认识它 ——
    那里把它折成 `_invalid_request_result`，于是 agent 拿到的是 summary / content_ok /
    next_action 齐全的信封，而不是 transport 兜底的裸 ``{"error": str(e)[:300]}``。

    兜底的注释写着「走到这里说明这不是 dhole 设计过的拒绝」。2026-10-08 的全参数反类型
    扫（111 颗参数各喂一个错类型的值，走真 SDK 回路）证伪了它：30 颗参数的句子本来就写完了
    —— `confirm must be a boolean, got 'one'. Pass true or false (default false).` ——
    却整条只回一个裸 `{"error": ...}`。与刀1 同一类，只是换了一整个出口面：刀1 收的是
    陌生参数名与动作形状，这一层收的是共用的类型归一化本身。
    """

    def __init__(self, msg: str, next_action: str = ""):
        super().__init__(msg)
        self.next_action = next_action


class ToolRosterRefusal(ValueError):
    """一句写完了的「这一扇门不存在」，走到协议边界才折成信封（评审 R02-130 · F123）。

    与 :class:`ArgumentRefusal` 同一套分工：`_dispatch` 照旧抛（三处
    `raises(ValueError, match="Unknown tool")` 的旧锁一字不动），只有 `call_tool` 认识它，
    于是 agent 拿到的是申报 / summary / 把手齐全的信封，而不是 transport 兜底那副
    ``{"error": str(e)[:300]}``。兜底自己的注释写着「走到这里说明这不是 dhole 设计过的拒绝」,
    而这两发（门名不在名册里 / 门被 `DHOLE_TOOLS` 关掉）恰恰是设计过的 —— 量到的形状见
    `review/.runtime/p1/r337_two_faces_pre_fix.json` 与 `r338_boundary_faces.json`：
    `is_error=True` 加一格 `error`，没有申报、没有 summary、没有 code（分类器对大写开头的
    这句交 `('', False, '')`，所以今天不是"一个错名字"，是"没有名字"），而 300 那一刀在旧名
    带 hint 的那一发上剪掉的正是"新家怎么写"那一段。

    `next_action` 在**抛出的那一刻**按门取词（`_nothing_went_out(name)`）：被关掉的
    `web_read` 说的是它自己的动词，而不是默认支那句 —— 等到边界再取，`tool_scope` 早已随
    异常退出，整族只会剩下 `Nothing was fetched or changed.`。
    """

    def __init__(self, msg: str, next_action: str = ""):
        super().__init__(msg)
        self.next_action = next_action


#: 每一颗 `_coerce_*` 的拒绝共用这**半句**：句子前半是"改哪一颗、改成什么是错的"已经写在
#: `error` 里（各颗的说法本来就各不相同，不该在出路里重讲一遍），后半是"为什么门要拦
#: 而不是猜" —— 那是这一层存在的全部理由，也是调用方最需要听的一句。
#:
#: 开头那半句「这一发什么也没发生」从前也焊在这里（`Nothing was fetched or changed.`），
#: 于是这一层在全部 11 扇门上只有一种说法 —— 评审 R02-089：那一扇只读的 `doc_parse` 与
#: 会写服务器的 `web_request` 听到的都是同一句"抓过或改过"。现在那半句由 `context.
#: nothing_went_out()` 按门取，本体仍是这一位作者，两半在这里拼一次。
_TYPE_REFUSAL_BODY = (
    "The argument named at the start of `error` is the one to rewrite, and `error` "
    "states the shape it takes. Types are checked at the door rather than guessed at: "
    "a misread `urls` would otherwise come back looking like a successful 19-URL "
    "fetch, and a string `confirm` would look like an approval.")


def _refuse(msg: str) -> ArgumentRefusal:
    """每一颗 `_coerce_*` 的拒绝都从这里出：句子各处不同，出路这一句按门取词后共用。"""
    from dhole_mcp.context import nothing_went_out
    return ArgumentRefusal(msg, next_action=f"{nothing_went_out()} {_TYPE_REFUSAL_BODY}")


def _coerce_int_arg(
    value: Any, name: str, *, lo: Optional[int] = None, hi: Optional[int] = None,
    default: int, clamp: bool = True, allow_float: bool = False,
) -> int:
    """Normalize an integer tool argument; never silently swap in the default.

    ``max_content_chars`` did exactly that: a non-int was replaced by
    ``MAX_CONTENT_CHARS``. Since several MCP clients stringify numbers (the same
    JSON-stringified clients need parsed), ``max_content_chars="2000"``
    silently became 40,000 — a 20x context overspend delivered as an ordinary
    200. That is the "looks like it worked" family already closed one level up
    by ``_coerce_arg_types`` and ``_normalize_schema``.

    Policy, in order:
      * ``None`` -> ``default`` (the argument was not set)
      * a bool -> raise (``True`` is an ``int`` subclass; nobody means 1 char)
      * a string that parses as an int -> coerce, honouring the caller's intent
      * anything else -> raise, naming the type it actually was
      * out of ``[lo, hi]`` -> clamp when ``clamp`` (a resource cap, which is
        documented on the wire so it is a known bound rather than a surprise),
        otherwise raise (a nonsense value with no sensible reading).

    ``clamp`` is the project's reject-vs-clamp boundary, so the decision it
    encodes is written down once, in this module's docstring ("越界：钳还是拒"):
    clamp when the out-of-range value still has ONE sensible reading and the
    bound is a documented resource cap; refuse when honouring a guess would
    change what the argument MEANS (``offset=-5`` would otherwise come back as
    "start at 0", i.e. a wrong body delivered as an ordinary 200). Whichever way
    it goes, the caller is told - a clamp is named in ``summary``, a refusal
    names the argument, its range and the correct spelling.

    ``lo``/``hi`` are optional because the wire-boundary pass
    (:func:`_coerce_arg_types`) converts types ONLY and leaves the range to the
    tool: every documented cap is already applied where it is enforced, with the
    note that tells the caller it was applied. Repeating the bounds here would
    clamp in silence one layer above that note.

    ``allow_float`` accepts a float as well as an int, for the arguments whose
    own annotation is ``int | float`` (timeouts, waits) — where 1500.5 ms is a
    value the caller meant, not a type error.
    """
    if value is None:
        return default
    # Every rejection below points at a concrete correct value. For an argument
    # whose own default is None ("not set") there is no number to suggest, so the
    # suggestion is to omit it.
    example = (f"e.g. {name}={default}" if default is not None
               else f"or omit {name} to use the tool default")
    if isinstance(value, bool):
        raise _refuse(
            f"{name} must be an integer, got a boolean. "
            f"Pass {name}={int(value)} ({example})."
        )
    number_types: tuple = (int, float) if allow_float else (int,)
    if isinstance(value, str):
        text = value.strip()
        try:
            value = int(text)
        except ValueError:
            if not allow_float:
                raise _refuse(
                    f"{name} must be an integer, got {value!r}. "
                    f"Pass a number ({example})."
                ) from None
            try:
                value = float(text)
            except ValueError:
                raise _refuse(
                    f"{name} must be a number, got {value!r}. "
                    f"Pass a number ({example})."
                ) from None
    if not isinstance(value, number_types):
        raise _refuse(
            f"{name} must be {'a number' if allow_float else 'an integer'}, got "
            f"{type(value).__name__}. Pass a number ({example})."
        )
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        if clamp:
            if lo is not None:
                value = max(lo, value)
            if hi is not None:
                value = min(hi, value)
            return value
        raise _refuse(
            f"{name} must be between {lo} and {hi}, got {value}."
        )
    return value


# The strings a client can legitimately send for a boolean. Listed explicitly
# because the defect this closes was caused by truthiness: `if all:` treats ANY
# non-empty string as True, so cache_clear(all="false") wiped the whole cache.
# A whitelist is the only version of this that cannot be wrong in that
# direction — an unlisted string raises instead of guessing.
_BOOL_LITERALS: dict[str, bool] = {
    "true": True, "1": True, "yes": True, "on": True,
    "false": False, "0": False, "no": False, "off": False,
}

# Fetcher tiers accepted on the wire. 'dynamic' (the alias left over from the
# Playwright tier removed in v3.5.0) is gone as of 17.0 B4: two names for one
# tier is a vocabulary the reader has to reconcile on every response, and the
# escalation_path it produced said 'stealthy' anyway.
_FORCE_FETCHER_VALUES = frozenset({"http", "stealthy"})


def _coerce_bool_arg(value: Any, name: str, *, default: bool = False) -> bool:
    """Normalize a boolean tool argument; never let truthiness decide.

    Same policy as ``_coerce_int_arg``: ``None`` means "not set" (-> ``default``),
    a real ``bool`` passes through, ``0``/``1`` pass through as ``False``/``True``
    (how a number-typed client spells them), and a string is honoured only when
    it is one of ``_BOOL_LITERALS``. Everything else raises, naming what actually
    arrived.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    # 0/1 are how a number-typed client says false/true. They have exactly one
    # reading, so they are honoured; any other int is a mistake and raises
    # (unlike _coerce_int_arg, which rejects bools outright because `True` there
    # would silently mean "1 character").
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _BOOL_LITERALS:
            return _BOOL_LITERALS[key]
        raise _refuse(
            f"{name} must be a boolean, got {value!r}. "
            f"Pass true or false (default {str(default).lower()})."
        )
    raise _refuse(
        f"{name} must be a boolean, got {type(value).__name__}. "
        f"Pass true or false (default {str(default).lower()})."
    )


def _coerce_str_list_arg(
    value: Any, name: str, *, single_hint: str = "",
    element_hint: str = "a URL string",
) -> Optional[List[str]]:
    """Normalize a list-of-strings argument; a bare string is an error.

    A string IS iterable, which is why ``web_read(urls="https://example.com")``
    used to answer with ``total=19, successful=19`` and nineteen one-character
    "results" — the bulk path took ``len(urls)`` and iterated it, and nothing
    downstream could tell that apart from a real 19-URL request.

    Wrapping the string would be a guess (is a comma a separator? a newline?),
    so it raises and names the argument that already means "one URL". A
    JSON-encoded array is accepted because several clients stringify nested
    structures (the same reason a JSON-stringified value gets parsed) and an array
    literal has exactly one reading.

    Elements are checked too: ``urls=["https://a", 123]`` used to reach
    ``ResponseModel(url=123)`` and come back as a bare Pydantic
    "1 validation error for ResponseModel" with no recovery hint.
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        parsed = None
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
        if isinstance(parsed, list):
            return _coerce_str_list_arg(parsed, name, single_hint=single_hint,
                                        element_hint=element_hint)
        raise _refuse(
            f"{name} must be an array of strings, got a single string. "
            + (single_hint or f'Pass {name}=["...", "..."] instead.')
        )
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for i, item in enumerate(value):
            if not isinstance(item, str):
                raise _refuse(
                    f"{name}[{i}] must be a string, got {type(item).__name__} "
                    f"({item!r}). Every element of {name} has to be {element_hint}."
                )
            out.append(item)
        return out
    raise _refuse(
        f"{name} must be an array of strings, got {type(value).__name__}. "
        + (single_hint or f'Pass {name}=["...", "..."] instead.')
    )


def _coerce_anti_detect_arg(value: Any, name: str = "anti_detect") -> str:
    """The one rung the caller named, or 'auto' - and nothing in between.

    `Literal[...]` reaches clients as schema only; this server reads every
    argument with `args.get()`, so an unlisted level would otherwise reach the
    ladder table and raise a KeyError there - a Python traceback where a list of
    the four spellings belongs.
    """
    if value is None:
        return "auto"
    if isinstance(value, str) and value.strip().lower() in _ANTI_DETECT:
        return value.strip().lower()
    raise _refuse(
        f"{name} must be one of {', '.join(_ANTI_DETECT)}, got {value!r}. "
        "The rungs go weakest to strongest; there is no fifth, and no single "
        "fingerprint knob to override."
    )


def _coerce_force_fetcher_arg(value: Any, name: str = "force_fetcher") -> Optional[str]:
    """Validate the force_fetcher enum instead of falling through to stealthy.

    ``Literal[...]`` on the method signature is documentation only — the
    dispatcher reads arguments with ``args.get()``, so ``force_fetcher="magic"``
    used to miss every ``== "http"`` branch and land in the stealthy ``else``:
    the heaviest tier (~5s plus anti-detect overhead) with no error saying the
    value was wrong. Returns ``None`` for "not set".
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _refuse(
            f"{name} must be a string, got {type(value).__name__}. "
            "Valid values: 'http', 'stealthy'."
        )
    key = value.strip().lower()
    if key == "dynamic":
        # Named rather than folded into "not a fetcher tier": a caller working
        # from old notes needs to be told where that name went, not asked to
        # guess between the two tiers that are left.
        raise _refuse(
            f"{name}='dynamic' was the alias of the browser tier that left in "
            "v3.5.0, and the alias itself is gone in 17.0 - pass "
            "force_fetcher='stealthy' (anti-detect browser) or 'http'. "
            "No request was made."
        )
    if key not in _FORCE_FETCHER_VALUES:
        raise _refuse(
            f"{name}={value!r} is not a fetcher tier. "
            "Valid values: 'http', 'stealthy'."
        )
    return key


# The two tools that read a fetcher tier. web_crawl took force_fetcher straight
# to the tier ladder with no boundary check, so `force_fetcher="hpt"` on a crawl
# was the web_read defect (`_coerce_force_fetcher_arg`'s docstring) with the
# whole site as the blast radius: it missed the `== "http"` branch and browsered
# every page.
_FORCE_FETCHER_TOOLS = frozenset({"web_read", "web_crawl"})


# ─── 枚举参数：合法值表 + 边界校验 ──────────────────────────────────
# 17.0 之前，"这一串里挑一个"这件事只写在线格式的 `enum` 数组里，于是非法值由
# **宿主**在进入 dhole 之前拒掉，而拒本文案不列合法值（实测原话："must be equal to
# one of the allowed values"）。摘掉 enum 之后，这句话必须由 dhole 自己说得出 ——
# 所以值集留在这里，校验也留在这里。
#
# 表与工具签名是同一条规则的两个副本（`ExtendedExtractionType` /
# `ReadExtractionType` / `focus.ENGINES`），所以有一对相等断言钉着它们
# （tests/test_the_enum_argument_values_are_checked_by_dhole.py）：漂了就是
# "描述里写着能传、传进来被读成不传"那一类缺陷的重新开张。
_EXTRACTION_TYPES = ("markdown", "html", "text", "article", "structured")
_READ_EXTRACTION_TYPES = _EXTRACTION_TYPES + ("check",)
_FOCUS_MODES = ("filter", "best")
_FOCUS_ENGINES = ("bm25", "neural")

_ENUM_ARGS: dict[str, dict[str, tuple[str, ...]]] = {
    "web_read": {
        "extraction_type": _READ_EXTRACTION_TYPES,
        "focus_mode": _FOCUS_MODES,
        "focus_engine": _FOCUS_ENGINES,
    },
    "web_act": {"extraction_type": _EXTRACTION_TYPES},
    "web_request": {"extraction_type": _EXTRACTION_TYPES},
    "web_crawl": {"focus_engine": _FOCUS_ENGINES},
    # web_search's `mode` and `freshness` are deliberately NOT here: search.py
    # rejects both before its first network call (`_validate_mode`,
    # `_validate_freshness`), and each refusal already prints the whole value set
    # — `Invalid mode: 'bogus' (auto|neural|find_similar)`. A second table here
    # would be a copy that can drift from `_IMPLEMENTED_MODES`, which is the
    # "bound repeated above the note that announces it" shape this module already
    # warns about.
}

# 只有"值在别的工具里合法、在这颗工具不合法"才需要额外一句：同一份笔记从 web_read
# 搬到 web_act 是最常见的一条路，而 `check` 恰恰是五颗里唯一长得不像格式的那颗。
# 键是 (工具, 参数)：对 web_read 自己说"'check' 是 web_read 的"是一句疯话。
_ENUM_NOTE = (
    "'check' (the changed-since-last-fetch receipt) is web_read's; the five above "
    "are the read-back formats this tool has."
)
_ENUM_NOTES: dict[tuple[str, str], str] = {
    ("web_act", "extraction_type"): _ENUM_NOTE,
    ("web_request", "extraction_type"): _ENUM_NOTE,
}


def _coerce_enum_arg(value: Any, name: str, allowed: tuple[str, ...],
                     note: str = "") -> Optional[str]:
    """One of the named values, or a refusal that prints them all.

    Read raw, an unlisted spelling never raised: every consumer compares the
    literal (`mode == "find_similar"`, `extraction_type in _EXTRACTED_TYPES`,
    `normalize_engine`), so a typo fell through to whichever branch "not equal"
    leads to — `mode="blurry"` searched, skipped the reranker, and reported
    `rerank_mode=merge` as if that had been asked for. While the wire `enum`
    existed this could not be reached; it is reachable now, which is the whole
    reason this function does.

    Policy mirrors ``_coerce_force_fetcher_arg``: ``None``/``""`` mean "not set"
    (return ``None``; ``_forward`` drops it so the method's own default applies),
    surrounding space and capitalization are the caller's noise rather than a
    different value, and anything else raises naming what actually arrived.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise _refuse(
            f"{name} must be a string, got {type(value).__name__}. "
            f"Valid values: {', '.join(allowed)}."
        )
    key = value.strip().lower()
    if key not in allowed:
        raise _refuse(
            f"{name}={value!r} is not a value this tool accepts. "
            f"Valid values: {', '.join(allowed)}."
            + (f" {note}" if note else "")
            + " No request was made."
        )
    return key


# ─── wire argument types ───────────────────────────────────────────
# web_read and cache_clear had per-argument coercers; the other six tools had
# none, so everything the dispatcher forwarded was whatever JSON happened to
# carry. Two client behaviors reach that gap on ordinary machines:
#
#   * a client that serializes numbers ("max_results": "6"). search.py then ran
#     `max(1, min("6", 50))`, and min() compares the SECOND operand against the
#     first: `'<' not supported between instances of 'int' and 'str'`. The tool
#     answered with that bare Python traceback as its `error` field, which is
#     what an external test report filed as "web_search 完全崩溃，重启不恢复"
#     and misdiagnosed as engine-cooldown state. The state files were innocent —
#     the same call succeeds once the number arrives as an int.
#   * a client that echoes every schema property as null. A null means "not
#     set", so it is dropped before any branch reads it - but the drop has to
#     be deliberate: `if cache_ttl > 0` on a None raises a NoneType comparison,
#     and letting the tool's own default apply is what the caller meant.
#
# A bare string where a list was meant is the same gap with a worse outcome:
# `path_include="/docs"` iterates character by character.
#
# So: convert TYPES at the boundary, for both channels, before any branch reads
# them. Ranges deliberately are NOT checked here — every documented cap is
# already enforced where it is applied (search clamps max_results and says so,
# crawl clamps max_pages, _validate_filters rejects page>10), usually with a note
# telling the caller the value moved. Clamping a second time above those notes
# would produce the silent version of the bug this table exists to close.
_ARG_TYPES: dict[str, dict[str, str]] = {
    "web_read": {
        "urls": "list",
        "include_media": "bool",
        "include_links": "bool", "ignore_robots": "bool",
        "cache_ttl": "int", "offset": "int", "max_content_chars": "int",
        "max_links": "int",
        "focus_mode": "str",
        "focus_engine": "str",
        # The two booleans that make a decision rather than ask for data. Read
        # raw, a string client's `confirm: "false"` is a truthy non-empty string
        # and authorizes a CDP attach nobody approved — the same defect
        # sys_state's `confirm` already fell into once.
        "confirm": "bool", "check_login": "bool",
        "timeout": "number",
        # W1：`headers=` 是键值对，不是字符串。字符串客户端把整段 JSON 发过来是常态
        # （`_coerce_dict_arg` 收下来），其余形状当场点名 —— 一个读成 `{"a": "b"}`
        # 之外的东西的调用，宁可拒绝，不要"安静地什么头都没带"。
        "headers": "dict",
        # A numeric PDF page request ("pages": 3) is unambiguous; reading it only
        # as a string used to drop the range silently and return the whole file.
        "pages": "strnum", "password": "strnum",
        # R3：`include_headers` 决定"这一次要不要交出证据"，而字符串客户端的
        # `"false"` 按裸值传是非空字符串 = 真 —— 于是调用方说了不要，回执里却多出一
        # 份响应头，而且这一次调用**绕开了缓存**（打开它就不吃缓存）。它不看任何
        # 别的东西，只是被读成真，所以后果是"多花了钱、多了一份没人要的证据"。
        "include_headers": "bool",
        # R4：落盘那两颗。`save_to` 是路径（字符串），空串与空白在这里就是"没给"，
        # 由 `web_read` 门口具名拒掉；`max_save_bytes` 是数字，字符串客户端把 256MB
        # 写成 "268435456" 是常态，而按裸字符串比较会当场 TypeError。
        "save_to": "str", "max_save_bytes": "int",
    },
    "web_crawl": {
        "max_pages": "int", "max_depth": "int", "max_content_chars_per": "int",
        "max_total_chars": "int",
        "cache_ttl": "int",
        "concurrency": "int", "timeout": "number", "deadline_ms": "number",
        "ignore_robots": "bool",
        "focus_engine": "str",
        # path_include/path_exclude are NOT here: a bare string is already read
        # as the one-prefix list it means, and crawl.py does that at the point
        # where it can still say so before any page is fetched.
        # sitemap is not here either: it is a tri-state (true|'auto'|false) and
        # crawl.py already normalizes every spelling of it, including the strings
        # a serialized client sends. Forcing it through _coerce_bool_arg here is
        # the one spelling 'auto' would not survive.
    },
    # 这一扇从前**整张表里都没有**，而 `_coerce_arg_types` 的第一行是
    # `if not spec: return args` —— "没有这一扇"与"这一扇没有要归一的参数"在运行时是同一个
    # 样子，所以一颗字符串写来的 `max_pages` 一路走进引擎，撞出
    # `invalid literal for int() with base 10: 'not-a-number'`，再由 `web_crawl` 的
    # `except Exception` 翻成 "Internal error, not a blocked site: retry the same call once"
    # —— 一句教人重试一颗永远不该重试的错（评审 R02-091；半径见
    # `review/.runtime/p1/r102_wrongtype_sweep.json`：61 颗里只有这两颗是这个形状）。
    # 同一颗错在 `web_crawl` 上一直被具名拒掉：两边现在共用那位作者。
    "web_discover": {
        "max_pages": "int", "max_total_chars": "int",
        # 三样刻意留在这里外面，理由与 web_crawl 上面那两条一字不差：
        # `path_include`/`path_exclude` 的裸字符串就是"一个前缀"，`sitemap` 是三态
        # （`_coerce_bool_arg` 活不下 'auto'）。`timeout` 也不列：这一扇自己有一句更具体的
        # 具名拒绝（`invalid_request: timeout= Timeout must be a number` + 1-120000 那段预算
        # 说明，门话由 `server._nothing_went_out()` 装配 —— R02-092 之前那句抄的是 `read`）。
    },
    # §4 写操作那颗：method/content_type 是字符串（大小写与空白是字符串客户端
    # 最容易带进来的噪声），timeout 是数字，body 不 coerce（dict 与 string 都合法）。
    # confirm 同 web_act：字符串写法要归一化——"false" 按裸值传是非空字符串 =
    # 授权，而它授权的是一次带存储凭据的写。
    "web_request": {
        "method": "str", "content_type": "str", "timeout": "number",
        # W1：写请求那一侧的同一颗门。同一个 coerce、同一份拒绝名单（`_header_door`），
        # 因为两边必须读同一份东西。
        "headers": "dict",
        "confirm": "bool",
        # R3：与 `web_read` 那一颗同一句话、同一只归一化。它尤其要紧在这儿 ——
        # 一次 HEAD 探针的全部价值就是那几颗头，而 `"false"` 被读成真会让"我只要个
        # 状态码"变成"把整份响应头也抄进 transcript"。
        "include_headers": "bool",
    },
    # §4 页面动作那颗。rawlist：动作链的每一个对象都是一个不同键的形状
    # （{click:…} / {fill:…} / {wait:…}），逐颗归一化会读掉那些键本身。
    # 两颗会做决定的布尔同样要读字符串写法：`confirm: "false"` 如果按裸值传，
    # 是一次非空字符串 = 授权，而它授权的是"在你的真实 Chrome 里 attach"。
    "web_act": {
        "actions": "rawlist", "timeout": "number", "max_content_chars": "int",
        "confirm": "bool", "check_login": "bool",
        # `headed` 也是"做决定的那颗"，而且是这一颗把窗口开到用户的屏幕上：字符串
        # 客户端的 `headed: "false"` 按裸值传是非空字符串 = 真，于是没人要窗口的调用
        # 弹出一个来。它与 confirm 走同一只归一化。
        "headed": "bool",
        # 刀9-7（F9）`pin` 同一族：它决定的是"下一次调用站在哪一页上"。字符串客户端
        # 把 `pin: "false"` 发过来时按裸值读是真，于是调用方明明说了不要驻留，页却被
        # 留在会话上（并且下一次调用真的复用了它）。与 confirm / headed 同一只归一化。
        "pin": "bool",
    },
    "web_search": {
        "max_results": "int", "cache_ttl": "int", "page": "int",
        "engines": "list", "sources": "list", "sort": "str",
        "exclude_sites": "list", "fetch_content": "bool",
        # min_relevance arrives as a string from clients that JSON-stringify
        # numbers ("0.3"); without this it would reach search.py as text and be
        # coerced to 0.0 there (silently disabling the floor).
        "min_relevance": "number",
        "min_raw_relevance": "number",
        # 共识地板是"做决定的那颗"：字符串客户端写 "2" 时按裸值传会进到比较里
        # （`"2" >= 2` 当场 TypeError），而它的判据是"够不够独立佐证"，不是排序。
        "fetch_min_consensus": "int",
    },
    "feed_read": {"max_items": "int", "timeout": "int"},
    # sys_state normalizes its three arguments in _validate_tool_args, because its
    # list elements are state tokens rather than URLs and it needs the wording to
    # say so.
    # doc_parse 现在也有一颗要归一化：`offset` 是分页位置，字符串客户端写 "12000"
    # 时按裸值传会进到切片下标上（`content[\"12000\":]` 当场 TypeError），而它承诺的
    # 那个"Next offset: N"是响应里印出来的数字 —— 印出来的东西必须能原样传回去。
    # `pages` / `password` 与 web_read 同名同档（"strnum"）：`pages: 3` 是第 3 页、
    # `password: 1234` 是 "1234" —— 一个只按字符串读的参数拿到整数时，那是同一个值。
    "doc_parse": {"offset": "int", "pages": "strnum", "password": "strnum"},
    # cred_defaults 的 `set` 是唯一一颗装着配置的对象参数。从前没人查它，
    # `set="github.com"` 答回来的是 `dict()` 自己的话（"dictionary update sequence
    # element #0 has length 1; 2 is required"）—— 既不说哪颗参数错了，也不说该写成什么。
    "cred_defaults": {"set": "dict"},
}

_ARG_DEFAULTS: dict[Any, dict[str, Any]] = {}


def _arg_defaults(tool: str) -> dict[str, Any]:
    """Each typed argument's own default, read off the method signature.

    Copied defaults drift; the signature is where the tool already states them.

    Keyed by the function it read, not by tool name: the test suite replaces
    these methods with stubs, and a cache keyed on the tool would keep serving
    whatever the first caller happened to see as that tool's signature.
    """
    # 这颗回看是这一层唯一的反向依赖，所以就地 import：模块级 import server 会绕成
    # 循环，而 `crawl.py` / `install.py` 读 server 用的就是这个写法。测试把方法换成
    # stub 时读到的仍是同一个类对象 —— 行为与搬迁前逐位相同。
    from dhole_mcp.server import MasterFetchServer

    fn = getattr(MasterFetchServer, tool)
    cached = _ARG_DEFAULTS.get(fn)
    if cached is not None:
        return cached
    params = inspect.signature(fn).parameters
    empty = inspect.Parameter.empty
    defaults = {name: p.default for name, p in params.items()
                if p.default is not empty and name in _ARG_TYPES.get(tool, {})}
    _ARG_DEFAULTS[fn] = defaults
    return defaults


def _coerce_str_arg(value: Any, name: str) -> Any:
    """一颗工具只会当文本读的参数，就得是文本。

    门前的两种坏法都实测到过：`web_request(url=7)` 答 `'int' object has no attribute
    'strip'`（一句 Python 内部话），`web_read(url=7)` 更坏 —— 它**一路跑完**，在
    `ResponseModel(url=…)` 装填响应时才炸出 pydantic 的校验文本。两种都把自己是哪颗参数
    写错了藏了起来，而后一种还把事情做完了才拒绝。
    """
    if value is None or isinstance(value, str):
        return value
    raise _refuse(f"{name} must be a string, got {type(value).__name__} "
                  f"({value!r}). Pass {name} as text.")


def _coerce_dict_arg(value: Any, name: str) -> Any:
    """一颗装着配置的对象参数得是对象。

    `cred_defaults(set="host")` 从前答的是 `dictionary update sequence element #0 has
    length 1; 2 is required` —— 那是 `dict()` 的 Python 话，说不出哪颗参数错了。与
    `_coerce_str_list_arg` 同一口径：把整段 JSON 文本发过来的客户端收下来（序列化是
    它们那一侧的常态），其余一律按形状拒。
    """
    if value is None or isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, dict):
                return parsed
    raise _refuse(f"{name} must be an object of key/value pairs, got "
                  f"{type(value).__name__} ({value!r}). "
                  f'Pass {name}={{"<key>": <value>, ...}}.')


_WIRE_STRINGS: dict[str, tuple[str, ...]] = {}


def _wire_string_params(tool: str) -> tuple[str, ...]:
    """schema 上写着 string、而 `_ARG_TYPES` 没认领的那些参数名。

    名单从 `_TOOL_DEFS` 现读而不是手抄：新增一颗参数就自动进门（手抄的名单正是那种会
    漂的东西）。表里认领过的（`strnum` 的 `pages`/`password`、`list`、`bool`…）一律跳过
    —— 它们各有自己那句按形状写的话，把 `pages=3` 当数字收下来是刻意的。
    """
    cached = _WIRE_STRINGS.get(tool)
    if cached is not None:
        return cached
    from dhole_mcp.server import MasterFetchServer

    spec = next((td for td in MasterFetchServer._TOOL_DEFS
                 if td["name"] == tool), None)
    taken = set(_ARG_TYPES.get(tool, {}))
    props = (spec or {}).get("inputSchema", {}).get("properties", {})
    names = tuple(n for n, s in props.items()
                  if s.get("type") == "string" and n not in taken)
    _WIRE_STRINGS[tool] = names
    return names


def _coerce_one_arg(kind: str, name: str, value: Any, default: Any) -> Any:
    """Apply one table entry to one value."""
    if kind == "int":
        return _coerce_int_arg(value, name, default=default)
    if kind == "number":
        return _coerce_int_arg(value, name, default=default, allow_float=True)
    if kind == "bool":
        return _coerce_bool_arg(value, name, default=bool(default))
    if kind == "str":
        return _coerce_str_arg(value, name)
    if kind == "dict":
        return _coerce_dict_arg(value, name)
    if kind == "list":
        return _coerce_str_list_arg(value, name)
    if kind == "rawlist":
        # Elements belong to the consumer (actions._validate_actions knows the
        # per-action shapes); the boundary only fixes the container.
        if isinstance(value, str):
            text = value.strip()
            try:
                parsed = json.loads(text) if text.startswith("[") else None
            except (ValueError, TypeError):
                parsed = None
            if isinstance(parsed, list):
                return parsed
            raise _refuse(
                f"{name} must be an array of objects, got {value!r}. "
                f"Pass {name}=[{{...}}, {{...}}]."
            )
        if value is None or isinstance(value, (list, tuple)):
            return value
        raise _refuse(
            f"{name} must be an array of objects, got {type(value).__name__}. "
            f"Pass {name}=[{{...}}, {{...}}]."
        )
    if kind == "strnum":
        # An argument the tool only reads as a string, where an integer is the
        # same value: PDF `pages: 3` is page 3, and `password: 1234` is "1234".
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value
    return value


def _coerce_arg_types(tool: str, args: dict) -> dict:
    """Normalize one call's arguments to the types the tool reads.

    One channel since 17.0 B4: parameters live at the top level, so there is no
    second copy arriving through a bag to keep in step with. Keys the caller
    never sent stay absent so the tool's own default applies; a key sent as null
    takes that same default.
    """
    spec = _ARG_TYPES.get(tool)
    if not spec or not any(k in args for k in spec):
        return args
    defaults = _arg_defaults(tool)
    out = dict(args)
    for key in spec:
        if key in out:
            out[key] = _coerce_one_arg(spec[key], key, out[key], defaults.get(key))
    return out


# The hint travels with the error: a caller who wrote urls="https://x" is one
# bracket away from the right call, and that is true whichever channel the value
# arrived in.
_URLS_SINGLE_HINT = ('Pass urls=["https://a", "https://b"], or use url= '
                     "for a single URL.")


def _validate_tool_args(name: str, args: dict) -> dict:
    """Type-check the arguments the manual dispatcher is about to read.

    This server runs on the low-level ``mcp.server.Server`` with a hand-written
    ``on_call_tool``, so the ``Annotated``/``Literal`` annotations on the tool
    methods only ever reach clients as wire schema — nothing validates at
    runtime, and every argument is read with ``args.get()``. Three defects lived
    in that gap, all silent and all delivered as an ordinary 200:
    ``urls="https://x"`` iterated the string into nineteen one-character
    results; the cache wipe's ``all="false"`` cleared the entire cache because a
    non-empty string is truthy; ``force_fetcher="magic"`` fell through to the
    stealthy browser tier.

    Returning a normalized copy here means every branch below reads a value that
    already has exactly one reading. Raises ArgumentRefusal (a ValueError): the
    web_read branch converts it to the ordinary invalid_request envelope, and
    ``call_tool`` folds every other tool's refusal into the same one.
    """
    out = dict(args)
    # 每一颗"schema 写着 string、没人认领"的参数。这一格从前是空的：`web_read(url=7)`
    # 一路抓到页、在装填响应时才炸出 pydantic 的校验文本，`doc_parse(file_path=7)` 答
    # `'int' object has no attribute 'strip'` —— 两句都是真话，两句都不说在哪颗参数上。
    for key in _wire_string_params(name):
        if key in out:
            out[key] = _coerce_str_arg(out[key], key)
    # 哪几颗工具读档位，就在这一处问一遍：`Literal[...]` 只到客户端为止，这台服务器
    # 自己读参数用 args.get()，一个不存在的档进了 `_ANTI_DETECT[level]` 就是 KeyError —
    # 那是一句 Python 栈，不是"这四颗里挑一个"的答案（`force_fetcher="magic"` 当年就是
    # 这样一路掉进浏览器层的）。缺席不动：缺席才是"用 cred_defaults 存的那一档"。
    if name in _RUNG_TOOLS and "anti_detect" in args:
        out["anti_detect"] = _coerce_anti_detect_arg(args["anti_detect"])
    # 枚举值同样在这一层问一遍。这些参数在 Python 侧一直是宽类型（`str`），
    # 过去只有线格式的 `enum` 拦着；17.0 把 enum 摘掉之后，"不合法的拼法"第一次
    # 有机会走到那条按字面量写的内部代码 —— 而每一条那样的代码都是"不相等 ⇒ 走
    # 到另一条路"，不是"不相等 ⇒ 报错"。校验器必须先于摘除存在，否则这行是空的。
    for key, allowed in _ENUM_ARGS.get(name, {}).items():
        if key in args:
            out[key] = _coerce_enum_arg(args[key], key, allowed,
                                        _ENUM_NOTES.get((name, key), ""))
    # Absent still writes `None` rather than leaving the key out: a caller who reads
    # the normalized dict must be able to tell "no tier" from "not looked at", and
    # `_forward` drops the None so the tool's own default applies either way.
    if name in _FORCE_FETCHER_TOOLS:
        out["force_fetcher"] = _coerce_force_fetcher_arg(args.get("force_fetcher"))
    if name == "web_read":
        out["urls"] = _coerce_str_list_arg(
            args.get("urls"), "urls", single_hint=_URLS_SINGLE_HINT)
    elif name == "sys_state":
        # The same trap the cache wipe fell into: read raw, `confirm="false"` is a
        # non-empty string, and a call that meant "just look" erases every session.
        # The elements are state classes and session ids, so the shared wording
        # ("a URL string") would send a caller looking for a URL to put in clear=.
        token_hint = ("a state class (cache|engine_state|sessions|cred_defaults|all) "
                      "or an open session id")
        for key in ("show", "clear"):
            out[key] = _coerce_str_list_arg(args.get(key), key,
                                            element_hint=token_hint)
        out["confirm"] = _coerce_bool_arg(args.get("confirm"), "confirm",
                                          default=False)
    elif name == "cred_defaults":
        # The same two traps, on the two shapes that carry them: `unset="proxy"`
        # is a string, and read raw it forgets six single-letter keys none of
        # which exist; `show="false"` is a non-empty string, so a call that meant
        # to forget something answers as though it had only been asked to look.
        out["unset"] = _coerce_str_list_arg(
            args.get("unset"), "unset",
            element_hint="a key name (auth|cookies|proxy|profile|cdp|"
                         "allow_private|anti_detect)")
        if "show" in out:
            out["show"] = _coerce_bool_arg(out.get("show"), "show", default=False)
    elif name == "feed_read":
        out["urls"] = _coerce_str_list_arg(args.get("urls"), "urls")
    return out
