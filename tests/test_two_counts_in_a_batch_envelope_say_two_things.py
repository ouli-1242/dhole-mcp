# -*- coding: utf-8 -*-
"""R01-031 · 一个信封里两个数，就得说清各自数的是什么。

现场（P07 吴岭，BI/数据，`sessions/P07-s1-batch12.json`）：12 个 URL 的批量
`web_read` 顶层只有 `results / total / successful / ssrf_recheck`，
而 `successful=9`。逐行数 `content_ok`：只有 4 行为真。他把 9 写进给主管的汇报，
那句就成了「9 个源可用」—— 而真实是 4 个。

`successful` 的定义其实一直存在（"status<400 + no error"），问题在两处：
一、这个名字在数据工作者口中就是"拿到了"；二、**响应自己**不携带任何定义，
读的人只有在那颗数旁边看到另一颗同类数，才会知道有两问。
所以这一刀加的不是措辞而是**第二个数**：`usable` = 行里 `content_ok=true` 的条数，
由行本身推出（不可能与行不一致），并在 `summary` 里把两个数各答什么问题一次。
"""

from __future__ import annotations

from dhole_mcp.models import BulkResponseModel, ResponseModel
from dhole_mcp.wire import _WIRE_OMIT


def _row(url: str, *, status: int = 200, content_ok: bool = True,
         error: str = "") -> ResponseModel:
    return ResponseModel(url=url, status=status, content_ok=content_ok,
                         content=["text"] if content_ok else [],
                         fetcher_used="http", error=error)


def _dump(**kw):
    return BulkResponseModel(**kw).model_dump(mode="json")


def test_the_second_count_is_derived_from_the_rows_not_from_the_caller():
    rows = [_row("http://a/", content_ok=True),
            _row("http://b/", content_ok=False),          # 200 但是登录墙
            _row("http://c/", status=404, content_ok=False, error="http_error_404: x"),
            _row("http://d/", content_ok=False)]          # 正文被截成空
    data = _dump(results=rows, total=4, successful=3)
    assert data["successful"] == 3, "第一颗数不许被顺手动掉（它有自己的定义）"
    assert data["usable"] == 1, data["usable"]


def test_the_envelope_says_which_question_each_number_answers():
    rows = [_row("http://a/"), _row("http://b/", content_ok=False),
            _row("http://c/", content_ok=False), _row("http://d/", content_ok=False)]
    text = _dump(results=rows, total=4, successful=3)["summary"]
    assert "successful" in text and "content_ok" in text, text
    assert "3" in text and "1" in text, text
    assert "different questions" in text, text


def test_a_lying_usable_value_is_overwritten_by_the_rows():
    """两颗数只能有一个作者：行。调用方（或将来某条路径）填错了也必须被纠正。"""
    rows = [_row("http://a/"), _row("http://b/", content_ok=False)]
    data = _dump(results=rows, total=2, successful=2, usable=99)
    assert data["usable"] == 1


def test_every_row_usable_still_reports_both_counts():
    rows = [_row("http://a/"), _row("http://b/")]
    data = _dump(results=rows, total=2, successful=2)
    assert data["usable"] == 2
    assert "2" in data["summary"]


def test_a_refused_batch_says_zero_usable():
    """批量与 attach 不兼容那条路（R01-031 的兄弟现场）：交回的是拒绝行，
    一颗可用的都没有 —— 这一格必须是 0，而不是缺席。"""
    rows = [_row("http://a/", content_ok=False, error="invalid_request: x")]
    data = _dump(results=rows, total=1, successful=0)
    assert data["usable"] == 0 and data["successful"] == 0, data


def test_both_fields_are_declared_and_never_trimmed_off_the_wire():
    """`响应内无定义` 的另一半修复在 schema：两颗数都得有说明，
    而且不许被上线裁剪当成默认值省掉（那正是"看不见"的来源）。"""
    fields = BulkResponseModel.model_fields
    assert "content_ok" in fields["usable"].description
    assert "status<400" in fields["successful"].description
    omit = _WIRE_OMIT.get("BulkResponseModel") or frozenset()
    assert "usable" not in omit and "summary" not in omit, omit
