"""web_recall：对本地缓存里「这台机器已经读过的页」做检索 —— 零网络、零新模型。

它回答的问题是「我之前读到过哪一页说过 X」，不是「X 的答案是什么」——返回的是
**指针**（url + 标题 + 摘录 + 什么时候抓的），不是正文。过期行照样召回但如实标注
`is_stale`：缓存 TTL 过了只意味着「这一页该重新抓才作数」，不意味着「我没读过它」
——而「在哪儿读过」恰恰是 TTL 过期之后唯一还值钱的那半句。

两条实测（2026-10-24）：本机 26 行真库上中文词「注意力机制」LIKE 2ms 命中 3 行；
合成 2,000 行 / 37MB 库上 4 词预筛 321ms、200 候选分词 605ms —— 10,000 行帽
（`cache.MAX_CACHE_ENTRIES`）下全链路 ≈2s，够格做一个同步的 MCP 调用。

两段式而不是嵌入向量（提升清单 2 的原案是 bge-small 嵌入）：reranker 基建是
**交叉编码器**（对 (query, doc) 逐对打分），不是双塔——复用它做重排是零新增
模型、零新增依赖；而召回侧在这个量级上 LIKE + BM25 实测已经够快。嵌入方案要多
下载一个模型、建一套向量索引，买不到这两段还没给出的东西。神经档**在场才用**：
`ensure_reranker(download=False)` 只看暖不暖、绝不触发下载（一次 119MB 的意外
下载不该由一次回忆发起），缺席时 BM25 序就是终序。

**存储层的形状决定预筛的写法**：`cache.set_cached` 用 `json.dumps` 默认的
`ensure_ascii=True` 存正文，中文在库里是 ``\\u6ce8\\u610f`` 这样的转义——拿原文
LIKE 永远打不中（真库实测：中文词 0 行，翻成转义后 3 行）。所以查询词先按**同一
套** json 转义规则翻过去再下 SQL；这依赖的是「存储函数是确定的」这个事实，不是
碰运气的字符串技巧。英文词原样通过（ASCII 不转义），一个词里两族混排也按字符各
归各的。
"""

from __future__ import annotations

import html as _html
import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any

import aiosqlite

from dhole_mcp.cache import _ensure_db
from dhole_mcp.focus import _tokens, snap_to_boundary

logger = logging.getLogger("dhole-mcp.recall")

#: 每个词的 LIKE 预筛最多取最近 400 行（ORDER BY fetched_at DESC）——「回忆」是
#: 近因偏置的活：同样命中，上周读过的比去年读过的更可能是要找的那一页。截断也
#: 因此是确定的、说得清的（不是 SQLite 扫描顺序的偶然）。
_PER_TERM_LIMIT = 400

#: 合并各词命中后的候选上限。400 × 30KB 的载入实测 50ms 一级，远在预算里。
_MAX_CANDIDATES = 400

#: 进 BM25 的正文只取每行前 8,000 字符——一页的主题在前 8k 里说过；为一页的第
#: 30,001 个字多付 3ms 分词换不来排序的变化。
_TOKEN_CHARS = 8_000

#: 摘录窗口。太短（<120）认不出「这句话在说什么」，太长就顶掉半条结果。
_SNIPPET_CHARS = 240

#: BM25 的前几名进交叉编码器重排。24 = 搜索路径量过的每批舒适区（~10-20ms/对
#: CPU），池再大延迟线性涨、排序基本不再变。喂给打分器的正文也是 1,500 字符为限：
#: 交叉编码器的窗口本来就 ~512 token，多喂只是白付分词。
_RERANK_POOL = 24
_RERANK_CHARS = 1_500

_WS_RE = re.compile(r"\s+")


def _merge_url_variants(rows: list[dict]) -> list[dict]:
    """R02-007：同一 URL 的多个抓取变体合并为一行——fetched_at 最新的胜出。

    缓存键按抓取变体存（同一 URL 的 markdown/html、不同时刻各一行），结果组装
    以前按键逐行出门：同一 URL 两行、is_stale 一真一假互相打架（两轮实测）。
    胜者的行原样保留（额外键如 ``i`` 穿透），合并数 >=2 时带 ``variants=N``；
    顺序按每个 URL 的**首次出现位**稳定输出。
    """
    winners: dict[str, dict] = {}
    order: list[str] = []
    counts: dict[str, int] = {}
    for row in rows:
        url = row["url"]
        if url not in winners:
            winners[url] = row
            order.append(url)
            counts[url] = 1
        else:
            counts[url] += 1
            if row["fetched_at"] > winners[url]["fetched_at"]:
                winners[url] = row
    out = []
    for url in order:
        row = winners[url]
        if counts[url] > 1:
            row = dict(row)
            row["variants"] = counts[url]
        out.append(row)
    return out


def _json_escape(term: str) -> str:
    """把查询词翻成存储层的形状（``json.dumps(..., ensure_ascii=True)`` 那一套）。

    逐字符：>= U+0080 翻 ``\\uXXXX``；BMP 之外（str 里的一个字符）翻成 json 的
    **代理对**形状——``json.dumps`` 对 U+10000 以上是这么写的，不翻成对的话
    生僻字与 emoji 永远打不中。ASCII 原样通过。
    """
    out: list[str] = []
    for ch in term:
        o = ord(ch)
        if o >= 0x10000:
            o -= 0x10000
            hi, lo = 0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)
            out.append(f"\\u{hi:04x}\\u{lo:04x}")
        elif o >= 0x80:
            out.append(f"\\u{o:04x}")
        else:
            out.append(ch)
    return "".join(out)


def _like_escape(term: str) -> str:
    """SQL LIKE 的三个通配（``%`` ``_`` ``\\``）按 ESCAPE '\\' 的规矩转义。

    次序不能反：先翻成存储层形状（产出 ``\\uXXXX``），再转义那串里真正的 LIKE
    通配——反过来的话，json 转义产出的反斜杠会被 LIKE 转义再吃一层。查询词出自
    `focus._tokens`（单词/二元组，天然不含通配），但判据不建立在"天然"上——
    这条函数自己站得住。
    """
    return (term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))


def _strip_tags(html_text: str) -> str:
    """html 行的正文是原始标记：摘录与打分前先褪成可见文本。"""
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html_text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _WS_RE.sub(" ", _html.unescape(text)).strip()


def _bm25(qterms: set, docs_tokens: list[list[str]], k1: float = 1.5,
          b: float = 0.75) -> list[float]:
    """页级 BM25（与 `focus._bm25_scores` 同一套数学：正 IDF、k1/b 同值），但不带
    标题加权——那是块级 focus 对「标题下的块」的补丁，页级没有这个形状。分数只
    用于**排序**：BM25 的读数没有校准，回执里不出现它（同一页换个候选集分数就变，
    当成相关度读是假话）。"""
    n = len(docs_tokens)
    if not n:
        return []
    avgdl = sum(len(t) for t in docs_tokens) / n or 1.0
    df: dict[str, int] = {}
    for toks in docs_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    out: list[float] = []
    for toks in docs_tokens:
        if not toks:
            out.append(0.0)
            continue
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        denom = k1 * (1 - b + b * len(toks) / avgdl)
        s = 0.0
        for term in qterms:
            f = tf.get(term)
            if f:
                d = df.get(term, 0)
                idf = math.log((n - d + 0.5) / (d + 0.5) + 1)
                s += idf * (f * (k1 + 1)) / (f + denom)
        out.append(s)
    return out


def _snippet(text: str, qterms: list[str]) -> str:
    """摘录 = 第一个词命中点周围的 240 字符窗（词命中优先于页首——页首是站点名
    与导航，命中点才是"这一页为什么中"的现场）。找不到词（词命中在打分截断之后）
    就退回页首——诚实的一个窗口，好过装作有更好的。"""
    low = text.lower()
    pos = -1
    for t in qterms:
        at = low.find(t.lower())
        if at >= 0 and (pos < 0 or at < pos):
            pos = at
    if pos < 0:
        pos = 0
    start = max(0, pos - 60)
    window = _WS_RE.sub(" ", text[start:start + _SNIPPET_CHARS]).strip()
    cut = snap_to_boundary(window, keep_mark=True) or len(window)
    window = window[:cut].strip()
    return ("…" if start > 0 else "") + window


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


async def recall_pages(query: str, limit: int = 8,
                       cache_dir: Path | None = None) -> dict[str, Any]:
    """主入口。返回 dict（信封的包装住在 server 那一侧，与各工具的 return 同层）。

    流程：分词（focus 的 CJK 二元组）→ json 转义翻成存储层形状 → LIKE 预筛
    （每词最近 400 行，纯 SQL，C 层速度）→ 合并候选（≤400，近者优先）→ 载入
    正文、页级 BM25 → 前 24 进交叉编码器重排（模型在场才用）→ 取前 limit 行，
    每行从信封里捡回页面自己的标题。
    """
    q = (query or "").strip()
    # R09-002：越界的 clamp 必须具名（行为指南在册绝对句「clamped-and-named or
    # refused, never silently rewritten」）——对照 web_search max_results 的具名
    # clamp 与 web_research 的 clamped 字段。未越界（含缺省 8）一个字不加。
    _raw_limit = limit
    limit = max(1, min(20, int(limit or 8)))
    _limit_clamp = (f"limit -> {limit} (1-20)"
                    if _raw_limit is not None and int(_raw_limit) != limit else None)
    # 分词就一条规则（focus 的）：CJK 二元组 + 拉丁词。去重保序，8 个词是
    # LIKE 预筛的预算——每词一趟扫描，第 9 个词基本是同义重复。
    qtokens = list(dict.fromkeys(_tokens(q)))[:8]
    if not qtokens:
        raise ValueError(
            "web_recall needs at least one searchable word (letters, digits or "
            "CJK) - punctuation and whitespace alone recall nothing")

    now_s = time.time()
    db_path = await _ensure_db(cache_dir)
    db_path_s = str(db_path)

    per_term_hits: dict[str, int] = {}
    candidates: dict[str, float] = {}   # key -> fetched_at（近者优先排序用）
    scanned = 0
    async with aiosqlite.connect(db_path_s) as db:
        db.row_factory = aiosqlite.Row
        row = await (await db.execute(
            "SELECT COUNT(*) FROM cache WHERE scope='fetch'")).fetchone()
        scanned = int(row[0] or 0)
        for term in qtokens:
            needle = f"%{_like_escape(_json_escape(term))}%"
            cur = await db.execute(
                "SELECT key, fetched_at FROM cache WHERE scope='fetch' "
                "AND content LIKE ? ESCAPE '\\' "
                "ORDER BY fetched_at DESC LIMIT ?",
                (needle, _PER_TERM_LIMIT))
            rows = await cur.fetchall()
            per_term_hits[term] = len(rows)
            for r in rows:
                # 重复命中（两词同页）不加分——预筛只管召回，排序是 BM25 的事。
                if r["key"] not in candidates or r["fetched_at"] > candidates[r["key"]]:
                    candidates[r["key"]] = r["fetched_at"]

    terms_found = [t for t in qtokens if per_term_hits.get(t)]
    terms_absent = [t for t in qtokens if not per_term_hits.get(t)]
    if not candidates:
        return {
            "query": q, "engine": "bm25", "results": [],
            "terms_found": terms_found, "terms_absent": terms_absent,
            "scanned_rows": scanned, "candidates_scored": 0,
            "note": ("nothing on this disk matches - the cache only knows pages "
                     "fetched through dhole on this machine"),
        }

    ordered = sorted(candidates, key=lambda k: -candidates[k])[:_MAX_CANDIDATES]

    # 载入与打分。正文整列读回（实测 200 候选 24ms），打分只吃前 8k；摘录窗口
    # 在打分用的那截里找，找不到就退页首（`_snippet` 自己说明）。
    keys: list[str] = []
    texts: list[str] = []
    docs_tokens: list[list[str]] = []
    meta: dict[str, dict] = {}
    async with aiosqlite.connect(db_path_s) as db:
        db.row_factory = aiosqlite.Row
        for key in ordered:
            cur = await db.execute(
                "SELECT url, extraction_type, content, fetched_at, ttl "
                "FROM cache WHERE key = ?", (key,))
            row = await cur.fetchone()
            if row is None:
                continue  # 预筛与载入之间被逐出（插入侧的 10k 帽）——如实少一行
            try:
                chunks = json.loads(row["content"])
            except (ValueError, TypeError):
                continue  # 不是我们存的形状，不猜
            raw = "\n".join(c for c in chunks if isinstance(c, str))
            if (row["extraction_type"] or "") == "html":
                raw = _strip_tags(raw)
            keys.append(key)
            texts.append(raw)
            docs_tokens.append(_tokens(raw[:_TOKEN_CHARS]))
            meta[key] = {"url": row["url"], "extraction_type": row["extraction_type"],
                         "fetched_at": row["fetched_at"], "ttl": row["ttl"]}

    scores = _bm25(set(qtokens), docs_tokens)
    pool_order = sorted(range(len(keys)), key=lambda i: -scores[i])

    engine = "bm25"
    relevance: dict[int, float] = {}
    dropped_low = 0
    try:
        from dhole_mcp.reranker import ensure_reranker, raw_thresholds
        rr = await ensure_reranker(download=False)
        if rr is not None:
            pool = pool_order[:_RERANK_POOL]
            nn = rr.score(q, [texts[i][:_RERANK_CHARS] for i in pool])
            engine = "neural"
            relevance = dict(zip(pool, nn))
            # raw_med 是这台机器**当前模型**实测出的跑题线（搜索路径对 621 个
            # 跨题对量过：跑题带 ~1e-4，对题带 0.93+，两簇几乎不相交；中带是
            # "沾边"——回忆要指针，噪声行剪掉，中带留给摘录自己说话）。
            _, raw_med = raw_thresholds()
            kept_pool = [i for i in pool if relevance[i] >= raw_med]
            dropped_low = len(pool) - len(kept_pool)
            pool_order = sorted(kept_pool, key=lambda i: -relevance[i]) \
                + pool_order[_RERANK_POOL:]
    except Exception as e:  # noqa: BLE001 - 神经档缺席是常态不是故障
        logger.debug("recall neural rerank skipped: %s", e)
        engine = "bm25"

    # R02-007（两轮同形：R01 P04 · R02 P10）：同一 URL 的多个抓取变体（markdown/html、
    # 不同时刻）在 recall 里逐行出门，is_stale 标注互相打架——「到底信哪行」。合并
    # 在截断**之前**做：被合并的变体不占 limit 名额，胜者是 fetched_at 最新的那枚，
    # is_stale 自然按它算；variants=N 说明合并了几个。
    merged = _merge_url_variants([
        {"i": i, "key": keys[i], "url": meta[keys[i]]["url"],
         "fetched_at": meta[keys[i]]["fetched_at"],
         "relevance": relevance.get(i)}
        for i in pool_order])
    variants_by_key = {c["key"]: c["variants"] for c in merged
                       if c.get("variants", 1) > 1}
    pool_order = [c["i"] for c in merged]

    results: list[dict[str, Any]] = []
    result_keys: list[str] = []
    for i in pool_order[:limit]:
        key = keys[i]
        m = meta[key]
        result_keys.append(key)
        one = {
            "url": m["url"],
            "title": "",
            "snippet": _snippet(texts[i], qtokens),
            "fetched_at": _iso(m["fetched_at"]),
            "is_stale": bool(m["fetched_at"] + (m["ttl"] or 0) <= now_s),
            "age_days": round(max(0.0, now_s - m["fetched_at"]) / 86_400, 1),
            "extraction_type": m["extraction_type"] or "",
        }
        if i in relevance:
            # 交叉编码器的 sigmoid 有大致的绝对读数（搜索路径实测：对题 0.93+，
            # 跑题 ~1e-4），BM25 没有这个性质——所以只有 neural 档给分数。
            one["relevance"] = round(relevance[i], 4)
        if key in variants_by_key:
            one["variants"] = variants_by_key[key]
        results.append(one)

    # 标题只在 finalists 上取（≤ limit 行）：信封可能比正文还大（links/TOC），
    # 400 行全载是为 20 行的标题付 400 行的账。按 key 精确取——同一个 URL 可能
    # 存着 markdown / html 多行，LIMIT 1 会拿到哪行的信封是碰运气。
    async with aiosqlite.connect(db_path_s) as db:
        db.row_factory = aiosqlite.Row
        for one, key in zip(results, result_keys):
            cur = await db.execute(
                "SELECT envelope FROM cache WHERE key = ?", (key,))
            row = await cur.fetchone()
            try:
                env = json.loads(row["envelope"]) if row else {}
                one["title"] = str(((env or {}).get("metadata") or {})
                                   .get("title") or "").strip()
            except (ValueError, TypeError):
                pass

    return {
        "query": q,
        "engine": engine,
        "results": results,
        "terms_found": terms_found,
        "terms_absent": terms_absent,
        "scanned_rows": scanned,
        "candidates_scored": len(keys),
        **({"dropped_low_relevance": dropped_low} if dropped_low else {}),
        **({"clamped": [_limit_clamp]} if _limit_clamp else {}),
        "note": ("pointers, not answers - web_read the URL (fresh or cached) for "
                 "the page itself; stale rows are ones whose cache TTL has passed"),
    }
