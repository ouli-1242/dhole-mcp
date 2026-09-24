"""Token-spend knobs: the default content budget, and the ceiling's identity.

Why this file exists: the per-call BODY — not the tools/list table — is where an
agent's tokens go. Measured on docs.python.org/3/library/asyncio-task.html: one
smart_fetch at the shipped 40,000-char default returned 42,037 chars (~10.5k
tokens), more than all 8 tool schemas together (11,295 chars / ~2.8k tokens).

``DHOLE_DEFAULT_CONTENT_CHARS`` changes what a caller gets when it does NOT pass
``max_content_chars``. It is deliberately not the ceiling: the 500-200000 range
and the 200000 clamp stay exactly as documented, so asking for everything is
still possible — only the unasked-for default shrinks, and the rest stays
reachable through offset/next_offset with the response saying so.
"""

from __future__ import annotations

import ast
import asyncio
import os
import pathlib
import subprocess
import sys

import pytest

import dhole_mcp.server as server_mod
from dhole_mcp.server import ResponseModel

WAY_OVER = 5000  # chars: longer than any budget under test


def _stub_fetch(monkeypatch, body: str) -> None:
    """Replace the fetch tiers with one canned body.

    The stub passes the budget it RECEIVES (the positional ``max_chars``) to the
    real ``_apply_chunking``, so the test observes the value ``smart_fetch``
    computed from the default — stubbing the tool method instead would test the
    stub rather than the wiring.
    """
    result = ResponseModel(url="https://example.com/", status=200, content=[body])

    async def fake(self, url, *a, **kw):
        return server_mod._apply_chunking(result, max_chars=a[-1])

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake)


def _fetch(args: dict) -> dict:
    """Run one smart_fetch through the real dispatcher; return the envelope."""
    _content, structured = asyncio.run(
        server_mod.MasterFetchServer(cache_ttl=0)._dispatch("smart_fetch", args))
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
        assert payload["is_truncated"] is False

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
