"""test_server_10.py —— 由 11 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import pytest
from dhole_mcp import security
import ast
import inspect
from pathlib import Path
from dhole_mcp.server import MasterFetchServer
import os
import sys
from dhole_mcp.server import _blocked_path_prefix
from dhole_mcp.server import _DISPATCH_REQUIRED, _TOP_LEVEL_ARGS
import pathlib
from dhole_mcp import server as server_mod
from dhole_mcp import identity
from dhole_mcp.server import ArgumentRefusal
from dhole_mcp import context
import dhole_mcp
from dhole_mcp.server import _http_allowed_hosts, _http_allowed_origins, _http_token_ok


# ============================================================================
# 来源：test_an_unsendable_write_is_not_put_to_the_user.py
# ============================================================================
# 一发永远发不出去的写，不许占用用户的授权（评审 R02-118 · F101）。

WRITE_METHODS = ['POST', 'PUT', 'PATCH', 'DELETE']
FAMILIES = [
    ('permanent', 'http://metadata.google.internal/x', security.PERMANENT_URL_RECOVERY),
    ('permanent-ip', 'http://169.254.169.254/x', security.PERMANENT_URL_RECOVERY),
    ('rebind', 'http://x.nip.io/y', security.PERMANENT_URL_RECOVERY),
    ('private', 'http://127.0.0.1:9/ok', security.PRIVATE_ADDRESS_RECOVERY),
    ('shape', 'http://', security.URL_SHAPE_RECOVERY),
]
# 能发出去的 URL：这台机器的解析判词是 fake-ip，隧道段被容忍，所以这颗过得了 validate_url，
# 而确认闸在它之前就返回 —— 一根连接都不会付出去（正控制靠这一点成立）。
SENDABLE = 'https://example.com/api/things'


async def _face(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from dhole_mcp.server import MasterFetchServer

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool('web_request', args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheAskComesAfterThePossibility:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('method', WRITE_METHODS)
    @pytest.mark.parametrize('family,url,needle', FAMILIES)
    async def test_an_unsendable_write_is_refused_before_any_approval_ask(self, method, family,
                                                                          url, needle):
        d = await _face({'url': url, 'method': method, 'body': '{}'})
        na = d.get('next_action') or ''
        err = d.get('error') or ''
        assert needle.split('{')[0][:44] in na, (
            f'{method} 打在 {family} 上时第一个失败不是那一族：{na[:140]}')
        assert 'confirm=true' not in na, (
            f'这一发永远发不出去，却还在教人去问授权：{na[:140]}')
        assert 'is a write' not in err, f'确认闸先说话了：{err[:120]}'
        assert d.get('content_ok') is False and d.get('status') == 0, '判决不许变'


class TheGateStillWorks:
    """正控制：顺序改了，钥匙没改 —— 能发出去的写照样要先问人。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize('method', WRITE_METHODS)
    async def test_a_sendable_write_still_demands_confirm(self, method):
        d = await _face({'url': SENDABLE, 'method': method, 'body': '{}'})
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        assert 'is a write' in err, f'确认闸被修没了：{err[:140]}'
        assert 'confirm=true' in na, na[:160]
        assert d.get('status') == 0 and d.get('content_ok') is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize('url,needle', [
        ('http://metadata.google.internal/x', security.PERMANENT_URL_RECOVERY),
        ('http://127.0.0.1:9/ok', security.PRIVATE_ADDRESS_RECOVERY)])
    async def test_confirm_true_does_not_open_anything(self, url, needle):
        """带了钥匙也一样拒：这一族没有门（私网那颗只在这台机器没开豁免时成立）。"""
        d = await _face({'url': url, 'method': 'POST', 'body': '{}', 'confirm': True})
        na = d.get('next_action') or ''
        assert needle.split('{')[0][:44] in na, na[:140]
        assert d.get('status') == 0, '不许因为 confirm=true 就发出去'


class TheVerbSentencesStayFirst:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args,needle', [
        ({'url': 'http://metadata.google.internal/x', 'body': '{}'}, 'method is required'),
        ({'url': 'http://metadata.google.internal/x', 'method': 'GET', 'body': '{}'},
         'is a read, not a request')])
    async def test_a_wrong_door_question_is_answered_before_the_address(self, args, needle):
        """先说"这扇门不这么用"，再说"这颗地址发不出去" —— 前者是门的选择，后者是地址的事实。"""
        d = await _face(args)
        assert needle in (d.get('error') or ''), d


# ============================================================================
# 来源：test_call_sites_match_the_handler_signature.py
# ============================================================================
# src/ 里对工具处理器的调用，实参名必须真的在那个处理器的签名里。

SRC = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"


def _tool_names() -> set[str]:
    return {td["name"] for td in MasterFetchServer._TOOL_DEFS}


def _accepted(tool: str) -> tuple[set[str], bool]:
    """(形参名集合, 是否有 **kwargs)。"""
    handler = getattr(MasterFetchServer, tool, None)
    if handler is None:
        return set(), True
    params = inspect.signature(handler).parameters
    return (
        {name for name in params},
        any(p.kind is p.VAR_KEYWORD for p in params.values()),
    )


def violations_in(source: str, *, tools: set[str] | None = None,
                  filename: str = "<src>") -> list[str]:
    """返回形如 ``文件:行 <tool> 传了不存在的实参 'x'`` 的违规列表。"""
    tools = tools or _tool_names()
    accepted = {t: _accepted(t) for t in tools}
    out: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in tools:
            continue
        # `**something` 转发：静态判不了，放行
        if any(kw.arg is None for kw in node.keywords):
            continue
        params, has_var_kw = accepted[func.attr]
        if has_var_kw:
            continue
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params:
                out.append(
                    f"{filename}:{kw.lineno} {func.attr} 传了不存在的实参 "
                    f"{kw.arg!r}（真签名没有它 —— 是不是从参数面下线了？）")
    return out


def test_no_src_call_site_passes_a_parameter_the_handler_rejects():
    bad: list[str] = []
    files = sorted(SRC.glob("*.py"))
    assert files, f"没扫到源文件：{SRC}"
    for path in files:
        bad += violations_in(path.read_text(encoding="utf-8"),
                             filename=path.name)
    assert not bad, (
        "src/ 里有调用点传了处理器不接受的实参 —— 运行时就是一个 TypeError，\n"
        "如果它落在某个 except 里，会变成「静默失败」而不是红：\n  "
        + "\n  ".join(bad))


def test_the_guard_notices_the_exact_regression_it_was_born_from():
    """反证：把 crawl.py 那行原样喂进来，必须被判红。

    守卫的有效性等于它能拦住历史。这条不依赖真源码，喂一段字符串。
    """
    snippet = (
        "async def f(server):\n"
        "    return await server.web_read(\n"
        "        url=u, extraction_type='html',\n"
        "        proxy=_crawl_proxy,\n"
        "        ignore_robots=ignore_robots,\n"
        "    )\n"
    )
    bad = violations_in(snippet, tools={"web_read"}, filename="snippet.py")
    assert len(bad) == 1, bad
    assert "'proxy'" in bad[0], bad


def test_the_guard_leaves_a_legitimate_forwarding_call_alone():
    """`**call` 转发是合法形态，不该被判红（否则守卫会逼人删掉正确的写法）。"""
    snippet = "async def f(server, call):\n    return await server.web_read(url=u, **call)\n"
    assert violations_in(snippet, tools={"web_read"}, filename="snippet.py") == []


# ============================================================================
# 来源：test_a_sibling_directory_is_not_inside_the_gate.py
# ============================================================================
# R02-F64 · 门看的是**目录**，不是「名字以它开头」（R02-079 · P2）。

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


HOME = os.path.expanduser("~").replace("/", "\\") if os.name == "nt" else os.path.expanduser("~")


def _p(*parts):
    sep = "\\" if os.name == "nt" else "/"
    return HOME + sep + sep.join(parts)


# ── 格 1：兄弟目录不再被冤枉 ────────────────────────────────────────────

class TestASiblingIsNotTheDirectory:

    @pytest.mark.parametrize("name", [".dhole-tmp", ".dholestuff", ".dholeX",
                                      ".ssh-tmp", ".aws-keys", ".kubelet"])
    def test_a_name_that_shares_a_prefix_is_not_the_blocked_directory(self, name):
        got = _blocked_path_prefix(_p(name, "notes.md"))
        assert got == "", (
            f"{name} 不是 {name.split('-')[0]} 的孩子，只是名字蹭了前缀：{got!r}")

    @pytest.mark.parametrize("name", [".envrc", ".environment", ".env.local",
                                      ".bash_history.bak", ".netrc.d"])
    def test_a_file_that_shares_a_credential_name_is_not_that_credential(self, name):
        assert _blocked_path_prefix(_p(name)) == "", name


# ── 格 2：名单一个不许少（安全方向）─────────────────────────────────────

class TestTheListIsStillTheList:

    @pytest.mark.parametrize("rel", [".dhole", ".ssh", ".gnupg", ".aws", ".kube",
                                     ".azure", ".config/gcloud", ".config/gh",
                                     ".env", ".bash_history", ".zsh_history",
                                     ".git-credentials", ".netrc"])
    def test_every_blocked_name_still_blocks_itself(self, rel):
        path = _p(*rel.split("/"))
        assert _blocked_path_prefix(path) != "", path

    @pytest.mark.parametrize("rel", [[".dhole", "config.json"],
                                     [".ssh", "id_ed25519"],
                                     [".dhole", "nested", "deeper.json"]])
    def test_every_blocked_name_still_blocks_its_children(self, rel):
        assert _blocked_path_prefix(_p(*rel)) != "", rel

    def test_the_system_directory_still_blocks_by_its_real_root(self):
        root = os.environ.get("SystemRoot") or "C:\\Windows"
        assert _blocked_path_prefix(os.path.join(root, "System32", "drivers",
                                                 "etc", "hosts")) != ""
        assert _blocked_path_prefix(root) != ""


# ── 格 3：边界要算上大小写与分隔符两种写法 ──────────────────────────────

class TestTheBoundaryCountsBothSpellings:

    def test_a_sibling_written_with_a_trailing_separator_is_still_a_sibling(self):
        # `~/.dhole-tmp/` 与 `~/.dhole-tmp\x` 同一个意思，都不该挡。
        assert _blocked_path_prefix(_p(".dhole-tmp", "")) == ""

    def test_the_blocked_directory_written_uppercase_is_still_blocked(self):
        path = _p(".dhole", "config.json").upper()
        assert _blocked_path_prefix(path) != "", path

    def test_the_fix_does_not_move_the_list(self):
        # 名单只有一个作者：这一格不许它被顺手加长或改短。
        import inspect

        src = inspect.getsource(_blocked_path_prefix)
        for needle in (".dhole", ".ssh", ".gnupg", ".aws", ".kube", ".azure",
                       ".config/gcloud", ".config/gh", ".env", ".git-credentials"):
            assert needle in src, needle


# ============================================================================
# 来源：test_a_bare_client_missing_required_arg_gets_the_envelope.py
# ============================================================================
# 裸 MCP 客户端的缺参调用要拿标准 invalid_request 信封（评审R1刀2）。

def _text(result) -> str:
    content, _payload = result
    return content[0].text


class TestABareClientMissingRequiredArgGetsTheEnvelope:

    @pytest.mark.asyncio
    async def test_web_crawl_without_url_is_an_envelope_not_a_keyerror(self):
        out = await MasterFetchServer()._dispatch("web_crawl",
                                                  {"resume_token": "x:0"})
        payload = json.loads(_text(out))
        err = payload["error"]
        assert err.startswith("invalid_request"), err
        assert err != "'url'", "KeyError repr 不能再当 error 文本"
        assert "url" in err and "Missing required argument" in err
        assert "resume_token" in err, "error 列出该工具接受的参数名"
        assert "no request was made" in payload["next_action"].lower()

    @pytest.mark.asyncio
    async def test_web_discover_without_url_is_an_envelope_not_a_typeerror(self):
        out = await MasterFetchServer()._dispatch("web_discover", {})
        text = _text(out)
        assert "invalid_request" in text
        assert "TypeError" not in text
        assert "url" in text and "Missing required argument" in text

    @pytest.mark.asyncio
    async def test_web_search_without_query_is_an_envelope(self):
        """评审R2刀4 之后这句由方法体说，不再由这张静态表说。

        表不认识 mode，而 `mode='find_similar'` 正是一颗**没有**查询词的调用：把它
        留在表里，那颗模式在裸客户端上就做不到。信封款式不变（invalid_request +
        点名 query + 一个请求都不发），只是判据搬到了认识 mode 的那一侧。
        """
        out = await MasterFetchServer()._dispatch("web_search", {})
        text = _text(out)
        assert "invalid_request" in text
        assert "query" in text
        assert "find_similar" in text, "唯一不带查询词的出路要在报错里说得出"

    @pytest.mark.asyncio
    async def test_doc_parse_without_file_path_is_an_envelope(self):
        out = await MasterFetchServer()._dispatch("doc_parse", {})
        text = _text(out)
        assert "invalid_request" in text
        assert "file_path" in text and "Missing required argument" in text

    @pytest.mark.asyncio
    async def test_an_empty_url_is_refused_before_the_plan_chain(self):
        """: empty 不静默落进 plan 校验链——同一个信封，同一个说法。"""
        out = await MasterFetchServer()._dispatch("web_crawl", {"url": "   "})
        payload = json.loads(_text(out))
        assert payload["error"].startswith("invalid_request")
        assert "url" in payload["error"]


class TestTheRequiredTableDoesNotDrift:

    def test_every_wire_required_tool_is_either_tabled_or_self_checked(self):
        schema_required = {
            td["name"] for td in MasterFetchServer._TOOL_DEFS
            if "required" in td["inputSchema"]
        }
        assert schema_required == (
            set(_DISPATCH_REQUIRED) | {"web_act", "web_request", "feed_read"}
        ), "schema 声明 required 的工具要么在 dispatch 表里，要么方法体自检信封"

    def test_a_full_argument_set_passes_the_guard(self):
        assert _DISPATCH_REQUIRED["web_crawl"] == ("url",)
        assert _TOP_LEVEL_ARGS["web_crawl"] >= {"url", "resume_token"}


# ============================================================================
# 来源：test_the_wire_tool_descriptions_have_one_author.py
# ============================================================================
# 连接面上的工具参数说明只有一个作者：`_TOOL_DEFS`。签名里不许再有第二份（D-32）。

SERVER_SRC = pathlib.Path(inspect.getsourcefile(server_mod)).read_text(encoding="utf-8")
TREE = ast.parse(SERVER_SRC)

TOOL_NAMES = [d["name"] for d in MasterFetchServer._TOOL_DEFS]


def _names(kind) -> list:
    out = []
    for node in ast.walk(TREE):
        if isinstance(node, kind) and isinstance(getattr(node, "value", None), ast.Name):
            out.append(node.value.id)
    return out


class TestTheShadowIsGone:

    def test_the_module_carries_no_field_metadata_at_all(self):
        """`Field(...)` 调用与 `Annotated[...]` 下标一个都不许留在 server.py。"""
        fields = [n for n in _names(ast.Call) if n == "Field"]
        subscripts = [n for n in _names(ast.Subscript) if n == "Annotated"]
        assert not fields and not subscripts, (
            f"签名里又长出了第二份客户端文本（Field {len(fields)} 处 / Annotated "
            f"{len(subscripts)} 处）。发上 wire 的那一份唯一的作者是 "
            "`MasterFetchServer._TOOL_DEFS` —— 改那里。")

    def test_the_two_imports_left_with_the_shadow(self):
        src = SERVER_SRC
        for line in ("from pydantic import Field", "from typing import Annotated"):
            assert line not in src, f"{line} 是影子时代的残留：描述摘掉后它没人用了"

    def test_signatures_carry_types_and_defaults_only(self):
        """摘描述不许顺手改到类型或默认值 —— 那两处都是客户端看得见的。"""
        for name in TOOL_NAMES:
            method = getattr(MasterFetchServer, name)
            for pname, param in inspect.signature(method).parameters.items():
                text = str(param.annotation)
                assert "description" not in text and "Field" not in text, \
                    f"{name}.{pname} 的注解里还带着描述: {text[:80]}"
        read = inspect.signature(MasterFetchServer.web_read).parameters
        assert read["cache_ttl"].default is None
        assert read["extraction_type"].default == "markdown"
        assert read["offset"].default == 0


class TestTheWireKeepsItsOwnWords:
    """摘掉影子之后，连接面那一份话一句都不能少 —— 这才是"一处作者"的另一半。"""

    def test_every_advertised_parameter_is_described(self):
        defs = MasterFetchServer._enabled_tool_defs()
        total = 0
        for tool in defs:
            for pname, schema in (tool["inputSchema"].get("properties") or {}).items():
                total += 1
                assert (schema.get("description") or "").strip(), \
                    f"{tool['name']}.{pname} 上了 wire 却没有一句话"
        assert total >= 100, f"参数位只剩 {total} 个 —— 表被人剪短了，不是影子被摘了"

    def test_the_served_definitions_are_the_table_itself(self):
        """`_enabled_tool_defs()` 只是过滤，不是第二处写作。"""
        table = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        for tool in MasterFetchServer._enabled_tool_defs():
            assert tool is table[tool["name"]], (
                f"{tool['name']} 交出的不是表里那一份 —— 又多了一个作者")

    def test_the_wire_price_did_not_move(self):
        """棘轮的那把尺与这里的口径同源：删描述不该改变连接面字节。"""
        from tests.test_wire_moves_land_in_the_manual import CONNECT_CEILING

        total = (len(json.dumps({"tools": MasterFetchServer._TOOL_DEFS},
                                ensure_ascii=False, separators=(",", ":")))
                 + len(server_mod.DHOLE_INSTRUCTIONS))
        assert total == CONNECT_CEILING, (
            f"连接面量到 {total}，棘轮写着 {CONNECT_CEILING} —— "
            "摘影子不该动这个数，动了就是顺手改了表")


# ============================================================================
# 来源：test_the_private_door_is_named_only_where_it_stands.py
# ============================================================================
# R01-009 — the private-host door gets named only where it actually stands.

PUBLIC = "https://www.ibm.com/think/topics/saas"
LOOPBACK = "http://127.0.0.1:1/"
NAMED = "http://my-service.local/api"


def _note(url: str) -> str:
    return MasterFetchServer()._identity_for(url)[1]


# ─── the note ───────────────────────────────────────────────────────────────

def test_a_public_target_does_not_name_the_private_door():
    identity.set_value("allow_private", True)
    assert "allow_private" not in _note(PUBLIC)


def test_a_public_target_does_not_name_a_door_that_names_another_host():
    identity.set_value("allow_private", ["my-service.local"])
    assert "allow_private" not in _note(PUBLIC)


def test_a_loopback_target_still_names_the_door():
    """Over-correcting must not cost the honest half: the waiver really is what
    makes a loopback target reachable, and the user needs to see it ran on it."""
    identity.set_value("allow_private", True)
    assert "allow_private" in _note(LOOPBACK)


def test_a_host_the_stored_list_names_still_gets_the_door():
    """`my-service.local` is not a private *literal* — the whole point of naming a
    host is that its geography is unknown until DNS answers, and the door is the
    reason the attempt is made at all."""
    identity.set_value("allow_private", ["my-service.local"])
    assert "allow_private" in _note(NAMED)


def test_the_other_stored_keys_are_unaffected():
    identity.set_value("allow_private", True)
    identity.set_value("proxy", "http://127.0.0.1:7890")
    note = _note(PUBLIC)
    assert "proxy" in note, note
    assert "allow_private" not in note


def test_nothing_stored_notes_nothing():
    assert _note(PUBLIC) == ""
    assert _note(LOOPBACK) == ""


# ─── the gate: this fix must not move it ────────────────────────────────────

def test_the_stored_door_still_ships_for_a_public_target():
    """Silencing the NAME must not silence the value: dropping it from `values`
    would change which targets validate_url lets through — a public name resolving
    into a TUN fake-ip range still needs the stored decision to exist downstream."""
    identity.set_value("allow_private", True)
    values, _note_text = MasterFetchServer()._identity_for(PUBLIC)
    assert values["allow_private"] is True


@pytest.mark.parametrize("value,url,expected", [
    (True, LOOPBACK, True),
    (True, NAMED, False),
    (["my-service.local"], NAMED, True),
    (["my-service.local"], PUBLIC, False),
    (True, PUBLIC, False),
    ("192.168.1.50", "http://192.168.1.50/x", True),
    (None, LOOPBACK, False),
    ("", PUBLIC, False),
])
def test_the_door_covers_a_target_by_geography_or_by_name(value, url, expected):
    assert security.allow_private_covers(value, url) is expected


def test_the_cover_check_never_raises_on_a_broken_url():
    assert security.allow_private_covers(True, "not a url") is False
    assert security.allow_private_covers(True, "") is False
    assert security.allow_private_covers(True, "file:///etc/passwd") is False


# ============================================================================
# 来源：test_a_designed_refusal_never_leaves_naked_through_the_transport.py
# ============================================================================
# 设计过的参数拒绝不许从 transport 兜底裸逃（2026-10-08 真实使用模拟 · 刀1）。

SITE = "https://example.com/"


async def _through_the_real_sdk(tool: str, args: dict) -> str:
    """一次真握手：InMemoryTransport 包住的正是 stdio/HTTP 跑的同一个 handler。"""
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return "".join(getattr(b, "text", "") for b in res.content)


def _envelope(text: str) -> dict:
    payload = json.loads(text)
    assert payload.get("error", "").startswith("invalid_request"), (
        f"这不是 invalid_request 信封：{text[:300]}")
    assert payload.get("content_ok") is False, payload
    assert payload.get("next_action"), "设计过的拒绝必须自带出路"
    assert payload.get("summary"), "拒绝不许到达时没有 summary"
    return payload


class TestADesignedRefusalArrivesEnveloped:

    @pytest.mark.asyncio
    async def test_an_unsupported_arg_keeps_its_supported_list_whole(self):
        """被剪掉的尾巴正是解药：支持列表必须整份到达。"""
        text = await _through_the_real_sdk(
            "web_crawl", {"url": SITE, "max_content_chars": 500})
        payload = _envelope(text)
        assert "max_content_chars" in payload["error"]
        for name in ("schema", "search", "selector", "sitemap", "timeout", "url"):
            assert f"'{name}'" in payload["error"], (
                f"支持列表又断了，{name} 没到：{payload['error'][-80:]}")

    @pytest.mark.asyncio
    async def test_a_bad_action_shape_arrives_with_a_way_out(self):
        text = await _through_the_real_sdk(
            "web_act", {"url": SITE,
                        "actions": [{"fill": {"selector": "input", "value": "x"}}]})
        payload = _envelope(text)
        assert "'fill'" in payload["error"], payload["error"]
        assert "text" in payload["error"], "形状错要说清正确的键叫什么"

    @pytest.mark.asyncio
    async def test_a_bare_screenshot_is_refused_in_the_same_shape(self):
        text = await _through_the_real_sdk(
            "web_act", {"url": SITE, "actions": [{"screenshot": True}]})
        payload = _envelope(text)
        assert "screenshot" in payload["error"]
        assert "full_page" in payload["error"], "三种合法形状一种都不许漏"


class TestTheLayeringStaysWhereItWas:
    """信封只在协议边界生成。dispatch 那一层不许跟着改口。"""

    @pytest.mark.asyncio
    async def test_the_refusal_is_still_a_valueerror_subclass(self):
        """旧锁按 ValueError 收，子类不改它们的判据。"""
        assert issubclass(ArgumentRefusal, ValueError)
        with pytest.raises(ValueError, match="Unsupported argument") as boom:
            await MasterFetchServer()._dispatch(
                "web_read", {"url": SITE, "headed": True})
        assert isinstance(boom.value, ArgumentRefusal)
        assert "headed" not in str(boom.value).split("Supported:")[1], (
            "白名单把 headed 放行了")


# ============================================================================
# 来源：test_every_door_is_asked_of_every_roster.py
# ============================================================================
# 每张登记表都要被门名册问过一遍（B-75 的正解 · 一把会自己报红的尺）。

FETCH_KINDS = {'read', 'write', 'action', 'search'}


def _roster() -> list[str]:
    return [td['name'] for td in MasterFetchServer._TOOL_DEFS]


@pytest.mark.parametrize('door', _roster())
def test_every_door_has_a_declared_clause(door):
    """① 在册：这扇门行首那句话有人写过，不是 `_UNDECLARED_DOOR_WAY_OUT` 代答。"""
    assert door in context._DOOR_KIND, (
        f'{door} 的门话仍由"没人认领"那一支代答（B-75 的形状：表外一支不会自己报红）')


@pytest.mark.parametrize('door', _roster())
def test_non_fetch_doors_have_their_own_refusal_shape(door):
    """② + ③ 非抓取的门要有自己的拒绝形状，而且只许有一副。"""
    kind = context._DOOR_KIND[door]
    if kind in FETCH_KINDS:
        # 抓取那一族本来就说抓取：13 格是它们的衣服（F97 的反向锁在
        # tests/test_a_state_door_refuses_in_its_own_shape.py 里钉着"一格不许少"）。
        return
    in_model = _projection_table(door)
    in_prose = door in _prose_doors()
    assert in_model or in_prose, (
        f'{door}（kind={kind}）既不在 `_STATE_DOOR_MODELS` 也不在 `_PROSE_DOORS` —— '
        '它的参数拒绝会又去借抓取壳（R02-103 的复发路径）')
    assert not (in_model and in_prose), (
        f'{door} 同时登记了两副形状：同一扇门两条路两种语言（R02-115 的复发路径）')


def _projection_table(door: str) -> bool:
    import dhole_mcp.server as sm
    return door in sm._STATE_DOOR_MODELS


@pytest.mark.parametrize('door', _roster())
def test_every_door_has_a_parameter_face(door):
    """⑤ 第四张表（B-76 要的常驻版）：每扇都得在 `_TOP_LEVEL_ARGS` 里有自己的参数面。

    这张表是陌生参数名那颗守卫的名单来源（`server._reject_unknown_args`），表外的一扇
    不报错 —— 它只是**谁的话都不收**。反向也问：一张参数面挂在一个不发的门名上，
    就是给一条走不到的句子养裁判。
    """
    import dhole_mcp.server as sm
    assert door in sm._TOP_LEVEL_ARGS, f'{door} 没有参数面（B-75 的形状：表外一支不会自己报红）'
    assert set(sm._TOP_LEVEL_ARGS) <= set(_roster()), sorted(
        set(sm._TOP_LEVEL_ARGS) - set(_roster()))


def _prose_doors() -> set:
    import dhole_mcp.server as sm
    return set(sm._PROSE_DOORS)


def test_the_roster_is_not_hand_copied():
    """反向锁：判据的门名单必须现场从 `_TOOL_DEFS` 取，不许自己抄一份。"""
    doors = _roster()
    assert len(doors) == len(set(doors)) > 8, doors
    assert 'sys_state' in doors and 'sys_guide' in doors and 'cred_defaults' in doors
    # 表外默认支还得留着兜未来的生门：它不是死代码，删了它上面两颗就失去意义
    assert context._UNDECLARED_DOOR_WAY_OUT
    with context.tool_scope('a_tool_that_does_not_exist_yet'):
        assert context.nothing_went_out() == context._UNDECLARED_DOOR_WAY_OUT


# ============================================================================
# 来源：test_doc_parse_reads_the_credential_files_next_door.py
# ============================================================================
# doc_parse 读得动隔壁的凭据文件 —— 黑名单拦得住 ~/.ssh，拦不住 ~/.dhole。

@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """把 ``~`` 指到临时目录 —— 凭据路径全是 fixture，一个真的都不碰。"""
    monkeypatch.setattr(
        os.path, "expanduser",
        lambda p: str(tmp_path / p.replace("~/", "")) if p.startswith("~/") else p,
    )
    return tmp_path


@pytest.mark.asyncio
async def test_doc_parse_reads_the_credential_files_next_door(sandbox_home):
    """症状本体：沙箱 ~/.dhole/config.json 被读出内容而不是拒绝。"""
    f = sandbox_home / ".dhole" / "config.json"
    f.parent.mkdir(parents=True)
    f.write_text('{"DHOLE_HTTP_TOKEN": "sk-FAKE-BEARER"}', encoding="utf-8")
    server = MasterFetchServer()
    r = await server.doc_parse(str(f))
    assert r.error.startswith("Access denied")
    assert "sk-FAKE-BEARER" not in "\n".join(r.content)


@pytest.mark.parametrize("rel", [
    ".dhole/config.json",
    ".dhole/cdp_hosts.json",
    ".config/gcloud/application_default_credentials.json",
    ".config/gh/hosts.yml",
    ".azure/accessTokens.json",
    ".kube/config.yaml",
])
def test_the_new_credential_rooms_are_in_the_block_list(sandbox_home, rel):
    assert _blocked_path_prefix(str(sandbox_home / rel)) != ""


@pytest.mark.parametrize("rel", [
    ".ssh/id_rsa", ".gnupg/secring.gpg", ".aws/credentials",  # 旧三间，仍在
])
def test_the_old_rooms_stay_blocked(sandbox_home, rel):
    assert _blocked_path_prefix(str(sandbox_home / rel)) != ""


def test_programdata_ssh_is_blocked():
    assert _blocked_path_prefix("C:\\ProgramData\\ssh\\keys.txt") != ""


def test_the_windows_prefix_follows_systemroot(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "WinRoot"))
    if os.name == "nt":
        assert _blocked_path_prefix(
            str(tmp_path / "WinRoot" / "System32" / "notes.txt")) != ""
    else:
        # 非 Windows 没有 SystemRoot 语义，写死的字面量仍是拦截面的一部分
        assert _blocked_path_prefix("c:\\windows\\system32\\notes.txt") != ""


@pytest.mark.asyncio
async def test_a_normal_report_still_parses(tmp_path):
    """核心用途零损伤：普通本地文件照常 200。"""
    f = tmp_path / "report.md"
    f.write_text("# 标题\n\n正文一句话。\n", encoding="utf-8")
    server = MasterFetchServer()
    r = await server.doc_parse(str(f))
    assert r.status == 200
    assert r.content_ok is True


# ============================================================================
# 来源：test_import_provenance.py
# ============================================================================
# KB-8: 跑测试时到底在验哪份代码。
# 重名消歧：SRC → SRC__import_provenance

SRC__import_provenance = (Path(__file__).resolve().parent.parent / "src").resolve()


def test_tests_validate_this_checkout_not_an_installed_copy():
    got = Path(dhole_mcp.__file__).resolve()
    assert "site-packages" not in str(got), (
        f"import dhole_mcp 命中了已安装的副本：{got}"
    )
    assert SRC__import_provenance in got.parents, (
        f"import dhole_mcp 落在 {got}，不在本 checkout 的 {SRC__import_provenance} 下 —— "
        f"这轮测试验的不是这份源码。用 `PYTHONPATH=src python -m pytest ...`，"
        f"或确认 pyproject 的 pythonpath 生效"
    )
    # 同一次运行里更靠近断言的那些模块也必须同源（server 是被验得最多的一个）。
    import dhole_mcp.server as server

    assert Path(server.__file__).resolve().parent == got.parent


# ─── 同一个名字被定义两次：后一个静默遮蔽前一个 ────────────────────────────
#
# 实测到的场景：一个测试类被 `>>` 追加了两次，于是**第二次定义遮蔽了第一次**，
# pytest 照常收集、照常全绿 —— 但只跑了 24 条里的一半。红/绿报告本身没撒谎，
# 它只是回答了另一个问题（"后一份 12 条过了吗"）。
#
# 这和本文件的主旨是同一件事：**先确认"全绿"指的是哪批测试**。同一模块里
# 顶层的类/函数重名、同一个类里方法重名，都属于"验证结论不可信"的一类，所以
# 故意大声失败。
#
# 只查模块体与类体的**直接**子节点，不看 `if`/`try` 分支内部 —— 条件定义
# （`try: def f() ... except ImportError: def f() ...`）是合法写法，而遮蔽是
# 无条件的。


_SCANNED_DIRS = ("src/dhole_mcp", "tests")


def _duplicates(names: list[str]) -> list[str]:
    return sorted({n for n in names if names.count(n) > 1})


def _module_definitions(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = [n.name for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    issues = [f"模块级重名: {_duplicates(names)}"] if _duplicates(names) else []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = [m.name for m in node.body
                       if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if _duplicates(methods):
                issues.append(f"{node.name} 里方法重名: {_duplicates(methods)}")
    return issues


def test_no_definition_is_shadowed_by_a_duplicate():
    root = SRC__import_provenance.parent
    files: list[Path] = []
    for d in _SCANNED_DIRS:
        files.extend(sorted((root / d).glob("*.py")))
    assert files, "没扫到任何文件，这条守卫本身失效了"

    offenders = {}
    for f in files:
        issues = _module_definitions(f)
        if issues:
            offenders[str(f.relative_to(root))] = issues

    assert not offenders, (
        "同一个名字被定义两次，后者会静默遮蔽前者 —— 测试会照常全绿但少跑一半。\n"
        f"{offenders}"
    )


# ============================================================================
# 来源：test_http_transport_security.py
# ============================================================================
# HTTP 传输面（G25 评审）：rebinding 防护 + 可选 bearer 门。

class TestTheHostAllowlist:

    def test_loopback_names_are_always_allowed(self):
        hosts = _http_allowed_hosts("127.0.0.1")
        for name in ("127.0.0.1", "localhost", "[::1]"):
            assert name in hosts, hosts
            assert f"{name}:*" in hosts, hosts

    def test_a_named_bind_host_is_allowed_with_any_port(self):
        hosts = _http_allowed_hosts("192.168.1.5")
        assert "192.168.1.5" in hosts and "192.168.1.5:*" in hosts, hosts

    def test_a_wildcard_bind_adds_nothing_guessable(self):
        """0.0.0.0 上客户端的 Host 无法预知：名单防不住 rebinding 之外的事，
        所以它自己也不进名单——那个配置该靠 DHOLE_HTTP_TOKEN。"""
        assert "0.0.0.0" not in _http_allowed_hosts("0.0.0.0")

    def test_origins_are_the_http_form_of_the_same_names(self):
        origins = _http_allowed_origins("127.0.0.1", 8765)
        assert "http://127.0.0.1:8765" in origins
        assert "http://localhost:8765" in origins


class TestTheBearerGate:

    def test_the_right_bearer_passes(self):
        assert _http_token_ok({"authorization": "Bearer s3cret"}, "s3cret")

    def test_the_scheme_is_case_insensitive(self):
        assert _http_token_ok({"authorization": "bearer s3cret"}, "s3cret")

    def test_the_wrong_or_absent_token_fails(self):
        assert not _http_token_ok({"authorization": "Bearer nope"}, "s3cret")
        assert not _http_token_ok({}, "s3cret")

    def test_a_prefix_of_the_token_is_not_enough(self):
        assert not _http_token_ok({"authorization": "Bearer s3cre"}, "s3cret")

    def test_the_header_alternative_passes_and_is_trimmed(self):
        assert _http_token_ok({"x-dhole-token": " s3cret "}, "s3cret")

    def test_an_unset_token_never_authenticates(self):
        assert not _http_token_ok({"authorization": "Bearer "}, "")
        assert not _http_token_ok({"x-dhole-token": "anything"}, "")


class TestTheGuardIsActuallyWired:

    def test_serve_hands_security_settings_to_the_session_manager(self):
        src = inspect.getsource(MasterFetchServer.serve)
        assert "security_settings=TransportSecuritySettings(" in src, (
            "HTTP 传输的 rebinding 防护没接线：settings=None 时 SDK 显式关闭校验")

    def test_the_token_gate_wraps_the_app_only_when_a_token_is_set(self):
        src = inspect.getsource(MasterFetchServer.serve)
        assert "if http_token:" in src and "_TokenGate(app, http_token)" in src

    def test_a_non_loopback_bind_without_a_token_prints_a_warning(self):
        src = inspect.getsource(MasterFetchServer.serve)
        assert "DHOLE_HTTP_TOKEN" in src
        assert "WARNING" in src
