# -*- coding: utf-8 -*-
"""一扇不存在的门也要用设计过的信封说话（评审 R02-130 的 unknown-tool 半边 · F123）。

量到的形状（真机 `review/.runtime/p1/r337_two_faces_pre_fix.json` 与
`r338_boundary_faces.py`/`.json`，两条入口各量一遍、真 SDK 回路）：

    tools/call("no_such_tool_at_all")  ->  is_error=**True**,  {"error": "Unknown tool: …"}
    tools/call("sys_state")（被 DHOLE_TOOLS 关掉）-> is_error=True, {"error": "Tool 'sys_state' is not enabled …"}

同一对出口在别处从来不是这副脸：陌生参数名交的是 `diagnosis` + `next_action` 齐全的信封
（F112 那一把立的形状），而**门名本身**错了的这一发交回的只有一格 `error` —— 没有本边界申报、
没有 summary、没有 code（分类器对大写开头的句子交 `('', False, '')`，所以今天不是"一个错名字"，
是"没有名字"）。`_dispatch` 门口那两发 raise 走的是 `call_tool` 的 transport 兜底，而兜底自己的
注释写着「走到这里说明这不是 dhole 设计过的拒绝」—— 这两发恰恰是设计过的。

第二颗在场的受害者量在同一处：兜底那一刀 `[:300]` 剪掉的正是调用方唯一能照着改的那一段。
r338 现算 —— `Unknown tool: web_resolve. …` 全长 355，被剪掉的是
`"); a bare aliveness probe is web_request(method='HEAD')"`；`web_screenshot` 那条 394 字符，
被剪掉的是替代调用的**参数形状**。这与 刀1（`test_a_designed_refusal_never_leaves_naked_through_the_transport.py`）
抱怨过的同一族，只是这一回发生在门名而不是参数。

修法是那条已经成立的分工：`_dispatch` 照旧**抛**（全仓三处 `raises(ValueError, match="Unknown tool")`
的锁一字不动 —— 抛的是 ValueError 的子类），协议边界认识它并折成信封。信封三件（申报 / summary /
把手）全部走现有装配位：申报由 `context.nothing_went_out()` 在**抛出那一刻**按门取（被关掉的
`web_read` 说的是它自己的动词，不是默认支那句），名字由 `quality` 的两张表给（`unknown_tool` 走
前缀档、`tool_not_enabled` 走子串档），summary 由 `_summary_label` 给。**没有为这一族手搓
`diagnosis={…}`**，也没有把抓取那副 14 格壳借给一扇根本不存在的门。

另记一处修掉的第二作者：`_dispatch` 末尾那支 `else: raise ValueError("Unknown tool: …")` 今天
走不到（11 扇在册门各有分支，r339 现场数过），但它把同一句话又写了一遍 —— 那句话说的其实是
"这扇门在册而这副树没有分支"，是内部错误而不是调用方的错，所以它改说自己的事。
"""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from dhole_mcp import quality, server as server_mod
from dhole_mcp.context import nothing_went_out, tool_scope
from dhole_mcp.server import MasterFetchServer

DEFAULT_WORD = "Nothing was fetched or changed."
SRC = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp"

ROSTER = sorted(server_mod._TOP_LEVEL_ARGS)


async def _wire(name: str, args: dict) -> tuple[str, bool]:
    """真 SDK 回路：InMemoryTransport 包住的正是 stdio/HTTP 跑的同一个 handler。"""
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(name, args)
    return "".join(getattr(b, "text", "") for b in res.content), bool(res.is_error)


def _envelope(name: str, args: dict | None = None) -> dict:
    text, is_error = _run(name, args or {})
    assert not is_error, f"设计过的拒绝不该以 transport 错误出去: {text[:200]}"
    payload = json.loads(text)
    assert isinstance(payload, dict), text[:200]
    return payload


def _run(name: str, args: dict) -> tuple[str, bool]:
    import asyncio

    return asyncio.run(_wire(name, args))


class TestTheUnknownNameStillAnswersWithAnEnvelope:
    """门名错的那一发，与参数错的那一发是同一种事：设计过的拒绝。"""

    def test_it_answers_json_with_the_three_envelope_pieces(self):
        payload = _envelope("no_such_tool_at_all")
        assert payload.get("error", "").startswith("Unknown tool"), payload
        assert payload.get("next_action"), payload
        assert payload.get("summary"), payload
        assert payload.get("diagnosis"), payload

    def test_it_declares_that_nothing_ran_at_the_head_of_the_way_out(self):
        way_out = _envelope("no_such_tool_at_all")["next_action"]
        assert way_out.startswith(DEFAULT_WORD), way_out

    def test_the_declaration_is_spoken_once(self):
        way_out = _envelope("no_such_tool_at_all")["next_action"]
        assert way_out.count(DEFAULT_WORD) == 1, way_out

    def test_the_name_it_gives_is_one_the_tables_author(self):
        payload = _envelope("no_such_tool_at_all")
        assert payload["diagnosis"]["code"] == "unknown_tool", payload
        assert payload["diagnosis"]["retryable"] is False, payload
        # 同一句话在表里也是这个名字：把手不是门自己编的
        assert quality.classify_error_text(payload["error"])[0] == "unknown_tool"

    def test_the_summary_is_the_one_the_label_author_writes(self):
        summary = _envelope("no_such_tool_at_all")["summary"]
        assert summary.startswith("invalid request"), summary

    def test_the_roster_arrives_whole(self):
        error = _envelope("no_such_tool_at_all")["error"]
        for name in ROSTER:
            assert f"'{name}'" in error, f"名册里少了 {name}: {error}"

    @pytest.mark.parametrize("retired", sorted(server_mod._REMOVED_TOOL_HINTS))
    def test_a_retired_name_reaches_the_door_it_moved_to(self, retired):
        """旧名那一发把"新家怎么写"整句带到 —— 兜底那一刀剪掉的正是这一截（r338）。"""
        hint = server_mod._REMOVED_TOOL_HINTS[retired]
        error = _envelope(retired)["error"]
        assert hint in error, f"出路被剪短了:\n  期待: {hint}\n  到场: {error[-220:]}"

    def test_no_door_runs_for_a_name_that_names_no_door(self, monkeypatch):
        ran = []
        for door in ("web_read", "web_search", "sys_state"):
            monkeypatch.setattr(MasterFetchServer, door,
                                _spy(ran, door), raising=False)
        _envelope("no_such_tool_at_all")
        assert ran == [], f"为了报个名字开始跑门了: {ran}"


def _spy(bucket, label):
    async def _fn(*a, **k):
        bucket.append(label)
        return None
    return _fn


class TestTheSwitchedOffDoorIsRefusedByTheSameAuthor:
    """`DHOLE_TOOLS` 关掉的那一扇是同一条边界的另一颗 raise —— 修一颗留一颗就是第二个名字。"""

    @pytest.fixture
    def only_read_enabled(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_ENABLED_TOOLS", frozenset({"web_read"}))

    def test_it_names_the_switch_that_opens_it(self, only_read_enabled):
        payload = _envelope("sys_state", {"show": ["config"]})
        assert "not enabled in this server" in payload["error"], payload
        assert "DHOLE_TOOLS" in payload["error"], payload["error"]

    def test_it_uses_its_own_registered_name(self, only_read_enabled):
        payload = _envelope("sys_state", {"show": ["config"]})
        assert payload["diagnosis"]["code"] == "tool_not_enabled", payload
        assert payload["diagnosis"]["retryable"] is False, payload

    def test_a_switched_off_read_door_still_speaks_its_own_verb(self, only_read_enabled):
        """这一支钉的是"申报在抛出的那一刻按门取"：被关掉的 `web_read` 不许听到默认支那句。

        若在边界现取词（`tool_scope` 早已随异常退出），这扇只会说 `Nothing was fetched or
        changed.` —— 而同一扇 `web_read` 的陌生参数面说的是 `Nothing was fetched.`。
        """
        payload = _envelope("web_search", {"query": "widgets"})
        assert payload["next_action"].startswith("Nothing was searched"), payload["next_action"]

    def test_the_enabled_list_arrives_whole(self, only_read_enabled):
        payload = _envelope("sys_state", {"show": ["config"]})
        assert "'web_read'" in payload["error"], payload["error"]


class TestTheEnvelopeDoesNotBorrowADoorShell:
    """一扇不存在的门不能借任何一扇的壳说话（R02-103 那条法在边界上的样子）。"""

    FETCH_SHELL = ("content", "content_ok", "duration_ms", "fetcher_used",
                   "status", "url", "results", "pages", "feeds")

    @pytest.mark.parametrize("name,arguments", [
        ("no_such_tool_at_all", {}),
        ("web_resolve", {}),
    ])
    def test_a_name_that_named_no_door_carries_no_door_fields(self, name, arguments):
        payload = _envelope(name, arguments)
        borrowed = [k for k in self.FETCH_SHELL if k in payload]
        assert not borrowed, f"借了别人的壳: {borrowed} · {sorted(payload)}"

    def test_the_door_level_param_refusal_keeps_its_own_shape(self):
        """对照面：那一扇的参数面仍是四格 + 把手 —— 两副壳没被我合一副。"""
        payload = _envelope("sys_state", {"not_an_argument": 1})
        assert payload["diagnosis"]["code"] == "invalid_request", payload
        assert set(payload) <= {"message", "error", "next_action", "summary",
                                "diagnosis"}, sorted(payload)


class TestTheOldLocksAndTheFallback:
    """边界认识的这一族不许顺手把兜底掏空。"""

    def test_dispatch_still_raises_a_valueerror_for_a_name_it_does_not_have(self):
        import asyncio

        with pytest.raises(ValueError) as exc:
            asyncio.run(MasterFetchServer()._dispatch("no_such_tool_at_all", {}))
        assert "Unknown tool" in str(exc.value)

    def test_the_disabled_door_keeps_the_head_of_its_sentence(self, monkeypatch):
        import asyncio

        monkeypatch.setattr(server_mod, "_ENABLED_TOOLS", frozenset({"web_read"}))
        with pytest.raises(ValueError) as exc:
            asyncio.run(MasterFetchServer()._dispatch("sys_state", {"show": []}))
        assert str(exc.value).startswith("Tool 'sys_state' is not enabled"), exc.value

    def test_a_real_internal_error_still_uses_the_transport_fallback(self, monkeypatch):
        """兜底还在收它该收的那一类：门里抛出的非设计异常不许被这条分支吞掉。"""
        async def _boom(self, url="", **kw):
            raise RuntimeError("something inside the door broke")

        monkeypatch.setattr(MasterFetchServer, "web_read", _boom, raising=False)
        text, is_error = _run("web_read", {"url": "https://example.com/"})
        assert is_error is True, text[:200]
        assert json.loads(text) == {"error": "something inside the door broke"}

    def test_the_default_word_for_an_unclaimed_name_is_the_table_default(self):
        """跨门那张表对没被认领的门名交默认支 —— 这一支是本把取词的来源，不是新造的话。"""
        with tool_scope("no_such_tool_at_all"):
            assert nothing_went_out() == DEFAULT_WORD


class TestOneAuthorPerSentence:
    """那句名册话只许有一位作者；两颗 code 只许住在表里。"""

    def test_the_roster_sentence_is_written_exactly_once_in_src(self):
        holders = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    text = "".join(v.value for v in node.values
                                   if isinstance(v, ast.Constant)
                                   and isinstance(v.value, str))
                    if text.startswith("Unknown tool:"):
                        holders.append(f"{path.name}:{node.lineno}")
        assert len(holders) == 1, f"那句名册话有 {len(holders)} 位作者: {holders}"

    def test_the_boundary_does_not_hand_make_a_diagnosis(self):
        """按 AST 的关键字实参数，不按源码文本数 —— 注释里提到它不算写它（R02-088 那把尺子）。"""
        import textwrap

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(server_mod.MasterFetchServer._mcp_server)))
        hand_made = [node.lineno for node in ast.walk(tree)
                     if isinstance(node, ast.keyword) and node.arg == "diagnosis"]
        assert not hand_made, f"边界上手搓了把手: {hand_made}"

    @pytest.mark.parametrize("code,table", [
        ("unknown_tool", {row[1] for row in quality._DIAGNOSIS_PREFIXES}),
        ("tool_not_enabled", {row[1] for row in quality._DIAGNOSIS_CONTAINS}),
    ])
    def test_each_new_name_is_registered_in_a_table(self, code, table):
        assert code in table, f"{code} 不在表里，等于门自己编了一个名字"

    def test_both_names_are_findable_in_the_manual(self):
        row = next(line for line in (SRC / "skills" / "dhole-web" / "references" /
                                     "fields.md").read_text(encoding="utf-8").splitlines()
                   if line.startswith("| `diagnosis`"))
        for code in ("unknown_tool", "tool_not_enabled"):
            assert code in row, f"调用方按 code 分支时查不到 {code}"
