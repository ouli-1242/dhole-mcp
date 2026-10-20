r"""空着的建议词要说得出「这一轮的行根本配不出词对」—— 用结构量，不用新阈值（R02-071）。

R02-F53 给三种空各配了一句，其中①的触发条件是 `words_total == 0`。这一趟量完发现那个零
不是形状，只是形状的一种极端：

    （`review/.runtime/p4/f56_measure2.txt`，25 发去重真 SERP，逐发数「只隔空白的词对」有几个）
      「乡村阅读 公益项目 年度报告 服务人次 2024 2025」 8 行 · words=0  · 空白词对 0 → 今天开口
      「就那种做乡村阅读的，他们自己发了数据的那种」    6 行 · words=0  · 空白词对 0 → 今天开口
      「光伏 硅料 价格 周度」                    8 行 · words=1  · 空白词对 0 → 今天沉默
      「这个呢」                               4 行 · words=1  · 空白词对 0 → 今天沉默
      「代工厂 换供应商 爬坡期 品牌」              3 行 · words=3  · 空白词对 0 → 今天沉默
      「储能 电芯 出口 数据」                    3 行 · words=5  · 空白词对 0 → 今天沉默
      「小家电 品牌 代工厂 更换 供应商 公告 2025」    6 行 · words=5  · 空白词对 0 → 今天沉默
      「代工厂 换供应商 爬坡期 品牌」              6 行 · words=12 · 空白词对 0 → 今天沉默
      「那个」                                6 行 · words=11 · 空白词对 0 → 今天沉默
      「python asyncio semaphore concurrency」 4 行 · words=14 · 空白词对 0 → 今天沉默
      ── 以上 10 发的「切得出词」都不为 0，可今天与「真的有证据、只是没重复」同格 ──
      「乡村阅读 公益项目 年度报告 服务人次」        8 行 · words=6  · 空白词对 1 → 沉默（**该继续沉默**）
      「那几本里有开放获取的吗」                 4 行 · words=8  · 空白词对 1 → 沉默（同上）
      「ceftriaxone diazepam Y-site compatibility chart pdf」等 6 发 · 空白词对 19..82 → 沉默（②的真意）
      所有「有交回」的轮子空白词对都 >= 1（最少 7）—— 交回与开口的集合天然不冲突

判据因此不是一条新画的百分比线（先量过再画线这一条本轮吃过两次教训：R02-049、R02-070），
而是**同一个函数已经在用的那个形状的极端**：`_related_queries` 的单位是「只隔空白的相邻两词」，
某一发连一对都造不出来时，`[]` 说的就不是「没有跨源重复」，而是「这一轮的行没有词边界」——
换问法不改这一族的形状。这一格今天由 `words_total == 0` 代答，量出来它把 10 发里只认得 2 发。

句子必须对新旧两批现场都成立：旧句写「the titles and snippets carry **no** whitespace-separated
words」，而「代工厂 换供应商 爬坡期 品牌」6 行那一发切得出 12 个词 —— 那句话在这一格是**假**的。
新句带着真实个数说（`{words_total} whitespace-separated word(s)` + `none of them stand next to
another with only space between`），词数为 0 的老现场照样成立 ⇒ 这是一次收紧，不是放松
（同名老测试的期望随之改，改的理由与不放松在哪写在这里，也写在 `fix-R02-F55.md`）。
"""

from __future__ import annotations

import inspect

from dhole_mcp.search import SearchResult, _follow_up_cause, _related_queries

# 「储能 电芯 出口 数据」那一发的三行标题，逐字沿用 R02-F26 已经钉在本仓库里的真现场
CHU_NENG_QUERY = "储能 电芯 出口 数据"
CHU_NENG_ROWS = [
    SearchResult(title="ACG猎奇【重口】图片大全-ACG猎奇【重口】高清好看的图片-花瓣桃奈小娘子的画板",
                 url="https://a.example.org/1", position=1, snippet=""),
    SearchResult(title="暗网猎奇 -- 序 - 知乎", url="https://b.example.net/2", position=2, snippet=""),
    SearchResult(title="百测网-分析测试_论文润色_学术绘图_科研培训_试剂耗材_实验仪器_科研服务",
                 url="https://c.example.com/3", position=3, snippet=""),
]


def _rows(titles_urls):
    return [SearchResult(title=t, url=u, position=i + 1, snippet=s,
                         relevance_score=1.0 - i / 10, relevance_raw=0.9, fetch_relevance="high")
            for i, (t, u, s) in enumerate(titles_urls)]


# ─── 1 · 这一格该开口（改前是沉默 ⇒ 本文件第一次跑就是红的） ─────────────────

def test_a_round_with_no_pairable_words_speaks():
    st: dict = {}
    got = _related_queries(CHU_NENG_QUERY, CHU_NENG_ROWS, stats=st)
    assert got == [], got
    assert st["spaced_pairs"] == 0, st
    assert st["words_total"] > 0, st          # 切得出词，但一个相邻对都配不出来
    cause = _follow_up_cause(st, got)
    assert cause, "这一格今天什么都不说"
    assert str(st["words_total"]) in cause, cause   # 句子带着真实个数，不许说「一个词都没有」
    assert "rewording the query cannot change" in cause, cause


def test_the_zero_word_round_still_speaks():
    """老现场不许退化：一个词都切不出来的那一发仍然要说，而且说的一样是「换问法没用」。"""
    rows = _rows([("乡村阅读公益项目年度报告服务人次", "https://a.example.org/4", ""),
                  ("公益项目年度报告服务人次乡村阅读", "https://b.example.net/5", "")])
    st: dict = {}
    got = _related_queries("乡村阅读 公益项目 年度报告 服务人次 2024 2025", rows, stats=st)
    assert got == [], got
    assert st["words_total"] == 0, st
    cause = _follow_up_cause(st, got)
    assert cause and "rewording the query cannot change" in cause, cause


def test_a_latin_query_whose_rows_are_all_address_runs_speaks_too():
    """行的正文全是地址串：切得出十几个词，却没有一对只隔空白（`代工厂…` 3 行那发的形状）。"""
    rows = _rows([("new.qq.com/rain/a/20230216A0739K00", "https://new.qq.com/rain/a/x1", "代工厂供货说明"),
                  ("news.qq.com/rain/a/20260505A068KK00", "https://news.qq.com/rain/a/x2", "供应商爬坡期"),
                  ("x.cn/rain/a/202401010A000001", "https://x.cn/rain/a/202401010A000001", "品牌公告")])
    st: dict = {}
    got = _related_queries("代工厂 换供应商 爬坡期 品牌", rows, stats=st)
    assert st["spaced_pairs"] == 0, (st, got)
    assert _follow_up_cause(st, got), st


# ─── 2 · 反面锁：造得出词对的轮子继续闭嘴 ──────────────────────────────────

def test_a_round_with_one_pairable_word_stays_silent():
    """`乡村阅读 公益项目 年度报告 服务人次` 8 行那发：words=6 但空白词对 = 1 ⇒ 还是②的真沉默。"""
    rows = _rows([("年度报告 服务人次 report 2024 annual", "https://a.example.org/6", ""),
                  ("公益项目 report 乡村阅读 annual", "https://b.example.net/7", ""),
                  ("乡村阅读 service 人次 report", "https://c.example.com/8", ""),
                  ("年度 annual 服务 report", "https://d.example.org/9", ""),
                  ("项目人次 report overview", "https://e.cn/10", ""),
                  ("服务 annual 乡村 report", "https://f.cn/11", ""),
                  ("阅读 report 年度 annual", "https://g.cn/12", ""),
                  ("乡村 overview 项目 report", "https://h.cn/13", "")])
    st: dict = {}
    got = _related_queries("乡村阅读 公益项目 年度报告 服务人次", rows, stats=st)
    assert st["spaced_pairs"] > 0, st
    assert _follow_up_cause(st, got) == "", (st, got)


def test_a_healthy_latin_round_with_no_repetition_stays_silent():
    """真正的「证据不足」：词对多得是，只是没有跨两家 —— ②按 docstring 该沉默。"""
    rows = _rows([("GPT scoring notes", "https://one.example/1",
                   "tokenizer overhead dominates inference latency in serving stacks today"),
                  ("LLM notes", "https://two.example/2",
                   "inference latency grows when tokenizer overhead dominates serving")])
    st: dict = {}
    got = _related_queries("large language models", rows, stats=st)
    assert st["spaced_pairs"] > 5, st
    assert _follow_up_cause(st, got) == "", (st, got)


def test_a_round_that_ships_phrases_never_says_the_emptiness_line():
    rows = _rows([("open access policy notes", "https://a.example.org/1",
                   "repository rules, open access mandate worldwide"),
                  ("open access policy notes", "https://b.example.net/2",
                   "repository rules, open access mandate worldwide")])
    st: dict = {}
    got = _related_queries("repository licensing", rows, stats=st)
    assert "open access" in got, got
    assert _follow_up_cause(st, got) == "", got


# ─── 3 · 结构锁：判据是同一个函数已经算过的形状，不是第二把尺 ────────────────

def test_the_emptiness_line_reads_the_same_adjacency_shape_as_the_miner():
    src = inspect.getsource(_related_queries)
    assert "spaced_pairs" in src, "词对的数量必须由挖词那位自己数出来（同一把尺）"
    st = {"words_total": 12, "recurring": 0, "shipped": 0, "spaced_pairs": 0}
    assert _follow_up_cause(st, [])
    st2 = dict(st, spaced_pairs=1)
    assert _follow_up_cause(st2, []) == ""     # 不再看 words_total 是不是 0
    # 早退那一条（没有任何行）也要把新数字填上，否则「没矿」会被读成「有矿但没话」
    st3: dict = {}
    assert _related_queries("q", [], stats=st3) == []
    assert "spaced_pairs" in st3, st3


def test_the_sentence_never_claims_zero_words_when_there_are_words():
    st = {"words_total": 12, "recurring": 0, "shipped": 0, "spaced_pairs": 0}
    cause = _follow_up_cause(st, [])
    assert "no whitespace-separated words" not in cause, cause
    assert "12 whitespace-separated word" in cause, cause
