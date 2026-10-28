"""test_ops.py —— 技能包装与安装（`dhole skill`）

只测行为：装出去的是不是完整的那一份、重复装是不是幂等、本地改过会不会被覆盖。
CHANGELOG / README 的措辞清点不在这里 —— 那是账本，不是行为。
"""


from __future__ import annotations


import pytest
from pathlib import Path

from dhole_mcp.ops import _cmd_skill, _skill_manifest

pytestmark = pytest.mark.smoke


# ============================================================================
# 来源：test_skill_install_g32.py
# ============================================================================
# G32 — the agent skill ships inside the package and installs via `dhole skill`.

_BUNDLED = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills" / "dhole-web"


def test_bundled_skill_is_complete():
    assert (_BUNDLED / "SKILL.md").is_file()
    refs = _BUNDLED / "references"
    assert refs.is_dir()
    names = {p.name for p in refs.glob("*.md")}
    assert {"tools.md", "examples.md", "fields.md", "troubleshooting.md",
            "configuration.md", "recipes.md", "start.md"} <= names
    manifest = _skill_manifest(_BUNDLED)
    # 只钉"该在的都在"，不钉总数：多带一份校准规格是改包，不是说谎。
    assert manifest["SKILL.md"] and manifest["references/tools.md"]
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


# ============================================================================
# 装到哪儿：默认一处，`--target` 才换
# ============================================================================
# 真实反馈：同一套 dhole 在另一个 harness 里模型能直接说出 13 颗工具，在 WorkBuddy
# 里说不出 —— 因为 WorkBuddy 只注入工具名，而随包技能被装到 ~/.agents/skills，它的
# 技能扫描不包含那个目录。装错地方不报错，只是永远不生效。
# 但「装哪儿」不该由 dhole 猜：默认只装那一处共享目录，宿主不扫它就由调用方点名。


def test_the_default_target_is_the_shared_agents_dir(tmp_path, monkeypatch):
    """默认只装一处，且不往任何宿主的私有目录里写。"""
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _cmd_skill(["install"]) == 0
    assert (tmp_path / ".agents" / "skills" / "dhole-web" / "SKILL.md").is_file()
    assert not (tmp_path / ".workbuddy-ai").exists()
    assert not (tmp_path / ".claude").exists()


def test_an_explicit_target_beats_the_default(tmp_path, capsys):
    """宿主不扫 ~/.agents/skills 时，`--target` 是唯一的出路。"""
    only = tmp_path / "only"
    assert _cmd_skill(["install", "--target", str(only)]) == 0
    assert (only / "SKILL.md").is_file()
    assert "--target" not in capsys.readouterr().out, "点名之后不该再教人点名"


def test_the_equals_form_of_target_works_too(tmp_path):
    """`--target=DIR` 与 `--target DIR` 是同一件事 —— 别让人去猜哪种写法。"""
    only = tmp_path / "only"
    assert _cmd_skill(["install", f"--target={only}"]) == 0
    assert (only / "SKILL.md").is_file()
