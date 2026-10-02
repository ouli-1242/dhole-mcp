"""L3 recipe（录制回放）与 L4 watch（监控 diff）—— 计划第九章编排轴的最后两级。

这里钉住的是**语义**，不是"调通了"：

* 录制时用的是执行器**同一套**验证器（`actions.validate_actions`），所以库里
  不可能存在一个执行器会拒绝的配方——存一个永远跑不通的配方比不存更糟；
* watch 的**首跑不是变化**（`changed=None`，`change_count` 不动）——把没有基线
  报成"没有变化"是凭空造一个事实；
* **失败的抓取不动基线**——否则一次网络故障就被写成"页面清空了"；
* 两个工具**默认就在线** —— 17.0 删了 opt-in 门（`_OPT_IN_TOOLS` 不复存在），
  这里钉的始终是语义，不是"要不要把它们放进连接期"。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp import tasks
from dhole_mcp.server import MasterFetchServer


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Point the whole library at a throwaway DHOLE_HOME."""
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
    tasks.reset_connection()
    yield tasks
    tasks.reset_connection()


def _run(coro):
    return asyncio.run(coro)


# ─── 录制时的验证 ───────────────────────────────────────────────────────

class TestRecordTimeValidation:
    def test_a_bad_chain_is_refused_and_nothing_is_stored(self, lib):
        with pytest.raises(ValueError):
            lib.save_recipe("r", "https://example.com/", [{"click": ""}])
        assert lib.get_recipe("r") is None
        assert lib.list_recipes() == []

    def test_an_unknown_action_key_is_refused(self, lib):
        with pytest.raises(ValueError):
            lib.save_recipe("r", "https://example.com/", [{"teleport": True}])
        assert lib.list_recipes() == []

    def test_too_many_actions_is_refused(self, lib):
        """MAX_ACTIONS 是执行器的上限，录制走同一个验证器就必须同样被拦。"""
        from dhole_mcp.actions import MAX_ACTIONS

        with pytest.raises(ValueError):
            lib.save_recipe("r", "https://example.com/",
                            [{"wait": 1}] * (MAX_ACTIONS + 1))

    def test_what_is_stored_is_what_the_validator_returned(self, lib):
        """存的是**归一化后**的链：执行器读到的形状与验证时的形状是同一个。"""
        from dhole_mcp.actions import validate_actions

        raw = [{"click": {"index": 3, "expect": "登录"}}, {"wait": "500"}]
        lib.save_recipe("r", "https://example.com/", raw)
        assert lib.get_recipe("r")["actions"] == validate_actions(raw)

    def test_an_empty_url_is_refused(self, lib):
        with pytest.raises(ValueError):
            lib.save_recipe("r", "   ", [{"wait": 1}])

    def test_a_name_is_required_and_bounded(self, lib):
        with pytest.raises(ValueError):
            lib.save_recipe("  ", "https://example.com/", [{"wait": 1}])
        with pytest.raises(ValueError):
            lib.save_recipe("x" * (lib.NAME_MAX + 1), "https://example.com/",
                            [{"wait": 1}])


# ─── recipe 的账 ────────────────────────────────────────────────────────

class TestRecipeBookkeeping:
    def test_save_get_list_delete(self, lib):
        saved = lib.save_recipe("r1", "https://example.com/a", [{"wait": 1}],
                                note="备注")
        assert saved["updated"] is False and saved["actions"] == 1
        got = lib.get_recipe("r1")
        assert got["url"] == "https://example.com/a" and got["note"] == "备注"
        assert [r["name"] for r in lib.list_recipes()] == ["r1"]
        assert lib.delete_recipe("r1") is True
        assert lib.delete_recipe("r1") is False
        assert lib.list_recipes() == []

    def test_resaving_keeps_the_usage_stats(self, lib):
        """重录一条配方是**修它**，不是把它变成一个没人用过的新配方。"""
        lib.save_recipe("r1", "https://example.com/a", [{"wait": 1}])
        lib.note_use("r1", ok=True)
        before = lib.get_recipe("r1")
        lib.save_recipe("r1", "https://example.com/b", [{"wait": 2}])
        after = lib.get_recipe("r1")
        assert after["url"] == "https://example.com/b"
        assert after["use_count"] == before["use_count"] == 1
        assert after["created_at"] == before["created_at"]

    def test_note_use_counts_and_records_the_reason(self, lib):
        lib.save_recipe("r1", "https://example.com/a", [{"wait": 1}])
        lib.note_use("r1", ok=False, error="action 0 failed")
        got = lib.get_recipe("r1")
        assert got["use_count"] == 1 and got["last_ok"] is False
        assert "action 0 failed" in got["last_error"]
        lib.note_use("r1", ok=True)
        got = lib.get_recipe("r1")
        assert got["use_count"] == 2 and got["last_ok"] is True
        assert got["last_error"] == ""

    def test_note_use_never_raises_on_a_missing_recipe(self, lib):
        """统计写入是尽力而为：它绝不能把一次成功的抓取变成失败的调用。"""
        lib.note_use("nope", ok=True)          # 不抛

    def test_the_cap_evicts_the_least_recently_used(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "MAX_RECIPES", 3)
        for name in ("a", "b", "c"):
            lib.save_recipe(name, "https://example.com/", [{"wait": 1}])
        lib.note_use("a", ok=True)             # a 变成最近用过的
        lib.save_recipe("d", "https://example.com/", [{"wait": 1}])
        names = {r["name"] for r in lib.list_recipes()}
        assert "a" in names and len(names) == 3
        assert "b" not in names or "c" not in names


# ─── watch 的语义 ───────────────────────────────────────────────────────

class TestWatchSemantics:
    def test_the_first_check_creates_the_baseline_and_is_not_a_change(self, lib):
        lib.save_watch("w", "https://example.com/")
        lib.set_baseline("w", "line one")
        lib.note_check("w", changed=None)
        got = lib.get_watch("w")
        assert got["baseline"] == "line one"
        assert got["check_count"] == 1
        assert got["change_count"] == 0        # 首跑绝不能算成一次变化

    def test_an_identical_snapshot_is_not_a_change(self, lib):
        lib.save_watch("w", "https://example.com/")
        lib.set_baseline("w", "a\nb")
        lib.note_check("w", changed=False)
        assert lib.get_watch("w")["change_count"] == 0

    def test_a_changed_snapshot_moves_the_counter(self, lib):
        lib.save_watch("w", "https://example.com/")
        lib.note_check("w", changed=True)
        assert lib.get_watch("w")["change_count"] == 1

    def test_the_snapshot_is_capped(self, lib):
        lib.save_watch("w", "https://example.com/")
        lib.set_baseline("w", "x" * (lib.MAX_SNAPSHOT_CHARS + 5000), cut=True)
        got = lib.get_watch("w")
        assert len(got["baseline"]) == lib.MAX_SNAPSHOT_CHARS
        assert got["baseline_cut"] is True

    def test_retargeting_drops_the_baseline(self, lib):
        """换 URL / focus 后旧基线毫无意义：留着它，下一次 check 会把整页报成新增。"""
        lib.save_watch("w", "https://example.com/a", focus="price")
        lib.set_baseline("w", "old snapshot")
        lib.save_watch("w", "https://example.com/b", focus="price")
        assert lib.get_watch("w")["baseline"] == ""

    def test_renoting_keeps_the_baseline(self, lib):
        lib.save_watch("w", "https://example.com/a", focus="price")
        lib.set_baseline("w", "old snapshot")
        lib.save_watch("w", "https://example.com/a", focus="price", note="改备注")
        got = lib.get_watch("w")
        assert got["baseline"] == "old snapshot" and got["note"] == "改备注"

    def test_changing_only_focus_drops_the_baseline(self, lib):
        lib.save_watch("w", "https://example.com/a", focus="price")
        lib.set_baseline("w", "old snapshot")
        lib.save_watch("w", "https://example.com/a", focus="title")
        assert lib.get_watch("w")["baseline"] == ""

    def test_list_reports_the_baseline_size(self, lib):
        lib.save_watch("w", "https://example.com/")
        lib.set_baseline("w", "abc")
        rows = lib.list_watches()
        assert rows[0]["baseline_chars"] == 3

    def test_max_chars_must_be_a_non_negative_int(self, lib):
        with pytest.raises(ValueError):
            lib.save_watch("w", "https://example.com/", max_chars=-1)
        with pytest.raises(ValueError):
            lib.save_watch("w", "https://example.com/", max_chars=True)


# ─── diff ───────────────────────────────────────────────────────────────

class TestDiff:
    def test_counts_the_real_change(self, lib):
        d = lib.diff_text("a\nb\nc", "a\nB\nc\nd")
        assert d["added"] == 2 and d["removed"] == 1
        assert any(line.startswith("+B") for line in d["diff"])
        assert any(line.startswith("-b") for line in d["diff"])
        assert d["diff_truncated"] is False

    def test_the_cap_reports_itself_but_the_counts_stay_complete(self, lib):
        d = lib.diff_text("", "\n".join(f"l{i}" for i in range(500)), max_lines=10)
        assert d["added"] == 500               # 计数没被截断
        assert len(d["diff"]) == 10            # 渲染被截断
        assert d["diff_truncated"] is True

    def test_identical_text_is_empty(self, lib):
        d = lib.diff_text("same\ntext", "same\ntext")
        assert (d["added"], d["removed"], d["diff"]) == (0, 0, [])

    def test_an_empty_old_snapshot_is_all_added(self, lib):
        d = lib.diff_text("", "x\ny")
        assert d["added"] == 2 and d["removed"] == 0


# ─── 17.0：没有 opt-in 组，默认全量注册 ─────────────────────────────────

class TestOptInWiring:
    def test_both_tools_are_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("DHOLE_TOOLS", raising=False)
        monkeypatch.delenv("DHOLE_TASKS", raising=False)
        enabled = server_mod._parse_enabled_tools()
        assert {"recipe", "watch"} <= set(server_mod._TOP_LEVEL_ARGS)
        assert {"recipe", "watch"} <= enabled

    def test_a_stale_tasks_flag_does_nothing(self, monkeypatch):
        """注销的旋钮运行时静默忽略 —— 不许它复活成一道闸。"""
        monkeypatch.delenv("DHOLE_TOOLS", raising=False)
        monkeypatch.setenv("DHOLE_TASKS", "0")
        assert {"recipe", "watch"} <= server_mod._parse_enabled_tools()

    def test_naming_them_in_dhole_tools_still_works(self, monkeypatch):
        monkeypatch.setenv("DHOLE_TOOLS", "web_read,recipe,watch")
        assert server_mod._parse_enabled_tools() == frozenset(
            {"web_read", "recipe", "watch"})

    def test_the_default_instructions_route_to_them(self, monkeypatch):
        """默认注册的工具必须在默认 instructions 里有路由行——否则连上来
        也找不到它们。"""
        monkeypatch.delenv("DHOLE_TOOLS", raising=False)
        monkeypatch.delenv("DHOLE_TASKS", raising=False)
        text = server_mod._compose_instructions(server_mod._parse_enabled_tools())
        assert "recipe" in text and "watch" in text

    def test_a_disabled_tool_is_refused_with_a_named_error(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_ENABLED_TOOLS", frozenset({"web_read"}))
        s = MasterFetchServer()
        with pytest.raises(ValueError) as e:
            _run(s._dispatch("recipe", {"op": "list"}))
        assert "not enabled" in str(e.value) and "DHOLE_TOOLS" in str(e.value)


# ─── 工具方法自己的形状 ─────────────────────────────────────────────────

class TestRecipeTool:
    def test_list_on_an_empty_library_says_how_to_start(self, lib):
        m = _run(MasterFetchServer().recipe(op="list"))
        assert m.recipes == [] and "save" in m.next_action

    def test_an_unknown_op_is_answered_not_raised(self, lib):
        m = _run(MasterFetchServer().recipe(op="frobnicate"))
        assert "unknown op" in m.error and "save" in m.next_action

    def test_run_without_a_name_says_so(self, lib):
        m = _run(MasterFetchServer().recipe(op="run"))
        assert "name is required" in m.error

    def test_run_of_a_missing_recipe_names_what_exists(self, lib):
        lib.save_recipe("known", "https://example.com/", [{"wait": 1}])
        m = _run(MasterFetchServer().recipe(op="run", name="other"))
        assert "No recipe named 'other'" in m.error
        assert "known" in m.next_action

    def test_a_bad_chain_comes_back_as_an_envelope(self, lib):
        """录制失败必须是 envelope，不是异常——调用方要拿到 next_action。"""
        m = _run(MasterFetchServer().recipe(
            op="save", name="r", url="https://example.com/", actions=[{"click": ""}]))
        assert m.error and "Nothing was stored" in m.next_action
        assert lib.get_recipe("r") is None

    def test_delete_of_a_missing_recipe_says_nothing_was_deleted(self, lib):
        m = _run(MasterFetchServer().recipe(op="delete", name="ghost"))
        assert "nothing was deleted" in m.message


class TestWatchTool:
    def test_check_without_a_name_says_so(self, lib):
        m = _run(MasterFetchServer().watch(op="check"))
        assert "name is required" in m.error

    def test_check_of_a_missing_watch_names_what_exists(self, lib):
        lib.save_watch("known", "https://example.com/")
        m = _run(MasterFetchServer().watch(op="check", name="other"))
        assert "No watch named 'other'" in m.error and "known" in m.next_action

    def test_an_unknown_op_is_answered_not_raised(self, lib):
        m = _run(MasterFetchServer().watch(op="frobnicate"))
        assert "unknown op" in m.error

    def test_a_failed_fetch_leaves_the_baseline_untouched(self, lib, monkeypatch):
        """一次网络故障绝不能被写成『页面清空了』。"""
        lib.save_watch("w", "https://example.com/")
        lib.set_baseline("w", "the good snapshot")

        async def _boom(self, **kw):
            return server_mod.ResponseModel(
                url="https://example.com/", status=0, content=[],
                error="connection reset", next_action="retry later")

        monkeypatch.setattr(MasterFetchServer, "web_read", _boom)
        m = _run(MasterFetchServer().watch(op="check", name="w"))
        assert m.error and m.changed is None
        assert lib.get_watch("w")["baseline"] == "the good snapshot"

    def test_add_reports_that_retargeting_dropped_the_baseline(self, lib):
        lib.save_watch("w", "https://example.com/a")
        lib.set_baseline("w", "old")
        m = _run(MasterFetchServer().watch(
            op="add", name="w", url="https://example.com/b"))
        assert "baseline was dropped" in m.message
