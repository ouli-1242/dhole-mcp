"""Machine-readable registry for every ``DHOLE_*`` environment variable, plus a
process-wide warning list for values the readers could not make sense of.

Why one file does both (W1 + W4): ``security.py`` / ``robots.py`` / ``sessions.py``
read env vars *without* going through ``server.py``. If the warning list lived in
``server.py`` they would have to import it, and ``server.py`` already imports all
of them - a cycle. This module imports nothing from ``dhole_mcp.*``, so any module
can reach it.

W1 · ``ALL`` is the single source of truth: every ``DHOLE_*`` string literal in
``src/dhole_mcp/**.py`` must appear here (``tests/test_env_registry.py`` checks
both directions), and ``doctor`` / ``configuration.md`` derive from it rather
than hand-maintaining a parallel list that would drift.

W4 · ``read_flag`` / ``read_int`` / ``read_float`` preserve existing semantics
while recording unrecognised values into the module-level ``_warnings`` list so
``dhole --doctor`` (W2) can surface them. A number that falls back to the
default used to be a silent event; it is now visible in exactly one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

# ─── shared token sets (W4.3) ───────────────────────────────────────────
# ``ON_TOKENS`` / ``OFF_TOKENS`` are exported here so ``_env_flag``, ``robots``
# and ``security`` stop maintaining three near-duplicates of the same list (the
# third one, ``security._dns_recheck_enabled``, additionally omits ``"" / none /
# null``, which is what makes the asymmetry observable).
ON_TOKENS = frozenset({"1", "true", "yes", "on"})
OFF_TOKENS = frozenset({"0", "false", "no", "off", "none", "null"})


Kind = Literal[
    "flag",     # on/off env; see ``read_flag``
    "int",      # integer env; see ``read_int``
    "float",    # float env; see ``read_float``
    "str",      # free-form string
    "secret",   # API key - NEVER echoed by any consumer
    "hostlist", # comma-separated list of hostnames / names
    "path",     # filesystem path
    "url",      # URL
    "enum",     # value from a fixed set
]
ReadAt = Literal["import", "call"]
FailSafe = Literal["open", "closed"]


@dataclass(frozen=True)
class EnvVar:
    """One env variable, described for both humans and diagnostics.

    ``read_at`` is the field W2 needs most: "call" means editing the host's
    env block is picked up on the next tool call; "import" means the process
    has to restart, and getting this wrong is one of the top two reasons a
    user thinks their configuration change "did not take".

    ``fail_safe`` marks a variable where a wrong value has an unsafe failure
    direction (D1). Currently only 4 gates: robots / CDP attach / SSRF recheck
    / private-host allowlist. Everything else keeps W4.1's promise that no
    flag flips semantics.
    """

    name: str
    kind: Kind
    default: str
    read_at: ReadAt
    scenario: str
    summary: str
    bounds: Optional[tuple] = None
    fail_safe: Optional[FailSafe] = None
    read_by: tuple[str, ...] = ()

    def display_value(self, raw: Optional[str]) -> str:
        """How W2 should print the value of this env var.

        ``secret`` never shows its value - even a length read from ``len`` is
        fine but the bytes are not. ``hostlist``/``path``/``str``/``url`` show
        the raw value verbatim; the diagnostic never parses it.
        """
        if raw is None:
            return "<unset>"
        if self.kind == "secret":
            return f"set (len={len(raw)})"
        return raw


# ─── the 39 env variables (see plan appendix A) ─────────────────────────
ALL: tuple[EnvVar, ...] = (
    EnvVar(
        name="DHOLE_ALLOW_PRIVATE_HOSTS",
        kind="hostlist",
        default="",
        read_at="call",
        scenario="fetching intranet / local dev services",
        summary="Comma-separated hostnames exempted from the SSRF guard.",
        fail_safe="closed",
        read_by=("security", "server"),
    ),
    EnvVar(
        name="DHOLE_BOCHA_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Bocha search API key; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_BRIGHTDATA_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Bright Data SERP API key; keyed backend, opt-in.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_BRIGHTDATA_COUNTRY",
        kind="str",
        default="us",
        read_at="import",
        scenario="poor search results / want a different pool",
        summary="Google result region for Bright Data SERP calls.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_BRIGHTDATA_ZONE",
        kind="str",
        default="dhole",
        read_at="import",
        scenario="poor search results / want a different pool",
        summary="Bright Data zone name (routing profile).",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_BROWSER_IDLE_TIMEOUT",
        kind="int",
        default="300",
        read_at="import",
        scenario="browser overhead",
        summary="Seconds of idle before the warm stealthy browser is closed; 0 = never.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_BROWSER_PROFILE",
        kind="path",
        default="",
        read_at="call",
        scenario="pages behind a login wall - CDP attach (17.0)",
        summary="Named on-disk profile directory instead of a fresh temp dir.",
        read_by=("browser", "paths"),
    ),
    EnvVar(
        name="DHOLE_CDP_ATTACH",
        kind="flag",
        default="off",
        read_at="call",
        scenario="pages behind a login wall - CDP attach (17.0)",
        summary="Master switch that lets cdp_url attach a real Chrome.",
        fail_safe="closed",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_CDP_HOSTS",
        kind="hostlist",
        default="",
        read_at="call",
        scenario="pages behind a login wall - CDP attach (17.0)",
        summary="Host allowlist for CDP-attached fetches.",
        read_by=("browser", "server"),
    ),
    EnvVar(
        name="DHOLE_COOKIE_TTL_S",
        kind="float",
        default="86400",
        read_at="call",
        scenario="pages behind a login wall - CDP attach (17.0)",
        summary="HTTP-tier session cookie lifetime in seconds (60s..30d).",
        bounds=(60.0, 2592000.0),
        read_by=("sessions",),
    ),
    EnvVar(
        name="DHOLE_DEFAULT_CONTENT_CHARS",
        kind="int",
        default="40000",
        read_at="import",
        scenario="context overhead too high",
        summary="Default body budget when the caller did not pass max_content_chars.",
        bounds=(500, 200000),
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_DEFAULT_ENGINES",
        kind="hostlist",
        default="baidu,bing,sogou,bing_global,yandex,brave",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Override the keyless default engine pool.",
        read_by=("search_metasearch", "updater"),
    ),
    EnvVar(
        name="DHOLE_ENGINE_CHALLENGE_COOLDOWN",
        kind="int",
        default="600",
        read_at="call",
        scenario="cooldowns too frequent / pacing",
        summary="Backoff after 3 consecutive challenge pages (60..7200s).",
        bounds=(60, 7200),
        read_by=("search_metasearch", "updater"),
    ),
    EnvVar(
        name="DHOLE_ENGINE_CONN_COOLDOWN",
        kind="int",
        default="600",
        read_at="call",
        scenario="cooldowns too frequent / pacing",
        summary="Backoff after 3 consecutive connection failures (60..7200s).",
        bounds=(60, 7200),
        read_by=("search_metasearch", "updater"),
    ),
    EnvVar(
        name="DHOLE_ENGINE_COOLDOWN",
        kind="int",
        default="60",
        read_at="call",
        scenario="cooldowns too frequent / pacing",
        summary="Backoff after an ordinary block (5..1800s).",
        bounds=(5, 1800),
        read_by=("search_metasearch", "updater"),
    ),
    EnvVar(
        name="DHOLE_ENGINE_HEARTBEAT",
        kind="int",
        default="300",
        read_at="call",
        scenario="cooldowns too frequent / pacing",
        summary="Patrol interval for cooling engines; 0 disables probing.",
        read_by=("search_metasearch", "server", "updater"),
    ),
    EnvVar(
        name="DHOLE_EXA_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Exa search API key; keyed backend, opt-in.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_HF_ENDPOINT",
        kind="url",
        default="",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Hugging Face mirror for the rerank model download.",
        read_by=("reranker",),
    ),
    EnvVar(
        name="DHOLE_HOME",
        kind="path",
        default="~/.dhole",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="State directory (cache, cookie jar, proxy pool).",
        read_by=("cli", "paths", "search", "search_metasearch", "sessions", "updater"),
    ),
    EnvVar(
        name="DHOLE_IGNORE_ROBOTS",
        kind="flag",
        default="off",
        read_at="call",
        scenario="compliance / fetch behavior",
        summary="Disable robots.txt compliance process-wide.",
        fail_safe="closed",
        read_by=("robots", "server"),
    ),
    EnvVar(
        name="DHOLE_MULTIMODAL",
        kind="flag",
        default="off",
        read_at="call",
        scenario="page operation, screenshots an agent can see, semantic focus (17.0)",
        summary="Declare that the calling agent can see images.",
        read_by=("actions", "qr", "server"),
    ),
    EnvVar(
        name="DHOLE_NEURAL_FOCUS",
        kind="flag",
        default="off",
        read_at="call",
        scenario="page operation, screenshots an agent can see, semantic focus (17.0)",
        summary="Score focus blocks semantically with the local reranker.",
        read_by=("crawl", "focus", "server"),
    ),
    EnvVar(
        name="DHOLE_NO_AUTO_REPAIR",
        kind="flag",
        default="off",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Print the repair command instead of auto-reinstalling.",
        read_by=("cli",),
    ),
    EnvVar(
        name="DHOLE_NO_BROWSER_PREWARM",
        kind="flag",
        default="off",
        read_at="call",
        scenario="browser overhead",
        summary="Skip the startup browser warm-up.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_OUTPUT_SCHEMA",
        kind="flag",
        default="off",
        read_at="import",
        scenario="context overhead too high",
        summary="Declare outputSchema in tools/list; implies DHOLE_STRUCTURED_CONTENT.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_SEARCH_DEADLINE",
        kind="float",
        default="16",
        read_at="import",
        scenario="poor search results / want a different pool",
        summary="Whole-search hard deadline in seconds.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_SEARCH_FEEDBACK",
        kind="flag",
        default="off",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Rank domains that fetched well higher next time.",
        read_by=("search",),
    ),
    EnvVar(
        name="DHOLE_SEARCH_PROXY",
        kind="url",
        default="",
        read_at="call",
        scenario="search engines blocked / rate-limited / should use a proxy",
        summary="Search-layer proxy; comma-separated for rotation.",
        read_by=("errors", "search", "search_engines", "search_metasearch", "search_proxy", "server"),
    ),
    EnvVar(
        name="DHOLE_SSRF_DNS_RECHECK",
        kind="flag",
        default="on",
        read_at="call",
        scenario="Clash / sing-box TUN proxy (fake-IP mode)",
        summary="Set 0 to disable the DNS recheck layer (fake-IP resolvers).",
        fail_safe="closed",
        read_by=("security",),
    ),
    EnvVar(
        name="DHOLE_STRUCTURED_CONTENT",
        kind="flag",
        default="off",
        read_at="import",
        scenario="context overhead too high",
        summary="Emit the structuredContent channel in addition to text.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_TASKS",
        kind="flag",
        default="off",
        read_at="import",
        scenario="repeatable tasks and change monitoring (17.0 opt-in)",
        summary="Register the recipe and watch tools.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_TAVILY_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Tavily search API key; keyed backend, opt-in.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_TOOLS",
        kind="hostlist",
        default="",
        read_at="import",
        scenario="context overhead too high",
        summary="Register only the listed tools; a typo raises at import.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_UPDATE_INDEX_URL",
        kind="url",
        default="",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Alternative pip index for the self-update path.",
        read_by=("cli", "updater"),
    ),
    EnvVar(
        name="DHOLE_UPDATE_PACKAGE",
        kind="str",
        default="dhole-mcp",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Package name the self-update path installs.",
        read_by=("cli", "updater"),
    ),
    EnvVar(
        name="DHOLE_USAGE_LOG",
        kind="flag",
        default="off",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Write a local call log; no parameter values, no network.",
        read_by=("actions", "login", "server", "vault"),
    ),
    EnvVar(
        name="DHOLE_VAULT",
        kind="flag",
        default="off",
        read_at="import",
        scenario="signing in with a credential the user already stores (17.0 opt-in)",
        summary="Register the vault and login tools.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_WORKDIR",
        kind="path",
        default="",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Extra search root for parse's relative paths.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_WIRE_FULL",
        kind="flag",
        default="off",
        read_at="import",
        scenario="context overhead too high",
        summary="Restore the pre-slim response shape (every field always present).",
        read_by=("server",),
    ),
)

_BY_NAME: dict[str, EnvVar] = {e.name: e for e in ALL}


def by_name(name: str) -> Optional[EnvVar]:
    return _BY_NAME.get(name)


# ─── process-wide warning list (W4.2) ────────────────────────────────────
@dataclass(frozen=True)
class EnvWarning:
    """A value that reached one of the readers but could not be parsed.

    ``raw_value`` is the string as it appeared in the environment; we never
    rewrite or normalise it. W2 prints these with the offending env name so
    the user can see "you set X to abc and we used 40000 instead" instead of
    living with a silent fallback.
    """

    name: str
    raw_value: str
    message: str


_warnings: list[EnvWarning] = []


def record_warning(name: str, raw_value: str, message: str) -> None:
    """Append an unrecognised-value warning. Never raises; never echoes secrets."""
    _warnings.append(EnvWarning(name=name, raw_value=raw_value, message=message))


def warnings_snapshot() -> tuple[EnvWarning, ...]:
    """Read-only view of the warning list - W2 iterates this in doctor()."""
    return tuple(_warnings)


def clear_warnings() -> None:
    """Drop every recorded warning. Intended for tests, not for the server."""
    _warnings.clear()


# ─── where a value comes from ────────────────────────────────────────────
#
# Two doors to one knob. `env` stays the highest-precedence source because that
# is what every existing install already configured (and what the README's
# example block writes), but an env block in an MCP client config means editing
# JSON and restarting the host to change one number — which is the reason the
# knobs read per-call stop being configuration and start being infrastructure.
# `~/.dhole/config.json` is the same knobs without the restart: it is re-read
# when its mtime changes, so `dhole config set DHOLE_DEFAULT_CONTENT_CHARS 8000`
# takes effect on the next call.
#
# What does NOT move: DHOLE_HOME, because it locates this file (bootstrap), and
# anything read at import time (DHOLE_TOOLS and friends) — a per-call re-read
# cannot un-register a tool that was already left off `tools/list`. Those entries
# stay writable here, but `dhole config` says `restart the host` on them and
# `dhole --doctor` shows the same note.

_CONFIG_BASENAME = "config.json"

#: Knobs that stay in the environment and are deliberately NOT readable from the
#: persisted file. Each one is here for a structural reason, not taste:
#:
#:   DHOLE_HOME              locates the file, so reading it from the file is a
#:                           circle (and would silently ignore the override that
#:                           tests and sandboxes depend on).
#:   DHOLE_TOOLS             a whitelist: honouring it from a file a caller cannot
#:                           see would drop tools silently for someone who set one
#:                           env entry. It belongs to the host's visible env block.
#:   DHOLE_UPDATE_PACKAGE    the recovery path. The repair script runs before the
#:   DHOLE_UPDATE_INDEX_URL  package may be importable at all, and it has to know
#:   DHOLE_NO_AUTO_REPAIR    which distribution to reinstall from a state directory
#:                           that may itself be the thing that is broken.
ENV_ONLY = frozenset({
    "DHOLE_HOME", "DHOLE_TOOLS", "DHOLE_UPDATE_PACKAGE",
    "DHOLE_UPDATE_INDEX_URL", "DHOLE_NO_AUTO_REPAIR",
})

_file_cache: dict[str, str] | None = None
_file_stamp: tuple[float, int] | None = None


def config_path():
    """``~/.dhole/config.json`` (honours DHOLE_HOME). Imported lazily: this module
    must stay importable before the state directory exists."""
    from dhole_mcp import paths
    return paths.home() / _CONFIG_BASENAME


def _load_file() -> dict[str, str]:
    """The file as a flat ``{NAME: "value"}``, re-read when it changes.

    A broken file is a recorded warning, never a crash and never a silent no-op:
    one stray brace must not disable every knob in it without saying so, and it
    must not take the server down either.
    """
    global _file_cache, _file_stamp
    import json
    path = config_path()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime, stat.st_size)
    except OSError:
        _file_cache, _file_stamp = {}, None
        return _file_cache
    if _file_cache is not None and stamp == _file_stamp:
        return _file_cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a bad config must not break reads
        record_warning(_CONFIG_BASENAME, "",
                       f"{path} is not readable JSON ({type(exc).__name__}) - "
                       "every value in it is being IGNORED; fix or delete the file")
        _file_cache, _file_stamp = {}, None
        return _file_cache
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, bool):
                flat[str(key)] = "1" if value else "0"
            elif value is None or isinstance(value, (str, int, float)):
                flat[str(key)] = str(value)
    _file_cache, _file_stamp = flat, stamp
    return flat


def file_entries() -> dict[str, str]:
    """Values the persisted config currently contributes (for CLI / doctor)."""
    return dict(_load_file())


def source_of(name: str) -> str:
    """`env` / `file` / `unset` — which door this knob came out of."""
    if name in os.environ:
        return "env"
    if name in _load_file():
        return "file"
    return "unset"


def value_of(name: str) -> Optional[str]:
    """The one lookup every typed reader goes through: env wins, then the file."""
    value = os.environ.get(name)
    if value is not None:
        return value
    return _load_file().get(name)


def set_entry(name: str, value: str):
    """Persist one knob. Raises on a name that is not a registered knob.

    Refusing an unknown name is the whole point: `DHOLE_DEFUALT_CONTENT_CHARS`
    written to a file looks exactly like a working setting, and the only honest
    moment to catch it is the moment it is written.
    """
    import difflib
    import json
    from dhole_mcp import paths

    if by_name(name) is None:
        near = difflib.get_close_matches(name, [e.name for e in ALL], n=2, cutoff=0.6)
        raise ValueError(
            f"{name!r} is not a dhole configuration variable "
            f"({len(ALL)} registered - see `dhole config list`)"
            + (f"; did you mean {', '.join(near)}?" if near else "")
        )
    path = config_path()
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001 - overwrite a broken file, said out loud
            pass
    data[name] = value
    paths.ensure_private_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.harden_file(path)
    invalidate_file_cache()
    return path


def unset_entry(name: str) -> bool:
    """Remove one knob from the file. True when something was removed."""
    import json
    path = config_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, dict) or name not in data:
        return False
    data.pop(name)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    invalidate_file_cache()
    return True


def invalidate_file_cache() -> None:
    """Drop the cached file. Tests point DHOLE_HOME at a throwaway directory, and
    a cache keyed by mtime alone can survive that on a coarse-grained clock."""
    global _file_cache, _file_stamp
    _file_cache, _file_stamp = None, None


# ─── typed readers ───────────────────────────────────────────────────────
#
# W4.1 keeps every existing semantic unchanged for the non-safety flags - the
# whole point is that "an operator has been using DHOLE_MULTIMODAL=yes-please"
# must not silently lose the capability when we add diagnostics. Only the 4
# safety gates (D1, marked ``fail_safe="closed"``) get the direction correction.


def read_flag(name: str, default: bool = False, *, fail_safe: bool = False) -> bool:
    """Read an on/off env var. See ``EnvVar.fail_safe`` for the two directions.

    ``fail_safe=False`` (matches ``_env_flag``): unset -> ``default``; empty
    or in ``OFF_TOKENS`` -> False; anything else -> True, with a warning
    recorded for values that are neither ``ON_TOKENS`` nor ``OFF_TOKENS``.

    ``fail_safe=True`` (safety gates): the value must be in ``ON_TOKENS`` or
    ``OFF_TOKENS``; anything unrecognised is read as False, so a typo like
    ``flase`` on DHOLE_IGNORE_ROBOTS keeps compliance ON instead of turning it
    OFF silently.
    """
    raw = value_of(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v == "":
        # Empty matches _env_flag's "in off-list" branch - False without a
        # warning (the user did say "off", just with an empty string).
        return False
    if v in ON_TOKENS:
        return True
    if v in OFF_TOKENS:
        return False
    if fail_safe:
        record_warning(
            name, raw,
            f"{raw!r} is not a recognised on/off value - treated as OFF (fail-safe)",
        )
        return False
    record_warning(
        name, raw,
        f"{raw!r} is not a recognised on/off value - treated as ON",
    )
    return True


def read_int(
    name: str,
    default: int,
    *,
    lo: Optional[int] = None,
    hi: Optional[int] = None,
) -> int:
    """Read an integer env var, clamped when bounds are declared.

    Preserves ``_env_int`` semantics: a value that fails ``int(float(raw))``
    returns ``default``. What is new: the failure is recorded so ``doctor``
    can tell the operator instead of leaving them guessing.
    """
    raw = value_of(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        record_warning(
            name, raw,
            f"{raw!r} is not an integer - using default {default}",
        )
        return default
    if lo is not None and val < lo:
        record_warning(
            name, raw,
            f"{raw!r}={val} is below the minimum {lo} - using {lo}",
        )
        return lo
    if hi is not None and val > hi:
        record_warning(
            name, raw,
            f"{raw!r}={val} is above the maximum {hi} - using {hi}",
        )
        return hi
    return val


def read_float(
    name: str,
    default: float,
    *,
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> float:
    """Float sibling of ``read_int``. Same clamping and warning rules."""
    raw = value_of(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        record_warning(
            name, raw,
            f"{raw!r} is not a number - using default {default:g}",
        )
        return default
    if lo is not None and val < lo:
        record_warning(
            name, raw,
            f"{raw!r}={val:g} is below the minimum {lo:g} - using {lo:g}",
        )
        return lo
    if hi is not None and val > hi:
        record_warning(
            name, raw,
            f"{raw!r}={val:g} is above the maximum {hi:g} - using {hi:g}",
        )
        return hi
    return val


# ─── validity predicate used by W2's config audit ───────────────────────
def is_valid_value(entry: EnvVar, raw: str) -> tuple[bool, str]:
    """Decide whether ``raw`` (a string) parses under ``entry.kind``.

    Returns ``(ok, why)``. ``why`` is the human-readable reason on failure;
    empty on success. W2 uses this to colour a set-but-invalid env as ✗ and
    to build the ``fix:`` line without duplicating the parse rules here.
    """
    if raw is None:
        return True, ""
    v = raw.strip().lower()
    if entry.kind == "flag":
        if v == "" or v in ON_TOKENS or v in OFF_TOKENS:
            return True, ""
        return False, "not a recognised on/off value (expected one of 1/true/yes/on or 0/false/no/off/none/null)"
    if entry.kind == "int":
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            return False, "not an integer"
        if entry.bounds:
            lo, hi = entry.bounds
            if val < lo or val > hi:
                return False, f"outside {lo}..{hi}"
        return True, ""
    if entry.kind == "float":
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return False, "not a number"
        if entry.bounds:
            lo, hi = entry.bounds
            if val < lo or val > hi:
                return False, f"outside {lo:g}..{hi:g}"
        return True, ""
    # str / secret / hostlist / path / url / enum: every non-empty string is
    # structurally valid; enum bounds check below if set.
    if entry.kind == "enum" and entry.bounds:
        allowed = entry.bounds
        if isinstance(allowed, Iterable) and v not in {str(a).lower() for a in allowed}:
            return False, f"must be one of {sorted(allowed)}"
    return True, ""
