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
3. cli.py writes repair.py under the state dir (`paths.home()`, i.e. `DHOLE_HOME`
   when set) and runs it: stop dhole, force-reinstall,
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


def _installed_version(dist: str) -> str:
    """当前已装的发行版版本，取不到就返回 ""。

    自愈路径用它把重装**钉死在现有版本**上。不钉的话，一个由任意 ImportError
    触发的无人值守重装会从索引拉取"当时的最新版" —— 那等于把信任根交给包名字空间
    （这个名字已经被改过两次，CHANGELOG 里就记着自愈曾因解析到上游发行名而覆盖掉本
    fork）。metadata 缺失时只能不钉，而那恰好就是安装已损坏的场景，故保留兜底。
    """
    try:
        from importlib.metadata import version as _v
        return (_v(dist) or "").strip()
    except Exception:
        return ""


def _pip_spec(dist: str) -> str:
    """pip 参数：能确定版本就钉住，否则退回裸包名。"""
    ver = _installed_version(dist)
    return f"{dist}=={ver}" if ver else dist


def _repair_script_text(dist: str, index_url: str) -> str:
    """Standalone repair script written into the state dir as ``repair.py``.

    Stdlib-only and path-independent on purpose: it must still run when the dhole
    package (or its metadata) is gone, which is the whole point of the file.
    """
    index_args = ""
    if index_url:
        index_args = "\n    args += [\"--index-url\", %r]" % index_url
    return '''import sys, subprocess

DIST = %r
SPEC = %r

def _stop():
    print("Dhole repair: stopping any running dhole...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/IM", "dhole.exe", "/F"], capture_output=True)
    else:
        # -x matches the process name exactly ("dhole"), not this script ("python").
        subprocess.run(["pkill", "-x", "dhole"], capture_output=True)

def main():
    _stop()
    print("Dhole repair: force-reinstalling %%s ..." %% SPEC)
    args = ["--force-reinstall", SPEC]%s
    r = subprocess.run([sys.executable, "-m", "pip", "install", *args,
                        "--quiet", "--disable-pip-version-check"])
    if r.returncode != 0:
        print("Dhole repair: reinstall failed (pip exit %%d)." %% r.returncode)
        print("  Try manually: %%s -m pip install --force-reinstall %%s" %% (sys.executable, SPEC))
        return r.returncode
    try:
        from importlib.metadata import version as _v
        print("%%s %%s  repaired" %% (DIST, _v(DIST)))
    except Exception:
        print("%%s reinstalled (version check skipped)" %% DIST)
    return 0

if __name__ == "__main__":
    sys.exit(main())
''' % (dist, _pip_spec(dist), index_args)


def _run_repair() -> int:
    """(Re)write repair.py under the state dir and run it to auto-recover.

    The script is rewritten on every repair so it always follows the configured
    distribution and index, rather than a name baked in at release time.

    The location comes from ``paths.home()`` (``DHOLE_HOME`` when set), which is
    where ``updater.repair_script_path()`` already pointed. It used to be spelled
    ``expanduser("~")/.dhole`` here, so a user who had moved their state dir got
    the repair written into the real home while everything else honoured the
    override - one product, two answers to "where is my state?".
    """
    dist = _dist_name()
    index_url = _index_url()
    from dhole_mcp import paths as _paths
    repair = str(_paths.file("repair.py"))
    try:
        os.makedirs(os.path.dirname(repair), exist_ok=True)
        with open(repair, "w", encoding="utf-8") as f:
            f.write(_repair_script_text(dist, index_url))
        _paths.harden_file(repair)
    except Exception:
        # Can't write repair.py - run pip directly as a last resort
        print("  recovering (direct reinstall)...")
        import subprocess
        cmd = [sys.executable, "-m", "pip", "install",
               "--force-reinstall", _pip_spec(dist), "--quiet", "--disable-pip-version-check"]
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
            print(f"  Fix manually: {sys.executable} -m pip install --force-reinstall '{_pip_spec(_dist_name())}'")
            return 1
        rc = _run_repair()
        if rc != 0:
            print(f"  If recovery failed, run: pip install --force-reinstall '{_pip_spec(_dist_name())}'")
        return rc
    except Exception:
        # Any other import-time crash (not a missing module) - re-raise
        # so real bugs surface, but only after trying repair as a last resort
        # if the error looks install-related.
        raise
