"""响应字段在全语料里查无此人 = 这个字段对只读手册的 agent 不存在。

请求侧早就有对等的闸（test_every_wire_parameter_is_findable_in_the_manual.py，
F-E-11 立的），**响应侧一直没有**。这个缺口不是洁癖：一次 15 场景的真人体测里，
至少有五处漂移都来自"字段确实上线了、手册里从来没解释过它意味着什么"——

  · `date_filter.exact=true` 只声明"预设串发出去了"，读者按字面读成"结果被过滤过"
  · `cached=true, duration_ms=0` 让一次缓存回放长得和一次重新核验一模一样
  · `source`/`archived_at` 明明白白写着内容来自 Wayback，`summary` 一个字不提
  · `engines_consensus="1 of 3"` 的分母会随池子缩水，读者以为它是固定的
  · `source_type` 在搜索行和抓取行上是**两套枚举**（搜索行根本产不出 github）

口径照抄请求侧那位：词边界匹配、**表面只用语料**（字段名在 wire 上本来就出现，
掺进来就是同义反复的自查，永远假绿）。
"""

from __future__ import annotations

import re

import pytest

from dhole_mcp import guide
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
from dhole_mcp.feed import FeedItem, FeedResult
from dhole_mcp.models import BulkResponseModel, ResponseModel
from dhole_mcp.search import SearchResponseModel, SearchResult


def _mentions(text: str, key: str) -> bool:
    """按词边界找字段名：`page` 不该被 `pages=1-5` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in guide._ROOT.rglob("*.md"))


# 每个信封都过一遍。PrivateAttr 不在 model_fields 里，天然排除 —— 它们按设计
# 就不上线，要求手册解释一个不上线的内部读数，只会逼人往手册里塞噪声。
_MODELS = {
    "ResponseModel": ResponseModel,
    "BulkResponseModel": BulkResponseModel,
    "CrawlPage": CrawlPage,
    "CrawlResponseModel": CrawlResponseModel,
    "SearchResponseModel": SearchResponseModel,
    "SearchResult": SearchResult,
    "FeedResult": FeedResult,
    "FeedItem": FeedItem,
}


@pytest.mark.parametrize("name", sorted(_MODELS))
def test_every_response_field_is_named_in_the_manual(name):
    model = _MODELS[name]
    corpus = _corpus()
    missing = sorted(k for k in model.model_fields
                     if not _mentions(corpus, k))
    assert not missing, (
        f"{name} 的这些响应字段在全语料查无此人 —— 只读手册的 agent 不知道它们"
        f"存在，更不知道该怎么信它: {missing}。补进 references/fields.md"
        "（写清这个字段在什么条件下出现、读者该拿它做什么判断），"
        "或把字段从信封上撤下来。")


def test_the_response_field_coverage_surface_is_the_corpus_alone():
    """机制自检：表面若混进 wire 文本，覆盖率永远假绿；语料空了同理。

    请求侧那位（test_every_wire_parameter_is_findable_in_the_manual）撞的就是这个
    坑，这里同法自证。
    """
    corpus = _corpus()
    assert len(corpus) > 20_000, "语料空了：字段覆盖率会伪装成通过"
    # 一个确实上线、也确实写在手册里的字段必须能找到
    assert _mentions(corpus, "content_ok"), "content_ok 都不在语料里，说明表面取错了"
    # 而词边界必须真的在挡事：子串命中不算数
    assert not _mentions("escalation_pathway to retry", "escalation_path")
