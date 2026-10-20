"""一页一块的页面在这台量具上不许是隐形的，而保留率量的必须是字符不是块数。

为什么要动这把尺子（期 0.3，是期 3.5 分块的前置）：`_split_blocks` 只按**空行**切块，
所以一份 81,000 字、段落之间只有单换行的在线文档（真实站点上量到过的那种形状）整页就是
**一块**。对那一页，focus 什么都做不了 —— 而 `evals/focus_baseline.py` 当时的第一行是：

    blocks = _split_blocks(doc)
    if len(blocks) <= 1:
        return None        # ← 这一格把整类页面从分母里删掉了

`return None` 不是"这一页没切"，是"这一页没被量过"。仓库里唯一测 focus 的量具对整类页面
瞎，而它报出来的均值因此**更好看**（少了一格永远 100% 的用例）。这不是一种测量误差，
是测量自己造出来的一个事实。

第二件事跟着第一件走：指标从"保留块 / 总块数"改成"保留字符 / 全文字符"。一块的大小不是
信息，省下来的 token 才是；块数占比在一页一块时只能回答 0% 或 100%，而 100% 那一格与一份
四百块的页交出 100% 是同一种难看 —— 前者连"切错了"都说不清，它只是没切。

汇总因此也换成字符加权（`Σchars_kept / Σchars_total`），不是各用例占比的均值：均值会把
那一页 40,000 字一刀没切与另一页 2,000 字切到 1/8 同等对待。

五条判据都在离线里跑（BM25 一支不需要 reranker，所以这些用例零模型、零网络、零靶站）。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals"))

import focus_baseline as FB  # noqa: E402

from dhole_mcp.focus import _split_blocks  # noqa: E402

BIG = "".join(f"这是第 {i} 段很长的正文，用来把字符数顶起来。" * 12 for i in range(20))
ANSWER = "会话 cookie 的存活时间是 DHOLE_COOKIE_TTL_S 决定的。"


def _two_block_doc() -> str:
    """一块装着答案（短），一块是长正文 —— 两块的字符数差一个量级。"""
    return f"{ANSWER}\n\n{BIG}"


# ─── 1 · 保留量的是字符，不是块数 ────────────────────────────────

def test_the_reported_retention_is_char_weighted_not_block_weighted():
    row = FB.run_one(_two_block_doc(), "cookie 的存活时间", "DHOLE_COOKIE_TTL_S", None)
    assert row, "这份语料里没有接地答案？用例本身要能成立"
    kept = row["bm25"]
    assert kept["retention"] == round(kept["chars_kept"] / row["chars_total"], 3), kept
    # 两把尺在这一格上必须**给出不同的数**，否则"换成字符加权"这句话没有内容
    assert kept["retention"] != kept["block_retention"], kept
    assert kept["retention"] < kept["block_retention"], (
        "保留下来的那块是短的那块：字符占比应当**低于**块数占比，报出来相反就是接错了线")


def test_the_answer_still_has_to_survive_the_cut():
    """省 token 不许以丢掉答案为代价 —— 分母约束跟着新尺子一起留着。"""
    row = FB.run_one(_two_block_doc(), "cookie 的存活时间", "DHOLE_COOKIE_TTL_S", None)
    assert row["bm25"]["answer_kept"] is True, row


# ─── 2 · 整页一块要留下一行记录，而不是从分母里消失 ────────────────

def test_a_one_block_page_is_measured_not_skipped():
    """一块的页面（短到 3.5 也切不开的那种）仍然必须留下一行记录。

    这一格守的是 `run_one` 那句 `if len(blocks) <= 1: return None` 被删掉之后不再复发：
    一台量具对某一类页面瞎，比它报一个难看的数更糟。类"段落之间只有单换行的长文档"
    今天已经不属于这一类了 —— 3.5 把它切开了，那条判据住在
    `tests/test_a_page_without_blank_lines_is_still_made_of_blocks.py`。
    """
    doc = ANSWER + "\n这一段没有空行，也短不到会被断开，用来造出真正只有一块的一页。" * 3
    assert len(_split_blocks(doc)) == 1, "这份夹具不再是「一整块」的形状了"
    row = FB.run_one(doc, "cookie 的存活时间", "DHOLE_COOKIE_TTL_S", None)
    assert row is not None, (
        "整页一块又被从分母里删掉了 —— 那正是这次要修的瞎")
    assert row["one_block"] is True, row
    assert row["blocks"] == 1 and row["bm25"]["retention"] == 1.0, row


def test_the_corpus_carries_the_one_block_shape():
    """量具的语料里就得有这一形状，否则"看得见"只存在于用例文件里。"""
    docs = FB.load_docs()
    one_block = [name for name, (doc, _cases) in docs.items()
                 if len(_split_blocks(doc)) == 1]
    assert one_block, f"语料里没有整页一块的那一份：{sorted(docs)}"
    assert "article_no_blank_lines" in one_block, one_block


def test_the_one_block_shape_actually_reaches_the_summary():
    """形状在语料里而那一格没进分母，等于还没看见 —— 这一条守的就是那个"等于"。

    2026-10-10 的活跑量的正是这一格没守住：`article_no_blank_lines` 一块没错，可 `CASES`
    那五颗仓库关键词它一个都不含，五个用例全部作废，汇总于是印着"其中整页一块的 0 个"。
    一份答不出问题的语料与一份没有的语料，在报表上是同一个形状。
    """
    measured = []
    for name, (doc, cases) in FB.load_docs().items():
        for question, keyword in cases:
            row = FB.run_one(doc, question, keyword, None)
            if row is not None:
                measured.append((name, row["one_block"], row["bm25"]["answer_kept"]))
    assert measured, "所有语料的所有用例都作废：这台量具在测空气"
    assert any(ob for _n, ob, _a in measured), (
        f"没有一条用例落在「整页一块」那一格上：{sorted(set(measured))}")
    # 那一格的召回也得报出来：一刀没切时答案必然还在里面，不在就是接错了线
    for name, one_block, answer_kept in measured:
        if one_block:
            assert answer_kept, f"{name}：整页一块还丢了答案，那不是切块问题"


# ─── 3 · 作废只有一种理由 ────────────────────────────────────────

def test_a_case_without_a_grounded_answer_is_the_only_legitimate_skip():
    assert FB.run_one(_two_block_doc(), "无关的问题", "NOT_IN_CORPUS_XYZ", None) is None


# ─── 4 · 汇总的分母是字符，不是用例数 ────────────────────────────

def test_the_aggregate_weighs_by_size_not_by_case_count():
    """汇总要说的是"实际交回去多少字"，不是"平均每个用例切掉了几成"。

    这一条的方向是量出来才想清楚的，不是先想好再凑数：一份 40,000 字**一刀没切**的页
    与一份 2,000 字切到 1/8 的页，各用例占比的均值报 0.5625（"看着省了一半"），而
    实际交回去 40,250 / 42,000 = 0.958 —— 均值替一个失败用例打了掩护。加权之后那个
    大页按它真实的大小占权重，藏不住了。
    """
    fat = {"chars_total": 40_000, "bm25": {"chars_kept": 40_000}}
    thin = {"chars_total": 2_000, "bm25": {"chars_kept": 250}}
    weighted = FB.weighted_retention([fat, thin], "bm25")
    mean_of_ratios = (1.0 + 250 / 2_000) / 2
    assert weighted == 40_250 / 42_000, weighted
    assert weighted > mean_of_ratios, (
        f"均值替没切动的那页打了掩护：加权应当更难看（{weighted} vs {mean_of_ratios}）")


def test_the_aggregate_follows_the_size_in_both_directions():
    """反向：大的那页切动了，加权汇总就比均值**更好看** —— 尺子跟着字节走，不站队。"""
    big_cut = {"chars_total": 40_000, "bm25": {"chars_kept": 2_000}}
    small_kept = {"chars_total": 100, "bm25": {"chars_kept": 100}}
    weighted = FB.weighted_retention([big_cut, small_kept], "bm25")
    mean_of_ratios = (2_000 / 40_000 + 1.0) / 2
    assert weighted == 2_100 / 40_100, weighted
    assert weighted < mean_of_ratios, (weighted, mean_of_ratios)


def test_an_empty_or_zero_denominator_aggregate_says_so():
    from math import isnan

    assert isnan(FB.weighted_retention([], "bm25"))
    assert isnan(FB.weighted_retention(
        [{"chars_total": 0, "bm25": {"chars_kept": 0}}], "bm25"))
