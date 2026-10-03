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
    # 语料是 7 份（SKILL.md + 六篇 serve 出去的 references）。第 8 份
    # evals/evals.json 是技能自己的校准跑规格：它进包是为了装完之后技能目录是完整的，
    # 但它不在 guide.TOPICS 里，所以永远不会被当成说明书的一节端出去 —— 下面那条就是
    # 钉这一点的，否则"多塞一份"从一次改包变成一条谎。
    assert len(manifest) == 8
    assert manifest["evals/evals.json"]
    from dhole_mcp.guide import TOPICS
    served = {path for (path, _when) in TOPICS.values()}
    assert "evals/evals.json" not in served, "校准规格不是语料"
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
