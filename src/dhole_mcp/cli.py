"""Self-healing CLI entry point for dhole.

This module is the pip entry point (dhole = dhole_mcp.cli:main). It is
deliberately lightweight: NO heavy imports at module level. When dhole.exe
runs, it does `from dhole_mcp.cli import main` which imports
`dhole_mcp.__init__` (just __version__, no deps) and this module (stdlib
only). The heavy server import happens lazily inside main(), wrapped in a
try/except that auto-recovers from a broken install.

Self-heal flow:
1. User runs `dhole` (any command) after a broken update/dep change
2. `from dhole_mcp.server import main` fails (ImportError/ModuleNotFoundError)
3. cli.py writes ~/.dhole/repair.py and runs it: stop dhole, force-reinstall,
   verify. The script follows DHOLE_UPDATE_PACKAGE / DHOLE_UPDATE_INDEX_URL so a
   fork is repaired from its OWN distribution, never from a name baked in at
   release time (that is how a repair once installed the wrong package over
   this fork's code).
4. If auto-repair is disabled (DHOLE_NO_AUTO_REPAIR=1) or fails: print a clean
   one-line error (not a traceback) with the exact fix command.
"""

from __future__ import annotations

import os
import sys


def _dist_name() -> str:
    """Distribution the repair path reinstalls (DHOLE_UPDATE_PACKAGE or the default)."""
    return (os.environ.get("DHOLE_UPDATE_PACKAGE") or "").strip() or "dhole-mcp"


def _index_url() -> str:
    """Optional index url for the repair pip call (DHOLE_UPDATE_INDEX_URL)."""
    return (os.environ.get("DHOLE_UPDATE_INDEX_URL") or "").strip()


def _auto_repair_enabled() -> bool:
    """DHOLE_NO_AUTO_REPAIR=1 disables the pip-reinstall-on-ImportError path.

    Without it, ANY ImportError - including one raised by an unrelated package in
    the same environment - triggers an unattended ``pip --force-reinstall`` of a
    distribution from the network plus a taskkill of every running dhole process.
    Users who would rather read the traceback and fix it themselves opt out here.
    """
    return (os.environ.get("DHOLE_NO_AUTO_REPAIR") or "").strip().lower() not in (
        "1", "true", "yes", "on",
    )


def _repair_script_text(dist: str, index_url: str) -> str:
    """Standalone repair script written to ~/.dhole/repair.py.

    Stdlib-only and path-independent on purpose: it must still run when the dhole
    package (or its metadata) is gone, which is the whole point of the file.
    """
    index_args = ""
    if index_url:
        index_args = "\n    args += [\"--index-url\", %r]" % index_url
    return '''import sys, subprocess

DIST = %r

def _stop():
    print("Dhole repair: stopping any running dhole...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "dhole.exe", "/F"], capture_output=True)
    else:
        # -x matches the process name exactly ("dhole"), not this script ("python").
        subprocess.run(["pkill", "-x", "dhole"], capture_output=True)

def main():
    _stop()
    print("Dhole repair: force-reinstalling %%s ..." %% DIST)
    args = ["--force-reinstall", DIST]%s
    r = subprocess.run([sys.executable, "-m", "pip", "install", *args,
                        "--quiet", "--disable-pip-version-check"])
    if r.returncode != 0:
        print("Dhole repair: reinstall failed (pip exit %%d)." %% r.returncode)
        print("  Try manually: %%s -m pip install --force-reinstall %%s" %% (sys.executable, DIST))
        return r.returncode
    try:
        from importlib.metadata import version as _v
        print("%%s %%s  repaired" %% (DIST, _v(DIST)))
    except Exception:
        print("%%s reinstalled (version check skipped)" %% DIST)
    return 0

if __name__ == "__main__":
    sys.exit(main())
''' % (dist, index_args)


def _run_repair() -> int:
    """(Re)write ~/.dhole/repair.py and run it to auto-recover a broken install.

    The script is rewritten on every repair so it always follows the configured
    distribution and index, rather than a name baked in at release time.
    """
    dist = _dist_name()
    index_url = _index_url()
    repair = os.path.join(os.path.expanduser("~"), ".dhole", "repair.py")
    try:
        os.makedirs(os.path.dirname(repair), exist_ok=True)
        with open(repair, "w", encoding="utf-8") as f:
            f.write(_repair_script_text(dist, index_url))
    except Exception:
        # Can't write repair.py - run pip directly as a last resort
        print("  recovering (direct reinstall)...")
        import subprocess
        cmd = [sys.executable, "-m", "pip", "install",
               "--force-reinstall", dist, "--quiet", "--disable-pip-version-check"]
        if index_url:
            cmd += ["--index-url", index_url]
        try:
            subprocess.run(cmd, timeout=120)
        except Exception as e:
            print(f"  Recovery error: {e}")
            return 1
        print("  Dhole recovered. Re-run your command.")
        return 0
    import subprocess
    print("  recovering...")
    try:
        result = subprocess.run(
            [sys.executable, repair],
            timeout=120,
            capture_output=False,
        )
        if result.returncode == 0:
            print("  Dhole recovered. Re-run your command.")
            return 0
        print("  Recovery failed. Run manually: "
              f'python "{repair}"')
        return 1
    except Exception as e:
        print(f"  Recovery error: {e}")
        print(f'  Run manually: python "{repair}"')
        return 1


def main() -> int:
    """Entry point that self-heals on broken imports."""
    try:
        from dhole_mcp.server import main as _server_main
        return _server_main() or 0
    except (ImportError, ModuleNotFoundError) as e:
        # Broken install: missing dep, half-failed update, etc.
        # Don't crash with a traceback - auto-recover.
        mod_name = getattr(e, "name", "") or str(e)
        print(f"  Dhole install broken: {mod_name}")
        if not _auto_repair_enabled():
            print("  Auto-repair disabled (DHOLE_NO_AUTO_REPAIR=1).")
            print(f"  Fix manually: {sys.executable} -m pip install --force-reinstall {_dist_name()}")
            return 1
        rc = _run_repair()
        if rc != 0:
            print(f"  If recovery failed, run: pip install --force-reinstall {_dist_name()}")
        return rc
    except Exception:
        # Any other import-time crash (not a missing module) - re-raise
        # so real bugs surface, but only after trying repair as a last resort
        # if the error looks install-related.
        raise
