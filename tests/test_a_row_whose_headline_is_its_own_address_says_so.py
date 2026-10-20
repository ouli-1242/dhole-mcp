# -*- coding: utf-8 -*-
"""R02-003 · 一行交回的「标题」就是它自己的地址时，行上必须说得出这件事。

题面（P02-01 庄砚，调查记者）很具体：她在结果列表上**决定点哪一条**，标题是唯一可用
信号。她记下的形状是「title 就是 URL 本身」。先量了这个形状 —— 逐字相等那一档在
1,067 行实测语料里**一行都没有**（0.0%）：引擎交回的是**去掉了协议的地址**
（`cnblogs.com/paihangbang/p/20553999`），有时后面还带一个版面省略号。所以这条判据
不能按「相等」写，只能按「本行标题是本行地址的前缀」写。

尺子的三条闸门各有实测出处（语料 = 15 轮线上 134 行 + `evals/corpus/serp` 冻结页
890 行 + `tests/engine_fixtures` 43 行）：
* 命中 14 行（1.4%），全部出自同一家（yandex），其中冻结页那一行是
  `paymentauth.org/draft-payment-transport-mcp-00.txt` —— 一个纯文本文件本来就没有标题。
* 必须锚在**本行自己的 href** 上：只看标题像不像地址那条尺，会在这 1,067 行里另外
  误伤 39 行（`asyncio.Semaphore()信号量控制…`、`从32元到105元，年内涨幅超150...` ——
  代码名里的点和版面截断的省略号都会响）。
* 必须要求标题里有一个点：否则三家把**站名**印在标题位的版面会被读成地址 ——
  冻结页里实测 3 行（`Medium` × 2 对 medium.com、`Kubernetes` 对 kubernetes.io）。
  站名是可分辨的信息，不是缺失，所以那一族**不标**。

交付的形态：一个**跟着行走**的行级标记。不住顶层注记，理由与 `consensus_basis` 那一刀
相同 —— 读行的人就在那一刻决定点不点，把同一句话的另一半留在顶层等于少了一半。
`title` 本身一个字都不改：不许编一个标题，要的是可分辨。
"""

from __future__ import annotations

import glob
import json
import os

import pytest

from dhole_mcp import search as search
from dhole_mcp.search import SearchResponseModel, SearchResult, _build_results
from dhole_mcp.search_engines import RawResult
from dhole_mcp.wire import _wire_dump

CORPUS = os.path.join("evals", "corpus", "serp")

# 15 轮线上真检索里被这条尺量出来的 13 行（逐字抄自
# review/.runtime/p4/f39_corpus.json · sessions/P02-R5-F39-*.jsonl 同批）
ADDRESS_HEADLINES = [
    ("cnblogs.com/paihangbang/p/20553999",
     "https://www.cnblogs.com/paihangbang/p/20553999"),
    ("k.sina.com.cn/article_7879996653_1d5af34ed02001k82c.html?...",
     "https://k.sina.com.cn/article_7879996653_1d5af34ed02001k82c.html?finpagefr=p_101"),
    ("post.smzdm.com/p/a70n82dd", "https://post.smzdm.com/p/a70n82dd/"),
    ("chwang.com/ask/208673715855", "https://www.chwang.com/ask/208673715855"),
    ("m.importingtochina.com", "https://m.importingtochina.com/"),
    ("163.com/dy/article/JPNEAMQI0519AU4N.html",
     "https://www.163.com/dy/article/JPNEAMQI0519AU4N.html"),
    ("headscm.com/Fingertip/detail/id/18485.html",
     "https://www.headscm.com/Fingertip/detail/id/18485.html"),
    ("xdsyzzs.com/shangyeliutong/7775.html",
     "https://xdsyzzs.com/shangyeliutong/7775.html"),
    ("tmtpost.com/4331690.html", "https://www.tmtpost.com/4331690.html"),
    ("project-gutenberg.github.io/Pincong/post/73e2aebbb0d4f9a1644792...",
     "https://project-gutenberg.github.io/Pincong/post/73e2aebbb0d4f9a1644792a9f2ba46d8/"),
    ("anjxny.com/?question/347.html", "https://anjxny.com/?question/347.html"),
    ("tmtpost.com/6325814.html", "https://www.tmtpost.com/6325814.html"),
    ("pangjing.cn/6108431-2", "https://pangjing.cn/6108431-2/"),
    # 冻结语料里那一行：纯文本文件，标题位上只有地址
    ("paymentauth.org/draft-payment-transport-mcp-00.txt",
     "https://paymentauth.org/draft-payment-transport-mcp-00.txt"),
]

# 只看形状那条尺会误伤的行（同一批语料里逐字抄回）：真标题，带代码名里的点或版面省略号
LOOKS_LIKE_AN_ADDRESS_BUT_IS_A_HEADLINE = [
    ("中国制造业品牌演进:从代工困境到创新突围_行业要闻_新闻资讯_译...",
     "http://www.yakugen.com.cn/news/show-24109.html"),
    ("从32元到105元，年内涨幅超150...",
     "http://news.cnfol.com/zhengquanyaowen/20260629/32293397.shtml"),
    ("赋能中国宠食品牌，知名德国宠物食品企业animonda...",
     "https://www.petdhw.com/ganzhou-baike-51043.html"),
    ("从吃饱到被爱：413...", "https://post.smzdm.com/p/ak82l4vk/"),
    ("asyncio.Semaphore限制协程并发数-CSDN博客",
     "https://blog.csdn.net/abc/article/details/12345678"),
    ("Python怎么限制并发数_asyncio.Semaphore()信号量控制同时执行的协程数量(如限制请求)-Python教程-PHP中文网",
     "https://www.php.cn/faq/987654.html"),
    ("SQLite3数据库并发异步解决方案及aiosqlite的应用方法...",
     "https://www.jb51.net/article/111111.htm"),
    ("在.NET中使用Milvus向量数据库-.NET博客",
     "http://mp.weixin.qq.com/s?src=11&timestamp=1792094505&ver=7028"),
]

# 站名印在标题位：可分辨的信息，不是地址，所以不标（冻结页实测 3 行）
SITE_NAME_HEADLINES = [
    ("Medium", "https://medium.com/@yadav.navya1601/understanding-mcp"),
    ("Medium", "https://medium.com/@gauravpatilsde/understanding-rust"),
    ("Kubernetes", "https://kubernetes.io/"),
]


def _row(title: str, url: str, **kw) -> SearchResult:
    d = dict(title=title, url=url, snippet="一段正文，句末有标点。" * 2,
             source="yandex", position=1, relevance_score=0.5)
    d.update(kw)
    return SearchResult(**d)


@pytest.mark.parametrize("title_url", ADDRESS_HEADLINES)
def test_each_captured_row_is_marked(title_url):
    title, url = title_url
    assert _row(title, url).title_is_address is True, (title, url)


@pytest.mark.parametrize("title_url", LOOKS_LIKE_AN_ADDRESS_BUT_IS_A_HEADLINE)
def test_a_real_headline_that_merely_looks_like_an_address_is_not_marked(title_url):
    """锚在本行 href 上是这条尺的全部价值：形状判据在这批语料里会误伤 39 行。"""
    title, url = title_url
    assert _row(title, url).title_is_address is False, (title, url)


@pytest.mark.parametrize("title,url", SITE_NAME_HEADLINES)
def test_a_site_name_in_the_headline_slot_is_not_an_address(title, url):
    """点这一闸门挡住的正是「站名当标题」：那是可分辨的信息，不是缺失。"""
    assert _row(title, url).title_is_address is False, (title, url)


def test_the_ruler_compares_a_row_only_with_its_own_address():
    """别行的地址出现在这一行的标题位上，不标 —— 这条判据没有全局表、没有关键词。"""
    row = _row("chwang.com/ask/208673715855", "https://www.tmtpost.com/4331690.html")
    assert row.title_is_address is False


def test_the_mark_never_rewrites_the_title_it_describes():
    """不许编一个标题：交回的字符串与引擎给的一字不差。"""
    title, url = ADDRESS_HEADLINES[0]
    row = _row(title, url)
    assert row.title == title
    assert row.title_is_address is True


def test_the_exact_copy_of_the_url_is_also_an_address_headline():
    """题面说的「逐字相等」在实测里是 0 行，但那形状仍然在这一条尺的覆盖里。"""
    url = "https://example.com/spec/v2.txt"
    assert _row(url, url).title_is_address is True


def test_a_headline_with_a_space_inside_it_is_not_an_address():
    """版面不会把一个带空格的字符串印成地址；带空格就是标题。"""
    assert _row("example com 首页", "https://example.com/home").title_is_address is False
    assert _row("a b", "https://a b.example.com/x").title_is_address is False


def test_case_and_full_width_form_do_not_change_the_verdict():
    assert _row("CNBLOGS.COM/PaihangBang/P/20553999",
                "https://www.cnblogs.com/paihangbang/p/20553999").title_is_address is True


def test_the_mark_is_minted_by_the_row_builder_not_by_the_caller():
    """走真链路（RawResult → _build_results）也要有这颗旗子。"""
    title, url = ADDRESS_HEADLINES[2]
    ranked = [RawResult(title=title, url=url, snippet="一段正文", source="yandex"),
              RawResult(title="Redis persistence | Redis",
                        url="https://redis.io/docs/latest/operate/",
                        snippet="一段正文", source="bing")]
    rows = _build_results("redis persistence", ranked, [0.9, 0.5], total_families=2)
    assert rows[0].title_is_address is True
    assert rows[1].title_is_address is False


def test_a_cached_row_written_before_the_mark_still_gets_it():
    """缓存里旧 payload 没这颗键：它是纯函数，重算而不是当成 false 交回去。"""
    stale = {"title": ADDRESS_HEADLINES[0][0], "url": ADDRESS_HEADLINES[0][1],
             "snippet": "一段正文", "position": 1, "relevance_score": 0.4}
    assert SearchResult(**stale).title_is_address is True


def test_both_ship_ports_of_the_row_agree_with_the_ruler_row_for_row():
    """行只有两个产地，两处的这颗旗子都必须逐行等于那条纯判据。

    这颗是「缺席才铸」：`tests/test_wire_compaction.py` 那条门要求每一格都能被独立设成
    非默认值，覆盖式的判据会把门自己的前提踩扁 —— 所以「没人传这颗」得由这一发钉住：
    真链路建出来的行与缓存 payload 复原的行，都不带与判据不同的旗子。
    """
    pairs = ADDRESS_HEADLINES[:4] + LOOKS_LIKE_AN_ADDRESS_BUT_IS_A_HEADLINE[:4]
    ranked = [RawResult(title=t, url=u, snippet="一段正文", source="yandex")
              for t, u in pairs]
    built = _build_results("代工厂", ranked, [0.5] * len(pairs), total_families=1)
    replayed = [SearchResult(**{"title": t, "url": u, "snippet": "一段正文",
                                "position": i + 1, "relevance_score": 0.5})
                for i, (t, u) in enumerate(pairs)]
    for rows in (built, replayed):
        assert [r.title_is_address for r in rows] == [
            search._headline_is_own_address(r.title, r.url) for r in rows]


def test_the_wire_omits_the_mark_on_an_ordinary_row_and_charges_the_other_one():
    """绝大多数行不付这颗字符（缺席 = 默认）；被标的行才付。

    一颗标记的代价在这里量：`", "title_is_address": true` 那一格实测 <30 字符，
    而它只落在 1,067 行里的那 14 行上（1.4%）。
    """
    title, url = ADDRESS_HEADLINES[0]
    resp = SearchResponseModel(
        query="代工厂 换供应商",
        results=[_row("Redis persistence | Redis", "https://redis.io/docs/",
                      position=1), _row(title, url, position=2)])
    dumped = _wire_dump(resp)
    clean, marked = dumped["results"][0], dumped["results"][1]
    assert "title_is_address" not in clean, sorted(clean)
    assert marked["title_is_address"] is True
    assert len(json.dumps({"title_is_address": True})) < 30


def test_the_marked_title_travels_onto_the_fetched_page_row():
    """`fetched_pages` 那一行抄的是搜索行的标题，所以那颗旗子也得跟着抄过去。"""
    from dhole_mcp.server import _inline_budget_hole

    title, url = ADDRESS_HEADLINES[3]
    hole = _inline_budget_hole(_row(title, url))
    assert hole["title"] == title
    assert hole["title_is_address"] is True
    plain = _inline_budget_hole(_row("Redis persistence | Redis", "https://redis.io/docs/"))
    assert "title_is_address" not in plain, sorted(plain)


def test_the_ruler_has_one_author_and_the_row_only_asks_it():
    """判据只有一位作者：行模型与 `_headline_is_own_address` 必须逐行同意。"""
    for title, url in ADDRESS_HEADLINES:
        assert search._headline_is_own_address(title, url) is True, title
    for title, url in LOOKS_LIKE_AN_ADDRESS_BUT_IS_A_HEADLINE + SITE_NAME_HEADLINES:
        assert search._headline_is_own_address(title, url) is False, title


def test_the_field_description_says_what_the_mark_measures_and_what_it_does_not():
    desc = SearchResult.model_fields["title_is_address"].description
    assert desc, "这颗旗子必须自述"
    assert "own address" in desc, desc[:300]
    assert "www." in desc, desc[:300]
    assert "scheme" in desc, desc[:300]
    assert "not a claim" in desc or "says nothing about" in desc, desc[:300]


def test_the_frozen_serp_corpus_marks_the_one_row_that_really_is_an_address():
    """890 行冻结真版面里命中 1 行、误伤 0 行 —— 这条尺的分界是量出来的。

    语料随改版不动（sha256 记在 .meta.json 里），所以这一发可以当回归锁。
    """
    from dhole_mcp.search_metasearch import _TEXT_ENGINES

    rows, marked = 0, []
    for path in sorted(glob.glob(os.path.join(CORPUS, "*.html"))):
        engine = os.path.basename(path).split("__")[1].split(".")[0]
        if engine not in _TEXT_ENGINES:
            continue
        eng = _TEXT_ENGINES[engine](proxy=None, timeout=10, verify=True)
        html = open(path, encoding="utf-8", errors="replace").read()
        for t in eng.extract_results(html) or []:
            title = t.title or ""
            href = t.href or ""
            if not title:
                continue
            rows += 1
            if _row(title, href).title_is_address:
                marked.append((engine, title))
    assert rows >= 800, rows
    assert marked == [("yandex",
                       "paymentauth.org/draft-payment-transport-mcp-00.txt")], marked


def test_the_manual_documents_the_mark_and_its_measured_band():
    """手册里这一格必须按**仓里那条覆盖门同一口径**找得到：词边界，不带 `results[].` 前缀。

    门（`test_every_response_field_is_in_the_manual.py`）第一次跑就因为我把手册行写成
    `results[].title_is_address` 而判它"查无此人" —— 我的锁不能比门更松，所以这里用同一个
    词边界匹配，而不是 `'title_is_address' in corpus` 那种子串自查。
    """
    import re

    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert re.search(r"(?<![\w.])title_is_address(?![\w])", corpus), (
        "手册里这一格只在 `results[].` 前缀下出现过：按覆盖门的口径它查无此人")
    assert "site name" in corpus, corpus[:400]
