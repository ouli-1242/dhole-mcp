"""Connect-time token spend: DHOLE_TOOLS registers only a subset of the 13 tools.

Why this file exists: the tools/list table plus instructions is paid on EVERY
connect, even when dhole is never called. Measured after the token audit: 19,141
chars of tool schemas + 2,141 chars of instructions per conversation = 21,282
chars, which that tokenizer run counts as ~5.1k cl100k tokens (4.16 chars per
token for this payload; the repo's older 3.88 divisor read 5.5k and was high),
with web_read alone at 6,508 chars.
``DHOLE_TOOLS=web_read,web_search`` keeps the daily drivers at roughly half
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

    @pytest.mark.parametrize("value", ["web_read", " web_read ,web_search ,"])
    def test_a_subset_registers_exactly_those_tools(self, value):
        out = _run_fresh(_ENABLED_SNIPPET, value)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert payload["enabled"] == sorted(
            n.strip() for n in value.split(",") if n.strip())
        assert len(payload["all"]) == 15, \
            "全集是 15 个工具（16.0 补上 close_session，L3/L4 补上 web_recipe/web_watch，" \
            "凭据/登录流补上 cred_vault/cred_login，文档通道补上 sys_guide，" \
            "B5 又把一张地图拆成 web_discover）；" \
            "默认关闭的也留在这里，守卫继续覆盖它们"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_means_every_tool(self, value):
        """17.0：未设 DHOLE_TOOLS = 全部工具（没有 opt-in 组；数量由上一条钉住）。"""
        out = _run_fresh(_ENABLED_SNIPPET, value)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert set(payload["enabled"]) == set(payload["all"])

    def test_a_stale_tasks_flag_enables_nothing_extra(self):
        """DHOLE_TASKS 已注销：设与不设，enabled 集合逐字节相同。"""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_TASKS", "DHOLE_VAULT")}
        out_plain = subprocess.run([sys.executable, "-c", _ENABLED_SNIPPET],
                                   capture_output=True, text=True, env=env,
                                   timeout=180)
        env["DHOLE_TASKS"] = "0"
        out_stale = subprocess.run([sys.executable, "-c", _ENABLED_SNIPPET],
                                   capture_output=True, text=True, env=env,
                                   timeout=180)
        assert out_plain.returncode == 0 and out_stale.returncode == 0
        assert json.loads(out_plain.stdout) == json.loads(out_stale.stdout)

    @pytest.mark.parametrize("value,bad", [
        ("smart_frobnicate", "smart_frobnicate"),
        ("web_read,bogus", "bogus"),
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
        out = _run_fresh(code, "web_read,web_search")
        assert out.returncode == 0, out.stderr[-600:]
        assert json.loads(out.stdout) == ["web_read", "web_search"], (
            "广告集被过滤，且保持 _TOOL_DEFS 的顺序")


class TestDispatchRefusesDisabledTools:

    CODE = (
        "import asyncio, dhole_mcp.server as s\n"
        "async def main():\n"
        "    srv = s.MasterFetchServer(cache_ttl=0)\n"
        "    try:\n"
        "        await srv._dispatch('web_crawl', {'url': 'https://example.com/'})\n"
        "        print('CRAWL: dispatched')\n"
        "    except ValueError as e:\n"
        "        print('CRAWL:', e)\n"
        "    try:\n"
        "        await srv._dispatch('smart_frobnicate', {})\n"
        "    except ValueError as e:\n"
        "        print('FROB:', e)\n"
        "    try:\n"
        "        r = await srv._dispatch('web_read', {'url': 'not a url'})\n"
        "        print('FETCH: dispatched', type(r).__name__)\n"
        "    except Exception as e:\n"
        "        print('FETCH: raised', type(e).__name__, str(e)[:100])\n"
        "asyncio.run(main())\n"
    )

    def test_disabled_known_unknown_and_enabled_calls_behave_differently(self):
        out = _run_fresh(self.CODE, "web_read")
        assert out.returncode == 0, out.stderr[-600:]
        crawl = next(x for x in out.stdout.splitlines() if x.startswith("CRAWL:"))
        frob = next(x for x in out.stdout.splitlines() if x.startswith("FROB:"))
        fetch = next(x for x in out.stdout.splitlines() if x.startswith("FETCH:"))
        assert "not enabled" in crawl and "DHOLE_TOOLS" in crawl
        assert "web_read" in crawl, "报错必须点名当前启用集，给出改法"
        assert "Unknown tool" in frob, "真未知工具的报错不能与「未启用」混淆"
        assert "not enabled" not in fetch, "启用的工具不许被这道门误拦"




class TestInstructionsFollowTheEnabledSet:

    def test_composing_every_tool_reproduces_the_literal(self):
        """Drift guard: the parts and DHOLE_INSTRUCTIONS are two sources of one
        text (tool_payload_measure.py reads the literal via ast.literal_eval)."""
        assert server_mod._compose_instructions(
            frozenset(server_mod._TOP_LEVEL_ARGS)) == server_mod.DHOLE_INSTRUCTIONS

    def test_a_subset_drops_the_other_tools_routing_lines(self):
        text = server_mod._compose_instructions(frozenset({"web_read"}))
        assert "web_read" in text
        for absent in ("web_crawl", "web_search", "feed_read",
                       "doc_parse", "web_screenshot", "web_resolve"):
            assert absent not in text, f"子集 instructions 不得路由到未启用的 {absent}"

    def test_the_trust_rules_survive_every_subset(self):
        """Intro and the trust rules are tool-agnostic; they must always ship."""
        text = server_mod._compose_instructions(frozenset({"doc_parse"}))
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
                "print('web_crawl' in s.ACTIVE_INSTRUCTIONS,"
                " 'web_read' in s.ACTIVE_INSTRUCTIONS)")
        out = _run_fresh(code, "web_read")
        assert out.returncode == 0, out.stderr[-600:]
        assert out.stdout.strip() == "False True", (
            "wire 发的是 ACTIVE_INSTRUCTIONS，不是全量字面量")

    def test_the_default_wire_instructions_are_the_full_literal(self):
        """17.0：没有 opt-in 组 —— 默认 instructions 就是全集字面量本身，
        不再有任何"减去的路由行"。"""
        assert server_mod._compose_instructions(
            frozenset(server_mod._TOP_LEVEL_ARGS)) == server_mod.DHOLE_INSTRUCTIONS
