"""`dhole` 的命令面：抓取/搜索/爬行/回忆四条内容命令、运维命令、体检、技能安装、引擎健康面板与进程入口。

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

import difflib
import json
import os
import sys

from pathlib import Path

from dhole_mcp import cli_spec
from dhole_mcp.server import MasterFetchServer


def _help_epilog() -> str:
    """`dhole --help` 的 epilog。

    内容只有一份作者：``cli_spec``。这里不再手抄命令表 —— 手抄的那一份在
    17.3 就漂过一次（read 的 nested_* 与 search 的 cache_ttl 只活在 --arg 里）。
    """
    return cli_spec.top_help()


def _skill_manifest(root) -> dict:
    """技能目录下每个文件的 relpath -> sha256[:16]。"""
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

    为什么值得有一扇命令门：`tools/list` 的每一个字符都是**每次连接**付一次的钱。想
    知道"某一句值多少字符"，从前只能改完再跑测试 —— 那把一次措辞决定变成一次试错，
    而试错的反馈是"红了"，不是"这句 211 字、可以从那句 168 字里腾出来"。这里把测量
    交到命令上：按工具、按参数拆开，改之前就能算。

    `--write` 是锁的**唯一写者**。那条锁文件的 docstring 早就写着"不许手抄"（阶段 7
    作者手抄时丢过一个开头的 3），而一条只能被"记得去算"的规矩迟早被破；现在算的人
    只有一个，而写它必须附一句理由 —— 理由进 `comment`，那是这块面孔的变更史，
    没有理由的改写不是更新锁，是抹掉一次决定。
    """
    import json

    from dhole_mcp import cli_ui as ui
    from dhole_mcp import wire as _wire
    from dhole_mcp.server import ACTIVE_INSTRUCTIONS, DHOLE_INSTRUCTIONS

    if _wants_help(argv):
        print(cli_spec.command_help("wire"))
        return 0
    raw = [a for a in argv if a and a.strip()]
    json_out = "--json" in raw
    pretty = "--pretty" in raw
    args = [a for a in raw if a not in ("--json", "--pretty")]
    action = (args[0].lower() if args else "ledger")
    rest = args[1:]
    if action in ("ledger", "ls", "list", "cost", ""):
        all_defs = MasterFetchServer._TOOL_DEFS
        active = MasterFetchServer._enabled_tool_defs()
        n_all, chars_all = _wire.face_cost(all_defs, DHOLE_INSTRUCTIONS)
        n_act, chars_act = _wire.face_cost(active, ACTIVE_INSTRUCTIONS)
        rows = sorted(_wire.face_ledger(all_defs), key=lambda r: -r["chars"])
        if json_out:
            _json_emit({
                "default": {"tools": n_all, "chars": chars_all,
                            "tokens": round(chars_all / 4.16)},
                "this_run": ({"tools": n_act, "chars": chars_act}
                             if chars_act != chars_all else None),
                "rows": rows,
            }, pretty=pretty)
            return 0
        print("  " + ui.dim("connect face (paid once per MCP connection):"))
        print(f"    default  {n_all:>2} tool(s)  {chars_all:>6} chars"
              f"  ~{chars_all / 4.16:.0f} tokens (4.16 = this payload's measured chars/token)")
        if chars_act != chars_all:
            print(f"    this run {n_act:>2} tool(s)  {chars_act:>6} chars"
                  "  (DHOLE_TOOLS narrowed the face)")
        if "--all" in rest:
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
        write = "--write" in rest
        reason = _flag_value(rest, "--reason")
        lock_path = _tool_defs_lock_path()
        if not lock_path.exists():
            message = (f"no lock file at {lock_path} (an installed wheel ships no tests/;"
                       " the lock lives in the source repo)")
            if json_out:
                return _json_error(message)
            print(ui.err(message), file=sys.stderr)
            return 2
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        defs = MasterFetchServer._TOOL_DEFS
        want_tools = _wire.tool_defs_digest(defs)
        want_face = _wire.wire_face_digest(defs, DHOLE_INSTRUCTIONS)
        drift = [k for k, want in (("sha256", want_tools),
                                   ("sha256_wire_face", want_face),
                                   ("tools", str(len(defs))))
                 if str(lock.get(k)) != want]
        if json_out and not write:
            _json_emit({
                "path": str(lock_path),
                "matches": not drift,
                "drift": drift,
                "sha256": want_tools,
                "sha256_wire_face": want_face,
                "tools": len(defs),
            }, pretty=pretty)
            return 1 if drift else 0
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
            message = ("--write needs --reason: the lock's comment is the change log "
                       "of this face; writing without one erases a decision")
            if json_out:
                return _json_error(message)
            print(ui.err(message), file=sys.stderr)
            return 2
        import datetime
        stamp = datetime.date.today().isoformat()
        lock["comment"] = f"{stamp} {reason}\n{lock['comment']}"
        lock["tools"] = len(defs)
        lock["sha256"] = want_tools
        lock["sha256_wire_face"] = want_face
        lock_path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        if json_out:
            _json_emit({
                "path": str(lock_path),
                "written": True,
                "drift": drift,
                "sha256": want_tools,
                "sha256_wire_face": want_face,
                "tools": len(defs),
            }, pretty=pretty)
            return 0
        print("  " + ui.ok(f"lock written by this command: {', '.join(drift) or 'no field'}"))
        return 0

    message = (f"unknown subcommand: dhole wire {action} "
               "(try: dhole wire ledger|lock)")
    if json_out:
        return _json_error(message)
    print(ui.err(message), file=sys.stderr)
    return 2


def _flag_value(args: list[str], flag: str) -> str:
    """`--reason 一句话` 或 `--reason=一句话`，没有则空串。"""
    for i, part in enumerate(args):
        if part == flag and i + 1 < len(args):
            return args[i + 1]
        if part.startswith(f"{flag}="):
            return part.split("=", 1)[1]
    return ""


def _run_tool(name: str, args: dict) -> dict | str:
    """一次 MCP 工具调用：收 dict、回回执 —— 不开进程、不走 stdio。

    边界是 `MasterFetchServer._dispatch(name, args)`（计划 §4.4 方案 A）。CLI 只取
    返回元组的第二项（结构化回执），第一项（ContentBlock 列表）直接丢弃，于是 MCP
    侧改块形状影响不到 CLI。`_arg_notes_scope()` 必须包在外面：被钳制的参数要出现
    在回执的 summary 里，那正是 CLI 最该显示的东西。

    唯一的例外是 `sys_guide`：它按设计只发散文（JSON 会把每个换行转义一遍，
    又买不到任何字段），没有结构化的第二项可拿 —— 那个工具的文字通道就是答案。

    这里依赖一个私有方法（计划 §7.3 的风险 3）：测试钉住"返回元组第二项"这一条。
    """
    import asyncio

    from dhole_mcp.context import _arg_notes_scope

    srv = MasterFetchServer()

    async def _call():
        with _arg_notes_scope():
            return await srv._dispatch(name, args)

    result = asyncio.run(_call())
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, list):
        text = "".join(getattr(block, "text", "") for block in result
                       if getattr(block, "type", "") == "text")
        if text:
            return text
    raise ValueError(
        f"{name} returned {type(result).__name__}, not the (content, structured) "
        "pair every CLI command reads - the dispatcher contract changed")


_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def _cli_parse(argv: list[str], spec: dict) -> tuple[list[str], dict, str]:
    """`--flag value` / `--flag=value` / 布尔开关 / 位置参数，所有命令共用。

    spec = {flag: (key, kind)}，kind 是 bool / int / float / str / list / json / kv。
    布尔还接受 ``--flag=false`` 与 ``--no-flag`` —— 后者让"临时关掉一个默认开的
    开关"不必记另一种拼写。未知旗标会给出最近的候选，而不是只说一句 unknown。

    返回 ``(positionals, values, error)``；error 非空时调用方按用法错误退出 2。
    """
    positionals: list[str] = []
    values: dict = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            positionals.extend(argv[i + 1:])
            break
        if arg.startswith("-") and arg != "-":
            flag, sep, inline = arg.partition("=")
            negated = False
            if flag.startswith("--no-") and flag not in spec:
                positive = "--" + flag[5:]
                if positive in spec and spec[positive][1] == "bool":
                    flag = positive
                    negated = True
            entry = spec.get(flag)
            if entry is None:
                near = difflib.get_close_matches(flag, list(spec), n=1, cutoff=0.6)
                hint = f" (did you mean {near[0]}?)" if near else ""
                return [], {}, f"unknown flag: {arg}{hint}"
            key, kind = entry
            if kind == "bool":
                if negated:
                    if sep:
                        return [], {}, f"{flag} takes no value"
                    values[key] = False
                elif sep:
                    raw = inline.strip().lower()
                    if raw in _BOOL_TRUE:
                        values[key] = True
                    elif raw in _BOOL_FALSE:
                        values[key] = False
                    else:
                        return [], {}, f"{flag} takes true or false"
                else:
                    values[key] = True
                i += 1
                continue
            if negated:
                return [], {}, f"{flag} takes a value"
            raw = inline if sep else None
            if raw is None:
                if i + 1 >= len(argv):
                    return [], {}, f"{flag} needs a value"
                raw = argv[i + 1]
                i += 1
            if kind == "kv":
                name, eq, value = raw.partition("=")
                if not eq or not name.strip():
                    return [], {}, f"{flag} needs NAME=VALUE"
                values.setdefault(key, {})[name.strip()] = _loose_json(value)
            elif kind == "list":
                values.setdefault(key, []).extend(
                    part.strip() for part in raw.split(",") if part.strip())
            elif kind in ("json", "str"):
                values[key] = raw
            else:
                try:
                    values[key] = int(raw) if kind == "int" else float(raw)
                except (TypeError, ValueError):
                    kind_name = "whole number" if kind == "int" else "number"
                    return [], {}, f"{flag} must be a {kind_name}"
            i += 1
            continue
        positionals.append(arg)
        i += 1
    return positionals, values, ""


def _timeout_ms(seconds) -> int | None:
    """CLI 的 `--timeout` 收秒，工具层收毫秒 —— 换算只发生在这一处。"""
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    try:
        return int(value * 1000) if value > 0 else None
    except OverflowError:
        return None


def _loose_json(raw: str):
    """`--arg name=value` 的值：是 JSON 就按 JSON（数字/布尔/数组/对象），否则当字符串。"""
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def _text_source(raw: str, flag: str) -> str:
    """`-` = stdin，`@path` = 那个文件，其余 = 字面文本（JSON 参数用）。"""
    try:
        if raw == "-":
            return sys.stdin.read()
        if raw.startswith("@"):
            return Path(raw[1:]).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{flag} cannot read {raw!r}: {exc}") from exc
    return raw


def _file_source(raw: str, flag: str) -> str:
    """`--body-file` / `--password-file`：路径就是路径，`-` 是 stdin。"""
    try:
        if raw == "-":
            return sys.stdin.read()
        return Path(raw).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{flag} cannot read {raw!r}: {exc}") from exc


def _json_source(raw: str, flag: str):
    """`--actions` / `--headers` / `creds set` 的 JSON：支持字面量、@file 与 stdin。"""
    text = _text_source(raw, flag) if raw == "-" or raw.startswith("@") else raw
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{flag} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})") from exc


def _cred_persistence_enabled() -> bool:
    """CLI 侧的唯一判定点：实际能落盘才算持久的默认身份。"""
    from dhole_mcp import identity

    return identity.persistence_enabled()


def _bad_usage(message: str, usage: str, json_out: bool = False) -> int:
    """用法错误：文字进 stderr；`--json` 时交一份可解析的错误信封到 stdout。

    stdout 的契约是"只装内容"，而用法错误没有内容 —— 给脚本一份
    ``{"error": ..., "exit_code": 2}`` 比让它从 stderr 里抠自然语言可靠。
    """
    from dhole_mcp import cli_ui as ui

    if json_out:
        print(json.dumps({"error": message, "exit_code": 2},
                         ensure_ascii=False, separators=(",", ":")))
        return 2
    print(ui.err(message), file=sys.stderr)
    print(ui.dim(f"  usage: {usage}"), file=sys.stderr)
    return 2


def _wants_help(argv: list[str]) -> bool:
    return any(a in ("-h", "--help") for a in argv)


def _json_emit(payload, pretty: bool = False) -> None:
    print(json.dumps(payload, ensure_ascii=False,
                     indent=2 if pretty else None,
                     separators=None if pretty else (",", ":")))


def _json_error(message: str, code: int = 2) -> int:
    _json_emit({"error": message, "exit_code": code})
    return code


def _output_error(opts: dict) -> str:
    """输出形态之间的互斥/依赖，在任何网络动作之前判掉。"""
    if opts.get("json") and opts.get("ndjson"):
        return "--json and --ndjson are different shapes; pick one"
    # `--dry-run` 交的就是一份 JSON，所以它也能吃 --pretty。
    if opts.get("pretty") and not (opts.get("json") or opts.get("dry_run")):
        return "--pretty needs --json (NDJSON is one object per line)"
    if opts.get("quiet") and opts.get("verbose"):
        return "--quiet and --verbose contradict each other"
    return ""


def _extract_color_flags(argv: list[str]) -> tuple[list[str], str]:
    """颜色旗标的实现住在 cli_ui（轻量入口也要用），这里只留一个转发。"""
    from dhole_mcp import cli_ui as ui

    return ui.extract_color_flags(argv)


def _refuse(exc: Exception, json_out: bool) -> None:
    """一句拒绝只有两种说法：`--json` 交信封，否则交一行红字到 stderr。

    `_run_tool_or_refuse` 与 `--dry-run` 都走这里 —— 后者以前自己拼一份输出，
    于是同一个"参数不对"在预演里根本不出现（见 `_dry_run`）。
    """
    from dhole_mcp import cli_ui as ui

    if json_out:
        print(json.dumps({"error": str(exc), "exit_code": 2},
                         ensure_ascii=False, separators=(",", ":")))
    else:
        print(ui.err(str(exc)), file=sys.stderr)


def _run_tool_or_refuse(name: str, args: dict, json_out: bool = False):
    """参数/名册类拒绝折成一句话；返回 None 让命令层退出 2。"""
    try:
        return _run_tool(name, args)
    except ValueError as exc:
        _refuse(exc, json_out)
        return None


def _progress(opts: dict, label: str):
    """`--progress`：只在 stderr 是 TTY 时画一行原地刷新的耗时。

    不是默认开的：管道/日志里一行每秒刷新的控制字符是噪声。`--quiet` 与
    `--json` 也不画 —— 那两种读法要的是干净的另一条流。
    """
    import contextlib
    import threading
    import time

    @contextlib.contextmanager
    def _idle():
        yield

    if (not opts.get("progress") or opts.get("quiet") or opts.get("json")
            or not sys.stderr.isatty()):
        return _idle()

    @contextlib.contextmanager
    def _spin():
        stop = threading.Event()
        started = time.monotonic()
        frames = "|/-\\"

        def _draw():
            n = 0
            while not stop.wait(0.12):
                sys.stderr.write(
                    f"\r  {label} {frames[n % len(frames)]} "
                    f"{time.monotonic() - started:.1f}s")
                sys.stderr.flush()
                n += 1

        thread = threading.Thread(target=_draw, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=1)
            sys.stderr.write("\r" + " " * 42 + "\r")
            sys.stderr.flush()

    return _spin()


def _dry_run(tool: str, args: dict, opts: dict) -> int:
    """`--dry-run`：把绑定结果原样交出来，然后立刻成功退出。

    旗标面的自检口 —— 参数怎么落成工具参数、`--arg` 覆盖了什么，都在这里看得见，
    不必真发一次请求去猜。JSON 是唯一形状：它没有内容通道可占。

    它过**同一道参数闸**，只是不拨号。此前不过：`--arg nope=1` 预演时原样打印、
    rc=0，真发才被拒 —— 一个自称"先看绑定再决定"的开关，把唯一会让这次调用失败
    的那件事留到了它不该在场的时候。`_reject_unknown_args` 只读 schema，不发网络。
    """
    from dhole_mcp.server import _reject_unknown_args

    try:
        _reject_unknown_args(tool, args)
    except ValueError as exc:
        _refuse(exc, bool(opts.get("json")))
        return 2
    print(json.dumps({"dry_run": True, "tool": tool, "args": args},
                     ensure_ascii=False,
                     indent=2 if opts.get("pretty") else None,
                     separators=None if opts.get("pretty") else (",", ":")))
    return 0


def _finish(tool: str, args: dict, command: str, opts: dict, renderer,
            usage: str = "") -> int:
    if usage:
        err = _output_error(opts)
        if err:
            return _bad_usage(err, usage, json_out=bool(opts.get("json")))
    if opts.get("dry_run"):
        return _dry_run(tool, args, opts)
    with _progress(opts, command):
        payload = _run_tool_or_refuse(tool, args, json_out=bool(opts.get("json")))
    if payload is None:
        return 2
    return _emit(command, payload, opts, renderer)


def _emit(command: str, payload, opts: dict, renderer) -> int:
    """把回执交给渲染器，按 0/1 退出；`--json` 时 stdout 只有完整信封。

    `--ndjson` 是行式投影：批量/爬行/搜索/订阅各交一行一个对象，供 ``jq -c``
    或逐行读取的消费端使用。它不改变退出码的聚合口径。
    """
    from dhole_mcp import cli_render
    from dhole_mcp import cli_ui as ui

    if opts.get("ndjson"):
        for row in cli_render.ndjson_rows(command, payload):
            print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        if isinstance(payload, dict) and payload.get("error"):
            print(ui.err(f"error: {payload['error']}"), file=sys.stderr)
        return cli_render.exit_code(command, payload)
    if opts.get("json"):
        print(json.dumps(payload, ensure_ascii=False,
                         indent=2 if opts.get("pretty") else None,
                         separators=None if opts.get("pretty") else (",", ":")))
        return cli_render.exit_code(command, payload)
    body, meta = renderer(payload, verbose=bool(opts.get("verbose")))
    if body:
        sys.stdout.write(body)
        if not body.endswith("\n"):
            sys.stdout.write("\n")
    if meta:
        if not opts.get("quiet"):
            print(ui.dim(meta), file=sys.stderr)
        else:
            # `--quiet` 只关元数据，不关失败：一行 "failed: …" 消失了，
            # 消费端就只剩一个非零退出码，没有原因。
            keep = [line for line in meta.splitlines()
                    if line.lstrip().startswith(
                        ("error:", "failed:", "failed page:", "pending:"))]
            if keep:
                print(ui.dim("\n".join(keep)), file=sys.stderr)
    return cli_render.exit_code(command, payload)


# ── 内容命令的通用装配 ───────────────────────────────────────────────
#
# 旗标名、类型、帮助、补全全部来自 cli_spec。这里只剩两件命令层才懂的事：
# 位置参数怎么变成工具参数，以及几处跨参数的形状校验（批量 vs 单页、body 互斥、
# 必填的 method/actions）。手写旗标表的时代到此为止 —— 漂一颗 nested_max 就少一颗
# 的日子不再重来。


def _positional_args(name: str, positionals: list[str],
                     opts: dict) -> tuple[dict, str]:
    """位置参数 → 工具参数；失败返回一句用法错误。"""
    cmd = cli_spec.CONTENT_COMMANDS[name]
    mode = cmd.positional_mode
    if mode == "urls":
        urls, err = _url_list(name, positionals, opts)
        if err:
            return {}, err
        if not urls:
            # 位置参数可以缺席：`--arg url=...` / `--arg urls=[...]` 是同一扇门，
            # 最终由绑定后的必填检查说话。
            return {}, ""
        single_key, multi_key = cmd.url_keys
        if len(urls) == 1 and single_key != multi_key:
            return {single_key: urls[0]}, ""
        # 两个键相同时（feed_read 只有 urls）单条也必须留在列表里：工具收的是
        # 数组，折成裸字符串会被"参数类型"拒掉，而那正是本条要修的缺陷。
        return {multi_key: urls}, ""
    if mode == "query":
        query = " ".join(a for a in positionals if a.strip()).strip()
        return ({"query": query} if query else {}), ""
    if mode == "one_url":
        urls = [a for a in positionals if a.strip()]
        if not urls:
            return {}, ""
        if len(urls) != 1:
            extra = " - pass the extra pages to dhole read" if len(urls) > 1 else ""
            return {}, f"{name} takes one URL{extra}"
        return {"url": urls[0]}, ""
    if mode == "file":
        files = [a for a in positionals if a.strip()]
        if not files:
            return {}, ""
        if len(files) != 1:
            return {}, "doc reads one file per call"
        return {"file_path": files[0]}, ""
    if mode == "none":
        if positionals:
            return {}, f"{name} takes no positional arguments (got {positionals[0]!r})"
        return {}, ""
    return {}, ""


def _url_list(name: str, positionals: list[str],
              opts: dict) -> tuple[list[str], str]:
    """URL 位置参数 + `--urls-file` / `-`（stdin）的合并。"""
    raw_file = opts.get("urls_file")
    if raw_file is not None and positionals == ["-"]:
        return [], "pass --urls-file or -, not both"
    if positionals == ["-"]:
        try:
            text = sys.stdin.read()
        except OSError as exc:
            return [], f"cannot read URLs from stdin: {exc}"
        urls = [line.strip() for line in text.splitlines() if line.strip()]
    elif "-" in positionals:
        return [], "a URL list from stdin must be the only positional"
    else:
        urls = [u.strip() for u in positionals if u.strip()]
    if raw_file is not None:
        try:
            text = _file_source(raw_file, "--urls-file")
        except ValueError as exc:
            return [], str(exc)
        urls.extend(line.strip() for line in text.splitlines() if line.strip())
    return urls, ""


def _bind_flags(name: str, opts: dict, args: dict) -> str:
    """把命名旗标绑到工具参数；返回空串 = 成功。

    `--arg` 最后合并；同名即错，不让命名旗标与逃生口静默打架。
    """
    cmd = cli_spec.CONTENT_COMMANDS[name]
    skip = {"json", "ndjson", "pretty", "verbose", "quiet", "progress", "args",
            "dry_run"}
    for flag in cmd.flags:
        key = flag.key
        if key not in opts or key in skip:
            continue
        value = opts[key]
        transform = flag.transform
        if transform == "urls_file":
            continue
        if flag.choices:
            # 封闭值集在解析期就收口：非法值不该付一次往返、也不该以 1 退出 ——
            # 它是用法错误（2），与服务端 `_coerce_enum_arg` 的判据同源。
            picked = value if isinstance(value, list) else [value]
            bad = [v for v in picked
                   if not isinstance(v, str)
                   or v.strip().lower() not in flag.choices]
            if bad:
                return (f"{flag.name} must be one of: {', '.join(flag.choices)}"
                        f" (got {bad[0]!r})")
            if isinstance(value, list):
                value = [v.strip().lower() for v in value]
            else:
                value = value.strip().lower()
        if transform == "timeout":
            timeout = _timeout_ms(value)
            if timeout is None:
                return f"{flag.name} must be a positive number of seconds"
            args["timeout"] = timeout
        elif transform == "json":
            try:
                args[flag.tool_arg] = _json_source(value, flag.name)
            except ValueError as exc:
                return str(exc)
        elif transform == "json_object":
            try:
                parsed = _json_source(value, flag.name)
            except ValueError as exc:
                return str(exc)
            if not isinstance(parsed, dict):
                return f"{flag.name} needs a JSON object"
            args[flag.tool_arg] = parsed
        elif transform == "json_array":
            try:
                parsed = _json_source(value, flag.name)
            except ValueError as exc:
                return str(exc)
            if not isinstance(parsed, list):
                return f"{flag.name} needs a JSON array"
            args[flag.tool_arg] = parsed
        elif transform in ("password_file", "body_file"):
            try:
                text = _file_source(value, flag.name)
            except ValueError as exc:
                return str(exc)
            args[flag.tool_arg] = (text.rstrip("\r\n")
                                   if transform == "password_file" else text)
        elif transform:
            return f"internal: unknown transform {transform!r} on {flag.name}"
        else:
            args[flag.tool_arg] = value

    extra = opts.get("args") or {}
    dupes = sorted(set(args) & set(extra))
    if dupes:
        return f"--arg {dupes[0]}= duplicates a named flag"
    args.update(extra)
    return ""


def _flag_name(name: str, key: str) -> str:
    cmd = cli_spec.CONTENT_COMMANDS[name]
    for flag in cmd.flags:
        if flag.key == key:
            return flag.name
    return "--" + key.replace("_", "-")


def _content_main(name: str, argv: list[str]) -> int:
    """所有内容命令的唯一入口：解析、装配、校验，然后交给工具层。"""
    from dhole_mcp import cli_render as render
    from dhole_mcp import cli_ui as ui

    cmd = cli_spec.CONTENT_COMMANDS[name]
    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, cmd.usage, json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help(name))
        return 0
    ui.utf8_stdio()

    positionals, opts, err = _cli_parse(argv, cli_spec.parse_spec(name))
    json_out = bool(opts.get("json"))
    if err:
        return _bad_usage(err, cmd.usage, json_out=json_out)
    err = _output_error(opts)
    if err:
        return _bad_usage(err, cmd.usage, json_out=json_out)
    for left, right in cmd.mutually_exclusive:
        if left in opts and right in opts:
            return _bad_usage(
                f"pass {_flag_name(name, left)} or {_flag_name(name, right)}, not both",
                cmd.usage, json_out=json_out)

    args, err = _positional_args(name, positionals, opts)
    if err:
        return _bad_usage(err, cmd.usage, json_out=json_out)
    err = _bind_flags(name, opts, args)
    if err:
        return _bad_usage(err, cmd.usage, json_out=json_out)

    if cmd.positional_mode == "urls" and not (args.get("url") or args.get("urls")):
        return _bad_usage(f"{name} needs at least one URL (or --urls-file FILE)",
                          cmd.usage, json_out=json_out)
    if cmd.positional_mode == "one_url" and not args.get("url"):
        return _bad_usage(f"{name} takes one URL", cmd.usage, json_out=json_out)
    if cmd.positional_mode == "file" and not args.get("file_path"):
        return _bad_usage("doc takes one local file", cmd.usage, json_out=json_out)

    if name == "read" and "urls" in args:
        single = sorted(flag.name for flag in cmd.flags
                        if flag.single_only and flag.key in opts)
        if single:
            return _bad_usage(
                f"{single[0]} reads one page; pass a single URL",
                cmd.usage, json_out=json_out)
    if name == "search" and not args.get("query"):
        allowed = args.get("mode") == cmd.query_optional_mode
        if not allowed:
            return _bad_usage(
                "search needs a query (or --mode find_similar --similar-to URL)",
                cmd.usage, json_out=json_out)
    if cmd.positional_mode == "query" and name != "search" and not args.get("query"):
        return _bad_usage(f"{name} needs a query", cmd.usage, json_out=json_out)
    for required in cmd.required_args:
        if args.get(required):
            continue
        message = {
            "method": "request needs --method (POST/PUT/PATCH/DELETE/HEAD)",
            "actions": "act needs --actions (a JSON array, @file, or -)",
        }.get(required, f"{name} needs {required}")
        return _bad_usage(message, cmd.usage, json_out=json_out)

    return _finish(cmd.tool, args, name, opts, getattr(render, name), cmd.usage)


def _cmd_read(argv: list[str]) -> int:
    """`dhole read` —— web_read 的 CLI 门。"""
    return _content_main("read", argv)


def _cmd_search(argv: list[str]) -> int:
    """`dhole search` —— web_search 的 CLI 门。"""
    return _content_main("search", argv)


def _cmd_crawl(argv: list[str]) -> int:
    """`dhole crawl` —— web_crawl 的 CLI 门（一个起点，不是 URL 列表）。"""
    return _content_main("crawl", argv)


def _cmd_recall(argv: list[str]) -> int:
    """`dhole recall` —— web_recall 的 CLI 门（只读本地 cache.db）。"""
    return _content_main("recall", argv)


def _cmd_discover(argv: list[str]) -> int:
    """`dhole discover` —— web_discover 的 CLI 门：stdout 是站点地图。"""
    return _content_main("discover", argv)


def _cmd_research(argv: list[str]) -> int:
    """`dhole research` —— 一次调用交一份带引用的证据包。"""
    return _content_main("research", argv)


def _cmd_request(argv: list[str]) -> int:
    """`dhole request` —— 一次非 GET 请求，发一次不重试。"""
    return _content_main("request", argv)


def _cmd_act(argv: list[str]) -> int:
    """`dhole act` —— 动作链，回执逐条进 stderr。"""
    return _content_main("act", argv)


def _cmd_doc(argv: list[str]) -> int:
    """`dhole doc` —— 本地文件转 markdown，不联网。"""
    return _content_main("doc", argv)


def _cmd_feed(argv: list[str]) -> int:
    """`dhole feed` —— RSS/Atom，按源分节；`--since` 是增量轮询。"""
    return _content_main("feed", argv)


_STATE_USAGE = cli_spec.usage("state")
_CREDS_USAGE = cli_spec.usage("creds")
_GUIDE_USAGE = cli_spec.usage("guide")


def _cmd_state(argv: list[str]) -> int:
    """`dhole state [--show ...] [--clear ...] [--confirm]` —— 本机状态的普查与抹除。

    `--clear sessions|cdp_hosts|all` 要 `--confirm`：那几类抹掉后要到下次访问
    才看得出来。没有 `--clear` 的调用只普查，什么都不动。
    """
    from dhole_mcp import cli_render as render
    from dhole_mcp import cli_ui as ui

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, _STATE_USAGE, json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("state"))
        return 0
    ui.utf8_stdio()
    positional, opts, err = _cli_parse(argv, cli_spec.parse_spec("state"))
    json_out = bool(opts.get("json"))
    if not err and positional:
        err = f"state takes no positional arguments (got {positional[0]!r})"
    if err:
        return _bad_usage(err, _STATE_USAGE, json_out=json_out)
    err = _output_error(opts)
    if err:
        return _bad_usage(err, _STATE_USAGE, json_out=json_out)

    for key, allowed in (("show", cli_spec.STATE_CLASSES),
                         ("clear", cli_spec.STATE_CLEAR_CLASSES)):
        values = [str(v).strip().lower() for v in opts.get(key) or []]
        bad = [v for v in values if v not in allowed]
        if bad:
            return _bad_usage(
                f"--{key} must be one of: {', '.join(allowed)} (got {bad[0]!r})",
                _STATE_USAGE, json_out=json_out)
        if values:
            opts[key] = values

    args: dict = {}
    for key in ("show", "clear"):
        if key in opts:
            args[key] = opts[key]
    if opts.get("confirm"):
        args["confirm"] = True
    clear = set(args.get("clear") or [])
    if (not opts.get("json") and not opts.get("quiet")
            and clear & {"sessions", "cred_defaults", "all"}
            and not _cred_persistence_enabled()):
        print(ui.dim(
            "note: persistence is off, so this process starts with an empty "
            "in-memory session/identity store; clearing those classes only affects "
            "this process's memory"), file=sys.stderr)
    return _finish("sys_state", args, "state", opts, render.state, _STATE_USAGE)


def _cmd_creds(argv: list[str]) -> int:
    """`dhole creds [show|set|unset]` —— 默认身份的只读投影。

    `set` 收一个 JSON 对象：字面量、`@file` 或 `-`（stdin）。凭据写在命令行上
    会进 shell 历史，所以 `@file` / stdin 是推荐写法；回执里永远只有脱敏后的值。
    """
    from dhole_mcp import cli_render as render
    from dhole_mcp import cli_ui as ui

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, _CREDS_USAGE, json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("creds"))
        return 0
    ui.utf8_stdio()
    positional, opts, err = _cli_parse(argv, cli_spec.parse_spec("creds"))
    json_out = bool(opts.get("json"))
    tokens = [a for a in positional if a.strip()]
    action = tokens[0].lower() if tokens and tokens[0].lower() in (
        "show", "set", "unset") else "show"
    rest = tokens[1:] if tokens and tokens[0].lower() == action else tokens
    if err:
        return _bad_usage(err, _CREDS_USAGE, json_out=json_out)
    err = _output_error(opts)
    if err:
        return _bad_usage(err, _CREDS_USAGE, json_out=json_out)

    # 这是 CLI 化最硬的一条跨进程事实：每条命令都是新进程，而身份存储默认只在
    # 内存里（identity._PERSIST 在 import 时读一次）。没有持久化时 `set` 会
    # "成功"、下一条命令就看不见 —— 那是一个效果为零却敢报成功的命令。拒绝它，
    # 并把那扇门指出来。判定用 identity.persistence_enabled()：开关打开但本机没有
    # DPAPI 时它同样降级为内存，只看环境变量会放过这一族。
    persistent = _cred_persistence_enabled()
    if action in ("set", "unset") and not persistent:
        return _bad_usage(
            "creds set/unset needs effective encrypted persistence "
            "(DHOLE_PERSIST_CREDENTIALS=1; encrypted storage needs Windows DPAPI): "
            "every CLI call is a new process, so the in-memory store ends when this "
            "command exits. Set it in the environment (or run `dhole config set "
            "DHOLE_PERSIST_CREDENTIALS 1`), then run this again.",
            _CREDS_USAGE, json_out=json_out)
    if (action == "show" and not persistent and not opts.get("json")
            and not opts.get("quiet")):
        print(ui.dim(
            "note: persistence is off, so this CLI process starts with an empty "
            "in-memory identity store; set DHOLE_PERSIST_CREDENTIALS=1 to read or "
            "write the encrypted store"), file=sys.stderr)

    if action == "show":
        if rest:
            return _bad_usage(f"creds show takes no arguments (got {rest[0]!r})",
                              _CREDS_USAGE, json_out=json_out)
        args: dict = {}
    elif action == "set":
        if len(rest) != 1:
            return _bad_usage(
                "creds set takes one JSON object (quote it, or use @file / -)",
                _CREDS_USAGE, json_out=json_out)
        try:
            value = _json_source(rest[0], "creds set")
        except ValueError as exc:
            return _bad_usage(str(exc), _CREDS_USAGE, json_out=json_out)
        if not isinstance(value, dict):
            return _bad_usage("creds set needs a JSON object", _CREDS_USAGE,
                              json_out=json_out)
        args = {"set": value}
    else:
        keys = [k.strip() for part in rest for k in part.split(",") if k.strip()]
        if not keys:
            return _bad_usage("creds unset needs at least one key name", _CREDS_USAGE,
                              json_out=json_out)
        args = {"unset": keys}
    return _finish("cred_defaults", args, "creds", opts, render.creds, _CREDS_USAGE)


def _cmd_guide(argv: list[str]) -> int:
    """`dhole guide [topic] [section]` —— dhole 自己的说明书。"""
    from dhole_mcp import cli_render as render
    from dhole_mcp import cli_ui as ui

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, _GUIDE_USAGE, json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("guide"))
        return 0
    ui.utf8_stdio()
    positional, opts, err = _cli_parse(argv, cli_spec.parse_spec("guide"))
    json_out = bool(opts.get("json"))
    parts = [a for a in positional if a.strip()]
    if not err and len(parts) > 2:
        err = "guide takes at most a topic and a section"
    if err:
        return _bad_usage(err, _GUIDE_USAGE, json_out=json_out)
    err = _output_error(opts)
    if err:
        return _bad_usage(err, _GUIDE_USAGE, json_out=json_out)

    args: dict = {}
    if len(parts) >= 1:
        args["topic"] = parts[0]
    if len(parts) >= 2:
        args["section"] = parts[1]
    return _finish("sys_guide", args, "guide", opts, render.guide, _GUIDE_USAGE)


def _mb(size: int) -> str:
    return f"{max(0, int(size)) / 1_000_000:.1f} MB"


class _DownloadProgress:
    """`dhole models fetch` 的字节进度：TTY 原地刷新，管道每 10% 打一行。

    进度只走 stderr —— stdout 要么是给人看的最终报告，要么是 `--json` 的完整回执，
    不该混进控制字符。
    """

    def __init__(self) -> None:
        self._tty = sys.stderr.isatty()
        self._active = ""
        self._last_pct = -1
        self._next_mark = 0
        self._last_mb = -1

    def __call__(self, name: str, done: int, total: int) -> None:
        if not self._tty:
            if name != self._active:
                self._active = name
                self._next_mark = 0
                self._last_mb = -1
            if total:
                step = 10 if total >= 50_000_000 else 50
                pct = int(done * 100 / total)
                if pct >= self._next_mark:
                    print(f"  {name}: {pct}% ({_mb(done)} / {_mb(total)})",
                          file=sys.stderr)
                    self._next_mark = (pct // step + 1) * step
            else:
                megabytes = done // (16 << 20)
                if megabytes > self._last_mb:
                    print(f"  {name}: {_mb(done)}", file=sys.stderr)
                    self._last_mb = megabytes
            return

        if name != self._active:
            self._clear()
            self._active = name
            self._last_pct = -1
        pct = int(done * 100 / total) if total else -1
        if pct == self._last_pct:
            return
        text = (f"  {name}  {pct}%  {_mb(done)} / {_mb(total)}" if total
                else f"  {name}  {_mb(done)}")
        sys.stderr.write("\r" + text + " " * 8)
        sys.stderr.flush()
        self._last_pct = pct

    def _clear(self) -> None:
        if self._tty and self._active:
            sys.stderr.write("\r" + " " * 72 + "\r")
            sys.stderr.flush()

    def close(self) -> None:
        self._clear()


def _print_model_report(report: dict, ui) -> None:
    ok = bool(report.get("ok"))
    print(ui.branded(ui.cyan("reranker"),
                     f"{report.get('model')} · {'ready' if ok else 'failed'}"))
    for row in report.get("files") or []:
        status = str(row.get("status") or "")
        painted = {"downloaded": ui.ok(status), "present": ui.dim(status),
                   "failed": ui.err(status), "missing": ui.dim(status)}.get(
                       status, status)
        print(f"  {str(row.get('name') or ''):<16} "
              f"{_mb(int(row.get('bytes') or 0)):>10}  {painted}")
    if ok:
        if report.get("already_present"):
            print("  " + ui.dim("nothing to download; every file was already "
                                "on disk"))
        if report.get("verified"):
            print("  " + ui.dim(f"verified against the {report['verified']}"))
        print("  " + ui.dim("ready: ") + ui.cmd(str(report.get("dir") or "")))
    else:
        print(ui.err(f"error: {report.get('error') or 'download failed'}"),
              file=sys.stderr)
        print("  " + ui.dim("a partial .part file is kept; rerun the same "
                            "command to resume"), file=sys.stderr)


def _cmd_models(argv: list[str]) -> int:
    """`dhole models fetch` —— 一次性把重排器下完。

    搜索路径把模型加载放在守护线程里，CLI 几秒就退，于是 279MB 永远下不完（只
    留下 `.part`）。这条命令是那条结构性约束的出口：同步等待、显示字节进度、
    续传与 sha256 校验全部复用搜索那套判据。它只下字节，**不**选模型 ——
    选择仍是启动期的 `DHOLE_RERANK_MODEL`。
    """
    from dhole_mcp import cli_ui as ui
    from dhole_mcp import reranker

    usage = cli_spec.usage("models")
    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, usage, json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("models"))
        return 0
    positional, opts, err = _cli_parse(argv, cli_spec.parse_spec("models"))
    json_out = bool(opts.get("json"))
    if not err:
        action = positional[0].lower() if positional else ""
        if not action:
            err = "models needs a subcommand: fetch"
        elif action != "fetch":
            err = f"unknown subcommand: {positional[0]!r} (try: dhole models fetch)"
        elif len(positional) > 1:
            err = "models fetch takes no positional arguments"
    if err:
        return _bad_usage(err, usage, json_out=json_out)
    err = _output_error(opts)
    if err:
        return _bad_usage(err, usage, json_out=json_out)

    model = str(opts.get("model") or "").strip().lower()
    flag = cli_spec.flag_for("models", "--model")
    if model and flag and model not in flag.choices:
        return _bad_usage(
            f"--model must be one of: {', '.join(flag.choices)} (got {model!r})",
            usage, json_out=json_out)
    progress = None if opts.get("quiet") else _DownloadProgress()
    try:
        report = reranker.fetch_model(model or None, progress=progress)
    finally:
        if progress is not None:
            progress.close()
    if json_out:
        _json_emit(report, pretty=bool(opts.get("pretty")))
    else:
        _print_model_report(report, ui)
    return 0 if report.get("ok") else 1


def _cmd_config(argv: list[str]) -> int:
    """`dhole config list | set NAME VALUE | unset NAME` —— 持久化的旋钮。

    为什么要有这扇命令：当环境变量是唯一的门时，改一个数意味着编辑宿主的 MCP
    JSON 然后重启它。那不是配置，那是基建工作，也正是没人需要的旋钮同时是没人
    够得着的旋钮的原因。``~/.dhole/config.json`` 里的值在文件 mtime 变化时重读，
    所以按调用读取的旋钮在下一次调用就生效。

    ``env`` 仍然优先，刻意如此：每个既有安装都是经宿主 env 块配置自己的，文件
    绝不能悄悄覆盖一个用户已经做过的决定。导入时读取的旋钮在这里也可写，但会
    明说需要重启 —— 与 `dhole doctor` 给的是同一句注记。
    """
    from dhole_mcp import cli_ui as ui
    from dhole_mcp import env_registry as er

    if _wants_help(argv):
        print(cli_spec.command_help("config"))
        return 0
    raw = [a for a in argv if a and a.strip()]
    json_out = "--json" in raw
    pretty = "--pretty" in raw
    args = [a for a in raw if a not in ("--json", "--pretty")]
    action = args[0].lower() if args else "list"

    if action == "list":
        entries = er.file_entries()
        if json_out:
            rows = []
            for name in sorted(set(entries) | {n for n in os.environ if er.by_name(n)}):
                entry = er.by_name(name)
                value = entries.get(name)
                if value is None and name in os.environ:
                    value = os.environ[name]
                rows.append({
                    "name": name,
                    "value": entry.display_value(value) if entry else value,
                    "source": er.source_of(name),
                    "read_at": entry.read_at if entry else None,
                    "summary": entry.summary if entry else "not a dhole knob",
                    "registered": entry is not None,
                    "shadowed_by_env": name in os.environ,
                    "env_value": (entry.display_value(os.environ.get(name))
                                  if entry else os.environ.get(name))
                    if name in os.environ else None,
                })
            _json_emit({
                "path": str(er.config_path()),
                "registered_count": len(er.ALL),
                "entries": rows,
            }, pretty=pretty)
            return 0
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

    if action == "get":
        if len(args) < 2:
            message = "usage: dhole config get <NAME>"
            return _json_error(message) if json_out else _bad_usage(
                message, cli_spec.usage("config"))
        name = args[1].strip().upper()
        entry = er.by_name(name)
        if entry is None:
            message = f"{name} is not a dhole knob"
            return _json_error(message) if json_out else _bad_usage(
                message, cli_spec.usage("config"))
        source = er.source_of(name)
        if source == "env":
            value = os.environ.get(name)
        elif source == "file":
            value = er.file_entries().get(name)
        else:
            value = None
        shown = entry.display_value(value)
        if json_out:
            _json_emit({
                "name": name,
                "value": shown,
                "source": source,
                "read_at": entry.read_at,
                "summary": entry.summary,
                "path": str(er.config_path()),
            }, pretty=pretty)
            return 0
        print("  " + ui.cmd(f"{name} = {shown}")
              + ui.dim(f"  (from {source})")
              + ui.dim("  · " + entry.summary))
        if entry.read_at == "import":
            print("  " + ui.dim("read at import: restart the host to change it"))
        return 0

    if action == "set":
        if len(args) < 3:
            message = "usage: dhole config set <NAME> <value>"
            return _json_error(message) if json_out else _bad_usage(
                message, cli_spec.usage("config"))
        name, value = args[1].strip().upper(), " ".join(args[2:]).strip()
        try:
            path = er.set_entry(name, value)
        except ValueError as exc:
            return _json_error(str(exc)) if json_out else _bad_usage(
                str(exc), cli_spec.usage("config"))
        entry = er.by_name(name)
        shadowed = name in os.environ and os.environ.get(name) != value
        effect = ("shadowed-by-env" if shadowed
                  else "restart" if entry is not None and entry.read_at == "import"
                  else "next-call")
        if json_out:
            _json_emit({
                "name": name,
                "value": entry.display_value(value) if entry else value,
                "path": str(path),
                "effect": effect,
                "shadowed_by_env": shadowed,
                "env_value": entry.display_value(os.environ.get(name))
                if entry and name in os.environ else None,
            }, pretty=pretty)
            return 0
        if shadowed:
            print(ui.warn(f"written to {path.name}, but env already sets {name} - "
                          "env wins, so this value has no effect until the host's "
                          "env block drops it"))
        elif effect == "restart":
            print(ui.ok(f"{name} = {entry.display_value(value)}") + ui.dim(
                f"  ({path})  read at import: restart the host to take effect"))
        else:
            print(ui.ok(f"{name} = {entry.display_value(value) if entry else value}")
                  + ui.dim(f"  ({path})  takes effect on the next call"))
        return 0

    if action == "unset":
        if len(args) < 2:
            message = "usage: dhole config unset <NAME>"
            return _json_error(message) if json_out else _bad_usage(
                message, cli_spec.usage("config"))
        name = args[1].strip().upper()
        removed = er.unset_entry(name)
        if json_out:
            _json_emit({
                "name": name,
                "removed": removed,
                "path": str(er.config_path()),
                "env_still_sets": name in os.environ,
                "env_value": os.environ.get(name) if name in os.environ else None,
            }, pretty=pretty)
            return 0
        if removed:
            print(ui.ok(f"removed {name} from {er.config_path().name}"))
            if name in os.environ:
                print(ui.dim(f"  env still sets it to {os.environ.get(name)!r} "
                             "(that value now decides)"))
        else:
            print(ui.dim(f"{name} was not in {er.config_path()}"))
        return 0

    message = f"unknown subcommand: {action} (try: dhole config list|get|set|unset)"
    return _json_error(message) if json_out else _bad_usage(
        message, cli_spec.usage("config"))


#: R02-001：`watch run` 在自己的 CLI 进程里建 MasterFetchServer——MCP 服务内存里的
#: cred_defaults 豁免（默认 memory 模式，凭据不落盘）够不着它。私网 URL 在这一路
#: 是**结构性永久拒**，错误行要把这句结构事实与真出路说出去，而不是复述安全门原话
#: （add 时收下私网 URL 是设计：watch.py `_clean_url` 的注释在册）。
_PRIVATE_ROW_PREFIX = "invalid_request: URL targets internal/private IP"
_WATCH_PRIVATE_HINT = (
    " · watch runs in its own process, so the MCP-side cred_defaults waiver in "
    "memory mode never reaches it: set DHOLE_PERSIST_CREDENTIALS=1 in the watch "
    "process's own environment too (export it where `dhole watch run` runs - the "
    "MCP side having it is not enough), store allow_private once (cred_defaults), "
    "or watch a publicly reachable URL")


def _watch_row_error_hint(error: str) -> str:
    """私网族错误行返回 watch 语境指引，其余错误原样（返回空串 = 不加话）。"""
    if (error or "").startswith(_PRIVATE_ROW_PREFIX):
        return _WATCH_PRIVATE_HINT
    return ""


def _watch_error_with_hint(error: str) -> str:
    hint = _watch_row_error_hint(error)
    return f"{error}{hint}" if hint else (error or "")


def _cmd_watch(argv: list[str], checker=None) -> int:
    """`dhole watch add|list|remove|run` —— 一份持久的变更监视清单。

    MCP 工具一次回答一个 URL；而调度器需要一份能跨重启存活、退出码能被 cron
    分支的清单。``run`` 仍然调用同一条 ``web_read(extraction_type='check')``
    路径（经一个真实服务器实例），所以基线、条件请求与 diff 窗口都是产品的，
    不是第二套实现。

    退出码就是契约：0 = 没有任何变化，1 = 至少一页变了，2 = 至少一次检查失败
    （含损坏的监视清单或已有一次运行在进行）。错误压过变化：一次丢掉一半 URL
    的扫描，绝不能看起来像一个安静的"今晚没什么变化"。
    """
    import asyncio
    import json as _json

    from dhole_mcp import cli_ui as ui
    from dhole_mcp import watch as watch_mod

    if _wants_help(argv):
        print(cli_spec.command_help("watch"))
        return 0
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
            notify = "--notify" in rest

            srv = MasterFetchServer()

            async def _default_checker(u: str) -> dict:
                row = await srv.web_read(url=u, extraction_type="check",
                                         timeout=timeout_ms, force_fetcher=force)
                if isinstance(row, dict):
                    return row
                return {"changed": None, "status": int(row.status or 0),
                        "error": _watch_error_with_hint(
                            row.error or "check returned no verdict"),
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
            # 通知出口（提升清单 3）：默认关——cron 在无头机器上没有 toast，
            # 沉默是诚实的默认。--notify 把 changed 行弹成桌面通知；它是旁路，
            # 弹不弹、成不成都不动退出码的合同（0/1/2 只说页面的事）。
            if notify and result["changed"]:
                changed_rows = [r for r in result["rows"]
                                if r["verdict"] == "changed"]
                line = watch_mod.notify_changed(changed_rows)
                if line:
                    print("  " + ui.dim(line))
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


def _default_skill_target() -> Path:
    """技能默认装到哪儿：`~/.agents/skills/dhole-web`。

    选它是因为它是「谁愿意读谁读」的约定目录 —— dhole 不去猜宿主是谁，也不往任何
    宿主的私有目录里写文件。代价是有的宿主不扫这里（WorkBuddy 只扫
    `~/.workbuddy-ai/skills`），那种情况由调用方用 `--target` 点明，安装动作不替
    用户决定该往哪儿放。
    """
    return Path.home() / ".agents" / "skills" / "dhole-web"


def _cmd_skill(argv: list[str], target=None) -> int:
    """`dhole skill [status|install [--force] [--target DIR]]` —— 安装 / 检查随包发布的 agent 技能。

    技能装在包内（dhole_mcp/skills/dhole-web/），教 agent 怎么驱动 dhole：调用
    模式、响应字段词典、故障排查、配置、配方。安装就是把它复制到
    `~/.agents/skills/dhole-web`（见 `_default_skill_target`）；宿主不扫那个目录时
    用 `--target` 指定它自己的根。CLI 是便利入口，不是第二个真源 —— 随包的那份才是。
    """
    from dhole_mcp import cli_ui as ui
    import shutil

    bundled = Path(__file__).resolve().parent / "skills" / "dhole-web"

    if _wants_help(argv):
        print(cli_spec.command_help("skill"))
        return 0
    args = [a for a in argv if a and a.strip()]
    json_out = "--json" in args
    pretty = "--pretty" in args
    force = any(a.lower() in ("--force", "-f") for a in args)
    explicit = target
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--target", "-t") and i + 1 < len(args):
            explicit = args[i + 1]
            i += 2
            continue
        if a.startswith("--target="):
            explicit = a.split("=", 1)[1]
        elif not a.startswith("-"):
            positional.append(a)
        i += 1
    target = Path(explicit) if explicit is not None else _default_skill_target()

    action = positional[0].lower() if positional else "status"
    if action not in ("status", "install"):
        message = f"unknown subcommand: {action} (try: dhole skill status|install [--force])"
        if json_out:
            return _json_error(message)
        print(ui.err(message))
        return 2
    if not bundled.is_dir():
        message = f"bundled skill not found at {bundled} (reinstall dhole-mcp)"
        if json_out:
            return _json_error(message, 1)
        print(ui.err(message))
        return 1
    bundled_files = _skill_manifest(bundled)

    if action == "status":
        if not target.is_dir():
            state, changed = "missing", []
        else:
            installed = _skill_manifest(target)
            if installed == bundled_files:
                state, changed = "current", []
            else:
                state = "differs"
                changed = [f for f in sorted(set(installed) | set(bundled_files))
                           if installed.get(f) != bundled_files.get(f)]
        if json_out:
            _json_emit({
                "bundled": str(bundled),
                "target": str(target),
                "bundled_files": len(bundled_files),
                "state": state,
                "changed": changed,
            }, pretty=pretty)
            return 0
        print("  " + ui.dim("bundled") + "  " + ui.cmd(str(bundled))
              + "  " + ui.dim(f"({len(bundled_files)} files)"))
        if state == "missing":
            print("  " + ui.dim("installed") + "  " + ui.dim("not installed") + "  " + ui.dim(str(target)))
            print("  " + ui.dim("install with") + "  " + ui.cmd("dhole skill install"))
        elif state == "current":
            print("  " + ui.ok("installed & up to date") + "  " + ui.dim(str(target)))
        else:
            print("  " + ui.warn("installed, differs from bundled") + "  " + ui.dim(str(target)))
            for f in changed[:8]:
                print("      " + ui.dim(f))
            if len(changed) > 8:
                print("      " + ui.dim(f"... and {len(changed) - 8} more"))
            print("  " + ui.dim("re-sync with") + "  " + ui.cmd("dhole skill install --force"))
        return 0

    if target.exists() and not target.is_dir():
        message = f"{target} exists and is not a directory; remove it first"
        if json_out:
            return _json_error(message)
        print(ui.err(message))
        return 2
    if target.is_dir():
        installed = _skill_manifest(target)
        if installed == bundled_files:
            if json_out:
                _json_emit({"target": str(target), "installed": False,
                            "up_to_date": True, "files": len(bundled_files)},
                           pretty=pretty)
                return 0
            print(ui.ok("already up to date") + "  " + ui.dim(str(target)))
            return 0
        if installed and not force:
            changed = [f for f in sorted(set(installed) | set(bundled_files))
                       if installed.get(f) != bundled_files.get(f)]
            if json_out:
                _json_emit({
                    "target": str(target),
                    "installed": False,
                    "up_to_date": False,
                    "changed": changed,
                    "error": "installed skill differs from the bundled one; "
                             "re-run with --force",
                }, pretty=pretty)
                return 2
            print(ui.err("installed skill differs from the bundled one:") + "  " + ui.dim(str(target)))
            for f in changed:
                print("  " + ui.dim(f))
            print("  " + ui.dim("re-install over it with") + "  " + ui.cmd("dhole skill install --force"))
            return 2
        shutil.rmtree(target)
    shutil.copytree(bundled, target)
    if json_out:
        _json_emit({"target": str(target), "installed": True,
                    "up_to_date": True, "files": len(bundled_files),
                    "restart_required": True}, pretty=pretty)
        return 0
    print(ui.branded(ui.ok("skill installed"), ui.dim(str(target))))
    print("  " + ui.dim(f"{len(bundled_files)} files · skills load at session start, so restart your agent to pick it up"))
    if explicit is None:
        print("  " + ui.dim("a host that does not scan ~/.agents/skills: point --target at its own root"))
    return 0


def _cmd_engines(argv: list[str]) -> int:
    """`dhole engines [list|reset|probe]` —— 查看 / 清除 / 实测引擎池。

    最近哪些引擎被跳过、为什么。两个事实住在 dhole home 的两个文件里，而在此之前
    读它们意味着手工打开 JSON，清除它们意味着删文件加重启进程。`probe` 是那两个
    文件说不出的第三件事：某家引擎**此刻**答不答（list 只记得别人上一轮跑的结果）。
    """
    from dhole_mcp import cli_ui as ui
    from dhole_mcp.updater import _engine_cooldowns, _engine_yield_row

    if _wants_help(argv):
        print(cli_spec.command_help("engines"))
        return 0
    raw = [a for a in argv if a and a.strip()]
    json_out = "--json" in raw
    pretty = "--pretty" in raw
    args = [a for a in raw if a not in ("--json", "--pretty")]
    action = (args[0].lower() if args else "list")
    rest = args[1:]
    if action in ("list", "ls", "status", ""):
        cooldowns = _engine_cooldowns()
        row = _engine_yield_row()
        # G6：这些超时值本身就是"这台引擎为什么不见了"的一部分。
        # 17.0 写死 —— 说出现在生效的值，而不是让人去读源码。
        from dhole_mcp.updater import _cooldown_config
        cfg = _cooldown_config()
        if json_out:
            _json_emit({
                "cooldowns": cooldowns,
                "yield": ({"label": row[0], "text": row[1], "ok": bool(row[2])}
                          if row else None),
                "backoff": cfg["tiers"],
                "heartbeat": cfg["heartbeat"],
            }, pretty=pretty)
            return 0
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
            message = f"cannot load the search layer to reset it: {str(e)[:160]}"
            if json_out:
                return _json_error(message, 1)
            print(ui.err(message), file=sys.stderr)
            return 1
        info = engine_state_reset()
        cool = info.get("released_cooldowns") or {}
        if json_out:
            _json_emit({
                "engines_forgotten": info.get("engines_forgotten", 0),
                "released_cooldowns": cool,
                "cleared": ["circuit_breaker.json", "engine_stats.json"],
            }, pretty=pretty)
            return 0
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
        return _engines_probe(rest, json_out=json_out, pretty=pretty)
    message = f"unknown subcommand: {action} (try: dhole engines list|reset|probe)"
    return _json_error(message) if json_out else _bad_usage(
        message, cli_spec.usage("engines"))


_PROBE_QUERY_DEFAULT = "python asyncio tutorial"
"""默认探针查询：中英混排站点都有的通用技术词。换查询是为了让"这家能不能用"不被
缓存和一个恰好无结果的查询干扰 —— 所以它必须能改。"""


def _engines_probe(args: list[str], json_out: bool = False,
                   pretty: bool = False) -> int:
    """`dhole engines probe` —— 每台引擎打一次实时查询，并说明各自做了什么。

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
            message = (f"unknown flag: {a} (try: dhole engines probe [--all] "
                       f"[--query=...] [engine ...])")
            return _json_error(message) if json_out else _bad_usage(
                message, cli_spec.usage("engines"))
        else:
            names.append(a.lower())

    from dhole_mcp.search_engines import DEFAULT_ENGINES, _cooldowns, multi_search
    try:
        from dhole_mcp.search_metasearch import _DHOLE_TO_BACKEND, _TEXT_ENGINES
    except Exception as e:
        message = f"cannot load the search layer to probe it: {str(e)[:160]}"
        if json_out:
            return _json_error(message, 1)
        print(ui.err(message), file=sys.stderr)
        return 1

    if everything:
        names = [n for n in _DHOLE_TO_BACKEND if n in _TEXT_ENGINES]
    elif not names:
        names = list(DEFAULT_ENGINES)
    unknown = [n for n in names if n not in _DHOLE_TO_BACKEND]
    if unknown:
        message = (f"unknown engine(s): {', '.join(sorted(unknown))} "
                   f"(list them with: dhole engines probe --all)")
        return _json_error(message) if json_out else _bad_usage(
            message, cli_spec.usage("engines"))

    async def _run() -> int:
        rows: list[dict] = []
        if not json_out:
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
                rows.append({"engine": name, "status": "cooling", "ok": False,
                             "cooldown_s": int(left)})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.dim(
                        f"cooling         -       -  {int(left)}s left (not asked; "
                        f"`dhole engines reset` to force)"))
                continue
            t0 = time.perf_counter()
            try:
                results, reports = await asyncio.wait_for(
                    multi_search(query, 6, engines=[name]), timeout=30)
            except asyncio.TimeoutError:
                rows.append({"engine": name, "status": "timeout", "ok": False,
                             "elapsed_s": 30.0})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.err("timeout      -     >30s"))
                continue
            except Exception as e:
                rows.append({"engine": name, "status": "error", "ok": False,
                             "detail": str(e)[:120]})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.err(
                        f"error        -    {str(e)[:60]}"))
                continue
            elapsed = (time.perf_counter() - t0)  # 秒
            rep = reports[0] if reports else None
            status = getattr(rep, "status", "") or "?"
            nodes = getattr(rep, "item_nodes", -1)
            got = getattr(rep, "usable", -1)
            verdict = getattr(rep, "yield_verdict", "") or ""
            err = (getattr(rep, "error", "") or "").strip()
            if rep is not None and rep.ok:
                usable += 1
                rows.append({"engine": name, "status": "ok", "ok": True,
                             "hits": len(results), "elapsed_s": round(elapsed, 3),
                             "parsed": got, "nodes": nodes})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.ok("ok") + f"    "
                          f"{str(len(results)) + ' hits':>9}  {elapsed:6.1f}s  "
                          + ui.dim(f"parsed {got}/{nodes} items"))
            elif status == "circuit_open":
                rows.append({"engine": name, "status": "cooling", "ok": False,
                             "detail": err or "on cooldown"})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.dim(
                        f"cooling        -       -  {err[:48] or 'on cooldown'}"))
            elif rep is not None and rep.preempted:
                rows.append({"engine": name, "status": "not_asked", "ok": False,
                             "elapsed_s": round(elapsed, 3)})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.dim("not asked      -       -"))
            elif status == "empty":
                # error 里带的是原因（"no parsable results (HTTP 302, redirect not
                # followed)" 之类），比再报一次"没结果"有用得多。
                detail = err or "no results"
                if nodes > 0:
                    detail += f" - {nodes} item nodes survived, usable={got} ({verdict})"
                rows.append({"engine": name, "status": "empty", "ok": False,
                             "elapsed_s": round(elapsed, 3), "parsed": got,
                             "nodes": nodes, "detail": detail})
                if not json_out:
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
                rows.append({"engine": name, "status": status, "ok": False,
                             "elapsed_s": round(elapsed, 3), "detail": detail})
                if not json_out:
                    print(f"    {name.ljust(14)} " + ui.err(
                        f"{status.ljust(26)}  -  {elapsed:5.1f}s  {detail[:52]}"))
        if json_out:
            _json_emit({"query": query, "engines": rows, "asked": len(names),
                        "usable": usable}, pretty=pretty)
        else:
            print("  " + (ui.ok(f"{usable} of {len(names)} engine(s) usable right now")
                          if usable else ui.err(f"no engine answered {query!r}"))
                  + ui.dim("  (cooldowns are honored here; `dhole engines reset` clears them)"))
        return 0 if usable else 1

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        print(ui.err("\nprobe interrupted"))
        return 130


_CALL_USAGE = cli_spec.usage("call")


def _cmd_call(argv: list[str]) -> int:
    """`dhole call TOOL` —— 任何 MCP 工具的通用入口。

    这是参数面的逃生口，不是第二条产品路径：命名旗标仍然优先，`call` 只保证
    "今天没有旗标的那颗参数明天也不会够不着"。默认输出 JSON，因为这一面没有
    命令专属渲染器；`--text` 交内容通道，元数据走 stderr。
    """
    from dhole_mcp import cli_render as render
    from dhole_mcp import cli_ui as ui

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, _CALL_USAGE, json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("call"))
        return 0
    ui.utf8_stdio()
    positional, opts, err = _cli_parse(argv, cli_spec.parse_spec("call"))
    json_out = bool(opts.get("json"))
    if err:
        return _bad_usage(err, _CALL_USAGE, json_out=json_out)
    if opts.get("json") and opts.get("ndjson"):
        return _bad_usage("--json and --ndjson are different shapes; pick one",
                          _CALL_USAGE, json_out=json_out)
    if opts.get("pretty") and opts.get("ndjson"):
        return _bad_usage("--pretty needs --json (NDJSON is one object per line)",
                          _CALL_USAGE, json_out=json_out)
    if opts.get("text") and (opts.get("json") or opts.get("ndjson")
                             or opts.get("pretty") or opts.get("dry_run")):
        return _bad_usage("--text prints the content channel; drop --json/--ndjson",
                          _CALL_USAGE, json_out=json_out)
    if opts.get("quiet") and opts.get("verbose"):
        return _bad_usage("--quiet and --verbose contradict each other",
                          _CALL_USAGE, json_out=json_out)
    tools = [a for a in positional if a.strip()]
    if len(tools) != 1:
        return _bad_usage("call takes one tool name (see: dhole tools)",
                          _CALL_USAGE, json_out=json_out)
    tool = tools[0]
    if tool not in cli_spec.TOOL_NAMES:
        near = difflib.get_close_matches(tool, cli_spec.TOOL_NAMES, n=1, cutoff=0.4)
        hint = f" (did you mean {near[0]}?)" if near else ""
        return _bad_usage(f"unknown tool: {tool}{hint} - list them with `dhole tools`",
                          _CALL_USAGE, json_out=json_out)

    args: dict = {}
    if "args_json" in opts:
        try:
            value = _json_source(opts["args_json"], "--args")
        except ValueError as exc:
            return _bad_usage(str(exc), _CALL_USAGE, json_out=json_out)
        if not isinstance(value, dict):
            return _bad_usage("--args needs a JSON object", _CALL_USAGE,
                              json_out=json_out)
        args.update(value)
    extra = opts.get("args") or {}
    dupes = sorted(set(args) & set(extra))
    if dupes:
        return _bad_usage(f"--arg {dupes[0]}= duplicates --args",
                          _CALL_USAGE, json_out=json_out)
    args.update(extra)

    if opts.get("dry_run"):
        return _dry_run(tool, args, opts)

    with _progress(opts, tool):
        payload = _run_tool_or_refuse(tool, args, json_out=json_out)
    if payload is None:
        return 2
    if opts.get("ndjson"):
        for row in render.ndjson_rows("call", payload):
            print(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        return render.exit_code("call", payload)
    if opts.get("text"):
        text_opts = {**opts, "json": False, "ndjson": False}
        return _emit("call", payload, text_opts, render.call)
    print(json.dumps(payload, ensure_ascii=False,
                     indent=2 if opts.get("pretty") else None,
                     separators=None if opts.get("pretty") else (",", ":")))
    return render.exit_code("call", payload)


def _cmd_tools(argv: list[str]) -> int:
    """`dhole tools [--json]` —— 活工具定义的名册与参数面。"""
    from dhole_mcp import cli_ui as ui

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, cli_spec.usage("tools"),
                          json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("tools"))
        return 0
    unknown = [a for a in argv if a not in ("--json", "--pretty")]
    if unknown:
        message = f"unknown flag: {unknown[0]}"
        if "--json" in argv:
            return _json_error(message)
        return _bad_usage(message, cli_spec.usage("tools"))
    ui.utf8_stdio()
    rows = []
    for tool_def in MasterFetchServer._TOOL_DEFS:
        schema = tool_def.get("inputSchema") or {}
        rows.append({
            "name": tool_def.get("name", ""),
            "description": tool_def.get("description", ""),
            "required": list(schema.get("required") or []),
            "params": sorted((schema.get("properties") or {}).keys()),
        })
    if "--json" in argv:
        print(json.dumps(rows, ensure_ascii=False,
                         indent=2 if "--pretty" in argv else None,
                         separators=None if "--pretty" in argv else (",", ":")))
        return 0
    for row in rows:
        first_line = (row["description"] or "").splitlines()[0] if row["description"] else ""
        print("  " + ui.cmd(row["name"]) + "  " + ui.dim(first_line[:100]))
        print("      " + ui.dim("params: " + ", ".join(row["params"])))
    print("  " + ui.dim("call one with: dhole call <tool> --arg name=value"))
    return 0


def _cmd_version(argv: list[str]) -> int:
    """`dhole version [--json]` —— 版本面板 + 能力清单。"""
    from dhole_mcp import updater

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, cli_spec.usage("version"),
                          json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("version"))
        return 0
    unknown = [a for a in argv if a not in ("--json", "--pretty")]
    if unknown:
        message = f"unknown flag: {unknown[0]}"
        if "--json" in argv:
            return _json_error(message)
        return _bad_usage(message, cli_spec.usage("version"))
    if "--json" not in argv:
        updater.print_version()
        return 0
    data = {
        "version": updater.installed_version(),
        "capabilities": [
            {"label": label, "state": state, "ok": bool(ok)}
            for label, state, ok in updater.capabilities()
        ],
    }
    print(json.dumps(data, ensure_ascii=False,
                     indent=2 if "--pretty" in argv else None,
                     separators=None if "--pretty" in argv else (",", ":")))
    return 0


def _cmd_doctor(argv: list[str]) -> int:
    """`dhole doctor [--json]` —— 安装体检；JSON 模式交同一份检查行。"""
    from dhole_mcp import updater

    argv, color_err = _extract_color_flags(argv)
    if color_err:
        return _bad_usage(color_err, cli_spec.usage("doctor"),
                          json_out="--json" in argv)
    if _wants_help(argv):
        print(cli_spec.command_help("doctor"))
        return 0
    unknown = [a for a in argv if a not in ("--json", "--pretty")]
    if unknown:
        message = f"unknown flag: {unknown[0]}"
        if "--json" in argv:
            return _json_error(message)
        return _bad_usage(message, cli_spec.usage("doctor"))
    return updater.doctor(json_out="--json" in argv, pretty="--pretty" in argv)


def main(argv: list[str] | None = None):
    """dhole CLI 的入口。"""
    from dhole_mcp import cli_ui as ui
    from dhole_mcp import updater
    import argparse

    args_in = list(sys.argv[1:] if argv is None else argv)
    ui.utf8_stdio()
    args_in, color_err = _extract_color_flags(args_in)
    if color_err:
        print(ui.err(color_err), file=sys.stderr)
        raise SystemExit(2)
    args_in = cli_spec.reorder_globals(args_in)

    # 子命令在 argparse **之前**分发：一个裸位置参数会与默认服务的行径冲突
    # （无参数 = 启动 MCP 服务器）。这张表保持字面量：tests/test_gate_enablement
    # 用 AST 读它来确认 init / model / proxy 没有偷偷回来。`models fetch` 只下
    # 模型字节，不写能力闸、不选模型（选择仍是 DHOLE_RERANK_MODEL）。
    _SUBCOMMANDS = ("read", "search", "crawl", "discover", "research", "recall",
                    "request", "act", "doc", "feed", "state", "creds", "guide",
                    "call", "tools", "version", "help", "commands", "completion",
                    "completions", "serve", "engines", "skill", "doctor", "config",
                    "watch", "wire", "models")
    _HANDLERS = {
        "read": _cmd_read, "search": _cmd_search, "crawl": _cmd_crawl,
        "discover": _cmd_discover, "research": _cmd_research,
        "recall": _cmd_recall, "request": _cmd_request, "act": _cmd_act,
        "doc": _cmd_doc, "feed": _cmd_feed, "state": _cmd_state,
        "creds": _cmd_creds, "guide": _cmd_guide, "call": _cmd_call,
        "tools": _cmd_tools, "version": _cmd_version, "doctor": _cmd_doctor,
        "help": lambda rest: cli_spec.print_help(rest),
        "commands": lambda rest: cli_spec.print_commands(rest),
        "completion": lambda rest: cli_spec.print_completion(rest),
        "completions": lambda rest: cli_spec.print_completion(rest),
        "engines": _cmd_engines, "skill": _cmd_skill, "config": _cmd_config,
        "watch": _cmd_watch, "wire": _cmd_wire, "models": _cmd_models,
    }
    if args_in:
        first = args_in[0].lower()
        rest = args_in[1:]
        if first == "serve":
            # `dhole serve` 是默认行径的显式拼法：把尾巴交回 argparse。
            args_in = rest
            first = ""
        handler = _HANDLERS.get(first)
        if handler is not None:
            raise SystemExit(handler(rest))
        # 既不是 flag 也不是已知子命令的东西都是笔误。旧行为（argparse 的
        # "error: unrecognized arguments"）不给任何建议；"did you mean: dhole
        # doctor?" 才能把一个卡住的首次用户变回能用的用户。
        if first and not first.startswith("-"):
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
    parser.add_argument("--json", action="store_true",
                        help="with -v / --doctor: machine-readable JSON")
    parser.add_argument("--pretty", action="store_true",
                        help="indent --json output")
    args = parser.parse_args(args_in)

    if args.version:
        if args.json:
            extra = ["--json"] + (["--pretty"] if args.pretty else [])
            raise SystemExit(_cmd_version(extra))
        updater.print_version()
        return
    if args.doctor:
        extra = (["--json"] if args.json else []) + (["--pretty"] if args.pretty else [])
        raise SystemExit(_cmd_doctor(extra))

    # HTTP 模式：stdout 是空闲的（不是 MCP stdio 管道），所以一行横幅是安全的。
    # uvicorn 随后会打自己的 URL 行。Stdio 模式保持沉默 —— 任何 stdout/stderr
    # 噪声都会破坏 MCP 协议，或被读成崩溃。
    if args.http:
        print(ui.branded(ui.cyan("serving HTTP"), ui.dim(f"http://{args.host}:{args.port}/mcp")))

    srv = MasterFetchServer(cache_ttl=args.cache_ttl)
    srv.serve(http=args.http, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
