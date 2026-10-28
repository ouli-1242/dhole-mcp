"""web_read 的按查询聚焦内容过滤。

当 agent 传 ``focus="..."`` 时，抽取出的 markdown 被过滤成与查询最相关的块
（段落 / 标题 / 表格 / 列表），让 agent 在长页面上少加载上下文。灵感来自
Crawl4AI 的 BM25ContentFilter，本地实现（不加依赖）。

设计选择：focus 跑在**缓存之后**。完整抽取文本每个 URL 只缓存一次；不同的 focus
查询只是同一份缓存内容的不同视图，所以聚焦永远不会触发重抓，同一 URL 的两次不同
聚焦共享一个缓存条目。过滤发生在 ``_apply_chunking``（通用的最后包装）里，因此
实时抓取、缓存命中与批量结果一视同仁。

BM25（k1=1.5，b=0.75）配永远为正的 IDF（log 里的 ``+1``），所以只出现一次查询词
的块也拿到正分并被保留。选择**锚定在最好的块上**（``relative_threshold``），而不
只看绝对分：只做绝对切分时，一个自然语言问题会留下文档页的 55-76%，因为它的高频词
（"what"、"is"、"the"）在几乎每个块里都拿正分 —— 也就是说，文档示例的用法
（``focus='question'``）恰恰是省上下文最少的那一档。紧挨保留块之前的标题会为上下文
而保留。如果没有任何块越过门槛，就保留最接近的几块，让 agent 有东西可判，而不是
拿到一页空白。

分词是 Unicode 感知的，并用二元组覆盖无空格文字（汉字、假名、泰文）—— 见
``_tokens``。它不是语言特定的分词器：没有词干化、没有词典，所以 CJK 查询按字符
二元组匹配。
"""

from __future__ import annotations

import contextvars
import logging
import math
import re
from typing import Optional

logger = logging.getLogger("dhole-mcp.focus")

# ─── 本次调用要哪个 focus 打分器（按次参数 focus_engine，W15）───────────────
# 为什么这个状态住在 focus.py：读它的是两个消费者 —— server 的
# ``_apply_chunking``（web_read 与 bulk）和 crawl 的 ``_classify_and_extract``，
# 而 crawl 不能在模块级 import server（循环）。focus.py 只
# 依赖标准库，两边都能 import；contextvar 对 ``asyncio.to_thread`` 也是透传的，
# 所以爬取线程读到的是发起那次调用的值。
#
# 语义（17.0）：focus 打分引擎是**纯按次选择** —— DHOLE_NEURAL_FOCUS 许可门
# 已删除（写死判据：要不要每页 ~12.5s 的 CPU 推理是每一次调用的决定，不是一次
# 配置；没有全局默认可跟随）。
#   ""       走 BM25（~23ms/页的便宜默认；不传就是这样）
#   "bm25"   明确走便宜的路（与 "" 同义，写给读描述的人）
#   "neural" 明确要语义分（~12.5s/页）；模型缺失在工具入口显式报错，不静默下载
FOCUS_ENGINE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dhole.focus_engine", default="")
ENGINES = ("bm25", "neural")


def normalize_engine(value: object) -> str:
    """把客户端给的东西读成 'bm25' / 'neural' / ''。

    不可识别的值取 ''（= 不点名，走 BM25）。这条宽容现在只对**内部调用方**成立：
    17.0 摘掉线格式 enum 之后，调用方传来的拼错值在进这一层之前就被
    `params._coerce_enum_arg` 拒掉（并把四颗合法值一起报回去），所以走到这里的
    非 'bm25'/'neural' 字符串只可能是代码里传错的，不是用户手打的。
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in ENGINES:
            return candidate
    return ""


def engine_wants_neural() -> bool:
    """本次调用**点名**语义打分了吗。只认按次的 ``focus_engine``：
    没点名（""）或点名 "bm25" 都是 BM25 —— 神经分是显式选择，从来不该默认发生。"""
    choice = FOCUS_ENGINE.get()
    if choice:
        return choice == "neural"
    return False

# 一条"词串"：任意文字的字母/数字，在下划线与标点处切开。用 Unicode 感知的 \w
# 而不是 [a-z0-9]，让西里尔 / 希腊 / 阿拉伯 / 韩文查询至少能被分词，而不是静默
# 产出空词集。
_WORD_RUN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# 词与词之间不写空格的文字。这些文字里的一条串是一个短语，不是一个词，所以要做
# 二元组（见 _tokens）。
_SPACELESS_CHAR_RE = re.compile(
    r"[\u0e00-\u0e7f"          # Thai
    r"\u1000-\u109f"           # Myanmar
    r"\u1780-\u17ff"           # Khmer
    r"\u3040-\u30ff"           # Hiragana + Katakana
    r"\u3400-\u4dbf"           # CJK ext A
    r"\u4e00-\u9fff"           # CJK unified
    r"\uf900-\ufaff"           # CJK compatibility
    r"\uff66-\uff9f]"          # halfwidth Katakana
)


def _is_spaceless(ch: str) -> bool:
    return bool(_SPACELESS_CHAR_RE.match(ch))


def _tokens(text: str) -> list[str]:
    """BM25 分词：Unicode 词串，无空格文字切成二元组。

    ``[a-z0-9]+`` 只匹配 ASCII，于是中文查询产出**空**词集，``focus_content``
    原样返回页面 —— 不报错、不加注，恰好是调用方传 ``focus`` 想避开的全文。下游
    没有任何东西能把它与"每个块都相关"区分开。

    汉字 / 假名 / 泰文串没有词边界可切，也没有随包的分词器，所以每条串展开成重叠
    的字符二元组（"如何创建任务" -> 如何, 何创, 创建, 建任, 任务）。每个串一个 token
    几乎匹配不到东西（整句话不是一个词），单个字符又会匹配几乎一切。二元组是不用
    分词器的标准折中。

    边界的判定按文字分段，所以 "Python教程" 这种混排串交出词 "python" 加上 CJK
    二元组，而不是一个无法使用的 token。ASCII 行为不变：短于 2 个字符的串丢弃。
    """
    out: list[str] = []
    for run in _WORD_RUN_RE.findall((text or "").lower()):
        i, n = 0, len(run)
        while i < n:
            spaceless = _is_spaceless(run[i])
            j = i
            while j < n and _is_spaceless(run[j]) == spaceless:
                j += 1
            piece = run[i:j]
            if spaceless:
                if len(piece) == 1:
                    out.append(piece)
                else:
                    out.extend(piece[k:k + 2] for k in range(len(piece) - 1))
            elif len(piece) >= 2:
                out.append(piece)
            i = j
    return out


def _query_runs(query: str) -> list[str]:
    """点名的那些"词"，按出现序去重 —— 存在性要说的是词，不是切出来的二元组。"""
    seen: set = set()
    runs: list[str] = []
    for run in _WORD_RUN_RE.findall((query or "").lower()):
        if len(run) >= 2 and run not in seen:
            seen.add(run)
            runs.append(run)
    return runs


def _runs_on_page(runs: list[str], block_tokens: list[list]) -> tuple[list, list]:
    """每个点名的词在这一页出现过没有（词级：一个 CJK 词只要有任一大二元组落地就算在）。

    保守的方向是对的：核对引用的人要知道的是"这话到底在不在这一页"，而"在不在"由字面
    存在性回答，不由分数回答 —— BM25 的分没有校准过的读数（见 `focus_content` 的
    docstring），而"页面上找不到这个词"是一个可核对的事实。
    """
    pool: set = set()
    for tokens in block_tokens:
        pool.update(tokens)
    found: list[str] = []
    absent: list[str] = []
    for run in runs:
        (found if set(_tokens(run)) & pool else absent).append(run)
    return found, absent


def _presence_clause(total: int, absent: list[str]) -> str:
    """"你点名的词里有几个在这一页找不到"那一句；没有词可说时是空串。"""
    if not total or not absent:
        return ""
    shown = ", ".join(f"'{w}'" for w in absent[:6])
    if len(absent) > 6:
        shown += f" (+{len(absent) - 6} more)"
    if len(absent) == total:
        return (f" none of the {total} words you named occurs on this page"
                f" ({shown}).")
    return (f" {len(absent)} of the {total} words you named occur nowhere on this"
            f" page ({shown}).")


def _focus_header(*pieces: str) -> str:
    """把头部的几段话各成一句：相关性/来源 → 缺席的点名词 → 删掉的结构块 → 出路。

    为什么要有这一颗函数：三段话此前是字符串直接相接的，实测（真 CLI 的 S3a）拼出来是
    "…shares no token with your query 5 of the 6 words you named occur nowhere…" ——
    两句话黏成一句，读的人分不清哪个数是说什么的。段内不许自带结束符，由这里统一加。
    """
    body = []
    for piece in pieces:
        text = (piece or "").strip().lstrip(";").strip().rstrip(".;")
        if text:
            body.append(text)
    return "[" + ". ".join(body) + ".]"


def _record_match_stats(stats: dict, selection: str, total: int,
                        found: list[str], absent: list[str]) -> None:
    """把"找没找到"记进 caller 交来的那份 stats。

    一处作者：两条头部（filter / best）与信封上那几个数读的都是这一份，谁都不许自己
    再算一遍（同一个响应里两个作者迟早漂，见 `_presence_clause`）。
    """
    stats["selection"] = selection
    stats["terms_total"] = total
    stats["terms_matched"] = len(found)
    stats["terms_absent"] = list(absent)


def _is_heading(block: str) -> bool:
    """块的第一非空行是合法 ATX markdown 标题时为 True。"""
    return _heading_level(block) <= 6


def _heading_level(block: str) -> int:
    """返回标题级别（1-6），不是标题时返回 99。

    CommonMark 要求 # 字符后跟空格（或行尾）：'# Title' 合法，'#Title' **不是**
    标题。
    """
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[level:]
            # 合法 ATX：'# ' 或单个 '#'（空标题）；'#H' 不是标题
            if level <= 6 and (not rest or rest[0] == " "):
                return level
            return 99
        if stripped:
            return 99
    return 99


def _is_table(block: str) -> bool:
    """块看起来像 markdown 表格（有 | 与 --- 分隔行）时为 True。"""
    lines = [line for line in block.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return "|" in lines[0] and "---" in lines[1] if len(lines) > 1 else False


def _is_code(block: str) -> bool:
    """识别代码块：围栏（```）或整体缩进。"""
    stripped = block.strip()
    if stripped.startswith("```"):
        return True
    lines = block.splitlines()
    non_blank = [line for line in lines if line.strip()]
    if not non_blank or len(non_blank) < 2:
        return False
    indented = sum(
        1 for line in non_blank if line.startswith("    ") or line.startswith("\t")
    )
    return indented >= len(non_blank) * 0.8


#: 结构性正文的两种名字。它们与散文的区别不是长度，是**能不能重排出来**：一段
#: 掉了两句的正文读者还能读懂，一张掉了三列的表读起来是假数据。
_STRUCTURAL_KINDS = (_is_table, _is_code)


def _structural_kind(block: str) -> str:
    for probe, name in zip(_STRUCTURAL_KINDS, ("table", "code block")):
        if probe(block):
            return name
    return ""


def _dropped_structural(blocks: list[str], keep: set) -> list[dict]:
    """被这次过滤**丢掉**的结构性正文，逐条点名。

    C4 的现场：用户点名要 3.5 节那张表，`focus=` 把它整块删了，回执里只剩
    "showing 6 of 11 blocks" —— 一个数。删掉的是散文还是表格，那个数分不出来，
    而这两件事对调用方是两个结论（前者顶多少两句闲话，后者是答案没了）。
    """
    tally: dict[str, dict] = {}
    for i, block in enumerate(blocks):
        kind = _structural_kind(block)
        if not kind:
            continue
        row = tally.setdefault(kind, {"total": 0, "gone": 0, "chars": 0})
        row["total"] += 1
        if i not in keep:
            row["gone"] += 1
            row["chars"] += len(block.strip())
    out = []
    for kind, row in tally.items():
        if not row["gone"]:
            continue
        if row["total"] == 1:
            label = f"the page's only {kind}"
            why = "it shares no token with your query"
        else:
            label = f"{row['gone']} of the page's {row['total']} {kind}s"
            why = "they share no token with your query"
        out.append({"kind": kind, "chars": row["chars"], "gone": row["gone"],
                    "label": f"{label} ({row['chars']:,} chars)", "why": why})
    return out


def _dropped_structural_note(dropped: list[dict]) -> str:
    if not dropped:
        return ""
    return "; " + "; ".join(f"dropped {d['label']} - {d['why']}" for d in dropped)


# ─── 界：一句在哪里停，一块在哪里结束 ─────────────────────────────
#
# 两个家族共用一颗函数（摘要标签 `quality._summary_label` 与这里的分块）。它们的**排序**
# 是同一条事实：帽内任何一处句末都好过一处更靠后的子句末，两者都好过词末，而"一个边界
# 都没有"不等于"可以剪半截"。各写一遍就是下一次漂移的开始。
_SENTENCE_BREAKS = (". ", "! ", "? ", "。", "！", "？")
_CLAUSE_BREAKS = ("; ", " - ", ", ", ": ", " (", "；", "，", "：")


def snap_to_boundary(head: str, keep_mark: bool = False) -> int:
    """这一串应当在哪里停 —— 返回**保留的字符数**（`<= len(head)`）。

    `keep_mark` 分开两件事：
      False（标签）—— 断点落在标点**之前**，摘要不以逗号或冒号收尾，与原 `_summary_label`
        的既有行为逐字一致；
      True（分块）—— 断点落在标点**之后**，因为块是正文本身：把"。"吃掉就是丢内容，
        而 `focus_content` 交回的是保留块的拼接，丢一个字符都说不出口。
    """
    for group in (_SENTENCE_BREAKS, _CLAUSE_BREAKS):
        # 自己写循环而不是 `max(rfind(sep) for sep in group)`：生成式里的那个 `sep`
        # 出了生成式就不存在，而 `keep_mark` 要的就是"这颗界符有多长"。
        best_at, best_len = -1, 0
        for sep in group:
            at = head.rfind(sep)
            if at > best_at:
                best_at, best_len = at, len(sep)
        if best_at > 0:
            return best_at + best_len if keep_mark else best_at
    space = head.rfind(" ")
    if space > 0:
        return space + 1 if keep_mark else space
    return len(head)


#: 一块的上限。超过它，"这一块讲什么"就不成立：BM25 的平均文档长度被一个巨块拉平，
#: 语义打分读的是一整块。真实的来路是一份 81,000 字、段落之间只有单换行的在线文档 ——
#: 在只认空行的切法下整页就是**一块**，focus 一刀切不动（`focus_content` 为此留着一句
#: 道歉："this page is one block, so there is nothing to filter" —— 那句诚实，而它的
#: 存在本身就是缺陷）。2400 是把"够长以自成一义"与"够短以能排序"放在一起量出来的取舍。
MAX_BLOCK_CHARS = 2_400

_ATX_HEADING = re.compile(r"^ {0,3}#{1,6}(?: |$)")
_FENCE = re.compile(r"^ {0,3}(?:`{3,}|~{3,})")
_HR = re.compile(r"^ {0,3}([-*_])(?:\s*\1){2,}\s*$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+\S")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")


def _is_table_start(lines: list[str], i: int) -> bool:
    """表头行的形状：这一行有 `|`，下一行是 `---|:--:` 那一种分隔行。"""
    if "|" not in lines[i]:
        return False
    return i + 1 < len(lines) and bool(_TABLE_SEP.match(lines[i + 1]))


def _is_table_row(line: str) -> bool:
    return line.count("|") >= 2


def _cap_block(block: str) -> list[str]:
    """超长的一块在句界断开；表格与代码围栏不断。

    结构性的一块断了就等于允许丢掉三行而没人知道 —— `_structural_kind` 那一族已经把
    "掉了两句的正文还能读，掉了三行的表是假数据"写成了判据，这里照它办。切法用
    `keep_mark=True`：断点落在标点之后，`"".join(parts)` 与原来那块**逐字相等**。
    """
    if len(block) <= MAX_BLOCK_CHARS or _is_table(block) or _is_code(block):
        return [block]
    parts: list[str] = []
    rest = block
    while len(rest) > MAX_BLOCK_CHARS:
        keep = snap_to_boundary(rest[:MAX_BLOCK_CHARS], keep_mark=True)
        parts.append(rest[:keep])
        rest = rest[keep:]
    if rest:
        parts.append(rest)
    return parts


def _split_blocks(text: str) -> list[str]:
    """把 markdown 切成块 —— 空行是一种界，**形状**也是，超长的一块再在句界断开。

    原来只认空行。真实站点上大量正文是"段落之间只有单换行"的（在线文档、被 markdownify
    压平的表格页），那一类页面在这里就是一整块，focus 对它什么都做不了。现在认五种界：
    ATX 标题、代码围栏（**围栏内永不切**）、表格（表头前切一刀，行内不切）、水平线、
    列表串的开始；再加 `MAX_BLOCK_CHARS` 的断句上限。

    标题后面紧跟的正文归进标题那一块 —— 这正是 `_bm25_scores` 的 heading boost 假设的
    形状，块自带标题比把标题单列一块更贴近"这一块讲什么"。

    一块的内容逐字来自原文：拼接所有块能还原整页（`_cap_block` 的 `keep_mark` 守的就是
    这一条），所以"切得更细"不会变成"悄悄少了几行"。
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            blocks.append("\n".join(current))
            current.clear()

    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            flush()
            continue
        opens_fence = bool(_FENCE.match(line))
        if in_fence:
            # 围栏里的一切（包括看起来像标题、表格或列表的行）都属于这一块
            current.append(line)
            if opens_fence:
                in_fence = False
                flush()
            continue

        prev = current[-1] if current else ""
        boundary = (opens_fence
                    or bool(_ATX_HEADING.match(line))
                    or bool(_HR.match(line))
                    or _is_table_start(lines, i)
                    # 表格结束：上一行是表格行而这一行不是
                    or (bool(prev) and _is_table_row(prev) and not _is_table_row(line))
                    # 列表串只在**开始**处切：连续的条目是一件事
                    or (bool(_LIST_ITEM.match(line))
                        and bool(prev) and not bool(_LIST_ITEM.match(prev))))
        if boundary:
            flush()
        current.append(line)
        if opens_fence:
            in_fence = True
    flush()

    out: list[str] = []
    for block in blocks:
        out.extend(_cap_block(block))
    return out


def _bm25_scores(
    blocks: list[str], block_tokens: list[list[str]], qterms: set, k1: float, b: float
) -> list[float]:
    """BM25+ 块打分，含标题感知的加权（第二遍）。

    从 ``focus_content`` 里抽出来，让神经打分器（M-1）只替换打分这一步，并继承
    流水线其余部分 —— 选择、表格/代码保留、标题上下文 —— 不变。标题加权只留在
    BM25 一侧：那是对词法匹配的补丁，而一个读完整 (query, doc) 对的 cross-encoder
    不需要标题词把它下面的块抬分。
    """
    n = len(blocks)
    avgdl = (sum(len(t) for t in block_tokens) / n) if n else 0.0 or 1.0
    if avgdl == 0:
        avgdl = 1.0

    # 每个词项的文档频率（跨块）。
    df: dict[str, int] = {}
    for toks in block_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1

    def idf(term: str) -> float:
        d = df.get(term, 0)
        # log 里 +1 让 IDF 保持为正（BM25+ 风味），于是只出现一次也总能得正分。
        return math.log((n - d + 0.5) / (d + 0.5) + 1)

    def score(i: int) -> float:
        toks = block_tokens[i]
        if not toks:
            return 0.0
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        dl = len(toks)
        s = 0.0
        denom_len = k1 * (1 - b + b * dl / avgdl)
        for term in qterms:
            f = tf.get(term)
            if f:
                s += idf(term) * (f * (k1 + 1)) / (f + denom_len)
        return s

    scores = [score(i) for i in range(n)]

    # 标题感知加权：当标题包含查询词时，给它下面所有块加权（1.5 倍），
    # 直到遇到同级或更高级的下一个标题。
    for i in range(n):
        if not _is_heading(blocks[i]):
            continue
        heading_tokens = set(_tokens(blocks[i]))
        if not heading_tokens & qterms:
            continue
        h_level = _heading_level(blocks[i])
        scores[i] *= 1.5
        for j in range(i + 1, n):
            if _is_heading(blocks[j]) and _heading_level(blocks[j]) <= h_level:
                break
            scores[j] *= 1.5

    return scores


# 一个 (query, doc) 对在 cross-encoder 的 MAX_SEQ=512 处被截断，所以超过窗口的块
# 只按开头打分，尾部对模型不可见。BM25 的块长是按词法重叠调的；编码器需要自己的
# 界。约 300 字符是安全上限：默认模型的最坏情况是汉字约 1 token/字符，这样 512 的
# 窗口里仍给查询本身留出空间。
_NEURAL_MAX_CHARS = 300

# 成本模型（2026-10-01 在 README.md 94 块 / CHANGELOG.md 140 块上实测）：墙钟时间
# 跟着**分片数**走，不是块数 —— 202 个分片 14.0s vs 214 个分片 14.8s，尽管块数差
# 1.5 倍。不同大小的块切到 <=300 字符后就收敛了，这也是早先"91 块 vs 140 块成本
# 差不多"的读数看似固定开销的原因：真正对齐的是分片数，不是一个常数。汉字文本约
# 69 ms/分片（300 字符 ~ 300 token，接近 MAX_SEQ）。
#
# **不要**为了优化把等长分片批在一起。score() 会把整批补齐到最长成员，而模型**不是**
# padding 不变的：同一个分片在 300 字符批里的得分与在 71 字符批里不同（实测最大
# 2.2e-2），一次四路长度拆分让单个得分最多移动 0.34，并把 2-3 个块换进/换出前 20。
# 长度分桶实测快 37-66%，但因改变答案而被**否决**。一页之内批是固定的，所以得分
# 可复现；它们只是不能**跨页**比较，而改变批的组成就是改变行为。


def _split_for_encoder(block: str, cap: int = _NEURAL_MAX_CHARS) -> list[str]:
    """把一块切成能放进编码器窗口的小片（感知长行）。

    压缩成单行的文档必须硬折行，否则一片仍然扛着整行。按行累积让本来就装得下的
    普通 markdown 块保持不变。
    """
    if len(block) <= cap:
        return [block]
    pieces: list[str] = []
    cur = ""

    def _flush() -> None:
        nonlocal cur
        if cur.strip():
            pieces.append(cur)
        cur = ""

    for line in block.splitlines():
        while len(line) > cap:
            _flush()
            pieces.append(line[:cap])
            line = line[cap:]
        if cur and len(cur) + len(line) + 1 > cap:
            _flush()
        cur = f"{cur}\n{line}" if cur else line
    _flush()
    return pieces or [block[:cap]]


def _neural_block_scores(reranker, query: str, blocks: list[str]) -> Optional[list[float]]:
    """每个块的 cross-encoder 得分；None = 打分器本身失败。

    score() 对一次推理抖动回以全零，对形状不符回以更短的列表；两者在这里都读成
    "模型没有在作答"，所以映射为 None，调用方回退到 BM25，而不是交出一页被坏打分器
    清空的内容。每块得分取它各分片的 MAX —— 一块的相关性等于它最相关的那部分。
    """
    try:
        chunk_map: list[tuple[int, str]] = []
        for i, bl in enumerate(blocks):
            for piece in _split_for_encoder(bl):
                chunk_map.append((i, piece))
        raw = reranker.score(query, [c for _, c in chunk_map])
        if len(raw) != len(chunk_map):
            return None
        out = [0.0] * len(blocks)
        for (i, _piece), s in zip(chunk_map, raw):
            if s > out[i]:
                out[i] = float(s)
        if all(s == 0.0 for s in out):
            return None
        return out
    except Exception as e:
        logger.warning("neural focus scoring failed: %s", e)
        return None


def _neural_scorer_note() -> str:
    try:
        from dhole_mcp.reranker import active_model
        return f"neural relevance ({active_model().name} cross-encoder)"
    except Exception:
        return "neural relevance (cross-encoder)"


def focus_content(
    text: str,
    query: str,
    threshold: float = 1.0,
    k1: float = 1.5,
    b: float = 0.75,
    fallback_top: int = 5,
    relative_threshold: float = 0.33,
    reranker=None,
    mode: str = "filter",
    stats: Optional[dict] = None,
) -> str:
    """返回 ``text`` 中最与 ``query`` 相关的那些块。

    FILTER 模式有两道切分，一块必须**同时**越过：绝对 ``threshold`` 分，以及
    ``relative_threshold`` × 最佳块的分数。相对切分存在是因为绝对切分分不清
    "这一块讲的就是查询"与"这一块包含 'the' 这个词"：BM25+ 让每个词的 IDF 恒正，
    于是问题的停用词在几乎每个块里都累积一点正分（实测：留下文档页的 55-76%，
    而关键词查询只留 9%）。因此 ``threshold`` 充当地板 —— 最佳块本身就很弱的页面
    保持旧的宽松行为。

    如果 ``query`` 为空、文本只有 <= 1 块，或查询产不出可用词项，原文本原样返回
    （focus 是空操作）。

    ``reranker``（M-1，神经 focus）：传入一个已预热的 ``_Reranker`` 时，每个块由
    本地 cross-encoder 而非 BM25 打分。打分器从不加载模型、从不下载 —— 调用方
    （``_apply_chunking``）只在本次调用点名 ``focus_engine='neural'`` 时才探
    ``get_reranker()``（17.0：没有许可旗；加载在工具入口安排，点名了但模型缺失
    在那里被拒绝，而不是降级）；这里拿到 None/失败的打分器就回退 BM25，并在头部
    说明。cross-encoder 不需要标题加权（它读的是对，不是词项重叠），绝对的
    ``threshold`` 也不适用 —— sigmoid 标度有自己的几何（切题约 0.9+，跑题约
    0.001），所以只用锚定最佳块的相对切分。

    ``mode="best"``（M-2）：只返回得分最高的那一块加上它紧前的标题 —— "这一页的
    哪部分回答了问题" —— 而不是越过切分的每一块。

    ``stats``（出参，M-3）：``{"scorer": "neural"|"bm25", "best_score": ...}``
    让调用方无需重解析输出就能报告一次意图落空的提示。``best_score`` 只为神经打分器
    填写 —— BM25 的无界标度没有校准过的读数。另外三个键出于同样的理由（不重解析）
    归调用方：``dropped_structural`` 点名这次过滤删掉的表格/代码，``ignored`` 说明
    文本原样返回时为什么根本没过滤，``blocks_total`` / ``blocks_kept`` 是块账 ——
    自 3.5 起一页被切成远多于空行规则给出的块数，所以"丢了几块"不再读成"你拿到了
    文档的多少"；这几个数才让丢失可审计。
    """
    stats_out = stats if isinstance(stats, dict) else None

    def _untouched(reason: str) -> str:
        """一条 no-op 也要有个说法：静默原样返回与"过滤了但什么都没删"不可分辨。

        账本在这一支也要填：整页原样留下意味着「一块都没丢」，而不是「没数」——
        两者对读者是不同的事，后者会让人以为过滤器坏掉了。
        """
        if stats_out is not None:
            stats_out["ignored"] = reason
            stats_out["blocks_total"] = len(blocks)
            stats_out["blocks_kept"] = len(blocks)
        return text

    if not query or not text or not text.strip():
        return text  # 没点名 focus：这本来就不是"该做而没做"，不需要说法
    blocks = _split_blocks(text)
    if len(blocks) <= 1:
        return _untouched("this page is one block, so there is nothing to filter")
    qterms = set(_tokens(query))
    if not qterms:
        return _untouched(f"focus={query!r} has no searchable word in it")

    block_tokens = [_tokens(bl) for bl in blocks]
    n = len(blocks)

    scorer_note = "BM25 relevance"
    neural_used = False
    scores: Optional[list[float]] = None
    if reranker is not None:
        neural = _neural_block_scores(reranker, query, blocks)
        if neural is not None:
            scores = neural
            neural_used = True
            scorer_note = _neural_scorer_note()
    if scores is None:
        scores = _bm25_scores(blocks, block_tokens, qterms, k1, b)

    # 含任何查询词的表格/代码永远保留（高价值）—— 这张确定性安全网对两个打分器都生效。
    preserved: set = set()
    for i in range(n):
        if _is_table(blocks[i]) or _is_code(blocks[i]):
            if set(block_tokens[i]) & qterms:
                preserved.add(i)

    # 选择：锚定在最好的**内容块**上，不是整体最好的块（BM25 下标题被加权 1.5 倍，
    # 且因为它是标题而匹配查询 —— 锚在标题上可能丢掉它引出的答案块）。神经路径跳过
    # 绝对门槛：sigmoid 标度没有 BM25 式的地板，相对切分已经丢掉 0.0001 的尾巴。
    content_scores = [s for i, s in enumerate(scores) if not _is_heading(blocks[i])]
    best = max(content_scores) if content_scores else (max(scores) if scores else 0.0)
    if stats_out is not None:
        stats_out["scorer"] = "neural" if neural_used else "bm25"
        stats_out["best_score"] = round(best, 4) if neural_used else None
    cut = best * relative_threshold
    if not neural_used:
        cut = max(threshold, cut)
    keep = [i for i in range(n) if scores[i] >= cut]
    selection = "cut"
    if not keep:
        # 没有任何块越过门槛 —— 保留最接近的几块，让 agent 有东西可判，而不是
        # 拿到空响应。自 R01-036 起这一支是**被点名**的：头部从前在这里写
        # "showing 5 of 28 blocks by BM25 relevance"，对一页什么都没答上的内容
        # 读起来像"这些匹配了"（实测：每个块都是 0.0 分）。
        keep = sorted(range(n), key=lambda i: scores[i], reverse=True)[:fallback_top]
        selection = "closest"
    runs = _query_runs(query)
    found_runs, absent_runs = _runs_on_page(runs, block_tokens)
    presence = _presence_clause(len(runs), absent_runs)

    keep_set = set(keep) | preserved
    # 保留紧挨某个保留的非标题块之前的标题。
    for i in keep:
        if i > 0 and _is_heading(blocks[i - 1]) and not _is_heading(blocks[i]):
            keep_set.add(i - 1)

    if mode == "best":
        top = max(keep, key=lambda i: scores[i])
        top_set = {top}
        if top > 0 and _is_heading(blocks[top - 1]) and not _is_heading(blocks[top]):
            top_set.add(top - 1)
        dropped = _dropped_structural(blocks, top_set)
        if stats_out is not None:
            stats_out["dropped_structural"] = dropped
            stats_out["blocks_total"] = n
            stats_out["blocks_kept"] = len(top_set)
            _record_match_stats(stats_out, selection, len(runs), found_runs,
                                absent_runs)
        if selection == "closest":
            header = _focus_header(
                f"Focus best block: {query!r}; NO block cleared the relevance cut "
                f"(best score {round(best, 3)} against a cut of {round(cut, 3)}) - "
                f"this block is the highest-scored of {n}, not a match to the query",
                _dropped_structural_note(dropped), presence,
                "Pass focus='' for the full page, or keep mode unset for the "
                "filtered page")
        else:
            header = _focus_header(
                f"Focus best block: {query!r}; top of {n} by {scorer_note}",
                _dropped_structural_note(dropped), presence,
                "Pass focus='' for the full page, or keep mode unset for the "
                "filtered page")
        if len(blocks[top].strip()) < 200:
            # BM25 这一支没有校准分来点名一次弱命中（不像神经的意图地板），但一个
            # 极小的最佳块不用校准也能核：30 字符的"最佳答案"通常只是个标题。
            header += (
                f" The best block is only {len(blocks[top].strip())} chars - "
                "possibly just a heading with no answer; keep mode unset "
                "(filter) or reword the focus query.")
        if stats_out is not None:
            # 头部不是页面正文。调用方要拿它把"过滤后正文"与"整页长度"放回同一
            # 把尺上比较（`server._pagination_basis`），否则小页会出现
            # "474 of 387 chars" 这种算术不可能。
            stats_out["header_chars"] = len(header)
        return header + "\n\n" + "\n\n".join(blocks[i] for i in range(n) if i in top_set)

    dropped = _dropped_structural(blocks, keep_set)
    if stats_out is not None:
        stats_out["dropped_structural"] = dropped
        # 3.6 · 切得更细之后，"不许静默丢内容"这笔税要当场结：这一页被分成几块、留下
        # 几块，是读者唯一能核对"少掉的是正文还是版面"的数。
        stats_out["blocks_total"] = n
        stats_out["blocks_kept"] = len(keep_set)
        _record_match_stats(stats_out, selection, len(runs), found_runs, absent_runs)
    kept = "\n\n".join(blocks[i] for i in range(n) if i in keep_set)
    if selection == "closest":
        header = _focus_header(
            f"Focus: {query!r}; NO block cleared the relevance cut (best score "
            f"{round(best, 3)} against a cut of {round(cut, 3)}) - these "
            f"{len(keep_set)} of {n} blocks are the highest-scored, not matches",
            _dropped_structural_note(dropped), presence,
            "Pass focus='' for the full page")
    else:
        header = _focus_header(
            f"Focus: {query!r}; showing {len(keep_set)} of {n} blocks "
            f"by {scorer_note}",
            _dropped_structural_note(dropped), presence,
            "Pass focus='' for the full page")
    if stats_out is not None:
        # 同 best 分支：头部字符不是页面正文，单独记账。
        stats_out["header_chars"] = len(header)
    return header + "\n\n" + kept
