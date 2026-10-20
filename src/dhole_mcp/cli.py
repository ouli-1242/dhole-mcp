"""CLI entry point for dhole — and the one thing it must still do on a broken install.

This module is the pip entry point (`dhole = dhole_mcp.cli:main`). It stays
deliberately lightweight: NO heavy imports at module level. When dhole.exe runs,
it does `from dhole_mcp.cli import main`, which pulls in `dhole_mcp.__init__`
(just `__version__`) and this module (stdlib only). The heavy server import
happens lazily inside main().

17.0 B2-3: the self-heal became a sentence instead of a subprocess. It used to
write ``~/.dhole/repair.py`` and run it — stop every dhole process, then
``pip --force-reinstall`` a few hundred MB — on ANY ImportError, including one
raised by an unrelated package sharing the environment. A repair that destroys
the state it was meant to protect, decided by an exception nobody read, is not a
recovery path. What remains: name the piece that is missing — the module when a module
is gone, the SYMBOL when dhole's own files disagree — and print the command that fixes
that one, then exit non-zero so a host cannot mistake this for a started server.
"""

from __future__ import annotations

import sys

_FIX_DEPS = """\
  dhole cannot start: {what}
  re-install dhole with its dependencies from the checkout, then restart your MCP host:
    python -m pip install -e ".[all]"
  (nothing was installed and no process was touched - 17.0 removed the unattended repair.)
"""

# R01-002: a missing SYMBOL is not a missing dependency. `.[all]` here sends the user
# through a few hundred MB of installs that change nothing, because the package that
# owns both sides of the broken import is dhole itself. Wording note: the 17.0 B2-3
# guard (tests/test_self_heal_prints_only.py) forbids the reinstall flag anywhere in an
# executed string, so this names the levers that actually restore the code instead.
_FIX_CODE = """\
  dhole cannot start: {what}
  this is not a missing package - dhole's own files disagree with each other (one side
  imports a name the other does not have), so installing dependencies would change
  nothing. Restore the code, then restart your MCP host:
    in a checkout:  git pull   (or re-clone at the revision you want)
    from the index: python -m pip uninstall -y dhole-mcp
                    python -m pip install --no-deps dhole-mcp
  (nothing was installed and no process was touched - 17.0 removed the unattended repair.)
"""


def _startup_failure(exc: BaseException) -> str:
    """One report per shape of "cannot import": a missing module vs a missing symbol.

    CPython hands `ModuleNotFoundError.name` the module, which is the whole story there.
    For "cannot import name 'X' from 'Y'" it hands the **module Y** (measured on 3.14),
    so reading `.name` alone drops the only actionable fact - the symbol X, which lives
    in `str(exc)`. The remedy differs too: the first shape wants its dependencies, the
    second wants dhole's own files brought back into agreement.
    """
    text = str(exc)
    if "cannot import name" in text:
        return _FIX_CODE.format(what=text)
    return _FIX_DEPS.format(what=getattr(exc, "name", "") or text)


def main() -> int:
    """Run the server; if it cannot even be imported, say which piece is missing."""
    try:
        from dhole_mcp.ops import main as _server_main
        return _server_main() or 0
    except (ImportError, ModuleNotFoundError) as exc:
        # The missing name is what makes this actionable: "No module named 'bs4'"
        # points at one install command, while "cannot import name X from Y" points at
        # a code mismatch that no amount of installing dependencies will repair.
        sys.stderr.write(_startup_failure(exc))
        return 1
