"""test_server_11.py —— 由 9 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path
import pytest
# fixture_site 住在 evals/：路径插入必须在导入之前（单独收集本文件时没有别的
# 模块替本进程插过路径，裸导入会炸）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import fixture_site as fs
from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer
import pathlib
import os
import json
from dhole_mcp.server import DHOLE_INSTRUCTIONS, _compose_instructions
import re
from dhole_mcp import guide
from dhole_mcp.server import _TOP_LEVEL_ARGS
import ast
import inspect
from dhole_mcp.server import _INSTRUCTIONS_ROUTING


# ============================================================================
# 来源：test_crawl_honors_stored_allow_private.py
# ============================================================================
# 靶站当爬虫的种子：存量 allow_private 必须在爬的门上同样成立。

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))



_REAL_GETADDRINFO = socket.getaddrinfo


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.asyncio
async def test_without_the_stored_allowance_the_seed_is_refused(live_site):
    result = await MasterFetchServer().web_crawl(
        f"{live_site}/collections?items=3", max_pages=2, max_depth=1)
    assert "private IP" in (result.error or ""), result.error
    assert result.pages == []


@pytest.mark.asyncio
async def test_stored_allow_private_opens_the_crawl_door(live_site):
    identity.set_value("allow_private", True)
    result = await MasterFetchServer().web_crawl(
        f"{live_site}/collections?items=3", max_pages=2, max_depth=1)
    assert not result.error, result.error
    assert result.pages, "放行后应真爬到页面"
    assert any("/work/" in p.url for p in result.pages), \
        [p.url for p in result.pages]


# ============================================================================
# 来源：test_visual_assist_options_live_in_the_manual_not_the_wire.py
# ============================================================================
# wire 上的枚举与 how-to 是同一事实的第二个作者 —— 手册已承重，wire 留契约。

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_TOOLS_MD = (_ROOT / "src" / "dhole_mcp" / "skills" / "dhole-web"
             / "references" / "tools.md").read_text(encoding="utf-8")
_FIELDS_MD = (_ROOT / "src" / "dhole_mcp" / "skills" / "dhole-web"
              / "references" / "fields.md").read_text(encoding="utf-8")


def _desc(tool: str) -> str:
    return next(td for td in MasterFetchServer._TOOL_DEFS
                if td["name"] == tool)["description"]


def test_the_visual_assist_options_live_in_the_manual_not_the_wire():
    act = _desc("web_act")
    assert "VISUAL ASSIST" in act, "存在性＋验证码用途的首句要留在 wire"
    assert "The dict form adds" not in act, "dict-form 枚举句仍在线上"
    for option in ("full_page", "save_to", "image_type", "'jpeg' only"):
        assert option not in act, f"枚举项 {option} 仍在线上 —— 手册已逐项承重"
    assert "sys_guide(topic='behavior')" in act, "枚举的替换句要指得到手册"
    for option in ("decode_qr", "full_page", "save_to", "image_type", "quality"):
        assert option in _TOOLS_MD, f"手册缺 {option} —— wire 撤了手册就得接住"


def test_the_charset_recovery_howto_lives_in_the_manual_not_the_wire():
    parse = _desc("doc_parse")
    assert "metadata.encoding names the charset used" in parse, (
        "charset 探测契约句要留在 wire")
    assert "next_action says to re-call with encoding=" not in parse, (
        "恢复 how-to 仍在线上 —— 运行时错误与 fields.md 已双重承重")
    assert "metadata.encoding" in _FIELDS_MD and "encoding=" in _FIELDS_MD, (
        "手册的 fields 卷要接住恢复 how-to")


def test_the_caps_sentence_is_settled_by_p11():
    """互斥兑现：web_crawl 的 Caps 句是 P11 的账，P11 已在此一次性 claim。

    批次8刀3 撤 web_act 枚举时把这句留给 P11 定夺；P11 落地 resume_token/
    selector/schema 时重写了 web_crawl 描述，这句随判决改口（默认值枚举撤
    给运行时回执与手册，只留"哪个到点说哪个"）。两案只计一次的约定由锁的
    理由行记账。"""
    crawl = _desc("web_crawl")
    assert "whichever bites is named" in crawl, (
        "P11 压缩后的 Caps 句缺席 —— 互斥的那次 claim 丢了")
    assert "(1-40, default 10)" not in crawl and "max_content_chars_per" not in crawl, (
        "Caps 句的默认值/逐参枚举又回来了 —— 那是要按需付的部分")


# ============================================================================
# 来源：test_an_ntfs_stream_path_is_refused_instead_of_read.py
# ============================================================================
# NTFS 备用数据流读得出来 —— 扩展名门被流名骗过。

@pytest.mark.skipif(sys.platform != "win32", reason="ADS 流语法只在 NTFS 上存在")
@pytest.mark.asyncio
async def test_an_ntfs_stream_path_is_refused_instead_of_read(tmp_path):
    base = tmp_path / "report.txt"
    base.write_text("benign cover file", encoding="utf-8")
    stream = str(base) + ":hidden.json"
    with open(stream, "w", encoding="utf-8") as f:  # 真造一个流：不拒就会泄
        f.write('{"ads_secret": "FAKE-ADS-SECRET"}')
    r = await MasterFetchServer().doc_parse(stream)
    assert r.error.startswith("Access denied")
    assert "FAKE-ADS-SECRET" not in "\n".join(r.content)


@pytest.mark.skipif(sys.platform != "win32", reason="盘符冒号只在 Windows 上是流语法歧义")
@pytest.mark.asyncio
async def test_a_normal_drive_letter_path_is_untouched(tmp_path):
    f = tmp_path / "plain.json"
    f.write_text('{"ok": true}', encoding="utf-8")
    r = await MasterFetchServer().doc_parse(str(f))
    assert r.status == 200
    assert r.content_ok is True


@pytest.mark.asyncio
async def test_a_colon_filename_on_this_platform_stays_consistent(tmp_path):
    """POSIX 上冒号是合法文件名 —— 拒绝条件必须只在 nt 生效（核心用途零损伤）。"""
    f = tmp_path / "weird:name.json"
    f.write_text('{"ok": true}', encoding="utf-8")
    r = await MasterFetchServer().doc_parse(str(f))
    if os.name == "nt":
        assert r.error.startswith("Access denied")
    else:
        assert r.status == 200


# ============================================================================
# 来源：test_readme_subset_numbers_match_composed_instructions.py
# ============================================================================
# README 的 DHOLE_TOOLS 子集数是「tools/list 实测 ＋ 全量 instructions」的手工口径 —— 与 wire 实际行为不符。

README = Path(__file__).resolve().parent.parent / "README.md"

SUB8 = ["web_read", "web_search", "web_request", "web_crawl",
        "doc_parse", "feed_read", "sys_state", "sys_guide"]


def _subset_connect(sel: list[str]) -> int:
    """measure 同款算法，但 instructions 按 _compose_instructions(子集) 实跑。"""
    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    table = len(json.dumps({"tools": [defs[n] for n in sel]},
                           ensure_ascii=False, separators=(",", ":")))
    return table + len(_compose_instructions(set(sel)))


def test_readme_subset_numbers_match_composed_instructions():
    text = README.read_text(encoding="utf-8")
    sub8 = _subset_connect(SUB8)
    sub9 = _subset_connect(SUB8 + ["web_discover"])
    assert f"{sub8:,}" in text, f"README 的 8 颗子集数应与实跑口径一致（实测 {sub8:,}）"
    assert f"{sub9:,}" in text, f"README 的 9 颗子集数应与实跑口径一致（实测 {sub9:,}）"


def test_the_full_surface_number_stays_the_measured_one():
    """全量口径两种算法同值（没有工具被裁），README 总数必须等于它。"""
    text = README.read_text(encoding="utf-8")
    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    total = (len(json.dumps({"tools": list(defs.values())},
                            ensure_ascii=False, separators=(",", ":")))
             + len(DHOLE_INSTRUCTIONS))
    assert f"{total:,}" in text, f"README 连接期总数应与实测一致（{total:,}）"


# ============================================================================
# 来源：test_every_wire_parameter_is_findable_in_the_manual.py
# ============================================================================
# 一颗 wire 参数在手册里查无此人 = 这颗参数对只读手册的 agent 不存在。

def _mentions(text: str, key: str) -> bool:
    """按词边界找键名：`wait` 不该被 `network_idle` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in guide._ROOT.rglob("*.md"))


def test_the_parameter_coverage_surface_is_the_corpus_alone():
    """机制自检：表面若混进 wire 文本，覆盖率永远假绿；语料空了同理。"""
    corpus = _corpus()
    assert len(corpus) > 20_000, "语料空了：参数覆盖率会伪装成通过"
    assert not _mentions("network_idle and waits", "wait")
    assert _mentions("hops from the start URL: max_depth", "max_depth")


@pytest.mark.parametrize("tool", sorted(_TOP_LEVEL_ARGS))
def test_every_wire_parameter_is_findable_in_the_manual(tool):
    corpus = _corpus()
    missing = sorted(k for k in _TOP_LEVEL_ARGS[tool]
                     if not _mentions(corpus, k))
    assert not missing, (
        f"{tool} 的这些参数在全语料查无此人 —— 只读手册的 agent 不知道它们存在: "
        f"{missing}。补进 references/tools.md 对应节（使用者视角一句），"
        "或从 _TOP_LEVEL_ARGS 里删掉。")


# ============================================================================
# 来源：test_no_docstring_names_a_removed_tool.py
# ============================================================================
# web_read 的方法 docstring 还在指路一颗不存在的工具 —— 17.0 已把截图并入 web_act。

SERVER = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "server.py"

REMOVED_TOOLS = (
    "web_screenshot", "web_resolve", "web_recipe",
    "web_watch", "cred_vault", "cred_login",
)


def test_no_docstring_names_a_removed_tool():
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    docs = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node) or ""
            docs.append((node.name, doc))
    offenders = [(name, tool) for name, doc in docs
                 for tool in REMOVED_TOOLS if tool in doc]
    assert not offenders, (
        "方法 docstring 点名了已移除的工具: "
        + ", ".join(f"{n} -> {t}" for n, t in offenders[:3]))


def test_the_live_docstring_points_at_the_current_route():
    doc = MasterFetchServer.web_read.__doc__ or ""
    assert "web_act" in doc and "screenshot" in doc


# ============================================================================
# 来源：test_a_loopback_http_bind_without_a_token_prints_the_same_warning.py
# ============================================================================
# --http 绑回环且无 token 时静默 —— 共享是模型固有属性，回环上同样成立。

def test_a_loopback_http_bind_without_a_token_prints_the_same_warning():
    src = inspect.getsource(MasterFetchServer.serve)
    cond = [line for line in src.splitlines() if "not http_token" in line]
    assert cond, "警告条件消失了"
    line = cond[0]
    assert "127.0.0.1" not in line and "localhost" not in line, (
        "回环绑定不应豁免警告 —— 默认 --host 恰恰是最需要听见这一句的场合")


def test_the_warning_names_the_single_principal_share():
    src = inspect.getsource(MasterFetchServer.serve)
    assert "shares one" in src, "警告文本要说出共享的是什么（存储身份/会话/缓存）"


def test_the_token_gate_behavior_is_unchanged():
    """token 设置后的既有行为不动：闸只在设了 token 时包上。"""
    src = inspect.getsource(MasterFetchServer.serve)
    assert "if http_token:" in src and "_TokenGate(app, http_token)" in src


# ============================================================================
# 来源：test_web_act_stops_claiming_destructive_is_false.py
# ============================================================================
# web_act 自称「只做加性更新」（destructiveHint: False），而 submit 门自己承认不可逆。

def test_web_act_stops_claiming_destructive_is_false():
    defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
    web_act = defs["web_act"]
    assert "destructiveHint" not in (web_act.get("annotations") or {}), (
        "spec 默认 true；写 False 是主动错报，删键落回默认")


def test_the_other_annotations_are_untouched():
    defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
    ann = defs["web_act"]["annotations"]
    assert ann == {"readOnlyHint": False, "idempotentHint": False,
                   "openWorldHint": True}
    # web_request 的 destructive=True 是真话（真的能写），不许被顺手带走
    req = defs["web_request"]["annotations"]
    assert req.get("destructiveHint") is True


# ============================================================================
# 来源：test_every_registered_tool_has_a_routing_line.py
# ============================================================================
# 注册了却没有路的目的地 —— web_discover 在连接期路由段查无此人。

def test_every_registered_tool_has_a_routing_line():
    unrooted = sorted(set(_TOP_LEVEL_ARGS) - set(_INSTRUCTIONS_ROUTING))
    assert not unrooted, (
        f"{unrooted} 注册了却没有连接期路由行 —— 只读 instructions 的 agent "
        "不知道它们存在，会用别的工具硬凑。给 _INSTRUCTIONS_ROUTING 补键，"
        "或把这颗工具下线。")


def test_no_routing_line_points_at_an_unregistered_tool():
    """反向：路由行指向表外名字 = instructions 在给不存在的目的地指路。"""
    ghost = sorted(set(_INSTRUCTIONS_ROUTING) - set(_TOP_LEVEL_ARGS))
    assert not ghost, f"{ghost} 有路由行但不在注册面 —— 指路指到了幽灵"
