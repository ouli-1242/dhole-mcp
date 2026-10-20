"""一颗 1.0 与一档 high 必须由同一句话说清是谁给的：座位号不是百分数，档位要说它靠什么站着。

现场（第 02 轮三格同一族的两个形状 · R02-011 + R02-030 · P2 · 组 D；
健康池对照 2026-10-15 已在 9202 重跑，见 sessions/P02-R5-F23-healthy-pool.jsonl）：

    web_search('ceftriaxone diazepam Y-site compatibility chart pdf')
      → row1 relevance_score=1.0 / relevance_raw=0.0295 / fetch_relevance=low
      → fetch_hint=「0 high, 0 med, 6 low. **Ranked by relevance_score**; web_read what
        fits your need (high first, but a lower tier can be the right call).」

那一句话说反了两把尺：**这一轮的档位不是 relevance_score 给的**（`_tier` 在有 raw 时只读
raw），而逐行那颗 1.0 是本轮内的 min-max 座位 —— 第一名按构造恒为 1.0，一整轮全离题时也是。
L1 画像读到的就是那个 1.0：「第一名是一百分吗？那我先点它」—— 临床场景里这一指把人带去一条
鱼的基因数据。

    web_search('那个')  →  4 high / 2 med，raw 0.9956 / 0.9886
    web_search('那几本里有开放获取的吗')  →  1 high / 2 med，raw 0.9658
    对照 web_search('河南省 2025 家电以旧换新 征集公告 申报条件') → 6 high，raw 0.9995

三封的 fetch_hint 逐字同一句「Ranked by relevance_score」。可用的一问与没有指代对象的一问
拿到同一句背书，而这一轮的档位其实**只站在模型的文本一致度上**：`_query_terms()` 对这三问都
数不出可核对的词（实测 67 条真查询里 27% 如此），所以「词面互证」这一维今天根本没有。
R02-030 要的不是判出「这问没头」，是**把两条轴分开交出来**：模型说了什么，以及它脚下有没有
第二根桩。

契约（本文件钉住）：
  * 有判决的每一轮，`fetch_hint` 要说清**档位由谁给出**（raw），而 `relevance_score` 只排座次，
    并且第一行那颗 1.0 是座位不是百分数；
  * 一整轮没有任何一行到 high 时，那句话要点出**本轮最高的 raw 是多少**（数要从证据里来），
    而不是留一颗 1.0 独自站在那里；
  * 这一轮数不出可核对的查询词时，要说明档位只站在模型一致度这一根桩上，并给出查询实际交出
    的字符数 —— 不许假装做过词面互证，也不许判用户「问得没头」；
  * **反面锁（不加噪声）**：数得出查询词的那些轮不许出现那一句；`raw is None`（没有 reranker）
    那一支的既有句子一字不动（上一把刀立的契约）；有判决时不许自称「no reranker」；
  * 阈值只有一位作者：句子里那条 high 线必须等于 `_RAW_HIGH_AT`，不许在措辞里抄第二个数。
"""

from __future__ import annotations

from dhole_mcp.search import _RAW_HIGH_AT, SearchResult, _tier, compute_fetch_hint


def _row(score: float, rank: int, total: int, raw) -> SearchResult:
    r = SearchResult(title=f"t{rank}", url=f"https://e{rank}.example/x{rank}",
                    position=rank, relevance_score=score)
    r.relevance_raw = raw
    # 档位由产品自己的那位作者铸 —— 测的是那句话怎么说，不是手搓一个 tier 字符串。
    r.fetch_relevance = _tier(score, rank, total, raw)
    return r


def _round(raws: list[float], query: str) -> str:
    total = len(raws)
    rows = [_row(1.0 - i / total, i + 1, total, raws[i]) for i in range(total)]
    return compute_fetch_hint(rows, query=query)


# ─── 1 · 两把尺要各说各的（R02-011）───

def test_a_seat_number_is_not_left_reading_as_a_percentage():
    hint = _round([0.0295, 0.0273, 0.0193], "ceftriaxone diazepam compatibility chart pdf")
    assert "relevance_raw" in hint, hint
    assert "seat" in hint.lower(), hint
    assert "Ranked by relevance_score" not in hint, hint


def test_a_round_nothing_cleared_names_its_own_best_number():
    """0 high 那一轮不能只留一颗 1.0：本轮最高的 raw 要点出来，数从证据里来。"""
    hint = _round([0.0295, 0.0273, 0.0067], "ceftriaxone diazepam compatibility chart pdf")
    assert "0.03" in hint, hint          # 本轮真实的最高 raw
    assert "0 high, 0 med, 3 low" in hint, hint


def test_the_line_in_the_sentence_is_the_one_the_ruler_uses():
    """阈值一位作者：句子里的 high 线就是 `_RAW_HIGH_AT`，不是措辞里抄来的第二个数。"""
    hint = _round([0.0295, 0.01], "ceftriaxone diazepam compatibility chart pdf")
    assert f"{_RAW_HIGH_AT:g}" in hint, (_RAW_HIGH_AT, hint)


# ─── 2 · 档位靠什么站着要说出来（R02-030）───

def test_a_round_with_no_term_to_check_against_says_it_rests_on_one_post():
    """`那个` 现场：数不出查询词的那一轮，要点明词面互证这一维今天没有。"""
    hint = _round([0.9956, 0.9886, 0.6379], "那个")
    assert "no" in hint and ("term" in hint or "tokeni" in hint), hint
    assert "2" in hint, hint            # 查询实际交出的字符数，是量出来的不是形容词
    assert "model" in hint.lower(), hint


def test_a_pronoun_followup_is_measured_the_same_way():
    """第二现场（11 个汉字的代词追问）走的是同一条判据 —— 不靠长度阈值。"""
    hint = _round([0.9658, 0.2422, 0.233], "那几本里有开放获取的吗")
    assert "term" in hint or "tokeni" in hint, hint


def test_the_model_is_not_claimed_to_be_wrong_only_because_the_query_is_short():
    """不许把「这一问没有指代对象」当成产品的判决端出来 —— 产品不知道。"""
    hint = _round([0.9956, 0.9886], "那个")
    assert "irrelevant" not in hint.lower(), hint
    assert "not a topic verdict" in hint.lower() or "single" in hint.lower(), hint


# ─── 3 · 反面锁：噪声不许扩散 ───

def test_a_round_that_can_check_terms_does_not_confess_to_not_checking():
    """数得出查询词的那些轮（拉丁文真查询）不许出现那一句自白。"""
    hint = _round([0.9995, 0.9982, 0.6], "python asyncio semaphore concurrency limit")
    assert "tokeni" not in hint and "no query term" not in hint, hint


def test_a_round_without_a_verdict_keeps_its_own_sentence_untouched():
    """上一把刀立的契约一字不动：`raw is None` 那一句必须还是那一句。"""
    rows = [_row(1.0 - i / 6, i + 1, 6, None) for i in range(6)]
    hint = compute_fetch_hint(rows, query="python asyncio semaphore")
    assert "no reranker" in hint, hint
    assert "nothing is claimed 'high'" in hint, hint
    assert "0.03" not in hint, hint


def test_a_round_with_a_verdict_never_claims_to_have_no_reranker():
    hint = _round([0.97, 0.2, 0.0002], "python asyncio semaphore concurrency limit")
    assert "no reranker" not in hint, hint
    assert "1 high" in hint and "1 low" in hint, hint


def test_the_actionable_half_survives():
    """改的是「这封响应说的是什么」，不是把人支走：那一半 advice 必须在。"""
    hint = _round([0.91, 0.6, 0.01], "python asyncio semaphore concurrency limit")
    assert "web_read" in hint, hint
    assert "lower tier can be the right call" in hint, hint
