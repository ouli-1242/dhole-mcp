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
import re
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


#: The two targets ``--probe`` touches. Named here so the flag's blast radius is
#: one glanceable line rather than a URL buried in a call.
PROBE_URL = "https://example.com/"

#: Where a debug-enabled Chrome listens by convention. `dhole init --cdp` only
#: asks this one port a yes/no question, and never sends it an HTTP request.
CDP_DEFAULT_PORT = 9222

#: Cloud metadata endpoints are never allowed under any setting (the SSRF guard
#: says so at every layer), so a guided command must not offer to write one into
#: an allowlist that hands over the user's authenticated browser cookies.
class CdpHostError(ValueError):
    """A host the guided command refuses to write into the CDP allowlist."""

_CDP_NEVER_HOSTS = frozenset({
    "169.254.169.254", "metadata.google.internal", "metadata",
    "instance.local", "fd00:ec2::254",
})


def _is_link_local(host: str) -> bool:
    """True for an IP literal in a range the SSRF guard never lets through."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved


def _take_value(argv: list[str], name: str) -> Optional[str]:
    """``--cdp host1,host2`` and ``--cdp=host1,host2`` both spell the same thing.

    Returns None when the flag is absent, "" when it is present with nothing
    after it - the two cases print different messages (an unknown flag versus a
    command that was cut off mid-typing).
    """
    prefix = name + "="
    for arg in argv:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            return argv[i + 1]
        return ""
    return None


def _cdp_hostnames(raw: str) -> list[str]:
    """Turn what the user pasted into what ``DHOLE_CDP_HOSTS`` actually holds.

    The fence is an EXACT hostname match (browser.py: entry check and the route
    handler both compare ``urlparse(...).hostname not in allowed_hosts``), so an
    address-bar string has to be reduced to its host before it can be written --
    and the reduced form has to be echoed back. "I allowlisted example.com and it
    still says cdp_host_not_allowed for www.example.com" is otherwise a support
    conversation nobody can predict.
    """
    from urllib.parse import urlparse

    out: list[str] = []
    for token in re.split(r"[,;]", raw or ""):
        token = token.strip().lower()
        if not token:
            continue
        if "*" in token or "?" in token.split("/")[0]:
            raise CdpHostError(
                f"{token!r} is a pattern, not a host. The fence is one exact "
                "hostname per entry - name each host you want reachable "
                "(www.example.com and example.com are two entries).")
        parsed = urlparse(token if "://" in token else "//" + token)
        host = (parsed.hostname or "").strip()
        if not host:
            raise CdpHostError(
                f"{token!r} has no hostname in it - pass a host or a full URL "
                "like portal.example.com or https://portal.example.com/dashboard")
        if host in _CDP_NEVER_HOSTS or host.endswith(".internal") or _is_link_local(host):
            raise CdpHostError(
                f"{host} is a cloud metadata / link-local address. Those are never "
                "allowed under any setting, and an attached browser carries your "
                "logged-in cookies - the guard refuses it, so this command will not "
                "help you route around it.")
        if host not in out:
            out.append(host)
    return out


def _cdp_endpoint_listening(port: int = CDP_DEFAULT_PORT):
    """Is a Chrome debug endpoint actually listening on loopback? True/False/None.

    A TCP connect, 200ms, and NO HTTP request: ``/json/version`` would hand this
    process the titles and URLs of the user's open tabs, which is more than a
    readiness check needs. A refused connection is the honest False; anything
else (a permission error, an odd sandbox) is None - unknown is reported as
    unknown, never as "not running".
    """
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except ConnectionRefusedError:
        return False
    except socket.timeout:
        return False
    except OSError:
        return None
    except Exception:  # noqa: BLE001 - a readiness probe never raises
        return None


def _cdp_setup(ui, hosts: list[str], yes: bool) -> None:
    """Write the two gates and print the boundary this command is turning on.

    Attach is the one capability in dhole that hands over *authenticated read
    access to every site logged into that browser*, which is a bigger deal than
    the write confirmations elsewhere: those cover a known action, this covers
    unknown reads. So the command refuses to turn it on without naming hosts --
    the fence is the point, not an extra.
    """
    from dhole_mcp import env_registry as er
    from dhole_mcp import paths

    print()
    print(ui.cyan("cdp attach") + ui.dim(
        " - the fetch runs inside YOUR Chrome, with its logged-in cookies"))
    print("  " + ui.dim("hosts this command will allow (exact hostnames):"))
    for host in hosts:
        print("    " + ui.cmd(host))
    print("  " + ui.dim("subdomains and third-party assets are NOT covered - a page "
                        "that loads scripts from cdn.example.net needs that host named "
                        "too, or those requests are aborted at the route level"))

    # The env block outranks the file, on purpose - so a name already set there is
    # reported rather than shadowed by a write that would look like a setting.
    existing_env = {n: er.source_of(n) for n in ("DHOLE_CDP_ATTACH", "DHOLE_CDP_HOSTS")}
    merged = list(hosts)
    for host in _cdp_hostnames(er.value_of("DHOLE_CDP_HOSTS") or ""):
        if host not in merged:
            merged.append(host)
    if merged != hosts:
        print("  " + ui.dim(f"merged with the allowlist already set -> {len(merged)} host(s): ")
              + ui.cmd(",".join(merged)))

    for name in ("DHOLE_CDP_ATTACH", "DHOLE_CDP_HOSTS"):
        if existing_env[name] == "env":
            print("  " + ui.dim(f"{name} already set in the host's env block (env wins) "
                                "- nothing to write"))

    to_write = {"DHOLE_CDP_HOSTS": ",".join(merged)}
    if existing_env["DHOLE_CDP_ATTACH"] != "env":
        to_write["DHOLE_CDP_ATTACH"] = "1"
    if not yes:
        print("  " + ui.dim("dry-run: would write ") + ui.cmd(
            "; ".join(f"{k}={v}" for k, v in to_write.items()))
              + ui.dim(f" to {er.config_path().name} (add --yes)"))
    else:
        for name, value in to_write.items():
            er.set_entry(name, value)
            print("  " + ui.ok(f"{name} -> {er.config_path().name}"))
        print("  " + ui.dim("read per call - no host restart needed"))

    listening = _cdp_endpoint_listening()
    if listening is True:
        print("  " + ui.ok(f"a debug endpoint is listening on 127.0.0.1:{CDP_DEFAULT_PORT}")
              + ui.dim("  (nothing was requested from it - the check is a TCP connect)"))
    elif listening is None:
        print("  " + ui.dim(f"could not check 127.0.0.1:{CDP_DEFAULT_PORT} from here - "
                            "not a verdict, start the browser and try a call"))
    else:
        print("  " + ui.warn(f"nothing is listening on 127.0.0.1:{CDP_DEFAULT_PORT} yet"))
    profile = paths.home() / "cdp-profile"
    print("  " + ui.dim("the browser has to be started by YOU (Chrome 136+ refuses "
                        "debugging on the default profile):"))
    print("    " + ui.cmd(f'chrome --user-data-dir="{profile}" '
                          f"--remote-debugging-port={CDP_DEFAULT_PORT}"))
    print("  " + ui.dim("log into the target site in that window once, then the call is "
                        "per-call, e.g."))
    print("    " + ui.cmd('{"url": "https://' + (hosts[0] if hosts else "portal.example.com")
                          + '/dashboard", "options": {"cdp_url": "http://127.0.0.1:'
                          + str(CDP_DEFAULT_PORT) + '", "check_login": true}}'))
    print("  " + ui.dim("every attach says cdp_attached=true in its response and is "
                        "audit-logged; cookie VALUES never leave the browser (check_login "
                        "reports names only). DataDome / Akamai / interactive Turnstile / "
                        "2FA are still the user's to solve"))
    if yes and not listening:
        print("  " + ui.dim("-> next: start that window, then `dhole init --probe` or one "
                            "real cdp_url call to prove it end to end"))



def _ms(value) -> str:
    """A wall-clock duration the way a human reads it.

    A first neural search loads (or downloads) the reranker, which legitimately
    takes minutes; `219626ms` reads like a bug in the tool, `219.6s` reads like
    what it is - and the next line of this output is the thing to look at.
    """
    try:
        ms = float(value or 0)
    except (TypeError, ValueError):
        return "?"
    return f"{ms / 1000:.1f}s" if ms >= 3000 else f"{ms:.0f}ms"


def _run_probe(ui) -> None:
    """Ask the live code instead of the static checks: does it work from here?

    ``dhole --doctor`` proves the install is wired correctly. It cannot prove a
    search returns anything from this IP, or that a public page is fetchable
    here - and those are the two questions a first-time user actually has. So
    this runs one real search and one real fetch through the same dispatcher an
    agent would hit.

    Opt-in because it sends real traffic: every line names the target it touched.
    An empty or failed result is reported as a fact with the next step, never as
    "the install is broken" - a machine behind a rate-limited IP or offline is a
    working install in an unfriendly network.
    """
    import asyncio

    print()
    print(ui.cyan("live probe") + ui.dim(" - real requests; nothing is cached (cache_ttl=0)"))
    try:
        from dhole_mcp import server as _server
    except Exception as exc:  # noqa: BLE001 - a broken import is the finding
        print("  " + ui.err(f"cannot load the server here: {type(exc).__name__}: {exc}"))
        return
    srv = _server.MasterFetchServer(cache_ttl=0)

    async def _call(name: str, args: dict):
        out = await srv._dispatch(name, args)
        return out[1] if isinstance(out, tuple) else out

    try:
        search = asyncio.run(_call("smart_search", {
            "query": "dhole mcp", "options": {"max_results": 3}}))
    except Exception as exc:  # noqa: BLE001
        search = {"error": f"{type(exc).__name__}: {exc}"}
    hits = search.get("results") or []
    if search.get("error"):
        print("  " + ui.warn(f"search: {search['error'][:120]}")
              + ui.dim("  -> next: dhole engines probe (who is answering from this IP)"))
    else:
        used = search.get("engines_used") or []
        print("  " + ui.ok(f"search: {len(hits)} results for 'dhole mcp'")
              + ui.dim(f"  engines {len(used)} | rerank {search.get('rerank_mode', '?')}"
                       f" | {_ms(search.get('duration_ms'))}"))
        if not hits:
            print("    " + ui.dim("empty pool: every keyless engine declined or was cooling - "
                                  "try `dhole engines probe`, or set a search proxy"))

    try:
        fetch = asyncio.run(_call("smart_fetch", {"url": PROBE_URL}))
    except Exception as exc:  # noqa: BLE001
        fetch = {"error": f"{type(exc).__name__}: {exc}"}
    if fetch.get("error"):
        print("  " + ui.warn(f"fetch {PROBE_URL}: {str(fetch['error'])[:120]}"))
        posture = fetch.get("next_action") or ""
        if posture:
            print("    " + ui.dim(str(posture)[:200]))
    else:
        print("  " + ui.ok(f"fetch {PROBE_URL}: {fetch.get('status')} "
                           f"{fetch.get('total_extracted_chars', 0)} chars "
                           f"via {fetch.get('fetcher_used', '?')}")
              + ui.dim(f"  | {_ms(fetch.get('duration_ms'))}"))
    note = (fetch.get("metadata") or {}).get("ssrf_recheck")
    if note:
        print("  " + ui.dim(str(note)[:170]))
    print("  " + ui.dim("a failing fetch here is a network fact, not an install fault - "
                        "`dhole --doctor` above checks the install itself"))


def _reranker_model(ui, yes: bool) -> None:
    """The reranker that neural search and neural focus share.

    Downloaded only under ``--yes``, for the same reason Chromium is: a few
    hundred MB fetched behind a command nobody re-reads is the thing the design
    notes call a 惊吓, not a fallback. Writing ``DHOLE_NEURAL_FOCUS=1`` without
    the file present would leave the user holding a switch whose every call is
    an error naming a model they have never seen, so the flag and the weight
    belong to one command.
    """
    from dhole_mcp import reranker as _rr

    model = _rr.active_model()
    try:
        present = _rr.model_present()
    except Exception:  # noqa: BLE001 - a missing/broken state dir is "not present"
        present = False
    if present:
        print("  " + ui.ok(f"reranker already downloaded ({model.name})"))
        return
    approx_mb = max(1, int(model.approx_bytes) // 1_000_000)
    if not yes:
        print("  " + ui.dim(f"reranker {_rr.DEFAULT_MODEL} missing; dry-run: would download "
                            f"(~{approx_mb}MB, resumable) into ")
              + ui.cmd(str(_rr.active_model_dir()))
              + ui.dim(" (add --yes)"))
        return
    print("  " + ui.cmd(f"downloading reranker {model.name} (~{approx_mb}MB, resumable)")
          + " ...")
    import asyncio
    try:
        reranker = asyncio.run(_rr.ensure_reranker(download=True))
    except Exception as exc:  # noqa: BLE001 - a failed fetch is reported, not raised
        print("  " + ui.warn(f"reranker download failed: {type(exc).__name__}: {exc}")
              + ui.dim("  -> the flag is written, so neural focus calls will name "
                       "this gap until the model lands (it retries on the first "
                       "neural search, or re-run this command with --yes)"))
        return
    if reranker is None:
        print("  " + ui.warn("reranker could not be loaded after the download - "
                             "check `dhole --doctor` output below"))
    else:
        print("  " + ui.ok(f"reranker ready ({model.name})"))


def run_init(argv: list[str]) -> int:
    """`dhole init [flags]`. Returns a shell exit code.

    Steps, in order (plan §3 W8):
      1. Confirm the interpreter / launcher are resolvable.
      2. Verify the extras are installed (W7 made default = full, so this
         is a `python -c "import patchright"` check, not a pip call).
      3. Install the Chromium binary via playwright if missing.
      3.5 With --tasks / --vault / --neural, enable the gated capabilities
          through ~/.dhole/config.json (the file door from W11 - no host JSON
          edit). --neural additionally fetches the reranker the flag needs.
      3.7 With --cdp <host>,host, turn CDP attach on AND fence it to those
          hosts, print the browser launch line, and check whether a debug
          endpoint is actually listening. The hosts are validated before
          anything is written or downloaded: half a command is a half-installed
          security boundary.
      4. Detect installed MCP clients.
      5. Print the planned JSON (dry-run default).
      6. If ``--yes``, back up + write each config.
      6.5 With --probe, run one real search and one real fetch through the
          same dispatcher an agent uses (it sends traffic; every line names
          the target).
      7. Run ``dhole doctor`` so the user sees the outcome in the same
         output they already trust.
    """
    yes = "--yes" in argv or "-y" in argv
    probe = "--probe" in argv
    want = {"--tasks": "DHOLE_TASKS", "--vault": "DHOLE_VAULT",
            "--neural": "DHOLE_NEURAL_FOCUS"}
    enable = [env for flag, env in want.items() if flag in argv]
    unknown = [a for a in argv
               if a.startswith("-") and a not in ("--yes", "-y", "--probe", *want)
               and a != "--cdp" and not a.startswith("--cdp=")]
    if unknown:
        # A flag that is silently ignored is how "I typed --task and nothing
        # happened" becomes a support conversation.
        from dhole_mcp import cli_ui as _ui
        print(_ui.err(f"unknown flag(s): {' '.join(unknown)} "
                      "(try: --yes, --probe, --tasks, --vault, --neural, --cdp <host>)"))
        return 2
    from dhole_mcp import cli_ui as ui

    # --cdp is validated up front, before the Chromium download and before any
    # write: this flag turns on a security boundary, so "it failed at host 2 of
    # 3, ATTACH is already on" is the one outcome this command must not produce.
    cdp_raw = _take_value(argv, "--cdp")
    cdp_hosts: list[str] = []
    if cdp_raw is not None:
        if not cdp_raw.strip():
            print(ui.err("usage: dhole init --cdp <host>[,<host>...] - at least one "
                         "host is required, because attaching your browser without a "
                         "host fence is not something this command offers (set the two "
                         "variables yourself if you really want the un-fenced shape)"))
            return 2
        try:
            cdp_hosts = _cdp_hostnames(cdp_raw)
        except CdpHostError as exc:
            print(ui.err(f"refused: {exc}"))
            return 2

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

    # 3.5 Opt-in capability groups. These live in ~/.dhole/config.json now that
    # the file door exists (W11), so turning `recipe` / `watch` or `vault` /
    # `login` on stopped being an exercise in editing the host's JSON. They are
    # still read at import, so the restart note is not decoration - and neither
    # is the consent question: DHOLE_VAULT puts a tool that reads the user's
    # password manager onto the wire.
    if enable:
        from dhole_mcp import env_registry as _reg
        print()
        for env_name in enable:
            # Which note is true depends on when the knob is read: the tool
            # groups are decided at import (so tools/list needs a restart), a
            # per-call flag takes the next call. Saying "restart" for both is the
            # kind of advice that gets followed and changes nothing.
            entry = _reg.by_name(env_name)
            needs_restart = entry is None or entry.read_at == "import"
            note = ("restart the host so tools/list picks the tools up" if needs_restart
                    else "takes effect on the next call")
            current = _reg.source_of(env_name)
            if current == "env":
                print("  " + ui.dim(f"{env_name} already set in the host's env block "
                                    "(env wins) - nothing to write"))
                continue
            if current == "file":
                print("  " + ui.ok(f"{env_name} already enabled in "
                                   f"{_reg.config_path().name}  · {note}"))
                continue
            if not yes:
                print("  " + ui.dim(f"dry-run: would write {env_name}=1 to ")
                      + ui.cmd(str(_reg.config_path()))
                      + ui.dim(f" (add --yes), then {'restart the host' if needs_restart else 'it applies to the next call'}"))
                continue
            _reg.set_entry(env_name, "1")
            print("  " + ui.ok(f"{env_name}=1 -> {_reg.config_path().name}")
                  + ui.dim("  " + note))

    # 3.6 --neural also needs the weights, and DHOLE_NEURAL_FOCUS is read per call,
    # so unlike the two tool groups above it does not wait on a restart - it waits
    # on the model file. Fetch it here rather than leaving a flag whose every call
    # answers neural_focus_model_missing.
    if "--neural" in argv:
        _reranker_model(ui, yes)

    # 3.7 CDP attach: the two gates plus the boundary they draw, said out loud.
    if cdp_hosts:
        _cdp_setup(ui, cdp_hosts, yes)

    # 4. Client detection.
    found = _existing_client_configs()
    if not found:
        print()
        _print_manual_instructions()
        if probe:
            _run_probe(ui)
        print()
        from dhole_mcp import updater
        updater.doctor()
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
            print("  " + ui.err(f"{spec.name}: {exc}"))
            continue
        if planned is None:
            print("  " + ui.dim(f"{spec.name}: already registered at {path}, skipping"))
            skipped += 1
            continue
        print("  " + ui.cyan(spec.name) + f" -> {path}")
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

    # 7. Optional live probe, then doctor - so the operator sees the shape of
    # the whole system without typing a second command.
    if probe:
        _run_probe(ui)
    print()
    from dhole_mcp import updater
    updater.doctor()
    return 0
