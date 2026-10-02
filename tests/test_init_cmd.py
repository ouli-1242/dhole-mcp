"""W8 · `dhole init` behaviour tests.

The risky surface here is *another application's* config file. Every test
below either redirects HOME to a tmp_path or monkeypatches SUPPORTED_CLIENTS
so a real Claude Desktop / Cursor / Windsurf / VS Code install on the test
machine cannot be touched. A test that reads a real user config would be a
bug in the test suite; that is the whole point of D3's "always back up and
never silent" clause.

Idempotency is a separate axis: a re-run must detect the existing dhole
entry and print "already registered" without writing. If a fix ever lets a
re-run clobber the block, that test goes red.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from dhole_mcp import init as init_cmd


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Redirect `Path.home()` and `os.path.expanduser` so nothing writes
    into the developer's real HOME dir."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("os.path.expanduser",
                        lambda p: str(tmp_path) + p.lstrip("~"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(init_cmd.shutil, "which", lambda name: "/fake/dhole")
    # Skip the browser-binary check (a real one shells out to `playwright
    # install --dry-run` which is slow and env-dependent); tests below set
    # this per case.
    monkeypatch.setattr(init_cmd, "_playwright_chromium_installed", lambda: True)
    return tmp_path


@pytest.fixture
def fake_claude_config(tmp_home):
    """A Claude Desktop config file we can point `_existing_client_configs`
    at without touching the real install. Also patches
    SUPPORTED_CLIENTS to only the fake."""
    cfg = tmp_home / "claude.json"
    cfg.write_text(json.dumps({"mcpServers": {"existing": {"command": "x"}}}),
                   encoding="utf-8")
    from dhole_mcp.init import ClientSpec
    fake_spec = ClientSpec(
        name="FakeClaude",
        path_resolver=lambda: cfg,
        servers_key="mcpServers",
    )
    # Swap in only the fake client; a test machine's real Claude is off-limits.
    clients_backup = list(init_cmd.SUPPORTED_CLIENTS)
    init_cmd.SUPPORTED_CLIENTS[:] = [fake_spec]
    # Silence the tail doctor() call - we are only testing the write
    # surface, and the full doctor render is already covered in
    # TestDoctorConfigAudit.
    from dhole_mcp import updater
    orig = updater.doctor
    updater.doctor = lambda: 0
    yield cfg
    updater.doctor = orig
    init_cmd.SUPPORTED_CLIENTS[:] = clients_backup


def test_dry_run_writes_nothing_and_prints_the_planned_json(
        fake_claude_config, capsys):
    before = fake_claude_config.read_text(encoding="utf-8")
    rc = init_cmd.run_init([])
    after = fake_claude_config.read_text(encoding="utf-8")
    assert rc == 0
    assert before == after, "dry-run must not touch the file"
    out = capsys.readouterr().out
    assert "dhole" in out, "dry-run must print the JSON it would write"
    assert "dry-run" in out
    # no backup files either
    assert not list(fake_claude_config.parent.glob(fake_claude_config.name + ".bak-*"))


def test_yes_writes_and_backs_up_the_original(fake_claude_config, capsys):
    original = fake_claude_config.read_text(encoding="utf-8")
    rc = init_cmd.run_init(["--yes"])
    assert rc == 0
    written = json.loads(fake_claude_config.read_text(encoding="utf-8"))
    assert "dhole" in written["mcpServers"]
    # The existing `existing` block must be preserved - init only adds.
    assert "existing" in written["mcpServers"]
    # A .bak-<timestamp> file must exist next to the original.
    backups = list(fake_claude_config.parent.glob(fake_claude_config.name + ".bak-*"))
    assert backups, "W8 requires a backup before every write"
    assert backups[0].read_text(encoding="utf-8") == original


def test_re_run_is_idempotent_and_skips(fake_claude_config, capsys):
    init_cmd.run_init(["--yes"])
    # Snapshot state after the first real write.
    first = fake_claude_config.read_text(encoding="utf-8")
    first_backups = set(
        p.name for p in fake_claude_config.parent.glob(
            fake_claude_config.name + ".bak-*"))

    capsys.readouterr()  # drain the first-run output
    rc = init_cmd.run_init(["--yes"])
    after = fake_claude_config.read_text(encoding="utf-8")
    assert rc == 0
    assert first == after, "second run must not re-add or reorder dhole"
    second_backups = set(
        p.name for p in fake_claude_config.parent.glob(
            fake_claude_config.name + ".bak-*"))
    # No NEW backups: nothing was written, so nothing needed backing up.
    assert second_backups == first_backups, \
        f"a re-run created extra backups: {second_backups - first_backups}"
    out = capsys.readouterr().out
    assert "already registered" in out


def test_no_client_found_exits_0_with_manual_instructions(tmp_home, capsys):
    """D3's non-invasive branch: if the machine has none of the four
    known clients, init MUST NOT create one behind the user's back -
    it prints the manual snippet instead and exits 0."""
    from dhole_mcp import updater
    updater.doctor = lambda: 0
    # Wipe the client list so nothing is detected on tmp_home.
    init_cmd.SUPPORTED_CLIENTS[:] = []
    rc = init_cmd.run_init([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no supported MCP client was detected" in out
    assert "command" in out and "dhole" in out


def test_unparseable_config_is_refused_and_not_overwritten(tmp_home, capsys):
    """A half-corrupted client config is not init's problem to silently
    overwrite. We bail out with an error and the file is untouched."""
    cfg = tmp_home / "broken.json"
    cfg.write_text("{not valid json", encoding="utf-8")
    from dhole_mcp.init import ClientSpec
    fake_spec = ClientSpec(name="Broken", path_resolver=lambda: cfg,
                           servers_key="mcpServers")
    backup = list(init_cmd.SUPPORTED_CLIENTS)
    init_cmd.SUPPORTED_CLIENTS[:] = [fake_spec]
    from dhole_mcp import updater
    orig = updater.doctor
    updater.doctor = lambda: 0
    try:
        init_cmd.run_init(["--yes"])
        after = cfg.read_text(encoding="utf-8")
        assert after == "{not valid json", "must not overwrite unparsable config"
        out = capsys.readouterr().out
        assert "not valid JSON" in out
    finally:
        updater.doctor = orig
        init_cmd.SUPPORTED_CLIENTS[:] = backup


def test_init_dispatches_from_main(monkeypatch, tmp_home):
    """W5 §3.4 said init must reuse the same dispatch mechanism as the
    other subcommands. Wire-level check: `dhole init` (bare) hits
    init.run_init with argv tail []."""
    called: list[list[str]] = []

    def fake_run(argv):
        called.append(list(argv))
        return 0

    # Import server first so the subcommand dispatch is patched at the
    # site it actually calls, not just the module attribute.
    from dhole_mcp import server as srv

    import dhole_mcp.init as init_module
    monkeypatch.setattr(init_module, "run_init", fake_run)

    monkeypatch.setattr(sys, "argv", ["dhole", "init", "--yes"])
    with pytest.raises(SystemExit):
        srv.main()
    assert called == [["--yes"]]



class TestTheLiveProbe:
    """--probe 问的是运行中的代码，不是静态体检：从这里搜得到吗、抓得开吗。"""

    @pytest.fixture
    def stub_dispatch(self, monkeypatch):
        import asyncio

        import dhole_mcp.server as srv
        from mcp.types import TextContent

        calls: list[str] = []

        async def fake(self, name, args):
            calls.append(name)
            body = {"results": [{"title": "t"}], "engines_consensus": "3 of 5",
                    "rerank_mode": "neural", "duration_ms": 900}
            if name == "smart_fetch":
                body = {"status": 200, "total_extracted_chars": 1234,
                        "fetcher_used": "http", "duration_ms": 210,
                        "metadata": {"ssrf_recheck": "auto-skipped on this host"}}
            return [TextContent(type="text", text="x")], body

        monkeypatch.setattr(srv.MasterFetchServer, "_auto_escalate",
                            lambda *a, **k: asyncio.sleep(0))
        monkeypatch.setattr(srv.MasterFetchServer, "_dispatch", fake)
        return calls

    def test_off_by_default(self, tmp_home, stub_dispatch, capsys):
        init_cmd.run_init([])
        assert stub_dispatch == [], "不发真实流量，除非明说 --probe"

    def test_names_both_targets_and_the_posture_note(self, tmp_home, stub_dispatch, capsys):
        init_cmd.run_init(["--probe"])
        out = capsys.readouterr().out
        assert stub_dispatch == ["smart_search", "smart_fetch"]
        assert "search: 1 results" in out and "engines 0" in out
        assert "fetch https://example.com/: 200 1234 chars via http" in out
        assert "auto-skipped on this host" in out

    def test_a_slow_first_search_is_printed_in_seconds(self):
        """219626ms 读起来像工具坏了，219.6s 读起来像它在说什么。"""
        assert init_cmd._ms(900) == "900ms"
        assert init_cmd._ms(219626) == "219.6s"
        assert init_cmd._ms(None) == "0ms"
        assert init_cmd._ms("junk") == "?"

    def test_an_empty_pool_is_reported_as_a_fact_with_a_next_step(
            self, tmp_home, monkeypatch, capsys):
        import dhole_mcp.server as srv
        from mcp.types import TextContent

        async def empty(self, name, args):
            body = {"results": [], "error": None} if name == "smart_search" \
                else {"error": "resolves to internal/private IP", "next_action": "x"}
            return [TextContent(type="text", text="x")], body

        monkeypatch.setattr(srv.MasterFetchServer, "_dispatch", empty)
        init_cmd.run_init(["--probe"])
        out = capsys.readouterr().out
        assert "engines probe" in out, "空池要给出下一步，不是只报 0 结果"
        assert "network fact" in out


class TestInitOpensNoGates:
    """17.0：init 只做安装与客户端登记 —— `--tasks/--vault/--neural/--cdp`
    随能力闸一起注销。任何 DHOLE_* 都不许被它写进配置文件。"""

    def test_the_capability_flags_are_refused_as_unknown(self, tmp_home, capsys):
        for argv in (["--tasks"], ["--vault"], ["--neural"], ["--cdp", "h"]):
            assert init_cmd.run_init(argv) == 2, argv
            assert "unknown flag" in capsys.readouterr().out

    def test_a_full_init_writes_no_dhole_knobs(self, tmp_home, monkeypatch):
        from dhole_mcp import env_registry as er
        assert init_cmd.run_init(["--yes"]) == 0
        assert [k for k in er.file_entries() if k.startswith("DHOLE_")] == []
