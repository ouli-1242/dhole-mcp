"""G32 — the agent skill ships inside the package and installs via `dhole skill`.

The skill (SKILL.md + references/) is the agent-facing operations handbook for
dhole's tools. Shipping it with the wheel means upgrading dhole upgrades the
skill — no separate release channel. `dhole skill install` copies the bundled
copy to ~/.agents/skills/dhole-web (agent hosts' user-skill directory) and
refuses to clobber local edits without --force.
"""

from pathlib import Path


from dhole_mcp.server import _cmd_skill, _skill_manifest

_BUNDLED = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills" / "dhole-web"


def test_bundled_skill_is_complete():
    assert (_BUNDLED / "SKILL.md").is_file()
    refs = _BUNDLED / "references"
    assert refs.is_dir()
    names = {p.name for p in refs.glob("*.md")}
    assert {"tools.md", "examples.md", "fields.md", "troubleshooting.md",
            "configuration.md", "recipes.md"} <= names
    manifest = _skill_manifest(_BUNDLED)
    assert len(manifest) == 7
    assert all(v for v in manifest.values())


def test_install_copies_everything(tmp_path, capsys):
    target = tmp_path / "skills" / "dhole-web"
    assert _cmd_skill(["install"], target=target) == 0
    assert _skill_manifest(target) == _skill_manifest(_BUNDLED)
    out = capsys.readouterr().out
    assert "skill installed" in out


def test_install_twice_is_up_to_date(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["install"], target=target) == 0
    assert _cmd_skill(["install"], target=target) == 0
    assert "already up to date" in capsys.readouterr().out


def test_install_refuses_to_clobber_local_edits(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["install"], target=target) == 0
    (target / "SKILL.md").write_text(
        (target / "SKILL.md").read_text(encoding="utf-8") + "\nlocal edit\n",
        encoding="utf-8")
    rc = _cmd_skill(["install"], target=target)
    assert rc == 2
    out = capsys.readouterr().out
    assert "differs" in out and "--force" in out
    # the local edit survived the refusal
    assert "local edit" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_install_force_resyncs(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["install"], target=target) == 0
    (target / "SKILL.md").write_text("drifted", encoding="utf-8")
    (target / "references" / "tools.md").unlink()
    assert _cmd_skill(["install", "--force"], target=target) == 0
    assert _skill_manifest(target) == _skill_manifest(_BUNDLED)
    assert (target / "SKILL.md").read_text(encoding="utf-8") != "drifted"


def test_status_reports_state(tmp_path, capsys):
    target = tmp_path / "sk"
    assert _cmd_skill(["status"], target=target) == 0
    assert "not installed" in capsys.readouterr().out
    _cmd_skill(["install"], target=target)
    capsys.readouterr()
    assert _cmd_skill(["status"], target=target) == 0
    assert "up to date" in capsys.readouterr().out


def test_unknown_subcommand_is_rejected(tmp_path, capsys):
    assert _cmd_skill(["bogus"], target=tmp_path / "sk") == 2
    assert "unknown subcommand" in capsys.readouterr().out


def test_manifest_is_content_hashed(tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    (a / "f.md").write_text("one", encoding="utf-8")
    b = tmp_path / "b"
    b.mkdir()
    (b / "f.md").write_text("two", encoding="utf-8")
    assert _skill_manifest(a) != _skill_manifest(b)
