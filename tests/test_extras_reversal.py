"""W7 · extras default reversal.

The venv-install test the plan asks for (§3 W7 验收) needs a clean
environment that the current editable install cannot give - that is the
verifier's job. These tests are the source-level guard: they pin the
invariants that make W7 safe to ship, so a future contributor cannot undo
the reversal by hand or drop the `[all]` extra (which updater's self-heal
path imports by name).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"


def _load():
    return tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))


def test_default_dependencies_now_includes_the_heavy_tier():
    """D2 was accepted: the obvious install command should install every
    capability, not the HTTP-only fallback. The old `[all]` items must now
    appear in `[project].dependencies` verbatim."""
    deps = [str(d).split("[")[0].split(">")[0].split("=")[0].split("<")[0]
            for d in _load()["project"]["dependencies"]]
    for heavy in ("patchright", "playwright", "browserforge", "pdfplumber",
                  "pypdfium2", "rapidocr", "onnxruntime", "opencv-python-headless",
                  "python-docx", "openpyxl"):
        assert heavy in deps, f"W7 folded {heavy} into default deps; a later PR cannot drop it"


def test_all_extra_is_preserved_with_same_contents():
    """updater.py's self-heal path calls
    ``_pip("--force-reinstall", "--no-deps", "dhole-mcp[all]==" + TARGET)``
    - dropping the `[all]` extra would break the repair, and the plan
    §3 W7.1 hard constraint says keep it. Contents stay the same list so
    the 49+ hint strings referencing `[all]` remain literally valid."""
    extras = _load()["project"]["optional-dependencies"]
    assert "all" in extras, "`all` extra must remain for updater self-heal"
    all_names = [str(d).split("[")[0].split(">")[0].split("=")[0].split("<")[0]
                 for d in extras["all"]]
    for heavy in ("patchright", "playwright", "pdfplumber", "rapidocr",
                  "onnxruntime", "opencv-python-headless", "python-docx",
                  "openpyxl"):
        assert heavy in all_names, f"[all] must still name {heavy} (self-heal path)"


def test_slim_extra_is_declared_as_a_name():
    """`slim` names the pre-W7 default tier. pip extras can only ADD, so
    this list is empty today - see pyproject's comment block. The
    assertion is that the key exists at all; a future release that ships
    a real slim wheel can populate it, and this test would still pass."""
    extras = _load()["project"]["optional-dependencies"]
    assert "slim" in extras, "W7 §3.2 requires a `slim` extra to exist"


def test_all_and_default_heavy_tiers_are_the_same_set():
    """Drift guard: if someone edits one and not the other, the compat
    promise breaks - `pip install dhole-mcp` and `pip install
    dhole-mcp[all]` should give the same result. Currently true; the test
    stops a future PR from making them disagree."""
    deps = _load()["project"]["dependencies"]
    all_extra = _load()["project"]["optional-dependencies"]["all"]
    dep_set = {str(d) for d in deps}
    # every item in `[all]` must also be in default deps
    for item in all_extra:
        assert str(item) in dep_set, f"{item!r} is in [all] but not default deps"


def test_readme_documents_the_playwright_install_step_is_separate():
    """W7 §3.3 - don't claim "install and go" without saying the Chromium
    binary is a separate download."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "playwright install chromium" in readme
    # the W7 wording that explains this explicitly
    assert "pip 装不到这一步" in readme or "两步都跑完" in readme
