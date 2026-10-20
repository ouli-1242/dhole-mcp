"""抓取排序也要听得见中文 focus：`crawl._query_terms` 的第二位作者缺一条腿。

台账 R02-050 —— R02-F26 那一次 grep 分词器作者撞到的同一维缺陷。`search._query_terms`
当时拿到了「拉丁词形 + CJK 相邻二元组」两条腿，`crawl._query_terms`（src/dhole_mcp/crawl.py:871，本刀之后是 877）
没有：它只要 `[A-Za-z0-9]+` 且长度 ≥2，中文交出空集，而 `score_link` 在 terms 为空时把
`focus_score` 留在 0.0。

现场量（`review/.runtime/p4/f51_measure.py`，离线纯函数）：

    中文「征集公告 申报条件」：crawl 交 0 词 / search 交 6 词
    score_link(锚文本正好就是这句中文) = 0.0000
    score_link(毫不相干的锚文本)        = 0.0000   ← 与**不带 focus** 一模一样
    四条候选按 -score 排序：带中文 focus 与不带 focus 的顺序完全相同
    英文同一对：1.3333 / 0.0000                    ← 腿是活的，只认拉丁

所以本文件的判据有两组，缺一不可：
① 正面 —— 中文/日文/混排的 focus 必须打得出分数、必须改得动排序（"装饰参数"这一形状
   一旦被修好，最该钉的就是"排序真的变了"）；
② 反面 —— 拉丁那一族的**现有分数一寸不许动**（`score_link` 是抓取顺序的全部依据）。

修法落在“作者”这一维，不是“给 crawl 再写一条腿”：切词这件事在仓库里已有判据
（`focus._tokens`：Unicode 词段 + 无空格文字的相邻二元组，那位作者的 docstring 里记的
是同一条“中文查询交出空集”的来路），crawl 读它，不再当第二位作者。crawl 的拉丁门槛
（≥2、数字算词）与 `focus._tokens` 的 ASCII 段本来一致，所以这一族由上面第②组原地钉住；
`search._query_terms` 的 ≥3 + 停用词是**另一位读者的另一维**（SERP 相关性判定），本刀不并
（那维另立，台账 R02-051）。附带继承来的一处行为差（带重音的词不再被咬掉一头）写在
`test_an_accented_word_stays_one_word`，不留悄悄走的变化。
"""
import pytest

from dhole_mcp import crawl

ZH = "征集公告 申报条件"
JA = "申請条件 公募"
EN = "funding announcement eligibility"


class TestTheCrawlTokenizerGetsTheLeg:

    def test_a_chinese_focus_yields_terms_not_the_empty_set(self):
        terms = crawl._query_terms(ZH)
        assert terms, "中文 focus 交出空集 ⇒ score_link 的 focus_score 永远 0"
        for want in ("征集", "公告", "申报", "条件"):
            assert want in terms, terms

    def test_a_japanese_focus_too(self):
        terms = crawl._query_terms(JA)
        assert {"申請", "条件", "公募"} <= terms, terms

    def test_a_one_character_run_gives_itself(self):
        assert crawl._query_terms("嗯") == {"嗯"}

    def test_a_mixed_focus_keeps_both_legs(self):
        terms = crawl._query_terms("储能 battery")
        assert "储能" in terms, terms
        assert "battery" in terms, terms

    def test_bigrams_come_from_contiguous_runs_only(self):
        """连段被空格切开就不缝：「河南省 家电」交不出「省家」这种页面里没有的词。"""
        terms = crawl._query_terms("河南省 家电")
        assert {"河南", "南省", "家电"} <= terms, terms
        assert "省家" not in terms, terms

    def test_an_accented_word_stays_one_word(self):
        """改读 `focus._tokens` 顺带带来的第二处行为，写下来而不是让它悄悄走。

        旧那一只 `[A-Za-z0-9]+` 把 "écoles" 切成 "coles"（é 不在字符类里，词被咬掉一头）；
        现在交回整个词形。代价是带重音的焦点不再匹配无重音写法（"école" 匹配不到 "ecole"），
        那位作者早已在 BM25 那侧认了这个取舍（不做折形、不带词典），crawl 用同一套词面
        就该同一条取舍，而不是各留一份。
        """
        terms = crawl._query_terms("écoles primaires")
        assert "écoles" in terms, terms
        assert "coles" not in terms, terms

    def test_the_cached_term_set_is_not_the_callers_to_edit(self):
        """缓存交回的是同一只集合：可变 ⇒ 这一次抓取改写下一次抓取的词表。"""
        terms = crawl._query_terms(ZH)
        assert isinstance(terms, frozenset), type(terms)


class TestTheSortingActuallyListens:

    def test_a_matching_anchor_outranks_an_unrelated_one(self):
        hit = crawl.score_link("https://ex.org/a/notice", "征集公告 申报条件 已发布", ZH)
        miss = crawl.score_link("https://ex.org/b/other", "完全无关的锚文本", ZH)
        assert hit > miss, f"中文 focus 在两条链接上同分（{hit} / {miss}）= 没表态"

    def test_no_focus_and_a_chinese_focus_cannot_be_the_same_score(self):
        url, text = "https://ex.org/a/notice", "征集公告 申报条件 已发布"
        assert crawl.score_link(url, text, ZH) > crawl.score_link(url, text, "")

    def test_the_order_of_a_candidate_list_actually_changes(self):
        """这条才是本题的要害：`focus=` 不许是一个装饰参数。"""
        cands = [
            ("https://ex.org/z/other-page", "不相干的一页"),
            ("https://ex.org/b/guideline", "申报条件 的说明页"),
            ("https://ex.org/m/random", "随机内容"),
            ("https://ex.org/a/notice", "征集公告 申报条件 已发布"),
        ]
        with_focus = sorted(cands, key=lambda c: -crawl.score_link(c[0], c[1], ZH))
        without = sorted(cands, key=lambda c: -crawl.score_link(c[0], c[1], ""))
        assert [c[0] for c in with_focus] != [c[0] for c in without], (
            "带 focus 与不带 focus 排出来的顺序一寸没变")
        assert with_focus[0][0].endswith("/notice"), with_focus

    def test_a_chinese_path_counts_too(self):
        got = crawl.score_link("https://ex.org/征集公告", "", ZH)
        assert got > 0, "URL 路径里的中文也算 path_hit，不该整条腿都瞎"


class TestTheLatinLegIsNotTouched:
    """反面锁：拉丁那一族的分数与门槛都是既有行为，本刀一寸不动。"""

    def test_the_english_pair_keeps_its_measured_scores(self):
        hit = crawl.score_link("https://ex.org/a/notice", "funding announcement published", EN)
        miss = crawl.score_link("https://ex.org/b/other", "totally unrelated anchor", EN)
        assert hit == pytest.approx(4 / 3, abs=1e-4), hit
        assert miss == 0.0, miss

    def test_two_letter_tokens_still_count_here(self):
        """crawl 的拉丁腿是 ≥2（search 那条是 ≥3 + 停用词）。补 CJK 不许顺手把两维并成一只。"""
        terms = crawl._query_terms("ai 3d model")
        assert {"ai", "3d", "model"} <= terms, terms
