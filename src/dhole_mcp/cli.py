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
recovery path. What remains: name the module that is missing, print the command
that fixes it, exit non-zero so a host cannot mistake this for a started server.
"""

from __future__ import annotations

import sys

_FIX = """\
  dhole cannot start: {what}
  re-install dhole with its dependencies from the checkout, then restart your MCP host:
    python -m pip install -e ".[all]"
  (nothing was installed and no process was touched - 17.0 removed the unattended repair.)
"""


def main() -> int:
    """Run the server; if it cannot even be imported, say which piece is missing."""
    try:
        from dhole_mcp.server import main as _server_main
        return _server_main() or 0
    except (ImportError, ModuleNotFoundError) as exc:
        # The missing name is what makes this actionable: "No module named 'bs4'"
        # points at one install command, while a traceback pointing at the import
        # inside server.py points at a bug hunt.
        what = getattr(exc, "name", "") or str(exc)
        sys.stderr.write(_FIX.format(what=what))
        return 1
