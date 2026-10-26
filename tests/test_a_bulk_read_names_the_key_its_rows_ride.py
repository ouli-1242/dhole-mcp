"""批量读的回执必须**先说出行数组叫什么**（E1，manual + R01 + R02 三现）。

现场：`web_read(urls=[...])` 的行数组键是 `results`，而 `web_crawl` 的叫 `pages`。
调用方按直觉写 `data["pages"]`，第一发脚本就 KeyError；修前这条只在行数组本身里
藏着，summary 与参数说明都不点名。

不把 `results` 改名：那是线上契约，改键会把已经读对的老脚本一次打断。这里修的是
"写之前/读之前看不到名字"——参数说明在 tools/list 里说，summary 在回执里说，
两者都点名 `results`，并把 crawl 的 `pages` 对照写出来。
"""

from __future__ import annotations

from dhole_mcp.models import BulkResponseModel, ResponseModel
from dhole_mcp.server import MasterFetchServer


def _row(i: int) -> ResponseModel:
    return ResponseModel(url=f"https://e.test/{i}", status=200,
                         content=["x"], content_ok=True)


def _summary(rows: list[ResponseModel] | None = None) -> str:
    rows = rows or [_row(1), _row(2)]
    dumped = BulkResponseModel(results=rows, total=len(rows),
                               successful=len(rows)).model_dump(mode="json")
    return dumped["summary"]


def test_the_bulk_summary_names_the_key_the_rows_ride():
    summary = _summary()
    assert "`results`" in summary, summary
    assert "one per URL" in summary, summary


def test_the_bulk_summary_names_the_crawl_key_it_is_often_confused_with():
    """E1 的来路就是 `web_crawl` 的 `pages`：对照写在同一句里，读者不用猜。"""
    summary = _summary()
    assert "`pages`" in summary, summary
    assert "web_crawl" in summary, summary


def test_the_urls_argument_names_the_reply_key_before_the_call():
    """写脚本之前能看到的只有 tools/list：那颗参数说明必须点名。"""
    td = next(t for t in MasterFetchServer._TOOL_DEFS if t["name"] == "web_read")
    desc = td["inputSchema"]["properties"]["urls"]["description"]
    assert "results" in desc, desc


def test_a_caller_written_summary_is_not_overwritten():
    """调用方自己写了 summary 的那一路，这层不许把它的句子换掉。"""
    dumped = BulkResponseModel(results=[_row(1)], total=1, successful=1,
                               summary="custom line").model_dump(mode="json")
    assert dumped["summary"] == "custom line"
