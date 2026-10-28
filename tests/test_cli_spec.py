"""CLI 规格层：帮助、补全、参数奇偶、输出形态与轻量入口。

这些用例刻意不碰网络：注册表是数据，输出层收假回执。真抓取仍由
`tests/e2e_cli_test.py` 负责。
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import subprocess
import sys

import pytest

from dhole_mcp import cli_spec, cli_ui, ops, updater
from dhole_mcp.server import MasterFetchServer

pytestmark = pytest.mark.smoke


@pytest.fixture(autouse=True)
def _color_back_to_auto():
    cli_ui.configure_color("auto")
    yield
    cli_ui.configure_color("auto")


def _read(**overrides):
    payload = {
        "url": "https://a.test",
        "status": 200,
        "content": ["hello"],
        "content_ok": True,
        "fetcher_used": "http",
        "duration_ms": 5,
    }
    payload.update(overrides)
    return payload


def _bulk(*urls):
    return {"results": [_read(url=url, content=[f"page {url}"]) for url in urls]}


def _capture(monkeypatch, payload):
    seen: dict = {}

    def fake(name, args):
        seen["tool"] = name
        seen["args"] = dict(args)
        return payload

    monkeypatch.setattr(ops, "_run_tool", fake)
    return seen


# ── 注册表本身：工具名与参数面 ─────────────────────────────────────

def test_tool_names_match_the_live_definitions():
    assert set(cli_spec.TOOL_NAMES) == {td["name"] for td in MasterFetchServer._TOOL_DEFS}


def test_every_tool_parameter_has_a_named_flag_or_positional():
    """工具 schema 里每个参数都必须有一条命令面路径；漏一颗就红。"""
    for cmd in cli_spec.CONTENT_COMMANDS.values():
        tool_def = next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == cmd.tool)
        params = set((tool_def.get("inputSchema") or {}).get("properties") or {})
        covered = {flag.tool_arg for flag in cmd.flags
                   if flag.transform != "urls_file"}
        covered |= {p.target for p in cmd.positionals if p.target}
        if cmd.positional_mode == "urls":
            covered |= set(cmd.url_keys)
        if cmd.positional_mode == "query":
            covered.add("query")
        if cmd.positional_mode == "file":
            covered.add("file_path")
        if cmd.positional_mode == "one_url":
            covered.add("url")
        covered |= cmd.covered_params
        assert params <= covered, (cmd.name, sorted(params - covered))


#: 只活在 opts 里的输出旗标 —— 它们由 `_emit` / `_finish` 消费，不是工具参数。
_OUTPUT_KEYS = frozenset({"json", "ndjson", "pretty", "verbose", "quiet",
                          "progress", "args", "dry_run"})


def test_every_cli_target_is_a_tool_parameter():
    """反方向的账：CLI 落点必须真的存在。

    上一颗测试只保证"工具的每个参数都有命令行落点"。`--similar-to` 的
    `target='url'`、`--password-file` 的 `target='password'` 打错一个字母，
    那条账仍然全绿，而命令会在运行时被工具拒成用法错误。同一条账也钉住
    `positional_mode='urls'` 折叠出的键 —— `feed_read` 只收 urls，单条 URL 被
    折成 url 时，帮助里的示例必然退出 2。
    """
    for cmd in cli_spec.CONTENT_COMMANDS.values():
        tool_def = next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == cmd.tool)
        params = set((tool_def.get("inputSchema") or {}).get("properties") or {})
        for flag in cmd.flags:
            if flag.key in _OUTPUT_KEYS or flag.transform == "urls_file":
                continue
            assert flag.tool_arg in params, (cmd.name, flag.name, flag.tool_arg)
        for pos in cmd.positionals:
            if pos.target:
                assert pos.target in params, (cmd.name, pos.name, pos.target)
        if cmd.positional_mode == "urls":
            single_key, multi_key = cmd.url_keys
            assert single_key in params, (cmd.name, single_key)
            assert multi_key in params, (cmd.name, multi_key)


#: 每条内容命令的最小可跑 argv —— 给"绑定结果能不能被工具接受"这条账用。
_MINIMAL_ARGV = {
    "read": ["https://a.test"],
    "search": ["q"],
    "crawl": ["https://a.test"],
    "discover": ["https://a.test"],
    "research": ["q"],
    "recall": ["q"],
    "request": ["https://a.test", "--method", "HEAD"],
    "act": ["https://a.test", "--actions", "[]"],
    "doc": ["a.md"],
    "feed": ["https://a.test/feed"],
}


def test_every_command_binds_to_arguments_the_tool_accepts():
    """绑定结果要过服务端那道闸，不靠我们另写一份"哪些键合法"。

    `feed_read` 只收 urls，而命令层曾把单条 URL 折成 `url` —— 静态对账看不出
    形状，这条用的是真校验器（`_reject_unknown_args`），不发网络请求。
    """
    from dhole_mcp.server import _reject_unknown_args

    plain = {name for name, cmd in cli_spec.CONTENT_COMMANDS.items() if not cmd.custom}
    assert set(_MINIMAL_ARGV) == plain
    for name, argv in _MINIMAL_ARGV.items():
        cmd = cli_spec.CONTENT_COMMANDS[name]
        positionals, opts, err = ops._cli_parse(argv, cli_spec.parse_spec(name))
        assert not err, (name, err)
        args, err = ops._positional_args(name, positionals, opts)
        assert not err, (name, err)
        assert not ops._bind_flags(name, opts, args), name
        # 未知键在这一行抛 ArgumentRefusal —— 正是 feed 单 URL 当年的形状。
        _reject_unknown_args(cmd.tool, args)


def test_enum_choices_match_the_server_side_tables():
    """CLI 的封闭值集是服务端那张表的一份副本 —— 副本必须相等。

    `params._ENUM_ARGS` 自己有一对相等断言钉着工具签名；这里再钉一层，防止
    CLI 放行一个服务端拒绝的值（`--sort date` 曾经就是这种：帮助写着 date，
    服务端只认 newest）。
    """
    from dhole_mcp import params, search

    for name, cmd in cli_spec.CONTENT_COMMANDS.items():
        for flag in cmd.flags:
            if not flag.choices:
                continue
            expected = params._ENUM_ARGS.get(cmd.tool, {}).get(flag.tool_arg)
            if expected is None:
                continue
            assert set(flag.choices) == set(expected), (name, flag.name)

    typed = {f.tool_arg: f.choices for f in cli_spec.CONTENT_COMMANDS["read"].flags}
    assert set(typed["extraction_type"]) == set(params._READ_EXTRACTION_TYPES)
    assert set(typed["focus_mode"]) == set(params._FOCUS_MODES)
    assert set(typed["focus_engine"]) == set(params._FOCUS_ENGINES)
    assert set(typed["force_fetcher"]) == set(params._FORCE_FETCHER_VALUES)

    crawl = {f.tool_arg: f.choices for f in cli_spec.CONTENT_COMMANDS["crawl"].flags}
    assert set(crawl["force_fetcher"]) == set(params._FORCE_FETCHER_VALUES)

    search_flags = {f.tool_arg: f.choices
                    for f in cli_spec.CONTENT_COMMANDS["search"].flags}
    assert set(search_flags["mode"]) == set(search._IMPLEMENTED_MODES)
    assert set(search_flags["sort"]) == {"relevance", "newest"}
    for window in search_flags["freshness"]:
        assert search._validate_freshness(window) == window


def test_the_subcommand_literal_matches_the_registry():
    """ops.main 里的字面量是给 AST 守卫读的；它必须与注册表同步。"""
    tree = ast.parse(pathlib.Path(ops.__file__).read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_SUBCOMMANDS"
                        for t in node.targets)
                and isinstance(node.value, (ast.Tuple, ast.List))):
            found = {e.value for e in node.value.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            break
    assert found is not None
    assert found == set(cli_spec.SUBCOMMANDS) | {"completions"}


def test_global_flags_move_in_front_of_the_command():
    assert cli_spec.reorder_globals(["--json", "read", "u"]) == ["read", "--json", "u"]
    assert cli_spec.reorder_globals(
        ["--color", "always", "--verbose", "read", "u"]
    ) == ["read", "--color", "always", "--verbose", "u"]
    assert cli_spec.reorder_globals(["-u"]) == ["-u"]
    assert cli_spec.reorder_globals(["--http"]) == ["--http"]


# ── 帮助与目录 ────────────────────────────────────────────────────

@pytest.mark.parametrize("name", list(cli_spec.CONTENT_COMMANDS))
def test_command_help_names_every_flag_and_example(name):
    text = cli_spec.command_help(name)
    assert cli_spec.usage(name) in text
    for flag in cli_spec.CONTENT_COMMANDS[name].flags:
        assert flag.name in text, (name, flag.name)
    assert "examples:" in text
    assert "exit codes:" in text


def test_top_help_lists_every_command():
    text = cli_spec.top_help()
    for name in cli_spec.ALL_COMMANDS:
        assert f"dhole {name}" in text, name


def test_catalog_is_machine_readable():
    data = cli_spec.catalog()
    assert data["version"]
    assert data["catalog_version"] == 2
    assert len(data["commands"]) == len(cli_spec.CONTENT_COMMANDS)
    assert {c["name"] for c in data["commands"]} == set(cli_spec.CONTENT_COMMANDS)
    assert set(data["tool_names"]) == set(cli_spec.TOOL_NAMES)
    read = next(c for c in data["commands"] if c["name"] == "read")
    assert any(f["name"] == "--nested-selector" for f in read["flags"])
    # agent 拿这份目录要能直接拼出合法调用：值集、必填项、互斥组都得在。
    extraction = next(f for f in read["flags"] if f["name"] == "--extraction-type")
    assert extraction["choices"] == ["markdown", "html", "text", "article",
                                    "structured", "check"]
    assert extraction["metavar"] == "TYPE"
    request = next(c for c in data["commands"] if c["name"] == "request")
    assert request["required_args"] == ["method"]
    assert request["mutually_exclusive"] == [["body", "body_file"]]
    feed = next(c for c in data["commands"] if c["name"] == "feed")
    assert feed["positional_mode"] == "urls"
    config = next(c for c in data["ops"] if c["name"] == "config")
    assert {s["name"] for s in config["subcommands"]} == {"list", "get", "set",
                                                          "unset"}


def test_declared_subcommands_cover_every_branch_the_usage_line_promises():
    """`usage` 与 `subcommands` 是同一件事的两种写法，不许各说一套。

    这条账存在的原因：目录里 `engines` 只写了 `list`，而它自己的 usage 写着
    `list | probe | reset`，两条都真能用 —— agent 按目录拼调用，永远发现不了
    `engines reset` 与 `skill install`。声明与实际分成两套，是 `feed` 那个
    `url`/`urls` 缺陷的同一类。

    只审「每个分支都以一个裸小写词开头」的命令：`act <url>`、`call TOOL`、
    `creds [show]` 的首段是占位符或可省分支，不是子命令词，跳过。
    """
    import re

    checked = set()
    for group in (cli_spec.CONTENT_COMMANDS, cli_spec.OPS_COMMANDS,
                  cli_spec.META_COMMANDS):
        for cmd in group.values():
            if "|" not in cmd.usage:
                continue
            words: list[str] = []
            for segment in cmd.usage.split("|"):
                tokens = segment.strip().split()
                if not tokens:
                    break
                head = tokens[2] if tokens[:2] == ["dhole", cmd.name] else tokens[0]
                if not re.fullmatch(r"[a-z]+", head):
                    break
                words.append(head)
            else:
                checked.add(cmd.name)
                assert set(words) == {s[0] for s in cmd.subcommands}, (
                    cmd.name, words, [s[0] for s in cmd.subcommands])

    # 规则本身要落在真命令上，否则这是一条空账。
    assert {"engines", "skill", "wire", "config", "watch",
            "completion"} <= checked


def test_help_unknown_command_suggests_the_near_one(capsys):
    assert cli_spec.print_help(["raed"]) == 2
    assert "did you mean: dhole help read?" in capsys.readouterr().err


def test_unknown_flag_suggests_the_near_one():
    _, _, err = ops._cli_parse(["--max-char", "5"], cli_spec.parse_spec("read"))
    assert "did you mean --max-chars?" in err


# ── 解析细节 ──────────────────────────────────────────────────────

def test_boolean_flags_accept_explicit_false_and_no_prefix():
    _, opts, err = ops._cli_parse(
        ["--include-links=false", "--no-include-media"],
        cli_spec.parse_spec("read"))
    assert err == ""
    assert opts["include_links"] is False
    assert opts["include_media"] is False


def test_color_flags_are_stripped_and_configure_the_process():
    cleaned, err = cli_ui.extract_color_flags(["--color", "always", "read", "u"])
    assert err == "" and cleaned == ["read", "u"]
    assert cli_ui._color_ok() is True


# ── 缺参数补齐与输入源 ────────────────────────────────────────────

def test_read_forwards_the_nested_family(monkeypatch):
    seen = _capture(monkeypatch, _read())
    assert ops._cmd_read([
        "https://a.test", "--nested-selector", ".item",
        "--nested-fields", '[{"name": "x", "selector": "a"}]',
        "--nested-max", "3",
    ]) == 0
    assert seen["args"]["nested_selector"] == ".item"
    assert seen["args"]["nested_fields"] == [{"name": "x", "selector": "a"}]
    assert seen["args"]["nested_max"] == 3


def test_search_forwards_fetch_schema_and_cache_ttl(monkeypatch):
    seen = _capture(monkeypatch, {
        "results": [], "engines_used": [], "duration_ms": 1, "error": "",
    })
    assert ops._cmd_search([
        "q", "--fetch-schema", '{"properties": {"x": ".x"}}',
        "--cache-ttl", "0",
    ]) == 0
    assert seen["args"]["fetch_schema"] == {"properties": {"x": ".x"}}
    assert seen["args"]["cache_ttl"] == 0


def test_arg_can_supply_a_missing_positional(monkeypatch):
    seen = _capture(monkeypatch, _read())
    assert ops._cmd_read(["--arg", "url=https://a.test",
                          "--arg", "nested_max=2"]) == 0
    assert seen["args"]["url"] == "https://a.test"
    assert seen["args"]["nested_max"] == 2

    crawl = _capture(monkeypatch, {"pages": [
        {"url": "https://a.test", "content": ["x"], "content_ok": True},
    ]})
    assert ops._cmd_crawl(["--arg", "url=https://a.test"]) == 0
    assert crawl["args"]["url"] == "https://a.test"

    feed = _capture(monkeypatch, {"feeds": [{"source_url": "https://f.test/rss",
                                             "items": []}]})
    assert ops._cmd_feed(["--arg", 'urls=["https://f.test/rss"]']) == 0
    assert feed["args"]["urls"] == ["https://f.test/rss"]


def test_urls_file_and_stdin_make_a_batch(monkeypatch, tmp_path):
    seen = _capture(monkeypatch, _bulk("https://a.test", "https://b.test"))
    urls = tmp_path / "urls.txt"
    urls.write_text("https://a.test\n\nhttps://b.test\n", encoding="utf-8")
    assert ops._cmd_read(["--urls-file", str(urls)]) == 0
    assert seen["args"]["urls"] == ["https://a.test", "https://b.test"]

    monkeypatch.setattr(sys, "stdin",
                        io.StringIO("https://a.test\nhttps://b.test\n"))
    assert ops._cmd_read(["-"]) == 0
    assert seen["args"]["urls"] == ["https://a.test", "https://b.test"]


def test_password_file_reads_the_file_not_the_literal_path(monkeypatch, tmp_path):
    seen = _capture(monkeypatch, _read())
    secret = tmp_path / "pw.txt"
    secret.write_text("s3cret\n", encoding="utf-8")
    assert ops._cmd_read(["https://a.test", "--password-file", str(secret)]) == 0
    assert seen["args"]["password"] == "s3cret"


def test_feed_sends_a_single_url_as_a_list(monkeypatch):
    """`feed_read` 只收 urls。单条 URL 也必须装进列表 —— 帮助里的第一条示例
    就是这个形状，折成 `url` 会让它稳定退出 2。"""
    seen = _capture(monkeypatch, {"feeds": [{"source_url": "u", "items": []}]})
    assert ops._cmd_feed(["https://a.test/feed"]) == 0
    assert seen["args"] == {"urls": ["https://a.test/feed"]}


def test_feed_urls_file_with_one_line_is_also_a_list(monkeypatch, tmp_path):
    seen = _capture(monkeypatch, {"feeds": [{"source_url": "u", "items": []}]})
    urls = tmp_path / "feeds.txt"
    urls.write_text("https://a.test/feed\n", encoding="utf-8")
    assert ops._cmd_feed(["--urls-file", str(urls)]) == 0
    assert seen["args"] == {"urls": ["https://a.test/feed"]}


def test_enum_typos_are_usage_errors_before_any_request(monkeypatch, capsys):
    """非法枚举是用法错误（2），不是"抓到了但不可用"（1）。

    以前它真的走到服务端，先付一次调用、再以 1 退出 —— 与 `--max-results abc`
    的口径相反。
    """
    called: list = []
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: called.append(name) or _read())
    assert ops._cmd_read(["https://a.test", "--focus-mode", "nonsense"]) == 2
    err = capsys.readouterr().err
    assert "filter, best" in err
    assert "nonsense" in err
    assert called == []


def test_enum_values_are_case_folded_not_rejected(monkeypatch):
    seen = _capture(monkeypatch, _read())
    assert ops._cmd_read(["https://a.test", "--focus-mode", "BEST"]) == 0
    assert seen["args"]["focus_mode"] == "best"


def test_dry_run_prints_the_bound_call_and_makes_no_request(monkeypatch, capsys):
    called: list = []
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: called.append(name) or _read())
    assert ops._cmd_read(["https://a.test", "--max-chars", "8000",
                          "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["tool"] == "web_read"
    assert payload["args"] == {"url": "https://a.test", "max_content_chars": 8000}
    assert called == []


def test_dry_run_is_available_on_call_and_skips_the_tool(monkeypatch, capsys):
    called: list = []
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: called.append(name) or {})
    assert ops._cmd_call(["web_read", "--arg", "url=https://a.test",
                          "--dry-run"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "web_read"
    assert payload["args"] == {"url": "https://a.test"}
    assert called == []


def test_dry_run_refuses_an_argument_the_tool_does_not_take(monkeypatch, capsys):
    """预演必须过真闸：它存在的理由就是"先看这次调用对不对"。

    此前的形状是 `--arg nope=1` 原样打印、rc=0 —— 唯一会让这次调用失败的键，
    恰好是预演看不见的那一个。
    """
    called: list = []
    monkeypatch.setattr(ops, "_run_tool",
                        lambda name, args: called.append(name) or _read())
    assert ops._cmd_read(["https://a.test", "--dry-run",
                          "--arg", "nope=1"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Unsupported argument(s) for web_read" in captured.err
    assert called == []


def test_dry_run_json_refusal_is_a_parseable_envelope(monkeypatch, capsys):
    monkeypatch.setattr(ops, "_run_tool", lambda name, args: _read())
    assert ops._cmd_call(["web_read", "--dry-run", "--json",
                          "--arg", "nope=1"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 2
    assert "Unsupported argument(s)" in payload["error"]


# ── 输出形态 ──────────────────────────────────────────────────────

def test_quiet_suppresses_metadata_but_keeps_content(monkeypatch, capsys):
    _capture(monkeypatch, _read())
    assert ops._cmd_read(["https://a.test", "--quiet"]) == 0
    captured = capsys.readouterr()
    assert "hello" in captured.out
    assert captured.err == ""


def test_quiet_still_prints_the_failure(monkeypatch, capsys):
    _capture(monkeypatch, _read(content=[], content_ok=False, error="boom"))
    assert ops._cmd_read(["https://a.test", "--quiet"]) == 1
    assert "error: boom" in capsys.readouterr().err


def test_json_usage_errors_are_parseable(monkeypatch, capsys):
    _capture(monkeypatch, _read())
    assert ops._cmd_read(["--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["exit_code"] == 2
    assert "URL" in payload["error"]


@pytest.mark.parametrize("flags", [
    ["--json", "--ndjson"],
    ["--pretty"],
    ["--quiet", "--verbose"],
])
def test_conflicting_output_flags_exit_two(monkeypatch, capsys, flags):
    _capture(monkeypatch, _read())
    assert ops._cmd_read(["https://a.test", *flags]) == 2
    captured = capsys.readouterr()
    assert captured.err or captured.out


def test_ndjson_is_one_object_per_page(monkeypatch, capsys):
    _capture(monkeypatch, _bulk("https://a.test", "https://b.test"))
    assert ops._cmd_read([
        "https://a.test", "https://b.test", "--ndjson",
    ]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["type"] for row in rows] == ["page", "page"]
    assert [row["url"] for row in rows] == ["https://a.test", "https://b.test"]


def test_ndjson_rows_for_search_crawl_feed_and_discover():
    from dhole_mcp import cli_render

    search = cli_render.ndjson_rows("search", {
        "results": [{"url": "https://a.test", "title": "A"}],
    })
    assert search == [{"url": "https://a.test", "title": "A", "type": "result"}]

    crawl = cli_render.ndjson_rows("crawl", {
        "pages": [{"url": "https://a.test", "content_ok": True}],
    })
    assert crawl[0]["type"] == "page"

    feed = cli_render.ndjson_rows("feed", {
        "feeds": [{"source_url": "https://f.test/rss",
                   "items": [{"url": "https://a.test", "title": "A"}]}],
    })
    assert feed == [{"url": "https://a.test", "title": "A",
                     "source_url": "https://f.test/rss", "type": "item"}]

    discover = cli_render.ndjson_rows("discover", {
        "pages": [{"url": "https://a.test"}],
    })
    assert discover == [{"url": "https://a.test", "type": "url"}]


# ── dhole call / tools / version / doctor ────────────────────────

def test_call_defaults_to_json_and_forwards_loose_args(monkeypatch, capsys):
    seen = _capture(monkeypatch, _read())
    assert ops._cmd_call([
        "web_read", "--arg", "url=https://a.test",
        "--arg", "max_content_chars=500",
    ]) == 0
    assert seen["tool"] == "web_read"
    assert seen["args"] == {"url": "https://a.test", "max_content_chars": 500}
    payload = json.loads(capsys.readouterr().out)
    assert payload["content_ok"] is True


def test_call_text_prints_the_content_channel(monkeypatch, capsys):
    _capture(monkeypatch, _read())
    assert ops._cmd_call(["web_read", "--arg", "url=https://a.test",
                          "--text"]) == 0
    assert "hello" in capsys.readouterr().out


def test_call_refuses_unknown_tools_and_duplicate_args(monkeypatch, capsys):
    _capture(monkeypatch, _read())
    assert ops._cmd_call(["not_a_tool"]) == 2
    assert "unknown tool" in capsys.readouterr().err

    assert ops._cmd_call([
        "web_read", "--args", '{"url": "https://a.test"}',
        "--arg", "url=https://b.test",
    ]) == 2
    assert "duplicates --args" in capsys.readouterr().err


def test_tools_json_matches_the_live_definitions(capsys):
    assert ops._cmd_tools(["--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert {row["name"] for row in rows} == {td["name"] for td in MasterFetchServer._TOOL_DEFS}
    read = next(row for row in rows if row["name"] == "web_read")
    assert "nested_selector" in read["params"]


def test_version_json_has_capabilities(capsys):
    assert ops._cmd_version(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == updater.installed_version()
    assert data["capabilities"]


def test_doctor_json_has_checks_and_capabilities(capsys):
    assert ops._cmd_doctor(["--json"]) in (0, 1)
    data = json.loads(capsys.readouterr().out)
    assert data["checks"]
    assert data["capabilities"]
    assert isinstance(data["ok"], bool)


# ── 运维命令 JSON ────────────────────────────────────────────────

def test_config_json_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
    assert ops._cmd_config(["list", "--json"]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing["registered_count"]

    assert ops._cmd_config([
        "set", "DHOLE_DEFAULT_CONTENT_CHARS", "8000", "--json",
    ]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["effect"] in ("next-call", "restart")

    assert ops._cmd_config([
        "get", "DHOLE_DEFAULT_CONTENT_CHARS", "--json",
    ]) == 0
    got = json.loads(capsys.readouterr().out)
    assert got["value"] == "8000"
    assert got["source"] == "file"

    assert ops._cmd_config([
        "unset", "DHOLE_DEFAULT_CONTENT_CHARS", "--json",
    ]) == 0
    removed = json.loads(capsys.readouterr().out)
    assert removed["removed"] is True


def test_engines_list_json(monkeypatch, capsys):
    monkeypatch.setattr(updater, "_engine_cooldowns", lambda: {"bing": 12})
    monkeypatch.setattr(updater, "_engine_yield_row",
                        lambda: ("engine yield", "bing: 3 usable", True))
    monkeypatch.setattr(updater, "_cooldown_config", lambda: {
        "tiers": {"block": 60.0, "challenge": 600.0, "connection": 600.0},
        "heartbeat": 300.0,
    })
    assert ops._cmd_engines(["list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["cooldowns"] == {"bing": 12}
    assert data["yield"]["ok"] is True
    assert data["backoff"]["block"] == 60.0


def test_engines_probe_json(monkeypatch, capsys):
    from dhole_mcp import search_engines, search_metasearch

    class Report:
        ok = True
        status = "ok"
        item_nodes = 3
        usable = 2
        yield_verdict = "healthy"
        error = ""
        preempted = False

    async def fake_multi(query, limit, engines=None):
        return [{"title": "x"}], [Report()]

    monkeypatch.setattr(search_engines, "DEFAULT_ENGINES", ["fake"])
    monkeypatch.setattr(search_engines, "_cooldowns", lambda: {})
    monkeypatch.setattr(search_engines, "multi_search", fake_multi)
    monkeypatch.setattr(search_metasearch, "_DHOLE_TO_BACKEND", {"fake": "fake"})
    monkeypatch.setattr(search_metasearch, "_TEXT_ENGINES", {"fake"})
    assert ops._cmd_engines(["probe", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["usable"] == 1
    assert data["engines"][0]["status"] == "ok"


def test_wire_ledger_and_lock_json(capsys):
    assert ops._cmd_wire(["ledger", "--json"]) == 0
    ledger = json.loads(capsys.readouterr().out)
    assert ledger["default"]["tools"] == len(cli_spec.TOOL_NAMES)
    assert len(ledger["rows"]) == len(cli_spec.TOOL_NAMES)

    assert ops._cmd_wire(["lock", "--json"]) in (0, 1)
    lock = json.loads(capsys.readouterr().out)
    assert "matches" in lock and "drift" in lock


def test_skill_json_round_trip(tmp_path, capsys):
    target = tmp_path / "skills" / "dhole-web"
    assert ops._cmd_skill(["status", "--json"], target=target) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "missing"

    assert ops._cmd_skill(["install", "--json"], target=target) == 0
    installed = json.loads(capsys.readouterr().out)
    assert installed["installed"] is True

    assert ops._cmd_skill(["status", "--json"], target=target) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "current"

    first = next(p for p in target.rglob("*") if p.is_file())
    first.write_text(first.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
    assert ops._cmd_skill(["status", "--json"], target=target) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "differs"

    assert ops._cmd_skill(["install", "--json"], target=target) == 2
    differs = json.loads(capsys.readouterr().out)
    assert differs["up_to_date"] is False and differs["changed"]


# ── 补全与轻量入口 ──────────────────────────────────────────────

@pytest.mark.parametrize("shell", cli_spec.SHELLS)
def test_completion_names_every_command(shell):
    text = cli_spec.completion(shell)
    for name in cli_spec.ALL_COMMANDS:
        assert name in text, (shell, name)
    assert ("nested-selector" if shell == "fish" else "--nested-selector") in text


def test_help_does_not_import_the_server():
    """`dhole --help` 是轻量路径：不 import 服务器，也不付它的启动税。"""
    code = (
        "import sys; sys.argv=['dhole','--help'];"
        "from dhole_mcp import cli; rc=cli.main();"
        "print('SERVER' if 'dhole_mcp.server' in sys.modules else 'LIGHT');"
        "print(rc)"
    )
    run = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    lines = run.stdout.strip().splitlines()
    assert lines[-2:] == ["LIGHT", "0"], run.stdout + run.stderr


def test_color_can_be_forced_and_disabled(capsys):
    cli_ui.configure_color("always")
    print(cli_ui.cyan("x"))
    assert "\033[" in capsys.readouterr().out
    cli_ui.configure_color("never")
    print(cli_ui.cyan("x"))
    assert "\033[" not in capsys.readouterr().out


def test_sources_choices_match_the_vertical_registry():
    """`--sources` 的封闭值集是 search_verticals 那张表的副本，必须相等。"""
    from dhole_mcp import search_verticals

    flags = {f.tool_arg: f.choices
             for f in cli_spec.CONTENT_COMMANDS["search"].flags}
    assert set(flags["sources"]) == set(search_verticals.SOURCE_KINDS)


def test_state_classes_match_the_server_tables():
    """state 的类别是本地的封闭值集，一张表都不能各说一套。"""
    flags = {f.key: f.choices for f in cli_spec.CONTENT_COMMANDS["state"].flags}
    assert set(flags["show"]) == set(MasterFetchServer._STATE_TOKENS)
    assert set(cli_spec.STATE_SHOW_ONLY) == set(MasterFetchServer._SHOW_ONLY)
    assert set(flags["clear"]) == (set(MasterFetchServer._STATE_TOKENS)
                                   - set(MasterFetchServer._SHOW_ONLY))


def test_research_and_recall_offer_ndjson():
    for name in ("research", "recall"):
        names = {f.name for f in cli_spec.CONTENT_COMMANDS[name].flags}
        assert "--ndjson" in names, name


def test_state_rejects_an_unknown_class_before_any_request(monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise AssertionError("no tool call should be made")

    monkeypatch.setattr(ops, "_finish", explode)
    assert ops._cmd_state(["--show", "bogus"]) == 2
    assert "must be one of" in capsys.readouterr().err
    assert ops._cmd_state(["--clear", "models"]) == 2
    assert "--clear must be one of" in capsys.readouterr().err


def test_models_fetch_matches_the_reranker_registry():
    """`dhole models fetch --model` 的封闭值集就是注册表本身，且不是新 MCP 工具。"""
    from dhole_mcp import reranker

    cmd = cli_spec.ALL_COMMANDS["models"]
    flags = {flag.key: flag for flag in cmd.flags}
    assert set(flags["model"].choices) == set(reranker.MODELS)
    assert [name for name, _help in cmd.subcommands] == ["fetch"]
    assert cmd.kind == "ops"
    assert "models" not in cli_spec.TOOL_NAMES, \
        "下载模型是 CLI 运维命令，不该长成第 14 颗 MCP 工具"
