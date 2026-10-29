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
    """归一化一个整数工具参数；绝不悄悄换成默认值。

    ``max_content_chars`` 过去正是这么干的：非整数被替换成 ``MAX_CONTENT_CHARS``。
    由于好几个 MCP 客户端会把数字串化（同样的 JSON 串化客户端需要解析），
    ``max_content_chars="2000"`` 悄悄变成了 40,000 —— 一次 20 倍的上下文超支，以
    普通 200 交付。那就是"看起来成功了"这一族，上一层已由 ``_coerce_arg_types`` 与
    ``_normalize_schema`` 堵掉。

    策略，按顺序：
      * ``None`` -> ``default``（参数没有设置）
      * bool -> 抛异常（``True`` 是 ``int`` 子类；没人想表达 1 个字符）
      * 能解析成整数的字符串 -> 转换，尊重调用方的意图
      * 其余 -> 抛异常，并点名它实际是什么类型
      * 越出 ``[lo, hi]`` -> ``clamp`` 为真时钳制（资源帽，线上有文档，所以是已知
        边界而非意外），否则抛异常（一个没有合理解读的胡值）。

    ``clamp`` 就是本仓"拒还是钳"的分界，所以它编码的决定只在本文档里写一次
    （模块 docstring"越界：钳还是拒"）：越界值仍有**唯一**合理解读、且边界是文档
    化的资源帽时钳；尊重猜测会改变参数**含义**时拒（``offset=-5`` 否则会变成
    "从 0 开始"，即一份错的正文以普通 200 交付）。无论走哪条，都要告诉调用方 ——
    钳制在 ``summary`` 里点名，拒绝点名参数、范围与正确写法。

    ``lo``/``hi`` 是可选的，因为线上边界那一趟（:func:`_coerce_arg_types`）只转换
    类型，把范围留给工具：每个文档化的帽都已经在它生效的地方应用，并带着告知调用方
    的注记。在这里重复边界，只会在那条注记之上再加一层静默钳制。

    ``allow_float`` 同时接受 float 与 int，用于自身注解就是 ``int | float`` 的参数
    （超时、等待）—— 那里 1500.5 ms 是调用方想要的值，不是类型错误。
    """
    if value is None:
        return default
    # 下面每一次拒绝都指向一个具体的正确值。对于自身默认值是 None（"没设置"）的
    # 参数，没有数字可建议，所以建议就是省略它。
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


# 客户端可以合法发来的布尔字符串。显式列出，因为这里堵的缺陷正是真值性造成的：
# `if all:` 把**任何**非空字符串当真，于是 cache_clear(all="false") 清空了整个缓存。
# 白名单是唯一不会在那个方向上出错的写法 —— 不在名单里的字符串抛异常，而不是猜。
_BOOL_LITERALS: dict[str, bool] = {
    "true": True, "1": True, "yes": True, "on": True,
    "false": False, "0": False, "no": False, "off": False,
}

# 线上接受的抓取档。'dynamic'（v3.5.0 删掉的 Playwright 档留下的别名）自 17.0 B4
# 起消失：一档两个名字是一套读者每次响应都要调和的词汇，而它产生的
# escalation_path 本来也写着 'stealthy'。
_FORCE_FETCHER_VALUES = frozenset({"http", "stealthy"})


def _coerce_bool_arg(value: Any, name: str, *, default: bool = False) -> bool:
    """归一化一个布尔工具参数；绝不让真值性做决定。

    与 ``_coerce_int_arg`` 同一政策：``None`` 表示"没设置"（-> ``default``），真正的
    ``bool`` 直接通过，``0``/``1`` 通过为 ``False``/``True``（数字类型客户端就是这么
    拼的），字符串只有在属于 ``_BOOL_LITERALS`` 时才被认可。其余一律抛异常，并点名
    实际收到了什么。
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    # 0/1 是数字类型客户端表达 false/true 的方式。它们恰好只有一种解读，所以被认可；
    # 其他整数都是错误并抛异常（与 _coerce_int_arg 不同，那里直接拒绝 bool，因为
    # `True` 在那里会悄悄变成"1 个字符"）。
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
    """归一化一个字符串列表参数；裸字符串是错误。

    字符串**是**可迭代的，所以 ``web_read(urls="https://example.com")`` 曾经答回
    ``total=19, successful=19`` 和十九条单字符"结果" —— 批量路径取了 ``len(urls)``
    并迭代它，下游没有任何东西能把它与一次真的 19-URL 请求区分开。

    把字符串包起来是一种猜测（逗号是分隔符吗？换行呢？），所以抛异常并点名那个已经
    表示"一个 URL"的参数。JSON 编码的数组被接受，因为好几个客户端会串化嵌套结构
    （与 JSON 串化的值会被解析同一个理由），而数组字面量恰好只有一种解读。

    元素也要检查：``urls=["https://a", 123]`` 过去会走到 ``ResponseModel(url=123)``，
    然后回一句裸的 Pydantic "1 validation error for ResponseModel"，没有任何恢复提示。
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
    """调用方点名的那一档，或 'auto' —— 中间没有别的。

    `Literal[...]` 只以 schema 形式到达客户端；本服务器用 `args.get()` 读每个参数，
    所以一个不在名单里的档位否则会进入阶梯表并在那里抛 KeyError —— 在应该列出四种
    拼法的地方来一段 Python traceback。
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
    """校验 force_fetcher 枚举，而不是掉进 stealthy 兜底。

    方法签名上的 ``Literal[...]`` 只是文档 —— 分发器用 ``args.get()`` 读参数，所以
    ``force_fetcher="magic"`` 过去会错过每个 ``== "http"`` 分支，落进 stealthy 的
    ``else``：最重的一档（约 5 秒加反检测开销），且没有报错指出这个值错了。"没设置"
    返回 ``None``。
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
        # 点名，而不是折进"不是抓取档"：拿着旧笔记的调用方需要被告知那个名字去了哪，
        # 而不是让他在剩下的两档之间猜。
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


# 读抓取档的两个工具。web_crawl 过去把 force_fetcher 直接送进档位阶梯，没有边界检查，
# 所以爬取上的 `force_fetcher="hpt"` 就是 web_read 那个缺陷（见
# `_coerce_force_fetcher_arg` 的 docstring），只是爆炸半径变成了整个站点：它错过
# `== "http"` 分支，把每一页都走浏览器。
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
    # web_search 的 `mode` 与 `freshness` 刻意**不**在这里：search.py 在第一次网络
    # 调用之前就把两者拒掉（`_validate_mode`、`_validate_freshness`），而且每次拒绝
    # 都已经印出完整值集 —— `Invalid mode: 'bogus' (auto|neural|find_similar)`。
    # 这里再加一张表就是一份会与 `_IMPLEMENTED_MODES` 漂开的副本，正是本模块已经
    # 警告过的"边界重复声明在宣告它的注记之上"那种形状。
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
    """命名值之一，否则是列出全部值的拒绝。

    按裸值读取时，不在名单里的拼法从不报错：每个消费者都比较字面量
    （`mode == "find_similar"`、`extraction_type in _EXTRACTED_TYPES`、
    `normalize_engine`），所以一个笔误会滑进"不相等"通向的那一支 ——
    `mode="blurry"` 照样搜索、跳过重排器，并报告 `rerank_mode=merge`，像是一切
    都被要求过。线上 `enum` 存在时这走不到；现在走得到，这正是这个函数存在的全部理由。

    政策与 ``_coerce_force_fetcher_arg`` 一致：``None``/``""`` 表示"没设置"
    （返回 ``None``；``_forward`` 会丢掉它，让自己方法的默认值生效），首尾空白与
    大小写是调用方的噪声而非另一个值，其余一律抛异常并点名实际收到了什么。
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


# ─── 线上参数类型 ──────────────────────────────────────────────────
# web_read 与 cache_clear 有逐参数 coercer；其余六个工具一个都没有，于是分发器转发
# 的一切都只是 JSON 恰好带来的东西。两种客户端行为让普通机器撞上这个缺口：
#
#   * 串化数字的客户端（"max_results": "6"）。search.py 随后跑
#     `max(1, min("6", 50))`，而 min() 拿第二个操作数与第一个比较：
#     `'<' not supported between instances of 'int' and 'str'`。工具把这段裸 Python
#     traceback 当成 `error` 字段答回去，外部测试报告把它记成"web_search 完全崩溃，
#     重启不恢复"，并误诊为引擎冷却状态。状态文件是无辜的 —— 数字以 int 到达时，
#     同一次调用就成功。
#   * 把每个 schema 属性都回传成 null 的客户端。null 表示"没设置"，所以在任何分支
#     读它之前就被丢掉 —— 但丢掉必须是有意的：对 None 跑 `if cache_ttl > 0` 会抛
#     NoneType 比较，而让工具自己的默认值生效才是调用方的本意。
#
# 该是列表的地方来了裸字符串是同一个缺口、更糟的结果：
# `path_include="/docs"` 会逐字符迭代。
#
# 所以：在任何分支读取之前，为两条通道在边界处转换**类型**。范围刻意**不**在这里
# 检查 —— 每个文档化的帽都已经在应用它的地方执行（search 钳制 max_results 并说明、
# crawl 钳制 max_pages、_validate_filters 拒绝 page>10），通常带着一句告知调用方
# 值被移动的注记。在这些注记之上再钳一次，产生的正是这张表存在要堵的那种静默版本。
_ARG_TYPES: dict[str, dict[str, str]] = {
    "web_read": {
        "urls": "list",
        "include_media": "bool",
        "include_links": "bool", "ignore_robots": "bool",
        "cache_ttl": "int", "offset": "int", "max_content_chars": "int",
        "max_links": "int",
        "focus_mode": "str",
        "focus_engine": "str",
        # 做决定而不是要数据的两个布尔。按裸值读时，字符串客户端的
        # `confirm: "false"` 是非空真值字符串，会授权一次没人批准过的 CDP attach ——
        # sys_state 的 `confirm` 已经掉进去过的同一个缺陷。
        "confirm": "bool", "check_login": "bool",
        "timeout": "number",
        # W1：`headers=` 是键值对，不是字符串。字符串客户端把整段 JSON 发过来是常态
        # （`_coerce_dict_arg` 收下来），其余形状当场点名 —— 一个读成 `{"a": "b"}`
        # 之外的东西的调用，宁可拒绝，不要"安静地什么头都没带"。
        "headers": "dict",
        # 数字形式的 PDF 页码请求（"pages": 3）是无歧义的；只按字符串读过去会
        # 悄悄丢掉范围并返回整个文件。
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
        # path_include/path_exclude **不**在这里：裸字符串本来就被读成它表示的
        # "单前缀列表"，而 crawl.py 在还能说出口、且尚未抓取任何页面的地方做这件事。
        # sitemap 也不在这里：它是三态（true|'auto'|false），crawl.py 已经归一化它的
        # 每一种拼法，包括串化客户端发来的字符串。在这里硬塞进 _coerce_bool_arg，
        # 正是 'auto' 活不下来的那一种拼法。
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
    # 3.2 · 证据包那颗门：三颗数字按数字读（字符串客户端把 "3" 写成 3 是常态），
    # query 是字符串。夹取的宣告住在方法体里（clamped 行），这里只管类型。
    # cross_check 是"做决定的那颗"：字符串客户端把 "false" 按裸值传是非空字符串 =
    # 真，于是没人要求的交叉印证会悄悄跑起来、多付一份输出。
    "web_research": {
        "max_sources": "int", "per_source_chars": "int", "timeout": "number",
        "cross_check": "bool",
    },
    # 提升清单 2 · 本地缓存检索：limit 按数字读（1-20 的夹取住在 recall_pages）。
    "web_recall": {
        "limit": "int",
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
        # min_relevance 从那些把数字 JSON 串化的客户端以字符串到达（"0.3"）；
        # 没有这一格它会以文本到达 search.py 并在那里被强转成 0.0（悄悄关掉地板）。
        "min_relevance": "number",
        "min_raw_relevance": "number",
        # 共识地板是"做决定的那颗"：字符串客户端写 "2" 时按裸值传会进到比较里
        # （`"2" >= 2` 当场 TypeError），而它的判据是"够不够独立佐证"，不是排序。
        "fetch_min_consensus": "int",
        # terse 是布尔：字符串客户端写 "false" 时不能当非空字符串读成真。
        "terse": "bool",
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
    """每个类型化参数自己的默认值，从方法签名上现读。

    抄来的默认值会漂；签名才是工具已经写明它们的地方。

    键是它读取的函数对象，不是工具名：测试套件会把这些方法换成 stub，而以工具名
    为键的缓存会一直交出第一个调用方碰巧看到的那份签名。
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
    """把一条表项应用到一个值上。"""
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
        # 元素归消费者管（actions._validate_actions 知道每种动作的形状）；
        # 边界层只修正容器。
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
        # 一个工具只按字符串读、而整数是同一个值的参数：PDF `pages: 3` 是第 3 页，
        # `password: 1234` 是 "1234"。
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value
    return value


def _coerce_arg_types(tool: str, args: dict) -> dict:
    """把一次调用的参数归一化成工具读取的类型。

    17.0 B4 起只有一条通道：参数住在顶层，所以没有第二份经包袋到达的副本要同步。
    调用方从未发送的键保持缺席，让工具自己的默认值生效；发成 null 的键取同一个默认值。
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


# 提示跟着错误走：写 urls="https://x" 的调用方离正确的调用只差一个方括号，而无论
# 值是从哪条通道到达的，这都是真的。
_URLS_SINGLE_HINT = ('Pass urls=["https://a", "https://b"], or use url= '
                     "for a single URL.")


def _validate_tool_args(name: str, args: dict) -> dict:
    """对本地分发器即将读取的参数做类型检查。

    本服务器跑在低层的 ``mcp.server.Server`` 上，``on_call_tool`` 是手写的，所以工具
    方法上的 ``Annotated``/``Literal`` 注解只以线格式 schema 到达客户端 —— 运行时
    什么都不校验，每个参数都用 ``args.get()`` 读取。三个缺陷住在这个缺口里，全都
    无声，全以普通 200 交付：``urls="https://x"`` 把字符串迭代成十九条单字符结果；
    缓存清空的 ``all="false"`` 清掉了整个缓存，因为非空字符串是真值；
    ``force_fetcher="magic"`` 掉进 stealthy 浏览器档。

    在这里返回一份归一化副本，意味着下面每个分支读到的值都只有唯一一种解读。抛出
    ArgumentRefusal（一个 ValueError）：web_read 分支把它转成普通的 invalid_request
    信封，``call_tool`` 把其余每个工具的拒绝折进同一个。
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
    # 缺席仍然写 `None`，而不是把键完全去掉：读归一化 dict 的调用方必须能分清
    # "没有档位"与"没看过"，而 `_forward` 会丢掉 None，让工具自己的默认值生效，
    # 两种情况下都一样。
    if name in _FORCE_FETCHER_TOOLS:
        out["force_fetcher"] = _coerce_force_fetcher_arg(args.get("force_fetcher"))
    if name == "web_read":
        out["urls"] = _coerce_str_list_arg(
            args.get("urls"), "urls", single_hint=_URLS_SINGLE_HINT)
    elif name == "sys_state":
        # 缓存清空掉进去过的同一个陷阱：按裸值读，`confirm="false"` 是非空字符串，
        # 一次本意"只看一眼"的调用会抹掉每个会话。元素是状态类与会话 id，所以共用的
        # 措辞（"a URL string"）会让调用方去找一个 URL 填进 clear=。
        token_hint = ("a state class (cache|engine_state|sessions|cred_defaults|all) "
                      "or an open session id")
        for key in ("show", "clear"):
            out[key] = _coerce_str_list_arg(args.get(key), key,
                                            element_hint=token_hint)
        out["confirm"] = _coerce_bool_arg(args.get("confirm"), "confirm",
                                          default=False)
    elif name == "cred_defaults":
        # 带着它们的两种形状上的同样两个陷阱：`unset="proxy"` 是字符串，按裸值读
        # 会忘掉六个单字母键，而它们一个都不存在；`show="false"` 是非空字符串，于是
        # 一次本意要忘记点什么的调用，答起来像只是被要求看一眼。
        out["unset"] = _coerce_str_list_arg(
            args.get("unset"), "unset",
            element_hint="a key name (auth|cookies|proxy|profile|cdp|"
                         "allow_private|anti_detect)")
        if "show" in out:
            out["show"] = _coerce_bool_arg(out.get("show"), "show", default=False)
    elif name == "feed_read":
        out["urls"] = _coerce_str_list_arg(args.get("urls"), "urls")
    return out
