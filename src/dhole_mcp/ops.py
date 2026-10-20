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
        f"  {ui.cyan('dhole watch')}        {ui.dim('add/list/remove/run a page-change watchlist (checks reuse web_read check; exit 0 none, 1 changed, 2 error)')}",
        f"  {ui.cyan('dhole wire')}         {ui.dim('the connect-face ledger (what each wire sentence costs every connection) and its lock: ledger|lock [--write --reason]')}",
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


def _tool_defs_lock_path():
    """锁文件的位置：源码仓库里的 `tests/tool_defs_lock.json`。

    单独一颗函数是为了**可测** —— `--write` 的往返要在临时目录里跑，不能碰真锁。
    装好的 wheel 里没有 `tests/`，所以读不到时上面那条命令会具名拒绝（不猜、不新建）。
    """
    from pathlib import Path
    return Path(__file__).resolve().parents[2] / "tests" / "tool_defs_lock.json"


def _cmd_wire(argv: list[str]) -> int:
    """`dhole wire [ledger | lock [--write --reason "…"]]` — 常驻层的账与它的锁。

    为什么值得有一扇命令门：`tools/list` 的每一个字符都是**每次连接**付一次的钱，而
    今天这笔账的余量是 0（`tests/test_wire_moves_land_in_the_manual.py` 把棘轮钉在实测
    值上，多一个字就红）。在此之前想知道"某一句值多少字符"只能改完再跑测试 —— 那把
    一次措辞决定变成一次试错，而试错的反馈是"红了"，不是"这句 211 字、可以从那句 168
    字里腾出来"。这里把测量交到命令上：按工具、按参数拆开。

    `--write` 是锁的**唯一写者**。那条锁文件的 docstring 早就写着"不许手抄"（阶段 7
    作者手抄时丢过一个开头的 3），而一条只能被"记得去算"的规矩迟早被破；现在算的人
    只有一个，而写它必须附一句理由 —— 理由进 `comment`，那是这块面孔的变更史，
    没有理由的改写不是更新锁，是抹掉一次决定。
    """
    import json

    from dhole_mcp import cli_ui as ui
    from dhole_mcp import wire as _wire
    from dhole_mcp.server import ACTIVE_INSTRUCTIONS, DHOLE_INSTRUCTIONS

    action = (argv[0].lower() if argv else "ledger")
    if action in ("ledger", "ls", "list", "cost", ""):
        all_defs = MasterFetchServer._TOOL_DEFS
        active = MasterFetchServer._enabled_tool_defs()
        n_all, chars_all = _wire.face_cost(all_defs, DHOLE_INSTRUCTIONS)
        n_act, chars_act = _wire.face_cost(active, ACTIVE_INSTRUCTIONS)
        print("  " + ui.dim("connect face (paid once per MCP connection):"))
        print(f"    default  {n_all:>2} tool(s)  {chars_all:>6} chars"
              f"  ~{chars_all / 4.16:.0f} tokens (4.16 = this payload's measured chars/token)")
        if chars_act != chars_all:
            print(f"    this run {n_act:>2} tool(s)  {chars_act:>6} chars"
                  "  (DHOLE_TOOLS narrowed the face)")
        rows = sorted(_wire.face_ledger(all_defs), key=lambda r: -r["chars"])
        if "--all" in argv[1:]:
            print("  " + ui.dim("every parameter, most expensive first:"))
            for row in rows:
                print(f"    {row['tool']} ({row['chars']} chars)")
                for name, chars in sorted(row["params"].items(), key=lambda kv: -kv[1]):
                    print(f"      {name:<22} {chars:>4}")
            return 0
        print("  " + ui.dim("where the characters go (compact json per tool, incl. schema):"))
        for row in rows:
            print(f"    {row['tool']:<13} {row['chars']:>5} chars"
                  f"  description {row['description_chars']:>5}")
            for name, chars in sorted(row["params"].items(),
                                      key=lambda kv: -kv[1])[:3]:
                print(f"      · {name:<18} {chars:>4}")
        print("  " + ui.dim("every parameter of every tool: dhole wire ledger --all"
                            " · 锁: dhole wire lock"))
        return 0

    if action == "lock":
        write = "--write" in argv[1:]
        reason = _flag_value(argv[1:], "--reason")
        lock_path = _tool_defs_lock_path()
        if not lock_path.exists():
            print(ui.err(f"no lock file at {lock_path} (an installed wheel ships no tests/;"
                         " the lock lives in the source repo)"), file=sys.stderr)
            return 2
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        defs = MasterFetchServer._TOOL_DEFS
        want_tools = _wire.tool_defs_digest(defs)
        want_face = _wire.wire_face_digest(defs, DHOLE_INSTRUCTIONS)
        drift = [k for k, want in (("sha256", want_tools),
                                   ("sha256_wire_face", want_face),
                                   ("tools", str(len(defs))))
                 if str(lock.get(k)) != want]
        if not write:
            if drift:
                print(ui.err(f"the lock disagrees on: {', '.join(drift)}"), file=sys.stderr)
                print(ui.dim("  代码确实变了 -> dhole wire lock --write --reason \"…\"；"
                             "没改代码 -> 查依赖/构建链路里谁动了描述"), file=sys.stderr)
                return 1
            print("  " + ui.ok("lock matches the face this build serves"))
            print("  " + ui.dim(f"    sha256={want_tools[:12]}… "
                                f"face={want_face[:12]}… tools={len(defs)}"))
            return 0
        if not reason:
            print(ui.err("--write needs --reason: the lock's comment is the change log "
                         "of this face; writing without one erases a decision"),
                 file=sys.stderr)
            return 2
        import datetime
        stamp = datetime.date.today().isoformat()
        lock["comment"] = f"{stamp} {_flag_value(argv[1:], '--reason')}\n{lock['comment']}"
        lock["tools"] = len(defs)
        lock["sha256"] = want_tools
        lock["sha256_wire_face"] = want_face
        lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        print("  " + ui.ok(f"lock written by this command: {', '.join(drift) or 'no field'}"))
        return 0

    print(ui.err(f"unknown subcommand: dhole wire {action} "
                 "(try: dhole wire ledger|lock)"), file=sys.stderr)
    return 2


def _flag_value(args: list[str], flag: str) -> str:
    """`--reason 一句话` 或 `--reason=一句话`，没有则空串。"""
    for i, part in enumerate(args):
        if part == flag and i + 1 < len(args):
            return args[i + 1]
        if part.startswith(f"{flag}="):
            return part.split("=", 1)[1]
    return ""


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


def _cmd_watch(argv: list[str], checker=None) -> int:
    """`dhole watch add|list|remove|run` — a persistent change watchlist.

    The MCP tool answers one URL at a time; a scheduler needs a list that
    survives restarts and an exit code a cron job can branch on. ``run`` still
    calls the same ``web_read(extraction_type='check')`` path (through a real
    server instance), so the baseline, conditional request and diff window are
    the product's, not a second implementation.

    Exit codes are the contract: 0 = nothing changed, 1 = at least one page
    changed, 2 = at least one check failed (including a corrupt watchlist or a
    run already in progress). Errors outrank changes: a sweep that lost half its
    URLs must not look like a quiet "nothing changed" night.
    """
    import asyncio
    import json as _json

    from dhole_mcp import cli_ui as ui
    from dhole_mcp import watch as watch_mod

    args = [a for a in argv if a and a.strip()]
    action = args[0].lower() if args and not args[0].startswith("-") else "list"
    rest = args[1:] if action == args[0].lower() and args else args
    json_out = "--json" in rest
    rest = [a for a in rest if a != "--json"]

    try:
        if action in ("list", "ls", "status"):
            state = watch_mod.load_state()
            if json_out:
                print(_json.dumps(state, ensure_ascii=False, indent=2))
                return 0
            print("  " + ui.dim(f"file: {watch_mod.state_path()}"))
            if not state["watches"]:
                print("  " + ui.dim("nothing watched yet") + "  " + ui.dim("|") + "  "
                      + ui.cmd("dhole watch add https://example.com/page"))
                return 0
            for row in state["watches"]:
                verdict = row.get("last_verdict") or "never checked"
                paint = {"changed": ui.warn, "error": ui.err,
                         "baseline": ui.dim, "unchanged": ui.ok}.get(verdict, ui.dim)
                label = row.get("label") or ""
                print(f"    {paint(verdict.ljust(13))} {ui.cmd(row['url'])}"
                      + (ui.dim(f"  [{label}]") if label else ""))
                if row.get("last_checked_at"):
                    extra = f"last {row['last_checked_at']} · HTTP {row.get('last_status')}"
                    if row.get("last_error"):
                        extra += f" · {row['last_error']}"
                    print("      " + ui.dim(extra))
            print("  " + ui.dim("run with") + "  " + ui.cmd("dhole watch run"))
            return 0

        if action == "add":
            positional = [a for a in rest if not a.startswith("-")]
            if not positional:
                print(ui.err("usage: dhole watch add <url> [--label TEXT] [--json]"),
                      file=sys.stderr)
                return 2
            label = _flag_value(rest, "--label")
            try:
                saved = watch_mod.add(positional[0], label=label)
            except (ValueError, watch_mod.WatchStateError) as exc:
                print(ui.err(str(exc)), file=sys.stderr)
                return 2
            if json_out:
                print(_json.dumps(saved, ensure_ascii=False, indent=2))
                return 0
            verb = "watching" if saved["created"] else "already watching (label updated)"
            print(ui.branded(ui.ok(verb), ui.cmd(saved["watch"]["url"])))
            print("  " + ui.dim("the first `dhole watch run` records its baseline; "
                                "the next run can answer changed/unchanged"))
            return 0

        if action in ("remove", "rm", "delete"):
            positional = [a for a in rest if not a.startswith("-")]
            if not positional:
                print(ui.err("usage: dhole watch remove <url>"), file=sys.stderr)
                return 2
            try:
                removed = watch_mod.remove(positional[0].split("#", 1)[0])
            except watch_mod.WatchStateError as exc:
                print(ui.err(str(exc)), file=sys.stderr)
                return 2
            if json_out:
                print(_json.dumps({"removed": removed, "url": positional[0]},
                                  ensure_ascii=False))
            if removed:
                print(ui.ok(f"stopped watching {positional[0]}"))
                return 0
            print(ui.dim(f"{positional[0]} was not in the watchlist"), file=sys.stderr)
            return 1

        if action == "run":
            only = []
            for i, a in enumerate(rest):
                if a.startswith("--only="):
                    only.append(a.split("=", 1)[1])
                elif a == "--only" and i + 1 < len(rest):
                    only.append(rest[i + 1])
            timeout_raw = _flag_value(rest, "--timeout") or "30000"
            try:
                timeout_ms = int(timeout_raw)
                if timeout_ms <= 0:
                    raise ValueError
            except ValueError:
                print(ui.err("--timeout must be a positive number of milliseconds"),
                      file=sys.stderr)
                return 2
            force = _flag_value(rest, "--force-fetcher") or None
            if force not in (None, "http", "stealthy"):
                print(ui.err("--force-fetcher must be http or stealthy"), file=sys.stderr)
                return 2

            srv = MasterFetchServer()

            async def _default_checker(u: str) -> dict:
                row = await srv.web_read(url=u, extraction_type="check",
                                         timeout=timeout_ms, force_fetcher=force)
                if isinstance(row, dict):
                    return row
                return {"changed": None, "status": int(row.status or 0),
                        "error": row.error or "check returned no verdict",
                        "note": row.next_action}

            async def _sweep():
                return await watch_mod.run_locked(
                    checker or _default_checker,
                    only=only or None)

            try:
                result = asyncio.run(_sweep())
            except watch_mod.WatchBusy as exc:
                print(ui.err(str(exc)), file=sys.stderr)
                return 2
            except watch_mod.WatchStateError as exc:
                print(ui.err(str(exc)), file=sys.stderr)
                return 2
            if json_out:
                print(_json.dumps(result, ensure_ascii=False, indent=2))
                return result["exit_code"]
            if result["unknown"]:
                print(ui.err("not watched: " + ", ".join(result["unknown"])),
                      file=sys.stderr)
                return 2
            if not result["checked"]:
                print("  " + ui.dim("nothing watched yet") + "  " + ui.dim("|") + "  "
                      + ui.cmd("dhole watch add https://example.com/page"))
                return result["exit_code"]
            for row in result["rows"]:
                paint = {"changed": ui.warn, "error": ui.err,
                         "baseline": ui.dim, "unchanged": ui.ok}.get(row["verdict"], ui.dim)
                line = (f"    {paint(row['verdict'].ljust(10))} {ui.cmd(row['url'])}"
                        f"  " + ui.dim(f"HTTP {row['status']}"))
                if row.get("label"):
                    line += ui.dim(f"  [{row['label']}]")
                if row.get("error"):
                    line += "  " + ui.err(row["error"])
                print(line)
            print("  " + ui.dim(f"{result['checked']} checked · "
                                f"{len(result['changed'])} changed · "
                                f"{len(result['unchanged'])} unchanged · "
                                f"{len(result['baseline'])} baseline · "
                                f"{len(result['errors'])} error")
                  + ui.dim(f"  (exit {result['exit_code']})"))
            return result["exit_code"]

        print(ui.err(f"unknown subcommand: {action} "
                     "(try: dhole watch add|list|remove|run)"), file=sys.stderr)
        return 2
    except watch_mod.WatchStateError as exc:
        print(ui.err(str(exc)), file=sys.stderr)
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
                # 断点只能在 `: ` 那一个界上：实测 `error:MetaSearchException` 被 18 列宽
                # 切成 "error:MetaSearchEx" + 换到原因栏的 "ception"，一个断在标识符中间的
                # 尾巴读起来像坏掉的输出，不像一句理由（同一个口径见 `_summary_label`）。
                detail = "" if err == status else err
                _head, _sep, _tail = status.rpartition(": ")
                if err == status and len(status) > 26 and _tail:
                    status, detail = (_head + _sep)[:26], _tail
                print(f"    {name.ljust(14)} " + ui.err(
                    f"{status.ljust(26)}  -  {elapsed:5.1f}s  {detail[:52]}"))
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
    _SUBCOMMANDS = ("engines", "skill", "doctor", "config", "watch", "wire")
    if len(_sys.argv) > 1:
        first = _sys.argv[1].lower()
        if first == "engines":
            raise SystemExit(_cmd_engines(_sys.argv[2:]))
        if first == "skill":
            raise SystemExit(_cmd_skill(_sys.argv[2:]))
        if first == "config":
            raise SystemExit(_cmd_config(_sys.argv[2:]))
        if first == "watch":
            raise SystemExit(_cmd_watch(_sys.argv[2:]))
        if first == "wire":
            raise SystemExit(_cmd_wire(_sys.argv[2:]))
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
