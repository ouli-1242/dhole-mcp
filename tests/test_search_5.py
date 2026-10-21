"""test_search_5.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
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
from unittest.mock import AsyncMock
from dhole_mcp import search as search_mod
from dhole_mcp.search import _parse_consensus, gate_by_consensus
from dhole_mcp.search_engines import EngineReport
from dhole_mcp.server import MasterFetchServer, ResponseModel
import re
from dhole_mcp.search import _DOC_SNIPPET_RUN_MIN_CHARS, _registrable_domain, _same_document_groups, _shared_literal_run
from dhole_mcp import search as S
from dhole_mcp.search import _drop_causes, _reputation_filter, _row_is_junk, _row_text_is_junk, _row_url_is_farm_shaped


# ============================================================================
# 来源：test_a_row_whose_headline_is_its_own_address_says_so.py
# ============================================================================
# R02-003 · 一行交回的「标题」就是它自己的地址时，行上必须说得出这件事。

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


# ============================================================================
# 来源：test_consensus_gated_fanout.py
# ============================================================================
# #8 共识感知扇出：让"够不够独立佐证"给自动取正文当预算分配器。
# 重名消歧：_row → _row__consensus_gated_fanout

def _row__consensus_gated_fanout(url: str, consensus: str, tier: str = "high") -> SearchResult:
    return SearchResult(title=f"t {url}", url=url,
                        engines_consensus=consensus, fetch_relevance=tier)


class TestTheParser:

    def test_it_reads_the_ratio(self):
        assert _parse_consensus("2 of 6") == (2, 6)

    def test_it_reads_the_no_corroboration_spelling(self):
        assert _parse_consensus("1 of 1 (no corroboration)") == (1, 1)

    def test_an_unreadable_string_is_not_read_as_zero_agreement(self):
        """解析失败返回 (0, 0)，让闸门走"不适用"——不能把一次解析失败读成
        "零个家族同意"，那会丢掉每一行。"""
        assert _parse_consensus("") == (0, 0)
        assert _parse_consensus("garbage") == (0, 0)
        assert _parse_consensus(None) == (0, 0)


class TestTheGate:

    def test_off_by_default(self):
        rows = [_row__consensus_gated_fanout("https://a/1", "1 of 6"), _row__consensus_gated_fanout("https://a/2", "3 of 7")]
        kept, skipped, note = gate_by_consensus(rows, 0)
        assert kept == rows and skipped == [] and note == ""

    def test_a_floor_of_one_is_a_noop_and_says_nothing(self):
        """地板 1 由调用方那一侧拒绝；纯函数这里按"没设"处理。"""
        rows = [_row__consensus_gated_fanout("https://a/1", "1 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 1)
        assert kept == rows and skipped == [] and note == ""

    def test_it_keeps_only_corroborated_rows(self):
        rows = [_row__consensus_gated_fanout("https://a/1", "1 of 6"), _row__consensus_gated_fanout("https://a/2", "3 of 7"),
                _row__consensus_gated_fanout("https://a/3", "2 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 2)
        assert [r.url for r in kept] == ["https://a/2", "https://a/3"]
        assert [r.url for r in skipped] == ["https://a/1"]
        assert "fetched 2" in note and "skipped 1" in note

    def test_a_single_family_pool_is_not_read_as_disagreement(self):
        """`1 of 1 (no corroboration)` 是"没人**能**同意"——池子降级或太小。把这份
        沉默读成反对，会在坏天气里丢掉每一行。"""
        rows = [_row__consensus_gated_fanout("https://a/1", "1 of 1 (no corroboration)"),
                _row__consensus_gated_fanout("https://a/2", "1 of 1 (no corroboration)")]
        kept, skipped, note = gate_by_consensus(rows, 2)
        assert kept == rows and skipped == []
        assert "not applied" in note and "single index family" in note

    def test_clearing_every_candidate_fetches_nothing_and_says_so(self):
        rows = [_row__consensus_gated_fanout("https://a/1", "1 of 6"), _row__consensus_gated_fanout("https://a/2", "1 of 6")]
        kept, skipped, note = gate_by_consensus(rows, 2)
        assert kept == []
        assert [r.url for r in skipped] == ["https://a/1", "https://a/2"]
        assert "nothing was fetched" in note

    def test_no_candidates_is_a_noop(self):
        assert gate_by_consensus([], 2) == ([], [], "")


# ─── integration through server.web_search ─────────────────────────────────

def _fake_search(consensus: list[int], engines=("brave", "bing", "baidu", "yandex", "sogou", "so360")):
    async def fake_multi_search(query, max_results, **kwargs):
        rows = [RawResult(title=f"widgets guide {i}",
                          url=f"https://a{i}.example.test/widgets-guide",
                          snippet="widgets guide", source="brave",
                          position=i + 1, consensus=c)
                for i, c in enumerate(consensus)]
        return rows, [EngineReport(name=e, ok=True) for e in engines]
    return fake_multi_search


def _force_neural(monkeypatch, scores: list[float]):
    """让 _rank 走 neural 分支并交出可控分数。

    为什么必须动这一步：merge 模式下排序分数是"位置分 + 共识加成"，而共识加成
    (0.2/家族) 远大于标题+URL 的加成上限 (0.18)——于是单家族行永远排在多家族行之后，
    闸门在 merge 模式下结构性地不可达。闸门真正会咬的场合是**神经模型判断与共识
    不一致**时（高相关但单一家族 vs 低相关但多家佐证），那正是调用方会想覆盖的
    那一刻，也正是这里要复现的。
    """
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=object()))
    monkeypatch.setattr(search_mod, "neural_rerank",
                        lambda q, ranked, **kw: [(r, s) for r, s in zip(ranked, scores)])


def _server() -> MasterFetchServer:
    server = MasterFetchServer()
    server.web_read = AsyncMock(return_value=ResponseModel(
        url="u", status=200, content=["page text"], content_ok=True))
    return server


class TestTheSearchCall:

    @pytest.mark.asyncio
    async def test_off_by_default_changes_nothing(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([3, 3, 3]))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True, cache_ttl=0)

        assert len(out.fetched_pages) == 3
        assert out.consensus_skipped == []

    @pytest.mark.asyncio
    async def test_the_floor_drops_the_uncorroborated_top_hit(self, monkeypatch):
        """排名第一但只有一家索引：开了地板就不花那一笔抓取。"""
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([1, 3, 3]))
        _force_neural(monkeypatch, [0.90, 0.20, 0.18])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True,
                                      fetch_min_consensus=2, cache_ttl=0)

        assert [p["url"] for p in out.fetched_pages] == [
            "https://a1.example.test/widgets-guide",
            "https://a2.example.test/widgets-guide"]
        assert out.consensus_skipped == ["https://a0.example.test/widgets-guide"]
        assert "fetched 2, skipped 1" in out.summary

    @pytest.mark.asyncio
    async def test_every_fetched_page_carries_its_own_consensus(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([3, 3, 3]))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True, cache_ttl=0)

        assert all(p["engines_consensus"] == "3 of 7" for p in out.fetched_pages)

    @pytest.mark.asyncio
    async def test_a_failed_fetch_still_carries_its_consensus(self, monkeypatch):
        """失败占位也带共识串——否则"这页没抓到"与"这页没被佐证"要分开查两次。"""
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([3]))
        _force_neural(monkeypatch, [0.9])
        server = MasterFetchServer()
        server.web_read = AsyncMock(side_effect=RuntimeError("boom"))

        out = await server.web_search("widgets guide", fetch_content=True, cache_ttl=0)

        assert out.fetched_pages[0]["engines_consensus"] == "3 of 7"
        assert out.fetched_pages[0]["content_ok"] is False

    @pytest.mark.asyncio
    async def test_a_single_family_pool_leaves_the_floor_unapplied(self, monkeypatch):
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([1, 1, 1], engines=("brave",)))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        # engines=["brave"] 把池子收窄到一个家族 —— 分母因此是 1，每一行都成
        # "1 of 1 (no corroboration)"，地板无处施加。
        out = await server.web_search("widgets guide", fetch_content=True,
                                      engines=["brave"],
                                      fetch_min_consensus=2, cache_ttl=0)

        assert len(out.fetched_pages) == 3          # 一页没丢
        assert out.consensus_skipped == []
        assert "not applied" in out.summary

    @pytest.mark.asyncio
    async def test_a_floor_that_clears_everything_fetches_nothing(self, monkeypatch):
        """显式点了地板就得照它执行：静默回退到"反正都抓"是替调用方做了相反的决定。"""
        monkeypatch.setattr(search_mod, "multi_search", _fake_search([1, 1, 1]))
        _force_neural(monkeypatch, [0.9, 0.8, 0.7])
        server = _server()

        out = await server.web_search("widgets guide", fetch_content=True,
                                      fetch_min_consensus=2, cache_ttl=0)

        assert out.fetched_pages == []
        assert len(out.consensus_skipped) == 3
        assert "nothing was fetched" in out.summary


class TestTheRefusals:
    """一颗显式给出的参数不能被静默忽略——两条表外用法都在发请求前拒掉。"""

    @pytest.mark.asyncio
    async def test_a_floor_of_one_is_refused(self):
        server = _server()
        out = await server.web_search("q", fetch_content=True,
                                      fetch_min_consensus=1, cache_ttl=0)
        assert out.results == []
        assert "must be 0 (off) or >= 2" in out.error
        server.web_read.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_floor_without_fetch_content_is_refused(self):
        server = _server()
        out = await server.web_search("q", fetch_min_consensus=2, cache_ttl=0)
        assert out.results == []
        assert "only applies with fetch_content=true" in out.error


class TestTheWireAndTheManual:

    def test_the_parameter_is_on_the_wire(self):
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        props = defs["web_search"]["inputSchema"]["properties"]
        assert props["fetch_min_consensus"]["type"] == "integer"
        assert "consensus_skipped" in props["fetch_min_consensus"]["description"]

    def test_the_parameter_is_accepted_by_the_dispatch_whitelist(self):
        from dhole_mcp.server import _SS_PARAMS
        assert "fetch_min_consensus" in _SS_PARAMS

    def test_a_string_floor_is_normalized(self):
        """字符串客户端写 "2" 时，`"2" >= 2` 会当场 TypeError——这颗进归一化表。"""
        from dhole_mcp.params import _ARG_TYPES
        assert _ARG_TYPES["web_search"]["fetch_min_consensus"] == "int"

    def test_an_empty_skip_list_is_omitted_from_the_wire(self):
        from dhole_mcp.search import SearchResponseModel
        from dhole_mcp.wire import _wire_dump
        clean = _wire_dump(SearchResponseModel(query="q", results=[]))
        assert "consensus_skipped" not in clean
        dirty = _wire_dump(SearchResponseModel(
            query="q", results=[], consensus_skipped=["https://a/1"]))
        assert dirty["consensus_skipped"] == ["https://a/1"]

    def test_the_manual_documents_the_floor_and_its_boundaries(self):
        from dhole_mcp import guide
        text = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        assert "fetch_min_consensus" in text
        assert "consensus_skipped" in text
        assert "not applied" in text          # 单家族池那条边界
        assert "nothing is fetched" in text   # 清空候选那条边界


# ============================================================================
# 来源：test_the_republication_leg_measures_a_share_not_a_length.py
# ============================================================================
# 同文判据的第二条腿要量的是「共享的那段占较短摘要的几成」，不是「共享了几个字」。
# 重名消歧：CORPUS → CORPUS__the_republication_leg_me_0492、_row → _row__the_republication_leg_me_0492

CORPUS__the_republication_leg_me_0492 = os.path.join("evals", "corpus", "serp")

#: 量出来的两端（同一趟测量里的人工判读；出处见本文件 docstring）
MEASURED_FALSE_CEILING = 0.3197    # 362 条假合并里最大的那一条占比
MEASURED_TRUE_FLOOR = 0.415        # 入账那一发（共享 34 字 / 较短摘要 82 字）
CORPUS_MIRROR_FLOOR = 0.795        # 冻结语料里真镜像最小的一档

#: 假合并最密的三个 query（各站自己写的释义，标题剥掉站点后缀一模一样）
FALSE_QUERIES = ("q015", "q008", "q042")
#: 真镜像的 query（同一份文件挂在不同地址上）
TRUE_QUERIES = ("q003", "q024", "q022", "q010")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _share_ratio(a: str, b: str) -> float:
    """测试也算一遍同一件事：共享的那段 / 较短那条摘要（判据的作者仍是 search 那一位）。"""
    na, nb = _norm(a), _norm(b)
    return _shared_literal_run(na, nb) / max(1, min(len(na), len(nb)))


def _serp_rows(qid: str) -> list[SearchResult]:
    """把某一题在所有冻结引擎页里的行拼起来（同一次测量走的就是这条路）。"""
    from dhole_mcp.search_metasearch import _TEXT_ENGINES

    rows: list[SearchResult] = []
    pos = 0                      # 全局行号：合并成一轮结果时 position 本来就是唯一的
    for path in sorted(glob.glob(f"{CORPUS__the_republication_leg_me_0492}/{qid}__*.html")):
        engine = os.path.basename(path).split("__")[1].split(".")[0]
        if engine not in _TEXT_ENGINES:
            continue
        eng = _TEXT_ENGINES[engine](proxy=None, timeout=10, verify=True)
        html = open(path, encoding="utf-8", errors="replace").read()
        for t in eng.extract_results(html) or []:
            pos += 1
            rows.append(SearchResult(title=t.title or "", url=t.href or "",
                                     snippet=t.body or "", position=pos,
                                     relevance_score=0.5, fetch_relevance="medium"))
    assert rows, f"{qid} 的冻结页解析不出行 —— 语料坏了，先修量具"
    return rows


def _domains_of(group: dict, rows: list[SearchResult]) -> set[str]:
    out = set()
    for pos in group["positions"]:
        row = next(r for r in rows if r.position == pos)
        d = _registrable_domain(row.url or "")
        if d:
            out.add(d)
    return out


def _row__the_republication_leg_me_0492(pos: int, title: str, url: str, snippet: str) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, position=pos,
                        relevance_score=0.9, fetch_relevance="high")


#: 入账现场（`sessions/P02-01-s1b-refined-search.json`）那 6 行的逐字回放挂具：
#: 同一份补充公告挂在四个地址，另外两行是一条无关的通知与一份公示名单。
BOOKED_ANNOUNCE = "河南省2025年家电产品以旧换新品牌厂家征集的补充公告"
BOOKED_COMMON = ("具体可参照《河南省2025年家电及3C产品以旧换新品牌厂家征集公告》要求,"
                 "进行厂家报名。咨询电话 0371")
BOOKED_ROWS = [
    _row__the_republication_leg_me_0492(1, BOOKED_ANNOUNCE + "- 开封本地宝", "http://kf.bendibao.com/news/20495.shtm",
         BOOKED_COMMON + "-65897，申报材料需加盖公章，逾期不予受理，工作日九时至十七时受理。"),
    _row__the_republication_leg_me_0492(2, "2025年第五批家电以旧换新企业公示名单", "http://swj.zhuzhou.gov.cn/c16568.html",
         "2025年第五批家电以旧换新企业公示名单，共 132 家"),
    _row__the_republication_leg_me_0492(3, "商务部等4部门办公厅关于做好2025年家电以旧换新工作的通知",
         "https://www.mofcom.gov.cn/index.html",
         "为推动家电以旧换新工作有序开展，现就有关事项通知如下，请各地结合实际认真组织实施。"),
    _row__the_republication_leg_me_0492(4, "河南省商务厅-" + BOOKED_ANNOUNCE,
         "https://hnsswt.henan.gov.cn/2025/01-06/3108794.html",
         "各省辖市商务局，郑州航空港实验教学区管委会，各有关单位：按要求进行厂家报名。"),
    _row__the_republication_leg_me_0492(5, BOOKED_ANNOUNCE, "https://www.chinamae.com/a/3108794.html",
         BOOKED_COMMON + "-65897。"),
    _row__the_republication_leg_me_0492(6, "2025年河南家电产品以旧换新品牌厂家征集补充公告- 郑州本地宝",
         "http://zz.bendibao.com/news/20496.shtm",
         "让美少女展现出笑容的温馨故事，具体见附件，报名材料需加盖公章。"),
]


# ─── 1 · 尺的形状：量的是几成，不是几个字 ─────────────────────

def test_the_leg_is_a_share_and_the_line_sits_in_a_measured_band():
    ratio = search._DOC_SNIPPET_RUN_MIN_RATIO
    assert MEASURED_FALSE_CEILING < ratio < MEASURED_TRUE_FLOOR, (
        f"闸门 {ratio} 不在量出来的空带 ({MEASURED_FALSE_CEILING}, {MEASURED_TRUE_FLOOR}) 里："
        "低了就放行实测那 362 条假合并，高了就切掉入账那一发")
    assert _DOC_SNIPPET_RUN_MIN_CHARS == 20, "绝对地板是另一件事，本刀不动它"


def test_the_same_absolute_run_reads_two_ways_at_two_lengths():
    """同样共享 40 来个字：在 82 字的摘要里是近半份拷贝（成组），
    在 220 字的摘要里只占两成（各站自己写的释义，实测假合并正长这样）。"""
    common = ("retrieval augmented generation is an ai framework that connects a "
              "knowledge source to a large language model")
    tail_a = (" so answers are grounded in retrieved passages, and the vendor documents its "
              "own chunking choice, its embedding model and a small evaluation harness here.")
    tail_b = (" and this glossary entry walks through embeddings, chunk strategy, an offline "
              "index refresh, two metrics this team tracks for answer quality today, and the "
              "cache layer that sits in front of the whole pipeline for repeat lookups.")
    pair_long = [
        _row__the_republication_leg_me_0492(1, "What is retrieval-augmented generation (RAG)? - IBM Research",
             "https://research.ibm.com/http/a",
             common + tail_a + " It is used across enterprise search deployments today."),
        _row__the_republication_leg_me_0492(2, "What is Retrieval-Augmented Generation (RAG)? | NVIDIA Glossary",
             "https://www.nvidia.com/http/b",
             common + tail_b + " Definitions vary by vendor and by the index behind each."),
    ]
    long_ratio = _share_ratio(pair_long[0].snippet, pair_long[1].snippet)
    # 这一对的字数早就过了绝对地板（今天就是靠那一半放行 173 条这种配对的），
    # 但占比在闸门之下 —— 实测假合并那一堆的最大占比是 0.3197，这一发比它更高，
    # 所以钉的是"低于本刀的线"，不是"等于实测上沿"。
    assert _DOC_SNIPPET_RUN_MIN_CHARS < _shared_literal_run(
        _norm(pair_long[0].snippet), _norm(pair_long[1].snippet))
    assert long_ratio < search._DOC_SNIPPET_RUN_MIN_RATIO, long_ratio
    assert _same_document_groups(pair_long) == [], (
        "两条各站自己写的释义被并成『同一份文件挂在两个地址』")
    pair_short = [
        _row__the_republication_leg_me_0492(1, pair_long[0].title, pair_long[0].url, common + tail_a),
        _row__the_republication_leg_me_0492(2, pair_long[1].title, pair_long[1].url, common + tail_b),
    ]
    short_ratio = _share_ratio(pair_short[0].snippet, pair_short[1].snippet)
    assert short_ratio > search._DOC_SNIPPET_RUN_MIN_RATIO, short_ratio
    assert len(_same_document_groups(pair_short)) == 1, (
        "同样这几个字在短摘要里就是近乎整份拷贝，这一对必须成组")


def test_the_absolute_floor_still_guards_tiny_snippets():
    """两段各 45 字、共享 18 个字：占比过线了，字数没过地板 —— 那还是偶然撞上的两句广告词。"""
    shared = "本周新增产能两成，库存下降"
    a = _row__the_republication_leg_me_0492(1, "某行业周报第 12 期：产能与库存", "https://a.example.org/w12",
             shared + "另外三家样本企业的开工率同步走高。")
    b = _row__the_republication_leg_me_0492(2, "某行业周报第 12 期：产能与库存摘要", "https://b.example.net/s12",
             shared + "本期同时收录进出口与物流两项修正数据。")
    assert _DOC_SNIPPET_RUN_MIN_CHARS > len(shared)
    assert _same_document_groups([a, b]) == [], "偶然的短句重合被当成了同一份文件"


def test_the_booked_announcement_still_groups():
    """这条腿当初就是为这一发立的：新尺之下开封本地宝与 chinamae 那两行仍要成组。"""
    assert _share_ratio(BOOKED_ROWS[0].snippet, BOOKED_ROWS[4].snippet) > MEASURED_FALSE_CEILING
    groups = _same_document_groups(BOOKED_ROWS)
    assert groups, "入账那一发被新尺切掉了：这条腿的存在理由就是它"
    positions = sorted(groups[0]["positions"])
    assert 1 in positions and 5 in positions, groups
    assert groups[0]["hosts"] >= 2, groups
    assert BOOKED_ANNOUNCE in groups[0]["title"], groups
    for g in groups:
        assert len(g["positions"]) < len(BOOKED_ROWS), f"无关行被吸进了组：{g}"


# ─── 2 · 真语料两端：四题仍成组、三题一个组都不出 ─────────────

@pytest.mark.parametrize("qid", TRUE_QUERIES)
def test_the_measured_republications_still_group(qid):
    rows = _serp_rows(qid)
    groups = _same_document_groups(rows)
    assert groups, f"{qid}: 量读为真镜像的那一发今天不成组了"
    for g in groups:
        assert len(_domains_of(g, rows)) >= 2, g


@pytest.mark.parametrize("qid", FALSE_QUERIES)
def test_the_measured_topic_siblings_do_not_group(qid):
    rows = _serp_rows(qid)
    groups = _same_document_groups(rows)
    assert not groups, f"{qid}: 各站自己写的释义被并成同一份文件 —— {groups}"


# ─── 3 · 一把尺不许有两个作者 ─────────────────────────────────

def test_the_two_numbers_on_the_wire_are_not_hand_written():
    """字段说明里那两颗数由常数拼出来：手写的数字就是下一个会说谎的说明。"""
    from dhole_mcp.search import SearchResponseModel

    desc = SearchResponseModel.model_fields["same_document_groups"].description
    assert f"{_DOC_SNIPPET_RUN_MIN_CHARS} characters" in desc, desc[:400]
    assert f"{search._DOC_SNIPPET_RUN_MIN_RATIO:.0%}" in desc, desc[:400]


def test_the_manual_carries_the_same_ruler():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "of the shorter snippet" in corpus, (
        "手册还在说『共享几个字』：读者学不到这把尺量的是占比")


# ============================================================================
# 来源：test_a_search_row_that_looks_like_a_farm_is_dropped_below_the_line.py
# ============================================================================
# 验收刀9-3（F1）：搜「最近很火的 AI 产品」不该端上一屏黄暴 SEO 农场。
# 重名消歧：_row → _row__a_search_row_that_looks__f41a

FARM_ONE = "http://d3gbed2ley04jq.cloudfront.net/player.html"
FARM_TWO = "https://1za7kjca.aass341.sbs/v/9f3.html"
# 实测里"字母+数字"混排却**是**人名/品牌名的三家（799 条语料里唯一带混排标签的三行）。
BRANDS = ["https://regex101.com/page", "https://eathealthy365.com/a",
          "https://www.heyuan110.com/a", "https://www.360doc.com/",
          "https://m.kuaibo88.com/dp/a.html"]


def _row__a_search_row_that_looks__f41a(url, *, raw=None, title="Normal title", snippet=""):
    return SearchResult(title=title, url=url, snippet=snippet, relevance_raw=raw)


# ─────────────── 形状：两条判据各自抓谁、都不抓谁 ───────────────

def test_each_shape_rule_catches_the_host_it_was_measured_on():
    """报告里那两例分别落在两条不同的形状判据上（各自 0/799 命中真结果行）。"""
    assert _row_url_is_farm_shaped(FARM_ONE).startswith("leftmost host label")
    assert "2 letter+digit labels" in _row_url_is_farm_shaped(FARM_TWO)


@pytest.mark.parametrize("url", BRANDS)
def test_a_host_that_is_a_word_with_digits_at_the_end_is_not_a_shape(url):
    """`regex101` / `heyuan110` 是"一个词 + 尾巴缀数字"，那是人起的名字。

    数字夹在中间才是机器摇的那一形。这一条不是假设：这三家就是 799 条真实结果行里
    唯一带字母+数字混排标签的主机，而阈值把它们全留在门外（量到 0 命中才敢定这条线）。
    """
    assert _row_url_is_farm_shaped(url) == "", url


# ─────────────── 词形：只认硬档，正版视频页不在这一族 ───────────────

@pytest.mark.parametrize("title,snippet", [
    ("某剧 全集 在线观看", "免费观看高清完整版"),
    ("某剧 免费播放 在线观看", "高清"),
])
def test_a_legit_video_platform_title_is_not_spam_wording(title, snippet):
    """这一档被**撤掉**了，不是没想到：799 条真实结果行里"在线观看/免费观看"命中 11 条，
    两颗同现仍有 3 条，全是 `tv.sohu.com` / `m.ixigua.com` 自己的标题。

    一个会天天误伤答案的形状留着，教出来的是读者跳过整个字段（`injection.py` 给那五类
    形状定阈值时写的是同一条理由）。盗播那一族因此只由主机形状管。
    """
    assert _row_text_is_junk(_row__a_search_row_that_looks__f41a("https://tv.sohu.com/v/abc.html",
                                  title=title, snippet=snippet)) == ""


def test_a_hard_wording_alone_convicts():
    for title in ("棋牌注册送体验金", "真人视讯官方平台", "六合彩开奖结果投注计划",
                  "无删减版资源"):
        basis = _row_text_is_junk(_row__a_search_row_that_looks__f41a("https://a.example.com/x", title=title))
        assert basis, title


# ─────────────── eligibility：只丢模型已经认为弱的那一行 ───────────────

def test_a_row_the_model_called_relevant_is_never_dropped():
    """农场形状 + raw=0.9 也留着。宁可漏滤，不误杀一条真相关。"""
    assert _row_is_junk(_row__a_search_row_that_looks__f41a(FARM_ONE, raw=0.9)) == ""


def test_a_weak_row_with_only_a_shape_is_dropped_and_says_which_shape():
    basis = _row_is_junk(_row__a_search_row_that_looks__f41a(FARM_TWO, raw=0.0001))
    assert "letter+digit labels" in basis, basis
    assert "relevance_raw=0.0001" in basis, basis


def test_with_no_verdict_a_shape_alone_drops_nothing():
    """`relevance_raw` 是 null 表示这一轮没有模型判断过 —— 缺席不是 0 分。

    那一轮里丢弃必须两份证据同现（形状 + 词形），否则"没人量过"会被读成"量出来是 0"。
    """
    assert _row_is_junk(_row__a_search_row_that_looks__f41a(FARM_ONE, raw=None)) == ""
    both = _row__a_search_row_that_looks__f41a(FARM_ONE, raw=None, title="无删减版 真人视讯入口")
    assert "no reranker verdict" in _row_is_junk(both), _row_is_junk(both)


def test_the_filter_drops_the_farm_rows_and_keeps_the_rest():
    rows = [_row__a_search_row_that_looks__f41a(FARM_ONE, raw=1e-4, title="AI video tool"),
            _row__a_search_row_that_looks__f41a(FARM_TWO, raw=2e-4, title="hot AI product"),
            _row__a_search_row_that_looks__f41a("https://goenhance.com/blog/hotel-lobby", raw=0.97,
                 title="Hotel Lobby explained"),
            _row__a_search_row_that_looks__f41a("https://tv.sohu.com/v/abc.html", raw=0.3, title="某剧 在线观看")]
    kept = [r.url for r in _reputation_filter(rows)]
    assert kept == ["https://goenhance.com/blog/hotel-lobby",
                    "https://tv.sohu.com/v/abc.html"], kept


def test_the_knob_switches_the_whole_family_off(monkeypatch):
    monkeypatch.setenv("DHOLE_SEARCH_ROW_FILTER", "off")
    rows = [_row__a_search_row_that_looks__f41a(FARM_ONE, raw=1e-4), _row__a_search_row_that_looks__f41a(FARM_TWO, raw=1e-4)]
    assert _reputation_filter(rows) == rows
    monkeypatch.delenv("DHOLE_SEARCH_ROW_FILTER")
    assert _reputation_filter(rows) == [], "默认必须是开着的"


# ─────────────── 账：丢了几个、为什么，必须进那一份逐因清单 ───────────────

def test_the_drop_lands_in_the_per_cause_ledger_next_to_the_other_three():
    reasons = _drop_causes(rank_dropped=0, low_dropped=0, off_topic=0,
                           farm_dropped=2, floors=[], verdict_ran=True)
    assert reasons.startswith("2 dropped as SEO-farm rows"), reasons
    assert "DHOLE_SEARCH_ROW_FILTER=off" in reasons, reasons
    # 四因并列时顺序与总数不许互相吞：合并成一句话就谁也不指向哪个旋钮。
    all_four = _drop_causes(rank_dropped=1, low_dropped=1, off_topic=1,
                            farm_dropped=1, floors=["min_relevance=0.2"],
                            verdict_ran=True)
    assert all(s in all_four for s in ("1 below min_relevance=0.2", "1 labelled low",
                                        "1 matched no query term",
                                        "1 dropped as SEO-farm rows")), all_four


# ─────────────── 走一次真流水线：账要说出口，缓存里不许留垃圾 ───────────────

def _patch(monkeypatch, rows, *, raws=None):
    """引擎与缓存两处桩；`_rank` 不桩，让 raw 沿着 `neural_raw` 走完整条链。"""
    rows = list(rows)
    for r, v in zip(rows, raws or []):
        r.neural_raw = v

    async def fake_multi(query, max_results, **kwargs):
        return rows, [EngineReport(name="bing", ok=True, status="ok")]

    async def no_cache(*a, **k):
        return None

    stored: dict = {}

    async def capture(url, extraction_type, content, *a, **k):
        stored["payload"] = content

    async def pass_through(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi)
    monkeypatch.setattr(S, "get_cached", no_cache)
    monkeypatch.setattr(S, "set_cached", capture)
    monkeypatch.setattr(S, "ensure_reranker", pass_through)
    return S, stored


def _rr(title, url, snippet=""):
    return RawResult(title=title, url=url, snippet=snippet, source="bing",
                     position=1, consensus=1, sources=("bing",))


@pytest.mark.asyncio
async def test_the_live_round_drops_them_and_says_how_many(monkeypatch):
    """报告里那一次的原形状：一条中文查询，两行农场 + 一行真答案。

    `_quality_filter` 在 3 行以下不动手（不许把一轮清到少于 min_keep），而
    `_filter_irrelevant_results` 对中文是 no-op（`_query_terms` 切不出 >=3 字符的拉丁
    词）—— 所以这一屏垃圾在这一刀之前**两道门都不拦**，走到这里才第一次有人管。
    """
    srv, _stored = _patch(
        monkeypatch,
        [_rr("AI视频 生成工具 爆火", FARM_ONE),
         _rr("热门AI产品 在线观看", FARM_TWO),
         _rr("Hotel Lobby explained", "https://goenhance.com/blog/hotel-lobby")],
        raws=[1e-4, 2e-4, 0.97])
    out = await srv.web_search(None, "最近很火的 AI 产品", 5, cache_ttl=0)
    assert [r.url for r in out.results] == ["https://goenhance.com/blog/hotel-lobby"], \
        out.results
    assert "2 dropped as SEO-farm rows" in (out.fetch_hint or ""), out.fetch_hint


@pytest.mark.asyncio
async def test_the_cache_holds_the_filtered_rows_not_the_farm(monkeypatch):
    """过滤必须在 `set_cached` 之前：否则第二次问同一句，垃圾从缓存里复活。"""
    srv, stored = _patch(
        monkeypatch,
        [_rr("AI视频 生成工具", FARM_ONE),
         _rr("Hotel Lobby explained", "https://goenhance.com/blog/hotel-lobby")],
        raws=[1e-4, 0.97])
    await srv.web_search(None, "最近很火的 AI 产品", 5, cache_ttl=60)
    assert FARM_ONE not in str(stored.get("payload")), stored.get("payload")


@pytest.mark.asyncio
async def test_the_knob_off_leaves_the_farm_in_the_round(monkeypatch):
    monkeypatch.setenv("DHOLE_SEARCH_ROW_FILTER", "off")
    srv, _stored = _patch(
        monkeypatch, [_rr("AI视频 生成工具", FARM_ONE),
                      _rr("Hotel Lobby explained",
                          "https://goenhance.com/blog/hotel-lobby")],
        raws=[1e-4, 0.97])
    out = await srv.web_search(None, "最近很火的 AI 产品", 5, cache_ttl=0)
    assert FARM_ONE in [r.url for r in out.results], out.results
    assert "SEO-farm" not in (out.fetch_hint or ""), out.fetch_hint
