r"""一次降级只有活在 metadata 第 14 行，就等于没说过。

实测形状（2026-10-05 那批，以及本次 15 场景复跑）：正文其实来自 Internet Archive 某年
的副本，而回执的头一行写的是 `200 OK · 12.4KB markdown · http`。三条证据分别在
`source='archive.org'`、`archived_at='2023-04-15'`、`escalation_path='http→stealthy→
archive.org'` 里 —— 三个字段都说了，**结论位一个字没提**。调用方扫 summary，然后拿一份
2023 年的副本当今天的现状引用。

这一刀改三件，顺序按价值：

1. **summary 里说。** `source` + `archived_at` + `escalation_path` 拼进 parts —— 这是
   **显示**改动，不是新字段，零 schema 成本（连接期一个字符不涨，`_TOOL_DEFS` 不动）。
2. **别把引用地址改成副本地址。** `canonical` 是调用方要缓存与引用的东西。快照页若带
   一条相对 canonical，`urljoin` 会把它补到 `web.archive.org` 名下 —— 那是 URL 解析的
   副产品，不是原站声明的规范地址。删掉它**并具名**（静默删一个引用字段是本批要消灭的那
   种静默），同时把真实快照地址留在 `metadata.snapshot_url`：`snap_resp.url = url` 那一行
   改写了地址，副本的出处不能跟着一起消失。
3. **快照自己的失败要给名字，不要整句抹掉。** 原来那句 `snap_resp.error = ""` 把硬拦截
   从 `_agent_hints` 唯一咨询的那个字段里抹掉了；原样留着又会把一次确实答出来的成功说成
   失败。所以：`metadata.snapshot_error` 记下它，判决位留空。

`/longaside` 那类"两条路径两种真相"是同一个病灶的另一处（R2 已修）：**同一个事实不许
有两个作者，也不许只有一个作者看得见。**
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _agent_hints
from dhole_mcp.server import MasterFetchServer

PUBLIC = "https://example.com/report"
SNAPSHOT = "https://web.archive.org/web/20230415120000/https://example.com/report"


def _archived(**updates) -> ResponseModel:
    """一封真的来自档案档的回执（字段形状照 `_fetch_from_archive` 那一步之后）。"""
    out = ResponseModel(status=200, content=["the report, as it read in 2023"] * 20,
                        url=PUBLIC, fetcher_used="http", source="archive.org",
                        archived_at="2023-04-15",
                        escalation_path="http→stealthy→archive.org",
                        total_extracted_chars=1_200, content_type="text/html")
    return out.model_copy(update=updates)


class TestTheArchiveAnswerSpeaksInTheConclusion:

    def test_the_one_line_an_agent_scans_names_the_copy_and_its_date(self):
        summary, _next, ok = _agent_hints(_archived())
        assert "archived copy" in summary, summary
        assert "2023-04-15" in summary, summary
        assert "http→stealthy→archive.org" in summary, summary
        assert ok is True, "档案档确实答出来了 —— 说它是降级不等于说它失败"

    def test_a_live_answer_says_nothing_about_archives(self):
        """反向：不降级的时候不许有那句 —— 否则那句话就不再是信号。"""
        summary, _next, _ok = _agent_hints(_archived(source="live", archived_at="",
                                                     escalation_path=""))
        assert "archived" not in summary, summary

    def test_an_undated_snapshot_does_not_become_an_empty_comma(self):
        """日期缺失要说出来。拼接不能造出 `archived copy, ` 那种**看起来说过**的半句。"""
        summary, _next, _ok = _agent_hints(_archived(archived_at=""))
        assert "archived copy, date unknown" in summary, summary

    def test_the_route_clause_only_appears_when_a_route_was_walked(self):
        summary, next_action, _ok = _agent_hints(_archived(escalation_path=""))
        assert "archived copy, 2023-04-15" in summary, summary
        assert "(via " not in summary, summary
        assert next_action is not None  # 只是把三样拼起来，没有顺手改别的字段


class _Wayback:
    """把 `_fetch_from_archive` 的两跳都接在本地：availability API + 快照那一跳。"""

    def __init__(self, monkeypatch, snapshot_response: ResponseModel):
        body = json.dumps({"archived_snapshots": {"closest": {
            "url": SNAPSHOT, "available": True, "timestamp": "20230415120000"}}})

        async def fake_http_get(url, timeout=10):
            return SimpleNamespace(status=200, body=body)

        async def fake_get(self, url, **kwargs):
            return snapshot_response.model_copy(update={"url": url})

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", fake_http_get)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)


def _snapshot(content=("body", "of the archived page"), **updates) -> ResponseModel:
    return ResponseModel(status=200, content=list(content), url=SNAPSHOT,
                         fetcher_used="http", content_type="text/html",
                         total_extracted_chars=sum(len(c) for c in content),
                         **updates)


def _take_down(monkeypatch, snapshot: ResponseModel):
    server = MasterFetchServer()
    _Wayback(monkeypatch, snapshot)
    with _arg_notes_scope() as notes:
        out = asyncio.run(server._fetch_from_archive(PUBLIC, "markdown", None, 0, 1000))
    return out, notes


class TestTheSnapshotProvenanceSurvivesTheUrlRewrite:

    def test_the_snapshot_url_is_kept_while_the_answer_is_named_by_the_origin(self,
                                                                             monkeypatch):
        out, _notes = _take_down(monkeypatch, _snapshot())
        assert out is not None
        assert out.url == PUBLIC, "调用方问的是原站，`url` 不能改成副本地址"
        assert out.metadata.get("snapshot_url") == SNAPSHOT, out.metadata
        assert out.source == "archive.org"
        assert out.archived_at == "2023-04-15", out.archived_at

    def test_a_canonical_pointing_at_the_origin_is_kept(self, monkeypatch):
        """不能过度开火：原站自己声明的 canonical 就是要引用的东西。"""
        snap = _snapshot(metadata={"canonical": "https://example.com/report/"})
        out, notes = _take_down(monkeypatch, snap)
        assert out.metadata.get("canonical") == "https://example.com/report/", out.metadata
        assert not any("canonical" in n for n in notes), notes

    def test_a_canonical_that_resolves_to_the_snapshot_host_is_dropped_and_named(
            self, monkeypatch):
        """相对 canonical 在副本主机上解析出来的那个 URL，没有任何人发布过它。"""
        bogus = "https://web.archive.org/report"
        snap = _snapshot(metadata={"canonical": bogus, "title": "The report"})
        out, notes = _take_down(monkeypatch, snap)
        assert "canonical" not in out.metadata, out.metadata
        assert out.metadata.get("title") == "The report", "只删那一颗，别把整张 metadata 扫掉"
        assert any(bogus in n and "snapshot host" in n for n in notes), notes


class TestTheSnapshotOwnFailureIsNamedNotErased:

    def test_a_hard_block_on_the_snapshot_does_not_disappear_with_the_wipe(self,
                                                                          monkeypatch):
        """快照答出来了但带着自己的失败：记下名字，判决位留空。

        摘掉修复（改回 `snap_resp.error = ""`）这一条就红 —— 那句话没有任何地方可以再
        被读到，而这正是 `snap_resp.error = ""` 那行注释（"clear any error from the HTTP
        fetch"）曾经做到过的事。
        """
        blocked = _snapshot(error="auth_required: the snapshot itself is behind a login")
        out, _notes = _take_down(monkeypatch, blocked)
        assert out is not None
        assert out.error == "", out.error
        assert out.metadata.get("snapshot_error") == (
            "auth_required: the snapshot itself is behind a login"), out.metadata

    def test_a_clean_snapshot_leaves_no_error_trace_behind(self, monkeypatch):
        """没有失败时不许凭空造一个 `snapshot_error` 键 —— 缺席就是没说。"""
        out, _notes = _take_down(monkeypatch, _snapshot())
        assert "snapshot_error" not in out.metadata, out.metadata

    def test_the_conclusion_still_carries_the_demotion_after_all_of_it(self, monkeypatch):
        """三件事叠在一起时，summary 那句话仍然在场（字段之间不许互相吞）。"""
        snap = _snapshot(metadata={"canonical": "https://web.archive.org/report"})
        out, _notes = _take_down(monkeypatch, snap)
        summary, _next, ok = _agent_hints(out)
        assert "archived copy, 2023-04-15" in summary, summary
        assert ok is True, (summary, out.error)
