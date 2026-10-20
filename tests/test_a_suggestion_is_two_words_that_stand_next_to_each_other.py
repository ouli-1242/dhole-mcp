r"""建议词得是行里**真出现过的相邻两词**：两个词之间只许站着空白。

现场（台账 R02-069 的第二、第三格；行原文逐字取自 `review/round-02/sessions/calls.jsonl`，
量程序与输出 `review/.runtime/p4/f55_measure.py` / `f55_measure.txt` / `f55_pairs.txt`）：

    「代工厂 换供应商 爬坡期 品牌」6 行 → 交回 `com rain`
        两行的标题位就是它们自己的地址：`new.qq.com/rain/a/20230216A0739K00`（new.qq.com）与
        `news.qq.com/rain/a/20260505A068KK00`（news.qq.com）。词对 `com` + `/` + `rain` 两家各一次
        ⇒ df=2 ⇒ 过得了「一家的措辞不算话题」那道门。
    「光伏 硅料 价格 周度」3 行 → 交回 `wxy88 top`
        `wxy88.top - 文献云图书馆`（域名 wxy88.top）与 `wxy88.top Uptime, Downtime and Server
        Information - Who.is`（域名 who.is）—— 词对 `wxy88` + `.` + `top` 是**一枚域名被点号切开**。
    「community self-organization urban studies open access monograph」8 行 → 交回 5 条，其中
        `definition meaning` 来自 `COMMUNITY definition and meaning`（Collins，gap ` and `）与
        `COMMUNITY Definition & Meaning`（Merriam-Webster，gap ` & `）—— 跳过中间那个词配出来的，
        行里从没这么写过；`som cluster` 来自 `maps (SOM). The cluster analysis`（gap `). the `）与
        `Map (SOM) and Cluster Analysis`（gap `) and `）—— 句子已经换掉了。

台账原来把 `wxy88 top` 猜成「两个真站点在各自正文里引了同一个站名，形状上 df=2 成立 ⇒ 要不要挡它
是产品判断」（R02-069 的 notes）。这一趟量完：**那个猜是错的** —— 它的 gap 是 `.`，两枚都是域名里的
一段，与 `com rain` 同族。改判记在台账，不另立一条。

判据只有一条，并把 R02-F52 那条（「中间站着无空格文字就不算相邻」吃成自己的特例）：`_WORD_RE`
连着找出来的两个词，只有**中间那段原文全是空白**时才算一个词组。

代价也量出来贴在账上（同一趟，20 发去重真 SERP）：13 条交回 → 8 条。被挡掉的 5 条 gap 分别是
`/`（com rain）、`.`（wxy88 top）、`.`（`asyncio gather` ← 行里写的是 `asyncio.gather`）、
` and `/` & `（definition meaning）、`). the `（som cluster）。**后两条里的第一条是真代价**：
标识符里那一点从今天起不再产出建议词。`.gap` 分不清 `asyncio.gather` 与 `wxy88.top`
（要分清就得查公共后缀表 = 词表路线，`review/decisions.md` 2026-10-16 11:28 已经不走）
⇒ 这一族只能一起挡，不假装它免费。

反面锁（同一趟量出的干净那族，gap 全是空白）：`open access` / `async web` / `limit concurrent` /
`community definition` / `cluster analysis` / `english dictionary` 一条不许少、一条不许变。
"""

from __future__ import annotations

import inspect
import re

from dhole_mcp.search import SearchResult, _related_queries


def _row(pos: int, title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10, relevance_raw=0.9,
                        fetch_relevance="high")


# ─── 现场原文（逐字取自本轮真 SERP，未改写） ───────────────────────────────
QQ_ADDRESS_ROWS = [
    _row(3, "new.qq.com/rain/a/20230216A0739K00", "https://new.qq.com/rain/a/20230216A0739K00",
         "原因在于赫比是 iRobot 的代工厂,是小米供应链里唯一有做扫地机经验的厂商。"),
    _row(4, "news.qq.com/rain/a/20260505A068KK00", "https://news.qq.com/rain/a/20260505A068KK00",
         "(1)人形机器人:马斯克发声表明Optimus项目仍稳步推进中,后续产品发布、量产爬坡值得重点关注。"),
]
WXY88_ROWS = [
    _row(0, "wxy88.top - 文献云图书馆", "http://wxy88.top/",
         "文献云图书馆 学术论文下载,https://lib.wenx.top/ 论文下载 知网论文 维普期刊 万方论文 "
         "读秀下载 英文期刊 中文文 …"),
    _row(2, "wxy88.top Uptime, Downtime and Server Information - Who.is",
         "https://who.is/uptime/wxy88.top",
         "View uptime status, downtime, and server information for wxy88.top."),
]
COMMUNITY_QUERY = "community self-organization urban studies open access monograph"
COMMUNITY_ROWS = [
    _row(1, "Precipitation Regionalization Using Self-Organizing Maps for Mumbai ...",
         "https://www.scirp.org/JOURNAL/papercitationdetails?paperid=87616&journalid=46",
         "analysis using self-organizing maps (SOM). The cluster analysis results show six clusters "
         "... (urban area) and mountain and hills areas (Sanjay Gandhi National Park located in "
         "the …"),
    _row(3, "COMMUNITY definition and meaning | Collins English Dictionary",
         "https://www.collinsdictionary.com/dictionary/english/community",
         "Community is friendship between different people or groups, and a sense of having "
         "something in common. The retirement home …"),
    _row(5, "COMMUNITY Definition & Meaning - Merriam-Webster",
         "https://www.merriam-webster.com/dictionary/community",
         "5 days ago · The meaning of COMMUNITY is a unified body of individuals. "
         "How to use community in a sentence."),
    _row(8, "Performance analysis of partial self organizing maps for radar signal ...",
         "https://m.zhangqiaokeyan.com/academic-conference-foreign_meeting-206928_thesis/020514217923.html",
         "自组织图和模糊C均值方法进行战略分组的性能 2. Evaluating the Performance of 16 Egyptian "
         "Wheat Varieties Using Self-Organizing Map (SOM) and Cluster Analysis [J] . O.M. …"),
]


def _gap_of(rows, phrase: str) -> list[str]:
    """把某一建议词组在行原文里实际用的那段间隔抓出来 —— 判据自己说的不算，读原文。"""
    from dhole_mcp import search
    out = []
    a, b = phrase.split(" ", 1)
    for r in rows:
        for field in (r.title or "", r.snippet or ""):
            text = field.lower()
            hits = [m for m in search._WORD_RE.finditer(text)
                    if m.group() not in search._STOPWORDS]
            for prev, cur in zip(hits, hits[1:]):
                if prev.group() == a and cur.group() == b:
                    out.append(text[prev.end():cur.start()])
    return out


# ─── 1 · 三格都不是词组 ─────────────────────────────────────────────────────

def test_an_address_path_segment_is_not_a_phrase():
    got = _related_queries("代工厂 换供应商 爬坡期 品牌", QQ_ADDRESS_ROWS)
    assert "com rain" not in got, (got, _gap_of(QQ_ADDRESS_ROWS, "com rain"))
    assert got == [], got


def test_a_domain_label_split_by_a_dot_is_not_a_phrase():
    got = _related_queries("光伏 硅料 价格 周度", WXY88_ROWS)
    assert "wxy88 top" not in got, (got, _gap_of(WXY88_ROWS, "wxy88 top"))


def test_a_span_that_skips_a_word_is_not_a_phrase():
    """`COMMUNITY Definition & Meaning` 跳过 `&` 配出来的东西，行里从来没有这个说法。"""
    got = _related_queries(COMMUNITY_QUERY, COMMUNITY_ROWS)
    assert "definition meaning" not in got, (got, _gap_of(COMMUNITY_ROWS, "definition meaning"))


def test_a_pair_across_a_sentence_break_is_not_a_phrase():
    """gap 是 `). the ` / `) and ` —— 句子已经换掉了。"""
    got = _related_queries(COMMUNITY_QUERY, COMMUNITY_ROWS)
    assert "som cluster" not in got, (got, _gap_of(COMMUNITY_ROWS, "som cluster"))


# ─── 2 · 反面锁：gap 全是空白的那一族一条不少 ───────────────────────────────

def test_the_whitespace_bigrams_in_the_same_rows_still_ship():
    got = _related_queries(COMMUNITY_QUERY, COMMUNITY_ROWS, n=12)
    assert {"community definition", "cluster analysis"} <= set(got), got


def test_the_clean_family_from_other_rounds_is_untouched():
    rows = [
        _row(0, "open access policy notes", "https://a.example.org/1",
             "repository rules, open access mandate, and cluster analysis"),
        _row(1, "open access policy notes", "https://b.example.net/2",
             "repository rules, open access mandate, and cluster analysis"),
        _row(2, "async web scraping guide", "https://c.example.com/3",
             "async web scraping tips: limit concurrent requests"),
        _row(3, "async web scraping guide", "https://d.example.org/4",
             "async web scraping tips: limit concurrent requests"),
    ]
    got = _related_queries("web scraping concurrency", rows, n=12)
    assert {"open access", "cluster analysis", "async web", "limit concurrent"} <= set(got), got


def test_unspaced_prose_is_still_not_a_phrase():
    """R02-F52 那一格被这条判据吃成特例：中间站着中文同样不是空白。"""
    rows = [
        _row(0, "开放获取政策", "https://a.example.org/1", "先说 open access 的，on 发布 per 要求"),
        _row(1, "开放获取声明", "https://b.example.net/2", "先说 open access 的，on 发布 per 要求"),
    ]
    got = _related_queries("repository licensing", rows)
    assert "on per" not in got, got
    assert "open access" in got, got


# ─── 3 · 计数与判决同一个口径（R02-070 量完之后必须成立） ───────────────────

def test_the_recurring_count_uses_the_same_adjacency_rule_as_the_decision():
    st: dict = {}
    got = _related_queries("代工厂 换供应商 爬坡期 品牌", QQ_ADDRESS_ROWS, stats=st)
    assert got == [], got
    assert st["words_total"] > 0, st           # 行里确实切得出词
    assert st["recurring"] == 0, st            # 但没有一个按同一条相邻判据算数


def test_only_one_adjacency_rule_is_left_in_the_miner():
    src = inspect.getsource(_related_queries)
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "_CJK_TERM_RE" not in body, "相邻判据不许留两条：空白判据已经把它吃成特例"
    assert re.search(r"gap\.strip\(\)", body), "必须看得见那条「中间只许空白」的判据"


# ─── 4 · 交付面的终态锁：交出的每一条都能在某一行的原文里逐字找到 ───────────

def test_every_shipped_phrase_appears_verbatim_in_a_row():
    for q, rows in (("代工厂 换供应商 爬坡期 品牌", QQ_ADDRESS_ROWS),
                    ("光伏 硅料 价格 周度", WXY88_ROWS),
                    (COMMUNITY_QUERY, COMMUNITY_ROWS)):
        for phrase in _related_queries(q, rows, n=12):
            texts = [(f.title + " " + f.snippet).lower() for f in rows]
            assert any(phrase in t for t in texts), (q, phrase, _gap_of(rows, phrase))
