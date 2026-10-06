"""web_crawl 的 deadline_ms：wire 承诺默认 25s，入口签名实际 120s —— 按承诺使用必然踩坑。

症状（2026-10-06 实测，ast 读 server.py，与 tool_payload_measure.py 同款离线口径）：

    WIRE: "Hard stop for the whole crawl in ms (default 25000 - aligned to a
           typical 30s host MCP timeout; ...)"
    MCP entry signature default: 120000 ms ＝ 120.0 s
    crawl.py library signature default: 25000 ms

批次13 只把 crawl.py 的库签名 120000→25000，MCP 入口（server.py 的 web_crawl
AsyncFunctionDef）未动，原样传下去，库默认永不生效。后果：agent 在 30s 宿主上
按 wire 描述相信默认会在 25s 先返回已抓页，实际爬满 120s，宿主 30s 裸砍连接、
什么都回不来。web_discover 委托 web_crawl 且不传 deadline_ms，同样继承。

这不是翻"改默认劣于加参数"的已判案：wire 描述、tools.md:171、批次13 ledger
三处**已经承诺** 25,000，入口签名是执行只落在库层的缺口 —— 此刀让代码追上
已发布契约。

判据：入口签名默认 ＝＝ 25000；与 wire 描述里的 "default 25000" 互证（通用
守卫：default N 与签名默认一致）；连接期 31,050 不变（描述未动）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "server.py"


def _entry_default(func_name: str, param: str):
    """MasterFetchServer.<func> 的 AsyncFunctionDef 签名里 <param> 的默认值。"""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (isinstance(node, ast.ClassDef) and node.name == "MasterFetchServer"):
            for item in node.body:
                if (isinstance(item, ast.AsyncFunctionDef)
                        and item.name == func_name):
                    # positional 的 defaults 对齐参数表**尾部**；kw_defaults 与
                    # kwonlyargs 一一对应，无默认的槽位是 None。
                    args = item.args.args
                    defs = item.args.defaults
                    for arg, default in zip(args[len(args) - len(defs):], defs):
                        if arg.arg == param and default is not None:
                            return ast.literal_eval(default)
                    for arg, default in zip(item.args.kwonlyargs,
                                            item.args.kw_defaults):
                        if arg.arg == param and default is not None:
                            return ast.literal_eval(default)
    return None


def _wire_deadline_claim() -> str:
    """_TOOL_DEFS 里 web_crawl.deadline_ms 描述写的 default N。"""
    tree = ast.parse(SERVER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict) and node.keys:
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "deadline_ms"
                        and isinstance(v, ast.Dict)):
                    for k2, v2 in zip(v.keys, v.values):
                        if (isinstance(k2, ast.Constant)
                                and k2.value == "description"):
                            desc = ast.literal_eval(v2)
                            m = re.search(r"default (\d+)", desc)
                            return m.group(1) if m else ""
    return ""


def test_crawl_deadline_says_25000_and_means_it():
    """症状本体：入口签名默认是 25000，不是 120000。"""
    assert _entry_default("web_crawl", "deadline_ms") == 25000


def test_crawl_deadline_wire_claim_matches_entry_signature():
    """通用守卫：wire 描述里的 default N ＝＝ 入口签名默认（两把尺一个数）。"""
    claim = _wire_deadline_claim()
    assert claim, "wire 描述应点名 default N"
    assert _entry_default("web_crawl", "deadline_ms") == int(claim)


def test_the_library_default_and_the_wire_claim_agree():
    """库签名（crawl.py）与 wire 描述同数 —— 三处一个数，缺口不再有藏身处。"""
    crawl = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp"
             / "crawl.py")
    tree = ast.parse(crawl.read_text(encoding="utf-8"))
    found = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args.args
            defs = node.args.defaults
            for arg, default in zip(args[len(args) - len(defs):], defs):
                if arg.arg == "deadline_ms" and default is not None:
                    found = ast.literal_eval(default)
    assert found == 25000 and int(_wire_deadline_claim()) == found
