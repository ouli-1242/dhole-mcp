"""watch 的 ETag 复验 —— §6 把条件请求从「agent 能传的参数」改成「监控自己的常规」。

钉住四件事，全都是"说得出对错"的语义而不是"跑通了"：

* **版本标记与基线同生同死**：存进 tasks.db 的 etag 描述的必须就是那份基线正文。
  错配的后果不是难看而是谎报 —— 带着 A 版的标记去问，源站答 304，我们却拿它
  当成"B 版没变"的证据。
* **304 是回答，不是失败**：`changed=false`、一个字节正文都不搬、基线不动。
* **源站不给标记时如实退化**：整页重读，不许在回答里假装复验过。
* **失败的抓取既不动基线也不动标记**（与 16.x 那条"故障不写成页面清空了"同一条）。

抓取层本身用假 `web_read` 替掉：这里要测的是 watch 与存储之间的那份约定，
真 HTTP 那一侧（校验子变成请求头、304 不当失败）由 `test_revalidation_g23.py` 钉。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp import tasks
from dhole_mcp.server import MasterFetchServer, ResponseModel


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    tasks.reset_connection()
    yield tmp_path
    tasks.reset_connection()


class _FakeFetch:
    """假抓取：记下这一次带没带校验子，按脚本答复。

    `answers` 是一列 dict：{"etag","last_modified","text","status"}。
    status=304 时按 HTTP 的规矩没有正文，content=[]。
    """

    def __init__(self, answers):
        self.answers = list(answers)
        self.seen: list = []      # 每次调用看到的 _REVALIDATE（None = 没问）
        self.calls = 0

    async def __call__(self, url=None, **kw):
        self.seen.append(dict(server_mod._REVALIDATE.get() or {}) or None)
        self.calls += 1
        a = self.answers[min(self.calls, len(self.answers)) - 1]
        status = a.get("status", 200)
        content = [] if status == 304 else [a.get("text", "")]
        out = ResponseModel(url=url or "", status=status, content=content,
                            content_ok=a.get("content_ok", True),
                            error=a.get("error", ""))
        out._validators = {k: v for k, v in
                           (("etag", a.get("etag", "")),
                            ("last_modified", a.get("last_modified", ""))) if v}
        return out


def _run(coro):
    return asyncio.run(coro)


def _watch(srv, **args):
    return _run(srv.web_watch(**args))


class TestValidatorsRideWithTheBaseline:

    def test_the_first_check_stores_the_markers_it_was_given(self, home, monkeypatch):
        fake = _FakeFetch([{"text": "v1\nline", "etag": '"A"'}])
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        out = _watch(srv, op="add", name="n", url="https://e.test/p")
        assert out.baseline_created is True or out.changed is None, out
        _watch(srv, op="check", name="n")
        w = tasks.get_watch("n")
        assert w["etag"] == '"A"' and w["baseline"].startswith("v1")
        assert fake.seen == [None], "第一次没有基线可对应，不该问'变了吗'"

    def test_a_changed_page_updates_the_markers_too(self, home, monkeypatch):
        """换基线必须同时换标记 —— 否则标记描述的是上一个版本。"""
        fake = _FakeFetch([{"text": "v1", "etag": '"A"'},
                           {"text": "v2", "etag": '"B"'}])
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        _watch(srv, op="add", name="n", url="https://e.test/p")
        _watch(srv, op="check", name="n")          # 建基线（A）
        out = _watch(srv, op="check", name="n")    # 200 + 新正文（B）
        assert out.changed is True, out
        w = tasks.get_watch("n")
        assert w["etag"] == '"B"' and w["baseline"] == "v2"

    def test_a_failure_leaves_both_the_baseline_and_the_markers(self, home, monkeypatch):
        fake = _FakeFetch([{"text": "v1", "etag": '"A"'},
                           {"status": 503, "content_ok": False, "error": "503"}])
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        _watch(srv, op="add", name="n", url="https://e.test/p")
        _watch(srv, op="check", name="n")
        out = _watch(srv, op="check", name="n")
        assert out.error, out
        w = tasks.get_watch("n")
        assert w["baseline"] == "v1" and w["etag"] == '"A"'


class TestTheConditionalQuestion:

    def test_the_second_check_asks_with_the_stored_markers(self, home, monkeypatch):
        fake = _FakeFetch([{"text": "v1", "etag": '"A"', "last_modified": "x"},
                           {"text": "v1", "etag": '"A"'}])
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        _watch(srv, op="add", name="n", url="https://e.test/p")
        _watch(srv, op="check", name="n")          # 建基线
        _watch(srv, op="check", name="n")
        assert fake.seen == [None, {"etag": '"A"', "last_modified": "x"}], fake.seen

    def test_a_304_is_an_answer_not_a_failure(self, home, monkeypatch):
        fake = _FakeFetch([{"text": "v1", "etag": '"A"'}, {"status": 304}])
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        _watch(srv, op="add", name="n", url="https://e.test/p")
        _watch(srv, op="check", name="n")
        out = _watch(srv, op="check", name="n")
        assert out.changed is False and not out.error, out
        assert "304" in (out.message or ""), out.message
        assert out.baseline_at, "基线时间要说得出是哪一版仍然有效"
        w = tasks.get_watch("n")
        assert w["baseline"] == "v1" and w["etag"] == '"A"'
        assert w["check_count"] == 2, "304 是一次检查，不该不计"
        assert w["change_count"] == 0

    def test_no_markers_means_no_question(self, home, monkeypatch):
        """源站不给 etag / last-modified：老老实实整页重读，不许假装复验过。"""
        fake = _FakeFetch([{"text": "v1"}] * 4)
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        _watch(srv, op="add", name="n", url="https://e.test/p")
        _watch(srv, op="check", name="n")
        _watch(srv, op="check", name="n")
        assert fake.seen == [None, None], fake.seen

    def test_retargeting_clears_the_markers_with_the_baseline(self, home, monkeypatch):
        """换靶清基线是既有规矩；标记必须一起清 —— 留着就是把旧站的 etag 问新站。"""
        fake = _FakeFetch([{"text": "v1", "etag": '"A"'}] * 4)
        monkeypatch.setattr(MasterFetchServer, "web_read", fake)
        srv = MasterFetchServer()
        _watch(srv, op="add", name="n", url="https://e.test/p")
        _watch(srv, op="check", name="n")
        assert tasks.get_watch("n")["etag"] == '"A"'
        _watch(srv, op="add", name="n", url="https://e.test/other")
        w = tasks.get_watch("n")
        assert w["baseline"] == "" and w["etag"] == "" and w["last_modified"] == ""
