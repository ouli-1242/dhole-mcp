"""DHOLE_TOOLS 的价目表必须就是 wire 本身：每个子集真起一个 stdio 进程去量。

症状（2026-10-09 量）：仓库里所有子集数字都是**进程内算术**（`_compose_instructions`
+ `_TOOL_DEFS`）算出来的，没有一条判据起过真服务器去看 initialize / tools/list 实际
发出去多少字节。这次逐子集量了 12 组，算术与 wire 逐字符一致 —— 也就是这组判据本来
该存在而一直不存在。同一次量尺顺手量出两处对不上：

  1. `tests/tool_payload_measure.py` 的 `default_wire_report` 小节名叫
     "what an ordinary client pays"，却把全量连接期报成 instructions 2,626 /
     总 30,857。真机是 2,476 / 30,707：它的路由行按**工具数**累加，而
     `_compose_instructions` 按**文本**去重（web_discover 与 web_crawl 共用一句，
     server.py 的 F-A-J1）。多报的 150 字符是一笔不存在的支付。
  2. `test_enabled_tools.py` 的头写着 `web_read,web_search` 付 11,423，真机 11,382。

判据：子集逐一真起进程（initialize → notifications/initialized → tools/list），
把原始应答里的三件事 —— 广告的工具名集合、tools/list 的紧凑字节、instructions 字符
—— 与**父进程**导入的同名常量算出的预测对齐；两个已发布的数（README 的 8 颗 /
9 颗、日常两件套）另加一句"文档里印的就是这个数"。全部用例只走 initialize 与
tools/list，不发出任何网络请求，也不起浏览器（实测子进程 stderr 为空、驻留 3 秒
后仍在 1.2 秒内退干净）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

SUB8 = ["web_read", "web_search", "web_request", "web_crawl",
        "doc_parse", "feed_read", "sys_state", "sys_guide"]

_wired: dict = {}


class _StdioClient:
    """A throwaway dhole over stdio, kept to the two connect-time requests."""

    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "dhole_mcp"], cwd=str(REPO_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        self.stderr_text = ""
        self._by_id: dict = {}
        self._cond = threading.Condition()

        def _out():
            for raw in iter(self.proc.stdout.readline, b""):
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not text:
                    continue
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                with self._cond:
                    if isinstance(parsed, dict) and parsed.get("id") is not None:
                        self._by_id[parsed["id"]] = (len(raw), parsed)
                    self._cond.notify_all()

        def _err():
            chunks = []
            for raw in iter(self.proc.stderr.readline, b""):
                chunks.append(raw.decode("utf-8", "replace"))
            self.stderr_text = "".join(chunks)

        threading.Thread(target=_out, daemon=True).start()
        threading.Thread(target=_err, daemon=True).start()

    def _send(self, obj):
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def ask(self, rid, method, params=None, timeout=90):
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.time() + timeout
        with self._cond:
            while rid not in self._by_id:
                left = deadline - time.time()
                if left <= 0 or self.proc.poll() is not None:
                    raise AssertionError(
                        f"{method} never answered (stderr tail: "
                        f"{self.stderr_text[-400:]})")
                self._cond.wait(left)
        return self._by_id[rid]

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=20)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)


def _connect(dhole_tools):
    """Ask a real server what it actually puts on the connect-time wire.

    Cached per DHOLE_TOOLS value: the knob is frozen at import, so re-asking the
    same subset costs a whole process and buys nothing.
    """
    if dhole_tools in _wired:
        return _wired[dhole_tools]
    env = {k: v for k, v in os.environ.items()
           if k not in ("DHOLE_TOOLS", "DHOLE_USAGE_LOG")}
    if dhole_tools is not None:
        env["DHOLE_TOOLS"] = dhole_tools
    client = _StdioClient(env)
    try:
        _raw, init = client.ask(1, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "price-audit", "version": "0"}})
        client._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        raw_len, listed = client.ask(2, "tools/list", {})
    finally:
        client.close()
    assert not client.stderr_text, (
        "起一台只问 initialize/tools/list 的服务器不该留下 stderr："
        f"{client.stderr_text[:300]}")
    tools = listed["result"]["tools"]
    wire = {
        "names": [t["name"] for t in tools],
        "tools_compact": len(json.dumps({"tools": tools},
                                        separators=(",", ":"), ensure_ascii=False)),
        "raw_tools_line": raw_len,
        "instructions": init["result"].get("instructions") or "",
    }
    _wired[dhole_tools] = wire
    return wire


def _predict(names):
    """The same two numbers, computed in THIS process from the imported source."""
    from dhole_mcp.server import MasterFetchServer, _compose_instructions

    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    compact = len(json.dumps({"tools": [defs[n] for n in names]},
                             separators=(",", ":"), ensure_ascii=False))
    return compact, len(_compose_instructions(frozenset(names)))


def _assert_price(dhole_tools, names, published=None):
    wire = _connect(dhole_tools)
    assert sorted(wire["names"]) == sorted(names), (
        f"广告集合不是请求的那一份：wire {wire['names']} vs {sorted(names)}")
    compact, instr_chars = _predict(names)
    assert wire["tools_compact"] == compact, (
        f"tools/list 字节与进程内预测不符：wire {wire['tools_compact']} vs {compact}")
    assert len(wire["instructions"]) == instr_chars, (
        f"instructions 字节与进程内预测不符：wire {len(wire['instructions'])} vs {instr_chars}")
    total = compact + instr_chars
    if published:
        path, phrase = published
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        assert phrase.replace("{total}", f"{total:,}") in text, (
            f"{path} 里没印着真机量到的 {total:,}")
    return total


def _ruler_module():
    spec = importlib.util.spec_from_file_location(
        "tool_payload_measure", REPO_ROOT / "tests" / "tool_payload_measure.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_default_connect_is_the_published_30_719():
    """不设 DHOLE_TOOLS 时，wire 就是 CONNECT_CEILING 那笔账：30,719。

    这一条同时是量尺的回归锁：`tool_payload_measure._wire_for` 曾按工具数累加路由行，
    把默认连接报成 30,857（web_discover / web_crawl 共用那句被算了两遍）。修成按文本
    去重后，脚本的读数、进程内预测与真机三者必须同值。

    30,707 → 30,701（2026-10-09，审计 D1+D4）：`web_act.confirm` 的描述说清了
    `confirm` 的两处门（净 +34），`cred_defaults.set` 的描述改掉了「host 在 auth
    里面」那句错话并缩到真话（净 −40）。这一对是**往下**走的，所以 CONNECT_CEILING
    也跟着降，而不是抬上限。同批的账在 test_wire_moves_land_in_the_manual.py。

    30,701 → 30,719（2026-10-10，体验修复刀3-C2）：一次**上抬**。两颗 `search` 描述
    都写着 "(URL path + page title)"，而 `sitemap=true` 的图只有 `loc`，没有标题可比
    —— 常驻层里的这句话在一条真实的调用形状上是假的。改动只做"不再承诺标题那一半"
    （crawl +4 / discover +14），完整解释留在 references/tools.md 按需付。
    """
    ruler = _ruler_module()
    import ast
    tree = ast.parse(ruler.SERVER.read_text(encoding="utf-8"))
    defs = ruler.literal_node(tree, "_TOOL_DEFS")
    parts = (ruler.literal_node(tree, "_INSTRUCTIONS_INTRO"),
             ruler.literal_node(tree, "_INSTRUCTIONS_ROUTING"),
             ruler.literal_node(tree, "_INSTRUCTIONS_RULES"))
    ruler_compact, ruler_instr = ruler._wire_for(
        defs, {td["name"] for td in defs}, parts)

    wire = _connect(None)
    assert len(wire["names"]) == 11
    assert ruler_compact == wire["tools_compact"], (
        f"仓库量尺与 wire 的 tools/list 对不上：{ruler_compact} vs {wire['tools_compact']}")
    assert ruler_instr == len(wire["instructions"]), (
        f"仓库量尺多算了路由行：{ruler_instr} vs wire {len(wire['instructions'])}")
    assert ruler_compact + ruler_instr == 30_719


def test_the_two_tool_daily_driver_pays_what_its_docstring_claims():
    """`DHOLE_TOOLS=web_read,web_search` 的真机价：11,382（旧的注释写 11,423）。"""
    assert _assert_price("web_read,web_search",
                         ["web_read", "web_search"]) == 11_382


def test_the_readme_eight_tool_price_is_what_the_server_sends():
    """README 的 23,595 不再只是算术 —— 真机同一值，且文档印的就是它。"""
    _assert_price(",".join(SUB8), SUB8,
                  published=("README.md", "这 8 颗是 {total} 字符"))


def test_the_ninth_tool_costs_its_own_row_and_no_routing_line():
    """加 web_discover 只多它自己那一行：路由行与 web_crawl 共用，去重后不再付第二次。

    这是 F-A-J1 那把共享路由行在 wire 上的样子：README 的 9 颗 25,167 =
    23,599 + 1,568，instructions 一字不变。
    """
    nine = SUB8 + ["web_discover"]
    assert _assert_price(",".join(nine), nine,
                         published=("README.md", "共 9 颗 {total}")) == 25_167
    eight = _connect(",".join(SUB8))
    assert len(_connect(",".join(nine))["instructions"]) == len(eight["instructions"]), (
        "两颗共用一句路由的工具，第二颗不该再收一次路由行的钱")


def test_the_raw_line_is_the_compact_table_plus_a_bounded_envelope():
    """"字节"要说清是哪一层：JSON-RPC 信封只多几十字，价目表量的是表本身。

    发布口径是紧凑 tools/list 的字符数；真机那一行还带着
    `{"jsonrpc":…,"id":…,"result":…}`。信封差钉在 40–60 字符内 —— 越过这个带说明
    wire 形状变了（比如开始转义非 ASCII），那才是需要人来看的一次改动。
    """
    wire = _connect("web_read,web_search")
    envelope = wire["raw_tools_line"] - wire["tools_compact"]
    assert 40 <= envelope <= 60, f"tools/list 那一行的信封开销异常：{envelope}"
