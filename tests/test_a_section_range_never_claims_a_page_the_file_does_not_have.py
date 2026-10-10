"""一份文档自己的书签不许把调用方派往一页不存在的页（P3）。

`table_of_contents` 是调用方挑 `pages=` 的那张地图，所以区间越界不是"信息多一点"，
是一次把人要去的调度。缺陷实测（3 页的文档，书签指向一份更长的版本 —— 这种事在真实
PDF 里成得起来，书签是从上一版带过来的）：

    改前：{'Section two', page 3, end_page 98}      # 95 页不存在
          {'Outlook (stale)', page 99, end_page 3}  # 区间倒转，end 比 start 还早
          len(pdf) 失败的路径 → end_page 0 而 page 2 # 同样倒转

而 `_toc_within_pages` 判"这一节的区间是否碰到你要的页"用的是 `range(start, end+1)`：
`range(3, 99)` 碰到**任何**请求，所以越界的那条不但留着，还会替自己把不相干的页认领下来。

修在区间的属主一处（`_add_end_pages`）：两端都钉在文档真实有的页上，页数读不出来时只报到
自己那一页而不是猜一个上界。起点就越界的条目**交出来并点名**（`_outline_inside_the_file`
+ summary）：它钳不成真相——没有一页可以指过去 —— 悄悄丢掉才是这批一直在打的那一种罪。

夹具在测试运行时从真 PDF `tests/wrapped_heading.pdf` 造（pypdf 加书签），与
`tests/test_pdf_real.py` 里"加密样本由 pypdf 从真文件生成，不往仓库塞二进制"同一规矩。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dhole_mcp.pdf_extractor import (
    _add_end_pages,
    _extract_toc,
    _toc_within_pages,
    extract_pdf,
)

HERE = Path(__file__).resolve().parent
BASE_PDF = HERE / "wrapped_heading.pdf"   # 真 3 页文档，正文里有三个小节标题


def _with_outline(stale: bool = True) -> bytes:
    """给真 PDF 加一份书签；`stale=True` 时最后一项指向第 99 页（这文档没有）。"""
    pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    writer.append(PdfReader(str(BASE_PDF)))
    writer.add_outline_item("Section one", 1)      # 0-based → 第 2 页
    writer.add_outline_item("Section two", 2)      # 0-based → 第 3 页
    if stale:
        writer.add_outline_item("Outlook from the longer edition", 98)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def stale_body() -> bytes:
    return _with_outline(stale=True)


@pytest.fixture(scope="module")
def clean_body() -> bytes:
    return _with_outline(stale=False)


class TestARangeStaysInsideTheFile:

    def test_no_section_reports_a_range_past_the_last_page(self, stale_body):
        res = extract_pdf(stale_body)
        assert res.pages_total == 3, res.pages_total
        for entry in res.table_of_contents:
            assert entry["end_page"] <= res.pages_total, entry
            assert entry["end_page"] >= entry["page"], entry

    def test_the_inverted_range_is_gone_not_merely_smaller(self, stale_body):
        """改前那条 `page 99 / end_page 3` 的形状：end 比 start 还早。"""
        for entry in _extract_toc(stale_body):
            assert not (entry.get("end_page", entry.get("page")) < entry["page"]), entry

    def test_a_clamped_range_still_resolves_to_the_section(self, clean_body):
        """钳过之后区间必须还能用：照地图上的 `page-end_page` 去要，要得回来。

        只把数字改小不算修好 —— 调用方拿的是"这一节在哪几页"，钳完必须还指得中。
        """
        toc = _extract_toc(clean_body)
        assert len(toc) >= 2, toc
        for entry in toc:
            spec = (str(entry["page"]) if entry["end_page"] == entry["page"]
                    else f"{entry['page']}-{entry['end_page']}")
            got = extract_pdf(clean_body, pages=spec)
            assert got.error == "", (spec, got.error)
            assert any(p == entry["page"] for p in got.pages_extracted), (spec, got.pages_extracted)

    def test_the_pages_filter_does_not_resurrect_an_out_of_range_section(
            self, stale_body):
        """`range(3, 99)` 从前碰到任何请求都算"相关"；钳过之后要能筛掉。"""
        toc = _extract_toc(stale_body)
        kept = _toc_within_pages(toc, [1])
        assert all(e["page"] == 1 or e["end_page"] >= 1 for e in kept)
        assert not any("Section two" == e["title"] for e in kept), (
            f"只要第 1 页，却留下了一节从第 3 页开始的地图：{kept}")


class TestAnOutOfRangeBookmarkIsSaidOutLoud:

    def test_the_entry_is_left_out_and_named(self, stale_body, tmp_path):
        import asyncio

        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.server import MasterFetchServer

        pdf = tmp_path / "stale_outline.pdf"
        pdf.write_bytes(stale_body)

        async def run():
            with _arg_notes_scope() as notes:
                out = await MasterFetchServer().doc_parse(str(pdf))
                return out, list(notes)

        out, notes = asyncio.run(run())
        joined = " ".join(notes)
        assert "outline points 1 section" in joined, joined
        assert "does not have" in joined, joined
        assert "page 99" in joined, "点名要点到那一页，不然读者还是不知道丢了谁"
        assert "Outlook from the longer edition" not in str(
            out.model_dump().get("table_of_contents")), out.model_dump()

    def test_the_dropped_entry_is_not_in_the_map(self, stale_body):
        res = extract_pdf(stale_body)
        titles = [e["title"] for e in res.table_of_contents]
        assert "Outlook from the longer edition" not in titles, titles
        assert "Section one" in titles and "Section two" in titles

    def test_a_healthy_outline_announces_nothing(self, clean_body):
        """没丢东西就别说这句话 —— 告警的默认值是沉默。"""
        from dhole_mcp.context import _arg_notes_scope
        import asyncio

        async def run():
            with _arg_notes_scope() as notes:
                _extract_toc(clean_body)
                return list(notes)

        assert asyncio.run(run()) == []
        titles = [e["title"] for e in _extract_toc(clean_body)]
        assert titles == ["Section one", "Section two"], titles


class TestTheRangeMathAlone:
    """`_add_end_pages` 单钉：区间是它算的，越界与倒转都只能在它这一层收住。"""

    def test_an_end_from_the_next_sibling_is_clamped_to_the_document(self):
        got = _add_end_pages([{"level": 1, "title": "a", "page": 2},
                              {"level": 1, "title": "b", "page": 99}], 3)
        assert [e["end_page"] for e in got] == [3, 99], got
        # 第二条起点就越界，区间只能报到它自己 —— 报 3 会造出一个倒转的区间
        assert got[1]["end_page"] >= got[1]["page"]

    def test_an_unknown_page_count_invents_no_upper_bound(self):
        """`len(pdf)` 失败时 total_pages 是 0：那时一节只报到它自己那一页。

        记一条过程更正：这里原本另写了一枝 `total_pages if total_pages > 0 else start`，
        反向清单跑出来它**摘掉也不红** —— 后面的 `max(start, end)` 已经把它要挡的那件事
        挡掉了。一个摘掉不红的分支不是保险，是第二个属主，所以删掉，只留这条 observable
        断言（它由 `max(start, end)` 那一行守着，摘掉那行这里就红）。
        """
        got = _add_end_pages([{"level": 1, "title": "a", "page": 2}], 0)
        assert got[0]["end_page"] == 2, got

    def test_a_last_section_still_reaches_the_last_page(self):
        """正对照：合法文档的"到末页"这句话不许被钳没。"""
        got = _add_end_pages([{"level": 1, "title": "a", "page": 2},
                              {"level": 1, "title": "b", "page": 4}], 9)
        assert got[0]["end_page"] == 3 and got[1]["end_page"] == 9, got

    def test_the_start_page_of_an_entry_is_never_rewritten(self):
        """钳的是区间，不是书签说的那一页。

        把 99 改成 3 会"看起来合法"，代价是把一节安在它从没在过的页上 —— 那比越界
        更难发现，所以那种条目走"丢掉并点名"那条路（`_outline_inside_the_file`）。
        """
        got = _add_end_pages([{"level": 1, "title": "a", "page": 99}], 3)
        assert got[0]["page"] == 99, got


class TestTheManualCarriesTheRule:

    def test_the_field_dictionary_says_a_range_is_always_resolvable(self):
        """`pages=` 是照这张地图挑的，所以"区间一定指得中"要写在字段词典里。"""
        from dhole_mcp.guide import _ROOT

        text = (_ROOT / "references" / "fields.md").read_text(encoding="utf-8")
        for needle in ("pinned to the pages this file actually has",
                       "left OUT and said in `summary`",
                       "never clamped onto a page it never named"):
            assert needle in text, f"fields.md 没写着「{needle}」"
