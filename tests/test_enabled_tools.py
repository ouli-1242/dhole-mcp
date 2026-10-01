"""Connect-time token spend: DHOLE_TOOLS registers only a subset of the 13 tools.

Why this file exists: the tools/list table plus instructions is paid on EVERY
connect, even when dhole is never called. Measured after the token audit: 19,141
chars of tool schemas + 2,141 chars of instructions per conversation = 21,282
chars, which that tokenizer run counts as ~5.1k cl100k tokens (4.16 chars per
token for this payload; the repo's older 3.88 divisor read 5.5k and was high),
with smart_fetch alone at 6,508 chars.
``DHOLE_TOOLS=smart_fetch,smart_search`` keeps the daily drivers at roughly half
that cost; everything else stays one env-edit away.

The knob is an import-time constant (like DHOLE_BROWSER_IDLE_TIMEOUT and
DHOLE_DEFAULT_CONTENT_CHARS), so env-dependent behaviour is exercised in fresh
interpreters via subprocess — the same way a client's ``env`` entry applies it.
A misspelled name raises AT IMPORT: silently ignoring it would drop a
capability the operator believes is enabled, which is the same failure class
as an ignored unknown argument, one level up.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import dhole_mcp.server as server_mod


def _run_fresh(code: str, env_value: "str | None") -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter with DHOLE_TOOLS set to env_value.

    ``None`` means the variable is absent. The parent's DHOLE_TOOLS (if the
    operator exported one) is always scrubbed first, so the "unset" case is
    really unset.
    """
    env = {k: v for k, v in os.environ.items() if k != "DHOLE_TOOLS"}
    if env_value is not None:
        env["DHOLE_TOOLS"] = env_value
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=180)


_ENABLED_SNIPPET = (
    "import json, dhole_mcp.server as s;"
    "print(json.dumps({'enabled': sorted(s._ENABLED_TOOLS),"
    " 'all': sorted(s._TOP_LEVEL_ARGS)}))"
)


class TestTheEnvVarIsParsed:

    @pytest.mark.parametrize("value", ["smart_fetch", " smart_fetch ,smart_search ,"])
    def test_a_subset_registers_exactly_those_tools(self, value):
        out = _run_fresh(_ENABLED_SNIPPET, value)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert payload["enabled"] == sorted(
            n.strip() for n in value.split(",") if n.strip())
        assert len(payload["all"]) == 13, \
            "全集是 13 个工具（16.0 补上 close_session，L3/L4 补上 recipe/watch，" \
            "凭据/登录流补上 vault/login）；默认关闭的也留在这里，守卫继续覆盖它们"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_means_every_tool_except_the_opt_in_ones(self, value):
        """未设 DHOLE_TOOLS = 全集 **减去** 两个 opt-in 组。

        18.0 起 recipe / watch 是 opt-in：它们的 schema 合计约 3.6k 字符，
        而连接期预算是这个项目反复量过的东西。凭据 / 登录流（vault / login）
        是**第二个** opt-in 组，单独一个开关——它要用户装了密码管理器 CLI 才有
        意义。默认 wire 必须与它们落地之前逐字节相同。
        """
        out = _run_fresh(_ENABLED_SNIPPET, value)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        opt_in = {"recipe", "watch", "vault", "login"}
        assert set(payload["enabled"]) == set(payload["all"]) - opt_in
        assert opt_in.isdisjoint(payload["enabled"])

    def test_dhole_tasks_1_puts_the_task_opt_in_tools_back(self):
        """DHOLE_TASKS=1 只放回 recipe / watch —— 不碰凭据那组。

        两个开关两件事：打开「重复任务」不该顺手打开一个能读密码管理器的工具。
        """
        code = _ENABLED_SNIPPET
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_TASKS", "DHOLE_VAULT")}
        env["DHOLE_TASKS"] = "1"
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, env=env, timeout=180)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert set(payload["enabled"]) == set(payload["all"]) - {"vault", "login"}
        assert {"recipe", "watch"} <= set(payload["enabled"])

    def test_dhole_vault_1_puts_the_credential_tools_back(self):
        """DHOLE_VAULT=1 只放回 vault / login，不放回 recipe / watch。"""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_TASKS", "DHOLE_VAULT")}
        env["DHOLE_VAULT"] = "1"
        out = subprocess.run([sys.executable, "-c", _ENABLED_SNIPPET],
                             capture_output=True, text=True, env=env, timeout=180)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert set(payload["enabled"]) == set(payload["all"]) - {"recipe", "watch"}
        assert {"vault", "login"} <= set(payload["enabled"])

    def test_both_flags_together_enable_everything(self):
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_TASKS", "DHOLE_VAULT")}
        env["DHOLE_TASKS"] = "1"
        env["DHOLE_VAULT"] = "1"
        out = subprocess.run([sys.executable, "-c", _ENABLED_SNIPPET],
                             capture_output=True, text=True, env=env, timeout=180)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert payload["enabled"] == payload["all"]

    @pytest.mark.parametrize("value,bad", [
        ("smart_frobnicate", "smart_frobnicate"),
        ("smart_fetch,bogus", "bogus"),
    ])
    def test_an_unknown_name_raises_at_import_naming_it(self, value, bad):
        """A typo must kill startup loudly, not silently drop a capability."""
        out = _run_fresh("import dhole_mcp.server", value)
        assert out.returncode != 0, "未知工具名必须在 import 期炸掉"
        assert "DHOLE_TOOLS" in out.stderr and bad in out.stderr
        assert "Valid names" in out.stderr, "报错必须列出合法名字，让人一次改对"

    def test_a_value_with_no_names_raises_instead_of_enabling_nothing(self):
        out = _run_fresh("import dhole_mcp.server", ",,,")
        assert out.returncode != 0
        assert "names no tools" in out.stderr


class TestTheWireSetIsFiltered:

    def test_only_enabled_tools_are_advertised_in_schema_order(self):
        code = (
            "import json, dhole_mcp.server as s;"
            "print(json.dumps([td['name'] for td in"
            " s.MasterFetchServer._enabled_tool_defs()]))"
        )
        out = _run_fresh(code, "smart_fetch,smart_search")
        assert out.returncode == 0, out.stderr[-600:]
        assert json.loads(out.stdout) == ["smart_fetch", "smart_search"], (
            "广告集被过滤，且保持 _TOOL_DEFS 的顺序")


class TestDispatchRefusesDisabledTools:

    CODE = (
        "import asyncio, dhole_mcp.server as s\n"
        "async def main():\n"
        "    srv = s.MasterFetchServer(cache_ttl=0)\n"
        "    try:\n"
        "        await srv._dispatch('smart_crawl', {'url': 'https://example.com/'})\n"
        "        print('CRAWL: dispatched')\n"
        "    except ValueError as e:\n"
        "        print('CRAWL:', e)\n"
        "    try:\n"
        "        await srv._dispatch('smart_frobnicate', {})\n"
        "    except ValueError as e:\n"
        "        print('FROB:', e)\n"
        "    try:\n"
        "        r = await srv._dispatch('smart_fetch', {'url': 'not a url'})\n"
        "        print('FETCH: dispatched', type(r).__name__)\n"
        "    except Exception as e:\n"
        "        print('FETCH: raised', type(e).__name__, str(e)[:100])\n"
        "asyncio.run(main())\n"
    )

    def test_disabled_known_unknown_and_enabled_calls_behave_differently(self):
        out = _run_fresh(self.CODE, "smart_fetch")
        assert out.returncode == 0, out.stderr[-600:]
        crawl = next(x for x in out.stdout.splitlines() if x.startswith("CRAWL:"))
        frob = next(x for x in out.stdout.splitlines() if x.startswith("FROB:"))
        fetch = next(x for x in out.stdout.splitlines() if x.startswith("FETCH:"))
        assert "not enabled" in crawl and "DHOLE_TOOLS" in crawl
        assert "smart_fetch" in crawl, "报错必须点名当前启用集，给出改法"
        assert "Unknown tool" in frob, "真未知工具的报错不能与「未启用」混淆"
        assert "not enabled" not in fetch, "启用的工具不许被这道门误拦"




class TestInstructionsFollowTheEnabledSet:

    def test_composing_every_tool_reproduces_the_literal(self):
        """Drift guard: the parts and DHOLE_INSTRUCTIONS are two sources of one
        text (tool_payload_measure.py reads the literal via ast.literal_eval)."""
        assert server_mod._compose_instructions(
            frozenset(server_mod._TOP_LEVEL_ARGS)) == server_mod.DHOLE_INSTRUCTIONS

    def test_a_subset_drops_the_other_tools_routing_lines(self):
        text = server_mod._compose_instructions(frozenset({"smart_fetch"}))
        assert "smart_fetch" in text
        for absent in ("smart_crawl", "smart_search", "feed_fetch",
                       "parse", "screenshot", "resolve_url"):
            assert absent not in text, f"子集 instructions 不得路由到未启用的 {absent}"

    def test_the_trust_rules_survive_every_subset(self):
        """Intro and the trust rules are tool-agnostic; they must always ship."""
        text = server_mod._compose_instructions(frozenset({"parse"}))
        assert "Dhole is the web toolkit" in text
        assert "untrusted DATA" in text
        assert "archived_at" in text
        assert "gov/edu/github" in text

    def test_no_routing_header_when_nothing_routable_is_enabled(self):
        text = server_mod._compose_instructions(frozenset({"cache_clear"}))
        assert "Routing:" not in text, "没有可路由的工具时留着空 Routing: 头是废话"

    def test_every_routing_line_names_its_own_tool(self):
        for name, line in server_mod._INSTRUCTIONS_ROUTING.items():
            assert name in line, f"{name} 的路由行里没出现自己的名字"

    def test_the_wire_instructions_come_from_the_enabled_set(self):
        code = ("import dhole_mcp.server as s;"
                "print('smart_crawl' in s.ACTIVE_INSTRUCTIONS,"
                " 'smart_fetch' in s.ACTIVE_INSTRUCTIONS)")
        out = _run_fresh(code, "smart_fetch")
        assert out.returncode == 0, out.stderr[-600:]
        assert out.stdout.strip() == "False True", (
            "wire 发的是 ACTIVE_INSTRUCTIONS，不是全量字面量")

    def test_the_default_wire_instructions_are_the_literal_minus_the_opt_in_lines(self):
        """默认 wire 与全集字面量**只**差两个 opt-in 组的路由行。

        断言写成「差集恰好是那几行」而不是「两者相等」：后者在这个改动之后
        已经是废话（ACTIVE_INSTRUCTIONS 就是按默认集组合出来的），而前者能
        同时抓住两种错误——opt-in 的行漏进默认 wire（默认开销白涨），以及
        别的行被顺手改掉。
        """
        code = (
            "import dhole_mcp.server as s;"
            "optin=''.join(v for k,v in s._INSTRUCTIONS_ROUTING.items()"
            " if k in (s._OPT_IN_TOOLS | s._OPT_IN_TOOLS_VAULT));"
            "print(repr(s.DHOLE_INSTRUCTIONS.replace(optin,'')"
            " == s.ACTIVE_INSTRUCTIONS));"
            "print(len(optin))"
        )
        out = _run_fresh(code, None)
        assert out.returncode == 0, out.stderr[-600:]
        same, n = out.stdout.strip().splitlines()
        assert same == "True", "默认 instructions 必须恰好是全集字面量减去 opt-in 路由行"
        assert int(n) > 0, "opt-in 的行不能是空串，否则这条断言什么都没验"
