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

from dhole_mcp import install

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
        # 只印还存在的引擎。engine_stats.json 是历史累加，17.0 删掉的 6 家后端
        # 在里面仍各有一条 —— 点名一个已经不存在的引擎，用户会去 `engines=` 里
        # 指它，然后收到一句看起来像我们坏了的 invalid_request。
        from dhole_mcp.search_engines import _INDEX_FAMILY
        known = set(_INDEX_FAMILY)
        retired = [n for n in sorted(stats) if n not in known]
        for name in sorted(stats):
            if name not in known:
                continue
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
        if retired:
            parts.append(f"(+{len(retired)} record(s) from backends 17.0 deleted)")
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
        from dhole_mcp.search_engines import _INDEX_FAMILY
        known = set(_INDEX_FAMILY)
        return {k: round(v - now_ts, 1) for k, v in data.items()
                if k in known and isinstance(v, (int, float)) and v > now_ts}
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
    ocr = _has_module("rapidocr") or _has_module("rapidocr_onnxruntime")
    caps.append((
        "pdf / ocr",
        "ready" if (pdf and ocr) else (
            "pdf only - no OCR, scanned pages come back empty "
            "(pip install 'dhole-mcp[all]')" if pdf else
            "missing (pip install 'dhole-mcp[all]')"),
        pdf and ocr,
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
        if entry.name in os.environ and entry.name in persisted:
            # 6 · 两扇门同时开着：谁赢必须是这行话说出来的，而不是让人回去猜。
            timing += "  (env SHADOWS the value in ~/.dhole/config.json)"
        elif entry.name not in os.environ and entry.name in persisted:
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


# ─── doctor 的检查项（B2-5：计划里的 12 项，重排而不是沿用旧表） ─────────────
#
# 每项一个独立、绝不抛异常的 `_append_*(checks)`。把顺序本身当文档：从"这台机器
# 能不能跑"读到"这次连接花了多少 token"，一个人顺着读下来就是他的排查顺序。

#: 会真起子进程、会真发消息的两项（5 宿主握手 / 10 实搜实抓）由这个开关决定。
#: 测试里由 conftest 置 False —— 不是为了省事：那两项跑一次几秒且依赖当场的网络，
#: 让它们进入每条 doctor 断言，得到的将是关于宽带而不是关于产品的断言。真实运行
#: 始终是 True，并由一条 e2e 用例证明它没有被静默跳过。
LIVE_CHECKS = True


def _live_checks_enabled() -> bool:
    return bool(LIVE_CHECKS)


def _append_runtime(checks) -> None:
    """1 · Python 与平台 —— "为什么没有浏览器"的最上游答案常常不在 dhole 里。"""
    ver = ".".join(str(x) for x in sys.version_info[:3])
    too_old = sys.version_info < (3, 11)
    plat = f"{sys.platform}, {os.name}"
    note = ""
    try:
        import platform as _pf
        machine = (_pf.machine() or "").lower()
        plat = f"{plat}, {machine}"
        if "com.termux" in os.environ.get("PREFIX", ""):
            note = " - Termux: playwright ships no wheel here, so no browser tier"
        elif machine in ("aarch64", "arm64") and sys.platform == "linux":
            note = " - arm64 linux: confirm a chromium build exists before using the browser tier"
    except Exception:  # noqa: BLE001
        pass
    checks.append((
        "python + platform", "fail" if too_old else "ok",
        f"{ver} on {plat}{note}",
        "run dhole on CPython >= 3.11" if too_old else "",
    ))


#: 分层依赖：缺哪一层就少哪一档能力。能力面板说"浏览器档没了"，这一行说少的是哪个包。
#: 每个槽位是一组**可替代**的包名（ocr 有 v3 与 legacy v1 两个发行名，pdf 有两个后端），
#: 全部可替代名都缺才算这个槽位缺 —— 按 AND 判定会把装全的机器报成缺层。
_LAYERS = (
    ("http", (("primp",), ("httpx",), ("trafilatura",))),
    ("browser", (("patchright",), ("playwright",))),
    ("pdf", (("pdfplumber", "pypdfium2"),)),
    ("ocr", (("rapidocr", "rapidocr_onnxruntime"), ("cv2",))),
    ("rerank", (("onnxruntime",), ("tokenizers",))),
)


def _layer_present(slots) -> str:
    """这层齐不齐；不齐时返回第一个补不上的包名。"""
    for slot in slots:
        if not any(_has_module(m) for m in slot):
            return slot[0]
    return ""


def _append_dependency_layers(checks) -> None:
    """2 · 依赖分层：五层一次看完。"""
    core = [m for m in ("httpx", "aiosqlite", "mcp", "pydantic")
            if not _has_module(m)]
    if core:
        checks.append((
            "core dependencies", "fail", "missing: " + ", ".join(core),
            'python -m pip install -e ".[all]"',
        ))
        return
    absent = [f"{label} tier needs {gap}"
              for label, slots in _LAYERS
              for gap in [_layer_present(slots)] if gap]
    checks.append((
        "dependency layers", "ok" if not absent else "info",
        "all five tiers present" if not absent else "; ".join(absent),
        "" if not absent else 'python -m pip install -e ".[all]" then restart the host',
    ))


def _append_chromium(checks) -> None:
    """3 · 浏览器二进制的真实状态（17.0 B3 之后才有"真实"可言）。

    三件此前做不到、也做错过的事：
      * 期望版本要问 driver 的 browsers.json。磁盘上最高的那个 chromium-XXXX 不算答案
        —— pip 升级过 patchright 之后旧目录还在，新 driver 却只认清单里那一版，照目录
        名猜会把一次必然失败的 launch 报成"已就绪"。
      * 一次 launch 要的是哪一个目录。判据是**显式 channel**，不是 headless（实测见
        `browser._ARTIFACT_DIRS`）：dhole 默认那条路要完整的 `chromium-<rev>`，而磁盘上
        可能只有 `chromium_headless_shell-<rev>` —— 那一份它根本不起。两个都报出来，
        "这次要用的没装"才说得出"没装"。
      * 首用安装是会失败的（网络、依赖、平台），失败得留痕。一条承诺"第一次自己装"的
        行，如果安装失败过却什么都不说，就是把 doctor 变成了广告。
    """
    try:
        from dhole_mcp import browser
        arts = browser.chromium_artifacts()
        state = browser.read_install_state()
    except Exception as exc:  # noqa: BLE001 - browser 是重模块，缺它也要能出报告
        checks.append(("chromium binary", "info",
                       f"not inspectable: {type(exc).__name__}"[:60], ""))
        return
    rev = arts.get("revision")
    cmd = install.browser_binary_command()
    if arts.get("system_chrome"):
        checks.append(("chromium binary", "ok",
                       "system Chrome - nothing to download", ""))
        return
    needs = arts.get("launch_needs") or "chromium"
    wanted = arts["expected"][needs]
    found = arts.get(needs)
    if found:
        checks.append(("chromium binary", "ok", f"{found} (revision {rev})", ""))
        return
    # 磁盘上有另一份 ≠ 这次起得来。这条要说得出名字，否则用户只会看到一次"明明装过"
    # 的失败。
    other = next((arts[k] for k in ("chromium", "headless_shell")
                  if k != needs and arts.get(k)), None)
    if state.get("status") == "failed":
        checks.append((
            "chromium binary", "fail",
            f"install failed (revision {state.get('revision', rev)}): "
            f"{str(state.get('error', ''))[:120]}",
            str(state.get("command") or cmd),
        ))
        return
    if state.get("status") == "installing":
        # 一条没有下文的"正在装"是 doctor 最容易说的话，也是最容易过期的话：安装线程
        # 会随进程一起没掉（客户端掐请求、机器重启），留下的状态文件却还写着 installing。
        # 安装自己有时间上限，超过它就该说"上次没装完"，而不是继续承诺在等。
        import time
        started = state.get("started_at") or 0
        age_s = time.time() - float(started) if started else 0.0
        if started and age_s > browser.INSTALL_TIMEOUT_S + 120:
            checks.append((
                "chromium binary", "info",
                f"last install did not finish ({int(age_s // 60)} min ago) - "
                f"{wanted} still not installed",
                cmd,
            ))
            return
        checks.append(("chromium binary", "info",
                       f"install in progress (revision {rev}) - "
                       "the first browser fetch waits on it", ""))
        return
    checks.append((
        "chromium binary", "info",
        f"{wanted} not installed (revision {rev}) - the first browser-tier fetch "
        f"installs the one it launches (~"
        f"{browser.INSTALL_DOWNLOAD_MB[needs]}MB, one time; an interrupted download "
        "starts over)"
        + (f"; {other} on disk is not launched with an explicit channel" if other
           else ""),
        cmd if _has_module("patchright") else 'python -m pip install -e ".[all]"',
    ))


def _dir_mb(path: str) -> tuple[float, int]:
    """(MB, 文件数)。只读目录树；任何一步失败都返回已知的部分而不是抛。"""
    total, files = 0, 0
    try:
        for root, _dirs, names in os.walk(path):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                    files += 1
                except OSError:
                    continue
    except Exception:  # noqa: BLE001
        pass
    return round(total / 1_000_000, 1), files


def _append_rerank_model(checks) -> None:
    """4 · 语义模型：在不在、多大、有没有发布方摘要可对。

    publisher_sha256 是"这份字节就是作者发布的那份"的唯一凭据；只有本机自算 hash
    的模型只能证明"没坏"。这一行把差别说出来，因为它决定了"离线拷过来的模型"该
    不该被信任。
    """
    if not (_has_module("onnxruntime") and _has_module("tokenizers")):
        checks.append(("rerank model", "info",
                       "runtime absent - consensus order only", ""))
        return
    try:
        from dhole_mcp.reranker import active_model, active_model_dir
        model = active_model()
        d = active_model_dir()
    except Exception as exc:  # noqa: BLE001
        checks.append((
            "rerank model", "fail",
            f"registry unreadable: {type(exc).__name__}"[:60],
            "check ~/.dhole/config/reranker.json",
        ))
        return
    if not (d / "model.onnx").exists():
        checks.append((
            "rerank model", "info",
            f"{model.name} not downloaded (~{model.approx_bytes // 1_000_000}MB, "
            "resumable; the first neural search fetches it)", "",
        ))
        return
    mb, files = _dir_mb(str(d))
    authority = (f"publisher digest for {len(model.publisher_sha256)} file(s)"
                 if model.publisher_sha256 else
                 "self-hash only - no publisher digest to compare against")
    checks.append(("rerank model", "ok",
                   f"{model.name}: {mb}MB in {files} file(s), {authority}", ""))


#: 握手预算：一次真 spawn + initialize + tools/list 在这台机器上实测 1.2s，30s 是
#: "宿主的 env 写错了导致服务起不来也说不清" 的上界。测试把它压到零点几秒。
_HANDSHAKE_BUDGET_S = 30.0


def _append_host_handshake(checks) -> None:
    """5 · 宿主能不能连上：真的起一个 stdio 服务，走完 initialize + tools/list。

    装机失败的第一大原因是"配置写错了"，而它和"代码坏了"在 `-v` 里长得一样。只有
    真握手一次能分开两者。子进程用完即杀 —— doctor 绝不能留下一个占着管道的服务器。
    """
    import json as _json
    import subprocess as _sp
    import threading as _th
    import time as _t

    try:
        proc = _sp.Popen(
            [sys.executable, "-m", "dhole_mcp"],
            stdin=_sp.PIPE, stdout=_sp.PIPE, stderr=_sp.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        checks.append(("stdio handshake", "fail",
                       f"cannot spawn: {type(exc).__name__}"[:60],
                       "python -m pip install -e ."))
        return

    answers: dict = {}

    def _reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                try:
                    parsed = _json.loads(raw.decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(parsed, dict) and parsed.get("id") is not None:
                    answers[parsed["id"]] = parsed
                    if 2 in answers:
                        return
        except Exception:  # noqa: BLE001
            pass

    _th.Thread(target=_reader, daemon=True).start()
    t0 = _t.perf_counter()
    outcome, tools = "", 0
    try:
        for rid, method, params in (
                (1, "initialize", {"protocolVersion": "2025-03-26",
                                   "capabilities": {},
                                   "clientInfo": {"name": "dhole-doctor",
                                                  "version": "1"}}),
                (2, "tools/list", {})):
            msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            proc.stdin.write((_json.dumps(msg) + "\n").encode("utf-8"))
            proc.stdin.flush()
        deadline = t0 + _HANDSHAKE_BUDGET_S
        while 2 not in answers and _t.perf_counter() < deadline:
            _t.sleep(0.05)
        if 2 in answers:
            tools = len((answers[2].get("result") or {}).get("tools") or [])
            outcome = f"initialize + tools/list in {_t.perf_counter() - t0:.2f}s"
        else:
            outcome = f"no tools/list answer within {_HANDSHAKE_BUDGET_S:.0f}s"
    except Exception as exc:  # noqa: BLE001
        outcome = f"handshake broke: {type(exc).__name__}: {exc}"[:70]
    finally:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
    checks.append((
        "stdio handshake", "ok" if tools else "fail",
        f"{outcome}{f' - {tools} tools advertised' if tools else ''}",
        "" if tools else "run the host's exact command by hand: python -m dhole_mcp",
    ))


def _append_robots_posture(checks) -> None:
    """8 · robots 三态里的两种半：遵守 / 已豁免 / 这一行看不见的那部分。

    进程级只能看见前两种。"这个站没读到 robots 所以根本没查"是每次响应的
    `metadata.robots`，任何诊断命令都聚不出它 —— 所以必须把"看不见"说出来，
    否则 all healthy 会被读成"一直在合规"。
    """
    try:
        from dhole_mcp import robots
        waived = robots.env_disabled()
    except Exception:  # noqa: BLE001 - 读不到 robots 本身就是发现
        checks.append(("robots posture", "fail",
                       "the robots module did not import",
                       "python -m pip install -e ."))
        return
    checks.append((
        "robots posture", "info" if waived else "ok",
        ("waived process-wide (DHOLE_IGNORE_ROBOTS) - every fetch skips the check"
         if waived else
         "compliance ON (per-call waiver needs ignore_robots=true)")
        + " - per-site state is NOT visible here: a robots.txt that could not be "
          "read is reported per response in metadata.robots, and not checked "
          "is not complied",
        "unset DHOLE_IGNORE_ROBOTS to go back to compliance" if waived else "",
    ))


def _append_proxy_tcp(checks) -> None:
    """9 · 出口：配置说了什么，加上 TCP 认不认。"""
    from dhole_mcp import search_proxy
    proxy, source = search_proxy.configured_proxy()
    if not proxy:
        checks.append(("search proxy", "ok", "none set - direct connection", ""))
        return
    detail = f"{search_proxy.redact(proxy)} (from {source})"
    why = ""
    try:
        from dhole_mcp.fetcher import proxy_preflight
        reachable, _category = proxy_preflight(proxy, 5.0)
    except Exception as exc:  # noqa: BLE001
        reachable, why = False, f"preflight raised {type(exc).__name__}"
    else:
        reachable = bool(reachable)
    if reachable:
        checks.append(("search proxy tcp", "ok",
                       detail + " - accepts connections", ""))
    else:
        checks.append((
            "search proxy tcp", "fail",
            f"{detail} - DID NOT ACCEPT a TCP connection"
            + (f": {why}" if why else ""),
            "start the proxy, or unset "
            + (source if source.startswith("DHOLE_") else "DHOLE_SEARCH_PROXY"),
        ))


def _append_state_inventory(checks) -> None:
    """11 · 状态目录里有什么、多大、最老多久 —— 这一行同时是隐私出口。"""
    import time as _t
    home = paths.home()
    files, dirs = [], []
    try:
        for e in sorted(os.scandir(home), key=lambda x: x.name):
            (dirs if e.is_dir() else files).append(e)
    except Exception:  # noqa: BLE001
        pass
    mb, _ = _dir_mb(str(home))
    ages = []
    for e in files:
        try:
            ages.append((_t.time() - os.path.getmtime(e.path)) / 86400.0)
        except OSError:
            continue
    oldest = max(ages) if ages else 0.0
    names = ", ".join(e.name for e in files[:6]) or "empty"
    if len(files) > 6:
        names += f" (+{len(files) - 6} more)"
    checks.append((
        "state dir contents", "info",
        f"{len(files)} file(s) + {len(dirs)} dir(s), {mb}MB, oldest {oldest:.0f}d: {names}",
        "",
    ))
    held = []
    if any(e.name == "sessions.db" for e in files):
        held.append("sessions.db holds cookie VALUES")
    if any(e.name == "usage.jsonl" for e in files):
        held.append("usage.jsonl is the call log")
    if any(e.name == "profiles" for e in dirs):
        held.append("profiles/ is a logged-in browser profile")
    if any(e.name == "cdp_hosts.json" for e in files):
        held.append("cdp_hosts.json names the hosts allowed to attach your Chrome")
    if held:
        checks.append(("credentials on disk", "info",
                       "; ".join(held) +
                       " - delete these before sharing a machine or pasting a bug report",
                       ""))


def _append_wire_cost(checks) -> None:
    """12 · 这次连接付了多少 token：注册工具数 + 连接期字符数。

    连接期字符数是每次会话都要付一次的固定成本，而这个数字此前只有 `tests/
    tool_payload_measure.py` 量得到 —— 也就是说只有开发者知道。把它印出来，用户
    才有依据决定要不要用 DHOLE_TOOLS 收窄注册面。4.16 chars/token 是这份载荷实测
    出来的比值，不是通用常数，所以句子里把出处写在一起。
    """
    try:
        import json as _json

        from dhole_mcp import server as _srv
        defs = _srv.MasterFetchServer._enabled_tool_defs()
        minimal = [{"name": td["name"], "description": td["description"],
                    "inputSchema": td["inputSchema"],
                    **({"annotations": td["annotations"]}
                       if "annotations" in td else {})} for td in defs]
        compact = len(_json.dumps({"tools": minimal}, separators=(",", ":"),
                                  ensure_ascii=False))
        total = compact + len(_srv.DHOLE_INSTRUCTIONS)
    except Exception as exc:  # noqa: BLE001
        checks.append(("connect cost", "info",
                       f"unreadable: {type(exc).__name__}"[:60], ""))
        return
    checks.append((
        "connect cost", "ok",
        f"{len(defs)} tool(s), {total} chars on the wire "
        f"~{total / 4.16:.0f} tokens (4.16 is this payload's measured chars/token)"
        " - to pay less: DHOLE_TOOLS=smart_fetch,smart_search (restart the host)",
        "",
    ))


PROBE_SEARCH_QUERY = "dhole mcp"
PROBE_FETCH_URL = "https://example.com/"


def _append_live_probe(checks) -> None:
    """10 · 真实搜索 + 真实抓取各一次（`init --probe` 的职责搬进 doctor）。

    免密引擎是对公开结果的直抓，没有 SLA：静态检查看不出"这个 IP 今天被盯着"。
    这两行是唯一能证明"今天从这台机器能用"的手段，所以每个目标都点名，且失败
    记成网络事实而不是安装故障。
    """
    import asyncio

    try:
        from dhole_mcp import server as _srv
    except Exception as exc:  # noqa: BLE001
        checks.append(("live search", "fail",
                       f"server did not import: {type(exc).__name__}"[:60],
                       "python -m pip install -e ."))
        return
    srv = _srv.MasterFetchServer(cache_ttl=0)

    async def _call(name, args):
        out = await srv._dispatch(name, args)
        return out[1] if isinstance(out, tuple) else out

    try:
        search = asyncio.run(_call("smart_search", {
            "query": PROBE_SEARCH_QUERY, "max_results": 3}))
    except Exception as exc:  # noqa: BLE001
        search = {"error": f"{type(exc).__name__}: {exc}"}
    hits = search.get("results") or []
    if search.get("error"):
        checks.append(("live search", "info",
                       f"failed: {str(search['error'])[:110]}",
                       "dhole engines probe says who still answers from this IP"))
    elif not hits:
        checks.append(("live search", "info",
                       "0 results - every keyless engine declined or is cooling",
                       "dhole engines probe, or set DHOLE_SEARCH_PROXY"))
    else:
        checks.append(("live search", "ok",
                       f"{len(hits)} result(s) for {PROBE_SEARCH_QUERY!r} via "
                       f"{len(search.get('engines_used') or [])} engine(s), "
                       f"rerank={search.get('rerank_mode', '?')}", ""))

    try:
        fetch = asyncio.run(_call("smart_fetch", {"url": PROBE_FETCH_URL}))
    except Exception as exc:  # noqa: BLE001
        fetch = {"error": f"{type(exc).__name__}: {exc}"}
    if fetch.get("error"):
        checks.append(("live fetch", "info",
                       f"{PROBE_FETCH_URL} failed: {str(fetch['error'])[:110]}",
                       str(fetch.get("next_action") or "check this network")[:150]))
    else:
        checks.append(("live fetch", "ok",
                       f"{PROBE_FETCH_URL} -> {fetch.get('status')} "
                       f"{fetch.get('total_extracted_chars', 0)} chars "
                       f"via {fetch.get('fetcher_used', '?')}", ""))


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

    def _safe(fn, label: str) -> None:
        """一项检查自己坏了，不许把整份报告带垮：换成一行说明，其余照旧。

        doctor 是"已经坏了才去跑"的命令，它自己抛异常是最坏的结局（这条不变量由
        test_never_raises_and_returns_an_exit_code 钉着，12 项只是让它更难守）。
        """
        try:
            fn(checks)
        except Exception as exc:  # noqa: BLE001
            checks.append((label, "info", f"skipped: {type(exc).__name__}"[:60], ""))

    live = _live_checks_enabled()

    # ── 1 · 运行环境与这份安装的真实身份 ──────────────────────────────────
    _safe(_append_runtime, "python + platform")

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

    # ── 2 · 依赖分层 ──────────────────────────────────────────────────────
    _safe(_append_dependency_layers, "dependency layers")

    # ── 3+4 · 抓取与排序真正要用的那两个大件 ──────────────────────────────
    _safe(_append_chromium, "chromium binary")
    _safe(_append_rerank_model, "rerank model")

    # ── 5 · 宿主连得上吗（真起一个 stdio 服务）──────────────────────────
    if live:
        _safe(_append_host_handshake, "stdio handshake")
    else:
        checks.append(("stdio handshake", "info",
                       "skipped by the test harness (it spawns a server)", ""))

    # ── 6+7 · 两扇门当前值、遮蔽关系，以及已被注销却被设着的旋钮 ─────────
    # The whole section runs inside a guard because a doctor() that raises is
    # worse than a doctor() with a missing section - the plan's W2 clause "doctor
    # 依然绝不许抛异常" is pinned by test_never_raises_and_returns_an_exit_code.
    _safe(_append_config_audit, "config audit")

    # ── 8 · robots 的三种状态 ─────────────────────────────────────────────
    _safe(_append_robots_posture, "robots posture")

    # ── 9 · 出口与解析器现实 ─────────────────────────────────────────────
    # §1.5：README 的"零配置可用"在 fake-ip TUN 上被证伪过，每次抓取都失败的现场
    # 看着像产品 bug。这里说出环境的真相，操作者才看得见"是你的代理这么配的"。
    # 离线 / DNS 坏了读成 info，绝不算失败。
    if live:
        _safe(_append_proxy_tcp, "search proxy tcp")
    else:
        from dhole_mcp import search_proxy
        proxy, source = search_proxy.configured_proxy()
        checks.append((
            "search proxy", "ok" if not proxy else "info",
            (f"{search_proxy.redact(proxy)} (from {source})" if proxy
             else "none set - direct connection"),
            "" if proxy else "set DHOLE_SEARCH_PROXY when your IP is being throttled",
        ))
    _safe(_append_resolver_reality, "resolver reality")

    # ── 10 · 实搜 + 实抓（免密引擎没有 SLA，这是唯一的"今天能用"证明）────
    if live:
        _safe(_append_live_probe, "live probe")
    else:
        checks.append(("live probe", "info",
                       "skipped by the test harness (it sends real traffic)", ""))

    # ── 11 · 状态目录：能不能写、里面有什么、多久没清 ────────────────────
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
    _safe(_append_state_inventory, "state dir contents")

    # ── 12 · 这次连接付了多少 ───────────────────────────────────────────
    _safe(_append_wire_cost, "connect cost")

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


