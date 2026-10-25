"""R01-003：next_action 自荐的 resume 调用形状必须照抄就能过门。

现场（2026-10-25 · P02 老周 · S5 断点续爬 · review/round-01）：
`web_crawl` max_pages 停下后，next_action 教「pass resume_token='e4590e39e63a:0'
to web_crawl」——照抄（不带 url）→ `Missing required argument(s) for web_crawl:
['url']`。自荐的形状过不了自己的门，8 秒白烧 + 一次照抄信任的磨损。

地图那两路（crawl.py 的 web_discover 文案）早就带着 `url=`；只有正文爬取这一路
的 `_resume_sentence` 少写了。同文件两作者不一致，跟的是缺的那个。

契约（本文件钉住）：
  * `_resume_sentence` 的句子必须点名 `web_crawl(url=…, resume_token=…)`——
    门要 url，句子就要带 url；
  * 不重抓、续传语义的两句话照旧（不许借修文案把语义改没）。
"""

from __future__ import annotations

from dhole_mcp.crawl import _resume_sentence


def test_the_sentence_names_the_full_call_shape_the_gate_accepts():
    sentence = _resume_sentence("e4590e39e63a:0", held=9, kept=3,
                                url="http://127.0.0.1:8399/collections")
    assert "web_crawl(url=" in sentence, sentence
    assert "resume_token='e4590e39e63a:0'" in sentence, sentence


def test_the_no_refetch_promise_survives_the_rewording():
    sentence = _resume_sentence("ab12:0", held=5, kept=2, url="https://x.test/")
    assert "without re-fetching the 2 page(s)" in sentence, sentence
    assert "5 queued URL(s)" in sentence, sentence
