"""`dhole init` - one command for install + client registration (W8).

D3 was accepted: init may write to another application's MCP config, but
only behind an explicit ``--yes``, with a timestamped backup next to every
file it touches, and a dry-run default that prints the exact JSON it would
have written. "Convenience vs reaching into the user's machine" was the
question; the answer is "both, if the write is reversible and reviewed
beforehand".

Detection is by config-file existence (see SUPPORTED_CLIENTS below); a
client that is not installed simply does not have its file, so nothing is
proposed for it. When no client is found we exit 0 with the manual
instructions - the operator is on a host whose client we do not know, and
init's job ends at "here is what would be added".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional


# The MCP server block we want in every client's config. `command="dhole"`
# relies on the launcher being on PATH after `pip install dhole-mcp`; if it
# is not, the doctor / --version check inside init will say so.
DHOLE_SERVER_ENTRY: dict = {"command": "dhole", "args": []}


class ClientSpec:
    """One known MCP client and how to add a server to its config.

    ``json_pointer`` names where the ``mcpServers``-like dict lives inside
    the config file. Different clients nest it differently: Claude Desktop
    puts it at the top, Cursor wraps it under ``mcpServers``, VS Code under
    ``servers``. A single flat key would have been nicer, but this is
    what the clients actually ship.
    """

    def __init__(
        self,
        name: str,
        path_resolver: Callable[[], Optional[Path]],
        servers_key: str,
        root_must_be: Optional[str] = None,
    ) -> None:
        self.name = name
        self.path_resolver = path_resolver
        self.servers_key = servers_key
        # root_must_be: if the config needs a specific top-level key to
        # even exist (VS Code's ``{ "servers": {} }`` lives inside
        # ``{ "mcp": { ... } }`` in some versions), name it here.
        self.root_must_be = root_must_be

    def config_path(self) -> Optional[Path]:
        try:
            return self.path_resolver()
        except Exception:  # noqa: BLE001
            return None


def _home(*parts: str) -> Path:
    return Path.home().joinpath(*parts)


# Detection order is the order we print plans in. A user with two clients
# installed sees both.
SUPPORTED_CLIENTS: list[ClientSpec] = [
    ClientSpec(
        name="Claude Desktop",
        path_resolver=lambda: _claude_desktop_config(),
        servers_key="mcpServers",
    ),
    ClientSpec(
        name="Cursor",
        path_resolver=lambda: _home(".cursor", "mcp.json"),
        servers_key="mcpServers",
    ),
    ClientSpec(
        name="Windsurf",
        path_resolver=lambda: _home(".codeium", "windsurf", "mcp_config.json"),
        servers_key="mcpServers",
    ),
    ClientSpec(
        name="VS Code",
        path_resolver=lambda: _home(".vscode", "mcp.json"),
        servers_key="servers",
    ),
    # OpenCode keeps its config under a project-local path; skip for now.
]


def _claude_desktop_config() -> Path:
    """The MCP config Claude Desktop reads. Platform-specific."""
    if sys.platform == "darwin":
        return _home("Library", "Application Support", "Claude",
                     "claude_desktop_config.json")
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(_home("AppData", "Roaming"))
        return Path(base) / "Claude" / "claude_desktop_config.json"
    # Linux / *nix - some builds use ~/.config/claude-desktop.
    return _home(".config", "Claude", "claude_desktop_config.json")


def _existing_client_configs() -> list[tuple[ClientSpec, Path]]:
    """Only configs that already exist on disk. A missing file means the
    client is not installed (or has not been opened yet), and writing one
    would be creating state the user did not ask for. init deliberately
    refuses that: no client found -> exit 0 with the manual instructions."""
    out: list[tuple[ClientSpec, Path]] = []
    for spec in SUPPORTED_CLIENTS:
        path = spec.config_path()
        if path is not None and path.is_file():
            out.append((spec, path))
    return out


def _planned_write(path: Path, spec: ClientSpec) -> Optional[dict]:
    """Return the modified config dict for ``path``, or None if dhole is
    already registered (idempotent: a re-run adds nothing)."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        # Refusing to write over an unparsable config is deliberate - a
        # hand-corrupted file is the user's to fix, not init's to
        # paper over by replacing it.
        raise RuntimeError(
            f"{path} is not valid JSON ({exc}); fix or move it, then re-run"
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} top level must be a JSON object")
    servers = data.setdefault(spec.servers_key, {})
    if not isinstance(servers, dict):
        raise RuntimeError(
            f"{path} has {spec.servers_key!r} as a non-object; not overwriting"
        )
    if "dhole" in servers:
        return None
    servers["dhole"] = dict(DHOLE_SERVER_ENTRY)
    return data


def _playwright_chromium_installed() -> bool:
    """Heuristic: `playwright install --dry-run chromium` prints a target
    path; if that path exists we already have the binary. Returning True
    here means init skips step 3, which is the entire point (a fresh
    Chromium download is 150MB and slow)."""
    try:
        proc = subprocess.run(
            ["playwright", "install", "--dry-run", "chromium"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    # Dry-run output mentions "would install: <dir>" or lists the browser
    # path; if any line resolves to an existing directory we have it.
    for line in (proc.stdout + proc.stderr).splitlines():
        for token in line.replace("=", " ").split():
            candidate = token.strip("'\"")
            try:
                if candidate and Path(candidate).is_dir():
                    return True
            except OSError:
                pass
    return False


def _backup(path: Path) -> Path:
    """Copy the original config next to it with a timestamped ``.bak-``
    suffix. Init always creates a backup before writing - the plan's
    D3 answer is "yes but never silently", and a backup is what makes
    the "never" concrete."""
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = path.with_suffix(path.suffix + f".bak-{ts}")
    shutil.copy2(path, target)
    return target


def _print_manual_instructions() -> None:
    """Fallback when no known client is on disk. Exit code 0 - init did
    its job by *not* touching the machine; the operator just needs to
    paste the JSON themselves."""
    sample = json.dumps({
        "mcpServers": {"dhole": DHOLE_SERVER_ENTRY}
    }, indent=2)
    print("  no supported MCP client was detected on this machine.")
    print("  add dhole to your client's config by hand; the JSON is:")
    print()
    for line in sample.splitlines():
        print("  " + line)
    print()
    print("  the file location depends on the client; see README.md "
          "(§让 agent 自己装) or `dhole doctor` for the launch shape.")


def run_init(argv: list[str]) -> int:
    """`dhole init [flags]`. Returns a shell exit code.

    Steps, in order (plan §3 W8):
      1. Confirm the interpreter / launcher are resolvable.
      2. Verify the extras are installed (W7 made default = full, so this
         is a `python -c "import patchright"` check, not a pip call).
      3. Install the Chromium binary via playwright if missing.
      4. Detect installed MCP clients.
      5. Print the planned JSON (dry-run default).
      6. If ``--yes``, back up + write each config.
      7. Run ``dhole doctor`` so the user sees the outcome in the same
         output they already trust.
    """
    yes = "--yes" in argv or "-y" in argv
    from dhole_mcp import cli_ui as ui

    print(ui.branded(ui.cyan("dhole init")))

    # 1. Launcher sanity: the MCP config's `command: "dhole"` will only
    # work if the shim is on PATH. `sys.executable` tells us which Python
    # is running; the Scripts/bin dir next to it is where pip drops
    # dhole.exe / dhole.
    if shutil.which("dhole") is None:
        print("  " + ui.warn("dhole is not on PATH - run `pip install dhole-mcp` "
                              "on this interpreter first"))
        return 1

    # 2. Extras check. W7 folded the heavy tier into default deps, so
    # a plain `pip install dhole-mcp` gives us all of it. `slim` users
    # hit the browser-tier warning below.
    try:
        import patchright  # noqa: F401
        print("  " + ui.ok("browser deps present (patchright imports)"))
    except ImportError:
        print("  " + ui.warn("browser deps missing (patchright not importable)"))
        print("    fix: " + ui.cmd("pip install 'dhole-mcp[all]' (or `pip install dhole-mcp` "
                                    "on W7+ wheels)"))

    # 3. Playwright browser binary. Only run when --yes is on; a dry-run
    # must not fire a 150MB download behind the operator's back.
    have_browser = _playwright_chromium_installed()
    if have_browser:
        print("  " + ui.ok("chromium already installed"))
    else:
        line = "  chromium binary missing; "
        if yes:
            print(line + ui.cmd("playwright install chromium") + " ...")
            try:
                rc = subprocess.run(
                    ["playwright", "install", "chromium"], timeout=900).returncode
                if rc == 0:
                    print("  " + ui.ok("chromium installed"))
                else:
                    print("  " + ui.warn(f"playwright install exited {rc} - "
                                          "fetch it yourself before browser-tier calls"))
            except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
                print("  " + ui.warn(f"could not run playwright install: {exc}"))
        else:
            print(line + "dry-run: would run " + ui.cmd("playwright install chromium")
                  + " (add --yes to actually fetch it)")

    # 4. Client detection.
    found = _existing_client_configs()
    if not found:
        print()
        _print_manual_instructions()
        return 0

    print()
    print(ui.dim("detected " + str(len(found)) + " MCP client(s)"))

    # 5/6. Print plan, optionally write.
    wrote = 0
    skipped = 0
    for spec, path in found:
        try:
            planned = _planned_write(path, spec)
        except RuntimeError as exc:
            print(f"  " + ui.err(f"{spec.name}: {exc}"))
            continue
        if planned is None:
            print(f"  " + ui.dim(f"{spec.name}: already registered at {path}, skipping"))
            skipped += 1
            continue
        print(f"  " + ui.cyan(spec.name) + f" -> {path}")
        # Print exactly the JSON that would be written so the operator
        # can eyeball the diff before --yes.
        body = json.dumps(planned, indent=2)
        for line in body.splitlines():
            print("    " + line)
        if yes:
            backup = _backup(path)
            try:
                path.write_text(json.dumps(planned, indent=2) + "\n",
                                encoding="utf-8")
            except OSError as exc:
                print("    " + ui.err(f"write failed: {exc}; original preserved "
                                       f"at {backup}"))
                continue
            print("    " + ui.ok(f"wrote, backup at {backup}"))
            wrote += 1
        else:
            print("    (dry-run; add --yes to write)")

    if not yes and wrote == 0 and skipped < len(found):
        print()
        print("  " + ui.dim("no files were changed. re-run with "
                            + ui.cmd("--yes")
                            + " to apply the writes above."))

    # 7. Doctor, so the operator sees the shape of the whole system
    # without typing a second command.
    print()
    from dhole_mcp import updater
    updater.doctor()
    return 0
