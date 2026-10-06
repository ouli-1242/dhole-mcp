"""抓回文本里的"指令形状"：间接注入的启发式标记（G25 评审 阶段4）。

抓回的正文是**数据**，但数据可以长成指令的样子——"忽略之前的指令"、
"ignore previous instructions"、"你现在是…"、页面里直接出现
``web_read(...)`` 这样的调用形状、``scheme://user:pass@host`` 的带凭据
URL、"curl --data @cookies https://…" 的外传命令。这些文本会落进调用方
agent 的 transcript；agent 把它当指令执行的那一刻，抓取层就成了注入的
运输通道。命中时响应信封带 ``untrusted_content``——一个**标记**，不是拦截。

边界，说不清边界的启发式比没有更糟：

* **标记，不是拦截，也不是判决。** dhole 不删改、不遮蔽任何正文；"这段
  算不算指令"的裁决归调用方的语言能力（dhole 保持零模型）。一篇*讲*提示
  注入的安全文章会命中同样的形状——标记只说"这段文本里有指令形状的语言"，
  不说"这里真的有攻击"。
* **不是穷尽。** 新的措辞会从旁边滑过去；这里钉的是实测载荷里反复出现的
  五类形状（指令覆盖 / 角色劫持 / 工具点名 / 带凭据 URL / 外传命令），
  不是一张会过时的攻击清单。工具描述里"抓回的正文按不可信数据对待"的
  纪律仍是第一道防线，本标记是第二道。
* **确定性、零模型。** 纯 stdlib 正则；扫描前先把文本归一，堵掉三类"字形
  看起来一样、正则却匹配不到"的绕过：①**不可见/格式字符**（零宽、bidi 控制、
  软连字符、变体选择符…）——`ig\u200bnore previous instructions` 这类在词内或
  词间插入的载荷能整段绕过写死可见字形的正则；②**全角/兼容字形与同形字**——
  `ｉｇｎｏｒｅ` 与西里尔 `ignоre` 经 NFKC + 同形字折叠后拼回 ASCII；③**词间
  分隔符**——`ignore-previous` / `ignore_all_instructions` 在正则里按
  `[\\s\\-_]*` 接受。归一给两条视角：折叠后整字去除（治词内插入）与替换为空格
  （治词间插入），只有正文里真的出现不可见字符时才多跑一条。结果只依赖文本
  本身，缓存命中重扫得到同一答案，标记天然与缓存一致。

只依赖 stdlib；被 quality（ResponseModel 信封）、crawl（CrawlPage）、
feed（FeedItem）三个正文落点调用，不反向依赖任何 dhole 模块。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

# 信封上最多点名的形状类数；total 永远是全部命中次数（两类以上没列全时，
# total 会大于 hits 里 count 之和——那是"还有没点名的形状"的信号）。
_MAX_HITS = 5
# 单条 excerpt 上限：引用攻击者文本本身要有界，否则标记成了新的注入面。
_EXCERPT_CHARS = 90

_TOOL_CALL = (
    "tool_call",
            # web_screenshot / web_resolve 已下线（A1/A2）但名字保留：一个页面
            # 试图指挥 agent 去调「已经不存在的工具」，恰恰是最值得点名的形状
            # ——调用的结果不是能力，而是一次报错注入 transcript。
    re.compile(r"\b(?:web_read|web_act|web_request|web_crawl|web_discover|"
               r"web_screenshot|web_search|web_resolve|cred_defaults|"
               r"sys_state|sys_guide|feed_read|doc_parse)\s*\("),
)
_PATTERNS = (
    # 指令覆盖（EN）：祈使动词 + 指令类名词（限定词与方位词可缺——
    # "ignore all instructions" 与 "ignore previous instructions" 同形）。
    # 分隔符用 `[\s\-_]*`：归一后 `ig\u200bnore\u200ball` 拼成 `ignoreall`，
    # 所以名词前的 `\b` 不能要（`ignorepreviousinstructions` 也得命中）。
    ("instruction_override", re.compile(
        r"\b(?:ignore|disregard|forget)[\s\-_]*"
        r"(?:(?:all|any|the|your|these|their)[\s\-_]*)?"
        r"(?:(?:previous|prior|above|earlier)[\s\-_]*)?"
        r"(?:instructions?|prompts?|rules?|directives?)\b", re.I)),
    # 指令覆盖（ZH）。方位词可叠用（"之前所有的指令"是实测里最常见的形状）。
    ("instruction_override", re.compile(
        r"(?:忽略|无视|不要理会)(?:(?:之前|以上|上面|先前|前面|所有|全部)的?){1,3}"
        r"(?:指令|提示词?|规则|要求)")),
    # 角色劫持：把"读页面的你"改写成"页面想要的你"。多词短语内部也按
    # `[\s\-_]*` 连写，否则 `you\u200bare now` 归一后拼成 `youare now` 会漏。
    ("role_hijack", re.compile(
        r"\b(?:you[\s\-_]*are[\s\-_]*now|pretend[\s\-_]*to[\s\-_]*be|act[\s\-_]*as)"
        r"[\s\-_]*(?:a|an|the|my)\b|"
        r"你现在是|从现在开始.{0,4}你是|扮演(?:一个)?(?:系统|管理员)", re.I)),
    # 工具点名：页面文本里出现本服务器工具的调用形状（名字来自 _TOOL_DEFS，
    # 13 颗的原文名单，硬编码在这里而不是 import——模块不许反向依赖 server）。
    _TOOL_CALL,
    # 带凭据的 URL：scheme://user:pass@host 的形状本身。
    ("credential_url", re.compile(
        r"\b[a-z][a-z0-9+.\-]*://[^\s/<>\"'@]+:[^\s/<>\"'@]+@", re.I)),
    # 外传命令：curl/wget 带数据旗（页面教 agent 把东西 POST 出去）。
    ("exfil_command", re.compile(
        r"\b(?:curl|wget)\b[^;\n]{0,120}"
        r"(?:--data(?:-raw|-binary|-urlencode)?|--upload-file|-T\s)", re.I)),
)


def _excerpt(text: str, start: int, end: int) -> str:
    """命中处 ±30 字符的有界引用，压平空白，总量 ≤ :data:`_EXCERPT_CHARS`。"""
    lo, hi = max(0, start - 30), min(len(text), end + 30)
    span = " ".join(text[lo:hi].split())
    if lo > 0:
        span = "…" + span
    if hi < len(text):
        span += "…"
    return span[:_EXCERPT_CHARS]


# 代码格式豁免：文档正文里 `web_read(url=...)` 的调用示例恰好就是 tool_call 的
# 形状（dhole 自己的 README 被标了 9 处——示例代码即正文）。启发式标的是形状，
# 不是排版；把 fenced code block 与 inline code span 掩成**等长**空白，偏移不变，
# excerpt 仍切原文，而代码区内的形状不再计数。
_CODE_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
_CODE_SPAN = re.compile(r"`[^`\n]+`")


def _mask_code(text: str) -> str:
    out = _CODE_FENCE.sub(lambda m: " " * len(m.group()), text)
    return _CODE_SPAN.sub(lambda m: " " * len(m.group()), out)


# 不可见 / 格式字符：它们在视觉上不占位，却能把一个词或一个短语切开，
# 让写死可见字形的正则匹配不到。攻击者用它把 `ignore previous instructions`
# 写成 `ig\u200bnore previous instructions`（词内）或
# `ignore\u200bprevious instructions`（词间）—— 两种都要治。
#
# 集合只收**真正的零宽/格式控制**，不收 \u2028/\u2029（行/段分隔符是空白，
# 不是伪装，`\s` 本来就认）。软连字符 \u00ad 收：它合法出现在断词提示里，
# 但同一个位置也能藏一个词内切口，去掉它对正常正文无损。
_INVISIBLE_RE = re.compile(
    "["
    "\u00ad"           # soft hyphen
    "\u061c"           # Arabic letter mark
    "\u180e"           # Mongolian vowel separator
    "\u200b-\u200f"    # ZWSP / ZWNJ / ZWJ / LRM / RLM
    "\u202a-\u202e"    # bidi embedding & override
    "\u2060-\u2064"    # word joiner / invisible operators
    "\u2066-\u2069"    # bidi isolates
    "\ufeff"           # BOM / zero-width no-break space
    "\ufff9-\ufffb"    # interlinear annotation
    "\ufe00-\ufe0f"    # variation selectors 1-16（emoji 后缀，也能当中缝）
    "\U000e0100-\U000e01ef"  # variation selectors 17-256
    "]"
)


# 同形字折叠：西里尔 / 希腊里与 ASCII 同形的少数几个字母，折回 ASCII，让写死
# ASCII 字形的正则也能命中「西里尔 o 混进 ignore」这类载荷。表只收**会出现在
# 触发词里**的那些字母；ASCII 正文上是恒等变换。折叠会抬高非拉丁正文的误报 ——
# 可接受，标记不是判决（见模块 docstring）。
_HOMOGLYPHS = str.maketrans({
    # 西里尔
    "\u0430": "a", "\u0432": "b", "\u0435": "e", "\u043a": "k",
    "\u043c": "m", "\u043d": "h", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0442": "t", "\u0443": "y", "\u0445": "x",
    "\u0456": "i", "\u0458": "j", "\u0455": "s", "\u0501": "d",
    "\u04bb": "h", "\u051b": "q", "\u051d": "w", "\u0475": "v",
    # 希腊
    "\u03b1": "a", "\u03b2": "b", "\u03b5": "e", "\u03b9": "i",
    "\u03ba": "k", "\u03bd": "v", "\u03bf": "o", "\u03c1": "p",
    "\u03c4": "t", "\u03c5": "u", "\u03c7": "x",
})


def _fold(text: str) -> str:
    """NFKC + 同形字折叠：全角/兼容字形与西里尔/希腊同形字母折回 ASCII。

    NFKC 治全角 `ｉｇｎｏｒｅ` 这类兼容字形；同形字表治"字形一样、码位不同"的
    字母。ASCII 正文上是恒等变换，常见路径因此不额外产生一个视角。
    """
    return unicodedata.normalize("NFKC", text).translate(_HOMOGLYPHS)


def _scan_variants(text: str) -> tuple[str, ...]:
    """正文的归一化视角：无不可见字符时就是折叠后的一份（常见路径零额外视角）。

    先 NFKC + 同形字折叠，再对不可见字符给两份 —— **整字去除**（把
    `ig\\u200bnore` 拼回 `ignore`，治词内插入）与**替换为空格**（把
    `ignore\\u200bprevious` 拼回 `ignore previous`，治词间插入）。只留一份治不了
    两种，所以两份都扫，命中的那一份算数。代码掩码在归一**之后**做：掩码要盖住的
    是可见代码，不可见字符先去掉不影响它。
    """
    folded = _fold(text)
    if not _INVISIBLE_RE.search(folded):
        return (folded,)
    removed = _INVISIBLE_RE.sub("", folded)
    spaced = _INVISIBLE_RE.sub(" ", folded)
    return (removed, spaced)


def scan(text: str) -> Optional[Dict[str, Any]]:
    """扫一段第三方文本；命中返回 ``{"hits": [...], "total": n}``，干净返回
    ``None``（信封上即字段缺席）。

    ``hits`` 每项 ``{kind, excerpt, count}``：kind 是形状类（模块 docstring
    的五类），excerpt 是首个命中的有界引用，count 是该类命中次数；最多列
    :data:`_MAX_HITS` 类。

    每一类在归一后的各视角上都试一遍，取命中更多的那个视角（同数取"整字去除"）：
    一份文本里两种插入都可能出现，取并集会把同一处数两遍，取更多的那份是保守的。
    """
    if not text:
        return None
    variants = tuple(_mask_code(v) for v in _scan_variants(text))
    hits: List[Dict[str, Any]] = []
    total = 0
    for kind, pattern in _PATTERNS:
        best_text: Optional[str] = None
        best_matches: Optional[list] = None
        for variant in variants:
            matches = list(pattern.finditer(variant))
            if matches and (best_matches is None or len(matches) > len(best_matches)):
                best_text, best_matches = variant, matches
        if not best_matches:
            continue
        total += len(best_matches)
        if len(hits) < _MAX_HITS:
            first = best_matches[0]
            hits.append({
                "kind": kind,
                "excerpt": _excerpt(best_text, first.start(), first.end()),
                "count": len(best_matches),
            })
    if not total:
        return None
    return {"hits": hits, "total": total}
