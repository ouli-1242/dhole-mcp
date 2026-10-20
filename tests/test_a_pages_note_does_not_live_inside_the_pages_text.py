"""R01-024（并入 R01-005）· 一页被切成窗时，那行旁注不许混在正文里交付。

现场（P05 苏婉清 S5→S5b，`sessions/P05-s5-truncated.json`，`max_content_chars=600`）：
`content[0]` 交出来的 531 个字符里，最后 174 个是 dhole 自己写的话 ——

```
… the infrastructure on which it runs

[Truncated: showing 357 of 23,406 extracted chars. 23,049 chars remaining.
Next offset: 357] (243 chars moved to the next window so this one ends at a clause,
not mid-word)
```

这位用户要的是把这一页翻出去复用（翻译、引用、存进自己的笔记）。旁注混在带内，代价是
**每翻一页都要手删一次**；而它同时把两套坐标挤进同一封响应：`showing 357` 说的是抽取坐标，
`len(content[0])` 是 531 —— 差的那 174 就是这句旁注自己。R01-005（P01 陈拓 s3，
`page_total_chars 4594 / total_extracted_chars 3016 / next_offset 2179`）是同一条根因的
第二半：这句话没说它的 `offset` 数的是哪一路，带 focus 的那一路还会随 focus 词变。

仓库里最直白的证据是它自己的测试：两处想要正文长度，都得先把这句旁注**剪掉**再数
（`split("\\n\\n[Truncated")[0]`，见 `test_the_number_that_changes_with_the_focus_string_says_it_is_filtered.py:106,118`）。
连作者都需要手删，用户当然也要。

修法把这句话搬到它自己的字段（`truncation_note`）：`content` 回到 models.py 对它的承诺
（"Extracted text"），两套坐标随之并成一套 —— 正文块自己就是 357 个字符，`showing 357`
不再是另一个数。每一个数字、`next_action` 里那句"offset=N 继续翻"、`is_truncated`/
`next_offset`/`chars_snapped_back` 全部原样保留：这一刀搬走的是位置，不是信息。
"""

from __future__ import annotations

from dhole_mcp import wire
from dhole_mcp.context import _FOCUS
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import _apply_chunking

COOKIE = "第{i}段讲 cookie 的存活时间，配置项叫 DHOLE_COOKIE_TTL_S，默认三千秒。"
OTHER = "第{i}段是别的话题：图表、排版、字体回退与一些无关的说明，用来把这一页填满一点。"


def _page(hits: int = 6, filler: int = 40) -> ResponseModel:
    paras = []
    for i in range(hits):
        paras.append(COOKIE.format(i=i))
        for k in range(filler // hits):
            paras.append(OTHER.format(i=f"{i}{k}"))
    return ResponseModel(url="https://e.test/p", status=200,
                         content=["\n\n".join(paras)], extracted_type="markdown")


def _chunked(result: ResponseModel, *, focus: str | None = None, max_chars: int = 4_000,
             offset: int = 0) -> ResponseModel:
    token = _FOCUS.set(focus)
    try:
        return _apply_chunking(result.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS.reset(token)


# ─── 1 · 交付形状：正文块里只有正文 ───────────────────────────────

def test_the_window_delivers_only_the_pages_own_text():
    out = _chunked(_page(), max_chars=120)
    assert out.is_truncated, "这一发本该是中间的一窗"
    assert "[Truncated" not in out.content[0], out.content[0][-120:]
    assert not out.content[0].endswith(")"), out.content[0][-80:]


def test_the_note_names_every_figure_it_used_to_name_in_the_text():
    """搬走位置不许搬走信息：四颗数一句不少。"""
    out = _chunked(_page(), max_chars=120)
    note = out.truncation_note
    assert note, out
    body = out.content[0]
    assert f"showing {len(body):,}" in note, (note, len(body))
    assert f"{out.total_extracted_chars:,} extracted chars" in note, note
    assert f"Next offset: {out.next_offset}]" in note, note
    if out.chars_snapped_back:
        assert f"{out.chars_snapped_back:,} chars moved to the next window" in note, note


def test_the_two_coordinate_systems_are_one_number_now():
    """修前实测的形状：`showing 357` 与 `len(content[0])==531` 差一颗旁注的长。"""
    out = _chunked(_page(), max_chars=120)
    assert len(out.content[0]) == out.next_offset, (
        f"正文 {len(out.content[0])} 与 next_offset {out.next_offset} 不是同一个数 —— "
        "说明还有别的东西混在带内")


def test_a_page_that_arrived_whole_pays_no_note():
    out = _chunked(_page(hits=1, filler=1), max_chars=4_000)
    assert not out.is_truncated, out.is_truncated
    assert out.truncation_note == ""
    assert "truncation_note" not in wire._wire_dump(out), "默认值上线了（压实表里没这颗？）"


def test_the_last_window_opens_with_its_own_marker_not_a_suffix():
    """翻到底那一支的 `[No more content.]` 是整块正文的替身，不是挂在正文尾巴上的旁注：
    它不在本刀的范围里，也不许被这次搬家顺手抹掉。"""
    page = _page(hits=2, filler=6)
    last = _chunked(page, max_chars=60, offset=10 ** 6)
    assert last.content == ["[No more content.]"], last.content
    assert last.truncation_note == "", last.truncation_note
    assert not last.is_truncated


# ─── 2 · 边界与接续：搬走旁注之后仍然是逐字无缝 ────────────────────

def test_the_snapped_characters_still_arrive_on_the_next_page():
    """P05 实测的加分项（重叠=0）不许被这次搬家弄坏：正文块拼起来就是那一路的全文。"""
    page = _page(hits=4, filler=40)
    whole = "\n\n".join(page.content)
    first = _chunked(page, max_chars=100)
    second = _chunked(page, max_chars=100, offset=first.next_offset)
    kept = first.next_offset
    assert whole[:kept] == first.content[0], "第一窗的正文不再是它声称的那一段"
    assert second.content[0].startswith(whole[kept:kept + 20]), (
        "下一页不是从这一页停下的地方开始的")
    assert first.chars_snapped_back == 0 or first.content[0] == whole[:kept]


def test_the_pointer_to_the_next_window_still_lives_where_an_agent_looks():
    """`next_action` 那句"offset=N 继续翻"是出路的本体，必须原样在。"""
    out = _chunked(_page(), max_chars=120)
    assert f"offset={out.next_offset}" in out.next_action, out.next_action
    assert out.next_offset and out.is_truncated


# ─── 3 · R01-005 那一半：这句话要说它数的是哪一路 ─────────────────

def test_a_focused_stream_says_which_coordinate_its_offsets_count_against():
    """带 focus 时 `total_extracted_chars` 是过滤之后那一路的长度 —— 旁注必须自己说。

    夹具要够长：`MIN_CHUNK_CHARS=500` 那一档以下 dhole 把剩下的并进这一窗（不发第二刀），
    于是根本没有"窗"可说。第一版拿 hits=6/filler=60 打，focus 后只剩 398 字，走的是
    "整页都给你"那一支 —— 断言的是不存在的形状，不是产品的错。
    """
    page = _page(hits=20, filler=200)
    out = _chunked(page, focus="cookie 存活时间", max_chars=120)
    assert out.is_truncated, (out.total_extracted_chars, out.truncation_note)
    assert out.pagination_stream == "focused", out.pagination_stream
    assert out.page_total_chars > out.total_extracted_chars, (
        out.page_total_chars, out.total_extracted_chars)
    note = out.truncation_note
    assert f"this page holds {out.page_total_chars:,} before focus" in note, note
    assert "these offsets count against the filtered text" in note, note


def test_an_unfocused_window_names_no_second_coordinate():
    """没动那一路就不许多说一句：默认那一路是全文，人人都知道的话不占字节。"""
    out = _chunked(_page(), max_chars=120)
    assert out.pagination_stream == "full", out.pagination_stream
    assert "before focus" not in out.truncation_note, out.truncation_note


# ─── 4 · 手册：这颗字段要住在 agent 会翻的地方 ────────────────────

def test_the_note_is_documented_where_an_agent_will_look():
    from dhole_mcp import guide

    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "truncation_note" in corpus, "字段上了 wire 而手册没有它：只读手册的 agent 不认识它"


def test_the_wire_carries_the_field_by_name():
    dumped = wire._wire_dump(_chunked(_page(), max_chars=120))
    assert "truncation_note" in dumped, sorted(dumped)
