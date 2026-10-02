"""Diagnostics for a dhole install: the version panel, the capability panel, doctor.

Before 17.0 B2-3 this module also owned self-update and self-repair. `dhole -u`
drove a detached Windows helper (wait for the launcher to exit, dodge the
dhole.exe file lock by renaming it, run pip, retry with --force-reinstall) plus a
standalone ~/.dhole/repair.py that taskkilled every dhole process and reinstalled
a few hundred MB from the index. The trigger was ANY ImportError - including one
raised by an unrelated package in the same environment. The cost was out of
proportion to the benefit (§3-34), so the whole mechanism is gone: this module
now only reads, and the most side effect it will produce is a `tasklist` that
matches process names.

What stays, and why:

- ``dhole -v`` - in a silent-degradation design the diagnostics command is the
  only place a user can see which tier is actually missing (no patchright means
  HTTP-only fetch, no onnxruntime means consensus-order search).
- ``dhole doctor`` - whether the install is the one that loads, which file that
  is, what the config wrote, what the resolver actually answers; every failure
  prints a command the human can run. Printing it is the whole point: executing
  it is the human's decision.
- version truth - ``__version__`` is authoritative and dist-info is compared
  against it, because an editable install makes dist-info lie (measured: 16.2
  code, 16.1 banner).
"""

from __future__ import annotations

import os
import pathlib
import sys

from dhole_mcp import env_registry

from dhole_mcp import paths

__all__ = ["installed_version", "print_version", "print_capabilities",
           "capabilities", "doctor"]


# 这是一个个人衍生版（of dondai1234/master-fetch），14.0 从 hound-mcp 改名为
# dhole-mcp，因为旧名在 PyPI 上撞车。17.0 写死：``DHOLE_UPDATE_PACKAGE`` /
# ``DHOLE_UPDATE_INDEX_URL`` 已注销（运行时静默忽略）—— 本模块不再从任何索引装东西。
_DIST_NAME = "dhole-mcp"   # the distribution this fork is installed as


# ─── version ──────────────────────────────────────────────────────────────

def installed_version() -> str:
    """当前版本。

    ``dhole_mcp.__version__`` 是唯一权威（pyproject 与 CHANGELOG 头部都这么声明）；
    importlib.metadata 读的是安装那一刻的 dist-info，editable 安装下源码升版后
    它会说谎，所以只作兜底。两者都拿不到就说 unknown，不猜。
    """
    try:
        from dhole_mcp import __version__ as runtime
        if runtime:
            return runtime
    except Exception:  # noqa: BLE001
        pass
    try:
        from importlib.metadata import version as _meta
        return _meta(_DIST_NAME)
    except Exception:  # noqa: BLE001
        return "unknown"


# ─── launcher + process helpers (只读：找到 launcher、数有几个在跑) ────
# ─── launcher + process helpers (Windows file-lock handling) ───────────────

def _dhole_launcher_path() -> str | None:
    """Locate the dhole launcher (dhole.exe on Windows, `dhole` on POSIX)."""
    import shutil
    candidate = shutil.which("dhole")
    if candidate and os.path.exists(candidate):
        return candidate
    scripts_dir = os.path.join(os.path.dirname(sys.executable), "Scripts")
    for name in ("dhole.exe", "dhole"):
        fb = os.path.join(scripts_dir, name)
        if os.path.exists(fb):
            return fb
    posix_bin = os.path.dirname(sys.executable)
    posix_fallback = os.path.join(posix_bin, "dhole")
    if os.path.exists(posix_fallback):
        return posix_fallback
    return None


def _other_dhole_pids() -> list[int]:
    """PIDs of OTHER running dhole launcher processes (excludes this one)."""
    import subprocess
    my_pid = os.getpid()
    pids: list[int] = []
    try:
        if sys.platform == "win32":
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq dhole.exe", "/FO", "CSV", "/NH"],
                text=True, timeout=10, creationflags=0x08000000,  # CREATE_NO_WINDOW
                # Windows 控制台程序按 OEM 代码页输出，中文系统上是 GBK，而
                # Python 的 UTF-8 模式会把 text=True 的解码器设成 utf-8 —— 于是
                # 「没有匹配进程」时 tasklist 的中文提示会解码失败。失败点在读
                # 取线程里，check_output 只会抛出无关的 TypeError。我们要的
                # dhole.exe 和 PID 全是 ASCII，所以替换掉坏字节即可。
                errors="replace",
            )
            for line in out.splitlines():
                parts = [p.strip().strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == "dhole.exe":
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        continue
                    if pid != my_pid:
                        pids.append(pid)
        else:
            out = subprocess.check_output(["ps", "-eo", "pid=,comm="], text=True,
                                          timeout=10, errors="replace")
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    pid_s, comm = line.split(None, 1)
                    pid = int(pid_s)
                except ValueError:
                    continue
                if os.path.basename(comm.strip()) == "dhole" and pid != my_pid:
                    pids.append(pid)
    except Exception:
        return []
    return pids


# ─── public commands ───────────────────────────────────────────────────────

def print_version() -> None:
    """Render `dhole -v`: the version panel, then what this install can actually do."""
    _print_version_panel()
    print_capabilities()


def _print_version_panel() -> None:
    """版本面板：我是谁、第几版、以及这一版该怎么升 —— 只打印，不执行。

    过去这里会先联网问 PyPI"最新是哪版"，然后印出 "up to date" / "vX available" 并
    给一句 `dhole -u`。三条都不成立了：这个 fork 不发 PyPI（问了也只会拿到上游
    dondai1234/master-fetch 的版本，那不是可比对象），而 `-u` 已经删掉。所以现在
    说的是一句能照着做的话，而不是一个状态。
    """
    from dhole_mcp import cli_ui as ui
    W = 50
    inner = W - 4
    print(ui.panel([
        ui.lr(ui.wordmark(), "", inner),
        ui.lr(ui.ver(installed_version()), ui.dim("no self-update"), inner),
    ], W))
    print("  " + ui.dim("personal fork - update by hand with")
          + "  " + ui.cmd('git pull && python -m pip install -e ".[all]"'))


# ─── capability report (`dhole -v`) ─────────────────────────────────────────
#
# Every optional piece below turns a headline feature OFF WITHOUT AN ERROR:
# without patchright/playwright the fetch pipeline is HTTP-only, without
# onnxruntime/tokenizers (or before the ~90MB model is downloaded) search falls
# back to consensus order, and all of them live in the [all] extra only. In a
# silent-degradation design the diagnostics command is the one place a user can
# see which parts are actually in place - otherwise the tool just looks "fine
# but worse" forever.

def _has_module(name: str) -> bool:
    """True if `name` is importable, without importing it."""
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _engine_yield_row() -> tuple[str, str, bool] | None:
    """每个引擎最近一轮的产出 —— 静默降级唯一能被看见的地方。

    刻意不 import 搜索层：诊断命令要在精简安装 / 半坏安装上也能跑（那时才最需要
    看它），而 search_metasearch 会拉 primp/lxml/httpx/fake_useragent。这里只做
    文件系统读，判据与 _classify_yield 保持同构。
    """
    try:
        import json

        from dhole_mcp import paths
        path = paths.file("engine_stats.json")
        if not os.path.exists(path):
            return ("engine yield", "no data yet (run a search first)", True)
        with open(path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        if not isinstance(stats, dict) or not stats:
            return ("engine yield", "no data yet (run a search first)", True)
        import time as _t
        now = _t.time()

        def _num(key: str, default: float) -> float:
            # 不能用 `st.get(k) or default`：0 在这里是**最有意义**的值（0 条可用
            # 产出正是漂移），`or` 会把它悄悄变成"没有观测"。
            try:
                return float(st.get(key, default))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return default

        parts: list[str] = []
        suspect = False
        for name in sorted(stats):
            st = stats[name]
            if not isinstance(st, dict):
                continue
            status = str(st.get("status", ""))
            nodes = int(_num("last_nodes", -1))
            usable = int(_num("last", -1))
            mean = _num("mean", 0.0)
            drift = int(_num("drift", 0))
            if now - _num("ts", 0.0) > 3600:
                parts.append(f"{name}: no recent run")
                continue
            if status in ("blocked", "circuit_open"):
                parts.append(f"{name}: blocked/cooled")
            elif status == "timeout":
                parts.append(f"{name}: timeout")
            elif status.startswith(("error", "init_error", "no_key")):
                parts.append(f"{name}: unreachable")
            elif status == "preempted":
                parts.append(f"{name}: not asked")
            elif nodes < 0:
                parts.append(f"{name}: {usable if usable >= 0 else 0} results")
            elif nodes > 0 and usable == 0:
                suspect = True
                parts.append(f"{name}: PARSER BROKEN ({nodes} item nodes, 0 usable)"
                             + (" [confirmed]" if drift >= 2 else " [suspect]"))
            elif nodes == 0 and usable == 0:
                parts.append(f"{name}: 0 nodes (usually {mean:.1f}/run)" if mean >= 1
                             else f"{name}: 0 this query")
            else:
                parts.append(f"{name}: {usable} usable ({mean:.1f}/run avg)")
        if not parts:
            return None
        return ("engine yield", " | ".join(parts), not suspect)
    except Exception:
        return None


def _cooldown_config() -> dict:
    """The backoff tiers + heartbeat in force (G6).

    17.0 写死：三个冷却档与心跳都是能算清的数，``DHOLE_ENGINE_*`` 旋钮已注销
    （运行时静默忽略），这里只镜像 search_metasearch 的固定值供 `dhole engines
    list` 展示。保持 stdlib-only：这条命令必须能在半坏安装上跑，而那正是用户
    想看超时配置的时候。
    """
    tiers = {
        "block": 60.0,
        "challenge": 600.0,
        "connection": 600.0,
    }
    return {"tiers": tiers, "heartbeat": 300.0, "note": ""}


def _engine_cooldowns() -> dict[str, float]:
    """{engine: seconds left} from circuit_breaker.json, expired entries dropped.

    Stdlib-only on purpose (like _engine_yield_row): the doctor has to run on a
    half-broken install, and importing the search layer pulls primp/lxml/httpx.
    """
    try:
        import json
        import time

        from dhole_mcp import paths
        path = paths.file("circuit_breaker.json")
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        now_ts = time.time()
        return {k: round(v - now_ts, 1) for k, v in data.items()
                if isinstance(v, (int, float)) and v > now_ts}
    except Exception:
        return {}


def _append_engine_yield(caps: list[tuple[str, str, bool]]) -> None:
    """capabilities() 有三条提前返回的分支，产出行每条都得看到。"""
    row = _engine_yield_row()
    if row:
        caps.append(row)
    cooldowns = _engine_cooldowns()
    if cooldowns:
        caps.append((
            "engine cooldowns",
            " | ".join(f"{n}: {int(s)}s left" for n, s in sorted(cooldowns.items()))
            + " - they expire on their own; `dhole engines reset` (or cache_clear "
              "engine_state=true) clears them now",
            True,
        ))


def capabilities() -> list[tuple[str, str, bool]]:
    """[(label, state, ok)] for the optional capabilities that degrade silently."""
    caps: list[tuple[str, str, bool]] = []

    browser = _has_module("patchright") and _has_module("playwright")
    caps.append((
        "browser tier",
        "ready (anti-bot / JS rendering / screenshot)" if browser
        else "missing - HTTP-only (pip install 'dhole-mcp[all]', playwright install chromium)",
        browser,
    ))

    pdf = _has_module("pdfplumber") or _has_module("pypdfium2")
    caps.append((
        "pdf / ocr",
        "ready" if pdf else "missing (pip install 'dhole-mcp[all]')",
        pdf,
    ))

    if not (_has_module("onnxruntime") and _has_module("tokenizers")):
        caps.append(("neural rerank", "missing (pip install 'dhole-mcp[all]')", False))
        _append_engine_yield(caps)
        return caps
    try:
        from dhole_mcp.reranker import active_model, active_model_dir
        model = active_model()
        d = active_model_dir()
        ready = ((d / "model.onnx").exists()
                 and (d / "tokenizer.json").exists())
    except Exception:
        caps.append(("neural rerank", "config unreadable - check ~/.dhole/config/reranker.json", False))
        _append_engine_yield(caps)
        return caps
    if ready:
        caps.append((
            "neural rerank",
            f"ready ({model.name}: {model.label})",
            True,
        ))
    else:
        caps.append((
            "neural rerank",
            f"{model.name} not downloaded yet "
            f"(~{model.approx_bytes // 1_000_000}MB, resumable; "
            "~/.dhole/config/reranker.json switches model)",
            False,
        ))

    # 这行以前是写死的字面量，换引擎池时漏改过一次（`dhole -v` 报旧池）——现在从
    # DEFAULT_ENGINES 推导，由 tests/test_cli.py 的 pool 断言钉住。
    pool = (env_registry.value_of("DHOLE_DEFAULT_ENGINES") or "").strip()
    if pool:
        caps.append(("search pool", pool, True))
    else:
        try:
            from dhole_mcp.search_engines import DEFAULT_ENGINES, _CN_DIRECT
            vpn = [e for e in DEFAULT_ENGINES if e not in _CN_DIRECT]
            note = f" (default; {','.join(vpn)} need VPN in CN)" if vpn else " (default)"
            pool_line = ",".join(DEFAULT_ENGINES) + note
        except Exception:
            pool_line = "unreadable - check dhole_mcp.search_engines"
        caps.append(("search pool", pool_line, False))
    _append_engine_yield(caps)
    return caps


def print_capabilities() -> None:
    """Print one line per optional capability. Never raises - diagnostics must
    not be the thing that crashes when an install is half-broken."""
    try:
        from dhole_mcp import cli_ui as ui
        print("  " + ui.dim("capabilities (each missing row degrades silently):"))
        for label, state, ok in capabilities():
            mark = ui.ok(state) if ok else ui.warn(state)
            print("    " + label.ljust(15) + " " + mark)
    except Exception:
        pass


def _config_effective_value(entry, raw: str) -> str:
    """W2 · how to display a value the operator has set, WITHOUT echoing
    the raw string for kinds where the raw itself is sensitive.

    ``secret`` - the four API keys - never appears in doctor output; only
    ``set (len=N)``. ``hostlist / path / url / str`` also skip the echo
    because a hostname / filesystem path / URL / package name is
    user-specific state that doctor should not lift onto the terminal or
    into a bug-report paste. ``int``/``float``/``flag`` show the parsed
    effective value (a number, or ``on``/``off``) - safe, and matches
    "生效值" in the plan's format.

    A raw string is echoed only for invalid entries (see the ``fail``
    branch in ``_append_config_audit``), because the operator needs to
    see the typo to fix it.
    """
    from dhole_mcp import env_registry as _reg
    kind = entry.kind
    if kind == "secret":
        return f"set (len={len(raw)})"
    if kind == "flag":
        v = raw.strip().lower()
        if v in _reg.ON_TOKENS:
            return "on"
        if v in _reg.OFF_TOKENS or v == "":
            return "off"
        return "on"   # non-fail-safe flags still resolve to True (see _env_flag)
    if kind in ("int", "float"):
        try:
            val = float(raw)
            if kind == "int":
                return str(int(val))
            return f"{val:g}"
        except (TypeError, ValueError):
            return raw  # invalid path already covered; keep display stable
    if kind == "hostlist":
        parts = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
        return f"{len(parts)} entries" if parts else "empty"
    # str / path / url / enum: presence indicator only.
    return "set"


def _fix_line_for(entry) -> str:
    """Actionable repair wording for a config-audit failure.

    Mirrors the plan's W2 example verbatim:
    ``fix: set DHOLE_DEFAULT_CONTENT_CHARS to an integer in 500..200000, or unset it``
    """
    kind = entry.kind
    if kind == "int":
        rng = f" in {entry.bounds[0]}..{entry.bounds[1]}" if entry.bounds else ""
        return f"set {entry.name} to an integer{rng}, or unset it"
    if kind == "float":
        rng = (f" in {entry.bounds[0]:g}..{entry.bounds[1]:g}"
               if entry.bounds else "")
        return f"set {entry.name} to a number{rng}, or unset it"
    if kind == "flag":
        return (f"set {entry.name} to one of 1/true/yes/on or "
                f"0/false/no/off/none/null, or unset it")
    return f"unset {entry.name}, or set it to a valid value"


def _append_config_audit(checks: list[tuple[str, str, str, str]]) -> None:
    """W2 · add one row per DHOLE_* variable the operator actually set.

    Unset variables are skipped - 39 rows of "unset" is noise the plan
    explicitly wants to avoid. Rows are grouped by scenario (env name is
    secondary sort) so someone debugging "why did my proxy setting not
    take" reads them in a coherent order rather than in registry order.

    Invalid values go into the shared ``checks`` list with state ``fail``
    so ``doctor``'s top-line counter and its ``1 if failures else 0``
    exit code both see them; valid values use state ``info`` (visible,
    not alarming). A single summary line uses state ``ok`` so a fully
    clean machine still reads green.
    """
    from dhole_mcp import env_registry as _reg

    persisted = _reg.file_entries()
    set_entries: list[tuple[str, str, object, str]] = []
    for entry in _reg.ALL:
        # Two doors, one audit: env wins over ~/.dhole/config.json, and `value_of`
        # is exactly what the typed readers consult - so what doctor prints is the
        # value in effect, not the value one of the two doors happens to hold.
        raw = _reg.value_of(entry.name)
        if raw is None or raw.strip() == "":
            continue
        set_entries.append((entry.scenario, entry.name, entry, raw))

    # A name in the file that no code reads is the loudest kind of silence: it
    # looks like a setting and does nothing. Nothing above can see it, so it
    # needs its own row - and it must count as a failure, not a note.
    for orphan in sorted(k for k in persisted if _reg.by_name(k) is None):
        checks.append((
            orphan, "fail",
            f"{orphan} is written to {_reg.config_path()} but is not a dhole "
            f"configuration variable - nothing reads it",
            f"fix: dhole config unset {orphan}",
        ))

    # §3-20：注销的 DHOLE_* 旋钮运行时静默忽略，唯一会提到它们的地方是
    # 这里。动态扫 os.environ（不点名任何已删旋钮），注册表里没有的名字
    # —— 无论注销还是拼错 —— 都得到同一句实话：设了也没人读。
    # 放在 "none set 早退" 之前：只设了已注销旋钮的机器最需要这一行。
    known = {e.name for e in _reg.ALL}
    stale_names = sorted(n for n in os.environ
                         if n.startswith("DHOLE_") and n not in known)
    if stale_names:
        checks.append((
            "retired env vars", "info",
            f"{len(stale_names)} DHOLE_* name(s) set in the env block that no "
            f"longer exist in 17.0 and are ignored: "
            + ", ".join(stale_names[:6])
            + ("…" if len(stale_names) > 6 else "")
            + " - remove them (their behavior is fixed in code now)",
            "dhole config list shows the 11 names that are still read"))

    total = len(_reg.ALL)
    if not set_entries:
        checks.append((
            "config", "ok",
            f"none set - dhole runs on defaults ({total} available)",
            "",
        ))
        return

    set_entries.sort(key=lambda x: (x[0], x[1]))
    for _scenario, name, entry, raw in set_entries:
        ok, why = _reg.is_valid_value(entry, raw)
        timing = ("  (read at startup - restart the host to change)"
                  if entry.read_at == "import" else "")
        if entry.name not in os.environ and entry.name in persisted:
            timing += "  (from ~/.dhole/config.json)"
        if ok:
            detail = _config_effective_value(entry, raw) + timing
            checks.append((name, "info", detail, ""))
        else:
            default = entry.default or "the default"
            msg = f"{raw!r} is {why} - using default {default}"
            checks.append((name, "fail", msg, _fix_line_for(entry)))

    # The one capability where "it works" and "it is safe" are different
    # statements: attaching your real Chrome hands every request the attached
    # browser makes the cookies of every site the user is logged into. 17.0
    # 先问再记（§3-16）：attach 没有总开关，围栏是落盘的已授权主机集合 —— 从未
    # 授权过的主机第一次 attach 会被拒绝并等待 confirm，所以这里报告的是磁盘上
    # 的授权现实，而不是任何配置项。保持 stdlib-only：直接读
    # ~/.dhole/cdp_hosts.json（server._cdp_authorized_hosts 的镜像）。
    try:
        import json as _json
        from dhole_mcp import paths as _paths
        _cdp_path = pathlib.Path(_paths.file("cdp_hosts.json"))
        _cdp_hosts: list = []
        if _cdp_path.exists():
            _cdp_hosts = sorted(str(h).strip().lower()
                                for h in (_json.loads(_cdp_path.read_text(encoding="utf-8"))
                                          .get("hosts") or []) if str(h).strip())
    except Exception:
        _cdp_hosts = []
    if _cdp_hosts:
        if len(_cdp_hosts) == 1:
            listing = _cdp_hosts[0]
        else:
            listing = (", ".join(_cdp_hosts[:3])
                       + (", …" if len(_cdp_hosts) > 3 else ""))
        checks.append((
            "cdp fence", "info",
            f"CDP attach is authorized for {len(_cdp_hosts)} "
            f"{'host: ' + listing if len(_cdp_hosts) == 1 else 'hosts: ' + listing}; "
            "every attach is fenced to these - a new host is refused once and "
            "asks for confirm first",
            f"revoke: delete cdp_hosts.json in {_cdp_path.parent}"))
    else:
        checks.append((
            "cdp fence", "ok",
            "no CDP host authorized yet - the first attach to your real Chrome "
            "is refused with an authorization ask (nothing executes until you "
            "confirm it)",
            ""))

    checks.append((
        "config", "ok",
        f"{len(set_entries)} of {total} variables set "
        f"(run dhole --doctor after editing the host's env block)",
        "",
    ))


_FAKE_IP_NETWORK = None  # lazily constructed to keep import cheap


def _fake_ip_network():
    """``198.18.0.0/15`` - the RFC 2544 benchmarking block.

    Clash / mihomo / sing-box TUN with a fake-ip resolver hands every public
    hostname an address from this range by design; a genuine public resolver
    never does. That asymmetry makes the block a specific enough signal that
    doctor can name the environment instead of leaving the user to guess.
    """
    global _FAKE_IP_NETWORK
    if _FAKE_IP_NETWORK is None:
        import ipaddress
        _FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
    return _FAKE_IP_NETWORK


def _resolve_probe() -> str:
    """The domain doctor resolves to sniff for a fake-ip resolver.

    Hard-coded so a change to the probe target has to go through review;
    a machine that cannot resolve ANY name is offline, and the check falls
    through to an ``info`` line rather than a failure.
    """
    return "example.com"


def _append_resolver_reality(checks: list[tuple[str, str, str, str]]) -> None:
    """W3 · tell the operator they are behind a fake-ip proxy, in the words
    they can act on.

    This is the fix for the plan §1.5 falsified README claim ("zero-config
    works by default"). On a Clash TUN + fake-ip resolver, the DNS recheck
    in ``security._resolves_to_internal`` refuses every public URL because
    the AAAA record lands in ``fc00::/7`` - the IPv4 answer in
    ``198.18.0.0/15`` alone would NOT trip it (that block is deliberately
    absent from ``_PRIVATE_NETWORKS``). The failure looks identical to "the
    product is broken" from the outside; naming the resolver class and the
    specific network turns it into a copy-pasteable setting change.

    Never counts as a failure. A machine with no network, a machine with a
    plain HTTP proxy, and a fake-ip machine whose auto-skip has engaged all
    read as either ``ok`` or ``info`` here.
    """
    import ipaddress
    import socket

    probe = _resolve_probe()
    try:
        infos = socket.getaddrinfo(probe, None)
    except Exception as exc:  # noqa: BLE001
        # Offline / resolver broken: report what we saw, do NOT count as a
        # failure - "the network is down" is not an install problem.
        checks.append((
            "resolver reality", "info",
            f"probe {probe} unresolved: {type(exc).__name__} (offline?)",
            "",
        ))
        return

    v4_in_fake_ip_block: str | None = None
    v6_in_ula: str | None = None
    fake_net = _fake_ip_network()
    ula = ipaddress.ip_network("fc00::/7")
    for info in infos:
        try:
            addr = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            continue
        if isinstance(addr, ipaddress.IPv4Address):
            if addr in fake_net and v4_in_fake_ip_block is None:
                v4_in_fake_ip_block = str(addr)
        else:
            if addr in ula and v6_in_ula is None:
                v6_in_ula = str(addr)

    if v4_in_fake_ip_block is None:
        # Normal public answer. Healthy.
        checks.append((
            "resolver reality", "ok",
            f"{probe} resolves to a public answer",
            "",
        ))
        return

    # fake-ip detected. 17.0：旋钮已注销，姿态只有一个来源 —— 开机探测的自动
    # 判定（security.probe_fake_ip），doctor 不再替用户读任何环境变量。
    # （行宽预算 280 字符，超了会被截头 —— 两段 detail 都要短。）
    from dhole_mcp import security as _sec
    if not _sec._dns_recheck_enabled():
        detail = (f"fake-ip proxy detected ({probe} -> {v4_in_fake_ip_block} in "
                  f"198.18.0.0/15); the DNS-recheck layer is skipped "
                  f"automatically on this host, so URLs work as-is "
                  f"(metadata.ssrf_recheck says so; IP-literal, hostname and "
                  f"cloud-metadata guards stay on)")
        if v6_in_ula:
            detail += f"; AAAA {v6_in_ula} in fc00::/7"
        checks.append(("resolver reality", "ok", detail, ""))
        return

    # 探测到了 fake-ip，但复查层在本进程仍然开着：两处探测结果不一致（例如解析
    # 器只对部分名字撒谎）。没有旋钮可关 —— 让用户重启，交给开机探测重新判定。
    checks.append((
        "resolver reality", "info",
        f"fake-ip proxy detected ({probe} -> {v4_in_fake_ip_block} in "
        f"198.18.0.0/15); the DNS-recheck layer is still on for this process",
        "restart dhole so the startup probe re-judges this resolver "
        "(no manual override exists; IP-literal, hostname and cloud-metadata "
        "guards always stay on)",
    ))


def doctor() -> int:
    """Proactive health check: diagnose a half-broken install and name the fix.

    ``-v`` says what this install *can* do. ``doctor`` additionally checks
    install integrity - launcher on PATH, which module file actually loads,
    metadata drift, stale launcher/processes, writable state dir - and prints a
    copy-pasteable repair command for every failure. Returns a shell exit code
    (0 = healthy, 1 = something needs attention) so it can gate a script.

    The "module loaded from" line exists because this project's tests are the
    thing most easily fooled: with a built wheel installed instead of an
    editable install, pytest silently exercises site-packages while the editor
    shows src/. Naming the resolved file makes that visible in one line.
    """
    from dhole_mcp import cli_ui as ui
    from importlib.metadata import version as _meta_version

    def _short(p: str, w: int = 44) -> str:
        if not p:
            return ""
        home = os.path.expanduser("~")
        if p.startswith(home):
            p = "~" + p[len(home):]
        return p if len(p) <= w else "..." + p[-(w - 3):]

    # (label, state, detail, fix) - state is "ok" | "fail" | "info"
    checks: list[tuple[str, str, str, str]] = []

    # ── install integrity ────────────────────────────────────────────────
    exe = _dhole_launcher_path()
    checks.append((
        "launcher resolves", "ok" if exe else "fail",
        _short(exe) if exe else "dhole is not on PATH",
        "" if exe else "python -m pip install -e .",
    ))

    mod_ver: str | None = None
    try:
        import dhole_mcp as _dm
        mod_ver = getattr(_dm, "__version__", "?")
        checks.append(("package imports", "ok", mod_ver, ""))
        checks.append(("module loaded from", "info", _short(_dm.__file__ or ""), ""))
    except Exception as exc:  # noqa: BLE001
        checks.append((
            "package imports", "fail",
            f"{type(exc).__name__}: {exc}"[:60],
            "python -m pip install -e .",
        ))

    try:
        meta_ver = _meta_version(_DIST_NAME)
        same = mod_ver is not None and meta_ver == mod_ver
        checks.append((
            "metadata consistent", "ok" if same else "fail",
            meta_ver if same else f"installed {meta_ver} vs module {mod_ver}",
            "" if same else "python -m pip install -e .",
        ))
    except Exception:  # noqa: BLE001
        checks.append((
            "metadata consistent", "fail", "package metadata missing",
            "python -m pip install -e .",
        ))

    stale_exe = ""
    if exe and sys.platform == "win32" and os.path.exists(exe + ".old"):
        try:
            os.remove(exe + ".old")
        except OSError:
            stale_exe = "dhole.exe.old locked by a running server"
    checks.append(("launcher clean", "ok" if not stale_exe else "info",
                   stale_exe or "no stale .old", ""))

    try:
        stale_pids = _other_dhole_pids()
    except Exception:  # noqa: BLE001
        stale_pids = []
    checks.append((
        "no stale servers", "ok" if not stale_pids else "info",
        "none running" if not stale_pids
        else f"{len(stale_pids)} running: PID " + ", ".join(str(p) for p in stale_pids[:4]),
        "",
    ))

    missing_core = [m for m in ("httpx", "aiosqlite", "mcp", "pydantic")
                    if not _has_module(m)]
    checks.append((
        "core dependencies", "ok" if not missing_core else "fail",
        "ok" if not missing_core else "missing: " + ", ".join(missing_core),
        "" if not missing_core else "python -m pip install -e .",
    ))

    # ── writable state ───────────────────────────────────────────────────
    try:
        home = paths.home()
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("state dir writable", "ok", _short(str(home)), ""))
    except Exception as exc:  # noqa: BLE001
        checks.append((
            "state dir writable", "fail", f"{type(exc).__name__}: {exc}"[:60],
            "set DHOLE_HOME to a writable path",
        ))

    try:
        from dhole_mcp import search_proxy
        proxy, source = search_proxy.configured_proxy()
        if proxy:
            checks.append(("search proxy", "info",
                           f"{search_proxy.redact(proxy)} (from {source})",
                           "TCP reachability is what `dhole --doctor` proves next; "
                           "17.0 has one egress, no pool"))
        else:
            checks.append(("search proxy", "info", "none (direct connection)",
                           "set DHOLE_SEARCH_PROXY when your IP is being throttled"))
    except Exception as exc:  # noqa: BLE001
        checks.append(("search proxy", "info", f"check failed: {exc}"[:60], ""))

    # ── config audit (W2) ────────────────────────────────────────────────
    # The whole section runs inside try/except because a doctor() that
    # raises is worse than a doctor() with a missing section - the tests
    # test_never_raises_and_returns_an_exit_code and the plan's W2 "doctor
    # 依然绝不许抛异常" clause both pin this.
    try:
        _append_config_audit(checks)
    except Exception as exc:  # noqa: BLE001
        checks.append(("config audit", "info", f"skipped: {exc}"[:60], ""))

    # ── resolver reality (W3) ────────────────────────────────────────────
    # The plan's §1.5 - README's "zero-config works" was falsified on a
    # fake-ip TUN setup, and every URL tool failed with what looked like
    # a product bug. This check names the environment so the operator
    # sees "your proxy is set up this way, here is the switch" instead
    # of an unexplained SecurityError from every fetch. Never counts as
    # a failure - offline / broken-DNS read as info, not red.
    try:
        _append_resolver_reality(checks)
    except Exception as exc:  # noqa: BLE001
        checks.append(("resolver reality", "info", f"skipped: {exc}"[:60], ""))

    # ── render ───────────────────────────────────────────────────────────
    failures = [c for c in checks if c[1] == "fail"]
    head = (ui.err(f"{len(failures)} issue(s) found") if failures
            else ui.ok("all healthy"))
    print(ui.branded(head))
    label_width = max((len(c[0]) for c in checks), default=21)
    label_width = max(21, min(32, label_width))
    for label, state, detail, fix in checks:
        if state == "ok":
            mark = ui._sty(ui._glyph("\u2713", "+"), ui._GREEN)
        elif state == "fail":
            mark = ui._sty(ui._glyph("\u2717", "x"), ui._RED)
        else:
            mark = ui._sty(ui._glyph("!", "!"), ui._MAGENTA)
        print(f"  {mark} {label:<{label_width}} {ui.dim(_short(detail, w=280))}")
        if fix:
            print(f"      {ui.dim('fix:')} {ui.cmd(fix)}")

    print()
    print_capabilities()

    if failures:
        print()
        print("  " + ui.warn("run the fix above, then") + "  "
              + ui.cmd("dhole --doctor"))
    return 1 if failures else 0


