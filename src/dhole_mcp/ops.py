"""`dhole` 的运维面：子命令、体检、技能安装、引擎健康面板与进程入口。

从 server.py 原样搬出（17.0 B6 的第一刀，零行为变更）。搬的理由不是"server.py 太长"，
而是这一块的依赖方向本来就只有一边：它读服务器，服务器从不读它 ——
`cli.py` 与 `__main__.py` 是它唯一的两个入口调用方。放在同一个文件里，
每次改 MCP 协议都要在 450 行运维代码里找位置；反过来改 `dhole skill install`
也要重读整个服务器。

`MasterFetchServer` 在模块级 import，而不是 `main()` 里现取 —— 这不是随手写的：
入口点那句"缺哪个依赖、跑这条命令补"的自愈文案，靠的是 `cli.py` 把
`from dhole_mcp.ops import main` 整个包在 try 里。改成惰性 import 之后，运维面的
导入会变得完全成功，于是 argv 解析先跑起来，缺依赖的报错就被 argparse 的
"unrecognized arguments" 挡在下面 —— 第一次装的人看到的是参数错误，而不是他该装的
那件东西。纯搬迁不许改这个形状。
"""

import os
import sys

from dhole_mcp.server import MasterFetchServer


def _help_epilog() -> str:
    """Styled epilog for `dhole --help`: the command cheat-sheet + docs link."""
    from dhole_mcp import cli_ui as ui
    return "\n".join([
        ui.dim("commands:"),
        f"  {ui.cyan('dhole')}              {ui.dim('serve · stdio MCP (Claude Code, Cursor, OpenCode, Pi)')}",
        f"  {ui.cyan('dhole --http')}       {ui.dim('serve · streamable HTTP (Open WebUI), use --host/--port')}",
        f"  {ui.cyan('dhole -v')}           {ui.dim('version + capability check')}",
        f"  {ui.cyan('dhole doctor')}       {ui.dim('the 12-item install check (spawns a server, launches the browser once, then one real search + one real fetch)')}",
        f"  {ui.cyan('dhole --doctor')}     {ui.dim('same as `dhole doctor` (accepted for backwards compatibility)')}",
        f"  {ui.cyan('dhole engines')}      {ui.dim('show / reset / live-test engine health (list|reset|probe)')}",
        f"  {ui.cyan('dhole config')}       {ui.dim('list / set / unset persisted knobs in ~/.dhole/config.json (no host restart for per-call knobs)')}",
        f"  {ui.cyan('dhole skill')}        {ui.dim('install the bundled agent skill (status|install)')}",
        "",
        ui.dim("docs:") + "  " + ui.cyan("https://github.com/ouli-1242/dhole-mcp"),
    ])


def _skill_manifest(root) -> dict:
    """relpath -> sha256[:16] for every file under a skill directory."""
    import hashlib
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    return out


def _cmd_config(argv: list[str]) -> int:
    """`dhole config list | set NAME VALUE | unset NAME` — the persisted knobs.

    Why this command exists: while env was the only door, changing one number
    meant editing the host's MCP JSON and restarting it. That is not
    configuration, that is infrastructure work, and it is why the knobs nobody
    needed were also the knobs nobody could reach. Values in
    ``~/.dhole/config.json`` are re-read when the file's mtime changes, so a
    knob read per call takes effect on the next call.

    ``env`` still wins, on purpose: every existing install configured itself
    through the host's env block, and a file must never quietly override a
    decision someone already made. Knobs read at import are writable here too,
    but said out loud as needing a restart - the same note `dhole doctor` gives.
    """
    from dhole_mcp import cli_ui as ui
    from dhole_mcp import env_registry as er

    args = [a for a in argv if a != ""]
    action = args[0].lower() if args else "list"

    if action == "list":
        entries = er.file_entries()
        print("  " + ui.dim(f"file: {er.config_path()}"))
        if not entries:
            print("  " + ui.dim("no persisted values yet") + "  " + ui.dim("|") + "  "
                  + ui.cmd("dhole config set DHOLE_DEFAULT_CONTENT_CHARS 8000"))
            print("  " + ui.dim(f"{len(er.ALL)} knobs are registered; env still outranks this "
                                "file - `dhole doctor` audits both."))
            return 0
        for name in sorted(entries):
            entry = er.by_name(name)
            if entry is None:
                print("  " + ui.warn(f"{name} = {entries[name]}")
                      + ui.dim("  ✗ not a dhole knob - nothing reads it (remove with "
                               f"dhole config unset {name})"))
                continue
            shown = entry.display_value(entries[name])
            tail = []
            if name in os.environ:
                tail.append(f"shadowed by env ({entry.display_value(os.environ.get(name))})")
            if entry.read_at == "import":
                tail.append("restart the host to change")
            print("  " + ui.cmd(f"{name} = {shown}") + ui.dim("  " + entry.summary)
                  + (ui.dim("  · " + "; ".join(tail)) if tail else ""))
        return 0

    if action == "set":
        if len(args) < 3:
            print(ui.err("usage: dhole config set <NAME> <value>"), file=sys.stderr)
            return 2
        name, value = args[1].strip().upper(), " ".join(args[2:]).strip()
        try:
            path = er.set_entry(name, value)
        except ValueError as exc:
            print(ui.err(str(exc)), file=sys.stderr)
            return 2
        entry = er.by_name(name)
        if name in os.environ and os.environ.get(name) != value:
            print(ui.warn(f"written to {path.name}, but env already sets {name} - "
                          "env wins, so this value has no effect until the host's "
                          "env block drops it"))
        elif entry is not None and entry.read_at == "import":
            print(ui.ok(f"{name} = {entry.display_value(value)}") + ui.dim(
                f"  ({path})  read at import: restart the host to take effect"))
        else:
            print(ui.ok(f"{name} = {entry.display_value(value) if entry else value}")
                  + ui.dim(f"  ({path})  takes effect on the next call"))
        return 0

    if action == "unset":
        if len(args) < 2:
            print(ui.err("usage: dhole config unset <NAME>"), file=sys.stderr)
            return 2
        name = args[1].strip().upper()
        if er.unset_entry(name):
            print(ui.ok(f"removed {name} from {er.config_path().name}"))
            if name in os.environ:
                print(ui.dim(f"  env still sets it to {os.environ.get(name)!r} "
                             "(that value now decides)"))
        else:
            print(ui.dim(f"{name} was not in {er.config_path()}"))
        return 0

    print(ui.err(f"unknown subcommand: {action} (try: dhole config list|set|unset)"),
          file=sys.stderr)
    return 2


def _cmd_skill(argv: list[str], target=None) -> int:
    """`dhole skill [status|install [--force]]` — install / inspect the bundled agent skill.

    The skill ships inside the package (dhole_mcp/skills/dhole-web/) and teaches
    the agent how to drive dhole: call patterns, response-field dictionary,
    troubleshooting, configuration, recipes. Installing copies it to
    ~/.agents/skills/dhole-web where agent hosts discover user-level skills.
    The CLI is a convenience, not a second source of truth — the bundled copy is.
    """
    from dhole_mcp import cli_ui as ui
    from pathlib import Path
    import shutil

    bundled = Path(__file__).resolve().parent / "skills" / "dhole-web"
    target = Path(target) if target is not None else Path.home() / ".agents" / "skills" / "dhole-web"

    args = [a for a in argv if a and a.strip()]
    force = any(a.lower() in ("--force", "-f") for a in args)
    positional = [a for a in args if not a.startswith("-")]
    action = positional[0].lower() if positional else "status"
    if action not in ("status", "install"):
        print(ui.err(f"unknown subcommand: {action} (try: dhole skill status|install [--force])"))
        return 2
    if not bundled.is_dir():
        print(ui.err(f"bundled skill not found at {bundled} (reinstall dhole-mcp)"))
        return 1
    bundled_files = _skill_manifest(bundled)

    if action == "status":
        print("  " + ui.dim("bundled") + "  " + ui.cmd(str(bundled))
              + "  " + ui.dim(f"({len(bundled_files)} files)"))
        if not target.is_dir():
            print("  " + ui.dim("installed") + "  " + ui.dim("not installed"))
            print("  " + ui.dim("install with") + "  " + ui.cmd("dhole skill install"))
        else:
            installed = _skill_manifest(target)
            if installed == bundled_files:
                print("  " + ui.ok("installed & up to date") + "  " + ui.dim(str(target)))
            else:
                changed = [f for f in sorted(set(installed) | set(bundled_files))
                           if installed.get(f) != bundled_files.get(f)]
                print("  " + ui.warn("installed, differs from bundled") + "  " + ui.dim(str(target)))
                for f in changed[:8]:
                    print("      " + ui.dim(f))
                if len(changed) > 8:
                    print("      " + ui.dim(f"... and {len(changed) - 8} more"))
                print("  " + ui.dim("re-sync with") + "  " + ui.cmd("dhole skill install --force"))
        return 0

    # install
    if target.exists() and not target.is_dir():
        print(ui.err(f"{target} exists and is not a directory; remove it first"))
        return 2
    if target.is_dir():
        installed = _skill_manifest(target)
        if installed == bundled_files:
            print(ui.ok("already up to date") + "  " + ui.dim(str(target)))
            return 0
        if installed and not force:
            print(ui.err("installed skill differs from the bundled one:"))
            for f in sorted(set(installed) | set(bundled_files)):
                if installed.get(f) != bundled_files.get(f):
                    print("  " + ui.dim(f))
            print("  " + ui.dim("re-install over it with") + "  " + ui.cmd("dhole skill install --force"))
            return 2
        shutil.rmtree(target)
    shutil.copytree(bundled, target)
    print(ui.branded(ui.ok("skill installed"), ui.dim(str(target))))
    print("  " + ui.dim(f"{len(bundled_files)} files · skills load at session start, so restart your agent to pick it up"))
    print("  " + ui.dim("hosts that only read ~/.zcode/skills can point at the installed folder the same way"))
    return 0


def _cmd_engines(argv: list[str]) -> int:
    """`dhole engines [list|reset|probe]` — look at / clear / live-test the pool.

    Which engines got skipped recently, and why. Both facts live in two files
    under the dhole home, and until now reading them meant opening JSON by hand
    and clearing them meant deleting files plus restarting the process. `probe`
    is the third thing those files cannot tell you: whether an engine answers
    *right now* (list only remembers the last round someone else ran).
    """
    from dhole_mcp import cli_ui as ui
    from dhole_mcp.updater import _engine_cooldowns, _engine_yield_row

    action = (argv[0].lower() if argv else "list")
    if action in ("list", "ls", "status", ""):
        cooldowns = _engine_cooldowns()
        row = _engine_yield_row()
        print("  " + ui.dim("search engine health (what dhole currently remembers)"))
        if row:
            print("  " + ui.dim(row[0]) + ": " + row[1])
        else:
            print("  " + ui.dim("no engine yield recorded yet (run a search first)"))
        if cooldowns:
            for name, seconds in sorted(cooldowns.items()):
                print(f"    {name.ljust(14)} " + ui.err(f"cooling down, {int(seconds)}s left"))
            print("  " + ui.dim("cooldowns expire by themselves; a successful search "
                                "clears one immediately"))
        else:
            print("    " + ui.ok("no engine is on cooldown"))
        # G6: the timeouts themselves are part of "why is this engine missing".
        # 17.0 写死 —— 说出现在生效的值，而不是让人去读源码。
        from dhole_mcp.updater import _cooldown_config
        cfg = _cooldown_config()
        tiers = cfg["tiers"]
        hb = cfg["heartbeat"]
        print("  " + ui.dim(
            f"backoff: blocked {tiers['block']:g}s / challenge x3 {tiers['challenge']:g}s"
            f" / unreachable x3 {tiers['connection']:g}s"
            + f"  ·  heartbeat: probes cooling engines every {hb:g}s"))
        print("  " + ui.dim("reset with") + "  " + ui.cmd("dhole engines reset"))
        return 0
    if action in ("reset", "clear"):
        try:
            from dhole_mcp.search_metasearch import engine_state_reset
        except Exception as e:
            print(ui.err(f"cannot load the search layer to reset it: {str(e)[:160]}"))
            return 1
        info = engine_state_reset()
        cool = info.get("released_cooldowns") or {}
        print(ui.branded(ui.cyan("engine state"),
                         ui.ok(f"reset - {info.get('engines_forgotten', 0)} engine "
                               f"record(s) forgotten, {len(cool)} cooldown(s) released")))
        print("  " + ui.dim("cleared") + "  " + ", ".join(
            [ui.cmd("circuit_breaker.json"), ui.cmd("engine_stats.json")]))
        print("  " + ui.dim("a RUNNING server keeps its own copy in memory - to un-stick "
                            "one without restarting, call sys_state with "
                            "clear='engine_state'"))
        return 0
    if action in ("probe", "check", "test"):
        return _engines_probe(argv[1:])
    print(ui.err(f"unknown subcommand: {action} (try: dhole engines list|reset|probe)"))
    return 2


_PROBE_QUERY_DEFAULT = "python asyncio tutorial"
"""默认探针查询：中英混排站点都有的通用技术词。换查询是为了让"这家能不能用"不被
缓存和一个恰好无结果的查询干扰 —— 所以它必须能改。"""


def _engines_probe(args: list[str]) -> int:
    """`dhole engines probe` - one live query per engine, and say what each one did.

    为什么必须一家一次：并跑时有早退配额，答得慢的引擎会被取消（preempted），于是
    "这家今天到底能不能用"在真实搜索里根本没有答案。点名单引擎才拿得到它自己的耗时、
    状态与解析产出。走 `multi_search` —— 与 web_search 同一条链，诊断的不是另一套代码。
    """
    import asyncio
    import time

    from dhole_mcp import cli_ui as ui

    query = _PROBE_QUERY_DEFAULT
    names: list[str] = []
    everything = False
    for a in args:
        if a.startswith("--query="):
            query = a.split("=", 1)[1].strip() or query
        elif a in ("--all", "-a"):
            everything = True
        elif a.startswith("-"):
            print(ui.err(f"unknown flag: {a} (try: dhole engines probe [--all] "
                         f"[--query=...] [engine ...])"))
            return 2
        else:
            names.append(a.lower())

    from dhole_mcp.search_engines import DEFAULT_ENGINES, _cooldowns, multi_search
    try:
        from dhole_mcp.search_metasearch import _DHOLE_TO_BACKEND, _TEXT_ENGINES
    except Exception as e:
        print(ui.err(f"cannot load the search layer to probe it: {str(e)[:160]}"))
        return 1

    if everything:
        names = [n for n in _DHOLE_TO_BACKEND if n in _TEXT_ENGINES]
    elif not names:
        names = list(DEFAULT_ENGINES)
    unknown = [n for n in names if n not in _DHOLE_TO_BACKEND]
    if unknown:
        print(ui.err(f"unknown engine(s): {', '.join(sorted(unknown))} "
                     f"(list them with: dhole engines probe --all)"))
        return 2

    async def _run() -> int:
        print("  " + ui.dim(f"probing {len(names)} engine(s), one at a time")
              + "  " + ui.cmd(f"--query={query!r}"))
        usable = 0
        for name in names:
            # 冷却中的引擎必须**先自己判**：点名单引擎时它是整个池子，metasearch 会
            # 因为"所有引擎都在冷却"直接抛 No search engines could start，于是这里印成
            # error 而不是"冷却中"—— 恰恰丢掉了探针最该说的那句话。
            backend = _DHOLE_TO_BACKEND.get(name, name)
            left = _cooldowns().get(backend, 0.0)
            if left > 0:
                print(f"    {name.ljust(14)} " + ui.dim(
                    f"cooling         -       -  {int(left)}s left (not asked; "
                    f"`dhole engines reset` to force)"))
                continue
            t0 = time.perf_counter()
            try:
                results, reports = await asyncio.wait_for(
                    multi_search(query, 6, engines=[name]), timeout=30)
            except asyncio.TimeoutError:
                print(f"    {name.ljust(14)} " + ui.err("timeout      -     >30s"))
                continue
            except Exception as e:
                print(f"    {name.ljust(14)} " + ui.err(
                    f"error        -    {str(e)[:60]}"))
                continue
            elapsed = (time.perf_counter() - t0)  # seconds
            rep = reports[0] if reports else None
            status = getattr(rep, "status", "") or "?"
            nodes = getattr(rep, "item_nodes", -1)
            got = getattr(rep, "usable", -1)
            verdict = getattr(rep, "yield_verdict", "") or ""
            err = (getattr(rep, "error", "") or "").strip()
            if rep is not None and rep.ok:
                usable += 1
                print(f"    {name.ljust(14)} " + ui.ok("ok") + f"    "
                      f"{str(len(results)) + ' hits':>9}  {elapsed:6.1f}s  "
                      + ui.dim(f"parsed {got}/{nodes} items"))
            elif status == "circuit_open":
                print(f"    {name.ljust(14)} " + ui.dim(
                    f"cooling        -       -  {err[:48] or 'on cooldown'}"))
            elif rep is not None and rep.preempted:
                print(f"    {name.ljust(14)} " + ui.dim("not asked      -       -"))
            elif status == "empty":
                # error 里带的是原因（"no parsable results (HTTP 302, redirect not
                # followed)" 之类），比再报一次"没结果"有用得多。
                detail = err or "no results"
                if nodes > 0:
                    detail += f" - {nodes} item nodes survived, usable={got} ({verdict})"
                print(f"    {name.ljust(14)} " + ui.err("empty")
                      + f"        -  {elapsed:6.1f}s  " + ui.dim(detail[:74]))
            else:
                # 出错时 status 本身就是最具体的信息（"error:MetaSearchException: …"），
                # 别把同一个字符串再印一遍当原因 —— 但列宽截掉的那一截要挪到原因栏，
                # 不然异常类型留下了、真正有用的尾巴（哪个 OSError）反而丢了。
                detail = "" if err == status else err
                if err == status and len(status) > 18:
                    detail = status[18:]
                print(f"    {name.ljust(14)} " + ui.err(
                    f"{status[:18].ljust(18)}  -  {elapsed:5.1f}s  {detail[:52]}"))
        print("  " + (ui.ok(f"{usable} of {len(names)} engine(s) usable right now")
                      if usable else ui.err(f"no engine answered {query!r}"))
              + ui.dim("  (cooldowns are honored here; `dhole engines reset` clears them)"))
        return 0 if usable else 1

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print(ui.err("\nprobe interrupted"))
        return 130


def main():
    """Entry point for the dhole CLI."""
    from dhole_mcp import cli_ui as ui
    from dhole_mcp import updater
    import argparse
    import sys as _sys
    # Subcommands dispatched BEFORE argparse: a bare positional would collide
    # with the serve-by-default behaviour (no args = start the MCP server), so
    # each management verb is matched by hand and its tail passed to a helper.
    #
    # W5: `doctor` joins the list. Before this change `dhole doctor` fell
    # through to argparse, which errored with "unrecognized arguments: doctor"
    # and never mentioned --doctor. Both spellings now go through the same
    # `updater.doctor()` call, so their stdout AND exit code are identical.
    _SUBCOMMANDS = ("engines", "skill", "doctor", "config")
    if len(_sys.argv) > 1:
        first = _sys.argv[1].lower()
        if first == "engines":
            raise SystemExit(_cmd_engines(_sys.argv[2:]))
        if first == "skill":
            raise SystemExit(_cmd_skill(_sys.argv[2:]))
        if first == "config":
            raise SystemExit(_cmd_config(_sys.argv[2:]))
        if first == "doctor":
            raise SystemExit(updater.doctor())
        # Anything that is not a flag and not a known subcommand is a typo.
        # The old behaviour (argparse "error: unrecognized arguments") gave
        # no suggestion; "did you mean: dhole doctor?" is what turns a hung
        # first-time user back into a working one.
        if not first.startswith("-"):
            import difflib
            near = difflib.get_close_matches(first, _SUBCOMMANDS, n=1, cutoff=0.4)
            hint = (f"did you mean: dhole {near[0]}?" if near
                    else f"try: dhole {'|'.join(_SUBCOMMANDS)}")
            print(ui.err(f"unknown subcommand: {first} ({hint})"),
                  file=sys.stderr)
            raise SystemExit(2)
    parser = argparse.ArgumentParser(
        prog="dhole",
        description=ui.branded(ui.dim("web research for AI agents · $0 · no keys"), ""),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_help_epilog(),
    )
    parser.add_argument("--http", action="store_true",
                        help="serve over streamable HTTP (MCP 2025-03-26) at http://host:port/mcp")
    parser.add_argument("--host", default="127.0.0.1",
                        help="host for HTTP transport (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765,
                        help="port for HTTP transport (default 8765)")
    parser.add_argument("--cache-ttl", type=int, default=3600,
                        help="default cache TTL in seconds (default 3600)")
    parser.add_argument("-v", "--version", action="store_true",
                        help="show version + the capability panel")
    parser.add_argument("--doctor", action="store_true",
                        help="the 12-item install check (launches the browser once); prints the fix for every failure")
    args = parser.parse_args()

    if args.version:
        updater.print_version()
        return
    if args.doctor:
        raise SystemExit(updater.doctor())

    # HTTP mode: stdout is free (not an MCP stdio pipe), so a one-line banner is
    # safe. uvicorn follows with its own URL line. Stdio mode stays silent - any
    # stdout/stderr noise corrupts the MCP protocol or reads as a crash.
    if args.http:
        print(ui.branded(ui.cyan("serving HTTP"), ui.dim(f"http://{args.host}:{args.port}/mcp")))

    srv = MasterFetchServer(cache_ttl=args.cache_ttl)
    srv.serve(http=args.http, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
