"""test_server_8.py —— 由 7 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import inspect
import json
from dhole_mcp import actions as actions_mod
from dhole_mcp import browser as browser_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, _min_visual_call_ms
import os
import pytest
import ast
import pathlib
import subprocess
import sys
from dhole_mcp.server import ResponseModel
from dhole_mcp import server as SM
from dhole_mcp.server import BULK_TOTAL_CONTENT_CHARS, DEFAULT_MAX_CONTENT_CHARS
import re
from dhole_mcp.server import _blocked_path_prefix
from dhole_mcp.server import _AUTH_SHAPES, _auth_request_headers, _identity_value_error


# ============================================================================
# 来源：test_a_budget_too_small_for_a_screenshot_is_named_before_it_is_spent.py
# ============================================================================
# 装不下一张截图的预算，要在开始之前点名拒绝，而不是把它吃掉。

def test_the_floor_is_a_sum_of_its_owners_not_a_magic_number():
    floor = _min_visual_call_ms()
    assert floor == (browser_mod.BROWSER_LAUNCH_TYPICAL_MS
                     + browser_mod.SETTLE_IDLE_MS
                     + actions_mod.STEP_WAIT_MS), floor
    assert floor == 20_000, "加数被改了而这个数没跟上：三处属主里有人动了手"


def test_the_floor_sits_under_the_budget_a_caller_who_said_nothing_gets():
    """没点名预算的调用拿的是默认那一笔，它在门槛之上 —— 所以这一支永远不会误伤谁。"""
    assert server_mod.ACTIONS_DEFAULT_TIMEOUT_MS > _min_visual_call_ms(), (
        "默认预算掉到门槛之下：那么每一次默认的动作链截图都会被当场拒掉，"
        "而调用方一个数都没改过 —— 用一颗他没给过的数去拒他，是新造的一种谎")


def test_the_refusal_is_decided_before_any_browser_is_started():
    src = inspect.getsource(MasterFetchServer.web_read)
    raise_at = src.index("a screenshot needs the page's real resources")
    # 见证人要挑真的那一处：注释里也出现 `_force_fetch`（"它自己的默认值"那句），
    # 拿它当"开始了吗"的依据会一直绿到某天顺序真被换掉。`await self._within_call_budget(`
    # 是浏览器档那一条路的第一道门，它后面才是起进程。
    starts_at = src.index("await self._within_call_budget(")
    assert raise_at < starts_at, (
        "拒绝排在了起浏览器后面：那笔预算已经花掉了，这句拒绝就只是事后诸葛")


def test_the_way_out_names_only_doors_this_tool_actually_takes():
    """期 1.5 那一条判据用在自己身上：出路里的 `timeout=` 得是 `web_act` 收的那一颗。"""
    sentence = inspect.getsource(MasterFetchServer.web_read)
    take = sentence[sentence.index("Two doors:"):]
    assert "timeout=" in take, take[:120]
    for tool in ("web_act", "web_read"):
        owned = server_mod._TOP_LEVEL_ARGS[tool]
        assert "timeout" in owned, (tool, sorted(owned))
        assert "screenshot" not in owned, (
            "那一步是 `actions` 载荷里的键，不是顶层参数 —— 话里也不许把它写成顶层")


def test_the_refusal_reaches_the_protocol_as_a_designed_refusal():
    """`ArgumentRefusal` 而不是裸 ValueError：裸的那一种会从 transport 兜底裸逃（刀1）。"""
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("a screenshot needs the page's real resources")
    head = src[max(0, at - 400):at]
    assert "raise ArgumentRefusal(" in head, head[-200:]


def test_a_call_that_named_nothing_is_not_refused_for_a_number_it_never_gave():
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("actions_need_resources = actions_want_visual(actions)")
    window = src[at:at + 400]
    assert "timeout is not None" in window, window[:300]


def test_the_cause_sentence_still_tells_which_action_needs_the_resources():
    """截图是唯一"产物就是渲染出来的页面"的动作 —— 那句话得说的是这一件，不是泛泛的慢。"""
    assert "the only action whose" in inspect.getsource(MasterFetchServer.web_read)
    doc = inspect.getdoc(actions_mod.actions_want_visual) or ""
    assert "product IS the rendered page" in doc, doc[:120]


def test_the_idle_cap_and_the_launch_estimate_are_public_because_two_modules_read_them():
    """私有名下跨模块读会逼出第二次抄写 —— 这两颗都有了第二个读者，所以是公开名。"""
    for name in ("SETTLE_IDLE_MS", "BROWSER_LAUNCH_TYPICAL_MS"):
        assert hasattr(browser_mod, name), name
        assert not name.startswith("_")
    assert hasattr(actions_mod, "STEP_WAIT_MS")
    # 加法只在一处：browser/actions 都不许**定义**这个下限（注释里指名它读给谁看是好事，
    # 再算一遍才是第二个作者）
    assert "def _min_visual_call_ms" not in inspect.getsource(browser_mod)
    assert "def _min_visual_call_ms" not in inspect.getsource(actions_mod)


def test_the_refusal_says_nothing_was_sent():
    """一支 0 秒的拒绝必须说明"什么都没发生"，否则读的人不知道页面被碰过没有。"""
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("a screenshot needs the page's real resources")
    window = src[at:at + 900]
    assert "Nothing was fetched and no browser was started" in window, window[:220]


# ─── 协议面：agent 拿到的那一封信 ────────────────────────────────

async def _through_the_real_sdk(tool: str, args: dict) -> dict:
    """真握手：InMemoryTransport 包住的正是 stdio/HTTP 跑的同一个 handler。

    robots 那一道预检会真的去取一次 robots.txt，而这一支的拒绝发生在它**之后**、起浏览器
    之前 —— 所以用例把 robots 关掉（这颗开关是配置面现有的），让这条测试保持零网络。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


def test_the_agent_sees_an_envelope_not_a_naked_transport_error(monkeypatch):
    """拒绝在 `_dispatch` 抛，折成信封的是 `call_tool` —— agent 那一侧必须是齐全的信封。

    裸的那一种只有 `{"error": str(e)[:300]}`，而 300 个字符剪掉的正是出路那半句（刀1 与
    批次 4 的同一族）。这里断的是**这一次新加的句子**走完整条边界，不是复用别人的结论。
    """
    monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "1")
    payload = asyncio.run(_through_the_real_sdk(
        "web_act", {"url": "https://example.com/checkout",
                    "actions": [{"screenshot": {"selector": "#qty"}}],
                    "timeout": 3_000}))
    assert payload.get("error", "").startswith("invalid_request"), payload
    assert payload.get("content_ok") is False, payload
    assert str(_min_visual_call_ms() // 1000) in (
        str(payload["error"]) + str(payload.get("next_action"))), payload
    assert "timeout=" in payload["next_action"], payload["next_action"][:200]
    assert "Nothing was fetched and no browser was started" in payload["next_action"]
    assert payload.get("summary"), "拒绝不许到达时没有 summary"


def test_a_budget_above_the_floor_still_runs_the_chain():
    """反面守卫：门槛之上这一支必须完全不出声，否则它就变成新的截图许可门。

    真跑一次截图要起浏览器（活半边在 `evals/verify_fixes.py` 的 D1 —— 那条走的就是**没点名
    timeout** 的路径，误伤会在那一条上红）。这里守的是代码形状：那一支只在两个条件同时成立
    时才存在，`timeout is None` 这一支根本不看下限。
    """
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("if int(timeout) < floor:")
    guard = src[src.index("if actions_need_resources and timeout is not None"):at + 40]
    assert "timeout is not None" in guard and "int(timeout) < floor" in guard, guard


def test_no_wire_face_moved_while_this_landed():
    """这一刀全在响应侧与拒绝话术里：常驻层一个字都没加（棘轮的余量是 0）。"""
    import json
    from pathlib import Path
    lock = json.loads((Path(__file__).resolve().parents[1] / "tests"
                       / "tool_defs_lock.json").read_text(encoding="utf-8"))
    assert lock["tools"] == 13
    assert "screenshot" not in str(
        [p for d in MasterFetchServer._TOOL_DEFS
         for p in d["inputSchema"]["properties"]])


# ============================================================================
# 来源：test_an_absolute_path_is_not_advised_to_become_absolute.py
# ============================================================================
# R02-062 · 已经写成绝对形式的输入，不该再被建议「换成绝对形式」。

ADVICE = "pass an absolute path"


def _parse(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _all_text(r) -> str:
    """整封回执里会被 agent 读到的散文（判决字段除外都是文案）。"""
    diag = r.diagnosis or {}
    return " ".join(x or "" for x in (r.error, r.next_action, r.summary,
                                      diag.get("recover_with"), diag.get("why")))


def _relative_name(tmp_path) -> str:
    """一颗相对写法的名（不在任何根里），让 server 走「按几个根试过」那一支。"""
    name = "r02-062-gone.txt"
    for root in (os.getcwd(), os.path.expanduser("~")):
        if os.path.exists(os.path.join(root, name)):
            pytest.fail(f"测试环境里 {root} 下已经有 {name}，换一颗名字再跑")
    return name


# ── 1 · 绝对那一发：不许再教人换绝对形式 ────────────────────────────────────

def test_an_absolute_path_is_not_told_to_become_absolute(tmp_path):
    gone = str(tmp_path / "nope-absolute.txt")
    assert os.path.isabs(gone), "这一格的前提是传进去的就是绝对形式"

    r = _parse(file_path=gone)

    assert r.error.startswith("File not found"), r.error
    assert ADVICE not in r.error.lower(), f"已经是绝对形式还被建议换绝对：{r.error[:220]}"
    assert ADVICE not in (r.next_action or "").lower(), r.next_action
    assert ADVICE not in ((r.diagnosis or {}).get("recover_with") or "").lower(), r.diagnosis


def test_the_absolute_answer_names_the_door_that_actually_moves(tmp_path):
    """出路要说得出**这一发**还有什么可动：名字本身，或点名目录 + 里面的文件。"""
    r = _parse(file_path=str(tmp_path / "nope-absolute.txt"))

    said = r.error.lower()
    assert "cwd" in said, f"没交代 `cwd=` 在这一发上的作用：{r.error[:240]}"
    assert "ignored" in said or "cannot" in said or "does not" in said, (
        f"那句关于 `cwd=` 的话没有把『不生效』说出口：{r.error[:240]}")
    assert "name" in said, f"没点名真正那扇门（名字/位置）：{r.error[:240]}"


def test_the_shape_judgment_is_not_a_path_special_case(tmp_path):
    """同一形状换拼法（反斜杠 / 正斜杠、另一颗盘符式）走同一支 —— 判据只有形状这一维。"""
    a = _parse(file_path=str(tmp_path / "one.txt"))
    b = _parse(file_path=(tmp_path / "two.txt").as_posix())

    for r in (a, b):
        assert ADVICE not in r.error.lower(), r.error[:200]
    # 两句同形：只差在被找的那一处，出路部分必须一致
    assert a.error.split(". ")[-1] == b.error.split(". ")[-1], (a.error, b.error)


# ── 2 · 相对那一发：一个字都不许放松 ────────────────────────────────────────

def test_a_relative_path_still_gets_both_doors_verbatim(tmp_path):
    r = _parse(file_path=_relative_name(tmp_path))

    assert r.error.startswith("File not found"), r.error
    assert "Pass an absolute path, or pass cwd=<working directory> so relative paths " \
           "resolve against it." in r.error, r.error
    assert r.error.lower().count(ADVICE) == 1, r.error


# ── 3 · 一位作者：出路在整封回执里只说一遍 ─────────────────────────────────

def test_the_way_out_is_written_by_one_author_not_three(tmp_path):
    """相对那一发：两句复述从三个抄本收成一位作者（指回 `error` 不算复述）。"""
    rel = _parse(file_path=_relative_name(tmp_path))
    assert _all_text(rel).lower().count(ADVICE) == 1, _all_text(rel)[:600]

    ab = _parse(file_path=str(tmp_path / "nope.txt"))
    assert _all_text(ab).lower().count(ADVICE) == 0, _all_text(ab)[:600]


def test_downstream_points_at_error_instead_of_guessing_the_shape(tmp_path):
    """下游两处不复述出路，但必须把读者送去那一句真的说对了的地方。"""
    for kw in ({"file_path": str(tmp_path / "nope.txt")},
               {"file_path": _relative_name(tmp_path)}):
        r = _parse(**kw)
        assert "error" in (r.next_action or "").lower(), r.next_action
        assert "error" in ((r.diagnosis or {}).get("recover_with") or "").lower(), r.diagnosis


# ── 4 · 判决与形状不许被文案带走 ────────────────────────────────────────────

def test_the_verdict_and_the_tried_places_survive_the_wording(tmp_path):
    ab = _parse(file_path=str(tmp_path / "nope-absolute.txt"))
    rel = _parse(file_path=_relative_name(tmp_path))

    for r in (ab, rel):
        assert r.status == 0 and not r.content, r
        assert r.content_ok is False, r
        assert (r.diagnosis or {}).get("code") == "file_not_found", r.diagnosis
        assert "nope-absolute.txt" in r.error or "r02-062-gone.txt" in r.error, r.error

    # 绝对那一发只在一处找过；相对那一发按几个根试过 —— 这个差别不许被抹平
    # （读「被找过的哪几处」那一段，不数整句里的逗号 —— 文案会改，形状不会）
    ab_tried = ab.error.split(". ")[0]
    rel_tried = rel.error.split(". ")[0]
    assert ", " not in ab_tried, ab_tried
    assert ", " in rel_tried, rel_tried


# ── 5 · 手册要说得出这件事（B-16 那一族：字段有话，手册没册）─────────────────

def test_the_handbook_names_the_two_shapes_and_the_one_author():
    """`tools.md` 的 doc_parse 那一节要说得出：两种形状两种出路，而出路只有一位作者。"""
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills"
            / "dhole-web" / "references" / "tools.md").read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines()
             if "genuinely missing file keeps its own sentence" in ln]
    assert len(lines) == 1, f"那段话丢了或被拆成多行：{len(lines)}"
    said = lines[0]
    assert "already absolute" in said and "ignored for an absolute path" in said, said[-700:]
    assert "one author" in said, said[-700:]
    # 手册不许把自己写成第二个抄本：它引用相对那一句是「说明这一发听到什么」，
    # 但绝对那一支必须说得出「两句出路都不给」，否则读者仍旧去找那句自相矛盾的话
    assert "gets neither" in said, said[-700:]


# ============================================================================
# 来源：test_context_budget.py
# ============================================================================
# Token-spend knobs: the default content budget, and the ceiling's identity.

WAY_OVER = 5000  # chars: longer than any budget under test


def _stub_fetch(monkeypatch, body: str) -> None:
    """Replace the fetch tiers with one canned body.

    The stub passes the budget it RECEIVES to the real ``_apply_chunking``, so the
    test observes the value ``web_read`` computed from the default — stubbing
    the tool method instead would test the stub rather than the wiring.

    It reads that budget by PARAMETER NAME rather than by position. A positional
    read is what let G14 ship: the schema branch dropped one argument and every
    later slot moved up, and a stub that just takes ``*a`` cannot notice. Binding
    against the real signature notices, because the name has to exist.
    """
    import inspect

    sig = inspect.signature(server_mod.MasterFetchServer._auto_escalate)
    result = ResponseModel(url="https://example.com/", status=200, content=[body])

    async def fake(self, *a, **kw):
        bound = sig.bind(self, *a, **kw).arguments
        return server_mod._apply_chunking(
            result, max_chars=bound.get("max_chars", server_mod.DEFAULT_MAX_CONTENT_CHARS))

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake)


def _fetch(args: dict) -> dict:
    """Run one web_read through the real dispatcher; return the envelope."""
    _content, structured = asyncio.run(
        server_mod.MasterFetchServer(cache_ttl=0)._dispatch("web_read", args))
    return structured


class TestTheDefaultBudgetIsWired:

    def test_an_omitted_budget_uses_the_configured_default(self, monkeypatch):
        monkeypatch.setattr(server_mod, "DEFAULT_MAX_CONTENT_CHARS", 1234)
        _stub_fetch(monkeypatch, "B" * WAY_OVER)
        payload = _fetch({"url": "https://example.com/", "cache_ttl": 0})
        assert payload["total_extracted_chars"] == WAY_OVER
        assert payload["is_truncated"] is True
        assert payload["next_offset"] == 1234, "截断点必须是本次生效的默认预算"

    def test_an_explicit_budget_still_wins(self, monkeypatch):
        """The knob is a DEFAULT, not a cap. An explicit argument runs the show —
        that is what keeps it a spend knob instead of a capability cut."""
        monkeypatch.setattr(server_mod, "DEFAULT_MAX_CONTENT_CHARS", 1234)
        _stub_fetch(monkeypatch, "B" * WAY_OVER)
        payload = _fetch({"url": "https://example.com/", "cache_ttl": 0,
                          "max_content_chars": 2000})
        assert payload["next_offset"] == 2000

    def test_the_ceiling_is_still_askable(self, monkeypatch):
        """200000 remains available: a lowered default may not quietly become a
        hard cap."""
        monkeypatch.setattr(server_mod, "DEFAULT_MAX_CONTENT_CHARS", 1234)
        _stub_fetch(monkeypatch, "B" * 200)
        payload = _fetch({"url": "https://example.com/", "cache_ttl": 0,
                          "max_content_chars": 200000})
        assert not payload.get("is_truncated", False)

    def test_a_truncated_default_still_names_the_way_back(self, monkeypatch):
        """Pagination is what makes the lower default lossless, so the response
        must keep pointing at it."""
        monkeypatch.setattr(server_mod, "DEFAULT_MAX_CONTENT_CHARS", 1234)
        _stub_fetch(monkeypatch, "B" * WAY_OVER)
        payload = _fetch({"url": "https://example.com/", "cache_ttl": 0})
        assert "offset=1234" in payload["next_action"]


def _constant_under_env(value: str) -> int:
    """Read DEFAULT_MAX_CONTENT_CHARS from a fresh interpreter.

    It is an import-time constant (like DHOLE_BROWSER_IDLE_TIMEOUT), which is how
    a client sets it: an ``env`` entry in the mcpServers config.
    """
    env = {**os.environ, "DHOLE_DEFAULT_CONTENT_CHARS": value}
    out = subprocess.run(
        [sys.executable, "-c",
         "import dhole_mcp.server as s; print(s.DEFAULT_MAX_CONTENT_CHARS)"],
        capture_output=True, text=True, env=env, timeout=180)
    assert out.returncode == 0, out.stderr[-600:]
    return int(out.stdout.strip())


@pytest.mark.parametrize("value,expected", [
    ("", 40000),          # unset / empty -> the shipped default
    ("abc", 40000),       # unparseable -> the shipped default, never a crash
    ("8000", 8000),
    ("1", 500),           # below the documented floor -> clamped
    ("999999", 200000),   # above the documented ceiling -> clamped
])
def test_the_env_var_sets_the_default_within_the_documented_range(value, expected):
    assert _constant_under_env(value) == expected


class TestTheCeilingIsNeverUsedAsADefault:
    """``MAX_CONTENT_CHARS`` is the clamp bound (a capability);
    ``DEFAULT_MAX_CONTENT_CHARS`` is the per-call spend knob.

    Swapping one for the other is silent: nothing fails, the call just spends 5x,
    and the knob is dead on that path. It is read from the SOURCE rather than
    from the live signatures, because with no env var set the two constants are
    the same object — ``param.default is DEFAULT_MAX_CONTENT_CHARS`` cannot tell
    them apart until someone configures the knob, which is exactly when the
    mistake would already be in production.
    """

    SERVER_SRC = (pathlib.Path(__file__).resolve().parents[1]
                  / "src" / "dhole_mcp" / "server.py")

    def test_no_budget_default_is_the_clamp_ceiling(self):
        tree = ast.parse(self.SERVER_SRC.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for default in [*node.args.defaults, *node.args.kw_defaults]:
                if isinstance(default, ast.Name) and default.id == "MAX_CONTENT_CHARS":
                    offenders.append(f"{node.name}(): max_chars 参数默认值")
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Name)
                        and sub.func.id == "_coerce_int_arg"):
                    continue
                for kw in sub.keywords:
                    if (kw.arg == "default" and isinstance(kw.value, ast.Name)
                            and kw.value.id == "MAX_CONTENT_CHARS"):
                        offenders.append(f"{node.name}(): _coerce_int_arg(default=...)")
        assert not offenders, (
            f"MAX_CONTENT_CHARS（钳制上限）被当成默认预算用了：{offenders}。"
            f"默认预算必须是 DEFAULT_MAX_CONTENT_CHARS，否则 "
            f"DHOLE_DEFAULT_CONTENT_CHARS 在这些路径上静默失效。"
        )


# ============================================================================
# 来源：test_a_batch_of_urls_shares_one_return_budget.py
# ============================================================================
# 验收刀9-5（F4）：一批 URL 共用一次返回预算，这笔账从前没人算过。

BODY = "Real body text so the extractor has something to chew on. " * 2000
URLS9 = [f"https://site{i}.example.com/post" for i in range(9)]
NEEDLE = "this batch shares one return budget"


def _stub(monkeypatch, *, failing=()):
    """每一页都回一大坨正文：真实的截断发生在 `mc` 那一步，不在抓取层。"""

    async def fake(self, url, **kwargs):
        if any(url.startswith(f) for f in failing):
            return SM.ResponseModel(url=url, status=0, content=[],
                                    fetcher_used="none", error="connection refused")
        return SM.ResponseModel(url=url, status=200, content=[BODY],
                                fetcher_used="http", content_ok=True,
                                metadata={"content_type": "text/html"},
                                total_size_bytes=len(BODY))

    monkeypatch.setattr(MasterFetchServer, "get", fake)


def _call(args):
    out = asyncio.run(MasterFetchServer()._dispatch("web_read", args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _chars(row) -> int:
    return sum(len(c) for c in row.get("content") or [])


@pytest.mark.parametrize("n", [2, 3, 9, 12])
def test_a_batch_splits_one_budget_instead_of_losing_the_tail(monkeypatch, n):
    _stub(monkeypatch)
    urls = URLS9[:n] if n <= 9 else URLS9 + [f"https://extra{i}.example.com/"
                                             for i in range(n - 9)]
    out = _call({"urls": urls, "force_fetcher": "http", "cache_ttl": 0})
    rows = out.get("results") or []
    assert len(rows) == n, f"要了 {n} 条只回 {len(rows)} 条"
    assert all(r["status"] == 200 for r in rows), [r.get("error") for r in rows]
    share = BULK_TOTAL_CONTENT_CHARS // n
    # 页首那几行（标题/出处）是另算的，所以这里量的是"落在那一页份额上"而不是一个
    # 逐字符的等式：这条主张是关于总载荷装不装得下，不是关于 markdown 的长度。
    for r in rows:
        assert abs(_chars(r) - share) <= 500, (_chars(r), share)
    assert sum(_chars(r) for r in rows) <= BULK_TOTAL_CONTENT_CHARS + 500 * n, \
        sum(_chars(r) for r in rows)


def test_every_row_says_it_was_capped_and_how_to_get_more(monkeypatch):
    _stub(monkeypatch)
    out = _call({"urls": URLS9, "force_fetcher": "http", "cache_ttl": 0})
    rows = out["results"]
    per = BULK_TOTAL_CONTENT_CHARS // 9
    for r in rows:
        summary = r.get("summary") or ""
        assert NEEDLE in summary, summary
        assert f"capped at {per} content chars" in summary, summary
        assert "9 x the 40000 default is 360000" in summary, summary
        assert "max_content_chars=" in summary and "read one URL alone" in summary, summary


def test_a_failed_row_is_not_told_it_was_cut(monkeypatch):
    """截断的账只写给真被截过的那一行：给一行没发出去的正文解释预算就是编故事。"""
    _stub(monkeypatch, failing=("https://site3.example.com",))
    out = _call({"urls": URLS9, "force_fetcher": "http", "cache_ttl": 0})
    rows = out["results"]
    dead = [r for r in rows if r["status"] == 0]
    assert len(dead) == 1, dead
    assert all(NEEDLE not in (r.get("summary") or "") for r in dead), dead
    assert all(NEEDLE in (r.get("summary") or "") for r in rows if r["status"] == 200)


def test_one_url_is_left_alone(monkeypatch):
    """单条不压：一次一条本来就用不满那份预算，压它只是把默认变差。"""
    _stub(monkeypatch)
    out = _call({"url": URLS9[0], "force_fetcher": "http", "cache_ttl": 0})
    assert abs(_chars(out) - DEFAULT_MAX_CONTENT_CHARS) <= 500, _chars(out)
    assert NEEDLE not in (out.get("summary") or ""), out.get("summary")


@pytest.mark.parametrize("named", [1500, DEFAULT_MAX_CONTENT_CHARS, 200000])
def test_a_named_budget_is_the_callers_own(monkeypatch, named):
    """点了数就不替他改 —— 哪怕点的是一个会把批次挤出宿主载荷的数。

    这一条不是为那个后果辩护，是把它钉成"显式值赢"的既有规矩：想少花就点名小值，想多花
    就点名大值，两种都不该被一个自动份额拦下来。
    """
    _stub(monkeypatch)
    urls = URLS9 if named <= DEFAULT_MAX_CONTENT_CHARS else URLS9[:2]
    out = _call({"urls": urls, "force_fetcher": "http", "cache_ttl": 0,
                 "max_content_chars": named})
    cap = min(named, 200000)
    for r in out["results"]:
        assert NEEDLE not in (r.get("summary") or ""), r.get("summary")
        if cap <= len(BODY):
            assert abs(_chars(r) - cap) <= 500, (_chars(r), cap)


def test_the_share_grows_when_the_batch_is_small_and_stops_at_the_floor(monkeypatch):
    """份额随页数走，但地板不许跌破 500：一页只剩 500 字符的话这条批读就没有意义了。

    100 是 `urls=` 的上限（`MAX_BULK_URLS`），那正是份额最小的一发 —— 48,000 / 100 = 480
    落在地板以下，所以每一页拿到 500 而不是 480：宁肯总载荷超出预算一点，也不交回一页
    读不出东西的正文。超出那部分是刻意的，且每一行都照样说清了压到了几。

    R01-024 之后这一条的数钉在"这一页自己的字"上：切口退到子句末时退回去的那几个字随
    `next_offset` 走下一窗，所以带内的正文可以少于 500 —— 只要 `chars_snapped_back` 报出
    差额、两者相加正好是 500。原来那句 `>= 500` 一直绿着，是因为 dhole 自己那句
    "[Truncated: …]"（实测 173 字）就挂在正文尾巴上把它垫过了头：地板是被缺陷垫出来的，
    而那一行 `summary` 明明写着 "capped at 500 content chars"。
    """
    _stub(monkeypatch)
    two = _call({"urls": URLS9[:2], "force_fetcher": "http", "cache_ttl": 0})
    nine = _call({"urls": URLS9, "force_fetcher": "http", "cache_ttl": 0})
    assert _chars(two["results"][0]) > _chars(nine["results"][0]), (
        _chars(two["results"][0]), _chars(nine["results"][0]))
    hundred = [f"https://h{i}.example.com/" for i in range(100)]
    out = _call({"urls": hundred, "force_fetcher": "http", "cache_ttl": 0})
    assert len(out["results"]) == 100, out.get("total")
    for r in out["results"]:
        body = _chars(r)
        snap = r.get("chars_snapped_back") or 0
        assert body + snap == 500, (body, snap)
        assert "capped at 500 content chars" in (r.get("summary") or ""), r.get("summary")


# ============================================================================
# 来源：test_the_guard_names_the_room_not_the_machine.py
# ============================================================================
# 受限目录那道门报的是**哪间屋子**，不是这台机器的门牌（2026-10-18 · R02-F107 · R02-126）
# 重名消歧：_call → _call__the_guard_names_the_room_3b35

HOME = os.path.expanduser("~")
CWD = os.getcwd()
USER = (os.environ.get("USERNAME") or os.path.basename(HOME)).strip()


def _hats(x: str) -> tuple:
    """四顶帽子：真反斜杠 / 正斜杠 / 全小写 / 百分号编码（B-80 那一课）。"""
    enc = x.replace("\\", "%5C").replace("/", "%2F").replace(":", "%3A")
    return (x, x.replace("\\", "/"), x.lower(), enc, enc.lower())


NEEDLES = tuple(v for x in (HOME, CWD, USER) for v in _hats(x) if v)


def _raw_root_fields(node, path=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _raw_root_fields(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _raw_root_fields(v, f"{path}[{i}]")
    elif isinstance(node, str):
        if any(n in node for n in NEEDLES):
            out.append(path)
    return out


async def _call__the_guard_names_the_room_3b35(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


@pytest.mark.parametrize("rel,room", [
    (r".ssh\id_ed25519", ".ssh"),
    (r".dhole\cache.db", ".dhole"),
    (r".dhole\usage.jsonl", ".dhole"),
])
@pytest.mark.asyncio
async def test_the_guard_names_the_room_not_this_home(rel, room):
    r"""门照旧拒，但报出来的是名单作者写的那一形：`~\\<room>`，不是这台的家目录。

    格子**不必在这台机器上存在**：门走在存在性检查之前（2026-10-20 CI 实测 —— runner
    上没有 `~\.ssh`，这一发落进 `File not found` 而不是守卫。那一版把"这间屋子被禁"的
    判决交给了这台机器的目录形状：同一颗路径在一台装过 ssh 的机器上拒、在一台干净的
    机器上"没找到"，而"这台没有这个文件"本身就是只有服务器知道的答案）。写侧
    `_resolve_save_to` 一直无条件过名单，读侧补齐同一纪律；r227 第一版那颗不存在的
    `~\.dhole\profiles\p.txt` 当年判不动，今天判得动。
    """
    payload = await _call__the_guard_names_the_room_3b35("doc_parse", {"file_path": os.path.join(HOME, rel)})
    err = str(payload.get("error") or "")
    assert err.startswith("Access denied: path is in a restricted system directory"), err
    assert room in err, err
    assert "~" in err, f"没有报出作者写的那一形: {err[:120]}"
    assert _raw_root_fields(payload) == [], (
        f"这些字段还带着这台机器的根: {_raw_root_fields(payload)}")


def test_the_matcher_itself_returns_the_authored_shape():
    """作者级锁（F93 的按作者钉）：`_blocked_path_prefix` 交回的就是可贴进句子的那一形。"""
    got = _blocked_path_prefix(os.path.realpath(os.path.join(HOME, r".ssh\id_ed25519")))
    assert got, "门被削弱了：家目录下的 .ssh 不再被挡"
    assert _raw_root_fields([got]) == [], got
    assert got.startswith("~"), got


def test_the_writer_side_guard_says_the_same_thing():
    """写侧同一颗病：`_resolve_save_to` 抬的 ValueError 也不许念出这台的门牌。

    句子里**允许**出现调用方自己写的那个目的地（`refusing to write '<他给的路径>'` —— F106 那条
    判决的另一半：请求身份是他说过的话），不许出现的是**服务器自己展开**的那颗名单条目。
    """
    with pytest.raises(ValueError) as got:
        actions_mod._resolve_save_to(os.path.join(HOME, r".ssh\shot.png"), "png")
    msg = str(got.value)
    assert "restricted system directory" in msg, msg
    room = re.search(r"restricted system directory \((.*)\)", msg)
    assert room, msg
    assert ".ssh" in room.group(1), room.group(1)
    assert not any(n in room.group(1) for n in NEEDLES), room.group(1)


@pytest.mark.asyncio
async def test_a_room_outside_this_home_is_still_named_verbatim(tmp_path):
    """不顺手改口：不在家目录里的那些条目，今天怎么报以后还怎么报（没有根可折就不动文案）。"""
    payload = await _call__the_guard_names_the_room_3b35("doc_parse", {
        "file_path": os.path.join(os.environ.get("SystemRoot") or r"C:\Windows",
                                  "System32", "drivers", "etc", "hosts")})
    err = str(payload.get("error") or "")
    assert err.startswith("Access denied: path is in a restricted system directory"), err
    assert "windows" in err.lower(), err
    assert "~" not in err, f"不在家目录里的条目被安了一个家目录符号: {err[:120]}"


@pytest.mark.asyncio
async def test_an_ordinary_path_still_reads(tmp_path):
    """邻座：门没被改宽也没被改窄 —— 名单外的普通文件照旧读得出内容。"""
    p = tmp_path / "room.txt"
    p.write_text("AAA1 BBB2 CCC3 DDD4 EEE5\n" + "z" * 40, encoding="utf-8")
    payload = await _call__the_guard_names_the_room_3b35("doc_parse", {"file_path": str(p)})
    assert not payload.get("error"), payload.get("error")
    assert payload.get("content_ok") is True
    assert "AAA1" in "".join(payload.get("content") or [])


# ============================================================================
# 来源：test_a_refused_credential_describes_its_shape_once.py
# ============================================================================
# 一份被拒的凭据不许把"该长什么样"说两遍（2026-10-08 验收 · D-01）

FAKE = "Bearer dummy-not-a-real-token"
#: 列表里最不像别处会出现的一段 —— 数它出现几次就是数列表被说了几遍。
PROBE = "{type:'bearer',token}"


def _said_once(text: str, where: str) -> None:
    assert text.count(PROBE) == 1, f"{where} 把形状列表说了 {text.count(PROBE)} 遍: {text}"


class TestTheRefusalNamesTheShapeOnce:

    @pytest.mark.parametrize("bad", [
        FAKE,                       # 一个字符串
        42,                         # 一个数
        ["a"],                      # 一个列表
        {"type": FAKE},             # type 里贴了 token（原文不许回显）
        {"typ": "basic"},           # 认不出的键
    ], ids=["string", "int", "list", "token-in-type", "unknown-key"])
    def test_a_rejected_auth_value_is_described_exactly_once(self, bad):
        err = _identity_value_error("auth", bad)
        assert err, "拒了却不说是为什么"
        _said_once(err, "cred_defaults 的 error")

    @pytest.mark.asyncio
    async def test_the_tool_receipt_carries_no_second_copy(self):
        """整条 handler 路径（真实方法调用，值仍是假的）：一份回执一处形状句。"""
        r = await MasterFetchServer().cred_defaults(
            set={"auth": FAKE, "host": "api.example.test"})
        _said_once(r.error, "cred_defaults 回执的 error")
        assert PROBE not in r.next_action, r.next_action
        # 评审 R02-102：这一扇从前两种说法并存（门内写 `Nothing was stored.`、装配层的陌生
        # 参数名那颗听见 `Nothing was fetched or changed.`）。现在整扇说它自己那句
        # `Nothing was stored or forgotten.` —— 这一格守的仍是"拒绝要说这一颗没进库"，
        # 词换成那位唯一作者的句子。
        assert "Nothing was stored or forgotten." in r.next_action, r.next_action

    @pytest.mark.asyncio
    async def test_a_refusal_stores_nothing(self):
        """修文案不能顺手改判据：被拒的这次仍然一颗都没存。"""
        server = MasterFetchServer()
        await server.cred_defaults(set={"auth": FAKE, "host": "api.example.test"})
        r = await server.cred_defaults(show=True)
        assert "api.example.test" not in str(r.rows), r.rows


class TestTheReadPathPointsInsteadOfRepeating:
    """`auth` 在 17.0 已经不从调用参数进来了 —— 它是 cred_defaults 存着的那一份，
    由 `_identity_for` 交回。这里代掉那一步，测的是读路径上的那颗拒门。"""

    @staticmethod
    def _stored_auth(monkeypatch, value):
        real = MasterFetchServer._identity_for

        def patched(self, url, **kw):
            values, note = real(self, url, **kw)
            values = dict(values)
            values["auth"] = value
            return values, note

        monkeypatch.setattr(MasterFetchServer, "_identity_for", patched)

    @pytest.mark.asyncio
    async def test_error_holds_the_shape_and_next_action_holds_the_way_out(self, monkeypatch):
        self._stored_auth(monkeypatch, FAKE)
        r = await MasterFetchServer().web_read(url="https://example.test/")
        assert "invalid auth:" in r.error, r.error
        _said_once(r.error + " " + r.next_action, "web_read 的整份回执")
        assert "No request was made" in r.next_action, r.next_action
        assert "`error` names the shape" in r.next_action, r.next_action
        assert r.status == 0 and not r.content, "形状错必须在任何一次外发之前停住"

    @pytest.mark.asyncio
    async def test_a_kind_specific_refusal_still_says_what_it_needs(self, monkeypatch):
        """只给了 username 的 basic：这一条本来就不需要全表，但也不能凭空少半句。"""
        self._stored_auth(monkeypatch, {"type": "basic", "username": "u"})
        r = await MasterFetchServer().web_read(url="https://example.test/")
        assert "username and password" in r.error, r.error
        assert PROBE not in r.error + r.next_action, r.error
        assert "No request was made" in r.next_action, r.next_action


class TestTheSingleAuthorStillCoversTheCases:

    def test_the_constant_is_the_only_place_the_list_is_written(self):
        import inspect

        import dhole_mcp.server as srv
        src = inspect.getsource(srv)
        assert src.count(PROBE) == 1, "形状列表又有了第二个作者"
        assert _AUTH_SHAPES.count(PROBE) == 1

    def test_every_raise_path_that_needs_the_menu_has_it(self):
        """没有形状句的那几条，都是已经点名的：它说得清自己缺什么。"""
        named_fields = {"type": "basic", "username": "u"}
        with pytest.raises(ValueError) as raised:
            _auth_request_headers(named_fields, None)
        assert "username and password" in str(raised.value)
        with pytest.raises(ValueError) as raised:
            _auth_request_headers({"type": "bearer"}, None)
        assert "non-empty string 'token'" in str(raised.value)
        with pytest.raises(ValueError) as raised:
            _auth_request_headers({"type": "header", "name": "X-Key"}, None)
        assert "non-empty string 'value'" in str(raised.value)

    @pytest.mark.parametrize("bad", [FAKE, {"type": "oauth"}], ids=["string", "bad-kind"])
    def test_the_menu_shows_up_in_the_message_that_fired(self, bad):
        with pytest.raises(ValueError) as raised:
            _auth_request_headers(bad, None)
        _said_once(str(raised.value), "raise 的原文")

    def test_the_refusal_never_echoes_what_it_was_given(self):
        """文案并了作者，安全性质不能跟着并掉：type 原文仍然不进错误面。"""
        err = _identity_value_error("auth", {"type": FAKE})
        assert "dummy-not-a-real-token" not in err, err

    def test_a_valid_shape_is_still_accepted(self):
        """反向锁：这一刀只搬文案，不动判据。"""
        headers, kind, names, ignored = _auth_request_headers(
            {"type": "bearer", "token": "t"}, None)
        assert headers == {"Authorization": "Bearer t"}
        assert (kind, names, ignored) == ("bearer", ("authorization",), None)


# ============================================================================
# 来源：test_a_write_door_never_answers_it_was_only_a_fetch.py
# ============================================================================
# 写的那一扇门不许回答「只是没抓到东西」（R02-084 · P3）。
# 重名消歧：_call → _call__a_write_door_never_answe_09ab

WRITE_URL = "https://write-door.invalid/"
READ_URL = "https://read-door.invalid/"

SENT = "Nothing was sent."
FETCHED = "Nothing was fetched."

#: 四条设计边界各取一颗会撞上的头名（凭据 / UA / 条件头 / 内部管理的头）。
HEADER_CASES = [
    ("cookie", {"Cookie": "sid=abc"}),
    ("user-agent", {"User-Agent": "MyOwn/1.0"}),
    ("if-none-match", {"If-None-Match": '"v1"'}),
    ("host", {"Host": "elsewhere.invalid"}),
]


def _call__a_write_door_never_answe_09ab(tool: str, method: str, headers: dict) -> str:
    """走真门（`_dispatch`）拿 `next_action`，不直接调内部函数：判据要读调用方读得到的那一份。

    地址都是 `.invalid`（RFC 2606 保留，永远连不上）—— 这几格赌的是"门在付费之前就拦"，
    所以最后一格（`host`）撞的是 `validate_headers` 那一支也要在发出任何东西之前返回。
    """
    srv = MasterFetchServer()
    args = {"url": WRITE_URL if tool == "web_request" else READ_URL,
            "headers": headers}
    if tool == "web_request":
        args["method"] = method
        args["confirm"] = True
    out = asyncio.run(srv._dispatch(tool, args))
    payload = out[0][0].text
    import json
    try:
        return (json.loads(payload).get("next_action") or "") + " " + payload
    except Exception:
        return payload


class TestTheWriteDoorSaysSent:

    @pytest.mark.parametrize("name,headers", HEADER_CASES)
    def test_every_header_refusal_on_the_write_door_says_sent(self, name, headers):
        text = _call__a_write_door_never_answe_09ab("web_request", "HEAD", headers)
        assert SENT in text, f"{name} 在写门上没说自己没发出去：{text[:300]}"
        assert FETCHED not in text, (
            f"{name} 在写门上的口径是「没抓到」，而这一扇门的调用方要问的是"
            f"「有没有付过一次请求」：{text[:300]}")


class TestTheReadDoorKeepsItsOwnWord:
    """反面锁：不许为了统一把读的门也说成 sent —— 那一扇可能从缓存答题，压根没发过请求。"""

    @pytest.mark.parametrize("name,headers", HEADER_CASES)
    def test_every_header_refusal_on_the_read_door_says_fetched(self, name, headers):
        text = _call__a_write_door_never_answe_09ab("web_read", "", headers)
        assert FETCHED in text, f"{name} 在读门上不再说 fetched：{text[:300]}"
        assert SENT not in text, (
            f"读的那一扇会说 sent 就等于宣称这一发非付出去不可，"
            f"而它能从缓存答题：{text[:300]}")


class TestTheRuleHasOneAuthor:

    def test_the_two_spellings_come_from_one_author(self):
        """拒绝链上不许有第二个手抄句子口径的地方：正反两面都由同一个助手交回。

        这一格第一版写的是「整份 server.py 里 `"Nothing was sent."` 手抄不许超过两处」——
        量到 3 就以为是漏分岔。读一下那三处才知道判据套错了对象：其中两处是**别条写路径**
        自己的句子（confirm 门与凭据回落，本来就该说 sent），一处是这个助手自己的 docstring
        在引用被修的那句原话。规矩管的是 `headers=` 这条拒绝链，不是整份文件。
        """
        import inspect

        from dhole_mcp import server as server_mod
        from dhole_mcp.server import _nothing_went_out

        for fn in (server_mod._header_door, server_mod._caller_headers):
            src = inspect.getsource(fn)
            for spelling in (SENT, FETCHED):
                assert spelling not in src, (
                    f"{fn.__name__} 里手抄了句子口径 {spelling!r} —— 分岔该只有一位作者")
            assert "_nothing_went_out()" in src, (
                f"{fn.__name__} 没从助手取词，它交出的那句会跟着门乱")
        assert _nothing_went_out() in (SENT, FETCHED), _nothing_went_out()

    def test_a_write_door_never_leaves_a_fetched_word_in_a_header_refusal(self):
        """一般式：把四族头名在写门上各打一遍，回执里出现的每一句「什么都没发生」都得是 sent。"""
        variants = [SENT, FETCHED, "Nothing was fetched or changed."]
        for _name, headers in HEADER_CASES:
            text = _call__a_write_door_never_answe_09ab("web_request", "POST", headers)
            present = [v for v in variants if v in text]
            assert present in ([SENT], []), f"写门上的申报口径 {present}：{text[:240]}"

    def test_the_promise_that_makes_this_a_correctness_matter(self):
        """这一扇门外写着 sent ONCE —— 所以"没发生什么"这句话必须按它的动词说。"""
        srv = MasterFetchServer()
        doc = srv.__doc__ or ""
        found = re.search(r"sent ONCE", doc)
        if not found:  # 承诺也可能住在工具描述里；两处任一处算数
            import inspect
            from dhole_mcp import server as server_mod
            src = inspect.getsource(server_mod)
            assert "sent ONCE" in src or "not retried" in src, "承诺的名字变了，判据要跟着改"
        else:
            assert found.group(0)
