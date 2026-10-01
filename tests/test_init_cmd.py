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

