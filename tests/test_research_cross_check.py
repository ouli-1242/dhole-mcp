"""`web_research(cross_check=true)`：来源一致性报告。

它和 `engines_consensus` 回答的不是同一个问题：那边数「几个索引收录了同一个
URL」，这边数「几个独立可注册域名对同一句话说了相近的话」。判据分四组：

1. **确认与未证**：两个独立域名说相近的话 = confirmed；一个域名永远 unverified。
2. **冲突**：yes/no 问句的否定、反义、同单位数字不一致 → contested，并给出
   perspectives。陈述句与名词词组里的否定词是词义的一部分，不再判成反对。
   冲突既可能来自「句子 vs claim」，也可能来自「来源 vs 来源」——后者是
   claim 自己没带数字时唯一能发现分歧的路。
3. **中文**：字符 n-gram 让中文 query 能切出 claim、能归因，不依赖分词器。
4. **诚实边界**：这是来源一致性信号，不是事实核查；失败只写
   cross_check_error，证据包照常返回；`cross_check=false` 时响应一字不变。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from dhole_mcp.corroborate import (
    cross_check,
    _is_open_question,
    _is_yes_no_question,
)
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer

import dhole_mcp.corroborate as corroborate_mod

pytestmark = pytest.mark.smoke


_TOWER = [
    ("https://example.com/eiffel",
     "The Eiffel Tower is 330 metres tall. It was completed in 1889 for the "
     "World's Fair in Paris."),
    ("https://example.org/tower",
     "According to official figures, the Eiffel Tower is 330 metres tall "
     "including its antennas. It is the tallest structure in Paris."),
]


def _wire(monkeypatch, texts, rows=None):
    async def fake_search(self, query="", **kw):
        return SimpleNamespace(
            results=[SimpleNamespace(url=url, title=url)
                     for url, _text in texts],
            error="", next_action="")

    async def fake_read(self, url, focus=None, max_content_chars=4000,
                        timeout=None, **kw):
        text = next(t for u, t in texts if u == url)
        return ResponseModel(url=url, status=200, content=[text],
                             fetcher_used="http", content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "web_search", fake_search)
    monkeypatch.setattr(MasterFetchServer, "web_read", fake_read)


# ─── 1 · verdicts ─────────────────────────────────────────────────────────

class TestTheVerdicts:

    def test_two_independent_domains_confirm(self):
        out = cross_check("How tall is the Eiffel Tower?", _TOWER)
        (claim,) = out["claims"]
        assert claim["verdict"] == "confirmed"
        assert claim["agreement_score"] >= 0.7
        assert len(claim["support"]) == 2
        assert claim["oppose"] == []

    def test_one_domain_is_never_confirmed(self):
        out = cross_check("How tall is the Eiffel Tower?", _TOWER[:1])
        (claim,) = out["claims"]
        assert claim["verdict"] == "unverified"
        assert claim["agreement_score"] <= 0.3

    def test_subdomains_of_one_site_count_once(self):
        blocks = [
            ("https://docs.example.com/a",
             "The Eiffel Tower is 330 metres tall including its antennas."),
            ("https://news.example.com/b",
             "The Eiffel Tower is 330 metres tall including its antennas."),
        ]
        out = cross_check("How tall is the Eiffel Tower?", blocks)
        (claim,) = out["claims"]
        assert claim["verdict"] == "unverified"
        assert out["cross_check_basis"]["domains"] == 1

    def test_opposite_verbs_are_contested_with_perspectives(self):
        out = cross_check("Did iPhone sales increase in 2025?", [
            ("https://a.test/1", "iPhone sales increase by 12 percent in 2025 "
                                 "according to the report."),
            ("https://b.test/2", "New data shows iPhone sales decrease by 8 "
                                 "percent in 2025 compared with last year."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "contested"
        assert claim["perspectives"], "冲突必须给出对立视角"
        assert claim["oppose"]

    def test_two_sources_disagreeing_on_a_number_are_contested(self):
        """claim 自己没带数字时，分歧只存在于来源之间。"""
        out = cross_check("What is the market share of the browser?", [
            ("https://a.test/1", "The browser now holds 45% of the desktop "
                                 "market worldwide in 2025."),
            ("https://b.test/2", "The same browser holds 50% of the desktop "
                                 "market worldwide in 2025."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "contested"
        assert len(claim["perspectives"]) == 2

    def test_the_same_number_in_two_spellings_is_not_a_conflict(self):
        out = cross_check("Is the Eiffel Tower 330 metres tall?", [
            ("https://a.test/1", "The Eiffel Tower is 330 metres tall "
                                 "including its antennas."),
            ("https://b.test/2", "The Eiffel Tower is 330 m tall including "
                                 "its antennas and broadcast towers."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "confirmed"

    def test_a_number_that_contradicts_the_claim_is_contested(self):
        out = cross_check("Is the Eiffel Tower 330 metres tall?", [
            ("https://a.test/1", "The Eiffel Tower is 330 metres tall "
                                 "including its antennas."),
            ("https://b.test/2", "The Eiffel Tower is 300 metres tall "
                                 "according to the older survey."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "contested"
        assert claim["oppose"] == ["https://b.test/2"]


# ─── 1b · open questions have no polarity ────────────────────────────────

class TestOpenQuestions:
    """问句不是一道可以被"不"否定的命题。

    2026-10-28 真机 h04：「异步编程是什么？」两个来源都在解释异步，却判
    contested / agreement_score=0.0 —— 否定奇偶把「不可或缺」「从来没」读成了
    两次立场翻转。开放式问句没有极性；只有 yes/no 问句保留否定极性，陈述句与
    名词词组里的否定词不再判冲突（见 TestNegationNeedsAYesNoQuestion）。
    """

    def test_the_shapes_that_have_no_polarity(self):
        assert _is_open_question("What is asyncio?")
        assert _is_open_question("How tall is the Eiffel Tower?")
        assert _is_open_question("异步编程是什么？")
        assert _is_open_question("这款手机的起售价是多少？")
        assert not _is_open_question("Did iPhone sales increase in 2025?")
        assert not _is_open_question("Does coffee cause dehydration?")
        assert not _is_open_question("The Eiffel Tower is 330 metres tall.")

    def test_a_chinese_open_question_is_not_polarised_by_negation_words(self):
        out = cross_check("异步编程是什么？", [
            ("https://docs.pingcode.com/ask/155131.html",
             "异步编程是Web开发中不可或缺的一部分，它通过提高性能、优化用户体验"
             "和增加应用的灵活性，成为了现代Web开发的一个重要标准。"),
            ("https://rustcc.cn/article?id=x",
             "以前从来没接触过异步编程，包括在 Python 或者 JavaScript 上也一样，"
             "新手求问"),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] != "contested", claim
        assert claim["oppose"] == []
        warnings = " ".join(out["cross_check_basis"]["warnings"])
        assert "open question" in warnings

    def test_an_english_open_question_is_not_polarised_by_not(self):
        out = cross_check("What is asyncio?", [
            ("https://a.test/1", "asyncio is a library for concurrent code, "
                                 "scheduling coroutines on an event loop."),
            ("https://b.test/2", "asyncio is not a threading replacement; it "
                                 "schedules coroutines on one event loop."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] != "contested", claim
        assert claim["oppose"] == []

    def test_a_yes_no_question_keeps_its_polarity(self):
        out = cross_check("Does coffee cause dehydration?", [
            ("https://a.test/1", "Coffee does cause dehydration because "
                                 "caffeine is a diuretic."),
            ("https://b.test/2", "Coffee does not cause dehydration in "
                                 "habitual drinkers."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "contested", claim
        assert claim["oppose"]


# ─── 1c · negation needs a yes/no question ────────────────────────────────

class TestNegationNeedsAYesNoQuestion:
    """陈述句 / 名词词组里的"不"是词义的一部分，不表示对 query 的反对。

    2026-10-28 真查询归因测量：10 条真实查询切出的复合问句上，negation 型
    oppose 21 条里 21 条是这类误判（"without memory leaks"、"no traffic"、
    "也不例外"）。否定极性从此只服务 yes/no 问句。
    """

    def test_the_shapes_that_are_yes_no_questions(self):
        assert _is_yes_no_question("Does coffee cause dehydration?")
        assert _is_yes_no_question("Is the Eiffel Tower 330 metres tall?")
        assert _is_yes_no_question("咖啡会导致脱水吗？")
        assert _is_yes_no_question("是否支持离线安装？")
        assert not _is_yes_no_question("mysql query not using index causes")
        assert not _is_yes_no_question("The Eiffel Tower is 330 metres tall.")
        assert not _is_yes_no_question("What is asyncio?")
        assert not _is_yes_no_question("异步编程是什么？")

    def test_a_noun_phrase_query_keeps_its_support_when_sources_lack_not(self):
        out = cross_check("mysql query not using index causes", [
            ("https://a.test/1", "A missing index forces MySQL to scan every "
                                 "row in the table, which is why the query is slow."),
            ("https://b.test/2", "When no usable index exists, MySQL scans the "
                                 "whole table instead of seeking rows."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "confirmed", claim
        assert claim["oppose"] == []
        warnings = " ".join(out["cross_check_basis"]["warnings"])
        assert "not a yes/no question" in warnings

    def test_a_declarative_claim_is_not_opposed_by_a_not_in_the_source(self):
        out = cross_check("The Eiffel Tower is 330 metres tall.", [
            ("https://a.test/1", "The Eiffel Tower is 330 metres tall "
                                 "including its antennas."),
            ("https://b.test/2", "The Eiffel Tower is not short at all and "
                                 "stands 330 metres tall."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "confirmed", claim
        assert claim["oppose"] == []

    def test_a_yes_no_question_still_uses_negation(self):
        out = cross_check("Is coffee dehydrating?", [
            ("https://a.test/1", "Coffee is dehydrating because caffeine is a "
                                 "diuretic."),
            ("https://b.test/2", "Coffee is not dehydrating in habitual "
                                 "drinkers."),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "contested", claim


# ─── 2 · Chinese ──────────────────────────────────────────────────────────

class TestTheChinesePath:

    def test_a_chinese_query_yields_a_claim_and_a_verdict(self):
        out = cross_check("iPhone 18 的起售价是多少？", [
            ("https://a.test/1", "苹果公司宣布 iPhone 18 的起售价为 799 美元，"
                                 "将在九月正式发售。这款手机采用了全新的芯片设计。"),
            ("https://b.test/2", "根据发布会信息，iPhone 18 的起售价是 799 美元，"
                                 "与前代产品保持相同的价格区间。"),
        ])
        (claim,) = out["claims"]
        assert claim["claim"].strip()
        assert claim["verdict"] == "confirmed"
        assert len(claim["support"]) == 2

    def test_a_chinese_number_conflict_is_contested(self):
        out = cross_check("这款手机的起售价是多少？", [
            ("https://a.test/1", "这款手机的起售价为 799 美元，将在九月正式发售。"),
            ("https://b.test/2", "这款手机的起售价为 899 美元，将在十月正式发售。"),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "contested"

    def test_chinese_matching_does_not_need_a_word_segmenter(self):
        """2-gram 保底：两个只共享部分词组的句子仍能归到同一 claim。"""
        out = cross_check("注意力机制的优势是什么", [
            ("https://a.test/1", "注意力机制的优势在于它能够并行处理序列，"
                                 "并且可以捕捉长距离依赖关系。"),
            ("https://b.test/2", "这种注意力机制的一个优势是可以并行处理序列，"
                                 "训练速度比循环网络更快。"),
        ])
        (claim,) = out["claims"]
        assert claim["verdict"] == "confirmed"


# ─── 3 · claim splitting ──────────────────────────────────────────────────

class TestTheClaims:

    def test_compound_questions_split_into_separate_claims(self):
        out = cross_check("Does the tower cost 30 euros and is it open in July?",
                          _TOWER)
        assert len(out["claims"]) == 2
        assert all(row["claim"].strip() for row in out["claims"])

    def test_the_claim_cap_is_declared_in_warnings(self):
        query = "；".join(f"claim number {i} about a different subject entirely"
                          for i in range(9))
        out = cross_check(query, _TOWER)
        assert len(out["claims"]) == 5
        assert any("only the first 5" in w
                   for w in out["cross_check_basis"]["warnings"])

    def test_the_score_is_named_agreement_not_confidence(self):
        out = cross_check("How tall is the Eiffel Tower?", _TOWER)
        assert "agreement_score" in out["claims"][0]
        assert "confidence" not in out["claims"][0]

    def test_a_capped_url_list_is_declared(self):
        blocks = [(f"https://site{i}.test/a",
                   "The Eiffel Tower is 330 metres tall including its antennas.")
                  for i in range(9)]
        out = cross_check("How tall is the Eiffel Tower?", blocks)
        (claim,) = out["claims"]
        assert len(claim["support"]) == 6
        assert any("URL(s) were left out" in w
                   for w in out["cross_check_basis"]["warnings"])


# ─── 4 · honesty and failure ──────────────────────────────────────────────

class TestTheEdges:

    def test_no_source_text_says_so_and_returns_an_empty_report(self):
        out = cross_check("Anything?", [])
        assert out["claims"] == []
        assert out["agreement_map"] == {"green": [], "yellow": [], "red": []}
        assert out["cross_check_error"] == ""
        assert "nothing to cross-check" in out["cross_check_basis"]["warnings"][0]

    def test_the_report_says_it_is_not_a_fact_check(self):
        out = cross_check("How tall is the Eiffel Tower?", _TOWER)
        warnings = " ".join(out["cross_check_basis"]["warnings"])
        assert "not a fact-check" in warnings

    def test_a_source_with_no_usable_sentence_is_an_empty_report(self):
        out = cross_check("How tall is the Eiffel Tower?",
                          [("https://a.test/1", "短。")])
        assert out["claims"] == []
        assert "usable sentence" in out["cross_check_basis"]["warnings"][0]


# ─── 5 · through the tool ─────────────────────────────────────────────────

class TestTheCall:

    def test_the_default_response_carries_no_cross_check_fields(self, monkeypatch):
        _wire(monkeypatch, _TOWER)
        out = asyncio.run(MasterFetchServer().web_research(
            query="How tall is the Eiffel Tower?"))
        for key in ("claims", "agreement_map", "cross_check_basis",
                    "cross_check_error"):
            assert key not in out

    def test_cross_check_true_keeps_the_pack_and_adds_the_report(self, monkeypatch):
        _wire(monkeypatch, _TOWER)
        out = asyncio.run(MasterFetchServer().web_research(
            query="How tall is the Eiffel Tower?", cross_check=True))
        assert len(out["evidence"]) == 2
        assert len(out["sources"]) == 2
        assert out["claims"][0]["verdict"] == "confirmed"
        assert out["cross_check_error"] == ""
        assert out["agreement_map"]["green"]

    def test_a_cross_check_crash_does_not_take_the_pack_down(self, monkeypatch):
        _wire(monkeypatch, _TOWER)

        def boom(_query, _blocks):
            raise RuntimeError("no vectors today")

        monkeypatch.setattr(corroborate_mod, "cross_check", boom)
        out = asyncio.run(MasterFetchServer().web_research(
            query="How tall is the Eiffel Tower?", cross_check=True))
        assert len(out["evidence"]) == 2
        assert out["failed"] == []
        assert out["claims"] == []
        assert "RuntimeError" in out["cross_check_error"]

    def test_a_failed_search_leg_still_answers_the_flag(self, monkeypatch):
        async def dead_search(self, query="", **kw):
            return SimpleNamespace(results=[], error="all engines blocked",
                                   next_action="retry later")

        monkeypatch.setattr(MasterFetchServer, "web_search", dead_search)
        out = asyncio.run(MasterFetchServer().web_research(
            query="anything", cross_check=True))
        assert out["error"] == "all engines blocked"
        assert out["claims"] == []
        assert out["cross_check_error"] == ""

    def test_a_string_false_from_a_client_is_read_as_false(self):
        """裸字符串 "false" 是真——归一化必须在参数层，不能在方法体里靠真值。"""
        from dhole_mcp.params import _coerce_arg_types

        coerced = _coerce_arg_types("web_research", {"cross_check": "false"})
        assert coerced["cross_check"] is False
        coerced = _coerce_arg_types("web_research", {"cross_check": "true"})
        assert coerced["cross_check"] is True
