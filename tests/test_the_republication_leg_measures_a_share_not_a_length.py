r"""同文判据的第二条腿要量的是「共享的那段占较短摘要的几成」，不是「共享了几个字」。

现场（P02-01/P02-02 · R02-047 · 台账自己写着「线要往哪儿放，现有语料里真同文只有 1 对，
样本不足以定线」）：这条腿（snippet 共享 ≥20 字）加上之后，最终那一趟 10 发真检索
（4 采集 + 6 hunt）一个组都没出，而几分钟前同一批查询只按标题的规则出过 3 组
（含一次 4 行跨 4 host 的假合并）。

本刀的语料是**当场量出来的**（`review/.runtime/p4/f38_measure.py`，读 `evals/corpus/serp`：
67 条人工标注真查询 × 各引擎的冻结页 = 931 行；标题相合且 host 不同的配对 367 条）：

    按「共享了几个字」看：假合并那 362 条最大共享 46 个字，而**入账那份现场**
        （P02-01 存档里同一份补充公告挂在开封本地宝与 chinamae）只共享 34 个字 ——
        绝对长度这把尺把两堆叠在一起：线放 ≤34 就放行 173 条假合并，放 >46 就切掉入账那一发。
    按「共享的那段占较短摘要的几成」看：
        假合并 362 条全部 ≤ 0.3197（0.32 以上一条都没有）
        真镜像 1.000 / 1.000 / 1.000 / 1.000 / 0.795，入账那一发 0.415（34 字 / 较短摘要 82 字）
        ⇒ 0.3197 与 0.415 之间是一条**空的带**（宽 0.095），线坐得进去，两端都留余量。

根因因此不在"线放低了/放高了"，在**尺的形状**：引擎摘要从 26 字到 300 字不等，
"20 个字"对一条 300 字的摘要几乎什么都不说，对一条 60 字的摘要却是近乎整份拷贝。

契约（本文件钉住）：
  * 第二条腿改读**占比**，闸门坐进量出来的空带（`0.3197 < RATIO < 0.415`）；
    绝对长度那枚地板留在 20 不动（它守的是另一件事：两段都很短的摘要里，20 个字还不足以
    当拷贝的证据 —— 那一半由 `test_the_absolute_floor_still_guards_tiny_snippets` 钉住）；
  * 两端各钉一组真语料：真镜像的四个 query 仍然成组（不退化锁），假合并最密的三个 query
    一个组都不出（这一格实现前是红的）；
  * **入账那一发逐字回放**：新尺之下仍要成组 —— 这条腿当初就是为它立的；
  * **同一把尺不许有两个作者**：字段说明里的两颗数由常数拼出来；
  * 成组与否都不许移动、重排、改档位任何一行。
"""

from __future__ import annotations

import glob
import os
import re

import pytest

from dhole_mcp import search
from dhole_mcp.search import (SearchResult, _DOC_SNIPPET_RUN_MIN_CHARS,
                              _registrable_domain, _same_document_groups,
                              _shared_literal_run)

CORPUS = os.path.join("evals", "corpus", "serp")

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
    for path in sorted(glob.glob(f"{CORPUS}/{qid}__*.html")):
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


def _row(pos: int, title: str, url: str, snippet: str) -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet, position=pos,
                        relevance_score=0.9, fetch_relevance="high")


#: 入账现场（`sessions/P02-01-s1b-refined-search.json`）那 6 行的逐字回放挂具：
#: 同一份补充公告挂在四个地址，另外两行是一条无关的通知与一份公示名单。
BOOKED_ANNOUNCE = "河南省2025年家电产品以旧换新品牌厂家征集的补充公告"
BOOKED_COMMON = ("具体可参照《河南省2025年家电及3C产品以旧换新品牌厂家征集公告》要求,"
                 "进行厂家报名。咨询电话 0371")
BOOKED_ROWS = [
    _row(1, BOOKED_ANNOUNCE + "- 开封本地宝", "http://kf.bendibao.com/news/20495.shtm",
         BOOKED_COMMON + "-65897，申报材料需加盖公章，逾期不予受理，工作日九时至十七时受理。"),
    _row(2, "2025年第五批家电以旧换新企业公示名单", "http://swj.zhuzhou.gov.cn/c16568.html",
         "2025年第五批家电以旧换新企业公示名单，共 132 家"),
    _row(3, "商务部等4部门办公厅关于做好2025年家电以旧换新工作的通知",
         "https://www.mofcom.gov.cn/index.html",
         "为推动家电以旧换新工作有序开展，现就有关事项通知如下，请各地结合实际认真组织实施。"),
    _row(4, "河南省商务厅-" + BOOKED_ANNOUNCE,
         "https://hnsswt.henan.gov.cn/2025/01-06/3108794.html",
         "各省辖市商务局，郑州航空港实验教学区管委会，各有关单位：按要求进行厂家报名。"),
    _row(5, BOOKED_ANNOUNCE, "https://www.chinamae.com/a/3108794.html",
         BOOKED_COMMON + "-65897。"),
    _row(6, "2025年河南家电产品以旧换新品牌厂家征集补充公告- 郑州本地宝",
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
        _row(1, "What is retrieval-augmented generation (RAG)? - IBM Research",
             "https://research.ibm.com/http/a",
             common + tail_a + " It is used across enterprise search deployments today."),
        _row(2, "What is Retrieval-Augmented Generation (RAG)? | NVIDIA Glossary",
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
        _row(1, pair_long[0].title, pair_long[0].url, common + tail_a),
        _row(2, pair_long[1].title, pair_long[1].url, common + tail_b),
    ]
    short_ratio = _share_ratio(pair_short[0].snippet, pair_short[1].snippet)
    assert short_ratio > search._DOC_SNIPPET_RUN_MIN_RATIO, short_ratio
    assert len(_same_document_groups(pair_short)) == 1, (
        "同样这几个字在短摘要里就是近乎整份拷贝，这一对必须成组")


def test_the_absolute_floor_still_guards_tiny_snippets():
    """两段各 45 字、共享 18 个字：占比过线了，字数没过地板 —— 那还是偶然撞上的两句广告词。"""
    shared = "本周新增产能两成，库存下降"
    a = _row(1, "某行业周报第 12 期：产能与库存", "https://a.example.org/w12",
             shared + "另外三家样本企业的开工率同步走高。")
    b = _row(2, "某行业周报第 12 期：产能与库存摘要", "https://b.example.net/s12",
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
