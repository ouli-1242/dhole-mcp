r"""点了帽、实际给的比帽多 —— 这一封回执说得出「多了多少、为什么多、没丢东西」。

现场（P02-02 邝野 S3 预算与截断的算术 · R02-007 · 存档 `sessions/P02-02-s3b-single-cap.json`
对照 `P02-02-s3c-cap600.json`）：

    web_read(url=/collections?items=60, max_content_chars=1200) → 1,446 字符全给
      `is_truncated=false` · `next_offset=0` · `truncation_note=""` · summary 里没有一个字提到帽
    同一 URL `max_content_chars=600` → 583 + `next_offset=583` + 167 字符的完整截断说明

先说什么没坏：`remaining ≤ 500` 把尾巴并回这一窗是**故意**的（少一次往返），且它守在有测试的
地板上（B-17 记过「地板 500 会被子句吸附吃到 462」那半个问题）。缺的是**超帽不申报**这一半：
调用方点了一个数，实际收到的是另一个数，而回执把这件事当成没发生过。用户判词：
「少了不说我最恨，多了不说我也得防。」

本刀只加主语，不改判据：交付的字节一个不动（不改成报错、不把帽变成硬顶 —— 那会新增往返、
会删内容），只在**这一封**回执里把「比您点的帽多了 N 个字符」说出来。

契约（本文件钉住）：
  * 说话的条件是「**调用方点过名**的帽被超过」：`MAX_CHARS_NAMED` 为空（用默认预算）时照旧
    **沉默** —— 这句是回答「我的指示被怎么执行了」，没人下过指示就没什么可回答（与
    `_timeout_floor_note`/`_note_clamped` 同一个家族规则：`requested is None` 就不说话）；
  * 那颗 N 只有一个作者：句子由 `context._over_cap_note()` 拼，`_apply_chunking` 的并回分支把
    **实际交付**与**点名的帽**交给它；测试断言句子里的数 == 回执正文的实际超出量，
    不许写地板 500 那种"看起来像"的数；
  * 申报不撒谎：并回这一支 `is_truncated` 仍为 `False`、`next_offset` 仍为 0、正文一字节不删
    （没有"下一页"，所以不许出现"下一页"那句话）；
  * 正常分页那一支**不重复记账**：有 `truncation_note` 的时候不许再有超帽句；
  * 边界与续页都要覆盖：余量正好等于地板（并回、要报）、地板 +1（分页、不报超帽）、
    余量 1 个字符（报 1，单数）、续页并回（报的是**这一片**的超出量）；
  * 话要落在 `summary`（客户端每一封都读的那一行），不是只落进内部的注记桶；
  * 手册要有这句的形状（只读手册的 agent 也要知道「比帽多」是可能且已被申报的）。
"""

from __future__ import annotations

from dhole_mcp import context
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MIN_CHUNK_CHARS, _apply_chunking

UNIT = "这是第{i}句，用来说明这一页的长度是怎么排布的，好让切口找得到界。"


def _page(total_len: int) -> str:
    return "".join(UNIT.format(i=i) for i in range(400))[:total_len]


def _read(total_len: int, cap: int, offset: int = 0, named: int | None = None):
    """走真 `_apply_chunking`；只把「调用方有没有点名」这一件事交给注记通道。"""
    result = ResponseModel(url="https://e.test/collections", status=200,
                           content=[_page(total_len)], extracted_type="markdown")
    token = context._MAX_CHARS_NAMED.set(named)
    try:
        with context._arg_notes_scope() as notes:
            out = _apply_chunking(result.model_copy(), max_chars=cap, offset=offset)
        return out, list(notes)
    finally:
        context._MAX_CHARS_NAMED.reset(token)


def _body(out) -> str:
    return "".join(out.content or [])


# ─── 1 · 画像那一发：点了 1200，给了 1446 ─────────────────────

def test_a_named_cap_that_was_over_taken_says_so():
    out, notes = _read(1446, 1200, named=1200)
    assert len(_body(out)) == 1446, "并回本身不许被改掉：交付的字节一个都不能少"
    line = " · ".join(notes)
    assert any("max_content_chars" in n for n in notes), (
        f"点了帽却被超过，回执一个字没提：notes={notes} summary={out.summary!r}")
    assert "1200" in line and "246" in line, line


def test_the_over_number_is_the_real_one_not_the_floor():
    """句子里那颗数是**这次**超出量（246），不是地板（500）——地板是原因，不是账。"""
    out, notes = _read(1446, 1200, named=1200)
    real = len(_body(out)) - 1200
    line = " · ".join(notes)
    assert f"{real:,} char" in line, (real, line)
    assert f"{MIN_CHUNK_CHARS:,} char" not in line or real == MIN_CHUNK_CHARS, line


def test_it_says_nothing_was_dropped_and_there_is_no_next_page():
    out, notes = _read(1446, 1200, named=1200)
    line = " · ".join(notes)
    assert out.is_truncated is False, "并回这一支不许假报分页"
    assert out.next_offset == 0, "没有下一页就不许留下 offset"
    assert "no next offset" in line, line
    assert "dropped" in line, line


def test_the_announcement_lands_on_the_line_every_caller_reads():
    """注记桶是内部形状，`summary` 才是客户端每封都读的那一行。"""
    out, _notes = _read(1446, 1200, named=1200)
    assert "max_content_chars" in (out.summary or ""), out.summary


# ─── 2 · 沉默的那几格：没点名的帽、没被超过的帽 ────────────────

def test_a_cap_nobody_named_stays_silent():
    """默认预算不是调用方的指示：超过了也没什么「指示被执行成什么样」可回答。
    （13,490 的页 / 13,000 的默认档 → 并回 490 个字符，与点名那一发同形。）"""
    _out, notes = _read(13490, 13000, named=None)
    assert not any("max_content_chars" in n for n in notes), notes


def test_a_page_shorter_than_the_named_cap_says_nothing():
    _out, notes = _read(500, 600, named=600)
    assert not any("max_content_chars" in n for n in notes), notes


def test_a_page_exactly_as_long_as_the_named_cap_says_nothing():
    _out, notes = _read(600, 600, named=600)
    assert not any("max_content_chars" in n for n in notes), notes


# ─── 3 · 边界：地板、地板 +1、一个字符 ────────────────────────

def test_the_boundary_at_the_floor_merges_and_declares():
    out, notes = _read(1100, 600, named=600)
    assert len(_body(out)) == 1100, out.next_offset
    assert out.is_truncated is False
    assert any("max_content_chars" in n for n in notes), notes


def test_one_char_past_the_floor_paginates_and_does_not_double_book():
    """正常分页那一支已经有自己的 167 字符说明；超帽句不许再挤进来。"""
    out, notes = _read(1101, 600, named=600)
    assert out.is_truncated is True
    assert out.truncation_note, "分页那一支的原有说明不许退化"
    assert not any("max_content_chars" in n for n in notes), notes


def test_the_smallest_over_run_names_one_char():
    out, notes = _read(601, 600, named=600)
    line = " · ".join(notes)
    assert len(_body(out)) - 600 == 1
    assert "1 char" in line and "1 chars" not in line, line


# ─── 4 · 续页：报的是这一片的超出量 ───────────────────────────

def test_a_continuation_page_that_merges_the_tail_declares_it_too():
    out, notes = _read(1446, 600, offset=600, named=600)
    delivered = len(_body(out))
    line = " · ".join(notes)
    assert delivered == 846, delivered
    assert f"{delivered - 600:,} char" in line, (delivered, line)


# ─── 5 · 一句话的作者只有一位 ─────────────────────────────────

def test_the_sentence_has_one_author():
    """句子的拼法住在 `context._over_cap_note`（与 `_timeout_floor_note` 同处）：
    `_apply_chunking` 只交出两个数。回执里那句就是这位亲手拼出来的那一句。"""
    said = context._over_cap_note(named=1200, delivered=1446, floor=MIN_CHUNK_CHARS)
    assert said, "作者不能交回空句"
    assert said.startswith("max_content_chars=1200"), said
    assert "246" in said and "500" in said, said
    _out, notes = _read(1446, 1200, named=1200)
    assert said in notes, (notes, said)
    assert context._over_cap_note(named=None, delivered=1446, floor=MIN_CHUNK_CHARS) == ""
    assert context._over_cap_note(named=1200, delivered=1200, floor=MIN_CHUNK_CHARS) == ""
    assert context._over_cap_note(named=1200, delivered=1100, floor=MIN_CHUNK_CHARS) == ""


# ─── 6 · 手册里要有这两种形状 ────────────────────────────────

def test_the_manual_carries_the_shape():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "max_content_chars=" in corpus
    assert "no next offset" in corpus, (
        "「比帽多但不是丢了」这个形状没进手册：读者只能把它当成又一次截断")
