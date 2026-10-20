"""同一份公告被四个站点交回时，响应必须说得出「这是一份文件，不是四个来源」。

现场（P02-01 祝野 · R02-001 · P2 · 组 D；健康池复验 2026-10-15 ·
sessions/P02-R5-F23-healthy-pool.jsonl 与 review/.runtime/p4/f24_measure_titles.py）：

    web_search('小家电 品牌 代工厂 更换 供应商 公告 2025')
      → rows 1/4/5/6 都是「河南省2025年家电产品以旧换新品牌厂家征集的补充公告」，
        分别来自 kf.bendibao.com / hnsswt.henan.gov.cn / chinamae.com / 又一个本地宝子站
      → 每行 raw 0.91–0.96 全部 high；顶层 `families_answered_of_asked` = 「4 of 6」；
        逐行 `engines_consensus` = 「1 of 6 (degraded_pool)」

档位没有错（同一份文件当然与查询相符 —— F23 的复验把这格量清了，所以 001 不在那一刀里）。
**缺的是身份那一维**：整封响应里没有一处说得出「第 1、4、5、6 行是同一份文件」，而两颗
现有的计数说的都是**引擎家数**（几家索引返回过这个 URL / 几家本可以表态）。记者据此会把
"多来源互证"写进稿子 —— 她写的其实是同一份文件被四个地址挂着。

动手前的测量（473 行真搜索行，来自两轮画像的 266 份会话存档）否掉了最直觉的做法：
「标题归一相等」单独用是**坏作者**。归一后标题相等的组只有 6 个，其中

    河南省…征集的补充公告   n=4   **跨 host**  ← 真同文（本缺陷的现场）
    4399网页游戏            n=4   同 host     ← 同一站的四个不同页
    Milvus Vector Database Documentation  n=31  同 host ← 文档站每页同一个 <title>
    Learn Milvus…           n=17  同 host     ← 同上
    Community / GitHub      n=4 / n=3 同 host  ← 通用词

也就是说：**假合并的形状全都在同一个 host 里**（站点标题被每页重复），真同文那一组恰好是
唯一跨 host 的。因此判据是「归一标题相等 **且** host 不同」，再加一道通用词的闸（归一后太短
的标题载不下一份文件的身份：实测假组里 'community'=9、'github'=6，真组=24）。

契约（本文件钉住）：
  * 跨 host 的归一标题相等 → 响应顶层给出组（哪几个 `position`、几个 host、标题原文），
    并在那句话旁边（`fetch_hint`）说「这是一份文件被 N 个地址挂着，不是 N 个独立来源」；
  * **反面锁（实测的假形状）**：同一 host 下的同名行一律不成组；归一后短于闸门的通用词不成组；
    标题互不相同的一轮不许多出任何东西；
  * **交付一位不动**：成组不许丢行、不许改序、不许改档位 —— 这一刀只加一维读数；
  * 站点后缀要剥得动：「标题 - 开封本地宝」与「标题 | 中国采购与招标网」是同一份文件；
  * 计数的作者唯一：host 由既有的 `_registrable_domain` 数，不许在组判定里写第二套域名规则。
"""

from __future__ import annotations

from dhole_mcp.search import (SearchResult, _same_document_groups, compute_fetch_hint)


def _row(pos: int, title: str, url: str, raw: float = 0.9,
         snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10,
                        relevance_raw=raw, fetch_relevance="high")


ANNOUNCE = "河南省2025年家电产品以旧换新品牌厂家征集的补充公告"
#: 重发的站点带着同一份通知的正文 —— 这条共享的 literal 是同文判据的第二条腿
#: （产品名相同的四个不同页面没有它，实测那一发就被这条腿切开了）。
COPY_SNIPPET = ("具体可参照《河南省2025年家电及3C产品以旧换新品牌厂家征集公告》"
                "要求,进行厂家报名。咨询电话 0371-65897")

COPY_ROWS = [
    _row(1, ANNOUNCE + "- 开封本地宝", "http://kf.bendibao.com/news/202519/20495.shtm",
         snippet=COPY_SNIPPET),
    _row(2, "2025年第五批家电以旧换新企业公示名单", "http://swj.zhuzhou.gov.cn/c16568/x.html",
         snippet="2025年第五批家电以旧换新企业公示名单，共 132 家"),
    _row(3, ANNOUNCE + " | 中国采购与招标网", "https://www.chinamae.com/a/3108794.html",
         snippet=COPY_SNIPPET),
    _row(4, "河南省商务厅-" + ANNOUNCE, "https://hnsswt.henan.gov.cn/2025/01-06/3108794.html",
         snippet=COPY_SNIPPET),
    _row(5, ANNOUNCE + "– 本地宝", "http://zz.bendibao.com/news/20496.shtm",
         snippet=COPY_SNIPPET),
]


# ─── 1 · 同一份文件要说得出是哪几行 ───

def test_the_copies_of_one_announcement_form_one_group():
    groups = _same_document_groups(COPY_ROWS)
    assert len(groups) == 1, groups
    g = groups[0]
    assert sorted(g["positions"]) == [1, 3, 4, 5], g
    assert g["hosts"] >= 3, g          # bendibao（两个子站）/ chinamae / henan.gov.cn
    assert ANNOUNCE in g["title"], g    # 标题原文端出来，不是编号


def test_the_hint_says_one_document_not_four_sources():
    rows = COPY_ROWS
    hint = compute_fetch_hint(rows, query="小家电 品牌 代工厂 公告",
                              same_document_groups=_same_document_groups(rows))
    low = hint.lower()
    assert "document" in low and "republished" in low, hint
    assert "independent sources" in low, hint
    assert "1, 3, 4, 5" in hint, hint


def test_a_site_that_prepends_its_own_name_is_the_same_document():
    """官方那份把站名写在**前面**（"河南省商务厅-…"）：只按相等会漏掉它。

    473 行真语料上加上"包含"这一条多出的配对是 1，而它就是现场那一份（跨 3 个 host）——
    漏掉官方那一份的组，正是调用方最需要看见的那一个。
    """
    rows = [_row(1, ANNOUNCE, "https://kf.bendibao.com/a", snippet=COPY_SNIPPET),
            _row(2, "河南省商务厅-" + ANNOUNCE, "https://hnsswt.henan.gov.cn/b",
                 snippet=COPY_SNIPPET)]
    g = _same_document_groups(rows)
    assert len(g) == 1 and g[0]["positions"] == [1, 2], g


def test_a_containment_below_the_length_floor_does_not_merge():
    """包含不许把短标题吸进长标题：两侧都要过那道闸（实测的 'Community' 一族长这样）。"""
    shared = "同一句共享的正文，用来把第二条腿（snippet 重合）排除在外"
    rows = [_row(1, "教程", "https://a.example.org/x", snippet=shared),
            _row(2, "Python 异步教程 第三章 并发限制与信号量", "https://b.example.net/y",
                 snippet=shared)]
    assert _same_document_groups(rows) == []


# ─── 2 · 反面锁：实测的假合并形状 ───

def test_a_site_title_repeated_on_every_page_of_one_host_is_not_a_group():
    """Milvus 那一族的形状：文档站每页同一个 <title>，31 行同 host —— 那不是同文。"""
    shared = "同一句共享的正文：挡住这一格的必须是 host 那条腿，不是 snippet 那条"
    rows = [_row(i, "Milvus Vector Database Documentation", f"https://milvus.io/docs/p{i}.md",
                 snippet=shared) for i in range(1, 8)]
    assert _same_document_groups(rows) == [], "同 host 的同名行被并成了份数"


def test_a_generic_word_is_not_a_document_name():
    """'Community' / 'GitHub' 这种通用词跨 host 也不成组：它载不下一份文件的身份。"""
    shared = "同一句共享的正文：挡住这一格的必须是标题长度那道闸"
    rows = [_row(1, "Community", "https://forum.example.org/t/1", snippet=shared),
            _row(2, "Community", "https://chat.example.net/rooms", snippet=shared),
            _row(3, "GitHub", "https://github.com/a", snippet=shared),
            _row(4, "GitHub", "https://gitlab.com/b", snippet=shared)]
    assert _same_document_groups(rows) == []


def test_the_same_head_on_different_pages_is_not_one_document():
    """**实测到的假合并本体**（sessions/P02-R5-F24-post.jsonl 的 C 格，真跑出来的那一发）：
    四个不同页面把产品名 "Milvus Vector Database" 当作标题的头，剥掉站点后缀一模一样、
    host 还各不同。产品名会相同，一份文件的正文不会 —— 第二条腿（共享的一段 literal）
    就是为这一格加的：实测真同文那一对共享 34 个字，而这一族的页面各有各的正文。
    """
    rows = [_row(1, "Milvus vector database documentation | Siddesh V R",
                 "https://www.linkedin.com/posts/x",
                 snippet="I built a retrieval pipeline last week and here is what I learned "
                         "about collection schemas and shard counts"),
            _row(2, "Milvus Vector Database - photos and vectors", "https://dishcuss.com/v",
                 snippet="Photo search experiments with clip embeddings running entirely on a "
                         "laptop gpu and a local index"),
            _row(4, "Milvus Vector Database - KodeKloud", "https://notes.kodekloud.com/n",
                 snippet="Course notes covering installation, cluster topology and the "
                         "partition strategy used in production")]
    assert _same_document_groups(rows) == []


def test_a_round_of_distinct_documents_adds_nothing():
    rows = [_row(1, "河南省商务厅-河南省2025年家电及3C产品以旧换新品牌厂家征集公告",
                 "https://hnsswt.henan.gov.cn/a.html"),
            _row(2, "2025年第五批家电以旧换新企业公示名单", "https://swj.zhuzhou.gov.cn/b.html")]
    assert _same_document_groups(rows) == []
    hint = compute_fetch_hint(rows, query="家电 以旧换新 公告",
                              same_document_groups=_same_document_groups(rows))
    assert "republished" not in hint.lower(), hint


def test_grouping_never_moves_or_retiers_a_row():
    """这一刀只加一维读数：行序、position、档位都不许因为成组而变。"""
    before = [(r.position, r.url, r.fetch_relevance) for r in COPY_ROWS]
    _same_document_groups(COPY_ROWS)
    after = [(r.position, r.url, r.fetch_relevance) for r in COPY_ROWS]
    assert before == after


def test_a_shared_product_name_in_the_middle_is_not_the_same_document():
    """两侧复验真撞上的那一次假合并：共同的产品名含在**中间**，不是同一份文件。

    现场（post 第一趟的 C 格，query='milvus vector database documentation'）：
    「Milvus Vector Store | Nodes | n8n Docs」与一篇更长的教程标题共享中间那颗产品名，
    未锚定的"包含"把两行并成了一组 —— 于是规则只认贴在两端的站名装饰。
    """
    rows = [_row(4, "Milvus Vector Store | Nodes | n8n Docs", "https://docs.n8n.io/milvus",
                 snippet="同一句共享的正文：挡住这一格的必须是「只认两端贴合」这条，不是 snippet"),
            _row(8, "使用 NVIDIA RAG 与 Milvus Vector Store 搭建智能检索系统",
                 "https://juejin.cn/post/1",
                 snippet="同一句共享的正文：挡住这一格的必须是「只认两端贴合」这条，不是 snippet")]
    assert _same_document_groups(rows) == []


def test_the_host_count_has_one_author():
    """组里的 host 由既有的 `_registrable_domain` 数出来，判定里没有第二套域名规则。"""
    import inspect

    from dhole_mcp import search as search_mod
    src = inspect.getsource(search_mod._same_document_groups)
    assert "_registrable_domain" in src, src
    assert "tldextract" not in src and "PUBLIC_SUFFIX" not in src, src
